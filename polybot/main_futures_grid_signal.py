"""Entry point for the signal-confirmed leveraged grid paper bot."""

import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env

apply_cli_env()

from polybot.futures_grid_signal_strategy import BOT_KEY, SignalFuturesGridBot
from polybot.paper_db import init_db, mark_bot_started, mark_bot_stopped

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "logs/futures_grid_signal_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] GRIDSIG: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


async def main() -> None:
    await init_db()
    await mark_bot_started(BOT_KEY)
    bot = SignalFuturesGridBot(
        initial_capital_eur=env_float("GRIDSIG_BUDGET", "500"),
        leverage=env_float("GRIDSIG_LEVERAGE", "2"),
        order_margin_eur=env_float("GRIDSIG_ORDER_MARGIN_EUR", "12.5"),
        take_profit_pct=env_float("GRIDSIG_TAKE_PROFIT_PCT", "1.2"),
        max_safety_orders=int(os.getenv("GRIDSIG_MAX_SAFETY_ORDERS", "7")),
        maintenance_margin_pct=env_float("GRIDSIG_MAINTENANCE_MARGIN_PCT", "5"),
        margin_guard_ratio=env_float("GRIDSIG_MARGIN_GUARD_RATIO", "1.25"),
        taker_fee_rate=env_float("GRIDSIG_TAKER_FEE_RATE", "0.0005"),
        funding_rate_8h=env_float("GRIDSIG_FUNDING_RATE_8H", "0.0001"),
        cooldown_win_sec=int(os.getenv("GRIDSIG_COOLDOWN_WIN_SEC", "43200")),
        cooldown_loss_sec=int(os.getenv("GRIDSIG_COOLDOWN_LOSS_SEC", "2592000")),
        crash_pause_sec=int(os.getenv("GRIDSIG_CRASH_PAUSE_SEC", "86400")),
        min_buy_gap_sec=int(os.getenv("GRIDSIG_MIN_BUY_GAP_SEC", "3600")),
        hard_stop_pct=env_float("GRIDSIG_HARD_STOP_PCT", "7.5"),
        cycle_loss_limit_pct=env_float("GRIDSIG_CYCLE_LOSS_LIMIT_PCT", "3"),
        min_trend_spread_atr=env_float("GRIDSIG_MIN_TREND_SPREAD_ATR", "0.75"),
        trailing_activation_pct=env_float("GRIDSIG_TRAILING_ACTIVATION_PCT", "2"),
        loss_trend_exit_sec=int(os.getenv("GRIDSIG_LOSS_TREND_EXIT_SEC", "1209600")),
        max_cycle_sec=int(os.getenv("GRIDSIG_MAX_CYCLE_SEC", "1814400")),
        signal_refresh_sec=int(os.getenv("GRIDSIG_SIGNAL_REFRESH_SEC", "300")),
        scan_interval_sec=int(os.getenv("GRIDSIG_SCAN_INTERVAL_SEC", "60")),
        paper_mode=os.getenv("GRIDSIG_PAPER_MODE", "true").lower() == "true",
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
    await mark_bot_stopped(BOT_KEY)


if __name__ == "__main__":
    asyncio.run(main())
