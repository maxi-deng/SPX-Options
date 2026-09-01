"""MCP server: SPX market data (ORATS, delayed).

Exposes the same market-data functions the agents use in-process, to any
MCP client (Claude Code, other agents, other tools).

Run:      python -m mcp_servers.market_data_server   (stdio transport)
Register: see .mcp.json in the project root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from mcp_servers.orats_client import get_hist_strikes, get_strikes

CHAIN_FIELDS = [
    "expirDate", "dte", "strike", "stockPrice",
    "callBidPrice", "callAskPrice", "putBidPrice", "putAskPrice",
    "callOpenInterest", "putOpenInterest", "delta", "theta",
]

mcp = FastMCP("spx-market-data")


@mcp.tool()
def get_chain(dte_min: int = 1, dte_max: int = 92) -> list[dict]:
    """Current SPX options chain (delayed ~15 min). One row per strike/expiry
    with call/put quotes, open interest, and greeks (delta is the call
    delta; put delta = delta - 1; theta is points/day)."""
    return get_strikes("SPX", dte=(dte_min, dte_max), fields=CHAIN_FIELDS)


@mcp.tool()
def get_hist_chain(trade_date: str, dte_min: int = 1,
                   dte_max: int = 92) -> list[dict]:
    """SPX options chain END-OF-DAY snapshot for a past trade date
    (ISO YYYY-MM-DD). Same row shape as get_chain."""
    return get_hist_strikes(trade_date, "SPX", dte=(dte_min, dte_max),
                            fields=CHAIN_FIELDS)


if __name__ == "__main__":
    mcp.run()
