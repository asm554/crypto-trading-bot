"""News-Trading paper bot — Der Reporter.

Paper-only Kraken-Spot (SOL/EUR, BTC/EUR), nur Long. Liest öffentliche Krypto-
RSS-Feeds, lässt jede NEUE Schlagzeile von Jev (TypeSafe System One) bewerten
und eröffnet nur bei hoher Wahrscheinlichkeit eine kurze Position. Jev ist hier
die Signalquelle und deshalb fail-closed: ohne Jev-Antwort wird nicht gehandelt.
Harte Sicherungen bleiben in Code: Stop-Loss, Trailing-Stop, Zeitlimit,
Tageslimit, Verlustpause und Kontoverlust-Limit. Ein neue bärische Meldung zum
selben Asset beendet eine offene Position früher. Kein Order-Execution-Code.
"""

import asyncio
import email.utils
import hashlib
import json
import logging
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp

from polybot import config
from polybot import paper_db as paper_db_module
from polybot.dca_strategy import fetch_ticker_data
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade
from polybot.surfer_strategy import SurferBot

logger = logging.getLogger(__name__)
PREFIX = "NEWS_"
BOT_KEY = "news"
MIN_POSITION_EUR = 1.0
ASSETS = ("SOLEUR", "XBTEUR")
ASSET_LABELS = {"SOLEUR": "SOL", "XBTEUR": "BTC"}
FEEDS = (
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
)
MAX_FEED_BYTES = 2_000_000
SEEN_LIMIT = 1000


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    """RSS 2.0 -> [{id, title, summary, published_ts, source}]. Fehlerhafte Feeds -> []."""
    if len(xml_bytes) > MAX_FEED_BYTES:
        return []
    head = xml_bytes.lower()
    # Entity-Expansion/XXE: Feeds sind unvertrauenswürdig und brauchen weder DTD noch Entities.
    if b"<!doctype" in head or b"<!entity" in head:
        logger.warning("NEWS: Feed %s enthält DTD/Entity – verworfen", source)
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        summary = " ".join((item.findtext("description") or "").split())[:400]
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            continue  # ohne verlässlichen Zeitstempel kein Handel auf die Meldung
        items.append({
            "id": hashlib.sha256(f"{source}|{guid}".encode()).hexdigest()[:16],
            "title": title[:300],
            "summary": summary,
            "published_ts": published,
            "source": source,
        })
    return items


def build_questions() -> dict:
    return {
        "asset": {
            "type": "choice",
            "instructions": (
                "Which cryptocurrency does the headline directly affect? "
                "Choose `none` if it is not clearly about SOL or BTC or the whole crypto market."
            ),
            "criteria": {
                "SOL": "News specifically about Solana or its ecosystem",
                "BTC": "News specifically about Bitcoin, or broad crypto-market news",
                "none": "Unrelated, opinion, price commentary, or about other coins",
            },
        },
        "is_new": {
            "type": "noul",
            "instructions": "The headline reports a new concrete development, not a recap, opinion or price commentary.",
        },
        "bullish": {
            "type": "noul",
            "instructions": "The news is likely to push the price of the affected asset up within the next hours.",
        },
        "bearish": {
            "type": "noul",
            "instructions": "The news is likely to push the price of the affected asset down within the next hours.",
        },
    }


def build_state(item: dict, now: float) -> dict:
    return {
        "headline": item["title"],
        "summary": item["summary"],
        "source": item["source"],
        "minutes_since_published": round(max(0.0, now - item["published_ts"]) / 60, 1),
    }


def evaluate(answers: dict, min_choice_prob: float, min_bullish: float, min_new: float, max_bearish: float):
    """Jev-Antworten -> (asset_pair|None, 'bullish'|'bearish'|None, details)."""
    try:
        choice = answers["asset"]
        label = choice["choice"]
        label_prob = float(choice["probabilities"][label])
        is_new = float(answers["is_new"]["noul"])
        bullish = float(answers["bullish"]["noul"])
        bearish = float(answers["bearish"]["noul"])
    except (KeyError, TypeError, ValueError):
        return None, None, {}
    details = {"asset": label, "asset_prob": label_prob, "is_new": is_new, "bullish": bullish, "bearish": bearish}
    pair = {"SOL": "SOLEUR", "BTC": "XBTEUR"}.get(label)
    if pair is None or label_prob < min_choice_prob or is_new < min_new:
        return None, None, details
    if bullish >= min_bullish and bearish <= max_bearish:
        return pair, "bullish", details
    if bearish >= min_bullish and bullish <= max_bearish:
        return pair, "bearish", details
    return pair, None, details


class NewsBot:
    def __init__(
        self,
        jev,
        initial_capital_eur: float = 500.0,
        poll_interval_sec: int = 60,
        max_headline_age_sec: int = 900,
        min_choice_prob: float = 0.6,
        min_bullish: float = 0.75,
        min_new: float = 0.7,
        max_bearish: float = 0.3,
        position_eur: float = 25.0,
        stop_loss_pct: float = 1.5,
        trailing_activation_pct: float = 1.0,
        trailing_stop_pct: float = 1.0,
        max_hold_sec: int = 2 * 3600,
        max_open_positions: int = 2,
        max_trades_per_day: int = 6,
        loss_streak_limit: int = 3,
        loss_pause_sec: int = 6 * 3600,
        account_loss_limit_pct: float = 10.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
        feeds: tuple = FEEDS,
    ):
        self.jev = jev
        self.initial_capital_eur = float(initial_capital_eur)
        self.capital_remaining = float(initial_capital_eur)
        self.poll_interval_sec = int(poll_interval_sec)
        self.max_headline_age_sec = int(max_headline_age_sec)
        self.min_choice_prob = float(min_choice_prob)
        self.min_bullish = float(min_bullish)
        self.min_new = float(min_new)
        self.max_bearish = float(max_bearish)
        self.position_eur = float(position_eur)
        self.stop_loss_pct = float(stop_loss_pct)
        self.trailing_activation_pct = float(trailing_activation_pct)
        self.trailing_stop_pct = float(trailing_stop_pct)
        self.max_hold_sec = int(max_hold_sec)
        self.max_open_positions = int(max_open_positions)
        self.max_trades_per_day = int(max_trades_per_day)
        self.loss_streak_limit = int(loss_streak_limit)
        self.loss_pause_sec = int(loss_pause_sec)
        self.account_loss_limit_pct = float(account_loss_limit_pct)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        self.feeds = tuple(feeds)
        if not self.paper_mode:
            logger.warning("News live mode is intentionally not implemented")
            raise NotImplementedError("NewsBot is paper-only")

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "news_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.seen: list[str] = []
        self.entry_timestamps: list[float] = []
        self.consecutive_losses = 0
        self.loss_pause_until = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self.primed = False
        self._load_state_or_rebuild()

    # ------------------------------------------------------------------ state
    def _save_state(self) -> None:
        payload = {
            "capital_remaining": round(self.capital_remaining, 8),
            "portfolio": self.portfolio,
            "seen": self.seen[-SEEN_LIMIT:],
            "entry_timestamps": self.entry_timestamps,
            "consecutive_losses": self.consecutive_losses,
            "loss_pause_until": self.loss_pause_until,
            "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count,
            "primed": self.primed,
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
                self.seen = list(raw.get("seen") or [])
                self.entry_timestamps = [float(t) for t in raw.get("entry_timestamps") or []]
                self.consecutive_losses = int(raw.get("consecutive_losses", 0))
                self.loss_pause_until = float(raw.get("loss_pause_until", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                self.primed = bool(raw.get("primed", False))
                state_ids = {int(p.get("trade_id") or 0) for p in self.portfolio.values() if int(p.get("trade_id") or 0) > 0}
                if state_ids != paper_db_module.get_open_trade_ids_by_prefix_sync(PREFIX):
                    raise ValueError("State und offenes NEWS-Ledger weichen ab")
                return
            except Exception as e:
                logger.warning("News state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital_eur
        self.portfolio = {}
        self.trade_count = 0
        self.consecutive_losses = 0
        self.loss_pause_until = 0.0
        realized = open_cost = 0.0
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE market_question LIKE ? ESCAPE '\\' ORDER BY id ASC",
                (paper_db_module.prefix_like_pattern(PREFIX),),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        last_resolved_at = None
        for row in rows:
            pair = str(row["market_question"]).removeprefix(PREFIX)
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            if size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += size * price
                self.portfolio[pair] = {
                    "shares": size, "cost_basis": size * price, "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    "needs_recovery_exit": True, "trade_id": int(row["id"]),
                }
            else:
                pnl = float(row["real_pnl"] or 0.0)
                realized += pnl
                last_resolved_at = float(row["resolved_at"])
                self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0
        if self.consecutive_losses >= self.loss_streak_limit and last_resolved_at is not None:
            self.loss_pause_until = last_resolved_at + self.loss_pause_sec
        self.capital_remaining = max(0.0, self.initial_capital_eur - open_cost + realized)

    # ------------------------------------------------------------------ feeds
    async def _fetch_feed(self, session: aiohttp.ClientSession, url: str) -> list[dict]:
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                resp.raise_for_status()
                body = b""
                async for chunk in resp.content.iter_chunked(65536):
                    body += chunk
                    if len(body) > MAX_FEED_BYTES:
                        break  # zu groß: parse_feed verwirft den Feed
            return parse_feed(body, url)
        except Exception as exc:
            logger.warning("NEWS: Feed %s fehlgeschlagen (%s)", url, exc)
            return []

    async def fetch_new_items(self) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(*(self._fetch_feed(session, u) for u in self.feeds))
        seen = set(self.seen)
        fresh = []
        for items in results:
            for item in items:
                if item["id"] not in seen:
                    seen.add(item["id"])
                    fresh.append(item)
                    self.seen.append(item["id"])
        self.seen = self.seen[-SEEN_LIMIT:]
        if not self.primed:
            # Beim ersten Lauf nur den Bestand merken, nie auf Altmeldungen handeln.
            self.primed = True
            self._save_state()
            return []
        fresh.sort(key=lambda i: i["published_ts"])
        return fresh

    # ---------------------------------------------------------------- trading
    async def _snapshot(self, pair: str) -> dict | None:
        return SurferBot._ticker_snapshot(pair, await fetch_ticker_data([pair]))

    def _entries_today(self, now: float) -> int:
        self.entry_timestamps = [t for t in self.entry_timestamps if now - t < 86400]
        return len(self.entry_timestamps)

    async def _close(self, pair: str, bid: float, reason: str, now: float) -> dict | None:
        pos = self.portfolio[pair]
        shares = float(pos["shares"])
        entry_cost = shares * float(pos["entry_price"])
        value = shares * bid
        fee = config.CRYPTO_TAKER_FEE_RATE
        real_pnl = value - entry_cost - entry_cost * fee - value * fee
        if not await resolve_trade(int(pos["trade_id"]), bid, round(real_pnl, 6)):
            self._rebuild_state_from_db()
            self._save_state()
            return None
        self.capital_remaining += entry_cost + real_pnl
        self.portfolio.pop(pair, None)
        if real_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.loss_streak_limit:
                self.loss_pause_until = now + self.loss_pause_sec
        else:
            self.consecutive_losses = 0
        logger.info("✅ NEWS Exit %s: %s @ %.6f€ | PnL %+0.4f€", pair, reason, bid, real_pnl)
        self._save_state()
        return {"pair": pair, "reason": reason, "pnl": real_pnl}

    async def manage_positions(self, bearish_pairs: set[str] | None = None) -> list[dict]:
        closed = []
        now = time.time()
        for pair in list(self.portfolio):
            pos = self.portfolio[pair]
            snap = await self._snapshot(pair)
            if not snap:
                continue
            last = float(snap["last_price"])
            bid = float(snap.get("bid") or last)
            entry = float(pos["entry_price"])
            peak = max(float(pos.get("peak_price") or entry), last)
            pos["peak_price"] = peak
            trailing_active = peak >= entry * (1 + self.trailing_activation_pct / 100)
            stop = max(
                entry * (1 - self.stop_loss_pct / 100),
                peak * (1 - self.trailing_stop_pct / 100) if trailing_active else 0.0,
            )
            reason = None
            if pos.get("needs_recovery_exit"):
                reason = "state_recovery_exit"
            elif bid <= stop:
                reason = "trailing_stop" if trailing_active and peak * (1 - self.trailing_stop_pct / 100) >= entry * (1 - self.stop_loss_pct / 100) else "stop_loss"
            elif bearish_pairs and pair in bearish_pairs:
                reason = "bearish_news"
            elif now - float(pos["entry_ts"]) >= self.max_hold_sec:
                reason = "time_exit"
            if reason:
                result = await self._close(pair, bid, reason, now)
                if result:
                    closed.append(result)
        if not closed:
            self._save_state()
        return closed

    async def open_position(self, pair: str, item: dict, details: dict) -> dict | None:
        now = time.time()
        if pair in self.portfolio or len(self.portfolio) >= self.max_open_positions:
            return None
        if self.loss_pause_until > now:
            return None
        if self._entries_today(now) >= self.max_trades_per_day:
            logger.info("⏭️ NEWS: Tageslimit erreicht")
            return None
        equity_snap = await self.equity()
        if equity_snap["equity_eur"] <= self.initial_capital_eur * (1 - self.account_loss_limit_pct / 100):
            logger.info("⏭️ NEWS: Kontoverlust-Limit erreicht – keine neuen Einstiege")
            return None
        snap = await self._snapshot(pair)
        if not snap:
            return None
        price = float(snap.get("ask") or snap["last_price"])
        value = min(self.position_eur, self.capital_remaining)
        if value < MIN_POSITION_EUR or price <= 0:
            return None
        qty = value / price
        trade_id = await log_paper_trade(f"{PREFIX}{pair}", "buy", qty, price, details["bullish"], "paper")
        self.capital_remaining -= value
        self.portfolio[pair] = {
            "shares": qty, "cost_basis": value, "entry_price": price, "entry_ts": now,
            "peak_price": max(float(snap["last_price"]), price), "trade_id": trade_id,
            "headline": item["title"][:120],
        }
        self.entry_timestamps.append(now)
        self.trade_count += 1
        logger.info("📰 NEWS Entry %s: %.2f€ @ %.6f€ | bullish %.2f | %s", pair, value, price, details["bullish"], item["title"][:100])
        self._save_state()
        return {"pair": pair, "amount": value, "price": price}

    async def process_news(self) -> tuple[list[dict], list[dict]]:
        """Neue Meldungen holen, von Jev bewerten lassen, handeln. -> (opened, closed)."""
        opened, closed = [], []
        now = time.time()
        for item in await self.fetch_new_items():
            age = now - item["published_ts"]
            if age > self.max_headline_age_sec or age < -300:
                continue
            answers = await self.jev.ask(build_state(item, now), build_questions())
            if answers is None:
                continue  # fail-closed
            pair, direction, details = evaluate(answers, self.min_choice_prob, self.min_bullish, self.min_new, self.max_bearish)
            logger.info("📰 NEWS %s → %s/%s %s", item["title"][:80], pair, direction, details)
            if direction == "bearish" and pair in self.portfolio:
                closed += await self.manage_positions({pair})
            elif direction == "bullish":
                result = await self.open_position(pair, item, details)
                if result:
                    opened.append(result)
        return opened, closed

    # ----------------------------------------------------------------- equity
    async def equity(self) -> dict:
        unrealized = mtm = 0.0
        fee = config.CRYPTO_TAKER_FEE_RATE
        for pair, pos in self.portfolio.items():
            entry_cost = float(pos["cost_basis"])
            snap = await self._snapshot(pair)
            if snap:
                value = float(pos["shares"]) * float(snap.get("bid") or snap["last_price"])
                mtm += value - entry_cost * fee - value * fee
                unrealized += value - entry_cost - entry_cost * fee - value * fee
            else:
                mtm += entry_cost
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": self.capital_remaining + mtm,
            "cash_eur": self.capital_remaining,
            "open_positions": len(self.portfolio),
            "unrealized_pnl_eur": unrealized,
            "realized_pnl_eur": realized,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **await self.equity())
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 News-Bot gestartet [PAPER] | Budget %.2f€ | Assets %s", self.initial_capital_eur, ",".join(ASSETS))
        while True:
            try:
                await self.manage_positions()
                await self.process_news()
                await self.maybe_snapshot()
            except Exception as e:
                logger.exception("⚠️ News-Loop-Fehler (%s) – weiter in 60s", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(self.poll_interval_sec)
