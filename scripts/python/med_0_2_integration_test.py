#!/usr/bin/env python3
"""MED integration test — actionable + opp_v2 unchanged (shadow only)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_integration_test_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _actionable_snapshot(db: sqlite3.Connection, trade_date: str) -> dict:
    rows = db.execute(
        "SELECT symbol, actionable FROM final_signals WHERE trade_date=?", (trade_date,),
    ).fetchall()
    actionable = {r["symbol"] for r in rows if int(r["actionable"] or 0) == 1}
    return {"count": len(actionable), "symbols": sorted(actionable)}


def _opp_snapshot(db: sqlite3.Connection, trade_date: str) -> dict:
    rows = db.execute(
        "SELECT symbol, opportunity_score FROM opportunity_score_v2 WHERE trade_date=?",
        (trade_date,),
    ).fetchall()
    return {r["symbol"]: float(r["opportunity_score"] or 0) for r in rows}


def run(params: dict | None = None) -> dict:
    params = params or {}
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM opportunity_score_v2").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        db.close()
        return {"success": False, "error": "no_trade_date"}

    os.environ["MED_OPP_BOOST"] = "0"
    os.environ["MED_FEED_BOOST"] = "0"

    before_actionable = _actionable_snapshot(db, trade_date)
    before_opp = _opp_snapshot(db, trade_date)

    from med_0_2_manifest import write_manifest
    write_manifest(trade_date)

    after_actionable = _actionable_snapshot(db, trade_date)
    after_opp = _opp_snapshot(db, trade_date)

    client_leak = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND client_path_allowed=1",
        (trade_date,),
    ).fetchone()["n"]
    max_boost = db.execute(
        "SELECT MAX(hypothetical_boost) m FROM med_research_feed WHERE trade_date=?",
        (trade_date,),
    ).fetchone()["m"] or 0

    actionable_unchanged = before_actionable == after_actionable
    opp_unchanged = before_opp == after_opp

    verdict = "PASS_MED_INTEGRATION" if (
        actionable_unchanged and opp_unchanged and client_leak == 0 and float(max_boost) <= 3.0
    ) else "FAIL_MED_INTEGRATION"

    out = {
        "success": actionable_unchanged and opp_unchanged,
        "verdict": verdict,
        "trade_date": trade_date,
        "before_actionable_count": before_actionable["count"],
        "after_actionable_count": after_actionable["count"],
        "actionable_unchanged": actionable_unchanged,
        "opp_unchanged": opp_unchanged,
        "client_path_leaks": client_leak,
        "max_hypothetical_boost": max_boost,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    db.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
