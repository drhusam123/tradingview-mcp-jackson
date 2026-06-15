#!/usr/bin/env python3
"""MED manifest for discovery fabric (shadow invariants)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
MANIFEST = DATA / "discovery_med_manifest.json"

MED_MANIFEST_INVARIANTS = {
    "MED_SHADOW": "1",
    "MED_CLIENT_SIGNAL": "0",
    "MED_OPP_BOOST": "0",
    "MED_FEED_BOOST": "0",
    "MED_FEED_PENALIZE": "1",
    "MED_POSITION_SIZING_LIVE": "0",
    "client_path_allowed": False,
    "research_only": True,
    "shadow_only": True,
    "no_veto": True,
    "no_actionable_change": True,
    "no_prioritizer_boost": True,
}


def write_manifest(trade_date: str | None = None) -> dict:
    db = sqlite3.connect(str(DB_PATH), timeout=60)
    db.row_factory = sqlite3.Row
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    feed_n = ana_n = edge_n = 0
    buckets = {}
    if trade_date:
        feed_n = db.execute(
            "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=?", (trade_date,),
        ).fetchone()["n"]
        ana_n = db.execute(
            "SELECT COUNT(*) n FROM med_analogue_scores_daily WHERE trade_date=?", (trade_date,),
        ).fetchone()["n"]
        edge_n = db.execute(
            "SELECT COUNT(*) n FROM med_conditional_edge_tables WHERE asof_date=?", (trade_date,),
        ).fetchone()["n"]
        for r in db.execute(
            "SELECT med_bucket, COUNT(*) n FROM med_daily_scores WHERE trade_date=? GROUP BY med_bucket",
            (trade_date,),
        ):
            buckets[r["med_bucket"]] = r["n"]
    db.close()

    doc = {
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": "MED-0.3",
        "trade_date": trade_date,
        "invariants": MED_MANIFEST_INVARIANTS,
        "tables": [
            "med_daily_scores", "med_research_feed", "med_conditional_edge_tables",
            "med_analogue_scores_daily", "med_forward_shadow_ledger",
        ],
        "counts": {"feed": feed_n, "analogue": ana_n, "edges": edge_n, "buckets": buckets},
        "feeds": ["discovery_fabric"],
        "does_not_feed": ["final_signals", "client_promotion", "telegram", "opportunity_score_v2"],
    }
    MANIFEST.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {"written": str(MANIFEST.relative_to(ROOT)), "trade_date": trade_date}


if __name__ == "__main__":
    import sys
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(write_manifest(p.get("trade_date")), indent=2))
