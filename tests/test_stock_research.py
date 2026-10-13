import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import polybot.stock_research as sr


def _closes(n=300, start=100.0, step=0.2):
    return [start + i * step for i in range(n)]


GOOD_FUND = {
    "name": "Good Corp", "forward_pe": 14, "peg": 0.9, "revenue_growth": 0.18,
    "earnings_growth": 0.25, "debt_to_equity": 30, "current_ratio": 2.0,
    "free_cashflow": 1e9, "roe": 0.3, "profit_margin": 0.25, "gross_margin": 0.6,
}
BAD_FUND = {
    "name": "Bad Corp", "forward_pe": -5, "peg": None, "revenue_growth": -0.1,
    "earnings_growth": -0.3, "debt_to_equity": 400, "current_ratio": 0.6,
    "free_cashflow": -1e8, "roe": -0.1, "profit_margin": -0.05, "gross_margin": 0.1,
}


def test_rsi_extremes():
    assert sr.rsi_wilder(_closes(50)) == 100.0
    assert sr.rsi_wilder(list(reversed(_closes(50)))) < 5
    assert sr.rsi_wilder([1, 2, 3]) is None


def test_technicals_uptrend():
    t = sr.technicals(_closes())
    assert t["sma50"] > t["sma200"]
    assert t["price"] > t["sma200"]
    assert t["mom_12_1_pct"] > 0
    assert t["drawdown_52w_pct"] == 0.0
    assert t["macd"] is not None


def test_score_good_beats_bad():
    tech_up = sr.technicals(_closes())
    tech_down = sr.technicals(list(reversed(_closes())))
    good = sr.score_stock(GOOD_FUND, tech_up)
    bad = sr.score_stock(BAD_FUND, tech_down)
    assert good["total"] > 75
    assert bad["total"] < 25
    assert set(good) == {"bewertung", "wachstum", "bilanz", "qualitaet", "technik", "total"}


def test_missing_fundamentals_are_neutral():
    s = sr.score_stock({}, {})
    assert s["bewertung"] == s["wachstum"] == s["bilanz"] == s["qualitaet"] == sr.NEUTRAL


def test_parse_verdict_takes_last_line():
    text = "Fazit ... früher stand VERDICT: MEIDEN als Beispiel\n\nVERDICT: **KAUFEN**"
    assert sr.parse_verdict(text) == "KAUFEN"
    assert sr.parse_verdict("kein urteil") is None


def test_load_watchlist_strips_comments_and_dupes(tmp_path):
    p = tmp_path / "wl.txt"
    p.write_text("# kommentar\nmsft\nSAP.DE  # walldorf\n\nMSFT\n", encoding="utf-8")
    assert sr.load_watchlist(str(p)) == ["MSFT", "SAP.DE"]


def test_evaluate_last_picks_vs_benchmark():
    picks = [
        {"run": "2026-09-05", "ticker": "OLD", "price": 1.0, "verdict": "KAUFEN"},
        {"run": "2026-10-03", "ticker": "A", "price": 100.0, "verdict": "KAUFEN"},
        {"run": "2026-10-03", "ticker": "B", "price": 50.0, "verdict": "KAUFEN"},
        {"run": "2026-10-03", "ticker": "C", "price": 10.0, "verdict": "MEIDEN"},
        {"run": "2026-10-03", "ticker": "URTH", "price": 100.0, "verdict": None, "benchmark": True},
    ]
    review = sr.evaluate_last_picks(picks, {"A": 110.0, "B": 45.0, "C": 20.0, "URTH": 102.0})
    assert review["run"] == "2026-10-03"
    assert review["buy_avg_pct"] == 0.0  # (+10 % + -10 %) / 2
    assert review["benchmark_pct"] == 2.0
    assert sr.evaluate_last_picks([], {}) is None


def _fake_fetcher(table):
    def fetch(ticker):
        if ticker not in table:
            return None
        fund, closes = table[ticker]
        return {"ticker": ticker, "closes": closes, "fundamentals": fund, "news": [{"title": "x"}]}
    return fetch


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="### Fazit\nGut.\n\nVERDICT: KAUFEN")],
        )


def test_run_ranks_analyzes_top_n_and_saves_picks(tmp_path):
    table = {
        "GOOD": (GOOD_FUND, _closes()),
        "BAD": (BAD_FUND, list(reversed(_closes()))),
        "URTH": ({}, _closes(start=90)),
    }
    client = _FakeClient()
    r = sr.StockResearcher(["BAD", "GOOD", "BROKEN"], top_n=1, fetcher=_fake_fetcher(table), client=client)
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    picks_path = str(tmp_path / "picks.json")
    results, review = asyncio.run(r.run(now=now, picks_path=picks_path))

    assert [x.ticker for x in results] == ["GOOD", "BAD"]
    assert len(client.calls) == 1  # nur Top-N geht an Claude
    call = client.calls[0]
    assert call["model"] == sr.DEFAULT_MODEL
    assert call["thinking"] == {"type": "adaptive"}
    assert results[0].verdict == "KAUFEN" and results[1].verdict is None
    assert review is None

    saved = sr.load_picks(picks_path)
    assert {p["ticker"] for p in saved} == {"GOOD", "URTH"}

    md = sr.build_markdown_report(results, review, 1, now)
    assert "## GOOD" in md and "VERDICT: KAUFEN" in md
    tg = sr.build_telegram_summary(results, review, 1, now)
    assert "🟢 GOOD" in tg and "Rest: BAD" in tg


def test_run_without_llm_makes_no_calls(tmp_path):
    client = _FakeClient()
    r = sr.StockResearcher(["GOOD"], llm_enabled=False, benchmark="", fetcher=_fake_fetcher({"GOOD": (GOOD_FUND, _closes())}), client=client)
    results, _ = asyncio.run(r.run(picks_path=str(tmp_path / "p.json")))
    assert client.calls == [] and results[0].analysis is None


def test_split_message_respects_limit():
    text = "\n".join("x" * 100 for _ in range(100))
    chunks = sr.split_message(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text
