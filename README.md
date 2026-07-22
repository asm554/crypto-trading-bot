# Polybot Crypto Paper Trading Bot

Standalone, secrets-freier Export des Crypto-/Paper-Trading-Bots aus `/root/polyarbi`.

Dieses Repository ist dafür gedacht, den Bot mit mehreren Leuten weiterzuentwickeln, ohne Server-State, Datenbanken, Logs oder echte API-Keys mitzucommitten.

## Enthalten

Kerncode im aktiven Hauptbaum:

- `polybot/dca_strategy.py` + `polybot/main_dca.py`  
  **Der Brave** — Conservative Recovery DCA, hart Paper-only.
- `polybot/momentum_strategy.py` + `polybot/main_momentum.py`  
  **Der Zocker** — Momentum + Trailing Stop, hart Paper-only.
- `polybot/meanrev_strategy.py` + `polybot/main_meanrev.py`  
  **Der Contrarian** — Mean-Reversion mit RSI/OHLC, hart Paper-only.
- `polybot/futures_strategy.py` + `polybot/main_futures.py`
  **Der Hebler** — Kraken Perpetual Futures long/short mit 2× Paper-Hebel, Funding und Liquidationsmodell.
- `polybot/battle_report.py`  
  Telegram Battle-Report nach Netto-Equity.
- `polybot/paper_db.py`  
  SQLite Paper-Trading DB und Equity-Snapshots.
- `systemd/*.example`  
  Beispiel-Units für Serverbetrieb.
- `tests/`  
  Regressionstests für DCA/Overview.

Legacy-/Experimentcode liegt bewusst getrennt unter `legacy/`, damit neue Mitentwickler den Battle-Kern klar erkennen.

## Nicht enthalten

Bewusst ausgeschlossen:

- echte `.env` Dateien
- Telegram-/API-/Exchange-Secrets
- SQLite-Datenbanken
- Logs
- Runtime-State wie `dca_state.json`, `momentum_state.json`, `meanrev_state.json`
- Backups und alte `.bak` Dateien
- virtuelle Umgebungen

## Setup lokal

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r polybot/requirements.txt
cp polybot/.env.example polybot/.env
```

Dann `polybot/.env` lokal befüllen. Für Paper-Mode reichen Public Kraken Endpoints; Telegram ist optional für Reports/Alerts.

Wichtig:

```env
PAPER_MODE=true
```

## Start im Paper-Modus

```bash
# Conservative Recovery DCA
python -m polybot.main_dca

# Momentum Bot
python -m polybot.main_momentum

# Mean-Reversion Bot
python -m polybot.main_meanrev

# Perpetual-Futures Bot (paper-only)
python -m polybot.main_futures

# Battle-Report manuell
python -m polybot.battle_report
```

## Serverbetrieb mit systemd

Die Dateien unter `systemd/*.example` sind Vorlagen. Auf dem Server z. B.:

```bash
sudo cp systemd/polybot-dca.service.example /etc/systemd/system/polybot-dca.service
sudo systemctl daemon-reload
sudo systemctl enable --now polybot-dca.service
```

Passe vorher unbedingt `WorkingDirectory` und `ExecStart` an deinen Installationspfad an.

## Entwicklungsregeln

1. Keine Secrets committen.
2. Keine Datenbanken/Logs/State-Dateien committen.
3. Jede Strategieänderung mit Tests oder mindestens `py_compile` prüfen.
4. Paper-only bleibt Default. Live-Trading darf nicht versehentlich aktiviert werden.
5. Effektive Strategieparameter im Serverbetrieb in systemd `Environment=` setzen, weil Unit-Env Code-Defaults überschreibt.

## Nützliche Checks

```bash
python -m py_compile polybot/dca_strategy.py polybot/main_dca.py polybot/momentum_strategy.py polybot/main_momentum.py polybot/meanrev_strategy.py polybot/main_meanrev.py polybot/battle_report.py
python -m pytest -q
```

Die technische und regulatorische Futures-Analyse steht unter
[`docs/futures-trading-analysis.md`](docs/futures-trading-analysis.md).

## Strategie-Battle KPI

Verglichen wird ausschließlich **Netto-Equity**:

```text
Cash + Mark-to-Market offener Positionen - simulierte Verkaufsgebühren
```

Nicht nur realisierter PnL, weil das DCA-System sonst offene Verluste ausblenden würde.
