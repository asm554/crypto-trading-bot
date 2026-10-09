"""Bot 6 - Auditor: Journal in R, Go/No-Go gegen eingefrorene Schwellen. Aendert nie Schwellen."""
import csv
import io

from . import store
from .config import RULES, THRESHOLDS, Rules, Thresholds, thresholds_hash


class ThresholdsChanged(RuntimeError):
    pass


def lock_thresholds(db, t: Thresholds = THRESHOLDS) -> None:
    h = thresholds_hash(t)
    saved = store.get_meta(db, "thresholds_hash")
    if saved is None:
        store.set_meta(db, "thresholds_hash", h)
    elif saved != h:
        raise ThresholdsChanged("Schwellen wurden nach dem ersten Lauf geaendert - Report verweigert.")


def max_drawdown(pnls: list[float], start: float) -> float:
    eq, peak, dd = start, start, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return dd


def _stats(rows, start):
    n = len(rows)
    rs = [r["r_multiple"] for r in rows]
    return {
        "trades": n,
        "win_rate": sum(1 for r in rs if r > 0) / n if n else 0.0,
        "expectancy_r": sum(rs) / n if n else 0.0,
        "max_dd": max_drawdown([r["pnl_usd"] for r in rows], start),
        "fees_usd": sum(r["fee_usd"] for r in rows),
        "slip_usd": sum(r["slip_usd"] for r in rows),
    }


def report(db, rules: Rules = RULES, t: Thresholds = THRESHOLDS) -> dict:
    lock_thresholds(db, t)
    closed = lambda d: [r for r in store.trades(db, decision=d, status="CLOSED")]
    taken, skipped = _stats(closed("TAKEN"), rules.account_usd), _stats(closed("SKIPPED"), rules.account_usd)
    checks = {
        "trades": taken["trades"] >= t.min_trades,
        "win_rate": taken["win_rate"] >= t.min_win_rate,
        "expectancy_r": taken["expectancy_r"] >= t.min_expectancy_r,
        "max_dd": taken["max_dd"] <= t.max_drawdown,
    }
    return {"taken": taken, "skipped_whatif": skipped, "checks": checks, "go": all(checks.values())}


def render(rep: dict, t: Thresholds = THRESHOLDS) -> str:
    k, c = rep["taken"], rep["checks"]
    mark = lambda ok: "PASS" if ok else "FAIL"
    lines = [
        f"Trades        {k['trades']:>6} (>= {t.min_trades})        {mark(c['trades'])}",
        f"Win-Rate      {k['win_rate']:>6.1%} (>= {t.min_win_rate:.0%})       {mark(c['win_rate'])}",
        f"Expectancy    {k['expectancy_r']:>+6.2f}R (>= +{t.min_expectancy_r}R)     {mark(c['expectancy_r'])}",
        f"Max Drawdown  {k['max_dd']:>6.1%} (<= {t.max_drawdown:.0%})       {mark(c['max_dd'])}",
        f"Gebuehren ${k['fees_usd']:.2f} | Slippage ${k['slip_usd']:.2f}",
        f"Uebersprungen (Was-waere-wenn): {rep['skipped_whatif']['trades']} Trades, "
        f"{rep['skipped_whatif']['expectancy_r']:+.2f}R Expectancy",
        "ERGEBNIS: " + ("GO" if rep["go"] else "NO-GO (Paper weiterlaufen lassen)"),
    ]
    return "\n".join(lines)


def journal_csv(db) -> str:
    buf = io.StringIO()
    cols = ["id", "coin", "decision", "add_number", "entry", "stop", "size", "risk_usd", "exit_price",
            "exit_reason", "gross_usd", "fee_usd", "slip_usd", "pnl_usd", "r_multiple"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in store.trades(db, status="CLOSED"):
        w.writerow([r[c] for c in cols])
    return buf.getvalue()
