"""Alle Zahlen an einer Stelle (Schwellen aus dem Artikel, nicht auf eigene Daten getunt)."""
from dataclasses import dataclass

SOLANA, BSC, ROBINHOOD, BASE = "solana", "bsc", "robinhood", "base"
NETS = (SOLANA, BSC, ROBINHOOD)
CHAIN_SET = {SOLANA: "solana", BSC: "bsc", BASE: "bsc", ROBINHOOD: "robinhood"}

HARD = {
    "min_age_minutes": 15, "max_age_hours": 72,
    "min_liquidity_usd": 12_000, "min_volume_h24": 40_000,
    "min_mcap_usd": 60_000, "max_mcap_usd": 8_000_000,
    "min_trades_h24": 150,
    "max_top_wallet": 0.05,   # nur Solana, exakt
    "max_top_10": 0.60,
    "min_holders": 80,
}
SOFT = {                       # (richtung, grenze)
    "concentration_is_exit_risk": ("max", 0.55),
    "momentum_already_spent": ("max", 0.60),
    "liquidity_fits_ticket": ("min", 0.60),
    "dev_still_loaded": ("max", 0.55),
    "sellable_by_evidence": ("min", 0.60),
}
SHAPE_MIN_CROWD = 0.55
PICK_MIN_WORTH = 0.60
PICK_MIN_CONF = 0.55
DARK_TICKET_CUT = 0.40
NO_SOCIAL_CUT = 0.60           # v1 liest kein X -> immer aktiv

# Gebuehren-/Slippage-Annahmen fuer Paper-Fills (pro Seite)
FEE_PCT = 1.0
SLIPPAGE_PCT = 1.5

# Budget je Zyklus (GeckoTerminal Free: 10 Calls/min)
GT_UNIVERSE_PAGES = 2
GT_DOSSIER = 3
CYCLE_SECONDS = 900
MONITOR_SECONDS = 300

# Bench-Dauer nach Ablehnungsgrund (Minuten)
BENCH_MINUTES = {
    "honeypot": 100_000, "authority_open": 100_000, "top_wallet": 100_000, "sell_side": 100_000,
    "top_10": 90, "holders": 90, "dev_still_loaded": 90, "concentration_is_exit_risk": 90,
    "shape": 25, "shape_weak": 25, "momentum_already_spent": 25, "liquidity_fits_ticket": 25,
    "liquidity": 25, "volume": 25, "trades": 25, "mcap": 25, "age": 20,
}
DEFAULT_BENCH = 45


@dataclass(frozen=True)
class PaperRules:
    bank_usd: float = 1000.0
    ticket_pct_of_bank: float = 6.0
    ticket_pct_of_liquidity: float = 2.0
    min_ticket_usd: float = 15.0
    exit_volume_ratio: float = 0.20      # h6-Volumen / (h24/4) darunter -> schliessen (Artikel-Regel)
    max_hold_hours: float = 24.0         # Paper-Zusatz: Meme-Tokens duerfen nicht ewig liegen
    hard_stop_pct: float = 35.0          # Paper-Zusatz: Verlustdeckel


PAPER = PaperRules()
