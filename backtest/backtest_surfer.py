"""Backtest für "Der Surfer" auf historischen Kraken-Kerzen.

Fährt die echte ``SurferBot``-Klasse gegen aufgezeichnete Kerzen, statt die
Strategie nachzubauen: Uhr, Ticker und OHLC-Abruf werden ersetzt, alles andere —
Einstiegsgates, ATR-Sizing, Trailing-/EMA-/Zeit-Exits, Verlustpause,
Kontoverlust-Sperre, Gebühren — läuft als produktiver Code. Ein Backtest, der
die Strategie nachimplementiert, testet sonst eine zweite Strategie.

Ablauf: Die Simulation schreitet in 15-Minuten-Schritten voran. Signale sehen
weiterhin nur abgeschlossene Stundenkerzen (``closed_ohlc_rows`` filtert die
laufende Kerze), Positionen werden aber viertelstündlich geprüft.

Bekannte Abweichung: live pollt der Bot alle 60 Sekunden, hier alle 15 Minuten.
Ein Stop, der innerhalb einer Viertelstunde gerissen und wieder aufgeholt wird,
bleibt im Backtest unentdeckt — die Ergebnisse sind an dieser Stelle also eher
zu gut. Bei ATR-weiten Stops (2x ATR14) fällt das wenig ins Gewicht, bei einer
Parametersuche mit engen Stops zunehmend mehr.

Beispiel:

    python -m backtest.backtest_surfer --start 2024-01-01 --end 2025-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import sys
import tempfile
import time as real_time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polybot import config  # noqa: E402
from polybot import paper_db as paper_db_module  # noqa: E402
from polybot import surfer_strategy  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
STEP_SEC = 15 * 60
HOUR_SEC = 3600
# Top-of-Book-Spread für SOL/EUR. Kraken liegt real bei ~0,02-0,05 %; der
# höhere Wert ist die konservative Wahl, weil zu enge Spreads die Fills
# schönrechnen.
DEFAULT_SPREAD_PCT = 0.05


def parse_date(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def load_candles(pair: str, interval_min: int) -> list[tuple]:
    """CSV -> Kraken-OHLC-Tupel (time, open, high, low, close, vwap, volume).

    ``fetch_ohlc`` liefert live 7-Tupel; die Strategie liest daraus high (2),
    low (3), close (4) und volume (6). Index 5 (vwap) nutzt sie nicht, dort
    steht hier der Close.
    """
    path = DATA_DIR / f"{pair}_{interval_min}m.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} fehlt — zuerst laden mit:\n"
            f"  python -m backtest.download_kraken_history --pairs {pair} --start 2024-01-01"
        )
    rows = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            close = float(row["close"])
            rows.append((
                float(row["timestamp"]), float(row["open"]), float(row["high"]),
                float(row["low"]), close, close, float(row["volume"]),
            ))
    rows.sort(key=lambda r: r[0])
    return rows


class Clock:
    """Simulierte Uhr, ersetzt ``time`` im Strategiemodul."""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now

    def ctime(self, secs: float | None = None) -> str:
        return real_time.ctime(self.now if secs is None else secs)


def build_ticker(price: float, volume_eur: float, spread_pct: float) -> dict:
    """Kraken-Ticker-Eintrag um einen Preis herum."""
    half = spread_pct / 200  # Spread hälftig um den Mid herum
    bid = price * (1 - half)
    ask = price * (1 + half)
    return {
        "a": [f"{ask:.10f}", "1", "1"],
        "b": [f"{bid:.10f}", "1", "1"],
        "c": [f"{price:.10f}", "1.0"],
        "h": [f"{price:.10f}", f"{price:.10f}"],
        "l": [f"{price:.10f}", f"{price:.10f}"],
        "o": f"{price:.10f}",
        "p": ["1", f"{price:.10f}"],
        "t": [10, 20],
        "v": ["1", f"{volume_eur / price if price > 0 else 0:.10f}"],
    }


def max_drawdown_pct(values: list[float]) -> float:
    peak = None
    worst = 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak and peak > 0:
            worst = min(worst, (v - peak) / peak * 100)
    return worst


async def run_backtest(args) -> dict:
    start_ts, end_ts = parse_date(args.start), parse_date(args.end) if args.end else int(real_time.time())
    hourly = [r for r in load_candles(args.pair, 60) if start_ts <= r[0] < end_ts]
    steps = [r for r in load_candles(args.pair, 15) if start_ts <= r[0] < end_ts]
    if not hourly or not steps:
        raise SystemExit(f"Keine Kerzen im Zeitraum {args.start}..{args.end or 'jetzt'}")

    span_days = (steps[-1][0] - steps[0][0]) / 86400
    print(f"📊 Backtest {args.pair} | {args.start} bis {args.end or 'jetzt'} "
          f"({span_days:.0f} Tage, {len(hourly):,} 1h-/{len(steps):,} 15m-Kerzen)")

    workdir = Path(tempfile.mkdtemp(prefix="surfer_bt_"))
    orig_db_path = paper_db_module.DB_PATH
    orig_time = surfer_strategy.time
    orig_fetch_ticker = surfer_strategy.fetch_ticker_data
    orig_fetch_ohlc = surfer_strategy.fetch_ohlc

    clock = Clock(float(steps[0][0]))
    state = SimpleNamespace(price=float(steps[0][4]), volume_eur=0.0)

    try:
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()

        async def fake_fetch_ticker_data(_pairs):
            return {args.pair: build_ticker(state.price, state.volume_eur, args.spread_pct)}

        async def fake_fetch_ohlc(_pair, _interval_min=60):
            # Nur was zur simulierten Zeit existierte — kein Blick nach vorn.
            # ``closed_ohlc_rows`` in der Strategie wirft die laufende Kerze weg.
            return [r for r in hourly if r[0] <= clock.now]

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
        # Live handelt der Surfer fest SOLEUR. Fürs Backtesting ist das Paar
        # überschreibbar, damit sich die Parameter auch gegen andere Märkte
        # prüfen lassen — die Strategie selbst bleibt unverändert.
        bot.pair = args.pair

        trades: list[dict] = []
        equity_curve: list[tuple[float, float]] = []
        pending: dict | None = None

        for candle in steps:
            open_ts, _o, _h, _l, close, _vwap, volume = candle
            # Wachzeitpunkt: der Moment, in dem diese Kerze schließt.
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
        return summarize(args, bot, trades, equity_curve, final, pending, span_days)
    finally:
        surfer_strategy.time = orig_time
        surfer_strategy.fetch_ticker_data = orig_fetch_ticker
        surfer_strategy.fetch_ohlc = orig_fetch_ohlc
        paper_db_module.DB_PATH = orig_db_path
        shutil.rmtree(workdir, ignore_errors=True)


def summarize(args, bot, trades, equity_curve, final, pending, span_days) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    equity_values = [v for _ts, v in equity_curve]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    result = {
        "pair": args.pair,
        "start": args.start,
        "end": args.end or "jetzt",
        "days": round(span_days, 1),
        "budget_eur": args.budget,
        "final_equity_eur": round(final["equity_eur"], 2),
        "total_return_pct": round((final["equity_eur"] / args.budget - 1) * 100, 2),
        "realized_pnl_eur": round(final["realized_pnl_eur"], 2),
        "trades": len(trades),
        "open_at_end": 1 if pending else 0,
        "trades_per_month": round(len(trades) / max(span_days / 30.4, 0.01), 2),
        "winrate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_win_eur": round(gross_win / len(wins), 3) if wins else None,
        "avg_loss_eur": round(-gross_loss / len(losses), 3) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_drawdown_pct(equity_values), 2),
        "avg_hold_h": round(sum(t["hold_h"] for t in trades) / len(trades), 1) if trades else None,
        "max_risk_per_trade_eur": round(max((t["cost"] * (1 - t["stop_price"] / t["entry_price"]) for t in trades), default=0.0), 3),
        "exit_reasons": reasons,
        "params": {
            "trend_lookback_h": args.trend_lookback_h, "min_trend_pct": args.min_trend_pct,
            "breakout_lookback_h": args.breakout_lookback_h, "atr_stop_multiplier": args.atr_stop_multiplier,
            "volume_multiplier": args.volume_multiplier, "trailing_stop_pct": args.trailing_stop_pct,
            "max_risk_eur": args.max_risk_eur, "max_position_eur": args.max_position_eur,
            "max_hold_h": args.max_hold_h, "loss_streak_limit": args.loss_streak_limit,
            "spread_pct": args.spread_pct, "taker_fee_rate": config.CRYPTO_TAKER_FEE_RATE,
        },
        "trade_log": [
            {
                "entry": datetime.fromtimestamp(t["entry_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "exit": datetime.fromtimestamp(t["exit_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "entry_price": round(t["entry_price"], 4), "exit_price": round(t["exit_price"], 4),
                "size_eur": round(t["cost"], 2), "pnl_eur": round(t["pnl"], 3),
                "hold_h": round(t["hold_h"], 1), "reason": t["reason"],
            }
            for t in trades
        ],
    }
    return result


def print_report(r: dict) -> None:
    print(f"\n{'=' * 62}")
    print(f"  DER SURFER — {r['pair']} | {r['start']} bis {r['end']} ({r['days']:.0f} Tage)")
    print(f"{'=' * 62}")
    print(f"  Startkapital      {r['budget_eur']:>10.2f} €")
    print(f"  Endkapital        {r['final_equity_eur']:>10.2f} €   ({r['total_return_pct']:+.2f} %)")
    print(f"  Realisiert        {r['realized_pnl_eur']:>10.2f} €")
    print(f"  Max. Drawdown     {r['max_drawdown_pct']:>10.2f} %")
    print()
    print(f"  Trades            {r['trades']:>10}   ({r['trades_per_month']:.2f}/Monat"
          + (f", {r['open_at_end']} offen am Ende" if r["open_at_end"] else "") + ")")
    if r["trades"]:
        print(f"  Trefferquote      {r['winrate_pct']:>10.1f} %")
        print(f"  Ø Gewinn          {r['avg_win_eur'] if r['avg_win_eur'] is not None else 0:>10.3f} €")
        print(f"  Ø Verlust         {r['avg_loss_eur'] if r['avg_loss_eur'] is not None else 0:>10.3f} €")
        pf = r["profit_factor"]
        print(f"  Profit-Faktor     {pf if pf is not None else float('inf'):>10}")
        print(f"  Ø Haltedauer      {r['avg_hold_h']:>10.1f} Std.")
        print(f"  Max. Risiko/Trade {r['max_risk_per_trade_eur']:>10.3f} €   "
              f"(Limit {r['params']['max_risk_eur']:.2f} €)")
        print(f"\n  Exit-Gründe:")
        for reason, n in sorted(r["exit_reasons"].items(), key=lambda kv: -kv[1]):
            print(f"     {reason:<20} {n:>4}x  ({n / r['trades'] * 100:.0f} %)")
    else:
        print("\n  ⚠️  Kein einziger Trade — Einstiegsgates zu eng für diesen Zeitraum.")
    print(f"{'=' * 62}\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pair", default="SOLEUR")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None)
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
    p.add_argument("--spread-pct", type=float, default=DEFAULT_SPREAD_PCT)
    p.add_argument("--json-out", default=None, help="Ergebnis zusätzlich als JSON speichern")
    p.add_argument("--quiet", action="store_true", help="Strategie-Logzeilen unterdrücken")
    args = p.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    result = asyncio.run(run_backtest(args))
    print_report(result)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"💾 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
