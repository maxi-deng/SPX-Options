# MCP Servers

Tool layer for the agents.

## `market_data/` — ORATS Data API (trade picker)

The user has an ORATS monthly data subscription. Token: view at
`dashboard.orats.com/api-console`, store in `.env` as `ORATS_API_TOKEN` —
never hardcode it (it rides as a URL query parameter on every call).

Verified facts from the 2026-08-04 "Verify ORATS API" session:

- Base: `https://api.orats.io/datav2/{endpoint}?token=...`
- Full chain: `GET /strikes?token=...&ticker=SPX` — optional `fields=` to
  slim the payload (e.g. `fields=tradeDate,expirDate,strike,stockPrice`).
- Single contract: `GET /strikes/options?token=...&tickers=<OCC>` where the
  OCC symbol = root + YYMMDD + C/P + strike×1000 zero-padded to 8 digits,
  e.g. `SPX260821C07800000` (SPX call, 2026-08-21, 7800 strike).
- **Use root `SPX`, never `SPXW`** — ORATS retired the SPXW ticker in 2022;
  SPX weeklies live under plain `SPX`.
- **Delayed endpoints only.** `/live/...` returns 403 on this token tier
  (live requires a separate signed agreement). Delayed data is same-day with
  ~15-min delay — sufficient for this project.
- Per-strike fields include: bid/ask/mid, bid/mid/ask IV, SMV smoothed vol,
  delta/gamma/theta/vega/rho (**theta is $/day**), volume, open interest,
  bid/ask sizes, quote timestamp.
- Append `.csv` to an endpoint name for CSV instead of JSON.

## `portfolio_files/` — trade log + recommendations (portfolio manager)

Read/write access to `data/portfolio/trade_log.xlsx`, and read access to
`data/recommendations/` for `add_trade` lookups by trade_id.
