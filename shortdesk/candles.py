"""Kraken-Public-OHLC (eigene Kopie, kein Import aus polybot). Nur abgeschlossene Kerzen."""
import time
from typing import NamedTuple

import requests

URL = "https://api.kraken.com/0/public/OHLC"
INTERVAL_SEC = {60: 3600, 240: 14400, 1440: 86400}


class C(NamedTuple):
    ts: int      # Open-Zeit
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def fetch(pair: str, interval: int, now: float | None = None, timeout: int = 15) -> list[C]:
    r = requests.get(URL, params={"pair": pair, "interval": interval}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken {pair}: {data['error']}")
    rows = next(v for k, v in data["result"].items() if k != "last")
    return closed_only([C(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in rows],
                       interval, now)


def closed_only(rows: list[C], interval: int, now: float | None = None) -> list[C]:
    now = time.time() if now is None else now
    return [c for c in rows if c.ts + INTERVAL_SEC[interval] <= now]


class Market:
    """Dünner Wrapper, damit Tests eine Fake-Quelle injizieren können."""

    def __init__(self, sleep: float = 1.0):
        self.sleep = sleep

    def candles(self, pair: str, interval: int) -> list[C]:
        out = fetch(pair, interval)
        time.sleep(self.sleep)
        return out
