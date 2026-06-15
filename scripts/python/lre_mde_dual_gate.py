#!/usr/bin/env python3
"""LRE-3.3 dual-gate shadow classification — observe only, no client path."""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from lre_3_2_stages import MONITOR_SUBSTAGES, SUBSTAGE_LABELS, TRADE_SUBSTAGES, classify_substage
from mde_forward_paper_trading import MIN_TRADEABILITY, comp001b_event_ok  # noqa: E402
from mde_hidden_cause_validation import strategic_liquidity  # noqa: E402
from mde_shadow_trade_factory import confirmation_ok, tradeability_score  # noqa: E402

MONITOR_VALID_SUBSTAGES = frozenset({"3B", "4A", "4B"})
LRE_LEAD_SUBSTAGES = frozenset({"3A", "3B", "4A"})
STAGE_QUALITY = {"3B": 0.90, "4A": 0.85, "4B": 0.75, "3A": 0.50, "4X": 0.10}

DUAL_GATE_TYPES = (
    "LRE_ONLY",
    "MDE_ONLY",
    "LRE_MDE_CONFLUENCE",
    "LRE_REJECTED_MDE_PASS",
    "LRE_PASS_MDE_REJECTED",
)


def _parse_tags(row: dict) -> List[str]:
    tags = row.get("list_tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    return tags if isinstance(tags, list) else []


def lre_rejected(row: dict) -> Tuple[bool, List[str]]:
    """LRE explicit reject: 4X, do-not-chase, exploded, high artifact."""
    reasons: List[str] = []
    sub = row.get("sub_stage")
    if sub == "4X":
        reasons.append("4X")
    if int(row.get("already_exploded") or 0):
        reasons.append("already_exploded")
    if "do_not_chase" in _parse_tags(row):
        reasons.append("do_not_chase")
    art_score = float(row.get("artifact_risk_score") or 0)
    if int(row.get("artifact_risk") or 0) or art_score >= 55:
        reasons.append("artifact_high")
    if float(row.get("stop_prone_score") or 0) >= 60:
        reasons.append("stop_prone_extreme")
    return len(reasons) > 0, reasons


def lre_monitoring_valid(row: dict) -> Tuple[bool, List[str], dict]:
    """Valid monitoring candidate: 3B/4A/4B with quality filters (not 3A/4X)."""
    sub = row.get("sub_stage")
    flags = {
        "artifact_flag": int(row.get("artifact_risk") or 0)
        or float(row.get("artifact_risk_score") or 0) >= 45,
        "liquidity_flag": float(row.get("liquidity_fitness_score") or 0) < 35,
        "already_exploded_flag": bool(int(row.get("already_exploded") or 0)),
        "lre_monitoring_only": sub in MONITOR_SUBSTAGES or sub == "4B",
    }
    reasons: List[str] = []
    if sub == "4X":
        return False, ["4X"], flags
    if sub == "3A":
        return False, ["3A_monitor_only_no_dual_pass"], flags
    if sub not in MONITOR_VALID_SUBSTAGES:
        return False, [f"sub_stage={sub}"], flags
    if flags["artifact_flag"]:
        reasons.append("artifact_risk")
    if flags["liquidity_flag"]:
        reasons.append("low_liquidity")
    if flags["already_exploded_flag"]:
        reasons.append("already_exploded")
    if "do_not_chase" in _parse_tags(row):
        reasons.append("do_not_chase")
    if float(row.get("stop_prone_score") or 0) >= 55:
        reasons.append("stop_prone_high")
    return len(reasons) == 0, reasons, flags


def lre_monitoring_sighting(row: Optional[dict]) -> bool:
    if not row:
        return False
    sub = row.get("sub_stage")
    if sub not in TRADE_SUBSTAGES | MONITOR_SUBSTAGES:
        return False
    rejected, _ = lre_rejected(row)
    return not rejected


def assess_mde_gate(event: dict, astat: dict) -> dict:
    """MDE confirmation from existing enriched outputs — does not modify MDE."""
    liq_info = strategic_liquidity(event, astat)
    liq_type = liq_info.get("liquidity_type", "")
    trad = tradeability_score(event)
    conf = confirmation_ok(event)
    hidden = bool(event.get("hidden_repricing"))
    comp001b = comp001b_event_ok(event, astat)
    watch = (float(event.get("discovery_score") or 0) >= 45 or hidden)
    timing = event.get("timing_class") or "UNKNOWN"

    risk_flags: List[str] = []
    reason_codes: List[str] = []
    if liq_type in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY"):
        risk_flags.append(liq_type)
    if timing in ("LATE", "TOO_LATE", "POST_MOVE_RISK"):
        risk_flags.append(timing)

    passed = (
        watch
        and conf
        and trad >= MIN_TRADEABILITY
        and liq_type not in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY")
        and timing not in ("LATE", "TOO_LATE", "POST_MOVE_RISK")
        and (comp001b or (hidden and conf and float(event.get("effective_score") or 0) > 55))
    )
    if hidden:
        reason_codes.append("hidden_repricing")
    if comp001b:
        reason_codes.append("COMP_001B")
    if conf:
        reason_codes.append("confirmation_ok")
    if trad >= MIN_TRADEABILITY:
        reason_codes.append("tradeability_ok")

    score = float(event.get("effective_score") or event.get("discovery_score") or 0)
    return {
        "mde_gate_passed": bool(passed),
        "mde_stage": event.get("mde_stage"),
        "mde_score": round(score, 2),
        "mde_reason_codes": reason_codes,
        "mde_risk_flags": risk_flags,
        "tradeability": round(trad, 1),
        "hidden_repricing": hidden,
        "comp001b": comp001b,
        "confirmation_ok": conf,
        "liquidity_type": liq_type,
    }


def classify_dual_gate_type(
    lre_row: Optional[dict],
    mde_gate: Optional[dict],
) -> Tuple[str, str]:
    """Return (dual_gate_type, dual_gate_reason)."""
    mde_pass = bool(mde_gate and mde_gate.get("mde_gate_passed"))
    if lre_row:
        valid, lre_reasons, _ = lre_monitoring_valid(lre_row)
        rejected, rej_reasons = lre_rejected(lre_row)
        sighting = lre_monitoring_sighting(lre_row)
    else:
        valid, lre_reasons, rejected, rej_reasons, sighting = False, [], False, [], False

    if valid and mde_pass:
        return "LRE_MDE_CONFLUENCE", "LRE 3B/4A/4B + MDE confirmation"
    if mde_pass and rejected and lre_row:
        return "LRE_REJECTED_MDE_PASS", f"MDE pass but LRE rejects: {','.join(rej_reasons)}"
    if valid and not mde_pass:
        return "LRE_PASS_MDE_REJECTED", f"LRE valid monitoring, MDE no confirm: {lre_reasons}"
    if mde_pass and not sighting:
        return "MDE_ONLY", "MDE pass without LRE monitoring sighting"
    if lre_row and rejected and not mde_pass:
        return "LRE_ONLY", f"LRE rejected sighting ({','.join(rej_reasons)}) — no MDE"
    if sighting and not mde_pass:
        sub = (lre_row or {}).get("sub_stage")
        if sub == "3A":
            return "LRE_ONLY", "LRE 3A early monitoring — no MDE"
        return "LRE_ONLY", "LRE monitoring sighting without MDE confirmation"
    if mde_pass:
        return "MDE_ONLY", "MDE pass — no LRE row"
    return "LRE_ONLY", "shadow_audit_unclassified"


def supply_absorption_overlap(lre_row: dict, mde_gate: dict) -> float:
    sp = lre_row.get("supply_exhaustion_detail") or {}
    overlap = 0.0
    if mde_gate.get("hidden_repricing"):
        overlap += 0.50
    if sp.get("green_red_asymmetry"):
        overlap += 0.25
    if sp.get("lower_wick_absorption"):
        overlap += 0.15
    if mde_gate.get("comp001b"):
        overlap += 0.10
    return min(1.0, overlap)


def dual_gate_score(lre_row: Optional[dict], mde_gate: dict) -> float:
    """Audit ranking only — not for trading recommendations."""
    mde_norm = min(1.0, float(mde_gate.get("mde_score") or 0) / 100.0)
    sub = (lre_row or {}).get("sub_stage") or "none"
    stage_q = STAGE_QUALITY.get(sub, 0.20)
    supply = supply_absorption_overlap(lre_row, mde_gate) if lre_row else 0.0
    liq = float((lre_row or {}).get("liquidity_fitness_score") or 50) / 100.0
    move20 = float((lre_row or {}).get("move_from_low_20d_pct") or 0)
    not_ext = max(0.0, 1.0 - move20 / 15.0)

    raw = (
        0.40 * mde_norm
        + 0.25 * stage_q
        + 0.15 * supply
        + 0.10 * liq
        + 0.10 * not_ext
    )
    if lre_row:
        if lre_row.get("sub_stage") == "4X":
            raw -= 0.30
        if int(lre_row.get("already_exploded") or 0):
            raw -= 0.20
        if "do_not_chase" in _parse_tags(lre_row):
            raw -= 0.15
        if float(lre_row.get("artifact_risk_score") or 0) >= 45 or int(lre_row.get("artifact_risk") or 0):
            raw -= 0.15
        if float(lre_row.get("liquidity_fitness_score") or 0) < 35:
            raw -= 0.10
    return round(max(0.0, min(100.0, raw * 100)), 1)


def lre_row_summary(row: dict) -> dict:
    valid, reasons, flags = lre_monitoring_valid(row)
    rejected, rej = lre_rejected(row)
    return {
        "lre_stage": row.get("stage"),
        "lre_sub_stage": row.get("sub_stage"),
        "lre_sub_label": SUBSTAGE_LABELS.get(row.get("sub_stage"), row.get("sub_stage")),
        "lre_eps": row.get("explosion_potential"),
        "lre_candidate_type": row.get("candidate_type") or row.get("stage_name"),
        "lre_reason_codes": reasons,
        "lre_risk_flags": rej if rejected else [],
        "lre_monitoring_only": flags.get("lre_monitoring_only", False),
        "lre_monitoring_valid": valid,
        "artifact_flag": flags.get("artifact_flag"),
        "liquidity_flag": flags.get("liquidity_flag"),
        "already_exploded_flag": flags.get("already_exploded_flag"),
    }
