import asyncio

import pytest

import polybot.arb_strategy as arb_strategy
import polybot.paper_db as paper_db
from polybot.arb_strategy import LEG_BTC_ETH, LEG_EUR_BTC, LEG_EUR_ETH, TriangularArbBot
from polybot.paper_db import get_open_trades_by_prefix, get_realized_pnl_by_prefix


def _leg(last, bid, ask):
    """Minimales Kraken-Ticker-Payload für ein Arb-Leg (nur c/b/a nötig)."""
    return {"c": [str(last), "0"], "b": [str(bid), "1", "1"], "a": [str(ask), "1", "1"]}


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "arb_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.capital_remaining = bot.initial_capital_eur
    bot.trade_count = 0
    bot.last_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_timestamps = []
    return bot


def test_arb_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        TriangularArbBot(paper_mode=False)


def test_cycle_margin_direction_a_hand_calculated():
    # Bewusst extreme, aber leicht nachrechenbare Werte statt realistischer
    # Marktdaten - das testet die Formel/Richtung, nicht den Realismus.
    quotes = {
        LEG_EUR_BTC: {"bid": 49990.0, "ask": 50000.0},
        LEG_EUR_ETH: {"bid": 1600.0, "ask": 1600.5},
        LEG_BTC_ETH: {"bid": 0.0299, "ask": 0.030},
    }
    fee = 0.004
    f = 1 - fee
    btc = (1 / 50000.0) * f
    eth = (btc / 0.030) * f
    expected = (eth * 1600.0) * f

    got = TriangularArbBot._cycle_margin(quotes, "eur_btc_eth", fee)
    assert got == pytest.approx(expected)
    assert got > 1.0  # profitabel nach Konstruktion


def test_cycle_margin_direction_b_hand_calculated():
    quotes = {
        LEG_EUR_BTC: {"bid": 49990.0, "ask": 50000.0},
        LEG_EUR_ETH: {"bid": 1600.0, "ask": 1600.5},
        LEG_BTC_ETH: {"bid": 0.0299, "ask": 0.030},
    }
    fee = 0.004
    f = 1 - fee
    eth = (1 / 1600.5) * f
    btc = (eth * 0.0299) * f
    expected = (btc * 49990.0) * f

    got = TriangularArbBot._cycle_margin(quotes, "eur_eth_btc", fee)
    assert got == pytest.approx(expected)


def test_scan_entries_executes_profitable_cycle_and_resolves_immediately(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {
            LEG_EUR_BTC: _leg(50000, 49990, 50000),
            LEG_EUR_ETH: _leg(1600, 1600, 1600.5),
            LEG_BTC_ETH: _leg(0.0300, 0.0299, 0.030),
        }

    monkeypatch.setattr(arb_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            TriangularArbBot(initial_capital_eur=100.0, ticket_eur=25.0, min_net_profit_eur=0.05, paper_mode=True),
            tmp_path,
        )
        bot.last_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["direction"] == "eur_btc_eth"
        assert opened[0]["net_profit_eur"] > 0
        assert bot.trade_count == 1
        assert bot.capital_remaining == pytest.approx(100.0 + opened[0]["net_profit_eur"])

        # Zyklus ist sofort aufgelöst - keine offene Position.
        open_rows = await get_open_trades_by_prefix("ARB_")
        assert open_rows == []
        realized = await get_realized_pnl_by_prefix("ARB_")
        assert realized == pytest.approx(opened[0]["net_profit_eur"])

    asyncio.run(scenario())


def test_scan_entries_does_not_trade_on_realistic_near_miss(monkeypatch, tmp_path, caplog):
    """Reale Kraken-Quotes (siehe Aufgaben-Kontext) ergeben ~-1.2% netto -
    der Bot darf hier nicht handeln, muss die Marge aber loggen."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {
            LEG_EUR_BTC: _leg(56078, 56076.9, 56080.5),
            LEG_EUR_ETH: _leg(1612.76, 1612.75, 1612.76),
            LEG_BTC_ETH: _leg(0.028753, 0.028751, 0.028755),
        }

    monkeypatch.setattr(arb_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            TriangularArbBot(initial_capital_eur=100.0, ticket_eur=25.0, min_net_profit_eur=0.05, paper_mode=True),
            tmp_path,
        )
        bot.last_scan = 0.0

        with caplog.at_level("INFO", logger="polybot.arb_strategy"):
            opened = await bot.scan_entries()

        assert opened == []
        assert bot.trade_count == 0
        assert bot.capital_remaining == pytest.approx(100.0)
        assert (await get_open_trades_by_prefix("ARB_")) == []
        assert any("netto -1" in rec.message or "netto -0" in rec.message for rec in caplog.records)

    asyncio.run(scenario())


def test_max_trades_per_hour_rate_limit(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {
            LEG_EUR_BTC: _leg(50000, 49990, 50000),
            LEG_EUR_ETH: _leg(1600, 1600, 1600.5),
            LEG_BTC_ETH: _leg(0.0300, 0.0299, 0.030),
        }

    monkeypatch.setattr(arb_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            TriangularArbBot(
                initial_capital_eur=100.0, ticket_eur=25.0, min_net_profit_eur=0.05,
                max_trades_per_hour=1, paper_mode=True,
            ),
            tmp_path,
        )

        bot.last_scan = 0.0
        first = await bot.scan_entries()
        assert len(first) == 1
        assert bot.trade_count == 1

        # Intervall-Gate manuell umgehen, um den Rate-Limit-Deckel isoliert zu testen.
        bot.last_scan = 0.0
        second = await bot.scan_entries()
        assert second == []
        assert bot.trade_count == 1  # zweiter, ebenfalls profitabler Zyklus wird blockiert

    asyncio.run(scenario())
