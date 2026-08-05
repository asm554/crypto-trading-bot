"""Pre-registered entry-quality gates for research-only DCA backtests.

The production :class:`polybot.dca_strategy.DCABot` remains responsible for
ranking, sizing, fills, fees, exits, cooldowns and accounting.  This module
only decides which already-ranked pairs may reach one scheduled DCA round.

Fixed candidate rules (no parameter sweep):

``trend``
    Buy a 24h dip only when the pair's last completed daily close is above its
    EMA200 and EMA50 is above EMA200.
``reversal``
    Buy a 24h dip only when the last completed hourly close is above the close
    four completed hours earlier.
``trend_reversal``
    Require both conditions.

All candidates disable recovery averaging into losing positions.  That is a
shared capital-protection rule, not a fourth candidate.  Normal position exits
remain entirely controlled by the production class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polybot.surfer_strategy import ema_series

QualityRule = Literal["trend", "reversal", "trend_reversal"]


@dataclass(frozen=True)
class PairQualityDecision:
    pair: str
    allowed: bool
    trend_ok: bool | None
    reversal_ok: bool | None
    daily_close: float | None
    ema50: float | None
    ema200: float | None
    reversal_4h_pct: float | None


def evaluate_pair_quality(
    pair: str,
    daily_rows: list[tuple],
    hourly_rows: list[tuple],
    rule: QualityRule,
) -> PairQualityDecision:
    """Evaluate completed candles only; missing required history blocks entry."""
    daily_closes = [float(row[4]) for row in daily_rows]
    hourly_closes = [float(row[4]) for row in hourly_rows]

    ema50 = ema_series(daily_closes, 50)
    ema200 = ema_series(daily_closes, 200)
    daily_close = daily_closes[-1] if daily_closes else None
    trend_ok: bool | None = None
    if daily_close is not None and ema50 and ema200:
        trend_ok = daily_close > ema200[-1] and ema50[-1] > ema200[-1]

    reversal_pct: float | None = None
    reversal_ok: bool | None = None
    if len(hourly_closes) >= 5 and hourly_closes[-5] > 0:
        reversal_pct = (hourly_closes[-1] - hourly_closes[-5]) / hourly_closes[-5] * 100
        reversal_ok = reversal_pct > 0.0

    allowed = {
        "trend": trend_ok is True,
        "reversal": reversal_ok is True,
        "trend_reversal": trend_ok is True and reversal_ok is True,
    }[rule]
    return PairQualityDecision(
        pair=pair,
        allowed=allowed,
        trend_ok=trend_ok,
        reversal_ok=reversal_ok,
        daily_close=daily_close,
        ema50=ema50[-1] if ema50 else None,
        ema200=ema200[-1] if ema200 else None,
        reversal_4h_pct=reversal_pct,
    )


async def execute_quality_gated_round(bot, sim, rule: QualityRule) -> tuple[list[PairQualityDecision], list[dict]]:
    """Run one real DCA round using only pairs that pass the fixed gate."""
    daily_cache = getattr(bot, "_backtest_quality_daily_cache", None)
    if daily_cache is None:
        daily_cache = {}
        bot._backtest_quality_daily_cache = daily_cache
    day_bucket = int(sim.clock.now // 86400)

    decisions: list[PairQualityDecision] = []
    for item in bot.active_pairs:
        pair = item["pair"]
        daily_rows: list[tuple] = []
        if rule in {"trend", "trend_reversal"}:
            cached = daily_cache.get(pair)
            if cached and cached[0] == day_bucket:
                daily_rows = cached[1]
            else:
                daily_rows = await sim.fetch_ohlc(pair, 1440)
                daily_cache[pair] = (day_bucket, daily_rows)
        hourly_rows = (
            await sim.fetch_ohlc(pair, 60)
            if rule in {"reversal", "trend_reversal"}
            else []
        )
        decisions.append(evaluate_pair_quality(
            pair,
            daily_rows,
            hourly_rows,
            rule,
        ))

    counts = getattr(bot, "_backtest_quality_counts", None)
    if counts is None:
        counts = {"evaluated": 0, "allowed": 0, "blocked": 0, "empty_rounds": 0}
        bot._backtest_quality_counts = counts
    counts["evaluated"] += len(decisions)
    counts["allowed"] += sum(d.allowed for d in decisions)
    counts["blocked"] += sum(not d.allowed for d in decisions)

    allowed_pairs = {d.pair for d in decisions if d.allowed}
    if not allowed_pairs:
        # Consume the scheduled round instead of repeatedly evaluating the same
        # closed candles every simulated hour.
        counts["empty_rounds"] += 1
        bot.last_buy = sim.clock.now
        return decisions, []

    original_pairs = bot.active_pairs
    original_recovery_ticket = bot.recovery_ticket_eur
    bot.active_pairs = [item for item in original_pairs if item["pair"] in allowed_pairs]
    bot.recovery_ticket_eur = 0.0
    try:
        return decisions, await bot.execute_dca_round()
    finally:
        bot.active_pairs = original_pairs
        bot.recovery_ticket_eur = original_recovery_ticket
