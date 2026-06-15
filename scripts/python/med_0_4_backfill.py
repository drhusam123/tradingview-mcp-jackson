#!/usr/bin/env python3
"""MED-0.4 — backfill med_daily_scores history (fast mode, progress tracking)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "med_0_4_backfill_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _warm_caches() -> dict:
    from med_common import OOS_END, OOS_START, connect, effective_oos_end, load_bars, load_lre_all, load_mde_all, load_sectors
    from med_0_3_regime_context import load_all_regime_caches

    conn = connect()
    oos_end = effective_oos_end(conn)
    by_sym, meta = load_bars(conn)
    sectors = load_sectors(conn)
    regime = load_all_regime_caches(conn)
    lre_all = load_lre_all(conn, OOS_START, oos_end)
    mde_all = load_mde_all(conn, OOS_START, oos_end)
    conn.close()
    return {
        "_bars_cache": by_sym,
        "_bars_meta": meta,
        "_sectors_cache": sectors,
        "_regime_cache": regime,
        "_lre_all_cache": lre_all,
        "_mde_all_cache": mde_all,
        "_oos_end": oos_end,
    }


def run(params: dict | None = None) -> dict:
    params = params or {}
    n_days = int(params.get("days", 30))
    max_days = int(params.get("max_run", n_days))

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    dates = [
        r[0] for r in conn.execute(
            """
            SELECT DISTINCT trade_date FROM lre_daily_scores
            WHERE trade_date >= date('now', ?)
            ORDER BY trade_date ASC
            """,
            (f"-{n_days} days",),
        ).fetchall()
    ]
    complete = {
        r[0] for r in conn.execute(
            """
            SELECT trade_date FROM med_daily_scores
            GROUP BY trade_date
            HAVING COUNT(DISTINCT med_bucket) >= 5 AND MAX(sample_quality) > 0.2
            """
        ).fetchall()
    }
    conn.close()

    target_dates = dates[-max_days:]
    if not params.get("force"):
        target_dates = [d for d in target_dates if d not in complete]

    if params.get("rebuild_edges_once") and dates:
        from med_0_3_daily_engine import run as run_daily
        run_daily({"trade_date": dates[-1], "rebuild_edges": True})

    from med_0_3_daily_engine import run as run_daily

    cache = _warm_caches() if params.get("backfill_fast", True) else {}
    results = []
    skipped = [d for d in dates[-max_days:] if d in complete] if not params.get("force") else []
    for i, d in enumerate(target_dates):
        try:
            r = run_daily({
                **cache,
                "trade_date": d,
                "rebuild_edges": False,
                "force_analogue_rebuild": False,
                "backfill_fast": params.get("backfill_fast", True),
            })
            buckets = r.get("buckets") or {}
            n_buckets = len(buckets)
            hc = buckets.get("MED_HIGH_CONVICTION_RESEARCH", 0)
            if n_buckets < 5:
                conn = sqlite3.connect(str(DB_PATH), timeout=120)
                conn.execute("DELETE FROM med_daily_scores WHERE trade_date=?", (d,))
                conn.execute("DELETE FROM med_analogue_scores_daily WHERE trade_date=?", (d,))
                conn.commit()
                conn.close()
                results.append({"trade_date": d, "error": f"incomplete_buckets={n_buckets}", "rolled_back": True})
                continue
            row = {
                "trade_date": d,
                "hc": hc,
                "scored": r.get("scored_rows", 0),
            }
            results.append(row)
            partial = {
                "success": True,
                "in_progress": True,
                "completed": len(results),
                "total": len(target_dates),
                "skipped": len(skipped),
                "last": row,
                "run_at": datetime.now(timezone.utc).isoformat(),
            }
            OUTPUT.write_text(json.dumps(partial, indent=2), encoding="utf-8")
        except Exception as exc:
            results.append({"trade_date": d, "error": str(exc)})

    hc_days = [x for x in results if x.get("hc", 0) > 0]
    out = {
        "success": True,
        "days_requested": n_days,
        "days_run": len(results),
        "days_skipped_complete": len(skipped),
        "days_with_hc": len(hc_days),
        "avg_hc": round(sum(x.get("hc", 0) for x in results) / max(len(results), 1), 2),
        "hc_by_day": results,
        "skipped_dates": skipped[:20],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
