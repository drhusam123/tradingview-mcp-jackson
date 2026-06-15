#!/usr/bin/env python3
"""
MDE Signal Provider — Phase 2.10E integration (shadow only).

Publishes COMP_001B + PRDC_SPECIAL daily signals to mde_shadow_signals_daily.
Does NOT change actionable, promotion, Telegram, or UES decisions.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = ROOT / "data" / "egx_trading.db"
LAST_JSON = DATA / "mde_signal_provider_last.json"

from mde_client_grade_edge_validation import build_analog_index  # noqa: E402
from mde_forward_paper_trading import (  # noqa: E402
    MIN_TRADEABILITY,
    PHASE_INVARIANTS,
    PRDC_SYMBOL,
    build_prdc_track,
    comp001b_event_ok,
)
from mde_hidden_cause_validation import infer_hidden_cause, strategic_liquidity  # noqa: E402
from mde_shadow_trade_factory import confirmation_ok, quick_analog, tradeability_score  # noqa: E402
from mde_actionable_discovery import enrich_events  # noqa: E402
from mde_walkforward_shadow import connect, load_events  # noqa: E402

PROVIDER_ID = "MDE_2_10E"
TIER = "RESEARCH_SHADOW"
CLIENT_GRADE = False


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS mde_shadow_signals_daily (
        signal_date           TEXT NOT NULL,
        symbol                TEXT NOT NULL,
        provider_id           TEXT NOT NULL DEFAULT 'MDE_2_10E',
        track                 TEXT NOT NULL,
        monitor_state         TEXT NOT NULL,
        effective_score       REAL,
        tradeability_score    REAL,
        hidden_cause          TEXT,
        trigger_matched       TEXT,
        confirmation_ok       INTEGER DEFAULT 0,
        client_grade_eligible INTEGER DEFAULT 0,
        tier                  TEXT DEFAULT 'RESEARCH_SHADOW',
        detail_json           TEXT,
        created_at            TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (signal_date, symbol, track)
    );
    CREATE INDEX IF NOT EXISTS idx_mde_shadow_signals_date
        ON mde_shadow_signals_daily(signal_date, monitor_state);
    """)


def _comp_state(e: dict, astat: dict) -> str:
    liq = strategic_liquidity(e, astat).get("liquidity_type", "")
    if liq in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY"):
        return "REJECTED_AFTER_TRIGGER"
    if not confirmation_ok(e):
        return "WAIT_CONFIRMATION"
    if tradeability_score(e) < MIN_TRADEABILITY:
        return "REJECTED_AFTER_TRIGGER"
    if e.get("timing_class") in ("LATE", "TOO_LATE", "POST_MOVE_RISK"):
        return "REJECTED_AFTER_TRIGGER"
    return "OPEN_PAPER_TRADE"


def build_daily_signals(events: List[dict], by_sector: dict, trade_date: str) -> List[dict]:
    """Emit provider signals for one session."""
    out: List[dict] = []
    day_events = [e for e in events if e["trade_date"] == trade_date]

    for e in day_events:
        if not e.get("hidden_repricing") and e.get("discovery_score", 0) < 45:
            continue
        astat = quick_analog(e, by_sector, trade_date)
        if not comp001b_event_ok(e, astat):
            continue
        cause, _, _ = infer_hidden_cause(e, astat)
        state = _comp_state(e, astat)
        out.append({
            "signal_date": trade_date,
            "symbol": e["symbol"],
            "provider_id": PROVIDER_ID,
            "track": "COMP_001B",
            "monitor_state": state,
            "effective_score": e.get("effective_score"),
            "tradeability_score": tradeability_score(e),
            "hidden_cause": cause,
            "trigger_matched": "TF_COMP_001B",
            "confirmation_ok": 1 if confirmation_ok(e) else 0,
            "client_grade_eligible": 0,
            "tier": TIER,
            "detail_json": {
                "timing_class": e.get("timing_class"),
                "analog_PF": astat.get("analog_PF"),
                "analog_hit_5d": astat.get("analog_hit_5d"),
                "liquidity_type": strategic_liquidity(e, astat).get("liquidity_type"),
                "discovery_score": e.get("discovery_score"),
                "mde_only": bool(e.get("mde_only")),
            },
        })

    prdc_e = next((e for e in day_events if e["symbol"] == PRDC_SYMBOL), None)
    if prdc_e:
        track = build_prdc_track(prdc_e, events, by_sector, trade_date)
        out.append({
            "signal_date": trade_date,
            "symbol": PRDC_SYMBOL,
            "provider_id": PROVIDER_ID,
            "track": "PRDC_SPECIAL",
            "monitor_state": track.get("monitor_state", "WAIT_CONFIRMATION"),
            "effective_score": prdc_e.get("effective_score"),
            "tradeability_score": tradeability_score(prdc_e),
            "hidden_cause": track.get("hidden_cause"),
            "trigger_matched": "PRDC_SPECIAL_SHADOW",
            "confirmation_ok": 1 if track.get("confirmation_achieved") else 0,
            "client_grade_eligible": 0,
            "tier": TIER,
            "detail_json": {
                k: track.get(k) for k in (
                    "prdc_gate_checks", "metaorder_stage", "analog_fusion_score",
                    "confirmation_trigger", "invalidation_trigger", "entry_status",
                )
            },
        })

    return out


def publish(conn: sqlite3.Connection, trade_date: str, signals: List[dict]) -> int:
    conn.execute("DELETE FROM mde_shadow_signals_daily WHERE signal_date=?", (trade_date,))
    for s in signals:
        conn.execute("""
            INSERT OR REPLACE INTO mde_shadow_signals_daily
            (signal_date, symbol, provider_id, track, monitor_state,
             effective_score, tradeability_score, hidden_cause, trigger_matched,
             confirmation_ok, client_grade_eligible, tier, detail_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            s["signal_date"], s["symbol"], s["provider_id"], s["track"],
            s["monitor_state"], s.get("effective_score"), s.get("tradeability_score"),
            s.get("hidden_cause"), s.get("trigger_matched"),
            s.get("confirmation_ok", 0), s.get("client_grade_eligible", 0),
            s.get("tier", TIER), json.dumps(s.get("detail_json") or {}, default=str),
        ))
    conn.commit()
    return len(signals)


def load_lookup(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    """Best signal per symbol for score_all shadow logging."""
    try:
        rows = conn.execute("""
            SELECT * FROM mde_shadow_signals_daily
            WHERE signal_date=?
            ORDER BY
              CASE monitor_state
                WHEN 'OPEN_PAPER_TRADE' THEN 0
                WHEN 'NEW_SIGNAL' THEN 1
                WHEN 'WAIT_CONFIRMATION' THEN 2
                ELSE 9
              END,
              tradeability_score DESC
        """, (trade_date,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    lookup: Dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in lookup:
            lookup[sym] = dict(r)
    return lookup


def shadow_fields(lookup: Dict[str, dict], symbol: str) -> dict:
    s = lookup.get(symbol)
    if not s:
        return {
            "shadow_mde_provider_state": None,
            "shadow_mde_track": None,
            "shadow_mde_tradeability": None,
            "shadow_mde_gate_passed": 0,
            "shadow_mde_would_paper": 0,
        }
    state = s.get("monitor_state")
    paper = state in ("OPEN_PAPER_TRADE", "NEW_SIGNAL")
    gate = (
        paper
        and int(s.get("confirmation_ok") or 0)
        and (s.get("tradeability_score") or 0) >= MIN_TRADEABILITY
        and state not in ("REJECTED_AFTER_TRIGGER", "INVALIDATED")
    )
    return {
        "shadow_mde_provider_state": state,
        "shadow_mde_track": s.get("track"),
        "shadow_mde_tradeability": s.get("tradeability_score"),
        "shadow_mde_gate_passed": 1 if gate else 0,
        "shadow_mde_would_paper": 1 if paper else 0,
    }


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    trade_date = params.get("trade_date") or params.get("date")

    conn = connect()
    ensure_tables(conn)
    events, by_sym = load_events(conn)
    from mde_walkforward_shadow import date_index  # noqa: E402
    edates, _ = date_index(events)
    enrich_events(events, by_sym, edates)
    by_sector = build_analog_index(events)
    if not trade_date:
        trade_date = edates[-1]

    signals = build_daily_signals(events, by_sector, trade_date)
    n = publish(conn, trade_date, signals)

    states: Dict[str, int] = {}
    for s in signals:
        states[s["monitor_state"]] = states.get(s["monitor_state"], 0) + 1

    payload = {
        "at": at,
        "provider_id": PROVIDER_ID,
        "tier": TIER,
        "client_grade_eligible": CLIENT_GRADE,
        "invariants": PHASE_INVARIANTS,
        "trade_date": trade_date,
        "signal_count": n,
        "state_counts": states,
        "tracks": {
            "COMP_001B": sum(1 for s in signals if s["track"] == "COMP_001B"),
            "PRDC_SPECIAL": sum(1 for s in signals if s["track"] == "PRDC_SPECIAL"),
        },
        "signals": signals,
        "gate_status": "RESEARCH_EDGE_ONLY",
        "note": "Shadow provider — observe only; no actionable/promotion/Telegram impact",
    }
    LAST_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()

    print(f"  MDE provider: {n} signals on {trade_date} states={states}", flush=True)
    return {"success": True, "signal_count": n, "trade_date": trade_date, "states": states}


COMMANDS = {"run": cmd_run}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    p: dict = {}
    if len(sys.argv) > 2:
        try:
            p = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            p = {}
    fn = COMMANDS.get(cmd, cmd_run)
    print(json.dumps(fn(p), indent=2))
