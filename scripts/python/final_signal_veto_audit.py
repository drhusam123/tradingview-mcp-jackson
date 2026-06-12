#!/usr/bin/env python3
"""
Final Signal Veto Audit
=======================

Checks whether final_signals vetoes historically blocked weak outcomes or also
blocked profitable discovery candidates.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())


def load_bars(conn):
    table = "ohlcv_history_execution" if table_exists(conn, "ohlcv_history_execution") else "ohlcv_history_execution"
    rows = conn.execute(f"""
        SELECT symbol, date(bar_time,'unixepoch') d, close
        FROM {table}
        WHERE close IS NOT NULL AND close > 0
        ORDER BY symbol, bar_time
    """).fetchall()
    by = defaultdict(list)
    idx = {}
    for r in rows:
        by[r["symbol"]].append((r["d"], float(r["close"])))
    for sym, arr in by.items():
        idx[sym] = {d: i for i, (d, _) in enumerate(arr)}
    return by, idx


def fwd_return(by, idx, symbol, d, horizon):
    arr = by.get(symbol)
    pos = idx.get(symbol, {}).get(d)
    if arr is None or pos is None or pos + horizon >= len(arr):
        return None
    c0 = arr[pos][1]
    c1 = arr[pos + horizon][1]
    if c0 <= 0:
        return None
    return (c1 / c0) - 1.0


def bucket(row):
    if int(row["actionable"] or 0) == 1 and not row["veto_reason"]:
        return "ACTIONABLE"
    if row["veto_reason"]:
        return str(row["veto_reason"]).split("|")[0].strip()[:80] or "VETO"
    return "NON_ACTIONABLE_NO_VETO"


def summarize(items):
    vals1 = [x["ret1"] for x in items if x["ret1"] is not None]
    vals3 = [x["ret3"] for x in items if x["ret3"] is not None]
    vals5 = [x["ret5"] for x in items if x["ret5"] is not None]
    return {
        "n": len(items),
        "n_eval_5d": len(vals5),
        "win1": round(sum(v > 0 for v in vals1) / len(vals1), 3) if vals1 else None,
        "win3": round(sum(v > 0 for v in vals3) / len(vals3), 3) if vals3 else None,
        "win5": round(sum(v > 0 for v in vals5) / len(vals5), 3) if vals5 else None,
        "avg1": round(mean(vals1), 4) if vals1 else None,
        "avg3": round(mean(vals3), 4) if vals3 else None,
        "avg5": round(mean(vals5), 4) if vals5 else None,
    }


def run():
    conn = connect()
    by, idx = load_bars(conn)
    rows = conn.execute("""
        SELECT fs.*, COALESCE(o.opportunity_score, NULL) opportunity_score,
               COALESCE(o.stage, NULL) opportunity_stage
        FROM final_signals fs
        LEFT JOIN opportunity_score_v2 o
          ON o.symbol=fs.symbol AND o.trade_date=fs.trade_date
        ORDER BY fs.trade_date, fs.symbol
    """).fetchall() if table_exists(conn, "opportunity_score_v2") else conn.execute("SELECT * FROM final_signals").fetchall()

    enriched = []
    for r in rows:
        d = r["trade_date"]
        sym = r["symbol"]
        enriched.append({
            "symbol": sym,
            "trade_date": d,
            "bucket": bucket(r),
            "actionable": int(r["actionable"] or 0),
            "score": float(r["score"] or 0),
            "opportunity_score": float(r["opportunity_score"]) if "opportunity_score" in r.keys() and r["opportunity_score"] is not None else None,
            "opportunity_stage": r["opportunity_stage"] if "opportunity_stage" in r.keys() else None,
            "ret1": fwd_return(by, idx, sym, d, 1),
            "ret3": fwd_return(by, idx, sym, d, 3),
            "ret5": fwd_return(by, idx, sym, d, 5),
        })

    by_bucket = defaultdict(list)
    for x in enriched:
        by_bucket[x["bucket"]].append(x)

    bucket_summary = {
        k: summarize(v) for k, v in sorted(by_bucket.items(), key=lambda kv: len(kv[1]), reverse=True)
    }

    blocked_discovery = [
        x for x in enriched
        if x["actionable"] == 0 and x["opportunity_score"] is not None and x["opportunity_score"] >= 75
    ]

    false_block_5d = [
        x for x in blocked_discovery
        if x["ret5"] is not None and x["ret5"] > 0.03
    ]

    blocked_summary = summarize(blocked_discovery)
    enough_5d = (blocked_summary.get("n_eval_5d") or 0) >= 20
    enough_3d = len([x for x in blocked_discovery if x["ret3"] is not None]) >= 20
    if enough_5d:
        decision = (
            "VETO_TOO_STRICT_REVIEW_REQUIRED"
            if len(false_block_5d) >= 5 and blocked_summary.get("win5", 0) and blocked_summary["win5"] >= 0.55
            else "VETO_ACCEPTABLE_KEEP_RESEARCH_ONLY"
        )
    elif enough_3d:
        decision = (
            "VETO_TOO_STRICT_REVIEW_REQUIRED_3D"
            if blocked_summary.get("win3", 0) and blocked_summary["win3"] >= 0.58 and (blocked_summary.get("avg3") or 0) > 0.01
            else "VETO_ACCEPTABLE_KEEP_RESEARCH_ONLY_3D"
        )
    else:
        decision = "VETO_AUDIT_INCONCLUSIVE_NEEDS_MORE_FORWARD_DAYS"

    out = {
        "success": True,
        "signals": len(enriched),
        "bucket_summary": bucket_summary,
        "blocked_discovery": blocked_summary,
        "blocked_discovery_big_winners_5d": len(false_block_5d),
        "top_false_blocks": sorted(false_block_5d, key=lambda x: x["ret5"], reverse=True)[:15],
        "decision": decision,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd != "run":
        raise SystemExit(f"Unknown command: {cmd}")
    run()
