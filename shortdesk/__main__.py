import argparse

from . import auditor, desk, gate, store
from .candles import Market


def main():
    p = argparse.ArgumentParser(prog="shortdesk", description="Short-only Paper-Desk (nur Alerts, keine Orders)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("daily", "4h", "1h", "weekly", "status", "journal"):
        sub.add_parser(name)
    d = sub.add_parser("decide")
    d.add_argument("trade_id", type=int)
    d.add_argument("decision", choices=["taken", "skipped"])
    a = p.parse_args()

    db = store.connect()
    auditor.lock_thresholds(db)
    m = Market()
    if a.cmd == "daily":
        print(desk.run_daily(db, m))
    elif a.cmd == "4h":
        print(desk.run_4h(db, m))
    elif a.cmd == "1h":
        print(desk.run_hourly(db, m))
    elif a.cmd == "weekly":
        print(desk.run_weekly(db))
    elif a.cmd == "status":
        for w in store.watchlist(db):
            print("WATCH", w["coin"], w["tag"], f"anchor {w['anchor_high']:.6g}")
        for t in store.trades(db, status="OPEN"):
            print("OPEN", t["id"], t["coin"], t["decision"], f"entry {t['entry']:.6g} stop {t['stop']:.6g}")
        print(auditor.render(auditor.report(db)))
    elif a.cmd == "journal":
        print(auditor.journal_csv(db))
    elif a.cmd == "decide":
        print("ok" if gate.decide(db, a.trade_id, a.decision.upper()) else "nicht moeglich (keine PENDING-ID)")


main()
