#!/usr/bin/env python3
"""Generate audit/DB_AUDIT.md + data/db_audit_last.json from SQLite."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUTPUT_JSON = ROOT / "data" / "db_audit_last.json"
OUTPUT_MD = ROOT / "audit" / "DB_AUDIT.md"

TABLE_META = {
    "ohlcv_history": {"writers": "daily_update, egx_tv_auto_update", "readers": "scan, ML, gates"},
    "indicators_cache": {"writers": "rebuild_indicators", "readers": "scan_today, scorer"},
    "explosion_predictions": {"writers": "egx_ml_trainer", "readers": "signal_integration"},
    "final_signals": {"writers": "signal_integration, promotion", "readers": "telegram, audit"},
    "unified_signals": {"writers": "score_all", "readers": "gates, outcomes"},
    "recommendation_outcomes": {"writers": "outcome_filler", "readers": "P6 KPI, graduation"},
    "notification_delivery_audit": {"writers": "notify pipeline", "readers": "reconcile, ops"},
    "gate_audit_snapshots": {"writers": "gate_doctor", "readers": "audit, diagnostics"},
    "med_daily_scores": {"writers": "med_0_3_daily_engine", "readers": "med_feed_ab, MED"},
    "egx_market_discovery_daily": {"writers": "mde engine", "readers": "LRE, MDE shadow"},
    "med_feed_ab_daily": {"writers": "med_feed_ab_pilot", "readers": "phase14 graduation"},
    "schema_migrations": {"writers": "migrate.mjs", "readers": "health, verify"},
    "stock_universe": {"writers": "universe sync", "readers": "scan, hygiene"},
    "scans": {"writers": "scan_today", "readers": "discovery"},
}


def _latest_date(conn: sqlite3.Connection, table: str) -> str | None:
    date_cols = [
        ("signal_date", f"SELECT MAX(signal_date) FROM {table}"),
        ("pred_date", f"SELECT MAX(pred_date) FROM {table}"),
        ("trade_date", f"SELECT MAX(trade_date) FROM {table}"),
        ("bar_date", f"SELECT MAX(bar_date) FROM {table} WHERE bar_date NOT LIKE '2099-%'"),
        ("bar_time", f"SELECT MAX(date(bar_time,'unixepoch')) FROM {table}"),
        ("created_at", f"SELECT MAX(date(created_at)) FROM {table}"),
    ]
    for _, sql in date_cols:
        try:
            row = conn.execute(sql).fetchone()
            if row and row[0]:
                return str(row[0])[:10]
        except sqlite3.Error:
            continue
    return None


def run(params: dict | None = None) -> dict:
    if not DB_PATH.exists():
        payload = {"success": False, "error": "NO_DB"}
        OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]

    rows_out = []
    for table in tables:
        if table.startswith("sqlite_"):
            continue
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        except sqlite3.Error:
            n = -1
        latest = _latest_date(conn, table) if n >= 0 else None
        cols = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
        meta = TABLE_META.get(table, {"writers": "—", "readers": "—"})
        schema_problems = []
        if n == 0 and table in TABLE_META:
            schema_problems.append("empty_critical_table")
        rows_out.append({
            "table": table,
            "rows": n,
            "latest_date": latest,
            "columns": len(cols),
            "used_by": meta.get("readers", "—"),
            "written_by": meta.get("writers", "—"),
            "schema_problems": schema_problems,
            "freshness_problems": ["stale"] if latest is None and n > 0 and table in TABLE_META else [],
            "action_required": "backfill" if n == 0 and table in TABLE_META else ("monitor" if schema_problems else "none"),
        })

    conn.close()
    important = [r for r in rows_out if r["table"] in TABLE_META or r["rows"] > 1000]
    important.sort(key=lambda x: (-x["rows"], x["table"]))

    lines = [
        "# Database Audit",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Database:** `{DB_PATH.relative_to(ROOT)}`",
        f"**Tables:** {len(rows_out)} | **Audited (important):** {len(important)}",
        "",
        "| Table | Rows | Latest Date | Written By | Read By | Schema | Action |",
        "|-------|------|-------------|------------|---------|--------|--------|",
    ]
    for r in important[:60]:
        sp = ",".join(r["schema_problems"]) or "—"
        lines.append(
            f"| `{r['table']}` | {r['rows']:,} | {r['latest_date'] or '—'} | "
            f"{r['written_by']} | {r['used_by']} | {sp} | {r['action_required']} |"
        )

    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "table_count": len(rows_out),
        "important": important,
        "markdown": str(OUTPUT_MD.relative_to(ROOT)),
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].startswith("{") else {}
    print(json.dumps(run(p), indent=2, default=str))
