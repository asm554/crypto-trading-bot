import asyncio
import sqlite3
import time
from collections import deque

import pytest

import polybot.paper_db as paper_db
import polybot.pumpfun_strategy as pumpfun
from polybot.pumpfun_strategy import PumpFunPaperBot, pumpswap_fee_pct


def test_pumpfun_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        PumpFunPaperBot(paper_mode=False)


def test_current_pumpfun_fee_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    assert PumpFunPaperBot().platform_fee_pct == pytest.approx(1.25)
    assert pumpswap_fee_pct(100) == pytest.approx(1.25)
    assert pumpswap_fee_pct(420) == pytest.approx(1.20)
    assert pumpswap_fee_pct(10_000) == pytest.approx(0.95)
    assert pumpswap_fee_pct(98_240) == pytest.approx(0.30)


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


def test_migrated_entry_preserves_ledger_cost_basis(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        bot = PumpFunPaperBot(
            initial_capital_eur=100,
            position_eur=5,
            min_age_sec=0,
            min_market_cap_sol=1,
            migrated_max_market_cap_sol=1_000,
            migrated_min_change_pct=1,
            migrated_max_change_pct=50,
            migrated_min_trades=1,
            min_unique_traders=1,
            min_buy_sell_ratio=1,
            min_recent_change_pct=0,
            # Seit v3-pullback-reclaim sind Migrated-Entries per Default aus.
            # Hier geht es um die Ledger-Kostenbasis eines solchen Einstiegs,
            # nicht um das Gate — also gezielt wieder einschalten.
            allow_migrated_entries=True,
        )
        bot.state_path = tmp_path / "pumpfun_state.json"
        now = time.time()
        # Peak 130 -> Rücksetzer auf 110 erfüllt die v3-Pullback-Bedingungen;
        # first/last bleiben so, dass change_pct weiterhin 10 % ergibt und die
        # Edge-Zusicherung unten unverändert gilt.
        item = {
            "mint": "MIGRATED",
            "symbol": "MIG",
            "created_ts": now - 60,
            "first_mcap": 100,
            "peak_mcap": 130,
            "last_mcap": 110,
            "buys": 6,
            "sells": 1,
            "trades": 6,
            "traders": {f"T{i}" for i in range(6)},
            "recent": deque(
                [(now - 25 + idx * 5, 108 + idx * 0.4, "buy", "migrated") for idx in range(6)],
                maxlen=40,
            ),
            "phase": "migrated",
        }
        assert await bot.consider_entry(item)

    asyncio.run(scenario())
    with sqlite3.connect(db_path) as connection:
        size, price, edge = connection.execute(
            "SELECT size, price, edge_percent FROM paper_trades WHERE market_question='PUMP_MIG@MIGRATED'"
        ).fetchone()
    assert size * price == pytest.approx(5.0)
    assert edge == pytest.approx(10.0)


def test_migrated_mark_charges_entry_and_exit_fee(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def scenario():
        bot = PumpFunPaperBot(migrated_slippage_pct=0)
        fee_pct = pumpswap_fee_pct(1_000)
        pos = {
            "phase": "migrated",
            "cost_basis": 100,
            "entry_mcap": 1_000,
            "entry_fee_pct": fee_pct,
            "entry_value_factor": 1 - fee_pct / 100,
        }
        value = await bot._mark({"last_mcap": 1_000}, pos)
        assert value == pytest.approx(100 * (1 - fee_pct / 100) ** 2)

    asyncio.run(scenario())


def test_metered_pumpportal_data_fee_reduces_cash_and_realized_pnl(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        bot = PumpFunPaperBot(initial_capital_eur=100)
        bot.state_path = tmp_path / "pumpfun_state.json"
        bot.metered_trade_events = 9_999

        async def sol_eur():
            return 100.0

        monkeypatch.setattr(bot, "_get_sol_eur", sol_eur)
        await bot._charge_metered_data_if_due({"txType": "buy"})
        assert bot.capital_remaining == pytest.approx(99.0)
        assert bot.data_fees_eur == pytest.approx(1.0)
        assert bot.metered_batches_billed == 1
        assert await paper_db.get_realized_pnl_by_prefix("PUMP_") == pytest.approx(-1.0)

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
