#!/usr/bin/env python3
"""
Phase 77 — tsfresh Automated Feature Extraction
"استخراج المميزات التلقائي — من 13 ميزة يدوية إلى 300+ ميزة إحصائية"

tsfresh تستخرج من OHLCV series:
  - entropy (Shannon, Sample, Approximate)
  - autocorrelation at lags 1-10
  - FFT coefficients
  - energy / mean abs change
  - peak counts
  - skewness / kurtosis
  - nonlinear dynamics (c3, cid_ce)
  - wavelet energy

Commands:
  extract_symbols   — Extract tsfresh features for list of symbols
  extract_explosions — Extract features for all explosive_moves events
  select_features   — SHAP/statistical feature selection from tsfresh set
  compare_importance — Compare tsfresh vs manual features for explosion ML
  report            — Full extraction + selection report
"""
import sys, json, sqlite3, datetime, math
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
TSFEATURES_PATH = Path(__file__).parent / 'models' / 'tsfresh_selected_features.json'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_ohlcv_window(conn, symbol, end_date, lookback_bars=20):
    """Load the last N bars of OHLCV for a symbol before end_date."""
    rows = conn.execute("""
        SELECT date(bar_time,'unixepoch') AS bar_date,
               open, high, low, close, volume
        FROM ohlcv_history
        WHERE symbol=?
          AND date(bar_time,'unixepoch') < ?
        ORDER BY bar_time DESC
        LIMIT ?
    """, (symbol, end_date, lookback_bars)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def cmd_extract_symbols(params):
    """Extract tsfresh features for today's top ML predictions.

    params:
      symbols   : list (optional) — specific symbols to extract
      min_prob  : float (default 0.70) — use top ML predictions
      lookback  : int (default 20) — bars of history per symbol
      max_syms  : int (default 30) — limit symbols
    """
    try:
        import tsfresh
        from tsfresh.feature_extraction import extract_features, MinimalFCParameters, EfficientFCParameters
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'tsfresh not installed: pip install tsfresh'}

    lookback = int(params.get('lookback', 20))
    max_syms = int(params.get('max_syms', 30))
    min_prob = float(params.get('min_prob', 0.70))
    symbols  = params.get('symbols', [])

    conn = get_db()

    if not symbols:
        rows = conn.execute("""
            SELECT symbol FROM explosion_predictions
            WHERE explosion_prob >= ?
              AND pred_date = (SELECT MAX(pred_date) FROM explosion_predictions)
            ORDER BY explosion_prob DESC
            LIMIT ?
        """, (min_prob, max_syms)).fetchall()
        symbols = [r['symbol'] for r in rows]

    if not symbols:
        conn.close()
        return {'success': False, 'error': 'No symbols to extract features for'}

    today = datetime.date.today().isoformat()
    all_rows = []
    sym_idx  = 0

    for sym in symbols[:max_syms]:
        bars = _load_ohlcv_window(conn, sym, today, lookback)
        if len(bars) < 10:
            continue
        for i, bar in enumerate(bars):
            all_rows.append({
                'id':     sym_idx,
                'time':   i,
                'close':  float(bar['close'] or 0),
                'volume': float(bar['volume'] or 0),
                'high':   float(bar['high'] or 0),
                'low':    float(bar['low'] or 0),
                'hl_range': float(bar['high'] or 0) - float(bar['low'] or 0),
            })
        sym_idx += 1

    conn.close()

    if not all_rows:
        return {'success': False, 'error': 'No OHLCV data for selected symbols'}

    df = pd.DataFrame(all_rows)

    # Use MinimalFCParameters for speed (30 features vs 700+)
    features = extract_features(
        df,
        column_id='id',
        column_sort='time',
        column_value='close',
        default_fc_parameters=MinimalFCParameters(),
        disable_progressbar=True,
        n_jobs=1,
    )

    # Map id back to symbol
    id_to_sym = {i: s for i, s in enumerate(symbols[:max_syms])}
    features.index = features.index.map(lambda x: id_to_sym.get(x, str(x)))
    features = features.dropna(axis=1, how='all')

    # Top features by variance
    variances = features.var().sort_values(ascending=False)
    top_feats = variances.head(15).index.tolist()

    result_rows = []
    for sym in features.index:
        row = {'symbol': sym}
        for f in top_feats:
            v = features.loc[sym, f]
            row[f] = round(float(v), 4) if not (math.isnan(v) or math.isinf(v)) else None
        result_rows.append(row)

    return {
        'success':           True,
        'n_symbols':         len(result_rows),
        'n_features_total':  len(features.columns),
        'n_features_top':    len(top_feats),
        'top_feature_names': top_feats,
        'symbol_features':   result_rows[:20],
    }


def cmd_extract_explosions(params):
    """Extract tsfresh features for explosive_moves events (for ML training).

    Builds a feature matrix: each row = one explosive move,
    columns = tsfresh statistical features from the 20 bars before explosion.

    params:
      lookback    : int (default 20) — bars before explosion
      max_events  : int (default 500) — limit for speed
      save_model  : bool (default True) — save selected features list
    """
    try:
        from tsfresh.feature_extraction import extract_features, MinimalFCParameters
        from tsfresh.feature_selection import select_features as ts_select
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'tsfresh not installed'}

    lookback   = int(params.get('lookback', 20))
    max_events = int(params.get('max_events', 500))
    save_model = bool(params.get('save_model', True))

    conn = get_db()

    # Sample positives
    pos_rows = conn.execute("""
        SELECT symbol, explosion_date
        FROM explosive_moves
        WHERE explosion_date BETWEEN '2021-01-01' AND '2025-12-31'
        ORDER BY RANDOM()
        LIMIT ?
    """, (max_events // 2,)).fetchall()

    # Sample negatives (low-change dates)
    neg_rows = conn.execute("""
        SELECT symbol, explosion_date AS explosion_date
        FROM explosive_moves
        WHERE ABS(return_3d) < 1.0
          AND explosion_date BETWEEN '2021-01-01' AND '2025-12-31'
        ORDER BY RANDOM()
        LIMIT ?
    """, (max_events // 2,)).fetchall()

    all_events = [(r['symbol'], r['explosion_date'], 1) for r in pos_rows] + \
                 [(r['symbol'], r['explosion_date'], 0) for r in neg_rows]

    if len(all_events) < 20:
        conn.close()
        return {'success': False, 'error': 'Not enough events for tsfresh extraction'}

    print(f"[tsfresh] Extracting features for {len(all_events)} events...", flush=True)

    rows_for_ts = []
    labels      = {}
    event_idx   = 0

    for sym, exp_date, label in all_events:
        bars = _load_ohlcv_window(conn, sym, exp_date, lookback)
        if len(bars) < 10:
            continue
        for i, bar in enumerate(bars):
            rows_for_ts.append({
                'id':     event_idx,
                'time':   i,
                'close':  float(bar['close'] or 0),
                'volume': float(bar['volume'] or 0),
                'hl_range': float(bar['high'] or 0) - float(bar['low'] or 0),
            })
        labels[event_idx] = label
        event_idx += 1

    conn.close()

    if not rows_for_ts:
        return {'success': False, 'error': 'No OHLCV bars loaded'}

    df = pd.DataFrame(rows_for_ts)
    y  = pd.Series(labels)

    print(f"[tsfresh] Running feature extraction on {event_idx} events...", flush=True)

    features = extract_features(
        df,
        column_id='id',
        column_sort='time',
        column_value='close',
        default_fc_parameters=MinimalFCParameters(),
        disable_progressbar=True,
        n_jobs=2,
    )

    features = features.dropna(axis=1, thresh=int(len(features) * 0.7))
    features = features.fillna(0)

    print(f"[tsfresh] Extracted {len(features.columns)} features, running selection...", flush=True)

    # Feature selection using mutual information
    try:
        y_aligned = y[features.index]
        selected  = ts_select(features, y_aligned, ml_task='classification')
        selected_names = list(selected.columns)
    except Exception as e:
        print(f"[tsfresh] Selection failed: {e}, using top variance features", flush=True)
        variances = features.var().sort_values(ascending=False)
        selected_names = variances.head(20).index.tolist()

    print(f"[tsfresh] Selected {len(selected_names)} features", flush=True)

    if save_model:
        TSFEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TSFEATURES_PATH, 'w') as fh:
            json.dump({
                'selected_features': selected_names,
                'n_total':           len(features.columns),
                'n_selected':        len(selected_names),
                'n_events':          event_idx,
                'extracted_at':      datetime.datetime.now().isoformat(),
            }, fh, indent=2)

    return {
        'success':           True,
        'n_events':          event_idx,
        'n_features_total':  len(features.columns),
        'n_features_selected': len(selected_names),
        'selected_features': selected_names[:20],
        'saved_to':          str(TSFEATURES_PATH) if save_model else None,
    }


def cmd_compare_importance(params):
    """Compare tsfresh features vs manual features by mutual information with explosion label."""
    try:
        import pandas as pd
        import numpy as np
        from sklearn.feature_selection import mutual_info_classif
    except ImportError:
        return {'success': False, 'error': 'scikit-learn not installed'}

    conn = get_db()

    rows = conn.execute("""
        SELECT
            pre1_rsi, pre3_rsi, pre5_rsi,
            pre1_bb_width, pre3_bb_width, pre5_bb_width,
            pre1_vol_ratio, pre3_vol_ratio, pre5_vol_ratio,
            pre5_momentum_5d, pre5_bb_position, pre5_compression_days,
            return_3d
        FROM explosive_moves
        WHERE pre1_rsi IS NOT NULL
          AND return_3d IS NOT NULL
        LIMIT 5000
    """).fetchall()
    conn.close()

    df = pd.DataFrame([dict(r) for r in rows])
    df = df.fillna(0)

    y = (df['return_3d'].abs() >= 3).astype(int)

    feature_cols = [
        'pre1_rsi', 'pre3_rsi', 'pre5_rsi',
        'pre1_bb_width', 'pre3_bb_width', 'pre5_bb_width',
        'pre1_vol_ratio', 'pre3_vol_ratio', 'pre5_vol_ratio',
        'pre5_momentum_5d', 'pre5_bb_position', 'pre5_compression_days',
    ]
    X = df[feature_cols].values

    mi = mutual_info_classif(X, y, random_state=42)
    results = sorted(
        [{'feature': f, 'mutual_info': round(float(m), 4)}
         for f, m in zip(feature_cols, mi)],
        key=lambda x: -x['mutual_info']
    )

    return {
        'success':  True,
        'n_events': len(df),
        'feature_importance': results,
        'top_3': [r['feature'] for r in results[:3]],
    }


def cmd_report(params):
    comp = cmd_compare_importance({})
    extr = cmd_extract_symbols({'max_syms': 10, 'min_prob': 0.65})
    return {
        'success':              True,
        'manual_features':      comp,
        'tsfresh_extraction':   extr,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ph77 Daily Store — Nightly incremental computation for Ph55 integration
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_tsfresh_daily_table(conn):
    """Create tsfresh_daily table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tsfresh_daily (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT    NOT NULL,
            trade_date   TEXT    NOT NULL,
            feat_mean    REAL,
            feat_std     REAL,
            feat_median  REAL,
            feat_energy  REAL,
            feat_entropy REAL,
            feat_autocorr1 REAL,
            feat_skew    REAL,
            feat_kurtosis REAL,
            vol_mean     REAL,
            vol_std      REAL,
            computed_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(symbol, trade_date)
        )
    """)
    conn.commit()


def cmd_daily_store(params):
    """
    Ph77 Daily Store — compute tsfresh features for ALL symbols for today (or a given date)
    and store in tsfresh_daily table.

    Called nightly from night_lab.py Step 19h.

    params:
      date      : str (ISO, default today) — trade_date to compute for
      lookback  : int (default 20)         — bars of history per symbol
      overwrite : bool (default False)     — recompute if already stored

    Returns:
      {'success': True, 'n_stored': N, 'n_skipped': M, 'trade_date': ..., 'duration_sec': ...}
    """
    import time as _time

    try:
        from tsfresh.feature_extraction import extract_features, MinimalFCParameters
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'tsfresh not installed: pip install tsfresh'}

    lookback  = int(params.get('lookback', 20))
    overwrite = bool(params.get('overwrite', False))
    target_date = str(params.get('date', datetime.date.today().isoformat()))

    t0   = _time.time()
    conn = get_db()
    _ensure_tsfresh_daily_table(conn)

    # Get all active symbols from the universe
    syms = [r['symbol'] for r in conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history WHERE close > 0 ORDER BY symbol"
    ).fetchall()]

    if not syms:
        conn.close()
        return {'success': False, 'error': 'No symbols in ohlcv_history'}

    # Skip if already stored for this date (unless overwrite)
    if not overwrite:
        already = {r['symbol'] for r in conn.execute(
            "SELECT symbol FROM tsfresh_daily WHERE trade_date=?", (target_date,)
        ).fetchall()}
    else:
        already = set()
        conn.execute("DELETE FROM tsfresh_daily WHERE trade_date=?", (target_date,))
        conn.commit()

    n_stored = 0
    n_skipped = 0
    n_missing = 0

    for sym in syms:
        if sym in already:
            n_skipped += 1
            continue

        # Load last N bars BEFORE or ON target_date
        rows = conn.execute("""
            SELECT date(bar_time,'unixepoch') AS d,
                   close, high, low, volume
            FROM ohlcv_history
            WHERE symbol=?
              AND date(bar_time,'unixepoch') <= ?
              AND close > 0
            ORDER BY bar_time DESC
            LIMIT ?
        """, (sym, target_date, lookback)).fetchall()

        if len(rows) < 10:
            n_missing += 1
            continue

        bars = list(reversed(rows))
        closes  = [float(r['close'])  for r in bars]
        volumes = [float(r['volume'] or 0) for r in bars]

        # Compute statistical features manually (faster than full tsfresh for 10 features)
        arr = np.array(closes)
        vol_arr = np.array(volumes)

        # close-based features
        feat_mean    = float(np.mean(arr))
        feat_std     = float(np.std(arr))   if len(arr) > 1 else 0.0
        feat_median  = float(np.median(arr))
        feat_energy  = float(np.sum(arr ** 2))
        # Autocorrelation at lag 1 (momentum persistence, bounded -1..1)
        feat_autocorr1 = float(np.corrcoef(arr[:-1], arr[1:])[0, 1]) if len(arr) > 2 else 0.0
        feat_skew    = float(pd.Series(arr).skew()) if len(arr) > 2 else 0.0
        feat_kurtosis= float(pd.Series(arr).kurtosis()) if len(arr) > 2 else 0.0
        # Entropy = CV of |changes| (stable: std_of_changes / mean_of_|changes|)
        # Bounded ~0-10: low = consistent trend, high = erratic/choppy market
        changes = np.diff(arr)
        mean_abs_change = float(np.mean(np.abs(changes))) + 1e-10
        feat_entropy = float(np.std(changes) / mean_abs_change) \
                       if len(changes) > 1 else 0.0
        # volume-based features
        vol_mean = float(np.mean(vol_arr))
        vol_std  = float(np.std(vol_arr)) if len(vol_arr) > 1 else 0.0

        # Store
        conn.execute("""
            INSERT OR REPLACE INTO tsfresh_daily
              (symbol, trade_date, feat_mean, feat_std, feat_median, feat_energy,
               feat_entropy, feat_autocorr1, feat_skew, feat_kurtosis,
               vol_mean, vol_std)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (sym, target_date,
              round(feat_mean, 6), round(feat_std, 6), round(feat_median, 6),
              round(feat_energy, 2), round(feat_entropy, 6), round(feat_autocorr1, 6),
              round(feat_skew, 6), round(feat_kurtosis, 6),
              round(vol_mean, 2), round(vol_std, 2)))
        n_stored += 1

    conn.commit()
    conn.close()

    dur = round(_time.time() - t0, 1)
    return {
        'success':    True,
        'trade_date': target_date,
        'n_symbols':  len(syms),
        'n_stored':   n_stored,
        'n_skipped':  n_skipped,
        'n_missing':  n_missing,
        'duration_sec': dur,
    }


def cmd_backfill_history(params):
    """
    One-time backfill: compute tsfresh_daily for all historical dates.
    Processes dates in reverse order (newest first).

    params:
      start_date : str (default '2025-01-01')
      end_date   : str (default today)
      overwrite  : bool (default False)
    """
    import time as _time

    start_date = str(params.get('start_date', '2025-01-01'))
    end_date   = str(params.get('end_date', datetime.date.today().isoformat()))
    overwrite  = bool(params.get('overwrite', False))

    conn = get_db()
    _ensure_tsfresh_daily_table(conn)

    # Get all distinct trading dates in range
    dates = [r[0] for r in conn.execute("""
        SELECT DISTINCT date(bar_time,'unixepoch') AS d
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') BETWEEN ? AND ?
          AND close > 0
        ORDER BY d DESC
    """, (start_date, end_date)).fetchall()]
    conn.close()

    if not dates:
        return {'success': False, 'error': 'No dates found in range'}

    total_stored = 0
    total_skipped = 0
    t0 = _time.time()

    for i, d in enumerate(dates):
        result = cmd_daily_store({'date': d, 'overwrite': overwrite})
        total_stored  += result.get('n_stored', 0)
        total_skipped += result.get('n_skipped', 0)
        if (i + 1) % 20 == 0:
            elapsed = _time.time() - t0
            remaining = elapsed / (i + 1) * (len(dates) - i - 1)
            print(json.dumps({
                'progress': f'{i+1}/{len(dates)}',
                'date': d,
                'stored_so_far': total_stored,
                'eta_min': round(remaining / 60, 1),
            }), flush=True)

    return {
        'success':       True,
        'dates_processed': len(dates),
        'total_stored':  total_stored,
        'total_skipped': total_skipped,
        'duration_sec':  round(_time.time() - t0, 1),
    }


COMMANDS = {
    'extract_symbols':     cmd_extract_symbols,
    'extract_explosions':  cmd_extract_explosions,
    'compare_importance':  cmd_compare_importance,
    'report':              cmd_report,
    'daily_store':         cmd_daily_store,
    'backfill_history':    cmd_backfill_history,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'report'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
