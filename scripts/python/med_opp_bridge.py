#!/usr/bin/env python3
"""MED-0.3 → opportunity_score_v2 research penalize bridge (shadow only, no boost)."""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Tuple

MAX_PENALTY = 14.0

BUCKET_BASE = {
    "MED_FAILURE_WARNING": 8.0,
    "MED_DO_NOT_CHASE": 10.0,
}

BUCKET_BOOST = {
    "MED_HIGH_CONVICTION_RESEARCH": 4.0,
    "MED_POSITIVE_EXPECTANCY": 2.5,
    "MED_MONITOR": 1.0,
}


def feed_boost_enabled() -> bool:
    """Positive MED boost — disabled by invariant (MED_FEED_BOOST=0)."""
    return os.environ.get("MED_FEED_BOOST", "0") != "0"


def feed_penalize_enabled() -> bool:
    """Research downrank for false-edge buckets — safe default on."""
    return os.environ.get("MED_FEED_PENALIZE", "1") != "0"


def load_med_feed_map(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='med_research_feed'"
        ).fetchone()
        if not row:
            return {}
        rows = conn.execute(
            """
            SELECT f.symbol, f.med_bucket, f.med_score, f.hypothetical_boost,
                   d.failure_similarity, d.crowding_score, d.p_cond_20d_10
            FROM med_research_feed f
            LEFT JOIN med_daily_scores d
              ON f.trade_date = d.trade_date AND f.symbol = d.symbol
            WHERE f.trade_date = ?
            """,
            (trade_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["symbol"]: dict(r) for r in rows}


def apply_med_research_penalty(
    symbol: str,
    feed_row: dict | None,
) -> Tuple[float, List[str], dict]:
    """Return (penalty_points, flags, evidence_fragment). Never boosts."""
    if feed_boost_enabled():
        return 0.0, [], {}
    if not feed_penalize_enabled() or not feed_row:
        return 0.0, [], {}

    bucket = str(feed_row.get("med_bucket") or "")
    base = BUCKET_BASE.get(bucket, 0.0)
    if base <= 0:
        return 0.0, [], {}

    fs = float(feed_row.get("failure_similarity") or 0)
    cr = float(feed_row.get("crowding_score") or 0)
    if bucket == "MED_FAILURE_WARNING":
        scale = 0.55 + 0.45 * min(fs / 0.35, 1.0) if fs > 0 else 0.55
    else:
        scale = 0.60 + 0.40 * min(cr / 0.65, 1.0) if cr > 0 else 0.60

    pts = min(base * scale, MAX_PENALTY)
    flags: List[str] = []
    if bucket == "MED_FAILURE_WARNING":
        flags.append("MED_FAILURE_WARNING")
    elif bucket == "MED_DO_NOT_CHASE":
        flags.append("MED_DO_NOT_CHASE")

    evidence = {
        "med_bucket": bucket,
        "med_penalty_pts": round(pts, 2),
        "med_score": feed_row.get("med_score"),
        "failure_similarity": fs,
        "crowding_score": cr,
        "p_cond_20d_10": feed_row.get("p_cond_20d_10"),
    }
    return pts, flags, evidence


def apply_med_research_boost(
    symbol: str,
    feed_row: dict | None,
) -> Tuple[float, List[str], dict]:
    """Positive MED boost when MED_FEED_BOOST=1."""
    if not feed_boost_enabled() or not feed_row:
        return 0.0, [], {}

    bucket = str(feed_row.get("med_bucket") or "")
    base = BUCKET_BOOST.get(bucket, 0.0)
    if base <= 0:
        return 0.0, [], {}

    p = float(feed_row.get("p_cond_20d_10") or 0)
    scale = 0.5 + min(p / 0.25, 1.0) * 0.5 if p > 0 else 0.55
    pts = min(base * scale, 8.0)
    flags = [f"MED_BOOST_{bucket.replace('MED_', '')[:12]}"]
    evidence = {
        "med_bucket": bucket,
        "med_boost_pts": round(pts, 2),
        "p_cond_20d_10": p,
        "track": "boost",
    }
    return pts, flags, evidence


def apply_med_research_effect(
    symbol: str,
    feed_row: dict | None,
) -> Tuple[float, List[str], dict, str]:
    """Return (score_delta, flags, evidence, track) — boost adds, penalize subtracts."""
    if feed_boost_enabled():
        pts, flags, ev = apply_med_research_boost(symbol, feed_row)
        return pts, flags, ev, "boost"
    pen, flags, ev = apply_med_research_penalty(symbol, feed_row)
    return -pen, flags, ev, "penalize"
