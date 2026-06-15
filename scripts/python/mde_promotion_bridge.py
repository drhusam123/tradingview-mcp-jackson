#!/usr/bin/env python3
"""
MDE Phase 11 — shadow promotion bridge (hints only, no client path).

Reads client-grade rerank/ranking artifacts and emits pilot hints for
intelligence_prioritizer / discovery_context. Never promotes actionable
or enables EGX_MDE_OPP_BOOST.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

INPUTS = {
    "rerank": DATA / "mde_current_candidates_client_grade_rerank.json",
    "ranking": DATA / "mde_client_ready_shadow_ranking.json",
    "gate": DATA / "mde_client_grade_gate_status.json",
}
OUTPUT = DATA / "mde_shadow_promotion_hints.json"

PILOT_ACTIONS = {
    "HIGH_QUALITY_PENDING_CONFIRMATION",
    "WATCH",
    "MONITOR",
    "ACTIONABLE_SHADOW",
}
MIN_TRADEABILITY = 70.0
MIN_SHADOW_SCORE = 65.0


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _eligible_row(row: dict) -> bool:
    action = str(row.get("final_shadow_action") or row.get("new_decision") or "")
    if action in ("REJECT", "FAIL", "DO_NOT_TRADE"):
        return False
    if action not in PILOT_ACTIONS and "HIGH_QUALITY" not in action:
        return False
    if row.get("lookahead_flag"):
        return False
    if float(row.get("tradeability_score") or 0) < MIN_TRADEABILITY:
        return False
    if float(row.get("client_ready_shadow_score") or 0) < MIN_SHADOW_SCORE:
        return False
    return True


def _normalize(row: dict) -> dict:
    sym = row.get("symbol")
    action = str(row.get("final_shadow_action") or row.get("new_decision") or "")
    return {
        "symbol": sym,
        "shadow_action": action,
        "client_ready_shadow_score": row.get("client_ready_shadow_score"),
        "tradeability_score": row.get("tradeability_score"),
        "hidden_cause": row.get("hidden_cause"),
        "timing_class": row.get("timing_class"),
        "liquidity_type": row.get("liquidity_type"),
        "risk_flags": row.get("risk_flags") or [],
        "mode": "shadow_hint_only",
        "client_path_allowed": False,
    }


def build_hints(trade_date: str | None = None) -> dict:
    rerank = _load(INPUTS["rerank"]) or {}
    ranking = _load(INPUTS["ranking"]) or {}
    gate = _load(INPUTS["gate"]) or {}

    signal_date = trade_date or rerank.get("date") or ranking.get("date")
    rows: List[dict] = list(ranking.get("ranked") or rerank.get("candidates") or [])

    seen = set()
    pilot: List[dict] = []
    for row in rows:
        sym = row.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if _eligible_row(row):
            pilot.append(_normalize(row))

    pilot.sort(key=lambda x: float(x.get("client_ready_shadow_score") or 0), reverse=True)
    pilot = pilot[:12]

    comp = gate.get("COMP_001B") or {}
    gate_passed = int(comp.get("gates_passed") or 0)
    gate_required = int(comp.get("gates_required") or 7)
    client_path_allowed = bool(comp.get("client_path_allowed"))

    out = {
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": "11_shadow_bridge",
        "signal_date": signal_date,
        "mode": "shadow_hints_only",
        "client_path_allowed": False,
        "egx_mde_opp_boost": 0,
        "pilot_eligible": len(pilot) >= 3 and not client_path_allowed,
        "pilot_symbols": pilot,
        "pilot_count": len(pilot),
        "mde_gate": {
            "COMP_001B_status": comp.get("status"),
            "gates_passed": gate_passed,
            "gates_required": gate_required,
            "client_path_allowed": client_path_allowed,
        },
        "inputs_present": {k: v.exists() for k, v in INPUTS.items()},
        "note": "Hints for prioritizer/research blocks only — no actionable promotion",
    }
    return out


def run(params: dict | None = None) -> dict:
    params = params or {}
    out = build_hints(params.get("trade_date"))
    DATA.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"success": True, "output": str(OUTPUT), **out}


if __name__ == "__main__":
    p: Dict[str, Any] = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2, ensure_ascii=False))
