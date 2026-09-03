# SPX Options — Multi-Agent Paper Portfolio Manager

Six-agent LangGraph system for screening SPX credit spreads, verifying them,
analyzing news sentiment, and managing a paper portfolio. Design doc and
decision record: `README.md`. Safety rules: `Safety and Intervention Plan.txt`.

## Runtime — ALWAYS use the venv

All Python runs through `.venv\Scripts\python.exe` (Python 3.12 — CrewAI does
not support the system Python 3.14). Never use the system `python` here.

## Commands

- `ask "..."` — the whole system: router parses English, graph runs the right
  agents. Examples: `ask "load news to rag"`, `ask "find me call spreads at
  least 4% away"`, `ask "add trade SPX-PS-..."`, `ask "show my P&L"`,
  `ask "empty the portfolio"` (closes all at market).
- `.venv\Scripts\python.exe -m agents.portfolio_manager history --plot` —
  daily P&L chart (auto-opens).
- `.venv\Scripts\python.exe -m agents.portfolio_manager reset --confirm` —
  archive trade log + fresh start. DESTRUCTIVE GATE: never run without the
  user explicitly asking; deliberately unreachable from `ask`.
- `.venv\Scripts\python.exe -m rag.update` — same as "load news to rag".
- Tests: `-m tests.test_grader_catches_lies`, `-m tests.test_mcp_servers`.

MCP servers (`.mcp.json`): `spx-portfolio` (analytics, P&L history, add /
add-manual / exit — all guardrails travel with the functions) and
`spx-market-data` (current + historical ORATS chains).

## Hard rules (user's design decisions — do not violate)

- The USER makes every trading decision. Never add/exit trades unprompted;
  never wire picked trades into the portfolio automatically.
- The trade log (`data/portfolio/trade_log.xlsx`) is an append/close audit
  trail. Never edit or delete rows; never rewrite history. "Start fresh" =
  `reset --confirm` (archives, never deletes).
- Never purge `rag/corpus/news/` — it is the archive powering as-of-date
  sentiment. The FAISS index (`rag/index/`) is rebuildable and gitignored.
- Secrets live in `.env` (gitignored): ANTHROPIC_API_KEY, ORATS_API_TOKEN.
  Never hardcode or commit them.
- The grader stays LLM-free; the answer generator renders all numbers
  deterministically (LLM lead may only quote them exactly).

## Data conventions (ORATS)

- Root is `SPX`, never `SPXW` (ORATS folded weeklies under SPX in 2022).
- Delayed endpoints only (~15 min); `/live/*` returns 403 on this token.
- Chain rows: `delta` is the CALL delta (put delta = delta − 1); `theta` is
  points/day, negative for long options. SPX multiplier: $100/point.
- Standing screen rules + 80-pair portfolio limit: `config.py`
  (`DEFAULT_TRADE_OBJECTIVES`, `MAX_PORTFOLIO_PAIRS`).

## Git

Repo: https://github.com/maxi-deng/SPX-Options (public). Commit at natural
milestones (new trades, new features) when the user asks.
