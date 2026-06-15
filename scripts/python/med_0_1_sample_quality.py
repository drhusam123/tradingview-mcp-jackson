#!/usr/bin/env python3
"""MED-1 — Sample quality + bootstrap confidence."""
from __future__ import annotations

import random
from statistics import median
from typing import Dict, List, Optional

from med_common import EPS, clip, pf_from_returns, sector_concentration, top10_dominance


def time_dispersion(dates: List[str]) -> float:
    if len(dates) < 2:
        return 0.5
    unique = len(set(dates))
    return clip(unique / max(len(dates), 1) * 2, 0, 1)


def bootstrap_confidence(rets: List[float], n_boot: int = 200) -> float:
    if len(rets) < 10:
        return 0.3
    pf_hits = 0
    med_hits = 0
    for _ in range(n_boot):
        sample = [rets[random.randrange(len(rets))] for _ in range(len(rets))]
        pf = pf_from_returns(sample, 0.01)
        if pf and pf > 1.3:
            pf_hits += 1
        if median(sample) > 0:
            med_hits += 1
    return 0.5 * pf_hits / n_boot + 0.5 * med_hits / n_boot


def compute_sample_quality(
    n: int,
    symbols: List[str],
    sectors: List[str],
    dates: Optional[List[str]] = None,
    rets: Optional[List[float]] = None,
    liquidity_flags: Optional[List[float]] = None,
) -> float:
    t10 = top10_dominance(symbols)
    sc = sector_concentration(sectors)
    td = time_dispersion(dates or symbols)
    bc = bootstrap_confidence(rets or [0.0] * max(n, 1))
    liq = mean_liq = 0.75
    if liquidity_flags:
        liq = sum(liquidity_flags) / len(liquidity_flags)
    return clip(
        min(1.0, n / 40) * (1 - t10) * (1 - sc) * td * bc * liq,
        0, 1,
    )


def adjusted_edge(raw_edge: float, sample_quality: float) -> float:
    return raw_edge * sample_quality
