#!/usr/bin/env python3
"""
MDE Phase 13 — behavior memory shadow pilot (hints + confidence adjustments, no opp boost).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUTPUT = DATA / "mde_pilot_shadow_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mde_behavior_memory import apply_confidence_adjustment, behavior_memory_enabled, load_profiles


def pilot_enabled() -> bool:
    return os.environ.get("EGX_MDE_PILOT_PROMOTE", "0") == "1"


def run(params: dict | None = None) -> dict:
    params = params or {}
    hints_path = DATA / "mde_shadow_promotion_hints.json"
    hints = {}
    if hints_path.exists():
        try:
            hints = json.loads(hints_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            hints = {}

    pilot_symbols = hints.get("pilot_symbols") or []
    profiles = load_profiles()
    memory_on = behavior_memory_enabled()

    adjustments: List[dict] = []
    for row in pilot_symbols[:12]:
        sym = row.get("symbol")
        base_conf = float(row.get("client_ready_shadow_score") or 50)
        setups = [row.get("hidden_cause") or "UNKNOWN"]
        adj_conf, notes = apply_confidence_adjustment(sym, setups, base_conf)
        prof = profiles.get(sym, {})
        adjustments.append({
            "symbol": sym,
            "base_confidence": base_conf,
            "adjusted_confidence": round(adj_conf, 2),
            "delta": round(adj_conf - base_conf, 2),
            "behavior_family": prof.get("sector_behavior_family") or prof.get("behavior_family"),
            "notes": notes,
            "shadow_action": row.get("shadow_action"),
        })

    payload = {
        "success": True,
        "phase": "13_mde_pilot_shadow",
        "pilot_enabled": pilot_enabled(),
        "EGX_MDE_BEHAVIOR_MEMORY": os.environ.get("EGX_MDE_BEHAVIOR_MEMORY", "0"),
        "EGX_MDE_OPP_BOOST": os.environ.get("EGX_MDE_OPP_BOOST", "0"),
        "memory_active": memory_on,
        "pilot_count": len(pilot_symbols),
        "profiles_loaded": len(profiles),
        "adjustments": adjustments,
        "client_path_allowed": False,
        "note": "Shadow confidence adjustments only — no actionable promotion",
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
