#!/usr/bin/env python3
"""
evaluate_negative_breadth_policy() — Phase 2.9 (2.9D wired in collect_quality_gate_failures + conviction)

Convert negative_breadth_ad from flat veto (ad_ratio < 1.0) to tiered response:
  PASS              — ad_ratio >= 0.8 (or >= 1.0 neutral)
  SOFT_PENALTY      — 0.6 <= ad_ratio < 0.8
  STRONG_PENALTY    — 0.4 <= ad_ratio < 0.6
  HARD_VETO         — ad_ratio < 0.4 (unless relative-strength override)
  OVERRIDE_TO_SOFT  — severe breadth but UES/ML strong (existing exceptions)

Usage:
    python3 scripts/python/negative_breadth_policy.py test
    python3 scripts/python/negative_breadth_policy.py outcome '{"start_date":"2026-06-01"}'
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

NEG_BREADTH_GATE = "negative_breadth_ad"

ACTIONABLE_CONVICTION = frozenset({
    "ULTRA_CONVICTION", "HIGH_CONVICTION", "MEDIUM_CONVICTION",
})

CONVICTION_DOWNGRADE = {
    "ULTRA_CONVICTION": "HIGH_CONVICTION",
    "HIGH_CONVICTION": "MEDIUM_CONVICTION",
    "MEDIUM_CONVICTION": "WATCH",
    "WATCH": "REJECT",
    "REJECT": "REJECT",
}

STRONG_DOWNGRADE_TWICE = {
    "ULTRA_CONVICTION": "MEDIUM_CONVICTION",
    "HIGH_CONVICTION": "WATCH",
    "MEDIUM_CONVICTION": "REJECT",
    "WATCH": "REJECT",
    "REJECT": "REJECT",
}


def safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def downgrade_conviction(conviction: str | None, levels: int = 1) -> str:
    cur = conviction or "REJECT"
    for _ in range(levels):
        cur = CONVICTION_DOWNGRADE.get(cur, "REJECT")
    return cur


def old_negative_breadth_would_fail(ad_ratio, ues: float, ml_score: float) -> bool:
    """Current production rule in collect_quality_gate_failures."""
    ad = safe_float(ad_ratio)
    if ad is None or ad >= 1.0:
        return False
    if ues >= 78.0 and ml_score >= 72.0:
        return False
    if ues >= 75.0 and ml_score >= 80.0:
        return False
    return True


def strength_override(ad_ratio, ues: float, ml_score: float) -> bool:
    """Existing relative-strength exceptions — severe breadth only."""
    ad = safe_float(ad_ratio)
    if ad is None or ad >= 0.4:
        return False
    return (ues >= 78.0 and ml_score >= 72.0) or (ues >= 75.0 and ml_score >= 80.0)


def evaluate_negative_breadth_policy(
    *,
    ad_ratio,
    ues: float = 0.0,
    ml_score: float = 0.0,
    conviction: str | None = None,
    forecast_veto: str | None = None,
    anti_law: bool = False,
) -> dict:
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)
    ad = safe_float(ad_ratio)

    result = {
        "applies": False,
        "ad_ratio": ad,
        "old_policy": "NONE",
        "new_policy": "NONE",
        "new_reason": None,
        "would_fail_old": False,
        "would_fail_new": False,
        "conviction_downgrade_levels": 0,
        "adjusted_conviction": conviction,
        "position_multiplier": 1.0,
    }

    if ad is None or ad >= 1.0:
        return result

    result["applies"] = True
    result["old_policy"] = "HARD_VETO" if old_negative_breadth_would_fail(ad, ues, ml_score) else "PASS_LEGACY_EXCEPTION"
    result["would_fail_old"] = old_negative_breadth_would_fail(ad, ues, ml_score)

    if ad >= 0.8:
        result.update({
            "new_policy": "PASS",
            "new_reason": "NEG_BREADTH_MONITOR",
            "would_fail_new": False,
            "position_multiplier": 1.0,
        })
        return result

    if ad >= 0.6:
        adj = downgrade_conviction(conviction, 1)
        result.update({
            "new_policy": "SOFT_PENALTY",
            "new_reason": "NEG_BREADTH_SOFT_PENALTY",
            "would_fail_new": False,
            "conviction_downgrade_levels": 1,
            "adjusted_conviction": adj,
            "position_multiplier": 0.75,
        })
        return result

    if ad >= 0.4:
        adj = STRONG_DOWNGRADE_TWICE.get(conviction or "REJECT", "REJECT")
        result.update({
            "new_policy": "STRONG_PENALTY",
            "new_reason": "NEG_BREADTH_STRONG_WARNING",
            "would_fail_new": adj not in ACTIONABLE_CONVICTION,
            "conviction_downgrade_levels": 2,
            "adjusted_conviction": adj,
            "position_multiplier": 0.5,
        })
        return result

    # ad < 0.4 — conditional hard veto
    if strength_override(ad, ues, ml_score):
        adj = downgrade_conviction(conviction, 1)
        result.update({
            "new_policy": "OVERRIDE_TO_SOFT",
            "new_reason": "NEG_BREADTH_STRENGTH_OVERRIDE",
            "would_fail_new": False,
            "conviction_downgrade_levels": 1,
            "adjusted_conviction": adj,
            "position_multiplier": 0.75,
        })
        return result

    result.update({
        "new_policy": "HARD_VETO",
        "new_reason": "NEG_BREADTH_HARD_VETO",
        "would_fail_new": True,
        "position_multiplier": 0.0,
    })
    return result


def build_negative_breadth_shadow_fields(
    ad_ratio, ues, ml_score, conviction=None, forecast_veto=None, anti_law=0,
) -> dict:
    pol = evaluate_negative_breadth_policy(
        ad_ratio=ad_ratio,
        ues=safe_float(ues, 0.0),
        ml_score=safe_float(ml_score, 0.0),
        conviction=conviction,
        forecast_veto=forecast_veto,
        anti_law=bool(anti_law),
    )
    return {
        "shadow_neg_breadth_policy": pol.get("new_policy"),
        "shadow_neg_breadth_reason": pol.get("new_reason"),
        "shadow_neg_breadth_would_fail_old": 1 if pol.get("would_fail_old") else 0,
        "shadow_neg_breadth_would_fail_new": 1 if pol.get("would_fail_new") else 0,
        "shadow_neg_breadth_adjusted_conviction": pol.get("adjusted_conviction"),
        "shadow_neg_breadth_position_mult": pol.get("position_multiplier"),
    }


def _parse_qg_failures(row) -> list:
    qf = row.get("quality_gate_failures")
    if isinstance(qf, str):
        try:
            return json.loads(qf)
        except Exception:
            return []
    return qf or []


def passes_except_neg_breadth(row: dict) -> bool:
    qf = [f for f in _parse_qg_failures(row) if f != NEG_BREADTH_GATE]
    if qf:
        return False
    if int(row.get("anti_law") or 0):
        return False
    if not int(row.get("final_edge_passed") or 0):
        return False
    if row.get("forecast_veto"):
        return False
    bucket = row.get("shadow_risk_bucket")
    if bucket and not int(row.get("shadow_risk_valid_for_rr") or 0):
        return False
    entry = row.get("entry_price")
    stop = row.get("stop_loss")
    target = row.get("t1_target")
    if not (entry and stop and target and stop < entry < target):
        return False
    rr = safe_float(row.get("shadow_computed_rr") or row.get("r_ratio"))
    if rr is not None and rr < 1.3:
        return False
    return True


def simulate_would_be_actionable(row: dict, pol: dict) -> bool:
    if not pol.get("applies"):
        return bool(int(row.get("actionable") or 0))
    if pol.get("would_fail_new"):
        return False
    if not passes_except_neg_breadth(row):
        return False
    conv = pol.get("adjusted_conviction") or row.get("conviction")
    if pol.get("new_policy") in ("SOFT_PENALTY", "STRONG_PENALTY", "OVERRIDE_TO_SOFT"):
        conv = pol.get("adjusted_conviction")
    return conv in ACTIONABLE_CONVICTION


# ─── Spec tests ───────────────────────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Test 1 — ad 0.9 now PASS (was old fail)",
        "kwargs": {"ad_ratio": 0.9, "ues": 70, "ml_score": 70, "conviction": "HIGH_CONVICTION"},
        "expect": {"new_policy": "PASS", "would_fail_old": True, "would_fail_new": False},
    },
    {
        "name": "Test 2 — ad 0.7 SOFT",
        "kwargs": {"ad_ratio": 0.7, "ues": 70, "ml_score": 70, "conviction": "HIGH_CONVICTION"},
        "expect": {"new_policy": "SOFT_PENALTY", "would_fail_new": False,
                   "adjusted_conviction": "MEDIUM_CONVICTION"},
    },
    {
        "name": "Test 3 — ad 0.5 STRONG",
        "kwargs": {"ad_ratio": 0.5, "ues": 70, "ml_score": 70, "conviction": "HIGH_CONVICTION"},
        "expect": {"new_policy": "STRONG_PENALTY", "adjusted_conviction": "WATCH",
                   "would_fail_new": True},
    },
    {
        "name": "Test 4 — ad 0.3 hard veto",
        "kwargs": {"ad_ratio": 0.3, "ues": 70, "ml_score": 70},
        "expect": {"new_policy": "HARD_VETO", "would_fail_new": True},
    },
    {
        "name": "Test 5 — ad 0.3 strength override",
        "kwargs": {"ad_ratio": 0.35, "ues": 80, "ml_score": 82, "conviction": "HIGH_CONVICTION"},
        "expect": {"new_policy": "OVERRIDE_TO_SOFT", "would_fail_new": False},
    },
    {
        "name": "Test 6 — ad >= 1 neutral",
        "kwargs": {"ad_ratio": 1.1, "ues": 70, "ml_score": 70},
        "expect": {"applies": False, "would_fail_old": False},
    },
    {
        "name": "Test 7 — legacy exception still pass old",
        "kwargs": {"ad_ratio": 0.95, "ues": 80, "ml_score": 75},
        "expect": {"would_fail_old": False, "new_policy": "PASS"},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_negative_breadth_policy(**t["kwargs"])
        for k, v in t["expect"].items():
            if got.get(k) != v:
                errors.append(f"{t['name']}: {k} expected {v!r} got {got.get(k)!r}")
                break
        else:
            passed += 1
    return {"success": len(errors) == 0, "passed": passed, "total": len(SPEC_TESTS), "errors": errors}


def _load_rows(conn, start, end):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("""
        SELECT * FROM gate_audit_snapshots
        WHERE signal_date>=? AND signal_date<=?
        ORDER BY signal_date, symbol
    """, (start, end)).fetchall()]


def cmd_outcome(params: dict):
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_end = "2026-06-05"

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import classify_winner_types, forward_bars, load_bars, sole_blocker

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_rows(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    eval_rows = [r for r in rows if int(r.get("outcomes_filled") or 0) == 1
                 and r["signal_date"] <= period_end]
    cohort = [r for r in eval_rows if NEG_BREADTH_GATE in _parse_qg_failures(r)]

    policy_counts = defaultdict(int)
    old_fail = new_fail = 0
    rescued_clean = []
    released_loser = []
    new_actionable_by_date = defaultdict(int)

    for r in cohort:
        pol = evaluate_negative_breadth_policy(
            ad_ratio=r.get("ad_ratio"),
            ues=safe_float(r.get("ues"), 0.0),
            ml_score=safe_float(r.get("ml_score"), 0.0),
            conviction=r.get("conviction"),
            forecast_veto=r.get("forecast_veto"),
            anti_law=bool(int(r.get("anti_law") or 0)),
        )
        policy_counts[pol["new_policy"]] += 1
        if pol["would_fail_old"]:
            old_fail += 1
        if pol["would_fail_new"]:
            new_fail += 1

        bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)
        wt = classify_winner_types(r, bars)
        old_act = int(r.get("actionable") or 0)
        new_act = simulate_would_be_actionable(r, pol)

        if new_act and not old_act:
            new_actionable_by_date[r["signal_date"]] += 1
            if wt.get("clean_winner"):
                rescued_clean.append({
                    "date": r["signal_date"], "symbol": r["symbol"],
                    "ad_ratio": r.get("ad_ratio"), "policy": pol["new_policy"],
                    "sole": sole_blocker(r, f"QG:{NEG_BREADTH_GATE}"),
                })
            if wt.get("loser_5d"):
                released_loser.append(r)

    n_rescued = len(rescued_clean)
    n_losers = len(released_loser)
    max_daily = max(new_actionable_by_date.values()) if new_actionable_by_date else 0

    acceptance = {
        "new_actionable_lte_5": sum(new_actionable_by_date.values()) <= 5,
        "clean_rescued_gte_losers": n_rescued >= n_losers,
        "hard_veto_reduced": new_fail < old_fail,
        "max_daily_in_range": max_daily <= 5,
    }
    ready = all(acceptance.values()) and sum(new_actionable_by_date.values()) <= 5

    report = {
        "success": True,
        "phase": "2.9_outcome",
        "no_production_patch": True,
        "cohort": f"A_FULL_5D neg_breadth ({start}→{period_end})",
        "n_neg_breadth_cohort": len(cohort),
        "n_old_would_fail": old_fail,
        "n_new_would_fail": new_fail,
        "policy_counts": dict(policy_counts),
        "new_actionable_total": sum(new_actionable_by_date.values()),
        "new_actionable_by_date": dict(sorted(new_actionable_by_date.items())),
        "clean_winners_rescued": n_rescued,
        "losers_released": n_losers,
        "rescued_samples": rescued_clean[:10],
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": ready,
        "recommendation": "PROCEED_2.9D" if ready else "REVIEW_BEFORE_2.9D",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"negative_breadth_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"negative_breadth_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "NEGATIVE_BREADTH Policy Shadow — Phase 2.9",
        f"Cohort: {len(cohort)} evaluable | old fail: {old_fail} → new fail: {new_fail}",
        "",
        "=== Tier Distribution ===",
    ]
    for k, v in sorted(policy_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {k:<20} {v}")
    lines += [
        "",
        "=== Outcome Impact ===",
        f"  New actionable: {sum(new_actionable_by_date.values())} | by date: {dict(new_actionable_by_date)}",
        f"  Clean rescued: {n_rescued} | Losers released: {n_losers}",
        "",
        "=== Acceptance ===",
    ]
    for k, v in acceptance.items():
        lines.append(f"  {'✅' if v else '❌'} {k}: {v}")
    lines += [
        "",
        f"Recommendation: {report['recommendation']}",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {"test": cmd_test, "outcome": cmd_outcome}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(COMMANDS.keys())}))
        sys.exit(1)
    result = handler(params) if cmd != "test" else handler()
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and not result.get("success", True):
        sys.exit(1)
