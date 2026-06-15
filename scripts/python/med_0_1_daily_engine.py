#!/usr/bin/env python3
"""
MED-0/1 daily engine — state field + conditional edges + MED score v1.
Shadow only. No client path.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import (
    DATA, HYPOTHETICAL_BOOST, MED_INVARIANTS, MIN_BARS, OOS_END, OOS_START,
    PRIMARY_H, PRIMARY_TH, connect, ensure_med_tables, forward_return,
    load_bars, load_lre_context, load_mde_context, load_sectors, rank_normalize,
)
from med_0_1_math_features import compute_math_fields
from med_0_1_distribution_shift import distribution_shift
from med_0_1_failure_patterns import (
    failure_similarity, failure_vector, is_failure_event, scaled_vec,
)
from med_0_1_path_profiles import compute_path_profiles
from med_0_1_conditional_edges import (
    aggregate_edges, build_condition_key, lookup_edge, persist_edges,
)
from med_0_1_sample_quality import compute_sample_quality


def _idx_for_date(bars: List[dict], d: str) -> Optional[int]:
    for i, b in enumerate(bars):
        if b["date"] == d:
            return i
    return None


def assign_bucket(row: dict, edge: Optional[dict]) -> str:
    ms = row.get("med_score", 0)
    fs = row.get("failure_similarity", 0)
    cp = row.get("crowding_score", 0)
    sq = row.get("sample_quality", 0)
    lf = row.get("liquidity_fitness", 0)
    p_cond = row.get("p_cond_20d_10", 0)
    exp_ret = row.get("expected_return_20d", 0)
    exp_edge = (edge or {}).get("expectancy", 0)
    dnc = row.get("do_not_chase", 0)
    n = (edge or {}).get("n", 0)

    if fs >= 0.60 or cp >= 0.75 or dnc:
        return "MED_FAILURE_WARNING"
    if n < 30 or sq < 0.25:
        return "MED_INSUFFICIENT_SAMPLE"
    if ms >= 80 and p_cond >= 0.15 and exp_ret > 0 and fs < 0.35 and cp < 0.50 and sq >= 0.50 and lf >= 0.50:
        return "MED_HIGH_CONVICTION_RESEARCH"
    if ms >= 65 and exp_edge > 0 and sq >= 0.35:
        return "MED_POSITIVE_EXPECTANCY"
    if ms >= 50:
        return "MED_MONITOR"
    return "MED_INSUFFICIENT_SAMPLE"


def compute_med_score(row: dict, edge: Optional[dict], pop: dict) -> float:
    p_cond = (edge or {}).get("hit_rate", 0) if edge else 0
    exp_ret = max((edge or {}).get("avg_return", 0) or 0, 0) if edge else 0
    energy = rank_normalize(row.get("stored_energy", 0), pop.get("energy", [0]))
    absorp = rank_normalize(row.get("absorption_score", 0), pop.get("absorp", [0]))
    dist = rank_normalize(row.get("distribution_shift_score", 0), pop.get("dist", [0]))
    pq = rank_normalize((edge or {}).get("path_quality", 0) or row.get("path_quality_20", 0), pop.get("pq", [0]))
    liq = rank_normalize(row.get("liquidity_fitness", 0), pop.get("liq", [0]))

    raw = (
        0.25 * p_cond
        + 0.20 * exp_ret
        + 0.15 * energy
        + 0.10 * absorp
        + 0.10 * dist
        + 0.10 * pq
        + 0.10 * liq
    )
    adj = raw * row.get("sample_quality", 0.5) * row.get("regime_fit", 0.75)
    adj *= (1 - row.get("crowding_score", 0))
    adj *= (1 - row.get("failure_similarity", 0))
    return 100 * rank_normalize(adj, pop.get("adj", [adj]))


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_med_tables(conn)
    by_sym, meta = load_bars(conn)
    sectors = load_sectors(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else meta.get("max_date")

    lre_all: Dict[str, Dict[str, dict]] = {}
    mde_all: Dict[str, Dict[str, dict]] = {}
    dates_set = set()
    for sym, bars in by_sym.items():
        for b in bars:
            if OOS_START <= b["date"] <= OOS_END:
                dates_set.add(b["date"])

    for d in sorted(dates_set):
        lre_all[d] = load_lre_context(conn, d)
        mde_all[d] = load_mde_context(conn, d)

    # Build historical rows for edges (causal)
    hist_rows: List[dict] = []
    fail_hist: Dict[str, List] = defaultdict(list)

    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS:
            continue
        sector = sectors.get(sym, "Unknown")
        for idx in range(MIN_BARS, len(bars) - 45):
            d = bars[idx]["date"]
            if d < OOS_START or d > OOS_END:
                continue
            lre = lre_all.get(d, {}).get(sym, {})
            mde = mde_all.get(d, {}).get(sym, {})
            mf = compute_math_fields(bars, idx, lre, mde)
            if not mf:
                continue
            dist = distribution_shift(bars, idx, mf)
            paths = compute_path_profiles(bars, idx)
            path20 = paths.get(20, {})
            fv = failure_vector(mf, dist)
            fs = failure_similarity(fv, fail_hist[sym], k=50)
            mf["failure_similarity"] = fs
            mf["distribution_shift_score"] = dist["shift_score"]
            mf["behavior_changed_before_price"] = dist["behavior_changed"]
            mf["liquidity_fitness"] = min(1.0, (mf.get("liquidity_fitness_raw", 0) / 5e7))

            fail_evt = is_failure_event(bars, idx, mf, path20)
            fail_hist[sym].append((sym, idx, fv, fail_evt))
            if len(fail_hist[sym]) > 500:
                fail_hist[sym] = fail_hist[sym][-500:]

            ck = build_condition_key(mf, dist, lre, mde)
            row = {
                "trade_date": d, "symbol": sym, "sector": sector,
                "condition_key": ck, **mf,
            }
            for h in (5, 10, 20, 30, 45):
                fr = forward_return(bars, idx, h)
                row[f"r_{h}"] = fr
                row[f"stop8_{h}"] = paths.get(h, {}).get("stop8", 0)
            row["path_quality_20"] = path20.get("path_quality") or 0
            hist_rows.append(row)

    edges = aggregate_edges(
        [{"condition_key": r["condition_key"], "symbol": r["symbol"], "sector": r["sector"],
          **{f"r_{h}": r.get(f"r_{h}") for h in (5, 10, 20, 30, 45)},
          **{f"stop8_{h}": r.get(f"stop8_{h}", 0) for h in (5, 10, 20, 30, 45)}}
         for r in hist_rows if r.get(f"r_{PRIMARY_H}") is not None],
        asof_date=trade_date,
        window_mode="expanding",
    )
    persist_edges(conn, edges)

    # Latest day scoring — includes tail dates without forward returns
    day_rows: List[dict] = []
    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS:
            continue
        idx = _idx_for_date(bars, trade_date)
        if idx is None or idx < MIN_BARS:
            continue
        sector = sectors.get(sym, "Unknown")
        lre = lre_all.get(trade_date, {}).get(sym, {})
        mde = mde_all.get(trade_date, {}).get(sym, {})
        mf = compute_math_fields(bars, idx, lre, mde)
        if not mf:
            continue
        dist = distribution_shift(bars, idx, mf)
        paths = compute_path_profiles(bars, idx)
        path20 = paths.get(20, {})
        fv = failure_vector(mf, dist)
        fs = failure_similarity(fv, fail_hist.get(sym, []), k=50)
        mf["failure_similarity"] = fs
        mf["distribution_shift_score"] = dist["shift_score"]
        mf["behavior_changed_before_price"] = dist["behavior_changed"]
        mf["liquidity_fitness"] = min(1.0, (mf.get("liquidity_fitness_raw", 0) / 5e7))
        ck = build_condition_key(mf, dist, lre, mde)
        row = {
            "trade_date": trade_date, "symbol": sym, "sector": sector,
            "condition_key": ck, **mf,
            "path_quality_20": path20.get("path_quality") or 0,
        }
        day_rows.append(row)

    if not day_rows and hist_rows:
        latest = max(r["trade_date"] for r in hist_rows)
        trade_date = latest
        day_rows = [r for r in hist_rows if r["trade_date"] == trade_date]

    pop = {
        "energy": [r.get("stored_energy", 0) for r in day_rows],
        "absorp": [r.get("absorption_score", 0) for r in day_rows],
        "dist": [r.get("distribution_shift_score", 0) for r in day_rows],
        "pq": [r.get("path_quality_20", 0) for r in day_rows],
        "liq": [r.get("liquidity_fitness", 0) for r in day_rows],
        "adj": [],
    }

    scored = []
    for r in day_rows:
        edge = lookup_edge(edges, r["condition_key"], PRIMARY_H, PRIMARY_TH)
        sq = compute_sample_quality(
            n=(edge or {}).get("n", 0),
            symbols=[x["symbol"] for x in hist_rows if x["condition_key"] == r["condition_key"]],
            sectors=[x["sector"] for x in hist_rows if x["condition_key"] == r["condition_key"]],
            dates=[x["trade_date"] for x in hist_rows if x["condition_key"] == r["condition_key"]],
            rets=[x.get(f"r_{PRIMARY_H}") for x in hist_rows if x["condition_key"] == r["condition_key"] and x.get(f"r_{PRIMARY_H}") is not None],
            liquidity_flags=[x.get("liquidity_fitness", 0.5) for x in hist_rows if x["condition_key"] == r["condition_key"]],
        )
        r["sample_quality"] = sq
        r["p_cond_20d_10"] = (edge or {}).get("hit_rate", 0)
        r["expected_return_20d"] = (edge or {}).get("avg_return", 0)
        flags = []
        if r.get("do_not_chase"):
            flags.append("DO_NOT_CHASE")
        if r.get("hidden_energy_flag"):
            flags.append("HIDDEN_ENERGY")
        if r.get("behavior_changed_before_price"):
            flags.append("BEHAVIOR_SHIFT")
        r["risk_flags"] = json.dumps(flags)
        r["reason_codes"] = json.dumps([r["condition_key"]])
        scored.append(r)

    for r in scored:
        edge = lookup_edge(edges, r["condition_key"], PRIMARY_H, PRIMARY_TH)
        raw_adj = compute_med_score(r, edge, pop) / 100
        pop["adj"].append(raw_adj)

    for r in scored:
        edge = lookup_edge(edges, r["condition_key"], PRIMARY_H, PRIMARY_TH)
        r["med_score"] = compute_med_score(r, edge, pop)
        r["med_bucket"] = assign_bucket(r, edge)
        r["hypothetical_boost"] = HYPOTHETICAL_BOOST.get(r["med_bucket"], 0)
        r["reason_codes"] = json.dumps([r["condition_key"], r["med_bucket"]])

    scored.sort(key=lambda x: x.get("med_score", 0), reverse=True)

    # Persist daily
    for r in scored:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_daily_scores
            (trade_date, symbol, sector, med_score, med_bucket, p_cond_20d_10,
             expected_return_20d, stored_energy, absorption_score, distribution_shift_score,
             behavior_changed_before_price, physics_force, stored_pressure_physics,
             liquidity_fitness, crowding_score, failure_similarity, sample_quality,
             regime_fit, condition_key, reason_codes, risk_flags, hypothetical_boost,
             client_path_allowed, research_only, shadow_only)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,1)
            """,
            (
                trade_date, r["symbol"], r["sector"], r["med_score"], r["med_bucket"],
                r["p_cond_20d_10"], r["expected_return_20d"],
                r.get("stored_energy"), r.get("absorption_score"),
                r.get("distribution_shift_score"), r.get("behavior_changed_before_price", 0),
                r.get("physics_force"), r.get("stored_pressure_physics"),
                r.get("liquidity_fitness"), r.get("crowding_score"),
                r.get("failure_similarity"), r.get("sample_quality"),
                r.get("regime_fit", 0.75), r["condition_key"],
                r["reason_codes"], r["risk_flags"], r["hypothetical_boost"],
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO med_research_feed
            (trade_date, symbol, med_score, med_bucket, condition_key,
             reason_codes, risk_flags, hypothetical_boost, client_path_allowed, research_only, shadow_only)
            VALUES (?,?,?,?,?,?,?,?,0,1,1)
            """,
            (
                trade_date, r["symbol"], r["med_score"], r["med_bucket"],
                r["condition_key"], r["reason_codes"], r["risk_flags"], r["hypothetical_boost"],
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO med_distribution_shift_daily
            (trade_date, symbol, psi, w_shift, ks, shift_score, behavior_changed)
            VALUES (?,?,?,?,?,?,?)
            """,
            (trade_date, r["symbol"], 0, 0, 0,
             r.get("distribution_shift_score", 0), r.get("behavior_changed_before_price", 0)),
        )
        for h in (5, 10, 20, 30, 45):
            p = compute_path_profiles(
                by_sym[r["symbol"]],
                _idx_for_date(by_sym[r["symbol"]], trade_date) or 0,
            ).get(h, {})
            conn.execute(
                """
                INSERT OR REPLACE INTO med_path_profiles
                (trade_date, symbol, horizon, mfe, mae, path_quality, stop6, stop8, stop10, late_mover)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (trade_date, r["symbol"], h, p.get("mfe"), p.get("mae"), p.get("path_quality"),
                 p.get("stop6", 0), p.get("stop8", 0), p.get("stop10", 0), p.get("late_mover", 0)),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO med_failure_patterns
            (trade_date, symbol, failure_similarity, crowding_penalty, do_not_chase)
            VALUES (?,?,?,?,?)
            """,
            (trade_date, r["symbol"], r.get("failure_similarity"), r.get("crowding_score"),
             r.get("do_not_chase", 0)),
        )

    for e in edges:
        if e["horizon"] == PRIMARY_H and abs(e["threshold"] - PRIMARY_TH) < 1e-6:
            conn.execute(
                """
                INSERT OR REPLACE INTO med_sample_quality
                (asof_date, condition_key, n, sample_quality, top10_dominance,
                 sector_concentration, bootstrap_confidence)
                VALUES (?,?,?,?,?,?,?)
                """,
                (trade_date, e["condition_key"], e["n"], e["sample_quality"],
                 e["top10_dominance"], e["sector_concentration"], e["sample_quality"]),
            )
    conn.commit()

    # JSON artifacts
    (DATA / "med_daily_scores_last.json").write_text(
        json.dumps({"trade_date": trade_date, "rows": scored[:100], "count": len(scored)}, indent=2, default=str),
        encoding="utf-8",
    )
    (DATA / "med_research_feed_last.json").write_text(
        json.dumps({"trade_date": trade_date, "count": len(scored),
                    "top20": [{"symbol": r["symbol"], "med_score": r["med_score"], "bucket": r["med_bucket"]}
                              for r in scored[:20]]}, indent=2),
        encoding="utf-8",
    )
    (DATA / "med_conditional_edge_tables_last.json").write_text(
        json.dumps({"asof_date": trade_date, "edges": edges[:200], "count": len(edges)}, indent=2, default=str),
        encoding="utf-8",
    )
    dist_rows = [{"symbol": r["symbol"], "shift_score": r.get("distribution_shift_score"),
                  "behavior_changed": r.get("behavior_changed_before_price")} for r in scored]
    (DATA / "med_distribution_shift_last.json").write_text(
        json.dumps({"trade_date": trade_date, "rows": dist_rows}, indent=2), encoding="utf-8",
    )
    (DATA / "med_path_profiles_last.json").write_text(
        json.dumps({"trade_date": trade_date, "horizons": [5, 10, 20, 30, 45], "symbols": len(scored)}, indent=2),
        encoding="utf-8",
    )
    fail_rows = [{"symbol": r["symbol"], "failure_similarity": r.get("failure_similarity"),
                  "crowding": r.get("crowding_score")} for r in scored]
    (DATA / "med_failure_patterns_last.json").write_text(
        json.dumps({"trade_date": trade_date, "rows": fail_rows}, indent=2), encoding="utf-8",
    )
    sq_rows = [e for e in edges if e["horizon"] == PRIMARY_H and abs(e["threshold"] - PRIMARY_TH) < 1e-6][:50]
    (DATA / "med_sample_quality_last.json").write_text(
        json.dumps({"asof_date": trade_date, "rows": sq_rows}, indent=2, default=str), encoding="utf-8",
    )

    buckets = defaultdict(int)
    for r in scored:
        buckets[r["med_bucket"]] += 1

    summary = {
        "success": True,
        "trade_date": trade_date,
        "invariants": MED_INVARIANTS,
        "symbols": meta["symbols"],
        "bars": meta["bars"],
        "scored_rows": len(scored),
        "edge_rows": len(edges),
        "hist_rows": len(hist_rows),
        "buckets": dict(buckets),
        "top10": [{"symbol": r["symbol"], "med_score": round(r["med_score"], 2),
                   "bucket": r["med_bucket"]} for r in scored[:10]],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "med_0_1_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
