#!/usr/bin/env python3
"""Portfolio import_signals open-position dedup tests."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from portfolio_tracker import ensure_tables, import_gate_passed_signals


def _temp_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unified_signals (
            symbol TEXT,
            entry_price REAL,
            entry_high REAL,
            stop_loss REAL,
            t1_target REAL,
            t2_target REAL,
            unified_score REAL,
            active_regime TEXT,
            behavioral_class TEXT,
            signal_date TEXT,
            quality_gate_passed INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_quality_flags (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            issue_type TEXT
        )
    """)
    conn.execute("""
        INSERT INTO unified_signals
        (symbol, entry_price, entry_high, stop_loss, t1_target, t2_target,
         unified_score, active_regime, behavioral_class, signal_date, quality_gate_passed)
        VALUES ('TEST', 10.0, 10.1, 9.5, 11.0, 12.0, 80.0, 'BULL', 'STEADY', '2026-06-14', 1)
    """)
    return conn


def test_import_skips_when_symbol_already_open():
    conn = _temp_db()
    conn.execute("""
        INSERT INTO portfolio_positions
        (symbol, entry_date, entry_price, shares, position_egp, status, signal_date)
        VALUES ('TEST', '2026-06-10', 9.8, 1000, 9800, 'OPEN', '2026-06-10')
    """)
    conn.commit()

    imported = import_gate_passed_signals(conn, date='2026-06-14')
    assert imported == [], f"expected no import, got {imported}"

    n_open = conn.execute(
        "SELECT COUNT(*) n FROM portfolio_positions WHERE symbol='TEST' AND status='OPEN'"
    ).fetchone()[0]
    assert n_open == 1


def test_import_allows_when_prior_position_closed():
    conn = _temp_db()
    conn.execute("""
        INSERT INTO portfolio_positions
        (symbol, entry_date, entry_price, shares, position_egp, status, signal_date)
        VALUES ('TEST', '2026-06-10', 9.8, 1000, 9800, 'CLOSED_T1', '2026-06-10')
    """)
    conn.commit()

    imported = import_gate_passed_signals(conn, date='2026-06-14')
    assert len(imported) == 1
    assert imported[0]['symbol'] == 'TEST'


if __name__ == "__main__":
    test_import_skips_when_symbol_already_open()
    test_import_allows_when_prior_position_closed()
    print("portfolio_import_dedup: OK")
