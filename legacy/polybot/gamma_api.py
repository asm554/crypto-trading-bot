import aiohttp
import logging
from . import config

logger = logging.getLogger(__name__)

# Cache for market metadata
# key: condition_id, value: { "question": "...", "active": bool }
market_cache = {}

async def fetch_market_metadata(condition_id: str) -> dict:
    """
    Fetches market metadata from Gamma API for a given condition_id.
    Uses local cache to avoid redundant network requests.
    """
    if condition_id in market_cache:
        return market_cache[condition_id]

    url = f"https://gamma-api.polymarket.com/markets?condition_id={condition_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        market = data[0]
                        metadata = {
                            "question": market.get("question", "Unknown Market"),
                            "slug": market.get("slug", ""),
                            "outcomes": market.get("outcomes", [])
                        }
                        market_cache[condition_id] = metadata
                        return metadata
    except Exception as e:
        logger.error(f"Error fetching Gamma metadata for {condition_id}: {e}")
    
    return {"question": f"Market {condition_id[:8]}", "outcomes": []}

async def get_market_title(condition_id: str) -> str:
    metadata = await fetch_market_metadata(condition_id)
    return metadata.get("question", "Unknown Market")
