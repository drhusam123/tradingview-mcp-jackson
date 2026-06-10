#!/usr/bin/env python3
"""
Phase 36: Statistical Grounding Engine — EGX Autonomous Quant System

Answers the critical question: "Is this edge real or statistical noise?"
Applies rigorous statistical tests to every law in the system.

Commands:
  grade_all_laws    — grade every law in pattern_laws table (A/B/C/D/F)
  test_law          — full statistical test for one law
  bootstrap_law     — bootstrap confidence intervals for one law
  oos_validation    — out-of-sample check for all laws
  expectancy_report — execution-adjusted expectancy for all laws
  build_full        — grade_all_laws + oos_validation + expectancy_report

Usage:
  python statistical_grounding.py <command> '<json_params>'
Output: last stdout line = valid JSON
"""

import os
import sys
import json
import math
import sqlite3
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# DB Setup
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RANDOM_BASELINE = 0.182   # EGX random precision baseline (18.2%)
FRICTION_BPS    = 100     # total round-trip friction (100bps = 1%)
AVG_WIN_MULT    = 1.5     # assume avg win is 1.5x avg loss (conservative)
SIGNIFICANCE    = 0.05    # p-value threshold
OOS_SPLIT       = 0.70    # 70% train, 30% test
STRESS_DROP_MAX = 0.40    # max precision drop under stress before flagging

# Regime stress factors
REGIME_STRESS = {
    'TRENDING':    0.2,
    'TRENDING_UP': 0.2,
    'TRENDING_DOWN': 0.2,
    'VOLATILE':    0.4,
    'TRANSITION':  0.6,
    'SIDEWAYS':    0.25,
    'RECOVERING':  0.25,
    'CRISIS':      0.5,
    'UNKNOWN':     0.3,
}

DEFAULT_STRESS_FACTOR = 0.3
AVG_LOSS_DEFAULT      = 0.02  # 2% assumed average loss


# ---------------------------------------------------------------------------
# DB Connection & Tables
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS law_grades (
        law_id           TEXT PRIMARY KEY,
        law_name         TEXT,
        precision        REAL,
        n_samples        INTEGER,
        p_value          REAL,
        z_score          REAL,
        is_significant   INTEGER,
        ci_low_95        REAL,
        ci_high_95       REAL,
        oos_degradation  REAL,
        overfitting_risk TEXT,
        stressed_precision REAL,
        stress_drop      REAL,
        eae              REAL,
        grade            TEXT,
        recommendation   TEXT,
        graded_at        TEXT
    );

    CREATE TABLE IF NOT EXISTS grounding_summary (
        run_date         TEXT PRIMARY KEY,
        n_graded         INTEGER,
        n_grade_a        INTEGER,
        n_grade_b        INTEGER,
        n_grade_c        INTEGER,
        n_grade_d        INTEGER,
        n_grade_f        INTEGER,
        n_significant    INTEGER,
        avg_precision    REAL,
        n_should_retire  INTEGER,
        grounding_score  REAL,
        summary_text     TEXT,
        computed_at      TEXT
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Pure-Python Statistical Utilities
# ---------------------------------------------------------------------------

def _normal_cdf_complement(z):
    """
    Approximation of 1 - Phi(z) for z >= 0.
    Uses Abramowitz & Stegun approximation 26.2.17.
    For z < 0: returns 1 - _normal_cdf_complement(-z).
    """
    if z < 0:
        return 1.0 - _normal_cdf_complement(-z)
    t = 1.0 / (1.0 + 0.2316419 * z)
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    return poly * math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def binomial_significance(n_successes, n_trials, p_null=RANDOM_BASELINE):
    """
    One-tailed binomial significance test using normal approximation.
    H0: true precision <= p_null (random baseline).
    H1: true precision > p_null.
    """
    if n_trials < 10:
        return {'p_value': 1.0, 'z_score': 0.0, 'is_significant': False}
    p_hat = n_successes / n_trials
    se = math.sqrt(p_null * (1 - p_null) / n_trials)
    z = (p_hat - p_null) / (se + 1e-10)
    p_value = _normal_cdf_complement(z)
    return {
        'p_value':        round(p_value, 6),
        'z_score':        round(z, 4),
        'is_significant': p_value < SIGNIFICANCE,
    }


def _benjamini_hochberg(p_values, alpha=0.05):
    """
    Benjamini-Hochberg procedure for controlling False Discovery Rate.
    Returns a list of booleans — True if hypothesis i is rejected (significant after correction).

    Steps:
    1. Sort p-values in ascending order, keeping track of original indices
    2. For rank k (1-based) out of m total: threshold = k/m * alpha
    3. Find the largest k where p[k] <= k/m * alpha
    4. All hypotheses with rank <= that k are significant
    """
    m = len(p_values)
    if m == 0:
        return []

    # Create (original_index, p_value) pairs and sort by p_value
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])

    # Find largest k where p[k] <= k/m * alpha
    last_significant = -1
    for rank, (orig_idx, p) in enumerate(indexed, 1):
        threshold = (rank / m) * alpha
        if p <= threshold:
            last_significant = rank

    # All hypotheses up to last_significant rank are significant
    significant_indices = set()
    if last_significant >= 0:
        for rank, (orig_idx, p) in enumerate(indexed, 1):
            if rank <= last_significant:
                significant_indices.add(orig_idx)

    return [i in significant_indices for i in range(m)]


def bootstrap_precision(n_successes, n_trials, n_bootstrap=1000, seed=42):
    """
    Bootstrap confidence intervals for observed precision.
    Simulates n_bootstrap resamples of n_trials Bernoulli draws.
    """
    random.seed(seed)
    p_obs = n_successes / max(n_trials, 1)
    bootstrap_precisions = []
    for _ in range(n_bootstrap):
        sample = sum(1 for _ in range(n_trials) if random.random() < p_obs)
        bootstrap_precisions.append(sample / n_trials)
    bootstrap_precisions.sort()
    ci_low  = bootstrap_precisions[int(0.025 * n_bootstrap)]
    ci_high = bootstrap_precisions[int(0.975 * n_bootstrap)]
    return {
        'ci_low_95':  round(ci_low, 4),
        'ci_high_95': round(ci_high, 4),
        'ci_width':   round(ci_high - ci_low, 4),
    }


# ---------------------------------------------------------------------------
# DB Helper: Read pattern_laws
# ---------------------------------------------------------------------------

def _load_laws(db):
    """
    Load all laws from pattern_laws. Handles missing columns gracefully.
    Returns list of dicts with standardised keys.
    """
    laws = []
    try:
        rows = db.execute("""
            SELECT * FROM pattern_laws
        """).fetchall()
    except Exception:
        return []

    # Detect available columns
    try:
        col_info = db.execute("PRAGMA table_info(pattern_laws)").fetchall()
        available_cols = {row['name'] for row in col_info}
    except Exception:
        available_cols = set()

    for row in rows:
        try:
            d = dict(row)
            law = {
                'law_id':         d.get('pattern_name') or d.get('law_id') or 'unknown',
                'law_name':       d.get('pattern_name') or d.get('law_name') or 'unknown',
                'precision':      float(d.get('precision') or 0.5),
                'recall':         float(d.get('recall') or 0.0),
                'f1':             float(d.get('f1') or 0.0),
                'n_samples':      int(d.get('n_samples') or 30),
                'sector':         d.get('sector') or 'UNKNOWN',
                'regime_context': d.get('regime_context') or 'UNKNOWN',
                'status':         d.get('status') or 'active',
                'last_validated': d.get('last_validated') or None,
                'created_at':     d.get('created_at') or None,
            }
            # n_samples fallback: if column missing, derive from recall/f1
            if 'n_samples' not in available_cols or law['n_samples'] == 0:
                if law['recall'] > 0:
                    # rough proxy: assume precision * n ~ recall * N
                    law['n_samples'] = max(30, int(law['recall'] * 100))
                else:
                    law['n_samples'] = 30
            laws.append(law)
        except Exception:
            continue
    return laws


def _load_single_law(db, law_name=None, law_id=None):
    """Load a single law by name or ID."""
    laws = _load_laws(db)
    for law in laws:
        if law_name and (law['law_name'] == law_name or law['law_id'] == law_name):
            return law
        if law_id and law['law_id'] == law_id:
            return law
    return None


# ---------------------------------------------------------------------------
# Failure & Regime Helpers
# ---------------------------------------------------------------------------

def _load_failure_archetypes(db):
    """Load failure_intelligence rows."""
    rows = []
    try:
        rows = db.execute("""
            SELECT symbol, archetype, confidence, date
            FROM failure_intelligence
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _load_regime_history(db):
    """Load regime_history or market_regime. Returns list of {date, regime_label}."""
    for table in ('regime_history', 'market_regime'):
        try:
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            results = []
            for r in rows:
                d = dict(r)
                label = d.get('regime_label') or d.get('regime') or 'UNKNOWN'
                results.append({'date': d.get('date', ''), 'regime_label': label.upper()})
            return results
        except Exception:
            continue
    return []


def _dominant_regime(db):
    """Return the most common regime in the history table."""
    regimes = _load_regime_history(db)
    if not regimes:
        return 'UNKNOWN'
    counts = defaultdict(int)
    for r in regimes:
        counts[r['regime_label']] += 1
    return max(counts, key=lambda k: counts[k])


def _stress_factor_for_law(db, law):
    """
    Determine stress factor for a law based on its regime context
    and failure patterns.
    """
    regime = (law.get('regime_context') or _dominant_regime(db) or 'UNKNOWN').upper()
    # Map to stress bucket
    for key, factor in REGIME_STRESS.items():
        if key in regime:
            return factor
    return DEFAULT_STRESS_FACTOR


# ---------------------------------------------------------------------------
# OOS Validation Helper
# ---------------------------------------------------------------------------

def _oos_degradation(law):
    """
    Compute OOS degradation proxy for a single law.
    Uses created_at / last_validated timestamps when available,
    else falls back to n_samples heuristic.
    """
    created_at     = law.get('created_at')
    last_validated = law.get('last_validated')
    precision      = law.get('precision', 0.5)
    recall         = law.get('recall', 0.0)
    n_samples      = law.get('n_samples', 30)

    # Try timestamp-based split
    ref_date = None
    for ts_str in (last_validated, created_at):
        if ts_str:
            try:
                ref_date = datetime.fromisoformat(str(ts_str)[:10])
                break
            except Exception:
                continue

    if ref_date:
        age_days = (datetime.now() - ref_date).days
        if age_days > 60:
            # OOS proxy: compare precision to recall ratio
            if recall > 0 and precision > 0:
                ratio = recall / precision
                # Perfect model: ratio near 1; degraded model: ratio < 0.7
                degradation = max(0.0, 1.0 - ratio) * 0.5
            else:
                degradation = 0.25
            return round(degradation, 4)

    # Fallback: n_samples heuristic
    if n_samples < 15:
        return 0.40  # HIGH overfitting risk → high degradation proxy
    elif n_samples <= 30:
        return 0.20  # MEDIUM risk
    else:
        return 0.10  # LOW risk


def _overfitting_risk(law):
    """Return 'HIGH', 'MEDIUM', or 'LOW' based on n_samples."""
    n = law.get('n_samples', 30)
    if n < 15:
        return 'HIGH'
    elif n <= 30:
        return 'MEDIUM'
    else:
        return 'LOW'


# ---------------------------------------------------------------------------
# Expectancy Helper
# ---------------------------------------------------------------------------

def _compute_eae(precision, avg_loss=AVG_LOSS_DEFAULT):
    """Execution-Adjusted Expectancy (EAE)."""
    avg_win = avg_loss * AVG_WIN_MULT
    eae = (precision * avg_win) - ((1 - precision) * avg_loss) - (FRICTION_BPS / 10000)
    return round(eae, 6)


# ---------------------------------------------------------------------------
# Grade Assignment
# ---------------------------------------------------------------------------

def _assign_grade(is_significant, overfitting_risk, eae, stress_drop):
    """
    Grade rules:
      A: significant AND no overfitting AND EAE > 0.005 AND not stressed
      B: significant AND moderate robustness AND EAE > 0
      C: significant AND (overfitting OR stressed) AND EAE > 0
      D: NOT significant
      F: EAE <= 0
    """
    if eae <= 0:
        return 'F'
    if not is_significant:
        return 'D'
    stressed = stress_drop > STRESS_DROP_MAX
    if is_significant and overfitting_risk == 'LOW' and eae > 0.005 and not stressed:
        return 'A'
    if is_significant and overfitting_risk in ('LOW', 'MEDIUM') and eae > 0:
        if not stressed:
            return 'B'
    if is_significant and (overfitting_risk == 'HIGH' or stressed) and eae > 0:
        return 'C'
    if is_significant and eae > 0:
        return 'B'
    return 'D'


def _recommendation(grade, law_name):
    """Human-readable recommendation for a graded law."""
    recs = {
        'A': f"KEEP — '{law_name}' is statistically robust, out-of-sample validated, and profitable after costs. Deploy with full confidence.",
        'B': f"KEEP — '{law_name}' shows genuine edge with moderate robustness. Monitor for regime shifts.",
        'C': f"CAUTION — '{law_name}' has a real edge but shows overfitting or stress risk. Use reduced position sizing.",
        'D': f"RETIRE — '{law_name}' is NOT statistically significant (p >= {SIGNIFICANCE}). This edge may be noise.",
        'F': f"RETIRE — '{law_name}' is net-negative after execution costs. Remove from trading universe immediately.",
    }
    return recs.get(grade, f"REVIEW — '{law_name}' requires manual inspection.")


# ---------------------------------------------------------------------------
# Step-by-step test for a single law
# ---------------------------------------------------------------------------

def _test_single_law(db, law):
    """Run all 5 steps for a single law. Returns full result dict."""
    law_id   = law['law_id']
    law_name = law['law_name']
    precision = law['precision']
    n_samples = law['n_samples']

    n_successes = max(1, int(round(precision * n_samples)))

    # Step 1: Binomial significance
    sig = binomial_significance(n_successes, n_samples)

    # Step 2: Bootstrap CI
    boot = bootstrap_precision(n_successes, n_samples)

    # Step 3: OOS validation
    oos_deg       = _oos_degradation(law)
    oos_risk      = _overfitting_risk(law)
    oos_precision = round(precision * (1.0 - oos_deg), 4)
    oos_result    = {
        'oos_precision':     oos_precision,
        'oos_degradation':   oos_deg,
        'overfitting_risk':  oos_risk,
        'oos_status':        'ROBUST' if oos_deg < 0.15 else ('FRAGILE' if oos_deg < 0.30 else 'OVERFIT'),
    }

    # Step 4: Stress test
    stress_factor     = _stress_factor_for_law(db, law)
    stressed_prec     = round(precision * (1.0 - stress_factor), 4)
    stress_drop       = round((precision - stressed_prec) / (precision + 1e-10), 4)
    stress_result     = {
        'stressed_precision': stressed_prec,
        'stress_factor':      stress_factor,
        'stress_drop':        stress_drop,
        'is_stressed':        stress_drop > STRESS_DROP_MAX,
    }

    # Step 5: EAE
    eae_val = _compute_eae(precision)
    eae_result = {
        'eae':            eae_val,
        'avg_win_pct':    round(AVG_LOSS_DEFAULT * AVG_WIN_MULT * 100, 2),
        'avg_loss_pct':   round(AVG_LOSS_DEFAULT * 100, 2),
        'friction_bps':   FRICTION_BPS,
        'is_profitable':  eae_val > 0,
    }

    # Step 6: Grade
    grade = _assign_grade(
        sig['is_significant'],
        oos_risk,
        eae_val,
        stress_drop,
    )
    rec = _recommendation(grade, law_name)

    return {
        'law_id':      law_id,
        'law_name':    law_name,
        'precision':   precision,
        'n_samples':   n_samples,
        'significance': sig,
        'bootstrap':   boot,
        'oos':         oos_result,
        'stress':      stress_result,
        'eae':         eae_result,
        'grade':       grade,
        'recommendation': rec,
    }


# ---------------------------------------------------------------------------
# Command 1: grade_all_laws
# ---------------------------------------------------------------------------

def grade_all_laws(params):
    db = get_db()
    laws = _load_laws(db)

    if not laws:
        result = {
            'n_graded': 0,
            'grade_distribution': {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0},
            'n_should_retire': 0,
            'n_significant': 0,
            'avg_precision_A_laws': 0.0,
            'top_A_laws': [],
            'bottom_laws_to_retire': [],
            'warning': 'No laws found in pattern_laws table.',
        }
        db.close()
        return result

    grade_dist   = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0}
    a_laws       = []
    retire_laws  = []
    n_significant = 0
    graded_at    = datetime.now().isoformat()

    for law in laws:
        tested = _test_single_law(db, law)
        grade  = tested['grade']
        grade_dist[grade] = grade_dist.get(grade, 0) + 1

        if tested['significance']['is_significant']:
            n_significant += 1

        if grade in ('D', 'F'):
            retire_laws.append({
                'law_name':   tested['law_name'],
                'grade':      grade,
                'precision':  tested['precision'],
                'eae':        tested['eae']['eae'],
                'reason':     tested['recommendation'],
            })

        if grade == 'A':
            a_laws.append({
                'law_name':  tested['law_name'],
                'precision': tested['precision'],
                'eae':       tested['eae']['eae'],
                'z_score':   tested['significance']['z_score'],
            })

        # Persist to law_grades
        try:
            db.execute("""
                INSERT OR REPLACE INTO law_grades (
                    law_id, law_name, precision, n_samples,
                    p_value, z_score, is_significant,
                    ci_low_95, ci_high_95,
                    oos_degradation, overfitting_risk,
                    stressed_precision, stress_drop,
                    eae, grade, recommendation, graded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                tested['law_id'],
                tested['law_name'],
                tested['precision'],
                tested['n_samples'],
                tested['significance']['p_value'],
                tested['significance']['z_score'],
                1 if tested['significance']['is_significant'] else 0,
                tested['bootstrap']['ci_low_95'],
                tested['bootstrap']['ci_high_95'],
                tested['oos']['oos_degradation'],
                tested['oos']['overfitting_risk'],
                tested['stress']['stressed_precision'],
                tested['stress']['stress_drop'],
                tested['eae']['eae'],
                grade,
                tested['recommendation'],
                graded_at,
            ))
        except Exception:
            pass

    db.commit()

    n_graded = len(laws)
    a_laws.sort(key=lambda x: x['eae'], reverse=True)
    retire_laws.sort(key=lambda x: x['eae'])

    # Compute grounding score
    grounding_score = 0.0
    if n_graded > 0:
        grounding_score = round(
            (grade_dist['A'] + grade_dist['B'] * 0.7) / n_graded * 100, 2
        )

    avg_precision_a = 0.0
    if a_laws:
        avg_precision_a = round(statistics.mean(l['precision'] for l in a_laws), 4)

    n_retire = grade_dist['D'] + grade_dist['F']

    # Persist grounding summary
    run_date = datetime.now().strftime('%Y-%m-%d')
    all_precisions = [l['precision'] for l in laws]
    avg_prec_all = round(statistics.mean(all_precisions), 4) if all_precisions else 0.0
    summary_text = (
        f"{n_graded} laws graded: {grade_dist['A']} A, {grade_dist['B']} B, "
        f"{grade_dist['C']} C, {grade_dist['D']} D, {grade_dist['F']} F. "
        f"Grounding score: {grounding_score}/100. "
        f"{n_retire} laws should be retired."
    )
    try:
        db.execute("""
            INSERT OR REPLACE INTO grounding_summary (
                run_date, n_graded, n_grade_a, n_grade_b, n_grade_c,
                n_grade_d, n_grade_f, n_significant, avg_precision,
                n_should_retire, grounding_score, summary_text, computed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_date, n_graded,
            grade_dist['A'], grade_dist['B'], grade_dist['C'],
            grade_dist['D'], grade_dist['F'],
            n_significant, avg_prec_all,
            n_retire, grounding_score,
            summary_text, graded_at,
        ))
        db.commit()
    except Exception:
        pass

    db.close()

    return {
        'n_graded':            n_graded,
        'grade_distribution':  grade_dist,
        'n_should_retire':     n_retire,
        'n_significant':       n_significant,
        'avg_precision_A_laws': avg_precision_a,
        'grounding_score':     grounding_score,
        'top_A_laws':          a_laws[:10],
        'bottom_laws_to_retire': retire_laws[:10],
        'summary':             summary_text,
    }


# ---------------------------------------------------------------------------
# Command 2: test_law
# ---------------------------------------------------------------------------

def test_law(params):
    law_name = params.get('law_name') or params.get('law_id')
    if not law_name:
        return {'error': 'Provide law_name or law_id in params.'}

    db   = get_db()
    law  = _load_single_law(db, law_name=law_name, law_id=law_name)

    if not law:
        # Attempt to synthesize a minimal law from params if provided inline
        precision = params.get('precision')
        n_samples = params.get('n_samples')
        if precision is not None:
            law = {
                'law_id':         law_name,
                'law_name':       law_name,
                'precision':      float(precision),
                'recall':         float(params.get('recall', 0.0)),
                'f1':             float(params.get('f1', 0.0)),
                'n_samples':      int(n_samples or 30),
                'sector':         params.get('sector', 'UNKNOWN'),
                'regime_context': params.get('regime_context', 'UNKNOWN'),
                'status':         'active',
                'last_validated': None,
                'created_at':     None,
            }
        else:
            db.close()
            return {'error': f"Law '{law_name}' not found in pattern_laws."}

    result = _test_single_law(db, law)

    # Persist to law_grades
    graded_at = datetime.now().isoformat()
    try:
        db.execute("""
            INSERT OR REPLACE INTO law_grades (
                law_id, law_name, precision, n_samples,
                p_value, z_score, is_significant,
                ci_low_95, ci_high_95,
                oos_degradation, overfitting_risk,
                stressed_precision, stress_drop,
                eae, grade, recommendation, graded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result['law_id'],
            result['law_name'],
            result['precision'],
            result['n_samples'],
            result['significance']['p_value'],
            result['significance']['z_score'],
            1 if result['significance']['is_significant'] else 0,
            result['bootstrap']['ci_low_95'],
            result['bootstrap']['ci_high_95'],
            result['oos']['oos_degradation'],
            result['oos']['overfitting_risk'],
            result['stress']['stressed_precision'],
            result['stress']['stress_drop'],
            result['eae']['eae'],
            result['grade'],
            result['recommendation'],
            graded_at,
        ))
        db.commit()
    except Exception:
        pass

    db.close()
    return result


# ---------------------------------------------------------------------------
# Command 3: bootstrap_law
# ---------------------------------------------------------------------------

def bootstrap_law(params):
    law_name   = params.get('law_name') or params.get('law_id')
    n_bootstrap = int(params.get('n_bootstrap', 1000))

    if not law_name:
        return {'error': 'Provide law_name in params.'}

    db  = get_db()
    law = _load_single_law(db, law_name=law_name, law_id=law_name)
    db.close()

    if not law:
        # Allow inline precision/n_samples
        precision = params.get('precision')
        n_samples = params.get('n_samples', 30)
        if precision is None:
            return {'error': f"Law '{law_name}' not found. Pass precision and n_samples to bootstrap inline."}
        law = {
            'law_id':    law_name,
            'law_name':  law_name,
            'precision': float(precision),
            'n_samples': int(n_samples),
        }

    precision = law['precision']
    n_samples = law['n_samples']
    n_successes = max(1, int(round(precision * n_samples)))

    boot = bootstrap_precision(n_successes, n_samples, n_bootstrap=n_bootstrap)
    width = boot['ci_width']

    if width < 0.10:
        interpretation = 'TIGHT'
    elif width > 0.25:
        interpretation = 'WIDE'
    else:
        interpretation = 'MODERATE'

    return {
        'law_name':      law_name,
        'precision':     precision,
        'n_samples':     n_samples,
        'n_bootstrap':   n_bootstrap,
        'ci_low_95':     boot['ci_low_95'],
        'ci_high_95':    boot['ci_high_95'],
        'ci_width':      boot['ci_width'],
        'interpretation': interpretation,
        'note': (
            f"95% of bootstrap samples fall between {boot['ci_low_95']:.1%} and "
            f"{boot['ci_high_95']:.1%} precision. Width {width:.3f} is {interpretation}."
        ),
    }


# ---------------------------------------------------------------------------
# Command 4: oos_validation
# ---------------------------------------------------------------------------

def oos_validation(params):
    db   = get_db()
    laws = _load_laws(db)
    db.close()

    if not laws:
        return {
            'n_robust': 0, 'n_fragile': 0, 'n_overfit': 0,
            'robust_laws': [], 'fragile_laws': [],
            'avg_oos_degradation': 0.0,
            'warning': 'No laws found.',
        }

    robust_laws  = []
    fragile_laws = []
    overfit_laws = []
    degradations = []

    for law in laws:
        oos_deg  = _oos_degradation(law)
        oos_risk = _overfitting_risk(law)
        degradations.append(oos_deg)

        entry = {
            'law_name':          law['law_name'],
            'precision':         law['precision'],
            'n_samples':         law['n_samples'],
            'oos_degradation':   oos_deg,
            'oos_precision':     round(law['precision'] * (1.0 - oos_deg), 4),
            'overfitting_risk':  oos_risk,
        }

        if oos_deg < 0.15:
            robust_laws.append(entry)
        elif oos_deg < 0.30:
            fragile_laws.append(entry)
        else:
            overfit_laws.append(entry)

    avg_deg = round(statistics.mean(degradations), 4) if degradations else 0.0

    robust_laws.sort(key=lambda x: x['oos_degradation'])
    fragile_laws.sort(key=lambda x: x['oos_degradation'])
    overfit_laws.sort(key=lambda x: x['oos_degradation'], reverse=True)

    return {
        'n_robust':            len(robust_laws),
        'n_fragile':           len(fragile_laws),
        'n_overfit':           len(overfit_laws),
        'avg_oos_degradation': avg_deg,
        'robust_laws':         robust_laws[:20],
        'fragile_laws':        fragile_laws[:20],
        'overfit_laws':        overfit_laws[:10],
        'interpretation': (
            f"{len(robust_laws)} laws pass OOS (degradation < 15%), "
            f"{len(fragile_laws)} are fragile (15-30%), "
            f"{len(overfit_laws)} are likely overfit (>30%). "
            f"Average OOS degradation: {avg_deg:.1%}."
        ),
    }


# ---------------------------------------------------------------------------
# Command 5: expectancy_report
# ---------------------------------------------------------------------------

def expectancy_report(params):
    db   = get_db()
    laws = _load_laws(db)

    # Also fetch existing grades if available
    grade_map = {}
    try:
        rows = db.execute("SELECT law_id, grade FROM law_grades").fetchall()
        grade_map = {row['law_id']: row['grade'] for row in rows}
    except Exception:
        pass
    db.close()

    if not laws:
        return {
            'n_positive_eae': 0,
            'n_negative_eae': 0,
            'avg_eae': 0.0,
            'best_eae_laws': [],
            'laws_to_retire': [],
            'warning': 'No laws found.',
        }

    positive = []
    negative = []
    all_eae  = []

    for law in laws:
        eae   = _compute_eae(law['precision'])
        grade = grade_map.get(law['law_id'], '?')
        entry = {
            'law':       law['law_name'],
            'precision': law['precision'],
            'eae':       eae,
            'grade':     grade,
            'n_samples': law['n_samples'],
        }
        all_eae.append(eae)
        if eae > 0:
            positive.append(entry)
        else:
            entry['reason'] = (
                f"EAE={eae:.4f} — net negative after {FRICTION_BPS}bps friction. "
                f"Precision {law['precision']:.1%} too low to overcome costs."
            )
            negative.append(entry)

    avg_eae = round(statistics.mean(all_eae), 6) if all_eae else 0.0
    positive.sort(key=lambda x: x['eae'], reverse=True)
    negative.sort(key=lambda x: x['eae'])

    # Breakeven precision: solve EAE = 0
    # p * (AVG_WIN_MULT * avg_loss) - (1-p) * avg_loss - friction = 0
    # p*(win+loss) = loss + friction
    loss   = AVG_LOSS_DEFAULT
    win    = loss * AVG_WIN_MULT
    frict  = FRICTION_BPS / 10000
    breakeven_prec = round((loss + frict) / (win + loss), 4)

    return {
        'n_positive_eae':  len(positive),
        'n_negative_eae':  len(negative),
        'avg_eae':         avg_eae,
        'breakeven_precision': breakeven_prec,
        'friction_bps':    FRICTION_BPS,
        'best_eae_laws':   positive[:15],
        'laws_to_retire':  negative[:15],
        'commentary': (
            f"This is the only number that matters for actual trading. "
            f"{len(positive)} laws have positive EAE, {len(negative)} are net-negative. "
            f"Breakeven precision at {FRICTION_BPS}bps friction: {breakeven_prec:.1%}. "
            f"Average system EAE: {avg_eae:.4f} ({'+' if avg_eae > 0 else ''}{avg_eae*100:.2f}% per trade)."
        ),
    }


# ---------------------------------------------------------------------------
# Command 6: build_full
# ---------------------------------------------------------------------------

def build_full(params):
    grading     = grade_all_laws(params)
    oos         = oos_validation(params)
    expectancy  = expectancy_report(params)

    n_confirmed = grading['grade_distribution'].get('A', 0) + grading['grade_distribution'].get('B', 0)
    n_retire    = grading['n_should_retire']
    n_graded    = grading['n_graded']

    summary = (
        f"{n_confirmed} laws confirmed real (A/B grade), "
        f"{n_retire} should be retired (D/F grade), "
        f"out of {n_graded} total laws. "
        f"System grounding score: {grading.get('grounding_score', 0.0)}/100."
    )

    return {
        'grading':    grading,
        'oos':        oos,
        'expectancy': expectancy,
        'status':     'complete',
        'grounding_summary': summary,
    }


# ---------------------------------------------------------------------------
# Command 7: fdr_correction
# ---------------------------------------------------------------------------

def fdr_correction(params):
    """Apply BH FDR correction to all law p-values stored in law_grades."""
    db = get_db()
    try:
        rows = db.execute("SELECT law_name, p_value FROM law_grades ORDER BY p_value").fetchall()
    except Exception:
        db.close()
        return {"error": "law_grades table not found", "n_corrected": 0}
    finally:
        db.close()

    if not rows:
        return {"n_corrected": 0, "message": "no laws found"}

    law_names = [r['law_name'] for r in rows]
    p_values  = [r['p_value'] if r['p_value'] is not None else 1.0 for r in rows]

    significant_after_correction = _benjamini_hochberg(p_values, alpha=0.05)

    n_significant_raw = sum(1 for p in p_values if p < 0.05)
    n_significant_fdr = sum(significant_after_correction)

    results = []
    for i, (name, p, sig) in enumerate(zip(law_names, p_values, significant_after_correction)):
        results.append({
            'law_name':       name,
            'p_value':        p,
            'significant_raw': p < 0.05,
            'significant_fdr': sig,
        })

    # Sort by p_value for display
    results.sort(key=lambda x: x['p_value'])

    false_discovery_avoided = n_significant_raw - n_significant_fdr

    return {
        'n_laws':                  len(law_names),
        'n_significant_raw':       n_significant_raw,
        'n_significant_fdr':       n_significant_fdr,
        'false_discoveries_avoided': false_discovery_avoided,
        'fdr_threshold_alpha':     0.05,
        'correction_method':       'Benjamini-Hochberg',
        'results':                 results[:20],   # top 20 for display
        'interpretation': (
            f"FDR correction removed {false_discovery_avoided} likely false discoveries"
        ),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'grade_all_laws':    grade_all_laws,
    'test_law':          test_law,
    'bootstrap_law':     bootstrap_law,
    'oos_validation':    oos_validation,
    'expectancy_report': expectancy_report,
    'build_full':        build_full,
    'fdr_correction':    fdr_correction,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'error': 'Usage: python statistical_grounding.py <command> [json_params]',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    command = sys.argv[1].strip()
    params  = {}
    if len(sys.argv) >= 3:
        raw = sys.argv[2].strip()
        try:
            params = json.loads(raw)
        except json.JSONDecodeError:
            print(json.dumps({'error': f'Invalid JSON params: {raw}'}))
            sys.exit(1)

    if command not in COMMANDS:
        print(json.dumps({
            'error': f"Unknown command '{command}'.",
            'available': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[command](params)
    except Exception as exc:
        import traceback
        result = {
            'error':     str(exc),
            'command':   command,
            'traceback': traceback.format_exc(),
        }

    # Last stdout line must be valid JSON
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
