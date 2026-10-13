"""Paper-Fills und Exit-Regeln. Fee und Slippage pro Seite, getrennt geführt wie im Artikel-Prinzip."""
from .config import FEE_PCT, SLIPPAGE_PCT, PaperRules


def size_ticket(bank: float, liquidity_usd: float, size_factor: float, rules: PaperRules) -> float:
    t = min(bank * rules.ticket_pct_of_bank / 100, liquidity_usd * rules.ticket_pct_of_liquidity / 100)
    return t * size_factor


def price_impact_pct(ticket_usd: float, liquidity_usd: float | None) -> float:
    """AMM-Naeherung: Ticket gegen eine Seite des Pools (halbe Liquiditaet). 2 % der Liquiditaet ~ 4 % Wirkung."""
    if not liquidity_usd:
        return 0.0
    return min(50.0, ticket_usd / (liquidity_usd / 2) * 100)


def fill_buy(price: float, ticket: float, liquidity_usd: float | None = None) -> dict:
    fill = price * (1 + (SLIPPAGE_PCT + price_impact_pct(ticket, liquidity_usd)) / 100)
    return {"price": fill, "units": ticket * (1 - FEE_PCT / 100) / fill, "ticket": ticket}


def fill_sell(trade, price: float, liquidity_usd: float | None = None) -> dict:
    value = trade["units"] * price
    fill = price * (1 - (SLIPPAGE_PCT + price_impact_pct(value, liquidity_usd)) / 100)
    proceeds = trade["units"] * fill * (1 - FEE_PCT / 100)
    pnl = proceeds - trade["ticket_usd"]
    return {"price": fill, "pnl_usd": pnl, "pnl_pct": pnl / trade["ticket_usd"] * 100}


def volume_ratio(t: dict) -> float | None:
    if not t.get("volume_h24"):
        return None
    return t["volume_h6"] / (t["volume_h24"] / 4)


def exit_reason(trade, cur: dict, now: float, rules: PaperRules) -> str | None:
    """Artikel-Regel (Volumen) plus zwei Paper-Sicherungen (Stop, Zeit). Fehlende Daten regelt der Monitor."""
    if not cur or not cur.get("price_usd"):
        return None
    ratio = volume_ratio(cur)
    if ratio is not None and ratio < rules.exit_volume_ratio:
        return "VOLUME"
    if (cur["price_usd"] / trade["entry_price"] - 1) * 100 <= -rules.hard_stop_pct:
        return "STOP"
    if (now - trade["opened_ts"]) / 3600 >= rules.max_hold_hours:
        return "TIME"
    return None
