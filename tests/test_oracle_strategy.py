import asyncio
import time

import pytest

import polybot.oracle_strategy as oracle
import polybot.paper_db as paper_db
from polybot.oracle_strategy import OracleBot, match_headlines, parse_market, side_quotes


def _raw(id="1", question="Will Bitcoin reach 120000 in October?", bid=0.40, ask=0.42, closed=False, price=0.41, end_days=10, vol=50000):
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + end_days * 86400))
    return {"id": id, "question": question, "outcomes": '["Yes","No"]', "outcomePrices": f'["{price}","{1 - price}"]',
            "bestBid": bid, "bestAsk": ask, "endDate": end, "volume24hr": vol, "closed": closed,
            "acceptingOrders": True, "description": "d"}


def _hl(title, age_days=0.5):
    return {"id": title, "title": title, "summary": "", "source": "s", "published_ts": time.time() - age_days * 86400}


class FakeJev:
    def __init__(self, p):
        self.p, self.calls = p, []

    async def ask(self, state, questions):
        self.calls.append(state)
        return None if self.p is None else {"resolves_yes": {"type": "noul", "noul": self.p}}


def _bot(tmp_path, monkeypatch, p, markets, **kw):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    bot = OracleBot(jev=FakeJev(p), feeds=(), **kw)
    bot.state_path, bot.db_path = tmp_path / "oracle_state.json", tmp_path / "paper_trades.db"
    bot.headlines = [_hl("Bitcoin could reach 120000 as ETF inflows surge")]
    state = {"markets": {m["id"]: m for m in markets}}

    async def fetch_markets():
        return [parse_market(m) for m in state["markets"].values()]

    async def fetch_market(mid):
        return parse_market(state["markets"][mid])

    async def refresh():
        return None

    monkeypatch.setattr(bot, "fetch_markets", fetch_markets)
    monkeypatch.setattr(bot, "fetch_market", fetch_market)
    monkeypatch.setattr(bot, "refresh_headlines", refresh)
    return bot, state


def test_parse_market_and_no_side_mirroring():
    mk = parse_market(_raw(bid=0.40, ask=0.42))
    assert mk["yes_bid"] == 0.40 and side_quotes(mk, "YES") == (0.40, 0.42)
    bid, ask = side_quotes(mk, "NO")
    assert bid == pytest.approx(0.58) and ask == pytest.approx(0.60)


def test_parse_market_rejects_non_binary_and_garbage():
    bad = _raw()
    bad["outcomes"] = '["A","B"]'
    assert parse_market(bad) is None and parse_market({}) is None


def test_match_headlines_needs_two_keywords_and_recency():
    items = [_hl("Bitcoin could reach 120000 soon"), _hl("Dogecoin news"), _hl("Bitcoin reach 120000", age_days=30)]
    got = match_headlines("Will Bitcoin reach 120000 in October?", items, time.time())
    assert [g["title"] for g in got] == ["Bitcoin could reach 120000 soon"]


def test_oracle_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        OracleBot(jev=None, paper_mode=False)


def test_no_context_means_no_judgement(monkeypatch, tmp_path):
    bot, _ = _bot(tmp_path, monkeypatch, 0.9, [_raw(question="Will the Lakers win tonight?")])

    async def scenario():
        await paper_db.init_db()
        return await bot.scan()

    assert asyncio.run(scenario()) == [] and bot.jev.calls == []


def test_jev_never_sees_market_price(monkeypatch, tmp_path):
    bot, _ = _bot(tmp_path, monkeypatch, 0.9, [_raw()])

    async def scenario():
        await paper_db.init_db()
        await bot.scan()

    asyncio.run(scenario())
    assert len(bot.jev.calls) == 1
    flat = str(bot.jev.calls[0])
    assert "0.42" not in flat and "0.41" not in flat and "bid" not in flat.lower()


def test_large_edge_buys_yes(monkeypatch, tmp_path):
    bot, _ = _bot(tmp_path, monkeypatch, 0.80, [_raw(bid=0.40, ask=0.42)])

    async def scenario():
        await paper_db.init_db()
        return await bot.scan()

    opened = asyncio.run(scenario())
    assert len(opened) == 1 and opened[0]["key"] == "1:YES" and opened[0]["price"] == 0.42


def test_large_negative_view_buys_no(monkeypatch, tmp_path):
    bot, _ = _bot(tmp_path, monkeypatch, 0.10, [_raw(bid=0.40, ask=0.42)])

    async def scenario():
        await paper_db.init_db()
        return await bot.scan()

    opened = asyncio.run(scenario())
    assert opened[0]["key"] == "1:NO" and opened[0]["price"] == pytest.approx(0.60)


def test_small_edge_and_jev_failure_do_not_trade(monkeypatch, tmp_path):
    for p in (0.45, None):
        bot, _ = _bot(tmp_path, monkeypatch, p, [_raw()])

        async def scenario():
            await paper_db.init_db()
            return await bot.scan()

        assert asyncio.run(scenario()) == [] and bot.portfolio == {}
        (tmp_path / "paper_trades.db").unlink(missing_ok=True)


def test_filters_reject_wide_spread_extreme_price_and_low_volume():
    bot = OracleBot.__new__(OracleBot)
    bot.min_volume24h, bot.max_spread, bot.min_price, bot.max_price = 20000, 0.04, 0.1, 0.9
    bot.min_hours_to_end, bot.max_days_to_end = 6, 30
    now = time.time()
    assert bot.candidate_ok(parse_market(_raw()), now)
    assert not bot.candidate_ok(parse_market(_raw(bid=0.30, ask=0.42)), now)
    assert not bot.candidate_ok(parse_market(_raw(price=0.97)), now)
    assert not bot.candidate_ok(parse_market(_raw(vol=100)), now)
    assert not bot.candidate_ok(parse_market(_raw(end_days=90)), now)


def test_resolution_win_and_loss_pnl(monkeypatch, tmp_path):
    bot, state = _bot(tmp_path, monkeypatch, 0.80, [_raw(bid=0.40, ask=0.42)])

    async def scenario():
        await paper_db.init_db()
        await bot.scan()
        state["markets"]["1"] = _raw(closed=True, price=1.0, bid=0.99, ask=1.0)
        return await bot.manage_positions()

    closed = asyncio.run(scenario())
    assert closed[0]["reason"] == "resolved_win"
    # 10 / 0.42 Shares * 1.0 - 10 - Einstiegsgebühr
    assert closed[0]["pnl"] == pytest.approx(10 / 0.42 - 10 - (10 / 0.42) * 0.07 * 0.42 * 0.58, rel=1e-6)

    sub = tmp_path / "second"
    sub.mkdir()
    bot2, state2 = _bot(sub, monkeypatch, 0.80, [_raw(id="2", bid=0.40, ask=0.42)])

    async def scenario2():
        await paper_db.init_db()
        await bot2.scan()
        state2["markets"]["2"] = _raw(id="2", closed=True, price=0.0, bid=0.0, ask=0.01)
        return await bot2.manage_positions()

    lost = asyncio.run(scenario2())
    assert lost[0]["reason"] == "resolved_loss" and lost[0]["pnl"] < -10


def test_stop_loss(monkeypatch, tmp_path):
    bot, state = _bot(tmp_path, monkeypatch, 0.80, [_raw(bid=0.40, ask=0.42)])

    async def scenario():
        await paper_db.init_db()
        await bot.scan()
        state["markets"]["1"] = _raw(bid=0.20, ask=0.22, price=0.21)
        return await bot.manage_positions()

    assert asyncio.run(scenario())[0]["reason"] == "stop_loss"
