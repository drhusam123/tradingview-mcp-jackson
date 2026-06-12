"""
anti_laws_engine.py — Phase 35
EGX Autonomous Quant System: Anti-Laws Engine

Key Insight: "Failure in markets is more stable than success."
This engine extracts LAWS OF FAILURE (Anti-Laws) from historical data.
Knowing when NOT to trade is as powerful as knowing when to trade.

Commands:
  extract_anti_laws  — scan all historical data, extract failure patterns as Anti-Laws
  build_library      — build/update the full anti-law library with rankings
  scan_symbol        — check if any anti-law is active for one symbol today
  daily_scan         — scan all symbols for anti-laws today
  anti_law_report    — detailed report of current anti-law landscape
  build_full         — extract + library + daily_scan + report in one pass

Usage:
  python anti_laws_engine.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# Anti-Law Taxonomy
# ---------------------------------------------------------------------------
ANTI_LAW_TYPES = {
    'VOLUME_TRAP':           'High volume spike without follow-through — fakeout',
    'FALSE_BREAKOUT':        'Price breaks level then immediately reverses',
    'FAILED_CONTAGION':      'Sector spread started then stopped suddenly',
    'CAUSAL_INVERSION':      'Causal relationship reversed direction',
    'REGIME_OVERRIDE':       'Good signal in wrong regime context',
    'LIQUIDITY_WITHDRAWAL':  'Sudden liquidity dry-up after signal',
    'POST_CATALYST_FADE':    'Reversal after positive news/event',
    'BREADTH_DIVERGENCE':    'Stock rising but sector declining',
    'LAW_DEGRADATION_TRAP':  'Signal from a degrading/dead law',
    'EXPLOSION_FAKEOUT':     'High explosion readiness score but no follow-through',
    'CROWDING_REVERSAL':     '>60% of recent trades in same direction — crowded trade reversal risk',
    'NEWS_OVERREACTION':     'Price moved >3% on low-impact catalyst — mean-reversion likely',
    'EXHAUSTION_REVERSAL':   '5+ consecutive sessions same direction with declining volume',
    'VOLATILITY_FAKE_RELEASE': 'Vol spike recovered in <2 sessions — fake-out precedes larger move',
    'POST_GAP_FAILURE':      'Gap >2% at open failed to hold by session end — strong reversal signal',
}

SEVERITY_SCORE = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

TODAY = datetime.now().strftime('%Y-%m-%d')

# ---------------------------------------------------------------------------
# DB Connection
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS anti_laws (
        anti_law_id     TEXT PRIMARY KEY,
        anti_law_type   TEXT,
        symbol          TEXT,
        anti_precision  REAL,
        frequency       INTEGER,
        avg_loss        REAL,
        severity        TEXT,
        is_veto         INTEGER,
        description     TEXT,
        extracted_at    TEXT
    );

    CREATE TABLE IF NOT EXISTS anti_law_daily_scan (
        symbol              TEXT,
        date                TEXT,
        triggered_types     TEXT,
        n_triggered         INTEGER,
        anti_law_veto       INTEGER,
        safety_level        TEXT,
        strongest_anti_law  TEXT,
        computed_at         TEXT,
        PRIMARY KEY (symbol, date)
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Safe DB Read Helpers
# ---------------------------------------------------------------------------

def _fetch_ohlcv(db):
    """Return list of dicts: symbol, date, close, volume."""
    try:
        rows = db.execute(
            "SELECT symbol, date(bar_time,'unixepoch') AS date, close, volume "
            "FROM ohlcv_history_execution ORDER BY symbol, date(bar_time,'unixepoch')"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_failure_intelligence(db):
    try:
        rows = db.execute(
            "SELECT symbol, archetype, confidence, date FROM failure_intelligence"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_pattern_laws(db):
    try:
        rows = db.execute(
            "SELECT pattern_name, precision, status, last_validated FROM pattern_laws"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_explosion_readiness(db):
    try:
        rows = db.execute(
            "SELECT symbol, readiness_score, date FROM explosion_readiness ORDER BY symbol, date"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_stock_dna(db):
    try:
        rows = db.execute(
            "SELECT symbol, sector, archetype, energy_score FROM stock_dna"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_regime(db):
    """Try market_regime then regime_history."""
    for table in ('market_regime', 'regime_history'):
        try:
            rows = db.execute(
                f"SELECT date, regime_label FROM {table} ORDER BY date"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            continue
    return []


def _fetch_causal_edges(db):
    """Try causal_edges then causal_chains."""
    for table in ('causal_edges', 'causal_chains'):
        try:
            rows = db.execute(
                f"SELECT source, target, strength, date FROM {table}"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            continue
    return []


def _fetch_liquidity_profiles(db):
    try:
        rows = db.execute(
            "SELECT symbol, tier, avg_daily_volume FROM liquidity_profiles"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def _group_by_symbol(rows, key='symbol'):
    """Group list of dicts by a key field."""
    grouped = defaultdict(list)
    for r in rows:
        grouped[r[key]].append(r)
    return grouped


def _compute_rolling_avg(values, window=20):
    """Return list of rolling averages same length as values; early entries use available data."""
    result = []
    for i, v in enumerate(values):
        start = max(0, i - window)
        window_vals = [x for x in values[start:i] if x is not None]
        result.append(statistics.mean(window_vals) if window_vals else 0.0)
    return result


def _safe_return(prices, i, n_days):
    """Compute return over next n_days from index i."""
    if i + n_days < len(prices) and prices[i] and prices[i] > 0:
        future = prices[i + n_days]
        if future and future > 0:
            return (future - prices[i]) / prices[i]
    return None


def _avg_return_range(prices, i, start_offset, end_offset):
    """Mean return across days [i+start_offset .. i+end_offset]."""
    rets = []
    for d in range(start_offset, end_offset + 1):
        r = _safe_return(prices, i, d)
        if r is not None:
            rets.append(r)
    return statistics.mean(rets) if rets else None


def _severity_from_loss(avg_loss_abs):
    if avg_loss_abs > 0.03:
        return 'HIGH'
    elif avg_loss_abs > 0.01:
        return 'MEDIUM'
    return 'LOW'


def _build_anti_law_record(anti_law_type, symbol, n_failure, n_total, losses, extracted_at):
    if n_total == 0:
        return None
    anti_precision = n_failure / n_total
    frequency = n_total
    avg_loss = statistics.mean(losses) if losses else 0.0
    severity = _severity_from_loss(abs(avg_loss))
    # Lower veto threshold: 60% failure rate with 3+ instances = veto
    # Previously 70%+5 was too conservative, missing real danger patterns
    is_veto = int(
        (anti_precision > 0.70 and frequency >= 3) or    # high precision, few samples
        (anti_precision > 0.60 and frequency >= 5) or    # moderate precision, more samples
        (anti_precision > 0.55 and frequency >= 10 and severity == 'HIGH')  # pattern with many high-loss instances
    )
    law_id = f"{anti_law_type}_{symbol}" if symbol else f"{anti_law_type}_GLOBAL"
    desc = ANTI_LAW_TYPES[anti_law_type]
    return {
        'anti_law_id':    law_id,
        'anti_law_type':  anti_law_type,
        'symbol':         symbol,
        'anti_precision': round(anti_precision, 4),
        'frequency':      frequency,
        'avg_loss':       round(avg_loss, 4),
        'severity':       severity,
        'is_veto':        is_veto,
        'description':    desc,
        'extracted_at':   extracted_at,
    }


def _upsert_anti_law(db, record):
    db.execute("""
        INSERT INTO anti_laws
            (anti_law_id, anti_law_type, symbol, anti_precision, frequency,
             avg_loss, severity, is_veto, description, extracted_at)
        VALUES
            (:anti_law_id, :anti_law_type, :symbol, :anti_precision, :frequency,
             :avg_loss, :severity, :is_veto, :description, :extracted_at)
        ON CONFLICT(anti_law_id) DO UPDATE SET
            anti_precision = excluded.anti_precision,
            frequency      = excluded.frequency,
            avg_loss       = excluded.avg_loss,
            severity       = excluded.severity,
            is_veto        = excluded.is_veto,
            description    = excluded.description,
            extracted_at   = excluded.extracted_at
    """, record)


# ---------------------------------------------------------------------------
# Detector: VOLUME_TRAP
# ---------------------------------------------------------------------------

def _detect_volume_trap(ohlcv_rows, extracted_at):
    """High volume spike (>3x 20-day avg) with price reversal in next 5 days."""
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        volumes = [r['volume'] or 0 for r in rows]
        closes  = [r['close']  or 0 for r in rows]
        roll_avg = _compute_rolling_avg(volumes, window=20)
        n_spike   = 0
        n_failure = 0
        losses    = []
        for i in range(20, len(rows)):
            avg_vol = roll_avg[i]
            if avg_vol <= 0:
                continue
            if volumes[i] > 3 * avg_vol:
                n_spike += 1
                ret = _avg_return_range(closes, i, 1, 5)
                if ret is not None and ret < -0.02:
                    n_failure += 1
                    losses.append(ret)
        rec = _build_anti_law_record('VOLUME_TRAP', sym, n_failure, n_spike, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: FALSE_BREAKOUT
# ---------------------------------------------------------------------------

def _detect_false_breakout(ohlcv_rows, extracted_at):
    """Price breaks 20-day high then reverses below breakout level within 1-3 days."""
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        closes = [r['close'] or 0 for r in rows]
        n_breakout = 0
        n_false    = 0
        losses     = []
        for i in range(20, len(rows)):
            prior_high = max(closes[i-20:i]) if closes[i-20:i] else 0
            if prior_high <= 0:
                continue
            if closes[i] > prior_high:
                n_breakout += 1
                breakout_level = prior_high
                reverted = False
                ret = None
                for d in range(1, 4):
                    if i + d < len(closes):
                        if closes[i + d] < breakout_level:
                            reverted = True
                            if closes[i] > 0:
                                ret = (closes[i + d] - closes[i]) / closes[i]
                            break
                if reverted:
                    n_false += 1
                    if ret is not None:
                        losses.append(ret)
        rec = _build_anti_law_record('FALSE_BREAKOUT', sym, n_false, n_breakout, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: BREADTH_DIVERGENCE
# ---------------------------------------------------------------------------

def _detect_breadth_divergence(ohlcv_rows, stock_dna_rows, extracted_at):
    """Stock up but sector avg return < -1%; check reversal in next 5 days."""
    dna_map = {r['symbol']: r for r in stock_dna_rows}
    by_sym  = _group_by_symbol(ohlcv_rows)

    # Build sector daily returns
    sector_daily = defaultdict(lambda: defaultdict(list))
    for sym, rows in by_sym.items():
        sector = dna_map.get(sym, {}).get('sector')
        if not sector:
            continue
        rows_s = sorted(rows, key=lambda r: r['date'])
        for i in range(1, len(rows_s)):
            c0 = rows_s[i-1]['close'] or 0
            c1 = rows_s[i]['close']   or 0
            if c0 > 0:
                sector_daily[sector][rows_s[i]['date']].append((c1 - c0) / c0)

    sector_avg = {sec: {dt: statistics.mean(rets) for dt, rets in date_map.items()}
                  for sec, date_map in sector_daily.items()}

    records = []
    for sym, rows in by_sym.items():
        sector = dna_map.get(sym, {}).get('sector')
        if not sector or sector not in sector_avg:
            continue
        rows_s = sorted(rows, key=lambda r: r['date'])
        closes  = [r['close'] or 0 for r in rows_s]
        dates   = [r['date'] for r in rows_s]
        n_div   = 0
        n_rev   = 0
        losses  = []
        for i in range(1, len(rows_s)):
            c0  = closes[i-1]
            c1  = closes[i]
            dt  = dates[i]
            if c0 <= 0:
                continue
            sym_ret    = (c1 - c0) / c0
            sec_ret    = sector_avg[sector].get(dt, 0)
            if sym_ret > 0 and sec_ret < -0.01:
                n_div += 1
                ret = _avg_return_range(closes, i, 1, 5)
                if ret is not None and ret < 0:
                    n_rev += 1
                    losses.append(ret)
        rec = _build_anti_law_record('BREADTH_DIVERGENCE', sym, n_rev, n_div, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: REGIME_OVERRIDE
# ---------------------------------------------------------------------------

def _detect_regime_override(pattern_laws_rows, regime_rows, ohlcv_rows, extracted_at):
    """Active law signal fired in VOLATILE/TRANSITION regime → loss."""
    bad_regimes = {'VOLATILE', 'TRANSITION', 'volatile', 'transition'}
    regime_map  = {r['date']: r['regime_label'] for r in regime_rows}
    by_sym = _group_by_symbol(ohlcv_rows)

    active_laws = [l for l in pattern_laws_rows
                   if (l.get('status') or '').upper() == 'ACTIVE'
                   and (l.get('precision') or 0) > 0.5]

    if not active_laws or not regime_map:
        return []

    n_wrong  = 0
    n_loss   = 0
    losses   = []

    # For each symbol, check each date where regime is wrong
    for sym, rows in by_sym.items():
        rows_s = sorted(rows, key=lambda r: r['date'])
        closes = [r['close'] or 0 for r in rows_s]
        dates  = [r['date'] for r in rows_s]
        for i, dt in enumerate(dates):
            regime = regime_map.get(dt, '')
            if regime in bad_regimes:
                # Assume a signal could fire on any day (simplified: use active law count as proxy)
                for law in active_laws:
                    n_wrong += 1
                    ret = _avg_return_range(closes, i, 1, 5)
                    if ret is not None and ret < 0:
                        n_loss += 1
                        losses.append(ret)
                break  # one count per symbol per day is enough to avoid explosion

    rec = _build_anti_law_record('REGIME_OVERRIDE', None, n_loss, n_wrong, losses, extracted_at)
    return [rec] if rec and rec['frequency'] >= 1 else []


# ---------------------------------------------------------------------------
# Detector: LAW_DEGRADATION_TRAP
# ---------------------------------------------------------------------------

def _detect_law_degradation_trap(pattern_laws_rows, ohlcv_rows, extracted_at):
    """Signals from DEGRADING laws lead to losses."""
    degrading = [l for l in pattern_laws_rows
                 if (l.get('status') or '').upper() == 'DEGRADING']
    if not degrading:
        return []

    by_sym = _group_by_symbol(ohlcv_rows)
    n_total = 0
    n_loss  = 0
    losses  = []

    for sym, rows in by_sym.items():
        rows_s = sorted(rows, key=lambda r: r['date'])
        closes = [r['close'] or 0 for r in rows_s]
        # Each degrading law could have fired once per symbol per last_validated date
        for law in degrading:
            lv_date = law.get('last_validated') or ''
            # Find the row index closest to last_validated
            idx = None
            for i, r in enumerate(rows_s):
                if r['date'] >= lv_date:
                    idx = i
                    break
            if idx is None:
                continue
            n_total += 1
            ret = _avg_return_range(closes, idx, 1, 5)
            if ret is not None and ret < 0:
                n_loss += 1
                losses.append(ret)

    rec = _build_anti_law_record('LAW_DEGRADATION_TRAP', None, n_loss, n_total, losses, extracted_at)
    return [rec] if rec and rec['frequency'] >= 1 else []


# ---------------------------------------------------------------------------
# Detector: EXPLOSION_FAKEOUT
# ---------------------------------------------------------------------------

def _detect_explosion_fakeout(explosion_rows, ohlcv_rows, extracted_at):
    """High readiness_score (>70) but price flat/down in next 10 days."""
    by_sym_exp  = _group_by_symbol(explosion_rows)
    by_sym_ohlc = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, exp_rows in by_sym_exp.items():
        ohlc = by_sym_ohlc.get(sym, [])
        if not ohlc:
            continue
        ohlc_s  = sorted(ohlc, key=lambda r: r['date'])
        closes  = [r['close'] or 0 for r in ohlc_s]
        dates_o = [r['date'] for r in ohlc_s]
        date_idx = {dt: i for i, dt in enumerate(dates_o)}

        n_high    = 0
        n_fakeout = 0
        losses    = []
        for exp in exp_rows:
            score = exp.get('readiness_score') or 0
            if score > 70:
                n_high += 1
                idx = date_idx.get(exp['date'])
                if idx is None:
                    continue
                ret = _avg_return_range(closes, idx, 1, 10)
                if ret is not None and ret < 0:
                    n_fakeout += 1
                    losses.append(ret)
        rec = _build_anti_law_record('EXPLOSION_FAKEOUT', sym, n_fakeout, n_high, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: FAILED_CONTAGION (market-wide)
# ---------------------------------------------------------------------------

def _detect_failed_contagion(ohlcv_rows, stock_dna_rows, extracted_at):
    """Sector spread started (>30% of sector up 2%+) then stopped in 1-2 days."""
    dna_map = {r['symbol']: r for r in stock_dna_rows}
    by_sym  = _group_by_symbol(ohlcv_rows)

    # Build sector-date-returns
    sector_daily = defaultdict(lambda: defaultdict(list))
    for sym, rows in by_sym.items():
        sector = dna_map.get(sym, {}).get('sector')
        if not sector:
            continue
        rows_s = sorted(rows, key=lambda r: r['date'])
        for i in range(1, len(rows_s)):
            c0 = rows_s[i-1]['close'] or 0
            c1 = rows_s[i]['close']   or 0
            if c0 > 0:
                sector_daily[sector][rows_s[i]['date']].append((c1 - c0) / c0)

    all_dates = sorted({r['date'] for r in ohlcv_rows})
    n_spread  = 0
    n_failed  = 0
    losses    = []

    for sector, date_map in sector_daily.items():
        sorted_dates = sorted(date_map.keys())
        for idx, dt in enumerate(sorted_dates[:-2]):
            rets = date_map[dt]
            pct_strong = sum(1 for r in rets if r > 0.02) / len(rets) if rets else 0
            if pct_strong >= 0.30:
                n_spread += 1
                dt2 = sorted_dates[idx + 1]
                rets2 = date_map.get(dt2, [])
                pct_strong2 = sum(1 for r in rets2 if r > 0.02) / len(rets2) if rets2 else 0
                if pct_strong2 < 0.10:
                    n_failed += 1
                    avg_r2 = statistics.mean(rets2) if rets2 else 0
                    losses.append(avg_r2)

    rec = _build_anti_law_record('FAILED_CONTAGION', None, n_failed, n_spread, losses, extracted_at)
    return [rec] if rec and rec['frequency'] >= 1 else []


# ---------------------------------------------------------------------------
# Detector: CAUSAL_INVERSION
# ---------------------------------------------------------------------------

def _detect_causal_inversion(causal_rows, ohlcv_rows, extracted_at):
    """Causal edge that reversed direction — source went up but target went down."""
    if not causal_rows:
        return []
    by_sym = _group_by_symbol(ohlcv_rows)
    # Build date→close map per symbol
    sym_date_close = {}
    for sym, rows in by_sym.items():
        sym_date_close[sym] = {r['date']: r['close'] for r in rows if r['close']}

    n_total   = 0
    n_invert  = 0
    losses    = []

    for edge in causal_rows:
        src    = edge.get('source') or ''
        tgt    = edge.get('target') or ''
        strength = float(edge.get('strength') or 0)
        dt     = edge.get('date') or ''
        if not src or not tgt or not dt:
            continue
        src_closes = sym_date_close.get(src, {})
        tgt_closes = sym_date_close.get(tgt, {})
        sorted_dates = sorted(src_closes.keys())
        idx = None
        for i, d in enumerate(sorted_dates):
            if d >= dt:
                idx = i
                break
        if idx is None or idx + 1 >= len(sorted_dates):
            continue
        dt_next = sorted_dates[idx + 1]
        src_c0 = src_closes.get(dt)
        src_c1 = src_closes.get(dt_next)
        tgt_c0 = tgt_closes.get(dt)
        tgt_c1 = tgt_closes.get(dt_next)
        if not all([src_c0, src_c1, tgt_c0, tgt_c1]):
            continue
        src_ret = (src_c1 - src_c0) / src_c0
        tgt_ret = (tgt_c1 - tgt_c0) / tgt_c0
        if strength > 0:
            n_total += 1
            # Inversion: expected same direction but got opposite
            if (src_ret > 0 and tgt_ret < -0.01) or (src_ret < 0 and tgt_ret > 0.01):
                n_invert += 1
                losses.append(tgt_ret)

    rec = _build_anti_law_record('CAUSAL_INVERSION', None, n_invert, n_total, losses, extracted_at)
    return [rec] if rec and rec['frequency'] >= 1 else []


# ---------------------------------------------------------------------------
# Detector: LIQUIDITY_WITHDRAWAL
# ---------------------------------------------------------------------------

def _detect_liquidity_withdrawal(liquidity_rows, ohlcv_rows, extracted_at):
    """LOW tier symbols: sudden volume drop after a signal day."""
    liq_map = {r['symbol']: r for r in liquidity_rows}
    by_sym  = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        tier = (liq_map.get(sym) or {}).get('tier', '')
        if str(tier).upper() != 'LOW':
            continue
        rows_s  = sorted(rows, key=lambda r: r['date'])
        volumes = [r['volume'] or 0 for r in rows_s]
        closes  = [r['close']  or 0 for r in rows_s]
        roll_avg = _compute_rolling_avg(volumes, window=20)
        n_signal  = 0
        n_failure = 0
        losses    = []
        for i in range(20, len(rows_s) - 1):
            avg_v = roll_avg[i]
            if avg_v <= 0:
                continue
            # Signal: volume spike day
            if volumes[i] > 2 * avg_v:
                n_signal += 1
                # Next day: volume collapses below avg and price drops
                if i + 1 < len(rows_s):
                    if volumes[i + 1] < avg_v * 0.5:
                        n_failure += 1
                        ret = _safe_return(closes, i, 1)
                        if ret is not None:
                            losses.append(ret)
        rec = _build_anti_law_record('LIQUIDITY_WITHDRAWAL', sym, n_failure, n_signal, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: POST_CATALYST_FADE
# ---------------------------------------------------------------------------

def _detect_post_catalyst_fade(failure_intel_rows, ohlcv_rows, extracted_at):
    """Uses failure_intelligence archetypes that indicate post-news reversals."""
    catalyst_archetypes = {'NEWS_FADE', 'CATALYST_REVERSAL', 'POST_EVENT_FADE',
                           'news_fade', 'catalyst_reversal', 'post_event_fade'}
    by_sym_ohlc = _group_by_symbol(ohlcv_rows)
    by_sym_fail = _group_by_symbol(failure_intel_rows)
    records = []
    for sym, fail_rows in by_sym_fail.items():
        ohlc = by_sym_ohlc.get(sym, [])
        if not ohlc:
            continue
        ohlc_s  = sorted(ohlc, key=lambda r: r['date'])
        closes  = [r['close'] or 0 for r in ohlc_s]
        date_idx = {r['date']: i for i, r in enumerate(ohlc_s)}
        n_cat  = 0
        n_fade = 0
        losses = []
        for fr in fail_rows:
            if fr.get('archetype') in catalyst_archetypes:
                n_cat += 1
                idx = date_idx.get(fr.get('date') or '')
                if idx is None:
                    continue
                ret = _avg_return_range(closes, idx, 1, 5)
                if ret is not None and ret < -0.01:
                    n_fade += 1
                    losses.append(ret)
        rec = _build_anti_law_record('POST_CATALYST_FADE', sym, n_fade, n_cat, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: CROWDING_REVERSAL
# ---------------------------------------------------------------------------

def _detect_crowding_reversal(ohlcv_rows, extracted_at):
    """
    High buy-volume ratio (>65%) or low buy-volume ratio (<35%) signals crowded
    trade — historically leads to reversal within 5 sessions.
    Uses volume direction proxy: if close > open, day counts as buy-dominated.
    """
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        closes  = [r['close']  or 0 for r in rows]
        volumes = [r['volume'] or 0 for r in rows]
        n_crowded = 0
        n_failure = 0
        losses    = []
        for i in range(5, len(rows) - 5):
            window = rows[i-5:i]
            buy_sessions  = sum(1 for j in range(i-5, i) if closes[j] > (closes[j-1] if j > 0 else closes[j]))
            total_sessions = 5
            buy_ratio = buy_sessions / total_sessions
            if buy_ratio > 0.65 or buy_ratio < 0.35:
                n_crowded += 1
                ret = _avg_return_range(closes, i, 1, 5)
                # Crowding reversal: if heavily bullish crowd, expect negative return
                if buy_ratio > 0.65 and ret is not None and ret < -0.01:
                    n_failure += 1
                    losses.append(ret)
                elif buy_ratio < 0.35 and ret is not None and ret > 0.01:
                    n_failure += 1
                    losses.append(-abs(ret))  # record as loss magnitude
        rec = _build_anti_law_record('CROWDING_REVERSAL', sym, n_failure, n_crowded, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: NEWS_OVERREACTION
# ---------------------------------------------------------------------------

def _detect_news_overreaction(failure_intel_rows, ohlcv_rows, extracted_at):
    """
    Uses failure_intelligence rows with low confidence as proxy for low-impact catalyst.
    If price moved >3% on a low-confidence (< 0.4) failure event, it's an overreaction.
    Mean-reversion expected within 3-5 sessions.
    """
    by_sym_ohlc = _group_by_symbol(ohlcv_rows)
    by_sym_fail = _group_by_symbol(failure_intel_rows)
    records = []
    for sym, fail_rows in by_sym_fail.items():
        ohlc = by_sym_ohlc.get(sym, [])
        if not ohlc:
            continue
        ohlc_s   = sorted(ohlc, key=lambda r: r['date'])
        closes   = [r['close'] or 0 for r in ohlc_s]
        date_idx = {r['date']: i for i, r in enumerate(ohlc_s)}
        n_events = 0
        n_revert = 0
        losses   = []
        for fr in fail_rows:
            catalyst_impact = float(fr.get('confidence') or 0.5)
            if catalyst_impact >= 0.4:
                continue  # only low-impact catalysts
            ev_date = fr.get('date') or ''
            idx = date_idx.get(ev_date)
            if idx is None or idx == 0:
                continue
            prev_close = closes[idx - 1]
            curr_close = closes[idx]
            if prev_close <= 0:
                continue
            price_change_pct = abs((curr_close - prev_close) / prev_close) * 100
            if price_change_pct > 3.0:
                n_events += 1
                ret = _avg_return_range(closes, idx, 1, 5)
                # Expect mean-reversion: if price spiked up, expect negative ret
                if curr_close > prev_close and ret is not None and ret < -0.01:
                    n_revert += 1
                    losses.append(ret)
                elif curr_close < prev_close and ret is not None and ret > 0.01:
                    n_revert += 1
                    losses.append(-abs(ret))
        rec = _build_anti_law_record('NEWS_OVERREACTION', sym, n_revert, n_events, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: EXHAUSTION_REVERSAL
# ---------------------------------------------------------------------------

def _detect_exhaustion_reversal(ohlcv_rows, extracted_at):
    """
    5+ consecutive sessions in same direction with declining volume = exhaustion.
    Classic reversal setup. Checks next 5 sessions for reversal.
    """
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        closes  = [r['close']  or 0 for r in rows]
        volumes = [r['volume'] or 0 for r in rows]
        roll_avg_vol = _compute_rolling_avg(volumes, window=20)
        n_exhaust = 0
        n_failure = 0
        losses    = []
        i = 5
        while i < len(rows) - 5:
            # Count consecutive up or down sessions ending at i
            direction = None
            consecutive = 0
            vol_trend_vals = []
            for j in range(i, max(i-10, 0), -1):
                if j == 0:
                    break
                if closes[j] > closes[j-1]:
                    d = 'up'
                elif closes[j] < closes[j-1]:
                    d = 'down'
                else:
                    break
                if direction is None:
                    direction = d
                if d != direction:
                    break
                consecutive += 1
                if j > 0 and roll_avg_vol[j-1] > 0:
                    vol_trend_vals.append(volumes[j] / roll_avg_vol[j-1] - 1)
            if consecutive >= 5 and vol_trend_vals:
                # Check volume is declining (negative trend)
                vol_trend = statistics.mean(vol_trend_vals[-3:]) if len(vol_trend_vals) >= 3 else vol_trend_vals[-1]
                if vol_trend < -0.1:
                    n_exhaust += 1
                    ret = _avg_return_range(closes, i, 1, 5)
                    # Expect reversal: up exhaustion → expect negative return
                    if direction == 'up' and ret is not None and ret < -0.01:
                        n_failure += 1
                        losses.append(ret)
                    elif direction == 'down' and ret is not None and ret > 0.01:
                        n_failure += 1
                        losses.append(-abs(ret))
                    i += 5  # skip ahead
                    continue
            i += 1
        rec = _build_anti_law_record('EXHAUSTION_REVERSAL', sym, n_failure, n_exhaust, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: VOLATILITY_FAKE_RELEASE
# ---------------------------------------------------------------------------

def _detect_volatility_fake_release(ohlcv_rows, extracted_at):
    """
    Detects volatility spikes (range > 1.8x 20-day avg range) that recover
    within 2 sessions back to normal — fake vol releases precede larger moves.
    """
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        closes  = [r['close']  or 0 for r in rows]
        volumes = [r['volume'] or 0 for r in rows]
        # Use intra-day range as vol proxy: |close[i] - close[i-1]| / close[i-1]
        daily_ranges = []
        for i in range(1, len(rows)):
            c0 = closes[i-1]
            c1 = closes[i]
            if c0 > 0:
                daily_ranges.append(abs(c1 - c0) / c0)
            else:
                daily_ranges.append(0.0)

        roll_avg_range = _compute_rolling_avg(daily_ranges, window=20)

        n_spikes  = 0
        n_failure = 0
        losses    = []
        for i in range(20, len(daily_ranges) - 3):
            avg_r = roll_avg_range[i]
            if avg_r <= 0:
                continue
            vol_spike_ratio = daily_ranges[i] / avg_r
            if vol_spike_ratio > 1.8:
                # Check if vol recovered within 2 sessions
                recovery_sessions = 0
                for d in range(1, 3):
                    if i + d < len(daily_ranges):
                        if daily_ranges[i + d] <= avg_r * 1.2:
                            recovery_sessions = d
                            break
                if recovery_sessions > 0 and recovery_sessions <= 2:
                    n_spikes += 1
                    ret = _avg_return_range(closes, i + 1, 1, 5)
                    if ret is not None and abs(ret) > 0.02:
                        n_failure += 1
                        losses.append(ret if ret < 0 else -ret)
        rec = _build_anti_law_record('VOLATILITY_FAKE_RELEASE', sym, n_failure, n_spikes, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Detector: POST_GAP_FAILURE
# ---------------------------------------------------------------------------

def _detect_post_gap_failure(ohlcv_rows, extracted_at):
    """
    Gap up/down >2% at open (proxied by close-to-close) that failed to hold
    by session end (next session close reverses the gap direction).
    anti_precision target: ~77%
    """
    by_sym = _group_by_symbol(ohlcv_rows)
    records = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r['date'])
        closes = [r['close'] or 0 for r in rows]
        n_gaps    = 0
        n_failure = 0
        losses    = []
        for i in range(2, len(rows) - 3):
            c_prev = closes[i - 1]
            c_gap  = closes[i]
            if c_prev <= 0:
                continue
            gap_pct = (c_gap - c_prev) / c_prev * 100
            if abs(gap_pct) > 2.0:
                n_gaps += 1
                # Check if gap held: next 1-3 sessions close in gap direction
                gap_held = False
                for d in range(1, 4):
                    if i + d < len(closes) and closes[i + d] > 0:
                        if gap_pct > 0 and closes[i + d] >= c_prev:
                            gap_held = True
                            break
                        elif gap_pct < 0 and closes[i + d] <= c_prev:
                            gap_held = True
                            break
                if not gap_held:
                    n_failure += 1
                    ret = _avg_return_range(closes, i, 1, 3)
                    if ret is not None:
                        # If gap up failed, the loss is negative return
                        loss = ret if gap_pct > 0 else -abs(ret)
                        losses.append(loss)
        rec = _build_anti_law_record('POST_GAP_FAILURE', sym, n_failure, n_gaps, losses, extracted_at)
        if rec and rec['frequency'] >= 1:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Core Command: extract_anti_laws
# ---------------------------------------------------------------------------

def cmd_extract_anti_laws(params):
    db = get_db()
    extracted_at = datetime.now().isoformat()

    ohlcv       = _fetch_ohlcv(db)
    fail_intel  = _fetch_failure_intelligence(db)
    pat_laws    = _fetch_pattern_laws(db)
    explosions  = _fetch_explosion_readiness(db)
    stock_dna   = _fetch_stock_dna(db)
    regime      = _fetch_regime(db)
    causal      = _fetch_causal_edges(db)
    liquidity   = _fetch_liquidity_profiles(db)

    all_records = []

    # Run all detectors
    all_records += _detect_volume_trap(ohlcv, extracted_at)
    all_records += _detect_false_breakout(ohlcv, extracted_at)
    all_records += _detect_breadth_divergence(ohlcv, stock_dna, extracted_at)
    all_records += _detect_regime_override(pat_laws, regime, ohlcv, extracted_at)
    all_records += _detect_law_degradation_trap(pat_laws, ohlcv, extracted_at)
    all_records += _detect_explosion_fakeout(explosions, ohlcv, extracted_at)
    all_records += _detect_failed_contagion(ohlcv, stock_dna, extracted_at)
    all_records += _detect_causal_inversion(causal, ohlcv, extracted_at)
    all_records += _detect_liquidity_withdrawal(liquidity, ohlcv, extracted_at)
    all_records += _detect_post_catalyst_fade(fail_intel, ohlcv, extracted_at)
    all_records += _detect_crowding_reversal(ohlcv, extracted_at)
    all_records += _detect_news_overreaction(fail_intel, ohlcv, extracted_at)
    all_records += _detect_exhaustion_reversal(ohlcv, extracted_at)
    all_records += _detect_volatility_fake_release(ohlcv, extracted_at)
    all_records += _detect_post_gap_failure(ohlcv, extracted_at)

    # Filter None
    all_records = [r for r in all_records if r is not None]

    # Persist
    for rec in all_records:
        _upsert_anti_law(db, rec)
    db.commit()

    n_veto = sum(1 for r in all_records if r['is_veto'])
    sorted_by_prec = sorted(all_records, key=lambda r: r['anti_precision'], reverse=True)
    top5 = [{'anti_law_id': r['anti_law_id'], 'anti_precision': r['anti_precision'],
              'frequency': r['frequency'], 'severity': r['severity']}
            for r in sorted_by_prec[:5]]
    types_found = list({r['anti_law_type'] for r in all_records})

    db.close()
    return {
        'n_anti_laws_extracted': len(all_records),
        'n_veto_laws':           n_veto,
        'top_5_by_precision':    top5,
        'anti_law_types_found':  types_found,
    }


# ---------------------------------------------------------------------------
# Core Command: build_library
# ---------------------------------------------------------------------------

def cmd_build_library(params):
    extraction = cmd_extract_anti_laws(params)
    db = get_db()
    rows = db.execute("SELECT * FROM anti_laws").fetchall()
    records = [dict(r) for r in rows]
    db.close()

    if not records:
        return {
            'extraction':    extraction,
            'library_stats': {'total': 0, 'avg_anti_precision': 0, 'n_veto': 0},
            'most_dangerous': [],
            'by_type':        {},
        }

    total       = len(records)
    avg_prec    = statistics.mean(r['anti_precision'] for r in records)
    n_veto      = sum(1 for r in records if r['is_veto'])

    # Danger score = anti_precision × severity_score × frequency (log-scaled)
    def danger_score(r):
        sv = SEVERITY_SCORE.get(r['severity'], 1)
        freq = max(r['frequency'], 1)
        return r['anti_precision'] * sv * math.log1p(freq)

    sorted_danger = sorted(records, key=danger_score, reverse=True)
    most_dangerous = [
        {'anti_law_id': r['anti_law_id'], 'anti_law_type': r['anti_law_type'],
         'anti_precision': r['anti_precision'], 'severity': r['severity'],
         'frequency': r['frequency'], 'danger_score': round(danger_score(r), 4)}
        for r in sorted_danger[:5]
    ]

    # By type
    by_type = defaultdict(list)
    for r in records:
        by_type[r['anti_law_type']].append(r)
    by_type_stats = {}
    for t, recs in by_type.items():
        by_type_stats[t] = {
            'count':            len(recs),
            'avg_precision':    round(statistics.mean(x['anti_precision'] for x in recs), 4),
            'n_veto':           sum(1 for x in recs if x['is_veto']),
        }

    return {
        'extraction':    extraction,
        'library_stats': {
            'total':              total,
            'avg_anti_precision': round(avg_prec, 4),
            'n_veto':             n_veto,
        },
        'most_dangerous': most_dangerous,
        'by_type':        by_type_stats,
    }


# ---------------------------------------------------------------------------
# Helpers: Real-time symbol state checks
# ---------------------------------------------------------------------------

def _get_symbol_recent_closes(db, symbol, n=30):
    try:
        rows = db.execute(
            "SELECT date(bar_time,'unixepoch') AS date, close, volume "
            "FROM ohlcv_history_execution WHERE symbol=? ORDER BY bar_time DESC LIMIT ?",
            (symbol, n)
        ).fetchall()
        return [dict(r) for r in rows][::-1]  # oldest first
    except Exception:
        return []


def _get_symbol_sector(db, symbol):
    try:
        row = db.execute(
            "SELECT sector FROM stock_dna WHERE symbol=?", (symbol,)
        ).fetchone()
        return row['sector'] if row else None
    except Exception:
        return None


def _get_sector_today_return(db, sector, today):
    try:
        rows = db.execute("""
            SELECT o.close, date(o.bar_time,'unixepoch') AS date
            FROM ohlcv_history_execution o
            JOIN stock_dna d ON o.symbol = d.symbol
            WHERE d.sector=? AND date(o.bar_time,'unixepoch') <= ?
            ORDER BY o.bar_time DESC LIMIT 100
        """, (sector, today)).fetchall()
        if not rows:
            return None
        # Two most recent dates
        dates = sorted({r['date'] for r in rows}, reverse=True)
        if len(dates) < 2:
            return None
        d0, d1 = dates[1], dates[0]
        c0s = [r['close'] for r in rows if r['date'] == d0 and r['close']]
        c1s = [r['close'] for r in rows if r['date'] == d1 and r['close']]
        if not c0s or not c1s:
            return None
        return (statistics.mean(c1s) - statistics.mean(c0s)) / statistics.mean(c0s)
    except Exception:
        return None


def _get_current_regime(db):
    for table in ('market_regime', 'regime_history'):
        try:
            row = db.execute(
                f"SELECT regime_label FROM {table} ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                return row['regime_label']
        except Exception:
            continue
    return None


def _get_symbol_explosion_recent(db, symbol, n=10):
    try:
        rows = db.execute(
            "SELECT readiness_score, date FROM explosion_readiness WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol, n)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_degrading_laws(db):
    try:
        rows = db.execute(
            "SELECT pattern_name FROM pattern_laws WHERE status='DEGRADING'"
        ).fetchall()
        return [r['pattern_name'] for r in rows]
    except Exception:
        return []


def _get_anti_law_record(db, anti_law_type, symbol=None):
    """Fetch the stored anti-law record for this type+symbol or GLOBAL."""
    law_id = f"{anti_law_type}_{symbol}" if symbol else f"{anti_law_type}_GLOBAL"
    try:
        row = db.execute(
            "SELECT * FROM anti_laws WHERE anti_law_id=?", (law_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core Command: scan_symbol
# ---------------------------------------------------------------------------

def cmd_scan_symbol(params):
    symbol = params.get('symbol', '').upper()
    if not symbol:
        return {'error': 'symbol required'}

    db = get_db()
    today_str = TODAY
    recent = _get_symbol_recent_closes(db, symbol, n=30)

    triggered = []

    # ---- VOLUME_TRAP ----
    if len(recent) >= 21:
        closes  = [r['close']  or 0 for r in recent]
        volumes = [r['volume'] or 0 for r in recent]
        avg_vol_20 = statistics.mean(volumes[-21:-1]) if volumes[-21:-1] else 0
        today_vol  = volumes[-1]
        if avg_vol_20 > 0 and today_vol > 3 * avg_vol_20:
            ratio    = today_vol / avg_vol_20
            law_rec  = _get_anti_law_record(db, 'VOLUME_TRAP', symbol)
            prec     = law_rec['anti_precision'] if law_rec else 0.65
            avg_loss = law_rec['avg_loss'] if law_rec else -0.025
            is_veto  = bool(law_rec['is_veto']) if law_rec else (prec > 0.70)
            triggered.append({
                'name':               'VOLUME_TRAP',
                'confidence':         round(prec, 3),
                'description':        f"Volume {ratio:.1f}x avg — historically {prec*100:.0f}% lead to reversal",
                'historical_loss_avg': round(avg_loss * 100, 2),
                'is_veto':             is_veto,
            })

    # ---- FALSE_BREAKOUT ----
    if len(recent) >= 21:
        closes = [r['close'] or 0 for r in recent]
        high_20 = max(closes[-21:-1]) if closes[-21:-1] else 0
        c_today = closes[-1]
        if high_20 > 0 and c_today > high_20:
            law_rec  = _get_anti_law_record(db, 'FALSE_BREAKOUT', symbol)
            prec     = law_rec['anti_precision'] if law_rec else 0.55
            avg_loss = law_rec['avg_loss'] if law_rec else -0.018
            is_veto  = bool(law_rec['is_veto']) if law_rec else (prec > 0.70)
            triggered.append({
                'name':               'FALSE_BREAKOUT',
                'confidence':         round(prec, 3),
                'description':        f"Price broke 20-day high — {prec*100:.0f}% chance of false breakout reversal",
                'historical_loss_avg': round(avg_loss * 100, 2),
                'is_veto':             is_veto,
            })

    # ---- LAW_DEGRADATION_TRAP ----
    degrading = _get_degrading_laws(db)
    if degrading:
        law_rec  = _get_anti_law_record(db, 'LAW_DEGRADATION_TRAP', None)
        prec     = law_rec['anti_precision'] if law_rec else 0.60
        avg_loss = law_rec['avg_loss'] if law_rec else -0.02
        is_veto  = bool(law_rec['is_veto']) if law_rec else (prec > 0.70)
        triggered.append({
            'name':               'LAW_DEGRADATION_TRAP',
            'confidence':         round(prec, 3),
            'description':        f"{len(degrading)} degrading laws active — signals from these are unreliable",
            'historical_loss_avg': round(avg_loss * 100, 2),
            'is_veto':             is_veto,
        })

    # ---- EXPLOSION_FAKEOUT ----
    exp_recent = _get_symbol_explosion_recent(db, symbol, n=10)
    if exp_recent:
        high_exp = [e for e in exp_recent if (e.get('readiness_score') or 0) > 70]
        if high_exp:
            closes_list = [r['close'] or 0 for r in recent]
            c_now   = closes_list[-1] if closes_list else 0
            c_at    = None
            exp_date = high_exp[0].get('date', '')
            for r in recent:
                if r['date'] == exp_date:
                    c_at = r['close']
                    break
            fakeout = c_at and c_now and c_now < c_at
            law_rec  = _get_anti_law_record(db, 'EXPLOSION_FAKEOUT', symbol)
            prec     = law_rec['anti_precision'] if law_rec else 0.50
            avg_loss = law_rec['avg_loss'] if law_rec else -0.015
            is_veto  = bool(law_rec['is_veto']) if law_rec else False
            if fakeout:
                triggered.append({
                    'name':               'EXPLOSION_FAKEOUT',
                    'confidence':         round(prec, 3),
                    'description':        f"Explosion readiness was {high_exp[0]['readiness_score']:.0f} on {exp_date} — price has not followed through",
                    'historical_loss_avg': round(avg_loss * 100, 2),
                    'is_veto':             is_veto,
                })

    # ---- BREADTH_DIVERGENCE ----
    sector = _get_symbol_sector(db, symbol)
    if sector and len(recent) >= 2:
        closes_list = [r['close'] or 0 for r in recent]
        c0 = closes_list[-2]
        c1 = closes_list[-1]
        if c0 > 0:
            sym_ret = (c1 - c0) / c0
            sec_ret = _get_sector_today_return(db, sector, today_str)
            if sec_ret is not None and sym_ret > 0 and sec_ret < -0.01:
                law_rec  = _get_anti_law_record(db, 'BREADTH_DIVERGENCE', symbol)
                prec     = law_rec['anti_precision'] if law_rec else 0.58
                avg_loss = law_rec['avg_loss'] if law_rec else -0.02
                is_veto  = bool(law_rec['is_veto']) if law_rec else (prec > 0.70)
                triggered.append({
                    'name':               'BREADTH_DIVERGENCE',
                    'confidence':         round(prec, 3),
                    'description':        f"Stock +{sym_ret*100:.1f}% but sector {sec_ret*100:.1f}% — divergence pattern",
                    'historical_loss_avg': round(avg_loss * 100, 2),
                    'is_veto':             is_veto,
                })

    # ---- REGIME_OVERRIDE ----
    regime = _get_current_regime(db)
    if regime and str(regime).upper() in ('VOLATILE', 'TRANSITION'):
        law_rec  = _get_anti_law_record(db, 'REGIME_OVERRIDE', None)
        prec     = law_rec['anti_precision'] if law_rec else 0.55
        avg_loss = law_rec['avg_loss'] if law_rec else -0.018
        is_veto  = bool(law_rec['is_veto']) if law_rec else False
        triggered.append({
            'name':               'REGIME_OVERRIDE',
            'confidence':         round(prec, 3),
            'description':        f"Current regime is {regime} — bullish signals fire in wrong context",
            'historical_loss_avg': round(avg_loss * 100, 2),
            'is_veto':             is_veto,
        })

    # Build output
    n_triggered = len(triggered)
    any_veto    = any(t['is_veto'] for t in triggered)
    strongest   = max(triggered, key=lambda t: t['confidence'])['name'] if triggered else None

    if any_veto:
        safety_level = 'VETO'
    elif n_triggered >= 3:
        safety_level = 'DANGER'
    elif n_triggered >= 1:
        safety_level = 'CAUTION'
    else:
        safety_level = 'SAFE'

    db.close()
    return {
        'symbol':               symbol,
        'date':                 today_str,
        'triggered_anti_laws':  triggered,
        'n_triggered':          n_triggered,
        'anti_law_veto':        any_veto,
        'strongest_anti_law':   strongest,
        'safety_level':         safety_level,
    }


# ---------------------------------------------------------------------------
# Core Command: daily_scan
# ---------------------------------------------------------------------------

def cmd_daily_scan(params):
    db = get_db()
    try:
        symbols = [r['symbol'] for r in db.execute(
            "SELECT DISTINCT symbol FROM ohlcv_history_execution"
        ).fetchall()]
    except Exception:
        symbols = []
    db.close()

    veto_syms     = []
    caution_syms  = []
    safe_syms     = []
    pattern_ctr   = Counter()

    for sym in symbols:
        result = cmd_scan_symbol({'symbol': sym})
        level  = result.get('safety_level', 'SAFE')
        for t in result.get('triggered_anti_laws', []):
            pattern_ctr[t['name']] += 1

        # Persist to daily scan table
        db2 = get_db()
        triggered_types = json.dumps([t['name'] for t in result.get('triggered_anti_laws', [])])
        try:
            db2.execute("""
                INSERT INTO anti_law_daily_scan
                    (symbol, date, triggered_types, n_triggered, anti_law_veto,
                     safety_level, strongest_anti_law, computed_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    triggered_types    = excluded.triggered_types,
                    n_triggered        = excluded.n_triggered,
                    anti_law_veto      = excluded.anti_law_veto,
                    safety_level       = excluded.safety_level,
                    strongest_anti_law = excluded.strongest_anti_law,
                    computed_at        = excluded.computed_at
            """, (
                sym, TODAY, triggered_types,
                result['n_triggered'],
                int(result['anti_law_veto']),
                level,
                result.get('strongest_anti_law'),
                datetime.now().isoformat(),
            ))
            db2.commit()
        except Exception:
            pass
        db2.close()

        if level == 'VETO':
            veto_syms.append(sym)
        elif level in ('CAUTION', 'DANGER'):
            caution_syms.append(sym)
        else:
            safe_syms.append(sym)

    n_total = len(symbols)
    pct_active = round((len(veto_syms) + len(caution_syms)) / max(n_total, 1) * 100, 1)
    most_dangerous_pattern = pattern_ctr.most_common(1)[0][0] if pattern_ctr else None

    return {
        'date':                    TODAY,
        'n_veto':                  len(veto_syms),
        'n_caution':               len(caution_syms),
        'n_safe':                  len(safe_syms),
        'veto_symbols':            veto_syms,
        'caution_symbols':         caution_syms,
        'most_dangerous_pattern':  most_dangerous_pattern,
        'anti_law_market_breadth': f"{pct_active}%",
    }


# ---------------------------------------------------------------------------
# Core Command: anti_law_report
# ---------------------------------------------------------------------------

def cmd_anti_law_report(params):
    db = get_db()
    rows = db.execute("SELECT * FROM anti_laws").fetchall()
    all_laws = [dict(r) for r in rows]

    # Daily scan results for today
    try:
        scan_rows = db.execute(
            "SELECT * FROM anti_law_daily_scan WHERE date=?", (TODAY,)
        ).fetchall()
        scan_today = [dict(r) for r in scan_rows]
    except Exception:
        scan_today = []

    db.close()

    library_size = len(all_laws)

    # Most active type from today's scan
    type_ctr = Counter()
    for sr in scan_today:
        try:
            types = json.loads(sr['triggered_types'] or '[]')
            for t in types:
                type_ctr[t] += 1
        except Exception:
            pass
    most_active_type = type_ctr.most_common(1)[0][0] if type_ctr else None

    # Highest risk symbols
    veto_today = [sr['symbol'] for sr in scan_today if sr.get('anti_law_veto')]
    danger_today = [sr['symbol'] for sr in scan_today if sr.get('safety_level') in ('DANGER', 'VETO')]

    # Market failure risk
    n_total = max(len(scan_today), 1)
    pct_danger = len(danger_today) / n_total
    if pct_danger > 0.50:
        market_failure_risk = 'EXTREME'
    elif pct_danger > 0.30:
        market_failure_risk = 'HIGH'
    elif pct_danger > 0.15:
        market_failure_risk = 'MODERATE'
    else:
        market_failure_risk = 'LOW'

    # Key warnings
    key_warnings = []
    if veto_today:
        key_warnings.append(f"{len(veto_today)} symbols under VETO — do not trade today")
    if most_active_type:
        key_warnings.append(f"Most active anti-law pattern: {most_active_type}")
    if market_failure_risk in ('HIGH', 'EXTREME'):
        key_warnings.append(f"Market failure risk is {market_failure_risk} — reduce all positions")

    top_veto_laws = sorted([l for l in all_laws if l['is_veto']],
                           key=lambda r: r['anti_precision'], reverse=True)[:5]
    highest_risk_symbols = list(dict.fromkeys(veto_today + danger_today))[:10]

    return {
        'date':                 TODAY,
        'library_size':         library_size,
        'most_active_type':     most_active_type,
        'highest_risk_symbols': highest_risk_symbols,
        'market_failure_risk':  market_failure_risk,
        'key_warnings':         key_warnings,
        'top_veto_laws':        [
            {'anti_law_id': l['anti_law_id'], 'anti_precision': l['anti_precision'],
             'severity': l['severity'], 'frequency': l['frequency']}
            for l in top_veto_laws
        ],
    }


# ---------------------------------------------------------------------------
# Core Command: build_full
# ---------------------------------------------------------------------------

def cmd_build_full(params):
    extraction = cmd_extract_anti_laws(params)
    library    = cmd_build_library(params)
    scan       = cmd_daily_scan(params)
    report     = cmd_anti_law_report(params)
    return {
        'extraction': extraction,
        'library':    library,
        'scan':       scan,
        'report':     report,
        'status':     'complete',
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'extract_anti_laws': cmd_extract_anti_laws,
    'build_library':     cmd_build_library,
    'scan_symbol':       cmd_scan_symbol,
    'daily_scan':        cmd_daily_scan,
    'anti_law_report':   cmd_anti_law_report,
    'build_full':        cmd_build_full,
}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: python anti_laws_engine.py <command> [json_params]'}))
        sys.exit(1)

    command = sys.argv[1]
    raw_params = sys.argv[2] if len(sys.argv) > 2 else '{}'
    try:
        params = json.loads(raw_params)
    except Exception:
        params = {}

    handler = COMMANDS.get(command)
    if handler is None:
        print(json.dumps({'error': f'Unknown command: {command}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
