"""Entrypoint for Der Kerzenreiter (paper-only)."""

import asyncio
import logging
import logging.handlers
import os

from polybot.candlestick_strategy import CandlestickBot
from polybot.paper_db import init_db, mark_bot_started

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/candlestick_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] CND: %(message)s"))
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
    await mark_bot_started("candlestick")
    bot = CandlestickBot(
        initial_capital_eur=env_float("CND_BUDGET", 100),
        interval_sec=env_int("CND_INTERVAL_SEC", 60),
        min_score=env_int("CND_MIN_SCORE", 75),
        volume_multiplier=env_float("CND_VOLUME_MULTIPLIER", 1.3),
        atr_stop_multiplier=env_float("CND_ATR_STOP_MULTIPLIER", 2.0),
        reward_risk_ratio=env_float("CND_REWARD_RISK", 1.8),
        max_risk_eur=env_float("CND_MAX_RISK_EUR", 0.5),
        max_position_eur=env_float("CND_MAX_POSITION_EUR", 25),
        max_price_impact_pct=env_float("CND_MAX_PRICE_IMPACT_PCT", 0.5),
        slippage_bps=env_int("CND_SLIPPAGE_BPS", 50),
        max_hold_sec=env_int("CND_MAX_HOLD_H", 48) * 3600,
        loss_streak_limit=env_int("CND_LOSS_STREAK_LIMIT", 3),
        loss_pause_sec=env_int("CND_LOSS_PAUSE_H", 24) * 3600,
        account_loss_limit_pct=env_float("CND_ACCOUNT_LOSS_LIMIT_PCT", 10),
        snapshot_interval_sec=env_int("CND_SNAPSHOT_MIN", 15) * 60,
        paper_mode=os.getenv("CND_PAPER_MODE", "true").lower() == "true",
    )
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
