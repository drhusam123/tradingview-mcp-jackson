"""Lightweight smoke tests — Intel Mac friendly, no GPU."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_performance_config_exists():
    p = ROOT / "config" / "performance.json"
    assert p.exists()
    cfg = json.loads(p.read_text())
    assert cfg["max_workers"] <= 4
    assert cfg["enable_heavy_research"] is False


def test_db_exists_and_integrity():
    db = ROOT / "data" / "egx_trading.db"
    assert db.exists()
    import sqlite3
    conn = sqlite3.connect(str(db), timeout=30)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_validate_market_data_runs():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/python/validate_market_data.py")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode in (0, 1)
    out = ROOT / "data" / "market_data_validation_last.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["status"] in ("PASS", "WARN", "FAIL")


def test_health_check_runs():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/python/system_health_check.py"), "--quick"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode in (0, 1)
    out = ROOT / "data" / "system_health_last.json"
    assert out.exists()
