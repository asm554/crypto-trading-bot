import asyncio

import pytest

import polybot.memecoin_strategy as memecoin_strategy
import polybot.paper_db as paper_db
from polybot.memecoin_strategy import MemecoinBreakoutBot


def _pair(symbol="BONK", price_usd="0.00002000", liquidity_usd=100_000.0):
    return {
        "chainId": "solana",
        "baseToken": {"symbol": symbol},
        "priceUsd": price_usd,
        "liquidity": {"usd": liquidity_usd},
    }


def _eur_usd_ticker(rate="1.10"):
    return {"ZEURZUSD": {"c": [rate, "1.0"]}}


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "memecoin_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.price_history = {}
    bot.cooldowns = {}
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_count = 0
    return bot


def test_memecoin_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        MemecoinBreakoutBot(paper_mode=False)


def test_rolling_high_requires_half_window_of_history():
    bot = MemecoinBreakoutBot.__new__(MemecoinBreakoutBot)
    bot.lookback_hours = 6.0
    bot.price_history = {}
    # Only 20 minutes of history for a 6h window -> not enough yet.
    bot._update_history("BONK", 0.0, 1.0)
    bot._update_history("BONK", 1200.0, 1.1)
    assert bot._rolling_high("BONK") is None
    # Now span covers >= 3h (half of 6h window).
    bot._update_history("BONK", 3 * 3600.0 + 1, 1.2)
    assert bot._rolling_high("BONK") == pytest.approx(1.1)


def test_scan_entries_opens_position_on_breakout(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, position_eur=8.0, lookback_hours=6.0, paper_mode=True),
            tmp_path,
        )
        bot.symbols = ["BONK"]
        # Seed enough history for a valid rolling high (span >= 3h), max = 1.0
        now = 4 * 3600.0
        bot.price_history["BONK"] = [[0.0, 1.0], [now - 3 * 3600 - 1, 0.9]]

        async def fake_fetch_meme_pairs(_symbols):
            return {"BONK": _pair("BONK", price_usd="1.02")}  # breaks above 1.0 by >0.5%

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert "BONK" in bot.portfolio
        # Kauf-Slippage macht den Fill teurer als den Quote-Preis (1.02 * 1.015).
        assert bot.portfolio["BONK"]["entry_price"] == pytest.approx(1.02 * 1.015)
        assert bot.capital_remaining == pytest.approx(92.0)

        rows = await paper_db.get_open_trades_by_prefix("CHAIN_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "CHAIN_BONK"

    asyncio.run(scenario())


def test_scan_entries_skips_below_min_liquidity(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, min_liquidity_usd=50_000.0, lookback_hours=6.0, paper_mode=True),
            tmp_path,
        )
        bot.symbols = ["BONK"]
        now = 4 * 3600.0
        bot.price_history["BONK"] = [[0.0, 1.0], [now - 3 * 3600 - 1, 0.9]]

        async def fake_fetch_meme_pairs(_symbols):
            return {"BONK": _pair("BONK", price_usd="1.02", liquidity_usd=1000.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_exits_via_take_profit(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, take_profit_pct=20.0, stop_loss_pct=10.0, slippage_pct=1.5, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"BONK": {"shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_meme_pairs(_symbols):
            return {"BONK": _pair("BONK", price_usd="1.25")}  # +25% > take-profit 20%

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "take_profit"
        assert bot.portfolio == {}
        # exit_price = 1.25 * (1 - 0.015) = 1.23125; pnl = 8*1.23125 - 8*1.0 = 1.85
        assert resolved[0]["pnl"] == pytest.approx(8 * 1.25 * 0.985 - 8.0)
        assert bot.capital_remaining > 92.0

        rows = await paper_db.get_open_trades_by_prefix("CHAIN_")
        assert rows == []

    asyncio.run(scenario())


def test_manage_positions_exits_via_stop_loss(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, take_profit_pct=20.0, stop_loss_pct=10.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"BONK": {"shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_meme_pairs(_symbols):
            return {"BONK": _pair("BONK", price_usd="0.85")}  # -15% < -stop_loss 10%

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "stop_loss"
        assert resolved[0]["pnl"] < 0
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_exits_via_max_hold(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, take_profit_pct=20.0, stop_loss_pct=10.0, max_hold_sec=3600, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        # entry_ts far in the past, price flat (no TP/SL trigger) -> must exit on time.
        bot.portfolio = {"BONK": {"shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_meme_pairs(_symbols):
            return {"BONK": _pair("BONK", price_usd="1.02")}  # +2%, no TP/SL trigger

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "time_exit"
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_respects_max_open_positions(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinBreakoutBot(initial_capital_eur=100.0, position_eur=8.0, max_open_positions=1, lookback_hours=6.0, paper_mode=True),
            tmp_path,
        )
        bot.symbols = ["BONK", "WIF"]
        now = 4 * 3600.0
        bot.price_history["BONK"] = [[0.0, 1.0], [now - 3 * 3600 - 1, 0.9]]
        bot.price_history["WIF"] = [[0.0, 2.0], [now - 3 * 3600 - 1, 1.8]]

        async def fake_fetch_meme_pairs(_symbols):
            return {
                "BONK": _pair("BONK", price_usd="1.02", liquidity_usd=200_000.0),
                "WIF": _pair("WIF", price_usd="2.05", liquidity_usd=100_000.0),
            }

        monkeypatch.setattr(memecoin_strategy, "fetch_meme_pairs", fake_fetch_meme_pairs)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        # Nur eine Position erlaubt -> die mit der höheren Liquidität gewinnt (BONK).
        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert len(bot.portfolio) == 1

    asyncio.run(scenario())
