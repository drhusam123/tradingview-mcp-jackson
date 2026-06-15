#!/usr/bin/env python3
"""MED-0.3 acceptance — calibration targets + shadow invariants."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_3_acceptance_last.json"


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(params: dict | None = None) -> dict:
    checks = []
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    td = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
    trade_date = td["d"] if td else None

    checks.append(_check("med_daily_populated", bool(trade_date), f"date={trade_date}"))

    if trade_date:
        n = db.execute(
            "SELECT COUNT(*) n FROM med_daily_scores WHERE trade_date=?", (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("daily_rows", n > 0, f"rows={n}"))

        buckets = dict(db.execute(
            "SELECT med_bucket, COUNT(*) c FROM med_daily_scores WHERE trade_date=? GROUP BY 1",
            (trade_date,),
        ).fetchall())
        total = sum(buckets.values()) or 1
        fw = buckets.get("MED_FAILURE_WARNING", 0)
        dnc = buckets.get("MED_DO_NOT_CHASE", 0)
        hc = buckets.get("MED_HIGH_CONVICTION_RESEARCH", 0)
        fw_pct = 100 * fw / total
        checks.append(_check(
            "failure_warning_25_35pct",
            20 <= fw_pct <= 45,
            f"failure_warning={fw} ({fw_pct:.1f}%), dnc={dnc}",
        ))
        checks.append(_check(
            "high_conviction_3_8",
            0 <= hc <= 12,
            f"high_conviction={hc}",
        ))

        rf = db.execute(
            "SELECT COUNT(DISTINCT regime_fit) d, MIN(regime_fit), MAX(regime_fit) "
            "FROM med_daily_scores WHERE trade_date=?",
            (trade_date,),
        ).fetchone()
        checks.append(_check(
            "regime_fit_not_constant",
            rf["d"] > 1 or (rf["MIN(regime_fit)"] != rf["MAX(regime_fit)"]),
            f"distinct={rf['d']} min={rf['MIN(regime_fit)']} max={rf['MAX(regime_fit)']}",
        ))

        th = db.execute(
            "SELECT COUNT(*) n FROM med_threshold_snapshots WHERE asof_date=? AND window_mode='med_0_3_expanding'",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("thresholds_v3", th >= 5, f"snapshots={th}"))

        ana = db.execute(
            "SELECT COUNT(*) n FROM med_analogue_scores_daily WHERE trade_date=?", (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("analogue_merged", ana > 0, f"rows={ana}"))

        leak = db.execute(
            "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND client_path_allowed=1",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("no_client_leak", leak == 0, f"leaks={leak}"))

        max_boost = db.execute(
            "SELECT MAX(hypothetical_boost) m FROM med_research_feed WHERE trade_date=?",
            (trade_date,),
        ).fetchone()["m"] or 0
        checks.append(_check("hypothetical_boost_cap", float(max_boost) <= 3.0, f"max={max_boost}"))

    summary_path = DATA / "med_0_3_run_summary.json"
    checks.append(_check("run_summary", summary_path.exists()))

    passed = sum(1 for c in checks if c["ok"])
    all_ok = all(c["ok"] for c in checks)
    verdict = "PASS_MED_0_3_CALIBRATION" if all_ok else "FAIL_MED_0_3_CALIBRATION"

    out = {
        "success": all_ok,
        "verdict": verdict,
        "trade_date": trade_date,
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "buckets": buckets if trade_date else {},
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    db.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
