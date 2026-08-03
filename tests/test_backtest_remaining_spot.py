import asyncio
import sqlite3
from pathlib import Path

import pytest

from backtest.backtest_multibot import PairSeries
from backtest.backtest_remaining_spot import RemainingMarketSim, sim_datetime
from backtest.backtest_surfer import Clock
from backtest.open_mark import mark_open_trades
from polybot import paper_db as paper_db_module


def row(ts, price, volume=1.0):
    return (float(ts), price, price + 1, price - 1, price, price, volume)


def test_feeds_expose_only_closed_candles():
    hourly = [row(0, 10), row(3600, 20), row(7200, 30)]
    steps = [row(0, 10), row(900, 11), row(1800, 12)]
    clock = Clock(4499)
    sim = RemainingMarketSim({"SOLEUR": PairSeries("SOLEUR", hourly, steps)}, clock, 0.1)

    assert [r[4] for r in asyncio.run(sim.fetch_ohlc("SOLEUR", 60))] == [10]
    assert [r[4] for r in asyncio.run(sim.fetch_ohlc("SOLUSDC", 15))] == [10, 11, 12]


def test_daily_feed_excludes_current_day():
    hourly = [row(h * 3600, 100 + h) for h in range(48)]
    steps = [row(h * 3600, 100 + h) for h in range(48)]
    clock = Clock(36 * 3600)
    sim = RemainingMarketSim({"BTCEUR": PairSeries("BTCEUR", hourly, steps)}, clock, 0.1)

    daily = asyncio.run(sim.fetch_ohlc("XBTEUR", 1440))
    assert len(daily) == 1
    assert daily[0][1] == 100
    assert daily[0][4] == 123


def test_sim_datetime_uses_backtest_clock():
    clock = Clock(1704067200)
    assert sim_datetime(clock).now().isoformat().startswith("2024-01-01T00:00:00")


def test_open_spot_trade_mark_uses_spread_and_both_fees(tmp_path: Path):
    db = tmp_path / "paper.db"
    with sqlite3.connect(db) as conn:
        conn.execute(paper_db_module.SYNC_SCHEMA_STATEMENTS[0])
        conn.execute(
            "INSERT INTO paper_trades(timestamp,market_question,side,size,price,status) VALUES(?,?,?,?,?,?)",
            (1000, "REV_SOLEUR", "buy", 2, 100, "paper"),
        )
    clock = Clock(4600)
    series = PairSeries("SOLEUR", [row(0, 110)], [row(0, 110)])
    sim = RemainingMarketSim({"SOLEUR": series}, clock, 0.2)
    sim.price["SOLEUR"] = 110

    marked = mark_open_trades([], db, "REV_", sim, 0.2, "spot")
    value = 2 * 110 * (1 - 0.2 / 200)
    expected = value - 200 - 200 * 0.004 - value * 0.004
    assert marked[0]["pnl"] == pytest.approx(expected)
    assert marked[0]["reason"] == "end_of_test_mtm"
    assert marked[0]["hold_h"] == 1
