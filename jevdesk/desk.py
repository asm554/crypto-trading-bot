"""Zyklus: Universe -> Gratis-Filter -> Dossier -> Fakten -> Judge -> Pick -> Paper-Position. Plus Monitor."""
import logging
import time

from . import book, filters, paper, pick as pickmod, record, sources, state as jstate, telegram
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

    def prices(self, chain, pools):
        return sources.pool_prices(chain, pools)


FAIL_ALERT_AFTER = 4          # 4 Zyklen = 1 Stunde ohne Daten/Judge -> einmal per Telegram melden


def _failed(db, msg, stats, notify, now):
    n = int(book.get_meta(db, "fail_streak", 0)) + 1
    book.set_meta(db, "fail_streak", n)
    book.log_cycle(db, msg, stats, now)
    if n == FAIL_ALERT_AFTER:
        notify(f"ACHTUNG jevdesk: seit {n} Zyklen kein normaler Lauf.\nLetzter Fehler: {msg[:300]}")


def _healthy(db, notify):
    if int(book.get_meta(db, "fail_streak", 0)) >= FAIL_ALERT_AFTER:
        notify("jevdesk laeuft wieder normal.")
    book.set_meta(db, "fail_streak", 0)


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
        _failed(db, f"universe failed: {e}", stats, notify, now)
        return None
    if not tokens:
        _failed(db, "keine Daten von GeckoTerminal (Limit oder Netz)", stats, notify, now)
        return None
    for t in tokens:
        try:
            stats["seen"] += 1
            if book.benched(db, t["tid"], now):
                stats["benched"] += 1
                continue
            if (k := filters.free_kill(t)):
                book.sit(db, t["tid"], k, now)
                stats["free"][k] = stats["free"].get(k, 0) + 1
                if k not in ("age",):                       # zu junge Token kommen spaeter ohnehin wieder
                    record.observe(db, t, "free", k, now=now)
                continue
            if slots <= 0:
                record.observe(db, t, "budget", now=now)   # bestand Gratis-Filter, aber kein Dossier-Slot frei
                continue
            slots -= 1
            try:
                d = src.dossier(t)
                stats["dossiers"] += 1
            except Exception as e:
                log.warning("dossier failed %s: %s", t["ticker"], e)
                book.sit(db, t["tid"], "dossier_failed", now)
                record.observe(db, t, "dossier_failed", now=now)
                continue
            if (k := filters.chain_kill(d)):
                book.sit(db, t["tid"], k, now)
                stats["chain"][k] = stats["chain"].get(k, 0) + 1
                record.observe(db, d, "chain", k, now=now)
                continue
            ticket_max = bank * rules.ticket_pct_of_bank / 100
            ans = {}
            try:
                r1 = judge.ask("market", jstate.market(d, ticket_max))
                ans |= r1["answers"]
                ans |= judge.ask(CHAIN_SET[d["chain"]], jstate.chain(d))["answers"]
                d["_model"] = r1.get("model")
            except JudgeError as e:
                _failed(db, f"judge unavailable: {e}", stats, notify, now)   # Judge weg -> Desk steht, kein Raten
                return None
            except Exception as e:
                log.warning("judge failed %s: %s", d["ticker"], e)
                continue                                                       # nicht am Token, keine Bench
            if (k := filters.soft_kill(ans)):
                book.sit(db, t["tid"], k, now)
                stats["soft"][k] = stats["soft"].get(k, 0) + 1
                record.observe(db, d, "soft", k, ans, d.get("_model"), now)
                continue
            survivors.append((d, ans))

        except Exception as e:                      # ein kaputter Datensatz darf nie den Zyklus stoppen
            log.warning("token %s skipped: %s", t.get("tid"), e)
            stats["errors"] = stats.get("errors", 0) + 1

    if stats.get("errors") and stats["errors"] >= max(3, stats["seen"] // 2):
        _failed(db, f"{stats['errors']} Token-Fehler in einem Zyklus", stats, notify, now)
        return None
    _healthy(db, notify)
    if not survivors:
        book.log_cycle(db, "no survivors", stats, now)
        return None
    try:
        if len(survivors) == 1:     # Choice ueber eine Option beweist nichts
            order = pickmod.finish(survivors[0], "single-survivor", None)
        else:
            order = pickmod.pick(judge, survivors)
    except JudgeError as e:
        _failed(db, f"judge unavailable (pick): {e}", stats, notify, now)
        return None
    def mark_survivors(chosen_tid, stage_for_chosen, reason=None):
        for sd, sa in survivors:
            if sd["tid"] == chosen_tid:
                record.observe(db, sd, stage_for_chosen, reason, sa, sd.get("_model"), now)
            else:
                record.observe(db, sd, "not_picked", None, sa, sd.get("_model"), now)

    if not order or "none" in order:
        reason = order["none"] if order else "empty"
        for sd, sa in survivors:
            record.observe(db, sd, "pick", reason, sa, sd.get("_model"), now)
        book.log_cycle(db, f"no trade: {reason}", {**stats, "pick": order}, now)
        return None

    d = order["token"]
    ticket = paper.size_ticket(bank, d["liquidity_usd"], order["size_factor"], rules)
    if ticket < rules.min_ticket_usd or not d.get("price_usd"):
        mark_survivors(d["tid"], "ticket", "too_small")
        book.log_cycle(db, f"no trade: ticket {ticket:.2f} too small", stats, now)
        return None
    mark_survivors(d["tid"], "entered")
    fill = paper.fill_buy(d["price_usd"], ticket, d["liquidity_usd"])
    tid = book.open_trade(db, order, fill, now)
    book.log_cycle(db, f"ENTRY {d['ticker']}", stats, now)
    notify(f"PAPER-ENTRY #{tid} {d['ticker']} ({d['chain']})\nTicket ${ticket:.2f} | Preis {fill['price']:.8g}\n"
           f"Modell {order['model']} | Confidence {order['confidence']} | Size-Faktor {order['size_factor']}\n{d['addr']}")
    return tid


def run_tracking(db, src, now: float | None = None) -> int:
    """Kurs-Nachmessung der Aufzeichnung. Fehler hier stoeren nie den Handel."""
    try:
        return record.track(db, src.prices, now)
    except Exception as e:
        log.warning("tracking failed: %s", e)
        return 0


def run_monitor(db, src, rules: PaperRules = PAPER, notify=telegram.send, now: float | None = None):
    now = time.time() if now is None else now
    h = book.held(db)
    if not h:
        return None
    try:
        cur = src.refresh(h)
    except Exception as e:
        log.warning("refresh failed: %s", e)    # Netz/Limit: nichts buchen, naechster Lauf misst neu
        return None
    if not cur or not cur.get("price_usd"):
        misses = int(book.get_meta(db, "gone_misses", 0)) + 1
        book.set_meta(db, "gone_misses", misses)
        if misses < 3:                          # dreimal in Folge nicht gelistet -> Pool gilt als weg
            return None
        cur, reason = {"price_usd": 0.0}, "GONE"
    else:
        book.set_meta(db, "gone_misses", 0)
        reason = None
    reason = reason or paper.exit_reason(h, cur, now, rules)
    if not reason:
        return None
    ex = paper.fill_sell(h, cur["price_usd"], cur.get("liquidity_usd") or h["liquidity_usd"])
    book.close_trade(db, h["id"], {**ex, "reason": reason}, now)
    book.set_meta(db, "gone_misses", 0)
    notify(f"PAPER-EXIT #{h['id']} {h['ticker']}: {reason}\nErgebnis {ex['pnl_pct']:+.1f}% (${ex['pnl_usd']:+.2f})")
    return reason


def run_forever(db, judge, src, rules: PaperRules = PAPER, cycle_s: int = 900, monitor_s: int = 300):
    nxt_cycle = nxt_mon = 0.0
    nxt_track = time.time() + 150          # versetzt zum Zyklus, damit GT-Abrufe sich nicht stapeln
    last_digest = float(book.get_meta(db, "last_digest", 0))
    while True:
        now = time.time()
        try:
            telegram.poll_commands(db, rules)
            if now >= nxt_mon:
                run_monitor(db, src, rules)
                nxt_mon = now + monitor_s
            if now >= nxt_track:
                run_tracking(db, src)
                nxt_track = now + monitor_s
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
