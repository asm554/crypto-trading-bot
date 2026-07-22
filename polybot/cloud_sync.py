"""
Cloud-Sync: Spiegelt die lokale Paper-Trading-Datenbank periodisch nach Supabase,
damit das Online-Dashboard (z.B. auf Vercel) Live-Daten anzeigen kann.

Läuft komplett unabhängig von den Trading-Bots (main_dca/momentum/meanrev) und
schreibt nicht in deren Zustand zurück — ein Fehler hier stoppt niemals den Handel.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime

import aiohttp
from aiohttp import BasicAuth

from polybot import config  # lädt polybot/.env zuverlässig (siehe config.py)
from polybot import paper_db as paper_db_module

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

BATCH_SIZE = 500

FT_CONFIG_PATH = os.getenv(
    "FREQTRADE_CONFIG_PATH",
    "/root/crypto-trading-bot/freqtrade/user_data/config.paper.json",
)
FT_BOT_KEY = "freqtrade"
FT_RUNTIME_KEY = "__runtime_freqtrade"
FT_TRADE_ID_BASE = 3_000_000_000
FT_SNAPSHOT_ID_BASE = 4_000_000_000


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


def _ft_timestamp(value) -> float:
    """Normalisiert Freqtrade-Zeitwerte (Unix, Millisekunden oder ISO-8601)."""
    if value is None or value == "":
        return 0.0
    try:
        numeric = float(value)
        return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


async def sync_freqtrade_once(session: aiohttp.ClientSession) -> dict:
    """Spiegelt ausschließlich lesend Freqtrade-Paper-Daten nach Supabase."""
    if not os.path.isfile(FT_CONFIG_PATH):
        return {"skipped": "Freqtrade-Paper-Konfiguration nicht vorhanden"}
    try:
        with open(FT_CONFIG_PATH, encoding="utf-8") as fh:
            ft_config = json.load(fh)

        if ft_config.get("dry_run") is not True:
            raise RuntimeError("Freqtrade-Sync verweigert: dry_run ist nicht aktiv")

        api_config = ft_config["api_server"]
        base_url = "http://127.0.0.1:{}".format(api_config.get("listen_port", 8080))
        auth = BasicAuth(str(api_config["username"]), str(api_config["password"]))

        async def get(path: str):
            async with session.get(
                base_url + path,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status >= 300:
                    raise RuntimeError(f"Freqtrade API {path}: HTTP {response.status}")
                return await response.json()

        status_rows, balance, profit, trades_payload = await asyncio.gather(
            get("/api/v1/status"),
            get("/api/v1/balance"),
            get("/api/v1/profit"),
            get("/api/v1/trades?limit=1000"),
        )

        trades = trades_payload.get("trades", []) if isinstance(trades_payload, dict) else []
        now = time.time()
        start_capital = float(ft_config.get("dry_run_wallet", {}).get("EUR", 1_000.0))
        equity = float(balance.get("total", start_capital))
        free_cash = next(
            (
                float(currency.get("free", 0.0))
                for currency in balance.get("currencies", [])
                if currency.get("currency") == "EUR"
            ),
            equity,
        )
        realized_pnl = float(profit.get("profit_closed_fiat", 0.0) or 0.0)
        total_pnl = float(profit.get("profit_all_fiat", realized_pnl) or 0.0)
        unrealized_pnl = total_pnl - realized_pnl
        open_count = len(status_rows) if isinstance(status_rows, list) else 0

        normalized_trades = []
        for trade in trades:
            raw_id = trade.get("trade_id", trade.get("id"))
            try:
                trade_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            opened_at = _ft_timestamp(trade.get("open_date_ts", trade.get("open_date"))) or now
            closed_at = _ft_timestamp(trade.get("close_date_ts", trade.get("close_date")))
            amount = float(trade.get("amount", 0.0) or 0.0)
            open_rate = float(trade.get("open_rate", trade.get("open_rate_requested", 0.0)) or 0.0)
            close_rate = trade.get("close_rate")
            trade_pnl = trade.get("close_profit_abs", trade.get("profit_abs"))
            normalized_trades.append(
                {
                    "id": FT_TRADE_ID_BASE + trade_id,
                    "timestamp": opened_at,
                    "market_question": "FT_" + str(trade.get("pair", "UNKNOWN")),
                    "side": "sell" if trade.get("is_short") else "buy",
                    "size": amount,
                    "price": open_rate,
                    "edge_percent": float(trade.get("profit_ratio", 0.0) or 0.0) * 100,
                    "status": "closed" if closed_at else "open",
                    "exit_price": float(close_rate) if close_rate is not None else None,
                    "resolved_at": closed_at or None,
                    "real_pnl": float(trade_pnl) if trade_pnl is not None else None,
                    "unrealized_pnl": None,
                }
            )

        timestamp_bucket = int(now // 30)
        snapshots = [
            {
                "id": FT_SNAPSHOT_ID_BASE + timestamp_bucket,
                "bot": FT_BOT_KEY,
                "ts": now,
                "equity_eur": equity,
                "cash_eur": free_cash,
                "open_positions": open_count,
                "unrealized_pnl_eur": unrealized_pnl,
                "realized_pnl_eur": realized_pnl,
            },
            {
                "id": FT_SNAPSHOT_ID_BASE + 1_000_000_000 + timestamp_bucket,
                "bot": FT_RUNTIME_KEY,
                "ts": now,
                "equity_eur": 0.0,
                "cash_eur": 0.0,
                "open_positions": 0,
                "unrealized_pnl_eur": 0.0,
                "realized_pnl_eur": 0.0,
            },
        ]

        written_trades = await _upsert(
            session, "paper_trades", normalized_trades, on_conflict="id"
        )
        written_snapshots = await _upsert(
            session, "equity_snapshots", snapshots, on_conflict="id"
        )
        logger.info(
            "Freqtrade read-only sync: %d Trades, equity %.2f EUR, %d offen",
            written_trades,
            equity,
            open_count,
        )
        return {
            "trades": written_trades,
            "snapshots": written_snapshots,
            "equity": equity,
            "open": open_count,
        }
    except Exception as exc:
        logger.warning("Freqtrade read-only sync fehlgeschlagen: %s", exc)
        return {"error": str(exc)}


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
                trade = dict(row)
                if trade["resolved_at"] is not None:
                    trade["unrealized_pnl"] = None
                trades.append(trade)
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

    result = {
        "trades": trades_written,
        "snapshots": snaps_written,
        "freqtrade": freqtrade,
        "ts": time.time(),
    }
    logger.info("☁️ Cloud-Sync: %d Trades, %d Equity-Snapshots übertragen", trades_written, snaps_written)
    return result
