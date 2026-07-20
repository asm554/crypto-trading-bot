# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

This repo contains the standalone Crypto/Paper-Trading bot package exported from `/root/polyarbi`.

Seven paper-trading bots run in parallel and are compared against each other ("battle"):

- `polybot.main_dca` — Conservative Recovery DCA / "Der Brave"
- `polybot.main_momentum` — Momentum + Trailing Stop / "Der Zocker"
- `polybot.main_meanrev` — Mean-Reversion + RSI confirmation / "Der Contrarian"
- `polybot.main_arb` — Triangular Arbitrage (EUR→BTC→ETH→EUR on Kraken) / "Der Pedant"
- `polybot.main_daytrade` — Short-window intraday momentum (4h lookback, ~6h max hold) / "Der Zappler"
- `polybot.main_memecoin` — On-chain Solana memecoin breakout (DexScreener, no wallet/key) / "Der Onchain"
- `polybot.main_surfer` — SOL/EUR trend/breakout: 4h uptrend + EMA20>EMA50 + 20h breakout + volume, ATR-sized, single position / "Der Surfer"
- `polybot.battle_report` — Telegram/console equity comparison report across all seven

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
python -m polybot.main_surfer
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
python -m py_compile polybot/dca_strategy.py polybot/main_dca.py polybot/momentum_strategy.py polybot/main_momentum.py polybot/meanrev_strategy.py polybot/main_meanrev.py polybot/arb_strategy.py polybot/main_arb.py polybot/daytrade_strategy.py polybot/main_daytrade.py polybot/memecoin_strategy.py polybot/main_memecoin.py polybot/surfer_strategy.py polybot/main_surfer.py polybot/battle_report.py
python -m pytest -q
```

Run a single test file/case: `python -m pytest tests/test_arb_strategy.py -q` or `python -m pytest tests/test_dca_strategy.py::test_execute_dca_round_fills_at_ask_not_last -q`.

## Architecture

**`polybot/dca_strategy.py` doubles as the shared Kraken utility module**, despite its name — `fetch_ticker_data()`, `extract_quote()` (bid/ask parsing with last-price fallback), `rolling_change_pct()`/`rolling_24h_change_pct()` (cached rolling OHLC change, keyed by `(pair, lookback_bars, interval_min)`), `CANDIDATE_PAIRS`, and `PAIR_MAP` are all imported from here by `momentum_strategy.py`, `meanrev_strategy.py`, `daytrade_strategy.py`, and (minus `CANDIDATE_PAIRS`, since it only ever trades `SOLEUR`) `surfer_strategy.py`. `rolling_24h_change_pct` is a thin backwards-compatible wrapper around `rolling_change_pct(lookback_bars=24, interval_min=60)` — don't change its signature, other bots and tests depend on it staying stable.

**Fills use real bid/ask, not the last price**: buys fill at ask, sells fill at bid, via `extract_quote()`. Entry/exit *decisions* (change-%, trailing peak, RSI, stop triggers) still evaluate against the last price — only the simulated fill price uses the spread. Mark-to-market valuation (`equity()`, `update_paper_pnl()`, battle report) values open positions at bid for the same reason: an exit would realize at bid, so unrealized PnL should not look better than what closing would actually produce.

**`polybot/paper_db.py` is a generic, prefix-keyed trade ledger** shared by all bots: `log_paper_trade()`, `resolve_trade()`, `get_open_trades_by_prefix()`, `get_realized_pnl_by_prefix()`, `log_equity_snapshot()`. Each bot's trades are tagged by a `market_question` prefix (`DCA_`, `MOM_`, `REV_`, `ARB_`, `DAY_`, `CHAIN_`, `SURF_`) — new bots need a new prefix, not schema changes. (`paper_db.py` also still defines a `smart_money_positions` table and `save_sm_position`/`load_sm_positions`/`get_smart_money_trades_sync` — these are vestigial, called only from dead `legacy/` code, not from any active bot.)

**"Der Pedant" (arb_strategy.py) breaks the usual open-position pattern.** DCA/Momentum/MeanRev/Daytrade all hold a `portfolio` dict of open positions, checked each loop via `manage_positions()` and closed later on a trigger. A triangular arb cycle is atomic — three fills happen and resolve within a single `scan_entries()` call, so `manage_positions()` is a no-op stub there by design, and each completed cycle is logged as one already-resolved trade (not three legs) so `get_open_trades_by_prefix("ARB_")` stays empty.

**"Der Onchain" (memecoin_strategy.py) is the only bot not on Kraken.** It prices Solana memecoins via the public DexScreener REST API (`fetch_pairs_by_address()`, no wallet/key needed for paper mode) instead of `dca_strategy.py`'s Kraken helpers, and converts USD→EUR itself via a Kraken `EURUSD` ticker lookup. Its universe is hybrid: a curated core of ~12 individually live-verified mint addresses (`SYMBOL_TO_MINT`, with its own looser gates `min_liquidity_usd`/`min_volume_usd`) plus, if `dynamic_enabled`, tokens discovered from DexScreener's public boost/profile feeds (`discover_dynamic_solana_tokens()`) — those feeds are paid promotion, not an organic signal, so a discovery failure returns `[]` and never blocks the curated core, and dynamic candidates get their own stricter gates (`min_liquidity_dynamic_usd`, `min_volume_dynamic_usd`, `min_pair_age_hours`, and a concurrent-position cap `max_dynamic_positions`) that the curated core is exempt from — a first live day showed dynamic tokens dominating trading entirely (5 of 6 trades) when both universes shared one gate set. **Keying is by mint address, not ticker**: Solana is permissionless, so two tokens (curated or dynamic) can share a symbol — a live probe during development found a homoglyph clone of "ai16z" (Greek/Cyrillic look-alike characters) with far more liquidity than the real token, which is exactly the failure mode address-pinning avoids. `portfolio`/`cooldowns` are keyed by address; `market_question` encodes both as `CHAIN_{symbol}@{address}` (symbol for the dashboard, address for price resolution) — parse with `.partition("@")`, never assume no `@` in the remainder. Entry is a momentum band on DexScreener's native `priceChange.h1` (`entry_change_pct`..`entry_max_change_pct`, default 8–35 %) plus freshness gates (`priceChange.m5` must still be positive, `priceChange.h6` capped by `max_h6_change_pct` to avoid buying a day-long blowoff), liquidity, 24h-volume, and an h1 buy/sell-ratio gate that also requires a minimum transaction count (`min_h1_txns`) so it can't be satisfied by a handful of trades; candidates are ranked by `priceChange.h1 * log10(volume_h1)`. Exit is a hybrid floor-plus-trailing-stop: reaching `take_profit_pct` doesn't sell, it switches the position into trailing mode (`peak_price`, same pattern as `momentum_strategy.py`), exiting at the higher of a fixed floor above entry (`trail_floor_pct`) or the peak minus `trailing_stop_pct` — a hard `stop_loss_pct` and `max_hold_sec` stay active as safety nets independent of trailing mode. A stop-loss exit gets a longer cooldown (`cooldown_after_stop_sec`, default 24h) than a take-profit/trailing/time exit (`cooldown_sec`, default 4h) to curb revenge-trading the same token. There's no order book, so fills simulate an AMM slippage/price-impact haircut (`slippage_pct`) plus, separately, a mechanical DEX/bonding-curve swap fee (`dex_fee_pct`, default ~1% like pump.fun pre-Raydium-migration) instead of a real bid/ask spread — both compound independently on buy and sell. Because `battle_report.equity_for()` assumes Kraken pairs, `battle_report.py` has a separate `equity_for_memecoin()` that parses the address out of `market_question` and calls `fetch_pairs_by_address()` instead — it re-applies `DEFAULT_SLIPPAGE_PCT`/`DEFAULT_DEX_FEE_PCT` itself since it doesn't go through the bot instance, so if you add data-flow logic to `equity_for()` or change either default, check whether it needs mirroring there too.

**"Der Surfer" (surfer_strategy.py) trades a single fixed pair (`SOLEUR`) instead of scanning `CANDIDATE_PAIRS`**, and holds at most one open position at a time. Entry requires four conditions simultaneously: a confirmed 4h uptrend (`rolling_change_pct(pair, lookback_bars=trend_lookback_hours, interval_min=60)` > `min_trend_pct`, same cached helper `daytrade_strategy.py` uses for its short window), EMA20 above EMA50 on hourly closes (`ema_series()`), a breakout above the prior `breakout_lookback_hours`-hour high, and current-bar volume above `volume_multiplier` × the trailing average — all computed from a locally fetched OHLC series (`fetch_ohlc()`, a duplicate of `meanrev_strategy.py`'s own OHLC fetcher rather than a shared import, matching that file's existing precedent of not centralizing OHLC access in `dca_strategy.py`). Position size is risk-based, not a fixed EUR amount like the other bots: it's derived from a Wilder ATR stop (`atr_wilder()`, `atr_stop_multiplier` × ATR14 below entry) so that `max_risk_eur` (default 0.50€) is the worst-case loss, capped by `max_position_eur`; if even the minimum viable position size would risk more than that, no trade is opened. Exit uses the higher (tighter) of the static ATR stop or a trailing stop off the peak — the same "higher of two stops" pattern `equity_for_memecoin`'s hybrid uses — plus an EMA20-crosses-below-EMA50 trend exit and a 7-day time exit. After `loss_streak_limit` (default 3) consecutive losing trades, entries pause for `loss_pause_sec` (default 24h); separately, if account equity drops `account_loss_limit_pct` (default 10%) below `initial_capital_eur`, new entries stop entirely regardless of streak (existing positions still get managed/closed normally). Because it trades a plain Kraken EUR pair, it needs no `equity_for_memecoin()`-style special case in `battle_report.py` — it goes through the standard `equity_for()` path like DCA/Momentum/MeanRev/Daytrade.

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
