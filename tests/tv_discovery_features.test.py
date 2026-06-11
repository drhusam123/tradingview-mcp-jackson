#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
from tv_discovery_features import derive_atoms


def test_vwap_reclaim_atom():
    row = {
        "vwap": 100.0,
        "rs_score": 60.0,
        "session_bias": "ABOVE_VWAP",
        "raw_pine_data": '{"vol_ratio": 2.8, "close_position": 0.28, "trend_score": 1.2}',
    }
    out = derive_atoms(row, 101.5)
    assert "VWAP_RECLAIM" in out["atoms"]
    assert "PARTICIPATION_SHOCK" in out["atoms"]
    assert out["tv_score"] > 0


def test_absorption_proxy():
    row = {
        "raw_pine_data": '{"vol_ratio": 1.9, "close_position": 0.25, "trend_score": 0.5}',
        "rs_score": 55,
    }
    out = derive_atoms(row, 50.0)
    assert "ABSORPTION_BAR" in out["atoms"] or "CVD_BULL_DIV_PROXY" in out["atoms"]


if __name__ == "__main__":
    test_vwap_reclaim_atom()
    test_absorption_proxy()
    print("tv_discovery_features_ok")
