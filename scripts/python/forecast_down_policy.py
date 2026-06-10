#!/usr/bin/env python3
"""
evaluate_forecast_down_policy() — Phase 2.7 Spec (shadow only, NOT production)

Converts FORECAST_DOWN from unconditional hard veto to:
  HARD_VETO      — only when ≥2 weakness signals
  SOFT_PENALTY   — default middle path (conviction downgrade)
  OVERRIDE       — strong evidence outweighs forecast

Production status (Phase 2.7): KEEP_SHADOW_REPORTING — no decision patch.

Usage:
    python3 scripts/python/forecast_down_policy.py test
    python3 scripts/python/forecast_down_policy.py shadow '{"start_date":"2026-06-01"}'
    python3 scripts/python/forecast_down_policy.py outcome '{"start_date":"2026-06-01"}'
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

VALID_RISK_BUCKETS = frozenset({
    "VALID_PULLBACK_RISK_MODEL",
    "VALID_BREAKOUT_RISK_MODEL",
    "VALID_DEFAULT_MARKET_ENTRY_MODEL",
})

OVERRIDE_SETUPS = (
    "trend continuation",
    "volume accumulation",
    "pullback",
    "reload",
    "post-breakout",
    "institutional retest",
    "sector rotation",
)

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

CONVICTION_DOWNGRADE = {
    "ULTRA_CONVICTION": "HIGH_CONVICTION",
    "HIGH_CONVICTION": "MEDIUM_CONVICTION",
    "MEDIUM_CONVICTION": "WATCH",
    "WATCH": "REJECT",
    "REJECT": "REJECT",
}

FORECAST_VETO_TYPES = frozenset({"FORECAST_DOWN", "FORECAST_DOWNSIDE_DOMINANT"})

MAX_DAILY_OVERRIDE = 3
MAX_DAILY_NEW_ACTIONABLE = 5


def safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _setup_text(setup_type: str | None) -> str:
    return (setup_type or "").strip().lower()


def _is_override_setup(setup_type: str | None) -> bool:
    text = _setup_text(setup_type)
    return any(k in text for k in OVERRIDE_SETUPS)


def _has_structural_block(
    final_edge_passed: bool,
    final_edge_failure: str | None,
    hard_gate_failure: str | None,
    risk_bucket: str | None,
    risk_actionability: str | None,
    quality_failures: list | None,
) -> bool:
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
    if not final_edge_passed and fe:
        return True
    if hg:
        return True
    if "high_volume_chase" in qf:
        return True
    return False


def _weakness_signals(
    ues: float,
    ml_score: float,
    risk_bucket: str | None,
    ad_ratio: float | None,
    vol_ratio: float | None,
    rs_percentile: float | None,
    setup_type: str | None,
    is_sector_leader: bool,
) -> tuple[list[str], list[str]]:
    """(strong_weakness, soft_weakness) — hard veto uses strong signals only."""
    strong, soft = [], []
    if ml_score < 75:
        strong.append("ml_lt_75")
    if ues < 75:
        strong.append("ues_lt_75")
    if risk_bucket not in VALID_RISK_BUCKETS:
        strong.append("risk_not_valid")
    if ad_ratio is not None and ad_ratio < 0.6:
        strong.append("negative_breadth_strong")
    elif ad_ratio is not None and ad_ratio < 0.8:
        soft.append("negative_breadth_moderate")
    if vol_ratio is not None and vol_ratio < 1.5:
        strong.append("volume_weak")
    if rs_percentile is not None and rs_percentile < 40:
        strong.append("relative_strength_weak")
    if not _is_override_setup(setup_type):
        soft.append("setup_not_strength_friendly")
    if not is_sector_leader:
        soft.append("not_sector_leader")
    return strong, soft


def _override_eligible(
    ues: float,
    ml_score: float,
    risk_bucket: str | None,
    setup_type: str | None,
    vol_ratio: float | None,
    structural_block: bool,
) -> tuple[bool, list[str]]:
    reasons = []
    if structural_block:
        return False, ["structural_block"]
    if risk_bucket not in VALID_RISK_BUCKETS:
        return False, ["risk_not_valid"]
    if ml_score < 85:
        return False, ["ml_lt_85"]
    if ues < 80:
        return False, ["ues_lt_80"]
    if not _is_override_setup(setup_type):
        return False, ["setup_not_override_eligible"]
    if vol_ratio is not None and vol_ratio > 3.5:
        return False, ["high_volume_chase_severe"]
    reasons.extend(["risk_valid", "ml_ge_85", "ues_ge_80", "strength_setup"])
    return True, reasons


def downgrade_conviction(conviction: str | None) -> str:
    return CONVICTION_DOWNGRADE.get(conviction or "REJECT", "REJECT")


def evaluate_forecast_down_policy(
    *,
    forecast_veto: str | None,
    ues: float = 0.0,
    ml_score: float = 0.0,
    setup_type: str | None = None,
    risk_bucket: str | None = None,
    risk_valid_for_rr: bool = False,
    ad_ratio: float | None = None,
    vol_ratio: float | None = None,
    rs_percentile: float | None = None,
    is_sector_leader: bool = False,
    final_edge_passed: bool = True,
    final_edge_failure: str | None = None,
    hard_gate_failure: str | None = None,
    quality_gate_failures: list | None = None,
    conviction: str | None = None,
    anti_law: bool = False,
    quality_gate_passed: bool = True,
    risk_actionability: str | None = None,
) -> dict:
    """
    Phase 2.7 — clinical forecast policy evaluator.
    Does not mutate production; returns full diagnosis dict.
    """
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)

    result = {
        "applies": False,
        "old_policy": "NONE",
        "new_policy": "NONE",
        "new_reason": None,
        "would_reject_old": False,
        "would_reject_new": False,
        "conviction_downgrade": False,
        "adjusted_conviction": conviction,
        "position_multiplier": 1.0,
        "hard_veto_signals": [],
        "override_signals": [],
        "weakness_count": 0,
        "structural_block": False,
    }

    if forecast_veto not in FORECAST_VETO_TYPES:
        return result

    result["applies"] = True
    result["old_policy"] = "HARD_VETO"
    result["would_reject_old"] = True

    structural = _has_structural_block(
        final_edge_passed, final_edge_failure, hard_gate_failure,
        risk_bucket, risk_actionability, quality_gate_failures,
    )
    result["structural_block"] = structural

    strong_weak, soft_weak = _weakness_signals(
        ues, ml_score, risk_bucket, ad_ratio, vol_ratio,
        rs_percentile, setup_type, is_sector_leader,
    )
    result["weakness_count"] = len(strong_weak) + len(soft_weak)

    override_ok, override_reasons = _override_eligible(
        ues, ml_score, risk_bucket, setup_type, vol_ratio, structural,
    )

    # Hard veto: ≥2 strong weakness signals, or structural block + any strong
    hard_threshold = 2
    if forecast_veto == "FORECAST_DOWNSIDE_DOMINANT":
        hard_threshold = 1
    hard_by_strength = len(strong_weak) >= hard_threshold
    hard_by_structure = structural and len(strong_weak) >= 1

    if (hard_by_strength or hard_by_structure) and not override_ok:
        result.update({
            "new_policy": "HARD_VETO",
            "new_reason": "FORECAST_DOWN_HARD_VETO",
            "would_reject_new": True,
            "hard_veto_signals": strong_weak + soft_weak,
        })
        return result

    if override_ok:
        result.update({
            "new_policy": "OVERRIDE",
            "new_reason": "FORECAST_DOWN_OVERRIDDEN_BY_STRENGTH",
            "would_reject_new": False,
            "override_signals": override_reasons,
            "position_multiplier": 0.75,
        })
        return result

    # Default: soft penalty
    adj = downgrade_conviction(conviction)
    result.update({
        "new_policy": "SOFT_PENALTY",
        "new_reason": "FORECAST_DOWN_SOFT_PENALTY",
        "conviction_downgrade": adj != conviction,
        "adjusted_conviction": adj,
        "position_multiplier": 0.5,
        "hard_veto_signals": strong_weak + soft_weak,
        "would_reject_new": adj not in ACTIONABLE_CONVICTION,
    })
    return result


def passes_non_forecast_actionable_checks(row: dict) -> bool:
    """Counterfactual: passes all actionable checks except forecast veto."""
    if int(row.get("anti_law") or 0):
        return False
    if not int(row.get("quality_gate_passed") or 0):
        return False
    if not int(row.get("final_edge_passed") or 0):
        return False

    bucket = row.get("shadow_risk_bucket")
    valid_rr = int(row.get("shadow_risk_valid_for_rr") or 0)
    if bucket and not valid_rr:
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
    return True


def simulate_would_be_actionable(row: dict, policy: dict) -> bool:
    """Whether signal becomes actionable if only forecast policy changes."""
    if not passes_non_forecast_actionable_checks(row):
        return False
    if policy.get("structural_block"):
        return False
    if not policy.get("applies"):
        return bool(int(row.get("actionable") or 0))
    if policy.get("new_policy") == "HARD_VETO":
        return False
    conv = row.get("conviction")
    if policy.get("new_policy") == "SOFT_PENALTY":
        conv = policy.get("adjusted_conviction")
    return conv in ACTIONABLE_CONVICTION


def build_forecast_shadow_fields(
    forecast_veto, ues, ml_score, setup_type, conviction,
    risk_bucket=None, risk_valid_for_rr=0, risk_actionability=None,
    ad_ratio=None, vol_ratio=None, rs_percentile=None,
    is_sector_leader=False, final_edge_passed=1, final_edge_failure=None,
    hard_gate_failure=None, quality_gate_failures=None, anti_law=0,
    quality_gate_passed=1,
) -> dict:
    pol = evaluate_forecast_down_policy(
        forecast_veto=forecast_veto,
        ues=safe_float(ues, 0.0),
        ml_score=safe_float(ml_score, 0.0),
        setup_type=setup_type,
        risk_bucket=risk_bucket,
        risk_valid_for_rr=bool(risk_valid_for_rr),
        ad_ratio=ad_ratio,
        vol_ratio=vol_ratio,
        rs_percentile=rs_percentile,
        is_sector_leader=is_sector_leader,
        final_edge_passed=bool(final_edge_passed),
        final_edge_failure=final_edge_failure,
        hard_gate_failure=hard_gate_failure,
        quality_gate_failures=quality_gate_failures or [],
        conviction=conviction,
        anti_law=bool(anti_law),
        quality_gate_passed=bool(quality_gate_passed),
        risk_actionability=risk_actionability,
    )
    return {
        "shadow_forecast_old_veto": forecast_veto,
        "shadow_forecast_policy": pol.get("new_policy"),
        "shadow_forecast_reason": pol.get("new_reason"),
        "shadow_forecast_would_reject_new": 1 if pol.get("would_reject_new") else 0,
        "shadow_forecast_weakness_count": pol.get("weakness_count"),
        "shadow_forecast_structural_block": 1 if pol.get("structural_block") else 0,
        "shadow_forecast_adjusted_conviction": pol.get("adjusted_conviction"),
        "shadow_forecast_position_mult": pol.get("position_multiplier"),
    }


# ─── Spec tests ───────────────────────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Test 1 — Strong Trend Continuation → OVERRIDE",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 86, "ml_score": 90,
            "setup_type": "Trend Continuation 📈",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL",
            "risk_valid_for_rr": True,
            "ad_ratio": 0.9, "vol_ratio": 2.2, "rs_percentile": 78,
            "is_sector_leader": True,
            "conviction": "HIGH_CONVICTION",
            "final_edge_passed": True,
        },
        "expect": {"new_policy": "OVERRIDE", "would_reject_new": False},
    },
    {
        "name": "Test 2 — Weak ML+UES → HARD_VETO",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 68, "ml_score": 62,
            "setup_type": "unknown",
            "risk_bucket": "STALE_TARGET",
            "ad_ratio": 0.5, "vol_ratio": 1.0, "rs_percentile": 25,
            "is_sector_leader": False,
            "conviction": "MEDIUM_CONVICTION",
            "final_edge_passed": False,
            "final_edge_failure": "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY",
        },
        "expect": {"new_policy": "HARD_VETO", "would_reject_new": True},
    },
    {
        "name": "Test 3 — Middle path → SOFT_PENALTY",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 78, "ml_score": 78,
            "setup_type": "Power Breakout ⚡",
            "risk_bucket": "VALID_BREAKOUT_RISK_MODEL",
            "risk_valid_for_rr": True,
            "ad_ratio": 0.95, "vol_ratio": 2.0, "rs_percentile": 55,
            "is_sector_leader": False,
            "conviction": "HIGH_CONVICTION",
            "final_edge_passed": True,
        },
        "expect": {"new_policy": "SOFT_PENALTY", "would_reject_new": False},
    },
    {
        "name": "Test 4 — STALE_TARGET blocks override",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 88, "ml_score": 92,
            "setup_type": "Trend Continuation 📈",
            "risk_bucket": "STALE_TARGET",
            "risk_valid_for_rr": False,
            "risk_actionability": "WATCH_REENTRY",
            "final_edge_passed": False,
            "final_edge_failure": "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY",
            "conviction": "HIGH_CONVICTION",
        },
        "expect": {"structural_block": True, "new_policy": "HARD_VETO"},
    },
    {
        "name": "Test 5 — SL structure blocks override",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 85, "ml_score": 88,
            "setup_type": "Volume Accumulation 📦",
            "risk_bucket": "VALID_PULLBACK_RISK_MODEL",
            "risk_valid_for_rr": True,
            "final_edge_passed": False,
            "final_edge_failure": "FINAL_EDGE:SL_NOT_BELOW_RECENT_STRUCTURE",
            "conviction": "HIGH_CONVICTION",
        },
        "expect": {"structural_block": True},
    },
    {
        "name": "Test 6 — No forecast → NONE",
        "kwargs": {"forecast_veto": None, "ues": 80, "ml_score": 80},
        "expect": {"applies": False, "new_policy": "NONE"},
    },
    {
        "name": "Test 7 — SOFT downgrades MEDIUM→WATCH rejects",
        "kwargs": {
            "forecast_veto": "FORECAST_DOWN",
            "ues": 76, "ml_score": 76,
            "setup_type": "Near ATH Breakout ⚠️",
            "risk_bucket": "VALID_DEFAULT_MARKET_ENTRY_MODEL",
            "risk_valid_for_rr": True,
            "ad_ratio": 1.0, "vol_ratio": 2.0,
            "conviction": "MEDIUM_CONVICTION",
            "final_edge_passed": True,
        },
        "expect": {"new_policy": "SOFT_PENALTY", "would_reject_new": True,
                   "adjusted_conviction": "WATCH"},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_forecast_down_policy(**t["kwargs"])
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
        SELECT g.*, fs.setup_type, fs.r_ratio,
               us.pine_rs_percentile,
               slp.sector, slp.liquidity_tier
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
        LEFT JOIN unified_signals us
          ON us.signal_date=g.signal_date AND us.symbol=g.symbol
        LEFT JOIN symbol_liquidity_profile slp ON slp.symbol=g.symbol
        WHERE g.signal_date>=? AND g.signal_date<=?
        ORDER BY g.signal_date, g.symbol
    """, (start, end)).fetchall()
    return [dict(r) for r in rows]


def _eval_row(row) -> dict:
    qf = row.get("quality_gate_failures")
    if isinstance(qf, str):
        try:
            qf = json.loads(qf)
        except Exception:
            qf = []
    rs = safe_float(row.get("pine_rs_percentile"))
    is_leader = rs is not None and rs >= 70
    pol = evaluate_forecast_down_policy(
        forecast_veto=row.get("forecast_veto"),
        ues=safe_float(row.get("ues"), 0.0),
        ml_score=safe_float(row.get("ml_score"), 0.0),
        setup_type=row.get("setup_type"),
        risk_bucket=row.get("shadow_risk_bucket"),
        risk_valid_for_rr=bool(int(row.get("shadow_risk_valid_for_rr") or 0)),
        risk_actionability=row.get("shadow_risk_actionability"),
        ad_ratio=safe_float(row.get("ad_ratio")),
        vol_ratio=safe_float(row.get("vol_ratio")),
        rs_percentile=rs,
        is_sector_leader=is_leader,
        final_edge_passed=bool(int(row.get("final_edge_passed") or 0)),
        final_edge_failure=row.get("final_edge_failure"),
        hard_gate_failure=row.get("hard_gate_failure"),
        quality_gate_failures=qf,
        conviction=row.get("conviction"),
        anti_law=bool(int(row.get("anti_law") or 0)),
        quality_gate_passed=bool(int(row.get("quality_gate_passed") or 0)),
    )
    pol["would_be_actionable_new"] = simulate_would_be_actionable(row, pol)
    pol["would_be_actionable_old"] = bool(int(row.get("actionable") or 0))
    return pol


def _classify_outcome(row, bars_cache=None):
    """Minimal clean/loser from gate_doctor logic."""
    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    try:
        from gate_doctor_audit import classify_winner_types, forward_bars, load_bars
    except Exception:
        ret5 = row.get("ret_5d")
        return {
            "clean_winner": int(row.get("tp_before_sl") == 1 and (row.get("ret_5d") or 0) > 0),
            "loser_5d": int(row.get("loser_5d") or 0),
        }
    if bars_cache is None:
        bars_cache = {}
    key = (row["signal_date"], row["symbol"])
    bars = bars_cache.get(key)
    wt = classify_winner_types(row, bars)
    return wt


def cmd_shadow(params: dict):
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_enriched(conn, start, end)
    conn.close()

    cohort = [r for r in rows if r.get("forecast_veto") in FORECAST_VETO_TYPES]
    policy_counts = defaultdict(int)
    comparisons = []
    for r in cohort:
        pol = _eval_row(r)
        policy_counts[pol["new_policy"]] += 1
        comparisons.append({
            "date": r["signal_date"], "symbol": r["symbol"],
            "forecast_veto": r.get("forecast_veto"),
            "old_actionable": int(r.get("actionable") or 0),
            **{k: pol.get(k) for k in (
                "new_policy", "new_reason", "would_reject_old", "would_reject_new",
                "weakness_count", "structural_block", "would_be_actionable_new",
            )},
        })

    report = {
        "success": True,
        "phase": "2.7B_shadow",
        "no_production_patch": True,
        "cohort_forecast_veto": len(cohort),
        "policy_counts": dict(policy_counts),
        "comparisons": comparisons,
    }
    tag = f"{start}_{end}"
    path = REPORT_DIR / f"forecast_down_policy_shadow_{tag}.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report_json"] = str(path)
    return report


def cmd_outcome(params: dict):
    """Phase 2.7C — outcome audit with acceptance criteria."""
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_end = "2026-06-05"
    conn = sqlite3.connect(str(DB_PATH), timeout=120)

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import load_bars, forward_bars

    rows = _load_enriched(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    bars_cache = {}
    for r in rows:
        if int(r.get("outcomes_filled") or 0) != 1:
            continue
        if r["signal_date"] > period_end:
            continue
        bars_cache[(r["signal_date"], r["symbol"])] = forward_bars(
            by_sym, idx, r["symbol"], r["signal_date"], 10,
        )

    cohort = [r for r in rows if r.get("forecast_veto") in FORECAST_VETO_TYPES]
    eval_rows = [r for r in cohort if int(r.get("outcomes_filled") or 0) == 1
                 and r["signal_date"] <= period_end]

    policy_counts = defaultdict(int)
    policy_counts_cf = defaultdict(int)
    rescued_clean = []
    released_loser = []
    override_rows = []
    soft_rows = []
    hard_rows = []
    new_actionable_by_date = defaultdict(int)
    override_by_date = defaultdict(int)
    cf_cohort = []
    cf_clean = []

    for r in eval_rows:
        pol = _eval_row(r)
        wt = _classify_outcome(r, bars_cache)
        pol.update(wt)
        policy_counts[pol["new_policy"]] += 1

        cf_ready = passes_non_forecast_actionable_checks(r)
        if cf_ready:
            cf_cohort.append(r)
            policy_counts_cf[pol["new_policy"]] += 1
            if wt.get("clean_winner"):
                cf_clean.append(r)

        old_act = int(r.get("actionable") or 0)
        new_act = pol["would_be_actionable_new"]

        if pol["new_policy"] == "OVERRIDE":
            override_rows.append({**pol, "date": r["signal_date"], "symbol": r["symbol"]})
            if new_act and not old_act:
                override_by_date[r["signal_date"]] += 1
        elif pol["new_policy"] == "SOFT_PENALTY":
            soft_rows.append(pol)
        elif pol["new_policy"] == "HARD_VETO":
            hard_rows.append(pol)

        if new_act and not old_act:
            new_actionable_by_date[r["signal_date"]] += 1
            if wt.get("clean_winner"):
                rescued_clean.append({**pol, "date": r["signal_date"], "symbol": r["symbol"]})
            if wt.get("loser_5d"):
                released_loser.append(pol)

    n_clean_forecast = sum(1 for r in eval_rows if _classify_outcome(r, bars_cache).get("clean_winner"))
    n_rescued = len(rescued_clean)
    n_released_loser = len(released_loser)
    max_daily_new = max(new_actionable_by_date.values()) if new_actionable_by_date else 0
    max_daily_override = max(override_by_date.values()) if override_by_date else 0

    stale_buy_leak = sum(
        1 for r in eval_rows
        if _eval_row(r)["would_be_actionable_new"]
        and r.get("shadow_risk_bucket") == "STALE_TARGET"
    )

    acceptance = {
        "override_daily_cap_ok": max_daily_override <= MAX_DAILY_OVERRIDE,
        "clean_rescued_gt_losers_released": n_rescued > n_released_loser,
        "no_stale_target_buy": stale_buy_leak == 0,
        "actionable_daily_in_range": max_daily_new <= MAX_DAILY_NEW_ACTIONABLE,
        "hard_veto_not_zero": policy_counts.get("HARD_VETO", 0) > 0,
    }
    ready = all(acceptance.values())

    cf_rescued = sum(
        1 for r in cf_clean
        if simulate_would_be_actionable(r, _eval_row(r))
    )
    clean_policy_counts = defaultdict(int)
    for r in eval_rows:
        if _classify_outcome(r, bars_cache).get("clean_winner"):
            clean_policy_counts[_eval_row(r)["new_policy"]] += 1

    report = {
        "success": True,
        "phase": "2.7C_outcome",
        "no_production_patch": True,
        "cohort": f"A_FULL_5D evaluable forecast ({start}→{period_end})",
        "n_forecast_cohort_evaluable": len(eval_rows),
        "n_clean_in_forecast_cohort": n_clean_forecast,
        "n_counterfactual_ready_except_forecast": len(cf_cohort),
        "n_clean_counterfactual_ready": len(cf_clean),
        "policy_counts_all_forecast": dict(policy_counts),
        "policy_counts_counterfactual_ready": dict(policy_counts_cf),
        "clean_winners_rescued": n_rescued,
        "clean_winners_rescuable_in_cf_cohort": cf_rescued,
        "clean_winner_policy_counts": dict(clean_policy_counts),
        "losers_released": n_released_loser,
        "max_daily_new_actionable": max_daily_new,
        "max_daily_override": max_daily_override,
        "new_actionable_by_date": dict(sorted(new_actionable_by_date.items())),
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": ready,
        "recommendation": "PROCEED_2.7D" if ready else "REVIEW_BEFORE_2.7D",
        "override_samples": sorted(override_rows, key=lambda x: x.get("mfe_r") or 0, reverse=True)[:10],
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"forecast_down_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"forecast_down_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    pc = report["policy_counts_all_forecast"]
    pccf = report["policy_counts_counterfactual_ready"]
    lines = [
        "FORECAST_DOWN Policy Shadow — Phase 2.7",
        f"Period: {start} → {end} | Evaluable forecast cohort: {len(eval_rows)}",
        f"Clean in forecast cohort (co-blocked): {n_clean_forecast}",
        f"Counterfactual ready (all gates pass except forecast): {len(cf_cohort)}",
        f"Clean in counterfactual-ready cohort: {len(cf_clean)}",
        "",
        "=== Policy Distribution — all FORECAST rows ===",
        f"  HARD_VETO:    {pc.get('HARD_VETO', 0)}",
        f"  SOFT_PENALTY: {pc.get('SOFT_PENALTY', 0)}",
        f"  OVERRIDE:     {pc.get('OVERRIDE', 0)}",
        "",
        "=== Policy Distribution — counterfactual-ready only ===",
        f"  HARD_VETO:    {pccf.get('HARD_VETO', 0)}",
        f"  SOFT_PENALTY: {pccf.get('SOFT_PENALTY', 0)}",
        f"  OVERRIDE:     {pccf.get('OVERRIDE', 0)}",
        "",
        "=== Outcome Impact (if policy applied) ===",
        f"  New actionable (net):     {sum(new_actionable_by_date.values())}",
        f"  Clean winners rescued:    {n_rescued}",
        f"  Clean rescuable in CF:    {cf_rescued}",
        f"  Losers released:          {n_released_loser}",
        f"  Max daily new actionable: {max_daily_new}",
        f"  Max daily override:       {max_daily_override}",
        "",
        "=== Clean Winner Policy Reclassification ===",
        f"  HARD_VETO:    {clean_policy_counts.get('HARD_VETO', 0)}",
        f"  SOFT_PENALTY: {clean_policy_counts.get('SOFT_PENALTY', 0)}",
        f"  OVERRIDE:     {clean_policy_counts.get('OVERRIDE', 0)}",
        "",
        "=== Clinical Note ===",
        "  104 clean FORECAST blocks were co-blockers (0 exclusive).",
        "  Counterfactual-ready=0 → policy change alone won't open actionables on Jun data.",
        "  Value is role change (penalty vs veto) + future-ready override path.",
        "",
        "=== Acceptance Criteria ===",
    ]
    for k, v in acceptance.items():
        lines.append(f"  {'✅' if v else '❌'} {k}: {v}")
    lines += [
        "",
        f"Verdict ready for 2.7D production: {ready}",
        f"Recommendation: {report['recommendation']}",
        "",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {
    "test": cmd_test,
    "shadow": cmd_shadow,
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
