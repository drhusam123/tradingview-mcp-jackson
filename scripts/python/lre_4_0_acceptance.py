#!/usr/bin/env python3
"""
LRE-4.0 acceptance gate — research feed invariants + pipeline health.

Shadow only. Does not prove alpha; proves client path is blocked and feed is wired.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
OUTPUT = DATA / "lre_4_0_acceptance_last.json"

REQUIRED_INVARIANTS = {
    "EGX_LRE_SHADOW": "1",
    "client_path_allowed": False,
    "additive_only": True,
    "no_actionable_change": True,
}


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(params: dict | None = None) -> dict:
    params = params or {}
    checks: list[dict] = []
    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(signal_date) d FROM lre_research_feed_daily").fetchone()
        trade_date = row["d"] if row and row["d"] else None
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        trade_date = row["d"] if row else None

    tables = ["lre_research_feed_daily", "lre_mde_dual_gate_audit", "lre_daily_scores"]
    for t in tables:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        checks.append(_check(f"table:{t}", bool(exists), "present" if exists else "missing"))

    feed_n = 0
    if trade_date:
        feed_n = db.execute(
            "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=?",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("feed_rows", feed_n > 0, f"date={trade_date} rows={feed_n}"))

        client_leak = db.execute(
            "SELECT COUNT(*) n FROM lre_research_feed_daily WHERE signal_date=? AND client_path_allowed=1",
            (trade_date,),
        ).fetchone()["n"]
        checks.append(_check("no_client_path_leak", client_leak == 0, f"leaks={client_leak}"))

        max_boost = db.execute(
            "SELECT MAX(opp_boost_points) m FROM lre_research_feed_daily WHERE signal_date=?",
            (trade_date,),
        ).fetchone()["m"] or 0
        checks.append(_check("boost_cap", float(max_boost) <= 3.0, f"max_boost={max_boost}"))

        dual_last = DATA / "lre_dual_gate_daily_last.json"
        if dual_last.exists():
            dual = json.loads(dual_last.read_text(encoding="utf-8"))
            date_ok = dual.get("trade_date") == trade_date
            checks.append(_check(
                "dual_gate_daily_ok",
                dual.get("success") is True,
                f"audit_date={dual.get('trade_date')} confluence={dual.get('confluence_count')}"
                + ("" if date_ok else f" (feed_date={trade_date})"),
            ))
            checks.append(_check(
                "dual_gate_no_client",
                not dual.get("client_path_allowed"),
                "client_path_allowed=False",
            ))

        manifest = DATA / "discovery_lre_manifest.json"
        if manifest.exists():
            man = json.loads(manifest.read_text(encoding="utf-8"))
            inv = man.get("invariants") or {}
            for k, v in REQUIRED_INVARIANTS.items():
                checks.append(_check(
                    f"manifest:{k}",
                    inv.get(k) == v,
                    f"expected={v!r} got={inv.get(k)!r}",
                ))

    fwd_last = DATA / "lre_3_6b_forward_shadow_last.json"
    if fwd_last.exists():
        fwd = json.loads(fwd_last.read_text(encoding="utf-8"))
        checks.append(_check(
            "forward_shadow_state",
            fwd.get("success") is True,
            fwd.get("reason") or f"open={fwd.get('open_positions')} total={fwd.get('total_forward_rows')}",
        ))

    integ = DATA / "lre_4_0_integration_test_last.json"
    if integ.exists():
        ir = json.loads(integ.read_text(encoding="utf-8"))
        checks.append(_check(
            "integration_verdict",
            ir.get("verdict") == "PASS_LRE_4_0_INTEGRATION",
            ir.get("verdict", "missing"),
        ))
        checks.append(_check(
            "actionable_unchanged",
            ir.get("actionable_unchanged") is True,
            f"before={ir.get('before_actionable_count')} after={ir.get('after_actionable_count')}",
        ))

    db.close()
    fail = [c for c in checks if not c["ok"]]
    payload = {
        "success": len(fail) == 0,
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "checks": checks,
        "pass": len(checks) - len(fail),
        "fail": len(fail),
        "verdict": "PASS_LRE_4_0_ACCEPTANCE" if not fail else "FAIL_LRE_4_0_ACCEPTANCE",
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload))
    return payload


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    result = run(p)
    sys.exit(0 if result.get("success") else 1)
