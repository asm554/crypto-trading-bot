"""
Cloud-Sync: Spiegelt die lokale Paper-Trading-Datenbank periodisch nach Supabase,
damit das Online-Dashboard (z.B. auf Vercel) Live-Daten anzeigen kann.

Läuft komplett unabhängig von den Trading-Bots (main_dca/momentum/meanrev) und
schreibt nicht in deren Zustand zurück — ein Fehler hier stoppt niemals den Handel.
"""

import logging
import os
import time

import aiohttp

from polybot import config  # lädt polybot/.env zuverlässig (siehe config.py)
from polybot import paper_db as paper_db_module

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

BATCH_SIZE = 500


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


async def _upsert(session: aiohttp.ClientSession, table: str, rows: list[dict], on_conflict: str) -> int:
    """Schreibt Zeilen per REST-Upsert (einfügen oder aktualisieren) nach Supabase."""
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    written = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        async with session.post(url, headers=headers, json=batch, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status >= 300:
                text = await resp.text()
                logger.warning("Cloud-Sync %s fehlgeschlagen (%s): %s", table, resp.status, text[:300])
                continue
            written += len(batch)
    return written


async def sync_once() -> dict:
    """Liest die lokale DB und spiegelt paper_trades + equity_snapshots nach Supabase."""
    if not is_configured():
        return {"skipped": "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY nicht gesetzt"}

    aiosqlite = paper_db_module._require_aiosqlite()
    db_path = paper_db_module.DB_PATH

    trades: list[dict] = []
    snapshots: list[dict] = []


    async with aiosqlite.connect(db_path, timeout=30.0) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, timestamp, market_question, side, size, price, edge_percent, status, "
            "exit_price, resolved_at, real_pnl, unrealized_pnl FROM paper_trades"
        ) as cur:
            async for row in cur:
                trades.append(dict(row))
        async with db.execute(
            "SELECT id, bot, ts, equity_eur, cash_eur, open_positions, unrealized_pnl_eur, realized_pnl_eur "
            "FROM equity_snapshots"
        ) as cur:
            async for row in cur:
                snapshots.append(dict(row))


    async with aiohttp.ClientSession() as session:
        trades_written = await _upsert(session, "paper_trades", trades, on_conflict="id")
        snaps_written = await _upsert(session, "equity_snapshots", snapshots, on_conflict="id")

    result = {"trades": trades_written, "snapshots": snaps_written, "ts": time.time()}
    logger.info("☁️ Cloud-Sync: %d Trades, %d Equity-Snapshots übertragen", trades_written, snaps_written)
    return result
