import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db
from polybot.daytrade_strategy import DaytradeBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/daytrade_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] DAY: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("DAY_BUDGET", "100"))
INTERVAL_SEC = int(os.getenv("DAY_INTERVAL_SEC", "300"))
LOOKBACK_H = int(os.getenv("DAY_LOOKBACK_H", "4"))
ENTRY_CHANGE_PCT = float(os.getenv("DAY_ENTRY_CHANGE_PCT", "3.0"))
ENTRY_MAX_CHANGE_PCT = float(os.getenv("DAY_ENTRY_MAX_CHANGE_PCT", "25.0"))
MIN_VOLUME_EUR = float(os.getenv("DAY_MIN_VOLUME_EUR", "500000"))
POSITION_EUR = float(os.getenv("DAY_POSITION_EUR", "10"))
MAX_OPEN_POSITIONS = int(os.getenv("DAY_MAX_OPEN_POSITIONS", "4"))
TRAILING_STOP_PCT = float(os.getenv("DAY_TRAILING_STOP_PCT", "1.5"))
HARD_STOP_PCT = float(os.getenv("DAY_HARD_STOP_PCT", "3.0"))
MAX_HOLD_H = float(os.getenv("DAY_MAX_HOLD_H", "6"))
COOLDOWN_H = float(os.getenv("DAY_COOLDOWN_H", "2"))
PAPER_MODE = os.getenv("DAY_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    bot = DaytradeBot(
        initial_capital_eur=BUDGET,
        interval_sec=INTERVAL_SEC,
        lookback_hours=LOOKBACK_H,
        entry_change_pct=ENTRY_CHANGE_PCT,
        entry_max_change_pct=ENTRY_MAX_CHANGE_PCT,
        min_volume_eur=MIN_VOLUME_EUR,
        position_eur=POSITION_EUR,
        max_open_positions=MAX_OPEN_POSITIONS,
        trailing_stop_pct=TRAILING_STOP_PCT,
        hard_stop_pct=HARD_STOP_PCT,
        max_hold_sec=int(MAX_HOLD_H * 3600),
        cooldown_sec=int(COOLDOWN_H * 3600),
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
