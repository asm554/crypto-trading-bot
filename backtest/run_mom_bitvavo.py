"""Varianten-Runner für "Der Zocker" (MOM_) auf den Bitvavo-Kerzen.

Fährt ``backtest.backtest_multibot.run_mom`` — also die echte ``MomentumBot``-
Klasse — über mehrere Marktregime, Trailing-Stops, Verlust-Cooldowns und zwei
Gebührenniveaus. Kein Code in ``polybot/`` wird geändert: der Gebührensatz wird
zur Laufzeit auf ``polybot.config.CRYPTO_TAKER_FEE_RATE`` gesetzt, den
``momentum_strategy`` bei jedem Fill neu ausliest.

Zusätzlich zur Standard-Auswertung von ``summarize()`` wird je Lauf die
Brutto-Edge berechnet (Bewegung VOR Gebühren) plus t-Wert und 95-%-KI, damit
eine zufällig positive Variante nicht als Edge durchgeht.

    python -m backtest.run_mom_bitvavo --list
    python -m backtest.run_mom_bitvavo --variant full_t25_cd24_kraken
    python -m backtest.run_mom_bitvavo --summary
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polybot import config  # noqa: E402
from backtest.backtest_multibot import run_mom  # noqa: E402
from backtest.backtest_surfer import DEFAULT_SPREAD_PCT, load_candles, parse_date  # noqa: E402

RESULT_DIR = Path(__file__).resolve().parent / "results" / "bitvavo"

# Gebührensätze je SEITE. Kraken = polybot/config.py-Default (0,40 %),
# Bitvavo = 0,25 % Taker im Basis-Tier.
FEES = {"kraken": 0.004, "bitvavo": 0.0025}

ALL_PAIRS = ["BTCEUR", "ETHEUR", "SOLEUR", "XRPEUR", "ADAEUR"]

# Marktregime. Achtung: die 15m-Historie (Zeitschritt + Fill-Preis) beginnt je
# Paar später als die 1h-Historie — ETHEUR/XRPEUR ab 2019-03, BTCEUR ab
# 2021-02, ADAEUR ab 2021-05, SOLEUR ab 2021-08. Ein Paar ohne 15m-Kerze ist
# zu dem Zeitpunkt schlicht nicht handelbar (kein Ticker), verzerrt also nicht,
# verkleinert aber das Universum. Deshalb gibt es zum Bullenmarkt zusätzlich
# eine Kontrollvariante nur mit ETHEUR+XRPEUR (durchgehend abgedeckt).
PERIODS = {
    # Referenz: maximale Historie, Paare treten bei, sobald 15m-Daten da sind.
    "full": ("2019-03-08", None, ALL_PAIRS),
    # Bullenmarkt: Corona-Tief -> ATH November 2021.
    "bull": ("2020-10-01", "2021-11-30", ALL_PAIRS),
    # Kontrolle zum Bullenmarkt mit durchgehend abgedecktem Universum.
    "bull2": ("2020-10-01", "2021-11-30", ["ETHEUR", "XRPEUR"]),
    # Bärenmarkt 2022 (Luna, 3AC, FTX).
    "bear": ("2022-01-01", "2023-01-01", ALL_PAIRS),
    # Der bisher getestete Zeitraum.
    "since2024": ("2024-01-01", None, ALL_PAIRS),
}

TRAILING = {"t25": 2.5, "t50": 5.0}
COOLDOWN = {"cd06": 6.0, "cd24": 24.0}


def variants() -> dict[str, dict]:
    out = {}
    for pk, (start, end, pairs) in PERIODS.items():
        for tk, tv in TRAILING.items():
            for ck, cv in COOLDOWN.items():
                for fk, fv in FEES.items():
                    out[f"{pk}_{tk}_{ck}_{fk}"] = {
                        "period": pk, "start": start, "end": end, "pairs": pairs,
                        "trailing_stop_pct": tv, "cooldown_after_loss_h": cv,
                        "fee_key": fk, "fee_rate": fv,
                    }
    return out


def edge_metrics(trade_log: list[dict], fee: float) -> dict:
    """Brutto-Edge vor Gebühren + Signifikanz.

    Alle Bots rechnen ``pnl = V - C - C*f - V*f``; daraus folgt exakt
    ``V = (pnl + C*(1+f)) / (1-f)``. ``edge_pct``/``ratio`` nutzen zusätzlich
    die im Auftrag vorgegebene Näherung (Round-Trip = 2f auf das Notional).
    """
    log = [t for t in trade_log if t.get("size_eur")]
    if not log:
        return {"trades": 0}
    rt = 2 * fee
    notional = sum(t["size_eur"] for t in log)
    net = sum(t["pnl_eur"] for t in log)
    gross_approx = net + notional * rt
    edge_pct = gross_approx / notional * 100
    ratio = edge_pct / (rt * 100)

    gross_pct: list[float] = []   # Brutto-Rendite je Trade in %
    hurdle_pct: list[float] = []  # exakte Gebührenhürde je Trade in %
    gross_eur = 0.0
    for t in log:
        c = float(t["size_eur"])
        v = (float(t["pnl_eur"]) + c * (1 + fee)) / (1 - fee)
        gross_pct.append((v - c) / c * 100)
        hurdle_pct.append(fee * (1 + v / c) * 100)
        gross_eur += v - c
    n = len(gross_pct)
    mean_g = statistics.mean(gross_pct)
    sd_g = statistics.stdev(gross_pct) if n > 1 else 0.0
    se_g = sd_g / math.sqrt(n) if n > 1 else float("nan")
    net_of_fee = [g - h for g, h in zip(gross_pct, hurdle_pct)]
    mean_n = statistics.mean(net_of_fee)
    sd_n = statistics.stdev(net_of_fee) if n > 1 else 0.0
    se_n = sd_n / math.sqrt(n) if n > 1 else float("nan")
    return {
        "trades": n,
        "notional_eur": round(notional, 2),
        "net_pnl_eur": round(net, 2),
        "gross_pnl_eur_exact": round(gross_eur, 2),
        "round_trip_fee_pct": round(rt * 100, 3),
        "edge_pct": round(edge_pct, 4),
        "edge_pct_exact": round(gross_eur / notional * 100, 4),
        "ratio": round(ratio, 3),
        "mean_gross_pct_per_trade": round(mean_g, 4),
        "median_gross_pct_per_trade": round(statistics.median(gross_pct), 4),
        "sd_gross_pct": round(sd_g, 4),
        "t_gross": round(mean_g / se_g, 3) if se_g and se_g == se_g else None,
        "ci95_gross_pct": [round(mean_g - 1.96 * se_g, 4), round(mean_g + 1.96 * se_g, 4)] if se_g == se_g else None,
        "mean_hurdle_pct": round(statistics.mean(hurdle_pct), 4),
        "mean_net_of_fee_pct": round(mean_n, 4),
        "t_net_of_fee": round(mean_n / se_n, 3) if se_n and se_n == se_n else None,
        "ci95_net_of_fee_pct": [round(mean_n - 1.96 * se_n, 4), round(mean_n + 1.96 * se_n, 4)] if se_n == se_n else None,
    }


def buy_and_hold(pairs: list[str], start: str, end: str | None) -> dict:
    lo = parse_date(start)
    hi = parse_date(end) if end else float("inf")
    out = {}
    for p in pairs:
        rows = [r for r in load_candles(p, 60) if lo <= r[0] < hi]
        out[p] = round((rows[-1][4] / rows[0][4] - 1) * 100, 1) if len(rows) > 1 else None
    return out


def run_variant(key: str, spec: dict, budget: float) -> dict:
    args = SimpleNamespace(
        bot="mom", start=spec["start"], end=spec["end"], budget=budget,
        pairs=",".join(spec["pairs"]), spread_pct=DEFAULT_SPREAD_PCT,
        trailing_stop_pct=spec["trailing_stop_pct"],
        cooldown_after_loss_h=spec["cooldown_after_loss_h"],
    )
    orig_fee = config.CRYPTO_TAKER_FEE_RATE
    try:
        config.CRYPTO_TAKER_FEE_RATE = spec["fee_rate"]
        result = asyncio.run(run_mom(args))
    finally:
        config.CRYPTO_TAKER_FEE_RATE = orig_fee
    result["variant"] = key
    result["regime"] = spec["period"]
    result["fee_venue"] = spec["fee_key"]
    result["edge"] = edge_metrics(result["trade_log"], spec["fee_rate"])
    result["buy_and_hold_pct"] = buy_and_hold(spec["pairs"], spec["start"], spec["end"])
    return result


def print_edge(r: dict) -> None:
    e = r["edge"]
    if not e.get("trades"):
        print(f"  {r['variant']}: KEIN TRADE — Harness prüfen!")
        return
    print(f"\n--- {r['variant']} ---")
    print(f"  Rendite            {r['total_return_pct']:+9.2f} %   PF {r.get('profit_factor')}   "
          f"Trefferquote {r.get('winrate_pct')} %   Trades {r['trades']}")
    print(f"  Brutto-Edge        {e['edge_pct']:+9.4f} % je Notional   (exakt {e['edge_pct_exact']:+.4f} %)")
    print(f"  Gebührenhürde      {e['round_trip_fee_pct']:9.3f} %   -> ratio {e['ratio']:.3f}x")
    print(f"  Ø Brutto/Trade     {e['mean_gross_pct_per_trade']:+9.4f} %   t = {e['t_gross']}   "
          f"KI95 {e['ci95_gross_pct']}")
    print(f"  Ø nach Gebühr      {e['mean_net_of_fee_pct']:+9.4f} %   t = {e['t_net_of_fee']}   "
          f"KI95 {e['ci95_net_of_fee_pct']}")
    print(f"  Buy&Hold           {r['buy_and_hold_pct']}")


def summary() -> None:
    rows = []
    for p in sorted(RESULT_DIR.glob("mom_*.json")):
        r = json.loads(p.read_text())
        e = r.get("edge", {})
        rows.append((r.get("variant", p.stem), r["days"], r["trades"], r["total_return_pct"],
                     r.get("winrate_pct"), r.get("profit_factor"), e.get("edge_pct"),
                     e.get("ratio"), e.get("mean_gross_pct_per_trade"), e.get("t_gross"),
                     e.get("ci95_gross_pct"), e.get("t_net_of_fee")))
    hdr = f"{'Variante':<28}{'Tage':>6}{'Trades':>8}{'Rend.%':>10}{'Tref.%':>8}{'PF':>6}{'Edge%':>9}{'ratio':>8}{'Ø brutto%':>11}{'t':>8}{'KI95 brutto':>26}{'t netto':>9}"
    print(hdr)
    print("-" * len(hdr))
    for v, d, n, ret, wr, pf, edge, ratio, mg, t, ci, tn in sorted(rows):
        ci_s = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "-"
        print(f"{v:<28}{d:>6.0f}{n:>8}{ret:>10.2f}{(wr if wr is not None else 0):>8.1f}"
              f"{(pf if pf is not None else 0):>6.2f}{(edge if edge is not None else 0):>9.4f}"
              f"{(ratio if ratio is not None else 0):>8.3f}{(mg if mg is not None else 0):>11.4f}"
              f"{(t if t is not None else 0):>8.2f}{ci_s:>26}{(tn if tn is not None else 0):>9.2f}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant", action="append", default=None)
    p.add_argument("--period", default=None, help="alle Varianten eines Regimes")
    p.add_argument("--budget", type=float, default=500.0)
    p.add_argument("--list", action="store_true")
    p.add_argument("--summary", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    vs = variants()
    if args.list:
        for k in vs:
            print(k)
        return 0
    if args.summary:
        summary()
        return 0

    keys = args.variant or [k for k, v in vs.items() if v["period"] == args.period]
    if not keys:
        print("Nichts zu tun — --variant/--period prüfen", file=sys.stderr)
        return 2
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    for k in keys:
        r = run_variant(k, vs[k], args.budget)
        (RESULT_DIR / f"mom_{k}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False))
        print_edge(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
