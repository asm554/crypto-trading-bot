import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db, mark_bot_started
from polybot.surfer_strategy import SurferBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/surfer_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] SURF: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("SURF_BUDGET", "100"))
INTERVAL_SEC = int(os.getenv("SURF_INTERVAL_SEC", "3600"))
TREND_LOOKBACK_H = int(os.getenv("SURF_TREND_LOOKBACK_H", "4"))
MIN_TREND_PCT = float(os.getenv("SURF_MIN_TREND_PCT", "0.0"))
BREAKOUT_LOOKBACK_H = int(os.getenv("SURF_BREAKOUT_LOOKBACK_H", "20"))
EMA_FAST_PERIOD = int(os.getenv("SURF_EMA_FAST_PERIOD", "20"))
EMA_SLOW_PERIOD = int(os.getenv("SURF_EMA_SLOW_PERIOD", "50"))
ATR_PERIOD = int(os.getenv("SURF_ATR_PERIOD", "14"))
ATR_STOP_MULTIPLIER = float(os.getenv("SURF_ATR_STOP_MULTIPLIER", "2.0"))
VOLUME_MULTIPLIER = float(os.getenv("SURF_VOLUME_MULTIPLIER", "1.2"))
MAX_RISK_EUR = float(os.getenv("SURF_MAX_RISK_EUR", "0.50"))
MAX_POSITION_EUR = float(os.getenv("SURF_MAX_POSITION_EUR", "25"))
TRAILING_STOP_PCT = float(os.getenv("SURF_TRAILING_STOP_PCT", "3.0"))
MAX_HOLD_H = float(os.getenv("SURF_MAX_HOLD_H", str(7 * 24)))
LOSS_STREAK_LIMIT = int(os.getenv("SURF_LOSS_STREAK_LIMIT", "3"))
LOSS_PAUSE_H = float(os.getenv("SURF_LOSS_PAUSE_H", "24"))
ACCOUNT_LOSS_LIMIT_PCT = float(os.getenv("SURF_ACCOUNT_LOSS_LIMIT_PCT", "10.0"))
PAPER_MODE = os.getenv("SURF_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    await mark_bot_started("surfer")
    bot = SurferBot(
        initial_capital_eur=BUDGET,
        interval_sec=INTERVAL_SEC,
        trend_lookback_hours=TREND_LOOKBACK_H,
        min_trend_pct=MIN_TREND_PCT,
        breakout_lookback_hours=BREAKOUT_LOOKBACK_H,
        ema_fast_period=EMA_FAST_PERIOD,
        ema_slow_period=EMA_SLOW_PERIOD,
        atr_period=ATR_PERIOD,
        atr_stop_multiplier=ATR_STOP_MULTIPLIER,
        volume_multiplier=VOLUME_MULTIPLIER,
        max_risk_eur=MAX_RISK_EUR,
        max_position_eur=MAX_POSITION_EUR,
        trailing_stop_pct=TRAILING_STOP_PCT,
        max_hold_sec=int(MAX_HOLD_H * 3600),
        loss_streak_limit=LOSS_STREAK_LIMIT,
        loss_pause_sec=int(LOSS_PAUSE_H * 3600),
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

if __name__ == "__main__":
    asyncio.run(main())
