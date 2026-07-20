import pytest

import polybot.dca_strategy as dca_strategy
import polybot.meanrev_strategy as meanrev_strategy
import polybot.momentum_strategy as momentum_strategy

try:
    import polybot.daytrade_strategy as daytrade_strategy
except ImportError:
    daytrade_strategy = None

try:
    import polybot.surfer_strategy as surfer_strategy
except ImportError:
    surfer_strategy = None


@pytest.fixture(autouse=True)
def _no_network_rolling_change(monkeypatch):
    """Verhindert echte OHLC-Netzwerkaufrufe in Tests.

    ``rolling_24h_change_pct``/``rolling_change_pct`` würden sonst Kraken
    kontaktieren. Standardmäßig liefern sie hier ``None``, sodass die
    Ticker-basierte ``change_pct`` erhalten bleibt (deterministisch, offline).
    Tests, die eine echte Bewegung brauchen, überschreiben den Patch gezielt
    mit einem eigenen Wert.
    """
    async def _none(_pair, *args, **kwargs):
        return None

    monkeypatch.setattr(dca_strategy, "rolling_24h_change_pct", _none)
    monkeypatch.setattr(momentum_strategy, "rolling_24h_change_pct", _none)
    monkeypatch.setattr(meanrev_strategy, "rolling_24h_change_pct", _none)
    if daytrade_strategy is not None:
        monkeypatch.setattr(daytrade_strategy, "rolling_change_pct", _none)
    if surfer_strategy is not None and hasattr(surfer_strategy, "rolling_change_pct"):
        monkeypatch.setattr(surfer_strategy, "rolling_change_pct", _none)
