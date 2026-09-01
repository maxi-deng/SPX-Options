"""Trade picker grader agent.

Deliberately simple and fully deterministic (no LLM): verify that the trade
picker's output actually matches the user's standing criteria and is not
hallucinated. Independence comes from the DATA, not the code — the grader
does its own fresh ORATS fetch and recomputes everything from that, never
trusting the numbers inside the recommendation.

Per recommendation it checks:
  - structure: two legs, same expiry/right, one SELL + one BUY, exact width,
    correct contract count
  - anti-hallucination: both legs exist in the fresh chain, are two-sided,
    and the claimed leg prices match fresh mids within tolerance
  - criteria: per-leg OI, DTE window, both strikes' distance from spot,
    credit-per-month floor, positive net theta
  - internal arithmetic: net_premium / premium_per_month consistent with the
    recommendation's own leg prices
  - the claimed theta/delta ratio matches the recomputed one

One-way by design: the verdict goes forward to the user, never back to the
trade picker (no feedback loop, no backtester).

Reads:  trade_recommendations, trade_objectives
Writes: grader_verdict (passed, per-check results, feedback)

Standalone run (grades the latest data/recommendations/rec_*.json):
    python -m agents.trade_grader
"""

import json

from agents.trade_picker import (
    CHAIN_FIELDS,
    DAYS_PER_MONTH,
    _leg_view,
)
from config import DEFAULT_TRADE_OBJECTIVES, RECOMMENDATIONS_DIR
from graph.state import GraderVerdict, PortfolioState, TradeRecommendation
from mcp_servers.orats_client import get_strikes

# Quotes can drift between the picker's fetch and ours (15-min delayed feed);
# same-snapshot runs match exactly, so these bands only absorb small ticks.
PRICE_TOL_PTS = 0.25
PRICE_TOL_REL = 0.05
RATIO_TOL_ABS = 0.2
RATIO_TOL_REL = 0.10


def _close(claimed: float, actual: float, abs_tol: float = PRICE_TOL_PTS,
           rel_tol: float = PRICE_TOL_REL) -> bool:
    return abs(claimed - actual) <= max(abs_tol, rel_tol * abs(actual))


def grade_trades(
    recs: list[TradeRecommendation], objectives: dict | None = None
) -> GraderVerdict:
    obj = {**DEFAULT_TRADE_OBJECTIVES, **(objectives or {})}
    if not recs:
        return {"passed": False, "checks": [],
                "feedback": "No recommendations to grade."}

    checks: list[dict] = []

    def check(trade_id: str, name: str, passed: bool, detail: str) -> bool:
        checks.append({"objective": f"{trade_id}: {name}",
                       "passed": bool(passed), "detail": detail})
        return passed

    # Independent fetch. Slightly widened DTE window so legs picked at the
    # edge of the window are still findable if a day has rolled over.
    max_dte = round(obj["max_months_to_expiry"] * DAYS_PER_MONTH)
    rows = get_strikes("SPX", dte=(1, max_dte + 2), fields=CHAIN_FIELDS)
    spot = rows[0]["stockPrice"]
    chain: dict[str, dict[float, dict]] = {}
    for r in rows:
        chain.setdefault(r["expirDate"], {})[float(r["strike"])] = r

    for rec in recs:
        tid = rec["trade_id"]
        legs = rec["legs"]

        ok = (len(legs) == 2 and legs[0]["expiry"] == legs[1]["expiry"]
              and legs[0]["right"] == legs[1]["right"]
              and {legs[0]["action"], legs[1]["action"]} == {"SELL", "BUY"})
        if not check(tid, "structure", ok,
                     "needs two legs, same expiry/right, one SELL + one BUY"):
            continue
        short = next(l for l in legs if l["action"] == "SELL")
        long_ = next(l for l in legs if l["action"] == "BUY")
        side = short["right"]

        expected_offset = obj["strike_width"] if side == "C" else -obj["strike_width"]
        check(tid, "strike width",
              long_["strike"] - short["strike"] == expected_offset,
              f"long-short strike offset {long_['strike'] - short['strike']:+g}, "
              f"expected {expected_offset:+g}")
        check(tid, "contracts",
              short["quantity"] == long_["quantity"] == obj["contracts"],
              f"{short['quantity']}/{long_['quantity']} vs required {obj['contracts']} per leg")

        exp_strikes = chain.get(short["expiry"], {})
        near_row = exp_strikes.get(float(short["strike"]))
        far_row = exp_strikes.get(float(long_["strike"]))
        if not check(tid, "legs exist in ORATS chain",
                     near_row is not None and far_row is not None,
                     "strike/expiry not in fresh chain — possible hallucination"):
            continue
        near = _leg_view(near_row, side)
        far = _leg_view(far_row, side)
        if not check(tid, "legs quotable", near is not None and far is not None,
                     "one-sided or empty quotes on a leg"):
            continue

        check(tid, "claimed leg prices match fresh mids",
              _close(short["price"], near["mid"]) and _close(long_["price"], far["mid"]),
              f"claimed {short['price']:.2f}/{long_['price']:.2f} "
              f"vs fresh {near['mid']:.2f}/{far['mid']:.2f}")

        check(tid, "open interest",
              near["oi"] > obj["min_leg_open_interest"]
              and far["oi"] > obj["min_leg_open_interest"],
              f"OI {near['oi']}/{far['oi']} must exceed {obj['min_leg_open_interest']}")

        dte = near_row["dte"]
        check(tid, "days to expiry",
              obj["min_days_to_expiry"] <= dte <= max_dte,
              f"dte {dte} vs window [{obj['min_days_to_expiry']}, {max_dte}]")

        sign = 1 if side == "C" else -1
        d_near = sign * (short["strike"] - spot) / spot * 100
        d_far = sign * (long_["strike"] - spot) / spot * 100
        check(tid, "strike distance from spot",
              d_near >= obj["min_strike_distance_pct"]
              and d_far >= obj["min_strike_distance_pct"],
              f"short {d_near:.2f}% / long {d_far:.2f}% vs "
              f"min {obj['min_strike_distance_pct']}% (OTM side)")

        credit = near["mid"] - far["mid"]
        months = dte / DAYS_PER_MONTH
        ppm = credit / months
        check(tid, "credit per month",
              credit > 0 and ppm > obj["min_premium_per_month"],
              f"fresh credit {credit:.2f} pts -> {ppm:.2f}/month vs "
              f"min {obj['min_premium_per_month']}")

        check(tid, "internal arithmetic",
              _close(rec["net_premium"], short["price"] - long_["price"], 0.02)
              and _close(rec["premium_per_month"], rec["net_premium"] / months, 0.05),
              "net_premium / premium_per_month inconsistent with claimed leg prices")

        net_theta = far["theta"] - near["theta"]
        net_delta = far["delta"] - near["delta"]
        ratio = net_theta / max(abs(net_delta), 1e-3)
        check(tid, "positive theta", net_theta > 0,
              f"fresh net theta {net_theta:+.4f} pts/day")
        claimed_ratio = rec["metrics"].get("theta_delta_ratio")
        check(tid, "theta/delta ratio claim",
              claimed_ratio is not None
              and _close(claimed_ratio, ratio, RATIO_TOL_ABS, RATIO_TOL_REL),
              f"claimed {claimed_ratio} vs fresh {ratio:.2f}")

    failed = [c for c in checks if not c["passed"]]
    passed = not failed
    if passed:
        feedback = (
            f"All {len(recs)} recommendation(s) verified against an independent "
            f"ORATS fetch ({len(checks)} checks): legs exist, prices/OI/greeks "
            f"match, and every screening criterion holds."
        )
    else:
        lines = "\n".join(f"- {c['objective']} — {c['detail']}" for c in failed)
        feedback = f"{len(failed)} of {len(checks)} checks FAILED:\n{lines}"
    return {"passed": passed, "checks": checks, "feedback": feedback}


def run(state: PortfolioState) -> dict:
    verdict = grade_trades(
        state["trade_recommendations"], state.get("trade_objectives")
    )
    return {"grader_verdict": verdict}


if __name__ == "__main__":
    rec_files = sorted(RECOMMENDATIONS_DIR.glob("rec_*.json"))
    if not rec_files:
        raise SystemExit("No recommendation files in data/recommendations/ — run the trade picker first.")
    latest = rec_files[-1]
    saved = json.loads(latest.read_text(encoding="utf-8"))
    verdict = grade_trades(saved["recommendations"], saved.get("objectives"))
    print(f"Grading {latest.name} ({len(saved['recommendations'])} trades)")
    print(f"VERDICT: {'PASS' if verdict['passed'] else 'FAIL'}")
    print(verdict["feedback"])
