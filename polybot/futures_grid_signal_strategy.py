"""Signal-confirmed variant of the leveraged ETH/EUR paper grid.

The original ``FuturesGridBot`` remains the control group.  This variant starts
new cycles only in a confirmed directional uptrend after a pullback and
recovery, arms crossed grid levels before buying, and pauses for 30 days after
a loss.  It is intentionally paper only and never sends exchange orders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from polybot.futures_grid_strategy import FuturesGridBot
from polybot.paper_db import DB_PATH
from polybot.surfer_strategy import atr_wilder, closed_ohlc_rows, ema_series, fetch_ohlc

logger = logging.getLogger(__name__)

BOT_KEY = "futures_grid_signal"
PREFIX = "GRIDSIG_"
PAIR = "ETHEUR"
STATE_PATH = Path(DB_PATH).resolve().parent / "futures_grid_signal_state.json"


def clamp(low: float, high: float, value: float) -> float:
    return max(low, min(high, value))


def rsi_wilder_series(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return []
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value() -> float:
        if avg_loss <= 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    out = [value()]
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out.append(value())
    return out


def dmi_adx_wilder(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float, float, float] | None:
    if len(highs) < period * 2 + 1 or len(highs) != len(lows) or len(highs) != len(closes):
        return None
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for idx in range(1, len(highs)):
        up = highs[idx] - highs[idx - 1]
        down = lows[idx - 1] - lows[idx]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(
            max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
        )

    smooth_tr = sum(true_ranges[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_values: list[float] = []
    plus_di = minus_di = 0.0
    for idx in range(period - 1, len(true_ranges)):
        if idx >= period:
            smooth_tr = smooth_tr - smooth_tr / period + true_ranges[idx]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[idx]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[idx]
        if smooth_tr <= 0:
            plus_di = minus_di = 0.0
        else:
            plus_di = 100 * smooth_plus / smooth_tr
            minus_di = 100 * smooth_minus / smooth_tr
        denom = plus_di + minus_di
        dx_values.append(0.0 if denom <= 0 else 100 * abs(plus_di - minus_di) / denom)

    if len(dx_values) < period:
        return None
    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period
    return plus_di, minus_di, adx


@dataclass(frozen=True)
class MarketSnapshot:
    close_15m: float
    previous_high_15m: float
    rsi_15m: float
    previous_rsi_15m: float
    bar_ts_15m: float
    close_1h: float
    previous_high_1h: float
    rsi_1h: float
    previous_rsi_1h: float
    atr_1h: float
    ema20_1h: float
    upper_bb_1h: float
    r4: float
    r12: float
    r24: float
    r1h: float
    r15m: float
    volume_ratio: float
    pullback_8h: bool
    close_4h: float
    previous_close_4h: float
    ema20_4h: float
    ema50_4h: float
    ema200_4h: float
    ema50_slope_24h: float
    atr_4h: float
    adx_4h: float
    plus_di_4h: float
    minus_di_4h: float
    bar_ts_4h: float

    @property
    def crash(self) -> bool:
        return (
            self.r15m <= -0.02
            or self.r1h <= -0.03
            or self.r4 <= -0.05
            or (self.r1h <= -0.02 and self.volume_ratio >= 2.5)
        )

    @property
    def strong_bear(self) -> bool:
        return (
            self.ema20_4h < self.ema50_4h
            and self.ema50_slope_24h < 0
            and self.adx_4h >= 25
            and self.minus_di_4h > self.plus_di_4h
        )

    @property
    def downtrend(self) -> bool:
        return self.strong_bear or (
            self.ema20_4h < self.ema50_4h and self.ema50_slope_24h < 0
        )


def build_market_snapshot(
    rows_15m: list[tuple],
    rows_1h: list[tuple],
    rows_4h: list[tuple],
) -> MarketSnapshot | None:
    if len(rows_15m) < 20 or len(rows_1h) < 55 or len(rows_4h) < 250:
        return None

    closes_15m = [float(row[4]) for row in rows_15m]
    closes_1h = [float(row[4]) for row in rows_1h]
    highs_1h = [float(row[2]) for row in rows_1h]
    lows_1h = [float(row[3]) for row in rows_1h]
    volumes_1h = [float(row[6]) * float(row[4]) for row in rows_1h]
    closes_4h = [float(row[4]) for row in rows_4h]
    highs_4h = [float(row[2]) for row in rows_4h]
    lows_4h = [float(row[3]) for row in rows_4h]

    rsi_15m = rsi_wilder_series(closes_15m)
    rsi_1h = rsi_wilder_series(closes_1h)
    ema20_1h = ema_series(closes_1h, 20)
    ema20_4h = ema_series(closes_4h, 20)
    ema50_4h = ema_series(closes_4h, 50)
    ema200_4h = ema_series(closes_4h, 200)
    atr_1h = atr_wilder(highs_1h, lows_1h, closes_1h, 14)
    atr_4h = atr_wilder(highs_4h, lows_4h, closes_4h, 14)
    dmi = dmi_adx_wilder(highs_4h, lows_4h, closes_4h, 14)
    if (
        len(rsi_15m) < 2
        or len(rsi_1h) < 2
        or not ema20_1h
        or not ema20_4h
        or not ema50_4h
        or not ema200_4h
        or atr_1h is None
        or atr_4h is None
        or dmi is None
    ):
        return None

    bb_window = closes_1h[-20:]
    bb_mid = sum(bb_window) / len(bb_window)
    upper_bb = bb_mid + 2 * statistics.pstdev(bb_window)
    quote_volumes = volumes_1h[-49:-1]
    median_volume = statistics.median(quote_volumes)
    volume_ratio = volumes_1h[-1] / median_volume if median_volume > 0 else 0.0
    pullback_8h = any(
        low <= ema
        for low, ema in zip(lows_1h[-8:], ema20_1h[-8:])
    ) or min(rsi_1h[-8:]) <= 45
    plus_di, minus_di, adx = dmi

    return MarketSnapshot(
        close_15m=closes_15m[-1],
        previous_high_15m=float(rows_15m[-2][2]),
        rsi_15m=rsi_15m[-1],
        previous_rsi_15m=rsi_15m[-2],
        bar_ts_15m=float(rows_15m[-1][0]),
        close_1h=closes_1h[-1],
        previous_high_1h=highs_1h[-2],
        rsi_1h=rsi_1h[-1],
        previous_rsi_1h=rsi_1h[-2],
        atr_1h=atr_1h,
        ema20_1h=ema20_1h[-1],
        upper_bb_1h=upper_bb,
        r4=closes_1h[-1] / closes_1h[-5] - 1,
        r12=closes_1h[-1] / closes_1h[-13] - 1,
        r24=closes_1h[-1] / closes_1h[-25] - 1,
        r1h=closes_1h[-1] / closes_1h[-2] - 1,
        r15m=closes_15m[-1] / closes_15m[-2] - 1,
        volume_ratio=volume_ratio,
        pullback_8h=pullback_8h,
        close_4h=closes_4h[-1],
        previous_close_4h=closes_4h[-2],
        ema20_4h=ema20_4h[-1],
        ema50_4h=ema50_4h[-1],
        ema200_4h=ema200_4h[-1],
        ema50_slope_24h=ema50_4h[-1] / ema50_4h[-7] - 1,
        atr_4h=atr_4h,
        adx_4h=adx,
        plus_di_4h=plus_di,
        minus_di_4h=minus_di,
        bar_ts_4h=float(rows_4h[-1][0]),
    )


class SignalFuturesGridBot(FuturesGridBot):
    """Balanced signal-filtered grid used alongside the unchanged control bot."""

    def __init__(
        self,
        *,
        cooldown_win_sec: int = 12 * 3600,
        cooldown_loss_sec: int = 30 * 24 * 3600,
        crash_pause_sec: int = 24 * 3600,
        min_buy_gap_sec: int = 3600,
        hard_stop_pct: float = 7.5,
        cycle_loss_limit_pct: float = 3.0,
        min_trend_spread_atr: float = 0.75,
        trailing_activation_pct: float = 2.0,
        loss_trend_exit_sec: int = 14 * 24 * 3600,
        max_cycle_sec: int = 21 * 24 * 3600,
        signal_refresh_sec: int = 300,
        state_path: Path = STATE_PATH,
        **kwargs,
    ):
        kwargs.setdefault("initial_capital_eur", 500.0)
        kwargs.setdefault("leverage", 2.0)
        kwargs.setdefault("order_margin_eur", 12.5)
        kwargs.setdefault("grid_step_pct", 0.8)
        kwargs.setdefault("take_profit_pct", 1.2)
        kwargs.setdefault("max_safety_orders", 7)
        super().__init__(
            state_path=state_path,
            bot_key=BOT_KEY,
            prefix=PREFIX,
            pair=PAIR,
            **kwargs,
        )
        self.cooldown_win_sec = max(0, int(cooldown_win_sec))
        self.cooldown_loss_sec = max(0, int(cooldown_loss_sec))
        self.crash_pause_sec = max(0, int(crash_pause_sec))
        self.min_buy_gap_sec = max(0, int(min_buy_gap_sec))
        self.hard_stop_pct = float(hard_stop_pct)
        self.cycle_loss_limit_pct = float(cycle_loss_limit_pct)
        self.min_trend_spread_atr = max(0.0, float(min_trend_spread_atr))
        self.trailing_activation_pct = max(0.0, float(trailing_activation_pct))
        self.loss_trend_exit_sec = max(0, int(loss_trend_exit_sec))
        self.max_cycle_sec = max(0, int(max_cycle_sec))
        self.signal_refresh_sec = max(60, int(signal_refresh_sec))
        self._cached_snapshot: MarketSnapshot | None = None
        self._snapshot_checked_at = 0.0

        self.cooldown_until = 0.0
        self.crash_pause_until = 0.0
        self.crash_recovery_required = False
        self.armed_trigger: float | None = None
        self.armed_at_bar_ts = 0.0
        self.next_trigger_price: float | None = None
        self.last_buy_ts = 0.0
        self.cycle_started_at = 0.0
        self.cycle_start_equity = self.initial_capital_eur
        self.cycle_start_funding_eur = self.realized_funding_eur
        self.trailing_active = False
        self.trailing_stop: float | None = None
        self.peak_close = 0.0
        self.bear_signal_count = 0
        self.last_bear_bar_ts = 0.0
        self.last_status = "Wartet auf Marktdaten"
        self._load_signal_state()

    def _load_signal_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text())
        except Exception:
            return
        self.cooldown_until = float(raw.get("cooldown_until", 0.0))
        self.crash_pause_until = float(raw.get("crash_pause_until", 0.0))
        self.crash_recovery_required = bool(raw.get("crash_recovery_required", False))
        armed = raw.get("armed_trigger")
        self.armed_trigger = float(armed) if armed is not None else None
        self.armed_at_bar_ts = float(raw.get("armed_at_bar_ts", 0.0))
        trigger = raw.get("next_trigger_price")
        self.next_trigger_price = float(trigger) if trigger is not None else None
        self.last_buy_ts = float(raw.get("last_buy_ts", 0.0))
        self.cycle_started_at = float(raw.get("cycle_started_at", 0.0))
        self.cycle_start_equity = float(raw.get("cycle_start_equity", self.initial_capital_eur))
        self.cycle_start_funding_eur = float(
            raw.get("cycle_start_funding_eur", self.realized_funding_eur)
        )
        self.trailing_active = bool(raw.get("trailing_active", False))
        trail = raw.get("trailing_stop")
        self.trailing_stop = float(trail) if trail is not None else None
        self.peak_close = float(raw.get("peak_close", 0.0))
        self.bear_signal_count = int(raw.get("bear_signal_count", 0))
        self.last_bear_bar_ts = float(raw.get("last_bear_bar_ts", 0.0))
        self.last_status = str(raw.get("last_status", self.last_status))

    def _state_payload(self) -> dict:
        payload = super()._state_payload()
        payload.update(
            {
                "cooldown_until": self.cooldown_until,
                "crash_pause_until": self.crash_pause_until,
                "crash_recovery_required": self.crash_recovery_required,
                "armed_trigger": self.armed_trigger,
                "armed_at_bar_ts": self.armed_at_bar_ts,
                "next_trigger_price": self.next_trigger_price,
                "last_buy_ts": self.last_buy_ts,
                "cycle_started_at": self.cycle_started_at,
                "cycle_start_equity": self.cycle_start_equity,
                "cycle_start_funding_eur": self.cycle_start_funding_eur,
                "trailing_active": self.trailing_active,
                "trailing_stop": self.trailing_stop,
                "peak_close": self.peak_close,
                "bear_signal_count": self.bear_signal_count,
                "last_bear_bar_ts": self.last_bear_bar_ts,
                "last_status": self.last_status,
            }
        )
        return payload

    async def market_snapshot(self, now: float) -> MarketSnapshot | None:
        if self._cached_snapshot is not None and now - self._snapshot_checked_at < self.signal_refresh_sec:
            return self._cached_snapshot
        raw_15m, raw_1h, raw_4h = await asyncio.gather(
            fetch_ohlc(self.pair, 15),
            fetch_ohlc(self.pair, 60),
            fetch_ohlc(self.pair, 240),
        )
        rows_15m = closed_ohlc_rows(raw_15m, 15, now)
        rows_1h = closed_ohlc_rows(raw_1h, 60, now)
        rows_4h = closed_ohlc_rows(raw_4h, 240, now)
        self._snapshot_checked_at = now
        self._cached_snapshot = build_market_snapshot(rows_15m, rows_1h, rows_4h)
        return self._cached_snapshot

    def entry_signal(self, snapshot: MarketSnapshot) -> tuple[bool, str]:
        allowed_regime = (
            snapshot.close_4h > snapshot.ema200_4h
            and snapshot.ema20_4h >= snapshot.ema50_4h
            and snapshot.ema50_slope_24h >= 0
            and snapshot.plus_di_4h > snapshot.minus_di_4h
            and snapshot.close_1h >= snapshot.ema20_1h
        )
        hot_return = snapshot.r4 >= 0.02 or snapshot.r12 >= 0.035 or snapshot.r24 >= 0.04
        hot_rsi = snapshot.rsi_1h >= 70
        hot_extension = (
            snapshot.close_1h > snapshot.upper_bb_1h
            or snapshot.close_1h - snapshot.ema20_1h > 1.75 * snapshot.atr_1h
        )
        hot_count = sum((hot_return, hot_rsi, hot_extension))
        if snapshot.crash:
            return False, "Schneller Kurssturz – Einstieg gesperrt"
        if not allowed_regime:
            return False, "Aufwärtstrend ist noch nicht klar genug"
        if snapshot.r12 <= 0:
            return False, "12-Stunden-Trend ist noch nicht positiv"
        trend_spread = (
            (snapshot.ema20_4h - snapshot.ema50_4h) / snapshot.atr_4h
            if snapshot.atr_4h > 0
            else 0.0
        )
        if trend_spread < self.min_trend_spread_atr:
            return False, "Abstand der Trendlinien ist noch zu klein"
        if hot_count >= 2:
            return False, "Markt ist überhitzt – Rücksetzer abwarten"
        if not snapshot.pullback_8h:
            return False, "Noch kein frischer Rücksetzer"
        if snapshot.close_1h <= snapshot.previous_high_1h:
            return False, "Erholung nach Rücksetzer noch nicht bestätigt"
        if not 45 <= snapshot.rsi_1h <= 65 or snapshot.rsi_1h <= snapshot.previous_rsi_1h:
            return False, "Stärke-Signal passt noch nicht"
        if not 0.7 <= snapshot.volume_ratio <= 3.0:
            return False, "Volumen außerhalb des gesunden Bereichs"
        return True, "Einstieg bestätigt"

    @staticmethod
    def safety_confirmation(snapshot: MarketSnapshot) -> bool:
        return (
            not snapshot.crash
            and not snapshot.strong_bear
            and snapshot.close_15m > snapshot.previous_high_15m
            and snapshot.rsi_15m >= 32
            and snapshot.rsi_15m > snapshot.previous_rsi_15m
        )

    @staticmethod
    def grid_distance_pct(snapshot: MarketSnapshot) -> float:
        natr_pct = snapshot.atr_1h / snapshot.close_1h * 100
        return clamp(0.8, 1.6, 1.25 * natr_pct)

    def _current_equity(self, bid: float) -> float:
        unrealized = self.unrealized_pnl(bid) if self.orders else 0.0
        exit_fee = self.total_shares * bid * self.taker_fee_rate if self.orders else 0.0
        return self.capital_remaining + self.reserved_margin + unrealized - exit_fee

    async def _close_signal_cycle(self, bid: float, reason: str, now: float) -> dict:
        closed = await super()._close_cycle(bid, reason)
        won = self.capital_remaining >= self.cycle_start_equity
        self.cooldown_until = now + (self.cooldown_win_sec if won else self.cooldown_loss_sec)
        self.armed_trigger = None
        self.next_trigger_price = None
        self.cycle_started_at = 0.0
        self.trailing_active = False
        self.trailing_stop = None
        self.peak_close = 0.0
        self.bear_signal_count = 0
        self.last_status = (
            f"Gewinnrunde beendet – Pause bis {time.ctime(self.cooldown_until)}"
            if won
            else f"Verlustrunde beendet – längere Pause bis {time.ctime(self.cooldown_until)}"
        )
        return {"action": reason, "closed": closed}

    async def step(
        self,
        ticker: dict | None = None,
        now: float | None = None,
        snapshot: MarketSnapshot | None = None,
    ) -> dict:
        now = float(now or time.time())
        ticker = ticker if ticker is not None else await self._fetch_ticker()
        quote = self._quote(ticker)
        if quote is None:
            return {"action": "no_price"}
        last, bid, ask = quote
        snapshot = snapshot if snapshot is not None else await self.market_snapshot(now)
        if snapshot is None:
            self.last_status = "Zu wenig abgeschlossene Marktdaten"
            self._save_state()
            return {"action": "no_market_data"}

        self._accrue_funding(now)
        if snapshot.crash:
            self.crash_pause_until = max(self.crash_pause_until, now + self.crash_pause_sec)
            self.crash_recovery_required = True
        elif (
            self.crash_recovery_required
            and now >= self.crash_pause_until
            and snapshot.close_4h > snapshot.ema20_4h
            and snapshot.close_4h > snapshot.previous_close_4h
        ):
            self.crash_recovery_required = False

        if not self.orders:
            if now < self.cooldown_until:
                self.last_status = "Pause nach der letzten Runde"
                self._save_state()
                return {"action": "cooldown", "until": self.cooldown_until}
            if now < self.crash_pause_until or self.crash_recovery_required:
                self.last_status = "Crash-Pause – stabile Erholung abwarten"
                self._save_state()
                return {"action": "crash_pause", "until": self.crash_pause_until}
            allowed, reason = self.entry_signal(snapshot)
            if not allowed:
                self.last_status = reason
                self._save_state()
                return {"action": "entry_blocked", "reason": reason}
            self.cycle_start_equity = self.capital_remaining
            self.cycle_start_funding_eur = self.realized_funding_eur
            opened = await self._open_order(ask, ask)
            if not opened:
                return {"action": "no_cash"}
            self.cycle_started_at = now
            self.last_buy_ts = now
            distance = self.grid_distance_pct(snapshot)
            self.next_trigger_price = ask * (1 - distance / 100)
            self.last_status = f"Runde aktiv – nächste Stufe bei {self.next_trigger_price:.2f} €"
            self._save_state()
            return {"action": "open", "grid_distance_pct": distance}

        ratio = self.margin_ratio(bid)
        if ratio <= self.margin_guard_ratio:
            result = await self._close_signal_cycle(bid, "margin_guard", now)
            self._save_state()
            return result

        target = self.average_entry * (1 + self.take_profit_pct / 100)
        if bid >= target:
            result = await self._close_signal_cycle(bid, "take_profit", now)
            self._save_state()
            return result

        cycle_age = max(0.0, now - self.cycle_started_at)
        cycle_loss_floor = self.cycle_start_equity * (1 - self.cycle_loss_limit_pct / 100)
        if bid <= self.average_entry * (1 - self.hard_stop_pct / 100) or self._current_equity(bid) <= cycle_loss_floor:
            result = await self._close_signal_cycle(bid, "hard_stop", now)
            self._save_state()
            return result

        if snapshot.bar_ts_4h > self.last_bear_bar_ts:
            self.last_bear_bar_ts = snapshot.bar_ts_4h
            bearish_exit_bar = (
                snapshot.close_4h < snapshot.ema200_4h
                and (
                    snapshot.minus_di_4h > snapshot.plus_di_4h
                    or snapshot.ema50_slope_24h < 0
                )
            )
            self.bear_signal_count = self.bear_signal_count + 1 if bearish_exit_bar else 0
        if self.bear_signal_count >= 2:
            result = await self._close_signal_cycle(bid, "trend_exit", now)
            self._save_state()
            return result
        if cycle_age >= self.max_cycle_sec or (
            cycle_age >= self.loss_trend_exit_sec
            and self._current_equity(bid) < self.cycle_start_equity
            and snapshot.downtrend
        ):
            result = await self._close_signal_cycle(bid, "time_exit", now)
            self._save_state()
            return result

        cycle_funding = max(0.0, self.realized_funding_eur - self.cycle_start_funding_eur)
        entry_fees = sum(float(order["entry_fee_eur"]) for order in self.orders)
        break_even = (
            (self.total_notional + entry_fees + cycle_funding)
            / (self.total_shares * (1 - self.taker_fee_rate))
        )
        if snapshot.close_15m >= self.average_entry * (
            1 + self.trailing_activation_pct / 100
        ):
            self.trailing_active = True
        if self.trailing_active:
            self.peak_close = max(self.peak_close, snapshot.close_15m)
            candidate = max(break_even, self.peak_close - 0.8 * snapshot.atr_1h)
            self.trailing_stop = max(self.trailing_stop or break_even, candidate)
            if bid <= self.trailing_stop:
                result = await self._close_signal_cycle(bid, "profit_trailing", now)
                self._save_state()
                return result

        if len(self.orders) - 1 >= self.max_safety_orders:
            self.last_status = "Alle acht Stufen genutzt – wartet auf Ausstieg"
            self._save_state()
            return {"action": "hold", "reason": "max_levels"}

        if self.armed_trigger is None and self.next_trigger_price is not None and last <= self.next_trigger_price:
            self.armed_trigger = self.next_trigger_price
            self.armed_at_bar_ts = snapshot.bar_ts_15m
            self.last_status = "Nächste Stufe vorgemerkt – wartet auf Erholung"
            self._save_state()
            return {"action": "armed", "trigger_price": self.armed_trigger}

        if self.armed_trigger is not None:
            if now < self.crash_pause_until or self.crash_recovery_required or snapshot.strong_bear:
                self.last_status = "Vorgemerkte Stufe wegen Abwärtstrend pausiert"
            elif now - self.last_buy_ts < self.min_buy_gap_sec:
                self.last_status = "Vorgemerkte Stufe wartet auf Mindestabstand"
            elif snapshot.bar_ts_15m <= self.armed_at_bar_ts:
                self.last_status = "Vorgemerkte Stufe wartet auf neue 15-Minuten-Kerze"
            elif last > self.armed_trigger + 0.5 * snapshot.atr_1h:
                self.armed_trigger = None
                self.last_status = "Erholung schon zu weit gelaufen – kein verspäteter Kauf"
                self._save_state()
                return {"action": "armed_cancelled"}
            elif self.safety_confirmation(snapshot):
                opened = await self._open_order(ask, self.armed_trigger)
                if opened:
                    self.last_buy_ts = now
                    self.armed_trigger = None
                    distance = self.grid_distance_pct(snapshot)
                    self.next_trigger_price = ask * (1 - distance / 100)
                    self.last_status = f"Stufe bestätigt – nächste bei {self.next_trigger_price:.2f} €"
                    self._save_state()
                    return {"action": "safety_order", "opened": 1, "grid_distance_pct": distance}
            else:
                self.last_status = "Vorgemerkte Stufe wartet auf sichtbare Erholung"
            self._save_state()
            return {"action": "armed_wait"}

        self.last_status = f"Runde aktiv – nächste Stufe bei {self.next_trigger_price:.2f} €"
        self._save_state()
        return {
            "action": "hold",
            "margin_ratio": ratio,
            "liquidation_price": self.liquidation_price(),
            "next_trigger_price": self.next_trigger_price,
        }

    async def _fetch_ticker(self) -> dict:
        from polybot.dca_strategy import fetch_ticker_data

        return await fetch_ticker_data([self.pair])
