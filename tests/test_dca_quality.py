import asyncio

import pytest

from backtest.dca_quality import evaluate_pair_quality, execute_quality_gated_round


def _rows(closes, step=86400):
    return [(i * step, c, c, c, c, 1.0, 1) for i, c in enumerate(closes)]


def test_trend_rule_requires_completed_ema_history_and_uptrend():
    short = evaluate_pair_quality("ETHEUR", _rows([100.0] * 199), _rows([100.0] * 5, 3600), "trend")
    assert short.allowed is False
    assert short.trend_ok is None

    up = [100.0] * 200 + [110.0] * 60
    decision = evaluate_pair_quality("ETHEUR", _rows(up), _rows([100.0] * 5, 3600), "trend")
    assert decision.allowed is True
    assert decision.trend_ok is True


def test_reversal_rule_requires_positive_completed_four_hour_change():
    positive = evaluate_pair_quality(
        "SOLEUR", [], _rows([100.0, 99.0, 98.0, 99.0, 101.0], 3600), "reversal"
    )
    negative = evaluate_pair_quality(
        "SOLEUR", [], _rows([100.0, 99.0, 98.0, 97.0, 96.0], 3600), "reversal"
    )
    assert positive.allowed is True
    assert positive.reversal_4h_pct == pytest.approx(1.0)
    assert negative.allowed is False


def test_combined_rule_requires_both_conditions():
    up = [100.0] * 200 + [110.0] * 60
    falling_hours = _rows([100.0, 99.0, 98.0, 97.0, 96.0], 3600)
    decision = evaluate_pair_quality("ADAEUR", _rows(up), falling_hours, "trend_reversal")
    assert decision.trend_ok is True
    assert decision.reversal_ok is False
    assert decision.allowed is False


def test_gate_filters_pairs_and_disables_recovery_during_real_round():
    class Sim:
        class Clock:
            now = 12345.0
        clock = Clock()

        async def fetch_ohlc(self, pair, interval):
            if interval == 1440:
                return []
            return _rows([100.0, 99.0, 98.0, 99.0, 101.0], 3600) if pair == "ETHEUR" else _rows([100.0, 99.0, 98.0, 97.0, 96.0], 3600)

    class Bot:
        active_pairs = [{"pair": "ETHEUR"}, {"pair": "SOLEUR"}]
        recovery_ticket_eur = 25.0
        last_buy = 0.0

        async def execute_dca_round(self):
            assert self.active_pairs == [{"pair": "ETHEUR"}]
            assert self.recovery_ticket_eur == 0.0
            return [{"pair": "ETHEUR"}]

    bot = Bot()
    decisions, trades = asyncio.run(execute_quality_gated_round(bot, Sim(), "reversal"))
    assert [d.allowed for d in decisions] == [True, False]
    assert trades == [{"pair": "ETHEUR"}]
    assert bot.active_pairs == [{"pair": "ETHEUR"}, {"pair": "SOLEUR"}]
    assert bot.recovery_ticket_eur == 25.0


def test_gate_consumes_empty_scheduled_round():
    class Sim:
        class Clock:
            now = 67890.0
        clock = Clock()

        async def fetch_ohlc(self, _pair, _interval):
            return []

    class Bot:
        active_pairs = [{"pair": "ETHEUR"}]
        recovery_ticket_eur = 25.0
        last_buy = 0.0

        async def execute_dca_round(self):
            raise AssertionError("blocked round must not call production entry logic")

    bot = Bot()
    _decisions, trades = asyncio.run(execute_quality_gated_round(bot, Sim(), "trend"))
    assert trades == []
    assert bot.last_buy == 67890.0
    assert bot._backtest_quality_counts["empty_rounds"] == 1
