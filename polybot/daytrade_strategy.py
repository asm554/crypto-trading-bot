"""Daytrader paper bot — Der Zappler.

Strukturell eine Parametervariante von momentum_strategy.py: kurzfristiges
Intraday-Momentum statt 24h-Trendfolge. Läuft durchgehend (24/7, kein
künstlicher Session-Cutoff - Krypto handelt ohnehin rund um die Uhr).
Unterscheidet sich von "Der Zocker" durch ein 4h- statt 24h-Signalfenster,
schnelleren Scan und einen kurzen rollierenden Max-Hold statt fester
Tagesgrenze.
"""

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
from polybot.dca_strategy import CANDIDATE_PAIRS, KRAKEN_PUBLIC, PAIR_MAP, extract_quote, fetch_ticker_data, rolling_change_pct
from polybot.paper_db import get_open_trades_by_prefix, log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "DAY_"
BOT_KEY = "daytrade"

LOOKBACK_INTERVAL_MIN = 60  # stündliche OHLC-Kerzen, wie bei DCA/Momentum/MeanRev


async def fetch_ohlc(pair: str, interval_min: int = LOOKBACK_INTERVAL_MIN) -> list[tuple]:
    internal = PAIR_MAP.get(pair, pair)
    url = f"{KRAKEN_PUBLIC}/OHLC?pair={internal}&interval={int(interval_min)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as exc:
        logger.warning("OHLC fetch %s fehlgeschlagen: %s", pair, exc)
        return []
    for key, rows in data.get("result", {}).items():
        if key == "last" or not isinstance(rows, list):
            continue
        parsed = []
        for row in rows:
            try:
                parsed.append(tuple(float(value) for value in row[:7]))
            except (TypeError, ValueError):
                continue
        # Die letzte Kraken-Zeile ist die aktuell laufende Kerze.
        return parsed[:-1] if len(parsed) > 1 else []
    return []


def relative_volume(rows: list[tuple], lookback_bars: int = 20) -> float | None:
    if lookback_bars <= 0 or len(rows) < lookback_bars + 1:
        return None
    current_volume = rows[-1][6]
    previous = [row[6] for row in rows[-(lookback_bars + 1):-1]]
    average = sum(previous) / len(previous)
    if average <= 0:
        return None
    return current_volume / average


class DaytradeBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 300,
        lookback_hours: int = 4,
        entry_change_pct: float = 3.0,
        entry_max_change_pct: float = 25.0,
        min_volume_eur: float = 500_000.0,
        volume_spike_enabled: bool = True,
        volume_lookback_bars: int = 20,
        volume_multiplier: float = 2.0,
        position_eur: float = 10.0,
        max_open_positions: int = 4,
        trailing_stop_pct: float = 1.5,
        hard_stop_pct: float = 3.0,
        max_hold_sec: int = 6 * 3600,
        cooldown_sec: int = 2 * 3600,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.lookback_hours = int(lookback_hours)
        self.entry_change_pct = float(entry_change_pct)
        self.entry_max_change_pct = float(entry_max_change_pct)
        self.min_volume_eur = float(min_volume_eur)
        self.volume_spike_enabled = bool(volume_spike_enabled)
        self.volume_lookback_bars = int(volume_lookback_bars)
        self.volume_multiplier = float(volume_multiplier)
        self.position_eur = float(position_eur)
        self.max_open_positions = int(max_open_positions)
        self.trailing_stop_pct = float(trailing_stop_pct)
        self.hard_stop_pct = float(hard_stop_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.cooldown_sec = int(cooldown_sec)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        if not self.paper_mode:
            logger.warning("Daytrade live mode is intentionally not implemented")
            raise NotImplementedError("DaytradeBot is paper-only")

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "daytrade_state.json"
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
                self.cooldowns = {k: float(v) for k, v in (raw.get("cooldowns") or {}).items() if float(v) > time.time()}
                self.last_entry_scan = float(raw.get("last_entry_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                logger.info("♻️ Daytrade state geladen: cash=%.2f€, open=%d", self.capital_remaining, len(self.portfolio))
                return
            except Exception as e:
                logger.warning("Daytrade state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
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
                self.portfolio[pair] = {
                    "shares": size,
                    "cost_basis": amount,
                    "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    "peak_price": price,
                    "trade_id": int(row["id"]),
                }
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)
        logger.info("🧱 Daytrade rebuild: cash=%.2f€, open=%d, trades=%d", self.capital_remaining, len(self.portfolio), self.trade_count)

    @staticmethod
    def _snapshot_for_pair(pair: str, ticker: dict) -> dict | None:
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        if not data:
            return None
        try:
            open_price = float(data["o"])
            last = float(data["c"][0])
            high = float(data["h"][1])
            low = float(data["l"][1])
            volume_coin = float(data["v"][1])
            vwap = float(data["p"][1])
            if open_price <= 0 or last <= 0:
                return None
            volume_eur = volume_coin * vwap
            change_pct = (last - open_price) / open_price * 100
            score = change_pct * math.log10(max(volume_eur, 1.0))
            bid, ask = extract_quote(data, last)
            return {"pair": pair, "last_price": last, "bid": bid, "ask": ask, "change_pct": change_pct, "volume_eur": volume_eur, "score": score, "high": high, "low": low}
        except Exception:
            return None

    async def manage_positions(self) -> list[dict]:
        if not self.portfolio:
            return []
        ticker = await fetch_ticker_data(list(self.portfolio.keys()))
        resolved = []
        now = time.time()
        open_rows = {int(r["id"]): r for r in await get_open_trades_by_prefix(PREFIX)}
        for pair, pos in list(self.portfolio.items()):
            snap = self._snapshot_for_pair(pair, ticker)
            if not snap:
                logger.info("⏭️ DAY %s: kein Ticker – Position unverändert", pair)
                continue
            last = float(snap["last_price"])
            entry = float(pos.get("entry_price") or 0.0)
            peak = max(float(pos.get("peak_price") or entry), last)
            pos["peak_price"] = peak
            age = now - float(pos.get("entry_ts") or now)
            reason = None
            if last <= peak * (1 - self.trailing_stop_pct / 100):
                reason = "trailing_stop"
            elif last <= entry * (1 - self.hard_stop_pct / 100):
                reason = "hard_stop"
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
            resolved.append({"pair": pair, "reason": reason, "pnl": real_pnl})
            logger.info("✅ DAY Exit %s: %s @ %.6f€ (Last %.6f€) | PnL %+0.4f€", pair, reason, exit_price, last, real_pnl)
        if resolved:
            self._save_state()
        return resolved

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_entry_scan < self.interval_sec:
            return []
        self.last_entry_scan = now
        ticker = await fetch_ticker_data(CANDIDATE_PAIRS)
        candidates = []
        for pair in CANDIDATE_PAIRS:
            snap = self._snapshot_for_pair(pair, ticker)
            if not snap:
                logger.info("⏭️ DAY %s: kein Ticker", pair)
                continue
            if pair in self.portfolio:
                logger.info("⏭️ DAY %s: bereits offen", pair)
                continue
            if self.cooldowns.get(pair, 0.0) > now:
                logger.info("⏭️ DAY %s: Cooldown aktiv", pair)
                continue
            # Kurzfristiges Intraday-Momentum statt 24h-Trend - der Punkt, der
            # "Der Zappler" von "Der Zocker" unterscheidet.
            ch_short = await rolling_change_pct(pair, lookback_bars=self.lookback_hours, interval_min=LOOKBACK_INTERVAL_MIN)
            if ch_short is None:
                logger.info("⏭️ DAY %s: keine %dh-OHLC", pair, self.lookback_hours)
                continue
            snap["change_pct"] = ch_short
            snap["score"] = ch_short * math.log10(max(snap["volume_eur"], 1.0))
            if not (self.entry_change_pct <= snap["change_pct"] <= self.entry_max_change_pct):
                logger.info("⏭️ DAY %s: Change %.2f%% nicht in %.2f..%.2f%%", pair, snap["change_pct"], self.entry_change_pct, self.entry_max_change_pct)
                continue
            if snap["volume_eur"] < self.min_volume_eur:
                logger.info("⏭️ DAY %s: Volumen %.0f€ < %.0f€", pair, snap["volume_eur"], self.min_volume_eur)
                continue
            volume_ratio = None
            if self.volume_spike_enabled:
                ohlc = await fetch_ohlc(pair, LOOKBACK_INTERVAL_MIN)
                volume_ratio = relative_volume(ohlc, self.volume_lookback_bars)
                if volume_ratio is None or volume_ratio < self.volume_multiplier:
                    logger.info("⏭️ DAY %s: relatives Volumen %s < %.2fx", pair, f"{volume_ratio:.2f}x" if volume_ratio is not None else "n/a", self.volume_multiplier)
                    continue
                snap["score"] *= volume_ratio
            snap["volume_ratio"] = volume_ratio
            candidates.append(snap)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        opened = []
        for snap in candidates:
            if len(self.portfolio) >= self.max_open_positions:
                break
            amount = min(self.position_eur, self.capital_remaining)
            if amount < 1.0:
                logger.info("⏭️ DAY: Cash %.2f€ reicht nicht", self.capital_remaining)
                break
            pair = snap["pair"]
            last = float(snap["last_price"])
            # Einstiegsfilter oben entscheidet auf Last, gekauft wird zum Ask.
            price = float(snap.get("ask") or last)
            shares = amount / price
            trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", shares, price, snap["change_pct"] / 100, "paper")
            self.capital_remaining -= amount
            self.portfolio[pair] = {"shares": shares, "cost_basis": amount, "entry_price": price, "entry_ts": time.time(), "peak_price": last, "trade_id": trade_id}
            self.trade_count += 1
            opened.append({"pair": pair, "amount": amount, "price": price, "volume_ratio": snap.get("volume_ratio")})
            logger.info("📝 DAY Entry %s: %.2f€ @ %.6f€ (Last %.6f€) | %dh %+0.2f%% | rel. Volumen %s", pair, amount, price, last, self.lookback_hours, snap["change_pct"], f"{snap['volume_ratio']:.2f}x" if snap.get("volume_ratio") is not None else "off")
        self._save_state()
        return opened

    async def equity(self) -> dict:
        ticker = await fetch_ticker_data(list(self.portfolio.keys())) if self.portfolio else {}
        unrealized = 0.0
        mtm = 0.0
        trade_pnls: dict[int, float] = {}
        fee = config.CRYPTO_TAKER_FEE_RATE
        for pair, pos in self.portfolio.items():
            entry_cost = float(pos["cost_basis"])
            snap = self._snapshot_for_pair(pair, ticker)
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

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        e = await self.equity()
        await log_equity_snapshot(BOT_KEY, **e)
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 Daytrade-Bot gestartet [PAPER] | Budget %.2f€ | Lookback %dh", self.initial_capital_eur, self.lookback_hours)
        await self.maybe_snapshot(force=True)
        while True:
            try:
                resolved = await self.manage_positions()
                opened = await self.scan_entries()
                await self.maybe_snapshot(force=bool(resolved or opened))
            except Exception as e:
                logger.exception("⚠️ Daytrade-Loop-Fehler (%s) – weiter in 30s", e)
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(30)
