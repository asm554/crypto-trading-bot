import asyncio

import pytest

import polybot.momentum_strategy as momentum_strategy
import polybot.paper_db as paper_db
from polybot.momentum_strategy import MomentumBot


def _valid_ticker(open_price="100", last="110", vol24="2000", vwap24="108", bid=None, ask=None):
    """Kraken-style ticker payload (o, c, h, l, v, p, b, a).

    Bid/Ask leiten sich per Default aus ``last`` ab (±0.1%), damit die Quote zu
    jedem überschriebenen ``last`` konsistent bleibt: ein fest verdrahtetes Bid
    läge sonst über dem Last und ergäbe Fills, die es real nicht gibt.
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
    bot.state_path = tmp_path / "momentum_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.cooldowns = {}
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_entry_scan = 0.0
    bot.last_snapshot = 0.0
    bot.trade_count = 0
    return bot


def test_snapshot_for_pair_parses_kraken_ticker():
    snap = MomentumBot._snapshot_for_pair("SOLEUR", {"SOLEUR": _valid_ticker()})
    assert snap is not None
    assert snap["pair"] == "SOLEUR"
    assert snap["last_price"] == pytest.approx(110.0)
    assert snap["change_pct"] == pytest.approx(10.0)
    # volume_eur = v[1] * p[1] = 2000 * 108
    assert snap["volume_eur"] == pytest.approx(216000.0)
    assert snap["high"] == pytest.approx(118.0)
    assert snap["low"] == pytest.approx(92.0)


def test_snapshot_for_pair_returns_none_on_missing_or_garbage():
    # Missing pair entirely
    assert MomentumBot._snapshot_for_pair("SOLEUR", {}) is None
    # Garbage payload -> parse raises -> None
    assert MomentumBot._snapshot_for_pair("SOLEUR", {"SOLEUR": {"o": "abc"}}) is None
    # Non-positive prices are rejected
    assert MomentumBot._snapshot_for_pair("SOLEUR", {"SOLEUR": _valid_ticker(open_price="0")}) is None


def test_scan_entries_opens_position_for_valid_momentum_candidate(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # SOLEUR: change +10% (within [3, 25]), volume 10000*110 = 1.1M€ > 500k
        return {"SOLEUR": _valid_ticker(open_price="100", last="110", vol24="10000", vwap24="110")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 10.0  # echte 24h-Bewegung im Band [3, 25]

    monkeypatch.setattr(momentum_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(momentum_strategy, "rolling_24h_change_pct", fake_rolling)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MomentumBot(initial_capital_eur=100.0, position_eur=12.0, paper_mode=True),
            tmp_path,
        )
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["pair"] == "SOLEUR"
        assert opened[0]["amount"] == pytest.approx(12.0)
        assert "SOLEUR" in bot.portfolio
        assert bot.capital_remaining == pytest.approx(88.0)

        rows = await paper_db.get_open_trades_by_prefix("MOM_")
        assert len(rows) == 1
        assert rows[0]["market_question"] == "MOM_SOLEUR"

    asyncio.run(scenario())


def test_scan_entries_fills_at_ask_not_last(monkeypatch, tmp_path):
    """Gekauft wird zum Ask; der Einstiegsfilter entscheidet weiter auf Last."""
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Breiter Spread, damit Ask (112) klar von Last (110) unterscheidbar ist.
        return {"SOLEUR": _valid_ticker(open_price="100", last="110", vol24="10000", vwap24="110", bid="108", ask="112")}

    async def fake_rolling(_pair, *args, **kwargs):
        return 10.0

    monkeypatch.setattr(momentum_strategy, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(momentum_strategy, "rolling_24h_change_pct", fake_rolling)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(
            MomentumBot(initial_capital_eur=100.0, position_eur=12.0, paper_mode=True),
            tmp_path,
        )
        bot.last_entry_scan = 0.0

        opened = await bot.scan_entries()

        assert len(opened) == 1
        assert opened[0]["price"] == pytest.approx(112.0)
        # 12€ zum Ask ergeben weniger Coins als zum Last -> Spread kostet echt.
        assert bot.portfolio["SOLEUR"]["shares"] == pytest.approx(12.0 / 112.0)
        assert bot.portfolio["SOLEUR"]["entry_price"] == pytest.approx(112.0)
        # Trailing-Peak ist ein Signal und startet auf Last, nicht auf dem Ask.
        assert bot.portfolio["SOLEUR"]["peak_price"] == pytest.approx(110.0)

    asyncio.run(scenario())


def test_manage_positions_exits_via_trailing_stop_and_returns_cash(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Price crashed from 100 to 90 -> below trailing stop (peak 100, -2.5%)
        return {"SOLEUR": _valid_ticker(open_price="100", last="90", vol24="2000", vwap24="108")}

    monkeypatch.setattr(momentum_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MomentumBot(
                initial_capital_eur=100.0,
                position_eur=12.0,
                trailing_stop_pct=2.5,
                hard_stop_pct=4.0,
                paper_mode=True,
            ),
            tmp_path,
        )
        # Simulate an already-open position: 12€ deployed, cash reduced.
        trade_id = await paper_db.log_paper_trade("MOM_SOLEUR", "buy", 0.12, 100.0, 0.1, "paper")
        bot.capital_remaining = 88.0
        bot.portfolio = {
            "SOLEUR": {
                "shares": 0.12,
                "cost_basis": 12.0,
                "entry_price": 100.0,
                "entry_ts": 0.0,
                "peak_price": 100.0,
                "trade_id": trade_id,
            }
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] == "trailing_stop"
        assert resolved[0]["pnl"] < 0
        assert bot.portfolio == {}
        # Trigger auf Last (90), Fill zum Bid (89.91):
        # value = 0.12 * 89.91 = 10.7892; pnl = 10.7892 - 12 - 12*0.004 - 10.7892*0.004
        # Cash returned: 88 + entry_cost(12) + pnl(-1.30196) = 98.69804
        assert bot.capital_remaining == pytest.approx(98.69804, rel=1e-4)

        rows = await paper_db.get_open_trades_by_prefix("MOM_")
        assert rows == []

    asyncio.run(scenario())


def test_manage_positions_exits_via_hard_stop(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        # Price 95: peak stays at entry 100, trailing (-2.5% -> 97.5) already breached,
        # but also below hard stop (entry -4% -> 96). Either way it must exit.
        return {"SOLEUR": _valid_ticker(open_price="100", last="95", vol24="2000", vwap24="108")}

    monkeypatch.setattr(momentum_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()

        bot = _bind_bot_to_tmp_storage(
            MomentumBot(initial_capital_eur=100.0, position_eur=12.0, paper_mode=True),
            tmp_path,
        )
        trade_id = await paper_db.log_paper_trade("MOM_SOLEUR", "buy", 0.12, 100.0, 0.1, "paper")
        bot.capital_remaining = 88.0
        bot.portfolio = {
            "SOLEUR": {
                "shares": 0.12,
                "cost_basis": 12.0,
                "entry_price": 100.0,
                "entry_ts": 0.0,
                "peak_price": 100.0,
                "trade_id": trade_id,
            }
        }

        resolved = await bot.manage_positions()

        assert len(resolved) == 1
        assert resolved[0]["reason"] in ("trailing_stop", "hard_stop")
        assert bot.portfolio == {}
        assert bot.capital_remaining > 88.0

        rows = await paper_db.get_open_trades_by_prefix("MOM_")
        assert rows == []

    asyncio.run(scenario())


def test_momentum_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        MomentumBot(paper_mode=False)
