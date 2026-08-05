"""End-of-test valuation helpers shared by candle backtests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polybot import config
from polybot import paper_db as paper_db_module

HOUR = 3600


def mark_open_trades(trades: list[dict], db_path: Path, prefix: str, sim,
                     spread_pct: float, mode: str) -> list[dict]:
    """Append synthetic closes for open ledger rows without changing bot state."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\' "
            "AND resolved_at IS NULL ORDER BY id",
            (paper_db_module.prefix_like_pattern(prefix),),
        ).fetchall()
    for row in rows:
        pair = str(row["market_question"]).removeprefix(prefix).split("_")[0]
        data_pair = "BTCEUR" if pair == "XBTEUR" else "SOLEUR" if pair == "SOLUSDC" else pair
        mid = sim.price.get(data_pair)
        if not mid:
            continue
        size, entry = float(row["size"]), float(row["price"])
        cost = size * entry
        bid = mid * (1 - spread_pct / 200)
        if mode == "hodl":
            value = size * bid * (1 - config.CRYPTO_TAKER_FEE_RATE)
            pnl = value - cost
        elif mode == "candlestick":
            value = size * bid * 0.995
            pnl = value - cost
        else:
            value = size * bid
            fee = config.CRYPTO_TAKER_FEE_RATE
            pnl = value - cost - cost * fee - value * fee
        entry_ts, exit_ts = float(row["timestamp"]), sim.clock.now
        trades.append({
            "pair": pair,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry_price": entry,
            "exit_price": value / size,
            "cost": cost,
            "pnl": pnl,
            "reason": "end_of_test_mtm",
            "hold_h": (exit_ts - entry_ts) / HOUR,
        })
    return trades
