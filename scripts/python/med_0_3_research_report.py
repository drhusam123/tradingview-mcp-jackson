#!/usr/bin/env python3
"""MED-0.3 — discovery report + false-edge feed for system enrichment."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_3_discovery_report.json"
FALSE_EDGE = DATA / "med_false_edge_feed_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))
from med_0_3_discovery_miner import export_false_edge_symbols, mine_egx_med


def run(params: dict | None = None) -> dict:
    params = params or {}
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    discoveries = []
    if trade_date:
        discoveries = [
            dict(r) for r in db.execute(
                """
                SELECT d.symbol, d.sector, d.med_score, d.med_bucket, d.p_cond_20d_10,
                       d.expected_return_20d, d.regime_fit, d.sample_quality,
                       a.analogue_p_tail_20_10, d.hypothetical_boost, d.condition_key
                FROM med_daily_scores d
                LEFT JOIN med_analogue_scores_daily a
                  ON d.trade_date=a.trade_date AND d.symbol=a.symbol
                WHERE d.trade_date=?
                  AND d.med_bucket IN (
                    'MED_HIGH_CONVICTION_RESEARCH', 'MED_POSITIVE_EXPECTANCY', 'MED_MONITOR'
                  )
                ORDER BY d.med_score DESC LIMIT 30
                """,
                (trade_date,),
            ).fetchall()
        ]

    analogue_top = []
    if trade_date:
        analogue_top = [
            dict(r) for r in db.execute(
                """
                SELECT a.symbol, a.analogue_p_tail_20_10, d.med_score, d.med_bucket
                FROM med_analogue_scores_daily a
                JOIN med_daily_scores d ON a.trade_date=d.trade_date AND a.symbol=d.symbol
                WHERE a.trade_date=? ORDER BY a.analogue_p_tail_20_10 DESC LIMIT 15
                """,
                (trade_date,),
            ).fetchall()
        ]

    edges = [
        dict(r) for r in db.execute(
            """
            SELECT condition_key, n, hit_rate, expectancy, sample_quality
            FROM med_conditional_edge_tables
            WHERE horizon=20 AND abs(threshold-0.10)<1e-6 AND n>=30
            ORDER BY expectancy DESC LIMIT 10
            """
        ).fetchall()
    ]

    buckets = {}
    if trade_date:
        buckets = dict(db.execute(
            "SELECT med_bucket, COUNT(*) c FROM med_daily_scores WHERE trade_date=? GROUP BY 1",
            (trade_date,),
        ).fetchall())

    atoms = mine_egx_med(db)
    false_edge = export_false_edge_symbols(db, trade_date) if trade_date else {}

    replay = {}
    replay_path = DATA / "med_replay_audit_last.json"
    if replay_path.exists():
        replay = json.loads(replay_path.read_text(encoding="utf-8")).get("incremental_lift", {})

    fwd = {}
    fwd_path = DATA / "med_forward_shadow_last.json"
    if fwd_path.exists():
        fwd = json.loads(fwd_path.read_text(encoding="utf-8"))

    report = {
        "success": True,
        "phase": "MED-0.4",
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "invariants": {
            "shadow_only": True,
            "client_path_allowed": False,
            "feeds_discovery_fabric": True,
            "med_feed_boost": False,
            "med_feed_penalize": True,
            "med_hc_active": True,
            "does_not_change_actionable": True,
        },
        "buckets": buckets,
        "discoveries_top": discoveries,
        "analogue_top": analogue_top,
        "conditional_edges_top": edges,
        "discovery_atoms": len(atoms),
        "atoms_preview": atoms[:6],
        "false_edge_feed": false_edge,
        "oos_lift": replay.get("MED_LRE_vs_LRE"),
        "forward_shadow": {
            "oos_closed": fwd.get("oos_closed"),
            "oos_median_return": fwd.get("oos_median_return"),
            "live_closed": fwd.get("live_closed_trades"),
        },
    }

    OUTPUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    FALSE_EDGE.write_text(json.dumps(false_edge, indent=2, default=str), encoding="utf-8")
    (DATA / "med_discovery_atoms_last.json").write_text(
        json.dumps({"atoms": atoms, "count": len(atoms)}, indent=2, default=str),
        encoding="utf-8",
    )
    db.close()
    return report


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
