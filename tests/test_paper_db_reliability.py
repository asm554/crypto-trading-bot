import asyncio
import sqlite3

from polybot import paper_db


def test_resolve_trade_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "paper.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        trade_id = await paper_db.log_paper_trade("MOM_SOLEUR", "buy", 1, 100, 0, "paper")
        assert await paper_db.resolve_trade(trade_id, 110, 9.0) is True
        assert await paper_db.resolve_trade(trade_id, 50, -51.0) is False
        return trade_id

    trade_id = asyncio.run(scenario())
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT exit_price, real_pnl FROM paper_trades WHERE id=?",
            (trade_id,),
        ).fetchone()
    assert row == (110.0, 9.0)


def test_open_trade_ids_use_literal_prefix(monkeypatch, tmp_path):
    db_path = tmp_path / "paper.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        expected = await paper_db.log_paper_trade("PUMP2_A@MINT", "buy", 1, 1, 0, "paper")
        await paper_db.log_paper_trade("PUMPX_A@MINT", "buy", 1, 1, 0, "paper")
        return expected

    expected = asyncio.run(scenario())
    assert paper_db.get_open_trade_ids_by_prefix_sync("PUMP2_") == {expected}


def test_runtime_start_and_stop_are_cloud_syncable_snapshots(monkeypatch, tmp_path):
    db_path = tmp_path / "paper.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        await paper_db.mark_bot_started("dca", started_at=1000)
        await paper_db.mark_bot_stopped("dca")

    asyncio.run(scenario())
    with sqlite3.connect(db_path) as connection:
        events = connection.execute(
            "SELECT bot FROM equity_snapshots WHERE bot LIKE '__runtime%' ORDER BY id"
        ).fetchall()
        status = connection.execute(
            "SELECT status FROM bot_status WHERE bot='dca'"
        ).fetchone()

    assert events == [("__runtime_dca",), ("__runtime_stopped_dca",)]
    assert status == ("stopped",)
