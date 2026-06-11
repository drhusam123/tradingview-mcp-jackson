"""
Discovery feedback loader — P6 closed loop → quant discovery + scoring.
Reads data/discovery_feedback_last.json produced by egx_closed_loop.mjs.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEEDBACK_PATH = ROOT / "data" / "discovery_feedback_last.json"

# Condition atoms penalized when P6 shows behavioral/pattern weakness
BEHAVIORAL_PENALTIES = {
    "EXPLOSIVE": {
        "vol_lt1_5": 0.55,
        "vol_gt3": 0.70,
        "vol_3_8": 0.65,
        "vol_gt5": 0.55,
        "upper_close": 0.75,
        "very_upper_close": 0.65,
        "high20_break": 0.80,
    },
    "VOLATILE": {
        "range_gt9pct": 0.75,
        "vol_gt5": 0.70,
        "very_upper_close": 0.80,
    },
}

BEHAVIORAL_BOOSTS = {
    "ACCUMULATION": {
        "vol_2_5_3": 1.08,
        "lower_third_close": 1.06,
        "low20_retest": 1.05,
    },
}

PATTERN_PENALTIES = {
    "repeat_ultra_loser": {"vol_gt3": 0.72, "vol_3_8": 0.68, "very_upper_close": 0.75},
    "explosive_low_vol": {"vol_lt1_5": 0.50},
    "upper_third_close": {"upper_close": 0.70, "very_upper_close": 0.62},
    "volume_chase": {"vol_gt3": 0.58, "vol_gt5": 0.50, "vol_3_8": 0.62},
    "post_breakout_vol_collapse": {"vol_lt1_5": 0.65, "high20_break": 0.78},
}


def load_feedback_queue() -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    try:
        data = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
        return list(data.get("queue") or [])
    except Exception:
        return []


def _condition_multiplier(conditions: list[str], queue: list[dict]) -> float:
    mult = 1.0
    cond_set = set(conditions or [])
    for item in queue:
        itype = item.get("type", "")
        target = item.get("target", "")
        if itype == "DOWNRANK_BEHAVIORAL":
            for c, m in BEHAVIORAL_PENALTIES.get(target, {}).items():
                if c in cond_set:
                    mult *= m
        elif itype == "UPRANK_BEHAVIORAL":
            for c, m in BEHAVIORAL_BOOSTS.get(target, {}).items():
                if c in cond_set:
                    mult *= m
        elif itype == "INVESTIGATE_PATTERN":
            for c, m in PATTERN_PENALTIES.get(target, {}).items():
                if c in cond_set:
                    mult *= m
    return max(0.35, min(1.25, mult))


def adjust_rule_composite(rule: dict, queue: list[dict] | None = None) -> dict:
    """Apply feedback multiplier to a quant_discovery rule dict."""
    queue = queue if queue is not None else load_feedback_queue()
    conds = rule.get("conditions")
    if not conds and rule.get("rule_name"):
        conds = [c.strip() for c in str(rule["rule_name"]).split("+")]
    mult = _condition_multiplier(conds or [], queue)
    if "composite_score" in rule:
        rule["composite_score"] = round(float(rule["composite_score"]) * mult, 4)
    rule["feedback_multiplier"] = round(mult, 4)
    return rule


def adjust_match_score(
    score: float,
    conditions: list[str],
    behavioral_class: str | None = None,
    queue: list[dict] | None = None,
) -> float:
    """Adjust live quant_discovery match score from P6 feedback."""
    queue = queue if queue is not None else load_feedback_queue()
    mult = _condition_multiplier(conditions or [], queue)
    bclass = (behavioral_class or "").upper()
    for item in queue:
        if item.get("type") == "DOWNRANK_BEHAVIORAL" and item.get("target") == bclass:
            mult *= 0.88
        if item.get("type") == "UPRANK_BEHAVIORAL" and item.get("target") == bclass:
            mult *= 1.06
    return max(35.0, min(92.0, float(score) * mult))


def feedback_summary(queue: list[dict] | None = None) -> dict:
    queue = queue if queue is not None else load_feedback_queue()
    return {
        "n_items": len(queue),
        "downrank": [x for x in queue if x.get("type") == "DOWNRANK_BEHAVIORAL"],
        "uprank": [x for x in queue if x.get("type") == "UPRANK_BEHAVIORAL"],
        "patterns": [x for x in queue if x.get("type") == "INVESTIGATE_PATTERN"],
        "promotion_gap": [x for x in queue if x.get("type") == "PROMOTION_GAP"],
    }


# Atoms boosted when P6 directives target counterfactual / residual loss gaps
DIRECTIVE_BOOST_ATOMS = {
    "counterfactual_wr_lift": {"lower_third_close", "vol_2_5_3", "low20_retest"},
    "residual_loss_gap": {"vol_2_5_3", "lower_third_close"},
    "opp_missed_high": {"vol_2_5_3", "low20_retest"},
    "opp_missed_trend": {"vol_2_5_3", "low20_retest"},
}


def apply_p6_research_hints(candidates: list[dict], params: dict) -> list[dict]:
    """Boost quant rules aligned with P6 priorities and pending research directives."""
    priorities = params.get("p6_priorities") or []
    directives = set(params.get("p6_directives") or [])
    hints = params.get("evolution_hints") or {}

    boost_atoms: set[str] = set()
    for item in priorities:
        itype = item.get("type", "")
        target = item.get("target", "")
        if itype == "UPRANK_BEHAVIORAL":
            boost_atoms.update(BEHAVIORAL_BOOSTS.get(target, {}).keys())
        if itype == "INVESTIGATE_PATTERN" and target in PATTERN_PENALTIES:
            # Already penalized via feedback queue — skip double boost
            pass

    for directive, atoms in DIRECTIVE_BOOST_ATOMS.items():
        if directive in directives:
            boost_atoms.update(atoms)

    downrank_classes = {
        item.get("target", "").upper()
        for item in priorities
        if item.get("type") == "DOWNRANK_BEHAVIORAL"
    }
    if hints.get("downrank_behavioral"):
        downrank_classes.update(str(x).upper() for x in hints["downrank_behavioral"])

    for rule in candidates:
        conds = set(rule.get("conditions") or [])
        if not conds and rule.get("rule_name"):
            conds = {c.strip() for c in str(rule["rule_name"]).split("+")}
        mult = 1.0
        if conds & boost_atoms:
            mult *= 1.08
        rule_name = str(rule.get("rule_name", "")).upper()
        if any(cls in rule_name for cls in downrank_classes):
            mult *= 0.92
        if mult != 1.0 and "composite_score" in rule:
            rule["composite_score"] = round(float(rule["composite_score"]) * mult, 4)
            rule["p6_hint_multiplier"] = round(mult, 4)
    return candidates


def load_promotion_tuning(
    queue: list[dict] | None = None,
    followup: dict | None = None,
) -> dict:
    """Closed-loop tuning for client_signal_promotion thresholds."""
    queue = queue if queue is not None else load_feedback_queue()
    tuning = {
        "min_opportunity": 75.0,
        "min_ues": 70.0,
        "min_scan": 58.0,
        "min_ml": 55.0,
        "adjustments": [],
    }

    strict = any(item.get("type") == "DISCOVERY_QUALITY_LOW" for item in queue)
    if strict:
        tuning["min_opportunity"] += 1.0
        tuning["adjustments"].append("DISCOVERY_QUALITY_LOW — slightly tighter promotion")

    for item in queue:
        if item.get("type") == "PROMOTION_GAP":
            tuning["min_opportunity"] -= 3.0
            tuning["min_ues"] -= 2.0
            tuning["min_scan"] -= 1.0
            tuning["adjustments"].append(item.get("rationale") or "PROMOTION_GAP")

    for alert in (followup or {}).get("alerts") or []:
        code = alert.get("code", "")
        if code == "MISSED_HIGH_OPP_RISING":
            tuning["min_opportunity"] -= 2.0
            tuning["min_ues"] -= 1.0
            tuning["adjustments"].append(alert.get("message") or code)
        elif code == "QUALITY_DECLINING":
            tuning["min_opportunity"] += 2.0
            tuning["adjustments"].append(alert.get("message") or code)
        elif code == "DELIVERY_IMPROVING":
            tuning["min_opportunity"] += 1.0

    tuning["min_opportunity"] = max(68.0, min(80.0, tuning["min_opportunity"]))
    tuning["min_ues"] = max(65.0, min(78.0, tuning["min_ues"]))
    tuning["min_scan"] = max(52.0, min(65.0, tuning["min_scan"]))
    tuning["min_ml"] = max(50.0, min(62.0, tuning["min_ml"]))
    return tuning


def load_opportunity_tuning(
    queue: list[dict] | None = None,
    followup: dict | None = None,
) -> dict:
    """Extra failure_penalty boost from P6 behavioral downrank + opp trends."""
    queue = queue if queue is not None else load_feedback_queue()
    penalty_boost = 0.0
    downrank_classes: set[str] = set()
    reasons: list[str] = []

    for item in queue:
        if item.get("type") == "DOWNRANK_BEHAVIORAL":
            downrank_classes.add(str(item.get("target", "")).upper())
            penalty_boost += 2.0
            reasons.append(item.get("rationale") or item.get("target"))

    for alert in (followup or {}).get("alerts") or []:
        if alert.get("code") == "QUALITY_DECLINING":
            penalty_boost += 3.0
            reasons.append(alert.get("message") or "QUALITY_DECLINING")

    return {
        "failure_penalty_boost": min(12.0, penalty_boost),
        "downrank_classes": sorted(downrank_classes),
        "reasons": reasons[:6],
    }
