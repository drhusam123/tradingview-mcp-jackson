#!/usr/bin/env python3
"""MED-2 — Orchestrator: analogue + thresholds + forward shadow."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_0_2_analogue_kernel import run as run_analogue
from med_0_2_threshold_recalibration import run as run_thresholds
from med_0_2_forward_shadow import run as run_forward


def run(params: dict | None = None) -> dict:
    params = params or {}
    if params.get("run_thresholds", True):
        try:
            run_thresholds(params)
        except Exception as e:
            print(f"thresholds skipped: {e}", flush=True)
    ana = run_analogue(params)
    fwd = run_forward(params)
    out = {
        "success": True,
        "phase": "MED-2",
        "analogue": ana,
        "forward_shadow": fwd,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "med_2_run_summary.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
