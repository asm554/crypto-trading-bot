"""Brutto-Edge + Signifikanz über Backtest-Ergebnis-JSONs.

Ergänzt ``analyze_edge.py`` um genau die Kennzahlen, an denen eine Strategie
scheitert oder besteht:

- ``edge_pct``/``ratio`` nach der Notional-Formel des Auftrags::

      notional = sum(size_eur)
      gross    = sum(pnl_eur) + notional * round_trip_fee
      edge_pct = gross / notional * 100
      ratio    = edge_pct / (round_trip_fee * 100)      # muss > 1.0

- die exakte Rekonstruktion je Trade (das Gebührenmodell der Bots ist
  ``pnl = V - C - C*f - V*f``, also ``V = (pnl + C*(1+f)) / (1-f)``), daraus
  Brutto-Rendite je Trade ``g = (V - C) / C``. Die Notional-Formel unterstellt
  die Gebühr auf dem Einsatz statt zusätzlich auf dem Exit-Wert und liegt
  darum minimal daneben — beide Werte werden ausgewiesen.

- Signifikanz: t-Wert und 95-%-Konfidenzintervall des Mittelwerts von ``g``
  (Normalapproximation, n ist hier immer dreistellig), zusätzlich der
  Median-Trade und der t-Wert der Netto-Rendite je Trade.

``--fee-rate`` überschreibt den im JSON gespeicherten Satz NICHT für die
Simulation (dafür muss der Lauf selbst mit dem Satz gefahren werden), sondern
nur für die Auswertung — nützlich, um zu sehen, was derselbe Trade-Strom bei
anderen Gebühren gebracht hätte.

    python -m backtest.edge_stats backtest/results/bitvavo/day_*.json
    python -m backtest.edge_stats --csv r.csv backtest/results/bitvavo/*.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

Z95 = 1.959963985


def stats_for(path: Path, fee_override: float | None = None) -> dict | None:
    r = json.loads(path.read_text())
    params = r.get("params", {})
    f = float(fee_override if fee_override is not None else params.get("taker_fee_rate", 0.004))
    rt = 2 * f
    trades = [t for t in r.get("trade_log", []) if t.get("size_eur")]
    if not trades:
        return None

    notional = sum(float(t["size_eur"]) for t in trades)
    net = sum(float(t["pnl_eur"]) for t in trades)
    gross_notional = net + notional * rt
    edge_pct = gross_notional / notional * 100
    ratio = edge_pct / (rt * 100)

    g, hurdle, netpct = [], [], []
    for t in trades:
        c = float(t["size_eur"])
        pnl = float(t["pnl_eur"])
        v = (pnl + c * (1 + f)) / (1 - f)
        g.append((v - c) / c * 100)
        hurdle.append(f * (1 + v / c) * 100)
        netpct.append(pnl / c * 100)

    n = len(g)
    mg, sg = statistics.mean(g), statistics.stdev(g) if n > 1 else 0.0
    se = sg / math.sqrt(n) if n > 1 else float("nan")
    t_gross = mg / se if se else float("nan")
    mn, sn = statistics.mean(netpct), statistics.stdev(netpct) if n > 1 else 0.0
    se_n = sn / math.sqrt(n) if n > 1 else float("nan")

    holds = [t["hold_h"] for t in trades if t.get("hold_h") is not None]
    days = float(r.get("days") or 0.0)
    ret = float(r.get("total_return_pct") or 0.0)
    cagr = ((1 + ret / 100) ** (365.0 / days) - 1) * 100 if days > 30 and ret > -100 else None

    return {
        "file": path.name,
        "start": r.get("start"),
        "end": r.get("end"),
        "days": round(days, 1),
        "pairs": len(r.get("pairs") or []),
        "fee_rate": f,
        "round_trip_fee_pct": rt * 100,
        "trades": n,
        "return_pct": ret,
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "max_dd_pct": r.get("max_drawdown_pct"),
        "winrate_pct": r.get("winrate_pct"),
        "profit_factor": r.get("profit_factor"),
        "avg_hold_h": round(statistics.mean(holds), 1) if holds else None,
        "median_hold_h": round(statistics.median(holds), 1) if holds else None,
        "notional_eur": round(notional, 1),
        "net_pnl_eur": round(net, 2),
        "fees_eur": round(notional * rt, 2),
        "gross_pnl_eur": round(gross_notional, 2),
        "edge_pct": round(edge_pct, 4),
        "ratio": round(ratio, 3),
        # exakte Rekonstruktion
        "edge_pct_exact_weighted": round(sum(
            ((float(t["pnl_eur"]) + float(t["size_eur"]) * (1 + f)) / (1 - f) - float(t["size_eur"]))
            for t in trades) / notional * 100, 4),
        "gross_mean_pct": round(mg, 4),
        "gross_median_pct": round(statistics.median(g), 4),
        "gross_sd_pct": round(sg, 3),
        "gross_t": round(t_gross, 2),
        "gross_ci95_lo": round(mg - Z95 * se, 4),
        "gross_ci95_hi": round(mg + Z95 * se, 4),
        "hurdle_mean_pct": round(statistics.mean(hurdle), 4),
        "ratio_pertrade": round(mg / statistics.mean(hurdle), 3),
        "net_mean_pct": round(mn, 4),
        "net_median_pct": round(statistics.median(netpct), 4),
        "net_t": round(mn / se_n, 2) if se_n else None,
    }


def print_row(s: dict) -> None:
    sig = "signifikant >0" if s["gross_ci95_lo"] > 0 else (
        "signifikant <0" if s["gross_ci95_hi"] < 0 else "nicht von 0 unterscheidbar")
    print(f"\n=== {s['file']}  ({s['start']}..{s['end']}, {s['days']:.0f} Tage, "
          f"{s['pairs']} Paare, Gebühr {s['round_trip_fee_pct']:.2f} % RT) ===")
    print(f"  Rendite            {s['return_pct']:+9.2f} %"
          + (f"   (CAGR {s['cagr_pct']:+.2f} %)" if s['cagr_pct'] is not None else "")
          + f"   MaxDD {s['max_dd_pct']} %")
    print(f"  Trades             {s['trades']:9d}     Trefferquote {s['winrate_pct']} %   PF {s['profit_factor']}")
    print(f"  Haltedauer Ø/Med   {s['avg_hold_h']:>9} / {s['median_hold_h']} h")
    print(f"  Notional           {s['notional_eur']:9.0f} €   Gebühren {s['fees_eur']:.2f} €   "
          f"Netto {s['net_pnl_eur']:+.2f} €   Brutto {s['gross_pnl_eur']:+.2f} €")
    print(f"  edge_pct           {s['edge_pct']:+9.4f} %   ratio {s['ratio']:.3f}x   "
          f"(exakt gewichtet {s['edge_pct_exact_weighted']:+.4f} %)")
    print(f"  Brutto je Trade    Ø {s['gross_mean_pct']:+.4f} %   Median {s['gross_median_pct']:+.4f} %   "
          f"SD {s['gross_sd_pct']:.2f} %")
    print(f"  Signifikanz        t = {s['gross_t']:+.2f}   95%-KI [{s['gross_ci95_lo']:+.4f}, "
          f"{s['gross_ci95_hi']:+.4f}] %  -> {sig}")
    print(f"  Netto je Trade     Ø {s['net_mean_pct']:+.4f} %   Median {s['net_median_pct']:+.4f} %   "
          f"t = {s['net_t']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    p.add_argument("--fee-rate", type=float, default=None,
                   help="Gebührensatz je Seite nur für die AUSWERTUNG überschreiben")
    p.add_argument("--csv", default=None)
    a = p.parse_args()

    rows = []
    for arg in a.paths:
        s = stats_for(Path(arg), a.fee_rate)
        if s is None:
            print(f"{arg}: keine Trades")
            continue
        rows.append(s)
        print_row(s)

    if rows:
        print(f"\n{'Datei':<34} {'Tage':>6} {'Trades':>7} {'Rend.%':>9} {'edge%':>9} {'ratio':>7} {'t':>7} {'KI-lo%':>9}")
        for s in rows:
            print(f"{s['file']:<34} {s['days']:>6.0f} {s['trades']:>7d} {s['return_pct']:>+9.2f} "
                  f"{s['edge_pct']:>+9.4f} {s['ratio']:>7.3f} {s['gross_t']:>+7.2f} {s['gross_ci95_lo']:>+9.4f}")
    if a.csv and rows:
        out = Path(a.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
