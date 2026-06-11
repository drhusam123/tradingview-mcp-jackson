#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
from discovery_promotion_policy import (
    arbitration_allows_discovery_override,
    effective_scan_score,
    is_opp_stage_promotable,
    veto_allows_discovery_override,
)


def test_opp_v2_stages_promotable():
    assert is_opp_stage_promotable("ACTIONABLE_CANDIDATE")
    assert is_opp_stage_promotable("NEAR_BREAKOUT")
    assert not is_opp_stage_promotable("AVOID")


def test_scan_proxy_from_structure():
    row = {
        "source_rules": 0,
        "structure_score": 94.0,
        "opportunity_score": 80.0,
    }
    assert effective_scan_score(row) >= 65


def test_survival_sl_override_with_discovery():
    row = {
        "opportunity_score": 80.74,
        "score": 77.0,
        "structure_score": 94.0,
        "opp_stage": "ACTIONABLE_CANDIDATE",
        "flags_json": '["LOWER_THIRD_CLOSE"]',
    }
    assert veto_allows_discovery_override("QUALITY_GATE:survival_sl_dominant", row)


def test_arbitration_liquidity_override():
    row = {
        "opportunity_score": 79.15,
        "score": 73.7,
        "structure_score": 94.55,
        "opp_stage": "ACTIONABLE_CANDIDATE",
        "flags_json": '["LIQUIDITY_EXPANSION", "LOWER_THIRD_CLOSE"]',
    }
    veto = "execution_infeasible: tier=ILLIQUID (requires DEEP or MID)"
    assert arbitration_allows_discovery_override(veto, row)


if __name__ == "__main__":
    test_opp_v2_stages_promotable()
    test_scan_proxy_from_structure()
    test_survival_sl_override_with_discovery()
    test_arbitration_liquidity_override()
    print("discovery_promotion_policy_ok")
