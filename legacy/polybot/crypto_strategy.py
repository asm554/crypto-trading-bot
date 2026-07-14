import logging
import time
from polybot import config
from polybot.binance_ws import get_history

logger = logging.getLogger(__name__)

# 5-Minuten-Fenster für echte Trend-Erkennung (größere Moves, profitabel nach Fees)
SIGNAL_WINDOW_SEC = 300.0

# Pro-Pair Detection-Log-Cooldown: verhindert Log-Spam wenn Edge dauerhaft aktiv ist
_last_detection_log: dict[str, float] = {}
DETECTION_LOG_COOLDOWN_SEC = 60.0


def _translate_edge_to_probability(kraken_edge: float) -> float:
    """
    Übersetzt Kraken-Preisdelta konservativ in eine Polymarket-Win-Wahrscheinlichkeit.

    Logik:
    - <0.05%  → reines Rauschen, keine Edge
    - 0.05%-0.20% → schwacher Trend, minimal Confidence (+0.3%)
    - >0.20%  → klarer Trend, Confidence bis max +1% (gekappt)

    Ziel: Kelly-Formel erhält eine realistische Wahrscheinlichkeit, nicht EUR-Preisrauschen.
    """
    abs_edge = abs(kraken_edge)

    if abs_edge < 0.0005:  # <0.05%
        return 0.50  # Kein Signal → kein Trade

    # Confidence wächst mit Edge, aber gekappt bei +1%
    # Bei 0.20% Edge: confidence = min(0.01, 0.20% * 0.05) = 0.001 → p=0.501
    confidence = min(0.01, abs_edge * 0.05)

    if kraken_edge > 0:
        return 0.50 + confidence
    else:
        return 0.50 - confidence


_last_scan_log: float = 0.0

def check_hft_signals(pairs: list[str]) -> list[dict]:
    """
    Checks price history for multiple pairs over 5-min window for real trends.
    """
    global _last_scan_log
    detected_signals = []
    now = time.time()

    # Status-Log alle 5 Minuten
    if now - _last_scan_log >= 300:
        for p in pairs:
            h = get_history(p)
            if h:
                delta = (h[-1][1] - h[0][1]) / h[0][1] * 100 if h[0][1] else 0
                logger.info(f"📈 {p.upper()}: {h[-1][1]:.4f} | Δ5m={delta:+.3f}% | ticks={len(h)}")
        _last_scan_log = now

    for pair in pairs:
        history = get_history(pair)
        if len(history) < 2:
            continue

        current_time = time.time()
        current_price = history[-1][1]

        # Find price from 60 seconds ago (real trends, not spread-noise)
        window_price = None
        for entry in reversed(history):
            ts, price = entry
            if current_time - ts >= SIGNAL_WINDOW_SEC:
                window_price = price
                break
        # If no 60s old price, use oldest price in history
        if window_price is None and history:
            window_price = history[0][1]

        if window_price is None or window_price == 0:
            continue

        delta_pct = (current_price - window_price) / window_price
        abs_delta = abs(delta_pct)

        # 1. Detection Threshold - Log but don't trade (max 1x/min pro Pair)
        if abs_delta >= config.MIN_DETECTION_EDGE:
            direction = "DOWN" if delta_pct > 0 else "UP"  # Mean-Reversion: Preis stieg → fällt zurück
            now = time.time()
            if now - _last_detection_log.get(pair, 0) >= DETECTION_LOG_COOLDOWN_SEC:
                logger.info(f"👀 EDGE DETECTED: {pair.upper()} {direction} {delta_pct:.2%}")
                _last_detection_log[pair] = now

            # 2. Execution Threshold - Trigger trade if edge is real (not noise)
            if abs_delta >= config.MIN_EXECUTION_EDGE:
                logger.warning(f"🚨 EXECUTION TRIGGER: {pair.upper()} {direction} {delta_pct:.2%}")

                # Translate Kraken-EUR edge to Polymarket probability
                kelly_prob = _translate_edge_to_probability(delta_pct)

                detected_signals.append({
                    "pair": pair,
                    "signal": direction,
                    "delta": delta_pct,
                    "edge": abs_delta,
                    "kelly_prob": kelly_prob,  # NEU: übersetzte Wahrscheinlichkeit für Kelly
                })

    return detected_signals
