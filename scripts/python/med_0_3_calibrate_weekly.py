#!/usr/bin/env python3
"""MED-0.3 weekly full calibration — rebuild edges + expanding med_score history."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
STATE_PATH = DATA / "med_0_3_calibrate_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))

CALIBRATE_INTERVAL_DAYS = 7


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _is_due(state: dict, force: bool) -> bool:
    if force:
        return True
    last = state.get("last_full_calibrate_at")
    if not last:
        return True
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts >= timedelta(days=CALIBRATE_INTERVAL_DAYS)
    except ValueError:
        return True


def run(params: dict | None = None) -> dict:
    params = params or {}
    force = bool(params.get("force"))
    if_due = params.get("if_due", True)
    state = _load_state()
    due = _is_due(state, force)

    if if_due and not due:
        return {
            "success": True,
            "skipped": True,
            "due": False,
            "last_full_calibrate_at": state.get("last_full_calibrate_at"),
            "next_due_in_days": CALIBRATE_INTERVAL_DAYS,
        }

    from med_0_3_daily_engine import run as run_daily

    daily = run_daily({
        **({"trade_date": params["trade_date"]} if params.get("trade_date") else {}),
        "rebuild_edges": True,
    })

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "success": bool(daily.get("success", True)),
        "skipped": False,
        "due": True,
        "ran_full": True,
        "last_full_calibrate_at": now,
        "trade_date": daily.get("trade_date"),
        "edge_rows": daily.get("edge_rows"),
        "scored_rows": daily.get("scored_rows"),
        "buckets": daily.get("buckets"),
        "thresholds": daily.get("thresholds"),
        "top10": daily.get("top10"),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
