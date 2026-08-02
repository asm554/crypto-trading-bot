"""Chainstack-inspired, paper-only Pump.fun momentum strategy.

This reuses only the public event/signal concepts (fast new-token listener,
market-cap/bonding-curve momentum, pressure and TP/SL). It deliberately has no
wallet, private key, transaction builder, signer, RPC send, or live fallback.
"""
from __future__ import annotations

from polybot.pumpfun_strategy import PumpFunPaperBot


class PumpFunV2PaperBot(PumpFunPaperBot):
    """More active independent competitor, ledger prefix PUMP2_."""

    def __init__(self, **kwargs):
        defaults = {
            "initial_capital_eur": 100.0,
            "position_eur": 10.0,
            "max_open_positions": 3,
            "min_age_sec": 15,
            "max_age_sec": 3600,
            "min_market_cap_sol": 8.0,
            "max_market_cap_sol": 800.0,
            "min_change_pct": 3.0,
            "max_change_pct": 60.0,
            "migrated_min_change_pct": 3.0,
            "migrated_max_change_pct": 70.0,
            "min_trades": 5,
            "migrated_min_trades": 3,
            "min_unique_traders": 3,
            "min_buy_sell_ratio": 1.05,
            "min_recent_change_pct": 1.0,
            # V2 stays the unchanged momentum control group. The pullback gates
            # below belong only to the redesigned Original strategy.
            "max_recent_change_pct": 1000.0,
            "min_peak_change_pct": 0.0,
            "min_pullback_pct": 0.0,
            "max_pullback_pct": 100.0,
            "min_recent_trades": 1,
            "min_recent_buy_sell_ratio": 0.0,
            "allow_migrated_entries": True,
            "stop_loss_pct": 18.0,
            "take_profit_pct": 25.0,
            "trailing_stop_pct": 12.0,
            "trail_floor_pct": 10.0,
            "max_hold_sec": 1800,
            "migrated_max_hold_sec": 4 * 3600,
            "platform_fee_pct": 1.25,
            "migrated_slippage_pct": 3.0,
            "paper_mode": True,
            "prefix": "PUMP2_",
            "bot_key": "pumpfun_v2",
            "state_filename": "pumpfun_v2_state.json",
            "strategy_version": "v2-chainstack-inspired-paper",
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
