#!/usr/bin/env python3
"""
MED-0.4 daily engine — dual-track score, hierarchical SQ, HC gate + cap.
Shadow only. No client path.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import (
    ANALOGUE_K, DATA, HYPOTHETICAL_BOOST, MED_INVARIANTS, MIN_BARS,
    OOS_END, OOS_START, PRIMARY_H, PRIMARY_TH, connect, effective_oos_end, ensure_med_schema,
    forward_return, load_bars, load_lre_all, load_mde_all, load_sectors,
)
from med_0_1_math_features import compute_math_fields
from med_0_1_distribution_shift import distribution_shift
from med_0_1_failure_patterns import (
    failure_similarity, failure_vector, is_failure_event,
)
from med_0_1_path_profiles import compute_path_profiles
from med_0_4_sample_quality import hierarchical_sample_quality
from med_0_2_analogue_kernel import analogue_p_tail, build_library, state_vector
from med_0_3_calibration import (
    apply_cross_section_ranks,
    persist_threshold_snapshots,
    compute_p_tail,
)
from med_0_4_scoring import (
    apply_dual_scores,
    apply_hc_daily_cap,
    assign_bucket_v4,
    attach_bootstrap_confidence,
    build_thresholds_v4,
)
from med_0_3_edges import (
    aggregate_edges_v2, build_condition_key_v2, build_edge_lookup,
    index_hist_by_ck, load_edges_from_db, lookup_edge_fast, persist_edges,
)
from med_0_3_regime_context import load_all_regime_caches, regime_context_for


def _idx_for_date(bars: List[dict], d: str) -> Optional[int]:
    for i, b in enumerate(bars):
        if b["date"] == d:
            return i
    return None


def _build_fail_hist(
    by_sym: dict,
    sectors: Dict[str, str],
    trade_date: str,
    lre_all: dict,
    mde_all: dict,
    markov: dict,
    rotation: dict,
    breadth: dict,
) -> Dict[str, list]:
    fail_hist: Dict[str, list] = defaultdict(list)
    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS:
            continue
        sector = sectors.get(sym, "Unknown")
        end_idx = _idx_for_date(bars, trade_date)
        if end_idx is None:
            continue
        start_idx = max(MIN_BARS, end_idx - 30)
        for idx in range(start_idx, end_idx):
            d = bars[idx]["date"]
            if d < OOS_START:
                continue
            lre = lre_all.get(d, {}).get(sym, {})
            mde = mde_all.get(d, {}).get(sym, {})
            rc = regime_context_for(d, sector, markov, rotation, breadth)
            mf = compute_math_fields(bars, idx, lre, mde, rc)
            if not mf:
                continue
            dist_light = {"shift_score": 0.0, "behavior_changed": 0}
            path20 = {}
            fv = failure_vector(mf, dist_light)
            fail_evt = int(mf.get("chase_risk") or mf.get("do_not_chase")) and mf.get("r_20", 0) <= 0
            fail_hist[sym].append((sym, idx, fv, fail_evt))
            if len(fail_hist[sym]) > 500:
                fail_hist[sym] = fail_hist[sym][-500:]
    return fail_hist


def _enrich_row(
    bars: List[dict],
    idx: int,
    sym: str,
    sector: str,
    d: str,
    lre: dict,
    mde: dict,
    regime_ctx: dict,
    fail_hist: list,
    sectors: Dict[str, str],
    with_paths: bool = False,
) -> Optional[tuple]:
    mf = compute_math_fields(bars, idx, lre, mde, regime_ctx)
    if not mf:
        return None
    dist = distribution_shift(bars, idx, mf)
    paths = compute_path_profiles(bars, idx) if with_paths else {}
    path20 = paths.get(20, {})
    fv = failure_vector(mf, dist)
    fs = failure_similarity(fv, fail_hist, k=ANALOGUE_K, sector=sector, sector_map=sectors)
    mf["failure_similarity"] = fs
    mf["distribution_shift_score"] = dist["shift_score"]
    mf["behavior_changed_before_price"] = dist["behavior_changed"]
    mf["liquidity_fitness"] = min(1.0, (mf.get("liquidity_fitness_raw", 0) / 5e7))
    ck = build_condition_key_v2(mf, dist, lre, mde, regime_ctx)
    row = {
        "trade_date": d, "symbol": sym, "sector": sector,
        "condition_key": ck, **mf,
        "path_quality_20": path20.get("path_quality") or 0,
    }
    return row, dist, paths, path20


def _scan_hist_rows(
    by_sym: dict,
    sectors: Dict[str, str],
    lre_all: dict,
    mde_all: dict,
    markov: dict,
    rotation: dict,
    breadth: dict,
) -> List[dict]:
    hist_rows: List[dict] = []
    fail_hist: Dict[str, list] = defaultdict(list)
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
            rc = regime_context_for(d, sector, markov, rotation, breadth)
            out = _enrich_row(
                bars, idx, sym, sector, d, lre, mde, rc, fail_hist[sym], sectors, with_paths=True,
            )
            if not out:
                continue
            row, dist, paths, path20 = out
            fail_evt = is_failure_event(bars, idx, row, path20)
            fail_hist[sym].append((sym, idx, failure_vector(row, dist), fail_evt))
            if len(fail_hist[sym]) > 500:
                fail_hist[sym] = fail_hist[sym][-500:]
            for h in (5, 10, 20, 30, 45):
                row[f"r_{h}"] = forward_return(bars, idx, h)
                row[f"stop8_{h}"] = paths.get(h, {}).get("stop8", 0)
            hist_rows.append(row)
    return hist_rows


def _med_core_history(hist_rows: List[dict], edge_lookup: dict, stride: int = 3) -> List[float]:
    """Legacy expanding history — unused in MED-0.4 cross-section scoring."""
    return []


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_med_schema(conn)
    bars_meta = params.get("_bars_meta")
    by_sym = params.get("_bars_cache")
    if by_sym is None:
        by_sym, bars_meta = load_bars(conn)
    sectors = params.get("_sectors_cache") or load_sectors(conn)
    regime = params.get("_regime_cache")
    if regime is None:
        markov, rotation, breadth = load_all_regime_caches(conn)
    else:
        markov, rotation, breadth = regime

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else (bars_meta or {}).get("max_date")

    rebuild_edges = params.get("rebuild_edges", True)
    oos_end = params.get("_oos_end") or effective_oos_end(conn, trade_date)
    lre_all = params.get("_lre_all_cache")
    mde_all = params.get("_mde_all_cache")
    if lre_all is None:
        lre_all = load_lre_all(conn, OOS_START, oos_end)
    if mde_all is None:
        mde_all = load_mde_all(conn, OOS_START, oos_end)

    hist_rows: List[dict] = []
    if rebuild_edges:
        hist_rows = _scan_hist_rows(by_sym, sectors, lre_all, mde_all, markov, rotation, breadth)
        edges = aggregate_edges_v2(
            [{"condition_key": r["condition_key"], "symbol": r["symbol"], "sector": r["sector"],
              "trade_date": r["trade_date"],
              **{f"r_{h}": r.get(f"r_{h}") for h in (5, 10, 20, 30, 45)},
              **{f"stop8_{h}": r.get(f"stop8_{h}", 0) for h in (5, 10, 20, 30, 45)}}
             for r in hist_rows if r.get(f"r_{PRIMARY_H}") is not None],
            asof_date=trade_date,
        )
        persist_edges(conn, edges)
    else:
        edges = load_edges_from_db(conn, trade_date)
        if not edges:
            prev = conn.execute(
                "SELECT MAX(asof_date) d FROM med_conditional_edge_tables WHERE horizon=?",
                (PRIMARY_H,),
            ).fetchone()
            if prev and prev["d"]:
                edges = load_edges_from_db(conn, prev["d"])

    edge_lookup = build_edge_lookup(edges)
    hist_by_ck = index_hist_by_ck(hist_rows) if hist_rows else {}
    med_core_history = _med_core_history(hist_rows, edge_lookup) if hist_rows else []

    fail_hist = _build_fail_hist(
        by_sym, sectors, trade_date, lre_all, mde_all, markov, rotation, breadth,
    )

    analogue_cache: Dict[str, float] = {}
    backfill_fast = bool(params.get("backfill_fast"))
    if not params.get("force_analogue_rebuild") and not backfill_fast:
        for r in conn.execute(
            "SELECT symbol, analogue_p_tail_20_10 FROM med_analogue_scores_daily WHERE trade_date=?",
            (trade_date,),
        ):
            analogue_cache[r["symbol"]] = float(r["analogue_p_tail_20_10"] or 0)

    vec_lib = None
    if not backfill_fast and len(analogue_cache) < 50:
        vec_lib = build_library(by_sym, conn, trade_date, stride=3)
    lre_td = lre_all.get(trade_date, {})
    mde_td = mde_all.get(trade_date, {})
    day_rows: List[dict] = []

    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS:
            continue
        idx = _idx_for_date(bars, trade_date)
        if idx is None or idx < MIN_BARS:
            continue
        sector = sectors.get(sym, "Unknown")
        rc = regime_context_for(trade_date, sector, markov, rotation, breadth)
        out = _enrich_row(
            bars, idx, sym, sector, trade_date,
            lre_td.get(sym, {}), mde_td.get(sym, {}), rc,
            fail_hist.get(sym, []), sectors,
        )
        if not out:
            continue
        row, dist, _paths, _path20 = out
        if backfill_fast:
            p_ana = 0.15
            n_ana, conf = 0, 0.3
        elif sym in analogue_cache:
            p_ana = analogue_cache[sym]
            n_ana = ANALOGUE_K
            conf = min(1.0, p_ana)
        elif vec_lib is not None:
            p_ana, n_ana, conf = analogue_p_tail(state_vector(row, dist), vec_lib, ANALOGUE_K)
        else:
            p_ana, n_ana, conf = row.get("p_cond_20d_10", 0) or 0.15, 0, 0.3
        row["analogue_p_tail"] = p_ana
        row["analogue_neighbors"] = n_ana
        row["analogue_confidence"] = conf
        day_rows.append(row)

    apply_cross_section_ranks(day_rows)
    scored: List[dict] = []
    edges_by_sym: Dict[str, dict] = {}

    for r in day_rows:
        edge = lookup_edge_fast(edge_lookup, r["condition_key"], PRIMARY_H, PRIMARY_TH)
        edges_by_sym[r["symbol"]] = edge
        ck_rows = hist_by_ck.get(r["condition_key"], [])
        r["sample_quality"] = hierarchical_sample_quality(edge, ck_rows, PRIMARY_H)
        p_cond = (edge or {}).get("hit_rate", 0) or 0
        r["p_cond_20d_10"] = p_cond
        r["expected_return_20d"] = (edge or {}).get("avg_return", 0)
        r["p_tail"] = compute_p_tail(p_cond, r.get("analogue_p_tail", 0))
        flags = []
        if r.get("chase_risk") or r.get("do_not_chase"):
            flags.append("CHASE_RISK")
        if r.get("hidden_energy_flag"):
            flags.append("HIDDEN_ENERGY")
        if r.get("behavior_changed_before_price"):
            flags.append("BEHAVIOR_SHIFT")
        r["risk_flags"] = json.dumps(flags)
        scored.append(r)

    attach_bootstrap_confidence(scored, hist_by_ck)
    apply_dual_scores(scored, edges_by_sym)

    th = build_thresholds_v4(scored)

    for r in scored:
        edge = edges_by_sym.get(r["symbol"])
        r["med_bucket"] = assign_bucket_v4(r, edge, th)
        r["hypothetical_boost"] = HYPOTHETICAL_BOOST.get(r["med_bucket"], 0)
        r["reason_codes"] = json.dumps([
            r["condition_key"], r["med_bucket"], "MED-0.4",
            round(r.get("med_score_rank", 0), 3),
        ])

    hc_n = apply_hc_daily_cap(scored)

    scored.sort(key=lambda x: x.get("med_score", 0), reverse=True)
    persist_threshold_snapshots(conn, trade_date, th)

    for r in scored:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_daily_scores
            (trade_date, symbol, sector, med_score, med_bucket, p_cond_20d_10,
             expected_return_20d, stored_energy, absorption_score, distribution_shift_score,
             behavior_changed_before_price, physics_force, stored_pressure_physics,
             liquidity_fitness, crowding_score, failure_similarity, sample_quality,
             regime_fit, condition_key, reason_codes, risk_flags, hypothetical_boost,
             p_tail, med_score_rank, med_edge_score, med_math_score,
             client_path_allowed, research_only, shadow_only)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,1)
            """,
            (
                trade_date, r["symbol"], r["sector"], r["med_score"], r["med_bucket"],
                r["p_cond_20d_10"], r["expected_return_20d"],
                r.get("stored_energy"), r.get("absorption_score"),
                r.get("distribution_shift_score"), r.get("behavior_changed_before_price", 0),
                r.get("physics_force"), r.get("stored_pressure_physics"),
                r.get("liquidity_fitness"), r.get("crowding_score"),
                r.get("failure_similarity"), r.get("sample_quality"),
                r.get("regime_fit", 0.5), r["condition_key"],
                r["reason_codes"], r["risk_flags"], r["hypothetical_boost"],
                r.get("p_tail"), r.get("med_score_rank"),
                r.get("med_edge_score"), r.get("med_math_score"),
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
            INSERT OR REPLACE INTO med_analogue_scores_daily
            (trade_date, symbol, analogue_p_tail_20_10, analogue_neighbors,
             analogue_confidence, analogue_lift, client_path_allowed)
            VALUES (?,?,?,?,?,?,0)
            """,
            (
                trade_date, r["symbol"], r.get("analogue_p_tail", 0),
                r.get("analogue_neighbors", 0), r.get("analogue_confidence", 0),
                (r.get("analogue_p_tail", 0) - r.get("p_cond_20d_10", 0)),
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO med_failure_patterns
            (trade_date, symbol, failure_similarity, crowding_penalty, do_not_chase)
            VALUES (?,?,?,?,?)
            """,
            (
                trade_date, r["symbol"], r.get("failure_similarity"),
                r.get("crowding_score"), r.get("chase_risk", r.get("do_not_chase", 0)),
            ),
        )

    conn.commit()

    buckets = defaultdict(int)
    for r in scored:
        buckets[r["med_bucket"]] += 1

    summary = {
        "success": True,
        "phase": "MED-0.4",
        "trade_date": trade_date,
        "rebuild_edges": rebuild_edges,
        "invariants": MED_INVARIANTS,
        "symbols": (bars_meta or {}).get("symbols", 0),
        "bars": (bars_meta or {}).get("bars", 0),
        "scored_rows": len(scored),
        "edge_rows": len(edges),
        "hist_rows": len(hist_rows),
        "buckets": dict(buckets),
        "high_conviction_count": hc_n,
        "thresholds": th.to_dict(),
        "top10": [{"symbol": r["symbol"], "med_score": round(r["med_score"], 2),
                   "med_score_rank": round(r.get("med_score_rank", 0), 3),
                   "bucket": r["med_bucket"], "p_tail": round(r.get("p_tail", 0), 3),
                   "p_cond": round(r.get("p_cond_20d_10", 0), 3)}
                  for r in scored[:10]],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "med_0_3_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (DATA / "med_0_4_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (DATA / "med_0_1_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
    return summary


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
