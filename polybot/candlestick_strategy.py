"""Multi-timeframe candlestick paper bot — Der Kerzenreiter.

Signals use closed Kraken SOL/USDC candles. Paper fills are priced with Jupiter
quotes only; this module never builds, signs, or submits a transaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import paper_db as paper_db_module
from polybot.dca_strategy import PAIR_MAP, fetch_ticker_data
from polybot.paper_db import get_realized_pnl_by_prefix, log_equity_snapshot, log_paper_trade, resolve_trade
from polybot.surfer_strategy import atr_wilder, closed_ohlc_rows, ema_series, fetch_ohlc

logger = logging.getLogger(__name__)

PREFIX = "CND_"
BOT_KEY = "candlestick"
PAIR = "SOLUSDC"
EURUSD_PAIR = "EURUSD"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
MIN_POSITION_EUR = 1.0


def rsi_wilder(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def macd_snapshot(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    if len(values) < slow + signal:
        return None
    fast_series = ema_series(values, fast)
    slow_series = ema_series(values, slow)
    if not fast_series or not slow_series:
        return None
    offset = slow - fast
    macd_values = [fast_series[offset + i] - slow_series[i] for i in range(len(slow_series))]
    signal_values = ema_series(macd_values, signal)
    if not signal_values:
        return None
    macd = macd_values[-1]
    signal_value = signal_values[-1]
    return macd, signal_value, macd - signal_value


def bullish_patterns(rows: list[tuple]) -> list[str]:
    if len(rows) < 2:
        return []
    previous, current = rows[-2], rows[-1]
    prev_open, prev_close = float(previous[1]), float(previous[4])
    open_, high, low, close = map(float, (current[1], current[2], current[3], current[4]))
    body = max(abs(close - open_), 1e-12)
    patterns: list[str] = []
    if prev_close < prev_open and close > open_ and open_ <= prev_close and close >= prev_open:
        patterns.append("bullish_engulfing")
    lower_wick = min(open_, close) - low
    upper_wick = high - max(open_, close)
    if close > open_ and lower_wick >= 2 * body and upper_wick <= body:
        patterns.append("hammer")
    if high < float(previous[2]) and low > float(previous[3]) and close > open_:
        patterns.append("bullish_inside_bar")
    return patterns


def signal_score(rows_1h: list[tuple], rows_15m: list[tuple], *, volume_multiplier: float = 1.3, breakout_lookback: int = 20) -> dict:
    closes_1h = [float(row[4]) for row in rows_1h]
    closes = [float(row[4]) for row in rows_15m]
    volumes = [float(row[6]) for row in rows_15m]
    ema50 = ema_series(closes_1h, 50)
    ema200 = ema_series(closes_1h, 200)
    ema20 = ema_series(closes, 20)
    ema50_15 = ema_series(closes, 50)
    rsi = rsi_wilder(closes)
    macd = macd_snapshot(closes)
    patterns = bullish_patterns(rows_15m)
    trend = bool(ema50 and ema200 and ema50[-1] > ema200[-1])
    ema_ok = bool(ema20 and ema50_15 and ema20[-1] > ema50_15[-1] and closes[-1] > ema20[-1])
    macd_ok = bool(macd and macd[0] > macd[1] and macd[2] > 0)
    rsi_ok = rsi is not None and 52 <= rsi <= 70
    volume_window = volumes[-21:-1]
    avg_volume = sum(volume_window) / len(volume_window) if volume_window else 0.0
    volume_ok = avg_volume > 0 and volumes[-1] >= avg_volume * volume_multiplier
    prior_highs = [float(row[2]) for row in rows_15m[-(breakout_lookback + 1):-1]]
    breakout = bool(prior_highs and closes[-1] > max(prior_highs))
    score = (
        (25 if patterns else 0)
        + (20 if trend else 0)
        + (15 if ema_ok else 0)
        + (10 if macd_ok else 0)
        + (10 if rsi_ok else 0)
        + (10 if volume_ok else 0)
        + (10 if breakout else 0)
    )
    return {
        "score": score,
        "patterns": patterns,
        "trend": trend,
        "ema": ema_ok,
        "macd": macd_ok,
        "rsi": rsi,
        "rsi_ok": rsi_ok,
        "volume": volume_ok,
        "breakout": breakout,
    }


async def fetch_jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> dict | None:
    if amount <= 0:
        return None
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "slippageBps": str(int(slippage_bps)),
        "restrictIntermediateTokens": "true",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(JUPITER_QUOTE_URL, params=params) as response:
                if response.status != 200:
                    logger.warning("CND Jupiter quote HTTP %s", response.status)
                    return None
                payload = await response.json()
    except Exception as exc:
        logger.warning("CND Jupiter quote fehlgeschlagen: %s", exc)
        return None
    if not payload.get("outAmount"):
        return None
    return payload


class CandlestickBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 60,
        min_score: int = 75,
        volume_multiplier: float = 1.3,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.0,
        reward_risk_ratio: float = 1.8,
        max_risk_eur: float = 0.50,
        max_position_eur: float = 25.0,
        max_price_impact_pct: float = 0.5,
        slippage_bps: int = 50,
        max_hold_sec: int = 48 * 3600,
        loss_streak_limit: int = 3,
        loss_pause_sec: int = 24 * 3600,
        account_loss_limit_pct: float = 10.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 900,
        state_path: Path | None = None,
    ):
        if not paper_mode:
            raise NotImplementedError("CandlestickBot is paper-only")
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.min_score = int(min_score)
        self.volume_multiplier = float(volume_multiplier)
        self.atr_period = int(atr_period)
        self.atr_stop_multiplier = float(atr_stop_multiplier)
        self.reward_risk_ratio = float(reward_risk_ratio)
        self.max_risk_eur = float(max_risk_eur)
        self.max_position_eur = float(max_position_eur)
        self.max_price_impact_pct = float(max_price_impact_pct)
        self.slippage_bps = int(slippage_bps)
        self.max_hold_sec = int(max_hold_sec)
        self.loss_streak_limit = int(loss_streak_limit)
        self.loss_pause_sec = int(loss_pause_sec)
        self.account_loss_limit_pct = float(account_loss_limit_pct)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        self.state_path = state_path or data_dir / "candlestick_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.consecutive_losses = 0
        self.loss_pause_until = 0.0
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "consecutive_losses": self.consecutive_losses,
            "loss_pause_until": self.loss_pause_until,
            "last_entry_scan": self.last_entry_scan,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
            "updated_at": time.time(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(self.state_path)

    def _load_state_or_rebuild(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, float(raw.get("capital_remaining", self.initial_capital_eur)))
                self.portfolio = raw.get("portfolio") or {}
                self.consecutive_losses = int(raw.get("consecutive_losses", 0))
                self.loss_pause_until = float(raw.get("loss_pause_until", 0))
                self.last_entry_scan = float(raw.get("last_entry_scan", 0))
                self.last_snapshot = float(raw.get("last_snapshot", 0))
                self.trade_count = int(raw.get("trade_count", 0))
                return
            except Exception as exc:
                logger.warning("CND state unlesbar, DB-Rebuild: %s", exc)
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
        for row in rows:
            self.trade_count += 1
            if row["resolved_at"] is None:
                cost = float(row["size"]) * float(row["price"])
                open_cost += cost
                self.portfolio[PAIR] = {
                    "shares": float(row["size"]), "cost_basis": cost, "entry_price": float(row["price"]),
                    "entry_ts": float(row["timestamp"]), "trade_id": int(row["id"]), "needs_recovery_exit": True,
                }
            else:
                realized += float(row["real_pnl"] or 0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    @staticmethod
    async def _eurusd_rate() -> float | None:
        ticker = await fetch_ticker_data([EURUSD_PAIR])
        data = ticker.get(PAIR_MAP.get(EURUSD_PAIR, EURUSD_PAIR)) or ticker.get(EURUSD_PAIR)
        try:
            rate = float(data["c"][0])
            return rate if rate > 0 else None
        except Exception:
            return None

    async def _entry_quote(self, position_eur: float, eurusd: float) -> tuple[float, dict] | None:
        quote = await fetch_jupiter_quote(USDC_MINT, SOL_MINT, round(position_eur * eurusd * 1_000_000), self.slippage_bps)
        if not quote:
            return None
        # Worst acceptable output includes the configured slippage tolerance;
        # using the optimistic outAmount would overstate every paper fill.
        shares = float(quote.get("otherAmountThreshold") or quote["outAmount"]) / 1_000_000_000
        return (shares, quote) if shares > 0 else None

    async def _exit_quote(self, shares: float, eurusd: float) -> tuple[float, dict] | None:
        quote = await fetch_jupiter_quote(SOL_MINT, USDC_MINT, round(shares * 1_000_000_000), self.slippage_bps)
        if not quote:
            return None
        value_eur = (float(quote.get("otherAmountThreshold") or quote["outAmount"]) / 1_000_000) / eurusd
        return (value_eur, quote) if value_eur > 0 else None

    async def manage_positions(self) -> list[dict]:
        position = self.portfolio.get(PAIR)
        if not position:
            return []
        eurusd = await self._eurusd_rate()
        if not eurusd:
            return []
        exit_quote = await self._exit_quote(float(position["shares"]), eurusd)
        rows_15m = closed_ohlc_rows(await fetch_ohlc(PAIR, 15), 15)
        if not exit_quote or len(rows_15m) < 60:
            return []
        value_eur, _quote = exit_quote
        executable_price = value_eur / float(position["shares"])
        highs = [float(row[2]) / eurusd for row in rows_15m]
        lows = [float(row[3]) / eurusd for row in rows_15m]
        closes_usdc = [float(row[4]) for row in rows_15m]
        closes = [value / eurusd for value in closes_usdc]
        atr_usdc = atr_wilder([float(row[2]) for row in rows_15m], [float(row[3]) for row in rows_15m], closes_usdc, self.atr_period)
        atr_eur = (atr_usdc / eurusd) if atr_usdc else 0.0
        peak = max(float(position.get("peak_price") or position["entry_price"]), executable_price)
        position["peak_price"] = peak
        trailing_stop = peak - atr_eur * self.atr_stop_multiplier
        position["stop_price"] = max(float(position.get("stop_price") or 0), trailing_stop)
        ema20, ema50 = ema_series(closes, 20), ema_series(closes, 50)
        rsi = rsi_wilder(closes)
        macd = macd_snapshot(closes_usdc)
        bearish_structure = len(lows) >= 3 and lows[-1] < lows[-2] and highs[-1] < highs[-2]
        now = time.time()
        reason = "state_recovery_exit" if position.get("needs_recovery_exit") else None
        if not reason and executable_price <= float(position["stop_price"]): reason = "atr_trailing_stop"
        elif not reason and ema20 and ema50 and ema20[-1] < ema50[-1]: reason = "ema_trend_exit"
        elif not reason and ((rsi is not None and rsi < 45) or (macd and macd[2] < 0)): reason = "momentum_exit"
        elif not reason and bearish_structure: reason = "bearish_structure"
        elif not reason and now - float(position["entry_ts"]) >= self.max_hold_sec: reason = "time_exit"
        if not reason:
            self._save_state()
            return []
        entry_cost = float(position["cost_basis"])
        pnl = value_eur - entry_cost
        await resolve_trade(int(position["trade_id"]), executable_price, round(pnl, 6))
        self.capital_remaining += value_eur
        self.portfolio.pop(PAIR, None)
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0
        if self.consecutive_losses >= self.loss_streak_limit:
            self.loss_pause_until = now + self.loss_pause_sec
        self._save_state()
        logger.info("✅ CND Exit %s @ %.6f€ | PnL %+.4f€", reason, executable_price, pnl)
        return [{"pair": PAIR, "reason": reason, "pnl": pnl}]

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_entry_scan < self.interval_sec or self.portfolio or self.loss_pause_until > now:
            return []
        equity = await self.equity()
        if equity["equity_eur"] <= self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100):
            return []
        rows_1h, rows_15m = await asyncio.gather(fetch_ohlc(PAIR, 60), fetch_ohlc(PAIR, 15))
        rows_1h = closed_ohlc_rows(rows_1h, 60, now)
        rows_15m = closed_ohlc_rows(rows_15m, 15, now)
        if len(rows_1h) < 210 or len(rows_15m) < 60:
            return []
        self.last_entry_scan = now
        assessment = signal_score(rows_1h, rows_15m, volume_multiplier=self.volume_multiplier)
        required = all((assessment["patterns"], assessment["trend"], assessment["ema"], assessment["macd"], assessment["rsi_ok"], assessment["volume"]))
        if assessment["score"] < self.min_score or not required:
            self._save_state()
            return []
        eurusd = await self._eurusd_rate()
        if not eurusd:
            return []
        highs = [float(row[2]) for row in rows_15m]
        lows = [float(row[3]) for row in rows_15m]
        closes = [float(row[4]) for row in rows_15m]
        atr_usdc = atr_wilder(highs, lows, closes, self.atr_period)
        if not atr_usdc:
            return []
        risk_per_sol_eur = atr_usdc * self.atr_stop_multiplier / eurusd
        if risk_per_sol_eur <= 0:
            return []
        position_eur = min(self.max_position_eur, self.capital_remaining, self.max_risk_eur / risk_per_sol_eur * closes[-1] / eurusd)
        if position_eur < MIN_POSITION_EUR:
            return []
        entry = await self._entry_quote(position_eur, eurusd)
        if not entry:
            return []
        shares, buy_quote = entry
        impact = float(buy_quote.get("priceImpactPct") or 0) * 100
        if impact > self.max_price_impact_pct:
            return []
        entry_price = position_eur / shares
        immediate_exit = await self._exit_quote(shares, eurusd)
        if not immediate_exit:
            return []
        round_trip_cost = position_eur - immediate_exit[0]
        stop_price = entry_price - risk_per_sol_eur
        expected_target = entry_price + (entry_price - stop_price) * self.reward_risk_ratio
        expected_net = shares * expected_target - position_eur - max(0.0, round_trip_cost)
        if expected_net <= 0:
            return []
        trade_id = await log_paper_trade(f"{PREFIX}{PAIR}", "buy", shares, entry_price, assessment["score"] / 100, "paper_jupiter")
        self.capital_remaining -= position_eur
        self.portfolio[PAIR] = {
            "shares": shares, "cost_basis": position_eur, "entry_price": entry_price, "entry_ts": now,
            "peak_price": entry_price, "stop_price": stop_price, "initial_target": expected_target,
            "score": assessment["score"], "patterns": assessment["patterns"], "trade_id": trade_id,
        }
        self.trade_count += 1
        self._save_state()
        logger.info("🕯️ CND Entry %.2f€ @ %.6f€ | Score %d | Stop %.6f€ | R:R %.1f", position_eur, entry_price, assessment["score"], stop_price, self.reward_risk_ratio)
        return [{"pair": PAIR, "trade_id": trade_id, "score": assessment["score"], "entry_price": entry_price}]

    async def equity(self) -> dict:
        realized = await get_realized_pnl_by_prefix(PREFIX)
        position = self.portfolio.get(PAIR)
        if not position:
            return {"equity_eur": self.capital_remaining, "cash_eur": self.capital_remaining, "open_positions": 0, "unrealized_pnl_eur": 0.0, "realized_pnl_eur": realized}
        eurusd = await self._eurusd_rate()
        quote = await self._exit_quote(float(position["shares"]), eurusd) if eurusd else None
        value = quote[0] if quote else float(position["cost_basis"])
        unrealized = value - float(position["cost_basis"])
        return {"equity_eur": self.capital_remaining + value, "cash_eur": self.capital_remaining, "open_positions": 1, "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

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
                logger.exception("CND loop failed")
            await asyncio.sleep(30)
