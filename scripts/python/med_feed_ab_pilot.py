#!/usr/bin/env python3
"""
MED Phase 13 — feed boost vs penalize A/B shadow pilot.

Computes both tracks side-by-side without changing production unless MED_FEED_BOOST=1.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_feed_ab_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_opp_bridge import (
    apply_med_research_penalty,
    feed_boost_enabled,
    feed_penalize_enabled,
    load_med_feed_map,
)

BUCKET_BOOST = {
    "MED_HIGH_CONVICTION_RESEARCH": 4.0,
    "MED_POSITIVE_EXPECTANCY": 2.5,
    "MED_MONITOR": 1.0,
}


def apply_med_research_boost(
    symbol: str,
    feed_row: dict | None,
) -> Tuple[float, List[str], dict]:
    if not feed_row:
        return 0.0, [], {}
    bucket = str(feed_row.get("med_bucket") or "")
    base = BUCKET_BOOST.get(bucket, 0.0)
    if base <= 0:
        return 0.0, [], {}
    p = float(feed_row.get("p_cond_20d_10") or 0)
    scale = 0.5 + min(p / 0.25, 1.0) * 0.5
    pts = round(base * scale, 2)
    flags = [f"MED_BOOST_{bucket.split('_')[-1]}"]
    return pts, flags, {
        "med_bucket": bucket,
        "med_boost_pts": pts,
        "p_cond_20d_10": p,
        "track": "boost",
    }


def ensure_ab_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS med_feed_ab_ledger (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        med_bucket TEXT,
        penalize_pts REAL DEFAULT 0,
        boost_pts REAL DEFAULT 0,
        delta_pts REAL DEFAULT 0,
        winner_track TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_feed_ab_daily (
        trade_date TEXT PRIMARY KEY,
        boost_wins INTEGER DEFAULT 0,
        penalize_wins INTEGER DEFAULT 0,
        neutral INTEGER DEFAULT 0,
        session_winner TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)


def compare_tracks(feed_map: Dict[str, dict]) -> List[dict]:
    rows = []
    for sym, feed in feed_map.items():
        pen, _, pe = apply_med_research_penalty(sym, feed)
        boost, _, be = apply_med_research_boost(sym, feed)
        delta = round(boost - pen, 2)
        if boost > 0 and pen > 0:
            winner = "boost" if delta > 0 else "penalize"
        elif boost > 0:
            winner = "boost"
        elif pen > 0:
            winner = "penalize"
        else:
            winner = "neutral"
        rows.append({
            "symbol": sym,
            "med_bucket": feed.get("med_bucket"),
            "penalize_pts": round(pen, 2),
            "boost_pts": round(boost, 2),
            "delta_pts": delta,
            "winner_track": winner,
            "penalize_evidence": pe,
            "boost_evidence": be,
        })
    rows.sort(key=lambda x: abs(x["delta_pts"]), reverse=True)
    return rows


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    ensure_ab_table(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    feed_map = load_med_feed_map(conn, trade_date) if trade_date else {}
    comparisons = compare_tracks(feed_map)

    for c in comparisons:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_feed_ab_ledger
            (trade_date, symbol, med_bucket, penalize_pts, boost_pts, delta_pts, winner_track)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                trade_date, c["symbol"], c["med_bucket"],
                c["penalize_pts"], c["boost_pts"], c["delta_pts"], c["winner_track"],
            ),
        )
    conn.commit()

    boost_wins = sum(1 for c in comparisons if c["winner_track"] == "boost")
    pen_wins = sum(1 for c in comparisons if c["winner_track"] == "penalize")
    neutral = sum(1 for c in comparisons if c["winner_track"] == "neutral")

    session_winner = "boost" if boost_wins > pen_wins else ("penalize" if pen_wins > boost_wins else "tie")
    if trade_date:
        conn.execute(
            """
            INSERT OR REPLACE INTO med_feed_ab_daily
            (trade_date, boost_wins, penalize_wins, neutral, session_winner)
            VALUES (?,?,?,?,?)
            """,
            (trade_date, boost_wins, pen_wins, neutral, session_winner),
        )
        conn.commit()

    streak = 0
    for row in conn.execute(
        "SELECT session_winner FROM med_feed_ab_daily ORDER BY trade_date DESC LIMIT 30"
    ).fetchall():
        if row["session_winner"] == "boost":
            streak += 1
        else:
            break

    production_track = "boost" if feed_boost_enabled() else ("penalize" if feed_penalize_enabled() else "off")

    payload = {
        "success": True,
        "phase": "13_feed_ab",
        "trade_date": trade_date,
        "production_track": production_track,
        "MED_FEED_BOOST": os.environ.get("MED_FEED_BOOST", "0"),
        "MED_FEED_PENALIZE": os.environ.get("MED_FEED_PENALIZE", "1"),
        "symbols_compared": len(comparisons),
        "boost_wins": boost_wins,
        "penalize_wins": pen_wins,
        "neutral": neutral,
        "session_winner": session_winner,
        "boost_win_streak": streak,
        "boost_streak_target": int(os.environ.get("EGX_MED_AB_STREAK_TARGET", "5")),
        "feed_boost_recommended": streak >= int(os.environ.get("EGX_MED_AB_STREAK_TARGET", "5")),
        "top_deltas": comparisons[:10],
        "recommendation": (
            "Enable MED_FEED_BOOST=1 when MED graduation met and boost_wins > penalize_wins for 5+ sessions"
            if boost_wins > pen_wins
            else "Keep MED_FEED_PENALIZE=1 — boost track not dominant yet"
        ),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
