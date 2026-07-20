import asyncio
import time

import pytest

import polybot.memecoin_strategy as memecoin_strategy
import polybot.paper_db as paper_db
from polybot.memecoin_strategy import MemecoinMomentumBot


def _pair(
    symbol="BONK",
    price_usd="1.00",
    liquidity_usd=100_000.0,
    volume_h24=500_000.0,
    volume_h1=50_000.0,
    buys=35,
    sells=20,
    created_ms=0,
    change_h1=10.0,
    change_m5=0.5,
    change_h6=10.0,
):
    """DexScreener-Pair mit Defaults, die alle Gates passieren (kuratiert UND
    dynamisch) — Tests überschreiben gezielt nur das Feld, das sie prüfen
    wollen. buys/sells summieren auf 55 (>= min_h1_txns=50) bei Ratio 1.75."""
    return {
        "chainId": "solana",
        "baseToken": {"symbol": symbol},
        "priceUsd": price_usd,
        "liquidity": {"usd": liquidity_usd},
        "volume": {"h24": volume_h24, "h1": volume_h1},
        "txns": {"h1": {"buys": buys, "sells": sells}},
        "priceChange": {"h1": change_h1, "m5": change_m5, "h6": change_h6},
        "pairCreatedAt": created_ms,
    }


def _eur_usd_ticker(rate="1.10"):
    return {"ZEURZUSD": {"c": [rate, "1.0"]}}


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "memecoin_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
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
                entry_change_pct=8.0,
                entry_max_change_pct=35.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", change_h1=20.0)}  # innerhalb [8, 35]

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert opened[0]["address"] == "ADDR_BONK"
        assert "ADDR_BONK" in bot.portfolio
        # Kauf-Slippage plus DEX-Gebühr machen den Fill teurer als den Quote-Preis (1.20 * 1.015 * 1.01).
        assert bot.portfolio["ADDR_BONK"]["entry_price"] == pytest.approx(1.20 * 1.015 * 1.01)
        assert bot.portfolio["ADDR_BONK"]["peak_price"] == pytest.approx(1.20 * 1.015 * 1.01)
        assert bot.portfolio["ADDR_BONK"]["trailing_active"] is False
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
                entry_change_pct=8.0,
                entry_max_change_pct=35.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK", "ADDR_WIF"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                "ADDR_BONK": _pair("BONK", price_usd="1.02", change_h1=2.0),   # < entry_change_pct
                "ADDR_WIF": _pair("WIF", price_usd="1.90", change_h1=50.0),    # > entry_max_change_pct
            }

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_when_m5_momentum_not_positive(monkeypatch, tmp_path):
    """h1-Momentum passt, aber die letzten 5 Minuten sind schon negativ – Pump läuft aus."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(initial_capital_eur=100.0, dynamic_enabled=False, curated_addresses=["ADDR_BONK"], paper_mode=True),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.15", change_h1=15.0, change_m5=-0.8)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_h6_blowoff(monkeypatch, tmp_path):
    """h1-Momentum passt, aber die Bewegung über 6h ist schon ein Tages-Blowoff."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(initial_capital_eur=100.0, max_h6_change_pct=100.0, dynamic_enabled=False, curated_addresses=["ADDR_BONK"], paper_mode=True),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.15", change_h1=15.0, change_h6=180.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_insufficient_h1_activity(monkeypatch, tmp_path):
    """Kaufdruck-Ratio wäre gut, aber zu wenige Transaktionen für ein belastbares Signal."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(initial_capital_eur=100.0, min_h1_txns=50, dynamic_enabled=False, curated_addresses=["ADDR_BONK"], paper_mode=True),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.15", change_h1=15.0, buys=5, sells=3)}  # ratio 1.67, aber nur 8 Txns

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

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
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", liquidity_usd=1000.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

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
                min_volume_usd=100_000.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", volume_h24=10_000.0)}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

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
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20", buys=20, sells=35)}  # ratio 0.57 < 1.2, 55 Txns

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_dynamic_has_stricter_liquidity_and_volume_than_curated(monkeypatch, tmp_path):
    """Dieselben (mittelmäßigen) Zahlen reichen für den kuratierten Kern, nicht aber für dynamisch entdeckte Tokens."""
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
                min_liquidity_dynamic_usd=100_000.0,
                min_volume_usd=100_000.0,
                min_volume_dynamic_usd=500_000.0,
                min_pair_age_hours=24.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 100 * 3600.0

        async def fake_discover(_max_tokens):
            return ["ADDR_NEW"]

        async def fake_fetch_pairs_by_address(_addresses):
            # Liquidität 60k$ / Volumen 150k$: passiert die kuratierten Gates (50k/100k),
            # scheitert aber an den strengeren dynamischen Gates (100k/500k).
            mid_tier = dict(liquidity_usd=60_000.0, volume_h24=150_000.0)
            return {
                "ADDR_BONK": _pair("BONK", price_usd="1.15", change_h1=15.0, **mid_tier),
                "ADDR_NEW": _pair("NEWCOIN", price_usd="1.15", change_h1=15.0, created_ms=(now - 30 * 3600) * 1000, **mid_tier),
            }

        monkeypatch.setattr(memecoin_strategy, "discover_dynamic_solana_tokens", fake_discover)
        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["address"] == "ADDR_BONK"

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
                min_pair_age_hours=6.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=["ADDR_BONK"],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 60 * 60.0

        async def fake_discover(_max_tokens):
            return ["ADDR_NEW"]

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                # Kuratiert, pairCreatedAt=0 (unbekannt) -> Alters-Gate gilt hier nicht.
                "ADDR_BONK": _pair("BONK", price_usd="1.20", change_h1=20.0, created_ms=0),
                # Dynamisch, erst vor 1h erstellt -> jünger als min_pair_age_hours=6h.
                "ADDR_NEW": _pair("NEWCOIN", price_usd="1.20", change_h1=20.0, created_ms=(now - 3600) * 1000),
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
                min_pair_age_hours=6.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=[],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 100 * 3600.0  # weit genug von der Epoche weg, damit "10h alt" nicht negativ wird

        async def fake_discover(_max_tokens):
            return ["ADDR_NEW"]

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_NEW": _pair("NEWCOIN", price_usd="1.20", change_h1=20.0, created_ms=(now - 10 * 3600) * 1000)}  # 10h alt

        monkeypatch.setattr(memecoin_strategy, "discover_dynamic_solana_tokens", fake_discover)
        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["address"] == "ADDR_NEW"
        assert opened[0]["symbol"] == "NEWCOIN"

    asyncio.run(scenario())


def test_scan_entries_respects_max_dynamic_positions(monkeypatch, tmp_path):
    """Von zwei gleich guten dynamischen Kandidaten wird nur einer geöffnet, wenn das Limit 1 ist."""
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
                max_open_positions=3,
                max_dynamic_positions=1,
                min_pair_age_hours=6.0,
                dynamic_enabled=True,
                max_dynamic_tokens=10,
                curated_addresses=[],
                paper_mode=True,
            ),
            tmp_path,
        )
        now = 100 * 3600.0

        async def fake_discover(_max_tokens):
            return ["ADDR_A", "ADDR_B"]

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                "ADDR_A": _pair("TOKA", price_usd="1.30", change_h1=30.0, volume_h1=200_000.0, created_ms=(now - 30 * 3600) * 1000),
                "ADDR_B": _pair("TOKB", price_usd="1.20", change_h1=20.0, volume_h1=50_000.0, created_ms=(now - 30 * 3600) * 1000),
            }

        monkeypatch.setattr(memecoin_strategy, "discover_dynamic_solana_tokens", fake_discover)
        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)
        monkeypatch.setattr(memecoin_strategy.time, "time", lambda: now)

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["address"] == "ADDR_A"  # höherer volumen-gewichteter Score gewinnt
        assert len(bot.portfolio) == 1

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
                entry_change_pct=8.0,
                entry_max_change_pct=35.0,
                dynamic_enabled=False,
                curated_addresses=["ADDR_BONK", "ADDR_WIF"],
                paper_mode=True,
            ),
            tmp_path,
        )

        async def fake_fetch_pairs_by_address(_addresses):
            return {
                # Beide im Band [8, 35]: BONK +30% mit hohem h1-Volumen, WIF +25% mit
                # niedrigerem h1-Volumen -> volumen-gewichteter Score bevorzugt BONK.
                "ADDR_BONK": _pair("BONK", price_usd="1.30", change_h1=30.0, volume_h1=200_000.0),
                "ADDR_WIF": _pair("WIF", price_usd="2.50", change_h1=25.0, volume_h1=50_000.0),
            }

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        opened = await bot.scan_entries()

        # Nur eine Position erlaubt -> die mit dem höheren Score gewinnt (BONK).
        assert len(opened) == 1
        assert opened[0]["symbol"] == "BONK"
        assert len(bot.portfolio) == 1

    asyncio.run(scenario())


def test_manage_positions_exits_via_take_profit_directly_when_trailing_disabled(monkeypatch, tmp_path):
    """take_profit_pct==trailing_stop_pct+trail_floor_pct=0 -> Floor sitzt exakt auf dem
    TP-Trigger, also realisiert der allererste Zyklus nach Erreichen der Schwelle sofort."""
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
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": time.time(), "peak_price": 1.0, "trailing_active": False, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.20")}  # +20% > take-profit 15%

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        # Erster Zyklus: Take-Profit-Schwelle erreicht -> Trailing-Modus, noch kein Exit.
        resolved = await bot.manage_positions()

        assert resolved == []
        assert "ADDR_BONK" in bot.portfolio
        assert bot.portfolio["ADDR_BONK"]["trailing_active"] is True
        assert bot.portfolio["ADDR_BONK"]["peak_price"] == pytest.approx(1.20)

    asyncio.run(scenario())


def test_manage_positions_exits_via_trailing_stop_after_pullback(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0, take_profit_pct=15.0, trailing_stop_pct=12.0, trail_floor_pct=5.0,
                stop_loss_pct=10.0, slippage_pct=1.5, paper_mode=True,
            ),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": time.time(), "peak_price": 1.0, "trailing_active": False, "trade_id": trade_id}}

        async def fake_pairs_step1(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.30")}  # +30% -> aktiviert Trailing, Peak 1.30

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_pairs_step1)
        resolved1 = await bot.manage_positions()
        assert resolved1 == []
        assert bot.portfolio["ADDR_BONK"]["trailing_active"] is True

        async def fake_pairs_step2(_addresses):
            # Unter Trail (1.30 * 0.88 = 1.144), aber über Floor (1.0 * 1.05 = 1.05).
            return {"ADDR_BONK": _pair("BONK", price_usd="1.10")}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_pairs_step2)
        resolved2 = await bot.manage_positions()

        assert len(resolved2) == 1
        assert resolved2[0]["reason"] == "trailing_stop"
        assert resolved2[0]["pnl"] > 0  # Gewinn bleibt trotz Rückgang vom Peak erhalten
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_trailing_floor_locks_minimum_profit(monkeypatch, tmp_path):
    """Fällt der Preis nach Trailing-Aktivierung hart unter den Trail, greift der feste
    Floor (Mindestgewinn über Einstand) statt eines noch tieferen Trail-Stops."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0, take_profit_pct=15.0, trailing_stop_pct=12.0, trail_floor_pct=5.0,
                stop_loss_pct=10.0, slippage_pct=1.5, paper_mode=True,
            ),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": time.time(), "peak_price": 1.0, "trailing_active": False, "trade_id": trade_id}}

        async def fake_pairs_step1(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.16")}  # +16% -> aktiviert Trailing, Peak 1.16

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_pairs_step1)
        await bot.manage_positions()

        async def fake_pairs_step2(_addresses):
            # Trail wäre 1.16*0.88=1.0208 (tiefer als der Floor 1.0*1.05=1.05) -> Floor greift
            # knapp unterhalb von 1.05, statt erst am tieferen Trail auszulösen.
            return {"ADDR_BONK": _pair("BONK", price_usd="1.04")}

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_pairs_step2)
        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "trailing_stop"
        assert resolved[0]["pnl"] > 0  # Floor sichert auch nach Slippage+DEX-Gebühr einen Mindestgewinn

    asyncio.run(scenario())


def test_manage_positions_peak_price_defaults_for_legacy_state_without_key(monkeypatch, tmp_path):
    """Positionen aus einem State ohne peak_price/trailing_active (vor dieser Änderung
    geschrieben) werden defensiv mit dem Entry-Preis initialisiert statt zu crashen."""
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
        # Absichtlich ohne peak_price/trailing_active-Schlüssel.
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": time.time(), "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.02")}  # +2%, kein Trigger

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        resolved = await bot.manage_positions()

        assert resolved == []
        assert bot.portfolio["ADDR_BONK"]["peak_price"] == pytest.approx(1.02)

    asyncio.run(scenario())


def test_manage_positions_exits_via_stop_loss_with_longer_cooldown(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0, take_profit_pct=15.0, stop_loss_pct=10.0,
                cooldown_sec=4 * 3600, cooldown_after_stop_sec=24 * 3600, paper_mode=True,
            ),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "peak_price": 1.0, "trailing_active": False, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="0.85")}  # -15% < -stop_loss 10%

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        before = time.time()
        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "stop_loss"
        assert resolved[0]["pnl"] < 0
        assert bot.portfolio == {}
        # 24h-Cooldown statt der üblichen 4h nach einem Stop-Loss (Anti-Revenge-Trading).
        assert bot.cooldowns["ADDR_BONK"] == pytest.approx(before + 24 * 3600, abs=5)

    asyncio.run(scenario())


def test_manage_positions_exits_via_max_hold_with_normal_cooldown(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return _eur_usd_ticker("1.00")

    monkeypatch.setattr(memecoin_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MemecoinMomentumBot(
                initial_capital_eur=100.0, take_profit_pct=15.0, stop_loss_pct=10.0, max_hold_sec=3600,
                cooldown_sec=4 * 3600, cooldown_after_stop_sec=24 * 3600, paper_mode=True,
            ),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("CHAIN_BONK@ADDR_BONK", "buy", 8.0, 1.0, 0.0, "paper")
        bot.capital_remaining = 92.0
        # entry_ts weit in der Vergangenheit, Preis flach (kein TP/SL-Trigger) -> muss über die Zeit raus.
        bot.portfolio = {"ADDR_BONK": {"symbol": "BONK", "shares": 8.0, "cost_basis": 8.0, "entry_price": 1.0, "entry_ts": 0.0, "peak_price": 1.0, "trailing_active": False, "trade_id": trade_id}}

        async def fake_fetch_pairs_by_address(_addresses):
            return {"ADDR_BONK": _pair("BONK", price_usd="1.02")}  # +2%, kein TP/SL-Trigger

        monkeypatch.setattr(memecoin_strategy, "fetch_pairs_by_address", fake_fetch_pairs_by_address)

        before = time.time()
        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "time_exit"
        assert bot.portfolio == {}
        assert bot.cooldowns["ADDR_BONK"] == pytest.approx(before + 4 * 3600, abs=5)

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
        assert bot.portfolio["ADDR_BONK"]["peak_price"] == pytest.approx(1.0)
        assert bot.portfolio["ADDR_BONK"]["trailing_active"] is False

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
