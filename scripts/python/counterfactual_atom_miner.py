#!/usr/bin/env python3
"""
Counterfactual Atom Miner — learning_loop → quant_discovery seed atoms.

Reads data/learning_loop_last.json (and optional proof forensic) to produce
priority boost/penalize atoms and seed pairs for walk-forward discovery.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEARNING_PATH = ROOT / "data" / "learning_loop_last.json"
OUTPUT_PATH = ROOT / "data" / "counterfactual_atoms_last.json"

# Map counterfactual block reasons → quant atoms (TRADING_LESSONS aligned)
REASON_TO_PENALIZE: dict[str, list[str]] = {
    "upper_third_close": ["upper_close", "very_upper_close"],
    "volume_chase": ["vol_gt3", "vol_gt5", "vol_3_8"],
    "post_breakout_volume": ["vol_lt1_5", "high20_break"],
    "post_breakout_vol_collapse": ["vol_lt1_5", "high20_break"],
    "behavioral_volatile": ["range_gt9pct", "vol_gt5", "mom5_pos_lt15"],
    "behavioral_dormant": ["vol_lt1_5", "mom20_pos"],
    "explosive_rsi": ["rsi_gt55_lt75", "rsi_50_75"],
    "explosive_ultra_thin_repeat": ["vol_lt1_5", "vol_1_5_3"],
    "false_signal_rate": ["high20_break", "upper_close", "vol_gt3"],
    "repeat_ultra_loser": ["vol_gt3", "very_upper_close"],
}

REASON_TO_BOOST: dict[str, list[str]] = {
    "upper_third_close": ["lower_third_close", "vol_2_5_3", "low20_retest"],
    "volume_chase": ["vol_2_5_3", "lower_third_close", "not_extended_3d"],
    "post_breakout_volume": ["vol_2_5_3", "lower_third_close", "mom3_soft_pullback"],
    "post_breakout_vol_collapse": ["vol_2_5_3", "low20_retest", "bb_squeeze_low35"],
    "behavioral_volatile": ["lower_third_close", "range_lt4pct", "vol_2_5_3"],
    "false_signal_rate": ["lower_third_close", "low20_retest", "bb_squeeze_low35"],
    "repeat_ultra_loser": ["lower_third_close", "vol_2_5_3", "not_near_ath"],
    "explosive_ultra_thin_repeat": ["vol_2_5_3", "lower_third_close", "low20_retest"],
}

CORE_SEED_PAIRS = [
    ["lower_third_close", "vol_2_5_3"],
    ["lower_third_close", "low20_retest"],
    ["vol_2_5_3", "low20_retest"],
    ["lower_third_close", "bb_squeeze_low35"],
]


def _load_learning() -> dict:
    if not LEARNING_PATH.exists():
        return {}
    try:
        return json.loads(LEARNING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mine(params: dict | None = None) -> dict:
    params = params or {}
    learning = _load_learning()
    cf = learning.get("counterfactual") or {}
    autopsy = learning.get("loss_autopsy") or {}

    block_counts: dict[str, int] = dict(cf.get("block_reason_counts") or {})
    for flag, n in (autopsy.get("flag_counts") or {}).items():
        if n >= 2:
            block_counts[flag] = block_counts.get(flag, 0) + int(n)

    boost_atoms: set[str] = set()
    penalize_atoms: set[str] = set()
    reason_weights: list[dict] = []

    for reason, count in sorted(block_counts.items(), key=lambda x: -x[1]):
        if count < 2:
            continue
        boosts = REASON_TO_BOOST.get(reason, [])
        penals = REASON_TO_PENALIZE.get(reason, [])
        for a in boosts:
            boost_atoms.add(a)
        for a in penals:
            penalize_atoms.add(a)
        if boosts or penals:
            reason_weights.append({
                "reason": reason,
                "count": count,
                "boost": boosts,
                "penalize": penals,
            })

    # Residual losses still passing filters → stronger lower_third / vol sweet spot
    residual = cf.get("loss_symbols_still_passing") or []
    if len(residual) >= 2:
        boost_atoms.update(["lower_third_close", "vol_2_5_3", "low20_retest"])
        penalize_atoms.update(["upper_close", "vol_gt3"])

    # Projected WR lift from counterfactual
    wr_delta = cf.get("wr_delta")
    if wr_delta is not None and float(wr_delta) >= 10:
        boost_atoms.update(["lower_third_close", "vol_2_5_3"])

    seed_pairs = list(CORE_SEED_PAIRS)
    priority_atoms = sorted(boost_atoms)
    if "lower_third_close" in boost_atoms and "vol_2_5_3" in boost_atoms:
        seed_pairs.insert(0, ["lower_third_close", "vol_2_5_3"])

    # TV microstructure atoms as quant seeds when available
    tv_path = ROOT / "data" / "tv_discovery_features_last.json"
    if tv_path.exists():
        try:
            tv = json.loads(tv_path.read_text(encoding="utf-8"))
            if (tv.get("atom_counts") or {}).get("ABSORPTION_BAR", 0) >= 3:
                boost_atoms.add("lower_third_close")
                seed_pairs.append(["lower_third_close", "vol_2_4"])
        except Exception:
            pass

    report = {
        "success": True,
        "at": datetime.utcnow().isoformat(),
        "source": str(LEARNING_PATH.name),
        "n_historical": cf.get("n_historical"),
        "actual_wr": cf.get("actual_wr"),
        "projected_wr": cf.get("projected_wr"),
        "wr_delta": wr_delta,
        "boost_atoms": sorted(boost_atoms),
        "penalize_atoms": sorted(penalize_atoms),
        "priority_atoms": priority_atoms[:12],
        "seed_pairs": seed_pairs[:8],
        "reason_weights": reason_weights[:12],
        "residual_losses": len(residual),
    }

    OUTPUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(mine(p), indent=2, ensure_ascii=False))
