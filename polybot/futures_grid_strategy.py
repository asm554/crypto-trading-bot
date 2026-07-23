"""Leveraged ETH/EUR grid bot for the paper-trading battle.

This deliberately simulates futures accounting while using Kraken's public
ETH/EUR bid/ask as the mark and fill source.  It never places exchange orders.
Margin is isolated to this bot, cannot be topped up, and a margin guard closes
the complete cycle before the simulated liquidation threshold is reached.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import PAIR_MAP, extract_quote, fetch_ticker_data
from polybot.paper_db import (
    DB_PATH,
    get_open_trades_by_prefix,
    log_equity_snapshot,
    log_paper_trade,
    resolve_trade,
)

logger = logging.getLogger(__name__)

BOT_KEY = "futures"
PREFIX = "FUT_"
PAIR = "ETHEUR"
STATE_PATH = Path(DB_PATH).resolve().parent / "futures_state.json"


class FuturesGridBot:
    """Paper-only, long-only futures grid with fixed isolated margin."""

    def __init__(
        self,
        initial_capital_eur: float = 1000.0,
        leverage: float = 2.0,
        order_margin_eur: float = 15.0,
        grid_step_pct: float = 0.8,
        take_profit_pct: float = 1.1,
        max_safety_orders: int = 50,
        maintenance_margin_pct: float = 5.0,
        margin_guard_ratio: float = 1.25,
        taker_fee_rate: float = config.CRYPTO_TAKER_FEE_RATE,
        funding_rate_8h: float = 0.0001,
        scan_interval_sec: int = 30,
        snapshot_interval_sec: int = 3600,
        paper_mode: bool = True,
        state_path: Path = STATE_PATH,
    ):
        if not paper_mode:
            raise NotImplementedError("FuturesGridBot is intentionally paper-only")
        if not 1.0 <= leverage <= 2.0:
            raise ValueError("leverage must be between 1x and 2x")
        if initial_capital_eur <= 0 or order_margin_eur <= 0:
            raise ValueError("capital and order margin must be positive")
        if max_safety_orders < 0 or grid_step_pct <= 0 or take_profit_pct <= 0:
            raise ValueError("invalid grid parameters")
        if margin_guard_ratio <= 1.0:
            raise ValueError("margin guard must stay above liquidation")

        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.leverage = float(leverage)
        self.order_margin_eur = float(order_margin_eur)
        self.grid_step_pct = float(grid_step_pct)
        self.take_profit_pct = float(take_profit_pct)
        self.max_safety_orders = int(max_safety_orders)
        self.maintenance_margin_rate = float(maintenance_margin_pct) / 100
        self.margin_guard_ratio = float(margin_guard_ratio)
        self.taker_fee_rate = float(taker_fee_rate)
        self.funding_rate_8h = float(funding_rate_8h)
        self.scan_interval_sec = max(5, int(scan_interval_sec))
        self.snapshot_interval_sec = max(60, int(snapshot_interval_sec))
        self.state_path = Path(state_path)
        self.orders: list[dict] = []
        self.cycle = 0
        self.realized_funding_eur = 0.0
        self.last_funding_ts = time.time()
        self.last_snapshot = 0.0
        self._load_state()

    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text())
        except Exception:
            return
        self.capital_remaining = float(raw.get("capital_remaining", self.initial_capital_eur))
        self.orders = list(raw.get("orders") or [])
        self.cycle = int(raw.get("cycle", 0))
        self.realized_funding_eur = float(raw.get("realized_funding_eur", 0.0))
        self.last_funding_ts = float(raw.get("last_funding_ts", time.time()))
        self.last_snapshot = float(raw.get("last_snapshot", 0.0))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital_remaining": self.capital_remaining,
            "orders": self.orders,
            "cycle": self.cycle,
            "realized_funding_eur": self.realized_funding_eur,
            "last_funding_ts": self.last_funding_ts,
            "last_snapshot": self.last_snapshot,
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(self.state_path)

    @staticmethod
    def _quote(ticker: dict) -> tuple[float, float, float] | None:
        data = ticker.get(PAIR_MAP.get(PAIR, PAIR)) or ticker.get(PAIR)
        if not data:
            return None
        try:
            last = float(data["c"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        bid, ask = extract_quote(data, last)
        return last, bid, ask

    @property
    def total_shares(self) -> float:
        return sum(float(o["shares"]) for o in self.orders)

    @property
    def total_notional(self) -> float:
        return sum(float(o["notional_eur"]) for o in self.orders)

    @property
    def reserved_margin(self) -> float:
        return sum(float(o["margin_eur"]) for o in self.orders)

    @property
    def average_entry(self) -> float:
        shares = self.total_shares
        return sum(float(o["shares"]) * float(o["entry_price"]) for o in self.orders) / shares if shares else 0.0

    def unrealized_pnl(self, mark_price: float) -> float:
        return sum(float(o["shares"]) * (mark_price - float(o["entry_price"])) for o in self.orders)

    def margin_ratio(self, mark_price: float) -> float:
        maintenance = self.total_notional * self.maintenance_margin_rate
        if maintenance <= 0:
            return float("inf")
        return (self.reserved_margin + self.unrealized_pnl(mark_price)) / maintenance

    def liquidation_price(self) -> float | None:
        shares = self.total_shares
        if shares <= 0:
            return None
        # reserved margin + shares * (liq - average entry) = maintenance margin
        return self.average_entry + (
            self.total_notional * self.maintenance_margin_rate - self.reserved_margin
        ) / shares

    def _accrue_funding(self, now: float) -> float:
        elapsed = max(0.0, now - self.last_funding_ts)
        self.last_funding_ts = now
        if not self.orders or elapsed <= 0:
            return 0.0
        charge = self.total_notional * self.funding_rate_8h * (elapsed / (8 * 3600))
        charge = min(charge, self.capital_remaining)
        self.capital_remaining -= charge
        self.realized_funding_eur += charge
        return charge

    async def _open_order(self, ask: float, grid_anchor: float) -> dict | None:
        margin = min(self.order_margin_eur, self.capital_remaining)
        notional = margin * self.leverage
        entry_fee = notional * self.taker_fee_rate
        if margin + entry_fee > self.capital_remaining or margin < 1.0:
            return None
        shares = notional / ask
        level = len(self.orders)
        trade_id = await log_paper_trade(
            f"{PREFIX}{PAIR}_C{self.cycle}_L{level}",
            "long",
            shares,
            ask,
            self.grid_step_pct / 100,
            "paper_futures",
        )
        self.capital_remaining -= margin + entry_fee
        order = {
            "trade_id": trade_id,
            "shares": shares,
            "entry_price": ask,
            # Grid anchor remains the planned level even if a fast market gaps
            # through several levels. This lets the loop catch every crossed
            # rung instead of silently re-anchoring the grid at the low.
            "trigger_price": grid_anchor,
            "margin_eur": margin,
            "notional_eur": notional,
            "entry_fee_eur": entry_fee,
            "taker_fee_rate": self.taker_fee_rate,
            "opened_at": time.time(),
        }
        self.orders.append(order)
        logger.info(
            "🪜 FUT Level %d: %.2f€ Margin / %.2f€ Notional @ %.2f€",
            level, margin, notional, ask,
        )
        return order

    async def _close_cycle(self, bid: float, reason: str) -> list[dict]:
        closed = []
        for order in self.orders:
            shares = float(order["shares"])
            gross_pnl = shares * (bid - float(order["entry_price"]))
            exit_fee = shares * bid * self.taker_fee_rate
            net_after_entry = gross_pnl - float(order["entry_fee_eur"]) - exit_fee
            # Entry fee was already taken from cash, so only margin + gross - exit fee returns.
            self.capital_remaining += float(order["margin_eur"]) + gross_pnl - exit_fee
            await resolve_trade(int(order["trade_id"]), bid, round(net_after_entry, 6))
            closed.append({"trade_id": order["trade_id"], "pnl": net_after_entry})
        logger.info(
            "🏁 FUT cycle %d closed (%s) @ %.2f€ | orders=%d | net=%+.2f€",
            self.cycle, reason, bid, len(closed), sum(x["pnl"] for x in closed),
        )
        self.orders = []
        self.cycle += 1
        return closed

    async def step(self, ticker: dict | None = None, now: float | None = None) -> dict:
        now = float(now or time.time())
        ticker = ticker if ticker is not None else await fetch_ticker_data([PAIR])
        quote = self._quote(ticker)
        if quote is None:
            return {"action": "no_price"}
        last, bid, ask = quote
        self._accrue_funding(now)

        if not self.orders:
            opened = await self._open_order(ask, last)
            self._save_state()
            return {"action": "open" if opened else "no_cash"}

        ratio = self.margin_ratio(bid)
        if ratio <= self.margin_guard_ratio:
            closed = await self._close_cycle(bid, "margin_guard")
            self._save_state()
            return {"action": "margin_guard", "closed": closed}

        target = self.average_entry * (1 + self.take_profit_pct / 100)
        if bid >= target:
            closed = await self._close_cycle(bid, "take_profit")
            self._save_state()
            return {"action": "take_profit", "closed": closed}

        opened_count = 0
        while len(self.orders) - 1 < self.max_safety_orders:
            next_trigger = float(self.orders[-1]["trigger_price"]) * (1 - self.grid_step_pct / 100)
            if last > next_trigger:
                break
            opened = await self._open_order(ask, next_trigger)
            if not opened:
                break
            opened_count += 1
        if opened_count:
            self._save_state()
            return {"action": "safety_order", "opened": opened_count}

        self._save_state()
        return {"action": "hold", "margin_ratio": ratio, "liquidation_price": self.liquidation_price()}

    async def equity(self, ticker: dict | None = None) -> dict:
        ticker = ticker if ticker is not None else await fetch_ticker_data([PAIR])
        quote = self._quote(ticker)
        bid = quote[1] if quote else self.average_entry
        unrealized = self.unrealized_pnl(bid) if self.orders else 0.0
        exit_fee = self.total_shares * bid * self.taker_fee_rate if self.orders else 0.0
        equity = self.capital_remaining + self.reserved_margin + unrealized - exit_fee
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": equity,
            "cash_eur": self.capital_remaining,
            "open_positions": len(self.orders),
            "unrealized_pnl_eur": unrealized - exit_fee,
            "realized_pnl_eur": realized - self.realized_funding_eur,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **(await self.equity()))
        self.last_snapshot = now
        self._save_state()

    async def reconcile_open_trades(self) -> None:
        """Refuse to invent state if DB and state file disagree after restart."""
        rows = await get_open_trades_by_prefix(PREFIX)
        state_ids = {int(o["trade_id"]) for o in self.orders}
        db_ids = {int(r["id"]) for r in rows}
        if state_ids != db_ids:
            raise RuntimeError(f"FUT state/DB mismatch: state={state_ids}, db={db_ids}")

    async def run(self) -> None:
        await self.reconcile_open_trades()
        logger.info(
            "🚀 Futures Grid [PAPER] | capital=%.2f€ leverage=%.1fx grid=%.2f%%",
            self.initial_capital_eur, self.leverage, self.grid_step_pct,
        )
        while True:
            try:
                await self.step()
                await self.maybe_snapshot()
            except Exception:
                logger.exception("Futures-grid loop failed")
            await asyncio.sleep(self.scan_interval_sec)
