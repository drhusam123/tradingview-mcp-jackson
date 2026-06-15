#!/usr/bin/env python3
"""LRE-4.0 → opportunity_score_v2 research feed bridge (additive shadow only)."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Tuple

MAX_BOOST = 3.0
ENABLED_DEFAULT = True

TIER_FLAGS = {
    "LRE_CLEAN_CORE": "LRE_CLEAN_CORE",
    "LRE_CONFLUENCE_CAPPED": "LRE_MDE_CONFLUENCE",
    "LRE_CONFLUENCE": "LRE_MDE_CONFLUENCE",
    "LRE_4B_MONITOR": "LRE_4B_MONITOR",
    "LRE_GATE": "LRE_GATE",
    "LRE_MONITOR": "LRE_MONITOR",
}


def feed_boost_enabled() -> bool:
    return os.environ.get("EGX_LRE_FEED_BOOST", "1" if ENABLED_DEFAULT else "0") != "0"


def load_lre_feed_map(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lre_research_feed_daily'"
        ).fetchone()
        if not row:
            return {}
        rows = conn.execute(
            """
            SELECT symbol, feed_tier, opp_boost_points, pilot_eligible, pilot_bucket,
                   dual_gate_type, lre_sub_stage, lre_eps, mde_score, dual_gate_score,
                   primary_list, fabric_atoms_json
            FROM lre_research_feed_daily
            WHERE signal_date=?
            """,
            (trade_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["symbol"]: dict(r) for r in rows}


def apply_lre_research_boost(
    symbol: str,
    feed_row: dict | None,
) -> Tuple[float, List[str], dict]:
    """Return (boost_points, flags, evidence_fragment)."""
    if not feed_boost_enabled() or not feed_row:
        return 0.0, [], {}
    pts = min(float(feed_row.get("opp_boost_points") or 0), MAX_BOOST)
    if pts <= 0:
        return 0.0, [], {}

    flags: List[str] = []
    tier = feed_row.get("feed_tier") or ""
    flag = TIER_FLAGS.get(tier)
    if flag:
        flags.append(flag)
    if int(feed_row.get("pilot_eligible") or 0):
        flags.append("LRE_PILOT_ELIGIBLE")
    bucket = feed_row.get("pilot_bucket")
    if bucket == "Clean_Confluence_Core":
        flags.append("LRE_CLEAN_CORE")
    elif bucket == "Controlled_4B_Monitor":
        flags.append("LRE_4B_MONITOR")

    atoms = []
    try:
        atoms = json.loads(feed_row.get("fabric_atoms_json") or "[]")
    except json.JSONDecodeError:
        atoms = []
    for a in atoms:
        flags.append(f"LRE_ATOM_{a.upper()}")

    evidence = {
        "lre_feed_tier": tier,
        "lre_boost_points": pts,
        "lre_eps": feed_row.get("lre_eps"),
        "lre_sub_stage": feed_row.get("lre_sub_stage"),
        "mde_score": feed_row.get("mde_score"),
        "dual_gate_score": feed_row.get("dual_gate_score"),
        "pilot_bucket": bucket,
    }
    return pts, flags, evidence
