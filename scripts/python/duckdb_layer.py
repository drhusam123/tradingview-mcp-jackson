"""
DuckDB Analytics Layer — Ph74
===============================
Fast read-only analytical queries via Parquet cache.

Strategy:
  - SQLite for all WRITES and indexed per-symbol reads
  - Parquet cache (exported nightly) for full-table analytical reads
  - DuckDB for vectorized aggregations on large tables

Performance vs SQLite (benchmarked on 75K-row ohlcv_history, 73K closing_pressure):
  cp_daily aggregation  : SQLite 200ms  →  DuckDB 27ms   (7x faster)
  ohlcv full scan       : SQLite 962ms  →  DuckDB 216ms  (4x faster)
  Indexed symbol lookup : SQLite 32ms   →  DuckDB SLOWER — NEVER use DuckDB for these

Usage:
  from duckdb_layer import export_parquet_snapshot, cp_agg_fast, ohlcv_parquet, get_duck

Called from:
  - night_lab.py          : export_parquet_snapshot() at end of nightly run
  - egx_ml_trainer.py Ph51: cp_agg_fast() in _load_breadth_for_ph51()
  - egx_ml_trainer.py Ph55: ohlcv_parquet() to replace pd.read_sql_query
  - egx_ml_trainer.py Ph56: ohlcv_parquet() for Markov feature computation
"""

from __future__ import annotations
import sqlite3, time, json
from pathlib import Path
from datetime import datetime, timezone

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

try:
    import duckdb
    _DUCKDB = True
except ImportError:
    _DUCKDB = False

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).resolve().parent
_ROOT        = _HERE.parent.parent
DATA_DIR     = _ROOT / 'data'
PARQUET_DIR  = DATA_DIR / 'parquet'
DB_PATH      = DATA_DIR / 'egx_trading.db'
MANIFEST_PATH= PARQUET_DIR / '_manifest.json'

# ── Tables exported to Parquet (name → SQL query on SQLite) ──────────────────
_EXPORT_TABLES: dict[str, str] = {
    'ohlcv_history': """
        SELECT symbol,
               bar_time,
               date(bar_time, 'unixepoch') AS trade_date,
               open, high, low, close, volume
        FROM ohlcv_history
        WHERE close > 0 AND volume > 0
        ORDER BY bar_time, symbol
    """,
    'closing_pressure_daily': """
        SELECT *
        FROM closing_pressure_daily
        ORDER BY trade_date, symbol
    """,
    'market_breadth_enhanced': """
        SELECT *
        FROM market_breadth_enhanced
        ORDER BY date
    """,
    'markov_signal_daily': """
        SELECT *
        FROM markov_signal_daily
        ORDER BY date
    """,
    'markov_regime_daily': """
        SELECT *
        FROM markov_regime_daily
        ORDER BY date
    """,
    'stock_universe': """
        SELECT symbol, name, sector, status, successor_symbol, archived_at,
               hygiene_reason, last_fetch, total_bars
        FROM stock_universe
        ORDER BY symbol
    """,
    'indicators_cache': """
        SELECT *
        FROM indicators_cache
        WHERE bar_date NOT LIKE '2099-%'
        ORDER BY bar_date, symbol
    """,
    'ohlcv_60min': """
        SELECT symbol,
               bar_time,
               date(bar_time, 'unixepoch') AS trade_date,
               open, high, low, close, volume
        FROM ohlcv_60min
        WHERE close > 0
        ORDER BY bar_time, symbol
    """,
}

# Maximum age (seconds) before a Parquet file is considered stale.
# 25h = safe margin for next-day runs; override per-table below.
_MAX_AGE_SEC = 25 * 3600


# ═════════════════════════════════════════════════════════════════════════════
# Export
# ═════════════════════════════════════════════════════════════════════════════

def export_parquet_snapshot(
    tables: list[str] | None = None,
    force: bool = False,
    verbose: bool = True,
    db_path: str | Path | None = None,
) -> dict:
    """
    Export key analytical tables from SQLite to Parquet.
    Called at the end of each nightly run in night_lab.py.

    Args:
        tables  : list of table names to export (None = all _EXPORT_TABLES)
        force   : overwrite even if Parquet is fresh
        verbose : print progress lines
        db_path : override default DB path

    Returns dict with keys: exported, skipped, failed, duration_sec
    """
    if not _PANDAS:
        return {'error': 'pandas not available', 'exported': [], 'skipped': [], 'failed': []}
    if not _DUCKDB:
        return {'error': 'duckdb not available', 'exported': [], 'skipped': [], 'failed': []}

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(db_path or DB_PATH)
    t0  = time.time()

    to_export = tables or list(_EXPORT_TABLES.keys())
    exported, skipped, failed = [], [], []
    manifest: dict = {}

    # Load existing manifest
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
    except Exception:
        manifest = {}

    conn = sqlite3.connect(str(src))
    try:
        for tbl in to_export:
            pq_path = PARQUET_DIR / f'{tbl}.parquet'
            sql     = _EXPORT_TABLES.get(tbl)
            if not sql:
                if verbose:
                    print(f'[DuckDB] SKIP {tbl}: not in export list', flush=True)
                skipped.append(tbl)
                continue

            # Check freshness
            if not force and pq_path.exists():
                age = time.time() - pq_path.stat().st_mtime
                if age < _MAX_AGE_SEC:
                    if verbose:
                        print(f'[DuckDB] SKIP {tbl}: Parquet is {age/3600:.1f}h old (fresh)', flush=True)
                    skipped.append(tbl)
                    continue

            try:
                t_start = time.time()
                df = pd.read_sql_query(sql, conn)
                df.to_parquet(str(pq_path), index=False, compression='snappy')
                dur_ms = int((time.time() - t_start) * 1000)
                size_kb = pq_path.stat().st_size // 1024
                manifest[tbl] = {
                    'exported_at': datetime.now(timezone.utc).isoformat(),
                    'rows': len(df),
                    'size_kb': size_kb,
                    'duration_ms': dur_ms,
                }
                exported.append(tbl)
                if verbose:
                    print(f'[DuckDB] EXPORT {tbl}: {len(df):,} rows → {size_kb}KB ({dur_ms}ms)', flush=True)
            except Exception as e:
                failed.append({'table': tbl, 'error': str(e)})
                if verbose:
                    print(f'[DuckDB] FAIL {tbl}: {e}', flush=True)
    finally:
        conn.close()

    # Write manifest
    try:
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    except Exception:
        pass

    total_dur = round(time.time() - t0, 1)
    if verbose:
        print(f'[DuckDB] Snapshot done: {len(exported)} exported, '
              f'{len(skipped)} skipped, {len(failed)} failed in {total_dur}s', flush=True)

    return {
        'exported': exported,
        'skipped':  skipped,
        'failed':   failed,
        'duration_sec': total_dur,
    }


# ═════════════════════════════════════════════════════════════════════════════
# DuckDB connection
# ═════════════════════════════════════════════════════════════════════════════

def get_duck(read_only: bool = True) -> 'duckdb.DuckDBPyConnection':
    """
    Return an in-memory DuckDB connection with Parquet views registered.
    Views are registered only for Parquet files that exist.

    Example:
        duck = get_duck()
        df = duck.execute("SELECT * FROM ohlcv_history").df()
        duck.close()
    """
    if not _DUCKDB:
        raise ImportError('duckdb is not installed: pip install duckdb')

    duck = duckdb.connect()

    for tbl in _EXPORT_TABLES:
        pq = PARQUET_DIR / f'{tbl}.parquet'
        if pq.exists():
            duck.execute(
                f"CREATE VIEW {tbl} AS SELECT * FROM read_parquet('{pq}')"
            )

    return duck


def _parquet_fresh(table: str) -> bool:
    """Return True if the Parquet file for `table` exists and is fresh."""
    pq = PARQUET_DIR / f'{table}.parquet'
    if not pq.exists():
        return False
    return (time.time() - pq.stat().st_mtime) < _MAX_AGE_SEC


# ═════════════════════════════════════════════════════════════════════════════
# High-level analytical functions
# ═════════════════════════════════════════════════════════════════════════════

def cp_agg_fast(sqlite_conn=None) -> 'pd.DataFrame | None':
    """
    Return closing_pressure_daily aggregated by trade_date.
    Uses DuckDB+Parquet (27ms) if available; falls back to SQLite (200ms).

    Columns returned:
      trade_date, mkt_close_pos_med, mkt_cp_pressure_med,
      mkt_vol_surge_med, mkt_gap_pct, mkt_reversal_pct

    Used by _load_breadth_for_ph51() in egx_ml_trainer.py.
    """
    if not _PANDAS:
        return None

    _SQL = """
        SELECT trade_date,
               AVG(close_pos)           AS mkt_close_pos_med,
               AVG(closing_pressure)    AS mkt_cp_pressure_med,
               AVG(vol_surge)           AS mkt_vol_surge_med,
               CAST(SUM(gap_potential) AS REAL) / COUNT(*) AS mkt_gap_pct,
               CAST(SUM(intraday_reversal) AS REAL) / COUNT(*) AS mkt_reversal_pct
        FROM closing_pressure_daily
        GROUP BY trade_date
        ORDER BY trade_date
    """

    # Try DuckDB Parquet (fast path)
    if _DUCKDB and _parquet_fresh('closing_pressure_daily'):
        try:
            pq   = PARQUET_DIR / 'closing_pressure_daily.parquet'
            duck = duckdb.connect()
            df   = duck.execute(
                f"SELECT trade_date, "
                f"AVG(close_pos) AS mkt_close_pos_med, "
                f"AVG(closing_pressure) AS mkt_cp_pressure_med, "
                f"AVG(vol_surge) AS mkt_vol_surge_med, "
                f"CAST(SUM(gap_potential) AS REAL) / COUNT(*) AS mkt_gap_pct, "
                f"CAST(SUM(intraday_reversal) AS REAL) / COUNT(*) AS mkt_reversal_pct "
                f"FROM read_parquet('{pq}') "
                f"GROUP BY trade_date ORDER BY trade_date"
            ).df()
            duck.close()
            return df
        except Exception:
            pass  # fall through to SQLite

    # SQLite fallback
    if sqlite_conn is not None:
        try:
            return pd.read_sql_query(_SQL, sqlite_conn)
        except Exception:
            return None

    # Last resort: open own connection
    try:
        conn = sqlite3.connect(str(DB_PATH))
        df   = pd.read_sql_query(_SQL, conn)
        conn.close()
        return df
    except Exception:
        return None


def ohlcv_parquet(filter_positive: bool = True) -> 'pd.DataFrame | None':
    """
    Return ohlcv_history as a DataFrame from Parquet cache (4x faster than SQLite).
    Falls back to SQLite if Parquet is stale or unavailable.

    Columns: symbol, bar_time, trade_date, open, high, low, close, volume

    Used by Ph55 and Ph56 loading paths.
    """
    if not _PANDAS:
        return None

    # DuckDB Parquet path
    if _DUCKDB and _parquet_fresh('ohlcv_history'):
        try:
            pq   = PARQUET_DIR / 'ohlcv_history.parquet'
            duck = duckdb.connect()
            where = "WHERE close > 0 AND volume > 0" if filter_positive else ""
            df   = duck.execute(
                f"SELECT symbol, bar_time, trade_date, open, high, low, close, volume "
                f"FROM read_parquet('{pq}') {where} "
                f"ORDER BY bar_time, symbol"
            ).df()
            duck.close()
            return df
        except Exception:
            pass

    # SQLite fallback
    try:
        where = "WHERE close > 0 AND volume > 0" if filter_positive else ""
        conn  = sqlite3.connect(str(DB_PATH))
        df    = pd.read_sql_query(
            f"SELECT symbol, date(bar_time,'unixepoch') AS trade_date, "
            f"bar_time, open, high, low, close, volume "
            f"FROM ohlcv_history {where} ORDER BY bar_time, symbol",
            conn
        )
        conn.close()
        return df
    except Exception:
        return None


def breadth_and_signals_fast(sqlite_conn=None) -> tuple:
    """
    Return (breadth_df, markov_df) from Parquet if fresh, else SQLite.
    Used by _load_breadth_for_ph51() fast path.

    Returns: (market_breadth_enhanced_df, markov_signal_daily_df)
    """
    if not _PANDAS:
        return None, None

    results = {}
    for tbl in ('market_breadth_enhanced', 'markov_signal_daily'):
        if _DUCKDB and _parquet_fresh(tbl):
            try:
                pq   = PARQUET_DIR / f'{tbl}.parquet'
                duck = duckdb.connect()
                results[tbl] = duck.execute(f"SELECT * FROM read_parquet('{pq}')").df()
                duck.close()
                continue
            except Exception:
                pass

        # SQLite fallback
        try:
            _conn = sqlite_conn or sqlite3.connect(str(DB_PATH))
            results[tbl] = pd.read_sql_query(f"SELECT * FROM {tbl} ORDER BY date", _conn)
            if not sqlite_conn:
                _conn.close()
        except Exception:
            results[tbl] = None

    return results.get('market_breadth_enhanced'), results.get('markov_signal_daily')


# ═════════════════════════════════════════════════════════════════════════════
# CLI — run directly to export snapshot
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    force = '--force' in sys.argv
    verbose = '--quiet' not in sys.argv
    result = export_parquet_snapshot(force=force, verbose=verbose)
    print(json.dumps(result, indent=2))
