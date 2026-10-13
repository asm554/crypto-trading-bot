# jevdesk

Eigenständiges Paper-Desk für frische DEX-Token (Solana, BSC, Robinhood). Keine Abhängigkeit zu `polybot/` oder `shortdesk/`,
eigene DB (`jevdesk/data/jevdesk.db`). **Kein Order-Code, keine FOMO-Anbindung**: Positionen sind Paper.

Prinzip: Code holt, der Judge urteilt, Code entscheidet.

```
GeckoTerminal new_pools (3 Chains)  -> Metriken stecken schon im Pool (kein FOMO-Login)
Gratis-Filter (Alter, Liquidität, Volumen, MCap, Trades) -> kein Netzwerk
Dossier (GT info + Solana-RPC Top-Wallet), max. 3 pro Zyklus (GT Free: 10 Calls/min)
Fakten-Filter (Top-Wallet, Top-10, Holder, offene Mint/Freeze, Honeypot)
Judge: "market" + Chain-Satz (solana|bsc|robinhood), je EIN Aufruf pro Token
Soft-Schwellen (thresholds in config.py) -> Bench nach Ablehnungsgrund
Pick: Choice über die Überlebenden + "lohnt der Tag" (Noul). Kein Trade ist ein normales Ergebnis
Paper-Position (nur eine), Exit: 6h-Volumen < 20 % des Ø (Artikelregel), plus Stop -35 % und 24h-Zeitlimit
```

Judge: mit `TYPESAFE_API_KEY` ruft das Desk Jev (`api.typesafe.ai/v1/systemone`, 422 nie wiederholen, 429/529 mit Backoff).
Ohne Key läuft ein grober Regel-Fallback; das Modell steht dann als `rules-fallback` im Log. Fällt Jev aus, steht das Desk
(kein Raten). Der Fall „Jev nicht erreichbar“ erscheint im Zyklus-Log.

Nicht in v1: X-/Social-Lesen (immer Size-Faktor 0,6), FOMO-Execution, Echtgeld.
Schwellen sind die des Artikels, nicht auf eigene Daten getunt. Erst eine Woche beobachten, dann `config.py` anpassen.

## Überwachung per Telegram

Der Bot meldet Paper-Entries, Paper-Exits und einen Tages-Digest. Kommandos (nur dein Chat): `/status` `/pos` `/report` `/help`.
Zusätzlich auf dem Server: `python -m jevdesk status | pos | report`, `journalctl -u jevdesk -f`.

## Betrieb

```bash
pip install requests
printf 'TELEGRAM_BOT_TOKEN=...\nTELEGRAM_CHAT_ID=...\nTYPESAFE_API_KEY=...\n' > jevdesk/.env && chmod 600 jevdesk/.env
python -m jevdesk once      # ein Zyklus zum Testen
python -m jevdesk run       # Dauerbetrieb: Zyklus alle 15 min, Monitor alle 5 min, Telegram-Polling
python -m jevdesk web       # lokale Oberfläche: http://127.0.0.1:8765 (nur lesend)
python -m pytest jevdesk/tests -q
```

systemd: `systemd/jevdesk.service.example` nach `/etc/systemd/system/jevdesk.service`, dann
`systemctl daemon-reload && systemctl enable --now jevdesk`.
