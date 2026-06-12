#!/usr/bin/env python3
"""Post-score actionable funnel summary + gate blocker histogram."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "egx_trading.db"


def simulate(params: dict | None = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    date = params.get("date")
    if not date:
        row = conn.execute(
            "SELECT MAX(trade_date) d FROM final_signals WHERE trade_date NOT LIKE '2099-%'"
        ).fetchone()
        date = row["d"] if row else None
    if not date:
        conn.close()
        return {"success": False, "error": "NO_DATE"}

    tot = conn.execute(
        "SELECT COUNT(*) n, SUM(actionable) act FROM final_signals WHERE trade_date=?",
        (date,),
    ).fetchone()
    blockers = Counter()
    gate_tiers = Counter()
    for r in conn.execute(
        "SELECT first_blocking_gate g, quality_gate_passed qg, final_edge_passed fe "
        "FROM gate_audit_snapshots WHERE signal_date=?",
        (date,),
    ):
        qg = bool(r["qg"])
        fe = bool(r["fe"])
        if not qg:
            gate_tiers["quality_gate"] += 1
            if r["g"]:
                blockers[r["g"]] += 1
        elif not fe:
            gate_tiers["final_edge"] += 1
            if r["g"]:
                blockers[r["g"]] += 1
        else:
            gate_tiers["post_edge"] += 1

    arb_vetoed = conn.execute(
        "SELECT COUNT(*) n FROM final_signals WHERE trade_date=? AND actionable=0 "
        "AND veto_reason LIKE 'ARBITRATION%'",
        (date,),
    ).fetchone()["n"]

    actionable_rows = conn.execute(
        "SELECT symbol, score, source_ml, r_ratio, veto_reason FROM final_signals "
        "WHERE trade_date=? AND actionable=1 ORDER BY score DESC LIMIT 20",
        (date,),
    ).fetchall()

    drift = conn.execute(
        "SELECT feature_value v FROM feature_store WHERE symbol='MARKET' "
        "AND feature_name='mladv_drift_throttle' ORDER BY feature_date DESC LIMIT 1"
    ).fetchone()
    ml_thr = conn.execute(
        "SELECT param_value v FROM adaptive_gate_params "
        "WHERE param_name='ml_threshold_BULL' ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    qg_pass = conn.execute(
        "SELECT COUNT(*) n FROM gate_audit_snapshots WHERE signal_date=? AND quality_gate_passed=1",
        (date,),
    ).fetchone()["n"]
    fe_pass = conn.execute(
        "SELECT COUNT(*) n FROM gate_audit_snapshots WHERE signal_date=? AND final_edge_passed=1",
        (date,),
    ).fetchone()["n"]

    conn.close()

    return {
        "success": True,
        "date": date,
        "total_scored": tot["n"],
        "actionable": tot["act"] or 0,
        "actionable_pct": round(100 * (tot["act"] or 0) / max(tot["n"], 1), 2),
        "gate_decision": {
            "quality_gate_blocked": gate_tiers.get("quality_gate", 0),
            "final_edge_blocked": gate_tiers.get("final_edge", 0),
            "quality_gate_passed": qg_pass,
            "final_edge_passed": fe_pass,
            "arbitration_vetoed": arb_vetoed,
        },
        "top_blockers": blockers.most_common(15),
        "actionable_symbols": [dict(r) for r in actionable_rows],
        "drift_throttle": float(drift["v"]) if drift else None,
        "ml_threshold_bull": float(ml_thr["v"]) if ml_thr else None,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulate"
    p = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if cmd == "simulate":
        print(json.dumps(simulate(p), indent=2))
    else:
        print(json.dumps({"success": False, "error": f"unknown {cmd}"}))
