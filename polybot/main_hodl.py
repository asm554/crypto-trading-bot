import asyncio
import logging
import os
import signal
from polybot.cli_env import apply_cli_env
apply_cli_env()
from polybot.paper_db import init_db
from polybot.hodl_strategy import HodlBot

def number(name, default): return float(os.getenv(name, str(default)))
async def main():
    await init_db()
    bot = HodlBot(initial_capital_eur=number("HODL_BUDGET", 100), cash_reserve_eur=number("HODL_CASH_RESERVE_EUR", 20), max_weekly_eur=number("HODL_MAX_WEEKLY_EUR", 20), bear_rate_pct=number("HODL_BEAR_RATE_PCT", 35), paper_mode=os.getenv("HODL_PAPER_MODE", "true").lower() == "true")
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(bot.run()); await stop.wait(); task.cancel(); await asyncio.gather(task, return_exceptions=True); await bot.maybe_snapshot(True)
if __name__ == "__main__": logging.basicConfig(level=logging.INFO); asyncio.run(main())
