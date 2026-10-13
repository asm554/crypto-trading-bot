"""Aufzeichnung fuer spaetere Auswertung ("Forward-Test mit Aufzeichnung").

Jeder Token, der die Gratis-Filter besteht, wird beim ersten Sehen einmal festgehalten: Werte von damals,
wie weit er kam (Stufe + Grund), Judge-Antworten. Danach misst der Monitor den Kurs nach 1h, 6h und 24h,
fuer angenommene UND abgelehnte Token. Kostet nur GeckoTerminal-Abrufe (30 Pools pro Abruf), keine Jev-Aufrufe.
"""
import json
import statistics
import time

CHECKPOINTS = (("1h", 3600), ("6h", 6 * 3600), ("24h", 24 * 3600))
GIVE_UP_AFTER = 3 * 3600        # Checkpoint verpasst (z. B. Mac schlief) -> nach 3h als verpasst markieren

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tid TEXT UNIQUE, chain TEXT, pool TEXT, ticker TEXT,
  observed_ts REAL, price_usd REAL, stage TEXT, reason TEXT, model TEXT, snapshot TEXT, answers TEXT,
  p_1h REAL, ts_1h REAL, p_6h REAL, ts_6h REAL, p_24h REAL, ts_24h REAL);
"""
SNAP_KEYS = ("age_minutes", "liquidity_usd", "mcap_usd", "volume_h1", "volume_h6", "volume_h24", "change",
             "buys_h1", "sells_h1", "buys_h6", "sells_h6", "trades_h24", "holder_count", "top_10_percent",
             "top_wallet_percent", "developer_holding_percentage", "mint_authority", "freeze_authority",
             "is_honeypot")


def ensure(db):
    db.executescript(SCHEMA)


def observe(db, t: dict, stage: str, reason: str | None = None, answers: dict | None = None,
            model: str | None = None, now: float | None = None) -> None:
    """Einmal je Token (erstes Sehen). Spaetere Stufen desselben Zyklus aktualisieren die Zeile."""
    ensure(db)
    snap = json.dumps({k: t.get(k) for k in SNAP_KEYS if k in t})
    db.execute(
        "INSERT INTO observations(tid,chain,pool,ticker,observed_ts,price_usd,stage,reason,model,snapshot,answers) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tid) DO UPDATE SET stage=excluded.stage, reason=excluded.reason, "
        "model=COALESCE(excluded.model, model), snapshot=excluded.snapshot, answers=COALESCE(excluded.answers, answers) "
        "WHERE observations.p_1h IS NULL AND observations.ts_1h IS NULL AND ?-observations.observed_ts < 900",
        (t["tid"], t["chain"], t.get("pool"), t.get("ticker"), time.time() if now is None else now,
         t.get("price_usd"), stage, reason, model, snap, json.dumps(answers) if answers else None,
         time.time() if now is None else now))
    db.commit()


def due(db, now: float) -> dict:
    """{chain: [(obs_id, pool, checkpoint_name)]} fuer faellige, noch nicht gemessene Checkpoints."""
    ensure(db)
    out = {}
    for name, sec in CHECKPOINTS:
        rows = db.execute(f"SELECT id, chain, pool, observed_ts FROM observations WHERE ts_{name} IS NULL "
                          f"AND pool IS NOT NULL AND observed_ts + ? <= ?", (sec, now)).fetchall()
        for r in rows:
            out.setdefault(r["chain"], []).append((r["id"], r["pool"], name, r["observed_ts"] + sec))
    return out


def track(db, prices_fn, now: float | None = None, max_calls: int = 2) -> int:
    """Misst faellige Checkpoints. prices_fn(chain, pools) -> {pool: preis}. Gibt Anzahl Abrufe zurueck."""
    now = time.time() if now is None else now
    calls = 0
    for chain, items in due(db, now).items():
        for i in range(0, len(items), 30):
            if calls >= max_calls:
                return calls
            batch = items[i:i + 30]
            try:
                prices = prices_fn(chain, sorted({p for _, p, _, _ in batch}))
            except Exception:
                return calls                          # naechster Monitor-Lauf versucht es erneut
            calls += 1
            for obs_id, pool, name, due_ts in batch:
                price = prices.get(pool)
                if price is None and now - due_ts < GIVE_UP_AFTER:
                    continue                          # nicht gefunden: spaeter nochmal, nicht 0 eintragen
                db.execute(f"UPDATE observations SET p_{name}=?, ts_{name}=? WHERE id=?", (price, now, obs_id))
            db.commit()
    return calls


def summary(db) -> list[dict]:
    """Je Stufe/Grund: Anzahl und Kursveraenderung (Median, Anteil > +50 %, Anteil < -50 %) nach 1h/6h/24h.
    Kein Kurs mehr (Pool nicht mehr gelistet) wird getrennt als 'weg' gezaehlt und geht nicht in den Median ein:
    das kann ein Rug sein, aber auch nur eine Luecke bei GeckoTerminal."""
    ensure(db)
    groups = {}
    for r in db.execute("SELECT * FROM observations WHERE price_usd > 0"):
        key = r["stage"] if r["stage"] in ("entered", "not_picked", "budget") else f"{r['stage']}:{r['reason']}"
        g = groups.setdefault(key, {"group": key, "n": 0, **{c: [] for c, _ in CHECKPOINTS},
                                    **{f"gone_{c}": 0 for c, _ in CHECKPOINTS}})
        g["n"] += 1
        for c, _ in CHECKPOINTS:
            if r[f"ts_{c}"] is not None:
                p = r[f"p_{c}"]
                if p is None:
                    g[f"gone_{c}"] += 1
                else:
                    g[c].append((p / r["price_usd"] - 1) * 100)
    out = []
    for g in groups.values():
        row = {"group": g["group"], "n": g["n"]}
        for c, _ in CHECKPOINTS:
            v, gone = g[c], g[f"gone_{c}"]
            row[c] = ({"measured": len(v), "gone": gone, "median_pct": round(statistics.median(v), 1),
                       "share_up50": round(sum(x > 50 for x in v) / len(v), 2),
                       "share_down50": round(sum(x < -50 for x in v) / len(v), 2)}
                      if v else {"measured": 0, "gone": gone})
        out.append(row)
    return sorted(out, key=lambda r: -r["n"])


def render(rows: list[dict]) -> str:
    if not rows:
        return "Noch keine Aufzeichnungen."
    lines = ["Je Zeitpunkt: gemessen | Median | Anteil >+50% | Anteil <-50% | weg (kein Kurs mehr)",
             f"{'Gruppe':34} {'n':>5}  " + "  ".join(f"{c:>27}" for c, _ in CHECKPOINTS)]
    for r in rows:
        cells = []
        for c, _ in CHECKPOINTS:
            x = r[c]
            cells.append(f"{x['measured']:>4} {x['median_pct']:>+7.1f}% {x['share_up50']:>4.0%} "
                         f"{x['share_down50']:>4.0%} {x['gone']:>3}" if x["measured"] else f"{'-':>23} {x['gone']:>3}")
        lines.append(f"{r['group'][:34]:34} {r['n']:>5}  " + "  ".join(f"{c:>27}" for c in cells))
    return "\n".join(lines)
