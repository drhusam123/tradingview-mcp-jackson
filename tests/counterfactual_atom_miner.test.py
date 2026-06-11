#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
from counterfactual_atom_miner import mine, REASON_TO_BOOST


def test_mine_from_learning_loop():
    learning = Path(__file__).resolve().parents[1] / "data" / "learning_loop_last.json"
    if not learning.exists():
        print("counterfactual_atom_miner_skip_no_data")
        sys.exit(0)
    report = mine()
    assert report["success"]
    assert "lower_third_close" in report["boost_atoms"]
    assert report["seed_pairs"]
    assert any("upper_third_close" in w["reason"] for w in report["reason_weights"])


def test_reason_maps_exist():
    assert "upper_third_close" in REASON_TO_BOOST
    assert REASON_TO_BOOST["upper_third_close"]


if __name__ == "__main__":
    test_reason_maps_exist()
    test_mine_from_learning_loop()
    print("counterfactual_atom_miner_ok")
