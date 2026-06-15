#!/usr/bin/env python3
"""SQLite index optimization + backup — safe for production DB."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
BACKUP_DIR = ROOT / "data" / "backups"
AUDIT_PATH = ROOT / "audit" / "DB_OPTIMIZATION_AUDIT.md"

INDEXES = [
    ("ohlcv_history", "idx_ohlcv_symbol_time", "symbol, bar_time"),
    ("ohlcv_history", "idx_ohlcv_date", "date(bar_time,'unixepoch')"),
    ("indicators_cache", "idx_ic_symbol_date", "symbol, bar_date"),
    ("final_signals", "idx_fs_trade_date", "trade_date"),
    ("final_signals", "idx_fs_signal_date", "signal_date"),
    ("final_signals", "idx_fs_symbol", "symbol"),
    ("gate_audit_snapshots", "idx_gas_signal_date", "signal_date"),
    ("explosion_predictions", "idx_ep_pred_date", "pred_date, symbol"),
    ("meta_label_scores", "idx_mls_date", "date, symbol"),
    ("notification_delivery_audit", "idx_nda_signal_date", "signal_date"),
]


def table_info(conn: sqlite3.Connection, table: str) -> dict:
    try:
        rows = conn.execute(f"SELECT COUNT(*) n FROM [{table}]").fetchone()[0]
        idxs = conn.execute(f"PRAGMA index_list({table})").fetchall()
        return {"rows": rows, "indexes": [i[1] for i in idxs]}
    except sqlite3.Error as e:
        return {"rows": 0, "indexes": [], "error": str(e)}


def run(*, apply: bool = True) -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    audit_lines = [
        "# DB Optimization Audit",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Table | Rows | Indexes Before | Index Added | Status |",
        "|-------|------|----------------|-------------|--------|",
    ]
    backup_path = None
    applied: list[str] = []

    if not DB_PATH.exists():
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text("# DB Optimization Audit\n\n**Status:** NO_DB\n", encoding="utf-8")
        return {"ok": False, "error": "NO_DB"}

    if apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"egx_trading_{stamp}.db"
        shutil.copy2(DB_PATH, backup_path)

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    try:
        ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            return {"ok": False, "error": f"integrity_check={ic}"}

        for table, idx_name, cols in INDEXES:
            before = table_info(conn, table)
            status = "skip"
            if apply and "error" not in before:
                try:
                    conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON [{table}] ({cols})")
                    applied.append(idx_name)
                    status = "applied"
                except sqlite3.Error as e:
                    status = f"fail:{e}"
            after = table_info(conn, table)
            audit_lines.append(
                f"| {table} | {before.get('rows', 0)} | {len(before.get('indexes', []))} | {idx_name} | {status} |"
            )

        if apply:
            conn.execute("ANALYZE")
            conn.commit()
    finally:
        conn.close()

    audit_lines += ["", f"**Backup:** `{backup_path}`" if backup_path else "", f"**Indexes applied:** {len(applied)}", ""]
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "backup": str(backup_path) if backup_path else None,
        "indexes_applied": applied,
        "audit": str(AUDIT_PATH),
    }


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    r = run(apply=not dry)
    print(json.dumps(r, indent=2))
    sys.exit(0 if r.get("ok") else 1)
