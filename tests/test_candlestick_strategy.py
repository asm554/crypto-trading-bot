import asyncio
import time

import pytest

from polybot import paper_db
import polybot.candlestick_strategy as strategy
from polybot.candlestick_strategy import CandlestickBot, bullish_patterns, macd_snapshot, rsi_wilder


def _rows(count: int, interval: int = 900, start: float = 100.0) -> list[tuple]:
    base_ts = time.time() - (count + 2) * interval
    rows = []
    for index in range(count):
        close = start + index * 0.12 + (0.25 if index % 4 in {0, 1} else -0.15)
        open_ = close - 0.08
        rows.append((base_ts + index * interval, open_, close + 0.4, close - 0.4, close, close, 100.0 + index))
    return rows


def test_indicators_are_finite():
    values = [100 + index * 0.2 + (0.5 if index % 5 == 0 else -0.1) for index in range(80)]
    rsi = rsi_wilder(values)
    macd = macd_snapshot(values)
    assert rsi is not None and 0 <= rsi <= 100
    assert macd is not None and all(value == pytest.approx(value) for value in macd)


def test_bullish_engulfing_is_detected():
    rows = [
        (1, 101.0, 101.2, 99.8, 100.0, 100.4, 10),
        (2, 99.9, 101.5, 99.7, 101.2, 100.7, 20),
    ]
    assert "bullish_engulfing" in bullish_patterns(rows)


def test_bot_is_hard_paper_only(tmp_path):
    with pytest.raises(NotImplementedError):
        CandlestickBot(paper_mode=False, state_path=tmp_path / "state.json")


def test_entry_uses_jupiter_paper_quote(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))
    rows_1h = _rows(220, interval=3600)
    rows_15m = _rows(80)

    async def fake_ohlc(_pair, interval):
        return rows_1h if interval == 60 else rows_15m

    async def fake_rate():
        return 1.0

    async def fake_entry(_amount, _rate):
        return 0.2, {"priceImpactPct": "0.001", "outAmount": "200000000"}

    async def fake_exit(_shares, _rate):
        return 24.8, {"outAmount": "24800000"}

    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "signal_score", lambda *_args, **_kwargs: {
        "score": 85, "patterns": ["hammer"], "trend": True, "ema": True,
        "macd": True, "rsi": 60.0, "rsi_ok": True, "volume": True, "breakout": True,
    })

    async def scenario():
        await paper_db.init_db()
        bot = CandlestickBot(state_path=tmp_path / "state.json")
        monkeypatch.setattr(bot, "_eurusd_rate", fake_rate)
        monkeypatch.setattr(bot, "_entry_quote", fake_entry)
        monkeypatch.setattr(bot, "_exit_quote", fake_exit)
        opened = await bot.scan_entries()
        assert opened and opened[0]["score"] == 85
        assert bot.capital_remaining == pytest.approx(75.0)
        assert bot.portfolio[strategy.PAIR]["shares"] == pytest.approx(0.2)
        rows = await paper_db.get_open_trades_by_prefix(strategy.PREFIX)
        assert len(rows) == 1
        assert rows[0]["market_question"] == "CND_SOLUSDC"

    asyncio.run(scenario())


def test_entry_stays_closed_when_jupiter_has_no_quote(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ohlc(_pair, interval):
        return _rows(220, 3600) if interval == 60 else _rows(80)

    async def fake_rate():
        return 1.0

    async def no_entry_quote(_amount, _rate):
        return None

    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "signal_score", lambda *_args, **_kwargs: {
        "score": 100, "patterns": ["hammer"], "trend": True, "ema": True,
        "macd": True, "rsi": 60.0, "rsi_ok": True, "volume": True, "breakout": True,
    })

    async def scenario():
        await paper_db.init_db()
        bot = CandlestickBot(state_path=tmp_path / "state.json")
        monkeypatch.setattr(bot, "_eurusd_rate", fake_rate)
        monkeypatch.setattr(bot, "_entry_quote", no_entry_quote)
        assert await bot.scan_entries() == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_time_exit_resolves_with_jupiter_value(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_rate():
        return 1.0

    async def fake_exit(_shares, _rate):
        return 26.0, {"outAmount": "26000000"}

    async def fake_ohlc(_pair, _interval):
        return _rows(80)

    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "rsi_wilder", lambda _values: 60.0)
    monkeypatch.setattr(strategy, "macd_snapshot", lambda _values: (1.0, 0.5, 0.5))

    async def scenario():
        await paper_db.init_db()
        trade_id = await paper_db.log_paper_trade("CND_SOLUSDC", "buy", 0.2, 125.0, 0.85, "paper_jupiter")
        bot = CandlestickBot(max_hold_sec=1, state_path=tmp_path / "state.json")
        bot.capital_remaining = 75.0
        bot.portfolio[strategy.PAIR] = {
            "shares": 0.2, "cost_basis": 25.0, "entry_price": 125.0,
            "entry_ts": time.time() - 10, "peak_price": 130.0, "stop_price": 100.0,
            "trade_id": trade_id,
        }
        monkeypatch.setattr(bot, "_eurusd_rate", fake_rate)
        monkeypatch.setattr(bot, "_exit_quote", fake_exit)
        closed = await bot.manage_positions()
        assert closed and closed[0]["reason"] == "time_exit"
        assert closed[0]["pnl"] == pytest.approx(1.0)
        assert bot.capital_remaining == pytest.approx(101.0)
        assert await paper_db.get_open_trades_by_prefix(strategy.PREFIX) == []

    asyncio.run(scenario())
