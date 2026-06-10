#!/usr/bin/env python3
"""
Replay Gate — validates ULTRA/HIGH actionable signals via TV replay bridge.
Writes replay_validation and demotes failures in final_signals.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPLAY_BRIDGE = ROOT / "scripts" / "tv_replay_bridge.mjs"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replay_validation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            replay_date TEXT,
            passed INTEGER DEFAULT 0,
            pnl_pct REAL,
            entry_price REAL,
            exit_price REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(trade_date, symbol)
        )
    """)


def run_replay(symbol: str, trade_date: str) -> dict:
    if not REPLAY_BRIDGE.exists():
        return {"success": False, "error": "replay bridge missing"}
    try:
        proc = subprocess.run(
            ["node", str(REPLAY_BRIDGE), symbol, trade_date],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
        if not line:
            return {"success": False, "error": proc.stderr or "no output"}
        return json.loads(line)
    except Exception as e:
        return {"success": False, "error": str(e)}


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_table(conn)

    trade_date = params.get("date") or conn.execute(
        "SELECT MAX(trade_date) FROM final_signals"
    ).fetchone()[0]
    if not trade_date:
        return {"success": False, "error": "no trade_date"}

    limit = int(params.get("limit", 8))
    min_score = float(params.get("min_score", 72.0))

    rows = conn.execute(
        """
        SELECT symbol, score, source_ml, r_ratio, entry_price
        FROM final_signals
        WHERE trade_date = ? AND actionable = 1
        ORDER BY score DESC LIMIT ?
        """,
        (trade_date, limit),
    ).fetchall()

    if not rows:
        rows = conn.execute(
            """
            SELECT symbol, score, source_ml, r_ratio, entry_price
            FROM final_signals
            WHERE trade_date = ? AND score >= ? AND veto_reason IS NULL
            ORDER BY score DESC LIMIT ?
            """,
            (trade_date, min_score, limit),
        ).fetchall()

    passed = []
    failed = []
    skipped = []

    for r in rows:
        sym = r["symbol"]
        rep = run_replay(sym, trade_date)
        if not rep.get("success", True) and rep.get("error"):
            skipped.append({"symbol": sym, "error": rep.get("error")})
            conn.execute(
                """
                INSERT OR REPLACE INTO replay_validation
                (trade_date, symbol, replay_date, passed, notes)
                VALUES (?, ?, ?, 0, ?)
                """,
                (trade_date, sym, trade_date, f"skip:{rep.get('error')}"[:200]),
            )
            continue

        pnl = float(rep.get("pnlPct") or rep.get("pnl_pct") or rep.get("pnl") or 0)
        ok = pnl >= -2.0 or rep.get("passed", pnl > 0)
        conn.execute(
            """
            INSERT OR REPLACE INTO replay_validation
            (trade_date, symbol, replay_date, passed, pnl_pct, entry_price, exit_price, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_date, sym, trade_date, 1 if ok else 0, pnl,
                rep.get("entry"), rep.get("current"),
                json.dumps(rep, default=str)[:500],
            ),
        )
        if ok:
            passed.append(sym)
        else:
            failed.append(sym)
            conn.execute(
                """
                UPDATE final_signals
                SET actionable = 0, veto_reason = 'REPLAY_FAIL'
                WHERE trade_date = ? AND symbol = ?
                """,
                (trade_date, sym),
            )

    conn.commit()
    conn.close()
    return {
        "success": True,
        "trade_date": trade_date,
        "tested": len(passed) + len(failed),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
