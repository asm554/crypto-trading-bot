"""Datenquellen: GeckoTerminal (Free, kein Key). Alles Metrik-Relevante kommt aus den Pool-Attributen,
kein FOMO, kein Login."""
import time
from datetime import datetime, timezone

import requests

GT = "https://api.geckoterminal.com/api/v2"
SOL_RPC = "https://api.mainnet-beta.solana.com"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def http_get_json(url: str, params: dict | None = None, timeout: int = 20) -> dict:
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def age_minutes(created_iso: str | None, now: float | None = None) -> float:
    if not created_iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return 0.0
    return max(0.0, ((time.time() if now is None else now) - ts) / 60)


def pool_to_token(pool: dict, net: str, now: float | None = None) -> dict | None:
    """GT-Pool -> Desk-Feldnamen (einmal, hier). Alles danach liest nur diese Namen."""
    a = pool.get("attributes") or {}
    base = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id")
    if not base or "_" not in base:
        return None
    tx = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    ch = a.get("price_change_percentage") or {}
    h = lambda w, k: ((tx.get(w) or {}).get(k))
    buys24, sells24 = h("h24", "buys"), h("h24", "sells")
    return {
        "tid": f"{net}:{base.split('_', 1)[1]}", "chain": net, "addr": base.split("_", 1)[1],
        "pool": a.get("address"), "ticker": (a.get("name") or "?").split(" / ")[0].strip(),
        "price_usd": _num(a.get("base_token_price_usd")),
        "mcap_usd": _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd")) or 0.0,
        "liquidity_usd": _num(a.get("reserve_in_usd")) or 0.0,
        "volume_h24": _num(vol.get("h24")) or 0.0, "volume_h6": _num(vol.get("h6")) or 0.0,
        "volume_h1": _num(vol.get("h1")) or 0.0,
        "change": {"5m": _num(ch.get("m5")), "1h": _num(ch.get("h1")), "6h": _num(ch.get("h6")),
                   "24h": _num(ch.get("h24"))},
        "buys_h1": h("h1", "buys"), "sells_h1": h("h1", "sells"),
        "buys_h6": h("h6", "buys"), "sells_h6": h("h6", "sells"),
        "trades_h24": (buys24 or 0) + (sells24 or 0) if buys24 is not None else None,
        "age_minutes": age_minutes(a.get("pool_created_at"), now),
    }


def universe(nets, pages: int = 2, get=http_get_json, now: float | None = None) -> list[dict]:
    """Frische Pools je Chain. Kostet einen GT-Slot pro Chain und Seite."""
    out, seen = [], set()
    for net in nets:
        for page in range(1, pages + 1):
            try:
                data = get(f"{GT}/networks/{net}/new_pools", {"page": page}).get("data", [])
            except Exception:
                break
            for pool in data:
                t = pool_to_token(pool, net, now)
                if t and t["tid"] not in seen:
                    seen.add(t["tid"])
                    out.append(t)
    out.sort(key=lambda t: t["volume_h24"] / max(t["mcap_usd"], 1), reverse=True)  # ordnet nur die Queue
    return out


def refresh_pool(chain: str, pool_addr: str, get=http_get_json) -> dict | None:
    """Aktuelle Pool-Daten einer offenen Position (Preis, Volumen)."""
    d = get(f"{GT}/networks/{chain}/pools/{pool_addr}").get("data")
    if not d:
        return None
    base = {"id": f"{chain}_x"}
    d = {**d, "relationships": {"base_token": {"data": base}}}
    return pool_to_token(d, chain)


def clean_handle(h):
    if not h:
        return None
    h = h.strip().lstrip("@").split("?")[0].split("/")[0]
    return h if h and h.replace("_", "").isalnum() and len(h) <= 15 else None


def dossier(t: dict, get=http_get_json, rpc_post=None) -> dict:
    """Ein GT-Call je Token (+ Solana-RPC fuer exakte Top-Wallet). Null bleibt null."""
    a = get(f"{GT}/networks/{t['chain']}/tokens/{t['addr']}/info")["data"]["attributes"]
    holders = a.get("holders") or {}
    d = {**t,
         "holder_count": holders.get("count"),
         "top_10_percent": (holders.get("distribution_percentage") or {}).get("top_10"),
         "developer_holding_percentage": a.get("developer_holding_percentage"),
         "gt_score_details": a.get("gt_score_details"),
         "is_honeypot": a.get("is_honeypot"),
         "mint_authority": a.get("mint_authority"),
         "freeze_authority": a.get("freeze_authority"),
         "description": a.get("description"),
         "x_handle": clean_handle(a.get("twitter_handle"))}
    if t["chain"] == "solana":
        d["top_wallet_percent"] = sol_top_wallet(t["addr"], rpc_post)
    return d


def sol_top_wallet(mint: str, rpc_post=None):
    def call(m, p):
        if rpc_post:
            return rpc_post(m, p)
        r = requests.post(SOL_RPC, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p},
                          headers=UA, timeout=20)
        return r.json()["result"]
    try:
        supply = float(call("getTokenSupply", [mint])["value"]["amount"])
        top = call("getTokenLargestAccounts", [mint])["value"]
        return float(top[0]["amount"]) / supply if supply and top else None
    except Exception:
        return None


def pool_prices(chain: str, pool_addrs: list[str], get=http_get_json) -> dict:
    """Aktuelle Preise fuer bis zu 30 Pools in EINEM Abruf. Fehlende Pools fehlen im Ergebnis (nicht 0)."""
    if not pool_addrs:
        return {}
    data = get(f"{GT}/networks/{chain}/pools/multi/{','.join(pool_addrs[:30])}").get("data") or []
    out = {}
    for p in data:
        a = p.get("attributes") or {}
        price = _num(a.get("base_token_price_usd"))
        if a.get("address") and price:
            out[a["address"]] = price
    return out
