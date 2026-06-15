#!/usr/bin/env python3
"""
Phase 19 — LRE forward OOS accumulator (daily forward shadow + closed count delta).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "lre_oos_accumulator_last.json"
TARGET = 40

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lre_3_6b_forward_shadow_pilot import run as run_forward_shadow  # noqa: E402


def _closed_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) n FROM lre_forward_shadow_ledger
        WHERE forward_return_20d IS NOT NULL
        """
    ).fetchone()
    return int(row[0] if row else 0)


def _read_prev() -> dict:
    if not OUTPUT.exists():
        return {}
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def run(params: dict | None = None) -> dict:
    params = params or {}
    prev = _read_prev()
    prev_closed = int(prev.get("oos_closed") or 0)

    shadow = run_forward_shadow(params)
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    closed = _closed_count(conn)
    open_n = conn.execute(
        "SELECT COUNT(*) FROM lre_forward_shadow_ledger WHERE exit_status='open'"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM lre_forward_shadow_ledger").fetchone()[0]
    conn.close()

    delta = closed - prev_closed
    payload = {
        "success": True,
        "phase": "19_lre_oos_accumulator",
        "trade_date": shadow.get("trade_date") or params.get("trade_date"),
        "forward_shadow": shadow,
        "oos_closed": closed,
        "oos_target": TARGET,
        "oos_delta": delta,
        "open_positions": int(open_n or 0),
        "total_forward_rows": int(total or 0),
        "progress_pct": round(closed / TARGET * 100, 1) if TARGET else 0,
        "status_line": f"LRE OOS {closed}/{TARGET} (Δ{delta:+d})",
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
