#!/usr/bin/env python3
"""Phase 21/22 — P6 delivered WR dashboard for live graduation tracking."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUTPUT = ROOT / "data" / "p6_delivered_wr_dashboard_last.json"

T5 = 5
MIN_N_BOOT = 3
MIN_WR_BOOT = 60.0
MIN_N_FULL = 30
MIN_WR_FULL = 60.0


def run(params: dict | None = None) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT symbol, signal_date, hit_t5, return_t5, conviction_tier, client_delivered
        FROM recommendation_outcomes
        WHERE COALESCE(client_delivered, 0) = 1 AND outcome_filled >= ?
        ORDER BY signal_date DESC
        """,
        (T5,),
    ).fetchall()
    conn.close()

    n = len(rows)
    wins = sum(1 for r in rows if r["hit_t5"] == 1)
    wr = round(wins / n * 100, 1) if n else None
    bootstrap_pass = n >= MIN_N_BOOT and wr is not None and wr >= MIN_WR_BOOT
    full_pass = n >= MIN_N_FULL and wr is not None and wr >= MIN_WR_FULL

    payload = {
        "success": True,
        "phase": "21_22_delivered_wr",
        "closed_n": n,
        "wins": wins,
        "wr_pct": wr,
        "bootstrap": {
            "pass": bootstrap_pass,
            "target_n": MIN_N_BOOT,
            "target_wr": MIN_WR_BOOT,
        },
        "full_live": {
            "pass": full_pass,
            "target_n": MIN_N_FULL,
            "target_wr": MIN_WR_FULL,
            "samples_needed": max(0, MIN_N_FULL - n),
        },
        "recent": [
            {
                "symbol": r["symbol"],
                "signal_date": r["signal_date"],
                "hit_t5": r["hit_t5"],
                "return_t5": r["return_t5"],
                "tier": r["conviction_tier"],
            }
            for r in rows[:10]
        ],
        "status_line": (
            f"delivered {n}/{MIN_N_FULL} @ {wr if wr is not None else '—'}% WR"
            + (f" | bootstrap {'PASS' if bootstrap_pass else 'pending'}" if n < MIN_N_FULL else "")
        ),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
