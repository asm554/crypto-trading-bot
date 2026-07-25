"""Pump.fun early-curve + migration paper trader.

Paper-only: the module consumes PumpPortal events but never creates, signs, or
submits Solana transactions. Early fills use the event's virtual bonding-curve
reserves; migrated fills use market-cap movement plus configured impact/fees.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import sqlite3
import time
from collections import deque
from pathlib import Path

import websockets

from polybot import paper_db as paper_db_module
from polybot.dca_strategy import fetch_ticker_data
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "PUMP_"
BOT_KEY = "pumpfun"
WS_URL = "wss://pumpportal.fun/api/data"
SOL_EUR_PAIR = "SOLEUR"
FALLBACK_SOL_EUR = 150.0
PHASE_EARLY = "early"
PHASE_MIGRATED = "migrated"
PUMPPORTAL_TRADES_PER_FEE = 10_000
PUMPPORTAL_DATA_FEE_SOL = 0.01

# Offizielle PumpSwap-Gesamtgebühr für kanonische SOL-Pools, Stand 21.05.2026.
# Jede Grenze ist exklusiv; ab 98.240 SOL gilt der letzte Satz von 0,30 %.
PUMPSWAP_SOL_FEE_SCHEDULE = (
    (420, 1.250), (1_470, 1.200), (2_460, 1.150), (3_440, 1.100),
    (4_420, 1.050), (9_820, 1.000), (14_740, 0.950), (19_650, 0.900),
    (24_560, 0.850), (29_470, 0.800), (34_380, 0.750), (39_300, 0.700),
    (44_210, 0.650), (49_120, 0.600), (54_030, 0.550), (58_940, 0.525),
    (63_860, 0.500), (68_770, 0.475), (73_681, 0.450), (78_590, 0.425),
    (83_500, 0.400), (88_400, 0.375), (93_330, 0.350), (98_240, 0.325),
)


def pumpswap_fee_pct(market_cap_sol: float) -> float:
    """Aktuelle gesamte PumpSwap-Handelsgebühr für kanonische SOL-Pools."""
    market_cap = max(0.0, float(market_cap_sol))
    for upper_bound, fee_pct in PUMPSWAP_SOL_FEE_SCHEDULE:
        if market_cap < upper_bound:
            return fee_pct
    return 0.300


class PumpFunPaperBot:
    def __init__(self, initial_capital_eur=100.0, position_eur=5.0,
                 max_open_positions=2, max_candidates=500,
                 min_age_sec=90, max_age_sec=6 * 3600,
                 min_market_cap_sol=20.0, max_market_cap_sol=350.0,
                 migrated_max_market_cap_sol=2000.0, min_change_pct=10.0,
                 max_change_pct=35.0, migrated_min_change_pct=6.0,
                 migrated_max_change_pct=50.0, min_trades=20,
                 migrated_min_trades=10, min_unique_traders=8,
                 min_buy_sell_ratio=1.4, min_recent_change_pct=2.0,
                 stop_loss_pct=20.0, take_profit_pct=30.0,
                 trailing_stop_pct=15.0, trail_floor_pct=15.0,
                 max_hold_sec=45 * 60, migrated_max_hold_sec=6 * 3600,
                 platform_fee_pct=1.25, migrated_slippage_pct=2.5,
                 paper_mode=True, snapshot_interval_sec=3600,
                 prefix=PREFIX, bot_key=BOT_KEY,
                 state_filename="pumpfun_state.json",
                 strategy_version="v2-curve-migration",
                 api_key=None, metered_data_enabled=False,
                 data_fee_sol_per_10k=PUMPPORTAL_DATA_FEE_SOL):
        if not paper_mode:
            raise NotImplementedError("PumpFunPaperBot is paper-only")
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.position_eur = float(position_eur)
        self.max_open_positions = int(max_open_positions)
        self.max_candidates = int(max_candidates)
        self.min_age_sec = int(min_age_sec)
        self.max_age_sec = int(max_age_sec)
        self.min_market_cap_sol = float(min_market_cap_sol)
        self.max_market_cap_sol = float(max_market_cap_sol)
        self.migrated_max_market_cap_sol = float(migrated_max_market_cap_sol)
        self.min_change_pct = float(min_change_pct)
        self.max_change_pct = float(max_change_pct)
        self.migrated_min_change_pct = float(migrated_min_change_pct)
        self.migrated_max_change_pct = float(migrated_max_change_pct)
        self.min_trades = int(min_trades)
        self.migrated_min_trades = int(migrated_min_trades)
        self.min_unique_traders = int(min_unique_traders)
        self.min_buy_sell_ratio = float(min_buy_sell_ratio)
        self.min_recent_change_pct = float(min_recent_change_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)
        self.trailing_stop_pct = float(trailing_stop_pct)
        self.trail_floor_pct = float(trail_floor_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.migrated_max_hold_sec = int(migrated_max_hold_sec)
        self.platform_fee_pct = float(platform_fee_pct)
        self.migrated_slippage_pct = float(migrated_slippage_pct)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        self.prefix = str(prefix)
        self.bot_key = str(bot_key)
        self.strategy_version = str(strategy_version)
        self.api_key = str(api_key or os.getenv("PUMPPORTAL_API_KEY", "")).strip()
        self.metered_data_enabled = bool(metered_data_enabled)
        self.data_fee_sol_per_10k = max(0.0, float(data_fee_sol_per_10k))
        self.state_path = Path(paper_db_module.DB_PATH).resolve().parent / state_filename
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio = {}
        self.candidates = {}
        self.cooldowns = {}
        self.subscribed = set()
        self.pending_unsubscribes = set()
        self.trade_count = 0
        self.last_snapshot = 0.0
        self.metered_trade_events = 0
        self.metered_batches_billed = 0
        self.data_fees_eur = 0.0
        self._sol_eur_cache = FALLBACK_SOL_EUR
        self._load_state()

    def _load_state(self):
        if not self.state_path.exists():
            self._rebuild_state_from_db()
            self._save_state()
            return
        try:
            raw = json.loads(self.state_path.read_text())
            self.capital_remaining = float(raw.get("capital_remaining", self.initial_capital_eur))
            self.portfolio = raw.get("portfolio") or {}
            self.cooldowns = {k: float(v) for k, v in (raw.get("cooldowns") or {}).items() if float(v) > time.time()}
            self.trade_count = int(raw.get("trade_count", 0))
            self.last_snapshot = float(raw.get("last_snapshot", 0.0))
            self.metered_trade_events = int(raw.get("metered_trade_events", 0))
            self.metered_batches_billed = int(raw.get("metered_batches_billed", 0))
            self.data_fees_eur = float(raw.get("data_fees_eur", 0.0))
            state_ids = {
                int(pos.get("trade_id") or 0)
                for pos in self.portfolio.values()
                if int(pos.get("trade_id") or 0) > 0
            }
            if state_ids != paper_db_module.get_open_trade_ids_by_prefix_sync(self.prefix):
                raise ValueError("State und offenes PUMP-Ledger weichen ab")
            ledger_batches, ledger_fees = self._ledger_data_fee_totals()
            if self.metered_batches_billed != ledger_batches or abs(self.data_fees_eur - ledger_fees) > 1e-6:
                raise ValueError("State und PumpPortal-Gebührenledger weichen ab")
        except Exception as exc:
            logger.warning("PUMP state konnte nicht geladen werden (%s) – rebuild aus DB", exc)
            self._rebuild_state_from_db()
            self._save_state()

    def _ledger_data_fee_totals(self) -> tuple[int, float]:
        if not self.db_path.exists():
            return 0, 0.0
        with sqlite3.connect(self.db_path, timeout=30.0) as connection:
            rows = connection.execute(
                "SELECT market_question, real_pnl FROM paper_trades "
                "WHERE market_question LIKE ? ESCAPE '\\' AND resolved_at IS NOT NULL",
                (paper_db_module.prefix_like_pattern(f"{self.prefix}DATA_API_"),),
            ).fetchall()
        highest_batch = 0
        total_eur = 0.0
        for market, pnl in rows:
            try:
                highest_batch = max(
                    highest_batch,
                    int(str(market).removeprefix(f"{self.prefix}DATA_API_")),
                )
            except ValueError:
                continue
            total_eur += max(0.0, -float(pnl or 0.0))
        return highest_batch, total_eur

    def _rebuild_state_from_db(self):
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.trade_count = 0
        self.metered_trade_events = 0
        self.metered_batches_billed = 0
        self.data_fees_eur = 0.0
        if not self.db_path.exists():
            return
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\' ORDER BY id",
                (paper_db_module.prefix_like_pattern(self.prefix),),
            ).fetchall()
        realized = open_cost = 0.0
        for row in rows:
            rest = str(row["market_question"]).removeprefix(self.prefix)
            if rest.startswith("DATA_API_"):
                if row["resolved_at"] is not None:
                    pnl = float(row["real_pnl"] or 0.0)
                    realized += pnl
                    self.data_fees_eur += max(0.0, -pnl)
                    try:
                        batch = int(rest.removeprefix("DATA_API_"))
                        self.metered_batches_billed = max(self.metered_batches_billed, batch)
                    except ValueError:
                        pass
                continue
            symbol, separator, mint = rest.partition("@")
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            cost = size * price
            if not separator or size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += cost
                self.portfolio[mint] = {
                    "symbol": symbol,
                    "shares": size,
                    "cost_basis": cost,
                    "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    "entry_mcap": 1.0,
                    "entry_fee_pct": pumpswap_fee_pct(1.0),
                    "entry_value_factor": 1 - pumpswap_fee_pct(1.0) / 100,
                    "peak_value": cost,
                    "mark_value": cost,
                    "phase": PHASE_MIGRATED,
                    "trailing_active": False,
                    "needs_recovery_exit": True,
                    "trade_id": int(row["id"]),
                }
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.metered_trade_events = self.metered_batches_billed * PUMPPORTAL_TRADES_PER_FEE
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    def _save_state(self):
        payload = {"capital_remaining": round(self.capital_remaining, 8),
                   "portfolio": self.portfolio, "cooldowns": self.cooldowns,
                   "trade_count": self.trade_count, "updated_at": time.time(),
                   "last_snapshot": self.last_snapshot,
                   "metered_trade_events": self.metered_trade_events,
                   "metered_batches_billed": self.metered_batches_billed,
                   "data_fees_eur": round(self.data_fees_eur, 8),
                   "paper_only": True, "strategy_version": self.strategy_version}
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(self.state_path)

    async def _get_sol_eur(self):
        try:
            data = await fetch_ticker_data([SOL_EUR_PAIR])
            quote = data.get(SOL_EUR_PAIR) or {}
            value = float((quote.get("c") or [0])[0])
            if value > 0: self._sol_eur_cache = value
        except Exception:
            pass
        return self._sol_eur_cache

    def _trade_fee_pct(self, phase: str, market_cap_sol: float) -> float:
        if phase == PHASE_MIGRATED:
            return pumpswap_fee_pct(market_cap_sol)
        return self.platform_fee_pct

    async def _charge_metered_data_if_due(self, event: dict) -> None:
        """Bucht PumpPortal-Datenkosten nach jeweils 10.000 Trade-Events."""
        if str(event.get("txType") or "").lower() not in ("buy", "sell"):
            return
        self.metered_trade_events += 1
        due_batches = self.metered_trade_events // PUMPPORTAL_TRADES_PER_FEE
        while self.metered_batches_billed < due_batches:
            batch = self.metered_batches_billed + 1
            fee_eur = round(self.data_fee_sol_per_10k * await self._get_sol_eur(), 6)
            await paper_db_module.log_resolved_paper_trade(
                f"{self.prefix}DATA_API_{batch}",
                "fee",
                1.0,
                fee_eur,
                0.0,
                0.0,
                -fee_eur,
                "paper",
                self.db_path,
            )
            self.capital_remaining -= fee_eur
            self.data_fees_eur += fee_eur
            self.metered_batches_billed = batch
            self._save_state()
            logger.info(
                "PUMP PumpPortal-Datengebühr Batch %d: %.4f SOL = %.4f€",
                batch,
                self.data_fee_sol_per_10k,
                fee_eur,
            )

    @staticmethod
    def _num(event, key, default=0.0):
        try: return float(event.get(key) or default)
        except (TypeError, ValueError): return default

    @classmethod
    def _phase(cls, event):
        pool = str(event.get("pool") or "pump").lower()
        complete = bool(event.get("complete"))
        return PHASE_MIGRATED if complete or pool not in ("", "pump") else PHASE_EARLY

    def _cleanup(self, now):
        cutoff = now - self.max_age_sec
        for mint, item in list(self.candidates.items()):
            if mint not in self.portfolio and float(item.get("last_ts", now)) < cutoff:
                self.candidates.pop(mint, None)
                self.subscribed.discard(mint)
                self.pending_unsubscribes.add(mint)
        if len(self.candidates) > self.max_candidates:
            removable = sorted((v.get("last_ts", 0), k) for k, v in self.candidates.items() if k not in self.portfolio)
            for _, mint in removable[:len(self.candidates) - self.max_candidates]:
                self.candidates.pop(mint, None)
                self.subscribed.discard(mint)
                self.pending_unsubscribes.add(mint)

    def _record(self, event):
        mint = event.get("mint")
        if not mint: return None, False
        now = time.time()
        mcap = self._num(event, "marketCapSol")
        if mcap <= 0: return None, False
        item = self.candidates.get(mint)
        is_new = item is None
        if item is None:
            item = {"mint": mint, "symbol": str(event.get("symbol") or event.get("name") or mint[:6]).upper()[:16],
                    "created_ts": now, "first_mcap": mcap, "peak_mcap": mcap,
                    "last_ts": now, "last_mcap": mcap, "buys": 0, "sells": 0,
                    "trades": 0, "traders": set(), "recent": deque(maxlen=40),
                    "phase": self._phase(event), "vsol": 0.0, "vtokens": 0.0,
                    "pool": str(event.get("pool") or "pump")}
            self.candidates[mint] = item
        item["last_ts"] = now
        item["last_mcap"] = mcap
        item["peak_mcap"] = max(float(item.get("peak_mcap", mcap)), mcap)
        item["phase"] = self._phase(event)
        item["pool"] = str(event.get("pool") or item.get("pool") or "pump")
        item["vsol"] = self._num(event, "vSolInBondingCurve", item.get("vsol", 0))
        item["vtokens"] = self._num(event, "vTokensInBondingCurve", item.get("vtokens", 0))
        tx = str(event.get("txType") or "").lower()
        if tx in ("buy", "sell"):
            item["trades"] += 1
            item["buys"] += tx == "buy"
            item["sells"] += tx == "sell"
            trader = event.get("traderPublicKey")
            if trader: item["traders"].add(trader)
        item["recent"].append((now, mcap, tx, item["phase"]))
        return item, is_new

    @staticmethod
    def _recent_change(item, now):
        recent = item.get("recent") or []
        cutoff = now - 30
        base = next(((ts, mc) for ts, mc, _, _ in recent if ts >= cutoff), None)
        if not base: return 0.0
        return (float(item["last_mcap"]) / max(float(base[1]), 1e-9) - 1) * 100

    async def _curve_buy(self, item, amount_eur):
        sol_eur = await self._get_sol_eur()
        vsol, vtokens = float(item.get("vsol") or 0), float(item.get("vtokens") or 0)
        sol_in = amount_eur / max(sol_eur, 1e-9)
        if vsol <= 0 or vtokens <= 0 or sol_in <= 0: return None
        net = sol_in * (1 - self.platform_fee_pct / 100)
        token_out = vtokens - ((vsol * vtokens) / (vsol + net))
        if token_out <= 0: return None
        return {"shares": token_out, "entry_price": amount_eur / token_out, "sol_in": sol_in}

    async def _mark(self, item, pos):
        if pos.get("phase") == PHASE_EARLY and float(item.get("vsol") or 0) > 0 and float(item.get("vtokens") or 0) > 0:
            sol_eur = await self._get_sol_eur()
            token_in = float(pos.get("shares") or 0)
            vsol, vtokens = float(item["vsol"]), float(item["vtokens"])
            sol_out = vsol - ((vsol * vtokens) / (vtokens + token_in))
            sol_out *= 1 - self.platform_fee_pct / 100
            return max(0.0, sol_out * sol_eur)
        ratio = float(item.get("last_mcap", 0)) / max(float(pos.get("entry_mcap", 1)), 1e-9)
        entry_factor = float(pos.get("entry_value_factor", 1 - float(pos.get("entry_fee_pct", 0.0)) / 100))
        gross = float(pos["cost_basis"]) * entry_factor * ratio
        exit_fee_pct = self._trade_fee_pct(PHASE_MIGRATED, float(item.get("last_mcap", 0)))
        return max(0.0, gross * (1 - self.migrated_slippage_pct / 100) * (1 - exit_fee_pct / 100))

    async def consider_entry(self, item):
        now = time.time(); mint = item["mint"]
        if mint in self.portfolio or self.cooldowns.get(mint, 0) > now or len(self.portfolio) >= self.max_open_positions: return None
        age = now - float(item["created_ts"])
        phase = item.get("phase", PHASE_EARLY)
        mcap = float(item["last_mcap"])
        change = (mcap / max(float(item["first_mcap"]), 1e-9) - 1) * 100
        recent_change = self._recent_change(item, now)
        ratio = float(item["buys"]) / max(float(item["sells"]), 1.0)
        min_trades = self.migrated_min_trades if phase == PHASE_MIGRATED else self.min_trades
        lo, hi = ((self.migrated_min_change_pct, self.migrated_max_change_pct) if phase == PHASE_MIGRATED else (self.min_change_pct, self.max_change_pct))
        max_mcap = self.migrated_max_market_cap_sol if phase == PHASE_MIGRATED else self.max_market_cap_sol
        if not (self.min_age_sec <= age <= self.max_age_sec and self.min_market_cap_sol <= mcap <= max_mcap): return None
        if not (lo <= change <= hi and recent_change >= self.min_recent_change_pct): return None
        if item["trades"] < min_trades or len(item["traders"]) < self.min_unique_traders or ratio < self.min_buy_sell_ratio: return None
        if not item["recent"] or item["recent"][-1][2] != "buy": return None
        amount = min(self.position_eur, self.capital_remaining)
        if amount < 1: return None
        entry_fee_pct = self._trade_fee_pct(phase, mcap)
        fill = await self._curve_buy(item, amount) if phase == PHASE_EARLY else None
        if phase == PHASE_EARLY and not fill: return None
        if not fill:
            # Ledger-Invariante: size * price muss immer dem EUR-Einsatz
            # entsprechen. Die Mark-to-Market-Logik nutzt weiter entry_mcap.
            fill = {"shares": amount, "entry_price": 1.0, "sol_in": 0.0}
        trade_id = await log_paper_trade(
            f"{self.prefix}{item['symbol']}@{mint}",
            "buy",
            fill["shares"],
            fill["entry_price"],
            change / 100,
            "paper",
        )
        self.capital_remaining -= amount
        self.portfolio[mint] = {"symbol": item["symbol"], "shares": fill["shares"], "cost_basis": amount,
            "entry_price": fill["entry_price"], "entry_ts": now, "entry_mcap": mcap,
            "entry_fee_pct": entry_fee_pct, "entry_value_factor": 1 - entry_fee_pct / 100,
            "peak_value": amount * (1 - entry_fee_pct / 100),
            "mark_value": amount * (1 - entry_fee_pct / 100), "phase": phase,
            "trailing_active": False, "trade_id": trade_id}
        self.trade_count += 1; self._save_state()
        logger.info("PUMP Entry %s phase=%s %.2f€ mcap=%.2fSOL change=%+.1f%%", item["symbol"], phase, amount, mcap, change)
        return {
            "mint": mint,
            "symbol": item["symbol"],
            "phase": phase,
            "amount": amount,
            "change_pct": change,
            "entry_fee_pct": entry_fee_pct,
        }

    async def manage(self, item):
        mint = item["mint"]; pos = self.portfolio.get(mint)
        if not pos: return None
        if item.get("phase") == PHASE_MIGRATED and pos.get("phase") == PHASE_EARLY:
            pos["phase"] = PHASE_MIGRATED
            logger.info("PUMP Migration erkannt %s (%s)", pos["symbol"], item.get("pool"))
        if pos.get("needs_recovery_exit"):
            exit_fee_pct = self._trade_fee_pct(PHASE_MIGRATED, float(item.get("last_mcap", 0)))
            value = float(pos["cost_basis"]) * (1 - self.migrated_slippage_pct / 100) * (1 - exit_fee_pct / 100)
        else:
            value = await self._mark(item, pos)
        pos["mark_value"] = value; pos["peak_value"] = max(float(pos.get("peak_value", value)), value)
        change = (value / max(float(pos["cost_basis"]), 1e-9) - 1) * 100
        age = time.time() - float(pos.get("entry_ts", time.time()))
        hold_limit = self.migrated_max_hold_sec if pos.get("phase") == PHASE_MIGRATED else self.max_hold_sec
        reason = "state_recovery_exit" if pos.get("needs_recovery_exit") else None
        if not reason and change <= -self.stop_loss_pct: reason = "stop_loss"
        elif not reason and age >= hold_limit: reason = "time_exit"
        elif not reason and pos.get("trailing_active") and value <= max(float(pos["cost_basis"]) * (1 + self.trail_floor_pct / 100), float(pos["peak_value"]) * (1 - self.trailing_stop_pct / 100)): reason = "trailing_stop"
        elif not reason and change >= self.take_profit_pct: pos["trailing_active"] = True
        if not reason: return None
        pnl = value - float(pos["cost_basis"])
        if not await resolve_trade(int(pos["trade_id"]), value / max(float(pos["shares"]), 1e-9), round(pnl, 6)):
            self._rebuild_state_from_db()
            self._save_state()
            return None
        self.capital_remaining += float(pos["cost_basis"]) + pnl
        self.cooldowns[mint] = time.time() + (6 * 3600 if reason == "stop_loss" else 2 * 3600)
        self.portfolio.pop(mint, None); self._save_state()
        logger.info("PUMP Exit %s phase=%s %s PnL=%+.4f€", pos["symbol"], pos.get("phase"), reason, pnl)
        exit_fee_pct = self._trade_fee_pct(pos.get("phase", PHASE_EARLY), float(item.get("last_mcap", 0)))
        return {
            "mint": mint,
            "symbol": pos["symbol"],
            "reason": reason,
            "pnl": pnl,
            "entry_fee_pct": float(pos.get("entry_fee_pct", self.platform_fee_pct)),
            "exit_fee_pct": exit_fee_pct,
        }

    async def on_event(self, event):
        await self._charge_metered_data_if_due(event)
        item, is_new = self._record(event)
        if not item: return []
        now = time.time(); self._cleanup(now)
        result = []
        if not is_new or str(event.get("txType") or "").lower() in ("buy", "sell"):
            entry = await self.consider_entry(item)
            if entry: result.append(entry)
        exit_result = await self.manage(item)
        if exit_result: result.append(exit_result)
        return result

    async def maybe_snapshot(self, force=False):
        if not force and time.time() - self.last_snapshot < self.snapshot_interval_sec: return
        mtm = sum(float(p.get("mark_value", p["cost_basis"])) for p in self.portfolio.values())
        realized = await paper_db_module.get_realized_pnl_by_prefix(self.prefix)
        await log_equity_snapshot(self.bot_key, self.capital_remaining + mtm, self.capital_remaining, len(self.portfolio), mtm - sum(float(p["cost_basis"]) for p in self.portfolio.values()), realized)
        self.last_snapshot = time.time(); self._save_state()

    async def run(self):
        await paper_db_module.init_db()
        if not self.api_key or not self.metered_data_enabled:
            logger.error(
                "PUMP Trade-Stream deaktiviert: PUMPPORTAL_API_KEY und "
                "PUMP*_METERED_DATA_ENABLED=true sind erforderlich; "
                "subscribeTokenTrade verursacht externe Datengebühren."
            )
            await asyncio.Event().wait()
            return
        lock_path = self.db_path.parent / "pumpportal_stream.lock"
        lock_handle = lock_path.open("a+")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error(
                "PUMP Trade-Stream nicht gestartet: PumpPortal erlaubt nur eine "
                "WebSocket-Verbindung; bereits von einer anderen Bot-Variante belegt."
            )
            await asyncio.Event().wait()
            return
        logger.info("PUMP %s gestartet [PAPER] | early+ migrated | %.2f€ | NO WALLET / NO ORDERS", self.strategy_version, self.initial_capital_eur)
        while True:
            try:
                ws_url = f"{WS_URL}?api-key={self.api_key}"
                async with websockets.connect(ws_url, ping_interval=20, open_timeout=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    existing = sorted(set(self.portfolio) | set(self.candidates))
                    if existing:
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": existing}))
                        self.subscribed.update(existing)
                    logger.info("PUMP PumpPortal new-token stream verbunden")
                    async for raw in ws:
                        try: event = json.loads(raw)
                        except json.JSONDecodeError: continue
                        if not event.get("mint"): continue
                        mint = event["mint"]
                        item = self.candidates.get(mint)
                        event_phase = self._phase(event)
                        phase_cap = self.migrated_max_market_cap_sol if event_phase == PHASE_MIGRATED else self.max_market_cap_sol
                        if item is None and self._num(event, "marketCapSol") <= phase_cap:
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                            self.subscribed.add(mint)
                        await self.on_event(event)
                        if self.pending_unsubscribes:
                            keys = sorted(self.pending_unsubscribes)
                            await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": keys}))
                            self.pending_unsubscribes.clear()
                        await self.maybe_snapshot()
            except asyncio.CancelledError: raise
            except Exception as exc:
                safe_error = str(exc).replace(self.api_key, "[redacted]")
                logger.warning("PUMP Streamfehler %s – Reconnect in 15s", safe_error)
                await asyncio.sleep(15)
