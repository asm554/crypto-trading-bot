import asyncio
import logging
from telegram import Bot
from telegram.error import TelegramError
from . import config

logger = logging.getLogger(__name__)

async def send_telegram(message: str):
    """
    Sendet einen Alert via Telegram, wenn ein Trade stattfindet oder das Limit erreicht wird.
    """
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID or "Your" in config.TELEGRAM_TOKEN:
        logger.debug(f"[TELEGRAM DISABLED] {message}")
        print(f"[TELEGRAM DISABLED] {message}")
        return

    bot = Bot(token=config.TELEGRAM_TOKEN)
    try:
        await bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=message)
        logger.info(f"Telegram sent: {message}")
    except TelegramError as e:
        logger.error(f"Fehler beim Senden von Telegram: {e}")

async def send_heartbeat(stats: dict):
    """Sends an hourly heartbeat with current performance stats."""
    msg = (
        f"💓 *HEARTBEAT - {config.BOT_NAME}*\n"
        f"💰 Balance: ${stats.get('balance', 0):.2f}\n"
        f"📈 Trades heute: {stats.get('count', 0)}\n"
        f"📅 Tage online: {stats.get('days_running', 0):.1f}\n"
        f"✨ Avg Edge: {stats.get('avg_edge', 0):.2f}%\n"
        f"🛡️ Mode: {'PAPER' if config.PAPER_MODE else 'LIVE'}"
    )
    await send_telegram(msg)
