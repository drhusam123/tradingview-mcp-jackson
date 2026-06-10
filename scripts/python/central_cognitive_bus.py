#!/usr/bin/env python3
"""
central_cognitive_bus.py — Phase 42
EGX Autonomous Quant System: Central Cognitive Bus

The single source of truth for system state. All Phases write their signals to
the Bus; Portfolio and Arbitration read from the Bus before making decisions.
Computes cross-phase coherence and contradiction maps.

Invocation: python central_cognitive_bus.py <command> '<json_params>'
Output: last stdout line = valid JSON

Commands:
  collect_signals     — harvest latest signal from every registered Phase
  compute_coherence   — direction coherence + contradiction pairs
  bus_directive       — global ENGAGE / WAIT / AVOID / DEFENSIVE / HALT decision
  read_bus            — collect_signals + compute_coherence + bus_directive
  contradiction_matrix — full NxN phase agreement/contradiction matrix
  build_full          — read_bus + persist to bus_signals / bus_state tables
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, date
from collections import defaultdict, Counter

# ---------------------------------------------------------------------------
# Paths & DB
# ---------------------------------------------------------------------------

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

TODAY = date.today().isoformat()
NOW   = datetime.utcnow().isoformat(timespec='seconds') + 'Z'

# ---------------------------------------------------------------------------
# Signal Schema Constants
# ---------------------------------------------------------------------------

SIGNAL_TYPES = ['CONFIDENCE', 'UNCERTAINTY', 'RISK', 'OPPORTUNITY', 'DIRECTION', 'REGIME']
DIRECTIONS   = ['BULLISH', 'BEARISH', 'NEUTRAL', 'UNKNOWN']
URGENCIES    = ['IMMEDIATE', 'DAILY', 'WEEKLY']

# ---------------------------------------------------------------------------
# Phase Registry
# ---------------------------------------------------------------------------

PHASE_REGISTRY = {
    16: {'name': 'regime_detector',       'table': 'market_regimes',             'signal_type': 'REGIME',      'direction_col': 'regime_type'},
    17: {'name': 'prediction_engine',     'table': 'predictions',                'signal_type': 'CONFIDENCE',  'direction_col': 'direction'},
    18: {'name': 'risk_manager',          'table': 'risk_assessments',           'signal_type': 'RISK',        'value_col': 'risk_score'},
    19: {'name': 'contagion_mapper',      'table': 'contagion_maps',             'signal_type': 'RISK',        'value_col': 'contagion_score'},
    20: {'name': 'law_library',           'table': 'pattern_laws',               'signal_type': 'OPPORTUNITY', 'value_col': 'precision'},
    22: {'name': 'anomaly_detector',      'table': 'market_anomalies',           'signal_type': 'OPPORTUNITY', 'value_col': 'anomaly_score'},
    23: {'name': 'sentiment_engine',      'table': 'sentiment_scores',           'signal_type': 'DIRECTION',   'direction_col': 'sentiment_direction'},
    24: {'name': 'catalyst_tracker',      'table': 'catalyst_events',            'signal_type': 'OPPORTUNITY', 'value_col': 'impact_score'},
    27: {'name': 'synthesis_engine',      'table': 'daily_synthesis',            'signal_type': 'CONFIDENCE',  'value_col': 'synthesis_score'},
    29: {'name': 'prioritizer',           'table': 'intelligence_scores',        'signal_type': 'CONFIDENCE',  'value_col': 'intelligence_score'},
    33: {'name': 'transition_forecaster', 'table': 'regime_transition_signals',  'signal_type': 'RISK',        'value_col': 'transition_probability'},
    34: {'name': 'arbitration',           'table': 'arbitration_decisions',      'signal_type': 'DIRECTION',   'direction_col': 'decision'},
    35: {'name': 'anti_laws',             'table': 'anti_law_daily_scan',        'signal_type': 'RISK',        'value_col': 'anti_law_market_breadth'},
    36: {'name': 'stat_grounding',        'table': 'law_grades',                 'signal_type': 'CONFIDENCE',  'value_col': 'precision'},
    37: {'name': 'observatory',           'table': 'system_health_reports',      'signal_type': 'CONFIDENCE',  'value_col': 'sts'},
    38: {'name': 'compression',           'table': 'market_intelligence_index',  'signal_type': 'CONFIDENCE',  'value_col': 'mii'},
    39: {'name': 'uncertainty',           'table': 'uncertainty_reports',        'signal_type': 'UNCERTAINTY', 'value_col': 'total_uncertainty'},
    40: {'name': 'sandbox',               'table': 'sandbox_results',            'signal_type': 'OPPORTUNITY', 'value_col': 'n_promoted'},
}

N_PHASES = len(PHASE_REGISTRY)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_exists(conn, table_name):
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def fetch_latest_row(conn, table_name):
    """Return the most-recent row from table_name as a dict, or None."""
    try:
        if not table_exists(conn, table_name):
            return None
        # Try common timestamp columns first, fall back to rowid
        for ts_col in ('created_at', 'generated_at', 'published_at', 'date', 'scan_date', 'id'):
            try:
                cur = conn.execute(
                    f"SELECT * FROM {table_name} ORDER BY {ts_col} DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
            except Exception:
                continue
        return None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def safe_float(v, default=0.5):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalise_value(phase_id, row, meta):
    """
    Return a normalised 0-1 float for the given phase's latest row.
    """
    name     = meta['name']
    val_col  = meta.get('value_col')

    if val_col is None:
        # Direction-only phases get a synthetic 0.5 presence signal
        return 0.5

    raw = safe_float(row.get(val_col), 0.5)

    # Special per-phase normalisation rules
    if name == 'observatory':           # sts is 0-100
        return clamp(raw / 100.0)
    if name == 'compression':           # mii is 0-100
        return clamp(raw / 100.0)
    if name == 'prioritizer':           # intelligence_score is 0-100
        return clamp(raw / 100.0)
    if name == 'synthesis_engine':      # synthesis_score is 0-100
        return clamp(raw / 100.0)
    if name == 'sandbox':               # n_promoted ÷ 5, cap 1.0
        return clamp(raw / 5.0)
    if name == 'risk_manager':          # already 0-1
        return clamp(raw)
    if name == 'contagion_mapper':      # already 0-1 (could be 0-100)
        return clamp(raw / 100.0) if raw > 1.0 else clamp(raw)
    if name == 'transition_forecaster': # already 0-1 probability
        return clamp(raw)
    if name == 'anti_laws':             # breadth 0-1
        return clamp(raw)
    if name == 'stat_grounding':        # precision 0-1
        return clamp(raw)
    if name == 'anomaly_detector':      # score 0-1 (may be z-score normalised)
        return clamp(raw)
    if name == 'law_library':           # precision 0-1
        return clamp(raw)
    if name == 'catalyst_tracker':      # impact_score 0-1 or 0-10
        return clamp(raw / 10.0) if raw > 1.0 else clamp(raw)
    if name == 'sentiment_engine':      # 0-1 sentiment
        return clamp(raw)
    if name == 'prediction_engine':     # confidence 0-1 or 0-100
        return clamp(raw / 100.0) if raw > 1.0 else clamp(raw)
    if name == 'uncertainty':           # 0-1 uncertainty
        return clamp(raw)

    # Generic: if > 1 assume 0-100 scale
    return clamp(raw / 100.0) if raw > 1.0 else clamp(raw)


def infer_direction(phase_id, row, meta, value):
    """
    Return one of BULLISH / BEARISH / NEUTRAL / UNKNOWN
    """
    name = meta['name']
    signal_type = meta['signal_type']

    # Direction-column phases
    if 'direction_col' in meta:
        raw_dir = str(row.get(meta['direction_col'], '')).upper().strip()

        # Regime detector
        if name == 'regime_detector':
            if 'BULL' in raw_dir:   return 'BULLISH'
            if 'BEAR' in raw_dir:   return 'BEARISH'
            return 'NEUTRAL'

        # Prediction engine
        if name == 'prediction_engine':
            if raw_dir in ('UP', 'BULLISH', 'BUY', 'LONG'):   return 'BULLISH'
            if raw_dir in ('DOWN', 'BEARISH', 'SELL', 'SHORT'): return 'BEARISH'
            return 'NEUTRAL'

        # Sentiment engine
        if name == 'sentiment_engine':
            if raw_dir in ('BULLISH', 'POSITIVE', 'UP'):   return 'BULLISH'
            if raw_dir in ('BEARISH', 'NEGATIVE', 'DOWN'): return 'BEARISH'
            return 'NEUTRAL'

        # Arbitration
        if name == 'arbitration':
            if raw_dir in ('ENTER', 'BUY', 'LONG'):        return 'BULLISH'
            if raw_dir in ('AVOID', 'SELL', 'SHORT'):       return 'BEARISH'
            return 'NEUTRAL'

        return 'UNKNOWN'

    # Value-based direction inference
    if signal_type == 'RISK':
        if value > 0.70: return 'BEARISH'
        if value < 0.30: return 'BULLISH'
        return 'NEUTRAL'

    if signal_type == 'CONFIDENCE':
        if value > 0.65: return 'BULLISH'
        if value < 0.35: return 'BEARISH'
        return 'NEUTRAL'

    if signal_type == 'OPPORTUNITY':
        if value > 0.60: return 'BULLISH'
        if value < 0.25: return 'BEARISH'
        return 'NEUTRAL'

    if signal_type == 'UNCERTAINTY':
        # High uncertainty → bearish pressure
        if value > 0.70: return 'BEARISH'
        if value < 0.30: return 'BULLISH'
        return 'NEUTRAL'

    return 'NEUTRAL'


def compute_urgency(value):
    if value > 0.8:  return 'IMMEDIATE'
    if value > 0.5:  return 'DAILY'
    return 'WEEKLY'

# ---------------------------------------------------------------------------
# Command: collect_signals
# ---------------------------------------------------------------------------

def collect_signals(params):
    signals = []
    n_available = 0
    n_missing   = 0

    try:
        conn = get_db()
    except Exception as e:
        return {'error': f'DB connection failed: {e}', 'signals': [], 'n_available': 0, 'n_missing': N_PHASES}

    try:
        for phase_id in sorted(PHASE_REGISTRY.keys()):
            meta  = PHASE_REGISTRY[phase_id]
            name  = meta['name']
            table = meta['table']
            signal_type = meta['signal_type']

            row = fetch_latest_row(conn, table)

            if row is None:
                signals.append({
                    'phase':       phase_id,
                    'name':        name,
                    'signal_type': 'UNKNOWN',
                    'value':       0.5,
                    'direction':   'UNKNOWN',
                    'urgency':     'WEEKLY',
                    'available':   False,
                })
                n_missing += 1
                continue

            value     = normalise_value(phase_id, row, meta)
            direction = infer_direction(phase_id, row, meta, value)
            urgency   = compute_urgency(value)

            signals.append({
                'phase':       phase_id,
                'name':        name,
                'signal_type': signal_type,
                'value':       round(value, 4),
                'direction':   direction,
                'urgency':     urgency,
                'available':   True,
            })
            n_available += 1

    except Exception as e:
        return {'error': str(e), 'signals': signals, 'n_available': n_available, 'n_missing': n_missing}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        'signals':      signals,
        'n_available':  n_available,
        'n_missing':    n_missing,
        'collected_at': NOW,
    }

# ---------------------------------------------------------------------------
# Command: compute_coherence
# ---------------------------------------------------------------------------

def _direction_from_signals(signals):
    """
    Given a list of signal dicts, return coherence metrics.
    Only considers signals where available=True and direction != UNKNOWN.
    """
    known = [s for s in signals if s.get('available') and s.get('direction') not in ('UNKNOWN',)]
    if not known:
        return {
            'coherence_score':              0.0,
            'coherence_level':              'LOW_COHERENCE',
            'contradiction_pairs':          [],
            'narrative_direction':          'UNKNOWN',
            'n_bullish':                    0,
            'n_bearish':                    0,
            'n_neutral':                    0,
            'direction_coherence_fraction': 0.0,
        }

    dir_counts = Counter(s['direction'] for s in known)
    n_bullish  = dir_counts.get('BULLISH', 0)
    n_bearish  = dir_counts.get('BEARISH', 0)
    n_neutral  = dir_counts.get('NEUTRAL', 0)
    total      = len(known)

    # Narrative: the plurality direction
    narrative = max(dir_counts, key=dir_counts.get)
    top_count = dir_counts[narrative]
    direction_coherence_fraction = top_count / total if total > 0 else 0.0

    # Coherence level
    if direction_coherence_fraction > 0.70:
        coherence_level = 'HIGH_COHERENCE'
    elif direction_coherence_fraction >= 0.50:
        coherence_level = 'MODERATE_COHERENCE'
    else:
        coherence_level = 'LOW_COHERENCE'

    # Coverage & avg confidence
    n_all        = N_PHASES
    n_avail      = sum(1 for s in signals if s.get('available'))
    signal_coverage = n_avail / n_all if n_all > 0 else 0.0

    avail_values = [s['value'] for s in signals if s.get('available')]
    avg_confidence = statistics.mean(avail_values) if avail_values else 0.5

    # Cognitive coherence score (0-100)
    coherence_score = (
        direction_coherence_fraction * 60.0
        + signal_coverage             * 20.0
        + avg_confidence              * 20.0
    )
    coherence_score = round(clamp(coherence_score, 0.0, 100.0), 2)

    # Contradiction pairs (BULLISH ↔ BEARISH)
    contradiction_pairs = []
    signal_list = [s for s in signals if s.get('available') and s['direction'] in ('BULLISH', 'BEARISH')]
    for i in range(len(signal_list)):
        for j in range(i + 1, len(signal_list)):
            si, sj = signal_list[i], signal_list[j]
            if si['direction'] != sj['direction']:
                severity = round((si['value'] + sj['value']) / 2.0, 4)
                contradiction_pairs.append({
                    'phase_a':           si['phase'],
                    'phase_b':           sj['phase'],
                    'phase_a_name':      si['name'],
                    'phase_b_name':      sj['name'],
                    'phase_a_direction': si['direction'],
                    'phase_b_direction': sj['direction'],
                    'severity':          severity,
                })

    # Sort by severity descending
    contradiction_pairs.sort(key=lambda x: x['severity'], reverse=True)

    return {
        'coherence_score':              coherence_score,
        'coherence_level':              coherence_level,
        'contradiction_pairs':          contradiction_pairs,
        'narrative_direction':          narrative,
        'n_bullish':                    n_bullish,
        'n_bearish':                    n_bearish,
        'n_neutral':                    n_neutral,
        'direction_coherence_fraction': round(direction_coherence_fraction, 4),
    }


def compute_coherence(params):
    sig_result = collect_signals(params)
    if 'error' in sig_result and not sig_result.get('signals'):
        return sig_result

    signals = sig_result['signals']
    return _direction_from_signals(signals)

# ---------------------------------------------------------------------------
# Command: bus_directive
# ---------------------------------------------------------------------------

def _get_phase_value(signals, phase_name):
    """Look up the value for a phase by name from signal list."""
    for s in signals:
        if s['name'] == phase_name and s.get('available'):
            return s['value']
    return None


def _compute_directive(signals, coherence):
    """
    Core logic to emit a single directive word and supporting metadata.
    """
    directive  = 'WAIT'
    reason     = ''

    coherence_score      = coherence.get('coherence_score', 0.0)
    narrative_direction  = coherence.get('narrative_direction', 'UNKNOWN')

    # Pull guard-rail phase values
    total_uncertainty = _get_phase_value(signals, 'uncertainty')
    sts               = _get_phase_value(signals, 'observatory')
    mii               = _get_phase_value(signals, 'compression')

    # Convert sts / mii back to raw scale for threshold checks
    sts_raw = (sts * 100.0) if sts is not None else None
    mii_raw = (mii * 100.0) if mii is not None else None

    # --- HALT conditions (safety first) ---
    if total_uncertainty is not None and total_uncertainty > 0.80:
        directive = 'HALT'
        reason    = f'System uncertainty too high ({total_uncertainty:.2f} > 0.80)'
    elif sts_raw is not None and sts_raw < 30:
        directive = 'HALT'
        reason    = f'System health critical (STS={sts_raw:.1f} < 30)'
    elif mii_raw is not None and mii_raw < 15:
        directive = 'HALT'
        reason    = f'Market intelligence index too low (MII={mii_raw:.1f} < 15)'

    # --- DEFENSIVE condition ---
    elif coherence_score < 30:
        directive = 'DEFENSIVE'
        reason    = f'Low cognitive coherence ({coherence_score:.1f} < 30) — protect capital'

    # --- AVOID condition ---
    elif narrative_direction == 'BEARISH' and coherence_score > 50:
        directive = 'AVOID'
        reason    = (f'Bearish narrative with moderate-high coherence '
                     f'({coherence_score:.1f})')

    # --- ENGAGE condition ---
    elif narrative_direction == 'BULLISH' and coherence_score > 65:
        directive = 'ENGAGE'
        reason    = (f'Bullish narrative with high coherence '
                     f'({coherence_score:.1f})')

    # --- WAIT (catch-all) ---
    else:
        directive = 'WAIT'
        reason    = (f'Insufficient coherence or neutral narrative '
                     f'({narrative_direction}, score={coherence_score:.1f})')

    # Confidence = coherence_score normalised 0-1, adjusted by n_available
    n_available  = sum(1 for s in signals if s.get('available'))
    coverage     = n_available / N_PHASES if N_PHASES > 0 else 0.0
    confidence   = round(clamp((coherence_score / 100.0) * coverage), 4)

    # Top-5 contributing signals (highest |value − 0.5| → most opinionated)
    opinionated = sorted(
        [s for s in signals if s.get('available')],
        key=lambda s: abs(s['value'] - 0.5),
        reverse=True,
    )[:5]
    key_signals = [
        {'phase': s['phase'], 'name': s['name'], 'value': s['value'], 'direction': s['direction']}
        for s in opinionated
    ]

    return {
        'directive':   directive,
        'confidence':  confidence,
        'reason':      reason,
        'key_signals': key_signals,
    }


def bus_directive(params):
    sig_result = collect_signals(params)
    if 'error' in sig_result and not sig_result.get('signals'):
        return sig_result

    signals   = sig_result['signals']
    coherence = _direction_from_signals(signals)
    return _compute_directive(signals, coherence)

# ---------------------------------------------------------------------------
# Command: read_bus
# ---------------------------------------------------------------------------

def read_bus(params):
    sig_result = collect_signals(params)
    if 'error' in sig_result and not sig_result.get('signals'):
        return sig_result

    signals   = sig_result['signals']
    coherence = _direction_from_signals(signals)
    directive = _compute_directive(signals, coherence)

    n_available = sig_result['n_available']
    coverage    = n_available / N_PHASES if N_PHASES > 0 else 0.0
    avail_vals  = [s['value'] for s in signals if s.get('available')]
    avg_val     = statistics.mean(avail_vals) if avail_vals else 0.5
    global_confidence = round(
        clamp((coherence['coherence_score'] / 100.0) * coverage * avg_val), 4
    )

    return {
        'signals':           signals,
        'coherence':         coherence,
        'directive':         directive,
        'global_confidence': global_confidence,
        'generated_at':      NOW,
    }

# ---------------------------------------------------------------------------
# Command: contradiction_matrix
# ---------------------------------------------------------------------------

def contradiction_matrix(params):
    sig_result = collect_signals(params)
    if 'error' in sig_result and not sig_result.get('signals'):
        return sig_result

    signals = [s for s in sig_result['signals'] if s.get('available')]

    phase_ids = [s['phase'] for s in signals]
    dir_map   = {s['phase']: s['direction'] for s in signals}

    # Build NxN matrix
    matrix = {}
    pair_scores = []

    for pi in phase_ids:
        matrix[str(pi)] = {}
        for pj in phase_ids:
            di = dir_map[pi]
            dj = dir_map[pj]

            if pi == pj:
                matrix[str(pi)][str(pj)] = 1
                continue

            if di == 'UNKNOWN' or dj == 'UNKNOWN':
                matrix[str(pi)][str(pj)] = 0
            elif di == 'NEUTRAL' or dj == 'NEUTRAL':
                matrix[str(pi)][str(pj)] = 0
            elif di == dj:
                matrix[str(pi)][str(pj)] = 1
            else:
                # One BULLISH one BEARISH → contradiction
                matrix[str(pi)][str(pj)] = -1
                if pi < pj:
                    val_i = next((s['value'] for s in signals if s['phase'] == pi), 0.5)
                    val_j = next((s['value'] for s in signals if s['phase'] == pj), 0.5)
                    name_i = dir_map_name = next((s['name'] for s in signals if s['phase'] == pi), '')
                    name_j = next((s['name'] for s in signals if s['phase'] == pj), '')
                    pair_scores.append({
                        'phase_a':      pi,
                        'phase_b':      pj,
                        'phase_a_name': name_i,
                        'phase_b_name': name_j,
                        'dir_a':        di,
                        'dir_b':        dj,
                        'severity':     round((val_i + val_j) / 2.0, 4),
                    })

    pair_scores.sort(key=lambda x: x['severity'], reverse=True)
    top_5 = pair_scores[:5]

    # Agreement ratio: fraction of off-diagonal known pairs that agree
    total_pairs      = 0
    agreement_count  = 0
    for i, pi in enumerate(phase_ids):
        for j, pj in enumerate(phase_ids):
            if i >= j:
                continue
            di = dir_map[pi]
            dj = dir_map[pj]
            if di in ('UNKNOWN',) or dj in ('UNKNOWN',):
                continue
            total_pairs += 1
            if di == dj or di == 'NEUTRAL' or dj == 'NEUTRAL':
                agreement_count += 1

    agreement_ratio = round(agreement_count / total_pairs, 4) if total_pairs > 0 else 0.0

    return {
        'matrix':                    matrix,
        'most_contradicting_pairs':  top_5,
        'agreement_ratio':           agreement_ratio,
        'n_phases_with_data':        len(phase_ids),
        'generated_at':              NOW,
    }

# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

_CREATE_BUS_SIGNALS = """
CREATE TABLE IF NOT EXISTS bus_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phase        INTEGER,
    phase_name   TEXT,
    signal_type  TEXT,
    value        REAL,
    direction    TEXT,
    urgency      TEXT,
    published_at TEXT
)
"""

_CREATE_BUS_STATE = """
CREATE TABLE IF NOT EXISTS bus_state (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    directive          TEXT,
    coherence_score    REAL,
    coherence_level    TEXT,
    narrative_direction TEXT,
    global_confidence  REAL,
    n_available        INTEGER,
    n_contradictions   INTEGER,
    generated_at       TEXT
)
"""


def build_full(params):
    bus = read_bus(params)
    if 'error' in bus and not bus.get('signals'):
        return bus

    signals   = bus['signals']
    coherence = bus['coherence']
    directive = bus['directive']
    global_confidence = bus.get('global_confidence', 0.0)
    n_available = sum(1 for s in signals if s.get('available'))
    n_contradictions = len(coherence.get('contradiction_pairs', []))

    try:
        conn = get_db()
        conn.execute(_CREATE_BUS_SIGNALS)
        conn.execute(_CREATE_BUS_STATE)

        ts = NOW
        for s in signals:
            conn.execute(
                """INSERT INTO bus_signals
                   (phase, phase_name, signal_type, value, direction, urgency, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    s['phase'],
                    s['name'],
                    s['signal_type'],
                    s['value'],
                    s['direction'],
                    s['urgency'],
                    ts,
                )
            )

        conn.execute(
            """INSERT INTO bus_state
               (directive, coherence_score, coherence_level, narrative_direction,
                global_confidence, n_available, n_contradictions, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                directive['directive'],
                coherence['coherence_score'],
                coherence['coherence_level'],
                coherence['narrative_direction'],
                global_confidence,
                n_available,
                n_contradictions,
                ts,
            )
        )

        conn.commit()
        conn.close()

    except Exception as e:
        return {
            'status':           'error',
            'error':            str(e),
            'directive':        directive.get('directive', 'UNKNOWN'),
            'coherence_score':  coherence.get('coherence_score', 0.0),
        }

    return {
        'status':             'built',
        'directive':          directive['directive'],
        'coherence_score':    coherence['coherence_score'],
        'coherence_level':    coherence['coherence_level'],
        'n_contradictions':   n_contradictions,
        'global_confidence':  global_confidence,
        'narrative_direction': coherence['narrative_direction'],
        'n_available':        n_available,
        'n_missing':          N_PHASES - n_available,
        'generated_at':       NOW,
    }

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'collect_signals':     collect_signals,
    'compute_coherence':   compute_coherence,
    'bus_directive':       bus_directive,
    'read_bus':            read_bus,
    'contradiction_matrix': contradiction_matrix,
    'build_full':          build_full,
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(json.dumps({
            'error':    'Usage: python central_cognitive_bus.py <command> <json_params>',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    params = {}
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({
            'error':    f'Unknown command: {cmd}',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
    except Exception as e:
        result = {'error': str(e), 'command': cmd}

    print(json.dumps(result))
