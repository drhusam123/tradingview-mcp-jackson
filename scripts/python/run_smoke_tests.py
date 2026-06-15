#!/usr/bin/env python3
"""Smoke tests without pytest — Intel Mac friendly."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{': ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("\n═══ Smoke Tests ═══\n")

    perf = ROOT / "config" / "performance.json"
    if perf.exists():
        cfg = json.loads(perf.read_text())
        check("performance_config", cfg.get("max_workers", 99) <= 4 and not cfg.get("enable_heavy_research"))
    else:
        check("performance_config", False, "missing")

    db = ROOT / "data" / "egx_trading.db"
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(str(db), timeout=30)
        check("db_integrity", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        conn.close()
    else:
        check("db_exists", False)

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/python/validate_market_data.py")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    val = ROOT / "data" / "market_data_validation_last.json"
    check("validate_market_data", proc.returncode in (0, 1) and val.exists())

    proc2 = subprocess.run(
        [sys.executable, str(ROOT / "scripts/python/system_health_check.py"), "--quick"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    check("health_check", proc2.returncode in (0, 1) and (ROOT / "data/system_health_last.json").exists())

    perf_env = ROOT / "scripts/lib/performance_config.mjs"
    check("performance_config_module", perf_env.exists())

    print(f"\n=== Smoke: {4 - len(FAILURES)}/4 OK ===\n")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
