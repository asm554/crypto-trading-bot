import asyncio
import time

import pytest

import polybot.futures_strategy as futures_strategy
import polybot.paper_db as paper_db
from polybot.futures_strategy import FuturesPaperBot, position_mark_to_market, trend_signal


def _candles(start=100.0, step=1.0, count=40):
    return [{"time": i * 3600 * 1000, "close": str(start + i * step), "volume": 1000} for i in range(count)]


def _ticker(mark=100.0, bid=99.9, ask=100.1, funding=0.0):
    return {
        "symbol": "PF_XBTUSD",
        "markPrice": mark,
        "bid": bid,
        "ask": ask,
        "volumeQuote": 100_000_000,
        "fundingRate": funding,
        "suspended": False,
        "postOnly": False,
    }


def test_trend_signal_detects_confirmed_long_and_short():
    assert trend_signal(_candles(step=1.0))["side"] == "long"
    assert trend_signal(_candles(start=140.0, step=-1.0))["side"] == "short"


def test_trend_signal_rejects_flat_market():
    assert trend_signal(_candles(step=0.0)) is None


def test_futures_bot_is_hard_paper_only_and_caps_leverage():
    with pytest.raises(NotImplementedError):
        FuturesPaperBot(paper_mode=False)
    with pytest.raises(ValueError):
        FuturesPaperBot(leverage=5.0)


def test_scan_entries_opens_long_at_ask_and_reserves_only_margin(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_tickers(_symbols):
        return {"PF_XBTUSD": _ticker(mark=139.0, bid=138.9, ask=139.1)}

    async def fake_candles(*_args, **_kwargs):
        return _candles(step=1.0)

    async def fake_fx():
        return 1.0

    monkeypatch.setattr(futures_strategy, "fetch_futures_tickers", fake_tickers)
    monkeypatch.setattr(futures_strategy, "fetch_futures_candles", fake_candles)
    monkeypatch.setattr(futures_strategy, "fetch_eur_usd_rate", fake_fx)

    async def scenario():
        await paper_db.init_db()
        bot = FuturesPaperBot(
            symbols=("PF_XBTUSD",), interval_sec=0, min_volume_usd=0,
            position_margin_eur=20, leverage=2, max_spread_pct=0.25, paper_mode=True,
        )
        opened = await bot.scan_entries()
        assert len(opened) == 1
        assert opened[0]["side"] == "long"
        pos = bot.portfolio["PF_XBTUSD"]
        assert pos["entry_price_usd"] == pytest.approx(139.1)
        assert pos["quantity"] * pos["entry_price_usd"] == pytest.approx(40.0)
        assert pos["entry_fee_eur"] == pytest.approx(0.02)
        assert bot.capital_remaining == pytest.approx(79.98)
        rows = await paper_db.get_open_trades_by_prefix("FUT_")
        assert rows[0]["side"] == "buy"
        assert rows[0]["size"] * rows[0]["entry_price"] == pytest.approx(40.0)

    asyncio.run(scenario())


def test_scan_entries_opens_short_at_bid(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_tickers(_symbols):
        return {"PF_XBTUSD": _ticker(mark=101.0, bid=100.9, ask=101.1)}

    async def fake_candles(*_args, **_kwargs):
        return _candles(start=140.0, step=-1.0)

    async def fake_fx():
        return 1.0

    monkeypatch.setattr(futures_strategy, "fetch_futures_tickers", fake_tickers)
    monkeypatch.setattr(futures_strategy, "fetch_futures_candles", fake_candles)
    monkeypatch.setattr(futures_strategy, "fetch_eur_usd_rate", fake_fx)

    async def scenario():
        await paper_db.init_db()
        bot = FuturesPaperBot(symbols=("PF_XBTUSD",), interval_sec=0, min_volume_usd=0, max_spread_pct=0.25)
        opened = await bot.scan_entries()
        assert opened[0]["side"] == "short"
        assert bot.portfolio["PF_XBTUSD"]["entry_price_usd"] == pytest.approx(100.9)
        rows = await paper_db.get_open_trades_by_prefix("FUT_")
        assert rows[0]["side"] == "sell"

    asyncio.run(scenario())


def test_funding_sign_is_opposite_for_long_and_short():
    base = {
        "quantity": 0.1,
        "entry_price_usd": 100.0,
        "margin_eur": 20.0,
        "funding_pnl_eur": 0.0,
        "last_funding_ts": time.time() - 3600,
    }
    ticker = _ticker(mark=100.0, bid=100.0, ask=100.0, funding=0.5)
    long_value, long_pnl = position_mark_to_market(
        {**base, "side": "long"}, ticker, 2.0, 0.0, include_pending_funding=True
    )
    short_value, short_pnl = position_mark_to_market(
        {**base, "side": "short"}, ticker, 2.0, 0.0, include_pending_funding=True
    )
    assert long_pnl == pytest.approx(-0.025, abs=0.001)
    assert short_pnl == pytest.approx(0.025, abs=0.001)
    assert long_value < 20 < short_value


def test_manage_long_stop_closes_at_bid_and_includes_both_fees(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_tickers(_symbols):
        return {"PF_XBTUSD": _ticker(mark=97.0, bid=96.9, ask=97.1)}

    async def fake_fx():
        return 1.0

    monkeypatch.setattr(futures_strategy, "fetch_futures_tickers", fake_tickers)
    monkeypatch.setattr(futures_strategy, "fetch_eur_usd_rate", fake_fx)

    async def scenario():
        await paper_db.init_db()
        bot = FuturesPaperBot(symbols=("PF_XBTUSD",), hard_stop_pct=2.0)
        trade_id = await paper_db.log_paper_trade("FUT_PF_XBTUSD", "buy", 0.4, 100.0, 0.01, "paper")
        bot.capital_remaining = 79.98
        bot.portfolio = {
            "PF_XBTUSD": {
                "side": "long", "quantity": 0.4, "entry_price_usd": 100.0,
                "entry_price_eur": 100.0, "margin_eur": 20.0,
                "entry_fee_eur": 0.02, "funding_pnl_eur": 0.0,
                "entry_ts": time.time(), "last_funding_ts": time.time(),
                "peak_return_pct": 0.0, "trade_id": trade_id,
            }
        }
        closed = await bot.manage_positions()
        assert closed[0]["reason"] == "hard_stop"
        assert closed[0]["pnl"] == pytest.approx((96.9 - 100.0) * 0.4 - 0.02 - 96.9 * 0.4 * 0.0005)
        assert bot.portfolio == {}
        assert await paper_db.get_open_trades_by_prefix("FUT_") == []

    asyncio.run(scenario())
