#!/usr/bin/env python3
"""
cognitive_arbitration.py — Phase 34
EGX Autonomous Quant System: Cognitive Arbitration Layer

The Decision Constitution. When signals from 36 phases conflict, this engine
decides WHO WINS and produces a single unified decision: ENTER | AVOID | WAIT.

Invocation: python cognitive_arbitration.py <command> '<json_params>'
Output: last stdout line = valid JSON

Commands:
  arbitrate_symbol    — full arbitration for one symbol
  arbitrate_all       — arbitrate all symbols in universe
  daily_decisions     — top ENTER decisions for today
  constitution_report — explain current constitution weights
  build_full          — arbitrate_all + daily_decisions + constitution_report
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, date
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths & DB
# ---------------------------------------------------------------------------

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# EGX-Specific Constants
# ---------------------------------------------------------------------------

RANDOM_BASELINE = 0.182   # EGX random precision baseline
COMMISSION_BPS  = 50      # per side
SPREAD_BPS      = 50

# ---------------------------------------------------------------------------
# The Decision Constitution — HARDCODED PRIORITY WEIGHTS by regime
# ---------------------------------------------------------------------------

CONSTITUTION = {
    'TRENDING_UP': {
        'execution_feasibility': 0.30,
        'law_precision':         0.25,
        'regime_alignment':      0.20,
        'causal_confidence':     0.15,
        'graph_health':          0.10,
    },
    'TRENDING_DOWN': {
        'failure_memory':        0.30,
        'execution_feasibility': 0.25,
        'regime_alignment':      0.20,
        'law_precision':         0.15,
        'transition_probability':0.10,
    },
    'VOLATILE': {
        'failure_memory':        0.35,
        'execution_feasibility': 0.25,
        'transition_probability':0.20,
        'regime_alignment':      0.15,
        'law_precision':         0.05,
    },
    'SIDEWAYS': {
        'execution_feasibility': 0.25,
        'law_precision':         0.25,
        'causal_confidence':     0.20,
        'regime_alignment':      0.20,
        'graph_health':          0.10,
    },
    'TRANSITION': {
        'transition_probability':0.40,
        'failure_memory':        0.30,
        'execution_feasibility': 0.20,
        'graph_health':          0.10,
    },
    'CRISIS': {
        'cash_flag': 1.0
    }
}

# Regime limits on max position sizes
REGIME_MAX_SIZE = {
    'TRENDING_UP':   15.0,
    'TRENDING_DOWN':  8.0,
    'VOLATILE':       5.0,
    'SIDEWAYS':      10.0,
    'TRANSITION':     5.0,
    'CRISIS':         0.0,
}

# Market posture thresholds
EWI_HIGH   = 70.0
EWI_MEDIUM = 40.0


# ---------------------------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS arbitration_decisions (
        symbol TEXT,
        date TEXT,
        decision TEXT,
        confidence REAL,
        score REAL,
        regime TEXT,
        ewi REAL,
        reasoning TEXT,
        veto_triggered INTEGER,
        veto_reason TEXT,
        blocking_factors TEXT,
        soft_warnings TEXT,
        suggested_size_pct REAL,
        dominant_source TEXT,
        signal_breakdown TEXT,
        computed_at TEXT,
        PRIMARY KEY (symbol, date)
    );

    CREATE TABLE IF NOT EXISTS daily_decision_summary (
        date TEXT PRIMARY KEY,
        regime TEXT,
        ewi REAL,
        n_enter INTEGER,
        n_wait INTEGER,
        n_avoid INTEGER,
        n_veto INTEGER,
        market_posture TEXT,
        top_decisions TEXT,
        computed_at TEXT
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Data Readers (all graceful try/except)
# ---------------------------------------------------------------------------

def read_current_regime(db):
    """Read latest regime row from regime_transition_signals."""
    try:
        row = db.execute(
            "SELECT current_regime, early_warning_index, prob_10d "
            "FROM regime_transition_signals ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                'regime': row['current_regime'] or 'SIDEWAYS',
                'ewi':    float(row['early_warning_index'] or 50.0),
                'prob_10d': float(row['prob_10d'] or 0.0),
            }
    except Exception:
        pass
    return {'regime': 'SIDEWAYS', 'ewi': 50.0, 'prob_10d': 0.0}


def read_intelligence_scores(db, symbol):
    """Phase 29: intelligence_scores for a symbol."""
    try:
        row = db.execute(
            "SELECT intelligence_score, execution_component, regime_component, "
            "causal_component, law_component "
            "FROM intelligence_scores WHERE symbol=? ORDER BY rowid DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            return {
                'intelligence_score':  float(row['intelligence_score'] or 0),
                'execution_component': float(row['execution_component'] or 0),
                'regime_component':    float(row['regime_component'] or 0),
                'causal_component':    float(row['causal_component'] or 0),
                'law_component':       float(row['law_component'] or 0),
            }
    except Exception:
        pass
    return None


def read_liquidity_profile(db, symbol):
    """Phase 27: liquidity_profiles."""
    try:
        row = db.execute(
            "SELECT tier, avg_daily_volume FROM liquidity_profiles WHERE symbol=? LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            return {'tier': row['tier'] or 'ILLIQUID', 'avg_daily_volume': float(row['avg_daily_volume'] or 0)}
    except Exception:
        pass
    return {'tier': 'ILLIQUID', 'avg_daily_volume': 0.0}


def read_failure_intelligence(db, symbol):
    """Phase 23: failure_intelligence."""
    try:
        row = db.execute(
            "SELECT archetype, confidence FROM failure_intelligence "
            "WHERE symbol=? ORDER BY confidence DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            return {'archetype': row['archetype'], 'confidence': float(row['confidence'] or 0)}
    except Exception:
        pass
    return None


def read_explosion_readiness(db, symbol):
    """Phase 24: explosion_readiness."""
    try:
        row = db.execute(
            "SELECT readiness_score FROM explosion_readiness WHERE symbol=? LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            return float(row['readiness_score'] or 0)
    except Exception:
        pass
    return None


def read_best_law_precision(db):
    """Phase 16: best active law precision (global)."""
    try:
        row = db.execute(
            "SELECT MAX(precision) as best FROM pattern_laws WHERE status='ACTIVE'"
        ).fetchone()
        if row and row['best'] is not None:
            return float(row['best'])
    except Exception:
        pass
    return None


def read_best_law_precision_for_symbol(db, symbol):
    """Phase 16: best active law precision for a symbol if column exists."""
    # Try symbol-specific first, fallback to global
    try:
        row = db.execute(
            "SELECT MAX(precision) as best FROM pattern_laws WHERE status='ACTIVE' AND symbol=?",
            (symbol,)
        ).fetchone()
        if row and row['best'] is not None:
            return float(row['best'])
    except Exception:
        pass
    return read_best_law_precision(db)


def read_anti_law_veto(db, symbol):
    """Phase 35: anti_laws veto check (table may not exist)."""
    try:
        row = db.execute(
            "SELECT is_veto FROM anti_laws WHERE symbol=? AND is_veto=1 LIMIT 1",
            (symbol,)
        ).fetchone()
        return row is not None
    except Exception:
        pass
    return False


def read_statistical_grade(db):
    """Phase 36: worst grade from law_grades (may not exist)."""
    try:
        row = db.execute(
            "SELECT grade FROM law_grades WHERE is_significant=0 "
            "ORDER BY CASE grade WHEN 'F' THEN 0 WHEN 'D' THEN 1 ELSE 2 END LIMIT 1"
        ).fetchone()
        if row:
            return row['grade']
    except Exception:
        pass
    return None


def read_portfolio_regime(db):
    """Phase 32: portfolio_allocations latest regime."""
    try:
        row = db.execute(
            "SELECT regime FROM portfolio_allocations ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return row['regime']
    except Exception:
        pass
    return None


def read_all_symbols(db):
    """Read all distinct symbols from ohlcv or stock_dna."""
    symbols = set()
    try:
        rows = db.execute("SELECT DISTINCT symbol FROM ohlcv").fetchall()
        for r in rows:
            symbols.add(r['symbol'])
    except Exception:
        pass
    if not symbols:
        try:
            rows = db.execute("SELECT DISTINCT symbol FROM stock_dna").fetchall()
            for r in rows:
                symbols.add(r['symbol'])
        except Exception:
            pass
    return sorted(symbols)


def read_systemic_risk(db):
    """Read systemic risk score if available."""
    try:
        row = db.execute(
            "SELECT systemic_risk_score FROM market_risk_summary ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return float(row['systemic_risk_score'] or 0)
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Signal Collection
# ---------------------------------------------------------------------------

def collect_signals(db, symbol, regime_data):
    """
    Collect all signals for a symbol. Returns a dict with keys matching
    CONSTITUTION dimensions plus metadata.
    """
    liq        = read_liquidity_profile(db, symbol)
    intel      = read_intelligence_scores(db, symbol)
    failure    = read_failure_intelligence(db, symbol)
    explosion  = read_explosion_readiness(db, symbol)
    law_prec   = read_best_law_precision_for_symbol(db, symbol)
    anti_veto  = read_anti_law_veto(db, symbol)
    stat_grade = read_statistical_grade(db)
    systemic   = read_systemic_risk(db)

    tier = liq['tier']
    execution_feasible = tier in ('DEEP', 'MID')

    # ---- Normalize each signal to 0-100 scale ----

    # execution_feasibility
    tier_score_map = {
        'DEEP':     100,
        'MID':       80,
        'LIQUID':    65,
        'MODERATE':  45,
        'SHALLOW':   25,
        'THIN':      10,
        'ILLIQUID':   0,
    }
    exec_score = tier_score_map.get(tier, 0)

    # law_precision (raw 0-1 → 0-100, adjusted relative to RANDOM_BASELINE)
    if law_prec is not None:
        # Normalize so RANDOM_BASELINE maps to 0 and 1.0 maps to 100
        law_score = max(0.0, (law_prec - RANDOM_BASELINE) / (1.0 - RANDOM_BASELINE)) * 100
        law_score = min(100.0, law_score)
    else:
        law_score = 30.0  # uncertain

    # regime_alignment (from intelligence_scores.regime_component, 0-1 → 0-100)
    if intel:
        regime_score = min(100.0, float(intel['regime_component']) * 100)
    else:
        # fallback: if tier is tradeable, moderate alignment
        regime_score = 50.0 if execution_feasible else 20.0

    # causal_confidence (from intelligence_scores.causal_component)
    if intel:
        causal_score = min(100.0, float(intel['causal_component']) * 100)
    else:
        causal_score = 30.0

    # graph_health (proxy: execution_component from intelligence_scores)
    if intel:
        graph_score = min(100.0, float(intel['execution_component']) * 100)
    else:
        graph_score = exec_score * 0.7  # degrade slightly

    # failure_memory (inverted: high failure confidence → low score)
    if failure:
        failure_risk = float(failure['confidence'])
        fail_score = max(0.0, 100.0 - failure_risk * 100)
    else:
        fail_score = 70.0  # no known failure archetype → moderate safety

    # transition_probability (inverted: high prob → lower score for entering)
    trans_prob = regime_data['prob_10d']
    trans_score = max(0.0, 100.0 - trans_prob * 100)

    # explosion_proximity (bonus signal — high readiness is good)
    if explosion is not None:
        explosion_score = min(100.0, float(explosion))
    else:
        explosion_score = 50.0

    return {
        # feasibility metadata
        'execution_feasible':  execution_feasible,
        'tier':                tier,
        'anti_law_veto':       anti_veto,
        'statistical_grade':   stat_grade,
        'systemic_risk_score': systemic,
        'transition_probability_raw': trans_prob,

        # scored dimensions
        'execution_feasibility': {'score': exec_score,    'raw': f'{tier} tier'},
        'law_precision':         {'score': law_score,     'raw': f'precision {law_prec:.3f}' if law_prec else 'N/A'},
        'regime_alignment':      {'score': regime_score,  'raw': f'component {intel["regime_component"]:.3f}' if intel else 'proxy'},
        'causal_confidence':     {'score': causal_score,  'raw': f'component {intel["causal_component"]:.3f}' if intel else 'proxy'},
        'graph_health':          {'score': graph_score,   'raw': f'exec_component {intel["execution_component"]:.3f}' if intel else 'proxy'},
        'failure_memory':        {'score': fail_score,    'raw': f'archetype {failure["archetype"]} conf {failure["confidence"]:.2f}' if failure else 'no archetype'},
        'transition_probability':{'score': trans_score,   'raw': f'prob_10d {trans_prob:.2f}'},
        'explosion_proximity':   {'score': explosion_score, 'raw': f'readiness {explosion:.1f}' if explosion else 'N/A'},
    }


# ---------------------------------------------------------------------------
# VETO Engine
# ---------------------------------------------------------------------------

def check_vetos(signals, regime):
    """
    Apply absolute veto rules. Returns (veto_triggered, veto_reason).
    """
    # Rule 1: execution infeasible
    if not signals['execution_feasible']:
        return True, f"execution_infeasible: tier={signals['tier']} (requires DEEP or MID)"

    # Rule 2: transition probability > 0.70
    if signals['transition_probability_raw'] > 0.70:
        return True, f"transition_probability={signals['transition_probability_raw']:.2f} > 0.70 — market regime unstable"

    # Rule 3: anti-law veto from Phase 35
    if signals['anti_law_veto']:
        return True, "anti_law_veto=True (Phase 35 — reversal law active for this symbol)"

    # Rule 4: systemic risk > 80
    if signals['systemic_risk_score'] > 80:
        return True, f"systemic_risk_score={signals['systemic_risk_score']:.1f} > 80 — market-wide stress"

    # Rule 5: CRISIS regime → cash only
    if regime == 'CRISIS':
        return True, "regime=CRISIS — cash-only posture (all positions forbidden)"

    # Rule 6: statistical grade D or F from Phase 36
    grade = signals['statistical_grade']
    if grade in ('D', 'F'):
        return True, f"statistical_grade={grade} — law not statistically significant (Phase 36)"

    return False, None


# ---------------------------------------------------------------------------
# Weighted Score Engine
# ---------------------------------------------------------------------------

def compute_weighted_score(signals, regime, ewi):
    """
    Compute the weighted arbitration score (0-100).
    Returns (score, signal_breakdown, dominant_source, soft_warnings).
    """
    weights = CONSTITUTION.get(regime, CONSTITUTION['SIDEWAYS'])

    # CRISIS is handled by veto — if we get here with CRISIS use SIDEWAYS fallback
    if regime == 'CRISIS':
        weights = CONSTITUTION['SIDEWAYS']

    breakdown = {}
    weighted_contributions = {}

    for dim, weight in weights.items():
        if dim == 'cash_flag':
            # Pure cash — this should have been vetoed, but just in case
            breakdown[dim] = {'score': 0, 'weight': weight, 'raw': 'CRISIS cash flag'}
            weighted_contributions[dim] = 0.0
            continue

        dim_data = signals.get(dim)
        if dim_data is None:
            # Dimension not available — use neutral score
            score = 50.0
            raw   = 'N/A'
        else:
            score = float(dim_data['score'])
            raw   = dim_data['raw']

        breakdown[dim] = {'score': round(score, 1), 'weight': weight, 'raw': raw}
        weighted_contributions[dim] = score * weight

    # Apply EWI stress adjustment: if EWI > 60, reduce all scores by (EWI-60)/100
    ewi_penalty = 0.0
    if ewi > 60:
        ewi_penalty = (ewi - 60.0) / 100.0  # e.g. EWI=80 → reduce by 0.20

    raw_score = sum(weighted_contributions.values())
    adjusted_score = raw_score * (1.0 - ewi_penalty)
    adjusted_score = max(0.0, min(100.0, adjusted_score))

    # Dominant source
    dominant_source = max(weighted_contributions, key=lambda k: weighted_contributions[k]) if weighted_contributions else 'execution_feasibility'

    # Soft warnings
    soft_warnings = []

    # Graph health warning
    if 'graph_health' in signals and signals['graph_health']['score'] < 50:
        soft_warnings.append("graph fragility moderate — reduce size by 20%")

    # Transition risk warning
    trans_raw = signals.get('transition_probability_raw', 0.0)
    if 0.50 <= trans_raw <= 0.70:
        soft_warnings.append(f"transition_probability={trans_raw:.2f} elevated — consider reduced position")

    # EWI warning
    if ewi > 60:
        soft_warnings.append(f"EWI={ewi:.1f} elevated — stress penalty {ewi_penalty*100:.0f}% applied")

    # Failure archetype warning
    if 'failure_memory' in signals and signals['failure_memory']['score'] < 40:
        soft_warnings.append(f"failure memory active ({signals['failure_memory']['raw']}) — defensive sizing")

    # Low law precision warning
    if 'law_precision' in signals and signals['law_precision']['score'] < 40:
        soft_warnings.append("law precision below threshold — statistical edge uncertain")

    return adjusted_score, breakdown, dominant_source, soft_warnings


# ---------------------------------------------------------------------------
# Decision Mapping
# ---------------------------------------------------------------------------

def score_to_decision(score, veto_triggered, veto_reason, regime):
    """Map numerical score to ENTER | WAIT | AVOID decision."""
    if veto_triggered:
        return 'AVOID'
    if regime == 'CRISIS':
        return 'AVOID'
    if score >= 65.0:
        return 'ENTER'
    if score >= 35.0:
        return 'WAIT'
    return 'AVOID'


def compute_confidence(score, veto_triggered, signals):
    """
    Confidence = how certain we are about the decision.
    Based on score distance from thresholds + signal consistency.
    Returns 0-100 integer.
    """
    if veto_triggered:
        return 90  # high confidence we should avoid

    # Distance from nearest threshold
    if score >= 65:
        dist = score - 65
        base = 50 + dist * 0.8
    elif score >= 35:
        # In the WAIT zone — lower confidence
        dist_to_enter = 65 - score
        dist_to_avoid = score - 35
        min_dist = min(dist_to_enter, dist_to_avoid)
        base = 40 + min_dist * 0.5
    else:
        dist = 35 - score
        base = 50 + dist * 0.8

    # Signal consistency bonus (low std of scored dimensions)
    dim_scores = []
    for key in ('execution_feasibility', 'law_precision', 'regime_alignment', 'causal_confidence'):
        dim_data = signals.get(key)
        if dim_data:
            dim_scores.append(float(dim_data['score']))

    if len(dim_scores) >= 2:
        std = statistics.stdev(dim_scores) if len(dim_scores) > 1 else 0
        consistency_bonus = max(0, 10 - std / 5)
        base += consistency_bonus

    return int(min(99, max(1, base)))


def compute_suggested_size(score, regime, veto_triggered):
    """
    suggested_size_pct = min(15, score/100 * 20), capped by regime limit.
    Returns 0 if vetoed.
    """
    if veto_triggered:
        return 0.0
    base_size = min(15.0, (score / 100.0) * 20.0)
    regime_cap = REGIME_MAX_SIZE.get(regime, 0.0)
    return round(min(base_size, regime_cap), 1)


# ---------------------------------------------------------------------------
# Reasoning Builder
# ---------------------------------------------------------------------------

def build_reasoning(signals, breakdown, regime, ewi, score, decision, dominant_source):
    """Build a human-readable reasoning string."""
    parts = []

    # Lead with dominant signal
    if dominant_source in breakdown:
        dom = breakdown[dominant_source]
        parts.append(f"{dominant_source.replace('_', ' ')} {dom['score']:.0f}/100 (raw: {dom['raw']})")

    # Execution tier
    tier = signals.get('tier', 'UNKNOWN')
    parts.append(f"execution {tier}")

    # Regime
    parts.append(f"regime {regime}")

    # EWI
    if ewi > 60:
        parts.append(f"EWI stress {ewi:.1f}")
    else:
        parts.append(f"EWI clear {ewi:.1f}")

    # Score summary
    parts.append(f"final score {score:.1f} → {decision}")

    return ' × '.join(parts)


# ---------------------------------------------------------------------------
# Core: arbitrate_symbol
# ---------------------------------------------------------------------------

def arbitrate_symbol(symbol, db=None):
    """
    Full arbitration for one symbol. Returns a complete decision dict.
    """
    close_db = False
    if db is None:
        db = get_db()
        close_db = True

    try:
        # 1. Current regime
        regime_data = read_current_regime(db)
        regime      = regime_data['regime']
        ewi         = regime_data['ewi']
        trans_prob  = regime_data['prob_10d']

        # Validate regime
        valid_regimes = set(CONSTITUTION.keys())
        if regime not in valid_regimes:
            regime = 'SIDEWAYS'

        # 2. Collect all signals
        signals = collect_signals(db, symbol, regime_data)

        # 3. Apply VETO rules (absolute)
        veto_triggered, veto_reason = check_vetos(signals, regime)

        # 4. Compute weighted score
        score, signal_breakdown, dominant_source, soft_warnings = compute_weighted_score(
            signals, regime, ewi
        )

        # 5. Map to decision
        decision = score_to_decision(score, veto_triggered, veto_reason, regime)

        # 6. Blocking factors (hard negatives that don't reach veto threshold)
        blocking_factors = []
        if signals.get('transition_probability_raw', 0) > 0.50:
            blocking_factors.append(f"transition_risk: prob_10d={trans_prob:.2f}")
        if signals['execution_feasibility']['score'] < 50:
            blocking_factors.append(f"low_execution_score: tier={signals['tier']}")
        if signals.get('failure_memory', {}).get('score', 100) < 30:
            blocking_factors.append(f"active_failure_archetype: {signals['failure_memory']['raw']}")

        # 7. Confidence
        confidence = compute_confidence(score, veto_triggered, signals)

        # 8. Suggested size
        suggested_size = compute_suggested_size(score, regime, veto_triggered)

        # 9. Reasoning
        reasoning = build_reasoning(signals, signal_breakdown, regime, ewi, score, decision, dominant_source)
        if veto_triggered:
            reasoning = f"VETO: {veto_reason}"

        result = {
            'symbol':           symbol,
            'date':             TODAY,
            'decision':         decision,
            'confidence':       confidence,
            'score':            round(score, 2),
            'regime':           regime,
            'ewi':              round(ewi, 2),
            'reasoning':        reasoning,
            'veto_triggered':   veto_triggered,
            'veto_reason':      veto_reason,
            'blocking_factors': blocking_factors,
            'soft_warnings':    soft_warnings,
            'suggested_size_pct': suggested_size,
            'dominant_source':  dominant_source,
            'signal_breakdown': signal_breakdown,
            'overridden_signals': [],
        }

        # Persist to DB
        try:
            db.execute("""
                INSERT OR REPLACE INTO arbitration_decisions
                (symbol, date, decision, confidence, score, regime, ewi,
                 reasoning, veto_triggered, veto_reason, blocking_factors,
                 soft_warnings, suggested_size_pct, dominant_source,
                 signal_breakdown, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, TODAY, decision, confidence, round(score, 2),
                regime, round(ewi, 2), reasoning,
                1 if veto_triggered else 0, veto_reason,
                json.dumps(blocking_factors), json.dumps(soft_warnings),
                suggested_size, dominant_source,
                json.dumps(signal_breakdown), datetime.utcnow().isoformat()
            ))
            db.commit()
        except Exception:
            pass

        return result

    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# arbitrate_all
# ---------------------------------------------------------------------------

def arbitrate_all(db=None):
    """
    Arbitrate all symbols in universe. Returns ranked results.
    """
    close_db = False
    if db is None:
        db = get_db()
        close_db = True

    try:
        symbols = read_all_symbols(db)
        if not symbols:
            # Fallback: try arbitration_decisions for known symbols
            try:
                rows = db.execute("SELECT DISTINCT symbol FROM arbitration_decisions").fetchall()
                symbols = [r['symbol'] for r in rows]
            except Exception:
                symbols = []

        regime_data = read_current_regime(db)
        regime = regime_data['regime']
        ewi    = regime_data['ewi']

        decisions = []
        n_enter = n_wait = n_avoid = n_veto = 0

        for sym in symbols:
            try:
                result = arbitrate_symbol(sym, db=db)
                decisions.append({
                    'symbol':     result['symbol'],
                    'decision':   result['decision'],
                    'confidence': result['confidence'],
                    'score':      result['score'],
                    'regime':     result['regime'],
                    'ewi':        result['ewi'],
                    'reasoning':  result['reasoning'],
                    'suggested_size_pct': result['suggested_size_pct'],
                    'veto_triggered': result['veto_triggered'],
                    'veto_reason':    result['veto_reason'],
                })
                if result['decision'] == 'ENTER':
                    n_enter += 1
                elif result['decision'] == 'WAIT':
                    n_wait += 1
                else:
                    n_avoid += 1
                if result['veto_triggered']:
                    n_veto += 1
            except Exception:
                n_avoid += 1

        # Sort by confidence desc, then score desc
        decisions.sort(key=lambda x: (-x['confidence'], -x['score']))

        summary_line = (
            f"{n_enter} ENTER, {n_wait} WAIT, {n_avoid} AVOID across {len(symbols)} symbols "
            f"in {regime} regime (EWI={ewi:.1f})"
        )

        return {
            'n_enter':   n_enter,
            'n_wait':    n_wait,
            'n_avoid':   n_avoid,
            'n_veto':    n_veto,
            'n_total':   len(symbols),
            'decisions': decisions,
            'regime':    regime,
            'ewi':       round(ewi, 2),
            'summary':   summary_line,
            'date':      TODAY,
        }

    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# daily_decisions
# ---------------------------------------------------------------------------

def daily_decisions(db=None):
    """
    Top ENTER decisions for today, with portfolio-level filters.
    """
    close_db = False
    if db is None:
        db = get_db()
        close_db = True

    try:
        all_results = arbitrate_all(db=db)
        regime = all_results['regime']
        ewi    = all_results['ewi']

        # Filter to ENTER only
        enter_decisions = [d for d in all_results['decisions'] if d['decision'] == 'ENTER']

        # Apply portfolio-level constraint: max 10 positions
        MAX_POSITIONS = 10
        top_decisions = enter_decisions[:MAX_POSITIONS]

        # Market posture
        posture = determine_market_posture(regime, ewi)

        # Portfolio stats
        if top_decisions:
            avg_confidence = statistics.mean(d['confidence'] for d in top_decisions)
            avg_score      = statistics.mean(d['score'] for d in top_decisions)
            total_alloc    = sum(d['suggested_size_pct'] for d in top_decisions)
        else:
            avg_confidence = 0.0
            avg_score      = 0.0
            total_alloc    = 0.0

        portfolio_stats = {
            'avg_confidence': round(avg_confidence, 1),
            'avg_score':      round(avg_score, 2),
            'total_allocation_pct': round(total_alloc, 1),
            'n_positions':    len(top_decisions),
            'market_posture': posture,
        }

        # Persist summary
        try:
            db.execute("""
                INSERT OR REPLACE INTO daily_decision_summary
                (date, regime, ewi, n_enter, n_wait, n_avoid, n_veto,
                 market_posture, top_decisions, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                TODAY, regime, ewi,
                all_results['n_enter'], all_results['n_wait'],
                all_results['n_avoid'], all_results['n_veto'],
                posture, json.dumps(top_decisions),
                datetime.utcnow().isoformat()
            ))
            db.commit()
        except Exception:
            pass

        return {
            'date':           TODAY,
            'regime':         regime,
            'ewi':            ewi,
            'market_posture': posture,
            'n_enter':        all_results['n_enter'],
            'n_wait':         all_results['n_wait'],
            'n_avoid':        all_results['n_avoid'],
            'n_veto':         all_results['n_veto'],
            'top_decisions':  top_decisions,
            'portfolio_stats': portfolio_stats,
        }

    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# constitution_report
# ---------------------------------------------------------------------------

def constitution_report(db=None):
    """
    Explain the current constitution weights — which sources dominate and why.
    """
    close_db = False
    if db is None:
        db = get_db()
        close_db = True

    try:
        regime_data = read_current_regime(db)
        regime = regime_data['regime']
        ewi    = regime_data['ewi']
        trans  = regime_data['prob_10d']

        weights = CONSTITUTION.get(regime, CONSTITUTION['SIDEWAYS'])
        posture = determine_market_posture(regime, ewi)

        # Dominant philosophy per regime
        regime_philosophy = {
            'TRENDING_UP':   "Momentum + execution quality. Ride the trend with tight law backing.",
            'TRENDING_DOWN': "Capital preservation. Memory of past failures overrides opportunity bias.",
            'VOLATILE':      "Survival first. Failure avoidance and regime stability trump precision.",
            'SIDEWAYS':      "Pattern selectivity. Only laws with proven edge in range-bound markets.",
            'TRANSITION':    "Radar mode. Prioritize detecting the transition over seeking opportunity.",
            'CRISIS':        "Cash is the position. No equity exposure until crisis resolves.",
        }
        dominant_philosophy = regime_philosophy.get(regime, "Balanced multi-signal arbitration.")

        # Active veto rules
        veto_rules_active = [
            "execution_feasible == False → AVOID immediately",
            "transition_probability > 0.70 → WAIT",
            "anti_law_veto == True → AVOID (Phase 35)",
            "systemic_risk_score > 80 → AVOID",
            "regime == CRISIS → AVOID (cash only)",
            "statistical_grade in (D, F) → AVOID (Phase 36)",
        ]

        # EWI impact explanation
        ewi_explanation = "No EWI stress penalty."
        if ewi > 60:
            penalty_pct = (ewi - 60.0) / 100.0 * 100
            ewi_explanation = f"EWI={ewi:.1f} — score penalty {penalty_pct:.0f}% applied to all signals."

        # Constitution sorted by weight desc
        sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)

        # Which signals are dominant and why
        top_signals = []
        for dim, wt in sorted_weights[:3]:
            top_signals.append({
                'dimension': dim,
                'weight':    wt,
                'rationale': _dimension_rationale(dim, regime),
            })

        return {
            'date':                 TODAY,
            'regime':               regime,
            'ewi':                  round(ewi, 2),
            'transition_prob_10d':  round(trans, 3),
            'constitution_weights': dict(sorted_weights),
            'top_signals':          top_signals,
            'veto_rules_active':    veto_rules_active,
            'dominant_philosophy':  dominant_philosophy,
            'market_posture':       posture,
            'ewi_impact':           ewi_explanation,
            'posture_rationale':    _posture_rationale(posture, regime, ewi),
        }

    finally:
        if close_db:
            db.close()


def _dimension_rationale(dim, regime):
    """Return a brief rationale for why a dimension matters in this regime."""
    rationales = {
        'execution_feasibility': "Without liquidity, even the best signal cannot be acted upon. Gate #1.",
        'law_precision':         "Statistically proven patterns are the alpha source. Higher precision = higher edge.",
        'regime_alignment':      "Trading with the regime increases success probability geometrically.",
        'causal_confidence':     "Causal chains (not just correlation) reduce false signals significantly.",
        'graph_health':          "Network connectivity health determines whether price signals propagate cleanly.",
        'failure_memory':        "Known failure archetypes repeat. Memory prevents re-entering losing patterns.",
        'transition_probability':"High transition probability means we could enter the wrong regime mid-trade.",
        'cash_flag':             "CRISIS regime: all capital protected in cash. No signal overrides this.",
    }
    return rationales.get(dim, f"{dim} is a key dimension in {regime} regime.")


def _posture_rationale(posture, regime, ewi):
    """Return rationale for the current market posture."""
    if posture == 'CASH':
        return f"CRISIS regime detected — all capital in cash regardless of individual signals."
    if posture == 'DEFENSIVE':
        return f"Volatile/Transition regime (EWI={ewi:.1f}) — defensive posture, smaller sizes, higher bars to enter."
    if posture == 'AGGRESSIVE':
        return f"Trending Up with low EWI ({ewi:.1f}) — momentum phase, push position sizes within risk limits."
    return f"Default balanced posture in {regime} regime (EWI={ewi:.1f})."


# ---------------------------------------------------------------------------
# Market Posture Helper
# ---------------------------------------------------------------------------

def determine_market_posture(regime, ewi):
    """
    AGGRESSIVE: trending + low EWI
    DEFENSIVE:  volatile / transition
    CASH:       crisis / very high EWI
    BALANCED:   default
    """
    if regime == 'CRISIS' or ewi > EWI_HIGH:
        return 'CASH'
    if regime in ('VOLATILE', 'TRANSITION'):
        return 'DEFENSIVE'
    if regime == 'TRENDING_UP' and ewi < EWI_MEDIUM:
        return 'AGGRESSIVE'
    return 'BALANCED'


# ---------------------------------------------------------------------------
# build_full
# ---------------------------------------------------------------------------

def build_full(db=None):
    """
    Run arbitrate_all + daily_decisions + constitution_report.
    """
    close_db = False
    if db is None:
        db = get_db()
        close_db = True

    try:
        arb     = arbitrate_all(db=db)
        dec     = daily_decisions(db=db)
        const   = constitution_report(db=db)

        return {
            'arbitration': arb,
            'decisions':   dec,
            'constitution': const,
            'status':      'complete',
            'date':        TODAY,
            'computed_at': datetime.utcnow().isoformat(),
        }

    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# Command Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'arbitrate_symbol',
    'arbitrate_all',
    'daily_decisions',
    'constitution_report',
    'build_full',
}


def run(command, params):
    """
    Dispatch command with params dict. Returns a result dict.
    """
    if command == 'arbitrate_symbol':
        symbol = params.get('symbol', '').strip().upper()
        if not symbol:
            return {'success': False, 'error': 'symbol is required'}
        result = arbitrate_symbol(symbol)
        return {'success': True, **result}

    elif command == 'arbitrate_all':
        result = arbitrate_all()
        return {'success': True, **result}

    elif command == 'daily_decisions':
        result = daily_decisions()
        return {'success': True, **result}

    elif command == 'constitution_report':
        result = constitution_report()
        return {'success': True, **result}

    elif command == 'build_full':
        result = build_full()
        return {'success': True, **result}

    else:
        return {
            'success': False,
            'error':   f"Unknown command: {command}",
            'valid_commands': sorted(COMMANDS),
        }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({
            'success': False,
            'error':   'Usage: python cognitive_arbitration.py <command> [json_params]',
            'commands': sorted(COMMANDS),
        }))
        sys.exit(1)

    _command = sys.argv[1].strip()

    _params = {}
    if len(sys.argv) >= 3:
        try:
            _params = json.loads(sys.argv[2])
        except json.JSONDecodeError as _e:
            print(json.dumps({'success': False, 'error': f'Invalid JSON params: {_e}'}))
            sys.exit(1)

    try:
        _result = run(_command, _params)
        print(json.dumps(_result, default=str))
    except Exception as _exc:
        print(json.dumps({'success': False, 'error': str(_exc), 'command': _command}))
        sys.exit(1)
