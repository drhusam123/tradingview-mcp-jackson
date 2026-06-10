#!/usr/bin/env python3
"""
Phase 75 — Hidden Regime Detection (HMM)
"الأنظمة السوقية الخفية — اكتشاف الحالات الكامنة بـ Hidden Markov Model"

بدلاً من BULL/BEAR/CHOPPY الثلاثة البسيطة،
يكتشف الـ HMM أنظمة سوقية كامنة مثل:
  - تراكم هادئ (Silent Accumulation)
  - جفاف السيولة (Liquidity Drought)
  - ذعر تراكمي (Panic Accumulation)
  - ارتفاع تجزئة (Retail Euphoria)
  - تدفق خارجي (Foreign Outflow)

Commands:
  fit           — Train HMM on EGX market breadth + OHLCV features
  detect        — Detect current hidden regime
  history       — Historical hidden regime sequence
  explosion_correlation — Which hidden regimes precede explosions most?
  report        — Full report
"""
import sys, json, sqlite3, datetime, math
from pathlib import Path
from collections import defaultdict, Counter

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
MODEL_PATH = Path(__file__).parent / 'models' / 'hmm_regime.json'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Feature Engineering for HMM
# ─────────────────────────────────────────────────────────────────────────────

def _build_market_features(conn, start_date='2021-01-01', end_date=None):
    """Build daily market-level features for HMM training.

    Features per day:
      - median_return       : median daily return across all stocks
      - breadth_adv_pct     : % advancing stocks
      - vol_ratio           : avg volume / 20d avg volume
      - return_std          : std of returns (cross-sectional volatility)
      - momentum_10d        : median 10d momentum
      - high_vol_pct        : % stocks with vol > 2x avg
    """
    import pandas as pd
    import numpy as np

    if end_date is None:
        end_date = datetime.date.today().isoformat()

    rows = conn.execute("""
        SELECT symbol,
               date(bar_time,'unixepoch') AS bar_date,
               close, volume,
               open
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') BETWEEN ? AND ?
        ORDER BY bar_time
    """, (start_date, end_date)).fetchall()

    if not rows:
        return None, None

    df = pd.DataFrame(rows, columns=['symbol', 'bar_date', 'close', 'volume', 'open'])
    df['close']  = pd.to_numeric(df['close'],  errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    df['open']   = pd.to_numeric(df['open'],   errors='coerce')

    # Compute daily returns per symbol
    df = df.sort_values(['symbol', 'bar_date'])
    df['ret'] = df.groupby('symbol')['close'].pct_change()

    # Rolling 20d avg volume per symbol
    df['vol_ma20'] = df.groupby('symbol')['volume'].transform(
        lambda x: x.rolling(20, min_periods=5).mean()
    )
    df['vol_ratio'] = df['volume'] / df['vol_ma20'].replace(0, float('nan'))

    # 10d momentum
    df['mom10'] = df.groupby('symbol')['close'].transform(
        lambda x: x.pct_change(10)
    )

    # Aggregate per day
    features = []
    dates = sorted(df['bar_date'].unique())

    for d in dates:
        day = df[df['bar_date'] == d].dropna(subset=['ret'])
        if len(day) < 20:
            continue

        rets      = day['ret'].values
        vols      = day['vol_ratio'].dropna().values
        moms      = day['mom10'].dropna().values

        n_adv     = (rets > 0).sum()
        n_dec     = (rets < 0).sum()
        n_total   = len(rets)

        features.append({
            'date':           d,
            'median_ret':     float(np.median(rets)),
            'breadth_adv':    float(n_adv / n_total) if n_total > 0 else 0.5,
            'ret_std':        float(np.std(rets)),
            'vol_ratio':      float(np.median(vols)) if len(vols) > 0 else 1.0,
            'momentum_10d':   float(np.median(moms)) if len(moms) > 0 else 0.0,
            'high_vol_pct':   float((vols > 2.0).mean()) if len(vols) > 0 else 0.0,
        })

    if not features:
        return None, None

    feat_df = pd.DataFrame(features).set_index('date').sort_index()
    feat_df = feat_df.dropna()

    return feat_df, dates


def _normalize(X):
    """Normalize each column to zero mean unit variance."""
    import numpy as np
    means = X.mean(axis=0)
    stds  = X.std(axis=0)
    stds[stds == 0] = 1.0
    return (X - means) / stds, means, stds


# ─────────────────────────────────────────────────────────────────────────────
# HMM Training & Detection
# ─────────────────────────────────────────────────────────────────────────────

REGIME_LABELS = {
    0: 'ACCUMULATION',
    1: 'TRENDING_UP',
    2: 'DISTRIBUTION',
    3: 'TRENDING_DOWN',
    4: 'LIQUIDITY_DROUGHT',
    5: 'HIGH_VOLATILITY',
}

REGIME_EMOJI = {
    'ACCUMULATION':      '🟡',
    'TRENDING_UP':       '🟢',
    'DISTRIBUTION':      '🟠',
    'TRENDING_DOWN':     '🔴',
    'LIQUIDITY_DROUGHT': '⚫',
    'HIGH_VOLATILITY':   '⚡',
}


def cmd_fit(params):
    """Train HMM on market features. Saves model params to JSON.

    params:
      n_states    : int (default 6)
      start_date  : str (default '2021-01-01')
      n_iter      : int (default 100)
    """
    try:
        from hmmlearn import hmm
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'hmmlearn not installed: pip install hmmlearn'}

    n_states   = int(params.get('n_states', 6))
    start_date = params.get('start_date', '2021-01-01')
    n_iter     = int(params.get('n_iter', 100))

    conn = get_db()
    feat_df, _ = _build_market_features(conn, start_date)
    conn.close()

    if feat_df is None or len(feat_df) < 60:
        return {'success': False, 'error': 'Not enough market feature data for HMM training'}

    X = feat_df.values
    X_norm, means, stds = _normalize(X)

    # Train Gaussian HMM
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type='diag',
        n_iter=n_iter,
        random_state=42,
    )
    model.fit(X_norm)

    # Predict hidden states
    states = model.predict(X_norm)
    dates  = list(feat_df.index)

    # Characterize each state by its mean feature values
    state_chars = {}
    for s in range(n_states):
        mask     = states == s
        if mask.sum() == 0:
            continue
        s_feats  = X[mask].mean(axis=0)
        s_cols   = feat_df.columns.tolist()

        # Auto-label based on breadth + ret + vol
        breadth = s_feats[s_cols.index('breadth_adv')]
        ret     = s_feats[s_cols.index('median_ret')]
        vol_r   = s_feats[s_cols.index('vol_ratio')]
        ret_std = s_feats[s_cols.index('ret_std')]

        if breadth > 0.55 and ret > 0.005:
            label = 'TRENDING_UP'
        elif breadth > 0.50 and ret > 0 and vol_r < 1.2:
            label = 'ACCUMULATION'
        elif breadth < 0.35 and ret < -0.005:
            label = 'TRENDING_DOWN'
        elif breadth < 0.45 and vol_r < 0.8:
            label = 'LIQUIDITY_DROUGHT'
        elif ret_std > 0.025:
            label = 'HIGH_VOLATILITY'
        else:
            label = 'DISTRIBUTION'

        state_chars[int(s)] = {
            'state_id':    int(s),
            'label':       label,
            'n_days':      int(mask.sum()),
            'breadth_adv': round(float(breadth), 3),
            'median_ret':  round(float(ret) * 100, 3),
            'vol_ratio':   round(float(vol_r), 3),
            'ret_std':     round(float(ret_std) * 100, 3),
        }

    # Save model params to JSON (hmmlearn models aren't JSON-serializable, save key arrays)
    import numpy as np
    model_data = {
        'n_states':      n_states,
        'start_date':    start_date,
        'n_days':        len(dates),
        'feature_cols':  feat_df.columns.tolist(),
        'means_norm':    means.tolist(),
        'stds_norm':     stds.tolist(),
        'hmm_means':     model.means_.tolist(),
        'hmm_covars':    model.covars_.tolist(),
        'hmm_transmat':  model.transmat_.tolist(),
        'hmm_startprob': model.startprob_.tolist(),
        'state_chars':   state_chars,
        'trained_at':    datetime.datetime.now().isoformat(),
        # Recent regime sequence for reference
        'recent_states': [
            {'date': dates[-i-1], 'state': int(states[-i-1]),
             'label': state_chars.get(int(states[-i-1]), {}).get('label', '?')}
            for i in range(min(30, len(dates)))
        ][::-1],
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, 'w') as fh:
        json.dump(model_data, fh, indent=2)

    current_state = int(states[-1])
    current_label = state_chars.get(current_state, {}).get('label', '?')

    return {
        'success':         True,
        'n_states':        n_states,
        'n_training_days': len(dates),
        'state_chars':     list(state_chars.values()),
        'current_regime':  current_label,
        'current_state_id': current_state,
        'model_saved':     str(MODEL_PATH),
    }


def _load_model():
    """Load saved HMM model data."""
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH) as fh:
        return json.load(fh)


def _predict_state(model_data, obs_vector):
    """Predict state for a single observation using Gaussian likelihood."""
    import numpy as np
    hmm_means  = np.array(model_data['hmm_means'])   # (n_states, n_features)
    hmm_covars = np.array(model_data['hmm_covars'])  # (n_states, n_features) for diag
    means_norm = np.array(model_data['means_norm'])
    stds_norm  = np.array(model_data['stds_norm'])

    x = (np.array(obs_vector) - means_norm) / np.maximum(stds_norm, 1e-8)

    log_likelihoods = []
    for s in range(len(hmm_means)):
        diff  = x - hmm_means[s]
        var   = np.maximum(hmm_covars[s], 1e-8)
        ll    = -0.5 * np.sum(diff**2 / var + np.log(2 * math.pi * var))
        log_likelihoods.append(ll)

    best_state = int(np.argmax(log_likelihoods))
    return best_state, log_likelihoods


def cmd_detect(params):
    """Detect current hidden regime using latest market data."""
    model_data = _load_model()
    if not model_data:
        return {'success': False, 'error': 'No HMM model — run fit first: npm run egx:hmm:fit'}

    conn = get_db()
    feat_df, _ = _build_market_features(conn, start_date='2026-01-01')
    conn.close()

    if feat_df is None or len(feat_df) == 0:
        return {'success': False, 'error': 'No recent market data for regime detection'}

    feature_cols = model_data['feature_cols']
    latest = feat_df[feature_cols].iloc[-1].values

    state_id, log_lls = _predict_state(model_data, latest)
    state_chars = model_data.get('state_chars', {})
    label = state_chars.get(str(state_id), {}).get('label', '?')
    emoji = REGIME_EMOJI.get(label, '❓')

    # Recent sequence
    recent = model_data.get('recent_states', [])[-7:]

    return {
        'success':        True,
        'current_date':   str(feat_df.index[-1]),
        'hidden_regime':  label,
        'state_id':       state_id,
        'emoji':          emoji,
        'state_features': {
            col: round(float(latest[i]) * 100, 3) if 'ret' in col or 'breadth' in col else round(float(latest[i]), 3)
            for i, col in enumerate(feature_cols)
        },
        'regime_context': state_chars.get(str(state_id), {}),
        'recent_7d':      recent,
    }


def cmd_history(params):
    """Full historical hidden regime sequence."""
    model_data = _load_model()
    if not model_data:
        return {'success': False, 'error': 'No HMM model — run fit first'}

    start_date = params.get('start_date', '2024-01-01')
    conn = get_db()
    feat_df, _ = _build_market_features(conn, start_date)
    conn.close()

    if feat_df is None:
        return {'success': False, 'error': 'No market data'}

    feature_cols = model_data['feature_cols']
    state_chars  = model_data.get('state_chars', {})

    history = []
    for d in feat_df.index:
        obs   = feat_df.loc[d, feature_cols].values
        state, _ = _predict_state(model_data, obs)
        label = state_chars.get(str(state), {}).get('label', '?')
        history.append({'date': d, 'state': state, 'label': label, 'emoji': REGIME_EMOJI.get(label, '?')})

    # Count distribution
    label_counts = Counter(h['label'] for h in history)

    return {
        'success':    True,
        'n_days':     len(history),
        'history':    history[-60:],   # last 60 days
        'distribution': dict(label_counts),
    }


def cmd_explosion_correlation(params):
    """Which hidden regimes most frequently precede explosive moves?

    For each explosive move, look back N days and find the hidden regime.
    Rank regimes by explosion rate.

    params:
      lookback_days : int (default 3) — days before explosion to check regime
    """
    model_data = _load_model()
    if not model_data:
        return {'success': False, 'error': 'No HMM model — run fit first'}

    lookback = int(params.get('lookback_days', 3))

    conn = get_db()
    feat_df, _ = _build_market_features(conn, '2021-01-01')
    explosions = conn.execute(
        "SELECT explosion_date FROM explosive_moves ORDER BY explosion_date"
    ).fetchall()
    conn.close()

    if feat_df is None:
        return {'success': False, 'error': 'No market data'}

    feature_cols = model_data['feature_cols']
    state_chars  = model_data.get('state_chars', {})
    dates_set    = set(feat_df.index)
    all_dates    = sorted(feat_df.index)

    # Map each explosion date → regime N days before
    regime_before_explosion = Counter()
    regime_total_days       = Counter()

    # Count regime distribution over all days
    for d in all_dates:
        obs   = feat_df.loc[d, feature_cols].values
        state, _ = _predict_state(model_data, obs)
        label = state_chars.get(str(state), {}).get('label', '?')
        regime_total_days[label] += 1

    # Count regime before each explosion
    exp_dates = [r['explosion_date'] for r in explosions]
    for exp_date in exp_dates:
        # Find the date `lookback_days` trading days before this explosion
        idx = all_dates.index(exp_date) if exp_date in all_dates else -1
        if idx >= lookback:
            pre_date = all_dates[idx - lookback]
            obs = feat_df.loc[pre_date, feature_cols].values
            state, _ = _predict_state(model_data, obs)
            label = state_chars.get(str(state), {}).get('label', '?')
            regime_before_explosion[label] += 1

    # Compute explosion rate per regime
    results = []
    for label, n_explosions in regime_before_explosion.items():
        total = regime_total_days.get(label, 1)
        rate  = n_explosions / total
        results.append({
            'regime':          label,
            'emoji':           REGIME_EMOJI.get(label, '?'),
            'n_explosions':    n_explosions,
            'total_days':      total,
            'explosion_rate':  round(rate * 100, 2),
        })

    results.sort(key=lambda x: -x['explosion_rate'])

    return {
        'success':         True,
        'lookback_days':   lookback,
        'n_explosions':    len(exp_dates),
        'results':         results,
        'best_regime':     results[0]['regime'] if results else None,
        'best_rate':       results[0]['explosion_rate'] if results else None,
    }


def cmd_report(params):
    fit_r  = cmd_fit({'n_states': 6, 'start_date': '2021-01-01'})
    det_r  = cmd_detect({})
    corr_r = cmd_explosion_correlation({'lookback_days': 3})
    return {
        'success':    True,
        'fit':        fit_r,
        'current':    det_r,
        'explosion_correlation': corr_r,
    }


COMMANDS = {
    'fit':                    cmd_fit,
    'detect':                 cmd_detect,
    'history':                cmd_history,
    'explosion_correlation':  cmd_explosion_correlation,
    'report':                 cmd_report,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'report'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
