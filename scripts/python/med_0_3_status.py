#!/usr/bin/env python3
"""MED-0.3 status — buckets, graduation, discovery feed health."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_3_status_last.json"

GRADUATION = {
    "min_live_closed": 40,
    "min_pf_100bps": 1.3,
    "max_top10_dominance_pct": 35.0,
    "feed_boost_env": "MED_FEED_BOOST",
    "client_signal_env": "MED_CLIENT_SIGNAL",
}


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _forward_live_metrics(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            """
            SELECT forward_return_20d, symbol, trade_date
            FROM med_forward_shadow_ledger
            WHERE forward_return_20d IS NOT NULL
              AND trade_date >= '2026-06-12'
            ORDER BY trade_date
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {"live_closed": 0, "note": "ledger_empty"}
    if not rows:
        return {"live_closed": 0, "note": "no_live_outcomes_yet"}
    rets = [float(r["forward_return_20d"]) for r in rows]
    wins = [r for r in rets if r > 0]
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    sym_counts: dict[str, int] = {}
    for r in rows:
        sym_counts[r["symbol"]] = sym_counts.get(r["symbol"], 0) + 1
    top = sorted(sym_counts.items(), key=lambda x: -x[1])[:10]
    dom = (sum(c for _, c in top) / len(rows) * 100) if rows else 0
    return {
        "live_closed": len(rows),
        "win_rate_pct": round(len(wins) / len(rets) * 100, 1),
        "median_return_20d": round(median(rets), 4),
        "mean_return_20d": round(mean(rets), 4),
        "pf_100bps_proxy": round(pf, 2) if pf is not None else None,
        "top10_dominance_pct": round(dom, 1),
        "top_symbols": top[:5],
    }


def _graduation_check(live: dict, oos: dict | None) -> dict:
    n = int(live.get("live_closed") or 0)
    pf = live.get("pf_100bps_proxy")
    dom = live.get("top10_dominance_pct")
    checks = {
        "live_closed_40": n >= GRADUATION["min_live_closed"],
        "pf_ge_1_3": pf is not None and pf >= GRADUATION["min_pf_100bps"],
        "dominance_lt_35": dom is not None and dom < GRADUATION["max_top10_dominance_pct"],
        "client_path_blocked": True,
        "penalize_wired": (_read_json(DATA / "med_0_3_wire_acceptance_last.json") or {}).get("verdict")
        == "PASS_MED_0_3_WIRE",
    }
    ready_feed_boost = all(checks.values())
    ready_client = ready_feed_boost  # same bar for now; client needs separate approval
    return {
        "ready_for_feed_boost": ready_feed_boost,
        "ready_for_client_signal": False,
        "checks": checks,
        "progress": {
            "live_closed": n,
            "target_live": GRADUATION["min_live_closed"],
            "live_pf_proxy": pf,
            "live_dominance_pct": dom,
            "oos_closed": (oos or {}).get("oos_closed"),
            "oos_median_return": (oos or {}).get("oos_median_return"),
        },
        "verdict": (
            "GRADUATION_READY_FEED_BOOST" if ready_feed_boost
            else "ACCUMULATING_LIVE" if n < GRADUATION["min_live_closed"]
            else "LIVE_NEEDS_QUALITY"
        ),
        "env_gates": {
            GRADUATION["feed_boost_env"]: "0",
            GRADUATION["client_signal_env"]: "0",
            "MED_FEED_PENALIZE": "1",
        },
    }


def run(params: dict | None = None) -> dict:
    params = params or {}
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    status = {
        "phase": "MED-0.4",
        "trade_date": trade_date,
        "shadow_only": True,
        "client_path_allowed": False,
        "automated": True,
        "pipeline": "egx_tv_auto_update.mjs → med_0_3_daily_chain → lessons + cognition",
        "wire": {
            "med_opp_bridge": "penalize_only",
            "MED_FEED_BOOST": "0",
            "MED_FEED_PENALIZE": "1",
        },
    }

    if trade_date:
        buckets = dict(db.execute(
            "SELECT med_bucket, COUNT(*) c FROM med_daily_scores WHERE trade_date=? GROUP BY 1",
            (trade_date,),
        ).fetchall())
        total = sum(buckets.values()) or 1
        status["buckets"] = buckets
        status["failure_warning_pct"] = round(
            100 * buckets.get("MED_FAILURE_WARNING", 0) / total, 1,
        )
        status["do_not_chase_pct"] = round(
            100 * buckets.get("MED_DO_NOT_CHASE", 0) / total, 1,
        )
        status["high_conviction_count"] = buckets.get("MED_HIGH_CONVICTION_RESEARCH", 0)
        status["top15"] = [
            dict(r) for r in db.execute(
                """
                SELECT d.symbol, d.med_score, d.med_bucket, d.p_cond_20d_10,
                       a.analogue_p_tail_20_10, d.hypothetical_boost
                FROM med_daily_scores d
                LEFT JOIN med_analogue_scores_daily a
                  ON d.trade_date=a.trade_date AND d.symbol=a.symbol
                WHERE d.trade_date=? ORDER BY d.med_score DESC LIMIT 15
                """,
                (trade_date,),
            ).fetchall()
        ]

    for name, path in (
        ("acceptance", DATA / "med_0_3_acceptance_last.json"),
        ("wire_acceptance", DATA / "med_0_3_wire_acceptance_last.json"),
        ("discovery_report", DATA / "med_0_3_discovery_report.json"),
        ("manifest", DATA / "discovery_med_manifest.json"),
        ("calibrate", DATA / "med_0_3_calibrate_last.json"),
    ):
        doc = _read_json(path)
        if doc:
            status[name] = doc.get("verdict") or doc.get("success") or "ok"

    fwd = _read_json(DATA / "med_forward_shadow_last.json") or {}
    live = _forward_live_metrics(db)
    status["graduation"] = _graduation_check(live, fwd)
    status["forward_shadow"] = {
        "oos_closed": fwd.get("oos_closed"),
        "oos_median_return": fwd.get("oos_median_return"),
        "live_skipped": fwd.get("live_skipped"),
        **live,
    }

    status["run_at"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    db.close()
    return status


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
