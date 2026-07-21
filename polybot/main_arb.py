import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db, mark_bot_started
from polybot.arb_strategy import TriangularArbBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/arb_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] ARB: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("ARB_BUDGET", "100"))
INTERVAL_SEC = int(os.getenv("ARB_INTERVAL_SEC", "45"))
TICKET_EUR = float(os.getenv("ARB_TICKET_EUR", "25"))
MIN_NET_PROFIT_EUR = float(os.getenv("ARB_MIN_NET_PROFIT_EUR", "0.05"))
MAX_TRADES_PER_HOUR = int(os.getenv("ARB_MAX_TRADES_PER_HOUR", "6"))
PAPER_MODE = os.getenv("ARB_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    await mark_bot_started("arb")
    bot = TriangularArbBot(
        initial_capital_eur=BUDGET,
        interval_sec=INTERVAL_SEC,
        ticket_eur=TICKET_EUR,
        min_net_profit_eur=MIN_NET_PROFIT_EUR,
        max_trades_per_hour=MAX_TRADES_PER_HOUR,
        paper_mode=PAPER_MODE,
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
