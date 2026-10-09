"""Single source of truth (DESK_RULES). Schwellen sind eingefroren und werden beim ersten Lauf gehasht."""
import hashlib
import json
from dataclasses import asdict, dataclass

DAY = 86400


@dataclass(frozen=True)
class Rules:
    universe: tuple = (
        "XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "DOGEUSD", "LINKUSD", "DOTUSD",
        "AVAXUSD", "LTCUSD", "ATOMUSD", "UNIUSD", "NEARUSD", "TRXUSD", "BCHUSD", "XLMUSD",
    )
    account_usd: float = 1000.0      # Paper-Konto
    risk_pct: float = 1.0            # Erstes Entry
    add_risk_pct: float = 0.5        # Adds
    max_adds: int = 2
    be_trigger_r: float = 1.0        # "Breakeven" = Vorgaenger im Gewinn >= 1R
    stop_buffer_pct: float = 0.5     # Stop ueber Zonen-Oberkante
    max_leverage: float = 3.0        # Notional-Deckel
    fee_pct: float = 0.06            # Taker pro Seite
    slippage_pct: float = 0.02       # pro Seite
    flip_max_age_days: int = 7       # C3 darf max. so alt sein
    bounce_bars_4h: int = 6          # PRIME: 4H-Close > Close vor n Kerzen
    stale_hours: int = 72
    stale_band_r: float = 0.25       # "flat" = |Delta| < 0.25R
    pending_expiry_hours: int = 4


@dataclass(frozen=True)
class Thresholds:
    min_trades: int = 40
    min_win_rate: float = 0.33
    min_expectancy_r: float = 0.4
    max_drawdown: float = 0.20


RULES = Rules()
THRESHOLDS = Thresholds()


def thresholds_hash(t: Thresholds = THRESHOLDS) -> str:
    return hashlib.sha256(json.dumps(asdict(t), sort_keys=True).encode()).hexdigest()[:16]
