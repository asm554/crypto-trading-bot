"""Forschungsvarianten OHNE Änderungen am Produktivcode in polybot/.

Drei Bausteine, alle nur zur Backtest-Zeit aktiv:

1. ``SurferRegimeBot`` — Subklasse von ``SurferBot``, die vor dem normalen
   Einstiegs-Scan einen übergeordneten Regime-Filter prüft (Kurs über EMA200
   auf Stundenbasis, alternativ positive 30-Tage-Änderung). Wird per
   Monkeypatch von ``surfer_strategy.SurferBot`` in den unveränderten
   ``backtest_surfer.run_backtest``-Harness eingehängt.
2. ``--fee-rate`` — patcht ``config.CRYPTO_TAKER_FEE_RATE`` für die Dauer des
   Laufs (Maker-Was-wäre-wenn: 0.0016). Ob Limit-Orders live gefüllt würden,
   kann der Backtest nicht beweisen — reine Sensitivitätsrechnung.
3. ``mom``/``day``-Runner mit überschreibbarem ``max_hold_sec``/Trailing —
   repliziert die Parameterblöcke aus ``backtest_multibot`` und nutzt dessen
   MarketSim/drive_simple/summarize unverändert.

Beispiele:

    python -m backtest.research_variants surfer --trailing-stop-pct 1000 \
        --max-hold-h 720 --regime ema200 --json-out r.json
    python -m backtest.research_variants day --max-hold-h 72 \
        --trailing-stop-pct 10 --json-out r.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
import time as real_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polybot import config  # noqa: E402
from polybot import paper_db as paper_db_module  # noqa: E402
from polybot import daytrade_strategy, momentum_strategy, surfer_strategy  # noqa: E402
from polybot.surfer_strategy import SurferBot, closed_ohlc_rows, ema_series  # noqa: E402

from backtest import backtest_surfer  # noqa: E402
from backtest.backtest_multibot import (  # noqa: E402
    FULL_PAIRS,
    MarketSim,
    build_timeline,
    drive_simple,
    load_series,
    summarize as summarize_multibot,
)
from backtest.backtest_surfer import Clock, DEFAULT_SPREAD_PCT, parse_date  # noqa: E402

logger = logging.getLogger(__name__)

OHLC_INTERVAL_MIN = surfer_strategy.OHLC_INTERVAL_MIN


class SurferRegimeBot(SurferBot):
    """Surfer plus übergeordneter Regime-Filter vor dem Einstiegs-Scan.

    ``regime_mode``:
      - ``"ema200"``: letzte geschlossene Stundenkerze muss über der EMA200
        der Stunden-Closes liegen (auf einem 4x-Perioden-Fenster berechnet,
        damit die Rechnung auch bei langer Historie billig bleibt und live
        mit Krakens 720-Kerzen-Fenster machbar wäre).
      - ``"change30d"``: Close jetzt > Close vor 720 Stunden.
    Fällt der Filter durch, wird ``last_entry_scan`` gesetzt (gleiche
    Throttle-Semantik wie die übrigen Gates der Strategie) und kein Trade
    eröffnet. Management offener Positionen bleibt unberührt.
    """

    regime_mode: str = "ema200"
    regime_ema_period: int = 200
    regime_change_hours: int = 720

    async def scan_entries(self) -> list[dict]:
        now = surfer_strategy.time.time()
        if now - self.last_entry_scan < self.interval_sec:
            return []
        if self.pair in self.portfolio:
            return await super().scan_entries()

        rows = closed_ohlc_rows(
            await surfer_strategy.fetch_ohlc(self.pair, OHLC_INTERVAL_MIN),
            OHLC_INTERVAL_MIN,
            now,
        )
        if not self._regime_ok(rows):
            self.last_entry_scan = now
            return []
        return await super().scan_entries()

    def _regime_ok(self, rows: list[tuple]) -> bool:
        closes = [r[4] for r in rows]
        if self.regime_mode == "ema200":
            period = self.regime_ema_period
            if len(closes) < period:
                return False  # zu wenig Historie: konservativ blocken
            window = closes[-(period * 4):]
            ema = ema_series(window, period)
            return bool(ema) and closes[-1] > ema[-1]
        if self.regime_mode == "change30d":
            h = self.regime_change_hours
            if len(closes) < h + 1:
                return False
            return closes[-1] > closes[-1 - h]
        raise ValueError(f"Unbekannter regime_mode: {self.regime_mode}")


def surfer_args(a: argparse.Namespace) -> argparse.Namespace:
    """Namespace, wie ihn backtest_surfer.run_backtest erwartet."""
    return argparse.Namespace(
        pair=a.pair, start=a.start, end=a.end, budget=a.budget,
        trend_lookback_h=4, min_trend_pct=0.0, breakout_lookback_h=20,
        atr_stop_multiplier=2.0, volume_multiplier=1.2,
        max_risk_eur=a.max_risk_eur, max_position_eur=a.max_position_eur,
        trailing_stop_pct=a.trailing_stop_pct, max_hold_h=a.max_hold_h,
        loss_streak_limit=3, loss_pause_h=24.0,
        account_loss_limit_pct=a.account_loss_limit_pct,
        spread_pct=a.spread_pct,
    )


async def run_surfer(a: argparse.Namespace) -> dict:
    orig_bot = surfer_strategy.SurferBot
    try:
        if a.regime:
            cls = type("SurferRegimeBotRun", (SurferRegimeBot,), {"regime_mode": a.regime})
            surfer_strategy.SurferBot = cls
        result = await backtest_surfer.run_backtest(surfer_args(a))
    finally:
        surfer_strategy.SurferBot = orig_bot
    result["variant"] = {"regime": a.regime, "fee_rate": config.CRYPTO_TAKER_FEE_RATE}
    return result


async def run_mom_long(a: argparse.Namespace) -> dict:
    pairs = a.pairs.split(",") if a.pairs else list(FULL_PAIRS)
    start_ts, end_ts = parse_date(a.start), parse_date(a.end) if a.end else int(real_time.time())
    series = load_series(pairs, start_ts, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    span_days = (timeline[-1] - timeline[0]) / 86400
    clock = Clock(float(timeline[0]))
    sim = MarketSim(series, clock, a.spread_pct)

    workdir = Path(tempfile.mkdtemp(prefix="mom_rv_"))
    orig = (paper_db_module.DB_PATH, momentum_strategy.time, momentum_strategy.fetch_ticker_data,
            momentum_strategy.rolling_24h_change_pct, momentum_strategy.CANDIDATE_PAIRS)
    try:
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()
        momentum_strategy.time = clock
        momentum_strategy.fetch_ticker_data = sim.fetch_ticker_data
        momentum_strategy.rolling_24h_change_pct = sim.rolling_24h_change_pct
        momentum_strategy.CANDIDATE_PAIRS = list(pairs)

        params = {  # Basis = polybot/main_momentum.py, nur Horizont/Trailing variiert
            "initial_capital_eur": a.budget, "interval_sec": 3600,
            "entry_change_pct": 3.0, "entry_max_change_pct": 25.0,
            "min_volume_eur": 500_000.0, "position_eur": 60.0,
            "max_open_positions": 4,
            "trailing_stop_pct": a.trailing_stop_pct,
            "hard_stop_pct": a.hard_stop_pct,
            "max_hold_sec": int(a.max_hold_h * 3600),
            "cooldown_sec": 6 * 3600,
            "cooldown_after_loss_sec": 24 * 3600,
        }
        bot = momentum_strategy.MomentumBot(paper_mode=True, **params)
        bot.state_path = workdir / "momentum_state.json"
        bot.db_path = Path(paper_db_module.DB_PATH)

        trades, equity_curve, pending = await drive_simple(bot, sim, clock, timeline)
        final = await bot.equity()
        params["taker_fee_rate"] = config.CRYPTO_TAKER_FEE_RATE
        params["spread_pct"] = a.spread_pct
        return summarize_multibot("MOM long-horizon", a, pairs, params, trades, equity_curve, final, pending, span_days)
    finally:
        (paper_db_module.DB_PATH, momentum_strategy.time, momentum_strategy.fetch_ticker_data,
         momentum_strategy.rolling_24h_change_pct, momentum_strategy.CANDIDATE_PAIRS) = orig
        shutil.rmtree(workdir, ignore_errors=True)


async def run_day_long(a: argparse.Namespace) -> dict:
    pairs = a.pairs.split(",") if a.pairs else list(FULL_PAIRS)
    start_ts, end_ts = parse_date(a.start), parse_date(a.end) if a.end else int(real_time.time())
    series = load_series(pairs, start_ts, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    span_days = (timeline[-1] - timeline[0]) / 86400
    clock = Clock(float(timeline[0]))
    sim = MarketSim(series, clock, a.spread_pct)

    workdir = Path(tempfile.mkdtemp(prefix="day_rv_"))
    orig = (paper_db_module.DB_PATH, daytrade_strategy.time, daytrade_strategy.fetch_ticker_data,
            daytrade_strategy.rolling_change_pct, daytrade_strategy.fetch_ohlc, daytrade_strategy.CANDIDATE_PAIRS)
    try:
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()
        daytrade_strategy.time = clock
        daytrade_strategy.fetch_ticker_data = sim.fetch_ticker_data
        daytrade_strategy.rolling_change_pct = sim.rolling_change_pct
        daytrade_strategy.fetch_ohlc = sim.fetch_ohlc_day
        daytrade_strategy.CANDIDATE_PAIRS = list(pairs)

        params = {  # Basis = polybot/main_daytrade.py, nur Horizont/Trailing variiert
            "initial_capital_eur": a.budget, "interval_sec": 300,
            "lookback_hours": 4, "entry_change_pct": 3.0, "entry_max_change_pct": 25.0,
            "min_volume_eur": 500_000.0, "volume_spike_enabled": True,
            "volume_lookback_bars": 20, "volume_multiplier": 2.0,
            "position_eur": 50.0, "max_open_positions": 4,
            "trailing_stop_pct": a.trailing_stop_pct,
            "hard_stop_pct": a.hard_stop_pct,
            "max_hold_sec": int(a.max_hold_h * 3600),
            "cooldown_sec": 2 * 3600,
        }
        bot = daytrade_strategy.DaytradeBot(paper_mode=True, **params)
        bot.state_path = workdir / "daytrade_state.json"
        bot.db_path = Path(paper_db_module.DB_PATH)

        trades, equity_curve, pending = await drive_simple(bot, sim, clock, timeline)
        final = await bot.equity()
        params["taker_fee_rate"] = config.CRYPTO_TAKER_FEE_RATE
        params["spread_pct"] = a.spread_pct
        return summarize_multibot("DAY long-horizon", a, pairs, params, trades, equity_curve, final, pending, span_days)
    finally:
        (paper_db_module.DB_PATH, daytrade_strategy.time, daytrade_strategy.fetch_ticker_data,
         daytrade_strategy.rolling_change_pct, daytrade_strategy.fetch_ohlc, daytrade_strategy.CANDIDATE_PAIRS) = orig
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("variant", choices=["surfer", "mom", "day"])
    p.add_argument("--pair", default="SOLEUR", help="surfer: Handelspaar")
    p.add_argument("--pairs", default=None, help="mom/day: kommagetrennt")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--budget", type=float, default=500.0)
    p.add_argument("--spread-pct", type=float, default=DEFAULT_SPREAD_PCT)
    p.add_argument("--trailing-stop-pct", type=float, default=1000.0,
                   help="1000 = Trailing praktisch aus")
    p.add_argument("--hard-stop-pct", type=float, default=None,
                   help="mom/day; Default 4.0 (mom) bzw. 3.0 (day)")
    p.add_argument("--max-hold-h", type=float, default=720.0)
    p.add_argument("--max-risk-eur", type=float, default=2.5)
    p.add_argument("--max-position-eur", type=float, default=125.0)
    p.add_argument("--account-loss-limit-pct", type=float, default=95.0)
    p.add_argument("--regime", choices=["ema200", "change30d"], default=None,
                   help="surfer: übergeordneter Regime-Filter")
    p.add_argument("--fee-rate", type=float, default=None,
                   help="patcht config.CRYPTO_TAKER_FEE_RATE (Maker-Was-wäre-wenn: 0.0016)")
    p.add_argument("--json-out", default=None)
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    if a.hard_stop_pct is None:
        a.hard_stop_pct = 4.0 if a.variant == "mom" else 3.0

    orig_fee = config.CRYPTO_TAKER_FEE_RATE
    try:
        if a.fee_rate is not None:
            config.CRYPTO_TAKER_FEE_RATE = float(a.fee_rate)
        runner = {"surfer": run_surfer, "mom": run_mom_long, "day": run_day_long}[a.variant]
        result = asyncio.run(runner(a))
    finally:
        config.CRYPTO_TAKER_FEE_RATE = orig_fee

    if a.variant == "surfer":
        backtest_surfer.print_report(result)
    else:
        from backtest.backtest_multibot import print_report
        print_report(result)
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
