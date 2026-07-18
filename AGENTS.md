# AGENTS.md

Guidance for coding assistants working in this repository.

## Scope

This repo contains the standalone Crypto/Paper-Trading bot package exported from `/root/polyarbi`.

Primary maintained bots:

- `polybot.main_dca` — Conservative Recovery DCA / "Der Brave"
- `polybot.main_momentum` — Momentum + Trailing Stop / "Der Zocker"
- `polybot.main_meanrev` — Mean-Reversion + RSI confirmation / "Der Contrarian"
- `polybot.battle_report` — Telegram/console equity comparison report

Legacy experiments from the original server tree live under `legacy/`. Do not modify or revive them unless the task explicitly asks for it.

## Safety Rules

- Default is paper trading only.
- Do not add real exchange order execution without explicit review.
- Never commit `.env`, API keys, Telegram tokens, SQLite DBs, logs, state files, or backups.
- Compare strategy performance by net equity, not realized PnL alone.
- If changing strategy parameters for systemd deployment, update the relevant `Environment=` lines too.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r polybot/requirements.txt
cp polybot/.env.example polybot/.env
```

## Run

```bash
python -m polybot.main_dca
python -m polybot.main_momentum
python -m polybot.main_meanrev
python -m polybot.battle_report
```

## Verify

```bash
python -m py_compile polybot/dca_strategy.py polybot/main_dca.py polybot/momentum_strategy.py polybot/main_momentum.py polybot/meanrev_strategy.py polybot/main_meanrev.py polybot/battle_report.py
python -m pytest -q
```

## Important Paths

- Runtime DB: `polybot/data/paper_trades.db` (ignored)
- Runtime states: `polybot/data/*_state.json` (ignored)
- systemd templates: `systemd/*.example`
- Aktueller Server-Deploy-Pfad: `/root/crypto-trading-bot` (nicht `/root/polyarbi` —
  das war ein alter, ungenutzter Stand und wurde entfernt). Siehe die
  `WorkingDirectory=`-Zeilen in `systemd/*.example`.

## Collaboration

Use feature branches and pull requests for strategy changes. Include:

- what changed
- why it should improve the strategy
- test/verification output
- whether systemd env vars also need changing
