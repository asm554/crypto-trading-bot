import json

import pytest

from polybot import paper_db
from polybot.daytrade_strategy import DaytradeBot
from polybot.dca_strategy import DCABot
from polybot.meanrev_strategy import MeanRevBot
from polybot.memecoin_strategy import MemecoinMomentumBot
from polybot.momentum_strategy import MomentumBot
from polybot.surfer_strategy import SurferBot
from polybot.futures_strategy import FuturesPaperBot


@pytest.mark.parametrize(
    ("state_name", "factory"),
    [
        ("dca_state.json", lambda: DCABot(paper_mode=True)),
        ("momentum_state.json", lambda: MomentumBot(paper_mode=True)),
        ("meanrev_state.json", lambda: MeanRevBot(paper_mode=True)),
        ("daytrade_state.json", lambda: DaytradeBot(paper_mode=True)),
        ("memecoin_state.json", lambda: MemecoinMomentumBot(paper_mode=True)),
        ("surfer_state.json", lambda: SurferBot(paper_mode=True)),
        ("futures_state.json", lambda: FuturesPaperBot(paper_mode=True)),
    ],
)
def test_restored_cash_keeps_realized_profit(monkeypatch, tmp_path, state_name, factory):
    monkeypatch.setattr(paper_db, "DB_PATH", str(tmp_path / "paper_trades.db"))
    (tmp_path / state_name).write_text(
        json.dumps(
            {
                "capital_remaining": 105.25,
                "portfolio": {},
                "cooldowns": {},
                "coin_cooldowns": {},
            }
        )
    )

    bot = factory()

    assert bot.capital_remaining == pytest.approx(105.25)
