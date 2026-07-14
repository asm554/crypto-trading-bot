import asyncio
import websockets
import json
import logging
import time
from . import config

logger = logging.getLogger(__name__)

# Dictionary for newest prices and timestamps
latest_prices = {}
latest_bids = {}
latest_asks = {}
last_update_ts = {}
price_histories = {} # key: pair, value: list of (ts, price)

def is_data_stale(pair: str) -> bool:
    """True if data for this pair is older than config threshold."""
    last_ts = last_update_ts.get(pair.lower(), 0)
    return (time.time() - last_ts) > config.STALE_DATA_THRESHOLD

def _normalize_pair(pair: str) -> str:
    return pair.replace("/", "").lower()


def _kraken_pair_name(pair: str) -> str:
    normalized = _normalize_pair(pair).upper()
    mapping = {
        "XBTEUR": "XBT/EUR",
        "ETHEUR": "ETH/EUR",
        "DOTEUR": "DOT/EUR",
        "SUIEUR": "SUI/EUR",
        "AVAXEUR": "AVAX/EUR",
    }
    return mapping.get(normalized, pair)


async def binance_ws_loop(pairs: list[str]):
    """
    Connects to Kraken Spot WebSocket for multiple pairs.
    Handles exponential backoff and stale data tracking.
    """
    normalized_pairs = [_normalize_pair(pair) for pair in pairs]
    kraken_pairs = [_kraken_pair_name(pair) for pair in pairs]
    url = "wss://ws.kraken.com"

    retries = 0
    max_retries = 10

    logger.info(f"🔗 Connecting to Kraken Spot Feed: {url} ({', '.join(kraken_pairs)})")
    
    while retries < max_retries:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({
                    "event": "subscribe",
                    "pair": kraken_pairs,
                    "subscription": {"name": "ticker"},
                }))
                retries = 0 # reset on success
                async for message in ws:
                    data = json.loads(message)

                    if isinstance(data, dict):
                        event = data.get("event")
                        if event == "subscriptionStatus" and data.get("status") != "subscribed":
                            logger.warning(f"Kraken subscription status: {data}")
                        continue

                    if not isinstance(data, list) or len(data) < 4:
                        continue
                    if data[2] != "ticker":
                        continue

                    ticker = data[1]
                    pair = _normalize_pair(data[3])
                    if pair not in normalized_pairs:
                            continue

                    last_trade = ticker.get("c", [])
                    if not last_trade:
                        continue
                    price = float(last_trade[0])
                    ts = time.time()

                    # Best bid/ask für Limit-Orders (Maker)
                    bid_data = ticker.get("b", [])
                    ask_data = ticker.get("a", [])
                    if bid_data:
                        latest_bids[pair] = float(bid_data[0])
                    if ask_data:
                        latest_asks[pair] = float(ask_data[0])

                    latest_prices[pair] = price
                    last_update_ts[pair] = ts

                    if pair not in price_histories:
                        price_histories[pair] = []

                    price_histories[pair].append((ts, price))
                    
                    # Cleanup history (keep last 310 seconds for 300s signal window)
                    while len(price_histories[pair]) > 0 and ts - price_histories[pair][0][0] > 310.0:
                        price_histories[pair].pop(0)

        except Exception as e:
            retries += 1
            wait_time = min(2 ** retries, 60)
            logger.error(f"Kraken WS Error (Attempt {retries}/{max_retries}): {e}. Retrying in {wait_time}s...")
            if retries >= max_retries:
                logger.critical("❌ MAX WEBOCSKET RETRIES REACHED. Trading stop requested.")
            await asyncio.sleep(wait_time)

def get_latest_price(pair: str) -> float:
    pair = _normalize_pair(pair)
    if is_data_stale(pair):
        return 0.0
    return latest_prices.get(pair, 0.0)

def get_best_bid(pair: str) -> float:
    pair = _normalize_pair(pair)
    if is_data_stale(pair):
        return 0.0
    return latest_bids.get(pair, 0.0)

def get_best_ask(pair: str) -> float:
    pair = _normalize_pair(pair)
    if is_data_stale(pair):
        return 0.0
    return latest_asks.get(pair, 0.0)

def get_history(pair: str) -> list:
    pair = _normalize_pair(pair)
    if is_data_stale(pair):
        return []
    return price_histories.get(pair, [])
