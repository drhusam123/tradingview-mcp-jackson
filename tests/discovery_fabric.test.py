#!/usr/bin/env python3
"""Smoke tests for Discovery Fabric L11."""
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "egx_trading.db"
import sys
sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_and_gate():
    merge = _load("merge", "scripts/python/discovery_fabric_merge.py")
    gate = _load("gate", "scripts/python/discovery_backtest_gate.py")
    m = merge.run({})
    assert m["success"]
    assert m["n_proposed"] > 10
    g = gate.run({})
    assert g["success"]
    manifest_path = ROOT / "data" / "discovery_ml_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert "priority_atoms" in manifest


def test_registry_table():
    if not DB.exists():
        return
    db = sqlite3.connect(DB)
    n = db.execute("SELECT COUNT(*) FROM discovery_atom_registry").fetchone()[0]
    db.close()
    assert n > 0


def test_manifest_loader():
    loader = _load("loader", "scripts/python/discovery_manifest_loader.py")
    seeds = loader.load_registry_seeds()
    assert isinstance(seeds, dict)


def test_all_miners_present():
    miners = _load("miners", "scripts/python/discovery_domain_miners.py")
    atoms, _ = miners.run_all_miners()
    miner_ids = {a.get("source_miner") for a in atoms}
    required = {
        "cross_market_miner", "tsfresh_pattern_miner", "survival_conformal_miner",
        "dom_liquidity_miner", "entry_gap_miner", "closing_pressure_miner",
    }
    missing = required - miner_ids
    assert not missing, f"missing miners: {missing}"


def test_validate_atoms_cli():
    bt = _load("bt", "scripts/python/backtest_engine.py")
    if not DB.exists():
        return
    r = bt.validate_discovery_atoms(atom_ids=["lower_third_close", "vol_gt5"])
    assert r["success"]
    assert "atoms" in r


if __name__ == "__main__":
    test_manifest_loader()
    test_all_miners_present()
    test_validate_atoms_cli()
    test_merge_and_gate()
    test_registry_table()
    print(json.dumps({"ok": True, "tests": 5}))
