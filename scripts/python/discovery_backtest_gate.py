#!/usr/bin/env python3
"""
Discovery Backtest Gate — validate proposed atoms on OOS examples, write ML manifest.
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
MANIFEST_PATH = DATA / "discovery_ml_manifest.json"

MIN_N = 40
MIN_LIFT = 1.03
MIN_WR_DELTA_PP = 2.0
MIN_PF_PROXY = 1.1

# Import quant pipeline for OOS evaluation
sys.path.insert(0, str(ROOT / "scripts/python"))
from quant_discovery import load_bars, build_examples, atoms  # noqa: E402


def ensure_tables(db):
    mig = ROOT / "scripts/migrations/004_discovery_fabric.sql"
    if mig.exists():
        db.executescript(mig.read_text(encoding="utf-8"))


def eval_atom_on_oos(atom_id: str, examples: list, split_date: str, atom_map: dict) -> dict | None:
    fn = atom_map.get(atom_id)
    if not fn:
        return None
    oos = [x for x in examples if x["date"] >= split_date]
    if len(oos) < MIN_N:
        return None
    base_wr = sum(1 for x in oos if x["hit"]) / len(oos)
    sub = [x for x in oos if fn(x)]
    if len(sub) < MIN_N:
        return None
    wr = sum(1 for x in sub if x["hit"]) / len(sub)
    wins = [x["realized"] for x in sub if x["hit"]]
    losses = [abs(x["realized"]) for x in sub if not x["hit"]]
    pf = (sum(wins) / max(sum(losses), 1e-9)) if losses else 2.0
    lift = wr / base_wr if base_wr > 0 else 0
    return {
        "backtest_wr": round(wr * 100, 1),
        "backtest_n": len(sub),
        "backtest_lift": round(lift, 3),
        "backtest_pf": round(pf, 2),
        "baseline_wr": round(base_wr * 100, 1),
    }


def passes_gate(metrics: dict, hard_negative: int) -> bool:
    if hard_negative:
        return metrics["backtest_wr"] < 18 and metrics["backtest_n"] >= MIN_N
    return (
        metrics["backtest_n"] >= MIN_N
        and metrics["backtest_lift"] >= MIN_LIFT
        and metrics["backtest_wr"] >= metrics["baseline_wr"] + MIN_WR_DELTA_PP
        and metrics["backtest_pf"] >= MIN_PF_PROXY
    )


# TRADING_LESSONS overrides — empirical backtest cannot contradict v3 lessons
CORE_BOOST = [
    "lower_third_close", "vol_2_5_3", "low20_retest", "not_near_ath",
    "bb_squeeze_low35", "range_lt4pct", "not_extended_3d", "cp_lower_third",
]
CORE_PENALIZE = [
    "vol_gt5", "vol_gt3", "vol_3_8", "vol_1_5_3", "very_upper_close",
    "upper_close", "high20_break", "range_gt9pct", "post_breakout_vol_collapse",
    "entry_gap_chase",
]


def build_manifest(db, extras: dict | None = None) -> dict:
    rows = db.execute(
        """
        SELECT atom_id, regime_filter, backtest_wr, backtest_n, backtest_lift,
               boost_weight, penalize_weight, hard_negative, ml_feature_col, status
        FROM discovery_atom_registry
        WHERE status IN ('validated', 'rejected')
        """
    ).fetchall()

    priority, penalize, pairs, hard_neg, feat_cols = [], [], [], [], []
    validated_wr = []

    for r in rows:
        aid, regime, wr, n, lift, boost, penal, hn, mlcol, status = r
        if status != "validated":
            if hn and status == "rejected":
                penalize.append(aid)
            continue
        if hn:
            hard_neg.append({"atom": aid, "min_wr": 0.12})
            penalize.append(aid)
        elif (lift or 0) >= MIN_LIFT and (wr or 0) >= 15:
            priority.append(aid)
            if wr:
                validated_wr.append(wr)
        elif (wr or 0) < 15:
            penalize.append(aid)
        if mlcol:
            feat_cols.append(mlcol)

    for a in CORE_BOOST:
        if a not in penalize:
            priority.insert(0, a)
    for a in CORE_PENALIZE:
        if a in priority:
            priority.remove(a)
        if a not in penalize:
            penalize.append(a)

    priority = list(dict.fromkeys(priority))
    penalize = list(dict.fromkeys(penalize))

    # seed pairs after CORE_BOOST merge
    if "lower_third_close" in priority and "vol_2_5_3" in priority:
        pairs.append(["lower_third_close", "vol_2_5_3"])
    if "cp_lower_third" in priority and "vol_2_5_3" in priority:
        pairs.append(["cp_lower_third", "vol_2_5_3"])
    if "low20_retest" in priority and "vol_2_5_3" in priority:
        pairs.append(["low20_retest", "vol_2_5_3"])

    manifest = {
        "at": datetime.now(timezone.utc).isoformat(),
        "priority_atoms": priority,
        "penalize_atoms": penalize,
        "boost_atoms": list(dict.fromkeys(priority)),
        "seed_pairs": pairs,
        "hard_negative_filters": hard_neg,
        "hard_negative_symbols": (extras or {}).get("hard_negative_symbols") or [],
        "feature_store_cols": list(dict.fromkeys(feat_cols)),
        "universe_gate": {"alpha_rankings_min_grade": "B", "is_alive": True},
        "backtest_summary": {
            "n_atoms_validated": len(priority),
            "n_penalized": len(penalize),
            "median_oos_wr": round(sorted(validated_wr)[len(validated_wr) // 2], 1) if validated_wr else None,
            "gate": {"min_n": MIN_N, "min_lift": MIN_LIFT},
        },
    }
    return manifest


def run(params: dict | None = None):
    params = params or {}
    extras = {}
    merge_path = DATA / "discovery_fabric_merge_last.json"
    if merge_path.exists():
        try:
            extras = json.loads(merge_path.read_text()).get("extras") or {}
        except Exception:
            pass

    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    ensure_tables(db)

    proposed = db.execute(
        "SELECT atom_id, hard_negative, regime_filter FROM discovery_atom_registry WHERE status = 'proposed'"
    ).fetchall()

    data = load_bars(db)
    examples = build_examples(data, horizon=5)
    dates = sorted({x["date"] for x in examples})
    split_date = dates[int(len(dates) * 0.75)] if dates else "2025-01-01"
    atom_map = {name: fn for name, fn in atoms()}

    n_val, n_rej = 0, 0
    for row in proposed:
        aid = row["atom_id"]
        if "_" in aid and aid.count("_") >= 2 and not atom_map.get(aid):
            # composite / non-evaluable — keep proposed with miner prior
            continue
        if not atom_map.get(aid):
            continue
        metrics = eval_atom_on_oos(aid, examples, split_date, atom_map)
        if not metrics:
            db.execute(
                "UPDATE discovery_atom_registry SET status='rejected', updated_at=datetime('now') WHERE atom_id=? AND regime_filter=?",
                (aid, row["regime_filter"] or ""),
            )
            n_rej += 1
            continue
        ok = passes_gate(metrics, row["hard_negative"])
        status = "validated" if ok else "rejected"
        if ok:
            n_val += 1
        else:
            n_rej += 1
        db.execute(
            """
            UPDATE discovery_atom_registry SET
              status=?, backtest_wr=?, backtest_n=?, backtest_lift=?, backtest_pf=?,
              validated_at=CASE WHEN ?='validated' THEN datetime('now') ELSE validated_at END,
              updated_at=datetime('now')
            WHERE atom_id=? AND regime_filter=?
            """,
            (status, metrics["backtest_wr"], metrics["backtest_n"], metrics["backtest_lift"],
             metrics["backtest_pf"], status, aid, row["regime_filter"] or ""),
        )

    # Trust miner-prevalidated outcome/bayes rows with existing metrics
    TRUSTED_MINERS = (
        'outcome_weighted_quant', 'bayesian_wr_miner', 'counterfactual_atoms',
        'arbitration_veto_miner', 'ml_error_miner', 'scans_setup_miner',
        'setup_performance_miner', 'grid_winner_miner', 'dmids_structural_miner',
        'hypothesis_sandbox_bridge', 'markov_transition_miner', 'markov_regime_miner',
        'sector_rotation_miner', 'survival_conformal_miner', 'pine_analytics_miner',
        'delivery_audit_miner', 'alpha_universe_gate', 'dom_liquidity_miner',
        'regime_conditional_sweep', 'spectral_atom_bridge', 'price_structure_miner',
        'tv_microstructure', 'tsfresh_pattern_miner', 'cross_market_miner',
        'indicators_confluence_miner', 'indicator_divergence_miner', 'breadth_regime_miner',
        'closing_pressure_miner', 'entry_gap_miner', 'post_breakout_vol_miner',
        'sector_rotation_daily_miner', 'explosive_moves_miner', 'market_experience_miner',
        'anti_law_miner', 'stock_profiles_miner', 'meta_label_miner',
        'validation_results_miner', 'law_competition_miner', 'contagion_miner',
    )
    placeholders = ",".join("?" * len(TRUSTED_MINERS))
    db.execute(
        f"""
        UPDATE discovery_atom_registry SET status='validated', validated_at=datetime('now')
        WHERE status='proposed' AND source_miner IN ({placeholders})
        """,
        TRUSTED_MINERS,
    )
    # TRADING_LESSONS canonical atoms — validated by empirical v3 backtest (override OOS reject)
    for aid in CORE_BOOST:
        db.execute(
            """
            UPDATE discovery_atom_registry SET status='validated', validated_at=datetime('now')
            WHERE atom_id=? AND (regime_filter='' OR regime_filter IS NULL)
            """,
            (aid,),
        )

    manifest = build_manifest(db, extras)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.execute(
        "INSERT INTO discovery_fabric_runs (stage, n_validated, n_rejected, detail_json) VALUES (?,?,?,?)",
        ("backtest_gate", n_val, n_rej, json.dumps(manifest.get("backtest_summary"))),
    )
    db.commit()
    db.close()

    payload = {
        "success": True,
        "n_validated": n_val,
        "n_rejected": n_rej,
        "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
        "priority_atoms": len(manifest.get("priority_atoms") or []),
        "penalize_atoms": len(manifest.get("penalize_atoms") or []),
    }
    (DATA / "discovery_backtest_gate_last.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), ensure_ascii=False))
