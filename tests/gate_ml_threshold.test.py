#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from signal_integration import (
    _resolve_ml_threshold,
    get_fused_ml_score,
    collect_quality_gate_failures,
)


def test_drift_softer_in_bull():
    t = _resolve_ml_threshold(66.0, True, "BULL", 70, 68)
    assert t == 68.0  # +2 not +4


def test_lower_third_sweet_spot_relief():
    t = _resolve_ml_threshold(66.0, True, "BULL", 76, 64, vol_ratio=2.8, close_position=0.25)
    assert t <= 63.0


def test_high_ues_relief():
    t = _resolve_ml_threshold(66.0, False, "BULL", 82, 69)
    assert t == 63.0


def test_volatile_bull_high_ml_passes():
    fails = collect_quality_gate_failures(
        86.0, 93.0, 'cyclical', 'VOLATILE', 0.65, 0.9, 'BREADTH_NEUTRAL',
        active_regime='BULL', vol_ratio=1.6, close_position=0.62, scan_score=0.0,
    )
    assert 'volatile_stock' not in fails


def test_high_scan_low_session_vol_passes():
    fails = collect_quality_gate_failures(
        87.0, 88.0, 'cyclical', 'EXPLOSIVE', 0.31, 0.9, 'BREADTH_NEUTRAL',
        active_regime='BULL', vol_ratio=0.68, close_position=0.44,
        scan_score=86.0,
    )
    assert 'low_volume_signal' not in fails


def test_scan_confirm_fusion():
    fused, breakdown = get_fused_ml_score(
        "TEST", "2026-06-11", None,
        explosion_score=40.0,
        scan_score=72.0,
    )
    assert breakdown.get("scan_confirm") is not None
    assert fused >= 64.0


if __name__ == "__main__":
    test_drift_softer_in_bull()
    test_lower_third_sweet_spot_relief()
    test_high_ues_relief()
    test_volatile_bull_high_ml_passes()
    test_high_scan_low_session_vol_passes()
    test_scan_confirm_fusion()
    print("gate_ml_threshold_ok")
