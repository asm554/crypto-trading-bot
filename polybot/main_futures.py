import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env

apply_cli_env()

from polybot.futures_strategy import DEFAULT_SYMBOLS, FuturesPaperBot
from polybot.paper_db import init_db, mark_bot_started

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "logs/futures_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] FUT: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


async def main() -> None:
    await init_db()
    await mark_bot_started("futures")
    symbols = tuple(s.strip().upper() for s in os.getenv("FUT_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",") if s.strip())
    bot = FuturesPaperBot(
        initial_capital_eur=env_float("FUT_BUDGET", 100),
        symbols=symbols,
        interval_sec=env_int("FUT_INTERVAL_SEC", 300),
        candle_resolution=os.getenv("FUT_CANDLE_RESOLUTION", "1h"),
        fast_ema=env_int("FUT_FAST_EMA", 9),
        slow_ema=env_int("FUT_SLOW_EMA", 21),
        momentum_bars=env_int("FUT_MOMENTUM_BARS", 6),
        min_momentum_pct=env_float("FUT_MIN_MOMENTUM_PCT", 0.8),
        min_trend_pct=env_float("FUT_MIN_TREND_PCT", 0.15),
        min_volume_usd=env_float("FUT_MIN_VOLUME_USD", 10_000_000),
        position_margin_eur=env_float("FUT_POSITION_MARGIN_EUR", 20),
        leverage=env_float("FUT_LEVERAGE", 2),
        max_open_positions=env_int("FUT_MAX_OPEN_POSITIONS", 2),
        hard_stop_pct=env_float("FUT_HARD_STOP_PCT", 2),
        take_profit_pct=env_float("FUT_TAKE_PROFIT_PCT", 5),
        trailing_activation_pct=env_float("FUT_TRAILING_ACTIVATION_PCT", 2),
        trailing_distance_pct=env_float("FUT_TRAILING_DISTANCE_PCT", 1),
        max_hold_sec=env_int("FUT_MAX_HOLD_H", 24) * 3600,
        cooldown_sec=env_int("FUT_COOLDOWN_H", 4) * 3600,
        max_spread_pct=env_float("FUT_MAX_SPREAD_PCT", 0.10),
        max_adverse_funding_rate=env_float("FUT_MAX_ADVERSE_FUNDING_RATE", 0.0002),
        taker_fee_rate=env_float("FUT_TAKER_FEE_RATE", 0.0005),
        maintenance_margin_rate=env_float("FUT_MAINTENANCE_MARGIN_RATE", 0.05),
        daily_loss_halt_pct=env_float("FUT_DAILY_LOSS_HALT_PCT", 5),
        paper_mode=os.getenv("FUT_PAPER_MODE", "true").lower() == "true",
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
