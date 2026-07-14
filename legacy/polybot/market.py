import asyncio
import aiohttp
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from . import config

_client: ClobClient = None

def get_client() -> ClobClient:
    global _client
    if _client is None:
        creds = None
        if config.CLOB_API_KEY:
            creds = ApiCreds(
                api_key=config.CLOB_API_KEY,
                api_secret=config.CLOB_SECRET,
                api_passphrase=config.CLOB_PASSPHRASE,
            )
            
        _client = ClobClient(
            host=config.HOST,
            key=config.PRIVATE_KEY or "0x0000000000000000000000000000000000000000000000000000000000000000",
            chain_id=config.POLYGON_CHAIN_ID,
            creds=creds
        )
    return _client

async def get_active_markets() -> list:
    """
    Holt über die Polymarket Gamma/REST API Wetter Märkte.
    Wir suchen gezielt nach Markets, deren "NO" Token wir prüfen können.
    """
    # Wir filtern die Gamma API direkt nach dem 'weather' Tag um exakt diese Nische zu scannen
    url = "https://gamma-api.polymarket.com/events?tag_slug=weather&closed=false"
    
    # Für das autonome Scanning holen wir alle Märkte,
    # die relevant sind.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return []
                data = await response.json()
    except Exception:
        # Fallback auf Dummy zum Testen, wenn Netz nicht erreichbar
        data = []

    weather_markets = []
    
    for event in data:
        # Check if weather related
        tags = [t.get("label", "").lower() for t in event.get("tags", [])]
        is_weather = "weather" in tags or "climate" in tags or event.get("slug", "").find("weather") != -1
        
        if is_weather or config.PAPER_MODE: # Fallback im Papermode, damit wir überhaupt Märkte sehen
            # Wir müssen die speziellen Token IDs für YES/NO extrahieren
            for market in event.get("markets", []):
                tokens = market.get("tokens", [])
                outcomes = market.get("outcomes", [])
                
                # Wir stellen sicher, dass es ein binärer Markt mit YES/NO ist
                if len(tokens) >= 2 and len(outcomes) >= 2:
                    no_index = -1
                    if outcomes[1].upper() == "NO": no_index = 1
                    elif outcomes[0].upper() == "NO": no_index = 0
                    
                    if no_index != -1:
                        weather_markets.append({
                            "question": market.get("question"),
                            "group_id": event.get("id"),
                            "market_id": market.get("id"),
                            "no_token_id": tokens[no_index]["token_id"] # Der ERC1155 Token-Hash für "NO"
                        })
    
    # Fallback Data falls GraphQL/Gamma blockiert
    if not weather_markets and config.PAPER_MODE:
        weather_markets = [
            {"question": "Will it rain in NY?", "market_id": "0x11", "no_token_id": "0x1"},
            {"question": "Hurricane forming?", "market_id": "0x22", "no_token_id": "0x2"}
        ]
        
    return weather_markets

async def get_orderbook(token_id: str) -> dict:
    """
    Holt das exakte Orderbook für die NO-Token-ID.
    """
    client = get_client()
    
    if config.PAPER_MODE and len(token_id) < 5:
        # Simulierte Daten um die Strategie zu triggern (0.95 Ask ist ein Kauf!)
        return {"bid": 0.92, "ask": 0.95, "token_id": token_id}
        
    try:
        book = await client.get_order_book(token_id)
        # Bester ASK (den Preis, den wir bezahlen um NO zu kaufen)
        best_ask = float(book.asks[0].price) if book.asks else 1.0
        best_bid = float(book.bids[0].price) if book.bids else 0.0
        return {"bid": best_bid, "ask": best_ask, "token_id": token_id}
    except Exception as e:
        return {"bid": 0, "ask": 1.0, "token_id": token_id}
