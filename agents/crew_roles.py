"""CrewAI role definitions for the LLM-powered personas.

Division of labor (decided at design time):
  - CrewAI defines WHO the LLM agents are (role / goal / backstory) and
    executes their LLM work as single-agent crews.
  - LangGraph owns the graph, routing, and shared state.
  - Deterministic agents (trade picker, grader, portfolio manager) have no
    LLM persona — their charter lives in their module docstrings.
  - The lead router stays on LangChain structured output (classification
    needs a typed schema, not a persona).

Executed personas:
  sentiment_analyst  -> used by agents/sentiment_retriever._synthesize
  desk_writer        -> used by agents/answer_generator._llm_lead

Each caller falls back to a direct LangChain path if CrewAI is unavailable
(e.g. on Python 3.14, which CrewAI does not yet support), so the system
degrades gracefully rather than breaking.
"""

import os

from config import MODEL, env_var


def _ensure_key() -> None:
    # litellm (under CrewAI) reads the key from the environment
    if not os.environ.get("ANTHROPIC_API_KEY"):
        key = env_var("ANTHROPIC_API_KEY")
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key


def _llm(max_tokens: int):
    from crewai import LLM
    _ensure_key()
    return LLM(model=f"anthropic/{MODEL}", max_tokens=max_tokens)


def sentiment_analyst():
    from crewai import Agent
    return Agent(
        role="Market Sentiment Analyst",
        goal=("Produce grounded, honestly-caveated market sentiment analysis "
              "for an SPX options premium seller, using only the retrieved "
              "news headlines provided — never invented facts."),
        backstory=("A buy-side analyst who reads the tape through news flow. "
                   "Cites sources and dates for every claim, says plainly "
                   "when evidence is thin or conflicting, and never makes "
                   "the trading decision — the user does."),
        llm=_llm(4000), verbose=False, allow_delegation=False)


def desk_writer():
    from crewai import Agent
    return Agent(
        role="Trading Desk Answer Writer",
        goal=("Write short, plain-prose leads for the desk's answers. Every "
              "number quoted exactly from the material provided — never "
              "computed, rounded, or invented."),
        backstory=("A desk assistant who summarizes screen results crisply "
                   "for a busy options trader and always finishes sentences "
                   "cleanly."),
        llm=_llm(600), verbose=False, allow_delegation=False)


def run_task(agent, description: str, expected_output: str) -> str:
    """Execute one task with one persona; returns the final output text."""
    from crewai import Crew, Task
    task = Task(description=description, expected_output=expected_output,
                agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    return str(crew.kickoff()).strip()
