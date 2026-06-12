#!/usr/bin/env python3
"""
Phase 26: Adaptive Research Loop — Law Evolution Engine
EGX Market Intelligence System

Self-evolving law discovery, mutation, survival scoring, and research directive
generation. Addresses the core crisis: all 6 current laws are DEGRADING (6-12%
precision), so the system must discover and promote genuinely new laws rather
than endlessly refining broken ones.

Invocation: python adaptive_research_loop.py <command> '<json_params>'
Output: json.dumps(result, default=str) to stdout
"""

import os
import sys
import sqlite3
import json
import math
import random
import datetime
import statistics
from collections import defaultdict

# ── DB Setup ──────────────────────────────────────────────────────────────────

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
    CREATE TABLE IF NOT EXISTS law_lineage (
        lineage_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        law_id              TEXT NOT NULL,
        law_name            TEXT,
        parent_law_id       TEXT,
        generation          INTEGER DEFAULT 1,
        birth_date          TEXT,
        mutation_type       TEXT,
        mutation_description TEXT,
        fitness_at_birth    REAL DEFAULT 0,
        current_fitness     REAL DEFAULT 0,
        peak_fitness        REAL DEFAULT 0,
        survival_score      REAL DEFAULT 50,
        regime_stability    REAL DEFAULT 0,
        is_retired          INTEGER DEFAULT 0,
        retirement_reason   TEXT
    );

    CREATE TABLE IF NOT EXISTS research_directives (
        directive_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at      TEXT,
        directive_type  TEXT,
        target          TEXT,
        priority        REAL DEFAULT 0.5,
        rationale       TEXT,
        status          TEXT DEFAULT 'PENDING',
        result          TEXT,
        completed_at    TEXT
    );

    CREATE TABLE IF NOT EXISTS evolution_history (
        evo_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date                TEXT,
        laws_tested             INTEGER DEFAULT 0,
        laws_promoted           INTEGER DEFAULT 0,
        laws_retired            INTEGER DEFAULT 0,
        laws_mutated            INTEGER DEFAULT 0,
        best_fitness            REAL DEFAULT 0,
        avg_fitness             REAL DEFAULT 0,
        n_directives_generated  INTEGER DEFAULT 0,
        summary                 TEXT
    );
    """)
    db.commit()


# ── Constants ─────────────────────────────────────────────────────────────────

RANDOM_BASELINE = 0.08           # EGX ~8% daily move probability in data
STRONG_THRESHOLD = 70.0
ACTIVE_THRESHOLD = 50.0
WEAK_THRESHOLD   = 30.0

COMMANDS = {
    'assess_laws',
    'discover_new_laws',
    'mutate_weak_laws',
    'generate_directives',
    'run_evolution_cycle',
    'get_law_tree',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def now_str():
    return datetime.datetime.utcnow().isoformat()


def compute_survival_score(precision, regime_stability, oos_gap):
    """
    Survival score ∈ [0, 100]:
      - 50 pts from precision vs random
      - 25 pts from regime stability
      - 25 pts from in-sample / out-of-sample alignment (1 - oos_gap)
    oos_gap is stored as negative in the DB (e.g. -0.06 means OOS outperforms IS
    slightly), so we normalise: feasible_oos = max(0, 1 + oos_gap)
    """
    precision_score = min(50.0, (safe(precision) / RANDOM_BASELINE) * 50.0)
    stability_score = min(25.0, safe(regime_stability) * 25.0)
    # oos_gap: negative means IS > OOS (bad). Clamp so score ∈ [0, 25]
    oos_normalised  = max(0.0, 1.0 + safe(oos_gap))  # oos_gap negative → close to 1
    oos_score       = min(25.0, oos_normalised * 25.0)
    return precision_score + stability_score + oos_score


def classify_fitness(score):
    if score >= STRONG_THRESHOLD:
        return 'STRONG'
    if score >= ACTIVE_THRESHOLD:
        return 'ACTIVE'
    if score >= WEAK_THRESHOLD:
        return 'WEAK'
    return 'DEAD'


def precision_from_returns(returns, direction='UP', threshold=0.03):
    """Precision: fraction of activations that exceeded threshold in given direction."""
    if not returns:
        return 0.0
    if direction == 'UP':
        hits = sum(1 for r in returns if r > threshold)
    else:
        hits = sum(1 for r in returns if r < -threshold)
    return hits / len(returns)


def compute_vs_random(precision, baseline=RANDOM_BASELINE):
    if baseline == 0:
        return 1.0
    return precision / baseline


def pearson_corr(xs, ys):
    n = len(xs)
    if n < 4:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = math.sqrt(sum((x - mx) ** 2 for x in xs) + 1e-12)
    dy  = math.sqrt(sum((y - my) ** 2 for y in ys) + 1e-12)
    return num / (dx * dy)


def upsert_lineage(db, law_id, law_name, fitness, survival_score,
                   regime_stability=0.0, parent_law_id=None,
                   generation=1, mutation_type=None, mutation_description=None):
    """Insert or update a law_lineage entry."""
    existing = db.execute(
        "SELECT lineage_id, peak_fitness FROM law_lineage WHERE law_id=?",
        (law_id,)
    ).fetchone()
    if existing:
        peak = max(safe(existing['peak_fitness']), fitness)
        db.execute("""
            UPDATE law_lineage
            SET current_fitness=?, peak_fitness=?, survival_score=?,
                regime_stability=?
            WHERE lineage_id=?
        """, (fitness, peak, survival_score, regime_stability,
              existing['lineage_id']))
    else:
        db.execute("""
            INSERT INTO law_lineage
              (law_id, law_name, parent_law_id, generation, birth_date,
               mutation_type, mutation_description,
               fitness_at_birth, current_fitness, peak_fitness,
               survival_score, regime_stability)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (law_id, law_name, parent_law_id, generation, now_str(),
              mutation_type, mutation_description,
              fitness, fitness, fitness, survival_score, regime_stability))
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: assess_laws
# ═══════════════════════════════════════════════════════════════════════════════

def assess_laws(db, params):
    """
    Load all laws from universal_laws_p16, compute survival scores, classify
    fitness, build law_lineage entries. Returns a full assessment summary.
    """
    rows = db.execute("""
        SELECT pattern_id, pattern_name, direction, precision,
               law_status, regime_stability_score, oos_gap, n_activations
        FROM   universal_laws_p16
    """).fetchall()

    if not rows:
        return {
            'success': False,
            'error': 'No laws found in universal_laws_p16',
            'n_laws': 0,
        }

    results = []
    distribution = defaultdict(int)
    survival_scores = {}

    for row in rows:
        precision         = safe(row['precision'])
        regime_stability  = safe(row['regime_stability_score'])
        oos_gap           = safe(row['oos_gap'])
        law_id            = row['pattern_id']
        law_name          = row['pattern_name']
        law_status        = row['law_status'] or 'UNKNOWN'

        score      = compute_survival_score(precision, regime_stability, oos_gap)
        fitness    = score  # fitness == survival_score for assessed laws
        category   = classify_fitness(score)
        vs_random  = compute_vs_random(precision)

        distribution[category] += 1
        survival_scores[law_id] = {
            'name': law_name,
            'direction': row['direction'],
            'precision': round(precision, 4),
            'vs_random': round(vs_random, 3),
            'regime_stability': round(regime_stability, 4),
            'oos_gap': round(oos_gap, 4),
            'n_activations': row['n_activations'],
            'db_status': law_status,
            'survival_score': round(score, 2),
            'fitness_category': category,
        }
        results.append(survival_scores[law_id])

        upsert_lineage(
            db, law_id, law_name, fitness=fitness, survival_score=score,
            regime_stability=regime_stability, generation=1,
        )

    n_laws = len(rows)
    avg_score = statistics.mean(s['survival_score'] for s in results) if results else 0.0
    all_degrading = all(
        r['db_status'] in ('DEGRADING', 'ARCHIVED') for r in results
    )
    action_required = (
        'CRITICAL: All laws degrading — expand feature space beyond BB/RSI immediately'
        if all_degrading else
        'Some laws healthy — focus mutation effort on weak laws'
    )

    return {
        'success': True,
        'n_laws': n_laws,
        'fitness_distribution': dict(distribution),
        'avg_survival_score': round(avg_score, 2),
        'survival_scores': survival_scores,
        'all_degrading': all_degrading,
        'action_required': action_required,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: discover_new_laws
# ═══════════════════════════════════════════════════════════════════════════════

def discover_new_laws(db, params):
    """
    Sample counterfactual_events to test NEW feature-based laws not represented
    in existing BB/RSI-derived laws. Promotes qualified candidates to
    universal_laws_p16 and records lineage.
    """
    min_support          = float(params.get('min_support', 0.05))
    min_precision_ratio  = float(params.get('min_precision_vs_random', 1.2))

    # Load base universe for context
    all_events = db.execute("""
        SELECT symbol, precursor_date, pattern_id, pattern_name, outcome,
               feature_value, next_max_return, regime, sector
        FROM   counterfactual_events
        ORDER  BY RANDOM()
        LIMIT  5000
    """).fetchall()

    if not all_events:
        return {
            'success': False,
            'error': 'counterfactual_events is empty — cannot discover laws',
            'n_tested': 0, 'n_promoted': 0,
        }

    # Build OHLCV lookup for feature computation
    ohlcv_rows = db.execute("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM   ohlcv_history_execution
        ORDER  BY symbol, bar_time
    """).fetchall()

    # Organise OHLCV per symbol
    ohlcv = defaultdict(list)
    for r in ohlcv_rows:
        ohlcv[r['symbol']].append({
            'bar_time': r['bar_time'],
            'open': safe(r['open']),
            'high': safe(r['high']),
            'low': safe(r['low']),
            'close': safe(r['close']),
            'volume': safe(r['volume']),
        })
    # Sort each symbol's bars chronologically
    for sym in ohlcv:
        ohlcv[sym].sort(key=lambda b: b['bar_time'])

    def _bar_time_to_date(bar_time):
        """Convert bar_time (unix int or ISO string) to 'YYYY-MM-DD'."""
        try:
            ts = int(bar_time)
            return datetime.date.fromtimestamp(ts).isoformat()
        except (TypeError, ValueError, OSError):
            return str(bar_time)[:10]

    def get_bar_index(sym, date_str):
        bars = ohlcv.get(sym, [])
        target = str(date_str)[:10]
        for i, b in enumerate(bars):
            if _bar_time_to_date(b['bar_time']) >= target:
                return i
        return None

    # ── Feature extraction helpers ─────────────────────────────────────────

    def volume_ratio_5d_20d(sym, idx):
        """avg_5d_volume / avg_20d_volume — measures unusual volume surge."""
        bars = ohlcv.get(sym, [])
        if idx is None or idx < 20:
            return None
        vols = [b['volume'] for b in bars[max(0, idx-20):idx] if b['volume'] > 0]
        if len(vols) < 10:
            return None
        avg5  = statistics.mean(vols[-5:])
        avg20 = statistics.mean(vols)
        return avg5 / avg20 if avg20 > 0 else None

    def momentum_3d(sym, idx):
        """Price return over the 3 bars ending at idx."""
        bars = ohlcv.get(sym, [])
        if idx is None or idx < 3:
            return None
        start = bars[idx - 3]['close']
        end   = bars[idx]['close']
        return (end - start) / start if start > 0 else None

    def day_of_week_feature(date_str):
        """0=Monday … 4=Friday."""
        try:
            d = datetime.date.fromisoformat(str(date_str)[:10])
            return d.weekday()
        except Exception:
            return None

    def regime_transition(current_regime, prev_regime):
        """1 if regime changed, 0 otherwise."""
        if current_regime is None or prev_regime is None:
            return None
        return 1 if current_regime != prev_regime else 0

    # ── Build candidate buckets ────────────────────────────────────────────

    # Candidate definitions: (name, direction, feature_fn, threshold, cmp)
    # cmp: 'gt' means feature > threshold triggers the law
    candidate_specs = [
        ('High Volume Surge UP',    'UP',   'vol_ratio_5d', 1.5, 'gt'),
        ('High Volume Surge DOWN',  'DOWN', 'vol_ratio_5d', 1.5, 'gt'),
        ('Low Volume Contraction',  'UP',   'vol_ratio_5d', 0.5, 'lt'),
        ('Momentum 3d Positive',    'UP',   'momentum_3d',  0.02, 'gt'),
        ('Momentum 3d Negative',    'DOWN', 'momentum_3d', -0.02, 'lt'),
        ('Monday Effect UP',        'UP',   'dow', 0, 'eq'),
        ('Friday Effect DOWN',      'DOWN', 'dow', 4, 'eq'),
        ('Mid-Week Momentum UP',    'UP',   'dow', 2, 'eq'),
        ('Regime Transition Signal','UP',   'regime_trans', 1, 'eq'),
    ]

    n_total_universe = len(all_events)
    candidates_tested = []
    new_laws = []

    # Pre-compute features for all events
    event_features = []
    prev_regimes = {}  # symbol → last regime seen

    for ev in all_events:
        sym  = ev['symbol']
        date = ev['precursor_date']
        idx  = get_bar_index(sym, date)

        features = {
            'vol_ratio_5d': volume_ratio_5d_20d(sym, idx),
            'momentum_3d':  momentum_3d(sym, idx),
            'dow':          day_of_week_feature(date),
            'regime_trans': regime_transition(
                ev['regime'], prev_regimes.get(sym)
            ),
        }
        prev_regimes[sym] = ev['regime']

        event_features.append({
            'ev': ev,
            'features': features,
        })

    for spec_name, spec_dir, feat_key, threshold, cmp_op in candidate_specs:
        # Filter events where feature exists
        activated = []
        for ef in event_features:
            fval = ef['features'].get(feat_key)
            if fval is None:
                continue
            if cmp_op == 'gt' and fval > threshold:
                activated.append(ef['ev'])
            elif cmp_op == 'lt' and fval < threshold:
                activated.append(ef['ev'])
            elif cmp_op == 'eq' and int(fval) == int(threshold):
                activated.append(ef['ev'])

        n_activated = len(activated)
        support     = n_activated / n_total_universe if n_total_universe > 0 else 0.0

        if n_activated < 30:
            candidates_tested.append({
                'name': spec_name, 'support': round(support, 4),
                'reason_skipped': 'insufficient_activations',
            })
            continue

        # Compute precision
        returns = [safe(ev['next_max_return']) for ev in activated]
        prec    = precision_from_returns(
            returns, direction=spec_dir, threshold=0.03
        )
        vs_random = compute_vs_random(prec)

        candidates_tested.append({
            'name': spec_name,
            'direction': spec_dir,
            'feature': feat_key,
            'threshold': threshold,
            'n_activations': n_activated,
            'support': round(support, 4),
            'precision': round(prec, 4),
            'vs_random': round(vs_random, 3),
            'qualified': (vs_random >= min_precision_ratio and support >= min_support),
        })

        if vs_random >= min_precision_ratio and support >= min_support:
            # Check not already present
            import hashlib
            law_id = 'DISC_' + hashlib.md5(
                f"{spec_name}{spec_dir}{feat_key}{threshold}".encode()
            ).hexdigest()[:12]

            existing = db.execute(
                "SELECT 1 FROM universal_laws_p16 WHERE pattern_id=?",
                (law_id,)
            ).fetchone()

            if not existing:
                score = compute_survival_score(
                    prec, regime_stability=0.2, oos_gap=-0.05
                )
                db.execute("""
                    INSERT OR IGNORE INTO universal_laws_p16
                      (pattern_id, pattern_name, direction, precision,
                       law_status, regime_stability_score, oos_gap, n_activations)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (law_id, spec_name, spec_dir, prec,
                      'ACTIVE', 0.2, -0.05, n_activated))
                db.commit()

                upsert_lineage(
                    db, law_id, spec_name, fitness=score, survival_score=score,
                    regime_stability=0.2, parent_law_id=None,
                    generation=1, mutation_type='DISCOVERY',
                    mutation_description=f"Discovered via counterfactual feature: {feat_key} {cmp_op} {threshold}",
                )

                new_laws.append({
                    'law_id': law_id,
                    'name': spec_name,
                    'direction': spec_dir,
                    'precision': round(prec, 4),
                    'vs_random': round(vs_random, 3),
                    'support': round(support, 4),
                    'n_activations': n_activated,
                    'survival_score': round(score, 2),
                })

    return {
        'success': True,
        'n_tested': len(candidates_tested),
        'n_promoted': len(new_laws),
        'candidates_tested': candidates_tested,
        'new_laws': new_laws,
        'notes': (
            'Discovery scans volume surge, momentum 3d, day-of-week, and regime '
            'transition features not present in existing BB/RSI laws.'
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: mutate_weak_laws
# ═══════════════════════════════════════════════════════════════════════════════

def mutate_weak_laws(db, params):
    """
    For each DEGRADING or ARCHIVED law: test threshold variations (±10/20/30%)
    and combination candidates. Record results in law_competition.
    Promote improved variants to universal_laws_p16.
    """
    laws = db.execute("""
        SELECT pattern_id, pattern_name, direction, precision,
               law_status, regime_stability_score, oos_gap, n_activations
        FROM   universal_laws_p16
        WHERE  law_status IN ('DEGRADING', 'ARCHIVED')
    """).fetchall()

    if not laws:
        return {
            'success': True,
            'message': 'No weak/degrading laws to mutate',
            'n_mutations_tested': 0,
            'n_improvements': 0,
            'best_mutations': [],
        }

    # Load activation data from counterfactual_events per law
    event_rows = db.execute("""
        SELECT pattern_id, pattern_name, next_max_return, feature_value, regime
        FROM   counterfactual_events
        WHERE  next_max_return IS NOT NULL
    """).fetchall()

    events_by_law = defaultdict(list)
    for ev in event_rows:
        events_by_law[ev['pattern_id']].append({
            'return': safe(ev['next_max_return']),
            'feature_value': safe(ev['feature_value']),
            'regime': ev['regime'],
        })

    # Ensure law_competition table has required columns
    try:
        db.execute("SELECT beats_base FROM law_competition LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE law_competition ADD COLUMN beats_base INTEGER DEFAULT 0")
        db.commit()

    threshold_multipliers = [0.7, 0.8, 0.9, 1.1, 1.2, 1.3]
    n_mutations_tested  = 0
    n_improvements      = 0
    best_mutations      = []

    for law in laws:
        law_id   = law['pattern_id']
        law_name = law['pattern_name']
        direction = law['direction']
        base_prec = safe(law['precision'])
        activations = events_by_law.get(law_id, [])

        if len(activations) < 20:
            continue

        returns = [a['return'] for a in activations]
        fvals   = [a['feature_value'] for a in activations]

        for mult in threshold_multipliers:
            # Simulate applying a tighter/looser threshold on feature_value
            median_fval = statistics.median(fvals) if fvals else 0.0
            new_threshold = median_fval * mult

            # Filter activations by new threshold
            if direction == 'UP':
                filtered = [r for r, f in zip(returns, fvals) if f >= new_threshold]
            else:
                filtered = [r for r, f in zip(returns, fvals) if f <= new_threshold]

            if len(filtered) < 10:
                continue

            variant_prec = precision_from_returns(filtered, direction=direction)
            improvement  = (variant_prec - base_prec) * 100.0  # in pp
            beats_base   = 1 if variant_prec > base_prec else 0
            n_mutations_tested += 1

            variant_name = f"{law_name} (thresh×{mult:.1f})"

            db.execute("""
                INSERT OR IGNORE INTO law_competition
                  (pattern_id, variant_name, variant_precision,
                   base_precision, improvement_pp, beats_base)
                VALUES (?,?,?,?,?,?)
            """, (law_id, variant_name,
                  round(variant_prec, 4), round(base_prec, 4),
                  round(improvement, 4), beats_base))
            db.commit()

            if beats_base and improvement > 1.5:
                n_improvements += 1
                score = compute_survival_score(
                    variant_prec,
                    safe(law['regime_stability_score']),
                    safe(law['oos_gap']),
                )
                best_mutations.append({
                    'parent_law_id': law_id,
                    'parent_name': law_name,
                    'variant_name': variant_name,
                    'threshold_multiplier': mult,
                    'base_precision': round(base_prec, 4),
                    'variant_precision': round(variant_prec, 4),
                    'improvement_pp': round(improvement, 2),
                    'survival_score': round(score, 2),
                })

                # Promote best variant if it exceeds RANDOM_BASELINE * 1.3
                if variant_prec >= RANDOM_BASELINE * 1.3:
                    import hashlib
                    mut_id = 'MUT_' + hashlib.md5(
                        f"{law_id}{mult}".encode()
                    ).hexdigest()[:12]
                    existing = db.execute(
                        "SELECT 1 FROM universal_laws_p16 WHERE pattern_id=?",
                        (mut_id,)
                    ).fetchone()
                    if not existing:
                        db.execute("""
                            INSERT OR IGNORE INTO universal_laws_p16
                              (pattern_id, pattern_name, direction, precision,
                               law_status, regime_stability_score, oos_gap,
                               n_activations)
                            VALUES (?,?,?,?,?,?,?,?)
                        """, (mut_id, variant_name, direction,
                              round(variant_prec, 4), 'ACTIVE',
                              safe(law['regime_stability_score']),
                              safe(law['oos_gap']),
                              len(filtered)))
                        db.commit()

                        upsert_lineage(
                            db, mut_id, variant_name, fitness=score,
                            survival_score=score,
                            regime_stability=safe(law['regime_stability_score']),
                            parent_law_id=law_id, generation=2,
                            mutation_type='THRESHOLD_SHIFT',
                            mutation_description=(
                                f"Threshold multiplied by {mult:.1f}; "
                                f"precision improved {improvement:.2f}pp"
                            ),
                        )

                        db.execute("""
                            INSERT INTO law_mutations
                              (pattern_id, pattern_name, mutation_type, delta,
                               pre_support, post_support, t_stat, confidence,
                               created_at)
                            VALUES (?,?,?,?,?,?,?,?,?)
                        """, (mut_id, variant_name, 'THRESHOLD_SHIFT',
                              round(variant_prec - base_prec, 4),
                              round(base_prec, 4), round(variant_prec, 4),
                              0.0, round(min(0.99, variant_prec / RANDOM_BASELINE / 2), 4),
                              now_str()))
                        db.commit()

    best_mutations.sort(key=lambda x: x['improvement_pp'], reverse=True)
    return {
        'success': True,
        'n_weak_laws_processed': len(laws),
        'n_mutations_tested': n_mutations_tested,
        'n_improvements': n_improvements,
        'best_mutations': best_mutations[:10],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: generate_directives
# ═══════════════════════════════════════════════════════════════════════════════

def generate_directives(db, params):
    """
    Analyse current system state and emit prioritised research directives.
    Saves directives to research_directives table.
    """
    directives = []

    # ── Check 1: Law health ────────────────────────────────────────────────
    laws = db.execute(
        "SELECT law_status, precision FROM universal_laws_p16"
    ).fetchall()
    n_laws = len(laws)
    n_degrading = sum(
        1 for l in laws if l['law_status'] in ('DEGRADING', 'ARCHIVED')
    )
    avg_precision = statistics.mean(
        safe(l['precision']) for l in laws
    ) if laws else 0.0

    if n_laws == 0 or n_degrading == n_laws:
        directives.append({
            'directive_type': 'FEATURE_EXPANSION',
            'target': 'universal_laws_p16',
            'priority': 1.0,
            'rationale': (
                f"All {n_laws} laws are in DEGRADING or ARCHIVED state "
                f"(avg precision {avg_precision:.1%}). The BB/RSI feature space "
                "is exhausted. Expand to: volume regime, macro coupling, "
                "day-of-week, sector rotation, cross-asset momentum, ADX, "
                "VWAP deviation, earnings proximity."
            ),
        })
    elif n_degrading > n_laws // 2:
        directives.append({
            'directive_type': 'FEATURE_EXPANSION',
            'target': 'universal_laws_p16',
            'priority': 0.85,
            'rationale': (
                f"{n_degrading}/{n_laws} laws degrading — partial feature exhaustion. "
                "Supplement existing laws with volume-based and macro-correlated features."
            ),
        })

    # ── Check 2: Sector DNA diversity ─────────────────────────────────────
    archetypes = db.execute(
        "SELECT DISTINCT sector_archetype FROM sector_dna"
    ).fetchall()
    unique_archetypes = [a['sector_archetype'] for a in archetypes if a['sector_archetype']]

    if len(unique_archetypes) <= 1:
        directives.append({
            'directive_type': 'DNA_RECLASSIFICATION',
            'target': 'sector_dna',
            'priority': 0.80,
            'rationale': (
                f"All sectors share the same archetype: '{unique_archetypes[0] if unique_archetypes else 'NONE'}'. "
                "DNA classification is degenerate. Re-run clustering with "
                "richer features: explosion_rate, false_breakout_rate, "
                "synchronization_pct, contagion_delay_days, rotation_period_days."
            ),
        })

    # ── Check 3: Historical data depth ────────────────────────────────────
    bar_count_row = db.execute(
        "SELECT COUNT(DISTINCT bar_time) as n_bars FROM ohlcv_history_execution"
    ).fetchone()
    n_bars = bar_count_row['n_bars'] if bar_count_row else 0

    if n_bars < 750:  # < ~3 years of trading days
        directives.append({
            'directive_type': 'DATA_EXPANSION',
            'target': 'ohlcv_history_execution',
            'priority': 0.90,
            'rationale': (
                f"Only {n_bars} unique date-bars in ohlcv_history_execution (< 3 years). "
                "Law validation requires 3+ years to capture full market cycles. "
                "Backfill historical data to 2020+ for all active symbols."
            ),
        })

    # ── Check 4: Causal structure ─────────────────────────────────────────
    has_causal = db.execute(
        "SELECT COUNT(*) as n FROM knowledge_graph_edges"
    ).fetchone()
    n_causal = has_causal['n'] if has_causal else 0

    if n_causal < 10:
        directives.append({
            'directive_type': 'CAUSAL_ANALYSIS',
            'target': 'knowledge_graph_edges',
            'priority': 0.75,
            'rationale': (
                f"Only {n_causal} causal edges in knowledge graph. "
                "Run PCMCI sector-level causal analysis to discover leading "
                "indicators within and across sectors. Causal laws are more "
                "robust than correlation-based patterns."
            ),
        })

    # ── Check 5: Hypothesis backlog ───────────────────────────────────────
    n_hypotheses = db.execute(
        "SELECT COUNT(*) as n FROM hypothesis_candidates"
    ).fetchone()
    n_hyp = n_hypotheses['n'] if n_hypotheses else 0
    n_tested = db.execute(
        "SELECT COUNT(*) as n FROM hypothesis_candidates WHERE status='TESTED'"
    ).fetchone()
    n_tested = n_tested['n'] if n_tested else 0

    if n_hyp > 0 and (n_hyp - n_tested) > 10:
        directives.append({
            'directive_type': 'HYPOTHESIS_TESTING',
            'target': 'hypothesis_candidates',
            'priority': 0.70,
            'rationale': (
                f"{n_hyp - n_tested} untested hypotheses are pending. "
                "Run batch hypothesis validation against full ohlcv_history_execution "
                "before generating more candidates."
            ),
        })

    # ── Check 6: OOS gap check ────────────────────────────────────────────
    negative_oos = [l for l in laws if safe(l['precision']) < RANDOM_BASELINE]
    if len(negative_oos) > n_laws // 2:
        directives.append({
            'directive_type': 'OOS_VALIDATION',
            'target': 'universal_laws_p16',
            'priority': 0.65,
            'rationale': (
                f"{len(negative_oos)}/{n_laws} laws have precision below random "
                "baseline. This suggests the 2021-2023 training period is not "
                "representative. Perform time-series cross-validation with "
                "walk-forward splits."
            ),
        })

    # ── Check 7: Regime coverage ──────────────────────────────────────────
    regimes = db.execute(
        "SELECT DISTINCT regime FROM counterfactual_events WHERE regime IS NOT NULL"
    ).fetchall()
    regime_list = [r['regime'] for r in regimes]
    if len(regime_list) < 3:
        directives.append({
            'directive_type': 'REGIME_EXPANSION',
            'target': 'regime_history',
            'priority': 0.60,
            'rationale': (
                f"Only {len(regime_list)} distinct regimes in training data: "
                f"{regime_list}. Add CRISIS, RALLY, SIDEWAYS, TRENDING regimes "
                "to make laws regime-aware."
            ),
        })

    # Sort by priority
    directives.sort(key=lambda d: d['priority'], reverse=True)

    # Save to DB
    ts = now_str()
    for d in directives:
        db.execute("""
            INSERT INTO research_directives
              (created_at, directive_type, target, priority, rationale)
            VALUES (?,?,?,?,?)
        """, (ts, d['directive_type'], d['target'], d['priority'], d['rationale']))
    db.commit()

    return {
        'success': True,
        'n_directives': len(directives),
        'generated_at': ts,
        'priority_list': directives,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: run_evolution_cycle
# ═══════════════════════════════════════════════════════════════════════════════

def run_evolution_cycle(db, params):
    """
    Full pipeline: assess → directives → mutate → discover → log.
    """
    run_date = now_str()

    # 1. Assess
    assessment = assess_laws(db, {})
    n_laws      = assessment.get('n_laws', 0)
    dist        = assessment.get('fitness_distribution', {})

    # 2. Directives
    directives = generate_directives(db, {})
    n_directives = directives.get('n_directives', 0)

    # 3. Mutate
    mutations = mutate_weak_laws(db, {})
    n_mutated   = mutations.get('n_improvements', 0)
    n_mut_tested = mutations.get('n_mutations_tested', 0)

    # 4. Discover
    discovery = discover_new_laws(db, {
        'min_support': 0.04,
        'min_precision_vs_random': 1.2,
    })
    n_promoted = discovery.get('n_promoted', 0)
    n_tested   = discovery.get('n_tested', 0)

    # 5. Final state
    final_laws = db.execute(
        "SELECT precision FROM universal_laws_p16 WHERE law_status NOT IN ('ARCHIVED')"
    ).fetchall()
    precisions = [safe(l['precision']) for l in final_laws]
    best_fitness = max(precisions) if precisions else 0.0
    avg_fitness  = statistics.mean(precisions) if precisions else 0.0

    summary = (
        f"Evolution cycle: {n_laws} laws assessed, "
        f"{n_mut_tested} mutations tested ({n_mutated} improvements), "
        f"{n_tested} candidates tested ({n_promoted} new laws promoted), "
        f"{n_directives} directives generated. "
        f"Best precision: {best_fitness:.1%}, Avg: {avg_fitness:.1%}."
    )

    db.execute("""
        INSERT INTO evolution_history
          (run_date, laws_tested, laws_promoted, laws_retired, laws_mutated,
           best_fitness, avg_fitness, n_directives_generated, summary)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (run_date, n_laws, n_promoted, 0, n_mutated,
          round(best_fitness, 4), round(avg_fitness, 4),
          n_directives, summary))
    db.commit()

    return {
        'success': True,
        'run_date': run_date,
        'phase_assess': {
            'n_laws': n_laws,
            'fitness_distribution': dist,
            'all_degrading': assessment.get('all_degrading'),
        },
        'phase_directives': {
            'n_directives': n_directives,
            'top_priority': (
                directives['priority_list'][0] if directives.get('priority_list') else None
            ),
        },
        'phase_mutations': {
            'n_tested': n_mut_tested,
            'n_improvements': n_mutated,
            'best': mutations.get('best_mutations', [])[:3],
        },
        'phase_discovery': {
            'n_candidates_tested': n_tested,
            'n_promoted': n_promoted,
            'new_laws': discovery.get('new_laws', []),
        },
        'final_state': {
            'best_precision': round(best_fitness, 4),
            'avg_precision': round(avg_fitness, 4),
        },
        'summary': summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: get_law_tree
# ═══════════════════════════════════════════════════════════════════════════════

def get_law_tree(db, params):
    """
    Return the full law lineage tree showing which laws descended from which,
    grouped by generation, with fitness stats per generation.
    """
    rows = db.execute("""
        SELECT lineage_id, law_id, law_name, parent_law_id, generation,
               birth_date, mutation_type, mutation_description,
               fitness_at_birth, current_fitness, peak_fitness,
               survival_score, regime_stability, is_retired, retirement_reason
        FROM law_lineage
        ORDER BY generation ASC, current_fitness DESC
    """).fetchall()

    if not rows:
        return {
            'success': True,
            'message': 'No lineage data yet — run assess_laws first',
            'law_tree': {},
            'generation_stats': {},
        }

    # Build parent → children map
    children_of = defaultdict(list)
    by_id       = {}
    for row in rows:
        d = dict(row)
        by_id[row['law_id']] = d
        if row['parent_law_id']:
            children_of[row['parent_law_id']].append(row['law_id'])

    # Recursive tree builder
    def build_node(law_id):
        node = dict(by_id.get(law_id, {'law_id': law_id}))
        kids = children_of.get(law_id, [])
        if kids:
            node['children'] = [build_node(k) for k in kids]
        return node

    # Find root laws (generation == 1, no parent)
    roots = [
        row['law_id'] for row in rows
        if row['generation'] == 1 or not row['parent_law_id']
    ]
    # Deduplicate roots
    seen_roots = set()
    unique_roots = []
    for r in roots:
        if r not in seen_roots:
            seen_roots.add(r)
            unique_roots.append(r)

    law_tree = {r: build_node(r) for r in unique_roots}

    # Generation stats
    gen_data = defaultdict(list)
    for row in rows:
        gen_data[row['generation']].append({
            'fitness': safe(row['current_fitness']),
            'survival': safe(row['survival_score']),
            'retired': row['is_retired'],
        })

    generation_stats = {}
    for gen, entries in gen_data.items():
        fitnesses = [e['fitness'] for e in entries]
        n_retired = sum(e['retired'] for e in entries)
        generation_stats[gen] = {
            'n_laws': len(entries),
            'avg_fitness': round(statistics.mean(fitnesses), 2) if fitnesses else 0.0,
            'max_fitness': round(max(fitnesses), 2) if fitnesses else 0.0,
            'n_retired': n_retired,
        }

    return {
        'success': True,
        'n_total_laws': len(rows),
        'n_generations': len(generation_stats),
        'generation_stats': generation_stats,
        'law_tree': law_tree,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════════════════════

DISPATCH = {
    'assess_laws':          assess_laws,
    'discover_new_laws':    discover_new_laws,
    'mutate_weak_laws':     mutate_weak_laws,
    'generate_directives':  generate_directives,
    'run_evolution_cycle':  run_evolution_cycle,
    'get_law_tree':         get_law_tree,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'success': False,
            'error': 'Usage: adaptive_research_loop.py <command> [json_params]',
            'available_commands': list(DISPATCH.keys()),
        }, default=str))
        sys.exit(1)

    command = sys.argv[1].strip()
    raw_params = sys.argv[2] if len(sys.argv) > 2 else '{}'

    try:
        params = json.loads(raw_params)
    except json.JSONDecodeError as e:
        print(json.dumps({
            'success': False,
            'error': f'Invalid JSON params: {e}',
            'command': command,
        }, default=str))
        sys.exit(1)

    if command not in DISPATCH:
        print(json.dumps({
            'success': False,
            'error': f'Unknown command: {command}',
            'available_commands': list(DISPATCH.keys()),
        }, default=str))
        sys.exit(1)

    try:
        db     = get_db()
        fn     = DISPATCH[command]
        result = fn(db, params)
        db.close()
    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'command': command,
        }, default=str))
        sys.exit(1)

    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
