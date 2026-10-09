"""Bot 3 - Risk Officer: sieht nur Konto, Stop, Risiko. Kein Chart."""
from .config import Rules


def build_plan(rules: Rules, equity: float, entry: float, stop: float,
               open_same_coin: list[dict], pending_same_coin: int, mark: float) -> dict:
    """open_same_coin: TAKEN+OPEN Trades der Coin (neuester zuletzt). Rueckgabe: {'ok':True,...} oder {'ok':False,'reason'}"""
    if stop <= entry:
        return {"ok": False, "reason": "stop <= entry"}
    if pending_same_coin:
        return {"ok": False, "reason": "offene Entscheidung auf dieser Coin"}
    add_number = len(open_same_coin)
    if add_number > rules.max_adds:
        return {"ok": False, "reason": f"max_adds={rules.max_adds} erreicht"}
    if add_number:
        prev = open_same_coin[-1]
        r_dist = prev["stop"] - prev["entry"]
        if (prev["entry"] - mark) < rules.be_trigger_r * r_dist:
            return {"ok": False, "reason": "Vorgaenger-Entry nicht bei Breakeven"}
    risk_pct = rules.risk_pct if add_number == 0 else rules.add_risk_pct
    risk_usd = equity * risk_pct / 100
    size = risk_usd / (stop - entry)
    cap = equity * rules.max_leverage / entry
    if size > cap:
        size = cap
        risk_usd = size * (stop - entry)
        risk_pct = risk_usd / equity * 100
    return {"ok": True, "size": size, "risk_usd": risk_usd, "risk_pct": risk_pct, "add_number": add_number}
