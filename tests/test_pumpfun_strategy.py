import asyncio

import pytest

import polybot.paper_db as paper_db
import polybot.pumpfun_strategy as pumpfun
from polybot.pumpfun_strategy import PumpFunPaperBot


def test_pumpfun_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        PumpFunPaperBot(paper_mode=False)


def test_pumpfun_opens_only_after_pressure_and_momentum(monkeypatch, tmp_path):
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
            min_trades=5,
            min_buy_sell_ratio=1.2,
            paper_mode=True,
        )
        bot.state_path = tmp_path / "pumpfun_state.json"
        now = pumpfun.time.time()
        item = {"mint": "MINT1", "symbol": "TEST", "created_ts": now - 120,
                "first_mcap": 20.0, "peak_mcap": 22.0, "last_mcap": 22.0,
                "buys": 5, "sells": 0, "trades": 5, "traders": {f"T{i}" for i in range(8)},
                "recent": pumpfun.deque([(now - 20, 20.0, "buy", pumpfun.PHASE_EARLY), (now, 22.0, "buy", pumpfun.PHASE_EARLY)], maxlen=40),
                "phase": pumpfun.PHASE_EARLY, "vsol": 30.0, "vtokens": 1_000_000_000.0}
        opened = await bot.consider_entry(item)
        assert opened is not None
        assert "MINT1" in bot.portfolio
        assert bot.portfolio["MINT1"]["entry_price"] > 0
        rows = await paper_db.get_open_trades_by_prefix("PUMP_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "PUMP_TEST@MINT1"

    asyncio.run(scenario())
