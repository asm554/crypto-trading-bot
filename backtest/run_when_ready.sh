#!/usr/bin/env bash
# Wartet auf das Ende des Kraken-Downloads und startet dann die Backtests.
# Der Download läuft je nach Trade-Dichte 6-10 Stunden; dieses Skript pollt
# den Prozess, statt eine feste Zeit zu raten.
set -uo pipefail
cd "$(dirname "$0")/.."

LOG=backtest/logs/backtest.log
mkdir -p backtest/logs
: > "$LOG"

echo "⏳ Warte auf Ende des Downloads ..." | tee -a "$LOG"
while pgrep -f 'download_kraken_history' > /dev/null; do
  sleep 300
done
echo "✅ Download beendet um $(date '+%Y-%m-%d %H:%M')" | tee -a "$LOG"
tail -3 backtest/logs/download.log | tee -a "$LOG"
echo "" | tee -a "$LOG"

# SOLEUR ist das Paar, das der Surfer live handelt — alles andere ist
# Robustheitsprüfung derselben Parameter gegen andere Märkte.
for PAIR in SOLEUR XBTEUR ETHEUR ADAEUR XRPEUR; do
  if [ ! -f "backtest/data/${PAIR}_60m.csv" ]; then
    echo "⏭️  $PAIR: keine Daten, übersprungen" | tee -a "$LOG"
    continue
  fi
  echo "▶️  Backtest $PAIR" | tee -a "$LOG"
  python3 -m backtest.backtest_surfer \
    --pair "$PAIR" --start 2024-01-01 --quiet \
    --json-out "backtest/results/surfer_${PAIR}.json" 2>&1 | tee -a "$LOG"
done

echo "🏁 Backtests fertig um $(date '+%Y-%m-%d %H:%M')" | tee -a "$LOG"
