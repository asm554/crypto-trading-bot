from polybot.bot_overview import format_overview_message


def test_format_overview_message_compacts_all_bots_into_one_summary():
    snapshot = {
        "mode": "PAPER",
        "generated_at": "27.04.2026 08:55 UTC",
        "bots": [
            {"name": "DCA", "running": True, "summary": "90€ Cash | 10€ investiert | 2 offen | PnL -0.13€"},
            {"name": "Kraken Paper", "running": True, "summary": "99.78€ Cash | 0 Positionen | real. PnL -0.22€"},
            {"name": "Myriad Candle", "running": True, "summary": "100€ Balance | 0 Trades | Signal HOLD"},
            {"name": "Smart Money/HFT", "running": False, "summary": "offline"},
        ],
    }

    msg = format_overview_message(snapshot)

    assert "*Bot-Überblick*" in msg
    assert "PAPER" in msg
    assert "DCA" in msg and "90€ Cash" in msg
    assert "Kraken Paper" in msg and "0 Positionen" in msg
    assert "Myriad Candle" in msg and "Signal HOLD" in msg
    assert "Smart Money/HFT" in msg and "offline" in msg
    assert msg.count("\n") >= 4

import asyncio
import polybot.main_dca as main_dca


def test_shutdown_sets_event_and_does_not_raise_systemexit(monkeypatch):
    sent = []

    class DummyBot:
        async def get_portfolio_value(self):
            return {
                "trade_count": 2,
                "total_value_eur": 9.95,
                "pnl_eur": -0.05,
                "pnl_pct": -0.5,
            }

    async def fake_send(message: str):
        sent.append(message)

    monkeypatch.setattr(main_dca, "send_telegram", fake_send)

    async def scenario():
        event = asyncio.Event()
        await main_dca._shutdown(DummyBot(), event)
        assert event.is_set() is True
        assert sent
        assert "DCA-Bot gestoppt" in sent[0]

    asyncio.run(scenario())



def test_dca_open_cost_basis_ignores_cumulative_turnover():
    from polybot.bot_overview import _dca_open_cost_basis

    state = {
        "total_invested": 770.0,
        "portfolio": {
            "XLMEUR": {"cost_basis": 21.66},
            "NEAREUR": {"cost_basis": 11.67},
        },
    }

    assert _dca_open_cost_basis(state) == 33.33
