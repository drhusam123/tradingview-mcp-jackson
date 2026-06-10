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
    }
