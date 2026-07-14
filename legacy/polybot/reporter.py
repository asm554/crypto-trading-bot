"""
Zentraler Summary-Reporter: sammelt Events aller Bots und schickt
alle 30 Minuten EINE zusammengefasste Telegram-Nachricht.

Einzelne send_telegram()-Aufrufe in den Bots werden durch
reporter.queue_event() ersetzt – sie landen im Puffer,
nicht sofort im Chat.
"""

import asyncio
import logging
import time
from collections import defaultdict
from polybot import config
from polybot.alerts import send_telegram

logger = logging.getLogger(__name__)

REPORT_INTERVAL_SEC = 30 * 60  # alle 30 Minuten

# Globaler Event-Puffer: {bot_name: [event_str, ...]}
_event_buffer: dict[str, list[str]] = defaultdict(list)
_stats: dict[str, dict] = {}   # {bot_name: {trades, pnl, ...}}
_last_report = 0.0
_lock = asyncio.Lock()


def queue_event(bot: str, event: str) -> None:
    """Fügt ein Event in den Puffer ein (non-blocking, thread-safe)."""
    _event_buffer[bot].append(event)
    # Maximal 5 Events pro Bot im Puffer halten
    if len(_event_buffer[bot]) > 5:
        _event_buffer[bot] = _event_buffer[bot][-5:]


def update_stats(bot: str, **kwargs) -> None:
    """Aktualisiert Performance-Kennzahlen für einen Bot."""
    if bot not in _stats:
        _stats[bot] = {}
    _stats[bot].update(kwargs)


async def _build_report() -> str:
    """Baut die zusammengefasste Nachricht aus Puffer + Stats."""
    lines = [f"📊 *Polybot Report* — {'PAPER' if config.PAPER_MODE else 'LIVE'}"]
    lines.append(f"🕐 {_timestamp()}\n")

    # ── Bot-Übersicht ──
    bot_order = ["DCA-Bot", "HFT/Whale", "Myriad Bot", "Smart Money"]
    best_bot = None
    best_pnl = float('-inf')

    for bot in bot_order:
        s = _stats.get(bot, {})
        trades = s.get("trades", 0)
        pnl = s.get("pnl", 0.0)
        sym = s.get("sym", "$")
        status = s.get("status", "aktiv")

        pnl_str = f"{pnl:+.2f}{sym}"
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{emoji} *{bot}*: {trades} Trades · PnL {pnl_str} · {status}")

        if trades > 0 and pnl > best_pnl:
            best_pnl = pnl
            best_bot = bot

    # ── Bester Bot ──
    if best_bot:
        lines.append(f"\n🏆 *Bester Bot*: {best_bot} ({best_pnl:+.2f})")

    # ── Letzte Events (max 1 pro Bot) ──
    has_events = any(len(v) > 0 for v in _event_buffer.values())
    if has_events:
        lines.append("\n📋 *Letzte Aktivität:*")
        for bot in bot_order:
            events = _event_buffer.get(bot, [])
            if events:
                # Nur letztes Event zeigen, kurz halten
                last = events[-1]
                short = last[:120] + "…" if len(last) > 120 else last
                lines.append(f"• {bot}: {short}")

    return "\n".join(lines)


async def flush_report() -> None:
    """Schickt sofort einen Report und leert den Puffer."""
    async with _lock:
        msg = await _build_report()
        await send_telegram(msg)
        _event_buffer.clear()


async def reporter_loop() -> None:
    """Haupt-Loop: schickt alle 30 Min einen Summary-Report."""
    global _last_report
    logger.info(f"📣 Reporter gestartet – Intervall {REPORT_INTERVAL_SEC//60} Min")

    # Kurz warten damit Bots sich initialisieren
    await asyncio.sleep(60)

    while True:
        now = time.time()
        if now - _last_report >= REPORT_INTERVAL_SEC:
            try:
                await flush_report()
                _last_report = now
            except Exception as e:
                logger.error(f"Reporter Fehler: {e}")
        await asyncio.sleep(60)


async def daily_analysis_loop() -> None:
    """Läuft täglich um 08:00 UTC — wertet alle Trades aus und zieht Schlüsse."""
    import sqlite3
    from datetime import datetime, timezone, timedelta
    from polybot.paper_db import DB_PATH

    logger.info("📅 Daily-Analyse Loop gestartet")
    while True:
        now = datetime.now(timezone.utc)
        # Nächsten 08:00 UTC berechnen
        next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        wait_sec = (next_run - now).total_seconds()
        await asyncio.sleep(wait_sec)

        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Alle gestrigen aufgelösten Trades
            cur.execute("""
                SELECT market_question, side, size, price, real_pnl, edge_percent
                FROM paper_trades
                WHERE DATE(timestamp, 'unixepoch') = ?
                AND real_pnl IS NOT NULL
            """, (yesterday,))
            trades = [dict(r) for r in cur.fetchall()]
            conn.close()

            if not trades:
                await send_telegram(f"📅 *Daily Report {yesterday}*\nKeine aufgelösten Trades gestern.")
                continue

            total = len(trades)
            wins = sum(1 for t in trades if (t["real_pnl"] or 0) > 0)
            losses = total - wins
            total_pnl = sum(t["real_pnl"] or 0 for t in trades)
            avg_edge = sum(t["edge_percent"] or 0 for t in trades) / total

            # Beste und schlechteste Trades
            best = max(trades, key=lambda t: t["real_pnl"] or 0)
            worst = min(trades, key=lambda t: t["real_pnl"] or 0)

            # Bot-Breakdown
            bot_pnl: dict[str, float] = {}
            for t in trades:
                mq = t["market_question"] or ""
                if mq.startswith("DCA_"):    bot = "DCA-Bot"
                elif mq.startswith("JUP_") or mq.startswith("SM_"): bot = "Smart Money"
                else:                         bot = "HFT/Whale"
                bot_pnl[bot] = bot_pnl.get(bot, 0) + (t["real_pnl"] or 0)

            # Learnings generieren
            learnings = []
            if losses > wins:
                learnings.append("⚠️ Mehr Verluste als Gewinne — Edge-Schwelle prüfen")
            if avg_edge < 1.0:
                learnings.append("📉 Ø Edge unter 1% — Signalqualität prüfen")
            if (worst["real_pnl"] or 0) < -5:
                learnings.append(f"🔴 Größter Verlust: {worst['market_question']} ({worst['real_pnl']:+.2f}$) — Stop-Loss erwägen")
            if not learnings:
                learnings.append("✅ Strategie läuft stabil — keine Anpassungen nötig")

            ranking = sorted(bot_pnl.items(), key=lambda x: x[1], reverse=True)
            rank_str = " · ".join(f"{b}: {p:+.2f}$" for b, p in ranking)

            msg = (
                f"📅 *Daily Report {yesterday}*\n\n"
                f"📊 Trades: {total} ({wins}W / {losses}L) | Winrate: {wins/total*100:.0f}%\n"
                f"💰 Gesamt-PnL: {total_pnl:+.2f}$ | Ø Edge: {avg_edge:.1f}%\n"
                f"🏆 Bester Trade: {best['market_question']} ({best['real_pnl']:+.2f}$)\n\n"
                f"🤖 Bot-Ranking: {rank_str}\n\n"
                f"🧠 *Learnings:*\n" + "\n".join(learnings)
            )
            await send_telegram(msg)

        except Exception as e:
            logger.error(f"Daily-Analyse Fehler: {e}")


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
