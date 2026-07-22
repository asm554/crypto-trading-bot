import pytest
from polybot.pumpfun_v2_strategy import PumpFunV2PaperBot


def test_pumpfun_v2_is_hard_paper_only():
    with pytest.raises(NotImplementedError):
        PumpFunV2PaperBot(paper_mode=False)


def test_pumpfun_v2_is_separate_namespace_and_state():
    bot = PumpFunV2PaperBot()
    assert bot.prefix == "PUMP2_"
    assert bot.bot_key == "pumpfun_v2"
    assert bot.state_path.name == "pumpfun_v2_state.json"
    assert bot.position_eur == 10.0
