from polybot.cli_env import apply_cli_env

apply_cli_env()

import asyncio
import logging
import logging.handlers
import os
from polybot import config
from polybot.binance_ws import binance_ws_loop, get_latest_price
from polybot.crypto_strategy import check_hft_signals
from polybot.executor import execute_taker_trade, execute_kraken_trade, RiskManager, calculate_kelly_size, calculate_kraken_kelly_size
from polybot.alerts import send_telegram, send_heartbeat
from polybot import reporter
from polybot.paper_db import init_db, log_paper_trade, get_paper_stats, get_unresolved_trades, resolve_trade, migrate_paper_trades_columns
from polybot.whale_tracker import whale_tracker_loop
from polybot.smart_money import smart_money_loop

# Setup Rotating Logs (50MB max, 3 backups)
os.makedirs("logs", exist_ok=True)
log_file = "logs/polybot_hft.log"
handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=50*1024*1024, backupCount=3)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] HFT: %(message)s")
handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

STOP_LOSS_PCT  = 0.005   # 0.5% Stop-Loss
TAKE_PROFIT_PCT = 0.003  # 0.3% Take-Profit
MAX_HOLD_SEC   = 600     # Fallback: nach 10 Min zwangsschließen
PAIR_COOLDOWN_SEC = 300  # 5 Min kein Re-Entry nach Exit

async def monitor_and_trade_loop(risk: RiskManager):
    """
    Entry: UP/DOWN Signal bei ≥0.20% 5-Min-Bewegung.
    Exit:  Gegensignal ODER Stop-Loss (-0.5%) ODER Take-Profit (+0.3%) ODER 10-Min-Timeout.
    """
    logger.info("⚔️ HFT Bot aktiv | SL=0.5% TP=0.3% | Pairs: DOT, SUI, AVAX, ETH, XBT")

    pairs = ["doteur", "suieur", "avaxeur", "etheur", "xbteur"]
    bankroll = config.BALANCE_USD
    last_trade_time: dict[str, float] = {}

    # Offene Position: {pair: {entry_price, direction, size, trade_id, opened_at}}
    open_pos: dict = {}

    while True:
        if risk.check_halt(bankroll):
            if risk.is_halted:
                logger.error("🛑 BOT HALTED.")
                break
            await asyncio.sleep(60)
            continue

        now = asyncio.get_event_loop().time()

        # ── EXIT-Logik für offene Positionen ──────────────────────────────
        for pair in list(open_pos.keys()):
            pos = open_pos[pair]
            current_price = get_latest_price(pair)
            if current_price <= 0:
                continue

            entry = pos["entry_price"]
            direction = pos["direction"]
            price_chg = (current_price - entry) / entry
            if direction == "DOWN":
                price_chg = -price_chg

            age = now - pos["opened_at"]
            exit_reason = None

            if price_chg <= -STOP_LOSS_PCT:
                exit_reason = f"🛑 Stop-Loss {price_chg:.2%}"
            elif price_chg >= TAKE_PROFIT_PCT:
                exit_reason = f"🎯 Take-Profit {price_chg:.2%}"
            elif age >= MAX_HOLD_SEC:
                exit_reason = f"⏱ Timeout {age:.0f}s"

            if exit_reason:
                fee_cost = 2 * config.CRYPTO_MAKER_FEE_RATE * pos["size"]
                real_pnl = price_chg * pos["size"] - fee_cost
                await resolve_trade(pos["trade_id"], current_price, real_pnl)
                logger.info(
                    f"{exit_reason} | {pair.upper()} {direction} | "
                    f"Entry={entry:.4f} Exit={current_price:.4f} PnL={real_pnl:+.4f}€"
                )
                reporter.queue_event("HFT/Whale",
                    f"{exit_reason} | {pair.upper()} {direction} | PnL {real_pnl:+.2f}€"
                )
                reporter.update_stats("HFT/Whale", sym="€", status="aktiv")
                last_trade_time[pair] = now
                del open_pos[pair]

        # ── ENTRY-Logik: nur wenn keine offene Position auf diesem Pair ───
        signals = check_hft_signals(pairs)

        for sig in signals:
            pair = sig["pair"]
            if pair in open_pos:
                continue  # bereits offen

            cooldown_left = PAIR_COOLDOWN_SEC - (now - last_trade_time.get(pair, 0))
            if cooldown_left > 0:
                continue

            direction = sig["signal"]
            kraken_price = get_latest_price(pair)
            if kraken_price <= 0:
                continue

            size_usd = calculate_kraken_kelly_size(sig["edge"], bankroll)
            if size_usd <= 0:
                continue

            result = await execute_kraken_trade(pair, direction, size_usd)
            fill_status = result.get("status", "error")

            if fill_status in ("filled_maker", "filled_taker", "filled", "ok", "paper"):
                trade_id = await log_paper_trade(
                    f"{pair.upper()} {direction}", direction, size_usd, kraken_price, sig["edge"]
                )
                open_pos[pair] = {
                    "entry_price": kraken_price,
                    "direction": direction,
                    "size": size_usd,
                    "trade_id": trade_id,
                    "opened_at": now,
                }
                logger.info(
                    f"📈 ENTRY {pair.upper()} {direction} | "
                    f"Edge {sig['delta']:.2%} | €{size_usd:.2f} @ €{kraken_price:,.2f} | "
                    f"SL={kraken_price*(1-STOP_LOSS_PCT):.2f} TP={kraken_price*(1+TAKE_PROFIT_PCT):.2f}"
                )
                reporter.queue_event("HFT/Whale",
                    f"📈 {pair.upper()} {direction} | Edge {sig['delta']:.2%} | €{size_usd:.2f}"
                )
                break

        await asyncio.sleep(1.0)

async def trade_resolver_loop():
    """
    Löst Paper-Trades nach SIGNAL_WINDOW_SEC auf.
    Holt den echten Kraken-Preis und berechnet den tatsächlichen PnL.
    """
    from polybot.crypto_strategy import SIGNAL_WINDOW_SEC
    logger.info("🔍 Trade-Resolver aktiv (löst Trades nach 5 Min auf)")

    while True:
        await asyncio.sleep(30)
        try:
            unresolved = await get_unresolved_trades(min_age_sec=SIGNAL_WINDOW_SEC)
            for trade in unresolved:
                # Pair aus market_question extrahieren: "DOTEUR UP" → "doteur"
                parts = trade["market_question"].lower().split()
                if not parts:
                    continue
                pair = parts[0]  # z.B. "doteur"

                exit_price = get_latest_price(pair)
                if exit_price <= 0:
                    import time as _time
                    age_sec = _time.time() - trade["timestamp"]
                    if age_sec > 86400:  # >24h ohne Preis → als veraltet abschließen
                        await resolve_trade(trade["id"], 0.0, 0.0)
                        logger.warning(f"🗑️ Trade #{trade['id']} ({pair}) nach {age_sec/3600:.1f}h ohne Preis abgeschlossen (PnL=0)")
                    else:
                        logger.debug(f"⚠️ Kein Preis für {pair}, Trade #{trade['id']} wird übersprungen")
                    continue

                entry_price = trade["entry_price"]
                size = trade["size"]
                side = trade["side"]  # "UP" oder "DOWN"

                # Richtungsbasierter PnL
                price_change = (exit_price - entry_price) / entry_price
                if side == "DOWN":
                    price_change = -price_change

                # Echte Fees abziehen (2× Maker-Fee für Runde: Kauf + Verkauf)
                fee_cost = 2 * config.CRYPTO_MAKER_FEE_RATE * size
                gross_pnl = price_change * size
                real_pnl = gross_pnl - fee_cost

                await resolve_trade(trade["id"], exit_price, real_pnl)

                result_emoji = "✅" if real_pnl > 0 else "❌"
                logger.info(
                    f"{result_emoji} Trade #{trade['id']} {trade['market_question']}: "
                    f"Entry={entry_price:.4f} Exit={exit_price:.4f} "
                    f"PnL={real_pnl:+.4f}$ (Gross={gross_pnl:+.4f}$ Fee={fee_cost:.4f}$)"
                )
        except Exception as e:
            logger.exception(f"Resolver Fehler: {e}")


async def heartbeat_loop():
    """Sends regular Telegram stats to confirm bot is alive."""
    while True:
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)
        stats = await get_paper_stats()
        stats["balance"] = config.BALANCE_USD # Simplify for paper
        resolved = stats.get("resolved", 0)
        total_pnl = stats.get("total_pnl", 0.0)
        reporter.update_stats("HFT/Whale",
            trades=stats.get("count", 0),
            pnl=total_pnl,
            sym="$",
            status="aktiv"
        )

async def _supervised(coro, name: str):
    """Wrapper that logs exceptions from background tasks."""
    try:
        await coro
    except Exception as e:
        logger.exception(f"Task '{name}' crashed: {e}")
        await send_telegram(f"🚨 HFT-Task '{name}' abgestürzt: {e}")
        raise

async def main():
    logger.info(f"🚀 Starting {config.BOT_NAME} [Production HFT Mode]")
    config.validate_config()
    await init_db()
    await migrate_paper_trades_columns()
    
    reporter.update_stats("HFT/Whale", trades=0, pnl=0.0, sym="$", status="aktiv")
    
    risk = RiskManager()
    
    # Concurrent tasks
    tasks = [
        asyncio.create_task(_supervised(binance_ws_loop(["xbteur", "etheur", "doteur", "suieur", "avaxeur"]), "binance_ws")),
        asyncio.create_task(_supervised(monitor_and_trade_loop(risk), "hft_monitor")),
        asyncio.create_task(_supervised(trade_resolver_loop(), "trade_resolver")),
        asyncio.create_task(_supervised(heartbeat_loop(), "heartbeat")),
        asyncio.create_task(_supervised(whale_tracker_loop(), "whale_tracker")),
        asyncio.create_task(_supervised(smart_money_loop(risk), "smart_money")),
        asyncio.create_task(_supervised(reporter.reporter_loop(), "reporter")),
        asyncio.create_task(_supervised(reporter.daily_analysis_loop(), "daily_analysis")),
    ]
    
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown by user.")
    except Exception as e:
        logger.exception("Fatal error in main loop")
