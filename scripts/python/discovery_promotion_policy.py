"""
Discovery → delivery promotion policy.

Bridges opportunity_score_v2 stages with client_signal_promotion.
Closes PROMOTION_GAP when discovery confirms edge but UES soft-vetos block.
"""
from __future__ import annotations

import json

# opportunity_score_v2 stages eligible for client promotion
OPP_V2_PROMOTABLE_STAGES = frozenset({
    "ACTIONABLE_CANDIDATE",
    "QUALIFIED_DISCOVERY",
    "NEAR_BREAKOUT",
    "EARLY_ACCUMULATION",
})

# Legacy scan stages (kept for backward compat)
LEGACY_PROMOTABLE_STAGES = frozenset({"ULTRA", "STRONG", "ACCUMULATION"})

# Soft vetos overridable when discovery conviction is high
DISCOVERY_OVERRIDE_VETOS = frozenset({
    "QUALITY_GATE:negative_breadth_ad",
    "QUALITY_GATE:meta_label_low",
    "QUALITY_GATE:survival_sl_dominant",
    "QUALITY_GATE:ml_too_low",
    "LOW_CONVICTION:WATCH",
})

DISCOVERY_OVERRIDE_MIN_OPP = 76.0
DISCOVERY_OVERRIDE_MIN_UES = 70.0
DISCOVERY_OVERRIDE_MIN_STRUCTURE = 58.0


def _row_val(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _flags_list(row) -> list[str]:
    raw = _row_val(row, "flags_json")
    if not raw:
        return []
    try:
        return list(json.loads(raw) if isinstance(raw, str) else raw)
    except Exception:
        return []


def effective_scan_score(row) -> float:
    """UES scan proxy — discovery structure fills gap when source_rules=0."""
    scan = float(_row_val(row, "source_rules") or 0)
    structure = float(_row_val(row, "structure_score") or 0)
    opp = float(_row_val(row, "opportunity_score") or 0)
    if structure > 0:
        scan = max(scan, structure * 0.88)
    if opp >= 75:
        scan = max(scan, opp * 0.68)
    return round(scan, 2)


def is_opp_stage_promotable(stage: str | None) -> bool:
    s = (stage or "").upper()
    if not s:
        return True
    return s in OPP_V2_PROMOTABLE_STAGES or s in LEGACY_PROMOTABLE_STAGES


def discovery_conviction(row) -> dict:
    opp = float(_row_val(row, "opportunity_score") or 0)
    ues = float(_row_val(row, "score") or 0)
    structure = float(_row_val(row, "structure_score") or 0)
    stage = _row_val(row, "opp_stage") or _row_val(row, "stage") or ""
    flags = _flags_list(row)
    return {
        "opp": opp,
        "ues": ues,
        "structure": structure,
        "stage": str(stage).upper(),
        "lower_third": "LOWER_THIRD_CLOSE" in flags,
        "liquidity_expansion": "LIQUIDITY_EXPANSION" in flags,
        "high": (
            opp >= DISCOVERY_OVERRIDE_MIN_OPP
            and ues >= DISCOVERY_OVERRIDE_MIN_UES
            and structure >= DISCOVERY_OVERRIDE_MIN_STRUCTURE
            and str(stage).upper() in OPP_V2_PROMOTABLE_STAGES
        ),
    }


ARBITRATION_SOFT_OVERRIDE_PREFIXES = (
    "execution_infeasible:",
)


def arbitration_allows_discovery_override(veto: str | None, row) -> bool:
    """Override stale/wrong arbitration liquidity veto when discovery confirms edge."""
    if not veto:
        return True
    if not any(veto.startswith(p) for p in ARBITRATION_SOFT_OVERRIDE_PREFIXES):
        return False
    conv = discovery_conviction(row)
    if not conv["high"]:
        return False
    # LIQUIDITY_EXPANSION in opp flags contradicts ILLIQUID arbitration
    if conv["liquidity_expansion"]:
        return True
    # ACTIONABLE_CANDIDATE + strong structure + lower_third (TRADING_LESSONS #8)
    if conv["stage"] == "ACTIONABLE_CANDIDATE" and conv["structure"] >= 85 and conv["lower_third"]:
        return conv["opp"] >= 78
    return False


def veto_allows_discovery_override(veto: str | None, row) -> bool:
    if not veto:
        return True
    conv = discovery_conviction(row)
    if not conv["high"]:
        return False
    if not any(veto.startswith(p) for p in DISCOVERY_OVERRIDE_VETOS):
        return False
    # survival_sl needs stronger discovery proof
    if veto.startswith("QUALITY_GATE:survival_sl_dominant"):
        return conv["opp"] >= 78 and conv["structure"] >= 85 and (
            conv["lower_third"] or conv["liquidity_expansion"]
        )
    return True


_HARD_PREFIXES = ("ANTI_LAW", "HARD_GATE:", "FORECAST_", "ARBITRATION:", "MISSING_RISK", "INVALID_RISK", "RR_TOO_LOW", "FINAL_EDGE:")


def promotion_skip_reason(row, *, min_opp, min_ues, min_scan, min_ml, tier, veto) -> str | None:
    if veto:
        if any(veto.startswith(p) for p in _HARD_PREFIXES):
            return f"hard_veto:{veto}"
        if not veto_allows_discovery_override(veto, row):
            return f"non_overridable_veto:{veto}"

    opp = float(row["opportunity_score"] or 0)
    ues = float(row["score"] or 0)
    ml = float(row["source_ml"] or 0)
    scan = effective_scan_score(row)
    stage = (row["opp_stage"] or "").upper()

    if opp < min_opp:
        return f"opp<{min_opp}"
    if ues < min_ues:
        return f"ues<{min_ues}"
    if scan < min_scan:
        return f"scan<{min_scan}({scan:.1f})"
    if ml < min_ml:
        return f"ml<{min_ml}"
    if stage and not is_opp_stage_promotable(stage) and tier == "MEDIUM":
        return f"stage:{stage}"
    return None
