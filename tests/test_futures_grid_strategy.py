import asyncio
from pathlib import Path

import polybot.futures_grid_strategy as strategy


def ticker(last: float, bid: float | None = None, ask: float | None = None) -> dict:
    bid = last if bid is None else bid
    ask = last if ask is None else ask
    return {
        "XETHZEUR": {
            "c": [str(last)],
            "b": [str(bid)],
            "a": [str(ask)],
        }
    }


def make_bot(tmp_path: Path, **kwargs) -> strategy.FuturesGridBot:
    return strategy.FuturesGridBot(
        state_path=tmp_path / "futures_state.json",
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

    monkeypatch.setattr(strategy, "log_paper_trade", log)
    monkeypatch.setattr(strategy, "resolve_trade", resolve)
    return trades


def test_opens_initial_and_safety_order(monkeypatch, tmp_path):
    trades = install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path)

    first = asyncio.run(bot.step(ticker(2000, 1999, 2001), now=1000))
    second = asyncio.run(bot.step(ticker(1983, 1982, 1984), now=1010))

    assert first["action"] == "open"
    assert second["action"] == "safety_order"
    assert len(bot.orders) == 2
    assert len(trades) == 2
    assert bot.reserved_margin == 30
    assert round(bot.total_notional, 2) == 60


def test_take_profit_closes_every_ladder_leg(monkeypatch, tmp_path):
    trades = install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, take_profit_pct=1.1)
    asyncio.run(bot.step(ticker(2000), now=1000))

    result = asyncio.run(bot.step(ticker(2023), now=1010))

    assert result["action"] == "take_profit"
    assert bot.orders == []
    assert trades[1]["exit_price"] == 2023
    assert trades[1]["pnl"] > 0
    assert bot.capital_remaining > 1000


def test_fast_gap_opens_every_crossed_grid_level(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, grid_step_pct=1, max_safety_orders=5)
    asyncio.run(bot.step(ticker(2000), now=1000))

    result = asyncio.run(bot.step(ticker(1900), now=1010))

    assert result == {"action": "safety_order", "opened": 5}
    assert len(bot.orders) == 6  # base order + five crossed safety levels
    assert [round(o["trigger_price"]) for o in bot.orders] == [2000, 1980, 1960, 1941, 1921, 1902]


def test_margin_guard_closes_before_liquidation(monkeypatch, tmp_path):
    install_fake_ledger(monkeypatch)
    bot = make_bot(tmp_path, maintenance_margin_pct=5, margin_guard_ratio=1.25)
    asyncio.run(bot.step(ticker(2000), now=1000))

    liq = bot.liquidation_price()
    assert liq is not None
    guard_price = liq + 2
    result = asyncio.run(bot.step(ticker(guard_price), now=1010))

    assert result["action"] == "margin_guard"
    assert bot.orders == []
    assert bot.capital_remaining < 1000


def test_rejects_live_mode_and_excess_leverage(tmp_path):
    try:
        strategy.FuturesGridBot(state_path=tmp_path / "x", paper_mode=False)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("live mode must be rejected")

    try:
        strategy.FuturesGridBot(state_path=tmp_path / "x", leverage=3)
    except ValueError:
        pass
    else:
        raise AssertionError("leverage above 2x must be rejected")
