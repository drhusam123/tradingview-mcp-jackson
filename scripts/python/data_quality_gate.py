"""
Phase 55 — Unified Data Quality Gate
EGX Autonomous Quant System

Gatekeeper that ensures all data (daily, weekly, monthly, intraday, cross-market)
meets quality standards before being used in analysis engines.
Provides trust scores for each data source.
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import datetime
import collections

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

QUALITY_RULES = {
    'OHLCV_INTEGRITY':    {'severity': 'CRITICAL', 'auto_fix': False},
    'ZERO_VOLUME':        {'severity': 'WARNING',  'auto_fix': False},
    'PRICE_CONTINUITY':   {'severity': 'HIGH',     'auto_fix': False},
    'TIMESTAMP_GAP':      {'severity': 'MEDIUM',   'auto_fix': False},
    'STALE_DATA':         {'severity': 'MEDIUM',   'auto_fix': False},
    'NEGATIVE_PRICE':     {'severity': 'CRITICAL', 'auto_fix': True},
    'DUPLICATE_BAR':      {'severity': 'HIGH',     'auto_fix': True},
    'CROSS_TF_INCONSIST': {'severity': 'MEDIUM',   'auto_fix': False},
}

# Severity weights for trust-score formula
SEVERITY_WEIGHTS = {
    'CRITICAL': 10,
    'HIGH':      5,
    'MEDIUM':    2,
    'WARNING':   1,
    'INFO':      0,
}

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_quality_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type       TEXT NOT NULL,
            table_name       TEXT NOT NULL,
            symbol           TEXT,
            bar_date         TEXT,
            issue_description TEXT,
            severity         TEXT,
            status           TEXT DEFAULT 'OPEN',
            auto_fixed       INTEGER DEFAULT 0,
            checked_at       TEXT,
            resolved_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS data_trust_scores (
            source           TEXT PRIMARY KEY,
            trust_score      REAL,
            last_checked     TEXT,
            n_issues_open    INTEGER,
            n_issues_critical INTEGER,
            status           TEXT
        );

        CREATE TABLE IF NOT EXISTS data_quality_bar_exclusions (
            symbol            TEXT NOT NULL,
            bar_time          INTEGER NOT NULL,
            trade_date        TEXT NOT NULL,
            source_table      TEXT NOT NULL,
            reason            TEXT NOT NULL,
            severity          TEXT NOT NULL,
            action            TEXT NOT NULL,
            notes             TEXT,
            status            TEXT DEFAULT 'ACTIVE',
            detected_at       TEXT NOT NULL,
            resolved_at       TEXT,
            PRIMARY KEY (source_table, symbol, bar_time, reason)
        );

        CREATE INDEX IF NOT EXISTS idx_dqbe_active
            ON data_quality_bar_exclusions(source_table, status, symbol, bar_time);
    """)
    conn.commit()


def ensure_ohlcv_quality_views(conn, source_table='ohlcv_history'):
    """
    Production-safe OHLCV views.

    Historical EGX data can include legitimate no-trade sessions represented as
    flat zero-volume bars. We keep the raw table immutable, but production
    feature/signal reads should use only actually traded bars.
    """
    if source_table != 'ohlcv_history':
        return

    conn.executescript("""
        DROP VIEW IF EXISTS ohlcv_history_features;
        DROP VIEW IF EXISTS ohlcv_history_execution;

        CREATE VIEW ohlcv_history_features AS
            SELECT h.*
            FROM ohlcv_history h
            WHERE h.close > 0
              AND h.volume > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM data_quality_bar_exclusions e
                  WHERE e.source_table = 'ohlcv_history'
                    AND e.symbol = h.symbol
                    AND e.bar_time = h.bar_time
                    AND e.status = 'ACTIVE'
              );

        CREATE VIEW ohlcv_history_execution AS
            SELECT *
            FROM ohlcv_history_features;
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# EGX trading-day helpers
# ---------------------------------------------------------------------------

# Known EGX public holidays (yyyy-mm-dd) — compiled from EGX announcements
# Covers 2022-2026. Islamic holidays shift ~11 days earlier each Gregorian year.
EGX_HOLIDAYS = set([
    # 2022
    '2022-01-07',  # Coptic Christmas
    '2022-04-25',  # Sinai Liberation Day
    '2022-04-29', '2022-04-30', '2022-05-01', '2022-05-02',  # Eid al-Fitr
    '2022-05-02',  # Labor Day (merged with Eid)
    '2022-06-30',  # June 30 Revolution
    '2022-07-07', '2022-07-08', '2022-07-09', '2022-07-10',  # Eid al-Adha
    '2022-07-11',  # Islamic New Year
    '2022-07-23',  # Revolution Day
    '2022-10-06',  # Armed Forces Day
    '2022-10-08',  # Prophet's Birthday

    # 2023
    '2023-01-07',  # Coptic Christmas
    '2023-01-01',  # New Year
    '2023-04-20', '2023-04-21', '2023-04-22', '2023-04-23',  # Eid al-Fitr
    '2023-04-25',  # Sinai Liberation Day
    '2023-05-01',  # Labor Day
    '2023-06-26', '2023-06-27', '2023-06-28', '2023-06-29',  # Eid al-Adha
    '2023-06-30',  # June 30 Revolution
    '2023-07-19',  # Islamic New Year
    '2023-07-23',  # Revolution Day
    '2023-09-27',  # Prophet's Birthday
    '2023-10-06',  # Armed Forces Day

    # 2024
    '2024-01-07',  # Coptic Christmas
    '2024-01-01',  # New Year
    '2024-04-09', '2024-04-10', '2024-04-11', '2024-04-12', '2024-04-13',  # Eid al-Fitr
    '2024-04-25',  # Sinai Liberation Day
    '2024-05-01',  # Labor Day
    '2024-06-16', '2024-06-17', '2024-06-18', '2024-06-19',  # Eid al-Adha
    '2024-06-30',  # June 30 Revolution
    '2024-07-07',  # Islamic New Year
    '2024-07-23',  # Revolution Day
    '2024-09-15',  # Prophet's Birthday
    '2024-10-06',  # Armed Forces Day

    # 2025
    '2025-01-01',  # New Year
    '2025-01-07',  # Coptic Christmas
    '2025-03-30', '2025-03-31', '2025-04-01', '2025-04-02', '2025-04-03',  # Eid al-Fitr
    '2025-04-20',  # Coptic Easter Sunday (EGX closed)
    '2025-04-21',  # Sham El-Nessim (Mon after Coptic Easter)
    '2025-04-24',  # Sinai Liberation Day
    '2025-05-01',  # Labor Day
    '2025-06-05', '2025-06-06', '2025-06-07', '2025-06-08', '2025-06-09', '2025-06-10',  # Eid al-Adha
    '2025-06-26',  # Islamic New Year 1447 AH
    '2025-06-30',  # June 30 Revolution
    '2025-07-03',  # July 3 Revolution Anniversary (2013)
    '2025-07-08',  # Islamic New Year extended / observed
    '2025-07-23',  # Revolution Day
    '2025-07-24',  # Revolution Day observed (extended holiday)
    '2025-09-04',  # Prophet's Birthday 1447
    '2025-10-06',  # Armed Forces Day
    '2025-10-09',  # Armed Forces Day observed (extended)

    # 2026
    '2026-01-01',  # New Year
    '2026-01-07',  # Coptic Christmas
    '2026-01-25',  # Jan 25 Revolution Day (Sunday = trading day, but EGX may close)
    '2026-01-29',  # Jan 25 Revolution Day observed (extended holiday, Thursday)
    '2026-03-19', '2026-03-20', '2026-03-21', '2026-03-22', '2026-03-23',  # Eid al-Fitr (est.)
    '2026-04-12',  # Coptic Easter Sunday (EGX closed)
    '2026-04-13',  # Sham El-Nessim 2026 (Mon after Coptic Easter)
    '2026-04-25',  # Sinai Liberation Day
    '2026-05-01',  # Labor Day
    '2026-05-26', '2026-05-27', '2026-05-28', '2026-05-29', '2026-05-30', '2026-05-31',  # Eid al-Adha confirmed
    '2026-06-15',  # Islamic New Year (est.)
    '2026-06-30',  # June 30 Revolution
    '2026-07-23',  # Revolution Day
    '2026-08-24',  # Prophet's Birthday (est.)
    '2026-10-06',  # Armed Forces Day
])


def is_egx_trading_day(date_str):
    """
    Returns True if the date is an EGX trading day (Sun–Thu, not a public holiday).

    Primary:  delegates to event_calendar.is_trading_day() which reads from the DB
              and is the single source of truth for 2025-2026+ holidays.
    Fallback: uses the local EGX_HOLIDAYS set for dates before 2025 or when the
              event_calendar module is unavailable.
    """
    try:
        import importlib.util as _ilu, os as _os
        _ec_path = _os.path.join(_os.path.dirname(__file__), 'event_calendar.py')
        _ec_spec = _ilu.spec_from_file_location('event_calendar', _ec_path)
        _ec_mod  = _ilu.module_from_spec(_ec_spec)
        _ec_spec.loader.exec_module(_ec_mod)
        return _ec_mod.is_trading_day(date_str)
    except Exception:
        pass  # event_calendar unavailable — fall through to local set

    # Local fallback (covers 2022-2026 hardcoded list)
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    weekday = dt.weekday()          # 0=Mon … 6=Sun
    if weekday not in [0, 1, 2, 3, 6]:   # Mon, Tue, Wed, Thu, Sun
        return False
    if date_str in EGX_HOLIDAYS:
        return False
    return True


def next_calendar_days(date_str, n=1):
    """Yield the next n calendar dates after date_str."""
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    for _ in range(n):
        dt += datetime.timedelta(days=1)
        yield dt.strftime('%Y-%m-%d')


def unix_to_date(ts):
    """Convert Unix timestamp (int/float/str) to YYYY-MM-DD string."""
    try:
        ts_int = int(float(ts))
        return datetime.datetime.utcfromtimestamp(ts_int).strftime('%Y-%m-%d')
    except (TypeError, ValueError, OSError):
        # ts may already be an ISO date string
        try:
            return str(ts)[:10]
        except Exception:
            return '1970-01-01'


def date_to_unix(date_str):
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


def egx_trading_days_between(date_a, date_b):
    """Return list of EGX trading days strictly between date_a and date_b."""
    da = datetime.datetime.strptime(date_a, '%Y-%m-%d')
    db = datetime.datetime.strptime(date_b, '%Y-%m-%d')
    days = []
    cur = da + datetime.timedelta(days=1)
    while cur < db:
        s = cur.strftime('%Y-%m-%d')
        if is_egx_trading_day(s):
            days.append(s)
        cur += datetime.timedelta(days=1)
    return days


def egx_staleness_trading_days(data_date, ref_date):
    """Return missed EGX trading sessions after data_date through last trading day(ref_date)."""
    try:
        import importlib.util as _ilu, os as _os
        _ec_path = _os.path.join(_os.path.dirname(__file__), 'event_calendar.py')
        _ec_spec = _ilu.spec_from_file_location('event_calendar', _ec_path)
        _ec_mod = _ilu.module_from_spec(_ec_spec)
        _ec_spec.loader.exec_module(_ec_mod)
        return int(_ec_mod.staleness_trading_days(data_date, ref_date))
    except Exception:
        last_td = ref_date
        dt = datetime.datetime.strptime(ref_date, '%Y-%m-%d')
        for _ in range(30):
            s = dt.strftime('%Y-%m-%d')
            if is_egx_trading_day(s):
                last_td = s
                break
            dt -= datetime.timedelta(days=1)
        return len([d for d in egx_trading_days_between(data_date, last_td)] + ([last_td] if is_egx_trading_day(last_td) and last_td > data_date else []))


# ---------------------------------------------------------------------------
# Trust-score calculator
# ---------------------------------------------------------------------------

def compute_trust_score(n_critical, n_high, n_medium, n_warning, n_bars=None):
    """
    Compute a normalized trust score.
    With n_bars provided, uses a per-bar violation rate so the score doesn't
    collapse to 0 for large universes with mostly holiday-gap MEDIUM issues.
    Score = 100 × (1 – weighted_violation_rate), clamped to [0, 100].
    """
    if n_bars and n_bars > 0:
        # Weighted violations as fraction of bars checked
        weighted = (n_critical * 10 + n_high * 2 + n_medium * 0.05 + n_warning * 0.01)
        rate = min(weighted / n_bars, 1.0)
        score = 100.0 * (1.0 - rate)
    else:
        # Legacy absolute formula (fallback for small datasets)
        score = 100 - (n_critical * 10) - (n_high * 1) - (n_medium * 0.1) - (n_warning * 0.05)
    return max(0.0, min(100.0, float(score)))


def score_to_status(score):
    if score >= 80:
        return 'TRUSTED'
    if score >= 50:
        return 'DEGRADED'
    return 'UNRELIABLE'


# ---------------------------------------------------------------------------
# Log helper
# ---------------------------------------------------------------------------

def log_issue(conn, check_type, table_name, symbol, bar_date,
              description, severity, status='OPEN', auto_fixed=0):
    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute("""
        INSERT INTO data_quality_log
            (check_type, table_name, symbol, bar_date,
             issue_description, severity, status, auto_fixed, checked_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (check_type, table_name, symbol, bar_date,
          description, severity, status, int(auto_fixed), now))


# ---------------------------------------------------------------------------
# Command: check_ohlcv_integrity
# ---------------------------------------------------------------------------

def check_ohlcv_integrity(params):
    table       = params.get('table', 'ohlcv_history')
    symbol_filt = params.get('symbol')
    fix_negative = bool(params.get('fix_negative', False))

    conn = get_conn()
    ensure_schema(conn)

    where_clause = ""
    args = []
    if symbol_filt:
        where_clause = "WHERE symbol = ?"
        args = [symbol_filt]

    rows = conn.execute(
        f"SELECT symbol, bar_time, open, high, low, close, volume FROM {table} {where_clause}",
        args
    ).fetchall()

    n_checked = len(rows)
    violations = collections.defaultdict(int)
    symbols_affected = set()
    deleted_negative = 0

    negative_keys = []  # (symbol, bar_time)

    for row in rows:
        sym  = row['symbol']
        ts   = row['bar_time']
        o, h, l, c, v = row['open'], row['high'], row['low'], row['close'], row['volume']
        date_str = unix_to_date(ts)
        issues = []

        # NEGATIVE_PRICE
        if any(p is not None and p < 0 for p in [o, h, l, c]):
            issues.append('NEGATIVE_PRICE')
            violations['NEGATIVE_PRICE'] += 1
            symbols_affected.add(sym)
            if fix_negative:
                negative_keys.append((sym, ts))
            log_issue(conn, 'NEGATIVE_PRICE', table, sym, date_str,
                      f"Negative price value O={o} H={h} L={l} C={c}",
                      QUALITY_RULES['NEGATIVE_PRICE']['severity'],
                      status='AUTO_FIXED' if fix_negative else 'OPEN',
                      auto_fixed=fix_negative)

        # OHLCV_INTEGRITY
        integrity_ok = True
        if h is not None and l is not None:
            if h < l:
                integrity_ok = False
            if o is not None and h < o:
                integrity_ok = False
            if c is not None and h < c:
                integrity_ok = False
            if o is not None and l > o:
                integrity_ok = False
            if c is not None and l > c:
                integrity_ok = False
        if not integrity_ok:
            violations['OHLCV_INTEGRITY'] += 1
            symbols_affected.add(sym)
            log_issue(conn, 'OHLCV_INTEGRITY', table, sym, date_str,
                      f"OHLC integrity violation O={o} H={h} L={l} C={c}",
                      QUALITY_RULES['OHLCV_INTEGRITY']['severity'])

        # ZERO_VOLUME
        if v is not None and v == 0:
            violations['ZERO_VOLUME'] += 1
            symbols_affected.add(sym)
            log_issue(conn, 'ZERO_VOLUME', table, sym, date_str,
                      "Volume is zero on a trading day",
                      QUALITY_RULES['ZERO_VOLUME']['severity'])

    # Apply fix_negative deletions
    if fix_negative and negative_keys:
        for sym, ts in negative_keys:
            conn.execute(
                f"DELETE FROM {table} WHERE symbol=? AND bar_time=?", (sym, ts)
            )
        deleted_negative = len(negative_keys)

    conn.commit()
    conn.close()

    return {
        'success': True,
        'command': 'check_ohlcv_integrity',
        'table': table,
        'n_checked': n_checked,
        'violations': dict(violations),
        'symbols_affected': sorted(symbols_affected),
        'deleted_negative_bars': deleted_negative,
    }


# ---------------------------------------------------------------------------
# Command: build_zero_volume_gate
# ---------------------------------------------------------------------------

def build_zero_volume_gate(params):
    """
    Conservative production gate for ZERO_VOLUME history.

    Policy:
      - Raw ohlcv_history remains intact by default.
      - All zero-volume bars are marked ACTIVE in data_quality_bar_exclusions.
      - Feature/execution views include only close > 0 and volume > 0 bars.
      - Optional delete_nonflat=True deletes only impossible bars that moved
        intraday while volume was zero. Flat no-trade sessions are never deleted.
    """
    table = params.get('table', 'ohlcv_history')
    delete_nonflat = bool(params.get('delete_nonflat', False))
    symbol_filt = params.get('symbol')

    conn = get_conn()
    ensure_schema(conn)

    where = "WHERE volume = 0"
    args = []
    if symbol_filt:
        where += " AND symbol = ?"
        args.append(symbol_filt)

    rows = conn.execute(f"""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM {table}
        {where}
        ORDER BY symbol, bar_time
    """, args).fetchall()

    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    by_symbol = collections.defaultdict(lambda: {'zero': 0, 'flat': 0, 'nonflat': 0})
    first_last = {}
    nonflat_keys = []

    upsert = """
        INSERT INTO data_quality_bar_exclusions
            (symbol, bar_time, trade_date, source_table, reason, severity,
             action, notes, status, detected_at, resolved_at)
        VALUES (?,?,?,?,?,?,?,?, 'ACTIVE', ?, NULL)
        ON CONFLICT(source_table, symbol, bar_time, reason) DO UPDATE SET
            trade_date=excluded.trade_date,
            severity=excluded.severity,
            action=excluded.action,
            notes=excluded.notes,
            status='ACTIVE',
            detected_at=excluded.detected_at,
            resolved_at=NULL
    """

    for row in rows:
        sym = row['symbol']
        ts = int(row['bar_time'])
        date_str = unix_to_date(ts)
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        is_flat = (o == h == l == c)

        by_symbol[sym]['zero'] += 1
        if is_flat:
            by_symbol[sym]['flat'] += 1
            reason = 'ZERO_VOLUME_FLAT_NO_TRADE'
            severity = 'WARNING'
            action = 'EXCLUDE_FROM_FEATURES_AND_EXECUTION'
            notes = 'Flat no-trade session retained in raw history; excluded from production feature/signal views.'
        else:
            by_symbol[sym]['nonflat'] += 1
            reason = 'ZERO_VOLUME_NONFLAT_CORRUPT'
            severity = 'HIGH'
            action = 'EXCLUDE_FROM_FEATURES_AND_EXECUTION'
            notes = f'Price bar moved with zero volume O={o} H={h} L={l} C={c}; raw row retained unless delete_nonflat=true.'
            nonflat_keys.append((sym, ts))

        if sym not in first_last:
            first_last[sym] = {'first': date_str, 'last': date_str}
        else:
            first_last[sym]['last'] = date_str

        conn.execute(upsert, (
            sym, ts, date_str, table, reason, severity, action, notes, now
        ))

    deleted_nonflat = 0
    if delete_nonflat and nonflat_keys:
        for sym, ts in nonflat_keys:
            conn.execute(
                f"DELETE FROM {table} WHERE symbol=? AND bar_time=? AND volume=0",
                (sym, ts),
            )
        deleted_nonflat = len(nonflat_keys)

    ensure_ohlcv_quality_views(conn, table)

    active_excluded = conn.execute("""
        SELECT COUNT(*) AS n
        FROM data_quality_bar_exclusions
        WHERE source_table=? AND status='ACTIVE'
    """, (table,)).fetchone()['n']

    clean_count = None
    if table == 'ohlcv_history':
        clean_count = conn.execute(
            "SELECT COUNT(*) AS n FROM ohlcv_history_features"
        ).fetchone()['n']

    conn.commit()
    conn.close()

    symbols = []
    for sym, counts in by_symbol.items():
        symbols.append({
            'symbol': sym,
            'zero_volume': counts['zero'],
            'flat_no_trade': counts['flat'],
            'nonflat_corrupt': counts['nonflat'],
            'first_zero': first_last.get(sym, {}).get('first'),
            'last_zero': first_last.get(sym, {}).get('last'),
        })

    symbols.sort(key=lambda s: (s['nonflat_corrupt'], s['zero_volume']), reverse=True)

    return {
        'success': True,
        'command': 'build_zero_volume_gate',
        'table': table,
        'policy': 'raw table preserved; zero-volume bars excluded from production feature/execution views',
        'n_zero_volume': len(rows),
        'n_flat_no_trade': sum(s['flat_no_trade'] for s in symbols),
        'n_nonflat_corrupt': sum(s['nonflat_corrupt'] for s in symbols),
        'n_symbols_affected': len(symbols),
        'active_exclusions': active_excluded,
        'deleted_nonflat_bars': deleted_nonflat,
        'production_views': ['ohlcv_history_features', 'ohlcv_history_execution'] if table == 'ohlcv_history' else [],
        'clean_view_rows': clean_count,
        'worst_symbols': symbols[:20],
    }


# ---------------------------------------------------------------------------
# Command: check_timestamp_gaps
# ---------------------------------------------------------------------------

def check_timestamp_gaps(params):
    table       = params.get('table', 'ohlcv_history')
    symbol_filt = params.get('symbol')

    conn = get_conn()
    ensure_schema(conn)

    where_clause = ""
    args = []
    if symbol_filt:
        where_clause = "WHERE symbol = ?"
        args = [symbol_filt]

    rows = conn.execute(
        f"SELECT symbol, bar_time FROM {table} {where_clause} ORDER BY symbol, bar_time",
        args
    ).fetchall()

    # Group by symbol
    sym_dates = collections.defaultdict(list)
    for row in rows:
        sym_dates[row['symbol']].append(unix_to_date(row['bar_time']))

    gaps_found = []
    total_gaps = 0

    for sym, dates in sym_dates.items():
        dates = sorted(set(dates))
        for i in range(1, len(dates)):
            prev_d, cur_d = dates[i - 1], dates[i]
            missing = egx_trading_days_between(prev_d, cur_d)
            if missing:
                total_gaps += len(missing)
                desc = f"Missing {len(missing)} EGX trading day(s) between {prev_d} and {cur_d}: {missing[:5]}"
                gaps_found.append({
                    'symbol': sym,
                    'from': prev_d,
                    'to': cur_d,
                    'missing_days': missing,
                })
                log_issue(conn, 'TIMESTAMP_GAP', table, sym, prev_d,
                          desc, QUALITY_RULES['TIMESTAMP_GAP']['severity'])

    conn.commit()
    conn.close()

    return {
        'success': True,
        'command': 'check_timestamp_gaps',
        'table': table,
        'n_symbols_checked': len(sym_dates),
        'total_gaps_found': total_gaps,
        'gaps': gaps_found[:100],   # cap output
    }


# ---------------------------------------------------------------------------
# Command: check_price_continuity
# ---------------------------------------------------------------------------

def check_price_continuity(params):
    threshold_pct = float(params.get('threshold_pct', 20))
    table         = params.get('table', 'ohlcv_history')

    conn = get_conn()
    ensure_schema(conn)

    rows = conn.execute(
        f"SELECT symbol, bar_time, close FROM {table} ORDER BY symbol, bar_time"
    ).fetchall()

    # Check if corporate_actions table exists
    has_corp_actions = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
    ).fetchone() is not None

    corp_action_keys = set()
    if has_corp_actions:
        ca_rows = conn.execute(
            "SELECT symbol, event_date FROM corporate_actions"
        ).fetchall()
        for ca in ca_rows:
            corp_action_keys.add((ca['symbol'], ca['event_date']))

    suspicious = []
    sym_dates = collections.defaultdict(list)
    for row in rows:
        sym_dates[row['symbol']].append((unix_to_date(row['bar_time']), row['close']))

    for sym, pairs in sym_dates.items():
        pairs = sorted(set(pairs), key=lambda x: x[0])
        for i in range(1, len(pairs)):
            prev_date, prev_close = pairs[i - 1]
            cur_date,  cur_close  = pairs[i]
            if prev_close is None or prev_close == 0 or cur_close is None:
                continue
            gap_pct = abs(cur_close / prev_close - 1) * 100
            if gap_pct > threshold_pct:
                is_known = (sym, cur_date) in corp_action_keys
                desc = (f"Price jump {gap_pct:.1f}% from {prev_close:.4f} to {cur_close:.4f} "
                        f"on {cur_date}"
                        + (" [known corporate action]" if is_known else " [UNEXPLAINED]"))
                suspicious.append({
                    'symbol':   sym,
                    'date':     cur_date,
                    'prev_close': prev_close,
                    'cur_close':  cur_close,
                    'gap_pct':  round(gap_pct, 2),
                    'is_known_corporate_action': is_known,
                })
                if not is_known:
                    log_issue(conn, 'PRICE_CONTINUITY', table, sym, cur_date,
                              desc, QUALITY_RULES['PRICE_CONTINUITY']['severity'])

    conn.commit()
    conn.close()

    return {
        'success': True,
        'command': 'check_price_continuity',
        'table': table,
        'threshold_pct': threshold_pct,
        'n_suspicious_moves': len(suspicious),
        'n_unexplained': sum(1 for s in suspicious if not s['is_known_corporate_action']),
        'suspicious_moves': suspicious[:100],
    }


# ---------------------------------------------------------------------------
# Command: check_stale_data
# ---------------------------------------------------------------------------

def check_stale_data(params):
    max_age_days = int(params.get('max_age_days', 5))
    today_str    = datetime.datetime.utcnow().strftime('%Y-%m-%d')

    conn = get_conn()
    ensure_schema(conn)

    sources = {
        'ohlcv_daily':    {'table': 'ohlcv_history',       'max_trading_days': int(params.get('max_trading_days', 0))},
        'cross_market':   {'table': 'cross_market_daily',  'max_age': max_age_days},
        'ohlcv_weekly':   {'table': 'ohlcv_weekly',        'max_age': 10},
        'ohlcv_monthly':  {'table': 'ohlcv_monthly',       'max_age': 45},
    }

    stale_sources = []
    checked = []

    for source_name, cfg in sources.items():
        tbl = cfg['table']
        # Check if table exists
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not exists:
            checked.append({
                'source': source_name,
                'table': tbl,
                'status': 'TABLE_MISSING',
                'last_update': None,
                'days_stale': None,
            })
            continue

        # Get latest bar_time
        row = conn.execute(f"SELECT MAX(bar_time) AS latest FROM {tbl}").fetchone()
        latest_ts = row['latest'] if row else None

        if latest_ts is None:
            checked.append({
                'source': source_name,
                'table': tbl,
                'status': 'NO_DATA',
                'last_update': None,
                'days_stale': None,
            })
            continue

        latest_date = unix_to_date(latest_ts)
        today_dt = datetime.datetime.strptime(today_str, '%Y-%m-%d')
        latest_dt = datetime.datetime.strptime(latest_date, '%Y-%m-%d')
        calendar_days = (today_dt - latest_dt).days

        if 'max_trading_days' in cfg:
            delta_days = egx_staleness_trading_days(latest_date, today_str)
            max_age = cfg['max_trading_days']
            units = 'trading_sessions'
        else:
            delta_days = calendar_days
            max_age = cfg['max_age']
            units = 'calendar_days'

        is_stale = delta_days > max_age
        entry = {
            'source':      source_name,
            'table':       tbl,
            'last_update': latest_date,
            'days_stale':  delta_days,
            'calendar_days_stale': calendar_days,
            'max_age':     max_age,
            'stale_units': units,
            'is_stale':    is_stale,
        }
        checked.append(entry)

        if is_stale:
            stale_sources.append(entry)
            log_issue(conn, 'STALE_DATA', tbl, None, latest_date,
                      f"Source '{source_name}' last updated {latest_date}, {delta_days} {units} stale (max={max_age})",
                      QUALITY_RULES['STALE_DATA']['severity'])

    conn.commit()
    conn.close()

    return {
        'success': True,
        'command': 'check_stale_data',
        'today': today_str,
        'max_age_days': max_age_days,
        'stale_sources': stale_sources,
        'all_sources': checked,
    }


# ---------------------------------------------------------------------------
# Command: full_audit
# ---------------------------------------------------------------------------

def full_audit(params):
    tables = params.get('tables', ['ohlcv_history', 'ohlcv_weekly', 'cross_market_daily'])

    conn = get_conn()
    ensure_schema(conn)
    conn.close()

    audit_results = {}
    trust_scores  = {}

    for tbl in tables:
        # Check table exists
        conn2 = get_conn()
        exists = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        conn2.close()

        if not exists:
            audit_results[tbl] = {'skipped': True, 'reason': 'Table does not exist'}
            continue

        integrity_res  = check_ohlcv_integrity({'table': tbl})
        gap_res        = check_timestamp_gaps({'table': tbl})
        continuity_res = check_price_continuity({'table': tbl})
        stale_res      = check_stale_data({'max_age_days': 5})

        # Count issues logged for this table from the DB
        conn3 = get_conn()
        rows = conn3.execute("""
            SELECT severity, COUNT(*) AS cnt
            FROM data_quality_log
            WHERE table_name=? AND status='OPEN'
            GROUP BY severity
        """, (tbl,)).fetchall()
        conn3.close()

        sev_counts = collections.defaultdict(int)
        for r in rows:
            sev_counts[r['severity']] += r['cnt']

        n_critical = sev_counts['CRITICAL']
        n_high     = sev_counts['HIGH']
        n_medium   = sev_counts['MEDIUM']
        n_warning  = sev_counts['WARNING']
        n_open     = sum(sev_counts.values())

        # Pass n_bars for normalized trust score
        n_bars = integrity_res.get('n_checked', 0) if isinstance(integrity_res, dict) else 0
        score  = compute_trust_score(n_critical, n_high, n_medium, n_warning, n_bars=n_bars or None)
        status = score_to_status(score)

        # Upsert trust score
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        conn4 = get_conn()
        conn4.execute("""
            INSERT INTO data_trust_scores
                (source, trust_score, last_checked, n_issues_open, n_issues_critical, status)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
                trust_score=excluded.trust_score,
                last_checked=excluded.last_checked,
                n_issues_open=excluded.n_issues_open,
                n_issues_critical=excluded.n_issues_critical,
                status=excluded.status
        """, (tbl, score, now, n_open, n_critical, status))
        conn4.commit()
        conn4.close()

        audit_results[tbl] = {
            'integrity':      integrity_res,
            'timestamp_gaps': gap_res,
            'price_continuity': continuity_res,
            'n_critical':     n_critical,
            'n_high':         n_high,
            'n_medium':       n_medium,
            'n_warning':      n_warning,
            'trust_score':    score,
            'status':         status,
        }

        trust_scores[tbl] = {
            'trust_score': score,
            'status': status,
            'n_issues_open': n_open,
            'n_issues_critical': n_critical,
        }

    stale_res = check_stale_data({'max_age_days': 5})

    return {
        'success': True,
        'command': 'full_audit',
        'tables_audited': tables,
        'results': audit_results,
        'trust_scores': trust_scores,
        'stale_data_check': stale_res,
    }


# ---------------------------------------------------------------------------
# Command: get_trust_scores
# ---------------------------------------------------------------------------

def get_trust_scores(params):
    conn = get_conn()
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT * FROM data_trust_scores ORDER BY trust_score ASC"
    ).fetchall()
    conn.close()

    if not rows:
        return {
            'success': True,
            'command': 'get_trust_scores',
            'message': 'No trust scores found. Run full_audit or build_full first.',
            'scores': [],
        }

    scores = [dict(r) for r in rows]
    return {
        'success': True,
        'command': 'get_trust_scores',
        'scores': scores,
        'summary': {
            'total_sources': len(scores),
            'trusted':    sum(1 for s in scores if s['status'] == 'TRUSTED'),
            'degraded':   sum(1 for s in scores if s['status'] == 'DEGRADED'),
            'unreliable': sum(1 for s in scores if s['status'] == 'UNRELIABLE'),
        },
    }


# ---------------------------------------------------------------------------
# Command: get_open_issues
# ---------------------------------------------------------------------------

def get_open_issues(params):
    severity_filt = params.get('severity')
    table_filt    = params.get('table')

    conn = get_conn()
    ensure_schema(conn)

    where_parts = ["status IN ('OPEN','MANUAL_REVIEW')"]
    args = []
    if severity_filt:
        where_parts.append("severity = ?")
        args.append(severity_filt.upper())
    if table_filt:
        where_parts.append("table_name = ?")
        args.append(table_filt)

    where_clause = " AND ".join(where_parts)

    rows = conn.execute(
        f"""SELECT id, check_type, table_name, symbol, bar_date,
                   issue_description, severity, status, checked_at
            FROM data_quality_log
            WHERE {where_clause}
            ORDER BY
                CASE severity
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'HIGH'     THEN 2
                    WHEN 'MEDIUM'   THEN 3
                    WHEN 'WARNING'  THEN 4
                    ELSE 5
                END,
                checked_at DESC""",
        args
    ).fetchall()

    # Group by severity
    by_severity = collections.defaultdict(list)
    for row in rows:
        by_severity[row['severity']].append(dict(row))

    conn.close()

    return {
        'success': True,
        'command': 'get_open_issues',
        'total_open': len(rows),
        'by_severity': {sev: issues for sev, issues in by_severity.items()},
        'counts': {sev: len(issues) for sev, issues in by_severity.items()},
    }


# ---------------------------------------------------------------------------
# Command: quarantine_symbol
# ---------------------------------------------------------------------------

def quarantine_symbol(params):
    symbol = params.get('symbol')
    reason = params.get('reason', 'Manual quarantine')

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}

    conn = get_conn()
    ensure_schema(conn)

    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute("""
        INSERT INTO data_quality_log
            (check_type, table_name, symbol, bar_date,
             issue_description, severity, status, auto_fixed, checked_at)
        VALUES ('QUARANTINE','ALL',?,NULL,?,?,?,0,?)
    """, (symbol, reason, 'CRITICAL', 'QUARANTINED', now))
    conn.commit()
    conn.close()

    return {
        'success': True,
        'command': 'quarantine_symbol',
        'symbol': symbol,
        'reason': reason,
        'quarantined_at': now,
        'note': 'Symbol marked as QUARANTINED. Exclude from law discovery and recommendations.',
    }


# ---------------------------------------------------------------------------
# Command: get_quarantined_symbols
# ---------------------------------------------------------------------------

def get_quarantined_symbols(params):
    conn = get_conn()
    ensure_schema(conn)

    rows = conn.execute("""
        SELECT symbol, issue_description AS reason, checked_at AS quarantined_at
        FROM data_quality_log
        WHERE status='QUARANTINED' AND check_type='QUARANTINE'
        ORDER BY checked_at DESC
    """).fetchall()
    conn.close()

    symbols = [dict(r) for r in rows]

    return {
        'success': True,
        'command': 'get_quarantined_symbols',
        'count': len(symbols),
        'quarantined_symbols': symbols,
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params):
    """Daily data health check — run full audit on all known tables,
    compute trust scores, return system data health summary."""

    # Discover all tables in the DB
    conn = get_conn()
    ensure_schema(conn)
    all_tables = [
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    conn.close()

    # Tables we care about for OHLCV-style audits (must have bar_time + symbol columns)
    # cross_market_daily uses 'asset' not 'symbol' — exclude it from symbol-based audits
    _ohlcv_tables = ('ohlcv_history', 'ohlcv_weekly', 'ohlcv_15min', 'ohlcv_60min', 'ohlcv_monthly')
    audit_candidates = [t for t in all_tables if t in _ohlcv_tables]

    if not audit_candidates:
        audit_candidates = ['ohlcv_history']   # fallback

    audit_result = full_audit({'tables': audit_candidates})
    stale_result = check_stale_data({'max_age_days': 5})
    scores_result = get_trust_scores({})
    open_result   = get_open_issues({})

    # Overall system health
    scores = scores_result.get('scores', [])
    if scores:
        avg_score = sum(s['trust_score'] for s in scores) / len(scores)
        worst_score = min(s['trust_score'] for s in scores)
    else:
        avg_score   = 0.0
        worst_score = 0.0

    n_critical_open = open_result.get('counts', {}).get('CRITICAL', 0)
    n_open_total    = open_result.get('total_open', 0)

    if n_critical_open > 0:
        system_status = 'CRITICAL'
    elif avg_score < 50:
        system_status = 'UNRELIABLE'
    elif avg_score < 80:
        system_status = 'DEGRADED'
    else:
        system_status = 'HEALTHY'

    return {
        'success': True,
        'command': 'build_full',
        'system_status': system_status,
        'avg_trust_score': round(avg_score, 1),
        'worst_trust_score': round(worst_score, 1),
        'n_open_issues': n_open_total,
        'n_critical_open': n_critical_open,
        'tables_audited': audit_candidates,
        'stale_check':    stale_result,
        'trust_scores':   scores_result,
        'open_issues_summary': open_result.get('counts', {}),
        'full_audit_results': audit_result.get('results', {}),
    }


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'check_ohlcv_integrity':  check_ohlcv_integrity,
    'build_zero_volume_gate': build_zero_volume_gate,
    'check_timestamp_gaps':   check_timestamp_gaps,
    'check_price_continuity': check_price_continuity,
    'check_stale_data':       check_stale_data,
    'full_audit':             full_audit,
    'get_trust_scores':       get_trust_scores,
    'get_open_issues':        get_open_issues,
    'quarantine_symbol':      quarantine_symbol,
    'get_quarantined_symbols': get_quarantined_symbols,
    'build_full':             build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success': False,
            'error': 'Usage: python data_quality_gate.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    params = json.loads(sys.argv[2])

    if cmd not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error': f"Unknown command: '{cmd}'",
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    result = COMMANDS[cmd](params)
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
