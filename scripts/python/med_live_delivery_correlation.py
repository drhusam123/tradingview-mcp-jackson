#!/usr/bin/env python3
"""
Phase 17 — correlate Telegram delivery with MED opp delta on delivered symbols.

Monitoring only — no Telegram or promotion path changes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_live_delivery_correlation_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_opp_bridge import apply_med_research_boost, apply_med_research_penalty, load_med_feed_map


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS med_delivery_correlation_ledger (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        send_success INTEGER DEFAULT 0,
        actionable INTEGER DEFAULT 0,
        base_opp_score REAL,
        penalize_pts REAL DEFAULT 0,
        boost_pts REAL DEFAULT 0,
        active_track_pts REAL DEFAULT 0,
        med_bucket TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    """)


def _delivered_symbols(conn: sqlite3.Connection, trade_date: str) -> List[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT symbol FROM notification_delivery_audit
        WHERE signal_date=? AND send_success=1 AND dry_run=0
          AND symbol IS NOT NULL AND symbol != ''
        """,
        (trade_date,),
    ).fetchall()
    syms = [r[0] for r in rows]
    if syms:
        return syms
    return [
        r[0]
        for r in conn.execute(
            """
            SELECT symbol FROM final_signals
            WHERE trade_date=? AND actionable=1
              AND (veto_reason IS NULL OR veto_reason='')
            """,
            (trade_date,),
        ).fetchall()
    ]


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute(
            "SELECT MAX(signal_date) d FROM notification_delivery_audit WHERE send_success=1"
        ).fetchone()
        trade_date = row["d"] if row and row["d"] else None
        if not trade_date:
            row = conn.execute("SELECT MAX(trade_date) d FROM final_signals").fetchone()
            trade_date = row["d"] if row else None

    feed_map = load_med_feed_map(conn, trade_date) if trade_date else {}
    symbols = _delivered_symbols(conn, trade_date) if trade_date else []

    boost_on = os.environ.get("MED_FEED_BOOST", "0") == "1"
    penalize_on = os.environ.get("MED_FEED_PENALIZE", "1") == "1"
    client_on = os.environ.get("MED_CLIENT_SIGNAL", "0") == "1"

    rows: List[dict] = []
    for sym in symbols:
        feed = feed_map.get(sym)
        pen, _, _ = apply_med_research_penalty(sym, feed)
        boost, _, _ = apply_med_research_boost(sym, feed)
        if boost_on:
            active = boost
            track = "boost"
        elif penalize_on:
            active = pen
            track = "penalize"
        else:
            active = 0.0
            track = "off"

        base = conn.execute(
            """
            SELECT opportunity_score FROM opportunity_score_v2
            WHERE trade_date=? AND symbol=? LIMIT 1
            """,
            (trade_date, sym),
        ).fetchone()
        base_score = float(base[0]) if base else None

        row = {
            "symbol": sym,
            "med_bucket": feed.get("med_bucket") if feed else None,
            "base_opp_score": base_score,
            "penalize_pts": round(pen, 2),
            "boost_pts": round(boost, 2),
            "active_track_pts": round(active, 2),
            "active_track": track,
            "delta_boost_vs_pen": round(boost - pen, 2),
        }
        rows.append(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO med_delivery_correlation_ledger
            (trade_date, symbol, send_success, actionable, base_opp_score,
             penalize_pts, boost_pts, active_track_pts, med_bucket)
            VALUES (?,?,1,1,?,?,?,?,?)
            """,
            (
                trade_date, sym, base_score, pen, boost, active,
                row["med_bucket"],
            ),
        )
    conn.commit()

    avg_active = round(sum(r["active_track_pts"] for r in rows) / len(rows), 2) if rows else 0.0
    avg_delta = round(sum(r["delta_boost_vs_pen"] for r in rows) / len(rows), 2) if rows else 0.0
    would_flip = sum(1 for r in rows if r["delta_boost_vs_pen"] > 0 and not boost_on)

    summary = (
        f"{len(rows)} delivered | track={('boost' if boost_on else 'penalize')} "
        f"| avg active {avg_active:+.2f} | avg Δ boost-vs-pen {avg_delta:+.2f}"
    )

    payload = {
        "success": True,
        "phase": "17_delivery_correlation",
        "trade_date": trade_date,
        "MED_CLIENT_SIGNAL": os.environ.get("MED_CLIENT_SIGNAL", "0"),
        "MED_FEED_BOOST": os.environ.get("MED_FEED_BOOST", "0"),
        "MED_FEED_PENALIZE": os.environ.get("MED_FEED_PENALIZE", "1"),
        "symbols_delivered": len(rows),
        "avg_active_pts": avg_active,
        "avg_delta_boost_vs_pen": avg_delta,
        "would_benefit_from_boost": would_flip,
        "summary": summary,
        "rows": rows,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
