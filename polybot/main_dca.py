"""
DCA-Bot Einstiegspunkt.

Startet den Dollar-Cost-Averaging Bot für Kraken im Paper-Mode.
Wählt automatisch die profitabelsten EUR-Paare aus und kauft
in festen Intervallen.

Start:
    cd /root/polyarbi
    python -m polybot.main_dca

    # Mit eigenen Parametern (Umgebungsvariablen):
    DCA_BUDGET=100 DCA_INTERVAL_H=4 DCA_TOP_N=3 python -m polybot.main_dca
"""

from polybot.cli_env import apply_cli_env
apply_cli_env()

import asyncio
import logging
import logging.handlers
import os
import signal

from polybot import config
from polybot.dca_strategy import DCABot
from polybot.paper_db import init_db
from polybot.alerts import send_telegram
from polybot.bot_overview import build_overview_message

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "logs/dca_bot.log", maxBytes=20 * 1024 * 1024, backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] DCA: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

# Konfiguration via Umgebungsvariablen (mit Fallbacks)
BUDGET_EUR      = float(os.getenv("DCA_BUDGET", "100"))
INTERVAL_H      = float(os.getenv("DCA_INTERVAL_H", "4"))
TOP_N           = int(os.getenv("DCA_TOP_N", "2"))
PAPER_MODE      = os.getenv("DCA_PAPER_MODE", "true").lower() == "true"
RESCAN_H        = float(os.getenv("DCA_RESCAN_H", "24"))

# Risk- / Qualitätsfilter — Conservative Recovery DCA.
# Ziel: weniger Mini-Dip-Einstiege, höhere Gewinner, keine realisierten Verlust-Exits.
MIN_EDGE_PCT            = float(os.getenv("DCA_MIN_EDGE_PCT", "1.20"))
NEG_STREAK_LIMIT        = int(os.getenv("DCA_NEG_STREAK_LIMIT", "2"))
COIN_COOLDOWN_H         = float(os.getenv("DCA_COIN_COOLDOWN_H", "8"))
ROLLING_WINDOW          = int(os.getenv("DCA_ROLLING_WINDOW", "6"))
ROLLING_LOSS_LIMIT_EUR  = float(os.getenv("DCA_ROLLING_LOSS_LIMIT_EUR", "-1.00"))
RISK_OFF_H              = float(os.getenv("DCA_RISK_OFF_H", "4"))
TAKE_PROFIT_PCT         = float(os.getenv("DCA_TAKE_PROFIT_PCT", "0.03"))
STOP_LOSS_PCT           = float(os.getenv("DCA_STOP_LOSS_PCT", "0.0"))
# Verlust-Backstop: nach 14 Tagen erzwungener Time-Exit (darf auch Minus realisieren).
MAX_HOLD_SEC            = int(os.getenv("DCA_MAX_HOLD_SEC", "1209600"))
MIN_NET_PROFIT_EUR      = float(os.getenv("DCA_MIN_NET_PROFIT_EUR", "0.15"))
MAX_OPEN_POSITIONS      = int(os.getenv("DCA_MAX_OPEN_POSITIONS", "2"))
MAX_PAIR_EXPOSURE_EUR   = float(os.getenv("DCA_MAX_PAIR_EXPOSURE_EUR", "20"))
MIN_CASH_RESERVE_EUR    = float(os.getenv("DCA_MIN_CASH_RESERVE_EUR", "10"))

# Markt-/Recovery-Filter
TREND_FILTER_ENABLED        = os.getenv("DCA_TREND_FILTER_ENABLED", "true").lower() == "true"
BTC_RISK_OFF_PCT            = float(os.getenv("DCA_BTC_RISK_OFF_PCT", "-2.0"))
ETH_RISK_OFF_PCT            = float(os.getenv("DCA_ETH_RISK_OFF_PCT", "-3.0"))
RECOVERY_TRIGGER_PCT        = float(os.getenv("DCA_RECOVERY_TRIGGER_PCT", "-5.0"))
RECOVERY_REVERSAL_PCT       = float(os.getenv("DCA_RECOVERY_REVERSAL_PCT", "0.8"))
RECOVERY_TICKET_EUR         = float(os.getenv("DCA_RECOVERY_TICKET_EUR", "5.0"))
RECOVERY_MAX_EXPOSURE_FACTOR = float(os.getenv("DCA_RECOVERY_MAX_EXPOSURE_FACTOR", "1.5"))


async def status_reporter(bot: DCABot, interval_sec: int = 3600) -> None:
    """Sendet stündlich einen Telegram-Report mit Portfolio-Status."""
    await asyncio.sleep(300)  # Erst nach 5 Min starten
    while True:
        try:
            p = await bot.get_portfolio_value()
            if p["trade_count"] == 0:
                await asyncio.sleep(interval_sec)
                continue

            await send_telegram(build_overview_message())
        except Exception as e:
            logger.error(f"Status-Report Fehler: {e}")

        await asyncio.sleep(interval_sec)


async def main() -> None:
    await init_db()

    bot = DCABot(
        initial_capital_eur=BUDGET_EUR,
        interval_sec=int(INTERVAL_H * 3600),
        top_n=TOP_N,
        paper_mode=PAPER_MODE,
        rescan_interval=int(RESCAN_H * 3600),
        rounds_target=5,   # 100€ / 5 Runden ≈ 20€ pro DCA-Runde
        min_edge_pct=MIN_EDGE_PCT,
        negative_streak_limit=NEG_STREAK_LIMIT,
        coin_cooldown_sec=int(COIN_COOLDOWN_H * 3600),
        rolling_window=ROLLING_WINDOW,
        rolling_loss_limit=ROLLING_LOSS_LIMIT_EUR,
        risk_off_sec=int(RISK_OFF_H * 3600),
        take_profit_pct=TAKE_PROFIT_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        max_hold_sec=MAX_HOLD_SEC,
        min_net_profit_eur=MIN_NET_PROFIT_EUR,
        max_open_positions=MAX_OPEN_POSITIONS,
        max_pair_exposure_eur=MAX_PAIR_EXPOSURE_EUR,
        min_cash_reserve_eur=MIN_CASH_RESERVE_EUR,
        trend_filter_enabled=TREND_FILTER_ENABLED,
        btc_risk_off_pct=BTC_RISK_OFF_PCT,
        eth_risk_off_pct=ETH_RISK_OFF_PCT,
        recovery_trigger_pct=RECOVERY_TRIGGER_PCT,
        recovery_reversal_pct=RECOVERY_REVERSAL_PCT,
        recovery_ticket_eur=RECOVERY_TICKET_EUR,
        recovery_max_exposure_factor=RECOVERY_MAX_EXPOSURE_FACTOR,
    )

    await send_telegram(build_overview_message())


    shutdown_event = asyncio.Event()

    # Graceful Shutdown auf SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(bot, shutdown_event)))

    bot_task = asyncio.create_task(bot.run())
    reporter_task = asyncio.create_task(status_reporter(bot, interval_sec=43200))

    await shutdown_event.wait()

    for task in (bot_task, reporter_task):
        task.cancel()
    await asyncio.gather(bot_task, reporter_task, return_exceptions=True)


async def _shutdown(bot: DCABot, shutdown_event: asyncio.Event) -> None:
    p = await bot.get_portfolio_value()
    logger.info(
        f"DCA-Bot gestoppt. Trades: {p['trade_count']} | "
        f"Portfolio: {p['total_value_eur']}€ | PnL: {p['pnl_eur']:+.2f}€"
    )
    await send_telegram(
        f"🛑 DCA-Bot gestoppt\n"
        f"Trades: {p['trade_count']} | "
        f"Endwert: {p['total_value_eur']}€ | "
        f"PnL: {p['pnl_eur']:+.2f}€ ({p['pnl_pct']:+.1f}%)"
    )
    shutdown_event.set()


if __name__ == "__main__":
    asyncio.run(main())
