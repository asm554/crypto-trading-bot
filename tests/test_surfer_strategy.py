import asyncio
import time

import pytest

import polybot.paper_db as paper_db
import polybot.surfer_strategy as surfer_strategy
from polybot.surfer_strategy import SurferBot, atr_wilder, ema_series


def _valid_ticker(last="20.3", bid=None, ask=None, vol24="1000", vwap24="20"):
    """Kraken-style ticker payload (o, c, h, l, v, p, b, a).

    Bid/Ask leiten sich per Default aus ``last`` ab (±0.1%).
    """
    last_f = float(last)
    return {
        "o": last,
        "c": [last, "1.0"],
        "h": [last, last],
        "l": [last, last],
        "v": ["500", vol24],
        "p": ["19", vwap24],
        "t": [10, 20],
        "b": [bid if bid is not None else f"{last_f * 0.999:.8f}", "1", "1"],
        "a": [ask if ask is not None else f"{last_f * 1.001:.8f}", "1", "1"],
    }


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "surfer_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.consecutive_losses = 0
    bot.loss_pause_until = 0.0
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_entry_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_count = 0
    return bot


def _build_ohlc_rows(closes=None, highs=None, lows=None, volumes=None):
    """6 stündliche Kerzen, die standardmäßig alle vier Einstiegsfilter erfüllen:
    EMA(3) über EMA(5), Ausbruch über das 5h-Hoch, letztes Volumen 5x Durchschnitt."""
    closes = closes or [10.0, 10.5, 11.0, 11.5, 12.0, 20.0]
    highs = highs or [10.2, 10.7, 11.2, 11.7, 12.2, 20.5]
    lows = lows or [9.8, 10.3, 10.8, 11.3, 11.8, 19.5]
    volumes = volumes or [100.0, 100.0, 100.0, 100.0, 100.0, 500.0]
    return [(float(i), closes[i], highs[i], lows[i], closes[i], closes[i], volumes[i]) for i in range(6)]


def _bot_kwargs(**overrides):
    kwargs = dict(
        initial_capital_eur=100.0,
        ema_fast_period=3,
        ema_slow_period=5,
        breakout_lookback_hours=5,
        atr_period=3,
        paper_mode=True,
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Reine Berechnungs-Helfer
# ---------------------------------------------------------------------------

def test_ticker_snapshot_parses_kraken_ticker():
    snap = SurferBot._ticker_snapshot("SOLEUR", {"SOLEUR": _valid_ticker(last="110")})
    assert snap is not None
    assert snap["pair"] == "SOLEUR"
    assert snap["last_price"] == pytest.approx(110.0)


def test_ticker_snapshot_returns_none_on_missing_or_garbage():
    assert SurferBot._ticker_snapshot("SOLEUR", {}) is None
    assert SurferBot._ticker_snapshot("SOLEUR", {"SOLEUR": {"c": ["abc"]}}) is None


def test_ema_series_basic():
    series = ema_series([10.0, 10.5, 11.0, 11.5, 12.0, 20.0], period=3)
    assert series is not None
    assert series[0] == pytest.approx(10.5)  # SMA-Seed der ersten 3 Werte
    assert series[-1] == pytest.approx(15.75)


def test_ema_series_returns_none_when_too_short():
    assert ema_series([1.0, 2.0], period=3) is None


def test_atr_wilder_basic():
    rows = _build_ohlc_rows()
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    atr = atr_wilder(highs, lows, closes, period=3)
    assert atr == pytest.approx(3.3, rel=1e-6)


def test_atr_wilder_returns_none_when_insufficient_data():
    assert atr_wilder([10.0, 11.0], [9.0, 10.0], [9.5, 10.5], period=14) is None


def test_surfer_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        SurferBot(paper_mode=False)


# ---------------------------------------------------------------------------
# scan_entries
# ---------------------------------------------------------------------------

def test_scan_entries_opens_when_all_filters_pass(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="20.3", bid="20.2", ask="20.4")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 5.0

    async def fake_fetch_ohlc(_pair, _interval=60):
        return _build_ohlc_rows()

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "rolling_change_pct", fake_rolling)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_fetch_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        # Fill zum Ask, nicht zum Last.
        assert opened[0]["price"] == pytest.approx(20.4)
        # ATR(3.3) * 2.0 unter dem Einstieg.
        assert opened[0]["stop_price"] == pytest.approx(20.4 - 3.3 * 2.0, rel=1e-4)
        # Risikobasierte Größe: 0.50€ Risiko / 6.6 Stop-Distanz.
        assert opened[0]["amount"] == pytest.approx(0.50 / 6.6 * 20.4, rel=1e-4)
        assert bot.portfolio["SOLEUR"]["shares"] == pytest.approx(0.50 / 6.6, rel=1e-4)
        assert bot.portfolio["SOLEUR"]["peak_price"] == pytest.approx(20.3)

    asyncio.run(scenario())


def test_scan_entries_skips_without_confirmed_trend(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="20.3")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 0.0  # kein Aufwärtstrend

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "rolling_change_pct", fake_rolling)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        opened = await bot.scan_entries()
        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_without_ema_alignment(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="20.3")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 5.0

    async def fake_fetch_ohlc(_pair, _interval=60):
        # Fallender statt steigender Kursverlauf -> EMA(3) < EMA(5).
        return _build_ohlc_rows(closes=[20.0, 19.0, 18.0, 17.0, 16.0, 15.0], highs=[20.5, 19.5, 18.5, 17.5, 16.5, 15.5], lows=[19.5, 18.5, 17.5, 16.5, 15.5, 14.5])

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "rolling_change_pct", fake_rolling)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_fetch_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        opened = await bot.scan_entries()
        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_without_breakout(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Unter dem 5h-Hoch von 12.2 - kein Ausbruch.
        return {"SOLEUR": _valid_ticker(last="12.0")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 5.0

    async def fake_fetch_ohlc(_pair, _interval=60):
        return _build_ohlc_rows()

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "rolling_change_pct", fake_rolling)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_fetch_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        opened = await bot.scan_entries()
        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_without_volume_confirmation(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="20.3")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 5.0

    async def fake_fetch_ohlc(_pair, _interval=60):
        # Letztes Volumen gleich dem Durchschnitt statt 1.2x darüber.
        return _build_ohlc_rows(volumes=[100.0] * 6)

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "rolling_change_pct", fake_rolling)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_fetch_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        opened = await bot.scan_entries()
        assert opened == []
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_scan_entries_skips_when_position_already_open(tmp_path):
    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        bot.portfolio["SOLEUR"] = {"shares": 0.01, "cost_basis": 1.0, "entry_price": 100.0, "entry_ts": time.time(), "peak_price": 100.0, "stop_price": 90.0, "trade_id": 1}
        opened = await bot.scan_entries()
        assert opened == []

    asyncio.run(scenario())


def test_scan_entries_skips_during_loss_pause(tmp_path):
    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs()), tmp_path)
        bot.last_entry_scan = 0.0
        bot.loss_pause_until = time.time() + 1000
        opened = await bot.scan_entries()
        assert opened == []

    asyncio.run(scenario())


def test_scan_entries_skips_when_account_loss_limit_breached(tmp_path):
    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(account_loss_limit_pct=10.0)), tmp_path)
        bot.last_entry_scan = 0.0
        bot.capital_remaining = 85.0  # < 90€ Kontoverlust-Sperre bei 100€ Start
        opened = await bot.scan_entries()
        assert opened == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# manage_positions
# ---------------------------------------------------------------------------

def test_manage_positions_exits_via_atr_stop(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="94")}

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        # Enger ATR-Stop (95), weiter Trailing-Stop (10% -> 90): der ATR-Stop
        # ist der engere/höhere der beiden und muss zuerst greifen.
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(trailing_stop_pct=10.0)), tmp_path)
        trade_id = await paper_db.log_paper_trade("SURF_SOLEUR", "buy", 0.01, 100.0, 0.05, "paper")
        bot.capital_remaining = 99.0
        bot.portfolio["SOLEUR"] = {"shares": 0.01, "cost_basis": 1.0, "entry_price": 100.0, "entry_ts": time.time(), "peak_price": 100.0, "stop_price": 95.0, "trade_id": trade_id}

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "atr_stop"
        assert resolved[0]["pnl"] < 0
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_exits_via_trailing_stop(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="96")}

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        # Enger Trailing-Stop (3% -> 97), weiter ATR-Stop (80): der Trailing-
        # Stop ist hier der engere/höhere der beiden.
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(trailing_stop_pct=3.0)), tmp_path)
        trade_id = await paper_db.log_paper_trade("SURF_SOLEUR", "buy", 0.01, 100.0, 0.05, "paper")
        bot.capital_remaining = 99.0
        bot.portfolio["SOLEUR"] = {"shares": 0.01, "cost_basis": 1.0, "entry_price": 100.0, "entry_ts": time.time(), "peak_price": 100.0, "stop_price": 80.0, "trade_id": trade_id}

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "trailing_stop"
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_exits_via_time_exit(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Kurs unverändert, kein Stop getriggert - nur die Zeit läuft ab.
        return {"SOLEUR": _valid_ticker(last="100")}

    async def fake_fetch_ohlc(_pair, _interval=60):
        return []  # zu wenig Daten -> kein EMA-Exit-Signal

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_fetch_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(trailing_stop_pct=3.0, max_hold_sec=7 * 24 * 3600)), tmp_path)
        trade_id = await paper_db.log_paper_trade("SURF_SOLEUR", "buy", 0.01, 100.0, 0.05, "paper")
        bot.capital_remaining = 99.0
        bot.portfolio["SOLEUR"] = {
            "shares": 0.01, "cost_basis": 1.0, "entry_price": 100.0,
            "entry_ts": time.time() - 8 * 24 * 3600,  # 8 Tage alt > 7 Tage Max-Hold
            "peak_price": 100.0, "stop_price": 50.0, "trade_id": trade_id,
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "time_exit"
        assert bot.portfolio == {}

    asyncio.run(scenario())


def test_manage_positions_activates_loss_pause_after_streak(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {"SOLEUR": _valid_ticker(last="94")}

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(trailing_stop_pct=10.0, loss_streak_limit=3, loss_pause_sec=24 * 3600)), tmp_path)
        bot.consecutive_losses = 2  # zwei Verluste liegen schon vor
        trade_id = await paper_db.log_paper_trade("SURF_SOLEUR", "buy", 0.01, 100.0, 0.05, "paper")
        bot.capital_remaining = 99.0
        bot.portfolio["SOLEUR"] = {"shares": 0.01, "cost_basis": 1.0, "entry_price": 100.0, "entry_ts": time.time(), "peak_price": 100.0, "stop_price": 95.0, "trade_id": trade_id}

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["pnl"] < 0
        assert bot.consecutive_losses == 3
        assert bot.loss_pause_until > time.time()

    asyncio.run(scenario())
