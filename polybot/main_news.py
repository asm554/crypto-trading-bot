import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.jev_gate import JevGate
from polybot.news_strategy import NewsBot
from polybot.paper_db import init_db, mark_bot_started, mark_bot_stopped

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/news_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] NEWS: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("NEWS_BUDGET", "500"))
POLL_INTERVAL_SEC = int(os.getenv("NEWS_POLL_INTERVAL_SEC", "60"))
MAX_HEADLINE_AGE_SEC = int(os.getenv("NEWS_MAX_HEADLINE_AGE_SEC", "900"))
MIN_CHOICE_PROB = float(os.getenv("NEWS_MIN_CHOICE_PROB", "0.6"))
MIN_BULLISH = float(os.getenv("NEWS_MIN_BULLISH", "0.75"))
MIN_NEW = float(os.getenv("NEWS_MIN_NEW", "0.7"))
MAX_BEARISH = float(os.getenv("NEWS_MAX_BEARISH", "0.3"))
POSITION_EUR = float(os.getenv("NEWS_POSITION_EUR", "25"))
STOP_LOSS_PCT = float(os.getenv("NEWS_STOP_LOSS_PCT", "1.5"))
TRAILING_ACTIVATION_PCT = float(os.getenv("NEWS_TRAILING_ACTIVATION_PCT", "1.0"))
TRAILING_STOP_PCT = float(os.getenv("NEWS_TRAILING_STOP_PCT", "1.0"))
MAX_HOLD_H = float(os.getenv("NEWS_MAX_HOLD_H", "2"))
MAX_OPEN_POSITIONS = int(os.getenv("NEWS_MAX_OPEN_POSITIONS", "2"))
MAX_TRADES_PER_DAY = int(os.getenv("NEWS_MAX_TRADES_PER_DAY", "6"))
LOSS_STREAK_LIMIT = int(os.getenv("NEWS_LOSS_STREAK_LIMIT", "3"))
LOSS_PAUSE_H = float(os.getenv("NEWS_LOSS_PAUSE_H", "6"))
ACCOUNT_LOSS_LIMIT_PCT = float(os.getenv("NEWS_ACCOUNT_LOSS_LIMIT_PCT", "10.0"))
PAPER_MODE = os.getenv("NEWS_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    await mark_bot_started("news")
    bot = NewsBot(
        jev=JevGate(timeout_sec=5.0),
        initial_capital_eur=BUDGET,
        poll_interval_sec=POLL_INTERVAL_SEC,
        max_headline_age_sec=MAX_HEADLINE_AGE_SEC,
        min_choice_prob=MIN_CHOICE_PROB,
        min_bullish=MIN_BULLISH,
        min_new=MIN_NEW,
        max_bearish=MAX_BEARISH,
        position_eur=POSITION_EUR,
        stop_loss_pct=STOP_LOSS_PCT,
        trailing_activation_pct=TRAILING_ACTIVATION_PCT,
        trailing_stop_pct=TRAILING_STOP_PCT,
        max_hold_sec=int(MAX_HOLD_H * 3600),
        max_open_positions=MAX_OPEN_POSITIONS,
        max_trades_per_day=MAX_TRADES_PER_DAY,
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
    await mark_bot_stopped("news")

if __name__ == "__main__":
    asyncio.run(main())
