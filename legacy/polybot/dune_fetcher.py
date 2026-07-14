"""
Dune Analytics Fetcher – holt Whale-Positionen alle 10 Minuten
und speichert sie als snapshot_new.json / snapshot_old.json.

Verwendung:
  - Als Cronjob: python -m polybot.dune_fetcher
  - Oder direkt importieren: from polybot.dune_fetcher import fetch_and_rotate

Dune Query muss folgende Spalten liefern:
  wallet, market_id, market_name, net_position_usd, avg_hold_days,
  trades_per_day, maker_ratio, win_rate_election, avg_position_usd,
  days_to_expiry, has_hedge, category
"""

import json
import os
import shutil
import time
import logging
import requests
from . import config

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "snapshots")
SNAPSHOT_NEW = os.path.join(SNAPSHOT_DIR, "snapshot_new.json")
SNAPSHOT_OLD = os.path.join(SNAPSHOT_DIR, "snapshot_old.json")

DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
# ID der Dune-Query, die Whale-Positionen liefert
DUNE_QUERY_ID = os.getenv("DUNE_QUERY_ID", "")


def _ensure_snapshot_dir():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def _load_json(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(data: list, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_dune_positions() -> list:
    """
    Startet eine Dune-Query und holt die Ergebnisse.
    Gibt eine Liste von Position-Dicts zurück.
    """
    if not DUNE_API_KEY or not DUNE_QUERY_ID:
        logger.warning("DUNE_API_KEY oder DUNE_QUERY_ID fehlt – verwende leere Snapshot-Liste.")
        return []

    headers = {"X-Dune-API-Key": DUNE_API_KEY, "Content-Type": "application/json"}

    # Query starten
    execute_url = f"https://api.dune.com/api/v1/query/{DUNE_QUERY_ID}/execute"
    try:
        resp = requests.post(execute_url, headers=headers, timeout=15)
        resp.raise_for_status()
        execution_id = resp.json().get("execution_id")
    except Exception as e:
        logger.error(f"Dune execute fehlgeschlagen: {e}")
        return []

    # Auf Ergebnis warten (max 150 Sekunden)
    result_url = f"https://api.dune.com/api/v1/execution/{execution_id}/results"
    for _ in range(30):
        time.sleep(5)
        try:
            r = requests.get(result_url, headers=headers, timeout=15)
            r.raise_for_status()
            body = r.json()
            state = body.get("state", "")
            if state == "QUERY_STATE_COMPLETED":
                rows = body.get("result", {}).get("rows", [])
                logger.info(f"Dune: {len(rows)} Positionen abgerufen.")
                return rows
            elif state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                logger.error(f"Dune Query fehlgeschlagen: {state}")
                return []
        except Exception as e:
            logger.error(f"Dune result poll error: {e}")

    logger.error("Dune Query Timeout (150s).")
    return []


def fetch_and_rotate() -> tuple[list, list]:
    """
    Holt neue Positionen, rotiert Snapshots und gibt
    (old_snapshot, new_snapshot) zurück.
    """
    _ensure_snapshot_dir()

    new_positions = fetch_dune_positions()

    # Wenn Dune nichts liefert, bestehenden new-Snapshot wiederverwenden
    if not new_positions:
        new_positions = _load_json(SNAPSHOT_NEW)

    old_snapshot = _load_json(SNAPSHOT_NEW)

    # Rotation: new → old
    if os.path.exists(SNAPSHOT_NEW):
        shutil.copy2(SNAPSHOT_NEW, SNAPSHOT_OLD)

    _save_json(new_positions, SNAPSHOT_NEW)
    logger.info("Snapshots rotiert: snapshot_new.json aktualisiert.")

    return old_snapshot, new_positions


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    old, new = fetch_and_rotate()
    print(f"Old: {len(old)} Positionen | New: {len(new)} Positionen")
