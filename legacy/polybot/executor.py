import asyncio
import time
import logging
from . import config
from .fees import buy_hold_ev_per_share, taker_fee_per_share
from .market import get_client
from .strategy import fractional_kelly_with_fee

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.trade_history = [] # List of outcome (profit/loss)
        self.ath = config.BALANCE_USD
        self.is_halted = False
        self.consecutive_losses = 0
        self.pause_until = 0

    def check_halt(self, current_balance: float) -> bool:
        """Checks for all kill-switch conditions."""
        if self.is_halted:
            return True

        # Update ATH
        if current_balance > self.ath:
            self.ath = current_balance

        # 1. Daily Halt (-20%)
        if self.daily_pnl < -(config.BALANCE_USD * config.DAILY_LOSS_HALT_PCT):
            self.is_halted = True
            logger.critical("🔥 KILL-SWITCH: Daily Loss Limit (-20%) reached. Terminal halt.")
            return True

        # 2. ATH Halt (-40%)
        if current_balance < (self.ath * (1 - config.ATH_DRAWDOWN_HALT_PCT)):
            self.is_halted = True
            logger.critical(f"💀 KILL-SWITCH: ATH Drawdown (-40%) reached. ATH: ${self.ath:.2f}. Halt.")
            return True

        # 3. Consecutive Loss Pause (5 losses = 30 min pause)
        if self.pause_until > time.time():
            return True
        
        return False

    def update_trade(self, pnl: float):
        """Updates internal state after a trade completes/resolves."""
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= config.CONSECUTIVE_LOSS_LIMIT:
                self.pause_until = time.time() + config.CONSECUTIVE_LOSS_PAUSE_SEC
                logger.warning(f"⏸️ PAUSE: {config.CONSECUTIVE_LOSS_LIMIT} losses in a row. Pausing for 30m.")
        else:
            self.consecutive_losses = 0

def calculate_kelly_size(price: float, edge: float, bankroll: float) -> float:
    """
    Positionsgröße für Taker-Snipes: Binance-Signal erhöht die angenommene Win-Wahrscheinlichkeit;
    Polymarket-Taker-Gebühr (Crypto feeRate) wird pro Share abgezogen wie in der offiziellen Formel.
    Im Paper-Mode: Fee = 0.0 (simuliert nur Signal-Validierung, nicht Fee-Optimierung).
    """
    if edge <= 0:
        return 0.0

    fee_rate = 0.0 if config.PAPER_MODE else (config.CRYPTO_TAKER_FEE_RATE if config.FEE_MODEL_ENABLED else 0.0)
    fee_ps = taker_fee_per_share(price, fee_rate)

    p = min(0.99, max(0.01, price + edge))
    ev_net = buy_hold_ev_per_share(p, price, fee_per_share=fee_ps)
    if ev_net <= 0:
        return 0.0

    kelly_f = fractional_kelly_with_fee(ev_net, price, p, fee_per_share=fee_ps)
    # Zusätzlich 0.5 wie zuvor (Half-Kelly auf dem bereits fraktionierten Kelly)
    safe_f = min(kelly_f * 0.5, config.MAX_PORTFOLIO_PCT)

    size_usd = min(bankroll * safe_f, config.MAX_BET_USD)

    # Paper-Mode: Mindesteinsatz von 0.01 EUR um Kelly-Berechnung nicht zu verfälschen
    if config.PAPER_MODE and size_usd > 0 and size_usd < 0.01:
        size_usd = 0.01

    return max(0.0, size_usd)


def calculate_kraken_kelly_size(edge: float, bankroll: float) -> float:
    """
    Kelly-Sizing für Kraken Spot Limit-Orders (Maker-Fee).

    Edge = 5-Min-Preisdelta (z.B. 0.012 = 1.2%).
    Fees: Maker Round-Trip = 2 × 0.16% = 0.32%.

    Profitabel nur wenn edge > round_trip_fee (0.32%).
    """
    round_trip_fee = 2 * config.CRYPTO_MAKER_FEE_RATE  # 0.0032 = 0.32%
    net_edge = edge - round_trip_fee

    if net_edge <= 0:
        return 0.0

    # Win-Wahrscheinlichkeit: konservativ 55% Basis, skaliert mit Edge-Stärke
    # Bei 1.2% Edge: p = min(0.65, 0.55 + 0.012 * 5) = 0.61
    # Bei 0.8% Edge: p = min(0.65, 0.55 + 0.008 * 5) = 0.59
    win_prob = min(0.65, 0.55 + edge * 5)

    # Kelly: f = p - q/b
    b = net_edge / round_trip_fee
    q = 1.0 - win_prob
    kelly = win_prob - (q / b)

    if kelly <= 0:
        return 0.0

    size = bankroll * kelly * config.KELLY_FRACTION
    return min(max(0.0, size), config.MAX_BET_USD)

async def execute_maker_trade(token_id: str, side: str, size: float, price: float) -> dict:
    """
    Führt den Trade als Market Maker aus (POST-ONLY).
    Wir stellen Liquidität bereit und meiden Taker-Fees.
    """
    start = time.monotonic()

    # Duplikat-Check via MCP Server
    try:
        from .mcp_client import has_open_position
        if await has_open_position(token_id, side):
            logger.info(f"MCP: Position bereits offen für {token_id[:12]} {side} – skip")
            return {"status": "skipped", "reason": "duplicate_position"}
    except Exception:
        pass  # MCP nicht erreichbar → weiter

    if config.PAPER_MODE:
        msg = f"[PAPER MAKER] {side} {size:.2f} Shares @ ${price:.3f} für Token {token_id}"
        logger.info(msg)
        return {"paper": True, "status": "resting_on_book"}

    client = get_client()
    try:
        # FMB (Post Only Limit Order) 
        order_payload = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": side,
            "order_type": "POST_ONLY"  # Essenziell für die Maker +1.12% Strategie
        }
        
        # Sende Async (Ggf Timeout etwas anpassen falls Clob laggy ist)
        order = await asyncio.wait_for(
            client.create_and_post_order(order_payload),
            timeout=config.TRADE_TIMEOUT
        )
        
        elapsed = time.monotonic() - start
        logger.info(f"Maker Order platziert in {elapsed:.3f}s: {order}")
        return order
        
    except asyncio.TimeoutError:
        logger.error(f"Execution Timeout (> {config.TRADE_TIMEOUT}s) auf Token {token_id}")
        return {"error": "timeout"}
    except Exception as e:
        logger.error(f"Trade Execution fehlgeschlagen: {e}")
        return {"error": str(e)}

async def execute_paired_arb(
    yes_token_id: str,
    no_token_id: str,
    yes_price: float,
    no_price: float,
    trade_size_usd: float,
) -> dict:
    """
    Platziert YES- und NO-Order als Paar für Delta-neutralen Arbitrage-Test.
    Wenn die NO-Order scheitert, wird die YES-Order automatisch storniert.

    Args:
        yes_token_id: Token-ID für YES/Up
        no_token_id:  Token-ID für NO/Down
        yes_price:    Aktueller YES-Preis (0–1)
        no_price:     Aktueller NO-Preis (0–1)
        trade_size_usd: USD-Betrag pro Seite

    Returns:
        Dict mit Status und Order-IDs
    """
    combined_cost = yes_price + no_price
    profit_margin = 1.0 - combined_cost

    if profit_margin <= 0:
        logger.warning(f"[PAIRED ARB] Kein Profit möglich: combined={combined_cost:.4f}")
        return {"status": "skipped", "reason": "no_profit_margin"}

    yes_shares = trade_size_usd / yes_price
    no_shares = trade_size_usd / no_price

    logger.info(
        f"[PAIRED ARB] YES {yes_shares:.2f}@{yes_price:.4f} + NO {no_shares:.2f}@{no_price:.4f} "
        f"| Combined: {combined_cost:.4f} | Margin: {profit_margin*100:.2f}%"
    )

    if config.PAPER_MODE:
        logger.info(f"[PAPER PAIRED ARB] Würde YES+NO platzieren, Margin={profit_margin*100:.2f}%")
        from .paper_db import log_arb_trade
        await log_arb_trade(
            yes_token_id, no_token_id, yes_price, no_price,
            trade_size_usd, "paper_yes", "paper_no", status="paper"
        )
        return {
            "status": "paper",
            "yes_order_id": "paper_yes",
            "no_order_id": "paper_no",
            "profit_margin": profit_margin,
        }

    client = get_client()

    # --- YES Order ---
    try:
        yes_order = await asyncio.wait_for(
            client.create_and_post_order({
                "token_id": yes_token_id,
                "price": yes_price,
                "size": yes_shares,
                "side": "BUY",
                "order_type": "GTC",
            }),
            timeout=config.TRADE_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"[PAIRED ARB] YES Order fehlgeschlagen: {e}")
        return {"status": "error", "reason": f"yes_order_failed: {e}"}

    yes_order_id = (yes_order or {}).get("orderID", "")
    if not yes_order_id:
        logger.error("[PAIRED ARB] YES Order ohne orderID zurückgegeben")
        return {"status": "error", "reason": "yes_order_no_id"}

    logger.info(f"[PAIRED ARB] YES Order platziert: {yes_order_id}")

    # --- NO Order ---
    try:
        no_order = await asyncio.wait_for(
            client.create_and_post_order({
                "token_id": no_token_id,
                "price": no_price,
                "size": no_shares,
                "side": "BUY",
                "order_type": "GTC",
            }),
            timeout=config.TRADE_TIMEOUT,
        )
    except Exception as e:
        no_order = None
        logger.error(f"[PAIRED ARB] NO Order fehlgeschlagen: {e}")

    no_order_id = (no_order or {}).get("orderID", "")

    if not no_order_id:
        # YES stornieren um ungehedgte Position zu vermeiden
        logger.error("[PAIRED ARB] NO Order fehlgeschlagen – storniere YES Order")
        try:
            cancelled = await asyncio.wait_for(
                client.cancel_order(yes_order_id),
                timeout=config.TRADE_TIMEOUT,
            )
            if cancelled:
                logger.info(f"[PAIRED ARB] YES Order erfolgreich storniert: {yes_order_id}")
            else:
                logger.critical(
                    f"[PAIRED ARB] MANUELLER EINGRIFF NÖTIG: "
                    f"YES Order {yes_order_id} konnte nicht storniert werden – ungehedgte Position offen!"
                )
        except Exception as e:
            logger.critical(
                f"[PAIRED ARB] MANUELLER EINGRIFF NÖTIG: "
                f"Stornierung von YES Order {yes_order_id} fehlgeschlagen: {e}"
            )
        return {"status": "error", "reason": "no_order_failed", "yes_order_id": yes_order_id}

    from .paper_db import log_arb_trade
    await log_arb_trade(
        yes_token_id, no_token_id, yes_price, no_price,
        trade_size_usd, yes_order_id, no_order_id, status="live"
    )
    logger.info(
        f"[PAIRED ARB] Beide Orders platziert | YES={yes_order_id} NO={no_order_id} "
        f"| Margin={profit_margin*100:.2f}%"
    )
    return {
        "status": "ok",
        "yes_order_id": yes_order_id,
        "no_order_id": no_order_id,
        "profit_margin": profit_margin,
        "combined_cost": combined_cost,
    }


async def execute_taker_trade(token_id: str, side: str, size: float, price: float) -> dict:
    """
    Führt den Trade aggressiv aus (FOK/IOC Limit GTC). Wichtig für HFT/Latency Arbitrage.
    Wir nehmen aktiv Liquidität aus dem Buch, solange das Oracle noch nicht geupdated hat.
    """
    start = time.monotonic()

    # Duplikat-Check via MCP Server
    try:
        from .mcp_client import has_open_position
        if await has_open_position(token_id, side):
            logger.info(f"MCP: Position bereits offen für {token_id[:12]} {side} – skip")
            return {"status": "skipped", "reason": "duplicate_position"}
    except Exception:
        pass  # MCP nicht erreichbar → weiter

    if config.PAPER_MODE:
        msg = f"[PAPER TAKER HFT] {side} {size:.2f} Shares @ ${price:.3f} für Token {token_id}"
        logger.info(msg)
        return {"paper": True, "status": "taken"}

    client = get_client()
    try:
        # Taker Order (GTC oder IOC/FOK) - IOC ist hier für schnelle Kills oft am besten
        order_payload = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": side,
            "order_type": "FOK"  # Fill Or Kill (um Partial Bugs in der Latenz zu meiden)
        }
        
        order = await asyncio.wait_for(
            client.create_and_post_order(order_payload),
            timeout=config.TRADE_TIMEOUT
        )
        
        elapsed = time.monotonic() - start
        logger.info(f"💥 Taker Order platziert in {elapsed:.3f}s: {order}")
        return order
        
    except asyncio.TimeoutError:
        logger.error(f"Taker Execution Timeout (> {config.TRADE_TIMEOUT}s)")
        return {"error": "timeout"}
    except Exception as e:
        logger.error(f"Taker Trade Execution fehlgeschlagen: {e}")
        return {"error": str(e)}


# ==========================================
# KRAKEN SPOT EXECUTION
# ==========================================

KRAKEN_PAIR_MAP = {
    "doteur": "DOTEUR",
    "suieur": "SUIEUR",
    "avaxeur": "AVAXEUR",
    "etheur": "XETHZEUR",
    "xbteur": "XXBTZEUR",
}


FILL_TIMEOUT_SEC = 60  # Max Wartezeit auf Limit-Order Fill
FILL_POLL_SEC = 4      # Poll-Intervall für Order-Status


async def execute_kraken_trade(pair: str, direction: str, size_usd: float) -> dict:
    """
    Platziert eine Post-Only Limit-Order auf Kraken Spot (Maker-Fee 0.16%).

    Ablauf:
    1. Bid/Ask aus WS-Feed holen
    2. Limit-Order auf Bid (BUY) oder Ask (SELL) platzieren → Maker
    3. Bis zu 60s auf Fill warten
    4. Kein Fill → Cancel + zurückmelden

    Args:
        pair: Normalisierter Pair-Name (z.B. "doteur")
        direction: "UP" → buy, "DOWN" → sell
        size_usd: Betrag in EUR (z.B. 20.0)
    """
    from .binance_ws import get_best_bid, get_best_ask

    kraken_pair = KRAKEN_PAIR_MAP.get(pair)
    if not kraken_pair:
        logger.error(f"Kein Kraken-Pair-Mapping für {pair}")
        return {"error": "unknown_pair"}

    side = "buy" if direction == "UP" else "sell"

    # Maker: BUY am Bid, SELL am Ask → sitzt auf dem Buch
    if side == "buy":
        limit_price = get_best_bid(pair)
    else:
        limit_price = get_best_ask(pair)

    if limit_price <= 0:
        logger.warning(f"Kein Bid/Ask für {pair}, kein Trade")
        return {"error": "no_bid_ask"}

    if config.PAPER_MODE:
        logger.info(
            f"[PAPER KRAKEN LIMIT] {side.upper()} €{size_usd:.2f} {kraken_pair} "
            f"@ {limit_price} (Maker-Fee: {config.CRYPTO_MAKER_FEE_RATE:.2%})"
        )
        return {"paper": True, "status": "filled", "price": limit_price}

    from .kraken_client import KrakenClient
    client = KrakenClient(config.KRAKEN_API_KEY, config.KRAKEN_API_SECRET)

    # 1. Post-Only Limit Order platzieren
    result = await client.add_limit_order(kraken_pair, side, size_usd, limit_price)

    errors = result.get("error", [])
    if errors:
        # Post-Only rejected (würde als Taker matchen) → Fallback Market Order
        if any("Post only" in str(e) or "EOrder:Post only" in str(e) for e in errors):
            logger.warning(f"Post-Only rejected für {kraken_pair} — Fallback auf Market Order")
            result = await client.add_market_order(kraken_pair, side, size_usd)
            if result.get("error"):
                logger.error(f"Kraken Market Fallback Fehler: {result['error']}")
                return {"error": result["error"]}
            logger.info(f"✅ Kraken Market Order (Fallback): {result}")
            return {"status": "filled_taker", "result": result}

        logger.error(f"Kraken Limit Order Fehler: {errors}")
        return {"error": errors}

    # 2. txid aus Response extrahieren
    txids = result.get("result", {}).get("txid", [])
    if not txids:
        logger.error(f"Keine txid in Kraken Response: {result}")
        return {"error": "no_txid"}

    txid = txids[0]
    logger.info(f"📋 Limit Order platziert: {txid} — {side.upper()} €{size_usd:.2f} {kraken_pair} @ {limit_price}")

    # 3. Auf Fill warten (max 60s)
    for i in range(FILL_TIMEOUT_SEC // FILL_POLL_SEC):
        await asyncio.sleep(FILL_POLL_SEC)
        status = await client.query_orders(txid)
        order_info = status.get("result", {}).get(txid, {})
        order_status = order_info.get("status", "")

        if order_status == "closed":
            logger.info(f"✅ Limit Order gefüllt: {txid}")
            return {"status": "filled_maker", "txid": txid, "price": limit_price}

        if order_status == "canceled":
            logger.warning(f"Order wurde extern storniert: {txid}")
            return {"status": "cancelled_external", "txid": txid}

    # 4. Timeout → Cancel
    logger.warning(f"⏰ Limit Order nicht gefüllt nach {FILL_TIMEOUT_SEC}s — Cancel: {txid}")
    await client.cancel_order(txid)
    return {"status": "cancelled_timeout", "txid": txid}
