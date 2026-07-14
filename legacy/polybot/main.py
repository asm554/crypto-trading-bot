from polybot.cli_env import apply_cli_env

apply_cli_env()

import asyncio
import time
import logging
from polybot import config
from polybot.market import get_active_markets, get_orderbook
from polybot.weather_api import fetch_weather_forecast
from polybot.data_logger import log_snapshot, log_trade
from polybot.strategy import calculate_maker_ev, get_maker_position_size
from polybot.fees import buy_hold_ev_per_share, taker_fee_usdc, weather_maker_entry_fee_per_share
from polybot.executor import RiskManager, execute_maker_trade
from polybot.alerts import send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Globaler Status Cache
last_hourly_scan = 0
weather_memory = {}

async def hourly_scan_loop():
    """Holt sich stündlich die Makro-Daten der Städte"""
    global last_hourly_scan, weather_memory
    
    now = time.time()
    if now - last_hourly_scan >= config.HOURLY_SCAN_SEC:
        logger.info("🌍 Starte Hourly Weather API Scan...")
        for city in config.CITIES[:5]: # MVP: Nur erste 5 Städte tracken wegen API Limits
            forecast = await fetch_weather_forecast(city)
            weather_memory[city] = forecast
            
        last_hourly_scan = time.time()
        logger.info("✅ Hourly Scan abgeschlossen.")

async def ten_minute_monitor_loop(risk_manager: RiskManager):
    """Prüft Orderbooks gegen Weather Memory, loggt und postet Maker Trades"""
    logger.info("🔍 Starte 10-Min Orderbook Tracker...")
    
    bankroll = config.BALANCE_USD
    if risk_manager.check_halt(bankroll):
        return

    markets = await get_active_markets()
    
    for market in markets:
        question = market.get("question", "Unknown")
        no_token_id = market["no_token_id"]
        
        # Finde passende Stadt aus config (Basic Matching)
        target_city = "New York, NY" # Fallback
        for c in config.CITIES:
            if c.split(",")[0] in question:
                target_city = c
                break
                
        forecast = weather_memory.get(target_city, {"implied_probability": 1.0})
        p_win = forecast["implied_probability"] # Vorerst p=1.0 "Sicher" wie zitiert
        
        book = await get_orderbook(no_token_id)
        best_bid = book.get("bid", 0.0)
        # Wir wollen als Maker agieren: Wir legen unsere Limit Order knapp über best_bid
        maker_offer_price = round(best_bid + 0.01, 3) 
        # Wenn Bid = 0.94, bieten wir 0.95 (Wir reihen uns ins Orderbuch ein)
        
        # EV netto (Maker 0 Fee laut Polymarket; optional Stress = Taker-Weather-Rate)
        ev = calculate_maker_ev(p_win, maker_offer_price)
        fee_ps = weather_maker_entry_fee_per_share(maker_offer_price)
        ev_gross = buy_hold_ev_per_share(p_win, maker_offer_price, fee_per_share=0.0)
        
        # Snapshot im JSON für MAE self-calibration loggen
        log_snapshot(market["market_id"], question, forecast, book)
        
        if ev > config.MIN_EV_PERCENT:
            size_shares = get_maker_position_size(ev, maker_offer_price, p_win, bankroll)
            
            if size_shares > 10: # Mindestmenge
                est_fee = taker_fee_usdc(size_shares, maker_offer_price, config.WEATHER_TAKER_FEE_RATE) if fee_ps > 0 else 0.0
                logger.info(
                    f"💎 Positive EV (net): {ev:.2%} | gross: {ev_gross:.2%} | fee/share: ${fee_ps:.5f} auf '{question}'"
                )
                
                # Poste Maker Order
                order = await execute_maker_trade(no_token_id, "BUY", size_shares, maker_offer_price)
                if not order.get("error"):
                    log_trade(
                        market["market_id"],
                        "BUY",
                        maker_offer_price,
                        size_shares,
                        ev,
                        fee_usdc=est_fee,
                        fee_per_share=fee_ps,
                        ev_gross_per_share=ev_gross,
                    )
                    await send_telegram(
                        f"📊 MAKER ORDER:\n{question}\n"
                        f"Limit: {size_shares} @ ${maker_offer_price}\n"
                        f"EV net: {ev:.2%} (gross {ev_gross:.2%})\n"
                        f"Einstiegsgebühr (Modell): ${est_fee:.4f} USDC"
                    )
                    
        await asyncio.sleep(0.5)

async def main():
    logger.info(f"🚀 Starte {config.BOT_NAME}...")
    config.validate_config()
    if config.PAPER_MODE: logger.info("⚠️ PAPER MODE ACTIVE")
    await send_telegram(f"🤖 {config.BOT_NAME} online. Game Theory Model Aktiv.")
    
    risk = RiskManager()
    
    while True:
        try:
            # 1. Hourly Scan
            await hourly_scan_loop()
            
            # 2. Monitor Loop (wird alle 10 mins gemäß Sleep aufgerufen)
            await ten_minute_monitor_loop(risk)
            
        except Exception as e:
            logger.error(f"Critical Loop Error: {e}")
            await send_telegram(f"⚠️ Error: {e}")
            
        # Warte 10 Minuten bis zum nächsten Market Scan
        await asyncio.sleep(config.MARKET_MONITOR_SEC)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown by user.")
