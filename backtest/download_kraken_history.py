"""Lädt historische Kraken-Kerzen für Backtests.

Krakens ``/0/public/OHLC`` ignoriert ``since`` beim Zurückblicken und liefert
immer nur die letzten ~720 Kerzen (bei 1h also ~30 Tage). Echte Historie gibt es
öffentlich nur über ``/0/public/Trades``, das sich per ``last``-Cursor vorwärts
paginieren lässt. Dieses Skript zieht deshalb Trades und aggregiert sie sofort
zu OHLCV-Kerzen — die Rohtrades werden nie gespeichert (2 Jahre BTC/EUR wären
mehrere Millionen Zeilen, die fertigen Stundenkerzen sind ~17.500).

Der Lauf ist unterbrechbar: nach jedem Flush landen Cursor und Kerzen auf der
Platte, ein erneuter Start setzt am gespeicherten Cursor fort.

Beispiel:

    python -m backtest.download_kraken_history --pairs XBTEUR SOLEUR --start 2024-01-01
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

KRAKEN_TRADES_URL = "https://api.kraken.com/0/public/Trades"
DATA_DIR = Path(__file__).resolve().parent / "data"

# Kraken erlaubt öffentlichen Endpunkten grob 1 Anfrage/Sekunde, bevor der
# Zähler greift. Etwas Luft nach oben lassen — ein Rate-Limit-Fehler kostet mehr
# Zeit als der zusätzliche Abstand.
REQUEST_INTERVAL_SEC = 1.1
FLUSH_EVERY_CALLS = 50
MAX_RETRIES = 5


def parse_date(value: str) -> int:
    """'2024-01-01' -> Unix-Sekunden (UTC)."""
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def fetch_trades(pair: str, since_ns: int) -> tuple[list, int]:
    """Ein Trades-Batch ab ``since_ns`` (Nanosekunden-Cursor).

    Gibt ``(zeilen, naechster_cursor)`` zurück. Bei wiederholtem Fehlschlag wird
    die Exception nach oben gereicht — der Aufrufer hat dann bereits geflushte
    Kerzen und kann später fortsetzen.
    """
    url = f"{KRAKEN_TRADES_URL}?pair={pair}&since={since_ns}"
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == MAX_RETRIES:
                raise
            print(f"   ⚠️  {pair}: {exc} — Versuch {attempt}/{MAX_RETRIES}, warte {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay *= 2
            continue

        errors = payload.get("error") or []
        if errors:
            # Rate-Limit ist vorübergehend, unbekannte Paare sind es nicht.
            if any("Rate limit" in str(e) for e in errors):
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"{pair}: Rate-Limit hält an: {errors}")
                print(f"   ⏳ {pair}: Rate-Limit, warte {delay:.0f}s", flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"{pair}: Kraken-Fehler {errors}")

        result = payload.get("result", {})
        rows = next((v for k, v in result.items() if k != "last" and isinstance(v, list)), [])
        return rows, int(result.get("last", since_ns))
    raise RuntimeError(f"{pair}: keine Antwort nach {MAX_RETRIES} Versuchen")


class CandleBuilder:
    """Aggregiert Trades laufend zu OHLCV-Kerzen eines Intervalls."""

    def __init__(self, interval_min: int):
        self.interval_sec = interval_min * 60
        self.candles: dict[int, list[float]] = {}

    def add(self, ts: float, price: float, volume: float) -> None:
        bucket = int(ts // self.interval_sec) * self.interval_sec
        candle = self.candles.get(bucket)
        if candle is None:
            # [open, high, low, close, volume, trades]
            self.candles[bucket] = [price, price, price, price, volume, 1]
            return
        candle[1] = max(candle[1], price)
        candle[2] = min(candle[2], price)
        candle[3] = price
        candle[4] += volume
        candle[5] += 1

    def rows(self) -> list[list]:
        return [[ts, *self.candles[ts]] for ts in sorted(self.candles)]


def write_csv(path: Path, rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "trades"])
        writer.writerows(rows)
    tmp.replace(path)


def load_progress(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def download_pair(pair: str, start_ts: int, end_ts: int, intervals: list[int], resume: bool) -> None:
    progress_path = DATA_DIR / f"{pair}.progress.json"
    builders = {iv: CandleBuilder(iv) for iv in intervals}

    cursor = start_ts * 1_000_000_000
    trade_count = 0
    if resume:
        saved = load_progress(progress_path)
        if saved.get("cursor"):
            cursor = int(saved["cursor"])
            trade_count = int(saved.get("trades", 0))
            # Bereits geschriebene Kerzen zurücklesen, damit der Flush sie nicht
            # überschreibt. Die jeweils letzte Kerze ist womöglich unvollständig
            # und wird durch neue Trades korrekt weitergeführt.
            for iv in intervals:
                csv_path = DATA_DIR / f"{pair}_{iv}m.csv"
                if not csv_path.exists():
                    continue
                with csv_path.open() as fh:
                    for row in csv.DictReader(fh):
                        builders[iv].candles[int(row["timestamp"])] = [
                            float(row["open"]), float(row["high"]), float(row["low"]),
                            float(row["close"]), float(row["volume"]), int(row["trades"]),
                        ]
            print(f"   ♻️  Fortsetzung ab {datetime.fromtimestamp(cursor / 1e9, timezone.utc):%Y-%m-%d %H:%M} "
                  f"({trade_count:,} Trades bereits verarbeitet)", flush=True)

    def flush() -> None:
        for iv, builder in builders.items():
            write_csv(DATA_DIR / f"{pair}_{iv}m.csv", builder.rows())
        progress_path.write_text(json.dumps({"cursor": cursor, "trades": trade_count}))

    calls = 0
    started = time.time()
    last_request = 0.0
    while cursor < end_ts * 1_000_000_000:
        wait = REQUEST_INTERVAL_SEC - (time.time() - last_request)
        if wait > 0:
            time.sleep(wait)
        last_request = time.time()

        try:
            rows, next_cursor = fetch_trades(pair, cursor)
        except Exception as exc:
            flush()
            print(f"   ❌ {pair}: abgebrochen bei {calls} Calls: {exc}", flush=True)
            raise

        calls += 1
        if not rows:
            break

        for row in rows:
            try:
                price, volume, ts = float(row[0]), float(row[1]), float(row[2])
            except (ValueError, IndexError):
                continue
            if ts > end_ts:
                continue
            for builder in builders.values():
                builder.add(ts, price, volume)
            trade_count += 1

        # Kein Fortschritt mehr: Kraken hat keine neueren Trades.
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if calls % FLUSH_EVERY_CALLS == 0:
            flush()
            newest = datetime.fromtimestamp(cursor / 1e9, timezone.utc)
            span = max(end_ts - start_ts, 1)
            done = min(max((cursor / 1e9 - start_ts) / span, 0.0), 1.0)
            elapsed = time.time() - started
            eta = (elapsed / done - elapsed) / 60 if done > 0.01 else 0
            print(f"   {pair}: {done * 100:5.1f}% | bis {newest:%Y-%m-%d} | "
                  f"{trade_count:>9,} Trades | {calls:>5} Calls | ETA {eta:5.1f} Min", flush=True)

    flush()
    for iv in intervals:
        print(f"   ✅ {pair} {iv}m: {len(builders[iv].candles):,} Kerzen -> "
              f"{DATA_DIR / f'{pair}_{iv}m.csv'}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pairs", nargs="+", required=True, help="Kraken-Altnames, z.B. XBTEUR SOLEUR")
    parser.add_argument("--start", default="2024-01-01", help="Startdatum YYYY-MM-DD (UTC)")
    parser.add_argument("--end", default=None, help="Enddatum YYYY-MM-DD (UTC), Standard: jetzt")
    parser.add_argument("--intervals", nargs="+", type=int, default=[60, 15], help="Kerzenintervalle in Minuten")
    parser.add_argument("--no-resume", action="store_true", help="Gespeicherten Fortschritt ignorieren")
    args = parser.parse_args()

    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else int(time.time())
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"📥 Kraken-Historie: {', '.join(args.pairs)}")
    print(f"   Zeitraum {args.start} bis {args.end or 'jetzt'} | Intervalle {args.intervals} Min")
    print(f"   Ziel: {DATA_DIR}\n", flush=True)

    failed = []
    for pair in args.pairs:
        print(f"▶️  {pair}", flush=True)
        try:
            download_pair(pair, start_ts, end_ts, args.intervals, resume=not args.no_resume)
        except Exception as exc:
            failed.append((pair, str(exc)))
            print(f"   übersprungen: {exc}\n", flush=True)
            continue
        print("", flush=True)

    if failed:
        print("⚠️  Nicht abgeschlossen (erneuter Lauf setzt fort):")
        for pair, err in failed:
            print(f"   {pair}: {err}")
        return 1
    print("🏁 Fertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
