#!/usr/bin/env python3
"""LRE-3.1 filter primitives — family A DNA, stop-prone, supply, volume, modes."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from egx_liquidity_rotation_engine import (
    PRIOR_EXPLODED_PCT,
    atr_pct,
    clv,
    compression_days,
    drawdown_from_high,
    liquidity_fitness,
    prior_exploded,
    score_symbol_daily,
    supply_exhaustion_score,
    vol_ratio,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FP_PATH = DATA / "lre_pre_explosion_fingerprints.json"

FP_WEIGHTS = {"T-1": 0.25, "T-3": 0.20, "T-5": 0.20, "T-10": 0.15, "T-20": 0.12, "T-40": 0.08}
FP_OFFSETS = {"T-1": 1, "T-3": 3, "T-5": 5, "T-10": 10, "T-20": 20, "T-40": 40}


def load_fingerprints() -> dict:
    return json.loads(FP_PATH.read_text(encoding="utf-8"))


def _bar_features(bars: List[dict], idx: int) -> dict:
    return {
        "vol_ratio": vol_ratio(bars, idx, 20),
        "clv": clv(bars[idx]),
        "compression_days": compression_days(bars, idx),
    }


def family_similarity(bars: List[dict], idx: int, fingerprints: dict, family: str) -> float:
    tmpl_fam = fingerprints.get(family)
    if not tmpl_fam:
        return 0.0
    dist = 0.0
    wsum = 0.0
    for key, w in FP_WEIGHTS.items():
        off = FP_OFFSETS[key]
        i = idx - off
        if i < 0:
            continue
        tmpl = tmpl_fam.get(key, {})
        feat = _bar_features(bars, i)
        t_vr = tmpl.get("avg_vol_ratio") or 1.0
        t_clv = tmpl.get("avg_clv") or 0.4
        t_comp = tmpl.get("avg_compression_days") or 25.0
        d_vr = abs(feat["vol_ratio"] - t_vr) / max(t_vr, 0.4)
        d_clv = abs(feat["clv"] - t_clv)
        d_comp = abs(feat["compression_days"] - t_comp) / max(t_comp, 10)
        dist += w * (0.38 * d_vr + 0.28 * d_clv + 0.34 * d_comp)
        wsum += w
    if wsum <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100 - (dist / wsum) * 42)), 1)


def artifact_similarity(bars: List[dict], idx: int) -> float:
    """F-family proxy: spike without base."""
    vr = vol_ratio(bars, idx, 20)
    comp = compression_days(bars, idx)
    bar = bars[idx]
    rng = (bar["high"] - bar["low"]) / bar["close"] if bar["close"] else 0
    uw = (bar["high"] - max(bar["open"], bar["close"])) / max(bar["high"] - bar["low"], 1e-9)
    score = 0.0
    if vr > 4:
        score += 30
    if comp < 8:
        score += 25
    if vr > 2.5 and rng < 0.02:
        score += 20
    if uw > 0.55 and vr > 2:
        score += 15
    liq, art = liquidity_fitness(bars, idx)
    if art:
        score += 25
    return min(100.0, score)


def low_float_pump_similarity(bars: List[dict], idx: int, fingerprints: dict) -> float:
    return family_similarity(bars, idx, fingerprints, "C_low_float_pump")


def prior_return_pct(bars: List[dict], idx: int, n: int) -> float:
    if idx < n:
        return 0.0
    p0, p1 = bars[idx - n]["close"], bars[idx]["close"]
    if not p0 or not p1 or p0 <= 0:
        return 0.0
    return (p1 - p0) / p0 * 100


def had_extended_stage_recently(bars: List[dict], idx: int, lookback: int = 20) -> bool:
    for i in range(max(40, idx - lookback), idx):
        mv = prior_return_pct(bars, i, 20)
        if mv >= 18:
            return True
    return False


def upper_wick_ratio(bar: dict) -> float:
    h, l, o, c = bar["high"], bar["low"], bar["open"], bar["close"]
    if not h or not l or h <= l:
        return 0.0
    return (h - max(o or c, c or o)) / (h - l)


def lower_wick_ratio(bar: dict) -> float:
    h, l, o, c = bar["high"], bar["low"], bar["open"], bar["close"]
    if not h or not l or h <= l:
        return 0.0
    return (min(o or c, c or o) - l) / (h - l)


def supply_exhaustion_signals(bars: List[dict], idx: int) -> Tuple[int, dict]:
    """Count positive supply-absorption signals (need >=2)."""
    bar = bars[idx]
    sigs = {}
    sigs["lower_wick_absorption"] = lower_wick_ratio(bar) > upper_wick_ratio(bar) and clv(bar) > 0.55
    clv_hist = [clv(bars[i]) for i in range(max(0, idx - 19), idx + 1)]
    sigs["clv_above_median"] = clv(bar) > (median(clv_hist) if clv_hist else 0.5)
    greens, reds, gvol, rvol = [], [], [], []
    for i in range(max(0, idx - 9), idx + 1):
        o, c, v = bars[i]["open"], bars[i]["close"], bars[i]["volume"]
        if o and c and o > 0:
            ret = (c - o) / o
            if ret >= 0:
                greens.append(abs(ret))
                gvol.append(v or 0)
            else:
                reds.append(abs(ret))
                rvol.append(v or 0)
    sigs["red_damage_low"] = (mean(reds) if reds else 0) < (mean(greens) if greens else 1) * 0.85
    sigs["green_red_asymmetry"] = (mean(greens) if greens else 0) > (mean(reds) if reds else 0)
    sigs["green_vol_gt_red"] = (mean(gvol) if gvol else 0) > (mean(rvol) if rvol else 1)
    recovery = False
    if idx >= 3:
        lows = [bars[i]["low"] for i in range(idx - 3, idx) if bars[i]["low"]]
        if lows and bar["close"] and min(lows) > 0:
            recovery = bar["close"] >= min(lows) * 1.01
    sigs["recovery_after_pressure"] = recovery
    count = sum(1 for v in sigs.values() if v)
    return count, sigs


def stop_prone_score(bars: List[dict], idx: int, row: dict) -> float:
    score = 0.0
    comp = compression_days(bars, idx)
    atr5 = atr_pct(bars, idx, 5)
    atr20 = atr_pct(bars, idx, 20)
    atr_exp = (atr5 / atr20) if atr5 and atr20 and atr20 > 0 else 1.0
    vz20 = vol_ratio(bars, idx, 20)
    bar = bars[idx]
    rng = (bar["high"] - bar["low"]) / bar["close"] if bar["close"] else 0
    mv5 = prior_return_pct(bars, idx, 5)
    mv20 = float(row.get("move_from_low_20d_pct") or 0)
    liq, art = liquidity_fitness(bars, idx)

    if atr_exp > 1.45 and comp < 15:
        score += 18
    if rng > 0.04 and vz20 > 2.5:
        score += 15
    if mv20 > 8:
        score += 12
    if upper_wick_ratio(bar) > 0.55:
        score += 14
    if mv5 > 5:
        score += 14
    if vz20 > 3.5:
        score += 14
    if idx >= 1:
        prev_vr = vol_ratio(bars, idx - 1, 20)
        if vz20 > 2.5 and vz20 > prev_vr * 1.6 and clv(bar) < 0.45:
            score += 12
    if liq < 40 or art:
        score += 10
    if drawdown_from_high(bars, idx, 60) < 0.02 and comp < 12:
        score += 8
    return min(100.0, score)


def volume_filter_ok(bars: List[dict], idx: int) -> Tuple[bool, List[str]]:
    fails = []
    vz20 = vol_ratio(bars, idx, 20)
    vz60 = vol_ratio(bars, idx, 60) if idx >= 60 else vz20
    if vz20 < 1.3 or vz20 > 3.5:
        fails.append("vol_ratio_20_out_of_band")
    if vz60 < 1.2 or vz60 > 3.0:
        fails.append("vol_ratio_60_out_of_band")
    bar = bars[idx]
    if vz20 > 3.5 and upper_wick_ratio(bar) > 0.5 and clv(bar) < 0.4:
        fails.append("spike_upper_wick_fail")
    if idx >= 1 and vz20 > 2.8:
        yv = bars[idx - 1]["volume"] or 1
        if (bar["volume"] or 0) > yv * 2.2 and clv(bar) < 0.35:
            fails.append("one_day_spike_no_close")
    return len(fails) == 0, fails


def compression_filter_ok(bars: List[dict], idx: int, min_comp: int) -> Tuple[bool, List[str]]:
    fails = []
    comp = compression_days(bars, idx)
    if comp < min_comp:
        fails.append(f"compression_lt_{min_comp}")
    atr5 = atr_pct(bars, idx, 5)
    atr20 = atr_pct(bars, idx, 20)
    if atr5 and atr20 and atr20 > 0 and (atr20 / atr5) < 1.05:
        fails.append("no_atr_contraction")
    mv20 = prior_return_pct(bars, max(40, idx), 20) if idx >= 20 else 0
    dd = drawdown_from_high(bars, idx, 40)
    if mv20 > 12 and comp < 12:
        fails.append("extended_without_base")
    if dd < 0.02 and comp < 10:
        fails.append("at_high_no_base")
    return len(fails) == 0, fails


def calibrate_a_thresholds(conn, by_sym: dict, fingerprints: dict) -> dict:
    rows = conn.execute(
        """SELECT symbol, signal_date FROM lre_explosion_events
           WHERE family='A_long_accumulation' AND include_research=1
           ORDER BY signal_date"""
    ).fetchall()
    sims = []
    for r in rows:
        bars = by_sym.get(r["symbol"])
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == r["signal_date"]), None)
        if idx is None or idx < 45:
            continue
        sims.append(family_similarity(bars, idx, fingerprints, "A_long_accumulation"))
    if len(sims) < 30:
        return {"balanced": 52.0, "conservative": 58.0, "ultra": 65.0, "calibrated_n": len(sims)}
    sims.sort()
    n = len(sims)

    def pct(p):
        i = int(n * p / 100)
        return sims[min(i, n - 1)]

    return {
        "calibrated_n": n,
        "A_p25": round(pct(25), 1),
        "A_p40": round(pct(40), 1),
        "A_p50": round(pct(50), 1),
        "balanced": round(pct(25), 1),
        "conservative": round(pct(40), 1),
        "ultra": round(pct(50), 1),
    }


def enrich_signal(conn, sym: str, bars: List[dict], idx: int, fingerprints: dict, thresholds: dict) -> Optional[dict]:
    td = bars[idx]["date"]
    row = score_symbol_daily(conn, sym, bars, td, {})
    if not row:
        return None
    a_sim = family_similarity(bars, idx, fingerprints, "A_long_accumulation")
    f_sim = artifact_similarity(bars, idx)
    c_sim = low_float_pump_similarity(bars, idx, fingerprints)
    sp_count, sp_detail = supply_exhaustion_signals(bars, idx)
    liq, art = liquidity_fitness(bars, idx)
    vz20 = vol_ratio(bars, idx, 20)
    vz60 = vol_ratio(bars, idx, 60) if idx >= 60 else vz20
    stop_prone = stop_prone_score(bars, idx, row)
    ret20 = prior_return_pct(bars, idx, 20)
    ret40 = prior_return_pct(bars, idx, 40)
    exploded = prior_exploded(bars, idx)
    extended_hist = had_extended_stage_recently(bars, idx)
    row.update({
        "family_similarity_A": a_sim,
        "family_similarity_F": f_sim,
        "family_similarity_C": c_sim,
        "supply_exhaustion_count": sp_count,
        "supply_exhaustion_detail": sp_detail,
        "supply_exhaustion_score": row.get("supply_exhaustion"),
        "stop_prone_score": round(stop_prone, 1),
        "liquidity_fitness_score": round(liq, 1),
        "artifact_risk_score": 100 if art else max(0, f_sim),
        "vol_ratio_60": round(vz60, 2),
        "prior_20d_return_pct": round(ret20, 2),
        "prior_40d_return_pct": round(ret40, 2),
        "already_exploded": int(exploded),
        "extended_stage_recent": int(extended_hist),
        "compression_days": compression_days(bars, idx),
        "drawdown_from_high_40d": round(drawdown_from_high(bars, idx, 40) * 100, 2),
        "thresholds": thresholds,
    })
    return row


MODE_SPECS = {
    "baseline_3_0": {
        "label": "LRE-3.0 Baseline",
        "stages": {3, 4},
        "min_eps": 50,
        "min_a_sim": 0,
        "max_f_sim": 100,
        "max_c_sim": 100,
        "max_stop_prone": 100,
        "min_compression": 0,
        "min_supply_signals": 0,
        "min_liquidity": 0,
        "max_move_20d": 15,
        "max_prior_20d": 999,
        "max_prior_40d": 999,
        "require_volume_band": False,
        "require_no_extended_hist": False,
        "require_no_exploded": False,
    },
    "balanced_research": {
        "label": "Balanced Research",
        "stages": {3, 4},
        "min_eps": 55,
        "min_a_sim_key": "balanced",
        "max_f_sim": 45,
        "max_c_sim": 55,
        "max_stop_prone": 70,
        "min_compression": 12,
        "min_supply_signals": 1,
        "min_liquidity": 35,
        "max_move_20d": 12,
        "max_prior_20d": 12,
        "max_prior_40d": 20,
        "require_volume_band": True,
        "require_no_extended_hist": True,
        "require_no_exploded": True,
    },
    "conservative": {
        "label": "Conservative",
        "stages": {3, 4},
        "min_eps": 60,
        "min_a_sim_key": "conservative",
        "max_f_sim": 35,
        "max_c_sim": 45,
        "max_stop_prone": 55,
        "min_compression": 15,
        "min_supply_signals": 2,
        "min_liquidity": 42,
        "max_move_20d": 12,
        "max_prior_20d": 12,
        "max_prior_40d": 20,
        "require_volume_band": True,
        "require_no_extended_hist": True,
        "require_no_exploded": True,
    },
    "ultra_conservative": {
        "label": "Ultra Conservative",
        "stages": {4},
        "min_eps": 65,
        "min_a_sim_key": "ultra",
        "max_f_sim": 25,
        "max_c_sim": 35,
        "max_stop_prone": 40,
        "min_compression": 20,
        "min_supply_signals": 3,
        "min_liquidity": 48,
        "max_move_20d": 10,
        "max_prior_20d": 10,
        "max_prior_40d": 18,
        "require_volume_band": True,
        "require_no_extended_hist": True,
        "require_no_exploded": True,
    },
}


def mode_passes(row: dict, mode: str, thresholds: dict) -> Tuple[bool, List[str]]:
    spec = MODE_SPECS[mode]
    fails = []
    if int(row.get("stage") or 0) not in spec["stages"]:
        fails.append("stage")
    if float(row.get("explosion_potential") or 0) < spec["min_eps"]:
        fails.append("eps")
    min_a = spec.get("min_a_sim")
    if min_a is None:
        key = spec.get("min_a_sim_key")
        min_a = thresholds.get(key, 55) if key else 0
    if float(row.get("family_similarity_A") or 0) < min_a:
        fails.append("A_similarity")
    if float(row.get("family_similarity_F") or 0) > spec["max_f_sim"]:
        fails.append("F_leakage")
    if float(row.get("family_similarity_C") or 0) > spec["max_c_sim"] and float(row.get("family_similarity_C") or 0) > float(row.get("family_similarity_A") or 0):
        fails.append("C_pump_dominant")
    if float(row.get("stop_prone_score") or 0) > spec["max_stop_prone"]:
        fails.append("stop_prone")
    if int(row.get("artifact_risk") or 0):
        fails.append("artifact")
    if float(row.get("liquidity_fitness_score") or 0) < spec["min_liquidity"]:
        fails.append("liquidity")
    if float(row.get("move_from_low_20d_pct") or 0) >= spec["max_move_20d"]:
        fails.append("move_extended")
    if float(row.get("prior_20d_return_pct") or 0) >= spec["max_prior_20d"]:
        fails.append("prior_20d")
    if float(row.get("prior_40d_return_pct") or 0) >= spec["max_prior_40d"]:
        fails.append("prior_40d")
    if spec["require_no_exploded"] and int(row.get("already_exploded") or 0):
        fails.append("already_exploded")
    if spec["require_no_extended_hist"] and int(row.get("extended_stage_recent") or 0):
        fails.append("recent_stage_6_7")
    if int(row.get("supply_exhaustion_count") or 0) < spec["min_supply_signals"]:
        fails.append("supply_exhaustion")
    tags = row.get("list_tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    if "do_not_chase" in tags:
        fails.append("do_not_chase")
    return len(fails) == 0, fails
