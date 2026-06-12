#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from signal_integration import _resolve_ml_threshold


def test_drift_softer_in_bull():
    t = _resolve_ml_threshold(66.0, True, "BULL", 70, 68)
    assert t == 68.0  # +2 not +4


def test_lower_third_sweet_spot_relief():
    t = _resolve_ml_threshold(66.0, True, "BULL", 76, 64, vol_ratio=2.8, close_position=0.25)
    assert t <= 63.0


def test_high_ues_relief():
    t = _resolve_ml_threshold(66.0, False, "BULL", 82, 69)
    assert t == 63.0


if __name__ == "__main__":
    test_drift_softer_in_bull()
    test_lower_third_sweet_spot_relief()
    test_high_ues_relief()
    print("gate_ml_threshold_ok")
