#!/usr/bin/env python3
"""MED-0.4 daily chain — dual-score engine + HC audit + forward shadow."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import MED_INVARIANTS, DATA as MED_DATA
from med_0_3_calibrate_weekly import run as run_calibrate_weekly
from med_0_3_daily_engine import run as run_daily
from med_0_2_forward_shadow import run as run_forward
from med_0_2_manifest import write_manifest
from med_0_3_research_report import run as run_report
from med_0_3_lessons_bridge import run as run_lessons
from med_0_3_cognition_feed import run as run_cognition
from med_0_3_status import run as run_status
from med_0_4_hc_audit import run as run_hc_audit
from med_0_4_acceptance import run as run_acceptance


def run(params: dict | None = None) -> dict:
    params = params or {}
    trade_date = params.get("trade_date")

    calibrate = run_calibrate_weekly({
        **({"trade_date": trade_date} if trade_date else {}),
        "if_due": params.get("calibrate_if_due", True),
        "force": params.get("force_calibrate", False),
    })

    if calibrate.get("ran_full"):
        daily = {
            "success": calibrate.get("success", True),
            "trade_date": calibrate.get("trade_date"),
            "scored_rows": calibrate.get("scored_rows"),
            "edge_rows": calibrate.get("edge_rows"),
            "buckets": calibrate.get("buckets"),
            "thresholds": calibrate.get("thresholds"),
            "top10": calibrate.get("top10"),
            "from_weekly_calibrate": True,
        }
        trade_date = daily.get("trade_date") or trade_date
    else:
        daily = run_daily({
            **({"trade_date": trade_date} if trade_date else {}),
            "rebuild_edges": params.get("rebuild_edges", False),
        })
        trade_date = daily.get("trade_date") or trade_date
    p = {"trade_date": trade_date} if trade_date else {}

    forward = run_forward({**p, "backfill_oos": params.get("backfill_oos", False)})
    manifest = write_manifest(trade_date)
    report = run_report(p)
    hc_audit = run_hc_audit(p)
    acceptance = run_acceptance(p)
    lessons = run_lessons({})
    cognition = run_cognition({})
    status = run_status(p)

    out = {
        "success": True,
        "phase": "MED-0.4",
        "trade_date": trade_date,
        "invariants": MED_INVARIANTS,
        "calibrate": {
            "skipped": calibrate.get("skipped"),
            "ran_full": calibrate.get("ran_full"),
            "last_full_calibrate_at": calibrate.get("last_full_calibrate_at"),
        },
        "daily": {
            "scored_rows": daily.get("scored_rows"),
            "edge_rows": daily.get("edge_rows"),
            "buckets": daily.get("buckets"),
            "thresholds": daily.get("thresholds"),
            "top10": daily.get("top10"),
        },
        "forward": {
            "live_skipped": forward.get("live_skipped"),
            "new_live_entries": forward.get("new_live_entries"),
            "oos_closed": forward.get("oos_closed"),
        },
        "manifest": manifest.get("written"),
        "discovery_report": report.get("trade_date"),
        "false_edge_count": (report.get("false_edge_feed") or {}).get("false_edge_count", 0),
        "discovery_atoms": report.get("discovery_atoms", 0),
        "lessons": lessons.get("action"),
        "cognition_feed": cognition.get("written"),
        "graduation_verdict": (status.get("graduation") or {}).get("verdict"),
        "high_conviction_count": hc_audit.get("high_conviction_count"),
        "hc_audit": hc_audit.get("high_conviction_symbols"),
        "analogue_overlap_pct": (hc_audit.get("analogue_overlap") or {}).get("overlap_pct"),
        "acceptance_verdict": acceptance.get("verdict"),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    (MED_DATA / "med_0_3_daily_chain_last.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8",
    )
    (MED_DATA / "med_daily_chain_last.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8",
    )
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
