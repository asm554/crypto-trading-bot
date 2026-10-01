import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.jev_gate import JevGate
from polybot.oracle_strategy import OracleBot
from polybot.paper_db import init_db, mark_bot_started, mark_bot_stopped

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/oracle_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] ORA: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("ORA_BUDGET", "500"))
SCAN_INTERVAL_SEC = int(os.getenv("ORA_SCAN_INTERVAL_SEC", "600"))
MIN_VOLUME_24H = float(os.getenv("ORA_MIN_VOLUME_24H", "20000"))
MAX_SPREAD = float(os.getenv("ORA_MAX_SPREAD", "0.04"))
MIN_EDGE = float(os.getenv("ORA_MIN_EDGE", "0.15"))
MAX_JUDGEMENTS = int(os.getenv("ORA_MAX_JUDGEMENTS_PER_CYCLE", "10"))
POSITION_SIZE = float(os.getenv("ORA_POSITION_SIZE", "10"))
MAX_OPEN_POSITIONS = int(os.getenv("ORA_MAX_OPEN_POSITIONS", "8"))
MAX_TRADES_PER_DAY = int(os.getenv("ORA_MAX_TRADES_PER_DAY", "5"))
TAKER_FEE_RATE = float(os.getenv("ORA_TAKER_FEE_RATE", "0.07"))
ACCOUNT_LOSS_LIMIT_PCT = float(os.getenv("ORA_ACCOUNT_LOSS_LIMIT_PCT", "15.0"))
PAPER_MODE = os.getenv("ORA_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    await mark_bot_started("oracle")
    bot = OracleBot(
        jev=JevGate(timeout_sec=8.0),
        initial_capital=BUDGET,
        scan_interval_sec=SCAN_INTERVAL_SEC,
        min_volume24h=MIN_VOLUME_24H,
        max_spread=MAX_SPREAD,
        min_edge=MIN_EDGE,
        max_judgements_per_cycle=MAX_JUDGEMENTS,
        position_size=POSITION_SIZE,
        max_open_positions=MAX_OPEN_POSITIONS,
        max_trades_per_day=MAX_TRADES_PER_DAY,
        taker_fee_rate=TAKER_FEE_RATE,
        account_loss_limit_pct=ACCOUNT_LOSS_LIMIT_PCT,
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
    await mark_bot_stopped("oracle")

if __name__ == "__main__":
    asyncio.run(main())
