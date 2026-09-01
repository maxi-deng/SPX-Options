"""Portfolio manager agent.

Fully standalone — no in-graph dependency on the trade picker or grader.
Three functions, selected by the router via state["portfolio_action"] (or by
CLI subcommand when run standalone):

  add_trade   Record a paper trade in the Excel trade log. The trade_id is
              looked up in data/recommendations/ (newest file first). With an
              explicit open date, legs are priced from ORATS historical
              END-OF-DAY mids for that date; otherwise the recommendation's
              own prices and today's date are used.
              REJECTS (with a reason, writing nothing) when:
                - the trade_id is not in data/recommendations/
                - the trade already has OPEN legs in the log (re-adding a
                  CLOSED trade_id is allowed — that's re-entering)
                - the portfolio's total OPEN contract pairs would exceed
                  config.MAX_PORTFOLIO_PAIRS (80)
  exit_trade  Close all OPEN legs of a trade_id at current (or historical,
              if a date is given) mid prices.
  exit_all    Economic close of EVERY open trade at market prices — the
              portfolio ends flat, all history and realized P&L preserved.

CLI-only (never router-reachable, requires --confirm): `reset` archives the
current trade log to a timestamped file and starts a fresh empty one.
Nothing is ever deleted or rewritten — history moves to an archive.
  analytics   Positions, marks, unrealized/realized P&L, and position greeks
              from the trade log plus a fresh ORATS fetch.

Long-term memory = data/portfolio/trade_log.xlsx, one row per leg (schema in
data/portfolio/README.md). The user always makes the final call; this agent
only records paper trades on instruction.

Standalone usage (from the project root):
    python -m agents.portfolio_manager add SPX-PS-20260930-7450x7350 --date 2026-08-27
    python -m agents.portfolio_manager exit SPX-PS-20260930-7450x7350
    python -m agents.portfolio_manager analytics
"""

import argparse
import json
import os
import re
from datetime import date, datetime

from openpyxl import Workbook, load_workbook

from config import MAX_PORTFOLIO_PAIRS, RECOMMENDATIONS_DIR, TRADE_LOG
from graph.state import PortfolioResult, PortfolioState
from mcp_servers.orats_client import get_hist_strikes, get_strikes

MULTIPLIER = 100  # SPX options are $100 per point

COLUMNS = ["trade_id", "structure", "underlying", "expiry", "strike", "right",
           "action", "quantity", "open_date", "open_price", "close_date",
           "close_price", "status", "notes"]

PRICE_FIELDS = ["expirDate", "strike", "stockPrice",
                "callBidPrice", "callAskPrice", "putBidPrice", "putAskPrice",
                "delta", "theta"]

TRADE_ID_RE = re.compile(r"SPX-[A-Z]{2}-\d{8}-\d+x\d+")


# ---------------------------------------------------------------- Excel log

def _ensure_log() -> None:
    if TRADE_LOG.exists():
        return
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.active.title = "trades"
    wb.active.append(COLUMNS)
    wb.save(TRADE_LOG)


def _load_rows() -> list[dict]:
    _ensure_log()
    ws = load_workbook(TRADE_LOG).active
    rows = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if values[0] is None:
            continue
        rows.append(dict(zip(COLUMNS, values)))
    return rows


def _append_rows(new_rows: list[dict]) -> None:
    _ensure_log()
    wb = load_workbook(TRADE_LOG)
    ws = wb.active
    for r in new_rows:
        ws.append([r.get(c) for c in COLUMNS])
    wb.save(TRADE_LOG)


def _update_rows(trade_id: str, updates: dict) -> int:
    """Apply updates to every OPEN row of trade_id; returns rows touched."""
    wb = load_workbook(TRADE_LOG)
    ws = wb.active
    col = {name: i + 1 for i, name in enumerate(COLUMNS)}
    touched = 0
    for row in ws.iter_rows(min_row=2):
        if (row[col["trade_id"] - 1].value == trade_id
                and row[col["status"] - 1].value == "OPEN"):
            for k, v in updates.items():
                if callable(v):  # per-leg value: f(strike) -> value
                    row[col[k] - 1].value = v(float(row[col["strike"] - 1].value))
                else:
                    row[col[k] - 1].value = v
            touched += 1
    wb.save(TRADE_LOG)
    return touched


# ------------------------------------------------------------- market marks

def _chain_lookup(rows: list[dict]) -> dict[tuple[str, float], dict]:
    return {(r["expirDate"], float(r["strike"])): r for r in rows}


def _mid_for(row: dict, right: str) -> float | None:
    bid = row["callBidPrice"] if right == "C" else row["putBidPrice"]
    ask = row["callAskPrice"] if right == "C" else row["putAskPrice"]
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return None


def _leg_mids(
    legs: list[dict], on_date: str | None
) -> tuple[dict[float, float], float]:
    """Mid price per strike for the legs (one expiry), plus spot.

    on_date=None uses the current delayed chain; else historical EOD.
    """
    expiry = legs[0]["expiry"]
    ref = date.fromisoformat(on_date) if on_date else date.today()
    days_left = (date.fromisoformat(expiry) - ref).days
    window = (max(1, days_left - 3), days_left + 3)
    if on_date:
        chain = get_hist_strikes(on_date, "SPX", dte=window, fields=PRICE_FIELDS)
    else:
        chain = get_strikes("SPX", dte=window, fields=PRICE_FIELDS)
    lookup = _chain_lookup(chain)
    mids: dict[float, float] = {}
    for leg in legs:
        row = lookup.get((expiry, float(leg["strike"])))
        if row is None:
            raise LookupError(f"{expiry} {leg['strike']} not in ORATS chain"
                              f"{' for ' + on_date if on_date else ''}")
        mid = _mid_for(row, leg["right"])
        if mid is None:
            raise LookupError(f"{expiry} {leg['strike']}{leg['right']} has no "
                              f"two-sided quote{' on ' + on_date if on_date else ''}")
        mids[float(leg["strike"])] = round(mid, 2)
    spot = chain[0]["stockPrice"]
    return mids, spot


# ------------------------------------------------------------ the functions

def _find_recommendation(trade_id: str) -> dict | None:
    for f in sorted(RECOMMENDATIONS_DIR.glob("rec_*.json"), reverse=True):
        saved = json.loads(f.read_text(encoding="utf-8"))
        for rec in saved["recommendations"]:
            if rec["trade_id"] == trade_id:
                return rec
    return None


def add_trade(trade_id: str, open_date: str | None = None,
              note: str = "") -> PortfolioResult:
    def reject(reason: str) -> PortfolioResult:
        return {"kind": "trade_added", "summary": f"REJECTED: {reason}",
                "data": {"rejected": True, "reason": reason}}

    rec = _find_recommendation(trade_id)
    if rec is None:
        return reject(f"{trade_id} not found in data/recommendations/ — "
                      f"run the trade picker first.")
    rows = _load_rows()
    if any(r["trade_id"] == trade_id and r["status"] == "OPEN" for r in rows):
        return reject(f"{trade_id} already has OPEN legs in the portfolio — "
                      f"duplicate adds are not allowed.")
    new_pairs = rec["legs"][0]["quantity"]
    open_pairs = sum({r["trade_id"]: r["quantity"] for r in rows
                      if r["status"] == "OPEN"}.values())
    if open_pairs + new_pairs > MAX_PORTFOLIO_PAIRS:
        return reject(
            f"portfolio pair limit: {open_pairs} pairs already open + "
            f"{new_pairs} new = {open_pairs + new_pairs}, which exceeds the "
            f"{MAX_PORTFOLIO_PAIRS}-pair limit. Close something first.")

    when = open_date or date.today().isoformat()
    if open_date:  # reprice legs at that day's EOD mids
        mids, spot = _leg_mids(rec["legs"], open_date)
        prices = {s: mids[s] for s in mids}
    else:
        prices = {float(l["strike"]): l["price"] for l in rec["legs"]}
        spot = None

    short = next(l for l in rec["legs"] if l["action"] == "SELL")
    long_ = next(l for l in rec["legs"] if l["action"] == "BUY")
    credit = prices[float(short["strike"])] - prices[float(long_["strike"])]
    qty = short["quantity"]
    new_rows = []
    for leg in rec["legs"]:
        new_rows.append({
            "trade_id": trade_id, "structure": rec["structure"],
            "underlying": leg["underlying"], "expiry": leg["expiry"],
            "strike": leg["strike"], "right": leg["right"],
            "action": leg["action"], "quantity": leg["quantity"],
            "open_date": when, "open_price": prices[float(leg["strike"])],
            "close_date": None, "close_price": None, "status": "OPEN",
            "notes": note,
        })
    _append_rows(new_rows)
    summary = (
        f"Opened {trade_id}: {rec['structure']}, sell {qty}x "
        f"{int(short['strike'])}{short['right']} @ {prices[float(short['strike'])]:.2f} / "
        f"buy {qty}x {int(long_['strike'])}{long_['right']} @ {prices[float(long_['strike'])]:.2f}, "
        f"open date {when}"
        + (f" (EOD prices, spot {spot:.2f})" if spot else "")
        + f". Credit {credit:.2f} pts = ${credit * MULTIPLIER * qty:,.0f} collected."
    )
    return {"kind": "trade_added", "summary": summary,
            "data": {"rows": new_rows, "credit_pts": round(credit, 2),
                     "credit_usd": round(credit * MULTIPLIER * qty, 0)}}


def add_trade_manual(expiry: str, right: str, short_strike: float,
                     long_strike: float, quantity: int = 20,
                     open_date: str | None = None,
                     note: str = "") -> PortfolioResult:
    """Human-override add: the user specifies the legs directly.

    Exists because the user makes the final call — a trade does NOT have to
    come from the picker. The other guardrails still apply (duplicate check,
    portfolio pair limit), legs are priced from real ORATS data for the open
    date, and the log row is marked as not picker-vetted.
    """
    def reject(reason: str) -> PortfolioResult:
        return {"kind": "trade_added", "summary": f"REJECTED: {reason}",
                "data": {"rejected": True, "reason": reason}}

    code = "CS" if right == "C" else "PS"
    trade_id = (f"SPX-{code}-{expiry.replace('-', '')}-"
                f"{int(short_strike)}x{int(long_strike)}")
    rows = _load_rows()
    if any(r["trade_id"] == trade_id and r["status"] == "OPEN" for r in rows):
        return reject(f"{trade_id} already has OPEN legs in the portfolio — "
                      f"duplicate adds are not allowed.")
    open_pairs = sum({r["trade_id"]: r["quantity"] for r in rows
                      if r["status"] == "OPEN"}.values())
    if open_pairs + quantity > MAX_PORTFOLIO_PAIRS:
        return reject(
            f"portfolio pair limit: {open_pairs} pairs already open + "
            f"{quantity} new = {open_pairs + quantity}, which exceeds the "
            f"{MAX_PORTFOLIO_PAIRS}-pair limit. Close something first.")

    structure = "call spread" if right == "C" else "put spread"
    legs = [
        {"underlying": "SPX", "expiry": expiry, "strike": float(short_strike),
         "right": right, "action": "SELL", "quantity": quantity},
        {"underlying": "SPX", "expiry": expiry, "strike": float(long_strike),
         "right": right, "action": "BUY", "quantity": quantity},
    ]
    when = open_date or date.today().isoformat()
    mids, spot = _leg_mids(legs, open_date)
    credit = mids[float(short_strike)] - mids[float(long_strike)]
    new_rows = []
    for leg in legs:
        new_rows.append({
            "trade_id": trade_id, "structure": structure,
            "underlying": leg["underlying"], "expiry": leg["expiry"],
            "strike": leg["strike"], "right": leg["right"],
            "action": leg["action"], "quantity": leg["quantity"],
            "open_date": when, "open_price": mids[float(leg["strike"])],
            "close_date": None, "close_price": None, "status": "OPEN",
            "notes": f"manual add (user override, not picker-vetted). {note}".strip(),
        })
    _append_rows(new_rows)
    summary = (
        f"Opened {trade_id} (MANUAL, not picker-vetted): {structure}, "
        f"sell {quantity}x {int(short_strike)}{right} @ {mids[float(short_strike)]:.2f} / "
        f"buy {quantity}x {int(long_strike)}{right} @ {mids[float(long_strike)]:.2f}, "
        f"open date {when} (EOD prices, spot {spot:.2f}). "
        f"Credit {credit:.2f} pts = ${credit * MULTIPLIER * quantity:,.0f} collected."
    )
    return {"kind": "trade_added", "summary": summary,
            "data": {"rows": new_rows, "credit_pts": round(credit, 2),
                     "credit_usd": round(credit * MULTIPLIER * quantity, 0)}}


def exit_trade(trade_id: str, close_date: str | None = None) -> PortfolioResult:
    open_legs = [r for r in _load_rows()
                 if r["trade_id"] == trade_id and r["status"] == "OPEN"]
    if not open_legs:
        return {"kind": "trade_exited", "summary":
                f"REJECTED: no OPEN legs for {trade_id}.", "data": {}}
    mids, spot = _leg_mids(open_legs, close_date)
    when = close_date or date.today().isoformat()
    _update_rows(trade_id, {
        "close_date": when,
        "close_price": lambda strike: mids[strike],
        "status": "CLOSED",
    })
    pnl = 0.0
    for leg in open_legs:
        sign = 1 if leg["action"] == "SELL" else -1  # sold: gain when price falls
        pnl += sign * (leg["open_price"] - mids[float(leg["strike"])]) \
            * leg["quantity"] * MULTIPLIER
    summary = (f"Closed {trade_id} on {when} at "
               + " / ".join(f"{int(s)} @ {m:.2f}" for s, m in mids.items())
               + f" (spot {spot:.2f}). Realized P&L ${pnl:,.0f}.")
    return {"kind": "trade_exited", "summary": summary,
            "data": {"realized_pnl_usd": round(pnl, 0)}}


def exit_all(close_date: str | None = None) -> PortfolioResult:
    """Close every OPEN trade at current (or a past date's EOD) mid prices."""
    open_ids = sorted({r["trade_id"] for r in _load_rows()
                       if r["status"] == "OPEN"})
    if not open_ids:
        return {"kind": "trade_exited", "data": {},
                "summary": "Nothing to close — the portfolio has no OPEN trades."}
    results = [exit_trade(tid, close_date) for tid in open_ids]
    total = sum(r["data"].get("realized_pnl_usd", 0) for r in results)
    lines = "\n".join(f"- {r['summary']}" for r in results)
    return {"kind": "trade_exited",
            "summary": (f"Closed all {len(open_ids)} open trade(s); portfolio "
                        f"is now flat. Total realized P&L ${total:,.0f}.\n{lines}"),
            "data": {"closed_trade_ids": open_ids,
                     "total_realized_pnl_usd": round(total, 0)}}


def reset_log() -> str:
    """Archive the trade log and start a fresh empty one (nothing deleted)."""
    if not TRADE_LOG.exists():
        return "No trade log exists — nothing to reset."
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = TRADE_LOG.with_name(f"trade_log_archived_{stamp}.xlsx")
    TRADE_LOG.rename(archive)
    _ensure_log()
    return (f"Trade log archived to {archive.name}; fresh empty log created. "
            f"History is preserved in the archive.")


def analytics() -> PortfolioResult:
    rows = _load_rows()
    if not rows:
        return {"kind": "analytics", "summary": "Trade log is empty.", "data": {}}

    open_legs = [r for r in rows if r["status"] == "OPEN"]
    trades: dict[str, dict] = {}
    realized_total = 0.0

    for r in rows:
        t = trades.setdefault(r["trade_id"], {
            "structure": r["structure"], "expiry": r["expiry"],
            "open_date": r["open_date"], "status": r["status"], "legs": []})
        t["legs"].append(r)
        if r["status"] == "CLOSED":
            sign = 1 if r["action"] == "SELL" else -1
            realized_total += sign * (r["open_price"] - r["close_price"]) \
                * r["quantity"] * MULTIPLIER

    spot = None
    unrealized_total = 0.0
    port_delta = port_theta = 0.0
    if open_legs:
        max_days = max((date.fromisoformat(r["expiry"]) - date.today()).days
                       for r in open_legs)
        chain = get_strikes("SPX", dte=(1, max_days + 3), fields=PRICE_FIELDS)
        lookup = _chain_lookup(chain)
        spot = chain[0]["stockPrice"]
        for r in open_legs:
            row = lookup.get((r["expiry"], float(r["strike"])))
            if row is None:
                continue
            mid = _mid_for(row, r["right"])
            sign = 1 if r["action"] == "SELL" else -1
            if mid is not None:
                pnl = sign * (r["open_price"] - mid) * r["quantity"] * MULTIPLIER
                r["_mark"] = round(mid, 2)
                r["_pnl"] = round(pnl, 0)
                unrealized_total += pnl
            delta = row["delta"] if r["right"] == "C" else row["delta"] - 1
            port_delta += -sign * delta * r["quantity"] * MULTIPLIER
            port_theta += -sign * row["theta"] * r["quantity"] * MULTIPLIER

    n_open = len({r["trade_id"] for r in open_legs})
    summary = (
        f"{n_open} open trade(s), {len(trades) - n_open} closed. "
        + (f"Spot {spot:.2f}. " if spot else "")
        + f"Unrealized P&L ${unrealized_total:,.0f}, realized ${realized_total:,.0f}. "
        + (f"Portfolio delta {port_delta:+,.0f} $/pt, theta {port_theta:+,.0f} $/day."
           if open_legs else "")
    )
    return {"kind": "analytics", "summary": summary,
            "data": {"trades": trades, "spot": spot,
                     "unrealized_pnl_usd": round(unrealized_total, 0),
                     "realized_pnl_usd": round(realized_total, 0),
                     "portfolio_delta_usd_per_pt": round(port_delta, 0),
                     "portfolio_theta_usd_per_day": round(port_theta, 0)}}


def pnl_history(start: str | None = None) -> list[dict]:
    """Daily portfolio P&L (realized + unrealized, $) from `start` to today.

    Marks every leg at each trading day's EOD mids via ORATS historical data
    (today uses the current delayed chain). Non-trading days and days with
    missing data are skipped.
    """
    rows = _load_rows()
    if not rows:
        return []
    first_open = min(str(r["open_date"]) for r in rows)
    d = date.fromisoformat(start or first_open)
    today = date.today()
    series: list[dict] = []
    while d <= today:
        if d.weekday() >= 5:  # weekend
            d = date.fromordinal(d.toordinal() + 1)
            continue
        iso = d.isoformat()
        pnl = 0.0
        ok = True
        # legs already closed by this date contribute frozen realized P&L
        live_by_expiry: dict[str, list[dict]] = {}
        for r in rows:
            if str(r["open_date"]) > iso:
                continue
            sign = 1 if r["action"] == "SELL" else -1
            if r["status"] == "CLOSED" and str(r["close_date"]) <= iso:
                pnl += sign * (r["open_price"] - r["close_price"]) \
                    * r["quantity"] * MULTIPLIER
            else:
                live_by_expiry.setdefault(r["expiry"], []).append(r)
        for legs in live_by_expiry.values():
            try:
                mids, _ = _leg_mids(legs, iso if d < today else None)
            except (LookupError, Exception):
                ok = False
                break
            for r in legs:
                sign = 1 if r["action"] == "SELL" else -1
                pnl += sign * (r["open_price"] - mids[float(r["strike"])]) \
                    * r["quantity"] * MULTIPLIER
        if ok:
            series.append({"date": iso, "pnl_usd": round(pnl, 0)})
        d = date.fromordinal(d.toordinal() + 1)
    return series


def plot_history(series: list[dict], out_path=None) -> str:
    """Line chart of the P&L series (light mode, validated palette)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = out_path or (TRADE_LOG.parent / "pnl_history.png")
    dates = [s["date"][5:] for s in series]  # MM-DD
    pnls = [s["pnl_usd"] for s in series]

    surface, ink, ink2, series1 = "#fcfcfb", "#0b0b0b", "#52514e", "#2a78d6"
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor(surface)
    ax.set_facecolor(surface)
    ax.axhline(0, color=ink2, linewidth=1, linestyle="--", alpha=0.5)
    ax.plot(dates, pnls, color=series1, linewidth=2, marker="o",
            markersize=7, zorder=3)
    # direct label on the latest point only
    ax.annotate(f"${pnls[-1]:,.0f}", (dates[-1], pnls[-1]),
                textcoords="offset points", xytext=(0, 12),
                ha="center", color=ink, fontsize=10, fontweight="bold")
    ax.set_title(f"Portfolio P&L since {series[0]['date']} (daily EOD)",
                 color=ink, fontsize=12, loc="left", pad=12)
    ax.grid(axis="y", color="#e3e2dd", linewidth=0.8, zorder=0)
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.tick_params(colors=ink2, labelsize=9)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#e3e2dd")
    ax.margins(y=0.2)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=surface)
    plt.close(fig)
    return str(out_path)


# ------------------------------------------------------------ graph node

def run(state: PortfolioState) -> dict:
    action = state.get("portfolio_action", "analytics")
    instruction = state.get("portfolio_instruction", "")
    m = TRADE_ID_RE.search(instruction)
    trade_id = m.group(0) if m else None
    if action == "add_trade":
        if trade_id is None:
            result: PortfolioResult = {"kind": "trade_added", "data": {},
                                       "summary": "REJECTED: no trade_id found in instruction."}
        else:
            result = add_trade(trade_id)
    elif action == "exit_all":
        result = exit_all()
    elif action == "exit_trade":
        if trade_id is None:
            result = {"kind": "trade_exited", "data": {},
                      "summary": "REJECTED: no trade_id found in instruction."}
        else:
            result = exit_trade(trade_id)
    else:
        result = analytics()
    return {"portfolio_result": result}


# ------------------------------------------------------------ CLI

def main() -> None:
    p = argparse.ArgumentParser(description="Paper portfolio manager")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="open a trade from data/recommendations/")
    a.add_argument("trade_id")
    a.add_argument("--date", help="open date YYYY-MM-DD (prices at that EOD)")
    a.add_argument("--note", default="")
    am = sub.add_parser("add-manual",
                        help="open a trade by explicit legs (human override)")
    am.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    am.add_argument("--right", required=True, choices=["C", "P"])
    am.add_argument("--short", required=True, type=float, help="short strike")
    am.add_argument("--long", required=True, type=float, help="long strike")
    am.add_argument("--qty", type=int, default=20)
    am.add_argument("--date", help="open date YYYY-MM-DD (prices at that EOD)")
    am.add_argument("--note", default="")
    e = sub.add_parser("exit", help="close all OPEN legs of a trade")
    e.add_argument("trade_id")
    e.add_argument("--date", help="close date YYYY-MM-DD (prices at that EOD)")
    ea = sub.add_parser("exit-all", help="close EVERY open trade at market")
    ea.add_argument("--date", help="close date YYYY-MM-DD (prices at that EOD)")
    rs = sub.add_parser("reset", help="archive the trade log, start fresh")
    rs.add_argument("--confirm", action="store_true",
                    help="required — this archives the current log")
    sub.add_parser("analytics", help="positions, P&L, portfolio greeks")
    h = sub.add_parser("history", help="daily P&L series since a date")
    h.add_argument("--start", help="YYYY-MM-DD (default: first open date)")
    h.add_argument("--plot", action="store_true", help="also save a PNG chart")
    args = p.parse_args()

    if args.cmd == "history":
        series = pnl_history(args.start)
        if not series:
            print("No P&L history (empty trade log or no data).")
            return
        for s in series:
            print(f"{s['date']}  ${s['pnl_usd']:>10,.0f}")
        if args.plot:
            path = plot_history(series)
            print(f"chart -> {path}")
            try:
                os.startfile(path)  # pop it open in the default image viewer
            except OSError:
                pass
        return

    if args.cmd == "reset":
        if not args.confirm:
            print("Refused: reset archives the current trade log and starts "
                  "fresh. Re-run with --confirm if you mean it.")
            return
        print(reset_log())
        return

    if args.cmd == "add":
        result = add_trade(args.trade_id, args.date, args.note)
    elif args.cmd == "exit-all":
        result = exit_all(args.date)
    elif args.cmd == "add-manual":
        result = add_trade_manual(args.expiry, args.right, args.short,
                                  args.long, args.qty, args.date, args.note)
    elif args.cmd == "exit":
        result = exit_trade(args.trade_id, args.date)
    else:
        result = analytics()

    print(result["summary"])
    if args.cmd == "analytics" and result["data"].get("trades"):
        print()
        for tid, t in result["data"]["trades"].items():
            print(f"{tid}  [{t['status']}]  opened {t['open_date']}")
            for leg in t["legs"]:
                mark = f"  mark {leg['_mark']:.2f}  P&L ${leg['_pnl']:,.0f}" \
                    if "_mark" in leg else ""
                closed = f"  closed {leg['close_date']} @ {leg['close_price']}" \
                    if leg["status"] == "CLOSED" else ""
                print(f"  {leg['action']:<4} {leg['quantity']}x "
                      f"{leg['expiry']} {int(leg['strike'])}{leg['right']} "
                      f"@ {leg['open_price']:.2f}{mark}{closed}")


if __name__ == "__main__":
    main()
