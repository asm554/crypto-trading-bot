import asyncio
import time

import pytest

import polybot.paper_db as paper_db
import polybot.hodl_strategy as hodl_strategy
from polybot.hodl_strategy import HodlBot


def test_hodler_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        HodlBot(paper_mode=False)


def test_market_phase_identifies_bear_and_overheating():
    assert HodlBot._phase({"close": 90, "ema50": 100, "ema200": 100, "momentum": -5}) == "bear"
    assert HodlBot._phase({"close": 130, "ema50": 100, "ema200": 90, "momentum": 51}) == "overheated"


def test_bear_market_buys_only_reduced_btc(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db(); bot = HodlBot()
        bot.state_path = tmp_path / "hodl_state.json"; bot.db_path = tmp_path / "paper_trades.db"; bot.last_daily_scan = ""
        bear = {"close": 90, "ema50": 100, "ema200": 100, "momentum": -5}
        async def market(_pair): return bear
        async def ticker(_pairs): return {"XXBTZEUR": {"c": ["100", "1"], "a": ["101", "1"], "b": ["99", "1"]}}
        monkeypatch.setattr(bot, "_market", market); monkeypatch.setattr(hodl_strategy, "fetch_ticker_data", ticker)
        opened = await bot.scan_entries()
        assert opened[0]["pair"] == "XBTEUR"
        assert opened[0]["amount"] == pytest.approx(7.0)
        assert len(bot.portfolio) == 3

    asyncio.run(scenario())


def test_profit_tranche_resolves_at_100_percent(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db(); bot = HodlBot(); bot.state_path = tmp_path / "hodl_state.json"
        trade_id = await paper_db.log_paper_trade("HODL_XBTEUR_profit100", "buy", 1, 100, 0, "paper")
        bot.capital_remaining = 0; bot.portfolio[str(trade_id)] = {"pair": "XBTEUR", "shares": 1, "cost_basis": 100, "trade_id": trade_id, "stage": "profit100"}
        async def ticker(_pairs): return {"XXBTZEUR": {"c": ["205", "1"], "a": ["206", "1"], "b": ["205", "1"]}}
        monkeypatch.setattr(hodl_strategy, "fetch_ticker_data", ticker)
        closed = await bot.manage_positions()
        assert closed[0]["reason"] == "profit_100"
        assert bot.portfolio == {}

    asyncio.run(scenario())
