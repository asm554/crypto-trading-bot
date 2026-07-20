"""Trend/Breakout paper bot — Der Surfer.

Paper-only Kraken SOL/EUR Strategie. Handelt ausschließlich SOL/EUR und hält
maximal eine Position gleichzeitig. Einstieg erfordert gleichzeitig einen
bestätigten 4h-Aufwärtstrend, EMA20 über EMA50, einen Ausbruch über das
20h-Hoch und erhöhtes Volumen. Exit ist ein ATR-Stop plus Trailing-Stop
("wer zuerst greift, gewinnt" – der jeweils höhere der beiden Preise) sowie
ein EMA-Trendbruch-Exit und ein 7-Tage-Zeitlimit. Kein Order-Execution-Code,
keine Live-Order-Unterstützung.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import KRAKEN_PUBLIC, PAIR_MAP, extract_quote, fetch_ticker_data, rolling_change_pct
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "SURF_"
BOT_KEY = "surfer"

PAIR = "SOLEUR"
OHLC_INTERVAL_MIN = 60  # stündliche Kerzen, wie bei DCA/Momentum/MeanRev/Daytrade
MIN_POSITION_EUR = 1.0  # kleinste sinnvolle Paper-Positionsgröße


async def fetch_ohlc(pair: str, interval_min: int = OHLC_INTERVAL_MIN) -> list[tuple]:
    """Stündliche Kerzen (time, open, high, low, close, vwap, volume) von Kraken."""
    internal = PAIR_MAP.get(pair, pair)
    url = f"{KRAKEN_PUBLIC}/OHLC?pair={internal}&interval={int(interval_min)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.warning("Surfer OHLC fetch %s fehlgeschlagen: %s", pair, e)
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
    return out


def ema_series(values: list[float], period: int) -> list[float] | None:
    """Exponentieller gleitender Durchschnitt, geseedet mit dem SMA der ersten ``period`` Werte."""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    series = [sum(values[:period]) / period]
    for price in values[period:]:
        series.append((price - series[-1]) * multiplier + series[-1])
    return series


def atr_wilder(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Wilder-geglättete Average True Range."""
    if len(highs) < period + 1 or len(highs) != len(lows) or len(highs) != len(closes):
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    avg = sum(trs[:period]) / period
    for tr in trs[period:]:
        avg = (avg * (period - 1) + tr) / period
    return avg


class SurferBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 3600,
        trend_lookback_hours: int = 4,
        min_trend_pct: float = 0.0,
        breakout_lookback_hours: int = 20,
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        atr_period: int = 14,
        atr_stop_multiplier: float = 2.0,
        volume_multiplier: float = 1.2,
        max_risk_eur: float = 0.50,
        max_position_eur: float = 25.0,
        trailing_stop_pct: float = 3.0,
        max_hold_sec: int = 7 * 24 * 3600,
        loss_streak_limit: int = 3,
        loss_pause_sec: int = 24 * 3600,
        account_loss_limit_pct: float = 10.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.trend_lookback_hours = int(trend_lookback_hours)
        self.min_trend_pct = float(min_trend_pct)
        self.breakout_lookback_hours = int(breakout_lookback_hours)
        self.ema_fast_period = int(ema_fast_period)
        self.ema_slow_period = int(ema_slow_period)
        self.atr_period = int(atr_period)
        self.atr_stop_multiplier = float(atr_stop_multiplier)
        self.volume_multiplier = float(volume_multiplier)
        self.max_risk_eur = float(max_risk_eur)
        self.max_position_eur = float(max_position_eur)
        self.trailing_stop_pct = float(trailing_stop_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.loss_streak_limit = int(loss_streak_limit)
        self.loss_pause_sec = int(loss_pause_sec)
        self.account_loss_limit_pct = float(account_loss_limit_pct)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        if not self.paper_mode:
            logger.warning("Surfer live mode is intentionally not implemented")
            raise NotImplementedError("SurferBot is paper-only")

        self.pair = PAIR
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "surfer_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.consecutive_losses = 0
        self.loss_pause_until = 0.0
        self.last_entry_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
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
                self.capital_remaining = max(0.0, min(float(raw.get("capital_remaining", self.initial_capital_eur)), self.initial_capital_eur))
                self.portfolio = raw.get("portfolio") or {}
                self.consecutive_losses = int(raw.get("consecutive_losses", 0))
                self.loss_pause_until = float(raw.get("loss_pause_until", 0.0))
                self.last_entry_scan = float(raw.get("last_entry_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                logger.info("♻️ Surfer state geladen: cash=%.2f€, open=%d", self.capital_remaining, len(self.portfolio))
                return
            except Exception as e:
                logger.warning("Surfer state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.trade_count = 0
        self.consecutive_losses = 0
        self.loss_pause_until = 0.0
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
        last_resolved_at = None
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
                    "stop_price": price * (1 - 2 * 0.02),  # grobe Näherung bis zum ersten manage_positions()-Lauf
                    "trade_id": int(row["id"]),
                }
            else:
                pnl = float(row["real_pnl"] or 0.0)
                realized += pnl
                last_resolved_at = float(row["resolved_at"])
                if pnl < 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0
        if self.consecutive_losses >= self.loss_streak_limit and last_resolved_at is not None:
            self.loss_pause_until = last_resolved_at + self.loss_pause_sec
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)
        logger.info("🧱 Surfer rebuild: cash=%.2f€, open=%d, trades=%d", self.capital_remaining, len(self.portfolio), self.trade_count)

    @staticmethod
    def _ticker_snapshot(pair: str, ticker: dict) -> dict | None:
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        if not data:
            return None
        try:
            last = float(data["c"][0])
            volume_eur = float(data["v"][1]) * float(data["p"][1])
            if last <= 0:
                return None
            bid, ask = extract_quote(data, last)
            return {"pair": pair, "last_price": last, "bid": bid, "ask": ask, "volume_eur": volume_eur}
        except Exception:
            return None

    async def _ema_exit_signal(self) -> bool:
        rows = await fetch_ohlc(self.pair, OHLC_INTERVAL_MIN)
        if len(rows) < self.ema_slow_period:
            return False
        closes = [r[4] for r in rows]
        ema_fast = ema_series(closes, self.ema_fast_period)
        ema_slow = ema_series(closes, self.ema_slow_period)
        if not ema_fast or not ema_slow:
            return False
        return ema_fast[-1] < ema_slow[-1]

    async def manage_positions(self) -> list[dict]:
        pos = self.portfolio.get(self.pair)
        if not pos:
            return []
        now = time.time()
        ticker = await fetch_ticker_data([self.pair])
        snap = self._ticker_snapshot(self.pair, ticker)
        if not snap:
            logger.info("⏭️ SURF %s: kein Ticker – Position unverändert", self.pair)
            return []
        last = float(snap["last_price"])
        entry = float(pos.get("entry_price") or 0.0)
        peak = max(float(pos.get("peak_price") or entry), last)
        pos["peak_price"] = peak
        atr_stop_price = float(pos.get("stop_price") or 0.0)
        trailing_stop_price = peak * (1 - self.trailing_stop_pct / 100)
        # ATR-Stop schützt initial, Trailing-Stop sichert Gewinne – der jeweils
        # höhere (engere) Preis gewinnt, analog zur Floor/Trailing-Logik von
        # "Der Onchain" (memecoin_strategy.py).
        effective_stop = max(atr_stop_price, trailing_stop_price)
        age = now - float(pos.get("entry_ts") or now)
        reason = None
        if last <= effective_stop:
            reason = "trailing_stop" if trailing_stop_price >= atr_stop_price else "atr_stop"
        elif await self._ema_exit_signal():
            reason = "ema_exit"
        elif age >= self.max_hold_sec:
            reason = "time_exit"
        if not reason:
            return []
        trade_id = int(pos.get("trade_id") or 0)
        shares = float(pos.get("shares") or 0.0)
        # Trigger oben entscheidet auf Last, verkauft wird zum Bid.
        exit_price = float(snap.get("bid") or last)
        entry_cost = shares * entry
        current_value = shares * exit_price
        fee = config.CRYPTO_TAKER_FEE_RATE
        real_pnl = current_value - entry_cost - entry_cost * fee - current_value * fee
        await resolve_trade(trade_id, exit_price, round(real_pnl, 6))
        self.capital_remaining += entry_cost + real_pnl
        self.portfolio.pop(self.pair, None)
        if real_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.loss_streak_limit:
                self.loss_pause_until = now + self.loss_pause_sec
                logger.info("🛑 SURF: %d Verluste in Folge – Verlustpause bis %s", self.consecutive_losses, time.ctime(self.loss_pause_until))
        else:
            self.consecutive_losses = 0
        logger.info("✅ SURF Exit %s: %s @ %.6f€ (Last %.6f€) | PnL %+0.4f€", self.pair, reason, exit_price, last, real_pnl)
        self._save_state()
        return [{"pair": self.pair, "reason": reason, "pnl": real_pnl}]

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_entry_scan < self.interval_sec:
            return []
        self.last_entry_scan = now
        if self.pair in self.portfolio:
            logger.info("⏭️ SURF: bereits offen")
            self._save_state()
            return []
        if self.loss_pause_until > now:
            logger.info("⏭️ SURF: Verlustpause aktiv bis %s", time.ctime(self.loss_pause_until))
            self._save_state()
            return []
        equity_snap = await self.equity()
        loss_floor = self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100)
        if equity_snap["equity_eur"] <= loss_floor:
            logger.info("⏭️ SURF: Kontoverlust-Limit erreicht (%.2f€ <= %.2f€) – keine neuen Einstiege", equity_snap["equity_eur"], loss_floor)
            self._save_state()
            return []

        ticker = await fetch_ticker_data([self.pair])
        snap = self._ticker_snapshot(self.pair, ticker)
        if not snap:
            logger.info("⏭️ SURF %s: kein Ticker", self.pair)
            self._save_state()
            return []

        ch_trend = await rolling_change_pct(self.pair, lookback_bars=self.trend_lookback_hours, interval_min=OHLC_INTERVAL_MIN)
        if ch_trend is None or ch_trend <= self.min_trend_pct:
            logger.info("⏭️ SURF %s: kein bestätigter %dh-Aufwärtstrend (%s)", self.pair, self.trend_lookback_hours, ch_trend)
            self._save_state()
            return []

        rows = await fetch_ohlc(self.pair, OHLC_INTERVAL_MIN)
        min_bars = max(self.ema_slow_period, self.breakout_lookback_hours + 1, self.atr_period + 1)
        if len(rows) < min_bars:
            logger.info("⏭️ SURF %s: zu wenig OHLC-Daten (%d < %d)", self.pair, len(rows), min_bars)
            self._save_state()
            return []
        closes = [r[4] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        volumes = [r[6] for r in rows]

        ema_fast = ema_series(closes, self.ema_fast_period)
        ema_slow = ema_series(closes, self.ema_slow_period)
        if not ema_fast or not ema_slow or ema_fast[-1] <= ema_slow[-1]:
            logger.info("⏭️ SURF %s: EMA%d nicht über EMA%d", self.pair, self.ema_fast_period, self.ema_slow_period)
            self._save_state()
            return []

        breakout_window = highs[-(self.breakout_lookback_hours + 1):-1]
        breakout_level = max(breakout_window) if breakout_window else float("inf")
        last = float(snap["last_price"])
        if last <= breakout_level:
            logger.info("⏭️ SURF %s: kein %dh-Ausbruch (last %.6f <= %.6f)", self.pair, self.breakout_lookback_hours, last, breakout_level)
            self._save_state()
            return []

        volume_window = volumes[-(self.breakout_lookback_hours + 1):-1]
        avg_volume = sum(volume_window) / len(volume_window) if volume_window else 0.0
        if avg_volume <= 0 or volumes[-1] < avg_volume * self.volume_multiplier:
            logger.info("⏭️ SURF %s: Volumen %.4f nicht über %.2fx Durchschnitt %.4f", self.pair, volumes[-1], self.volume_multiplier, avg_volume)
            self._save_state()
            return []

        atr = atr_wilder(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            logger.info("⏭️ SURF %s: ATR nicht berechenbar", self.pair)
            self._save_state()
            return []

        # Einstiegsfilter oben entscheiden auf Last, gekauft wird zum Ask.
        price = float(snap.get("ask") or last)
        stop_price = price - atr * self.atr_stop_multiplier
        stop_distance = price - stop_price
        if stop_distance <= 0:
            logger.info("⏭️ SURF %s: ungültige Stop-Distanz", self.pair)
            self._save_state()
            return []

        risk_budget = min(self.max_risk_eur, self.capital_remaining)
        # Selbst zur minimal sinnvollen Positionsgröße darf das Risiko das
        # Budget nicht überschreiten – sonst ist der ATR-Abstand zu groß für
        # eine regelkonforme Position.
        if (MIN_POSITION_EUR / price) * stop_distance > risk_budget:
            logger.info("⏭️ SURF %s: ATR-Stop-Distanz zu groß für Risikobudget %.2f€", self.pair, risk_budget)
            self._save_state()
            return []

        qty = risk_budget / stop_distance
        position_value = min(qty * price, self.max_position_eur, self.capital_remaining)
        if position_value < MIN_POSITION_EUR:
            logger.info("⏭️ SURF: Cash %.2f€ reicht nicht", self.capital_remaining)
            self._save_state()
            return []
        qty = position_value / price

        trade_id = await log_paper_trade(f"{PREFIX}{self.pair}", "buy", qty, price, ch_trend / 100, "paper")
        self.capital_remaining -= position_value
        self.portfolio[self.pair] = {
            "shares": qty,
            "cost_basis": position_value,
            "entry_price": price,
            "entry_ts": now,
            "peak_price": last,
            "stop_price": stop_price,
            "trade_id": trade_id,
        }
        self.trade_count += 1
        logger.info(
            "📝 SURF Entry %s: %.2f€ @ %.6f€ (Last %.6f€) | %dh %+0.2f%% | ATR-Stop %.6f€",
            self.pair, position_value, price, last, self.trend_lookback_hours, ch_trend, stop_price,
        )
        self._save_state()
        return [{"pair": self.pair, "amount": position_value, "price": price, "stop_price": stop_price}]

    async def equity(self) -> dict:
        pos = self.portfolio.get(self.pair)
        unrealized = 0.0
        mtm = 0.0
        if pos:
            ticker = await fetch_ticker_data([self.pair])
            snap = self._ticker_snapshot(self.pair, ticker)
            if snap:
                fee = config.CRYPTO_TAKER_FEE_RATE
                # Mark-to-Market simuliert den Verkauf, also zum Bid bewerten.
                current_value = float(pos["shares"]) * float(snap.get("bid") or snap["last_price"])
                sell_fee = current_value * fee
                entry_cost = float(pos["cost_basis"])
                mtm += current_value - sell_fee
                unrealized += current_value - entry_cost - entry_cost * fee - sell_fee
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": self.capital_remaining + mtm,
            "cash_eur": self.capital_remaining,
            "open_positions": len(self.portfolio),
            "unrealized_pnl_eur": unrealized,
            "realized_pnl_eur": realized,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        e = await self.equity()
        await log_equity_snapshot(BOT_KEY, **e)
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 Surfer-Bot gestartet [PAPER] | Budget %.2f€ | Paar %s", self.initial_capital_eur, self.pair)
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception as e:
                logger.exception("⚠️ Surfer-Loop-Fehler (%s) – weiter in 60s", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(60)
