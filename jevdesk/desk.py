"""Zyklus: Universe -> Gratis-Filter -> Dossier -> Fakten -> Judge -> Pick -> Paper-Position. Plus Monitor."""
import logging
import time

from . import book, filters, paper, pick as pickmod, sources, state as jstate, telegram
from .config import (CHAIN_SET, GT_DOSSIER, GT_UNIVERSE_PAGES, NETS, PAPER, PaperRules)
from .judge import JudgeError

log = logging.getLogger("jevdesk")


class LiveSources:
    def universe(self):
        return sources.universe(NETS, GT_UNIVERSE_PAGES)

    def dossier(self, t):
        return sources.dossier(t)

    def refresh(self, trade):
        return sources.refresh_pool(trade["chain"], trade["pool"])


def run_once(db, judge, src, rules: PaperRules = PAPER, notify=telegram.send, now: float | None = None):
    now = time.time() if now is None else now
    if (h := book.held(db)):
        book.log_cycle(db, f"held {h['ticker']}", {}, now)
        return None
    stats = {"seen": 0, "benched": 0, "free": {}, "chain": {}, "soft": {}, "dossiers": 0}
    bank = rules.bank_usd + book.realized_pnl(db)
    survivors, slots = [], GT_DOSSIER
    try:
        tokens = src.universe()
    except Exception as e:
        book.log_cycle(db, f"universe failed: {e}", stats, now)
        return None
    for t in tokens:
        stats["seen"] += 1
        if book.benched(db, t["tid"], now):
            stats["benched"] += 1
            continue
        if (k := filters.free_kill(t)):
            book.sit(db, t["tid"], k, now)
            stats["free"][k] = stats["free"].get(k, 0) + 1
            continue
        if slots <= 0:
            break
        slots -= 1
        try:
            d = src.dossier(t)
            stats["dossiers"] += 1
        except Exception as e:
            log.warning("dossier failed %s: %s", t["ticker"], e)
            book.sit(db, t["tid"], "dossier_failed", now)
            continue
        if (k := filters.chain_kill(d)):
            book.sit(db, t["tid"], k, now)
            stats["chain"][k] = stats["chain"].get(k, 0) + 1
            continue
        ticket_max = bank * rules.ticket_pct_of_bank / 100
        ans = {}
        try:
            ans |= judge.ask("market", jstate.market(d, ticket_max))["answers"]
            ans |= judge.ask(CHAIN_SET[d["chain"]], jstate.chain(d))["answers"]
        except JudgeError as e:
            book.log_cycle(db, f"judge unavailable: {e}", stats, now)   # Judge weg -> Desk steht, kein Raten
            return None
        except Exception as e:
            log.warning("judge failed %s: %s", d["ticker"], e)
            continue                                                       # nicht am Token, keine Bench
        if (k := filters.soft_kill(ans)):
            book.sit(db, t["tid"], k, now)
            stats["soft"][k] = stats["soft"].get(k, 0) + 1
            continue
        survivors.append((d, ans))

    if not survivors:
        book.log_cycle(db, "no survivors", stats, now)
        return None
    try:
        if len(survivors) == 1:     # Choice ueber eine Option beweist nichts
            order = pickmod.finish(survivors[0], "single-survivor", None)
        else:
            order = pickmod.pick(judge, survivors)
    except JudgeError as e:
        book.log_cycle(db, f"judge unavailable (pick): {e}", stats, now)
        return None
    if not order or "none" in order:
        book.log_cycle(db, f"no trade: {order['none'] if order else 'empty'}", {**stats, "pick": order}, now)
        return None

    d = order["token"]
    ticket = paper.size_ticket(bank, d["liquidity_usd"], order["size_factor"], rules)
    if ticket < rules.min_ticket_usd or not d.get("price_usd"):
        book.log_cycle(db, f"no trade: ticket {ticket:.2f} too small", stats, now)
        return None
    fill = paper.fill_buy(d["price_usd"], ticket)
    tid = book.open_trade(db, order, fill, now)
    book.log_cycle(db, f"ENTRY {d['ticker']}", stats, now)
    notify(f"PAPER-ENTRY #{tid} {d['ticker']} ({d['chain']})\nTicket ${ticket:.2f} | Preis {fill['price']:.8g}\n"
           f"Modell {order['model']} | Confidence {order['confidence']} | Size-Faktor {order['size_factor']}\n{d['addr']}")
    return tid


def run_monitor(db, src, rules: PaperRules = PAPER, notify=telegram.send, now: float | None = None):
    now = time.time() if now is None else now
    h = book.held(db)
    if not h:
        return None
    try:
        cur = src.refresh(h)
    except Exception as e:
        log.warning("refresh failed: %s", e)
        cur = None
        misses = int(book.get_meta(db, "refresh_misses", 0)) + 1
        book.set_meta(db, "refresh_misses", misses)
        if misses < 3:                  # zwei Fehlversuche tolerieren, dann zaehlt es als nicht messbar
            return None
    else:
        book.set_meta(db, "refresh_misses", 0)
    reason = paper.exit_reason(h, cur, now, rules)
    if not reason:
        return None
    price = (cur or {}).get("price_usd") or h["entry_price"]
    ex = paper.fill_sell(h, price)
    book.close_trade(db, h["id"], {**ex, "reason": reason}, now)
    book.set_meta(db, "refresh_misses", 0)
    notify(f"PAPER-EXIT #{h['id']} {h['ticker']}: {reason}\nErgebnis {ex['pnl_pct']:+.1f}% (${ex['pnl_usd']:+.2f})")
    return reason


def run_forever(db, judge, src, rules: PaperRules = PAPER, cycle_s: int = 900, monitor_s: int = 300):
    nxt_cycle = nxt_mon = 0.0
    last_digest = float(book.get_meta(db, "last_digest", 0))
    while True:
        now = time.time()
        try:
            telegram.poll_commands(db, rules)
            if now >= nxt_mon:
                run_monitor(db, src, rules)
                nxt_mon = now + monitor_s
            if now >= nxt_cycle:
                run_once(db, judge, src, rules)
                nxt_cycle = now + cycle_s
            if now - last_digest >= 86400:
                telegram.send("Tages-Digest\n" + telegram.status_text(db, rules))
                last_digest = now
                book.set_meta(db, "last_digest", now)
        except Exception:
            log.exception("loop error")
        time.sleep(15)
