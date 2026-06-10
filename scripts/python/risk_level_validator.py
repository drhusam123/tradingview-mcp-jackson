#!/usr/bin/env python3
"""
validate_risk_levels() — Phase 2.3 Spec (shadow only, NOT wired to production)

Runs BEFORE Final Edge / RR_TOO_LOW in the proposed pipeline:

    get scan levels → validate_risk_levels() → Final Edge (only if valid_for_rr)

Usage:
    python3 scripts/python/risk_level_validator.py test
    python3 scripts/python/risk_level_validator.py shadow '{"start_date":"2026-06-01"}'
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

PULLBACK_SETUPS = ("pullback", "accumulation", "volume_accumulation", "mean_reversion", "retest", "institutional")
BREAKOUT_SETUPS = ("breakout", "momentum", "thrust", "expansion", "power")
MIN_RR = 1.3


def validate_risk_levels(
    symbol: str,
    date: str,
    close: float,
    entry: float,
    target: float,
    stop: float,
    scan_date: str | None = None,
    setup_type: str | None = None,
    level_source: str | None = None,
    atr: float | None = None,
    next_open: float | None = None,
) -> dict:
    """
    Phase 2.3 spec — clinical risk-level validation before RR gate.

    Returns full diagnosis dict; never collapses to bare True/False.
    """
    _ = symbol, date, scan_date, level_source, atr  # reserved for shadow logging / future rules

    result = {
        "valid_for_rr": False,
        "actionability": "HARD_BLOCK",
        "bucket": None,
        "effective_entry": None,
        "effective_entry_model": None,
        "rr": None,
        "risk_warning": None,
        "final_edge_reason": None,
    }

    if not entry or not target or not stop or entry <= 0 or target <= 0 or stop <= 0:
        result.update({
            "bucket": "INVALID_LEVELS",
            "final_edge_reason": "FINAL_EDGE:INVALID_RISK_LEVELS",
        })
        return result

    if stop >= entry:
        result.update({
            "bucket": "INVALID_STOP",
            "final_edge_reason": "FINAL_EDGE:INVALID_STOP",
        })
        return result

    risk = entry - stop
    if risk <= 0:
        result.update({
            "bucket": "INVALID_RISK",
            "final_edge_reason": "FINAL_EDGE:INVALID_RISK",
        })
        return result

    # Rule 3 — target already crossed (never RR_TOO_LOW here)
    if close >= target:
        result.update({
            "valid_for_rr": False,
            "actionability": "WATCH_REENTRY",
            "bucket": "STALE_TARGET",
            "risk_warning": "target_already_crossed",
            "final_edge_reason": "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY",
        })
        return result

    # Rule 4 — entry zone missed (>2R above entry)
    distance = close - entry
    if distance >= 2.0 * risk:
        result.update({
            "valid_for_rr": False,
            "actionability": "WATCH_PULLBACK",
            "bucket": "ENTRY_ALREADY_GONE",
            "risk_warning": "entry_zone_missed",
            "final_edge_reason": "FINAL_EDGE:ENTRY_ALREADY_GONE",
        })
        return result

    setup = (setup_type or "").lower()

    if any(x in setup for x in PULLBACK_SETUPS):
        effective_entry = entry
        bucket = "VALID_PULLBACK_RISK_MODEL"
        entry_model = "entry_price"
    elif any(x in setup for x in BREAKOUT_SETUPS):
        effective_entry = next_open if next_open else close
        bucket = "VALID_BREAKOUT_RISK_MODEL"
        entry_model = "next_open_or_close"
    else:
        effective_entry = close
        bucket = "VALID_DEFAULT_MARKET_ENTRY_MODEL"
        entry_model = "close"

    if stop >= effective_entry:
        result.update({
            "bucket": "INVALID_MARKET_STOP",
            "final_edge_reason": "FINAL_EDGE:INVALID_MARKET_STOP",
        })
        return result

    if target <= effective_entry:
        result.update({
            "valid_for_rr": False,
            "actionability": "WATCH_REENTRY",
            "bucket": "TARGET_NOT_ACTIONABLE",
            "risk_warning": "target_below_effective_entry",
            "final_edge_reason": "FINAL_EDGE:TARGET_NOT_ACTIONABLE",
        })
        return result

    eff_risk = effective_entry - stop
    if eff_risk <= 0:
        result.update({
            "bucket": "INVALID_RISK",
            "final_edge_reason": "FINAL_EDGE:INVALID_RISK",
        })
        return result

    rr = (target - effective_entry) / eff_risk
    result.update({
        "valid_for_rr": True,
        "actionability": "BUY",
        "bucket": bucket,
        "effective_entry": round(effective_entry, 4),
        "effective_entry_model": entry_model,
        "rr": round(rr, 4),
        "risk_warning": None,
        "final_edge_reason": None,
    })
    return result


def apply_rr_minimum(risk_state: dict, min_rr: float = MIN_RR) -> dict:
    """Final Edge RR gate — only runs when valid_for_rr=True."""
    out = dict(risk_state)
    if not out.get("valid_for_rr"):
        return out
    rr = out.get("rr")
    if rr is not None and rr < min_rr:
        out["actionability"] = "HARD_BLOCK"
        out["bucket"] = "RR_TOO_LOW"
        out["final_edge_reason"] = "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC"
    return out


# ─── Spec test cases (Phase 2.3) ───────────────────────────────────────────

SPEC_TESTS = [
    {
        "name": "Test 1 — ELSH 06-01 STALE_TARGET",
        "kwargs": {
            "symbol": "ELSH", "date": "2026-06-01",
            "close": 9.28, "entry": 7.87, "target": 8.60, "stop": 7.507,
            "setup_type": "Power Breakout ⚡", "level_source": "scans_same_day",
        },
        "expect": {
            "bucket": "STALE_TARGET",
            "actionability": "WATCH_REENTRY",
            "valid_for_rr": False,
            "final_edge_reason": "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY",
            "not_reason": "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC",
        },
    },
    {
        "name": "Test 2 — ELSH 06-03 lookback stale",
        "kwargs": {
            "symbol": "ELSH", "date": "2026-06-03",
            "close": 11.89, "entry": 7.87, "target": 8.60, "stop": 7.507,
            "scan_date": "2026-06-01", "setup_type": "Power Breakout ⚡",
            "level_source": "scans_lookback_10d",
        },
        "expect": {
            "bucket": "STALE_TARGET",
            "actionability": "WATCH_REENTRY",
            "valid_for_rr": False,
            "risk_warning": "target_already_crossed",
        },
    },
    {
        "name": "Test 3 — FIRE stale target",
        "kwargs": {
            "symbol": "FIRE", "date": "2026-06-03",
            "close": 3.81, "entry": 2.87, "target": 3.17, "stop": 2.721,
            "setup_type": "Power Breakout ⚡",
        },
        "expect": {
            "bucket": "STALE_TARGET",
            "actionability": "WATCH_REENTRY",
            "valid_for_rr": False,
        },
    },
    {
        "name": "Test 4 — Pullback valid (no max(entry,close))",
        "kwargs": {
            "symbol": "TEST", "date": "2026-06-01",
            "close": 10.20, "entry": 10.00, "target": 11.00, "stop": 9.50,
            "setup_type": "volume_accumulation",
        },
        "expect": {
            "effective_entry": 10.00,
            "rr": 2.0,
            "bucket": "VALID_PULLBACK_RISK_MODEL",
            "actionability": "BUY",
            "valid_for_rr": True,
            "effective_entry_model": "entry_price",
        },
    },
    {
        "name": "Test 5 — Breakout valid then RR_TOO_LOW at 1.0",
        "kwargs": {
            "symbol": "TEST", "date": "2026-06-01",
            "close": 10.30, "entry": 10.00, "target": 11.20, "stop": 9.50,
            "setup_type": "breakout", "next_open": 10.35,
        },
        "expect": {
            "effective_entry": 10.35,
            "rr": 1.0,
            "bucket": "VALID_BREAKOUT_RISK_MODEL",
            "actionability": "BUY",
            "valid_for_rr": True,
        },
        "after_rr_gate": {
            "final_edge_reason": "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC",
            "actionability": "HARD_BLOCK",
        },
    },
    {
        "name": "Test 6 — Invalid stop hard block",
        "kwargs": {
            "symbol": "TEST", "date": "2026-06-01",
            "close": 10.00, "entry": 10.00, "target": 11.00, "stop": 10.10,
        },
        "expect": {
            "bucket": "INVALID_STOP",
            "actionability": "HARD_BLOCK",
        },
    },
    {
        "name": "Test 7 — Entry already gone WATCH_PULLBACK",
        "kwargs": {
            "symbol": "TEST", "date": "2026-06-01",
            "close": 11.20, "entry": 10.00, "target": 12.00, "stop": 9.50,
            "setup_type": "volume_accumulation",
        },
        "expect": {
            "bucket": "ENTRY_ALREADY_GONE",
            "actionability": "WATCH_PULLBACK",
            "valid_for_rr": False,
            "final_edge_reason": "FINAL_EDGE:ENTRY_ALREADY_GONE",
        },
    },
]


def _check_expect(got: dict, expect: dict, label: str) -> list[str]:
    errors = []
    for k, v in expect.items():
        if k == "not_reason":
            if got.get("final_edge_reason") == v:
                errors.append(f"{label}: must NOT have final_edge_reason={v}")
            continue
        if got.get(k) != v:
            errors.append(f"{label}: {k} expected {v!r}, got {got.get(k)!r}")
    return errors


def cmd_test():
    errors = []
    passed = 0
    for tc in SPEC_TESTS:
        got = validate_risk_levels(**tc["kwargs"])
        errors.extend(_check_expect(got, tc["expect"], tc["name"]))
        if "after_rr_gate" in tc:
            gated = apply_rr_minimum(got)
            errors.extend(_check_expect(gated, tc["after_rr_gate"], tc["name"] + " [RR gate]"))
        if not any(e.startswith(tc["name"]) for e in errors):
            passed += 1
    return {
        "success": len(errors) == 0,
        "passed": passed,
        "total": len(SPEC_TESTS),
        "errors": errors,
    }


def cmd_shadow(params: dict):
    """Shadow audit: compare old final_edge vs validate_risk_levels on RR cohort."""
    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import (
        RR_GATE, bar_on_date, connect, evaluable, gates_for_row,
        load_bars, load_enriched_rows, next_open, resolve_scan_levels,
        classify_winner_types, forward_bars, _load_rr_blocked_cohort,
    )

    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period = params.get("period", "A_FULL_5D")

    conn = connect()
    by_sym, idx = load_bars(conn)
    bars_cache = {}
    for r in load_enriched_rows(conn, start, end):
        if evaluable(r):
            k = (r["signal_date"], r["symbol"])
            bars_cache[k] = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)
    cohort = _load_rr_blocked_cohort(conn, by_sym, idx, start, end, period, bars_cache)

    comparisons = []
    bucket_counts = {}
    fixes = 0
    for r in cohort:
        sym, d = r["symbol"], r["signal_date"]
        snap = conn.execute(
            "SELECT * FROM gate_audit_snapshots WHERE signal_date=? AND symbol=?",
            (d, sym),
        ).fetchone()
        if snap:
            r = {**r, **dict(snap)}
        bar = bar_on_date(by_sym, idx, sym, d)
        close = bar["close"] if bar else None
        nopen = next_open(by_sym, idx, sym, d)
        levels = resolve_scan_levels(conn, d, sym)
        entry = r.get("entry_price") or levels["entry_price_resolved"]
        stop = r.get("stop_loss") or levels["stop_loss"]
        target = r.get("t1_target") or levels["t1_target"]
        setup = r.get("setup_type") or levels["setup_type_scan"]

        vs = validate_risk_levels(
            symbol=sym, date=d, close=close, entry=entry, target=target, stop=stop,
            scan_date=levels["scan_date"], setup_type=setup,
            level_source=levels["level_source"], next_open=nopen,
        )
        gated = apply_rr_minimum(vs)
        old_reason = r.get("veto_reason") or r.get("final_edge_failure")
        new_reason = gated.get("final_edge_reason") or (
            None if gated.get("actionability") == "BUY" else gated.get("bucket")
        )
        had_rr_block = "FINAL_EDGE:RR_TOO_LOW" in str(
            r.get("final_edge_failure") or r.get("veto_reason") or ""
        ) or RR_GATE in gates_for_row(r)
        if had_rr_block and gated.get("bucket") == "STALE_TARGET":
            fixes += 1

        row = {
            "date": d, "symbol": sym,
            "old_final_edge_reason": old_reason,
            "new_bucket": gated.get("bucket"),
            "new_actionability": gated.get("actionability"),
            "new_final_edge_reason": gated.get("final_edge_reason"),
            "effective_entry": gated.get("effective_entry"),
            "effective_entry_model": gated.get("effective_entry_model"),
            "computed_rr": gated.get("rr"),
            "risk_validation_status": gated.get("actionability"),
            "risk_level_source": levels["level_source"],
            "risk_level_age": levels.get("scan_date"),
            "level_source": levels["level_source"],
            "close": close, "entry": entry, "target": target, "stop": stop,
            "tp_before_sl": r.get("tp_before_sl"),
            "ret_5d": r.get("ret_5d"),
        }
        comparisons.append(row)
        b = gated.get("bucket") or "unknown"
        bucket_counts[b] = bucket_counts.get(b, 0) + 1

    conn.close()

    report = {
        "success": True,
        "phase": "2.3_shadow",
        "spec": "validate_risk_levels",
        "no_production_patch": True,
        "cohort_size": len(comparisons),
        "rr_too_low_reclassified_as_stale": fixes,
        "new_bucket_counts": bucket_counts,
        "comparisons": comparisons,
        "integration_point": "score_all: after scan levels fetch, before _apply_final_edge_gates",
        "proposed_snapshot_fields": [
            "risk_bucket", "risk_level_source", "risk_level_age",
            "effective_entry_model", "effective_entry", "computed_rr",
            "risk_validation_status",
        ],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{period}"
    path = REPORT_DIR / f"validate_risk_levels_shadow_{tag}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report_json"] = str(path)
    return report


def cmd_audit_score_shadow(params: dict):
    """Phase 2.4 report — compare old production vs shadow from gate_audit_snapshots."""
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM gate_audit_snapshots
        WHERE signal_date>=? AND signal_date<=?
        ORDER BY signal_date, symbol
    """, (start, end)).fetchall()
    conn.close()

    if not rows:
        return {"success": False, "error": "no snapshots — run rescore first"}

    def has_shadow(r):
        return r["shadow_risk_bucket"] is not None

    with_shadow = [dict(r) for r in rows if has_shadow(r)]
    rr_stale = []
    valid_old_blocked = []
    watch_not_buy = []
    actionable_bad_risk = []
    by_date = {}

    for r in with_shadow:
        d = r["signal_date"]
        by_date.setdefault(d, {"total": 0, "stale_shadow": 0, "actionable": 0})
        by_date[d]["total"] += 1

        old_fe = r.get("old_final_edge_reason") or r.get("veto_reason") or ""
        old_fe_edge = r.get("final_edge_failure") or ""
        old_act = int(r.get("old_actionable") if r.get("old_actionable") is not None else r.get("actionable") or 0)
        sb = r.get("shadow_risk_bucket")
        sa = r.get("shadow_risk_actionability")
        valid_rr = int(r.get("shadow_risk_valid_for_rr") or 0)

        had_rr_block = (
            "RR_TOO_LOW" in str(old_fe)
            or "RR_TOO_LOW" in str(old_fe_edge)
        )
        if had_rr_block and sb == "STALE_TARGET":
            rr_stale.append(r)
        if valid_rr and not old_act:
            valid_old_blocked.append(r)
        if sa in ("WATCH_REENTRY", "WATCH_PULLBACK"):
            watch_not_buy.append(r)
            by_date[d]["stale_shadow"] += 1
        if old_act and sb in ("STALE_TARGET", "INVALID_STOP", "ENTRY_ALREADY_GONE", "INVALID_MARKET_STOP"):
            actionable_bad_risk.append(r)
        if old_act:
            by_date[d]["actionable"] += 1

    actionable_total = sum(int(r.get("old_actionable") if r.get("old_actionable") is not None else r.get("actionable") or 0) for r in with_shadow)
    shadow_buckets = {}
    for r in with_shadow:
        b = r.get("shadow_risk_bucket") or "unknown"
        shadow_buckets[b] = shadow_buckets.get(b, 0) + 1

    report = {
        "success": True,
        "phase": "2.4",
        "period": {"start": start, "end": end},
        "n_snapshots": len(rows),
        "n_with_shadow": len(with_shadow),
        "q1_rr_too_low_to_stale_target": len(rr_stale),
        "q2_shadow_valid_old_blocked": len(valid_old_blocked),
        "q3_watch_not_buy": len(watch_not_buy),
        "q4_actionable_with_bad_shadow_risk": len(actionable_bad_risk),
        "q5_by_date": by_date,
        "old_actionable_total": actionable_total,
        "shadow_bucket_counts": shadow_buckets,
        "actionable_bad_risk_samples": [
            {"date": r["signal_date"], "symbol": r["symbol"],
             "shadow_bucket": r["shadow_risk_bucket"], "old_reason": r.get("old_final_edge_reason")}
            for r in actionable_bad_risk[:20]
        ],
        "valid_release_candidates_sample": [
            {"date": r["signal_date"], "symbol": r["symbol"],
             "shadow_rr": r.get("shadow_computed_rr"), "old_reason": r.get("old_final_edge_reason")}
            for r in valid_old_blocked[:20]
        ],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"risk_shadow_score_all_audit_{tag}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Risk Shadow score_all Audit — Phase 2.4",
        f"Period: {start} → {end}",
        f"Snapshots: {len(rows)} | With shadow: {len(with_shadow)}",
        f"Old actionable total (unchanged): {actionable_total}",
        "",
        "=== 5 Clinical Questions ===",
        f"Q1 RR_TOO_LOW → STALE_TARGET: {len(rr_stale)}",
        f"Q2 shadow valid_for_rr but old blocked: {len(valid_old_blocked)}",
        f"Q3 WATCH (not BUY): {len(watch_not_buy)}",
        f"Q4 old actionable + bad shadow risk: {len(actionable_bad_risk)}",
        "",
        "=== Shadow buckets ===",
    ]
    for k, v in sorted(shadow_buckets.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {k:<32} {v:>5}")
    lines += ["", "=== By date (06-08+ survival/meta era) ==="]
    for d in sorted(by_date):
        st = by_date[d]
        lines.append(f"  {d}: total={st['total']} watch_shadow={st['stale_shadow']} old_actionable={st['actionable']}")

    txt_path = REPORT_DIR / f"risk_shadow_score_all_audit_{tag}.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


def _had_rr_too_low(r: dict) -> bool:
    for k in ("old_final_edge_reason", "final_edge_failure", "veto_reason"):
        if "RR_TOO_LOW" in str(r.get(k) or ""):
            return True
    return False


def _is_clean_winner(r: dict) -> bool:
    if int(r.get("tp_before_sl") or 0) != 1:
        return False
    entry, stop, mfe = r.get("risk_entry") or r.get("entry_price"), r.get("risk_stop") or r.get("stop_loss"), r.get("mfe_5d")
    if entry and stop and mfe and entry > stop:
        risk = (entry - stop) / entry
        if risk > 0 and mfe / risk >= 2.0:
            return True
    return int(r.get("winner_5d") or 0) == 1


def _cohort_stats(rows: list, label: str) -> dict:
    eval_rows = [r for r in rows if int(r.get("outcomes_filled") or 0) == 1 and r.get("ret_5d") is not None]
    n = len(rows)
    ne = len(eval_rows)
    tp = sum(int(r.get("tp_before_sl") or 0) == 1 for r in eval_rows)
    clean = sum(_is_clean_winner(r) for r in eval_rows)
    losers = sum(int(r.get("loser_5d") or 0) == 1 for r in eval_rows)
    ret5 = [r.get("ret_5d") for r in eval_rows if r.get("ret_5d") is not None]
    mfe = [r.get("mfe_5d") for r in eval_rows if r.get("mfe_5d") is not None]
    return {
        "cohort": label,
        "n": n,
        "n_eval_5d": ne,
        "tp_before_sl": tp,
        "tp_before_sl_pct": round(tp / ne, 3) if ne else None,
        "clean_winners": clean,
        "clean_winner_pct": round(clean / ne, 3) if ne else None,
        "losers_5d": losers,
        "loser_pct": round(losers / ne, 3) if ne else None,
        "avg_ret_5d_pct": round(sum(ret5) / len(ret5) * 100, 2) if ret5 else None,
        "avg_mfe_5d_pct": round(sum(mfe) / len(mfe) * 100, 2) if mfe else None,
    }


def cmd_audit_shadow_outcome(params: dict):
    """Phase 2.5 — shadow bucket vs actual outcomes (safety before patch)."""
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-05")
    period = params.get("period", "A_FULL_5D")

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import cmd_fill_outcomes, period_bucket
    cmd_fill_outcomes({"start_date": start, "end_date": params.get("end_date", "2026-06-10")})

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("""
        SELECT * FROM gate_audit_snapshots
        WHERE signal_date>=? AND signal_date<=? AND shadow_risk_bucket IS NOT NULL
    """, (start, params.get("end_date", "2026-06-10"))).fetchall()]
    conn.close()

    if period != "ALL":
        rows = [r for r in rows if period_bucket(r["signal_date"]) == period]

    rr_stale = [r for r in rows if _had_rr_too_low(r) and r.get("shadow_risk_bucket") == "STALE_TARGET"]
    valid_pullback = [r for r in rows if r.get("shadow_risk_bucket") == "VALID_PULLBACK_RISK_MODEL"]
    shadow_buy = [
        r for r in rows
        if int(r.get("shadow_risk_valid_for_rr") or 0) == 1
        and r.get("shadow_risk_actionability") == "BUY"
    ]
    shadow_buy_blocked = [
        r for r in shadow_buy
        if not int(r.get("old_actionable") if r.get("old_actionable") is not None else r.get("actionable") or 0)
    ]
    watch_reentry = [
        r for r in rows
        if r.get("shadow_risk_actionability") == "WATCH_REENTRY"
        or (r.get("shadow_risk_bucket") == "STALE_TARGET" and r.get("shadow_risk_actionability") == "WATCH_REENTRY")
    ]
    watch_reentry = [r for r in rows if r.get("shadow_risk_actionability") == "WATCH_REENTRY"]

    # Patch simulation: only RR misdiagnosis fixed, other gates unchanged
    patch_release = [
        r for r in rows
        if _had_rr_too_low(r)
        and r.get("shadow_risk_bucket") not in ("STALE_TARGET", "ENTRY_ALREADY_GONE", "INVALID_STOP", "INVALID_MARKET_STOP")
        and int(r.get("shadow_risk_valid_for_rr") or 0) == 1
        and int(r.get("quality_gate_passed") or 0) == 1
        and int(r.get("final_edge_passed") or 0) == 0
        and _had_rr_too_low(r)
        and not int(r.get("old_actionable") if r.get("old_actionable") is not None else 0)
    ]
    # Narrower: RR was ONLY final edge failure (would become actionable if RR fixed)
    patch_rr_only = [
        r for r in patch_release
        if str(r.get("final_edge_failure") or "").startswith("FINAL_EDGE:RR_TOO_LOW")
        and int(r.get("anti_law") or 0) == 0
        and not r.get("forecast_veto")
    ]

    report = {
        "success": True,
        "phase": "2.5",
        "title": "Shadow vs Outcome — Safety Confirmation",
        "period": {"start": start, "end": end, "cohort": period},
        "verdict_ready_for_patch": None,
        "q1_rr_stale_target_173": _cohort_stats(rr_stale, "RR_TOO_LOW→STALE_TARGET"),
        "q2_valid_pullback": _cohort_stats(valid_pullback, "VALID_PULLBACK_RISK_MODEL"),
        "q3_shadow_buy_safety": _cohort_stats(shadow_buy, "shadow_BUY_all"),
        "q3_shadow_buy_old_blocked": _cohort_stats(shadow_buy_blocked, "shadow_BUY_old_blocked"),
        "q4_watch_reentry_continued_up": _cohort_stats(watch_reentry, "WATCH_REENTRY"),
        "q5_patch_actionable_simulation": {
            "current_old_actionable": sum(
                int(r.get("old_actionable") if r.get("old_actionable") is not None else r.get("actionable") or 0)
                for r in rows
            ),
            "would_release_if_rr_only_fixed": len(patch_rr_only),
            "would_release_broad": len(patch_release),
            "net_new_actionable_estimate": len(patch_rr_only),
            "release_samples": [
                {"date": r["signal_date"], "symbol": r["symbol"],
                 "shadow_rr": r.get("shadow_computed_rr"), "tp_before_sl": r.get("tp_before_sl"),
                 "ret_5d_pct": round((r.get("ret_5d") or 0) * 100, 2) if r.get("ret_5d") else None}
                for r in patch_rr_only[:15]
            ],
        },
        "clinical_summary": {},
    }

    q1 = report["q1_rr_stale_target_173"]
    q2 = report["q2_valid_pullback"]
    q3b = report["q3_shadow_buy_old_blocked"]
    q4 = report["q4_watch_reentry_continued_up"]
    q5 = report["q5_patch_actionable_simulation"]

    stale_mostly_missed = (
        q1.get("tp_before_sl_pct") is not None and q1["tp_before_sl_pct"] >= 0.5
    )
    pullback_good = (
        q2.get("clean_winner_pct") is not None and q2["clean_winner_pct"] >= 0.15
    )
    buy_path_safe = (
        q3b.get("loser_pct") is not None and q3b["loser_pct"] <= 0.55
    )
    patch_modest = q5["net_new_actionable_estimate"] <= 20

    report["verdict_ready_for_patch"] = bool(
        stale_mostly_missed and pullback_good and buy_path_safe and patch_modest
    )
    report["clinical_summary"] = {
        "stale_target_mostly_missed_moves": stale_mostly_missed,
        "valid_pullback_has_clean_winners": pullback_good,
        "shadow_buy_path_not_toxic": buy_path_safe,
        "patch_wont_flood_actionables": patch_modest,
        "recommendation": (
            "PROCEED_P0_RISK_PIPELINE_PATCH"
            if report["verdict_ready_for_patch"]
            else "REVIEW_BEFORE_PATCH"
        ),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{period}"
    json_path = REPORT_DIR / f"shadow_outcome_audit_{tag}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Shadow vs Outcome Audit — Phase 2.5",
        f"Cohort: {period} ({start} → {end})",
        f"Verdict ready for patch: {report['verdict_ready_for_patch']}",
        f"Recommendation: {report['clinical_summary']['recommendation']}",
        "",
        "=== Q1 RR_TOO_LOW → STALE_TARGET ===",
        f"  n={q1['n']} eval_5d={q1['n_eval_5d']} TP|SL={q1['tp_before_sl']} ({q1.get('tp_before_sl_pct',0):.1%})",
        f"  clean_winners={q1['clean_winners']} avg_ret_5d={q1.get('avg_ret_5d_pct')}%",
        "  → فرص فاتت (ليس BUY مباشر) إذا TP|SL عالي",
        "",
        "=== Q2 VALID_PULLBACK ===",
        f"  n={q2['n']} clean_winners={q2['clean_winners']} ({q2.get('clean_winner_pct',0):.1%})",
        f"  losers={q2['losers_5d']} ({q2.get('loser_pct',0):.1%})",
        "",
        "=== Q3 Shadow BUY path safety ===",
        f"  old_blocked BUY path: n={q3b['n']} losers={q3b['losers_5d']} ({q3b.get('loser_pct',0):.1%})",
        "",
        "=== Q4 WATCH_REENTRY continued up? ===",
        f"  n={q4['n']} avg_ret_5d={q4.get('avg_ret_5d_pct')}% avg_mfe={q4.get('avg_mfe_5d_pct')}%",
        f"  TP|SL={q4['tp_before_sl']} → يحتاج reentry engine لا BUY",
        "",
        "=== Q5 Patch actionable impact ===",
        f"  current actionable: {q5['current_old_actionable']}",
        f"  net new if RR-only fix: {q5['net_new_actionable_estimate']}",
    ]
    txt_path = REPORT_DIR / f"shadow_outcome_audit_{tag}.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {
    "test": cmd_test,
    "shadow": cmd_shadow,
    "audit_score_shadow": cmd_audit_score_shadow,
    "audit_shadow_outcome": cmd_audit_shadow_outcome,
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
