"""Alle Fragen des Desks (REST-Format von api.typesafe.ai). Ein Satz = ein Aufruf, State wird nur einmal berechnet."""

CONCENTRATION = {
    "type": "noul",
    "instructions": "Holding this token means being exit liquidity, based on `top_10_percent`, "
                    "`top_wallet_percent` and `holder_count`.",
}
DEV_LOADED = {
    "type": "noul",
    "instructions": "`developer_holding_percentage` is large enough that the creator selling would "
                    "meaningfully move the price.",
}

MARKET = {
    "shape": {
        "type": "choice",
        "instructions": "Classify the shape of this launch from the fields in `state`.",
        "criteria": {
            "crowd": "Buys outnumber sells across both recent windows. Volume is spread rather than spiking once.",
            "one_buyer": "Price climbing much faster than the number of trades or holders. One wallet walking the price up.",
            "fading": "Recent volume is a small fraction of the daily average, or sells outnumber buys in both windows.",
            "too_early": "Too few data points to tell any of the above apart yet.",
        },
    },
    "liquidity_fits_ticket": {
        "type": "noul",
        "instructions": "A position of `intended_ticket_usd` could be exited into `liquidity_usd` without "
                        "moving the price more than a few percent.",
    },
    "momentum_already_spent": {
        "type": "noul",
        "instructions": "The move in `change` has already happened, so entering now means buying after "
                        "the information is public. The largest change sitting in the older windows means true.",
    },
}

CHAIN_SOLANA = {
    "authority_risk": {
        "type": "choice",
        "instructions": "Judge contract control risk from `mint_authority` and `freeze_authority`.",
        "criteria": {
            "renounced": "Both null. Supply cannot be inflated, balances cannot be frozen.",
            "mint_open": "mint_authority is set.",
            "freeze_open": "freeze_authority is set.",
            "both_open": "Both are set.",
        },
    },
    "concentration_is_exit_risk": CONCENTRATION,
    "dev_still_loaded": DEV_LOADED,
}

CHAIN_BSC = {
    "sell_side_risk": {
        "type": "choice",
        "instructions": "Judge whether a position here can be sold, from `is_honeypot`, `gt_score_details` "
                        "and the buy and sell counts.",
        "criteria": {
            "clean": "Not flagged, and sells are going through in the data.",
            "flagged": "Explicitly flagged as a honeypot.",
            "suspicious": "Not flagged, but sells are absent or very rare while buys are plentiful.",
            "unknown": "Honeypot field missing and trade counts too thin to stand in for it.",
        },
    },
    "concentration_is_exit_risk": CONCENTRATION,
}

CHAIN_ROBINHOOD = {
    "data_coverage": {
        "type": "choice",
        "instructions": "Judge how much of this token is visible, from which fields in `state` carry values.",
        "criteria": {
            "indexed": "Holder count and distribution present, honeypot field is a real answer.",
            "partial": "Holder count present, but distribution or the honeypot field is missing.",
            "dark": "Neither distribution nor honeypot available. Only price, volume and pool age are known.",
        },
    },
    "sellable_by_evidence": {
        "type": "noul",
        "instructions": "Sells are going through on this token, judged from the buy and sell counts "
                        "rather than from any honeypot flag.",
    },
    "concentration_is_exit_risk": CONCENTRATION,
    "dev_still_loaded": DEV_LOADED,
}


def PICK(state: dict) -> dict:
    return {
        "best": {
            "type": "choice",
            "instructions": "Choose the single token in `candidates` that is the best entry right now. Weigh "
                            "crowd shape, contract risk and concentration together. Prefer a clean unspent "
                            "setup over a larger move that already happened.",
            "criteria": {c["key"]: c["summary"] for c in state["candidates"]},
        },
        "worth_trading_at_all": {
            "type": "noul",
            "instructions": "At least one token in `candidates` is worth a position today, rather than all "
                            "of them being mediocre.",
        },
    }


SETS = {"market": MARKET, "solana": CHAIN_SOLANA, "bsc": CHAIN_BSC, "robinhood": CHAIN_ROBINHOOD, "pick": PICK}


def build(set_name: str, state: dict) -> dict:
    qs = SETS[set_name]
    return qs(state) if callable(qs) else qs
