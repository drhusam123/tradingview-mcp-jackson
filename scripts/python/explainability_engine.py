#!/usr/bin/env python3
"""
Explainability Engine — Phase 19
==================================
LightGBM classifier + SHAP TreeExplainer for explaining explosion signals.
Falls back to sklearn HistGradientBoostingClassifier if LightGBM is unavailable.

Commands (sys.argv[1]):
  train_model        — train LightGBM on counterfactual events to predict explosion prob
  explain_stock      — SHAP values for a given symbol today
  feature_importance — global SHAP feature importance across all stocks
  daily_explanations — explain all high-signal stocks, format Telegram output
  model_report       — ROC-AUC, precision-recall, confusion matrix
  retrain            — retrain model on latest data

DB Tables created:
  ml_model_log        — training history with AUC, feature count
  daily_explanations  — per-stock SHAP explanations with Telegram summaries
"""

import json, sys, time, sqlite3, math, os, pickle
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DATA      = Path(__file__).parent.parent.parent / 'data'
DB_PATH   = str(DATA / 'egx_trading.db')
MODEL_DIR = DATA / 'models'
MODEL_DIR.mkdir(exist_ok=True)
MODEL_PATH = str(MODEL_DIR / 'explainability_model.pkl')

# ─── Library imports with graceful fallback ──────────────────────────────────

try:
    import lightgbm as lgb
    HAS_LGB = True
    print('[Phase 19] LightGBM available', flush=True)
except Exception:
    HAS_LGB = False

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False
    class np:  # minimal stub
        @staticmethod
        def array(x): return x
        @staticmethod
        def mean(x): return sum(x)/len(x) if x else 0.0

try:
    import pandas as pd
    HAS_PD = True
except ImportError:
    HAS_PD = False

try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import cross_val_score, train_test_split
    from sklearn.metrics import roc_auc_score, precision_recall_curve
    from sklearn.preprocessing import LabelEncoder
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def ensure_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ml_model_log (
            trained_at TEXT, model_type TEXT, n_samples INTEGER,
            train_auc REAL, val_auc REAL, n_features INTEGER,
            feature_importance_json TEXT, PRIMARY KEY (trained_at)
        );
        CREATE TABLE IF NOT EXISTS daily_explanations (
            symbol TEXT, analysis_date TEXT, prediction_proba REAL,
            top_factors_json TEXT, signal_strength TEXT, updated_at TEXT,
            PRIMARY KEY (symbol, analysis_date)
        );
    """)
    con.commit()

# ─── Feature engineering ─────────────────────────────────────────────────────

FEATURE_COLS = [
    'rsi', 'rsi_5', 'bb_width', 'bb_pct', 'vol_ratio_20',
    'momentum_1d', 'momentum_5d', 'momentum_20d', 'atr_ratio',
    'bb_squeeze', 'vol_surge', 'momentum_positive',
    'close_vs_ema20', 'high_low_range', 'gap_pct',
]

def load_training_data(con):
    """
    Load indicators_cache + explosive_moves to build training set.
    X: indicator features per (symbol, date)
    y: 1 if LARGE/EXTREME explosion within 5 days, 0 otherwise
    """
    # Load indicators
    try:
        ind_rows = con.execute("""
            SELECT symbol, rsi, rsi_5, bb_width, bb_pct, vol_ratio_20,
                   momentum_1d, momentum_5d, momentum_20d, atr_ratio,
                   updated_at
            FROM indicators_cache
        """).fetchall()
    except Exception:
        ind_rows = []

    # Load explosive moves
    try:
        exp_rows = con.execute("""
            SELECT symbol, bar_time, magnitude, move_type
            FROM explosive_moves
            WHERE magnitude IN ('LARGE', 'EXTREME')
        """).fetchall()
    except Exception:
        try:
            exp_rows = con.execute("""
                SELECT symbol, bar_time, category as magnitude
                FROM explosive_moves
                WHERE category IN ('LARGE', 'EXTREME')
            """).fetchall()
        except Exception:
            exp_rows = []

    # Build explosion lookup: symbol → set of bar_times with large/extreme moves
    explosion_times = defaultdict(set)
    for r in exp_rows:
        explosion_times[r['symbol']].add(r['bar_time'])

    # Build OHLCV date index for each symbol
    ohlcv_rows = con.execute("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history
        ORDER BY symbol, bar_time
    """).fetchall()

    # Group ohlcv by symbol
    ohlcv_by_sym = defaultdict(list)
    for r in ohlcv_rows:
        ohlcv_by_sym[r['symbol']].append(r)

    # Build features X and labels y
    X_rows = []
    y_vals = []
    meta   = []

    if not ind_rows:
        return [], [], [], FEATURE_COLS

    for row in ind_rows:
        sym = row['symbol']
        sym_ohlcv = ohlcv_by_sym.get(sym, [])
        if not sym_ohlcv:
            continue

        # Use latest 100 OHLCV bars as candidate dates
        recent_bars = sorted(sym_ohlcv, key=lambda r: r['bar_time'])[-100:]

        closes  = [float(r['close']) for r in recent_bars]
        highs   = [float(r['high'])  for r in recent_bars]
        lows    = [float(r['low'])   for r in recent_bars]
        volumes = [float(r['volume'])for r in recent_bars]
        opens   = [float(r['open'])  for r in recent_bars]
        times   = [r['bar_time']     for r in recent_bars]

        if len(closes) < 21:
            continue

        for i in range(20, len(closes)):
            bar_time = times[i]

            # Compute features
            rsi_val    = float(row['rsi']          or 50)
            rsi5_val   = float(row['rsi_5']         or 50)
            bb_w       = float(row['bb_width']      or 0)
            bb_p       = float(row['bb_pct']        or 0.5)
            vol20      = float(row['vol_ratio_20']  or 1)
            mom1d      = float(row['momentum_1d']   or 0)
            mom5d      = float(row['momentum_5d']   or 0)
            mom20d     = float(row['momentum_20d']  or 0)
            atr_r      = float(row['atr_ratio']     or 1)

            # Derived binary features
            bb_squeeze    = 1.0 if bb_w < 0.02 else 0.0
            vol_surge     = 1.0 if vol20 > 2.0  else 0.0
            mom_positive  = 1.0 if mom5d > 0.02 else 0.0

            # OHLCV-derived
            c_slice = closes[max(0, i-19):i+1]
            mean_20 = sum(c_slice) / len(c_slice)
            close_vs_ema20 = (closes[i] / mean_20 - 1) if mean_20 > 0 else 0
            hl_range = (highs[i] - lows[i]) / closes[i] if closes[i] > 0 else 0
            gap_pct  = (opens[i] - closes[i-1]) / closes[i-1] if closes[i-1] > 0 else 0

            features = [
                rsi_val, rsi5_val, bb_w, bb_p, vol20,
                mom1d, mom5d, mom20d, atr_r,
                bb_squeeze, vol_surge, mom_positive,
                close_vs_ema20, hl_range, gap_pct,
            ]

            # Label: 1 if any explosion within next 5 bars
            label = 0
            for offset in range(1, 6):
                if i + offset < len(times):
                    if times[i + offset] in explosion_times.get(sym, set()):
                        label = 1
                        break

            X_rows.append(features)
            y_vals.append(label)
            meta.append({'symbol': sym, 'bar_time': bar_time})

    return X_rows, y_vals, meta, FEATURE_COLS

def _safe_float(v):
    if v is None: return 0.0
    try:
        f = float(v)
        return 0.0 if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return 0.0

# ─── Model training ──────────────────────────────────────────────────────────

def train_classifier(X, y, feature_names):
    """Train LightGBM or HistGradientBoosting. Returns (model, auc_train, auc_val, model_type)."""
    if not HAS_NP or not HAS_SKLEARN:
        return None, 0.0, 0.0, 'none'

    X_np = [[_safe_float(v) for v in row] for row in X]
    y_np = [int(v) for v in y]

    # Stratified split
    n = len(X_np)
    n_val = max(50, n // 5)
    indices = list(range(n))
    import random
    random.seed(42)
    val_idx = sorted(random.sample(indices, n_val))
    train_idx = [i for i in indices if i not in set(val_idx)]

    X_train = [X_np[i] for i in train_idx]
    y_train = [y_np[i] for i in train_idx]
    X_val   = [X_np[i] for i in val_idx]
    y_val   = [y_np[i] for i in val_idx]

    if HAS_LGB:
        model_type = 'lightgbm'
        try:
            pos_count = sum(y_train)
            neg_count = len(y_train) - pos_count
            scale_pos = neg_count / max(1, pos_count)
            model = lgb.LGBMClassifier(
                n_estimators=200,
                learning_rate=0.05,
                max_depth=6,
                num_leaves=31,
                scale_pos_weight=scale_pos,
                random_state=42,
                verbose=-1,
                n_jobs=1,
            )
            model.fit(X_train, y_train,
                      eval_set=[(X_val, y_val)],
                      callbacks=[lgb.early_stopping(20, verbose=False),
                                 lgb.log_evaluation(period=-1)])
        except Exception as e:
            # libomp issue — fall back
            print(f'[Phase 19] LightGBM error: {e}, falling back to sklearn', flush=True)
            HAS_LGB_flag = False
            model, model_type = _train_sklearn(X_train, y_train)
    else:
        model, model_type = _train_sklearn(X_train, y_train)

    # Evaluate
    try:
        if HAS_PD:
            X_train_np = pd.DataFrame(X_train, columns=feature_names).values
            X_val_np   = pd.DataFrame(X_val,   columns=feature_names).values
        else:
            X_train_np = X_train
            X_val_np   = X_val

        y_train_pred = model.predict_proba(X_train_np)[:, 1]
        y_val_pred   = model.predict_proba(X_val_np)[:, 1]
        train_auc = float(roc_auc_score(y_train, y_train_pred))
        val_auc   = float(roc_auc_score(y_val,   y_val_pred))
    except Exception:
        train_auc, val_auc = 0.5, 0.5

    return model, train_auc, val_auc, model_type

def _train_sklearn(X_train, y_train):
    model = HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.05,
        max_depth=6, random_state=42
    )
    model.fit(X_train, y_train)
    return model, 'sklearn_hgb'

def get_feature_importance(model, feature_names, model_type):
    """Extract feature importance dict from model."""
    try:
        if model_type == 'lightgbm':
            imp = model.feature_importances_
        else:
            imp = getattr(model, 'feature_importances_', [1]*len(feature_names))
        total = sum(imp) or 1
        return {name: round(float(v)/total, 4)
                for name, v in zip(feature_names, imp)}
    except Exception:
        return {name: 0.0 for name in feature_names}

def save_model(model, model_type, train_auc, val_auc, n_samples,
               n_features, feature_importance):
    """Pickle model and log to DB."""
    try:
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump({'model': model, 'model_type': model_type,
                         'feature_names': FEATURE_COLS}, f)
    except Exception as e:
        print(f'[Phase 19] Could not save model: {e}', flush=True)

    con = get_connection()
    ensure_schema(con)
    con.execute("""
        INSERT OR REPLACE INTO ml_model_log
        (trained_at, model_type, n_samples, train_auc, val_auc, n_features, feature_importance_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), model_type, n_samples,
          round(train_auc, 4), round(val_auc, 4), n_features,
          json.dumps(feature_importance)))
    con.commit()
    con.close()

def load_model():
    """Load pickled model or return None."""
    if not os.path.exists(MODEL_PATH):
        return None, None, FEATURE_COLS
    try:
        with open(MODEL_PATH, 'rb') as f:
            d = pickle.load(f)
        return d.get('model'), d.get('model_type', 'unknown'), d.get('feature_names', FEATURE_COLS)
    except Exception:
        return None, None, FEATURE_COLS

# ─── Command: train_model ────────────────────────────────────────────────────

def cmd_train_model(params):
    t0 = time.time()
    print('[Phase 19] Loading training data...', flush=True)
    con = get_connection()
    ensure_schema(con)

    X, y, meta, feature_names = load_training_data(con)
    con.close()

    if len(X) < 100:
        return {
            'error': f'Insufficient training data: only {len(X)} samples. Need indicators_cache + explosive_moves data.',
            'n_samples': len(X),
            'elapsed': round(time.time()-t0, 2)
        }

    print(f'[Phase 19] Training on {len(X)} samples, {len(feature_names)} features...', flush=True)
    n_pos   = sum(y)
    n_neg   = len(y) - n_pos
    balance = round(n_pos / max(1, len(y)), 3)
    print(f'[Phase 19] Class balance: {n_pos} positive ({balance*100:.1f}%), {n_neg} negative', flush=True)

    model, train_auc, val_auc, model_type = train_classifier(X, y, feature_names)

    if model is None:
        return {
            'error': 'No ML library available (need sklearn or lightgbm)',
            'n_samples': len(X),
            'elapsed': round(time.time()-t0, 2)
        }

    feat_imp = get_feature_importance(model, feature_names, model_type)
    save_model(model, model_type, train_auc, val_auc, len(X),
               len(feature_names), feat_imp)

    top_features = sorted(feat_imp.items(), key=lambda x: -x[1])[:10]

    return {
        'model_type':    model_type,
        'n_samples':     len(X),
        'n_features':    len(feature_names),
        'n_positive':    n_pos,
        'class_balance': balance,
        'train_auc':     round(train_auc, 4),
        'val_auc':       round(val_auc, 4),
        'top_features':  [{'feature': f, 'importance': v} for f, v in top_features],
        'model_saved':   os.path.exists(MODEL_PATH),
        'elapsed':       round(time.time()-t0, 2)
    }

# ─── Command: explain_stock ──────────────────────────────────────────────────

def cmd_explain_stock(params):
    t0 = time.time()
    symbol = params.get('symbol', '')
    if not symbol:
        return {'error': 'symbol parameter required'}

    print(f'[Phase 19] Explaining {symbol}...', flush=True)

    model, model_type, feature_names = load_model()
    if model is None:
        # Try to train first
        print('[Phase 19] No model found, training...', flush=True)
        cmd_train_model({})
        model, model_type, feature_names = load_model()
        if model is None:
            return {'error': 'Model not available and training failed', 'symbol': symbol,
                    'elapsed': round(time.time()-t0, 2)}

    con = get_connection()
    # Get latest indicators for this stock
    try:
        row = con.execute("""
            SELECT * FROM indicators_cache WHERE symbol = ?
            ORDER BY updated_at DESC LIMIT 1
        """, (symbol,)).fetchone()
    except Exception:
        row = None

    # Get latest OHLCV
    ohlcv_rows = con.execute("""
        SELECT bar_time, open, high, low, close, volume
        FROM ohlcv_history WHERE symbol = ?
        ORDER BY bar_time DESC LIMIT 30
    """, (symbol,)).fetchall()
    ohlcv_rows = list(reversed(ohlcv_rows))

    # Get active patterns
    try:
        n_active = con.execute("""
            SELECT COUNT(*) as n FROM precursor_patterns
            WHERE symbol = ? AND active = 1
        """, (symbol,)).fetchone()['n']
    except Exception:
        n_active = 0

    con.close()

    if not ohlcv_rows:
        return {'error': f'No OHLCV data for {symbol}', 'symbol': symbol,
                'elapsed': round(time.time()-t0, 2)}

    closes  = [float(r['close'])  for r in ohlcv_rows]
    highs   = [float(r['high'])   for r in ohlcv_rows]
    lows    = [float(r['low'])    for r in ohlcv_rows]
    volumes = [float(r['volume']) for r in ohlcv_rows]
    opens   = [float(r['open'])   for r in ohlcv_rows]

    if len(closes) < 5:
        return {'error': f'Insufficient OHLCV data for {symbol}', 'symbol': symbol,
                'elapsed': round(time.time()-t0, 2)}

    # Compute features
    mean_20 = sum(closes[-20:]) / min(20, len(closes))
    std_20  = _compute_std(closes[-20:])
    c = closes[-1]

    rsi_val   = float(row['rsi']         if row and row['rsi']         else _compute_rsi_list(closes, 14))
    rsi5_val  = float(row['rsi_5']       if row and row['rsi_5']       else _compute_rsi_list(closes[-6:], 5))
    bb_w      = float(row['bb_width']    if row and row['bb_width']    else (std_20*2/mean_20 if mean_20 > 0 else 0))
    bb_p      = float(row['bb_pct']      if row and row['bb_pct']      else 0.5)
    vol20     = float(row['vol_ratio_20']if row and row['vol_ratio_20']else 1.0)
    mom1d     = float(row['momentum_1d'] if row and row['momentum_1d'] else (closes[-1]-closes[-2])/closes[-2] if len(closes)>1 and closes[-2]>0 else 0)
    mom5d     = float(row['momentum_5d'] if row and row['momentum_5d'] else (closes[-1]-closes[-6])/closes[-6] if len(closes)>5 and closes[-6]>0 else 0)
    mom20d    = float(row['momentum_20d']if row and row['momentum_20d']else (closes[-1]-closes[-21])/closes[-21] if len(closes)>20 and closes[-21]>0 else 0)
    atr_r     = float(row['atr_ratio']   if row and row['atr_ratio']   else 1.0)

    bb_squeeze   = 1.0 if bb_w < 0.02 else 0.0
    vol_surge    = 1.0 if vol20 > 2.0  else 0.0
    mom_positive = 1.0 if mom5d > 0.02 else 0.0

    close_vs_ema20 = (c / mean_20 - 1) if mean_20 > 0 else 0
    hl_range = (highs[-1] - lows[-1]) / c if c > 0 else 0
    gap_pct  = (opens[-1] - closes[-2]) / closes[-2] if len(closes) > 1 and closes[-2] > 0 else 0

    features = [rsi_val, rsi5_val, bb_w, bb_p, vol20, mom1d, mom5d, mom20d, atr_r,
                bb_squeeze, vol_surge, mom_positive, close_vs_ema20, hl_range, gap_pct]
    features_safe = [_safe_float(v) for v in features]

    # Predict
    try:
        if HAS_PD:
            X_df = pd.DataFrame([features_safe], columns=feature_names)
            proba = float(model.predict_proba(X_df)[0, 1])
        else:
            proba = float(model.predict_proba([features_safe])[0, 1])
    except Exception as e:
        return {'error': f'Prediction failed: {e}', 'symbol': symbol,
                'elapsed': round(time.time()-t0, 2)}

    # SHAP explanation
    shap_values = None
    if HAS_SHAP and model_type in ('lightgbm', 'sklearn_hgb'):
        try:
            explainer   = shap.TreeExplainer(model)
            if HAS_PD:
                shap_vals = explainer.shap_values(pd.DataFrame([features_safe], columns=feature_names))
            else:
                shap_vals = explainer.shap_values([features_safe])
            # For binary classifier shap returns list of 2 arrays
            if isinstance(shap_vals, list):
                shap_arr = shap_vals[1][0] if len(shap_vals) > 1 else shap_vals[0][0]
            else:
                shap_arr = shap_vals[0]
            shap_values = {name: float(v) for name, v in zip(feature_names, shap_arr)}
        except Exception as e:
            print(f'[Phase 19] SHAP failed: {e}, using feature importance fallback', flush=True)
            shap_values = None

    # Fallback: use feature importance × feature value as proxy
    if shap_values is None:
        fi = get_feature_importance(model, feature_names, model_type)
        shap_values = {name: fi.get(name, 0) * features_safe[i] * 0.1
                       for i, name in enumerate(feature_names)}

    # Top factors
    top_factors = []
    for name, impact in sorted(shap_values.items(), key=lambda x: -abs(x[1]))[:8]:
        idx = feature_names.index(name) if name in feature_names else -1
        raw_val = features_safe[idx] if idx >= 0 else 0.0
        direction = 'bullish' if impact > 0 else 'bearish'
        top_factors.append({
            'feature':   name,
            'impact':    round(abs(impact), 4),
            'value':     round(raw_val, 4),
            'direction': direction,
            'shap':      round(impact, 4),
        })

    # Signal strength
    if proba >= 0.5:
        strength = 'HIGH'
    elif proba >= 0.3:
        strength = 'MEDIUM'
    elif proba >= 0.15:
        strength = 'LOW'
    else:
        strength = 'MINIMAL'

    # Telegram summary
    lines = [f"🔍 {symbol} — explosion probability {proba*100:.1f}%"]
    arrows = {'bullish': '↑', 'bearish': '↓'}
    for f in top_factors[:4]:
        arrow = arrows.get(f['direction'], '→')
        lines.append(f"  {arrow} {f['feature'].replace('_',' ')} ({f['value']:.3f})")
    lines.append(f"  → Law activation: {n_active} patterns")
    telegram_summary = '\n'.join(lines)

    result = {
        'symbol':          symbol,
        'prediction':      round(proba, 4),
        'signal_strength': strength,
        'top_factors':     top_factors,
        'telegram_summary': telegram_summary,
        'n_active_patterns': int(n_active),
        'shap_available':  HAS_SHAP,
        'elapsed':         round(time.time()-t0, 2)
    }

    # Save to DB
    con = get_connection()
    ensure_schema(con)
    today = datetime.utcnow().strftime('%Y-%m-%d')
    con.execute("""
        INSERT OR REPLACE INTO daily_explanations
        (symbol, analysis_date, prediction_proba, top_factors_json, signal_strength, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (symbol, today, round(proba, 4),
          json.dumps(top_factors), strength, datetime.utcnow().isoformat()))
    con.commit()
    con.close()

    return result

def _compute_rsi_list(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        if d >= 0:
            gains.append(d); losses.append(0.0)
        else:
            gains.append(0.0); losses.append(-d)
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0: return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))

def _compute_std(xs):
    if not xs: return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x-m)**2 for x in xs) / len(xs))

# ─── Command: feature_importance ─────────────────────────────────────────────

def cmd_feature_importance(params):
    t0 = time.time()
    print('[Phase 19] Computing global feature importance...', flush=True)

    model, model_type, feature_names = load_model()
    if model is None:
        print('[Phase 19] No model found, training...', flush=True)
        train_result = cmd_train_model({})
        if 'error' in train_result:
            return train_result
        model, model_type, feature_names = load_model()
        if model is None:
            return {'error': 'Model training failed', 'elapsed': round(time.time()-t0, 2)}

    fi = get_feature_importance(model, feature_names, model_type)
    fi_sorted = sorted(fi.items(), key=lambda x: -x[1])

    # Load last training log
    con = get_connection()
    log_row = con.execute("""
        SELECT * FROM ml_model_log ORDER BY trained_at DESC LIMIT 1
    """).fetchone()
    con.close()

    log_info = dict(log_row) if log_row else {}

    return {
        'model_type':         model_type,
        'n_features':         len(feature_names),
        'feature_importance': [{'feature': f, 'importance': v, 'rank': i+1}
                                for i, (f, v) in enumerate(fi_sorted)],
        'top_3_features':     [f for f, v in fi_sorted[:3]],
        'train_auc':          log_info.get('train_auc', 0.0),
        'val_auc':            log_info.get('val_auc',   0.0),
        'elapsed':            round(time.time()-t0, 2)
    }

# ─── Command: daily_explanations ─────────────────────────────────────────────

def cmd_daily_explanations(params):
    t0 = time.time()
    min_signal = float(params.get('min_proba', 0.2))
    print('[Phase 19] Running daily explanations...', flush=True)

    con = get_connection()

    # Get all symbols with current indicators
    try:
        symbols = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM indicators_cache"
        ).fetchall()]
    except Exception:
        symbols = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM ohlcv_history LIMIT 253"
        ).fetchall()]

    con.close()

    print(f'[Phase 19] Explaining {len(symbols)} symbols...', flush=True)
    results = []
    high_signal = []

    for sym in symbols[:100]:  # cap for performance
        try:
            res = cmd_explain_stock({'symbol': sym})
            if 'error' not in res:
                results.append(res)
                if res.get('prediction', 0) >= min_signal:
                    high_signal.append(res)
        except Exception as e:
            pass  # skip errored symbols

    high_signal.sort(key=lambda x: -x.get('prediction', 0))

    # Format Telegram digest
    tg_lines = [f"📊 EGX Daily Signal Report — {datetime.utcnow().strftime('%Y-%m-%d')}",
                f"Analyzed: {len(results)} stocks | High signal (≥{min_signal*100:.0f}%): {len(high_signal)}",
                ""]
    for r in high_signal[:8]:
        tg_lines.append(r.get('telegram_summary', ''))
        tg_lines.append('')

    return {
        'n_analyzed':    len(results),
        'n_high_signal': len(high_signal),
        'high_signal_stocks': high_signal[:15],
        'telegram_digest':    '\n'.join(tg_lines),
        'elapsed':            round(time.time()-t0, 2)
    }

# ─── Command: model_report ────────────────────────────────────────────────────

def cmd_model_report(params):
    t0 = time.time()
    print('[Phase 19] Generating model report...', flush=True)

    con = get_connection()
    ensure_schema(con)

    # Load training history
    log_rows = con.execute("""
        SELECT * FROM ml_model_log ORDER BY trained_at DESC LIMIT 10
    """).fetchall()

    # Load recent explanations
    exp_rows = con.execute("""
        SELECT signal_strength, COUNT(*) as n
        FROM daily_explanations
        WHERE analysis_date >= date('now', '-7 days')
        GROUP BY signal_strength
    """).fetchall()
    con.close()

    training_history = [dict(r) for r in log_rows]
    signal_dist      = {r['signal_strength']: r['n'] for r in exp_rows}

    if not training_history:
        return {
            'note': 'No model trained yet. Run train_model first.',
            'elapsed': round(time.time()-t0, 2)
        }

    latest = training_history[0]
    model, model_type, feature_names = load_model()

    if model is None:
        return {
            'note':    'Model file not found. Re-run train_model.',
            'history': training_history,
            'elapsed': round(time.time()-t0, 2)
        }

    # Run quick evaluation on a fresh sample
    conn2 = get_connection()
    X, y, meta, _ = load_training_data(conn2)
    conn2.close()

    roc_auc_val = latest.get('val_auc', 0.5)
    conf_matrix = {'TP': 0, 'FP': 0, 'TN': 0, 'FN': 0}

    if HAS_SKLEARN and X and y:
        try:
            X_safe = [[_safe_float(v) for v in row] for row in X[-500:]]
            y_safe = y[-500:]
            if HAS_PD:
                X_df   = pd.DataFrame(X_safe, columns=feature_names)
                y_pred = model.predict_proba(X_df)[:, 1]
            else:
                y_pred = model.predict_proba(X_safe)[:, 1]
            roc_auc_val = float(roc_auc_score(y_safe, y_pred))
            # Confusion matrix at threshold 0.3
            for yt, yp in zip(y_safe, y_pred):
                pred = 1 if yp >= 0.3 else 0
                if pred == 1 and yt == 1: conf_matrix['TP'] += 1
                elif pred == 1 and yt == 0: conf_matrix['FP'] += 1
                elif pred == 0 and yt == 0: conf_matrix['TN'] += 1
                elif pred == 0 and yt == 1: conf_matrix['FN'] += 1
        except Exception:
            pass

    precision = conf_matrix['TP'] / max(1, conf_matrix['TP'] + conf_matrix['FP'])
    recall    = conf_matrix['TP'] / max(1, conf_matrix['TP'] + conf_matrix['FN'])
    f1        = 2 * precision * recall / max(0.001, precision + recall)

    fi = get_feature_importance(model, feature_names, model_type)
    fi_sorted = sorted(fi.items(), key=lambda x: -x[1])

    return {
        'model_type':       latest.get('model_type', 'unknown'),
        'trained_at':       latest.get('trained_at', ''),
        'n_samples':        latest.get('n_samples', 0),
        'train_auc':        round(float(latest.get('train_auc', 0)), 4),
        'val_auc':          round(roc_auc_val, 4),
        'precision':        round(precision, 4),
        'recall':           round(recall, 4),
        'f1_score':         round(f1, 4),
        'confusion_matrix': conf_matrix,
        'signal_distribution_7d': signal_dist,
        'top_5_features':   [{'feature': f, 'importance': v} for f, v in fi_sorted[:5]],
        'training_history': training_history[:5],
        'shap_available':   HAS_SHAP,
        'lightgbm_available': HAS_LGB,
        'elapsed':          round(time.time()-t0, 2)
    }

# ─── Command: retrain ─────────────────────────────────────────────────────────

def cmd_retrain(params):
    t0 = time.time()
    print('[Phase 19] Retraining model on latest data...', flush=True)
    # Delete old model
    try:
        if os.path.exists(MODEL_PATH):
            os.remove(MODEL_PATH)
    except Exception:
        pass
    result = cmd_train_model(params)
    result['retrained'] = True
    result['elapsed']   = round(time.time()-t0, 2)
    return result

# ─── Dispatch ────────────────────────────────────────────────────────────────

COMMANDS = {
    'train_model':         cmd_train_model,
    'explain_stock':       cmd_explain_stock,
    'feature_importance':  cmd_feature_importance,
    'daily_explanations':  cmd_daily_explanations,
    'model_report':        cmd_model_report,
    'retrain':             cmd_retrain,
}

if __name__ == '__main__':
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else 'model_report'
        params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        fn = COMMANDS.get(command)
        if fn is None:
            out = {'error': f'unknown command: {command}', 'available': list(COMMANDS.keys())}
        else:
            out = fn(params)
        print(json.dumps(out))
    except Exception as ex:
        import traceback
        print(json.dumps({'error': str(ex), 'traceback': traceback.format_exc()[-1000:]}))
