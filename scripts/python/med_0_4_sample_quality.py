#!/usr/bin/env python3
"""MED-0.4 — hierarchical sample quality (edge table + cohort bootstrap)."""
from __future__ import annotations

from typing import List, Optional

from med_0_1_sample_quality import bootstrap_confidence, compute_sample_quality
from med_common import clip


def cohort_bootstrap_quality(
    ck_rows: List[dict],
    horizon: int = 20,
) -> float:
    if not ck_rows:
        return 0.25
    rets = [
        x.get(f"r_{horizon}")
        for x in ck_rows
        if x.get(f"r_{horizon}") is not None
    ]
    if len(rets) < 5:
        return 0.25
    return bootstrap_confidence(rets)


def hierarchical_sample_quality(
    edge: Optional[dict],
    ck_rows: List[dict],
    horizon: int = 20,
) -> float:
    edge_sq = float((edge or {}).get("sample_quality") or 0.0)
    edge_n = int((edge or {}).get("n") or 0)

    if ck_rows:
        cohort_sq = compute_sample_quality(
            n=edge_n or len(ck_rows),
            symbols=[x["symbol"] for x in ck_rows],
            sectors=[x.get("sector", "Unknown") for x in ck_rows],
            dates=[x.get("trade_date", "") for x in ck_rows],
            rets=[
                x.get(f"r_{horizon}")
                for x in ck_rows
                if x.get(f"r_{horizon}") is not None
            ],
            liquidity_flags=[x.get("liquidity_fitness", 0.5) for x in ck_rows],
        )
        boot = cohort_bootstrap_quality(ck_rows, horizon)
        cohort_sq = 0.6 * cohort_sq + 0.4 * boot
    else:
        cohort_sq = edge_sq if edge_sq > 0 else 0.25

    if edge_sq <= 0 and edge_n >= 30:
        edge_sq = min(0.55, 0.25 + edge_n / 200)

    sq = 0.6 * edge_sq + 0.4 * cohort_sq
    if edge_n >= 30:
        sq = min(1.0, sq * 1.08)
    elif edge_n >= 20:
        sq = min(1.0, sq * 1.03)
    if edge_n >= 100:
        sq = max(sq, 0.32)
    elif edge_n >= 50:
        sq = max(sq, 0.18)
    return clip(sq, 0, 1)
