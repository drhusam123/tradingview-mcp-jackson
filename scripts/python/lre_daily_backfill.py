#!/usr/bin/env python3
"""Backfill lre_daily_scores for OOS replay (causal, read-only scoring)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "python"))

from med_common import OOS_END, OOS_START, connect as med_connect, load_bars
import egx_liquidity_rotation_engine as lre

DATA = ROOT / "data"
OUTPUT = DATA / "lre_daily_backfill_last.json"


def run(params: dict | None = None) -> dict:
    params = params or {}
    start = params.get("start_date", OOS_START)
    end = params.get("end_date", OOS_END)
    step = int(params.get("step", 1))

    conn = lre.connect()
    lre.ensure_tables(conn)
    by_sym, meta = lre.load_all_bars(conn)

    dates = sorted({
        b["date"] for bars in by_sym.values() for b in bars
        if start <= b["date"] <= end
    })[::step]

    analogue_cache = {sym: lre.analogue_score_for_symbol(conn, sym) for sym in by_sym}
    orig = lre.analogue_score_for_symbol
    lre.analogue_score_for_symbol = lambda c, sym: analogue_cache.get(sym, 42.0)

    total_rows = 0
    try:
        for i, td in enumerate(dates):
            rotation_map = lre.load_rotation_triggers(conn, by_sym, td)
            scores = []
            for sym, bars in by_sym.items():
                row = lre.score_symbol_daily(conn, sym, bars, td, rotation_map)
                if row:
                    scores.append(row)
            market = lre.compute_speculative_appetite(by_sym, td, conn)
            market["trade_date"] = td
            lre.publish_daily_scores(conn, scores, market)
            total_rows += len(scores)
            if (i + 1) % 20 == 0 or i == len(dates) - 1:
                print(f"  backfill {i+1}/{len(dates)} @ {td} rows={len(scores)}", flush=True)
    finally:
        lre.analogue_score_for_symbol = orig
    conn.close()

    out = {
        "success": True,
        "start_date": start,
        "end_date": end,
        "dates_filled": len(dates),
        "total_score_rows": total_rows,
        "symbols": meta["symbols"],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
