#!/usr/bin/env python3
"""
Phase 18 — P6 delivered KPI tracker (client-delivered signals awaiting t5).

Monitors EGCH/UEFM-style pending outcomes with OHLCV bar progress.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "p6_delivered_kpi_last.json"

T5_BARS = 5


def _bars_after(conn: sqlite3.Connection, symbol: str, signal_date: str) -> List[float]:
    rows = conn.execute(
        """
        SELECT close FROM ohlcv_history_execution
        WHERE symbol=? AND date(bar_time,'unixepoch') > ?
        ORDER BY bar_time LIMIT 15
        """,
        (symbol, signal_date),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row

    pending = conn.execute(
        """
        SELECT symbol, signal_date, conviction_tier, entry_price,
               outcome_filled, return_t5, hit_t5, client_delivered, delivered_at
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered, 0) = 1
          AND COALESCE(outcome_filled, 0) < ?
        ORDER BY signal_date DESC
        LIMIT 30
        """,
        (T5_BARS,),
    ).fetchall()

    tracked: List[dict] = []
    for row in pending:
        sym = row["symbol"]
        sd = row["signal_date"]
        entry = float(row["entry_price"] or 0)
        bars = _bars_after(conn, sym, sd)
        bars_avail = len(bars)
        partial_ret = None
        if entry > 0 and bars:
            partial_ret = round((bars[-1] / entry - 1) * 100, 2)
        tracked.append({
            "symbol": sym,
            "signal_date": sd,
            "conviction_tier": row["conviction_tier"],
            "entry_price": entry,
            "outcome_filled": int(row["outcome_filled"] or 0),
            "bars_available": bars_avail,
            "bars_to_t5": max(0, T5_BARS - bars_avail),
            "partial_return_pct": partial_ret,
            "return_t5": row["return_t5"],
            "hit_t5": row["hit_t5"],
            "delivered_at": row["delivered_at"],
            "t5_ready": bars_avail >= T5_BARS,
        })

    closed = conn.execute(
        """
        SELECT COUNT(*) n,
               SUM(CASE WHEN hit_t5=1 THEN 1 ELSE 0 END) wins
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered, 0) = 1 AND outcome_filled >= ?
        """,
        (T5_BARS,),
    ).fetchone()
    n_closed = int(closed["n"] or 0)
    wins = int(closed["wins"] or 0)
    wr = round(wins / n_closed * 100, 1) if n_closed else None

    payload = {
        "success": True,
        "phase": "18_p6_delivered_kpi",
        "pending_count": len(tracked),
        "pending": tracked,
        "closed_delivered": {
            "n": n_closed,
            "wins": wins,
            "wr_pct": wr,
            "target_n": 30,
            "target_wr_pct": 60,
        },
        "status_line": (
            f"delivered pending {len(tracked)} | closed {n_closed}/30"
            + (f" @ {wr}%" if wr is not None else "")
        ),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
