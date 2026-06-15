#!/usr/bin/env python3
"""MED-0.3 wire acceptance — penalize path active, actionable unchanged, no client boost."""
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
OUTPUT = DATA / "med_0_3_wire_acceptance_last.json"

sys.path.insert(0, str(ROOT / "scripts" / "python"))


def _actionable_snapshot(db: sqlite3.Connection, trade_date: str) -> set[str]:
    rows = db.execute(
        "SELECT symbol FROM final_signals WHERE trade_date=? AND actionable=1",
        (trade_date,),
    ).fetchall()
    return {r["symbol"] for r in rows}


def run(params: dict | None = None) -> dict:
    params = params or {}
    os.environ["MED_SHADOW"] = "1"
    os.environ["MED_CLIENT_SIGNAL"] = "0"
    os.environ["MED_OPP_BOOST"] = "0"
    os.environ["MED_FEED_BOOST"] = "0"
    os.environ["MED_FEED_PENALIZE"] = "1"

    db = sqlite3.connect(str(DB_PATH), timeout=120)
    db.row_factory = sqlite3.Row

    trade_date = params.get("trade_date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) d FROM med_research_feed").fetchone()
        trade_date = row["d"] if row else None
    if not trade_date:
        db.close()
        return {"success": False, "error": "no_trade_date"}

    actionable_before = _actionable_snapshot(db, trade_date)

    penalize_atoms = db.execute(
        """
        SELECT atom_id, status FROM discovery_atom_registry
        WHERE source_miner='egx_med_miner' AND hard_negative=1
        """
    ).fetchall()
    penalize_validated = sum(1 for r in penalize_atoms if r["status"] == "validated")

    manifest_path = DATA / "discovery_ml_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    med_penalize_in_manifest = [
        a for a in (manifest.get("penalize_atoms") or []) if str(a).startswith("med_")
    ]
    med_hn_syms = [
        s for s in (manifest.get("hard_negative_symbols") or [])
        if s in {r["symbol"] for r in db.execute(
            "SELECT symbol FROM med_daily_scores WHERE trade_date=? "
            "AND med_bucket IN ('MED_FAILURE_WARNING','MED_DO_NOT_CHASE')",
            (trade_date,),
        ).fetchall()}
    ]

    from med_opp_bridge import load_med_feed_map, apply_med_research_penalty

    feed_map = load_med_feed_map(db, trade_date)
    penalized_samples = []
    for sym, row in list(feed_map.items())[:300]:
        pts, flags, _ = apply_med_research_penalty(sym, row)
        if pts > 0:
            penalized_samples.append({"symbol": sym, "pts": pts, "flags": flags})
    penalized_samples.sort(key=lambda x: x["pts"], reverse=True)

    client_leak = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND client_path_allowed=1",
        (trade_date,),
    ).fetchone()["n"]

    actionable_after = _actionable_snapshot(db, trade_date)

    checks = [
        {"name": "med_penalize_atoms_validated", "ok": penalize_validated >= 1,
         "detail": f"validated={penalize_validated}/{len(penalize_atoms)}"},
        {"name": "med_penalize_in_ml_manifest", "ok": len(med_penalize_in_manifest) >= 1,
         "detail": med_penalize_in_manifest},
        {"name": "med_strict_hn_in_manifest", "ok": len(med_hn_syms) >= 1,
         "detail": f"count={len(med_hn_syms)}"},
        {"name": "med_penalty_applies", "ok": len(penalized_samples) >= 5,
         "detail": f"n={len(penalized_samples)}"},
        {"name": "actionable_unchanged", "ok": actionable_before == actionable_after,
         "detail": f"before={len(actionable_before)} after={len(actionable_after)}"},
        {"name": "no_client_path_leak", "ok": client_leak == 0, "detail": str(client_leak)},
        {"name": "MED_FEED_BOOST_off", "ok": os.environ.get("MED_FEED_BOOST", "0") == "0",
         "detail": os.environ.get("MED_FEED_BOOST", "0")},
    ]
    passed = all(c["ok"] for c in checks)
    verdict = "PASS_MED_0_3_WIRE" if passed else "FAIL_MED_0_3_WIRE"

    out = {
        "success": passed,
        "verdict": verdict,
        "trade_date": trade_date,
        "checks": checks,
        "penalized_sample_top5": penalized_samples[:5],
        "med_hn_symbols_sample": med_hn_syms[:10],
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    db.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
