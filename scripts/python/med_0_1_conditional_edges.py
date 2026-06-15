#!/usr/bin/env python3
"""MED-1 — Conditional edge tables from condition keys."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from med_common import (
    DATA, HORIZONS, PRIMARY_H, PRIMARY_TH, THRESHOLDS,
    connect, ensure_med_tables, forward_return, pf_from_returns,
    sector_concentration, top10_dominance,
)


def build_condition_key(math_f: dict, dist: dict, lre: dict, mde: dict) -> str:
    parts = []
    sub = math_f.get("lre_sub_stage") or lre.get("lre_sub_stage") or ""
    stage = int(math_f.get("lre_stage") or lre.get("stage") or 0)
    if sub == "4X":
        parts.append("LRE_REJECTED_4X")
    elif sub == "4B":
        parts.append("LRE_4B_MONITOR")
    elif math_f.get("lre_mde_confluence"):
        parts.append("LRE_CONFLUENCE")
    elif math_f.get("lre_gate_passed") or stage >= 3:
        parts.append("LRE_GATE")
    elif lre:
        parts.append("LRE_CLEAN_CORE")
    else:
        parts.append("NONE")

    if math_f.get("mde_gate_passed"):
        parts.append("MDE_PASS")
    elif mde:
        parts.append("MDE_REJECT")
    else:
        parts.append("NONE")

    se = math_f.get("stored_energy", 0)
    if se >= 0.35:
        parts.append("STORED_ENERGY_HIGH")
    if math_f.get("absorption_score", 0) >= 0.45:
        parts.append("ABSORPTION_HIGH")
    if dist.get("shift_score", 0) >= 0.55:
        parts.append("DISTRIBUTION_SHIFT_HIGH")
    if math_f.get("hidden_energy_flag"):
        parts.append("HIDDEN_ENERGY")
    if math_f.get("crowding_penalty", 1) <= 0.45:
        parts.append("CROWDING_LOW")
    if math_f.get("failure_similarity", 1) <= 0.35:
        parts.append("FAILURE_LOW")
    if math_f.get("liquidity_fitness", 0) >= 0.45:
        parts.append("LIQUIDITY_OK")

    if not lre and not mde:
        parts = ["MED_ONLY"] + [p for p in parts[2:] if p != "NONE"]

    return "|".join(parts) if parts else "MED_ONLY"


def aggregate_edges(
    rows: List[dict],
    asof_date: str,
    window_mode: str = "expanding",
) -> List[dict]:
    """rows: list of {condition_key, symbol, sector, horizon returns, path}"""
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
                    "return": fr,
                    "hit": int(fr >= th),
                    "stop8": r.get(f"stop8_{h}", 0),
                })

    edges = []
    for (ck, h, th), items in grouped.items():
        if len(items) < 5:
            continue
        rets = [x["return"] for x in items]
        hits = [x["hit"] for x in items]
        wins = [r for r in rets if r > 0.01]
        losses = [abs(r) for r in rets if r <= 0.01]
        p = sum(hits) / len(hits)
        avg_w = mean(wins) if wins else 0.0
        avg_l = mean(losses) if losses else 0.0
        cost = 0.015
        exp = p * avg_w - (1 - p) * avg_l - cost
        syms = [x["symbol"] for x in items]
        secs = [x["sector"] for x in items]
        t10 = top10_dominance(syms)
        sc = sector_concentration(secs)
        sq = min(1.0, len(items) / 40) * (1 - t10) * (1 - sc)
        edges.append({
            "asof_date": asof_date,
            "condition_key": ck,
            "horizon": h,
            "threshold": th,
            "n": len(items),
            "hit_rate": p,
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
            "window_mode": window_mode,
        })
    return edges


def lookup_edge(edges: List[dict], condition_key: str, h: int = PRIMARY_H, th: float = PRIMARY_TH) -> Optional[dict]:
    for e in edges:
        if e["condition_key"] == condition_key and e["horizon"] == h and abs(e["threshold"] - th) < 1e-6:
            return e
    return None


def persist_edges(conn, edges: List[dict]) -> int:
    ensure_med_tables(conn)
    n = 0
    for e in edges:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_conditional_edge_tables
            (asof_date, condition_key, horizon, threshold, n, hit_rate, avg_return,
             median_return, avg_win, avg_loss, expectancy, pf_100, pf_150, pf_200,
             stop8, top10_dominance, sector_concentration, sample_quality, window_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                e["asof_date"], e["condition_key"], e["horizon"], e["threshold"],
                e["n"], e["hit_rate"], e["avg_return"], e["median_return"],
                e["avg_win"], e["avg_loss"], e["expectancy"],
                e["pf_100"], e["pf_150"], e["pf_200"], e["stop8"],
                e["top10_dominance"], e["sector_concentration"], e["sample_quality"],
                e["window_mode"],
            ),
        )
        n += 1
    conn.commit()
    return n


def write_json(edges: List[dict], path=None) -> None:
    path = path or DATA / "med_conditional_edge_tables_last.json"
    path.write_text(json.dumps({"edges": edges, "count": len(edges)}, indent=2, default=str), encoding="utf-8")
