"""Lead / router agent.

The conversational front door: parses the user's plain-English query into the
structured state fields the downstream agents consume. LLM-powered, but the
LLM only classifies and extracts — it never answers the query itself.

Outputs per task type:
  sentiment       sentiment_action ("analyze" | "refresh_news") and, for
                  analyze, sentiment_request (asset_class, horizon, optional
                  as_of date, optional freeform question)
  pick_trade      trade_objectives — ONLY the keys the user explicitly
                  changed; everything else stays at config defaults
  portfolio_task  portfolio_action ("add_trade" | "exit_trade" |
                  "analytics") + portfolio_instruction (the raw query; the
                  portfolio manager extracts trade_ids itself)

Standalone:  python -m agents.lead_router "load news to rag"
"""

import json
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from config import DEFAULT_TRADE_OBJECTIVES, MODEL, env_var
from graph.state import PortfolioState


class RouteDecision(BaseModel):
    """Structured routing decision — classification and extraction only."""

    task_type: Literal["sentiment", "pick_trade", "portfolio_task"]
    sentiment_action: Optional[Literal["analyze", "refresh_news"]] = Field(
        None, description="Only for task_type=sentiment. 'refresh_news' for "
        "loading/updating/refreshing news into the RAG; 'analyze' otherwise.")
    asset_class: Optional[str] = Field(
        None, description="analyze only; default 'US equity indices'")
    horizon: Optional[str] = Field(
        None, description="analyze only, e.g. '2 weeks'; default '1 month'")
    as_of: Optional[str] = Field(
        None, description="analyze only: ISO date YYYY-MM-DD when the user "
        "asks about sentiment at a past point in time")
    question: Optional[str] = Field(
        None, description="analyze only: the user's freeform question, "
        "included whenever the query is a question rather than a plain "
        "request for an outlook")
    trade_objectives: Optional[dict] = Field(
        None, description="pick_trade only: ONLY the screening keys the user "
        "explicitly changed, omitting everything left at defaults")
    portfolio_action: Optional[
        Literal["add_trade", "exit_trade", "analytics"]] = Field(
        None, description="Only for task_type=portfolio_task")


def _prompt(user_query: str) -> str:
    today = date.today()
    defaults = json.dumps(DEFAULT_TRADE_OBJECTIVES, indent=2)
    return f"""Route this query for a US index options paper-trading assistant.
Today is {today.isoformat()} ({today.strftime('%A')}).

Task types:
- sentiment: market sentiment questions/outlooks (action "analyze"), or
  loading/refreshing news into the RAG (action "refresh_news").
  For analyze: extract asset_class, horizon, an as_of ISO date if the user
  references a past point in time (resolve relative dates like "last Friday"
  using today's date above), and pass the user's question through when the
  query is a question.
- pick_trade: screening/finding/recommending trades. The standing screen
  defaults are:
{defaults}
  Emit trade_objectives with ONLY the keys the user explicitly changed
  (e.g. "call spreads only" -> {{"structures": ["call spread"]}};
  "at least 4% away" -> {{"min_strike_distance_pct": 4.0}};
  "top 5" -> {{"top_n": 5}}). Omit the field entirely if nothing changed.
- portfolio_task: the paper portfolio — adding a trade (add_trade), exiting/
  closing a trade (exit_trade), or viewing positions/P&L/greeks/analytics
  (analytics).

Query: {user_query}"""


def route(user_query: str) -> dict:
    """Parse the query into state updates. Raises on missing key or parse failure."""
    api_key = env_var("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("lead_router needs ANTHROPIC_API_KEY in .env")
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model=MODEL, max_tokens=1000, api_key=api_key)
    decision: RouteDecision = llm.with_structured_output(
        RouteDecision).invoke(_prompt(user_query))

    updates: dict = {"task_type": decision.task_type}
    if decision.task_type == "sentiment":
        updates["sentiment_action"] = decision.sentiment_action or "analyze"
        if updates["sentiment_action"] == "analyze":
            req: dict = {
                "asset_class": decision.asset_class or "US equity indices",
                "horizon": decision.horizon or "1 month",
            }
            if decision.as_of:
                req["as_of"] = decision.as_of
            if decision.question:
                req["question"] = decision.question
            updates["sentiment_request"] = req
    elif decision.task_type == "pick_trade":
        if decision.trade_objectives:
            updates["trade_objectives"] = decision.trade_objectives
    else:  # portfolio_task
        updates["portfolio_action"] = decision.portfolio_action or "analytics"
        updates["portfolio_instruction"] = user_query
    return updates


def run(state: PortfolioState) -> dict:
    return route(state["user_query"])


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or "show me my portfolio"
    print(json.dumps(route(query), indent=2))
