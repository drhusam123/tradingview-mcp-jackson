#!/usr/bin/env python3
"""MED-0/1 status — health snapshot + graduation tracker."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_1_status_last.json"


def run(params: dict | None = None) -> dict:
    params = params or {}
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else None

    status = {
        "trade_date": trade_date,
        "shadow_only": True,
        "client_path_allowed": False,
        "graduation_requirements": {
            "oos_closed_trades": "40+",
            "pf_100": ">= 1.3",
            "median_return": "> 0",
            "top10_dominance": "< 35%",
            "sector_concentration": "< 35%",
        },
        "graduation_met": False,
    }

    if trade_date:
        rows = db.execute(
            "SELECT symbol, med_score, med_bucket, hypothetical_boost FROM med_daily_scores WHERE trade_date=? ORDER BY med_score DESC",
            (trade_date,),
        ).fetchall()
        buckets = Counter(r["med_bucket"] for r in rows)
        status["daily_rows"] = len(rows)
        status["buckets"] = dict(buckets)
        status["top20"] = [
            {"symbol": r["symbol"], "med_score": r["med_score"], "bucket": r["med_bucket"]}
            for r in rows[:20]
        ]
        status["max_hypothetical_boost"] = max((r["hypothetical_boost"] or 0) for r in rows) if rows else 0

        edge_n = db.execute(
            "SELECT COUNT(*) n FROM med_conditional_edge_tables WHERE asof_date=?", (trade_date,)
        ).fetchone()["n"]
        status["edge_rows"] = edge_n

    summary = DATA / "med_0_1_run_summary.json"
    if summary.exists():
        status["run_summary"] = json.loads(summary.read_text(encoding="utf-8"))

    acceptance = DATA / "med_0_1_acceptance_last.json"
    if acceptance.exists():
        status["acceptance_verdict"] = json.loads(acceptance.read_text(encoding="utf-8")).get("verdict")

    status["run_at"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    return status


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
