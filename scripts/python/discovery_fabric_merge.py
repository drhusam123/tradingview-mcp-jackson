#!/usr/bin/env python3
"""
Discovery Fabric Merge — all miners + JSON → discovery_atom_registry.
Also writes discovery_data_catalog.json snapshot.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

import sys
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from discovery_domain_miners import run_all_miners

try:
    from discovery_data_hydrate import enumerate_tables as _enum_tables
except ImportError:
    _enum_tables = None

CATALOG = {
    "at": None,
    "layers": {
        "L0": ["ohlcv_history", "stock_universe"],
        "L1": ["indicators_cache"],
        "L2": ["pine_analytics", "tv_discovery_features", "closing_pressure_daily", "market_breadth_enhanced",
               "cross_market_regime", "dom_snapshots"],
        "L3": ["scans", "setup_performance"],
        "L4": ["feature_store", "explosion_predictions", "forward_test_predictions",
               "tsfresh_daily", "survival_exit_profile", "conformal_scores"],
        "L5": ["final_signals"],
        "L6": ["arbitration_decisions"],
        "L7": ["opportunity_score_v2"],
        "L7.5": ["quant_discovery_rules"],
        "L8": ["recommendation_outcomes", "bayesian_wr"],
        "L9": ["sandbox_hypotheses", "alpha_rankings", "grid_runs", "walkforward_results"],
        "L10": ["notification_delivery_audit"],
        "L11": ["discovery_atom_registry", "discovery_fabric_runs"],
    },
    "miners": [
        "price_structure_miner", "post_breakout_vol_miner", "entry_gap_miner", "closing_pressure_miner",
        "indicators_confluence_miner", "tv_microstructure", "breadth_regime_miner", "cross_market_miner",
        "dom_liquidity_miner", "outcome_weighted_quant", "bayesian_wr_miner", "counterfactual_atoms",
        "arbitration_veto_miner", "hypothesis_sandbox_bridge", "alpha_universe_gate",
        "ml_error_miner", "spectral_atom_bridge", "tsfresh_pattern_miner", "survival_conformal_miner",
        "regime_conditional_sweep", "indicator_divergence_miner", "markov_transition_miner",
        "sector_rotation_miner", "grid_winner_miner", "dmids_structural_miner", "scans_setup_miner",
        "explosive_moves_miner", "market_experience_miner", "anti_law_miner", "stock_profiles_miner",
        "meta_label_miner", "validation_results_miner", "law_competition_miner",
    ],
    "json_artifacts": [
        "counterfactual_atoms_last.json", "regime_conditional_sweep_last.json",
        "hypothesis_sandbox_bridge_last.json", "tv_microstructure_last.json",
        "discovery_ml_manifest.json",
    ],
}


def ensure_tables(db):
    mig = ROOT / "scripts/migrations/004_discovery_fabric.sql"
    if mig.exists():
        db.executescript(mig.read_text(encoding="utf-8"))
    db.commit()


def upsert_atoms(db, atoms: list[dict]) -> int:
    sql = """
    INSERT INTO discovery_atom_registry (
        atom_id, source_layer, source_table, source_miner, condition_json,
        regime_filter, status, backtest_wr, backtest_n, backtest_lift,
        boost_weight, penalize_weight, ml_feature_col, hard_negative,
        proposed_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
    ON CONFLICT(atom_id, regime_filter) DO UPDATE SET
        source_layer=excluded.source_layer,
        source_table=excluded.source_table,
        source_miner=excluded.source_miner,
        condition_json=excluded.condition_json,
        boost_weight=excluded.boost_weight,
        penalize_weight=excluded.penalize_weight,
        ml_feature_col=COALESCE(excluded.ml_feature_col, discovery_atom_registry.ml_feature_col),
        hard_negative=excluded.hard_negative,
        backtest_wr=COALESCE(excluded.backtest_wr, discovery_atom_registry.backtest_wr),
        backtest_n=COALESCE(excluded.backtest_n, discovery_atom_registry.backtest_n),
        status=CASE WHEN discovery_atom_registry.status='validated' THEN 'validated' ELSE excluded.status END,
        updated_at=datetime('now')
    """
    n = 0
    for a in atoms:
        regime = a.get("regime_filter") or ""
        db.execute(sql, (
            a["atom_id"], a["source_layer"], a.get("source_table"), a.get("source_miner"),
            a.get("condition_json"), regime, a.get("status", "proposed"),
            a.get("backtest_wr"), a.get("backtest_n"), a.get("backtest_lift"),
            a.get("boost_weight", 1.0), a.get("penalize_weight", 1.0),
            a.get("ml_feature_col"), a.get("hard_negative", 0),
        ))
        n += 1
    db.commit()
    return n


def run(params: dict | None = None):
    params = params or {}
    atoms, extras = run_all_miners()
    now = datetime.now(timezone.utc).isoformat()

    if not DB_PATH.exists():
        return {"success": False, "error": "NO_DB"}

    db = sqlite3.connect(DB_PATH, timeout=60)
    ensure_tables(db)
    n_upserted = upsert_atoms(db, atoms)
    db.execute(
        "INSERT INTO discovery_fabric_runs (stage, n_proposed, detail_json) VALUES (?,?,?)",
        ("merge", n_upserted, json.dumps({"miners": len(set(a.get("source_miner") for a in atoms))})),
    )
    db.commit()
    db.close()

    CATALOG["at"] = now
    if _enum_tables and DB_PATH.exists():
        db2 = sqlite3.connect(DB_PATH, timeout=30)
        tables = _enum_tables(db2)
        db2.close()
        production = [t for t in tables if t.get("has_data") and t.get("layer") not in ("OPS", "OTHER")]
        by_layer: dict[str, list] = {}
        for t in production:
            by_layer.setdefault(t["layer"], []).append(t["table"])
        CATALOG["total_tables"] = len(tables)
        CATALOG["production_tables_with_data"] = len(production)
        CATALOG["layers_full"] = {k: sorted(v) for k, v in sorted(by_layer.items())}
        CATALOG["table_stats"] = tables
    (DATA / "discovery_data_catalog.json").write_text(json.dumps(CATALOG, indent=2), encoding="utf-8")
    payload = {
        "success": True,
        "at": now,
        "n_proposed": n_upserted,
        "n_unique_atoms": len({a["atom_id"] for a in atoms}),
        "miners_run": len(set(a.get("source_miner") for a in atoms)),
        "extras": extras,
        "catalog": "data/discovery_data_catalog.json",
    }
    (DATA / "discovery_fabric_merge_last.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), ensure_ascii=False))
