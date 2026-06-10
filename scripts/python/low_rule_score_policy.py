#!/usr/bin/env python3
"""
evaluate_low_rule_score_policy() — Phase 2.8 (2.8D wired in _apply_final_edge_gates)

Bug: quant-path stocks get scan_score=0/missing → FINAL_EDGE:LOW_RULE_SCORE
even when UES/ML/risk are strong.

Usage:
    python3 scripts/python/low_rule_score_policy.py test
    python3 scripts/python/low_rule_score_policy.py outcome '{"start_date":"2026-06-01"}'
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

LOW_RULE_THRESHOLD = 55.0
LOW_RULE_GATE = "FINAL_EDGE:LOW_RULE_SCORE"

VALID_RISK_BUCKETS = frozenset({
    "VALID_PULLBACK_RISK_MODEL",
    "VALID_BREAKOUT_RISK_MODEL",
    "VALID_DEFAULT_MARKET_ENTRY_MODEL",
})

STRUCTURAL_BLOCK_FRAGMENTS = (
    "INVALID_STOP",
    "INVALID_MARKET_STOP",
    "STALE_TARGET",
    "ENTRY_ALREADY_GONE",
    "SL_NOT_BELOW_RECENT_STRUCTURE",
    "WEAK_BREAKOUT_DAY_VOLUME",
    "VOLUME_COLLAPSE_AFTER_BREAKOUT",
    "BREAKOUT_HIGH_VOLUME_CHASE",
    "NO_STRUCTURAL_SL",
    "STRUCTURAL_SL_IMPLAUSIBLE",
)

ACTIONABLE_CONVICTION = frozenset({
    "ULTRA_CONVICTION", "HIGH_CONVICTION", "MEDIUM_CONVICTION",
})


def safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _setup_text(setup_type: str | None) -> str:
    return (setup_type or "").strip().lower()


def has_setup(setup_type: str | None, quant_rule: str | None = None,
              quant_matches: int = 0) -> bool:
    if _setup_text(setup_type):
        return True
    if quant_rule and quant_matches >= 6 and _setup_text(quant_rule):
        return True
    return False


def classify_scan_score(scan_score) -> str:
    """missing_or_zero | weak_real | ok"""
    s = safe_float(scan_score, 0.0)
    if s < 1.0:
        return "missing_or_zero"
    if s < LOW_RULE_THRESHOLD:
        return "weak_real"
    return "ok"


def has_structural_block(
    final_edge_failure: str | None,
    hard_gate_failure: str | None,
    risk_bucket: str | None,
    risk_actionability: str | None,
    quality_failures: list | None,
    anti_law: bool = False,
) -> bool:
    if anti_law:
        return True
    fe = str(final_edge_failure or "")
    hg = str(hard_gate_failure or "")
    qf = " ".join(str(x) for x in (quality_failures or []))
    blob = f"{fe} {hg} {qf}"
    if any(tag in blob for tag in STRUCTURAL_BLOCK_FRAGMENTS):
        return True
    if risk_bucket in ("STALE_TARGET", "ENTRY_ALREADY_GONE", "INVALID_STOP", "INVALID_MARKET_STOP"):
        return True
    if risk_actionability in ("WATCH_REENTRY", "WATCH_PULLBACK"):
        return True
    if hg:
        return True
    if "high_volume_chase" in qf:
        return True
    return False


def evaluate_low_rule_score_policy(
    *,
    scan_score,
    quant_matches: int = 0,
    quant_rule: str | None = None,
    setup_type: str | None = None,
    ues: float = 0.0,
    ml_score: float = 0.0,
    risk_bucket: str | None = None,
    risk_valid_for_rr: bool = False,
    risk_actionability: str | None = None,
    final_edge_failure: str | None = None,
    hard_gate_failure: str | None = None,
    quality_gate_failures: list | None = None,
    anti_law: bool = False,
    used_fallback_risk: bool = False,
) -> dict:
    """
    Phase 2.8 — narrow quant-path exception for LOW_RULE_SCORE gate.
    Returns diagnosis dict; does not change production.
    """
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)
    quant_matches = int(quant_matches or 0)
    scan_class = classify_scan_score(scan_score)
    s = safe_float(scan_score, 0.0)
    setup_ok = has_setup(setup_type, quant_rule, quant_matches)

    result = {
        "scan_score_class": scan_class,
        "would_fail_old_low_rule": False,
        "would_fail_new_low_rule": False,
        "exception_applies": False,
        "exception_reason": None,
        "policy": "KEEP_LOW_RULE",
        "old_block_reason": None,
        "new_block_reason": None,
    }

    if not setup_ok:
        result["new_block_reason"] = "FINAL_EDGE:NO_RULE_SETUP"
        return result

    if used_fallback_risk:
        result["new_block_reason"] = "FINAL_EDGE:NO_STRUCTURAL_SL"
        result["would_fail_new_low_rule"] = True
        return result

    structural = has_structural_block(
        final_edge_failure, hard_gate_failure, risk_bucket,
        risk_actionability, quality_gate_failures, anti_law,
    )
    if structural and final_edge_failure and final_edge_failure != LOW_RULE_GATE:
        result["new_block_reason"] = final_edge_failure
        result["would_fail_new_low_rule"] = True
        return result

    old_fail = s < LOW_RULE_THRESHOLD
    result["would_fail_old_low_rule"] = old_fail
    if not old_fail:
        result["policy"] = "PASS_SCAN_SCORE"
        return result

    result["old_block_reason"] = LOW_RULE_GATE

    # weak real scan — never exempt
    if scan_class == "weak_real":
        result.update({
            "would_fail_new_low_rule": True,
            "new_block_reason": LOW_RULE_GATE,
            "policy": "REJECT_WEAK_REAL_SCAN",
        })
        return result

    # missing/zero — quant-path exception (narrow)
    exempt = (
        scan_class == "missing_or_zero"
        and quant_matches >= 6
        and ues >= 80.0
        and ml_score >= 80.0
        and risk_bucket in VALID_RISK_BUCKETS
        and risk_valid_for_rr
        and not structural
        and not anti_law
        and not used_fallback_risk
    )

    if exempt:
        result.update({
            "exception_applies": True,
            "exception_reason": "LOW_RULE_QUANT_PATH_EXCEPTION",
            "policy": "EXEMPT_QUANT_PATH",
            "would_fail_new_low_rule": False,
            "new_block_reason": None,
        })
        return result

    result.update({
        "would_fail_new_low_rule": True,
        "new_block_reason": LOW_RULE_GATE,
        "policy": "REJECT_MISSING_SCAN",
    })
    return result


def build_low_rule_shadow_fields(
    scan_score, quant_matches, quant_rule, setup_type, ues, ml_score,
    risk_bucket=None, risk_valid_for_rr=0, risk_actionability=None,
    final_edge_failure=None, hard_gate_failure=None,
    quality_gate_failures=None, anti_law=0, used_fallback_risk=False,
) -> dict:
    pol = evaluate_low_rule_score_policy(
        scan_score=scan_score,
        quant_matches=quant_matches,
        quant_rule=quant_rule,
        setup_type=setup_type,
        ues=safe_float(ues, 0.0),
        ml_score=safe_float(ml_score, 0.0),
        risk_bucket=risk_bucket,
        risk_valid_for_rr=bool(risk_valid_for_rr),
        risk_actionability=risk_actionability,
        final_edge_failure=final_edge_failure,
        hard_gate_failure=hard_gate_failure,
        quality_gate_failures=quality_gate_failures or [],
        anti_law=bool(anti_law),
        used_fallback_risk=bool(used_fallback_risk),
    )
    return {
        "shadow_low_rule_scan_class": pol.get("scan_score_class"),
        "shadow_low_rule_policy": pol.get("policy"),
        "shadow_low_rule_exception": 1 if pol.get("exception_applies") else 0,
        "shadow_low_rule_exception_reason": pol.get("exception_reason"),
        "shadow_low_rule_would_fail_old": 1 if pol.get("would_fail_old_low_rule") else 0,
        "shadow_low_rule_would_fail_new": 1 if pol.get("would_fail_new_low_rule") else 0,
    }


def passes_non_forecast_actionable_checks(row: dict) -> bool:
    if int(row.get("anti_law") or 0):
        return False
    if not int(row.get("quality_gate_passed") or 0):
        return False
    bucket = row.get("shadow_risk_bucket")
    if bucket and not int(row.get("shadow_risk_valid_for_rr") or 0):
        return False
    entry = row.get("entry_price") or row.get("risk_entry")
    stop = row.get("stop_loss") or row.get("risk_stop")
    target = row.get("t1_target") or row.get("risk_target")
    if not (entry and stop and target and stop < entry < target):
        return False
    rr = safe_float(row.get("shadow_computed_rr") or row.get("r_ratio"))
    if rr is not None and rr < 1.3:
        return False
    if (row.get("conviction") or "") not in ACTIONABLE_CONVICTION:
        return False
    if row.get("forecast_veto"):
        return False
    return True


def simulate_would_be_actionable_with_exception(row: dict, pol: dict) -> bool:
    """Actionable if LOW_RULE exception fixes the only final-edge LOW_RULE block."""
    if not pol.get("exception_applies"):
        return False
    if not passes_non_forecast_actionable_checks(row):
        return False
    fe = row.get("final_edge_failure") or ""
    if fe == LOW_RULE_GATE:
        return True
    if LOW_RULE_GATE in _parse_gates(row) and pol.get("exception_applies"):
        # co-blocked: only if no other final edge failure remains
        other_fe = fe if fe and fe != LOW_RULE_GATE else None
        if other_fe:
            return False
        return True
    return False


def _parse_gates(row) -> list[str]:
    ag = row.get("all_blocking_gates")
    if isinstance(ag, str):
        try:
            return json.loads(ag)
        except Exception:
            return []
    return ag or []


# ─── Spec tests ───────────────────────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Test 1 — quant path exempt",
        "kwargs": {
            "scan_score": 0, "quant_matches": 12, "quant_rule": "volume_accumulation",
            "setup_type": None, "ues": 86, "ml_score": 88,
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
            "final_edge_failure": LOW_RULE_GATE,
        },
        "expect": {"exception_applies": True, "policy": "EXEMPT_QUANT_PATH"},
    },
    {
        "name": "Test 2 — weak real scan not exempt",
        "kwargs": {
            "scan_score": 43, "quant_matches": 15, "ues": 90, "ml_score": 90,
            "setup_type": "Power Breakout ⚡",
            "risk_bucket": "VALID_BREAKOUT_RISK_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"exception_applies": False, "policy": "REJECT_WEAK_REAL_SCAN",
                   "scan_score_class": "weak_real"},
    },
    {
        "name": "Test 3 — missing scan low quant",
        "kwargs": {
            "scan_score": 0, "quant_matches": 3, "ues": 90, "ml_score": 90,
            "setup_type": "Trend Continuation 📈",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"exception_applies": False, "policy": "REJECT_MISSING_SCAN"},
    },
    {
        "name": "Test 4 — STALE blocks exception",
        "kwargs": {
            "scan_score": 0, "quant_matches": 10, "ues": 85, "ml_score": 85,
            "setup_type": "Trend Continuation 📈",
            "risk_bucket": "STALE_TARGET", "risk_valid_for_rr": False,
            "risk_actionability": "WATCH_REENTRY",
            "final_edge_failure": "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY",
        },
        "expect": {"exception_applies": False, "would_fail_new_low_rule": True},
    },
    {
        "name": "Test 5 — UES too low",
        "kwargs": {
            "scan_score": 0, "quant_matches": 10, "ues": 72, "ml_score": 90,
            "setup_type": "Trend Continuation 📈",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"exception_applies": False},
    },
    {
        "name": "Test 6 — ok scan passes",
        "kwargs": {
            "scan_score": 72, "quant_matches": 0, "ues": 70, "ml_score": 70,
            "setup_type": "Institutional Retest 🏆",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"would_fail_old_low_rule": False, "policy": "PASS_SCAN_SCORE"},
    },
    {
        "name": "Test 7 — SL structure blocks",
        "kwargs": {
            "scan_score": 0, "quant_matches": 12, "ues": 88, "ml_score": 88,
            "setup_type": "Volume Accumulation 📦",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
            "final_edge_failure": "FINAL_EDGE:SL_NOT_BELOW_RECENT_STRUCTURE",
        },
        "expect": {"exception_applies": False, "would_fail_new_low_rule": True},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_low_rule_score_policy(**t["kwargs"])
        for k, v in t["expect"].items():
            if got.get(k) != v:
                errors.append(f"{t['name']}: {k} expected {v!r} got {got.get(k)!r}")
                break
        else:
            passed += 1
    return {"success": len(errors) == 0, "passed": passed, "total": len(SPEC_TESTS), "errors": errors}


def _load_enriched(conn, start, end):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT g.*, fs.setup_type, fs.r_ratio, fs.source_breakdown,
               us.pine_rs_percentile
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
        LEFT JOIN unified_signals us
          ON us.signal_date=g.signal_date AND us.symbol=g.symbol
        WHERE g.signal_date>=? AND g.signal_date<=?
        ORDER BY g.signal_date, g.symbol
    """, (start, end)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        sb = d.get("source_breakdown")
        if isinstance(sb, str):
            try:
                sb = json.loads(sb)
            except Exception:
                sb = {}
        d["_quant_rule"] = (sb or {}).get("quant_discovery_rule")
        d["_quant_matches_sb"] = (sb or {}).get("quant_discovery_matches")
        out.append(d)
    return out


def _eval_row(row) -> dict:
    qf = row.get("quality_gate_failures")
    if isinstance(qf, str):
        try:
            qf = json.loads(qf)
        except Exception:
            qf = []
    qm = int(row.get("quant_matches") or row.get("_quant_matches_sb") or 0)
    pol = evaluate_low_rule_score_policy(
        scan_score=row.get("scan_score"),
        quant_matches=qm,
        quant_rule=row.get("_quant_rule"),
        setup_type=row.get("setup_type"),
        ues=safe_float(row.get("ues"), 0.0),
        ml_score=safe_float(row.get("ml_score"), 0.0),
        risk_bucket=row.get("shadow_risk_bucket"),
        risk_valid_for_rr=bool(int(row.get("shadow_risk_valid_for_rr") or 0)),
        risk_actionability=row.get("shadow_risk_actionability"),
        final_edge_failure=row.get("final_edge_failure"),
        hard_gate_failure=row.get("hard_gate_failure"),
        quality_gate_failures=qf,
        anti_law=bool(int(row.get("anti_law") or 0)),
        used_fallback_risk=row.get("risk_level_source") in ("atr_fallback",),
    )
    pol["would_be_actionable_new"] = simulate_would_be_actionable_with_exception(row, pol)
    pol["would_be_actionable_old"] = bool(int(row.get("actionable") or 0))
    return pol


def cmd_outcome(params: dict):
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_end = "2026-06-05"

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import classify_winner_types, forward_bars, load_bars, sole_blocker

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_enriched(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    eval_rows = [r for r in rows if int(r.get("outcomes_filled") or 0) == 1
                 and r["signal_date"] <= period_end]
    low_rule_cohort = [r for r in eval_rows if (r.get("final_edge_failure") == LOW_RULE_GATE
                        or LOW_RULE_GATE in _parse_gates(r))]

    policy_counts = defaultdict(int)
    scan_class_counts = defaultdict(int)
    rescued_clean = []
    released_loser = []
    new_actionable_by_date = defaultdict(int)
    exempt_rows = []

    for r in low_rule_cohort:
        pol = _eval_row(r)
        policy_counts[pol["policy"]] += 1
        scan_class_counts[pol["scan_score_class"]] += 1
        if pol.get("exception_applies"):
            exempt_rows.append(r)

        bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)
        wt = classify_winner_types(r, bars)
        old_act = int(r.get("actionable") or 0)
        new_act = pol["would_be_actionable_new"]

        if new_act and not old_act:
            new_actionable_by_date[r["signal_date"]] += 1
            if wt.get("clean_winner"):
                rescued_clean.append({
                    "date": r["signal_date"], "symbol": r["symbol"],
                    "ues": r.get("ues"), "ml_score": r.get("ml_score"),
                    "quant_matches": r.get("quant_matches"),
                    "sole_low_rule": sole_blocker(r, LOW_RULE_GATE),
                })
            if wt.get("loser_5d"):
                released_loser.append(r)

    bug_cohort = [r for r in low_rule_cohort
                  if classify_scan_score(r.get("scan_score")) == "missing_or_zero"
                  and int(r.get("quant_matches") or 0) >= 6]
    exempt_in_bug = sum(1 for r in bug_cohort if _eval_row(r).get("exception_applies"))

    n_rescued = len(rescued_clean)
    n_losers = len(released_loser)
    max_daily = max(new_actionable_by_date.values()) if new_actionable_by_date else 0

    acceptance = {
        "new_actionable_0_to_2": sum(new_actionable_by_date.values()) <= 2,
        "clean_rescued_gte_losers": n_rescued >= n_losers,
        "no_stale_exempt": all(
            r.get("shadow_risk_bucket") != "STALE_TARGET" for r in exempt_rows
        ),
        "weak_real_never_exempt": all(
            not _eval_row(r).get("exception_applies")
            for r in low_rule_cohort
            if classify_scan_score(r.get("scan_score")) == "weak_real"
        ),
        "max_daily_in_range": max_daily <= 2,
    }
    ready = all(acceptance.values())

    report = {
        "success": True,
        "phase": "2.8_outcome",
        "no_production_patch": True,
        "forecast_status": "FORECAST_DOWN_POLICY=KEEP_SHADOW_REPORTING",
        "cohort": f"A_FULL_5D LOW_RULE ({start}→{period_end})",
        "n_low_rule_cohort": len(low_rule_cohort),
        "n_bug_cohort_missing_scan_quant6": len(bug_cohort),
        "n_exempt_in_bug_cohort": exempt_in_bug,
        "policy_counts": dict(policy_counts),
        "scan_class_counts": dict(scan_class_counts),
        "new_actionable_total": sum(new_actionable_by_date.values()),
        "new_actionable_by_date": dict(sorted(new_actionable_by_date.items())),
        "clean_winners_rescued": n_rescued,
        "losers_released": n_losers,
        "rescued_samples": rescued_clean[:10],
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": ready,
        "recommendation": "PROCEED_2.8D" if ready else "REVIEW_BEFORE_2.8D",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"low_rule_score_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"low_rule_score_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "LOW_RULE_SCORE Policy Shadow — Phase 2.8",
        f"Period: {start} → {end} | LOW_RULE cohort evaluable: {len(low_rule_cohort)}",
        f"Bug cohort (scan missing + quant≥6): {len(bug_cohort)}",
        f"Exempt in bug cohort: {exempt_in_bug}",
        "",
        "=== Policy Distribution ===",
    ]
    for k, v in sorted(policy_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {k:<28} {v}")
    lines += [
        "",
        "=== Scan Score Class ===",
    ]
    for k, v in sorted(scan_class_counts.items()):
        lines.append(f"  {k:<20} {v}")
    lines += [
        "",
        "=== Outcome Impact (shadow) ===",
        f"  New actionable (net):   {sum(new_actionable_by_date.values())}",
        f"  Clean winners rescued:  {n_rescued}",
        f"  Losers released:        {n_losers}",
        f"  By date: {dict(new_actionable_by_date)}",
        "",
        "=== Acceptance Criteria ===",
    ]
    for k, v in acceptance.items():
        lines.append(f"  {'✅' if v else '❌'} {k}: {v}")
    lines += [
        "",
        f"Verdict ready for 2.8D production: {ready}",
        f"Recommendation: {report['recommendation']}",
        "",
        "FORECAST: KEEP_SHADOW_REPORTING (no production patch).",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {
    "test": cmd_test,
    "outcome": cmd_outcome,
}


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
