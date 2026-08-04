import asyncio
from dataclasses import replace
from pathlib import Path

import polybot.futures_grid_strategy as base_strategy
from polybot.futures_grid_signal_strategy import (
    MarketSnapshot,
    SignalFuturesGridBot,
    dmi_adx_wilder,
    rsi_wilder_series,
)


def ticker(last: float, bid: float | None = None, ask: float | None = None) -> dict:
    return {
        "XETHZEUR": {
            "c": [str(last)],
            "b": [str(last if bid is None else bid)],
            "a": [str(last if ask is None else ask)],
        }
    }


def snapshot(**changes) -> MarketSnapshot:
    base = MarketSnapshot(
        close_15m=100.0,
        previous_high_15m=99.0,
        rsi_15m=40.0,
        previous_rsi_15m=35.0,
        bar_ts_15m=900.0,
        close_1h=100.0,
        previous_high_1h=99.0,
        rsi_1h=55.0,
        previous_rsi_1h=52.0,
        atr_1h=1.0,
        ema20_1h=99.5,
        upper_bb_1h=103.0,
        r4=0.005,
        r12=0.01,
        r24=0.015,
        r1h=0.005,
        r15m=0.002,
        volume_ratio=1.0,
        pullback_8h=True,
        close_4h=101.0,
        previous_close_4h=100.0,
        ema20_4h=100.0,
        ema50_4h=97.0,
        ema200_4h=95.0,
        ema50_slope_24h=0.005,
        atr_4h=3.0,
        adx_4h=22.0,
        plus_di_4h=25.0,
        minus_di_4h=15.0,
        bar_ts_4h=0.0,
    )
    return replace(base, **changes)


def make_bot(tmp_path: Path, **kwargs) -> SignalFuturesGridBot:
    return SignalFuturesGridBot(
        state_path=tmp_path / "signal_grid_state.json",
        initial_capital_eur=1000,
        leverage=2,
        order_margin_eur=15,
        taker_fee_rate=0.0005,
        funding_rate_8h=0,
        **kwargs,
    )


def install_fake_ledger(monkeypatch):
    trades = {}

    async def log(market, side, size, price, edge, status):
        trade_id = len(trades) + 1
        trades[trade_id] = {"market": market, "size": size, "price": price}
        return trade_id

    async def resolve(trade_id, exit_price, pnl):
        trades[trade_id].update(exit_price=exit_price, pnl=pnl)

    monkeypatch.setattr(base_strategy, "log_paper_trade", log)
    monkeypatch.setattr(base_strategy, "resolve_trade", resolve)
    return trades


def test_blocks_hot_market_instead_of_opening_immediately(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path)
    hot = snapshot(r24=0.05, rsi_1h=72.0)

    result = asyncio.run(bot.step(ticker(100), now=1000, snapshot=hot))

    assert result["action"] == "entry_blocked"
    assert "überhitzt" in result["reason"]
    assert bot.orders == []


def test_requires_positive_directional_trend(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path)

    weak_momentum = asyncio.run(
        bot.step(ticker(100), now=1000, snapshot=snapshot(r12=-0.001))
    )
    narrow_trend = asyncio.run(
        bot.step(
            ticker(100),
            now=1100,
            snapshot=snapshot(ema20_4h=99.0, ema50_4h=98.0, atr_4h=3.0),
        )
    )

    assert weak_momentum["action"] == "entry_blocked"
    assert "12-Stunden-Trend" in weak_momentum["reason"]
    assert narrow_trend["action"] == "entry_blocked"
    assert "Trendlinien" in narrow_trend["reason"]
    assert bot.orders == []


def test_arms_crossed_level_then_waits_for_later_recovery_bar(monkeypatch, tmp_path):
    trades = install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, min_buy_gap_sec=0)
    market = snapshot(bar_ts_15m=900)
    opened = asyncio.run(bot.step(ticker(100), now=1000, snapshot=market))
    trigger = bot.next_trigger_price

    armed = asyncio.run(bot.step(ticker(trigger - 0.1), now=1010, snapshot=market))
    same_bar = asyncio.run(bot.step(ticker(trigger - 0.1), now=1020, snapshot=market))
    recovered = asyncio.run(
        bot.step(
            ticker(trigger),
            now=1030,
            snapshot=replace(market, bar_ts_15m=1800, close_15m=100, previous_high_15m=99),
        )
    )

    assert opened["action"] == "open"
    assert armed["action"] == "armed"
    assert same_bar["action"] == "armed_wait"
    assert recovered["action"] == "safety_order"
    assert len(bot.orders) == 2
    assert len(trades) == 2


def test_take_profit_starts_twelve_hour_cooldown(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, cooldown_win_sec=12 * 3600)
    market = snapshot()
    asyncio.run(bot.step(ticker(100), now=1000, snapshot=market))

    closed = asyncio.run(bot.step(ticker(102), now=1100, snapshot=market))
    blocked = asyncio.run(bot.step(ticker(100), now=1200, snapshot=market))

    assert closed["action"] == "take_profit"
    assert blocked["action"] == "cooldown"
    assert bot.cooldown_until == 1100 + 12 * 3600


def test_crash_freezes_new_entries_and_requires_recovery(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, crash_pause_sec=24 * 3600)
    crash = snapshot(r15m=-0.025)

    result = asyncio.run(bot.step(ticker(98), now=1000, snapshot=crash))

    assert result["action"] == "crash_pause"
    assert bot.crash_recovery_required is True
    assert bot.crash_pause_until == 1000 + 24 * 3600
    assert bot.orders == []


def test_cycle_loss_limit_closes_before_margin_guard(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, cycle_loss_limit_pct=0.1)
    market = snapshot()
    asyncio.run(bot.step(ticker(100), now=1000, snapshot=market))

    result = asyncio.run(bot.step(ticker(96), now=1100, snapshot=market))

    assert result["action"] == "hard_stop"
    assert bot.orders == []
    assert bot.cooldown_until == 1100 + 30 * 24 * 3600


def test_defaults_use_proportionally_bounded_500_eur_stake(tmp_path):
    bot = SignalFuturesGridBot(state_path=tmp_path / "defaults.json", funding_rate_8h=0)

    assert bot.initial_capital_eur == 500
    assert bot.order_margin_eur == 12.5
    assert bot.take_profit_pct == 1.2
    assert bot.cooldown_loss_sec == 30 * 24 * 3600
    assert bot.min_trend_spread_atr == 0.75


def test_wilder_indicators_recognize_clean_uptrend():
    closes = [100 + idx for idx in range(40)]
    highs = [close + 1 for close in closes]
    lows = [close - 1 for close in closes]

    rsi = rsi_wilder_series(closes)
    dmi = dmi_adx_wilder(highs, lows, closes)

    assert rsi[-1] == 100
    assert dmi is not None
    plus_di, minus_di, adx = dmi
    assert plus_di > minus_di
    assert adx > 25


def test_armed_state_survives_restart(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    state_path = tmp_path / "signal_grid_state.json"
    bot = SignalFuturesGridBot(
        state_path=state_path,
        funding_rate_8h=0,
        min_buy_gap_sec=0,
    )
    market = snapshot(bar_ts_15m=900)
    asyncio.run(bot.step(ticker(100), now=1000, snapshot=market))
    trigger = bot.next_trigger_price
    asyncio.run(bot.step(ticker(trigger - 0.1), now=1010, snapshot=market))

    restored = SignalFuturesGridBot(state_path=state_path, funding_rate_8h=0)

    assert len(restored.orders) == 1
    assert restored.armed_trigger == trigger
    assert restored.armed_at_bar_ts == 900
