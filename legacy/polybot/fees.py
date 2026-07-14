"""
Polymarket Handelsgebühren (Stand Doku: https://docs.polymarket.com/trading/fees).

Taker: fee = C × feeRate × p × (1 − p)  (USDC, symmetrisch um p=0.5)
Maker: laut Doku keine Gebühr (feeRate für Maker-Zeile = 0).

feeRate ist marktabhängig (Weather 0.05, Crypto 0.072, …) und sollte langfristig
pro Token per API kommen; hier konfigurierbar für Strategietests.
"""
from __future__ import annotations


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    """USDC Gebühr pro Share beim Taker-Trade (Kauf/Verkauf-Formel laut Doku)."""
    if fee_rate <= 0 or not (0 < price < 1):
        return 0.0
    return fee_rate * price * (1.0 - price)


def taker_fee_usdc(num_shares: float, price: float, fee_rate: float) -> float:
    """Gesamt-USDC Taker-Gebühr für C Shares zum Preis p."""
    if num_shares <= 0:
        return 0.0
    return num_shares * taker_fee_per_share(price, fee_rate)


def weather_maker_entry_fee_per_share(price: float) -> float:
    """
    Wetter-Bot nutzt POST_ONLY (Maker): Polymarket verlangt dafür 0 Taker-Fee.
    stress_test_maker_as_taker_fees=true wendet die Weather-Taker-Rate wie beim
    aggressiven Kauf an (konservativer Strategietest).
    """
    from . import config

    if not config.FEE_MODEL_ENABLED:
        return 0.0
    if config.STRESS_TEST_MAKER_AS_TAKER:
        return taker_fee_per_share(price, config.WEATHER_TAKER_FEE_RATE)
    return 0.0


def buy_hold_ev_per_share(
    p_win: float,
    entry_price: float,
    *,
    fee_per_share: float,
) -> float:
    """
    Erwartungswert pro eingesetztem Share bei Kauf und Halten bis Settlement,
    nach Abzug der einmaligen Einstiegsgebühr fee_per_share (Taker).
    Maker: fee_per_share = 0.
    """
    profit_win = 1.0 - entry_price - fee_per_share
    loss_lose = entry_price + fee_per_share
    return p_win * profit_win - (1.0 - p_win) * loss_lose
