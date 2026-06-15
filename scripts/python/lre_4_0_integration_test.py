#!/usr/bin/env python3
"""
LRE-4.0 integration test — before/after opportunity_score_v2 with research feed.

Verifies:
- final_signals.actionable unchanged
- LRE boost only on feed symbols, max +3
- feed table populated
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "lre_4_0_integration_test_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _opp_snapshot(db: sqlite3.Connection, trade_date: str) -> dict:
    rows = db.execute(
        """
        SELECT symbol, opportunity_score, stage, flags_json
        FROM opportunity_score_v2 WHERE trade_date=?
        """,
        (trade_date,),
    ).fetchall()
    return {r["symbol"]: dict(r) for r in rows}


def _actionable_snapshot(db: sqlite3.Connection, trade_date: str) -> dict:
    rows = db.execute(
        """
        SELECT symbol, actionable, score, veto_reason
        FROM final_signals WHERE trade_date=?
        """,
        (trade_date,),
    ).fetchall()
    actionable = {r["symbol"] for r in rows if int(r["actionable"] or 0) == 1}
    return {"actionable_symbols": sorted(actionable), "count": len(actionable)}


def run(params: dict | None = None) -> dict:
    params = params or {}
    from lre_4_0_research_feed import run as feed_run
    import opportunity_score_v2 as opp

    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM opportunity_score_v2").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        db.close()
        return {"success": False, "error": "no_trade_date"}

    os.environ["EGX_LRE_FEED_BOOST"] = "0"
    opp.run({"trade_date": trade_date})
    before_opp = _opp_snapshot(db, trade_date)
    before_actionable = _actionable_snapshot(db, trade_date)

    feed_result = feed_run({"trade_date": trade_date, "refresh_shadow": False})

    os.environ["EGX_LRE_FEED_BOOST"] = "1"
    opp.run({"trade_date": trade_date})
    after_opp = _opp_snapshot(db, trade_date)
    after_actionable = _actionable_snapshot(db, trade_date)

    feed_rows = db.execute(
        "SELECT symbol, feed_tier, opp_boost_points, pilot_eligible FROM lre_research_feed_daily WHERE signal_date=?",
        (trade_date,),
    ).fetchall()
    feed_map = {r["symbol"]: dict(r) for r in feed_rows}

    deltas = []
    for sym in set(before_opp) | set(after_opp):
        b = before_opp.get(sym, {})
        a = after_opp.get(sym, {})
        bscore = float(b.get("opportunity_score") or 0)
        ascore = float(a.get("opportunity_score") or 0)
        delta = round(ascore - bscore, 3)
        if abs(delta) > 0.001:
            deltas.append({
                "symbol": sym,
                "before": bscore,
                "after": ascore,
                "delta": delta,
                "feed_tier": (feed_map.get(sym) or {}).get("feed_tier"),
                "expected_boost": float((feed_map.get(sym) or {}).get("opp_boost_points") or 0),
            })

    max_delta = max((abs(d["delta"]) for d in deltas), default=0)
    unexpected = [
        d for d in deltas
        if abs(d["delta"] - d["expected_boost"]) > 0.15 and d["expected_boost"] > 0
    ]
    non_feed_delta = [d for d in deltas if d["symbol"] not in feed_map or d["expected_boost"] <= 0]

    actionable_unchanged = before_actionable == after_actionable
    boost_ok = max_delta <= 3.01
    feed_ok = feed_result.get("success") and feed_result.get("feed_rows", 0) > 0

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "feed": feed_result,
        "before_actionable_count": before_actionable["count"],
        "after_actionable_count": after_actionable["count"],
        "actionable_unchanged": actionable_unchanged,
        "feed_symbols": len(feed_map),
        "opp_deltas_count": len(deltas),
        "max_opp_delta": max_delta,
        "boost_within_cap": boost_ok,
        "unexpected_boost_mismatch": unexpected[:10],
        "non_feed_deltas": non_feed_delta[:10],
        "top_deltas": sorted(deltas, key=lambda x: -abs(x["delta"]))[:15],
        "verdict": (
            "PASS_LRE_4_0_INTEGRATION"
            if actionable_unchanged and boost_ok and feed_ok and not non_feed_delta
            else "FAIL_LRE_4_0_INTEGRATION"
        ),
        "invariants": {
            "CLIENT_SIGNAL": 0,
            "actionable_unchanged": actionable_unchanged,
            "max_boost_cap": 3.0,
        },
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    db.close()
    print(json.dumps({"verdict": payload["verdict"], "feed_rows": feed_result.get("feed_rows")}))
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    run(p)
