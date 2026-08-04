from backtest.backtest_dca_core import _daily_rows, _max_drawdown_pct


def test_daily_rows_uses_only_complete_days():
    complete = [(hour * 3600, 100, 101, 99, 100 + hour, 1, 1) for hour in range(24)]
    incomplete = [(86400 + hour * 3600, 200, 201, 199, 200 + hour, 1, 1) for hour in range(10)]
    rows = _daily_rows(complete + incomplete)
    assert len(rows) == 1
    assert rows[0][1] == 100
    assert rows[0][4] == 123


def test_max_drawdown_uses_peak_to_trough_equity():
    assert _max_drawdown_pct([500, 550, 495, 520]) == -10.0
