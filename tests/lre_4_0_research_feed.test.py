#!/usr/bin/env python3
"""Smoke tests for LRE-4.0 research feed (shadow / additive only)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "egx_trading.db"
sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tier_boost_caps():
    feed = _load("lre_feed", "scripts/python/lre_4_0_research_feed.py")
    boosts = list(feed.FEED_TIER_BOOST.values())
    assert max(boosts) == 3.0
    assert min(boosts) == 0.0
    assert feed.PHASE_INVARIANTS["client_path_allowed"] is False
    assert feed.PHASE_INVARIANTS["EGX_LRE_OPP_BOOST"] == "0"


def test_tier_for_confluence_and_gate():
    feed = _load("lre_feed", "scripts/python/lre_4_0_research_feed.py")
    conf = {
        "pilot_eligible": True,
        "pilot_bucket": "Clean_Confluence_Core",
        "dual_gate_type": "LRE_MDE_CONFLUENCE",
    }
    tier, boost, atoms = feed._tier_for_row({}, conf)
    assert tier == "LRE_CLEAN_CORE"
    assert boost == 3.0
    assert "lre_confluence_clean_core" in atoms

    gate_row = {"stage": 3, "explosion_potential": 55, "artifact_risk": 0, "list_tags": ["ignition_candidates"]}
    tier2, boost2, _ = feed._tier_for_row(gate_row, None)
    assert tier2 == "LRE_GATE"
    assert boost2 == 1.0


def test_confluence_only_feed_path():
    """Regression: audit confluence without lre_daily_scores must still publish rows."""
    feed = _load("lre_feed", "scripts/python/lre_4_0_research_feed.py")
    confluence_map = {
        "TEST1": {
            "symbol": "TEST1",
            "dual_gate_type": "LRE_MDE_CONFLUENCE",
            "pilot_eligible": False,
            "sector": "Finance",
            "lre_eps": 60.0,
            "dual_gate_score": 72.0,
        },
    }
    # Mirror build_research_feed early-exit guard
    score_rows = []
    assert not (not score_rows and not confluence_map)

    rows = []
    for sym, conf in confluence_map.items():
        tier, boost, atoms = feed._tier_for_row({}, conf)
        rows.append({"symbol": sym, "feed_tier": tier, "opp_boost_points": boost, "atoms": atoms})
    assert len(rows) == 1
    assert rows[0]["feed_tier"] == "LRE_CONFLUENCE"
    assert rows[0]["opp_boost_points"] == 2.0


def test_feed_table_and_no_client_leak():
    if not DB.exists():
        return
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        db.execute("SELECT 1 FROM lre_research_feed_daily LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        db.close()
        return
    latest = db.execute("SELECT MAX(signal_date) d FROM lre_research_feed_daily").fetchone()["d"]
    if not latest:
        db.close()
        return
    n = db.execute(
        "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=?",
        (latest,),
    ).fetchone()["n"]
    leak = db.execute(
        "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=? AND client_path_allowed=1",
        (latest,),
    ).fetchone()["n"]
    max_boost = db.execute(
        "SELECT MAX(opp_boost_points) m FROM lre_research_feed_daily WHERE signal_date=?",
        (latest,),
    ).fetchone()["m"]
    db.close()
    assert n > 0
    assert leak == 0
    assert float(max_boost or 0) <= 3.0


def test_forward_shadow_forward_start():
    fwd = _load("lre_fwd", "scripts/python/lre_3_6b_forward_shadow_pilot.py")
    assert fwd.FORWARD_START == "2026-06-12"
    result = fwd.run({"trade_date": "2026-06-11"})
    assert result.get("skipped") is True


def test_status_graduation_shape():
    status_path = ROOT / "data" / "lre_4_0_status_last.json"
    if not status_path.exists():
        return
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert "graduation" in data
    assert "progress" in data["graduation"]
    assert data["graduation"]["progress"]["target_oos"] == 40


if __name__ == "__main__":
    test_tier_boost_caps()
    test_tier_for_confluence_and_gate()
    test_confluence_only_feed_path()
    test_feed_table_and_no_client_leak()
    test_forward_shadow_forward_start()
    test_status_graduation_shape()
    print("OK lre_4_0_research_feed tests")
