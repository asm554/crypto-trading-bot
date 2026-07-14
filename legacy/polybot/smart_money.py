"""
Smart Money Signal Tracker — fetches on-chain smart money signals
from Binance Web3 API (Solana + BSC), alerts via Telegram, and trades.

Two execution paths:
1. Solana signals → Jupiter DEX swap (buy the exact token)
2. BSC signals  → Kraken spot (ETH/BTC as sentiment proxy)
"""

import asyncio
import aiohttp
import logging
import time
from . import config
from .alerts import send_telegram
from . import reporter
from .binance_ws import get_latest_price, get_best_bid, get_best_ask
from .executor import execute_kraken_trade, calculate_kraken_kelly_size, RiskManager
from .paper_db import log_paper_trade, save_sm_position, close_sm_position, load_sm_positions

logger = logging.getLogger(__name__)

API_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai"
HEADERS = {
    "Content-Type": "application/json",
    "Accept-Encoding": "identity",
    "User-Agent": "binance-web3/1.1 (Skill)",
}

CHAINS = {
    "501": "SOL",
    "56": "BSC",
}

# Map smart money sentiment to Kraken pairs (most liquid)
TRADE_PAIRS = ["etheur", "xbteur"]

# Track already-alerted signal IDs to avoid duplicates
_alerted_ids: set[int] = set()


async def fetch_signals(session: aiohttp.ClientSession, chain_id: str) -> list[dict]:
    """Fetch smart money signals for a given chain."""
    payload = {
        "smartSignalType": "",
        "page": 1,
        "pageSize": 50,
        "chainId": chain_id,
    }
    try:
        async with session.post(API_URL, json=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            if data.get("success") and data.get("data"):
                return data["data"]
    except Exception as e:
        logger.warning(f"Smart Money API error ({chain_id}): {e}")
    return []


def has_wash_trading(sig: dict) -> bool:
    """Check if signal has wash trading tags."""
    tags = sig.get("tokenTag", {})
    if "Wash Trading Behavior" in tags:
        tag_names = [t.get("tagName", "") for t in tags["Wash Trading Behavior"]]
        if "Wash Trading" in tag_names or "Insider Wash Trading" in tag_names:
            return True
    return False


def passes_filter(sig: dict) -> bool:
    """Filter signals worth alerting on."""
    # Accept "valid" (active) and recent "timeout" signals (within 2h)
    status = sig.get("status", "")
    if status not in ("valid", "timeout", "active"):
        return False
    if status == "timeout":
        trigger_ms = sig.get("signalTriggerTime", 0)
        import time
        if trigger_ms and (time.time() * 1000 - trigger_ms) > 7_200_000:
            return False  # Älter als 2h → überspringen
    if sig.get("smartMoneyCount", 0) < config.SM_MIN_WALLETS:
        return False
    if sig.get("exitRate", 100) > config.SM_MAX_EXIT_RATE:
        return False
    if has_wash_trading(sig):
        return False
    return True


def passes_trade_filter(sig: dict) -> bool:
    """Stricter filter for signals that trigger actual Kraken trades."""
    if not passes_filter(sig):
        return False
    # Higher bar for real trades: more wallets, meaningful value
    if sig.get("smartMoneyCount", 0) < config.SM_TRADE_MIN_WALLETS:
        return False
    total_value = float(sig.get("totalTokenValue", 0))
    if total_value < config.SM_TRADE_MIN_VALUE_USD:
        return False
    return True


def calculate_sentiment(signals: list[dict]) -> dict:
    """
    Aggregate smart money signals into a directional sentiment score.

    Returns: {
        "direction": "UP" | "DOWN" | None,
        "score": float (0-1, strength),
        "buy_count": int,
        "sell_count": int,
        "total_buy_value": float,
        "total_sell_value": float,
    }
    """
    buy_count = 0
    sell_count = 0
    buy_value = 0.0
    sell_value = 0.0

    for sig in signals:
        if not passes_trade_filter(sig):
            continue

        value = float(sig.get("totalTokenValue", 0))
        wallets = sig.get("smartMoneyCount", 1)

        # Weight by wallet count (more wallets = stronger signal)
        weighted_value = value * (1 + wallets * 0.1)

        if sig.get("direction") == "buy":
            buy_count += 1
            buy_value += weighted_value
        elif sig.get("direction") == "sell":
            sell_count += 1
            sell_value += weighted_value

    total = buy_value + sell_value
    if total == 0:
        return {"direction": None, "score": 0, "buy_count": buy_count, "sell_count": sell_count,
                "total_buy_value": buy_value, "total_sell_value": sell_value}

    # Net direction
    if buy_value > sell_value and buy_count >= config.SM_TRADE_MIN_SIGNALS:
        score = (buy_value - sell_value) / total
        return {"direction": "UP", "score": score, "buy_count": buy_count, "sell_count": sell_count,
                "total_buy_value": buy_value, "total_sell_value": sell_value}
    elif sell_value > buy_value and sell_count >= config.SM_TRADE_MIN_SIGNALS:
        score = (sell_value - buy_value) / total
        return {"direction": "DOWN", "score": score, "buy_count": buy_count, "sell_count": sell_count,
                "total_buy_value": buy_value, "total_sell_value": sell_value}

    return {"direction": None, "score": 0, "buy_count": buy_count, "sell_count": sell_count,
            "total_buy_value": buy_value, "total_sell_value": sell_value}


def sentiment_to_edge(score: float) -> float:
    """
    Convert sentiment score (0-1) to a synthetic edge for Kelly sizing.

    Conservative mapping:
    - score < 0.3  → no trade (too weak)
    - score 0.3-0.6 → edge 0.5%-1.0%
    - score 0.6-1.0 → edge 1.0%-2.0% (capped)
    """
    if score < 0.3:
        return 0.0
    # Linear scale: 0.3 → 0.005, 1.0 → 0.02
    return min(0.02, 0.005 + (score - 0.3) * 0.0214)


def format_alert(sig: dict, chain_label: str) -> str:
    """Format a signal into a Telegram message."""
    ticker = sig.get("ticker", "???")
    direction = sig.get("direction", "?").upper()
    sm_count = sig.get("smartMoneyCount", 0)
    alert_price = float(sig.get("alertPrice", 0))
    current_price = float(sig.get("currentPrice", 0))
    total_value = float(sig.get("totalTokenValue", 0))
    exit_rate = sig.get("exitRate", 0)
    max_gain = float(sig.get("maxGain", 0))
    mcap = float(sig.get("currentMarketCap", 0))
    platform = sig.get("launchPlatform") or "Unknown"

    pct_change = ((current_price - alert_price) / alert_price * 100) if alert_price > 0 else 0

    if mcap >= 1_000_000:
        mcap_str = f"${mcap / 1_000_000:.1f}M"
    elif mcap >= 1_000:
        mcap_str = f"${mcap / 1_000:.0f}K"
    else:
        mcap_str = f"${mcap:.0f}"

    return (
        f"🧠 SMART MONEY {direction} — {ticker} [{chain_label}]\n"
        f"Wallets: {sm_count} | Platform: {platform}\n"
        f"Trigger: ${alert_price:.6g} → Now: ${current_price:.6g} ({pct_change:+.1f}%)\n"
        f"MCap: {mcap_str} | Value: ${total_value:,.0f}\n"
        f"Max Gain: {max_gain:.1f}% | Exit Rate: {exit_rate}%"
    )


def _get_jupiter_client():
    """Lazy-init Jupiter client (only when SOL_PRIVATE_KEY is set)."""
    global _jupiter
    if _jupiter is not None:
        return _jupiter
    if not config.SOL_PRIVATE_KEY:
        return None
    from .jupiter_client import JupiterClient
    _jupiter = JupiterClient(config.SOL_PRIVATE_KEY, config.SOL_RPC_URL)
    return _jupiter

_jupiter = None

# Track active Jupiter positions: {contract_address: {"size_usd", "entry_time"}}
_jupiter_positions: dict[str, dict] = {}


def calculate_jupiter_size(sig: dict, bankroll: float) -> float:
    """
    Calculate trade size for a Jupiter swap based on signal strength.

    Factors: smartMoneyCount, totalTokenValue, exitRate.
    Conservative: max SM_JUPITER_MAX_USD per trade.
    """
    sm_count = sig.get("smartMoneyCount", 0)
    total_value = float(sig.get("totalTokenValue", 0))
    exit_rate = sig.get("exitRate", 0)

    # Base size: fraction of bankroll
    base = bankroll * config.KELLY_FRACTION * 0.25  # ~3% of bankroll

    # Scale up with wallet count (4 wallets = 1x, 8 wallets = 2x)
    wallet_mult = min(2.0, sm_count / 4.0)

    # Scale up with total value (3k = 1x, 10k = 2x)
    value_mult = min(2.0, total_value / 3000.0)

    # Scale down with exit rate (0% = 1x, 40% = 0.6x)
    exit_mult = max(0.4, 1.0 - exit_rate / 100.0)

    size = base * wallet_mult * value_mult * exit_mult
    return min(max(0.0, size), config.SM_JUPITER_MAX_USD)


async def execute_jupiter_trade(sig: dict, risk: RiskManager, bankroll: float) -> bool:
    """
    Execute a Jupiter DEX swap for a Solana smart money signal.
    Buys the exact token that smart money is accumulating.
    """
    jupiter = _get_jupiter_client()
    contract = sig.get("contractAddress", "")
    ticker = sig.get("ticker", "???")

    if not contract:
        return False

    # Skip if we already hold this token
    if contract in _jupiter_positions:
        logger.info(f"Jupiter: Bereits Position in {ticker} — skip")
        return False

    # Max concurrent positions
    if len(_jupiter_positions) >= config.SM_JUPITER_MAX_POSITIONS:
        logger.info(f"Jupiter: Max Positionen ({config.SM_JUPITER_MAX_POSITIONS}) erreicht — skip")
        return False

    if risk.check_halt(bankroll):
        logger.warning("Jupiter Trade blocked: RiskManager halt")
        return False

    size_usd = calculate_jupiter_size(sig, bankroll)
    if size_usd < 0.50:
        logger.info(f"Jupiter: Size zu klein für {ticker} (${size_usd:.2f})")
        return False

    current_price = float(sig.get("currentPrice", 0))
    mcap = float(sig.get("currentMarketCap", 0))
    sm_count = sig.get("smartMoneyCount", 0)

    # Paper mode
    if config.PAPER_MODE:
        logger.info(f"[PAPER JUPITER] BUY ${size_usd:.2f} {ticker} @ ${current_price:.8g}")
        await log_paper_trade(
            f"JUP_{ticker}", "BUY", size_usd, current_price, 0.0
        )
        _jupiter_positions[contract] = {"size_usd": size_usd, "entry_time": time.time()}
        await save_sm_position(contract, ticker, "SOL", size_usd, current_price)
        reporter.queue_event("Smart Money", 
            f"🧠🟣 JUPITER PAPER TRADE — {ticker}\n"
            f"BUY ${size_usd:.2f} | Price: ${current_price:.8g}\n"
            f"MCap: ${mcap:,.0f} | Smart Wallets: {sm_count}\n"
            f"Contract: {contract[:20]}..."
        )
        return True

    # Live mode — need Jupiter client
    if not jupiter:
        logger.warning("Jupiter Trade übersprungen: SOL_PRIVATE_KEY nicht konfiguriert")
        return False

    slippage = config.SM_JUPITER_SLIPPAGE_BPS
    result = await jupiter.swap_usdc_for_token(contract, size_usd, slippage)

    if result.get("status") == "ok":
        txid = result.get("txid", "")
        _jupiter_positions[contract] = {"size_usd": size_usd, "entry_time": time.time()}
        await save_sm_position(contract, ticker, "SOL", size_usd, current_price)

        await log_paper_trade(
            f"JUP_{ticker}", "BUY", size_usd, current_price, 0.0
        )
        reporter.queue_event("Smart Money", 
            f"🧠🟣 JUPITER SWAP — {ticker}\n"
            f"BUY ${size_usd:.2f} | Price: ${current_price:.8g}\n"
            f"MCap: ${mcap:,.0f} | Smart Wallets: {sm_count}\n"
            f"TX: {txid[:20]}..."
        )
        return True
    else:
        reason = result.get("reason", "unknown")
        logger.warning(f"Jupiter Swap fehlgeschlagen für {ticker}: {reason}")
        return False


async def execute_kraken_sentiment_trade(sentiment: dict, risk: RiskManager, bankroll: float) -> bool:
    """
    Execute a Kraken trade based on aggregate BSC smart money sentiment.
    Fallback path for non-Solana signals.
    """
    direction = sentiment["direction"]
    score = sentiment["score"]

    if direction is None:
        return False

    edge = sentiment_to_edge(score)
    if edge <= 0:
        return False

    if risk.check_halt(bankroll):
        logger.warning("Smart Money Kraken Trade blocked: RiskManager halt")
        return False

    for pair in TRADE_PAIRS:
        price = get_latest_price(pair)
        if price <= 0:
            continue

        size_usd = calculate_kraken_kelly_size(edge, bankroll)
        if size_usd <= 0:
            continue

        result = await execute_kraken_trade(pair, direction, size_usd)
        fill_status = result.get("status", "error")

        if fill_status in ("filled_maker", "filled_taker", "filled", "ok"):
            net_edge = edge - 2 * config.CRYPTO_MAKER_FEE_RATE
            fee_label = "Maker" if fill_status == "filled_maker" else "Taker"

            await log_paper_trade(
                f"SM_{pair.upper()} {direction}", direction, size_usd, price, edge
            )
            reporter.queue_event("Smart Money", 
                f"🧠⚡ SMART MONEY KRAKEN — {pair.upper()} {direction}\n"
                f"Sentiment: {score:.0%} | Buys: {sentiment['buy_count']} | Sells: {sentiment['sell_count']}\n"
                f"Edge: {edge:.2%} | Net: {net_edge:.2%} | {fee_label}\n"
                f"Size: €{size_usd:.2f} | Price: €{price:,.2f}"
            )
            return True

    return False


async def check_jupiter_exits():
    """
    Check if any Jupiter positions should be exited.
    Exit if: held longer than max hold time.
    """
    jupiter = _get_jupiter_client()
    if not jupiter and not config.PAPER_MODE:
        return

    now = time.time()
    to_remove = []

    SLIPPAGE = config.SM_JUPITER_SLIPPAGE_BPS / 10000  # 150bps = 1.5%

    for contract, pos in _jupiter_positions.items():
        hold_sec = now - pos["entry_time"]
        if hold_sec >= config.SM_JUPITER_MAX_HOLD_SEC:
            size_usd   = pos.get("size_usd", 0)
            entry_price = pos.get("entry_price", 0)

            if config.PAPER_MODE:
                # Simulierten Exit-Preis aus Binance WS holen (Ticker-Fallback)
                exit_price = 0.0
                try:
                    from polybot.binance_ws import get_latest_price
                    ticker_sym = contract[:10].lower()  # Näherung
                    exit_price = get_latest_price(ticker_sym)
                except Exception:
                    pass

                # PnL: Preisänderung × Größe − Slippage beider Seiten
                if entry_price > 0 and exit_price > 0:
                    price_change = (exit_price - entry_price) / entry_price
                    gross_pnl   = price_change * size_usd
                    total_slip   = size_usd * SLIPPAGE * 2  # Kauf + Verkauf
                    real_pnl    = gross_pnl - total_slip
                else:
                    # Kein Preis verfügbar → konservativ: nur Slippage-Kosten
                    real_pnl = -(size_usd * SLIPPAGE * 2)

                # Trade in DB auflösen
                try:
                    async with __import__('aiosqlite').connect(
                        __import__('os').path.join(__import__('os').path.dirname(__file__), "data", "paper_trades.db")
                    ) as db:
                        async with db.execute(
                            "SELECT id FROM paper_trades WHERE market_question LIKE ? "
                            "AND resolved_at IS NULL ORDER BY timestamp DESC LIMIT 1",
                            (f"JUP_%",)
                        ) as cur:
                            row = await cur.fetchone()
                        if row:
                            await db.execute(
                                "UPDATE paper_trades SET exit_price=?, resolved_at=?, real_pnl=? WHERE id=?",
                                (exit_price, now, real_pnl, row[0])
                            )
                            await db.commit()
                except Exception as e:
                    logger.warning(f"Jupiter PnL DB-Update fehlgeschlagen: {e}")

                result_emoji = "✅" if real_pnl >= 0 else "❌"
                logger.info(f"[PAPER] Jupiter Exit {contract[:20]}: PnL {real_pnl:+.2f}$ (slip {total_slip:.2f}$)")
                reporter.queue_event("Smart Money",
                    f"🟣 JUPITER EXIT {result_emoji} | PnL {real_pnl:+.2f}$ | "
                    f"Hold {hold_sec/60:.0f}min | Slip {SLIPPAGE*2*100:.1f}%"
                )
                reporter.update_stats("Smart Money", status="aktiv")
                to_remove.append(contract)

            elif jupiter:
                balance = await jupiter.get_token_balance(contract)
                if balance > 0:
                    result = await jupiter.swap_token_for_usdc(contract, balance)
                    if result.get("status") == "ok":
                        usdc_received = result.get("usdc_received", 0)
                        real_pnl = usdc_received - size_usd
                        reporter.queue_event("Smart Money",
                            f"🟣 JUPITER SELL ✅ | PnL {real_pnl:+.2f}$ | TX: {result.get('txid','')[:20]}"
                        )
                        to_remove.append(contract)
                else:
                    to_remove.append(contract)

    for c in to_remove:
        _jupiter_positions.pop(c, None)
        await close_sm_position(c)


async def smart_money_loop(risk: RiskManager = None):
    """Main loop: poll smart money signals, alert, and trade."""
    logger.info("🧠 Smart Money Tracker gestartet (Jupiter + Kraken)")
    reporter.queue_event("Smart Money", 
        "🧠 Smart Money Tracker aktiv\n"
        f"SOL Signale → Jupiter DEX {'(PAPER)' if config.PAPER_MODE else '(LIVE)'}\n"
        f"BSC Signale → Kraken Sentiment"
    )

    if risk is None:
        risk = RiskManager()

    # Recover open positions from DB (survives bot restart)
    global _jupiter_positions
    try:
        _jupiter_positions = await load_sm_positions()
        if _jupiter_positions:
            logger.info(f"🔄 {len(_jupiter_positions)} offene SM-Positionen aus DB geladen")
    except Exception as e:
        logger.warning(f"SM Position Recovery fehlgeschlagen: {e}")

    bankroll = config.BALANCE_USD
    last_kraken_trade_time = 0

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                bsc_signals = []

                for chain_id, chain_label in CHAINS.items():
                    signals = await fetch_signals(session, chain_id)

                    for sig in signals:
                        sig_id = sig.get("signalId")
                        if sig_id not in _alerted_ids:
                            if passes_filter(sig):
                                msg = format_alert(sig, chain_label)
                                reporter.queue_event("Smart Money", msg)
                                logger.info(f"Smart Money Alert: {sig.get('ticker')} on {chain_label}")

                                # --- Solana: Direct Jupiter trade ---
                                if chain_id == "501" and passes_trade_filter(sig) and sig.get("direction") == "buy":
                                    await execute_jupiter_trade(sig, risk, bankroll)

                            _alerted_ids.add(sig_id)

                    if chain_id == "56":
                        bsc_signals.extend(signals)

                # --- BSC: Kraken sentiment trade ---
                now = time.time()
                if now - last_kraken_trade_time >= config.SM_TRADE_COOLDOWN_SEC:
                    sentiment = calculate_sentiment(bsc_signals)
                    if sentiment["direction"]:
                        logger.info(
                            f"BSC Sentiment: {sentiment['direction']} "
                            f"score={sentiment['score']:.2f} "
                            f"buys={sentiment['buy_count']} sells={sentiment['sell_count']}"
                        )
                        traded = await execute_kraken_sentiment_trade(sentiment, risk, bankroll)
                        if traded:
                            last_kraken_trade_time = now

                # --- Check Jupiter exits ---
                await check_jupiter_exits()

                logger.debug(f"🧠 SM Scan: BSC={len(bsc_signals)} Signale | alerted={len(_alerted_ids)}")

                # Prune old IDs
                if len(_alerted_ids) > 500:
                    sorted_ids = sorted(_alerted_ids)
                    _alerted_ids.difference_update(sorted_ids[: len(sorted_ids) - 500])

            except Exception as e:
                logger.exception(f"Smart Money Loop error: {e}")

            await asyncio.sleep(config.SM_INTERVAL)
