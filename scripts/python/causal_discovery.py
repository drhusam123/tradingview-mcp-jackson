#!/usr/bin/env python3
"""
Phase 78 — Causal Discovery
"اكتشاف العلاقات السببية — هل breadth يسبب الانفجارات أم مجرد correlated؟"

يستخدم:
  1. Granger Causality (statsmodels) — هل X يتنبأ بـ Y بعد إزالة تأثير Y على نفسه؟
  2. Cross-correlation with lag analysis — ما هو الـ lag الأمثل؟
  3. Mutual Information — قياس المعلومات المشتركة الغير خطية

المتغيرات المدروسة:
  - market_breadth → explosive_moves count
  - USD/EGX volume → explosions
  - gold/oil returns → EGX explosions
  - cross_market_daily → sector explosions

Commands:
  granger_test     — Granger causality tests for all market drivers
  lag_analysis     — Optimal lag structure for predictors
  mi_matrix        — Mutual information matrix between drivers + explosions
  report           — Full causal discovery report
"""
import sys, json, sqlite3, datetime, math
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _safe(v, d=0.0):
    try: return float(v) if v is not None and math.isfinite(float(v)) else d
    except: return d


# ─────────────────────────────────────────────────────────────────────────────
# Data Loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_daily_explosions(conn, start_date='2021-01-01'):
    """Daily count of explosive moves."""
    rows = conn.execute("""
        SELECT explosion_date AS date, COUNT(*) AS n_explosions
        FROM explosive_moves
        WHERE explosion_date >= ?
        GROUP BY explosion_date
        ORDER BY explosion_date
    """, (start_date,)).fetchall()
    return {r['date']: r['n_explosions'] for r in rows}


def _load_breadth_series(conn, start_date='2021-01-01'):
    """Market breadth: daily advance ratio."""
    rows = conn.execute("""
        SELECT date, breadth_score, ad_ratio, pct_above_ma20
        FROM market_breadth_daily
        WHERE date >= ?
        ORDER BY date
    """, (start_date,)).fetchall()
    return {r['date']: {
        'breadth_score': _safe(r['breadth_score']),
        'ad_ratio':      _safe(r['ad_ratio']),
        'pct_above_ma20': _safe(r['pct_above_ma20']),
    } for r in rows}


def _build_aligned_series(conn, start_date='2022-01-01'):
    """Build aligned daily DataFrame with all drivers."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return None

    expl = _load_daily_explosions(conn, start_date)
    breadth = _load_breadth_series(conn, start_date)

    # Build from OHLCV for market-level metrics
    raw = conn.execute("""
        SELECT date(bar_time,'unixepoch') AS bar_date,
               AVG(volume) AS avg_vol,
               AVG((close - open) / NULLIF(open, 0)) AS avg_ret
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') >= ?
        GROUP BY bar_date
        ORDER BY bar_date
    """, (start_date,)).fetchall()

    vol_series = {r['bar_date']: _safe(r['avg_vol']) for r in raw}
    ret_series = {r['bar_date']: _safe(r['avg_ret']) for r in raw}

    # Align all series to common dates
    all_dates = sorted(set(expl.keys()) | set(breadth.keys()) | set(vol_series.keys()))

    rows = []
    for d in all_dates:
        n_exp = expl.get(d, 0)
        b     = breadth.get(d, {})
        rows.append({
            'date':           d,
            'n_explosions':   n_exp,
            'breadth_score':  b.get('breadth_score', float('nan')),
            'ad_ratio':       b.get('ad_ratio', float('nan')),
            'avg_volume':     vol_series.get(d, float('nan')),
            'avg_return':     ret_series.get(d, float('nan')),
        })

    df = pd.DataFrame(rows).set_index('date').sort_index()
    df = df.replace([float('inf'), float('-inf')], float('nan'))
    df = df.fillna(method='ffill', limit=5).dropna()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Granger Causality
# ─────────────────────────────────────────────────────────────────────────────

def cmd_granger_test(params):
    """Granger causality: does X Granger-cause explosion count?

    H0: X does NOT Granger-cause Y (explosions)
    p < 0.05 → reject H0 → X provides causal signal for explosions

    params:
      max_lag     : int (default 5)
      start_date  : str (default '2022-01-01')
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'statsmodels not installed'}

    max_lag    = int(params.get('max_lag', 5))
    start_date = params.get('start_date', '2022-01-01')

    conn = get_db()
    df = _build_aligned_series(conn, start_date)
    conn.close()

    if df is None or len(df) < 50:
        return {'success': False, 'error': 'Not enough aligned data for Granger test'}

    y     = df['n_explosions'].values
    tests = ['breadth_score', 'ad_ratio', 'avg_volume', 'avg_return']
    results = []

    for x_col in tests:
        if x_col not in df.columns:
            continue
        x = df[x_col].values
        try:
            data = np.column_stack([y, x])
            gc   = grangercausalitytests(data, maxlag=max_lag, verbose=False)

            best_lag  = min(gc.keys(), key=lambda k: gc[k][0]['ssr_ftest'][1])
            best_p    = gc[best_lag][0]['ssr_ftest'][1]
            best_f    = gc[best_lag][0]['ssr_ftest'][0]

            results.append({
                'driver':    x_col,
                'best_lag':  best_lag,
                'f_stat':    round(float(best_f), 3),
                'p_value':   round(float(best_p), 4),
                'causal':    best_p < 0.05,
                'strength':  'STRONG' if best_p < 0.01 else 'MODERATE' if best_p < 0.05 else 'NONE',
            })
        except Exception as e:
            results.append({'driver': x_col, 'error': str(e)})

    results.sort(key=lambda x: x.get('p_value', 999))
    causal_drivers = [r['driver'] for r in results if r.get('causal')]

    return {
        'success':        True,
        'n_days':         len(df),
        'max_lag':        max_lag,
        'period':         f'{df.index[0]}→{df.index[-1]}',
        'results':        results,
        'causal_drivers': causal_drivers,
        'summary':        f"{len(causal_drivers)}/{len(results)} drivers Granger-cause explosions",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lag Analysis
# ─────────────────────────────────────────────────────────────────────────────

def cmd_lag_analysis(params):
    """Cross-correlation analysis: what lag maximizes correlation with explosions?

    params:
      max_lag    : int (default 10)
      start_date : str (default '2022-01-01')
    """
    try:
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'numpy not installed'}

    max_lag    = int(params.get('max_lag', 10))
    start_date = params.get('start_date', '2022-01-01')

    conn = get_db()
    df = _build_aligned_series(conn, start_date)
    conn.close()

    if df is None or len(df) < 30:
        return {'success': False, 'error': 'Not enough data'}

    y = df['n_explosions'].values
    y_norm = (y - y.mean()) / (y.std() + 1e-8)

    predictors = ['breadth_score', 'ad_ratio', 'avg_volume', 'avg_return']
    results = []

    for col in predictors:
        if col not in df.columns:
            continue
        x = df[col].values
        x_norm = (x - x.mean()) / (x.std() + 1e-8)

        corrs = []
        n = len(x_norm)
        for lag in range(-max_lag, max_lag + 1):
            if lag == 0:
                xcorr = float(np.corrcoef(x_norm, y_norm)[0, 1])
            elif lag > 0:
                xcorr = float(np.corrcoef(x_norm[:-lag], y_norm[lag:])[0, 1])
            else:
                xcorr = float(np.corrcoef(x_norm[-lag:], y_norm[:lag])[0, 1])
            if not math.isnan(xcorr):
                corrs.append({'lag': lag, 'corr': round(xcorr, 3)})

        if not corrs:
            continue
        best = max(corrs, key=lambda c: abs(c['corr']))
        results.append({
            'driver':   col,
            'best_lag': best['lag'],
            'best_corr': best['corr'],
            'interpretation': (
                f"X leads Y by {abs(best['lag'])} days (predictive)"
                if best['lag'] > 0 else
                f"Y leads X by {abs(best['lag'])} days (reactive)"
                if best['lag'] < 0 else
                "Contemporaneous"
            ),
            'top_lags': sorted(corrs, key=lambda c: -abs(c['corr']))[:3],
        })

    results.sort(key=lambda x: -abs(x['best_corr']))

    return {
        'success': True,
        'n_days':  len(df),
        'results': results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Mutual Information Matrix
# ─────────────────────────────────────────────────────────────────────────────

def cmd_mi_matrix(params):
    """Mutual information between all drivers and explosion count."""
    try:
        from sklearn.feature_selection import mutual_info_regression
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'scikit-learn not installed'}

    start_date = params.get('start_date', '2022-01-01')

    conn = get_db()
    df = _build_aligned_series(conn, start_date)
    conn.close()

    if df is None or len(df) < 30:
        return {'success': False, 'error': 'Not enough data'}

    y = df['n_explosions'].values
    predictors = [c for c in df.columns if c != 'n_explosions']
    X = df[predictors].values

    mi = mutual_info_regression(X, y, random_state=42)
    results = sorted(
        [{'driver': predictors[i], 'mutual_info': round(float(mi[i]), 4)}
         for i in range(len(predictors))],
        key=lambda x: -x['mutual_info']
    )

    return {
        'success':  True,
        'n_days':   len(df),
        'mi_scores': results,
        'top_driver': results[0]['driver'] if results else None,
    }


# ─────────────────────────────────────────────────────────────────────────────

def cmd_lag_analysis_fixed(params):
    """Fixed version of lag analysis."""
    try:
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'numpy not installed'}

    max_lag    = int(params.get('max_lag', 10))
    start_date = params.get('start_date', '2022-01-01')

    conn = get_db()
    df = _build_aligned_series(conn, start_date)
    conn.close()

    if df is None or len(df) < 30:
        return {'success': False, 'error': 'Not enough data'}

    y = df['n_explosions'].values
    y_norm = (y - y.mean()) / (y.std() + 1e-8)

    predictors = ['breadth_score', 'ad_ratio', 'avg_volume', 'avg_return']
    results = []

    for col in predictors:
        if col not in df.columns:
            continue
        x = df[col].values
        x_norm = (x - x.mean()) / (x.std() + 1e-8)

        corrs = []
        n = len(x_norm)
        for lag in range(-max_lag, max_lag + 1):
            if lag == 0:
                xcorr = float(np.corrcoef(x_norm, y_norm)[0, 1])
            elif lag > 0:
                # x leads y by lag: x[:-lag] vs y[lag:]
                xcorr = float(np.corrcoef(x_norm[:-lag], y_norm[lag:])[0, 1])
            else:
                # y leads x by |lag|: x[-lag:] vs y[:lag]
                xcorr = float(np.corrcoef(x_norm[-lag:], y_norm[:lag])[0, 1])
            if not math.isnan(xcorr):
                corrs.append({'lag': lag, 'corr': round(xcorr, 3)})

        if not corrs:
            continue
        best = max(corrs, key=lambda c: abs(c['corr']))
        results.append({
            'driver':          col,
            'best_lag':        best['lag'],
            'best_corr':       best['corr'],
            'interpretation':  (
                f"X leads Y by {abs(best['lag'])} days (predictive)"
                if best['lag'] > 0 else
                f"Y leads X by {abs(best['lag'])} days (reactive)"
                if best['lag'] < 0 else
                "Contemporaneous"
            ),
            'top_lags': sorted(corrs, key=lambda c: -abs(c['corr']))[:3],
        })

    results.sort(key=lambda x: -abs(x['best_corr']))

    return {
        'success': True,
        'n_days':  len(df),
        'results': results,
    }


def cmd_report(params):
    granger = cmd_granger_test({'max_lag': 5})
    lag     = cmd_lag_analysis_fixed({'max_lag': 7})
    mi      = cmd_mi_matrix({})

    # ── Persist key causal findings to DB ─────────────────────────────────────
    _causal_drivers = granger.get('causal_drivers', [])
    _top_mi_driver  = mi.get('top_driver', '')
    _top_lag_driver = (lag.get('results', [{}]) or [{}])[0].get('driver', '') if lag.get('results') else ''
    _summary        = granger.get('summary', '')
    _now            = datetime.datetime.utcnow().isoformat()
    _conn = get_db()
    try:
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS causal_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                granger_drivers TEXT,
                top_mi_driver TEXT,
                top_lag_driver TEXT,
                n_causal INTEGER,
                summary TEXT,
                generated_at TEXT
            )
        """)
        _conn.execute(
            """INSERT INTO causal_insights
               (date, granger_drivers, top_mi_driver, top_lag_driver,
                n_causal, summary, generated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                _now[:10],
                json.dumps(_causal_drivers),
                _top_mi_driver,
                _top_lag_driver,
                len(_causal_drivers),
                _summary,
                _now,
            )
        )
        _conn.commit()
    except Exception:
        try: _conn.rollback()
        except Exception: pass
    finally:
        _conn.close()

    return {
        'success': True,
        'granger_causality': granger,
        'lag_analysis':      lag,
        'mutual_information': mi,
    }


COMMANDS = {
    'granger_test':  cmd_granger_test,
    'lag_analysis':  cmd_lag_analysis_fixed,
    'mi_matrix':     cmd_mi_matrix,
    'report':        cmd_report,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'report'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
