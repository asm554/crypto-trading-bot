"""Low-turnover core DCA research prototype with realistic spot costs.

This is a deliberately different strategy class from the legacy ranked-dip
``DCABot``.  It exists under ``backtest/`` until it passes development and a
single final holdout; it is not wired into any paper or live process.

Fixed candidates:

``btc``
    Invest 50 EUR in BTC once per UTC week while BTC is in the confirmed bull
    regime.
``balanced``
    Invest 25 EUR each in BTC and ETH under the same condition.

Bull means completed BTC daily close > EMA200 and EMA50 > EMA200.  Bear means
close < EMA200 and EMA50 < EMA200.  Neutral does nothing.  A bear transition
liquidates every open lot.  Fills use the configured spread and 0.4% taker fee
per side.  At least 50 EUR remains cash.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest.backtest_surfer import DEFAULT_SPREAD_PCT, load_candles, parse_date
from backtest.dca_regime import classify_btc_regime

DAY = 86400
FEE_RATE = 0.004
BUDGET = 500.0
CASH_RESERVE = 50.0
WEEKLY_TOTAL_EUR = 50.0
CANDIDATES = {
    "btc": {"weights": {"BTCEUR": 1.0}, "circuit_breaker_pct": None, "halt": False},
    "balanced": {
        "weights": {"BTCEUR": 0.5, "ETHEUR": 0.5}, "circuit_breaker_pct": None, "halt": False,
    },
    "btc_protected": {
        "weights": {"BTCEUR": 1.0}, "circuit_breaker_pct": 10.0, "halt": False,
    },
    "balanced_protected": {
        "weights": {"BTCEUR": 0.5, "ETHEUR": 0.5}, "circuit_breaker_pct": 10.0, "halt": False,
    },
    "btc_hardlimit": {
        "weights": {"BTCEUR": 1.0}, "circuit_breaker_pct": 10.0, "halt": True,
    },
    "balanced_hardlimit": {
        "weights": {"BTCEUR": 0.5, "ETHEUR": 0.5}, "circuit_breaker_pct": 10.0, "halt": True,
    },
}


def _date(ts: float):
    return datetime.fromtimestamp(ts, timezone.utc).date()


def _daily_rows(hourly: list[tuple]) -> list[tuple]:
    grouped: dict[object, list[tuple]] = defaultdict(list)
    for row in hourly:
        grouped[_date(row[0])].append(row)
    out = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row[0])
        if len(rows) < 23:
            continue
        out.append((
            rows[0][0], rows[0][1], max(row[2] for row in rows),
            min(row[3] for row in rows), rows[-1][4],
            sum(row[5] for row in rows), sum(row[6] for row in rows),
        ))
    return sorted(out, key=lambda row: row[0])


def _max_drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else BUDGET
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak * 100)
    return round(worst, 2)


def run(candidate: str, start: str, end: str, spread_pct: float = DEFAULT_SPREAD_PCT) -> dict:
    definition = CANDIDATES[candidate]
    weights = definition["weights"]
    circuit_breaker_pct = definition["circuit_breaker_pct"]
    halt_after_breaker = definition["halt"]
    start_ts, end_ts = parse_date(start), parse_date(end)
    warmup_ts = start_ts - 260 * DAY
    hourly = {
        pair: [row for row in load_candles(pair, 60) if warmup_ts <= row[0] < end_ts]
        for pair in weights
    }
    btc_hourly = [row for row in load_candles("BTCEUR", 60) if warmup_ts <= row[0] < end_ts]
    daily_btc = _daily_rows(btc_hourly)
    first_hour = {
        pair: {_date(row[0]): row for row in rows if datetime.fromtimestamp(row[0], timezone.utc).hour == 0}
        for pair, rows in hourly.items()
    }

    cash = BUDGET
    lots: list[dict] = []
    closed: list[dict] = []
    curve: list[float] = []
    regime_counts = {"bull": 0, "neutral": 0, "bear": 0}
    spread_side = spread_pct / 200.0
    peak_equity = BUDGET
    cooldown_until = None
    circuit_breakers = 0
    halted = False

    def prices_for(day):
        out = {}
        for pair in weights:
            row = first_hour[pair].get(day)
            if row:
                out[pair] = float(row[4])
        return out

    def equity(prices: dict[str, float]) -> float:
        value = cash
        for lot in lots:
            last = prices.get(lot["pair"], lot["last_price"])
            bid = last * (1 - spread_side)
            gross = lot["size"] * bid
            value += gross - lot["buy_fee"] - gross * FEE_RATE
        return value

    def liquidate(reason: str, prices: dict[str, float], event_ts: float) -> None:
        nonlocal cash, lots
        for lot in lots:
            bid = prices[lot["pair"]] * (1 - spread_side)
            proceeds = lot["size"] * bid
            sell_fee = proceeds * FEE_RATE
            pnl = proceeds - lot["cost"] - lot["buy_fee"] - sell_fee
            cash += lot["cost"] + pnl
            closed.append({
                "pair": lot["pair"], "entry_ts": lot["entry_ts"], "exit_ts": event_ts,
                "entry_price": lot["entry_price"], "exit_price": bid,
                "size_eur": lot["cost"], "pnl_eur": pnl,
                "hold_h": (event_ts - lot["entry_ts"]) / 3600, "reason": reason,
            })
        lots = []

    day = datetime.fromtimestamp(start_ts, timezone.utc).date()
    final_day = datetime.fromtimestamp(end_ts, timezone.utc).date()
    while day < final_day:
        prices = prices_for(day)
        if len(prices) != len(weights):
            day += timedelta(days=1)
            continue
        for lot in lots:
            lot["last_price"] = prices[lot["pair"]]

        completed = [row for row in daily_btc if _date(row[0]) < day]
        decision = classify_btc_regime(completed[-400:])
        regime_counts[decision.regime] += 1
        event_ts = datetime(day.year, day.month, day.day, 1, tzinfo=timezone.utc).timestamp()

        pre_action_equity = equity(prices)
        if (
            circuit_breaker_pct is not None and lots
            and pre_action_equity <= peak_equity * (1 - circuit_breaker_pct / 100)
        ):
            liquidate("circuit_breaker", prices, event_ts)
            circuit_breakers += 1
            halted = halt_after_breaker
            cooldown_until = None if halted else day + timedelta(days=30)
            peak_equity = cash
        elif decision.regime == "bear" and lots:
            liquidate("bear_exit", prices, event_ts)
            # A later bull re-entry starts a new allocation cycle; otherwise a
            # historical peak from the prior cycle could instantly trip the
            # circuit breaker without any new-cycle loss.
            peak_equity = cash

        cooldown_active = halted or (cooldown_until is not None and day < cooldown_until)
        if decision.regime == "bull" and day.weekday() == 0 and not cooldown_active:
            available = max(0.0, cash - CASH_RESERVE)
            round_budget = min(WEEKLY_TOTAL_EUR, available)
            for pair, weight in weights.items():
                cost = min(round_budget * weight, max(0.0, cash - CASH_RESERVE))
                if cost < 1.0:
                    continue
                ask = prices[pair] * (1 + spread_side)
                size = cost / ask
                lots.append({
                    "pair": pair, "entry_ts": event_ts, "entry_price": ask,
                    "size": size, "cost": cost, "buy_fee": cost * FEE_RATE,
                    "last_price": prices[pair],
                })
                cash -= cost

        day_equity = equity(prices)
        peak_equity = max(peak_equity, day_equity)
        curve.append(day_equity)
        day += timedelta(days=1)

    last_prices = {
        pair: float(rows[-1][4]) for pair, rows in hourly.items() if rows
    }
    final_equity = equity(last_prices)
    end_event_ts = float(end_ts)
    trade_log = list(closed)
    for lot in lots:
        bid = last_prices[lot["pair"]] * (1 - spread_side)
        proceeds = lot["size"] * bid
        pnl = proceeds - lot["cost"] - lot["buy_fee"] - proceeds * FEE_RATE
        trade_log.append({
            "pair": lot["pair"], "entry_ts": lot["entry_ts"], "exit_ts": end_event_ts,
            "entry_price": lot["entry_price"], "exit_price": bid,
            "size_eur": lot["cost"], "pnl_eur": pnl,
            "hold_h": (end_event_ts - lot["entry_ts"]) / 3600, "reason": "end_of_test_mtm",
        })

    wins = [trade for trade in trade_log if trade["pnl_eur"] > 0]
    losses = [trade for trade in trade_log if trade["pnl_eur"] <= 0]
    gross_win = sum(trade["pnl_eur"] for trade in wins)
    gross_loss = abs(sum(trade["pnl_eur"] for trade in losses))
    return {
        "bot": f"DCA Core ({candidate})",
        "start": start,
        "end": end,
        "days": (end_ts - start_ts) / DAY,
        "pairs": list(weights),
        "initial_capital_eur": BUDGET,
        "final_equity_eur": round(final_equity, 2),
        "total_return_pct": round((final_equity / BUDGET - 1) * 100, 2),
        "max_drawdown_pct": _max_drawdown_pct(curve),
        "trades": len(trade_log),
        "winrate_pct": round(len(wins) / len(trade_log) * 100, 1) if trade_log else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (math.inf if gross_win else 0.0),
        "trade_log": trade_log,
        "params": {
            "candidate": candidate, "weights": weights, "weekly_total_eur": WEEKLY_TOTAL_EUR,
            "cash_reserve_eur": CASH_RESERVE, "regime": "BTC daily close/EMA50/EMA200",
            "neutral_action": "hold/no buy", "bear_action": "sell all",
            "circuit_breaker_pct": circuit_breaker_pct,
            "circuit_breaker_cooldown_days": (
                None if circuit_breaker_pct is None or halt_after_breaker else 30
            ),
            "halt_after_circuit_breaker": halt_after_breaker,
            "taker_fee_rate": FEE_RATE, "spread_pct": spread_pct,
        },
        "regime_days": regime_counts,
        "circuit_breakers": circuit_breakers,
        "halted": halted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, choices=tuple(CANDIDATES))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--spread-pct", type=float, default=DEFAULT_SPREAD_PCT)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    result = run(args.candidate, args.start, args.end, args.spread_pct)
    print(
        f"{result['bot']}: {result['total_return_pct']:+.2f}% | "
        f"MaxDD {result['max_drawdown_pct']:.2f}% | Trades {result['trades']}"
    )
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(f"JSON: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
