"""Judge-Schicht. JevJudge ruft api.typesafe.ai (ein Aufruf je Fragensatz). RuleJudge ist der Regel-Fallback
ohne Key; seine Antworten tragen model='rules-fallback', damit das Log nie mit Jev verwechselt wird."""
import os
import time

import requests

from .questions import build

API_URL = "https://api.typesafe.ai/v1/systemone"


class JudgeError(RuntimeError):
    pass


class JevJudge:
    def __init__(self, api_key: str | None = None, model: str = "jev-latest", url: str = API_URL,
                 timeout: float = 10.0, post=None):
        self.api_key = api_key if api_key is not None else os.getenv("TYPESAFE_API_KEY", "")
        if not self.api_key:
            raise JudgeError("TYPESAFE_API_KEY fehlt")
        self.model, self.url, self.timeout = model, url, timeout
        self._post = post or self._http_post

    def _http_post(self, payload: dict) -> dict:
        for attempt in range(3):
            r = requests.post(self.url, json=payload, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code in (429, 529):       # Backoff, nie bei 422
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 403):
                raise JudgeError(f"Jev-Key abgelehnt ({r.status_code})")
            if r.status_code == 422:
                raise JudgeError(f"malformed question set: {r.text[:300]}")
            r.raise_for_status()
            return r.json()
        raise JudgeError("rate limited")

    def ask(self, set_name: str, state: dict) -> dict:
        data = self._post({"state": state, "model": self.model, "questions": build(set_name, state)})
        answers = data["answers"]
        if not isinstance(answers, dict):
            raise JudgeError("answers ist kein Objekt")
        return {"model": data.get("model", "unknown"), "answers": answers}


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _noul(p):
    return {"type": "noul", "noul": round(_clip(p), 3)}


def _choice(probs: dict):
    total = sum(probs.values()) or 1
    probs = {k: v / total for k, v in probs.items()}
    best = max(probs, key=probs.get)
    return {"type": "choice", "choice": best, "probabilities": probs, "confidence": probs[best]}


class RuleJudge:
    """Grobe, deterministische Heuristiken. Zweck: die Pipeline ohne Key lauffaehig halten, kein Ersatz fuer Jev."""
    model = "rules-fallback"

    def ask(self, set_name: str, state: dict) -> dict:
        fn = getattr(self, f"_{set_name}")
        return {"model": self.model, "answers": fn(state)}

    @staticmethod
    def _buy_ratio(s):
        b, se = s.get("buys_h1") or 0, s.get("sells_h1") or 0
        return b / (b + se) if b + se else 0.5

    def _market(self, s):
        r = self._buy_ratio(s)
        vol = s.get("volume_usd") or {}
        recent = (vol.get("h1") or 0) * 24 / max(vol.get("h24") or 1, 1)  # 1 = gleichmaessig
        crowd = _clip((r - 0.4) * 3) * _clip(recent)
        fading = 1 - _clip(recent)
        ch = s.get("price_change_pct") or {}
        spent = _clip(((ch.get("h24") or 0) - 4 * (ch.get("h1") or 0)) / 400)
        return {
            "shape": _choice({"crowd": crowd + 0.01, "one_buyer": 0.1 * (1 - crowd), "fading": fading + 0.01,
                              "too_early": 0.05}),
            "liquidity_fits_ticket": _noul((s.get("liquidity_usd") or 0) / max((s.get("intended_ticket_usd") or 1) * 50, 1)),
            "momentum_already_spent": _noul(spent),
        }

    def _conc(self, s):
        t10 = s.get("top_10_holders_pct_of_supply")
        return _noul(float(t10) / 100 if t10 is not None else 0.5)

    def _dev(self, s):
        return _noul((s.get("developer_holding_pct_of_supply") or 0) / 20)

    def _solana(self, s):
        m, f = bool(s.get("mint_authority")), bool(s.get("freeze_authority"))
        probs = {"renounced": 1.0 if not (m or f) else 0.0, "mint_open": 1.0 if m and not f else 0.0,
                 "freeze_open": 1.0 if f and not m else 0.0, "both_open": 1.0 if m and f else 0.0}
        return {"authority_risk": _choice(probs),
                "concentration_is_exit_risk": self._conc(s), "dev_still_loaded": self._dev(s)}

    def _bsc(self, s):
        hp = s.get("is_honeypot")
        sells = (s.get("sells_h1") or 0)
        if hp is True:
            p = {"flagged": 1.0}
        elif hp is None or hp == "unknown":
            p = {"unknown": 1.0}
        elif sells == 0 and (s.get("buys_h1") or 0) > 20:
            p = {"suspicious": 1.0}
        else:
            p = {"clean": 1.0}
        return {"sell_side_risk": _choice({"clean": 0, "flagged": 0, "suspicious": 0, "unknown": 0, **p}),
                "concentration_is_exit_risk": self._conc(s)}

    def _robinhood(self, s):
        has_holders = s.get("holder_count") is not None
        has_dist = s.get("top_10_holders_pct_of_supply") is not None
        cov = "indexed" if has_holders and has_dist else "partial" if has_holders else "dark"
        sells = s.get("sells_h1") or 0
        return {"data_coverage": _choice({"indexed": 0, "partial": 0, "dark": 0, cov: 1.0}),
                "sellable_by_evidence": _noul(1.0 if sells >= 10 else sells / 10),
                "concentration_is_exit_risk": self._conc(s), "dev_still_loaded": self._dev(s)}

    def _pick(self, s):
        cands = s["candidates"]
        scores = {c["key"]: c.get("rule_score", 0.5) for c in cands}
        out = _choice(scores)
        out["confidence"] = round(max(scores.values()), 3)   # absolut, sonst sinkt sie mit jeder weiteren Option
        return {"best": out, "worth_trading_at_all": _noul(max(scores.values()))}
