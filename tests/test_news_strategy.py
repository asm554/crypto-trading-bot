import asyncio
import time
from email.utils import format_datetime
from datetime import datetime, timezone

import pytest

import polybot.news_strategy as news
import polybot.paper_db as paper_db
from polybot.news_strategy import NewsBot, evaluate, parse_feed
from test_surfer_strategy import _valid_ticker


def _rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>http://x/{i}</link><description>d</description>"
        f"<pubDate>{format_datetime(datetime.fromtimestamp(ts, timezone.utc))}</pubDate></item>"
        for i, (t, ts) in enumerate(items)
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def _answers(asset="SOL", asset_p=0.9, is_new=0.9, bullish=0.9, bearish=0.05):
    return {
        "asset": {"type": "choice", "choice": asset, "confidence": 0.8, "probabilities": {asset: asset_p}},
        "is_new": {"type": "noul", "noul": is_new},
        "bullish": {"type": "noul", "noul": bullish},
        "bearish": {"type": "noul", "noul": bearish},
    }


class FakeJev:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def ask(self, state, questions):
        self.calls.append(state)
        return self.answers


def _bot(tmp_path, monkeypatch, jev, **kw):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    bot = NewsBot(jev=jev, feeds=(), **kw)
    bot.state_path = tmp_path / "news_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.primed = True
    return bot


def _patch_market(monkeypatch, last="100"):
    price = {"v": last}

    async def fake_ticker(_pairs):
        v = price["v"]
        t = _valid_ticker(last=v, bid=str(float(v) * 0.999), ask=str(float(v) * 1.001))
        return {"SOLEUR": t, "XBTEUR": t}

    monkeypatch.setattr(news, "fetch_ticker_data", fake_ticker)
    return price


def _feed_with(monkeypatch, bot, items):
    async def fake_fetch(_session, _url):
        return parse_feed(_rss(items), "t")

    bot.feeds = ("t",)
    monkeypatch.setattr(bot, "_fetch_feed", fake_fetch)


# ---- Parsing / Sicherheit
def test_parse_feed_extracts_items():
    items = parse_feed(_rss([("SOL ETF approved", time.time())]), "src")
    assert len(items) == 1 and items[0]["title"] == "SOL ETF approved" and len(items[0]["id"]) == 16


def test_parse_feed_rejects_entity_bombs_and_garbage():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert parse_feed(bomb, "s") == []
    assert parse_feed(b"not xml", "s") == []
    assert parse_feed(b"x" * 2_000_001, "s") == []


def test_parse_feed_skips_items_without_valid_date():
    xml = b"<rss><channel><item><title>t</title><pubDate>nonsense</pubDate></item></channel></rss>"
    assert parse_feed(xml, "s") == []


# ---- Jev-Auswertung
def test_evaluate_bullish_entry():
    pair, direction, _ = evaluate(_answers(), 0.6, 0.75, 0.7, 0.3)
    assert (pair, direction) == ("SOLEUR", "bullish")


def test_evaluate_with_real_jev_values_at_default_threshold():
    # Gemessene Live-Werte (Test 2026-10-01): eindeutige Meldungen lagen bei 0.74.
    bull = _answers(bullish=0.74, bearish=0.16)
    bear = _answers(asset="BTC", bullish=0.14, bearish=0.74)
    assert evaluate(bull, 0.6, 0.70, 0.7, 0.3)[:2] == ("SOLEUR", "bullish")
    assert evaluate(bear, 0.6, 0.70, 0.7, 0.3)[:2] == ("XBTEUR", "bearish")
    assert evaluate(bull, 0.6, 0.75, 0.7, 0.3)[1] is None


def test_default_min_bullish_is_070():
    import inspect
    assert inspect.signature(NewsBot.__init__).parameters["min_bullish"].default == 0.70


def test_evaluate_btc_and_bearish():
    assert evaluate(_answers(asset="BTC"), 0.6, 0.75, 0.7, 0.3)[0] == "XBTEUR"
    pair, direction, _ = evaluate(_answers(bullish=0.05, bearish=0.9), 0.6, 0.75, 0.7, 0.3)
    assert (pair, direction) == ("SOLEUR", "bearish")


@pytest.mark.parametrize("kw", [dict(asset="none"), dict(asset_p=0.4), dict(is_new=0.2), dict(bullish=0.5), dict(bullish=0.9, bearish=0.6)])
def test_evaluate_no_signal(kw):
    assert evaluate(_answers(**kw), 0.6, 0.75, 0.7, 0.3)[1] is None


def test_evaluate_malformed_answers():
    assert evaluate({}, 0.6, 0.75, 0.7, 0.3)[:2] == (None, None)


# ---- Bot
def test_news_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        NewsBot(jev=None, paper_mode=False)


def test_first_run_primes_and_never_trades_on_backlog(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    jev = FakeJev(_answers())
    bot = _bot(tmp_path, monkeypatch, jev)
    bot.primed = False
    _feed_with(monkeypatch, bot, [("Old news", time.time() - 30)])

    async def scenario():
        await paper_db.init_db()
        return await bot.process_news()

    assert asyncio.run(scenario()) == ([], [])
    assert jev.calls == [] and bot.seen  # gemerkt, aber nicht bewertet


def test_fresh_bullish_headline_opens_once(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    jev = FakeJev(_answers())
    bot = _bot(tmp_path, monkeypatch, jev)
    _feed_with(monkeypatch, bot, [("Solana ETF approved", time.time() - 60)])

    async def scenario():
        await paper_db.init_db()
        first = await bot.process_news()
        second = await bot.process_news()  # gleiche Meldung: Dedup
        return first, second

    (opened, _), (opened2, _) = asyncio.run(scenario())
    assert len(opened) == 1 and opened[0]["price"] == pytest.approx(100.1)
    assert opened2 == [] and len(jev.calls) == 1 and "SOLEUR" in bot.portfolio


def test_stale_headline_is_ignored(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    jev = FakeJev(_answers())
    bot = _bot(tmp_path, monkeypatch, jev, max_headline_age_sec=900)
    _feed_with(monkeypatch, bot, [("Old", time.time() - 3600)])

    async def scenario():
        await paper_db.init_db()
        return await bot.process_news()

    assert asyncio.run(scenario()) == ([], []) and jev.calls == []


def test_jev_failure_is_fail_closed(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    bot = _bot(tmp_path, monkeypatch, FakeJev(None))
    _feed_with(monkeypatch, bot, [("Solana ETF approved", time.time() - 60)])

    async def scenario():
        await paper_db.init_db()
        return await bot.process_news()

    assert asyncio.run(scenario()) == ([], []) and bot.portfolio == {}


def test_daily_limit_and_max_positions(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    bot = _bot(tmp_path, monkeypatch, FakeJev(_answers()), max_trades_per_day=1)

    async def scenario():
        await paper_db.init_db()
        item = {"title": "x", "summary": "", "source": "s", "published_ts": time.time(), "id": "1"}
        d = {"bullish": 0.9}
        a = await bot.open_position("SOLEUR", item, d)
        b = await bot.open_position("XBTEUR", item, d)
        return a, b

    a, b = asyncio.run(scenario())
    assert a is not None and b is None


def test_stop_loss_and_bearish_exit(monkeypatch, tmp_path):
    price = _patch_market(monkeypatch)
    bot = _bot(tmp_path, monkeypatch, FakeJev(_answers()))

    async def scenario():
        await paper_db.init_db()
        item = {"title": "x", "summary": "", "source": "s", "published_ts": time.time(), "id": "1"}
        await bot.open_position("SOLEUR", item, {"bullish": 0.9})
        assert await bot.manage_positions() == []  # Preis unverändert
        bearish = await bot.manage_positions({"SOLEUR"})
        assert bearish[0]["reason"] == "bearish_news"
        await bot.open_position("SOLEUR", item, {"bullish": 0.9})
        price["v"] = "97"  # unter Stop (-1.5%)
        stopped = await bot.manage_positions()
        return stopped

    stopped = asyncio.run(scenario())
    assert stopped[0]["reason"] == "stop_loss" and stopped[0]["pnl"] < 0 and bot.portfolio == {}


def test_time_exit(monkeypatch, tmp_path):
    _patch_market(monkeypatch)
    bot = _bot(tmp_path, monkeypatch, FakeJev(_answers()), max_hold_sec=60)

    async def scenario():
        await paper_db.init_db()
        item = {"title": "x", "summary": "", "source": "s", "published_ts": time.time(), "id": "1"}
        await bot.open_position("SOLEUR", item, {"bullish": 0.9})
        bot.portfolio["SOLEUR"]["entry_ts"] -= 120
        return await bot.manage_positions()

    assert asyncio.run(scenario())[0]["reason"] == "time_exit"
