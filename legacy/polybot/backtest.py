"""
Backtesting-Modul: simuliert Whale-Tracker über historische Snapshots.

Verwendung:
  python -m polybot.backtest

Datenquellen:
  - snapshots/ (snapshot_old.json, snapshot_new.json)
  - data/markets/{market_id}.json (aus data_logger.py)

Für echtes 6-Monats-Backtesting: füge historische Dune-Snapshots
als Liste von snapshot_*.json in den snapshots/-Ordner ein.

Output: backtest_report.json + Terminalzusammenfassung
"""

import json
import os
import glob
import logging
from datetime import datetime
from .whale_tracker import classify_whale, apply_hard_filters, calculate_risk

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "snapshots")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "markets")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "backtest_report.json")

# Simuliertes Kapital für Backtesting
BACKTEST_BANKROLL = 10_000


def _load_json(path: str) -> list | dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _detect_signals(old_snapshot: list, new_snapshot: list) -> list:
    """Einfache USD-Delta-Detection für Backtesting."""
    old_dict = {p["wallet"] + "_" + p.get("market_id", ""): p for p in old_snapshot}
    signals = []

    for pos in new_snapshot:
        key = pos["wallet"] + "_" + pos.get("market_id", "")
        old_pos = old_dict.get(key)
        old_value = old_pos["net_position_usd"] if old_pos else 0
        delta = pos.get("net_position_usd", 0) - old_value

        if delta > 8000:
            signals.append({**pos, "delta_usd": round(delta, 2)})

    return signals


def _simulate_trade(signal: dict, resolved_yes: bool) -> float:
    """
    Simuliert PnL eines Trades.
    Nimmt an: Einstieg zum Preis 0.5 (worst case), Auflösung 1.0 oder 0.0.
    Gibt PnL in USDC zurück.
    """
    risk = calculate_risk(signal, BACKTEST_BANKROLL)
    size_usdc = risk["recommended_usdc"]
    entry_price = 0.50  # konservativ
    outcome_value = 1.0 if resolved_yes else 0.0
    shares = size_usdc / entry_price
    pnl = shares * outcome_value - size_usdc
    return round(pnl, 2)


def run_backtest(snapshot_pairs: list[tuple[list, list]], resolutions: dict = None) -> dict:
    """
    snapshot_pairs: Liste von (old_snapshot, new_snapshot) Tupeln
    resolutions: { market_id: True/False } – ob Markt mit YES aufgelöst wurde
    Gibt Backtest-Report zurück.
    """
    resolutions = resolutions or {}
    total_signals = 0
    passed_filter = 0
    directional = 0
    total_pnl = 0.0
    wins = 0
    losses = 0
    score_tier_stats: dict[str, dict] = {
        "65-79": {"count": 0, "pnl": 0.0, "wins": 0},
        "80-100": {"count": 0, "pnl": 0.0, "wins": 0},
    }

    for old_snap, new_snap in snapshot_pairs:
        signals = _detect_signals(old_snap, new_snap)
        total_signals += len(signals)

        for signal in signals:
            classified = classify_whale(signal)
            if classified["type"] != "DIRECTIONAL":
                continue
            directional += 1

            if not apply_hard_filters(signal):
                continue
            passed_filter += 1

            market_id = signal.get("market_id", "")
            resolved_yes = resolutions.get(market_id, None)
            if resolved_yes is None:
                continue  # kein Outcome bekannt → überspringen

            pnl = _simulate_trade(signal, resolved_yes)
            total_pnl += pnl

            score = classified["whale_score"]
            tier = "80-100" if score >= 80 else "65-79"
            score_tier_stats[tier]["count"] += 1
            score_tier_stats[tier]["pnl"] += pnl
            if pnl > 0:
                wins += 1
                score_tier_stats[tier]["wins"] += 1
            else:
                losses += 1

    total_trades = wins + losses
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    roi = (total_pnl / BACKTEST_BANKROLL) * 100

    report = {
        "generated_at": datetime.now().isoformat(),
        "bankroll_usdc": BACKTEST_BANKROLL,
        "total_signals_detected": total_signals,
        "directional_signals": directional,
        "passed_hard_filters": passed_filter,
        "simulated_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate * 100, 1),
        "total_pnl_usdc": round(total_pnl, 2),
        "roi_pct": round(roi, 2),
        "score_tiers": score_tier_stats,
    }
    return report


def fetch_resolutions(market_ids: list) -> dict:
    """Holt Auflösungsstatus über Polymarket Gamma API."""
    import requests
    result = {}
    for mid in market_ids:
        if not mid:
            continue
        try:
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets/{mid}",
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("resolved"):
                    result[mid] = str(data.get("outcome", "")).upper() == "YES"
        except Exception:
            pass
    if result:
        logger.info(f"Resolutions geladen: {len(result)} Märkte aufgelöst.")
    return result


def main():
    logging.basicConfig(level=logging.INFO)

    # Alle Snapshot-Paare aus snapshots/ laden
    snapshot_files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "snapshot_*.json")))
    if len(snapshot_files) < 2:
        # Fallback: nur old/new
        old = _load_json(os.path.join(SNAPSHOT_DIR, "snapshot_old.json"))
        new = _load_json(os.path.join(SNAPSHOT_DIR, "snapshot_new.json"))
        pairs = [(old, new)] if old or new else []
    else:
        pairs = [
            (_load_json(snapshot_files[i]), _load_json(snapshot_files[i + 1]))
            for i in range(len(snapshot_files) - 1)
        ]

    if not pairs:
        print("⚠️  Keine Snapshot-Daten gefunden. Bitte zuerst Dune-Fetcher laufen lassen.")
        return

    # Market IDs aus allen Snapshots sammeln → Resolutions holen
    all_market_ids = set()
    for old_s, new_s in pairs:
        for pos in old_s + new_s:
            mid = pos.get("market_id", "")
            if mid:
                all_market_ids.add(mid)
    print(f"🌐 Hole Resolutions für {len(all_market_ids)} Märkte…")
    resolutions = fetch_resolutions(list(all_market_ids))
    print(f"✅ {len(resolutions)} Märkte aufgelöst.")

    print(f"🔍 Analysiere {len(pairs)} Snapshot-Paare...")
    report = run_backtest(pairs, resolutions)

    # Report speichern
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 50)
    print("📊 BACKTEST REPORT")
    print("=" * 50)
    print(f"Signals erkannt:     {report['total_signals_detected']}")
    print(f"Directional Whales:  {report['directional_signals']}")
    print(f"Nach Filtern:        {report['passed_hard_filters']}")
    print(f"Simulierte Trades:   {report['simulated_trades']}")
    print(f"Win-Rate:            {report['win_rate_pct']}%")
    print(f"PnL:                 ${report['total_pnl_usdc']:,.2f}")
    print(f"ROI:                 {report['roi_pct']}%")
    print(f"\nScore 65-79:  {report['score_tiers']['65-79']['count']} Trades, "
          f"PnL ${report['score_tiers']['65-79']['pnl']:,.2f}")
    print(f"Score 80-100: {report['score_tiers']['80-100']['count']} Trades, "
          f"PnL ${report['score_tiers']['80-100']['pnl']:,.2f}")
    print(f"\n✅ Report gespeichert: {REPORT_PATH}")


if __name__ == "__main__":
    main()
