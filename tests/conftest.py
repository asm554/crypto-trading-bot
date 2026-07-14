import pytest

import polybot.dca_strategy as dca_strategy
import polybot.meanrev_strategy as meanrev_strategy
import polybot.momentum_strategy as momentum_strategy


@pytest.fixture(autouse=True)
def _no_network_rolling_change(monkeypatch):
    """Verhindert echte OHLC-Netzwerkaufrufe in Tests.

    ``rolling_24h_change_pct`` würde sonst Kraken kontaktieren. Standardmäßig
    liefert es hier ``None``, sodass die Ticker-basierte ``change_pct`` erhalten
    bleibt (deterministisch, offline). Tests, die eine echte 24h-Bewegung
    brauchen, überschreiben den Patch gezielt mit einem eigenen Wert.
    """
    async def _none(_pair, *args, **kwargs):
        return None

    monkeypatch.setattr(dca_strategy, "rolling_24h_change_pct", _none)
    monkeypatch.setattr(momentum_strategy, "rolling_24h_change_pct", _none)
    monkeypatch.setattr(meanrev_strategy, "rolling_24h_change_pct", _none)
