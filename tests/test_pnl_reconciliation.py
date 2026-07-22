import asyncio
import sqlite3

import pytest

import polybot.daytrade_strategy as daytrade_strategy
import polybot.meanrev_strategy as meanrev_strategy
import polybot.momentum_strategy as momentum_strategy
import polybot.paper_db as paper_db
from polybot.daytrade_strategy import DaytradeBot
from polybot.meanrev_strategy import MeanRevBot
from polybot.momentum_strategy import MomentumBot


def _ticker():
    return {
        "o": "100",
        "c": ["101", "1"],
        "h": ["102", "103"],
        "l": ["98", "97"],
        "v": ["1000", "2000"],
        "p": ["100", "100"],
        "b": ["100.5", "1", "1"],
        "a": ["101.5", "1", "1"],
    }


@pytest.mark.parametrize(
    ("strategy_module", "bot_class", "prefix", "position_eur"),
    [
        (momentum_strategy, MomentumBot, "MOM_", 12.0),
        (daytrade_strategy, DaytradeBot, "DAY_", 10.0),
        (meanrev_strategy, MeanRevBot, "REV_", 15.0),
    ],
)
def test_open_position_equity_reconciles_and_persists_pnl(
    monkeypatch,
    tmp_path,
    strategy_module,
    bot_class,
    prefix,
    position_eur,
):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _ticker()}

    monkeypatch.setattr(strategy_module, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = bot_class(initial_capital_eur=100.0, position_eur=position_eur, paper_mode=True)
        trade_id = await paper_db.log_paper_trade(
            f"{prefix}SOLEUR",
            "buy",
            position_eur / 100.0,
            100.0,
            0.1,
            "paper",
        )
        bot.capital_remaining = 100.0 - position_eur
        bot.portfolio = {
            "SOLEUR": {
                "shares": position_eur / 100.0,
                "cost_basis": position_eur,
                "entry_price": 100.0,
                "entry_ts": 0.0,
                "trade_id": trade_id,
            }
        }

        equity = await bot.equity()

        assert equity["equity_eur"] - 100.0 == pytest.approx(
            equity["realized_pnl_eur"] + equity["unrealized_pnl_eur"]
        )
        with sqlite3.connect(db_path) as connection:
            stored = connection.execute(
                "SELECT unrealized_pnl FROM paper_trades WHERE id=?",
                (trade_id,),
            ).fetchone()[0]
        assert stored == pytest.approx(equity["unrealized_pnl_eur"])

    asyncio.run(scenario())


def test_resolving_trade_clears_stale_unrealized_pnl(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        trade_id = await paper_db.log_paper_trade("DCA_SOLEUR", "buy", 0.1, 100.0, 0.1, "paper")
        await paper_db.update_unrealized_pnls({trade_id: 1.25})
        await paper_db.resolve_trade(trade_id, 110.0, 0.9)
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT real_pnl, unrealized_pnl FROM paper_trades WHERE id=?",
                (trade_id,),
            ).fetchone()
        assert row[0] == pytest.approx(0.9)
        assert row[1] is None

    asyncio.run(scenario())
