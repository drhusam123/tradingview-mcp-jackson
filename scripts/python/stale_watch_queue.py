#!/usr/bin/env python3
"""
stale_watch_queue — Phase 2.12C shadow WATCH_QUEUE fields (NOT production)

Combines STALE pullback reentry + momentum continuation into one watch-queue record
persisted in gate_audit_snapshots during score_all.

Usage:
    python3 scripts/python/stale_watch_queue.py audit
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"

STALE_WATCH_POLICY = "KEEP_SHADOW_REPORTING"


def safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def build_stale_watch_shadow_fields(
    *,
    risk_bucket=None,
    entry=None,
    stop=None,
    target=None,
    close=None,
    signal_low=None,
    ues=0.0,
    ml_score=0.0,
    vol_ratio=None,
    forward_bars=None,
) -> dict:
    """Unified shadow fields for STALE watch queue (observe only)."""
    empty = {
        "shadow_stale_watch_path": None,
        "shadow_stale_pullback_triggered": 0,
        "shadow_stale_momentum_triggered": 0,
        "shadow_stale_momentum_day": None,
        "shadow_stale_momentum_entry": None,
        "shadow_stale_momentum_stop": None,
        "shadow_stale_momentum_rr": None,
        "shadow_stale_watch_position_mult": None,
        "shadow_stale_watch_reason": None,
        "shadow_stale_would_watch_queue": 0,
    }
    if risk_bucket != "STALE_TARGET":
        return empty

    from stale_target_reentry_policy import evaluate_reentry_trigger
    from stale_momentum_policy import evaluate_momentum_continuation

    pull = evaluate_reentry_trigger(
        entry=entry, stop=stop, target=target, close=close,
        ues=ues, ml_score=ml_score, vol_ratio=vol_ratio,
        forward_bars=forward_bars,
    )
    mom = evaluate_momentum_continuation(
        entry=entry, stop=stop, target=target, close=close,
        signal_low=signal_low, ues=ues, ml_score=ml_score,
        forward_bars=forward_bars,
    )

    path = "WATCH_REENTRY"
    pos_mult = 0.75
    reason = pull.get("reason") or "STALE_AT_SIGNAL"
    would_queue = 1

    if mom.get("momentum_triggered"):
        path = "MOMENTUM_CANDIDATE"
        pos_mult = mom.get("position_multiplier", 0.5)
        reason = mom.get("reason")
    elif pull.get("reentry_triggered"):
        path = "REENTRY_CANDIDATE"
        pos_mult = 1.0
        reason = pull.get("reason")
    elif mom.get("applies") and mom.get("reason") == "MOMENTUM_NO_TRIGGER":
        path = "WATCH_MOMENTUM"
        pos_mult = 0.5
        reason = "STALE_MOMENTUM_WATCH"
    elif pull.get("applies"):
        path = "WATCH_REENTRY"
        pos_mult = 0.75
        reason = pull.get("reason") or "STALE_PULLBACK_WATCH"

    if not pull.get("applies") and not mom.get("applies"):
        would_queue = 0

    return {
        "shadow_stale_watch_path": path,
        "shadow_stale_pullback_triggered": 1 if pull.get("reentry_triggered") else 0,
        "shadow_stale_momentum_triggered": 1 if mom.get("momentum_triggered") else 0,
        "shadow_stale_momentum_day": mom.get("trigger_day"),
        "shadow_stale_momentum_entry": mom.get("continuation_entry"),
        "shadow_stale_momentum_stop": mom.get("continuation_stop"),
        "shadow_stale_momentum_rr": mom.get("continuation_rr"),
        "shadow_stale_watch_position_mult": pos_mult,
        "shadow_stale_watch_reason": reason,
        "shadow_stale_would_watch_queue": would_queue,
    }


def cmd_audit(params: dict | None = None):
    params = params or {}
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-05")

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT signal_date, symbol, ues, ml_score, veto_reason, actionable,
               shadow_stale_watch_path, shadow_stale_momentum_triggered,
               shadow_stale_pullback_triggered, shadow_stale_momentum_rr,
               shadow_stale_watch_reason, ret_5d, outcomes_filled
        FROM gate_audit_snapshots
        WHERE signal_date>=? AND signal_date<=?
          AND shadow_stale_would_watch_queue=1
        ORDER BY signal_date, symbol
    """, (start, end)).fetchall()
    conn.close()

    paths = Counter(r["shadow_stale_watch_path"] for r in rows)
    momentum = [dict(r) for r in rows if int(r["shadow_stale_momentum_triggered"] or 0)]
    eval_mom = [r for r in momentum if int(r["outcomes_filled"] or 0)]

    report = {
        "success": True,
        "phase": "2.12C_watch_queue",
        "policy": STALE_WATCH_POLICY,
        "cohort": f"{start}→{end}",
        "n_watch_queue": len(rows),
        "by_path": dict(paths),
        "momentum_triggered": len(momentum),
        "momentum_evaluable": len(eval_mom),
        "momentum_samples": [
            {
                "date": r["signal_date"], "symbol": r["symbol"],
                "rr": r["shadow_stale_momentum_rr"], "ret_5d": r["ret_5d"],
                "veto": r["veto_reason"],
            }
            for r in eval_mom[:12]
        ],
        "recommendation": "KEEP_SHADOW_REPORTING",
    }

    tag = f"{start}_{end}"
    json_path = REPORT_DIR / f"stale_watch_queue_{tag}.json"
    txt_path = REPORT_DIR / f"stale_watch_queue_{tag}.txt"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "STALE WATCH_QUEUE Shadow — Phase 2.12C",
        f"Period: {start} → {end} | queue rows: {len(rows)}",
        f"Policy: {STALE_WATCH_POLICY}",
        "",
        "=== By Path ===",
    ]
    for k, v in paths.most_common():
        lines.append(f"  {k:<22} {v}")
    lines += [
        "",
        f"Momentum triggered: {len(momentum)} | evaluable: {len(eval_mom)}",
        "",
        "=== Momentum Samples ===",
    ]
    for s in report["momentum_samples"]:
        lines.append(
            f"  {s['date']} {s['symbol']:<6} rr={s['rr']} ret5={s['ret_5d']} veto={s['veto']}"
        )
    lines += ["", f"Recommendation: {report['recommendation']}"]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {"audit": cmd_audit}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(COMMANDS.keys())}))
        sys.exit(1)
    result = handler(params)
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
