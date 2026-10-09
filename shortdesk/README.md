# shortdesk

Eigenständiges Short-only-Desk. Keine Abhängigkeit zu `polybot/`, eigene DB (`shortdesk/data/shortdesk.db`), eigene Tests.
**Es gibt keinen Order-Code.** Das Desk liefert nur Alerts; du entscheidest. Positionen sind Paper.

| Rolle | Modul | Aufgabe |
|---|---|---|
| Screener | `screener.py` | Daily-Drei-Kerzen-Flip (short). Bullische Flips nur Kontext. PRIME = 1D runter, 4H bounct |
| Cartographer | `cartographer.py` | Bearische 4H-FVGs Anker → Tief, Leiter, TESTED bei Docht, 1H-Bestätigung |
| Risk Officer | `risk.py` | Size aus Konto/Stop/Risiko. Add 0,5 % nur wenn Vorgänger ≥ 1R im Gewinn |
| Gate | `gate.py` | Telegram-Alert, Antwort `TAKEN <id>` / `SKIP <id>`. Offen > 4h → SKIPPED |
| Exit Clerk | `exitclerk.py` | Stop, Daily-Close über Anker, STALE nach 72h flat. Neutral im Gewinn = halten |
| Auditor | `auditor.py` | Journal in R, Gebühren/Slippage getrennt, Go/No-Go gegen eingefrorene Schwellen |

Schwellen (`config.py`): ≥ 40 Trades, Win-Rate ≥ 33 %, Expectancy ≥ +0,4R, Max-DD ≤ 20 %. Der Hash wird beim ersten Lauf
gespeichert; ändert man sie danach, verweigert der Auditor den Report.

Hinweis: Der Stop ist eine Ergänzung zu den Artikel-Regeln (ohne Stop gibt es kein Risiko-Sizing). Ausgeschlagene Alerts laufen als
Schatten-Trades mit und erscheinen im Report als „Was-wäre-wenn“.

## Betrieb

```bash
pip install requests
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...   # optional, sonst nur Konsole
python -m shortdesk daily      # nach Daily-Close: Screener → Exit Clerk
python -m shortdesk 4h         # nach 4H-Close: Antworten abholen, Exits prüfen
python -m shortdesk 1h         # nach 1H-Close: Cartographer → Risk → Gate → Exits
python -m shortdesk weekly     # Sonntag: Auditor-Report
python -m shortdesk status | journal | decide <id> taken|skipped
python -m pytest shortdesk/tests -q
```

Cron (UTC, mit 2 Min Versatz nach Kerzenschluss):
```
2 0 * * *    cd /root/crypto-trading-bot && python -m shortdesk daily
2 */4 * * *  cd /root/crypto-trading-bot && python -m shortdesk 4h
2 * * * *    cd /root/crypto-trading-bot && python -m shortdesk 1h
5 18 * * 0   cd /root/crypto-trading-bot && python -m shortdesk weekly
```

Datenquelle: Kraken-Spot-OHLC (USD-Paare). Kraken Spot kann nicht shorten, die Shorts sind simuliert. Für echte Shorts müsste die
Datenquelle auf die Read-only-API einer Futures-Börse umgestellt werden.
