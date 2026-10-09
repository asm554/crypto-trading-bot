"""Bot 4 - Gate: einziger Kanal zum Menschen. Telegram-Alert, Antwort TAKEN/SKIP. Nie eine Order."""
import os
import re
import time

import requests

from . import store

API = "https://api.telegram.org/bot{token}/{method}"


def _creds():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send(text: str) -> bool:
    token, chat = _creds()
    print(text)
    if not token or not chat:
        return False
    try:
        r = requests.post(API.format(token=token, method="sendMessage"),
                          data={"chat_id": chat, "text": text}, timeout=15)
        return r.ok
    except requests.RequestException:
        return False


def format_alert(t) -> str:
    return (f"#{t['id']} SHORT {t['coin']} | Zone {t['zone_bottom']:.6g}-{t['zone_top']:.6g} | "
            f"Entry {t['entry']:.6g} | Stop {t['stop']:.6g} | Size {t['size']:.6g} | "
            f"Risiko {t['risk_pct']:.2f}% (${t['risk_usd']:.2f}) | Add #{t['add_number']}\n"
            f"Antwort: 'TAKEN {t['id']}' oder 'SKIP {t['id']}'")


def alert(t) -> bool:
    return send(format_alert(t))


REPLY = re.compile(r"^/?(taken|take|skip|skipped)\s+#?(\d+)\s*$", re.I)


def parse_reply(text: str) -> tuple[int, str] | None:
    m = REPLY.match(text.strip())
    if not m:
        return None
    return int(m.group(2)), "TAKEN" if m.group(1).lower().startswith("take") else "SKIPPED"


def decide(db, trade_id: int, decision: str) -> bool:
    cur = db.execute("UPDATE trades SET decision=? WHERE id=? AND decision='PENDING'", (decision, trade_id))
    db.commit()
    return cur.rowcount == 1


def poll_replies(db) -> int:
    """Liest Telegram-Antworten aus dem konfigurierten Chat. Gibt Anzahl angewandter Entscheidungen zurueck."""
    token, chat = _creds()
    if not token or not chat:
        return 0
    offset = int(store.get_meta(db, "tg_offset", 0))
    try:
        r = requests.get(API.format(token=token, method="getUpdates"),
                         params={"offset": offset, "timeout": 0}, timeout=15)
        updates = r.json().get("result", []) if r.ok else []
    except (requests.RequestException, ValueError):
        return 0
    applied = 0
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue  # fremde Chats ignorieren
        parsed = parse_reply(msg.get("text", ""))
        if parsed and decide(db, *parsed):
            applied += 1
    store.set_meta(db, "tg_offset", offset)
    return applied


def expire_pending(db, hours: int, now: float | None = None) -> int:
    now = time.time() if now is None else now
    cur = db.execute("UPDATE trades SET decision='SKIPPED' WHERE decision='PENDING' AND created_ts<=?",
                     (int(now - hours * 3600),))
    db.commit()
    return cur.rowcount
