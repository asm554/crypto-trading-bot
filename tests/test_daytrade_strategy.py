import asyncio
import time

import pytest

import polybot.daytrade_strategy as daytrade_strategy
import polybot.paper_db as paper_db
from polybot.daytrade_strategy import DaytradeBot


def _valid_ticker(open_price="100", last="110", vol24="2000", vwap24="108", bid=None, ask=None):
    """Kraken-style ticker payload (o, c, h, l, v, p, b, a).

    Bid/Ask leiten sich per Default aus ``last`` ab (±0.1%), damit die Quote zu
    jedem überschriebenen ``last`` konsistent bleibt.
    """
    last_f = float(last)
    return {
        "o": open_price,
        "c": [last, "1.5"],
        "h": ["115", "118"],
        "l": ["95", "92"],
        "v": ["1000", vol24],
        "p": ["105", vwap24],
        "t": [10, 20],
        "b": [bid if bid is not None else f"{last_f * 0.999:.8f}", "1", "1"],
        "a": [ask if ask is not None else f"{last_f * 1.001:.8f}", "1", "1"],
    }


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "daytrade_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.cooldowns = {}
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_entry_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_count = 0
    return bot


def test_snapshot_for_pair_parses_kraken_ticker():
    snap = DaytradeBot._snapshot_for_pair("SOLEUR", {"SOLEUR": _valid_ticker()})
    assert snap is not None
    assert snap["pair"] == "SOLEUR"
    assert snap["last_price"] == pytest.approx(110.0)
    assert snap["volume_eur"] == pytest.approx(216000.0)


def test_snapshot_for_pair_returns_none_on_missing_or_garbage():
    assert DaytradeBot._snapshot_for_pair("SOLEUR", {}) is None
    assert DaytradeBot._snapshot_for_pair("SOLEUR", {"SOLEUR": {"o": "abc"}}) is None
    assert DaytradeBot._snapshot_for_pair("SOLEUR", {"SOLEUR": _valid_ticker(open_price="0")}) is None


def test_scan_entries_uses_short_lookback_not_24h(monkeypatch, tmp_path):
    """Der Zappler fragt rolling_change_pct mit dem konfigurierten lookback_hours
    ab, nicht rolling_24h_change_pct - das ist der zentrale Unterschied zu Momentum."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(open_price="100", last="110", vol24="10000", vwap24="110")}

    seen_calls = []

    async def fake_rolling(pair, lookback_bars=24, interval_min=60, **kwargs):
        seen_calls.append((pair, lookback_bars, interval_min))
        return 10.0

    monkeypatch.setattr(daytrade_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(daytrade_strategy, "rolling_change_pct", fake_rolling)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            DaytradeBot(initial_capital_eur=100.0, position_eur=10.0, lookback_hours=4, paper_mode=True),
            tmp_path,
        )
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["pair"] == "SOLEUR"
        assert opened[0]["amount"] == pytest.approx(10.0)
        # lookback_bars=4 (nicht 24!) wurde tatsächlich angefragt.
        assert ("SOLEUR", 4, 60) in seen_calls

    asyncio.run(scenario())


def test_scan_entries_fills_at_ask_not_last(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(open_price="100", last="110", vol24="10000", vwap24="110", bid="108", ask="112")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 10.0

    monkeypatch.setattr(daytrade_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(daytrade_strategy, "rolling_change_pct", fake_rolling)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            DaytradeBot(initial_capital_eur=100.0, position_eur=10.0, paper_mode=True),
            tmp_path,
        )
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["price"] == pytest.approx(112.0)
        assert bot.portfolio["SOLEUR"]["shares"] == pytest.approx(10.0 / 112.0)
        assert bot.portfolio["SOLEUR"]["peak_price"] == pytest.approx(110.0)

    asyncio.run(scenario())


def test_manage_positions_exits_via_trailing_stop(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Peak 100, -1.5% Trailing -> 98.5 unterschritten
        return {"SOLEUR": _valid_ticker(open_price="100", last="98", vol24="2000", vwap24="108")}

    monkeypatch.setattr(daytrade_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            DaytradeBot(initial_capital_eur=100.0, position_eur=10.0, trailing_stop_pct=1.5, hard_stop_pct=3.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("DAY_SOLEUR", "buy", 0.10, 100.0, 0.1, "paper")
        bot.capital_remaining = 90.0
        bot.portfolio = {
            "SOLEUR": {"shares": 0.10, "cost_basis": 10.0, "entry_price": 100.0, "entry_ts": 0.0, "peak_price": 100.0, "trade_id": trade_id}
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "trailing_stop"
        assert bot.portfolio == {}
        rows = await paper_db.get_open_trades_by_prefix("DAY_")
        assert rows == []

    asyncio.run(scenario())


def test_manage_positions_exits_via_short_time_exit(monkeypatch, tmp_path):
    """Der kurze rollierende Max-Hold (6h statt 48h) ist der zweite zentrale
    Unterschied zu Momentum - eine Position, die nicht getriggert hat, aber
    lange genug offen ist, muss zwangsweise schließen."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Kurs unverändert - kein Stop getriggert, nur die Zeit läuft ab.
        return {"SOLEUR": _valid_ticker(open_price="100", last="100", vol24="2000", vwap24="108")}

    monkeypatch.setattr(daytrade_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            DaytradeBot(initial_capital_eur=100.0, position_eur=10.0, max_hold_sec=6 * 3600, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("DAY_SOLEUR", "buy", 0.10, 100.0, 0.1, "paper")
        bot.capital_remaining = 90.0
        bot.portfolio = {
            "SOLEUR": {
                "shares": 0.10, "cost_basis": 10.0, "entry_price": 100.0,
                "entry_ts": time.time() - 7 * 3600,  # 7h alt > 6h Max-Hold
                "peak_price": 100.0, "trade_id": trade_id,
            }
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "time_exit"
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_daytrade_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        DaytradeBot(paper_mode=False)
