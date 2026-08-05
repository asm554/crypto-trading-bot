"""Lädt historische Bitvavo-Kerzen für Backtests.

Ersetzt ``download_kraken_history.py``. Der Unterschied ist gewaltig: Kraken
liefert öffentlich gar keine OHLC-Historie (``since`` wird beim Zurückblicken
ignoriert, es kommen immer die letzten ~720 Kerzen), weshalb dort Millionen
Rohtrades gezogen und selbst aggregiert werden mussten — Stunden pro Paar.
Bitvavo paginiert dagegen echte Kerzen: 1440 Stück pro Abruf, Gewicht 1 bei
1000 Punkten/Minute. Fünf Jahre SOL-EUR sind so in rund 10 Sekunden geladen.

Ein Stolperstein: ``start`` allein genügt nicht — dann liefert die API die
*neuesten* 1440 Kerzen statt der ältesten ab ``start``. Historie bekommt nur,
wer ``start`` **und** ``end`` setzt und das Fenster selbst vorwärts schiebt.

Beispiel:

    python -m backtest.download_bitvavo_history --markets SOL-EUR BTC-EUR --intervals 1h 15m
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_SCHEME = "https"
API_HOST = "api.bitvavo.com"
API = f"{API_SCHEME}://{API_HOST}/v2"

# Opener, der ausschließlich HTTPS beherrscht. Der Standard-``urlopen`` kennt
# auch ``file://`` und ``ftp://`` — ein Marktname aus der Kommandozeile könnte
# damit Dateien lesen statt die API abzufragen. Dieser Opener hat die
# entsprechenden Handler schlicht nicht.
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler())
DATA_DIR = Path(__file__).resolve().parent / "data"
MAX_CANDLES = 1440
# 1000 Gewichtspunkte/Minute, Kerzen kosten 1 Punkt. Ein kleiner Abstand hält
# uns weit unter dem Limit und bleibt trotzdem um Größenordnungen schneller
# als Krakens ~1 Anfrage/Sekunde.
REQUEST_PAUSE_SEC = 0.1
MAX_RETRIES = 5

INTERVAL_SEC = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
}


def parse_date(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _checked_request(url: str) -> urllib.request.Request:
    """Request-Objekt bauen, aber nur für https auf dem Bitvavo-Host.

    ``urlopen`` versteht auch ``file://`` und ``ftp://``. Die URL entsteht hier
    zwar nur aus CLI-Argumenten, aber ein Marktname wie ``../..`` oder ein
    kompletter ``file://``-String würde sonst Dateien lesen statt der API.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme != API_SCHEME or parts.netloc != API_HOST:
        raise ValueError(f"Nur {API_SCHEME}://{API_HOST} erlaubt, bekam: {parts.scheme}://{parts.netloc}")
    return urllib.request.Request(url, headers={"User-Agent": "polybot-backtest"})


def fetch(url: str) -> list:
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with _OPENER.open(_checked_request(url), timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            # 429 = Rate-Limit. Alles andere ist ein echter Fehler.
            if exc.code != 429 or attempt == MAX_RETRIES:
                raise
            print(f"   ⏳ Rate-Limit, warte {delay:.0f}s", flush=True)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == MAX_RETRIES:
                raise
            print(f"   ⚠️  {exc} — Versuch {attempt}/{MAX_RETRIES}", flush=True)
        time.sleep(delay)
        delay *= 2
    return []


def download(market: str, interval: str, start_ts: int, end_ts: int) -> list[tuple]:
    """Alle Kerzen im Zeitraum, chronologisch, dedupliziert."""
    step = INTERVAL_SEC[interval]
    rows: dict[int, list] = {}
    cursor = start_ts
    calls = 0
    while cursor < end_ts:
        window_end = min(cursor + MAX_CANDLES * step, end_ts)
        url = (f"{API}/{market}/candles?interval={interval}"
               f"&start={cursor * 1000}&end={window_end * 1000}&limit={MAX_CANDLES}")
        batch = fetch(url)
        calls += 1
        for row in batch:
            try:
                rows[int(row[0]) // 1000] = [float(x) for x in row[1:6]]
            except (ValueError, IndexError, TypeError):
                continue
        cursor = window_end
        time.sleep(REQUEST_PAUSE_SEC)
    print(f"   {market} {interval}: {len(rows):,} Kerzen in {calls} Calls", flush=True)
    return [(ts, *rows[ts]) for ts in sorted(rows)]


def write_csv(path: Path, rows: list[tuple], step: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        # Gleiches Schema wie der Kraken-Downloader, damit die vorhandenen
        # Backtest-Runner die Dateien unverändert lesen können. Bitvavo liefert
        # keine Trade-Zahl je Kerze — die Spalte bleibt 0, sie wird von den
        # Strategien ohnehin nicht gelesen.
        w.writerow(["timestamp", "open", "high", "low", "close", "volume", "trades"])
        for ts, o, h, l, c, v in rows:
            w.writerow([ts, o, h, l, c, v, 0])
    tmp.replace(path)
    gaps = sum(1 for a, b in zip(rows, rows[1:]) if b[0] - a[0] != step)
    span = ""
    if rows:
        f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
        span = f" | {f(rows[0][0])} bis {f(rows[-1][0])}"
    print(f"   → {path.name}: {len(rows):,} Zeilen, {gaps} Lücken{span}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--markets", nargs="+", required=True, help="z.B. SOL-EUR BTC-EUR")
    p.add_argument("--intervals", nargs="+", default=["1h", "15m"], choices=sorted(INTERVAL_SEC))
    p.add_argument("--start", default="2019-01-01", help="YYYY-MM-DD; frühere Daten liefert die API einfach nicht")
    p.add_argument("--end", default=None)
    args = p.parse_args()

    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(time.time())
    print(f"📥 Bitvavo-Historie: {', '.join(args.markets)} | {args.intervals} | ab {args.start}\n", flush=True)

    t0 = time.time()
    failed = []
    for market in args.markets:
        print(f"▶️  {market}", flush=True)
        for interval in args.intervals:
            try:
                rows = download(market, interval, start_ts, end_ts)
                if not rows:
                    print(f"   ⏭️  {interval}: keine Daten", flush=True)
                    continue
                # Dateiname ohne Bindestrich, damit die Runner ihn wie einen
                # Kraken-Altname behandeln können (SOL-EUR -> SOLEUR_60m.csv).
                name = market.replace("-", "")
                minutes = INTERVAL_SEC[interval] // 60
                write_csv(DATA_DIR / f"{name}_{minutes}m.csv", rows, INTERVAL_SEC[interval])
            except Exception as exc:
                failed.append((market, interval, str(exc)))
                print(f"   ❌ {interval}: {exc}", flush=True)
        print("", flush=True)

    print(f"🏁 Fertig in {time.time() - t0:.1f} Sekunden")
    if failed:
        print("⚠️  Fehlgeschlagen:")
        for m, i, e in failed:
            print(f"   {m} {i}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
