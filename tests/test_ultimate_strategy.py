import asyncio
import time

import pytest

from polybot import paper_db
import polybot.ultimate_strategy as strategy
from polybot.ultimate_strategy import (
    DEFAULT_TAKER_FEE_RATE,
    UltimateBot,
    advanced_bullish_patterns,
    analyse_market,
    break_even_price,
    fee_adjusted_target,
    net_risk_per_unit,
)


def _rows(count=220, *, rising=True, interval=3600):
    started = time.time() - (count + 2) * interval
    rows = []
    for index in range(count):
        base = 100 + (index * 0.15 if rising else -index * 0.15)
        open_ = base - 0.05 if rising else base + 0.05
        rows.append((started + index * interval, open_, base + 0.4, base - 0.4, base, base, 100 + index))
    return rows


def _ticker(price=100.0):
    return {"c": [str(price)], "b": [str(price - 0.1)], "a": [str(price + 0.1)], "v": ["10", "20"], "p": [str(price), str(price)]}


def _analysis(score=90, regime="uptrend", setup="breakout", atr=1.0):
    return {
        "regime": regime, "setup": setup, "score": score, "rsi": 60.0,
        "macd": (1.0, 0.5, 0.5), "volume_ok": True, "patterns": ["hammer"],
        "atr": atr, "ema20": 101.0, "ema50": 100.0, "ema200": 95.0,
        "support": 95.0, "resistance": 110.0, "natural_target": 110.0,
        "signal_ts": 123456, "close": 102.0,
    }


def test_bot_is_hard_paper_only(tmp_path):
    with pytest.raises(NotImplementedError):
        UltimateBot(paper_mode=False, state_path=tmp_path / "state.json")


def test_downtrend_disables_long_entries():
    result = analyse_market(_rows(rising=False))
    assert result is not None
    assert result["regime"] == "downtrend"
    assert result["setup"] is None
    assert result["score"] == 0


def test_fee_adjusted_target_really_delivers_two_to_one_net():
    entry, stop, fee = 100.0, 98.0, DEFAULT_TAKER_FEE_RATE
    target = fee_adjusted_target(entry, stop, 2.0, fee)
    risk = net_risk_per_unit(entry, stop, fee)
    reward = target * (1 - fee) - entry * (1 + fee)
    assert reward == pytest.approx(2 * risk)
    assert break_even_price(entry, fee) > 101.6


def test_advanced_patterns_include_three_white_soldiers():
    rows = [
        (1, 100, 101.2, 99.8, 101, 100.5, 10),
        (2, 100.8, 102.2, 100.5, 102, 101.5, 11),
        (3, 101.7, 103.2, 101.5, 103, 102.5, 12),
    ]
    assert "three_white_soldiers" in advanced_bullish_patterns(rows)


def test_entry_creates_two_paper_tranches(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ohlc(_pair, _interval):
        return _rows()

    async def fake_ticker(pairs):
        return {pair: _ticker() for pair in pairs}

    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(strategy, "analyse_market", lambda rows, **kwargs: _analysis(score=85 if rows is not None else 0))

    async def scenario():
        await paper_db.init_db()
        bot = UltimateBot(state_path=tmp_path / "state.json", max_spread_pct=0.25)
        opened = await bot.scan_entries()
        assert opened and opened[0]["score"] == 85
        assert len(bot.portfolio) == 1
        position = next(iter(bot.portfolio.values()))
        invested = sum(leg["cost_basis"] for leg in position["legs"])
        assert bot.capital_remaining == pytest.approx(100 - invested)
        assert 1 <= invested <= 12.5
        assert {leg["role"] for leg in position["legs"]} == {"take_profit", "runner"}
        ledger = await paper_db.get_open_trades_by_prefix(strategy.PREFIX)
        assert len(ledger) == 2
        assert {row["market_question"] for row in ledger} == {"ULT_XBTEUR"}

    asyncio.run(scenario())


def test_score_below_80_never_opens(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ohlc(_pair, _interval):
        return _rows()

    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "analyse_market", lambda *_args, **_kwargs: _analysis(score=79))

    async def scenario():
        await paper_db.init_db()
        bot = UltimateBot(state_path=tmp_path / "state.json")
        assert await bot.scan_entries() == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_partial_profit_closes_only_target_leg(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ticker(_pairs):
        return {"XBTEUR": _ticker(104.2)}

    async def fake_ohlc(_pair, _interval):
        return _rows()

    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "analyse_market", lambda *_args, **_kwargs: _analysis())

    async def scenario():
        await paper_db.init_db()
        first = await paper_db.log_paper_trade("ULT_XBTEUR", "buy", 0.05, 100, 0.9, "paper_take_profit")
        second = await paper_db.log_paper_trade("ULT_XBTEUR", "buy", 0.05, 100, 0.9, "paper_runner")
        bot = UltimateBot(state_path=tmp_path / "state.json")
        bot.capital_remaining = 90.0
        bot.portfolio = {"XBTEUR": {
            "legs": [
                {"trade_id": first, "role": "take_profit", "shares": 0.05, "entry_price": 100, "cost_basis": 5, "open": True},
                {"trade_id": second, "role": "runner", "shares": 0.05, "entry_price": 100, "cost_basis": 5, "open": True},
            ],
            "entry_ts": time.time(), "initial_entry": 100, "peak_price": 100,
            "stop_price": 98, "initial_stop": 98, "target_price": 104,
            "added": False,
        }}
        result = await bot.manage_positions()
        assert result and result[0]["reason"] == "partial_profit"
        assert len(bot._open_legs(bot.portfolio["XBTEUR"])) == 1
        assert len(await paper_db.get_open_trades_by_prefix("ULT_")) == 1

    asyncio.run(scenario())


def test_equity_uses_bid_and_both_fees(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ticker(_pairs):
        return {"XBTEUR": _ticker(100.1)}  # bid exactly 100.0

    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)

    async def scenario():
        await paper_db.init_db()
        bot = UltimateBot(state_path=tmp_path / "state.json")
        bot.capital_remaining = 90.0
        bot.portfolio = {"XBTEUR": {"legs": [
            {"trade_id": 1, "role": "runner", "shares": 0.1, "entry_price": 100, "cost_basis": 10, "open": True},
        ]}}
        equity = await bot.equity()
        expected = 100.0 - 20.0 * DEFAULT_TAKER_FEE_RATE
        assert equity["equity_eur"] == pytest.approx(expected)

    asyncio.run(scenario())


def test_mean_reversion_is_rejected_when_range_cannot_cover_fees(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ohlc(_pair, _interval):
        return _rows()

    async def fake_ticker(pairs):
        return {pair: _ticker() for pair in pairs}

    too_narrow = _analysis(score=95, regime="sideways", setup="mean_reversion", atr=1.0)
    too_narrow["natural_target"] = 101.0
    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(strategy, "analyse_market", lambda *_args, **_kwargs: too_narrow)

    async def scenario():
        await paper_db.init_db()
        bot = UltimateBot(state_path=tmp_path / "state.json")
        assert await bot.scan_entries() == []
        assert bot.portfolio == {}
        assert await paper_db.get_open_trades_by_prefix(strategy.PREFIX) == []

    asyncio.run(scenario())


def test_same_closed_candle_signal_is_never_traded_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ohlc(_pair, _interval):
        return _rows()

    async def fake_ticker(pairs):
        return {pair: _ticker() for pair in pairs}

    analysis = _analysis()
    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(strategy, "analyse_market", lambda *_args, **_kwargs: analysis)

    async def scenario():
        await paper_db.init_db()
        bot = UltimateBot(state_path=tmp_path / "state.json")
        bot.last_traded_signals = {"XBTEUR": f"{analysis['signal_ts']}:{analysis['setup']}"}
        bot.last_traded_signals.update({
            "ETHEUR": f"{analysis['signal_ts']}:{analysis['setup']}",
            "SOLEUR": f"{analysis['signal_ts']}:{analysis['setup']}",
        })
        assert await bot.scan_entries() == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_momentum_exit_cannot_close_a_fresh_mean_reversion_trade(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    monkeypatch.setattr(strategy.paper_db_module, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ticker(_pairs):
        return {"XBTEUR": _ticker(100.1)}

    async def fake_ohlc(_pair, _interval):
        return _rows()

    weak = _analysis(regime="sideways", setup="mean_reversion")
    weak["macd"] = (-1.0, -0.5, -0.5)
    monkeypatch.setattr(strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(strategy, "fetch_ohlc", fake_ohlc)
    monkeypatch.setattr(strategy, "analyse_market", lambda *_args, **_kwargs: weak)

    async def scenario():
        await paper_db.init_db()
        first = await paper_db.log_paper_trade("ULT_XBTEUR", "buy", 0.05, 100, 0.9, "paper_take_profit")
        second = await paper_db.log_paper_trade("ULT_XBTEUR", "buy", 0.05, 100, 0.9, "paper_runner")
        bot = UltimateBot(state_path=tmp_path / "state.json")
        bot.capital_remaining = 90.0
        bot.portfolio = {"XBTEUR": {
            "legs": [
                {"trade_id": first, "role": "take_profit", "shares": 0.05, "entry_price": 100, "cost_basis": 5, "open": True},
                {"trade_id": second, "role": "runner", "shares": 0.05, "entry_price": 100, "cost_basis": 5, "open": True},
            ],
            "entry_ts": time.time(), "initial_entry": 100, "peak_price": 100,
            "stop_price": 98, "initial_stop": 98, "target_price": 109,
            "setup": "mean_reversion", "added": False,
        }}
        assert await bot.manage_positions() == []
        assert "XBTEUR" in bot.portfolio
        assert len(await paper_db.get_open_trades_by_prefix("ULT_")) == 2

    asyncio.run(scenario())
