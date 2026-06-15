#!/usr/bin/env python3
"""MED-0.3 — cognition feed for market_os / agents (JSON artifact)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
ARCH_DIR = DATA / "cognition_archive"
OUTPUT = DATA / "med_cognition_feed_last.json"


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def build_feed(sources: Dict[str, Any]) -> dict:
    report = sources.get("report") or {}
    wire = sources.get("wire") or {}
    fwd = sources.get("forward") or {}
    replay = sources.get("replay") or {}
    hc_audit = sources.get("hc_audit") or {}

    td = report.get("trade_date") or hc_audit.get("trade_date")
    buckets = report.get("buckets") or hc_audit.get("buckets") or {}
    lift = (replay.get("incremental_lift") or {}).get("MED_LRE_vs_LRE") or {}

    penalize_ok = wire.get("verdict") == "PASS_MED_0_3_WIRE"
    false_edge = (report.get("false_edge_feed") or {}).get("false_edge_count", 0)
    hc_symbols = hc_audit.get("high_conviction_symbols") or []
    hc_top = hc_audit.get("hc_top") or []

    top_disc = report.get("discoveries_top") or []
    analogue_top = report.get("analogue_top") or []
    ana_overlap = hc_audit.get("analogue_overlap") or {}

    return {
        "layer": "MED-0.4",
        "trade_date": td,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_only": True,
        "posture": (
            "HC_RESEARCH_ACTIVE" if hc_symbols
            else "FILTER_DEFENSIVE" if buckets.get("MED_FAILURE_WARNING", 0) > 40
            else "NEUTRAL"
        ),
        "buckets": buckets,
        "high_conviction_count": len(hc_symbols),
        "high_conviction_symbols": hc_symbols,
        "false_edge_count": false_edge,
        "penalize_wired": penalize_ok,
        "oos_median_lift_pp": round(float(lift.get("median_delta") or 0) * 100, 2) if lift else None,
        "analogue_overlap_pct": ana_overlap.get("overlap_pct"),
        "graduation": {
            "live_closed": fwd.get("live_closed_trades", 0),
            "required": 40,
            "graduation_met": fwd.get("graduation_met", False),
            "feed_boost_allowed": False,
        },
        "actionable_signals": {
            "high_conviction": hc_top[:8] or [
                d for d in top_disc if d.get("med_bucket") == "MED_HIGH_CONVICTION_RESEARCH"
            ][:8],
            "analogue_confluence": analogue_top[:5],
        },
        "directives": [
            "MED-0.4 HC: dual-score rank + edge.n>=30 + p_tail gate",
            "apply MED_FAILURE_WARNING penalize when failure_similarity >= 0.35",
            "apply MED_DO_NOT_CHASE penalize when crowding_score >= 0.70",
            "do not enable MED_FEED_BOOST until live_closed >= 40",
        ],
    }


def _patch_cognition_archive(trade_date: str, feed: dict) -> bool:
    if not trade_date:
        return False
    path = ARCH_DIR / f"{trade_date}.json"
    if not path.exists():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    doc["med_layer"] = {
        "phase": "MED-0.4",
        "high_conviction_count": feed.get("high_conviction_count"),
        "high_conviction_symbols": feed.get("high_conviction_symbols"),
        "buckets": feed.get("buckets"),
        "false_edge_count": feed.get("false_edge_count"),
        "penalize_wired": feed.get("penalize_wired"),
        "oos_median_lift_pp": feed.get("oos_median_lift_pp"),
        "analogue_overlap_pct": feed.get("analogue_overlap_pct"),
        "graduation": feed.get("graduation"),
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def run(params: dict | None = None) -> dict:
    params = params or {}
    sources = {
        "report": _read_json(DATA / "med_0_3_discovery_report.json"),
        "wire": _read_json(DATA / "med_0_3_wire_acceptance_last.json"),
        "forward": _read_json(DATA / "med_forward_shadow_last.json"),
        "replay": _read_json(DATA / "med_replay_audit_last.json"),
        "hc_audit": _read_json(DATA / "med_0_4_hc_audit_last.json"),
    }
    feed = build_feed(sources)
    OUTPUT.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")

    patched = False
    if params.get("patch_archive", True):
        patched = _patch_cognition_archive(str(feed.get("trade_date") or ""), feed)

    return {
        "success": True,
        "written": str(OUTPUT.relative_to(ROOT)),
        "trade_date": feed.get("trade_date"),
        "patched_cognition_archive": patched,
        "feed": feed,
    }


if __name__ == "__main__":
    import sys

    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), ensure_ascii=False, indent=2))
