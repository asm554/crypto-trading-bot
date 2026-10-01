"""Jev-Entry-Gate (TypeSafe System One) — optionaler Veto-Filter.

Paper-only Hilfsmodul. Fragt Jev (https://docs.typesafe.ai) per REST, ob ein
bereits von den regulären Gates bestätigtes Einstiegssignal tragfähig wirkt.
Jev kann einen Einstieg nur verhindern, nie auslösen, und ersetzt keine
bestehende Regel. Kein Order-Execution-Code.

Fail-open: Bei fehlendem Key, Timeout, HTTP-Fehler oder unerwarteter Antwort
wird der Einstieg durchgelassen (Verhalten wie ohne Gate) und die Ursache
geloggt. So bleibt der Paper-Vergleich gegen den normalen Surfer sauber.
"""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
QUESTION_KEY = "sustained_breakout"
QUESTION_TEXT = (
    "The price just broke out of its recent range after an uptrend, as described "
    "in `signal`. Will the price keep rising over the next few hours instead of "
    "falling back below the breakout level?"
)


def build_state(pair: str, price: float, ch_trend_pct: float, trend_hours: int,
                ema_fast: float, ema_slow: float, breakout_level: float,
                volume_ratio: float, atr: float) -> dict:
    """Markt-Snapshot als JSON-State für Jev (nur Fakten, keine Wertung).

    Jev ist primär auf Englisch trainiert, daher englische Feldnamen/Texte.
    """
    return {
        "asset": pair,
        "signal": {
            "type": "hourly breakout after confirmed uptrend",
            "price_eur": round(price, 6),
            "trend_change_pct": round(ch_trend_pct, 2),
            "trend_window_hours": trend_hours,
            "ema_fast": round(ema_fast, 6),
            "ema_slow": round(ema_slow, 6),
            "breakout_level_eur": round(breakout_level, 6),
            "price_above_breakout_pct": round((price / breakout_level - 1) * 100, 2),
            "volume_vs_average": round(volume_ratio, 2),
            "atr_pct_of_price": round(atr / price * 100, 2),
        },
    }


class JevGate:
    def __init__(self, api_key: str | None = None, min_prob: float = 0.6,
                 timeout_sec: float = 3.0, model: str = "jev-latest", url: str = API_URL):
        self.api_key = api_key if api_key is not None else os.getenv("TYPESAFE_API_KEY", "")
        self.min_prob = float(min_prob)
        self.timeout_sec = float(timeout_sec)
        self.model = model
        self.url = url

    async def _post(self, payload: dict) -> dict:
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def allows_entry(self, state: str | dict) -> tuple[bool, float | None]:
        """(erlaubt, Wahrscheinlichkeit). Wahrscheinlichkeit ist None bei Fail-open."""
        if not self.api_key:
            logger.warning("JEV: kein TYPESAFE_API_KEY – Gate übersprungen (fail-open)")
            return True, None
        payload = {
            "state": state,
            "model": self.model,
            "questions": {QUESTION_KEY: {"type": "noul", "instructions": QUESTION_TEXT}},
        }
        try:
            data = await self._post(payload)
            prob = float(data["answers"][QUESTION_KEY]["noul"])
        except Exception as exc:  # bewusst breit: das Gate darf den Bot nie stoppen
            logger.warning("JEV: Abfrage fehlgeschlagen (%s) – fail-open", exc)
            return True, None
        if not 0.0 <= prob <= 1.0:
            logger.warning("JEV: Wahrscheinlichkeit %r außerhalb 0..1 – fail-open", prob)
            return True, None
        return prob >= self.min_prob, prob

    async def ask(self, state: str | dict, questions: dict) -> dict | None:
        """Mehrere Fragen in einem Aufruf. Gibt ``answers`` zurück oder None bei Fehler.

        Anders als ``allows_entry`` ist das fail-closed gedacht: Wer Jev als
        Signalquelle nutzt, darf bei Ausfall nicht handeln.
        """
        if not self.api_key:
            logger.warning("JEV: kein TYPESAFE_API_KEY – keine Abfrage möglich")
            return None
        payload = {"state": state, "model": self.model, "questions": questions}
        try:
            data = await self._post(payload)
            answers = data["answers"]
            if not isinstance(answers, dict):
                raise ValueError("answers ist kein Objekt")
            return answers
        except Exception as exc:
            logger.warning("JEV: Abfrage fehlgeschlagen (%s)", exc)
            return None
