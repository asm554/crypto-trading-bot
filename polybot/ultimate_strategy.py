"""Adaptive multi-strategy paper bot — Der Ultimative.

The bot trades BTC/EUR, ETH/EUR and SOL/EUR.  It classifies the current
market regime and only enables the matching long strategy: trend breakouts or
pullbacks in an uptrend, controlled mean reversion in a sideways market, and
no entries in downtrends or unclear regimes.  There is deliberately no live
order path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from polybot import paper_db as paper_db_module
from polybot.candlestick_strategy import bullish_patterns, macd_snapshot, rsi_wilder
from polybot.dca_strategy import PAIR_MAP, extract_quote, fetch_ticker_data
from polybot.paper_db import get_realized_pnl_by_prefix, log_equity_snapshot, log_paper_trade, resolve_trade
from polybot.surfer_strategy import atr_wilder, closed_ohlc_rows, ema_series, fetch_ohlc

logger = logging.getLogger(__name__)

PREFIX = "ULT_"
BOT_KEY = "ultimate"
PAIRS = ("XBTEUR", "ETHEUR", "SOLEUR")
MIN_POSITION_EUR = 1.0
DEFAULT_TAKER_FEE_RATE = 0.008


def break_even_price(entry_price: float, fee_rate: float) -> float:
    """Price that covers both taker fees."""
    return float(entry_price) * (1 + float(fee_rate)) / (1 - float(fee_rate))


def net_risk_per_unit(entry_price: float, stop_price: float, fee_rate: float) -> float:
    """Worst-case loss per coin including entry and stop-exit fees."""
    return float(entry_price) * (1 + float(fee_rate)) - float(stop_price) * (1 - float(fee_rate))


def fee_adjusted_target(entry_price: float, stop_price: float, reward_risk_ratio: float, fee_rate: float) -> float:
    """Target whose net profit is reward_risk_ratio times the net stop loss."""
    risk = net_risk_per_unit(entry_price, stop_price, fee_rate)
    return (float(entry_price) * (1 + float(fee_rate)) + float(reward_risk_ratio) * risk) / (1 - float(fee_rate))


def advanced_bullish_patterns(rows: list[tuple]) -> list[str]:
    patterns = bullish_patterns(rows)
    if len(rows) < 2:
        return patterns
    previous, current = rows[-2], rows[-1]
    prev_open, prev_high, prev_low, prev_close = map(float, (previous[1], previous[2], previous[3], previous[4]))
    open_, high, low, close = map(float, (current[1], current[2], current[3], current[4]))
    tolerance = max(prev_close, close) * 0.0015
    if prev_close < prev_open and close > open_ and open_ < prev_close and close >= prev_open - (prev_open - prev_close) * 0.5:
        patterns.append("piercing_pattern")
    if abs(low - prev_low) <= tolerance and prev_close < prev_open and close > open_:
        patterns.append("tweezer_bottom")
    if len(rows) >= 3:
        first, middle, third = rows[-3], rows[-2], rows[-1]
        first_open, first_close = float(first[1]), float(first[4])
        middle_body = abs(float(middle[4]) - float(middle[1]))
        first_body = abs(first_open - first_close)
        if first_close < first_open and middle_body <= first_body * 0.4 and close > open_ and close >= (first_open + first_close) / 2:
            patterns.append("morning_star")
        soldiers = rows[-3:]
        if all(float(row[4]) > float(row[1]) for row in soldiers) and all(float(soldiers[i][4]) > float(soldiers[i - 1][4]) for i in (1, 2)):
            patterns.append("three_white_soldiers")
    return sorted(set(patterns))


def analyse_market(rows: list[tuple], *, volume_multiplier: float = 1.2, breakout_lookback: int = 20) -> dict | None:
    if len(rows) < 210:
        return None
    closes = [float(row[4]) for row in rows]
    highs = [float(row[2]) for row in rows]
    lows = [float(row[3]) for row in rows]
    volumes = [float(row[6]) for row in rows]
    ema20, ema50, ema200 = ema_series(closes, 20), ema_series(closes, 50), ema_series(closes, 200)
    if not ema20 or not ema50 or not ema200:
        return None
    e20, e50, e200 = ema20[-1], ema50[-1], ema200[-1]
    close = closes[-1]
    slope_lookback = min(24, len(ema200) - 1)
    ema200_slope_pct = (e200 / ema200[-(slope_lookback + 1)] - 1) * 100 if slope_lookback > 0 else 0.0
    if e20 > e50 > e200 and close > e20:
        regime = "uptrend"
    elif e20 < e50 < e200 and close < e20:
        regime = "downtrend"
    elif (
        abs(e50 - e200) / e200 * 100 <= 1.0
        and abs(close - e200) / e200 * 100 <= 2.5
        and abs(ema200_slope_pct) <= 0.5
    ):
        regime = "sideways"
    else:
        regime = "unclear"

    rsi = rsi_wilder(closes)
    macd = macd_snapshot(closes)
    previous_macd = macd_snapshot(closes[:-1])
    macd_hist_rising = bool(macd and previous_macd and macd[2] > previous_macd[2])
    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ok = avg_volume > 0 and volumes[-1] >= avg_volume * volume_multiplier
    patterns = advanced_bullish_patterns(rows)
    resistance = max(highs[-(breakout_lookback + 1):-1])
    support = min(lows[-(breakout_lookback + 1):-1])
    bullish_confirmation = bool(patterns) or (close > closes[-2] and macd_hist_rising)
    breakout = close > resistance * 1.0005 and volume_ok and close > float(rows[-1][1])
    pullback = (
        lows[-1] <= e20 * 1.005
        and close > e20
        and closes[-2] <= e20
        and bullish_confirmation
        and macd_hist_rising
        and rsi is not None
        and 45 <= rsi <= 68
    )
    mean_reversion = (
        close <= support * 1.015
        and rsi is not None
        and 35 <= rsi <= 48
        and volume_ok
        and bullish_confirmation
        and macd_hist_rising
    )
    macd_ok = bool(macd and macd[0] > macd[1] and macd[2] > 0)
    natural_target = max(e20, (support + resistance) / 2) if regime == "sideways" else None
    score = 0
    setup = None
    if regime == "uptrend":
        setup = "breakout" if breakout else "pullback" if pullback else None
        score = 20 + 15
        score += 10 if rsi is not None and 50 <= rsi <= 70 else 0
        score += 10 if macd_ok else 0
        score += 10 if volume_ok else 0
        score += 15 if patterns else 0
        score += 20 if setup else 0
    elif regime == "sideways":
        setup = "mean_reversion" if mean_reversion else None
        score = 20
        score += 15 if close < e20 else 0
        score += 10 if rsi is not None and 35 <= rsi <= 50 else 0
        score += 10 if macd_ok else 0
        score += 10 if volume_ok else 0
        score += 15 if patterns else 0
        score += 20 if setup else 0
    atr = atr_wilder(highs, lows, closes, 14)
    return {
        "regime": regime,
        "setup": setup,
        "score": score,
        "rsi": rsi,
        "macd": macd,
        "macd_hist_rising": macd_hist_rising,
        "volume_ok": volume_ok,
        "patterns": patterns,
        "atr": atr,
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "support": support,
        "resistance": resistance,
        "natural_target": natural_target,
        "signal_ts": int(float(rows[-1][0])),
        "ema200_slope_pct": ema200_slope_pct,
        "range_width_pct": (resistance - support) / close * 100 if close else 0.0,
        "close": close,
    }


class UltimateBot:
    def __init__(
        self,
        initial_capital_eur: float = 500.0,
        interval_sec: int = 300,
        min_score: int = 80,
        volume_multiplier: float = 1.2,
        atr_stop_multiplier: float = 2.0,
        reward_risk_ratio: float = 2.0,
        max_risk_eur: float = 0.50,
        max_position_eur: float = 25.0,
        max_hold_sec: int = 72 * 3600,
        account_loss_limit_pct: float = 10.0,
        fee_rate: float = DEFAULT_TAKER_FEE_RATE,
        min_hold_sec: int = 3600,
        pair_cooldown_sec: int = 12 * 3600,
        loss_streak_limit: int = 2,
        loss_pause_sec: int = 12 * 3600,
        max_entries_per_day: int = 3,
        max_spread_pct: float = 0.15,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 900,
        state_path: Path | None = None,
    ):
        if not paper_mode:
            raise NotImplementedError("UltimateBot is paper-only")
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.min_score = int(min_score)
        self.volume_multiplier = float(volume_multiplier)
        self.atr_stop_multiplier = float(atr_stop_multiplier)
        self.reward_risk_ratio = float(reward_risk_ratio)
        self.max_risk_eur = float(max_risk_eur)
        self.max_position_eur = float(max_position_eur)
        self.max_hold_sec = int(max_hold_sec)
        self.account_loss_limit_pct = float(account_loss_limit_pct)
        self.fee_rate = float(fee_rate)
        self.min_hold_sec = int(min_hold_sec)
        self.pair_cooldown_sec = int(pair_cooldown_sec)
        self.loss_streak_limit = int(loss_streak_limit)
        self.loss_pause_sec = int(loss_pause_sec)
        self.max_entries_per_day = int(max_entries_per_day)
        self.max_spread_pct = float(max_spread_pct)
        if not 0 <= self.fee_rate < 0.1:
            raise ValueError("fee_rate must be between 0 and 0.1")
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        self.state_path = state_path or data_dir / "ultimate_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self.last_traded_signals: dict[str, str] = {}
        self.cooldowns: dict[str, float] = {}
        self.recent_entry_ts: list[float] = []
        self.loss_streak = 0
        self.paused_until = 0.0
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "last_entry_scan": self.last_entry_scan,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
            "last_traded_signals": self.last_traded_signals,
            "cooldowns": self.cooldowns,
            "recent_entry_ts": self.recent_entry_ts,
            "loss_streak": self.loss_streak,
            "paused_until": self.paused_until,
            "updated_at": time.time(),
        }
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        temporary.replace(self.state_path)

    def _load_state_or_rebuild(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, float(raw.get("capital_remaining", self.initial_capital_eur)))
                self.portfolio = raw.get("portfolio") or {}
                self.last_entry_scan = float(raw.get("last_entry_scan", 0))
                self.last_snapshot = float(raw.get("last_snapshot", 0))
                self.trade_count = int(raw.get("trade_count", 0))
                self.last_traded_signals = {str(k): str(v) for k, v in (raw.get("last_traded_signals") or {}).items()}
                self.cooldowns = {str(k): float(v) for k, v in (raw.get("cooldowns") or {}).items()}
                self.recent_entry_ts = [float(v) for v in (raw.get("recent_entry_ts") or [])]
                self.loss_streak = int(raw.get("loss_streak", 0))
                self.paused_until = float(raw.get("paused_until", 0))
                if "loss_streak" not in raw and self.trade_count > 0:
                    self.loss_streak = self.loss_streak_limit
                    self.paused_until = time.time() + self.loss_pause_sec
                return
            except Exception as exc:
                logger.warning("ULT state unlesbar, DB-Rebuild: %s", exc)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        if not self.db_path.exists():
            return
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM paper_trades WHERE market_question LIKE ? ORDER BY id", (f"{PREFIX}%",)).fetchall()
        finally:
            connection.close()
        realized = 0.0
        open_cost = 0.0
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            self.trade_count += 1
            if row["resolved_at"] is None:
                pair = str(row["market_question"]).removeprefix(PREFIX)
                cost = float(row["size"]) * float(row["price"])
                open_cost += cost
                grouped.setdefault(pair, []).append({
                    "trade_id": int(row["id"]), "role": "runner", "shares": float(row["size"]),
                    "entry_price": float(row["price"]), "cost_basis": cost, "open": True,
                })
            else:
                realized += float(row["real_pnl"] or 0)
        for pair, legs in grouped.items():
            self.portfolio[pair] = {"legs": legs, "entry_ts": time.time(), "needs_recovery_exit": True, "added": True}
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    @staticmethod
    def _snapshot(pair: str, ticker: dict) -> dict | None:
        data = ticker.get(PAIR_MAP.get(pair, pair)) or ticker.get(pair)
        if not data:
            return None
        try:
            last = float(data["c"][0])
            bid, ask = extract_quote(data, last)
            return {"last": last, "bid": bid, "ask": ask}
        except Exception:
            return None

    @staticmethod
    def _open_legs(position: dict) -> list[dict]:
        return [leg for leg in position.get("legs", []) if leg.get("open", True)]

    @classmethod
    def _average_entry(cls, position: dict) -> float:
        legs = cls._open_legs(position)
        shares = sum(float(leg["shares"]) for leg in legs)
        return sum(float(leg["shares"]) * float(leg["entry_price"]) for leg in legs) / shares if shares else 0.0

    async def _resolve_leg(self, leg: dict, exit_price: float) -> float:
        shares = float(leg["shares"])
        entry_price = float(leg["entry_price"])
        entry_cost = float(leg["cost_basis"])
        exit_value = shares * exit_price
        pnl = exit_value - entry_cost - entry_cost * self.fee_rate - exit_value * self.fee_rate
        await resolve_trade(int(leg["trade_id"]), exit_price, round(pnl, 6))
        leg["open"] = False
        self.capital_remaining += entry_cost + pnl
        return pnl

    def _record_outcome(self, pair: str, pnl: float) -> None:
        now = time.time()
        self.cooldowns[pair] = now + self.pair_cooldown_sec
        if pnl < 0:
            self.loss_streak += 1
            if self.loss_streak >= self.loss_streak_limit:
                self.paused_until = max(self.paused_until, now + self.loss_pause_sec)
        else:
            self.loss_streak = 0

    async def _close_position(self, pair: str, exit_price: float, reason: str) -> dict:
        position = self.portfolio[pair]
        pnl = float(position.get("realized_pnl") or 0.0)
        for leg in self._open_legs(position):
            pnl += await self._resolve_leg(leg, exit_price)
        self.portfolio.pop(pair, None)
        self._record_outcome(pair, pnl)
        self._save_state()
        logger.info("✅ ULT Exit %s: %s @ %.6f€ | PnL %+.4f€", pair, reason, exit_price, pnl)
        return {"pair": pair, "reason": reason, "pnl": pnl}

    async def manage_positions(self) -> list[dict]:
        if not self.portfolio:
            return []
        pair, position = next(iter(self.portfolio.items()))
        ticker, raw_rows = await asyncio.gather(fetch_ticker_data([pair]), fetch_ohlc(pair, 60))
        snap = self._snapshot(pair, ticker)
        rows = closed_ohlc_rows(raw_rows, 60)
        analysis = analyse_market(rows, volume_multiplier=self.volume_multiplier)
        if not snap or not analysis or not analysis["atr"]:
            return []
        bid = float(snap["bid"])
        avg_entry = self._average_entry(position)
        atr = float(analysis["atr"])
        peak = max(float(position.get("peak_price") or avg_entry), bid)
        position["peak_price"] = peak
        stop = float(position.get("stop_price") or avg_entry - atr * self.atr_stop_multiplier)
        initial_stop = float(position.get("initial_stop") or stop)
        break_even = break_even_price(avg_entry, self.fee_rate)
        one_r_target = fee_adjusted_target(avg_entry, initial_stop, 1.0, self.fee_rate)
        if peak >= one_r_target:
            stop = max(stop, break_even)
        if position.get("partial_taken"):
            stop = max(stop, peak - atr * self.atr_stop_multiplier)
        position["stop_price"] = stop

        tp_leg = next((leg for leg in self._open_legs(position) if leg.get("role") == "take_profit"), None)
        target_price = float(position.get("target_price") or fee_adjusted_target(avg_entry, initial_stop, self.reward_risk_ratio, self.fee_rate))
        if tp_leg and bid >= target_price:
            pnl = await self._resolve_leg(tp_leg, bid)
            position["partial_taken"] = True
            position["realized_pnl"] = float(position.get("realized_pnl") or 0.0) + pnl
            self._save_state()
            return [{"pair": pair, "reason": "partial_profit", "pnl": pnl}]

        now = time.time()
        reason = "state_recovery_exit" if position.get("needs_recovery_exit") else None
        if not reason and bid <= stop: reason = "atr_stop"
        held_sec = now - float(position.get("entry_ts") or now)
        if not reason and held_sec >= self.min_hold_sec:
            setup = str(position.get("setup") or "")
            if analysis["regime"] == "downtrend":
                reason = "regime_exit"
            elif setup in {"breakout", "pullback"} and analysis["macd"] and analysis["macd"][2] < 0 and bid < float(analysis["ema20"]):
                reason = "momentum_exit"
            elif setup == "mean_reversion" and position.get("partial_taken") and bid >= float(position.get("natural_target") or target_price):
                reason = "mean_reversion_target"
            elif held_sec >= self.max_hold_sec:
                reason = "time_exit"
        if reason:
            return [await self._close_position(pair, bid, reason)]

        if (
            not position.get("added")
            and position.get("setup") == "pullback"
            and held_sec >= self.min_hold_sec
            and bid <= float(position["initial_entry"]) - 0.75 * atr
            and bid > stop
            and analysis["regime"] == "uptrend"
        ):
            open_legs = self._open_legs(position)
            current_risk = sum(float(leg["shares"]) * net_risk_per_unit(float(leg["entry_price"]), stop, self.fee_rate) for leg in open_legs)
            remaining_risk = max(0.0, self.max_risk_eur - current_risk)
            max_add_value = min(self.max_position_eur - sum(float(leg["cost_basis"]) for leg in open_legs), self.capital_remaining)
            add_risk = net_risk_per_unit(float(snap["ask"]), stop, self.fee_rate)
            add_shares = min(remaining_risk / max(add_risk, 1e-9), max_add_value / float(snap["ask"]))
            if add_shares * float(snap["ask"]) >= MIN_POSITION_EUR:
                price = float(snap["ask"])
                cost = add_shares * price
                trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", add_shares, price, -0.75, "paper_add")
                position["legs"].append({"trade_id": trade_id, "role": "runner", "shares": add_shares, "entry_price": price, "cost_basis": cost, "open": True})
                position["added"] = True
                self.capital_remaining -= cost
                self.trade_count += 1
                self._save_state()
                return [{"pair": pair, "reason": "single_add", "amount": cost}]
        self._save_state()
        return []

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if self.portfolio or now - self.last_entry_scan < self.interval_sec:
            return []
        self.recent_entry_ts = [ts for ts in self.recent_entry_ts if now - ts < 86400]
        if now < self.paused_until or len(self.recent_entry_ts) >= self.max_entries_per_day:
            self.last_entry_scan = now
            self._save_state()
            return []
        equity = await self.equity()
        if equity["equity_eur"] <= self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100):
            return []
        raw_rows = await asyncio.gather(*(fetch_ohlc(pair, 60) for pair in PAIRS))
        candidates = []
        for pair, rows in zip(PAIRS, raw_rows):
            closed = closed_ohlc_rows(rows, 60, now)
            analysis = analyse_market(closed, volume_multiplier=self.volume_multiplier)
            if not analysis or analysis["regime"] not in {"uptrend", "sideways"} or not analysis["setup"] or analysis["score"] < self.min_score or not analysis["atr"]:
                continue
            signal_key = f"{analysis['signal_ts']}:{analysis['setup']}"
            if self.last_traded_signals.get(pair) == signal_key or now < self.cooldowns.get(pair, 0):
                continue
            candidates.append((pair, analysis))
        self.last_entry_scan = now
        if not candidates:
            self._save_state()
            return []
        candidates.sort(key=lambda item: (item[1]["score"], bool(item[1]["patterns"]), item[1]["volume_ok"]), reverse=True)
        ticker = await fetch_ticker_data([pair for pair, _analysis in candidates])
        for pair, analysis in candidates:
            snap = self._snapshot(pair, ticker)
            if not snap:
                continue
            price = float(snap["ask"])
            bid = float(snap["bid"])
            spread_pct = (price - bid) / price * 100 if price else 100.0
            if spread_pct > self.max_spread_pct:
                continue
            atr = float(analysis["atr"])
            stop = price - atr * self.atr_stop_multiplier
            unit_risk = net_risk_per_unit(price, stop, self.fee_rate)
            target = fee_adjusted_target(price, stop, self.reward_risk_ratio, self.fee_rate)
            natural_target = analysis.get("natural_target")
            if analysis["setup"] == "mean_reversion" and (not natural_target or float(natural_target) < target):
                continue
            # Half the risk and capital are reserved for one permitted pullback add.
            initial_risk = self.max_risk_eur / 2
            position_value = min(initial_risk / unit_risk * price, self.max_position_eur / 2, self.capital_remaining)
            if position_value < MIN_POSITION_EUR:
                continue
            total_shares = position_value / price
            first_shares = total_shares / 2
            legs = []
            for role in ("take_profit", "runner"):
                cost = first_shares * price
                trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", first_shares, price, analysis["score"] / 100, f"paper_{role}")
                legs.append({"trade_id": trade_id, "role": role, "shares": first_shares, "entry_price": price, "cost_basis": cost, "open": True})
            self.capital_remaining -= position_value
            signal_key = f"{analysis['signal_ts']}:{analysis['setup']}"
            self.last_traded_signals[pair] = signal_key
            self.recent_entry_ts.append(now)
            self.portfolio[pair] = {
                "legs": legs, "entry_ts": now, "initial_entry": price, "peak_price": price,
                "stop_price": stop, "initial_stop": stop, "target_price": target,
                "natural_target": natural_target, "regime": analysis["regime"],
                "setup": analysis["setup"], "score": analysis["score"], "signal_key": signal_key,
                "added": False, "realized_pnl": 0.0,
            }
            self.trade_count += 2
            self._save_state()
            logger.info("🏆 ULT Entry %s %.2f€ @ %.6f€ | %s/%s | Score %d | Stop %.6f€ | Netto-2R %.6f€", pair, position_value, price, analysis["regime"], analysis["setup"], analysis["score"], stop, target)
            return [{"pair": pair, "amount": position_value, "score": analysis["score"], "regime": analysis["regime"], "setup": analysis["setup"]}]
        self._save_state()
        return []

    async def equity(self) -> dict:
        realized = await get_realized_pnl_by_prefix(PREFIX)
        if not self.portfolio:
            return {"equity_eur": self.capital_remaining, "cash_eur": self.capital_remaining, "open_positions": 0, "unrealized_pnl_eur": 0.0, "realized_pnl_eur": realized}
        pairs = list(self.portfolio)
        ticker = await fetch_ticker_data(pairs)
        mtm = unrealized = 0.0
        for pair, position in self.portfolio.items():
            snap = self._snapshot(pair, ticker)
            for leg in self._open_legs(position):
                cost = float(leg["cost_basis"])
                if not snap:
                    mtm += cost
                    continue
                value = float(leg["shares"]) * float(snap["bid"])
                net = value - cost * self.fee_rate - value * self.fee_rate
                mtm += net
                unrealized += net - cost
        return {"equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining, "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

    async def maybe_snapshot(self, force: bool = False) -> None:
        if not force and time.time() - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **await self.equity())
        self.last_snapshot = time.time()
        self._save_state()

    async def run(self) -> None:
        await self.maybe_snapshot(force=True)
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception:
                logger.exception("ULT loop failed")
            await asyncio.sleep(60)
