import asyncio
import logging
import os
import signal
from polybot.cli_env import apply_cli_env
apply_cli_env()
from polybot.paper_db import init_db, mark_bot_started
from polybot.dca_core_strategy import DcaCoreBot

def number(name, default): return float(os.getenv(name, str(default)))
async def main():
    await init_db()
    await mark_bot_started("dca_core")
    bot = DcaCoreBot(initial_capital_eur=number("DCACORE_BUDGET", 500), cash_reserve_eur=number("DCACORE_CASH_RESERVE_EUR", 50), weekly_total_eur=number("DCACORE_WEEKLY_TOTAL_EUR", 50), circuit_breaker_pct=number("DCACORE_CIRCUIT_BREAKER_PCT", 10), paper_mode=os.getenv("DCACORE_PAPER_MODE", "true").lower() == "true")
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(bot.run()); await stop.wait(); task.cancel(); await asyncio.gather(task, return_exceptions=True); await bot.maybe_snapshot(True)
if __name__ == "__main__": logging.basicConfig(level=logging.INFO); asyncio.run(main())
