import polybot.battle_report as battle_report
from polybot.battle_report import max_drawdown, time_under_water_hours


H = 3600.0


def test_max_drawdown_measures_worst_fall_from_peak():
    # Hoch 120, Tief 90 -> -25%
    assert max_drawdown([100.0, 120.0, 90.0, 110.0]) == -25.0


def test_max_drawdown_is_zero_when_only_rising():
    assert max_drawdown([100.0, 110.0, 120.0]) == 0.0
    assert max_drawdown([]) == 0.0


def test_time_under_water_measures_from_peak_to_last_red_snapshot():
    # Hoch bei t=0h (100), letzter roter Snapshot bei t=3h, Erholung bei t=4h.
    rows = [(0 * H, 100.0), (1 * H, 95.0), (2 * H, 90.0), (3 * H, 98.0), (4 * H, 101.0)]
    assert time_under_water_hours(rows) == 3.0


def test_time_under_water_counts_ongoing_stretch():
    # Nie erholt: Strecke läuft vom Hoch bis zum letzten Snapshot.
    assert time_under_water_hours([(0 * H, 100.0), (5 * H, 80.0)]) == 5.0
    assert time_under_water_hours([(0 * H, 100.0), (5 * H, 80.0), (9 * H, 70.0)]) == 9.0


def test_time_under_water_resets_on_new_peak():
    # Erstes Tal 1h (Hoch t=0), zweites Tal 4h (Hoch t=2h) -> das längere zählt.
    rows = [
        (0 * H, 100.0),
        (1 * H, 95.0),
        (2 * H, 105.0),   # neues Hoch, Reset
        (3 * H, 100.0),
        (6 * H, 99.0),
        (7 * H, 110.0),
    ]
    assert time_under_water_hours(rows) == 4.0


def test_time_under_water_is_zero_without_data():
    assert time_under_water_hours([]) == 0.0
    assert time_under_water_hours([(0.0, 100.0)]) == 0.0


def test_longest_losing_streak_counts_consecutive_losses(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "paper_trades.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE paper_trades (id INTEGER PRIMARY KEY, market_question TEXT, "
        "real_pnl REAL, resolved_at REAL)"
    )
    # Verluste: -1,-2 (Serie 2), dann Gewinn, dann -1,-1,-1 (Serie 3), dann offen.
    pnls = [(-1.0, 1.0), (-2.0, 2.0), (3.0, 3.0), (-1.0, 4.0), (-1.0, 5.0), (-1.0, 6.0)]
    for pnl, ts in pnls:
        con.execute("INSERT INTO paper_trades (market_question, real_pnl, resolved_at) VALUES (?,?,?)", ("MOM_SOLEUR", pnl, ts))
    # Offener Trade zählt nicht mit.
    con.execute("INSERT INTO paper_trades (market_question, real_pnl, resolved_at) VALUES (?,?,?)", ("MOM_SOLEUR", -9.0, None))
    # Anderer Bot darf die Serie nicht verfälschen.
    con.execute("INSERT INTO paper_trades (market_question, real_pnl, resolved_at) VALUES (?,?,?)", ("DCA_SOLEUR", -5.0, 7.0))
    con.commit()
    con.close()

    monkeypatch.setattr(battle_report, "DB_PATH", str(db))
    assert battle_report.longest_losing_streak("MOM_") == 3
    assert battle_report.longest_losing_streak("DCA_") == 1
    assert battle_report.longest_losing_streak("REV_") == 0
