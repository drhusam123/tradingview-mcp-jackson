#!/usr/bin/env python3
"""MED-2 status — graduation tracker + analogue + forward ledger."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_2_status_last.json"


def run(params: dict | None = None) -> dict:
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date") if params else None
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else None

    status = {
        "phase": "MED-2",
        "trade_date": trade_date,
        "shadow_only": True,
        "client_path_allowed": False,
    }

    if trade_date:
        top = db.execute(
            """
            SELECT d.symbol, d.med_score, d.med_bucket, a.analogue_p_tail_20_10, a.analogue_confidence
            FROM med_daily_scores d
            LEFT JOIN med_analogue_scores_daily a ON d.trade_date=a.trade_date AND d.symbol=a.symbol
            WHERE d.trade_date=? ORDER BY d.med_score DESC LIMIT 15
            """,
            (trade_date,),
        ).fetchall()
        status["top15_with_analogue"] = [dict(r) for r in top]

    fwd = DATA / "med_forward_shadow_last.json"
    if fwd.exists():
        status["forward_shadow"] = json.loads(fwd.read_text(encoding="utf-8"))

    ana = DATA / "med_analogue_scores_last.json"
    if ana.exists():
        status["analogue"] = json.loads(ana.read_text(encoding="utf-8"))

    grad = status.get("forward_shadow", {})
    status["graduation"] = {
        "live_closed": grad.get("live_closed_trades", 0),
        "required_closed": 40,
        "live_pf_100": grad.get("live_pf_100"),
        "oos_research_closed": grad.get("oos_closed", 0),
        "oos_pf_100": grad.get("oos_pf_100"),
        "graduation_met": grad.get("graduation_met", False),
    }

    status["run_at"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    return status


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
