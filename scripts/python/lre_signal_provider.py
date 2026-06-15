#!/usr/bin/env python3
"""
LRE Signal Provider — Phase 2.0 integration (shadow only).

Publishes daily pre-explosion scores to lre_shadow_signals_daily.
Does NOT change actionable, promotion, Telegram, or UES decisions.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
LAST_JSON = DATA / "lre_signal_provider_last.json"
GATE_JSON = DATA / "lre_client_grade_gate_status.json"

from egx_liquidity_rotation_engine import LRE_INVARIANTS, ensure_tables  # noqa: E402

PROVIDER_ID = "LRE_2_0"
TIER = "RESEARCH_SHADOW"
MIN_EPS_GATE = 50.0
GATE_STAGES = {3, 4}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_provider_tables(conn: sqlite3.Connection) -> None:
    ensure_tables(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_shadow_signals_daily (
        signal_date           TEXT NOT NULL,
        symbol                TEXT NOT NULL,
        provider_id           TEXT NOT NULL DEFAULT 'LRE_2_0',
        stage                 INTEGER,
        stage_name            TEXT,
        explosion_potential   REAL,
        primary_list          TEXT,
        list_tags             TEXT,
        rotation_trigger      INTEGER DEFAULT 0,
        rotation_leader       TEXT,
        speculative_context   REAL,
        artifact_risk         INTEGER DEFAULT 0,
        tier                  TEXT DEFAULT 'RESEARCH_SHADOW',
        detail_json           TEXT,
        created_at            TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (signal_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_shadow_signals_date
        ON lre_shadow_signals_daily(signal_date, primary_list);
    """)


def _primary_list(tags: List[str]) -> Optional[str]:
    priority = (
        "ignition_candidates",
        "next_rotation",
        "silent_accumulation",
        "volume_awakening",
        "do_not_chase",
        "artifact_excluded",
    )
    for p in priority:
        if p in tags:
            return p
    return tags[0] if tags else None


def build_daily_signals(conn: sqlite3.Connection, trade_date: str) -> List[dict]:
    rows = conn.execute(
        """
        SELECT * FROM lre_daily_scores
        WHERE trade_date=?
          AND artifact_risk=0
          AND explosion_potential >= 35
        ORDER BY explosion_potential DESC
        """,
        (trade_date,),
    ).fetchall()
    market = conn.execute(
        "SELECT speculative_appetite FROM lre_market_daily WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    sai = market["speculative_appetite"] if market else None

    out: List[dict] = []
    for r in rows:
        tags = json.loads(r["list_tags"] or "[]")
        if "artifact_excluded" in tags:
            continue
        out.append({
            "signal_date": trade_date,
            "symbol": r["symbol"],
            "provider_id": PROVIDER_ID,
            "stage": r["stage"],
            "stage_name": r["stage_name"],
            "explosion_potential": r["explosion_potential"],
            "primary_list": _primary_list(tags),
            "list_tags": tags,
            "rotation_trigger": int(r["rotation_trigger"] or 0),
            "rotation_leader": r["rotation_leader"],
            "speculative_context": sai,
            "artifact_risk": int(r["artifact_risk"] or 0),
            "tier": TIER,
            "detail_json": {
                "abnormality_score": r["abnormality_score"],
                "stored_energy": r["stored_energy"],
                "supply_exhaustion": r["supply_exhaustion"],
                "vol_ratio_20": r["vol_ratio_20"],
                "compression_days": r["compression_days"],
                "move_from_low_20d_pct": r["move_from_low_20d_pct"],
                "analogue_score": r["analogue_score"],
            },
        })
    return out


def publish(conn: sqlite3.Connection, trade_date: str, signals: List[dict]) -> int:
    conn.execute("DELETE FROM lre_shadow_signals_daily WHERE signal_date=?", (trade_date,))
    for s in signals:
        conn.execute("""
            INSERT OR REPLACE INTO lre_shadow_signals_daily
            (signal_date, symbol, provider_id, stage, stage_name, explosion_potential,
             primary_list, list_tags, rotation_trigger, rotation_leader,
             speculative_context, artifact_risk, tier, detail_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            s["signal_date"], s["symbol"], s["provider_id"], s.get("stage"),
            s.get("stage_name"), s.get("explosion_potential"), s.get("primary_list"),
            json.dumps(s.get("list_tags") or []), s.get("rotation_trigger", 0),
            s.get("rotation_leader"), s.get("speculative_context"),
            s.get("artifact_risk", 0), s.get("tier", TIER),
            json.dumps(s.get("detail_json") or {}, default=str),
        ))
    conn.commit()
    return len(signals)


def load_lookup(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    try:
        rows = conn.execute("""
            SELECT * FROM lre_shadow_signals_daily
            WHERE signal_date=?
            ORDER BY explosion_potential DESC
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
            "shadow_lre_stage": None,
            "shadow_lre_eps": None,
            "shadow_lre_list": None,
            "shadow_lre_gate_passed": 0,
            "shadow_lre_rotation_leader": None,
        }
    stage = int(s.get("stage") or 0)
    eps = float(s.get("explosion_potential") or 0)
    tags = json.loads(s.get("list_tags") or "[]") if isinstance(s.get("list_tags"), str) else (s.get("list_tags") or [])
    gate = (
        stage in GATE_STAGES
        and eps >= MIN_EPS_GATE
        and int(s.get("artifact_risk") or 0) == 0
        and "do_not_chase" not in tags
    )
    return {
        "shadow_lre_stage": s.get("stage_name"),
        "shadow_lre_eps": eps,
        "shadow_lre_list": s.get("primary_list"),
        "shadow_lre_gate_passed": 1 if gate else 0,
        "shadow_lre_rotation_leader": s.get("rotation_leader"),
    }


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    trade_date = params.get("trade_date") or params.get("date")

    conn = connect()
    ensure_provider_tables(conn)
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        conn.close()
        return {"success": False, "error": "no trade_date / lre_daily_scores empty"}

    signals = build_daily_signals(conn, trade_date)
    n = publish(conn, trade_date, signals)

    lists: Dict[str, int] = {}
    for s in signals:
        pl = s.get("primary_list") or "unlisted"
        lists[pl] = lists.get(pl, 0) + 1

    gate_status = "RESEARCH_EDGE_ONLY"
    if GATE_JSON.exists():
        try:
            gate_status = json.loads(GATE_JSON.read_text()).get("IGNITION", {}).get("status", gate_status)
        except Exception:
            pass

    payload = {
        "at": at,
        "provider_id": PROVIDER_ID,
        "tier": TIER,
        "invariants": LRE_INVARIANTS,
        "trade_date": trade_date,
        "signal_count": n,
        "list_counts": lists,
        "gate_candidates": sum(1 for s in signals if shadow_fields({s["symbol"]: s}, s["symbol"])["shadow_lre_gate_passed"]),
        "gate_status": gate_status,
        "top_eps": signals[:15],
        "note": "Shadow provider — observe only; no actionable/promotion/Telegram impact",
    }
    LAST_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()

    print(f"  LRE provider: {n} signals on {trade_date} lists={lists}", flush=True)
    return {"success": True, "signal_count": n, "trade_date": trade_date, "lists": lists}


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
