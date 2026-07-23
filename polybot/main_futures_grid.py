"""Entry point for the paper-only leveraged grid bot."""

import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env

apply_cli_env()

from polybot.futures_grid_strategy import FuturesGridBot
from polybot.paper_db import init_db, mark_bot_started, mark_bot_stopped

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "logs/futures_grid_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] FUT: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def _env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


async def main() -> None:
    await init_db()
    await mark_bot_started("futures_grid")
    bot = FuturesGridBot(
        initial_capital_eur=_env_float("GRIDFUT_BUDGET", "1000"),
        leverage=_env_float("GRIDFUT_LEVERAGE", "2"),
        order_margin_eur=_env_float("GRIDFUT_ORDER_MARGIN_EUR", "15"),
        grid_step_pct=_env_float("GRIDFUT_GRID_STEP_PCT", "0.8"),
        take_profit_pct=_env_float("GRIDFUT_TAKE_PROFIT_PCT", "1.1"),
        max_safety_orders=int(os.getenv("GRIDFUT_MAX_SAFETY_ORDERS", "50")),
        maintenance_margin_pct=_env_float("GRIDFUT_MAINTENANCE_MARGIN_PCT", "5"),
        margin_guard_ratio=_env_float("GRIDFUT_MARGIN_GUARD_RATIO", "1.25"),
        taker_fee_rate=_env_float("GRIDFUT_TAKER_FEE_RATE", "0.0005"),
        funding_rate_8h=_env_float("GRIDFUT_FUNDING_RATE_8H", "0.0001"),
        scan_interval_sec=int(os.getenv("GRIDFUT_SCAN_INTERVAL_SEC", "30")),
        paper_mode=os.getenv("GRIDFUT_PAPER_MODE", "true").lower() == "true",
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
    await mark_bot_stopped("futures_grid")


if __name__ == "__main__":
    asyncio.run(main())
