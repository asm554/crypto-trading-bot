import asyncio
import pytest

from polybot.dca_strategy import DCABot, extract_quote, rolling_24h_change_pct
import polybot.dca_strategy as dca_strategy
import polybot.paper_db as paper_db


def test_extract_quote_reads_bid_and_ask():
    data = {"b": ["99.5", "1", "1"], "a": ["100.5", "1", "1"]}
    assert extract_quote(data, 100.0) == (99.5, 100.5)


def test_extract_quote_falls_back_to_last_without_quote():
    # Kraken-Antwort ohne a/b -> Fill = Last (altes Verhalten, kein Spread).
    assert extract_quote({"c": ["100.0"]}, 100.0) == (100.0, 100.0)


def test_extract_quote_falls_back_on_unusable_quote():
    # Verdrehtes Buch (ask < bid) und nicht-positive Preise sind unbrauchbar.
    assert extract_quote({"b": ["101"], "a": ["99"]}, 100.0) == (100.0, 100.0)
    assert extract_quote({"b": ["0"], "a": ["100.5"]}, 100.0) == (100.0, 100.0)
    assert extract_quote({"b": ["abc"], "a": ["100.5"]}, 100.0) == (100.0, 100.0)


def test_rolling_24h_change_pct_uses_close_24_bars_back(monkeypatch):
    # Der lokale Import zeigt auf die echte Funktion; die autouse-Fixture patcht
    # nur das Modul-Attribut dca_strategy.rolling_24h_change_pct.
    dca_strategy._rolling_change_cache.clear()
    closes = [100.0] * 100
    closes[-1] = 110.0            # aktueller Close
    closes[-1 - 24] = 100.0       # Referenz ~24h zurück

    async def fake_closes(_pair, interval_min=60):
        return closes

    monkeypatch.setattr(dca_strategy, "_fetch_ohlc_closes", fake_closes)
    val = asyncio.run(rolling_24h_change_pct("SOLEUR"))
    assert val == pytest.approx(10.0)


def test_rolling_24h_change_pct_returns_none_without_data(monkeypatch):
    dca_strategy._rolling_change_cache.clear()

    async def fake_closes(_pair, interval_min=60):
        return []

    monkeypatch.setattr(dca_strategy, "_fetch_ohlc_closes", fake_closes)
    assert asyncio.run(rolling_24h_change_pct("SOLEUR")) is None


def _bind_bot_to_tmp_storage(bot, tmp_path):
    bot.state_path = tmp_path / "dca_state.json"
    bot.db_path = tmp_path / "paper_trades.db"
    bot.portfolio = {}
    bot.total_invested = 0.0
    bot.capital_remaining = bot.initial_capital_eur
    bot.last_rescan = 0.0
    bot.last_buy = 0.0
    bot.trade_count = 0
    bot.coin_cooldowns = {}
    bot.risk_off_until = 0.0
    return bot


def test_execute_dca_round_deploys_only_one_round_budget(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {
            "XXBTZEUR": {"c": ["50000"]},
            "XETHZEUR": {"c": ["2500"]},
            "SOLEUR": {"c": ["100"]},
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(initial_capital_eur=100.0, top_n=3, rounds_target=10, paper_mode=True),
        tmp_path,
    )
    bot.active_pairs = [
        {"pair": "XBTEUR", "score": 4.0, "change_pct": -2.0, "last_price": 50000.0},
        {"pair": "ETHEUR", "score": 3.0, "change_pct": -1.5, "last_price": 2500.0},
        {"pair": "SOLEUR", "score": 2.0, "change_pct": -1.0, "last_price": 100.0},
    ]

    trades = asyncio.run(bot.execute_dca_round())

    invested = sum(t["amount_eur"] for t in trades)
    assert len(trades) == 3
    assert invested == pytest.approx(10.0, rel=1e-6)
    assert bot.total_invested == pytest.approx(10.0, rel=1e-6)
    assert bot.capital_remaining == pytest.approx(90.0, rel=1e-6)


def test_execute_dca_round_fills_at_ask_not_last(monkeypatch, tmp_path):
    """Gekauft wird zum Ask; der DB-Preis muss der Fill sein, nicht der Last.

    Sonst driftet ``_rebuild_state_from_db`` (size * price) vom investierten
    Betrag weg.
    """
    async def fake_fetch_ticker_data(_pairs):
        # Vollständiges Payload, sonst scheitert _ticker_snapshot und fällt auf
        # active_pairs zurück – dann käme die Quote nie an. Last 100, Bid 98,
        # Ask 102; Open 102 ergibt einen Dip von 1.96% > min_edge_pct (1.0).
        return {
            "SOLEUR": {
                "o": "102",
                "c": ["100", "1.0"],
                "h": ["103", "104"],
                "l": ["97", "96"],
                "v": ["1000", "2000"],
                "p": ["100", "100"],
                "b": ["98", "1", "1"],
                "a": ["102", "1", "1"],
            }
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(initial_capital_eur=100.0, top_n=1, rounds_target=10, paper_mode=True),
        tmp_path,
    )
    bot.active_pairs = [{"pair": "SOLEUR", "score": 2.0, "change_pct": -1.0, "last_price": 100.0}]

    trades = asyncio.run(bot.execute_dca_round())

    assert len(trades) == 1
    assert trades[0]["price"] == pytest.approx(102.0)
    amount = trades[0]["amount_eur"]
    assert trades[0]["coins_bought"] == pytest.approx(amount / 102.0)
    # size * price muss den Einsatz exakt reproduzieren.
    assert trades[0]["coins_bought"] * trades[0]["price"] == pytest.approx(amount)
    assert bot.portfolio["SOLEUR"]["cost_basis"] == pytest.approx(amount)


def test_restore_state_from_db_rebuilds_open_positions_and_cash(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        await paper_db.log_paper_trade("DCA_XBTEUR", "buy", size=0.1, price=100.0, edge=0.02, status="paper")
        await paper_db.log_paper_trade("DCA_ETHEUR", "buy", size=0.05, price=200.0, edge=0.01, status="paper")
        await paper_db.resolve_trade(2, exit_price=210.0, real_pnl=0.42)

        bot = _bind_bot_to_tmp_storage(
            DCABot(initial_capital_eur=100.0, top_n=3, rounds_target=10, paper_mode=True),
            tmp_path,
        )
        await bot.restore_state_from_db()

        assert bot.portfolio["XBTEUR"]["shares"] == pytest.approx(0.1, rel=1e-6)
        assert bot.portfolio["XBTEUR"]["cost_basis"] == pytest.approx(10.0, rel=1e-6)
        assert "ETHEUR" not in bot.portfolio
        assert bot.trade_count == 2
        assert bot.total_invested == pytest.approx(20.0, rel=1e-6)
        assert bot.capital_remaining == pytest.approx(90.42, rel=1e-6)
        assert bot.last_buy > 0

    asyncio.run(scenario())


def test_rolling_and_pair_pnl_ignore_unresolved_trades(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def scenario():
        await paper_db.init_db()
        await paper_db.log_paper_trade("DCA_XBTEUR", "buy", size=0.1, price=100.0, edge=0.02, status="paper")
        await paper_db.log_paper_trade("DCA_XBTEUR", "buy", size=0.1, price=100.0, edge=0.02, status="paper")
        await paper_db.resolve_trade(2, exit_price=95.0, real_pnl=-0.55)

        bot = _bind_bot_to_tmp_storage(
            DCABot(initial_capital_eur=100.0, top_n=1, rounds_target=10, paper_mode=True),
            tmp_path,
        )

        rolling_sum, rolling_count = bot._rolling_real_pnl_stats(9)
        pair_vals = bot._recent_pair_real_pnls("XBTEUR", 3)

        assert rolling_count == 1
        assert rolling_sum == pytest.approx(-0.55, rel=1e-6)
        assert pair_vals == pytest.approx([-0.55], rel=1e-6)

    asyncio.run(scenario())


def test_resolve_due_trades_closes_take_profit_and_returns_cash(monkeypatch, tmp_path):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_db, "DB_PATH", str(db_path))

    async def fake_fetch_ticker_data(_pairs):
        return {
            "XXBTZEUR": {"c": ["103.0"]},
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    async def scenario():
        await paper_db.init_db()
        await paper_db.log_paper_trade("DCA_XBTEUR", "buy", size=0.1, price=100.0, edge=0.02, status="paper")

        bot = _bind_bot_to_tmp_storage(
            DCABot(
                initial_capital_eur=100.0,
                top_n=1,
                rounds_target=10,
                paper_mode=True,
                take_profit_pct=0.02,
                stop_loss_pct=0.05,
                max_hold_sec=86400,
            ),
            tmp_path,
        )
        await bot.restore_state_from_db()
        resolved = await bot.resolve_due_trades()

        assert len(resolved) == 1
        event = resolved[0]
        assert event["reason"] == "take_profit"
        assert event["pair"] == "XBTEUR"
        assert event["real_pnl"] == pytest.approx(0.2188, rel=1e-4)
        assert bot.capital_remaining == pytest.approx(100.2188, rel=1e-4)
        assert bot.portfolio == {}

        rows = await paper_db.get_open_dca_trades()
        assert rows == []

    asyncio.run(scenario())



def test_execute_dca_round_respects_cash_reserve_pair_cap_and_open_position_limit(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {
            "XXBTZEUR": {"c": ["100.0"]},
            "XETHZEUR": {"c": ["50.0"]},
            "SOLEUR": {"c": ["25.0"]},
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(
            initial_capital_eur=100.0,
            top_n=3,
            rounds_target=3,
            paper_mode=True,
            max_open_positions=2,
            max_pair_exposure_eur=30.0,
            min_cash_reserve_eur=20.0,
            min_edge_pct=0.2,
        ),
        tmp_path,
    )
    bot.active_pairs = [
        {"pair": "XBTEUR", "score": 3.0, "change_pct": -3.0, "last_price": 100.0},
        {"pair": "ETHEUR", "score": 2.0, "change_pct": -2.0, "last_price": 50.0},
        {"pair": "SOLEUR", "score": 1.0, "change_pct": -4.0, "last_price": 25.0},
    ]

    trades = asyncio.run(bot.execute_dca_round())

    assert {t["pair"] for t in trades} == {"XBTEUR", "ETHEUR"}
    assert sum(t["amount_eur"] for t in trades) <= 33.34
    assert bot.capital_remaining >= 66.66
    assert len(bot.portfolio) == 2
    assert all(pos["cost_basis"] <= 30.0 for pos in bot.portfolio.values())


def test_execute_dca_round_does_not_average_down_losing_open_pair(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {
            "XXBTZEUR": {"c": ["90.0"]},
            "XETHZEUR": {"c": ["50.0"]},
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(
            initial_capital_eur=100.0,
            top_n=2,
            rounds_target=3,
            paper_mode=True,
            max_open_positions=2,
            max_pair_exposure_eur=35.0,
            min_cash_reserve_eur=20.0,
            min_edge_pct=0.2,
        ),
        tmp_path,
    )
    bot.capital_remaining = 70.0
    bot.portfolio = {"XBTEUR": {"shares": 0.3, "cost_basis": 30.0}}
    bot.active_pairs = [
        {"pair": "XBTEUR", "score": 5.0, "change_pct": -5.0, "last_price": 90.0},
        {"pair": "ETHEUR", "score": 3.0, "change_pct": -2.0, "last_price": 50.0},
    ]

    trades = asyncio.run(bot.execute_dca_round())

    assert [t["pair"] for t in trades] == ["ETHEUR"]
    assert bot.portfolio["XBTEUR"]["cost_basis"] == pytest.approx(30.0)


def test_execute_dca_round_allows_small_recovery_after_reversal(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {
            "XXBTZEUR": {"c": ["85.0"]},
            "XETHZEUR": {"c": ["50.0"]},
        }

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(
            initial_capital_eur=100.0,
            top_n=2,
            rounds_target=3,
            paper_mode=True,
            max_open_positions=2,
            max_pair_exposure_eur=35.0,
            min_cash_reserve_eur=20.0,
            min_edge_pct=0.2,
            recovery_trigger_pct=-5.0,
            recovery_reversal_pct=0.8,
            recovery_ticket_eur=5.0,
            recovery_max_exposure_factor=1.5,
        ),
        tmp_path,
    )
    bot.capital_remaining = 70.0
    bot.portfolio = {"XBTEUR": {"shares": 0.3, "cost_basis": 30.0}}
    bot.active_pairs = [
        {"pair": "XBTEUR", "score": 5.0, "change_pct": 1.0, "last_price": 85.0},
        {"pair": "ETHEUR", "score": 3.0, "change_pct": -2.0, "last_price": 50.0},
    ]

    trades = asyncio.run(bot.execute_dca_round())

    assert trades[0]["pair"] == "XBTEUR"
    assert trades[0]["reason"] == "recovery"
    assert trades[0]["amount_eur"] == pytest.approx(5.0)
    assert bot.portfolio["XBTEUR"]["cost_basis"] == pytest.approx(35.0)


def test_dca_bot_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        DCABot(paper_mode=False)


def test_get_portfolio_value_does_not_mark_missing_ticker_as_zero(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {}

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(initial_capital_eur=100.0, top_n=1, rounds_target=3, paper_mode=True),
        tmp_path,
    )
    bot.portfolio = {"XBTEUR": {"shares": 1.0, "cost_basis": 100.0}}

    value = asyncio.run(bot.get_portfolio_value())

    assert value["positions"]["XBTEUR"]["current_value"] is None
    assert value["positions"]["XBTEUR"]["valuation_status"] == "missing_ticker"


def test_get_portfolio_value_reports_open_cost_basis_not_cumulative_turnover(monkeypatch, tmp_path):
    async def fake_fetch_ticker_data(_pairs):
        return {"XXBTZEUR": {"c": ["105.0"]}}

    monkeypatch.setattr(dca_strategy, "fetch_ticker_data", fake_fetch_ticker_data)

    bot = _bind_bot_to_tmp_storage(
        DCABot(initial_capital_eur=100.0, top_n=1, rounds_target=3, paper_mode=True),
        tmp_path,
    )
    bot.total_invested = 770.0
    bot.portfolio = {"XBTEUR": {"shares": 1.0, "cost_basis": 100.0}}

    value = asyncio.run(bot.get_portfolio_value())

    assert value["total_value_eur"] == pytest.approx(105.0)
    assert value["total_invested_eur"] == pytest.approx(100.0)
    assert value["pnl_eur"] == pytest.approx(5.0)
    assert value["pnl_pct"] == pytest.approx(5.0)
