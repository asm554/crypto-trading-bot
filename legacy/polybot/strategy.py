import logging
from . import config
from .fees import buy_hold_ev_per_share, weather_maker_entry_fee_per_share

logger = logging.getLogger(__name__)

def calculate_expected_value(probability_win: float, odds: float) -> float:
    """
    Berechnet den Expected Value (EV).
    P(Win) * Payout(Net) - P(Loss)
    odds sind die implizierten Dezimal-Quoten (zB. Preis $0.05 entspricht Quote 20.0, aber hier 
    vereinfachen wir im Prediction Market Kontext: 
    Wenn man NO kauft, zahlt man price (zB. 0.95), Payout bei Win = 1.0. Profit = 0.05.
    EV = P(Win) * Profit - P(Loss) * Cost
    """
    # Da wir Market Maker sind und bei extremen Rändern operieren (Favorite-Longshot Bias),
    # kaufen wir ein "sicheres" Outcome.
    # payout_net = (1.0 - price)
    # loss = price
    # Für diese Funktion verwenden wir die generelle Wahrscheinlichkeitsformel
    cost = 1.0 / odds # cost in % (equivalent) 
    profit = 1.0 - cost
    
    # EV = P(Win)*Profit - P(Loss)*Cost
    p_loss = 1.0 - probability_win
    ev = (probability_win * profit) - (p_loss * cost)
    return ev  # EV_percent_of_investment

def calculate_maker_ev(probability_win: float, maker_price: float) -> float:
    """
    EV pro Share für Wetter/Maker-Pfad: nach Polymarket-Doku zahlen Maker keine Fee;
    mit stress_test_maker_as_taker_fees=true wird konservativ die Weather-Taker-Rate
    auf den Einstieg angewendet (nur für Paper/Stress).
    """
    fee = weather_maker_entry_fee_per_share(maker_price)
    return buy_hold_ev_per_share(probability_win, maker_price, fee_per_share=fee)

def fractional_kelly(ev: float, maker_price: float, probability_win: float) -> float:
    return fractional_kelly_with_fee(ev, maker_price, probability_win, fee_per_share=0.0)


def fractional_kelly_with_fee(
    ev: float,
    entry_price: float,
    probability_win: float,
    *,
    fee_per_share: float,
) -> float:
    """
    Kelly mit Netto-Gewinn / Netto-Verlust inkl. einmaliger Einstiegsgebühr pro Share.
    """
    profit_win = 1.0 - entry_price - fee_per_share
    loss_if_fail = entry_price + fee_per_share
    if profit_win <= 0 or loss_if_fail <= 0:
        return 0.0
    b = profit_win / loss_if_fail
    if b <= 0:
        return 0.0

    q = 1.0 - probability_win
    full_kelly = probability_win - (q / b)

    if full_kelly <= 0:
        return 0.0

    return full_kelly * config.KELLY_FRACTION

def get_maker_position_size(ev: float, maker_price: float, probability: float, bankroll: float) -> float:
    if ev < config.MIN_EV_PERCENT:
        return 0.0

    fee = weather_maker_entry_fee_per_share(maker_price)
    kelly_pct = fractional_kelly_with_fee(ev, maker_price, probability, fee_per_share=fee)
    target_size_usd = bankroll * kelly_pct
    
    # Kappen bei config limits
    target_size_usd = min(target_size_usd, config.MAX_BET_USD)
    
    # In Shares umrechnen
    return round(target_size_usd / maker_price, 2)
