#!/usr/bin/env python3
"""LRE-3.2 sub-stage classification — 3A/3B/4A/4B/4X rebuild."""
from __future__ import annotations

from typing import Dict, List, Tuple

from lre_3_1_filters import (
    prior_return_pct,
    supply_exhaustion_signals,
    upper_wick_ratio,
    vol_ratio,
)
from egx_liquidity_rotation_engine import compression_days, drawdown_from_high

TRADE_SUBSTAGES = frozenset({"3B", "4A", "4B"})
MONITOR_SUBSTAGES = frozenset({"3A"})
EXCLUDE_SUBSTAGES = frozenset({"4X"})

SUBSTAGE_LABELS = {
    "3A": "Early_Absorption",
    "3B": "Confirmed_Absorption",
    "4A": "Compression_Near_Edge",
    "4B": "Controlled_Pre_Ignition",
    "4X": "False_Pre_Ignition",
}


def _range_high_distance_pct(bars: List[dict], idx: int, lookback: int = 40) -> float:
    """% distance below lookback high (0 = at high)."""
    sl = bars[max(0, idx - lookback):idx + 1]
    hi = max(b["high"] for b in sl if b["high"])
    c = bars[idx]["close"]
    if not hi or not c or hi <= 0:
        return 100.0
    return (hi - c) / hi * 100


def _volume_gradual(bars: List[dict], idx: int) -> bool:
    if idx < 5:
        return False
    vrs = [vol_ratio(bars, i, 20) for i in range(idx - 4, idx + 1)]
    return vrs[-1] >= vrs[0] * 1.05 and max(vrs) - min(vrs) < 1.5


def _one_day_spike(bars: List[dict], idx: int) -> bool:
    vz = vol_ratio(bars, idx, 20)
    if idx < 1:
        return vz > 3.5
    prev = bars[idx - 1]["volume"] or 1
    return vz > 3.0 and (bars[idx]["volume"] or 0) > prev * 2.0


def is_4x(bars: List[dict], idx: int, row: dict) -> Tuple[bool, List[str]]:
    reasons = []
    bar = bars[idx]
    vz20 = float(row.get("vol_ratio_20") or vol_ratio(bars, idx, 20))
    stop_prone = float(row.get("stop_prone_score") or 0)
    move20 = float(row.get("move_from_low_20d_pct") or 0)
    prior20 = float(row.get("prior_20d_return_pct") or 0)
    comp = int(row.get("compression_days") or compression_days(bars, idx))
    uw = upper_wick_ratio(bar)

    if stop_prone >= 45:
        reasons.append("stop_prone_high")
    if _one_day_spike(bars, idx):
        reasons.append("one_day_spike")
    if uw > 0.55 and vz20 > 2.0:
        reasons.append("upper_wick_dominance")
    if move20 >= 10 or prior20 >= 12:
        reasons.append("extended_move")
    if comp < 12 and vz20 > 2.5:
        reasons.append("weak_base_spike")
    return len(reasons) >= 2 or (stop_prone >= 55), reasons


def classify_substage(bars: List[dict], idx: int, row: dict) -> Tuple[str, dict]:
    """Return sub_stage code and detail flags."""
    stage = int(row.get("stage") or 0)
    sp_count = int(row.get("supply_exhaustion_count") or 0)
    sp_detail = row.get("supply_exhaustion_detail") or {}
    vz20 = float(row.get("vol_ratio_20") or vol_ratio(bars, idx, 20))
    comp = int(row.get("compression_days") or compression_days(bars, idx))
    move20 = float(row.get("move_from_low_20d_pct") or 0)
    prior20 = float(row.get("prior_20d_return_pct") or 0)
    stop_prone = float(row.get("stop_prone_score") or 0)
    dist_hi = _range_high_distance_pct(bars, idx, 40)
    dd60 = drawdown_from_high(bars, idx, 60) * 100

    detail = {
        "legacy_stage": stage,
        "compression_days": comp,
        "vol_ratio_20": vz20,
        "dist_from_high_pct": round(dist_hi, 2),
        "supply_signals": sp_count,
        "stop_prone": stop_prone,
    }

    if stage not in (3, 4):
        return f"S{stage}", detail

    is_false, false_reasons = is_4x(bars, idx, row)
    if is_false and stage == 4:
        detail["4x_reasons"] = false_reasons
        return "4X", detail

    if stage == 3:
        confirmed = (
            sp_count >= 2
            and sp_detail.get("green_red_asymmetry")
            and (
                sp_detail.get("lower_wick_absorption")
                or sp_detail.get("recovery_after_pressure")
                or sp_detail.get("green_vol_gt_red")
            )
            and 1.3 <= vz20 <= 3.5
            and move20 < 8
        )
        if confirmed:
            return "3B", detail
        early = (
            vz20 >= 1.15
            and move20 < 6
            and dist_hi >= 3
            and dd60 >= 2
            and sp_count >= 1
        )
        if early:
            return "3A", detail
        if stop_prone >= 50:
            return "4X", detail
        return "3A", detail

    # stage 4
    near_edge = dist_hi <= 6 and comp >= 15 and move20 < 10
    controlled = (
        sp_count >= 2
        and stop_prone < 45
        and move20 < 10
        and prior20 < 12
        and 1.3 <= vz20 <= 3.5
        and comp >= 15
        and sp_detail.get("green_red_asymmetry")
    )
    if is_false:
        detail["4x_reasons"] = false_reasons
        return "4X", detail
    if controlled:
        return "4B", detail
    if near_edge and comp >= 12:
        return "4A", detail
    if stop_prone >= 40 or _one_day_spike(bars, idx):
        return "4X", detail
    return "4A", detail


def substage_tradeable(sub: str) -> bool:
    return sub in TRADE_SUBSTAGES


def substage_monitoring(sub: str) -> bool:
    return sub in MONITOR_SUBSTAGES
