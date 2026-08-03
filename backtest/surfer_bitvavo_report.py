"""Tabellarische Auswertung der Bitvavo-Surfer-Läufe.

Liest die JSONs aus ``backtest/results/bitvavo/`` und stellt je Lauf die
entscheidende Kennzahl dar: kommt die Brutto-Bewegung je Trade über die
Gebührenhürde?

Wichtig für die Gebührenspalten: die Brutto-Bewegung ist **gebührenunabhängig** —
Ein-/Ausstiegsentscheidungen der Strategie hängen nur an Preisen, nicht am
Gebührensatz. Deshalb lässt sich ``ratio`` für jedes Gebührenniveau aus
derselben Brutto-Edge ableiten (``edge_pct / (2*fee*100)``); die Netto-Rendite
dagegen nicht, die kommt aus dem jeweiligen Lauf.

    python -m backtest.surfer_bitvavo_report            # alles
    python -m backtest.surfer_bitvavo_report ref_       # nur Präfix ref_
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results" / "bitvavo"
KRAKEN_RT = 0.008
BITVAVO_RT = 0.005


def load(prefix: str = "") -> list[dict]:
    out = []
    for p in sorted(RESULTS.glob(f"surfer_{prefix}*.json")):
        r = json.loads(p.read_text())
        r["_file"] = p.name
        out.append(r)
    return out


def row(r: dict) -> str:
    e = r.get("edge") or {}
    n = e.get("trades", 0)
    if not n:
        return (f"{r.get('name', r['_file']):<26} {r['pair']:<7} {r['start']}..{r['end']:<10} "
                f"{r['days']:>6.0f}d   —  keine Trades")
    edge = e["edge_pct"]
    return (
        f"{r.get('name', r['_file']):<26} {r['pair']:<7} {r['start']}..{r['end']:<10} "
        f"{r['days']:>6.0f}d n={n:<4} "
        f"ret={r['total_return_pct']:+7.2f}% bh={r.get('buy_and_hold_pct', float('nan')):+8.1f}% "
        f"edge={edge:+7.3f}% med={e['median_gross_pct']:+7.3f}% "
        f"K={edge / (KRAKEN_RT * 100):5.2f}x B={edge / (BITVAVO_RT * 100):5.2f}x "
        f"t={e['t_stat'] if e['t_stat'] is not None else float('nan'):+6.2f} "
        f"KI95=[{e['ci95_low_pct']:+.2f},{e['ci95_high_pct']:+.2f}] "
        f"WR={r.get('winrate_pct')}%"
    )


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    results = load(prefix)
    if not results:
        print(f"Keine Ergebnisse unter {RESULTS} (Präfix '{prefix}')")
        return 1
    print("Spalten: edge = Brutto-Edge je Notional, med = Median-Brutto-Trade,")
    print("         K/B = edge / Round-Trip-Gebühr (Kraken 0,80 % | Bitvavo 0,50 %), >1,0 nötig")
    print("         t / KI95 = t-Wert und 95-%-Konfidenzintervall der Brutto-Rendite je Trade\n")
    for r in results:
        print(row(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
