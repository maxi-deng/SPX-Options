"""Shared LangGraph state schema for the multi-agent portfolio system.

This is the single contract every agent builds against. Communication is
one-way: each agent fills only its own output fields and never mutates
another agent's output.
"""

from typing import Literal, NotRequired, Optional, TypedDict


class TradeLeg(TypedDict):
    """One leg of an options structure."""

    underlying: str                  # e.g. "SPX"
    expiry: str                      # ISO date, e.g. "2026-09-18"
    strike: float
    right: Literal["C", "P"]
    action: Literal["BUY", "SELL"]
    quantity: int
    price: Optional[float]           # per-contract premium at recommendation time


class TradeRecommendation(TypedDict):
    """A full trade idea produced by the trade picker."""

    trade_id: str                    # e.g. "SPX-CS-20260918-001"
    structure: str                   # e.g. "call spread"
    legs: list[TradeLeg]
    net_premium: float               # net premium for the structure
    premium_per_month: float         # normalized for objectives like "min $10/month"
    metrics: dict[str, Optional[float]]  # e.g. {"theta": ..., "delta": ..., "theta_delta_ratio": ...}; None = not computable
    distance_from_spot_pct: float    # e.g. 2.3 means nearest short strike is 2.3% away
    rationale: str


class GraderVerdict(TypedDict):
    """Trade picker grader output: did the recommendations meet the stated objectives?"""

    passed: bool
    checks: list[dict]               # [{"objective": "...", "passed": bool, "detail": "..."}]
    feedback: str                    # surfaced to the user next to the recommendations


class SentimentRequest(TypedDict):
    """Parsed from the user query by the router before calling the sentiment agent."""

    horizon: str                     # user-defined, e.g. "2 weeks", "3 months"
    asset_class: str                 # e.g. "US equity indices"
    as_of: NotRequired[str]          # "YYYY-MM-DD": sentiment as of a past date,
                                     # ignoring all news published after it
    question: NotRequired[str]       # freeform question to answer from the news
                                     # (drives retrieval and the LLM's task)


class PortfolioResult(TypedDict):
    """Portfolio manager output — analytics or a trade action confirmation."""

    kind: Literal["analytics", "trade_added", "trade_exited"]
    summary: str                     # human-readable headline
    data: dict                       # tables/series serialized for the answer generator


# The task types the lead router can choose (see graph/build_graph.py):
#   sentiment      -> sentiment_retriever -> answer
#   pick_trade     -> trade_picker -> trade_grader -> answer
#   portfolio_task -> portfolio_manager -> answer
#
# "load news to rag" is NOT a separate flow: the router sends it down the
# sentiment flow with sentiment_action="refresh_news". Maintaining the news
# corpus is a function of the sentiment retriever (it owns that store), the
# same way add/exit/analytics are functions of the portfolio manager.
#
# There is deliberately NO combined pick->add flow: the user manually reviews
# pick_trade recommendations, then sends a separate query to add (or exit) a
# trade. The trade picker persists every recommendation set to
# data/recommendations/ so a later add_trade instruction can reference a
# trade_id — persistence, not a graph edge, carries the dependency.
TaskType = Literal["sentiment", "pick_trade", "portfolio_task"]

# Standalone functions inside the sentiment retriever, chosen by the router:
SentimentAction = Literal["analyze", "refresh_news"]

# Standalone functions inside the portfolio manager, chosen by the router.
# (Log reset/archive is deliberately NOT here — it is CLI-only with an
# explicit --confirm flag, so no chat phrasing can ever trigger it.)
PortfolioAction = Literal["add_trade", "exit_trade", "exit_all", "analytics"]


class PortfolioState(TypedDict, total=False):
    # ---- input ----
    user_query: str

    # ---- lead router output ----
    task_type: TaskType
    trade_objectives: dict           # parsed constraints, e.g. {"structure": "call spread",
                                     #   "min_distance_pct": 2.0, "min_premium_per_month": 10.0,
                                     #   "optimize": "theta_delta_ratio"}
    sentiment_action: SentimentAction
    sentiment_request: SentimentRequest
    portfolio_action: PortfolioAction
    portfolio_instruction: str       # e.g. "show daily P&L since inception",
                                     #      "add trade SPX-CS-20260918-001",
                                     #      "exit trade SPX-CS-20260918-001"

    # ---- market sentiment retriever output ----
    sentiment_analysis: str          # analyze action; deliberately unstructured, no grader
    news_refresh_summary: str        # refresh_news action: fetch/index counts

    # ---- trade picker output ----
    trade_recommendations: list[TradeRecommendation]

    # ---- trade grader output ----
    grader_verdict: GraderVerdict

    # ---- portfolio manager output ----
    portfolio_result: PortfolioResult

    # ---- answer generator output ----
    final_answer: str
