import asyncio

import pytest

from backtest.dca_regime import classify_btc_regime, execute_regime_gated_round


def rows(closes):
    return [(i * 86400.0, value, value, value, value, value, 1.0) for i, value in enumerate(closes)]


def test_regime_is_neutral_until_200_completed_days():
    result = classify_btc_regime(rows([100.0] * 199))
    assert result.regime == "neutral"
    assert result.size_factor == 0.5


def test_rising_market_is_bull_and_falling_market_is_bear():
    bull = classify_btc_regime(rows([100 + i for i in range(220)]))
    bear = classify_btc_regime(rows([320 - i for i in range(220)]))
    assert (bull.regime, bull.size_factor) == ("bull", 1.0)
    assert (bear.regime, bear.size_factor) == ("bear", 0.0)


class FakeSim:
    class FakeClock:
        now = 12345.0

    clock = FakeClock()

    def __init__(self, closes):
        self.closes = closes

    async def fetch_ohlc(self, pair, interval):
        assert (pair, interval) == ("BTCEUR", 1440)
        return rows(self.closes)


class FakeBot:
    def __init__(self):
        self.per_round_eur = 100.0
        self.last_buy = 0.0
        self.seen_round = None

    async def execute_dca_round(self):
        self.seen_round = self.per_round_eur
        return [{"amount_eur": self.per_round_eur}]


def test_neutral_halves_round_temporarily():
    bot = FakeBot()
    decision, trades = asyncio.run(execute_regime_gated_round(bot, FakeSim([100.0] * 100)))
    assert decision.regime == "neutral"
    assert bot.seen_round == 50.0
    assert bot.per_round_eur == 100.0
    assert trades == [{"amount_eur": 50.0}]


def test_bear_skips_buy_but_advances_schedule():
    bot = FakeBot()
    decision, trades = asyncio.run(execute_regime_gated_round(bot, FakeSim([320 - i for i in range(220)])))
    assert decision.regime == "bear"
    assert trades == []
    assert bot.seen_round is None
    assert bot.last_buy == pytest.approx(12345.0)
