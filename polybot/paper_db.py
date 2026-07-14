import os
import time
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "paper_trades.db")

_AZURO_MIGRATION = """
    ALTER TABLE azuro_bets ADD COLUMN real_pnl REAL DEFAULT NULL
"""
_AZURO_MIGRATION_2 = """
    ALTER TABLE azuro_bets ADD COLUMN resolved_at REAL DEFAULT NULL
"""

SYNC_SCHEMA_STATEMENTS = (
    '''
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            market_question TEXT,
            side TEXT,
            size REAL,
            price REAL,
            edge_percent REAL,
            status TEXT,
            exit_price REAL DEFAULT NULL,
            resolved_at REAL DEFAULT NULL,
            real_pnl REAL DEFAULT NULL,
            unrealized_pnl REAL DEFAULT NULL
        )
    ''',
    '''
        CREATE TABLE IF NOT EXISTS whale_positions (
            wallet_address TEXT,
            market_slug TEXT,
            outcome TEXT,
            size REAL,
            value REAL,
            PRIMARY KEY (wallet_address, market_slug, outcome)
        )
    ''',
    '''
        CREATE TABLE IF NOT EXISTS arb_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            yes_token_id TEXT,
            no_token_id TEXT,
            yes_price REAL,
            no_price REAL,
            combined_cost REAL,
            profit_margin REAL,
            trade_size_usd REAL,
            yes_order_id TEXT,
            no_order_id TEXT,
            status TEXT DEFAULT 'paper'
        )
    ''',
    '''
        CREATE TABLE IF NOT EXISTS smart_money_positions (
            contract_address TEXT PRIMARY KEY,
            ticker TEXT,
            chain TEXT,
            side TEXT,
            size_usd REAL,
            entry_price REAL,
            entry_time REAL,
            status TEXT DEFAULT 'open'
        )
    ''',
    '''
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            ts REAL NOT NULL,
            equity_eur REAL NOT NULL,
            cash_eur REAL NOT NULL,
            open_positions INTEGER NOT NULL,
            unrealized_pnl_eur REAL NOT NULL,
            realized_pnl_eur REAL NOT NULL
        )
    ''',
    '''
        CREATE INDEX IF NOT EXISTS idx_equity_bot_ts ON equity_snapshots(bot, ts)
    ''',
    '''
        CREATE TABLE IF NOT EXISTS azuro_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            condition_id TEXT,
            outcome_id TEXT,
            game_title TEXT,
            sport TEXT,
            amount_usdc REAL,
            odds REAL,
            expected_value REAL,
            status TEXT DEFAULT 'paper'
        )
    ''',
)


def _require_aiosqlite():
    try:
        import aiosqlite
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "aiosqlite is required for async DB writes. Install dependencies from polybot/requirements.txt."
        ) from e
    return aiosqlite


def _ensure_sync_db():
    import sqlite3

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        # WAL + NORMAL: robuster bei paralleln Schreibern (mehrere Bot-Prozesse)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in SYNC_SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()

async def init_db():
    """Initializes the SQLite database correctly."""
    aiosqlite = _require_aiosqlite()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        # WAL + NORMAL: robuster bei paralleln Schreibern (mehrere Bot-Prozesse)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        for stmt in SYNC_SCHEMA_STATEMENTS:
            await db.execute(stmt)
        # Azuro-Spalten migrieren (safe: ignoriert falls schon vorhanden)
        for stmt in (_AZURO_MIGRATION, _AZURO_MIGRATION_2):
            try:
                await db.execute(stmt)
            except Exception:
                pass
        # unrealized_pnl-Spalte migrieren (safe: ignoriert falls schon vorhanden)
        try:
            await db.execute("ALTER TABLE paper_trades ADD COLUMN unrealized_pnl REAL DEFAULT NULL")
        except Exception:
            pass
        await db.commit()

async def log_paper_trade(market, side, size, price, edge, status="taken") -> int:
    """Logs a simulated trade to the database and returns its row id."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        cursor = await db.execute('''
            INSERT INTO paper_trades (timestamp, market_question, side, size, price, edge_percent, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (time.time(), market, side, size, price, edge * 100, status))
        await db.commit()
        trade_id = int(cursor.lastrowid or 0)
    logger.info(f"💾 Paper trade logged to DB: #{trade_id} {market} @ {price}")
    return trade_id

async def migrate_paper_trades_columns():
    """Fügt neue Spalten hinzu falls sie noch nicht existieren (safe migration)."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        for col, definition in [
            ("exit_price", "REAL DEFAULT NULL"),
            ("resolved_at", "REAL DEFAULT NULL"),
            ("real_pnl", "REAL DEFAULT NULL"),
            ("unrealized_pnl", "REAL DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {definition}")
                await db.commit()
                logger.info(f"Migration: Spalte '{col}' hinzugefügt.")
            except Exception:
                pass  # Spalte existiert bereits


async def get_unresolved_trades(min_age_sec: float) -> list[dict]:
    """Gibt alle Trades zurück die älter als min_age_sec sind und noch kein Ergebnis haben."""
    aiosqlite = _require_aiosqlite()
    cutoff = time.time() - min_age_sec
    rows = []
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT id, timestamp, market_question, side, size, price FROM paper_trades "
            "WHERE resolved_at IS NULL AND timestamp <= ?",
            (cutoff,)
        ) as cursor:
            async for row in cursor:
                rows.append({
                    "id": row[0], "timestamp": row[1], "market_question": row[2],
                    "side": row[3], "size": row[4], "entry_price": row[5],
                })
    return rows




async def get_open_dca_trades() -> list[dict]:
    """Gibt alle offenen DCA-Trades zurück."""
    aiosqlite = _require_aiosqlite()
    rows = []
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT id, timestamp, market_question, side, size, price, edge_percent, status FROM paper_trades "
            "WHERE market_question LIKE 'DCA_%' AND resolved_at IS NULL ORDER BY id ASC"
        ) as cursor:
            async for row in cursor:
                rows.append({
                    "id": row[0],
                    "timestamp": row[1],
                    "market_question": row[2],
                    "side": row[3],
                    "size": row[4],
                    "entry_price": row[5],
                    "edge_percent": row[6],
                    "status": row[7],
                })
    return rows

async def resolve_trade(trade_id: int, exit_price: float, real_pnl: float):
    """Speichert das echte Ergebnis eines Trades."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE paper_trades SET exit_price=?, resolved_at=?, real_pnl=? WHERE id=?",
            (exit_price, time.time(), real_pnl, trade_id)
        )
        await db.commit()
    logger.info(f"✅ Trade #{trade_id} aufgelöst: exit={exit_price:.4f} pnl={real_pnl:+.4f}$")


async def get_open_trades_by_prefix(prefix: str) -> list[dict]:
    """Gibt offene Paper-Trades mit Prefix zurück, z.B. DCA_, MOM_, REV_."""
    aiosqlite = _require_aiosqlite()
    rows = []
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT id, timestamp, market_question, side, size, price, edge_percent, status FROM paper_trades "
            "WHERE market_question LIKE ? AND resolved_at IS NULL ORDER BY id ASC",
            (f"{prefix}%",),
        ) as cursor:
            async for row in cursor:
                rows.append({
                    "id": row[0],
                    "timestamp": row[1],
                    "market_question": row[2],
                    "side": row[3],
                    "size": row[4],
                    "entry_price": row[5],
                    "edge_percent": row[6],
                    "status": row[7],
                })
    return rows


async def get_realized_pnl_by_prefix(prefix: str) -> float:
    """Summe realisierter PnL für Prefix."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT SUM(COALESCE(real_pnl,0)) FROM paper_trades WHERE market_question LIKE ? AND resolved_at IS NOT NULL",
            (f"{prefix}%",),
        ) as cursor:
            row = await cursor.fetchone()
    return float((row[0] if row else 0.0) or 0.0)


async def log_equity_snapshot(bot: str, equity_eur: float, cash_eur: float, open_positions: int, unrealized_pnl_eur: float, realized_pnl_eur: float) -> None:
    """Schreibt stündliche/daily Equity-Snapshots für Strategie-Battle."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "INSERT INTO equity_snapshots (bot, ts, equity_eur, cash_eur, open_positions, unrealized_pnl_eur, realized_pnl_eur) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bot, time.time(), float(equity_eur), float(cash_eur), int(open_positions), float(unrealized_pnl_eur), float(realized_pnl_eur)),
        )
        await db.commit()


async def get_paper_stats():
    """Echte Performance-Metriken aus aufgelösten Trades."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        # Heute: alle Trades
        async with db.execute(
            'SELECT COUNT(*) FROM paper_trades WHERE DATE(timestamp, "unixepoch") = DATE("now")'
        ) as cursor:
            count = (await cursor.fetchone())[0] or 0

        # Aufgelöste Trades: echter PnL, Winrate
        async with db.execute(
            "SELECT COUNT(*), SUM(real_pnl), "
            "SUM(CASE WHEN real_pnl > 0 THEN 1 ELSE 0 END) "
            "FROM paper_trades WHERE resolved_at IS NOT NULL"
        ) as cursor:
            row = await cursor.fetchone()
            resolved = row[0] or 0
            total_pnl = row[1] or 0.0
            wins = row[2] or 0

        winrate = (wins / resolved * 100) if resolved > 0 else 0.0

        async with db.execute('SELECT MIN(timestamp) FROM paper_trades') as cursor:
            row = await cursor.fetchone()
            start_ts = row[0] if row[0] else time.time()
            days = (time.time() - start_ts) / 86400

    return {
        "count": count,
        "resolved": resolved,
        "total_pnl": round(total_pnl, 4),
        "winrate": round(winrate, 1),
        "days_running": days,
    }

async def save_whale_positions(address, positions):
    """Saves current whale positions to DB."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        # Clear old positions for this wallet
        await db.execute('DELETE FROM whale_positions WHERE wallet_address = ?', (address,))
        for (slug, outcome), data in positions.items():
            await db.execute('''
                INSERT INTO whale_positions (wallet_address, market_slug, outcome, size, value)
                VALUES (?, ?, ?, ?, ?)
            ''', (address, slug, outcome, data["size"], data["value"]))
        await db.commit()

async def load_whale_states():
    """Loads all known whale positions from DB into memory."""
    aiosqlite = _require_aiosqlite()
    states = {}
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute('SELECT * FROM whale_positions') as cursor:
            async for row in cursor:
                addr = row[0]
                if addr not in states:
                    states[addr] = {}
                states[addr][(row[1], row[2])] = {
                    "size": row[3],
                    "value": row[4]
                }
    return states

def get_all_trades_sync():
    """Synchronous read for Dashboard (Flask)."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM paper_trades ORDER BY timestamp DESC LIMIT 50')
        rows = cursor.fetchall()
        trades = []
        total_pnl = 0.0
        for r in rows:
            trade = dict(r)
            # Echter PnL wenn aufgelöst, sonst None
            trade["pnl"] = trade.get("real_pnl")
            if trade["pnl"] is not None:
                total_pnl += trade["pnl"]
            trades.append(trade)
        conn.close()
        return trades, total_pnl
    except Exception as e:
        logger.error(f"Dashboard DB error: {e}")
        return [], 0.0

def get_whale_states_sync():
    """Synchronous read for Dashboard (Flask)."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM whale_positions ORDER BY value DESC LIMIT 20')
        rows = cursor.fetchall()
        whales = []
        for r in rows:
            whales.append(dict(r))
        conn.close()
        return whales
    except Exception as e:
        logger.error(f"Dashboard Whale DB error: {e}")
        return []


async def log_arb_trade(
    yes_token_id: str, no_token_id: str,
    yes_price: float, no_price: float,
    trade_size_usd: float,
    yes_order_id: str = "", no_order_id: str = "",
    status: str = "paper"
):
    """Loggt einen Paired-Arb-Trade in die DB."""
    aiosqlite = _require_aiosqlite()
    combined_cost = yes_price + no_price
    profit_margin = 1.0 - combined_cost
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute('''
            INSERT INTO arb_trades
              (timestamp, yes_token_id, no_token_id, yes_price, no_price,
               combined_cost, profit_margin, trade_size_usd, yes_order_id, no_order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (time.time(), yes_token_id, no_token_id, yes_price, no_price,
              combined_cost, profit_margin, trade_size_usd, yes_order_id, no_order_id, status))
        await db.commit()
    logger.info(f"💾 Arb trade logged: combined={combined_cost:.4f} margin={profit_margin*100:.2f}%")


async def get_open_azuro_bets() -> list[dict]:
    """Gibt alle offenen (nicht aufgelösten) Paper-Wetten zurück."""
    aiosqlite = _require_aiosqlite()
    rows = []
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT id, condition_id, outcome_id, game_title, amount_usdc, odds "
            "FROM azuro_bets WHERE resolved_at IS NULL AND status IN ('paper','filled')"
        ) as cursor:
            async for row in cursor:
                rows.append({
                    "id": row[0], "condition_id": row[1], "outcome_id": str(row[2]),
                    "game_title": row[3], "amount_usdc": row[4], "odds": row[5],
                })
    return rows


async def resolve_azuro_bet(bet_id: int, real_pnl: float) -> None:
    """Schreibt das Ergebnis einer Azuro-Wette (PnL + Zeitstempel)."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE azuro_bets SET real_pnl=?, resolved_at=?, status='resolved' WHERE id=?",
            (real_pnl, time.time(), bet_id)
        )
        await db.commit()
    logger.info(f"✅ Azuro Wette #{bet_id} aufgelöst: pnl={real_pnl:+.4f}$")


def get_arb_trades_sync(limit: int = 20) -> list:
    """Synchronous read für Dashboard (Flask)."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM arb_trades ORDER BY timestamp DESC LIMIT ?', (limit,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Arb DB read error: {e}")
        return []


async def log_azuro_bet(condition_id: str, outcome_id: str, game_title: str,
                        sport: str, amount_usdc: float, odds: float,
                        expected_value: float, status: str = "paper"):
    """Loggt eine simulierte Azuro-Wette."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute('''
            INSERT INTO azuro_bets
              (timestamp, condition_id, outcome_id, game_title, sport,
               amount_usdc, odds, expected_value, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (time.time(), condition_id, outcome_id, game_title, sport,
              amount_usdc, odds, expected_value, status))
        await db.commit()
    logger.info(f"💾 Azuro bet logged: {game_title} #{outcome_id} ${amount_usdc:.2f} @ {odds:.3f}x")


def get_azuro_bets_sync(limit: int = 20) -> list[dict]:
    """Synchronous read für Dashboard (Flask)."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM azuro_bets ORDER BY timestamp DESC LIMIT ?', (limit,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Azuro DB read error: {e}")
        return []


# ==========================================
# SMART MONEY POSITIONS
# ==========================================

async def save_sm_position(contract: str, ticker: str, chain: str,
                           size_usd: float, entry_price: float):
    """Persistiert eine aktive Smart Money Position."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute('''
            INSERT OR REPLACE INTO smart_money_positions
              (contract_address, ticker, chain, side, size_usd, entry_price, entry_time, status)
            VALUES (?, ?, ?, 'BUY', ?, ?, ?, 'open')
        ''', (contract, ticker, chain, size_usd, entry_price, time.time()))
        await db.commit()
    logger.info(f"💾 SM Position saved: {ticker} ${size_usd:.2f}")


async def close_sm_position(contract: str):
    """Markiert eine Position als geschlossen."""
    aiosqlite = _require_aiosqlite()
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute(
            "UPDATE smart_money_positions SET status = 'closed' WHERE contract_address = ?",
            (contract,)
        )
        await db.commit()
    logger.info(f"💾 SM Position closed: {contract[:20]}...")


async def load_sm_positions() -> dict:
    """Lädt offene Positionen aus DB (für Bot-Restart Recovery)."""
    aiosqlite = _require_aiosqlite()
    positions = {}
    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        async with db.execute(
            "SELECT * FROM smart_money_positions WHERE status = 'open'"
        ) as cursor:
            async for row in cursor:
                positions[row[0]] = {
                    "size_usd": row[4],
                    "entry_time": row[6],
                }
    return positions


def get_smart_money_trades_sync(limit: int = 20) -> list[dict]:
    """Liest Smart Money Trades (JUP_* und SM_*) für Dashboard."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM paper_trades WHERE market_question LIKE 'JUP_%' OR market_question LIKE 'SM_%' "
            "ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Smart Money DB read error: {e}")
        return []


def get_sm_positions_sync() -> list[dict]:
    """Liest offene Smart Money Positionen für Dashboard."""
    import sqlite3
    try:
        _ensure_sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM smart_money_positions WHERE status = 'open'")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"SM Positions DB read error: {e}")
        return []
