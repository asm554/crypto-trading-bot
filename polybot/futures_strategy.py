"""Kraken Perpetual-Futures Trend-Bot — Der Hebler (paper-only).

Der Bot handelt lineare Multi-Collateral-Perpetuals auf Basis der öffentlichen
Kraken-Futures-Marktdaten. Es werden weder API-Schlüssel noch Wallets benötigt:
alle Ausführungen, Gebühren, Funding-Zahlungen und Liquidationen sind simuliert.

Die Strategie handelt BTC, ETH und SOL long oder short. Ein 9/21-EMA-Trend auf
abgeschlossenen 1h-Mark-Price-Kerzen muss durch 6h-Momentum bestätigt sein.
Fills erfolgen konservativ als Taker: Long eröffnet zum Ask und schließt zum
Bid; Short eröffnet zum Bid und schließt zum Ask. Das Konto rechnet in EUR,
obwohl Kraken die linearen Kontrakte in USD notiert.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp

from polybot import paper_db as paper_db_module
from polybot.dca_strategy import fetch_ticker_data
from polybot.paper_db import (
    get_open_trades_by_prefix,
    log_equity_snapshot,
    log_paper_trade,
    resolve_trade,
)

logger = logging.getLogger(__name__)

PREFIX = "FUT_"
BOT_KEY = "futures"
FUTURES_API_BASE = "https://futures.kraken.com"
FUTURES_TICKERS_URL = f"{FUTURES_API_BASE}/derivatives/api/v3/tickers"
FUTURES_CHARTS_URL = f"{FUTURES_API_BASE}/api/charts/v1"
EURUSD_PAIR = "EURUSD"
EURUSD_INTERNAL = "ZEURZUSD"
FALLBACK_EUR_USD_RATE = 1.08

DEFAULT_SYMBOLS = ("PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD")
SYMBOL_LABELS = {
    "PF_XBTUSD": "BTC-PERP",
    "PF_ETHUSD": "ETH-PERP",
    "PF_SOLUSD": "SOL-PERP",
}

RESOLUTION_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


async def fetch_futures_tickers(symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, dict]:
    """Return Kraken Futures tickers keyed by symbol (public endpoint)."""
    wanted = set(symbols or ())
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FUTURES_TICKERS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
    except Exception as exc:
        logger.warning("Kraken Futures ticker unavailable: %s", exc)
        return {}
    if payload.get("result") != "success":
        logger.warning("Kraken Futures ticker error: %s", payload)
        return {}
    result = {}
    for item in payload.get("tickers") or []:
        symbol = str(item.get("symbol") or "")
        if symbol and (not wanted or symbol in wanted):
            result[symbol] = item
    return result


async def fetch_futures_candles(
    symbol: str,
    resolution: str = "1h",
    count: int = 80,
    tick_type: str = "mark",
) -> list[dict]:
    """Fetch closed Kraken Futures OHLC candles from the public charts API."""
    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(f"unsupported Futures candle resolution: {resolution}")
    url = f"{FUTURES_CHARTS_URL}/{quote(tick_type, safe='')}/{quote(symbol, safe='')}/{resolution}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"count": int(count)}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
    except Exception as exc:
        logger.warning("Kraken Futures candles unavailable for %s: %s", symbol, exc)
        return []
    candles = [c for c in (payload.get("candles") or []) if isinstance(c, dict)]
    candles.sort(key=lambda row: int(row.get("time") or 0))
    cutoff_ms = int((time.time() - RESOLUTION_SECONDS[resolution]) * 1000)
    return [c for c in candles if int(c.get("time") or 0) <= cutoff_ms]


async def fetch_eur_usd_rate() -> float:
    """Return USD per EUR so USD-settled PnL can join the EUR battle."""
    try:
        ticker = await fetch_ticker_data([EURUSD_PAIR])
        row = ticker.get(EURUSD_INTERNAL) or ticker.get(EURUSD_PAIR)
        rate = float(row["c"][0]) if row else 0.0
        if rate > 0:
            return rate
    except Exception as exc:
        logger.warning("EUR/USD conversion unavailable: %s", exc)
    return FALLBACK_EUR_USD_RATE


def ema(values: list[float], period: int) -> float:
    if period <= 0 or len(values) < period:
        raise ValueError("not enough values for EMA")
    value = sum(values[:period]) / period
    alpha = 2.0 / (period + 1.0)
    for current in values[period:]:
        value = current * alpha + value * (1.0 - alpha)
    return value


def trend_signal(
    candles: list[dict],
    fast_period: int = 9,
    slow_period: int = 21,
    momentum_bars: int = 6,
    min_momentum_pct: float = 0.8,
    min_trend_pct: float = 0.15,
) -> dict | None:
    """Return a confirmed long/short signal from closed mark-price candles."""
    closes = []
    for candle in candles:
        try:
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(close) and close > 0:
            closes.append(close)
    if len(closes) < max(slow_period + 2, momentum_bars + 1):
        return None
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    momentum = (closes[-1] / closes[-1 - momentum_bars] - 1.0) * 100.0
    trend = (fast / slow - 1.0) * 100.0
    side = None
    if closes[-1] > slow and trend >= min_trend_pct and momentum >= min_momentum_pct:
        side = "long"
    elif closes[-1] < slow and trend <= -min_trend_pct and momentum <= -min_momentum_pct:
        side = "short"
    if side is None:
        return None
    return {
        "side": side,
        "close": closes[-1],
        "fast_ema": fast,
        "slow_ema": slow,
        "momentum_pct": momentum,
        "trend_pct": trend,
        "score": abs(momentum) + abs(trend) * 2.0,
    }


def ticker_quote(ticker: dict) -> tuple[float, float, float]:
    mark = float(ticker.get("markPrice") or ticker.get("last") or 0.0)
    bid = float(ticker.get("bid") or mark)
    ask = float(ticker.get("ask") or mark)
    if min(mark, bid, ask) <= 0 or bid > ask:
        raise ValueError("invalid Futures quote")
    return mark, bid, ask


def position_mark_to_market(
    position: dict,
    ticker: dict | None,
    eur_usd_rate: float,
    taker_fee_rate: float,
    include_pending_funding: bool = False,
    now: float | None = None,
) -> tuple[float, float]:
    """Return (isolated position value, open PnL) in EUR.

    Entry fees are already removed from free cash. Position value therefore
    subtracts only the exit fee, while the reported open PnL includes both
    entry and estimated exit fees for a complete trade-level view.
    """
    margin = float(position.get("margin_eur") or 0.0)
    funding = float(position.get("funding_pnl_eur") or 0.0)
    if not ticker:
        reported_pnl = funding - float(position.get("entry_fee_eur") or 0.0)
        return margin + funding, reported_pnl
    mark, bid, ask = ticker_quote(ticker)
    direction = 1.0 if position.get("side") == "long" else -1.0
    quantity = float(position.get("quantity") or 0.0)
    entry_usd = float(position.get("entry_price_usd") or 0.0)
    fx = eur_usd_rate if eur_usd_rate > 0 else FALLBACK_EUR_USD_RATE
    if include_pending_funding:
        elapsed = max(0.0, float(now or time.time()) - float(position.get("last_funding_ts") or time.time()))
        # A current rate must not be back-filled over an arbitrarily long outage.
        hours = min(elapsed / 3600.0, 1.0)
        absolute_rate = float(ticker.get("fundingRate") or 0.0)
        absolute_rate = max(-mark * 0.005, min(mark * 0.005, absolute_rate))
        funding += -direction * quantity * absolute_rate * hours / fx
    gross = direction * quantity * (mark - entry_usd) / fx
    exit_fill = bid if direction > 0 else ask
    exit_fee = quantity * exit_fill * taker_fee_rate / fx
    value_pnl = gross + funding - exit_fee
    reported_pnl = value_pnl - float(position.get("entry_fee_eur") or 0.0)
    return margin + value_pnl, reported_pnl


class FuturesPaperBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
        interval_sec: int = 300,
        candle_resolution: str = "1h",
        fast_ema: int = 9,
        slow_ema: int = 21,
        momentum_bars: int = 6,
        min_momentum_pct: float = 0.8,
        min_trend_pct: float = 0.15,
        min_volume_usd: float = 10_000_000.0,
        position_margin_eur: float = 20.0,
        leverage: float = 2.0,
        max_open_positions: int = 2,
        hard_stop_pct: float = 2.0,
        take_profit_pct: float = 5.0,
        trailing_activation_pct: float = 2.0,
        trailing_distance_pct: float = 1.0,
        trailing_floor_pct: float = 0.5,
        max_hold_sec: int = 24 * 3600,
        cooldown_sec: int = 4 * 3600,
        max_spread_pct: float = 0.10,
        max_adverse_funding_rate: float = 0.0002,
        taker_fee_rate: float = 0.0005,
        maintenance_margin_rate: float = 0.05,
        daily_loss_halt_pct: float = 5.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        if not paper_mode:
            logger.warning("Futures live mode is intentionally not implemented")
            raise NotImplementedError("FuturesPaperBot is paper-only")
        if not 1.0 <= float(leverage) <= 3.0:
            raise ValueError("paper Futures leverage must be between 1x and 3x")
        if candle_resolution not in RESOLUTION_SECONDS:
            raise ValueError("unsupported Futures candle resolution")

        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.symbols = tuple(symbols)
        self.interval_sec = int(interval_sec)
        self.candle_resolution = candle_resolution
        self.fast_ema = int(fast_ema)
        self.slow_ema = int(slow_ema)
        self.momentum_bars = int(momentum_bars)
        self.min_momentum_pct = float(min_momentum_pct)
        self.min_trend_pct = float(min_trend_pct)
        self.min_volume_usd = float(min_volume_usd)
        self.position_margin_eur = float(position_margin_eur)
        self.leverage = float(leverage)
        self.max_open_positions = int(max_open_positions)
        self.hard_stop_pct = float(hard_stop_pct)
        self.take_profit_pct = float(take_profit_pct)
        self.trailing_activation_pct = float(trailing_activation_pct)
        self.trailing_distance_pct = float(trailing_distance_pct)
        self.trailing_floor_pct = float(trailing_floor_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.cooldown_sec = int(cooldown_sec)
        self.max_spread_pct = float(max_spread_pct)
        self.max_adverse_funding_rate = float(max_adverse_funding_rate)
        self.taker_fee_rate = float(taker_fee_rate)
        self.maintenance_margin_rate = float(maintenance_margin_rate)
        self.daily_loss_halt_pct = float(daily_loss_halt_pct)
        self.paper_mode = True
        self.snapshot_interval_sec = int(snapshot_interval_sec)

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "futures_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.cooldowns: dict[str, float] = {}
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "cooldowns": self.cooldowns,
            "last_entry_scan": self.last_entry_scan,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
            "leverage": self.leverage,
            "taker_fee_rate": self.taker_fee_rate,
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
                now = time.time()
                self.cooldowns = {k: float(v) for k, v in (raw.get("cooldowns") or {}).items() if float(v) > now}
                self.last_entry_scan = float(raw.get("last_entry_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                logger.info("Futures state loaded: cash=%.2f EUR open=%d", self.capital_remaining, len(self.portfolio))
                return
            except Exception as exc:
                logger.warning("Futures state invalid (%s); rebuilding from DB", exc)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.trade_count = 0
        if not self.db_path.exists():
            return
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT * FROM paper_trades WHERE market_question LIKE 'FUT\\_%' ESCAPE '\\' ORDER BY id ASC"
            ).fetchall()
        finally:
            con.close()
        realized = 0.0
        reserved = 0.0
        now = time.time()
        for row in rows:
            quantity = float(row["size"] or 0.0)
            entry_eur = float(row["price"] or 0.0)
            if quantity <= 0 or entry_eur <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is not None:
                realized += float(row["real_pnl"] or 0.0)
                continue
            symbol = str(row["market_question"]).removeprefix(PREFIX)
            side = "long" if str(row["side"]).lower() == "buy" else "short"
            notional_eur = quantity * entry_eur
            margin = notional_eur / self.leverage
            entry_fee = notional_eur * self.taker_fee_rate
            reserved += margin + entry_fee
            self.portfolio[symbol] = {
                "side": side,
                "quantity": quantity,
                "entry_price_usd": entry_eur * FALLBACK_EUR_USD_RATE,
                "entry_price_eur": entry_eur,
                "margin_eur": margin,
                "entry_fee_eur": entry_fee,
                "funding_pnl_eur": 0.0,
                "entry_ts": float(row["timestamp"] or now),
                "last_funding_ts": now,
                "peak_return_pct": 0.0,
                "trade_id": int(row["id"]),
            }
        self.capital_remaining = max(0.0, self.initial_capital_eur + realized - reserved)

    def _apply_funding(self, position: dict, ticker: dict, eur_usd_rate: float, now: float) -> float:
        elapsed = max(0.0, now - float(position.get("last_funding_ts") or now))
        if elapsed < 60.0:
            return 0.0
        mark, _bid, _ask = ticker_quote(ticker)
        hours = min(elapsed / 3600.0, 1.0)
        rate = float(ticker.get("fundingRate") or 0.0)
        rate = max(-mark * 0.005, min(mark * 0.005, rate))
        direction = 1.0 if position.get("side") == "long" else -1.0
        payment = -direction * float(position["quantity"]) * rate * hours / eur_usd_rate
        position["funding_pnl_eur"] = float(position.get("funding_pnl_eur") or 0.0) + payment
        position["last_funding_ts"] = now
        return payment

    async def manage_positions(self) -> list[dict]:
        if not self.portfolio:
            return []
        symbols = list(self.portfolio)
        tickers, eur_usd_rate = await asyncio.gather(fetch_futures_tickers(symbols), fetch_eur_usd_rate())
        now = time.time()
        resolved = []
        changed = False
        open_rows = {int(row["id"]): row for row in await get_open_trades_by_prefix(PREFIX)}
        for symbol, position in list(self.portfolio.items()):
            ticker = tickers.get(symbol)
            if not ticker:
                logger.info("FUT %s: no ticker; position unchanged", symbol)
                continue
            try:
                mark, bid, ask = ticker_quote(ticker)
            except ValueError:
                continue
            funding_delta = self._apply_funding(position, ticker, eur_usd_rate, now)
            changed = changed or funding_delta != 0.0
            direction = 1.0 if position["side"] == "long" else -1.0
            entry = float(position["entry_price_usd"])
            return_pct = direction * (mark / entry - 1.0) * 100.0
            peak_return = max(float(position.get("peak_return_pct") or 0.0), return_pct)
            if peak_return != float(position.get("peak_return_pct") or 0.0):
                position["peak_return_pct"] = peak_return
                changed = True

            quantity = float(position["quantity"])
            margin = float(position["margin_eur"])
            funding = float(position.get("funding_pnl_eur") or 0.0)
            gross_at_mark = direction * quantity * (mark - entry) / eur_usd_rate
            current_notional = quantity * mark / eur_usd_rate
            maintenance = current_notional * self.maintenance_margin_rate
            exit_fill = bid if direction > 0 else ask
            estimated_exit_fee = quantity * exit_fill * self.taker_fee_rate / eur_usd_rate
            isolated_equity = margin + gross_at_mark + funding
            age = now - float(position.get("entry_ts") or now)

            reason = None
            if isolated_equity <= maintenance + estimated_exit_fee:
                reason = "liquidation"
            elif return_pct <= -self.hard_stop_pct:
                reason = "hard_stop"
            elif return_pct >= self.take_profit_pct:
                reason = "take_profit"
            elif peak_return >= self.trailing_activation_pct:
                trailing_trigger = max(self.trailing_floor_pct, peak_return - self.trailing_distance_pct)
                if return_pct <= trailing_trigger:
                    reason = "trailing_stop"
            if reason is None and age >= self.max_hold_sec:
                reason = "time_exit"
            if reason is None:
                continue

            trade_id = int(position.get("trade_id") or 0)
            row = open_rows.get(trade_id)
            if not row:
                logger.warning("FUT %s: DB row %d missing; refusing to close state only", symbol, trade_id)
                continue
            actual_exit = bid if direction > 0 else ask
            gross = direction * quantity * (actual_exit - entry) / eur_usd_rate
            exit_fee = quantity * actual_exit * self.taker_fee_rate / eur_usd_rate
            liquidation_fee = maintenance * 0.5 if reason == "liquidation" else 0.0
            entry_fee = float(position.get("entry_fee_eur") or 0.0)
            real_pnl = gross + funding - entry_fee - exit_fee - liquidation_fee
            exit_price_eur = actual_exit / eur_usd_rate
            await resolve_trade(trade_id, exit_price_eur, round(real_pnl, 6))
            # Entry fee was paid when the position opened; do not subtract it twice.
            self.capital_remaining = max(
                0.0,
                self.capital_remaining + margin + gross + funding - exit_fee - liquidation_fee,
            )
            self.cooldowns[symbol] = now + self.cooldown_sec
            self.portfolio.pop(symbol, None)
            resolved.append({"symbol": symbol, "side": position["side"], "reason": reason, "pnl": real_pnl})
            changed = True
            logger.info(
                "FUT exit %s %s: %s @ %.4f USD | PnL %+.4f EUR (funding %+.4f)",
                position["side"], symbol, reason, actual_exit, real_pnl, funding,
            )
        if changed:
            self._save_state()
        return resolved

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_entry_scan < self.interval_sec:
            return []
        self.last_entry_scan = now
        tickers, eur_usd_rate = await asyncio.gather(fetch_futures_tickers(self.symbols), fetch_eur_usd_rate())
        current_equity = await self.equity(tickers=tickers, eur_usd_rate=eur_usd_rate)
        halt_level = self.initial_capital_eur * (1.0 - self.daily_loss_halt_pct / 100.0)
        if current_equity["equity_eur"] <= halt_level:
            logger.warning("FUT risk halt: equity %.2f EUR <= %.2f EUR", current_equity["equity_eur"], halt_level)
            self._save_state()
            return []

        available = [symbol for symbol in self.symbols if symbol not in self.portfolio and self.cooldowns.get(symbol, 0.0) <= now]
        candle_sets = await asyncio.gather(*[
            fetch_futures_candles(symbol, resolution=self.candle_resolution, count=max(80, self.slow_ema * 3))
            for symbol in available
        ])
        candidates = []
        for symbol, candles in zip(available, candle_sets):
            ticker = tickers.get(symbol)
            if not ticker or ticker.get("suspended") or ticker.get("postOnly"):
                continue
            try:
                mark, bid, ask = ticker_quote(ticker)
            except ValueError:
                continue
            spread_pct = (ask - bid) / mark * 100.0
            if spread_pct > self.max_spread_pct:
                logger.info("FUT %s skipped: spread %.3f%%", symbol, spread_pct)
                continue
            volume = float(ticker.get("volumeQuote") or 0.0)
            if volume < self.min_volume_usd:
                continue
            signal = trend_signal(
                candles,
                fast_period=self.fast_ema,
                slow_period=self.slow_ema,
                momentum_bars=self.momentum_bars,
                min_momentum_pct=self.min_momentum_pct,
                min_trend_pct=self.min_trend_pct,
            )
            if not signal:
                continue
            absolute_funding = float(ticker.get("fundingRate") or 0.0)
            relative_funding = absolute_funding / mark
            adverse = (signal["side"] == "long" and relative_funding > self.max_adverse_funding_rate) or (
                signal["side"] == "short" and relative_funding < -self.max_adverse_funding_rate
            )
            if adverse:
                logger.info("FUT %s skipped: adverse hourly funding %.4f%%", symbol, relative_funding * 100.0)
                continue
            signal.update({"symbol": symbol, "bid": bid, "ask": ask, "mark": mark, "volume": volume})
            signal["score"] *= math.log10(max(volume, 10.0))
            candidates.append(signal)
        candidates.sort(key=lambda item: float(item["score"]), reverse=True)

        opened = []
        for signal in candidates:
            if len(self.portfolio) >= self.max_open_positions:
                break
            margin = min(self.position_margin_eur, self.capital_remaining)
            estimated_entry_fee = margin * self.leverage * self.taker_fee_rate
            if margin < 5.0 or self.capital_remaining < margin + estimated_entry_fee:
                break
            side = str(signal["side"])
            fill_usd = float(signal["ask"] if side == "long" else signal["bid"])
            notional_usd = margin * self.leverage * eur_usd_rate
            quantity = notional_usd / fill_usd
            entry_fee = quantity * fill_usd * self.taker_fee_rate / eur_usd_rate
            entry_price_eur = fill_usd / eur_usd_rate
            db_side = "buy" if side == "long" else "sell"
            trade_id = await log_paper_trade(
                f"{PREFIX}{signal['symbol']}",
                db_side,
                quantity,
                entry_price_eur,
                abs(float(signal["momentum_pct"])) / 100.0,
                "paper",
            )
            self.capital_remaining -= margin + entry_fee
            self.portfolio[str(signal["symbol"])] = {
                "side": side,
                "quantity": quantity,
                "entry_price_usd": fill_usd,
                "entry_price_eur": entry_price_eur,
                "margin_eur": margin,
                "entry_fee_eur": entry_fee,
                "funding_pnl_eur": 0.0,
                "entry_ts": now,
                "last_funding_ts": now,
                "peak_return_pct": 0.0,
                "trade_id": trade_id,
            }
            self.trade_count += 1
            opened.append({
                "symbol": signal["symbol"],
                "side": side,
                "margin_eur": margin,
                "notional_eur": margin * self.leverage,
                "price_usd": fill_usd,
            })
            logger.info(
                "FUT entry %s %s: margin %.2f EUR, %.1fx, fill %.4f USD, signal %+.2f%%",
                side, signal["symbol"], margin, self.leverage, fill_usd, signal["momentum_pct"],
            )
        self._save_state()
        return opened

    async def equity(
        self,
        tickers: dict[str, dict] | None = None,
        eur_usd_rate: float | None = None,
    ) -> dict:
        if tickers is None:
            tickers = await fetch_futures_tickers(list(self.portfolio)) if self.portfolio else {}
        if eur_usd_rate is None:
            eur_usd_rate = await fetch_eur_usd_rate()
        open_value = 0.0
        unrealized = 0.0
        for symbol, position in self.portfolio.items():
            value, pnl = position_mark_to_market(
                position,
                tickers.get(symbol),
                eur_usd_rate,
                self.taker_fee_rate,
            )
            open_value += value
            unrealized += pnl
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": self.capital_remaining + open_value,
            "cash_eur": self.capital_remaining,
            "open_positions": len(self.portfolio),
            "unrealized_pnl_eur": unrealized,
            "realized_pnl_eur": realized,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **(await self.equity()))
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info(
            "Futures bot started [PAPER] | budget %.2f EUR | leverage %.1fx | symbols %s",
            self.initial_capital_eur, self.leverage, ",".join(self.symbols),
        )
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception as exc:
                logger.exception("Futures loop error (%s); retrying in 30s", exc)
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(30)
