#!/usr/bin/env python3
"""
stale_momentum_policy — Phase 2.12B Spec (shadow only, NOT production)

When STALE_TARGET (close >= target) and price continues without pullback,
define a controlled momentum-continuation entry (half-size, structural stop).

Production status: KEEP_SHADOW_REPORTING — not wired to score_all.

Usage:
    python3 scripts/python/stale_momentum_policy.py test
    python3 scripts/python/stale_momentum_policy.py outcome
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

STALE_MOMENTUM_POLICY = "KEEP_SHADOW_REPORTING"
MOMENTUM_WINDOW = 3
MIN_UES = 76.0
MIN_ML = 78.0
TARGET_SUPPORT = 0.98
MIN_CONTINUATION_RR = 1.0
MAX_CHASE_PCT_ABOVE_TARGET = 0.30
POSITION_MULT = 0.50
MIN_REENTRY_RR = 1.3


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


def bar_ohlc(bar):
    """forward_bars tuple: (date, open, high, low, close)."""
    if not bar or len(bar) < 5:
        return None, None, None, None
    return (
        safe_float(bar[1]),
        safe_float(bar[2]),
        safe_float(bar[3]),
        safe_float(bar[4]),
    )


def compute_rr(entry, stop, target):
    if not (entry and stop and target and stop < entry < target):
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return (target - entry) / risk


def structural_momentum_stop(stop: float, target: float, signal_low: float | None) -> float | None:
    """Stop under target support zone — never above entry."""
    candidates = [safe_float(stop), safe_float(target, 0) * TARGET_SUPPORT]
    if signal_low:
        candidates.append(safe_float(signal_low))
    valid = [c for c in candidates if c is not None and c > 0]
    return min(valid) if valid else None


def evaluate_momentum_continuation(
    *,
    entry: float,
    stop: float,
    target: float,
    close: float,
    signal_low: float | None = None,
    ues: float = 0.0,
    ml_score: float = 0.0,
    forward_bars: list | None = None,
) -> dict:
    """Detect day-1..3 continuation after stale target crossed."""
    entry = safe_float(entry)
    stop = safe_float(stop)
    target = safe_float(target)
    close = safe_float(close)
    ues = safe_float(ues, 0.0)
    ml_score = safe_float(ml_score, 0.0)

    result = {
        "applies": False,
        "stale_at_signal": False,
        "momentum_triggered": False,
        "trigger_day": None,
        "continuation_entry": None,
        "continuation_stop": None,
        "continuation_target": None,
        "continuation_rr": None,
        "policy": "WATCH_ONLY",
        "reason": None,
        "position_multiplier": POSITION_MULT,
        "would_actionable": False,
    }

    if not (entry and stop and target and close):
        return result
    if close < target:
        return result

    result["applies"] = True
    result["stale_at_signal"] = True
    result["policy"] = "WATCH_MOMENTUM"

    if ues < MIN_UES or ml_score < MIN_ML:
        result["reason"] = "MOMENTUM_WEAK_SCORES"
        return result

    if not forward_bars:
        result["reason"] = "MOMENTUM_NO_FORWARD_BARS"
        return result

    support = target * TARGET_SUPPORT
    for i, bar in enumerate(forward_bars[:MOMENTUM_WINDOW], start=1):
        _, high, low, bar_close = bar_ohlc(bar)
        if None in (high, low, bar_close):
            continue
        if bar_close <= close:
            continue
        if low < support:
            continue
        if high < close * 1.002:
            continue

        chase_pct = (bar_close - target) / target if target > 0 else 1.0
        if chase_pct > MAX_CHASE_PCT_ABOVE_TARGET:
            result["reason"] = "MOMENTUM_CHASE_TOO_EXTENDED"
            continue

        cont_entry = bar_close
        cont_stop = structural_momentum_stop(stop, target, signal_low or low)
        if cont_stop is None or cont_stop >= cont_entry:
            continue

        risk = cont_entry - cont_stop
        cont_target = cont_entry + 1.5 * risk
        rr = compute_rr(cont_entry, cont_stop, cont_target)
        if rr is None or rr < MIN_CONTINUATION_RR:
            continue

        result.update({
            "momentum_triggered": True,
            "trigger_day": i,
            "continuation_entry": round(cont_entry, 4),
            "continuation_stop": round(cont_stop, 4),
            "continuation_target": round(cont_target, 4),
            "continuation_rr": round(rr, 3),
            "policy": "MOMENTUM_CANDIDATE",
            "reason": f"MOMENTUM_CONTINUE_D{i}",
            "would_actionable": True,
        })
        return result

    if result["reason"] is None:
        result["reason"] = "MOMENTUM_NO_TRIGGER"
    return result


def passes_except_stale(row: dict) -> bool:
    """Would pass if STALE_TARGET were watch-only (co-blockers still apply)."""
    if int(row.get("anti_law") or 0):
        return False
    qf = parse_json_list(row.get("quality_gate_failures"))
    if qf:
        return False
    if not int(row.get("final_edge_passed") or 0):
        fe = row.get("final_edge_failure") or ""
        if fe != "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY":
            return False
    if row.get("forecast_veto"):
        return False
    conv = row.get("conviction") or ""
    return conv in ("ULTRA_CONVICTION", "HIGH_CONVICTION", "MEDIUM_CONVICTION")


SPEC_TESTS = [
    {
        "name": "Not stale",
        "kwargs": {"entry": 10, "stop": 9, "target": 12, "close": 11},
        "expect": {"applies": False},
    },
    {
        "name": "Weak scores",
        "kwargs": {"entry": 10, "stop": 9, "target": 11, "close": 12, "ues": 70, "ml_score": 70},
        "expect": {"reason": "MOMENTUM_WEAK_SCORES"},
    },
    {
        "name": "ELSH-like day 1 continuation",
        "kwargs": {
            "entry": 7.87, "stop": 7.507, "target": 8.596, "close": 9.28,
            "signal_low": 9.28, "ues": 76.6, "ml_score": 89.0,
            "forward_bars": [
                ("2026-06-02", 9.28, 11.13, 9.28, 11.13),
            ],
        },
        "expect": {"momentum_triggered": True, "trigger_day": 1, "would_actionable": True},
    },
    {
        "name": "Target support lost",
        "kwargs": {
            "entry": 10, "stop": 9, "target": 11, "close": 12,
            "ues": 80, "ml_score": 80,
            "forward_bars": [("d1", 12, 12.5, 10.5, 12.2)],
        },
        "expect": {"reason": "MOMENTUM_NO_TRIGGER"},
    },
]


def cmd_test():
    errors = []
    passed = 0
    for t in SPEC_TESTS:
        got = evaluate_momentum_continuation(**t["kwargs"])
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
    shadow_actionable = []
    co_blocked = []

    for r in eval_rows:
        entry = safe_float(r.get("risk_entry") or r.get("entry_price"))
        stop = safe_float(r.get("risk_stop") or r.get("stop_loss"))
        target = safe_float(r.get("risk_target") or r.get("t1_target"))
        close = safe_float(r.get("risk_close"))
        bars = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], MOMENTUM_WINDOW + 2)
        signal_bar = by_sym.get(r["symbol"])
        signal_pos = idx.get(r["symbol"], {}).get(r["signal_date"])
        signal_low = None
        if signal_bar and signal_pos is not None:
            signal_low = signal_bar[signal_pos][3]

        pol = evaluate_momentum_continuation(
            entry=entry, stop=stop, target=target, close=close,
            signal_low=signal_low, ues=r.get("ues"), ml_score=r.get("ml_score"),
            forward_bars=bars,
        )
        wt = classify_winner_types(r, forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10))

        if pol.get("momentum_triggered"):
            triggered.append({
                "date": r["signal_date"], "symbol": r["symbol"],
                "day": pol["trigger_day"], "ret_5d": r.get("ret_5d"),
                "rr": pol.get("continuation_rr"),
                "clean": wt.get("clean_winner"),
                "veto": r.get("veto_reason"),
            })
            if passes_except_stale(r):
                shadow_actionable.append(r["symbol"])
            else:
                co_blocked.append({
                    "symbol": r["symbol"], "date": r["signal_date"],
                    "veto": r.get("veto_reason"),
                })

    ret_trig = [safe_float(t["ret_5d"]) for t in triggered if t.get("ret_5d") is not None]
    clean_trig = sum(1 for t in triggered if t.get("clean"))
    losers_trig = sum(1 for t in triggered if safe_float(t.get("ret_5d"), 0) < 0)

    acceptance = {
        "trigger_rate_lte_50pct": len(triggered) <= max(1, int(len(eval_rows) * 0.50)),
        "triggered_avg_ret_positive": (mean(ret_trig) > 0) if ret_trig else None,
        "clean_rate_gte_50pct": (clean_trig / len(triggered) >= 0.5) if triggered else None,
        "shadow_actionable_lte_5": len(shadow_actionable) <= 5,
        "losers_lte_clean": losers_trig <= clean_trig,
    }

    if not triggered:
        recommendation = "REVIEW_MOMENTUM_THRESHOLDS"
    elif acceptance["triggered_avg_ret_positive"] and acceptance.get("clean_rate_gte_50pct"):
        recommendation = "PROCEED_2.12B_WATCH_QUEUE"
    else:
        recommendation = "REVIEW_BEFORE_2.12B"

    report = {
        "success": True,
        "phase": "2.12B_momentum_outcome",
        "policy": STALE_MOMENTUM_POLICY,
        "cohort": f"STALE_TARGET_WATCH ({start}→{end})",
        "n_evaluable": len(eval_rows),
        "momentum_triggered": len(triggered),
        "clean_on_triggered": clean_trig,
        "losers_on_triggered": losers_trig,
        "shadow_actionable_if_co_blockers_removed": len(shadow_actionable),
        "shadow_actionable_symbols": shadow_actionable[:10],
        "co_blocked_on_trigger": len(co_blocked),
        "co_blocked_samples": co_blocked[:8],
        "triggered_samples": triggered[:15],
        "avg_ret_5d_triggered": round(mean(ret_trig), 4) if ret_trig else None,
        "acceptance_criteria": acceptance,
        "recommendation": recommendation,
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"stale_momentum_policy_shadow_{tag}.json"
    txt_path = REPORT_DIR / f"stale_momentum_policy_shadow_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "STALE Momentum Continuation — Phase 2.12B Shadow",
        f"Cohort: {len(eval_rows)} evaluable | momentum triggered: {len(triggered)}",
        f"Clean on triggered: {clean_trig} | Losers: {losers_trig}",
        f"Shadow actionable (no co-blockers): {len(shadow_actionable)}",
        f"Avg ret_5d (triggered): {report['avg_ret_5d_triggered']}",
        "",
        "=== Top Triggered ===",
    ]
    for t in triggered[:10]:
        lines.append(
            f"  {t['date']} {t['symbol']:<6} D{t['day']} ret5={t.get('ret_5d')} "
            f"rr={t.get('rr')} clean={t.get('clean')} veto={t.get('veto')}"
        )
    lines += ["", "=== Acceptance ==="]
    for k, v in acceptance.items():
        mark = "—" if v is None else ("✅" if v else "❌")
        lines.append(f"  {mark} {k}: {v}")
    lines += [
        "",
        f"Recommendation: {recommendation}",
        f"STALE_MOMENTUM_POLICY={STALE_MOMENTUM_POLICY}",
        "Half-size (0.5x) on momentum path — NO PRODUCTION PATCH.",
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
