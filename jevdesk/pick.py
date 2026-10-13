"""Ein Choice ueber die Ueberlebenden plus das absolute Tor 'lohnt der Tag'. Kein Re-Ranking."""
from .config import DARK_TICKET_CUT, NO_SOCIAL_CUT, PICK_MIN_CONF, PICK_MIN_WORTH


def summary(d, ans) -> str:
    bits = [f"{d['chain']}, {d['age_minutes']:.0f}m old, ${d['mcap_usd']:,.0f} mcap, "
            f"${d['liquidity_usd']:,.0f} liq, {d.get('holder_count') or '?'} holders",
            f"crowd {ans['shape']['probabilities'].get('crowd', 0):.2f}, "
            f"concentration risk {ans['concentration_is_exit_risk']['noul']:.2f}"]
    if "authority_risk" in ans:
        bits.append(f"authority {ans['authority_risk']['choice']}")
    if "sell_side_risk" in ans:
        bits.append(f"sell side {ans['sell_side_risk']['choice']}")
    if "data_coverage" in ans:
        bits.append(f"data {ans['data_coverage']['choice']}")
    bits.append("no X account read")
    return "; ".join(bits)


def pick(judge, survivors) -> dict | None:
    """survivors: [(dossier, answers)]. Gibt Order-Dict oder None."""
    if not survivors:
        return None
    cands = [{"key": f"candidate_{i + 1}", "summary": summary(d, a)} for i, (d, a) in enumerate(survivors)]
    if getattr(judge, "needs_hints", False):   # nur der Regel-Fallback braucht Zahlen
        for c, (d, a) in zip(cands, survivors):
            c["rule_score"] = a["shape"]["probabilities"].get("crowd", 0) * (1 - a["concentration_is_exit_risk"]["noul"])
    r = judge.ask("pick", {"candidates": cands})
    best, worth = r["answers"]["best"], r["answers"]["worth_trading_at_all"]
    if worth["noul"] < PICK_MIN_WORTH:
        return {"none": "worth_trading_at_all", "value": worth["noul"], "model": r["model"]}
    if best["confidence"] < PICK_MIN_CONF:
        return {"none": "low_confidence", "value": best["confidence"], "model": r["model"]}
    keys = {c["key"]: i for i, c in enumerate(cands)}
    hit = survivors[keys[best["choice"]]] if best["choice"] in keys else None
    if hit is None:
        return {"none": "choice_not_in_list", "value": None, "model": r["model"]}
    return finish(hit, r["model"], best["confidence"], best["probabilities"])


def finish(hit, model, confidence, probs=None) -> dict:
    d, ans = hit
    size_factor = NO_SOCIAL_CUT                      # v1 liest kein X
    if ans.get("data_coverage", {}).get("choice") == "dark":
        size_factor *= DARK_TICKET_CUT
    return {"token": d, "answers": ans, "model": model, "confidence": confidence,
            "size_factor": round(size_factor, 2), "probabilities": probs}
