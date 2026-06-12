"""
regime_transition.py — Regime Transition Prediction Engine

Predicts regime transitions BEFORE they happen using:
  - Empirical transition probability matrices
  - Leading indicator analysis (what precedes each transition type)
  - Real-time early-warning scoring
  - Multi-step probabilistic forecasting
  - Volatility acceleration detection

Usage:
  python regime_transition.py transition_matrix
  python regime_transition.py leading_indicators
  python regime_transition.py early_warning
  python regime_transition.py forecast
  python regime_transition.py volatility_acceleration
  python regime_transition.py report
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_PATH          = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
REGIME_HMM_PATH  = Path(__file__).parent / 'models' / 'ohlcv_regime_hmm.json'
HMM_PATH         = Path(__file__).parent / 'models' / 'hmm_regime.json'

REGIMES = ['TRENDING_UP', 'TRENDING_DOWN', 'HIGH_VOLATILITY', 'CHOPPY']

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _safe(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_db():
    """Open DB with Row factory."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f'DB not found: {DB_PATH}')
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _load_regime_map():
    """Load ohlcv_regime_hmm.json → sorted list of (date_str, regime)."""
    if not REGIME_HMM_PATH.exists():
        raise FileNotFoundError(f'Regime map not found: {REGIME_HMM_PATH}')
    with open(REGIME_HMM_PATH) as f:
        data = json.load(f)
    regimes_dict = data.get('regimes', {})
    sorted_seq = sorted(regimes_dict.items())  # [(date_str, regime), ...]
    return sorted_seq


def _cosine_similarity(a, b):
    """Cosine similarity between two lists/arrays."""
    if not HAS_NUMPY:
        dot   = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x**2 for x in a) ** 0.5
        norm_b = sum(x**2 for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _rolling_std(vals):
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def _rolling_mean(vals):
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# Regime run-length extraction
# ---------------------------------------------------------------------------

def _build_regime_runs(seq):
    """
    Convert raw (date, regime) sequence → list of regime runs.
    Each run: {'regime': str, 'start': date_str, 'end': date_str, 'days': int}
    Also returns list of transition dicts:
      {'from': r1, 'to': r2, 'transition_date': date_str, 'run_days': int}
    """
    if not seq:
        return [], []

    runs = []
    transitions = []
    cur_regime = seq[0][1]
    cur_start  = seq[0][0]
    run_count  = 1

    for i in range(1, len(seq)):
        date_str, regime = seq[i]
        if regime == cur_regime:
            run_count += 1
        else:
            run = {'regime': cur_regime, 'start': cur_start,
                   'end': seq[i - 1][0], 'days': run_count}
            runs.append(run)
            transitions.append({
                'from': cur_regime,
                'to': regime,
                'transition_date': date_str,
                'run_days': run_count,
            })
            cur_regime = regime
            cur_start  = date_str
            run_count  = 1

    # Last run
    runs.append({'regime': cur_regime, 'start': cur_start,
                 'end': seq[-1][0], 'days': run_count})
    return runs, transitions


# ---------------------------------------------------------------------------
# cmd_transition_matrix
# ---------------------------------------------------------------------------

def cmd_transition_matrix(params):
    """Build empirical 4×4 transition probability matrix from historical regimes."""
    try:
        seq = _load_regime_map()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    runs, transitions = _build_regime_runs(seq)

    # Count transitions: counts[from_r][to_r]
    counts = {r: {r2: 0 for r2 in REGIMES} for r in REGIMES}
    for t in transitions:
        fr = t['from']
        to = t['to']
        if fr in counts and to in REGIMES:
            counts[fr][to] += 1

    # Normalise rows → probabilities
    matrix = {}
    most_likely = {}
    for r in REGIMES:
        row_total = sum(counts[r].values())
        if row_total == 0:
            matrix[r] = {r2: 0.0 for r2 in REGIMES}
        else:
            matrix[r] = {r2: round(counts[r][r2] / row_total, 4) for r2 in REGIMES}
        # Most likely NEXT regime (excluding self)
        others = {r2: matrix[r][r2] for r2 in REGIMES if r2 != r}
        if others:
            best = max(others, key=lambda k: others[k])
            most_likely[r] = {'regime': best, 'probability': matrix[r][best]}
        else:
            most_likely[r] = {'regime': None, 'probability': 0.0}

    # Average holding days per regime
    holding = {r: [] for r in REGIMES}
    for run in runs:
        if run['regime'] in holding:
            holding[run['regime']].append(run['days'])
    avg_holding = {}
    for r in REGIMES:
        vals = holding[r]
        avg_holding[r] = {
            'avg_days': round(_rolling_mean(vals), 1),
            'min_days': min(vals) if vals else 0,
            'max_days': max(vals) if vals else 0,
            'n_episodes': len(vals),
        }

    return {
        'success': True,
        'n_transitions': len(transitions),
        'n_dates': len(seq),
        'transition_matrix': matrix,
        'most_likely_next': most_likely,
        'avg_holding_days': avg_holding,
        'raw_counts': {r: dict(counts[r]) for r in REGIMES},
    }


# ---------------------------------------------------------------------------
# cmd_leading_indicators
# ---------------------------------------------------------------------------

def _get_market_stats_before(conn, date_str, lookback=10):
    """
    Load market stats for the `lookback` trading days BEFORE date_str.
    Returns a dict with lists of values keyed by metric name.
    """
    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS d,
            AVG((close - open) / NULLIF(open, 0) * 100.0) AS median_ret,
            AVG(ABS((close - open) / NULLIF(open, 0) * 100.0)) AS avg_abs_ret,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS adv_ratio,
            SUM(volume) AS total_vol
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') < ?
          AND date(bar_time,'unixepoch') >= date(?, ? || ' days')
        GROUP BY d
        ORDER BY d
    """, (date_str, date_str, f'-{lookback}')).fetchall()

    if not rows:
        return None

    rets     = [_safe(r['median_ret']) for r in rows]
    abs_rets = [_safe(r['avg_abs_ret']) for r in rows]
    adv      = [_safe(r['adv_ratio']) for r in rows]
    vols     = [_safe(r['total_vol']) for r in rows]

    # Compute 20d baseline volatility (one more lookback)
    baseline_rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS d,
            AVG(ABS((close - open) / NULLIF(open, 0) * 100.0)) AS avg_abs_ret
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') < date(?, ? || ' days')
          AND date(bar_time,'unixepoch') >= date(?, '-30 days')
        GROUP BY d
        ORDER BY d
    """, (date_str, f'-{lookback}', date_str)).fetchall()
    baseline_vols = [_safe(r['avg_abs_ret']) for r in baseline_rows]
    baseline_mean = _rolling_mean(baseline_vols) if baseline_vols else 1.0
    current_mean  = _rolling_mean(abs_rets) if abs_rets else 0.0
    vol_ratio = current_mean / max(baseline_mean, 1e-6)

    # Breadth from market_breadth_daily if available
    breadth_rows = conn.execute("""
        SELECT breadth_score, ad_ratio
        FROM market_breadth_daily
        WHERE date < ?
          AND date >= date(?, ? || ' days')
        ORDER BY date
    """, (date_str, date_str, f'-{lookback}')).fetchall()

    breadth_scores = [_safe(r['breadth_score']) for r in breadth_rows]
    ad_ratios      = [_safe(r['ad_ratio']) for r in breadth_rows]

    return {
        'median_ret':     _rolling_mean(rets),
        'ret_std':        _rolling_std(rets),
        'avg_abs_ret':    current_mean,
        'vol_ratio':      vol_ratio,
        'adv_ratio':      _rolling_mean(adv),
        'breadth_score':  _rolling_mean(breadth_scores) if breadth_scores else None,
        'ad_ratio':       _rolling_mean(ad_ratios) if ad_ratios else None,
        'n_days':         len(rows),
    }


def cmd_leading_indicators(params):
    """
    Identify what PRECEDES each regime transition (1-10 days before).
    Returns average leading indicator values per transition type.
    """
    lookback = int(params.get('lookback', 5))

    try:
        seq = _load_regime_map()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    try:
        conn = get_db()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    _, transitions = _build_regime_runs(seq)

    # Group by transition type
    transition_stats = {}
    skipped = 0

    for t in transitions:
        key = f"{t['from']}→{t['to']}"
        stats = _get_market_stats_before(conn, t['transition_date'], lookback=lookback)
        if stats is None:
            skipped += 1
            continue
        if key not in transition_stats:
            transition_stats[key] = {
                'count': 0,
                'median_ret':    [],
                'ret_std':       [],
                'avg_abs_ret':   [],
                'vol_ratio':     [],
                'adv_ratio':     [],
                'breadth_score': [],
                'ad_ratio':      [],
                'run_days':      [],
            }
        ts = transition_stats[key]
        ts['count'] += 1
        ts['median_ret'].append(stats['median_ret'])
        ts['ret_std'].append(stats['ret_std'])
        ts['avg_abs_ret'].append(stats['avg_abs_ret'])
        ts['vol_ratio'].append(stats['vol_ratio'])
        ts['adv_ratio'].append(stats['adv_ratio'])
        if stats['breadth_score'] is not None:
            ts['breadth_score'].append(stats['breadth_score'])
        if stats['ad_ratio'] is not None:
            ts['ad_ratio'].append(stats['ad_ratio'])
        ts['run_days'].append(t['run_days'])

    conn.close()

    # Summarise averages
    summary = {}
    for key, ts in transition_stats.items():
        def avg(lst): return round(_rolling_mean(lst), 4) if lst else None
        summary[key] = {
            'count':         ts['count'],
            'avg_prior_median_ret':  avg(ts['median_ret']),
            'avg_prior_ret_std':     avg(ts['ret_std']),
            'avg_prior_abs_ret':     avg(ts['avg_abs_ret']),
            'avg_prior_vol_ratio':   avg(ts['vol_ratio']),
            'avg_prior_adv_ratio':   avg(ts['adv_ratio']),
            'avg_prior_breadth':     avg(ts['breadth_score']),
            'avg_prior_ad_ratio':    avg(ts['ad_ratio']),
            'avg_run_days_before':   avg(ts['run_days']),
            # Feature vector for cosine similarity (ordered, stable)
            '_feature_vector': [
                avg(ts['median_ret']) or 0.0,
                avg(ts['ret_std']) or 0.0,
                avg(ts['avg_abs_ret']) or 0.0,
                avg(ts['vol_ratio']) or 0.0,
                avg(ts['adv_ratio']) or 0.0,
                avg(ts['breadth_score']) or 0.0,
            ],
        }

    return {
        'success':            True,
        'n_transitions_analyzed': sum(ts['count'] for ts in transition_stats.values()),
        'n_skipped':          skipped,
        'lookback_days':      lookback,
        'transition_precursors': summary,
    }


# ---------------------------------------------------------------------------
# Current regime detection (mirrors regime_specific_ml.py)
# ---------------------------------------------------------------------------

def _detect_current_regime(conn):
    """Detect current regime from the most recent 20 trading days in DB."""
    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS bar_date,
            AVG((close - open) / NULLIF(open, 0) * 100) AS median_ret,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS adv_ratio
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') IN (
            SELECT DISTINCT date(bar_time,'unixepoch')
            FROM ohlcv_history_execution
            ORDER BY date(bar_time,'unixepoch') DESC
            LIMIT 30
        )
        GROUP BY bar_date
        ORDER BY bar_date DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        return 'UNKNOWN'

    rets = [_safe(r['median_ret']) for r in rows]
    adv  = _safe(rows[0]['adv_ratio'])
    roll_mean = _rolling_mean(rets)
    roll_std  = _rolling_std(rets)

    if roll_std > 2.5:
        return 'HIGH_VOLATILITY'
    elif roll_mean > 0.35 and adv > 0.55:
        return 'TRENDING_UP'
    elif roll_mean < -0.35 and adv < 0.45:
        return 'TRENDING_DOWN'
    else:
        return 'CHOPPY'


def _get_current_stats(conn, lookback=5):
    """
    Get current market stats vector using the most recent `lookback` trading days in DB.
    Uses latest available data regardless of wall-clock date.
    """
    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS d,
            AVG((close - open) / NULLIF(open, 0) * 100.0) AS median_ret,
            AVG(ABS((close - open) / NULLIF(open, 0) * 100.0)) AS avg_abs_ret,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS adv_ratio,
            SUM(volume) AS total_vol
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') IN (
            SELECT DISTINCT date(bar_time,'unixepoch')
            FROM ohlcv_history_execution
            ORDER BY date(bar_time,'unixepoch') DESC
            LIMIT ?
        )
        GROUP BY d
        ORDER BY d DESC
    """, (lookback,)).fetchall()

    if not rows:
        return None

    rets     = [_safe(r['median_ret']) for r in rows]
    abs_rets = [_safe(r['avg_abs_ret']) for r in rows]
    adv      = [_safe(r['adv_ratio']) for r in rows]

    # Latest date in DB for baseline cutoff
    latest_row = conn.execute(
        "SELECT MAX(date(bar_time,'unixepoch')) AS ld FROM ohlcv_history_execution"
    ).fetchone()
    latest_date = latest_row['ld'] if latest_row else None

    # 20d baseline (20 days prior to the lookback window)
    baseline_rows = conn.execute("""
        SELECT AVG(ABS((close - open) / NULLIF(open, 0) * 100.0)) AS avg_abs_ret
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') IN (
            SELECT DISTINCT date(bar_time,'unixepoch')
            FROM ohlcv_history_execution
            ORDER BY date(bar_time,'unixepoch') DESC
            LIMIT 30
        )
          AND date(bar_time,'unixepoch') NOT IN (
            SELECT DISTINCT date(bar_time,'unixepoch')
            FROM ohlcv_history_execution
            ORDER BY date(bar_time,'unixepoch') DESC
            LIMIT ?
        )
    """, (lookback,)).fetchone()
    baseline_mean = _safe(baseline_rows['avg_abs_ret']) if baseline_rows else 1.0
    current_mean  = _rolling_mean(abs_rets)
    vol_ratio = current_mean / max(baseline_mean, 1e-6)

    # Breadth — use most recent available date
    breadth_row = conn.execute("""
        SELECT breadth_score, ad_ratio
        FROM market_breadth_daily
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    breadth_score = _safe(breadth_row['breadth_score']) if breadth_row else 0.0
    ad_ratio_val  = _safe(breadth_row['ad_ratio']) if breadth_row else 0.0

    return {
        'median_ret':    _rolling_mean(rets),
        'ret_std':       _rolling_std(rets),
        'avg_abs_ret':   current_mean,
        'vol_ratio':     vol_ratio,
        'adv_ratio':     _rolling_mean(adv),
        'breadth_score': breadth_score,
        'ad_ratio':      ad_ratio_val,
        'feature_vector': [
            _rolling_mean(rets),
            _rolling_std(rets),
            current_mean,
            vol_ratio,
            _rolling_mean(adv),
            breadth_score,
        ],
    }


# ---------------------------------------------------------------------------
# cmd_early_warning
# ---------------------------------------------------------------------------

def cmd_early_warning(params):
    """
    Real-time regime change warning system.
    Compares current market stats to historical precursor patterns.
    """
    lookback = int(params.get('lookback', 5))

    try:
        conn = get_db()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    current_regime = _detect_current_regime(conn)
    current_stats  = _get_current_stats(conn, lookback=lookback)
    conn.close()

    if current_stats is None:
        return {'success': False, 'error': 'No recent OHLCV data available'}

    # Load precursor patterns
    li_result = cmd_leading_indicators({'lookback': lookback})
    if not li_result['success']:
        return li_result

    precursors = li_result['transition_precursors']
    current_vec = current_stats['feature_vector']

    # Score each transition that starts from current regime
    warnings = []
    for key, prec in precursors.items():
        from_regime, to_regime = key.split('→')
        if from_regime != current_regime:
            continue
        if prec['count'] < 2:
            continue

        fv = prec.get('_feature_vector', [])
        if not fv or len(fv) != len(current_vec):
            continue

        sim = _cosine_similarity(current_vec, fv)
        sim = max(0.0, sim)  # clip negatives

        warnings.append({
            'transition':       key,
            'target_regime':    to_regime,
            'similarity_score': round(sim, 4),
            'historical_count': prec['count'],
            'avg_run_days_before': prec['avg_run_days_before'],
        })

    warnings.sort(key=lambda x: x['similarity_score'], reverse=True)

    # Determine warning level from top similarity
    max_sim = warnings[0]['similarity_score'] if warnings else 0.0
    if max_sim >= 0.90:
        warning_level = 'CRITICAL'
    elif max_sim >= 0.75:
        warning_level = 'HIGH'
    elif max_sim >= 0.55:
        warning_level = 'MEDIUM'
    else:
        warning_level = 'LOW'

    most_likely_next = warnings[0]['target_regime'] if warnings else None
    top_prob = warnings[0]['similarity_score'] if warnings else 0.0

    # Key signals
    key_signals = []
    if current_stats['vol_ratio'] > 1.5:
        key_signals.append(f"vol_ratio={current_stats['vol_ratio']:.2f} (>1.5x baseline — volatility accelerating)")
    if current_stats['ret_std'] > 2.0:
        key_signals.append(f"ret_std={current_stats['ret_std']:.2f}% (high daily return dispersion)")
    if current_stats['breadth_score'] is not None and current_stats['breadth_score'] < 0.3:
        key_signals.append(f"breadth_score={current_stats['breadth_score']:.2f} (weak breadth)")
    if current_stats['adv_ratio'] < 0.40:
        key_signals.append(f"adv_ratio={current_stats['adv_ratio']:.2f} (bearish breadth)")
    if current_stats['median_ret'] < -0.3:
        key_signals.append(f"median_ret={current_stats['median_ret']:.2f}% (negative drift)")

    return {
        'success':             True,
        'current_regime':      current_regime,
        'warning_level':       warning_level,
        'most_likely_next_regime': most_likely_next,
        'similarity_to_precursor': round(top_prob, 4),
        'key_signals':         key_signals,
        'current_stats':       {k: round(v, 4) if isinstance(v, float) else v
                                for k, v in current_stats.items()
                                if k != 'feature_vector'},
        'all_warnings':        warnings,
        'lookback_days':       lookback,
    }


# ---------------------------------------------------------------------------
# cmd_forecast
# ---------------------------------------------------------------------------

def cmd_forecast(params):
    """
    Multi-step regime forecast using transition matrix.
    Returns P(regime in N days) for N = 1, 3, 5, 10.
    """
    horizons = [1, 3, 5, 10]

    try:
        conn = get_db()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    current_regime = _detect_current_regime(conn)
    conn.close()

    tm_result = cmd_transition_matrix({})
    if not tm_result['success']:
        return tm_result

    matrix = tm_result['transition_matrix']
    avg_holding = tm_result['avg_holding_days']

    # Build numpy or pure-python transition matrix
    idx = {r: i for i, r in enumerate(REGIMES)}

    # Initial state distribution: 1.0 for current regime
    state = [0.0] * len(REGIMES)
    if current_regime in idx:
        state[idx[current_regime]] = 1.0

    # Build P matrix (row = from, col = to)
    P = [[matrix.get(r_from, {}).get(r_to, 0.0) for r_to in REGIMES] for r_from in REGIMES]

    # We need: state * P^n  (vector × matrix)
    def mat_mult(A, B):
        n = len(A)
        C = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                for k in range(n):
                    C[i][j] += A[i][k] * B[k][j]
        return C

    def vec_mat(v, M):
        n = len(v)
        out = [0.0] * n
        for j in range(n):
            for i in range(n):
                out[j] += v[i] * M[i][j]
        return out

    forecasts = {}
    Pn = [[1.0 if i == j else 0.0 for j in range(len(REGIMES))] for i in range(len(REGIMES))]  # identity
    prev_h = 0
    for h in sorted(horizons):
        steps = h - prev_h
        for _ in range(steps):
            Pn = mat_mult(Pn, P)
        prev_h = h
        dist = vec_mat(state, Pn)
        forecasts[h] = {r: round(dist[idx[r]], 4) for r in REGIMES}

    # Expected duration remaining (geometric: 1/P(leave) = 1/(1-P(stay)))
    p_stay = matrix.get(current_regime, {}).get(current_regime, 0.5)
    p_leave = 1.0 - p_stay
    expected_duration_remaining = round(1.0 / p_leave, 1) if p_leave > 0 else float('inf')

    # Dangerous regimes
    dangerous_regimes = ['HIGH_VOLATILITY', 'TRENDING_DOWN']

    # Which regimes have >20% probability at any horizon?
    danger_flags = {}
    for h, dist in forecasts.items():
        for r in dangerous_regimes:
            if dist.get(r, 0) > 0.20:
                if r not in danger_flags:
                    danger_flags[r] = []
                danger_flags[r].append(h)

    return {
        'success':                 True,
        'current_regime':          current_regime,
        'p_stay_current':          round(p_stay, 4),
        'expected_days_remaining': expected_duration_remaining,
        'forecast_horizons':       forecasts,
        'danger_flags':            danger_flags,
        'avg_holding_days':        avg_holding,
        'regimes_ranked_by': {
            h: sorted(forecasts[h].items(), key=lambda kv: kv[1], reverse=True)
            for h in horizons
        },
    }


# ---------------------------------------------------------------------------
# cmd_volatility_acceleration
# ---------------------------------------------------------------------------

def cmd_volatility_acceleration(params):
    """
    Detect volatility acceleration — key precursor to regime changes.
    Computes vol_5d, vol_10d, vol_20d and acceleration ratio.
    """
    lookback_days = int(params.get('lookback', 60))

    try:
        conn = get_db()
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}

    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS d,
            AVG(ABS((close - open) / NULLIF(open, 0) * 100.0)) AS avg_abs_ret
        FROM ohlcv_history_execution
        WHERE date(bar_time,'unixepoch') >= ?
        GROUP BY d
        ORDER BY d
    """, (cutoff,)).fetchall()

    conn.close()

    if not rows:
        return {'success': False, 'error': 'No OHLCV data in the requested window'}

    dates = [r['d'] for r in rows]
    vals  = [_safe(r['avg_abs_ret']) for r in rows]
    n     = len(vals)

    def window_avg(arr, w):
        if len(arr) < w:
            return _rolling_mean(arr)
        return _rolling_mean(arr[-w:])

    vol_5d  = window_avg(vals, 5)
    vol_10d = window_avg(vals, 10)
    vol_20d = window_avg(vals, 20)
    vol_20d_safe = max(vol_20d, 1e-6)

    acceleration = vol_5d / vol_20d_safe
    trend = 'ACCELERATING' if acceleration > 1.3 else ('DECELERATING' if acceleration < 0.75 else 'STABLE')

    # Rolling acceleration series (for context)
    accel_series = []
    for i in range(min(20, n)):
        idx = n - 20 + i
        if idx < 5:
            continue
        s5  = _rolling_mean(vals[max(0, idx - 5):idx])
        s20 = _rolling_mean(vals[max(0, idx - 20):idx])
        accel_series.append({
            'date':         dates[idx - 1],
            'vol_5d':       round(s5, 4),
            'vol_20d':      round(s20, 4),
            'acceleration': round(s5 / max(s20, 1e-6), 4),
        })

    # GARCH-proxy: recent variance vs historical variance
    recent_vals   = vals[-5:]  if len(vals) >= 5  else vals
    historic_vals = vals[:-5]  if len(vals) > 5   else vals
    recent_var   = _rolling_std(recent_vals) ** 2
    historic_var = _rolling_std(historic_vals) ** 2
    garch_ratio  = round(recent_var / max(historic_var, 1e-8), 3)

    # Historical acceleration before each past regime change
    try:
        seq = _load_regime_map()
        _, transitions = _build_regime_runs(seq)
    except FileNotFoundError:
        transitions = []

    # Map dates to avg_abs_ret for historical lookup
    date_to_vol = {r['d']: _safe(r['avg_abs_ret']) for r in rows}

    hist_accel = {}
    for t in transitions:
        td = t['transition_date']
        key = f"{t['from']}→{t['to']}"
        # Look at 5d avg vol before transition
        transition_dt = datetime.strptime(td, '%Y-%m-%d')
        pre_dates = []
        for i in range(1, 6):
            d = (transition_dt - timedelta(days=i)).strftime('%Y-%m-%d')
            if d in date_to_vol:
                pre_dates.append(date_to_vol[d])
        if not pre_dates:
            continue
        pre_vol = _rolling_mean(pre_dates)
        if key not in hist_accel:
            hist_accel[key] = []
        hist_accel[key].append(pre_vol)

    hist_accel_summary = {
        key: round(_rolling_mean(v_list), 4)
        for key, v_list in hist_accel.items()
    }

    return {
        'success':             True,
        'current_vol_5d':      round(vol_5d, 4),
        'current_vol_10d':     round(vol_10d, 4),
        'current_vol_20d':     round(vol_20d, 4),
        'current_acceleration': round(acceleration, 4),
        'trend':               trend,
        'garch_ratio':         garch_ratio,
        'garch_interpretation': (
            'VOLATILITY_CLUSTERING' if garch_ratio > 1.5
            else 'MEAN_REVERTING' if garch_ratio < 0.67
            else 'STABLE'
        ),
        'n_days_analyzed':     n,
        'first_date':          dates[0] if dates else None,
        'last_date':           dates[-1] if dates else None,
        'rolling_acceleration_series': accel_series[-10:],  # last 10 points
        'hist_vol_before_transitions': hist_accel_summary,
        'thresholds': {
            'accelerating': 1.3,
            'warning':      1.5,
            'critical':     2.0,
        },
    }


# ---------------------------------------------------------------------------
# cmd_report
# ---------------------------------------------------------------------------

def cmd_report(params):
    """Combined report: transition_matrix + early_warning + forecast + volatility_acceleration."""
    tm  = cmd_transition_matrix(params)
    ew  = cmd_early_warning(params)
    fc  = cmd_forecast(params)
    va  = cmd_volatility_acceleration(params)

    success = all(r.get('success') for r in [tm, ew, fc, va])

    # Summary banner
    current_regime = ew.get('current_regime', 'UNKNOWN')
    warning_level  = ew.get('warning_level', 'UNKNOWN')
    next_regime    = ew.get('most_likely_next_regime', None)
    accel          = va.get('current_acceleration', None)
    accel_trend    = va.get('trend', None)
    expected_days  = fc.get('expected_days_remaining', None)

    summary = {
        'current_regime':              current_regime,
        'warning_level':               warning_level,
        'most_likely_next_regime':     next_regime,
        'similarity_to_transition':    ew.get('similarity_to_precursor'),
        'expected_days_in_regime':     expected_days,
        'volatility_acceleration':     accel,
        'volatility_trend':            accel_trend,
        'key_signals':                 ew.get('key_signals', []),
        'danger_flags':                fc.get('danger_flags', {}),
    }

    return {
        'success':                success,
        'generated_at':           datetime.utcnow().isoformat() + 'Z',
        'summary':                summary,
        'transition_matrix':      tm,
        'early_warning':          ew,
        'forecast':               fc,
        'volatility_acceleration': va,
    }


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'transition_matrix':      cmd_transition_matrix,
    'leading_indicators':     cmd_leading_indicators,
    'early_warning':          cmd_early_warning,
    'forecast':               cmd_forecast,
    'volatility_acceleration': cmd_volatility_acceleration,
    'report':                 cmd_report,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'success': False,
            'error':   'No command specified',
            'available': list(COMMANDS.keys()),
        }, indent=2))
        sys.exit(1)

    cmd_name = sys.argv[1]
    if cmd_name not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error':   f'Unknown command: {cmd_name}',
            'available': list(COMMANDS.keys()),
        }, indent=2))
        sys.exit(1)

    # Parse params: JSON string (standard convention) or key=value pairs
    params = {}
    if len(sys.argv) > 2:
        try:
            params = json.loads(sys.argv[2])
        except (json.JSONDecodeError, ValueError):
            for arg in sys.argv[2:]:
                if '=' in arg:
                    k, v = arg.split('=', 1)
                    params[k.strip()] = v.strip()

    try:
        result = COMMANDS[cmd_name](params)
    except Exception as exc:
        result = {'success': False, 'error': str(exc)}

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get('success') else 1)


if __name__ == '__main__':
    main()
