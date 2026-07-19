import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db
from polybot.memecoin_strategy import MemecoinBreakoutBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/memecoin_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] CHAIN: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("CHAIN_BUDGET", "100"))
INTERVAL_SEC = int(os.getenv("CHAIN_INTERVAL_SEC", "300"))
LOOKBACK_H = float(os.getenv("CHAIN_LOOKBACK_H", "6"))
BREAKOUT_MARGIN_PCT = float(os.getenv("CHAIN_BREAKOUT_MARGIN_PCT", "0.5"))
MIN_LIQUIDITY_USD = float(os.getenv("CHAIN_MIN_LIQUIDITY_USD", "50000"))
POSITION_EUR = float(os.getenv("CHAIN_POSITION_EUR", "8"))
MAX_OPEN_POSITIONS = int(os.getenv("CHAIN_MAX_OPEN_POSITIONS", "3"))
TAKE_PROFIT_PCT = float(os.getenv("CHAIN_TAKE_PROFIT_PCT", "20"))
STOP_LOSS_PCT = float(os.getenv("CHAIN_STOP_LOSS_PCT", "10"))
MAX_HOLD_H = float(os.getenv("CHAIN_MAX_HOLD_H", "24"))
COOLDOWN_H = float(os.getenv("CHAIN_COOLDOWN_H", "4"))
SLIPPAGE_PCT = float(os.getenv("CHAIN_SLIPPAGE_PCT", "1.5"))
PAPER_MODE = os.getenv("CHAIN_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    bot = MemecoinBreakoutBot(
        initial_capital_eur=BUDGET,
        interval_sec=INTERVAL_SEC,
        lookback_hours=LOOKBACK_H,
        breakout_margin_pct=BREAKOUT_MARGIN_PCT,
        min_liquidity_usd=MIN_LIQUIDITY_USD,
        position_eur=POSITION_EUR,
        max_open_positions=MAX_OPEN_POSITIONS,
        take_profit_pct=TAKE_PROFIT_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        max_hold_sec=int(MAX_HOLD_H * 3600),
        cooldown_sec=int(COOLDOWN_H * 3600),
        slippage_pct=SLIPPAGE_PCT,
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
