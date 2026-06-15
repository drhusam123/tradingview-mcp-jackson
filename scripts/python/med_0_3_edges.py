#!/usr/bin/env python3
"""MED-0.3 — 4-bucket condition keys + Bayesian edge shrinkage."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from med_common import (
    HORIZONS, PRIMARY_H, PRIMARY_TH, THRESHOLDS,
    pf_from_returns, sector_concentration, top10_dominance,
)
from med_0_1_conditional_edges import lookup_edge, persist_edges

BAYES_ALPHA = 2.0
BAYES_BETA = 8.0
PRIOR_HIT = BAYES_ALPHA / (BAYES_ALPHA + BAYES_BETA)


def bayesian_hit_rate(hits: int, n: int, alpha: float = BAYES_ALPHA, beta: float = BAYES_BETA) -> float:
    return (hits + alpha) / (n + alpha + beta)


def build_condition_key_v2(math_f: dict, dist: dict, lre: dict, mde: dict, regime_ctx: dict) -> str:
    sub = math_f.get("lre_sub_stage") or lre.get("lre_sub_stage") or ""
    stage = int(math_f.get("lre_stage") or lre.get("stage") or 0)
    if sub == "4X":
        lre_b = "LRE_REJECT"
    elif stage >= 6:
        lre_b = "LRE_LATE"
    elif stage >= 3 or math_f.get("lre_gate_passed"):
        lre_b = "LRE_GATE"
    elif lre:
        lre_b = "LRE_EARLY"
    else:
        lre_b = "LRE_NONE"

    if math_f.get("mde_gate_passed") or mde.get("mde_gate_passed"):
        mde_b = "MDE_PASS"
    elif mde:
        mde_b = "MDE_OFF"
    else:
        mde_b = "MDE_NONE"

    se = math_f.get("se_rank", math_f.get("stored_energy", 0))
    if math_f.get("hidden_energy_flag"):
        math_b = "HIDDEN"
    elif se >= 0.70:
        math_b = "SE_HIGH"
    elif math_f.get("absorption_score", 0) >= 0.30:
        math_b = "ABSORB"
    elif dist.get("shift_score", 0) >= 0.45:
        math_b = "DIST_SHIFT"
    else:
        math_b = "BASE"

    reg = (regime_ctx or {}).get("regime_label", "NEUTRAL")
    if reg in ("BULL", "BULLISH"):
        reg_b = "REG_BULL"
    elif reg in ("BEAR", "BEARISH"):
        reg_b = "REG_BEAR"
    else:
        reg_b = "REG_NEUTRAL"

    return f"{lre_b}|{mde_b}|{math_b}|{reg_b}"


def time_dispersion(dates: List[str]) -> float:
    if len(dates) < 2:
        return 0.5
    unique = len(set(dates))
    return min(1.0, unique / max(len(dates), 1) * 2)


def aggregate_edges_v2(
    rows: List[dict],
    asof_date: str,
    window_mode: str = "expanding",
    min_n_display: int = 30,
) -> List[dict]:
    grouped: Dict[Tuple[str, int, float], List[dict]] = defaultdict(list)
    for r in rows:
        ck = r["condition_key"]
        for h in HORIZONS:
            fr = r.get(f"r_{h}")
            if fr is None:
                continue
            for th in THRESHOLDS:
                grouped[(ck, h, th)].append({
                    "symbol": r["symbol"],
                    "sector": r.get("sector", "Unknown"),
                    "trade_date": r.get("trade_date", ""),
                    "return": fr,
                    "hit": int(fr >= th),
                    "stop8": r.get(f"stop8_{h}", 0),
                })

    edges = []
    for (ck, h, th), items in grouped.items():
        if len(items) < 5:
            continue
        rets = [x["return"] for x in items]
        hits = sum(x["hit"] for x in items)
        n = len(items)
        p = bayesian_hit_rate(hits, n)
        wins = [r for r in rets if r > 0.01]
        losses = [abs(r) for r in rets if r <= 0.01]
        avg_w = mean(wins) if wins else 0.0
        avg_l = mean(losses) if losses else 0.0
        cost = 0.015
        exp = p * avg_w - (1 - p) * avg_l - cost
        syms = [x["symbol"] for x in items]
        secs = [x["sector"] for x in items]
        dates = [x["trade_date"] for x in items]
        t10 = top10_dominance(syms)
        sc = sector_concentration(secs)
        td = time_dispersion(dates)
        sq = min(1.0, n / 40) * (1 - t10) * (1 - sc) * td
        if n < min_n_display or td < 0.4:
            sq *= 0.5
        edges.append({
            "asof_date": asof_date,
            "condition_key": ck,
            "horizon": h,
            "threshold": th,
            "n": n,
            "hit_rate": p,
            "hit_rate_raw": hits / n,
            "avg_return": mean(rets),
            "median_return": median(rets),
            "avg_win": avg_w,
            "avg_loss": avg_l,
            "expectancy": exp,
            "pf_100": pf_from_returns(rets, 0.01),
            "pf_150": pf_from_returns(rets, 0.015),
            "pf_200": pf_from_returns(rets, 0.02),
            "stop8": mean(x["stop8"] for x in items),
            "top10_dominance": t10,
            "sector_concentration": sc,
            "sample_quality": sq,
            "time_dispersion": td,
            "window_mode": window_mode,
        })
    return edges


def build_edge_lookup(edges: List[dict]) -> Dict[tuple, dict]:
    return {(e["condition_key"], e["horizon"], e["threshold"]): e for e in edges}


def lookup_edge_fast(
    lookup: Dict[tuple, dict],
    condition_key: str,
    h: int = PRIMARY_H,
    th: float = PRIMARY_TH,
) -> Optional[dict]:
    return lookup.get((condition_key, h, th))


def index_hist_by_ck(hist_rows: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    for r in hist_rows:
        out[r["condition_key"]].append(r)
    return dict(out)


def load_edges_from_db(conn, asof_date: str) -> List[dict]:
    rows = conn.execute(
        """
        SELECT asof_date, condition_key, horizon, threshold, n, hit_rate, avg_return,
               median_return, avg_win, avg_loss, expectancy, pf_100, pf_150, pf_200,
               stop8, top10_dominance, sector_concentration, sample_quality, window_mode
        FROM med_conditional_edge_tables
        WHERE asof_date=? AND horizon=? AND abs(threshold-?)<1e-6
        """,
        (asof_date, PRIMARY_H, PRIMARY_TH),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "build_condition_key_v2",
    "bayesian_hit_rate",
    "aggregate_edges_v2",
    "lookup_edge",
    "lookup_edge_fast",
    "build_edge_lookup",
    "index_hist_by_ck",
    "load_edges_from_db",
    "persist_edges",
    "PRIOR_HIT",
]
