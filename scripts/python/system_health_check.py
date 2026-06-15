#!/usr/bin/env python3
"""Institutional system health check — single-command PASS/WARN/FAIL."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUTPUT = ROOT / "data" / "system_health_last.json"
LOGS = ROOT / "logs"

P0_IDS = {
    "db_exists", "db_integrity", "db_migrations", "env_readiness",
    "automation_verify",
}
P1_IDS = {
    "ohlcv_freshness", "final_signals_freshness", "delivery_reconcile",
    "gate_snapshots", "prod_ready", "audit_closed",
}


def _read_json(name: str) -> dict | None:
    p = ROOT / "data" / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _cairo_date() -> str:
    try:
        raw = subprocess.run(
            [sys.executable, str(ROOT / "scripts/python/event_calendar.py"), "cairo_today", "{}"],
            capture_output=True, text=True, timeout=15, cwd=str(ROOT),
        )
        if raw.returncode == 0:
            return json.loads(raw.stdout).get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _staleness_days(data_date: str | None, ref: str) -> int | None:
    if not data_date:
        return None
    try:
        d0 = datetime.strptime(str(data_date)[:10], "%Y-%m-%d")
        d1 = datetime.strptime(ref[:10], "%Y-%m-%d")
        return (d1 - d0).days
    except ValueError:
        return None


def _env_keys_present() -> tuple[bool, str]:
    keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    found = []
    missing = []
    for k in keys:
        v = os.environ.get(k, "")
        if not v and (ROOT / ".env").exists():
            for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(f"{k}="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if v:
            found.append(k)
        else:
            missing.append(k)
    ok = len(missing) == 0
    return ok, f"set={','.join(found) or 'none'} missing={','.join(missing) or 'none'}"


def _lock_conflicts() -> tuple[bool, str]:
    if not LOGS.exists():
        return True, "no logs dir"
    stale = []
    now = datetime.now(timezone.utc).timestamp()
    for p in LOGS.glob("*.lock"):
        age_h = (now - p.stat().st_mtime) / 3600
        if age_h > 6:
            stale.append(f"{p.name}({age_h:.0f}h)")
    if stale:
        return False, f"stale locks: {', '.join(stale[:5])}"
    return True, "no stale locks"


def _run_automation_verify(quick: bool) -> tuple[bool, str]:
    if quick:
        return True, "skipped (--quick)"
    node = os.environ.get("NODE_BIN") or "node"
    try:
        proc = subprocess.run(
            [node, str(ROOT / "scripts/egx_automation_verify.mjs")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        )
        tail = (proc.stdout or proc.stderr or "").strip().split("\n")[-1]
        return proc.returncode == 0, tail[:200]
    except Exception as e:
        return False, str(e)[:120]


def run(params: dict | None = None) -> dict:
    params = params or {}
    quick = bool(params.get("quick") or "--quick" in sys.argv)
    ref_date = _cairo_date()
    checks: list[dict] = []
    failures: list[str] = []
    warnings: list[str] = []
    next_actions: list[str] = []

    def add(cid: str, ok: bool, detail: str, *, severity: str = "info", warn_only: bool = False):
        checks.append({"id": cid, "ok": ok, "detail": detail, "severity": severity})
        if not ok:
            if warn_only or cid not in P0_IDS:
                warnings.append(f"{cid}: {detail}")
            else:
                failures.append(f"{cid}: {detail}")

    # ── P0: DB ──────────────────────────────────────────────────────────
    db_ok = DB_PATH.exists()
    add("db_exists", db_ok, str(DB_PATH) if db_ok else "MISSING")

    if db_ok:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.row_factory = sqlite3.Row
        try:
            ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
            add("db_integrity", ic == "ok", ic)

            mig_rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall() if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone() else []
            mig_names = [r[0] for r in mig_rows] if mig_rows else []
            mig_ok = len(mig_names) >= 7
            add("db_migrations", mig_ok, f"{len(mig_names)} applied" + (f" latest={mig_names[-1]}" if mig_names else ""))

            ohlcv_latest = conn.execute(
                "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history"
            ).fetchone()["d"]
            lag = _staleness_days(ohlcv_latest, ref_date)
            ohlcv_ok = lag is not None and lag <= 3
            add("ohlcv_freshness", ohlcv_ok, f"latest={ohlcv_latest} lag_days={lag}", warn_only=lag == 2)

            ic_latest = None
            try:
                ic_latest = conn.execute(
                    "SELECT MAX(bar_date) d FROM indicators_cache WHERE bar_date NOT LIKE '2099-%'"
                ).fetchone()["d"]
            except sqlite3.Error:
                pass
            ic_lag = _staleness_days(ic_latest, ref_date)
            add("indicators_freshness", ic_lag is not None and ic_lag <= 4,
                f"latest={ic_latest} lag_days={ic_lag}", warn_only=True)

            pred_latest = None
            try:
                pred_latest = conn.execute("SELECT MAX(pred_date) d FROM explosion_predictions").fetchone()["d"]
            except sqlite3.Error:
                pass
            pred_lag = _staleness_days(pred_latest, ref_date)
            add("ml_pred_freshness", pred_lag is not None and pred_lag <= 4,
                f"latest={pred_latest} lag_days={pred_lag}", warn_only=True)

            fs_latest = None
            try:
                fs_latest = conn.execute("SELECT MAX(trade_date) d FROM final_signals").fetchone()["d"]
            except sqlite3.Error:
                try:
                    fs_latest = conn.execute("SELECT MAX(signal_date) d FROM final_signals").fetchone()["d"]
                except sqlite3.Error:
                    pass
            fs_lag = _staleness_days(fs_latest, ref_date)
            add("final_signals_freshness", fs_lag is not None and fs_lag <= 3,
                f"latest={fs_latest} lag_days={fs_lag}")

            gate_n = 0
            try:
                gate_n = conn.execute("SELECT COUNT(*) n FROM gate_audit_snapshots").fetchone()["n"]
            except sqlite3.Error:
                pass
            add("gate_snapshots", gate_n > 0, f"rows={gate_n}")

            act_n = 0
            if fs_latest:
                try:
                    act_n = conn.execute(
                        "SELECT COUNT(*) n FROM final_signals WHERE trade_date=? AND actionable=1",
                        (fs_latest,),
                    ).fetchone()["n"]
                except sqlite3.Error:
                    try:
                        act_n = conn.execute(
                            "SELECT COUNT(*) n FROM final_signals WHERE signal_date=? AND actionable=1",
                            (fs_latest,),
                        ).fetchone()["n"]
                    except sqlite3.Error:
                        pass
            add("actionable_count", True, f"date={fs_latest} actionable={act_n}", warn_only=True)

            pending = 0
            try:
                pending = conn.execute("""
                    SELECT COUNT(DISTINCT d.signal_date) n
                    FROM notification_delivery_audit d
                    WHERE d.deliverable = 1
                      AND d.dry_run = 0
                      AND d.signal_date >= date('now', '-3 days')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM notification_delivery_audit s
                        WHERE s.signal_date = d.signal_date
                          AND s.send_success = 1
                          AND s.dry_run = 0
                      )
                """).fetchone()["n"]
            except sqlite3.Error:
                pass
            add("delivery_reconcile", pending == 0, f"pending_sends_3d={pending}", warn_only=True)
        finally:
            conn.close()
    else:
        for cid in ("db_integrity", "db_migrations", "ohlcv_freshness", "final_signals_freshness",
                    "gate_snapshots", "delivery_reconcile"):
            add(cid, False, "no database")

    # ── Artifacts ───────────────────────────────────────────────────────
    prod = _read_json("prod_ready_last.json")
    add("prod_ready", bool(prod and prod.get("pass")), prod.get("at", "not run") if prod else "missing", warn_only=True)

    audit = _read_json("audit_close_last.json")
    add("audit_closed", bool(audit and audit.get("audit_closed")),
        audit.get("verdict", "missing") if audit else "missing", warn_only=True)

    engine_status = []
    for fname in ("lre_4_0_status_last.json", "med_0_3_status_last.json", "mde_pilot_shadow_last.json"):
        j = _read_json(fname)
        engine_status.append(f"{fname.split('_')[0]}={'ok' if j else 'missing'}")
    add("engine_last_runs", any(_read_json(f) for f in (
        "lre_4_0_status_last.json", "med_0_3_status_last.json"
    )), "; ".join(engine_status), warn_only=True)

    # env + locks + verify
    env_ok, env_detail = _env_keys_present()
    add("env_readiness", env_ok, env_detail)

    lock_ok, lock_detail = _lock_conflicts()
    add("lock_conflicts", lock_ok, lock_detail, warn_only=True)

    reg_path = ROOT / "scripts/lib/discovery_engine_registry.mjs"
    reg_ok = reg_path.exists() and "med_daily_chain" in reg_path.read_text(encoding="utf-8", errors="replace")
    add("registry_complete", reg_ok, "discovery_engine_registry.mjs", warn_only=True)

    av_ok, av_detail = _run_automation_verify(quick)
    add("automation_verify", av_ok, av_detail)

    # ── Verdict ─────────────────────────────────────────────────────────
    p0_fail = [c for c in checks if c["id"] in P0_IDS and not c["ok"]]
    p1_fail = [c for c in checks if c["id"] in P1_IDS and not c["ok"]]

    if p0_fail:
        status = "FAIL"
        next_actions.append("Fix P0: " + ", ".join(c["id"] for c in p0_fail))
    elif p1_fail or warnings:
        status = "WARN"
        if p1_fail:
            next_actions.append("Review P1: " + ", ".join(c["id"] for c in p1_fail))
    else:
        status = "PASS"

    payload = {
        "status": status,
        "health": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ref_date": ref_date,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "next_actions": next_actions,
        "quick_mode": quick,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1 and sys.argv[1].startswith("{"):
        p = json.loads(sys.argv[1])
    result = run(p)
    if "--json" in sys.argv or os.environ.get("HEALTH_JSON") == "1":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\nHEALTH: {result['status']}")
        print(f"  Checks: {sum(1 for c in result['checks'] if c['ok'])}/{len(result['checks'])} OK")
        if result["failures"]:
            print("  Failures:")
            for f in result["failures"]:
                print(f"    • {f}")
        if result["warnings"]:
            print("  Warnings:")
            for w in result["warnings"][:8]:
                print(f"    • {w}")
        print(f"  Saved: {OUTPUT}\n")
    sys.exit(0 if result["status"] != "FAIL" else 1)
