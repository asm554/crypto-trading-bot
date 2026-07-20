import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db
from polybot.memecoin_strategy import MemecoinMomentumBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/memecoin_bot.log", maxBytes=20*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] CHAIN: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

BUDGET = float(os.getenv("CHAIN_BUDGET", "100"))
INTERVAL_SEC = int(os.getenv("CHAIN_INTERVAL_SEC", "300"))
ENTRY_CHANGE_PCT = float(os.getenv("CHAIN_ENTRY_CHANGE_PCT", "8.0"))
ENTRY_MAX_CHANGE_PCT = float(os.getenv("CHAIN_ENTRY_MAX_CHANGE_PCT", "35.0"))
MAX_H6_CHANGE_PCT = float(os.getenv("CHAIN_MAX_H6_CHANGE_PCT", "100.0"))
MIN_LIQUIDITY_USD = float(os.getenv("CHAIN_MIN_LIQUIDITY_USD", "50000"))
MIN_LIQUIDITY_DYNAMIC_USD = float(os.getenv("CHAIN_MIN_LIQUIDITY_DYNAMIC_USD", "100000"))
MIN_VOLUME_USD = float(os.getenv("CHAIN_MIN_VOLUME_USD", "100000"))
MIN_VOLUME_DYNAMIC_USD = float(os.getenv("CHAIN_MIN_VOLUME_DYNAMIC_USD", "500000"))
MIN_BUY_SELL_RATIO = float(os.getenv("CHAIN_MIN_BUY_SELL_RATIO", "1.2"))
MIN_H1_TXNS = int(os.getenv("CHAIN_MIN_H1_TXNS", "50"))
DYNAMIC_ENABLED = os.getenv("CHAIN_DYNAMIC_ENABLED", "true").lower() == "true"
MAX_DYNAMIC_TOKENS = int(os.getenv("CHAIN_MAX_DYNAMIC_TOKENS", "15"))
MAX_DYNAMIC_POSITIONS = int(os.getenv("CHAIN_MAX_DYNAMIC_POSITIONS", "2"))
MIN_PAIR_AGE_H = float(os.getenv("CHAIN_MIN_PAIR_AGE_H", "24"))
POSITION_EUR = float(os.getenv("CHAIN_POSITION_EUR", "8"))
MAX_OPEN_POSITIONS = int(os.getenv("CHAIN_MAX_OPEN_POSITIONS", "3"))
TAKE_PROFIT_PCT = float(os.getenv("CHAIN_TAKE_PROFIT_PCT", "15"))
TRAILING_STOP_PCT = float(os.getenv("CHAIN_TRAILING_STOP_PCT", "12"))
TRAIL_FLOOR_PCT = float(os.getenv("CHAIN_TRAIL_FLOOR_PCT", "5"))
STOP_LOSS_PCT = float(os.getenv("CHAIN_STOP_LOSS_PCT", "10"))
MAX_HOLD_H = float(os.getenv("CHAIN_MAX_HOLD_H", "24"))
COOLDOWN_H = float(os.getenv("CHAIN_COOLDOWN_H", "4"))
COOLDOWN_AFTER_STOP_H = float(os.getenv("CHAIN_COOLDOWN_AFTER_STOP_H", "24"))
SLIPPAGE_PCT = float(os.getenv("CHAIN_SLIPPAGE_PCT", "1.5"))
DEX_FEE_PCT = float(os.getenv("CHAIN_DEX_FEE_PCT", "1.0"))
PAPER_MODE = os.getenv("CHAIN_PAPER_MODE", "true").lower() == "true"

async def main():
    await init_db()
    bot = MemecoinMomentumBot(
        initial_capital_eur=BUDGET,
        interval_sec=INTERVAL_SEC,
        entry_change_pct=ENTRY_CHANGE_PCT,
        entry_max_change_pct=ENTRY_MAX_CHANGE_PCT,
        max_h6_change_pct=MAX_H6_CHANGE_PCT,
        min_liquidity_usd=MIN_LIQUIDITY_USD,
        min_liquidity_dynamic_usd=MIN_LIQUIDITY_DYNAMIC_USD,
        min_volume_usd=MIN_VOLUME_USD,
        min_volume_dynamic_usd=MIN_VOLUME_DYNAMIC_USD,
        min_buy_sell_ratio=MIN_BUY_SELL_RATIO,
        min_h1_txns=MIN_H1_TXNS,
        dynamic_enabled=DYNAMIC_ENABLED,
        max_dynamic_tokens=MAX_DYNAMIC_TOKENS,
        max_dynamic_positions=MAX_DYNAMIC_POSITIONS,
        min_pair_age_hours=MIN_PAIR_AGE_H,
        position_eur=POSITION_EUR,
        max_open_positions=MAX_OPEN_POSITIONS,
        take_profit_pct=TAKE_PROFIT_PCT,
        trailing_stop_pct=TRAILING_STOP_PCT,
        trail_floor_pct=TRAIL_FLOOR_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        max_hold_sec=int(MAX_HOLD_H * 3600),
        cooldown_sec=int(COOLDOWN_H * 3600),
        cooldown_after_stop_sec=int(COOLDOWN_AFTER_STOP_H * 3600),
        slippage_pct=SLIPPAGE_PCT,
        dex_fee_pct=DEX_FEE_PCT,
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
