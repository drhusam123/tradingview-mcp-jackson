#!/usr/bin/env python3
"""
Phase 20 — watch-list t5 closure tracker (EGCH/UEFM @ 2026-06-14 → ~2026-06-19).

Runs outcome_filler and reports per-symbol t5 closure for P6 delivered KPI.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "p6_watch_t5_closure_last.json"

WATCH = [
    s.strip().upper()
    for s in os.environ.get("EGX_T5_WATCH_SYMBOLS", "EGCH,UEFM").split(",")
    if s.strip()
]
SIGNAL_DATE = os.environ.get("EGX_T5_WATCH_SIGNAL_DATE", "2026-06-14")
CLOSURE_ANCHOR = os.environ.get("EGX_T5_CLOSURE_ANCHOR", "2026-06-19")
T5_BARS = 5


def _run_filler() -> dict:
    filler = ROOT / "scripts/python/outcome_filler.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(filler)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {"ok": proc.returncode == 0, "tail": (proc.stdout or proc.stderr or "").strip()[-120:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _bars(conn: sqlite3.Connection, symbol: str, signal_date: str) -> List[str]:
    rows = conn.execute(
        """
        SELECT date(bar_time,'unixepoch') d FROM ohlcv_history_execution
        WHERE symbol=? AND date(bar_time,'unixepoch') > ?
        ORDER BY bar_time LIMIT ?
        """,
        (symbol, signal_date, T5_BARS),
    ).fetchall()
    return [r["d"] for r in rows]


def run(params: dict | None = None) -> dict:
    params = params or {}
    signal_date = params.get("signal_date") or params.get("trade_date")
    as_of = params.get("as_of_date") or signal_date

    filler = _run_filler() if params.get("auto_fill", os.environ.get("EGX_T5_FILL_AUTO", "1") == "1") else {"skipped": True}

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row

    watch_rows = []
    all_closed = True
    for sym in WATCH:
        row = conn.execute(
            """
            SELECT symbol, signal_date, outcome_filled, return_t5, hit_t5, entry_price, client_delivered
            FROM recommendation_outcomes
            WHERE symbol=? AND signal_date=? AND COALESCE(client_delivered, 0)=1
            LIMIT 1
            """,
            (sym, SIGNAL_DATE),
        ).fetchone()
        bars = _bars(conn, sym, SIGNAL_DATE)
        filled = int(row["outcome_filled"] or 0) if row else 0
        t5_ready = filled >= T5_BARS or len(bars) >= T5_BARS
        if row and filled < T5_BARS:
            all_closed = False
        elif not row:
            all_closed = False
            t5_ready = False

        watch_rows.append({
            "symbol": sym,
            "signal_date": SIGNAL_DATE,
            "found": row is not None,
            "client_delivered": int(row["client_delivered"] or 0) if row else 0,
            "outcome_filled": filled,
            "return_t5": row["return_t5"] if row else None,
            "hit_t5": row["hit_t5"] if row else None,
            "bars_available": len(bars),
            "projected_t5_date": bars[T5_BARS - 1] if len(bars) >= T5_BARS else None,
            "t5_closed": t5_ready and row is not None and filled >= T5_BARS,
            "t5_ready_bars": t5_ready,
        })

    closed_n = conn.execute(
        """
        SELECT COUNT(*) n, SUM(CASE WHEN hit_t5=1 THEN 1 ELSE 0 END) w
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered,0)=1 AND outcome_filled>=?
        """,
        (T5_BARS,),
    ).fetchone()
    conn.close()

    on_or_after_anchor = bool(as_of and as_of >= CLOSURE_ANCHOR)
    closure_met = all(w["t5_closed"] for w in watch_rows if w["found"])

    payload = {
        "success": True,
        "phase": "20_watch_t5_closure",
        "watch_symbols": WATCH,
        "watch_signal_date": SIGNAL_DATE,
        "closure_anchor": CLOSURE_ANCHOR,
        "as_of_date": as_of,
        "on_or_after_closure_anchor": on_or_after_anchor,
        "closure_met": closure_met,
        "all_watch_found": all(w["found"] for w in watch_rows),
        "filler": filler,
        "watch": watch_rows,
        "delivered_closed": {
            "n": int(closed_n["n"] or 0),
            "wins": int(closed_n["w"] or 0),
            "wr_pct": round(int(closed_n["w"] or 0) / int(closed_n["n"]) * 100, 1) if closed_n["n"] else None,
            "target_n": 30,
        },
        "status_line": (
            f"watch t5 {'CLOSED' if closure_met else 'pending'} "
            f"({sum(1 for w in watch_rows if w['t5_closed'])}/{len(WATCH)}) | "
            f"delivered {int(closed_n['n'] or 0)}/30"
        ),
        "gate_pass": closure_met if on_or_after_anchor else True,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
