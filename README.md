# Capstone Project — US Index Options Portfolio Manager (Multi-Agent System)

A multi-agent system that helps decide what US index options to trade and manages a **paper portfolio** to track performance. The human user always makes the final call on actual trading — the system recommends, grades, and tracks; it does not auto-execute.

**Project root:** `C:\Users\dengm\Capstone Project` — all code and relevant files live here.

## Why Multi-Agent

- The main components are dramatically different from each other; modularization improves accuracy and efficiency.
- The scope will keep expanding, so a modular design lets one agent's rules change without touching the others (e.g., swap only the trade picker's rules; everything else stays intact).
- Deliberately a **small** number of agents: each one exists for a non-negotiable reason with a clear, distinct objective. Agent count is kept low because large agent fleets are hard to manage.

## Tech Stack

- **LangChain / LangGraph** — agent orchestration; agents communicate via **LangGraph state**.
- **CrewAI** — defines WHO the LLM agents are and executes their LLM work (`agents/crew_roles.py`: the Market Sentiment Analyst and Trading Desk Answer Writer personas run as single-agent crews). LangGraph owns the graph/routing/state; the router uses LangChain structured output (typed schema, not a persona); deterministic agents have no persona. Requires the Python 3.12 venv (CrewAI does not support 3.14 yet); direct LangChain fallback paths keep the system running without it.
- **MCP (Model Context Protocol)** — tool/data access (e.g., market data, portfolio Excel files as MCP servers).
- **ORATS Data API** — market data source for the trade picker (SPX chains, greeks; delayed `datav2` endpoints). Verified details in `mcp_servers/README.md`; token in `.env` as `ORATS_API_TOKEN`.
- **RAG** — backs the market sentiment agent.
- Long-term memory: **Excel files** storing all trade open/close records.

## Running the System

Everything runs through the **Python 3.12 venv** at `.venv` (CrewAI doesn't
support the system Python 3.14 yet). The whole interface is one command:

    ask "load news to rag"
    ask "what is the market sentiment for the next 2 weeks?"
    ask "find me call spreads at least 4% away, top 5"
    ask "add trade SPX-PS-20260918-7250x7150"
    ask "show me my portfolio P&L and greeks"

(`ask.bat` = `.venv\Scripts\python.exe main.py "<query>"` — router parses the
English, the graph runs the right agents, the answer generator replies.)
Standalone agent CLIs still work via the venv python, e.g. the P&L chart:
`.venv\Scripts\python.exe -m agents.portfolio_manager history --plot`.

**MCP**: `.mcp.json` registers two servers for any MCP client (e.g. Claude
Code opened in this folder): `spx-market-data` (current + historical ORATS
chains) and `spx-portfolio` (analytics, P&L history, add/add-manual/exit —
all guardrails travel with the functions).

## The Six Agents

| # | Agent | Role | Grader? |
|---|-------|------|---------|
| 1 | **Lead / Router** | On user query, decides which agent to call next (decision step). | — |
| 2 | **Market Sentiment Retriever** | Owns the news RAG corpus. Two functions selected by the router: `analyze` (queries FAISS to produce market sentiment analysis for user-defined horizons and asset classes; output is high-level, not binary/numeric) and `refresh_news` (daily "load news to rag": fetch Google News topics + rebuild the index). | No — precise output isn't expected, so a grader adds nothing. |
| 3 | **Trade Picker** | Picks the best instruments to implement the user's view. Example query: *"I want to trade a call spread at least 2% away from market with minimum $10/month premium; find me the trades with the best theta/delta ratio."* Returns trade recommendations with supporting numbers. | **Yes** (agent #4). |
| 4 | **Trade Picker Grader** | Verifies the recommendations actually satisfy the stated trade objectives. Needed because trade-picker output is not straightforward enough to eyeball-check. Only agent paired with a grader — the user can quickly grade the others themselves. | — |
| 5 | **Portfolio Manager** | Standalone functions: `add_trade`, `exit_trade`, `analytics`. Has access to long-term memory (Excel files of all trade opens/closes). Shows portfolio analytics at any point in time; adds/exits paper trades only on the user's explicit instruction (user makes the final call). | No |
| 6 | **Final Answer Generator** | Renders the final answer back to the user. | — |

## Coordination Strategy: Hybrid, with a Manual Review Gate

Not strictly sequential (that was the initial blueprint, rejected). Profitable trading requires human intervention, so the same fixed sequence should **not** run every time.

- `pick_trade` and portfolio actions are always **separate queries**. The user manually reviews the graded recommendations; if they like one, they send a new query asking the portfolio manager to add it. Same pattern for exiting a trade.
- The Portfolio Manager is therefore **fully standalone** with three functions selected by the router: `add_trade`, `exit_trade`, `analytics` (e.g., "show me daily profits of my portfolio since inception"). It has no in-graph dependency on the Trade Picker or Grader.
- The pick→add dependency is carried by **persistence, not graph edges**: the Trade Picker saves every recommendation set to `data/recommendations/`, so a later "add trade `<trade_id>`" query can look it up.

## Communication

Agents share information via **LangGraph state**, and the flow is **one-way only** — the grader plus the user's own judgment are considered sufficient feedback; no bidirectional agent-to-agent loops.

## Deliberate Design Exclusions (do not re-add without reason)

- **No graders for agents other than the Trade Picker** — the user can quickly evaluate those outputs directly.
- **No mini-backtester feedback loop inside the grader** — it was prototyped and got messy; the feedback to the trade picker was not straightforward, and since past returns don't predict future returns, the loop doesn't guarantee better outcomes.
- **No query rewriter agent** — queries in this project aren't verbose enough to need one.
- **No automatic pick→add flow** — trades enter or leave the portfolio only via an explicit, separate user instruction after manual review of the recommendations. The graph never routes a picked trade into the portfolio manager.

## Scalability Path (examples of intended expansions)

1. Add Nasdaq (NDX) options alongside SPX → only add a separate trade picker; nothing else changes.
2. Change portfolio risk limits → only touch the Portfolio Manager agent.

## Proposed Repository Layout

```
Capstone Project/
├── README.md              # this file — project design & decisions
├── agents/
│   ├── lead_router.py
│   ├── sentiment_retriever.py
│   ├── trade_picker.py
│   ├── trade_grader.py
│   ├── portfolio_manager.py
│   └── answer_generator.py
├── graph/                 # LangGraph wiring: state schema, edges, dependencies
├── mcp_servers/           # MCP tools (market data, portfolio file access)
├── rag/                   # sentiment corpus ingestion + retrieval
├── data/
│   └── portfolio/         # Excel long-term memory: trade open/close records
└── tests/
```

## Status

- [x] Design finalized (this document)
- [x] LangGraph vs CrewAI division of labor: CrewAI defines agent roles/tasks; LangGraph owns graph, routing, and state; each LangGraph node invokes its CrewAI agent
- [x] Scaffold repo layout
- [x] Draft state schema (`graph/state.py`) — pending user review
- [x] Market data source decided: ORATS Data API (details in `mcp_servers/README.md`)
- [x] Portfolio manager made standalone: `add_trade` / `exit_trade` / `analytics`; manual review gate between pick and add
- [x] Trade picker core (`agents/trade_picker.py`) — screens all 100-wide SPX credit spread pairs; verified live against ORATS 2026-08-28
- [x] Trade picker grader (`agents/trade_grader.py`) — deterministic re-verification against a fresh ORATS fetch; negative test in `tests/test_grader_catches_lies.py`
- [x] Portfolio manager + Excel memory (`agents/portfolio_manager.py`) — add/exit/analytics live; first paper trade opened 2026-08-27
- [x] Market sentiment RAG pipeline — Google News RSS per key-event topic → FAISS (fastembed local embeddings); digest works
- [x] Sentiment LLM synthesis (`_synthesize` via langchain-anthropic) — grounded prompt + digest fallback; tested live 2026-09-01 with the configured ANTHROPIC_API_KEY
- [x] Final answer generator (`agents/answer_generator.py`) — deterministic number rendering + LLM lead (exact-quote rule); tested live on all flows 2026-09-01
- [x] Lead router (`agents/lead_router.py`) — LLM structured-output parse of plain English into state fields (classify/extract only, never answers); 6/6 test queries routed correctly 2026-09-01
- [x] Graph wiring COMPLETE (2026-09-01): LangGraph graph live end-to-end (`main.py` / `ask.bat`); all four flows tested through the full graph; CrewAI personas executing (crewai 1.15.18, py3.12 venv); MCP servers protocol-tested + registered in `.mcp.json`
- Deferred (optional): confirmation-echo interrupt for ambiguous router parses; P&L chart via graph (CLI-only today)
