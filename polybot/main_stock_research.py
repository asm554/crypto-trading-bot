"""Monatlicher Aktien-Research-Lauf: Screen -> Claude-Analyse -> Telegram + Markdown.

    python -m polybot.main_stock_research
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from polybot import config  # noqa: F401  (lädt polybot/.env)
from polybot.alerts import send_telegram
from polybot.stock_research import (
    DEFAULT_MODEL,
    DEFAULT_WATCHLIST_PATH,
    StockResearcher,
    build_markdown_report,
    build_telegram_summary,
    load_watchlist,
    split_message,
    write_report,
)


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tickers = load_watchlist(os.getenv("STOCK_WATCHLIST_PATH", DEFAULT_WATCHLIST_PATH))
    top_n = int(os.getenv("STOCK_TOP_N", "8"))
    llm_enabled = _env_bool("STOCK_LLM_ENABLED", True) and bool(os.getenv("ANTHROPIC_API_KEY"))
    researcher = StockResearcher(
        tickers,
        top_n=top_n,
        llm_enabled=llm_enabled,
        model=os.getenv("STOCK_MODEL", DEFAULT_MODEL),
        benchmark=os.getenv("STOCK_BENCHMARK", "URTH"),
    )
    now = datetime.now(timezone.utc)
    results, review = await researcher.run(now=now)
    if not results:
        await send_telegram("📊 Aktien-Research: keine Daten abrufbar (yfinance down?).")
        return

    path = write_report(build_markdown_report(results, review, top_n, now), now)
    summary = build_telegram_summary(results, review, top_n, now) + f"\nReport: {path}"
    print(summary)
    for chunk in split_message(summary):
        try:
            await send_telegram(chunk)
        except Exception as e:
            print(f"Telegram failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
