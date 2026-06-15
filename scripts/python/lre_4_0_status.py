#!/usr/bin/env python3
"""
LRE-4.0 status — research feed health + forward OOS graduation tracker.

Shadow only. Summarizes feed, dual-gate, 3.6B ledger, and graduation criteria.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "lre_4_0_status_last.json"

PHASE = "LRE-4.0"
FORWARD_START = "2026-06-12"
GRADUATION = {
    "min_live_oos_trades": 40,
    "min_pf_100bps": 1.3,
    "max_top10_dominance_pct": 35.0,
    "client_path_allowed": False,
}


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _tier_counts(conn: sqlite3.Connection, trade_date: str) -> Dict[str, int]:
    rows = conn.execute(
        """
        SELECT feed_tier, COUNT(*) n
        FROM lre_research_feed_daily WHERE signal_date=?
        GROUP BY feed_tier ORDER BY n DESC
        """,
        (trade_date,),
    ).fetchall()
    return {r["feed_tier"]: r["n"] for r in rows}


def _forward_metrics(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT forward_return_20d, symbol, sector, feed_tier, trade_date
        FROM lre_forward_shadow_ledger
        WHERE forward_return_20d IS NOT NULL
        ORDER BY trade_date
        """
    ).fetchall()
    if not rows:
        return {"n_closed": 0, "note": "no_forward_outcomes_yet"}
    rets = [float(r["forward_return_20d"]) for r in rows]
    wins = [r for r in rets if r > 0]
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    sym_counts: Dict[str, int] = {}
    for r in rows:
        sym_counts[r["symbol"]] = sym_counts.get(r["symbol"], 0) + 1
    top = sorted(sym_counts.items(), key=lambda x: -x[1])[:10]
    top_n = sum(c for _, c in top)
    dom = (top_n / len(rows) * 100) if rows else 0
    return {
        "n_closed": len(rows),
        "win_rate_pct": round(len(wins) / len(rets) * 100, 1),
        "median_return_20d": round(median(rets), 2),
        "mean_return_20d": round(mean(rets), 2),
        "pf_100bps_proxy": round(pf, 2) if pf is not None else None,
        "top10_dominance_pct": round(dom, 1),
        "top_symbols": top[:5],
    }


def _graduation_check(forward: dict, wf: Optional[dict]) -> dict:
    n = int(forward.get("n_closed") or 0)
    pf = forward.get("pf_100bps_proxy")
    dom = forward.get("top10_dominance_pct")
    checks = {
        "live_oos_trades_40": n >= GRADUATION["min_live_oos_trades"],
        "pf_ge_1_3": pf is not None and pf >= GRADUATION["min_pf_100bps"],
        "dominance_lt_35": dom is not None and dom < GRADUATION["max_top10_dominance_pct"],
        "client_path_blocked": True,
    }
    hist_pf = None
    hist_dom = None
    if wf:
        primary = (wf.get("primary") or {}).get("metrics") or {}
        hist_pf = primary.get("PF_100bps") or primary.get("net_PF")
        hist_dom = primary.get("top10_dominance_pct")
    ready = all(checks.values())
    return {
        "ready_for_client_graduation": ready,
        "checks": checks,
        "progress": {
            "live_oos_closed": n,
            "target_oos": GRADUATION["min_live_oos_trades"],
            "live_pf_proxy": pf,
            "live_dominance_pct": dom,
            "historical_wf_pf_100": hist_pf,
            "historical_wf_dominance_pct": hist_dom,
        },
        "verdict": (
            "GRADUATION_READY" if ready
            else "ACCUMULATING_LIVE_OOS" if n < GRADUATION["min_live_oos_trades"]
            else "LIVE_OOS_NEEDS_QUALITY"
        ),
    }


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(signal_date) d FROM lre_research_feed_daily").fetchone()
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    feed_n = 0
    max_boost = 0.0
    pilot_eligible = 0
    confluence_n = 0
    if trade_date:
        feed_n = conn.execute(
            "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=?",
            (trade_date,),
        ).fetchone()["n"]
        max_boost = float(conn.execute(
            "SELECT MAX(opp_boost_points) m FROM lre_research_feed_daily WHERE signal_date=?",
            (trade_date,),
        ).fetchone()["m"] or 0)
        pilot_eligible = conn.execute(
            "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=? AND pilot_eligible=1",
            (trade_date,),
        ).fetchone()["n"]
        confluence_n = conn.execute(
            """
            SELECT COUNT(*) n FROM lre_research_feed_daily
            WHERE signal_date=? AND dual_gate_type='LRE_MDE_CONFLUENCE'
            """,
            (trade_date,),
        ).fetchone()["n"]

    open_fwd = conn.execute(
        "SELECT COUNT(*) n FROM lre_forward_shadow_ledger WHERE exit_status='open'"
    ).fetchone()["n"]
    total_fwd = conn.execute("SELECT COUNT(*) n FROM lre_forward_shadow_ledger").fetchone()["n"]

    dual = _read_json(DATA / "lre_dual_gate_daily_last.json")
    integ = _read_json(DATA / "lre_4_0_integration_test_last.json")
    accept = _read_json(DATA / "lre_4_0_acceptance_last.json")
    fwd_last = _read_json(DATA / "lre_3_6b_forward_shadow_last.json")
    wf = _read_json(DATA / "lre_3_6a_walk_forward_results.json")
    manifest = _read_json(DATA / "discovery_lre_manifest.json")

    forward = _forward_metrics(conn)
    tier_counts = _tier_counts(conn, trade_date) if trade_date else {}
    graduation = _graduation_check(forward, wf)
    conn.close()

    automation = {
        "pipeline_wired": True,
        "invariants": {
            "EGX_LRE_SHADOW": "1",
            "EGX_LRE_OPP_BOOST": "0",
            "EGX_LRE_FEED_BOOST": "1",
            "client_path_allowed": False,
        },
        "integration_verdict": (integ or {}).get("verdict"),
        "acceptance_verdict": (accept or {}).get("verdict"),
        "actionable_unchanged": (integ or {}).get("actionable_unchanged"),
    }

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": PHASE,
        "trade_date": trade_date,
        "forward_start": FORWARD_START,
        "feed": {
            "rows": feed_n,
            "tier_counts": tier_counts,
            "max_opp_boost": max_boost,
            "pilot_eligible": pilot_eligible,
            "confluence_symbols": confluence_n,
        },
        "dual_gate_daily": {
            "success": (dual or {}).get("success"),
            "trade_date": (dual or {}).get("trade_date"),
            "confluence_count": (dual or {}).get("confluence_count"),
            "confluence_symbols": (dual or {}).get("confluence_symbols", [])[:10],
        },
        "forward_shadow": {
            "last_run": fwd_last,
            "forward_window_active": trade_date >= FORWARD_START if trade_date else False,
            "open_positions": open_fwd,
            "total_entries": total_fwd,
            "metrics": forward,
        },
        "walk_forward_baseline": {
            "verdict": (wf or {}).get("verdict", "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED"),
            "primary_capped_pf_100": (
                ((wf or {}).get("primary") or {}).get("metrics") or {}
            ).get("PF_100bps"),
            "primary_top10_dominance_pct": (
                ((wf or {}).get("primary") or {}).get("metrics") or {}
            ).get("top10_dominance_pct"),
            "primary_trade_count": (
                ((wf or {}).get("primary") or {}).get("metrics") or {}
            ).get("trade_count"),
        },
        "graduation": graduation,
        "automation": automation,
        "manifest_trade_date": (manifest or {}).get("trade_date"),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return payload


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    run(p)
