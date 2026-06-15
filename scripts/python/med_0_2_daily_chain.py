#!/usr/bin/env python3
"""MED daily chain — MED-0/1 daily + MED-2 analogue/forward (shadow only)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import MED_INVARIANTS, DATA as MED_DATA
from med_0_1_daily_engine import run as run_daily
from med_0_2_analogue_kernel import run as run_analogue
from med_0_2_threshold_recalibration import run as run_thresholds
from med_0_2_forward_shadow import run as run_forward
from med_0_2_manifest import write_manifest


def run(params: dict | None = None) -> dict:
    params = params or {}
    trade_date = params.get("trade_date")

    daily = run_daily({"trade_date": trade_date} if trade_date else {})
    trade_date = daily.get("trade_date") or trade_date
    p = {"trade_date": trade_date} if trade_date else {}

    thresholds = run_thresholds(p)
    analogue = run_analogue(p)
    forward = run_forward({**p, "backfill_oos": params.get("backfill_oos", False)})

    manifest = write_manifest(trade_date)

    out = {
        "success": True,
        "phase": "MED-daily-chain",
        "trade_date": trade_date,
        "invariants": MED_INVARIANTS,
        "daily": {
            "scored_rows": daily.get("scored_rows"),
            "edge_rows": daily.get("edge_rows"),
            "buckets": daily.get("buckets"),
            "top10": daily.get("top10"),
        },
        "analogue_scored": analogue.get("scored"),
        "forward": {
            "live_skipped": forward.get("live_skipped"),
            "new_live_entries": forward.get("new_live_entries"),
            "open_positions": forward.get("live_closed_trades", 0),
        },
        "manifest": manifest.get("written"),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    (MED_DATA / "med_daily_chain_last.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8",
    )
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
