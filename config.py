"""Central configuration: model choice, file locations, env access."""

import os
from pathlib import Path

# One model everywhere to start; can be split per-agent later (e.g. a cheaper
# model for the answer generator) once the system works end to end.
# LangChain:  ChatAnthropic(model=MODEL)
# CrewAI:     LLM(model=f"anthropic/{MODEL}")   (litellm naming)
MODEL = "claude-opus-5"

PROJECT_ROOT = Path(__file__).parent
PORTFOLIO_DIR = PROJECT_ROOT / "data" / "portfolio"
TRADE_LOG = PORTFOLIO_DIR / "trade_log.xlsx"
RECOMMENDATIONS_DIR = PROJECT_ROOT / "data" / "recommendations"
RAG_CORPUS_DIR = PROJECT_ROOT / "rag" / "corpus"
NEWS_DIR = RAG_CORPUS_DIR / "news"          # daily Google News pulls (JSONL)
FAISS_INDEX_DIR = PROJECT_ROOT / "rag" / "index"

# Key market event topics — each becomes a Google News RSS *search* feed
# (keyword-matched), pulled daily and tagged on every article. These
# guarantee coverage of themes we specifically care about, but a search feed
# only surfaces articles matching its keywords — e.g. a one-off "ADP private
# payrolls" release won't match any of these. Edit freely; re-run
# `python -m rag.update` after changes.
NEWS_TOPICS = [
    "Federal Reserve",
    "US inflation",
    "Trump economy",
    "Anthropic IPO",
    "US stock market",
    "SPX volatility",
]

# Google's own algorithmically-curated Business section feed — not a keyword
# search, so it catches whatever's actually moving US financial news day to
# day (earnings, econ data releases, etc.) that the narrow topics above miss.
GENERAL_NEWS_TOPIC = "US financial news"
GENERAL_NEWS_FEED = ("https://news.google.com/rss/headlines/section/topic/"
                      "BUSINESS?hl=en-US&gl=US&ceid=US:en")

# fastembed model used for RAG embeddings (ONNX, local, no API key)
EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def env_var(name: str) -> str | None:
    """Read a secret from the environment, falling back to the project .env."""
    val = os.environ.get(name)
    if val:
        return val
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or None
    return None

# ORATS Data API — market data for the trade picker.
# Token lives in .env as ORATS_API_TOKEN; never hardcode it.
# Delayed endpoints only (~15-min delay); /live/* returns 403 on our tier.
ORATS_BASE_URL = "https://api.orats.io/datav2"

# Portfolio risk limit: total OPEN contract pairs across all trades may not
# exceed this. add_trade rejects anything that would breach it.
MAX_PORTFOLIO_PAIRS = 80

# The user's standing screening rules (2026-08-28). A query can override any
# of these; the router parses overrides into state["trade_objectives"].
DEFAULT_TRADE_OBJECTIVES = {
    "structures": ["call spread", "put spread"],  # credit spreads, both sides
    "strike_width": 100.0,          # exact distance between the two legs
    "contracts": 10,                # per trade
    "min_leg_open_interest": 500,   # each leg must EXCEED this
    "min_premium_per_month": 5.0,   # upfront credit received per PAIR at open,
                                    # in quoted price terms ("dollars" = SPX
                                    # points, i.e. $500 cash per pair), per
                                    # 30.44-day month; must EXCEED this
    "min_days_to_expiry": 14,       # 2 weeks — filters out super short-dated
    "max_months_to_expiry": 3,
    "min_strike_distance_pct": 3.0, # BOTH legs' strikes >= 3% away from spot
                                    # (OTM direction), e.g. calls above spot*1.03
    "optimize": "theta_delta_ratio",
    "top_n": 10,
}
