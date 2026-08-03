"""Baseline-Backtests fuer HODL, MeanRev, DCA und Candlestick.

Der Harness faehrt die echten Produktklassen gegen lokale Bitvavo-Kerzen. Nur
Uhr, Marktfeeds, Paper-DB und (beim Jupiter-basierten Candlestick-Bot) Quotes
werden ersetzt. Produktivcode und Live-/Paper-Parameter bleiben unveraendert.

Kein Lookahead: Strategien sehen ausschliesslich bereits geschlossene Kerzen.
HODL, MeanRev und DCA werden stuendlich aufgerufen; Candlestick alle 15 Minuten.
Der Candlestick-Lauf ist ein expliziter Proxy: SOLEUR wird als SOLUSDC bei
EURUSD=1 verwendet, Quotes enthalten Bitvavo-Spread plus 50 bp Slippage je
Seite. Er testet die Signalmechanik, nicht historische Jupiter-Liquiditaet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sqlite3
import tempfile
import time as real_time
from datetime import datetime, timezone
from pathlib import Path

from backtest.backtest_multibot import MarketSim, PairSeries, build_timeline, load_series, print_report, summarize
from backtest.backtest_surfer import Clock, DEFAULT_SPREAD_PCT, load_candles, parse_date
from backtest.open_mark import mark_open_trades
from polybot import candlestick_strategy, config, dca_strategy, hodl_strategy, meanrev_strategy
from polybot import paper_db as paper_db_module
from polybot import surfer_strategy

HOUR = 3600
DAY = 86400
DEFAULT_PAIRS = ["BTCEUR", "ETHEUR", "SOLEUR", "ADAEUR", "XRPEUR"]


class RemainingMarketSim(MarketSim):
    """Feed-Ergaenzungen fuer Tages- und 15m-Kerzen."""

    async def fetch_ohlc(self, pair: str, interval_min: int = 60) -> list[tuple]:
        pair = "BTCEUR" if pair == "XBTEUR" else "SOLEUR" if pair == "SOLUSDC" else pair
        series = self.series.get(pair)
        if not series:
            return []
        if interval_min == 60:
            idx = series.closed_idx(self.clock.now)
            return series.hourly[max(0, idx - 720):idx]
        if interval_min == 15:
            rows = sorted(series.steps_by_ts.values(), key=lambda row: row[0])
            closed = [row for row in rows if row[0] + 900 <= self.clock.now]
            return closed[-720:]
        if interval_min == 1440:
            idx = series.closed_idx(self.clock.now)
            grouped: dict[str, list[tuple]] = {}
            for row in series.hourly[:idx]:
                key = datetime.fromtimestamp(row[0], timezone.utc).date().isoformat()
                grouped.setdefault(key, []).append(row)
            today = datetime.fromtimestamp(self.clock.now, timezone.utc).date().isoformat()
            out = []
            for key, rows in grouped.items():
                if key >= today or len(rows) < 23:
                    continue
                out.append((rows[0][0], rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows),
                            rows[-1][4], rows[-1][5], sum(r[6] for r in rows)))
            return out[-400:]
        return []

    async def fetch_ticker_data(self, pairs: list[str]) -> dict:
        translated = ["BTCEUR" if p == "XBTEUR" else "SOLEUR" if p == "SOLUSDC" else p for p in pairs]
        raw = await super().fetch_ticker_data(translated)
        out = dict(raw)
        if "BTCEUR" in raw:
            out["XBTEUR"] = raw["BTCEUR"]
        if "SOLEUR" in raw:
            out["SOLUSDC"] = raw["SOLEUR"]
        return out

    async def rolling_change_pct(self, pair: str, lookback_bars: int = 24, interval_min: int = 60, ttl_sec: int = 900):
        pair = "BTCEUR" if pair == "XBTEUR" else pair
        return await super().rolling_change_pct(pair, lookback_bars, interval_min, ttl_sec)

    async def rolling_24h_change_pct(self, pair: str, ttl_sec: int = 900):
        return await self.rolling_change_pct(pair, 24, 60, ttl_sec)


def sim_datetime(clock: Clock):
    class SimDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock.now, tz or timezone.utc)
    return SimDateTime


async def no_sleep(_seconds):
    return None


def ledger_trades(db_path: Path, prefix: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\' "
            "AND resolved_at IS NOT NULL ORDER BY id", (paper_db_module.prefix_like_pattern(prefix),)
        ).fetchall()
    trades = []
    for row in rows:
        market = str(row["market_question"])
        pair = market.removeprefix(prefix).split("_")[0]
        entry_ts, exit_ts = float(row["timestamp"]), float(row["resolved_at"])
        trades.append({"pair": pair, "entry_ts": entry_ts, "exit_ts": exit_ts,
                       "entry_price": float(row["price"]), "exit_price": float(row["exit_price"]),
                       "cost": float(row["size"]) * float(row["price"]), "pnl": float(row["real_pnl"]),
                       "reason": "ledger_exit", "hold_h": (exit_ts - entry_ts) / HOUR})
    return trades


def setup(args, warmup_days: int = 400):
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(real_time.time())
    series = load_series(DEFAULT_PAIRS, start_ts - warmup_days * DAY, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    if not timeline:
        raise SystemExit("Keine 15m-Kerzen im angeforderten Zeitraum")
    clock = Clock(float(timeline[0]))
    sim = RemainingMarketSim(series, clock, args.spread_pct)
    return start_ts, end_ts, series, timeline, clock, sim


async def drive(bot, sim, clock, timeline, *, scan_step=HOUR, dca=False):
    curve = []
    for open_ts in timeline:
        if int(open_ts) % scan_step:
            continue
        # ``open_ts`` ist der Beginn einer 15m-Kerze; der sichtbare Preis
        # endet daher auch bei stuendlicher Aufruffrequenz 15 Minuten spaeter.
        clock.now = float(open_ts) + 900
        sim.on_step(open_ts)
        if dca:
            await bot.resolve_due_trades()
            if clock.now - bot.last_rescan >= bot.rescan_interval:
                await bot.rescan_top_coins()
            if clock.now - bot.last_buy >= bot.interval_sec:
                await bot.execute_dca_round()
        else:
            await bot.manage_positions()
            await bot.scan_entries()
        if int(open_ts) % (24 * HOUR) == 0:
            curve.append((await bot.equity())["equity_eur"])
    return curve


def finish(name, args, pairs, params, bot, trades, curve, final, timeline):
    span = (timeline[-1] - timeline[0]) / DAY
    return summarize(name, args, pairs, params, trades, curve, final, bot.portfolio, span)


async def run_hodl(args):
    _, _, _, timeline, clock, sim = setup(args)
    work = Path(tempfile.mkdtemp(prefix="hodl_bt_"))
    orig = (paper_db_module.DB_PATH, paper_db_module.time, hodl_strategy.dt.datetime, hodl_strategy.time,
            hodl_strategy.fetch_ticker_data, hodl_strategy.fetch_ohlc, surfer_strategy.time)
    try:
        paper_db_module.DB_PATH = str(work / "paper.db"); paper_db_module.time = clock; await paper_db_module.init_db()
        hodl_strategy.dt.datetime = sim_datetime(clock); hodl_strategy.time = clock; surfer_strategy.time = clock
        hodl_strategy.fetch_ticker_data = sim.fetch_ticker_data; hodl_strategy.fetch_ohlc = sim.fetch_ohlc
        params = dict(initial_capital_eur=args.budget, cash_reserve_eur=100, max_weekly_eur=100,
                      bear_rate_pct=35, overheat_momentum_pct=50, overheat_ema_pct=25, paper_mode=True)
        bot = hodl_strategy.HodlBot(**params); bot._save = lambda: None
        curve = await drive(bot, sim, clock, timeline)
        final = await bot.equity(); trades = mark_open_trades(ledger_trades(Path(paper_db_module.DB_PATH), "HODL_"), Path(paper_db_module.DB_PATH), "HODL_", sim, args.spread_pct, "hodl")
        params.update(taker_fee_rate=config.CRYPTO_TAKER_FEE_RATE, spread_pct=args.spread_pct)
        return finish("HODL (Der HODLer)", args, ["BTCEUR", "ETHEUR", "SOLEUR"], params, bot, trades, curve, final, timeline)
    finally:
        (paper_db_module.DB_PATH, paper_db_module.time, hodl_strategy.dt.datetime, hodl_strategy.time,
         hodl_strategy.fetch_ticker_data, hodl_strategy.fetch_ohlc, surfer_strategy.time) = orig
        shutil.rmtree(work, ignore_errors=True)


async def run_meanrev(args):
    _, _, _, timeline, clock, sim = setup(args, 35)
    work = Path(tempfile.mkdtemp(prefix="rev_bt_"))
    orig = (paper_db_module.DB_PATH, paper_db_module.time, meanrev_strategy.time, meanrev_strategy.fetch_ticker_data,
            meanrev_strategy.rolling_24h_change_pct, meanrev_strategy.fetch_ohlc,
            meanrev_strategy.CANDIDATE_PAIRS, meanrev_strategy.asyncio.sleep)
    try:
        paper_db_module.DB_PATH = str(work / "paper.db"); paper_db_module.time = clock; await paper_db_module.init_db()
        meanrev_strategy.time = clock; meanrev_strategy.fetch_ticker_data = sim.fetch_ticker_data
        meanrev_strategy.rolling_24h_change_pct = sim.rolling_24h_change_pct
        meanrev_strategy.fetch_ohlc = sim.fetch_ohlc; meanrev_strategy.CANDIDATE_PAIRS = DEFAULT_PAIRS
        meanrev_strategy.asyncio.sleep = no_sleep
        params = dict(initial_capital_eur=args.budget, interval_sec=HOUR, entry_drop_pct=8, rsi_period=14,
                      rsi_max=30, bollinger_enabled=True, bollinger_period=20, bollinger_stddev=2,
                      stochastic_enabled=True, stochastic_period=14, stochastic_max=20, confirm_pct=.5,
                      position_eur=75, max_open_positions=3, take_profit_pct=4, stop_loss_pct=5,
                      max_hold_sec=96*HOUR, cooldown_sec=12*HOUR, paper_mode=True)
        bot = meanrev_strategy.MeanRevBot(**params); bot._save_state = lambda: None
        curve = await drive(bot, sim, clock, timeline)
        final = await bot.equity(); trades = mark_open_trades(ledger_trades(Path(paper_db_module.DB_PATH), "REV_"), Path(paper_db_module.DB_PATH), "REV_", sim, args.spread_pct, "spot")
        params.update(taker_fee_rate=config.CRYPTO_TAKER_FEE_RATE, spread_pct=args.spread_pct)
        return finish("REV (Der Contrarian)", args, DEFAULT_PAIRS, params, bot, trades, curve, final, timeline)
    finally:
        (paper_db_module.DB_PATH, paper_db_module.time, meanrev_strategy.time, meanrev_strategy.fetch_ticker_data,
         meanrev_strategy.rolling_24h_change_pct, meanrev_strategy.fetch_ohlc,
         meanrev_strategy.CANDIDATE_PAIRS, meanrev_strategy.asyncio.sleep) = orig
        shutil.rmtree(work, ignore_errors=True)


async def run_dca(args):
    _, _, _, timeline, clock, sim = setup(args, 35)
    work = Path(tempfile.mkdtemp(prefix="dca_bt_"))
    orig = (paper_db_module.DB_PATH, paper_db_module.time, dca_strategy.time, dca_strategy.fetch_ticker_data,
            dca_strategy.rolling_24h_change_pct, dca_strategy.CANDIDATE_PAIRS)
    try:
        paper_db_module.DB_PATH = str(work / "paper.db"); paper_db_module.time = clock; await paper_db_module.init_db()
        dca_strategy.time = clock; dca_strategy.fetch_ticker_data = sim.fetch_ticker_data
        dca_strategy.rolling_24h_change_pct = sim.rolling_24h_change_pct
        dca_strategy.CANDIDATE_PAIRS = ["XBTEUR", "ETHEUR", "SOLEUR", "ADAEUR", "XRPEUR"]
        params = dict(initial_capital_eur=args.budget, interval_sec=4*HOUR, top_n=2, paper_mode=True,
                      rescan_interval=24*HOUR, rounds_target=5, min_edge_pct=1.2, negative_streak_limit=2,
                      coin_cooldown_sec=8*HOUR, rolling_window=6, rolling_loss_limit=-5, risk_off_sec=4*HOUR,
                      take_profit_pct=.03, stop_loss_pct=0, max_hold_sec=14*DAY, min_net_profit_eur=.75,
                      max_open_positions=2, max_pair_exposure_eur=100, min_cash_reserve_eur=50,
                      trend_filter_enabled=True, btc_risk_off_pct=-2, eth_risk_off_pct=-3,
                      recovery_trigger_pct=-5, recovery_reversal_pct=.8, recovery_ticket_eur=25,
                      recovery_max_exposure_factor=1.5)
        bot = dca_strategy.DCABot(**params); bot._save_state = lambda: None
        curve = await drive(bot, sim, clock, timeline, dca=True)
        final = await bot.equity(); trades = mark_open_trades(ledger_trades(Path(paper_db_module.DB_PATH), "DCA_"), Path(paper_db_module.DB_PATH), "DCA_", sim, args.spread_pct, "spot")
        params.update(taker_fee_rate=config.CRYPTO_TAKER_FEE_RATE, spread_pct=args.spread_pct)
        return finish("DCA (Der Stapler)", args, DEFAULT_PAIRS, params, bot, trades, curve, final, timeline)
    finally:
        (paper_db_module.DB_PATH, paper_db_module.time, dca_strategy.time, dca_strategy.fetch_ticker_data,
         dca_strategy.rolling_24h_change_pct, dca_strategy.CANDIDATE_PAIRS) = orig
        shutil.rmtree(work, ignore_errors=True)


async def run_candlestick(args):
    _, _, _, timeline, clock, sim = setup(args, 12)
    work = Path(tempfile.mkdtemp(prefix="cnd_bt_"))
    orig = (paper_db_module.DB_PATH, paper_db_module.time, candlestick_strategy.time, candlestick_strategy.fetch_ohlc, surfer_strategy.time)
    try:
        paper_db_module.DB_PATH = str(work / "paper.db"); paper_db_module.time = clock; await paper_db_module.init_db()
        candlestick_strategy.time = clock; surfer_strategy.time = clock; candlestick_strategy.fetch_ohlc = sim.fetch_ohlc
        params = dict(initial_capital_eur=args.budget, interval_sec=60, min_score=75, volume_multiplier=1.3,
                      atr_stop_multiplier=2, reward_risk_ratio=1.8, max_risk_eur=2.5,
                      max_position_eur=125, max_price_impact_pct=.5, slippage_bps=50,
                      max_hold_sec=48*HOUR, loss_streak_limit=3, loss_pause_sec=24*HOUR,
                      account_loss_limit_pct=10, paper_mode=True)
        bot = candlestick_strategy.CandlestickBot(state_path=work / "state.json", **params); bot._save_state = lambda: None
        async def eurusd(): return 1.0
        async def entry(amount, _rate):
            last = sim.price.get("SOLEUR"); ask = last * (1 + args.spread_pct/200) * 1.005
            return (amount / ask, {"priceImpactPct": "0"}) if last else None
        async def exit_quote(shares, _rate):
            last = sim.price.get("SOLEUR"); value = shares * last * (1 - args.spread_pct/200) * .995
            return (value, {"priceImpactPct": "0"}) if last else None
        bot._eurusd_rate = eurusd; bot._entry_quote = entry; bot._exit_quote = exit_quote
        curve = await drive(bot, sim, clock, timeline, scan_step=900)
        final = await bot.equity(); trades = mark_open_trades(ledger_trades(Path(paper_db_module.DB_PATH), "CND_"), Path(paper_db_module.DB_PATH), "CND_", sim, args.spread_pct, "candlestick")
        params.update(taker_fee_rate=.005, spread_pct=args.spread_pct, proxy="SOLEUR as SOLUSDC; EURUSD=1; 50bp slippage/side")
        return finish("CND (Der Kerzenreiter, Proxy)", args, ["SOLEUR"], params, bot, trades, curve, final, timeline)
    finally:
        paper_db_module.DB_PATH, paper_db_module.time, candlestick_strategy.time, candlestick_strategy.fetch_ohlc, surfer_strategy.time = orig
        shutil.rmtree(work, ignore_errors=True)


RUNNERS = {"hodl": run_hodl, "meanrev": run_meanrev, "dca": run_dca, "candlestick": run_candlestick}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True, choices=sorted(RUNNERS))
    parser.add_argument("--start", default="2024-01-01"); parser.add_argument("--end")
    parser.add_argument("--budget", type=float, default=500.0)
    parser.add_argument("--spread-pct", type=float, default=DEFAULT_SPREAD_PCT)
    parser.add_argument("--json-out"); parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    result = asyncio.run(RUNNERS[args.bot](args)); print_report(result)
    if args.json_out:
        out = Path(args.json_out); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False)); print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
