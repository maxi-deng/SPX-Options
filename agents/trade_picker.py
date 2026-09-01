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

Persists every recommendation set to data/recommendations/ so a later,
separate add_trade query can reference a trade_id — there is no in-graph
handoff to the portfolio manager.

Reads:  trade_objectives (overrides on the defaults)
Writes: trade_recommendations (list[TradeRecommendation])

Standalone run:  python -m agents.trade_picker   (from the project root)
"""

import json
from datetime import datetime

from config import DEFAULT_TRADE_OBJECTIVES, RECOMMENDATIONS_DIR
from graph.state import PortfolioState, TradeLeg, TradeRecommendation
from mcp_servers.orats_client import get_strikes

DAYS_PER_MONTH = 30.4375
CONTRACT_MULTIPLIER = 100  # SPX options are $100 per point

CHAIN_FIELDS = [
    "expirDate", "dte", "strike", "stockPrice",
    "callBidPrice", "callAskPrice", "putBidPrice", "putAskPrice",
    "callOpenInterest", "putOpenInterest", "delta", "theta",
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
    else:
        mid = _mid(row["putBidPrice"], row["putAskPrice"])
        oi = row["putOpenInterest"]
        delta = row["delta"] - 1  # put delta from the shared call delta
    if mid is None:
        return None
    return {"mid": mid, "oi": oi, "delta": delta, "theta": row["theta"]}


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
                candidates.append({
                    "structure": structure, "side": side, "exp": exp, "dte": dte,
                    "short_k": k, "long_k": k + far_offset,
                    "near": near, "far": far, "credit": credit,
                    "premium_per_month": premium_per_month,
                    "net_theta": net_theta, "net_delta": net_delta,
                    "ratio": ratio, "distance_pct": dist_near,
                })

    candidates.sort(key=lambda c: c["ratio"], reverse=True)

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
                "theta_usd_per_day_position": round(c["net_theta"] * position_scale, 0),
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


def table_str(recs: list[TradeRecommendation]) -> str:
    if not recs:
        return "No trades passed the filters."
    hdr = (f"{'trade_id':<28} {'dte':>4} {'credit':>7} {'pts/mo':>7} "
           f"{'cred$(20x)':>11} {'theta$/d':>9} {'delta':>8} {'t/d':>7} "
           f"{'dist%':>6} {'OI s/l':>12}")
    lines = [hdr, "-" * len(hdr)]
    for r in recs:
        m = r["metrics"]
        lines.append(
            f"{r['trade_id']:<28} {m['dte']:>4} {r['net_premium']:>7.2f} "
            f"{r['premium_per_month']:>7.2f} {m['credit_usd_position']:>11,.0f} "
            f"{m['theta_usd_per_day_position']:>9,.0f} {m['net_delta']:>8.3f} "
            f"{m['theta_delta_ratio']:>7.2f} {r['distance_from_spot_pct']:>6.1f} "
            f"{m['short_leg_oi']:>5}/{m['long_leg_oi']:<6}")
    return "\n".join(lines)


def _print_table(recs: list[TradeRecommendation]) -> None:
    print(table_str(recs))


if __name__ == "__main__":
    picks = pick_trades()
    _print_table(picks)
