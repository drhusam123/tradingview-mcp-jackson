#!/usr/bin/env python3
"""MED-0/1 acceptance gate — shadow invariants + research feed health."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_1_acceptance_last.json"

VERDICTS = [
    "PASS_MED_0_1_RESEARCH_FEED",
    "RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED",
    "RESEARCH_EDGE_MONITOR_ONLY",
    "FAIL_NO_INCREMENTAL_EDGE",
    "FAIL_LEAKAGE_RISK",
    "FAIL_SAMPLE_QUALITY",
]


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(params: dict | None = None) -> dict:
    params = params or {}
    checks: list[dict] = []
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else None

    tables = [
        "med_daily_scores", "med_research_feed", "med_conditional_edge_tables",
        "med_distribution_shift_daily", "med_path_profiles", "med_failure_patterns",
        "med_sample_quality",
    ]
    for t in tables:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        checks.append(_check(f"table:{t}", bool(exists)))

    daily_n = feed_n = 0
    if trade_date:
        daily_n = db.execute(
            "SELECT COUNT(*) n FROM med_daily_scores WHERE trade_date=?", (trade_date,)
        ).fetchone()["n"]
        feed_n = db.execute(
            "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=?", (trade_date,)
        ).fetchone()["n"]
        checks.append(_check("med_daily_scores_populated", daily_n > 0, f"rows={daily_n}"))
        checks.append(_check("med_research_feed_populated", feed_n > 0, f"rows={feed_n}"))

        client_leak = db.execute(
            "SELECT COUNT(*) n FROM med_daily_scores WHERE trade_date=? AND client_path_allowed=1",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("client_path_allowed_zero", client_leak == 0, f"leaks={client_leak}"))

        max_boost = db.execute(
            "SELECT MAX(hypothetical_boost) m FROM med_research_feed WHERE trade_date=?",
            (trade_date,),
        ).fetchone()["m"] or 0
        checks.append(_check("hypothetical_boost_only", float(max_boost) <= 3.0, f"max={max_boost}"))

        fail_sim = db.execute(
            "SELECT COUNT(*) n FROM med_failure_patterns WHERE trade_date=? AND failure_similarity IS NOT NULL",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("failure_similarity_calculated", fail_sim > 0, f"rows={fail_sim}"))

        dist_n = db.execute(
            "SELECT COUNT(*) n FROM med_distribution_shift_daily WHERE trade_date=?",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("distribution_shift_calculated", dist_n > 0, f"rows={dist_n}"))

        path_n = db.execute(
            "SELECT COUNT(*) n FROM med_path_profiles WHERE trade_date=?",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("path_profile_calculated", path_n > 0, f"rows={path_n}"))

        sq_n = db.execute(
            "SELECT COUNT(*) n FROM med_sample_quality WHERE asof_date=?",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("sample_quality_calculated", sq_n > 0, f"rows={sq_n}"))

    replay = DATA / "med_replay_audit_last.json"
    static_only = True
    if replay.exists():
        rep = json.loads(replay.read_text(encoding="utf-8"))
        static_only = all(
            (v or {}).get("static_only", True)
            for v in (rep.get("results") or {}).values()
        ) if rep.get("results") else True
        checks.append(_check("not_static_only", not static_only, "walk-forward modes run"))

    summary = DATA / "med_0_1_run_summary.json"
    if summary.exists():
        s = json.loads(summary.read_text(encoding="utf-8"))
        inv = s.get("invariants") or {}
        checks.append(_check("MED_SHADOW", inv.get("MED_SHADOW") == "1", str(inv.get("MED_SHADOW"))))
        checks.append(_check("MED_CLIENT_SIGNAL", inv.get("MED_CLIENT_SIGNAL") == "0", str(inv.get("MED_CLIENT_SIGNAL"))))

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    all_ok = passed == total

    verdict = "FAIL_NO_INCREMENTAL_EDGE"
    if not all_ok:
        if any(c["name"] == "client_path_allowed_zero" and not c["ok"] for c in checks):
            verdict = "FAIL_LEAKAGE_RISK"
        elif any(c["name"] in ("sample_quality_calculated", "failure_similarity_calculated") and not c["ok"] for c in checks):
            verdict = "FAIL_SAMPLE_QUALITY"
    elif replay.exists():
        rep = json.loads(replay.read_text(encoding="utf-8"))
        lift = rep.get("incremental_lift") or {}
        med_lift = lift.get("MED_LRE_MDE_vs_LRE_MDE", {})
        if med_lift.get("pf_100_delta", 0) > 0.05:
            verdict = "PASS_MED_0_1_RESEARCH_FEED"
        elif med_lift.get("pf_100_delta", 0) > 0:
            verdict = "RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED"
        else:
            verdict = "RESEARCH_EDGE_MONITOR_ONLY"

    out = {
        "success": all_ok,
        "verdict": verdict,
        "trade_date": trade_date,
        "checks_passed": passed,
        "checks_total": total,
        "checks": checks,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
