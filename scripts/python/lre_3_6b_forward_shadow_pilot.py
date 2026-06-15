#!/usr/bin/env python3
"""
LRE Phase 3.6B — Live Forward Shadow Pilot Ledger.

Appends real forward OOS entries after LRE-3.6A historical walk-forward window.
Shadow only — no client path, Telegram, or actionable changes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from egx_liquidity_rotation_engine import LRE_INVARIANTS, connect, ensure_tables, load_all_bars  # noqa: E402
from lre_3_3_dual_gate_audit import forward_metrics  # noqa: E402
from lre_3_5_dual_gate_shadow_pilot import update_forward_outcomes  # noqa: E402
from lre_4_0_research_feed import build_research_feed, publish_feed, ensure_feed_table  # noqa: E402
from lre_3_4_confluence_robustness import load_sectors  # noqa: E402

PHASE = "LRE-3.6B"
FORWARD_START = "2026-06-12"

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": PHASE,
    "mode": "live_forward",
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
}

OUTPUT = DATA / "lre_3_6b_forward_shadow_last.json"


def ensure_forward_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_forward_shadow_ledger (
        trade_date              TEXT NOT NULL,
        symbol                  TEXT NOT NULL,
        sector                  TEXT,
        feed_tier               TEXT,
        pilot_bucket            TEXT,
        pilot_eligible          INTEGER DEFAULT 0,
        lre_sub_stage           TEXT,
        lre_eps                 REAL,
        mde_score               REAL,
        dual_gate_score         REAL,
        opp_boost_points        REAL,
        forward_return_5d       REAL,
        forward_return_10d      REAL,
        forward_return_20d      REAL,
        forward_return_30d      REAL,
        mfe_20d                 REAL,
        mae_20d                 REAL,
        exit_status             TEXT DEFAULT 'open',
        client_path_allowed     INTEGER DEFAULT 0,
        created_at              TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    """)


def _eligible_forward_rows(feed_rows: List[dict]) -> List[dict]:
    ok_tiers = {
        "LRE_CLEAN_CORE", "LRE_CONFLUENCE_CAPPED", "LRE_4B_MONITOR", "LRE_CONFLUENCE",
    }
    return [
        r for r in feed_rows
        if r.get("feed_tier") in ok_tiers or int(r.get("pilot_eligible") or 0)
    ]


def persist_forward_ledger(
    conn,
    trade_date: str,
    feed_rows: List[dict],
    by_sym: dict,
) -> int:
    eligible = _eligible_forward_rows(feed_rows)
    if not eligible:
        return 0
    n = 0
    for r in eligible:
        sym = r["symbol"]
        bars = by_sym.get(sym)
        fwd = {}
        if bars:
            idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
            if idx is not None:
                fwd = forward_metrics(bars, idx)
        conn.execute(
            """
            INSERT OR REPLACE INTO lre_forward_shadow_ledger
            (trade_date, symbol, sector, feed_tier, pilot_bucket, pilot_eligible,
             lre_sub_stage, lre_eps, mde_score, dual_gate_score, opp_boost_points,
             forward_return_5d, forward_return_10d, forward_return_20d, forward_return_30d,
             mfe_20d, mae_20d, exit_status, client_path_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_date, sym, r.get("sector"), r.get("feed_tier"), r.get("pilot_bucket"),
                int(r.get("pilot_eligible") or 0), r.get("lre_sub_stage"), r.get("lre_eps"),
                r.get("mde_score"), r.get("dual_gate_score"), r.get("opp_boost_points"),
                fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                fwd.get("forward_return_20d"), fwd.get("forward_return_30d"),
                fwd.get("mfe_20d"), fwd.get("mae_20d"), "open", 0,
            ),
        )
        n += 1
    conn.commit()
    return n


def update_forward_ledger_outcomes(conn, by_sym: dict, as_of_date: str) -> int:
    rows = conn.execute(
        "SELECT trade_date, symbol FROM lre_forward_shadow_ledger WHERE exit_status='open'"
    ).fetchall()
    updated = 0
    for r in rows:
        sym, td = r["symbol"], r["trade_date"]
        bars = by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
        if idx is None:
            continue
        as_idx = next((i for i, b in enumerate(bars) if b["date"] == as_of_date), None)
        if as_idx is None or as_idx <= idx:
            continue
        fwd = forward_metrics(bars, idx)
        closed = fwd.get("forward_return_20d") is not None and (as_idx - idx) >= 20
        conn.execute(
            """
            UPDATE lre_forward_shadow_ledger
            SET forward_return_5d=?, forward_return_10d=?, forward_return_20d=?,
                forward_return_30d=?, mfe_20d=?, mae_20d=?,
                exit_status=CASE WHEN ? THEN 'closed' ELSE exit_status END
            WHERE trade_date=? AND symbol=?
            """,
            (
                fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                fwd.get("forward_return_20d"), fwd.get("forward_return_30d"),
                fwd.get("mfe_20d"), fwd.get("mae_20d"), closed, td, sym,
            ),
        )
        updated += 1
    conn.commit()
    return updated


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_tables(conn)
    ensure_feed_table(conn)
    ensure_forward_table(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    trade_date = params.get("trade_date") or params.get("date")
    if not trade_date:
        row = conn.execute("SELECT MAX(signal_date) d FROM lre_research_feed_daily").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        conn.close()
        return {"success": False, "error": "no_trade_date"}

    if trade_date < FORWARD_START:
        payload = {
            "success": True,
            "skipped": True,
            "reason": f"trade_date {trade_date} before forward start {FORWARD_START}",
            "forward_start": FORWARD_START,
        }
        OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        conn.close()
        print(json.dumps(payload))
        return payload

    feed_rows = build_research_feed(conn, trade_date, by_sym, sectors)
    publish_feed(conn, trade_date, feed_rows)
    n_new = persist_forward_ledger(conn, trade_date, feed_rows, by_sym)
    n_fwd_updated = update_forward_ledger_outcomes(conn, by_sym, trade_date)
    n_pilot_updated = update_forward_outcomes(conn, by_sym, trade_date)

    open_n = conn.execute(
        "SELECT COUNT(*) n FROM lre_forward_shadow_ledger WHERE exit_status='open'"
    ).fetchone()["n"]
    total_n = conn.execute("SELECT COUNT(*) n FROM lre_forward_shadow_ledger").fetchone()["n"]

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": PHASE,
        "trade_date": trade_date,
        "forward_start": FORWARD_START,
        "forward_window_active": trade_date >= FORWARD_START,
        "new_entries": n_new,
        "forward_outcomes_updated": n_fwd_updated,
        "pilot_outcomes_updated": n_pilot_updated,
        "open_positions": open_n,
        "total_forward_rows": total_n,
        "eligible_today": len(_eligible_forward_rows(feed_rows)),
        "invariants": PHASE_INVARIANTS,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    print(json.dumps(payload))
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    run(p)
