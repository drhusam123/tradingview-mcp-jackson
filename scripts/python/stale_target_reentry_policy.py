#!/usr/bin/env python3
"""
stale_target_reentry_policy — Phase 2.12 Spec (shadow only, NOT production)

When close >= target (STALE_TARGET), P0 routes to WATCH_REENTRY instead of RR_TOO_LOW.
This module defines pullback re-entry triggers for the watch queue.

Production status: KEEP_SHADOW_REPORTING — reentry engine not wired to score_all.

Usage:
    python3 scripts/python/stale_target_reentry_policy.py test
    python3 scripts/python/stale_target_reentry_policy.py struct
    python3 scripts/python/stale_target_reentry_policy.py outcome
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

STALE_REENTRY_POLICY = "KEEP_SHADOW_REPORTING"
MIN_REENTRY_RR = 1.3
PULLBACK_TOLERANCE = 0.02
MIN_VOL_RATIO = 1.5
MIN_UES = 72.0
MIN_ML = 65.0
MAX_REENTRY_DAYS = 5


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


def compute_rr(entry, stop, target):
    if not (entry and stop and target and stop < entry < target):
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return (target - entry) / risk


def evaluate_reentry_trigger(
    *,
    entry: float,
    stop: float,
    target: float,
    close: float,
    ues: float = 0.0,
    ml_score: float = 0.0,
    vol_ratio: float | None = None,
    forward_bars: list | None = None,
) -> dict:
    """Scan up to MAX_REENTRY_DAYS forward bars for pullback re-entry."""
    entry = safe_float(entry)
    stop = safe_float(stop)
    target = safe_float(target)
    close = safe_float(close)
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)

    result = {
        "applies": False,
        "stale_at_signal": False,
        "reentry_triggered": False,
        "reentry_day": None,
        "reentry_price": None,
        "reentry_rr": None,
        "policy": "WATCH_ONLY",
        "reason": None,
        "would_actionable": False,
    }

    if not (entry and stop and target and close):
        return result

    if close < target:
        return result

    result["applies"] = True
    result["stale_at_signal"] = True
    result["policy"] = "WATCH_REENTRY"

    if ues < MIN_UES or ml_score < MIN_ML:
        result["reason"] = "STALE_WEAK_SCORES"
        return result

    base_rr = compute_rr(entry, stop, target)
    if base_rr is None or base_rr < MIN_REENTRY_RR:
        result["reason"] = "STALE_RR_TOO_LOW"
        return result

    if not forward_bars:
        result["reason"] = "STALE_NO_FORWARD_BARS"
        return result

    entry_hi = entry * (1.0 + PULLBACK_TOLERANCE)
    vol_ok_signal = vol_ratio is None or vol_ratio >= MIN_VOL_RATIO
    for i, bar in enumerate(forward_bars[:MAX_REENTRY_DAYS], start=1):
        # forward_bars: (date, open, high, low, close)
        low = safe_float(bar[3] if len(bar) > 3 else None)
        if low is None:
            continue
        touched = low <= entry_hi
        if touched and vol_ok_signal:
            rr = compute_rr(entry, stop, target)
            result.update({
                "reentry_triggered": True,
                "reentry_day": i,
                "reentry_price": entry,
                "reentry_rr": round(rr, 3) if rr else None,
                "policy": "REENTRY_CANDIDATE",
                "reason": f"STALE_PULLBACK_D{i}",
                "would_actionable": rr is not None and rr >= MIN_REENTRY_RR,
            })
            return result

    result["reason"] = "STALE_NO_PULLBACK"
    return result


SPEC_TESTS = [
    {
        "name": "Not stale — no apply",
        "kwargs": {"entry": 10, "stop": 9, "target": 12, "close": 11},
        "expect": {"applies": False},
    },
    {
        "name": "Stale weak UES",
        "kwargs": {"entry": 10, "stop": 9, "target": 11, "close": 12, "ues": 65, "ml_score": 70},
        "expect": {"applies": True, "reason": "STALE_WEAK_SCORES"},
    },
    {
        "name": "Stale pullback day 2",
        "kwargs": {
            "entry": 10, "stop": 9, "target": 12, "close": 12.5,
            "ues": 78, "ml_score": 80, "vol_ratio": 2.0,
            "forward_bars": [
                ("d1", 0, 0, 11.5, 0, 1000),
                ("d2", 0, 0, 9.9, 0, 2000),
            ],
        },
        "expect": {"reentry_triggered": True, "reentry_day": 2, "would_actionable": True},
    },
    {
        "name": "Stale no pullback",
        "kwargs": {
            "entry": 10, "stop": 9, "target": 13, "close": 13.5,
            "ues": 80, "ml_score": 75, "vol_ratio": 2.0,
            "forward_bars": [("d1", 0, 0, 11.8, 0, 1000), ("d2", 0, 0, 11.5, 0, 1000)],
        },
        "expect": {"reason": "STALE_NO_PULLBACK"},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_reentry_trigger(**t["kwargs"])
        for k, v in t["expect"].items():
            if got.get(k) != v:
                errors.append(f"{t['name']}: {k} expected {v!r} got {got.get(k)!r}")
                break
        else:
            passed += 1
    return {"success": len(errors) == 0, "passed": passed, "total": len(SPEC_TESTS), "errors": errors}


def _load_stale_cohort(conn, start: str, end: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT g.*, fs.source_breakdown
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
        WHERE g.signal_date>=? AND g.signal_date<=?
          AND g.shadow_risk_bucket='STALE_TARGET'
          AND g.final_edge_failure='FINAL_EDGE:STALE_TARGET_WATCH_REENTRY'
        ORDER BY g.signal_date, g.symbol
    """, (start, end)).fetchall()
    return [dict(r) for r in rows]


def cmd_struct(params: dict | None = None):
    params = params or {}
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-05")

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_stale_cohort(conn, start, end)
    conn.close()

    veto = Counter()
    for r in rows:
        veto[r.get("veto_reason") or "UNKNOWN"] += 1

    report = {
        "success": True,
        "phase": "2.12_structural",
        "policy": STALE_REENTRY_POLICY,
        "cohort": f"STALE_TARGET_WATCH ({start}→{end})",
        "n_stale": len(rows),
        "n_evaluable": sum(1 for r in rows if int(r.get("outcomes_filled") or 0)),
        "top_veto_reasons": [{"reason": k, "n": v} for k, v in veto.most_common(10)],
        "note": "P0 fixed RR mislabel — co-blockers (FORECAST/QG) still dominate",
        "recommendation": "BUILD_REENTRY_QUEUE_SHADOW",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"stale_reentry_struct_{tag}.json"
    txt_path = REPORT_DIR / f"stale_reentry_struct_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "STALE_TARGET Reentry — Phase 2.12 Structural",
        f"Cohort: {len(rows)} stale watch rows | evaluable: {report['n_evaluable']}",
        f"Policy: {STALE_REENTRY_POLICY}",
        "",
        "=== Top Veto Reasons (co-blockers) ===",
    ]
    for v in report["top_veto_reasons"]:
        lines.append(f"  {v['n']:>3}  {v['reason']}")
    lines += ["", f"Recommendation: {report['recommendation']}"]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


def cmd_outcome(params: dict | None = None):
    params = params or {}
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-05")

    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from gate_doctor_audit import classify_winner_types, forward_bars, load_bars

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    rows = _load_stale_cohort(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    eval_rows = [r for r in rows if int(r.get("outcomes_filled") or 0) == 1]
    triggered = []
    clean_missed = []
    loser_avoided = []

    for r in eval_rows:
        entry = safe_float(r.get("risk_entry") or r.get("entry_price"))
        stop = safe_float(r.get("risk_stop") or r.get("stop_loss"))
        target = safe_float(r.get("risk_target") or r.get("t1_target"))
        close = safe_float(r.get("risk_close"))
        bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], MAX_REENTRY_DAYS + 2)
        pol = evaluate_reentry_trigger(
            entry=entry, stop=stop, target=target, close=close,
            ues=r.get("ues"), ml_score=r.get("ml_score"),
            vol_ratio=safe_float(r.get("vol_ratio")),
            forward_bars=bars,
        )
        wt = classify_winner_types(r, bars)
        if pol.get("reentry_triggered"):
            triggered.append({
                "date": r["signal_date"], "symbol": r["symbol"],
                "day": pol["reentry_day"], "ret_5d": r.get("ret_5d"),
                "clean": wt.get("clean_winner"),
            })
        if wt.get("clean_winner") and not pol.get("reentry_triggered"):
            clean_missed.append(r["symbol"])
        if int(r.get("loser_5d") or 0) and not pol.get("reentry_triggered"):
            loser_avoided.append(r["symbol"])

    ret_triggered = [safe_float(t["ret_5d"]) for t in triggered if t.get("ret_5d") is not None]
    momentum_continuation = len(clean_missed) > 0 and len(triggered) == 0
    acceptance = {
        "reentry_rate_lte_40pct": len(triggered) <= max(1, int(len(eval_rows) * 0.40)),
        "triggered_avg_ret_positive": (mean(ret_triggered) > 0) if ret_triggered else None,
        "no_false_chase_when_momentum": momentum_continuation or len(triggered) > 0,
    }
    if len(triggered) == 0 and momentum_continuation:
        recommendation = "KEEP_WATCH_QUEUE_MOMENTUM_PATH"
    elif len(triggered) > 0 and ret_triggered and mean(ret_triggered) > 0:
        recommendation = "PROCEED_2.12D_QUEUE"
    else:
        recommendation = "REVIEW_BEFORE_2.12D"

    report = {
        "success": True,
        "phase": "2.12_outcome",
        "policy": STALE_REENTRY_POLICY,
        "cohort": f"STALE_TARGET_WATCH ({start}→{end})",
        "n_cohort": len(rows),
        "n_evaluable": len(eval_rows),
        "reentry_triggered": len(triggered),
        "clean_winners_total": sum(1 for r in eval_rows
                                  if classify_winner_types(r, forward_bars(
                                      by_sym, idx, r["symbol"], r["signal_date"], 10)
                                  ).get("clean_winner")),
        "clean_without_reentry": len(clean_missed),
        "losers_avoided_no_reentry": len(loser_avoided),
        "triggered_samples": triggered[:12],
        "avg_ret_5d_triggered": round(mean(ret_triggered), 4) if ret_triggered else None,
        "momentum_continuation_detected": momentum_continuation,
        "acceptance_criteria": acceptance,
        "verdict_ready_for_production": recommendation == "PROCEED_2.12D_QUEUE",
        "recommendation": recommendation,
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"stale_reentry_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"stale_reentry_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "STALE_TARGET Reentry Shadow — Phase 2.12",
        f"Cohort: {len(eval_rows)} evaluable | reentry triggered: {len(triggered)}",
        f"Clean winners: {report['clean_winners_total']} | clean w/o reentry: {len(clean_missed)}",
        f"Avg ret_5d (triggered): {report['avg_ret_5d_triggered']}",
        f"Momentum continuation (no pullback): {momentum_continuation}",
        "",
        "=== Acceptance ===",
    ]
    for k, v in acceptance.items():
        mark = "—" if v is None else ("✅" if v else "❌")
        lines.append(f"  {mark} {k}: {v}")
    lines += [
        "",
        f"Recommendation: {report['recommendation']}",
        f"STALE_REENTRY_POLICY={STALE_REENTRY_POLICY}",
        "NO PRODUCTION PATCH APPLIED.",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {"test": cmd_test, "struct": cmd_struct, "outcome": cmd_outcome}


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
