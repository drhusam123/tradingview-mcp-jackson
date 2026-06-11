#!/usr/bin/env python3
"""
Discovery Data Hydrate — enumerate all DB tables, map layers, run light backfills.
Writes data/discovery_data_catalog.json with row counts for every production table.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

LAYER_MAP = {
    "ohlcv": "L0", "stock_universe": "L0", "ohlcv_weekly": "L0", "ohlcv_history": "L0",
    "corporate_actions": "L0", "intraday_live": "L0", "tv_data_reconcile": "L0",
    "indicators_cache": "L1", "indicator": "L1", "feature_matrix": "L1",
    "pine_analytics": "L2", "tv_discovery": "L2", "closing_pressure": "L2",
    "market_breadth": "L2", "cross_market": "L2", "dom_": "L2", "macro_": "L2",
    "data_quality": "L2", "markov": "L2", "regime": "L2", "sector_breadth": "L2",
    "sector_rotation": "L2", "liquidity_profile": "L2", "anti_law": "L2",
    "data_integrity": "L2", "gate_audit": "L2", "spectral": "L2", "market_physics": "L2",
    "market_cycles": "L2", "event_calendar": "L2", "contagion": "L2",
    "scans": "L3", "setup_performance": "L3", "validation_results": "L3",
    "feature_store": "L4", "explosion_predictions": "L4", "forward_test": "L4",
    "explosive_moves": "L4", "explosion_readiness": "L4", "meta_label": "L4",
    "tsfresh": "L4", "survival": "L4", "conformal": "L4", "ml_": "L4",
    "stock_forecast": "L4", "pattern_analog": "L4", "dtw_similarity": "L4",
    "final_signals": "L5", "unified_signals": "L5", "bus_signals": "L5",
    "arbitration": "L6",
    "opportunity_score": "L7", "quant_discovery": "L7",
    "recommendation_outcomes": "L8", "bayesian_wr": "L8", "outcome": "L8",
    "counterfactual": "L8", "failure_reconstruction": "L8", "false_breakout": "L8",
    "market_experience": "L8", "reliability_curve": "L8",
    "sandbox": "L9", "alpha_rankings": "L9", "grid_runs": "L9", "walkforward": "L9",
    "hypothesis": "L9", "research": "L9", "structural": "L9", "law_competition": "L9",
    "law_quality": "L9", "knowledge_graph": "L9", "umcg": "L9", "stock_profiles": "L9",
    "stock_lead_lag": "L9", "correlation_cluster": "L9", "market_episode": "L9",
    "tv_replay": "L9", "engine_health": "L9",
    "notification_delivery": "L10", "telegram": "L10",
    "discovery_atom": "L11", "discovery_fabric": "L11",
}

HYDRATE_CMDS = [
    ("stock_universe", ["node", "scripts/tv_universe_sync.mjs"], 300),
    ("ohlcv_history", ["node", "scripts/daily_update.mjs", "--force"], 14400),
    ("cross_market_regime", ["node", "scripts/egx_cross_market.mjs", "macro"], 120),
    ("indicators_cache", ["node", "scripts/rebuild_indicators.mjs"], 600),
    ("pine_analytics", ["node", "scripts/fetch_pine_analytics.mjs", "session"], 600),
    ("scans", ["node", "scripts/scan_today.mjs", "--db-only"], 300),
]

LATEST_COLS = (
    "date", "trade_date", "scan_date", "signal_date", "bar_time", "bar_date",
    "last_fetch", "pred_date", "computed_at", "created_at",
)


def _layer_for_table(name: str) -> str:
    low = name.lower()
    for key, layer in LAYER_MAP.items():
        if key in low:
            return layer
    if low.startswith("egx_") or low.endswith("_log"):
        return "OPS"
    return "OTHER"


def _normalize_latest(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 10:
        try:
            from datetime import datetime as _dt
            return _dt.utcfromtimestamp(int(s)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass
    return s[:32]


def _ohlcv_stale(db) -> bool:
    try:
        row = db.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history"
        ).fetchone()
        latest = row[0] if row else None
        if not latest:
            return True
        from datetime import date, timedelta
        return latest < (date.today() - timedelta(days=3)).isoformat()
    except sqlite3.OperationalError:
        return True


def enumerate_tables(db) -> list[dict]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    out = []
    for (name,) in rows:
        try:
            n = db.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            latest = None
            for col in LATEST_COLS:
                try:
                    r = db.execute(f"SELECT MAX([{col}]) FROM [{name}]").fetchone()[0]
                    if r:
                        latest = _normalize_latest(r)
                        break
                except sqlite3.OperationalError:
                    continue
            if latest is None and name == "ohlcv_history":
                try:
                    r = db.execute(
                        "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history"
                    ).fetchone()[0]
                    latest = _normalize_latest(r)
                except sqlite3.OperationalError:
                    pass
            out.append({
                "table": name,
                "layer": _layer_for_table(name),
                "row_count": n,
                "latest": latest,
                "has_data": n > 0,
            })
        except sqlite3.OperationalError:
            out.append({"table": name, "layer": "OTHER", "row_count": 0, "has_data": False})
    return out


def run_hydrate_commands(params: dict) -> list[dict]:
    if params.get("skip_fetch"):
        return []
    results = []
    targets = set(params.get("targets") or [])
    refresh_l0 = params.get("refresh_l0", False)
    db = sqlite3.connect(DB_PATH, timeout=30) if DB_PATH.exists() else None
    ohlcv_stale = _ohlcv_stale(db) if db else True
    if db:
        db.close()
    for label, cmd, timeout in HYDRATE_CMDS:
        if targets and label not in targets:
            continue
        if label == "ohlcv_history" and not refresh_l0 and not ohlcv_stale:
            results.append({"target": label, "ok": True, "skipped": True, "reason": "fresh"})
            continue
        if label == "stock_universe" and not refresh_l0 and not ohlcv_stale:
            results.append({"target": label, "ok": True, "skipped": True, "reason": "ohlcv_fresh"})
            continue
        try:
            proc = subprocess.run(
                cmd, cwd=ROOT, timeout=timeout, check=False,
                capture_output=True, text=True,
            )
            ok = proc.returncode == 0
            results.append({
                "target": label,
                "ok": ok,
                "exit_code": proc.returncode,
                "cmd": " ".join(cmd),
                "stderr": (proc.stderr or "")[-200:] if not ok else None,
            })
        except Exception as e:
            results.append({"target": label, "ok": False, "error": str(e)[:120]})
    return results


def run(params: dict | None = None):
    params = params or {}
    if not DB_PATH.exists():
        return {"success": False, "error": "NO_DB"}

    fetch_results = run_hydrate_commands(params)
    db = sqlite3.connect(DB_PATH, timeout=60)
    tables = enumerate_tables(db)
    db.close()

    production = [t for t in tables if t["layer"] not in ("OPS", "OTHER") and t["has_data"]]
    by_layer: dict[str, list] = {}
    for t in production:
        by_layer.setdefault(t["layer"], []).append(t["table"])

    catalog = {
        "at": datetime.now(timezone.utc).isoformat(),
        "total_tables": len(tables),
        "production_tables_with_data": len(production),
        "layers": {k: sorted(v) for k, v in sorted(by_layer.items())},
        "tables": tables,
        "hydrate_runs": fetch_results,
        "miners": (ROOT / "scripts/python/discovery_fabric_merge.py").exists(),
    }
    (DATA / "discovery_data_catalog.json").write_text(
        json.dumps(catalog, indent=2), encoding="utf-8"
    )
    payload = {
        "success": True,
        "total_tables": len(tables),
        "production_with_data": len(production),
        "layers": len(by_layer),
        "hydrate": fetch_results,
        "catalog": "data/discovery_data_catalog.json",
    }
    (DATA / "discovery_hydrate_last.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), ensure_ascii=False))
