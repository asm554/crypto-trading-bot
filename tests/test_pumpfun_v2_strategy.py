import asyncio
from collections import deque
import sqlite3
import time

import pytest

from polybot import paper_db
from polybot.pumpfun_v2_strategy import PumpFunV2PaperBot


def test_pumpfun_v2_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        PumpFunV2PaperBot(paper_mode=False)


def test_pumpfun_v2_is_separate_namespace_and_state():
    bot = PumpFunV2PaperBot()
    assert bot.prefix == "PUMP2_"
    assert bot.bot_key == "pumpfun_v2"
    assert bot.state_path.name == "pumpfun_v2_state.json"
    assert bot.position_eur == 10.0
    assert bot.strategy_version == "v2-chainstack-inspired-paper"


def test_pumpfun_v2_writes_only_to_its_ledger_and_snapshot_namespace(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", tmp_path / "paper.db")

    async def scenario():
        await paper_db.init_db()

        bot = PumpFunV2PaperBot(snapshot_interval_sec=0)
        bot.state_path = tmp_path / "pumpfun_v2_state.json"
        monkeypatch.setattr(bot, "_get_sol_eur", lambda: _async_value(150.0))

        now = time.time()
        item = {
            "mint": "MINT_V2",
            "symbol": "TEST2",
            "created_ts": now - 30,
            "first_mcap": 100.0,
            "last_mcap": 106.0,
            "buys": 5,
            "sells": 1,
            "trades": 6,
            "traders": {"a", "b", "c"},
            "recent": deque([(now - 20, 102.0, "buy", "early"), (now, 106.0, "buy", "early")], maxlen=40),
            "phase": "early",
            "vsol": 30.0,
            "vtokens": 1_000_000_000.0,
        }

        await bot.consider_entry(item)
        rows = await paper_db.get_open_trades_by_prefix("PUMP2_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "PUMP2_TEST2@MINT_V2"
        assert await paper_db.get_open_trades_by_prefix("PUMP_") == []

        await bot.maybe_snapshot(force=True)
        with sqlite3.connect(paper_db.DB_PATH) as connection:
            snapshots = connection.execute(
                "SELECT bot, open_positions FROM equity_snapshots ORDER BY id"
            ).fetchall()
        assert snapshots == [("pumpfun_v2", 1)]

        await paper_db.resolve_trade(rows[0]["id"], exit_price=1.0, real_pnl=1.25)
        assert await paper_db.get_realized_pnl_by_prefix("PUMP2_") == 1.25
        assert await paper_db.get_realized_pnl_by_prefix("PUMP_") == 0.0

    asyncio.run(scenario())


async def _async_value(value):
    return value
