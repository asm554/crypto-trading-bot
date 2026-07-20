import asyncio
import time

import pytest

import polybot.paper_db as paper_db
from polybot.scout_strategy import ScoutBot, score_token


MINT = "Mint111111111111111111111111111111111111111"


def valid_token(**overrides):
    token = {
        "id": MINT, "symbol": "TEST", "usdPrice": 1.0, "liquidity": 50_000, "holderCount": 200,
        "topHoldersPercentage": 20, "developerHoldingsPercentage": 5, "organicScore": 80,
        "priceChange5m": 4, "priceChange1h": 20, "fdv": 1_000_000,
        "audit": {"mintAuthorityDisabled": True, "freezeAuthorityDisabled": True, "isSus": False},
        "stats5m": {"volumeUsd": 8_000, "organicBuyVolumeUsd": 800, "organicBuyers": 12, "traders": 60, "buys": 30, "sells": 15},
    }
    token.update(overrides)
    return token


def bind(bot, tmp_path):
    bot.state_path = tmp_path / "scout_state.json"; bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}; bot.watchlist = {}; bot.capital_remaining = bot.initial_capital_eur; bot.last_scan = 0
    return bot


def test_score_requires_every_security_and_activity_gate():
    score, reasons = score_token(valid_token())
    assert score == 100 and reasons == []
    score, reasons = score_token(valid_token(audit={"mintAuthorityDisabled": False, "freezeAuthorityDisabled": True}))
    assert score < 100 and "mint_authority" in reasons
    score, reasons = score_token(valid_token(stats5m={"volumeUsd": 8_000}))
    assert "organic_activity" in reasons


def test_scout_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        ScoutBot(paper_mode=False)


def test_matured_scored_token_opens_paper_position(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = bind(ScoutBot(api_key="test"), tmp_path)
        bot.watchlist[MINT] = {"seen_at": time.time() - 21 * 60}

        async def details(_mints): return {MINT: valid_token()}
        async def route(_mint, _rate): return True
        async def rate(): return 1.0
        monkeypatch.setattr(bot, "_tokens", details); monkeypatch.setattr(bot, "_route_ok", route); monkeypatch.setattr(bot, "_eurusd", rate)

        opened = await bot.scan_entries()
        assert opened == [{"symbol": "TEST", "mint": MINT, "score": 100}]
        assert bot.capital_remaining == pytest.approx(95)
        assert MINT in bot.portfolio

    asyncio.run(scenario())


def test_stop_loss_closes_paper_position(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        await paper_db.init_db()
        bot = bind(ScoutBot(api_key="test"), tmp_path)
        trade_id = await paper_db.log_paper_trade(f"SCOUT_TEST@{MINT}", "buy", 5, 1, 1, "paper")
        bot.capital_remaining = 95
        bot.portfolio[MINT] = {"shares": 5, "cost_basis": 5, "entry_price": 1, "entry_ts": time.time(), "peak_price": 1, "trade_id": trade_id}

        async def prices(_mints): return {MINT: {"usdPrice": .85}}
        async def rate(): return 1.0
        monkeypatch.setattr(bot, "_prices", prices); monkeypatch.setattr(bot, "_eurusd", rate)

        closed = await bot.manage_positions()
        assert closed[0]["reason"] == "stop_loss"
        assert bot.portfolio == {}
        assert bot.consecutive_losses == 1

    asyncio.run(scenario())
