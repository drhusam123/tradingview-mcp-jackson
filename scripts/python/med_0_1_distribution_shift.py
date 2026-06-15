#!/usr/bin/env python3
"""MED-0 — Distribution shift field (PSI + Wasserstein-like + KS)."""
from __future__ import annotations

import math
from statistics import mean
from typing import Dict, List

from med_common import EPS, clip, ret_n


def _quantile(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = int(q * (len(s) - 1))
    return s[min(i, len(s) - 1)]


def distribution_shift(bars: List[dict], idx: int, math_f: dict) -> dict:
    if idx < 130:
        return {"psi": 0, "w_shift": 0, "ks": 0, "shift_score": 0, "behavior_changed": 0}

    def feat_series(fn, n: int) -> List[float]:
        out = []
        for i in range(max(0, idx - n + 1), idx + 1):
            try:
                out.append(float(fn(i)))
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    f20_rets = feat_series(lambda i: ret_n(bars, i, 1), 20)
    f120_rets = feat_series(lambda i: ret_n(bars, i, 1), 120)
    f20_clv = feat_series(lambda i: (
        (bars[i]["close"] - bars[i]["low"]) / (bars[i]["high"] - bars[i]["low"] + EPS)
        if bars[i]["high"] and bars[i]["low"] and bars[i]["high"] > bars[i]["low"] else 0.5
    ), 20)
    f120_clv = feat_series(lambda i: (
        (bars[i]["close"] - bars[i]["low"]) / (bars[i]["high"] - bars[i]["low"] + EPS)
        if bars[i]["high"] and bars[i]["low"] and bars[i]["high"] > bars[i]["low"] else 0.5
    ), 120)

    def psi(p: List[float], q: List[float], bins: int = 8) -> float:
        if len(p) < 5 or len(q) < 5:
            return 0.0
        lo = min(min(p), min(q))
        hi = max(max(p), max(q))
        if hi <= lo:
            return 0.0
        step = (hi - lo) / bins
        s = 0.0
        for b in range(bins):
            lo_b, hi_b = lo + b * step, lo + (b + 1) * step
            pb = sum(1 for x in p if lo_b <= x < hi_b or (b == bins - 1 and x == hi_b)) / len(p)
            qb = sum(1 for x in q if lo_b <= x < hi_b or (b == bins - 1 and x == hi_b)) / len(q)
            s += (pb - qb) * math.log((pb + EPS) / (qb + EPS))
        return abs(s)

    psi_val = psi(f20_rets, f120_rets)
    qs = [0.1, 0.25, 0.5, 0.75, 0.9]
    w_shift = mean(abs(_quantile(f20_rets, q) - _quantile(f120_rets, q)) for q in qs)

    def ecdf(vals: List[float], x: float) -> float:
        return sum(1 for v in vals if v <= x) / max(len(vals), 1)

    grid = sorted(set(f20_rets + f120_rets))
    ks = max(abs(ecdf(f20_rets, x) - ecdf(f120_clv if False else f120_rets, x)) for x in grid) if grid else 0

    raw = 0.4 * psi_val + 0.3 * w_shift + 0.3 * ks
    shift_score = clip(raw / 2.0, 0, 1)

    r10, r20 = math_f.get("r_10", 0), math_f.get("r_20", 0)
    behavior = int(shift_score >= 0.65 and abs(r10) < 0.07 and abs(r20) < 0.12)

    return {
        "psi": psi_val, "w_shift": w_shift, "ks": ks,
        "shift_score": shift_score, "behavior_changed": behavior,
    }
