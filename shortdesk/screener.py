"""Bot 1 - Screener: bestaetigter Daily-Trendwechsel per Drei-Kerzen-Regel (short). Bullische Flips nur Kontext."""
from .candles import C
from .config import DAY


def _mirror(cs: list[C]) -> list[C]:
    return [C(c.ts, -c.o, -c.l, -c.h, -c.c, c.v) for c in cs]


def detect_short_flip(daily: list[C], max_age_days: int = 7) -> dict | None:
    """C1 schliesst runter; C2-Close < C1-Close, C2-High < C1-High; C3-Close < C2-Close, C3-High < C1-High.
    Dojis zaehlen, C2 muss nicht rot sein. State endet mit Daily-Close ueber dem Anker (C1-High)."""
    n = len(daily)
    for i in range(n - 3, max(-1, n - 4 - max_age_days), -1):
        c1, c2, c3 = daily[i:i + 3]
        if not (c1.c < c1.o):
            continue
        if not (c2.c < c1.c and c2.h < c1.h):
            continue
        if not (c3.c < c2.c and c3.h < c1.h):
            continue
        if any(c.c > c1.h for c in daily[i + 3:]):
            continue  # Anker per Close ueberschritten -> dieser Flip ist tot
        return {"anchor_high": c1.h, "anchor_ts": c1.ts, "flip_ts": c3.ts + DAY}
    return None


def detect_long_flip(daily: list[C], max_age_days: int = 7) -> dict | None:
    f = detect_short_flip(_mirror(daily), max_age_days)
    if f:
        f = {**f, "anchor_high": -f["anchor_high"]}  # = Anker-Tief
    return f


def is_bouncing_4h(c4h: list[C], bars: int = 6) -> bool:
    return len(c4h) > bars and c4h[-1].c > c4h[-1 - bars].c


def scan(coin: str, daily: list[C], c4h: list[C], max_age_days: int, bounce_bars: int) -> dict:
    """Ergebnis: {'short': {...,'tag'} | None, 'bull_context': bool}"""
    short = detect_short_flip(daily, max_age_days)
    if short:
        short = {**short, "coin": coin, "tag": "PRIME" if is_bouncing_4h(c4h, bounce_bars) else "FLIP"}
    return {"short": short, "bull_context": detect_long_flip(daily, max_age_days) is not None}
