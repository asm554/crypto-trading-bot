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
        freqtrade = await sync_freqtrade_once(session)

    result = {"trades": trades_written, "snapshots": snaps_written, "freqtrade": freqtrade, "ts": time.time()}
    logger.info("☁️ Cloud-Sync: %d Trades, %d Equity-Snapshots übertragen", trades_written, snaps_written)
    return result


# ---------------------------------------------------------------------------
# Freqtrade read-only bridge
# ---------------------------------------------------------------------------

FT_CONFIG_PATH = os.getenv(
    "FREQTRADE_CONFIG_PATH",
    "/root/crypto-trading-bot/freqtrade/user_data/config.paper.json",
)
FT_BOT_KEY = "freqtrade"
FT_RUNTIME_KEY = "__runtime_freqtrade"
FT_TRADE_ID_BASE = 3_000_000_000
FT_SNAPSHOT_ID_BASE = 4_000_000_000


def _ft_timestamp(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


async def sync_freqtrade_once(session: aiohttp.ClientSession) -> dict:
    """Read-only Freqtrade API -> existing Supabase dashboard tables.

    This function never calls an order/control endpoint. It only GETs status,
    balance, profit, config and trades, then upserts normalized dashboard rows.
    """
    if not is_configured():
        return {"skipped": "Supabase nicht konfiguriert"}
    try:
        import json
        from aiohttp import BasicAuth
        with open(FT_CONFIG_PATH, encoding="utf-8") as fh:
            ft = json.load(fh)
        api = ft["api_server"]
        base = "http://{}:{}".format(api.get("listen_ip_address", "127.0.0.1"), api.get("listen_port", 8080))
        auth = BasicAuth(str(api["username"]), str(api["password"]))

        async def get(path):
            async with session.get(base + path, auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Freqtrade API {path}: HTTP {resp.status}")
                return await resp.json()

        status_rows, balance, profit, cfg = await __import__("asyncio").gather(
            get("/api/v1/status"), get("/api/v1/balance"),
            get("/api/v1/profit"), get("/api/v1/show_config"),
        )
        trades_payload = await get("/api/v1/trades?limit=1000")
        trades = trades_payload.get("trades", []) if isinstance(trades_payload, dict) else []
        now = time.time()
        start_capital = float(ft.get("dry_run_wallet", {}).get("EUR", 1000.0))
        equity = float(balance.get("total", start_capital))
        free_cash = next((float(c.get("free", 0.0)) for c in balance.get("currencies", []) if c.get("currency") == "EUR"), equity)
        realized = float(profit.get("profit_all_fiat", profit.get("profit_closed_fiat", 0.0)) or 0.0)
        open_count = len(status_rows) if isinstance(status_rows, list) else 0

        normalized=[]
        for row in trades:
            raw_id = row.get("trade_id", row.get("id"))
            try: raw_id=int(raw_id)
            except (TypeError,ValueError): continue
            opened = _ft_timestamp(row.get("open_date_ts", row.get("open_date"))) or now
            closed = _ft_timestamp(row.get("close_date_ts", row.get("close_date")))
            amount = float(row.get("amount", 0.0) or 0.0)
            open_rate = float(row.get("open_rate", row.get("open_rate_requested", 0.0)) or 0.0)
            close_rate = row.get("close_rate")
            close_profit = row.get("close_profit_abs", row.get("profit_abs"))
            normalized.append({
                "id": FT_TRADE_ID_BASE + raw_id,
                "timestamp": opened,
                "market_question": "FT_" + str(row.get("pair", "UNKNOWN")),
                "side": "buy",
                "size": amount,
                "price": open_rate,
                "edge_percent": float(row.get("profit_ratio", 0.0) or 0.0) * 100,
                "status": "closed" if closed else "open",
                "exit_price": float(close_rate) if close_rate is not None else None,
                "resolved_at": closed or None,
                "real_pnl": float(close_profit) if close_profit is not None else None,
                "unrealized_pnl": None,
            })

        ts_bucket=int(now // 30)
        snapshots=[
            {"id": FT_SNAPSHOT_ID_BASE + ts_bucket, "bot": FT_BOT_KEY, "ts": now,
             "equity_eur": equity, "cash_eur": free_cash, "open_positions": open_count,
             "unrealized_pnl_eur": max(0.0, equity - free_cash), "realized_pnl_eur": realized},
            {"id": FT_SNAPSHOT_ID_BASE + 1_000_000_000 + ts_bucket, "bot": FT_RUNTIME_KEY, "ts": now,
             "equity_eur": 0.0, "cash_eur": 0.0, "open_positions": 0,
             "unrealized_pnl_eur": 0.0, "realized_pnl_eur": 0.0},
        ]
        written_trades=await _upsert(session, "paper_trades", normalized, on_conflict="id")
        written_snaps=await _upsert(session, "equity_snapshots", snapshots, on_conflict="id")
        logger.info("Freqtrade read-only sync: %d Trades, equity %.2f EUR, %d offen", written_trades, equity, open_count)
        return {"trades": written_trades, "snapshots": written_snaps, "equity": equity, "open": open_count}
    except Exception as exc:
        logger.warning("Freqtrade read-only sync fehlgeschlagen: %s", exc)
        return {"error": str(exc)}
