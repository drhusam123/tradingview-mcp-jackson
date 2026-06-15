#!/usr/bin/env python3
"""
LRE dual-gate daily refresh — incremental audit upsert for one trade_date.

Fast causal daily path (no full-history replay). Feeds LRE-4.0 research feed.
Shadow only — no client path.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from egx_liquidity_rotation_engine import connect, ensure_tables, load_all_bars, table_exists  # noqa: E402
from lre_3_1_filters import enrich_signal, load_fingerprints  # noqa: E402
from lre_3_2_stages import classify_substage  # noqa: E402
from lre_3_3_dual_gate_audit import (  # noqa: E402
    audit_row_from_pair,
    ensure_dual_gate_table,
    forward_metrics,
)
from lre_3_4_confluence_robustness import load_sectors  # noqa: E402
from lre_3_6a_causal import (  # noqa: E402
    build_causal_signal,
    calibrate_thresholds_causal,
    load_explosion_events,
    load_mde_by_date,
    mde_watch_row,
)
from lre_3_6a_walk_forward_pilot import _static_thresholds  # noqa: E402
from lre_mde_dual_gate import assess_mde_gate  # noqa: E402

OUTPUT = DATA / "lre_dual_gate_daily_last.json"


def _persist_day(conn, trade_date: str, rows: List[dict]) -> int:
    conn.execute("DELETE FROM lre_mde_dual_gate_audit WHERE trade_date=?", (trade_date,))
    if not rows:
        conn.commit()
        return 0
    sql = """
        INSERT OR REPLACE INTO lre_mde_dual_gate_audit
        (trade_date, symbol, lre_stage, lre_sub_stage, lre_eps, lre_candidate_type,
         lre_reason_codes, lre_risk_flags, lre_monitoring_only,
         mde_stage, mde_score, mde_gate_passed, mde_reason_codes, mde_risk_flags,
         dual_gate_type, dual_gate_score, dual_gate_passed_shadow, dual_gate_reason,
         client_path_allowed, forward_return_5d, forward_return_10d, forward_return_20d,
         forward_return_30d, forward_return_45d, mfe_20d, mae_20d,
         hit_5pct_20d, hit_10pct_20d, hit_15pct_30d,
         artifact_flag, liquidity_flag, already_exploded_flag)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    batch = []
    for r in rows:
        batch.append((
            r["trade_date"], r["symbol"], r.get("lre_stage"), r.get("lre_sub_stage"),
            r.get("lre_eps"), r.get("lre_candidate_type"),
            json.dumps(r.get("lre_reason_codes") or []),
            json.dumps(r.get("lre_risk_flags") or []),
            int(r.get("lre_monitoring_only") or 0),
            r.get("mde_stage"), r.get("mde_score"), r.get("mde_gate_passed"),
            json.dumps(r.get("mde_reason_codes") or []),
            json.dumps(r.get("mde_risk_flags") or []),
            r.get("dual_gate_type"), r.get("dual_gate_score"),
            r.get("dual_gate_passed_shadow"), r.get("dual_gate_reason"),
            0,
            r.get("forward_return_5d"), r.get("forward_return_10d"), r.get("forward_return_20d"),
            r.get("forward_return_30d"), r.get("forward_return_45d"),
            r.get("mfe_20d"), r.get("mae_20d"),
            r.get("hit_5pct_20d"), r.get("hit_10pct_20d"), r.get("hit_15pct_30d"),
            int(r.get("artifact_flag") or 0),
            int(r.get("liquidity_flag") or 0),
            int(r.get("already_exploded_flag") or 0),
        ))
    conn.executemany(sql, batch)
    conn.commit()
    return len(batch)


def build_daily_audit_rows(
    conn,
    trade_date: str,
    by_sym: dict,
    sectors: Dict[str, str],
) -> List[dict]:
    fingerprints = load_fingerprints()
    events = load_explosion_events(conn)
    static_th = _static_thresholds(conn, by_sym, fingerprints)
    th = calibrate_thresholds_causal(events, by_sym, fingerprints, trade_date, None)
    mde_by_date = load_mde_by_date(conn, "2022-04-05")
    mde_rows = {r["symbol"]: r for r in mde_by_date.get(trade_date, []) if mde_watch_row(r)}

    lre_syms = set()
    if table_exists(conn, "lre_daily_scores"):
        for r in conn.execute(
            "SELECT symbol FROM lre_daily_scores WHERE trade_date=? AND explosion_potential>=35",
            (trade_date,),
        ).fetchall():
            lre_syms.add(r["symbol"])

    symbols = sorted(set(mde_rows) | lre_syms)
    rows: List[dict] = []
    for sym in symbols:
        bars = by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
        if idx is None or idx < 45:
            continue
        mde_row = mde_rows.get(sym)
        if mde_row:
            sig = build_causal_signal(
                conn, sym, trade_date, bars, idx, fingerprints, th, mde_row,
                sectors.get(sym, "Unknown"),
            )
            if sig:
                audit = {
                    "trade_date": trade_date,
                    "symbol": sym,
                    "lre_stage": sig.get("lre_stage"),
                    "lre_sub_stage": sig.get("lre_sub_stage"),
                    "lre_eps": sig.get("lre_eps"),
                    "lre_candidate_type": sig.get("lre_candidate_type"),
                    "lre_reason_codes": sig.get("lre_risk_flags") or [],
                    "lre_risk_flags": sig.get("lre_risk_flags") or [],
                    "lre_monitoring_only": int(not sig.get("lre_monitoring_valid")),
                    "mde_stage": sig.get("mde_stage"),
                    "mde_score": sig.get("mde_score"),
                    "mde_gate_passed": int(sig.get("mde_gate_passed") or 0),
                    "mde_reason_codes": sig.get("mde_reason_codes") or [],
                    "mde_risk_flags": sig.get("mde_risk_flags") or [],
                    "dual_gate_type": sig.get("dual_gate_type"),
                    "dual_gate_score": sig.get("dual_gate_score"),
                    "dual_gate_passed_shadow": int(sig.get("dual_gate_passed_shadow") or 0),
                    "dual_gate_reason": sig.get("dual_gate_reason"),
                    "artifact_flag": int(sig.get("artifact_flag") or 0),
                    "liquidity_flag": int(sig.get("liquidity_flag") or 0),
                    "already_exploded_flag": int(sig.get("already_exploded_flag") or 0),
                    **forward_metrics(bars, idx),
                }
                rows.append(audit)
                continue

        lre_row = enrich_signal(conn, sym, bars, idx, fingerprints, th)
        if not lre_row or float(lre_row.get("explosion_potential") or 0) < 35:
            continue
        sub, _ = classify_substage(bars, idx, lre_row)
        lre_row["sub_stage"] = sub
        lre_row["symbol"] = sym
        lre_row["trade_date"] = trade_date
        mde_stub = {
            "mde_stage": None, "mde_score": 0, "mde_gate_passed": 0,
            "mde_reason_codes": [], "mde_risk_flags": [],
        }
        rows.append(audit_row_from_pair(sym, trade_date, lre_row, mde_stub, by_sym))
    return rows


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_tables(conn)
    ensure_dual_gate_table(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    trade_date = params.get("trade_date") or params.get("date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        conn.close()
        return {"success": False, "error": "no_trade_date"}

    rows = build_daily_audit_rows(conn, trade_date, by_sym, sectors)
    n = _persist_day(conn, trade_date, rows)
    conf = sum(1 for r in rows if r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE")

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "rows_upserted": n,
        "confluence_count": conf,
        "confluence_symbols": [
            r["symbol"] for r in rows if r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE"
        ],
        "client_path_allowed": False,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
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
