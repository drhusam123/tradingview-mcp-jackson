#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from signal_integration import _apply_final_edge_gates
from low_rule_score_policy import evaluate_low_rule_score_policy


def _risk_ctx(bucket="VALID_BREAKOUT_RISK_MODEL", rr=2.0):
    return {
        "_risk_validation": {
            "bucket": bucket,
            "valid_for_rr": True,
            "actionability": "BUY",
            "effective_entry": 100.0,
            "rr": rr,
        },
        "_low_rule_ctx": {
            "ues": 82.0,
            "ml_score": 88.0,
            "quality_gate_passed": True,
            "quality_gate_failures": [],
            "anti_law": False,
        },
        "recent_low_8": 98.5,
        "close_position": 0.25,
        "vol_ratio_20": 1.2,
    }


def test_tight_sl_float_edge_passes():
    ok, reason, _ = _apply_final_edge_gates(
        symbol="VALU",
        setup_type="Volume Accumulation",
        scan_score=87.0,
        entry_price=11.0,
        entry_high=11.0,
        stop_loss=10.835,
        t1_target=11.33,
        r_ratio=2.0,
        used_fallback_risk=False,
        scan_volume_ratio=1.5,
        price_ctx=_risk_ctx(),
    )
    assert ok, reason


def test_marginal_sl_breach_passes():
    ok, reason, metrics = _apply_final_edge_gates(
        symbol="ISPH",
        setup_type="Volume Accumulation",
        scan_score=56.0,
        entry_price=11.92,
        entry_high=11.92,
        stop_loss=11.595,
        t1_target=12.57,
        r_ratio=2.0,
        used_fallback_risk=False,
        scan_volume_ratio=2.6,
        price_ctx={
            **_risk_ctx(rr=1.8),
            "recent_low_8": 11.45,
            "close_position": 0.29,
            "_low_rule_ctx": {
                "ues": 78.7,
                "ml_score": 88.0,
                "quality_gate_passed": True,
                "quality_gate_failures": [],
                "anti_law": False,
            },
        },
    )
    assert ok, reason
    assert metrics.get("marginal_sl_exception")


def test_ml_led_low_rule_exception():
    pol = evaluate_low_rule_score_policy(
        scan_score=0.0,
        quant_matches=0,
        setup_type="Near ATH Breakout",
        ues=81.9,
        ml_score=88.0,
        risk_bucket="VALID_BREAKOUT_RISK_MODEL",
        risk_valid_for_rr=True,
        risk_actionability="BUY",
        quality_gate_passed=True,
    )
    assert pol["exception_applies"]
    assert pol["exception_reason"] == "LOW_RULE_ML_LED_EXCEPTION"


if __name__ == "__main__":
    test_tight_sl_float_edge_passes()
    test_marginal_sl_breach_passes()
    test_ml_led_low_rule_exception()
    print("final_edge_relaxations_ok")
