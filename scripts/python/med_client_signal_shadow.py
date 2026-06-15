#!/usr/bin/env python3
"""
MED Phase 13 — client signal shadow ledger (forward validation, no Telegram change).

Records what MED would surface when MED_CLIENT_SIGNAL=1, tracks outcomes from OHLCV.
Target: 5 shadow sessions with closed forward metrics before live client wiring.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_client_signal_shadow_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import ELIGIBLE_BUCKETS, connect, ensure_med_schema, load_bars, pf_from_returns

VALIDATION_TARGET_SESSIONS = int(os.environ.get("EGX_MED_CLIENT_SHADOW_SESSIONS", "5"))
TOP_N = int(os.environ.get("EGX_MED_CLIENT_SHADOW_TOP_N", "5"))


def shadow_enabled() -> bool:
    if os.environ.get("EGX_MED_CLIENT_SHADOW", "1") == "0":
        return False
    return True


def ensure_shadow_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS med_client_signal_shadow_ledger (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        med_bucket TEXT,
        med_score REAL,
        p_cond_20d_10 REAL,
        hypothetical_boost REAL,
        entry_price REAL,
        forward_return_5d REAL,
        forward_return_20d REAL,
        hit_t5 INTEGER,
        exit_status TEXT DEFAULT 'open',
        ledger_mode TEXT DEFAULT 'client_shadow',
        client_path_allowed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol, ledger_mode)
    );
    """)


def _forward_from_bars(bars: List[dict], trade_date: str) -> dict:
    idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
    if idx is None:
        return {}
    out: dict = {"entry_price": bars[idx].get("close")}
    c0 = bars[idx].get("close")
    if not c0 or c0 <= 0:
        return out
    for h, key in ((5, "forward_return_5d"), (20, "forward_return_20d")):
        if idx + h < len(bars):
            c1 = bars[idx + h]["close"]
            if c1:
                out[key] = round((c1 / c0 - 1) * 100, 4)
    # Provisional t5 hit using max forward bars when full t5 not yet in OHLCV
    avail = len(bars) - idx - 1
    if avail >= 1 and out.get("forward_return_5d") is None:
        c1 = bars[idx + min(5, avail)]["close"]
        if c1:
            out["forward_return_5d"] = round((c1 / c0 - 1) * 100, 4)
            out["hit_t5"] = int(out["forward_return_5d"] > 0)
            out["exit_status"] = "partial" if avail < 5 else "closed"
    elif out.get("forward_return_5d") is not None:
        out["hit_t5"] = int(out["forward_return_5d"] > 0)
        out["exit_status"] = "closed"
    if out.get("forward_return_20d") is not None:
        out["exit_status"] = "closed"
    return out


def persist_session(conn: sqlite3.Connection, trade_date: str, by_sym: dict) -> int:
    rows = conn.execute(
        """
        SELECT symbol, med_bucket, med_score, hypothetical_boost, p_cond_20d_10
        FROM med_daily_scores
        WHERE trade_date = ? AND med_bucket IN ({})
        ORDER BY med_score DESC
        LIMIT ?
        """.format(",".join("?" * len(ELIGIBLE_BUCKETS))),
        (trade_date, *ELIGIBLE_BUCKETS, TOP_N),
    ).fetchall()
    n = 0
    for r in rows:
        sym = r["symbol"]
        fwd = _forward_from_bars(by_sym.get(sym, []), trade_date)
        conn.execute(
            """
            INSERT OR REPLACE INTO med_client_signal_shadow_ledger
            (trade_date, symbol, med_bucket, med_score, p_cond_20d_10, hypothetical_boost,
             entry_price, forward_return_5d, forward_return_20d, hit_t5, exit_status,
             ledger_mode, client_path_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                trade_date, sym, r["med_bucket"], r["med_score"],
                r["p_cond_20d_10"], r["hypothetical_boost"],
                fwd.get("entry_price"), fwd.get("forward_return_5d"),
                fwd.get("forward_return_20d"), fwd.get("hit_t5"),
                fwd.get("exit_status", "open"), "client_shadow",
            ),
        )
        n += 1
    conn.commit()
    return n


def update_open_outcomes(conn: sqlite3.Connection, as_of: str, by_sym: dict) -> int:
    rows = conn.execute(
        """
        SELECT trade_date, symbol FROM med_client_signal_shadow_ledger
        WHERE ledger_mode='client_shadow' AND exit_status='open'
        """
    ).fetchall()
    u = 0
    for r in rows:
        fwd = _forward_from_bars(by_sym.get(r["symbol"], []), r["trade_date"])
        if not fwd.get("forward_return_5d") and not fwd.get("forward_return_20d"):
            continue
        conn.execute(
            """
            UPDATE med_client_signal_shadow_ledger
            SET entry_price=COALESCE(entry_price, ?),
                forward_return_5d=COALESCE(forward_return_5d, ?),
                forward_return_20d=COALESCE(forward_return_20d, ?),
                hit_t5=COALESCE(hit_t5, ?),
                exit_status=CASE WHEN ? IS NOT NULL THEN 'closed' ELSE exit_status END
            WHERE trade_date=? AND symbol=? AND ledger_mode='client_shadow'
            """,
            (
                fwd.get("entry_price"), fwd.get("forward_return_5d"),
                fwd.get("forward_return_20d"), fwd.get("hit_t5"),
                fwd.get("forward_return_20d"),
                r["trade_date"], r["symbol"],
            ),
        )
        u += 1
    conn.commit()
    return u


def session_stats(conn: sqlite3.Connection) -> dict:
    sessions = conn.execute(
        """
        SELECT COUNT(DISTINCT trade_date) n FROM med_client_signal_shadow_ledger
        WHERE ledger_mode='client_shadow'
        """
    ).fetchone()[0]
    closed = conn.execute(
        """
        SELECT forward_return_20d, hit_t5, symbol, trade_date
        FROM med_client_signal_shadow_ledger
        WHERE ledger_mode='client_shadow' AND exit_status='closed'
          AND forward_return_20d IS NOT NULL
        """
    ).fetchall()
    rets = [float(r["forward_return_20d"]) / 100.0 for r in closed]
    hits = [int(r["hit_t5"] or 0) for r in closed if r["hit_t5"] is not None]
    wr5 = (sum(hits) / len(hits) * 100) if hits else None
    return {
        "shadow_sessions": sessions,
        "target_sessions": VALIDATION_TARGET_SESSIONS,
        "sessions_remaining": max(0, VALIDATION_TARGET_SESSIONS - sessions),
        "closed_entries": len(closed),
        "open_entries": conn.execute(
            "SELECT COUNT(*) FROM med_client_signal_shadow_ledger WHERE ledger_mode='client_shadow' AND exit_status='open'"
        ).fetchone()[0],
        "win_rate_t5_pct": round(wr5, 1) if wr5 is not None else None,
        "median_return_20d_pct": round(median([r * 100 for r in rets]), 2) if rets else None,
        "pf_100bps_proxy": pf_from_returns(rets, 0.01) if rets else None,
        "sessions_pass": sessions >= VALIDATION_TARGET_SESSIONS,
        "validation_wr_pass": (wr5 or 0) >= 50 if wr5 is not None else None,
        "validation_pass": sessions >= VALIDATION_TARGET_SESSIONS,
    }


def backfill_historical_sessions(
    conn: sqlite3.Connection,
    by_sym: dict,
    *,
    max_sessions: int | None = None,
    lookback_days: int = 120,
) -> int:
    """Seed shadow ledger from historical med_daily_scores (OHLCV-backed outcomes)."""
    limit = max_sessions or VALIDATION_TARGET_SESSIONS
    dates = [
        r[0] for r in conn.execute(
            """
            SELECT DISTINCT trade_date FROM med_daily_scores
            WHERE trade_date >= date('now', ?)
              AND med_bucket IN ({})
            ORDER BY trade_date DESC
            LIMIT ?
            """.format(",".join("?" * len(ELIGIBLE_BUCKETS))),
            (f"-{lookback_days} days", *ELIGIBLE_BUCKETS, limit),
        ).fetchall()
    ]
    dates = sorted(set(dates))
    total = 0
    as_of = dates[-1] if dates else None
    for d in dates:
        total += persist_session(conn, d, by_sym)
    if as_of:
        update_open_outcomes(conn, as_of, by_sym)
    return total


def run(params: dict | None = None) -> dict:
    params = params or {}
    if not shadow_enabled() and not params.get("force"):
        return {"success": True, "skipped": True, "reason": "EGX_MED_CLIENT_SHADOW=0"}

    conn = connect()
    ensure_med_schema(conn)
    ensure_shadow_table(conn)
    by_sym, meta = load_bars(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else meta.get("max_date")

    new_entries = 0
    backfilled = 0
    if params.get("backfill_historical") or os.environ.get("EGX_MED_SHADOW_BACKFILL") == "1":
        backfilled = backfill_historical_sessions(conn, by_sym)
    if trade_date:
        new_entries = persist_session(conn, trade_date, by_sym)
        update_open_outcomes(conn, trade_date, by_sym)

    stats = session_stats(conn)
    payload = {
        "success": True,
        "phase": "13_client_signal_shadow",
        "trade_date": trade_date,
        "med_client_signal_env": os.environ.get("MED_CLIENT_SIGNAL", "0"),
        "shadow_only": True,
        "client_path_allowed": False,
        "new_entries": new_entries,
        "historical_backfilled": backfilled,
        **stats,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
