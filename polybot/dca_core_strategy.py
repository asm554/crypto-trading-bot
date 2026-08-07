"""Core-DCA paper pilot -- "Der Kern".

Deliberately different, much lower-turnover strategy than the legacy
``DCABot`` ("Der Brave"): weekly BTC/ETH buys gated by a BTC daily
regime filter, with a hard capital-preservation halt. Ported from the
research prototype in ``backtest/backtest_dca_core.py`` (candidate
``balanced_hardlimit``), which passed the preregistered development
criteria on Bull/Recovery/2024/2025 windows but produced zero trades in
its final 2026 holdout (184 days, confirmed bear regime the whole time) --
see ``backtest/results/bitvavo/DCA_WALKFORWARD_REPORT_2026-08-04.md``.
That means the holdout only confirmed capital protection, not edge. This
bot exists to run the only remaining valid test: a real forward paper
pilot, watched until a bull regime produces actual completed trades.

Rule (unchanged from the backtest, do not re-tune after seeing live results
-- that is exactly the "peeking" the walk-forward report forbids):

1. Only BTC/EUR and ETH/EUR, 50/50 weekly allocation.
2. At most 50 EUR invested per UTC week (Monday), 50 EUR cash reserve.
3. Buy only when the last completed BTC daily close is above EMA200 and
   EMA50 is also above EMA200 ("bull"). Neutral: hold, no buy, no sell.
4. Confirmed bear regime (close and EMA50 both below EMA200): sell every
   open lot and wait for the next bull regime.
5. 10% drawdown from the equity high-water mark: sell everything and stop
   trading for the rest of this bot's lifetime (the "hardlimit" variant --
   halting is permanent, matching exactly what was backtested; there is no
   invented auto-resume, that would be a live behavior never tested).
6. No altcoins, no recovery-style re-buys, no leverage.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import sqlite3
import time
from pathlib import Path

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import PAIR_MAP, extract_quote, fetch_ticker_data
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade
from polybot.surfer_strategy import closed_ohlc_rows, ema_series, fetch_ohlc

logger = logging.getLogger(__name__)
PREFIX, BOT_KEY = "DCACORE_", "dca_core"
WEIGHTS = {"XBTEUR": 0.5, "ETHEUR": 0.5}


def classify_btc_regime(closes: list[float]) -> str:
    """bull/neutral/bear from completed BTC daily closes. Insufficient history -> neutral."""
    ema50 = ema_series(closes, 50)
    ema200 = ema_series(closes, 200)
    if not closes or not ema50 or not ema200:
        return "neutral"
    close, fast, slow = closes[-1], ema50[-1], ema200[-1]
    if close > slow and fast > slow:
        return "bull"
    if close < slow and fast < slow:
        return "bear"
    return "neutral"


class DcaCoreBot:
    def __init__(
        self,
        initial_capital_eur: float = 500.0,
        cash_reserve_eur: float = 50.0,
        weekly_total_eur: float = 50.0,
        circuit_breaker_pct: float = 10.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        if not paper_mode:
            raise NotImplementedError("DcaCoreBot is paper-only")
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.cash_reserve_eur = float(cash_reserve_eur)
        self.weekly_total_eur = float(weekly_total_eur)
        self.circuit_breaker_pct = float(circuit_breaker_pct)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        self.portfolio: dict[str, dict] = {}
        self.last_purchase_week = ""
        self.peak_equity = float(initial_capital_eur)
        self.halted = False
        self.last_snapshot = 0.0
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "dca_core_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self._load()

    def _load(self) -> None:
        try:
            state = json.loads(self.state_path.read_text())
            self.capital_remaining = float(state["capital_remaining"])
            self.portfolio = state.get("portfolio", {})
            self.last_purchase_week = state.get("last_purchase_week", "")
            self.peak_equity = float(state.get("peak_equity", self.initial_capital_eur))
            self.halted = bool(state.get("halted", False))
            self.last_snapshot = float(state.get("last_snapshot", 0))
            state_ids = {
                int(pos.get("trade_id") or 0)
                for pos in self.portfolio.values()
                if int(pos.get("trade_id") or 0) > 0
            }
            if state_ids != paper_db_module.get_open_trade_ids_by_prefix_sync(PREFIX):
                raise ValueError("State und offenes DCACORE-Ledger weichen ab")
        except Exception:
            self._rebuild()
            self._save()

    def _save(self) -> None:
        payload = {
            "capital_remaining": self.capital_remaining,
            "portfolio": self.portfolio,
            "last_purchase_week": self.last_purchase_week,
            "peak_equity": self.peak_equity,
            "halted": self.halted,
            "last_snapshot": self.last_snapshot,
        }
        temp = self.state_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")))
        temp.replace(self.state_path)

    def _rebuild(self) -> None:
        # Best-effort fallback if state is missing/corrupt. peak_equity and
        # halted cannot be recovered from the ledger alone, so they reset --
        # documented limitation, same tradeoff HodlBot's _rebuild makes.
        self.portfolio = {}
        self.last_purchase_week = ""
        self.peak_equity = self.initial_capital_eur
        self.halted = False
        if not self.db_path.exists():
            return
        realized = open_cost = 0.0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\'",
                (paper_db_module.prefix_like_pattern(PREFIX),),
            ).fetchall()
        for row in rows:
            cost = float(row["size"] or 0) * float(row["price"] or 0)
            pair = str(row["market_question"]).removeprefix(PREFIX)
            if row["resolved_at"] is None:
                self.portfolio[str(row["id"])] = {
                    "pair": pair, "shares": float(row["size"]), "cost_basis": cost,
                    "trade_id": int(row["id"]),
                }
                open_cost += cost
            else:
                realized += float(row["real_pnl"] or 0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    async def _btc_regime(self) -> str:
        rows = closed_ohlc_rows(await fetch_ohlc("XBTEUR", 1440), 1440)
        closes = [float(r[4]) for r in rows]
        return classify_btc_regime(closes)

    async def _liquidate_all(self, reason: str) -> list[dict]:
        if not self.portfolio:
            return []
        pairs = sorted({pos["pair"] for pos in self.portfolio.values()})
        ticker = await fetch_ticker_data(pairs)
        fee = config.CRYPTO_TAKER_FEE_RATE
        closed = []
        for key, pos in list(self.portfolio.items()):
            data = ticker.get(PAIR_MAP.get(pos["pair"], pos["pair"])) or ticker.get(pos["pair"])
            if not data:
                continue
            last = float(data["c"][0])
            bid, _ = extract_quote(data, last)
            proceeds = float(pos["shares"]) * bid * (1 - fee)
            pnl = proceeds - float(pos["cost_basis"])
            if not await resolve_trade(int(pos["trade_id"]), bid, round(pnl, 6)):
                continue
            self.capital_remaining += proceeds
            self.portfolio.pop(key)
            closed.append({"pair": pos["pair"], "reason": reason, "pnl": pnl})
        self._save()
        return closed

    async def manage_positions(self) -> list[dict]:
        if self.halted:
            return []
        regime = await self._btc_regime()
        if regime == "bear" and self.portfolio:
            closed = await self._liquidate_all("bear_exit")
            # A later bull re-entry starts a fresh allocation cycle; a stale
            # peak from before this exit could otherwise trip the circuit
            # breaker on day one of the next cycle without any new loss.
            self.peak_equity = self.capital_remaining
            self._save()
            return closed

        equity = await self.equity()
        self.peak_equity = max(self.peak_equity, equity["equity_eur"])
        if (
            self.portfolio
            and equity["equity_eur"] <= self.peak_equity * (1 - self.circuit_breaker_pct / 100)
        ):
            closed = await self._liquidate_all("circuit_breaker")
            self.halted = True
            self._save()
            logger.warning("DCACORE Circuit-Breaker ausgelöst -- Bot dauerhaft angehalten")
            return closed
        self._save()
        return []

    @staticmethod
    def _now() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    async def scan_entries(self) -> list[dict]:
        if self.halted:
            return []
        today = self._now()
        if today.weekday() != 0:
            return []
        week = today.strftime("%G-W%V")
        if self.last_purchase_week == week:
            return []
        regime = await self._btc_regime()
        self.last_purchase_week = week
        if regime != "bull":
            self._save()
            return []
        available = max(0.0, self.capital_remaining - self.cash_reserve_eur)
        round_budget = min(self.weekly_total_eur, available)
        if round_budget < 1.0:
            self._save()
            return []
        ticker = await fetch_ticker_data(list(WEIGHTS))
        fee = config.CRYPTO_TAKER_FEE_RATE
        opened = []
        for pair, weight in WEIGHTS.items():
            cost = min(round_budget * weight, max(0.0, self.capital_remaining - self.cash_reserve_eur))
            if cost < 1.0:
                continue
            data = ticker.get(PAIR_MAP.get(pair, pair)) or ticker.get(pair)
            if not data:
                continue
            last = float(data["c"][0])
            _, ask = extract_quote(data, last)
            shares = cost / (ask * (1 + fee))
            trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", shares, ask * (1 + fee), 0.0, "paper")
            self.portfolio[str(trade_id)] = {
                "pair": pair, "shares": shares, "cost_basis": cost, "trade_id": trade_id,
            }
            self.capital_remaining -= cost
            opened.append({"pair": pair, "amount": cost})
        self._save()
        return opened

    async def equity(self) -> dict:
        pairs = sorted({pos["pair"] for pos in self.portfolio.values()})
        ticker = await fetch_ticker_data(pairs) if pairs else {}
        fee = config.CRYPTO_TAKER_FEE_RATE
        mtm = unrealized = 0.0
        for pos in self.portfolio.values():
            cost = float(pos["cost_basis"])
            data = ticker.get(PAIR_MAP.get(pos["pair"], pos["pair"])) or ticker.get(pos["pair"])
            if not data:
                value = cost
            else:
                bid, _ = extract_quote(data, float(data["c"][0]))
                value = float(pos["shares"]) * bid * (1 - fee)
            mtm += value
            unrealized += value - cost
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": self.capital_remaining + mtm,
            "cash_eur": self.capital_remaining,
            "open_positions": len(self.portfolio),
            "unrealized_pnl_eur": unrealized,
            "realized_pnl_eur": realized,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        if not force and time.time() - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **await self.equity())
        self.last_snapshot = time.time()
        self._save()

    async def run(self) -> None:
        logger.info(
            "Der Kern (Core-DCA) gestartet [PAPER] | Budget %.2f EUR | Halted: %s",
            self.initial_capital_eur, self.halted,
        )
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception:
                logger.exception("DcaCoreBot loop error")
            await asyncio.sleep(300)
