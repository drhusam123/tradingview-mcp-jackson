#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))

from signal_integration import get_explosion_score


def test_prefers_on_or_before_signal_date():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE explosion_predictions (
            symbol TEXT, pred_date TEXT, prob_pct REAL, model_version TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE ml_governance_audit (
            run_date TEXT, accepted_for_client INTEGER, risk_level TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO explosion_predictions VALUES (?,?,?,?)",
        [
            ("TST", "2026-06-11", 72.0, "lgbm"),
            ("TST", "2026-06-12", 1.0, "ens"),
        ],
    )
    conn.commit()
    score = get_explosion_score("TST", "2026-06-11", conn)
    assert score == 72.0, f"expected 72 got {score}"


if __name__ == "__main__":
    test_prefers_on_or_before_signal_date()
    print("explosion_score_date_ok")
