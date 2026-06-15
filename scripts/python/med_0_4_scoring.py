#!/usr/bin/env python3
"""MED-0.4 — dual-track scoring, thresholds, HC bucket gate + daily cap."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from med_0_3_calibration import (
    MedThresholds,
    chase_risk,
    combined_risk,
    compute_p_tail,
    failure_risk_score,
    rank_normalize,
)
from med_0_1_sample_quality import bootstrap_confidence
from med_common import clip

HC_DAILY_CAP = 8
INDEX_PREFIXES = ("EGX",)


def is_index_symbol(symbol: str) -> bool:
    s = (symbol or "").upper()
    return any(s.startswith(p) for p in INDEX_PREFIXES)


@dataclass
class MedThresholdsV4(MedThresholds):
    med_score_rank_p85: float = 0.85
    med_score_rank_p70: float = 0.70
    med_score_rank_p50: float = 0.50


def build_thresholds_v4(day_rows: List[dict]) -> MedThresholdsV4:
    fs = [r.get("failure_similarity", 0) for r in day_rows]
    cp = [r.get("crowding_score", 0) for r in day_rows]
    sq = [r.get("sample_quality", 0) for r in day_rows]
    se = [r.get("se_rank", 0) for r in day_rows]
    scores = [r.get("med_score", 0) for r in day_rows]
    p_tails = sorted(r.get("p_tail", 0) for r in day_rows)
    risks = [combined_risk(r.get("failure_similarity", 0), r.get("crowding_score", 0)) for r in day_rows]

    def p(vals: List[float], q: float, default: float) -> float:
        if not vals:
            return default
        s = sorted(vals)
        i = min(len(s) - 1, max(0, int(q * (len(s) - 1))))
        return float(s[i])

    th = MedThresholdsV4(
        se_rank_p70=p(se, 0.70, 0.70),
        failure_p75=p(fs, 0.75, 0.40),
        crowding_p75=p(cp, 0.75, 0.60),
        med_score_p85=p(scores, 0.85, 75.0) if scores else 75.0,
        p_tail_p70=p_tails[min(len(p_tails) - 1, int(0.7 * max(len(p_tails) - 1, 0)))] if p_tails else 0.15,
        risk_p40=p(risks, 0.40, 0.35),
        quality_p50=p(sq, 0.50, 0.25),
        sq_p60=p(sq, 0.60, 0.30),
        med_score_rank_p85=0.85,
        med_score_rank_p70=0.70,
        med_score_rank_p50=0.50,
    )
    return th


def _edge_raw(row: dict, edge: Optional[dict], p_analogue: float) -> float:
    p_cond = (edge or {}).get("hit_rate", 0) or 0
    p_tail = compute_p_tail(p_cond, p_analogue)
    exp_ret = max((edge or {}).get("expectancy", 0) or (edge or {}).get("avg_return", 0) or 0, 0)
    bc = float(row.get("bootstrap_confidence") or 0.3)
    return 0.50 * p_tail + 0.30 * clip(exp_ret, 0, 0.20) + 0.20 * bc


def _math_raw(row: dict) -> float:
    return (
        0.25 * row.get("se_rank", 0)
        + 0.25 * row.get("absorption_rank", 0)
        + 0.20 * row.get("dist_rank", 0)
        + 0.20 * row.get("pq_rank", 0)
        + 0.10 * row.get("liq_rank", 0)
    )


def apply_dual_scores(rows: List[dict], edges_by_sym: Optional[dict] = None) -> None:
    if not rows:
        return
    edge_raws = []
    math_raws = []
    for r in rows:
        edge = (edges_by_sym or {}).get(r["symbol"])
        edge_raws.append(_edge_raw(r, edge, r.get("analogue_p_tail", 0)))
        math_raws.append(_math_raw(r))

    score_pop = []
    for er, mr in zip(edge_raws, math_raws):
        es = 100 * rank_normalize(er, edge_raws)
        ms = 100 * rank_normalize(mr, math_raws)
        v4 = 0.65 * es + 0.35 * ms
        score_pop.append(v4)

    for r, er, mr, v4 in zip(rows, edge_raws, math_raws, score_pop):
        r["med_edge_score"] = round(100 * rank_normalize(er, edge_raws), 2)
        r["med_math_score"] = round(100 * rank_normalize(mr, math_raws), 2)
        r["med_score"] = round(v4, 2)
        r["med_score_rank"] = rank_normalize(v4, score_pop)
        r["med_core"] = v4 / 100.0


def assign_bucket_v4(row: dict, edge: Optional[dict], th: MedThresholdsV4) -> str:
    fs = row.get("failure_similarity", 0)
    cp = row.get("crowding_score", 0)
    sq = row.get("sample_quality", 0)
    lf = row.get("liquidity_fitness", 0)
    p_tail = row.get("p_tail", 0)
    risk = combined_risk(fs, cp)
    n = int((edge or {}).get("n", 0))
    ms_rank = float(row.get("med_score_rank", 0))

    if chase_risk(row):
        return "MED_DO_NOT_CHASE"
    if fs >= th.failure_p75 or cp >= th.crowding_p75:
        return "MED_FAILURE_WARNING"
    if n < 20 or sq < th.quality_p50 * 0.35:
        return "MED_INSUFFICIENT_SAMPLE"

    if (
        not is_index_symbol(row.get("symbol", ""))
        and ms_rank >= th.med_score_rank_p85
        and p_tail >= th.p_tail_p70
        and failure_risk_score(fs) <= th.risk_p40
        and sq >= th.sq_p60
        and n >= 30
        and lf >= 0.20
    ):
        return "MED_HIGH_CONVICTION_RESEARCH"

    if (
        ms_rank >= th.med_score_rank_p70
        and n >= 20
        and p_tail >= th.p_tail_p70 * 0.85
        and (edge or {}).get("hit_rate", 0) >= 0.20
    ):
        return "MED_POSITIVE_EXPECTANCY"

    if ms_rank >= th.med_score_rank_p50:
        return "MED_MONITOR"
    return "MED_INSUFFICIENT_SAMPLE"


def apply_hc_daily_cap(rows: List[dict], cap: int = HC_DAILY_CAP) -> int:
    """Keep top `cap` HC by med_score; downgrade overflow to POSITIVE_EXPECTANCY."""
    hc = [r for r in rows if r.get("med_bucket") == "MED_HIGH_CONVICTION_RESEARCH"]
    if len(hc) <= cap:
        return len(hc)
    hc.sort(key=lambda x: (x.get("med_score", 0), x.get("p_tail", 0)), reverse=True)
    keep = {r["symbol"] for r in hc[:cap]}
    for r in rows:
        if r.get("med_bucket") == "MED_HIGH_CONVICTION_RESEARCH" and r["symbol"] not in keep:
            r["med_bucket"] = "MED_POSITIVE_EXPECTANCY"
            r["hypothetical_boost"] = 2.0
    return len(keep)


def attach_bootstrap_confidence(rows: List[dict], ck_rows_map: dict) -> None:
    for r in rows:
        ck = r.get("condition_key", "")
        hist = ck_rows_map.get(ck, [])
        rets = [x.get("r_20") for x in hist if x.get("r_20") is not None]
        r["bootstrap_confidence"] = bootstrap_confidence(rets) if len(rets) >= 5 else 0.3
