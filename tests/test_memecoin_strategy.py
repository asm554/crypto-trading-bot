import asyncio

import pytest

import polybot.memecoin_strategy as memecoin_strategy
import polybot.paper_db as paper_db
from polybot.memecoin_strategy import MemecoinMomentumBot


def _pair(symbol="BONK", price_usd="1.00", liquidity_usd=100_000.0, volume_h24=500_000.0, buys=20, sells=10, created_ms=0):
    """DexScreener-Pair mit Defaults, die alle Gates passieren — Tests
    überschreiben gezielt nur das Feld, das sie prüfen wollen."""
    return {
        "chainId": "solana",
        "baseToken": {"symbol": symbol},
        "priceUsd": price_usd,
        "liquidity": {"usd": liquidity_usd},
        "volume": {"h24": volume_h24},
        "txns": {"h1": {"buys": buys, "sells": sells}},
        "pairCreatedAt": created_ms,
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


class _FakeGetCtx:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def json(self):
        return self._payload


class _RaisingGetCtx:
    async def __aenter__(self):
        raise RuntimeError("network down")

    async def __aexit__(self, *_a):
        return False


class _FakeSession:
    """Minimaler aiohttp.ClientSession-Ersatz für discover_dynamic_solana_tokens-Tests."""

    def __init__(self, responses, fail_urls=()):
        self.responses = responses
        self.fail_urls = set(fail_urls)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def get(self, url, timeout=None):
        if url in self.fail_urls:
            return _RaisingGetCtx()
        return _FakeGetCtx(self.responses.get(url, []))


def test_memecoin_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        MemecoinMomentumBot(paper_mode=False)


def test_momentum_change_pct_requires_half_window_of_history():
    bot = MemecoinMomentumBot.__new__(MemecoinMomentumBot)
    bot.momentum_lookback_min = 60.0
    bot.price_history = {}
    # Only 20 minutes of history for a 60min window -> not enough yet.
    bot._update_history("ADDR_BONK", 0.0, 1.0)
    bot._update_history("ADDR_BONK", 1200.0, 1.1)
    assert bot._momentum_change_pct("ADDR_BONK") is None
    # Now span covers >= 30min (half of the 60min window).
    bot._update_history("ADDR_BONK", 30 * 60.0 + 1, 1.2)
    assert bot._momentum_change_pct("ADDR_BONK") == pytest.approx(20.0)  # (1.2-1.0)/1.0*100


def test_scan_entries_opens_position_on_momentum(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                position_eur=8.0,
                momentum_lookback_min=60.0,
                entry_change_pct=8.0,
                entry_max_change_pct=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        # Seed enough history (span >= 30min) showing a +20% move -> within [8, 60] band.
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20")}  # +20% since oldest sample

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert opened[0]["address"] == "ADDR_BONK"
        assert "ADDR_BONK" in bot.portfolio
        # Kauf-Slippage macht den Fill teurer als den Quote-Preis (1.20 * 1.015).
        assert bot.portfolio["ADDR_BONK"]["entry_price"] == pytest.approx(1.20 * 1.015)
        assert bot.capital_remaining == pytest.approx(92.0)

        rows = await paper_db.get_open_trades_by_prefix("CHAIN_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "CHAIN_BONK@ADDR_BONK"

    asyncio.run(scenario())


def test_scan_entries_skips_momentum_outside_band(monkeypatch, tmp_path):
    """Zu schwach (kein Momentum) UND zu stark (schon ausgelaufener Pump) werden übersprungen."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                momentum_lookback_min=60.0,
                entry_change_pct=8.0,
                entry_max_change_pct=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK", "ADDR_WIF"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0]]  # +2% -> below entry_change_pct
        bot.price_history["ADDR_WIF"] = [[0.0, 1.0]]  # +90% -> above entry_max_change_pct

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                "ADDR_BONK": _pair("BONK", price_usd="1.02"),
                "ADDR_WIF": _pair("WIF", price_usd="1.90"),
            }

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

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
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                min_liquidity_usd=50_000.0,
                momentum_lookback_min=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", liquidity_usd=1000.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_below_min_volume(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                min_volume_usd=250_000.0,
                momentum_lookback_min=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", volume_h24=10_000.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_low_buy_sell_ratio(monkeypatch, tmp_path):
    """Ein Anstieg, der schon ins Verkaufen kippt (mehr Sells als Buys), wird nicht gekauft."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                min_buy_sell_ratio=1.2,
                momentum_lookback_min=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", buys=10, sells=15)}  # ratio 0.67 < 1.2

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_dynamic_token_too_young_but_allows_curated(monkeypatch, tmp_path):
    """Mindestalter gilt nur für dynamisch entdeckte Tokens, nicht für den kuratierten Kern."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                momentum_lookback_min=60.0,
                min_pair_age_hours=6.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]
        bot.price_history["ADDR_NEW"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_discover(_max_tokens):
            return ["ADDR_NEW"]

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                # Kuratiert, pairCreatedAt=0 (unbekannt) -> Alters-Gate gilt hier nicht.
                "ADDR_BONK": _pair("BONK", price_usd="1.20", created_ms=0),
                # Dynamisch, erst vor 1h erstellt -> jünger als min_pair_age_hours=6h.
                "ADDR_NEW": _pair("NEWCOIN", price_usd="1.20", created_ms=(now - 3600) * 1000),
            }

        monkeypatch.setattr(memecoin_strategy, "discover_dynamic_solana_tokens", fake_discover)
        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["address"] == "ADDR_BONK"

    asyncio.run(scenario())


def test_scan_entries_allows_dynamic_token_old_enough(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                momentum_lookback_min=60.0,
                min_pair_age_hours=6.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=[],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 100 * 3600.0  # weit genug von der Epoche weg, damit "10h alt" nicht negativ wird
        bot.price_history["ADDR_NEW"] = [[now - 3600, 1.0], [now - 30 * 60 - 1, 1.05]]

        async def fake_discover(_max_tokens):
            return ["ADDR_NEW"]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_NEW": _pair("NEWCOIN", price_usd="1.20", created_ms=(now - 10 * 3600) * 1000)}  # 10h alt

        monkeypatch.setattr(memecoin_strategy, "discover_dynamic_solana_tokens", fake_discover)
        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["address"] == "ADDR_NEW"
        assert opened[0]["symbol"] == "NEWCOIN"

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
            MemecoinMomentumBot(initial_capital_eur=100.0, take_profit_pct=15.0, stop_loss_pct=10.0, slippage_pct=1.5, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20")}  # +20% > take-profit 15%

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "take_profit"
        assert resolved[0]["symbol"] == "BONK"
        assert bot.portfolio == {}
        # exit_price = 1.20 * (1 - 0.015) = 1.182; pnl = 8*1.182 - 8*1.0
        assert resolved[0]["pnl"] == pytest.approx(8 * 1.20 * 0.985 - 8.0)
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
            MemecoinMomentumBot(initial_capital_eur=100.0, take_profit_pct=15.0, stop_loss_pct=10.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="0.85")}  # -15% < -stop_loss 10%

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

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
            MemecoinMomentumBot(initial_capital_eur=100.0, take_profit_pct=15.0, stop_loss_pct=10.0, max_hold_sec=3600, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        # entry_ts far in the past, price flat (no TP/SL trigger) -> must exit on time.
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.02")}  # +2%, no TP/SL trigger

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

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
            MemecoinMomentumBot(
                initial_capital_eur=100.0,
                position_eur=8.0,
                max_open_positions=1,
                momentum_lookback_min=60.0,
                entry_change_pct=8.0,
                entry_max_change_pct=60.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK", "ADDR_WIF"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0
        bot.price_history["ADDR_BONK"] = [[0.0, 1.0], [now - 30 * 60 - 1, 1.05]]
        bot.price_history["ADDR_WIF"] = [[0.0, 2.0], [now - 30 * 60 - 1, 2.1]]

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                # Beide im Band [8, 60]: BONK +30% mit hohem Volumen, WIF +25% mit
                # niedrigerem Volumen -> volumen-gewichteter Score bevorzugt BONK.
                "ADDR_BONK": _pair("BONK", price_usd="1.30", volume_h24=1_000_000.0),
                "ADDR_WIF": _pair("WIF", price_usd="2.50", volume_h24=300_000.0),
            }

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        # Nur eine Position erlaubt -> die mit dem höheren Score gewinnt (BONK).
        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert len(bot.portfolio) == 1

    asyncio.run(scenario())


def test_rebuild_state_parses_symbol_and_address_from_market_question(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")

        bot = MemecoinMomentumBot.__new__(MemecoinMomentumBot)
        bot.initial_capital_eur = 100.0
        bot.db_path = db_path
        bot._rebuild_state_from_db()

        assert bot.trade_count == 1
        assert "ADDR_BONK" in bot.portfolio
        assert bot.portfolio["ADDR_BONK"]["symbol"] == "BONK"
        assert bot.portfolio["ADDR_BONK"]["shares"] == pytest.approx(8.0)

    asyncio.run(scenario())


def test_discover_dynamic_solana_tokens_filters_dedupes_and_caps(monkeypatch):
    responses = {
        memecoin_strategy.DEXSCREENER_BOOSTS_TOP_URL: [
            {"chainId": "solana", "tokenAddress": "ADDR_A"},
            {"chainId": "ethereum", "tokenAddress": "ADDR_ETH"},
            {"chainId": "solana", "tokenAddress": "ADDR_B"},
        ],
        memecoin_strategy.DEXSCREENER_BOOSTS_LATEST_URL: [
            {"chainId": "solana", "tokenAddress": "ADDR_A"},  # Duplikat -> dedupe
            {"chainId": "solana", "tokenAddress": "ADDR_C"},
        ],
        memecoin_strategy.DEXSCREENER_PROFILES_LATEST_URL: [
            {"chainId": "solana", "tokenAddress": "ADDR_D"},
        ],
    }
    session = _FakeSession(responses)
    monkeypatch.setattr(memecoin_strategy.aiohttp, "ClientSession", lambda *a, **k: session)

    result = asyncio.run(memecoin_strategy.discover_dynamic_solana_tokens(max_tokens=3))

    assert result == ["ADDR_A", "ADDR_B", "ADDR_C"]


def test_discover_dynamic_solana_tokens_survives_one_feed_failing(monkeypatch):
    responses = {
        memecoin_strategy.DEXSCREENER_BOOSTS_LATEST_URL: [{"chainId": "solana", "tokenAddress": "ADDR_X"}],
        memecoin_strategy.DEXSCREENER_PROFILES_LATEST_URL: [{"chainId": "solana", "tokenAddress": "ADDR_Y"}],
    }
    session = _FakeSession(responses, fail_urls={memecoin_strategy.DEXSCREENER_BOOSTS_TOP_URL})
    monkeypatch.setattr(memecoin_strategy.aiohttp, "ClientSession", lambda *a, **k: session)

    result = asyncio.run(memecoin_strategy.discover_dynamic_solana_tokens(max_tokens=10))

    assert result == ["ADDR_X", "ADDR_Y"]
