#!/usr/bin/env bash
# Einrichtung in einem Rutsch: Token abfragen, Chat-ID finden, Test-Nachricht, Testzyklus, optional systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="python3"; [ -x bot-env/bin/python3 ] && PY="bot-env/bin/python3"
"$PY" -m pip install -q requests >/dev/null 2>&1 || true

echo "1/4 Telegram-Token (von @BotFather). Eingabe bleibt unsichtbar:"
read -rs TOKEN; echo
curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe" >/dev/null || { echo "Token ungueltig oder kein Internet."; exit 1; }
echo "Token ok."

echo "2/4 Schreib deinem Bot jetzt 'hi' in Telegram, dann druecke Enter."
read -r _
CHAT=$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getUpdates" | "$PY" -c '
import sys, json
r = json.load(sys.stdin).get("result", [])
ids = [u["message"]["chat"]["id"] for u in r if "message" in u]
print(ids[-1] if ids else "")')
[ -n "$CHAT" ] || { echo "Keine Nachricht gefunden. Schreib dem Bot 'hi' und starte das Skript neu."; exit 1; }
echo "Chat-ID: $CHAT"

echo "3/4 Jev-Key (TYPESAFE_API_KEY). Enter = ohne Key, dann laeuft der Regel-Fallback:"
read -rs JEV; echo

umask 077
{ echo "TELEGRAM_BOT_TOKEN=${TOKEN}"; echo "TELEGRAM_CHAT_ID=${CHAT}"; [ -n "$JEV" ] && echo "TYPESAFE_API_KEY=${JEV}"; } > jevdesk/.env
chmod 600 jevdesk/.env
set -a; . jevdesk/.env; set +a

curl -fsS "https://api.telegram.org/bot${TOKEN}/sendMessage" -d "chat_id=${CHAT}" -d "text=jevdesk verbunden. Testzyklus startet." >/dev/null
echo "Test-Nachricht gesendet. Schau in Telegram."

echo "4/4 Testzyklus:"
"$PY" -m jevdesk once

read -rp "Als Dauerdienst einrichten (systemd)? [j/N] " A
if [ "${A:-n}" = "j" ]; then
  sed "s#/root/crypto-trading-bot#$(pwd)#g" systemd/jevdesk.service.example | sudo tee /etc/systemd/system/jevdesk.service >/dev/null
  sudo systemctl daemon-reload && sudo systemctl enable --now jevdesk
  echo "Laeuft. Status: systemctl status jevdesk"
fi
echo "Fertig. In Telegram: /status"
