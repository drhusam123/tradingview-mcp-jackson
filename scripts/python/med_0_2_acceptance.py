#!/usr/bin/env python3
"""MED-2 acceptance — analogue + forward shadow + invariants."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_2_acceptance_last.json"


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(params: dict | None = None) -> dict:
    checks = []
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    for t in ("med_analogue_scores_daily", "med_threshold_snapshots", "med_forward_shadow_ledger"):
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        checks.append(_check(f"table:{t}", bool(exists)))

    trade_date = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
    td = trade_date["d"] if trade_date else None

    ana_n = db.execute("SELECT COUNT(*) n FROM med_analogue_scores_daily").fetchone()["n"] if td else 0
    checks.append(_check("analogue_populated", ana_n > 0, f"rows={ana_n}"))

    th_n = db.execute("SELECT COUNT(*) n FROM med_threshold_snapshots").fetchone()["n"]
    checks.append(_check("thresholds_populated", th_n > 0, f"rows={th_n}"))

    oos_n = db.execute(
        "SELECT COUNT(*) n FROM med_forward_shadow_ledger WHERE ledger_mode='research_oos'"
    ).fetchone()["n"]
    checks.append(_check("oos_research_ledger", oos_n > 0, f"rows={oos_n}"))

    leak = db.execute(
        "SELECT COUNT(*) n FROM med_forward_shadow_ledger WHERE client_path_allowed=1"
    ).fetchone()["n"]
    checks.append(_check("no_client_leak", leak == 0, f"leaks={leak}"))

    fwd = DATA / "med_forward_shadow_last.json"
    live_ok = True
    if fwd.exists():
        f = json.loads(fwd.read_text(encoding="utf-8"))
        live_ok = f.get("live_skipped") is True or f.get("new_live_entries", 0) >= 0
        checks.append(_check("forward_shadow_json", f.get("success") is True))
        checks.append(_check("live_forward_gated", f.get("live_skipped") or f.get("forward_start") == "2026-06-12"))

    passed = sum(1 for c in checks if c["ok"])
    verdict = "PASS_MED_2_RESEARCH_LAYER"
    if not all(c["ok"] for c in checks):
        verdict = "FAIL_MED_2_INCOMPLETE"
    elif oos_n < 100:
        verdict = "MED_2_MONITOR_OOS_LEDGER_THIN"

    out = {
        "success": all(c["ok"] for c in checks),
        "verdict": verdict,
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
