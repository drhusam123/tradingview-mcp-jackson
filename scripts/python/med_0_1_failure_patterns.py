#!/usr/bin/env python3
"""MED-0 — Failure similarity via causal KNN on failure vectors."""
from __future__ import annotations

import math
from typing import Dict, List, Tuple, Optional

from med_common import EPS, clip, forward_return


def failure_vector(math_f: dict, dist: dict) -> List[float]:
    return [
        math_f.get("upper_wick_ratio", 0),
        math_f.get("crowding_score", 0),
        math_f.get("price_impact", 0),
        math_f.get("volume_ratio_20", 1) - 1,
        math_f.get("already_exploded_penalty", 0),
        1.0 - math_f.get("clv", 0.5),
        math_f.get("red_damage_ratio", 0),
        float(math_f.get("low_float_pump", 0)),
        1.0 if math_f.get("lre_sub_stage") == "4X" else 0.0,
        math_f.get("stored_energy", 0),
        math_f.get("absorption_score", 0),
        dist.get("shift_score", 0),
    ]


def is_failure_event(bars: List[dict], idx: int, math_f: dict, path: dict) -> bool:
    r20 = forward_return(bars, idx, 20)
    if r20 is not None and r20 <= -0.05:
        return True
    if path.get("stop8") and (path.get("mfe") or 0) < 0.05:
        return True
    if math_f.get("lre_sub_stage") == "4X":
        return True
    if math_f.get("do_not_chase") and r20 is not None and r20 <= 0:
        return True
    return False


def build_failure_index(
    history: List[Tuple[str, int, List[float], bool]],
) -> List[Tuple[str, int, List[float], bool]]:
    return history


def failure_similarity(
    vec: List[float],
    history: List[Tuple[str, int, List[float], bool]],
    k: int = 20,
    sector: Optional[str] = None,
    sector_map: Optional[dict] = None,
) -> float:
    if not history or not vec:
        return 0.0
    pool = history
    if sector and sector_map:
        same = [h for h in history if sector_map.get(h[0]) == sector]
        if len(same) >= max(5, k // 2):
            pool = same
    dists = []
    for sym, _, hv, fail in pool:
        d = sum((a - b) ** 2 for a, b in zip(vec, hv)) / max(len(vec), 1)
        dists.append((d, fail))
    dists.sort(key=lambda x: x[0])
    top = dists[:k]
    if not top:
        return 0.0
    return sum(1 for _, f in top if f) / len(top)


def scaled_vec(vec: List[float], medians: List[float], iqrs: List[float]) -> List[float]:
    out = []
    for v, med, iqr in zip(vec, medians, iqrs):
        out.append((v - med) / (iqr + EPS))
    return out
