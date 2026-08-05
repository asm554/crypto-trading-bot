"""Brutto-Edge-Auswertung über Backtest-Ergebnis-JSONs.

Für jeden Trade wird die Brutto-Bewegung vor Gebühren rekonstruiert:
alle Bots rechnen ``pnl = V - C - C*f - V*f`` (C = Einsatz, V = Exit-Wert,
f = Gebührensatz je Seite), also ``V = (pnl + C*(1+f)) / (1-f)`` und
``brutto = (V - C) / C``. Die Gebührenhürde je Trade ist ``f * (1 + V/C)``
(~2f). Leitfrage: mean(brutto) / mean(hürde) > 1?

    python -m backtest.analyze_edge backtest/results/research/*.json
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


def buy_and_hold_pct(pair: str, start_ts: float, end_ts: float) -> float | None:
    path = DATA_DIR / f"{pair}_60m.csv"
    if not path.exists():
        return None
    first = last = None
    with path.open() as fh:
        for row in csv.DictReader(fh):
            ts = float(row["timestamp"])
            if ts < start_ts or ts > end_ts:
                continue
            if first is None:
                first = float(row["close"])
            last = float(row["close"])
    if not first or not last:
        return None
    return (last / first - 1) * 100


def analyze(path: Path) -> None:
    r = json.loads(path.read_text())
    params = r.get("params", {})
    f = float(params.get("taker_fee_rate", 0.004))
    trades = [t for t in r.get("trade_log", []) if t.get("size_eur")]
    if not trades:
        print(f"{path.name}: keine Trades")
        return

    gross, hurdles = [], []
    for t in trades:
        c = float(t["size_eur"])
        pnl = float(t["pnl_eur"])
        v = (pnl + c * (1 + f)) / (1 - f)
        gross.append((v - c) / c * 100)
        hurdles.append(f * (1 + v / c) * 100)

    mean_gross = statistics.mean(gross)
    mean_hurdle = statistics.mean(hurdles)
    ratio = mean_gross / mean_hurdle if mean_hurdle else float("nan")
    holds = [t["hold_h"] for t in trades if t.get("hold_h") is not None]

    pairs = r.get("pairs") or [r.get("pair")]
    # Zeitfenster aus den Trades ableiten reicht nicht (erste/letzte Kerze!),
    # daher direkt aus den Rohdaten des Laufzeitraums.
    import datetime as dt
    start_ts = dt.datetime.strptime(r["start"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    end_ts = float("inf")
    if r.get("end") not in (None, "jetzt", "Datenende"):
        end_ts = dt.datetime.strptime(r["end"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    bh = {p: buy_and_hold_pct(p, start_ts, end_ts) for p in pairs if p}

    print(f"\n=== {path.name} ===")
    print(f"  Rendite gesamt      {r['total_return_pct']:+8.2f} %   "
          f"(Endkapital {r['final_equity_eur']:.2f} € von {r['budget_eur']:.2f} €)")
    print(f"  Trades              {len(trades):8d}   Trefferquote {r.get('winrate_pct')} %  PF {r.get('profit_factor')}")
    if holds:
        print(f"  Haltedauer Ø        {statistics.mean(holds):8.1f} h   Median {statistics.median(holds):.1f} h")
    print(f"  Brutto-Edge Ø       {mean_gross:+8.3f} %   Median {statistics.median(gross):+.3f} %")
    print(f"  Gebührenhürde Ø     {mean_hurdle:8.3f} %   (Satz {f*100:.2f} %/Seite)")
    print(f"  Verhältnis          {ratio:8.2f}x   {'>1: über der Hürde' if ratio > 1 else '<1: unter der Hürde'}")
    print(f"  Max. Drawdown       {r.get('max_drawdown_pct'):8.2f} %")
    print(f"  Exit-Gründe         {json.dumps(r.get('exit_reasons'), ensure_ascii=False)}")
    print(f"  Buy&Hold Vergleich  " + "  ".join(
        f"{p}: {v:+.1f} %" if v is not None else f"{p}: n/a" for p, v in bh.items()))


def main() -> int:
    for arg in sys.argv[1:]:
        analyze(Path(arg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
