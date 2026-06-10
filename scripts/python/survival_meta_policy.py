#!/usr/bin/env python3
"""
survival_meta_policy — Phase 2.11 Spec (shadow only, NOT production)

Audit ML-advanced quality gates:
  survival_sl_dominant — survival exit profile SL-first dominance
  meta_label_low       — meta-labeler probability floor

Production status: KEEP_SHADOW_REPORTING until ret_5d for C_PENDING cohort (08–10 Jun).

Usage:
    python3 scripts/python/survival_meta_policy.py test
    python3 scripts/python/survival_meta_policy.py struct
    python3 scripts/python/survival_meta_policy.py outcome
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

SURVIVAL_META_POLICY = "KEEP_SHADOW_REPORTING"

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


def parse_json_list(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except Exception:
        return [str(raw)]


def strength_bypass(ues: float, ml_score: float, risk_bucket: str | None,
                    risk_valid: bool, tier: str = "soft") -> bool:
    if risk_bucket and risk_bucket not in VALID_RISK_BUCKETS:
        return False
    if risk_bucket and not risk_valid:
        return False
    if tier == "strong":
        return ues >= 85.0 and ml_score >= 85.0
    if tier == "monitor":
        return ues >= 82.0 and ml_score >= 82.0
    return ues >= 80.0 and ml_score >= 80.0


def evaluate_survival_policy(
    *,
    survival_p_tp: float | None = None,
    survival_p_sl: float | None = None,
    ues: float = 0.0,
    ml_score: float = 0.0,
    meta_prob: float | None = None,
    risk_bucket: str | None = None,
    risk_valid_for_rr: bool = False,
    conviction: str | None = None,
    currently_blocks: bool = False,
) -> dict:
    """Shadow tiered response for survival_sl_dominant."""
    p_tp = safe_float(survival_p_tp)
    p_sl = safe_float(survival_p_sl)
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)
    meta_prob = safe_float(meta_prob)

    result = {
        "gate": "survival_sl_dominant",
        "applies": False,
        "old_policy": "PASS",
        "new_policy": "PASS",
        "new_reason": None,
        "would_block_old": bool(currently_blocks),
        "would_block_new": bool(currently_blocks),
        "margin": None,
        "adjusted_conviction": conviction,
        "position_multiplier": 1.0,
    }

    if p_tp is None or p_sl is None:
        return result

    margin = p_sl - p_tp
    result["margin"] = round(margin, 4)
    legacy = p_sl >= 0.55 and margin > 0.20
    if not legacy and not currently_blocks:
        return result

    result["applies"] = True
    result["old_policy"] = "HARD_VETO" if legacy else "PASS"

    if p_sl >= 0.70 and margin > 0.30:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "SURVIVAL_EXTREME_SL",
            "would_block_new": True,
        })
        return result

    if strength_bypass(ues, ml_score, risk_bucket, risk_valid_for_rr, "strong"):
        if meta_prob is not None and meta_prob >= 0.45:
            result.update({
                "new_policy": "PASS_OVERRIDE",
                "new_reason": "SURVIVAL_STRONG_ENSEMBLE",
                "would_block_new": False,
            })
            return result

    if legacy and strength_bypass(ues, ml_score, risk_bucket, risk_valid_for_rr, "soft"):
        adj = CONVICTION_DOWNGRADE.get(conviction or "REJECT", "REJECT")
        result.update({
            "new_policy": "SOFT_PENALTY",
            "new_reason": "SURVIVAL_SOFT_STRONG",
            "would_block_new": False,
            "adjusted_conviction": adj,
            "position_multiplier": 0.75,
        })
        return result

    if legacy and 0.15 < margin <= 0.20 and strength_bypass(
            ues, ml_score, risk_bucket, risk_valid_for_rr, "monitor"):
        result.update({
            "new_policy": "MONITOR",
            "new_reason": "SURVIVAL_BORDERLINE",
            "would_block_new": False,
            "position_multiplier": 0.85,
        })
        return result

    if legacy:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "SURVIVAL_SL_DOMINANT",
            "would_block_new": True,
        })
    return result


def evaluate_meta_policy(
    *,
    meta_prob: float | None = None,
    ml_score: float = 0.0,
    ues: float = 0.0,
    quant_matches: int = 0,
    quant_score: float = 0.0,
    risk_bucket: str | None = None,
    risk_valid_for_rr: bool = False,
    conviction: str | None = None,
    currently_blocks: bool = False,
) -> dict:
    """Shadow tiered response for meta_label_low."""
    meta_prob = safe_float(meta_prob)
    ml_score = safe_float(ml_score, 0.0)
    ues = safe_float(ues, 0.0)
    quant_score = safe_float(quant_score, 0.0)

    result = {
        "gate": "meta_label_low",
        "applies": False,
        "old_policy": "PASS",
        "new_policy": "PASS",
        "new_reason": None,
        "would_block_old": bool(currently_blocks),
        "would_block_new": bool(currently_blocks),
        "adjusted_conviction": conviction,
        "position_multiplier": 1.0,
    }

    if meta_prob is None:
        return result

    legacy = meta_prob < 0.30 and ml_score < 80.0
    if not legacy and not currently_blocks:
        return result

    result["applies"] = True
    result["old_policy"] = "HARD_VETO" if legacy else "PASS"

    if meta_prob < 0.15:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "META_EXTREME_LOW",
            "would_block_new": True,
        })
        return result

    if (quant_score >= 85.0 and quant_matches >= 10 and ml_score >= 45.0):
        result.update({
            "new_policy": "PASS_OVERRIDE",
            "new_reason": "META_QUANT_OVERRIDE",
            "would_block_new": False,
        })
        return result

    # Gate only fires when ml<80 — soften near-threshold setups with strong UES
    if legacy and ues >= 78.0 and ml_score >= 75.0:
        if risk_bucket and risk_bucket not in VALID_RISK_BUCKETS:
            pass
        elif risk_bucket and not risk_valid_for_rr:
            pass
        else:
            adj = CONVICTION_DOWNGRADE.get(conviction or "REJECT", "REJECT")
            result.update({
                "new_policy": "SOFT_PENALTY",
                "new_reason": "META_SOFT_STRONG",
                "would_block_new": False,
                "adjusted_conviction": adj,
                "position_multiplier": 0.75,
            })
            return result

    if legacy and meta_prob >= 0.22 and ues >= 82.0 and ml_score >= 78.0:
        result.update({
            "new_policy": "MONITOR",
            "new_reason": "META_BORDERLINE",
            "would_block_new": False,
            "position_multiplier": 0.85,
        })
        return result

    if legacy:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "META_LABEL_LOW",
            "would_block_new": True,
        })
    return result


def _gate_blocks(row: dict, gate: str) -> bool:
    qf = parse_json_list(row.get("quality_gate_failures"))
    return gate in qf


def _sole_gate(row: dict, gate: str) -> bool:
    qf = parse_json_list(row.get("quality_gate_failures"))
    return gate in qf and len(qf) == 1


def _co_blockers(row: dict, gate: str) -> list[str]:
    return [x for x in parse_json_list(row.get("quality_gate_failures")) if x != gate]


def passes_except_gate(row: dict, gate: str) -> bool:
    if int(row.get("anti_law") or 0):
        return False
    qf = [x for x in parse_json_list(row.get("quality_gate_failures")) if x != gate]
    if qf:
        return False
    if not int(row.get("final_edge_passed") or 0):
        return False
    if row.get("forecast_veto"):
        return False
    conv = row.get("conviction") or ""
    return conv in ACTIONABLE_CONVICTION


def simulate_actionable(row: dict, pol: dict) -> bool:
    if not pol.get("applies"):
        return bool(int(row.get("actionable") or 0))
    if pol.get("would_block_new"):
        return False
    gate = pol.get("gate", "")
    if not passes_except_gate(row, gate):
        return False
    conv = pol.get("adjusted_conviction") or row.get("conviction")
    return conv in ACTIONABLE_CONVICTION


# ─── Spec tests ───────────────────────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Survival extreme SL stays hard",
        "fn": evaluate_survival_policy,
        "kwargs": {
            "survival_p_tp": 0.10, "survival_p_sl": 0.75,
            "ues": 90, "ml_score": 90, "currently_blocks": True,
        },
        "expect": {"new_policy": "HARD_VETO", "would_block_new": True},
    },
    {
        "name": "Survival soft with strength",
        "fn": evaluate_survival_policy,
        "kwargs": {
            "survival_p_tp": 0.20, "survival_p_sl": 0.60,
            "ues": 82, "ml_score": 85,
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
            "conviction": "HIGH_CONVICTION", "currently_blocks": True,
        },
        "expect": {"new_policy": "SOFT_PENALTY", "would_block_new": False},
    },
    {
        "name": "Survival ensemble override",
        "fn": evaluate_survival_policy,
        "kwargs": {
            "survival_p_tp": 0.20, "survival_p_sl": 0.65,
            "ues": 86, "ml_score": 88, "meta_prob": 0.50,
            "risk_bucket": "VALID_DEFAULT_MARKET_ENTRY_MODEL", "risk_valid_for_rr": True,
            "currently_blocks": True,
        },
        "expect": {"new_policy": "PASS_OVERRIDE", "would_block_new": False},
    },
    {
        "name": "Meta extreme low hard",
        "fn": evaluate_meta_policy,
        "kwargs": {"meta_prob": 0.10, "ml_score": 70, "currently_blocks": True},
        "expect": {"new_policy": "HARD_VETO", "would_block_new": True},
    },
    {
        "name": "Meta soft with strength",
        "fn": evaluate_meta_policy,
        "kwargs": {
            "meta_prob": 0.25, "ml_score": 78, "ues": 82,
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL", "risk_valid_for_rr": True,
            "conviction": "HIGH_CONVICTION", "currently_blocks": True,
        },
        "expect": {"new_policy": "SOFT_PENALTY", "would_block_new": False},
    },
    {
        "name": "Meta quant override",
        "fn": evaluate_meta_policy,
        "kwargs": {
            "meta_prob": 0.20, "ml_score": 50, "quant_matches": 12, "quant_score": 90,
            "currently_blocks": True,
        },
        "expect": {"new_policy": "PASS_OVERRIDE", "would_block_new": False},
    },
    {
        "name": "Meta pass when ml>=80",
        "fn": evaluate_meta_policy,
        "kwargs": {"meta_prob": 0.25, "ml_score": 85, "currently_blocks": False},
        "expect": {"applies": False, "would_block_new": False},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = t["fn"](**t["kwargs"])
        for k, v in t["expect"].items():
            if got.get(k) != v:
                errors.append(f"{t['name']}: {k} expected {v!r} got {got.get(k)!r}")
                break
        else:
            passed += 1
    return {"success": len(errors) == 0, "passed": passed, "total": len(SPEC_TESTS), "errors": errors}


def _load_rows(conn, start: str, end: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT g.*, fs.source_breakdown
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
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
        d["_quant_matches"] = (sb or {}).get("quant_discovery_matches")
        d["_quant_score"] = (sb or {}).get("quant_discovery_score")
        out.append(d)
    return out


def _eval_row(row: dict) -> dict:
    surv_blocks = _gate_blocks(row, "survival_sl_dominant")
    meta_blocks = _gate_blocks(row, "meta_label_low")
    surv = evaluate_survival_policy(
        survival_p_tp=row.get("survival_p_tp"),
        survival_p_sl=row.get("survival_p_sl"),
        ues=row.get("ues"),
        ml_score=row.get("ml_score"),
        meta_prob=row.get("meta_prob"),
        risk_bucket=row.get("shadow_risk_bucket"),
        risk_valid_for_rr=bool(int(row.get("shadow_risk_valid_for_rr") or 0)),
        conviction=row.get("conviction"),
        currently_blocks=surv_blocks,
    )
    meta = evaluate_meta_policy(
        meta_prob=row.get("meta_prob"),
        ml_score=row.get("ml_score"),
        ues=row.get("ues"),
        quant_matches=int(row.get("_quant_matches") or row.get("quant_matches") or 0),
        quant_score=safe_float(row.get("_quant_score"), 0.0),
        risk_bucket=row.get("shadow_risk_bucket"),
        risk_valid_for_rr=bool(int(row.get("shadow_risk_valid_for_rr") or 0)),
        conviction=row.get("conviction"),
        currently_blocks=meta_blocks,
    )
    return {"survival": surv, "meta": meta}


def cmd_struct(params: dict | None = None):
    params = params or {}
    start = params.get("start_date", "2026-06-08")
    end = params.get("end_date", "2026-06-10")

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_rows(conn, start, end)
    conn.close()

    gates = ("survival_sl_dominant", "meta_label_low")
    summary = {}
    for gate in gates:
        cohort = [r for r in rows if _gate_blocks(r, gate)]
        co = Counter()
        for r in cohort:
            co[tuple(_co_blockers(r, gate))] += 1
        exclusive = sum(1 for r in cohort if _sole_gate(r, gate))
        summary[gate] = {
            "blocked": len(cohort),
            "exclusive": exclusive,
            "co_blocked": len(cohort) - exclusive,
            "top_co_blockers": [
                {"others": list(k), "n": v}
                for k, v in co.most_common(8)
            ],
            "avg_ues": round(mean([safe_float(r["ues"], 0) for r in cohort]), 2) if cohort else None,
            "avg_ml": round(mean([safe_float(r["ml_score"], 0) for r in cohort]), 2) if cohort else None,
        }

    report = {
        "success": True,
        "phase": "2.11_structural",
        "policy": SURVIVAL_META_POLICY,
        "cohort": f"C_PENDING ({start}→{end})",
        "n_snapshots": len(rows),
        "note": "Structural only — ret_5d not yet available for this cohort",
        "gates": summary,
        "recommendation": "WAIT_FOR_RET_5D",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"survival_meta_struct_{tag}.json"
    txt_path = REPORT_DIR / f"survival_meta_struct_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Survival / Meta — Phase 2.11 Structural Audit",
        f"Cohort: {start} → {end} | snapshots: {len(rows)}",
        f"Policy: {SURVIVAL_META_POLICY}",
        "",
    ]
    for gate, s in summary.items():
        lines += [
            f"=== {gate} ===",
            f"  blocked: {s['blocked']} | exclusive: {s['exclusive']} | co-blocked: {s['co_blocked']}",
            f"  avg UES: {s['avg_ues']} | avg ML: {s['avg_ml']}",
            "  top co-blockers:",
        ]
        for cb in s["top_co_blockers"][:6]:
            lines.append(f"    {cb['n']:>3}  {', '.join(cb['others']) or '(none)'}")
        lines.append("")

    lines += ["Recommendation: WAIT_FOR_RET_5D — no production patch."]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


def cmd_outcome(params: dict | None = None):
    params = params or {}
    start = params.get("start_date", "2026-06-08")
    end = params.get("end_date", "2026-06-10")

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import classify_winner_types, forward_bars, load_bars, sole_blocker

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_rows(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    eval_rows = [r for r in rows if int(r.get("outcomes_filled") or 0) == 1]
    use_ret1_proxy = len(eval_rows) == 0
    proxy_note = None

    def outcome_metric(r, bars):
        if use_ret1_proxy and r.get("ret_1d") is not None:
            return {"winner": r["ret_1d"] > 0, "ret": r["ret_1d"], "proxy": "ret_1d"}
        wt = classify_winner_types(r, bars)
        return {
            "clean_winner": wt.get("clean_winner"),
            "loser_5d": wt.get("loser_5d") or int(r.get("loser_5d") or 0),
            "ret": r.get("ret_5d"),
            "proxy": "ret_5d",
        }

    gate_results = {}
    for gate_name, pol_key in (
        ("survival_sl_dominant", "survival"),
        ("meta_label_low", "meta"),
    ):
        cohort = [r for r in rows if _gate_blocks(r, gate_name)]
        policy_counts = Counter()
        old_block = new_block = 0
        new_actionable = 0
        rescued = released = 0
        ret_vals = []

        for r in cohort:
            pol = _eval_row(r)[pol_key]
            policy_counts[pol["new_policy"]] += 1
            if pol.get("would_block_old"):
                old_block += 1
            if pol.get("would_block_new"):
                new_block += 1

            bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)
            om = outcome_metric(r, bars)
            if om.get("ret") is not None:
                ret_vals.append(om["ret"])

            old_act = int(r.get("actionable") or 0)
            new_act = simulate_actionable(r, pol)
            if new_act and not old_act:
                new_actionable += 1
                if use_ret1_proxy:
                    if om.get("winner"):
                        rescued += 1
                    elif om.get("ret") is not None and om["ret"] < 0:
                        released += 1
                else:
                    if om.get("clean_winner"):
                        rescued += 1
                    if om.get("loser_5d"):
                        released += 1

        gate_results[gate_name] = {
            "n_cohort": len(cohort),
            "exclusive": sum(1 for r in cohort if _sole_gate(r, gate_name)),
            "old_blocks": old_block,
            "new_blocks": new_block,
            "policy_counts": dict(policy_counts),
            "new_actionable": new_actionable,
            "rescued_proxy": rescued,
            "released_proxy": released,
            "avg_ret_proxy": round(mean(ret_vals), 3) if ret_vals else None,
        }

    if use_ret1_proxy:
        proxy_note = "PRELIMINARY ret_1d proxy — ret_5d pending for C_PENDING cohort"

    acceptance = {
        "ret_5d_available": not use_ret1_proxy,
        "new_actionable_lte_5": sum(g["new_actionable"] for g in gate_results.values()) <= 5,
        "survival_blocks_reduced": (
            gate_results["survival_sl_dominant"]["new_blocks"]
            < gate_results["survival_sl_dominant"]["old_blocks"]
        ),
        "meta_blocks_reduced": (
            gate_results["meta_label_low"]["new_blocks"]
            < gate_results["meta_label_low"]["old_blocks"]
        ),
    }
    ready = acceptance["ret_5d_available"] and all([
        acceptance["new_actionable_lte_5"],
        acceptance["survival_blocks_reduced"] or acceptance["meta_blocks_reduced"],
    ])

    report = {
        "success": True,
        "phase": "2.11_outcome",
        "policy": SURVIVAL_META_POLICY,
        "cohort": f"{start}→{end}",
        "outcome_mode": "ret_5d" if not use_ret1_proxy else "ret_1d_proxy",
        "proxy_note": proxy_note,
        "gates": gate_results,
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": ready,
        "recommendation": (
            "PROCEED_2.11D" if ready else
            "WAIT_RET_5D" if use_ret1_proxy else
            "REVIEW_BEFORE_2.11D"
        ),
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"survival_meta_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"survival_meta_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Survival / Meta Policy Shadow — Phase 2.11",
        f"Cohort: {start} → {end} | mode: {report['outcome_mode']}",
        f"Policy: {SURVIVAL_META_POLICY}",
    ]
    if proxy_note:
        lines.append(f"NOTE: {proxy_note}")
    lines.append("")
    for gate, g in gate_results.items():
        lines += [
            f"=== {gate} ===",
            f"  blocked: {g['n_cohort']} | exclusive: {g['exclusive']}",
            f"  old blocks: {g['old_blocks']} → new blocks: {g['new_blocks']}",
            f"  new actionable: {g['new_actionable']} | rescued: {g['rescued_proxy']} | released: {g['released_proxy']}",
            f"  avg ret proxy: {g['avg_ret_proxy']}",
            "  policies:",
        ]
        for k, v in sorted(g["policy_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"    {k:<18} {v}")
        lines.append("")

    lines += ["=== Acceptance ==="]
    for k, v in acceptance.items():
        lines.append(f"  {'✅' if v else '❌'} {k}: {v}")
    lines += [
        "",
        f"Recommendation: {report['recommendation']}",
        f"SURVIVAL_META_POLICY={SURVIVAL_META_POLICY}",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {
    "test": cmd_test,
    "struct": cmd_struct,
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
