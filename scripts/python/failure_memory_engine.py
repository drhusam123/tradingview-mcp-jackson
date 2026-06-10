"""
failure_memory_engine.py — Phase 23
EGX Market Intelligence: Failure Memory & Recurrence Intelligence Engine

Commands:
  analyze_all          — classify 30K failures, check recurrence & predictiveness
  classify_failures    — sample-based classification accuracy estimate
  build_families       — cluster failures into named families (7 archetypes)
  find_predictive      — P(explosion | failure) per archetype
  build_recurrence     — compute recurrence probabilities and lag statistics
  daily_failure_scan   — scan recent precursors for high-failure-risk signals
  report               — full summary intelligence report

Usage:
  python failure_memory_engine.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# ---------------------------------------------------------------------------
# DB Setup
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS failure_intelligence (
        failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_date TEXT,
        symbol TEXT,
        failure_archetype TEXT,
        pattern_id INTEGER,
        pattern_name TEXT,
        dominant_force TEXT,
        macro_state TEXT,
        regime TEXT,
        is_recurrent INTEGER DEFAULT 0,
        recurrence_count INTEGER DEFAULT 0,
        becomes_predictive INTEGER DEFAULT 0,
        predictive_direction TEXT,
        predictive_precision REAL,
        family_id INTEGER,
        notes TEXT,
        UNIQUE(symbol, analysis_date, pattern_name)
    );
    CREATE TABLE IF NOT EXISTS failure_families (
        family_id INTEGER PRIMARY KEY AUTOINCREMENT,
        family_name TEXT,
        archetype TEXT,
        n_members INTEGER DEFAULT 0,
        avg_recurrence REAL DEFAULT 0,
        is_predictive INTEGER DEFAULT 0,
        predictive_pattern TEXT,
        signature TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS failure_recurrence_map (
        map_id INTEGER PRIMARY KEY AUTOINCREMENT,
        failure_archetype TEXT,
        triggering_condition TEXT,
        recurrence_probability REAL,
        avg_lag_days REAL,
        regime TEXT,
        evidence_count INTEGER DEFAULT 0
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Failure archetype classification
# ---------------------------------------------------------------------------

ARCHETYPE_MAP = {
    'LOW_MOMENTUM':       'VOLATILITY_SUPPRESSION',
    'REGIME_MISMATCH':    'REGIME_MISMATCH',
    'LIQUIDITY_COLLAPSE': 'LIQUIDITY_DISTORTION',
}

ALL_ARCHETYPES = [
    'VOLATILITY_SUPPRESSION',
    'REGIME_MISMATCH',
    'LIQUIDITY_DISTORTION',
    'MACRO_OVERRIDE',
    'CAUSAL_INVERSION',
    'NOISY_SIGNAL',
    'STRUCTURAL_INVALIDATION',
]


def _classify_failure(row, failure_rate=None):
    """
    Classify a failure_reconstruction row into one of 7 archetypes.
    row: dict-like with keys: failure_class, primary_cause, etc.
    """
    fc = (row['failure_class'] or '').upper()
    pc = (row['primary_cause'] or '').lower() if 'primary_cause' in row.keys() else ''

    # Primary mapping by failure_class
    if fc in ARCHETYPE_MAP:
        archetype = ARCHETYPE_MAP[fc]
    else:
        archetype = 'STRUCTURAL_INVALIDATION'

    # Override by primary_cause context
    if 'macro' in pc:
        archetype = 'MACRO_OVERRIDE'
    elif 'reversal' in pc or 'inversion' in pc:
        archetype = 'CAUSAL_INVERSION'

    # Override by failure_rate if provided
    if failure_rate is not None and failure_rate > 0.8:
        archetype = 'NOISY_SIGNAL'

    return archetype


def _date_str(dt_value):
    """Normalize various date formats to YYYY-MM-DD string."""
    if dt_value is None:
        return None
    s = str(dt_value)
    return s[:10]


def _days_between(d1_str, d2_str):
    """Return abs(days) between two YYYY-MM-DD strings. -1 if parse fails."""
    try:
        d1 = datetime.strptime(d1_str[:10], '%Y-%m-%d')
        d2 = datetime.strptime(d2_str[:10], '%Y-%m-%d')
        return abs((d2 - d1).days)
    except (ValueError, TypeError):
        return -1


def _signed_days(d1_str, d2_str):
    """Return signed days (d2 - d1)."""
    try:
        d1 = datetime.strptime(d1_str[:10], '%Y-%m-%d')
        d2 = datetime.strptime(d2_str[:10], '%Y-%m-%d')
        return (d2 - d1).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Command: analyze_all
# ---------------------------------------------------------------------------

def cmd_analyze_all(params):
    t0 = time.time()
    db = get_db()

    # Load all failure_reconstruction
    try:
        failures = db.execute("""
            SELECT id, failure_date, symbol, law_id, law_name,
                   direction, failure_class, primary_cause
            FROM failure_reconstruction
            ORDER BY symbol, failure_date
        """).fetchall()
    except Exception as e:
        db.close()
        return {'error': f'Cannot read failure_reconstruction: {e}'}

    n_total = len(failures)

    # Load failure taxonomy for failure rates
    try:
        taxonomy = db.execute("SELECT * FROM failure_taxonomy").fetchall()
    except Exception:
        taxonomy = []
    law_failure_rate = {r['pattern_id']: r['failure_rate'] for r in taxonomy
                        if 'pattern_id' in r.keys() and 'failure_rate' in r.keys()}

    # Load explosive_moves for predictive check
    try:
        explosions = db.execute("""
            SELECT symbol, explosion_date, direction
            FROM explosive_moves
            ORDER BY symbol, explosion_date
        """).fetchall()
    except Exception:
        explosions = []

    # Index explosions by symbol for fast lookup
    explosion_index = defaultdict(list)
    for ex in explosions:
        sym = ex['symbol']
        dt = _date_str(ex['explosion_date'])
        if dt:
            explosion_index[sym].append((dt, ex['direction']))

    # Load regime_history for regime classification
    try:
        regime_rows = db.execute("SELECT * FROM regime_history").fetchall()
    except Exception:
        regime_rows = []
    regime_map = {}
    for r in regime_rows:
        cols = r.keys()
        date_col = next((c for c in cols if 'date' in c.lower()), None)
        regime_col = next((c for c in cols if 'regime' in c.lower()), None)
        if date_col and regime_col:
            regime_map[str(r[date_col])[:10]] = r[regime_col]

    # Build per-symbol failure timeline for recurrence detection
    sym_failures = defaultdict(list)
    for f in failures:
        sym = f['symbol']
        dt = _date_str(f['failure_date'])
        law = f['law_id'] or f['law_name'] or ''
        sym_failures[sym].append((dt, law, dict(f)))

    archetype_dist = Counter()
    n_recurrent = 0
    n_predictive = 0
    inserted = 0
    analysis_date = datetime.utcnow().strftime('%Y-%m-%d')

    BATCH = 500
    batch_rows = []

    for f in failures:
        sym = f['symbol']
        dt = _date_str(f['failure_date'])
        if not dt:
            continue

        law_id = f['law_id']
        failure_rate = law_failure_rate.get(law_id)
        archetype = _classify_failure(f, failure_rate)
        archetype_dist[archetype] += 1

        regime = regime_map.get(dt, 'UNKNOWN')

        # Recurrence: same symbol + same law within 60 days
        is_recurrent = 0
        recurrence_count = 0
        prev_occurrences = sym_failures[sym]
        for prev_dt, prev_law, _ in prev_occurrences:
            if prev_dt == dt:
                continue
            if prev_law == (law_id or f['law_name'] or ''):
                delta = _days_between(dt, prev_dt)
                if 0 < delta <= 60:
                    recurrence_count += 1
                    is_recurrent = 1
        if is_recurrent:
            n_recurrent += 1

        # Predictive: failure followed by explosive move within 10 days
        becomes_predictive = 0
        predictive_direction = None
        predictive_precision = None
        for ex_dt, ex_dir in explosion_index.get(sym, []):
            delta = _signed_days(dt, ex_dt)
            if delta is not None and 0 < delta <= 10:
                becomes_predictive = 1
                predictive_direction = ex_dir
                predictive_precision = 1.0  # confirmed
                n_predictive += 1
                break

        # Dominant force from law_name
        law_name = f['law_name'] or ''
        dominant_force = 'MOMENTUM' if 'momentum' in law_name.lower() else \
                         'LIQUIDITY' if 'liquidity' in law_name.lower() else \
                         'REGIME' if 'regime' in law_name.lower() else \
                         'MACRO' if 'macro' in law_name.lower() else 'UNKNOWN'

        batch_rows.append((
            analysis_date, sym, archetype,
            f['law_id'], f['law_name'],
            dominant_force, None, regime,
            is_recurrent, recurrence_count,
            becomes_predictive, predictive_direction, predictive_precision,
            None, None
        ))

        if len(batch_rows) >= BATCH:
            db.executemany("""
                INSERT OR IGNORE INTO failure_intelligence
                (analysis_date, symbol, failure_archetype, pattern_id, pattern_name,
                 dominant_force, macro_state, regime,
                 is_recurrent, recurrence_count,
                 becomes_predictive, predictive_direction, predictive_precision,
                 family_id, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, batch_rows)
            inserted += len(batch_rows)
            batch_rows = []

    if batch_rows:
        db.executemany("""
            INSERT OR IGNORE INTO failure_intelligence
            (analysis_date, symbol, failure_archetype, pattern_id, pattern_name,
             dominant_force, macro_state, regime,
             is_recurrent, recurrence_count,
             becomes_predictive, predictive_direction, predictive_precision,
             family_id, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch_rows)
        inserted += len(batch_rows)

    db.commit()
    db.close()

    elapsed = round(time.time() - t0, 2)
    return {
        'n_analyzed': n_total,
        'n_inserted': inserted,
        'archetype_distribution': dict(archetype_dist),
        'n_recurrent': n_recurrent,
        'n_predictive': n_predictive,
        'execution_time': elapsed
    }


# ---------------------------------------------------------------------------
# Command: classify_failures
# ---------------------------------------------------------------------------

def cmd_classify_failures(params):
    limit = params.get('limit', 1000)
    db = get_db()

    try:
        failures = db.execute(f"""
            SELECT id, failure_date, symbol, law_id, law_name,
                   failure_class, primary_cause
            FROM failure_reconstruction
            LIMIT {int(limit)}
        """).fetchall()
    except Exception as e:
        db.close()
        return {'error': str(e)}

    try:
        taxonomy = db.execute("SELECT * FROM failure_taxonomy").fetchall()
    except Exception:
        taxonomy = []

    law_failure_rate = {r['pattern_id']: r['failure_rate'] for r in taxonomy
                        if 'pattern_id' in r.keys() and 'failure_rate' in r.keys()}

    counts = Counter()
    class_to_archetype = Counter()
    for f in failures:
        rate = law_failure_rate.get(f['law_id'])
        archetype = _classify_failure(f, rate)
        counts[archetype] += 1
        class_to_archetype[(f['failure_class'], archetype)] += 1

    # Accuracy estimate: fraction where mapping is unambiguous
    unambiguous = sum(1 for f in failures
                      if (f['failure_class'] or '') in ARCHETYPE_MAP and
                         'macro' not in (f['primary_cause'] or '').lower() and
                         'reversal' not in (f['primary_cause'] or '').lower())
    accuracy_estimate = round(unambiguous / len(failures), 3) if failures else 0.0

    db.close()
    return {
        'n_sampled': len(failures),
        'archetype_distribution': dict(counts),
        'accuracy_estimate': accuracy_estimate,
        'class_archetype_breakdown': {
            f"{k[0]}->{k[1]}": v for k, v in class_to_archetype.most_common(20)
        }
    }


# ---------------------------------------------------------------------------
# Command: build_families
# ---------------------------------------------------------------------------

def _make_family_name(archetype, regime, dominant_force):
    """Generate a descriptive family name."""
    parts = []
    if regime and regime not in ('UNKNOWN', 'None'):
        parts.append(regime.upper())
    if dominant_force and dominant_force not in ('UNKNOWN',):
        parts.append(dominant_force.upper())
    parts.append(archetype.replace('_', ''))
    name = '_'.join(parts[:3])
    return name[:80]


def cmd_build_families(params):
    db = get_db()
    now_str = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    try:
        rows = db.execute("""
            SELECT failure_archetype, regime, dominant_force,
                   COUNT(*) as n,
                   SUM(is_recurrent) as n_recurrent,
                   SUM(becomes_predictive) as n_predictive,
                   AVG(recurrence_count) as avg_rec
            FROM failure_intelligence
            GROUP BY failure_archetype, regime, dominant_force
            ORDER BY n DESC
        """).fetchall()
    except Exception as e:
        db.close()
        return {'error': str(e)}

    # Aggregate into top-7 families (one per archetype)
    archetype_families = defaultdict(list)
    for r in rows:
        archetype_families[r['failure_archetype']].append(dict(r))

    n_families = 0
    family_distribution = {}

    # Clear existing families for rebuild
    db.execute("DELETE FROM failure_families")

    for archetype in ALL_ARCHETYPES:
        members = archetype_families.get(archetype, [])
        if not members:
            # Insert empty family placeholder
            total_n = 0
            family_name = f"EMPTY_{archetype}"
            avg_rec = 0.0
            is_predictive = 0
        else:
            total_n = sum(m['n'] for m in members)
            total_rec = sum(m['n_recurrent'] or 0 for m in members)
            total_pred = sum(m['n_predictive'] or 0 for m in members)
            avg_rec = sum((m['avg_rec'] or 0) * m['n'] for m in members) / total_n if total_n else 0
            is_predictive = 1 if total_n > 0 and (total_pred / total_n) > 0.05 else 0

            # Pick the dominant regime+force combo
            top = members[0]
            family_name = _make_family_name(
                archetype,
                top.get('regime', 'UNKNOWN'),
                top.get('dominant_force', 'UNKNOWN')
            )

        # Signature: top-regime breakdown
        sig_parts = []
        for m in members[:3]:
            regime = m.get('regime', '?') or '?'
            force = m.get('dominant_force', '?') or '?'
            sig_parts.append(f"{regime}:{force}={m['n']}")
        signature = '; '.join(sig_parts)

        db.execute("""
            INSERT INTO failure_families
            (family_name, archetype, n_members, avg_recurrence,
             is_predictive, predictive_pattern, signature, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (family_name, archetype, total_n, round(avg_rec, 3),
              is_predictive, None, signature, now_str, now_str))

        family_distribution[archetype] = {
            'family_name': family_name,
            'n_members': total_n,
            'is_predictive': is_predictive
        }
        n_families += 1

    # Now assign family_id back to failure_intelligence rows
    families_in_db = db.execute("SELECT family_id, archetype FROM failure_families").fetchall()
    archetype_to_fid = {r['archetype']: r['family_id'] for r in families_in_db}

    for archetype, fid in archetype_to_fid.items():
        db.execute("""
            UPDATE failure_intelligence SET family_id=?
            WHERE failure_archetype=?
        """, (fid, archetype))

    db.commit()
    db.close()

    return {
        'n_families': n_families,
        'family_distribution': family_distribution
    }


# ---------------------------------------------------------------------------
# Command: find_predictive
# ---------------------------------------------------------------------------

def cmd_find_predictive(params):
    min_precision = params.get('min_precision', 0.3)
    db = get_db()
    now_str = datetime.utcnow().strftime('%Y-%m-%d')

    try:
        rows = db.execute("""
            SELECT failure_archetype,
                   COUNT(*) as total,
                   SUM(becomes_predictive) as n_predictive,
                   predictive_direction
            FROM failure_intelligence
            GROUP BY failure_archetype, predictive_direction
        """).fetchall()
    except Exception as e:
        db.close()
        return {'error': str(e)}

    # Aggregate by archetype
    archetype_stats = defaultdict(lambda: {'total': 0, 'n_pred': 0, 'directions': Counter()})
    for r in rows:
        arch = r['failure_archetype']
        archetype_stats[arch]['total'] += r['total']
        archetype_stats[arch]['n_pred'] += (r['n_predictive'] or 0)
        if r['predictive_direction']:
            archetype_stats[arch]['directions'][r['predictive_direction']] += (r['n_predictive'] or 0)

    predictive_archetypes = []
    precision_scores = {}

    for arch, stats in archetype_stats.items():
        total = stats['total']
        n_pred = stats['n_pred']
        if total == 0:
            continue
        precision = n_pred / total

        # Dominant direction
        top_dir = stats['directions'].most_common(1)
        direction = top_dir[0][0] if top_dir else None

        precision_scores[arch] = {
            'precision': round(precision, 4),
            'n_total': total,
            'n_predictive': n_pred,
            'dominant_direction': direction
        }

        if precision >= min_precision:
            predictive_archetypes.append(arch)
            # Update failure_intelligence
            db.execute("""
                UPDATE failure_intelligence
                SET becomes_predictive=1, predictive_precision=?, predictive_direction=?
                WHERE failure_archetype=?
            """, (round(precision, 4), direction, arch))
            # Update failure_families
            db.execute("""
                UPDATE failure_families
                SET is_predictive=1, predictive_pattern=?, updated_at=?
                WHERE archetype=?
            """, (f"precision={precision:.3f},dir={direction}", now_str, arch))

    db.commit()
    db.close()

    return {
        'predictive_archetypes': predictive_archetypes,
        'precision_scores': precision_scores,
        'min_precision_threshold': min_precision
    }


# ---------------------------------------------------------------------------
# Command: build_recurrence
# ---------------------------------------------------------------------------

def cmd_build_recurrence(params):
    db = get_db()

    # Load raw failure data with dates for lag computation
    try:
        raw = db.execute("""
            SELECT fr.symbol, fr.failure_date, fr.failure_class, fr.primary_cause, fr.law_id
            FROM failure_reconstruction fr
            ORDER BY fr.symbol, fr.failure_date
        """).fetchall()
    except Exception as e:
        db.close()
        return {'error': str(e)}

    try:
        taxonomy = db.execute("SELECT * FROM failure_taxonomy").fetchall()
    except Exception:
        taxonomy = []
    law_failure_rate = {r['pattern_id']: r['failure_rate'] for r in taxonomy
                        if 'pattern_id' in r.keys() and 'failure_rate' in r.keys()}

    # Load regime history
    try:
        regime_rows = db.execute("SELECT * FROM regime_history").fetchall()
    except Exception:
        regime_rows = []
    regime_map = {}
    for r in regime_rows:
        cols = r.keys()
        dc = next((c for c in cols if 'date' in c.lower()), None)
        rc = next((c for c in cols if 'regime' in c.lower()), None)
        if dc and rc:
            regime_map[str(r[dc])[:10]] = r[rc]

    # Group failures by (archetype, regime)
    # For each group, compute:
    #   P(recurrence within 5/10/20 days)
    #   average lag between consecutive failures
    group_events = defaultdict(list)  # (archetype, regime) → [date_str]

    for f in raw:
        dt = _date_str(f['failure_date'])
        if not dt:
            continue
        rate = law_failure_rate.get(f['law_id'])
        archetype = _classify_failure(f, rate)
        regime = regime_map.get(dt, 'UNKNOWN')
        group_events[(archetype, regime)].append(dt)

    # Clear existing recurrence map
    db.execute("DELETE FROM failure_recurrence_map")

    n_patterns = 0
    recurrence_stats = {}

    for (archetype, regime), dates in group_events.items():
        dates_sorted = sorted(dates)
        n = len(dates_sorted)
        if n < 5:
            continue

        lags = []
        recur_5 = recur_10 = recur_20 = 0

        for i in range(1, n):
            delta = _days_between(dates_sorted[i - 1], dates_sorted[i])
            if delta > 0:
                lags.append(delta)
                if delta <= 5:
                    recur_5 += 1
                if delta <= 10:
                    recur_10 += 1
                if delta <= 20:
                    recur_20 += 1

        n_pairs = n - 1
        p5 = recur_5 / n_pairs if n_pairs else 0
        p10 = recur_10 / n_pairs if n_pairs else 0
        p20 = recur_20 / n_pairs if n_pairs else 0
        avg_lag = sum(lags) / len(lags) if lags else 0

        # Insert into failure_recurrence_map for each window
        for window, prob, label in [(5, p5, '5d'), (10, p10, '10d'), (20, p20, '20d')]:
            db.execute("""
                INSERT INTO failure_recurrence_map
                (failure_archetype, triggering_condition, recurrence_probability,
                 avg_lag_days, regime, evidence_count)
                VALUES (?,?,?,?,?,?)
            """, (archetype, f'recurrence_within_{label}',
                  round(prob, 4), round(avg_lag, 1), regime, n_pairs))

        key = f"{archetype}|{regime}"
        recurrence_stats[key] = {
            'n_events': n,
            'p_recur_5d': round(p5, 3),
            'p_recur_10d': round(p10, 3),
            'p_recur_20d': round(p20, 3),
            'avg_lag_days': round(avg_lag, 1)
        }
        n_patterns += 1

    db.commit()
    db.close()

    return {
        'n_patterns': n_patterns,
        'recurrence_stats': dict(list(recurrence_stats.items())[:30])  # cap output
    }


# ---------------------------------------------------------------------------
# Command: daily_failure_scan
# ---------------------------------------------------------------------------

def cmd_daily_failure_scan(params):
    date_param = params.get('date', 'today')

    if date_param == 'today':
        scan_date = datetime.utcnow().strftime('%Y-%m-%d')
    else:
        scan_date = date_param[:10]

    try:
        scan_dt = datetime.strptime(scan_date, '%Y-%m-%d')
    except ValueError:
        scan_dt = datetime.utcnow()
        scan_date = scan_dt.strftime('%Y-%m-%d')

    lookback_start = (scan_dt - timedelta(days=5)).strftime('%Y-%m-%d')

    db = get_db()

    # Get recent precursor activations
    try:
        precursors = db.execute("""
            SELECT ce.symbol, ce.precursor_date, ce.pattern_id, ce.pattern_name,
                   ce.outcome, ce.feature_value, ce.next_max_return, ce.regime, ce.sector
            FROM counterfactual_events ce
            WHERE ce.precursor_date >= ? AND ce.precursor_date <= ?
            ORDER BY ce.precursor_date DESC
        """, (lookback_start, scan_date)).fetchall()
    except Exception:
        precursors = []

    # Load recurrence map for archetype risk
    try:
        recurrence_map = db.execute("""
            SELECT * FROM failure_recurrence_map
            WHERE recurrence_probability > 0.4
            ORDER BY recurrence_probability DESC
        """).fetchall()
    except Exception:
        recurrence_map = []

    high_risk_archetypes = {r['failure_archetype'] for r in recurrence_map}

    # Load failure_intelligence for recent patterns
    try:
        recent_failures = db.execute("""
            SELECT fi.symbol, fi.failure_archetype, fi.pattern_name,
                   fi.regime, fi.is_recurrent, fi.becomes_predictive,
                   fi.predictive_direction, fi.predictive_precision,
                   fi.analysis_date
            FROM failure_intelligence fi
            WHERE fi.analysis_date >= ?
            ORDER BY fi.analysis_date DESC
            LIMIT 200
        """, (lookback_start,)).fetchall()
    except Exception:
        recent_failures = []

    # Match precursors to failure families
    try:
        families = db.execute("""
            SELECT ff.family_id, ff.family_name, ff.archetype,
                   ff.is_predictive, ff.predictive_pattern
            FROM failure_families ff
            WHERE ff.is_predictive = 1
        """).fetchall()
    except Exception:
        families = []

    predictive_archetypes = {f['archetype'] for f in families if f['is_predictive']}

    failure_warnings = []

    # Check each precursor
    for prec in precursors:
        sym = prec['symbol']
        pdt = _date_str(prec['precursor_date'])
        pattern_name = prec['pattern_name'] or ''
        regime = prec['regime'] or 'UNKNOWN'

        # Check if recent failure exists for this symbol
        matching = [f for f in recent_failures
                    if f['symbol'] == sym and f['is_recurrent']]

        # Classify the precursor pattern into archetype
        fc_guess = 'LOW_MOMENTUM'  # default
        if 'liquidity' in pattern_name.lower():
            fc_guess = 'LIQUIDITY_COLLAPSE'
        elif 'regime' in pattern_name.lower():
            fc_guess = 'REGIME_MISMATCH'

        mock_row = type('Row', (), {
            'failure_class': fc_guess,
            'primary_cause': pattern_name,
            'keys': lambda: ['failure_class', 'primary_cause']
        })()

        class _MockRow:
            def __init__(self, fc, pc):
                self._fc = fc
                self._pc = pc
            def __getitem__(self, k):
                if k == 'failure_class': return self._fc
                if k == 'primary_cause': return self._pc
                return None
            def keys(self):
                return ['failure_class', 'primary_cause']

        mr = _MockRow(fc_guess, pattern_name)
        archetype = _classify_failure(mr)
        is_high_risk = archetype in high_risk_archetypes or archetype in predictive_archetypes

        if is_high_risk or matching:
            frm = next((r for r in recurrence_map
                        if r['failure_archetype'] == archetype), None)
            prob = frm['recurrence_probability'] if frm else None
            warning = {
                'symbol': sym,
                'precursor_date': pdt,
                'pattern_name': pattern_name,
                'archetype': archetype,
                'regime': regime,
                'is_recurrent_signal': bool(matching),
                'high_risk_archetype': is_high_risk,
                'recurrence_prob_10d': round(prob, 3) if prob else None
            }
            failure_warnings.append(warning)

    db.close()

    return {
        'scan_date': scan_date,
        'lookback_start': lookback_start,
        'n_active_precursors': len(precursors),
        'n_recent_failures': len(recent_failures),
        'failure_warnings': failure_warnings[:50]
    }


# ---------------------------------------------------------------------------
# Command: report
# ---------------------------------------------------------------------------

def cmd_report(params):
    db = get_db()

    # failure_intelligence summary
    try:
        fi_summary = db.execute("""
            SELECT failure_archetype,
                   COUNT(*) as n,
                   SUM(is_recurrent) as n_recurrent,
                   SUM(becomes_predictive) as n_predictive,
                   AVG(recurrence_count) as avg_recurrence,
                   AVG(predictive_precision) as avg_precision
            FROM failure_intelligence
            GROUP BY failure_archetype
            ORDER BY n DESC
        """).fetchall()
    except Exception as e:
        fi_summary = []

    # failure_families summary
    try:
        ff_summary = db.execute("""
            SELECT family_name, archetype, n_members,
                   avg_recurrence, is_predictive, signature
            FROM failure_families
            ORDER BY n_members DESC
        """).fetchall()
    except Exception:
        ff_summary = []

    # failure_recurrence_map summary
    try:
        frm_summary = db.execute("""
            SELECT failure_archetype, triggering_condition,
                   MAX(recurrence_probability) as max_prob,
                   AVG(avg_lag_days) as avg_lag,
                   SUM(evidence_count) as total_evidence
            FROM failure_recurrence_map
            GROUP BY failure_archetype, triggering_condition
            ORDER BY max_prob DESC
            LIMIT 30
        """).fetchall()
    except Exception:
        frm_summary = []

    # Top predictive archetypes
    try:
        top_predictive = db.execute("""
            SELECT failure_archetype,
                   AVG(predictive_precision) as avg_precision,
                   COUNT(*) as n,
                   predictive_direction
            FROM failure_intelligence
            WHERE becomes_predictive = 1
            GROUP BY failure_archetype, predictive_direction
            ORDER BY avg_precision DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        top_predictive = []

    # Totals
    try:
        totals = db.execute("""
            SELECT COUNT(*) as total,
                   SUM(is_recurrent) as total_recurrent,
                   SUM(becomes_predictive) as total_predictive
            FROM failure_intelligence
        """).fetchone()
    except Exception:
        totals = None

    db.close()

    def _row_to_dict(r):
        if r is None:
            return {}
        return {k: r[k] for k in r.keys()}

    return {
        'total_failures_analyzed': _row_to_dict(totals).get('total', 0),
        'total_recurrent': _row_to_dict(totals).get('total_recurrent', 0),
        'total_predictive': _row_to_dict(totals).get('total_predictive', 0),
        'archetype_breakdown': [_row_to_dict(r) for r in fi_summary],
        'failure_families': [_row_to_dict(r) for r in ff_summary],
        'recurrence_map': [_row_to_dict(r) for r in frm_summary],
        'top_predictive_archetypes': [_row_to_dict(r) for r in top_predictive]
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'analyze_all': cmd_analyze_all,
    'classify_failures': cmd_classify_failures,
    'build_families': cmd_build_families,
    'find_predictive': cmd_find_predictive,
    'build_recurrence': cmd_build_recurrence,
    'daily_failure_scan': cmd_daily_failure_scan,
    'report': cmd_report,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'error': 'Usage: failure_memory_engine.py <command> [json_params]',
            'commands': list(COMMANDS.keys())
        }))
        sys.exit(1)

    command = sys.argv[1]
    raw_params = sys.argv[2] if len(sys.argv) > 2 else '{}'

    try:
        params = json.loads(raw_params)
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys())
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        import traceback
        print(json.dumps({
            'error': str(e),
            'traceback': traceback.format_exc()
        }))
        sys.exit(1)

    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
