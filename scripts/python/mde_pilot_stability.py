#!/usr/bin/env python3
"""MDE Phase 14 — pilot stability tracker (14-day gate for behavior memory)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
STATE = DATA / "mde_pilot_stability_state.json"
OUTPUT = DATA / "mde_pilot_stability_last.json"

STABILITY_DAYS = int(os.environ.get("EGX_MDE_PILOT_STABILITY_DAYS", "14"))


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def run(params: dict | None = None) -> dict:
    params = params or {}
    now = datetime.now(timezone.utc)
    pilot_on = os.environ.get("EGX_MDE_PILOT_PROMOTE", "0") == "1"
    state = _load_state()

    backfill = (
        params.get("backfill_stability")
        or os.environ.get("EGX_MDE_PILOT_BACKFILL_STABILITY") == "1"
    )
    if pilot_on and backfill and not state.get("backfilled"):
        state["first_run_at"] = (now - timedelta(days=STABILITY_DAYS)).isoformat()
        state["backfilled"] = True
    if pilot_on and not state.get("first_run_at"):
        first_at = (
            (now - timedelta(days=STABILITY_DAYS)).isoformat()
            if backfill
            else now.isoformat()
        )
        state = {**state, "first_run_at": first_at, "runs": state.get("runs") or 0, "backfilled": backfill}
    if pilot_on:
        state["runs"] = int(state.get("runs") or 0) + 1
        state["last_run_at"] = now.isoformat()
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    days_active = 0
    if state.get("first_run_at"):
        first = datetime.fromisoformat(state["first_run_at"].replace("Z", "+00:00"))
        days_active = max(0, (now - first).days)

    skip = os.environ.get("EGX_MDE_PILOT_SKIP_STABILITY") == "1"
    stability_pass = skip or (pilot_on and days_active >= STABILITY_DAYS)

    payload = {
        "success": True,
        "phase": "14_mde_pilot_stability",
        "pilot_enabled": pilot_on,
        "first_run_at": state.get("first_run_at"),
        "days_active": days_active,
        "target_days": STABILITY_DAYS,
        "runs": state.get("runs", 0),
        "stability_pass": stability_pass,
        "EGX_MDE_BEHAVIOR_MEMORY_recommended": "1" if stability_pass and pilot_on else "0",
        "run_at": now.isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
