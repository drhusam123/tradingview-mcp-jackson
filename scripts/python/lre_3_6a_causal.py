#!/usr/bin/env python3
"""LRE-3.6A causal helpers — walk-forward thresholds, daily signals, no future leakage."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from lre_3_1_filters import enrich_signal, family_similarity, load_fingerprints
from lre_3_2_stages import classify_substage
from lre_mde_dual_gate import assess_mde_gate, classify_dual_gate_type, dual_gate_score, lre_row_summary
from lre_mde_dual_gate import lre_rejected  # noqa: F401

STATIC_THRESHOLD_DEFAULTS = {
    "balanced": 52.0,
    "conservative": 58.0,
    "ultra": 65.0,
    "calibrated_n": 0,
    "source": "STATIC_RESEARCH_THRESHOLD",
}


def load_explosion_events(conn) -> List[dict]:
    try:
        rows = conn.execute(
            """SELECT symbol, signal_date FROM lre_explosion_events
               WHERE family='A_long_accumulation' AND include_research=1
               ORDER BY signal_date"""
        ).fetchall()
    except Exception:
        return []
    return [{"symbol": r["symbol"], "signal_date": r["signal_date"]} for r in rows]


def calibrate_thresholds_causal(
    events: List[dict],
    by_sym: dict,
    fingerprints: dict,
    before_date: str,
    rolling_dates: Optional[set] = None,
) -> dict:
    """Calibrate A-sim thresholds using only events with signal_date < before_date."""
    sims = []
    for r in events:
        if r["signal_date"] >= before_date:
            continue
        if rolling_dates is not None and r["signal_date"] not in rolling_dates:
            continue
        bars = by_sym.get(r["symbol"])
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == r["signal_date"]), None)
        if idx is None or idx < 45:
            continue
        sims.append(family_similarity(bars, idx, fingerprints, "A_long_accumulation"))
    if len(sims) < 30:
        out = dict(STATIC_THRESHOLD_DEFAULTS)
        out["calibrated_n"] = len(sims)
        out["source"] = "WALK_FORWARD_FALLBACK_STATIC"
        return out
    sims.sort()
    n = len(sims)

    def pct(p):
        i = int(n * p / 100)
        return sims[min(i, n - 1)]

    return {
        "calibrated_n": n,
        "A_p25": round(pct(25), 1),
        "A_p40": round(pct(40), 1),
        "A_p50": round(pct(50), 1),
        "balanced": round(pct(25), 1),
        "conservative": round(pct(40), 1),
        "ultra": round(pct(50), 1),
        "source": "WALK_FORWARD_RECALIBRATED",
    }


def load_mde_by_date(conn, date_from: str) -> Dict[str, List[dict]]:
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT symbol, trade_date, discovery_score, confidence_score, effective_score,
               mde_stage, hidden_repricing, setups_json, metrics_json
        FROM egx_market_discovery_daily
        WHERE trade_date >= ?
        """,
        (date_from,),
    ).fetchall():
        try:
            setups = json.loads(r["setups_json"] or "[]")
        except json.JSONDecodeError:
            setups = []
        try:
            metrics = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            metrics = {}
        by_date[r["trade_date"]].append({
            "symbol": r["symbol"],
            "trade_date": r["trade_date"],
            "discovery_score": float(r["discovery_score"] or 0),
            "confidence_score": float(r["confidence_score"] or 0),
            "effective_score": float(r["effective_score"] or 0),
            "mde_stage": r["mde_stage"],
            "hidden_repricing": bool(r["hidden_repricing"]),
            "setups": setups,
            "metrics": metrics,
            "timing_class": metrics.get("timing_class", "ON_TIME"),
            "liquidity_track": metrics.get("liquidity_track", "Mid"),
        })
    return dict(by_date)


def mde_watch_row(row: dict) -> bool:
    return float(row.get("discovery_score") or 0) >= 45 or bool(row.get("hidden_repricing"))


def build_causal_signal(
    conn,
    sym: str,
    trade_date: str,
    bars: List[dict],
    bar_idx: int,
    fingerprints: dict,
    thresholds: dict,
    mde_row: dict,
    sector: str,
) -> Optional[dict]:
    """LRE+MDE confluence snapshot using data only through trade_date bar."""
    if bar_idx < 45 or bar_idx >= len(bars) - 5:
        return None
    lre_row = enrich_signal(conn, sym, bars, bar_idx, fingerprints, thresholds)
    if not lre_row:
        return None
    sub, _ = classify_substage(bars, bar_idx, lre_row)
    lre_row["sub_stage"] = sub
    lre_row["symbol"] = sym
    lre_row["trade_date"] = trade_date

    metrics = mde_row.get("metrics") or {}
    astat = {
        "analog_hit_5d": metrics.get("analog_hit_5d", 38),
        "analog_PF": metrics.get("analog_PF", 2.1),
    }
    mde_gate = assess_mde_gate(mde_row, astat)
    dg_type, dg_reason = classify_dual_gate_type(lre_row, mde_gate)
    score = dual_gate_score(lre_row, mde_gate)
    lre_sum = lre_row_summary(lre_row)

    rejected, rej = lre_rejected(lre_row)
    return {
        "symbol": sym,
        "trade_date": trade_date,
        "signal_date": trade_date,
        "sector": sector,
        "dual_gate_type": dg_type,
        "dual_gate_reason": dg_reason,
        "dual_gate_score": score,
        "threshold_source": thresholds.get("source"),
        **lre_sum,
        **mde_gate,
        "lre_risk_flags": rej if rejected else [],
        "stop_prone_score": lre_row.get("stop_prone_score"),
        "_lre_row": lre_row,
    }


def map_bucket_36a(row: dict, pilot_eligible: bool, cap_status: str, cap_reason: str) -> str:
    if not pilot_eligible:
        if cap_status == "rejected" and cap_reason and "cap" in str(cap_reason):
            return "Rejected_By_Cap"
        return "Rejected_By_Risk"
    b = row.get("pilot_bucket") or ""
    if b == "Clean_Confluence_Core":
        return "Clean_Confluence_Core"
    if b == "Controlled_4B_Monitor":
        return "Controlled_4B_Monitor"
    if b == "New_Pattern_Monitor":
        return "New_Pattern_Monitor"
    return b or "Rejected_By_Risk"
