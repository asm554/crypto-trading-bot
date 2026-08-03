import pytest

from backtest.research_new_signals import (
    close_position,
    open_position,
    relative_strength,
    stop_mid,
    zscore,
)


def test_relative_strength_is_coin_return_minus_btc_return():
    assert relative_strength(120, 100, 105, 100) == pytest.approx(0.15)


def test_zscore_uses_last_value_against_supplied_window():
    values = [1.0, 1.0, 1.0, 3.0]
    assert zscore(values) == pytest.approx((3.0 - 1.5) / 0.8660254)


def test_round_trip_charges_spread_and_both_fees():
    position = open_position("ETHEUR", 0, 100.0, 100.0, 0.004, 0.05, 10.0)
    proceeds, trade = close_position(position, 3600, 100.0, 0.004, 0.05, "test")
    assert proceeds < 100.0
    assert trade["pnl_eur"] < -0.8


def test_stop_gaps_to_open_instead_of_optimistic_stop_fill():
    position = open_position("SOLEUR", 0, 100.0, 100.0, 0.004, 0.0, 10.0)
    candle = (3600, 85.0, 92.0, 80.0, 88.0, 88.0, 1.0)
    assert stop_mid(position, candle) == 85.0


def test_stop_not_triggered_when_hourly_low_stays_above_it():
    position = open_position("SOLEUR", 0, 100.0, 100.0, 0.004, 0.0, 10.0)
    candle = (3600, 100.0, 105.0, 91.0, 102.0, 102.0, 1.0)
    assert stop_mid(position, candle) is None
