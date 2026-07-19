"""On-Chain Memecoin-Momentum-Bot — Der Onchain.

Handelt Solana-Memecoins über öffentliche DexScreener-Marktdaten (kein Wallet,
kein API-Key, keine echte Order — reines Paper-Trading). Anders als die
Kraken-Bots gibt es hier kein Orderbuch mit Bid/Ask: DexScreener liefert nur
den aktuellen Pool-Preis (``priceUsd``). Fills simulieren deshalb einen
AMM-typischen Slippage-/Preis-Impact-Aufschlag statt eines echten Spreads.

Einstieg ist ein Momentum-Band: der Bot beobachtet die eigene, rollierende
Preis-Historie der letzten ``momentum_lookback_min`` Minuten (aus selbst
gesammelten Preis-Samples, da DexScreener keine öffentliche OHLC-Kerzen-API
hat) und steigt ein, sobald ein Coin zwischen ``entry_change_pct`` und
``entry_max_change_pct`` gestiegen ist — früh genug, um noch am Momentum zu
partizipieren, aber mit einer Obergrenze, um nicht in einen bereits
auslaufenden Pump zu kaufen. Ausstieg ist bewusst dreifach abgesichert: fester
Take-Profit (~15 %, realisiert den Gewinn statt ihn laufen zu lassen),
*zwingender* fester Stop-Loss und eine Max-Haltedauer — nur Take-Profit wäre
asymmetrisch, da Verluste sonst unbegrenzt liefen.
"""

import asyncio
import json
import logging
import math
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import paper_db as paper_db_module
from polybot.dca_strategy import fetch_ticker_data
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "CHAIN_"
BOT_KEY = "memecoin"

DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens"
# Solana ist ein Permissionless-Netzwerk: jeder kann einen Spam-/Klon-Token mit
# demselben Ticker anlegen (teils mit künstlich aufgeblasener Liquidität) — eine
# Suche nach dem Tickernamen ("WIF", "PNUT") trifft live nachweislich öfter den
# Klon als das Original. Deshalb wie bei Krakens PAIR_MAP feste, verifizierte
# Mint-Adressen statt einer Namenssuche (Adresse = Identität, nicht Ticker).
SYMBOL_TO_MINT = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "POPCAT": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
    "PNUT": "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
    "GOAT": "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
    "MEW": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
}
DEFAULT_SYMBOLS = list(SYMBOL_TO_MINT.keys())
# Kraken-Tickername für EUR/USD, um DexScreeners USD-Preise nach EUR
# umzurechnen (das Bot-Budget und alle anderen Bots rechnen in EUR).
EURUSD_PAIR = "EURUSD"
EURUSD_INTERNAL = "ZEURZUSD"
FALLBACK_EUR_USD_RATE = 1.08
# Auch von battle_report.py als Default für die Mark-to-Market-Bewertung
# offener Positionen wiederverwendet, damit beide Stellen nicht auseinanderlaufen.
DEFAULT_SLIPPAGE_PCT = 1.5


async def fetch_meme_pairs(symbols: list[str]) -> dict[str, dict]:
    """Holt je Symbol das liquideste Solana-Paar von DexScreener (public REST, kein Key).

    Fragt die feste Mint-Adresse aus ``SYMBOL_TO_MINT`` ab (ein Request für
    alle Symbole, Adressen kommagetrennt) statt nach dem Ticker zu suchen —
    die Adresse pinnt den exakten Token fest, unabhängig davon, wie viele
    gleichnamige Pools sonst noch existieren. Von den zurückgegebenen Pools für
    diese Mint-Adresse wird der mit der höchsten Liquidität gewählt (ein
    legitimer Token kann auf mehreren DEXes/Pools gleichzeitig handeln).
    """
    mints = {SYMBOL_TO_MINT[s]: s for s in symbols if s in SYMBOL_TO_MINT}
    if not mints:
        return {}
    result: dict[str, dict] = {}
    url = f"{DEXSCREENER_TOKENS_URL}/{','.join(mints.keys())}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"DexScreener fetch fehlgeschlagen: {e}")
            return {}
    best_by_symbol: dict[str, dict] = {}
    for p in data.get("pairs") or []:
        if p.get("chainId") != "solana":
            continue
        addr = p.get("baseToken", {}).get("address")
        symbol = mints.get(addr)
        if not symbol:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        current = best_by_symbol.get(symbol)
        if current is None or liq > float((current.get("liquidity") or {}).get("usd") or 0):
            best_by_symbol[symbol] = p
    result.update(best_by_symbol)
    return result


class MemecoinMomentumBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 300,
        momentum_lookback_min: float = 60.0,
        entry_change_pct: float = 8.0,
        entry_max_change_pct: float = 60.0,
        min_liquidity_usd: float = 50_000.0,
        position_eur: float = 8.0,
        max_open_positions: int = 3,
        take_profit_pct: float = 15.0,
        stop_loss_pct: float = 10.0,
        max_hold_sec: int = 24 * 3600,
        cooldown_sec: int = 4 * 3600,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        symbols: list[str] | None = None,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.momentum_lookback_min = float(momentum_lookback_min)
        self.entry_change_pct = float(entry_change_pct)
        self.entry_max_change_pct = float(entry_max_change_pct)
        self.min_liquidity_usd = float(min_liquidity_usd)
        self.position_eur = float(position_eur)
        self.max_open_positions = int(max_open_positions)
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.cooldown_sec = int(cooldown_sec)
        self.slippage_pct = float(slippage_pct)
        self.symbols = list(symbols) if symbols else list(DEFAULT_SYMBOLS)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        if not self.paper_mode:
            logger.warning("Memecoin live mode is intentionally not implemented")
            raise NotImplementedError("MemecoinMomentumBot is paper-only")

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "memecoin_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.price_history: dict[str, list[list[float]]] = {}
        self.cooldowns: dict[str, float] = {}
        self.last_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self._eur_usd_rate_cache = FALLBACK_EUR_USD_RATE
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "price_history": self.price_history,
            "cooldowns": self.cooldowns,
            "last_scan": self.last_scan,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
            "updated_at": time.time(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(self.state_path)

    def _load_state_or_rebuild(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, min(float(raw.get("capital_remaining", self.initial_capital_eur)), self.initial_capital_eur))
                self.portfolio = raw.get("portfolio") or {}
                self.price_history = raw.get("price_history") or {}
                self.cooldowns = {k: float(v) for k, v in (raw.get("cooldowns") or {}).items() if float(v) > time.time()}
                self.last_scan = float(raw.get("last_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                logger.info("♻️ Memecoin state geladen: cash=%.2f€, open=%d", self.capital_remaining, len(self.portfolio))
                return
            except Exception as e:
                logger.warning("Memecoin state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.price_history = {}
        self.trade_count = 0
        realized = 0.0
        open_cost = 0.0
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM paper_trades WHERE market_question LIKE ? ORDER BY id ASC", (f"{PREFIX}%",)).fetchall()
        finally:
            conn.close()
        for row in rows:
            symbol = str(row["market_question"]).removeprefix(PREFIX)
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            amount = size * price
            if size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += amount
                self.portfolio[symbol] = {
                    "shares": size,
                    "cost_basis": amount,
                    "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    "trade_id": int(row["id"]),
                }
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)
        logger.info("🧱 Memecoin rebuild: cash=%.2f€, open=%d, trades=%d", self.capital_remaining, len(self.portfolio), self.trade_count)

    def _update_history(self, symbol: str, ts: float, price_usd: float) -> None:
        hist = self.price_history.setdefault(symbol, [])
        hist.append([ts, price_usd])
        cutoff = ts - self.momentum_lookback_min * 60
        hist[:] = [p for p in hist if p[0] >= cutoff]

    def _momentum_change_pct(self, symbol: str) -> float | None:
        """% Änderung seit dem ältesten Preis-Sample im Momentum-Fenster.

        Verlangt Historie über mindestens die halbe Fensterbreite — kurz genug,
        damit der Bot früh auf einen frischen Pump aufspringen kann, aber nicht
        so kurz, dass ein einzelner verrauschter Preis-Tick schon als Momentum
        durchgeht.
        """
        hist = self.price_history.get(symbol) or []
        if len(hist) < 2:
            return None
        span = hist[-1][0] - hist[0][0]
        if span < self.momentum_lookback_min * 60 * 0.5:
            return None
        oldest_price = hist[0][1]
        if oldest_price <= 0:
            return None
        return (hist[-1][1] - oldest_price) / oldest_price * 100

    async def _get_eur_usd_rate(self) -> float:
        ticker = await fetch_ticker_data([EURUSD_PAIR])
        data = ticker.get(EURUSD_INTERNAL) or ticker.get(EURUSD_PAIR)
        try:
            if data:
                rate = float(data["c"][0])
                if rate > 0:
                    self._eur_usd_rate_cache = rate
        except Exception:
            pass
        return self._eur_usd_rate_cache

    async def manage_positions(self) -> list[dict]:
        if not self.portfolio:
            return []
        pairs = await fetch_meme_pairs(list(self.portfolio.keys()))
        rate = await self._get_eur_usd_rate()
        resolved = []
        now = time.time()
        for symbol, pos in list(self.portfolio.items()):
            pair = pairs.get(symbol)
            if not pair:
                logger.info("⏭️ CHAIN %s: kein DexScreener-Paar – Position unverändert", symbol)
                continue
            try:
                price_usd = float(pair["priceUsd"])
            except (KeyError, TypeError, ValueError):
                continue
            self._update_history(symbol, now, price_usd)
            price_eur = price_usd / rate
            entry = float(pos.get("entry_price") or 0.0)
            if entry <= 0:
                continue
            entry_ts = pos.get("entry_ts")
            age = now - float(entry_ts if entry_ts is not None else now)
            change_pct = (price_eur - entry) / entry * 100
            reason = None
            if change_pct >= self.take_profit_pct:
                reason = "take_profit"
            elif change_pct <= -self.stop_loss_pct:
                reason = "stop_loss"
            elif age >= self.max_hold_sec:
                reason = "time_exit"
            if not reason:
                continue
            shares = float(pos.get("shares") or 0.0)
            # Kein Orderbuch on-chain: Verkauf verschiebt den Pool-Preis, daher
            # Slippage/Preis-Impact statt eines echten Bid abziehen.
            exit_price = price_eur * (1 - self.slippage_pct / 100)
            entry_cost = shares * entry
            current_value = shares * exit_price
            real_pnl = current_value - entry_cost
            await resolve_trade(int(pos["trade_id"]), exit_price, round(real_pnl, 6))
            self.capital_remaining += entry_cost + real_pnl
            self.cooldowns[symbol] = now + self.cooldown_sec
            self.portfolio.pop(symbol, None)
            resolved.append({"symbol": symbol, "reason": reason, "pnl": real_pnl})
            logger.info("✅ CHAIN Exit %s: %s @ %.8f€ (Pool %.8f$) | PnL %+0.4f€", symbol, reason, exit_price, price_usd, real_pnl)
        if resolved:
            self._save_state()
        return resolved

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_scan < self.interval_sec:
            return []
        self.last_scan = now
        pairs = await fetch_meme_pairs(self.symbols)
        rate = await self._get_eur_usd_rate()
        candidates = []
        for symbol in self.symbols:
            if symbol in self.portfolio:
                logger.info("⏭️ CHAIN %s: bereits offen", symbol)
                continue
            if self.cooldowns.get(symbol, 0.0) > now:
                logger.info("⏭️ CHAIN %s: Cooldown aktiv", symbol)
                continue
            pair = pairs.get(symbol)
            if not pair:
                logger.info("⏭️ CHAIN %s: kein DexScreener-Paar", symbol)
                continue
            try:
                price_usd = float(pair["priceUsd"])
                liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
            except (TypeError, ValueError):
                continue
            if price_usd <= 0:
                continue
            self._update_history(symbol, now, price_usd)
            if liquidity_usd < self.min_liquidity_usd:
                logger.info("⏭️ CHAIN %s: Liquidität %.0f$ < %.0f$", symbol, liquidity_usd, self.min_liquidity_usd)
                continue
            change_pct = self._momentum_change_pct(symbol)
            if change_pct is None:
                logger.info("⏭️ CHAIN %s: noch nicht genug Historie für %.0fmin-Momentum", symbol, self.momentum_lookback_min)
                continue
            if not (self.entry_change_pct <= change_pct <= self.entry_max_change_pct):
                logger.info("⏭️ CHAIN %s: Momentum %+0.2f%% nicht in %.2f..%.2f%%", symbol, change_pct, self.entry_change_pct, self.entry_max_change_pct)
                continue
            # Score wie beim Momentum-Kraken-Bot: starke Bewegung mit echter
            # Liquidität schlägt starke Bewegung in einem dünnen Pool.
            score = change_pct * math.log10(max(liquidity_usd, 1.0))
            candidates.append((symbol, price_usd, score))
        candidates.sort(key=lambda t: t[2], reverse=True)
        opened = []
        for symbol, price_usd, _score in candidates:
            if len(self.portfolio) >= self.max_open_positions:
                break
            amount = min(self.position_eur, self.capital_remaining)
            if amount < 1.0:
                logger.info("⏭️ CHAIN: Cash %.2f€ reicht nicht", self.capital_remaining)
                break
            # Kauf-Slippage: AMM-Preisimpact macht den Fill teurer als der Quote-Preis.
            price_eur = (price_usd / rate) * (1 + self.slippage_pct / 100)
            shares = amount / price_eur
            trade_id = await log_paper_trade(f"{PREFIX}{symbol}", "buy", shares, price_eur, 0.0, "paper")
            self.capital_remaining -= amount
            self.portfolio[symbol] = {"shares": shares, "cost_basis": amount, "entry_price": price_eur, "entry_ts": now, "trade_id": trade_id}
            self.trade_count += 1
            opened.append({"symbol": symbol, "amount": amount, "price": price_eur})
            logger.info("📝 CHAIN Entry %s: %.2f€ @ %.8f€ (Pool %.8f$)", symbol, amount, price_eur, price_usd)
        self._save_state()
        return opened

    async def equity(self) -> dict:
        pairs = await fetch_meme_pairs(list(self.portfolio.keys())) if self.portfolio else {}
        rate = await self._get_eur_usd_rate()
        unrealized = 0.0
        mtm = 0.0
        for symbol, pos in self.portfolio.items():
            pair = pairs.get(symbol)
            entry_cost = float(pos["cost_basis"])
            if not pair:
                # Kein Live-Preis: konservativ zum Einstandswert bewerten statt
                # einen Fake-Drawdown/-Gewinn zu erzeugen.
                mtm += entry_cost
                continue
            try:
                price_usd = float(pair["priceUsd"])
            except (KeyError, TypeError, ValueError):
                mtm += entry_cost
                continue
            price_eur = price_usd / rate
            # Mark-to-Market simuliert den Verkauf, also inkl. Verkaufs-Slippage bewerten.
            current_value = float(pos["shares"]) * price_eur * (1 - self.slippage_pct / 100)
            mtm += current_value
            unrealized += current_value - entry_cost
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {"equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining, "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized}

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        e = await self.equity()
        await log_equity_snapshot(BOT_KEY, **e)
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 Memecoin-Onchain-Bot gestartet [PAPER] | Budget %.2f€", self.initial_capital_eur)
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception as e:
                logger.exception("⚠️ Memecoin-Loop-Fehler (%s) – weiter in 60s", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(60)
