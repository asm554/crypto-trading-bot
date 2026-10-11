"""SQLite (jevdesk/data/jevdesk.db): Position, Bench, Paper-Trades, Zyklus-Log."""
import json
import os
import sqlite3
import time
from pathlib import Path

from .config import BENCH_MINUTES, DEFAULT_BENCH

DB_PATH = Path(os.environ.get("JEVDESK_DB", Path(__file__).parent / "data" / "jevdesk.db"))
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS bench (tid TEXT PRIMARY KEY, reason TEXT, until REAL);
CREATE TABLE IF NOT EXISTS cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, outcome TEXT, stats TEXT);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tid TEXT, ticker TEXT, chain TEXT, addr TEXT, pool TEXT,
  opened_ts REAL, entry_price REAL, units REAL, ticket_usd REAL, liquidity_usd REAL,
  size_factor REAL, confidence REAL, model TEXT, why TEXT,
  status TEXT DEFAULT 'OPEN', exit_price REAL, exit_ts REAL, exit_reason TEXT,
  pnl_usd REAL, pnl_pct REAL);
"""


def connect(path=None) -> sqlite3.Connection:
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


def held(db):
    return db.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY id LIMIT 1").fetchone()


def benched(db, tid: str, now: float | None = None) -> bool:
    r = db.execute("SELECT until FROM bench WHERE tid=?", (tid,)).fetchone()
    return bool(r and r["until"] > (time.time() if now is None else now))


def sit(db, tid: str, reason: str, now: float | None = None):
    mins = BENCH_MINUTES.get(reason, DEFAULT_BENCH)
    until = (time.time() if now is None else now) + mins * 60
    db.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)", (tid, reason, until))
    db.commit()


def open_trade(db, order: dict, fill: dict, now: float | None = None) -> int:
    d = order["token"]
    cur = db.execute(
        "INSERT INTO trades(tid,ticker,chain,addr,pool,opened_ts,entry_price,units,ticket_usd,liquidity_usd,"
        "size_factor,confidence,model,why) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["tid"], d["ticker"], d["chain"], d["addr"], d["pool"], time.time() if now is None else now,
         fill["price"], fill["units"], fill["ticket"], d["liquidity_usd"], order["size_factor"],
         order["confidence"], order["model"], json.dumps(order["answers"])))
    db.commit()
    return cur.lastrowid


def close_trade(db, trade_id: int, exit_: dict, now: float | None = None):
    db.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_ts=?, exit_reason=?, pnl_usd=?, pnl_pct=? WHERE id=?",
               (exit_["price"], time.time() if now is None else now, exit_["reason"], exit_["pnl_usd"],
                exit_["pnl_pct"], trade_id))
    db.commit()


def log_cycle(db, outcome: str, stats: dict, now: float | None = None):
    db.execute("INSERT INTO cycles(ts,outcome,stats) VALUES(?,?,?)",
               (time.time() if now is None else now, outcome, json.dumps(stats)))
    db.commit()


def realized_pnl(db) -> float:
    return db.execute("SELECT COALESCE(SUM(pnl_usd),0) s FROM trades WHERE status='CLOSED'").fetchone()["s"]


def stats(db) -> dict:
    rows = db.execute("SELECT pnl_usd, pnl_pct FROM trades WHERE status='CLOSED'").fetchall()
    n = len(rows)
    return {"closed": n, "win_rate": (sum(1 for r in rows if r["pnl_usd"] > 0) / n) if n else 0.0,
            "avg_pct": (sum(r["pnl_pct"] for r in rows) / n) if n else 0.0,
            "total_usd": sum(r["pnl_usd"] for r in rows)}
