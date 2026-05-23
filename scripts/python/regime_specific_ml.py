#!/usr/bin/env python3
"""
Phase 79 — Regime-Specific ML Models
"نموذج ML مختلف لكل regime — لأن physics كل نظام مختلفة"

Architecture:
  1. assign_regimes  — Label all historical dates with HMM regime (OHLCV-based, full history)
  2. train           — Train separate LightGBM per regime on explosion features
  3. evaluate        — Compare regime-specific vs global model (precision, recall, AUC)
  4. predict         — Predict using current regime's model (auto-detect regime)
  5. adversarial     — Adversarial validation: detect distribution shift 2024→2026
  6. report          — Full regime-specific research report

Key insight: breadth only covers 90 days. We build an OHLCV-only HMM for full 2021-2026 history.
"""
import sys, json, sqlite3, datetime, math, pickle, os
from pathlib import Path
from collections import defaultdict, Counter

DB_PATH     = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
MODELS_DIR  = Path(__file__).parent / 'models' / 'regime_models'
GLOBAL_MODEL_PATH = Path(__file__).parent / 'models' / 'explosion_model.txt'
REGIME_HMM_PATH   = Path(__file__).parent / 'models' / 'ohlcv_regime_hmm.json'

# Must match explosion_ml.py FEATURE_COLS exactly (13 features)
FEATURE_COLS = [
    'pre1_bb_width',    'pre3_bb_width',    'pre5_bb_width',
    'pre1_vol_ratio',   'pre3_vol_ratio',   'pre5_vol_ratio',
    'pre1_rsi',         'pre3_rsi',         'pre5_rsi',
    'pre3_momentum_5d', 'pre5_momentum_5d',
    'pre5_bb_position', 'pre5_compression_days',
]

REGIME_LABELS = {
    0: 'TRENDING_UP',
    1: 'TRENDING_DOWN',
    2: 'HIGH_VOLATILITY',
    3: 'CHOPPY',
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _safe(v, d=0.0):
    try: return float(v) if v is not None and math.isfinite(float(v)) else d
    except: return d


# ─────────────────────────────────────────────────────────────────────────────
# OHLCV-Based Regime Assignment (full history, no breadth dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _build_market_daily_stats(conn, start_date='2021-01-01'):
    """Build daily market stats from OHLCV — no breadth needed."""
    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS bar_date,
            AVG((close - open) / NULLIF(open, 0) * 100) AS median_ret,
            AVG(ABS((close - open) / NULLIF(open, 0) * 100)) AS avg_abs_ret,
            COUNT(DISTINCT symbol) AS n_symbols,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS adv_ratio
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') >= ?
        GROUP BY bar_date
        ORDER BY bar_date
    """, (start_date,)).fetchall()
    return {r['bar_date']: dict(r) for r in rows}


def _assign_ohlcv_regimes(daily_stats, window=20):
    """
    Rule-based + rolling stats regime assignment.
    4 regimes:
      TRENDING_UP    : rolling median_ret > +0.4% AND ret_std moderate
      TRENDING_DOWN  : rolling median_ret < -0.4% AND ret_std moderate
      HIGH_VOLATILITY: rolling ret_std > 2.5%
      CHOPPY         : everything else (low vol, no trend)
    """
    dates  = sorted(daily_stats.keys())
    regime = {}

    for i, d in enumerate(dates):
        window_dates = dates[max(0, i - window + 1): i + 1]
        rets  = [daily_stats[wd]['median_ret'] for wd in window_dates if daily_stats[wd]['median_ret'] is not None]
        if not rets:
            regime[d] = 'CHOPPY'
            continue

        roll_mean = sum(rets) / len(rets)
        roll_std  = (sum((r - roll_mean) ** 2 for r in rets) / len(rets)) ** 0.5
        adv       = daily_stats[d].get('adv_ratio', 0.5) or 0.5

        if roll_std > 2.5:
            regime[d] = 'HIGH_VOLATILITY'
        elif roll_mean > 0.35 and adv > 0.55:
            regime[d] = 'TRENDING_UP'
        elif roll_mean < -0.35 and adv < 0.45:
            regime[d] = 'TRENDING_DOWN'
        else:
            regime[d] = 'CHOPPY'

    return regime


def cmd_assign_regimes(params):
    """Build and save OHLCV-based regime labels for all dates 2021-2026.

    params:
      start_date : str (default '2021-01-01')
      window     : int rolling window in days (default 20)
    """
    start_date = params.get('start_date', '2021-01-01')
    window     = int(params.get('window', 20))

    conn = get_db()
    daily = _build_market_daily_stats(conn, start_date)
    conn.close()

    if not daily:
        return {'success': False, 'error': 'No OHLCV market data found'}

    regimes = _assign_ohlcv_regimes(daily, window=window)
    dist = Counter(regimes.values())

    # Save for reuse
    REGIME_HMM_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGIME_HMM_PATH, 'w') as f:
        json.dump({
            'regimes':       regimes,
            'distribution':  dict(dist),
            'n_dates':       len(regimes),
            'period':        f'{min(regimes.keys())}→{max(regimes.keys())}',
            'window':        window,
            'assigned_at':   datetime.datetime.now().isoformat(),
        }, f)

    return {
        'success':      True,
        'n_dates':      len(regimes),
        'distribution': dict(dist),
        'period':       f'{min(regimes.keys())}→{max(regimes.keys())}',
        'saved_to':     str(REGIME_HMM_PATH),
    }


def _load_regime_map():
    """Load saved OHLCV regime map."""
    if not REGIME_HMM_PATH.exists():
        return None
    with open(REGIME_HMM_PATH) as f:
        data = json.load(f)
    return data.get('regimes', {})


# ─────────────────────────────────────────────────────────────────────────────
# Training Data Builder
# ─────────────────────────────────────────────────────────────────────────────

def _load_training_data(conn, regime_map, is_end='2025-12-31', oos_start='2026-01-30'):
    """Load explosive_moves with features + labels, grouped by regime."""
    rows = conn.execute("""
        SELECT
            symbol, explosion_date, direction, return_3d,
            pre1_bb_width, pre3_bb_width, pre5_bb_width,
            pre1_vol_ratio, pre3_vol_ratio, pre5_vol_ratio,
            pre1_rsi, pre3_rsi, pre5_rsi,
            pre3_momentum_5d, pre5_momentum_5d,
            pre5_bb_position, pre5_compression_days
        FROM explosive_moves
        WHERE pre1_rsi IS NOT NULL
          AND return_3d IS NOT NULL
        ORDER BY explosion_date
    """).fetchall()

    is_data  = defaultdict(lambda: {'X': [], 'y': []})
    oos_data = defaultdict(lambda: {'X': [], 'y': []})

    for r in rows:
        edate = r['explosion_date']
        label = 1 if (r['return_3d'] or 0) >= 0.05 else 0  # >= 5% gain in 3 days
        feat  = [_safe(r[c] if c in r.keys() else 0) for c in FEATURE_COLS]

        regime = regime_map.get(edate, 'UNKNOWN')
        if regime == 'UNKNOWN':
            continue

        if edate <= is_end:
            is_data[regime]['X'].append(feat)
            is_data[regime]['y'].append(label)
        elif edate >= oos_start:
            oos_data[regime]['X'].append(feat)
            oos_data[regime]['y'].append(label)

    return is_data, oos_data


# ─────────────────────────────────────────────────────────────────────────────
# Train Regime-Specific Models
# ─────────────────────────────────────────────────────────────────────────────

def cmd_train(params):
    """Train separate LightGBM model per regime.

    params:
      min_samples : int minimum IS samples per regime (default 80)
      is_end      : str IS end date (default '2025-12-31')
      oos_start   : str OOS start date (default '2026-01-30')
    """
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'lightgbm not installed'}

    min_samples = int(params.get('min_samples', 80))
    is_end      = params.get('is_end', '2025-12-31')
    oos_start   = params.get('oos_start', '2026-01-30')

    regime_map = _load_regime_map()
    if not regime_map:
        return {'success': False, 'error': 'No regime map — run assign_regimes first'}

    conn = get_db()
    is_data, oos_data = _load_training_data(conn, regime_map, is_end, oos_start)
    conn.close()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for regime in ['TRENDING_UP', 'TRENDING_DOWN', 'HIGH_VOLATILITY', 'CHOPPY']:
        is_X  = np.array(is_data[regime]['X'])
        is_y  = np.array(is_data[regime]['y'])
        oos_X = np.array(oos_data[regime]['X'])
        oos_y = np.array(oos_data[regime]['y'])

        if len(is_X) < min_samples:
            results.append({
                'regime':    regime,
                'skipped':   True,
                'reason':    f'Only {len(is_X)} IS samples (min={min_samples})',
                'n_is':      len(is_X),
            })
            continue

        pos_ratio = is_y.sum() / len(is_y) if len(is_y) > 0 else 0
        scale_pos = (1 - pos_ratio) / (pos_ratio + 1e-8)

        model = lgb.LGBMClassifier(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=20,
            min_child_samples=15,
            feature_fraction=0.8,
            reg_alpha=1.0,
            reg_lambda=1.0,
            scale_pos_weight=scale_pos,
            random_state=42,
            n_jobs=1,
            verbose=-1,
        )
        model.fit(is_X, is_y)

        is_pred  = model.predict_proba(is_X)[:, 1]
        is_prec  = float((is_y[is_pred >= 0.5]).sum() / max((is_pred >= 0.5).sum(), 1))
        is_rec   = float((is_y[is_pred >= 0.5]).sum() / max(is_y.sum(), 1))

        oos_prec, oos_rec, oos_n = None, None, 0
        if len(oos_X) >= 10:
            oos_pred = model.predict_proba(oos_X)[:, 1]
            oos_n    = int((oos_pred >= 0.5).sum())
            oos_prec = float((oos_y[oos_pred >= 0.5]).sum() / max(oos_n, 1))
            oos_rec  = float((oos_y[oos_pred >= 0.5]).sum() / max(oos_y.sum(), 1))

        # Feature importance
        imp = model.feature_importances_
        top_feats = sorted(zip(FEATURE_COLS[:len(imp)], imp),
                          key=lambda x: -x[1])[:5]

        # Save model
        model_path = MODELS_DIR / f'explosion_model_{regime.lower()}.pkl'
        with open(model_path, 'wb') as f:
            pickle.dump({'model': model, 'regime': regime,
                        'feature_cols': FEATURE_COLS, 'trained_at': datetime.datetime.now().isoformat()}, f)

        results.append({
            'regime':       regime,
            'skipped':      False,
            'n_is':         len(is_X),
            'n_oos':        len(oos_X),
            'is_pos_rate':  round(pos_ratio, 3),
            'is_precision': round(is_prec, 3),
            'is_recall':    round(is_rec, 3),
            'oos_precision': round(oos_prec, 3) if oos_prec is not None else None,
            'oos_recall':    round(oos_rec, 3) if oos_rec is not None else None,
            'oos_signals':   oos_n,
            'top_features':  [{'feature': f, 'importance': int(v)} for f, v in top_feats],
            'saved_to':      str(model_path),
        })

    trained = [r for r in results if not r.get('skipped')]
    return {
        'success':        True,
        'regimes_trained': len(trained),
        'regimes_skipped': len(results) - len(trained),
        'results':         results,
        'is_period':       f'→{is_end}',
        'oos_period':      f'{oos_start}→',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Evaluate: Regime-Specific vs Global
# ─────────────────────────────────────────────────────────────────────────────

def cmd_evaluate(params):
    """Compare regime-specific models vs global model on OOS data."""
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'lightgbm not installed'}

    regime_map = _load_regime_map()
    if not regime_map:
        return {'success': False, 'error': 'Run assign_regimes first'}

    if not GLOBAL_MODEL_PATH.exists():
        return {'success': False, 'error': 'No global model — run egx:ml:train first'}

    global_model = lgb.Booster(model_file=str(GLOBAL_MODEL_PATH))
    global_is_lgb_booster = True

    oos_start = params.get('oos_start', '2026-01-30')
    conn = get_db()
    _, oos_data = _load_training_data(conn, regime_map, oos_start=oos_start)
    conn.close()

    results = []

    for regime in ['TRENDING_UP', 'TRENDING_DOWN', 'HIGH_VOLATILITY', 'CHOPPY']:
        model_path = MODELS_DIR / f'explosion_model_{regime.lower()}.pkl'
        oos_X = np.array(oos_data[regime]['X'])
        oos_y = np.array(oos_data[regime]['y'])

        if len(oos_X) < 5:
            results.append({'regime': regime, 'n_oos': len(oos_X), 'skipped': True})
            continue

        # Global model on this regime's data (LightGBM Booster)
        global_pred = global_model.predict(oos_X)
        g_prec = float((oos_y[global_pred >= 0.5]).sum() / max((global_pred >= 0.5).sum(), 1))
        g_sig  = int((global_pred >= 0.5).sum())

        # Regime-specific model (sklearn LGBMClassifier)
        r_prec, r_sig = None, 0
        if model_path.exists():
            with open(model_path, 'rb') as f:
                rm = pickle.load(f)
            r_model = rm.get('model') or rm
            r_pred  = r_model.predict_proba(oos_X)[:, 1]
            r_prec  = float((oos_y[r_pred >= 0.5]).sum() / max((r_pred >= 0.5).sum(), 1))
            r_sig   = int((r_pred >= 0.5).sum())

        lift = round((r_prec - g_prec) / (g_prec + 1e-8) * 100, 1) if r_prec is not None else None

        results.append({
            'regime':              regime,
            'n_oos':               len(oos_X),
            'skipped':             False,
            'global_precision':    round(g_prec, 3),
            'global_signals':      g_sig,
            'regime_precision':    round(r_prec, 3) if r_prec is not None else None,
            'regime_signals':      r_sig,
            'precision_lift_pct':  lift,
            'winner':              'REGIME' if (r_prec or 0) > g_prec else 'GLOBAL',
        })

    tested = [r for r in results if not r.get('skipped') and r.get('n_oos', 0) >= 5]
    regime_wins = sum(1 for r in tested if r.get('winner') == 'REGIME')
    return {
        'success':      True,
        'oos_period':   f'{oos_start}→',
        'results':      results,
        'regimes_tested': len(tested),
        'regime_model_wins': regime_wins,
        'global_model_wins': len(tested) - regime_wins,
        'verdict': (
            'REGIME_MODELS_BETTER' if regime_wins >= len(tested) and len(tested) > 0
            else 'MIXED' if regime_wins > 0
            else 'GLOBAL_MODEL_BETTER'
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Predict using Current Regime's Model
# ─────────────────────────────────────────────────────────────────────────────

def _detect_current_regime(conn):
    """Detect today's regime from OHLCV (last 20 days of market stats)."""
    rows = conn.execute("""
        SELECT
            date(bar_time,'unixepoch') AS bar_date,
            AVG((close - open) / NULLIF(open, 0) * 100) AS median_ret,
            COUNT(DISTINCT symbol) AS n_sym,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS adv_ratio
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') >= date('now','-30 days')
        GROUP BY bar_date
        ORDER BY bar_date DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        return 'UNKNOWN'

    rets = [_safe(r['median_ret']) for r in rows]
    adv  = _safe(rows[0]['adv_ratio'])
    roll_mean = sum(rets) / len(rets)
    roll_std  = (sum((r - roll_mean)**2 for r in rets) / len(rets))**0.5

    if roll_std > 2.5:
        return 'HIGH_VOLATILITY'
    elif roll_mean > 0.35 and adv > 0.55:
        return 'TRENDING_UP'
    elif roll_mean < -0.35 and adv < 0.45:
        return 'TRENDING_DOWN'
    else:
        return 'CHOPPY'


def cmd_predict(params):
    """Predict explosions for today using the current regime's model.

    params:
      min_prob  : float minimum probability (default 0.60)
      top_n     : int top N symbols (default 20)
      regime    : str override regime (optional)
    """
    try:
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'numpy not installed'}

    min_prob = float(params.get('min_prob', 0.60))
    top_n    = int(params.get('top_n', 20))

    conn = get_db()
    current_regime = params.get('regime') or _detect_current_regime(conn)

    model_path = MODELS_DIR / f'explosion_model_{current_regime.lower()}.pkl'

    # Fallback to global model if no regime-specific model
    if not model_path.exists():
        if not GLOBAL_MODEL_PATH.exists():
            conn.close()
            return {'success': False, 'error': f'No model for regime {current_regime} and no global model'}
        try:
            import lightgbm as lgb
            _global_lgb = lgb.Booster(model_file=str(GLOBAL_MODEL_PATH))
            # Wrap as a simple callable
            class _BoosterWrapper:
                def __init__(self, b): self.b = b
                def predict_proba(self, X):
                    p = self.b.predict(X)
                    import numpy as np
                    return np.column_stack([1 - p, p])
            model = _BoosterWrapper(_global_lgb)
        except Exception as ex:
            conn.close()
            return {'success': False, 'error': f'Failed to load global model: {ex}'}
        model_source = 'global_fallback'
    else:
        with open(model_path, 'rb') as f:
            m = pickle.load(f)
        model = m.get('model') or m
        model_source = f'regime_{current_regime}'

    # Load today's top symbols from existing predictions
    today = datetime.date.today().isoformat()
    pred_rows = conn.execute("""
        SELECT p.symbol, p.explosion_prob,
               e.pre1_rsi, e.pre3_rsi, e.pre5_rsi,
               e.pre1_bb_width, e.pre3_bb_width, e.pre5_bb_width,
               e.pre1_vol_ratio, e.pre3_vol_ratio, e.pre5_vol_ratio,
               e.pre5_momentum_5d, e.pre5_bb_position, e.pre5_compression_days,
               e.pre3_adx, e.pre5_adx
        FROM explosion_predictions p
        LEFT JOIN (
            SELECT symbol, explosion_date,
                   pre1_rsi, pre3_rsi, pre5_rsi,
                   pre1_bb_width, pre3_bb_width, pre5_bb_width,
                   pre1_vol_ratio, pre3_vol_ratio, pre5_vol_ratio,
                   pre5_momentum_5d, pre5_bb_position, pre5_compression_days,
                   pre3_adx, pre5_adx
            FROM explosive_moves
            WHERE explosion_date = (SELECT MAX(explosion_date) FROM explosive_moves)
        ) e ON p.symbol = e.symbol
        WHERE p.pred_date = (SELECT MAX(pred_date) FROM explosion_predictions)
          AND p.explosion_prob >= ?
        ORDER BY p.explosion_prob DESC
        LIMIT ?
    """, (min_prob * 0.8, top_n * 2)).fetchall()
    conn.close()

    predictions = []
    for row in pred_rows:
        feat = [_safe(row[c] if c in row.keys() else 0.0) for c in FEATURE_COLS]
        if all(f == 0.0 for f in feat):
            continue
        X = np.array([feat])
        try:
            regime_prob = float(model.predict_proba(X)[0, 1])
        except Exception:
            continue
        if regime_prob >= min_prob:
            predictions.append({
                'symbol':       row['symbol'],
                'global_prob':  round(float(row['explosion_prob']), 3),
                'regime_prob':  round(regime_prob, 3),
                'regime_boost': round(regime_prob - float(row['explosion_prob']), 3),
            })

    predictions.sort(key=lambda x: -x['regime_prob'])

    return {
        'success':        True,
        'current_regime': current_regime,
        'model_source':   model_source,
        'as_of':          today,
        'n_predictions':  len(predictions),
        'predictions':    predictions[:top_n],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Validation — Detect Distribution Shift
# ─────────────────────────────────────────────────────────────────────────────

def cmd_adversarial(params):
    """Adversarial validation: can a classifier distinguish 2024-25 from 2026 data?

    If AUC > 0.65 → significant distribution shift → regime drift detected.
    If AUC ≈ 0.50 → distributions are similar → model should generalize.

    params:
      period_a_end   : str (default '2025-12-31') — training period end
      period_b_start : str (default '2026-01-01') — new period start
    """
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
    except ImportError:
        return {'success': False, 'error': 'scikit-learn not installed'}

    period_a_end   = params.get('period_a_end',   '2025-12-31')
    period_b_start = params.get('period_b_start', '2026-01-01')

    conn = get_db()
    rows = conn.execute("""
        SELECT
            explosion_date,
            pre1_rsi, pre3_rsi, pre5_rsi,
            pre1_bb_width, pre3_bb_width, pre5_bb_width,
            pre1_vol_ratio, pre3_vol_ratio, pre5_vol_ratio,
            pre5_momentum_5d, pre5_bb_position, pre5_compression_days,
            pre3_adx, pre5_adx
        FROM explosive_moves
        WHERE pre1_rsi IS NOT NULL
          AND explosion_date BETWEEN '2024-01-01' AND '2026-12-31'
        ORDER BY explosion_date
    """).fetchall()
    conn.close()

    X_a, X_b = [], []
    for r in rows:
        feat = [_safe(r[c] if c in r.keys() else 0) for c in FEATURE_COLS]
        if r['explosion_date'] <= period_a_end:
            X_a.append(feat)
        elif r['explosion_date'] >= period_b_start:
            X_b.append(feat)

    if len(X_a) < 20 or len(X_b) < 10:
        return {'success': False, 'error': f'Not enough data: Period A={len(X_a)}, Period B={len(X_b)}'}

    n = min(len(X_a), len(X_b) * 3)
    import random
    random.seed(42)
    X_a_samp = random.sample(X_a, n)

    X = np.array(X_a_samp + X_b)
    y = np.array([0] * n + [1] * len(X_b))

    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=1)
    auc_scores = cross_val_score(clf, X, y, cv=min(5, len(X_b)), scoring='roc_auc')
    auc = float(auc_scores.mean())

    drift_level = 'SEVERE' if auc > 0.75 else 'MODERATE' if auc > 0.65 else 'MILD' if auc > 0.58 else 'NONE'
    verdict     = 'REGIME_DRIFT_DETECTED' if auc > 0.65 else 'DISTRIBUTION_STABLE'

    # Feature-level drift
    clf.fit(X, y)
    imp = clf.feature_importances_
    top_drift_feats = sorted(zip(FEATURE_COLS[:len(imp)], imp), key=lambda x: -x[1])[:5]

    return {
        'success':           True,
        'n_period_a':        len(X_a),
        'n_period_b':        len(X_b),
        'period_a':          f'2024-01-01→{period_a_end}',
        'period_b':          f'{period_b_start}→2026',
        'adversarial_auc':   round(auc, 4),
        'auc_std':           round(float(auc_scores.std()), 4),
        'drift_level':       drift_level,
        'verdict':           verdict,
        'interpretation':    (
            'AUC ≈ 0.5 means same distribution. Higher = more drift.' if auc < 0.6
            else f'AUC={auc:.2f}: model sees a clear difference between periods'
        ),
        'top_drift_features': [{'feature': f, 'drift_importance': round(float(v), 4)}
                                for f, v in top_drift_feats],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Feature Importance Per Regime
# ─────────────────────────────────────────────────────────────────────────────

def cmd_regime_importance(params):
    """Show top features per regime and compare across regimes."""
    results = {}
    for regime in ['TRENDING_UP', 'TRENDING_DOWN', 'HIGH_VOLATILITY', 'CHOPPY']:
        model_path = MODELS_DIR / f'explosion_model_{regime.lower()}.pkl'
        if not model_path.exists():
            results[regime] = {'available': False}
            continue
        with open(model_path, 'rb') as f:
            m = pickle.load(f)
        model  = m.get('model') or m
        imp    = model.feature_importances_
        top5   = sorted(zip(FEATURE_COLS[:len(imp)], imp), key=lambda x: -x[1])[:5]
        results[regime] = {
            'available':    True,
            'top_features': [{'feature': f, 'importance': int(v)} for f, v in top5],
        }

    return {'success': True, 'regime_importance': results}


# ─────────────────────────────────────────────────────────────────────────────
# Full Report
# ─────────────────────────────────────────────────────────────────────────────

def cmd_report(params):
    regime_dist = cmd_assign_regimes({'start_date': '2023-01-01', 'window': 20})
    train_r     = cmd_train({'min_samples': 50})
    eval_r      = cmd_evaluate({})
    adv_r       = cmd_adversarial({})
    imp_r       = cmd_regime_importance({})
    return {
        'success':            True,
        'regime_assignment':  regime_dist,
        'training':           train_r,
        'evaluation':         eval_r,
        'adversarial':        adv_r,
        'feature_importance': imp_r,
    }


COMMANDS = {
    'assign_regimes':   cmd_assign_regimes,
    'train':            cmd_train,
    'evaluate':         cmd_evaluate,
    'predict':          cmd_predict,
    'adversarial':      cmd_adversarial,
    'regime_importance': cmd_regime_importance,
    'report':           cmd_report,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'report'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
