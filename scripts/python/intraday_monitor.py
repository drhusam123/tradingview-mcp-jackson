"""
intraday_monitor.py — Phase 61: Intraday Monitor
EGX Autonomous Quant System

Manages real-time intraday data: DOM snapshots, live quote batches,
session status, and execution timing. The JS fetch script handles
TradingView calls; this Python file handles storage, analysis, and
session logic.

Commands:
    session_status      — current EGX session phase and timing metadata
    save_dom_snapshot   — store a DOM depth snapshot and compute metrics
    save_live_quotes    — batch-save live quote records
    execution_timing    — per-symbol execution recommendation
    compute_spread      — spread trend analysis from recent DOM snapshots
    live_snapshot       — real-time market picture across all live symbols
    build_full          — session_status + live_snapshot combined

Usage:
    python intraday_monitor.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import datetime
import collections

# ─── DB ──────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

NOW = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ─── Schema ───────────────────────────────────────────────────────────────────

def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_live_quotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            price       REAL,
            change_pct  REAL,
            volume      REAL,
            bid         REAL,
            ask         REAL,
            spread_pct  REAL,
            fetched_at  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dom_live_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol            TEXT NOT NULL,
            dom_data          TEXT,
            best_bid          REAL,
            best_ask          REAL,
            spread_bps        REAL,
            total_bid_depth   REAL,
            total_ask_depth   REAL,
            imbalance_ratio   REAL,
            fetched_at        TEXT
        )
    """)
    conn.commit()


# ─── EGX Session Logic ────────────────────────────────────────────────────────

EGX_OFFSET_HOURS = 2          # Egypt Standard Time, UTC+2 (no DST in recent years)
EGX_TRADING_WEEKDAYS = {0, 1, 2, 3, 6}   # Mon=0 … Sun=6

# Session boundaries in Cairo local time (HH, MM)
SESSION_PHASES = [
    ('PRE_MARKET',      ( 9, 30), (10,  0)),
    ('OPENING_AUCTION', (10,  0), (10,  5)),
    ('CONTINUOUS',      (10,  5), (14, 25)),
    ('CLOSING_AUCTION', (14, 25), (14, 30)),
]
SESSION_START_HM = (10,  0)
SESSION_END_HM   = (14, 30)


def _to_cairo(utc_dt):
    """Return a naive datetime in Cairo local time (UTC+2)."""
    return utc_dt + datetime.timedelta(hours=EGX_OFFSET_HOURS)


def _hm_to_minutes(h, m):
    return h * 60 + m


def _phase_for_hm(h, m):
    total = _hm_to_minutes(h, m)
    for phase, (sh, sm), (eh, em) in SESSION_PHASES:
        if _hm_to_minutes(sh, sm) <= total < _hm_to_minutes(eh, em):
            return phase
    return 'CLOSED'


def _optimal_execution(phase):
    mapping = {
        'OPENING_AUCTION': 'OPENING_MOMENTUM',
        'CONTINUOUS':      'MID_SESSION',     # refined below by time
        'CLOSING_AUCTION': 'PRE_CLOSE',
        'PRE_MARKET':      'AVOID',
        'CLOSED':          'AVOID',
    }
    return mapping.get(phase, 'AVOID')


def cmd_session_status(_params):
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    cairo   = _to_cairo(utc_now)
    weekday = cairo.weekday()   # Monday=0 … Sunday=6

    is_trading_day = weekday in EGX_TRADING_WEEKDAYS
    phase = _phase_for_hm(cairo.hour, cairo.minute) if is_trading_day else 'CLOSED'

    # Minutes to open / close
    current_mins   = _hm_to_minutes(cairo.hour, cairo.minute)
    open_mins      = _hm_to_minutes(*SESSION_START_HM)
    close_mins     = _hm_to_minutes(*SESSION_END_HM)
    session_total  = close_mins - open_mins     # 270 min = 4.5 h

    minutes_to_open  = None
    minutes_to_close = None
    session_progress = 0.0

    if is_trading_day:
        if phase == 'CLOSED':
            if current_mins < open_mins:
                minutes_to_open = open_mins - current_mins
            else:
                # After close — compute for tomorrow (simplified: show 0)
                minutes_to_open = 0
        else:
            minutes_to_close   = max(0, close_mins - current_mins)
            elapsed            = max(0, current_mins - open_mins)
            session_progress   = round(min(100.0, elapsed / session_total * 100), 1)

    # Refine optimal execution for CONTINUOUS phase based on time-of-day
    opt_exec = _optimal_execution(phase)
    if phase == 'CONTINUOUS':
        if current_mins <= open_mins + 60:
            opt_exec = 'OPENING_MOMENTUM'   # first hour
        elif current_mins >= close_mins - 30:
            opt_exec = 'PRE_CLOSE'
        else:
            opt_exec = 'MID_SESSION'

    result = {
        'current_utc':          utc_now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'cairo_time':           cairo.strftime('%Y-%m-%d %H:%M:%S'),
        'session_phase':        phase,
        'is_trading_day':       is_trading_day,
        'minutes_to_open':      minutes_to_open,
        'minutes_to_close':     minutes_to_close,
        'session_progress_pct': session_progress,
        'optimal_execution':    opt_exec,
    }
    return result


# ─── DOM Snapshot ─────────────────────────────────────────────────────────────

def _compute_dom_metrics(dom_data):
    """
    dom_data: {"bids": [{"price": X, "volume": Y}, ...], "asks": [...]}
    Returns dict with best_bid, best_ask, spread_bps, total_bid_depth,
    total_ask_depth, imbalance_ratio.
    """
    bids = dom_data.get('bids', [])
    asks = dom_data.get('asks', [])

    best_bid = max((b['price'] for b in bids), default=None)
    best_ask = min((a['price'] for a in asks), default=None)

    spread_bps = None
    if best_bid and best_ask and best_bid > 0 and best_ask > 0:
        mid        = (best_bid + best_ask) / 2.0
        spread_bps = round((best_ask - best_bid) / mid * 10000, 2)

    total_bid_depth = sum(b.get('volume', 0) for b in bids)
    total_ask_depth = sum(a.get('volume', 0) for a in asks)

    imbalance_ratio = None
    combined = total_bid_depth + total_ask_depth
    if combined > 0:
        imbalance_ratio = round(total_bid_depth / combined, 4)

    return {
        'best_bid':        best_bid,
        'best_ask':        best_ask,
        'spread_bps':      spread_bps,
        'total_bid_depth': total_bid_depth,
        'total_ask_depth': total_ask_depth,
        'imbalance_ratio': imbalance_ratio,
    }


def cmd_save_dom_snapshot(params):
    symbol   = params.get('symbol', '').upper()
    dom_data = params.get('dom_data', {})
    if not symbol:
        return {'error': 'symbol is required'}

    metrics     = _compute_dom_metrics(dom_data)
    fetched_at  = NOW
    dom_json    = json.dumps(dom_data)

    conn = get_db()
    ensure_tables(conn)
    conn.execute("""
        INSERT INTO dom_live_snapshots
            (symbol, dom_data, best_bid, best_ask, spread_bps,
             total_bid_depth, total_ask_depth, imbalance_ratio, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        symbol, dom_json,
        metrics['best_bid'], metrics['best_ask'], metrics['spread_bps'],
        metrics['total_bid_depth'], metrics['total_ask_depth'],
        metrics['imbalance_ratio'], fetched_at,
    ))
    conn.commit()
    conn.close()

    return {
        'symbol':     symbol,
        'fetched_at': fetched_at,
        **metrics,
    }


# ─── Live Quotes ──────────────────────────────────────────────────────────────

def cmd_save_live_quotes(params):
    quotes_raw = params.get('quotes', [])
    if not quotes_raw:
        return {'error': 'quotes list is required'}

    fetched_at = NOW
    rows = []
    for q in quotes_raw:
        symbol     = str(q.get('symbol', '')).upper()
        price      = q.get('price')
        change_pct = q.get('change_pct')
        volume     = q.get('volume')
        bid        = q.get('bid')
        ask        = q.get('ask')

        spread_pct = None
        if bid is not None and ask is not None and price and price > 0:
            spread_pct = round((ask - bid) / price * 100, 4)

        rows.append((symbol, price, change_pct, volume, bid, ask, spread_pct, fetched_at))

    conn = get_db()
    ensure_tables(conn)
    conn.executemany("""
        INSERT INTO intraday_live_quotes
            (symbol, price, change_pct, volume, bid, ask, spread_pct, fetched_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

    return {'n_saved': len(rows), 'fetched_at': fetched_at}


# ─── Execution Timing ─────────────────────────────────────────────────────────

def _market_pressure_label(imbalance_ratio):
    if imbalance_ratio is None:
        return 'NEUTRAL'
    if imbalance_ratio > 0.6:
        return 'BUY_PRESSURE'
    if imbalance_ratio < 0.4:
        return 'SELL_PRESSURE'
    return 'NEUTRAL'


def cmd_execution_timing(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol is required'}

    session = cmd_session_status({})
    phase   = session['session_phase']

    conn = get_db()
    ensure_tables(conn)

    # Recent DOM snapshots (last 10)
    dom_rows = conn.execute("""
        SELECT spread_bps, imbalance_ratio
        FROM   dom_live_snapshots
        WHERE  symbol = ?
        ORDER  BY id DESC
        LIMIT  10
    """, (symbol,)).fetchall()

    # Recent intraday_analytics from Phase 50 (if table exists)
    analytics_spread = None
    try:
        ana_rows = conn.execute("""
            SELECT avg_spread_bps
            FROM   intraday_analytics
            WHERE  symbol = ?
            ORDER  BY id DESC
            LIMIT  5
        """, (symbol,)).fetchall()
        valid = [r['avg_spread_bps'] for r in ana_rows if r['avg_spread_bps'] is not None]
        if valid:
            analytics_spread = round(sum(valid) / len(valid), 2)
    except sqlite3.OperationalError:
        pass   # table doesn't exist yet

    conn.close()

    current_spread_bps = dom_rows[0]['spread_bps'] if dom_rows else None
    current_imbalance  = dom_rows[0]['imbalance_ratio'] if dom_rows else None

    valid_spreads = [r['spread_bps'] for r in dom_rows if r['spread_bps'] is not None]
    avg_spread_today = round(sum(valid_spreads) / len(valid_spreads), 2) if valid_spreads else analytics_spread

    market_pressure = _market_pressure_label(current_imbalance)

    # Build recommendation
    opt = session['optimal_execution']
    if not session['is_trading_day'] or phase == 'CLOSED':
        recommendation = 'MARKET_CLOSED'
        reason         = 'EGX is closed right now.'
    elif phase == 'PRE_MARKET':
        recommendation = 'WAIT_FOR_OPEN'
        reason         = 'Pre-market window. Wait for opening auction at 10:00 Cairo.'
    elif phase == 'OPENING_AUCTION':
        recommendation = 'EXECUTE_NOW'
        reason         = 'Opening auction in progress — good for momentum entries.'
    elif phase == 'CONTINUOUS':
        if opt == 'OPENING_MOMENTUM':
            recommendation = 'EXECUTE_NOW'
            reason         = 'First hour of continuous session — strong momentum window.'
        elif opt == 'PRE_CLOSE':
            recommendation = 'AVOID_CLOSE'
            reason         = 'Last 30 minutes before close — wider spreads, lower liquidity.'
        else:
            if current_spread_bps and current_spread_bps > 100:
                recommendation = 'WAIT_MID_SESSION'
                reason         = f'Spread currently wide ({current_spread_bps:.0f} bps). Monitor before entering.'
            elif market_pressure == 'SELL_PRESSURE':
                recommendation = 'WAIT_MID_SESSION'
                reason         = 'DOM shows sell pressure. Wait for imbalance to neutralise.'
            else:
                recommendation = 'EXECUTE_NOW'
                reason         = 'Mid-session, normal spread conditions.'
    elif phase == 'CLOSING_AUCTION':
        recommendation = 'AVOID_CLOSE'
        reason         = 'Closing auction — avoid new entries.'
    else:
        recommendation = 'MARKET_CLOSED'
        reason         = 'Outside trading hours.'

    return {
        'symbol':              symbol,
        'session_phase':       phase,
        'recommendation':      recommendation,
        'reason':              reason,
        'current_spread_bps':  current_spread_bps,
        'avg_spread_bps_today': avg_spread_today,
        'imbalance_ratio':     current_imbalance,
        'market_pressure':     market_pressure,
    }


# ─── Spread Analysis ──────────────────────────────────────────────────────────

def cmd_compute_spread(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol is required'}

    conn = get_db()
    ensure_tables(conn)
    rows = conn.execute("""
        SELECT spread_bps
        FROM   dom_live_snapshots
        WHERE  symbol = ?
        ORDER  BY id DESC
        LIMIT  20
    """, (symbol,)).fetchall()
    conn.close()

    values = [r['spread_bps'] for r in rows if r['spread_bps'] is not None]
    if not values:
        return {'symbol': symbol, 'error': 'no DOM snapshots found'}

    avg_spread     = round(sum(values) / len(values), 2)
    min_spread     = round(min(values), 2)
    max_spread     = round(max(values), 2)
    current_spread = round(values[0], 2)   # most recent is first (DESC)

    # Trend: compare first half vs second half of the series
    mid = len(values) // 2
    if mid >= 2:
        recent_avg = sum(values[:mid]) / mid
        older_avg  = sum(values[mid:]) / (len(values) - mid)
        if recent_avg < older_avg * 0.95:
            spread_trend = 'TIGHTENING'
        elif recent_avg > older_avg * 1.05:
            spread_trend = 'WIDENING'
        else:
            spread_trend = 'STABLE'
    else:
        spread_trend = 'STABLE'

    return {
        'symbol':           symbol,
        'avg_spread_bps':   avg_spread,
        'min_spread_bps':   min_spread,
        'max_spread_bps':   max_spread,
        'current_spread_bps': current_spread,
        'spread_trend':     spread_trend,
        'n_snapshots':      len(values),
    }


# ─── Live Snapshot ────────────────────────────────────────────────────────────

def cmd_live_snapshot(params):
    top_n = int(params.get('top_n', 20))

    session = cmd_session_status({})

    # Cutoff: last 30 minutes
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ')

    conn = get_db()
    ensure_tables(conn)

    # Most recent quote per symbol in the last 30 min
    rows = conn.execute("""
        SELECT   symbol,
                 price, change_pct, volume, bid, ask, spread_pct,
                 MAX(fetched_at) AS last_seen
        FROM     intraday_live_quotes
        WHERE    fetched_at >= ?
        GROUP BY symbol
        ORDER BY change_pct DESC
    """, (cutoff,)).fetchall()

    # Most recent DOM spread per symbol
    dom_rows = conn.execute("""
        SELECT   symbol, spread_bps, MAX(fetched_at) AS last_dom
        FROM     dom_live_snapshots
        WHERE    fetched_at >= ?
        GROUP BY symbol
    """, (cutoff,)).fetchall()
    dom_spread = {r['symbol']: r['spread_bps'] for r in dom_rows}

    conn.close()

    all_quotes = [dict(r) for r in rows]
    n_symbols  = len(all_quotes)

    sorted_up   = sorted(all_quotes, key=lambda x: x.get('change_pct') or -999, reverse=True)
    sorted_down = sorted(all_quotes, key=lambda x: x.get('change_pct') or 999)

    def fmt(q):
        return {
            'symbol':     q['symbol'],
            'price':      q['price'],
            'change_pct': q['change_pct'],
            'volume':     q['volume'],
        }

    top_movers_up   = [fmt(q) for q in sorted_up[:5]]
    top_movers_down = [fmt(q) for q in sorted_down[:5]]

    # High spread warning: DOM spread > 50 bps
    high_spread = []
    for sym, spread in dom_spread.items():
        if spread is not None and spread > 50:
            high_spread.append({'symbol': sym, 'spread_bps': round(spread, 1)})
    high_spread.sort(key=lambda x: -x['spread_bps'])

    return {
        'session_phase':        session['session_phase'],
        'cairo_time':           session['cairo_time'],
        'n_symbols_live':       n_symbols,
        'top_movers_up':        top_movers_up,
        'top_movers_down':      top_movers_down,
        'high_spread_warning':  high_spread,
        'timestamp':            NOW,
    }


# ─── Build Full ───────────────────────────────────────────────────────────────

def cmd_build_full(_params):
    session  = cmd_session_status({})
    snapshot = cmd_live_snapshot({'top_n': 20})
    return {
        'session':  session,
        'snapshot': snapshot,
    }


# ─── Dispatch ─────────────────────────────────────────────────────────────────

COMMANDS = {
    'session_status':    cmd_session_status,
    'save_dom_snapshot': cmd_save_dom_snapshot,
    'save_live_quotes':  cmd_save_live_quotes,
    'execution_timing':  cmd_execution_timing,
    'compute_spread':    cmd_compute_spread,
    'live_snapshot':     cmd_live_snapshot,
    'build_full':        cmd_build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: python intraday_monitor.py <command> [json_params]'}))
        sys.exit(1)

    command    = sys.argv[1]
    params_raw = sys.argv[2] if len(sys.argv) > 2 else '{}'

    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({'error': f'Invalid JSON params: {exc}'}))
        sys.exit(1)

    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({'error': f'Unknown command: {command}', 'available': list(COMMANDS)}))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'command': command}))
        sys.exit(1)


if __name__ == '__main__':
    main()
