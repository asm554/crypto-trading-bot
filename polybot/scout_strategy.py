"""Jupiter new-pool paper bot -- Der Spaeher.

Discovers recent Solana pools through Jupiter Tokens V2 and buys only after a
strict 20 minute observation period. This module never creates or submits a
transaction: Jupiter quotes are used solely as paper-trading route checks.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import config  # Loads polybot/.env, including JUPITER_API_KEY.
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import fetch_ticker_data
from polybot.memecoin_strategy import EURUSD_INTERNAL, EURUSD_PAIR, FALLBACK_EUR_USD_RATE
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "SCOUT_"
BOT_KEY = "scout"
JUPITER_API = "https://api.jup.ag"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6


async def fetch_scout_prices(mints: list[str], api_key: str | None = None) -> dict:
    """Fetches Jupiter V3 prices for paper valuation; missing data is benign."""
    key = api_key or os.getenv("JUPITER_API_KEY", "")
    if not key or not mints:
        return {}
    try:
        async with aiohttp.ClientSession(headers={"x-api-key": key}) as session:
            async with session.get(f"{JUPITER_API}/price/v3", params={"ids": ",".join(mints[:50])}, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                data = await resp.json() if resp.status == 200 else {}
                return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Scout price fetch fehlgeschlagen: %s", exc)
        return {}


def _number(data: dict, *names: str, default: float = 0.0) -> float:
    for name in names:
        value = data.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return default


def _truth(data: dict, *names: str) -> bool:
    return any(data.get(name) is True for name in names)


def _symbol(token: dict) -> str:
    raw = str(token.get("symbol") or token.get("name") or "TOKEN")
    return "".join(char for char in raw.upper() if char.isalnum())[:12] or "TOKEN"


def score_token(token: dict, *, min_liquidity_usd: float = 40_000, min_holders: int = 150) -> tuple[int, list[str]]:
    """Returns a transparent 100-point score and hard-gate rejection reasons.

    Jupiter can add fields over time; this deliberately accepts documented
    aliases but fails closed when a security or activity field is absent.
    """
    audit = token.get("audit") or {}
    stats = token.get("stats5m") or token.get("stats") or {}
    reasons: list[str] = []
    mint_disabled = _truth(audit, "mintAuthorityDisabled", "mintAuthorityDisabledAt")
    freeze_disabled = _truth(audit, "freezeAuthorityDisabled", "freezeAuthorityDisabledAt")
    shield_ok = not _truth(audit, "isSus", "isScam", "hasWarning") and not _truth(token, "isSus", "hasWarning")
    clean_audit = shield_ok and not _truth(audit, "isHoneypot", "isMutable")
    liquidity = _number(token, "liquidity", "liquidityUsd")
    holders = int(_number(token, "holderCount", "holders"))
    top_holders = _number(token, "topHoldersPercentage", "topHolderPct", default=101)
    developer = _number(token, "developerHoldingsPercentage", "developerPct", default=101)
    organic = _number(token, "organicScore", default=-1)
    volume = _number(stats, "volumeUsd", "volume", "volume5m")
    organic_volume = _number(stats, "organicBuyVolumeUsd", "organicBuyVolume")
    organic_buyers = int(_number(stats, "organicBuyers"))
    traders = int(_number(stats, "traders", "uniqueTraders"))
    buys = _number(stats, "buys", "buyCount")
    sells = _number(stats, "sells", "sellCount")
    m5 = _number(token, "priceChange5m", "m5Change", default=999)
    h1 = _number(token, "priceChange1h", "h1Change", default=999)
    fdv = _number(token, "fdv", "marketCap", default=float("inf"))
    if not mint_disabled: reasons.append("mint_authority")
    if not freeze_disabled: reasons.append("freeze_authority")
    if not clean_audit: reasons.append("audit_or_shield")
    if liquidity < min_liquidity_usd: reasons.append("liquidity")
    if holders < min_holders: reasons.append("holders")
    if top_holders > 30: reasons.append("top_holders")
    if developer > 10: reasons.append("developer")
    if organic < 30: reasons.append("organic_score")
    if volume < 5_000 or organic_volume < 250 or organic_buyers < 5 or traders < 30: reasons.append("organic_activity")
    ratio = buys / max(sells, 1)
    if not 1.1 <= ratio <= 4.0: reasons.append("buy_sell_ratio")
    if not 0.5 <= m5 <= 20 or not -10 <= h1 <= 80: reasons.append("momentum")
    if fdv > 20_000_000 or liquidity / max(fdv, 1) < 0.01: reasons.append("valuation")
    score = (
        20 * int(mint_disabled and freeze_disabled) + 15 * int(clean_audit) +
        10 * int(liquidity >= min_liquidity_usd) + 10 * int(holders >= min_holders) +
        10 * int(top_holders <= 30) + 10 * int(developer <= 10) +
        10 * int(organic >= 30) + 10 * int(volume >= 5_000 and organic_volume >= 250 and organic_buyers >= 5 and traders >= 30) +
        5 * int(1.1 <= ratio <= 4.0 and 0.5 <= m5 <= 20 and -10 <= h1 <= 80)
    )
    return score, reasons


class ScoutBot:
    def __init__(self, initial_capital_eur: float = 100, interval_sec: int = 30, position_eur: float = 5,
                 max_open_positions: int = 2, cash_reserve_eur: float = 85, maturity_sec: int = 20 * 60,
                 max_pool_age_sec: int = 12 * 3600, min_score: int = 60, max_price_impact_pct: float = 1.5,
                 max_round_trip_cost_pct: float = 8, paper_slippage_pct: float = .5, stop_loss_pct: float = 12,
                 take_profit_pct: float = 25, trail_activation_pct: float = 10, trailing_stop_pct: float = 8,
                 max_hold_sec: int = 6 * 3600, loss_streak_limit: int = 2, risk_off_sec: int = 12 * 3600,
                 account_loss_limit_pct: float = 8, snapshot_interval_sec: int = 15 * 60,
                 api_key: str | None = None, paper_mode: bool = True):
        if not paper_mode:
            raise NotImplementedError("ScoutBot is paper-only")
        self.initial_capital_eur, self.capital_remaining = float(initial_capital_eur), float(initial_capital_eur)
        self.interval_sec, self.position_eur = int(interval_sec), float(position_eur)
        self.max_open_positions, self.cash_reserve_eur = int(max_open_positions), float(cash_reserve_eur)
        self.maturity_sec, self.max_pool_age_sec, self.min_score = int(maturity_sec), int(max_pool_age_sec), int(min_score)
        self.max_price_impact_pct, self.max_round_trip_cost_pct, self.paper_slippage_pct = float(max_price_impact_pct), float(max_round_trip_cost_pct), float(paper_slippage_pct)
        self.stop_loss_pct, self.take_profit_pct, self.trail_activation_pct, self.trailing_stop_pct = map(float, (stop_loss_pct, take_profit_pct, trail_activation_pct, trailing_stop_pct))
        self.max_hold_sec, self.loss_streak_limit, self.risk_off_sec, self.account_loss_limit_pct = int(max_hold_sec), int(loss_streak_limit), int(risk_off_sec), float(account_loss_limit_pct)
        self.snapshot_interval_sec, self.api_key = int(snapshot_interval_sec), api_key or os.getenv("JUPITER_API_KEY", "")
        self.portfolio: dict[str, dict] = {}; self.watchlist: dict[str, dict] = {}
        self.consecutive_losses = 0; self.risk_off_until = 0.0; self.last_snapshot = 0.0; self.last_scan = 0.0
        data_dir = Path(paper_db_module.DB_PATH).resolve().parent; data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "scout_state.json"; self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self._load_state()

    def _save_state(self):
        payload = {"capital_remaining": self.capital_remaining, "portfolio": self.portfolio, "watchlist": self.watchlist,
                   "consecutive_losses": self.consecutive_losses, "risk_off_until": self.risk_off_until,
                   "last_snapshot": self.last_snapshot, "last_scan": self.last_scan}
        temp = self.state_path.with_suffix(".json.tmp"); temp.write_text(json.dumps(payload, separators=(",", ":"))); temp.replace(self.state_path)

    def _load_state(self):
        try:
            raw = json.loads(self.state_path.read_text()); self.capital_remaining = float(raw["capital_remaining"])
            self.portfolio = raw.get("portfolio") or {}; self.watchlist = raw.get("watchlist") or {}
            self.consecutive_losses = int(raw.get("consecutive_losses", 0)); self.risk_off_until = float(raw.get("risk_off_until", 0))
            self.last_snapshot = float(raw.get("last_snapshot", 0)); self.last_scan = float(raw.get("last_scan", 0))
        except Exception:
            self._rebuild_state()

    def _rebuild_state(self):
        if not self.db_path.exists(): return
        realized = open_cost = 0.0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM paper_trades WHERE market_question LIKE ? ORDER BY id", (f"{PREFIX}%",)).fetchall()
        for row in rows:
            cost = float(row["size"] or 0) * float(row["price"] or 0)
            if row["resolved_at"] is None:
                mint = str(row["market_question"]).partition("@")[2]; open_cost += cost
                self.portfolio[mint] = {"shares": float(row["size"]), "cost_basis": cost, "entry_price": float(row["price"]), "entry_ts": float(row["timestamp"]), "trade_id": int(row["id"]), "needs_recovery_exit": True}
            else: realized += float(row["real_pnl"] or 0)
        self.capital_remaining = max(0, self.initial_capital_eur - open_cost + realized)

    def _headers(self): return {"x-api-key": self.api_key}
    async def _get(self, path: str, params: dict | None = None):
        if not self.api_key: return None
        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                async with session.get(f"{JUPITER_API}{path}", params=params, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    return await resp.json() if resp.status == 200 else None
        except Exception as exc: logger.warning("Scout Jupiter %s fehlgeschlagen: %s", path, exc); return None

    async def _recent(self):
        data = await self._get("/tokens/v2/recent")
        return data if isinstance(data, list) else []
    async def _tokens(self, mints: list[str]):
        data = await self._get("/tokens/v2/search", {"query": ",".join(mints[:100])})
        return {str(x.get("id")): x for x in data} if isinstance(data, list) else {}
    async def _prices(self, mints: list[str]):
        return await fetch_scout_prices(mints, self.api_key)
    async def _quote(self, input_mint: str, output_mint: str, amount: int):
        return await self._get("/swap/v1/quote", {"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount), "slippageBps": 50, "restrictIntermediateTokens": "true"})
    async def _eurusd(self):
        ticker = await fetch_ticker_data([EURUSD_PAIR]); row = ticker.get(EURUSD_INTERNAL) or ticker.get(EURUSD_PAIR)
        try: return float(row["c"][0])
        except (KeyError, TypeError, ValueError): return FALLBACK_EUR_USD_RATE

    async def _route_ok(self, mint: str, eurusd: float) -> bool:
        amount = max(1, round(self.position_eur * eurusd * 10 ** USDC_DECIMALS))
        buy = await self._quote(USDC_MINT, mint, amount)
        if not isinstance(buy, dict) or not buy.get("outAmount") or float(buy.get("priceImpactPct") or 99) * 100 > self.max_price_impact_pct: return False
        sell = await self._quote(mint, USDC_MINT, int(buy["outAmount"]))
        if not isinstance(sell, dict) or float(sell.get("priceImpactPct") or 99) * 100 > self.max_price_impact_pct: return False
        received = int(sell.get("outAmount") or 0); return received > 0 and (1 - received / amount) * 100 <= self.max_round_trip_cost_pct

    async def manage_positions(self):
        if not self.portfolio: return []
        prices = await self._prices(list(self.portfolio)); eurusd = await self._eurusd(); now = time.time(); closed = []
        for mint, pos in list(self.portfolio.items()):
            price = _number(prices.get(mint) or {}, "usdPrice", "price") / eurusd
            if price <= 0: continue
            entry = float(pos["entry_price"]); peak = max(float(pos.get("peak_price") or entry), price); pos["peak_price"] = peak
            change = (price / entry - 1) * 100; reason = "state_recovery_exit" if pos.get("needs_recovery_exit") else None
            if not reason and change <= -self.stop_loss_pct: reason = "stop_loss"
            elif not reason and change >= self.take_profit_pct: reason = "take_profit"
            elif not reason and change >= self.trail_activation_pct and price <= peak * (1 - self.trailing_stop_pct / 100): reason = "trailing_stop"
            elif not reason and now - float(pos["entry_ts"]) >= self.max_hold_sec: reason = "time_exit"
            if not reason: continue
            value = float(pos["shares"]) * price * (1 - self.paper_slippage_pct / 100); pnl = value - float(pos["cost_basis"])
            await resolve_trade(int(pos["trade_id"]), price, round(pnl, 6)); self.capital_remaining += value; self.portfolio.pop(mint)
            self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0
            if self.consecutive_losses >= self.loss_streak_limit: self.risk_off_until = now + self.risk_off_sec
            closed.append({"mint": mint, "reason": reason, "pnl": pnl})
        self._save_state(); return closed

    async def scan_entries(self):
        now = time.time()
        if now - self.last_scan < self.interval_sec or not self.api_key: return []
        self.last_scan = now
        for token in await self._recent():
            mint = str(token.get("id") or ""); first_pool = (token.get("firstPool") or {}).get("createdAt")
            if mint and mint not in self.watchlist and _number(token, "liquidity", "liquidityUsd") >= 5_000:
                self.watchlist[mint] = {"seen_at": now, "first_pool": first_pool}
        if now < self.risk_off_until or self.capital_remaining <= self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100): self._save_state(); return []
        details = await self._tokens(list(self.watchlist)); eurusd = await self._eurusd(); opened = []
        for mint, watch in list(self.watchlist.items()):
            if len(self.portfolio) >= self.max_open_positions or self.capital_remaining - self.position_eur < self.cash_reserve_eur: break
            age = now - float(watch["seen_at"])
            if age < self.maturity_sec: continue
            if age > self.max_pool_age_sec: self.watchlist.pop(mint, None); continue
            token = details.get(mint)
            if not token or mint in self.portfolio: continue
            score, reasons = score_token(token)
            if reasons or score < self.min_score or not await self._route_ok(mint, eurusd): continue
            price = _number(token, "usdPrice", "price") / eurusd
            if price <= 0: continue
            entry = price * (1 + self.paper_slippage_pct / 100); shares = self.position_eur / entry; symbol = _symbol(token)
            trade_id = await log_paper_trade(f"{PREFIX}{symbol}@{mint}", "buy", shares, entry, score / 100, "paper")
            self.capital_remaining -= self.position_eur; self.portfolio[mint] = {"symbol": symbol, "shares": shares, "cost_basis": self.position_eur, "entry_price": entry, "entry_ts": now, "peak_price": entry, "trade_id": trade_id}; opened.append({"symbol": symbol, "mint": mint, "score": score})
        self._save_state(); return opened

    async def equity(self):
        prices = await self._prices(list(self.portfolio)); eurusd = await self._eurusd(); mtm = unrealized = 0.0
        for mint, pos in self.portfolio.items():
            cost = float(pos["cost_basis"]); price = _number(prices.get(mint) or {}, "usdPrice", "price") / eurusd
            value = float(pos["shares"]) * price * (1 - self.paper_slippage_pct / 100) if price > 0 else cost
            mtm += value; unrealized += value - cost
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {"equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining, "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

    async def maybe_snapshot(self, force=False):
        if not force and time.time() - self.last_snapshot < self.snapshot_interval_sec: return
        await log_equity_snapshot(BOT_KEY, **await self.equity()); self.last_snapshot = time.time(); self._save_state()
    async def run(self):
        logger.info("Scout gestartet [PAPER] | JUPITER_API_KEY=%s", "gesetzt" if self.api_key else "fehlt")
        while True:
            try: await self.manage_positions(); await self.scan_entries(); await self.maybe_snapshot()
            except Exception: logger.exception("Scout loop error")
            await asyncio.sleep(30)
