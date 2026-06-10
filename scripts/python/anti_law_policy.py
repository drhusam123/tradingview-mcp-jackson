#!/usr/bin/env python3
"""
evaluate_anti_law_policy() — Phase 2.10 Spec (shadow only, NOT production)

Decompose ANTI_LAW into sub-rules with tiered response:
  HARD_VETO     — FALSE_BREAKOUT, VOLUME_TRAP (proven failure modes)
  SOFT_PENALTY  — BREADTH_DIVERGENCE with relative-strength override
  MONITOR       — UNKNOWN / weak signals when UES/ML strong
  PASS_OVERRIDE — existing quant/UES overrides preserved

Production status (Phase 2.10): KEEP_SHADOW_REPORTING — no decision patch.
Outcome audit: 54 blocks unchanged; 0 new actionable; bypass thresholds not met.

Usage:
    python3 scripts/python/anti_law_policy.py test
    python3 scripts/python/anti_law_policy.py outcome '{"start_date":"2026-06-01"}'
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

ANTI_LAW_POLICY = "KEEP_SHADOW_REPORTING"

HARD_SUB_RULES = frozenset({
    "FALSE_BREAKOUT",
    "VOLUME_TRAP",
    "OVEREXTENSION",
    "FAKE_BREAKOUT",
    "LIQUIDITY_TRAP",
})

SOFT_SUB_RULES = frozenset({
    "BREADTH_DIVERGENCE",
    "SECTOR_WEAKNESS",
    "RELATIVE_WEAKNESS",
})

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

VALID_RISK_BUCKETS = frozenset({
    "VALID_PULLBACK_RISK_MODEL",
    "VALID_BREAKOUT_RISK_MODEL",
    "VALID_DEFAULT_MARKET_ENTRY_MODEL",
})


def safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def parse_triggered_types(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except Exception:
        return [str(raw)] if raw else []


def primary_sub_rule(triggered_types: list[str], strongest: str | None) -> str:
    if triggered_types:
        return triggered_types[0]
    return strongest or "UNKNOWN"


def strength_bypass(ues: float, ml_score: float, risk_bucket: str | None,
                    risk_valid_for_rr: bool, tier: str = "soft") -> bool:
    if risk_bucket and risk_bucket not in VALID_RISK_BUCKETS:
        return False
    if risk_bucket and not risk_valid_for_rr:
        return False
    if tier == "monitor":
        return ues >= 80.0 and ml_score >= 80.0
    return ues >= 78.0 and ml_score >= 75.0


def legacy_overrides(quant_matches: int, quant_score: float, scan_score: float,
                     ues: float, ml_score: float) -> tuple[bool, str | None]:
    if (quant_score >= 85.0 and quant_matches >= 10
            and scan_score >= 55.0 and ml_score >= 45.0):
        return True, "ANTI_LAW_QUANT_OVERRIDE"
    if ues >= 78.0 and scan_score >= 72.0 and (ml_score >= 58.0 or quant_score >= 75.0):
        return True, "ANTI_LAW_UES_OVERRIDE"
    return False, None


def evaluate_anti_law_policy(
    *,
    is_anti: bool = False,
    is_anti_for_decision: bool = False,
    triggered_types: list[str] | str | None = None,
    strongest_anti_law: str | None = None,
    safety_level: str | None = None,
    ues: float = 0.0,
    ml_score: float = 0.0,
    quant_matches: int = 0,
    quant_score: float = 0.0,
    scan_score: float = 0.0,
    risk_bucket: str | None = None,
    risk_valid_for_rr: bool = False,
    conviction: str | None = None,
    static_veto: bool = False,
) -> dict:
    """Phase 2.10 — per sub-rule ANTI_LAW diagnosis."""
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)
    types = parse_triggered_types(triggered_types)
    primary = primary_sub_rule(types, strongest_anti_law)

    result = {
        "applies": False,
        "sub_rules": types,
        "primary_sub_rule": primary,
        "old_policy": "PASS",
        "new_policy": "PASS",
        "new_reason": None,
        "would_block_old": bool(is_anti_for_decision),
        "would_block_new": bool(is_anti_for_decision),
        "conviction_downgrade_levels": 0,
        "adjusted_conviction": conviction,
        "position_multiplier": 1.0,
        "override_applied": None,
    }

    if not is_anti and not is_anti_for_decision:
        return result

    result["applies"] = True
    result["old_policy"] = "HARD_VETO" if is_anti_for_decision else "SCORE_PENALTY_ONLY"

    ov, ov_reason = legacy_overrides(quant_matches, quant_score, scan_score, ues, ml_score)
    if ov:
        result.update({
            "new_policy": "PASS_OVERRIDE",
            "new_reason": ov_reason,
            "would_block_new": False,
            "override_applied": ov_reason,
        })
        return result

    if static_veto or (safety_level or "").upper() == "VETO" and not types:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "ANTI_LAW_STATIC_VETO",
            "would_block_new": True,
            "primary_sub_rule": primary or "STATIC",
        })
        return result

    # Classify primary sub-rule
    if primary in HARD_SUB_RULES:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": f"ANTI_LAW_HARD:{primary}",
            "would_block_new": True,
        })
        return result

    if primary in SOFT_SUB_RULES:
        if strength_bypass(ues, ml_score, risk_bucket, risk_valid_for_rr, "soft"):
            adj = CONVICTION_DOWNGRADE.get(conviction or "REJECT", "REJECT")
            result.update({
                "new_policy": "SOFT_PENALTY",
                "new_reason": f"ANTI_LAW_SOFT:{primary}",
                "would_block_new": False,
                "conviction_downgrade_levels": 1,
                "adjusted_conviction": adj,
                "position_multiplier": 0.75,
            })
            return result
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": f"ANTI_LAW_SOFT_NO_BYPASS:{primary}",
            "would_block_new": True,
        })
        return result

    # UNKNOWN / other
    if strength_bypass(ues, ml_score, risk_bucket, risk_valid_for_rr, "monitor"):
        result.update({
            "new_policy": "MONITOR",
            "new_reason": "ANTI_LAW_UNKNOWN_MONITOR",
            "would_block_new": False,
            "position_multiplier": 0.9,
        })
        return result

    result.update({
        "new_policy": "HARD_VETO",
        "new_reason": f"ANTI_LAW_UNKNOWN:{primary}",
        "would_block_new": True,
    })
    return result


def build_anti_law_shadow_fields(
    is_anti, is_anti_for_decision, triggered_types, strongest_anti_law,
    safety_level, ues, ml_score, quant_matches, quant_score, scan_score,
    risk_bucket=None, risk_valid_for_rr=0, conviction=None, static_veto=False,
) -> dict:
    pol = evaluate_anti_law_policy(
        is_anti=bool(is_anti),
        is_anti_for_decision=bool(is_anti_for_decision),
        triggered_types=triggered_types,
        strongest_anti_law=strongest_anti_law,
        safety_level=safety_level,
        ues=safe_float(ues, 0.0),
        ml_score=safe_float(ml_score, 0.0),
        quant_matches=int(quant_matches or 0),
        quant_score=safe_float(quant_score, 0.0),
        scan_score=safe_float(scan_score, 0.0),
        risk_bucket=risk_bucket,
        risk_valid_for_rr=bool(risk_valid_for_rr),
        conviction=conviction,
        static_veto=bool(static_veto),
    )
    return {
        "shadow_anti_law_primary_rule": pol.get("primary_sub_rule"),
        "shadow_anti_law_sub_rules": json.dumps(pol.get("sub_rules") or []),
        "shadow_anti_law_policy": pol.get("new_policy"),
        "shadow_anti_law_reason": pol.get("new_reason"),
        "shadow_anti_law_would_block_old": 1 if pol.get("would_block_old") else 0,
        "shadow_anti_law_would_block_new": 1 if pol.get("would_block_new") else 0,
        "shadow_anti_law_adjusted_conviction": pol.get("adjusted_conviction"),
        "shadow_anti_law_position_mult": pol.get("position_multiplier"),
    }


def passes_except_anti_law(row: dict) -> bool:
    if int(row.get("anti_law") or 0):
        return False
    if not int(row.get("quality_gate_passed") or 0):
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
    conv = row.get("conviction") or ""
    return conv in ACTIONABLE_CONVICTION


def simulate_would_be_actionable(row: dict, pol: dict) -> bool:
    if not pol.get("applies"):
        return bool(int(row.get("actionable") or 0))
    if pol.get("would_block_new"):
        return False
    if not passes_except_anti_law({**row, "anti_law": 0}):
        return False
    conv = pol.get("adjusted_conviction") or row.get("conviction")
    return conv in ACTIONABLE_CONVICTION


# ─── Spec tests ───────────────────────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Test 1 — FALSE_BREAKOUT stays hard",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": ["FALSE_BREAKOUT"],
            "ues": 85, "ml_score": 90,
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"new_policy": "HARD_VETO", "would_block_new": True},
    },
    {
        "name": "Test 2 — BREADTH_DIVERGENCE soft with strength",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": ["BREADTH_DIVERGENCE"],
            "ues": 82, "ml_score": 80,
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
            "conviction": "HIGH_CONVICTION",
        },
        "expect": {"new_policy": "SOFT_PENALTY", "would_block_new": False,
                   "adjusted_conviction": "MEDIUM_CONVICTION"},
    },
    {
        "name": "Test 3 — BREADTH weak stays hard",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": ["BREADTH_DIVERGENCE"],
            "ues": 70, "ml_score": 70,
        },
        "expect": {"new_policy": "HARD_VETO", "would_block_new": True},
    },
    {
        "name": "Test 4 — UNKNOWN monitor when strong",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": [],
            "strongest_anti_law": "UNKNOWN",
            "ues": 85, "ml_score": 85,
            "risk_bucket": "VALID_DEFAULT_MARKET_ENTRY_MODEL", "risk_valid_for_rr": True,
        },
        "expect": {"new_policy": "MONITOR", "would_block_new": False},
    },
    {
        "name": "Test 5 — quant override",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": ["FALSE_BREAKOUT"],
            "quant_matches": 12, "quant_score": 90, "scan_score": 60, "ml_score": 50,
        },
        "expect": {"new_policy": "PASS_OVERRIDE", "would_block_new": False},
    },
    {
        "name": "Test 6 — no anti pass",
        "kwargs": {"is_anti": False, "is_anti_for_decision": False},
        "expect": {"applies": False, "would_block_new": False},
    },
    {
        "name": "Test 7 — VOLUME_TRAP hard",
        "kwargs": {
            "is_anti": True, "is_anti_for_decision": True,
            "triggered_types": ["VOLUME_TRAP"],
            "ues": 90, "ml_score": 90,
        },
        "expect": {"new_policy": "HARD_VETO", "would_block_new": True},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_anti_law_policy(**t["kwargs"])
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
               al.strongest_anti_law, al.triggered_types AS anti_law_types,
               al.safety_level
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
        LEFT JOIN anti_law_daily_scan al
          ON al.symbol=g.symbol AND al.date=g.signal_date
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
        d["_breakdown"] = sb or {}
        d["_quant_matches"] = (sb or {}).get("quant_discovery_matches") or d.get("quant_matches")
        d["_quant_score"] = (sb or {}).get("quant_discovery_score")
        out.append(d)
    return out


def _eval_row(row) -> dict:
    is_anti = bool(int(row.get("anti_law") or 0))
    if row.get("shadow_anti_law_policy"):
        pol = {
            "applies": is_anti,
            "primary_sub_rule": row.get("shadow_anti_law_primary_rule"),
            "sub_rules": parse_triggered_types(row.get("shadow_anti_law_sub_rules")),
            "new_policy": row.get("shadow_anti_law_policy"),
            "new_reason": row.get("shadow_anti_law_reason"),
            "would_block_old": bool(int(row.get("shadow_anti_law_would_block_old") or 0)),
            "would_block_new": bool(int(row.get("shadow_anti_law_would_block_new") or 0)),
            "adjusted_conviction": row.get("shadow_anti_law_adjusted_conviction"),
            "position_multiplier": safe_float(row.get("shadow_anti_law_position_mult"), 1.0),
        }
    else:
        sb = row.get("_breakdown") or {}
        pol = evaluate_anti_law_policy(
            is_anti=is_anti,
            is_anti_for_decision=is_anti,
            triggered_types=row.get("anti_law_types"),
            strongest_anti_law=row.get("strongest_anti_law"),
            safety_level=row.get("safety_level"),
            ues=safe_float(row.get("ues"), 0.0),
            ml_score=safe_float(row.get("ml_score"), 0.0),
            quant_matches=int(row.get("_quant_matches") or row.get("quant_matches") or 0),
            quant_score=safe_float(row.get("_quant_score"), 0.0),
            scan_score=safe_float(row.get("scan_score"), 0.0),
            risk_bucket=row.get("shadow_risk_bucket"),
            risk_valid_for_rr=bool(int(row.get("shadow_risk_valid_for_rr") or 0)),
            conviction=row.get("conviction"),
        )
    pol["would_be_actionable_new"] = simulate_would_be_actionable(row, pol)
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
    cohort = [r for r in eval_rows if int(r.get("anti_law") or 0)]

    policy_counts = defaultdict(int)
    by_sub = defaultdict(lambda: {"n": 0, "old_block": 0, "new_block": 0, "clean": 0, "lose": 0})
    rescued_clean = []
    released_loser = []
    new_actionable_by_date = defaultdict(int)

    for r in cohort:
        pol = _eval_row(r)
        policy_counts[pol["new_policy"]] += 1
        sub = pol.get("primary_sub_rule") or "UNKNOWN"
        by_sub[sub]["n"] += 1
        if pol.get("would_block_old"):
            by_sub[sub]["old_block"] += 1
        if pol.get("would_block_new"):
            by_sub[sub]["new_block"] += 1

        bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)
        wt = classify_winner_types(r, bars)
        if wt.get("clean_winner"):
            by_sub[sub]["clean"] += 1
        if wt.get("loser_5d"):
            by_sub[sub]["lose"] += 1

        old_act = int(r.get("actionable") or 0)
        new_act = pol["would_be_actionable_new"]
        if new_act and not old_act:
            new_actionable_by_date[r["signal_date"]] += 1
            if wt.get("clean_winner"):
                rescued_clean.append({
                    "date": r["signal_date"], "symbol": r["symbol"],
                    "sub_rule": sub, "policy": pol["new_policy"],
                    "sole": sole_blocker(r, "ANTI_LAW"),
                })
            if wt.get("loser_5d"):
                released_loser.append(r)

    n_rescued = len(rescued_clean)
    n_losers = len(released_loser)
    old_block = sum(1 for r in cohort if _eval_row(r).get("would_block_old"))
    new_block = sum(1 for r in cohort if _eval_row(r).get("would_block_new"))

    hard_unchanged = True
    for hr in ("FALSE_BREAKOUT", "VOLUME_TRAP", "OVEREXTENSION", "FAKE_BREAKOUT", "LIQUIDITY_TRAP"):
        stats = by_sub.get(hr)
        if stats and stats.get("n", 0) > 0:
            if stats.get("new_block", 0) < stats.get("old_block", 0):
                hard_unchanged = False
                break

    acceptance = {
        "new_actionable_lte_3": sum(new_actionable_by_date.values()) <= 3,
        "clean_rescued_gte_losers": n_rescued >= n_losers,
        "hard_subrules_unchanged": hard_unchanged,
        "blocks_reduced": new_block < old_block,
    }
    ready = all(acceptance.values())

    sub_table = [
        {
            "sub_rule": k,
            "blocked": v["n"],
            "old_block": v["old_block"],
            "new_block": v["new_block"],
            "clean_winners": v["clean"],
            "losers": v["lose"],
            "decision": (
                "KEEP_HARD" if k in HARD_SUB_RULES
                else "SOFT_IF_STRONG" if k in SOFT_SUB_RULES
                else "MONITOR_IF_STRONG"
            ),
        }
        for k, v in sorted(by_sub.items(), key=lambda x: -x[1]["n"])
    ]

    report = {
        "success": True,
        "phase": "2.10_outcome",
        "no_production_patch": True,
        "cohort": f"A_FULL_5D anti_law ({start}→{period_end})",
        "n_anti_law_cohort": len(cohort),
        "old_blocks": old_block,
        "new_blocks": new_block,
        "policy_counts": dict(policy_counts),
        "sub_rule_table": sub_table,
        "new_actionable_total": sum(new_actionable_by_date.values()),
        "clean_winners_rescued": n_rescued,
        "losers_released": n_losers,
        "rescued_samples": rescued_clean[:10],
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": ready,
        "recommendation": "PROCEED_2.10D" if ready else "REVIEW_BEFORE_2.10D",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"anti_law_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"anti_law_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "ANTI_LAW Sub-Rule Policy Shadow — Phase 2.10",
        f"Cohort: {len(cohort)} | old blocks: {old_block} → new blocks: {new_block}",
        "",
        "=== Sub-Rule Table ===",
        f"{'Sub-Rule':<22} {'N':>4} {'OldBlk':>7} {'NewBlk':>7} {'Clean':>6} {'Lose':>5} {'Decision':<18}",
    ]
    for s in sub_table:
        lines.append(
            f"{s['sub_rule']:<22} {s['blocked']:>4} {s['old_block']:>7} {s['new_block']:>7} "
            f"{s['clean_winners']:>6} {s['losers']:>5} {s['decision']:<18}"
        )
    lines += [
        "",
        "=== Policy Distribution ===",
    ]
    for k, v in sorted(policy_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {k:<18} {v}")
    lines += [
        "",
        f"New actionable: {sum(new_actionable_by_date.values())} | Clean rescued: {n_rescued} | Losers: {n_losers}",
        "",
        "=== Acceptance ===",
    ]
    for k, v in acceptance.items():
        lines.append(f"  {'✅' if v else '❌'} {k}: {v}")
    lines += [
        "",
        f"Recommendation: {report['recommendation']}",
        f"ANTI_LAW_POLICY={ANTI_LAW_POLICY}",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    report["anti_law_policy"] = ANTI_LAW_POLICY
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
