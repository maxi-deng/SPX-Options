"""Minimal ORATS datav2 client (stdlib only, no dependencies).

Will later be exposed through the market_data MCP server; for now the trade
picker, grader, and portfolio manager import it directly. Delayed endpoints
only — /live/* returns 403 on our token tier. Schema verified live on
2026-08-28: strikes rows carry call* and put* quote fields and shared greeks
(delta is the CALL delta; put delta = delta - 1; theta is points/day,
negative for long options). /hist/strikes returns the same shape for a past
tradeDate (end-of-day snapshot).
"""

import json
import os
import urllib.parse
import urllib.request

from config import ORATS_BASE_URL, PROJECT_ROOT


def _token() -> str:
    tok = os.environ.get("ORATS_API_TOKEN")
    if not tok:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ORATS_API_TOKEN="):
                    tok = line.split("=", 1)[1].strip()
                    break
    if not tok:
        raise RuntimeError("ORATS_API_TOKEN not set (environment or .env)")
    return tok


def _get(endpoint: str, params: dict[str, str]) -> list[dict]:
    params = {"token": _token(), **params}
    url = f"{ORATS_BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=90) as resp:
        payload = json.load(resp)
    return payload["data"]


def _chain_params(
    ticker: str, dte: tuple[int, int] | None, fields: list[str] | None
) -> dict[str, str]:
    params: dict[str, str] = {"ticker": ticker}
    if dte:
        params["dte"] = f"{dte[0]},{dte[1]}"
    if fields:
        params["fields"] = ",".join(fields)
    return params


def get_strikes(
    ticker: str = "SPX",
    dte: tuple[int, int] | None = None,
    fields: list[str] | None = None,
) -> list[dict]:
    """Current (delayed ~15 min) chain. dte=(lo, hi) filters server-side."""
    return _get("strikes", _chain_params(ticker, dte, fields))


def get_hist_strikes(
    trade_date: str,
    ticker: str = "SPX",
    dte: tuple[int, int] | None = None,
    fields: list[str] | None = None,
) -> list[dict]:
    """End-of-day chain for a past trade date (ISO 'YYYY-MM-DD')."""
    params = _chain_params(ticker, dte, fields)
    params["tradeDate"] = trade_date
    return _get("hist/strikes", params)
