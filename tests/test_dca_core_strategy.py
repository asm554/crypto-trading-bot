import asyncio

import pytest

import datetime as dt

import polybot.paper_db as paper_db
import polybot.dca_core_strategy as dca_core_strategy
from polybot.dca_core_strategy import DcaCoreBot, classify_btc_regime


def test_dca_core_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        DcaCoreBot(paper_mode=False)


def test_classify_btc_regime_bull_bear_neutral():
    rising = [100.0 + i for i in range(260)]  # close/EMA50/EMA200 all trending up
    assert classify_btc_regime(rising) == "bull"

    falling = [400.0 - i for i in range(260)]
    assert classify_btc_regime(falling) == "bear"

    assert classify_btc_regime([100.0] * 10) == "neutral"  # insufficient history
    assert classify_btc_regime([]) == "neutral"


def _ticker(btc=30000.0, eth=2000.0):
    async def fake(_pairs):
        return {
            "XXBTZEUR": {"c": [str(btc), "1"], "a": [str(btc * 1.001), "1"], "b": [str(btc * 0.999), "1"]},
            "XETHZEUR": {"c": [str(eth), "1"], "a": [str(eth * 1.001), "1"], "b": [str(eth * 0.999), "1"]},
        }
    return fake


def test_bull_regime_buys_50_50_btc_eth(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"
        bot.db_path = tmp_path / "paper_trades.db"
        bot.last_purchase_week = ""

        async def bull(_self=None):
            return "bull"

        monkeypatch.setattr(bot, "_btc_regime", bull)
        monkeypatch.setattr(bot, "_now", lambda: dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone.utc))  # a Monday
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker())

        opened = await bot.scan_entries()
        assert {o["pair"] for o in opened} == {"XBTEUR", "ETHEUR"}
        assert sum(o["amount"] for o in opened) == pytest.approx(50.0)
        assert len(bot.portfolio) == 2
        assert bot.capital_remaining == pytest.approx(450.0)

    asyncio.run(scenario())


def test_neutral_regime_does_not_buy(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"
        bot.last_purchase_week = ""

        async def neutral(_self=None):
            return "neutral"

        monkeypatch.setattr(bot, "_btc_regime", neutral)
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker())

        opened = await bot.scan_entries()
        assert opened == []
        assert bot.portfolio == {}
        assert bot.capital_remaining == pytest.approx(500.0)

    asyncio.run(scenario())


def test_second_purchase_same_week_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"

        async def bull(_self=None):
            return "bull"

        monkeypatch.setattr(bot, "_btc_regime", bull)
        monkeypatch.setattr(bot, "_now", lambda: dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone.utc))  # a Monday
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker())

        first = await bot.scan_entries()
        assert len(first) == 2
        second = await bot.scan_entries()
        assert second == []

    asyncio.run(scenario())


def test_bear_regime_liquidates_open_lots(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"
        trade_id = await paper_db.log_paper_trade("DCACORE_XBTEUR", "buy", 0.001, 25000, 0, "paper")
        bot.portfolio[str(trade_id)] = {"pair": "XBTEUR", "shares": 0.001, "cost_basis": 25.0, "trade_id": trade_id}
        bot.capital_remaining = 475.0
        bot.peak_equity = 500.0

        async def bear(_self=None):
            return "bear"

        monkeypatch.setattr(bot, "_btc_regime", bear)
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker(btc=26000.0))

        closed = await bot.manage_positions()
        assert closed[0]["reason"] == "bear_exit"
        assert bot.portfolio == {}
        assert bot.capital_remaining > 475.0  # sold at a profit, bid ~ 25974

    asyncio.run(scenario())


def test_circuit_breaker_liquidates_and_halts_permanently(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"
        trade_id = await paper_db.log_paper_trade("DCACORE_XBTEUR", "buy", 0.01, 50000, 0, "paper")
        bot.portfolio[str(trade_id)] = {"pair": "XBTEUR", "shares": 0.01, "cost_basis": 500.0, "trade_id": trade_id}
        bot.capital_remaining = 0.0
        bot.peak_equity = 500.0

        async def neutral(_self=None):
            return "neutral"

        monkeypatch.setattr(bot, "_btc_regime", neutral)
        # BTC crashes hard -> equity drops >10% from the 500 EUR peak
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker(btc=40000.0))

        closed = await bot.manage_positions()
        assert closed[0]["reason"] == "circuit_breaker"
        assert bot.halted is True
        assert bot.portfolio == {}

        # Halt is permanent: even a bull regime does not resume trading.
        async def bull(_self=None):
            return "bull"

        monkeypatch.setattr(bot, "_btc_regime", bull)
        assert await bot.manage_positions() == []
        assert await bot.scan_entries() == []

    asyncio.run(scenario())


def test_cash_reserve_is_never_invested(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = DcaCoreBot()
        bot.state_path = tmp_path / "dca_core_state.json"
        bot.capital_remaining = 60.0  # only 10 EUR above the 50 EUR reserve

        async def bull(_self=None):
            return "bull"

        monkeypatch.setattr(bot, "_btc_regime", bull)
        monkeypatch.setattr(bot, "_now", lambda: dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone.utc))  # a Monday
        monkeypatch.setattr(dca_core_strategy, "fetch_ticker_data", _ticker())

        opened = await bot.scan_entries()
        assert sum(o["amount"] for o in opened) == pytest.approx(10.0)
        assert bot.capital_remaining == pytest.approx(50.0)

    asyncio.run(scenario())
