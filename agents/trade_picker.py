"""Trade picker agent.

Screens ALL SPX call-spread and put-spread pairs (credit spreads: sell the
near leg, buy the leg exactly `strike_width` further out) against the user's
standing objectives (config.DEFAULT_TRADE_OBJECTIVES, overridable per query):

  - strike difference exactly 100
  - 20 contracts per trade
  - each leg's open interest > 500
  - upfront credit per pair, per 30.44-day month, > 8 in quoted price terms
    (8 SPX points = $800 cash per pair)
  - expiries between 2 weeks and 3 months
  - both legs' strikes at least 3% away from spot (OTM direction)
  - rank by highest theta/delta ratio of the net position

Data: ORATS /strikes (delayed, ~15-min lag). Greek conventions verified live:
`delta` is the call delta (put delta = delta - 1); `theta` is points/day and
negative for long options. For a credit spread (short near + long far):
    net_theta = theta(far) - theta(near)   -> positive (we collect decay)
    net_delta = delta(far) - delta(near)   -> small; sign shows direction
Position dollars = points x 100 multiplier x contracts.

Informational columns (shown and stored, never used to select or rank):
  - gamma$: net gamma x multiplier x contracts = $ change in position delta
    per 1 SPX point (negative for a credit spread: short near-strike gamma
    outweighs the long wing).
  - be_pts: break-even daily move = sqrt(2 * net theta / |net gamma|) in SPX
    points -- the one-day move at which the gamma loss equals the theta
    collected (ignores delta and vega). Roughly the implied one-day move
    scaled down by the skew between the two legs.
  - sd: short strike's distance from spot in implied standard deviations,
    |K - S| / (S * IV * sqrt(T)), using the short leg's own OTM-side mid IV.
    ORATS dte counts today as day 1, so T = (dte - 1) / 365 (floored).

Persists every recommendation set to data/recommendations/ so a later,
separate add_trade query can reference a trade_id — there is no in-graph
handoff to the portfolio manager.

Reads:  trade_objectives (overrides on the defaults)
Writes: trade_recommendations (list[TradeRecommendation])

Standalone run:  python -m agents.trade_picker   (from the project root)
"""

import json
import math
from datetime import datetime

from config import DEFAULT_TRADE_OBJECTIVES, RECOMMENDATIONS_DIR
from graph.state import PortfolioState, TradeLeg, TradeRecommendation
from mcp_servers.orats_client import get_strikes

DAYS_PER_MONTH = 30.4375
CONTRACT_MULTIPLIER = 100  # SPX options are $100 per point

CHAIN_FIELDS = [
    "expirDate", "dte", "strike", "stockPrice",
    "callBidPrice", "callAskPrice", "putBidPrice", "putAskPrice",
    "callOpenInterest", "putOpenInterest", "delta", "theta", "gamma",
    "callMidIv", "putMidIv",
]


def _mid(bid: float, ask: float) -> float | None:
    """Mid price; None when the market is not two-sided."""
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return None


def _leg_view(row: dict, side: str) -> dict | None:
    """Extract one option (call or put) from a chain row, or None if unquotable."""
    if side == "C":
        mid = _mid(row["callBidPrice"], row["callAskPrice"])
        oi = row["callOpenInterest"]
        delta = row["delta"]
        iv = row.get("callMidIv")
    else:
        mid = _mid(row["putBidPrice"], row["putAskPrice"])
        oi = row["putOpenInterest"]
        delta = row["delta"] - 1  # put delta from the shared call delta
        iv = row.get("putMidIv")
    if mid is None:
        return None
    # gamma is shared by the call and put at a strike; iv is the OTM-side mid IV
    return {"mid": mid, "oi": oi, "delta": delta, "theta": row["theta"],
            "gamma": row.get("gamma"), "iv": iv}


def _breakeven_move(net_theta: float, net_gamma: float | None) -> float | None:
    """Daily SPX move (points) where the gamma loss equals one day's theta.

    From P&L ~ theta + 0.5 * gamma * dS^2 = 0. None when gamma is missing or
    not negative (zero-greek artifacts), where the ratio is meaningless.
    """
    if net_gamma is None or net_gamma >= 0 or net_theta <= 0:
        return None
    return math.sqrt(2 * net_theta / -net_gamma)


def _strike_sd(spot: float, strike: float, iv: float | None, dte: int) -> float | None:
    """Distance from spot to `strike` in implied standard deviations of SPX
    over the time left. ORATS dte counts today as day 1, so calendar time is
    roughly dte - 1 days (floored so a 1-day expiry does not divide by zero)."""
    if not iv or iv <= 0:
        return None
    years = max(dte - 1, 0.5) / 365
    return abs(strike - spot) / (spot * iv * math.sqrt(years))


def pick_trades(objectives: dict | None = None) -> list[TradeRecommendation]:
    obj = {**DEFAULT_TRADE_OBJECTIVES, **(objectives or {})}
    width = obj["strike_width"]
    contracts = obj["contracts"]
    min_oi = obj["min_leg_open_interest"]
    min_prem = obj["min_premium_per_month"]
    min_dist = obj["min_strike_distance_pct"]
    min_dte = obj["min_days_to_expiry"]
    max_dte = round(obj["max_months_to_expiry"] * DAYS_PER_MONTH)

    rows = get_strikes("SPX", dte=(min_dte, max_dte), fields=CHAIN_FIELDS)
    spot = rows[0]["stockPrice"]

    by_exp: dict[str, dict[float, dict]] = {}
    for r in rows:
        by_exp.setdefault(r["expirDate"], {})[float(r["strike"])] = r

    candidates = []
    for exp, strikes in by_exp.items():
        dte = next(iter(strikes.values()))["dte"]
        months = dte / DAYS_PER_MONTH
        for structure in obj["structures"]:
            side = "C" if structure == "call spread" else "P"
            # short the near strike, long the strike exactly `width` further out
            far_offset = width if side == "C" else -width
            otm_sign = 1 if side == "C" else -1
            for k, near_row in strikes.items():
                far_row = strikes.get(k + far_offset)
                if far_row is None:
                    continue
                # both legs at least min_dist% away from spot, OTM side
                dist_near = otm_sign * (k - spot) / spot * 100
                dist_far = otm_sign * (k + far_offset - spot) / spot * 100
                if dist_near < min_dist or dist_far < min_dist:
                    continue
                near = _leg_view(near_row, side)
                far = _leg_view(far_row, side)
                if near is None or far is None:
                    continue
                if near["oi"] <= min_oi or far["oi"] <= min_oi:
                    continue
                credit = near["mid"] - far["mid"]
                if credit <= 0:
                    continue
                premium_per_month = credit / months
                if premium_per_month <= min_prem:
                    continue
                net_theta = far["theta"] - near["theta"]
                net_delta = far["delta"] - near["delta"]
                if net_theta <= 0:
                    continue  # not a decay-collecting position; also drops stale zero-greek rows
                ratio = net_theta / max(abs(net_delta), 1e-3)
                net_gamma = (far["gamma"] - near["gamma"]
                             if near["gamma"] is not None and far["gamma"] is not None
                             else None)
                candidates.append({
                    "structure": structure, "side": side, "exp": exp, "dte": dte,
                    "short_k": k, "long_k": k + far_offset,
                    "near": near, "far": far, "credit": credit,
                    "premium_per_month": premium_per_month,
                    "net_theta": net_theta, "net_delta": net_delta,
                    "net_gamma": net_gamma,
                    "breakeven_move": _breakeven_move(net_theta, net_gamma),
                    "short_sd": _strike_sd(spot, k, near["iv"], dte),
                    "ratio": ratio, "distance_pct": dist_near,
                })

    candidates.sort(key=lambda c: c["ratio"], reverse=True)
    # Theta/delta ratio still drives which trades get selected (top_n below) --
    # this is a second, informational ranking by raw theta$/day alone, so a
    # trade's standing on absolute decay is visible alongside its ratio rank.
    theta_rank = {id(c): i + 1 for i, c in enumerate(
        sorted(candidates, key=lambda c: c["net_theta"], reverse=True))}

    recs: list[TradeRecommendation] = []
    for c in candidates[: obj["top_n"]]:
        code = "CS" if c["side"] == "C" else "PS"
        trade_id = (
            f"SPX-{code}-{c['exp'].replace('-', '')}-"
            f"{int(c['short_k'])}x{int(c['long_k'])}"
        )
        legs: list[TradeLeg] = [
            {"underlying": "SPX", "expiry": c["exp"], "strike": c["short_k"],
             "right": c["side"], "action": "SELL", "quantity": contracts,
             "price": round(c["near"]["mid"], 2)},
            {"underlying": "SPX", "expiry": c["exp"], "strike": c["long_k"],
             "right": c["side"], "action": "BUY", "quantity": contracts,
             "price": round(c["far"]["mid"], 2)},
        ]
        position_scale = CONTRACT_MULTIPLIER * contracts
        recs.append({
            "trade_id": trade_id,
            "structure": c["structure"],
            "legs": legs,
            "net_premium": round(c["credit"], 2),
            "premium_per_month": round(c["premium_per_month"], 2),
            "metrics": {
                "theta_delta_ratio": round(c["ratio"], 2),
                "net_theta_pts_per_day": round(c["net_theta"], 4),
                "net_delta": round(c["net_delta"], 4),
                "delta_usd_position": round(c["net_delta"] * position_scale, 0),
                "net_gamma": (round(c["net_gamma"], 6)
                              if c["net_gamma"] is not None else None),
                "gamma_usd_position": (round(c["net_gamma"] * position_scale, 2)
                                       if c["net_gamma"] is not None else None),
                "breakeven_move_pts": (round(c["breakeven_move"], 1)
                                       if c["breakeven_move"] is not None else None),
                "short_strike_sd": (round(c["short_sd"], 2)
                                    if c["short_sd"] is not None else None),
                "theta_usd_per_day_position": round(c["net_theta"] * position_scale, 0),
                "theta_usd_rank": theta_rank[id(c)],
                "candidates_considered": len(candidates),
                "credit_usd_position": round(c["credit"] * position_scale, 0),
                "short_leg_oi": c["near"]["oi"],
                "long_leg_oi": c["far"]["oi"],
                "dte": c["dte"],
            },
            "distance_from_spot_pct": round(c["distance_pct"], 2),
            "rationale": (
                f"Sell {contracts}x {c['exp']} {int(c['short_k'])}{c['side']} / "
                f"buy {int(c['long_k'])}{c['side']} for {c['credit']:.2f} pts credit "
                f"(${c['credit'] * position_scale:,.0f} on {contracts} contracts; "
                f"{c['premium_per_month']:.2f} pts/month). Position theta "
                f"+{c['net_theta'] * position_scale:,.0f} $/day vs net delta "
                f"{c['net_delta']:+.3f} -> theta/delta {c['ratio']:.1f}. "
                f"Short strike {c['distance_pct']:+.1f}% from spot."
            ),
        })

    _persist(obj, spot, recs)
    return recs


def _persist(objectives: dict, spot: float, recs: list[TradeRecommendation]) -> None:
    """Save the run so a later add_trade query can look up a trade_id."""
    RECOMMENDATIONS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "spot": spot,
        "objectives": objectives,
        "recommendations": recs,
    }
    (RECOMMENDATIONS_DIR / f"rec_{stamp}.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )


def run(state: PortfolioState) -> dict:
    """LangGraph node: screen with the query's overrides on the defaults."""
    recs = pick_trades(state.get("trade_objectives"))
    return {"trade_recommendations": recs}


def _fmt(value: float | None, spec: str, width: int) -> str:
    """Right-aligned number, or 'n/a' when the metric could not be computed."""
    return f"{'n/a':>{width}}" if value is None else f"{value:>{width}{spec}}"


def table_str(recs: list[TradeRecommendation]) -> str:
    if not recs:
        return "No trades passed the filters."
    hdr = (f"{'trade_id':<28} {'dte':>4} {'credit':>7} {'pts/mo':>7} "
           f"{'cred$(20x)':>11} {'theta$/d':>9} {'delta':>8} {'delta$':>9} "
           f"{'gamma$':>8} {'t/d':>7} {'th$rank':>9} {'be_pts':>7} "
           f"{'dist%':>6} {'sd':>5} {'OI s/l':>12}")
    lines = [hdr, "-" * len(hdr)]
    for r in recs:
        m = r["metrics"]
        th_rank = f"{m['theta_usd_rank']}/{m['candidates_considered']}"
        lines.append(
            f"{r['trade_id']:<28} {m['dte']:>4} {r['net_premium']:>7.2f} "
            f"{r['premium_per_month']:>7.2f} {m['credit_usd_position']:>11,.0f} "
            f"{m['theta_usd_per_day_position']:>9,.0f} {m['net_delta']:>8.3f} "
            f"{m['delta_usd_position']:>9,.0f} "
            f"{_fmt(m['gamma_usd_position'], '.2f', 8)} "
            f"{m['theta_delta_ratio']:>7.2f} {th_rank:>9} "
            f"{_fmt(m['breakeven_move_pts'], '.1f', 7)} "
            f"{r['distance_from_spot_pct']:>6.1f} "
            f"{_fmt(m['short_strike_sd'], '.2f', 5)} "
            f"{m['short_leg_oi']:>5}/{m['long_leg_oi']:<6}")
    return "\n".join(lines)


def _print_table(recs: list[TradeRecommendation]) -> None:
    print(table_str(recs))


if __name__ == "__main__":
    picks = pick_trades()
    _print_table(picks)
