# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

This repo contains the standalone Crypto/Paper-Trading bot package exported from `/root/polyarbi`.

Six paper-trading bots run in parallel and are compared against each other ("battle"):

- `polybot.main_dca` — Conservative Recovery DCA / "Der Brave"
- `polybot.main_momentum` — Momentum + Trailing Stop / "Der Zocker"
- `polybot.main_meanrev` — Mean-Reversion + RSI confirmation / "Der Contrarian"
- `polybot.main_arb` — Triangular Arbitrage (EUR→BTC→ETH→EUR on Kraken) / "Der Pedant"
- `polybot.main_daytrade` — Short-window intraday momentum (4h lookback, ~6h max hold) / "Der Zappler"
- `polybot.main_memecoin` — On-chain Solana memecoin breakout (DexScreener, no wallet/key) / "Der Onchain"
- `polybot.battle_report` — Telegram/console equity comparison report across all six

A Next.js/shadcn dashboard under `dashboard/` reads the same data from Supabase (see Architecture below) and is the primary way the group actually watches the battle; `dashboard/` has its own `AGENTS.md` warning that the installed Next.js version has breaking changes vs. training data — read `node_modules/next/dist/docs/` there before touching framework-level code.

Legacy experiments from the original server tree live under `legacy/`. Do not modify or revive them unless the task explicitly asks for it. `polybot/bot_overview.py` also references a sibling `power_trader_kraken/` directory that does not exist in this repo — that code path is not functional standalone and should be treated with the same caution as `legacy/`.

## Safety Rules

- Default is paper trading only. Every bot's `__init__` hard-raises `NotImplementedError` if constructed with `paper_mode=False` — this is intentional and must not be "fixed" or worked around.
- Do not add real exchange order execution without explicit review.
- Never commit `.env`, API keys, Telegram tokens, SQLite DBs, logs, state files, or backups.
- Compare strategy performance by net equity, not realized PnL alone (see KPI note below).
- If changing strategy parameters for systemd deployment, update the relevant `Environment=` lines too — code defaults and the live systemd units can silently diverge otherwise.

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
python -m polybot.main_arb
python -m polybot.main_daytrade
python -m polybot.main_memecoin
python -m polybot.battle_report
python -m polybot.main_cloud_sync   # mirrors the local DB to Supabase for the dashboard
```

Dashboard (separate Node project):

```bash
cd dashboard
npm install
npm run dev     # local dev server
npm run build   # production build; run before deploying
npm run lint
npx tsc --noEmit
```

## Verify

```bash
python -m py_compile polybot/dca_strategy.py polybot/main_dca.py polybot/momentum_strategy.py polybot/main_momentum.py polybot/meanrev_strategy.py polybot/main_meanrev.py polybot/arb_strategy.py polybot/main_arb.py polybot/daytrade_strategy.py polybot/main_daytrade.py polybot/memecoin_strategy.py polybot/main_memecoin.py polybot/battle_report.py
python -m pytest -q
```

Run a single test file/case: `python -m pytest tests/test_arb_strategy.py -q` or `python -m pytest tests/test_dca_strategy.py::test_execute_dca_round_fills_at_ask_not_last -q`.

## Architecture

**`polybot/dca_strategy.py` doubles as the shared Kraken utility module**, despite its name — `fetch_ticker_data()`, `extract_quote()` (bid/ask parsing with last-price fallback), `rolling_change_pct()`/`rolling_24h_change_pct()` (cached rolling OHLC change, keyed by `(pair, lookback_bars, interval_min)`), `CANDIDATE_PAIRS`, and `PAIR_MAP` are all imported from here by `momentum_strategy.py`, `meanrev_strategy.py`, and `daytrade_strategy.py`. `rolling_24h_change_pct` is a thin backwards-compatible wrapper around `rolling_change_pct(lookback_bars=24, interval_min=60)` — don't change its signature, other bots and tests depend on it staying stable.

**Fills use real bid/ask, not the last price**: buys fill at ask, sells fill at bid, via `extract_quote()`. Entry/exit *decisions* (change-%, trailing peak, RSI, stop triggers) still evaluate against the last price — only the simulated fill price uses the spread. Mark-to-market valuation (`equity()`, `update_paper_pnl()`, battle report) values open positions at bid for the same reason: an exit would realize at bid, so unrealized PnL should not look better than what closing would actually produce.

**`polybot/paper_db.py` is a generic, prefix-keyed trade ledger** shared by all bots: `log_paper_trade()`, `resolve_trade()`, `get_open_trades_by_prefix()`, `get_realized_pnl_by_prefix()`, `log_equity_snapshot()`. Each bot's trades are tagged by a `market_question` prefix (`DCA_`, `MOM_`, `REV_`, `ARB_`, `DAY_`, `CHAIN_`) — new bots need a new prefix, not schema changes. (`paper_db.py` also still defines a `smart_money_positions` table and `save_sm_position`/`load_sm_positions`/`get_smart_money_trades_sync` — these are vestigial, called only from dead `legacy/` code, not from any active bot.)

**"Der Pedant" (arb_strategy.py) breaks the usual open-position pattern.** DCA/Momentum/MeanRev/Daytrade all hold a `portfolio` dict of open positions, checked each loop via `manage_positions()` and closed later on a trigger. A triangular arb cycle is atomic — three fills happen and resolve within a single `scan_entries()` call, so `manage_positions()` is a no-op stub there by design, and each completed cycle is logged as one already-resolved trade (not three legs) so `get_open_trades_by_prefix("ARB_")` stays empty.

**"Der Onchain" (memecoin_strategy.py) is the only bot not on Kraken.** It prices Solana memecoins (BONK, WIF, POPCAT, PNUT, GOAT, MEW) via the public DexScreener REST API (`fetch_meme_pairs()`, no wallet/key needed for paper mode) instead of `dca_strategy.py`'s Kraken helpers, and converts USD→EUR itself via a Kraken `EURUSD` ticker lookup. Two consequences ripple outward: (1) there's no order book, so fills simulate an AMM slippage/price-impact haircut (`slippage_pct`) instead of a real bid/ask spread; (2) DexScreener has no public OHLC candle API, so the momentum entry signal is computed from the bot's own rolling price history (`price_history` in its state file, pruned to `momentum_lookback_min`) rather than exchange candles — a fresh bot needs `momentum_lookback_min/2` of runtime before it has enough samples to ever enter a trade. Entry is a momentum band (`entry_change_pct`..`entry_max_change_pct`, default 8–60 % over the lookback window), not a breakout over a historical high — the goal is catching a coin early in a strong move, with the upper bound guarding against buying into an already-exhausted pump; candidates are ranked by `change_pct * log10(liquidity)` like the Kraken momentum bot. Because `battle_report.equity_for()` assumes Kraken pairs, `battle_report.py` has a separate `equity_for_memecoin()` that goes through `fetch_meme_pairs()` instead — if you add data-flow logic to `equity_for()`, check whether it needs mirroring there too.

**Data flow to the dashboard**: bot → local SQLite (`paper_trades.db`) → `polybot.main_cloud_sync` (polls and upserts to Supabase, independent process, failures there never affect trading) → `dashboard/src/lib/bots.ts` reads from Supabase via REST → rendered on the overview/trades/settings pages. The dashboard has no direct access to the bots or the local DB — if new data isn't showing up, check cloud-sync's logs before the dashboard code.

**Actual server deployment path is `/root/crypto-trading-bot`**, not `/root/polyarbi` (an old, incomplete May snapshot that was deleted) — see the `WorkingDirectory=`/`ExecStart=` lines in `systemd/*.example`. This is a live discrepancy trap: if you add a new bot, its systemd template must point here, not at `/root/polyarbi`.

## Important Paths

- Runtime DB: `polybot/data/paper_trades.db` (ignored)
- Runtime states: `polybot/data/*_state.json` (ignored)
- systemd templates: `systemd/*.example`
- Battle metadata (42-day window tracking): `polybot/data/battle_meta.json` (ignored) — resetting this restarts the "Tag N/42" counter in `battle_report.py`; don't reset it on a server that already has real trading history.

## Collaboration

Use feature branches and pull requests for strategy changes. Include:

- what changed
- why it should improve the strategy
- test/verification output
- whether systemd env vars also need changing
