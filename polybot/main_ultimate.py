"""Entrypoint for Der Ultimative (paper-only)."""

import asyncio
import logging
import logging.handlers
import os

from polybot.paper_db import init_db, mark_bot_started, mark_bot_stopped
from polybot.ultimate_strategy import UltimateBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/ultimate_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] ULT: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


async def main() -> None:
    await init_db()
    await mark_bot_started("ultimate")
    bot = UltimateBot(
        initial_capital_eur=env_float("ULT_BUDGET", 500),
        interval_sec=env_int("ULT_INTERVAL_SEC", 300),
        min_score=env_int("ULT_MIN_SCORE", 85),
        volume_multiplier=env_float("ULT_VOLUME_MULTIPLIER", 1.2),
        atr_stop_multiplier=env_float("ULT_ATR_STOP_MULTIPLIER", 2.0),
        reward_risk_ratio=env_float("ULT_REWARD_RISK", 2.0),
        max_risk_eur=env_float("ULT_MAX_RISK_EUR", 2.5),
        max_position_eur=env_float("ULT_MAX_POSITION_EUR", 125),
        max_hold_sec=env_int("ULT_MAX_HOLD_H", 72) * 3600,
        account_loss_limit_pct=env_float("ULT_ACCOUNT_LOSS_LIMIT_PCT", 10),
        fee_rate=env_float("ULT_TAKER_FEE_RATE", 0.008),
        min_hold_sec=env_int("ULT_MIN_HOLD_MIN", 60) * 60,
        pair_cooldown_sec=env_int("ULT_PAIR_COOLDOWN_H", 12) * 3600,
        loss_streak_limit=env_int("ULT_LOSS_STREAK_LIMIT", 2),
        loss_pause_sec=env_int("ULT_LOSS_PAUSE_H", 12) * 3600,
        max_entries_per_day=env_int("ULT_MAX_ENTRIES_DAY", 3),
        max_spread_pct=env_float("ULT_MAX_SPREAD_PCT", 0.15),
        snapshot_interval_sec=env_int("ULT_SNAPSHOT_MIN", 15) * 60,
        paper_mode=os.getenv("ULT_PAPER_MODE", "true").lower() == "true",
    )
    try:
        await bot.run()
    finally:
        await bot.maybe_snapshot(force=True)
        await mark_bot_stopped("ultimate")


if __name__ == "__main__":
    asyncio.run(main())
