#!/usr/bin/env python3
"""
ML Feature Bridge
=================

Exports production discovery/microstructure layers into feature_store so ML
monitoring and retraining can consume them with lineage.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone())


def ensure_feature_store(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS feature_store (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        feature_name TEXT NOT NULL,
        feature_value REAL,
        version TEXT NOT NULL,
        source_table TEXT,
        computed_at TEXT DEFAULT (datetime('now')),
        UNIQUE(feature_date, symbol, feature_name, version)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_store_symbol ON feature_store(symbol, version)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_store_date ON feature_store(feature_date)")


def put(conn: sqlite3.Connection, feature_date: str, symbol: str, name: str, value, version: str, source: str) -> int:
    try:
        if value is None:
            return 0
        v = float(value)
        conn.execute(
            """
            INSERT OR REPLACE INTO feature_store
            (feature_date, symbol, feature_name, feature_value, version, source_table)
            VALUES (?,?,?,?,?,?)
            """,
            (feature_date, symbol, name, v, version, source),
        )
        return 1
    except Exception:
        return 0


def stage_value(stage: str) -> float:
    return {
        "AVOID": 0.0,
        "WATCH": 0.35,
        "EARLY_ACCUMULATION": 0.60,
        "NEAR_BREAKOUT": 0.78,
        "QUALIFIED_DISCOVERY": 0.88,
        "ACTIONABLE_CANDIDATE": 1.0,
    }.get(str(stage or "").upper(), 0.0)


def xpro_stage_value(stage: str) -> float:
    return {
        "X_AVOID": 0.0,
        "X_BASE_BUILDING": 0.55,
        "X_REVIEW_QUALITY": 0.25,
        "X_WATCH": 0.68,
        "X_WATCH_HIGH": 0.82,
        "X_READY": 1.0,
    }.get(str(stage or "").upper(), 0.0)


def run() -> dict:
    conn = db()
    ensure_feature_store(conn)
    today = date.today().isoformat()
    version = f"ml_bridge_{today}"
    written = 0
    symbols = set()

    if table_exists(conn, "opportunity_score_v2"):
        d = conn.execute("SELECT MAX(trade_date) d FROM opportunity_score_v2").fetchone()["d"]
        if d:
            rows = conn.execute("SELECT * FROM opportunity_score_v2 WHERE trade_date=?", (d,)).fetchall()
            for r in rows:
                sym = r["symbol"]
                symbols.add(sym)
                mapping = {
                    "opp_score": r["opportunity_score"],
                    "opp_market_regime": r["market_regime_score"],
                    "opp_sector_strength": r["sector_strength_score"],
                    "opp_rs_market": r["rs_market_score"],
                    "opp_rs_sector": r["rs_sector_score"],
                    "opp_liquidity_expansion": r["liquidity_expansion_score"],
                    "opp_behavioral_fingerprint": r["behavioral_fingerprint_score"],
                    "opp_structure": r["structure_score"],
                    "opp_risk": r["risk_score"],
                    "opp_smart_money": r["smart_money_score"],
                    "opp_failure_penalty": r["failure_penalty"],
                    "opp_stage_value": stage_value(r["stage"]),
                }
                flags = []
                try:
                    flags = json.loads(r["flags_json"] or "[]")
                except Exception:
                    flags = []
                mapping.update({
                    "opp_has_volume_dryup": 1.0 if "VOLUME_DRYUP" in flags else 0.0,
                    "opp_has_liquidity_expansion": 1.0 if "LIQUIDITY_EXPANSION" in flags else 0.0,
                    "opp_has_vcp_proxy": 1.0 if "VCP_PROXY" in flags else 0.0,
                    "opp_final_veto": 1.0 if "FINAL_VETO" in flags else 0.0,
                })
                for name, value in mapping.items():
                    written += put(conn, d, sym, name, value, version, "opportunity_score_v2")

    if table_exists(conn, "egx_x_pro_daily"):
        d = conn.execute("SELECT MAX(trade_date) d FROM egx_x_pro_daily").fetchone()["d"]
        if d:
            rows = conn.execute("SELECT * FROM egx_x_pro_daily WHERE trade_date=?", (d,)).fetchall()
            for r in rows:
                sym = r["symbol"]
                symbols.add(sym)
                flags = []
                try:
                    flags = json.loads(r["flags_json"] or "[]")
                except Exception:
                    flags = []
                mapping = {
                    "xpro_score": r["x_score"],
                    "xpro_stage_value": xpro_stage_value(r["stage"]),
                    "xpro_rs_market": r["rs_market_score"],
                    "xpro_rs_sector": r["rs_sector_score"],
                    "xpro_rvol5": r["rvol5"],
                    "xpro_rvol20": r["rvol20"],
                    "xpro_rvol60": r["rvol60"],
                    "xpro_liquidity_expansion": r["liquidity_expansion_score"],
                    "xpro_compression": r["compression_score"],
                    "xpro_turnover": r["turnover_score"],
                    "xpro_ownership_rotation": r["ownership_rotation_score"],
                    "xpro_atr_pct": r["atr_pct"],
                    "xpro_rr": r["rr_ratio"],
                    "xpro_above_ema50": 1.0 if "ABOVE_EMA50" in flags else 0.0,
                    "xpro_above_ema200": 1.0 if "ABOVE_EMA200" in flags else 0.0,
                    "xpro_above_vwap": 1.0 if "ABOVE_VWAP" in flags else 0.0,
                    "xpro_above_anchored_vwap": 1.0 if "ABOVE_ANCHORED_VWAP" in flags else 0.0,
                    "xpro_has_rs_sector": 1.0 if "RS_SECTOR" in flags else 0.0,
                    "xpro_has_liquidity_expansion": 1.0 if "LIQUIDITY_EXPANSION" in flags else 0.0,
                    "xpro_has_compression": 1.0 if "VOLATILITY_COMPRESSION" in flags else 0.0,
                }
                for name, value in mapping.items():
                    written += put(conn, d, sym, name, value, version, "egx_x_pro_daily")

    if table_exists(conn, "dom_live_snapshots"):
        rows = conn.execute("""
            SELECT d.*
            FROM dom_live_snapshots d
            JOIN (
              SELECT symbol, MAX(fetched_at) fetched_at
              FROM dom_live_snapshots
              GROUP BY symbol
            ) x ON x.symbol=d.symbol AND x.fetched_at=d.fetched_at
        """).fetchall()
        for r in rows:
            sym = r["symbol"]
            symbols.add(sym)
            dom_data = {}
            try:
                dom_data = json.loads(r["dom_data"] or "{}")
            except Exception:
                dom_data = {}
            fetched = str(r["fetched_at"] or today)[:10]
            mapping = {
                "dom_spread_bps": r["spread_bps"],
                "dom_total_bid_depth": r["total_bid_depth"],
                "dom_total_ask_depth": r["total_ask_depth"],
                "dom_imbalance_ratio": r["imbalance_ratio"],
                "dom_is_proxy": 1.0 if dom_data.get("proxy") else 0.0,
            }
            for name, value in mapping.items():
                written += put(conn, fetched, sym, name, value, version, "dom_live_snapshots")

    conn.commit()
    out = {
        "success": True,
        "version": version,
        "symbols": len(symbols),
        "features_written": written,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd != "run":
        raise SystemExit(f"Unknown command: {cmd}")
    run()
