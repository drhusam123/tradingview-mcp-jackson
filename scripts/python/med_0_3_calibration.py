#!/usr/bin/env python3
"""MED-0.3 — EGX-native calibration: ranks, buckets, med_ok, expanding score."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from med_common import clip, percentile_rank, rank_normalize


@dataclass
class MedThresholds:
    se_rank_p70: float = 0.70
    failure_p75: float = 0.75
    crowding_p75: float = 0.75
    med_score_p85: float = 0.85
    p_tail_p70: float = 0.70
    risk_p40: float = 0.40
    quality_p50: float = 0.50
    sq_p60: float = 0.60

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def chase_risk(mf: dict) -> int:
    """Price-run chase only — not crowding, not LRE stage."""
    return int(
        mf.get("r_20", 0) > 0.15
        or mf.get("r_40", 0) > 0.25
        or mf.get("upper_wick_ratio", 0) > 0.6
    )


def failure_risk_score(fs: float) -> float:
    return clip(fs, 0, 1)


def crowding_risk_score(cp: float) -> float:
    return clip(cp, 0, 1)


def combined_risk(fs: float, cp: float) -> float:
    return max(failure_risk_score(fs), crowding_risk_score(cp))


def apply_cross_section_ranks(rows: List[dict]) -> None:
    if not rows:
        return
    fields = {
        "se_rank": "stored_energy",
        "absorption_rank": "absorption_score",
        "dist_rank": "distribution_shift_score",
        "liq_rank": "liquidity_fitness",
        "pq_rank": "path_quality_20",
    }
    pops = {k: [r.get(src, 0) for r in rows] for k, src in fields.items()}
    for r in rows:
        for rank_key, src in fields.items():
            r[rank_key] = rank_normalize(r.get(src, 0), pops[rank_key])


def build_thresholds(
    hist_rows: List[dict],
    day_rows: List[dict],
    med_cores: List[float],
) -> MedThresholds:
    fs = [r.get("failure_similarity", 0) for r in day_rows]
    cp = [r.get("crowding_score", 0) for r in day_rows]
    sq = [r.get("sample_quality", 0) for r in day_rows]
    se = [r.get("se_rank", 0) for r in day_rows]
    cores = [c for c in med_cores if c is not None]

    def p(vals: List[float], q: float, default: float) -> float:
        if not vals:
            return default
        s = sorted(vals)
        i = min(len(s) - 1, max(0, int(q * (len(s) - 1))))
        return float(s[i])

    return MedThresholds(
        se_rank_p70=p(se, 0.70, 0.70),
        failure_p75=p(fs, 0.75, 0.40),
        crowding_p75=p(cp, 0.75, 0.60),
        med_score_p85=p(cores, 0.85, 0.80) if cores else 0.80,
        p_tail_p70=0.15,
        risk_p40=p([combined_risk(r.get("failure_similarity", 0), r.get("crowding_score", 0)) for r in day_rows], 0.40, 0.35),
        quality_p50=p(sq, 0.50, 0.25),
        sq_p60=p(sq, 0.60, 0.30),
    )


def med_ok_v3(mf: dict, th: MedThresholds) -> bool:
    return mf.get("se_rank", 0) >= th.se_rank_p70 or bool(mf.get("hidden_energy_flag"))


def compute_p_tail(p_cond: float, p_analogue: float) -> float:
    return 0.6 * p_cond + 0.4 * p_analogue


def compute_med_core(
    row: dict,
    edge: Optional[dict],
    p_analogue: float = 0.0,
) -> float:
    p_cond = (edge or {}).get("hit_rate", 0) or 0
    p_tail = compute_p_tail(p_cond, p_analogue)
    exp_ret = max((edge or {}).get("avg_return", 0) or 0, 0)
    se = row.get("se_rank", 0)
    ab = row.get("absorption_rank", 0)
    dist = row.get("dist_rank", 0)
    pq = row.get("pq_rank", 0)
    liq = row.get("liq_rank", 0)
    risk = combined_risk(row.get("failure_similarity", 0), row.get("crowding_score", 0))
    quality = row.get("sample_quality", 0.5) * row.get("regime_fit", 0.5)
    raw = (
        0.20 * p_tail
        + 0.15 * exp_ret
        + 0.15 * se
        + 0.10 * ab
        + 0.10 * dist
        + 0.10 * pq
        + 0.05 * liq
    )
    return raw * quality * (1 - risk * 0.5)


def expanding_percentile_score(med_core: float, history: List[float]) -> float:
    clean = sorted(h for h in history if h is not None)
    if len(clean) < 20:
        return 100 * clip(med_core * 2, 0, 1)
    return 100 * percentile_rank(clean, med_core)


def assign_bucket_v3(row: dict, edge: Optional[dict], th: MedThresholds) -> str:
    fs = row.get("failure_similarity", 0)
    cp = row.get("crowding_score", 0)
    sq = row.get("sample_quality", 0)
    lf = row.get("liquidity_fitness", 0)
    ms = row.get("med_score", 0) / 100.0
    p_tail = row.get("p_tail", 0)
    risk = combined_risk(fs, cp)
    quality = sq * row.get("regime_fit", 0.5) * clip(lf * 2, 0, 1)
    n = (edge or {}).get("n", 0)

    if chase_risk(row):
        return "MED_DO_NOT_CHASE"
    if fs >= th.failure_p75 or cp >= th.crowding_p75:
        return "MED_FAILURE_WARNING"
    if n < 20 or sq < th.quality_p50 * 0.35:
        return "MED_INSUFFICIENT_SAMPLE"
    if (
        ms >= max(th.med_score_p85, 0.55)
        and p_tail >= th.p_tail_p70
        and risk <= max(th.risk_p40, 0.45)
        and sq >= max(th.sq_p60 * 0.5, 0.08)
        and lf >= 0.25
    ):
        return "MED_HIGH_CONVICTION_RESEARCH"
    if ms >= 0.65 and (edge or {}).get("expectancy", 0) > 0 and sq >= th.quality_p50 * 0.7:
        return "MED_POSITIVE_EXPECTANCY"
    if ms >= 0.50:
        return "MED_MONITOR"
    return "MED_INSUFFICIENT_SAMPLE"


def persist_threshold_snapshots(conn, trade_date: str, th: MedThresholds) -> None:
    for metric, val in th.to_dict().items():
        conn.execute(
            """
            INSERT OR REPLACE INTO med_threshold_snapshots
            (asof_date, metric, p50, p75, p90, window_mode)
            VALUES (?,?,?,?,?,?)
            """,
            (trade_date, metric, val, val, val, "med_0_3_expanding"),
        )
    conn.commit()
