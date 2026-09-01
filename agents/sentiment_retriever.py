"""Market sentiment retriever agent.

Owns the news RAG corpus (rag/). Two functions, selected by the router via
state["sentiment_action"] — mirroring how add/exit/analytics live inside the
portfolio manager:

  analyze       Query the FAISS news index and produce a high-level market
                sentiment analysis for a user-defined horizon and asset
                class. With ANTHROPIC_API_KEY set, the retrieved headlines
                are synthesized into written analysis by the LLM (grounded:
                the model only sees retrieved, dated, source-attributed
                headlines); without a key — or if the LLM call fails — it
                falls back to the topic-grouped retrieval digest.
                Deliberately unstructured prose — no grader, by design.
  refresh_news  Daily corpus maintenance ("load news to rag"): fetch every
                config.NEWS_TOPICS Google News feed, rebuild the FAISS
                index. Same work as `python -m rag.update`.

Reads:  sentiment_action, sentiment_request (horizon, asset_class)
Writes: sentiment_analysis (analyze), news_refresh_summary (refresh_news)

Standalone:
    python -m agents.sentiment_retriever "US equity indices" "1 month"
    python -m agents.sentiment_retriever refresh
"""

from collections import defaultdict

from config import MODEL, env_var
from graph.state import PortfolioState
from rag.retrieve import retrieve


def _digest(hits: list[dict], asset_class: str, horizon: str,
            as_of: str | None = None) -> str:
    """Fallback output: the retrieved headlines, grouped by topic."""
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        by_topic[h["topic"]].append(h)
    stamp = f" as of {as_of}" if as_of else ""
    lines = [f"Market sentiment inputs — {asset_class}, horizon {horizon}{stamp} "
             f"(top {len(hits)} recent headlines from the news index):", ""]
    for topic, arts in by_topic.items():
        lines.append(f"## {topic}")
        for a in arts:
            lines.append(f"- ({a['published']}, {a['source']}) {a['title']}")
        lines.append("")
    return "\n".join(lines)


def _headline_block(hits: list[dict]) -> str:
    return "\n".join(f"- [{h['topic']}] ({h['published']}, {h['source']}) "
                     f"{h['title']}" for h in hits)


def _synthesize(hits: list[dict], asset_class: str, horizon: str,
                api_key: str, as_of: str | None = None,
                question: str | None = None) -> str:
    vantage = (f"Write from the vantage point of {as_of} — treat that date "
               "as 'today'; you know nothing published after it. "
               if as_of else "")
    if question:
        task = (f"The user asks: \"{question}\"\n"
                "Answer the question directly, grounded ONLY in the recent "
                "news headlines below (retrieved from a curated index; dates "
                "and sources shown). Give your sentiment read, how it bears "
                "on the user's question, the main risks, and what to watch. ")
    else:
        task = (f"Write a high-level market sentiment analysis for "
                f"{asset_class} over a {horizon} horizon, based ONLY on the "
                "recent news headlines below (retrieved from a curated "
                "index; dates and sources shown). Cover: overall sentiment "
                "stance, the key drivers, main risks to that view, and what "
                "to watch next. ")
    prompt = (
        "You are a market analyst supporting an SPX options premium seller. "
        f"{vantage}{task}"
        "Where a point rests on a specific headline, name the source and "
        "date inline. Note explicitly if the headlines conflict or are too "
        "thin to support a confident view — do not invent facts beyond "
        "them. You provide sentiment input only — the user makes all "
        "trading decisions. "
        "Keep it to roughly 400-600 words and make sure to finish cleanly.\n\n"
        f"Headlines:\n{_headline_block(hits)}"
    )
    try:  # CrewAI persona (see agents/crew_roles.py)
        from agents.crew_roles import run_task, sentiment_analyst
        return run_task(sentiment_analyst(), prompt,
                        "A grounded market sentiment analysis in plain prose "
                        "with inline source/date citations.")
    except ImportError:  # CrewAI unavailable — direct LangChain path
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model=MODEL, max_tokens=4000, api_key=api_key)
        resp = llm.invoke(prompt)
        content = resp.content
        if isinstance(content, list):  # content-block form
            content = "".join(b.get("text", "") for b in content
                              if isinstance(b, dict))
        return content.strip()


def analyze(asset_class: str = "US equity indices",
            horizon: str = "1 month", as_of: str | None = None,
            question: str | None = None) -> str:
    query = question or f"{asset_class} outlook sentiment {horizon} market impact"
    hits = retrieve(query, k=15, days=7, as_of=as_of)
    if not hits:
        return ("No relevant news in the RAG index for that window"
                + (f" (as of {as_of})" if as_of else "")
                + " — the corpus only reaches back to its first fetch "
                  "(2026-08-28); run `python -m rag.update` to refresh.")

    api_key = env_var("ANTHROPIC_API_KEY")
    if not api_key:
        return (_digest(hits, asset_class, horizon, as_of)
                + "\nNote: LLM synthesis of these inputs activates once "
                  "ANTHROPIC_API_KEY is configured.")
    try:
        analysis = _synthesize(hits, asset_class, horizon, api_key, as_of,
                               question)
    except Exception as exc:  # fail visible, fall back to grounded digest
        return (_digest(hits, asset_class, horizon, as_of)
                + f"\nNote: LLM synthesis failed ({exc}); showing the "
                  f"retrieval digest instead.")
    return (analysis + "\n\n--- Headlines used ---\n" + _headline_block(hits))


def refresh_news() -> str:
    """Fetch today's news for every topic and rebuild the FAISS index."""
    from rag.ingest import build_index
    from rag.news_fetcher import fetch_all

    added, total = fetch_all()
    n = build_index()
    return (f"Fetched {total} articles ({added} new); "
            f"FAISS index rebuilt with {n} articles.")


def run(state: PortfolioState) -> dict:
    if state.get("sentiment_action") == "refresh_news":
        return {"news_refresh_summary": refresh_news()}
    req = state.get("sentiment_request", {})
    return {"sentiment_analysis": analyze(
        req.get("asset_class", "US equity indices"),
        req.get("horizon", "1 month"),
        req.get("as_of"),
        req.get("question"))}


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0].lower() == "refresh":
        print(refresh_news())
    elif args and args[0].lower() == "ask":
        # python -m agents.sentiment_retriever ask "question" [as_of]
        print(analyze(question=args[1],
                      as_of=args[2] if len(args) > 2 else None))
    else:
        print(analyze(args[0] if args else "US equity indices",
                      args[1] if len(args) > 1 else "1 month",
                      args[2] if len(args) > 2 else None))
