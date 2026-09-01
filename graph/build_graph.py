"""LangGraph wiring: nodes and edges.

Flow per task_type (chosen by the lead router):

    sentiment       router -> sentiment_retriever -> answer_generator
    pick_trade      router -> trade_picker -> trade_grader -> answer_generator
    portfolio_task  router -> portfolio_manager -> answer_generator

"load news to rag" rides the sentiment flow: the router sets
sentiment_action="refresh_news" and the sentiment retriever does the
fetch + FAISS rebuild itself — it owns the news corpus.

There is deliberately no pick->add path in the graph: the user manually
reviews graded recommendations, then sends a separate portfolio_task query to
add (or exit) a trade. The pick->add dependency is carried by the trade
picker persisting recommendations to data/recommendations/, not by a graph
edge. Communication is one-way through PortfolioState; no feedback loops.
"""

from langgraph.graph import END, StateGraph

from agents import (
    answer_generator,
    lead_router,
    portfolio_manager,
    sentiment_retriever,
    trade_grader,
    trade_picker,
)
from graph.state import PortfolioState


def _route_from_router(state: PortfolioState) -> str:
    return {
        "sentiment": "sentiment_retriever",
        "pick_trade": "trade_picker",
        "portfolio_task": "portfolio_manager",
    }[state["task_type"]]


def build_graph():
    g = StateGraph(PortfolioState)

    g.add_node("lead_router", lead_router.run)
    g.add_node("sentiment_retriever", sentiment_retriever.run)
    g.add_node("trade_picker", trade_picker.run)
    g.add_node("trade_grader", trade_grader.run)
    g.add_node("portfolio_manager", portfolio_manager.run)
    g.add_node("answer_generator", answer_generator.run)

    g.set_entry_point("lead_router")
    g.add_conditional_edges("lead_router", _route_from_router)
    g.add_edge("sentiment_retriever", "answer_generator")
    g.add_edge("trade_picker", "trade_grader")
    g.add_edge("trade_grader", "answer_generator")
    g.add_edge("portfolio_manager", "answer_generator")
    g.add_edge("answer_generator", END)

    return g.compile()
