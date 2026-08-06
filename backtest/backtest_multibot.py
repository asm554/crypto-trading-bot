"""Backtest-Harness für MomentumBot (MOM_), DaytradeBot (DAY_) und UltimateBot (ULT_).

Wie ``backtest_surfer.py`` fährt dieser Backtest die ECHTEN Strategie-Klassen
gegen aufgezeichnete Kraken-Kerzen, statt die Logik nachzubauen. Ersetzt werden
pro Strategiemodul nur:

- die Uhr (``<modul>.time`` -> Clock mit ``.time()``/``.ctime()``),
- ``fetch_ticker_data`` (liefert einen synthetischen Ticker je Paar aus der
  aktuellen 15m-Kerze inkl. Spread und rollierendem 24h-EUR-Volumen),
- die OHLC-/Änderungs-Helfer, die die Strategie aus dem Netz ziehen würde:
  ``momentum_strategy.rolling_24h_change_pct``,
  ``daytrade_strategy.rolling_change_pct`` + ``daytrade_strategy.fetch_ohlc``,
  ``ultimate_strategy.fetch_ohlc``.

Alles andere — Einstiegsgates, Sizing, Trailing-/Stop-/Zeit-Exits, Cooldowns,
Gebühren, Tageslimits, Verlustpausen — läuft als Produktivcode. Zusätzlich wird
``paper_db.DB_PATH`` VOR der Bot-Konstruktion auf eine Temp-DB gezeigt, damit
``_load_state_or_rebuild()`` niemals echten State aus ``polybot/data/`` liest.

Kein Lookahead: alle Feeds geben nur Kerzen zurück, die zur simulierten Zeit
bereits GESCHLOSSEN waren (``row[0] + 3600 <= clock.now``); die laufende Kerze
existiert für die Strategien nicht.

Bewusste, dokumentierte Abweichungen vom Live-Betrieb:

- Zeitschritt 15 Minuten (wie beim Surfer-Backtest). Live pollen MOM alle 60s,
  DAY alle 30s, ULT alle 60s. Enge Stops (DAY 1,5 % Trailing) werden dadurch
  eher zu spät ausgelöst; Haltedauern unter 15 Minuten sind nicht auflösbar.
- DAY scannt live alle 300s, hier alle 900s (Schrittweite) — weniger
  Einstiegsgelegenheiten als live.
- ``CANDIDATE_PAIRS`` (25 Paare) bzw. ULT-``PAIRS`` werden auf die Paare
  beschränkt, für die lokale Historie liegt — das Universum ist also kleiner
  als live.
- Der ULT-Feed liefert wie Kraken maximal die letzten 720 Stundenkerzen.
- ``analyse_market`` wird inhaltsgleich memoisiert (reine Funktion, gleicher
  Kerzensatz -> gleiches Ergebnis), sonst dauert ein ULT-Lauf Stunden.

Beispiele:

    python -m backtest.backtest_multibot --bot mom --start 2024-01-01
    python -m backtest.backtest_multibot --bot day --trailing-stop-pct 3
    python -m backtest.backtest_multibot --bot ult --account-loss-limit-pct 95
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import logging
import shutil
import statistics
import sys
import tempfile
import time as real_time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polybot import config  # noqa: E402
from polybot import paper_db as paper_db_module  # noqa: E402
from polybot import daytrade_strategy, momentum_strategy, surfer_strategy, ultimate_strategy  # noqa: E402
from backtest.backtest_surfer import (  # noqa: E402
    Clock,
    DEFAULT_SPREAD_PCT,
    build_ticker,
    load_candles,
    max_drawdown_pct,
    parse_date,
)

STEP_SEC = 15 * 60
HOUR_SEC = 3600

# Paare mit vollständiger Historie 2024-01-01..2026-08-02. XBTEUR fehlt hier
# absichtlich: der Download läuft noch (Stand: bis 2025-11-26) — ein Paar,
# dessen Feed mitten im Lauf endet, würde offene Positionen einfrieren.
FULL_PAIRS = ["SOLEUR", "ETHEUR", "ADAEUR", "XRPEUR"]
# ULT handelt live ``PAIRS = ("XBTEUR", "ETHEUR", "SOLEUR")``. In den Bitvavo-
# Daten heißt das BTC-Paar ``BTCEUR``; die Umbenennung passiert hier im
# Backtest-Layer (``ultimate_strategy.PAIRS`` wird ohnehin überschrieben),
# nicht in ``polybot/``.
ULT_DEFAULT_PAIRS = ["BTCEUR", "ETHEUR", "SOLEUR"]


class PairSeries:
    """Vorberechnete Arrays je Paar für schnelle Lookups ohne Lookahead."""

    def __init__(self, pair: str, hourly: list[tuple], steps: list[tuple]):
        self.pair = pair
        self.hourly = hourly
        self.h_ts = [r[0] for r in hourly]
        self.h_close = [r[4] for r in hourly]
        vol_eur = [r[6] * r[4] for r in hourly]
        cum = [0.0]
        for v in vol_eur:
            cum.append(cum[-1] + v)
        self.h_cum_vol_eur = cum
        self.steps_by_ts = {r[0]: r for r in steps}

    def closed_idx(self, now: float) -> int:
        """Anzahl Stundenkerzen, die zur Zeit ``now`` abgeschlossen sind."""
        return bisect.bisect_right(self.h_ts, now - HOUR_SEC)

    def vol_24h_eur(self, now: float) -> float:
        """EUR-Volumen aller Stundenkerzen, die in den letzten 24h geschlossen haben."""
        j = self.closed_idx(now)
        i = bisect.bisect_right(self.h_ts, now - 86400.0 - HOUR_SEC)
        return self.h_cum_vol_eur[j] - self.h_cum_vol_eur[i]

    def rolling_change_pct(self, now: float, lookback_bars: int) -> float | None:
        """Wie ``dca_strategy.rolling_change_pct``: letzte geschlossene Kerze vs.
        ``lookback_bars`` zurück, Fallback auf die älteste vorhandene."""
        n = self.closed_idx(now)
        if n < 2:
            return None
        last_close = self.h_close[n - 1]
        ref_close = self.h_close[max(0, n - 1 - lookback_bars)]
        if ref_close <= 0:
            return None
        return (last_close - ref_close) / ref_close * 100


class MarketSim:
    """Hält den Marktzustand zur simulierten Zeit und stellt die Fake-Feeds."""

    # Kraken liefert je OHLC-Request maximal 720 Kerzen — der ULT-Feed hier auch.
    KRAKEN_OHLC_WINDOW = 720
    # relative_volume() im Daytrader braucht nur die letzten ~21 Kerzen.
    DAY_OHLC_WINDOW = 60

    def __init__(self, series: dict[str, PairSeries], clock: Clock, spread_pct: float):
        self.series = series
        self.clock = clock
        self.spread_pct = spread_pct
        self.price: dict[str, float] = {}
        self._ult_ohlc_cache: dict[str, tuple[int, list[tuple]]] = {}

    def on_step(self, open_ts: float) -> None:
        for pair, s in self.series.items():
            row = s.steps_by_ts.get(open_ts)
            if row:
                self.price[pair] = float(row[4])

    async def fetch_ticker_data(self, pairs: list[str]) -> dict:
        now = self.clock.now
        out = {}
        for pair in pairs:
            s = self.series.get(pair)
            price = self.price.get(pair)
            if s is None or price is None:
                continue
            out[pair] = build_ticker(price, s.vol_24h_eur(now), self.spread_pct)
        return out

    async def rolling_change_pct(
        self, pair: str, lookback_bars: int = 24, interval_min: int = 60, ttl_sec: int = 900
    ) -> float | None:
        if interval_min != 60:
            raise ValueError(f"Backtest-Feed kennt nur 60m-Kerzen, nicht {interval_min}m")
        s = self.series.get(pair)
        return s.rolling_change_pct(self.clock.now, lookback_bars) if s else None

    async def rolling_24h_change_pct(self, pair: str, ttl_sec: int = 900) -> float | None:
        return await self.rolling_change_pct(pair, lookback_bars=24, interval_min=60)

    async def fetch_ohlc_day(self, pair: str, interval_min: int = 60) -> list[tuple]:
        """Für ``daytrade_strategy.fetch_ohlc``: nur GESCHLOSSENE Kerzen (das
        Live-Pendant wirft die laufende Kraken-Kerze selbst weg)."""
        if interval_min != 60:
            return []
        s = self.series.get(pair)
        if not s:
            return []
        idx = s.closed_idx(self.clock.now)
        return s.hourly[max(0, idx - self.DAY_OHLC_WINDOW):idx]

    async def fetch_ohlc_ult(self, pair: str, interval_min: int = 60) -> list[tuple]:
        """Für ``ultimate_strategy.fetch_ohlc``: die letzten <=720 geschlossenen
        Kerzen. ``closed_ohlc_rows`` in der Strategie filtert erneut — auf
        bereits geschlossenen Kerzen ist das ein No-Op."""
        if interval_min != 60:
            return []
        s = self.series.get(pair)
        if not s:
            return []
        idx = s.closed_idx(self.clock.now)
        cached = self._ult_ohlc_cache.get(pair)
        if cached and cached[0] == idx:
            return cached[1]
        rows = s.hourly[max(0, idx - self.KRAKEN_OHLC_WINDOW):idx]
        self._ult_ohlc_cache[pair] = (idx, rows)
        return rows


def memoized_analyse_market(orig):
    """Inhaltsgleicher Cache um ``analyse_market`` (reine Funktion über den
    Kerzensatz). Der Schlüssel identifiziert den Kerzensatz über Länge, erste/
    letzte Zeitstempel sowie letzte Close-/Volumenwerte — innerhalb einer
    simulierten Stunde ändert sich nichts, ohne Cache rechnet ULT dieselben
    EMAs viermal pro Stunde und Paar neu."""
    cache: dict[tuple, dict | None] = {}

    def wrapper(rows: list[tuple], *, volume_multiplier: float = 1.2, breakout_lookback: int = 20):
        if len(rows) < 2:
            return orig(rows, volume_multiplier=volume_multiplier, breakout_lookback=breakout_lookback)
        key = (
            len(rows), rows[0][0], rows[-1][0], rows[-1][4], rows[-1][6], rows[-2][4],
            volume_multiplier, breakout_lookback,
        )
        if key not in cache:
            if len(cache) > 512:
                cache.clear()
            cache[key] = orig(rows, volume_multiplier=volume_multiplier, breakout_lookback=breakout_lookback)
        return cache[key]

    return wrapper


def build_timeline(series: dict[str, PairSeries], start_ts: int, end_ts: int) -> list[float]:
    all_ts: set[float] = set()
    for s in series.values():
        all_ts.update(ts for ts in s.steps_by_ts if start_ts <= ts < end_ts)
    return sorted(all_ts)


def load_series(pairs: list[str], start_ts: int, end_ts: int) -> dict[str, PairSeries]:
    series = {}
    for pair in pairs:
        hourly = [r for r in load_candles(pair, 60) if start_ts <= r[0] < end_ts]
        steps = [r for r in load_candles(pair, 15) if start_ts <= r[0] < end_ts]
        if not hourly or not steps:
            raise SystemExit(f"{pair}: keine Kerzen im Zeitraum — Paar weglassen oder Zeitraum anpassen")
        series[pair] = PairSeries(pair, hourly, steps)
    return series


async def drive_simple(bot, sim: MarketSim, clock: Clock, timeline: list[float]):
    """Simulationsschleife für MOM/DAY (mehrere Positionen, je eine pro Paar)."""
    trades: list[dict] = []
    equity_curve: list[float] = []
    pending: dict[str, dict] = {}
    for open_ts in timeline:
        clock.now = float(open_ts) + STEP_SEC
        sim.on_step(open_ts)
        for r in await bot.manage_positions():
            p = pending.pop(r["pair"], None) or {}
            trades.append({
                "pair": r["pair"],
                "entry_ts": p.get("entry_ts"),
                "exit_ts": clock.now,
                "entry_price": p.get("entry_price"),
                "exit_price": sim.price.get(r["pair"]),
                "cost": p.get("cost"),
                "pnl": float(r["pnl"]),
                "reason": r["reason"],
                "hold_h": (clock.now - p["entry_ts"]) / 3600 if p.get("entry_ts") else None,
            })
        for o in await bot.scan_entries():
            pending[o["pair"]] = {
                "entry_ts": clock.now,
                "entry_price": float(o["price"]),
                "cost": float(o["amount"]),
            }
        if int(clock.now) % HOUR_SEC == 0:
            equity_curve.append((await bot.equity())["equity_eur"])
    return trades, equity_curve, pending


async def drive_ult(bot, sim: MarketSim, clock: Clock, timeline: list[float]):
    """Simulationsschleife für ULT (eine Position, Teilverkäufe + Nachkauf).

    ``partial_profit`` und ``single_add`` sind keine abgeschlossenen Trades:
    der finale Close-Event enthält den Gesamt-PnL der Position inklusive
    Teilgewinn — nur er landet als ein Trade in der Auswertung."""
    trades: list[dict] = []
    equity_curve: list[float] = []
    pending: dict[str, dict] = {}
    for open_ts in timeline:
        clock.now = float(open_ts) + STEP_SEC
        sim.on_step(open_ts)
        for ev in await bot.manage_positions():
            pair = ev.get("pair")
            reason = ev.get("reason")
            if reason == "partial_profit":
                if pair in pending:
                    pending[pair]["partials"] += 1
            elif reason == "single_add":
                if pair in pending:
                    pending[pair]["cost"] += float(ev.get("amount") or 0.0)
                    pending[pair]["adds"] += 1
            else:
                p = pending.pop(pair, None) or {}
                trades.append({
                    "pair": pair,
                    "entry_ts": p.get("entry_ts"),
                    "exit_ts": clock.now,
                    "entry_price": p.get("entry_price"),
                    "exit_price": sim.price.get(pair),
                    "cost": p.get("cost"),
                    "pnl": float(ev["pnl"]),
                    "reason": reason,
                    "setup": p.get("setup"),
                    "regime": p.get("regime"),
                    "score": p.get("score"),
                    "partials": p.get("partials", 0),
                    "adds": p.get("adds", 0),
                    "hold_h": (clock.now - p["entry_ts"]) / 3600 if p.get("entry_ts") else None,
                })
        for o in await bot.scan_entries():
            pending[o["pair"]] = {
                "entry_ts": clock.now,
                "entry_price": None,
                "cost": float(o["amount"]),
                "setup": o.get("setup"),
                "regime": o.get("regime"),
                "score": o.get("score"),
                "partials": 0,
                "adds": 0,
            }
        if int(clock.now) % HOUR_SEC == 0:
            equity_curve.append((await bot.equity())["equity_eur"])
    return trades, equity_curve, pending


def summarize(bot_name: str, args, pairs: list[str], params: dict,
              trades: list[dict], equity_curve: list[float], final: dict,
              pending: dict, span_days: float) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    holds = [t["hold_h"] for t in trades if t["hold_h"] is not None]
    reasons: dict[str, dict] = {}
    for t in trades:
        r = reasons.setdefault(t["reason"], {"n": 0, "pnl_eur": 0.0})
        r["n"] += 1
        r["pnl_eur"] += t["pnl"]
    for r in reasons.values():
        r["avg_pnl_eur"] = round(r["pnl_eur"] / r["n"], 4)
        r["pnl_eur"] = round(r["pnl_eur"], 3)
    by_pair: dict[str, dict] = {}
    for t in trades:
        p = by_pair.setdefault(t["pair"], {"n": 0, "pnl_eur": 0.0})
        p["n"] += 1
        p["pnl_eur"] += t["pnl"]
    for p in by_pair.values():
        p["pnl_eur"] = round(p["pnl_eur"], 3)

    return {
        "bot": bot_name,
        "pairs": pairs,
        "start": args.start,
        "end": args.end or "Datenende",
        "days": round(span_days, 1),
        "budget_eur": args.budget,
        "final_equity_eur": round(final["equity_eur"], 2),
        "total_return_pct": round((final["equity_eur"] / args.budget - 1) * 100, 2),
        "realized_pnl_eur": round(final["realized_pnl_eur"], 2),
        "trades": len(trades),
        "open_at_end": len(pending),
        "trades_per_month": round(len(trades) / max(span_days / 30.4, 0.01), 2),
        "winrate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_win_eur": round(gross_win / len(wins), 3) if wins else None,
        "avg_loss_eur": round(-gross_loss / len(losses), 3) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_drawdown_pct(equity_curve), 2),
        "avg_hold_h": round(sum(holds) / len(holds), 2) if holds else None,
        "median_hold_h": round(statistics.median(holds), 2) if holds else None,
        "exit_reasons": reasons,
        "by_pair": by_pair,
        "params": params,
        "trade_log": [
            {
                "pair": t["pair"],
                "entry": datetime.fromtimestamp(t["entry_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M") if t.get("entry_ts") else None,
                "exit": datetime.fromtimestamp(t["exit_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "size_eur": round(t["cost"], 2) if t.get("cost") is not None else None,
                "pnl_eur": round(t["pnl"], 4),
                "hold_h": round(t["hold_h"], 2) if t.get("hold_h") is not None else None,
                "reason": t["reason"],
                **({"setup": t["setup"], "partials": t["partials"], "adds": t["adds"], "score": t.get("score")} if "setup" in t else {}),
            }
            for t in trades
        ],
    }


def print_report(r: dict) -> None:
    print(f"\n{'=' * 66}")
    print(f"  {r['bot'].upper()} | {'+'.join(r['pairs'])} | {r['start']} bis {r['end']} ({r['days']:.0f} Tage)")
    print(f"{'=' * 66}")
    print(f"  Startkapital      {r['budget_eur']:>10.2f} €")
    print(f"  Endkapital        {r['final_equity_eur']:>10.2f} €   ({r['total_return_pct']:+.2f} %)")
    print(f"  Realisiert        {r['realized_pnl_eur']:>10.2f} €")
    print(f"  Max. Drawdown     {r['max_drawdown_pct']:>10.2f} %")
    print(f"  Trades            {r['trades']:>10}   ({r['trades_per_month']:.2f}/Monat"
          + (f", {r['open_at_end']} offen am Ende" if r["open_at_end"] else "") + ")")
    if r["trades"]:
        print(f"  Trefferquote      {r['winrate_pct']:>10.1f} %")
        print(f"  Ø Gewinn          {r['avg_win_eur'] if r['avg_win_eur'] is not None else 0:>10.3f} €")
        print(f"  Ø Verlust         {r['avg_loss_eur'] if r['avg_loss_eur'] is not None else 0:>10.3f} €")
        pf = r["profit_factor"]
        print(f"  Profit-Faktor     {pf if pf is not None else float('inf'):>10}")
        print(f"  Ø/Median Halten   {r['avg_hold_h']:>10.2f} / {r['median_hold_h']:.2f} Std.")
        print("\n  Exit-Gründe (n, Summe, Ø PnL):")
        for reason, v in sorted(r["exit_reasons"].items(), key=lambda kv: -kv[1]["n"]):
            print(f"     {reason:<22} {v['n']:>5}x  {v['pnl_eur']:>+9.2f} €  Ø {v['avg_pnl_eur']:+.4f} €")
        print("\n  Nach Paar:")
        for pair, v in sorted(r["by_pair"].items(), key=lambda kv: -kv[1]['n']):
            print(f"     {pair:<10} {v['n']:>5}x  {v['pnl_eur']:>+9.2f} €")
    else:
        print("\n  Kein einziger Trade — bei 0 Trades zuerst den Harness prüfen!")
    print(f"{'=' * 66}\n")


async def run_mom(args) -> dict:
    pairs = args.pairs.split(",") if args.pairs else list(FULL_PAIRS)
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(real_time.time())
    series = load_series(pairs, start_ts, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    span_days = (timeline[-1] - timeline[0]) / 86400
    clock = Clock(float(timeline[0]))
    sim = MarketSim(series, clock, args.spread_pct)

    workdir = Path(tempfile.mkdtemp(prefix="mom_bt_"))
    orig = (paper_db_module.DB_PATH, momentum_strategy.time, momentum_strategy.fetch_ticker_data,
            momentum_strategy.rolling_24h_change_pct, momentum_strategy.CANDIDATE_PAIRS)
    try:
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()
        momentum_strategy.time = clock
        momentum_strategy.fetch_ticker_data = sim.fetch_ticker_data
        momentum_strategy.rolling_24h_change_pct = sim.rolling_24h_change_pct
        momentum_strategy.CANDIDATE_PAIRS = list(pairs)

        # Defaults = polybot/main_momentum.py (500-€-Battle)
        params = {
            "initial_capital_eur": args.budget,
            "interval_sec": 3600,
            "entry_change_pct": 3.0,
            "entry_max_change_pct": 25.0,
            "min_volume_eur": 500_000.0,
            "position_eur": 60.0,
            "max_open_positions": 4,
            "trailing_stop_pct": args.trailing_stop_pct if args.trailing_stop_pct is not None else 2.5,
            "hard_stop_pct": 4.0,
            "max_hold_sec": 48 * 3600,
            "cooldown_sec": 6 * 3600,
            "cooldown_after_loss_sec": int((args.cooldown_after_loss_h if args.cooldown_after_loss_h is not None else 24.0) * 3600),
        }
        bot = momentum_strategy.MomentumBot(paper_mode=True, **params)
        bot.state_path = workdir / "momentum_state.json"
        bot.db_path = Path(paper_db_module.DB_PATH)

        trades, equity_curve, pending = await drive_simple(bot, sim, clock, timeline)
        final = await bot.equity()
        params["taker_fee_rate"] = config.CRYPTO_TAKER_FEE_RATE
        params["spread_pct"] = args.spread_pct
        return summarize("MOM (Der Zocker)", args, pairs, params, trades, equity_curve, final, pending, span_days)
    finally:
        (paper_db_module.DB_PATH, momentum_strategy.time, momentum_strategy.fetch_ticker_data,
         momentum_strategy.rolling_24h_change_pct, momentum_strategy.CANDIDATE_PAIRS) = orig
        shutil.rmtree(workdir, ignore_errors=True)


async def run_day(args) -> dict:
    pairs = args.pairs.split(",") if args.pairs else list(FULL_PAIRS)
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(real_time.time())
    series = load_series(pairs, start_ts, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    span_days = (timeline[-1] - timeline[0]) / 86400
    clock = Clock(float(timeline[0]))
    sim = MarketSim(series, clock, args.spread_pct)

    workdir = Path(tempfile.mkdtemp(prefix="day_bt_"))
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

        # Defaults = polybot/main_daytrade.py (500-€-Battle)
        params = {
            "initial_capital_eur": args.budget,
            "interval_sec": 300,
            "lookback_hours": 4,
            "entry_change_pct": 3.0,
            "entry_max_change_pct": 25.0,
            "min_volume_eur": 500_000.0,
            "volume_spike_enabled": True,
            "volume_lookback_bars": 20,
            "volume_multiplier": 2.0,
            "position_eur": 50.0,
            "max_open_positions": 4,
            "trailing_stop_pct": args.trailing_stop_pct if args.trailing_stop_pct is not None else 1.5,
            "hard_stop_pct": 3.0,
            "max_hold_sec": 6 * 3600,
            "cooldown_sec": 2 * 3600,
        }
        bot = daytrade_strategy.DaytradeBot(paper_mode=True, **params)
        bot.state_path = workdir / "daytrade_state.json"
        bot.db_path = Path(paper_db_module.DB_PATH)

        trades, equity_curve, pending = await drive_simple(bot, sim, clock, timeline)
        final = await bot.equity()
        params["taker_fee_rate"] = config.CRYPTO_TAKER_FEE_RATE
        params["spread_pct"] = args.spread_pct
        return summarize("DAY (Der Zappler)", args, pairs, params, trades, equity_curve, final, pending, span_days)
    finally:
        (paper_db_module.DB_PATH, daytrade_strategy.time, daytrade_strategy.fetch_ticker_data,
         daytrade_strategy.rolling_change_pct, daytrade_strategy.fetch_ohlc, daytrade_strategy.CANDIDATE_PAIRS) = orig
        shutil.rmtree(workdir, ignore_errors=True)


async def run_ult(args) -> dict:
    pairs = args.pairs.split(",") if args.pairs else list(ULT_DEFAULT_PAIRS)
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(real_time.time())
    series = load_series(pairs, start_ts, end_ts)
    timeline = build_timeline(series, start_ts, end_ts)
    span_days = (timeline[-1] - timeline[0]) / 86400
    clock = Clock(float(timeline[0]))
    sim = MarketSim(series, clock, args.spread_pct)

    workdir = Path(tempfile.mkdtemp(prefix="ult_bt_"))
    orig = (paper_db_module.DB_PATH, ultimate_strategy.time, ultimate_strategy.fetch_ticker_data,
            ultimate_strategy.fetch_ohlc, ultimate_strategy.PAIRS, ultimate_strategy.analyse_market,
            surfer_strategy.time)
    try:
        paper_db_module.DB_PATH = str(workdir / "paper_trades.db")
        await paper_db_module.init_db()
        ultimate_strategy.time = clock
        # closed_ohlc_rows lebt in surfer_strategy und fragt dort time.time() ab.
        surfer_strategy.time = clock
        ultimate_strategy.fetch_ticker_data = sim.fetch_ticker_data
        ultimate_strategy.fetch_ohlc = sim.fetch_ohlc_ult
        ultimate_strategy.PAIRS = tuple(pairs)
        ultimate_strategy.analyse_market = memoized_analyse_market(orig[5])

        # Defaults = polybot/main_ultimate.py (500-€-Battle); Achtung: min_score
        # ist dort 85, nicht der Klassen-Default 80.
        params = {
            "initial_capital_eur": args.budget,
            "interval_sec": 300,
            "min_score": args.min_score if args.min_score is not None else 85,
            "volume_multiplier": 1.2,
            "atr_stop_multiplier": 2.0,
            "reward_risk_ratio": 2.0,
            "max_risk_eur": 2.5,
            "max_position_eur": 125.0,
            "max_hold_sec": 72 * 3600,
            "account_loss_limit_pct": args.account_loss_limit_pct if args.account_loss_limit_pct is not None else 10.0,
            "fee_rate": args.fee_rate if args.fee_rate is not None else 0.008,
            "min_hold_sec": int((args.min_hold_min if args.min_hold_min is not None else 60) * 60),
            "pair_cooldown_sec": 12 * 3600,
            "loss_streak_limit": 2,
            "loss_pause_sec": 12 * 3600,
            "max_entries_per_day": 3,
            "max_spread_pct": 0.15,
            "score_scaled_sizing": False,
        }
        # --ult-set KEY=WERT überschreibt einzelne Gates (Typ vom Default geerbt),
        # damit gelockerte Varianten ohne Codeänderung laufen.
        for override in args.ult_set or []:
            key, _, raw = override.partition("=")
            key = key.strip()
            if key not in params:
                raise SystemExit(f"--ult-set: unbekannter Parameter {key!r}; erlaubt: {sorted(params)}")
            params[key] = type(params[key])(float(raw))
        bot = ultimate_strategy.UltimateBot(paper_mode=True, state_path=workdir / "ultimate_state.json", **params)
        bot.db_path = Path(paper_db_module.DB_PATH)
        if not args.state_writes:
            # ``_save_state`` schreibt bei JEDEM Scan eine JSON-Datei (Temp-Datei
            # + rename). Über sieben Jahre sind das ~500k Dateisystem-Operationen
            # und der mit Abstand größte Zeitfresser. Der State liegt hier in
            # einem Temp-Verzeichnis und wird nach ``__init__`` nie wieder
            # gelesen — das Stubben ist ergebnisneutral (mit --state-writes
            # gegengeprüft). Der In-Memory-State bleibt unangetastet.
            bot._save_state = lambda: None

        trades, equity_curve, pending = await drive_ult(bot, sim, clock, timeline)
        final = await bot.equity()
        params["spread_pct"] = args.spread_pct
        # analyze_edge.py liest den Gebührensatz unter diesem Namen.
        params["taker_fee_rate"] = params["fee_rate"]
        result = summarize("ULT (Der Ultimative)", args, pairs, params, trades, equity_curve, final, pending, span_days)
        result["setups"] = {}
        for t in trades:
            s = result["setups"].setdefault(t.get("setup") or "?", {"n": 0, "pnl_eur": 0.0})
            s["n"] += 1
            s["pnl_eur"] = round(s["pnl_eur"] + t["pnl"], 3)
        return result
    finally:
        (paper_db_module.DB_PATH, ultimate_strategy.time, ultimate_strategy.fetch_ticker_data,
         ultimate_strategy.fetch_ohlc, ultimate_strategy.PAIRS, ultimate_strategy.analyse_market,
         surfer_strategy.time) = orig
        shutil.rmtree(workdir, ignore_errors=True)


RUNNERS = {"mom": run_mom, "day": run_day, "ult": run_ult}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bot", required=True, choices=sorted(RUNNERS))
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--budget", type=float, default=500.0)
    p.add_argument("--pairs", default=None, help="Kommagetrennt; Default: alle Paare mit voller Historie")
    p.add_argument("--spread-pct", type=float, default=DEFAULT_SPREAD_PCT)
    p.add_argument("--trailing-stop-pct", type=float, default=None, help="MOM/DAY: Trailing-Stop (Default 2.5/1.5)")
    p.add_argument("--cooldown-after-loss-h", type=float, default=None, help="MOM: Cooldown nach Verlust (Default 24)")
    p.add_argument("--account-loss-limit-pct", type=float, default=None, help="ULT: Kontoverlust-Sperre (Default 10, KEIN Reset!)")
    p.add_argument("--min-hold-min", type=float, default=None, help="ULT: Mindesthaltedauer in Minuten (Default 60)")
    p.add_argument("--min-score", type=int, default=None, help="ULT: Mindest-Score (Default 85 wie main_ultimate.py)")
    p.add_argument("--fee-rate", type=float, default=None, help="ULT: Taker-Gebühr je Seite (Default 0.008 wie main_ultimate.py)")
    p.add_argument("--ult-set", action="append", default=None, metavar="KEY=WERT",
                   help="ULT: einzelnen Konstruktorparameter überschreiben, mehrfach nutzbar")
    p.add_argument("--state-writes", action="store_true",
                   help="ULT: State-JSON wirklich schreiben (langsam, nur zur Gegenprobe)")
    p.add_argument("--json-out", default=None)
    p.add_argument("--verbose", action="store_true", help="Strategie-Logzeilen zeigen (sehr laut)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    result = asyncio.run(RUNNERS[args.bot](args))
    print_report(result)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
