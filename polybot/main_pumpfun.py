import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db, mark_bot_started
from polybot.pumpfun_strategy import PumpFunPaperBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/pumpfun_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] PUMP: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def env(name, default, cast):
    return cast(os.getenv(name, str(default)))


async def main():
    await init_db()
    await mark_bot_started("pumpfun")
    bot = PumpFunPaperBot(
        initial_capital_eur=env("PUMP_BUDGET", 100.0, float),
        position_eur=env("PUMP_POSITION_EUR", 5.0, float),
        max_open_positions=env("PUMP_MAX_OPEN_POSITIONS", 2, int),
        min_age_sec=env("PUMP_MIN_AGE_SEC", 90, int),
        max_age_sec=env("PUMP_MAX_AGE_SEC", 21600, int),
        min_market_cap_sol=env("PUMP_MIN_MARKET_CAP_SOL", 20.0, float),
        max_market_cap_sol=env("PUMP_MAX_MARKET_CAP_SOL", 350.0, float),
        min_change_pct=env("PUMP_MIN_CHANGE_PCT", 10.0, float),
        max_change_pct=env("PUMP_MAX_CHANGE_PCT", 35.0, float),
        min_trades=env("PUMP_MIN_TRADES", 20, int),
        min_unique_traders=env("PUMP_MIN_UNIQUE_TRADERS", 8, int),
        min_buy_sell_ratio=env("PUMP_MIN_BUY_SELL_RATIO", 1.4, float),
        min_recent_change_pct=env("PUMP_MIN_RECENT_CHANGE_PCT", 2.0, float),
        stop_loss_pct=env("PUMP_STOP_LOSS_PCT", 20.0, float),
        take_profit_pct=env("PUMP_TAKE_PROFIT_PCT", 30.0, float),
        trailing_stop_pct=env("PUMP_TRAILING_STOP_PCT", 15.0, float),
        trail_floor_pct=env("PUMP_TRAIL_FLOOR_PCT", 15.0, float),
        max_hold_sec=env("PUMP_MAX_HOLD_SEC", 2700, int),
        migrated_slippage_pct=env("PUMP_SLIPPAGE_PCT", 2.5, float),
        platform_fee_pct=env("PUMP_PLATFORM_FEE_PCT", 1.0, float),
        paper_mode=os.getenv("PUMP_PAPER_MODE", "true").lower() == "true",
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
