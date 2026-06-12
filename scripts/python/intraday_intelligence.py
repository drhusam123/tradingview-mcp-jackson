"""
intraday_intelligence.py — Phase 50: Intraday Intelligence Layer
EGX Autonomous Quant System

Analyzes 60min and 15min OHLCV data to extract microstructure signals, VWAP,
opening range, session bias, and intraday execution windows. Bridges the gap
between daily swing signals and actual intraday entry/exit timing.

Commands:
    session_analytics      — compute VWAP, opening range, session bias from 15min data
    intraday_coverage      — data coverage stats for 15min/60min tables
    execution_window       — best/worst execution windows across last 20 days
    opening_gap_analysis   — opening gap distribution, fill rates, persistence
    intraday_momentum      — hour-by-hour return decomposition from 60min data
    build_session_profiles — batch compute session analytics for multiple symbols
    build_full             — full intraday intelligence build across all symbols

Usage:
    python intraday_intelligence.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict

# ─── DB ──────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
TODAY = datetime.now(timezone.utc).strftime('%Y-%m-%d')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ─── EGX Session Constants ────────────────────────────────────────────────────

EGX_SESSION = {
    'open_hour': 10, 'open_min': 0,
    'close_hour': 14, 'close_min': 30,
    'lunch_start': 12, 'lunch_end': 12,
    'timezone_offset': 2,   # Cairo = UTC+2 (winter) or UTC+3 (summer)
    'trading_days': [0, 1, 2, 3, 6],   # Mon–Thu + Sun
}
OPENING_RANGE_MINUTES = 30  # first 30 min defines the opening range


# ─── DB Helpers ───────────────────────────────────────────────────────────────

def safe_query(conn, sql, params=()):
    """Execute a query; return list of dicts or [] on any error."""
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def safe_scalar(conn, sql, params=(), default=None):
    """Return first column of first row or default."""
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default


def table_exists(conn, name):
    count = safe_scalar(
        conn,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
        default=0,
    )
    return bool(count)


def ensure_intraday_analytics_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            vwap REAL,
            opening_range_high REAL,
            opening_range_low REAL,
            opening_gap_pct REAL,
            first_hour_direction TEXT,
            volume_profile_bins TEXT,
            session_bias TEXT,
            best_entry_window TEXT,
            volatility_percentile REAL,
            computed_at TEXT,
            UNIQUE(symbol, trade_date)
        )
    """)
    conn.commit()


def ensure_intraday_summary_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            n_symbols INTEGER,
            avg_session_bias TEXT,
            execution_windows_computed INTEGER,
            details TEXT,
            computed_at TEXT
        )
    """)
    conn.commit()


def require_intraday_tables(conn):
    """Raise a friendly error if ohlcv_15min / ohlcv_60min are missing."""
    missing = []
    for tbl in ('ohlcv_15min', 'ohlcv_60min'):
        if not table_exists(conn, tbl):
            missing.append(tbl)
    if missing:
        raise ValueError(
            json.dumps({
                'error': 'intraday_data_not_fetched',
                'missing_tables': missing,
                'hint': 'Run: npm run egx:intraday:fetch',
            })
        )


# ─── Computation Helpers ──────────────────────────────────────────────────────

def bar_time_to_datetime(bar_time_int):
    """Convert Unix timestamp int to UTC datetime."""
    return datetime.utcfromtimestamp(bar_time_int)


def bars_for_date(conn, table, symbol, trade_date_str):
    """Fetch all intraday bars for a symbol on a given date string (YYYY-MM-DD)."""
    dt = datetime.strptime(trade_date_str, '%Y-%m-%d')
    # We accept bars from midnight UTC to the next midnight. EGX is UTC+2,
    # so 10:00 Cairo = 08:00 UTC, 14:30 Cairo = 12:30 UTC.
    day_start = int(dt.replace(hour=0, minute=0, second=0).timestamp())
    day_end = int(dt.replace(hour=23, minute=59, second=59).timestamp())
    rows = safe_query(
        conn,
        f"SELECT * FROM {table} WHERE symbol=? AND bar_time>=? AND bar_time<=? ORDER BY bar_time",
        (symbol, day_start, day_end),
    )
    return rows


def compute_vwap(bars):
    """VWAP = sum(typical_price × volume) / sum(volume)."""
    tp_vol = 0.0
    total_vol = 0.0
    for b in bars:
        tp = (b['high'] + b['low'] + b['close']) / 3.0
        v = b.get('volume') or 0.0
        tp_vol += tp * v
        total_vol += v
    if total_vol == 0:
        return None
    return tp_vol / total_vol


def bar_minutes_offset(bar_time_int, open_hour=10, open_min=0, tz_offset=2):
    """Minutes elapsed since session open (Cairo local time)."""
    dt_utc = datetime.utcfromtimestamp(bar_time_int)
    local_hour = (dt_utc.hour + tz_offset) % 24
    local_min = dt_utc.minute
    return (local_hour - open_hour) * 60 + (local_min - open_min)


def opening_range_bars(bars, minutes=30, tz_offset=2):
    """Return bars within the first `minutes` of the session."""
    return [b for b in bars if 0 <= bar_minutes_offset(b['bar_time'], tz_offset=tz_offset) < minutes]


def volume_profile(bars, n_bins=5):
    """Bucket volume into n_bins price bins. Returns list of {price_level, volume_pct}."""
    if not bars:
        return []
    prices = [(b['high'] + b['low']) / 2.0 for b in bars]
    volumes = [b.get('volume') or 0.0 for b in bars]
    lo = min(b['low'] for b in bars)
    hi = max(b['high'] for b in bars)
    if hi <= lo:
        return []
    bin_size = (hi - lo) / n_bins
    bins = defaultdict(float)
    for i, b in enumerate(bars):
        mid = prices[i]
        idx = min(int((mid - lo) / bin_size), n_bins - 1)
        bins[idx] += volumes[i]
    total = sum(bins.values()) or 1.0
    result = []
    for idx in range(n_bins):
        price_level = round(lo + (idx + 0.5) * bin_size, 4)
        vol_pct = round(bins[idx] / total * 100, 2)
        result.append({'price_level': price_level, 'volume_pct': vol_pct})
    return result


def window_label(bar_time_int, duration_min=30, tz_offset=2):
    """Return 'HH:MM-HH:MM' label for a 30-min window starting at bar_time."""
    dt_utc = datetime.utcfromtimestamp(bar_time_int)
    local_hour = (dt_utc.hour + tz_offset) % 24
    local_min = dt_utc.minute
    end_min = local_min + duration_min
    end_hour = local_hour + end_min // 60
    end_min = end_min % 60
    return f'{local_hour:02d}:{local_min:02d}-{end_hour:02d}:{end_min:02d}'


def bar_atr(bars):
    """Average True Range over provided bars."""
    trs = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1]['close']
        h = bars[i]['high']
        l = bars[i]['low']
        trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
    if not trs:
        return 0.0
    return statistics.mean(trs)


def volatility_percentile(bars_15min_today, conn, symbol):
    """Rough percentile of today's session volatility vs historical ATRs."""
    today_atr = bar_atr(bars_15min_today)
    rows = safe_query(
        conn,
        "SELECT bar_time, high, low, close FROM ohlcv_15min WHERE symbol=? ORDER BY bar_time DESC LIMIT 1000",
        (symbol,),
    )
    if len(rows) < 20:
        return 50.0
    # Split into groups of ~16 bars (roughly one session)
    session_atrs = []
    chunk = []
    for r in rows:
        chunk.append(r)
        if len(chunk) == 16:
            session_atrs.append(bar_atr(chunk))
            chunk = []
    if not session_atrs:
        return 50.0
    below = sum(1 for x in session_atrs if x <= today_atr)
    return round(below / len(session_atrs) * 100, 1)


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_session_analytics(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    try:
        require_intraday_tables(conn)
        ensure_intraday_analytics_table(conn)

        # Resolve date
        trade_date = params.get('date')
        if not trade_date:
            row = safe_scalar(
                conn,
                "SELECT MAX(DATE(bar_time, 'unixepoch')) FROM ohlcv_15min WHERE symbol=?",
                (symbol,),
            )
            if not row:
                return {'error': 'no_data', 'symbol': symbol}
            trade_date = row

        # Check if already computed
        existing = safe_query(
            conn,
            "SELECT * FROM intraday_analytics WHERE symbol=? AND trade_date=?",
            (symbol, trade_date),
        )
        if existing:
            return {'status': 'already_computed', 'data': existing[0]}

        # Fetch 15min bars for the day
        bars = bars_for_date(conn, 'ohlcv_15min', symbol, trade_date)
        if not bars:
            return {'error': 'no_bars', 'symbol': symbol, 'trade_date': trade_date}

        # VWAP
        vwap = compute_vwap(bars)

        # Opening range (first 30 min)
        or_bars = opening_range_bars(bars, OPENING_RANGE_MINUTES)
        opening_range_high = max(b['high'] for b in or_bars) if or_bars else None
        opening_range_low = min(b['low'] for b in or_bars) if or_bars else None

        # Opening gap vs previous day close
        prev_date = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        # Try up to 5 calendar days back for prev trading close
        prev_close = None
        for delta in range(1, 8):
            prev_d = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=delta)).strftime('%Y-%m-%d')
            prev_bars = bars_for_date(conn, 'ohlcv_15min', symbol, prev_d)
            if prev_bars:
                prev_close = prev_bars[-1]['close']
                break
        first_bar_open = bars[0]['open'] if bars else None
        opening_gap_pct = None
        if prev_close and first_bar_open and prev_close != 0:
            opening_gap_pct = round((first_bar_open - prev_close) / prev_close * 100, 4)

        # First-hour direction (bar at +60min offset vs session open)
        first_hour_end_bars = [b for b in bars if bar_minutes_offset(b['bar_time']) >= 60]
        first_hour_direction = None
        if first_hour_end_bars and bars:
            fh_close = first_hour_end_bars[0]['close']
            session_open = bars[0]['open']
            if fh_close > session_open * 1.001:
                first_hour_direction = 'UP'
            elif fh_close < session_open * 0.999:
                first_hour_direction = 'DOWN'
            else:
                first_hour_direction = 'FLAT'

        # Volume profile
        vol_profile = volume_profile(bars, n_bins=5)

        # Session bias
        last_close = bars[-1]['close'] if bars else None
        session_bias = 'NEUTRAL'
        if vwap and last_close:
            diff_pct = (last_close - vwap) / vwap * 100
            if diff_pct > 0.3:
                session_bias = 'BULLISH'
            elif diff_pct < -0.3:
                session_bias = 'BEARISH'
            else:
                session_bias = 'NEUTRAL'

        # Best entry window: 30-min slot with highest volume, excluding first 30min
        post_open_bars = [b for b in bars if bar_minutes_offset(b['bar_time']) >= OPENING_RANGE_MINUTES]
        best_entry_window = None
        if post_open_bars:
            window_vols = defaultdict(float)
            window_start = {}
            for b in post_open_bars:
                offset = bar_minutes_offset(b['bar_time'])
                slot = (offset // 30) * 30  # round down to nearest 30min slot
                window_vols[slot] += b.get('volume') or 0.0
                if slot not in window_start:
                    window_start[slot] = b['bar_time']
            if window_vols:
                best_slot = max(window_vols, key=lambda s: window_vols[s])
                best_entry_window = window_label(window_start[best_slot], duration_min=30)

        # Volatility percentile
        vol_pct = volatility_percentile(bars, conn, symbol)

        # Save to DB
        conn.execute("""
            INSERT OR REPLACE INTO intraday_analytics
                (symbol, trade_date, vwap, opening_range_high, opening_range_low,
                 opening_gap_pct, first_hour_direction, volume_profile_bins,
                 session_bias, best_entry_window, volatility_percentile, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, trade_date,
            round(vwap, 4) if vwap else None,
            round(opening_range_high, 4) if opening_range_high else None,
            round(opening_range_low, 4) if opening_range_low else None,
            opening_gap_pct,
            first_hour_direction,
            json.dumps(vol_profile),
            session_bias,
            best_entry_window,
            vol_pct,
            NOW,
        ))
        conn.commit()

        return {
            'status': 'computed',
            'symbol': symbol,
            'trade_date': trade_date,
            'vwap': round(vwap, 4) if vwap else None,
            'opening_range_high': round(opening_range_high, 4) if opening_range_high else None,
            'opening_range_low': round(opening_range_low, 4) if opening_range_low else None,
            'opening_gap_pct': opening_gap_pct,
            'first_hour_direction': first_hour_direction,
            'volume_profile_bins': vol_profile,
            'session_bias': session_bias,
            'best_entry_window': best_entry_window,
            'volatility_percentile': vol_pct,
            'n_bars': len(bars),
        }
    finally:
        conn.close()


def cmd_intraday_coverage(params):
    conn = get_db()
    try:
        result = {}

        # 60min coverage
        if table_exists(conn, 'ohlcv_60min'):
            sym_60 = safe_scalar(conn, "SELECT COUNT(DISTINCT symbol) FROM ohlcv_60min", default=0)
            min_60 = safe_scalar(conn, "SELECT MIN(DATE(bar_time,'unixepoch')) FROM ohlcv_60min", default=None)
            max_60 = safe_scalar(conn, "SELECT MAX(DATE(bar_time,'unixepoch')) FROM ohlcv_60min", default=None)
            avg_60 = safe_scalar(conn, "SELECT CAST(COUNT(*) AS REAL)/COUNT(DISTINCT symbol) FROM ohlcv_60min", default=0)
            result['ohlcv_60min'] = {
                'symbols': sym_60,
                'date_range': [min_60, max_60],
                'avg_bars_per_symbol': round(avg_60, 1) if avg_60 else 0,
            }
        else:
            result['ohlcv_60min'] = {'error': 'table_missing'}

        # 15min coverage
        if table_exists(conn, 'ohlcv_15min'):
            sym_15 = safe_scalar(conn, "SELECT COUNT(DISTINCT symbol) FROM ohlcv_15min", default=0)
            min_15 = safe_scalar(conn, "SELECT MIN(DATE(bar_time,'unixepoch')) FROM ohlcv_15min", default=None)
            max_15 = safe_scalar(conn, "SELECT MAX(DATE(bar_time,'unixepoch')) FROM ohlcv_15min", default=None)
            avg_15 = safe_scalar(conn, "SELECT CAST(COUNT(*) AS REAL)/COUNT(DISTINCT symbol) FROM ohlcv_15min", default=0)
            result['ohlcv_15min'] = {
                'symbols': sym_15,
                'date_range': [min_15, max_15],
                'avg_bars_per_symbol': round(avg_15, 1) if avg_15 else 0,
            }
        else:
            result['ohlcv_15min'] = {'error': 'table_missing'}

        # intraday_analytics coverage
        if table_exists(conn, 'intraday_analytics'):
            ana_syms = safe_scalar(conn, "SELECT COUNT(DISTINCT symbol) FROM intraday_analytics", default=0)
            ana_rows = safe_scalar(conn, "SELECT COUNT(*) FROM intraday_analytics", default=0)
            ana_latest = safe_scalar(conn, "SELECT MAX(trade_date) FROM intraday_analytics", default=None)
            result['intraday_analytics'] = {
                'symbols_with_analytics': ana_syms,
                'total_rows': ana_rows,
                'latest_date': ana_latest,
            }
        else:
            result['intraday_analytics'] = {'status': 'not_yet_computed'}

        return result
    finally:
        conn.close()


def cmd_execution_window(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    try:
        require_intraday_tables(conn)
        ensure_intraday_analytics_table(conn)

        rows = safe_query(
            conn,
            """SELECT trade_date, best_entry_window, volume_profile_bins, opening_range_high,
                      opening_range_low, vwap
               FROM intraday_analytics WHERE symbol=? ORDER BY trade_date DESC LIMIT 20""",
            (symbol,),
        )
        if not rows:
            return {'error': 'no_analytics', 'symbol': symbol,
                    'hint': 'Run session_analytics first'}

        # Tally window frequencies and average volume pct
        window_counts = defaultdict(int)
        window_vol_sum = defaultdict(float)

        for r in rows:
            w = r.get('best_entry_window')
            if w:
                window_counts[w] += 1
            # Parse volume profile for distribution insight
            try:
                bins = json.loads(r.get('volume_profile_bins') or '[]')
                for b in bins:
                    window_counts['_profile'] = window_counts.get('_profile', 0)  # keep as is
            except Exception:
                pass

        # Find most consistent window
        best_windows = sorted(window_counts.items(), key=lambda x: -x[1])
        optimal_window = best_windows[0][0] if best_windows else None
        frequency = best_windows[0][1] if best_windows else 0
        confidence = round(frequency / len(rows) * 100, 1)

        # Average volatility
        vol_pcts = []
        for r in rows:
            if r.get('volatility_percentile') is not None:
                vol_pcts.append(r['volatility_percentile'])
        avg_vol_pct = round(statistics.mean(vol_pcts), 1) if vol_pcts else None

        # Range analysis for opening range
        or_ranges = []
        for r in rows:
            if r.get('opening_range_high') and r.get('opening_range_low'):
                or_ranges.append(r['opening_range_high'] - r['opening_range_low'])
        avg_or_range = round(statistics.mean(or_ranges), 4) if or_ranges else None

        # Avoid windows: first 15 min is typically volatile on EGX
        avoid_windows = ['10:00-10:15', '10:00-10:30']

        return {
            'symbol': symbol,
            'lookback_sessions': len(rows),
            'optimal_entry_window': optimal_window,
            'window_frequency': frequency,
            'confidence_pct': confidence,
            'avg_volatility_percentile': avg_vol_pct,
            'avg_opening_range': avg_or_range,
            'avoid_windows': avoid_windows,
            'all_windows_ranked': [
                {'window': w, 'count': c, 'frequency_pct': round(c / len(rows) * 100, 1)}
                for w, c in best_windows if not w.startswith('_')
            ],
        }
    finally:
        conn.close()


def cmd_opening_gap_analysis(params):
    symbol = params.get('symbol', '').upper()
    lookback_days = int(params.get('lookback_days', 30))
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    try:
        require_intraday_tables(conn)

        # Get all distinct dates for the symbol in the lookback window
        cutoff_ts = int((datetime.utcnow() - timedelta(days=lookback_days)).timestamp())
        dates_rows = safe_query(
            conn,
            """SELECT DISTINCT DATE(bar_time,'unixepoch') AS d
               FROM ohlcv_15min WHERE symbol=? AND bar_time>=?
               ORDER BY d""",
            (symbol, cutoff_ts),
        )
        dates = [r['d'] for r in dates_rows]

        if len(dates) < 3:
            return {'error': 'insufficient_data', 'symbol': symbol, 'dates_found': len(dates)}

        gap_records = []
        for i in range(1, len(dates)):
            prev_d = dates[i - 1]
            curr_d = dates[i]
            prev_bars = bars_for_date(conn, 'ohlcv_15min', symbol, prev_d)
            curr_bars = bars_for_date(conn, 'ohlcv_15min', symbol, curr_d)
            if not prev_bars or not curr_bars:
                continue
            prev_close = prev_bars[-1]['close']
            curr_open = curr_bars[0]['open']
            curr_close = curr_bars[-1]['close']
            curr_high = max(b['high'] for b in curr_bars)
            curr_low = min(b['low'] for b in curr_bars)
            if prev_close == 0:
                continue
            gap_pct = (curr_open - prev_close) / prev_close * 100

            # Gap fill: did price return to prev_close during the day?
            if gap_pct >= 0.5:   # gap up
                filled = curr_low <= prev_close
            elif gap_pct <= -0.5:  # gap down
                filled = curr_high >= prev_close
            else:
                filled = None

            # Direction persistence: does close continue in gap direction?
            if gap_pct > 0.1:
                persisted = curr_close > curr_open
            elif gap_pct < -0.1:
                persisted = curr_close < curr_open
            else:
                persisted = None

            gap_records.append({
                'date': curr_d,
                'gap_pct': round(gap_pct, 4),
                'gap_type': 'UP' if gap_pct >= 0.5 else ('DOWN' if gap_pct <= -0.5 else 'FLAT'),
                'filled': filled,
                'persisted': persisted,
                'prev_close': prev_close,
                'open': curr_open,
                'close': curr_close,
            })

        if not gap_records:
            return {'error': 'no_gap_data', 'symbol': symbol}

        gap_ups = [g for g in gap_records if g['gap_type'] == 'UP']
        gap_downs = [g for g in gap_records if g['gap_type'] == 'DOWN']
        gap_flats = [g for g in gap_records if g['gap_type'] == 'FLAT']

        significant = [g for g in gap_records if abs(g['gap_pct']) >= 0.5]
        fill_rate = None
        if significant:
            filled_count = sum(1 for g in significant if g['filled'])
            fill_rate = round(filled_count / len(significant) * 100, 1)

        persist_rate = None
        directional = [g for g in gap_records if g['persisted'] is not None]
        if directional:
            persist_count = sum(1 for g in directional if g['persisted'])
            persist_rate = round(persist_count / len(directional) * 100, 1)

        all_gaps = [g['gap_pct'] for g in gap_records]

        return {
            'symbol': symbol,
            'lookback_days': lookback_days,
            'n_sessions': len(gap_records),
            'gap_distribution': {
                'up_gaps': len(gap_ups),
                'down_gaps': len(gap_downs),
                'flat': len(gap_flats),
            },
            'avg_gap_pct': round(statistics.mean(all_gaps), 4),
            'max_gap_pct': round(max(all_gaps), 4),
            'min_gap_pct': round(min(all_gaps), 4),
            'gap_fill_rate_pct': fill_rate,
            'direction_persistence_pct': persist_rate,
            'significant_gap_threshold_pct': 0.5,
            'recent_gaps': gap_records[-10:],
        }
    finally:
        conn.close()


def cmd_intraday_momentum(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    try:
        require_intraday_tables(conn)

        # Get last 20 trading days of 60min data
        rows = safe_query(
            conn,
            """SELECT bar_time, open, high, low, close, volume
               FROM ohlcv_60min WHERE symbol=?
               ORDER BY bar_time DESC LIMIT 200""",
            (symbol,),
        )
        if not rows:
            return {'error': 'no_60min_data', 'symbol': symbol}

        rows = list(reversed(rows))

        # Group by date
        by_date = defaultdict(list)
        for r in rows:
            d = datetime.utcfromtimestamp(r['bar_time']).strftime('%Y-%m-%d')
            by_date[d].append(r)

        if not by_date:
            return {'error': 'no_data'}

        # Hour-by-hour return decomposition
        hour_returns = defaultdict(list)  # hour_label -> [ret%]
        for d, day_bars in by_date.items():
            day_bars = sorted(day_bars, key=lambda x: x['bar_time'])
            for i, b in enumerate(day_bars):
                dt_utc = datetime.utcfromtimestamp(b['bar_time'])
                local_hour = (dt_utc.hour + EGX_SESSION['timezone_offset']) % 24
                hour_label = f'{local_hour:02d}:00'
                if i == 0:
                    ret = (b['close'] - b['open']) / b['open'] * 100 if b['open'] else 0
                else:
                    prev_close = day_bars[i - 1]['close']
                    ret = (b['close'] - prev_close) / prev_close * 100 if prev_close else 0
                hour_returns[hour_label].append(ret)

        hour_stats = {}
        for hour_label, rets in sorted(hour_returns.items()):
            hour_stats[hour_label] = {
                'avg_return_pct': round(statistics.mean(rets), 4),
                'std': round(statistics.stdev(rets), 4) if len(rets) > 1 else 0.0,
                'positive_pct': round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                'n_obs': len(rets),
            }

        # Strongest / weakest hour
        sorted_hours = sorted(hour_stats.items(), key=lambda x: x[1]['avg_return_pct'])
        weakest_hour = sorted_hours[0][0] if sorted_hours else None
        strongest_hour = sorted_hours[-1][0] if sorted_hours else None

        # Today vs average
        today_str = TODAY
        today_bars = by_date.get(today_str, [])
        today_pattern = []
        if today_bars:
            today_bars = sorted(today_bars, key=lambda x: x['bar_time'])
            for i, b in enumerate(today_bars):
                dt_utc = datetime.utcfromtimestamp(b['bar_time'])
                local_hour = (dt_utc.hour + EGX_SESSION['timezone_offset']) % 24
                hour_label = f'{local_hour:02d}:00'
                if i == 0:
                    ret = (b['close'] - b['open']) / b['open'] * 100 if b['open'] else 0
                else:
                    prev_close = today_bars[i - 1]['close']
                    ret = (b['close'] - prev_close) / prev_close * 100 if prev_close else 0
                avg = hour_stats.get(hour_label, {}).get('avg_return_pct', 0)
                today_pattern.append({
                    'hour': hour_label,
                    'return_pct': round(ret, 4),
                    'vs_avg': round(ret - avg, 4),
                })

        return {
            'symbol': symbol,
            'n_sessions': len(by_date),
            'strongest_hour': strongest_hour,
            'weakest_hour': weakest_hour,
            'hour_by_hour': hour_stats,
            'today_vs_avg': today_pattern,
        }
    finally:
        conn.close()


def cmd_build_session_profiles(params):
    symbols_param = params.get('symbols')
    date_param = params.get('date')

    conn = get_db()
    try:
        require_intraday_tables(conn)

        if symbols_param:
            symbols = [s.upper() for s in symbols_param]
        else:
            # All symbols with recent 15min data
            rows = safe_query(conn, "SELECT DISTINCT symbol FROM ohlcv_15min")
            symbols = [r['symbol'] for r in rows]

        n_computed = 0
        n_skipped = 0
        errors = []

        for sym in symbols:
            try:
                sub_params = {'symbol': sym}
                if date_param:
                    sub_params['date'] = date_param
                result = cmd_session_analytics(sub_params)
                if result.get('status') in ('computed',):
                    n_computed += 1
                elif result.get('status') == 'already_computed':
                    n_skipped += 1
                elif result.get('error'):
                    errors.append({'symbol': sym, 'error': result['error']})
            except Exception as e:
                errors.append({'symbol': sym, 'error': str(e)})

        return {
            'n_computed': n_computed,
            'n_skipped': n_skipped,
            'n_errors': len(errors),
            'errors': errors[:20],
        }
    finally:
        conn.close()


def cmd_build_full(params):
    conn = get_db()
    try:
        require_intraday_tables(conn)
        ensure_intraday_summary_table(conn)

        # Step 1: build session profiles for all symbols
        profile_result = cmd_build_session_profiles({})
        n_symbols = profile_result.get('n_computed', 0) + profile_result.get('n_skipped', 0)

        # Step 2: aggregate session bias stats
        ensure_intraday_analytics_table(conn)
        bias_rows = safe_query(
            conn,
            """SELECT session_bias, COUNT(*) AS cnt FROM intraday_analytics
               WHERE trade_date >= DATE('now', '-30 days')
               GROUP BY session_bias""",
        )
        bias_counts = {r['session_bias']: r['cnt'] for r in bias_rows}
        total_bias = sum(bias_counts.values()) or 1
        dominant_bias = max(bias_counts, key=lambda k: bias_counts[k]) if bias_counts else 'NEUTRAL'

        # Step 3: compute execution windows for all symbols
        syms_with_ana = safe_query(
            conn, "SELECT DISTINCT symbol FROM intraday_analytics"
        )
        execution_windows_computed = 0
        ew_details = {}
        for r in syms_with_ana:
            sym = r['symbol']
            try:
                ew = cmd_execution_window({'symbol': sym})
                if ew.get('optimal_entry_window'):
                    execution_windows_computed += 1
                    ew_details[sym] = ew.get('optimal_entry_window')
            except Exception:
                pass

        # Save summary
        details = {
            'bias_counts': bias_counts,
            'execution_windows': ew_details,
            'build_errors': profile_result.get('errors', []),
        }
        conn.execute("""
            INSERT INTO intraday_summary
                (snapshot_date, n_symbols, avg_session_bias,
                 execution_windows_computed, details, computed_at)
            VALUES (?,?,?,?,?,?)
        """, (
            TODAY, n_symbols, dominant_bias,
            execution_windows_computed, json.dumps(details), NOW,
        ))
        conn.commit()

        return {
            'status': 'built',
            'n_symbols': n_symbols,
            'avg_session_bias': dominant_bias,
            'bias_breakdown': bias_counts,
            'execution_windows_computed': execution_windows_computed,
            'n_computed': profile_result.get('n_computed', 0),
            'n_skipped': profile_result.get('n_skipped', 0),
            'n_errors': profile_result.get('n_errors', 0),
        }
    finally:
        conn.close()


# ─── Dispatch ─────────────────────────────────────────────────────────────────

COMMANDS = {
    'session_analytics':      cmd_session_analytics,
    'intraday_coverage':      cmd_intraday_coverage,
    'execution_window':       cmd_execution_window,
    'opening_gap_analysis':   cmd_opening_gap_analysis,
    'intraday_momentum':      cmd_intraday_momentum,
    'build_session_profiles': cmd_build_session_profiles,
    'build_full':             cmd_build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'usage: python intraday_intelligence.py <command> \'<json_params>\'',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except Exception as e:
        print(json.dumps({'error': f'invalid params JSON: {e}'}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({
            'error': f'unknown command: {cmd}',
            'available': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
    except ValueError as ve:
        # raised by require_intraday_tables with a pre-built JSON string
        try:
            result = json.loads(str(ve))
        except Exception:
            result = {'error': str(ve)}
    except Exception as e:
        result = {'error': str(e)}

    print(json.dumps(result))


if __name__ == '__main__':
    main()
