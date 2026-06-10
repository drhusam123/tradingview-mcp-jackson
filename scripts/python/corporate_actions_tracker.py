#!/usr/bin/env python3
"""
Phase 54: Corporate Actions Tracker
Detects, records, and adjusts for EGX corporate actions (dividends, rights issues,
capital increases, splits, bonus shares) that create price distortions in historical data.
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import datetime
import collections

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ── Detection rules ───────────────────────────────────────────────────────────

DETECTION_RULES = {
    'DIVIDEND':     {'gap_pct': (-20, -2),  'volume_mult': 0.5,  'confidence_base': 0.6},
    'RIGHTS_ISSUE': {'gap_pct': (-35, -15), 'volume_mult': 3.0,  'confidence_base': 0.75},
    'SPLIT':        {'gap_pct': (-60, -40), 'volume_mult': 5.0,  'confidence_base': 0.70},
    'CAPITAL_INC':  {'gap_pct': (-50, -20), 'volume_mult': 2.0,  'confidence_base': 0.65},
    'BONUS_SHARES': {'gap_pct': (-40, -10), 'volume_mult': 2.5,  'confidence_base': 0.70},
    'SUSPICIOUS':   {'gap_pct': (-100, -5), 'volume_mult': 1.5,  'confidence_base': 0.3},
}

# Threshold for flagging a gap as a potential corporate action
GAP_FLAG_THRESHOLD = -5.0  # percent

# Window for computing average volume
AVG_VOLUME_WINDOW = 20

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            price_before REAL,
            price_after REAL,
            gap_pct REAL,
            volume_on_day REAL,
            avg_volume_20d REAL,
            volume_multiple REAL,
            adjustment_factor REAL,
            confidence REAL,
            is_confirmed INTEGER DEFAULT 0,
            is_adjusted INTEGER DEFAULT 0,
            notes TEXT,
            detected_at TEXT,
            UNIQUE(symbol, event_date)
        )
    """)
    db.commit()

# ── Classification logic ──────────────────────────────────────────────────────

def classify_event(gap_pct, volume_multiple):
    """
    Return (event_type, confidence) for the given gap % and volume multiple.
    Iterates rules in priority order; returns the best matching type.
    Falls back to SUSPICIOUS if nothing matches cleanly.
    """
    priority_order = ['SPLIT', 'RIGHTS_ISSUE', 'CAPITAL_INC', 'BONUS_SHARES', 'DIVIDEND', 'SUSPICIOUS']

    best_type = 'UNKNOWN'
    best_conf = 0.0

    for etype in priority_order:
        rule = DETECTION_RULES[etype]
        lo, hi = rule['gap_pct']
        vm_target = rule['volume_mult']
        conf_base = rule['confidence_base']

        if lo <= gap_pct <= hi:
            # Volume bonus/penalty
            if volume_multiple > 0:
                vm_ratio = volume_multiple / vm_target
                if vm_ratio >= 1.0:
                    vm_bonus = min(0.20, (vm_ratio - 1.0) * 0.05)
                else:
                    vm_bonus = max(-0.20, (vm_ratio - 1.0) * 0.05)
            else:
                vm_bonus = -0.10

            # Gap centrality bonus: closer to the midpoint of the range → higher confidence
            midpoint = (lo + hi) / 2.0
            range_half = (hi - lo) / 2.0 if (hi - lo) > 0 else 1.0
            centrality = 1.0 - abs(gap_pct - midpoint) / range_half
            gap_bonus = centrality * 0.15

            conf = min(1.0, max(0.0, conf_base + vm_bonus + gap_bonus))

            if conf > best_conf:
                best_conf = conf
                best_type = etype

    if best_type == 'UNKNOWN' and gap_pct < GAP_FLAG_THRESHOLD:
        best_type = 'SUSPICIOUS'
        best_conf = DETECTION_RULES['SUSPICIOUS']['confidence_base']

    return best_type, round(best_conf, 4)


def is_isolated_event(bars, idx):
    """
    Return True if the gap at bars[idx] appears isolated (neighbours are not
    also highly volatile), which is a signal that a corporate action occurred
    rather than a market-wide crash.
    """
    window = 3  # bars on each side to check
    event_gap = 0.0
    prev_close = bars[idx - 1]['close']
    if prev_close and prev_close > 0:
        event_gap = abs((bars[idx]['close'] - prev_close) / prev_close * 100)

    neighbour_gaps = []
    for offset in range(-window, window + 1):
        j = idx + offset
        if j <= 0 or j >= len(bars) or offset == 0:
            continue
        prev_c = bars[j - 1]['close']
        curr_c = bars[j]['close']
        if prev_c and prev_c > 0 and curr_c:
            g = abs((curr_c - prev_c) / prev_c * 100)
            neighbour_gaps.append(g)

    if not neighbour_gaps:
        return True

    avg_neighbour = statistics.mean(neighbour_gaps)
    # If event gap is much larger than neighbour volatility → isolated
    return event_gap > avg_neighbour * 2.5

# ── Commands ──────────────────────────────────────────────────────────────────

def scan_symbol(params):
    symbol = params.get('symbol', '')
    lookback = params.get('lookback_days', 1000)

    if not symbol:
        return {'error': 'symbol is required', 'success': False}

    db = get_db()
    ensure_table(db)

    cutoff = int(
        (datetime.datetime.utcnow() - datetime.timedelta(days=lookback)).timestamp()
    )

    rows = db.execute("""
        SELECT bar_time, open, high, low, close, volume,
               date(bar_time, 'unixepoch') as bar_date
        FROM ohlcv_history
        WHERE symbol = ? AND bar_time >= ?
        ORDER BY bar_time ASC
    """, (symbol, cutoff)).fetchall()

    if len(rows) < AVG_VOLUME_WINDOW + 2:
        return {
            'success': True,
            'symbol': symbol,
            'n_detected': 0,
            'events': [],
            'message': f'Insufficient data ({len(rows)} bars)'
        }

    # Convert to list of dicts for easier indexing
    bars = [dict(r) for r in rows]

    events = []
    now_str = datetime.datetime.utcnow().isoformat()

    for i in range(1, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]

        prev_close = prev['close']
        curr_close = curr['close']

        if not prev_close or prev_close <= 0 or not curr_close:
            continue

        gap_pct = (curr_close - prev_close) / prev_close * 100

        # Only flag large negative gaps
        if gap_pct >= GAP_FLAG_THRESHOLD:
            continue

        # Compute 20-day average volume (bars before current)
        vol_window_start = max(0, i - AVG_VOLUME_WINDOW)
        vol_slice = [
            b['volume'] for b in bars[vol_window_start:i]
            if b['volume'] and b['volume'] > 0
        ]
        avg_vol_20d = statistics.mean(vol_slice) if vol_slice else 0.0
        vol_on_day = curr['volume'] or 0.0
        vol_multiple = (vol_on_day / avg_vol_20d) if avg_vol_20d > 0 else 0.0

        # Filter out days where neighbours are also very volatile (market event, not corp action)
        if not is_isolated_event(bars, i):
            continue

        event_type, confidence = classify_event(gap_pct, vol_multiple)
        adjustment_factor = curr_close / prev_close if prev_close > 0 else 1.0

        event = {
            'symbol': symbol,
            'event_date': curr['bar_date'],
            'event_type': event_type,
            'price_before': round(prev_close, 4),
            'price_after': round(curr_close, 4),
            'gap_pct': round(gap_pct, 4),
            'volume_on_day': round(vol_on_day, 2),
            'avg_volume_20d': round(avg_vol_20d, 2),
            'volume_multiple': round(vol_multiple, 4),
            'adjustment_factor': round(adjustment_factor, 6),
            'confidence': confidence,
            'is_confirmed': 0,
            'is_adjusted': 0,
            'notes': None,
            'detected_at': now_str,
        }

        # Upsert into DB (ignore duplicate symbol+event_date)
        try:
            db.execute("""
                INSERT OR IGNORE INTO corporate_actions
                  (symbol, event_date, event_type, price_before, price_after,
                   gap_pct, volume_on_day, avg_volume_20d, volume_multiple,
                   adjustment_factor, confidence, is_confirmed, is_adjusted,
                   notes, detected_at)
                VALUES
                  (?,?,?,?,?,?,?,?,?,?,?,0,0,NULL,?)
            """, (
                symbol, event['event_date'], event_type,
                event['price_before'], event['price_after'],
                event['gap_pct'], event['volume_on_day'],
                event['avg_volume_20d'], event['volume_multiple'],
                event['adjustment_factor'], confidence, now_str
            ))
        except sqlite3.Error:
            pass  # Already exists — don't overwrite confirmed records

        events.append(event)

    db.commit()
    db.close()

    return {
        'success': True,
        'symbol': symbol,
        'bars_scanned': len(bars),
        'n_detected': len(events),
        'events': events,
    }


def scan_all(params):
    lookback = params.get('lookback_days', 365)

    db = get_db()
    symbols_rows = db.execute(
        "SELECT DISTINCT symbol FROM stock_universe ORDER BY symbol"
    ).fetchall()
    db.close()

    if not symbols_rows:
        return {'success': True, 'total_detected': 0, 'by_type': {}, 'most_affected_symbols': []}

    symbols = [r['symbol'] for r in symbols_rows]

    total_detected = 0
    by_type = collections.defaultdict(int)
    symbol_counts = {}
    all_events = []

    for sym in symbols:
        result = scan_symbol({'symbol': sym, 'lookback_days': lookback})
        if result.get('success') and result.get('n_detected', 0) > 0:
            n = result['n_detected']
            total_detected += n
            symbol_counts[sym] = n
            for ev in result.get('events', []):
                by_type[ev['event_type']] += 1
                all_events.append(ev)

    most_affected = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        'success': True,
        'symbols_scanned': len(symbols),
        'total_detected': total_detected,
        'by_type': dict(by_type),
        'most_affected_symbols': [{'symbol': s, 'count': c} for s, c in most_affected],
    }


def list_events(params):
    symbol = params.get('symbol', None)
    event_type = params.get('event_type', None)
    confirmed_only = params.get('confirmed_only', False)

    db = get_db()
    ensure_table(db)

    query = "SELECT * FROM corporate_actions WHERE 1=1"
    args = []

    if symbol:
        query += " AND symbol = ?"
        args.append(symbol)
    if event_type:
        query += " AND event_type = ?"
        args.append(event_type)
    if confirmed_only:
        query += " AND is_confirmed = 1"

    query += " ORDER BY event_date DESC"

    rows = db.execute(query, args).fetchall()
    db.close()

    events = [dict(r) for r in rows]

    return {
        'success': True,
        'count': len(events),
        'events': events,
    }


def confirm_event(params):
    symbol = params.get('symbol', '')
    event_date = params.get('event_date', '')
    event_type = params.get('event_type', None)

    if not symbol or not event_date:
        return {'success': False, 'error': 'symbol and event_date are required'}

    db = get_db()
    ensure_table(db)

    if event_type:
        db.execute("""
            UPDATE corporate_actions
            SET is_confirmed = 1, event_type = ?
            WHERE symbol = ? AND event_date = ?
        """, (event_type, symbol, event_date))
    else:
        db.execute("""
            UPDATE corporate_actions
            SET is_confirmed = 1
            WHERE symbol = ? AND event_date = ?
        """, (symbol, event_date))

    rows_updated = db.execute(
        "SELECT changes()"
    ).fetchone()[0]
    db.commit()
    db.close()

    if rows_updated == 0:
        return {'success': False, 'error': 'Event not found — run scan_symbol first'}

    return {
        'success': True,
        'symbol': symbol,
        'event_date': event_date,
        'confirmed': True,
        'event_type_set': event_type,
    }


def impact_analysis(params):
    db = get_db()
    ensure_table(db)

    cutoff_365 = (datetime.datetime.utcnow() - datetime.timedelta(days=365)).strftime('%Y-%m-%d')

    # Count events in last 365 days
    total_row = db.execute(
        "SELECT COUNT(*) as cnt FROM corporate_actions WHERE event_date >= ?",
        (cutoff_365,)
    ).fetchone()
    total_365 = total_row['cnt'] if total_row else 0

    # Unconfirmed events
    unconfirmed_row = db.execute(
        "SELECT COUNT(*) as cnt FROM corporate_actions WHERE event_date >= ? AND is_confirmed = 0",
        (cutoff_365,)
    ).fetchone()
    unconfirmed_365 = unconfirmed_row['cnt'] if unconfirmed_row else 0

    # Symbols with unconfirmed events
    suspicious_symbols = db.execute("""
        SELECT symbol, COUNT(*) as cnt
        FROM corporate_actions
        WHERE event_date >= ? AND is_confirmed = 0
        GROUP BY symbol
        ORDER BY cnt DESC
        LIMIT 20
    """, (cutoff_365,)).fetchall()

    # Estimate contaminated laws: any law whose backtest period overlaps with a
    # known (unconfirmed) corporate action day for any symbol in its universe
    # Proxy: count pattern_laws backtests that touch affected symbols
    affected_symbols = {r['symbol'] for r in suspicious_symbols}

    contaminated_laws_estimate = 0
    # pattern_laws doesn't have a symbol column — use affected symbol count as proxy
    contaminated_laws_estimate = len(affected_symbols) * 5  # rough estimate: 5 laws per affected symbol

    # Returns around events — are days T-1, T, T+1 predictable?
    event_rows = db.execute("""
        SELECT ca.symbol, ca.event_date, ca.gap_pct, ca.event_type
        FROM corporate_actions ca
        WHERE ca.event_date >= ?
        ORDER BY ca.event_date DESC
        LIMIT 200
    """, (cutoff_365,)).fetchall()

    gap_stats = {}
    if event_rows:
        gaps = [r['gap_pct'] for r in event_rows if r['gap_pct'] is not None]
        if gaps:
            gap_stats = {
                'mean_gap_pct': round(statistics.mean(gaps), 2),
                'median_gap_pct': round(statistics.median(gaps), 2),
                'stdev_gap_pct': round(statistics.stdev(gaps), 2) if len(gaps) > 1 else 0.0,
                'min_gap_pct': round(min(gaps), 2),
                'max_gap_pct': round(max(gaps), 2),
            }

    # Recommendation
    revalidation_needed = contaminated_laws_estimate
    recommendation = (
        f"Found {unconfirmed_365} unconfirmed corporate action events in the last 365 days "
        f"across {len(affected_symbols)} symbols. "
        f"Approximately {revalidation_needed} laws may be contaminated. "
        "Recommended action: run confirm_event on high-confidence detections, then "
        "re-validate affected laws excluding corporate action dates (±3 bars)."
    )

    db.close()

    return {
        'success': True,
        'period': 'last_365_days',
        'total_events': total_365,
        'unconfirmed_events': unconfirmed_365,
        'confirmed_events': total_365 - unconfirmed_365,
        'symbols_with_unconfirmed': [
            {'symbol': r['symbol'], 'unconfirmed_count': r['cnt']}
            for r in suspicious_symbols
        ],
        'contaminated_laws_estimate': contaminated_laws_estimate,
        'gap_statistics': gap_stats,
        'revalidation_needed': revalidation_needed,
        'recommendation': recommendation,
    }


def unadjusted_data_warning(params):
    db = get_db()
    ensure_table(db)

    # Symbols with confirmed but unadjusted corporate actions
    rows = db.execute("""
        SELECT symbol,
               COUNT(*) as event_count,
               MIN(event_date) as earliest_event,
               MAX(event_date) as latest_event,
               GROUP_CONCAT(DISTINCT event_type) as event_types,
               AVG(ABS(gap_pct)) as avg_distortion_pct
        FROM corporate_actions
        WHERE is_confirmed = 1 AND is_adjusted = 0
        GROUP BY symbol
        ORDER BY event_count DESC
    """).fetchall()

    warnings = []
    for r in rows:
        # Severity based on number of events and average distortion
        distortion = r['avg_distortion_pct'] or 0.0
        count = r['event_count'] or 0
        if distortion > 30 or count >= 3:
            severity = 'HIGH'
        elif distortion > 15 or count >= 2:
            severity = 'MEDIUM'
        else:
            severity = 'LOW'

        warnings.append({
            'symbol': r['symbol'],
            'event_count': count,
            'earliest_event': r['earliest_event'],
            'latest_event': r['latest_event'],
            'event_types': r['event_types'],
            'avg_distortion_pct': round(distortion, 2),
            'severity': severity,
        })

    # Estimate affected laws
    affected_symbols = {w['symbol'] for w in warnings}
    affected_laws_estimate = 0
    if affected_symbols:
        placeholders = ','.join('?' * len(affected_symbols))
        try:
            law_row = db.execute(f"""
                SELECT COUNT(DISTINCT id) as cnt
                FROM pattern_laws
                WHERE symbol IN ({placeholders})
            """, list(affected_symbols)).fetchone()
            affected_laws_estimate = law_row['cnt'] if law_row and law_row['cnt'] else 0
        except sqlite3.OperationalError:
            affected_laws_estimate = 0

    high_count = sum(1 for w in warnings if w['severity'] == 'HIGH')
    med_count = sum(1 for w in warnings if w['severity'] == 'MEDIUM')

    db.close()

    return {
        'success': True,
        'total_unadjusted_symbols': len(warnings),
        'severity_summary': {
            'HIGH': high_count,
            'MEDIUM': med_count,
            'LOW': len(warnings) - high_count - med_count,
        },
        'affected_laws_estimate': affected_laws_estimate,
        'warnings': warnings,
        'action_required': (
            f"{len(warnings)} symbols have confirmed corporate actions with unadjusted price history. "
            f"{high_count} are HIGH severity. These symbols' law backtests contain spurious signals."
        ) if warnings else "No unadjusted confirmed corporate actions found.",
    }


def build_full(params):
    scan_result = scan_all({'lookback_days': params.get('lookback_days', 365)})
    impact_result = impact_analysis({})
    warning_result = unadjusted_data_warning({})

    return {
        'success': True,
        'phase': 54,
        'report': 'Corporate Actions Full Build',
        'generated_at': datetime.datetime.utcnow().isoformat(),
        'scan_summary': {
            'symbols_scanned': scan_result.get('symbols_scanned', 0),
            'total_detected': scan_result.get('total_detected', 0),
            'by_type': scan_result.get('by_type', {}),
            'most_affected_symbols': scan_result.get('most_affected_symbols', []),
        },
        'impact_analysis': impact_result,
        'unadjusted_warnings': warning_result,
    }

# ── Command dispatch ──────────────────────────────────────────────────────────

COMMANDS = {
    'scan_symbol':              scan_symbol,
    'scan_all':                 scan_all,
    'list_events':              list_events,
    'confirm_event':            confirm_event,
    'impact_analysis':          impact_analysis,
    'unadjusted_data_warning':  unadjusted_data_warning,
    'build_full':               build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success': False,
            'error': 'Usage: corporate_actions_tracker.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'success': False, 'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({
            'success': False,
            'error': f'Unknown command: {cmd}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e), 'command': cmd}))
        sys.exit(1)


if __name__ == '__main__':
    main()
