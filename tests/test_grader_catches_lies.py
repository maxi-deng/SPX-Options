"""Negative test: the grader must FAIL recommendations that are hallucinated.

Takes the latest real recommendation file and plants three lies:
  1. a strike that does not exist in the SPX chain
  2. an inflated short-leg price (with matching inflated premium arithmetic)
  3. a fabricated theta/delta ratio

Hits the live ORATS API (like the grader itself).
Run from the project root:  python -m tests.test_grader_catches_lies
"""

import copy
import json

from agents.trade_grader import grade_trades
from config import RECOMMENDATIONS_DIR


def main() -> None:
    latest = sorted(RECOMMENDATIONS_DIR.glob("rec_*.json"))[-1]
    saved = json.loads(latest.read_text(encoding="utf-8"))
    real = saved["recommendations"]
    assert len(real) >= 3, "need at least 3 real recommendations to corrupt"

    lie1 = copy.deepcopy(real[0])           # nonexistent strike
    lie1["legs"][0]["strike"] = 7777.0
    lie1["trade_id"] += "-FAKESTRIKE"

    lie2 = copy.deepcopy(real[1])           # inflated price + premium
    lie2["legs"][0]["price"] = round(lie2["legs"][0]["price"] * 2, 2)
    lie2["net_premium"] = round(
        lie2["legs"][0]["price"] - lie2["legs"][1]["price"], 2
    )
    lie2["trade_id"] += "-FAKEPRICE"

    lie3 = copy.deepcopy(real[2])           # fabricated ratio
    lie3["metrics"]["theta_delta_ratio"] = 99.0
    lie3["trade_id"] += "-FAKERATIO"

    verdict = grade_trades([lie1, lie2, lie3], saved.get("objectives"))
    failed_names = [c["objective"] for c in verdict["checks"] if not c["passed"]]

    assert not verdict["passed"], "grader passed corrupted recommendations!"
    assert any("FAKESTRIKE" in n and "width" in n or
               "FAKESTRIKE" in n and "exist" in n for n in failed_names), \
        "nonexistent strike not caught"
    assert any("FAKEPRICE" in n and "prices" in n for n in failed_names), \
        "inflated price not caught"
    assert any("FAKERATIO" in n and "ratio" in n for n in failed_names), \
        "fabricated ratio not caught"

    print("OK — grader caught all three planted lies:")
    for name in failed_names:
        print(f"  caught: {name}")


if __name__ == "__main__":
    main()
