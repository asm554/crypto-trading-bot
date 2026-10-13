"""Monatlicher Aktien-Researcher ("Der Analyst").

Ablauf pro Lauf:
1. Watchlist laden (``stock_watchlist.txt``, ein Ticker pro Zeile).
2. Pro Ticker Fundamentaldaten + 1 Jahr Tageskurse + News via yfinance holen.
3. Harter Code-Screener (Checkliste): Bewertung, Wachstum, Bilanz, Qualität,
   Technik -> 0-100 Punkte. Das LLM bekommt nur die Top-N.
4. Claude schreibt für die Top-N eine Fundamental-, Chart- und News-Analyse
   mit Szenarien und einem festen Fazit (KAUFEN/HALTEN/BEOBACHTEN/MEIDEN).
5. Picks werden mit Kurs gespeichert; im nächsten Monat wird ehrlich gegen
   die Benchmark abgerechnet, ob die Picks etwas getaugt haben.

Reine Research-/Paper-Funktion: es gibt keine Order-Ausführung und es soll
auch keine dazukommen (siehe Safety Rules in CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
DEFAULT_WATCHLIST_PATH = os.path.join(BASE_DIR, "stock_watchlist.txt")
DATA_DIR = os.path.join(BASE_DIR, "data")
PICKS_PATH = os.path.join(DATA_DIR, "stock_picks.json")
REPORT_DIR = os.path.join(DATA_DIR, "stock_reports")

DEFAULT_MODEL = "claude-opus-5-5"
VERDICTS = ("KAUFEN", "HALTEN", "BEOBACHTEN", "MEIDEN")
NEUTRAL = 10.0  # Punkte, wenn eine Kennzahl fehlt (z. B. bei ETFs/Banken)


# ---------------------------------------------------------------------------
# Indikatoren (reine Funktionen, testbar)
# ---------------------------------------------------------------------------

def sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        d = cur - prev
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def macd(closes: list[float]) -> tuple[float, float] | None:
    """MACD-Linie (EMA12-EMA26) und Signal (EMA9 der Linie), jeweils letzter Wert."""
    if len(closes) < 35:
        return None
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    signal = ema_series(line, 9)
    return line[-1], signal[-1]


def pct_change(closes: list[float], bars_back: int, skip_last: int = 0) -> float | None:
    """Rendite in % von ``bars_back`` Bars vor dem Ende bis ``skip_last`` Bars vor dem Ende."""
    if len(closes) <= bars_back or bars_back <= skip_last:
        return None
    start = closes[-bars_back - 1]
    end = closes[-1 - skip_last]
    if start <= 0:
        return None
    return (end / start - 1) * 100


def technicals(closes: list[float]) -> dict:
    """Technik-Kennzahlen aus Tagesschlusskursen (ca. 1 Jahr)."""
    if not closes:
        return {}
    price = closes[-1]
    sma50, sma200 = sma(closes, 50), sma(closes, 200)
    m = macd(closes)
    high_52w = max(closes[-252:])
    return {
        "price": round(price, 2),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi14": round(r, 1) if (r := rsi_wilder(closes)) is not None else None,
        "macd": round(m[0], 3) if m else None,
        "macd_signal": round(m[1], 3) if m else None,
        # 12-1-Momentum: 12 Monate Rendite ohne den letzten Monat (klassischer Faktor)
        "mom_12_1_pct": _r(pct_change(closes, 252, skip_last=21)),
        "ret_1m_pct": _r(pct_change(closes, 21)),
        "ret_3m_pct": _r(pct_change(closes, 63)),
        "drawdown_52w_pct": round((price / high_52w - 1) * 100, 1) if high_52w else None,
        "support_3m": round(min(closes[-63:]), 2),
        "resistance_3m": round(max(closes[-63:]), 2),
    }


def _r(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


# ---------------------------------------------------------------------------
# Checklisten-Screener
# ---------------------------------------------------------------------------

def _tier(value: float | None, thresholds: list[tuple[float, float]], floor: float, higher_is_better: bool = True) -> float | None:
    """Ordnet ``value`` über Schwellen Punkte zu. ``None`` bleibt ``None``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    for limit, points in thresholds:
        if (value >= limit) if higher_is_better else (value <= limit):
            return points
    return floor


def _avg(parts: list[float | None]) -> float:
    vals = [p for p in parts if p is not None]
    return sum(vals) / len(vals) if vals else NEUTRAL


def score_stock(fund: dict, tech: dict) -> dict:
    """Checkliste: 5 Kategorien à max. 20 Punkte = max. 100.

    Fehlende Kennzahlen zählen neutral (10), damit z. B. Banken ohne
    sinnvolle Debt/Equity nicht automatisch rausfliegen.
    """
    pe = fund.get("forward_pe")
    if pe is not None and pe <= 0:
        pe_score = 0.0  # Verlust erwartet
    else:
        pe_score = _tier(pe, [(15, 20), (22, 15), (30, 9), (45, 4)], 0, higher_is_better=False)
    peg = fund.get("peg")
    peg_score = None if peg is None or peg <= 0 else _tier(peg, [(1.0, 20), (1.5, 15), (2.5, 8)], 2, higher_is_better=False)
    valuation = _avg([pe_score, peg_score])

    growth = _avg([
        _tier(fund.get("revenue_growth"), [(0.15, 20), (0.08, 15), (0.03, 10), (0.0, 6)], 0),
        _tier(fund.get("earnings_growth"), [(0.20, 20), (0.10, 15), (0.0, 8)], 0),
    ])

    de = fund.get("debt_to_equity")  # yfinance liefert Prozent (150 = 1,5x)
    fcf = fund.get("free_cashflow")
    health = _avg([
        _tier(de, [(50, 20), (100, 15), (200, 8)], 2, higher_is_better=False) if de is not None and de >= 0 else None,
        _tier(fund.get("current_ratio"), [(1.5, 20), (1.0, 14)], 5),
        None if fcf is None else (20.0 if fcf > 0 else 0.0),
    ])

    quality = _avg([
        _tier(fund.get("roe"), [(0.20, 20), (0.12, 15), (0.05, 8)], 0),
        _tier(fund.get("profit_margin"), [(0.20, 20), (0.10, 15), (0.03, 8)], 0),
        _tier(fund.get("gross_margin"), [(0.50, 20), (0.30, 14)], 6),
    ])

    tech_parts: list[float | None] = []
    price, s50, s200 = tech.get("price"), tech.get("sma50"), tech.get("sma200")
    if price is not None and s200:
        tech_parts.append(20.0 if price > s200 else 4.0)
    if s50 and s200:
        tech_parts.append(20.0 if s50 > s200 else 4.0)
    tech_parts.append(_tier(tech.get("mom_12_1_pct"), [(25, 20), (10, 15), (0, 10)], 2))
    rsi = tech.get("rsi14")
    if rsi is not None:
        # Überkauft (>75) wird bestraft: monatlich kaufen heißt nicht ins Top springen.
        tech_parts.append(6.0 if rsi > 75 else 18.0 if 40 <= rsi <= 70 else 12.0)
    technical = _avg(tech_parts)

    cats = {
        "bewertung": round(valuation, 1),
        "wachstum": round(growth, 1),
        "bilanz": round(health, 1),
        "qualitaet": round(quality, 1),
        "technik": round(technical, 1),
    }
    return {**cats, "total": round(sum(cats.values()), 1)}


# ---------------------------------------------------------------------------
# Datenabruf (yfinance) – auf dem Server; in Tests gemockt
# ---------------------------------------------------------------------------

_INFO_MAP = {
    "name": "longName",
    "sector": "sector",
    "industry": "industry",
    "currency": "currency",
    "market_cap": "marketCap",
    "forward_pe": "forwardPE",
    "trailing_pe": "trailingPE",
    "peg": "trailingPegRatio",
    "price_to_book": "priceToBook",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "debt_to_equity": "debtToEquity",
    "current_ratio": "currentRatio",
    "free_cashflow": "freeCashflow",
    "roe": "returnOnEquity",
    "profit_margin": "profitMargins",
    "gross_margin": "grossMargins",
    "dividend_yield": "dividendYield",
    "analyst_target": "targetMeanPrice",
    "analyst_rating": "recommendationKey",
    "summary": "longBusinessSummary",
}


def fetch_stock_data(ticker: str) -> dict | None:
    """Holt Fundamentaldaten, ~1 Jahr Tagesschlusskurse und News-Headlines."""
    import yfinance as yf  # lazy: nur der Researcher braucht das Paket

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="13mo", interval="1d", auto_adjust=True)
        closes = [float(c) for c in hist["Close"].dropna().tolist()]
        if len(closes) < 60:
            logger.warning("%s: zu wenig Kursdaten (%d)", ticker, len(closes))
            return None
        info = t.info or {}
        fund = {key: info.get(src) for key, src in _INFO_MAP.items()}
        if fund.get("summary"):
            fund["summary"] = fund["summary"][:600]
        news = []
        for item in (t.news or [])[:8]:
            content = item.get("content", item)  # neues und altes yfinance-Format
            title = content.get("title")
            if title:
                provider = content.get("provider", {})
                news.append({
                    "title": title,
                    "publisher": provider.get("displayName") if isinstance(provider, dict) else content.get("publisher"),
                    "date": content.get("pubDate") or content.get("providerPublishTime"),
                })
        return {"ticker": ticker, "closes": closes, "fundamentals": fund, "news": news}
    except Exception as e:  # Ein kaputter Ticker darf den Monatslauf nicht abbrechen
        logger.warning("%s: Datenabruf fehlgeschlagen: %s", ticker, e)
        return None


def fetch_last_price(ticker: str) -> float | None:
    data = fetch_stock_data(ticker)
    return data["closes"][-1] if data else None


# ---------------------------------------------------------------------------
# Claude-Analyse
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Du bist ein nüchterner Aktien-Research-Analyst für einen Privatanleger, \
der einmal im Monat Positionen überprüft und neue Käufe plant (Haltedauer Monate, kein Daytrading).

Du bekommst pro Aktie strukturierte Daten: Fundamentaldaten, technische Kennzahlen, \
einen Checklisten-Score (0-100) und aktuelle News-Headlines. Arbeite nur mit diesen Daten; \
wenn etwas fehlt, sag das, statt es zu erfinden. Keine Kursziele als Prognose, sondern Szenarien.

Antworte auf Deutsch in genau diesem Markdown-Format:

### Geschäftsmodell & Burggraben
2-3 Sätze: womit verdient die Firma Geld, was schützt sie vor Konkurrenz.

### Fundamentals
Bewertung, Wachstum, Bilanz, Profitabilität – was ist stark, was ist schwach.

### Chart & Technik
Trend (SMA50/200), Momentum, RSI, MACD, Unterstützung/Widerstand. Ist jetzt ein guter Einstiegszeitpunkt oder eher abwarten?

### News & Risiken
Was sagen die Headlines kurz- und langfristig? Die 2-3 größten Risiken.

### Szenarien (12 Monate)
- Bull: ...
- Basis: ...
- Bear: ...

### Fazit
2-3 Sätze klare Einschätzung inkl. was die These widerlegen würde.

Letzte Zeile exakt: VERDICT: <KAUFEN|HALTEN|BEOBACHTEN|MEIDEN>"""


def build_stock_prompt(ticker: str, fund: dict, tech: dict, score: dict, news: list[dict]) -> str:
    payload = {
        "ticker": ticker,
        "fundamentaldaten": fund,
        "technik": tech,
        "checklisten_score": score,
        "news": news,
    }
    return "Analysiere diese Aktie:\n\n" + json.dumps(payload, ensure_ascii=False, indent=1, default=str)


_VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*(KAUFEN|HALTEN|BEOBACHTEN|MEIDEN)", re.IGNORECASE)


def parse_verdict(text: str) -> str | None:
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].upper() if matches else None


def analyze_with_claude(client, model: str, prompt: str) -> str | None:
    """Ein Claude-Call pro Aktie. Gibt den Analysetext zurück oder ``None``."""
    import anthropic

    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            # Bei einer Ablehnung durch Safety-Klassifizierer serverseitig auf
            # das empfohlene Ersatzmodell ausweichen statt leer zurückzukommen.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as e:
        logger.warning("Claude rate-limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        logger.warning("Claude API-Fehler %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        logger.warning("Claude nicht erreichbar: %s", e)
        return None

    if response.stop_reason == "refusal":
        logger.warning("Claude hat die Analyse abgelehnt")
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


# ---------------------------------------------------------------------------
# Pick-Tracking (ehrliche Erfolgskontrolle)
# ---------------------------------------------------------------------------

def load_picks(path: str = PICKS_PATH) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_picks(picks: list[dict], path: str = PICKS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(picks, f, ensure_ascii=False, indent=1)


def evaluate_last_picks(picks: list[dict], current_prices: dict[str, float]) -> dict | None:
    """Rendite der KAUFEN-Picks des letzten Laufs vs. Benchmark (gleichgewichtet)."""
    if not picks:
        return None
    last_run = max(p["run"] for p in picks)
    batch = [p for p in picks if p["run"] == last_run]
    rows = []
    for p in batch:
        now = current_prices.get(p["ticker"])
        if now is None or not p.get("price"):
            continue
        rows.append({**p, "now": now, "ret_pct": round((now / p["price"] - 1) * 100, 1)})
    buys = [r for r in rows if r.get("verdict") == "KAUFEN" and not r.get("benchmark")]
    bench = next((r for r in rows if r.get("benchmark")), None)
    avg_buy = round(sum(r["ret_pct"] for r in buys) / len(buys), 1) if buys else None
    return {
        "run": last_run,
        "rows": rows,
        "buy_avg_pct": avg_buy,
        "benchmark_pct": bench["ret_pct"] if bench else None,
        "benchmark": bench["ticker"] if bench else None,
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------

@dataclass
class StockResult:
    ticker: str
    name: str | None
    score: dict
    tech: dict
    fund: dict
    news: list[dict] = field(default_factory=list)
    analysis: str | None = None
    verdict: str | None = None


def load_watchlist(path: str = DEFAULT_WATCHLIST_PATH) -> list[str]:
    tickers = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            t = line.split("#", 1)[0].strip().upper()
            if t and t not in tickers:
                tickers.append(t)
    return tickers


class StockResearcher:
    def __init__(
        self,
        tickers: list[str],
        top_n: int = 8,
        llm_enabled: bool = True,
        model: str = DEFAULT_MODEL,
        benchmark: str = "URTH",
        fetcher=fetch_stock_data,
        client=None,
    ):
        self.tickers = tickers
        self.top_n = top_n
        self.llm_enabled = llm_enabled
        self.model = model
        self.benchmark = benchmark
        self.fetcher = fetcher
        self.client = client

    async def screen(self) -> list[StockResult]:
        datas = await asyncio.gather(*(asyncio.to_thread(self.fetcher, t) for t in self.tickers))
        results = []
        for data in datas:
            if not data:
                continue
            tech = technicals(data["closes"])
            fund = data["fundamentals"]
            results.append(StockResult(
                ticker=data["ticker"],
                name=fund.get("name"),
                score=score_stock(fund, tech),
                tech=tech,
                fund=fund,
                news=data.get("news", []),
            ))
        results.sort(key=lambda r: r.score["total"], reverse=True)
        return results

    def _get_client(self):
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()
        return self.client

    async def analyze(self, results: list[StockResult]) -> None:
        if not self.llm_enabled:
            return
        try:
            client = self._get_client()
        except Exception as e:
            logger.warning("Claude-Client nicht verfügbar, nur Zahlen-Report: %s", e)
            return
        for r in results[: self.top_n]:
            prompt = build_stock_prompt(r.ticker, r.fund, r.tech, r.score, r.news)
            r.analysis = await asyncio.to_thread(analyze_with_claude, client, self.model, prompt)
            r.verdict = parse_verdict(r.analysis) if r.analysis else None

    async def run(self, now: datetime | None = None, picks_path: str = PICKS_PATH) -> tuple[list[StockResult], dict | None]:
        now = now or datetime.now(timezone.utc)
        picks = load_picks(picks_path)
        results = await self.screen()

        # Letzten Monat abrechnen: aktuelle Preise aus dem Screen, Rest nachladen.
        prices = {r.ticker: r.tech["price"] for r in results if r.tech.get("price")}
        missing = {p["ticker"] for p in picks if p["ticker"] not in prices}
        if self.benchmark:
            missing.add(self.benchmark)
        for t in missing - set(prices):
            data = await asyncio.to_thread(self.fetcher, t)
            if data:
                prices[t] = data["closes"][-1]
        review = evaluate_last_picks(picks, prices)

        await self.analyze(results)

        run_id = now.strftime("%Y-%m-%d")
        new = [
            {"run": run_id, "ticker": r.ticker, "price": r.tech["price"], "verdict": r.verdict, "score": r.score["total"]}
            for r in results[: self.top_n] if r.tech.get("price")
        ]
        if self.benchmark and prices.get(self.benchmark):
            new.append({"run": run_id, "ticker": self.benchmark, "price": prices[self.benchmark], "verdict": None, "benchmark": True})
        save_picks([p for p in picks if p["run"] != run_id] + new, picks_path)
        return results, review


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_VERDICT_ICON = {"KAUFEN": "🟢", "HALTEN": "🔵", "BEOBACHTEN": "🟡", "MEIDEN": "🔴", None: "⚪"}


def _fmt(x, suffix="", nd=1) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}f}{suffix}"
    return f"{x}{suffix}"


def build_telegram_summary(results: list[StockResult], review: dict | None, top_n: int, now: datetime) -> str:
    lines = [f"📊 Aktien-Research {now:%m/%Y}", ""]
    if review:
        lines.append(f"Rückblick Picks vom {review['run']}:")
        buys = [r for r in review["rows"] if r.get("verdict") == "KAUFEN"]
        for r in buys:
            lines.append(f"  {r['ticker']}: {r['ret_pct']:+.1f} %")
        lines.append(
            f"  KAUFEN-Schnitt {_fmt(review['buy_avg_pct'], ' %')} vs. "
            f"{review['benchmark'] or 'Benchmark'} {_fmt(review['benchmark_pct'], ' %')}"
        )
        lines.append("")
    lines.append(f"Top {top_n} (Checklisten-Score /100):")
    for r in results[:top_n]:
        lines.append(
            f"{_VERDICT_ICON.get(r.verdict, '⚪')} {r.ticker} {r.score['total']:.0f} "
            f"| RSI {_fmt(r.tech.get('rsi14'), nd=0)} | 12-1M {_fmt(r.tech.get('mom_12_1_pct'), ' %')}"
            f"{' | ' + r.verdict if r.verdict else ''}"
        )
    if len(results) > top_n:
        rest = ", ".join(f"{r.ticker} {r.score['total']:.0f}" for r in results[top_n:])
        lines += ["", f"Rest: {rest}"]
    lines += ["", "Volle Analyse im Markdown-Report. Keine Anlageberatung."]
    return "\n".join(lines)


def build_markdown_report(results: list[StockResult], review: dict | None, top_n: int, now: datetime) -> str:
    out = [f"# Aktien-Research {now:%B %Y}", "", f"Erstellt {now:%Y-%m-%d %H:%M} UTC. Keine Anlageberatung.", ""]
    if review:
        out += [f"## Rückblick: Picks vom {review['run']}", "", "| Ticker | Verdict | Kurs damals | Kurs jetzt | Rendite |", "|---|---|---|---|---|"]
        for r in review["rows"]:
            label = "Benchmark" if r.get("benchmark") else (r.get("verdict") or "–")
            out.append(f"| {r['ticker']} | {label} | {r['price']:.2f} | {r['now']:.2f} | {r['ret_pct']:+.1f} % |")
        out += ["", f"**KAUFEN-Schnitt:** {_fmt(review['buy_avg_pct'], ' %')} · **Benchmark:** {_fmt(review['benchmark_pct'], ' %')}", ""]

    out += ["## Ranking", "", "| # | Ticker | Score | Bew. | Wachs. | Bilanz | Qual. | Technik | Verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        s = r.score
        out.append(
            f"| {i} | {r.ticker} | {s['total']:.0f} | {s['bewertung']:.0f} | {s['wachstum']:.0f} | "
            f"{s['bilanz']:.0f} | {s['qualitaet']:.0f} | {s['technik']:.0f} | {r.verdict or '–'} |"
        )
    out.append("")

    for r in results[:top_n]:
        t, f = r.tech, r.fund
        out += [
            f"## {r.ticker} – {r.name or ''}",
            "",
            f"Kurs {_fmt(t.get('price'), nd=2)} {f.get('currency') or ''} · Fwd-KGV {_fmt(f.get('forward_pe'))} · "
            f"SMA50/200 {_fmt(t.get('sma50'), nd=2)}/{_fmt(t.get('sma200'), nd=2)} · RSI {_fmt(t.get('rsi14'))} · "
            f"52W-Drawdown {_fmt(t.get('drawdown_52w_pct'), ' %')}",
            "",
            r.analysis or "_Keine KI-Analyse (deaktiviert oder fehlgeschlagen)._",
            "",
        ]
    return "\n".join(out)


def write_report(markdown: str, now: datetime, report_dir: str = REPORT_DIR) -> str:
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"{now:%Y-%m}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Telegram erlaubt max. 4096 Zeichen pro Nachricht – an Zeilen umbrechen."""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks
