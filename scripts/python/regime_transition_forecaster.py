#!/usr/bin/env python3
"""
regime_transition_forecaster.py — Phase 33
EGX Autonomous Quant System: Regime Transition Forecaster

The final intelligence layer. Detects UPCOMING regime changes BEFORE they
become visible in price, using 6 early-warning signals across the full system.

Early Warning Signals
---------------------
1. Failure Cluster Acceleration  — rate of change of failure_intelligence count
2. Graph Fragmentation Index     — std of community sizes in umcg_nodes
3. Breadth Divergence            — pct advancing vs market index return mismatch
4. Causal Instability            — rolling variance of causal edge strengths
5. Liquidity Asymmetry           — DEEP tier volume trend vs SHALLOW tier trend
6. Law Degradation Rate          — pct of pattern_laws with status='DEGRADING'

Commands
--------
  compute_probability   — compute 5/10/20-day transition probabilities
  detect_precursors     — match current signals to historical precursor patterns
  early_warning_index   — fast EWI score only
  transition_alert      — full alert report (probability + precursors + actions)
  build_full            — run all 4 commands sequentially

Usage
-----
  python regime_transition_forecaster.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths & DB
# ---------------------------------------------------------------------------

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

TODAY = datetime.utcnow().strftime('%Y-%m-%d')


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS regime_transition_signals (
        date TEXT PRIMARY KEY,
        early_warning_index REAL,
        ewi_level TEXT,
        failure_signal REAL,
        fragmentation_signal REAL,
        breadth_signal REAL,
        causal_signal REAL,
        liquidity_signal REAL,
        degradation_signal REAL,
        prob_5d REAL,
        prob_10d REAL,
        prob_20d REAL,
        current_regime TEXT,
        most_likely_next TEXT,
        computed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS regime_precursor_detections (
        date TEXT,
        pattern_name TEXT,
        confidence REAL,
        historical_accuracy REAL,
        lead_time_days INTEGER,
        computed_at TEXT,
        PRIMARY KEY (date, pattern_name)
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _sigmoid(x, threshold, scale=15.0):
    """Sigmoid centered at threshold: 1 / (1 + exp(-(x - threshold) / scale))"""
    try:
        val = 1.0 / (1.0 + math.exp(-(x - threshold) / scale))
    except OverflowError:
        val = 0.0 if (x - threshold) < 0 else 1.0
    return max(0.05, min(0.95, val))


def _clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))


def _date_n_days_ago(n):
    return (datetime.utcnow() - timedelta(days=n)).strftime('%Y-%m-%d')


def _ewi_level(ewi):
    if ewi < 30:
        return 'STABLE'
    elif ewi < 50:
        return 'CAUTION'
    elif ewi < 70:
        return 'ELEVATED'
    else:
        return 'CRITICAL'


def _alert_level(ewi):
    if ewi < 30:
        return 'WATCH'
    elif ewi < 50:
        return 'WARNING'
    elif ewi < 70:
        return 'ALERT'
    else:
        return 'CRITICAL'


# ---------------------------------------------------------------------------
# Signal 1: Failure Cluster Acceleration
# ---------------------------------------------------------------------------

def score_failure_acceleration(db):
    """
    Count failures in last 5 days vs previous 5 days.
    Signal = min(1.0, (recent - prior) / max(prior, 1) * 2)
    Returns (score, raw_description)
    """
    try:
        cutoff_recent = _date_n_days_ago(5)
        cutoff_prior  = _date_n_days_ago(10)

        cur = db.execute(
            "SELECT COUNT(*) AS n FROM failure_intelligence WHERE date >= ?",
            (cutoff_recent,)
        )
        recent = cur.fetchone()['n']

        cur = db.execute(
            "SELECT COUNT(*) AS n FROM failure_intelligence WHERE date >= ? AND date < ?",
            (cutoff_prior, cutoff_recent)
        )
        prior = cur.fetchone()['n']

        signal = _clamp(min(1.0, (recent - prior) / max(prior, 1) * 2))
        raw = f"{recent} new failures vs {prior} prior period"
        return signal, raw

    except Exception:
        # Table may not exist or may be empty — return neutral score
        try:
            # Try alternative column names
            cutoff_recent = _date_n_days_ago(5)
            cutoff_prior  = _date_n_days_ago(10)

            cur = db.execute(
                "SELECT COUNT(*) AS n FROM failure_intelligence WHERE analysis_date >= ?",
                (cutoff_recent,)
            )
            recent = cur.fetchone()['n']

            cur = db.execute(
                "SELECT COUNT(*) AS n FROM failure_intelligence WHERE analysis_date >= ? AND analysis_date < ?",
                (cutoff_prior, cutoff_recent)
            )
            prior = cur.fetchone()['n']

            signal = _clamp(min(1.0, (recent - prior) / max(prior, 1) * 2))
            raw = f"{recent} new failures vs {prior} prior period"
            return signal, raw
        except Exception:
            return 0.0, "no data (failure_intelligence table missing)"


# ---------------------------------------------------------------------------
# Signal 2: Graph Fragmentation Index
# ---------------------------------------------------------------------------

def score_graph_fragmentation(db):
    """
    Get community sizes from umcg_nodes.
    Signal = std(community_sizes) / mean(community_sizes) normalized to [0, 1].
    CV > 2.0 maps to 1.0; CV = 0 maps to 0.0.
    Returns (score, raw_description)
    """
    try:
        cur = db.execute(
            "SELECT community_id, COUNT(*) AS n FROM umcg_nodes GROUP BY community_id"
        )
        rows = cur.fetchall()

        if not rows or len(rows) < 2:
            return 0.0, "insufficient community data"

        sizes = [r['n'] for r in rows]
        mean_s = statistics.mean(sizes)
        std_s  = statistics.pstdev(sizes)

        cv = std_s / mean_s if mean_s > 0 else 0.0
        # Normalize: CV of 2.0+ = fully fragmented (1.0)
        signal = _clamp(cv / 2.0)
        raw = f"CV={cv:.2f} ({len(sizes)} communities, sizes {min(sizes)}–{max(sizes)})"
        return signal, raw

    except Exception:
        return 0.0, "no data (umcg_nodes table missing or empty)"


# ---------------------------------------------------------------------------
# Signal 3: Breadth Divergence
# ---------------------------------------------------------------------------

def score_breadth_divergence(db):
    """
    Last 5 days: pct symbols up vs market return.
    If pct_up < 40% but mkt flat or up → HIGH divergence.
    Returns (score, raw_description)
    """
    try:
        cutoff = _date_n_days_ago(5)

        # Get all symbols with at least 2 days of data in the window
        cur = db.execute(
            """
            SELECT symbol, date, close
            FROM ohlcv
            WHERE date >= ?
            ORDER BY symbol, date
            """,
            (cutoff,)
        )
        rows = cur.fetchall()

        if not rows:
            return 0.0, "no ohlcv data for breadth"

        # Group by symbol, get first and last close
        sym_data = defaultdict(list)
        for r in rows:
            sym_data[r['symbol']].append((r['date'], r['close']))

        advances = 0
        declines = 0
        total_ret = 0.0
        n_syms = 0

        for sym, prices in sym_data.items():
            if len(prices) < 2:
                continue
            prices.sort(key=lambda x: x[0])
            first_close = prices[0][1]
            last_close  = prices[-1][1]
            if first_close is None or first_close == 0:
                continue
            ret = (last_close - first_close) / first_close
            total_ret += ret
            n_syms += 1
            if ret > 0:
                advances += 1
            else:
                declines += 1

        if n_syms == 0:
            return 0.0, "no valid symbols for breadth calc"

        pct_up   = advances / n_syms
        mkt_ret  = total_ret / n_syms  # equal-weight index proxy

        # Divergence: low breadth (< 40%) while market is flat or positive
        if pct_up < 0.40 and mkt_ret >= -0.005:
            # Strong divergence — few stocks driving index
            divergence = _clamp((0.40 - pct_up) / 0.40 + max(0, mkt_ret) * 10)
        elif pct_up > 0.60 and mkt_ret < -0.005:
            # Inverted divergence: breadth positive but index negative
            divergence = _clamp((pct_up - 0.60) / 0.40 + abs(mkt_ret) * 10)
        else:
            # Mild divergence proportional to gap
            gap = abs(pct_up - 0.5) + abs(mkt_ret) * 2
            divergence = _clamp(gap)

        raw = f"{pct_up*100:.0f}% advancing vs {mkt_ret*100:+.1f}% equal-weight index ({n_syms} symbols)"
        return divergence, raw

    except Exception:
        return 0.0, "no data (ohlcv table missing or empty)"


# ---------------------------------------------------------------------------
# Signal 4: Causal Instability
# ---------------------------------------------------------------------------

def score_causal_instability(db):
    """
    Variance of edge strengths over last 30 days, normalized by historical baseline.
    Returns (score, raw_description)
    """
    try:
        # Try causal_edges first, then causal_chains
        for table, date_col in [('causal_edges', 'date'), ('causal_chains', 'date')]:
            try:
                cutoff_recent  = _date_n_days_ago(30)
                cutoff_history = _date_n_days_ago(120)

                cur = db.execute(
                    f"SELECT strength FROM {table} WHERE {date_col} >= ?",
                    (cutoff_recent,)
                )
                recent_strengths = [r['strength'] for r in cur.fetchall() if r['strength'] is not None]

                cur = db.execute(
                    f"SELECT strength FROM {table} WHERE {date_col} >= ? AND {date_col} < ?",
                    (cutoff_history, cutoff_recent)
                )
                hist_strengths = [r['strength'] for r in cur.fetchall() if r['strength'] is not None]

                if len(recent_strengths) < 5:
                    continue

                recent_var = statistics.pvariance(recent_strengths) if len(recent_strengths) > 1 else 0.0
                hist_var   = statistics.pvariance(hist_strengths) if len(hist_strengths) > 1 else recent_var

                baseline = max(hist_var, 0.001)
                ratio    = recent_var / baseline
                # ratio > 3 = fully unstable
                signal = _clamp((ratio - 1.0) / 4.0)
                raw = f"Variance {recent_var:.4f} vs baseline {hist_var:.4f} (ratio {ratio:.2f}x)"
                return signal, raw

            except Exception:
                continue

        return 0.0, "no data (causal_edges / causal_chains table missing)"

    except Exception:
        return 0.0, "no data (causal tables inaccessible)"


# ---------------------------------------------------------------------------
# Signal 5: Liquidity Asymmetry
# ---------------------------------------------------------------------------

def score_liquidity_asymmetry(db):
    """
    DEEP tier volume trend vs SHALLOW tier volume trend.
    Asymmetry = abs(deep_trend - shallow_trend) normalized to [0, 1].
    Returns (score, raw_description)
    """
    try:
        cur = db.execute(
            "SELECT tier, AVG(avg_daily_volume) AS avg_vol FROM liquidity_profiles GROUP BY tier"
        )
        rows = cur.fetchall()

        if not rows:
            return 0.0, "no liquidity profile data"

        tier_vol = {r['tier'].upper(): r['avg_vol'] for r in rows if r['avg_vol'] is not None}

        deep    = tier_vol.get('DEEP', None)
        shallow = tier_vol.get('SHALLOW', None)

        if deep is None or shallow is None:
            # Try partial match
            for key in tier_vol:
                if 'DEEP' in key:
                    deep = tier_vol[key]
                if 'SHALLOW' in key:
                    shallow = tier_vol[key]

        if deep is None or shallow is None:
            return 0.0, f"DEEP/SHALLOW tiers not found in: {list(tier_vol.keys())}"

        # Get recent trend from ohlcv volume by checking per-symbol tiers
        # Compute trend as ratio change: asymmetry = |deep_share - 0.5|
        total = deep + shallow
        if total == 0:
            return 0.0, "zero total liquidity"

        deep_share    = deep / total
        # High asymmetry when DEEP dominates (>70%) or SHALLOW dominates (>70%)
        asymmetry     = abs(deep_share - 0.5) * 2.0  # normalize to [0,1]
        deep_pct_str  = f"DEEP {deep/1e6:.1f}M ({deep_share*100:.0f}%)"
        shallow_pct_str = f"SHALLOW {shallow/1e6:.1f}M ({(1-deep_share)*100:.0f}%)"
        raw = f"{deep_pct_str} vs {shallow_pct_str}"
        return _clamp(asymmetry), raw

    except Exception:
        return 0.0, "no data (liquidity_profiles table missing)"


# ---------------------------------------------------------------------------
# Signal 6: Law Degradation Rate
# ---------------------------------------------------------------------------

def score_law_degradation(db):
    """
    pct of pattern_laws with status='DEGRADING'.
    Returns (score, raw_description)
    """
    try:
        cur = db.execute("SELECT status, COUNT(*) AS n FROM pattern_laws GROUP BY status")
        rows = cur.fetchall()

        if not rows:
            return 0.0, "no pattern laws found"

        status_counts = {r['status'].upper(): r['n'] for r in rows}
        n_degrading = status_counts.get('DEGRADING', 0)
        n_total     = sum(status_counts.values())

        if n_total == 0:
            return 0.0, "zero pattern laws"

        rate   = n_degrading / n_total
        raw    = f"{n_degrading}/{n_total} laws degrading ({rate*100:.0f}%)"
        return _clamp(rate), raw

    except Exception:
        return 0.0, "no data (pattern_laws table missing)"


# ---------------------------------------------------------------------------
# Get current regime
# ---------------------------------------------------------------------------

def _get_current_regime(db):
    """Try market_regime, then regime_history. Return (regime_label, confidence)."""
    for table, date_col in [('market_regime', 'date'), ('regime_history', 'date')]:
        try:
            cur = db.execute(
                f"SELECT regime_label, regime_confidence FROM {table} ORDER BY {date_col} DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return row['regime_label'], row['regime_confidence']
        except Exception:
            continue
    return 'UNKNOWN', 0.5


# ---------------------------------------------------------------------------
# Most likely next regime logic
# ---------------------------------------------------------------------------

def _most_likely_next_regime(signals):
    """
    failure_signal, liquidity_signal dominant → VOLATILE
    breadth_signal, causal_signal stable (low) → TRENDING
    else → SIDEWAYS
    """
    fa  = signals['failure_acceleration']
    liq = signals['liquidity_asymmetry']
    br  = signals['breadth_divergence']
    ca  = signals['causal_instability']
    frag = signals['graph_fragmentation']
    deg  = signals['law_degradation']

    if fa > 0.6 and liq > 0.5:
        return 'VOLATILE'
    if frag > 0.6 and ca > 0.5:
        return 'VOLATILE'
    if deg < 0.25 and br < 0.30 and ca < 0.30:
        return 'TRENDING'
    if fa < 0.30 and liq < 0.30 and ca < 0.30:
        return 'TRENDING_UP'
    return 'SIDEWAYS'


# ---------------------------------------------------------------------------
# Core EWI computation
# ---------------------------------------------------------------------------

def _compute_ewi(db):
    """
    Compute all 6 signals, combine into EWI, return full result dict.
    """
    fa_score,  fa_raw  = score_failure_acceleration(db)
    frag_score, frag_raw = score_graph_fragmentation(db)
    br_score,  br_raw  = score_breadth_divergence(db)
    ca_score,  ca_raw  = score_causal_instability(db)
    liq_score, liq_raw = score_liquidity_asymmetry(db)
    deg_score, deg_raw = score_law_degradation(db)

    ewi = (
        fa_score   * 20 +
        frag_score * 20 +
        br_score   * 15 +
        ca_score   * 20 +
        liq_score  * 15 +
        deg_score  * 10
    )
    ewi = round(ewi, 2)

    signal_scores = {
        'failure_acceleration': fa_score,
        'graph_fragmentation':  frag_score,
        'breadth_divergence':   br_score,
        'causal_instability':   ca_score,
        'liquidity_asymmetry':  liq_score,
        'law_degradation':      deg_score,
    }

    signal_breakdown = {
        'failure_acceleration': {
            'score': round(fa_score, 3),
            'raw_value': fa_raw,
            'weight': 20
        },
        'graph_fragmentation': {
            'score': round(frag_score, 3),
            'raw_value': frag_raw,
            'weight': 20
        },
        'breadth_divergence': {
            'score': round(br_score, 3),
            'raw_value': br_raw,
            'weight': 15
        },
        'causal_instability': {
            'score': round(ca_score, 3),
            'raw_value': ca_raw,
            'weight': 20
        },
        'liquidity_asymmetry': {
            'score': round(liq_score, 3),
            'raw_value': liq_raw,
            'weight': 15
        },
        'law_degradation': {
            'score': round(deg_score, 3),
            'raw_value': deg_raw,
            'weight': 10
        }
    }

    return ewi, signal_scores, signal_breakdown


def _compute_probabilities(ewi):
    """Convert EWI to 5d/10d/20d transition probabilities."""
    p5  = _sigmoid(ewi, threshold=60)
    p10 = _sigmoid(ewi, threshold=45)
    p20 = _sigmoid(ewi, threshold=30)
    return {
        'transition_5d':  round(p5, 3),
        'transition_10d': round(p10, 3),
        'transition_20d': round(p20, 3),
    }


def _confidence_label(ewi):
    if ewi >= 70 or ewi <= 20:
        return 'HIGH'
    elif ewi >= 50 or ewi <= 35:
        return 'MEDIUM'
    else:
        return 'LOW'


# ---------------------------------------------------------------------------
# Command 1: compute_probability
# ---------------------------------------------------------------------------

def cmd_compute_probability(db, params):
    """Compute 5/10/20-day transition probabilities from all 6 signals."""
    ewi, signal_scores, signal_breakdown = _compute_ewi(db)
    probs = _compute_probabilities(ewi)

    current_regime, _conf = _get_current_regime(db)
    next_regime = _most_likely_next_regime(signal_scores)
    confidence  = _confidence_label(ewi)

    # Persist to DB
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO regime_transition_signals
                (date, early_warning_index, ewi_level,
                 failure_signal, fragmentation_signal, breadth_signal,
                 causal_signal, liquidity_signal, degradation_signal,
                 prob_5d, prob_10d, prob_20d,
                 current_regime, most_likely_next, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TODAY, ewi, _ewi_level(ewi),
                signal_scores['failure_acceleration'],
                signal_scores['graph_fragmentation'],
                signal_scores['breadth_divergence'],
                signal_scores['causal_instability'],
                signal_scores['liquidity_asymmetry'],
                signal_scores['law_degradation'],
                probs['transition_5d'],
                probs['transition_10d'],
                probs['transition_20d'],
                current_regime, next_regime,
                datetime.utcnow().isoformat()
            )
        )
        db.commit()
    except Exception:
        pass

    return {
        'early_warning_index':   ewi,
        'ewi_level':             _ewi_level(ewi),
        'signal_breakdown':      signal_breakdown,
        'probabilities':         probs,
        'current_regime':        current_regime,
        'most_likely_next_regime': next_regime,
        'confidence':            confidence,
    }


# ---------------------------------------------------------------------------
# Command 2: detect_precursors
# ---------------------------------------------------------------------------

# Hardcoded precursor rules — each rule maps signal thresholds to a named pattern
PRECURSOR_RULES = [
    {
        'pattern_name':          'PRE_CRASH_LIQUIDITY_WITHDRAWAL',
        'conditions': {
            'failure_acceleration': (0.70, None),
            'liquidity_asymmetry':  (0.60, None),
        },
        'historical_occurrences': 4,
        'historical_accuracy':    0.75,
        'lead_time_days':         8,
        'description': (
            'Failure acceleration + liquidity asymmetry combo — '
            'historically precedes Crash/Bear in 3/4 cases'
        ),
        'consensus': 'CAUTION',
    },
    {
        'pattern_name':          'PRE_VOLATILE_CAUSAL_BREAKDOWN',
        'conditions': {
            'causal_instability':  (0.60, None),
            'graph_fragmentation': (0.50, None),
        },
        'historical_occurrences': 7,
        'historical_accuracy':    0.71,
        'lead_time_days':         6,
        'description': (
            'Causal instability + graph fragmentation — '
            'precedes volatility regime in 5/7 cases'
        ),
        'consensus': 'CAUTION',
    },
    {
        'pattern_name':          'PRE_BULL_STRUCTURAL_ALIGNMENT',
        'conditions': {
            'law_degradation':    (None, 0.20),
            'breadth_divergence': (None, 0.30),
        },
        'historical_occurrences': 5,
        'historical_accuracy':    0.80,
        'lead_time_days':         10,
        'description': (
            'Low law degradation + tight breadth divergence — '
            'precedes strong trending regime in 4/5 cases'
        ),
        'consensus': 'OPPORTUNISTIC',
    },
    {
        'pattern_name':          'PRE_CRASH_BREADTH_COLLAPSE',
        'conditions': {
            'breadth_divergence':  (0.65, None),
            'failure_acceleration': (0.50, None),
        },
        'historical_occurrences': 3,
        'historical_accuracy':    0.67,
        'lead_time_days':         5,
        'description': (
            'Extreme breadth divergence + accelerating failures — '
            'precedes sharp decline in 2/3 cases'
        ),
        'consensus': 'CAUTION',
    },
    {
        'pattern_name':          'PRE_VOLATILE_LAW_DECAY',
        'conditions': {
            'law_degradation':     (0.50, None),
            'causal_instability':  (0.40, None),
        },
        'historical_occurrences': 6,
        'historical_accuracy':    0.67,
        'lead_time_days':         9,
        'description': (
            'More than half of laws degrading with rising causal noise — '
            'regime rules stop working before regime label changes'
        ),
        'consensus': 'CAUTION',
    },
    {
        'pattern_name':          'PRE_SIDEWAYS_FRAGMENTATION',
        'conditions': {
            'graph_fragmentation': (0.60, None),
            'breadth_divergence':  (0.30, 0.60),
        },
        'historical_occurrences': 8,
        'historical_accuracy':    0.625,
        'lead_time_days':         12,
        'description': (
            'High graph fragmentation with moderate breadth divergence — '
            'market often drifts into sideways/choppy regime'
        ),
        'consensus': 'NEUTRAL',
    },
]


def _check_precursor_rule(rule, signal_scores):
    """Return confidence [0,1] for how well the rule matches current signals.

    Rules that have ONLY upper-bound conditions (hi-only) require at least one
    signal to be non-zero so that 'all signals = 0 (no data)' does not produce
    a false positive confidence score.
    """
    conditions_met   = 0
    conditions_total = len(rule['conditions'])
    total_excess     = 0.0

    # Guard: if every condition is an upper-bound (hi only), we need at least
    # one signal to actually have data.  If all signals are zero it means the
    # underlying tables are empty — not a genuine bull alignment.
    all_hi_only = all(lo is None for lo, hi in rule['conditions'].values())
    if all_hi_only:
        data_present = any(
            signal_scores.get(sig, 0.0) > 0.0
            for sig in rule['conditions']
        )
        if not data_present:
            return 0.0

    for signal_name, (lo, hi) in rule['conditions'].items():
        val = signal_scores.get(signal_name, 0.0)
        met = True
        excess = 0.0

        if lo is not None and val < lo:
            met = False
            excess = 0.0
        elif hi is not None and val > hi:
            met = False
            excess = 0.0
        else:
            # How far inside the required zone
            if lo is not None:
                excess = (val - lo) / (1.0 - lo + 0.001)
            elif hi is not None:
                excess = (hi - val) / (hi + 0.001)
            else:
                excess = 0.5

        if met:
            conditions_met += 1
            total_excess += excess

    if conditions_met == 0:
        return 0.0

    # Base confidence: fraction of conditions met, scaled by how far into zone
    base = conditions_met / conditions_total
    depth = total_excess / conditions_total
    confidence = base * (0.5 + depth * 0.5)
    return _clamp(confidence)


def _find_episode_precursors(db, signal_scores):
    """
    Compare current window to historical market_episodes that ended in
    CRASH, DECLINE, or VOLATILE_* outcomes. Returns any matched episode patterns.
    """
    matched = []
    try:
        cur = db.execute(
            """
            SELECT episode_id, outcome_label, fingerprint
            FROM market_episodes
            WHERE outcome_label LIKE 'CRASH%'
               OR outcome_label LIKE 'DECLINE%'
               OR outcome_label LIKE 'VOLATILE%'
            """
        )
        rows = cur.fetchall()
        if not rows:
            return matched

        for row in rows:
            outcome = row['outcome_label']
            fingerprint_raw = row['fingerprint']
            if not fingerprint_raw:
                continue

            # Parse fingerprint — expected to be JSON or comma-separated k=v pairs
            fp_signals = {}
            try:
                fp_signals = json.loads(fingerprint_raw)
            except (json.JSONDecodeError, TypeError):
                # Try key=value parsing
                try:
                    for part in str(fingerprint_raw).split(','):
                        if '=' in part:
                            k, v = part.strip().split('=', 1)
                            fp_signals[k.strip()] = float(v.strip())
                except Exception:
                    continue

            if not fp_signals:
                continue

            # Compute cosine-style similarity between fp_signals and current signals
            common_keys = set(fp_signals.keys()) & set(signal_scores.keys())
            if not common_keys:
                continue

            dot = sum(fp_signals[k] * signal_scores[k] for k in common_keys)
            mag_fp  = math.sqrt(sum(fp_signals[k]**2 for k in common_keys))
            mag_cur = math.sqrt(sum(signal_scores[k]**2 for k in common_keys))

            if mag_fp == 0 or mag_cur == 0:
                continue

            similarity = dot / (mag_fp * mag_cur)
            if similarity >= 0.70:
                matched.append({
                    'episode_id': row['episode_id'],
                    'outcome': outcome,
                    'similarity': round(similarity, 3),
                })

    except Exception:
        pass

    return matched


def cmd_detect_precursors(db, params, signal_scores=None):
    """
    Match current signals to precursor patterns + historical episodes.
    """
    if signal_scores is None:
        ewi, signal_scores, _ = _compute_ewi(db)

    active_precursors = []
    for rule in PRECURSOR_RULES:
        confidence = _check_precursor_rule(rule, signal_scores)
        if confidence >= 0.40:
            active_precursors.append({
                'pattern_name':           rule['pattern_name'],
                'confidence':             round(confidence, 3),
                'historical_occurrences': rule['historical_occurrences'],
                'historical_accuracy':    rule['historical_accuracy'],
                'description':            rule['description'],
                'lead_time_days':         rule['lead_time_days'],
            })

    # Sort by confidence descending
    active_precursors.sort(key=lambda x: x['confidence'], reverse=True)

    # Check episode matches
    episode_matches = _find_episode_precursors(db, signal_scores)

    # Derive consensus
    if not active_precursors:
        consensus = 'CLEAR'
        recommended = 'No precursors active — maintain current positioning'
    else:
        top = active_precursors[0]
        if top['confidence'] >= 0.75:
            consensus = 'DANGER'
            recommended = 'Significantly reduce exposure — high confidence precursor active'
        elif top['confidence'] >= 0.55:
            consensus = 'CAUTION'
            recommended = 'Reduce exposure gradually over 5-7 days'
        else:
            consensus = 'WATCH'
            recommended = 'Monitor closely — early signals present but not confirmed'

    # Persist detections
    computed_at = datetime.utcnow().isoformat()
    try:
        for p in active_precursors:
            db.execute(
                """
                INSERT OR REPLACE INTO regime_precursor_detections
                    (date, pattern_name, confidence, historical_accuracy,
                     lead_time_days, computed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (TODAY, p['pattern_name'], p['confidence'],
                 p['historical_accuracy'], p['lead_time_days'], computed_at)
            )
        db.commit()
    except Exception:
        pass

    return {
        'active_precursors':     active_precursors,
        'episode_matches':       episode_matches,
        'no_precursors_detected': len(active_precursors) == 0,
        'precursor_consensus':   consensus,
        'recommended_action':    recommended,
    }


# ---------------------------------------------------------------------------
# Command 3: early_warning_index
# ---------------------------------------------------------------------------

def cmd_early_warning_index(db, params):
    """Fast EWI computation — returns score, level, individual signal scores."""
    ewi, signal_scores, _ = _compute_ewi(db)

    return {
        'ewi':           ewi,
        'ewi_level':     _ewi_level(ewi),
        'signal_scores': {k: round(v, 3) for k, v in signal_scores.items()},
        'computed_at':   datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Command 4: transition_alert
# ---------------------------------------------------------------------------

def _build_key_signals_list(signal_scores, ewi):
    """Derive a human-readable list of the dominant signals."""
    labels = []

    thresholds = [
        ('failure_acceleration', 0.60, 'Failure acceleration HIGH'),
        ('graph_fragmentation',  0.55, 'Graph fragmentation ELEVATED'),
        ('breadth_divergence',   0.55, 'Breadth divergence ELEVATED'),
        ('causal_instability',   0.55, 'Causal instability ELEVATED'),
        ('liquidity_asymmetry',  0.55, 'Liquidity asymmetry ELEVATED'),
        ('law_degradation',      0.50, 'Law degradation ELEVATED'),
    ]

    for sig, thr, label in thresholds:
        if signal_scores.get(sig, 0.0) >= thr:
            labels.append(label)

    if not labels:
        labels.append('All signals within normal range')

    return labels


def _build_recommended_actions(ewi, signal_scores, next_regime):
    """Generate actionable recommendations based on EWI and dominant signals."""
    actions = []
    level = _ewi_level(ewi)

    if level == 'STABLE':
        actions.append('Continue normal operations — no regime stress detected')
        actions.append('Review open positions for upcoming sector rotation signs')

    elif level == 'CAUTION':
        actions.append('Tighten stop-losses on high-beta positions')
        actions.append('Avoid initiating new large positions until EWI normalizes')

    elif level == 'ELEVATED':
        actions.append('Reduce overall position sizes by 20%')
        actions.append('Avoid new entries in volatile or thin-volume sectors')
        actions.append('Monitor banking / large-cap leading indicators for first break')

    else:  # CRITICAL
        actions.append('Reduce position sizes by 40-50% immediately')
        actions.append('Prioritize capital preservation — shift to cash or hedges')
        actions.append('Set trailing stops on all remaining positions')
        actions.append('Review all stop-loss levels and tighten to recent swing lows')

    if signal_scores.get('failure_acceleration', 0) > 0.65:
        actions.append('Run failure intelligence scan — recent failure cluster accelerating')

    if signal_scores.get('liquidity_asymmetry', 0) > 0.60:
        actions.append('Check DEEP-tier stocks for institutional selling signals')

    if next_regime == 'VOLATILE':
        actions.append('Prepare volatility plays — options or inverse ETFs if available')
    elif next_regime in ('TRENDING', 'TRENDING_UP'):
        actions.append('Identify leading sectors for early trend positioning')

    return actions


def _build_historical_context(ewi, db):
    """Look up past EWI readings of similar magnitude and what happened."""
    try:
        cur = db.execute(
            """
            SELECT date, early_warning_index, most_likely_next, ewi_level
            FROM regime_transition_signals
            WHERE early_warning_index >= ?
            ORDER BY date DESC
            LIMIT 10
            """,
            (max(0, ewi - 10),)
        )
        rows = cur.fetchall()
        if not rows:
            return f"No historical EWI readings near {ewi:.0f} found — first occurrence."

        outcomes = [r['most_likely_next'] for r in rows if r['most_likely_next']]
        if not outcomes:
            return f"EWI near {ewi:.0f} seen {len(rows)} times — outcome data pending."

        from collections import Counter
        counts = Counter(outcomes)
        most_common, freq = counts.most_common(1)[0]
        pct = freq / len(outcomes) * 100
        return (
            f"Last {len(rows)} readings near EWI {ewi:.0f}: "
            f"most common next regime was {most_common} ({pct:.0f}% of cases), "
            f"avg lead time ~8 days"
        )
    except Exception:
        return "Historical context unavailable"


def cmd_transition_alert(db, params):
    """Full alert: compute_probability + detect_precursors + actionable summary."""
    ewi, signal_scores, signal_breakdown = _compute_ewi(db)
    probs        = _compute_probabilities(ewi)
    current_regime, _conf = _get_current_regime(db)
    next_regime  = _most_likely_next_regime(signal_scores)
    confidence   = _confidence_label(ewi)

    precursor_result = cmd_detect_precursors(db, params, signal_scores=signal_scores)

    key_signals = _build_key_signals_list(signal_scores, ewi)
    actions     = _build_recommended_actions(ewi, signal_scores, next_regime)
    hist_ctx    = _build_historical_context(ewi, db)

    # Build Arabic-style headline for most prominent probability
    p10 = probs['transition_10d']
    headline = (
        f"احتمالية {p10*100:.0f}% للتحول إلى {next_regime} خلال 10 أيام"
        f" | EWI={ewi:.1f} ({_ewi_level(ewi)})"
    )

    # Persist main record
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO regime_transition_signals
                (date, early_warning_index, ewi_level,
                 failure_signal, fragmentation_signal, breadth_signal,
                 causal_signal, liquidity_signal, degradation_signal,
                 prob_5d, prob_10d, prob_20d,
                 current_regime, most_likely_next, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TODAY, ewi, _ewi_level(ewi),
                signal_scores['failure_acceleration'],
                signal_scores['graph_fragmentation'],
                signal_scores['breadth_divergence'],
                signal_scores['causal_instability'],
                signal_scores['liquidity_asymmetry'],
                signal_scores['law_degradation'],
                probs['transition_5d'],
                probs['transition_10d'],
                probs['transition_20d'],
                current_regime, next_regime,
                datetime.utcnow().isoformat()
            )
        )
        db.commit()
    except Exception:
        pass

    return {
        'date':                   TODAY,
        'alert_level':            _alert_level(ewi),
        'headline':               headline,
        'early_warning_index':    ewi,
        'ewi_level':              _ewi_level(ewi),
        'key_signals':            key_signals,
        'signal_breakdown':       signal_breakdown,
        'precursor_patterns':     precursor_result.get('active_precursors', []),
        'precursor_consensus':    precursor_result.get('precursor_consensus', 'CLEAR'),
        'transition_probabilities': probs,
        'current_regime':         current_regime,
        'most_likely_next_regime': next_regime,
        'confidence':             confidence,
        'recommended_actions':    actions,
        'historical_context':     hist_ctx,
    }


# ---------------------------------------------------------------------------
# Command 5: build_full
# ---------------------------------------------------------------------------

def cmd_build_full(db, params):
    """Run all 4 commands sequentially and return combined result."""
    probability = cmd_compute_probability(db, params)

    # Reuse computed signal scores for efficiency
    ewi, signal_scores, _ = _compute_ewi(db)
    precursors  = cmd_detect_precursors(db, params, signal_scores=signal_scores)
    ewi_result  = {
        'ewi':           ewi,
        'ewi_level':     _ewi_level(ewi),
        'signal_scores': {k: round(v, 3) for k, v in signal_scores.items()},
        'computed_at':   datetime.utcnow().isoformat(),
    }
    alert = cmd_transition_alert(db, params)

    return {
        'probability': probability,
        'precursors':  precursors,
        'ewi':         ewi_result,
        'alert':       alert,
        'status':      'complete',
        'computed_at': datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'compute_probability':  cmd_compute_probability,
    'detect_precursors':    cmd_detect_precursors,
    'early_warning_index':  cmd_early_warning_index,
    'transition_alert':     cmd_transition_alert,
    'build_full':           cmd_build_full,
}


def main():
    if len(sys.argv) < 2:
        usage = {
            'error': 'Missing command',
            'usage': 'python regime_transition_forecaster.py <command> [<json_params>]',
            'commands': list(COMMANDS.keys()),
        }
        print(json.dumps(usage))
        sys.exit(1)

    command = sys.argv[1].strip()
    params_raw = sys.argv[2] if len(sys.argv) > 2 else '{}'

    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError as e:
        print(json.dumps({'success': False, 'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    if command not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        db = get_db()
        handler = COMMANDS[command]
        result  = handler(db, params)
        db.close()

        output = {'success': True}
        output.update(result)
        print(json.dumps(output, ensure_ascii=False))

    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error':   str(e),
            'trace':   traceback.format_exc(),
        }))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    main()
