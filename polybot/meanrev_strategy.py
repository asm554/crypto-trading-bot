"""Mean-reversion paper bot — Der Contrarian."""

import asyncio
import json
import logging
import math
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import CANDIDATE_PAIRS, KRAKEN_PUBLIC, PAIR_MAP, extract_quote, fetch_ticker_data, rolling_24h_change_pct
from polybot.paper_db import get_open_trades_by_prefix, log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "REV_"
BOT_KEY = "meanrev"


async def fetch_ohlc(pair: str, interval_min: int = 60) -> list[tuple]:
    internal = PAIR_MAP.get(pair, pair)
    url = f"{KRAKEN_PUBLIC}/OHLC?pair={internal}&interval={int(interval_min)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.warning("OHLC fetch %s fehlgeschlagen: %s", pair, e)
        return []
    result = data.get("result", {})
    rows = None
    for k, v in result.items():
        if k != "last" and isinstance(v, list):
            rows = v
            break
    out = []
    for r in rows or []:
        try:
            out.append((float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])))
        except Exception:
            continue
    # Kraken liefert die laufende, noch nicht abgeschlossene Kerze als letzte Zeile.
    return out[:-1] if len(out) > 1 else []


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_lower(closes: list[float], period: int = 20, stddev_multiplier: float = 2.0) -> float | None:
    if period <= 1 or len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((value - mean) ** 2 for value in window) / period
    return mean - float(stddev_multiplier) * math.sqrt(variance)


def stochastic_k(rows: list[tuple], period: int = 14) -> float | None:
    if period <= 1 or len(rows) < period:
        return None
    window = rows[-period:]
    lowest_low = min(row[3] for row in window)
    highest_high = max(row[2] for row in window)
    price_range = highest_high - lowest_low
    if price_range <= 0:
        return None
    return (window[-1][4] - lowest_low) / price_range * 100


class MeanRevBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 3600,
        entry_drop_pct: float = 8.0,
        rsi_period: int = 14,
        rsi_max: float = 30.0,
        bollinger_enabled: bool = True,
        bollinger_period: int = 20,
        bollinger_stddev: float = 2.0,
        stochastic_enabled: bool = True,
        stochastic_period: int = 14,
        stochastic_max: float = 20.0,
        confirm_pct: float = 0.5,
        position_eur: float = 15.0,
        max_open_positions: int = 3,
        take_profit_pct: float = 4.0,
        stop_loss_pct: float = 5.0,
        max_hold_sec: int = 96 * 3600,
        cooldown_sec: int = 12 * 3600,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.entry_drop_pct = float(entry_drop_pct)
        self.rsi_period = int(rsi_period)
        self.rsi_max = float(rsi_max)
        self.bollinger_enabled = bool(bollinger_enabled)
        self.bollinger_period = int(bollinger_period)
        self.bollinger_stddev = float(bollinger_stddev)
        self.stochastic_enabled = bool(stochastic_enabled)
        self.stochastic_period = int(stochastic_period)
        self.stochastic_max = float(stochastic_max)
        self.confirm_pct = float(confirm_pct)
        self.position_eur = float(position_eur)
        self.max_open_positions = int(max_open_positions)
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.cooldown_sec = int(cooldown_sec)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        if not self.paper_mode:
            logger.warning("MeanRev live mode is intentionally not implemented")
            raise NotImplementedError("MeanRevBot is paper-only")
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        self.state_path = data_dir / "meanrev_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.cooldowns: dict[str, float] = {}
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._load_state_or_rebuild()

    def _save_state(self):
        payload = {"capital_remaining": round(self.capital_remaining, 8), "portfolio": self.portfolio, "cooldowns": self.cooldowns, "last_entry_scan": self.last_entry_scan, "last_snapshot": self.last_snapshot, "trade_count": self.trade_count, "updated_at": time.time()}
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(self.state_path)

    def _load_state_or_rebuild(self):
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, float(raw.get("capital_remaining", self.initial_capital_eur)))
                self.portfolio = raw.get("portfolio") or {}
                self.cooldowns = {k: float(v) for k, v in (raw.get("cooldowns") or {}).items() if float(v) > time.time()}
                self.last_entry_scan = float(raw.get("last_entry_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                logger.info("♻️ MeanRev state geladen: cash=%.2f€, open=%d", self.capital_remaining, len(self.portfolio))
                return
            except Exception as e:
                logger.warning("MeanRev state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self):
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.trade_count = 0
        realized = 0.0
        open_cost = 0.0
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM paper_trades WHERE market_question LIKE ? ORDER BY id ASC", (f"{PREFIX}%",)).fetchall()
        finally:
            conn.close()
        for row in rows:
            pair = str(row["market_question"]).removeprefix(PREFIX)
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            amount = size * price
            if size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += amount
                self.portfolio[pair] = {"shares": size, "cost_basis": amount, "entry_price": price, "entry_ts": float(row["timestamp"] or time.time()), "trade_id": int(row["id"])}
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    @staticmethod
    def _ticker_snapshot(pair: str, ticker: dict) -> dict | None:
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        if not data:
            return None
        try:
            open_price = float(data["o"])
            last = float(data["c"][0])
            volume_eur = float(data["v"][1]) * float(data["p"][1])
            if open_price <= 0 or last <= 0:
                return None
            bid, ask = extract_quote(data, last)
            return {"pair": pair, "last_price": last, "bid": bid, "ask": ask, "change_pct": (last - open_price) / open_price * 100, "volume_eur": volume_eur}
        except Exception:
            return None

    async def manage_positions(self):
        if not self.portfolio:
            return []
        ticker = await fetch_ticker_data(list(self.portfolio.keys()))
        open_rows = {int(r["id"]): r for r in await get_open_trades_by_prefix(PREFIX)}
        out = []
        now = time.time()
        for pair, pos in list(self.portfolio.items()):
            snap = self._ticker_snapshot(pair, ticker)
            if not snap:
                logger.info("⏭️ REV %s: kein Ticker – Position unverändert", pair)
                continue
            last = float(snap["last_price"])
            entry = float(pos["entry_price"])
            price_change = (last - entry) / entry * 100 if entry > 0 else 0.0
            age = now - float(pos.get("entry_ts") or now)
            reason = None
            if price_change >= self.take_profit_pct:
                reason = "take_profit"
            elif price_change <= -self.stop_loss_pct:
                reason = "stop_loss"
            elif age >= self.max_hold_sec:
                reason = "time_exit"
            if not reason:
                continue
            trade_id = int(pos.get("trade_id") or 0)
            row = open_rows.get(trade_id)
            shares = float(pos.get("shares") or (row or {}).get("size") or 0.0)
            # Trigger oben entscheidet auf Last, verkauft wird zum Bid.
            exit_price = float(snap.get("bid") or last)
            entry_cost = shares * entry
            current_value = shares * exit_price
            fee = config.CRYPTO_TAKER_FEE_RATE
            real_pnl = current_value - entry_cost - entry_cost * fee - current_value * fee
            await resolve_trade(trade_id, exit_price, round(real_pnl, 6))
            self.capital_remaining += entry_cost + real_pnl
            self.cooldowns[pair] = now + self.cooldown_sec
            self.portfolio.pop(pair, None)
            out.append({"pair": pair, "reason": reason, "pnl": real_pnl})
            logger.info("✅ REV Exit %s: %s @ %.6f€ (Last %.6f€) | PnL %+0.4f€", pair, reason, exit_price, last, real_pnl)
        if out:
            self._save_state()
        return out

    async def scan_entries(self):
        now = time.time()
        if now - self.last_entry_scan < self.interval_sec:
            return []
        self.last_entry_scan = now
        ticker = await fetch_ticker_data(CANDIDATE_PAIRS)
        pre = []
        for pair in CANDIDATE_PAIRS:
            snap = self._ticker_snapshot(pair, ticker)
            if not snap:
                logger.info("⏭️ REV %s: kein Ticker", pair)
                continue
            if pair in self.portfolio:
                logger.info("⏭️ REV %s: bereits offen", pair)
                continue
            if self.cooldowns.get(pair, 0.0) > now:
                logger.info("⏭️ REV %s: Cooldown aktiv", pair)
                continue
            # Echte 24h-Bewegung statt Tages-Open (springt sonst um Mitternacht auf 0).
            ch24 = await rolling_24h_change_pct(pair)
            if ch24 is None:
                logger.info("⏭️ REV %s: keine 24h-OHLC", pair)
                continue
            snap["change_pct"] = ch24
            if snap["change_pct"] > -self.entry_drop_pct:
                logger.info("⏭️ REV %s: Drop %.2f%% > -%.2f%%", pair, snap["change_pct"], self.entry_drop_pct)
                continue
            pre.append(snap)
        opened = []
        for snap in pre:
            if len(self.portfolio) >= self.max_open_positions:
                break
            pair = snap["pair"]
            ohlc = await fetch_ohlc(pair, 60)
            await asyncio.sleep(1)
            min_bars = max(
                self.rsi_period + 1,
                self.bollinger_period if self.bollinger_enabled else 0,
                self.stochastic_period if self.stochastic_enabled else 0,
                8,
            )
            if len(ohlc) < min_bars:
                logger.info("⏭️ REV %s: zu wenig OHLC-Daten", pair)
                continue
            closes = [r[4] for r in ohlc]
            rsi = rsi_wilder(closes, self.rsi_period)
            if rsi is None or rsi >= self.rsi_max:
                logger.info("⏭️ REV %s: RSI %.1f >= %.1f", pair, rsi if rsi is not None else -1, self.rsi_max)
                continue
            lower_band = bollinger_lower(closes, self.bollinger_period, self.bollinger_stddev) if self.bollinger_enabled else None
            signal_close = closes[-1]
            if self.bollinger_enabled and (lower_band is None or signal_close > lower_band):
                logger.info("⏭️ REV %s: Close %.6f nicht unter Bollinger-Unterband %.6f", pair, signal_close, lower_band if lower_band is not None else -1)
                continue
            stoch_k = stochastic_k(ohlc, self.stochastic_period) if self.stochastic_enabled else None
            if self.stochastic_enabled and (stoch_k is None or stoch_k >= self.stochastic_max):
                logger.info("⏭️ REV %s: Stochastic %%K %.1f >= %.1f", pair, stoch_k if stoch_k is not None else -1, self.stochastic_max)
                continue
            low6 = min(r[3] for r in ohlc[-6:])
            last = float(snap["last_price"])
            if last < low6 * (1 + self.confirm_pct / 100):
                logger.info("⏭️ REV %s: keine Stabilisierung last %.6f < low6 %.6f + %.2f%%", pair, last, low6, self.confirm_pct)
                continue
            amount = min(self.position_eur, self.capital_remaining)
            if amount < 1.0:
                logger.info("⏭️ REV: Cash %.2f€ reicht nicht", self.capital_remaining)
                break
            # Stabilisierungs-/RSI-Filter oben entscheiden auf Last, gekauft wird zum Ask.
            fill_price = float(snap.get("ask") or last)
            shares = amount / fill_price
            trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", shares, fill_price, abs(snap["change_pct"]) / 100, "paper")
            self.capital_remaining -= amount
            self.portfolio[pair] = {"shares": shares, "cost_basis": amount, "entry_price": fill_price, "entry_ts": time.time(), "trade_id": trade_id}
            self.trade_count += 1
            opened.append({"pair": pair, "amount": amount, "price": fill_price, "rsi": rsi, "bollinger_lower": lower_band, "stochastic_k": stoch_k})
            logger.info("📝 REV Entry %s: %.2f€ @ %.6f€ (Last %.6f€) | drop %.2f%% RSI %.1f BB %s Stoch %s", pair, amount, fill_price, last, snap["change_pct"], rsi, f"{lower_band:.6f}" if lower_band is not None else "off", f"{stoch_k:.1f}" if stoch_k is not None else "off")
        self._save_state()
        return opened

    async def equity(self):
        ticker = await fetch_ticker_data(list(self.portfolio.keys())) if self.portfolio else {}
        unrealized = 0.0
        mtm = 0.0
        trade_pnls: dict[int, float] = {}
        fee = config.CRYPTO_TAKER_FEE_RATE
        for pair, pos in self.portfolio.items():
            entry_cost = float(pos["cost_basis"])
            snap = self._ticker_snapshot(pair, ticker)
            if not snap:
                mtm += entry_cost
                trade_pnls[int(pos["trade_id"])] = 0.0
                continue
            # Mark-to-Market simuliert den Verkauf, also zum Bid bewerten.
            current_value = float(pos["shares"]) * float(snap.get("bid") or snap["last_price"])
            sell_fee = current_value * fee
            position_pnl = current_value - entry_cost - entry_cost * fee - sell_fee
            mtm += entry_cost + position_pnl
            unrealized += position_pnl
            trade_pnls[int(pos["trade_id"])] = position_pnl
        await paper_db_module.update_unrealized_pnls(trade_pnls)
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {"equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining, "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

    async def maybe_snapshot(self, force: bool = False):
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **(await self.equity()))
        self.last_snapshot = now
        self._save_state()

    async def run(self):
        logger.info("🤖 MeanRev-Bot gestartet [PAPER] | Budget %.2f€", self.initial_capital_eur)
        await self.maybe_snapshot(force=True)
        while True:
            try:
                resolved = await self.manage_positions()
                opened = await self.scan_entries()
                await self.maybe_snapshot(force=bool(resolved or opened))
            except Exception as e:
                logger.exception("⚠️ MeanRev-Loop-Fehler (%s) – weiter in 60s", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(60)
