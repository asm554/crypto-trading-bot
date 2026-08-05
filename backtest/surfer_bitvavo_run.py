"""Ein einzelner Surfer-Backtest gegen die Bitvavo-Historie, mit Edge-Statistik.

Warum eine eigene Datei statt ``backtest_surfer.py`` direkt:

1. **Laufzeit.** ``backtest_surfer.fake_fetch_ohlc`` filtert bei jedem Schritt
   die komplette Stundenhistorie (``[r for r in hourly if r[0] <= now]``). Bei
   den alten Daten (ab 2024) waren das ~14k Zeilen, bei 7 Jahren Bitvavo-
   Historie ~65k — der Lauf wird quadratisch und praktisch unbenutzbar.
   Hier wird per ``bisect`` geschnitten **und** auf die letzten
   ``OHLC_WINDOW`` Kerzen begrenzt. Das ist zugleich *näher an live*: Krakens
   OHLC-Endpoint liefert ohnehin nur ~720 Kerzen zurück, der bisherige
   Backtest gab der Strategie also mehr Historie als sie real je sieht.
   EMA20/EMA50 sind nach 720 Kerzen vollständig einkonvergiert (Seed-Gewicht
   < 1e-11), das Signal ändert sich dadurch nicht.
2. **Gebührenniveau.** ``config.CRYPTO_TAKER_FEE_RATE`` (0,40 %/Seite, Kraken)
   muss für den Bitvavo-Vergleich (0,25 %/Seite) umschaltbar sein.
3. **Statistik.** Brutto-Edge, t-Wert und 95-%-Konfidenzintervall der
   Brutto-Rendite je Trade landen direkt im Ergebnis-JSON.

``polybot/`` wird nicht angefasst: die echte ``SurferBot``-Klasse läuft, nur
Uhr, Ticker und OHLC-Zugriff sind wie im Original-Runner ersetzt.

    python -m backtest.surfer_bitvavo_run --name ref_SOLEUR_kraken \
        --pair SOLEUR --start 2021-08-05 --fee 0.004
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import logging
import math
import shutil
import statistics
import sys
import tempfile
import time as real_time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.backtest_surfer import (  # noqa: E402
    STEP_SEC, Clock, build_ticker, load_candles, max_drawdown_pct, parse_date, summarize,
)
from polybot import config  # noqa: E402
from polybot import paper_db as paper_db_module  # noqa: E402
from polybot import surfer_strategy  # noqa: E402

HOUR_SEC = 3600
# Krakens OHLC-Endpoint gibt ~720 Kerzen zurück; mehr sieht der Bot live nie.
OHLC_WINDOW = 720
RESULTS_DIR = Path(__file__).resolve().parent / "results" / "bitvavo"

_CANDLE_CACHE: dict[tuple[str, int], list[tuple]] = {}


def cached_candles(pair: str, interval_min: int) -> list[tuple]:
    key = (pair, interval_min)
    if key not in _CANDLE_CACHE:
        _CANDLE_CACHE[key] = load_candles(pair, interval_min)
    return _CANDLE_CACHE[key]


# ---------------------------------------------------------------- Statistik

def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """Zweiseitiger p-Wert der Student-t-Verteilung (ohne scipy)."""
    if df <= 0:
        return float("nan")
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def t_quantile_975(df: int) -> float:
    """t-Quantil für ein zweiseitiges 95-%-Intervall, per Bisektion aus dem p-Wert."""
    if df <= 0:
        return float("nan")
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def edge_stats(trade_log: list[dict], fee: float) -> dict:
    """Brutto-Edge, Gebührenhürde und Signifikanz aus den Trade-Logs.

    Die Bots rechnen ``pnl = V - C - C*f - V*f``; daraus lässt sich der
    Exit-Wert ``V`` und damit die Brutto-Bewegung je Trade rekonstruieren.
    """
    trades = [t for t in trade_log if t.get("size_eur")]
    if not trades:
        return {"trades": 0}
    rt = 2 * fee  # Round-Trip-Gebühr
    notional = sum(float(t["size_eur"]) for t in trades)
    net = sum(float(t["pnl_eur"]) for t in trades)
    gross_eur = net + notional * rt
    edge_pct = gross_eur / notional * 100
    ratio = edge_pct / (rt * 100)

    per_trade, hurdles = [], []
    for t in trades:
        c, pnl = float(t["size_eur"]), float(t["pnl_eur"])
        v = (pnl + c * (1 + fee)) / (1 - fee)
        per_trade.append((v - c) / c * 100)
        hurdles.append(fee * (1 + v / c) * 100)

    n = len(per_trade)
    mean = statistics.mean(per_trade)
    sd = statistics.stdev(per_trade) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    tval = mean / se if se > 0 else float("nan")
    tq = t_quantile_975(n - 1) if n > 1 else float("nan")
    ci = (mean - tq * se, mean + tq * se) if se > 0 else (float("nan"), float("nan"))
    return {
        "trades": n,
        "fee_per_side_pct": round(fee * 100, 3),
        "round_trip_fee_pct": round(rt * 100, 3),
        "notional_eur": round(notional, 2),
        "net_pnl_eur": round(net, 3),
        "gross_pnl_eur": round(gross_eur, 3),
        "edge_pct": round(edge_pct, 4),
        "ratio": round(ratio, 3),
        "mean_gross_pct": round(mean, 4),
        "median_gross_pct": round(statistics.median(per_trade), 4),
        "sd_gross_pct": round(sd, 4),
        "mean_hurdle_pct": round(statistics.mean(hurdles), 4),
        "t_stat": round(tval, 3) if tval == tval else None,
        "p_value": round(t_two_sided_p(tval, n - 1), 4) if tval == tval else None,
        "ci95_low_pct": round(ci[0], 4) if ci[0] == ci[0] else None,
        "ci95_high_pct": round(ci[1], 4) if ci[1] == ci[1] else None,
        "ci95_excludes_zero": bool(ci[0] == ci[0] and ci[0] > 0),
    }


# ---------------------------------------------------------------- Backtest

async def run(args) -> dict:
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(real_time.time())
    hourly_all = cached_candles(args.pair, 60)
    steps_all = cached_candles(args.pair, 15)
    hourly = [r for r in hourly_all if start_ts <= r[0] < end_ts]
    steps = [r for r in steps_all if start_ts <= r[0] < end_ts]
    if not hourly or not steps:
        raise SystemExit(f"Keine Kerzen im Zeitraum {args.start}..{args.end or 'jetzt'} für {args.pair}")
    hourly_ts = [r[0] for r in hourly]
    span_days = (steps[-1][0] - steps[0][0]) / 86400

    workdir = Path(tempfile.mkdtemp(prefix="surfer_bv_"))
    orig = (paper_db_module.DB_PATH, surfer_strategy.time,
            surfer_strategy.fetch_ticker_data, surfer_strategy.fetch_ohlc,
            config.CRYPTO_TAKER_FEE_RATE)

    clock = Clock(float(steps[0][0]))
    state = SimpleNamespace(price=float(steps[0][4]), volume_eur=0.0)

    try:
        config.CRYPTO_TAKER_FEE_RATE = float(args.fee)
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()

        async def fake_fetch_ticker_data(_pairs):
            return {args.pair: build_ticker(state.price, state.volume_eur, args.spread_pct)}

        async def fake_fetch_ohlc(_pair, _interval_min=60):
            cut = bisect.bisect_right(hourly_ts, clock.now)
            return hourly[max(0, cut - OHLC_WINDOW):cut]

        surfer_strategy.time = clock
        surfer_strategy.fetch_ticker_data = fake_fetch_ticker_data
        surfer_strategy.fetch_ohlc = fake_fetch_ohlc

        bot = surfer_strategy.SurferBot(
            initial_capital_eur=args.budget,
            trend_lookback_hours=args.trend_lookback_h,
            min_trend_pct=args.min_trend_pct,
            breakout_lookback_hours=args.breakout_lookback_h,
            atr_stop_multiplier=args.atr_stop_multiplier,
            volume_multiplier=args.volume_multiplier,
            max_risk_eur=args.max_risk_eur,
            max_position_eur=args.max_position_eur,
            trailing_stop_pct=args.trailing_stop_pct,
            max_hold_sec=int(args.max_hold_h * 3600),
            loss_streak_limit=args.loss_streak_limit,
            loss_pause_sec=int(args.loss_pause_h * 3600),
            account_loss_limit_pct=args.account_loss_limit_pct,
            paper_mode=True,
        )
        bot.state_path = workdir / "surfer_state.json"
        bot.db_path = Path(paper_db_module.DB_PATH)
        bot.pair = args.pair

        trades: list[dict] = []
        equity_curve: list[tuple[float, float]] = []
        pending: dict | None = None

        for candle in steps:
            open_ts, _o, _h, _l, close, _vwap, volume = candle
            clock.now = float(open_ts) + STEP_SEC
            state.price = float(close)
            state.volume_eur = float(volume) * float(close)

            if pending is None and bot.portfolio.get(bot.pair):
                pos = bot.portfolio[bot.pair]
                pending = {
                    "entry_ts": float(pos["entry_ts"]), "entry_price": float(pos["entry_price"]),
                    "shares": float(pos["shares"]), "cost": float(pos["cost_basis"]),
                    "stop_price": float(pos.get("stop_price") or 0.0),
                }

            for resolved in await bot.manage_positions():
                if pending:
                    pending.update(
                        exit_ts=clock.now, exit_price=state.price,
                        reason=resolved["reason"], pnl=float(resolved["pnl"]),
                        hold_h=(clock.now - pending["entry_ts"]) / 3600,
                    )
                    trades.append(pending)
                    pending = None

            for opened in await bot.scan_entries():
                pos = bot.portfolio[bot.pair]
                pending = {
                    "entry_ts": clock.now, "entry_price": float(opened["price"]),
                    "shares": float(pos["shares"]), "cost": float(opened["amount"]),
                    "stop_price": float(opened["stop_price"]),
                }

            if int(clock.now) % HOUR_SEC == 0:
                snap = await bot.equity()
                equity_curve.append((clock.now, snap["equity_eur"]))

        final = await bot.equity()
        result = summarize(args, bot, trades, equity_curve, final, pending, span_days)
        result["name"] = args.name
        result["data_source"] = "bitvavo"
        result["ohlc_window"] = OHLC_WINDOW
        result["account_loss_limit_pct"] = args.account_loss_limit_pct
        result["edge"] = edge_stats(result["trade_log"], float(args.fee))
        result["buy_and_hold_pct"] = round((hourly[-1][4] / hourly[0][4] - 1) * 100, 2)
        result["first_candle"] = int(hourly[0][0])
        result["last_candle"] = int(hourly[-1][0])
        result["hourly_candles"] = len(hourly)
        result["step_candles"] = len(steps)
        return result
    finally:
        (paper_db_module.DB_PATH, surfer_strategy.time, surfer_strategy.fetch_ticker_data,
         surfer_strategy.fetch_ohlc, config.CRYPTO_TAKER_FEE_RATE) = orig
        shutil.rmtree(workdir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True, help="Dateiname (ohne .json) unter backtest/results/bitvavo/")
    p.add_argument("--pair", default="SOLEUR")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--fee", type=float, default=0.004, help="Taker-Gebühr je Seite (0.004 Kraken, 0.0025 Bitvavo)")
    p.add_argument("--budget", type=float, default=500.0)
    p.add_argument("--trend-lookback-h", type=int, default=4)
    p.add_argument("--min-trend-pct", type=float, default=0.0)
    p.add_argument("--breakout-lookback-h", type=int, default=20)
    p.add_argument("--atr-stop-multiplier", type=float, default=2.0)
    p.add_argument("--volume-multiplier", type=float, default=1.2)
    p.add_argument("--max-risk-eur", type=float, default=2.5)
    p.add_argument("--max-position-eur", type=float, default=125.0)
    p.add_argument("--trailing-stop-pct", type=float, default=3.0)
    p.add_argument("--max-hold-h", type=float, default=168.0)
    p.add_argument("--loss-streak-limit", type=int, default=3)
    p.add_argument("--loss-pause-h", type=float, default=24.0)
    p.add_argument("--account-loss-limit-pct", type=float, default=10.0)
    p.add_argument("--spread-pct", type=float, default=0.05)
    p.add_argument("--out-dir", default=str(RESULTS_DIR))
    return p


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    t0 = real_time.time()
    result = asyncio.run(run(args))
    out = Path(args.out_dir) / f"surfer_{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    e = result["edge"]
    print(f"{args.name:<34} {result['pair']:<7} {result['start']}..{result['end']:<10} "
          f"n={e.get('trades', 0):<4} ret={result['total_return_pct']:+7.2f}% "
          f"edge={e.get('edge_pct', float('nan')):+7.3f}% ratio={e.get('ratio', float('nan')):>6}x "
          f"t={e.get('t_stat')} [{real_time.time() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
