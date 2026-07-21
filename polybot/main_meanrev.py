import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db, mark_bot_started
from polybot.meanrev_strategy import MeanRevBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/meanrev_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] REV: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("REV_BUDGET", "100"))
INTERVAL_H = float(os.getenv("REV_INTERVAL_H", "1"))
ENTRY_DROP_PCT = float(os.getenv("REV_ENTRY_DROP_PCT", "8.0"))
RSI_PERIOD = int(os.getenv("REV_RSI_PERIOD", "14"))
RSI_MAX = float(os.getenv("REV_RSI_MAX", "30"))
BOLLINGER_ENABLED = os.getenv("REV_BOLLINGER_ENABLED", "true").lower() == "true"
BOLLINGER_PERIOD = int(os.getenv("REV_BOLLINGER_PERIOD", "20"))
BOLLINGER_STDDEV = float(os.getenv("REV_BOLLINGER_STDDEV", "2.0"))
STOCHASTIC_ENABLED = os.getenv("REV_STOCHASTIC_ENABLED", "true").lower() == "true"
STOCHASTIC_PERIOD = int(os.getenv("REV_STOCHASTIC_PERIOD", "14"))
STOCHASTIC_MAX = float(os.getenv("REV_STOCHASTIC_MAX", "20"))
CONFIRM_PCT = float(os.getenv("REV_CONFIRM_PCT", "0.5"))
POSITION_EUR = float(os.getenv("REV_POSITION_EUR", "15"))
MAX_OPEN_POSITIONS = int(os.getenv("REV_MAX_OPEN_POSITIONS", "3"))
TAKE_PROFIT_PCT = float(os.getenv("REV_TAKE_PROFIT_PCT", "4.0"))
STOP_LOSS_PCT = float(os.getenv("REV_STOP_LOSS_PCT", "5.0"))
MAX_HOLD_H = float(os.getenv("REV_MAX_HOLD_H", "96"))
COOLDOWN_H = float(os.getenv("REV_COOLDOWN_H", "12"))
PAPER_MODE = os.getenv("REV_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    await mark_bot_started("meanrev")
    bot = MeanRevBot(
        initial_capital_eur=BUDGET,
        interval_sec=int(INTERVAL_H * 3600),
        entry_drop_pct=ENTRY_DROP_PCT,
        rsi_period=RSI_PERIOD,
        rsi_max=RSI_MAX,
        bollinger_enabled=BOLLINGER_ENABLED,
        bollinger_period=BOLLINGER_PERIOD,
        bollinger_stddev=BOLLINGER_STDDEV,
        stochastic_enabled=STOCHASTIC_ENABLED,
        stochastic_period=STOCHASTIC_PERIOD,
        stochastic_max=STOCHASTIC_MAX,
        confirm_pct=CONFIRM_PCT,
        position_eur=POSITION_EUR,
        max_open_positions=MAX_OPEN_POSITIONS,
        take_profit_pct=TAKE_PROFIT_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
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
