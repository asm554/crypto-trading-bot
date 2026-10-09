import pytest

from shortdesk import auditor, cartographer, desk, exitclerk, gate, risk, screener, store
from shortdesk.candles import C
from shortdesk.config import RULES, DAY, Thresholds

R = RULES


def d(i, o, h, l, c):
    return C(i * DAY, o, h, l, c)


# --- Screener -------------------------------------------------------------
def flip_series():
    return [d(0, 100, 110, 99, 108), d(1, 108, 112, 100, 105),  # C1 faellt aber Anker 112 wird gleich genutzt
            d(2, 105, 111, 98, 100), d(3, 100, 109, 93, 95), d(4, 95, 100, 90, 92)]


def test_short_flip_detected_with_doji_c2():
    ds = [d(0, 100, 120, 95, 110), d(1, 110, 115, 100, 110), d(2, 108, 112, 94, 95)]
    # C1 muss runter schliessen -> hier nicht
    assert screener.detect_short_flip(ds) is None
    ds = [d(0, 110, 120, 95, 100), d(1, 100, 110, 95, 100 - 1), d(2, 99, 108, 90, 95)]
    f = screener.detect_short_flip(ds)
    assert f and f["anchor_high"] == 120


def test_flip_invalid_if_c2_takes_out_c1_high():
    ds = [d(0, 110, 120, 95, 100), d(1, 100, 121, 95, 98), d(2, 98, 110, 90, 95)]
    assert screener.detect_short_flip(ds) is None


def test_flip_dies_on_close_above_anchor():
    ds = [d(0, 110, 120, 95, 100), d(1, 100, 110, 95, 99), d(2, 99, 108, 90, 95), d(3, 95, 125, 94, 122)]
    assert screener.detect_short_flip(ds) is None


def test_bullish_flip_is_context_only():
    ds = [d(0, 100, 105, 80, 90 + 20), d(1, 100, 105, 95, 120), d(2, 120, 130, 118, 128)]
    # C1 up, C2 closer higher, C2 low > C1 low, C3 closer higher
    ds = [d(0, 100, 112, 90, 110), d(1, 110, 120, 95, 115), d(2, 115, 125, 98, 120)]
    r = screener.scan("X", ds, [C(i, 1, 1, 1, 1) for i in range(10)], 7, 6)
    assert r["short"] is None and r["bull_context"] is True


def test_prime_when_4h_bounces():
    ds = [d(0, 110, 120, 95, 100), d(1, 100, 110, 95, 99), d(2, 99, 108, 90, 95)]
    c4 = [C(i, 10, 10, 10, 10 + (5 if i == 9 else 0)) for i in range(10)]
    assert screener.scan("X", ds, c4, 7, 6)["short"]["tag"] == "PRIME"
    flat = [C(i, 10, 10, 10, 10) for i in range(10)]
    assert screener.scan("X", ds, flat, 7, 6)["short"]["tag"] == "FLIP"


# --- Cartographer ---------------------------------------------------------
H4 = 14400


def c4(i, o, h, l, c):
    return C(i * H4, o, h, l, c)


def test_fvg_found_tested_and_filled():
    # a.low=100 > c.high=95 -> Zone 95..100
    base = [c4(0, 105, 110, 100, 102), c4(1, 101, 101, 90, 92), c4(2, 92, 95, 88, 90)]
    z = cartographer.find_fvgs(base, 0)
    assert len(z) == 1 and (z[0].bottom, z[0].top, z[0].status) == (95, 100, "UNTESTED")
    tested = base + [c4(3, 90, 96, 89, 91)]  # Docht in die Zone
    assert cartographer.find_fvgs(tested, 0)[0].status == "TESTED"
    filled = base + [c4(3, 90, 104, 89, 103)]  # Close ueber Oberkante
    assert cartographer.find_fvgs(filled, 0)[0].status == "FILLED"


def test_confirmation_needs_rejection_below_bottom():
    z = cartographer.Zone(top=100, bottom=95, formed_ts=0, status="TESTED")
    ok = [C(3600, 96, 97, 93, 94)]
    assert cartographer.confirm_1h(z, ok) == {"entry": 94, "confirm_ts": 3600}
    assert cartographer.confirm_1h(z, [C(3600, 94, 94.5, 92, 93)]) is None   # nie in der Zone
    assert cartographer.confirm_1h(z, [C(3600, 96, 99, 94, 98)]) is None     # Close in Zone
    assert cartographer.confirm_1h(cartographer.Zone(100, 95, 0, "FILLED"), ok) is None


# --- Risk -----------------------------------------------------------------
def test_sizing_risks_exactly_one_percent():
    p = risk.build_plan(R, 1000, entry=100, stop=102, open_same_coin=[], pending_same_coin=0, mark=100)
    assert p["ok"] and p["risk_usd"] == pytest.approx(10) and p["size"] * 2 == pytest.approx(10)


def test_leverage_cap_reduces_risk():
    p = risk.build_plan(R, 1000, entry=100, stop=100.01, open_same_coin=[], pending_same_coin=0, mark=100)
    assert p["size"] * 100 == pytest.approx(3000) and p["risk_pct"] < 1.0


def test_add_requires_breakeven_and_uses_half_percent():
    prev = {"entry": 100, "stop": 102}
    no = risk.build_plan(R, 1000, 99, 101, [prev], 0, mark=99.5)
    assert not no["ok"] and "Breakeven" in no["reason"]
    yes = risk.build_plan(R, 1000, 97, 99, [prev], 0, mark=97.9)  # 2.1 = >1R Gewinn
    assert yes["ok"] and yes["risk_pct"] == pytest.approx(0.5) and yes["add_number"] == 1


def test_pending_blocks_and_stop_below_entry_rejected():
    assert not risk.build_plan(R, 1000, 100, 101, [], 1, 100)["ok"]
    assert not risk.build_plan(R, 1000, 100, 99, [], 0, 100)["ok"]


# --- Exit Clerk -----------------------------------------------------------
T = {"id": 1, "entry": 100, "stop": 102, "size": 5, "risk_usd": 10, "anchor_high": 110,
     "created_ts": 10 * 3600, "stale_flagged": 0}


def test_stop_hit_and_cost_math():
    h = [C(10 * 3600, 100, 101, 99, 100), C(11 * 3600, 100, 102.5, 99, 101)]
    ev = exitclerk.evaluate(T, h, [], 101, 12 * 3600, R)
    assert ev["reason"] == "STOP" and ev["price"] == 102
    s = exitclerk.settle(T, 102, ev["ts"], "STOP", R)
    assert s["gross_usd"] == pytest.approx(-10)
    assert s["fee_usd"] == pytest.approx(0.0006 * 202 * 5)
    assert s["slip_usd"] == pytest.approx(0.0002 * 202 * 5)
    assert s["r_multiple"] == pytest.approx(s["pnl_usd"] / 10) and s["r_multiple"] < -1


def test_anchor_close_exits_and_wick_above_does_not():
    day_wick = [C(0, 100, 115, 99, 105)]          # Docht ueber Anker, Close drunter
    assert exitclerk.evaluate(T, [], day_wick, 100, 20 * 3600, R) is None
    day_close = [C(DAY, 100, 115, 99, 111)]
    ev = exitclerk.evaluate(T, [], day_close, 100, 3 * DAY, R)
    assert ev["reason"] == "ANCHOR_CLOSE" and ev["price"] == 111


def test_stale_after_72h_flat_but_not_when_in_profit():
    now = T["created_ts"] + 73 * 3600
    assert exitclerk.evaluate(T, [], [], 100.1, now, R) == {"action": "STALE"}
    assert exitclerk.evaluate(T, [], [], 97, now, R) is None  # im Gewinn: laufen lassen


# --- Auditor / Gate -------------------------------------------------------
@pytest.fixture
def db(tmp_path):
    return store.connect(tmp_path / "t.db")


def add_closed(db, i, r, decision="TAKEN"):
    tid = store.insert_trade(db, dict(coin="X", zone_key=str(i), zone_top=1, zone_bottom=1, entry=1, stop=2, size=1,
                                      risk_usd=10, risk_pct=1, add_number=0, anchor_high=3, created_ts=i))
    db.execute("UPDATE trades SET decision=?, status='CLOSED', r_multiple=?, pnl_usd=?, fee_usd=1, slip_usd=0.5 WHERE id=?",
               (decision, r, r * 10, tid))
    db.commit()


def test_auditor_no_go_below_40_trades_and_go_when_all_pass(db):
    for i in range(10):
        add_closed(db, i, 1.0)
    assert auditor.report(db)["go"] is False  # zu wenig Trades
    for i in range(10, 45):
        add_closed(db, i, 1.0 if i % 2 else -0.5)
    rep = auditor.report(db)
    assert rep["checks"]["trades"] and rep["go"] is True


def test_drawdown_check_fails_the_go(db):
    for i in range(40):
        add_closed(db, i, 2.0)
    for i in range(40, 54):
        add_closed(db, i, -3.0)  # 14 * -30$ = -420 von 1800 Peak = 23%
    rep = auditor.report(db)
    assert rep["checks"]["max_dd"] is False and rep["go"] is False


def test_thresholds_are_locked(db):
    auditor.lock_thresholds(db)
    with pytest.raises(auditor.ThresholdsChanged):
        auditor.lock_thresholds(db, Thresholds(min_expectancy_r=0.1))


def test_gate_parse_and_single_decision(db):
    assert gate.parse_reply("TAKEN 7") == (7, "TAKEN")
    assert gate.parse_reply("/skip #7") == (7, "SKIPPED")
    assert gate.parse_reply("kaufen!") is None
    tid = store.insert_trade(db, dict(coin="X", zone_key="k", zone_top=1, zone_bottom=1, entry=1, stop=2, size=1,
                                      risk_usd=10, risk_pct=1, add_number=0, anchor_high=3, created_ts=0))
    assert gate.decide(db, tid, "TAKEN") is True
    assert gate.decide(db, tid, "SKIPPED") is False  # nur einmal


def test_pending_expires_to_skipped(db):
    store.insert_trade(db, dict(coin="X", zone_key="k", zone_top=1, zone_bottom=1, entry=1, stop=2, size=1,
                                risk_usd=10, risk_pct=1, add_number=0, anchor_high=3, created_ts=0))
    assert gate.expire_pending(db, 4, now=5 * 3600) == 1


# --- End-to-End mit Fake-Markt -------------------------------------------
class FakeMarket:
    def __init__(self, data):
        self.data = data

    def candles(self, pair, interval):
        return self.data.get((pair, interval), [])


def test_pipeline_flip_to_alert(db, monkeypatch):
    monkeypatch.setattr(gate, "send", lambda t: True)
    rules = type(R)(universe=("X",))
    daily = [d(0, 110, 120, 95, 100), d(1, 100, 110, 95, 99), d(2, 99, 108, 90, 95)]
    anchor_ts = 0
    c4h = [c4(0, 105, 110, 100, 102), c4(1, 101, 101, 90, 92), c4(2, 92, 95, 88, 90), c4(3, 90, 96, 89, 91)]
    c1h = [C(5 * H4, 96, 97, 93, 94)]
    m = FakeMarket({("X", 1440): daily, ("X", 240): c4h, ("X", 60): c1h})
    assert desk.run_daily(db, m, rules)["flips"] == ["X"]
    out = desk.run_hourly(db, m, rules)
    assert len(out["new_plans"]) == 1
    t = store.trades(db)[0]
    assert t["decision"] == "PENDING" and t["entry"] == 94 and t["stop"] == pytest.approx(100.5)
    assert t["risk_usd"] == pytest.approx(10)
    assert desk.run_hourly(db, m, rules)["new_plans"] == []  # kein Duplikat-Alert
