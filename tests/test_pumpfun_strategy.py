import asyncio

import pytest

import polybot.paper_db as paper_db
import polybot.pumpfun_strategy as pumpfun
from polybot.pumpfun_strategy import PumpFunPaperBot


def test_pumpfun_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        PumpFunPaperBot(paper_mode=False)


def test_pumpfun_opens_only_after_pullback_and_reclaim(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_ticker(_pairs):
        return {"ZEURZUSD": {"c": ["1.0", "1.0"]}}

    monkeypatch.setattr(pumpfun, "fetch_ticker_data", fake_ticker)

    async def scenario():
        await paper_db.init_db()
        bot = PumpFunPaperBot(
            initial_capital_eur=100,
            min_age_sec=0,
            max_age_sec=3600,
            min_market_cap_sol=10,
            max_market_cap_sol=100,
            min_change_pct=5,
            max_change_pct=30,
            min_trades=6,
            min_buy_sell_ratio=1.2,
            paper_mode=True,
        )
        bot.state_path = tmp_path / "pumpfun_state.json"
        now = pumpfun.time.time()
        item = {"mint": "MINT1", "symbol": "TEST", "created_ts": now - 120,
                "first_mcap": 20.0, "peak_mcap": 26.0, "last_mcap": 22.0,
                "buys": 6, "sells": 0, "trades": 6, "traders": {f"T{i}" for i in range(12)},
                "recent": pumpfun.deque([
                    (now - 25, 20.0, "buy", pumpfun.PHASE_EARLY),
                    (now - 20, 20.5, "buy", pumpfun.PHASE_EARLY),
                    (now - 15, 21.0, "buy", pumpfun.PHASE_EARLY),
                    (now - 10, 21.3, "buy", pumpfun.PHASE_EARLY),
                    (now - 5, 21.7, "buy", pumpfun.PHASE_EARLY),
                    (now, 22.0, "buy", pumpfun.PHASE_EARLY),
                ], maxlen=200),
                "phase": pumpfun.PHASE_EARLY, "vsol": 30.0, "vtokens": 1_000_000_000.0}
        opened = await bot.consider_entry(item)
        assert opened is not None
        assert "MINT1" in bot.portfolio
        assert bot.portfolio["MINT1"]["entry_price"] > 0
        rows = await paper_db.get_open_trades_by_prefix("PUMP_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "PUMP_TEST@MINT1"

    asyncio.run(scenario())


def test_pumpfun_rejects_straight_breakout_without_pullback(monkeypatch, tmp_path):
    async def scenario():
        bot = PumpFunPaperBot(
            min_age_sec=0,
            min_trades=6,
            min_unique_traders=6,
        )
        bot.state_path = tmp_path / "pumpfun_state.json"
        now = pumpfun.time.time()
        item = {
            "mint": "MINT2",
            "symbol": "CHASE",
            "created_ts": now - 300,
            "first_mcap": 20.0,
            "peak_mcap": 24.0,
            "last_mcap": 24.0,
            "buys": 6,
            "sells": 0,
            "trades": 6,
            "traders": {f"T{i}" for i in range(6)},
            "recent": pumpfun.deque(
                [
                    (now - 25 + idx * 5, 22.0 + idx * 0.4, "buy", pumpfun.PHASE_EARLY)
                    for idx in range(6)
                ],
                maxlen=200,
            ),
            "phase": pumpfun.PHASE_EARLY,
            "vsol": 30.0,
            "vtokens": 1_000_000_000.0,
        }

        assert await bot.consider_entry(item) is None
        assert bot.portfolio == {}

    asyncio.run(scenario())
