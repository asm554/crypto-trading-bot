"""Routinen: verdrahtet die sechs Rollen. Die Rollen sprechen nur ueber die DB miteinander."""
import time

from . import auditor, cartographer, exitclerk, gate, risk, screener, store
from .config import RULES, Rules


class _Cache:
    def __init__(self, market):
        self.market, self.data = market, {}

    def get(self, coin, interval):
        k = (coin, interval)
        if k not in self.data:
            self.data[k] = self.market.candles(coin, interval)
        return self.data[k]


def run_exits(db, market, rules: Rules = RULES, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    cache, out = _Cache(market), []
    for t in store.trades(db, status="OPEN"):
        if t["decision"] not in ("TAKEN", "SKIPPED"):
            continue
        c1h, c1d = cache.get(t["coin"], 60), cache.get(t["coin"], 1440)
        if not c1h:
            continue
        ev = exitclerk.evaluate(t, c1h, c1d, c1h[-1].c, now, rules)
        if not ev:
            continue
        tag = "" if t["decision"] == "TAKEN" else " [SKIPPED/Schatten]"
        if ev["action"] == "STALE":
            db.execute("UPDATE trades SET stale_flagged=1 WHERE id=?", (t["id"],))
            db.commit()
            if t["decision"] == "TAKEN":
                msg = f"STALE #{t['id']} {t['coin']}: {rules.stale_hours}h flat - du entscheidest."
                gate.send(msg); out.append(msg)
            continue
        res = exitclerk.settle(t, ev["price"], ev["ts"], ev["reason"], rules)
        db.execute("UPDATE trades SET status='CLOSED', exit_price=:exit_price, exit_ts=:exit_ts, exit_reason=:exit_reason,"
                   " gross_usd=:gross_usd, fee_usd=:fee_usd, slip_usd=:slip_usd, pnl_usd=:pnl_usd, r_multiple=:r_multiple"
                   " WHERE id=:id", {**res, "id": t["id"]})
        db.commit()
        if t["decision"] == "TAKEN":
            msg = f"EXIT NOW #{t['id']} {t['coin']}: {ev['reason']} @ {ev['price']:.6g} ({res['r_multiple']:+.2f}R)"
            gate.send(msg); out.append(msg)
        else:
            out.append(f"exit{tag} #{t['id']} {ev['reason']} {res['r_multiple']:+.2f}R")
    return out


def run_daily(db, market, rules: Rules = RULES) -> dict:
    flips, bull = [], []
    for coin in rules.universe:
        try:
            r = screener.scan(coin, market.candles(coin, 1440), market.candles(coin, 240),
                              rules.flip_max_age_days, rules.bounce_bars_4h)
        except Exception as e:  # ein Coin-Fehler stoppt nie den Scan
            print(f"[screener] {coin}: {e}")
            continue
        if r["short"]:
            store.upsert_watch(db, r["short"]); flips.append(coin)
        else:
            store.drop_watch(db, coin)
        if r["bull_context"]:
            bull.append(coin)
    return {"flips": flips, "bull_context": bull, "exits": run_exits(db, market, rules)}


def run_hourly(db, market, rules: Rules = RULES) -> dict:
    gate.poll_replies(db)
    gate.expire_pending(db, rules.pending_expiry_hours)
    new = []
    for w in store.watchlist(db):
        coin = w["coin"]
        try:
            c4h, c1h = market.candles(coin, 240), market.candles(coin, 60)
        except Exception as e:
            print(f"[cartographer] {coin}: {e}")
            continue
        for zone in cartographer.find_fvgs(c4h, w["anchor_ts"]):
            conf = cartographer.confirm_1h(zone, c1h)
            if not conf:
                continue
            stop = zone.top * (1 + rules.stop_buffer_pct / 100)
            taken_open = [t for t in store.trades(db, coin=coin, decision="TAKEN", status="OPEN")]
            pending = len(store.trades(db, coin=coin, decision="PENDING"))
            plan = risk.build_plan(rules, store.equity(db, rules.account_usd), conf["entry"], stop,
                                   taken_open, pending, c1h[-1].c)
            if not plan["ok"]:
                print(f"[risk] {coin} {zone.key}: {plan['reason']}")
                continue
            tid = store.insert_trade(db, {
                "coin": coin, "zone_key": zone.key, "zone_top": zone.top, "zone_bottom": zone.bottom,
                "entry": conf["entry"], "stop": stop, "size": plan["size"], "risk_usd": plan["risk_usd"],
                "risk_pct": plan["risk_pct"], "add_number": plan["add_number"],
                "anchor_high": w["anchor_high"], "created_ts": conf["confirm_ts"] + 3600})
            if tid:
                gate.alert(store.trades(db, id=tid)[0]); new.append(tid)
            break  # pro Coin und Zyklus ein Plan
    return {"new_plans": new, "exits": run_exits(db, market, rules)}


def run_4h(db, market, rules: Rules = RULES) -> dict:
    gate.poll_replies(db)
    return {"exits": run_exits(db, market, rules)}


def run_weekly(db, rules: Rules = RULES) -> str:
    text = auditor.render(auditor.report(db, rules))
    gate.send("Auditor-Wochenreport\n" + text)
    return text
