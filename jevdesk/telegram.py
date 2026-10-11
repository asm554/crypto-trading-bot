"""Telegram: Alerts + Kommandos (/status /pos /report /help). Nur der konfigurierte Chat darf fragen."""
import os
import time

import requests

from . import book

API = "https://api.telegram.org/bot{token}/{method}"


def _creds():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send(text: str) -> bool:
    print(text)
    token, chat = _creds()
    if not token or not chat:
        return False
    try:
        return requests.post(API.format(token=token, method="sendMessage"),
                             data={"chat_id": chat, "text": text[:4000]}, timeout=15).ok
    except requests.RequestException:
        return False


def status_text(db, paper) -> str:
    h = book.held(db)
    s = book.stats(db)
    last = db.execute("SELECT ts, outcome, stats FROM cycles ORDER BY id DESC LIMIT 1").fetchone()
    lines = [f"Konto (Paper) ${paper.bank_usd + book.realized_pnl(db):,.2f} | geschlossen {s['closed']} | "
             f"Win {s['win_rate']:.0%} | Ø {s['avg_pct']:+.1f}% | Summe ${s['total_usd']:+.2f}"]
    lines.append(f"Position: {h['ticker']} ({h['chain']}) seit {(time.time() - h['opened_ts']) / 3600:.1f}h" if h else "Position: keine")
    lines.append(f"Letzter Zyklus: {last['outcome']}" if last else "Noch kein Zyklus gelaufen")
    return "\n".join(lines)


def pos_text(db) -> str:
    h = book.held(db)
    if not h:
        return "Keine offene Position."
    return (f"{h['ticker']} ({h['chain']})\nEntry {h['entry_price']:.8g} | Ticket ${h['ticket_usd']:.2f}\n"
            f"Modell {h['model']} | Confidence {h['confidence']}\n{h['addr']}")


def handle(db, paper, text: str) -> str | None:
    cmd = text.strip().split()[0].lower().split("@")[0] if text.strip() else ""
    if cmd in ("/status", "/start"):
        return status_text(db, paper)
    if cmd == "/pos":
        return pos_text(db)
    if cmd == "/report":
        s = book.stats(db)
        return (f"Geschlossene Paper-Trades: {s['closed']}\nWin-Rate {s['win_rate']:.0%}\n"
                f"Ø Ergebnis {s['avg_pct']:+.1f}%\nSumme ${s['total_usd']:+.2f}")
    if cmd == "/help":
        return "/status /pos /report"
    return None


def poll_commands(db, paper) -> int:
    token, chat = _creds()
    if not token or not chat:
        return 0
    offset = int(book.get_meta(db, "tg_offset", 0))
    try:
        r = requests.get(API.format(token=token, method="getUpdates"), params={"offset": offset, "timeout": 0}, timeout=15)
        updates = r.json().get("result", []) if r.ok else []
    except (requests.RequestException, ValueError):
        return 0
    n = 0
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue
        reply = handle(db, paper, msg.get("text", ""))
        if reply:
            send(reply)
            n += 1
    book.set_meta(db, "tg_offset", offset)
    return n
