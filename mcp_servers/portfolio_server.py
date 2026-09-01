"""MCP server: the paper portfolio.

Exposes the portfolio manager's functions to any MCP client. All guardrails
travel with the functions (duplicate rejection, 80-pair limit,
picker-vetted-only for add_trade) — the MCP layer adds access, never
bypasses checks. Nothing here touches a real brokerage.

Run:      python -m mcp_servers.portfolio_server   (stdio transport)
Register: see .mcp.json in the project root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from agents import portfolio_manager as pm

mcp = FastMCP("spx-portfolio")


@mcp.tool()
def portfolio_analytics() -> dict:
    """Current positions, marks, unrealized/realized P&L, and portfolio
    delta ($/pt) and theta ($/day)."""
    return dict(pm.analytics())


@mcp.tool()
def portfolio_pnl_history(start: str | None = None) -> list[dict]:
    """Daily portfolio P&L series ($, EOD marks) from `start`
    (ISO YYYY-MM-DD; default: first trade's open date) to today."""
    return pm.pnl_history(start)


@mcp.tool()
def add_trade(trade_id: str, open_date: str | None = None,
              note: str = "") -> dict:
    """Open a picker-vetted paper trade by trade_id (must exist in
    data/recommendations/). open_date prices legs at that day's EOD."""
    return dict(pm.add_trade(trade_id, open_date, note))


@mcp.tool()
def add_trade_manual(expiry: str, right: str, short_strike: float,
                     long_strike: float, quantity: int = 20,
                     open_date: str | None = None, note: str = "") -> dict:
    """Human-override add with explicit legs (marked not picker-vetted).
    right is "C" or "P"; duplicate and pair-limit guardrails still apply."""
    return dict(pm.add_trade_manual(expiry, right, short_strike, long_strike,
                                    quantity, open_date, note))


@mcp.tool()
def exit_trade(trade_id: str, close_date: str | None = None) -> dict:
    """Close all OPEN legs of a trade at current (or a past date's EOD)
    mid prices; reports realized P&L."""
    return dict(pm.exit_trade(trade_id, close_date))


if __name__ == "__main__":
    mcp.run()
