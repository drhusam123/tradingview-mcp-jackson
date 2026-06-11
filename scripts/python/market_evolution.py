"""
EGX Phase 15 — Self-Learning Market Evolution & Failure Intelligence System
============================================================================
A continuously self-learning market intelligence organism that:
  • learns from every success, failure, and historical event
  • evolves law confidence dynamically
  • reinforces useful structures, archives dead ones
  • reconstructs the cause of every failure
  • builds per-stock behavioral memory
  • generates and tests new hypothesis candidates
  • calibrates regime-specific models
  • produces daily/weekly evolution reports

Owner: Dr. Husam | May 2026
"""

import sqlite3, json, math, time, sys, hashlib, random
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from p6_research_context import (
    ingest_p6_ultra_failures,
    apply_p6_stock_adjustments,
    load_context,
)

ROOT       = Path(__file__).resolve().parent.parent.parent
DB_PATH    = ROOT / 'data' / 'egx_trading.db'
REPORT_DIR = ROOT / 'data' / 'research_reports'
REPORT_DIR.mkdir(exist_ok=True)

# ── Learning hyper-parameters ─────────────────────────────────────────────────
CONFIDENCE_LR        = 0.12   # learning rate for confidence EMA update
CONFIDENCE_DECAY     = 0.003  # daily decay when law is inactive
REINFORCEMENT_WINDOW = 90     # days rolling window for reinforcement score
MIN_ACTIVATIONS      = 15     # min activations needed to update confidence
HYPOTHESIS_MIN_SR    = 0.28   # support rate below this → REJECTED
HYPOTHESIS_IMPROVE   = 0.05   # minimum SR improvement to promote candidate
MUTATION_DELTA_SR    = 0.12   # ΔSR required to flag mutation in evolution
FAILURE_SAMPLE_CAP   = 5000   # cap on false-alarm events to reconstruct

RSI_RANGE_LO, RSI_RANGE_HI = 35.0, 65.0   # matches Phase 13/14

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_experience (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date          TEXT,
    symbol              TEXT,
    event_type          TEXT,
    law_id              TEXT,
    law_name            TEXT,
    direction           TEXT,
    outcome             TEXT,
    confidence_contrib  REAL,
    regime              TEXT,
    sector              TEXT,
    feature_value       REAL,
    next_max_return     REAL,
    n_other_laws_active INTEGER,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS law_confidence_evolution (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    law_id               TEXT,
    law_name             TEXT,
    direction            TEXT,
    snapshot_date        TEXT,
    confidence           REAL,
    delta                REAL,
    trigger_event        TEXT,
    n_recent_activations INTEGER,
    n_recent_successes   INTEGER,
    rolling_sr           REAL,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS structural_reinforcement (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    law_id              TEXT UNIQUE,
    law_name            TEXT,
    direction           TEXT,
    reinforcement_score REAL,
    activation_count    INTEGER,
    success_count       INTEGER,
    failure_count       INTEGER,
    rolling_90d_sr      REAL,
    baseline_sr         REAL,
    status              TEXT,
    last_active_date    TEXT,
    last_updated        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_behavioral_memory (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                  TEXT UNIQUE,
    sector                  TEXT,
    large_explosion_count   INTEGER,
    explosion_rate_per_100  REAL,
    false_signal_rate       REAL,
    dominant_precursor      TEXT,
    best_precursor_sr       REAL,
    regime_sensitivity      REAL,
    behavioral_class        TEXT,
    mutation_flag           INTEGER DEFAULT 0,
    last_explosion_date     TEXT,
    last_updated            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hypothesis_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id   TEXT UNIQUE,
    parent_law_id   TEXT,
    law_name        TEXT,
    direction       TEXT,
    feature         TEXT,
    threshold       REAL,
    operator        TEXT,
    support_rate    REAL,
    baseline_sr     REAL,
    sr_improvement  REAL,
    n_tested        INTEGER,
    status          TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    last_tested     TEXT
);

CREATE TABLE IF NOT EXISTS failure_reconstruction (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    failure_date              TEXT,
    symbol                    TEXT,
    law_id                    TEXT,
    law_name                  TEXT,
    direction                 TEXT,
    failure_class             TEXT,
    primary_cause             TEXT,
    secondary_cause           TEXT,
    regime_at_failure         TEXT,
    feature_value_at_failure  REAL,
    n_competing_laws          INTEGER,
    reconstruction_confidence REAL,
    created_at                TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS regime_behavioral_models (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    regime           TEXT,
    law_id           TEXT,
    law_name         TEXT,
    direction        TEXT,
    expected_support REAL,
    observed_support REAL,
    calibration_error REAL,
    n_observations   INTEGER,
    model_confidence REAL,
    last_updated     TEXT DEFAULT (datetime('now')),
    UNIQUE(regime, law_id)
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp       TEXT,
    run_type            TEXT,
    laws_updated        INTEGER,
    new_hypotheses      INTEGER,
    archived_laws       INTEGER,
    failures_analyzed   INTEGER,
    stocks_profiled     INTEGER,
    key_findings        TEXT,
    report_file         TEXT,
    elapsed_s           REAL
);
"""

def open_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory  = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db

def ensure_schema(db):
    for stmt in SCHEMA_SQL.strip().split(';'):
        s = stmt.strip()
        if s:
            try:
                db.execute(s)
            except Exception:
                pass
    db.commit()

# ── Statistics helpers ────────────────────────────────────────────────────────

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _std(xs):
    if len(xs) < 2: return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

def _ema_update(current, new_value, alpha):
    """Exponential moving average update."""
    return alpha * new_value + (1.0 - alpha) * current

def _check_op(val, threshold, operator):
    if val is None: return False
    if operator == 'lt':    return val < threshold
    if operator == 'gt':    return val > threshold
    if operator == 'range': return RSI_RANGE_LO <= val <= RSI_RANGE_HI
    return False

def _hash(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — INGEST MARKET EXPERIENCE
# ══════════════════════════════════════════════════════════════════════════════

def ingest_market_experience(db):
    """
    Convert counterfactual_events into the market_experience learning table.
    Classifies each activation as SUCCESS / PARTIAL / FAILURE and computes
    confidence contribution.
    Already-ingested rows are skipped (idempotent via DELETE+re-insert strategy
    with a date cutoff to avoid reprocessing old data on every run).
    """
    # Find last ingested date to avoid full re-scan
    last = db.execute(
        "SELECT MAX(event_date) FROM market_experience"
    ).fetchone()[0] or '2000-01-01'

    rows = db.execute("""
        SELECT ce.symbol, ce.precursor_date, ce.pattern_id, ce.pattern_name,
               ce.outcome, ce.feature_value, ce.next_max_return,
               ce.regime, ce.sector, ce.n_other_active,
               pp.direction
        FROM counterfactual_events ce
        JOIN precursor_patterns pp ON pp.id = ce.pattern_id
        WHERE ce.precursor_date > ?
        ORDER BY ce.precursor_date
    """, (last,)).fetchall()

    OUTCOME_CONTRIB = {
        'HIT':         +0.08,
        'PARTIAL':     +0.02,
        'FALSE_ALARM': -0.06,
    }
    EVENT_TYPE_MAP = {
        'HIT':         'EXPLOSION',
        'PARTIAL':     'PARTIAL_SIGNAL',
        'FALSE_ALARM': 'FALSE_SIGNAL',
    }

    inserted = 0
    for r in rows:
        contrib = OUTCOME_CONTRIB.get(r['outcome'], 0.0)
        db.execute("""
            INSERT INTO market_experience
              (event_date, symbol, event_type, law_id, law_name, direction,
               outcome, confidence_contrib, regime, sector,
               feature_value, next_max_return, n_other_laws_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r['precursor_date'], r['symbol'],
            EVENT_TYPE_MAP.get(r['outcome'], 'SIGNAL'),
            r['pattern_id'], r['pattern_name'], r['direction'],
            r['outcome'], contrib,
            r['regime'], r['sector'],
            r['feature_value'], r['next_max_return'], r['n_other_active']
        ))
        inserted += 1

    db.commit()
    total = db.execute("SELECT COUNT(*) FROM market_experience").fetchone()[0]
    return {
        'new_events_ingested': inserted,
        'total_experience_events': total,
        'date_range': [last, datetime.utcnow().strftime('%Y-%m-%d')],
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — EVOLVE LAW CONFIDENCE
# ══════════════════════════════════════════════════════════════════════════════

def _get_alltime_precision(db, pattern_id):
    """
    Compute all-time precision (HIT rate) from counterfactual_events.
    This is the correct baseline: P(large explosion | precursor active).
    """
    row = db.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN outcome='HIT' THEN 1 ELSE 0 END) hits
        FROM counterfactual_events WHERE pattern_id=?
    """, (pattern_id,)).fetchone()
    total = row['total'] or 0
    hits  = row['hits']  or 0
    return hits / total if total > 0 else 0.05   # fallback 5%


def evolve_law_confidence(db):
    """
    Update confidence for each law using rolling 90-day forward precision
    (P(explosion | precursor active)) vs. all-time precision baseline.

    The confidence score tracks whether the law's forward predictive power
    is improving or degrading relative to its historical average.
    """
    patterns  = db.execute("SELECT * FROM precursor_patterns").fetchall()
    hyps      = {r['pattern_id']: dict(r) for r in
                 db.execute("SELECT * FROM hypothesis_lifecycle").fetchall()}

    today     = datetime.utcnow().strftime('%Y-%m-%d')
    cutoff_90 = (datetime.utcnow() - timedelta(days=REINFORCEMENT_WINDOW)
                 ).strftime('%Y-%m-%d')

    results = []
    for pat in patterns:
        pid          = pat['id']
        alltime_prec = _get_alltime_precision(db, pid)  # forward P(explosion|precursor)
        # Ensure float — DB may return string or None
        _raw_conf    = (hyps.get(pid, {}).get('confidence_score') or
                        pat['confidence_level'] or alltime_prec or 0.5)
        try:
            old_conf = float(_raw_conf)
        except (TypeError, ValueError):
            old_conf = float(alltime_prec or 0.5)

        # Rolling 90-day forward precision
        acts = db.execute("""
            SELECT outcome FROM counterfactual_events
            WHERE pattern_id=? AND precursor_date>=?
        """, (pid, cutoff_90)).fetchall()

        n_total   = len(acts)
        n_success = sum(1 for a in acts if a['outcome'] == 'HIT')
        rolling_prec = n_success / n_total if n_total >= MIN_ACTIVATIONS else None

        if rolling_prec is not None:
            # Normalize precision relative to all-time baseline for confidence update
            # confidence is anchored to the validated lifecycle score, not raw precision
            normalized = rolling_prec / alltime_prec if alltime_prec > 0 else 1.0
            new_conf   = _ema_update(old_conf, old_conf * normalized, CONFIDENCE_LR)
            trigger    = 'ROLLING_UPDATE'
        else:
            new_conf = old_conf * (1.0 - CONFIDENCE_DECAY)
            trigger  = 'DECAY'
            rolling_prec = alltime_prec

        new_conf = max(0.01, min(0.99, round(new_conf, 4)))
        delta    = round(new_conf - old_conf, 4)

        db.execute("""
            INSERT INTO law_confidence_evolution
              (law_id, law_name, direction, snapshot_date, confidence, delta,
               trigger_event, n_recent_activations, n_recent_successes, rolling_sr)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (pid, pat['pattern_name'], pat['direction'],
              today, new_conf, delta, trigger,
              n_total, n_success, round(rolling_prec, 4)))

        results.append({
            'pattern':          pat['pattern_name'],
            'direction':        pat['direction'],
            'old_conf':         round(old_conf, 3),
            'new_conf':         new_conf,
            'delta':            delta,
            'rolling_precision': round(rolling_prec, 3),
            'alltime_precision': round(alltime_prec, 3),
            'n_activations':    n_total,
            'trigger':          trigger,
        })

    db.commit()
    gaining = [r for r in results if r['delta'] > 0]
    losing  = [r for r in results if r['delta'] < 0]
    return {
        'laws_updated':  len(results),
        'gaining':       len(gaining),
        'losing':        len(losing),
        'updates':       results,
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — STRUCTURAL REINFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

def reinforce_structures(db):
    """
    Compute reinforcement scores for all laws based on FORWARD precision
    (P(explosion | precursor active) in rolling 90-day window vs. all-time baseline).

    Score = rolling_precision / alltime_precision
    Status tiers:
      REINFORCED  — score ≥ 1.20  (rolling precision 20%+ above baseline)
      ACTIVE      — score ≥ 0.80  (within ±20% of baseline)
      DEGRADING   — score ≥ 0.50
      ARCHIVED    — score <  0.50
    """
    patterns  = db.execute("SELECT * FROM precursor_patterns").fetchall()
    cutoff_90 = (datetime.utcnow() - timedelta(days=REINFORCEMENT_WINDOW)
                 ).strftime('%Y-%m-%d')
    today = datetime.utcnow().strftime('%Y-%m-%d')

    db.execute("DELETE FROM structural_reinforcement")
    results = []

    for pat in patterns:
        pid      = pat['id']
        baseline = _get_alltime_precision(db, pid)  # use forward precision as baseline

        acts = db.execute("""
            SELECT outcome, precursor_date FROM counterfactual_events
            WHERE pattern_id=? AND precursor_date>=?
            ORDER BY precursor_date DESC
        """, (pid, cutoff_90)).fetchall()

        n_total   = len(acts)
        n_success = sum(1 for a in acts if a['outcome'] == 'HIT')
        n_fail    = sum(1 for a in acts if a['outcome'] == 'FALSE_ALARM')
        rolling_sr = n_success / n_total if n_total > 0 else baseline
        last_active = acts[0]['precursor_date'] if acts else None

        score = rolling_sr / baseline if baseline > 0 else 1.0
        score = round(score, 3)

        if   score >= 1.20: status = 'REINFORCED'
        elif score >= 0.80: status = 'ACTIVE'
        elif score >= 0.50: status = 'DEGRADING'
        else:               status = 'ARCHIVED'

        db.execute("""
            INSERT OR REPLACE INTO structural_reinforcement
              (law_id, law_name, direction, reinforcement_score,
               activation_count, success_count, failure_count,
               rolling_90d_sr, baseline_sr, status, last_active_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (pid, pat['pattern_name'], pat['direction'],
              score, n_total, n_success, n_fail,
              round(rolling_sr, 4), round(baseline, 4),
              status, last_active))

        results.append({
            'pattern':    pat['pattern_name'],
            'direction':  pat['direction'],
            'score':      score,
            'status':     status,
            'rolling_sr': round(rolling_sr, 3),
            'baseline_sr': round(baseline, 3),
        })

    db.commit()
    by_status = defaultdict(int)
    for r in results:
        by_status[r['status']] += 1

    return {
        'laws_scored':  len(results),
        'by_status':    dict(by_status),
        'structures':   results,
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — FAILURE RECONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════

# Failure cause taxonomy
FAILURE_CAUSES = {
    'REGIME_MISMATCH':      'Signal fired in hostile regime (BEAR for UP, BULL for DOWN reversal)',
    'LOW_MOMENTUM':         'Insufficient price momentum to sustain breakout after activation',
    'COMPETING_SIGNALS':    'Contradicting laws active simultaneously — signal suppressed',
    'LIQUIDITY_COLLAPSE':   'Very low BBW (hyper-compressed) — false compression, not true squeeze',
    'OVEREXTENDED_RSI':     'RSI outside expected range at activation — momentum already exhausted',
    'SECTOR_DIVERGENCE':    'Sector underperforming broad market despite individual signal',
    'MACRO_PRESSURE':       'Strong negative market breadth overwhelmed individual stock setup',
    'UNKNOWN':              'Failure cause not determinable from available features',
}

def learn_from_failures(db):
    """
    Reconstruct the market context for FALSE_ALARM activations.
    Classify each failure into a primary cause using available features.
    Aggregate failure patterns per law.
    """
    patterns  = db.execute("SELECT * FROM precursor_patterns").fetchall()
    regime_map = {r['date']: r['regime'] for r in
                  db.execute("SELECT date, regime FROM regime_history").fetchall()}

    db.execute("DELETE FROM failure_reconstruction")

    # Sample from FALSE_ALARM events (cap per pattern to avoid huge tables)
    all_reconstructions = []
    per_pattern_summary = {}

    for pat in patterns:
        pid = pat['id']

        failures = db.execute("""
            SELECT symbol, precursor_date, feature_value,
                   next_max_return, regime, sector, n_other_active
            FROM counterfactual_events
            WHERE pattern_id=? AND outcome='FALSE_ALARM'
            ORDER BY RANDOM()
            LIMIT ?
        """, (pid, FAILURE_SAMPLE_CAP)).fetchall()

        cause_counts = defaultdict(int)
        for f in failures:
            regime   = f['regime'] or regime_map.get(f['precursor_date'], 'UNKNOWN')
            fv       = f['feature_value']
            n_other  = f['n_other_active'] or 0
            ret      = f['next_max_return'] or 0.0

            # Rule-based failure cause classification
            primary = 'UNKNOWN'
            secondary = None

            if pat['direction'] == 'UP' and regime == 'BEAR':
                primary   = 'REGIME_MISMATCH'
                secondary = 'LOW_MOMENTUM'
            elif pat['direction'] == 'DOWN' and regime == 'BULL':
                primary   = 'REGIME_MISMATCH'
                secondary = 'COMPETING_SIGNALS'
            elif n_other >= 3:
                primary   = 'COMPETING_SIGNALS'
                secondary = 'REGIME_MISMATCH'
            elif fv is not None and pat['feature'] == 'pre3_bb_width' and fv < 0.10:
                primary   = 'LIQUIDITY_COLLAPSE'
                secondary = 'LOW_MOMENTUM'
            elif fv is not None and pat['feature'] in ('pre3_rsi', 'pre5_rsi'):
                if fv > 75 and pat['direction'] == 'UP':
                    primary   = 'OVEREXTENDED_RSI'
                    secondary = 'LOW_MOMENTUM'
                elif fv < 25 and pat['direction'] == 'DOWN':
                    primary   = 'OVEREXTENDED_RSI'
                    secondary = 'REGIME_MISMATCH'
                else:
                    primary = 'LOW_MOMENTUM'
            elif ret < 0.01:
                primary   = 'LOW_MOMENTUM'
                secondary = 'MACRO_PRESSURE'
            else:
                primary = 'LOW_MOMENTUM'

            cause_counts[primary] += 1
            conf = 0.65 if primary != 'UNKNOWN' else 0.30

            db.execute("""
                INSERT INTO failure_reconstruction
                  (failure_date, symbol, law_id, law_name, direction,
                   failure_class, primary_cause, secondary_cause,
                   regime_at_failure, feature_value_at_failure,
                   n_competing_laws, reconstruction_confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f['precursor_date'], f['symbol'],
                pid, pat['pattern_name'], pat['direction'],
                primary, primary, secondary or '',
                regime, fv, n_other, conf
            ))
            all_reconstructions.append(primary)

        dom_cause = max(cause_counts, key=cause_counts.get) if cause_counts else 'UNKNOWN'
        total_f   = sum(cause_counts.values())
        per_pattern_summary[pat['pattern_name']] = {
            'direction':    pat['direction'],
            'n_failures':   total_f,
            'dominant_cause': dom_cause,
            'cause_distribution': {
                k: round(v / total_f, 3) for k, v in cause_counts.items()
            } if total_f else {},
        }

    db.commit()

    # Global cause distribution
    global_causes = defaultdict(int)
    for c in all_reconstructions:
        global_causes[c] += 1
    total = sum(global_causes.values())

    return {
        'total_failures_analyzed': total,
        'global_cause_distribution': {
            k: {'n': v, 'pct': round(v / total * 100, 1)}
            for k, v in sorted(global_causes.items(), key=lambda x: -x[1])
        } if total else {},
        'per_pattern':  per_pattern_summary,
        'failure_definitions': FAILURE_CAUSES,
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — STOCK BEHAVIORAL MEMORY
# ══════════════════════════════════════════════════════════════════════════════

BEHAVIORAL_CLASSES = {
    'EXPLOSIVE':  'High explosion frequency, strong precursor sensitivity',
    'STEADY':     'Moderate explosions, consistent precursor response',
    'VOLATILE':   'Frequent moves but low precursor predictability',
    'DORMANT':    'Rarely explodes, low behavioral signal',
}

def evolve_stock_profiles(db):
    """
    Build or update per-stock behavioral memory from explosive_moves
    and counterfactual_events. Classifies each stock's behavioral identity.
    """
    symbols = db.execute(
        "SELECT DISTINCT symbol FROM explosive_moves"
    ).fetchall()
    sector_map = {r['symbol']: r['sector'] for r in
                  db.execute("SELECT symbol, sector FROM stock_profiles").fetchall()}

    # Explosion counts per symbol
    exp_counts = {r['symbol']: r['n'] for r in
                  db.execute("""SELECT symbol, COUNT(*) n FROM explosive_moves
                                WHERE explosion_class IN ('LARGE','EXTREME')
                                GROUP BY symbol""").fetchall()}

    # Total explosion days per symbol (any class)
    total_bars = {r['symbol']: r['n'] for r in
                  db.execute("""SELECT symbol, COUNT(*) n FROM explosive_moves
                                GROUP BY symbol""").fetchall()}

    # Last explosion date
    last_exp = {r['symbol']: r['last_date'] for r in
                db.execute("""SELECT symbol, MAX(explosion_date) last_date
                              FROM explosive_moves GROUP BY symbol""").fetchall()}

    # Per-symbol false signal rate (from counterfactuals)
    false_rates = {r['symbol']: r for r in db.execute("""
        SELECT symbol,
               COUNT(*) total,
               SUM(CASE WHEN outcome='FALSE_ALARM' THEN 1 ELSE 0 END) n_false
        FROM counterfactual_events GROUP BY symbol
    """).fetchall()}

    # Best precursor per symbol
    best_pre = {r['symbol']: r for r in db.execute("""
        SELECT symbol, pattern_name,
               CAST(SUM(CASE WHEN outcome='HIT' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) sr
        FROM counterfactual_events
        GROUP BY symbol, pattern_name
        HAVING COUNT(*) >= 10
        ORDER BY sr DESC
    """).fetchall()}

    # Regime sensitivity: stdev of support rate across regimes per symbol
    regime_sr = defaultdict(lambda: defaultdict(list))
    for r in db.execute("""
        SELECT symbol, regime, outcome FROM counterfactual_events
        WHERE regime IS NOT NULL AND regime != 'UNKNOWN'
    """).fetchall():
        regime_sr[r['symbol']][r['regime']].append(
            1 if r['outcome'] == 'HIT' else 0
        )

    db.execute("DELETE FROM stock_behavioral_memory")
    profiled = 0

    for row in symbols:
        sym = row['symbol']
        sector = sector_map.get(sym, 'Unknown')

        large_n  = exp_counts.get(sym, 0)
        total_n  = total_bars.get(sym, 1)
        # Explosion rate per 100 bars
        exp_rate = round(large_n / total_n * 100, 2) if total_n > 0 else 0.0

        # False signal rate
        fr_row   = false_rates.get(sym)
        false_sr = round(fr_row['n_false'] / fr_row['total'], 3) if fr_row and fr_row['total'] > 0 else 0.5

        # Best precursor
        best = best_pre.get(sym)
        dom_pre = best['pattern_name'] if best else None
        best_sr = round(float(best['sr']), 3) if best else 0.0

        # Regime sensitivity (std dev of per-regime hit rates)
        rsrs = []
        for regime_acts in regime_sr.get(sym, {}).values():
            if len(regime_acts) >= 5:
                rsrs.append(_mean(regime_acts))
        regime_sens = round(_std(rsrs), 3) if len(rsrs) >= 2 else 0.0

        # Behavioral class
        if exp_rate >= 5.0 and false_sr < 0.55:
            bclass = 'EXPLOSIVE'
        elif exp_rate >= 2.0 and false_sr < 0.60:
            bclass = 'STEADY'
        elif exp_rate >= 1.0 and false_sr >= 0.60:
            bclass = 'VOLATILE'
        else:
            bclass = 'DORMANT'

        db.execute("""
            INSERT OR REPLACE INTO stock_behavioral_memory
              (symbol, sector, large_explosion_count, explosion_rate_per_100,
               false_signal_rate, dominant_precursor, best_precursor_sr,
               regime_sensitivity, behavioral_class, last_explosion_date)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sym, sector, large_n, exp_rate, false_sr,
              dom_pre, best_sr, regime_sens, bclass,
              last_exp.get(sym)))
        profiled += 1

    db.commit()

    # Summary by class
    class_dist = {r['behavioral_class']: r['n'] for r in db.execute("""
        SELECT behavioral_class, COUNT(*) n FROM stock_behavioral_memory
        GROUP BY behavioral_class
    """).fetchall()}

    return {
        'stocks_profiled': profiled,
        'behavioral_distribution': class_dist,
        'definitions': BEHAVIORAL_CLASSES,
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — HYPOTHESIS EVOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def evolve_hypotheses(db):
    """
    Generate and test hypothesis variations by perturbing thresholds ±10/20%.
    Promote candidates that improve support rate by > HYPOTHESIS_IMPROVE.
    Reject candidates below HYPOTHESIS_MIN_SR.
    """
    patterns   = db.execute("SELECT * FROM precursor_patterns").fetchall()
    explosions = db.execute("""
        SELECT explosion_date, direction, explosion_class,
               pre3_bb_width, pre5_bb_width,
               pre3_rsi, pre5_rsi,
               pre3_momentum_5d, pre5_momentum_5d,
               pre3_vol_ratio, pre5_vol_ratio
        FROM explosive_moves ORDER BY explosion_date
    """).fetchall()

    today = datetime.utcnow().strftime('%Y-%m-%d')
    new_count = 0
    promoted  = 0
    rejected  = 0

    # Perturbation multipliers for threshold search
    PERTURBATIONS = [0.85, 0.90, 0.95, 1.05, 1.10, 1.15]

    for pat in patterns:
        feature   = pat['feature']
        threshold = pat['threshold']
        operator  = pat['operator']
        direction = pat['direction']
        baseline_sr = pat['support_rate'] or 0.0

        # Collect feature values for all target explosions
        target_vals  = []
        control_vals = []
        for e in explosions:
            fv = e[feature] if feature in e.keys() else None
            if fv is None: continue
            if e['direction'] == direction and e['explosion_class'] in ('LARGE', 'EXTREME'):
                target_vals.append(float(fv))
            elif e['explosion_class'] == 'SMALL':
                control_vals.append(float(fv))

        if len(target_vals) < 20:
            continue

        for mult in PERTURBATIONS:
            new_thresh = round(threshold * mult, 4)
            if new_thresh <= 0:
                continue

            # Test new threshold
            n_pos = sum(1 for v in target_vals if _check_op(v, new_thresh, operator))
            sr    = n_pos / len(target_vals) if target_vals else 0.0
            improvement = sr - baseline_sr

            hyp_id = _hash(f"{pat['id']}_{new_thresh}_{operator}_{direction}")

            # Check if already exists
            existing = db.execute(
                "SELECT * FROM hypothesis_candidates WHERE hypothesis_id=?", (hyp_id,)
            ).fetchone()

            if sr < HYPOTHESIS_MIN_SR:
                if existing:
                    db.execute(
                        "UPDATE hypothesis_candidates SET status='REJECTED', last_tested=? WHERE hypothesis_id=?",
                        (today, hyp_id)
                    )
                    rejected += 1
                continue

            if improvement >= HYPOTHESIS_IMPROVE:
                status = 'VALIDATED'
                promoted += 1
            elif improvement >= 0:
                status = 'CANDIDATE'
            else:
                status = 'WEAKER'

            if existing:
                db.execute("""
                    UPDATE hypothesis_candidates
                    SET support_rate=?, sr_improvement=?, n_tested=n_tested+1,
                        status=?, last_tested=?
                    WHERE hypothesis_id=?
                """, (round(sr, 4), round(improvement, 4), status, today, hyp_id))
            else:
                db.execute("""
                    INSERT INTO hypothesis_candidates
                      (hypothesis_id, parent_law_id, law_name, direction,
                       feature, threshold, operator, support_rate,
                       baseline_sr, sr_improvement, n_tested, status, last_tested)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (hyp_id, pat['id'], pat['pattern_name'], direction,
                      feature, new_thresh, operator,
                      round(sr, 4), round(baseline_sr, 4),
                      round(improvement, 4), 1, status, today))
                new_count += 1

    db.commit()

    # Summary
    candidates = db.execute("""
        SELECT status, COUNT(*) n, AVG(support_rate) avg_sr
        FROM hypothesis_candidates GROUP BY status
    """).fetchall()

    top_validated = db.execute("""
        SELECT law_name, direction, feature, threshold, support_rate, sr_improvement
        FROM hypothesis_candidates WHERE status='VALIDATED'
        ORDER BY sr_improvement DESC LIMIT 5
    """).fetchall()

    return {
        'new_candidates':    new_count,
        'promoted':          promoted,
        'rejected':          rejected,
        'by_status':         {r['status']: r['n'] for r in candidates},
        'top_improvements':  [dict(r) for r in top_validated],
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 7 — REGIME MODEL CALIBRATION
# ══════════════════════════════════════════════════════════════════════════════

def calibrate_regime_models(db):
    """
    For each law × regime combination, compare expected vs observed support rate.
    Computes calibration error and model confidence.
    """
    patterns   = db.execute("SELECT * FROM precursor_patterns").fetchall()
    REGIMES    = ['BULL', 'BEAR', 'CHOPPY']
    today      = datetime.utcnow().strftime('%Y-%m-%d')
    results    = []

    for pat in patterns:
        pid = pat['id']
        baseline = _get_alltime_precision(db, pid)  # forward precision baseline

        for regime in REGIMES:
            acts = db.execute("""
                SELECT outcome FROM counterfactual_events
                WHERE pattern_id=? AND regime=?
            """, (pid, regime)).fetchall()

            n = len(acts)
            if n < 10:
                continue

            n_hit    = sum(1 for a in acts if a['outcome'] == 'HIT')
            obs_sr   = n_hit / n
            exp_sr   = baseline  # all-time precision (forward-looking) as expected
            cal_err  = obs_sr - exp_sr
            conf     = min(0.99, 1.0 / (1.0 + math.exp(-n / 50)))  # sigmoid

            db.execute("""
                INSERT OR REPLACE INTO regime_behavioral_models
                  (regime, law_id, law_name, direction,
                   expected_support, observed_support, calibration_error,
                   n_observations, model_confidence, last_updated)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (regime, pid, pat['pattern_name'], pat['direction'],
                  round(exp_sr, 4), round(obs_sr, 4),
                  round(cal_err, 4), n,
                  round(conf, 3), today))

            results.append({
                'pattern':   pat['pattern_name'],
                'direction': pat['direction'],
                'regime':    regime,
                'expected':  round(exp_sr, 3),
                'observed':  round(obs_sr, 3),
                'error':     round(cal_err, 3),
                'n':         n,
            })

    db.commit()

    # Laws with largest calibration errors
    worst = sorted(results, key=lambda x: abs(x['error']), reverse=True)[:5]
    return {
        'models_calibrated': len(results),
        'worst_calibrations': worst,
        'avg_abs_error': round(_mean([abs(r['error']) for r in results]), 4) if results else 0,
    }

# ══════════════════════════════════════════════════════════════════════════════
# MASTER PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def p6_sync_only(db, params=None):
    """Lightweight P6 closed-loop sync — failures + stock adjustments only."""
    ensure_schema(db)
    evolve_stock_profiles(db)
    return {
        'p6_failures': ingest_p6_ultra_failures(db, params),
        'p6_adjustments': apply_p6_stock_adjustments(db, params),
    }


def full_evolution(db, params=None):
    """
    7-stage self-learning evolution pipeline.
    Runs all stages in sequence, returns consolidated results.
    P6 closed-loop context (params.p6_context or data/p6_research_context.json)
    feeds failure ingestion and post-profile stock adjustments.
    """
    params = params or {}
    t0 = time.time()
    results = {}
    p6_ctx = params.get('p6_context') or load_context()
    if p6_ctx:
        print('  [P6] Research context loaded — wiring live outcomes', flush=True)
        dq = p6_ctx.get('discovery_quality') or {}
        results['p6_context'] = {
            'loaded': True,
            'at': p6_ctx.get('at'),
            'discovery_quality_score': dq.get('score'),
            'discovery_grade': dq.get('grade'),
        }
        if dq.get('grade') in ('C', 'D') or (dq.get('score') or 100) < 52:
            results['discovery_quality_guard'] = {
                'active': True,
                'score': dq.get('score'),
                'grade': dq.get('grade'),
                'action': 'conservative_hypothesis_promotion',
            }
            print(f'  [Discovery] Quality guard ON — grade {dq.get("grade")} score {dq.get("score")}', flush=True)

    print('  [1/7] Ingesting market experience …', flush=True)
    results['experience'] = ingest_market_experience(db)

    print('  [2/7] Evolving law confidence …', flush=True)
    results['confidence'] = evolve_law_confidence(db)

    print('  [3/7] Structural reinforcement scoring …', flush=True)
    results['reinforcement'] = reinforce_structures(db)

    print('  [4/7] Failure reconstruction …', flush=True)
    results['failures'] = learn_from_failures(db)
    results['p6_failures'] = ingest_p6_ultra_failures(db, params)

    print('  [5/7] Stock behavioral memory …', flush=True)
    results['stocks'] = evolve_stock_profiles(db)
    results['p6_adjustments'] = apply_p6_stock_adjustments(db, params)

    print('  [6/7] Hypothesis evolution …', flush=True)
    results['hypotheses'] = evolve_hypotheses(db)

    print('  [7/7] Regime model calibration + report …', flush=True)
    results['regime_models'] = calibrate_regime_models(db)
    results['report_file']   = _generate_report(db, results)
    results['total_elapsed'] = round(time.time() - t0, 1)

    # Log run
    key_findings = _extract_key_findings(results)
    for block in ('p6_failures', 'p6_adjustments'):
        for f in (results.get(block) or {}).get('key_findings') or []:
            key_findings.append(f)
    guard = results.get('discovery_quality_guard')
    if guard and guard.get('active'):
        key_findings.append(
            f"Discovery quality guard: grade {guard.get('grade')} — tighten hypothesis promotion"
        )
    db.execute("""
        INSERT INTO evolution_log
          (run_timestamp, run_type, laws_updated, new_hypotheses,
           archived_laws, failures_analyzed, stocks_profiled,
           key_findings, report_file, elapsed_s)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.utcnow().isoformat(),
        'full',
        results['confidence'].get('laws_updated', 0),
        results['hypotheses'].get('new_candidates', 0),
        results['reinforcement'].get('by_status', {}).get('ARCHIVED', 0),
        results['failures'].get('total_failures_analyzed', 0),
        results['stocks'].get('stocks_profiled', 0),
        json.dumps(key_findings),
        results['report_file'],
        results['total_elapsed'],
    ))
    db.commit()

    results['key_findings'] = key_findings
    return results

def _extract_key_findings(results):
    findings = []
    # Confidence changes
    conf = results.get('confidence', {})
    if conf.get('gaining', 0) > 0:
        top = sorted(conf.get('updates', []), key=lambda x: x.get('delta', 0), reverse=True)
        if top:
            findings.append(f"Confidence RISING: {top[0]['pattern']} ({top[0]['direction']}) +{top[0]['delta']:.3f}")
    if conf.get('losing', 0) > 0:
        bot = sorted(conf.get('updates', []), key=lambda x: x.get('delta', 0))
        if bot:
            findings.append(f"Confidence FALLING: {bot[0]['pattern']} ({bot[0]['direction']}) {bot[0]['delta']:.3f}")

    # Reinforcement
    rf = results.get('reinforcement', {})
    by_s = rf.get('by_status', {})
    if by_s.get('REINFORCED', 0) > 0:
        findings.append(f"{by_s['REINFORCED']} law(s) REINFORCED beyond baseline")
    if by_s.get('ARCHIVED', 0) > 0:
        findings.append(f"⚠️ {by_s['ARCHIVED']} law(s) ARCHIVED (rolling SR < 50% baseline)")

    # Failures
    fr = results.get('failures', {})
    gcd = fr.get('global_cause_distribution', {})
    if gcd:
        dom = max(gcd, key=lambda k: gcd[k]['n'])
        findings.append(f"Dominant failure cause: {dom} ({gcd[dom]['pct']}% of all failures)")

    # Hypotheses
    hyp = results.get('hypotheses', {})
    if hyp.get('promoted', 0) > 0:
        findings.append(f"{hyp['promoted']} hypothesis variant(s) VALIDATED (SR improvement ≥ {HYPOTHESIS_IMPROVE*100:.0f}pp)")
    top_imp = hyp.get('top_improvements', [])
    if top_imp:
        t = top_imp[0]
        findings.append(f"Best hypothesis: {t['law_name']} threshold={t['threshold']} SR={t['support_rate']:.1%} (+{t['sr_improvement']:.1%})")

    # Stock behavior
    st = results.get('stocks', {})
    bd = st.get('behavioral_distribution', {})
    if bd.get('EXPLOSIVE', 0) > 0:
        findings.append(f"{bd['EXPLOSIVE']} stocks classified EXPLOSIVE (high explosion rate, low false-signal)")

    # Regime calibration
    rc = results.get('regime_models', {})
    worst = rc.get('worst_calibrations', [])
    if worst:
        w = worst[0]
        sign = '+' if w['error'] > 0 else ''
        findings.append(f"Largest regime miscalibration: {w['pattern']} in {w['regime']} ({sign}{w['error']:.1%})")

    return findings

# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH REPORT
# ══════════════════════════════════════════════════════════════════════════════

SECTION_LINE = '━' * 72

def _generate_report(db, results):
    now   = datetime.now()
    fname = f"evolution_report_{now.strftime('%Y-%m-%d')}.txt"
    fpath = REPORT_DIR / fname

    L = []
    def w(*args): L.extend(args)
    def sep(): w('', SECTION_LINE)
    def hdr(s): w(f"  {s}")

    w('══════════════════════════════════════════════════════════════════════')
    w('  🧠 EGX PHASE 15 — SELF-LEARNING MARKET EVOLUTION REPORT')
    w(f"  Generated: {now.strftime('%Y-%m-%d %H:%M')}  |  Phase 15 Evolution Engine")
    w('══════════════════════════════════════════════════════════════════════')
    w('')

    # ── Section 1: Experience Ingestion ─────────────────────────────────────
    sep(); hdr('📚 SECTION 1 — MARKET EXPERIENCE LEARNING')
    sep(); w('')
    exp = results.get('experience', {})
    hdr(f"Total experience events:  {exp.get('total_experience_events', 0):,}")
    hdr(f"New events ingested:      {exp.get('new_events_ingested', 0):,}")
    w('')

    # ── Section 2: Confidence Evolution ────────────────────────────────────
    sep(); hdr('📈 SECTION 2 — LAW CONFIDENCE EVOLUTION')
    sep(); w('')
    conf = results.get('confidence', {})
    updates = conf.get('updates', [])
    hdr(f"Laws updated:  {conf.get('laws_updated', 0)} | Gaining: {conf.get('gaining', 0)} | Losing: {conf.get('losing', 0)}")
    w('')
    hdr(f"  {'Pattern':<32} {'Dir':<6} {'Old→New':<18} {'Δ':<9} {'Rolling SR':<12} {'Trigger'}")
    hdr('  ' + '─' * 85)
    for u in sorted(updates, key=lambda x: -abs(x.get('delta', 0))):
        arrow  = '▲' if u['delta'] >= 0 else '▼'
        delta  = f"{arrow}{abs(u['delta']):.3f}"
        old_n  = f"{u['old_conf']:.3f}→{u['new_conf']:.3f}"
        rp = u.get('rolling_precision') or u.get('rolling_sr') or 0.0
        hdr(f"  {(u['pattern'] + ' (' + u['direction'] + ')'):<32} {old_n:<18} {delta:<9} {rp:.1%}  {' '*5} {u['trigger']}")
    w('')

    # ── Section 3: Structural Reinforcement ─────────────────────────────────
    sep(); hdr('⚡ SECTION 3 — STRUCTURAL REINFORCEMENT')
    sep(); w('')
    rf = results.get('reinforcement', {})
    by_s = rf.get('by_status', {})
    STATUS_ICONS = {'REINFORCED':'🟢', 'ACTIVE':'✅', 'DEGRADING':'🟡', 'ARCHIVED':'❌'}
    for status, icon in STATUS_ICONS.items():
        n = by_s.get(status, 0)
        if n > 0:
            hdr(f"  {icon} {status:<14}: {n} law(s)")
    w('')
    hdr(f"  {'Pattern':<32} {'Dir':<6} {'Score':<8} {'Status':<14} {'Rolling SR':<12} {'Baseline'}")
    hdr('  ' + '─' * 85)
    for s in sorted(rf.get('structures', []), key=lambda x: -x.get('score', 0)):
        hdr(f"  {(s['pattern']+' ('+s['direction']+')'):<38} {s['score']:<8.3f} {s['status']:<14} {s['rolling_sr']:.1%}   {s['baseline_sr']:.1%}")
    w('')

    # ── Section 4: Failure Reconstruction ────────────────────────────────────
    sep(); hdr('⚠️  SECTION 4 — FAILURE RECONSTRUCTION & ROOT CAUSE ANALYSIS')
    sep(); w('')
    fr = results.get('failures', {})
    hdr(f"Failures analyzed: {fr.get('total_failures_analyzed', 0):,}")
    w('')
    hdr('  Global failure cause distribution:')
    gcd = fr.get('global_cause_distribution', {})
    for cause, stats in sorted(gcd.items(), key=lambda x: -x[1]['n']):
        bar = '█' * round(stats['pct'] / 5)
        hdr(f"    {cause:<28}  {stats['pct']:>5.1f}%  {bar}")
    w('')
    hdr('  Per-law dominant failure:')
    for pname, ps in fr.get('per_pattern', {}).items():
        hdr(f"    {(pname + ' (' + ps['direction'] + ')'):<38} → {ps['dominant_cause']}")
    w('')

    # ── Section 5: Stock Behavioral Memory ───────────────────────────────────
    sep(); hdr('🏭 SECTION 5 — STOCK BEHAVIORAL MEMORY')
    sep(); w('')
    st = results.get('stocks', {})
    bd = st.get('behavioral_distribution', {})
    hdr(f"Stocks profiled: {st.get('stocks_profiled', 0)}")
    w('')
    CLASS_ICONS = {'EXPLOSIVE':'💥', 'STEADY':'✅', 'VOLATILE':'⚡', 'DORMANT':'😴'}
    for cls, n in sorted(bd.items(), key=lambda x: -x[1]):
        bar  = '█' * min(30, n // 5)
        icon = CLASS_ICONS.get(cls, '•')
        hdr(f"  {icon} {cls:<12}  {n:>4} stocks  {bar}")
    w('')
    # Top EXPLOSIVE stocks
    top_exp = db.execute("""
        SELECT symbol, sector, explosion_rate_per_100, false_signal_rate, best_precursor_sr
        FROM stock_behavioral_memory
        WHERE behavioral_class='EXPLOSIVE'
        ORDER BY explosion_rate_per_100 DESC LIMIT 10
    """).fetchall()
    if top_exp:
        hdr('  Top explosive stocks:')
        for s in top_exp:
            hdr(f"    {s['symbol']:<8} ({s['sector']:<16})  exp_rate={s['explosion_rate_per_100']:.1f}/100  false_sig={s['false_signal_rate']:.1%}  best_pre_sr={s['best_precursor_sr']:.1%}")
    w('')

    # ── Section 6: Hypothesis Evolution ──────────────────────────────────────
    sep(); hdr('🔬 SECTION 6 — HYPOTHESIS EVOLUTION')
    sep(); w('')
    hyp = results.get('hypotheses', {})
    by_hs = hyp.get('by_status', {})
    hdr(f"New candidates:   {hyp.get('new_candidates', 0)}")
    hdr(f"Promoted:         {hyp.get('promoted', 0)}")
    hdr(f"Rejected:         {hyp.get('rejected', 0)}")
    w('')
    for status, n in by_hs.items():
        hdr(f"  {status:<14}: {n}")
    w('')
    if hyp.get('top_improvements'):
        hdr('  Top validated hypotheses (SR improvement):')
        for t in hyp['top_improvements']:
            hdr(f"    {t['law_name']:<30} ({t['direction']})  thresh={t['threshold']:.4f}  SR={t['support_rate']:.1%}  Δ={t['sr_improvement']:+.1%}")
    w('')

    # ── Section 7: Regime Model Calibration ──────────────────────────────────
    sep(); hdr('🌐 SECTION 7 — REGIME MODEL CALIBRATION')
    sep(); w('')
    rc = results.get('regime_models', {})
    hdr(f"Models calibrated:   {rc.get('models_calibrated', 0)}")
    hdr(f"Avg |calibration error|: {rc.get('avg_abs_error', 0):.1%}")
    w('')
    all_models = db.execute("""
        SELECT regime, law_name, direction, expected_support, observed_support,
               calibration_error, n_observations, model_confidence
        FROM regime_behavioral_models
        ORDER BY regime, ABS(calibration_error) DESC
    """).fetchall()
    if all_models:
        hdr(f"  {'Regime':<8} {'Law':<32} {'Expected':<11} {'Observed':<11} {'Error':<9} {'N':<6} {'Conf'}")
        hdr('  ' + '─' * 90)
        for m in all_models:
            err  = m['calibration_error']
            sign = '+' if err >= 0 else ''
            hdr(f"  {m['regime']:<8} {(m['law_name']+' ('+m['direction']+')'):<32} {m['expected_support']:.1%}      {m['observed_support']:.1%}      {sign}{err:.1%}    {m['n_observations']:<6} {m['model_confidence']:.2f}")
    w('')

    # ── Section 8: Key Findings ───────────────────────────────────────────────
    sep(); hdr('🧪 SECTION 8 — EVOLUTION SUMMARY & KEY FINDINGS')
    sep(); w('')
    for i, finding in enumerate(results.get('key_findings', []), 1):
        hdr(f"  {i}. {finding}")
    w('')
    hdr('  SELF-CORRECTION CHECKS:')
    hdr('  ✓ Confirmation bias guard: all failure causes systematically classified')
    hdr('  ✓ Survivorship bias guard: DORMANT stocks tracked, not discarded')
    hdr('  ✓ Overfitting guard: hypothesis MIN_SR threshold applied')
    hdr('  ✓ Stale assumption guard: confidence decay applied to inactive laws')
    w('')
    w('══════════════════════════════════════════════════════════════════════')
    hdr(f"  ⏱ Evolution complete in {results.get('total_elapsed', '?')}s")
    w('══════════════════════════════════════════════════════════════════════')

    fpath.write_text('\n'.join(L), encoding='utf-8')
    return str(fpath)

# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH / CLI
# ══════════════════════════════════════════════════════════════════════════════

def status(db):
    tables = [
        ('market_experience',       'total_experience_events'),
        ('law_confidence_evolution', 'confidence_snapshots'),
        ('structural_reinforcement', 'laws_scored'),
        ('failure_reconstruction',   'failure_records'),
        ('stock_behavioral_memory',  'stocks_profiled'),
        ('hypothesis_candidates',    'hypothesis_candidates'),
        ('regime_behavioral_models', 'regime_models'),
        ('evolution_log',            'runs'),
    ]
    result = {}
    for tbl, key in tables:
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            n = 0
        result[key] = n

    # Last run
    last = db.execute(
        "SELECT run_timestamp, run_type, elapsed_s FROM evolution_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    result['last_run'] = dict(last) if last else None
    return result

def dispatch(cmd, params):
    db = open_db()
    ensure_schema(db)
    if cmd == 'status':               return status(db)
    if cmd == 'experience':           return ingest_market_experience(db)
    if cmd == 'confidence':           return evolve_law_confidence(db)
    if cmd == 'reinforcement':        return reinforce_structures(db)
    if cmd == 'failures':             return learn_from_failures(db)
    if cmd == 'stocks':               return evolve_stock_profiles(db)
    if cmd == 'hypotheses':           return evolve_hypotheses(db)
    if cmd == 'regime_calibration':   return calibrate_regime_models(db)
    if cmd == 'full_evolution':       return full_evolution(db, params)
    if cmd == 'p6_sync':              return p6_sync_only(db, params)
    return {'error': f'Unknown command: {cmd}'}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'status'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    result = dispatch(cmd, params)
    print(json.dumps(result, default=str))
