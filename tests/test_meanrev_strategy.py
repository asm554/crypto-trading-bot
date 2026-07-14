import asyncio

import pytest

import polybot.meanrev_strategy as meanrev_strategy
import polybot.paper_db as paper_db
from polybot.meanrev_strategy import MeanRevBot, rsi_wilder


def _ticker(open_price="100", last="90", vol24="2000", vwap24="96"):
    """Kraken-style ticker payload used by MeanRevBot._ticker_snapshot."""
    return {
        "o": open_price,
        "c": [last, "1.0"],
        "v": ["1000", vol24],
        "p": ["95", vwap24],
    }


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "meanrev_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.cooldowns = {}
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_entry_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_count = 0
    return bot


# ---------------------------------------------------------------------------
# rsi_wilder
# ---------------------------------------------------------------------------

def test_rsi_wilder_returns_none_for_too_few_closes():
    assert rsi_wilder([1.0, 2.0, 3.0], period=14) is None


def test_rsi_wilder_returns_100_for_monotonic_increase():
    closes = [float(x) for x in range(1, 25)]  # strictly increasing -> avg_loss == 0
    assert rsi_wilder(closes, period=14) == 100.0


def test_rsi_wilder_strongly_falling_series_is_well_below_30():
    closes = [float(x) for x in range(50, 20, -1)]  # strictly decreasing
    rsi = rsi_wilder(closes, period=14)
    assert rsi is not None
    assert 0.0 <= rsi < 30.0


def test_rsi_wilder_mixed_series_in_open_interval():
    # Alternating up/down closes -> RSI strictly between 0 and 100
    closes = []
    price = 100.0
    for i in range(30):
        price += 1.0 if i % 2 == 0 else -1.2
        closes.append(price)
    rsi = rsi_wilder(closes, period=14)
    assert rsi is not None
    assert 0.0 < rsi < 100.0


# ---------------------------------------------------------------------------
# _ticker_snapshot
# ---------------------------------------------------------------------------

def test_ticker_snapshot_parses_correctly():
    snap = MeanRevBot._ticker_snapshot("SOLEUR", {"SOLEUR": _ticker()})
    assert snap is not None
    assert snap["pair"] == "SOLEUR"
    assert snap["last_price"] == pytest.approx(90.0)
    assert snap["change_pct"] == pytest.approx(-10.0)
    # volume_eur = v[1] * p[1] = 2000 * 96
    assert snap["volume_eur"] == pytest.approx(192000.0)


def test_ticker_snapshot_returns_none_on_missing_or_garbage():
    assert MeanRevBot._ticker_snapshot("SOLEUR", {}) is None
    assert MeanRevBot._ticker_snapshot("SOLEUR", {"SOLEUR": {"o": "abc"}}) is None
    assert MeanRevBot._ticker_snapshot("SOLEUR", {"SOLEUR": _ticker(open_price="0")}) is None


# ---------------------------------------------------------------------------
# scan_entries
# ---------------------------------------------------------------------------

def test_scan_entries_opens_position_on_oversold_stabilized_candidate(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # SOLEUR down ~-13.6% (<= -entry_drop_pct 8), last price 95
        return {"SOLEUR": _ticker(open_price="110", last="95", vol24="2000", vwap24="96")}

    def build_ohlc():
        # 20 bars, strictly falling closes -> RSI 0 (< rsi_max).
        # Recent 6-bar low is ~91 so last=95 clears low6 * (1 + confirm_pct/100).
        rows = []
        for i in range(20):
            close = 120.0 - i
            low = close - 10.0
            high = close + 2.0
            open_ = close + 1.0
            vwap = close
            vol = 1000.0
            rows.append((float(i), open_, high, low, close, vwap, vol))
        return rows

    async def fake_fetch_ohlc(pair, interval_min=60):
        return build_ohlc()

    async def fake_sleep(_seconds):
        return None

    async def fake_rolling(_pair, *args, **kwargs):
        return -13.6  # echter 24h-Drop <= -entry_drop_pct (8)

    monkeypatch.setattr(meanrev_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(meanrev_strategy, "fetch_ohlc", fake_fetch_ohlc)
    monkeypatch.setattr(meanrev_strategy, "rolling_24h_change_pct", fake_rolling)
    monkeypatch.setattr(meanrev_strategy.asyncio, "sleep", fake_sleep)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MeanRevBot(initial_capital_eur=100.0, position_eur=15.0, paper_mode=True),
            tmp_path,
        )
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["pair"] == "SOLEUR"
        assert opened[0]["amount"] == pytest.approx(15.0)
        assert opened[0]["rsi"] < bot.rsi_max
        assert "SOLEUR" in bot.portfolio
        assert bot.capital_remaining == pytest.approx(85.0)

        rows = await paper_db.get_open_trades_by_prefix("REV_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "REV_SOLEUR"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# manage_positions
# ---------------------------------------------------------------------------

def test_manage_positions_exits_via_take_profit(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # entry 100 -> last 105 = +5% >= take_profit_pct(4)
        return {"SOLEUR": _ticker(open_price="100", last="105")}

    monkeypatch.setattr(meanrev_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MeanRevBot(initial_capital_eur=100.0, position_eur=15.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("REV_SOLEUR", "buy", 0.15, 100.0, 0.1, "paper")
        bot.capital_remaining = 85.0
        bot.portfolio = {
            "SOLEUR": {
                "shares": 0.15,
                "cost_basis": 15.0,
                "entry_price": 100.0,
                "entry_ts": 0.0,
                "trade_id": trade_id,
            }
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "take_profit"
        assert resolved[0]["pnl"] > 0
        assert bot.portfolio == {}
        assert bot.capital_remaining > 85.0

        rows = await paper_db.get_open_trades_by_prefix("REV_")
        assert rows == []

    asyncio.run(scenario())


def test_manage_positions_exits_via_stop_loss(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # entry 100 -> last 94 = -6% <= -stop_loss_pct(5)
        return {"SOLEUR": _ticker(open_price="100", last="94")}

    monkeypatch.setattr(meanrev_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MeanRevBot(initial_capital_eur=100.0, position_eur=15.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("REV_SOLEUR", "buy", 0.15, 100.0, 0.1, "paper")
        bot.capital_remaining = 85.0
        bot.portfolio = {
            "SOLEUR": {
                "shares": 0.15,
                "cost_basis": 15.0,
                "entry_price": 100.0,
                "entry_ts": 0.0,
                "trade_id": trade_id,
            }
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "stop_loss"
        assert resolved[0]["pnl"] < 0
        assert bot.portfolio == {}
        # Cash returns but below original 100 due to loss + fees.
        assert 85.0 < bot.capital_remaining < 100.0

        rows = await paper_db.get_open_trades_by_prefix("REV_")
        assert rows == []

    asyncio.run(scenario())


def test_meanrev_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        MeanRevBot(paper_mode=False)
