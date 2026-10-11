"""Paper-Fills und Exit-Regeln. Fee und Slippage pro Seite, getrennt geführt wie im Artikel-Prinzip."""
from .config import FEE_PCT, SLIPPAGE_PCT, PaperRules


def size_ticket(bank: float, liquidity_usd: float, size_factor: float, rules: PaperRules) -> float:
    t = min(bank * rules.ticket_pct_of_bank / 100, liquidity_usd * rules.ticket_pct_of_liquidity / 100)
    return t * size_factor


def fill_buy(price: float, ticket: float) -> dict:
    fill = price * (1 + SLIPPAGE_PCT / 100)
    return {"price": fill, "units": ticket * (1 - FEE_PCT / 100) / fill, "ticket": ticket}


def fill_sell(trade, price: float) -> dict:
    fill = price * (1 - SLIPPAGE_PCT / 100)
    proceeds = trade["units"] * fill * (1 - FEE_PCT / 100)
    pnl = proceeds - trade["ticket_usd"]
    return {"price": fill, "pnl_usd": pnl, "pnl_pct": pnl / trade["ticket_usd"] * 100}


def volume_ratio(t: dict) -> float | None:
    if not t.get("volume_h24"):
        return None
    return t["volume_h6"] / (t["volume_h24"] / 4)


def exit_reason(trade, cur: dict, now: float, rules: PaperRules) -> str | None:
    """Artikel-Regel (Volumen) plus zwei Paper-Sicherungen (Stop, Zeit). Preis fehlt -> schliessen (nicht messbar)."""
    if not cur or not cur.get("price_usd"):
        return "NO_DATA"
    ratio = volume_ratio(cur)
    if ratio is not None and ratio < rules.exit_volume_ratio:
        return "VOLUME"
    if (cur["price_usd"] / trade["entry_price"] - 1) * 100 <= -rules.hard_stop_pct:
        return "STOP"
    if (now - trade["opened_ts"]) / 3600 >= rules.max_hold_hours:
        return "TIME"
    return None
