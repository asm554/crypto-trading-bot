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
from polybot.dca_strategy import PAIR_MAP, fetch_ticker_data
from polybot.paper_db import DB_PATH, get_open_trades_by_prefix, init_db, log_equity_snapshot

DATA_DIR = Path(DB_PATH).resolve().parent
META_PATH = DATA_DIR / "battle_meta.json"
DURATION_DAYS = 42
FEE = config.CRYPTO_TAKER_FEE_RATE
BOTS = {
    "dca": {"label": "Der Brave", "prefix": "DCA_", "state": DATA_DIR / "dca_state.json"},
    "momentum": {"label": "Der Zocker", "prefix": "MOM_", "state": DATA_DIR / "momentum_state.json"},
    "meanrev": {"label": "Der Contrarian", "prefix": "REV_", "state": DATA_DIR / "meanrev_state.json"},
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
        current_value = shares * last
        sell_fee = current_value * FEE
        mtm += current_value - sell_fee
        unrealized += current_value - entry_cost - entry_cost * FEE - sell_fee
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


def spark(vals: list[float]) -> str:
    vals = vals[-5:]
    chars = "▁▂▃▄▅▆▇█"
    if not vals:
        return "▁▁▁▁▁"
    lo, hi = min(vals), max(vals)
    if abs(hi - lo) < 1e-9:
        return chars[3] * len(vals)
    return "".join(chars[min(7, max(0, round((v - lo) / (hi - lo) * 7)))] for v in vals)


async def build_report() -> str:
    await init_db()
    meta = ensure_meta()
    day = max(1, min(int((time.time() - float(meta["start_ts"])) // 86400) + 1, int(meta.get("duration_days", DURATION_DAYS))))
    snaps = {}
    for bot, cfg in BOTS.items():
        snaps[bot] = await equity_for(cfg["prefix"], cfg["state"], bot)
    ranking = sorted(snaps.items(), key=lambda kv: kv[1]["equity_eur"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏁 Strategie-Battle — Tag {day}/{int(meta.get('duration_days', DURATION_DAYS))}", ""]
    for idx, (bot, s) in enumerate(ranking):
        vals = [r[1] for r in rows_for_bot(bot)]
        pct = (s["equity_eur"] - 100.0)
        lines.append(f"{medals[idx]} {BOTS[bot]['label']:<16} {s['equity_eur']:>7.2f} € ({pct:+.1f} %) {spark(vals)}")
    lines.append("")
    lines.append("```")
    lines.append("           Equity   offen  real.PnL  Trades  MaxDD")
    for bot in ["dca", "momentum", "meanrev"]:
        cfg = BOTS[bot]
        s = snaps[bot]
        vals = [r[1] for r in rows_for_bot(bot)]
        lines.append(f"{cfg['label'][:10]:<10} {s['equity_eur']:>7.2f}€ {s['open_positions']:>5} {s['realized_pnl_eur']:>+8.2f}€ {trades_count(cfg['prefix']):>6} {max_drawdown(vals):>+6.1f}%")
    lines.append("```")
    lines.append("")
    lines.append("KPI: Ranking nach Netto-Equity (Cash + Mark-to-Market nach Gebühren), nicht nach realisiertem PnL.")
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
