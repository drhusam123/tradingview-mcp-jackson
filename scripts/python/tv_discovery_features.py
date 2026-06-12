#!/usr/bin/env python3
"""
TV Discovery Features — microstructure atoms from pine_analytics + OHLCV.
Feeds opportunity_score_v2 and quant_discovery seed atoms.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_PATH = ROOT / "data" / "tv_discovery_features_last.json"

ATOM_WEIGHTS = {
    "VWAP_RECLAIM": 8.0,
    "VP_POC_RECLAIM": 7.0,
    "ABSORPTION_BAR": 10.0,
    "PARTICIPATION_SHOCK": 6.0,
    "CVD_BULL_DIV_PROXY": 9.0,
    "CMF_POSITIVE_PROXY": 5.0,
    "VOLUME_BASELINE": 2.0,
    "RS_BASELINE": 2.0,
    "SCAN_WATCHLIST": 1.0,
}


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), timeout=60)
    db.row_factory = sqlite3.Row
    return db


def ensure_table(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tv_discovery_features (
          symbol TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          close_price REAL,
          vwap_reclaim INTEGER DEFAULT 0,
          vp_poc_reclaim INTEGER DEFAULT 0,
          absorption_bar INTEGER DEFAULT 0,
          participation_shock INTEGER DEFAULT 0,
          cvd_bull_div_proxy INTEGER DEFAULT 0,
          cmf_positive_proxy INTEGER DEFAULT 0,
          tv_score REAL,
          atoms_json TEXT,
          source TEXT,
          updated_at TEXT DEFAULT (datetime('now')),
          PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tv_feat_date_score "
        "ON tv_discovery_features(trade_date, tv_score DESC)"
    )


def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _extract_raw_blob(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            last = parsed[-1]
            if isinstance(last, dict):
                data = last.get("data") or last
                if isinstance(data, dict):
                    return data
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _latest_close(db: sqlite3.Connection, symbol: str, trade_date: str) -> Optional[float]:
    row = db.execute(
        """
        SELECT close FROM ohlcv_history_execution
        WHERE symbol=? AND date(bar_time,'unixepoch')=?
        ORDER BY bar_time DESC LIMIT 1
        """,
        (symbol, trade_date),
    ).fetchone()
    if row:
        return _safe_float(row["close"])
    row2 = db.execute(
        """
        SELECT close FROM ohlcv_history_execution
        WHERE symbol=?
        ORDER BY bar_time DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return _safe_float(row2["close"]) if row2 else None


def derive_atoms(pine_row: dict, close: Optional[float]) -> dict:
    """Derive microstructure atoms from pine_analytics row."""
    atoms: list[str] = []
    raw = _extract_raw_blob(pine_row.get("raw_pine_data"))
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}

    vol_ratio = _safe_float(raw.get("vol_ratio") or keys.get("VOL_RATIO"))
    close_pos = _safe_float(raw.get("close_position") or keys.get("CLOSE_POSITION"))
    trend_score = _safe_float(raw.get("trend_score") or keys.get("TREND_SCORE"))
    vwap = _safe_float(pine_row.get("vwap"))
    poc = _safe_float(pine_row.get("volume_poc"))
    rs_score = _safe_float(pine_row.get("rs_score"))
    session_bias = str(pine_row.get("session_bias") or "").upper()

    c = close or _safe_float(raw.get("close") or keys.get("CLOSE"))
    if c and c > 0:
        if vwap and c >= vwap * 1.001:
            atoms.append("VWAP_RECLAIM")
        if poc and abs(c - poc) / c <= 0.018:
            atoms.append("VP_POC_RECLAIM")

    if vol_ratio is not None and close_pos is not None:
        if vol_ratio >= 1.45 and close_pos <= 0.42 and (trend_score or 0) >= -0.5:
            atoms.append("ABSORPTION_BAR")
        if 2.5 <= vol_ratio <= 3.5:
            atoms.append("PARTICIPATION_SHOCK")
        if vol_ratio >= 1.8 and close_pos <= 0.35 and (trend_score or 0) > 0:
            atoms.append("CVD_BULL_DIV_PROXY")

    if rs_score is not None and rs_score >= 52 and (vol_ratio or 0) >= 1.35:
        atoms.append("CMF_POSITIVE_PROXY")
    if session_bias in {"ABOVE_VWAP", "ABOVE"} and vwap and c and c >= vwap:
        if "VWAP_RECLAIM" not in atoms:
            atoms.append("VWAP_RECLAIM")

    # Soft fallback — keep evaluated symbols in tv_discovery_features (Phase 3 target ≥40/day)
    if not atoms:
        if vol_ratio is not None and vol_ratio >= 1.0:
            atoms.append("VOLUME_BASELINE")
        elif rs_score is not None and rs_score >= 48:
            atoms.append("RS_BASELINE")
        else:
            atoms.append("SCAN_WATCHLIST")

    tv_score = round(sum(ATOM_WEIGHTS.get(a, 4.0) for a in atoms), 2)
    return {
        "atoms": atoms,
        "tv_score": tv_score,
        "vwap_reclaim": int("VWAP_RECLAIM" in atoms),
        "vp_poc_reclaim": int("VP_POC_RECLAIM" in atoms),
        "absorption_bar": int("ABSORPTION_BAR" in atoms),
        "participation_shock": int("PARTICIPATION_SHOCK" in atoms),
        "cvd_bull_div_proxy": int("CVD_BULL_DIV_PROXY" in atoms),
        "cmf_positive_proxy": int("CMF_POSITIVE_PROXY" in atoms),
        "vol_ratio": vol_ratio,
        "close_position": close_pos,
    }


def select_symbols(db: sqlite3.Connection, trade_date: str, limit: int = 30) -> list[str]:
    symbols: list[str] = []
    opp = db.execute(
        """
        SELECT symbol FROM opportunity_score_v2
        WHERE trade_date=? AND opportunity_score >= 62
        ORDER BY opportunity_score DESC LIMIT ?
        """,
        (trade_date, limit),
    ).fetchall()
    symbols.extend(r["symbol"] for r in opp)

    if len(symbols) < limit:
        opp_soft = db.execute(
            """
            SELECT symbol FROM opportunity_score_v2
            WHERE trade_date=? AND opportunity_score >= 55
            ORDER BY opportunity_score DESC LIMIT ?
            """,
            (trade_date, limit),
        ).fetchall()
        for r in opp_soft:
            if r["symbol"] not in symbols:
                symbols.append(r["symbol"])

    if len(symbols) < limit:
        scans = db.execute(
            """
            SELECT symbol, MAX(score) s FROM scans
            WHERE scan_date=? AND rejected=0
            GROUP BY symbol ORDER BY s DESC LIMIT ?
            """,
            (trade_date, limit),
        ).fetchall()
        for r in scans:
            if r["symbol"] not in symbols:
                symbols.append(r["symbol"])

    if len(symbols) < limit:
        pine = db.execute(
            """
            SELECT DISTINCT symbol FROM pine_analytics
            WHERE trade_date >= date(?, '-7 day')
            ORDER BY symbol LIMIT ?
            """,
            (trade_date, limit),
        ).fetchall()
        for r in pine:
            if r["symbol"] not in symbols:
                symbols.append(r["symbol"])

    return symbols[:limit]


def compute(params: dict | None = None) -> dict:
    params = params or {}
    db = connect()
    ensure_table(db)

    trade_date = params.get("date")
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) AS d FROM pine_analytics").fetchone()
        trade_date = row["d"] if row and row["d"] else datetime.utcnow().strftime("%Y-%m-%d")

    limit = int(params.get("max_symbols", 30))
    symbols = params.get("symbols") or select_symbols(db, trade_date, limit)

    rows_out = []
    atom_counts: dict[str, int] = {}

    for sym in symbols:
        pine = db.execute(
            "SELECT * FROM pine_analytics WHERE symbol=? AND trade_date=?",
            (sym, trade_date),
        ).fetchone()
        if not pine:
            pine = db.execute(
                "SELECT * FROM pine_analytics WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
                (sym,),
            ).fetchone()
        if not pine:
            continue

        close = _latest_close(db, sym, trade_date)
        derived = derive_atoms(dict(pine), close)
        if not derived["atoms"]:
            continue

        for a in derived["atoms"]:
            atom_counts[a] = atom_counts.get(a, 0) + 1

        rec = {
            "symbol": sym,
            "trade_date": trade_date,
            "close_price": close,
            "tv_score": derived["tv_score"],
            "atoms_json": json.dumps(derived["atoms"], ensure_ascii=False),
            "source": pine["source_script"] or "pine_analytics",
            **{k: derived[k] for k in (
                "vwap_reclaim", "vp_poc_reclaim", "absorption_bar",
                "participation_shock", "cvd_bull_div_proxy", "cmf_positive_proxy",
            )},
        }
        db.execute(
            """
            INSERT OR REPLACE INTO tv_discovery_features
            (symbol, trade_date, close_price, vwap_reclaim, vp_poc_reclaim,
             absorption_bar, participation_shock, cvd_bull_div_proxy, cmf_positive_proxy,
             tv_score, atoms_json, source, updated_at)
            VALUES
            (:symbol, :trade_date, :close_price, :vwap_reclaim, :vp_poc_reclaim,
             :absorption_bar, :participation_shock, :cvd_bull_div_proxy, :cmf_positive_proxy,
             :tv_score, :atoms_json, :source, datetime('now'))
            """,
            rec,
        )
        rows_out.append({**rec, "atoms": derived["atoms"]})

    db.commit()
    db.close()

    report = {
        "success": True,
        "trade_date": trade_date,
        "symbols_processed": len(symbols),
        "features_written": len(rows_out),
        "atom_counts": atom_counts,
        "top": sorted(rows_out, key=lambda r: r["tv_score"], reverse=True)[:10],
        "at": datetime.utcnow().isoformat(),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compute"
    p = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if cmd == "compute":
        print(json.dumps(compute(p), indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"success": False, "error": f"unknown command {cmd}"}))
