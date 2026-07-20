import asyncio
import logging
import logging.handlers
import os
import signal

from polybot.cli_env import apply_cli_env
apply_cli_env()

from polybot.paper_db import init_db
from polybot.scout_strategy import ScoutBot

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler("logs/scout_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] SCOUT: %(message)s"))
root = logging.getLogger(); root.setLevel(logging.INFO); root.addHandler(handler); root.addHandler(logging.StreamHandler())

def env_float(name, default): return float(os.getenv(name, str(default)))
def env_int(name, default): return int(os.getenv(name, str(default)))

async def main():
    await init_db()
    bot = ScoutBot(
        initial_capital_eur=env_float("SCOUT_BUDGET", 100), interval_sec=env_int("SCOUT_INTERVAL_SEC", 30),
        position_eur=env_float("SCOUT_POSITION_EUR", 5), max_open_positions=env_int("SCOUT_MAX_OPEN_POSITIONS", 2),
        cash_reserve_eur=env_float("SCOUT_CASH_RESERVE_EUR", 85), maturity_sec=env_int("SCOUT_MATURITY_MIN", 20) * 60,
        max_pool_age_sec=env_int("SCOUT_MAX_POOL_AGE_H", 12) * 3600, min_score=env_int("SCOUT_MIN_SCORE", 60),
        max_price_impact_pct=env_float("SCOUT_MAX_PRICE_IMPACT_PCT", 1.5), max_round_trip_cost_pct=env_float("SCOUT_MAX_ROUND_TRIP_COST_PCT", 8),
        paper_slippage_pct=env_float("SCOUT_PAPER_SLIPPAGE_PCT", .5), stop_loss_pct=env_float("SCOUT_STOP_LOSS_PCT", 12),
        take_profit_pct=env_float("SCOUT_TAKE_PROFIT_PCT", 25), trail_activation_pct=env_float("SCOUT_TRAIL_ACTIVATION_PCT", 10),
        trailing_stop_pct=env_float("SCOUT_TRAILING_STOP_PCT", 8), max_hold_sec=env_int("SCOUT_MAX_HOLD_H", 6) * 3600,
        loss_streak_limit=env_int("SCOUT_LOSS_STREAK_LIMIT", 2), risk_off_sec=env_int("SCOUT_RISK_OFF_H", 12) * 3600,
        account_loss_limit_pct=env_float("SCOUT_ACCOUNT_LOSS_LIMIT_PCT", 8), snapshot_interval_sec=env_int("SCOUT_SNAPSHOT_MIN", 15) * 60,
        paper_mode=os.getenv("SCOUT_PAPER_MODE", "true").lower() == "true",
    )
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(bot.run()); await stop.wait(); task.cancel(); await asyncio.gather(task, return_exceptions=True); await bot.maybe_snapshot(force=True)

if __name__ == "__main__": asyncio.run(main())
