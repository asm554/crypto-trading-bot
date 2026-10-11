"""Fakten vor Urteilen: erst Arithmetik, dann der Judge."""
from .config import HARD, SHAPE_MIN_CROWD, SOFT


def free_kill(t) -> str | None:
    if not HARD["min_age_minutes"] <= t["age_minutes"] <= HARD["max_age_hours"] * 60:
        return "age"
    if t["liquidity_usd"] < HARD["min_liquidity_usd"]:
        return "liquidity"
    if t["volume_h24"] < HARD["min_volume_h24"]:
        return "volume"
    if not HARD["min_mcap_usd"] <= t["mcap_usd"] <= HARD["max_mcap_usd"]:
        return "mcap"
    if t["trades_h24"] is None or t["trades_h24"] < HARD["min_trades_h24"]:
        return "trades"
    return None


def chain_kill(d) -> str | None:
    if d.get("top_wallet_percent") is not None and d["top_wallet_percent"] > HARD["max_top_wallet"]:
        return "top_wallet"
    if d.get("top_10_percent") is not None and float(d["top_10_percent"]) / 100 > HARD["max_top_10"]:
        return "top_10"
    if d.get("holder_count") is not None and d["holder_count"] < HARD["min_holders"]:
        return "holders"
    if d["chain"] == "solana" and (d.get("mint_authority") or d.get("freeze_authority")):
        return "authority_open"
    if d["chain"] in ("bsc", "base") and d.get("is_honeypot") is True:
        return "honeypot"
    if d["sells_h1"] == 0 and (d["buys_h1"] or 0) > 20:
        return "sell_side"
    return None


def soft_kill(ans) -> str | None:
    for name, (direction, limit) in SOFT.items():
        a = ans.get(name)
        if a is None:
            continue
        v = a.get("noul", a.get("score"))
        if v is None:
            continue
        if (direction == "max" and v > limit) or (direction == "min" and v < limit):
            return name
    shape = ans.get("shape")
    if shape:
        if shape["choice"] in ("fading", "one_buyer"):
            return "shape"
        if shape["probabilities"].get("crowd", 0) < SHAPE_MIN_CROWD:
            return "shape_weak"
    sell = ans.get("sell_side_risk")
    if sell and sell["choice"] in ("flagged", "suspicious"):
        return "sell_side"
    return None
