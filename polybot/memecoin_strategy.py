"""On-Chain Memecoin-Momentum-Bot — Der Onchain.

Handelt Solana-Memecoins über öffentliche DexScreener-Marktdaten (kein Wallet,
kein API-Key, keine echte Order — reines Paper-Trading). Anders als die
Kraken-Bots gibt es hier kein Orderbuch mit Bid/Ask: DexScreener liefert nur
den aktuellen Pool-Preis (``priceUsd``). Fills simulieren deshalb zwei
getrennte Kosten statt eines echten Spreads: einen AMM-typischen
Slippage-/Preis-Impact-Aufschlag (``slippage_pct``) und zusätzlich die
mechanische Swap-Gebühr des DEX/der Bonding-Curve (``dex_fee_pct``, Default
~1 % wie bei pump.fun vor der Raydium-Migration) — beide ziehen bei einem
echten Trade unabhängig voneinander ab.

Universum ist hybrid: ein kuratierter Kern fest verifizierter Mint-Adressen
(``SYMBOL_TO_MINT``) plus optional dynamisch entdeckte Solana-Tokens aus
DexScreeners öffentlichen Boost-/Profile-Feeds (``discover_dynamic_solana_tokens``).
Diese Feeds sind bezahlte Promotion, keine organische Kennzahl — ein erster
Live-Tag zeigte, dass sie das Trading sonst komplett dominieren (5 von 6
Trades), deshalb gelten für dynamische Kandidaten eigene, strengere Gates
(``min_liquidity_dynamic_usd``, ``min_volume_dynamic_usd``, ein höheres
Mindestalter des Pools ``min_pair_age_hours`` und ein Positionslimit
``max_dynamic_positions``), während der kuratierte Kern ein eigenes, tieferes
Volumen-Gate (``min_volume_usd``) hat, das ihn nicht mehr aus dem eigenen
Universum aussperrt.

Einstieg ist ein Momentum-Band auf DexScreeners nativem ``priceChange.h1``
(kein selbst gepflegter Preis-Verlauf mehr nötig, DexScreener liefert die
Fenster m5/h1/h6/h24 direkt im Pair-Objekt): der Bot steigt ein, sobald ein
Coin zwischen ``entry_change_pct`` und ``entry_max_change_pct`` gestiegen ist
— früh genug, um noch am Momentum zu partizipieren, aber mit einer
Obergrenze, um nicht in einen bereits auslaufenden Pump zu kaufen. Zwei
Frische-Gates sichern das zusätzlich ab: ``priceChange.m5`` muss noch positiv
sein (Pump läuft gerade noch) und ``priceChange.h6`` darf ``max_h6_change_pct``
nicht überschreiten (kein Kauf am Ende eines Tages-Blowoffs). Das
Kaufdruck-Gate (h1 buys/sells-Verhältnis) verlangt zusätzlich eine
Mindest-Stichprobe (``min_h1_txns``), damit es nicht mit ein paar
Mini-Transaktionen leicht zu erfüllen ist.

Ausstieg ist ein Hybrid aus festem Floor und Trailing-Stop, nach demselben
``peak_price``-Muster wie beim Momentum-Bot (``momentum_strategy.py``):
erreicht eine Position ``take_profit_pct`` (~15 %), wird nicht sofort
verkauft, sondern in den Trailing-Modus geschaltet — der Exit-Preis ist dann
das Maximum aus einem festen Gewinn-Floor (``trail_floor_pct`` über Einstand)
und dem Hoch minus ``trailing_stop_pct``. So kann ein Gewinner weiterlaufen,
ohne dass der bereits erreichte Gewinn beim Rückfall verloren geht. *Vor*
Erreichen der Take-Profit-Schwelle bleibt ein *zwingender* fester Stop-Loss
und eine Max-Haltedauer als harte Sicherheitsnetze aktiv — nur ein
Take-Profit/Trailing wäre asymmetrisch, da Verluste sonst unbegrenzt liefen.
Ein Exit über Stop-Loss löst zusätzlich einen längeren Cooldown aus
(``cooldown_after_stop_sec``) als ein Exit über Take-Profit/Trailing/Zeit
(``cooldown_sec``) — verhindert Revenge-Trading auf einem Coin, der gerade
gegen den Bot gelaufen ist.

Intern wird nach Mint-Adresse geschlüsselt, nicht nach Ticker: zwei dynamisch
entdeckte Tokens können denselben Namen tragen (Solana ist permissionless).
``market_question`` kodiert deshalb beides — ``CHAIN_{symbol}@{address}`` —
Symbol fürs Dashboard, Adresse für die eindeutige Preis-Auflösung.
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
DEXSCREENER_BOOSTS_TOP_URL = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_BOOSTS_LATEST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_PROFILES_LATEST_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
# DexScreener erlaubt bis zu ~30 kommagetrennte Adressen pro /tokens/-Call.
TOKENS_BATCH_SIZE = 30

# Solana ist ein Permissionless-Netzwerk: jeder kann einen Spam-/Klon-Token mit
# demselben Ticker anlegen (teils mit künstlich aufgeblasener Liquidität) — eine
# Suche nach dem Tickernamen ("WIF", "PNUT", auch "ai16z" — beobachtet als
# Homoglyph-Klon mit griechischen Buchstaben und höherer Fake-Liquidität als das
# Original) trifft live nachweislich öfter den Klon als das Original. Deshalb
# wie bei Krakens PAIR_MAP feste, einzeln live verifizierte Mint-Adressen statt
# einer Namenssuche (Adresse = Identität, nicht Ticker). Jede Adresse hier wurde
# einzeln gegen die /tokens/-API geprüft (Symbol+Name+Liquidität passen).
SYMBOL_TO_MINT = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "POPCAT": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
    "PNUT": "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
    "GOAT": "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
    "MEW": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
    "FARTCOIN": "3srC8ksB2EiJynMGfk72mDk7joF56Aqz3NjwQEyEki7c",
    "GIGA": "63LfDmNb3MQ8mw9MtZ2To9bEA2M71kZUUGq5tiJxcqj9",
    "MOODENG": "ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzPJBY",
    "FWOG": "A8C3xuqscfmyLrte3VmTqrAq8kgMASius9AFNANwpump",
    "PENGU": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
    "SLERF": "7BgBvyjrZX1YKz4oh9mjb8ZScatkkwb8DzFx7LoiVkM3",
}
MINT_TO_SYMBOL = {addr: sym for sym, addr in SYMBOL_TO_MINT.items()}
DEFAULT_CURATED_ADDRESSES = list(SYMBOL_TO_MINT.values())
# Kraken-Tickername für EUR/USD, um DexScreeners USD-Preise nach EUR
# umzurechnen (das Bot-Budget und alle anderen Bots rechnen in EUR).
EURUSD_PAIR = "EURUSD"
EURUSD_INTERNAL = "ZEURZUSD"
FALLBACK_EUR_USD_RATE = 1.08
# Auch von battle_report.py als Default für die Mark-to-Market-Bewertung
# offener Positionen wiederverwendet, damit beide Stellen nicht auseinanderlaufen.
DEFAULT_SLIPPAGE_PCT = 1.5
# Mechanische Swap-Gebühr des DEX/der Bonding-Curve (z.B. pump.fun ~1%,
# Raydium-AMM-Pools eher ~0.25%) — unabhängig vom Preis-Impact/Slippage oben,
# der die Bewegung des Pool-Preises durch den eigenen Trade abbildet. Beides
# zusammen zieht real ab; hier bewusst konservativ mit dem höheren
# Bonding-Curve-Satz als Default, da ein Großteil frischer Memecoin-Pumps
# noch vor der Raydium-Migration läuft.
DEFAULT_DEX_FEE_PCT = 1.0


async def fetch_pairs_by_address(addresses: list[str]) -> dict[str, dict]:
    """Holt je Mint-Adresse das liquideste Solana-Paar von DexScreener (public REST, kein Key).

    Schlüsselt nach Adresse statt Ticker — die Adresse pinnt den exakten Token
    fest, unabhängig davon, wie viele gleichnamige Pools sonst noch existieren.
    Batcht in Gruppen von ``TOKENS_BATCH_SIZE`` (DexScreener-Limit pro Request).
    Von den zurückgegebenen Pools je Adresse wird der mit der höchsten
    Liquidität gewählt (ein Token kann auf mehreren DEXes/Pools gleichzeitig
    handeln).
    """
    seen: set[str] = set()
    unique_addresses = [a for a in addresses if a and not (a in seen or seen.add(a))]
    if not unique_addresses:
        return {}
    result: dict[str, dict] = {}
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(unique_addresses), TOKENS_BATCH_SIZE):
            batch = unique_addresses[i:i + TOKENS_BATCH_SIZE]
            batch_set = set(batch)
            url = f"{DEXSCREENER_TOKENS_URL}/{','.join(batch)}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.error(f"DexScreener /tokens/ fehlgeschlagen: {e}")
                continue
            for p in data.get("pairs") or []:
                if p.get("chainId") != "solana":
                    continue
                addr = p.get("baseToken", {}).get("address")
                if addr not in batch_set:
                    continue
                liq = float((p.get("liquidity") or {}).get("usd") or 0)
                current = result.get(addr)
                if current is None or liq > float((current.get("liquidity") or {}).get("usd") or 0):
                    result[addr] = p
    return result


async def discover_dynamic_solana_tokens(max_tokens: int = 15) -> list[str]:
    """Entdeckt aktuell beworbene Solana-Token-Adressen über DexScreeners
    öffentliche Boost-/Profile-Feeds (kein Key nötig).

    Diese Feeds spiegeln bezahlte Promotion wider, nicht organisches
    Handelsvolumen — sie sind deshalb bewusst nur eine Kandidaten-Quelle für
    ``scan_entries()``, das anschließend hart nach Liquidität/Volumen/
    Kaufdruck/Mindestalter filtert. Ein Ausfall (Netzwerkfehler, leere
    Antwort, unerwartetes Format) liefert ``[]`` und darf den kuratierten
    Kern niemals blockieren.
    """
    addresses: list[str] = []
    seen: set[str] = set()
    async with aiohttp.ClientSession() as session:
        for url in (DEXSCREENER_BOOSTS_TOP_URL, DEXSCREENER_BOOSTS_LATEST_URL, DEXSCREENER_PROFILES_LATEST_URL):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.warning(f"Discovery-Feed nicht erreichbar ({url}): {e}")
                continue
            if not isinstance(data, list):
                continue
            for entry in data:
                if not isinstance(entry, dict) or entry.get("chainId") != "solana":
                    continue
                addr = entry.get("tokenAddress")
                if not addr or addr in seen:
                    continue
                seen.add(addr)
                addresses.append(addr)
    return addresses[:max_tokens]


def _sanitize_symbol(raw: str) -> str:
    """Nur Alphanumerisch, upper, gekappt — verhindert, dass ein böswillig
    benannter Dynamik-Token Sonderzeichen (inkl. ``@``) in ``market_question``
    einschleust."""
    cleaned = "".join(ch for ch in raw if ch.isalnum())[:12].upper()
    return cleaned


class MemecoinMomentumBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 300,
        entry_change_pct: float = 8.0,
        entry_max_change_pct: float = 35.0,
        max_m5_change_pct: float = 4.0,
        max_h6_change_pct: float = 100.0,
        min_liquidity_usd: float = 50_000.0,
        min_liquidity_dynamic_usd: float = 100_000.0,
        min_volume_usd: float = 100_000.0,
        min_volume_dynamic_usd: float = 500_000.0,
        min_buy_sell_ratio: float = 1.2,
        min_h1_txns: int = 50,
        dynamic_enabled: bool = True,
        max_dynamic_tokens: int = 15,
        max_dynamic_positions: int = 2,
        min_pair_age_hours: float = 24.0,
        position_eur: float = 8.0,
        max_open_positions: int = 3,
        take_profit_pct: float = 15.0,
        trailing_stop_pct: float = 12.0,
        trail_floor_pct: float = 5.0,
        stop_loss_pct: float = 10.0,
        max_hold_sec: int = 24 * 3600,
        cooldown_sec: int = 4 * 3600,
        cooldown_after_stop_sec: int = 24 * 3600,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        dex_fee_pct: float = DEFAULT_DEX_FEE_PCT,
        curated_addresses: list[str] | None = None,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.entry_change_pct = float(entry_change_pct)
        self.entry_max_change_pct = float(entry_max_change_pct)
        self.max_m5_change_pct = float(max_m5_change_pct)
        self.max_h6_change_pct = float(max_h6_change_pct)
        self.min_liquidity_usd = float(min_liquidity_usd)
        self.min_liquidity_dynamic_usd = float(min_liquidity_dynamic_usd)
        self.min_volume_usd = float(min_volume_usd)
        self.min_volume_dynamic_usd = float(min_volume_dynamic_usd)
        self.min_buy_sell_ratio = float(min_buy_sell_ratio)
        self.min_h1_txns = int(min_h1_txns)
        self.dynamic_enabled = bool(dynamic_enabled)
        self.max_dynamic_tokens = int(max_dynamic_tokens)
        self.max_dynamic_positions = int(max_dynamic_positions)
        self.min_pair_age_hours = float(min_pair_age_hours)
        self.position_eur = float(position_eur)
        self.max_open_positions = int(max_open_positions)
        self.take_profit_pct = float(take_profit_pct)
        self.trailing_stop_pct = float(trailing_stop_pct)
        self.trail_floor_pct = float(trail_floor_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.cooldown_sec = int(cooldown_sec)
        self.cooldown_after_stop_sec = int(cooldown_after_stop_sec)
        self.slippage_pct = float(slippage_pct)
        self.dex_fee_pct = float(dex_fee_pct)
        self.curated_addresses = list(curated_addresses) if curated_addresses else list(DEFAULT_CURATED_ADDRESSES)
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
                self.capital_remaining = max(0.0, float(raw.get("capital_remaining", self.initial_capital_eur)))
                self.portfolio = raw.get("portfolio") or {}
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
            rest = str(row["market_question"]).removeprefix(PREFIX)
            if "@" not in rest:
                logger.warning("CHAIN-Trade mit unerwartetem market_question übersprungen: %s", row["market_question"])
                continue
            symbol, _, address = rest.partition("@")
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            amount = size * price
            if size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += amount
                self.portfolio[address] = {
                    "symbol": symbol,
                    "shares": size,
                    "cost_basis": amount,
                    "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    # Nach einem Rebuild ist die tatsächliche Preisspitze seit Entry
                    # unbekannt; konservativ mit dem Entry-Preis initialisieren statt
                    # einen fiktiven Trailing-Vorteil anzunehmen.
                    "peak_price": price,
                    "trailing_active": False,
                    "trade_id": int(row["id"]),
                }
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)
        logger.info("🧱 Memecoin rebuild: cash=%.2f€, open=%d, trades=%d", self.capital_remaining, len(self.portfolio), self.trade_count)

    @staticmethod
    def _display_symbol(address: str, pair: dict | None) -> str:
        """Menschlich lesbares Kürzel fürs Dashboard: bekannter Ticker aus
        ``SYMBOL_TO_MINT`` für den kuratierten Kern, sonst das (sanitisierte)
        Base-Token-Symbol aus DexScreener für dynamische Tokens, sonst als
        letzter Ausweg ein Adress-Fragment."""
        known = MINT_TO_SYMBOL.get(address)
        if known:
            return known
        if pair:
            cleaned = _sanitize_symbol(str(pair.get("baseToken", {}).get("symbol") or ""))
            if cleaned:
                return cleaned
        return address[:6].upper()

    @staticmethod
    def _pair_age_hours(pair: dict, now: float) -> float | None:
        try:
            created_ms = float(pair.get("pairCreatedAt"))
        except (TypeError, ValueError):
            return None
        if created_ms <= 0:
            return None
        return (now - created_ms / 1000.0) / 3600.0

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
        pairs = await fetch_pairs_by_address(list(self.portfolio.keys()))
        rate = await self._get_eur_usd_rate()
        resolved = []
        now = time.time()
        for address, pos in list(self.portfolio.items()):
            symbol = pos.get("symbol") or address[:6].upper()
            pair = pairs.get(address)
            if not pair:
                logger.info("⏭️ CHAIN %s: kein DexScreener-Paar – Position unverändert", symbol)
                continue
            try:
                price_usd = float(pair["priceUsd"])
            except (KeyError, TypeError, ValueError):
                continue
            price_eur = price_usd / rate
            entry = float(pos.get("entry_price") or 0.0)
            if entry <= 0:
                continue
            # peak_price fehlt bei Positionen aus einem DB-Rebuild vor diesem
            # Feature nie (Rebuild setzt ihn auf entry) — der Fallback greift
            # trotzdem defensiv für Alt-State aus vor dieser Änderung.
            peak = max(float(pos.get("peak_price") or entry), price_eur)
            pos["peak_price"] = peak
            trailing_active = bool(pos.get("trailing_active", False))
            entry_ts = pos.get("entry_ts")
            age = now - float(entry_ts if entry_ts is not None else now)
            change_pct = (price_eur - entry) / entry * 100
            reason = None
            # Stop-Loss und Max-Haltedauer sind harte Sicherheitsnetze und gelten
            # unabhängig vom Trailing-Modus. Erst danach: falls schon im Trailing
            # (Take-Profit-Schwelle wurde bereits erreicht), Exit am Floor/Trail;
            # sonst prüfen, ob die Take-Profit-Schwelle jetzt den Trailing-Modus
            # aktiviert (das ist noch kein Exit in diesem Zyklus).
            if change_pct <= -self.stop_loss_pct:
                reason = "stop_loss"
            elif age >= self.max_hold_sec:
                reason = "time_exit"
            elif trailing_active:
                floor_price = entry * (1 + self.trail_floor_pct / 100)
                trail_price = peak * (1 - self.trailing_stop_pct / 100)
                stop_price = max(floor_price, trail_price)
                if price_eur <= stop_price:
                    reason = "trailing_stop"
            elif change_pct >= self.take_profit_pct:
                pos["trailing_active"] = True
                logger.info("🎯 CHAIN %s: Take-Profit-Schwelle erreicht (%+0.2f%%) – Trailing-Modus aktiv", symbol, change_pct)
            if not reason:
                continue
            shares = float(pos.get("shares") or 0.0)
            # Kein Orderbuch on-chain: Verkauf verschiebt den Pool-Preis, daher
            # Slippage/Preis-Impact statt eines echten Bid abziehen. Zusätzlich
            # die mechanische DEX-/Bonding-Curve-Gebühr, unabhängig vom Impact.
            exit_price = price_eur * (1 - self.slippage_pct / 100) * (1 - self.dex_fee_pct / 100)
            entry_cost = shares * entry
            current_value = shares * exit_price
            real_pnl = current_value - entry_cost
            await resolve_trade(int(pos["trade_id"]), exit_price, round(real_pnl, 6))
            self.capital_remaining += entry_cost + real_pnl
            cooldown_len = self.cooldown_after_stop_sec if reason == "stop_loss" else self.cooldown_sec
            self.cooldowns[address] = now + cooldown_len
            self.portfolio.pop(address, None)
            resolved.append({"symbol": symbol, "address": address, "reason": reason, "pnl": real_pnl})
            logger.info("✅ CHAIN Exit %s: %s @ %.8f€ (Pool %.8f$) | PnL %+0.4f€", symbol, reason, exit_price, price_usd, real_pnl)
        if resolved:
            self._save_state()
        return resolved

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_scan < self.interval_sec:
            return []
        self.last_scan = now

        curated_set = set(self.curated_addresses)
        universe = list(self.curated_addresses)
        if self.dynamic_enabled:
            try:
                dynamic = await discover_dynamic_solana_tokens(self.max_dynamic_tokens)
            except Exception as e:
                logger.warning("⚠️ Dynamische Discovery fehlgeschlagen (%s) – nur kuratierter Kern", e)
                dynamic = []
            for addr in dynamic:
                if addr not in universe:
                    universe.append(addr)

        pairs = await fetch_pairs_by_address(universe)
        rate = await self._get_eur_usd_rate()
        # Dynamisch = nicht im kuratierten Kern, unabhängig davon, ob die
        # Discovery diese Runde den Token erneut gemeldet hat — so zählt eine
        # offene dynamische Position auch dann fürs Limit, wenn sie gerade
        # nicht mehr in den Boost-Feeds auftaucht.
        open_dynamic_count = sum(1 for addr in self.portfolio if addr not in curated_set)
        candidates = []
        for address in universe:
            if address in self.portfolio:
                continue
            if self.cooldowns.get(address, 0.0) > now:
                continue
            pair = pairs.get(address)
            if not pair:
                continue
            is_dynamic = address not in curated_set
            try:
                price_usd = float(pair["priceUsd"])
                liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
                volume_h24 = float((pair.get("volume") or {}).get("h24") or 0)
                volume_h1 = float((pair.get("volume") or {}).get("h1") or 0)
                change = pair.get("priceChange") or {}
                change_h1 = float(change["h1"]) if change.get("h1") is not None else None
                change_m5 = float(change["m5"]) if change.get("m5") is not None else None
                change_h6 = float(change["h6"]) if change.get("h6") is not None else None
                h1_txns = (pair.get("txns") or {}).get("h1") or {}
                buys = float(h1_txns.get("buys") or 0)
                sells = float(h1_txns.get("sells") or 0)
            except (TypeError, ValueError):
                continue
            if price_usd <= 0:
                continue
            symbol = self._display_symbol(address, pair)
            min_liq = self.min_liquidity_dynamic_usd if is_dynamic else self.min_liquidity_usd
            if liquidity_usd < min_liq:
                logger.info("⏭️ CHAIN %s: Liquidität %.0f$ < %.0f$", symbol, liquidity_usd, min_liq)
                continue
            min_vol = self.min_volume_dynamic_usd if is_dynamic else self.min_volume_usd
            if volume_h24 < min_vol:
                logger.info("⏭️ CHAIN %s: 24h-Volumen %.0f$ < %.0f$", symbol, volume_h24, min_vol)
                continue
            if (buys + sells) < self.min_h1_txns:
                logger.info("⏭️ CHAIN %s: nur %d h1-Txns – zu wenig für belastbaren Kaufdruck (min %d)", symbol, int(buys + sells), self.min_h1_txns)
                continue
            buy_sell_ratio = buys / max(sells, 1.0)
            if buy_sell_ratio < self.min_buy_sell_ratio:
                logger.info("⏭️ CHAIN %s: Kaufdruck %.2f < %.2f (h1 buys/sells)", symbol, buy_sell_ratio, self.min_buy_sell_ratio)
                continue
            if change_h1 is None:
                logger.info("⏭️ CHAIN %s: kein priceChange.h1 von DexScreener", symbol)
                continue
            if not (self.entry_change_pct <= change_h1 <= self.entry_max_change_pct):
                logger.info("⏭️ CHAIN %s: Momentum %+0.2f%% (h1) nicht in %.2f..%.2f%%", symbol, change_h1, self.entry_change_pct, self.entry_max_change_pct)
                continue
            if change_m5 is not None and not (0 < change_m5 <= self.max_m5_change_pct):
                logger.info("⏭️ CHAIN %s: m5-Momentum %+0.2f%% nicht im Reclaim-Band 0..%.2f%%", symbol, change_m5, self.max_m5_change_pct)
                continue
            if change_h6 is not None and change_h6 >= self.max_h6_change_pct:
                logger.info("⏭️ CHAIN %s: h6-Bewegung %+0.2f%% >= %.0f%% – möglicher Tages-Blowoff", symbol, change_h6, self.max_h6_change_pct)
                continue
            if is_dynamic:
                # Dynamisch entdeckte Tokens: Mindestalter gegen frische Rug-Bait-Launches.
                age_hours = self._pair_age_hours(pair, now)
                if age_hours is None or age_hours < self.min_pair_age_hours:
                    logger.info("⏭️ CHAIN %s: Pool-Alter unbekannt/zu jung (dynamisch, Mindestalter %.1fh)", symbol, self.min_pair_age_hours)
                    continue
            # Score volumen-gewichtet auf frischem h1-Volumen: starke Bewegung mit
            # echtem aktuellem Handelsvolumen schlägt starke Bewegung in einem
            # gerade kaum gehandelten Pool.
            score = change_h1 * math.log10(max(volume_h1, 1.0))
            candidates.append((address, symbol, price_usd, score, is_dynamic))
        candidates.sort(key=lambda t: t[3], reverse=True)
        opened = []
        dynamic_opened = 0
        for address, symbol, price_usd, _score, is_dynamic in candidates:
            if len(self.portfolio) >= self.max_open_positions:
                break
            if is_dynamic and (open_dynamic_count + dynamic_opened) >= self.max_dynamic_positions:
                logger.info("⏭️ CHAIN %s: dynamisches Positionslimit erreicht (%d)", symbol, self.max_dynamic_positions)
                continue
            amount = min(self.position_eur, self.capital_remaining)
            if amount < 1.0:
                logger.info("⏭️ CHAIN: Cash %.2f€ reicht nicht", self.capital_remaining)
                break
            # Kauf-Slippage (AMM-Preisimpact) plus mechanische DEX-Gebühr machen
            # den Fill teurer als der reine Quote-Preis.
            price_eur = (price_usd / rate) * (1 + self.slippage_pct / 100) * (1 + self.dex_fee_pct / 100)
            shares = amount / price_eur
            market_question = f"{PREFIX}{symbol}@{address}"
            trade_id = await log_paper_trade(market_question, "buy", shares, price_eur, 0.0, "paper")
            self.capital_remaining -= amount
            self.portfolio[address] = {
                "symbol": symbol, "shares": shares, "cost_basis": amount,
                "entry_price": price_eur, "entry_ts": now,
                "peak_price": price_eur, "trailing_active": False,
                "trade_id": trade_id,
            }
            if is_dynamic:
                dynamic_opened += 1
            self.trade_count += 1
            opened.append({"symbol": symbol, "address": address, "amount": amount, "price": price_eur})
            logger.info("📝 CHAIN Entry %s: %.2f€ @ %.8f€ (Pool %.8f$)", symbol, amount, price_eur, price_usd)
        self._save_state()
        return opened

    async def equity(self) -> dict:
        pairs = await fetch_pairs_by_address(list(self.portfolio.keys())) if self.portfolio else {}
        rate = await self._get_eur_usd_rate()
        unrealized = 0.0
        mtm = 0.0
        for address, pos in self.portfolio.items():
            pair = pairs.get(address)
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
            # Mark-to-Market simuliert den Verkauf, also inkl. Verkaufs-Slippage und DEX-Gebühr bewerten.
            current_value = float(pos["shares"]) * price_eur * (1 - self.slippage_pct / 100) * (1 - self.dex_fee_pct / 100)
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
