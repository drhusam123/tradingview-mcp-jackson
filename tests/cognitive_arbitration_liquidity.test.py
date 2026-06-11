#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import sqlite3
from cognitive_arbitration import read_liquidity_profile, _normalize_liquidity_tier, DB_PATH


def test_tier_normalize():
    assert _normalize_liquidity_tier("TIER1") == "DEEP"
    assert _normalize_liquidity_tier("DEEP") == "DEEP"


def test_read_liquidity_mpci():
    db = sqlite3.connect(DB_PATH)
    liq = read_liquidity_profile(db, "MPCI")
    db.close()
    assert liq["tier"] in ("DEEP", "MID"), liq
    assert liq["avg_daily_volume"] > 1_000_000


if __name__ == "__main__":
    test_tier_normalize()
    test_read_liquidity_mpci()
    print("cognitive_arbitration_liquidity_ok")
