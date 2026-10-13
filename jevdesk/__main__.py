import argparse
import logging

from . import book, desk, telegram
from .config import CYCLE_SECONDS, MONITOR_SECONDS, PAPER
from .judge import JevJudge, JudgeError, RuleJudge


def make_judge():
    try:
        j = JevJudge()
        print("Judge: Jev")
        return j
    except JudgeError:
        j = RuleJudge()
        j.needs_hints = True
        print("Judge: Regel-Fallback (kein TYPESAFE_API_KEY) - Log-Modell 'rules-fallback'")
        return j


def main():
    p = argparse.ArgumentParser(prog="jevdesk", description="Paper-Desk fuer frische DEX-Token (keine Orders)")
    p.add_argument("cmd", choices=["run", "once", "monitor", "status", "pos", "report", "web", "replay"])
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if a.cmd == "web":
        from . import web
        web.serve()
        return
    db = book.connect()
    if a.cmd == "run":
        from . import web
        web.start_background()
        desk.run_forever(db, make_judge(), desk.LiveSources(), PAPER, CYCLE_SECONDS, MONITOR_SECONDS)
    elif a.cmd == "once":
        print(desk.run_once(db, make_judge(), desk.LiveSources()))
    elif a.cmd == "monitor":
        print(desk.run_monitor(db, desk.LiveSources()))
    elif a.cmd == "status":
        print(telegram.status_text(db, PAPER))
    elif a.cmd == "pos":
        print(telegram.pos_text(db))
    elif a.cmd == "replay":
        from . import record
        print(record.render(record.summary(db)))
    elif a.cmd == "report":
        print(telegram.handle(db, PAPER, "/report"))


main()
