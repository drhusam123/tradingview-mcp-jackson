#!/usr/bin/env python3
"""Lightweight market data validation — SQLite only, Intel Mac friendly."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUTPUT = ROOT / "data" / "market_data_validation_last.json"


def run() -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    bad_rows: list[dict] = []
    missing_symbols: list[str] = []

    if not DB_PATH.exists():
        payload = {
            "status": "FAIL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latest_date": None,
            "symbols_checked": 0,
            "missing_symbols": [],
            "bad_rows": [],
            "warnings": [],
            "failures": ["database missing"],
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history"
        ).fetchone()["d"]

        universe = conn.execute("SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history").fetchone()["n"]
        on_latest = 0
        if latest:
            on_latest = conn.execute(
                "SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history WHERE date(bar_time,'unixepoch')=?",
                (latest,),
            ).fetchone()["n"]
        # Use effective session date when latest calendar day is partial (weekend/holiday)
        best_date = conn.execute("""
            SELECT date(bar_time,'unixepoch') d, COUNT(DISTINCT symbol) n
            FROM ohlcv_history GROUP BY d ORDER BY n DESC LIMIT 1
        """).fetchone()
        effective = best_date["d"] if best_date else latest
        on_effective = conn.execute(
            "SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history WHERE date(bar_time,'unixepoch')=?",
            (effective,),
        ).fetchone()["n"]
        if latest and on_latest < universe * 0.85:
            if on_effective >= universe * 0.85:
                warnings.append(f"partial latest={latest} ({on_latest} syms); effective={effective} ({on_effective})")
            else:
                warnings.append(f"only {on_latest}/{universe} symbols on latest={latest}")

        zero_close = conn.execute(
            "SELECT COUNT(*) n FROM ohlcv_history WHERE close <= 0"
        ).fetchone()["n"]
        if zero_close:
            failures.append(f"zero_or_negative_close={zero_close}")
            for r in conn.execute(
                "SELECT symbol, date(bar_time,'unixepoch') d, close FROM ohlcv_history WHERE close<=0 LIMIT 10"
            ):
                bad_rows.append(dict(r))

        neg_vol = conn.execute(
            "SELECT COUNT(*) n FROM ohlcv_history WHERE volume < 0"
        ).fetchone()["n"]
        if neg_vol:
            failures.append(f"negative_volume={neg_vol}")

        dupes = conn.execute("""
            SELECT COUNT(*) n FROM (
              SELECT symbol, date(bar_time,'unixepoch') d, COUNT(*) c
              FROM ohlcv_history GROUP BY symbol, d HAVING c > 1
            )
        """).fetchone()["n"]
        if dupes:
            warnings.append(f"duplicate_symbol_days={dupes}")

        ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            failures.append(f"integrity_check={ic}")

        # Ghost symbols in universe without OHLCV
        try:
            ghosts = conn.execute("""
                SELECT symbol FROM stock_universe u
                WHERE status IN ('ACTIVE','WATCH')
                  AND NOT EXISTS (SELECT 1 FROM ohlcv_history h WHERE h.symbol=u.symbol)
                LIMIT 20
            """).fetchall()
            missing_symbols = [r[0] for r in ghosts]
            if missing_symbols:
                warnings.append(f"universe_ghosts={len(missing_symbols)}")
        except sqlite3.Error:
            pass

        status = "FAIL" if failures else ("WARN" if warnings else "PASS")
        payload = {
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latest_date": latest,
            "symbols_checked": universe,
            "symbols_on_latest": on_latest,
            "missing_symbols": missing_symbols,
            "bad_rows": bad_rows,
            "warnings": warnings,
            "failures": failures,
        }
    finally:
        conn.close()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    r = run()
    print(f"\nMARKET DATA: {r['status']}")
    print(f"  latest={r.get('latest_date')} symbols={r.get('symbols_checked')}")
    if r.get("failures"):
        for f in r["failures"]:
            print(f"  FAIL: {f}")
    if r.get("warnings"):
        for w in r["warnings"][:5]:
            print(f"  WARN: {w}")
    print(f"  Saved: {OUTPUT}\n")
    sys.exit(0 if r["status"] != "FAIL" else 1)
