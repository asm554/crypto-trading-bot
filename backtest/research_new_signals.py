"""Vorregistrierte Research-Backtests für zwei neue, nicht produktive Signalklassen.

Die Modelle sind absichtlich mechanisch und parameterfest:

``rotation``
    Wöchentliche Relative-Stärke-Rotation gegen BTC über 168 abgeschlossene
    Stunden. Gehalten werden die zwei stärksten aus ETH/SOL/ADA/XRP.

``pairs``
    Long-only Mean-Reversion im ETH/SOL-Verhältnis auf Basis eines rollierenden
    30-Tage-Z-Scores.

Nur 60-Minuten-Bitvavo-CSVs werden gelesen. Es gibt keine Importe aus
``polybot`` und keine Änderungen an Produktionsstrategien.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backtest.backtest_surfer import DATA_DIR, load_candles, max_drawdown_pct, parse_date


HOUR = 3600
ROTATION_UNIVERSE = ("SOLEUR", "ETHEUR", "ADAEUR", "XRPEUR")
ROTATION_REFERENCE = "BTCEUR"


@dataclass
class Position:
    pair: str
    entry_ts: float
    entry_price: float
    quantity: float
    notional: float
    cash_cost: float
    stop_price: float


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _bid(mid: float, spread_pct: float) -> float:
    return mid * (1.0 - spread_pct / 200.0)


def _ask(mid: float, spread_pct: float) -> float:
    return mid * (1.0 + spread_pct / 200.0)


def open_position(pair: str, ts: float, mid: float, notional: float,
                  fee: float, spread_pct: float, stop_pct: float) -> Position:
    price = _ask(mid, spread_pct)
    return Position(
        pair=pair,
        entry_ts=ts,
        entry_price=price,
        quantity=notional / price,
        notional=notional,
        cash_cost=notional * (1.0 + fee),
        stop_price=price * (1.0 - stop_pct / 100.0),
    )


def close_position(position: Position, ts: float, mid: float, fee: float,
                   spread_pct: float, reason: str) -> tuple[float, dict]:
    price = _bid(mid, spread_pct)
    proceeds = position.quantity * price * (1.0 - fee)
    pnl = proceeds - position.cash_cost
    return proceeds, {
        "pair": position.pair,
        "entry": _utc(position.entry_ts),
        "exit": _utc(ts),
        "entry_price": round(position.entry_price, 8),
        "exit_price": round(price, 8),
        "size_eur": round(position.notional, 6),
        "pnl_eur": round(pnl, 6),
        "hold_h": round((ts - position.entry_ts) / HOUR, 2),
        "reason": reason,
    }


def stop_mid(position: Position, candle: tuple) -> float | None:
    """Konservativer Stop-Fill aus einer abgeschlossenen Stundenkerze.

    Bei Gap unter den Stop wird zum Open gefüllt, sonst am Stop. Der Bid-Abzug
    erfolgt anschließend in ``close_position``.
    """
    _ts, open_, _high, low, _close, _vwap, _volume = candle
    if low > position.stop_price:
        return None
    return min(float(open_), position.stop_price)


def relative_strength(current_coin: float, past_coin: float,
                      current_btc: float, past_btc: float) -> float:
    return (current_coin / past_coin - 1.0) - (current_btc / past_btc - 1.0)


def zscore(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.pstdev(values)
    if sd <= 0:
        return None
    return (values[-1] - statistics.mean(values)) / sd


def aligned_rows(pairs: tuple[str, ...], start_ts: int, end_ts: int,
                 warmup_h: int) -> tuple[list[float], dict[str, dict[float, tuple]]]:
    by_pair: dict[str, dict[float, tuple]] = {}
    for pair in pairs:
        rows = load_candles(pair, 60)
        by_pair[pair] = {
            float(row[0]): row for row in rows
            if start_ts - warmup_h * HOUR <= row[0] < end_ts
        }
    common = set.intersection(*(set(rows) for rows in by_pair.values()))
    timeline = sorted(ts for ts in common if start_ts <= ts < end_ts)
    if not timeline:
        raise SystemExit("Keine gemeinsame Stundenhistorie im gewählten Zeitraum")
    return timeline, by_pair


def _summary(strategy: str, args, trades: list[dict], equity_curve: list[float],
             final_equity: float, first_ts: float, last_ts: float,
             extra: dict | None = None) -> dict:
    wins = [t for t in trades if t["pnl_eur"] > 0]
    losses = [t for t in trades if t["pnl_eur"] <= 0]
    gross_win = sum(t["pnl_eur"] for t in wins)
    gross_loss = abs(sum(t["pnl_eur"] for t in losses))
    reasons: dict[str, int] = {}
    for trade in trades:
        reasons[trade["reason"]] = reasons.get(trade["reason"], 0) + 1
    days = (last_ts - first_ts) / 86400.0
    result = {
        "bot": strategy,
        "start": args.start,
        "end": args.end or "Datenende",
        "days": round(days, 1),
        "budget_eur": args.budget,
        "final_equity_eur": round(final_equity, 2),
        "total_return_pct": round((final_equity / args.budget - 1.0) * 100.0, 2),
        "realized_pnl_eur": round(sum(t["pnl_eur"] for t in trades), 2),
        "trades": len(trades),
        "trades_per_month": round(len(trades) / max(days / 30.4, 0.01), 2),
        "winrate_pct": round(len(wins) / len(trades) * 100.0, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_pct": round(max_drawdown_pct(equity_curve), 2),
        "avg_hold_h": round(statistics.mean(t["hold_h"] for t in trades), 1) if trades else None,
        "median_hold_h": round(statistics.median(t["hold_h"] for t in trades), 1) if trades else None,
        "exit_reasons": reasons,
        "params": {
            "taker_fee_rate": args.fee,
            "spread_pct": args.spread_pct,
        },
        "trade_log": trades,
    }
    if extra:
        result.update(extra)
    return result


def run_rotation(args) -> dict:
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else 2**63 - 1
    pairs = (*ROTATION_UNIVERSE, ROTATION_REFERENCE)
    timeline, rows = aligned_rows(pairs, start_ts, end_ts, warmup_h=168)
    available = set.intersection(*(set(rows[p]) for p in pairs))

    cash = float(args.budget)
    positions: dict[str, Position] = {}
    trades: list[dict] = []
    equity_curve: list[float] = []
    next_rebalance = timeline[0]

    for ts in timeline:
        now = ts + HOUR  # die Kerze mit Open-Zeit ts ist jetzt abgeschlossen
        for pair, position in list(positions.items()):
            candle = rows[pair][ts]
            stopped = stop_mid(position, candle)
            max_hold = now - position.entry_ts >= 30 * 24 * HOUR
            if stopped is not None or max_hold:
                proceeds, trade = close_position(
                    position, now, stopped if stopped is not None else float(candle[4]),
                    args.fee, args.spread_pct, "hard_stop" if stopped is not None else "max_hold",
                )
                cash += proceeds
                trades.append(trade)
                del positions[pair]

        if ts >= next_rebalance:
            past_ts = ts - 168 * HOUR
            if past_ts in available:
                btc_now = float(rows[ROTATION_REFERENCE][ts][4])
                btc_past = float(rows[ROTATION_REFERENCE][past_ts][4])
                ranked = sorted(
                    ROTATION_UNIVERSE,
                    key=lambda pair: relative_strength(
                        float(rows[pair][ts][4]), float(rows[pair][past_ts][4]), btc_now, btc_past,
                    ),
                    reverse=True,
                )
                selected = set(ranked[:2])
                for pair, position in list(positions.items()):
                    if pair not in selected:
                        proceeds, trade = close_position(
                            position, now, float(rows[pair][ts][4]), args.fee, args.spread_pct, "rotation",
                        )
                        cash += proceeds
                        trades.append(trade)
                        del positions[pair]
                marked_equity = cash + sum(
                    p.quantity * _bid(float(rows[pair][ts][4]), args.spread_pct) * (1.0 - args.fee)
                    for pair, p in positions.items()
                )
                target_notional = marked_equity * 0.5
                for pair in ranked[:2]:
                    if pair in positions:
                        continue
                    notional = min(target_notional, cash / (1.0 + args.fee))
                    if notional <= 0:
                        continue
                    position = open_position(
                        pair, now, float(rows[pair][ts][4]), notional,
                        args.fee, args.spread_pct, stop_pct=15.0,
                    )
                    cash -= position.cash_cost
                    positions[pair] = position
            next_rebalance = ts + 168 * HOUR

        equity = cash + sum(
            p.quantity * _bid(float(rows[pair][ts][4]), args.spread_pct) * (1.0 - args.fee)
            for pair, p in positions.items()
        )
        equity_curve.append(equity)

    last_ts = timeline[-1]
    now = last_ts + HOUR
    for pair, position in list(positions.items()):
        proceeds, trade = close_position(
            position, now, float(rows[pair][last_ts][4]), args.fee, args.spread_pct, "end_of_test",
        )
        cash += proceeds
        trades.append(trade)
    equity_curve.append(cash)
    return _summary(
        "Relative Strength Rotation", args, trades, equity_curve, cash,
        timeline[0] + HOUR, now,
        {"pairs": list(ROTATION_UNIVERSE), "reference_pair": ROTATION_REFERENCE,
         "params": {"taker_fee_rate": args.fee, "spread_pct": args.spread_pct,
                    "lookback_h": 168, "rebalance_h": 168, "top_n": 2,
                    "hard_stop_pct": 15.0, "max_hold_h": 720}},
    )


def run_pairs(args) -> dict:
    start_ts = parse_date(args.start)
    end_ts = parse_date(args.end) if args.end else 2**63 - 1
    pairs = ("ETHEUR", "SOLEUR")
    timeline, rows = aligned_rows(pairs, start_ts, end_ts, warmup_h=720)
    common_all = sorted(set(rows["ETHEUR"]) & set(rows["SOLEUR"]))
    ratio_by_ts = {
        ts: float(rows["ETHEUR"][ts][4]) / float(rows["SOLEUR"][ts][4])
        for ts in common_all
    }
    ratio_history: list[float] = []
    history_ts: list[float] = []
    cash = float(args.budget)
    position: Position | None = None
    trades: list[dict] = []
    equity_curve: list[float] = []

    for ts in common_all:
        ratio_history.append(ratio_by_ts[ts])
        history_ts.append(ts)
        if ts < start_ts:
            continue
        if ts >= end_ts:
            break
        now = ts + HOUR
        window = ratio_history[-720:]
        z = zscore(window) if len(window) == 720 else None

        if position is not None:
            candle = rows[position.pair][ts]
            stopped = stop_mid(position, candle)
            reason = None
            exit_mid = float(candle[4])
            if stopped is not None:
                reason, exit_mid = "hard_stop", stopped
            elif now - position.entry_ts >= 14 * 24 * HOUR:
                reason = "max_hold"
            elif z is not None and abs(z) <= 0.5:
                reason = "mean_reversion"
            if reason:
                proceeds, trade = close_position(
                    position, now, exit_mid, args.fee, args.spread_pct, reason,
                )
                cash += proceeds
                trades.append(trade)
                position = None

        if position is None and z is not None and (z < -2.0 or z > 2.0):
            pair = "ETHEUR" if z < -2.0 else "SOLEUR"
            notional = cash / (1.0 + args.fee)
            position = open_position(
                pair, now, float(rows[pair][ts][4]), notional,
                args.fee, args.spread_pct, stop_pct=10.0,
            )
            cash -= position.cash_cost

        equity = cash
        if position is not None:
            equity += position.quantity * _bid(float(rows[position.pair][ts][4]), args.spread_pct) * (1.0 - args.fee)
        equity_curve.append(equity)

    last_ts = timeline[-1]
    now = last_ts + HOUR
    if position is not None:
        proceeds, trade = close_position(
            position, now, float(rows[position.pair][last_ts][4]), args.fee, args.spread_pct, "end_of_test",
        )
        cash += proceeds
        trades.append(trade)
    equity_curve.append(cash)
    return _summary(
        "ETH/SOL Long-only Pairs", args, trades, equity_curve, cash,
        timeline[0] + HOUR, now,
        {"pairs": list(pairs),
         "params": {"taker_fee_rate": args.fee, "spread_pct": args.spread_pct,
                    "z_window_h": 720, "entry_z": 2.0, "exit_abs_z": 0.5,
                    "hard_stop_pct": 10.0, "max_hold_h": 336}},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy", choices=("rotation", "pairs"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default=None)
    parser.add_argument("--budget", type=float, default=500.0)
    parser.add_argument("--fee", type=float, default=0.004)
    parser.add_argument("--spread-pct", type=float, default=0.05)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()
    result = run_rotation(args) if args.strategy == "rotation" else run_pairs(args)
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"{result['bot']}: {result['trades']} Trades, {result['total_return_pct']:+.2f} %, MaxDD {result['max_drawdown_pct']:.2f} %")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
