#!/usr/bin/env python3
"""
Phase 84 — Lightweight Feature Store (SQLite-backed)
Versioned feature management with lineage tracking, offline/online consistency,
and feature drift detection. feast is NOT used (Python 3.9 incompatibility).

Usage:
    python feature_store.py refresh
    python feature_store.py get_features --symbol EFG
    python feature_store.py drift_report
    python feature_store.py lineage
    python feature_store.py report
"""
import sys
import json
import math
import sqlite3
import datetime
import argparse
from pathlib import Path
from typing import Optional

# ── Paths ───────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

# ── Feature columns (mirrors explosion_ml.py) ───────────────────────────────
FEATURE_COLS = [
    'pre1_bb_width',    'pre3_bb_width',    'pre5_bb_width',
    'pre1_vol_ratio',   'pre3_vol_ratio',   'pre5_vol_ratio',
    'pre1_rsi',         'pre3_rsi',         'pre5_rsi',
    'pre3_momentum_5d', 'pre5_momentum_5d',
    'pre5_bb_position', 'pre5_compression_days',
]

# OHLCV-derived features computed from last 5 bars
OHLCV_FEATURE_COLS = [
    'vol_5d_avg',   # avg daily volume (last 5 bars)
    'ret_5d',       # 5-day return
    'hl_ratio_5d',  # avg (high-low)/close last 5 bars
]

ALL_FEATURES = FEATURE_COLS + OHLCV_FEATURE_COLS


# ── DB helpers ───────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS feature_store (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_date    TEXT    NOT NULL,
        symbol          TEXT    NOT NULL,
        feature_name    TEXT    NOT NULL,
        feature_value   REAL,
        version         TEXT    NOT NULL,
        source_table    TEXT,
        computed_at     TEXT,
        UNIQUE(feature_date, symbol, feature_name, version)
    );

    CREATE TABLE IF NOT EXISTS feature_lineage (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_name        TEXT NOT NULL,
        version             TEXT NOT NULL,
        source_table        TEXT,
        computation_logic   TEXT,
        params              TEXT,
        created_at          TEXT,
        n_records           INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_fs_symbol_version
        ON feature_store(symbol, version);
    CREATE INDEX IF NOT EXISTS idx_fs_date
        ON feature_store(feature_date);
    """)
    conn.commit()


# ── OHLCV feature computation ────────────────────────────────────────────────
def compute_ohlcv_features(conn: sqlite3.Connection, symbol: str) -> dict:
    """Load last 5 OHLCV bars for symbol and compute derived features."""
    rows = conn.execute("""
        SELECT close, high, low, volume
        FROM ohlcv_history_execution
        WHERE symbol = ?
        ORDER BY bar_time DESC
        LIMIT 5
    """, (symbol,)).fetchall()

    if not rows:
        return {col: None for col in OHLCV_FEATURE_COLS}

    closes  = [safe_float(r['close'])  for r in rows]
    highs   = [safe_float(r['high'])   for r in rows]
    lows    = [safe_float(r['low'])    for r in rows]
    volumes = [safe_float(r['volume']) for r in rows]

    # vol_5d_avg
    vol_5d_avg = sum(volumes) / len(volumes) if volumes else None

    # ret_5d — newest / oldest - 1
    if len(closes) >= 2 and closes[-1] != 0:
        ret_5d = (closes[0] - closes[-1]) / closes[-1]
    else:
        ret_5d = None

    # hl_ratio_5d — avg (high-low)/close
    hl_ratios = []
    for h, l, c in zip(highs, lows, closes):
        if c and c != 0:
            hl_ratios.append((h - l) / c)
    hl_ratio_5d = sum(hl_ratios) / len(hl_ratios) if hl_ratios else None

    return {
        'vol_5d_avg':  vol_5d_avg,
        'ret_5d':      ret_5d,
        'hl_ratio_5d': hl_ratio_5d,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  cmd_refresh
# ══════════════════════════════════════════════════════════════════════════════
def cmd_refresh(params: dict) -> dict:
    """Compute and store today's features for all (or specified) symbols."""
    today     = datetime.date.today().isoformat()
    version   = params.get('version') or f"v{today}"
    symbols   = params.get('symbols')           # optional list
    now_ts    = datetime.datetime.utcnow().isoformat()

    conn = get_db()
    ensure_tables(conn)

    # ── Resolve symbol list ──────────────────────────────────────────────────
    if symbols:
        symbol_list = list(symbols)
    else:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM explosive_moves ORDER BY symbol"
        ).fetchall()
        symbol_list = [r['symbol'] for r in rows]

    if not symbol_list:
        conn.close()
        return {"success": False, "error": "No symbols found in explosive_moves"}

    n_inserted     = 0
    symbols_done   = []
    lineage_counts: dict[str, int] = {col: 0 for col in ALL_FEATURES}

    for symbol in symbol_list:
        # ── Latest explosive_moves row ─────────────────────────────────────
        em_row = conn.execute("""
            SELECT *
            FROM explosive_moves
            WHERE symbol = ?
            ORDER BY explosion_date DESC
            LIMIT 1
        """, (symbol,)).fetchone()

        # ── OHLCV-derived features (always computed from ohlcv_history_execution) ────
        ohlcv_feats = compute_ohlcv_features(conn, symbol)

        # ── Combine feature values ─────────────────────────────────────────
        feature_rows = []
        for col in FEATURE_COLS:
            val = safe_float(em_row[col]) if em_row and em_row[col] is not None else None
            feature_rows.append((col, val, 'explosive_moves'))

        for col in OHLCV_FEATURE_COLS:
            val = ohlcv_feats.get(col)
            feature_rows.append((col, val, 'ohlcv_history_execution'))

        # ── Insert into feature_store ──────────────────────────────────────
        for feat_name, feat_val, src_table in feature_rows:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO feature_store
                        (feature_date, symbol, feature_name, feature_value,
                         version, source_table, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (today, symbol, feat_name, feat_val,
                      version, src_table, now_ts))
                n_inserted += conn.execute("SELECT changes()").fetchone()[0]
                lineage_counts[feat_name] = lineage_counts.get(feat_name, 0) + 1
            except sqlite3.IntegrityError:
                pass  # already stored

        symbols_done.append(symbol)

    conn.commit()

    # ── Write lineage ────────────────────────────────────────────────────────
    for feat_name in ALL_FEATURES:
        src = 'explosive_moves' if feat_name in FEATURE_COLS else 'ohlcv_history_execution'
        logic = (
            'Pre-event indicator window (1/3/5 days before explosion)'
            if feat_name in FEATURE_COLS
            else 'OHLCV-derived: last 5 bars from ohlcv_history_execution'
        )
        conn.execute("""
            INSERT INTO feature_lineage
                (feature_name, version, source_table, computation_logic,
                 params, created_at, n_records)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            feat_name, version, src, logic,
            json.dumps({'symbols': len(symbols_done)}),
            now_ts, lineage_counts.get(feat_name, 0)
        ))

    conn.commit()
    conn.close()

    return {
        "success":    True,
        "n_symbols":  len(symbols_done),
        "n_features": len(ALL_FEATURES),
        "version":    version,
        "stored_at":  now_ts,
        "n_rows_inserted": n_inserted,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  cmd_get_features
# ══════════════════════════════════════════════════════════════════════════════
def cmd_get_features(params: dict) -> dict:
    """Fast online feature lookup for a symbol."""
    symbol  = params.get('symbol')
    version = params.get('version', 'latest')

    if not symbol:
        return {"success": False, "error": "symbol is required"}

    conn = get_db()
    ensure_tables(conn)

    # Resolve version
    if version == 'latest':
        row = conn.execute("""
            SELECT version, feature_date
            FROM feature_store
            WHERE symbol = ?
            ORDER BY feature_date DESC, version DESC
            LIMIT 1
        """, (symbol,)).fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": f"No features found for symbol '{symbol}'"}
        version      = row['version']
        feature_date = row['feature_date']
    else:
        row = conn.execute("""
            SELECT feature_date
            FROM feature_store
            WHERE symbol = ? AND version = ?
            ORDER BY feature_date DESC
            LIMIT 1
        """, (symbol, version)).fetchone()
        feature_date = row['feature_date'] if row else None

    # Fetch all features for this symbol + version
    rows = conn.execute("""
        SELECT feature_name, feature_value, feature_date
        FROM feature_store
        WHERE symbol = ? AND version = ?
        ORDER BY feature_name
    """, (symbol, version)).fetchall()

    conn.close()

    if not rows:
        return {"success": False, "error": f"No features for symbol='{symbol}' version='{version}'"}

    features = {r['feature_name']: r['feature_value'] for r in rows}

    # Compute age_days
    try:
        fd   = datetime.date.fromisoformat(feature_date)
        age  = (datetime.date.today() - fd).days
    except Exception:
        age = None

    return {
        "success":      True,
        "symbol":       symbol,
        "version":      version,
        "feature_date": feature_date,
        "age_days":     age,
        "n_features":   len(features),
        "features":     features,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  cmd_drift_report
# ══════════════════════════════════════════════════════════════════════════════
def _load_feature_stats(conn: sqlite3.Connection,
                        version: Optional[str],
                        start_date: Optional[str],
                        end_date: Optional[str]) -> dict:
    """Return {feature_name: [values]} for a version/date range."""
    params: list = []
    clauses: list[str] = []

    if version:
        clauses.append("version = ?")
        params.append(version)
    if start_date:
        clauses.append("feature_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("feature_date <= ?")
        params.append(end_date)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows  = conn.execute(
        f"SELECT feature_name, feature_value FROM feature_store {where}",
        params
    ).fetchall()

    data: dict[str, list[float]] = {}
    for r in rows:
        if r['feature_value'] is not None:
            data.setdefault(r['feature_name'], []).append(r['feature_value'])
    return data


def _stats(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "std": None, "min": None, "max": None, "n": 0}
    n    = len(values)
    mean = sum(values) / n
    var  = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
    std  = math.sqrt(var)
    return {"mean": mean, "std": std, "min": min(values), "max": max(values), "n": n}


def cmd_drift_report(params: dict) -> dict:
    """Detect feature drift between two versions or date windows."""
    version_a = params.get('version_a')
    version_b = params.get('version_b')
    start_a   = params.get('start_a', '2025-01-01')
    end_a     = params.get('end_a',   '2025-06-30')
    start_b   = params.get('start_b', '2026-01-01')
    end_b     = params.get('end_b',   '2026-05-31')

    conn = get_db()
    ensure_tables(conn)

    data_a = _load_feature_stats(conn, version_a, start_a if not version_a else None, end_a if not version_a else None)
    data_b = _load_feature_stats(conn, version_b, start_b if not version_b else None, end_b if not version_b else None)
    conn.close()

    if not data_a:
        return {"success": False, "error": "No data found for period A / version_a"}
    if not data_b:
        return {"success": False, "error": "No data found for period B / version_b"}

    all_features = sorted(set(data_a) | set(data_b))
    feature_drift: list[dict] = []
    drifted_count = 0
    DRIFT_THRESHOLD = 2.0

    for feat in all_features:
        sa = _stats(data_a.get(feat, []))
        sb = _stats(data_b.get(feat, []))

        if sa['std'] is not None and sb['mean'] is not None and sa['std'] is not None:
            drift_score = abs(sb['mean'] - sa['mean']) / (sa['std'] + 1e-8)
        else:
            drift_score = None

        drifted = (drift_score is not None and drift_score > DRIFT_THRESHOLD)
        if drifted:
            drifted_count += 1

        feature_drift.append({
            "feature":     feat,
            "period_a":    sa,
            "period_b":    sb,
            "drift_score": round(drift_score, 4) if drift_score is not None else None,
            "status":      "DRIFTED" if drifted else ("OK" if drift_score is not None else "MISSING"),
        })

    overall_verdict = "DRIFTED" if drifted_count > 0 else "STABLE"

    return {
        "success":         True,
        "period_a":        {"version": version_a, "start": start_a, "end": end_a},
        "period_b":        {"version": version_b, "start": start_b, "end": end_b},
        "n_features":      len(all_features),
        "n_drifted":       drifted_count,
        "overall_verdict": overall_verdict,
        "drift_threshold": DRIFT_THRESHOLD,
        "features":        feature_drift,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  cmd_lineage
# ══════════════════════════════════════════════════════════════════════════════
def cmd_lineage(params: dict) -> dict:
    """Show feature lineage records."""
    conn = get_db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT feature_name, version, source_table, computation_logic,
               params, created_at, n_records
        FROM feature_lineage
        ORDER BY created_at DESC
    """).fetchall()

    if not rows:
        conn.close()
        return {"success": True, "lineage": [], "summary": {"n_versions": 0, "n_features": 0}}

    versions  = sorted({r['version'] for r in rows})
    features  = sorted({r['feature_name'] for r in rows})
    dates     = [r['created_at'] for r in rows if r['created_at']]
    oldest    = min(dates) if dates else None
    newest    = max(dates) if dates else None

    lineage = [dict(r) for r in rows]

    conn.close()

    return {
        "success": True,
        "lineage": lineage,
        "summary": {
            "n_versions":       len(versions),
            "n_features":       len(features),
            "versions":         versions,
            "oldest_version":   oldest,
            "newest_version":   newest,
            "total_records":    len(rows),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
#  cmd_report
# ══════════════════════════════════════════════════════════════════════════════
def cmd_report(params: dict) -> dict:
    """Full report: refresh if needed → drift → lineage."""
    today   = datetime.date.today().isoformat()
    version = f"v{today}"
    conn    = get_db()
    ensure_tables(conn)

    count = conn.execute("""
        SELECT COUNT(*) AS n FROM feature_store WHERE feature_date = ? AND version = ?
    """, (today, version)).fetchone()['n']
    conn.close()

    report: dict = {"success": True, "date": today}

    if count == 0:
        print(f"[feature_store] No features for {today}/{version} — running refresh...")
        refresh_result = cmd_refresh({"version": version})
        report["refresh"] = refresh_result
    else:
        report["refresh"] = {"skipped": True, "reason": "features already stored today", "n_rows": count}

    report["drift"]   = cmd_drift_report(params)
    report["lineage"] = cmd_lineage(params)

    return report


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════
COMMANDS = {
    "refresh":      cmd_refresh,
    "get_features": cmd_get_features,
    "drift_report": cmd_drift_report,
    "lineage":      cmd_lineage,
    "report":       cmd_report,
}


def main():
    # Standard calling convention: python3 script.py <command> [JSON_params]
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'Usage: feature_store.py <command> [json_params]',
                          'available': list(COMMANDS.keys())}))
        sys.exit(1)

    cmd = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'success': False, 'error': f'Unknown command: {cmd}',
                          'available': list(COMMANDS.keys())}))
        sys.exit(1)

    result = handler(params)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
