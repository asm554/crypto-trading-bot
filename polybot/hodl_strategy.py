"""Long-term allocation paper bot -- Der HODLer.

Uses only completed daily Kraken candles. There is deliberately no live order
path, wallet, private key, or normal stop-loss.
"""
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
PREFIX, BOT_KEY = "HODL_", "hodl"
ALLOCATIONS = {"XBTEUR": .50, "ETHEUR": .30, "SOLEUR": .20}


class HodlBot:
    def __init__(self, initial_capital_eur=100, cash_reserve_eur=20, max_weekly_eur=20,
                 bear_rate_pct=35, overheat_momentum_pct=50, overheat_ema_pct=25,
                 paper_mode=True, snapshot_interval_sec=3600):
        if not paper_mode:
            raise NotImplementedError("HodlBot is paper-only")
        self.initial_capital_eur, self.capital_remaining = float(initial_capital_eur), float(initial_capital_eur)
        self.cash_reserve_eur, self.max_weekly_eur = float(cash_reserve_eur), float(max_weekly_eur)
        self.bear_rate_pct, self.overheat_momentum_pct, self.overheat_ema_pct = float(bear_rate_pct), float(overheat_momentum_pct), float(overheat_ema_pct)
        self.snapshot_interval_sec = int(snapshot_interval_sec); self.portfolio = {}; self.weekly_spend = {}; self.last_daily_scan = ""; self.last_snapshot = 0.
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent; data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path, self.db_path = data_dir / "hodl_state.json", Path(paper_db_module.DB_PATH).resolve(); self._load()

    def _load(self):
        try:
            state = json.loads(self.state_path.read_text()); self.capital_remaining = float(state["capital_remaining"])
            self.portfolio = state.get("portfolio", {}); self.weekly_spend = state.get("weekly_spend", {}); self.last_daily_scan = state.get("last_daily_scan", ""); self.last_snapshot = float(state.get("last_snapshot", 0))
            state_ids = {
                int(pos.get("trade_id") or 0)
                for pos in self.portfolio.values()
                if int(pos.get("trade_id") or 0) > 0
            }
            if state_ids != paper_db_module.get_open_trade_ids_by_prefix_sync(PREFIX):
                raise ValueError("State und offenes HODL-Ledger weichen ab")
        except Exception:
            self._rebuild()
            self._save()

    def _save(self):
        payload = {"capital_remaining": self.capital_remaining, "portfolio": self.portfolio, "weekly_spend": self.weekly_spend, "last_daily_scan": self.last_daily_scan, "last_snapshot": self.last_snapshot}
        temp = self.state_path.with_suffix(".json.tmp"); temp.write_text(json.dumps(payload, separators=(",", ":"))); temp.replace(self.state_path)

    def _rebuild(self):
        if not self.db_path.exists(): return
        self.portfolio = {}
        self.weekly_spend = {}
        self.last_daily_scan = ""
        realized = open_cost = 0.
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row; rows = conn.execute(
                "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\'",
                (paper_db_module.prefix_like_pattern(PREFIX),),
            ).fetchall()
        for row in rows:
            cost = float(row["size"] or 0) * float(row["price"] or 0); label = str(row["market_question"]).removeprefix(PREFIX)
            timestamp = float(row["timestamp"] or 0)
            if timestamp > 0:
                opened = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
                week = opened.strftime("%G-W%V")
                self.weekly_spend[week] = float(self.weekly_spend.get(week, 0.0)) + cost
                self.last_daily_scan = max(self.last_daily_scan, opened.date().isoformat())
            if row["resolved_at"] is None:
                self.portfolio[str(row["id"])] = {"pair": label.split("_")[0], "shares": float(row["size"]), "cost_basis": cost, "trade_id": int(row["id"]), "stage": label.split("_")[-1]}
                open_cost += cost
            else: realized += float(row["real_pnl"] or 0)
        self.capital_remaining = max(0., self.initial_capital_eur - open_cost + realized)

    async def _market(self, pair):
        rows = closed_ohlc_rows(await fetch_ohlc(pair, 1440), 1440)
        if len(rows) < 201: return None
        closes = [r[4] for r in rows]; ema50, ema200 = ema_series(closes, 50), ema_series(closes, 200)
        if not ema50 or not ema200 or closes[-91] <= 0: return None
        momentum = (closes[-1] / closes[-91] - 1) * 100
        return {"close": closes[-1], "ema50": ema50[-1], "ema200": ema200[-1], "momentum": momentum}

    @staticmethod
    def _phase(market, overheat_momentum_pct=50.0, overheat_ema_pct=25.0):
        if market["momentum"] >= overheat_momentum_pct or (market["close"] / market["ema50"] - 1) * 100 >= overheat_ema_pct: return "overheated"
        if market["close"] < market["ema200"] or market["momentum"] < 0: return "bear"
        if market["close"] > market["ema50"] > market["ema200"] and market["momentum"] > 0: return "bull"
        return "neutral"

    async def manage_positions(self):
        if not self.portfolio: return []
        pairs = sorted({pos["pair"] for pos in self.portfolio.values()}); ticker = await fetch_ticker_data(pairs); closed = []
        fee = config.CRYPTO_TAKER_FEE_RATE
        for key, pos in list(self.portfolio.items()):
            data = ticker.get(PAIR_MAP.get(pos["pair"], pos["pair"])) or ticker.get(pos["pair"])
            if not data: continue
            last = float(data["c"][0]); bid, _ = extract_quote(data, last); entry = float(pos["cost_basis"]) / float(pos["shares"])
            target = 100 if pos["stage"] == "profit100" else 200 if pos["stage"] == "profit200" else None
            if target is None or (bid / entry - 1) * 100 < target: continue
            value = float(pos["shares"]) * bid * (1 - fee); pnl = value - float(pos["cost_basis"])
            if not await resolve_trade(int(pos["trade_id"]), bid, round(pnl, 6)):
                self._rebuild(); self._save(); return closed
            self.capital_remaining += value; self.portfolio.pop(key); closed.append({"pair": pos["pair"], "reason": f"profit_{target}", "pnl": pnl})
        self._save(); return closed

    async def scan_entries(self):
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if self.last_daily_scan == today: return []
        self.last_daily_scan = today; week = dt.datetime.now(dt.timezone.utc).strftime("%G-W%V")
        spent = float(self.weekly_spend.get(week, 0)); capacity = min(self.max_weekly_eur - spent, self.capital_remaining - self.cash_reserve_eur)
        if capacity <= 0: self._save(); return []
        markets = {pair: await self._market(pair) for pair in ALLOCATIONS}; phases = {
            pair: self._phase(market, self.overheat_momentum_pct, self.overheat_ema_pct) if market else "unknown"
            for pair, market in markets.items()
        }
        weights = {pair: weight for pair, weight in ALLOCATIONS.items() if phases[pair] != "overheated"}
        if phases["XBTEUR"] == "bear":
            bear_weekly_cap = self.max_weekly_eur * self.bear_rate_pct / 100
            capacity = min(
                max(0.0, bear_weekly_cap - spent),
                max(0.0, self.capital_remaining - self.cash_reserve_eur),
            )
            weights = {"XBTEUR": 1.0}
        elif any(phase == "bear" for phase in phases.values()): weights = {pair: weight for pair, weight in weights.items() if pair == "XBTEUR"}
        total = sum(weights.values())
        if not total: self._save(); return []
        ticker = await fetch_ticker_data(list(weights)); fee = config.CRYPTO_TAKER_FEE_RATE; opened = []
        for pair, weight in weights.items():
            amount = capacity * weight / total; data = ticker.get(PAIR_MAP.get(pair, pair)) or ticker.get(pair)
            if not data or amount < .01: continue
            last = float(data["c"][0]); _, ask = extract_quote(data, last); shares = amount / (ask * (1 + fee))
            for stage, fraction in (("core", .5), ("profit100", .25), ("profit200", .25)):
                tranche = shares * fraction; cost = tranche * ask * (1 + fee)
                trade_id = await log_paper_trade(f"{PREFIX}{pair}_{stage}", "buy", tranche, ask * (1 + fee), 0., "paper")
                self.portfolio[str(trade_id)] = {"pair": pair, "shares": tranche, "cost_basis": cost, "trade_id": trade_id, "stage": stage}
            self.capital_remaining -= amount; opened.append({"pair": pair, "amount": amount, "phase": phases[pair]})
        self.weekly_spend[week] = spent + sum(item["amount"] for item in opened); self._save(); return opened

    async def equity(self):
        pairs = sorted({pos["pair"] for pos in self.portfolio.values()}); ticker = await fetch_ticker_data(pairs) if pairs else {}; fee = config.CRYPTO_TAKER_FEE_RATE; mtm = unrealized = 0.; trade_pnls = {}
        for pos in self.portfolio.values():
            cost = float(pos["cost_basis"]); data = ticker.get(PAIR_MAP.get(pos["pair"], pos["pair"])) or ticker.get(pos["pair"])
            if not data: value = cost
            else:
                bid, _ = extract_quote(data, float(data["c"][0])); value = float(pos["shares"]) * bid * (1 - fee)
            position_pnl = value - cost; mtm += value; unrealized += position_pnl; trade_pnls[int(pos["trade_id"])] = position_pnl
        await paper_db_module.update_unrealized_pnls(trade_pnls)
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {"equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining, "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

    async def maybe_snapshot(self, force=False):
        if not force and time.time() - self.last_snapshot < self.snapshot_interval_sec: return
        await log_equity_snapshot(BOT_KEY, **await self.equity()); self.last_snapshot = time.time(); self._save()
    async def run(self):
        logger.info("HODLer gestartet [PAPER] | Budget %.2f EUR", self.initial_capital_eur)
        await self.maybe_snapshot(force=True)
        while True:
            try:
                await self.manage_positions(); opened = await self.scan_entries(); await self.maybe_snapshot(force=bool(opened))
            except Exception: logger.exception("HODLer loop error")
            await asyncio.sleep(60)
