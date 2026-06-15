#!/usr/bin/env python3
"""MED-0 — State vector + mathematical fields per symbol/day."""
from __future__ import annotations

import math
from statistics import mean
from typing import Dict, List, Optional

from med_common import (
    EPS, clip, dollar_volume, ret_n, sf, sigmoid, vol_ratio, zscore_hist,
)


def _clv(bar: dict) -> float:
    h, l, c = bar["high"], bar["low"], bar["close"]
    if not h or not l or not c or h <= l:
        return 0.5
    return (c - l) / (h - l)


def _wick_lower(bar: dict) -> float:
    h, l, o, c = bar["high"], bar["low"], bar["open"], bar["close"]
    if not h or not l or h <= l:
        return 0.0
    return (min(o or c, c) - l) / (h - l + EPS)


def _wick_upper(bar: dict) -> float:
    h, l, o, c = bar["high"], bar["low"], bar["open"], bar["close"]
    if not h or not l or h <= l:
        return 0.0
    return (h - max(o or c, c)) / (h - l + EPS)


def _atr_pct(bars: List[dict], idx: int, n: int = 14) -> float:
    if idx < n:
        return 0.0
    trs = []
    for i in range(idx - n + 1, idx + 1):
        h, l = bars[i]["high"], bars[i]["low"]
        pc = bars[i - 1]["close"] if i > 0 else bars[i]["close"]
        if h and l and pc:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c = bars[idx]["close"]
    return mean(trs) / c if trs and c else 0.0


def _base_duration(bars: List[dict], idx: int, band: float = 0.06, lookback: int = 40) -> int:
    if idx < 5:
        return 0
    sl = bars[max(0, idx - lookback):idx + 1]
    hi = max(b["high"] for b in sl if b["high"])
    lo = min(b["low"] for b in sl if b["low"])
    if not hi or not lo or hi <= 0:
        return 0
    rng = (hi - lo) / hi
    return lookback if rng <= band else max(0, lookback - 10)


def _green_red_asymmetry(bars: List[dict], idx: int, n: int = 10) -> float:
    sl = bars[max(0, idx - n + 1):idx + 1]
    greens, reds = [], []
    for b in sl:
        o, c = b["open"], b["close"]
        if not o or not c:
            continue
        ch = (c - o) / o
        if c >= o:
            greens.append(ch)
        else:
            reds.append(abs(ch))
    gr = mean(greens) if greens else 0.0
    rr = mean(reds) if reds else 0.0
    return gr / (rr + EPS)


def compute_math_fields(
    bars: List[dict],
    idx: int,
    lre_ctx: Optional[dict] = None,
    mde_ctx: Optional[dict] = None,
    regime_ctx: Optional[dict] = None,
) -> Optional[dict]:
    if idx < MIN_BARS_LOCAL or idx >= len(bars):
        return None
    bar = bars[idx]
    c = bar["close"]
    if not c or c <= 0:
        return None

    lre_ctx = lre_ctx or {}
    mde_ctx = mde_ctx or {}

    r1, r3, r5 = ret_n(bars, idx, 1), ret_n(bars, idx, 3), ret_n(bars, idx, 5)
    r10, r20, r40 = ret_n(bars, idx, 10), ret_n(bars, idx, 20), ret_n(bars, idx, 40)

    vr20, vr60 = vol_ratio(bars, idx, 20), vol_ratio(bars, idx, 60)
    dv = dollar_volume(bar)
    dv_hist = [dollar_volume(b) for b in bars[max(0, idx - 120):idx + 1]]
    vol_hist = [b["volume"] or 0 for b in bars[max(0, idx - 120):idx + 1]]
    vr_hist = [vol_ratio(bars, i, 20) for i in range(max(20, idx - 120), idx + 1)]

    volume_z_20 = zscore_hist(vol_hist, bar["volume"] or 0, 20)
    volume_z_60 = zscore_hist(vol_hist, bar["volume"] or 0, 60)
    dollar_volume_z = zscore_hist(dv_hist, dv, 60)

    atr14 = _atr_pct(bars, idx, 14)
    atr_hist = [_atr_pct(bars, i, 14) for i in range(max(14, idx - 120), idx + 1)]
    atr_pctile = sum(1 for a in atr_hist if a <= atr14) / max(len(atr_hist), 1)

    ranges = []
    for i in range(max(0, idx - 120), idx + 1):
        h, l, cl = bars[i]["high"], bars[i]["low"], bars[i]["close"]
        if h and l and cl and cl > 0:
            ranges.append((h - l) / cl)
    rng20 = mean(ranges[-20:]) if len(ranges) >= 20 else (ranges[-1] if ranges else 0)
    rng120 = mean(ranges) if ranges else rng20
    range_tightness = 1.0 - clip(rng20 / (rng120 + EPS), 0, 1)

    base_dur = _base_duration(bars, idx)
    base_dur_norm = min(base_dur / 40.0, 1.0)
    C = (1.0 - atr_pctile) * base_dur_norm * range_tightness

    vol_persist = sum(1 for i in range(max(0, idx - 9), idx + 1)
                      if vol_ratio(bars, i, 20) > 1.0) / 10.0
    V = clip(vr20, 1.0, 3.5) / 3.5 * vol_persist

    clv_v = _clv(bar)
    lw = _wick_lower(bar)
    gra = _green_red_asymmetry(bars, idx, 10)
    rec_speed = max(0.0, r1) if r1 < 0 else clip(r1 / 0.03, 0, 1)
    A = 0.35 * clv_v + 0.25 * lw + 0.20 * clip(gra, 0, 2) / 2 + 0.20 * rec_speed

    extended = clip(
        0.50 * (1 if r20 > 0.15 else 0)
        + 0.30 * (1 if r40 > 0.25 else 0)
        + 0.20 * (1 if r20 > 0.12 else 0),
        0, 1,
    )
    stored_energy = C * V * A * (1.0 - extended)

    prev_c = bars[idx - 1]["close"] if idx > 0 else c
    ret1 = c / prev_c - 1.0 if prev_c else 0.0
    ac_raw = math.log1p(dv) / (EPS + abs(ret1))
    ac_hist = []
    for i in range(max(1, idx - 60), idx + 1):
        pc, cc = bars[i - 1]["close"], bars[i]["close"]
        if pc and cc:
            ri = cc / pc - 1.0
            ac_hist.append(math.log1p(dollar_volume(bars[i])) / (EPS + abs(ri)))
    ac_z = zscore_hist(ac_hist, ac_raw, 60) if ac_hist else 0.0

    med_abs_ret = mean(abs(bars[i]["close"] / bars[i - 1]["close"] - 1)
                       for i in range(max(1, idx - 60), idx + 1)
                       if bars[i - 1]["close"] and bars[i]["close"]) or 0.01
    not_red = 1.0 - clip(abs(ret1) * (1 if ret1 < 0 else 0) / (med_abs_ret + EPS), 0, 1)
    absorption_score = sigmoid(ac_z) * clv_v * not_red

    price_impact = abs(ret1) / math.log1p(dv + 1)
    pi_hist = []
    for i in range(max(1, idx - 60), idx + 1):
        pc, cc = bars[i - 1]["close"], bars[i]["close"]
        if pc and cc:
            pi_hist.append(abs(cc / pc - 1) / math.log1p(dollar_volume(bars[i]) + 1))
    impact_z = zscore_hist(pi_hist, price_impact, 60) if pi_hist else 0.0

    med_dv60 = mean(dv_hist[-60:]) if len(dv_hist) >= 5 else dv
    med_ar60 = med_abs_ret
    friction = med_dv60 / (med_ar60 + EPS)
    low_float_pump = int(impact_z >= 1.5 and med_dv60 < mean(dv_hist) * 0.5 and abs(ret1) > 0.04)
    artifact_risk = int(lre_ctx.get("artifact_risk") or 0)
    liq_fitness_raw = med_dv60 * (1 - low_float_pump) * (1 - artifact_risk * 0.5)

    mass = math.log1p(dv)
    velocity = r5
    momentum = mass * velocity
    mom_prev = 0.0
    if idx >= 5:
        dv5 = dollar_volume(bars[idx - 5])
        mom_prev = math.log1p(dv5) * ret_n(bars, idx, 5) if idx >= 5 else 0
    force = momentum - mom_prev
    force_hist = []
    for i in range(max(5, idx - 60), idx + 1):
        dvi = dollar_volume(bars[i])
        force_hist.append(math.log1p(dvi) * ret_n(bars, i, 5) - (
            math.log1p(dollar_volume(bars[i - 5])) * ret_n(bars, i, 5) if i >= 5 else 0))
    force_z = zscore_hist(force_hist, force, 60) if force_hist else 0.0
    displacement = abs(r5)
    stored_pressure = force_z * (1 - clip(displacement / 0.10, 0, 1)) * clip(liq_fitness_raw / 1e8, 0, 1)

    dist_hi = max(b["high"] for b in bars[max(0, idx - 60):idx + 1] if b["high"])
    dist_base = (dist_hi - c) / dist_hi if dist_hi else 0
    crowding_raw = (
        zscore_hist([ret_n(bars, i, 5) for i in range(max(5, idx - 60), idx + 1)], r5, 60)
        + zscore_hist([ret_n(bars, i, 20) for i in range(max(20, idx - 60), idx + 1)], r20, 60)
        + zscore_hist([_wick_upper(bars[i]) for i in range(max(0, idx - 60), idx + 1)], _wick_upper(bar), 60)
        + zscore_hist(vr_hist, vr20, 60)
        + dist_base * 2
        + extended
    )
    crowding_penalty = clip((crowding_raw + 2) / 4, 0, 1)

    lre_stage = int(lre_ctx.get("stage") or 0)
    sub = (lre_ctx.get("sub_stage") or lre_ctx.get("lre_sub_stage") or "")
    chase_risk_flag = int(
        r20 > 0.15 or r40 > 0.25 or _wick_upper(bar) > 0.6
    )
    do_not_chase = int(chase_risk_flag)

    hidden_energy = int(volume_z_20 >= 2.0 and abs(r5) <= 0.05 and extended <= 0.3)

    regime_ctx = regime_ctx or {}
    sector_strength_val = regime_ctx.get("sector_strength", 0.5)
    breadth_state_val = regime_ctx.get("breadth_state", 0.5)
    regime_state_val = regime_ctx.get("regime_state", 0.5)
    regime_fit_val = regime_ctx.get("regime_fit", 0.5)

    return {
        "r_5": r5, "r_10": r10, "r_20": r20, "r_40": r40,
        "volume_ratio_20": vr20, "volume_z_20": volume_z_20,
        "volume_z_60": volume_z_60, "dollar_volume_z": dollar_volume_z,
        "atr_percentile": atr_pctile, "range_compression": range_tightness,
        "base_duration": base_dur, "clv": clv_v,
        "lower_wick_ratio": lw, "upper_wick_ratio": _wick_upper(bar),
        "green_red_asymmetry": gra, "recovery_speed": rec_speed,
        "absorption_coefficient": ac_raw, "absorption_score": absorption_score,
        "price_impact": price_impact, "impact_z": impact_z,
        "liquidity_friction": friction, "stored_energy": stored_energy,
        "physics_force": force_z, "stored_pressure_physics": stored_pressure,
        "liquidity_fitness_raw": liq_fitness_raw,
        "crowding_score": crowding_penalty, "crowding_penalty": crowding_penalty,
        "extended_penalty": extended, "hidden_energy_flag": hidden_energy,
        "chase_risk": chase_risk_flag, "do_not_chase": do_not_chase,
        "lre_stage": lre_stage, "lre_sub_stage": sub,
        "lre_eps": sf(lre_ctx.get("explosion_potential"), 0),
        "lre_bucket": lre_ctx.get("feed_tier") or "",
        "lre_gate_passed": int(lre_ctx.get("stage") or 0) >= 3,
        "lre_mde_confluence": int((lre_ctx.get("dual_gate_type") or "") == "LRE_MDE_CONFLUENCE"),
        "mde_score": sf(mde_ctx.get("mde_score"), 0),
        "mde_gate_passed": int(mde_ctx.get("mde_gate_passed") or 0),
        "already_exploded_penalty": extended,
        "red_damage_ratio": 1.0 - not_red,
        "sector_strength": sector_strength_val,
        "breadth_state": breadth_state_val,
        "regime_state": regime_state_val,
        "regime_fit": regime_fit_val,
        "artifact_risk": artifact_risk,
        "low_float_pump": low_float_pump,
    }


MIN_BARS_LOCAL = 130
