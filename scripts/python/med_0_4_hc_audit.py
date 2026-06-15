#!/usr/bin/env python3
"""MED-0.4 — HIGH_CONVICTION blocker waterfall audit."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_4_hc_audit_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))
from med_common import latest_med_trade_date
from med_0_4_scoring import is_index_symbol, failure_risk_score


def run(params: dict | None = None) -> dict:
    params = params or {}
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date") or latest_med_trade_date(db)
    if not trade_date:
        db.close()
        return {"success": False, "error": "no_trade_date"}

    rows = [
        dict(r) for r in db.execute(
            """
            SELECT d.symbol, d.med_score, d.med_bucket, d.p_cond_20d_10,
                   d.failure_similarity, d.crowding_score, d.sample_quality,
                   d.liquidity_fitness, d.condition_key, d.reason_codes,
                   d.p_tail, d.med_score_rank, a.analogue_p_tail_20_10
            FROM med_daily_scores d
            LEFT JOIN med_analogue_scores_daily a
              ON d.trade_date = a.trade_date AND d.symbol = a.symbol
            WHERE d.trade_date=?
            """,
            (trade_date,),
        ).fetchall()
    ]

    for r in rows:
        if r.get("p_tail") is None:
            p_cond = r.get("p_cond_20d_10") or 0
            p_ana = r.get("analogue_p_tail_20_10") or 0
            r["p_tail"] = 0.6 * p_cond + 0.4 * p_ana
        if r.get("med_score_rank") is None:
            try:
                rc = json.loads(r.get("reason_codes") or "[]")
                r["med_score_rank"] = float(rc[3]) if len(rc) > 3 else 0.0
            except (json.JSONDecodeError, TypeError, ValueError):
                r["med_score_rank"] = 0.0

    p_tail_p70 = sorted(r.get("p_tail") or 0 for r in rows)
    pt70 = p_tail_p70[int(0.7 * max(len(p_tail_p70) - 1, 0))] if p_tail_p70 else 0.15
    sq_vals = sorted(r.get("sample_quality") or 0 for r in rows)
    sq60 = sq_vals[int(0.6 * max(len(sq_vals) - 1, 0))] if sq_vals else 0.3
    risks = sorted(max(r.get("failure_similarity") or 0, r.get("crowding_score") or 0) for r in rows)
    risk40 = risks[int(0.4 * max(len(risks) - 1, 0))] if risks else 0.35

    blockers = {
        "index_symbol": 0,
        "chase_or_failure_bucket": 0,
        "ms_rank_lt_p85": 0,
        "p_tail_lt_p70": 0,
        "risk_gt_p40": 0,
        "sq_lt_p60": 0,
        "lf_lt_0_25": 0,
        "would_pass_hc_gate": 0,
    }

    for r in rows:
        sym = r["symbol"]
        if is_index_symbol(sym):
            blockers["index_symbol"] += 1
            continue
        if r.get("med_bucket") in ("MED_DO_NOT_CHASE", "MED_FAILURE_WARNING"):
            blockers["chase_or_failure_bucket"] += 1
            continue
        rk = r.get("med_score_rank", 0)
        pt = r.get("p_tail") or 0
        risk = failure_risk_score(r.get("failure_similarity") or 0)
        sq = r.get("sample_quality") or 0
        lf = r.get("liquidity_fitness") or 0
        failed = False
        if rk < 0.85:
            blockers["ms_rank_lt_p85"] += 1
            failed = True
        if pt < pt70:
            blockers["p_tail_lt_p70"] += 1
            failed = True
        if risk > risk40:
            blockers["risk_gt_p40"] += 1
            failed = True
        if sq < sq60:
            blockers["sq_lt_p60"] += 1
            failed = True
        if lf < 0.20:
            blockers["lf_lt_0_25"] += 1
            failed = True
        if not failed:
            blockers["would_pass_hc_gate"] += 1

    hc = [r for r in rows if r.get("med_bucket") == "MED_HIGH_CONVICTION_RESEARCH"]
    buckets = {}
    for r in rows:
        buckets[r["med_bucket"]] = buckets.get(r["med_bucket"], 0) + 1

    top_pcond = sorted(
        [r for r in rows if (r.get("p_cond_20d_10") or 0) >= 0.22],
        key=lambda x: x.get("p_cond_20d_10", 0),
        reverse=True,
    )[:10]
    pcond_in_top40 = sum(1 for r in top_pcond if (r.get("med_score_rank") or 0) >= 0.60)

    hc_syms = {r["symbol"] for r in hc}
    med_top20 = sorted(rows, key=lambda x: x.get("med_score", 0), reverse=True)[:20]
    med_top20_syms = [r["symbol"] for r in med_top20 if not is_index_symbol(r["symbol"])]
    ana_top = sorted(
        rows,
        key=lambda x: x.get("analogue_p_tail_20_10") or 0,
        reverse=True,
    )[:20]
    ana_top_syms = [r["symbol"] for r in ana_top]
    overlap = sorted(set(med_top20_syms) & set(ana_top_syms))
    hc_ana_overlap = sorted(hc_syms & set(ana_top_syms))

    out = {
        "success": True,
        "phase": "MED-0.4",
        "trade_date": trade_date,
        "buckets": buckets,
        "high_conviction_count": len(hc),
        "high_conviction_symbols": [r["symbol"] for r in hc],
        "thresholds_used": {"p_tail_p70": pt70, "sq_p60": sq60, "risk_p40": risk40},
        "blockers": blockers,
        "sq_stats": {
            "min": min((r.get("sample_quality") or 0) for r in rows) if rows else 0,
            "max": max((r.get("sample_quality") or 0) for r in rows) if rows else 0,
            "avg": sum((r.get("sample_quality") or 0) for r in rows) / len(rows) if rows else 0,
            "ge_p60": sum(1 for r in rows if (r.get("sample_quality") or 0) >= sq60),
        },
        "pcond_top_rank_check": {
            "n_pcond_ge_0_22": len(top_pcond),
            "in_top40pct_rank": pcond_in_top40,
        },
        "analogue_overlap": {
            "med_top20": med_top20_syms[:10],
            "analogue_top20": ana_top_syms[:10],
            "overlap_count": len(overlap),
            "overlap_pct": round(100 * len(overlap) / 20, 1),
            "overlap_symbols": overlap,
            "hc_in_analogue_top20": hc_ana_overlap,
            "target_pct_ge_30": len(overlap) >= 6,
        },
        "hc_top": [
            {
                "symbol": r["symbol"],
                "med_score": r.get("med_score"),
                "med_score_rank": r.get("med_score_rank"),
                "p_cond": r.get("p_cond_20d_10"),
                "p_tail": r.get("p_tail"),
                "sq": r.get("sample_quality"),
            }
            for r in sorted(hc, key=lambda x: x.get("med_score", 0), reverse=True)
        ],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    db.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
