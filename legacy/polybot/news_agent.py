"""
LangGraph News-Agent: prüft ob aktuelle News das Whale-Signal stützen
und gibt einen Score-Modifier (+/-15) zurück.

Abhängigkeiten:
  pip install langgraph tavily-python anthropic

Umgebungsvariablen:
  TAVILY_API_KEY  – für News-Suche
  ANTHROPIC_API_KEY – für LLM-Bewertung

Verwendung:
  from polybot.news_agent import check_news_alignment
  result = await check_news_alignment("Presidential Election Winner 2028", 72)
  # result = { "score_modifier": 10, "news_summary": "...", "confidence": "HIGH" }
"""

import os
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


class AgentState(TypedDict):
    market_name: str
    whale_score: int
    news_snippets: list[str]
    score_modifier: int
    news_summary: str
    confidence: str


def _fetch_news(state: AgentState) -> AgentState:
    """Node 1: Holt aktuelle News via Tavily."""
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY fehlt – News-Check übersprungen.")
        return {**state, "news_snippets": []}

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            query=state["market_name"],
            search_depth="basic",
            max_results=5,
        )
        snippets = [r.get("content", "") for r in results.get("results", [])]
        logger.info(f"News: {len(snippets)} Ergebnisse für '{state['market_name']}'")
        return {**state, "news_snippets": snippets}
    except Exception as e:
        logger.error(f"Tavily Fehler: {e}")
        return {**state, "news_snippets": []}


def _score_news(state: AgentState) -> AgentState:
    """Node 2: LLM bewertet ob News das Whale-Signal stützen (+/-15)."""
    snippets = state.get("news_snippets", [])
    if not snippets or not OPENAI_API_KEY:
        return {**state, "score_modifier": 0, "news_summary": "Keine News verfügbar.", "confidence": "LOW"}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        news_text = "\n---\n".join(snippets[:5])
        prompt = f"""Du analysierst ein Polymarket-Signal.

Markt: {state['market_name']}
Whale-Score: {state['whale_score']}/100

Aktuelle News:
{news_text}

Aufgabe:
1. Gib einen score_modifier zwischen -15 und +15 zurück (positiv = News stützen das Signal, negativ = News widersprechen)
2. Schreibe eine kurze Zusammenfassung (max 2 Sätze) auf Deutsch
3. Gib confidence an: HIGH, MEDIUM oder LOW

Antworte NUR im folgenden JSON-Format (kein Markdown):
{{"score_modifier": 10, "news_summary": "...", "confidence": "HIGH"}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        result = json.loads(response.choices[0].message.content.strip())
        modifier = max(-15, min(15, int(result.get("score_modifier", 0))))
        return {
            **state,
            "score_modifier": modifier,
            "news_summary": result.get("news_summary", ""),
            "confidence": result.get("confidence", "MEDIUM"),
        }
    except Exception as e:
        logger.error(f"LLM Score-Boost Fehler: {e}")
        return {**state, "score_modifier": 0, "news_summary": "LLM-Fehler.", "confidence": "LOW"}


async def check_news_alignment(market_name: str, whale_score: int) -> dict:
    """
    Hauptfunktion: gibt score_modifier, news_summary, confidence zurück.
    Läuft synchron im Executor (kein echter async-Graph nötig für diesen 2-Node-Flow).
    """
    try:
        from langgraph.graph import StateGraph, END

        graph = StateGraph(AgentState)
        graph.add_node("fetch_news", _fetch_news)
        graph.add_node("score_news", _score_news)
        graph.set_entry_point("fetch_news")
        graph.add_edge("fetch_news", "score_news")
        graph.add_edge("score_news", END)
        app = graph.compile()

        initial_state: AgentState = {
            "market_name": market_name,
            "whale_score": whale_score,
            "news_snippets": [],
            "score_modifier": 0,
            "news_summary": "",
            "confidence": "LOW",
        }
        final_state = app.invoke(initial_state)
        return {
            "score_modifier": final_state["score_modifier"],
            "news_summary": final_state["news_summary"],
            "confidence": final_state["confidence"],
        }
    except ImportError:
        # langgraph nicht installiert – direkt ausführen
        logger.warning("langgraph nicht installiert – führe Nodes direkt aus.")
        state: AgentState = {
            "market_name": market_name,
            "whale_score": whale_score,
            "news_snippets": [],
            "score_modifier": 0,
            "news_summary": "",
            "confidence": "LOW",
        }
        state = _fetch_news(state)
        state = _score_news(state)
        return {
            "score_modifier": state["score_modifier"],
            "news_summary": state["news_summary"],
            "confidence": state["confidence"],
        }
