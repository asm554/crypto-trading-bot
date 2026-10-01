"""Prediction-Market Forschungsbot — Der Orakler.

REIN PAPER, nur lesende Polymarket-Endpunkte (Gamma API, ohne Konto/Key/Wallet).
Es gibt weder Order-Code noch Wallet-/Auth-Zugriff, und das soll so bleiben:
Polymarket ist für Deutschland nicht zugelassen (siehe CLAUDE.md/Recherche).

Ablauf: aktive Binärmärkte laden, passende Schlagzeilen aus den News-Feeds als
Kontext suchen, Jev die Ja-Wahrscheinlichkeit UNABHÄNGIG vom Marktpreis schätzen
lassen (Preis wird Jev nicht gezeigt) und bei großer Abweichung eine Paper-
Position zum Ask eröffnen. Ohne passenden News-Kontext wird nie gehandelt, denn
ohne Zusatzinformation hat das Modell gegenüber dem Markt keinen Vorteil.
Jev ist Signalquelle und deshalb fail-closed.
"""

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from polybot import paper_db as paper_db_module
from polybot.news_strategy import FEEDS, MAX_FEED_BYTES, parse_feed
from polybot.paper_db import log_equity_snapshot, log_paper_trade, resolve_trade

logger = logging.getLogger(__name__)
PREFIX = "ORA_"
BOT_KEY = "oracle"
GAMMA = "https://gamma-api.polymarket.com"
MIN_POSITION = 1.0
STOPWORDS = frozenset(
    "will with that this from have been after before above below over under between "
    "than into about their there which would could should what when does price market "
    "reach hit end".split()
)
QUESTION = {
    "type": "noul",
    "instructions": (
        "Based only on the information in `recent_headlines` and your own knowledge as of `today`, "
        "the event described in `market.question` will resolve Yes."
    ),
}


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOPWORDS}


def match_headlines(question: str, items: list[dict], now: float, max_age_sec: float = 7 * 86400, limit: int = 5) -> list[dict]:
    qk = keywords(question)
    scored = []
    for it in items:
        if not (0 <= now - it["published_ts"] <= max_age_sec):
            continue
        overlap = len(qk & keywords(it["title"]))
        if overlap >= 2:
            scored.append((overlap, it["published_ts"], it))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in scored[:limit]]


def parse_market(m: dict) -> dict | None:
    """Gamma-Markt -> normalisierter Binärmarkt oder None."""
    try:
        outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
        if [str(o).lower() for o in outcomes] != ["yes", "no"]:
            return None
        yes_bid, yes_ask = float(m["bestBid"]), float(m["bestAsk"])
        end = datetime.fromisoformat(str(m["endDate"]).replace("Z", "+00:00")).timestamp()
        prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
        return {
            "id": str(m["id"]), "question": str(m["question"]), "description": str(m.get("description") or "")[:500],
            "end_ts": end, "yes_bid": yes_bid, "yes_ask": yes_ask,
            "yes_price": float(prices[0]),
            "volume24h": float(m.get("volume24hr") or 0.0),
            "closed": bool(m.get("closed")), "accepting": bool(m.get("acceptingOrders", True)),
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def side_quotes(mk: dict, side: str) -> tuple[float, float]:
    """(bid, ask) des gehandelten Outcome-Tokens. No = 1 - Yes (gespiegelt)."""
    if side == "YES":
        return mk["yes_bid"], mk["yes_ask"]
    return 1.0 - mk["yes_ask"], 1.0 - mk["yes_bid"]


class OracleBot:
    def __init__(
        self,
        jev,
        initial_capital: float = 500.0,
        scan_interval_sec: int = 600,
        min_volume24h: float = 20000.0,
        max_spread: float = 0.04,
        min_price: float = 0.10,
        max_price: float = 0.90,
        min_hours_to_end: float = 6.0,
        max_days_to_end: float = 30.0,
        min_edge: float = 0.15,
        rejudge_after_sec: int = 6 * 3600,
        max_judgements_per_cycle: int = 10,
        position_size: float = 10.0,
        max_open_positions: int = 8,
        max_trades_per_day: int = 5,
        stop_loss_frac: float = 0.4,
        take_profit_edge_frac: float = 0.6,
        taker_fee_rate: float = 0.07,
        account_loss_limit_pct: float = 15.0,
        paper_mode: bool = True,
        snapshot_interval_sec: int = 3600,
        feeds: tuple = FEEDS,
    ):
        self.jev = jev
        self.initial_capital = float(initial_capital)
        self.capital_remaining = float(initial_capital)
        self.scan_interval_sec = int(scan_interval_sec)
        self.min_volume24h = float(min_volume24h)
        self.max_spread = float(max_spread)
        self.min_price = float(min_price)
        self.max_price = float(max_price)
        self.min_hours_to_end = float(min_hours_to_end)
        self.max_days_to_end = float(max_days_to_end)
        self.min_edge = float(min_edge)
        self.rejudge_after_sec = int(rejudge_after_sec)
        self.max_judgements_per_cycle = int(max_judgements_per_cycle)
        self.position_size = float(position_size)
        self.max_open_positions = int(max_open_positions)
        self.max_trades_per_day = int(max_trades_per_day)
        self.stop_loss_frac = float(stop_loss_frac)
        self.take_profit_edge_frac = float(take_profit_edge_frac)
        self.taker_fee_rate = float(taker_fee_rate)
        self.account_loss_limit_pct = float(account_loss_limit_pct)
        self.paper_mode = bool(paper_mode)
        self.snapshot_interval_sec = int(snapshot_interval_sec)
        self.feeds = tuple(feeds)
        if not self.paper_mode:
            logger.warning("Oracle live mode is intentionally not implemented")
            raise NotImplementedError("OracleBot is paper-only")

        data_dir = Path(paper_db_module.DB_PATH).resolve().parent
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "oracle_state.json"
        self.db_path = Path(paper_db_module.DB_PATH).resolve()
        self.portfolio: dict[str, dict] = {}
        self.judged: dict[str, float] = {}
        self.entry_timestamps: list[float] = []
        self.last_scan = 0.0
        self.last_snapshot = 0.0
        self.trade_count = 0
        self.headlines: list[dict] = []
        self._load_state_or_rebuild()

    # ------------------------------------------------------------------ state
    def _save_state(self) -> None:
        payload = {
            "capital_remaining": round(self.capital_remaining, 8), "portfolio": self.portfolio,
            "judged": self.judged, "entry_timestamps": self.entry_timestamps,
            "last_scan": self.last_scan, "last_snapshot": self.last_snapshot,
            "trade_count": self.trade_count, "updated_at": time.time(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(self.state_path)

    def _load_state_or_rebuild(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                self.capital_remaining = max(0.0, float(raw.get("capital_remaining", self.initial_capital)))
                self.portfolio = raw.get("portfolio") or {}
                self.judged = {k: float(v) for k, v in (raw.get("judged") or {}).items()}
                self.entry_timestamps = [float(t) for t in raw.get("entry_timestamps") or []]
                self.last_scan = float(raw.get("last_scan", 0.0))
                self.last_snapshot = float(raw.get("last_snapshot", 0.0))
                self.trade_count = int(raw.get("trade_count", 0))
                ids = {int(p.get("trade_id") or 0) for p in self.portfolio.values() if int(p.get("trade_id") or 0) > 0}
                if ids != paper_db_module.get_open_trade_ids_by_prefix_sync(PREFIX):
                    raise ValueError("State und offenes ORA-Ledger weichen ab")
                return
            except Exception as e:
                logger.warning("Oracle state kaputt (%s) – rebuild aus DB", e)
        self._rebuild_state_from_db()
        self._save_state()

    def _rebuild_state_from_db(self) -> None:
        self.capital_remaining = self.initial_capital
        self.portfolio = {}
        self.trade_count = 0
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
        for row in rows:
            key = str(row["market_question"]).removeprefix(PREFIX)  # "{market_id}:{YES|NO}"
            size, price = float(row["size"] or 0.0), float(row["price"] or 0.0)
            if size <= 0 or price <= 0:
                continue
            self.trade_count += 1
            if row["resolved_at"] is None:
                open_cost += size * price
                self.portfolio[key] = {
                    "shares": size, "cost_basis": size * price, "entry_price": price,
                    "entry_ts": float(row["timestamp"] or time.time()),
                    "needs_recovery_exit": True, "trade_id": int(row["id"]),
                }
            else:
                realized += float(row["real_pnl"] or 0.0)
        self.capital_remaining = max(0.0, self.initial_capital - open_cost + realized)

    # ------------------------------------------------------------------- data
    async def _get_json(self, session: aiohttp.ClientSession, url: str, params: dict | None = None):
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def fetch_markets(self) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                raw = await self._get_json(session, f"{GAMMA}/markets", {
                    "limit": 100, "active": "true", "closed": "false",
                    "order": "volume24hr", "ascending": "false",
                })
        except Exception as exc:
            logger.warning("ORA: Gamma-Abruf fehlgeschlagen (%s)", exc)
            return []
        return [mk for mk in (parse_market(m) for m in raw) if mk]

    async def fetch_market(self, market_id: str) -> dict | None:
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                return parse_market(await self._get_json(session, f"{GAMMA}/markets/{market_id}"))
        except Exception as exc:
            logger.warning("ORA: Markt %s nicht abrufbar (%s)", market_id, exc)
            return None

    async def refresh_headlines(self) -> None:
        timeout = aiohttp.ClientTimeout(total=15)
        collected: dict[str, dict] = {}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async def one(url):
                try:
                    async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                        resp.raise_for_status()
                        body = b""
                        async for chunk in resp.content.iter_chunked(65536):
                            body += chunk
                            if len(body) > MAX_FEED_BYTES:
                                break
                    return parse_feed(body, url)
                except Exception as exc:
                    logger.warning("ORA: Feed %s fehlgeschlagen (%s)", url, exc)
                    return []
            for items in await asyncio.gather(*(one(u) for u in self.feeds)):
                for it in items:
                    collected[it["id"]] = it
        if collected:
            self.headlines = sorted(collected.values(), key=lambda i: i["published_ts"], reverse=True)[:300]

    # ---------------------------------------------------------------- trading
    def _fee(self, shares: float, price: float) -> float:
        return shares * self.taker_fee_rate * price * (1 - price)

    def _entries_today(self, now: float) -> int:
        self.entry_timestamps = [t for t in self.entry_timestamps if now - t < 86400]
        return len(self.entry_timestamps)

    def candidate_ok(self, mk: dict, now: float) -> bool:
        if mk["closed"] or not mk["accepting"] or mk["volume24h"] < self.min_volume24h:
            return False
        if mk["yes_ask"] - mk["yes_bid"] > self.max_spread or not (0 < mk["yes_bid"] <= mk["yes_ask"] < 1):
            return False
        if not (self.min_price <= mk["yes_price"] <= self.max_price):
            return False
        hours = (mk["end_ts"] - now) / 3600
        return self.min_hours_to_end <= hours <= self.max_days_to_end * 24

    async def judge(self, mk: dict, headlines: list[dict], now: float) -> float | None:
        state = {
            "today": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"),
            "market": {
                "question": mk["question"], "description": mk["description"],
                "end_date": datetime.fromtimestamp(mk["end_ts"], timezone.utc).strftime("%Y-%m-%d"),
            },
            "recent_headlines": [
                {"title": h["title"], "summary": h["summary"], "source": h["source"],
                 "days_ago": round((now - h["published_ts"]) / 86400, 1)}
                for h in headlines
            ],
        }
        answers = await self.jev.ask(state, {"resolves_yes": QUESTION})
        try:
            p = float(answers["resolves_yes"]["noul"])
        except (TypeError, KeyError, ValueError):
            return None
        return p if 0.0 <= p <= 1.0 else None

    async def open_position(self, mk: dict, side: str, p_side: float, edge: float, now: float) -> dict | None:
        key = f"{mk['id']}:{side}"
        if key in self.portfolio or len(self.portfolio) >= self.max_open_positions:
            return None
        if f"{mk['id']}:{'NO' if side == 'YES' else 'YES'}" in self.portfolio:
            return None  # nie beide Seiten desselben Marktes
        if self._entries_today(now) >= self.max_trades_per_day:
            return None
        eq = await self.equity()
        if eq["equity_eur"] <= self.initial_capital * (1 - self.account_loss_limit_pct / 100):
            logger.info("⏭️ ORA: Kontoverlust-Limit erreicht – keine neuen Einstiege")
            return None
        _bid, ask = side_quotes(mk, side)
        value = min(self.position_size, self.capital_remaining)
        if value < MIN_POSITION or ask <= 0:
            return None
        shares = value / ask
        trade_id = await log_paper_trade(f"{PREFIX}{key}", "buy", shares, ask, edge, "paper")
        self.capital_remaining -= value
        self.portfolio[key] = {
            "shares": shares, "cost_basis": value, "entry_price": ask, "entry_ts": now,
            "trade_id": trade_id, "jev_p": p_side, "edge": edge, "question": mk["question"][:120],
            "end_ts": mk["end_ts"],
        }
        self.entry_timestamps.append(now)
        self.trade_count += 1
        logger.info("🔮 ORA Entry %s: %.2f @ %.3f | Jev %.2f Edge %+0.2f | %s", key, value, ask, p_side, edge, mk["question"][:80])
        self._save_state()
        return {"key": key, "amount": value, "price": ask, "edge": edge}

    async def scan(self) -> list[dict]:
        now = time.time()
        if now - self.last_scan < self.scan_interval_sec:
            return []
        self.last_scan = now
        await self.refresh_headlines()
        if not self.headlines:
            self._save_state()
            return []
        opened, judged_now = [], 0
        for mk in await self.fetch_markets():
            if judged_now >= self.max_judgements_per_cycle:
                break
            if not self.candidate_ok(mk, now) or now - self.judged.get(mk["id"], 0.0) < self.rejudge_after_sec:
                continue
            context = match_headlines(mk["question"], self.headlines, now)
            if not context:
                continue  # ohne Zusatzinformation kein Vorteil gegenüber dem Markt
            p_yes = await self.judge(mk, context, now)
            judged_now += 1
            if p_yes is None:
                continue  # fail-closed, nicht als beurteilt markieren
            self.judged[mk["id"]] = now
            yes_edge = p_yes - mk["yes_ask"]
            no_edge = (1 - p_yes) - side_quotes(mk, "NO")[1]
            side, p_side, edge = ("YES", p_yes, yes_edge) if yes_edge >= no_edge else ("NO", 1 - p_yes, no_edge)
            logger.info("🔮 ORA %s | Jev %.2f vs Markt %.2f/%.2f → %s %+0.2f", mk["question"][:70], p_yes, mk["yes_bid"], mk["yes_ask"], side, edge)
            if edge >= self.min_edge:
                result = await self.open_position(mk, side, p_side, edge, now)
                if result:
                    opened.append(result)
        self.judged = {k: v for k, v in self.judged.items() if now - v < 7 * 86400}
        self._save_state()
        return opened

    async def _close(self, key: str, exit_price: float, reason: str, exit_fee: float) -> dict | None:
        pos = self.portfolio[key]
        shares = float(pos["shares"])
        cost = float(pos["cost_basis"])
        real_pnl = shares * exit_price - cost - self._fee(shares, float(pos["entry_price"])) - exit_fee
        if not await resolve_trade(int(pos["trade_id"]), exit_price, round(real_pnl, 6)):
            self._rebuild_state_from_db()
            self._save_state()
            return None
        self.capital_remaining += cost + real_pnl
        self.portfolio.pop(key, None)
        logger.info("✅ ORA Exit %s: %s @ %.3f | PnL %+0.4f", key, reason, exit_price, real_pnl)
        self._save_state()
        return {"key": key, "reason": reason, "pnl": real_pnl}

    async def manage_positions(self) -> list[dict]:
        closed = []
        for key in list(self.portfolio):
            pos = self.portfolio[key]
            market_id, side = key.split(":")
            mk = await self.fetch_market(market_id)
            if not mk:
                continue
            if mk["closed"] and (mk["yes_price"] >= 0.99 or mk["yes_price"] <= 0.01):
                won = (mk["yes_price"] >= 0.99) == (side == "YES")
                result = await self._close(key, 1.0 if won else 0.0, "resolved_win" if won else "resolved_loss", 0.0)
            elif mk["closed"]:
                continue  # geschlossen, aber noch nicht aufgelöst: abwarten
            else:
                bid, _ask = side_quotes(mk, side)
                entry = float(pos["entry_price"])
                reason = None
                if pos.get("needs_recovery_exit"):
                    reason = "state_recovery_exit"
                elif bid <= entry * (1 - self.stop_loss_frac):
                    reason = "stop_loss"
                elif pos.get("edge") and bid >= entry + self.take_profit_edge_frac * float(pos["edge"]):
                    reason = "take_profit"
                if not reason:
                    continue
                result = await self._close(key, bid, reason, self._fee(float(pos["shares"]), bid))
            if result:
                closed.append(result)
        return closed

    # ----------------------------------------------------------------- equity
    async def equity(self) -> dict:
        unrealized = mtm = 0.0
        for key, pos in self.portfolio.items():
            cost = float(pos["cost_basis"])
            mk = await self.fetch_market(key.split(":")[0])
            if mk:
                bid, _ = side_quotes(mk, key.split(":")[1])
                shares = float(pos["shares"])
                value = shares * bid - self._fee(shares, float(pos["entry_price"])) - self._fee(shares, bid)
                mtm += value
                unrealized += value - cost
            else:
                mtm += cost
        realized = await paper_db_module.get_realized_pnl_by_prefix(PREFIX)
        return {
            "equity_eur": self.capital_remaining + mtm, "cash_eur": self.capital_remaining,
            "open_positions": len(self.portfolio), "unrealized_pnl_eur": unrealized, "realized_pnl_eur": realized,
        }

    async def maybe_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_snapshot < self.snapshot_interval_sec:
            return
        await log_equity_snapshot(BOT_KEY, **await self.equity())
        self.last_snapshot = now
        self._save_state()

    async def run(self) -> None:
        logger.info("🤖 Oracle-Bot gestartet [PAPER, nur lesend] | Budget %.2f", self.initial_capital)
        while True:
            try:
                await self.manage_positions()
                await self.scan()
                await self.maybe_snapshot()
            except Exception as e:
                logger.exception("⚠️ Oracle-Loop-Fehler (%s) – weiter in 60s", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(60)
