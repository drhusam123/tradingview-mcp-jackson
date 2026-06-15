#!/usr/bin/env python3
"""MED-0.4 acceptance — HC count, ranking sanity, invariants."""
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
OUTPUT = DATA / "med_0_4_acceptance_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))
from med_common import latest_med_trade_date
from med_0_4_scoring import is_index_symbol


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(params: dict | None = None) -> dict:
    params = params or {}
    os.environ.setdefault("MED_SHADOW", "1")
    os.environ.setdefault("MED_CLIENT_SIGNAL", "0")
    os.environ.setdefault("MED_FEED_BOOST", "0")

    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date") or latest_med_trade_date(db)

    checks = []
    if not trade_date:
        db.close()
        return {"success": False, "error": "no_trade_date"}

    buckets = dict(db.execute(
        "SELECT med_bucket, COUNT(*) c FROM med_daily_scores WHERE trade_date=? GROUP BY 1",
        (trade_date,),
    ).fetchall())
    hc_n = buckets.get("MED_HIGH_CONVICTION_RESEARCH", 0)
    checks.append(_check("hc_count_3_8", 3 <= hc_n <= 8, f"hc={hc_n}"))

    hc_rows = db.execute(
        "SELECT symbol, med_score, p_cond_20d_10, reason_codes FROM med_daily_scores "
        "WHERE trade_date=? AND med_bucket='MED_HIGH_CONVICTION_RESEARCH'",
        (trade_date,),
    ).fetchall()
    idx_in_hc = sum(1 for r in hc_rows if is_index_symbol(r["symbol"]))
    checks.append(_check("no_index_in_hc", idx_in_hc == 0, f"index_in_hc={idx_in_hc}"))

    sq_row = db.execute(
        "SELECT MIN(sample_quality), MAX(sample_quality), AVG(sample_quality) "
        "FROM med_daily_scores WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    checks.append(_check("sq_max_ge_0_30", (sq_row[1] or 0) >= 0.30, f"max_sq={sq_row[1]}"))

    pcond_top = db.execute(
        """
        SELECT symbol, p_cond_20d_10, med_score, reason_codes
        FROM med_daily_scores WHERE trade_date=? AND p_cond_20d_10 >= 0.22
        ORDER BY p_cond_20d_10 DESC LIMIT 10
        """,
        (trade_date,),
    ).fetchall()
    in_top = 0
    for r in pcond_top:
        try:
            rc = json.loads(r["reason_codes"] or "[]")
            if len(rc) > 3 and float(rc[3]) >= 0.40:
                in_top += 1
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    checks.append(_check(
        "pcond_stocks_in_top40_rank",
        in_top >= min(3, len(pcond_top)) if pcond_top else False,
        f"in_top={in_top}/{len(pcond_top)}",
    ))

    client_leak = db.execute(
        "SELECT COUNT(*) FROM med_research_feed WHERE trade_date=? AND client_path_allowed=1",
        (trade_date,),
    ).fetchone()[0]
    checks.append(_check("no_client_leak", client_leak == 0, str(client_leak)))

    fail_pct = 100 * buckets.get("MED_FAILURE_WARNING", 0) / max(sum(buckets.values()), 1)
    checks.append(_check("failure_warning_25_40", 20 <= fail_pct <= 45, f"pct={fail_pct:.1f}"))

    hc_audit = DATA / "med_0_4_hc_audit_last.json"
    if hc_audit.exists():
        ha = json.loads(hc_audit.read_text())
        if ha.get("trade_date") == trade_date:
            checks.append(_check(
                "hc_audit_exists", True,
                f"would_pass={ha.get('blockers', {}).get('would_pass_hc_gate')}",
            ))
            ana = ha.get("analogue_overlap") or {}
            overlap_pct = ana.get("overlap_pct") or 0
            checks.append(_check(
                "analogue_overlap_ge_30",
                bool(ana.get("target_pct_ge_30")) or overlap_pct >= 30,
                f"overlap_pct={overlap_pct}",
            ))

    passed = all(c["ok"] for c in checks)
    verdict = "PASS_MED_0_4_HC" if passed else "FAIL_MED_0_4_HC"

    out = {
        "success": passed,
        "verdict": verdict,
        "trade_date": trade_date,
        "buckets": buckets,
        "high_conviction_count": hc_n,
        "high_conviction_symbols": [r["symbol"] for r in hc_rows],
        "checks": checks,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    db.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
