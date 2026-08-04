"""Aggregate fixed DCA development folds and apply pre-registered criteria.

Example::

    python -m backtest.dca_walkforward_eval \
      --prefix dca_wf_riskfix \
      --json-out backtest/results/bitvavo/dca_wf_riskfix_development_summary.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

FOLDS = ("bull", "bear", "recovery", "y2024", "y2025")
VARIANTS = ("baseline", "trend", "reversal", "trend_reversal", "stop5", "asymmetric", "timeboxed")

MIN_TRADES = 100
MIN_POSITIVE_FOLDS = 4
MIN_RATIO = 1.20
MIN_MEAN_RETURN = 0.0
MIN_WORST_DD = -15.0


def summarize_variant(paths: list[Path]) -> dict:
    results = [json.loads(path.read_text()) for path in paths]
    returns = [float(result["total_return_pct"]) for result in results]
    drawdowns = [float(result["max_drawdown_pct"]) for result in results]
    notional = 0.0
    net_pnl = 0.0
    trades = 0
    for result in results:
        for trade in result.get("trade_log", []):
            size = trade.get("size_eur")
            if not size:
                continue
            trades += 1
            notional += float(size)
            net_pnl += float(trade["pnl_eur"])

    fee_rate = float(results[0].get("params", {}).get("taker_fee_rate", 0.004))
    edge_pct = (net_pnl + notional * 2 * fee_rate) / notional * 100 if notional else 0.0
    ratio = edge_pct / (2 * fee_rate * 100) if fee_rate else 0.0
    positive_folds = sum(value > 0 for value in returns)
    mean_return = statistics.mean(returns)
    worst_dd = min(drawdowns)
    checks = {
        "minimum_pooled_trades": trades >= MIN_TRADES,
        "minimum_positive_development_folds": positive_folds >= MIN_POSITIVE_FOLDS,
        "minimum_pooled_edge_ratio": ratio >= MIN_RATIO,
        "minimum_mean_fold_return_pct": mean_return > MIN_MEAN_RETURN,
        "maximum_worst_fold_drawdown_pct": worst_dd >= MIN_WORST_DD,
    }
    return {
        "files": [path.name for path in paths],
        "fold_returns_pct": dict(zip(FOLDS, returns)),
        "pooled_trades": trades,
        "positive_folds": positive_folds,
        "mean_fold_return_pct": round(mean_return, 3),
        "worst_fold_drawdown_pct": round(worst_dd, 3),
        "pooled_notional_eur": round(notional, 2),
        "pooled_net_pnl_eur": round(net_pnl, 2),
        "pooled_edge_pct": round(edge_pct, 4),
        "pooled_edge_ratio": round(ratio, 3),
        "checks": checks,
        "passes": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="dca_wf_riskfix")
    parser.add_argument("--results-dir", default="backtest/results/bitvavo")
    parser.add_argument("--json-out")
    args = parser.parse_args()

    root = Path(args.results_dir)
    summaries = {}
    for variant in VARIANTS:
        paths = [root / f"{args.prefix}_{variant}_{fold}.json" for fold in FOLDS]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise SystemExit("Missing results: " + ", ".join(missing))
        summaries[variant] = summarize_variant(paths)

    passing = [name for name in VARIANTS if name != "baseline" and summaries[name]["passes"]]
    selected = max(passing, key=lambda name: summaries[name]["pooled_edge_ratio"]) if passing else None
    report = {
        "prefix": args.prefix,
        "criteria": {
            "minimum_pooled_trades": MIN_TRADES,
            "minimum_positive_development_folds": MIN_POSITIVE_FOLDS,
            "minimum_pooled_edge_ratio": MIN_RATIO,
            "minimum_mean_fold_return_pct": MIN_MEAN_RETURN,
            "maximum_worst_fold_drawdown_pct": abs(MIN_WORST_DD),
        },
        "variants": summaries,
        "selected_for_holdout": selected,
    }

    print(f"{'Variante':<18} {'Trades':>7} {'Positiv':>8} {'Mean%':>8} {'WorstDD':>9} {'Ratio':>8} {'PASS':>6}")
    for name in VARIANTS:
        item = summaries[name]
        print(
            f"{name:<18} {item['pooled_trades']:>7} {item['positive_folds']:>5}/5 "
            f"{item['mean_fold_return_pct']:>+8.2f} {item['worst_fold_drawdown_pct']:>+8.2f}% "
            f"{item['pooled_edge_ratio']:>8.3f} {str(item['passes']):>6}"
        )
    print(f"\nSelected for holdout: {selected or 'NONE'}")

    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"JSON: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
