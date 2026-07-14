import os
import json
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "markets")

# Stelle sicher, dass der Ordner existiert
os.makedirs(DATA_DIR, exist_ok=True)

def _get_filepath(market_id: str) -> str:
    return os.path.join(DATA_DIR, f"{market_id}.json")

def log_snapshot(market_id: str, question: str, weather_forecast: dict, orderbook: dict):
    """Speichert einen initialen oder stündlichen Snapshot des Marktes zur Bayesian Calibration."""
    filepath = _get_filepath(market_id)
    
    data = {"market_id": market_id, "question": question, "snapshots": [], "trades": []}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except Exception:
            pass

    data["snapshots"].append({
        "timestamp": time.time(),
        "weather_data": weather_forecast,
        "orderbook_state": orderbook
    })

    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

def log_trade(
    market_id: str,
    side: str,
    price: float,
    size: float,
    ev_at_time: float,
    *,
    fee_usdc: float | None = None,
    fee_per_share: float | None = None,
    ev_gross_per_share: float | None = None,
):
    """Speichert eine Execution zur EV-Validierung (ev = netto nach Gebühren, falls angegeben)."""
    filepath = _get_filepath(market_id)
    if not os.path.exists(filepath):
        return  # Falls Markt nicht getrackt wird
        
    with open(filepath, "r") as f:
        data = json.load(f)

    entry = {
        "timestamp": time.time(),
        "side": side,
        "price": price,
        "size": size,
        "expected_value_percent": ev_at_time,
    }
    if fee_usdc is not None:
        entry["fee_usdc"] = fee_usdc
    if fee_per_share is not None:
        entry["fee_per_share"] = fee_per_share
    if ev_gross_per_share is not None:
        entry["ev_gross_per_share"] = ev_gross_per_share

    data["trades"].append(entry)
    
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)
