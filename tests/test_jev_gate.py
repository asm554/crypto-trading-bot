import asyncio

import pytest

import polybot.paper_db as paper_db
import polybot.surfer_strategy as surfer_strategy
from polybot.jev_gate import JevGate, build_state
from polybot.surfer_strategy import SurferBot
from test_surfer_strategy import (
    _bind_bot_to_tmp_storage,
    _bot_kwargs,
    _build_ohlc_rows,
    _valid_ticker,
)


def _gate(post, **kwargs):
    gate = JevGate(api_key="test-key", **kwargs)
    gate._post = post
    return gate


def _answer(prob):
    return {"answers": {"sustained_breakout": {"type": "noul", "noul": prob}}}


def test_build_state_contains_key_facts():
    state = build_state("SOLEUR", 20.4, 2.5, 4, 15.0, 12.0, 12.2, 5.0, 3.3)
    assert state["asset"] == "SOLEUR"
    sig = state["signal"]
    assert sig["trend_change_pct"] == 2.5
    assert sig["volume_vs_average"] == 5.0
    assert sig["price_above_breakout_pct"] == pytest.approx(67.21, abs=0.01)
    assert sig["atr_pct_of_price"] == pytest.approx(16.18, abs=0.01)


def test_gate_allows_above_threshold():
    async def post(_payload):
        return _answer(0.8)

    assert asyncio.run(_gate(post, min_prob=0.6).allows_entry("s")) == (True, 0.8)


def test_gate_vetoes_below_threshold():
    async def post(_payload):
        return _answer(0.3)

    assert asyncio.run(_gate(post, min_prob=0.6).allows_entry("s")) == (False, 0.3)


def test_gate_sends_noul_question_with_state():
    seen = {}

    async def post(payload):
        seen.update(payload)
        return _answer(0.9)

    asyncio.run(_gate(post).allows_entry({"asset": "SOLEUR"}))
    assert seen["state"] == {"asset": "SOLEUR"}
    assert seen["model"] == "jev-latest"
    assert seen["questions"]["sustained_breakout"]["type"] == "noul"


@pytest.mark.parametrize("failure", ["raise", "garbage", "out_of_range"])
def test_gate_fails_open(failure):
    async def post(_payload):
        if failure == "raise":
            raise TimeoutError("timeout")
        if failure == "garbage":
            return {"answers": {}}
        return _answer(1.7)

    assert asyncio.run(_gate(post).allows_entry("s")) == (True, None)


def test_gate_without_api_key_fails_open(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert asyncio.run(JevGate().allows_entry("s")) == (True, None)


def _scan(monkeypatch, tmp_path, gate):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))

    async def fake_ticker(_pairs):
        return {"SOLEUR": _valid_ticker(last="20.3", bid="20.2", ask="20.4")}

    async def fake_ohlc(_pair, _interval=60):
        return _build_ohlc_rows()

    monkeypatch.setattr(surfer_strategy, "fetch_ticker_data", fake_ticker)
    monkeypatch.setattr(surfer_strategy, "fetch_ohlc", fake_ohlc)

    async def scenario():
        await paper_db.init_db()
        bot = _bind_bot_to_tmp_storage(SurferBot(**_bot_kwargs(jev_gate=gate)), tmp_path)
        return await bot.scan_entries(), bot

    return asyncio.run(scenario())


def test_surfer_skips_entry_on_jev_veto(monkeypatch, tmp_path):
    async def post(_payload):
        return _answer(0.1)

    opened, bot = _scan(monkeypatch, tmp_path, _gate(post))
    assert opened == [] and bot.portfolio == {}


def test_surfer_enters_when_jev_confirms(monkeypatch, tmp_path):
    async def post(_payload):
        return _answer(0.9)

    opened, bot = _scan(monkeypatch, tmp_path, _gate(post))
    assert len(opened) == 1 and "SOLEUR" in bot.portfolio


def test_surfer_enters_when_jev_down(monkeypatch, tmp_path):
    async def post(_payload):
        raise ConnectionError("down")

    opened, _bot = _scan(monkeypatch, tmp_path, _gate(post))
    assert len(opened) == 1


def test_ask_returns_answers_and_fails_closed():
    async def ok(_payload):
        return {"answers": {"q": {"type": "noul", "noul": 0.5}}}

    async def boom(_payload):
        raise TimeoutError("t")

    assert asyncio.run(_gate(ok).ask({"a": 1}, {"q": {}})) == {"q": {"type": "noul", "noul": 0.5}}
    assert asyncio.run(_gate(boom).ask({"a": 1}, {"q": {}})) is None
    assert asyncio.run(JevGate(api_key="").ask("s", {})) is None


def test_gate_sends_shadow_questions_in_one_call_but_decides_on_main_only():
    seen = {}

    async def post(payload):
        seen.update(payload)
        return {"model": "jev-1.13.0", "answers": {
            "sustained_breakout": {"type": "noul", "noul": 0.8},
            "momentum_already_spent": {"type": "noul", "noul": 0.99},
            "volume_is_climax": {"type": "noul", "noul": 0.99}}}

    assert asyncio.run(_gate(post, min_prob=0.6).allows_entry("s")) == (True, 0.8)
    assert {"sustained_breakout", "momentum_already_spent", "volume_is_climax"} == set(seen["questions"])


def test_shadow_log_records_raw_answers_model_and_decision(tmp_path):
    import json

    async def post(_payload):
        return {"model": "jev-1.13.0", "answers": {
            "sustained_breakout": {"type": "noul", "noul": 0.3},
            "momentum_already_spent": {"type": "noul", "noul": 0.7}}}

    log = tmp_path / "shadow.jsonl"
    gate = _gate(post, min_prob=0.6, shadow_log=log)
    asyncio.run(gate.allows_entry({"asset": "SOLEUR"}))
    row = json.loads(log.read_text().splitlines()[0])
    assert row["model"] == "jev-1.13.0" and row["allowed"] is False and row["prob"] == 0.3
    assert row["answers"]["momentum_already_spent"]["noul"] == 0.7
    assert row["state"] == {"asset": "SOLEUR"}


def test_shadow_log_records_failures_and_is_off_by_default(tmp_path):
    import json

    async def boom(_payload):
        raise TimeoutError("timeout")

    log = tmp_path / "s.jsonl"
    assert asyncio.run(_gate(boom, shadow_log=log).allows_entry("s")) == (True, None)
    row = json.loads(log.read_text().splitlines()[0])
    assert row["allowed"] is True and "timeout" in row["error"]
    assert _gate(boom).shadow_log is None


def test_unwritable_shadow_log_never_stops_the_gate(tmp_path):
    async def post(_payload):
        return _answer(0.9)

    blocker = tmp_path / "file"
    blocker.write_text("x")
    gate = _gate(post, shadow_log=blocker / "sub" / "log.jsonl")  # Elternpfad ist eine Datei
    assert asyncio.run(gate.allows_entry("s")) == (True, 0.9)
