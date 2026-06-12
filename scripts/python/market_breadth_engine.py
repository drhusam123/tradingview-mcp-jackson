"""
Phase 56 — Market Breadth Engine
EGX Autonomous Quant System

Computes market-wide breadth metrics from historical OHLCV data stored in SQLite:
  - Advance/Decline statistics and cumulative A/D line
  - % of stocks above MA20 / MA50 / MA200
  - 52-week new highs and new lows
  - McClellan Oscillator and Summation Index
  - Sector-level breadth breakdown
  - Composite breadth score (0-100) and regime signal
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import datetime
import collections

from db_ohlcv import OHLCV_TABLE

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# EGX trades Sun–Thu  (weekday(): Sun=6, Mon=0, Tue=1, Wed=2, Thu=3)
EGX_TRADING_DAYS = {0, 1, 2, 3, 6}

# Breadth score signal thresholds
SIGNAL_THRESHOLDS = [
    (70, 'BREADTH_BULL'),
    (55, 'BREADTH_LEAN_BULL'),
    (45, 'BREADTH_NEUTRAL'),
    (30, 'BREADTH_LEAN_BEAR'),
    (0,  'BREADTH_BEAR'),
]

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_breadth_daily (
            date                   TEXT PRIMARY KEY,
            n_advances             INTEGER,
            n_declines             INTEGER,
            n_unchanged            INTEGER,
            ad_ratio               REAL,
            ad_line_value          REAL,
            pct_above_ma20         REAL,
            pct_above_ma50         REAL,
            pct_above_ma200        REAL,
            n_new_highs_52w        INTEGER,
            n_new_lows_52w         INTEGER,
            hl_ratio               REAL,
            mcclellan_oscillator   REAL,
            mcclellan_summation    REAL,
            breadth_score          REAL,
            signal                 TEXT,
            computed_at            TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_breadth_sectors (
            date          TEXT,
            sector        TEXT,
            n_advances    INTEGER,
            n_declines    INTEGER,
            breadth_pct   REAL,
            PRIMARY KEY (date, sector)
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Pure computation helpers
# ---------------------------------------------------------------------------

def _ts_to_date(ts: int) -> str:
    """Convert unix timestamp (int seconds UTC) to 'YYYY-MM-DD'."""
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')


def _date_to_ts(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' to unix timestamp at midnight UTC."""
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


def _ema(values: list, span: int) -> list:
    """
    Compute EMA series using exponential smoothing.
    alpha = 2 / (span + 1)
    Returns a list of the same length; first value seeds the EMA.
    """
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _safe_div(a, b, default=0.0):
    return a / b if b else default


def _score_to_signal(score: float) -> str:
    for threshold, label in SIGNAL_THRESHOLDS:
        if score >= threshold:
            return label
    return 'BREADTH_BEAR'


def _compute_breadth_score(ad_ratio, pct_above_ma50, hl_ratio, mcclellan_osc,
                           pct_rsi_overbought: float = 0.0) -> float:
    """
    Composite breadth score (0-100):
      35%  A/D ratio  (0-1 → 0-100)
      25%  % above MA50 (already 0-100)
      20%  H/L ratio  (0-1 → 0-100, capped)
      10%  McClellan > 0 → 100 else 0
      10%  RSI overbought penalty (inverted: 0% overbought → full 10pts, 100% → 0pts)
           + extra penalty when >60% overbought (crowded market = mean-reversion risk)
    """
    ad_component   = min(max(ad_ratio, 0.0), 1.0) * 100.0
    ma50_component = min(max(pct_above_ma50, 0.0), 100.0)
    hl_component   = min(max(hl_ratio, 0.0), 1.0) * 100.0
    mcl_component  = 100.0 if mcclellan_osc > 0 else 0.0

    # RSI overbought component: inverted scale + crowding penalty
    # 0% overbought → 10pts | 40% → 6pts | 60% → 0pts | 80%+ → -8pts
    pct_ob = min(max(pct_rsi_overbought, 0.0), 100.0)
    if pct_ob >= 80.0:
        rsi_component = -8.0   # extreme crowding — strong mean-reversion risk
    elif pct_ob >= 60.0:
        rsi_component = 0.0    # overbought majority — no bonus
    else:
        rsi_component = 10.0 * (1.0 - pct_ob / 60.0)  # linear: 10 → 0 as pct_ob goes 0→60%

    score = (0.35 * ad_component +
             0.25 * ma50_component +
             0.20 * hl_component +
             0.10 * mcl_component +
             rsi_component)
    return round(max(0.0, min(100.0, score)), 2)


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------

def _all_trading_dates(conn) -> list:
    """Return sorted list of unique 'YYYY-MM-DD' dates present in ohlcv_history."""
    rows = conn.execute(
        f"SELECT DISTINCT bar_time FROM {OHLCV_TABLE} ORDER BY bar_time"
    ).fetchall()
    seen = set()
    dates = []
    for r in rows:
        d = _ts_to_date(r[0])
        if d not in seen:
            seen.add(d)
            dates.append(d)
    return sorted(dates)


def _latest_date(conn) -> str:
    row = conn.execute(f"SELECT MAX(bar_time) FROM {OHLCV_TABLE}").fetchone()
    if not row or row[0] is None:
        return None
    return _ts_to_date(row[0])


def _get_closes_on_date(conn, date_str: str) -> dict:
    """Return {symbol: close} for all symbols on the given date."""
    day_start = _date_to_ts(date_str)
    day_end   = day_start + 86399
    rows = conn.execute(
        f"SELECT symbol, close FROM {OHLCV_TABLE} WHERE bar_time BETWEEN ? AND ?",
        (day_start, day_end)
    ).fetchall()
    return {r['symbol']: r['close'] for r in rows}


def _get_n_closes_before(conn, symbol: str, before_ts: int, n: int) -> list:
    """
    Return up to N closing prices for `symbol` with bar_time < before_ts,
    ordered oldest-first.
    """
    rows = conn.execute(
        """SELECT close FROM ohlcv_history
           WHERE symbol = ? AND bar_time < ?
           ORDER BY bar_time DESC LIMIT ?""",
        (symbol, before_ts, n)
    ).fetchall()
    return [r['close'] for r in reversed(rows)]


def _get_symbol_sector_map(conn) -> dict:
    """Return {symbol: sector} from stock_universe."""
    rows = conn.execute(
        "SELECT symbol, sector FROM stock_universe WHERE status = 'active'"
    ).fetchall()
    return {r['symbol']: (r['sector'] or 'Unknown') for r in rows}


def _get_ad_history(conn, n_days: int) -> list:
    """Return last n_days rows of (date, ad_net) from market_breadth_daily, oldest-first."""
    rows = conn.execute(
        """SELECT date, n_advances, n_declines
           FROM market_breadth_daily
           ORDER BY date DESC LIMIT ?""",
        (n_days,)
    ).fetchall()
    return [(r['date'], r['n_advances'] - r['n_declines']) for r in reversed(rows)]


def _get_ad_line_last(conn) -> float:
    """Return the last cumulative A/D line value stored in market_breadth_daily."""
    row = conn.execute(
        "SELECT ad_line_value FROM market_breadth_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return row['ad_line_value'] if row else 0.0


def _dates_range(conn, start_date: str, end_date: str) -> list:
    """Return sorted trading dates within [start_date, end_date]."""
    all_dates = _all_trading_dates(conn)
    return [d for d in all_dates if start_date <= d <= end_date]


# ---------------------------------------------------------------------------
# Command: compute_breadth
# ---------------------------------------------------------------------------

def compute_breadth(params: dict) -> dict:
    """Compute full breadth metrics for a single date and persist to DB."""
    conn = get_db()
    ensure_tables(conn)

    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data found in database'}

        # --- Advance / Decline --------------------------------------------------
        all_dates = _all_trading_dates(conn)
        if date_str not in all_dates:
            return {'success': False, 'error': f'No data for date {date_str}'}

        idx = all_dates.index(date_str)
        if idx == 0:
            return {
                'success': False,
                'error': f'No previous trading day before {date_str} to compute A/D'
            }

        prev_date = all_dates[idx - 1]
        today_closes = _get_closes_on_date(conn, date_str)
        prev_closes  = _get_closes_on_date(conn, prev_date)

        common_symbols = sorted(set(today_closes) & set(prev_closes))
        if not common_symbols:
            return {
                'success': True,
                'warning': f'No symbols with data on both {prev_date} and {date_str}',
                'date': date_str
            }

        n_advances = n_declines = n_unchanged = 0
        for sym in common_symbols:
            delta = today_closes[sym] - prev_closes[sym]
            if delta > 0:
                n_advances += 1
            elif delta < 0:
                n_declines += 1
            else:
                n_unchanged += 1

        ad_ratio = _safe_div(n_advances, n_advances + n_declines, 0.5)

        # Cumulative A/D line
        prev_ad_line = _get_ad_line_last(conn)
        ad_line_value = round(prev_ad_line + (n_advances - n_declines), 2)

        # --- % above MAs -------------------------------------------------------
        date_ts = _date_to_ts(date_str)
        n_above_ma20 = n_above_ma50 = n_above_ma200 = n_ma_checked = 0

        for sym in common_symbols:
            close_today = today_closes[sym]
            closes_200  = _get_n_closes_before(conn, sym, date_ts, 200)
            closes_200.append(close_today)   # include today

            n_ma_checked += 1

            if len(closes_200) >= 20:
                ma20 = statistics.mean(closes_200[-20:])
                if close_today >= ma20:
                    n_above_ma20 += 1

            if len(closes_200) >= 50:
                ma50 = statistics.mean(closes_200[-50:])
                if close_today >= ma50:
                    n_above_ma50 += 1

            if len(closes_200) >= 200:
                ma200 = statistics.mean(closes_200[-200:])
                if close_today >= ma200:
                    n_above_ma200 += 1

        pct_above_ma20  = round(_safe_div(n_above_ma20,  n_ma_checked) * 100, 2)
        pct_above_ma50  = round(_safe_div(n_above_ma50,  n_ma_checked) * 100, 2)
        pct_above_ma200 = round(_safe_div(n_above_ma200, n_ma_checked) * 100, 2)

        # --- 52-week highs/lows ------------------------------------------------
        n_new_highs = n_new_lows = 0
        sym_highs = []
        sym_lows  = []

        for sym in common_symbols:
            close_today = today_closes[sym]
            past_252    = _get_n_closes_before(conn, sym, date_ts, 252)
            if not past_252:
                continue
            if close_today >= max(past_252):
                n_new_highs += 1
                sym_highs.append(sym)
            if close_today <= min(past_252):
                n_new_lows += 1
                sym_lows.append(sym)

        hl_ratio = round(_safe_div(n_new_highs, n_new_highs + n_new_lows, 0.5), 4)

        # --- McClellan Oscillator & Summation ----------------------------------
        # We need the last 50 AD_net values to build EMA19 and EMA39.
        # First, collect stored AD_net history from DB.
        stored_ad = _get_ad_history(conn, 49)  # up to 49 previous days
        # Append today
        today_ad_net = n_advances - n_declines
        ad_series = [net for _, net in stored_ad] + [today_ad_net]

        ema19_series = _ema(ad_series, 19)
        ema39_series = _ema(ad_series, 39)

        mcclellan_osc = round(ema19_series[-1] - ema39_series[-1], 4)

        # Build oscillator history for summation (last 30 osc values)
        osc_history = []
        if len(ad_series) >= 2:
            e19 = _ema(ad_series[:-1], 19)
            e39 = _ema(ad_series[:-1], 39)
            # Reconstruct mini oscillator list from stored DB rows
            n_hist = min(29, len(stored_ad))
            ad_chunk = [net for _, net in stored_ad[-n_hist:]]
            e19c = _ema(ad_chunk, 19) if ad_chunk else []
            e39c = _ema(ad_chunk, 39) if ad_chunk else []
            osc_history = [
                e19c[i] - e39c[i] for i in range(len(e19c))
            ]
        osc_history.append(mcclellan_osc)
        mcclellan_summation = round(sum(osc_history[-30:]), 4)

        # --- RSI overbought % from indicators_cache ----------------------------
        pct_rsi_overbought = 0.0
        try:
            # Count symbols with latest RSI14 > 70 (overbought) for this date
            rsi_rows = conn.execute("""
                SELECT symbol, rsi14 FROM indicators_cache
                WHERE bar_date <= ? AND bar_date >= date(?, '-3 days')
                GROUP BY symbol HAVING bar_date = MAX(bar_date)
            """, (date_str, date_str)).fetchall()
            if rsi_rows:
                n_ob = sum(1 for r in rsi_rows if (r['rsi14'] or 0) > 70)
                pct_rsi_overbought = round(n_ob / len(rsi_rows) * 100, 1)
        except Exception:
            pct_rsi_overbought = 0.0

        # --- Breadth score & signal --------------------------------------------
        breadth_score = _compute_breadth_score(
            ad_ratio, pct_above_ma50, hl_ratio, mcclellan_osc, pct_rsi_overbought
        )
        signal = _score_to_signal(breadth_score)

        computed_at = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        # --- Persist -----------------------------------------------------------
        conn.execute("""
            INSERT OR REPLACE INTO market_breadth_daily (
                date, n_advances, n_declines, n_unchanged,
                ad_ratio, ad_line_value,
                pct_above_ma20, pct_above_ma50, pct_above_ma200,
                n_new_highs_52w, n_new_lows_52w, hl_ratio,
                mcclellan_oscillator, mcclellan_summation,
                breadth_score, signal, computed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date_str, n_advances, n_declines, n_unchanged,
            round(ad_ratio, 4), ad_line_value,
            pct_above_ma20, pct_above_ma50, pct_above_ma200,
            n_new_highs, n_new_lows, hl_ratio,
            mcclellan_osc, mcclellan_summation,
            breadth_score, signal, computed_at
        ))
        conn.commit()

        return {
            'success': True,
            'date': date_str,
            'n_advances': n_advances,
            'n_declines': n_declines,
            'n_unchanged': n_unchanged,
            'n_symbols': len(common_symbols),
            'ad_ratio': round(ad_ratio, 4),
            'ad_line_value': ad_line_value,
            'pct_above_ma20': pct_above_ma20,
            'pct_above_ma50': pct_above_ma50,
            'pct_above_ma200': pct_above_ma200,
            'n_new_highs_52w': n_new_highs,
            'n_new_lows_52w': n_new_lows,
            'hl_ratio': hl_ratio,
            'mcclellan_oscillator': mcclellan_osc,
            'mcclellan_summation': mcclellan_summation,
            'breadth_score': breadth_score,
            'signal': signal,
            'computed_at': computed_at,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: advance_decline
# ---------------------------------------------------------------------------

def advance_decline(params: dict) -> dict:
    start_date = params.get('start_date', '2020-01-01')
    end_date   = params.get('end_date')

    conn = get_db()
    ensure_tables(conn)

    try:
        if not end_date:
            end_date = _latest_date(conn) or datetime.date.today().strftime('%Y-%m-%d')

        # Check which dates are already in DB
        stored = conn.execute(
            """SELECT date, n_advances, n_declines, ad_ratio, ad_line_value
               FROM market_breadth_daily
               WHERE date BETWEEN ? AND ?
               ORDER BY date""",
            (start_date, end_date)
        ).fetchall()
        stored_map = {r['date']: r for r in stored}

        # Fill in missing dates by computing on the fly
        trading_dates = _dates_range(conn, start_date, end_date)
        result_rows = []

        for d in trading_dates:
            if d in stored_map:
                r = stored_map[d]
                result_rows.append({
                    'date':          r['date'],
                    'n_advances':    r['n_advances'],
                    'n_declines':    r['n_declines'],
                    'ad_ratio':      r['ad_ratio'],
                    'ad_line_value': r['ad_line_value'],
                })
            else:
                # Compute for this date (lightweight — only A/D, no MA/H/L)
                res = compute_breadth({'date': d})
                if res.get('success') and 'ad_ratio' in res:
                    result_rows.append({
                        'date':          res['date'],
                        'n_advances':    res['n_advances'],
                        'n_declines':    res['n_declines'],
                        'ad_ratio':      res['ad_ratio'],
                        'ad_line_value': res['ad_line_value'],
                    })

        return {
            'success': True,
            'start_date': start_date,
            'end_date': end_date,
            'n_days': len(result_rows),
            'data': result_rows,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: ma_breadth
# ---------------------------------------------------------------------------

def ma_breadth(params: dict) -> dict:
    conn = get_db()
    ensure_tables(conn)

    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data in database'}

        # If already computed, read from DB
        row = conn.execute(
            "SELECT * FROM market_breadth_daily WHERE date = ?", (date_str,)
        ).fetchone()

        if row and row['pct_above_ma50'] is not None:
            return {
                'success': True,
                'date':              date_str,
                'n_above_ma20':      None,   # stored as pct only
                'pct_above_ma20':    row['pct_above_ma20'],
                'n_above_ma50':      None,
                'pct_above_ma50':    row['pct_above_ma50'],
                'n_above_ma200':     None,
                'pct_above_ma200':   row['pct_above_ma200'],
                'n_symbols_checked': None,
                'source': 'cache',
            }

        # Otherwise compute fresh
        date_ts = _date_to_ts(date_str)
        closes_today = _get_closes_on_date(conn, date_str)
        if not closes_today:
            return {'success': False, 'error': f'No data for {date_str}'}

        n_above_ma20 = n_above_ma50 = n_above_ma200 = n_checked = 0

        for sym, close_today in closes_today.items():
            closes_200 = _get_n_closes_before(conn, sym, date_ts, 200)
            closes_200.append(close_today)
            n_checked += 1

            if len(closes_200) >= 20:
                if close_today >= statistics.mean(closes_200[-20:]):
                    n_above_ma20 += 1
            if len(closes_200) >= 50:
                if close_today >= statistics.mean(closes_200[-50:]):
                    n_above_ma50 += 1
            if len(closes_200) >= 200:
                if close_today >= statistics.mean(closes_200[-200:]):
                    n_above_ma200 += 1

        return {
            'success': True,
            'date':              date_str,
            'n_above_ma20':      n_above_ma20,
            'pct_above_ma20':    round(_safe_div(n_above_ma20,  n_checked) * 100, 2),
            'n_above_ma50':      n_above_ma50,
            'pct_above_ma50':    round(_safe_div(n_above_ma50,  n_checked) * 100, 2),
            'n_above_ma200':     n_above_ma200,
            'pct_above_ma200':   round(_safe_div(n_above_ma200, n_checked) * 100, 2),
            'n_symbols_checked': n_checked,
            'source': 'computed',
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: new_highs_lows
# ---------------------------------------------------------------------------

def new_highs_lows(params: dict) -> dict:
    conn = get_db()
    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data in database'}

        # Return from cache if available
        row = conn.execute(
            "SELECT n_new_highs_52w, n_new_lows_52w, hl_ratio FROM market_breadth_daily WHERE date = ?",
            (date_str,)
        ).fetchone()

        closes_today = _get_closes_on_date(conn, date_str)
        if not closes_today:
            return {'success': False, 'error': f'No data for {date_str}'}

        date_ts = _date_to_ts(date_str)
        n_new_highs = n_new_lows = 0
        sym_highs = []
        sym_lows  = []

        for sym, close_today in closes_today.items():
            past_252 = _get_n_closes_before(conn, sym, date_ts, 252)
            if not past_252:
                continue
            if close_today >= max(past_252):
                n_new_highs += 1
                sym_highs.append(sym)
            if close_today <= min(past_252):
                n_new_lows += 1
                sym_lows.append(sym)

        hl_ratio = round(_safe_div(n_new_highs, n_new_highs + n_new_lows, 0.5), 4)

        return {
            'success': True,
            'date': date_str,
            'n_new_highs': n_new_highs,
            'n_new_lows': n_new_lows,
            'hl_ratio': hl_ratio,
            'symbols_highs': sorted(sym_highs),
            'symbols_lows':  sorted(sym_lows),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: mcclellan
# ---------------------------------------------------------------------------

def mcclellan(params: dict) -> dict:
    conn = get_db()
    ensure_tables(conn)

    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data in database'}

        # Pull from cache if available
        row = conn.execute(
            "SELECT mcclellan_oscillator, mcclellan_summation FROM market_breadth_daily WHERE date = ?",
            (date_str,)
        ).fetchone()

        if row and row['mcclellan_oscillator'] is not None:
            osc  = row['mcclellan_oscillator']
            summ = row['mcclellan_summation']
        else:
            # Compute fresh: need full breadth for this date
            res = compute_breadth({'date': date_str})
            if not res.get('success'):
                return res
            osc  = res['mcclellan_oscillator']
            summ = res['mcclellan_summation']

        if osc > 100:
            mcl_signal = 'OVERBOUGHT'
        elif osc < -100:
            mcl_signal = 'OVERSOLD'
        else:
            mcl_signal = 'NEUTRAL'

        return {
            'success': True,
            'date': date_str,
            'mcclellan_oscillator': osc,
            'mcclellan_summation': summ,
            'signal': mcl_signal,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: sector_breadth
# ---------------------------------------------------------------------------

def sector_breadth(params: dict) -> dict:
    conn = get_db()
    ensure_tables(conn)

    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data in database'}

        all_dates = _all_trading_dates(conn)
        if date_str not in all_dates:
            return {'success': False, 'error': f'No data for date {date_str}'}

        idx = all_dates.index(date_str)
        if idx == 0:
            return {'success': False, 'error': f'No previous day before {date_str}'}

        prev_date = all_dates[idx - 1]
        today_closes = _get_closes_on_date(conn, date_str)
        prev_closes  = _get_closes_on_date(conn, prev_date)
        sector_map   = _get_symbol_sector_map(conn)

        # Aggregate per sector
        sector_stats = collections.defaultdict(lambda: {'advances': 0, 'declines': 0})

        for sym in set(today_closes) & set(prev_closes):
            sector = sector_map.get(sym, 'Unknown')
            delta  = today_closes[sym] - prev_closes[sym]
            if delta > 0:
                sector_stats[sector]['advances'] += 1
            elif delta < 0:
                sector_stats[sector]['declines'] += 1

        rows_out = []
        for sector, stats in sector_stats.items():
            adv = stats['advances']
            dec = stats['declines']
            total = adv + dec
            breadth_pct = round(_safe_div(adv, total) * 100, 2) if total else 50.0

            if breadth_pct >= 65:
                sec_signal = 'SECTOR_BULL'
            elif breadth_pct >= 50:
                sec_signal = 'SECTOR_LEAN_BULL'
            elif breadth_pct >= 35:
                sec_signal = 'SECTOR_LEAN_BEAR'
            else:
                sec_signal = 'SECTOR_BEAR'

            rows_out.append({
                'sector':      sector,
                'n_advances':  adv,
                'n_declines':  dec,
                'breadth_pct': breadth_pct,
                'signal':      sec_signal,
            })

            conn.execute("""
                INSERT OR REPLACE INTO market_breadth_sectors
                    (date, sector, n_advances, n_declines, breadth_pct)
                VALUES (?, ?, ?, ?, ?)
            """, (date_str, sector, adv, dec, breadth_pct))

        conn.commit()
        rows_out.sort(key=lambda x: x['breadth_pct'], reverse=True)

        return {
            'success': True,
            'date': date_str,
            'n_sectors': len(rows_out),
            'sectors': rows_out,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: breadth_signal
# ---------------------------------------------------------------------------

def breadth_signal(params: dict) -> dict:
    conn = get_db()
    ensure_tables(conn)

    try:
        date_str = params.get('date') or _latest_date(conn)
        if not date_str:
            return {'success': False, 'error': 'No OHLCV data in database'}

        # Use cached or compute
        row = conn.execute(
            "SELECT * FROM market_breadth_daily WHERE date = ?", (date_str,)
        ).fetchone()

        if not row or row['breadth_score'] is None:
            res = compute_breadth({'date': date_str})
            if not res.get('success'):
                return res
            row = conn.execute(
                "SELECT * FROM market_breadth_daily WHERE date = ?", (date_str,)
            ).fetchone()

        score  = row['breadth_score']
        signal = row['signal']

        # Regime label for upstream engines
        if score >= 60:
            regime_input = 'BULLISH_INTERNAL'
        elif score >= 40:
            regime_input = 'NEUTRAL'
        else:
            regime_input = 'BEARISH_INTERNAL'

        # Human-readable recommendation
        if signal == 'BREADTH_BULL':
            recommendation = ('Market internals are strongly bullish. '
                              'Breadth supports aggressive long exposure.')
        elif signal == 'BREADTH_LEAN_BULL':
            recommendation = ('Market internals lean bullish. '
                              'Moderate long bias is supported.')
        elif signal == 'BREADTH_NEUTRAL':
            recommendation = ('Market internals are mixed. '
                              'Maintain balanced positioning, wait for clarity.')
        elif signal == 'BREADTH_LEAN_BEAR':
            recommendation = ('Market internals lean bearish. '
                              'Reduce exposure, favor defensive positioning.')
        else:
            recommendation = ('Market internals are broadly weak. '
                              'Defensive posture or cash recommended.')

        return {
            'success': True,
            'date': date_str,
            'breadth_score': score,
            'signal': signal,
            'regime_input': regime_input,
            'key_stats': {
                'ad_ratio':            row['ad_ratio'],
                'pct_above_ma50':      row['pct_above_ma50'],
                'pct_above_ma200':     row['pct_above_ma200'],
                'n_new_highs_52w':     row['n_new_highs_52w'],
                'n_new_lows_52w':      row['n_new_lows_52w'],
                'mcclellan_oscillator': row['mcclellan_oscillator'],
                'mcclellan_summation':  row['mcclellan_summation'],
            },
            'recommendation': recommendation,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params: dict) -> dict:
    conn = get_db()
    ensure_tables(conn)
    conn.close()

    date_str = params.get('date') or None

    # Step 1: full breadth computation
    breadth_res = compute_breadth({'date': date_str} if date_str else {})
    if not breadth_res.get('success'):
        return breadth_res

    date_str = breadth_res['date']

    # Step 2: sector breakdown
    sector_res = sector_breadth({'date': date_str})

    # Step 3: breadth signal
    signal_res = breadth_signal({'date': date_str})

    # Step 4: 52-week high/low symbols
    hl_res = new_highs_lows({'date': date_str})

    return {
        'success': True,
        'date': date_str,
        'breadth': breadth_res,
        'sector_breadth': sector_res.get('sectors', []),
        'signal': signal_res,
        'new_highs_symbols': hl_res.get('symbols_highs', []),
        'new_lows_symbols':  hl_res.get('symbols_lows', []),
    }


# ---------------------------------------------------------------------------
# Command: compute_history
# ---------------------------------------------------------------------------

def compute_history(params: dict) -> dict:
    days = int(params.get('days', 90))

    conn = get_db()
    ensure_tables(conn)

    try:
        all_dates = _all_trading_dates(conn)
        if not all_dates:
            return {'success': False, 'error': 'No OHLCV data in database'}

        target_dates = all_dates[-days:]

        # Which dates are already in DB?
        if target_dates:
            placeholders = ','.join(['?' for _ in target_dates])
            existing = set(
                r[0] for r in conn.execute(
                    f"SELECT date FROM market_breadth_daily WHERE date IN ({placeholders})",
                    target_dates
                ).fetchall()
            )
        else:
            existing = set()

        missing_dates = [d for d in target_dates if d not in existing]

    finally:
        conn.close()

    # Compute missing dates (outside lock to avoid long transaction)
    computed_count = 0
    for d in missing_dates:
        res = compute_breadth({'date': d})
        if res.get('success') and 'breadth_score' in res:
            computed_count += 1

    # Now read results
    conn = get_db()
    try:
        if target_dates:
            placeholders = ','.join(['?' for _ in target_dates])
            rows = conn.execute(
                f"""SELECT date, signal, breadth_score, ad_ratio, pct_above_ma50
                    FROM market_breadth_daily
                    WHERE date IN ({placeholders})
                    ORDER BY date""",
                target_dates
            ).fetchall()
        else:
            rows = []

        result_list = [
            {
                'date':           r['date'],
                'signal':         r['signal'],
                'breadth_score':  r['breadth_score'],
                'ad_ratio':       r['ad_ratio'],
                'pct_above_ma50': r['pct_above_ma50'],
            }
            for r in rows
        ]

        return {
            'success': True,
            'days_requested': days,
            'dates_found': len(all_dates),
            'dates_in_range': len(target_dates),
            'newly_computed': computed_count,
            'data': result_list,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    'compute_breadth': compute_breadth,
    'advance_decline': advance_decline,
    'ma_breadth':      ma_breadth,
    'new_highs_lows':  new_highs_lows,
    'mcclellan':       mcclellan,
    'sector_breadth':  sector_breadth,
    'breadth_signal':  breadth_signal,
    'build_full':      build_full,
    'history':         compute_history,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    fn = COMMANDS.get(cmd)
    if not fn:
        print(json.dumps({
            'error':    f'Unknown command: {cmd}',
            'commands': list(COMMANDS),
        }))
        sys.exit(1)
    result = fn(params)
    print(json.dumps(result, default=str))
