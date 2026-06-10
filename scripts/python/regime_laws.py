"""
regime_laws.py — Regime-conditioned law activation for EGX universal laws.

Applies regime conditioning to all universal_laws_p16 entries using
market_experience data to find when each law actually works best,
dramatically improving precision by filtering activations to the right
market environment.

Commands:
  analyze_conditions        — compute regime×breadth precision matrix for every law
  conditioned_signals       — return only laws active given current regime/breadth
  law_matrix                — full law × regime precision table
  update_conditions         — write best conditions back to universal_laws_p16
  law_performance_history   — early vs late precision trajectory per law
  build_full                — analyze_conditions + conditioned_signals + update_conditions

Usage:
  python regime_laws.py analyze_conditions
  python regime_laws.py conditioned_signals '{"date": "2026-05-06"}'
  python regime_laws.py law_matrix
  python regime_laws.py update_conditions
  python regime_laws.py law_performance_history '{"law_id": "LAW_001"}'
  python regime_laws.py build_full
"""

import os
import sys
import json
import sqlite3
import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create the two new tables if they don't already exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS regime_law_conditions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            law_id              TEXT NOT NULL,
            law_name            TEXT,
            direction           TEXT,
            regime              TEXT NOT NULL,
            breadth_condition   TEXT,
            n_activations       INTEGER,
            n_hits              INTEGER,
            precision           REAL,
            baseline_precision  REAL,
            lift                REAL,
            is_recommended      INTEGER DEFAULT 0,
            analyzed_at         TEXT DEFAULT (datetime('now')),
            UNIQUE(law_id, regime, breadth_condition)
        );

        CREATE TABLE IF NOT EXISTS conditioned_signals (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date             TEXT NOT NULL,
            symbol                  TEXT NOT NULL,
            law_id                  TEXT,
            law_name                TEXT,
            direction               TEXT,
            active_regime           TEXT,
            conditioned_precision   REAL,
            unconditioned_precision REAL,
            precision_lift          REAL,
            conviction_tier         TEXT,
            created_at              TEXT DEFAULT (datetime('now')),
            UNIQUE(signal_date, symbol, law_id)
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data-access helpers
# ---------------------------------------------------------------------------

def get_all_laws(conn):
    """Return all rows from universal_laws_p16 as plain dicts."""
    rows = conn.execute(
        "SELECT pattern_id, pattern_name, direction, precision, "
        "       best_regime, best_regime_precision, random_baseline_precision "
        "FROM universal_laws_p16 ORDER BY pattern_id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_current_regime(conn, date=None):
    """Return the most recent (or on/before date) regime from regime_history."""
    try:
        if date:
            row = conn.execute(
                "SELECT regime FROM regime_history WHERE date <= ? ORDER BY date DESC LIMIT 1",
                (date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return row['regime'] if row else 'UNKNOWN'
    except Exception:
        return 'UNKNOWN'


def get_current_breadth(conn, date=None):
    """Return the most recent breadth record as a dict."""
    try:
        if date:
            row = conn.execute(
                "SELECT * FROM market_breadth_daily WHERE date <= ? ORDER BY date DESC LIMIT 1",
                (date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM market_breadth_daily ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def breadth_signal_to_condition(signal: str) -> str:
    """Map a raw breadth signal string to STRONG / WEAK / NEUTRAL."""
    if not signal:
        return 'NEUTRAL'
    s = signal.upper()
    if any(x in s for x in ('BULL', 'LEAN_BULL', 'BREADTH_BULL')):
        return 'STRONG'
    if any(x in s for x in ('BEAR', 'LEAN_BEAR', 'BREADTH_BEAR')):
        return 'WEAK'
    return 'NEUTRAL'


def get_breadth_signal_for_date(conn: sqlite3.Connection, date: str) -> str:
    """Return STRONG/WEAK/NEUTRAL for a given date."""
    try:
        row = conn.execute(
            "SELECT signal FROM market_breadth_daily WHERE date <= ? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        return breadth_signal_to_condition(row['signal'] if row else '')
    except Exception:
        return 'NEUTRAL'


def precision_from_outcomes(outcomes):
    """Compute hit-rate precision from a list of outcome strings."""
    if not outcomes:
        return 0.0
    hits = sum(1 for o in outcomes if o == 'HIT')
    return round(hits / len(outcomes), 4)


def conviction_tier(conditioned_precision: float, lift: float) -> str:
    """Assign conviction tier based on conditioned precision and lift."""
    if conditioned_precision >= 0.15 and lift >= 2.0:
        return 'HIGH'
    if conditioned_precision >= 0.10 and lift >= 1.5:
        return 'MEDIUM'
    if conditioned_precision >= 0.06:
        return 'LOW'
    return 'BELOW_BASELINE'


# ---------------------------------------------------------------------------
# Command: analyze_conditions
# ---------------------------------------------------------------------------

def analyze_conditions(params: dict) -> dict:
    """
    For every law in universal_laws_p16, compute precision broken down by
    regime × breadth_condition and store results in regime_law_conditions.
    """
    conn = get_db()
    ensure_tables(conn)

    laws = get_all_laws(conn)
    if not laws:
        return {'error': 'No laws found in universal_laws_p16'}

    # Pre-load all relevant market_experience rows once to avoid N+1 queries.
    # We join with market_breadth_daily to get breadth signals.
    exp_rows = conn.execute("""
        SELECT
            me.law_id,
            me.law_name,
            me.direction,
            me.outcome,
            me.regime,
            me.event_date,
            mbd.signal AS breadth_signal
        FROM market_experience me
        LEFT JOIN market_breadth_daily mbd
            ON mbd.date = me.event_date
        WHERE me.law_id IS NOT NULL
    """).fetchall()

    # Group: law_id → regime → breadth_condition → [outcomes]
    grouped = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    law_meta = {}

    for row in exp_rows:
        lid = row['law_id']
        regime = row['regime'] or 'UNKNOWN'
        bc = breadth_signal_to_condition(row['breadth_signal'] or '')
        grouped[lid][regime][bc].append(row['outcome'])
        if lid not in law_meta:
            law_meta[lid] = {
                'law_name': row['law_name'],
                'direction': row['direction'],
            }

    # Merge with laws that may have zero experience rows
    for law in laws:
        lid = law['pattern_id']
        if lid not in law_meta:
            law_meta[lid] = {
                'law_name': law['pattern_name'],
                'direction': law['direction'],
            }

    summary_rows = []
    improved_count = 0
    precision_lifts = []

    for law in laws:
        lid = law['pattern_id']
        overall_baseline = law['random_baseline_precision'] or law['precision'] or 0.0
        overall_precision = law['precision'] or 0.0
        meta = law_meta.get(lid, {})

        law_conditions = []
        best_prec = 0.0
        best_combo: dict | None = None

        regimes = grouped.get(lid, {})
        all_regimes = {'BULL', 'BEAR', 'CHOPPY', 'UNKNOWN'}
        for regime in all_regimes:
            bc_map = regimes.get(regime, {})
            breadth_conditions = {'STRONG', 'WEAK', 'NEUTRAL', 'ANY'}

            # Build an 'ANY' breadth bucket by merging all
            all_outcomes_in_regime = []
            for bc, outcomes in bc_map.items():
                all_outcomes_in_regime.extend(outcomes)

            for bc in breadth_conditions:
                if bc == 'ANY':
                    outcomes = all_outcomes_in_regime
                else:
                    outcomes = bc_map.get(bc, [])

                n = len(outcomes)
                if n == 0:
                    continue

                prec = precision_from_outcomes(outcomes)
                hits = sum(1 for o in outcomes if o == 'HIT')
                lift_val = round(prec / overall_baseline, 3) if overall_baseline > 0 else 0.0

                cond = {
                    'law_id': lid,
                    'law_name': meta.get('law_name', ''),
                    'direction': meta.get('direction', ''),
                    'regime': regime,
                    'breadth_condition': bc,
                    'n_activations': n,
                    'n_hits': hits,
                    'precision': prec,
                    'baseline_precision': overall_baseline,
                    'lift': lift_val,
                    'is_recommended': 0,
                }
                law_conditions.append(cond)

                if prec > best_prec and n >= 3:
                    best_prec = prec
                    best_combo = cond

        # Mark the best combination
        if best_combo and best_prec > overall_precision:
            best_combo['is_recommended'] = 1
            improved_count += 1
            lift_gain = (best_prec - overall_precision) / overall_precision if overall_precision > 0 else 0
            precision_lifts.append(lift_gain)

        # Upsert all conditions into DB
        for cond in law_conditions:
            conn.execute("""
                INSERT INTO regime_law_conditions
                    (law_id, law_name, direction, regime, breadth_condition,
                     n_activations, n_hits, precision, baseline_precision,
                     lift, is_recommended, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(law_id, regime, breadth_condition)
                DO UPDATE SET
                    n_activations=excluded.n_activations,
                    n_hits=excluded.n_hits,
                    precision=excluded.precision,
                    baseline_precision=excluded.baseline_precision,
                    lift=excluded.lift,
                    is_recommended=excluded.is_recommended,
                    analyzed_at=excluded.analyzed_at
            """, (
                cond['law_id'], cond['law_name'], cond['direction'],
                cond['regime'], cond['breadth_condition'],
                cond['n_activations'], cond['n_hits'], cond['precision'],
                cond['baseline_precision'], cond['lift'], cond['is_recommended'],
            ))

        summary_rows.append({
            'law_id': lid,
            'law_name': meta.get('law_name', ''),
            'overall_precision': overall_precision,
            'best_conditioned_precision': round(best_prec, 4),
            'best_condition': f"{best_combo['regime']}×{best_combo['breadth_condition']}" if best_combo else 'N/A',
            'improved': bool(best_combo and best_prec > overall_precision),
        })

    conn.commit()
    conn.close()

    avg_lift = round(sum(precision_lifts) / len(precision_lifts), 3) if precision_lifts else 0.0
    return {
        'success': True,
        'total_laws': len(laws),
        'laws_with_experience_data': len([r for r in summary_rows if r['best_conditioned_precision'] > 0]),
        'laws_improved_by_conditioning': improved_count,
        'average_precision_lift_pct': round(avg_lift * 100, 1),
        'conditions_stored': 'regime_law_conditions',
        'law_summary': summary_rows,
    }


# ---------------------------------------------------------------------------
# Command: conditioned_signals
# ---------------------------------------------------------------------------

def conditioned_signals(params: dict) -> dict:
    """
    Given current (or specified) regime/breadth state, return only laws
    whose recommended condition matches, filtered to recent scan signals.
    """
    conn = get_db()
    ensure_tables(conn)

    target_date = params.get('date', datetime.date.today().isoformat())
    lookback_days = params.get('lookback_days', 5)

    current_regime = get_current_regime(conn, target_date)
    breadth_data = get_current_breadth(conn, target_date)
    current_breadth_signal = breadth_data.get('signal', '')
    current_bc = breadth_signal_to_condition(current_breadth_signal)
    current_breadth_score = breadth_data.get('breadth_score', 50.0) or 50.0

    # Get all recommended conditions matching current regime
    recommended = conn.execute("""
        SELECT rlc.*, ul.precision AS overall_precision
        FROM regime_law_conditions rlc
        JOIN universal_laws_p16 ul ON ul.pattern_id = rlc.law_id
        WHERE rlc.is_recommended = 1
          AND rlc.regime = ?
          AND (rlc.breadth_condition = 'ANY' OR rlc.breadth_condition = ?)
        ORDER BY rlc.lift DESC
    """, (current_regime, current_bc)).fetchall()

    active_law_ids = {row['law_id']: dict(row) for row in recommended}

    # Get recent scans
    cutoff = (datetime.date.fromisoformat(target_date) - datetime.timedelta(days=lookback_days)).isoformat()
    scans = conn.execute("""
        SELECT symbol, scan_date, setup_type, score, close_price,
               entry_low, entry_high, stop_loss, t1, t2, rr1, rr2, confidence
        FROM scans
        WHERE scan_date >= ? AND scan_date <= ? AND rejected = 0
        ORDER BY scan_date DESC, score DESC
    """, (cutoff, target_date)).fetchall()

    # Match scans against active laws via market_experience
    signals_out = []
    for scan in scans:
        symbol = scan['symbol']
        scan_date = scan['scan_date']

        # Find law activations for this symbol near the scan date
        law_hits = conn.execute("""
            SELECT DISTINCT law_id, law_name, direction
            FROM market_experience
            WHERE symbol = ?
              AND event_date >= ?
              AND event_date <= ?
            ORDER BY event_date DESC
        """, (symbol, cutoff, target_date)).fetchall()

        matched_laws = []
        for lh in law_hits:
            lid = lh['law_id']
            if lid in active_law_ids:
                cond = active_law_ids[lid]
                overall_prec = cond.get('overall_precision', 0.0) or 0.0
                cond_prec = cond['precision']
                lift_val = cond['lift']
                tier = conviction_tier(cond_prec, lift_val)

                matched_laws.append({
                    'law_id': lid,
                    'law_name': lh['law_name'],
                    'direction': lh['direction'],
                    'conditioned_precision': round(cond_prec, 4),
                    'unconditioned_precision': round(overall_prec, 4),
                    'precision_lift': lift_val,
                    'conviction_tier': tier,
                })

                # Upsert into conditioned_signals table
                conn.execute("""
                    INSERT INTO conditioned_signals
                        (signal_date, symbol, law_id, law_name, direction,
                         active_regime, conditioned_precision, unconditioned_precision,
                         precision_lift, conviction_tier, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(signal_date, symbol, law_id)
                    DO UPDATE SET
                        conditioned_precision=excluded.conditioned_precision,
                        unconditioned_precision=excluded.unconditioned_precision,
                        precision_lift=excluded.precision_lift,
                        conviction_tier=excluded.conviction_tier,
                        created_at=excluded.created_at
                """, (
                    scan_date, symbol, lid, lh['law_name'], lh['direction'],
                    current_regime, cond_prec, overall_prec, lift_val, tier,
                ))

        if matched_laws:
            best_law = max(matched_laws, key=lambda x: x['conditioned_precision'])
            signals_out.append({
                'symbol': symbol,
                'scan_date': scan_date,
                'setup_type': scan['setup_type'],
                'scan_score': scan['score'],
                'close_price': scan['close_price'],
                'entry_range': [scan['entry_low'], scan['entry_high']],
                'stop_loss': scan['stop_loss'],
                'targets': [scan['t1'], scan['t2']],
                'rr_ratios': [scan['rr1'], scan['rr2']],
                'matched_laws': matched_laws,
                'best_law': best_law,
                'n_confirming_laws': len(matched_laws),
            })

    conn.commit()
    conn.close()

    # Sort by best conditioned precision descending
    signals_out.sort(
        key=lambda x: x['best_law']['conditioned_precision'],
        reverse=True
    )

    return {
        'success': True,
        'target_date': target_date,
        'current_regime': current_regime,
        'current_breadth_condition': current_bc,
        'current_breadth_score': current_breadth_score,
        'active_conditioned_laws': len(active_law_ids),
        'total_scans_checked': len(scans),
        'signals_with_conditioned_laws': len(signals_out),
        'signals': signals_out,
    }


# ---------------------------------------------------------------------------
# Command: law_matrix
# ---------------------------------------------------------------------------

def law_matrix(params: dict) -> dict:
    """
    Return a full matrix: each law × each regime with precision per cell.
    Useful for a dashboard view of which laws are regime-sensitive.
    """
    conn = get_db()
    ensure_tables(conn)

    # Try reading from cached regime_law_conditions first
    rows = conn.execute("""
        SELECT law_id, law_name, direction, regime, breadth_condition,
               n_activations, n_hits, precision, lift, is_recommended
        FROM regime_law_conditions
        ORDER BY law_id, regime, breadth_condition
    """).fetchall()

    if not rows:
        conn.close()
        return {'success': False, 'message': 'No conditions computed yet. Run analyze_conditions first.'}

    # Build matrix structure
    laws_seen = {}
    for row in rows:
        lid = row['law_id']
        if lid not in laws_seen:
            laws_seen[lid] = {
                'law_id': lid,
                'law_name': row['law_name'],
                'direction': row['direction'],
                'regimes': {},
            }
        regime = row['regime']
        bc = row['breadth_condition']
        key = f"{regime}_{bc}"
        laws_seen[lid]['regimes'][key] = {
            'n': row['n_activations'],
            'precision': round(row['precision'], 4) if row['precision'] else 0.0,
            'lift': round(row['lift'], 2) if row['lift'] else 0.0,
            'recommended': bool(row['is_recommended']),
        }

    # Get overall law precisions
    laws_meta = {r['pattern_id']: r['precision'] for r in conn.execute(
        "SELECT pattern_id, precision FROM universal_laws_p16"
    ).fetchall()}

    matrix = []
    for lid, law_data in laws_seen.items():
        best_regime_cell = None
        best_prec = 0.0
        for key, cell in law_data['regimes'].items():
            if cell['recommended'] and cell['precision'] > best_prec:
                best_prec = cell['precision']
                best_regime_cell = key

        matrix.append({
            'law_id': lid,
            'law_name': law_data['law_name'],
            'direction': law_data['direction'],
            'overall_precision': laws_meta.get(lid, 0.0),
            'best_condition': best_regime_cell,
            'best_conditioned_precision': round(best_prec, 4),
            'regime_cells': law_data['regimes'],
        })

    matrix.sort(key=lambda x: x['best_conditioned_precision'], reverse=True)
    conn.close()

    return {
        'success': True,
        'total_laws': len(matrix),
        'columns': ['law_id', 'law_name', 'direction', 'overall_precision',
                    'best_condition', 'best_conditioned_precision', 'regime_cells'],
        'matrix': matrix,
    }


# ---------------------------------------------------------------------------
# Command: update_conditions
# ---------------------------------------------------------------------------

def update_conditions(params: dict) -> dict:
    """
    Write the best computed conditions back to universal_laws_p16,
    updating best_regime, best_regime_precision, is_regime_dependent.
    """
    conn = get_db()
    ensure_tables(conn)

    recommended = conn.execute("""
        SELECT law_id, regime, precision
        FROM regime_law_conditions
        WHERE is_recommended = 1
    """).fetchall()

    if not recommended:
        conn.close()
        return {'success': False, 'message': 'No recommended conditions found. Run analyze_conditions first.'}

    updated = 0
    skipped = 0
    updates = []

    for row in recommended:
        lid = row['law_id']
        new_regime = row['regime']
        new_prec = row['precision']

        current = conn.execute(
            "SELECT best_regime, best_regime_precision, precision FROM universal_laws_p16 WHERE pattern_id = ?",
            (lid,)
        ).fetchone()

        if not current:
            skipped += 1
            continue

        overall = current['precision'] or 0.0
        current_best = current['best_regime_precision'] or 0.0

        if new_prec > current_best:
            conn.execute("""
                UPDATE universal_laws_p16
                SET best_regime = ?,
                    best_regime_precision = ?,
                    is_regime_dependent = 1,
                    updated_at = datetime('now')
                WHERE pattern_id = ?
            """, (new_regime, new_prec, lid))
            updated += 1
            updates.append({
                'law_id': lid,
                'old_best_regime': current['best_regime'],
                'old_best_precision': round(current_best, 4),
                'new_best_regime': new_regime,
                'new_best_precision': round(new_prec, 4),
                'overall_precision': round(overall, 4),
                'improvement': round((new_prec - overall) / overall * 100, 1) if overall > 0 else 0.0,
            })
        else:
            skipped += 1

    conn.commit()
    conn.close()

    return {
        'success': True,
        'laws_updated': updated,
        'laws_skipped_no_improvement': skipped,
        'updates': updates,
    }


# ---------------------------------------------------------------------------
# Command: law_performance_history
# ---------------------------------------------------------------------------

def law_performance_history(params: dict) -> dict:
    """
    Show precision trajectory for a law (or all laws): early vs late precision
    derived from market_experience event dates. Splits events at midpoint.
    """
    conn = get_db()
    ensure_tables(conn)

    law_id_filter = params.get('law_id', None)
    top_n = params.get('top_n', 10)

    if law_id_filter:
        laws = conn.execute(
            "SELECT pattern_id, pattern_name, precision, early_precision, late_precision, oos_gap "
            "FROM universal_laws_p16 WHERE pattern_id = ?",
            (law_id_filter,)
        ).fetchall()
    else:
        laws = conn.execute(
            "SELECT pattern_id, pattern_name, precision, early_precision, late_precision, oos_gap "
            "FROM universal_laws_p16 ORDER BY precision DESC LIMIT ?",
            (top_n,)
        ).fetchall()

    if not laws:
        conn.close()
        return {'success': False, 'message': 'No laws found.'}

    results = []
    for law in laws:
        lid = law['pattern_id']

        # Load all experience events for this law, sorted by date
        events = conn.execute("""
            SELECT event_date, outcome, regime
            FROM market_experience
            WHERE law_id = ?
            ORDER BY event_date ASC
        """, (lid,)).fetchall()

        if not events:
            # Fall back to stored values
            results.append({
                'law_id': lid,
                'law_name': law['pattern_name'],
                'overall_precision': law['precision'],
                'early_precision': law['early_precision'],
                'late_precision': law['late_precision'],
                'oos_gap': law['oos_gap'],
                'trend': 'no_data',
                'n_events': 0,
                'date_range': None,
            })
            continue

        midpoint = len(events) // 2
        early_events = events[:midpoint]
        late_events = events[midpoint:]

        early_prec = precision_from_outcomes([e['outcome'] for e in early_events])
        late_prec = precision_from_outcomes([e['outcome'] for e in late_events])

        # Regime breakdown for context
        regime_counts = defaultdict(int)
        for e in events:
            regime_counts[e['regime'] or 'UNKNOWN'] += 1

        trend = 'improving' if late_prec > early_prec + 0.01 else \
                'declining' if early_prec > late_prec + 0.01 else 'stable'

        results.append({
            'law_id': lid,
            'law_name': law['pattern_name'],
            'overall_precision': round(law['precision'] or 0.0, 4),
            'early_precision_computed': round(early_prec, 4),
            'late_precision_computed': round(late_prec, 4),
            'early_precision_stored': law['early_precision'],
            'late_precision_stored': law['late_precision'],
            'oos_gap_stored': law['oos_gap'],
            'trend': trend,
            'n_events': len(events),
            'n_early': len(early_events),
            'n_late': len(late_events),
            'date_range': {
                'first': events[0]['event_date'],
                'last': events[-1]['event_date'],
            },
            'regime_distribution': dict(regime_counts),
        })

    conn.close()

    return {
        'success': True,
        'n_laws': len(results),
        'performance_history': results,
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params: dict) -> dict:
    """Run analyze_conditions → conditioned_signals → update_conditions in sequence."""
    today = datetime.date.today().isoformat()
    target_date = params.get('date', today)

    result_analyze = analyze_conditions({})
    if not result_analyze.get('success'):
        return {'success': False, 'stage': 'analyze_conditions', 'error': result_analyze}

    result_signals = conditioned_signals({'date': target_date})
    if not result_signals.get('success'):
        return {'success': False, 'stage': 'conditioned_signals', 'error': result_signals}

    result_update = update_conditions({})
    if not result_update.get('success'):
        return {'success': False, 'stage': 'update_conditions', 'error': result_update}

    # Also back-populate MUT_ law regime conditioning (uses ohlcv × explosive_moves × regime_history)
    result_mut = populate_mut_regime(params)

    return {
        'success': True,
        'build_date': target_date,
        'analyze_conditions': {
            'laws_analyzed': result_analyze['total_laws'],
            'laws_improved': result_analyze['laws_improved_by_conditioning'],
            'avg_lift_pct': result_analyze['average_precision_lift_pct'],
        },
        'conditioned_signals': {
            'active_conditioned_laws': result_signals['active_conditioned_laws'],
            'regime': result_signals['current_regime'],
            'breadth': result_signals['current_breadth_condition'],
            'signals_found': result_signals['signals_with_conditioned_laws'],
            'signals': result_signals['signals'],
        },
        'update_conditions': {
            'laws_updated': result_update['laws_updated'],
            'updates': result_update['updates'],
        },
        'populate_mut_regime': {
            'laws_found':  result_mut.get('laws_found', 0),
            'updated':     result_mut.get('updated', 0),
            'skipped':     result_mut.get('skipped', 0),
        },
    }


# ---------------------------------------------------------------------------
# Command: populate_mut_regime
# Back-populate best_regime for MUT_ laws using explosive_moves × regime_history
# ---------------------------------------------------------------------------

import re as _re
import math as _math
from statistics import median as _median

def _parse_mut_law(pattern_name: str):
    """
    Parse MUT_ law name to extract base feature column and threshold multiplier.
    Returns (feature_col, direction, multiplier) or (None, None, None).
    Examples:
      "Pre-Momentum (5d) (thresh×1.3)"  → ('pre5_momentum_5d', '>', 1.3)
      "BB Squeeze (3d pre) (thresh×0.9)" → ('pre3_bb_width', '<', 0.9)
      "RSI Momentum State (thresh×1.1)"  → ('pre1_rsi', '>', 1.1)
    """
    m = _re.search(r'thresh×([\d.]+)', pattern_name)
    mult = float(m.group(1)) if m else 1.0

    name_lower = pattern_name.lower()
    if 'pre-momentum' in name_lower or 'pre momentum' in name_lower:
        # momentum_5d — pre5_momentum_5d fires when momentum is HIGH
        lookback = '5'
        m2 = _re.search(r'\((\d+)d\)', pattern_name)
        if m2: lookback = m2.group(1)
        return (f'pre{lookback}_momentum_5d', '>', mult)
    elif 'bb squeeze' in name_lower:
        # bb_width — fires when width is LOW (squeeze)
        m3 = _re.search(r'\((\d+)d', pattern_name)
        lookback = m3.group(1) if m3 else '3'
        return (f'pre{lookback}_bb_width', '<', mult)
    elif 'rsi momentum' in name_lower or 'rsi' in name_lower:
        # RSI — fires when RSI is HIGH (momentum)
        return ('pre1_rsi', '>', mult)
    return (None, None, None)


def populate_mut_regime(params: dict) -> dict:
    """
    Back-populate best_regime / best_regime_precision for MUT_ laws that have
    NULL best_regime.  Uses explosive_moves (which has pre-computed pre-event
    features) × regime_history.  Algorithm:

      precision_in_regime = hit_rate_in_regime × overall_precision / regime_freq

    where hit_rate_in_regime = (explosions meeting condition in regime) /
                               (all explosions meeting condition)
    and   regime_freq        = proportion of days in that regime (from regime_history)
    """
    conn = get_db()
    ensure_tables(conn)
    min_lift = float(params.get('min_lift', 0.05))

    # ── 1. Regime frequency from regime_history ─────────────────────────────
    rh_rows = conn.execute("SELECT date, regime FROM regime_history").fetchall()
    regime_dates: dict = {r['date']: r['regime'] for r in rh_rows}
    regime_count: dict = {}
    for reg in regime_dates.values():
        regime_count[reg] = regime_count.get(reg, 0) + 1
    total_days = max(len(regime_dates), 1)

    # ── 2. Load MUT_ laws that still lack best_regime ───────────────────────
    mut_laws = conn.execute("""
        SELECT pattern_id, pattern_name, precision, n_activations
        FROM universal_laws_p16
        WHERE pattern_id LIKE 'MUT_%'
          AND best_regime IS NULL
        ORDER BY precision DESC
    """).fetchall()

    if not mut_laws:
        conn.close()
        return {'success': True, 'message': 'All MUT_ laws already have best_regime', 'updated': 0}

    # ── 3. Load all explosion rows with their feature values ────────────────
    exp_rows = conn.execute("""
        SELECT explosion_date,
               pre3_bb_width, pre5_bb_width,
               pre1_rsi, pre3_rsi, pre5_rsi,
               pre3_momentum_5d, pre5_momentum_5d
        FROM explosive_moves
        WHERE explosion_date IS NOT NULL
    """).fetchall()

    updated = 0
    skipped = 0
    results = []

    for law in mut_laws:
        pid   = law['pattern_id']
        pname = law['pattern_name']
        overall_prec = law['precision'] or 0.0

        feat_col, direction, mult = _parse_mut_law(pname)
        if feat_col is None:
            skipped += 1
            continue

        # ── 3a. Collect all feature values (non-NULL) to compute base threshold
        vals = [float(r[feat_col]) for r in exp_rows if r[feat_col] is not None]
        if len(vals) < 30:
            skipped += 1
            continue

        vals_sorted = sorted(vals)
        if direction == '<':
            # BB Squeeze: fires when width < base_pct35 × mult
            base_thresh = vals_sorted[int(len(vals_sorted) * 0.35)]
            threshold   = base_thresh * mult
        else:
            # Momentum / RSI: fires when value > base_pct65 × mult
            base_thresh = vals_sorted[int(len(vals_sorted) * 0.65)]
            threshold   = base_thresh * mult

        # ── 3b. Count hits (qualifying explosions) by regime ────────────────
        hits_by_regime: dict = {}
        total_hits = 0
        for r in exp_rows:
            fv = r[feat_col]
            if fv is None:
                continue
            fv = float(fv)
            meets = (fv < threshold) if direction == '<' else (fv > threshold)
            if not meets:
                continue
            regime = regime_dates.get(r['explosion_date'], 'UNKNOWN')
            hits_by_regime[regime] = hits_by_regime.get(regime, 0) + 1
            total_hits += 1

        if total_hits < 10:
            skipped += 1
            continue

        # ── 3c. Compute precision per regime ────────────────────────────────
        best_regime   = None
        best_prec_est = 0.0
        regime_scores = {}

        for regime, n_hits in hits_by_regime.items():
            hit_rate  = n_hits / total_hits
            reg_freq  = regime_count.get(regime, 1) / total_days
            # Bayesian lift estimate
            prec_est  = overall_prec * (hit_rate / reg_freq)
            prec_est  = min(prec_est, 0.99)
            lift      = prec_est - overall_prec
            regime_scores[regime] = {
                'n_hits':   n_hits,
                'hit_rate': round(hit_rate, 4),
                'prec_est': round(prec_est, 4),
                'lift':     round(lift, 4),
            }
            if prec_est > best_prec_est and lift >= min_lift:
                best_prec_est = prec_est
                best_regime   = regime

        if best_regime is None:
            skipped += 1
            results.append({'law_id': pid, 'status': 'no_lift', 'scores': regime_scores})
            continue

        # ── 3d. Write best_regime back to universal_laws_p16 ────────────────
        conn.execute("""
            UPDATE universal_laws_p16
            SET best_regime            = ?,
                best_regime_precision  = ?,
                is_regime_dependent    = 1,
                updated_at             = datetime('now')
            WHERE pattern_id = ?
        """, (best_regime, round(best_prec_est, 4), pid))
        updated += 1
        results.append({
            'law_id':     pid,
            'law_name':   pname,
            'status':     'updated',
            'best_regime': best_regime,
            'precision':   round(overall_prec, 4),
            'regime_precision': round(best_prec_est, 4),
            'lift_pp':    round((best_prec_est - overall_prec) * 100, 1),
            'scores':     regime_scores,
        })

    conn.commit()
    conn.close()

    return {
        'success':    True,
        'laws_found': len(mut_laws),
        'updated':    updated,
        'skipped':    skipped,
        'results':    [r for r in results if r.get('status') == 'updated'],
    }


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'analyze_conditions':    analyze_conditions,
    'conditioned_signals':   conditioned_signals,
    'law_matrix':            law_matrix,
    'update_conditions':     update_conditions,
    'law_performance_history': law_performance_history,
    'build_full':            build_full,
    'populate_mut_regime':   populate_mut_regime,
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params: dict = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown command: {cmd}. Available: {list(COMMANDS.keys())}'}))
        sys.exit(1)
    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        print(json.dumps({'error': str(e), 'command': cmd}))
        sys.exit(1)
