"""
Paper-trading gate helpers: aggregate logged trades from polybot/data/markets.

Win rate is not computed automatically: Polymarket settlement outcomes are not
stored in these JSON files. Use trade count + uptime for gates (1) and (3);
for (2) define wins externally (e.g. spreadsheet) once markets resolve.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data" / "markets"

GATE_MIN_TRADES = 200
GATE_MIN_WINRATE = 0.75
GATE_MIN_DAYS = 7


def _iter_trade_records():
    if not DATA_DIR.is_dir():
        return
    for path in DATA_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for t in data.get("trades") or []:
            yield path.name, t


def trade_count() -> int:
    return sum(1 for _ in _iter_trade_records())


def oldest_trade_age_days() -> float | None:
    oldest: float | None = None
    now = time.time()
    for _name, t in _iter_trade_records():
        ts = t.get("timestamp")
        if ts is None:
            continue
        age_days = (now - float(ts)) / 86400.0
        oldest = age_days if oldest is None else max(oldest, age_days)
    return oldest


def print_report() -> None:
    n = trade_count()
    span = oldest_trade_age_days()
    print(f"Logged paper trades (all markets): {n}")
    print(f"Gate: at least {GATE_MIN_TRADES} trades -> {'PASS' if n >= GATE_MIN_TRADES else 'NOT MET'}")
    if span is not None:
        print(f"Oldest logged trade age (max span in files): {span:.1f} days")
        print(
            f"Gate: {GATE_MIN_DAYS}+ consecutive days of operation -> "
            "verify manually from process uptime / logs (script only sees trade timestamps)."
        )
    else:
        print("No trades with timestamps found.")
    print(
        f"Gate: paper win rate > {GATE_MIN_WINRATE:.0%} -> "
        "NOT auto-computed; correlate log_trade entries with market resolutions."
    )


if __name__ == "__main__":
    print_report()
