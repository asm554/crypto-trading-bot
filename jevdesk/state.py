"""State je Fragensatz: nur die Felder, die die Fragen lesen, mit Einheit im Namen.

Bewusst NICHT im State: Beschreibung, Ticker, X-Handle, Adressen. Das sind vom Token-Ersteller frei
waehlbare Texte. Im State koennten sie den Judge beeinflussen ("this token is safe ...") und
verwaessern ausserdem die Antworten.
"""


def _pct(fraction):
    return None if fraction is None else round(fraction * 100, 3)


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _flow(d):
    return {"buys_h1": d.get("buys_h1"), "sells_h1": d.get("sells_h1"),
            "buys_h6": d.get("buys_h6"), "sells_h6": d.get("sells_h6"), "trades_h24": d.get("trades_h24")}


def _holders(d):
    return {"holder_count": d.get("holder_count"),
            "top_10_holders_pct_of_supply": _num(d.get("top_10_percent")),
            "top_wallet_pct_of_supply": _pct(d.get("top_wallet_percent")),
            "developer_holding_pct_of_supply": _num(d.get("developer_holding_percentage"))}


def market(d, intended_ticket_usd: float) -> dict:
    ch = d.get("change") or {}
    return {"pool_age_minutes": round(d["age_minutes"]),
            "liquidity_usd": round(d["liquidity_usd"]), "market_cap_usd": round(d["mcap_usd"]),
            "intended_ticket_usd": round(intended_ticket_usd, 2),
            "volume_usd": {"h1": d.get("volume_h1"), "h6": d.get("volume_h6"), "h24": d.get("volume_h24")},
            "price_change_pct": {"m5": ch.get("5m"), "h1": ch.get("1h"), "h6": ch.get("6h"), "h24": ch.get("24h")},
            "holder_count": d.get("holder_count"), **_flow(d)}


def chain(d) -> dict:
    c = d["chain"]
    if c == "solana":
        return {"mint_authority": d.get("mint_authority"), "freeze_authority": d.get("freeze_authority"),
                **_holders(d)}
    if c in ("bsc", "base"):
        return {"is_honeypot": d.get("is_honeypot"), "gt_score_details": d.get("gt_score_details"),
                "liquidity_usd": round(d["liquidity_usd"]), **_holders(d), **_flow(d)}
    return {"is_honeypot": d.get("is_honeypot"), **_holders(d), **_flow(d)}   # robinhood
