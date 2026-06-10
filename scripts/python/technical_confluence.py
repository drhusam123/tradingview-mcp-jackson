#!/usr/bin/env python3
"""
technical_confluence.py — Phase 58: Technical Confluence Scoring
═════════════════════════════════════════════════════════════════
Reads technical indicator values (RSI, MACD, Bollinger Bands, EMA) fetched
by a JS script from TradingView and stored in technical_indicators_cache.
Computes a Technical Confirmation Score (0-100) for each scan result and
produces a confluence report joining fundamental scan grades with technical
confirmation.

The JS fetch script handles TradingView interaction; this module handles
storage, scoring, and reporting only.

Commands:
  save_indicators   — store raw indicator values, compute derived fields & score
  score_symbol      — return score breakdown for a single symbol+date
  score_batch       — score all cached symbols for a date
  confluence_report — join top scans with technical scores
  coverage          — cache coverage stats for a date
  build_full        — confluence_report + coverage in one call
"""

import os
import sys
import json
import math
import sqlite3
import collections
from datetime import datetime

# ── DB path ────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Zone / classification constants ───────────────────────────────────────
# EMA alignment
EMA_FULL_BULL = 'FULL_BULL'
EMA_BULL      = 'BULL'
EMA_NEUTRAL   = 'NEUTRAL'
EMA_BEAR      = 'BEAR'
EMA_FULL_BEAR = 'FULL_BEAR'

# RSI zones
RSI_OVERSOLD      = 'OVERSOLD'
RSI_NEUTRAL_BULL  = 'NEUTRAL_BULL'
RSI_NEUTRAL       = 'NEUTRAL'
RSI_NEUTRAL_BEAR  = 'NEUTRAL_BEAR'
RSI_OVERBOUGHT    = 'OVERBOUGHT'

# MACD zones
MACD_BULL_CROSS = 'BULL_CROSS'
MACD_BULL       = 'BULL'
MACD_BEAR_CROSS = 'BEAR_CROSS'
MACD_BEAR       = 'BEAR'

# BB zones
BB_SQUEEZE      = 'SQUEEZE'
BB_UPPER_BAND   = 'UPPER_BAND'
BB_LOWER_BAND   = 'LOWER_BAND'
BB_ABOVE_MIDDLE = 'ABOVE_MIDDLE'
BB_BELOW_MIDDLE = 'BELOW_MIDDLE'

# Tech signal thresholds
SIG_STRONG_BUY  = 'STRONG_BUY'
SIG_BUY         = 'BUY'
SIG_NEUTRAL     = 'NEUTRAL'
SIG_SELL        = 'SELL'
SIG_STRONG_SELL = 'STRONG_SELL'


# ══════════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════════

_DDL_TECHNICAL_CACHE = """
CREATE TABLE IF NOT EXISTS technical_indicators_cache (
    symbol           TEXT NOT NULL,
    fetch_date       TEXT NOT NULL,
    close_price      REAL,
    rsi_14           REAL,
    macd_value       REAL,
    macd_signal_line REAL,
    macd_histogram   REAL,
    bb_upper         REAL,
    bb_middle        REAL,
    bb_lower         REAL,
    bb_width_pct     REAL,
    ema_20           REAL,
    ema_50           REAL,
    ema_200          REAL,
    volume           REAL,
    volume_ma20      REAL,
    volume_ratio     REAL,
    ema_alignment    TEXT,
    rsi_zone         TEXT,
    macd_zone        TEXT,
    bb_zone          TEXT,
    tech_score       REAL,
    tech_signal      TEXT,
    fetched_at       TEXT,
    PRIMARY KEY (symbol, fetch_date)
)
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL_TECHNICAL_CACHE)
    conn.commit()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ══════════════════════════════════════════════════════════════════════════
# Derived-field helpers
# ══════════════════════════════════════════════════════════════════════════

def _safe_float(val, default=None):
    """Convert val to float, returning default on None/error."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _compute_bb_width_pct(bb_upper, bb_lower, bb_middle):
    """(upper - lower) / middle * 100, or None if inputs are missing/zero."""
    if None in (bb_upper, bb_lower, bb_middle) or bb_middle == 0:
        return None
    return (bb_upper - bb_lower) / bb_middle * 100.0


def _compute_volume_ratio(volume, volume_ma20):
    """volume / volume_ma20, or None if inputs are missing/zero."""
    if None in (volume, volume_ma20) or volume_ma20 == 0:
        return None
    return volume / volume_ma20


def _classify_ema_alignment(close, ema_20, ema_50, ema_200):
    """
    FULL_BULL : price > ema_20 > ema_50 > ema_200
    BULL      : price > ema_20 AND ema_20 > ema_50 (ema_200 may be missing/inverted)
    FULL_BEAR : price < ema_20 < ema_50 < ema_200
    BEAR      : price < ema_20 AND ema_20 < ema_50
    NEUTRAL   : everything else (mixed)
    """
    if None in (close, ema_20):
        return EMA_NEUTRAL

    above_20 = close > ema_20

    if ema_50 is not None and ema_200 is not None:
        if close > ema_20 > ema_50 > ema_200:
            return EMA_FULL_BULL
        if close < ema_20 < ema_50 < ema_200:
            return EMA_FULL_BEAR

    if ema_50 is not None:
        bull_stack = close > ema_20 and ema_20 > ema_50
        bear_stack = close < ema_20 and ema_20 < ema_50
        if bull_stack:
            return EMA_BULL
        if bear_stack:
            return EMA_BEAR

    if above_20:
        return EMA_BULL
    return EMA_BEAR


def _classify_rsi_zone(rsi):
    """
    <30           → OVERSOLD
    30 – <45      → NEUTRAL_BULL  (recovering from oversold)
    45 – <55      → NEUTRAL
    55 – <70      → NEUTRAL_BULL  (momentum building)
    ≥70           → OVERBOUGHT
    NOTE: spec maps both 30-45 and 55-70 to NEUTRAL_BULL; the 45-55 band
          is plain NEUTRAL. Values <45 closer to oversold territory are
          treated as NEUTRAL_BULL (recovery bias = bullish for entry).
          Values between 45-55 that are below 50 get NEUTRAL_BEAR.
    Revised per spec:
      30-45 = NEUTRAL_BULL, 45-55 split: 45-50 = NEUTRAL_BEAR?, 50-55 = NEUTRAL_BULL?
    The spec text says:
      30-45 = NEUTRAL_BULL (recovering)
      45-55 = NEUTRAL
      55-70 = NEUTRAL_BULL
    So we implement exactly that.
    """
    if rsi is None:
        return RSI_NEUTRAL
    if rsi < 30:
        return RSI_OVERSOLD
    if rsi < 45:
        return RSI_NEUTRAL_BULL
    if rsi < 55:
        return RSI_NEUTRAL
    if rsi < 70:
        return RSI_NEUTRAL_BULL
    return RSI_OVERBOUGHT


def _classify_macd_zone(macd_histogram, prev_histogram=None):
    """
    BULL_CROSS : histogram > 0 AND histogram > prev_histogram  (rising, crossed up)
    BULL       : histogram > 0
    BEAR_CROSS : histogram <= 0 AND histogram < prev_histogram (falling, crossed down)
    BEAR       : histogram <= 0

    When prev_histogram is None we fall back to sign-only classification.
    """
    if macd_histogram is None:
        return MACD_BEAR

    if macd_histogram > 0:
        if prev_histogram is not None and macd_histogram > prev_histogram:
            return MACD_BULL_CROSS
        return MACD_BULL
    else:
        if prev_histogram is not None and macd_histogram < prev_histogram:
            return MACD_BEAR_CROSS
        return MACD_BEAR


def _classify_bb_zone(close, bb_upper, bb_lower, bb_middle, bb_width_pct):
    """
    SQUEEZE      : bb_width_pct < 5
    UPPER_BAND   : price >= bb_upper
    LOWER_BAND   : price <= bb_lower
    ABOVE_MIDDLE : price > bb_middle
    BELOW_MIDDLE : price <= bb_middle
    """
    if close is None:
        return BB_BELOW_MIDDLE

    if bb_width_pct is not None and bb_width_pct < 5.0:
        return BB_SQUEEZE

    if bb_upper is not None and close >= bb_upper:
        return BB_UPPER_BAND

    if bb_lower is not None and close <= bb_lower:
        return BB_LOWER_BAND

    if bb_middle is not None:
        if close > bb_middle:
            return BB_ABOVE_MIDDLE
        return BB_BELOW_MIDDLE

    return BB_BELOW_MIDDLE


# ══════════════════════════════════════════════════════════════════════════
# Scoring
# ══════════════════════════════════════════════════════════════════════════

_RSI_SCORES = {
    RSI_OVERSOLD:     25,
    RSI_NEUTRAL_BULL: 20,
    RSI_NEUTRAL:      12,
    RSI_NEUTRAL_BEAR:  8,
    RSI_OVERBOUGHT:    5,
}

_MACD_SCORES = {
    MACD_BULL_CROSS: 25,
    MACD_BULL:       18,
    MACD_BEAR_CROSS:  8,
    MACD_BEAR:        5,
}

_EMA_SCORES = {
    EMA_FULL_BULL: 30,
    EMA_BULL:      22,
    EMA_NEUTRAL:   12,
    EMA_BEAR:       6,
    EMA_FULL_BEAR:  2,
}


def _volume_score(volume_ratio) -> int:
    if volume_ratio is None:
        return 5
    if volume_ratio > 2.0:
        return 20
    if volume_ratio > 1.5:
        return 16
    if volume_ratio > 1.0:
        return 12
    if volume_ratio > 0.7:
        return 8
    return 5


def _compute_tech_score(rsi_zone, macd_zone, ema_alignment, volume_ratio) -> float:
    rsi_c    = _RSI_SCORES.get(rsi_zone, 12)
    macd_c   = _MACD_SCORES.get(macd_zone, 5)
    ema_c    = _EMA_SCORES.get(ema_alignment, 12)
    vol_c    = _volume_score(volume_ratio)
    return float(rsi_c + macd_c + ema_c + vol_c)


def _compute_tech_signal(score: float) -> str:
    if score >= 75:
        return SIG_STRONG_BUY
    if score >= 60:
        return SIG_BUY
    if score >= 45:
        return SIG_NEUTRAL
    if score >= 30:
        return SIG_SELL
    return SIG_STRONG_SELL


# ══════════════════════════════════════════════════════════════════════════
# Command: save_indicators
# ══════════════════════════════════════════════════════════════════════════

def save_indicators(params: dict) -> dict:
    """
    Upsert raw indicator values + all derived fields into technical_indicators_cache.

    Required params:
      symbol      : str
      fetch_date  : str  (YYYY-MM-DD)
      indicators  : dict  (see schema for keys)

    Optional indicators keys:
      rsi_14, macd_value, macd_signal_line, macd_histogram,
      bb_upper, bb_middle, bb_lower,
      ema_20, ema_50, ema_200,
      close_price, volume, volume_ma20
    """
    symbol     = params.get('symbol', '').strip().upper()
    fetch_date = params.get('fetch_date', '').strip()
    raw        = params.get('indicators', {})

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}
    if not fetch_date:
        return {'success': False, 'error': 'fetch_date is required'}

    # ── Parse raw indicator values ────────────────────────────────────────
    close_price      = _safe_float(raw.get('close_price'))
    rsi_14           = _safe_float(raw.get('rsi_14'))
    macd_value       = _safe_float(raw.get('macd_value'))
    macd_signal_line = _safe_float(raw.get('macd_signal_line'))
    macd_histogram   = _safe_float(raw.get('macd_histogram'))
    bb_upper         = _safe_float(raw.get('bb_upper'))
    bb_middle        = _safe_float(raw.get('bb_middle'))
    bb_lower         = _safe_float(raw.get('bb_lower'))
    ema_20           = _safe_float(raw.get('ema_20'))
    ema_50           = _safe_float(raw.get('ema_50'))
    ema_200          = _safe_float(raw.get('ema_200'))
    volume           = _safe_float(raw.get('volume'))
    volume_ma20      = _safe_float(raw.get('volume_ma20'))

    # ── Derived fields ────────────────────────────────────────────────────
    bb_width_pct  = _compute_bb_width_pct(bb_upper, bb_lower, bb_middle)
    volume_ratio  = _compute_volume_ratio(volume, volume_ma20)
    ema_alignment = _classify_ema_alignment(close_price, ema_20, ema_50, ema_200)
    rsi_zone      = _classify_rsi_zone(rsi_14)
    macd_zone     = _classify_macd_zone(macd_histogram)
    bb_zone       = _classify_bb_zone(close_price, bb_upper, bb_lower, bb_middle, bb_width_pct)

    tech_score  = _compute_tech_score(rsi_zone, macd_zone, ema_alignment, volume_ratio)
    tech_signal = _compute_tech_signal(tech_score)
    fetched_at  = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    record = {
        'symbol':           symbol,
        'fetch_date':       fetch_date,
        'close_price':      close_price,
        'rsi_14':           rsi_14,
        'macd_value':       macd_value,
        'macd_signal_line': macd_signal_line,
        'macd_histogram':   macd_histogram,
        'bb_upper':         bb_upper,
        'bb_middle':        bb_middle,
        'bb_lower':         bb_lower,
        'bb_width_pct':     bb_width_pct,
        'ema_20':           ema_20,
        'ema_50':           ema_50,
        'ema_200':          ema_200,
        'volume':           volume,
        'volume_ma20':      volume_ma20,
        'volume_ratio':     volume_ratio,
        'ema_alignment':    ema_alignment,
        'rsi_zone':         rsi_zone,
        'macd_zone':        macd_zone,
        'bb_zone':          bb_zone,
        'tech_score':       tech_score,
        'tech_signal':      tech_signal,
        'fetched_at':       fetched_at,
    }

    conn = get_db()
    try:
        _ensure_schema(conn)
        conn.execute("""
            INSERT INTO technical_indicators_cache (
                symbol, fetch_date, close_price,
                rsi_14, macd_value, macd_signal_line, macd_histogram,
                bb_upper, bb_middle, bb_lower, bb_width_pct,
                ema_20, ema_50, ema_200,
                volume, volume_ma20, volume_ratio,
                ema_alignment, rsi_zone, macd_zone, bb_zone,
                tech_score, tech_signal, fetched_at
            ) VALUES (
                :symbol, :fetch_date, :close_price,
                :rsi_14, :macd_value, :macd_signal_line, :macd_histogram,
                :bb_upper, :bb_middle, :bb_lower, :bb_width_pct,
                :ema_20, :ema_50, :ema_200,
                :volume, :volume_ma20, :volume_ratio,
                :ema_alignment, :rsi_zone, :macd_zone, :bb_zone,
                :tech_score, :tech_signal, :fetched_at
            )
            ON CONFLICT(symbol, fetch_date) DO UPDATE SET
                close_price      = excluded.close_price,
                rsi_14           = excluded.rsi_14,
                macd_value       = excluded.macd_value,
                macd_signal_line = excluded.macd_signal_line,
                macd_histogram   = excluded.macd_histogram,
                bb_upper         = excluded.bb_upper,
                bb_middle        = excluded.bb_middle,
                bb_lower         = excluded.bb_lower,
                bb_width_pct     = excluded.bb_width_pct,
                ema_20           = excluded.ema_20,
                ema_50           = excluded.ema_50,
                ema_200          = excluded.ema_200,
                volume           = excluded.volume,
                volume_ma20      = excluded.volume_ma20,
                volume_ratio     = excluded.volume_ratio,
                ema_alignment    = excluded.ema_alignment,
                rsi_zone         = excluded.rsi_zone,
                macd_zone        = excluded.macd_zone,
                bb_zone          = excluded.bb_zone,
                tech_score       = excluded.tech_score,
                tech_signal      = excluded.tech_signal,
                fetched_at       = excluded.fetched_at
        """, record)
        conn.commit()
    finally:
        conn.close()

    return {'success': True, 'record': record}


# ══════════════════════════════════════════════════════════════════════════
# Command: score_symbol
# ══════════════════════════════════════════════════════════════════════════

def score_symbol(params: dict) -> dict:
    """
    Return the tech score breakdown for a single symbol on a date.

    Required params: symbol, date
    """
    symbol = params.get('symbol', '').strip().upper()
    date   = params.get('date', '').strip()

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}
    if not date:
        return {'success': False, 'error': 'date is required'}

    conn = get_db()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM technical_indicators_cache WHERE symbol=? AND fetch_date=?",
            (symbol, date)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            'success': False,
            'error': f'No cached indicators for {symbol} on {date}',
        }

    r = _row_to_dict(row)

    rsi_contrib    = _RSI_SCORES.get(r.get('rsi_zone'), 12)
    macd_contrib   = _MACD_SCORES.get(r.get('macd_zone'), 5)
    ema_contrib    = _EMA_SCORES.get(r.get('ema_alignment'), 12)
    volume_contrib = _volume_score(r.get('volume_ratio'))

    return {
        'success':       True,
        'symbol':        symbol,
        'date':          date,
        'tech_score':    r.get('tech_score'),
        'tech_signal':   r.get('tech_signal'),
        'ema_alignment': r.get('ema_alignment'),
        'rsi_zone':      r.get('rsi_zone'),
        'macd_zone':     r.get('macd_zone'),
        'bb_zone':       r.get('bb_zone'),
        'volume_ratio':  r.get('volume_ratio'),
        'breakdown': {
            'rsi_contrib':    rsi_contrib,
            'macd_contrib':   macd_contrib,
            'ema_contrib':    ema_contrib,
            'volume_contrib': volume_contrib,
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# Command: score_batch
# ══════════════════════════════════════════════════════════════════════════

def score_batch(params: dict) -> dict:
    """
    Score all (or a subset of) symbols with cached data for a given date.

    Required params: date
    Optional params: symbols (list of str) — if omitted, all cached symbols for date
    """
    date    = params.get('date', '').strip()
    symbols = params.get('symbols')

    if not date:
        return {'success': False, 'error': 'date is required'}

    conn = get_db()
    try:
        _ensure_schema(conn)
        if symbols:
            placeholders = ','.join('?' * len(symbols))
            upper_syms   = [s.strip().upper() for s in symbols]
            rows = conn.execute(
                f"SELECT * FROM technical_indicators_cache "
                f"WHERE fetch_date=? AND symbol IN ({placeholders})",
                [date] + upper_syms
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM technical_indicators_cache WHERE fetch_date=?",
                (date,)
            ).fetchall()
    finally:
        conn.close()

    results = []
    signal_counts = collections.Counter()

    for row in rows:
        r      = _row_to_dict(row)
        score  = r.get('tech_score')
        signal = r.get('tech_signal', SIG_NEUTRAL)
        results.append({
            'symbol':        r['symbol'],
            'tech_score':    score,
            'tech_signal':   signal,
            'ema_alignment': r.get('ema_alignment'),
            'rsi_zone':      r.get('rsi_zone'),
            'macd_zone':     r.get('macd_zone'),
            'bb_zone':       r.get('bb_zone'),
        })
        signal_counts[signal] += 1

    results.sort(key=lambda x: (x['tech_score'] or 0), reverse=True)

    scores = [x['tech_score'] for x in results if x['tech_score'] is not None]
    summary = {
        'n_scored':      len(results),
        'avg_score':     round(sum(scores) / len(scores), 2) if scores else None,
        'max_score':     max(scores) if scores else None,
        'min_score':     min(scores) if scores else None,
        'signal_counts': dict(signal_counts),
    }

    return {
        'success': True,
        'date':    date,
        'results': results,
        'summary': summary,
    }


# ══════════════════════════════════════════════════════════════════════════
# Command: confluence_report
# ══════════════════════════════════════════════════════════════════════════

def confluence_report(params: dict) -> dict:
    """
    Join top scans with technical scores for a given date.

    Required params: scan_date
    Optional params: min_score (default 65)

    Returns:
      top_picks          — all scan symbols with tech data, sorted by combined_score
      strongly_confirmed — scan_score>70 AND tech_score>65
      contradicted       — scan grade bullish but tech signal bearish
      n_with_tech_data
      n_without_tech_data
    """
    scan_date = params.get('scan_date', '').strip()
    min_score = float(params.get('min_score', 65))

    if not scan_date:
        return {'success': False, 'error': 'scan_date is required'}

    conn = get_db()
    try:
        _ensure_schema(conn)

        # Fetch all scans for date
        scan_rows = conn.execute(
            "SELECT * FROM scans WHERE scan_date=?", (scan_date,)
        ).fetchall()

        # Fetch all technical cache rows for date
        tech_rows = conn.execute(
            "SELECT * FROM technical_indicators_cache WHERE fetch_date=?",
            (scan_date,)
        ).fetchall()
    finally:
        conn.close()

    # Index tech data by symbol
    tech_by_symbol = {_row_to_dict(r)['symbol']: _row_to_dict(r) for r in tech_rows}

    top_picks          = []
    strongly_confirmed = []
    contradicted       = []
    n_with             = 0
    n_without          = 0

    bearish_signals = {SIG_SELL, SIG_STRONG_SELL}

    for scan_row in scan_rows:
        s = _row_to_dict(scan_row)
        symbol     = s.get('symbol', '').upper()
        scan_score = _safe_float(s.get('score'), 0.0)

        tech = tech_by_symbol.get(symbol)
        if tech is None:
            n_without += 1
            continue

        n_with += 1
        tech_score      = _safe_float(tech.get('tech_score'), 0.0)
        tech_signal     = tech.get('tech_signal', SIG_NEUTRAL)
        ema_alignment   = tech.get('ema_alignment')
        combined_score  = round(0.6 * scan_score + 0.4 * tech_score, 2)

        entry = {
            'symbol':         symbol,
            'scan_score':     scan_score,
            'tech_score':     tech_score,
            'combined_score': combined_score,
            'tech_signal':    tech_signal,
            'ema_alignment':  ema_alignment,
            'grade':          s.get('grade'),
            'close_price':    s.get('close_price'),
            'entry_low':      s.get('entry_low'),
            'entry_high':     s.get('entry_high'),
            'stop_loss':      s.get('stop_loss'),
        }

        if combined_score >= min_score:
            top_picks.append(entry)

        if scan_score > 70 and tech_score > 65:
            strongly_confirmed.append(entry)

        # Contradicted: scan has a bullish grade and tech is bearish
        grade = (s.get('grade') or '').upper()
        bullish_grades = {'A', 'A+', 'A-', 'B', 'B+', 'BUY', 'STRONG_BUY',
                          'STRONG BUY', 'BULLISH'}
        if grade in bullish_grades and tech_signal in bearish_signals:
            contradicted.append(entry)

    top_picks.sort(key=lambda x: x['combined_score'], reverse=True)
    strongly_confirmed.sort(key=lambda x: x['combined_score'], reverse=True)

    return {
        'success':            True,
        'scan_date':          scan_date,
        'min_score_filter':   min_score,
        'top_picks':          top_picks,
        'strongly_confirmed': strongly_confirmed,
        'contradicted':       contradicted,
        'n_with_tech_data':   n_with,
        'n_without_tech_data': n_without,
    }


# ══════════════════════════════════════════════════════════════════════════
# Command: coverage
# ══════════════════════════════════════════════════════════════════════════

def coverage(params: dict) -> dict:
    """
    Cache coverage stats for a given date.

    Required params: date
    """
    date = params.get('date', '').strip()
    if not date:
        return {'success': False, 'error': 'date is required'}

    conn = get_db()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT symbol, fetched_at FROM technical_indicators_cache WHERE fetch_date=? ORDER BY symbol",
            (date,)
        ).fetchall()

        # oldest and newest across all cached dates
        bounds = conn.execute(
            "SELECT MIN(fetch_date) AS oldest, MAX(fetch_date) AS newest "
            "FROM technical_indicators_cache"
        ).fetchone()
    finally:
        conn.close()

    symbols_cached = [r['symbol'] for r in rows]
    fetched_ats    = [r['fetched_at'] for r in rows if r['fetched_at']]

    return {
        'success':         True,
        'date':            date,
        'n_cached':        len(symbols_cached),
        'symbols_cached':  symbols_cached,
        'oldest_cache':    bounds['oldest'] if bounds else None,
        'newest_cache':    bounds['newest'] if bounds else None,
        'earliest_fetch':  min(fetched_ats) if fetched_ats else None,
        'latest_fetch':    max(fetched_ats) if fetched_ats else None,
    }


# ══════════════════════════════════════════════════════════════════════════
# Command: build_full
# ══════════════════════════════════════════════════════════════════════════

def build_full(params: dict) -> dict:
    """
    Comprehensive report: confluence_report + coverage in one call.

    Required params: date (used as both scan_date and fetch_date)
    Optional params: min_score (default 65)
    """
    date      = params.get('date', '').strip()
    min_score = params.get('min_score', 65)

    if not date:
        return {'success': False, 'error': 'date is required'}

    confluence = confluence_report({'scan_date': date, 'min_score': min_score})
    cov        = coverage({'date': date})

    return {
        'success':    True,
        'date':       date,
        'confluence': confluence,
        'coverage':   cov,
    }


# ══════════════════════════════════════════════════════════════════════════
# Command dispatch
# ══════════════════════════════════════════════════════════════════════════

COMMANDS = {
    'save_indicators':   save_indicators,
    'score_symbol':      score_symbol,
    'score_batch':       score_batch,
    'confluence_report': confluence_report,
    'coverage':          coverage,
    'build_full':        build_full,
}


def main():
    cmd    = sys.argv[1].strip() if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if not cmd:
        print(json.dumps({'success': False, 'error': 'No command provided', 'available_commands': list(COMMANDS.keys())}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error':   f"Unknown command: '{cmd}'",
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
    except Exception as e:
        import traceback
        result = {
            'success':   False,
            'error':     str(e),
            'traceback': traceback.format_exc(),
        }

    print(json.dumps(result))


if __name__ == '__main__':
    main()
