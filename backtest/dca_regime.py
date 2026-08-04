"""Preregistered BTC regime gate for the research-only DCA backtest.

Fixed rule, evaluated on completed BTC/EUR daily candles:

- bull: close > EMA200 and EMA50 > EMA200 -> 100% DCA round
- bear: close < EMA200 and EMA50 < EMA200 -> no new buys
- neutral (including <200 completed days) -> 50% DCA round

Open positions are always managed by the unchanged production ``DCABot``.
This module does not modify ``polybot/`` or any live/paper configuration.

Preregistered fit criteria versus the production-default baseline:

- Bear return improves by >=8 percentage points.
- Bear max drawdown improves by >=5 percentage points.
- Bull return stays positive and retains >=50% of baseline return.
- Bull produces >=30 completed/end-marked trades.

Only if all criteria pass may Current be run once as confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polybot.surfer_strategy import ema_series

Regime = Literal["bull", "neutral", "bear"]


@dataclass(frozen=True)
class RegimeDecision:
    regime: Regime
    size_factor: float
    close: float | None
    ema50: float | None
    ema200: float | None


def classify_btc_regime(rows: list[tuple]) -> RegimeDecision:
    """Classify from completed daily rows only; insufficient history is neutral."""
    closes = [float(row[4]) for row in rows]
    ema50 = ema_series(closes, 50)
    ema200 = ema_series(closes, 200)
    if not closes or not ema50 or not ema200:
        return RegimeDecision("neutral", 0.5, closes[-1] if closes else None, None, None)

    close = closes[-1]
    fast, slow = ema50[-1], ema200[-1]
    if close > slow and fast > slow:
        return RegimeDecision("bull", 1.0, close, fast, slow)
    if close < slow and fast < slow:
        return RegimeDecision("bear", 0.0, close, fast, slow)
    return RegimeDecision("neutral", 0.5, close, fast, slow)


async def execute_regime_gated_round(bot, sim) -> tuple[RegimeDecision, list[dict]]:
    """Apply the research gate around one unchanged production DCA round."""
    decision = classify_btc_regime(await sim.fetch_ohlc("BTCEUR", 1440))
    counts = getattr(bot, "_backtest_regime_counts", None)
    if counts is None:
        counts = {"bull": 0, "neutral": 0, "bear": 0}
        bot._backtest_regime_counts = counts
    counts[decision.regime] += 1

    if decision.regime == "bear":
        # This counts as the scheduled four-hour round. Position exits remain
        # active because ``resolve_due_trades`` runs before this function.
        bot.last_buy = sim.clock.now
        return decision, []

    original_round_eur = bot.per_round_eur
    bot.per_round_eur = round(original_round_eur * decision.size_factor, 2)
    try:
        return decision, await bot.execute_dca_round()
    finally:
        bot.per_round_eur = original_round_eur
