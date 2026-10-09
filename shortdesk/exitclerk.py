"""Bot 5 - Exit Clerk: Regeln statt Verhandlung. Stop, Daily-Close ueber Anker, STALE nach 72h flat."""
from .candles import C
from .config import DAY, Rules


def evaluate(t, c1h: list[C], c1d: list[C], mark: float, now: float, rules: Rules) -> dict | None:
    """Gibt {'action': 'EXIT','price','ts','reason'} | {'action':'STALE'} | None zurueck.
    Nur Kerzen, die nach der Alert-Erstellung geschlossen haben, zaehlen.
    Zustand NEUTRAL im Gewinn -> bewusst keine Aktion (Gewinner laufen lassen)."""
    events = []
    for c in c1h:
        if c.ts >= t["created_ts"] and c.h >= t["stop"]:
            events.append((c.ts + 3600, t["stop"], "STOP"))
            break
    for d in c1d:
        if d.ts + DAY > t["created_ts"] and d.c > t["anchor_high"]:
            events.append((d.ts + DAY, d.c, "ANCHOR_CLOSE"))
            break
    if events:
        ts, price, reason = min(events)
        return {"action": "EXIT", "price": price, "ts": ts, "reason": reason}
    r_dist = t["stop"] - t["entry"]
    age_h = (now - t["created_ts"]) / 3600
    if age_h >= rules.stale_hours and not t["stale_flagged"] and abs(t["entry"] - mark) < rules.stale_band_r * r_dist:
        return {"action": "STALE"}
    return None


def settle(t, price: float, ts: int, reason: str, rules: Rules) -> dict:
    """Short: Entry-Fill unter Marke, Exit-Fill ueber Marke. Gebuehren und Slippage getrennt, Ergebnis in R."""
    gross = (t["entry"] - price) * t["size"]
    fee = rules.fee_pct / 100 * (t["entry"] + price) * t["size"]
    slip = rules.slippage_pct / 100 * (t["entry"] + price) * t["size"]
    pnl = gross - fee - slip
    return {"exit_price": price, "exit_ts": int(ts), "exit_reason": reason, "gross_usd": gross,
            "fee_usd": fee, "slip_usd": slip, "pnl_usd": pnl, "r_multiple": pnl / t["risk_usd"]}
