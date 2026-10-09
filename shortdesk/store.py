"""Eigene SQLite-DB (shortdesk/data/shortdesk.db). Keine Verbindung zu polybot/paper_trades.db."""
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("SHORTDESK_DB", Path(__file__).parent / "data" / "shortdesk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS watchlist (
  coin TEXT PRIMARY KEY, anchor_high REAL, anchor_ts INTEGER, flip_ts INTEGER, tag TEXT, updated INTEGER);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coin TEXT, zone_key TEXT, zone_top REAL, zone_bottom REAL,
  entry REAL, stop REAL, size REAL, risk_usd REAL, risk_pct REAL, add_number INTEGER,
  anchor_high REAL, created_ts INTEGER,
  decision TEXT DEFAULT 'PENDING',          -- PENDING | TAKEN | SKIPPED
  status TEXT DEFAULT 'OPEN',               -- OPEN | CLOSED
  exit_price REAL, exit_ts INTEGER, exit_reason TEXT,
  gross_usd REAL, fee_usd REAL, slip_usd REAL, pnl_usd REAL, r_multiple REAL,
  stale_flagged INTEGER DEFAULT 0,
  UNIQUE (coin, zone_key));
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def get_meta(db, k, default=None):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_meta(db, k, v):
    db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    db.commit()


def upsert_watch(db, w: dict):
    db.execute(
        "INSERT INTO watchlist(coin,anchor_high,anchor_ts,flip_ts,tag,updated) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(coin) DO UPDATE SET anchor_high=excluded.anchor_high, anchor_ts=excluded.anchor_ts, "
        "flip_ts=excluded.flip_ts, tag=excluded.tag, updated=excluded.updated",
        (w["coin"], w["anchor_high"], w["anchor_ts"], w["flip_ts"], w["tag"], int(time.time())))
    db.commit()


def drop_watch(db, coin):
    db.execute("DELETE FROM watchlist WHERE coin=?", (coin,))
    db.commit()


def watchlist(db) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM watchlist ORDER BY coin").fetchall()


def insert_trade(db, t: dict) -> int | None:
    try:
        cur = db.execute(
            "INSERT INTO trades(coin,zone_key,zone_top,zone_bottom,entry,stop,size,risk_usd,risk_pct,add_number,"
            "anchor_high,created_ts) VALUES(:coin,:zone_key,:zone_top,:zone_bottom,:entry,:stop,:size,:risk_usd,"
            ":risk_pct,:add_number,:anchor_high,:created_ts)", t)
    except sqlite3.IntegrityError:
        return None
    db.commit()
    return cur.lastrowid


def trades(db, **where) -> list[sqlite3.Row]:
    q, args = "SELECT * FROM trades", []
    if where:
        q += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        args = list(where.values())
    return db.execute(q + " ORDER BY id", args).fetchall()


def equity(db, account_usd: float) -> float:
    r = db.execute("SELECT COALESCE(SUM(pnl_usd),0) s FROM trades WHERE decision='TAKEN' AND status='CLOSED'").fetchone()
    return account_usd + r["s"]
