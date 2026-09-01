"""Protocol-level test of the MCP servers: spawn each over stdio, list its
tools, and make one real call.

Run from the project root:  python -m tests.test_mcp_servers
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def check(script: str, call: str, args: dict) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "mcp_servers" / script)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"{script}: tools = {names}")
            result = await session.call_tool(call, args)
            text = result.content[0].text
            print(f"  {call} -> {text[:180]}{'...' if len(text) > 180 else ''}")
            assert not result.isError, f"{call} returned an error"


async def main() -> None:
    await check("portfolio_server.py", "portfolio_analytics", {})
    await check("market_data_server.py", "get_chain",
                {"dte_min": 1, "dte_max": 3})
    print("MCP servers OK")


if __name__ == "__main__":
    asyncio.run(main())
