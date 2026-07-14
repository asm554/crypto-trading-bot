"""
Polymarket MCP-Server Client
Verbindet sich mit einem lokal laufenden polymarket-mcp-server
(z.B. github.com/caiovicentino/polymarket-mcp-server).

Starte den MCP-Server separat:
  npx polymarket-mcp-server   (oder laut README)

Standard-Port: 3000 (anpassbar via MCP_SERVER_URL in .env)

Verwendung:
  from polybot.mcp_client import get_live_orderbook, get_portfolio
"""

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

MCP_BASE_URL = os.getenv("MCP_SERVER_URL", "http://localhost:3000")


def _get(path: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(f"{MCP_BASE_URL}{path}", params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.ConnectionError:
        logger.warning(f"MCP-Server nicht erreichbar ({MCP_BASE_URL}). Starte polymarket-mcp-server.")
        return None
    except Exception as e:
        logger.error(f"MCP request fehlgeschlagen ({path}): {e}")
        return None


def get_live_orderbook(market_id: str) -> Optional[dict]:
    """
    Holt das Live-Orderbook für einen Markt.
    Rückgabe: { "bids": [...], "asks": [...], "best_bid": float, "best_ask": float }
    """
    data = _get(f"/orderbook/{market_id}")
    if not data:
        return None

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    best_bid = max((float(b["price"]) for b in bids), default=0.0)
    best_ask = min((float(a["price"]) for a in asks), default=1.0)

    return {
        "market_id": market_id,
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(best_ask - best_bid, 4),
    }


def get_portfolio() -> list[dict]:
    """
    Gibt eigene offene Positionen zurück.
    Rückgabe: [{ market_id, outcome, size, avg_price, current_value }, ...]
    """
    data = _get("/portfolio")
    if not data:
        return []
    return data.get("positions", [])


def has_open_position(market_id: str, outcome: str) -> bool:
    """
    Prüft ob eine eigene Position in diesem Markt/Outcome bereits offen ist.
    Verhindert Doppelpositionen.
    """
    portfolio = get_portfolio()
    for pos in portfolio:
        if pos.get("market_id") == market_id and pos.get("outcome", "").upper() == outcome.upper():
            return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Portfolio:", get_portfolio())
    print("Orderbook test:", get_live_orderbook("presidential-2028-winner"))
