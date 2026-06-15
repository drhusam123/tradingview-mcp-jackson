#!/usr/bin/env python3
"""
Phase 15 — MED opportunity delta monitor (penalize vs boost vs current env).

Tracks score impact of MED research feed on top opportunity symbols.
No Telegram change — monitoring only.
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
OUTPUT = DATA / "med_opp_delta_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_opp_bridge import (
    apply_med_research_boost,
    apply_med_research_penalty,
    load_med_feed_map,
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS med_opp_delta_ledger (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        base_opp_score REAL,
        penalize_pts REAL DEFAULT 0,
        boost_pts REAL DEFAULT 0,
        net_delta_boost_vs_pen REAL DEFAULT 0,
        med_bucket TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_live_session_ledger (
        trade_date TEXT PRIMARY KEY,
        med_client_signal INTEGER DEFAULT 0,
        symbols_monitored INTEGER DEFAULT 0,
        avg_delta REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)


def _top_symbols(conn: sqlite3.Connection, trade_date: str, limit: int = 15) -> List[str]:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunity_score_v2'"
    ).fetchone():
        rows = conn.execute(
            """
            SELECT symbol FROM opportunity_score_v2
            WHERE trade_date=? ORDER BY opportunity_score DESC LIMIT ?
            """,
            (trade_date, limit),
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
    rows = conn.execute(
        """
        SELECT symbol FROM final_signals
        WHERE trade_date=? AND actionable=1
        ORDER BY unified_score DESC LIMIT ?
        """,
        (trade_date, limit),
    ).fetchall()
    return [r[0] for r in rows]


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM opportunity_score_v2").fetchone()
        trade_date = row["d"] if row and row["d"] else None
        if not trade_date:
            row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
            trade_date = row["d"] if row else None

    client_on = os.environ.get("MED_CLIENT_SIGNAL", "0") == "1"
    feed_map = load_med_feed_map(conn, trade_date) if trade_date else {}
    symbols = _top_symbols(conn, trade_date) if trade_date else []

    deltas: List[dict] = []
    for sym in symbols:
        feed = feed_map.get(sym)
        if not feed:
            continue
        base = conn.execute(
            "SELECT opportunity_score FROM opportunity_score_v2 WHERE trade_date=? AND symbol=?",
            (trade_date, sym),
        ).fetchone()
        base_score = float(base["opportunity_score"]) if base else None
        pen, _, pe = apply_med_research_penalty(sym, feed)
        boost, _, be = apply_med_research_boost(sym, feed)
        net = round(boost - (-pen), 2) if pen or boost else 0.0
        row = {
            "symbol": sym,
            "med_bucket": feed.get("med_bucket"),
            "base_opp_score": base_score,
            "penalize_pts": round(pen, 2),
            "boost_pts": round(boost, 2),
            "net_delta_boost_vs_pen": net,
            "penalize_evidence": pe,
            "boost_evidence": be,
        }
        deltas.append(row)
        conn.execute(
            """
            INSERT OR REPLACE INTO med_opp_delta_ledger
            (trade_date, symbol, base_opp_score, penalize_pts, boost_pts, net_delta_boost_vs_pen, med_bucket)
            VALUES (?,?,?,?,?,?,?)
            """,
            (trade_date, sym, base_score, row["penalize_pts"], row["boost_pts"], net, row["med_bucket"]),
        )
    conn.commit()

    avg_delta = round(sum(d["net_delta_boost_vs_pen"] for d in deltas) / len(deltas), 2) if deltas else 0.0
    if trade_date and client_on:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_live_session_ledger
            (trade_date, med_client_signal, symbols_monitored, avg_delta)
            VALUES (?,?,?,?)
            """,
            (trade_date, 1, len(deltas), avg_delta),
        )
        conn.commit()

    live_sessions = conn.execute(
        "SELECT COUNT(*) n FROM med_live_session_ledger WHERE med_client_signal=1"
    ).fetchone()[0]

    top_movers = sorted(deltas, key=lambda x: abs(x["net_delta_boost_vs_pen"]), reverse=True)[:8]

    payload = {
        "success": True,
        "phase": "15_opp_delta",
        "trade_date": trade_date,
        "MED_CLIENT_SIGNAL": os.environ.get("MED_CLIENT_SIGNAL", "0"),
        "MED_FEED_BOOST": os.environ.get("MED_FEED_BOOST", "0"),
        "MED_FEED_PENALIZE": os.environ.get("MED_FEED_PENALIZE", "1"),
        "symbols_monitored": len(deltas),
        "avg_delta_boost_vs_pen": avg_delta,
        "live_sessions_with_client_signal": live_sessions,
        "top_movers": top_movers,
        "note": "Delta = boost_pts - penalize_pts on opp score axis (monitoring only)",
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
