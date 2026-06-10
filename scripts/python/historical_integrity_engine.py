#!/usr/bin/env python3
"""
Historical Integrity Engine — Phase 20
========================================
يفحص جودة البيانات التاريخية لكل سهم في الكون المصري ويحسب درجة النزاهة.

الاستخدام:
  python scripts/python/historical_integrity_engine.py scan_all
  python scripts/python/historical_integrity_engine.py scan_symbol '{"symbol":"COMI"}'
  python scripts/python/historical_integrity_engine.py compute_breadth
  python scripts/python/historical_integrity_engine.py get_report
  python scripts/python/historical_integrity_engine.py get_confidence
  python scripts/python/historical_integrity_engine.py flag_anomalies

المالك: Dr. Husam | الإصدار: Phase 20 | مايو 2026
"""

import os
import sys
import json
import math
import sqlite3
import datetime
from typing import Any, Dict, List, Optional, Tuple


# ─── DB PATH ──────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ─── Constants ────────────────────────────────────────────────────────────────
EXPECTED_BARS        = 315     # ~15 months × 21 trading days/month
MIN_BARS_PREMIUM     = 250
COMPLETENESS_GOOD    = 0.95    # 95 % threshold
MAX_GAP_NORMAL       = 5      # calendar days (covers long weekends + holidays)
ANOMALY_RETURN_LIMIT = 0.15    # 15 % daily return triggers anomaly flag
SECONDS_PER_DAY      = 86_400

# Tier thresholds
TIER_PREMIUM    = 90
TIER_STANDARD   = 70
TIER_LOW        = 50

# ─── Penalty weights ──────────────────────────────────────────────────────────
PENALTY_LOW_COMPLETENESS = 20
PENALTY_MAX_GAP          = 15
PENALTY_PRICE_ANOMALIES  = 10
PENALTY_FEW_BARS         = 20


# ──────────────────────────────────────────────────────────────────────────────
#  Database helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Open (or create) the EGX trading database with WAL mode enabled."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    # Ensure integrity table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS data_integrity (
            symbol              TEXT    NOT NULL,
            check_date          TEXT    NOT NULL,
            total_bars          INTEGER,
            expected_bars       INTEGER,
            completeness_pct    REAL,
            max_gap_days        INTEGER,
            n_gaps              INTEGER,
            has_suspensions     INTEGER,
            stale_periods       INTEGER,
            price_anomalies     INTEGER,
            integrity_score     REAL,
            confidence_penalty  REAL,
            data_tier           TEXT,
            earliest_reliable   TEXT,
            latest_bar          TEXT,
            notes               TEXT,
            PRIMARY KEY (symbol, check_date)
        )
    """)

    # Ensure breadth table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_breadth_history (
            date                TEXT    PRIMARY KEY,
            n_advancing         INTEGER,
            n_declining         INTEGER,
            n_unchanged         INTEGER,
            breadth_ratio       REAL,
            advance_decline_line REAL,
            new_highs_52w       INTEGER,
            new_lows_52w        INTEGER,
            above_20ma          INTEGER,
            above_50ma          INTEGER,
            pct_positive        INTEGER,
            avg_volume          REAL,
            volume_ratio        REAL,
            computed_at         TEXT
        )
    """)

    db.commit()
    return db


# ──────────────────────────────────────────────────────────────────────────────
#  Gap detection helper
# ──────────────────────────────────────────────────────────────────────────────

def detect_gaps(bars: List[Dict]) -> List[Dict]:
    """
    Detect abnormal gaps in bar sequence.

    A gap is defined as >5 calendar days between consecutive bars (after
    accounting for weekends and occasional public holidays).

    Parameters
    ----------
    bars : list of dicts with key 'bar_time' (unix timestamp)

    Returns
    -------
    list of dicts: [{start, end, days}, ...]
    """
    gaps: List[Dict] = []
    for i in range(1, len(bars)):
        diff = (bars[i]['bar_time'] - bars[i - 1]['bar_time']) / SECONDS_PER_DAY
        if diff > MAX_GAP_NORMAL:
            gaps.append({
                'start': bars[i - 1]['bar_time'],
                'end':   bars[i]['bar_time'],
                'days':  round(diff, 1),
            })
    return gaps


# ──────────────────────────────────────────────────────────────────────────────
#  Integrity scoring
# ──────────────────────────────────────────────────────────────────────────────

def compute_tier(score: float) -> str:
    """Map a numeric integrity score to a data tier string."""
    if score >= TIER_PREMIUM:
        return 'PREMIUM'
    if score >= TIER_STANDARD:
        return 'STANDARD'
    if score >= TIER_LOW:
        return 'LOW'
    return 'UNRELIABLE'


def score_symbol(
    total_bars: int,
    completeness: float,
    max_gap: float,
    n_anomalies: int,
) -> float:
    """
    Compute integrity score (0–100) with four penalty dimensions.

    Penalties:
      - completeness < 95 %  → -20
      - max gap > 5 days     → -15
      - price anomalies > 3  → -10
      - total bars < 250     → -20
    """
    score = 100.0
    if completeness < COMPLETENESS_GOOD:
        score -= PENALTY_LOW_COMPLETENESS
    if max_gap > MAX_GAP_NORMAL:
        score -= PENALTY_MAX_GAP
    if n_anomalies > 3:
        score -= PENALTY_PRICE_ANOMALIES
    if total_bars < MIN_BARS_PREMIUM:
        score -= PENALTY_FEW_BARS
    return max(0.0, score)


def confidence_penalty_from_score(score: float) -> float:
    """Confidence penalty: max 50 % for UNRELIABLE symbols."""
    return round((1.0 - score / 100.0) * 0.5, 4)


# ──────────────────────────────────────────────────────────────────────────────
#  Price anomaly detection
# ──────────────────────────────────────────────────────────────────────────────

def find_price_anomalies(bars: List[Dict]) -> List[Dict]:
    """
    Detect price anomalies in bar list.

    Two types are flagged:
    1. Daily return > ±15 % (potential data error or trading halt)
    2. Zero-volume days with non-zero price change (phantom bars)

    Returns list of anomaly dicts.
    """
    anomalies: List[Dict] = []
    for i in range(1, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]

        prev_close = prev.get('close') or 0
        curr_close = curr.get('close') or 0

        if prev_close > 0 and curr_close > 0:
            daily_return = (curr_close - prev_close) / prev_close
            if abs(daily_return) > ANOMALY_RETURN_LIMIT:
                anomalies.append({
                    'type':        'large_move',
                    'bar_time':    curr['bar_time'],
                    'date':        ts_to_date(curr['bar_time']),
                    'return_pct':  round(daily_return * 100, 2),
                    'close':       curr_close,
                    'prev_close':  prev_close,
                })

        volume   = curr.get('volume') or 0
        curr_open = curr.get('open') or 0
        if volume == 0 and curr_close != curr_open:
            anomalies.append({
                'type':     'zero_vol_price_change',
                'bar_time': curr['bar_time'],
                'date':     ts_to_date(curr['bar_time']),
                'open':     curr_open,
                'close':    curr_close,
            })

    return anomalies


# ──────────────────────────────────────────────────────────────────────────────
#  Moving average helper
# ──────────────────────────────────────────────────────────────────────────────

def simple_moving_avg(prices: List[float], period: int, idx: int) -> Optional[float]:
    """Return SMA of length `period` ending at index `idx`."""
    if idx < period - 1:
        return None
    window = prices[idx - period + 1: idx + 1]
    return sum(window) / period


# ──────────────────────────────────────────────────────────────────────────────
#  Timestamp utilities
# ──────────────────────────────────────────────────────────────────────────────

def ts_to_date(ts: int) -> str:
    """Convert Unix timestamp (seconds) to YYYY-MM-DD string."""
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
    except Exception:
        return str(ts)


# ──────────────────────────────────────────────────────────────────────────────
#  Single-symbol integrity check
# ──────────────────────────────────────────────────────────────────────────────

def _check_one_symbol(db: sqlite3.Connection, symbol: str, check_date: str) -> Dict:
    """
    Run full integrity check for a single symbol.

    Returns a dict with all integrity fields suitable for insertion into
    data_integrity and/or returning as a result payload.
    """
    rows = db.execute(
        "SELECT bar_time, open, high, low, close, volume "
        "FROM ohlcv_history WHERE symbol=? ORDER BY bar_time ASC",
        (symbol,)
    ).fetchall()

    if not rows:
        return {
            'symbol':             symbol,
            'check_date':         check_date,
            'total_bars':         0,
            'expected_bars':      EXPECTED_BARS,
            'completeness_pct':   0.0,
            'max_gap_days':       0,
            'n_gaps':             0,
            'has_suspensions':    0,
            'stale_periods':      0,
            'price_anomalies':    0,
            'integrity_score':    0.0,
            'confidence_penalty': 0.5,
            'data_tier':          'UNRELIABLE',
            'earliest_reliable':  None,
            'latest_bar':         None,
            'notes':              'no data',
        }

    bars = [dict(r) for r in rows]
    total_bars   = len(bars)
    earliest_ts  = bars[0]['bar_time']
    latest_ts    = bars[-1]['bar_time']

    completeness = min(1.0, total_bars / EXPECTED_BARS)

    # Gap analysis
    gaps          = detect_gaps(bars)
    n_gaps        = len(gaps)
    max_gap_days  = max((g['days'] for g in gaps), default=0)

    # Detect possible suspension periods (gaps > 30 days)
    suspension_gaps = [g for g in gaps if g['days'] > 30]
    has_suspensions = 1 if suspension_gaps else 0

    # Stale periods: trailing gap — days since last bar to today
    today_ts      = int(datetime.datetime.utcnow().timestamp())
    stale_days    = (today_ts - latest_ts) / SECONDS_PER_DAY
    stale_periods = 1 if stale_days > 7 else 0  # no update for >1 week

    # Price anomalies
    anomalies      = find_price_anomalies(bars)
    n_anomalies    = len(anomalies)

    # Score
    score = score_symbol(total_bars, completeness, max_gap_days, n_anomalies)
    tier  = compute_tier(score)

    # Confidence penalty
    confidence = confidence_penalty_from_score(score)

    # Earliest reliable bar: skip if huge leading gap exists
    earliest_reliable = ts_to_date(earliest_ts)

    # Build notes
    notes_parts = []
    if n_gaps > 0:
        notes_parts.append(f'{n_gaps} gaps (max {max_gap_days:.0f}d)')
    if has_suspensions:
        notes_parts.append(f'{len(suspension_gaps)} suspension(s)')
    if n_anomalies > 0:
        notes_parts.append(f'{n_anomalies} price anomalies')
    if stale_periods:
        notes_parts.append(f'stale {stale_days:.0f}d')
    notes = '; '.join(notes_parts) if notes_parts else 'clean'

    return {
        'symbol':             symbol,
        'check_date':         check_date,
        'total_bars':         total_bars,
        'expected_bars':      EXPECTED_BARS,
        'completeness_pct':   round(completeness * 100, 2),
        'max_gap_days':       int(max_gap_days),
        'n_gaps':             n_gaps,
        'has_suspensions':    has_suspensions,
        'stale_periods':      stale_periods,
        'price_anomalies':    n_anomalies,
        'integrity_score':    round(score, 2),
        'confidence_penalty': confidence,
        'data_tier':          tier,
        'earliest_reliable':  earliest_reliable,
        'latest_bar':         ts_to_date(latest_ts),
        'notes':              notes,
    }


def _upsert_integrity(db: sqlite3.Connection, rec: Dict) -> None:
    """Upsert a data_integrity record."""
    db.execute("""
        INSERT OR REPLACE INTO data_integrity
            (symbol, check_date, total_bars, expected_bars, completeness_pct,
             max_gap_days, n_gaps, has_suspensions, stale_periods, price_anomalies,
             integrity_score, confidence_penalty, data_tier, earliest_reliable,
             latest_bar, notes)
        VALUES
            (:symbol, :check_date, :total_bars, :expected_bars, :completeness_pct,
             :max_gap_days, :n_gaps, :has_suspensions, :stale_periods, :price_anomalies,
             :integrity_score, :confidence_penalty, :data_tier, :earliest_reliable,
             :latest_bar, :notes)
    """, rec)


# ──────────────────────────────────────────────────────────────────────────────
#  Command: scan_all
# ──────────────────────────────────────────────────────────────────────────────

def cmd_scan_all(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Scan all symbols in stock_universe, compute integrity scores, and save results.

    Returns aggregate statistics and a list of low-quality symbols.
    """
    check_date = datetime.datetime.utcnow().strftime('%Y-%m-%d')

    # Pull all known symbols
    sym_rows = db.execute(
        "SELECT symbol FROM stock_universe ORDER BY symbol"
    ).fetchall()
    symbols = [r['symbol'] for r in sym_rows]

    if not symbols:
        # Fall back: scan whatever symbols exist in ohlcv_history
        sym_rows = db.execute(
            "SELECT DISTINCT symbol FROM ohlcv_history ORDER BY symbol"
        ).fetchall()
        symbols = [r['symbol'] for r in sym_rows]

    n_scanned        = 0
    score_sum        = 0.0
    tier_dist: Dict[str, int] = {
        'PREMIUM': 0, 'STANDARD': 0, 'LOW': 0, 'UNRELIABLE': 0
    }
    low_quality: List[Dict] = []
    errors: List[str]       = []

    for sym in symbols:
        try:
            rec = _check_one_symbol(db, sym, check_date)
            _upsert_integrity(db, rec)

            n_scanned  += 1
            score_sum  += rec['integrity_score']
            tier        = rec['data_tier']
            tier_dist[tier] = tier_dist.get(tier, 0) + 1

            if rec['data_tier'] in ('LOW', 'UNRELIABLE'):
                low_quality.append({
                    'symbol': sym,
                    'score':  rec['integrity_score'],
                    'tier':   rec['data_tier'],
                    'notes':  rec['notes'],
                })
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    db.commit()

    avg_integrity = round(score_sum / n_scanned, 2) if n_scanned else 0.0

    # Sort low-quality by score ascending (worst first)
    low_quality.sort(key=lambda x: x['score'])

    return {
        'command':           'scan_all',
        'check_date':        check_date,
        'n_scanned':         n_scanned,
        'avg_integrity':     avg_integrity,
        'tier_distribution': tier_dist,
        'low_quality_symbols': low_quality[:20],   # top 20 worst
        'errors':            errors[:10],
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Command: scan_symbol
# ──────────────────────────────────────────────────────────────────────────────

def cmd_scan_symbol(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Detailed integrity check for a single symbol.

    Params: {symbol: 'COMI'}
    """
    symbol = params.get('symbol', '').strip().upper()
    if not symbol:
        return {'error': 'symbol param required', 'command': 'scan_symbol'}

    check_date = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    rec = _check_one_symbol(db, symbol, check_date)
    _upsert_integrity(db, rec)
    db.commit()

    # Fetch gap detail
    bars_raw = db.execute(
        "SELECT bar_time, close, volume FROM ohlcv_history "
        "WHERE symbol=? ORDER BY bar_time ASC",
        (symbol,)
    ).fetchall()
    bars = [dict(r) for r in bars_raw]
    gaps = detect_gaps(bars)

    # Fetch anomaly detail
    bars_full = db.execute(
        "SELECT bar_time, open, high, low, close, volume "
        "FROM ohlcv_history WHERE symbol=? ORDER BY bar_time ASC",
        (symbol,)
    ).fetchall()
    anomalies = find_price_anomalies([dict(r) for r in bars_full])

    rec['gaps_detail']      = gaps[:10]    # show first 10
    rec['anomalies_detail'] = anomalies[:10]
    rec['command']          = 'scan_symbol'
    return rec


# ──────────────────────────────────────────────────────────────────────────────
#  Command: compute_breadth
# ──────────────────────────────────────────────────────────────────────────────

def cmd_compute_breadth(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Compute market breadth history across all trading dates.

    For each unique trading date in ohlcv_history:
      - count advancing (close > open) vs declining
      - compute cumulative A/D line
      - count symbols above 20-day and 50-day SMA
      - compute new 52-week highs/lows
    """
    computed_at = datetime.datetime.utcnow().isoformat()

    # All distinct trading dates
    date_rows = db.execute("""
        SELECT DISTINCT bar_time FROM ohlcv_history ORDER BY bar_time ASC
    """).fetchall()
    all_timestamps = [r['bar_time'] for r in date_rows]

    if not all_timestamps:
        return {
            'command':        'compute_breadth',
            'dates_computed': 0,
            'error':          'no data in ohlcv_history',
        }

    # Build a per-symbol price series: {symbol: [(ts, close), ...]}
    sym_series_raw = db.execute("""
        SELECT symbol, bar_time, open, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time ASC
    """).fetchall()

    # Group by symbol
    sym_data: Dict[str, List[Dict]] = {}
    for row in sym_series_raw:
        s = row['symbol']
        if s not in sym_data:
            sym_data[s] = []
        sym_data[s].append({
            'bar_time': row['bar_time'],
            'open':     row['open'],
            'close':    row['close'],
            'volume':   row['volume'],
        })

    # Pre-build index: symbol → {bar_time → bar_index}
    sym_ts_idx: Dict[str, Dict[int, int]] = {}
    for s, bars in sym_data.items():
        sym_ts_idx[s] = {b['bar_time']: i for i, b in enumerate(bars)}

    ad_line          = 0.0
    dates_computed   = 0
    all_vols: List[float] = []
    for b in sym_series_raw:
        if (b['volume'] or 0) > 0:
            all_vols.append(b['volume'])
    avg_vol_overall = sum(all_vols) / len(all_vols) if all_vols else 0.0

    # 52-week window in seconds
    week52_secs = 365 * SECONDS_PER_DAY

    rows_to_insert: List[Dict] = []

    for ts in all_timestamps:
        date_str   = ts_to_date(ts)
        advancing  = 0
        declining  = 0
        unchanged_ = 0
        above_20   = 0
        above_50   = 0
        vol_today: List[float] = []

        high_52w = 0
        low_52w  = 0

        for sym, bars in sym_data.items():
            idx_map = sym_ts_idx[sym]
            if ts not in idx_map:
                continue
            idx = idx_map[ts]
            bar = bars[idx]

            c = bar['close'] or 0
            o = bar['open']  or 0
            v = bar['volume'] or 0
            if v > 0:
                vol_today.append(v)

            if c > o:
                advancing += 1
            elif c < o:
                declining += 1
            else:
                unchanged_ += 1

            # SMA checks
            closes_up_to = [b['close'] for b in bars[:idx + 1] if (b['close'] or 0) > 0]
            n = len(closes_up_to)

            if n >= 20 and closes_up_to[-1] > (sum(closes_up_to[-20:]) / 20):
                above_20 += 1
            if n >= 50 and closes_up_to[-1] > (sum(closes_up_to[-50:]) / 50):
                above_50 += 1

            # 52-week high/low: find all closes in prior 52 weeks
            past_closes = [
                b['close'] for b in bars[:idx + 1]
                if (ts - b['bar_time']) <= week52_secs and (b['close'] or 0) > 0
            ]
            if past_closes and c > 0:
                if c >= max(past_closes):
                    high_52w += 1
                if c <= min(past_closes):
                    low_52w += 1

        total_active = advancing + declining + unchanged_
        breadth_ratio = (
            advancing / declining if declining > 0
            else float('inf') if advancing > 0 else 1.0
        )
        if math.isinf(breadth_ratio) or math.isnan(breadth_ratio):
            breadth_ratio = advancing if advancing > 0 else 1.0

        ad_line      += (advancing - declining)
        pct_positive  = int(advancing / total_active * 100) if total_active else 0
        avg_vol_day   = sum(vol_today) / len(vol_today) if vol_today else 0.0
        vol_ratio     = round(avg_vol_day / avg_vol_overall, 4) if avg_vol_overall else 1.0

        rows_to_insert.append({
            'date':                date_str,
            'n_advancing':         advancing,
            'n_declining':         declining,
            'n_unchanged':         unchanged_,
            'breadth_ratio':       round(breadth_ratio, 4),
            'advance_decline_line': round(ad_line, 2),
            'new_highs_52w':       high_52w,
            'new_lows_52w':        low_52w,
            'above_20ma':          above_20,
            'above_50ma':          above_50,
            'pct_positive':        pct_positive,
            'avg_volume':          round(avg_vol_day, 2),
            'volume_ratio':        vol_ratio,
            'computed_at':         computed_at,
        })
        dates_computed += 1

    # Bulk insert
    db.executemany("""
        INSERT OR REPLACE INTO market_breadth_history
            (date, n_advancing, n_declining, n_unchanged, breadth_ratio,
             advance_decline_line, new_highs_52w, new_lows_52w,
             above_20ma, above_50ma, pct_positive, avg_volume, volume_ratio, computed_at)
        VALUES
            (:date, :n_advancing, :n_declining, :n_unchanged, :breadth_ratio,
             :advance_decline_line, :new_highs_52w, :new_lows_52w,
             :above_20ma, :above_50ma, :pct_positive, :avg_volume, :volume_ratio, :computed_at)
    """, rows_to_insert)
    db.commit()

    # Determine A/D line trend (last 20 dates vs prior 20)
    ad_trend = 'neutral'
    if len(rows_to_insert) >= 40:
        last20  = [r['advance_decline_line'] for r in rows_to_insert[-20:]]
        prior20 = [r['advance_decline_line'] for r in rows_to_insert[-40:-20]]
        if last20[-1] > prior20[-1]:
            ad_trend = 'bullish'
        elif last20[-1] < prior20[-1]:
            ad_trend = 'bearish'

    latest = rows_to_insert[-1] if rows_to_insert else {}

    return {
        'command':              'compute_breadth',
        'dates_computed':       dates_computed,
        'latest_breadth_ratio': latest.get('breadth_ratio'),
        'latest_date':          latest.get('date'),
        'ad_line_current':      latest.get('advance_decline_line'),
        'ad_line_trend':        ad_trend,
        'pct_positive_latest':  latest.get('pct_positive'),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Command: get_report
# ──────────────────────────────────────────────────────────────────────────────

def cmd_get_report(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Full summary report from data_integrity table.

    Groups symbols by tier and surfaces worst/best performers.
    """
    # Get latest check per symbol
    rows = db.execute("""
        SELECT di.*
        FROM data_integrity di
        INNER JOIN (
            SELECT symbol, MAX(check_date) AS latest
            FROM data_integrity GROUP BY symbol
        ) latest_check ON di.symbol = latest_check.symbol
                      AND di.check_date = latest_check.latest
        ORDER BY di.integrity_score DESC
    """).fetchall()

    if not rows:
        return {
            'command':        'get_report',
            'total_symbols':  0,
            'message':        'no integrity data — run scan_all first',
        }

    all_recs = [dict(r) for r in rows]
    n        = len(all_recs)
    avg      = round(sum(r['integrity_score'] for r in all_recs) / n, 2)

    tier_dist: Dict[str, List[str]] = {
        'PREMIUM': [], 'STANDARD': [], 'LOW': [], 'UNRELIABLE': []
    }
    for r in all_recs:
        t = r['data_tier'] or 'UNRELIABLE'
        tier_dist.setdefault(t, []).append(r['symbol'])

    # Tier counts
    tier_count = {k: len(v) for k, v in tier_dist.items()}

    worst  = [{'symbol': r['symbol'], 'score': r['integrity_score'],
               'tier': r['data_tier'], 'notes': r['notes']}
              for r in all_recs[-10:]]
    best   = [{'symbol': r['symbol'], 'score': r['integrity_score'],
               'tier': r['data_tier']}
              for r in all_recs[:10]]

    return {
        'command':           'get_report',
        'total_symbols':     n,
        'avg_score':         avg,
        'tier_distribution': tier_count,
        'tier_symbols':      {k: v[:5] for k, v in tier_dist.items()},  # sample
        'best_symbols':      best,
        'worst_symbols':     worst,
        'report_generated':  datetime.datetime.utcnow().isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Command: get_confidence
# ──────────────────────────────────────────────────────────────────────────────

def cmd_get_confidence(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Return confidence adjustment penalties for all symbols.

    Format: {symbol: confidence_penalty (0–0.5)}
    A penalty of 0 means full confidence; 0.5 means max 50% discount.
    """
    rows = db.execute("""
        SELECT di.symbol, di.confidence_penalty, di.integrity_score, di.data_tier
        FROM data_integrity di
        INNER JOIN (
            SELECT symbol, MAX(check_date) AS latest
            FROM data_integrity GROUP BY symbol
        ) latest_check ON di.symbol = latest_check.symbol
                      AND di.check_date = latest_check.latest
        ORDER BY di.symbol
    """).fetchall()

    if not rows:
        return {
            'command': 'get_confidence',
            'message': 'no integrity data — run scan_all first',
            'penalties': {},
        }

    penalties = {r['symbol']: r['confidence_penalty'] for r in rows}
    scores    = {r['symbol']: r['integrity_score']    for r in rows}
    tiers     = {r['symbol']: r['data_tier']          for r in rows}

    avg_penalty = round(sum(penalties.values()) / len(penalties), 4) if penalties else 0.0

    return {
        'command':       'get_confidence',
        'n_symbols':     len(penalties),
        'avg_penalty':   avg_penalty,
        'penalties':     penalties,
        'scores':        scores,
        'tiers':         tiers,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Command: flag_anomalies
# ──────────────────────────────────────────────────────────────────────────────

def cmd_flag_anomalies(db: sqlite3.Connection, params: Dict) -> Dict:
    """
    Scan all symbols for price anomalies across entire history.

    Flags:
      - Daily returns > ±15 % (potential data errors or circuit-breaker halts)
      - Zero-volume bars with non-zero price change (ghost bars)
    """
    sym_rows = db.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history ORDER BY symbol"
    ).fetchall()
    symbols = [r['symbol'] for r in sym_rows]

    all_anomalies: List[Dict] = []
    n_symbols_with_anomalies  = 0

    for sym in symbols:
        try:
            bars_raw = db.execute(
                "SELECT bar_time, open, high, low, close, volume "
                "FROM ohlcv_history WHERE symbol=? ORDER BY bar_time ASC",
                (sym,)
            ).fetchall()
            bars = [dict(r) for r in bars_raw]
            anom = find_price_anomalies(bars)
            if anom:
                n_symbols_with_anomalies += 1
                for a in anom:
                    a['symbol'] = sym
                all_anomalies.extend(anom)
        except Exception as exc:
            all_anomalies.append({'symbol': sym, 'type': 'scan_error', 'error': str(exc)})

    # Sort by return magnitude for large_move anomalies
    large_moves = [a for a in all_anomalies if a.get('type') == 'large_move']
    large_moves.sort(key=lambda x: abs(x.get('return_pct', 0)), reverse=True)

    zero_vol = [a for a in all_anomalies if a.get('type') == 'zero_vol_price_change']

    return {
        'command':                   'flag_anomalies',
        'n_anomalies':               len(all_anomalies),
        'n_symbols_with_anomalies':  n_symbols_with_anomalies,
        'n_large_moves':             len(large_moves),
        'n_zero_vol_price_changes':  len(zero_vol),
        'anomaly_list':              (large_moves[:20] + zero_vol[:10]),
        'note': (
            'large_move: daily return >15% (may indicate data error or halt). '
            'zero_vol_price_change: bar moved with zero volume.'
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Main dispatcher
# ──────────────────────────────────────────────────────────────────────────────

COMMANDS = {
    'scan_all':       cmd_scan_all,
    'scan_symbol':    cmd_scan_symbol,
    'compute_breadth': cmd_compute_breadth,
    'get_report':     cmd_get_report,
    'get_confidence': cmd_get_confidence,
    'flag_anomalies': cmd_flag_anomalies,
}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({
            'error':    'command required',
            'usage':    'python historical_integrity_engine.py <command> [json_params]',
            'commands': list(COMMANDS.keys()),
        }, default=str))
        sys.exit(1)

    command = sys.argv[1].strip().lower()
    raw_params = sys.argv[2] if len(sys.argv) > 2 else '{}'

    try:
        params: Dict = json.loads(raw_params)
    except json.JSONDecodeError as exc:
        print(json.dumps({'error': f'invalid JSON params: {exc}', 'command': command}, default=str))
        sys.exit(1)

    if command not in COMMANDS:
        print(json.dumps({
            'error':     f'unknown command: {command}',
            'available': list(COMMANDS.keys()),
        }, default=str))
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(json.dumps({
            'error':   f'database not found at {DB_PATH}',
            'command': command,
        }, default=str))
        sys.exit(1)

    db     = get_db()
    result = {}

    try:
        result = COMMANDS[command](db, params)
    except Exception as exc:
        result = {
            'error':    str(exc),
            'command':  command,
            'type':     type(exc).__name__,
        }
    finally:
        try:
            db.close()
        except Exception:
            pass

    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
