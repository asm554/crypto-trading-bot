"""Triangulärer Arbitrage-Bot — Der Pedant.

Prüft pro Intervall, ob der Umweg EUR->BTC->ETH->EUR (oder umgekehrt) nach drei
Taker-Gebühren mehr EUR zurückgibt als der direkte Halt. Anders als DCA/Momentum/
MeanRev hält dieser Bot nie eine offene Position: ein Zyklus ist drei Fills, die
im selben Aufruf beginnen und enden. ``manage_positions()`` existiert deshalb nur
als Stub für die gemeinsame run()-Loop-Struktur.

Live gegen Kraken gemessen liegt die Netto-Marge auf diesem Dreieck meist bei
ca. -1.15% bis -1.25% (im Wesentlichen die drei Taker-Gebühren) — der Bot wird
also realistisch fast nie tatsächlich handeln. Das ist die ehrliche Eigenschaft
von Arbitrage auf einer liquiden Börse ohne Kolokation, kein Bug.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import extract_quote, fetch_ticker_data
from polybot.paper_db import get_realized_pnl_by_prefix, log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)

PREFIX = "ARB_"
BOT_KEY = "arb"

# EUR->BTC->ETH->EUR-Dreieck. XETHXXBT ist Krakens direktes Kreuzpaar und
# notiert "wie viel BTC kostet 1 ETH" (Kraken-Altname: ETHXBT).
LEG_EUR_BTC = "XXBTZEUR"
LEG_EUR_ETH = "XETHZEUR"
LEG_BTC_ETH = "XETHXXBT"
ARB_PAIRS = [LEG_EUR_BTC, LEG_EUR_ETH, LEG_BTC_ETH]

DIRECTIONS = ("eur_btc_eth", "eur_eth_btc")


class TriangularArbBot:
    def __init__(
        self,
        initial_capital_eur: float = 100.0,
        interval_sec: int = 45,
        ticket_eur: float = 25.0,
        min_net_profit_eur: float = 0.05,
        max_trades_per_hour: int = 6,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
    ):
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.interval_sec = int(interval_sec)
        self.ticket_eur = float(ticket_eur)
        self.min_net_profit_eur = float(min_net_profit_eur)
        self.max_trades_per_hour = max(1, int(max_trades_per_hour))
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        if not self.paper_mode:
            logger.warning("Arb live mode is intentionally not implemented")
            raise NotImplementedError("TriangularArbBot is paper-only")

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "arb_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()

        self.trade_count = 0
        self.last_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_timestamps: list[float] = []
        self._load_state_or_rebuild()

    def _save_state(self) -> None:
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "trade_count": self.trade_count,
            "last_scan": self.last_scan,
            "last_snapshot": self.last_snapshot,
            "trade_timestamps": self.trade_timestamps,
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
                self.trade_count = int(raw.get("trade_count", 0))
                self.last_scan = float(raw.get("last_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                now = time.time()
                self.trade_timestamps = [float(t) for t in (raw.get("trade_timestamps") or []) if now - float(t) < 3600]
                logger.info("♻️ Arb state geladen: cash=%.2f€, trades=%d", self.capital_remaining, self.trade_count)
                return
            except Exception as e:
                logger.warning("Arb state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.trade_count = 0
        self.trade_timestamps = []
        realized = 0.0
        if not self.db_path.exists():
            self.capital_remaining = self.initial_capital_eur
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT real_pnl FROM paper_trades WHERE market_question LIKE ? AND resolved_at IS NOT NULL",
                (f"{PREFIX}%",),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            self.trade_count += 1
            realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital_eur + realized)
        logger.info("🧱 Arb rebuild: cash=%.2f€, trades=%d", self.capital_remaining, self.trade_count)

    @staticmethod
    def _compute_leg_quotes(ticker: dict) -> dict | None:
        """Bid/Ask für alle drei Legs; None wenn irgendein Leg fehlt."""
        quotes = {}
        for pair in ARB_PAIRS:
            data = ticker.get(pair)
            if not data:
                return None
            try:
                last = float(data["c"][0])
            except (KeyError, ValueError, IndexError, TypeError):
                return None
            if last <= 0:
                return None
            bid, ask = extract_quote(data, last)
            quotes[pair] = {"bid": bid, "ask": ask}
        return quotes

    @staticmethod
    def _cycle_margin(quotes: dict, direction: str, fee_rate: float) -> float:
        """Netto-Multiplikator für 1 EUR Startkapital durch das Dreieck (>1 = Gewinn).

        XETHXXBT notiert "wie viel BTC kostet 1 ETH":
          - ETH mit BTC kaufen  -> BTC bezahlen, ETH bekommen -> zum Ask von XETHXXBT
          - ETH gegen BTC verkaufen -> ETH bezahlen, BTC bekommen -> zum Bid von XETHXXBT
        """
        btc_eur = quotes[LEG_EUR_BTC]
        eth_eur = quotes[LEG_EUR_ETH]
        eth_btc = quotes[LEG_BTC_ETH]
        f = 1.0 - fee_rate

        if direction == "eur_btc_eth":
            # EUR -> BTC (kaufe BTC zum Ask) -> ETH (kaufe ETH mit BTC zum Ask von ETH/BTC) -> EUR (verkaufe ETH zum Bid)
            btc = (1.0 / btc_eur["ask"]) * f
            eth = (btc / eth_btc["ask"]) * f
            return (eth * eth_eur["bid"]) * f
        elif direction == "eur_eth_btc":
            # EUR -> ETH (kaufe ETH zum Ask) -> BTC (verkaufe ETH gegen BTC zum Bid von ETH/BTC) -> EUR (verkaufe BTC zum Bid)
            eth = (1.0 / eth_eur["ask"]) * f
            btc = (eth * eth_btc["bid"]) * f
            return (btc * btc_eur["bid"]) * f
        raise ValueError(f"unbekannte Richtung: {direction}")

    def _trades_last_hour(self, now: float) -> int:
        self.trade_timestamps = [t for t in self.trade_timestamps if now - t < 3600]
        return len(self.trade_timestamps)

    async def scan_entries(self) -> list[dict]:
        now = time.time()
        if now - self.last_scan < self.interval_sec:
            return []
        self.last_scan = now

        ticker = await fetch_ticker_data(ARB_PAIRS)
        quotes = self._compute_leg_quotes(ticker)
        if not quotes:
            logger.info("⏭️ ARB: kein vollständiger Ticker für das Dreieck")
            return []

        fee = config.CRYPTO_TAKER_FEE_RATE
        margins = {}
        for direction in DIRECTIONS:
            multiplier = self._cycle_margin(quotes, direction, fee)
            net_profit_eur = self.ticket_eur * (multiplier - 1.0)
            margins[direction] = net_profit_eur
            logger.info(
                "📐 ARB %s: netto %+.4f%% (%+.4f€ auf %.2f€ Ticket)",
                direction, (multiplier - 1.0) * 100, net_profit_eur, self.ticket_eur,
            )

        best_direction = max(margins, key=margins.get)
        best_profit = margins[best_direction]

        if best_profit <= self.min_net_profit_eur:
            return []
        if self._trades_last_hour(now) >= self.max_trades_per_hour:
            logger.info("⏭️ ARB: Rate-Limit erreicht (%d Trades/Std.)", self.max_trades_per_hour)
            return []
        if self.capital_remaining < self.ticket_eur:
            logger.info("⏭️ ARB: Cash %.2f€ reicht nicht für Ticket %.2f€", self.capital_remaining, self.ticket_eur)
            return []

        suffix = "A" if best_direction == "eur_btc_eth" else "B"
        market_question = f"{PREFIX}BTC-ETH-{suffix}"
        fill_price = 1.0 + (best_profit / self.ticket_eur)

        trade_id = await log_paper_trade(market_question, "cycle", self.ticket_eur, fill_price, best_profit / self.ticket_eur, "paper")
        await resolve_trade(trade_id, fill_price, round(best_profit, 6))

        self.capital_remaining += best_profit
        self.trade_count += 1
        self.trade_timestamps.append(now)
        self._save_state()

        logger.info("✅ ARB Zyklus %s: %+.4f€ Netto-Gewinn", market_question, best_profit)
        return [{"direction": best_direction, "market_question": market_question, "net_profit_eur": best_profit}]

    async def manage_positions(self) -> list[dict]:
        """No-Op: ein Arb-Zyklus ist atomar (drei Fills in scan_entries()), es
        gibt nie eine offene Position, die hier verwaltet werden müsste."""
        return []

    async def equity(self) -> dict:
        return {
            "equity_eur": self.capital_remaining,
            "cash_eur": self.capital_remaining,
            "open_positions": 0,
            "unrealized_pnl_eur": 0.0,
            "realized_pnl_eur": await get_realized_pnl_by_prefix(PREFIX),
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        e = await self.equity()
        await log_equity_snapshot(BOT_KEY, **e)
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 Arb-Bot gestartet [PAPER] | Budget %.2f€ | Ticket %.2f€", self.initial_capital_eur, self.ticket_eur)
        # Kurzes interval_sec ändert nichts daran, dass echte Arb-Fenster in
        # Millisekunden von kolokierten Bots geschlossen werden. Bewusst als
        # Beobachtungs-/Lehrmittel verstehen, nicht als ernsthafte Gewinnquelle.
        while True:
            try:
                await self.manage_positions()
                await self.scan_entries()
                await self.maybe_snapshot()
            except Exception as e:
                logger.exception("⚠️ Arb-Loop-Fehler (%s) – weiter in 30s", e)
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(10)
