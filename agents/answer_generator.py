"""Final answer generator agent.

Renders whatever the flow produced into the final answer shown to the user.

Design rule: ALL numbers are rendered deterministically (tables, verdicts,
portfolio summaries are assembled by code, never paraphrased) — this agent
must not reintroduce the hallucination risk the grader exists to kill. The
LLM contributes only a short conversational lead paragraph for pick_trade
runs, and is explicitly forbidden from stating numbers not present in the
rendered output. If the LLM is unavailable or fails, the lead is simply
omitted — the deterministic answer stands alone.

Reads:  whatever is present on the state for this run
        (sentiment_analysis / news_refresh_summary /
         trade_recommendations + grader_verdict / portfolio_result)
Writes: final_answer

Standalone preview of the wired pick_trade flow (picker -> grader -> answer):
    python -m agents.answer_generator
"""

from agents.trade_picker import table_str
from config import MODEL, env_var
from graph.state import PortfolioState


def _render_picks(state: PortfolioState) -> str:
    recs = state.get("trade_recommendations", [])
    verdict = state.get("grader_verdict")
    parts = []
    if recs:
        parts.append(f"Trade recommendations ({len(recs)} passed the screen):")
        parts.append(table_str(recs))
    else:
        parts.append("No trades passed the screen with the current objectives.")
    if verdict is not None:
        status = "PASSED" if verdict["passed"] else "FAILED"
        parts.append(f"Grader verdict: {status} "
                     f"({len(verdict['checks'])} checks). {verdict['feedback']}")
    if recs:
        parts.append('Nothing is executed automatically — to record one in the '
                     'paper portfolio, say e.g. "add trade '
                     f"{recs[0]['trade_id']}\".")
    return "\n\n".join(parts)


def _llm_lead(rendered: str) -> str | None:
    """2-3 sentence conversational lead; numbers only by exact quotation."""
    api_key = env_var("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    instruction = (
        "Write a 2-3 sentence lead for the trading-desk answer below: "
        "how the screen went, the standout trade and why, and the grader "
        "outcome. Every number you mention must be quoted exactly as it "
        "appears below — do not compute, round, or invent any number. "
        "Plain prose, no headers. Finish your sentences cleanly.\n\n"
        + rendered)
    try:
        try:  # CrewAI persona (see agents/crew_roles.py)
            from agents.crew_roles import desk_writer, run_task
            return run_task(desk_writer(), instruction,
                            "A 2-3 sentence plain-prose lead.") or None
        except ImportError:  # CrewAI unavailable — direct LangChain path
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(model=MODEL, max_tokens=600, api_key=api_key)
            resp = llm.invoke(instruction)
            content = resp.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") for b in content
                                  if isinstance(b, dict))
            return content.strip() or None
    except Exception:
        return None  # deterministic answer stands alone


def run(state: PortfolioState) -> dict:
    parts: list[str] = []
    if state.get("news_refresh_summary"):
        parts.append(f"News RAG refreshed: {state['news_refresh_summary']}")
    if state.get("sentiment_analysis"):
        parts.append(state["sentiment_analysis"])
    if state.get("trade_recommendations") is not None \
            or state.get("grader_verdict") is not None:
        rendered = _render_picks(state)
        lead = _llm_lead(rendered) if state.get("trade_recommendations") else None
        parts.append(f"{lead}\n\n{rendered}" if lead else rendered)
    if state.get("portfolio_result"):
        parts.append(state["portfolio_result"]["summary"])
    answer = "\n\n".join(parts) if parts else \
        "Nothing to report — no agent produced output for this query."
    return {"final_answer": answer}


if __name__ == "__main__":
    # live preview of the wired pick_trade flow: picker -> grader -> answer
    from agents.trade_grader import grade_trades
    from agents.trade_picker import pick_trades

    recs = pick_trades()
    verdict = grade_trades(recs)
    state: PortfolioState = {"trade_recommendations": recs,
                             "grader_verdict": verdict}
    print(run(state)["final_answer"])
