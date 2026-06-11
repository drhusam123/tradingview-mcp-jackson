#!/usr/bin/env python3
"""Smoke tests for hypothesis_sandbox_bridge."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "scripts/python/hypothesis_sandbox_bridge.py"


def load():
    spec = importlib.util.spec_from_file_location("hsb", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_atoms():
    mod = load()
    atoms = mod._extract_atoms("lower_third close with vol_2_5 sweet spot", "ACCUMULATION")
    assert "lower_third_close" in atoms
    assert "vol_2_5_3" in atoms


def test_run_writes_json(tmp_path=None):
    mod = load()
    out = mod.run({"merge_feedback": False})
    assert out["success"] is True
    assert (ROOT / "data/hypothesis_sandbox_bridge_last.json").exists()


if __name__ == "__main__":
    test_extract_atoms()
    test_run_writes_json()
    print(json.dumps({"ok": True, "tests": 2}))
