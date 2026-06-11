#!/usr/bin/env python3
"""Smoke tests for regime_conditional_sweep."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "scripts/python/regime_conditional_sweep.py"


def load():
    spec = importlib.util.spec_from_file_location("rcs", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_atom_fns_present():
    mod = load()
    assert "lower_third_close" in mod.ATOM_FNS
    assert "vol_2_5_3" in mod.ATOM_FNS


def test_score_pair_min_n():
    mod = load()
    examples = [
        {"hit": True, "realized": 3.0, "close_pos": 0.2, "vol_ratio": 2.7,
         "low20_retest": True, "pct_from_ath": 0.1, "bb_width_pct": 0.2,
         "range_pct": 0.03, "ret3": 0.05, "high20_break": False},
    ] * 50
    r = mod.score_pair(examples, "lower_third_close", "vol_2_5_3", min_n=40)
    assert r is not None
    assert r["n"] == 50


if __name__ == "__main__":
    test_atom_fns_present()
    test_score_pair_min_n()
    print(json.dumps({"ok": True, "tests": 2}))
