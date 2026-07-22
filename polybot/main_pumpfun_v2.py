import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db, mark_bot_started
from polybot.pumpfun_v2_strategy import PumpFunV2PaperBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/pumpfun_v2_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] PUMP2: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def env(name, default, cast):
    return cast(os.getenv(name, str(default)))


async def main():
    await init_db()
    await mark_bot_started("pumpfun_v2")
    bot = PumpFunV2PaperBot(
        initial_capital_eur=env("PUMP2_BUDGET", 100.0, float),
        position_eur=env("PUMP2_POSITION_EUR", 10.0, float),
        max_open_positions=env("PUMP2_MAX_OPEN_POSITIONS", 3, int),
        min_age_sec=env("PUMP2_MIN_AGE_SEC", 15, int),
        max_age_sec=env("PUMP2_MAX_AGE_SEC", 3600, int),
        min_market_cap_sol=env("PUMP2_MIN_MARKET_CAP_SOL", 8.0, float),
        max_market_cap_sol=env("PUMP2_MAX_MARKET_CAP_SOL", 800.0, float),
        min_change_pct=env("PUMP2_MIN_CHANGE_PCT", 3.0, float),
        max_change_pct=env("PUMP2_MAX_CHANGE_PCT", 60.0, float),
        min_trades=env("PUMP2_MIN_TRADES", 5, int),
        min_unique_traders=env("PUMP2_MIN_UNIQUE_TRADERS", 3, int),
        min_buy_sell_ratio=env("PUMP2_MIN_BUY_SELL_RATIO", 1.05, float),
        min_recent_change_pct=env("PUMP2_MIN_RECENT_CHANGE_PCT", 1.0, float),
        stop_loss_pct=env("PUMP2_STOP_LOSS_PCT", 18.0, float),
        take_profit_pct=env("PUMP2_TAKE_PROFIT_PCT", 25.0, float),
        trailing_stop_pct=env("PUMP2_TRAILING_STOP_PCT", 12.0, float),
        trail_floor_pct=env("PUMP2_TRAIL_FLOOR_PCT", 10.0, float),
        max_hold_sec=env("PUMP2_MAX_HOLD_SEC", 1800, int),
        migrated_slippage_pct=env("PUMP2_SLIPPAGE_PCT", 3.0, float),
        platform_fee_pct=env("PUMP2_PLATFORM_FEE_PCT", 1.0, float),
        paper_mode=os.getenv("PUMP2_PAPER_MODE", "true").lower() == "true",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(bot.run())
    await stop.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await bot.maybe_snapshot(force=True)


if __name__ == "__main__":
    asyncio.run(main())
