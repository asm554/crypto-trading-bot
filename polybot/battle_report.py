"""Daily Telegram report for the 3-way paper strategy battle."""

import asyncio
import json
import math
import sqlite3
import time
from pathlib import Path

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.alerts import send_telegram
from polybot.dca_strategy import PAIR_MAP, extract_quote, fetch_ticker_data
from polybot.memecoin_strategy import DEFAULT_DEX_FEE_PCT, DEFAULT_SLIPPAGE_PCT, EURUSD_INTERNAL, EURUSD_PAIR, FALLBACK_EUR_USD_RATE, fetch_pairs_by_address
from polybot.scout_strategy import fetch_scout_prices
from polybot.paper_db import DB_PATH, get_open_trades_by_prefix, init_db, log_equity_snapshot

DATA_DIR = Path(DB_PATH).resolve().parent
META_PATH = DATA_DIR / "battle_meta.json"
DURATION_DAYS = 42
FEE = config.CRYPTO_TAKER_FEE_RATE
BOTS = {
    "dca": {"label": "Der Brave", "prefix": "DCA_", "state": DATA_DIR / "dca_state.json"},
    "momentum": {"label": "Der Zocker", "prefix": "MOM_", "state": DATA_DIR / "momentum_state.json"},
    "meanrev": {"label": "Der Contrarian", "prefix": "REV_", "state": DATA_DIR / "meanrev_state.json"},
    "arb": {"label": "Der Pedant", "prefix": "ARB_", "state": DATA_DIR / "arb_state.json"},
    "daytrade": {"label": "Der Zappler", "prefix": "DAY_", "state": DATA_DIR / "daytrade_state.json"},
    "memecoin": {"label": "Der Onchain", "prefix": "CHAIN_", "state": DATA_DIR / "memecoin_state.json"},
    "surfer": {"label": "Der Surfer", "prefix": "SURF_", "state": DATA_DIR / "surfer_state.json"},
    "scout": {"label": "Der Spaeher", "prefix": "SCOUT_", "state": DATA_DIR / "scout_state.json"},
}


def ensure_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text())
    meta = {"start_ts": time.time(), "duration_days": DURATION_DAYS}
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, separators=(",", ":")))
    tmp.replace(META_PATH)
    return meta


def load_cash(state_path: Path, default: float = 100.0) -> float:
    try:
        raw = json.loads(state_path.read_text())
        return float(raw.get("capital_remaining", default))
    except Exception:
        return default


async def equity_for(prefix: str, state_path: Path, bot: str) -> dict:
    cash = load_cash(state_path)
    open_rows = await get_open_trades_by_prefix(prefix)
    pairs = sorted({r["market_question"].replace(prefix, "") for r in open_rows})
    ticker = await fetch_ticker_data(pairs) if pairs else {}
    mtm = 0.0
    unrealized = 0.0
    for row in open_rows:
        pair = row["market_question"].replace(prefix, "")
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        shares = float(row["size"])
        entry = float(row["entry_price"])
        entry_cost = shares * entry
        if not data:
            # Kein Live-Ticker für diese offene Position: nicht fallen lassen,
            # sonst crasht die Equity und wir erzeugen einen Fake-Drawdown.
            # Konservativ zum Einstands-Kostenwert bewerten (flach, kein Fake-Verlust).
            mtm += entry_cost
            unrealized += 0.0
            continue
        last = float(data["c"][0])
        # Mark-to-Market simuliert den Verkauf, also zum Bid bewerten – sonst
        # weicht der Battle-Report von der Equity der Bots ab.
        bid, _ask = extract_quote(data, last)
        current_value = shares * bid
        sell_fee = current_value * FEE
        mtm += current_value - entry_cost * FEE - sell_fee
        unrealized += current_value - entry_cost - entry_cost * FEE - sell_fee
    realized = await paper_db_module.get_realized_pnl_by_prefix(prefix)
    snap = {"equity_eur": cash + mtm, "cash_eur": cash, "open_positions": len(open_rows), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}
    await log_equity_snapshot(bot, **snap)
    return snap


async def equity_for_memecoin(prefix: str, state_path: Path, bot: str) -> dict:
    """Wie ``equity_for``, aber über DexScreener statt Kraken.

    "Der Onchain" schlüsselt intern nach Mint-Adresse, nicht nach Ticker
    (zwei dynamisch entdeckte Tokens können denselben Namen tragen) —
    ``market_question`` kodiert deshalb ``CHAIN_{symbol}@{address}``; die
    Adresse nach dem ``@`` ist der Teil, der gegen DexScreener aufgelöst wird.
    Bewertung sonst wie ``equity_for``: Cash aus dem State, offene Positionen
    zum aktuellen Preis abzüglich Verkaufs-Slippage (da es on-chain kein Bid
    gibt) und der mechanischen DEX-Gebühr, siehe memecoin_strategy.py.
    """
    cash = load_cash(state_path)
    open_rows = await get_open_trades_by_prefix(prefix)
    addresses = sorted({
        row["market_question"].replace(prefix, "").partition("@")[2]
        for row in open_rows if "@" in row["market_question"]
    })
    pairs = await fetch_pairs_by_address(addresses) if addresses else {}
    ticker = await fetch_ticker_data([EURUSD_PAIR])
    eurusd = ticker.get(EURUSD_INTERNAL) or ticker.get(EURUSD_PAIR)
    rate = float(eurusd["c"][0]) if eurusd else FALLBACK_EUR_USD_RATE
    mtm = 0.0
    unrealized = 0.0
    for row in open_rows:
        _, _, address = row["market_question"].replace(prefix, "").partition("@")
        shares = float(row["size"])
        entry = float(row["entry_price"])
        entry_cost = shares * entry
        pair = pairs.get(address)
        if not pair:
            mtm += entry_cost
            continue
        price_usd = float(pair["priceUsd"])
        current_value = shares * (price_usd / rate) * (1 - DEFAULT_SLIPPAGE_PCT / 100) * (1 - DEFAULT_DEX_FEE_PCT / 100)
        mtm += current_value
        unrealized += current_value - entry_cost
    realized = await paper_db_module.get_realized_pnl_by_prefix(prefix)
    snap = {"equity_eur": cash + mtm, "cash_eur": cash, "open_positions": len(open_rows), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}
    await log_equity_snapshot(bot, **snap)
    return snap


async def equity_for_scout(prefix: str, state_path: Path, bot: str) -> dict:
    cash = load_cash(state_path)
    open_rows = await get_open_trades_by_prefix(prefix)
    mints = [row["market_question"].replace(prefix, "").partition("@")[2] for row in open_rows]
    prices = await fetch_scout_prices(mints)
    ticker = await fetch_ticker_data([EURUSD_PAIR])
    eurusd = ticker.get(EURUSD_INTERNAL) or ticker.get(EURUSD_PAIR)
    rate = float(eurusd["c"][0]) if eurusd else FALLBACK_EUR_USD_RATE
    mtm = unrealized = 0.0
    for row in open_rows:
        mint = row["market_question"].replace(prefix, "").partition("@")[2]
        cost = float(row["size"]) * float(row["entry_price"])
        price_usd = float((prices.get(mint) or {}).get("usdPrice") or 0)
        value = float(row["size"]) * (price_usd / rate) * 0.995 if price_usd > 0 else cost
        mtm += value; unrealized += value - cost
    realized = await paper_db_module.get_realized_pnl_by_prefix(prefix)
    snap = {"equity_eur": cash + mtm, "cash_eur": cash, "open_positions": len(open_rows), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}
    await log_equity_snapshot(bot, **snap)
    return snap


def rows_for_bot(bot: str) -> list[tuple]:
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        return con.execute("SELECT ts,equity_eur FROM equity_snapshots WHERE bot=? ORDER BY ts ASC", (bot,)).fetchall()
    finally:
        con.close()


def trades_count(prefix: str) -> int:
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        return int(con.execute("SELECT count(*) FROM paper_trades WHERE market_question LIKE ?", (f"{prefix}%",)).fetchone()[0] or 0)
    finally:
        con.close()


def max_drawdown(vals: list[float]) -> float:
    peak = None
    worst = 0.0
    for v in vals:
        peak = v if peak is None else max(peak, v)
        if peak and peak > 0:
            worst = min(worst, (v - peak) / peak * 100)
    return worst


def time_under_water_hours(rows: list[tuple]) -> float:
    """Längste Strecke unterhalb eines früheren Equity-Hochs, in Stunden.

    Beantwortet die Frage, die MaxDD offenlässt: nicht wie tief es ging, sondern
    wie lange man es aushalten musste. Gemessen wird ab dem Hoch (nicht ab dem
    ersten roten Snapshot – unter Wasser ist man ab dem Moment, wo es vom Peak
    runtergeht) bis zum letzten Snapshot, der noch unter diesem Hoch liegt.
    Läuft die Serie am Ende noch unter Wasser, zählt sie bis zum letzten Snapshot.
    """
    peak = None
    peak_ts = None
    worst = 0.0
    for ts, v in rows:
        ts, v = float(ts), float(v)
        if peak is None or v >= peak:
            peak = v
            peak_ts = ts
            continue
        worst = max(worst, ts - peak_ts)
    return worst / 3600.0


def longest_losing_streak(prefix: str) -> int:
    """Längste Serie aufeinanderfolgender Verlust-Trades (nach Gebühren)."""
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        rows = con.execute(
            "SELECT real_pnl FROM paper_trades WHERE market_question LIKE ? "
            "AND resolved_at IS NOT NULL ORDER BY resolved_at ASC",
            (f"{prefix}%",),
        ).fetchall()
    finally:
        con.close()
    worst = current = 0
    for (pnl,) in rows:
        if float(pnl or 0.0) < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def spark(vals: list[float]) -> str:
    vals = vals[-5:]
    chars = "▁▂▃▄▅▆▇█"
    if not vals:
        return "▁▁▁▁▁"
    lo, hi = min(vals), max(vals)
    if abs(hi - lo) < 1e-9:
        return chars[3] * len(vals)
    return "".join(chars[min(7, max(0, round((v - lo) / (hi - lo) * 7)))] for v in vals)


MEDALS = ["🥇", "🥈", "🥉"]


def rank_marker(idx: int) -> str:
    """Platzierungs-Symbol fürs Ranking. Fällt auf "4.", "5.", ... zurück,
    sobald mehr Bots als Medaillen im Rennen sind."""
    return MEDALS[idx] if idx < len(MEDALS) else f"{idx + 1}."


async def build_report() -> str:
    await init_db()
    meta = ensure_meta()
    day = max(1, min(int((time.time() - float(meta["start_ts"])) // 86400) + 1, int(meta.get("duration_days", DURATION_DAYS))))
    snaps = {}
    for bot, cfg in BOTS.items():
        if bot == "memecoin":
            snaps[bot] = await equity_for_memecoin(cfg["prefix"], cfg["state"], bot)
        elif bot == "scout":
            snaps[bot] = await equity_for_scout(cfg["prefix"], cfg["state"], bot)
        else:
            snaps[bot] = await equity_for(cfg["prefix"], cfg["state"], bot)
    ranking = sorted(snaps.items(), key=lambda kv: kv[1]["equity_eur"], reverse=True)
    lines = [f"🏁 Strategie-Battle — Tag {day}/{int(meta.get('duration_days', DURATION_DAYS))}", ""]
    for idx, (bot, s) in enumerate(ranking):
        vals = [r[1] for r in rows_for_bot(bot)]
        pct = (s["equity_eur"] - 100.0)
        lines.append(f"{rank_marker(idx)} {BOTS[bot]['label']:<16} {s['equity_eur']:>7.2f} € ({pct:+.1f} %) {spark(vals)}")
    lines.append("")
    lines.append("```")
    lines.append("           Equity   offen  real.PnL  Trades  MaxDD  UW(h)  Serie")
    for bot in ["dca", "momentum", "meanrev", "arb", "daytrade", "memecoin", "surfer", "scout"]:
        cfg = BOTS[bot]
        s = snaps[bot]
        rows = rows_for_bot(bot)
        vals = [r[1] for r in rows]
        lines.append(
            f"{cfg['label'][:10]:<10} {s['equity_eur']:>7.2f}€ {s['open_positions']:>5} "
            f"{s['realized_pnl_eur']:>+8.2f}€ {trades_count(cfg['prefix']):>6} "
            f"{max_drawdown(vals):>+6.1f}% {time_under_water_hours(rows):>6.1f} "
            f"{longest_losing_streak(cfg['prefix']):>6}"
        )
    lines.append("```")
    lines.append("")
    lines.append("KPI: Ranking nach Netto-Equity (Cash + Mark-to-Market nach Gebühren), nicht nach realisiertem PnL.")
    lines.append("Fills: Kauf zum Ask, Verkauf zum Bid (echter Kraken-Spread).")
    lines.append("MaxDD = tiefster Abfall vom Hoch | UW(h) = längste Zeit unter Wasser | Serie = längste Verlustserie.")
    return "\n".join(lines)


async def main():
    msg = await build_report()
    try:
        await send_telegram(msg)
    except Exception as e:
        print(f"Telegram report failed: {e}")
    print(msg)

if __name__ == "__main__":
    asyncio.run(main())
