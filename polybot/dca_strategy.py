"""
DCA-Bot für Kraken: Wählt automatisch die profitabelsten EUR-Paare aus,
kauft in festen Intervallen (Dollar-Cost-Averaging) und loggt alles in die Paper-DB.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

import aiohttp

from polybot import paper_db as paper_db_module
from polybot.paper_db import resolve_trade, get_open_dca_trades

logger = logging.getLogger(__name__)

KRAKEN_PUBLIC = "https://api.kraken.com/0/public"

# EUR-Paare die Kraken anbietet (erweiterbar)
CANDIDATE_PAIRS = [
    "XBTEUR", "ETHEUR", "SOLEUR", "XRPEUR", "ADAEUR",
    "DOTEUR", "AVAXEUR", "LINKEUR", "MATICEUR", "LTCEUR",
    "UNIEUR", "ATOMEUR", "NEAREUR", "APTEUR", "SUIEUR",
    "DOGEEUR", "SHIBEUR", "TRXEUR", "XLMEUR", "FILEUR",
    # zusätzliche volatile/meme-lastige Kandidaten (falls auf Kraken verfügbar)
    "PEPEEUR", "BONKEUR", "WIFEUR", "FLOKIEUR", "JTOEUR",
]

# Kraken-interne Ticker-Namen (für Paare die abweichen)
PAIR_MAP = {
    "XBTEUR": "XXBTZEUR",
    "ETHEUR": "XETHZEUR",
    "LTCEUR": "XLTCZEUR",
    "XRPEUR": "XXRPZEUR",
    "XLMEUR": "XXLMZEUR",
}


async def fetch_ticker_data(pairs: list[str]) -> dict:
    """
    Holt 24h-Ticker-Daten von Kraken. Fragt in Batches von 10 ab
    um 'Unknown asset pair' Fehler für ungültige Paare zu vermeiden.
    Gibt ein dict zurück das BEIDE Schreibweisen (Original + Intern) enthält.
    """
    result: dict = {}
    batch_size = 10
    # Eine Session für alle Batches wiederverwenden (spart Verbindungsaufbau).
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            url = f"{KRAKEN_PUBLIC}/Ticker?pair={','.join(batch)}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    result.update(data.get("result", {}))
            except Exception as e:
                logger.error(f"Ticker fetch fehlgeschlagen: {e}")
    return result


# Kraken liefert im Ticker 'a' (Ask) und 'b' (Bid) mit. Käufe füllen sich zum Ask,
# Verkäufe zum Bid – das ist der reale Spread statt einer erfundenen Slippage-
# Konstante. Bei den Positionsgrößen dieser Bots (~8-12€) füllt sich eine Order
# komplett am Top of Book, deshalb IST Ask/Bid der Fill und keine Näherung.
# Wichtig: Signale (change_pct, Trailing-Peak, Exit-Trigger) rechnen weiter mit
# 'c' (Last) – nur die Fills nehmen Ask/Bid. Sonst würde der Spread die
# Strategie-Entscheidungen verschieben statt nur ihre Ausführung.
def extract_quote(data: dict, last_price: float) -> tuple[float, float]:
    """Bid/Ask aus einem Kraken-Ticker-Eintrag; (bid, ask).

    Fällt auf ``last_price`` zurück, wenn Kraken keine brauchbare Quote liefert
    (fehlend, <= 0 oder verdreht). Der Fill entspricht dann dem alten Verhalten
    ohne Spread – lieber ein zu optimistischer Fill als ein toter Bot.
    """
    try:
        ask = float(data["a"][0])
        bid = float(data["b"][0])
    except (KeyError, ValueError, IndexError, TypeError):
        return last_price, last_price
    if bid <= 0 or ask <= 0 or ask < bid:
        logger.debug("Unbrauchbare Quote (bid=%s ask=%s) – Fallback auf Last", bid, ask)
        return last_price, last_price
    return bid, ask


# Kraken-Ticker 'o' ist der Tages-Open (00:00 UTC) und springt um Mitternacht
# auf ~0 zurück – daraus lässt sich KEINE echte 24h-Bewegung ableiten. Für
# Einstiegsentscheidungen brauchen alle Bots die tatsächliche 24h-Änderung; die
# kommt aus stündlichen OHLC-Kerzen und wird kurz gecacht, um das Rate-Limit zu
# schonen (der Cache wird von allen drei Bots gemeinsam genutzt).
ROLLING_24H_TTL_SEC = 900  # 15 Min
_rolling_change_cache: dict[str, tuple[float, float]] = {}  # pair -> (fetched_at, change_pct)


async def _fetch_ohlc_closes(pair: str, interval_min: int = 60) -> list[float]:
    """Holt OHLC-Kerzen von Kraken und gibt die Close-Preise chronologisch zurück."""
    internal = PAIR_MAP.get(pair, pair)
    url = f"{KRAKEN_PUBLIC}/OHLC?pair={internal}&interval={int(interval_min)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.warning("OHLC fetch %s fehlgeschlagen: %s", pair, e)
        return []
    result = data.get("result", {})
    for key, rows in result.items():
        if key != "last" and isinstance(rows, list):
            closes = []
            for r in rows:
                try:
                    closes.append(float(r[4]))
                except (ValueError, IndexError, TypeError):
                    continue
            return closes
    return []


async def rolling_24h_change_pct(pair: str, ttl_sec: int = ROLLING_24H_TTL_SEC) -> float | None:
    """Echte rollierende 24h-Kursänderung in % (aus 60m-OHLC), gecacht.

    Gibt None zurück wenn keine ausreichenden OHLC-Daten vorliegen – Aufrufer
    sollen dann konservativ handeln (z.B. Einstieg überspringen).
    """
    now = time.time()
    cached = _rolling_change_cache.get(pair)
    if cached and (now - cached[0]) < ttl_sec:
        return cached[1]
    closes = await _fetch_ohlc_closes(pair, 60)
    if len(closes) < 2:
        return None
    last_close = closes[-1]
    # Kerze ~24h zurück (24 Stunden-Bars); Fallback auf älteste vorhandene.
    ref_close = closes[max(0, len(closes) - 1 - 24)]
    if ref_close <= 0:
        return None
    change = (last_close - ref_close) / ref_close * 100
    _rolling_change_cache[pair] = (now, change)
    return change


async def rank_pairs_by_opportunity(candidates: list[str], top_n: int = 5) -> list[dict]:
    """
    Bewertet Paare nach einem kombinierten Score:
    - Momentum: |24h-Kursveränderung| (Bewegungsstärke)
    - Volatilität: (High - Low) / Open (Intraday-Spanne = DCA-Chance)
    - Liquidität: log(Volume_EUR) als Multiplikator (verhindert BTC-Dominanz)
    - Malus: Wenn change_pct > +15% → überhitzt, halber Score
    """
    import math
    ticker = await fetch_ticker_data(candidates)
    if not ticker:
        logger.warning("Kein Ticker-Daten – nutze Fallback-Paare")
        return [{"pair": p, "score": 0, "change_pct": 0, "volume_eur": 0} for p in candidates[:top_n]]

    ranked = []
    for pair in candidates:
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        if not data:
            continue
        try:
            open_price  = float(data["o"])
            last_price  = float(data["c"][0])
            high_price  = float(data["h"][1])   # 24h high
            low_price   = float(data["l"][1])   # 24h low
            volume_coin = float(data["v"][1])
            vwap        = float(data["p"][1])

            if open_price <= 0 or volume_coin <= 0:
                continue

            # Echte 24h-Bewegung (nicht Tages-Open); Fallback auf Ticker-Open.
            rolling = await rolling_24h_change_pct(pair)
            change_pct   = rolling if rolling is not None else (last_price - open_price) / open_price * 100
            volatility   = (high_price - low_price) / open_price * 100  # Intraday-Spanne
            volume_eur   = volume_coin * vwap
            liq_factor   = math.log10(max(volume_eur, 1))  # log skaliert Liquidität

            # Kombinierter Score
            momentum     = abs(change_pct)
            score        = (momentum * 0.5 + volatility * 0.5) * liq_factor

            # Malus für überhitzte Coins (>15% in 24h)
            if change_pct > 15:
                score *= 0.4

            bid, ask = extract_quote(data, last_price)
            ranked.append({
                "pair": pair,
                "last_price": last_price,
                "bid": bid,
                "ask": ask,
                "change_pct": round(change_pct, 2),
                "volatility_pct": round(volatility, 2),
                "volume_eur": round(volume_eur, 0),
                "score": round(score, 2),
            })
        except (KeyError, ValueError, IndexError) as e:
            logger.debug(f"Parse-Fehler für {pair}: {e}")
            continue

    ranked.sort(key=lambda x: x["score"], reverse=True)
    logger.info("📊 Coin-Ranking: " + " | ".join(
        f"{r['pair']}({r['change_pct']:+.1f}% vol={r['volatility_pct']:.1f}%)" for r in ranked[:6]
    ))
    return ranked[:top_n]


class DCABot:
    """
    Dollar-Cost-Averaging Bot für Kraken.

    Startkapital: initial_capital_eur (Standard 100€).
    Pro Runde wird ein kleiner fixer Betrag investiert bis das Kapital erschöpft ist.
    Danach nur noch Positionen halten + PnL tracken (kein Nachkauf).
    """

    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 4 * 3600,
        top_n: int = 3,
        paper_mode: bool = True,
        rescan_interval: int = 24 * 3600,
        rounds_target: int = 10,          # Kapital auf N Runden verteilen
        min_edge_pct: float = 1.0,
        negative_streak_limit: int = 3,
        coin_cooldown_sec: int = 12 * 3600,
        rolling_window: int = 9,
        rolling_loss_limit: float = -0.30,
        risk_off_sec: int = 8 * 3600,
        take_profit_pct: float = 0.04,
        stop_loss_pct: float = 0.03,
        max_hold_sec: int = 7 * 24 * 3600,
        min_net_profit_eur: float = 0.0,
        max_open_positions: int = 3,
        max_pair_exposure_eur: float = 35.0,
        min_cash_reserve_eur: float = 20.0,
        trend_filter_enabled: bool = True,
        btc_risk_off_pct: float = -2.0,
        eth_risk_off_pct: float = -3.0,
        recovery_trigger_pct: float = -5.0,
        recovery_reversal_pct: float = 0.8,
        recovery_ticket_eur: float = 5.0,
        recovery_max_exposure_factor: float = 1.5,
    ):
        self.initial_capital_eur = initial_capital_eur
        self.capital_remaining = initial_capital_eur   # verbleibendes Kapital
        self.interval_sec = interval_sec
        self.top_n = top_n
        self.paper_mode = bool(paper_mode)
        if not self.paper_mode:
            logger.warning("DCA live mode is intentionally not implemented")
            raise NotImplementedError("DCABot is paper-only")
        self.rescan_interval = rescan_interval
        # Pro Runde max. capital / rounds_target, mindestens 1€ pro Coin
        self.per_round_eur = round(initial_capital_eur / rounds_target, 2)

        self.min_edge_pct = max(0.0, float(min_edge_pct))
        self.negative_streak_limit = max(2, int(negative_streak_limit))
        self.coin_cooldown_sec = max(300, int(coin_cooldown_sec))
        self.rolling_window = max(3, int(rolling_window))
        self.rolling_loss_limit = float(rolling_loss_limit)
        self.risk_off_sec = max(300, int(risk_off_sec))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.max_hold_sec = max(0, int(max_hold_sec))
        self.min_net_profit_eur = max(0.0, float(min_net_profit_eur))
        self.max_open_positions = max(1, int(max_open_positions))
        self.max_pair_exposure_eur = max(1.0, float(max_pair_exposure_eur))
        self.min_cash_reserve_eur = max(0.0, min(float(min_cash_reserve_eur), float(initial_capital_eur)))
        self.trend_filter_enabled = bool(trend_filter_enabled)
        self.btc_risk_off_pct = float(btc_risk_off_pct)
        self.eth_risk_off_pct = float(eth_risk_off_pct)
        self.recovery_trigger_pct = float(recovery_trigger_pct)
        self.recovery_reversal_pct = float(recovery_reversal_pct)
        self.recovery_ticket_eur = max(0.0, float(recovery_ticket_eur))
        self.recovery_max_exposure_factor = max(1.0, float(recovery_max_exposure_factor))

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / 'dca_state.json'
        self.db_path = Path(paper_db_module.DB_PATH).resolve()

        self.portfolio: dict[str, dict] = {}
        self.total_invested = 0.0
        self.active_pairs: list[dict] = []
        self.last_rescan = 0.0
        self.last_buy = 0.0
        self.trade_count = 0
        self.coin_cooldowns: dict[str, float] = {}
        self.risk_off_until = 0.0

        self._load_state_or_rebuild()

    @property
    def budget_eur(self) -> float:
        """Alias für Kompatibilität."""
        return self.initial_capital_eur

    @property
    def per_coin_eur(self) -> float:
        """EUR pro Coin in dieser Runde."""
        n = max(1, len(self.active_pairs))
        per_round = min(self.per_round_eur, self.capital_remaining)
        return round(per_round / n, 2)

    def _load_state_or_rebuild(self) -> None:
        """Lädt persistenten Zustand; fallback: rekonstruiert aus DB."""
        loaded = False
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, min(float(raw.get('capital_remaining', self.initial_capital_eur)), self.initial_capital_eur))
                self.total_invested = max(0.0, float(raw.get('total_invested', 0.0)))
                self.last_rescan = float(raw.get('last_rescan', 0.0))
                self.last_buy = float(raw.get('last_buy', 0.0))
                self.trade_count = int(raw.get('trade_count', 0))
                self.risk_off_until = float(raw.get('risk_off_until', 0.0))

                portfolio = {}
                for pair, pos in (raw.get('portfolio') or {}).items():
                    shares = float((pos or {}).get('shares', 0.0))
                    cost_basis = float((pos or {}).get('cost_basis', 0.0))
                    if shares > 0 and cost_basis >= 0:
                        portfolio[pair] = {'shares': shares, 'cost_basis': cost_basis}
                self.portfolio = portfolio

                cooldowns = {}
                now = time.time()
                for pair, until_ts in (raw.get('coin_cooldowns') or {}).items():
                    ts = float(until_ts)
                    if ts > now:
                        cooldowns[pair] = ts
                self.coin_cooldowns = cooldowns

                if self.total_invested <= 0 and self.portfolio:
                    self.total_invested = sum(v['cost_basis'] for v in self.portfolio.values())

                loaded = True
                logger.info(f'♻️ DCA state geladen: investiert={self.total_invested:.2f}€, rest={self.capital_remaining:.2f}€, trades={self.trade_count}')
            except Exception as e:
                logger.warning(f'⚠️ DCA state konnte nicht geladen werden ({e}) – rekonstruiere aus DB')

        if not loaded:
            self._rebuild_state_from_db()
            self._save_state()

    def _rebuild_state_from_db(self) -> None:
        """Rekonstruiert Portfolio, Cash und Trade-Zähler aus DCA-DB-Trades."""
        self.portfolio = {}
        self.total_invested = 0.0
        self.trade_count = 0
        self.last_buy = 0.0

        if not self.db_path.exists():
            self.capital_remaining = self.initial_capital_eur
            return

        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT timestamp, market_question, size, price, resolved_at, real_pnl FROM paper_trades "
                "WHERE market_question LIKE 'DCA_%' ORDER BY id ASC"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f'⚠️ DCA state rebuild aus DB fehlgeschlagen: {e}')
            self.capital_remaining = self.initial_capital_eur
            return

        open_cost_basis = 0.0
        realized_pnl = 0.0

        for row in rows:
            pair = str(row['market_question']).removeprefix('DCA_')
            size = float(row['size'])
            price = float(row['price'])
            amount = max(0.0, size * price)
            ts = float(row['timestamp'] or 0.0)
            if ts > self.last_buy:
                self.last_buy = ts
            if size <= 0 or price <= 0:
                continue

            self.trade_count += 1
            self.total_invested += amount

            if row['resolved_at'] is None:
                if pair not in self.portfolio:
                    self.portfolio[pair] = {'shares': 0.0, 'cost_basis': 0.0}
                self.portfolio[pair]['shares'] += size
                self.portfolio[pair]['cost_basis'] += amount
                open_cost_basis += amount
            else:
                realized_pnl += float(row['real_pnl'] or 0.0)

        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost_basis + realized_pnl)
        logger.info(f'🧱 DCA state aus DB rekonstruiert: investiert={self.total_invested:.2f}€, rest={self.capital_remaining:.2f}€, trades={self.trade_count}')

    async def restore_state_from_db(self) -> None:
        self._rebuild_state_from_db()
        self._save_state()

    def _save_state(self) -> None:
        """Persistiert den Laufzeit-Zustand robust auf Disk."""
        try:
            payload = {
                'capital_remaining': round(self.capital_remaining, 8),
                'total_invested': round(self.total_invested, 8),
                'portfolio': {
                    pair: {
                        'shares': round(float(pos.get('shares', 0.0)), 12),
                        'cost_basis': round(float(pos.get('cost_basis', 0.0)), 8),
                    }
                    for pair, pos in self.portfolio.items()
                },
                'last_rescan': self.last_rescan,
                'last_buy': self.last_buy,
                'trade_count': self.trade_count,
                'coin_cooldowns': self.coin_cooldowns,
                'risk_off_until': self.risk_off_until,
                'updated_at': time.time(),
            }
            tmp = self.state_path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
            tmp.replace(self.state_path)
        except Exception as e:
            logger.warning(f'⚠️ DCA state write fehlgeschlagen: {e}')

    def _rolling_real_pnl_stats(self, window: int) -> tuple[float, int]:
        if not self.db_path.exists():
            return 0.0, 0
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT real_pnl FROM paper_trades "
                "WHERE market_question LIKE 'DCA_%' AND resolved_at IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (int(window),),
            )
            vals = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
            conn.close()
            return sum(vals), len(vals)
        except Exception as e:
            logger.warning(f'⚠️ Rolling-PnL konnte nicht gelesen werden: {e}')
            return 0.0, 0

    def _recent_pair_real_pnls(self, pair: str, limit: int) -> list[float]:
        if not self.db_path.exists():
            return []
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT real_pnl FROM paper_trades "
                "WHERE market_question = ? AND resolved_at IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (f'DCA_{pair}', int(limit)),
            )
            vals = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
            conn.close()
            return vals
        except Exception as e:
            logger.warning(f'⚠️ Pair-PnL für {pair} nicht lesbar: {e}')
            return []

    def _is_pair_on_cooldown(self, pair: str, now_ts: float) -> bool:
        until_ts = float(self.coin_cooldowns.get(pair, 0.0) or 0.0)
        if until_ts <= now_ts:
            self.coin_cooldowns.pop(pair, None)
            return False
        return True

    def _set_pair_cooldown(self, pair: str, now_ts: float, reason: str) -> None:
        until_ts = now_ts + self.coin_cooldown_sec
        self.coin_cooldowns[pair] = until_ts
        mins = int((until_ts - now_ts) / 60)
        logger.info(f'⏸️ {pair} im Cooldown für {mins}m ({reason})')

    async def rescan_top_coins(self) -> None:
        """Ermittelt die aktuell besten Coins neu."""
        logger.info("🔍 DCA: Scanne Top-Coins auf Kraken...")
        self.active_pairs = await rank_pairs_by_opportunity(CANDIDATE_PAIRS, top_n=self.top_n)
        self.last_rescan = time.time()

        if self.active_pairs:
            summary = ", ".join(
                f"{p['pair']}({p['change_pct']:+.1f}%)" for p in self.active_pairs
            )
            logger.info(f"📊 Top-{self.top_n} für DCA: {summary}")
        else:
            logger.warning("⚠️ Keine Paare gefunden – nächster Scan in 10 Min.")
        self._save_state()

    def _open_cost_basis(self) -> float:
        return sum(float(pos.get("cost_basis", 0.0)) for pos in self.portfolio.values())

    def _risk_cash_available(self) -> float:
        return max(0.0, self.capital_remaining - self.min_cash_reserve_eur)

    def _ticker_snapshot(self, pair: str, ticker: dict, fallback: dict | None = None) -> dict | None:
        """Live-Snapshot aus Kraken-Ticker inkl. frischem 24h-Change."""
        internal = PAIR_MAP.get(pair, pair)
        data = ticker.get(internal) or ticker.get(pair)
        if not data:
            return fallback
        try:
            open_price = float(data["o"])
            last_price = float(data["c"][0])
            high_price = float(data["h"][1])
            low_price = float(data["l"][1])
            volume_coin = float(data["v"][1])
            vwap = float(data["p"][1])
            if open_price <= 0 or last_price <= 0:
                return fallback
            change_pct = (last_price - open_price) / open_price * 100
            volatility = (high_price - low_price) / open_price * 100 if open_price > 0 else 0.0
            volume_eur = volume_coin * vwap
            bid, ask = extract_quote(data, last_price)
            out = dict(fallback or {})
            out.update({
                "pair": pair,
                "last_price": last_price,
                "bid": bid,
                "ask": ask,
                "change_pct": round(change_pct, 2),
                "volatility_pct": round(volatility, 2),
                "volume_eur": round(volume_eur, 0),
            })
            out.setdefault("score", max(abs(change_pct), volatility))
            return out
        except (KeyError, ValueError, IndexError, TypeError):
            return fallback

    async def _market_regime(self) -> dict:
        """BTC/ETH-Risk-Off-Filter für neue Altcoin-Einstiege. Recovery bleibt erlaubt."""
        if not self.trend_filter_enabled:
            return {"ok": True, "reasons": [], "btc_change_pct": None, "eth_change_pct": None}

        ticker = await fetch_ticker_data(["XBTEUR", "ETHEUR"])
        reasons: list[str] = []

        btc = self._ticker_snapshot("XBTEUR", ticker) or {}
        eth = self._ticker_snapshot("ETHEUR", ticker) or {}
        btc_change = btc.get("change_pct")
        eth_change = eth.get("change_pct")
        if btc_change is not None and float(btc_change) <= self.btc_risk_off_pct:
            reasons.append(f"BTC 24h {float(btc_change):+.2f}% <= {self.btc_risk_off_pct:+.2f}%")
        if eth_change is not None and float(eth_change) <= self.eth_risk_off_pct:
            reasons.append(f"ETH 24h {float(eth_change):+.2f}% <= {self.eth_risk_off_pct:+.2f}%")

        return {
            "ok": not reasons,
            "reasons": reasons,
            "btc_change_pct": btc_change,
            "eth_change_pct": eth_change,
        }

    def _pair_unrealized_pnl(self, pair: str, current_price: float) -> float:
        pos = self.portfolio.get(pair)
        if not pos:
            return 0.0
        return float(pos.get("shares", 0.0)) * current_price - float(pos.get("cost_basis", 0.0))

    def _pair_unrealized_pct(self, pair: str, current_price: float) -> float:
        pos = self.portfolio.get(pair)
        if not pos:
            return 0.0
        cost_basis = float(pos.get("cost_basis", 0.0))
        if cost_basis <= 0:
            return 0.0
        return self._pair_unrealized_pnl(pair, current_price) / cost_basis * 100

    def _recovery_allowed(self, pair: str, current_price: float, change_pct: float) -> bool:
        """Recovery-DCA nur nach deutlichem Minus und sichtbarer 24h-Erholung."""
        if pair not in self.portfolio:
            return False
        pnl_pct = self._pair_unrealized_pct(pair, current_price)
        return pnl_pct <= self.recovery_trigger_pct and change_pct >= self.recovery_reversal_pct

    def _record_dca_buy(self, trades: list[dict], coin_info: dict, pair: str, price: float, amount_eur: float, reason: str) -> None:
        """Bucht einen Paper-DCA-Kauf in Runtime-State; DB-Logging macht run().

        ``price`` ist der Last-Preis aus der Entscheidungslogik – gefüllt wird zum
        Ask. Der Fill-Preis wandert auch in die DB, damit ``_rebuild_state_from_db``
        über ``size * price`` wieder exakt auf ``amount_eur`` kommt.
        """
        fill_price = float(coin_info.get("ask") or price)
        coins_bought = amount_eur / fill_price
        logger.info(
            f"📝 PAPER DCA: KAUF {pair} | {amount_eur:.2f}€ → "
            f"{coins_bought:.6f} Coins @ {fill_price:.4f}€ (Last {price:.4f}€) [{reason}]"
        )

        self.capital_remaining = max(0.0, self.capital_remaining - amount_eur)
        if pair not in self.portfolio:
            self.portfolio[pair] = {"shares": 0.0, "cost_basis": 0.0}
        self.portfolio[pair]["shares"] += coins_bought
        self.portfolio[pair]["cost_basis"] += amount_eur
        self.total_invested += amount_eur
        self.trade_count += 1
        trades.append({
            "pair": pair,
            "price": fill_price,
            "amount_eur": amount_eur,
            "coins_bought": coins_bought,
            "change_pct": coin_info.get("change_pct", 0),
            "reason": reason,
            "timestamp": time.time(),
        })

    async def execute_dca_round(self) -> list[dict]:
        """
        Führt eine DCA-Runde durch. Stoppt wenn Kapital erschöpft ist.
        """
        now = time.time()

        risk_cash_available = self._risk_cash_available()
        if risk_cash_available < 0.50:
            logger.info(
                f"💰 DCA: Risikobudget erschöpft ({self.capital_remaining:.2f}€ Cash, "
                f"Reserve {self.min_cash_reserve_eur:.2f}€) — nur noch Positionen halten."
            )
            return []

        if self.risk_off_until > now:
            wait_min = int((self.risk_off_until - now) / 60)
            logger.info(f"🧯 Risk-Off aktiv: keine neuen Käufe für {wait_min}m")
            return []

        rolling_sum, rolling_count = self._rolling_real_pnl_stats(self.rolling_window)
        if rolling_count >= self.rolling_window and rolling_sum <= self.rolling_loss_limit:
            self.risk_off_until = now + self.risk_off_sec
            logger.warning(
                f"🧯 Risk-Off ausgelöst: rolling {rolling_count} Trades = {rolling_sum:+.4f}€ "
                f"(Schwelle {self.rolling_loss_limit:+.4f}€)"
            )
            self._save_state()
            return []

        if not self.active_pairs:
            await self.rescan_top_coins()

        # Frische Preise zum Kaufzeitpunkt holen. Wichtig: offene Portfolio-Paare
        # immer mitprüfen, auch wenn sie nicht mehr in Top-N gerankt sind.
        top_pairs = [p["pair"] for p in self.active_pairs]
        all_pairs = sorted(set(top_pairs) | set(self.portfolio.keys()))
        live_ticker = await fetch_ticker_data(all_pairs)
        market = await self._market_regime()
        if not market["ok"]:
            logger.warning("🧯 Markt-Risk-Off: neue Einstiege gesperrt (%s)" % "; ".join(market["reasons"]))

        live_info: dict[str, dict] = {}
        ranked_by_pair = {p["pair"]: p for p in self.active_pairs}
        for pair in all_pairs:
            snap = self._ticker_snapshot(pair, live_ticker, ranked_by_pair.get(pair))
            if snap:
                # Dip-Filter und Recovery-Reversal auf echte 24h-Bewegung stützen.
                rolling = await rolling_24h_change_pct(pair)
                if rolling is not None:
                    snap["change_pct"] = round(rolling, 2)
                live_info[pair] = snap

        trades: list[dict] = []
        per_round_budget = min(self.per_round_eur, risk_cash_available)
        round_remaining = per_round_budget

        # 1) Recovery zuerst: fester kleiner Nachkauf, sofern vorhandene Position
        # tief im Minus ist und live eine 24h-Erholung sichtbar ist.
        for pair in sorted(self.portfolio.keys()):
            coin_info = live_info.get(pair)
            if not coin_info:
                continue
            price = float(coin_info.get("last_price", 0.0) or 0.0)
            change_pct = float(coin_info.get("change_pct", 0.0) or 0.0)
            if price <= 0 or not self._recovery_allowed(pair, price, change_pct):
                continue
            if self._is_pair_on_cooldown(pair, now):
                wait_min = int((self.coin_cooldowns[pair] - now) / 60)
                logger.info(f"⏸️ Skip Recovery {pair}: Cooldown aktiv ({wait_min}m)")
                continue

            current_exposure = float(self.portfolio.get(pair, {}).get("cost_basis", 0.0))
            recovery_cap = self.max_pair_exposure_eur * self.recovery_max_exposure_factor
            remaining_pair_capacity = max(0.0, recovery_cap - current_exposure)
            amount_eur = round(min(
                self.recovery_ticket_eur,
                round_remaining,
                self._risk_cash_available(),
                remaining_pair_capacity,
            ), 2)
            if amount_eur < 0.01:
                logger.info(
                    f"⏭️ Skip Recovery {pair}: kein Budget/Capacity "
                    f"(cash={self.capital_remaining:.2f}€, reserve={self.min_cash_reserve_eur:.2f}€, "
                    f"exposure={current_exposure:.2f}/{recovery_cap:.2f}€)"
                )
                continue

            logger.info(
                f"♻️ Recovery-DCA {pair}: PnL {self._pair_unrealized_pct(pair, price):+.1f}%, "
                f"24h {change_pct:+.1f}%, Ticket {amount_eur:.2f}€"
            )
            self._record_dca_buy(trades, coin_info, pair, price, amount_eur, "recovery")
            round_remaining = round(max(0.0, round_remaining - amount_eur), 2)
            if round_remaining < 0.01 or self._risk_cash_available() < 0.50:
                break

        # 2) Normale Neueinstiege nur mit konservativem Dip-Filter und nur wenn
        # BTC/ETH-Regime okay ist. Recovery oben bleibt von Risk-Off ausgenommen.
        active_pairs = []
        projected_new_pairs: set[str] = set()
        if round_remaining >= 0.01:
            for coin_info in self.active_pairs:
                pair = coin_info["pair"]
                coin_info = live_info.get(pair, coin_info)
                change_pct = float(coin_info.get("change_pct", 0.0) or 0.0)
                price = float(coin_info.get("last_price", 0.0) or 0.0)
                if price <= 0:
                    continue

                if not market["ok"] and pair not in self.portfolio:
                    logger.info(f"⏭️ Skip {pair}: Markt-Risk-Off, keine neue Position")
                    continue

                dip_pct = -change_pct
                if dip_pct < self.min_edge_pct:
                    logger.info(f"⏭️ Skip {pair}: Dip {dip_pct:.2f}% < min {self.min_edge_pct:.2f}%")
                    continue

                if self._is_pair_on_cooldown(pair, now):
                    wait_min = int((self.coin_cooldowns[pair] - now) / 60)
                    logger.info(f"⏸️ Skip {pair}: Cooldown aktiv ({wait_min}m)")
                    continue

                if pair not in self.portfolio and (len(self.portfolio) + len(projected_new_pairs)) >= self.max_open_positions:
                    logger.info(f"⏭️ Skip {pair}: Max offene Positionen erreicht ({self.max_open_positions})")
                    continue

                current_exposure = float(self.portfolio.get(pair, {}).get("cost_basis", 0.0))
                if current_exposure >= self.max_pair_exposure_eur:
                    logger.info(f"⏭️ Skip {pair}: Coin-Exposure {current_exposure:.2f}€ >= Limit {self.max_pair_exposure_eur:.2f}€")
                    continue

                recent = self._recent_pair_real_pnls(pair, self.negative_streak_limit)
                if len(recent) >= self.negative_streak_limit and all(v < 0 for v in recent):
                    self._set_pair_cooldown(pair, now, f"{self.negative_streak_limit}x negative real_pnl")
                    continue

                if pair in self.portfolio and self._pair_unrealized_pnl(pair, price) < 0:
                    logger.info(f"⏭️ Skip {pair}: offene Position im Minus — normales Averaging blockiert")
                    continue

                remaining_pair_capacity = max(0.0, self.max_pair_exposure_eur - current_exposure)
                active_pairs.append((coin_info, pair, price, remaining_pair_capacity))
                if pair not in self.portfolio:
                    projected_new_pairs.add(pair)

        if not active_pairs:
            if not trades:
                logger.warning("⚠️ DCA: Keine gültigen Paare nach Filtern/Cooldowns – Runde wird übersprungen.")
                self._save_state()
                return []
        else:
            total_score = sum(max(float(info.get("score", 0) or 0), 0.0) for info, _, _, _ in active_pairs)
            if total_score <= 0:
                total_score = float(len(active_pairs))

            raw_allocations = []
            n = len(active_pairs)
            min_weight = 0.7 / n
            max_weight = 1.3 / n
            for coin_info, pair, price, remaining_pair_capacity in active_pairs:
                base_weight = max(float(coin_info.get("score", 0) or 0), 0.0) / total_score if total_score else 1 / n
                bounded_weight = max(min_weight, min(max_weight, base_weight))
                raw_allocations.append((coin_info, pair, price, bounded_weight, remaining_pair_capacity))

            normalized_total = sum(item[3] for item in raw_allocations) or 1.0

            for index, (coin_info, pair, price, bounded_weight, remaining_pair_capacity) in enumerate(raw_allocations):
                if index == len(raw_allocations) - 1:
                    amount_eur = round(round_remaining - sum(t["amount_eur"] for t in trades if t.get("reason") == "entry"), 2)
                else:
                    amount_eur = round(round_remaining * (bounded_weight / normalized_total), 2)

                amount_eur = max(0.0, min(amount_eur, self.capital_remaining, self._risk_cash_available(), remaining_pair_capacity))
                if amount_eur < 0.01:
                    continue
                self._record_dca_buy(trades, coin_info, pair, price, amount_eur, "entry")

        self.last_buy = time.time()
        self._save_state()
        return trades

    async def get_portfolio_value(self) -> dict:
        """Berechnet aktuellen Portfolio-Wert anhand Live-Preise."""
        if not self.portfolio:
            return {"total_value_eur": 0.0, "total_invested_eur": round(self.total_invested, 2), "pnl_eur": 0.0, "pnl_pct": 0.0, "trade_count": self.trade_count, "positions": {}}

        pairs = list(self.portfolio.keys())
        ticker = await fetch_ticker_data(pairs)

        positions = {}
        total_value = 0.0

        for pair, pos in self.portfolio.items():
            internal = PAIR_MAP.get(pair, pair)
            data = ticker.get(internal) or ticker.get(pair)
            if not data:
                logger.warning("Portfolio-Bewertung %s: kein Live-Ticker – Position nicht mit 0 bewerten", pair)
                positions[pair] = {
                    "shares": round(pos["shares"], 6),
                    "cost_basis": round(pos["cost_basis"], 2),
                    "current_value": None,
                    "pnl_eur": None,
                    "pnl_pct": None,
                    "current_price": None,
                    "valuation_status": "missing_ticker",
                }
                continue

            current_price = float(data["c"][0])
            current_value = pos["shares"] * current_price
            pnl = current_value - pos["cost_basis"]
            pnl_pct = (pnl / pos["cost_basis"] * 100) if pos["cost_basis"] > 0 else 0

            positions[pair] = {
                "shares": round(pos["shares"], 6),
                "cost_basis": round(pos["cost_basis"], 2),
                "current_value": round(current_value, 2),
                "pnl_eur": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "current_price": current_price,
                "valuation_status": "live",
            }
            total_value += current_value

        open_cost_basis = self._open_cost_basis()
        pnl_total = total_value - open_cost_basis
        pnl_pct_total = (pnl_total / open_cost_basis * 100) if open_cost_basis > 0 else 0

        return {
            "total_value_eur": round(total_value, 2),
            "total_invested_eur": round(open_cost_basis, 2),
            "pnl_eur": round(pnl_total, 2),
            "pnl_pct": round(pnl_pct_total, 2),
            "trade_count": self.trade_count,
            "positions": positions,
        }

    async def update_paper_pnl(self) -> None:
        """
        Schreibt für jeden offenen DCA-Trade den realistischen PnL in die DB.
        Formel (roundtrip): pnl = (bid - entry_price) * shares
                                  - entry_cost * TAKER_FEE   (Kaufgebühr)
                                  - current_value * TAKER_FEE (simulierter Verkauf)

        Der simulierte Verkauf rechnet mit dem Bid, weil das der Erlös wäre. Sonst
        wäre der unrealisierte PnL systematisch optimistischer als der Betrag, den
        ``manage_dca_exits`` beim echten Schließen realisiert.
        """
        from polybot import config
        import aiosqlite
        import os

        TAKER_FEE = config.CRYPTO_TAKER_FEE_RATE  # 0.004 = 0.4%

        # Aktuelle Preise holen
        pairs = list(self.portfolio.keys())
        if not pairs:
            return
        ticker = await fetch_ticker_data(pairs)

        # Alle offenen DCA-Trades aus DB laden
        db_path = str(self.db_path)
        try:
            async with aiosqlite.connect(db_path, timeout=30.0) as db:
                async with db.execute(
                    "SELECT id, market_question, size, price FROM paper_trades "
                    "WHERE market_question LIKE 'DCA_%' AND resolved_at IS NULL"
                ) as cursor:
                    rows = [dict(zip([c[0] for c in cursor.description], row))
                            async for row in cursor]
        except Exception as e:
            logger.warning(f"DCA PnL-Update: DB-Lesefehler: {e}")
            return

        updated = 0
        async with aiosqlite.connect(db_path, timeout=30.0) as db:
            for row in rows:
                pair = row["market_question"].removeprefix("DCA_")
                internal = PAIR_MAP.get(pair, pair)
                data = ticker.get(internal) or ticker.get(pair)
                if not data:
                    continue

                current_price = float(data["c"][0])
                bid, _ask     = extract_quote(data, current_price)
                entry_price   = float(row["price"])
                shares        = float(row["size"])
                entry_cost    = entry_price * shares

                current_value = bid * shares
                gross_pnl     = current_value - entry_cost
                buy_fee       = entry_cost * TAKER_FEE
                sell_fee      = current_value * TAKER_FEE
                real_pnl      = gross_pnl - buy_fee - sell_fee

                # UPDATE only — do NOT close the trade (resolved_at stays NULL).
                # Unrealisierter PnL gehört in unrealized_pnl, NICHT in real_pnl.
                try:
                    await db.execute(
                        "UPDATE paper_trades SET unrealized_pnl = ? WHERE id = ?",
                        (round(real_pnl, 6), row["id"])
                    )
                    updated += 1
                except Exception as e:
                    logger.warning(f"DCA PnL update #{row['id']}: {e}")
            await db.commit()

        if updated:
            logger.info(f"💰 DCA unrealisierter PnL aktualisiert: {updated} Trades (unrealized_pnl, Fees 0.8% roundtrip)")
            self._save_state()


    async def resolve_due_trades(self) -> list[dict]:
        """Schließt offene DCA-Trades bei TP/SL/Time-Exit und gibt Cash frei."""
        from polybot import config

        open_rows = await get_open_dca_trades()
        if not open_rows:
            return []

        pairs = sorted({row["market_question"].removeprefix("DCA_") for row in open_rows})
        ticker = await fetch_ticker_data(pairs)
        taker_fee = config.CRYPTO_TAKER_FEE_RATE
        now = time.time()
        resolved = []

        for row in open_rows:
            pair = row["market_question"].removeprefix("DCA_")
            internal = PAIR_MAP.get(pair, pair)
            data = ticker.get(internal) or ticker.get(pair)
            if not data:
                continue

            current_price = float(data["c"][0])
            # Trigger entscheidet auf Last, verkauft wird zum Bid.
            exit_price, _ask = extract_quote(data, current_price)
            entry_price = float(row["entry_price"])
            shares = float(row["size"])
            entry_cost = entry_price * shares
            current_value = exit_price * shares
            gross_pnl = current_value - entry_cost
            buy_fee = entry_cost * taker_fee
            sell_fee = current_value * taker_fee
            real_pnl = gross_pnl - buy_fee - sell_fee
            price_change = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
            age_sec = max(0.0, now - float(row["timestamp"] or 0.0))

            reason = None
            if self.take_profit_pct > 0 and price_change >= self.take_profit_pct:
                reason = "take_profit"
            elif self.stop_loss_pct > 0 and price_change <= -self.stop_loss_pct:
                reason = "stop_loss"
            elif self.max_hold_sec > 0 and age_sec >= self.max_hold_sec:
                reason = "time_exit"

            if not reason:
                continue

            # Harte Schutzregel: TP/SL niemals mit negativem Real-PnL schließen.
            # time_exit ist der Verlust-Backstop und muss IMMER schließen dürfen.
            if reason != "time_exit" and real_pnl < 0:
                logger.info("🛡️ DCA Exit blockiert %s: %s hätte Minus realisiert (%+.4f€)" % (pair, reason, real_pnl))
                continue

            # Mindest-Netto-Gewinn (nach Fees), um Mini-Exits zu vermeiden.
            # time_exit wird davon ebenfalls nicht aufgehalten (Zwangsschließung).
            if reason != "time_exit" and real_pnl < self.min_net_profit_eur:
                logger.info("💡 DCA Exit verschoben %s: %s netto %+.4f€ < Mindestgewinn %.4f€" % (pair, reason, real_pnl, self.min_net_profit_eur))
                continue

            await resolve_trade(int(row["id"]), exit_price, round(real_pnl, 6))

            pos = self.portfolio.get(pair)
            if pos:
                remaining_shares = float(pos.get("shares", 0.0)) - shares
                remaining_cost = float(pos.get("cost_basis", 0.0)) - entry_cost
                if remaining_shares <= 1e-12 or remaining_cost <= 1e-9:
                    self.portfolio.pop(pair, None)
                else:
                    pos["shares"] = remaining_shares
                    pos["cost_basis"] = max(0.0, remaining_cost)

            self.capital_remaining += entry_cost + real_pnl
            resolved.append({
                "id": int(row["id"]),
                "pair": pair,
                "reason": reason,
                "exit_price": current_price,
                "real_pnl": round(real_pnl, 6),
            })
            logger.info(f"✅ DCA Exit {pair}: {reason} @ {current_price:.4f}€ | PnL {real_pnl:+.4f}€")

        if resolved:
            self._save_state()
        return resolved

    async def run(self) -> None:
        """Haupt-Loop: scannt Coins, kauft in Intervallen, loggt Performance."""
        mode = "📝 PAPER" if self.paper_mode else "💰 LIVE"
        logger.info(
            f"🤖 DCA-Bot gestartet [{mode}] | Budget: {self.budget_eur}€ | "
            f"Intervall: {self.interval_sec//3600}h | Top-{self.top_n} Coins"
        )

        # Initialer Scan nur wenn nötig
        now = time.time()
        if not self.active_pairs or (now - self.last_rescan >= self.rescan_interval):
            await self.rescan_top_coins()

        while True:
            now = time.time()

            resolved = await self.resolve_due_trades()
            if resolved:
                profit_rows = [r for r in resolved if float(r.get("real_pnl", 0.0)) > 0]
                if profit_rows:
                    try:
                        from polybot.alerts import send_telegram
                        lines = "\n".join(
                            f"• {r['pair']}: {float(r['real_pnl']):+.2f}€ ({r['reason']})"
                            for r in profit_rows
                        )
                        total_profit = sum(float(r.get("real_pnl", 0.0)) for r in profit_rows)
                        await send_telegram(
                            f"✅ Gewinn realisiert ({len(profit_rows)} Trade(s), gesamt {total_profit:+.2f}€)\n{lines}"
                        )
                    except Exception as e:
                        logger.warning(f"Gewinn-Alert fehlgeschlagen: {e}")

            if self.portfolio:
                await self.update_paper_pnl()

            # Coin-Auswahl täglich aktualisieren
            if now - self.last_rescan >= self.rescan_interval:
                await self.rescan_top_coins()

            # DCA-Kauf wenn Intervall abgelaufen
            if now - self.last_buy >= self.interval_sec:
                trades = await self.execute_dca_round()

                # Portfolio-Status nach jedem Kauf loggen
                portfolio = await self.get_portfolio_value()
                logger.info(
                    f"💼 Portfolio: {portfolio['total_value_eur']}€ "
                    f"(investiert: {portfolio.get('total_invested_eur', self.total_invested):.2f}€, "
                    f"PnL: {portfolio['pnl_eur']:+.2f}€ / {portfolio['pnl_pct']:+.1f}%)"
                )

                # In paper_db loggen + PnL aller offenen Trades aktualisieren
                try:
                    from polybot.paper_db import log_paper_trade
                    for t in trades:
                        await log_paper_trade(
                            market=f"DCA_{t['pair']}",
                            side="buy",
                            size=t["coins_bought"],
                            price=t["price"],
                            edge=abs(t["change_pct"]) / 100,
                            status="paper" if self.paper_mode else "live",
                        )
                    await self.update_paper_pnl()
                except Exception as e:
                    logger.warning(f"DB-Log fehlgeschlagen: {e}")

            await asyncio.sleep(60)  # Jede Minute prüfen
