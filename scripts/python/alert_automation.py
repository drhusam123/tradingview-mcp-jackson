#!/usr/bin/env python3
"""
Phase 57: Alert Automation
Manages TradingView alert tracking in SQLite for the EGX Autonomous Quant System.
The JS fetch script (fetch_alerts.mjs) calls this CLI to get client-safe alert
targets, then creates them via MCP, then calls back to log them.
"""

import os
import sys
import json
import sqlite3
import datetime
import collections

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            price_level REAL NOT NULL,
            condition TEXT NOT NULL,
            tv_alert_name TEXT,
            scan_date TEXT,
            created_at TEXT,
            expires_at TEXT,
            status TEXT DEFAULT 'PENDING',
            notes TEXT
        )
    """)
    db.commit()

# ── EGX calendar helpers ──────────────────────────────────────────────────────

def egx_add_trading_days(date_str, n_days):
    """
    Add n EGX trading days (Sun–Thu) to a date string (YYYY-MM-DD).
    Returns a date string.
    """
    dt = datetime.date.fromisoformat(date_str)
    added = 0
    while added < n_days:
        dt += datetime.timedelta(days=1)
        # EGX trades Sun(6)–Thu(3); weekday(): Mon=0 … Sun=6
        if dt.weekday() not in (4, 5):  # 4=Friday, 5=Saturday
            added += 1
    return dt.isoformat()


def today_str():
    return datetime.date.today().isoformat()


def now_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Command implementations ───────────────────────────────────────────────────

def cmd_get_alert_targets(params):
    """
    Read final client-approved signals and generate alert specs for each pick.
    Research scans are intentionally excluded from alert creation.
    """
    scan_date      = params.get("scan_date", today_str())
    min_score      = int(params.get("min_score", 70))
    max_picks      = int(params.get("max_picks", 10))
    alert_days_valid = int(params.get("alert_days_valid", 5))

    db = get_db()
    ensure_table(db)

    rows = db.execute("""
        SELECT f.symbol, f.setup_type, f.score,
               f.entry_price AS entry_low,
               f.entry_high,
               f.stop_loss,
               f.t1_target AS t1,
               f.t2_target AS t2,
               f.r_ratio AS rr1,
               f.r_ratio * 1.75 AS rr2,
               f.entry_price AS close_price,
               f.confidence,
               COALESCE(u.name, f.symbol) AS name
        FROM final_signals f
        LEFT JOIN stock_universe u ON u.symbol = f.symbol
        WHERE f.trade_date = ?
          AND f.actionable = 1
          AND f.veto_reason IS NULL
          AND f.score >= ?
          AND f.entry_price IS NOT NULL
          AND f.entry_high IS NOT NULL
          AND f.stop_loss IS NOT NULL
          AND f.t1_target IS NOT NULL
          AND f.r_ratio IS NOT NULL
        ORDER BY f.score DESC
        LIMIT ?
    """, (scan_date, min_score, max_picks)).fetchall()

    expires_at = egx_add_trading_days(scan_date, alert_days_valid)
    targets = []

    for row in rows:
        sym = row["symbol"]
        base_price_str = lambda p: f"{p:.2f}" if p else "0.00"
        setup = row["setup_type"] or "SCAN"

        def make_name(alert_type, price):
            return f"EGX_{sym}_{alert_type}_{price:.2f}"

        # Helper to add an alert spec
        def add(alert_type, price_level, condition, notes=""):
            if not price_level or price_level <= 0:
                return
            targets.append({
                "symbol":        sym,
                "alert_type":    alert_type,
                "price_level":   round(price_level, 4),
                "condition":     condition,
                "tv_alert_name": make_name(alert_type, price_level),
                "scan_date":     scan_date,
                "expires_at":    expires_at,
                "notes":         notes or f"{setup} final_score={row['score']}",
            })

        entry_low  = row["entry_low"]
        entry_high = row["entry_high"]
        stop_loss  = row["stop_loss"]
        t1         = row["t1"]
        t2         = row["t2"]

        # ENTRY_LONG at entry_low — trigger when price crosses up to entry zone
        add("ENTRY_LONG",   entry_low,  "crossing",
            f"Enter long if price crosses {entry_low:.2f} (entry zone low)")
        # ENTRY_LONG at entry_high — still within range (price below ceiling)
        add("ENTRY_LONG",   entry_high, "less_than",
            f"Entry ceiling {entry_high:.2f} — price still in range")
        # STOP_LOSS
        add("STOP_LOSS",    stop_loss,  "less_than",
            f"Stop loss at {stop_loss:.2f}")
        # TARGET_1
        add("TARGET_1",     t1,         "greater_than",
            f"Target 1 at {t1:.2f} (RR1={row['rr1']:.2f}x)")
        # TARGET_2 — only if set
        if t2 and t2 > 0:
            add("TARGET_2", t2, "greater_than",
                f"Target 2 at {t2:.2f} (RR2={row['rr2']:.2f}x)")

    db.close()
    return {
        "targets": targets,
        "n_picks": len(rows),
        "scan_date": scan_date,
        "expires_at": expires_at,
        "source": "final_signals.actionable",
    }


def cmd_log_created(params):
    """
    Save created alerts to alert_log with status='ACTIVE'.
    """
    alerts = params.get("alerts", [])
    if not alerts:
        return {"n_logged": 0, "created_at": now_str(), "error": "no alerts provided"}

    db = get_db()
    ensure_table(db)

    created_at = now_str()
    n_logged = 0

    for a in alerts:
        try:
            db.execute("""
                INSERT INTO alert_log
                    (symbol, alert_type, price_level, condition, tv_alert_name,
                     scan_date, created_at, expires_at, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
            """, (
                a.get("symbol"),
                a.get("alert_type"),
                a.get("price_level"),
                a.get("condition"),
                a.get("tv_alert_name"),
                a.get("scan_date"),
                created_at,
                a.get("expires_at"),
                a.get("notes", ""),
            ))
            n_logged += 1
        except sqlite3.Error as e:
            # Log but continue; don't abort the batch
            sys.stderr.write(f"[alert_log] insert error for {a.get('symbol')}: {e}\n")

    db.commit()
    db.close()
    return {"n_logged": n_logged, "created_at": created_at}


def cmd_list_active(params):
    """
    Return all (or per-symbol) active alerts grouped by symbol.
    """
    symbol = params.get("symbol")

    db = get_db()
    ensure_table(db)

    if symbol:
        rows = db.execute("""
            SELECT * FROM alert_log WHERE symbol = ?
            ORDER BY symbol, alert_type
        """, (symbol,)).fetchall()
    else:
        rows = db.execute("""
            SELECT * FROM alert_log
            ORDER BY symbol, alert_type
        """).fetchall()

    alerts = [dict(r) for r in rows]
    by_status = collections.Counter(a["status"] for a in alerts)

    # Group by symbol for convenience
    by_symbol = collections.defaultdict(list)
    for a in alerts:
        if a["status"] == "ACTIVE":
            by_symbol[a["symbol"]].append(a)

    db.close()
    return {
        "alerts":    alerts,
        "n_active":  by_status.get("ACTIVE", 0),
        "n_expired": by_status.get("EXPIRED", 0),
        "n_total":   len(alerts),
        "by_symbol": dict(by_symbol),
    }


def cmd_sync_status(params):
    """
    Mark alerts as EXPIRED where expires_at < today AND status='ACTIVE'.
    """
    today = params.get("today", today_str())

    db = get_db()
    ensure_table(db)

    cur = db.execute("""
        UPDATE alert_log
        SET status = 'EXPIRED'
        WHERE status = 'ACTIVE'
          AND expires_at IS NOT NULL
          AND expires_at < ?
    """, (today,))
    n_expired = cur.rowcount

    n_still_active = db.execute(
        "SELECT COUNT(*) FROM alert_log WHERE status = 'ACTIVE'"
    ).fetchone()[0]

    db.commit()
    db.close()
    return {"n_expired": n_expired, "n_still_active": n_still_active, "synced_as_of": today}


def cmd_clear_expired(params):
    """
    Delete EXPIRED/DELETED alerts older than before_date.
    """
    before_date = params.get("before_date")
    if not before_date:
        return {"error": "before_date is required", "n_deleted": 0}

    db = get_db()
    ensure_table(db)

    cur = db.execute("""
        DELETE FROM alert_log
        WHERE status IN ('EXPIRED', 'DELETED')
          AND created_at < ?
    """, (before_date,))
    n_deleted = cur.rowcount

    db.commit()
    db.close()
    return {"n_deleted": n_deleted, "before_date": before_date}


def cmd_summary(params):
    """
    Return aggregate stats for the last N days.
    """
    days = int(params.get("days", 30))
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    db = get_db()
    ensure_table(db)

    total_created_30d = db.execute(
        "SELECT COUNT(*) FROM alert_log WHERE created_at >= ?", (since,)
    ).fetchone()[0]

    n_active = db.execute(
        "SELECT COUNT(*) FROM alert_log WHERE status = 'ACTIVE'"
    ).fetchone()[0]

    n_expired = db.execute(
        "SELECT COUNT(*) FROM alert_log WHERE status = 'EXPIRED'"
    ).fetchone()[0]

    # Top symbols by alert count (all-time)
    top_rows = db.execute("""
        SELECT symbol, COUNT(*) AS cnt
        FROM alert_log
        GROUP BY symbol
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    top_symbols = [{"symbol": r["symbol"], "count": r["cnt"]} for r in top_rows]

    # Alert type distribution (all-time)
    dist_rows = db.execute("""
        SELECT alert_type, COUNT(*) AS cnt
        FROM alert_log
        GROUP BY alert_type
        ORDER BY cnt DESC
    """).fetchall()
    alert_type_distribution = {r["alert_type"]: r["cnt"] for r in dist_rows}

    db.close()
    return {
        "total_created_30d":      total_created_30d,
        "n_active":               n_active,
        "n_expired":              n_expired,
        "top_symbols":            top_symbols,
        "alert_type_distribution": alert_type_distribution,
        "since":                  since,
    }


def cmd_build_full(params):
    """
    Convenience: sync_status + get_alert_targets for today.
    Returns targets_to_create, synced_status, and current_active count.
    """
    scan_date = params.get("scan_date", today_str())
    min_score = params.get("min_score", 65)

    synced = cmd_sync_status({"today": scan_date})
    targets_result = cmd_get_alert_targets({
        "scan_date": scan_date,
        "min_score": min_score,
        "max_picks": params.get("max_picks", 10),
        "alert_days_valid": params.get("alert_days_valid", 5),
    })
    current_active = synced["n_still_active"]

    return {
        "targets_to_create": targets_result["targets"],
        "n_picks":           targets_result["n_picks"],
        "synced_status":     synced,
        "current_active":    current_active,
        "scan_date":         scan_date,
    }

# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "get_alert_targets": cmd_get_alert_targets,
    "log_created":       cmd_log_created,
    "list_active":       cmd_list_active,
    "sync_status":       cmd_sync_status,
    "clear_expired":     cmd_clear_expired,
    "summary":           cmd_summary,
    "build_full":        cmd_build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "Usage: alert_automation.py <command> [params_json]",
            "commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    command = sys.argv[1]
    if command not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {command}", "commands": list(COMMANDS.keys())}))
        sys.exit(1)

    raw_params = sys.argv[2] if len(sys.argv) > 2 else "{}"
    try:
        params = json.loads(raw_params)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON params: {e}"}))
        sys.exit(1)

    try:
        result = COMMANDS[command](params)
        print(json.dumps(result, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "command": command}))
        sys.exit(1)


if __name__ == "__main__":
    main()
