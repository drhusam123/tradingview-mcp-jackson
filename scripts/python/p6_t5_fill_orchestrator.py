#!/usr/bin/env python3
"""
Phase 19 — P6 t5 fill orchestrator for client-delivered signals.

Runs outcome_filler, tracks EGCH/UEFM watch list, reports t5 closure progress.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "p6_t5_fill_last.json"

T5_BARS = 5
WATCH = [
    s.strip().upper()
    for s in os.environ.get("EGX_T5_WATCH_SYMBOLS", "EGCH,UEFM").split(",")
    if s.strip()
]


def _pending(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute(
        """
        SELECT symbol, signal_date, outcome_filled, return_t5, hit_t5, entry_price, client_delivered
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered, 0) = 1
          AND COALESCE(outcome_filled, 0) < ?
        ORDER BY signal_date DESC
        """,
        (T5_BARS,),
    ).fetchall()
    return [dict(r) for r in rows]


def _bars_after(conn: sqlite3.Connection, symbol: str, signal_date: str) -> List[str]:
    rows = conn.execute(
        """
        SELECT date(bar_time,'unixepoch') as d FROM ohlcv_history_execution
        WHERE symbol=? AND date(bar_time,'unixepoch') > ?
        ORDER BY bar_time LIMIT ?
        """,
        (symbol, signal_date, T5_BARS),
    ).fetchall()
    return [r["d"] for r in rows]


def _run_outcome_filler() -> dict:
    filler = ROOT / "scripts/python/outcome_filler.py"
    if not filler.exists():
        return {"ok": False, "error": "outcome_filler missing"}
    try:
        proc = subprocess.run(
            [sys.executable, str(filler)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "ok": proc.returncode == 0,
            "summary": (proc.stdout or proc.stderr or "").strip().split("\n")[-1][:200],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def run(params: dict | None = None) -> dict:
    params = params or {}
    auto_fill = params.get("auto_fill", os.environ.get("EGX_T5_FILL_AUTO", "1") == "1")

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    before = _pending(conn)
    before_keys = {(r["symbol"], r["signal_date"]) for r in before}

    filler = {"ok": True, "skipped": True}
    if auto_fill:
        filler = _run_outcome_filler()
        filler["skipped"] = False

    after = _pending(conn)
    after_keys = {(r["symbol"], r["signal_date"]) for r in after}
    newly_closed = [
        {"symbol": s, "signal_date": d}
        for s, d in before_keys - after_keys
    ]

    watch_rows = []
    for sym in WATCH:
        for row in after:
            if row["symbol"] != sym:
                continue
            bars = _bars_after(conn, sym, row["signal_date"])
            watch_rows.append({
                "symbol": sym,
                "signal_date": row["signal_date"],
                "outcome_filled": row["outcome_filled"],
                "bars_available": len(bars),
                "bars_to_t5": max(0, T5_BARS - len(bars)),
                "projected_t5_date": bars[T5_BARS - 1] if len(bars) >= T5_BARS else None,
                "return_t5": row["return_t5"],
                "hit_t5": row["hit_t5"],
            })

    closed = conn.execute(
        """
        SELECT COUNT(*) n, SUM(CASE WHEN hit_t5=1 THEN 1 ELSE 0 END) w
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered, 0)=1 AND outcome_filled >= ?
        """,
        (T5_BARS,),
    ).fetchone()
    conn.close()

    n_closed = int(closed["n"] or 0)
    wins = int(closed["w"] or 0)

    payload = {
        "success": True,
        "phase": "19_t5_fill",
        "auto_fill": auto_fill,
        "filler": filler,
        "pending_before": len(before),
        "pending_after": len(after),
        "newly_closed_t5": newly_closed,
        "watch_symbols": WATCH,
        "watch_pending": watch_rows,
        "closed_delivered": {
            "n": n_closed,
            "wins": wins,
            "wr_pct": round(wins / n_closed * 100, 1) if n_closed else None,
            "target_n": 30,
        },
        "status_line": (
            f"t5 pending {len(after)} | closed {n_closed}/30"
            + (f" | filled today {len(newly_closed)}" if newly_closed else "")
        ),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
