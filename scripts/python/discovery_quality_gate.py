"""
Discovery Quality Gate — TRADING_LESSONS-aligned rule filtering.

Raises precision of quant_discovery output by rejecting toxic atom combos
and boosting sweet-spot patterns (lower_third_close, vol_2_5_3).
"""
from __future__ import annotations

from typing import Any

# TRADING_LESSONS #8, #10 — highest historical WR atoms
SWEET_SPOT_ATOMS = frozenset({
    "vol_2_5_3",
    "vol_1_8_3",
    "lower_third_close",
    "low20_retest",
    "vol_2_4",
})

# TRADING_LESSONS #1, #2 — toxic without confirmation
TOXIC_ATOMS = frozenset({
    "vol_gt5",
    "vol_3_8",
    "very_upper_close",
    "vol_lt1_5",
    "range_gt9pct",
})

TOXIC_COMBOS = (
    frozenset({"near_ath_300", "vol_lt1_5"}),
    frozenset({"vol_gt5", "upper_close"}),
    frozenset({"high20_break", "vol_lt1_5"}),
    frozenset({"very_upper_close", "vol_gt3"}),
)

VOL_CONFIRM_ATOMS = frozenset({"vol_2_5_3", "vol_1_8_3", "vol_2_4", "vol_1_5_3", "vol_gt3"})

DEFAULT_GATES = {
    "min_quality_score": 52.0,
    "min_stability": 0.62,
    "min_oos_lift": 1.02,
    "max_oos_stop_rate": 0.58,
    "min_oos_profit_factor": 1.04,
    "min_oos_precision": 0.0,  # set dynamically vs baseline
}


def _conditions(rule: dict) -> set[str]:
    conds = rule.get("conditions")
    if conds:
        return set(conds)
    name = str(rule.get("rule_name") or "")
    return {c.strip() for c in name.split("+") if c.strip()}


def score_rule_quality(rule: dict) -> dict[str, Any]:
    """Per-rule quality score 0–100 aligned with TRADING_LESSONS."""
    conds = _conditions(rule)
    score = 50.0
    notes: list[str] = []

    lift = float(rule.get("oos_lift") or 0)
    stability = float(rule.get("stability_score") or 0)
    pf = float(rule.get("oos_profit_factor") or 0)
    stop = float(rule.get("oos_stop_rate") or 1)
    precision = float(rule.get("oos_precision") or 0)
    baseline = float(rule.get("baseline_precision") or 0.395)

    if lift >= 1.15:
        score += 8
        notes.append("lift≥1.15")
    elif lift >= 1.08:
        score += 4
    if stability >= 0.78:
        score += 7
        notes.append("stable")
    elif stability >= 0.70:
        score += 3
    if pf >= 1.35:
        score += 6
    elif pf >= 1.15:
        score += 3
    if stop <= 0.42:
        score += 5
        notes.append("low_stop")
    elif stop > 0.55:
        score -= 6
    if precision >= baseline + 0.04:
        score += 4

    sweet = conds & SWEET_SPOT_ATOMS
    if sweet:
        score += min(14, 4 * len(sweet))
        notes.append(f"sweet:{','.join(sorted(sweet)[:3])}")
    if "lower_third_close" in conds:
        score += 6  # rule #8 — highest WR close zone

    toxic = conds & TOXIC_ATOMS
    if toxic:
        score -= min(18, 5 * len(toxic))
        notes.append(f"toxic:{','.join(sorted(toxic)[:3])}")

    for combo in TOXIC_COMBOS:
        if combo <= conds:
            score -= 14
            notes.append("toxic_combo")
            break

    if "near_ath_300" in conds and not (conds & VOL_CONFIRM_ATOMS):
        score -= 16
        notes.append("near_ath_no_vol")

    quality_score = round(max(0.0, min(100.0, score)), 1)
    return {
        "quality_score": quality_score,
        "sweet_atoms": sorted(sweet),
        "toxic_atoms": sorted(toxic),
        "notes": notes[:5],
    }


def passes_quality_gate(rule: dict, gates: dict | None = None) -> bool:
    gates = {**DEFAULT_GATES, **(gates or {})}
    q = rule.get("quality_tags") or score_rule_quality(rule)
    quality = float(rule.get("quality_score") or q["quality_score"])
    baseline = float(rule.get("baseline_precision") or 0.395)
    precision = float(rule.get("oos_precision") or 0)

    if quality < gates["min_quality_score"]:
        return False
    if float(rule.get("stability_score") or 0) < gates["min_stability"]:
        return False
    if float(rule.get("oos_lift") or 0) < gates["min_oos_lift"]:
        return False
    if float(rule.get("oos_stop_rate") or 1) > gates["max_oos_stop_rate"]:
        return False
    if float(rule.get("oos_profit_factor") or 0) < gates["min_oos_profit_factor"]:
        return False
    if precision < max(baseline, gates["min_oos_precision"]):
        return False
    if q.get("toxic_atoms") and "toxic_combo" in (q.get("notes") or []):
        return False
    return True


def filter_quant_candidates(
    candidates: list[dict],
    params: dict | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Apply quality gate; annotate survivors with quality_score."""
    params = params or {}
    gates = {**DEFAULT_GATES, **(params.get("quality_gates") or {})}

    # Tighten when P6 gate failing or discovery quality declining
    p6_gate = params.get("p6_gate") or {}
    if p6_gate.get("gate_pass") is False:
        gates["min_quality_score"] = max(gates["min_quality_score"], 56.0)
        gates["min_stability"] = max(gates["min_stability"], 0.68)
    if params.get("strict_quality"):
        gates["min_quality_score"] = max(gates["min_quality_score"], 58.0)

    queue = params.get("feedback_queue") or []
    if any(item.get("type") == "DISCOVERY_QUALITY_LOW" for item in queue):
        gates["min_quality_score"] = max(gates["min_quality_score"], 58.0)
        gates["min_stability"] = max(gates["min_stability"], 0.70)
        gates["max_oos_stop_rate"] = min(gates["max_oos_stop_rate"], 0.52)

    passed: list[dict] = []
    rejected: list[dict] = []
    sweet_hits = 0

    for rule in candidates:
        tags = score_rule_quality(rule)
        rule["quality_score"] = tags["quality_score"]
        rule["quality_tags"] = tags
        if tags["sweet_atoms"]:
            sweet_hits += 1
        if passes_quality_gate(rule, gates):
            passed.append(rule)
        else:
            rejected.append({
                "rule": rule.get("rule_name"),
                "quality_score": tags["quality_score"],
                "reason": tags["notes"][:2],
            })

    summary = {
        "n_in": len(candidates),
        "n_pass": len(passed),
        "n_reject": len(rejected),
        "sweet_atom_rules": sweet_hits,
        "avg_quality_pass": round(
            sum(r["quality_score"] for r in passed) / len(passed), 1
        ) if passed else 0,
        "avg_quality_reject": round(
            sum(r["quality_score"] for r in rejected) / len(rejected), 1
        ) if rejected else 0,
        "gates_applied": gates,
        "rejected_sample": rejected[:8],
    }
    return passed, summary


def score_discovery_run(
    quant_summary: dict | None = None,
    opp_summary: dict | None = None,
) -> dict[str, Any]:
    """Aggregate discovery run quality 0–100."""
    score = 50.0
    components: dict[str, float] = {}

    if quant_summary:
        n_pass = quant_summary.get("n_pass") or quant_summary.get("rules_kept") or 0
        n_in = quant_summary.get("n_in") or max(n_pass, 1)
        pass_rate = n_pass / n_in if n_in else 0
        avg_q = float(quant_summary.get("avg_quality_pass") or quant_summary.get("avg_quality") or 0)
        sweet = quant_summary.get("sweet_atom_rules") or 0
        components["quant_pass_rate"] = round(pass_rate * 100, 1)
        components["quant_avg_quality"] = avg_q
        score += min(15, pass_rate * 20)
        score += min(12, avg_q * 0.12)
        score += min(8, sweet * 0.5)

    if opp_summary:
        qualified = int(opp_summary.get("qualified_plus") or 0)
        scored = int(opp_summary.get("symbols_scored") or 1)
        avg_opp = float(opp_summary.get("avg_opportunity_score") or 0)
        lower_third = int(opp_summary.get("lower_third_count") or 0)
        components["opp_qualified_pct"] = round(qualified / scored * 100, 1) if scored else 0
        components["opp_avg_score"] = avg_opp
        score += min(10, components["opp_qualified_pct"] * 0.08)
        score += min(8, avg_opp * 0.08)
        score += min(6, lower_third * 0.3)

    discovery_quality = round(max(0.0, min(100.0, score)), 1)
    grade = (
        "A" if discovery_quality >= 75 else
        "B" if discovery_quality >= 62 else
        "C" if discovery_quality >= 50 else
        "D"
    )
    return {
        "discovery_quality_score": discovery_quality,
        "grade": grade,
        "components": components,
    }
