# Trade Log Schema (`trade_log.xlsx`)

Long-term memory for the portfolio manager. One row per **leg**; legs of the
same structure share a `trade_id`. All trades are paper trades.

| Column | Type | Notes |
|---|---|---|
| trade_id | str | e.g. `SPX-CS-20260918-001`; groups legs of one structure |
| structure | str | e.g. `call spread` |
| underlying | str | e.g. `SPX` |
| expiry | date | option expiry |
| strike | float | |
| right | str | `C` / `P` |
| action | str | `BUY` / `SELL` |
| quantity | int | contracts |
| open_date | date | |
| open_price | float | per-contract premium at open |
| close_date | date | blank while open |
| close_price | float | blank while open |
| status | str | `OPEN` / `CLOSED` / `EXPIRED` |
| notes | str | free text (e.g. grader feedback snapshot, thesis) |

Daily P&L / analytics are computed from this log plus market data; a second
sheet `marks` (date, trade_id, mark_price) can be added later if we want to
store daily marks instead of re-fetching history.
