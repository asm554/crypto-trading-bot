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

from polybot import config
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
    if e20 > e50 > e200 and close > e20:
        regime = "uptrend"
    elif e20 < e50 < e200 and close < e20:
        regime = "downtrend"
    elif abs(e50 - e200) / e200 * 100 <= 2.0 and abs(close - e200) / e200 * 100 <= 4.0:
        regime = "sideways"
    else:
        regime = "unclear"

    rsi = rsi_wilder(closes)
    macd = macd_snapshot(closes)
    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ok = avg_volume > 0 and volumes[-1] >= avg_volume * volume_multiplier
    patterns = advanced_bullish_patterns(rows)
    resistance = max(highs[-(breakout_lookback + 1):-1])
    support = min(lows[-(breakout_lookback + 1):-1])
    breakout = close > resistance
    pullback = lows[-1] <= e20 * 1.005 and close > e20 and closes[-2] <= e20
    mean_reversion = close <= support * 1.015 and rsi is not None and 35 <= rsi <= 50
    macd_ok = bool(macd and macd[0] > macd[1] and macd[2] > 0)
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
        "volume_ok": volume_ok,
        "patterns": patterns,
        "atr": atr,
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "support": support,
        "resistance": resistance,
        "close": close,
    }


class UltimateBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 300,
        min_score: int = 80,
        volume_multiplier: float = 1.2,
        atr_stop_multiplier: float = 2.0,
        reward_risk_ratio: float = 2.0,
        max_risk_eur: float = 0.50,
        max_position_eur: float = 25.0,
        max_hold_sec: int = 72 * 3600,
        account_loss_limit_pct: float = 10.0,
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
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        self.state_path = state_path or data_dir / "ultimate_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "last_entry_scan": self.last_entry_scan,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
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
        fee = config.CRYPTO_TAKER_FEE_RATE
        pnl = exit_value - entry_cost - entry_cost * fee - exit_value * fee
        await resolve_trade(int(leg["trade_id"]), exit_price, round(pnl, 6))
        leg["open"] = False
        self.capital_remaining += entry_cost + pnl
        return pnl

    async def _close_position(self, pair: str, exit_price: float, reason: str) -> dict:
        position = self.portfolio[pair]
        pnl = 0.0
        for leg in self._open_legs(position):
            pnl += await self._resolve_leg(leg, exit_price)
        self.portfolio.pop(pair, None)
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
        risk_unit = max(avg_entry - stop, atr * 0.5)
        fee = config.CRYPTO_TAKER_FEE_RATE
        break_even = avg_entry * (1 + fee) / (1 - fee)
        if peak >= avg_entry + risk_unit:
            stop = max(stop, break_even)
        if peak >= avg_entry + 2 * risk_unit:
            stop = max(stop, peak - atr * self.atr_stop_multiplier)
        position["stop_price"] = stop

        tp_leg = next((leg for leg in self._open_legs(position) if leg.get("role") == "take_profit"), None)
        if tp_leg and bid >= avg_entry + self.reward_risk_ratio * risk_unit:
            pnl = await self._resolve_leg(tp_leg, bid)
            position["partial_taken"] = True
            self._save_state()
            return [{"pair": pair, "reason": "partial_profit", "pnl": pnl}]

        now = time.time()
        reason = "state_recovery_exit" if position.get("needs_recovery_exit") else None
        if not reason and bid <= stop: reason = "atr_stop"
        elif not reason and analysis["regime"] == "downtrend": reason = "regime_exit"
        elif not reason and analysis["macd"] and analysis["macd"][2] < 0 and bid < float(analysis["ema20"]): reason = "momentum_exit"
        elif not reason and now - float(position.get("entry_ts") or now) >= self.max_hold_sec: reason = "time_exit"
        if reason:
            return [await self._close_position(pair, bid, reason)]

        if not position.get("added") and bid <= float(position["initial_entry"]) - 0.75 * atr and bid > stop and analysis["regime"] != "downtrend":
            open_legs = self._open_legs(position)
            current_risk = sum(float(leg["shares"]) * max(0.0, float(leg["entry_price"]) - stop) for leg in open_legs)
            remaining_risk = max(0.0, self.max_risk_eur - current_risk)
            max_add_value = min(self.max_position_eur - sum(float(leg["cost_basis"]) for leg in open_legs), self.capital_remaining)
            add_shares = min(remaining_risk / max(bid - stop, 1e-9), max_add_value / float(snap["ask"]))
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
        equity = await self.equity()
        if equity["equity_eur"] <= self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100):
            return []
        raw_rows = await asyncio.gather(*(fetch_ohlc(pair, 60) for pair in PAIRS))
        candidates = []
        for pair, rows in zip(PAIRS, raw_rows):
            closed = closed_ohlc_rows(rows, 60, now)
            analysis = analyse_market(closed, volume_multiplier=self.volume_multiplier)
            if analysis and analysis["regime"] in {"uptrend", "sideways"} and analysis["setup"] and analysis["score"] >= self.min_score and analysis["atr"]:
                candidates.append((pair, analysis))
        self.last_entry_scan = now
        if not candidates:
            self._save_state()
            return []
        candidates.sort(key=lambda item: (item[1]["score"], bool(item[1]["patterns"]), item[1]["volume_ok"]), reverse=True)
        pair, analysis = candidates[0]
        ticker = await fetch_ticker_data([pair])
        snap = self._snapshot(pair, ticker)
        if not snap:
            return []
        price = float(snap["ask"])
        atr = float(analysis["atr"])
        stop = price - atr * self.atr_stop_multiplier
        stop_distance = price - stop
        # Half the risk and capital are reserved for the single permitted add.
        initial_risk = self.max_risk_eur / 2
        position_value = min(initial_risk / stop_distance * price, self.max_position_eur / 2, self.capital_remaining)
        if position_value < MIN_POSITION_EUR:
            return []
        target = price + self.reward_risk_ratio * stop_distance
        fee = config.CRYPTO_TAKER_FEE_RATE
        expected_net = (position_value / price) * target - position_value - position_value * fee - (position_value / price) * target * fee
        if expected_net <= 0:
            return []
        total_shares = position_value / price
        first_shares = total_shares / 2
        legs = []
        for role in ("take_profit", "runner"):
            cost = first_shares * price
            trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", first_shares, price, analysis["score"] / 100, f"paper_{role}")
            legs.append({"trade_id": trade_id, "role": role, "shares": first_shares, "entry_price": price, "cost_basis": cost, "open": True})
        self.capital_remaining -= position_value
        self.portfolio[pair] = {
            "legs": legs, "entry_ts": now, "initial_entry": price, "peak_price": price,
            "stop_price": stop, "initial_target": target, "regime": analysis["regime"],
            "setup": analysis["setup"], "score": analysis["score"], "added": False,
        }
        self.trade_count += 2
        self._save_state()
        logger.info("🏆 ULT Entry %s %.2f€ @ %.6f€ | %s/%s | Score %d | Stop %.6f€ | Ziel %.6f€", pair, position_value, price, analysis["regime"], analysis["setup"], analysis["score"], stop, target)
        return [{"pair": pair, "amount": position_value, "score": analysis["score"], "regime": analysis["regime"], "setup": analysis["setup"]}]

    async def equity(self) -> dict:
        realized = await get_realized_pnl_by_prefix(PREFIX)
        if not self.portfolio:
            return {"equity_eur": self.capital_remaining, "cash_eur": self.capital_remaining, "open_positions": 0, "unrealized_pnl_eur": 0.0, "realized_pnl_eur": realized}
        pairs = list(self.portfolio)
        ticker = await fetch_ticker_data(pairs)
        mtm = unrealized = 0.0
        fee = config.CRYPTO_TAKER_FEE_RATE
        for pair, position in self.portfolio.items():
            snap = self._snapshot(pair, ticker)
            for leg in self._open_legs(position):
                cost = float(leg["cost_basis"])
                if not snap:
                    mtm += cost
                    continue
                value = float(leg["shares"]) * float(snap["bid"])
                net = value - cost * fee - value * fee
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
