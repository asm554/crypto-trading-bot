import asyncio
import aiohttp
import logging
import time
from . import config
from .gamma_api import get_market_title

logger = logging.getLogger(__name__)

# State for tracking whale positions
# key: wallet_address, value: { (condition_id, outcome): size }
whale_states = {}


# ========================= CLASSIFIER =========================

def classify_whale(wallet_data: dict) -> dict:
    """
    Bewertet eine Wallet mit einem Score 0-100.
    DIRECTIONAL = echter informierter Trader (Score >= WHALE_CLASSIFIER_MIN_SCORE)
    BOT_MM      = Market Maker oder Bot → überspringen
    """
    score = 0
    reasons = []

    avg_hold = wallet_data.get("avg_hold_days", 0)
    if avg_hold > 7:
        score += 35
        reasons.append("✅ lange Haltedauer")
    elif avg_hold < 1:
        score -= 40
        reasons.append("❌ Scalper/Bot")

    trades_per_day = wallet_data.get("trades_per_day", 0)
    if trades_per_day < 3:
        score += 25
        reasons.append("✅ niedrige Frequenz")
    elif trades_per_day > 30:
        score -= 35
        reasons.append("❌ HFT/MM")

    maker_ratio = wallet_data.get("maker_ratio", 0)
    if maker_ratio > 0.6:
        score += 15
        reasons.append("✅ guter Maker")

    win_rate = wallet_data.get("win_rate_election", 0)
    if win_rate > 0.62:
        score += 20
        reasons.append("✅ starke Election-Performance")

    avg_pos = wallet_data.get("avg_position_usd", 0)
    if avg_pos > 5000:
        score += 15

    final_score = max(0, min(100, score))
    min_score = config.WHALE_CLASSIFIER_MIN_SCORE

    return {
        "wallet": wallet_data.get("wallet", "unknown"),
        "whale_score": final_score,
        "type": "DIRECTIONAL" if final_score >= min_score else "BOT_MM",
        "reasons": reasons,
        "confidence": "HIGH" if final_score >= 80 else "MEDIUM",
        "avg_hold_days": avg_hold,
        "trades_per_day": trades_per_day,
    }


def apply_hard_filters(signal: dict) -> bool:
    """
    Gibt False zurück wenn das Signal gefiltert werden soll.
    Regeln: Mindest-Notional, Restlaufzeit, kein MM, kein Hedge.
    """
    if signal.get("net_position_usd", signal.get("value", 0)) < config.WHALE_MIN_NOTIONAL_USD:
        return False
    if signal.get("days_to_expiry", 999) < config.WHALE_MIN_DAYS_LEFT:
        return False
    if signal.get("avg_hold_days", 999) < 1 or signal.get("trades_per_day", 999) > 40:
        return False
    if signal.get("has_hedge", False):
        return False
    return True


def calculate_risk(signal: dict, bankroll_usd: float) -> dict:
    """Empfohlene Positionsgröße: max. WHALE_MAX_BANKROLL_PCT % des Kapitals,
    aber nicht mehr als 30 % der Whale-Position."""
    max_risk = bankroll_usd * (config.WHALE_MAX_BANKROLL_PCT / 100)
    delta = signal.get("delta_usd", signal.get("value", 0))
    recommended = min(max_risk, delta * 0.3)
    return {
        "recommended_usdc": round(recommended, 2),
        "max_bankroll_pct": config.WHALE_MAX_BANKROLL_PCT,
    }

async def fetch_leaderboard():
    """Fetches top 5 All-Time and Top 5 Weekly traders."""
    wallets = []
    
    # We try both windows to get a mix of stability and momentum
    windows = ["all", "week"]
    
    async with aiohttp.ClientSession() as session:
        for window in windows:
            url = f"https://data-api.polymarket.com/v1/leaderboard?limit=5&window={window}&orderBy=pnl"
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for entry in data:
                            addr = entry.get("proxyWallet")
                            if addr and addr not in wallets:
                                wallets.append(addr)
            except Exception as e:
                logger.error(f"Error fetching leaderboard ({window}): {e}")
                
    return wallets[:config.WHALE_TOP_LIMIT]

async def fetch_user_positions(address: str) -> dict:
    """Fetches current open positions for a specific wallet."""
    url = f"https://data-api.polymarket.com/positions?user={address}"
    positions = {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    for pos in data:
                        # We identify a position by market_slug and outcome
                        # This is much more robust than cryptic IDs
                        slug = pos.get("market_slug", "unknown")
                        outcome = pos.get("outcome", "unknown")
                        size = float(pos.get("size", 0))
                        price = float(pos.get("price", 0))
                        
                        if slug != "unknown" and size > 0:
                            positions[(slug, outcome)] = {
                                "size": size,
                                "price": price,
                                "value": size * price # Calculated current value
                            }
    except Exception as e:
        logger.error(f"Error fetching positions for {address}: {e}")
        
    return positions

async def detect_deltas(address: str, current_positions: dict):
    """
    Hybrid delta detection: prüft Share-Delta UND USD-Delta.
    Gibt Liste von Alerts zurück.
    """
    alerts = []
    old_positions = whale_states.get(address, {})

    for (slug, outcome), data in current_positions.items():
        new_size = data["size"]
        new_price = data["price"]
        new_value = data["value"]

        old_data = old_positions.get((slug, outcome))
        old_size = old_data["size"] if old_data else 0
        old_value = old_data["value"] if old_data else 0

        share_delta = new_size - old_size
        usd_delta = new_value - old_value

        # Trigger wenn Shares zugenommen haben UND USD-Delta über Mindestschwelle
        if share_delta > 0 and usd_delta >= config.WHALE_MIN_BET:
            market_title = slug.replace("-", " ").title()
            action = "NEU EINGESTIEGEN" if old_size == 0 else "AUFGESTOCKT"

            alerts.append({
                "address": address,
                "action": action,
                "market": market_title,
                "market_slug": slug,
                "outcome": outcome,
                "value": usd_delta,          # USD-Delta für Risk-Engine
                "net_position_usd": new_value,
                "delta_usd": usd_delta,
                "wallet": address,
                # Felder für Classifier (werden aus Leaderboard-Daten befüllt falls vorhanden)
                "avg_hold_days": 0,
                "trades_per_day": 0,
            })

    whale_states[address] = current_positions
    return alerts

_dune_tick = 0


async def whale_tracker_loop():
    """Main background task for whale monitoring."""
    global _dune_tick
    logger.info("🐋 Whale Tracker aktiviert. Scanne Top-Trader...")
    from .alerts import send_telegram
    from .paper_db import load_whale_states, save_whale_positions
    from .news_agent import check_news_alignment

    # Init state from DB
    global whale_states
    whale_states = await load_whale_states()
    logger.info(f"📁 Geladene Whale-Daten für {len(whale_states)} Adressen.")

    while True:
        _dune_tick += 1

        # Dune-Snapshot jede 60. Iteration rotieren (~1h bei 60s Interval)
        if _dune_tick % 60 == 1:
            try:
                from .dune_fetcher import fetch_and_rotate
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, fetch_and_rotate)
                logger.info("📊 Dune-Snapshot rotiert.")
            except Exception as e:
                logger.warning(f"Dune-Fetch übersprungen: {e}")

        try:
            # 1. Get Top Traders
            top_wallets = await fetch_leaderboard()

            for addr in top_wallets:
                # 2. Get current positions
                current_pos = await fetch_user_positions(addr)

                if current_pos:
                    # 3. Detect changes
                    deltas = await detect_deltas(addr, current_pos)

                    # 4. Klassifizieren, filtern und alerten
                    for d in deltas:
                        classified = classify_whale(d)

                        if classified["type"] != "DIRECTIONAL":
                            logger.debug(f"🤖 Bot/MM übersprungen: {d['address'][:10]}")
                            continue

                        if not apply_hard_filters(d):
                            logger.debug(f"🚫 Hard-Filter: {d['market']}")
                            continue

                        risk = calculate_risk(d, bankroll_usd=config.BALANCE_USD)

                        # News-Agent Score-Boost
                        try:
                            news = await check_news_alignment(d["market"], classified["whale_score"])
                            score_modifier = news.get("score_modifier", 0)
                            boosted_score = classified["whale_score"] + score_modifier
                            news_line = (
                                f"📰 News: {news['news_summary']} ({news['confidence']})\n"
                                f"📊 Score: <b>{boosted_score}/100</b> "
                                f"(Basis {classified['whale_score']} {score_modifier:+d})"
                            )
                        except Exception as e:
                            logger.warning(f"News-Agent Fehler: {e}")
                            boosted_score = classified["whale_score"]
                            news_line = f"📊 Score: <b>{boosted_score}/100</b> ({classified['confidence']})"

                        reasons_str = " • ".join(classified["reasons"]) if classified["reasons"] else "keine Daten"
                        msg = (
                            f"🐳 <b>WHALE SIGNAL</b>\n\n"
                            f"📍 <b>{d['market']}</b>\n"
                            f"💎 Ergebnis: <b>{d['outcome']}</b>\n\n"
                            f"👤 Wallet: <code>{d['address'][-8:]}</code>\n"
                            f"⚡ Aktion: <b>{d['action']}</b>\n"
                            f"🏷️ Typ: <b>{classified['type']}</b>\n\n"
                            f"💰 Delta: <b>${d['delta_usd']:,.0f}</b>\n"
                            f"📈 Empfohlener Einsatz: <b>${risk['recommended_usdc']}</b> "
                            f"({risk['max_bankroll_pct']}% Kapital)\n\n"
                            f"{news_line}\n\n"
                            f"📝 {reasons_str}"
                        )
                        await send_telegram(msg)

                    # 5. Persistent Save
                    await save_whale_positions(addr, current_pos)

                # Sleep briefly between users to respect rate limits
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Whale Tracker Loop Error: {e}")

        await asyncio.sleep(config.WHALE_INTERVAL)
