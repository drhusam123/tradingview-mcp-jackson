#!/usr/bin/env python3
"""MED-0 — Path profile engine (MFE/MAE/stops per horizon)."""
from __future__ import annotations

from typing import Dict, List

from med_common import HORIZONS, forward_path


def compute_path_profiles(bars: List[dict], idx: int) -> Dict[int, dict]:
    out = {}
    for h in HORIZONS:
        p = forward_path(bars, idx, h)
        out[h] = {
            "mfe": p.get("mfe"),
            "mae": p.get("mae"),
            "path_quality": p.get("path_quality"),
            "stop6": p.get("stop6", 0),
            "stop8": p.get("stop8", 0),
            "stop10": p.get("stop10", 0),
            "late_mover": p.get("late_mover", 0),
        }
    return out
