#!/usr/bin/env python3
"""Unified loader for discovery_atom_registry + discovery_ml_manifest.json."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
MANIFEST_PATH = ROOT / "data" / "discovery_ml_manifest.json"


def load_ml_manifest(params: dict | None = None) -> dict:
    if params and params.get("discovery_ml_manifest"):
        return params["discovery_ml_manifest"]
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_registry_seeds(params: dict | None = None) -> dict:
    """Return quant-compatible seed dict from validated registry + manifest."""
    manifest = load_ml_manifest(params)
    if manifest.get("priority_atoms") or manifest.get("penalize_atoms"):
        return {
            "priority_atoms": list(manifest.get("priority_atoms") or []),
            "penalize_atoms": list(manifest.get("penalize_atoms") or []),
            "boost_atoms": list(manifest.get("priority_atoms") or manifest.get("boost_atoms") or []),
            "seed_pairs": list(manifest.get("seed_pairs") or []),
            "hard_negative_filters": list(manifest.get("hard_negative_filters") or []),
            "feature_store_cols": list(manifest.get("feature_store_cols") or []),
            "universe_gate": manifest.get("universe_gate") or {},
            "source": "discovery_ml_manifest",
        }

    if not DB_PATH.exists():
        return manifest or {}

    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT atom_id, regime_filter, backtest_wr, backtest_lift, boost_weight,
                   penalize_weight, hard_negative, ml_feature_col
            FROM discovery_atom_registry
            WHERE status = 'validated'
            ORDER BY backtest_lift DESC, backtest_wr DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return manifest or {}
    db.close()

    priority, penalize, pairs, hard_neg, feat_cols = [], [], [], [], []
    for r in rows:
        aid = r["atom_id"]
        if r["hard_negative"]:
            hard_neg.append({"atom": aid, "min_wr": 0.12})
            penalize.append(aid)
        elif (r["backtest_lift"] or 0) >= 1.05:
            priority.append(aid)
        elif (r["backtest_wr"] or 0) < 15 and (r["backtest_n"] or 0) >= 40:
            penalize.append(aid)
        if r["ml_feature_col"]:
            feat_cols.append(r["ml_feature_col"])

    # dedupe
    priority = list(dict.fromkeys(priority))
    penalize = list(dict.fromkeys(penalize))
    return {
        "priority_atoms": priority,
        "penalize_atoms": penalize,
        "boost_atoms": priority,
        "seed_pairs": pairs,
        "hard_negative_filters": hard_neg,
        "feature_store_cols": list(dict.fromkeys(feat_cols)),
        "universe_gate": manifest.get("universe_gate") or {},
        "source": "discovery_atom_registry",
        "n_validated": len(rows),
    }


def hard_negative_symbols(manifest: dict | None = None) -> set[str]:
    """Symbols to down-weight in ML training from ml_error_miner."""
    m = manifest or load_ml_manifest()
    syms = set()
    for item in m.get("hard_negative_symbols") or []:
        if isinstance(item, str):
            syms.add(item)
        elif isinstance(item, dict) and item.get("symbol"):
            syms.add(item["symbol"])
    return syms
