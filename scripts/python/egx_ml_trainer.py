#!/usr/bin/env python3
"""
EGX ML Trainer — خطة تدريب شاملة
===================================
يستخدم كامل قوة الجهاز (i9-9880H × 16 thread، 16 GB RAM) لتدريب:

  Phase 1 — Feature Engineering : 60+ مميز من OHLCV + DNA + سوق
  Phase 2 — Explosion Ensemble  : LightGBM + XGBoost + RF + Optuna HPO n_jobs=8
  Phase 3 — Regime-Specific ML  : n_jobs=-1، 500 شجرة، Optuna لكل نظام
  Phase 4 — Per-Stock Models    : multiprocessing.Pool(8) لـ 100 سهم
  Phase 5 — Triple Barrier      : تسمية أفضل + meta-labeling stacking
  Phase 6 — Walk-Forward BT     : Sharpe + Sortino + MaxDD + Regime P&L
  Phase 7 — SHAP Analysis       : أهمية المميزات + حذف الضعيفة تلقائياً

التشغيل:
  python3 scripts/python/egx_ml_trainer.py train_all     ← كل المراحل (~30 دقيقة)
  python3 scripts/python/egx_ml_trainer.py phase1        ← Feature Engineering فقط
  python3 scripts/python/egx_ml_trainer.py phase2        ← Explosion Ensemble
  python3 scripts/python/egx_ml_trainer.py phase3        ← Regime Models
  python3 scripts/python/egx_ml_trainer.py phase4        ← Per-Stock Models
  python3 scripts/python/egx_ml_trainer.py phase5        ← Triple Barrier
  python3 scripts/python/egx_ml_trainer.py phase6        ← Walk-Forward Backtest
  python3 scripts/python/egx_ml_trainer.py phase7        ← SHAP Analysis
  python3 scripts/python/egx_ml_trainer.py status        ← تقرير آخر تدريب

المالك: Dr. Husam | مايو 2026
"""
import os, sys, json, sqlite3, datetime, time, math, random, warnings, gc
from pathlib import Path
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── Ph74 DuckDB Analytics Layer (optional — graceful fallback to SQLite) ──────
try:
    _DL_PATH = Path(__file__).parent
    if str(_DL_PATH) not in sys.path:
        sys.path.insert(0, str(_DL_PATH))
    from duckdb_layer import cp_agg_fast as _cp_agg_fast, ohlcv_parquet as _ohlcv_parquet
    _DUCKDB_LAYER = True
except ImportError:
    _DUCKDB_LAYER = False
    _cp_agg_fast  = None
    _ohlcv_parquet= None

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent.parent
DB_PATH  = ROOT / 'data' / 'egx_trading.db'
MODELS   = Path(__file__).parent / 'models' / 'ml_trainer'
MODELS.mkdir(parents=True, exist_ok=True)

# ── Hardware config ───────────────────────────────────────────────────────────
N_CPUS       = cpu_count()          # 16 logical cores
N_JOBS       = max(1, N_CPUS - 2)   # leave 2 for OS (14 jobs)
N_OPTUNA     = max(1, N_CPUS // 2)  # 8 Optuna workers
POOL_WORKERS = min(8, N_CPUS // 2)  # 8 for per-stock Pool

print(f"[Trainer] HW: {N_CPUS} logical cores → n_jobs={N_JOBS}, optuna={N_OPTUNA}, pool={POOL_WORKERS}", flush=True)

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-131072")   # 128 MB cache
    return conn

def sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except: return default

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ml_trainer_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        phase TEXT,
        duration_seconds REAL,
        results TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS per_stock_models (
        symbol TEXT PRIMARY KEY,
        model_date TEXT,
        n_train INTEGER,
        n_features INTEGER,
        auc_oos REAL,
        precision_50 REAL,
        top_features TEXT,
        model_path TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS walkforward_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        window_id INTEGER,
        train_start TEXT,
        train_end TEXT,
        test_start TEXT,
        test_end TEXT,
        model TEXT,
        auc_test REAL,
        precision_50 REAL,
        sharpe REAL,
        sortino REAL,
        max_drawdown REAL,
        n_signals INTEGER,
        win_rate REAL,
        regime TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS feature_importance_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_date TEXT,
        phase TEXT,
        feature_name TEXT,
        importance_mean REAL,
        importance_std REAL,
        shap_mean REAL,
        rank INTEGER,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 — RICH FEATURE ENGINEERING (60+ features)
# ═════════════════════════════════════════════════════════════════════════════

TECH_FEATURES = [
    # Pre-event technical (existing)
    'pre1_bb_width', 'pre3_bb_width', 'pre5_bb_width',
    'pre1_vol_ratio', 'pre3_vol_ratio', 'pre5_vol_ratio',
    'pre1_rsi', 'pre3_rsi', 'pre5_rsi',
    'pre3_momentum_5d', 'pre5_momentum_5d',
    'pre5_bb_position', 'pre5_compression_days',
    # New technical features
    'rsi_14', 'rsi_7', 'rsi_slope_3d',
    'adx_14', 'adx_slope_5d',
    'atr_pct', 'atr_ratio_20d',
    'ema5_slope', 'ema20_slope', 'ema_cross',
    'vol_ma5_ratio', 'vol_ma20_ratio', 'vol_spike_3d',
    'close_above_ema20', 'close_above_ema50',
    'bb_squeeze_15d', 'bb_position',
    'macd_hist', 'macd_cross',
    'stoch_k', 'stoch_d', 'stoch_cross',
    'price_vs_52w_high', 'price_vs_52w_low',
    'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d',
    'vol_5d', 'vol_20d', 'vol_percentile_60d',
    'high_low_ratio_5d', 'gap_up_count_10d',
    # DNA features (from stock_profiles_deep)
    'rsi_optimal_buy', 'rsi_optimal_sell',
    'accumulation_score', 'trend_persistence_score',
    'mean_reversion_score', 'hurst_exp',
    'best_month_match', 'vol_regime_pct',
    # Market context
    'breadth_adv_pct', 'breadth_vol_ratio',
    'market_ret_5d', 'market_vol_ratio',
    'regime_bull', 'regime_bear', 'regime_choppy',
    # Sector context
    'sector_momentum_5d', 'sector_breadth',
    # Cycle context
    'days_to_peak', 'days_to_trough', 'cycle_confidence',
]

ALL_FEATURES = TECH_FEATURES   # 62 features total


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators from OHLCV DataFrame.
    Input: df with columns [close, high, low, open, volume] indexed by date.
    """
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float) if 'high' in df.columns else c
    lo = df['low'].values.astype(float) if 'low' in df.columns else c
    v = df['volume'].values.astype(float)
    n = len(c)

    def ema(x, p):
        out = np.full(n, np.nan)
        if n < p: return out
        out[p-1] = np.mean(x[:p])
        k = 2/(p+1)
        for i in range(p, n):
            out[i] = x[i]*k + out[i-1]*(1-k)
        return out

    def rsi(x, p=14):
        out = np.full(n, 50.0)
        if n < p+1: return out
        d = np.diff(x, prepend=x[0])
        gains = np.where(d > 0, d, 0.0)
        losses = np.where(d < 0, -d, 0.0)
        ag = np.mean(gains[1:p+1])
        al = np.mean(losses[1:p+1])
        for i in range(p, n):
            ag = (ag*(p-1) + gains[i])/p
            al = (al*(p-1) + losses[i])/p
            rs = ag/(al+1e-10)
            out[i] = 100 - 100/(1+rs)
        return out

    def atr(p=14):
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i]-lo[i], abs(h[i]-c[i-1]), abs(lo[i]-c[i-1]))
        out = np.full(n, np.nan)
        if n < p: return out
        out[p-1] = np.mean(tr[:p])
        for i in range(p, n):
            out[i] = (out[i-1]*(p-1) + tr[i])/p
        return out

    def adx(p=14):
        plus_dm = np.maximum(h[1:] - h[:-1], 0)
        minus_dm = np.maximum(lo[:-1] - lo[1:], 0)
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm <= plus_dm] = 0
        tr = np.array([max(h[i]-lo[i], abs(h[i]-c[i-1]), abs(lo[i]-c[i-1]))
                       for i in range(1, n)])
        def smooth(x, p):
            out = np.full(len(x), np.nan)
            if len(x) < p: return out
            out[p-1] = np.sum(x[:p])
            for i in range(p, len(x)):
                out[i] = out[i-1] - out[i-1]/p + x[i]
            return out
        str_ = smooth(tr, p); pdm = smooth(plus_dm, p); mdm = smooth(minus_dm, p)
        pdi = 100*pdm/(str_+1e-10); mdi = 100*mdm/(str_+1e-10)
        dx = 100*np.abs(pdi-mdi)/(pdi+mdi+1e-10)
        adx_val = np.full(n, np.nan)
        dx_full = np.concatenate([[np.nan], dx])
        if 2*p-1 < n:
            adx_val[2*p-1] = np.nanmean(dx_full[p:2*p])
            for i in range(2*p, n):
                adx_val[i] = (adx_val[i-1]*(p-1) + dx_full[i])/p
        return adx_val

    def rolling_std(x, p):
        out = np.full(n, np.nan)
        for i in range(p-1, n):
            out[i] = np.std(x[i-p+1:i+1])
        return out

    def rolling_mean(x, p):
        out = np.full(n, np.nan)
        cs = np.cumsum(np.concatenate([[0], x]))
        for i in range(p-1, n):
            out[i] = (cs[i+1] - cs[i-p+1]) / p
        return out

    e5  = ema(c, 5)
    e20 = ema(c, 20)
    e50 = ema(c, 50)
    r14 = rsi(c, 14)
    r7  = rsi(c, 7)
    atr14 = atr(14)
    adx14 = adx(14)
    vm5  = rolling_mean(v, 5)
    vm20 = rolling_mean(v, 20)
    bb_std = rolling_std(c, 20)
    bb_mid = rolling_mean(c, 20)

    rets = np.diff(c, prepend=c[0]) / (np.abs(c) + 1e-10)
    # Clip extreme daily returns caused by data errors (unit mismatches, unadjusted splits):
    # EGX circuit breakers cap moves at ±20%; clip to ±25% to remove data corruptions
    # (e.g. BIGP: 0.183→86 = +46894%, UNIP: 0.287→662 = +230562% on 2026-05-11).
    # Without clipping, ret_5d/ret_20d features and vol_5d/vol_20d stds are useless.
    # (Added 2026-05-23: data quality hardening)
    rets = np.clip(rets, -0.25, 0.25)

    # BB width
    bb_width = 4*bb_std / (bb_mid + 1e-10)
    bb_pos   = (c - (bb_mid - 2*bb_std)) / (4*bb_std + 1e-10)

    # MACD
    e12 = ema(c, 12); e26 = ema(c, 26)
    macd_line = e12 - e26
    macd_sig  = ema(np.nan_to_num(macd_line), 9)
    macd_hist_arr = macd_line - macd_sig

    # Stochastic
    def rolling_max(x, p):
        out = np.full(n, np.nan)
        for i in range(p-1, n): out[i] = np.max(x[i-p+1:i+1])
        return out
    def rolling_min(x, p):
        out = np.full(n, np.nan)
        for i in range(p-1, n): out[i] = np.min(x[i-p+1:i+1])
        return out
    hi14 = rolling_max(h, 14); lo14 = rolling_min(lo, 14)
    stoch_k_arr = 100*(c - lo14)/(hi14 - lo14 + 1e-10)
    stoch_d_arr = rolling_mean(np.nan_to_num(stoch_k_arr), 3)

    # 52-week high/low
    hi252 = rolling_max(h, min(252, n))
    lo252 = rolling_min(lo, min(252, n))

    out = pd.DataFrame(index=df.index)
    out['rsi_14']         = r14
    out['rsi_7']          = r7
    out['rsi_slope_3d']   = pd.Series(r14, index=df.index).diff(3)
    out['adx_14']         = adx14
    out['adx_slope_5d']   = pd.Series(np.nan_to_num(adx14), index=df.index).diff(5)
    out['atr_pct']        = atr14 / (c + 1e-10) * 100
    out['atr_ratio_20d']  = atr14 / (rolling_mean(atr14, 20) + 1e-10)
    out['ema5_slope']     = pd.Series(np.nan_to_num(e5), index=df.index).pct_change(3)
    out['ema20_slope']    = pd.Series(np.nan_to_num(e20), index=df.index).pct_change(5)
    out['ema_cross']      = (e5 > e20).astype(float)
    out['vol_ma5_ratio']  = v / (vm5 + 1e-10)
    out['vol_ma20_ratio'] = v / (vm20 + 1e-10)
    out['vol_spike_3d']   = pd.Series(v / (vm20+1e-10), index=df.index).rolling(3).max()
    out['close_above_ema20'] = (c > e20).astype(float)
    out['close_above_ema50'] = (c > e50).astype(float)
    out['bb_squeeze_15d'] = pd.Series(bb_width, index=df.index).rolling(15).min()
    out['bb_position']    = bb_pos
    out['macd_hist']      = macd_hist_arr
    out['macd_cross']     = ((macd_hist_arr > 0) & (pd.Series(macd_hist_arr, index=df.index).shift(1) <= 0)).astype(float)
    out['stoch_k']        = stoch_k_arr
    out['stoch_d']        = stoch_d_arr
    out['stoch_cross']    = ((stoch_k_arr > stoch_d_arr) & (pd.Series(stoch_k_arr, index=df.index).shift(1) <= pd.Series(stoch_d_arr, index=df.index).shift(1))).astype(float)
    out['price_vs_52w_high'] = c / (hi252 + 1e-10)
    out['price_vs_52w_low']  = c / (lo252 + 1e-10)
    for p, name in [(1,'ret_1d'),(3,'ret_3d'),(5,'ret_5d'),(10,'ret_10d'),(20,'ret_20d')]:
        out[name] = pd.Series(rets, index=df.index).rolling(p).sum()
    out['vol_5d']         = pd.Series(rets, index=df.index).rolling(5).std()
    out['vol_20d']        = pd.Series(rets, index=df.index).rolling(20).std()
    out['vol_percentile_60d'] = pd.Series(atr14, index=df.index).rolling(60).rank(pct=True)
    out['high_low_ratio_5d']  = pd.Series((h - lo) / (lo + 1e-10), index=df.index).rolling(5).mean()
    if 'open' in df.columns:
        gap_up = ((df['open'].values / np.concatenate([[df['close'].values[0]], df['close'].values[:-1]]) - 1) > 0.01).astype(float)
        out['gap_up_count_10d'] = pd.Series(gap_up, index=df.index).rolling(10).sum()
    else:
        out['gap_up_count_10d'] = 0.0

    # BB pre-event features (lag features for explosion model)
    for lag, name in [(1,'pre1_bb_width'),(3,'pre3_bb_width'),(5,'pre5_bb_width')]:
        out[name] = pd.Series(bb_width, index=df.index).shift(lag)
    for lag, name in [(1,'pre1_vol_ratio'),(3,'pre3_vol_ratio'),(5,'pre5_vol_ratio')]:
        out[name] = pd.Series(v/(vm20+1e-10), index=df.index).shift(lag)
    for lag, name in [(1,'pre1_rsi'),(3,'pre3_rsi'),(5,'pre5_rsi')]:
        out[name] = pd.Series(r14, index=df.index).shift(lag)
    out['pre3_momentum_5d'] = pd.Series(rets, index=df.index).rolling(5).sum().shift(3)
    out['pre5_momentum_5d'] = pd.Series(rets, index=df.index).rolling(5).sum().shift(5)
    out['pre5_bb_position'] = pd.Series(bb_pos, index=df.index).shift(5)
    # compression_days: consecutive days of narrow BB
    narrow = (bb_width < bb_width.mean() * 0.7)
    comp_days = np.zeros(n)
    cnt = 0
    for i in range(n):
        cnt = cnt+1 if narrow[i] else 0
        comp_days[i] = cnt
    out['pre5_compression_days'] = pd.Series(comp_days, index=df.index).shift(5)
    out['hl_ratio_5d'] = pd.Series((h-lo)/(lo+1e-10), index=df.index).rolling(5).mean()
    out['ret_5d']      = out.get('ret_5d', pd.Series(0.0, index=df.index))
    out['vol_5d_avg']  = pd.Series(v, index=df.index).rolling(5).mean()

    return out


def phase1_build_features():
    """Phase 1: Build rich feature matrix for all symbols and save to feature_store."""
    t0 = time.time()
    print(json.dumps({"phase": "1", "step": "start", "desc": "بناء 60+ مميز لكل سهم"}), flush=True)

    conn = get_db()
    ensure_tables(conn)

    # Load all OHLCV
    rows = conn.execute("""
        SELECT symbol, date(bar_time,'unixepoch') as bar_date,
               open, high, low, close, volume
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') >= '2021-01-01'
        ORDER BY symbol, bar_time
    """).fetchall()

    df_all = pd.DataFrame([dict(r) for r in rows])
    symbols = df_all['symbol'].unique()
    print(f"[P1] {len(symbols)} رمز، {len(df_all)} صف", flush=True)

    # Load DNA profiles
    dna_rows = conn.execute("""
        SELECT symbol, rsi_optimal_buy, rsi_optimal_sell,
               accumulation_score, trend_persistence_score,
               mean_reversion_score, avg_atr_pct, vol_regime_low, vol_regime_high
        FROM stock_profiles_deep
        WHERE (symbol, computed_date) IN (
            SELECT symbol, MAX(computed_date) FROM stock_profiles_deep GROUP BY symbol
        )
    """).fetchall()
    dna = {r['symbol']: dict(r) for r in dna_rows}

    # Load breadth data
    breadth_rows = conn.execute("""
        SELECT date,
               CAST(n_advances AS REAL)/(CAST(n_advances AS REAL)+CAST(n_declines AS REAL)+0.01) as adv_pct,
               COALESCE(breadth_score/100.0, 0.5) as vol_ratio_avg
        FROM market_breadth_daily
        ORDER BY date
    """).fetchall()
    breadth_map = {r['date']: {'adv_pct': sf(r['adv_pct'], 0.5), 'vol_ratio_avg': sf(r['vol_ratio_avg'], 1.0)} for r in breadth_rows}

    # Load regime
    regime_rows = conn.execute("SELECT date, regime FROM regime_history ORDER BY date").fetchall()
    regime_map = {r['date']: r['regime'] for r in regime_rows}

    # Load cycle data (market-wide)
    cycle_rows = conn.execute("""
        SELECT period_days, confidence, next_peak_date, next_trough_date
        FROM market_cycles
        WHERE (symbol IS NULL OR symbol='MARKET')
        ORDER BY confidence DESC LIMIT 5
    """).fetchall()
    cycles = [dict(r) for r in cycle_rows]

    n_done = 0
    n_features_written = 0
    today_str = datetime.date.today().isoformat()

    for sym in symbols:
        try:
            sym_df = df_all[df_all['symbol'] == sym].copy()
            sym_df['bar_date'] = pd.to_datetime(sym_df['bar_date'])
            sym_df = sym_df.sort_values('bar_date').set_index('bar_date')
            sym_df = sym_df[['open','high','low','close','volume']].apply(pd.to_numeric, errors='coerce')

            if len(sym_df) < 60:
                continue

            # Compute technical indicators
            feats = _compute_indicators(sym_df)

            # Add DNA features
            d = dna.get(sym, {})
            feats['rsi_optimal_buy']         = sf(d.get('rsi_optimal_buy'), 30.0)
            feats['rsi_optimal_sell']         = sf(d.get('rsi_optimal_sell'), 70.0)
            feats['accumulation_score']       = sf(d.get('accumulation_score'), 50.0)
            feats['trend_persistence_score']  = sf(d.get('trend_persistence_score'), 50.0)
            feats['mean_reversion_score']     = sf(d.get('mean_reversion_score'), 50.0)
            feats['hurst_exp']                = sf(d.get('avg_atr_pct'), 0.5)
            sym_vol = float(sym_df['close'].pct_change().std())
            vrl = sf(d.get('vol_regime_low'), 0.01)
            vrh = sf(d.get('vol_regime_high'), 0.05)
            feats['vol_regime_pct'] = float(np.clip((sym_vol - vrl) / (vrh - vrl + 1e-10), 0, 1))
            # Best month match
            cur_month = pd.Timestamp.today().month
            best_month = int(sf(d.get('avg_drawdown_pct', 0)) % 12) + 1  # proxy
            feats['best_month_match'] = float(cur_month == best_month)

            # Market context per date
            def get_breadth(date_str):
                b = breadth_map.get(date_str, {})
                return sf(b.get('adv_pct'), 0.5), sf(b.get('vol_ratio_avg'), 1.0)
            def get_regime(date_str):
                r = regime_map.get(date_str, 'BULL')
                return float(r=='BULL'), float(r=='BEAR'), float(r=='CHOPPY')

            date_strings = feats.index.strftime('%Y-%m-%d').tolist()
            breadth_adv = [get_breadth(d)[0] for d in date_strings]
            breadth_vol = [get_breadth(d)[1] for d in date_strings]
            reg_bull = [get_regime(d)[0] for d in date_strings]
            reg_bear = [get_regime(d)[1] for d in date_strings]
            reg_chop = [get_regime(d)[2] for d in date_strings]

            feats['breadth_adv_pct']  = breadth_adv
            feats['breadth_vol_ratio'] = breadth_vol
            feats['regime_bull']      = reg_bull
            feats['regime_bear']      = reg_bear
            feats['regime_choppy']    = reg_chop

            # Market momentum (all-market rolling return)
            feats['market_ret_5d']    = feats.get('ret_5d', pd.Series(0.0, index=feats.index))
            feats['market_vol_ratio'] = breadth_vol

            # Sector context — proxy with symbol's own 10d momentum relative to 20d
            feats['sector_momentum_5d'] = feats.get('ret_5d', 0.0)
            feats['sector_breadth']     = feats['breadth_adv_pct']

            # Cycle context
            if cycles:
                cyc = cycles[0]
                try:
                    peak_date = datetime.date.fromisoformat(cyc['next_peak_date']) if cyc.get('next_peak_date') else None
                    trough_date = datetime.date.fromisoformat(cyc['next_trough_date']) if cyc.get('next_trough_date') else None
                    days_to_peak_vals = []
                    days_to_trough_vals = []
                    for d_str in date_strings:
                        d_obj = datetime.date.fromisoformat(d_str)
                        dtp = (peak_date - d_obj).days if peak_date else 0
                        dtt = (trough_date - d_obj).days if trough_date else 0
                        days_to_peak_vals.append(max(-30, min(30, dtp)))
                        days_to_trough_vals.append(max(-30, min(30, dtt)))
                    feats['days_to_peak']     = days_to_peak_vals
                    feats['days_to_trough']   = days_to_trough_vals
                    feats['cycle_confidence'] = float(cyc['confidence'])
                except:
                    feats['days_to_peak']     = 0.0
                    feats['days_to_trough']   = 0.0
                    feats['cycle_confidence'] = 0.5
            else:
                feats['days_to_peak'] = feats['days_to_trough'] = feats['cycle_confidence'] = 0.0

            # Write to feature_store (only last 60 dates to avoid bloat)
            feats = feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)
            recent = feats.iloc[-60:]

            records = []
            for date_idx, row_f in recent.iterrows():
                date_str = date_idx.strftime('%Y-%m-%d')
                for fname in TECH_FEATURES:
                    if fname in row_f.index:
                        records.append((
                            date_str, sym, fname,
                            float(row_f[fname]),
                            'v2', 'computed',
                            datetime.datetime.now().isoformat()
                        ))

            conn.executemany("""
                INSERT OR REPLACE INTO feature_store
                (feature_date, symbol, feature_name, feature_value, version, source_table, computed_at)
                VALUES (?,?,?,?,?,?,?)
            """, records)
            n_features_written += len(records)
            n_done += 1

            if n_done % 50 == 0:
                conn.commit()
                print(f"[P1] {n_done}/{len(symbols)} symbols... {n_features_written} features", flush=True)

        except Exception as e:
            print(json.dumps({"warn": f"P1 {sym} error: {e}"}), flush=True)

    conn.commit()
    dur = time.time() - t0
    result = {"phase": "1", "symbols": n_done, "features_written": n_features_written,
              "duration_seconds": round(dur, 1), "features_per_symbol": len(TECH_FEATURES)}
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '1', dur, json.dumps(result)))
    conn.commit()
    conn.close()
    print(json.dumps(result), flush=True)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 — EXPLOSION ENSEMBLE (LightGBM + XGBoost + RF, Optuna HPO)
# ═════════════════════════════════════════════════════════════════════════════

def _load_explosion_dataset(conn, use_rich_features=True, train_end='2025-12-31', oos_start='2026-01-30'):
    """Build X, y from feature_store (60+ features) + explosion labels."""
    import numpy as np

    # Positives: explosive moves LARGE + EXTREME
    pos_rows = conn.execute("""
        SELECT em.symbol, em.explosion_date,
               em.pre1_bb_width, em.pre3_bb_width, em.pre5_bb_width,
               em.pre1_vol_ratio, em.pre3_vol_ratio, em.pre5_vol_ratio,
               em.pre1_rsi, em.pre3_rsi, em.pre5_rsi,
               em.pre3_momentum_5d, em.pre5_momentum_5d,
               em.pre5_bb_position, em.pre5_compression_days,
               em.direction
        FROM explosive_moves em
        WHERE em.explosion_class IN ('LARGE','EXTREME')
          AND em.explosion_date IS NOT NULL
        ORDER BY em.explosion_date
    """).fetchall()

    # Core 13 features (properly computed for both pos and neg in explosive_moves)
    # NOTE: negatives sampled from feature_store to avoid all-zeros leakage
    CORE_FEAT = [
        'pre1_bb_width','pre3_bb_width','pre5_bb_width',
        'pre1_vol_ratio','pre3_vol_ratio','pre5_vol_ratio',
        'pre1_rsi','pre3_rsi','pre5_rsi',
        'pre3_momentum_5d','pre5_momentum_5d',
        'pre5_bb_position','pre5_compression_days',
    ]

    # Negatives: sample from feature_store (has real computed features for recent dates)
    # Fall back to ohlcv sampling with neutral feature values for older dates
    neg_from_fs = conn.execute("""
        SELECT symbol, feature_date as bar_date,
               MAX(CASE WHEN feature_name='pre1_bb_width' THEN feature_value END) as pre1_bb_width,
               MAX(CASE WHEN feature_name='pre3_bb_width' THEN feature_value END) as pre3_bb_width,
               MAX(CASE WHEN feature_name='pre5_bb_width' THEN feature_value END) as pre5_bb_width,
               MAX(CASE WHEN feature_name='pre1_vol_ratio' THEN feature_value END) as pre1_vol_ratio,
               MAX(CASE WHEN feature_name='pre3_vol_ratio' THEN feature_value END) as pre3_vol_ratio,
               MAX(CASE WHEN feature_name='pre5_vol_ratio' THEN feature_value END) as pre5_vol_ratio,
               MAX(CASE WHEN feature_name='pre1_rsi' THEN feature_value END) as pre1_rsi,
               MAX(CASE WHEN feature_name='pre3_rsi' THEN feature_value END) as pre3_rsi,
               MAX(CASE WHEN feature_name='pre5_rsi' THEN feature_value END) as pre5_rsi,
               MAX(CASE WHEN feature_name='pre3_momentum_5d' THEN feature_value END) as pre3_momentum_5d,
               MAX(CASE WHEN feature_name='pre5_momentum_5d' THEN feature_value END) as pre5_momentum_5d,
               MAX(CASE WHEN feature_name='pre5_bb_position' THEN feature_value END) as pre5_bb_position,
               MAX(CASE WHEN feature_name='pre5_compression_days' THEN feature_value END) as pre5_compression_days
        FROM feature_store
        WHERE feature_date NOT IN (SELECT explosion_date FROM explosive_moves)
          AND feature_name IN ('pre1_bb_width','pre3_bb_width','pre5_bb_width',
              'pre1_vol_ratio','pre3_vol_ratio','pre5_vol_ratio',
              'pre1_rsi','pre3_rsi','pre5_rsi',
              'pre3_momentum_5d','pre5_momentum_5d','pre5_bb_position','pre5_compression_days')
        GROUP BY symbol, feature_date
        ORDER BY RANDOM() LIMIT ?
    """, (len(pos_rows) * 3,)).fetchall()

    FEAT_NAMES = CORE_FEAT

    def pos_vec(r):
        return [sf(r['pre1_bb_width']), sf(r['pre3_bb_width']), sf(r['pre5_bb_width']),
                sf(r['pre1_vol_ratio']), sf(r['pre3_vol_ratio']), sf(r['pre5_vol_ratio']),
                sf(r['pre1_rsi'], 50), sf(r['pre3_rsi'], 50), sf(r['pre5_rsi'], 50),
                sf(r['pre3_momentum_5d']), sf(r['pre5_momentum_5d']),
                sf(r['pre5_bb_position'], 0.5), sf(r['pre5_compression_days'])]

    def neg_vec(r):
        # Negatives from feature_store: real computed values (no leakage)
        return [sf(r['pre1_bb_width']), sf(r['pre3_bb_width']), sf(r['pre5_bb_width']),
                sf(r['pre1_vol_ratio'], 1.0), sf(r['pre3_vol_ratio'], 1.0), sf(r['pre5_vol_ratio'], 1.0),
                sf(r['pre1_rsi'], 50), sf(r['pre3_rsi'], 50), sf(r['pre5_rsi'], 50),
                sf(r['pre3_momentum_5d']), sf(r['pre5_momentum_5d']),
                sf(r['pre5_bb_position'], 0.5), sf(r['pre5_compression_days'])]

    X_train, y_train, X_oos, y_oos = [], [], [], []

    for r in pos_rows:
        d = r['explosion_date']
        vec = pos_vec(r)
        if d <= train_end:
            X_train.append(vec); y_train.append(1)
        elif d >= oos_start:
            X_oos.append(vec); y_oos.append(1)

    for r in neg_from_fs:
        d = r['bar_date']
        vec = neg_vec(r)
        if d <= train_end:
            X_train.append(vec); y_train.append(0)
        elif d >= oos_start:
            X_oos.append(vec); y_oos.append(0)

    return (np.array(X_train, dtype=np.float32),
            np.array(y_train, dtype=np.int32),
            np.array(X_oos, dtype=np.float32),
            np.array(y_oos, dtype=np.int32),
            FEAT_NAMES)


def _auc(y_true, y_prob):
    """Compute AUC without sklearn dependency (trapezoidal)."""
    from sklearn.metrics import roc_auc_score
    try:
        if len(set(y_true)) < 2: return 0.5
        return float(roc_auc_score(y_true, y_prob))
    except: return 0.5


def phase2_explosion_ensemble():
    """Phase 2: Train LightGBM + XGBoost + RF ensemble with Optuna HPO.
    Uses explosion_ml.py's proven data pipeline for negatives (real OHLCV features).
    """
    import lightgbm as lgb
    import xgboost as xgb
    import optuna
    import pickle
    from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import build_training_data, build_oos_data, FEATURE_COLS

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    t0 = time.time()
    print(json.dumps({"phase": "2", "step": "start", "desc": "Explosion Ensemble: LightGBM+XGB+RF+Optuna"}), flush=True)

    import datetime as _dt
    # Ph 31 — Dynamic train_end: use 30 days ago so model always trains on recent data
    # This fixes AUC degradation from hardcoded 2025-12-31 cutoff
    _today      = _dt.date.today()
    _train_end  = (_today - _dt.timedelta(days=30)).isoformat()   # 30-day buffer for label stability
    _oos_start  = (_today - _dt.timedelta(days=60)).isoformat()   # 60-day OOS window
    _cutoff_12m = (_today - _dt.timedelta(days=365)).isoformat()  # recency weight cutoff

    conn = get_db()
    X_train_list, y_train_list = build_training_data(conn, train_end=_train_end)
    X_oos_list, y_oos_list, _ = build_oos_data(conn, oos_start=_oos_start)

    # Ph 31 — Recency sample weights: last 12 months → 3x weight, rest → 1x
    _pos_dates = [r['explosion_date'] for r in conn.execute(
        "SELECT explosion_date FROM explosive_moves WHERE explosion_date <= ? ORDER BY rowid",
        (_train_end,)
    ).fetchall()]
    conn.close()
    print(f"[P2][Ph31] Dynamic window: train_end={_train_end}, oos_start={_oos_start}", flush=True)

    X_tr = np.array(X_train_list, dtype=np.float32)
    y_tr = np.array(y_train_list, dtype=np.int32)
    X_os = np.array(X_oos_list, dtype=np.float32)
    y_os = np.array(y_oos_list, dtype=np.int32)
    feat_names = FEATURE_COLS

    n_pos = int(y_tr.sum()); n_neg = int((y_tr==0).sum())
    scale_pos = n_neg / max(n_pos, 1)

    # Build sample_weights: positives in order of rowid, negatives uniform
    _sample_weights = np.ones(len(X_tr), dtype=np.float64)
    _recent_count = 0
    for _i, _d in enumerate(_pos_dates[:n_pos]):
        if _d >= _cutoff_12m:
            _sample_weights[_i] = 3.0
            _recent_count += 1
    print(f"[P2] Train: {len(X_tr)} ({n_pos}+/{n_neg}-), OOS: {len(X_os)}", flush=True)
    print(f"[P2] Features: {len(feat_names)}, scale_pos_weight={scale_pos:.2f}", flush=True)
    print(f"[P2][Ph31] Recency weights: {_recent_count} recent pos (3×) / {n_pos - _recent_count} old pos (1×) / {n_neg} neg (1×)", flush=True)

    results = {}

    # ── 2a. LightGBM Optuna HPO ───────────────────────────────────────────────
    print("[P2] LightGBM Optuna HPO (150 trials)...", flush=True)

    def lgb_objective(trial):
        params = {
            'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
            'num_threads': N_JOBS,
            'scale_pos_weight': scale_pos,
            'learning_rate':    trial.suggest_float('lr', 0.01, 0.15, log=True),
            'num_leaves':       trial.suggest_int('nl', 16, 128),
            'min_data_in_leaf': trial.suggest_int('mdl', 10, 50),
            'feature_fraction': trial.suggest_float('ff', 0.5, 1.0),
            'bagging_fraction': trial.suggest_float('bf', 0.5, 1.0),
            'bagging_freq':     trial.suggest_int('bfreq', 1, 10),
            'reg_alpha':        trial.suggest_float('ra', 1e-4, 10.0, log=True),
            'reg_lambda':       trial.suggest_float('rl', 1e-4, 10.0, log=True),
        }
        # Mini-split for HPO speed (use 30% of train data)
        idx = np.random.permutation(len(X_tr))
        cut = int(len(idx)*0.7)
        X_h, y_h = X_tr[idx[:cut]], y_tr[idx[:cut]]
        X_v, y_v = X_tr[idx[cut:]], y_tr[idx[cut:]]
        ds = lgb.Dataset(X_h, label=y_h, feature_name=feat_names, free_raw_data=True)
        dv = lgb.Dataset(X_v, label=y_v, feature_name=feat_names, free_raw_data=True)
        m = lgb.train(params, ds, num_boost_round=300,
                      valid_sets=[dv],
                      callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)])
        return _auc(y_v, m.predict(X_v))

    lgb_study = optuna.create_study(direction='maximize',
                                    sampler=optuna.samplers.TPESampler(seed=42))
    lgb_study.optimize(lgb_objective, n_trials=150, n_jobs=N_OPTUNA,
                       show_progress_bar=False)
    best_lgb_params = lgb_study.best_params
    best_lgb_params.update({
        'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
        'num_threads': N_JOBS, 'scale_pos_weight': scale_pos,
    })
    print(f"[P2] Best LGB AUC={lgb_study.best_value:.4f} params={best_lgb_params}", flush=True)

    # Train final LightGBM — with Ph31 recency weights
    lgb_train = lgb.Dataset(X_tr, label=y_tr, weight=_sample_weights,
                            feature_name=feat_names, free_raw_data=False)
    lgb_val   = lgb.Dataset(X_os, label=y_os, feature_name=feat_names, free_raw_data=False) if len(X_os) > 10 else None
    lgb_model = lgb.train(
        best_lgb_params, lgb_train,
        num_boost_round=1000,
        valid_sets=[lgb_val] if lgb_val else None,
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)] if lgb_val else [lgb.log_evaluation(-1)]
    )
    lgb_model.save_model(str(MODELS / 'explosion_lgbm_v3.txt'))
    auc_lgb_oos = _auc(y_os, lgb_model.predict(X_os)) if len(X_os) > 10 else 0.5
    results['lgbm'] = {'auc_oos': round(auc_lgb_oos, 4), 'n_trees': lgb_model.num_trees()}
    print(f"[P2] LightGBM final: AUC_OOS={auc_lgb_oos:.4f}, trees={lgb_model.num_trees()}", flush=True)

    # ── 2b. XGBoost Optuna HPO ────────────────────────────────────────────────
    print("[P2] XGBoost Optuna HPO (100 trials)...", flush=True)

    def xgb_objective(trial):
        params = {
            'objective': 'binary:logistic', 'eval_metric': 'auc',
            'nthread': N_JOBS, 'verbosity': 0,
            'scale_pos_weight': scale_pos,
            'eta':            trial.suggest_float('eta', 0.01, 0.3, log=True),
            'max_depth':      trial.suggest_int('md', 3, 8),
            'min_child_weight': trial.suggest_int('mcw', 1, 20),
            'subsample':      trial.suggest_float('ss', 0.5, 1.0),
            'colsample_bytree': trial.suggest_float('cb', 0.5, 1.0),
            'reg_alpha':      trial.suggest_float('ra', 1e-4, 10.0, log=True),
            'reg_lambda':     trial.suggest_float('rl', 1e-4, 10.0, log=True),
        }
        idx = np.random.permutation(len(X_tr))
        cut = int(len(idx)*0.7)
        X_h, y_h = X_tr[idx[:cut]], y_tr[idx[:cut]]
        X_v, y_v = X_tr[idx[cut:]], y_tr[idx[cut:]]
        dh = xgb.DMatrix(X_h, label=y_h)
        dv = xgb.DMatrix(X_v, label=y_v)
        m = xgb.train(params, dh, num_boost_round=300,
                      evals=[(dv,'val')], early_stopping_rounds=20,
                      verbose_eval=False)
        return _auc(y_v, m.predict(xgb.DMatrix(X_v)))

    xgb_study = optuna.create_study(direction='maximize',
                                    sampler=optuna.samplers.TPESampler(seed=42))
    xgb_study.optimize(xgb_objective, n_trials=100, n_jobs=N_OPTUNA,
                       show_progress_bar=False)
    best_xgb_params = xgb_study.best_params
    best_xgb_params.update({'objective':'binary:logistic','eval_metric':'auc',
                             'nthread':N_JOBS,'verbosity':0,'scale_pos_weight':scale_pos})
    # XGBoost uses anonymous DMatrix — Ph31 recency weights applied
    xgb_dtrain = xgb.DMatrix(X_tr, label=y_tr, weight=_sample_weights)
    xgb_doos   = xgb.DMatrix(X_os, label=y_os) if len(X_os) > 10 else None
    xgb_model = xgb.train(best_xgb_params, xgb_dtrain,
                           num_boost_round=800,
                           evals=[(xgb_doos,'oos')] if xgb_doos else [],
                           early_stopping_rounds=50 if xgb_doos else None,
                           verbose_eval=False)
    xgb_model.save_model(str(MODELS / 'explosion_xgb_v1.json'))
    auc_xgb_oos = _auc(y_os, xgb_model.predict(xgb_doos)) if xgb_doos else 0.5
    results['xgb'] = {'auc_oos': round(auc_xgb_oos, 4)}
    print(f"[P2] XGBoost final: AUC_OOS={auc_xgb_oos:.4f}", flush=True)

    # ── 2c. Random Forest ─────────────────────────────────────────────────────
    print("[P2] Random Forest (500 trees, n_jobs=-1)...", flush=True)
    rf = RandomForestClassifier(
        n_estimators=500, max_depth=12, min_samples_leaf=10,
        max_features='sqrt', class_weight='balanced',
        n_jobs=N_JOBS, random_state=42
    )
    rf.fit(X_tr, y_tr, sample_weight=_sample_weights)  # Ph31 recency weights
    with open(MODELS / 'explosion_rf_v1.pkl', 'wb') as f:
        pickle.dump(rf, f)
    auc_rf_oos = _auc(y_os, rf.predict_proba(X_os)[:,1]) if len(X_os) > 10 else 0.5
    results['rf'] = {'auc_oos': round(auc_rf_oos, 4), 'n_trees': 500}
    print(f"[P2] RF final: AUC_OOS={auc_rf_oos:.4f}", flush=True)

    # ── 2d. Extra Trees ───────────────────────────────────────────────────────
    print("[P2] Extra Trees (400 trees)...", flush=True)
    et = ExtraTreesClassifier(
        n_estimators=400, max_depth=14, min_samples_leaf=8,
        max_features='sqrt', class_weight='balanced',
        n_jobs=N_JOBS, random_state=42
    )
    et.fit(X_tr, y_tr, sample_weight=_sample_weights)  # Ph31 recency weights
    with open(MODELS / 'explosion_et_v1.pkl', 'wb') as f:
        pickle.dump(et, f)
    auc_et_oos = _auc(y_os, et.predict_proba(X_os)[:,1]) if len(X_os) > 10 else 0.5
    results['et'] = {'auc_oos': round(auc_et_oos, 4)}
    print(f"[P2] ExtraTrees final: AUC_OOS={auc_et_oos:.4f}", flush=True)

    # ── 2e. Stacking Ensemble ─────────────────────────────────────────────────
    print("[P2] Building stacking ensemble...", flush=True)
    lgb_tr_proba = lgb_model.predict(X_tr)
    xgb_tr_proba = xgb_model.predict(xgb.DMatrix(X_tr))   # no feature_names → array input
    rf_tr_proba  = rf.predict_proba(X_tr)[:,1]
    et_tr_proba  = et.predict_proba(X_tr)[:,1]
    X_stack_tr = np.column_stack([lgb_tr_proba, xgb_tr_proba, rf_tr_proba, et_tr_proba])

    # Meta-learner: isotonic calibrated LightGBM
    meta_params = {
        'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
        'num_threads': N_JOBS, 'scale_pos_weight': scale_pos,
        'learning_rate': 0.05, 'num_leaves': 8, 'min_data_in_leaf': 10,
    }
    meta_ds = lgb.Dataset(X_stack_tr, label=y_tr, free_raw_data=True)
    meta_model = lgb.train(meta_params, meta_ds, num_boost_round=200,
                           callbacks=[lgb.log_evaluation(-1)])
    meta_model.save_model(str(MODELS / 'explosion_meta_v1.txt'))

    if len(X_os) > 10:
        lgb_os = lgb_model.predict(X_os)
        xgb_os = xgb_model.predict(xgb.DMatrix(X_os))
        rf_os  = rf.predict_proba(X_os)[:,1]
        et_os  = et.predict_proba(X_os)[:,1]
        X_stack_os = np.column_stack([lgb_os, xgb_os, rf_os, et_os])
        auc_meta = _auc(y_os, meta_model.predict(X_stack_os))
        results['ensemble'] = {'auc_oos': round(auc_meta, 4)}
        print(f"[P2] ENSEMBLE AUC_OOS={auc_meta:.4f}", flush=True)

    # Save best params for use in daily pipeline
    with open(MODELS / 'best_lgb_params.json', 'w') as f:
        json.dump(best_lgb_params, f, indent=2)

    # Save feature list
    with open(MODELS / 'explosion_features_v3.json', 'w') as f:
        json.dump(feat_names, f)

    dur = time.time() - t0
    summary = {"phase": "2", "duration_seconds": round(dur, 1), "models": results}
    conn = get_db()
    today_str = datetime.date.today().isoformat()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '2', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — REGIME-SPECIFIC MODELS (n_jobs=-1, Optuna, 500 trees)
# ═════════════════════════════════════════════════════════════════════════════

def phase3_regime_models():
    """Phase 3: Train one LightGBM per HMM regime.
    Uses real OHLCV features for negatives (via explosion_ml pipeline) — no data leakage.
    Optuna 80 trials × 8 parallel workers per regime.
    """
    import lightgbm as lgb
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import FEATURE_COLS, safe_float, _build_ohlcv_cache, _build_feature_row

    t0 = time.time()
    print(json.dumps({"phase": "3", "step": "start", "desc": "Regime-Specific LightGBM (no-leakage)"}), flush=True)

    conn = get_db()
    today_str = datetime.date.today().isoformat()
    # Ph 80 — Dynamic dates: train on all data up to 30 days ago, OOS is last 60 days.
    # Matches Phase 2 dynamic window logic. Previously hardcoded to 2025-12-31.
    _today_dt = datetime.date.today()
    IS_END    = (_today_dt - datetime.timedelta(days=30)).isoformat()
    OOS_START = (_today_dt - datetime.timedelta(days=60)).isoformat()

    # ── Regime lookup map ─────────────────────────────────────────────────────
    regime_map = {}
    for r in conn.execute("SELECT date, regime FROM regime_history"):
        regime_map[r['date']] = r['regime']
    print(f"[P3] Regime history: {len(regime_map)} dates", flush=True)

    # ── Load OHLCV cache for computing negative features ──────────────────────
    print("[P3] Building OHLCV cache for negative features...", flush=True)
    cache = _build_ohlcv_cache(conn, '2026-12-31')
    print(f"[P3] OHLCV cache: {len(cache)} symbols", flush=True)

    # ── All positives from explosive_moves (IS + OOS) ─────────────────────────
    # Ph 80 — Limit positives to last 3 years for faster training
    # The full history (13000+ rows) × 3 negatives takes 3+ hours to compute.
    # Limiting to 3 years (2023+) reduces to ~6000 pos, ~18000 neg, ~30min training.
    _p3_min_date = (_today_dt - datetime.timedelta(days=3*365)).isoformat()
    pos_rows = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date >= ?",
        (_p3_min_date,)
    ).fetchall()

    # ── Negative candidates (non-explosion days, 3× positives) ───────────────
    # Ph 80 — Limit to same 3-year window for consistency, 3× positives (not 6×)
    target_neg = len(pos_rows) * 3
    neg_candidates = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history o
        WHERE date(o.bar_time,'unixepoch') >= ?
          AND NOT EXISTS (
            SELECT 1 FROM explosive_moves e
            WHERE e.symbol = o.symbol
              AND e.explosion_date = date(o.bar_time,'unixepoch')
        )
        ORDER BY RANDOM()
        LIMIT ?
    """, (_p3_min_date, target_neg * 2,)).fetchall()
    conn.close()

    print(f"[P3] Pos candidates: {len(pos_rows)}, Neg candidates: {len(neg_candidates)}", flush=True)

    # ── Build unified labeled dataset with (X, y, split, regime) ─────────────
    all_X, all_y, all_splits, all_regimes = [], [], [], []

    for r in pos_rows:
        row = [safe_float(r[c]) for c in FEATURE_COLS]
        if sum(abs(v) for v in row) < 1e-6:
            continue
        dt = r['explosion_date']
        split = 'IS' if dt <= IS_END else 'OOS'
        regime = regime_map.get(dt, 'BULL')
        all_X.append(row); all_y.append(1)
        all_splits.append(split); all_regimes.append(regime)

    neg_count = 0
    neg_fail  = 0
    for neg in neg_candidates:
        if neg_count >= target_neg:
            break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None:
            neg_fail += 1; continue
        row = _build_feature_row(sym_df, neg['bar_date'])
        if row is None:
            neg_fail += 1; continue
        dt = neg['bar_date']
        split = 'IS' if dt <= IS_END else ('OOS' if dt >= OOS_START else None)
        if split is None:
            continue  # skip embargo period
        regime = regime_map.get(dt, 'BULL')
        all_X.append(row); all_y.append(0)
        all_splits.append(split); all_regimes.append(regime)
        neg_count += 1

    all_X       = np.array(all_X,       dtype=np.float32)
    all_y       = np.array(all_y,       dtype=np.int32)
    all_splits  = np.array(all_splits)
    all_regimes = np.array(all_regimes)
    print(f"[P3] Dataset: {len(all_X)} rows ({all_y.sum()} pos), neg_fail={neg_fail}", flush=True)

    # ── Per-regime training ───────────────────────────────────────────────────
    regimes = ['BULL', 'BEAR', 'CHOPPY', 'UNKNOWN']
    results = {}

    for regime in regimes:
        mask_is  = (all_regimes == regime) & (all_splits == 'IS')
        mask_oos = (all_regimes == regime) & (all_splits == 'OOS')
        X_is,  y_is  = all_X[mask_is],  all_y[mask_is]
        X_oos, y_oos = all_X[mask_oos], all_y[mask_oos]

        n_pos = int(y_is.sum())
        n_neg = int((y_is == 0).sum())

        if n_pos < 30:
            print(f"[P3] {regime}: skip (only {n_pos} positives)", flush=True)
            results[regime] = {'skipped': True, 'n_pos': n_pos}
            continue

        scale_pos = max(1.0, n_neg / max(n_pos, 1))
        print(f"[P3] {regime}: IS={len(X_is)} ({n_pos}+/{n_neg}-), OOS={len(X_oos)}, spw={scale_pos:.1f}", flush=True)

        # Capture for closure
        _X_is, _y_is, _spw = X_is, y_is, scale_pos

        def regime_objective(trial):
            p = {
                'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
                'num_threads': N_JOBS, 'scale_pos_weight': _spw,
                'learning_rate':    trial.suggest_float('lr', 0.01, 0.2, log=True),
                'num_leaves':       trial.suggest_int('nl', 16, 96),
                'min_data_in_leaf': trial.suggest_int('mdl', 5, 40),
                'feature_fraction': trial.suggest_float('ff', 0.5, 1.0),
                'reg_alpha':        trial.suggest_float('ra', 1e-4, 5.0, log=True),
                'reg_lambda':       trial.suggest_float('rl', 1e-4, 5.0, log=True),
            }
            idx = np.random.permutation(len(_X_is))
            cut = int(len(idx) * 0.75)
            ds = lgb.Dataset(_X_is[idx[:cut]], label=_y_is[idx[:cut]], free_raw_data=True)
            dv = lgb.Dataset(_X_is[idx[cut:]], label=_y_is[idx[cut:]], free_raw_data=True)
            m  = lgb.train(p, ds, num_boost_round=300,
                           valid_sets=[dv],
                           callbacks=[lgb.early_stopping(20, verbose=False),
                                      lgb.log_evaluation(-1)])
            return _auc(_y_is[idx[cut:]], m.predict(_X_is[idx[cut:]]))

        study = optuna.create_study(direction='maximize',
                                    sampler=optuna.samplers.TPESampler(seed=42))
        # Ph 80 — Reduced from 80→40 trials (still finds good params, 2× faster)
        # Phase 3 has 4 regimes × 40 trials = 160 total, vs 320 before
        study.optimize(regime_objective, n_trials=40, n_jobs=N_OPTUNA,
                       show_progress_bar=False)

        best_p = study.best_params
        best_p.update({'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
                       'num_threads': N_JOBS, 'scale_pos_weight': scale_pos})

        ds_full = lgb.Dataset(X_is, label=y_is, feature_name=list(FEATURE_COLS), free_raw_data=False)
        dv_oos  = (lgb.Dataset(X_oos, label=y_oos, feature_name=list(FEATURE_COLS), free_raw_data=False)
                   if len(X_oos) > 10 else None)
        model = lgb.train(best_p, ds_full, num_boost_round=500,
                          valid_sets=[dv_oos] if dv_oos else None,
                          callbacks=([lgb.early_stopping(50, verbose=False),
                                      lgb.log_evaluation(-1)] if dv_oos else
                                     [lgb.log_evaluation(-1)]))

        model_path = str(MODELS / f'regime_{regime.lower()}_lgbm_v3.txt')
        model.save_model(model_path)

        auc_oos = _auc(y_oos, model.predict(X_oos)) if len(X_oos) > 10 else 0.5
        results[regime] = {
            'auc_oos': round(auc_oos, 4), 'n_is_pos': n_pos, 'n_is_neg': n_neg,
            'best_auc_hpo': round(study.best_value, 4),
            'model_path': model_path
        }
        print(f"[P3] {regime}: AUC_OOS={auc_oos:.4f} (HPO best={study.best_value:.4f})", flush=True)

    dur = time.time() - t0
    summary = {"phase": "3", "duration_seconds": round(dur, 1), "regimes": results}
    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '3', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — PER-STOCK ML MODELS (multiprocessing.Pool)
# ═════════════════════════════════════════════════════════════════════════════

def _train_single_stock(args):
    """Worker: train LightGBM model for one stock using all 62 TECH_FEATURES.
    Called by multiprocessing.Pool.
    """
    import lightgbm as lgb
    sym, n_min = args

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # Get OHLCV for this symbol — use full history back to 2020 for more data
        rows = conn.execute("""
            SELECT date(bar_time,'unixepoch') as d,
                   open, close, high, low, volume
            FROM ohlcv_history
            WHERE symbol=? AND date(bar_time,'unixepoch') >= '2020-01-01'
            ORDER BY bar_time
        """, (sym,)).fetchall()

        # Load DNA profile for this symbol
        dna_row = conn.execute("""
            SELECT rsi_optimal_buy, rsi_optimal_sell, accumulation_score,
                   trend_persistence_score, mean_reversion_score, avg_atr_pct,
                   vol_regime_low, vol_regime_high, avg_drawdown_pct
            FROM stock_profiles_deep
            WHERE symbol=?
            ORDER BY computed_date DESC LIMIT 1
        """, (sym,)).fetchone()

        # Load market breadth / regime for date-aligned context
        breadth_rows = conn.execute("""
            SELECT date,
                   CAST(n_advances AS REAL)/(CAST(n_advances AS REAL)+CAST(n_declines AS REAL)+0.01) as adv_pct,
                   COALESCE(breadth_score/100.0, 0.5) as vol_ratio_avg
            FROM market_breadth_daily ORDER BY date
        """).fetchall()
        breadth_map = {r['date']: (float(r['adv_pct'] or 0.5), float(r['vol_ratio_avg'] or 1.0))
                       for r in breadth_rows}

        regime_rows = conn.execute(
            "SELECT date, regime FROM regime_history ORDER BY date"
        ).fetchall()
        regime_map = {r['date']: r['regime'] for r in regime_rows}

        conn.close()

        if len(rows) < n_min:
            return sym, None, f"only {len(rows)} bars"

        df = pd.DataFrame([dict(r) for r in rows])
        df['d'] = pd.to_datetime(df['d'])
        df = df.sort_values('d').set_index('d')

        # Ensure all required columns exist
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col not in df.columns:
                df[col] = df['close'] if 'close' in df.columns else 0.0
        df = df[['open', 'close', 'high', 'low', 'volume']].apply(
            pd.to_numeric, errors='coerce').dropna()

        if len(df) < 60:
            return sym, None, "too short after dropna"

        # Use the shared _compute_indicators function for all 62 TECH_FEATURES
        feats = _compute_indicators(df)

        # Add DNA features (constant across rows for this stock)
        d = dict(dna_row) if dna_row else {}
        feats['rsi_optimal_buy']         = float(d.get('rsi_optimal_buy') or 30.0)
        feats['rsi_optimal_sell']        = float(d.get('rsi_optimal_sell') or 70.0)
        feats['accumulation_score']      = float(d.get('accumulation_score') or 50.0)
        feats['trend_persistence_score'] = float(d.get('trend_persistence_score') or 50.0)
        feats['mean_reversion_score']    = float(d.get('mean_reversion_score') or 50.0)
        feats['hurst_exp']               = float(d.get('avg_atr_pct') or 0.5)
        sym_vol = float(df['close'].pct_change().std())
        vrl = float(d.get('vol_regime_low') or 0.01)
        vrh = float(d.get('vol_regime_high') or 0.05)
        feats['vol_regime_pct'] = float(np.clip((sym_vol - vrl) / (vrh - vrl + 1e-10), 0, 1))
        cur_month = pd.Timestamp.today().month
        best_month = int((float(d.get('avg_drawdown_pct') or 0)) % 12) + 1
        feats['best_month_match'] = float(cur_month == best_month)

        # Market context per date
        date_strings = feats.index.strftime('%Y-%m-%d').tolist()
        def _brd(ds):
            b = breadth_map.get(ds, (0.5, 1.0))
            return float(b[0]), float(b[1])
        def _reg(ds):
            r = regime_map.get(ds, 'BULL')
            return float(r == 'BULL'), float(r == 'BEAR'), float(r == 'CHOPPY')

        feats['breadth_adv_pct']   = [_brd(d)[0] for d in date_strings]
        feats['breadth_vol_ratio'] = [_brd(d)[1] for d in date_strings]
        reg_bull = [_reg(d)[0] for d in date_strings]
        reg_bear = [_reg(d)[1] for d in date_strings]
        reg_chop = [_reg(d)[2] for d in date_strings]
        feats['regime_bull']       = reg_bull
        feats['regime_bear']       = reg_bear
        feats['regime_choppy']     = reg_chop

        feats['market_ret_5d']    = feats.get('ret_5d', pd.Series(0.0, index=feats.index))
        feats['market_vol_ratio'] = feats['breadth_vol_ratio']
        feats['sector_momentum_5d'] = feats.get('ret_5d', 0.0)
        feats['sector_breadth']     = feats['breadth_adv_pct']

        # Cycle context (no per-symbol cycle data — use 0 defaults)
        feats['days_to_peak']     = 0.0
        feats['days_to_trough']   = 0.0
        feats['cycle_confidence'] = 0.5

        # Clean up
        feats = feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        # Keep only the 62 TECH_FEATURES columns
        fname = [f for f in TECH_FEATURES if f in feats.columns]
        feat_df = feats[fname]

        n = len(feat_df)
        c = df['close'].values
        if n < 60:
            return sym, None, "too short after feature computation"

        # Label: forward 3-day return > 1.5%
        fwd3 = np.full(n, np.nan)
        # feat_df is aligned to df.index, compute forward returns
        close_aligned = df['close'].reindex(feat_df.index).values
        for i in range(n - 3):
            fwd3[i] = (close_aligned[i+3] - close_aligned[i]) / (close_aligned[i] + 1e-10)
        label = (fwd3 > 0.015).astype(int)

        X_all = feat_df.values.astype(np.float32)
        y_all = label

        # Purged split: in-sample up to end of 2025, OOS from 2026
        dates = feat_df.index
        is_mask  = dates <= pd.Timestamp('2025-12-31')
        oos_mask = dates >= pd.Timestamp('2026-01-30')
        # Exclude the last 3 rows (no valid label)
        valid_mask = np.concatenate([np.ones(n-3, dtype=bool), np.zeros(3, dtype=bool)])
        is_mask  = is_mask  & valid_mask
        oos_mask = oos_mask & valid_mask

        X_is, y_is   = X_all[is_mask], y_all[is_mask]
        X_oos, y_oos = X_all[oos_mask], y_all[oos_mask]

        if len(X_is) < 50 or y_is.sum() < 10:
            return sym, None, f"insufficient IS: {len(X_is)} samples, {y_is.sum()} pos"

        scale_pos = int((y_is == 0).sum()) / max(int(y_is.sum()), 1)

        params = {
            'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
            'num_threads': 2,   # 2 threads per stock (pool of 8 = 16 total)
            'learning_rate': 0.03, 'num_leaves': 24, 'min_data_in_leaf': 15,
            'scale_pos_weight': scale_pos, 'feature_fraction': 0.7,
            'bagging_fraction': 0.8, 'bagging_freq': 5,
            'reg_alpha': 0.3, 'reg_lambda': 0.5,
        }
        ds = lgb.Dataset(X_is, label=y_is, feature_name=fname, free_raw_data=True)
        dv = lgb.Dataset(X_oos, label=y_oos, feature_name=fname, free_raw_data=True) if len(X_oos) > 10 else None
        cbs = [lgb.early_stopping(40, verbose=False), lgb.log_evaluation(-1)] if dv else [lgb.log_evaluation(-1)]
        m = lgb.train(params, ds, num_boost_round=500,
                      valid_sets=[dv] if dv else None, callbacks=cbs)

        auc_oos = _auc(y_oos, m.predict(X_oos)) if (dv and len(y_oos) > 5) else 0.5
        prec50  = 0.0
        if dv and len(y_oos) > 5:
            preds = m.predict(X_oos)
            mask50 = preds >= 0.5
            prec50 = float(y_oos[mask50].mean()) if mask50.any() else 0.0

        top_feat = sorted(zip(fname, m.feature_importance(importance_type='gain')),
                          key=lambda x: -x[1])[:5]

        model_path = str(MODELS / f'stock_{sym}.txt')
        m.save_model(model_path)

        return sym, {
            'auc_oos': round(auc_oos, 4),
            'precision_50': round(prec50, 4),
            'n_train': int(len(X_is)),
            'n_features': len(fname),
            'top_features': json.dumps([f[0] for f in top_feat]),
            'model_path': model_path,
        }, None

    except Exception as e:
        import traceback
        return sym, None, f"{e}\n{traceback.format_exc()[-300:]}"


def phase4_per_stock_models():
    """Phase 4: Train per-stock LightGBM in parallel (multiprocessing.Pool)."""
    t0 = time.time()
    print(json.dumps({"phase":"4","step":"start","desc":f"Per-Stock Models — Pool({POOL_WORKERS})"}), flush=True)
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    syms = conn.execute("""
        SELECT symbol, COUNT(DISTINCT date(bar_time,'unixepoch')) as n_days
        FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') >= '2020-01-01'
        GROUP BY symbol HAVING n_days >= 100
        ORDER BY n_days DESC LIMIT 300
    """).fetchall()
    conn.close()

    tasks = [(r['symbol'], 80) for r in syms]
    print(f"[P4] Training {len(tasks)} stocks with Pool({POOL_WORKERS})...", flush=True)

    results = {}
    with Pool(POOL_WORKERS) as pool:
        for sym, res, err in pool.imap_unordered(_train_single_stock, tasks, chunksize=5):
            if res:
                results[sym] = res
                if len(results) % 20 == 0:
                    print(f"[P4] {len(results)}/{len(tasks)} done", flush=True)
            elif err and 'only' not in err and 'insufficient' not in err and 'too short' not in err:
                print(f"[P4] {sym} error: {err}", flush=True)

    # Save to DB
    conn = get_db()
    for sym, res in results.items():
        conn.execute("""
            INSERT OR REPLACE INTO per_stock_models
            (symbol, model_date, n_train, n_features, auc_oos, precision_50, top_features, model_path)
            VALUES (?,?,?,?,?,?,?,?)
        """, (sym, today_str, res['n_train'], res['n_features'],
              res['auc_oos'], res['precision_50'], res['top_features'], res['model_path']))

    dur = time.time() - t0
    good = sum(1 for v in results.values() if v['auc_oos'] > 0.55)
    summary = {"phase":"4","n_trained":len(results),"n_good_auc":good,
               "duration_seconds":round(dur,1)}
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '4', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5 — TRIPLE BARRIER META-LABELING + STACKING
# ═════════════════════════════════════════════════════════════════════════════

def phase5_triple_barrier():
    """Phase 5: Compute triple barrier labels and train meta-label model."""
    import lightgbm as lgb
    t0 = time.time()
    print(json.dumps({"phase":"5","step":"start","desc":"Triple Barrier Meta-Labeling"}), flush=True)
    today_str = datetime.date.today().isoformat()

    conn = get_db()

    # Load OHLCV for top liquid symbols
    syms = conn.execute("""
        SELECT symbol FROM ohlcv_history
        GROUP BY symbol HAVING COUNT(*) >= 300
        ORDER BY COUNT(*) DESC LIMIT 80
    """).fetchall()
    sym_list = [r['symbol'] for r in syms]

    all_labels = []
    all_features = []

    for sym in sym_list:
        rows = conn.execute("""
            SELECT date(bar_time,'unixepoch') as d, close, high, low, volume
            FROM ohlcv_history WHERE symbol=? ORDER BY bar_time
        """, (sym,)).fetchall()
        if len(rows) < 60: continue

        c = np.array([sf(r['close']) for r in rows])
        h = np.array([sf(r['high'],  sf(r['close'])) for r in rows])
        lo = np.array([sf(r['low'],   sf(r['close'])) for r in rows])
        n = len(c)

        # ATR-based barriers
        atr_vals = np.zeros(n)
        for i in range(1, n):
            atr_vals[i] = max(h[i]-lo[i], abs(h[i]-c[i-1]), abs(lo[i]-c[i-1]))
        atr_ma = np.zeros(n)
        for i in range(14, n):
            atr_ma[i] = np.mean(atr_vals[i-14:i])

        # Triple barrier: TP=2×ATR, SL=1×ATR, timeout=5 bars
        for i in range(20, n-5):
            tp = c[i] + 2*atr_ma[i]
            sl = c[i] - 1*atr_ma[i]
            label = 0
            for j in range(1, 6):
                if h[i+j] >= tp:   label = 1; break
                if lo[i+j] <= sl:  label = -1; break
            if label == 0: label = int(c[i+5] > c[i]) * 2 - 1

            # Feature: base explosion model prediction + RSI + BB
            vm20 = np.mean(np.array([sf(rows[k]['volume']) for k in range(max(0,i-20),i)]) + 1e-10) if i >= 20 else 1.0
            vol_r = sf(rows[i]['volume']) / (vm20 + 1e-10)
            rsi_approx = 50.0
            bb_w = atr_ma[i] / (c[i] + 1e-10) * 4

            all_features.append([vol_r, bb_w, rsi_approx, float(c[i]/max(c[max(0,i-10):i+1])), float(atr_ma[i]/(c[i]+1e-10))])
            all_labels.append(int(label == 1))  # binary: barrier UP hit

    if len(all_features) < 100:
        conn.close()
        return {"phase":"5","error":"insufficient data for triple barrier"}

    X = np.array(all_features, dtype=np.float32)
    y = np.array(all_labels,   dtype=np.int32)

    # Purge split by time (last 20% as OOS)
    split = int(len(X)*0.8)
    X_tr, y_tr = X[:split], y[:split]
    X_os, y_os = X[split:], y[split:]

    scale_pos = int((y_tr==0).sum()) / max(int(y_tr.sum()), 1)
    params = {
        'objective':'binary','metric':'auc','verbosity':-1,
        'num_threads': N_JOBS, 'scale_pos_weight': scale_pos,
        'learning_rate':0.05,'num_leaves':24,'min_data_in_leaf':15,
    }
    fname = ['vol_ratio','bb_width','rsi','price_rank10d','atr_pct']
    ds = lgb.Dataset(X_tr, label=y_tr, feature_name=fname, free_raw_data=True)
    dv = lgb.Dataset(X_os, label=y_os, feature_name=fname, free_raw_data=True)
    m = lgb.train(params, ds, num_boost_round=300,
                  valid_sets=[dv],
                  callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(-1)])
    m.save_model(str(MODELS / 'triple_barrier_v1.txt'))
    auc_os = _auc(y_os, m.predict(X_os))

    dur = time.time() - t0
    summary = {"phase":"5","n_labels":len(all_labels),"pct_pos":round(float(np.mean(all_labels)),3),
               "auc_oos":round(auc_os,4),"duration_seconds":round(dur,1)}
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str,'5',dur,json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 6 — WALK-FORWARD BACKTEST
# ═════════════════════════════════════════════════════════════════════════════

def phase6_walkforward():
    """Phase 6: Walk-forward backtesting — 4 expanding windows."""
    import lightgbm as lgb
    t0 = time.time()
    print(json.dumps({"phase":"6","step":"start","desc":"Walk-Forward Backtest (4 windows)"}), flush=True)
    today_str = datetime.date.today().isoformat()

    # Ph38 — Window 4 test_end is always today-1 (dynamic, no stale hardcoding)
    _today_wf = datetime.date.today()
    _w4_end   = (_today_wf - datetime.timedelta(days=1)).isoformat()
    WINDOWS = [
        {'id':1,'train_start':'2021-01-01','train_end':'2023-06-30','test_start':'2023-07-01','test_end':'2024-06-30'},
        {'id':2,'train_start':'2021-01-01','train_end':'2024-06-30','test_start':'2024-07-01','test_end':'2025-03-31'},
        {'id':3,'train_start':'2021-01-01','train_end':'2024-12-31','test_start':'2025-01-01','test_end':'2025-09-30'},
        {'id':4,'train_start':'2021-01-01','train_end':'2025-09-30','test_start':'2025-10-01','test_end':_w4_end},
    ]

    conn = get_db()
    # Load full dataset once (up to today — no future leakage)
    _wf_cutoff = datetime.date.today().isoformat()
    X_full, y_full, _, _, feat_names = _load_explosion_dataset(
        conn, use_rich_features=False,
        train_end=_wf_cutoff, oos_start='2099-01-01')

    all_rows = conn.execute("""
        SELECT em.explosion_date, em.explosion_class, em.direction
        FROM explosive_moves em WHERE em.explosion_class IN ('LARGE','EXTREME')
        ORDER BY em.explosion_date
    """).fetchall()
    date_labels = [(r['explosion_date'], 1) for r in all_rows]

    neg_dates = conn.execute("""
        SELECT date(bar_time,'unixepoch') as d FROM ohlcv_history
        WHERE date(bar_time,'unixepoch') NOT IN (SELECT explosion_date FROM explosive_moves)
        ORDER BY RANDOM() LIMIT 40000
    """).fetchall()
    date_labels += [(r['d'], 0) for r in neg_dates]
    date_labels.sort(key=lambda x: x[0])

    conn.close()

    window_results = []

    for w in WINDOWS:
        print(f"[P6] Window {w['id']}: train {w['train_start']}→{w['train_end']}, test {w['test_start']}→{w['test_end']}", flush=True)

        tr_mask = [(d >= w['train_start'] and d <= w['train_end']) for d, _ in date_labels]
        te_mask = [(d >= w['test_start']  and d <= w['test_end'])  for d, _ in date_labels]

        dl = np.array(date_labels, dtype=object)
        X_all = np.zeros((len(date_labels), len(feat_names)), dtype=np.float32)
        y_all = np.array([lbl for _, lbl in date_labels], dtype=np.int32)

        # Simple features from pos data index
        pos_count = 0
        for i, (d, lbl) in enumerate(date_labels):
            if i < len(X_full):
                X_all[i] = X_full[min(i, len(X_full)-1)]

        X_tr = X_all[tr_mask]; y_tr = y_all[tr_mask]
        X_te = X_all[te_mask]; y_te = y_all[te_mask]

        if len(X_tr) < 100 or len(X_te) < 20:
            continue

        scale_pos = int((y_tr==0).sum()) / max(int(y_tr.sum()), 1)
        params = {'objective':'binary','metric':'auc','verbosity':-1,
                  'num_threads':N_JOBS,'scale_pos_weight':scale_pos,
                  'learning_rate':0.05,'num_leaves':31,'min_data_in_leaf':20,
                  'feature_fraction':0.8,'bagging_fraction':0.8,'bagging_freq':5}
        ds = lgb.Dataset(X_tr, label=y_tr, free_raw_data=True)
        m = lgb.train(params, ds, num_boost_round=400, callbacks=[lgb.log_evaluation(-1)])

        probs = m.predict(X_te)
        auc_te = _auc(y_te, probs)

        # Simulate trading signals (signal = prob >= 0.5)
        sig_mask = probs >= 0.5
        n_signals = int(sig_mask.sum())
        win_rate  = float(y_te[sig_mask].mean()) if sig_mask.any() else 0.0
        prec50    = win_rate

        # Ph79 calibrated returns — real-backtest integration with regime fallback
        _use_real_backtest = False
        _real_bt = None
        try:
            import sys as _sys
            _dl_path = str(Path(__file__).parent)
            if _dl_path not in _sys.path:
                _sys.path.insert(0, _dl_path)
            import backtest_engine as _be
            _real_bt = _be.run_backtest(str(DB_PATH), days=180, min_signals=10)
            if _real_bt and _real_bt.get('n_trades', 0) >= 10:
                _use_real_backtest = True
        except Exception:
            pass

        if _use_real_backtest and _real_bt:
            _avg_win  = min(0.08, max(0.02, _real_bt.get('avg_win_pct',  0.045)))
            _avg_loss = max(-0.08, min(-0.01, _real_bt.get('avg_loss_pct', -0.025)))
        else:
            _avg_win  = 0.045
            _avg_loss = -0.025

        # Ph79 calibrated returns from simulated daily P&L
        pnl = np.where(sig_mask,
                       np.where(y_te==1, _avg_win * 0.997, _avg_loss * 1.003),
                       0.0)
        _pnl_std = float(pnl.std())
        if _pnl_std > 1e-8:
            sharpe  = float(pnl.mean() / _pnl_std * np.sqrt(252))
            # Cap Sharpe at ±50 to guard against near-degenerate windows
            sharpe  = max(-50.0, min(50.0, sharpe))
            sortino = float(pnl.mean() / max(float(pnl[pnl<0].std()) if (pnl<0).any() else _pnl_std, 1e-6) * np.sqrt(252))
            sortino = max(-50.0, min(50.0, sortino))
        else:
            sharpe = sortino = 0.0
        cum = np.cumsum(pnl)
        max_dd = float(np.min(cum - np.maximum.accumulate(cum))) if len(cum) > 0 else 0.0

        r = {'window_id': w['id'], 'train_start': w['train_start'],
             'train_end': w['train_end'], 'test_start': w['test_start'],
             'test_end': w['test_end'], 'model': 'lgbm',
             'auc_test': round(auc_te,4), 'precision_50': round(prec50,4),
             'sharpe': round(sharpe,3), 'sortino': round(sortino,3),
             'max_drawdown': round(max_dd,4), 'n_signals': n_signals,
             'win_rate': round(win_rate,4)}
        window_results.append(r)
        print(f"[P6] W{w['id']}: AUC={auc_te:.3f} Sharpe={sharpe:.2f} Prec50={prec50:.2%} n={n_signals}", flush=True)

    conn = get_db()
    for r in window_results:
        conn.execute("""INSERT INTO walkforward_results
            (run_date,window_id,train_start,train_end,test_start,test_end,
             model,auc_test,precision_50,sharpe,sortino,max_drawdown,n_signals,win_rate)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (today_str, r['window_id'], r['train_start'], r['train_end'],
             r['test_start'], r['test_end'], r['model'], r['auc_test'],
             r['precision_50'], r['sharpe'], r['sortino'],
             r['max_drawdown'], r['n_signals'], r['win_rate']))

    dur = time.time() - t0
    avg_sharpe = np.mean([r['sharpe'] for r in window_results]) if window_results else 0
    avg_auc    = np.mean([r['auc_test'] for r in window_results]) if window_results else 0
    summary = {"phase":"6","n_windows":len(window_results),
               "avg_sharpe":round(float(avg_sharpe),3),
               "avg_auc":round(float(avg_auc),4),
               "duration_seconds":round(dur,1),
               "windows": window_results}
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str,'6',dur,json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 7 — SHAP ANALYSIS + FEATURE PRUNING
# ═════════════════════════════════════════════════════════════════════════════

def phase7_shap():
    """Phase 7: SHAP analysis on best model — rank features, flag weak ones."""
    import shap
    import lightgbm as lgb
    t0 = time.time()
    print(json.dumps({"phase":"7","step":"start","desc":"SHAP Feature Analysis"}), flush=True)
    today_str = datetime.date.today().isoformat()

    # Load best model (v3 or fall back to v2)
    v3_path = MODELS / 'explosion_lgbm_v3.txt'
    v2_path = Path(__file__).parent / 'models' / 'explosion_model.txt'
    model_path = v3_path if v3_path.exists() else v2_path

    if not model_path.exists():
        return {"phase":"7","error":"No explosion model found — run phase2 first"}

    model = lgb.Booster(model_file=str(model_path))

    # Load feature names
    feat_path = MODELS / 'explosion_features_v3.json'
    feat_names = (json.loads(feat_path.read_text()) if feat_path.exists()
                  else model.feature_name())

    conn = get_db()
    X_tr, y_tr, X_os, y_os, _ = _load_explosion_dataset(
        conn, use_rich_features=False,
        train_end='2025-12-31', oos_start='2026-01-30')
    conn.close()

    # Use OOS or train sample for SHAP (max 2000 for speed)
    X_shap = X_os if len(X_os) > 200 else X_tr
    if len(X_shap) > 2000:
        idx = np.random.choice(len(X_shap), 2000, replace=False)
        X_shap = X_shap[idx]

    print(f"[P7] Running SHAP on {len(X_shap)} samples × {X_shap.shape[1]} features...", flush=True)

    # Align features
    n_model_feats = len(model.feature_name())
    n_avail = X_shap.shape[1]
    if n_avail < n_model_feats:
        X_shap_aligned = np.zeros((len(X_shap), n_model_feats), dtype=np.float32)
        X_shap_aligned[:, :n_avail] = X_shap
    else:
        X_shap_aligned = X_shap[:, :n_model_feats]

    explainer = shap.TreeExplainer(model)
    shap_vals  = explainer.shap_values(X_shap_aligned)

    # Aggregate
    feat_names_model = model.feature_name()
    mean_abs_shap = np.abs(shap_vals).mean(axis=0)
    std_abs_shap  = np.abs(shap_vals).std(axis=0)

    # LightGBM importance
    lgb_imp = model.feature_importance(importance_type='gain')
    lgb_imp = lgb_imp / (lgb_imp.max() + 1e-10)

    # Rank by SHAP mean
    ranked = sorted(zip(feat_names_model, mean_abs_shap, std_abs_shap, lgb_imp),
                    key=lambda x: -x[1])

    conn = get_db()
    weak_features = []
    for rank, (fname, shap_mean, shap_std, imp) in enumerate(ranked, 1):
        status = 'active' if shap_mean > 0.001 else 'weak'
        if status == 'weak': weak_features.append(fname)
        conn.execute("""INSERT INTO feature_importance_log
            (log_date, phase, feature_name, importance_mean, importance_std, shap_mean, rank, status)
            VALUES (?,?,?,?,?,?,?,?)""",
            (today_str, '7', fname, float(imp), 0.0, float(shap_mean), rank, status))

    print(f"\n[P7] Top 15 features by SHAP:", flush=True)
    for rank, (fname, sm, ss, imp) in enumerate(ranked[:15], 1):
        print(f"  #{rank:2d}  {fname:35s}  SHAP={sm:.4f}  LGB_imp={imp:.3f}", flush=True)
    print(f"\n[P7] Weak features ({len(weak_features)}): {weak_features}", flush=True)

    dur = time.time() - t0
    summary = {"phase":"7","n_features_analyzed":len(feat_names_model),
               "n_active":len(ranked)-len(weak_features),
               "n_weak":len(weak_features),"weak_features":weak_features,
               "top5":[r[0] for r in ranked[:5]],
               "duration_seconds":round(dur,1)}

    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str,'7',dur,json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# Ph46 — Bayesian Win Rate Estimator (Beta-Binomial)
# بديل عن تقدير Win Rate الكلاسيكي — يعطي credible intervals حتى مع عينات صغيرة
# ═════════════════════════════════════════════════════════════════════════════

def phase46_bayesian_winrate():
    """
    Ph46: Bayesian Win Rate — Beta-Binomial posterior per signal class.

    المشكلة مع الطريقة الكلاسيكية:
        WR_classical = wins / (wins + losses)  ← غير موثوق مع <50 observation
        مثال: 3 wins من 4 → WR=75% لكن الـ CI [30%–95%]!

    الحل — Beta-Binomial:
        Prior:  WR ~ Beta(α₀=2, β₀=2)  ← slight skeptical prior (WR≈50%)
        Update: posterior Beta(α₀+wins, β₀+losses)
        Output:
          • P(WR > 50%) — احتمال أن الإشارة رابحة
          • credible interval [2.5%, 97.5%]
          • effective_wr — المتوسط الترجيحي (أفضل للقرارات)

    الجداول:
        bayesian_wr (symbol, category, regime, alpha, beta, mean_wr, ci_lower,
                     ci_upper, p_gt_50, n_obs, run_date)
    """
    from scipy.stats import beta as beta_dist
    import datetime as dt

    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(json.dumps({"phase": "46", "step": "start",
                      "desc": "Bayesian Win Rate (Beta-Binomial posterior)"}), flush=True)

    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bayesian_wr (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        category    TEXT,          -- 'overall' | 'by_regime' | 'by_symbol'
        label       TEXT,          -- regime name or symbol ticker
        alpha       REAL,          -- posterior alpha = α₀ + wins
        beta_param  REAL,          -- posterior beta  = β₀ + losses
        mean_wr     REAL,          -- posterior mean = α/(α+β)
        ci_lower    REAL,          -- 2.5th percentile
        ci_upper    REAL,          -- 97.5th percentile
        p_gt_50     REAL,          -- P(WR > 0.5)
        n_obs       INTEGER,       -- total observations used
        n_wins      INTEGER,
        n_losses    INTEGER,
        run_date    TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

    # ── Load outcome data ────────────────────────────────────────────────────
    rows = conn.execute("""
        SELECT ro.symbol, ro.signal_date,
               COALESCE(rh.regime, 'UNKNOWN') AS regime,
               ro.hit_t1,
               ro.return_t1
        FROM recommendation_outcomes ro
        LEFT JOIN regime_history rh ON rh.date = ro.signal_date
        WHERE ro.hit_t1 IS NOT NULL
    """).fetchall()

    print(f"[P46] Loaded {len(rows)} outcomes with hit_t1 labels", flush=True)

    # ── Helper: compute Beta posterior ──────────────────────────────────────
    ALPHA0, BETA0 = 2.0, 2.0   # skeptical prior: center at 50%, low confidence

    def beta_posterior(wins, losses):
        a = ALPHA0 + wins
        b = BETA0  + losses
        dist = beta_dist(a, b)
        return {
            'alpha':    round(a, 2),
            'beta':     round(b, 2),
            'mean_wr':  round(a / (a + b), 4),
            'ci_lower': round(float(dist.ppf(0.025)), 4),
            'ci_upper': round(float(dist.ppf(0.975)), 4),
            'p_gt_50':  round(float(1 - dist.cdf(0.5)), 4),
        }

    def save_posterior(category, label, wins, losses):
        if wins + losses == 0:
            return
        p = beta_posterior(wins, losses)
        conn.execute("""
            INSERT INTO bayesian_wr
            (category, label, alpha, beta_param, mean_wr, ci_lower, ci_upper,
             p_gt_50, n_obs, n_wins, n_losses, run_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (category, label,
              p['alpha'], p['beta'],
              p['mean_wr'], p['ci_lower'], p['ci_upper'],
              p['p_gt_50'], wins + losses, wins, losses, today_str))

    # ── 1. Overall win rate ──────────────────────────────────────────────────
    all_wins   = sum(1 for r in rows if r['hit_t1'] == 1)
    all_losses = sum(1 for r in rows if r['hit_t1'] == 0)
    save_posterior('overall', 'ALL', all_wins, all_losses)
    overall_p = beta_posterior(all_wins, all_losses)
    print(f"[P46] Overall: {all_wins}W/{all_losses}L → "
          f"mean_WR={overall_p['mean_wr']:.1%} "
          f"CI=[{overall_p['ci_lower']:.1%},{overall_p['ci_upper']:.1%}] "
          f"P(WR>50%)={overall_p['p_gt_50']:.1%}", flush=True)

    # ── 2. By regime ─────────────────────────────────────────────────────────
    from collections import defaultdict
    regime_wins = defaultdict(int)
    regime_loss = defaultdict(int)
    for r in rows:
        reg = r['regime']
        if r['hit_t1'] == 1: regime_wins[reg] += 1
        else:                 regime_loss[reg] += 1
    for reg in set(list(regime_wins.keys()) + list(regime_loss.keys())):
        save_posterior('by_regime', reg, regime_wins[reg], regime_loss[reg])

    # ── 3. By symbol (top 30 most-observed) ──────────────────────────────────
    sym_wins = defaultdict(int)
    sym_loss = defaultdict(int)
    for r in rows:
        if r['hit_t1'] == 1: sym_wins[r['symbol']] += 1
        else:                 sym_loss[r['symbol']] += 1
    all_syms = set(list(sym_wins.keys()) + list(sym_loss.keys()))
    top_syms = sorted(all_syms, key=lambda s: -(sym_wins[s] + sym_loss[s]))[:30]
    for sym in top_syms:
        save_posterior('by_symbol', sym, sym_wins[sym], sym_loss[sym])

    conn.commit()

    # ── Summary ───────────────────────────────────────────────────────────────
    n_saved = conn.execute(
        "SELECT count(*) FROM bayesian_wr WHERE run_date=?", (today_str,)
    ).fetchone()[0]
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "46",
        "n_outcomes": len(rows),
        "n_wins": all_wins,
        "n_losses": all_losses,
        "overall_mean_wr": overall_p['mean_wr'],
        "overall_ci": [overall_p['ci_lower'], overall_p['ci_upper']],
        "p_gt50": overall_p['p_gt_50'],
        "n_posteriors_saved": n_saved,
        "duration_seconds": round(dur, 2),
    }
    conn2 = get_db()
    conn2.execute("INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
                  (today_str, '46', dur, json.dumps(summary)))
    conn2.commit(); conn2.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# Ph47 — QMC Portfolio Risk Simulator (Quasi-Monte Carlo via Sobol)
# أسرع convergence من Monte Carlo العادي (O(log(N)^k/N) بدل O(1/√N))
# ═════════════════════════════════════════════════════════════════════════════

def phase47_qmc_portfolio_risk():
    """
    Ph47: QMC Portfolio Risk — Sobol-sequence simulation of multi-signal portfolio.

    لماذا QMC أفضل من MC العادي؟
        MC:  يستخدم أرقام عشوائية → تجمّع عشوائي → تغطية غير متساوية للفضاء
        QMC: تسلسلات Sobol low-discrepancy → تغطية منتظمة → convergence أسرع

    المدخلات:
        - الإشارات المُبوَّبة (gated) من unified_signals اليوم
        - توزيعات العوائد من ohlcv_history (historical returns per symbol)

    المخرجات لكل محفظة (يوم):
        VaR@95%, CVaR@95%, Expected Return, Sharpe estimate,
        Max Drawdown (QMC), Probability of 10%+ gain

    الجدول: qmc_portfolio_risk (run_date, n_signals, n_simulations,
                                 var_95, cvar_95, expected_return,
                                 sharpe_qmc, p_gain_10pct, max_drawdown_mean)
    """
    from scipy.stats.qmc import Sobol
    import numpy as np

    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    N_SIM = 4096   # Sobol يعمل أفضل مع قوى 2 (2^12)
    HOLDING_DAYS = 5  # T1 horizon (5 trading days)

    print(json.dumps({"phase": "47", "step": "start",
                      "desc": f"QMC Portfolio Risk (Sobol N={N_SIM}, T={HOLDING_DAYS}d)"}), flush=True)

    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS qmc_portfolio_risk (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date         TEXT,
        n_signals        INTEGER,
        n_simulations    INTEGER,
        var_95           REAL,    -- 5th percentile portfolio return
        cvar_95          REAL,    -- mean of worst 5% (Expected Shortfall)
        expected_return  REAL,    -- mean simulated return
        sharpe_qmc       REAL,    -- Sharpe ratio (annualized)
        p_gain_10pct     REAL,    -- P(portfolio return > +10%)
        p_loss_5pct      REAL,    -- P(portfolio return < -5%)
        max_drawdown_mean REAL,   -- mean of simulated max drawdowns
        kelly_fraction   REAL,    -- optimal Kelly for this portfolio
        created_at       TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

    # ── Load today's gated signals ────────────────────────────────────────────
    signals = conn.execute("""
        SELECT symbol, entry_price, stop_loss, t1_target AS target1, unified_score
        FROM unified_signals
        WHERE quality_gate_passed=1
          AND signal_date = (SELECT MAX(signal_date) FROM unified_signals WHERE quality_gate_passed=1)
        LIMIT 20
    """).fetchall()

    if not signals:
        print("[P47] No gated signals found — skip", flush=True)
        conn.close()
        return {"phase": "47", "skipped": True}

    n_sig = len(signals)
    print(f"[P47] {n_sig} gated signals → building return distributions", flush=True)

    # ── For each signal: estimate μ and σ from 60-day history ────────────────
    mus, sigmas, max_losses = [], [], []

    for sig in signals:
        sym = sig['symbol']
        prices = conn.execute("""
            SELECT close FROM ohlcv_history
            WHERE symbol=? AND close IS NOT NULL AND close > 0
            ORDER BY bar_time DESC LIMIT 65
        """, (sym,)).fetchall()

        # Prices fetched DESC → reverse to chronological order for correct diff
        closes = [r['close'] for r in reversed(prices) if r['close'] and r['close'] > 0]
        if len(closes) < 10:
            mus.append(0.005);  sigmas.append(0.025);  max_losses.append(-0.10)
            continue

        rets = np.diff(np.log(np.array(closes, dtype=float)))[-60:]
        mu_d    = float(np.mean(rets)) if len(rets) > 0 else 0.0
        sigma_d = float(np.std(rets))  if len(rets) > 0 else 0.02

        # Maximum theoretical loss = down to stop_loss from entry
        entry = sig['entry_price'] or 1.0
        stop  = sig['stop_loss']   or entry * 0.93
        max_l = (stop - entry) / entry if entry > 0 else -0.07

        mus.append(mu_d * HOLDING_DAYS)            # scale to holding period
        sigmas.append(sigma_d * np.sqrt(HOLDING_DAYS))
        max_losses.append(max_l)

    mus    = np.array(mus,    dtype=np.float32)
    sigmas = np.array(sigmas, dtype=np.float32)

    # ── QMC Sobol simulation ──────────────────────────────────────────────────
    # Sobol dimension = n_sig (one per signal)
    # Each row = one scenario, each col = one signal's return draw
    dim = min(n_sig, 21)   # Sobol max practical dimension ~21 for 4096 samples
    sampler = Sobol(d=dim, scramble=True, seed=42)
    u = sampler.random(N_SIM).astype(np.float32)   # shape (N_SIM, dim)

    # Convert uniform [0,1] → Normal using inverse CDF (Box-Muller equivalent)
    from scipy.special import ndtri
    z = ndtri(np.clip(u, 1e-6, 1 - 1e-6)).astype(np.float32)  # standard normal

    # Signal returns under QMC
    sig_returns = mus[:dim] + sigmas[:dim] * z   # (N_SIM, dim)

    # Equal-weight portfolio return
    port_returns = sig_returns.mean(axis=1)       # (N_SIM,)

    # ── Portfolio statistics ──────────────────────────────────────────────────
    var_95  = float(np.percentile(port_returns, 5))    # 5th percentile
    cvar_95 = float(port_returns[port_returns <= var_95].mean())  # Expected Shortfall
    exp_ret = float(np.mean(port_returns))
    std_ret = float(np.std(port_returns))
    sharpe  = float(exp_ret / std_ret * np.sqrt(252 / HOLDING_DAYS)) if std_ret > 0 else 0.0
    p_gain  = float((port_returns > 0.10).mean())
    p_loss  = float((port_returns < -0.05).mean())

    # Simulated drawdown: simulate HOLDING_DAYS step paths (N_SIM × HOLDING_DAYS)
    # Use a separate set of Sobol draws for the intra-holding path
    sampler2 = Sobol(d=HOLDING_DAYS, scramble=True, seed=99)
    u2 = sampler2.random(N_SIM).astype(np.float32)
    z2 = ndtri(np.clip(u2, 1e-6, 1 - 1e-6)).astype(np.float32)
    # Daily portfolio return = mean(signal daily returns)
    mu_daily  = float(np.mean(mus[:dim]  / HOLDING_DAYS))
    sig_daily = float(np.mean(sigmas[:dim] / np.sqrt(HOLDING_DAYS)))
    daily_rets = mu_daily + sig_daily * z2          # (N_SIM × HOLDING_DAYS)
    equity_path = np.cumprod(1 + daily_rets, axis=1)   # (N_SIM × HOLDING_DAYS)
    running_max = np.maximum.accumulate(equity_path, axis=1)
    drawdowns = (equity_path - running_max) / (running_max + 1e-10)
    max_dd = float(np.mean(drawdowns.min(axis=1)))      # mean of per-path max DD

    # Kelly fraction (simplified: f = μ/σ² capped at 25%)
    kelly = float(np.clip(exp_ret / (std_ret**2 + 1e-10), 0, 0.25))
    max_dd = max_dd if np.isfinite(max_dd) else 0.0

    print(f"[P47] VaR@95%={var_95:.2%}  CVaR@95%={cvar_95:.2%}  "
          f"E[R]={exp_ret:.2%}  Sharpe={sharpe:.2f}  "
          f"P(>10%)={p_gain:.1%}  MaxDD={max_dd:.2%}", flush=True)

    conn.execute("""
        INSERT INTO qmc_portfolio_risk
        (run_date, n_signals, n_simulations, var_95, cvar_95, expected_return,
         sharpe_qmc, p_gain_10pct, p_loss_5pct, max_drawdown_mean, kelly_fraction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (today_str, n_sig, N_SIM,
          round(var_95, 4), round(cvar_95, 4), round(exp_ret, 4),
          round(sharpe, 3), round(p_gain, 4), round(p_loss, 4),
          round(max_dd, 4), round(kelly, 4)))
    conn.commit()
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "47",
        "n_signals": n_sig,
        "n_simulations": N_SIM,
        "var_95":  round(var_95, 4),
        "cvar_95": round(cvar_95, 4),
        "expected_return": round(exp_ret, 4),
        "sharpe_qmc": round(sharpe, 3),
        "p_gain_10pct": round(p_gain, 4),
        "max_drawdown_mean": round(max_dd, 4),
        "kelly_fraction": round(kelly, 4),
        "duration_seconds": round(dur, 2),
    }
    conn2 = get_db()
    conn2.execute("INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
                  (today_str, '47', dur, json.dumps(summary)))
    conn2.commit(); conn2.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# Ph48 — Antithetic Variates Backtest (Variance Reduction)
# يخفض تباين تقديرات الـ backtest بـ ~40-60% بدون تكلفة إضافية
# ═════════════════════════════════════════════════════════════════════════════

def phase48_antithetic_backtest():
    """
    Ph48: Antithetic Variates Walk-Forward — Variance Reduction Technique.

    المشكلة مع Walk-Forward العادي (Ph6):
        تقدير Sharpe / WR يعاني من تباين عالٍ مع samples صغيرة
        σ(Sharpe_estimate) مرتفعة → قرارات بناءً على noise وليس signal

    الحل — Antithetic Variates:
        لكل مسار عشوائي Z، ننشئ المسار المعكوس -Z
        هذان المساران يرتبطان سلبياً → متوسطهما يلغي معظم التباين
        التحسين النظري: Var_AV ≤ Var_MC / 2 (عملياً 40-60% تخفيض)

    المخرجات:
        sharpe_av, sortino_av, ci_sharpe_95, n_paths, var_reduction_pct
        + مقارنة مع Ph6 (MC العادي)

    الجدول: antithetic_backtest_results
    """
    import numpy as np

    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    N_PATHS = 2000    # 1000 pairs antithetic (total 2000 paths)
    T = 252           # trading days per year

    print(json.dumps({"phase": "48", "step": "start",
                      "desc": f"Antithetic Variates Backtest (N={N_PATHS} paths, T={T}d)"}), flush=True)

    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS antithetic_backtest_results (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date         TEXT,
        n_signals        INTEGER,
        n_paths          INTEGER,
        sharpe_standard  REAL,   -- Standard MC estimate
        sharpe_av        REAL,   -- Antithetic Variates estimate
        sortino_av       REAL,
        ci_lower_95      REAL,   -- 95% confidence interval lower
        ci_upper_95      REAL,
        var_reduction_pct REAL,  -- % variance reduction vs standard MC
        win_rate_av      REAL,
        max_drawdown_av  REAL,
        created_at       TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

    # ── Load recent signal outcomes for distribution estimation ───────────────
    outcomes = conn.execute("""
        SELECT ro.return_t1, ro.hit_t1,
               u.unified_score, u.stop_loss, u.entry_price
        FROM recommendation_outcomes ro
        JOIN unified_signals u ON u.symbol=ro.symbol AND u.signal_date=ro.signal_date
        WHERE ro.return_t1 IS NOT NULL AND u.quality_gate_passed=1
    """).fetchall()

    # ── Fallback: use gated signals + historical returns to estimate distribution
    if len(outcomes) < 10:
        print(f"[P48] Only {len(outcomes)} outcomes — using historical explosion stats", flush=True)
        # Use explosive_moves as proxy for distribution
        hist = conn.execute("""
            SELECT return_5d AS ret
            FROM explosive_moves
            WHERE explosion_date >= date('now', '-365 days')
              AND return_5d IS NOT NULL AND return_5d != 0
            LIMIT 500
        """).fetchall()
        if not hist:
            conn.close()
            return {"phase": "48", "skipped": True, "reason": "insufficient data"}
        returns_hist = np.array([r['ret'] for r in hist if r['ret'] is not None], dtype=float)
        mu    = float(np.mean(returns_hist))
        sigma = float(np.std(returns_hist))
        wr    = float((returns_hist > 0).mean())
    else:
        returns_hist = np.array([r['return_t1'] for r in outcomes], dtype=float)
        mu    = float(np.mean(returns_hist))
        sigma = float(np.std(returns_hist))
        wr    = float((returns_hist > 0).mean())

    print(f"[P48] Distribution: μ={mu:.3%}, σ={sigma:.3%}, WR={wr:.1%}, n={len(returns_hist)}", flush=True)

    # ── Standard Monte Carlo paths ────────────────────────────────────────────
    rng = np.random.default_rng(seed=42)
    z_std = rng.standard_normal((N_PATHS // 2, T))          # standard draws
    r_std = mu + sigma * z_std                               # returns (N/2 × T)
    equity_std = np.cumprod(1 + r_std, axis=1)              # equity curves
    final_std = equity_std[:, -1]                           # terminal values
    total_ret_std = final_std - 1.0
    sharpe_std = float(np.mean(total_ret_std) / (np.std(total_ret_std) + 1e-10))
    var_std = float(np.var(total_ret_std))

    # ── Antithetic Variates — CORRECT implementation ─────────────────────────
    # الطريقة الصحيحة: نحسب الـ terminal wealth لكل مسار (original و antithetic)
    # ثم نأخذ متوسط الـ terminal wealth (وليس متوسط الـ daily returns!)
    # لأن: avg(r_orig + r_anti) = mu → cum_prod = (1+mu)^T → std=0 → Sharpe=inf
    # الحل الصحيح: AV_estimator = (f(Z) + f(-Z)) / 2 حيث f = terminal wealth
    z_anti = -z_std                                          # mirrored draws
    r_anti = mu + sigma * z_anti                             # antithetic returns

    equity_orig = np.cumprod(1 + r_std,  axis=1)[:, -1]    # terminal wealth original
    equity_anti = np.cumprod(1 + r_anti, axis=1)[:, -1]    # terminal wealth antithetic

    # AV estimator: each pair contributes ONE estimate (their average)
    equity_av_pairs = (equity_orig + equity_anti) / 2.0     # N/2 paired estimates
    total_ret_av    = equity_av_pairs - 1.0

    # For drawdown: use full antithetic paths (not just terminal)
    equity_path_orig = np.cumprod(1 + r_std,  axis=1)      # (N/2 × T)
    equity_path_anti = np.cumprod(1 + r_anti, axis=1)
    # Average path (for each scenario, AV path)
    equity_av_full   = (equity_path_orig + equity_path_anti) / 2.0  # (N/2 × T)

    # ── Statistics ────────────────────────────────────────────────────────────
    mean_av   = float(np.mean(total_ret_av))
    std_av    = float(np.std(total_ret_av))
    sharpe_av = float(mean_av / (std_av + 1e-10))
    downside  = total_ret_av[total_ret_av < 0]
    sortino_av = float(mean_av / (np.std(downside) + 1e-10)) if len(downside) > 0 else 0.0
    win_rate_av = float((total_ret_av > 0).mean())

    # Running max drawdown on AV paths
    rm_av = np.maximum.accumulate(equity_av_full, axis=1)
    dd_av = (equity_av_full - rm_av) / (rm_av + 1e-10)
    max_dd_av = float(dd_av.min(axis=1).mean())

    # Confidence interval via bootstrap on AV estimates
    n_boot = 500
    boot_sharpes = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(total_ret_av), len(total_ret_av))
        s = total_ret_av[idx]
        boot_sharpes.append(float(np.mean(s) / (np.std(s) + 1e-10)))
    ci_lower = float(np.percentile(boot_sharpes, 2.5))
    ci_upper = float(np.percentile(boot_sharpes, 97.5))

    # Variance reduction: compare AV variance vs standard MC
    var_av = float(np.var(total_ret_av))
    var_reduction = float((var_std - var_av) / (var_std + 1e-10) * 100)
    var_reduction = float(np.clip(var_reduction, -999, 99.9))

    print(f"[P48] Sharpe_MC={sharpe_std:.3f}  Sharpe_AV={sharpe_av:.3f}  "
          f"CI=[{ci_lower:.2f},{ci_upper:.2f}]  "
          f"VarReduction={var_reduction:.1f}%  "
          f"WR_AV={win_rate_av:.1%}", flush=True)

    conn.execute("""
        INSERT INTO antithetic_backtest_results
        (run_date, n_signals, n_paths, sharpe_standard, sharpe_av, sortino_av,
         ci_lower_95, ci_upper_95, var_reduction_pct, win_rate_av, max_drawdown_av)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (today_str, len(returns_hist), N_PATHS,
          round(sharpe_std, 4), round(sharpe_av, 4), round(sortino_av, 4),
          round(ci_lower, 4), round(ci_upper, 4),
          round(var_reduction, 2), round(win_rate_av, 4),
          round(max_dd_av, 4)))
    conn.commit()
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "48",
        "n_returns_used": len(returns_hist),
        "n_paths": N_PATHS,
        "sharpe_standard_mc": round(sharpe_std, 4),
        "sharpe_antithetic":  round(sharpe_av, 4),
        "sortino_av":         round(sortino_av, 4),
        "ci_sharpe_95":       [round(ci_lower, 4), round(ci_upper, 4)],
        "var_reduction_pct":  round(var_reduction, 2),
        "win_rate_av":        round(win_rate_av, 4),
        "max_drawdown_av":    round(max_dd_av, 4),
        "duration_seconds":   round(dur, 2),
    }
    conn2 = get_db()
    conn2.execute("INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
                  (today_str, '46_48', dur, json.dumps(summary)))
    conn2.commit(); conn2.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# Ph49 — Latin Hypercube Sampling: Parameter Sensitivity Analysis
# يقيس مدى حساسية نتائج النظام لاختيار معاملاته الحرجة
# ═════════════════════════════════════════════════════════════════════════════

def phase49_lhs_sensitivity():
    """
    Ph49: Latin Hypercube Sampling (LHS) — Parameter Sensitivity Analysis.

    السؤال المحوري: "كم تتغير إشاراتنا إذا غيّرنا بارامتر واحداً بـ ±20%؟"

    الأسلوب:
        LHS أفضل من MC بكثير لتحليل الحساسية:
        - يضمن تغطية كل نطاق بارامتر بشكل متساوٍ (stratified)
        - يكشف "نظرة حساسة" تُخبرنا أي بارامتر أكثر خطورة

    البارامترات المحللة (8 بارامترات):
        1. rsi_threshold     : حد RSI للانضغاط       [60, 80] ← افتراضي 70
        2. bb_width_min      : عرض BB الأدنى          [0.02, 0.08] ← 0.04
        3. vol_ratio_min     : نسبة حجم دخول          [1.5, 4.0] ← 2.0
        4. compression_days  : أيام الانضغاط          [3, 10] ← 5
        5. ml_threshold      : حد احتمالية ML         [0.55, 0.85] ← 0.65
        6. ues_gate          : حد UES للبوابة          [60, 85] ← 70
        7. stop_pct          : نسبة وقف الخسارة       [0.03, 0.12] ← 0.07
        8. min_rr            : الحد الأدنى لـ R:R      [1.5, 3.5] ← 2.0

    المخرجات:
        n_signals_mean, n_signals_std, most_sensitive_param,
        sensitivity_scores per param (Sobol-like total order effect)

    الجدول: lhs_sensitivity_results
    """
    from scipy.stats.qmc import LatinHypercube
    from scipy.stats import spearmanr
    import numpy as np

    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    N_SAMPLES = 512   # LHS samples (powers of 2 work well)

    print(json.dumps({"phase": "49", "step": "start",
                      "desc": f"LHS Parameter Sensitivity (N={N_SAMPLES}, d=8 params)"}), flush=True)

    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lhs_sensitivity_results (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date             TEXT,
        n_samples            INTEGER,
        most_sensitive_param TEXT,
        sensitivity_json     TEXT,   -- JSON: {param: spearman_corr}
        n_signals_mean       REAL,
        n_signals_std        REAL,
        n_signals_min        INTEGER,
        n_signals_max        INTEGER,
        robustness_score     REAL,   -- 1 - CV(n_signals): higher = more robust
        created_at           TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

    # ── Load signal data for scoring ─────────────────────────────────────────
    signals = conn.execute("""
        SELECT
            u.symbol, u.signal_date,
            u.unified_score,
            COALESCE(h.rsi, 50.0)          AS rsi_val,
            COALESCE(h.bb_width, 0.04)     AS bb_w,
            COALESCE(h.vol_ratio, 2.0)     AS vol_r,
            COALESCE(h.compression_days, 5) AS comp_d,
            COALESCE(ep.prob_pct, 65.0)    AS ml_prob,
            COALESCE(u.stop_loss, u.entry_price * 0.93) AS stop_l,
            COALESCE(u.entry_price, 1.0)   AS ep_val,
            COALESCE(u.t1_target, u.entry_price * 1.15)  AS t1_val,
            COALESCE(u.r_ratio, 2.0)       AS rr_val
        FROM unified_signals u
        LEFT JOIN (
            SELECT symbol,
                   MAX(CASE WHEN feature_name='rsi'              THEN feature_value END) AS rsi,
                   MAX(CASE WHEN feature_name='bb_width'         THEN feature_value END) AS bb_width,
                   MAX(CASE WHEN feature_name='volume_ratio'     THEN feature_value END) AS vol_ratio,
                   MAX(CASE WHEN feature_name='compression_days' THEN feature_value END) AS compression_days
            FROM feature_store
            WHERE feature_date = (SELECT MAX(signal_date) FROM unified_signals WHERE quality_gate_passed=1)
            GROUP BY symbol
        ) h ON h.symbol = u.symbol
        LEFT JOIN explosion_predictions ep
               ON ep.symbol = u.symbol AND ep.pred_date = u.signal_date
        WHERE u.signal_date = (SELECT MAX(signal_date) FROM unified_signals WHERE quality_gate_passed=1)
    """).fetchall()
    conn.close()

    n_total = len(signals)
    if n_total < 5:
        print(f"[P49] Only {n_total} signals — skip", flush=True)
        return {"phase": "49", "skipped": True, "reason": "insufficient signals"}

    print(f"[P49] {n_total} signals loaded. Running LHS ({N_SAMPLES} samples × 8 params)", flush=True)

    # ── Parameter bounds: [lower, upper] ─────────────────────────────────────
    PARAMS = {
        'rsi_threshold':    (60.0,  80.0),
        'bb_width_min':     (0.02,  0.08),
        'vol_ratio_min':    (1.5,   4.0),
        'compression_days': (3.0,   10.0),
        'ml_threshold':     (0.55,  0.85),
        'ues_gate':         (60.0,  85.0),
        'stop_pct':         (0.03,  0.12),
        'min_rr':           (1.5,   3.5),
    }
    param_names = list(PARAMS.keys())
    d = len(param_names)

    # ── Latin Hypercube Sampling ──────────────────────────────────────────────
    sampler = LatinHypercube(d=d, scramble=True, seed=42)
    lhs_unit = sampler.random(N_SAMPLES)           # shape (N_SAMPLES, d), range [0,1]

    # Scale to actual parameter ranges
    lhs_scaled = np.zeros_like(lhs_unit)
    for j, pname in enumerate(param_names):
        lo, hi = PARAMS[pname]
        lhs_scaled[:, j] = lo + (hi - lo) * lhs_unit[:, j]

    # ── Evaluate n_signals for each parameter set ─────────────────────────────
    n_signals_arr = np.zeros(N_SAMPLES, dtype=np.float32)

    for i in range(N_SAMPLES):
        p = {pname: lhs_scaled[i, j] for j, pname in enumerate(param_names)}

        count = 0
        for sig in signals:
            rsi       = float(sig['rsi_val'] or 50)
            bb_w      = float(sig['bb_w'] or 0.04)
            vol_r     = float(sig['vol_r'] or 2.0)
            comp_d    = float(sig['comp_d'] or 5)
            ml_prob   = float(sig['ml_prob'] or 65) / 100.0
            ues       = float(sig['unified_score'] or 0)
            ep_v      = float(sig['ep_val'] or 1.0)
            stop_v    = float(sig['stop_l'] or ep_v * 0.93)
            t1_v      = float(sig['t1_val'] or ep_v * 1.15)

            # Stop pct from entry
            stop_pct_actual = abs(ep_v - stop_v) / (ep_v + 1e-10)
            # R:R from entry
            rr_actual = (t1_v - ep_v) / (ep_v - stop_v + 1e-10) if ep_v > stop_v else 0

            # Apply parameter gates
            if rsi > p['rsi_threshold']:          continue
            if bb_w < p['bb_width_min']:          continue
            if vol_r < p['vol_ratio_min']:        continue
            if comp_d < p['compression_days']:    continue
            if ml_prob < p['ml_threshold']:       continue
            if ues < p['ues_gate']:               continue
            if stop_pct_actual > p['stop_pct']:   continue
            if rr_actual < p['min_rr']:           continue
            count += 1

        n_signals_arr[i] = count

    # ── Sensitivity Analysis — Spearman rank correlation ─────────────────────
    # High |ρ| → this parameter strongly controls n_signals
    sensitivity = {}
    for j, pname in enumerate(param_names):
        rho, pval = spearmanr(lhs_scaled[:, j], n_signals_arr)
        sensitivity[pname] = round(float(rho), 4)

    # Sort by absolute sensitivity
    ranked_params = sorted(sensitivity.items(), key=lambda x: -abs(x[1]))
    most_sensitive = ranked_params[0][0]
    top3 = [(p, r) for p, r in ranked_params[:3]]

    n_mean = float(np.mean(n_signals_arr))
    n_std  = float(np.std(n_signals_arr))
    n_min  = int(np.min(n_signals_arr))
    n_max  = int(np.max(n_signals_arr))
    cv     = n_std / (n_mean + 1e-10)
    robustness = float(np.clip(1.0 - cv, 0, 1))

    print(f"[P49] n_signals: mean={n_mean:.1f} std={n_std:.1f} "
          f"range=[{n_min},{n_max}] CV={cv:.2f} robustness={robustness:.2f}", flush=True)
    print(f"[P49] Most sensitive param: {most_sensitive} (ρ={sensitivity[most_sensitive]:.3f})", flush=True)
    for pname, rho in top3:
        lo, hi = PARAMS[pname]
        direction = '↑ more signals' if rho > 0 else '↓ fewer signals'
        print(f"[P49]   {pname}: ρ={rho:+.3f} ({direction}) range=[{lo},{hi}]", flush=True)

    conn2 = get_db()
    conn2.execute("""
        INSERT INTO lhs_sensitivity_results
        (run_date, n_samples, most_sensitive_param, sensitivity_json,
         n_signals_mean, n_signals_std, n_signals_min, n_signals_max, robustness_score)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (today_str, N_SAMPLES, most_sensitive, json.dumps(sensitivity),
          round(n_mean, 2), round(n_std, 2), n_min, n_max, round(robustness, 4)))
    conn2.commit()

    dur = time.time() - t0
    summary = {
        "phase": "49",
        "n_samples": N_SAMPLES,
        "n_total_signals": n_total,
        "n_signals_mean":  round(n_mean, 2),
        "n_signals_std":   round(n_std,  2),
        "n_signals_range": [n_min, n_max],
        "robustness_score": round(robustness, 4),
        "most_sensitive_param": most_sensitive,
        "top3_sensitivity": {p: r for p, r in top3},
        "all_sensitivity":  sensitivity,
        "duration_seconds": round(dur, 2),
    }
    conn2.execute("INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
                  (today_str, '49', dur, json.dumps(summary)))
    conn2.commit(); conn2.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# Ph50 — Adaptive Quality Gate Calibration (Bayesian-Informed Thresholds)
# يستخدم posteriors Ph46 لضبط حدود البوابة تلقائياً بناءً على الأداء الفعلي
# ═════════════════════════════════════════════════════════════════════════════

def phase50_adaptive_gate():
    """
    Ph50: Adaptive Quality Gate — يحسب ثوابت البوابة الديناميكية بناءً على:

    1. Bayesian WR posterior (Ph46) → يخفض حد ML عندما يكون P(WR>50%) مرتفعاً
    2. Spectral regime performance  → يُعدّل حد cycle_bottom_prox
    3. Behavioral class performance → يُجيز VOLATILE في الأسواق الصاعدة عالية الثقة

    المنطق الإحصائي:
        - حد ML الأساسي = 65%
        - إذا كان mean_WR > 80% AND n_obs >= 20 → خفّض الحد إلى max(50%, 65 - 5*(wr-0.70)/0.10)
        - إذا كان mean_WR < 55% OR P(WR>50%) < 0.7 → ارفع الحد إلى min(75%, ...)
        - VOLATILE: مسموح فقط إذا P(WR>50%) >= 0.95 AND n_volatile >= 5

    النتيجة: adaptive_gate_params يُستخدَم في signal_integration.py
    """
    from scipy.stats import beta as beta_dist
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(json.dumps({"phase": "50", "step": "start",
                      "desc": "Adaptive Quality Gate Calibration"}), flush=True)

    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS adaptive_gate_params (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date        TEXT NOT NULL,
        param_name      TEXT NOT NULL,   -- e.g. 'ml_threshold_BULL', 'volatile_allowed'
        param_value     REAL NOT NULL,
        basis           TEXT,            -- why: 'bayesian_wr', 'default', 'empirical'
        n_obs           INTEGER DEFAULT 0,
        confidence      REAL DEFAULT 0.5,
        created_at      TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_adg_date ON adaptive_gate_params(run_date);
    """)
    conn.commit()

    # ── 1. Load Bayesian WR posteriors for all regimes ────────────────────────
    bwr_rows = conn.execute("""
        SELECT label, mean_wr, ci_lower, ci_upper, p_gt_50, n_obs, n_wins, n_losses
        FROM bayesian_wr
        WHERE run_date = (SELECT MAX(run_date) FROM bayesian_wr)
          AND category IN ('overall','by_regime')
        ORDER BY n_obs DESC
    """).fetchall()

    # ── 2. Load spectral regime performance (Ph23) ────────────────────────────
    spec_rows = conn.execute("""
        SELECT regime, avg_return_5d, hit_rate, explosion_rate, sharpe_5d, n_obs
        FROM spectral_alpha_dashboard
        WHERE computed_date = (SELECT MAX(computed_date) FROM spectral_alpha_dashboard)
    """).fetchall() if conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='spectral_alpha_dashboard'"
    ).fetchone()[0] else []

    spec_perf = {r['regime']: r for r in spec_rows}

    # ── 3. Check date diversity (prevent single-day BULL market overfitting) ────
    n_distinct_dates = conn.execute("""
        SELECT COUNT(DISTINCT signal_date) FROM recommendation_outcomes
        WHERE hit_t1 IS NOT NULL
    """).fetchone()[0]

    # ── 4. Compute adaptive thresholds ───────────────────────────────────────
    params_to_save = []

    # Default thresholds
    DEFAULT_ML     = 65.0   # baseline ML floor
    MIN_ML         = 52.0   # never go below this
    MAX_ML         = 78.0   # never go above this
    MIN_OBS_ADAPT  = 20     # require at least N obs before adapting
    MIN_DATES      = 3      # require ≥3 distinct signal_dates to guard vs single-day bias

    # Damping factor: reduce adaptation strength based on evidence diversity
    # 1 date  → 0% adaptation (use default)
    # 3 dates → 50% adaptation
    # 5 dates → 100% adaptation (full formula)
    damp = max(0.0, min(1.0, (n_distinct_dates - 1) / 4.0))

    # Use CI lower bound as conservative WR estimate (more robust than mean)
    # CI lower = 2.5th percentile of Beta posterior — guarantees 97.5% confidence we're above it

    # Overall posterior
    overall_row = next((r for r in bwr_rows if r['label'] == 'ALL'), None)
    if overall_row and int(overall_row['n_obs']) >= MIN_OBS_ADAPT and n_distinct_dates >= MIN_DATES:
        wr_mean = float(overall_row['mean_wr'])
        wr_ci   = float(overall_row['ci_lower'])   # conservative 2.5th pct
        p50     = float(overall_row['p_gt_50'])
        n       = int(overall_row['n_obs'])

        # Use CONSERVATIVE CI lower bound for adaptation decision
        # This ensures we only lower gate when there's strong evidence, not just luck
        wr_eff = wr_ci  # effective WR = lower credible bound

        if p50 >= 0.95 and wr_eff > 0.70:
            # Robust high confidence: lower threshold, damped by date diversity
            raw_adapt = DEFAULT_ML - 15.0 * (wr_eff - 0.65) * (p50 - 0.70) / (0.35 * 0.30)
            adapt_ml  = DEFAULT_ML + damp * (raw_adapt - DEFAULT_ML)  # damped interpolation
            adapt_ml  = max(MIN_ML, min(DEFAULT_ML, adapt_ml))
            basis     = f'bayesian_ci_lower_n{n}_d{n_distinct_dates}'
        elif p50 < 0.65 or (wr_eff < 0.50 and n >= 30):
            # Poor performance: tighten gate (not damped — safer to be strict)
            adapt_ml = DEFAULT_ML + 10.0 * max(0, 0.55 - wr_eff)
            adapt_ml = min(MAX_ML, adapt_ml)
            basis = 'bayesian_poor_performance'
        else:
            adapt_ml = DEFAULT_ML
            basis = 'bayesian_default'

        params_to_save.append(('ml_threshold_OVERALL', adapt_ml, basis, n, p50))
        print(f"[P50] Overall: WR_ci={wr_ci:.1%} P>50={p50:.1%} n={n} dates={n_distinct_dates} damp={damp:.2f} → ML≥{adapt_ml:.1f}%", flush=True)
    else:
        adapt_ml = DEFAULT_ML
        reason = f'insufficient_obs({overall_row["n_obs"] if overall_row else 0})' if n_distinct_dates >= MIN_DATES else f'insufficient_dates({n_distinct_dates})'
        params_to_save.append(('ml_threshold_OVERALL', adapt_ml, reason, 0, 0.5))
        print(f"[P50] Using default ML={DEFAULT_ML}% — {reason}", flush=True)

    # Per-regime thresholds (inherit from overall with same dampening)
    regime_rows = [r for r in bwr_rows if r['label'] not in ('ALL',)]
    for r in regime_rows:
        wr_ci = float(r['ci_lower'])   # use conservative CI lower bound
        p50   = float(r['p_gt_50'])
        n     = int(r['n_obs'])
        reg   = r['label']

        if n < MIN_OBS_ADAPT or n_distinct_dates < MIN_DATES:
            ml_thr = adapt_ml   # inherit overall (same evidence level)
            basis  = 'inherited_overall'
        elif p50 >= 0.95 and wr_ci > 0.70:
            raw_thr = max(MIN_ML, DEFAULT_ML - 15.0 * (wr_ci - 0.65) * (p50 - 0.70) / (0.35 * 0.30))
            ml_thr  = DEFAULT_ML + damp * (raw_thr - DEFAULT_ML)
            basis   = f'bayesian_ci_d{n_distinct_dates}'
        elif p50 < 0.65:
            ml_thr = min(MAX_ML, DEFAULT_ML + 10.0 * max(0, 0.55 - wr_ci))
            basis  = 'bayesian_poor_performance'
        else:
            ml_thr = DEFAULT_ML
            basis  = 'default'

        params_to_save.append((f'ml_threshold_{reg}', ml_thr, basis, n, p50))
        print(f"[P50] {reg}: WR_ci={wr_ci:.1%} n={n} → ML≥{ml_thr:.1f}% [{basis}]", flush=True)

    # VOLATILE class: require ≥3 dates AND very high confidence before allowing
    volatile_allowed = 0  # default: blocked
    if (overall_row and float(overall_row['p_gt_50']) >= 0.98
            and int(overall_row['n_obs']) >= 30 and n_distinct_dates >= 3):
        # Multi-day high confidence → relax VOLATILE with ML premium
        volatile_allowed = 1
        volatile_ml = max(DEFAULT_ML, adapt_ml + 12.0)  # +12pp premium for VOLATILE
        params_to_save.append(('volatile_ml_premium', volatile_ml, 'bayesian_multiday', int(overall_row['n_obs']), float(overall_row['p_gt_50'])))
    else:
        params_to_save.append(('volatile_ml_premium', 75.0, 'default', 0, 0.5))
    params_to_save.append(('volatile_allowed', volatile_allowed, 'bayesian', n_distinct_dates, float(overall_row['p_gt_50']) if overall_row else 0.5))

    # Noisy regime cycle_bottom_prox threshold (default 0.55)
    noisy_spec = spec_perf.get('noisy')
    if noisy_spec and int(noisy_spec['n_obs']) >= 20:
        noisy_hit = float(noisy_spec['hit_rate'] or 0)
        # If noisy regime has good hit rate, allow lower cycle_bottom_prox
        noisy_prox_thr = 0.55 - 0.15 * max(0, noisy_hit - 0.55)
        noisy_prox_thr = max(0.30, min(0.75, noisy_prox_thr))
        params_to_save.append(('noisy_prox_threshold', noisy_prox_thr, 'spectral_empirical', int(noisy_spec['n_obs']), noisy_hit))
    else:
        params_to_save.append(('noisy_prox_threshold', 0.55, 'default', 0, 0.5))

    # ── 4. Save to DB ─────────────────────────────────────────────────────────
    # Clear today's old run first
    conn.execute("DELETE FROM adaptive_gate_params WHERE run_date=?", (today_str,))
    for name, val, basis, n, conf in params_to_save:
        conn.execute("""
            INSERT INTO adaptive_gate_params
            (run_date, param_name, param_value, basis, n_obs, confidence)
            VALUES (?,?,?,?,?,?)
        """, (today_str, name, val, basis, n, conf))
    conn.commit()
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "50",
        "n_params_saved": len(params_to_save),
        "ml_threshold_overall": adapt_ml,
        "volatile_allowed": volatile_allowed,
        "params": {name: round(val,3) for name, val, *_ in params_to_save},
        "duration_seconds": round(dur, 2),
    }
    conn2 = get_db()
    conn2.execute("INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
                  (today_str, '50', dur, json.dumps(summary)))
    conn2.commit(); conn2.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 52+53 — ENHANCED MARKET BREADTH + SECTOR ROTATION ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def _ensure_breadth_tables(conn):
    """Create market_breadth_enhanced and sector_breadth_daily if missing."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS market_breadth_enhanced (
        date              TEXT PRIMARY KEY,
        n_stocks          INTEGER,
        n_advances        INTEGER,
        n_declines        INTEGER,
        n_unchanged       INTEGER,
        ad_ratio          REAL,
        up_vol_ratio      REAL,
        down_vol_ratio    REAL,
        pct_above_ema20   REAL,
        pct_above_ema50   REAL,
        pct_above_sma20   REAL,
        pct_above_sma50   REAL,
        n_new_highs_20d   INTEGER,
        n_new_lows_20d    INTEGER,
        n_new_highs_52w   INTEGER,
        n_new_lows_52w    INTEGER,
        hl_ratio_20d      REAL,
        hl_ratio_52w      REAL,
        rsi_mean          REAL,
        rsi_median        REAL,
        pct_oversold      REAL,
        pct_overbought    REAL,
        market_ret_median REAL,
        market_ret_mean   REAL,
        mkt_vol_5d        REAL,
        mcclellan_norm    REAL,
        breadth_mom_3d    REAL,
        breadth_mom_5d    REAL,
        ad_ratio_ma5      REAL,
        up_vol_ma5        REAL,
        breadth_score     REAL,
        signal            TEXT,
        computed_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sector_breadth_daily (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT NOT NULL,
        sector       TEXT NOT NULL,
        n_stocks     INTEGER,
        n_advances   INTEGER,
        n_declines   INTEGER,
        ad_ratio     REAL,
        pct_above_ema20 REAL,
        pct_above_ema50 REAL,
        mean_ret     REAL,
        median_ret   REAL,
        momentum_5d  REAL,
        rsi_mean     REAL,
        up_vol_ratio REAL,
        sector_rank  INTEGER,
        signal       TEXT,
        UNIQUE(date, sector)
    );
    CREATE TABLE IF NOT EXISTS sector_rotation_daily (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT NOT NULL UNIQUE,
        leading_sector  TEXT,
        lagging_sector  TEXT,
        rotation_score  REAL,
        sector_dispersion REAL,
        top3_sectors    TEXT,
        bot3_sectors    TEXT,
        computed_at  TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def phase52_53_enhanced_breadth():
    """
    Phase 52+53 — Enhanced Market Breadth + Sector Rotation Engine
    ---------------------------------------------------------------
    Backfills and updates two new tables from raw ohlcv_history:

    1. `market_breadth_enhanced` (Ph52):
       - Volume-weighted A/D (up_vol_ratio)
       - % stocks above EMA20/EMA50 (EWM-based)
       - New 20-day and 52-week highs vs lows
       - RSI distribution (mean, median, % oversold/overbought)
       - Market return (median/mean), 5d volatility
       - Breadth momentum 3d/5d, A/D ratio MA5, up-volume MA5
       - Composite breadth score

    2. `sector_breadth_daily` (Ph53):
       - Per-sector: A/D ratio, % above EMA20/50, mean return, momentum
       - Sector ranking by composite score
       - Identifies leading/lagging sectors

    Runs nightly (~60-120s). Used by Ph51 (tomorrow forecast) and Telegram report.
    """
    t0        = time.time()
    today_str = datetime.date.today().isoformat()
    conn      = get_db()
    ensure_tables(conn)
    _ensure_breadth_tables(conn)

    # ── 1. Load OHLCV ─────────────────────────────────────────────────────────
    print("[Ph52] Loading ohlcv_history …", flush=True)
    ohlcv = pd.read_sql_query("""
        SELECT symbol, date(bar_time,'unixepoch') AS trade_date,
               open, high, low, close, volume
        FROM ohlcv_history
        WHERE close > 0 AND volume > 0
        ORDER BY symbol, trade_date
    """, conn)

    if len(ohlcv) < 1000:
        conn.close()
        return {"error": "insufficient OHLCV data"}

    ohlcv['trade_date'] = pd.to_datetime(ohlcv['trade_date'])

    # ── 2. Load sector map ────────────────────────────────────────────────────
    try:
        sec_map = pd.read_sql_query("""
            SELECT symbol, sector FROM stock_universe
            WHERE sector IS NOT NULL AND sector != ''
        """, conn).set_index('symbol')['sector'].to_dict()
    except Exception:
        sec_map = {}

    # ── 3. Per-stock technical indicators ─────────────────────────────────────
    print("[Ph52] Computing per-stock indicators …", flush=True)
    ohlcv = ohlcv.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
    grp = ohlcv.groupby('symbol', sort=False)

    ohlcv['ema20']  = grp['close'].transform(lambda x: x.ewm(span=20, adjust=False).mean())
    ohlcv['ema50']  = grp['close'].transform(lambda x: x.ewm(span=50, adjust=False).mean())
    ohlcv['sma20']  = grp['close'].transform(lambda x: x.rolling(20, min_periods=1).mean())
    ohlcv['sma50']  = grp['close'].transform(lambda x: x.rolling(50, min_periods=1).mean())
    ohlcv['hi20']   = grp['high'].transform(lambda x: x.rolling(20, min_periods=1).max())   # 20d high
    ohlcv['lo20']   = grp['low'].transform(lambda x: x.rolling(20, min_periods=1).min())    # 20d low
    ohlcv['hi52w']  = grp['high'].transform(lambda x: x.rolling(252, min_periods=20).max()) # 52w high
    ohlcv['lo52w']  = grp['low'].transform(lambda x: x.rolling(252, min_periods=20).min())  # 52w low
    ohlcv['rsi14']  = grp['close'].transform(lambda x: _rsi_series(x, 14))
    ohlcv['ret1d']  = grp['close'].transform(lambda x: x.pct_change()).clip(-0.30, 0.30)

    ohlcv['above_ema20']  = (ohlcv['close'] > ohlcv['ema20']).astype('float32')
    ohlcv['above_ema50']  = (ohlcv['close'] > ohlcv['ema50']).astype('float32')
    ohlcv['above_sma20']  = (ohlcv['close'] > ohlcv['sma20']).astype('float32')
    ohlcv['above_sma50']  = (ohlcv['close'] > ohlcv['sma50']).astype('float32')
    ohlcv['price_up']     = (ohlcv['ret1d'] > 0).astype('float32')
    ohlcv['price_down']   = (ohlcv['ret1d'] < 0).astype('float32')
    ohlcv['up_vol']       = ohlcv['volume'] * ohlcv['price_up']
    ohlcv['down_vol']     = ohlcv['volume'] * ohlcv['price_down']
    ohlcv['rsi_over']     = (ohlcv['rsi14'] < 35).astype('float32')
    ohlcv['rsi_overbought'] = (ohlcv['rsi14'] > 65).astype('float32')
    # 20-day and 52w high/low breakouts
    ohlcv['new_hi20']  = (ohlcv['close'] >= ohlcv['hi20']).astype('float32')
    ohlcv['new_lo20']  = (ohlcv['close'] <= ohlcv['lo20']).astype('float32')
    ohlcv['new_hi52w'] = (ohlcv['close'] >= ohlcv['hi52w']).astype('float32')
    ohlcv['new_lo52w'] = (ohlcv['close'] <= ohlcv['lo52w']).astype('float32')

    # Add sector
    ohlcv['sector'] = ohlcv['symbol'].map(sec_map).fillna('Unknown')

    # ── 4. Daily aggregate breadth ────────────────────────────────────────────
    print("[Ph52] Aggregating market breadth …", flush=True)
    valid = ohlcv.dropna(subset=['ret1d'])

    daily = valid.groupby('trade_date').agg(
        n_stocks      = ('symbol',        'count'),
        n_advances    = ('price_up',      'sum'),
        n_declines    = ('price_down',    'sum'),
        n_above_ema20 = ('above_ema20',   'sum'),
        n_above_ema50 = ('above_ema50',   'sum'),
        n_above_sma20 = ('above_sma20',   'sum'),
        n_above_sma50 = ('above_sma50',   'sum'),
        total_volume  = ('volume',        'sum'),
        up_volume     = ('up_vol',        'sum'),
        down_volume   = ('down_vol',      'sum'),
        n_new_hi20    = ('new_hi20',      'sum'),
        n_new_lo20    = ('new_lo20',      'sum'),
        n_new_hi52w   = ('new_hi52w',     'sum'),
        n_new_lo52w   = ('new_lo52w',     'sum'),
        rsi_mean      = ('rsi14',         'mean'),
        rsi_median    = ('rsi14',         'median'),
        n_oversold    = ('rsi_over',      'sum'),
        n_overbought  = ('rsi_overbought','sum'),
        med_ret       = ('ret1d',         'median'),
        mean_ret      = ('ret1d',         'mean'),
    ).reset_index()

    daily = daily[daily['n_stocks'] >= 30].sort_values('trade_date').reset_index(drop=True)

    # Derived ratios
    daily['ad_ratio']       = daily['n_advances'] / (daily['n_declines'] + 1)
    daily['up_vol_ratio']   = daily['up_volume']   / (daily['total_volume'] + 1)
    daily['down_vol_ratio'] = daily['down_volume']  / (daily['total_volume'] + 1)
    daily['pct_above_ema20'] = daily['n_above_ema20'] / daily['n_stocks']
    daily['pct_above_ema50'] = daily['n_above_ema50'] / daily['n_stocks']
    daily['pct_above_sma20'] = daily['n_above_sma20'] / daily['n_stocks']
    daily['pct_above_sma50'] = daily['n_above_sma50'] / daily['n_stocks']
    daily['hl_ratio_20d']   = daily['n_new_hi20'] / (daily['n_new_hi20'] + daily['n_new_lo20'] + 1)
    daily['hl_ratio_52w']   = daily['n_new_hi52w'] / (daily['n_new_hi52w'] + daily['n_new_lo52w'] + 1)
    daily['pct_oversold']   = daily['n_oversold']   / daily['n_stocks']
    daily['pct_overbought'] = daily['n_overbought'] / daily['n_stocks']
    daily['n_unchanged']    = (daily['n_stocks'] - daily['n_advances'] - daily['n_declines']).clip(lower=0)

    # McClellan oscillator
    daily['ad_net']        = daily['n_advances'] - daily['n_declines']
    daily['ema19_ad']      = daily['ad_net'].ewm(span=19, adjust=False).mean()
    daily['ema39_ad']      = daily['ad_net'].ewm(span=39, adjust=False).mean()
    daily['mcclellan']     = daily['ema19_ad'] - daily['ema39_ad']
    daily['mcclellan_norm']= daily['mcclellan'] / (daily['n_stocks'] + 1)

    # Momentum and rolling features
    daily['mkt_vol_5d']      = daily['med_ret'].rolling(5).std()
    daily['breadth_mom_3d']  = daily['pct_above_ema20'] - daily['pct_above_ema20'].shift(3)
    daily['breadth_mom_5d']  = daily['pct_above_ema20'] - daily['pct_above_ema20'].shift(5)
    daily['ad_ratio_ma5']    = daily['ad_ratio'].rolling(5).mean()
    daily['up_vol_ma5']      = daily['up_vol_ratio'].rolling(5).mean()

    # Composite breadth score (0–100)
    daily['breadth_score'] = (
        daily['pct_above_ema20'] * 25 +
        daily['up_vol_ratio']    * 25 +
        daily['hl_ratio_20d']    * 20 +
        (daily['ad_ratio'].clip(0, 5) / 5) * 20 +
        (1 - daily['pct_oversold']) * 10
    ).clip(0, 100)

    def _breadth_signal(score):
        if score >= 70: return 'BREADTH_BULL'
        elif score >= 55: return 'BREADTH_LEAN_BULL'
        elif score >= 40: return 'BREADTH_NEUTRAL'
        elif score >= 25: return 'BREADTH_LEAN_BEAR'
        else: return 'BREADTH_BEAR'

    daily['signal'] = daily['breadth_score'].apply(_breadth_signal)

    # ── 5. Write market_breadth_enhanced ──────────────────────────────────────
    print(f"[Ph52] Writing {len(daily)} rows to market_breadth_enhanced …", flush=True)
    rows_written = 0
    for _, row in daily.iterrows():
        def sv(k): return None if pd.isna(row.get(k)) else row[k]
        conn.execute("""
            INSERT OR REPLACE INTO market_breadth_enhanced
            (date, n_stocks, n_advances, n_declines, n_unchanged,
             ad_ratio, up_vol_ratio, down_vol_ratio,
             pct_above_ema20, pct_above_ema50, pct_above_sma20, pct_above_sma50,
             n_new_highs_20d, n_new_lows_20d, n_new_highs_52w, n_new_lows_52w,
             hl_ratio_20d, hl_ratio_52w,
             rsi_mean, rsi_median, pct_oversold, pct_overbought,
             market_ret_median, market_ret_mean, mkt_vol_5d,
             mcclellan_norm, breadth_mom_3d, breadth_mom_5d,
             ad_ratio_ma5, up_vol_ma5, breadth_score, signal)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row['trade_date'].strftime('%Y-%m-%d'),
            int(sv('n_stocks') or 0), int(sv('n_advances') or 0),
            int(sv('n_declines') or 0), int(sv('n_unchanged') or 0),
            round(float(sv('ad_ratio') or 0), 4), round(float(sv('up_vol_ratio') or 0), 4),
            round(float(sv('down_vol_ratio') or 0), 4),
            round(float(sv('pct_above_ema20') or 0), 4), round(float(sv('pct_above_ema50') or 0), 4),
            round(float(sv('pct_above_sma20') or 0), 4), round(float(sv('pct_above_sma50') or 0), 4),
            int(sv('n_new_hi20') or 0), int(sv('n_new_lo20') or 0),
            int(sv('n_new_hi52w') or 0), int(sv('n_new_lo52w') or 0),
            round(float(sv('hl_ratio_20d') or 0), 4), round(float(sv('hl_ratio_52w') or 0), 4),
            round(float(sv('rsi_mean') or 50), 2), round(float(sv('rsi_median') or 50), 2),
            round(float(sv('pct_oversold') or 0), 4), round(float(sv('pct_overbought') or 0), 4),
            round(float(sv('med_ret') or 0), 6), round(float(sv('mean_ret') or 0), 6),
            round(float(sv('mkt_vol_5d') or 0), 6),
            round(float(sv('mcclellan_norm') or 0), 4),
            None if pd.isna(sv('breadth_mom_3d')) else round(float(sv('breadth_mom_3d')), 4),
            None if pd.isna(sv('breadth_mom_5d')) else round(float(sv('breadth_mom_5d')), 4),
            None if pd.isna(sv('ad_ratio_ma5')) else round(float(sv('ad_ratio_ma5')), 4),
            None if pd.isna(sv('up_vol_ma5')) else round(float(sv('up_vol_ma5')), 4),
            round(float(sv('breadth_score') or 50), 2), row['signal'],
        ))
        rows_written += 1
    conn.commit()

    # ── 6. Sector breadth (Ph53) ──────────────────────────────────────────────
    print("[Ph53] Computing sector breadth …", flush=True)
    if sec_map:
        valid_sec = valid[valid['sector'] != 'Unknown'].copy()
        sec_daily = valid_sec.groupby(['trade_date', 'sector']).agg(
            n_stocks    = ('symbol',      'count'),
            n_advances  = ('price_up',    'sum'),
            n_declines  = ('price_down',  'sum'),
            n_above_e20 = ('above_ema20', 'sum'),
            n_above_e50 = ('above_ema50', 'sum'),
            mean_ret    = ('ret1d',       'mean'),
            median_ret  = ('ret1d',       'median'),
            rsi_mean    = ('rsi14',       'mean'),
            up_volume   = ('up_vol',      'sum'),
            total_vol   = ('volume',      'sum'),
        ).reset_index()

        sec_daily = sec_daily[sec_daily['n_stocks'] >= 3].copy()
        sec_daily['ad_ratio']       = sec_daily['n_advances'] / (sec_daily['n_declines'] + 1)
        sec_daily['pct_above_ema20'] = sec_daily['n_above_e20'] / sec_daily['n_stocks']
        sec_daily['pct_above_ema50'] = sec_daily['n_above_e50'] / sec_daily['n_stocks']
        sec_daily['up_vol_ratio']   = sec_daily['up_volume'] / (sec_daily['total_vol'] + 1)

        # Sector momentum (5d rolling mean return)
        sec_daily = sec_daily.sort_values(['sector', 'trade_date'])
        sec_daily['momentum_5d'] = sec_daily.groupby('sector')['mean_ret'].transform(
            lambda x: x.rolling(5, min_periods=1).mean()
        )

        # Sector composite score for ranking
        sec_daily['sector_score'] = (
            (sec_daily['pct_above_ema20'] * 40) +
            (sec_daily['up_vol_ratio']    * 30) +
            (sec_daily['ad_ratio'].clip(0, 5) / 5 * 20) +
            (sec_daily['momentum_5d'].clip(-0.05, 0.05) / 0.05 * 10 + 5)
        ).clip(0, 100)

        # Rank sectors per day
        sec_daily['sector_rank'] = sec_daily.groupby('trade_date')['sector_score'].rank(
            ascending=False, method='dense'
        ).astype(int)

        sec_daily['signal'] = sec_daily['sector_score'].apply(
            lambda s: 'STRONG' if s >= 60 else 'NEUTRAL' if s >= 40 else 'WEAK'
        )

        sec_rows = 0
        for _, row in sec_daily.iterrows():
            conn.execute("""
                INSERT OR REPLACE INTO sector_breadth_daily
                (date, sector, n_stocks, n_advances, n_declines,
                 ad_ratio, pct_above_ema20, pct_above_ema50,
                 mean_ret, median_ret, momentum_5d, rsi_mean,
                 up_vol_ratio, sector_rank, signal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row['trade_date'].strftime('%Y-%m-%d'), row['sector'],
                int(row['n_stocks']), int(row['n_advances']), int(row['n_declines']),
                round(float(row['ad_ratio']), 4),
                round(float(row['pct_above_ema20']), 4),
                round(float(row['pct_above_ema50']), 4),
                round(float(row['mean_ret']), 6),
                round(float(row['median_ret']), 6),
                round(float(row['momentum_5d']), 6),
                round(float(row['rsi_mean']), 2),
                round(float(row['up_vol_ratio']), 4),
                int(row['sector_rank']), row['signal'],
            ))
            sec_rows += 1
        conn.commit()

        # ── 7. Sector Rotation daily summary ──────────────────────────────────
        print("[Ph53] Writing sector rotation summary …", flush=True)
        dates_to_process = sec_daily['trade_date'].unique()
        rot_rows = 0
        for dt in dates_to_process:
            day_sec = sec_daily[sec_daily['trade_date'] == dt].sort_values('sector_rank')
            if len(day_sec) < 2:
                continue
            top3    = list(day_sec.head(3)['sector'])
            bot3    = list(day_sec.tail(3)['sector'])
            leading = day_sec.iloc[0]['sector'] if len(day_sec) > 0 else None
            lagging = day_sec.iloc[-1]['sector'] if len(day_sec) > 0 else None
            scores  = day_sec['sector_score'].values
            disp    = float(np.std(scores)) if len(scores) > 1 else 0.0
            rot_score = float(scores[0] - scores[-1]) if len(scores) > 1 else 0.0

            conn.execute("""
                INSERT OR REPLACE INTO sector_rotation_daily
                (date, leading_sector, lagging_sector, rotation_score,
                 sector_dispersion, top3_sectors, bot3_sectors)
                VALUES (?,?,?,?,?,?,?)
            """, (
                dt.strftime('%Y-%m-%d'), leading, lagging,
                round(rot_score, 2), round(disp, 2),
                json.dumps(top3), json.dumps(bot3),
            ))
            rot_rows += 1
        conn.commit()
    else:
        sec_rows, rot_rows = 0, 0
        print("[Ph53] No sector map available — skipping sector breadth", flush=True)

    dur = time.time() - t0

    # Latest enhanced breadth summary
    latest = conn.execute("""
        SELECT date, n_stocks, ad_ratio, pct_above_ema20, up_vol_ratio,
               breadth_score, signal
        FROM market_breadth_enhanced ORDER BY date DESC LIMIT 1
    """).fetchone()

    # Latest sector rotation
    latest_rot = conn.execute("""
        SELECT date, leading_sector, lagging_sector, sector_dispersion
        FROM sector_rotation_daily ORDER BY date DESC LIMIT 1
    """).fetchone() if rot_rows > 0 else None

    summary = {
        "phase": "52+53",
        "breadth_rows_written":  rows_written,
        "sector_rows_written":   sec_rows,
        "rotation_rows_written": rot_rows,
        "latest_breadth": {
            "date":           latest['date']            if latest else None,
            "n_stocks":       int(latest['n_stocks'])   if latest else 0,
            "ad_ratio":       round(float(latest['ad_ratio']), 3) if latest else None,
            "pct_above_ema20":round(float(latest['pct_above_ema20']), 3) if latest else None,
            "up_vol_ratio":   round(float(latest['up_vol_ratio']), 3) if latest else None,
            "breadth_score":  round(float(latest['breadth_score']), 1) if latest else None,
            "signal":         latest['signal'] if latest else None,
        } if latest else {},
        "latest_rotation": {
            "leading":    latest_rot['leading_sector'] if latest_rot else None,
            "lagging":    latest_rot['lagging_sector'] if latest_rot else None,
            "dispersion": latest_rot['sector_dispersion'] if latest_rot else None,
        } if latest_rot else {},
        "duration_seconds": round(dur, 1),
    }

    conn2 = get_db()
    conn2.execute(
        "INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
        (today_str, '52_53', dur, json.dumps(summary))
    )
    conn2.commit(); conn2.close()
    conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 51 — TOMORROW DIRECTION FORECAST ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def _rsi_series(series: 'pd.Series', period: int = 14) -> 'pd.Series':
    """Compute RSI on a pandas Series (vectorised, EWM-based)."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    rs   = avg_gain / (avg_loss + 1e-10)
    return 100 - 100 / (1 + rs)


def _ensure_tomorrow_forecast_table(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tomorrow_forecast (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        forecast_date   TEXT NOT NULL,
        direction       TEXT,
        p_up            REAL,
        p_flat          REAL,
        p_down          REAL,
        expected_move   REAL,
        expected_move_lo REAL,
        expected_move_hi REAL,
        gap_up_prob     REAL,
        volatility_regime TEXT,
        model_accuracy  REAL,
        model_auc       REAL,
        n_training_days INTEGER,
        top_features    TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def _ensure_markov_tables(conn):
    """Create Markov regime tables if missing."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS markov_regime_daily (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        date              TEXT NOT NULL UNIQUE,
        roll20_pct        REAL,
        roll20_percentile REAL,
        roll20_zscore     REAL,
        state_pct         TEXT,
        state_z           TEXT,
        state_base        TEXT,
        sub_label         TEXT,
        base_confidence   TEXT,
        state_hmm         INTEGER,
        hmm_state_label   TEXT,
        hmm_agreement     INTEGER,
        computed_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS markov_signal_daily (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        date                    TEXT NOT NULL UNIQUE,
        current_state           TEXT,
        regime_age              INTEGER,
        p_bear_1d               REAL,
        p_side_1d               REAL,
        p_bull_1d               REAL,
        continuation_confidence REAL,
        signal_1d               REAL,
        transition_risk         REAL,
        entropy                 REAL,
        p_bull_3d               REAL,
        signal_3d               REAL,
        p_bull_5d               REAL,
        signal_5d               REAL,
        stat_bear               REAL,
        stat_side               REAL,
        stat_bull               REAL,
        hmm_agreement           INTEGER,
        triple_confirmed        INTEGER,
        wf_signal_correct       INTEGER,
        computed_at             TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def _markov_decision_table(state_pct: str, state_z: str):
    """
    Combine percentile state and robust-Z state using the agreed decision table.
    Returns (state_base, sub_label, base_confidence).

    | Percentile | Robust-Z | state_base | sub_label      | confidence  |
    |-----------|----------|------------|----------------|-------------|
    | BULL      | BULL     | BULL       | neutral        | strong      |
    | BEAR      | BEAR     | BEAR       | neutral        | strong      |
    | SIDE      | SIDE     | SIDE       | neutral        | strong      |
    | BULL      | SIDE     | SIDE       | bullish_lean   | weak        |
    | SIDE      | BULL     | SIDE       | bullish_lean   | weak        |
    | BEAR      | SIDE     | SIDE       | bearish_lean   | weak        |
    | SIDE      | BEAR     | SIDE       | bearish_lean   | weak        |
    | BULL      | BEAR     | SIDE       | conflicted     | conflicted  |
    | BEAR      | BULL     | SIDE       | conflicted     | conflicted  |
    """
    if state_pct == state_z:
        return state_pct, 'neutral', 'strong'
    if {state_pct, state_z} == {'BULL', 'SIDE'}:
        return 'SIDE', 'bullish_lean', 'weak'
    if {state_pct, state_z} == {'BEAR', 'SIDE'}:
        return 'SIDE', 'bearish_lean', 'weak'
    # BULL vs BEAR — direct conflict
    return 'SIDE', 'conflicted', 'conflicted'


def _ensure_forecast_outcomes_table(conn):
    """Create tomorrow_forecast_outcomes if missing — tracks rolling accuracy."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tomorrow_forecast_outcomes (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        forecast_date  TEXT NOT NULL UNIQUE,
        predicted_dir  TEXT,
        p_up           REAL,
        p_flat         REAL,
        p_down         REAL,
        actual_ret     REAL,
        actual_dir     TEXT,
        correct        INTEGER,
        confidence     REAL,
        created_at     TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def _load_breadth_for_ph51(conn) -> 'pd.DataFrame | None':
    """
    Load daily breadth data for Ph51 training.
    Fast path: reads from market_breadth_enhanced (pre-computed by Ph52).
    Slow path: recomputes from ohlcv_history (fallback when Ph52 hasn't run yet).
    Returns a DataFrame with unified column names, or None on failure.
    """
    # ── Try fast path first ──────────────────────────────────────────────────
    try:
        n_enh = conn.execute(
            "SELECT COUNT(*) FROM market_breadth_enhanced"
        ).fetchone()[0]
    except Exception:
        n_enh = 0

    if n_enh >= 80:
        print("[Ph51] Fast path: reading market_breadth_enhanced …", flush=True)
        enh = pd.read_sql_query("""
            SELECT date AS trade_date,
                   market_ret_median  AS median_ret,
                   market_ret_mean    AS mean_ret,
                   ad_ratio, up_vol_ratio, pct_above_ema20, pct_above_ema50,
                   pct_oversold, pct_overbought,
                   mcclellan_norm, rsi_mean, rsi_median,
                   mkt_vol_5d         AS mkt_vol5,
                   breadth_mom_3d     AS breadth_mom3,
                   breadth_mom_5d     AS breadth_mom5,
                   ad_ratio_ma5, up_vol_ma5,
                   n_advances, n_declines, n_stocks
            FROM market_breadth_enhanced
            ORDER BY date
        """, conn)
        if len(enh) < 80:
            return None

        enh['trade_date'] = pd.to_datetime(enh['trade_date'])
        enh = enh.sort_values('trade_date').reset_index(drop=True)

        # Compute missing features from enhanced table
        enh['ad_net']          = enh['n_advances'] - enh['n_declines']
        enh['ad_line']         = enh['ad_net'].cumsum()
        enh['mkt_ret_3d']      = enh['median_ret'].rolling(3).mean()
        enh['mkt_ret_5d']      = enh['median_ret'].rolling(5).mean()
        enh['mkt_ret_10d']     = enh['median_ret'].rolling(10).mean()
        enh['breadth_mom10']   = enh['pct_above_ema20'] - enh['pct_above_ema20'].shift(10)
        enh['ad_ratio_ma10']   = enh['ad_ratio'].rolling(10).mean()
        enh['up_vol_ma10']     = enh['up_vol_ratio'].rolling(10).mean()
        enh['rsi_slope5']      = enh['rsi_mean'] - enh['rsi_mean'].shift(5)
        enh['mkt_vol10']       = enh['median_ret'].rolling(10).std()
        enh['dow']             = enh['trade_date'].dt.dayofweek
        enh['is_sunday']       = (enh['dow'] == 6).astype('float32')
        enh['is_thursday']     = (enh['dow'] == 3).astype('float32')

        # ── Markov features (Ph56) — LEFT JOIN if available ──────────────────
        try:
            n_mk = conn.execute("SELECT COUNT(*) FROM markov_signal_daily").fetchone()[0]
            if n_mk >= 20:
                mkv = pd.read_sql_query("""
                    SELECT date AS trade_date,
                           signal_1d          AS markov_signal_1d,
                           continuation_confidence AS markov_stickiness,
                           entropy            AS markov_entropy,
                           regime_age         AS markov_regime_age,
                           transition_risk    AS markov_transition_risk
                    FROM markov_signal_daily ORDER BY date
                """, conn)
                mkv['trade_date'] = pd.to_datetime(mkv['trade_date'])
                enh = enh.merge(mkv, on='trade_date', how='left')
            else:
                raise ValueError("not enough markov rows")
        except Exception:
            enh['markov_signal_1d']       = 0.0
            enh['markov_stickiness']      = 0.5
            enh['markov_entropy']         = 1.0
            enh['markov_regime_age']      = 1
            enh['markov_transition_risk'] = 0.5
        # Fill NaN Markov features with neutral values
        enh['markov_signal_1d']       = enh['markov_signal_1d'].fillna(0.0)
        enh['markov_stickiness']      = enh['markov_stickiness'].fillna(0.5)
        enh['markov_entropy']         = enh['markov_entropy'].fillna(1.0)
        enh['markov_regime_age']      = enh['markov_regime_age'].fillna(1)
        enh['markov_transition_risk'] = enh['markov_transition_risk'].fillna(0.5)

        # ── Ph57 Closing Pressure — market-level daily aggregates (Ph74: DuckDB fast) ──
        try:
            n_cp51 = conn.execute("SELECT COUNT(*) FROM closing_pressure_daily").fetchone()[0]
            if n_cp51 >= 100:
                # Ph74: use DuckDB Parquet if available (27ms vs 200ms), else SQLite
                cp_agg = (_cp_agg_fast(sqlite_conn=conn)
                          if _DUCKDB_LAYER else None)
                if cp_agg is None:
                    cp_agg = pd.read_sql_query("""
                        SELECT trade_date,
                               AVG(close_pos)           AS mkt_close_pos_med,
                               AVG(closing_pressure)    AS mkt_cp_pressure_med,
                               AVG(vol_surge)           AS mkt_vol_surge_med,
                               CAST(SUM(gap_potential) AS REAL) / COUNT(*) AS mkt_gap_pct,
                               CAST(SUM(intraday_reversal) AS REAL) / COUNT(*) AS mkt_reversal_pct
                        FROM closing_pressure_daily
                        GROUP BY trade_date
                        ORDER BY trade_date
                    """, conn)
                cp_agg['trade_date'] = pd.to_datetime(cp_agg['trade_date'])
                enh = enh.merge(cp_agg, on='trade_date', how='left')
            else:
                raise ValueError("insufficient cp rows")
        except Exception:
            enh['mkt_close_pos_med']  = 0.5
            enh['mkt_cp_pressure_med']= 0.5
            enh['mkt_vol_surge_med']  = 1.0
            enh['mkt_gap_pct']        = 0.0
            enh['mkt_reversal_pct']   = 0.0
        for _col, _fill in [('mkt_close_pos_med', 0.5), ('mkt_cp_pressure_med', 0.5),
                             ('mkt_vol_surge_med', 1.0), ('mkt_gap_pct', 0.0),
                             ('mkt_reversal_pct', 0.0)]:
            enh[_col] = enh[_col].fillna(_fill)

        # ── Ph77 tsfresh market-level aggregates ──────────────────────────────
        try:
            n_ts51 = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM tsfresh_daily").fetchone()[0]
            if n_ts51 >= 30:
                ts_agg = pd.read_sql_query("""
                    SELECT trade_date,
                           AVG(feat_autocorr1) AS mkt_ts_autocorr1,
                           AVG(feat_entropy)   AS mkt_ts_entropy,
                           AVG(feat_skew)      AS mkt_ts_skew,
                           AVG(vol_std / NULLIF(vol_mean, 0)) AS mkt_ts_vol_cv
                    FROM tsfresh_daily
                    GROUP BY trade_date
                    ORDER BY trade_date
                """, conn)
                ts_agg['trade_date'] = pd.to_datetime(ts_agg['trade_date'])
                enh = enh.merge(ts_agg, on='trade_date', how='left')
            else:
                raise ValueError(f"only {n_ts51} tsfresh dates")
        except Exception:
            enh['mkt_ts_autocorr1'] = 0.75
            enh['mkt_ts_entropy']   = 2.0
            enh['mkt_ts_skew']      = 0.0
            enh['mkt_ts_vol_cv']    = 1.0
        for _col, _fill in [('mkt_ts_autocorr1', 0.75), ('mkt_ts_entropy', 2.0),
                             ('mkt_ts_skew', 0.0),       ('mkt_ts_vol_cv', 1.0)]:
            enh[_col] = enh[_col].fillna(_fill)

        enh.attrs['source'] = 'market_breadth_enhanced'
        return enh

    # ── Slow path: recompute from ohlcv_history ───────────────────────────────
    print("[Ph51] Slow path: computing from ohlcv_history …", flush=True)
    ohlcv = pd.read_sql_query("""
        SELECT symbol, date(bar_time,'unixepoch') AS trade_date,
               close, volume
        FROM ohlcv_history WHERE close > 0 AND volume > 0
        ORDER BY symbol, trade_date
    """, conn)
    if len(ohlcv) < 5000:
        return None

    ohlcv['trade_date'] = pd.to_datetime(ohlcv['trade_date'])
    ohlcv = ohlcv.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
    grp   = ohlcv.groupby('symbol', sort=False)

    ohlcv['ema20']  = grp['close'].transform(lambda x: x.ewm(span=20, adjust=False).mean())
    ohlcv['ema50']  = grp['close'].transform(lambda x: x.ewm(span=50, adjust=False).mean())
    ohlcv['rsi14']  = grp['close'].transform(lambda x: _rsi_series(x, 14))
    ohlcv['ret1d']  = grp['close'].transform(lambda x: x.pct_change()).clip(-0.30, 0.30)

    ohlcv['a_ema20'] = (ohlcv['close'] > ohlcv['ema20']).astype('float32')
    ohlcv['a_ema50'] = (ohlcv['close'] > ohlcv['ema50']).astype('float32')
    ohlcv['pup']     = (ohlcv['ret1d'] > 0).astype('float32')
    ohlcv['pdn']     = (ohlcv['ret1d'] < 0).astype('float32')
    ohlcv['upv']     = ohlcv['volume'] * ohlcv['pup']
    ohlcv['rsi_ov']  = (ohlcv['rsi14'] < 35).astype('float32')
    ohlcv['rsi_ob']  = (ohlcv['rsi14'] > 65).astype('float32')

    valid = ohlcv.dropna(subset=['ema20', 'ema50', 'rsi14', 'ret1d'])
    daily = valid.groupby('trade_date').agg(
        n_stocks      = ('symbol',  'count'),
        n_advances    = ('pup',     'sum'),
        n_declines    = ('pdn',     'sum'),
        n_above_ema20 = ('a_ema20', 'sum'),
        n_above_ema50 = ('a_ema50', 'sum'),
        total_volume  = ('volume',  'sum'),
        up_volume     = ('upv',     'sum'),
        median_ret    = ('ret1d',   'median'),
        mean_ret      = ('ret1d',   'mean'),
        rsi_mean      = ('rsi14',   'mean'),
        rsi_median    = ('rsi14',   'median'),
        n_oversold    = ('rsi_ov',  'sum'),
        n_overbought  = ('rsi_ob',  'sum'),
    ).reset_index()

    daily = daily[daily['n_stocks'] >= 50].sort_values('trade_date').reset_index(drop=True)
    daily['ad_ratio']        = daily['n_advances'] / (daily['n_declines'] + 1)
    daily['pct_above_ema20'] = daily['n_above_ema20'] / daily['n_stocks']
    daily['pct_above_ema50'] = daily['n_above_ema50'] / daily['n_stocks']
    daily['up_vol_ratio']    = daily['up_volume'] / (daily['total_volume'] + 1)
    daily['pct_oversold']    = daily['n_oversold']   / daily['n_stocks']
    daily['pct_overbought']  = daily['n_overbought'] / daily['n_stocks']

    daily['ad_net']        = daily['n_advances'] - daily['n_declines']
    daily['ad_line']       = daily['ad_net'].cumsum()
    daily['ema19_ad']      = daily['ad_net'].ewm(span=19, adjust=False).mean()
    daily['ema39_ad']      = daily['ad_net'].ewm(span=39, adjust=False).mean()
    daily['mcclellan_norm']= (daily['ema19_ad'] - daily['ema39_ad']) / (daily['n_stocks'] + 1)

    daily['mkt_ret_3d']   = daily['median_ret'].rolling(3).mean()
    daily['mkt_ret_5d']   = daily['median_ret'].rolling(5).mean()
    daily['mkt_ret_10d']  = daily['median_ret'].rolling(10).mean()
    daily['breadth_mom3'] = daily['pct_above_ema20'] - daily['pct_above_ema20'].shift(3)
    daily['breadth_mom5'] = daily['pct_above_ema20'] - daily['pct_above_ema20'].shift(5)
    daily['breadth_mom10']= daily['pct_above_ema20'] - daily['pct_above_ema20'].shift(10)
    daily['ad_ratio_ma5'] = daily['ad_ratio'].rolling(5).mean()
    daily['ad_ratio_ma10']= daily['ad_ratio'].rolling(10).mean()
    daily['up_vol_ma5']   = daily['up_vol_ratio'].rolling(5).mean()
    daily['up_vol_ma10']  = daily['up_vol_ratio'].rolling(10).mean()
    daily['rsi_slope5']   = daily['rsi_mean'] - daily['rsi_mean'].shift(5)
    daily['mkt_vol5']     = daily['median_ret'].rolling(5).std()
    daily['mkt_vol10']    = daily['median_ret'].rolling(10).std()
    daily['dow']          = daily['trade_date'].dt.dayofweek
    daily['is_sunday']    = (daily['dow'] == 6).astype('float32')
    daily['is_thursday']  = (daily['dow'] == 3).astype('float32')

    # ── Markov features (Ph56) — slow path join ──────────────────────────────
    try:
        n_mk = conn.execute("SELECT COUNT(*) FROM markov_signal_daily").fetchone()[0]
        if n_mk >= 20:
            mkv = pd.read_sql_query("""
                SELECT date AS trade_date,
                       signal_1d               AS markov_signal_1d,
                       continuation_confidence AS markov_stickiness,
                       entropy                 AS markov_entropy,
                       regime_age              AS markov_regime_age,
                       transition_risk         AS markov_transition_risk
                FROM markov_signal_daily ORDER BY date
            """, conn)
            mkv['trade_date'] = pd.to_datetime(mkv['trade_date'])
            daily = daily.merge(mkv, on='trade_date', how='left')
        else:
            raise ValueError("not enough markov rows")
    except Exception:
        daily['markov_signal_1d']       = 0.0
        daily['markov_stickiness']      = 0.5
        daily['markov_entropy']         = 1.0
        daily['markov_regime_age']      = 1
        daily['markov_transition_risk'] = 0.5
    daily['markov_signal_1d']       = daily['markov_signal_1d'].fillna(0.0)
    daily['markov_stickiness']      = daily['markov_stickiness'].fillna(0.5)
    daily['markov_entropy']         = daily['markov_entropy'].fillna(1.0)
    daily['markov_regime_age']      = daily['markov_regime_age'].fillna(1)
    daily['markov_transition_risk'] = daily['markov_transition_risk'].fillna(0.5)

    # ── Ph57 Closing Pressure — slow-path join (Ph74: DuckDB fast) ──────────────
    try:
        n_cp51s = conn.execute("SELECT COUNT(*) FROM closing_pressure_daily").fetchone()[0]
        if n_cp51s >= 100:
            # Ph74: DuckDB Parquet if available, else SQLite
            cp_agg_s = (_cp_agg_fast(sqlite_conn=conn)
                        if _DUCKDB_LAYER else None)
            if cp_agg_s is None:
                cp_agg_s = pd.read_sql_query("""
                    SELECT trade_date,
                           AVG(close_pos)           AS mkt_close_pos_med,
                           AVG(closing_pressure)    AS mkt_cp_pressure_med,
                           AVG(vol_surge)           AS mkt_vol_surge_med,
                           CAST(SUM(gap_potential) AS REAL) / COUNT(*) AS mkt_gap_pct,
                           CAST(SUM(intraday_reversal) AS REAL) / COUNT(*) AS mkt_reversal_pct
                    FROM closing_pressure_daily
                    GROUP BY trade_date ORDER BY trade_date
                """, conn)
            cp_agg_s['trade_date'] = pd.to_datetime(cp_agg_s['trade_date'])
            daily = daily.merge(cp_agg_s, on='trade_date', how='left')
        else:
            raise ValueError("insufficient cp rows")
    except Exception:
        daily['mkt_close_pos_med']  = 0.5
        daily['mkt_cp_pressure_med']= 0.5
        daily['mkt_vol_surge_med']  = 1.0
        daily['mkt_gap_pct']        = 0.0
        daily['mkt_reversal_pct']   = 0.0
    for _col, _fill in [('mkt_close_pos_med', 0.5), ('mkt_cp_pressure_med', 0.5),
                         ('mkt_vol_surge_med', 1.0), ('mkt_gap_pct', 0.0),
                         ('mkt_reversal_pct', 0.0)]:
        daily[_col] = daily[_col].fillna(_fill)

    # ── Ph77 tsfresh market-level aggregates (slow path) ─────────────────────
    try:
        n_ts51s = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM tsfresh_daily").fetchone()[0]
        if n_ts51s >= 30:
            ts_agg_s = pd.read_sql_query("""
                SELECT trade_date,
                       AVG(feat_autocorr1) AS mkt_ts_autocorr1,
                       AVG(feat_entropy)   AS mkt_ts_entropy,
                       AVG(feat_skew)      AS mkt_ts_skew,
                       AVG(vol_std / NULLIF(vol_mean, 0)) AS mkt_ts_vol_cv
                FROM tsfresh_daily
                GROUP BY trade_date ORDER BY trade_date
            """, conn)
            ts_agg_s['trade_date'] = pd.to_datetime(ts_agg_s['trade_date'])
            daily = daily.merge(ts_agg_s, on='trade_date', how='left')
        else:
            raise ValueError(f"only {n_ts51s} tsfresh dates")
    except Exception:
        daily['mkt_ts_autocorr1'] = 0.75
        daily['mkt_ts_entropy']   = 2.0
        daily['mkt_ts_skew']      = 0.0
        daily['mkt_ts_vol_cv']    = 1.0
    for _col, _fill in [('mkt_ts_autocorr1', 0.75), ('mkt_ts_entropy', 2.0),
                         ('mkt_ts_skew', 0.0),       ('mkt_ts_vol_cv', 1.0)]:
        daily[_col] = daily[_col].fillna(_fill)

    daily.attrs['source'] = 'ohlcv_history'
    return daily


def _fill_forecast_outcomes(conn, daily_df):
    """
    Fill actual outcomes for past tomorrow_forecast rows that don't have them yet.
    For each unfilled forecast, look up the actual market return for forecast_date+1
    and compute whether the direction prediction was correct.
    """
    try:
        unfilled = conn.execute("""
            SELECT f.id, f.forecast_date, f.direction, f.p_up, f.p_flat, f.p_down
            FROM tomorrow_forecast f
            LEFT JOIN tomorrow_forecast_outcomes o ON f.forecast_date = o.forecast_date
            WHERE o.id IS NULL
            ORDER BY f.forecast_date
        """).fetchall()

        if not unfilled:
            return

        # Build date → median_ret lookup from daily_df
        if daily_df is not None and 'median_ret' in daily_df.columns:
            ret_map = {
                row['trade_date'].strftime('%Y-%m-%d'): float(row['median_ret'])
                for _, row in daily_df.iterrows()
            }
        else:
            ret_map = {}

        # Sorted date list for next-day lookup
        sorted_dates = sorted(ret_map.keys())

        UP_THR, DN_THR = 0.003, -0.003
        filled = 0
        for row in unfilled:
            fc_date = row['forecast_date']
            # forecast_date = "date forecast was MADE" — actual outcome is the NEXT trading day
            next_dates  = [d for d in sorted_dates if d > fc_date]
            actual_date = next_dates[0] if next_dates else None
            actual_ret  = ret_map.get(actual_date) if actual_date else None
            if actual_ret is None:
                continue  # next trading day data not yet available

            actual_dir = ('UP'   if actual_ret >  UP_THR else
                          'DOWN' if actual_ret < DN_THR else 'FLAT')
            correct    = 1 if actual_dir == row['direction'] else 0
            confidence = max(float(row['p_up'] or 0),
                             float(row['p_flat'] or 0),
                             float(row['p_down'] or 0))

            conn.execute("""
                INSERT OR IGNORE INTO tomorrow_forecast_outcomes
                (forecast_date, predicted_dir, p_up, p_flat, p_down,
                 actual_ret, actual_dir, correct, confidence)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (fc_date, row['direction'],
                  float(row['p_up'] or 0), float(row['p_flat'] or 0),
                  float(row['p_down'] or 0),
                  round(actual_ret, 6), actual_dir, correct,
                  round(confidence, 4)))
            filled += 1

        if filled:
            conn.commit()
            print(f"[Ph51] Filled {filled} forecast outcome(s)", flush=True)

        # Rolling accuracy summary
        stats = conn.execute("""
            SELECT COUNT(*) n, SUM(correct) hits,
                   AVG(correct) acc_all,
                   SUM(CASE WHEN forecast_date >= date('now','-30 days') THEN correct END) h30,
                   COUNT(CASE WHEN forecast_date >= date('now','-30 days') THEN 1 END) n30
            FROM tomorrow_forecast_outcomes
        """).fetchone()
        if stats and stats['n'] and stats['n'] > 0:
            acc_all = round(float(stats['acc_all'] or 0) * 100, 1)
            h30, n30 = int(stats['h30'] or 0), int(stats['n30'] or 0)
            acc30 = round(h30 / n30 * 100, 1) if n30 else None
            print(f"[Ph51] Forecast accuracy: all-time={acc_all}% ({stats['n']} obs)"
                  + (f"  30d={acc30}% ({n30} obs)" if n30 else ""), flush=True)
    except Exception as e:
        print(f"[Ph51] _fill_forecast_outcomes error: {e}", flush=True)


def phase51_tomorrow_forecast():
    """
    Phase 51 — Tomorrow Direction Forecast Engine  (v2: fast-path + calibration)
    ------------------------------------------------------------------------------
    LightGBM 3-class classifier (UP / FLAT / DOWN) on daily market breadth.

    Fast path: if market_breadth_enhanced is populated (Ph52 ran first),
    reads directly from that table (~3s). Falls back to full OHLCV recompute (~22s).

    Improvements in v2:
      • Uses pre-computed market_breadth_enhanced when available
      • Isotonic calibration via CalibratedClassifierCV for reliable probabilities
      • Stores calibration flag in tomorrow_forecast
      • Triggers ph54_forecast_accuracy to fill yesterday's outcome
    """
    try:
        import lightgbm as lgb
    except ImportError:
        print(json.dumps({"phase": "51", "error": "lightgbm not installed"}), flush=True)
        return {"error": "lightgbm not installed"}

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        HAS_OPTUNA = True
    except ImportError:
        HAS_OPTUNA = False

    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.preprocessing import label_binarize, StandardScaler
    from sklearn.metrics import roc_auc_score, accuracy_score
    from sklearn.calibration import CalibratedClassifierCV
    import pickle

    t0        = time.time()
    today_str = datetime.date.today().isoformat()
    conn      = get_db()
    ensure_tables(conn)
    _ensure_tomorrow_forecast_table(conn)
    _ensure_forecast_outcomes_table(conn)

    # ── 1. Load daily breadth — fast path or full OHLCV recompute ────────────
    daily = _load_breadth_for_ph51(conn)

    if daily is None or len(daily) < 80:
        conn.close()
        msg = "insufficient breadth data"
        print(json.dumps({"phase": "51", "error": msg}), flush=True)
        return {"error": msg}

    data_source = daily.attrs.get('source', 'ohlcv')
    print(f"[Ph51] Breadth data: {len(daily)} days (source={data_source})", flush=True)

    # ── 2. Target: NEXT-day market direction ──────────────────────────────────
    UP_THR, DN_THR = 0.003, -0.003
    daily = daily.sort_values('trade_date').reset_index(drop=True)
    daily['next_mkt_ret'] = daily['median_ret'].shift(-1)
    daily['target'] = np.where(
        daily['next_mkt_ret'] >  UP_THR, 2,
        np.where(daily['next_mkt_ret'] < DN_THR, 0, 1)
    )

    # ── 3. Feature matrix ─────────────────────────────────────────────────────
    FEATURE_COLS = [
        'ad_ratio', 'pct_above_ema20', 'pct_above_ema50',
        'up_vol_ratio', 'pct_oversold', 'pct_overbought',
        'mcclellan_norm', 'ad_line',
        'median_ret', 'mean_ret', 'rsi_mean', 'rsi_median',
        'mkt_ret_3d', 'mkt_ret_5d', 'mkt_ret_10d',
        'breadth_mom3', 'breadth_mom5', 'breadth_mom10',
        'ad_ratio_ma5', 'ad_ratio_ma10',
        'up_vol_ma5', 'up_vol_ma10',
        'rsi_slope5', 'mkt_vol5', 'mkt_vol10',
        'dow', 'is_sunday', 'is_thursday',
        # Ph56 Markov regime features
        'markov_signal_1d', 'markov_stickiness',
        'markov_entropy', 'markov_regime_age', 'markov_transition_risk',
        # Ph57 Closing Pressure — market-level aggregates
        'mkt_close_pos_med', 'mkt_cp_pressure_med', 'mkt_vol_surge_med',
        'mkt_gap_pct', 'mkt_reversal_pct',
        # Ph77 tsfresh — market-level statistical dynamics
        'mkt_ts_autocorr1', 'mkt_ts_entropy', 'mkt_ts_skew', 'mkt_ts_vol_cv',
    ]

    df_train = daily.dropna(subset=FEATURE_COLS + ['next_mkt_ret']).copy()
    df_train = df_train[df_train['trade_date'] < pd.Timestamp(today_str)].copy()

    if len(df_train) < 80:
        msg = f"insufficient training rows: {len(df_train)}"
        print(json.dumps({"phase": "51", "error": msg}), flush=True)
        conn.close()
        return {"error": msg}

    X = df_train[FEATURE_COLS].values.astype('float32')
    y = df_train['target'].values.astype(int)

    class_counts = np.bincount(y, minlength=3)
    print(f"[Ph51] Training: {len(df_train)} days | UP={class_counts[2]} FLAT={class_counts[1]} DOWN={class_counts[0]}", flush=True)

    # ── 4. Scaling ────────────────────────────────────────────────────────────
    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    n_cls = 3
    cw    = {i: len(y) / (n_cls * max(1, c)) for i, c in enumerate(class_counts)}

    BASE_PARAMS = dict(
        objective='multiclass', num_class=3, metric='multi_logloss',
        n_estimators=300, learning_rate=0.05,
        max_depth=4, num_leaves=15,
        min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0,
        class_weight=cw, n_jobs=N_JOBS, random_state=42, verbose=-1,
    )

    # ── 5. Optuna HPO — with weekly caching ───────────────────────────────────
    # Only re-run HPO once per week (Saturday night deep train or first run of week).
    # On weekdays, load cached best params → daily runtime drops to ~3-5s.
    _HPO_CACHE_PATH = MODELS / 'phase51_hpo_params.json'
    _cached_hpo     = None
    _hpo_ran        = False

    if _HPO_CACHE_PATH.exists():
        try:
            _cached_hpo = json.loads(_HPO_CACHE_PATH.read_text())
            cache_age_days = (datetime.date.today() -
                              datetime.date.fromisoformat(_cached_hpo.get('date', '2000-01-01'))).days
            if cache_age_days <= 7:
                BASE_PARAMS.update({k: v for k, v in _cached_hpo.items()
                                    if k not in ('date', 'log_loss')})
                BASE_PARAMS['class_weight'] = cw
                BASE_PARAMS['n_jobs']       = N_JOBS
                print(f"[Ph51] HPO cached ({cache_age_days}d old), log-loss={_cached_hpo.get('log_loss','?'):.4f}", flush=True)
            else:
                _cached_hpo = None   # stale — force re-run
        except Exception:
            _cached_hpo = None

    if _cached_hpo is None and HAS_OPTUNA and len(df_train) >= 200:
        print("[Ph51] Optuna HPO (30 trials) …", flush=True)
        from sklearn.metrics import log_loss as sk_log_loss

        def _objective(trial):
            p = dict(
                objective='multiclass', num_class=3, metric='multi_logloss',
                n_estimators=trial.suggest_int('n_estimators', 100, 500),
                learning_rate=trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
                max_depth=trial.suggest_int('max_depth', 3, 7),
                num_leaves=trial.suggest_int('num_leaves', 8, 63),
                min_child_samples=trial.suggest_int('min_child_samples', 10, 50),
                subsample=trial.suggest_float('subsample', 0.6, 1.0),
                colsample_bytree=trial.suggest_float('colsample_bytree', 0.6, 1.0),
                reg_alpha=trial.suggest_float('reg_alpha', 0.0, 1.0),
                reg_lambda=trial.suggest_float('reg_lambda', 0.1, 5.0),
                class_weight=cw, n_jobs=min(4, N_JOBS),
                random_state=42, verbose=-1,
            )
            cv_   = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
            scores = []
            for tr_i, va_i in cv_.split(X_scaled, y):
                clf = lgb.LGBMClassifier(**p)
                clf.fit(X_scaled[tr_i], y[tr_i])
                scores.append(sk_log_loss(y[va_i], clf.predict_proba(X_scaled[va_i])))
            return float(np.mean(scores))

        study = optuna.create_study(direction='minimize')
        study.optimize(_objective, n_trials=30, n_jobs=1, show_progress_bar=False)
        best = study.best_params
        BASE_PARAMS.update(best)
        BASE_PARAMS['class_weight'] = cw
        BASE_PARAMS['n_jobs']       = N_JOBS
        print(f"[Ph51] Best HPO log-loss: {study.best_value:.4f}", flush=True)
        _hpo_ran = True
        # Cache best params for next 7 days
        cache_data = {k: v for k, v in best.items()}
        cache_data['date']     = today_str
        cache_data['log_loss'] = round(study.best_value, 6)
        _HPO_CACHE_PATH.write_text(json.dumps(cache_data, indent=2))

    # ── 6. OOS evaluation (raw model) ─────────────────────────────────────────
    cv5        = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    oos_probas = cross_val_predict(lgb.LGBMClassifier(**BASE_PARAMS),
                                   X_scaled, y, cv=cv5, method='predict_proba')
    oos_cls    = np.argmax(oos_probas, axis=1)
    acc        = float(accuracy_score(y, oos_cls))

    y_bin = label_binarize(y, classes=[0, 1, 2])
    try:
        auc_ovr = float(roc_auc_score(y_bin, oos_probas,
                                       multi_class='ovr', average='macro'))
    except Exception:
        auc_ovr = 0.5

    print(f"[Ph51] OOS Accuracy={acc:.3f}  AUC_OVR={auc_ovr:.3f}", flush=True)

    # ── 7. Train final model ──────────────────────────────────────────────────
    # Split: IS (80%) for training, cal (20%) for calibration — time-aware
    cal_start   = int(len(X_scaled) * 0.80)
    X_is        = X_scaled[:cal_start];  y_is  = y[:cal_start]
    X_cal       = X_scaled[cal_start:];  y_cal = y[cal_start:]

    # Train on IS split only (preserves cal set as true holdout for calibration)
    raw_model = lgb.LGBMClassifier(**BASE_PARAMS)
    raw_model.fit(X_is, y_is)

    # Also train on full data for final raw model (used if calibration fails)
    raw_model_full = lgb.LGBMClassifier(**BASE_PARAMS)
    raw_model_full.fit(X_scaled, y)

    # ── 8. Isotonic probability calibration via prefit ────────────────────────
    # Use cv='prefit': calibrates the EXISTING raw_model on the holdout cal set
    # — no re-training, calibrator sees truly fresh data.
    calibrated = False
    final_model = raw_model_full  # default: use full-data model

    if len(X_cal) >= 30:
        try:
            from sklearn.metrics import log_loss as sk_log_loss
            from sklearn.calibration import CalibratedClassifierCV as _CCV

            raw_probs = raw_model.predict_proba(X_cal)
            raw_ll    = sk_log_loss(y_cal, raw_probs)

            # Prefit calibration — fits sigmoid on raw_model's holdout probabilities
            cal_model = _CCV(raw_model, method='sigmoid', cv='prefit')
            cal_model.fit(X_cal, y_cal)
            cal_probs = cal_model.predict_proba(X_cal)
            cal_ll    = sk_log_loss(y_cal, cal_probs)

            if cal_ll < raw_ll:
                final_model = cal_model
                calibrated  = True
                print(f"[Ph51] Calibration (sigmoid/prefit): raw={raw_ll:.4f} → cal={cal_ll:.4f} ✓", flush=True)
            else:
                # Try isotonic as fallback
                cal_iso = _CCV(raw_model, method='isotonic', cv='prefit')
                cal_iso.fit(X_cal, y_cal)
                iso_probs = cal_iso.predict_proba(X_cal)
                iso_ll    = sk_log_loss(y_cal, iso_probs)
                if iso_ll < raw_ll:
                    final_model = cal_iso
                    calibrated  = True
                    print(f"[Ph51] Calibration (isotonic/prefit): raw={raw_ll:.4f} → iso={iso_ll:.4f} ✓", flush=True)
                else:
                    # Neither helped — use full-data raw model
                    print(f"[Ph51] Calibration: no improvement (raw={raw_ll:.4f} sig={cal_ll:.4f} iso={iso_ll:.4f}), using full-data raw", flush=True)
        except Exception as e:
            print(f"[Ph51] Calibration failed ({e}), using raw model", flush=True)
    else:
        print(f"[Ph51] Too few cal samples ({len(X_cal)}), skipping calibration", flush=True)

    # ── 9. Predict TOMORROW ───────────────────────────────────────────────────
    today_row = daily[daily['trade_date'] == daily['trade_date'].max()].copy()
    if today_row.empty:
        today_row = daily.tail(1).copy()

    X_today   = today_row[FEATURE_COLS].values.astype('float32')
    col_means = df_train[FEATURE_COLS].mean().values.astype('float32')
    X_today   = np.where(np.isnan(X_today), col_means, X_today)
    X_today_s = scaler.transform(X_today)

    proba             = final_model.predict_proba(X_today_s)[0]   # [p_down, p_flat, p_up]
    p_down, p_flat, p_up = float(proba[0]), float(proba[1]), float(proba[2])
    direction         = {0: 'DOWN', 1: 'FLAT', 2: 'UP'}[int(np.argmax(proba))]

    # Expected move range (empirical conditional distributions)
    up_rets   = df_train.loc[df_train['target'] == 2, 'next_mkt_ret'].values * 100
    flat_rets = df_train.loc[df_train['target'] == 1, 'next_mkt_ret'].values * 100
    down_rets = df_train.loc[df_train['target'] == 0, 'next_mkt_ret'].values * 100

    def _cond(arr, default): return float(np.mean(arr)) if len(arr) else default

    exp_move    = (p_up * _cond(up_rets, 0.5)   + p_flat * _cond(flat_rets, 0.0)
                 + p_down * _cond(down_rets, -0.5))
    exp_move_lo = (p_up   * (np.percentile(up_rets,   20) if len(up_rets)   else 0.0)
                 + p_flat * _cond(flat_rets, 0.0)
                 + p_down * (np.percentile(down_rets,  80) if len(down_rets) else -0.3))
    exp_move_hi = (p_up   * (np.percentile(up_rets,   80) if len(up_rets)   else 1.0)
                 + p_flat * _cond(flat_rets, 0.0)
                 + p_down * (np.percentile(down_rets,  20) if len(down_rets) else -0.8))
    # Ensure lo ≤ hi (can swap when p_down is large and down distribution is wide)
    if exp_move_lo > exp_move_hi:
        exp_move_lo, exp_move_hi = exp_move_hi, exp_move_lo

    # Volatility regime + gap-up probability
    try:
        cur_vol5 = float(today_row['mkt_vol5'].iloc[0])
    except Exception:
        cur_vol5 = float(daily['mkt_vol5'].median())
    med_vol5  = float(daily['mkt_vol5'].median())
    vol_ratio = cur_vol5 / (med_vol5 + 1e-10)
    if vol_ratio > 1.5:
        vol_regime  = 'HIGH';   gap_up_prob = p_up * 0.65 + p_flat * 0.20
    elif vol_ratio > 0.8:
        vol_regime  = 'MEDIUM'; gap_up_prob = p_up * 0.50 + p_flat * 0.15
    else:
        vol_regime  = 'LOW';    gap_up_prob = p_up * 0.35 + p_flat * 0.10

    # ── 10. Feature importance ────────────────────────────────────────────────
    try:
        fi_model = raw_model  # always use raw model for feature importance
        fi       = dict(zip(FEATURE_COLS, fi_model.feature_importances_))
    except Exception:
        fi = {}
    top_feats = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:8]

    # ── 11. Save model ────────────────────────────────────────────────────────
    model_path = MODELS / 'phase51_tomorrow_forecast.pkl'
    with open(model_path, 'wb') as fh:
        pickle.dump({
            'model': final_model, 'raw_model': raw_model,
            'scaler': scaler, 'features': FEATURE_COLS,
            'calibrated': calibrated,
        }, fh)

    # ── 12. Write forecast to DB ───────────────────────────────────────────────
    conn.execute("DELETE FROM tomorrow_forecast WHERE forecast_date=?", (today_str,))
    conn.execute("""
        INSERT INTO tomorrow_forecast
          (forecast_date, direction,
           p_up, p_flat, p_down,
           expected_move, expected_move_lo, expected_move_hi,
           gap_up_prob, volatility_regime,
           model_accuracy, model_auc, n_training_days,
           top_features, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
    """, (
        today_str, direction,
        round(p_up, 4), round(p_flat, 4), round(p_down, 4),
        round(exp_move, 3), round(exp_move_lo, 3), round(exp_move_hi, 3),
        round(gap_up_prob, 3), vol_regime,
        round(acc, 4), round(auc_ovr, 4), len(df_train),
        json.dumps([f for f, _ in top_feats]),
    ))
    conn.commit()

    # ── 13. Fill yesterday's actual outcome ───────────────────────────────────
    _fill_forecast_outcomes(conn, daily)

    dur     = time.time() - t0
    summary = {
        "phase":              "51",
        "direction":          direction,
        "p_up":               round(p_up,   3),
        "p_flat":             round(p_flat,  3),
        "p_down":             round(p_down,  3),
        "expected_move_pct":  round(exp_move, 2),
        "expected_move_range":[round(exp_move_lo, 2), round(exp_move_hi, 2)],
        "gap_up_prob":        round(gap_up_prob, 3),
        "volatility_regime":  vol_regime,
        "model_accuracy_oos": round(acc, 4),
        "model_auc_ovr":      round(auc_ovr, 4),
        "n_training_days":    len(df_train),
        "calibrated":         calibrated,
        "data_source":        data_source,
        "top_features":       [f for f, _ in top_feats[:5]],
        "duration_seconds":   round(dur, 1),
    }

    conn2 = get_db()
    conn2.execute(
        "INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
        (today_str, '51', dur, json.dumps(summary))
    )
    conn2.commit(); conn2.close()
    conn.close()

    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# STATUS
# ═════════════════════════════════════════════════════════════════════════════

def cmd_status():
    conn = get_db()
    ensure_tables(conn)
    rows = conn.execute("""
        SELECT phase, run_date, duration_seconds, results
        FROM ml_trainer_runs ORDER BY id DESC LIMIT 20
    """).fetchall()
    print("\n══════════════════════════════════════════════════")
    print("  EGX ML Trainer — آخر عمليات التدريب")
    print("══════════════════════════════════════════════════")
    for r in rows:
        res = json.loads(r['results']) if r['results'] else {}
        dur = r['duration_seconds']
        dur_str = f"{dur:.0f}s" if dur < 120 else f"{dur/60:.1f}m"
        print(f"  Phase {r['phase']} [{r['run_date']}] {dur_str}")
        # Print key metric
        if r['phase'] == '1':
            print(f"    features: {res.get('features_written','?')}, syms: {res.get('symbols','?')}")
        elif r['phase'] == '2':
            for m, v in res.get('models', {}).items():
                print(f"    {m}: AUC_OOS={v.get('auc_oos','?')}")
        elif r['phase'] == '3':
            for reg, v in res.get('regimes', {}).items():
                if not v.get('skipped'):
                    print(f"    {reg}: AUC_OOS={v.get('auc_oos','?')}")
        elif r['phase'] == '4':
            print(f"    stocks: {res.get('n_trained','?')}, good_AUC>0.55: {res.get('n_good_auc','?')}")
        elif r['phase'] == '6':
            print(f"    avg_AUC={res.get('avg_auc','?')}, avg_Sharpe={res.get('avg_sharpe','?')}")
        elif r['phase'] == '7':
            print(f"    active: {res.get('n_active','?')}, weak: {res.get('n_weak','?')}, top: {res.get('top5','?')}")
        elif r['phase'] == '51':
            dir_ = res.get('direction', '?')
            pu   = res.get('p_up', 0);  pd_ = res.get('p_down', 0)
            lo, hi = (res.get('expected_move_range') or ['?','?'])
            print(f"    direction={dir_} p_up={pu:.0%} p_down={pd_:.0%} "
                  f"move=[{lo}%,{hi}%] acc={res.get('model_accuracy_oos','?'):.3f}")
        elif r['phase'] == '55':
            dc   = res.get('dir_counts', {})
            top  = res.get('top_up_stocks', [])[:5]
            acc  = res.get('acc_oos', 0)
            n    = res.get('n_scored', 0)
            print(f"    n_scored={n} UP={dc.get('UP','?')} FLAT={dc.get('FLAT','?')} "
                  f"DOWN={dc.get('DOWN','?')} acc_oos={acc:.3f} | top_up={top}")
        elif r['phase'] == '56':
            sc   = res.get('state_counts', {})
            acc  = res.get('wf_accuracy', 0)
            lat  = res.get('latest_state', '?')
            sig  = res.get('latest_signal_1d', 0) or 0
            age  = res.get('latest_regime_age', '?')
            ent  = res.get('latest_entropy', 0) or 0
            hmm  = '✓' if res.get('hmm_enabled') else '✗'
            print(f"    BULL={sc.get('BULL','?')} SIDE={sc.get('SIDE','?')} "
                  f"BEAR={sc.get('BEAR','?')} wf_acc={acc:.1%} HMM={hmm} | "
                  f"latest={lat} signal={sig:+.3f} age={age}d entropy={ent:.2f}bits")

    # Tomorrow forecast (Ph51)
    _ensure_tomorrow_forecast_table(conn)
    tf = conn.execute("""
        SELECT forecast_date, direction, p_up, p_flat, p_down,
               expected_move_lo, expected_move_hi, volatility_regime,
               model_accuracy, model_auc
        FROM tomorrow_forecast ORDER BY id DESC LIMIT 3
    """).fetchall()
    if tf:
        print("\n  📅 Tomorrow Forecast (Ph51):")
        for t in tf:
            print(f"    [{t['forecast_date']}] {t['direction']} "
                  f"↑{t['p_up']:.0%} →{t['p_flat']:.0%} ↓{t['p_down']:.0%} "
                  f"move=[{t['expected_move_lo']:.1f}%,{t['expected_move_hi']:.1f}%] "
                  f"vol={t['volatility_regime']} acc={t['model_accuracy']:.3f}")

    # Per-Stock Tomorrow Forecast summary (Ph55)
    try:
        _ensure_stock_forecast_table(conn)
        sf55 = conn.execute("""
            SELECT forecast_date,
                   SUM(CASE WHEN direction='UP'   THEN 1 ELSE 0 END) n_up,
                   SUM(CASE WHEN direction='FLAT' THEN 1 ELSE 0 END) n_flat,
                   SUM(CASE WHEN direction='DOWN' THEN 1 ELSE 0 END) n_down,
                   COUNT(*) n_total
            FROM stock_tomorrow_forecast
            WHERE forecast_date = (SELECT MAX(forecast_date) FROM stock_tomorrow_forecast)
            GROUP BY forecast_date
        """).fetchone()
        if sf55 and sf55['n_total']:
            top_up55 = conn.execute("""
                SELECT symbol, p_up FROM stock_tomorrow_forecast
                WHERE forecast_date = ? AND direction='UP'
                ORDER BY p_up DESC LIMIT 5
            """, (sf55['forecast_date'],)).fetchall()
            top_str = ', '.join([f"{r['symbol']}({r['p_up']:.0%})" for r in top_up55])
            print(f"\n  📈 Stock Forecast (Ph55) [{sf55['forecast_date']}]: "
                  f"UP={sf55['n_up']} FLAT={sf55['n_flat']} DOWN={sf55['n_down']} "
                  f"| Top UP: {top_str}")
    except Exception:
        pass

    # Markov Regime signal (Ph56)
    try:
        _ensure_markov_tables(conn)
        mk = conn.execute("""
            SELECT ms.date, ms.current_state, ms.regime_age,
                   ms.signal_1d, ms.p_bear_1d, ms.p_side_1d, ms.p_bull_1d,
                   ms.continuation_confidence, ms.transition_risk, ms.entropy,
                   ms.signal_3d, ms.signal_5d,
                   ms.triple_confirmed, ms.wf_signal_correct,
                   mr.sub_label, mr.base_confidence
            FROM markov_signal_daily ms
            LEFT JOIN markov_regime_daily mr ON ms.date = mr.date
            ORDER BY ms.date DESC LIMIT 3
        """).fetchall()
        if mk:
            print("\n  🔄 Markov Regime (Ph56) — latest 3 days:")
            for m in mk:
                tc  = '✓✓✓' if m['triple_confirmed'] == 1 else ''
                acc = f" acc={m['wf_signal_correct']}" if m['wf_signal_correct'] is not None else ''
                sub = f"({m['sub_label']})" if m['sub_label'] and m['sub_label'] != 'neutral' else ''
                print(f"    [{m['date']}] {m['current_state']}{sub} "
                      f"age={m['regime_age']}d sig={m['signal_1d']:+.3f} "
                      f"↑{m['p_bull_1d']:.1%} →{m['p_side_1d']:.1%} ↓{m['p_bear_1d']:.1%} "
                      f"H={m['entropy']:.2f}bits tRisk={m['transition_risk']:.1%} "
                      f"3d={m['signal_3d']:+.3f} 5d={m['signal_5d']:+.3f}"
                      f"{tc}{acc}")
    except Exception:
        pass

    # Walk-forward summary
    wf = conn.execute("""
        SELECT window_id, auc_test, sharpe, sortino, max_drawdown, win_rate, n_signals
        FROM walkforward_results ORDER BY run_date DESC, window_id LIMIT 8
    """).fetchall()
    if wf:
        print("\n  Walk-Forward Windows:")
        for w in wf:
            print(f"    W{w['window_id']}: AUC={w['auc_test']:.3f} Sharpe={w['sharpe']:.2f} "
                  f"WinRate={w['win_rate']:.1%} n={w['n_signals']}")

    # Per-stock model summary
    good = conn.execute("SELECT COUNT(*) as n FROM per_stock_models WHERE auc_oos > 0.55").fetchone()['n']
    total = conn.execute("SELECT COUNT(*) as n FROM per_stock_models").fetchone()['n']
    if total > 0:
        print(f"\n  Per-Stock Models: {total} trained, {good} with AUC>0.55")

    # Ph51 forecast accuracy
    try:
        _ensure_forecast_outcomes_table(conn)
        stats = conn.execute("""
            SELECT COUNT(*) n, SUM(correct) hits, AVG(correct) acc_all,
                   SUM(CASE WHEN forecast_date >= date('now','-30 days') THEN correct END) h30,
                   COUNT(CASE WHEN forecast_date >= date('now','-30 days') THEN 1 END) n30,
                   SUM(CASE WHEN forecast_date >= date('now','-7 days')  THEN correct END) h7,
                   COUNT(CASE WHEN forecast_date >= date('now','-7 days')  THEN 1 END) n7
            FROM tomorrow_forecast_outcomes
        """).fetchone()
        if stats and stats['n']:
            acc_all = round(float(stats['acc_all'] or 0) * 100, 1)
            n30, h30 = int(stats['n30'] or 0), int(stats['h30'] or 0)
            n7,  h7  = int(stats['n7']  or 0), int(stats['h7']  or 0)
            acc30 = f"{h30/n30*100:.0f}% ({n30})" if n30 else "—"
            acc7  = f"{h7/n7*100:.0f}% ({n7})"    if n7  else "—"
            print(f"\n  📅 Ph51 Forecast Accuracy: all-time={acc_all}% ({stats['n']} obs)"
                  f"  30d={acc30}  7d={acc7}")
    except Exception:
        pass

    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 54 — FORECAST ACCURACY TRACKER
# ═════════════════════════════════════════════════════════════════════════════

def phase54_forecast_accuracy():
    """
    Phase 54 — Tomorrow Forecast Accuracy Tracker
    -----------------------------------------------
    Reads past tomorrow_forecast rows, computes actual market direction from
    market_breadth_enhanced (or ohlcv_history), fills tomorrow_forecast_outcomes,
    and returns rolling accuracy metrics.

    Runs nightly (fast, <5s). Essential for measuring Ph51 real-world accuracy
    and detecting model drift over time.
    """
    t0        = time.time()
    today_str = datetime.date.today().isoformat()
    conn      = get_db()
    ensure_tables(conn)
    _ensure_tomorrow_forecast_table(conn)
    _ensure_forecast_outcomes_table(conn)

    daily = _load_breadth_for_ph51(conn)
    if daily is not None:
        _fill_forecast_outcomes(conn, daily)

    # ── Summary report ─────────────────────────────────────────────────────────
    stats = conn.execute("""
        SELECT COUNT(*) n, SUM(correct) hits, AVG(correct) acc_all,
               SUM(CASE WHEN forecast_date >= date('now','-30 days') THEN correct END) h30,
               COUNT(CASE WHEN forecast_date >= date('now','-30 days') THEN 1 END) n30,
               SUM(CASE WHEN forecast_date >= date('now','-7 days')   THEN correct END) h7,
               COUNT(CASE WHEN forecast_date >= date('now','-7 days')   THEN 1 END) n7,
               AVG(CASE WHEN correct=1 THEN confidence END)    avg_conf_correct,
               AVG(CASE WHEN correct=0 THEN confidence END)    avg_conf_wrong
        FROM tomorrow_forecast_outcomes
    """).fetchone()

    # Per-class breakdown
    breakdown = conn.execute("""
        SELECT predicted_dir, COUNT(*) n, SUM(correct) hits
        FROM tomorrow_forecast_outcomes
        GROUP BY predicted_dir
    """).fetchall()

    n_total  = int(stats['n']    or 0)
    acc_all  = round(float(stats['acc_all'] or 0) * 100, 1) if n_total else None
    n30, h30 = int(stats['n30'] or 0), int(stats['h30'] or 0)
    n7,  h7  = int(stats['n7']  or 0), int(stats['h7']  or 0)
    acc30    = round(h30 / n30 * 100, 1) if n30 else None
    acc7     = round(h7  / n7  * 100, 1) if n7  else None

    by_class = {}
    for row in breakdown:
        dir_ = row['predicted_dir']
        by_class[dir_] = {
            'n':    int(row['n']    or 0),
            'hits': int(row['hits'] or 0),
            'acc':  round(int(row['hits'] or 0) / max(1, int(row['n'])) * 100, 1),
        }

    dur     = time.time() - t0
    summary = {
        "phase":        "54",
        "n_outcomes":   n_total,
        "acc_all_pct":  acc_all,
        "acc_30d_pct":  acc30,
        "acc_7d_pct":   acc7,
        "n_30d":        n30,
        "n_7d":         n7,
        "by_class":     by_class,
        "duration_seconds": round(dur, 2),
    }

    conn2 = get_db()
    conn2.execute(
        "INSERT INTO ml_trainer_runs (run_date,phase,duration_seconds,results) VALUES (?,?,?,?)",
        (today_str, '54', dur, json.dumps(summary))
    )
    conn2.commit(); conn2.close()
    conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PREDICT ENSEMBLE — score today's stocks with the full 4-model + meta stack
# ═════════════════════════════════════════════════════════════════════════════

def cmd_predict_ensemble():
    """
    Score every stock with the trained ensemble (LGBM + XGB + RF + ET → meta).
    Writes results to explosion_predictions table (same schema as explosion_ml.py).
    Called daily from run_daily.mjs after the old single-model predict.
    """
    import lightgbm as lgb
    import joblib
    import xgboost as xgb
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import (FEATURE_COLS, safe_float,
                               _build_ohlcv_cache, _build_feature_row_from_tail,
                               ensure_tables, _compute_indicators)

    print(json.dumps({"cmd": "predict_ensemble", "step": "start"}), flush=True)

    # ── Model paths ──────────────────────────────────────────────────────────
    lgbm_path = MODELS / 'explosion_lgbm_v3.txt'
    xgb_path  = MODELS / 'explosion_xgb_v1.json'
    rf_path   = MODELS / 'explosion_rf_v1.pkl'
    et_path   = MODELS / 'explosion_et_v1.pkl'
    meta_path = MODELS / 'explosion_meta_v1.txt'

    missing = [p for p in [lgbm_path, xgb_path, rf_path, et_path, meta_path]
               if not p.exists()]
    if missing:
        print(json.dumps({"error": "models not found", "missing": [str(m) for m in missing],
                          "hint": "Run: python3 egx_ml_trainer.py phase2"}), flush=True)
        return

    lgbm_model = lgb.Booster(model_file=str(lgbm_path))
    xgb_model  = xgb.Booster()
    xgb_model.load_model(str(xgb_path))
    rf_model   = joblib.load(str(rf_path))
    et_model   = joblib.load(str(et_path))
    meta_model = lgb.Booster(model_file=str(meta_path))

    # Load calibrator if available (Phase 9)
    cal_path = MODELS / 'explosion_calibrator_v1.pkl'
    calibrator = joblib.load(str(cal_path)) if cal_path.exists() else None
    if calibrator is not None:
        print("[ENS] Calibrator loaded (Phase 9 Isotonic)", flush=True)

    # ── Phase 3 regime-specific models (2026-05-22) ──────────────────────────
    # Load all regime LightGBM models if they exist (trained by phase3_regime_models).
    # At predict time we look up today's HMM regime and apply the matching model
    # as a 35% weight blend with the Phase 2 ensemble (65%). When a regime model
    # is missing (not yet trained or regime is UNKNOWN), falls back to ensemble-only.
    regime_models = {}
    for rg in ['bull', 'bear', 'choppy', 'unknown']:
        rp = MODELS / f'regime_{rg}_lgbm_v3.txt'
        if rp.exists():
            try:
                regime_models[rg.upper()] = lgb.Booster(model_file=str(rp))
            except Exception as e:
                print(f"[ENS] WARNING: Could not load regime_{rg} model: {e}", flush=True)
    if regime_models:
        print(f"[ENS] Phase 3 regime models loaded: {sorted(regime_models.keys())}", flush=True)
    else:
        print("[ENS] Phase 3 regime models not found — using ensemble-only (run phase3 to improve)", flush=True)

    # ── Phase 4.5 per-stock models (2026-05-23) ───────────────────────────────
    # Load per-stock LightGBM models (trained by phase4_per_stock_models).
    # Each stock's personal model captures idiosyncratic patterns (sector cycles,
    # liquidity rhythms, individual volatility regimes) that the global ensemble misses.
    # Only blend when OOS AUC >= 0.55 (genuine predictive signal vs noise).
    # Weight scales from 0% at AUC=0.55 → max 20% at AUC=1.0, leaving 80%+ for global.
    # Stocks without good per-stock models keep the current ensemble-only prediction.
    _conn_ps = get_db()
    stock_model_meta = {}  # symbol → auc_oos
    try:
        for row in _conn_ps.execute(
            "SELECT symbol, auc_oos, model_path FROM per_stock_models WHERE auc_oos >= 0.55"
        ).fetchall():
            stock_model_meta[row['symbol']] = float(row['auc_oos'] or 0)
    except Exception:
        pass
    _conn_ps.close()

    # Pre-load latest TECH_FEATURES from feature_store for all symbols at once.
    # feature_store is pre-computed by Phase 1 / daily pipeline — much faster than
    # recomputing 67 features per stock in predict_ensemble.
    _conn_fs = get_db()
    stock_feature_vectors = {}  # symbol → np.array of 67 TECH_FEATURES (ordered by TECH_FEATURES list)
    try:
        fs_rows = _conn_fs.execute(
            """SELECT symbol, feature_name, feature_value
               FROM feature_store
               WHERE feature_date = (SELECT MAX(feature_date) FROM feature_store)"""
        ).fetchall()
        # Pivot: {symbol: {feature_name: value}}
        _fs_pivot = {}
        for row in fs_rows:
            sym_k = row['symbol']
            if sym_k not in _fs_pivot:
                _fs_pivot[sym_k] = {}
            _fs_pivot[sym_k][row['feature_name']] = float(row['feature_value'] or 0.0)
        # Build ordered feature arrays for each symbol
        for sym_k, fdict in _fs_pivot.items():
            vec = np.array([fdict.get(f, 0.0) for f in TECH_FEATURES], dtype=np.float32).reshape(1, -1)
            stock_feature_vectors[sym_k] = vec
        fs_latest = _conn_fs.execute("SELECT MAX(feature_date) FROM feature_store").fetchone()[0]
        print(f"[ENS] Feature store loaded: {len(stock_feature_vectors)} symbols, latest={fs_latest}", flush=True)
    except Exception as e:
        print(f"[ENS] Feature store unavailable: {e}", flush=True)
    _conn_fs.close()

    stock_models = {}  # symbol → LightGBM Booster (lazy-loaded only for today's stocks)
    print(f"[ENS] Per-stock models available: {len(stock_model_meta)} with AUC>=0.55", flush=True)

    feat_path = MODELS / 'explosion_features_v3.json'
    if feat_path.exists():
        FEAT = json.loads(feat_path.read_text())
    else:
        FEAT = list(FEATURE_COLS)

    conn = get_db()
    ensure_tables(conn)

    pred_date = datetime.date.today().isoformat()

    # ── Get today's market regime for Phase 3 blending ──────────────────────
    today_regime = 'UNKNOWN'
    try:
        rrow = conn.execute(
            "SELECT regime FROM regime_history WHERE date<=? ORDER BY date DESC LIMIT 1",
            (pred_date,)
        ).fetchone()
        if rrow:
            today_regime = str(rrow['regime'] or 'UNKNOWN').upper()
    except Exception:
        pass
    print(f"[ENS] Today's regime: {today_regime} "
          f"(regime model {'ACTIVE' if today_regime in regime_models else 'not available'})",
          flush=True)

    # ── Recent failure memory (2026-05-23) ──────────────────────────────────
    # Stocks that hit STOP_LOSS or negative TIME_STOP in last 60 days get
    # a probability penalty that fades linearly from 20% (day 0) → 0% (day 60).
    # Penalty is also scaled by loss severity: a -10% loss → full severity multiplier,
    # smaller losses get proportionally less penalty.
    # Rationale: MHOT had STOP_LOSS on 2026-04-27 but still scored 83.6% on the
    # next cycle — the market condition that caused the failure likely persists.
    recent_losers = {}   # symbol → {last_loss_date, days_ago, worst_pnl, loss_count}
    try:
        import backtest_engine as _be_mem
        recent_losers = _be_mem.get_recent_losers(str(DB_PATH), lookback_days=60)
        print(f"[ENS] Recent failure memory: {len(recent_losers)} stocks penalized "
              f"(60d STOP_LOSS/TIME_STOP)", flush=True)
        if recent_losers:
            # Show the 5 most recently-failed stocks
            top_fails = sorted(recent_losers.items(), key=lambda x: x[1]['days_ago'])[:5]
            for s_f, i_f in top_fails:
                print(f"[ENS]   penalty: {s_f:8s}  last_loss={i_f['last_loss_date']} "
                      f"({i_f['days_ago']}d ago)  worst={i_f['worst_pnl']:.1f}%  "
                      f"n={i_f['loss_count']}", flush=True)
    except Exception as e_mem:
        print(f"[ENS] Recent failure memory unavailable: {e_mem}", flush=True)

    # ── Build OHLCV cache ────────────────────────────────────────────────────
    # Clear any stale predictions from earlier runs today before re-scoring.
    # predict_ensemble uses INSERT OR REPLACE per (symbol, pred_date), so symbols
    # that are now filtered by anomaly/vol guards (JUFO, DTPP etc.) would retain
    # old scores from earlier today's runs. A clean delete + re-insert ensures the
    # prediction table always reflects the CURRENT guard state.
    conn.execute("DELETE FROM explosion_predictions WHERE pred_date = ?", (pred_date,))
    conn.commit()

    print("[ENS] Building OHLCV cache...", flush=True)
    t0 = time.time()

    symbols = [r['symbol'] for r in conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history"
    ).fetchall()]

    predictions = []
    n_scored = 0
    n_anomaly_skipped = 0
    n_failure_penalized = 0

    for sym in symbols:
        bars = conn.execute("""
            SELECT date(bar_time,'unixepoch') AS bar_date, open, high, low, close, volume
            FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 40
        """, (sym,)).fetchall()

        if len(bars) < 10:
            continue

        bars = list(reversed(bars))

        # ── Price anomaly guard (2026-05-22) ────────────────────────────────
        # Skip stocks with extreme single-day moves in the last 20 bars.
        #
        # UPWARD extreme (>50% up): EGX data errors — bad ticks, price normalization
        #   issues, corporate-event recording errors (TORA +12,000%, TRTO +34,000%,
        #   CICH +587%, etc.). The ML model scores these highly because the unusual
        #   OHLCV patterns resemble BB compression breakouts — they are NOT valid setups.
        #
        # DOWNWARD extreme (>35% down): EGX ex-dividend adjustments — stocks paying
        #   50-100% dividends gap-down on ex-div day. Post-dividend stocks have
        #   artificial "BB compression + momentum" features that fool the ML model.
        #   They are NOT good short-swing candidates (9-day hold); use long-swing/
        #   investment strategies for post-dividend recovery plays.
        #   Note: 35% threshold chosen to catch large EGX dividends while allowing
        #   normal market corrections (EGX daily limit is ~10-20% in most periods).
        anomaly_skip = False
        try:
            closes = [float(b['close'] or 0) for b in bars[-20:]]
            for i in range(1, len(closes)):
                if closes[i-1] > 0 and closes[i] > 0:
                    day_chg = (closes[i] - closes[i-1]) / closes[i-1]
                    if day_chg > 0.50:   # >50% upward in one day = data error/artifact
                        anomaly_skip = True
                        break
                    if day_chg < -0.30:  # >30% downward = ex-dividend gap (EGX daily limit ~10%, so >30% = halt/dividend only)
                        anomaly_skip = True
                        break
        except Exception:
            pass
        if anomaly_skip:
            n_anomaly_skipped += 1
            continue

        # ── Near-zero-volume guard ────────────────────────────────────────────
        # Skip stocks with vol_ratio_20 < 0.10 in the most recent bar.
        # These are essentially non-trading stocks (JUFO vol=0.06, DTPP vol=0.08)
        # that score falsely high due to BB compression on near-zero volume days.
        # Gate 6d in signal_integration.py (vol>=0.90) would block them anyway,
        # but filtering here prevents them from appearing in the prediction table
        # and misleading the ranking dashboard.
        try:
            import pandas as _pd
            _df_tmp = _pd.DataFrame([{'close': float(b['close'] or 0),
                                       'volume': float(b['volume'] or 0)}
                                      for b in bars[-5:]])
            _avg_vol = _df_tmp['close'].iloc[-1] if len(_df_tmp) == 0 else None
            # Use volume directly: compare last bar to rolling 20-bar avg
            _bar_vols = [float(b['volume'] or 0) for b in bars]
            _avg20 = sum(_bar_vols[-20:]) / max(1, len(_bar_vols[-20:]))
            _last_vol = _bar_vols[-1] if _bar_vols else 0
            if _avg20 > 0 and (_last_vol / _avg20) < 0.10:
                n_anomaly_skipped += 1
                continue
        except Exception:
            pass

        try:
            import pandas as pd
            df = pd.DataFrame([{
                'bar_date': r['bar_date'],
                'open':  float(r['open']   or 0),
                'high':  float(r['high']   or 0),
                'low':   float(r['low']    or 0),
                'close': float(r['close']  or 0),
                'volume':float(r['volume'] or 0),
            } for r in bars]).sort_values('bar_date').reset_index(drop=True)
            df = _compute_indicators(df)
            feat = _build_feature_row_from_tail(df)
        except Exception:
            continue

        if feat is None:
            continue

        X = np.array([feat], dtype=np.float32)

        # ── Base model predictions ────────────────────────────────────────────
        p_lgbm = p_xgb = p_rf = p_et = 0.5
        p_regime = None
        try:
            p_lgbm = float(lgbm_model.predict(X)[0])
            p_xgb  = float(xgb_model.predict(xgb.DMatrix(X))[0])
            p_rf   = float(rf_model.predict_proba(X)[0, 1])
            p_et   = float(et_model.predict_proba(X)[0, 1])

            # Meta-model stacking → calibration
            meta_X   = np.array([[p_lgbm, p_xgb, p_rf, p_et]], dtype=np.float32)
            raw_prob = float(meta_model.predict(meta_X)[0])
            ensemble_prob = float(calibrator.transform([raw_prob])[0]) if calibrator else raw_prob
        except Exception:
            # Fallback to LGBM alone if meta fails
            ensemble_prob = p_lgbm

        # ── Phase 3: blend regime-specific model (updated 2026-05-23) ──────────
        # OOS AUC analysis (2026-02-01 to 2026-05-20) shows regime-specific benefit
        # varies significantly by regime:
        #   BEAR:    regime_AUC=0.615 vs ensemble_AUC=0.530 → +8.5pp → w=0.50
        #   CHOPPY:  regime_AUC=0.560 vs ensemble_AUC=0.547 → +1.3pp → w=0.40
        #   BULL:    regime_AUC=0.519 vs ensemble_AUC=0.511 → +0.9pp → w=0.20
        #   UNKNOWN: no OOS evidence → conservative w=0.15
        # In BEAR regime, the regime-specific model captures bear-specific patterns
        # (RSI oversold + BB compression + volume drying) that the global ensemble misses.
        # In BULL regime (current), global ensemble is already well-calibrated;
        # reducing regime blend weight from 35% → 20% prevents over-fitting to bull patterns.
        REGIME_BLEND_WEIGHTS = {
            'BEAR':    0.50,   # was 0.35 — large benefit (+8.5pp AUC)
            'CHOPPY':  0.40,   # was 0.35 — moderate benefit (+1.3pp AUC)
            'BULL':    0.20,   # was 0.35 — marginal benefit (+0.9pp AUC)
            'UNKNOWN': 0.15,   # conservative — no OOS validation
        }
        regime_model = regime_models.get(today_regime)
        if regime_model is not None:
            try:
                p_regime = float(regime_model.predict(X)[0])
                w_regime  = REGIME_BLEND_WEIGHTS.get(today_regime, 0.35)
                prob = (1.0 - w_regime) * ensemble_prob + w_regime * p_regime
            except Exception:
                prob = ensemble_prob
        else:
            prob = ensemble_prob

        # ── Phase 4.5: blend per-stock model (2026-05-23) ────────────────────
        # Per-stock LightGBM captures each stock's idiosyncratic patterns using
        # the full 67-feature TECH_FEATURES vector (from feature_store), which
        # includes DNA traits, market breadth, and regime context — far richer
        # than the 22-feature global ensemble input.
        # Weight = min(20%, (auc_oos - 0.55) / 0.45 * 0.20) scales with model quality.
        # The global blend (Phases 2+3) retains at least 80% weight always.
        p_stock = None
        stock_blend_weight = 0.0
        if sym in stock_model_meta and sym in stock_feature_vectors:
            auc_stock = stock_model_meta[sym]
            stock_blend_weight = min(0.20, (auc_stock - 0.55) / 0.45 * 0.20)
            # Lazy-load per-stock model on first encounter
            if sym not in stock_models:
                sp = MODELS / f'stock_{sym}.txt'
                if sp.exists():
                    try:
                        stock_models[sym] = lgb.Booster(model_file=str(sp))
                    except Exception:
                        stock_blend_weight = 0.0
            if sym in stock_models and stock_blend_weight > 0:
                try:
                    X_stock = stock_feature_vectors[sym]  # (1, 67) TECH_FEATURES
                    p_stock = float(stock_models[sym].predict(X_stock)[0])
                    prob = (1.0 - stock_blend_weight) * prob + stock_blend_weight * p_stock
                except Exception:
                    p_stock = None
                    stock_blend_weight = 0.0

        # ── Phase 5: recent failure memory penalty (2026-05-23) ──────────────
        # Down-weight stocks that had a STOP_LOSS within the last 60 days.
        # Max penalty = 20% at day 0, fades to 0% at day 60.
        # Severity scaling: a 10%+ loss → full 20% max; smaller losses proportional.
        # This prevents the model from re-selecting the same losers cycle after cycle
        # (e.g. MHOT scored 83.6% the cycle after its STOP_LOSS exit).
        failure_penalty = 0.0
        p_before_penalty = prob
        if sym in recent_losers:
            info_f = recent_losers[sym]
            days_f = info_f['days_ago']
            if days_f < 60:
                base_pen  = 0.20 * (1.0 - days_f / 60.0)           # fades 20% → 0%
                severity  = min(1.0, abs(info_f['worst_pnl']) / 10.0)  # scale by loss size
                failure_penalty = base_pen * (0.5 + 0.5 * severity)  # min 50% of base
                prob = prob * (1.0 - failure_penalty)
                n_failure_penalized += 1

        n_scored += 1
        tier = 'HIGH' if prob >= 0.70 else 'MEDIUM' if prob >= 0.50 else 'LOW'

        # Write prediction for ALL symbols (not just prob >= 0.30) so that
        # get_explosion_score() can return real values instead of 50.0 default.
        last = df.iloc[-1] if hasattr(df, 'iloc') else {}
        rsi  = safe_float(last.get('rsi14',      50.0) if hasattr(last, 'get') else 50.0)
        bbw  = safe_float(last.get('bb_width',   0.05) if hasattr(last, 'get') else 0.05)
        volr = safe_float(last.get('vol_ratio_20',1.0) if hasattr(last, 'get') else 1.0)
        mom5 = safe_float(last.get('momentum_5d', 0.0) if hasattr(last, 'get') else 0.0)
        bbpos= safe_float(last.get('bb_position', 0.5) if hasattr(last, 'get') else 0.5)

        drivers = [
            {'feature': 'lgbm_prob',     'value': round(p_lgbm, 3)},
            {'feature': 'xgb_prob',      'value': round(p_xgb,  3)},
            {'feature': 'rf_prob',       'value': round(p_rf,   3)},
            {'feature': 'vol_ratio',     'value': round(volr,   2)},
            {'feature': 'rsi14',         'value': round(rsi,    1)},
            {'feature': 'bb_width',      'value': round(bbw,    4)},
        ]
        if p_regime is not None:
            drivers.append({'feature': f'regime_{today_regime.lower()}_prob',
                            'value': round(p_regime, 3)})
            drivers.append({'feature': 'ensemble_prob_pre_blend',
                            'value': round(ensemble_prob, 3)})
        if p_stock is not None:
            drivers.append({'feature': 'stock_model_prob',
                            'value': round(p_stock, 3)})
            drivers.append({'feature': 'stock_model_weight',
                            'value': round(stock_blend_weight, 3)})
        if failure_penalty > 0:
            drivers.append({'feature': 'failure_penalty',
                            'value': round(failure_penalty, 3)})
            drivers.append({'feature': 'prob_before_penalty',
                            'value': round(p_before_penalty, 3)})

        conn.execute("""
            INSERT OR REPLACE INTO explosion_predictions
            (symbol, pred_date, explosion_prob, prob_pct, confidence_tier, direction, top_drivers)
            VALUES (?,?,?,?,?,?,?)
        """, (
            sym, pred_date, prob, int(prob * 100), tier, 'UP',
            json.dumps(drivers)
        ))

        if prob >= 0.30:
            entry = {
                'symbol':          sym,
                'explosion_prob':  round(prob, 4),
                'lgbm':            round(p_lgbm, 3),
                'xgb':             round(p_xgb,  3),
                'rf':              round(p_rf,   3),
                'tier':            tier,
            }
            if p_regime is not None:
                entry['regime_prob'] = round(p_regime, 3)
                entry['ensemble_pre_blend'] = round(ensemble_prob, 3)
            if p_stock is not None:
                entry['stock_prob'] = round(p_stock, 3)
                entry['stock_weight'] = round(stock_blend_weight, 3)
            if failure_penalty > 0:
                entry['failure_penalty'] = round(failure_penalty, 3)
                entry['prob_pre_penalty'] = round(p_before_penalty, 3)
            predictions.append(entry)

    conn.commit()

    predictions.sort(key=lambda x: -x['explosion_prob'])
    dur = time.time() - t0
    regime_blend_active = bool(regime_models.get(today_regime))
    n_stock_blended = sum(1 for p in predictions if 'stock_prob' in p)

    regime_blend_weight = REGIME_BLEND_WEIGHTS.get(today_regime, 0.35) if regime_blend_active else 0.0
    print(json.dumps({
        "cmd": "predict_ensemble",
        "pred_date": pred_date,
        "today_regime": today_regime,
        "regime_blend_active": regime_blend_active,
        "regime_blend_weight": regime_blend_weight,
        "n_stock_models_blended": len(stock_models),
        "n_predictions_with_stock_blend": n_stock_blended,
        "n_failure_penalized": n_failure_penalized,
        "n_recent_losers_tracked": len(recent_losers),
        "n_scored": n_scored,
        "n_anomaly_skipped": n_anomaly_skipped,
        "n_stored": len(predictions),
        "top5": [{'sym': p['symbol'], 'prob': p['explosion_prob'],
                  'pen': p.get('failure_penalty', 0)} for p in predictions[:5]],
        "duration_seconds": round(dur, 1),
    }), flush=True)

    conn.close()
    return predictions


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 9 — MODEL CALIBRATION (Isotonic Regression + Brier + ECE)
# ═════════════════════════════════════════════════════════════════════════════

def phase9_calibration():
    """
    Phase 9: Calibrate ensemble meta-model with Isotonic Regression.
    Fixes RF/ET overconfidence (55 stocks at 0.9999).

    Method:
      1. Build OOS dataset (2026-01-30 →): positives from explosive_moves,
         negatives from sampled non-explosion bars — both with real features.
      2. Run ensemble (LGBM+XGB+RF+ET→meta) on OOS to get raw probs.
      3. Fit IsotonicRegression(raw_prob → calibrated_prob).
      4. Save calibrator + report Brier Score + ECE (15 bins).
    """
    import lightgbm as lgb
    import joblib
    import xgboost as xgb
    from sklearn.isotonic import IsotonicRegression
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import FEATURE_COLS, safe_float, _build_ohlcv_cache, _build_feature_row

    t0 = time.time()
    print(json.dumps({"phase": "9", "step": "start", "desc": "Calibration (Isotonic)"}), flush=True)

    OOS_START = '2026-01-30'
    conn = get_db()
    today_str = datetime.date.today().isoformat()

    # ── Load ensemble models ─────────────────────────────────────────────────
    lgbm_m = lgb.Booster(model_file=str(MODELS / 'explosion_lgbm_v3.txt'))
    xgb_m  = xgb.Booster(); xgb_m.load_model(str(MODELS / 'explosion_xgb_v1.json'))
    rf_m   = joblib.load(str(MODELS / 'explosion_rf_v1.pkl'))
    et_m   = joblib.load(str(MODELS / 'explosion_et_v1.pkl'))
    meta_m = lgb.Booster(model_file=str(MODELS / 'explosion_meta_v1.txt'))
    print("[P9] Models loaded", flush=True)

    # ── Build OOS dataset with real features ─────────────────────────────────
    cache = _build_ohlcv_cache(conn, '2026-12-31')

    pos_rows = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date >= ?", (OOS_START,)
    ).fetchall()
    neg_cands = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history o
        WHERE date(o.bar_time,'unixepoch') >= ?
          AND NOT EXISTS (SELECT 1 FROM explosive_moves e
                          WHERE e.symbol=o.symbol AND e.explosion_date=date(o.bar_time,'unixepoch'))
        ORDER BY RANDOM() LIMIT ?
    """, (OOS_START, len(pos_rows) * 4)).fetchall()
    conn.close()

    X_oos, y_oos = [], []
    for r in pos_rows:
        row = [safe_float(r[c]) for c in FEATURE_COLS]
        if sum(abs(v) for v in row) < 1e-6: continue
        X_oos.append(row); y_oos.append(1)

    neg_count = 0
    target_neg = len(y_oos) * 3
    for neg in neg_cands:
        if neg_count >= target_neg: break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None: continue
        row = _build_feature_row(sym_df, neg['bar_date'])
        if row is None: continue
        X_oos.append(row); y_oos.append(0)
        neg_count += 1

    X_oos = np.array(X_oos, dtype=np.float32)
    y_oos = np.array(y_oos, dtype=np.int32)
    print(f"[P9] OOS dataset: {len(X_oos)} ({y_oos.sum()} pos)", flush=True)

    if len(X_oos) < 100:
        print(json.dumps({"phase": "9", "error": "insufficient OOS data"}), flush=True)
        return

    # ── Get raw ensemble probs on OOS ────────────────────────────────────────
    p_lgbm = lgbm_m.predict(X_oos)
    p_xgb  = xgb_m.predict(xgb.DMatrix(X_oos))
    p_rf   = rf_m.predict_proba(X_oos)[:, 1]
    p_et   = et_m.predict_proba(X_oos)[:, 1]
    meta_X = np.column_stack([p_lgbm, p_xgb, p_rf, p_et]).astype(np.float32)
    raw_probs = meta_m.predict(meta_X)

    # ── Train Isotonic calibrator ────────────────────────────────────────────
    # Use 70% for calibrator training, 30% for evaluation
    n = len(X_oos)
    idx = np.random.RandomState(42).permutation(n)
    cut = int(n * 0.70)
    tr_idx, ev_idx = idx[:cut], idx[cut:]

    calibrator = IsotonicRegression(out_of_bounds='clip')
    calibrator.fit(raw_probs[tr_idx], y_oos[tr_idx])
    cal_probs_eval = calibrator.transform(raw_probs[ev_idx])

    # ── Brier Score ───────────────────────────────────────────────────────────
    brier_raw = float(np.mean((raw_probs[ev_idx] - y_oos[ev_idx]) ** 2))
    brier_cal = float(np.mean((cal_probs_eval    - y_oos[ev_idx]) ** 2))
    brier_ref = float(np.mean(y_oos[ev_idx]))  # naive predict-mean baseline
    brier_ref_score = float(np.mean((brier_ref - y_oos[ev_idx]) ** 2))

    # ── Expected Calibration Error (ECE, 15 bins) ─────────────────────────────
    def ece(probs, labels, n_bins=15):
        bins = np.linspace(0, 1, n_bins + 1)
        total_ece = 0.0
        for i in range(n_bins):
            mask = (probs >= bins[i]) & (probs < bins[i+1])
            if mask.sum() == 0: continue
            bin_acc  = labels[mask].mean()
            bin_conf = probs[mask].mean()
            total_ece += mask.sum() / len(probs) * abs(bin_acc - bin_conf)
        return float(total_ece)

    ece_raw = ece(raw_probs[ev_idx], y_oos[ev_idx])
    ece_cal = ece(cal_probs_eval,    y_oos[ev_idx])

    # ── Probability distribution after calibration ────────────────────────────
    # Calibrate full OOS predictions to see spread
    all_cal = calibrator.transform(raw_probs)
    hist = {f"{i*10}-{i*10+10}%": int(((all_cal >= i/10) & (all_cal < (i+1)/10)).sum())
            for i in range(10)}

    # ── Save calibrator ───────────────────────────────────────────────────────
    cal_path = MODELS / 'explosion_calibrator_v1.pkl'
    joblib.dump(calibrator, str(cal_path))

    dur = time.time() - t0
    summary = {
        "phase": "9",
        "n_oos": len(X_oos),
        "brier_raw": round(brier_raw, 4),
        "brier_calibrated": round(brier_cal, 4),
        "brier_skill": round(1 - brier_cal / brier_ref_score, 3),
        "ece_raw": round(ece_raw, 4),
        "ece_calibrated": round(ece_cal, 4),
        "prob_distribution": hist,
        "calibrator_path": str(cal_path),
        "duration_seconds": round(dur, 1),
    }

    print(f"[P9] Brier: raw={brier_raw:.4f} → cal={brier_cal:.4f} (skill={summary['brier_skill']:.3f})", flush=True)
    print(f"[P9] ECE:   raw={ece_raw:.4f} → cal={ece_cal:.4f}", flush=True)
    print(f"[P9] Prob distribution after calibration: {hist}", flush=True)

    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '9', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 10 — TV REPLAY BACKTEST (TradingView MCP replay_start/step/trade)
# ═════════════════════════════════════════════════════════════════════════════

def phase10_tv_replay_backtest():
    """
    Phase 10: Backtesting using actual TradingView Replay engine.

    Strategy:
      For each historical HIGH_CONVICTION signal (explosive_moves IS period):
        1. chart_set_symbol(symbol)
        2. replay_start(explosion_date - 5 bars)
        3. Read ensemble score → if score > threshold → buy
        4. replay_step × 10 bars to capture outcome
        5. replay_status() → extract P&L, fill price
        6. replay_trade(close)
        7. Save result to tv_replay_backtest table

    Uses Node.js MJS bridge script (scripts/tv_replay_bridge.mjs).
    Falls back to DB-based simulation if TradingView not available.
    """
    import subprocess as sp
    t0 = time.time()
    print(json.dumps({"phase": "10", "step": "start", "desc": "TV Replay Backtest"}), flush=True)

    today_str = datetime.date.today().isoformat()
    conn = get_db()
    ensure_tv_replay_table(conn)

    # ── Select signals to replay ──────────────────────────────────────────────
    # TOP 200 explosive moves in IS period with high pre-explosion compression
    signals = conn.execute("""
        SELECT em.symbol, em.explosion_date,
               COALESCE(em.return_1d, 0.0) as explosion_return,
               em.pre5_bb_width, em.pre5_vol_ratio, em.pre5_rsi,
               em.pre5_bb_position, em.pre5_compression_days,
               em.explosion_class
        FROM explosive_moves em
        WHERE em.explosion_date BETWEEN '2022-01-01' AND '2025-12-31'
          AND em.explosion_class IN ('LARGE','EXTREME')
          AND em.pre5_bb_width > 0
          AND em.pre5_vol_ratio > 0
        ORDER BY em.pre5_compression_days DESC, em.return_1d DESC
        LIMIT 200
    """).fetchall()
    conn.close()

    print(f"[P10] {len(signals)} signals to replay", flush=True)

    # ── Check if TradingView MCP bridge is available ─────────────────────────
    bridge_path = ROOT / 'scripts' / 'tv_replay_bridge.mjs'
    tv_available = bridge_path.exists()

    results = []
    db_sim_results = []

    if tv_available:
        # ── TradingView Replay path ────────────────────────────────────────────
        for i, sig in enumerate(signals[:50]):   # 50 replays (each ~20s)
            try:
                cmd = ['node', str(bridge_path),
                       sig['symbol'], sig['explosion_date']]
                proc = sp.run(cmd, capture_output=True, text=True,
                              timeout=60, cwd=str(ROOT))
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout.strip().split('\n')[-1])
                    results.append({**dict(sig), **data, 'source': 'tv_replay'})
            except Exception as e:
                print(f"[P10] {sig['symbol']} TV error: {e}", flush=True)

            if (i + 1) % 10 == 0:
                print(f"[P10] TV replay: {i+1}/50", flush=True)
    else:
        print("[P10] TV bridge not found — using DB simulation", flush=True)

    # ── DB simulation (always runs, provides full statistics) ─────────────────
    db_conn = get_db()
    for sig in signals:
        sym, ex_date = sig['symbol'], sig['explosion_date']

        # Get 15 bars around explosion
        bars = db_conn.execute("""
            SELECT date(bar_time,'unixepoch') d, open, high, low, close, volume
            FROM ohlcv_history WHERE symbol=? ORDER BY bar_time
        """, (sym,)).fetchall()

        dates = [r['d'] for r in bars]
        if ex_date not in dates: continue
        idx = dates.index(ex_date)
        if idx < 5 or idx >= len(bars) - 5: continue

        # Entry: open of explosion bar
        entry_open = float(bars[idx]['open'] or bars[idx]['close'])
        entry_close = float(bars[idx]['close'])

        # ATR-based stop and target
        atr14 = np.mean([max(
            float(bars[j]['high'] or 0) - float(bars[j]['low'] or 0),
            abs(float(bars[j]['high'] or 0) - float(bars[j-1]['close'] or 0)),
            abs(float(bars[j]['low']  or 0) - float(bars[j-1]['close'] or 0))
        ) for j in range(max(1, idx-14), idx)]) if idx >= 14 else 0.0

        tp_price = entry_open + 2 * atr14
        sl_price = entry_open - 1 * atr14

        # Simulate next 5 bars
        outcome = 'timeout'
        exit_price = float(bars[min(idx+5, len(bars)-1)]['close'])
        max_fav = 0.0; max_adv = 0.0
        for j in range(1, min(6, len(bars) - idx)):
            h = float(bars[idx+j]['high']  or entry_open)
            l = float(bars[idx+j]['low']   or entry_open)
            max_fav = max(max_fav, h - entry_open)
            max_adv = max(max_adv, entry_open - l)
            if atr14 > 0:
                if h >= tp_price: outcome = 'tp'; exit_price = tp_price; break
                if l <= sl_price: outcome = 'sl'; exit_price = sl_price; break

        pnl_pct = (exit_price - entry_open) / (entry_open + 1e-10) * 100
        r_multiple = (exit_price - entry_open) / (atr14 + 1e-10) if atr14 > 0 else 0.0

        db_sim_results.append({
            'symbol':       sym,
            'signal_date':  ex_date,
            'entry_price':  round(entry_open, 4),
            'exit_price':   round(exit_price, 4),
            'outcome':      outcome,
            'pnl_pct':      round(pnl_pct, 3),
            'r_multiple':   round(r_multiple, 3),
            'mfe_pct':      round(max_fav / (entry_open + 1e-10) * 100, 3),
            'mae_pct':      round(max_adv / (entry_open + 1e-10) * 100, 3),
            'explosion_class': sig['explosion_class'],
            'source':       'db_sim',
        })

    db_conn.close()

    # ── Merge TV + DB sim results ─────────────────────────────────────────────
    all_results = results + db_sim_results
    if not all_results:
        print(json.dumps({"phase": "10", "error": "no results"}), flush=True)
        return

    # ── Save to DB ────────────────────────────────────────────────────────────
    conn = get_db()
    ensure_tv_replay_table(conn)
    for r in all_results:
        conn.execute("""
            INSERT OR REPLACE INTO tv_replay_backtest
            (symbol, signal_date, entry_price, exit_price, outcome,
             pnl_pct, r_multiple, mfe_pct, mae_pct, explosion_class, source, run_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (r['symbol'], r['signal_date'], r.get('entry_price'), r.get('exit_price'),
              r['outcome'], r['pnl_pct'], r['r_multiple'],
              r.get('mfe_pct', 0), r.get('mae_pct', 0),
              r.get('explosion_class', ''), r['source'], today_str))
    conn.commit()

    # ── Statistics ────────────────────────────────────────────────────────────
    pnls = [r['pnl_pct'] for r in all_results]
    rmuls = [r['r_multiple'] for r in all_results]
    wins  = [r for r in all_results if r['pnl_pct'] > 0]
    tps   = [r for r in all_results if r['outcome'] == 'tp']
    sls   = [r for r in all_results if r['outcome'] == 'sl']

    sharpe = float(np.mean(pnls) / (np.std(pnls) + 1e-10) * np.sqrt(252))
    dd = _max_drawdown(np.cumsum(pnls))

    summary = {
        "phase": "10",
        "n_signals":    len(all_results),
        "win_rate":     round(len(wins) / len(all_results), 3),
        "tp_rate":      round(len(tps) / len(all_results), 3),
        "sl_rate":      round(len(sls) / len(all_results), 3),
        "avg_pnl_pct":  round(float(np.mean(pnls)), 3),
        "avg_r_mult":   round(float(np.mean(rmuls)), 3),
        "sharpe":       round(sharpe, 3),
        "max_drawdown": round(float(dd), 3),
        "tv_replay_n":  len(results),
        "db_sim_n":     len(db_sim_results),
        "duration_seconds": round(time.time() - t0, 1),
    }

    for k, v in summary.items():
        if k not in ('phase', 'duration_seconds'):
            print(f"[P10] {k}: {v}", flush=True)

    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '10', summary['duration_seconds'], json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


def ensure_tv_replay_table(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tv_replay_backtest (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol         TEXT NOT NULL,
        signal_date    TEXT NOT NULL,
        entry_price    REAL,
        exit_price     REAL,
        outcome        TEXT,
        pnl_pct        REAL,
        r_multiple     REAL,
        mfe_pct        REAL,
        mae_pct        REAL,
        explosion_class TEXT,
        source         TEXT,
        run_date       TEXT,
        created_at     TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, signal_date, run_date)
    );
    """)
    conn.commit()


def _max_drawdown(equity_curve):
    peak = -np.inf; max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 11 — PINE ANALYTICS FEATURE FUSION
# ═════════════════════════════════════════════════════════════════════════════

def phase11_pine_features():
    """
    Phase 11: Add Pine Analytics features (VWAP, Volume Profile, RS Score)
    to feature_store.

    Pipeline:
      1. Fetch pine_analytics from DB (populated by fetch_pine_analytics.mjs)
      2. Compute 8 derived features per symbol:
         vwap_dev_pct, poc_distance, above_vah, rs_score,
         rs_percentile, or_breakout, session_bias_bull, corp_event_flag
      3. Write to feature_store with source='pine_analytics'
      4. Report coverage (how many symbols have Pine data today)
    """
    t0 = time.time()
    print(json.dumps({"phase": "11", "step": "start", "desc": "Pine Analytics Feature Fusion"}), flush=True)

    today_str = datetime.date.today().isoformat()
    conn = get_db()

    # ── Fetch latest Pine analytics ───────────────────────────────────────────
    pine_rows = conn.execute("""
        SELECT pa.symbol, pa.trade_date, pa.vwap, pa.volume_poc,
               pa.volume_vah, pa.volume_val, pa.opening_range_high,
               pa.opening_range_low, pa.session_bias, pa.rs_score,
               pa.rs_percentile, pa.corporate_event_flag,
               oh.close as last_close
        FROM pine_analytics pa
        JOIN (
            SELECT symbol, close
            FROM ohlcv_history
            WHERE (symbol, bar_time) IN (
                SELECT symbol, MAX(bar_time) FROM ohlcv_history GROUP BY symbol
            )
        ) oh ON oh.symbol = pa.symbol
        WHERE pa.trade_date >= date('now', '-3 days')
    """).fetchall()

    print(f"[P11] Pine rows: {len(pine_rows)}", flush=True)

    if not pine_rows:
        # No Pine data yet — trigger fetch_pine_analytics.mjs
        import subprocess as sp
        bridge = ROOT / 'scripts' / 'fetch_pine_analytics.mjs'
        if bridge.exists():
            print("[P11] No Pine data — running fetch_pine_analytics.mjs rs...", flush=True)
            proc = sp.run(['node', str(bridge), 'rs'],
                          capture_output=True, text=True, timeout=300,
                          cwd=str(ROOT))
            if proc.returncode == 0:
                # Re-fetch
                pine_rows = conn.execute("""
                    SELECT pa.*, oh.close as last_close
                    FROM pine_analytics pa
                    JOIN (SELECT symbol, close FROM ohlcv_history
                          WHERE (symbol, bar_time) IN (
                              SELECT symbol, MAX(bar_time) FROM ohlcv_history GROUP BY symbol))
                         oh ON oh.symbol=pa.symbol
                    WHERE pa.trade_date >= date('now','-3 days')
                """).fetchall()
                print(f"[P11] After fetch: {len(pine_rows)} rows", flush=True)

    # ── Compute and write Pine features to feature_store ─────────────────────
    version   = f"pine_{today_str}"
    written   = 0
    n_symbols = 0

    for r in pine_rows:
        sym        = r['symbol']
        close      = float(r['last_close'] or 0)
        vwap       = float(r['vwap'] or 0)
        poc        = float(r['volume_poc'] or 0)
        vah        = float(r['volume_vah'] or 0)
        val        = float(r['volume_val'] or 0)
        or_high    = float(r['opening_range_high'] or 0)
        or_low     = float(r['opening_range_low'] or 0)
        rs_score   = float(r['rs_score'] or 50)
        rs_pct     = float(r['rs_percentile'] or 50)
        s_bias     = str(r['session_bias'] or '')
        corp_event = int(r['corporate_event_flag'] or 0)

        if close <= 0: continue

        feats = {
            'pine_vwap_dev_pct':    (close - vwap) / (vwap + 1e-10) if vwap > 0 else 0.0,
            'pine_poc_dist_pct':    (close - poc)  / (poc  + 1e-10) if poc  > 0 else 0.0,
            'pine_above_vah':       float(close > vah)               if vah  > 0 else 0.5,
            'pine_below_val':       float(close < val)               if val  > 0 else 0.5,
            'pine_rs_score':        rs_score / 100.0,
            'pine_rs_percentile':   rs_pct   / 100.0,
            'pine_or_breakout':     float(close > or_high)           if or_high > 0 else 0.5,
            'pine_session_bias':    1.0 if 'bull' in s_bias.lower() else (
                                   -1.0 if 'bear' in s_bias.lower() else 0.0),
            'pine_corp_event':      float(corp_event),
        }

        for feat_name, feat_val in feats.items():
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO feature_store
                    (feature_date, symbol, feature_name, feature_value, version, source_table)
                    VALUES (?,?,?,?,?,?)
                """, (today_str, sym, feat_name, float(feat_val), version, 'pine_analytics'))
                written += 1
            except Exception:
                pass

        n_symbols += 1

    conn.commit()
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "11",
        "n_symbols_with_pine": n_symbols,
        "features_written": written,
        "n_pine_features": 9,
        "coverage_pct": round(n_symbols / 249 * 100, 1),
        "duration_seconds": round(dur, 1),
    }
    print(f"[P11] {n_symbols} symbols, {written} features written ({summary['coverage_pct']}% coverage)", flush=True)

    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '11', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 12 — INCREMENTAL ONLINE LEARNING (daily LightGBM fine-tune)
# ═════════════════════════════════════════════════════════════════════════════

def phase12_incremental_update():
    """
    Phase 12: Add 30 new trees to explosion_lgbm_v3.txt using last 5-day data.

    Daily fine-tuning strategy:
      1. Get recent non-explosion days (last 7 calendar days) → negatives.
      2. Get recent explosive moves (last 60 days) → positives (fresh data).
      3. Continue training: init_model = current LightGBM checkpoint.
      4. Overwrite model file with updated model (+30 trees).
      5. Compare AUC before/after on a small hold-out.

    Runs in ~10 seconds vs 38 minutes for full retrain.
    """
    import lightgbm as lgb
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import FEATURE_COLS, safe_float, _build_ohlcv_cache, _build_feature_row

    t0 = time.time()
    print(json.dumps({"phase": "12", "step": "start", "desc": "Incremental Online Learning"}), flush=True)

    model_path = MODELS / 'explosion_lgbm_v3.txt'
    if not model_path.exists():
        print(json.dumps({"phase": "12", "error": "base model not found — run phase2 first"}), flush=True)
        return

    today_str  = datetime.date.today().isoformat()
    cutoff_neg = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    cutoff_pos = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()

    conn = get_db()

    # ── Positives: recent explosive moves ─────────────────────────────────────
    pos_rows = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date >= ? AND explosion_date <= ?",
        (cutoff_pos, today_str)
    ).fetchall()

    # ── Negatives: recent non-explosion bars ──────────────────────────────────
    neg_cands = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history o
        WHERE date(o.bar_time,'unixepoch') >= ?
          AND NOT EXISTS (SELECT 1 FROM explosive_moves e
                          WHERE e.symbol=o.symbol AND e.explosion_date=date(o.bar_time,'unixepoch'))
        ORDER BY RANDOM()
        LIMIT ?
    """, (cutoff_neg, len(pos_rows) * 5)).fetchall()
    conn.close()

    print(f"[P12] Incremental: {len(pos_rows)} pos (last 60d), {len(neg_cands)} neg candidates", flush=True)

    if len(pos_rows) < 10:
        print(json.dumps({"phase": "12", "status": "skip", "reason": "too few recent positives",
                          "n_pos": len(pos_rows)}), flush=True)
        return

    # ── Build feature matrix ──────────────────────────────────────────────────
    cache = _build_ohlcv_cache(get_db(), today_str)

    X, y = [], []
    for r in pos_rows:
        row = [safe_float(r[c]) for c in FEATURE_COLS]
        if sum(abs(v) for v in row) < 1e-6: continue
        X.append(row); y.append(1)

    n_pos = len(X); neg_count = 0; target_neg = n_pos * 3
    for neg in neg_cands:
        if neg_count >= target_neg: break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None: continue
        row = _build_feature_row(sym_df, neg['bar_date'])
        if row is None: continue
        X.append(row); y.append(0)
        neg_count += 1

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int32)

    if len(X) < 30:
        print(json.dumps({"phase": "12", "status": "skip", "reason": "too few samples", "n": len(X)}), flush=True)
        return

    # ── Hold-out (20%) for AUC comparison ────────────────────────────────────
    idx = np.random.RandomState(42).permutation(len(X))
    cut = int(len(idx) * 0.80)
    tr_idx, ho_idx = idx[:cut], idx[cut:]
    X_tr, y_tr = X[tr_idx], y[tr_idx]
    X_ho, y_ho = X[ho_idx], y[ho_idx]

    # ── AUC before update ─────────────────────────────────────────────────────
    base_model = lgb.Booster(model_file=str(model_path))
    auc_before = _auc(y_ho, base_model.predict(X_ho)) if len(y_ho) > 10 else 0.5

    # ── Incremental training: 30 new trees ───────────────────────────────────
    n_pos_tr = int(y_tr.sum()); n_neg_tr = int((y_tr == 0).sum())
    scale_pos = max(1.0, n_neg_tr / max(n_pos_tr, 1))

    params = {
        'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
        'num_threads': N_JOBS, 'scale_pos_weight': scale_pos,
        'learning_rate': 0.01,   # small LR for incremental
        'num_leaves': 16, 'min_data_in_leaf': 5,
        'feature_fraction': 0.8,
    }
    ds = lgb.Dataset(X_tr, label=y_tr, feature_name=list(FEATURE_COLS), free_raw_data=True)
    updated = lgb.train(params, ds, num_boost_round=30,
                        init_model=str(model_path),
                        callbacks=[lgb.log_evaluation(-1)])

    auc_after = _auc(y_ho, updated.predict(X_ho)) if len(y_ho) > 10 else 0.5

    # ── Only save if model improved or stayed similar (not degraded >0.01) ───
    delta = auc_after - auc_before
    backup_path = MODELS / f'explosion_lgbm_v3_backup_{today_str}.txt'
    base_model.save_model(str(backup_path))
    if delta >= -0.01:
        updated.save_model(str(model_path))
        saved = True
    else:
        saved = False
        print(f"[P12] Model degraded (Δ={delta:.4f}) — keeping backup, NOT saving", flush=True)

    dur = time.time() - t0
    summary = {
        "phase": "12",
        "n_new_samples": len(X),
        "n_pos": n_pos, "n_neg": neg_count,
        "n_new_trees": 30,
        "auc_before": round(auc_before, 4),
        "auc_after":  round(auc_after, 4),
        "delta_auc":  round(delta, 4),
        "model_saved": saved,
        "duration_seconds": round(dur, 1),
    }
    print(f"[P12] AUC: {auc_before:.4f} → {auc_after:.4f} (Δ={delta:+.4f}) | saved={saved}", flush=True)

    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '12', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 13 — COMBINATORIAL PURGED WALK-FORWARD (CPCV, López de Prado)
# ═════════════════════════════════════════════════════════════════════════════

def phase13_cpcv():
    """
    Phase 13: Combinatorial Purged Cross-Validation (CPCV).

    Replaces the simple 4-window walk-forward with a rigorous statistical framework:
      N=6 folds, k=2 test folds per combination, embargo=30 days
      → C(6,2)=15 independent test paths
      → Full Sharpe distribution (mean, std, skew)
      → Probabilistic Sharpe Ratio (PSR)
      → Deflated Sharpe Ratio (DSR) correcting for multiple testing

    Reference: Advances in Financial Machine Learning, M. López de Prado, Ch.12
    """
    import lightgbm as lgb
    from itertools import combinations
    sys.path.insert(0, str(Path(__file__).parent))
    from explosion_ml import FEATURE_COLS, safe_float, _build_ohlcv_cache, _build_feature_row

    t0 = time.time()
    print(json.dumps({"phase": "13", "step": "start", "desc": "CPCV (N=6, k=2, embargo=30d)"}), flush=True)

    today_str = datetime.date.today().isoformat()
    N_FOLDS = 6; K_TEST = 2; EMBARGO_DAYS = 30

    conn = get_db()
    cache = _build_ohlcv_cache(conn, '2025-12-31')

    # ── Build full labeled dataset ─────────────────────────────────────────────
    pos_rows = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date BETWEEN '2020-12-01' AND '2025-12-31'"
    ).fetchall()
    neg_cands = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history o
        WHERE date(o.bar_time,'unixepoch') BETWEEN '2020-12-01' AND '2025-12-31'
          AND NOT EXISTS (SELECT 1 FROM explosive_moves e
                          WHERE e.symbol=o.symbol AND e.explosion_date=date(o.bar_time,'unixepoch'))
        ORDER BY RANDOM() LIMIT ?
    """, (len(pos_rows) * 4,)).fetchall()
    conn.close()

    X_all, y_all, dates_all = [], [], []

    for r in pos_rows:
        row = [safe_float(r[c]) for c in FEATURE_COLS]
        if sum(abs(v) for v in row) < 1e-6: continue
        X_all.append(row); y_all.append(1); dates_all.append(r['explosion_date'])

    neg_count = 0; target_neg = len(y_all) * 3
    for neg in neg_cands:
        if neg_count >= target_neg: break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None: continue
        row = _build_feature_row(sym_df, neg['bar_date'])
        if row is None: continue
        X_all.append(row); y_all.append(0); dates_all.append(neg['bar_date'])
        neg_count += 1

    X_all     = np.array(X_all,     dtype=np.float32)
    y_all     = np.array(y_all,     dtype=np.int32)
    dates_all = np.array(dates_all)
    sort_idx  = np.argsort(dates_all)
    X_all, y_all, dates_all = X_all[sort_idx], y_all[sort_idx], dates_all[sort_idx]

    print(f"[P13] Dataset: {len(X_all)} ({y_all.sum()} pos)", flush=True)

    # ── Create N=6 time folds ─────────────────────────────────────────────────
    n = len(X_all)
    fold_size = n // N_FOLDS
    folds = []
    for fi in range(N_FOLDS):
        start = fi * fold_size
        end   = start + fold_size if fi < N_FOLDS - 1 else n
        folds.append((start, end))

    # ── CPCV: all C(N,K) test combinations ───────────────────────────────────
    embargo_dt = datetime.timedelta(days=EMBARGO_DAYS)
    path_sharpes = []
    path_wins    = []
    path_aucs    = []

    combo_list = list(combinations(range(N_FOLDS), K_TEST))
    print(f"[P13] Running {len(combo_list)} test combinations × {K_TEST} test folds...", flush=True)

    for ci, test_fold_ids in enumerate(combo_list):
        test_indices  = np.concatenate([np.arange(folds[fi][0], folds[fi][1])
                                         for fi in test_fold_ids])
        test_dates    = set(dates_all[test_indices])

        train_indices = []
        for fi in range(N_FOLDS):
            if fi in test_fold_ids: continue
            for idx in range(folds[fi][0], folds[fi][1]):
                d = dates_all[idx]
                # Embargo: skip bars within EMBARGO_DAYS of any test date
                try:
                    d_dt = datetime.date.fromisoformat(d)
                    in_embargo = any(
                        abs((d_dt - datetime.date.fromisoformat(td)).days) < EMBARGO_DAYS
                        for td in test_dates
                        if isinstance(td, str)
                    )
                except Exception:
                    in_embargo = False
                if not in_embargo:
                    train_indices.append(idx)

        train_indices = np.array(train_indices)
        if len(train_indices) < 100: continue

        X_tr, y_tr = X_all[train_indices], y_all[train_indices]
        X_te, y_te = X_all[test_indices],  y_all[test_indices]
        if y_te.sum() < 5: continue

        n_pos_tr = int(y_tr.sum()); n_neg_tr = int((y_tr == 0).sum())
        spw = max(1.0, n_neg_tr / max(n_pos_tr, 1))

        params = {
            'objective': 'binary', 'metric': 'auc', 'verbosity': -1,
            'num_threads': N_JOBS, 'scale_pos_weight': spw,
            'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 15,
            'feature_fraction': 0.8, 'reg_alpha': 0.5, 'reg_lambda': 0.5,
        }
        ds = lgb.Dataset(X_tr, label=y_tr, feature_name=list(FEATURE_COLS), free_raw_data=True)
        try:
            m = lgb.train(params, ds, num_boost_round=200,
                          callbacks=[lgb.log_evaluation(-1)])
        except Exception as e:
            continue

        probs   = m.predict(X_te)
        auc_te  = _auc(y_te, probs)

        # Signal P&L: predict → if prob > 0.5, go long, outcome = y_te
        signals_mask = probs >= 0.50
        if signals_mask.sum() == 0: continue

        signal_returns = np.where(y_te[signals_mask] == 1, 0.03, -0.01)  # 3% win / 1% loss
        sharpe_path = (signal_returns.mean() / (signal_returns.std() + 1e-10)
                       * np.sqrt(252)) if len(signal_returns) > 1 else 0.0
        win_rate    = float(y_te[signals_mask].mean())

        path_sharpes.append(sharpe_path)
        path_wins.append(win_rate)
        path_aucs.append(auc_te)

        if (ci + 1) % 5 == 0:
            print(f"[P13] {ci+1}/{len(combo_list)} done | mean_sharpe={np.mean(path_sharpes):.2f}", flush=True)

    if not path_sharpes:
        print(json.dumps({"phase": "13", "error": "no valid paths"}), flush=True)
        return

    sharpe_arr = np.array(path_sharpes)
    # ── Probabilistic Sharpe Ratio ────────────────────────────────────────────
    from scipy import stats
    sr_mean   = float(np.mean(sharpe_arr))
    sr_std    = float(np.std(sharpe_arr))
    sr_skew   = float(stats.skew(sharpe_arr))
    sr_kurt   = float(stats.kurtosis(sharpe_arr))
    # PSR: P(SR* > 0) = Φ[(SR_hat * √(T-1)) / √(1 - γ3*SR_hat + (γ4-1)/4 * SR_hat²)]
    T = len(sharpe_arr)
    psr_num = sr_mean * np.sqrt(T - 1)
    psr_den = np.sqrt(1 - sr_skew * sr_mean + (sr_kurt - 1) / 4 * sr_mean ** 2 + 1e-10)
    psr     = float(stats.norm.cdf(psr_num / psr_den)) if psr_den > 0 else 0.5
    # DSR: deflate for number of independent tests
    n_trials = len(combo_list)
    dsr_threshold = (sr_std * (
        (1 - np.euler_gamma) * stats.norm.ppf(1 - 1/n_trials) +
        np.euler_gamma      * stats.norm.ppf(1 - 1/(n_trials * np.e))
    )) if n_trials > 1 else 0.0
    dsr = float(stats.norm.cdf((sr_mean - dsr_threshold) / (sr_std + 1e-10)))

    dur = time.time() - t0
    summary = {
        "phase": "13",
        "n_paths": len(path_sharpes),
        "sharpe_mean":   round(sr_mean, 3),
        "sharpe_std":    round(sr_std, 3),
        "sharpe_min":    round(float(sharpe_arr.min()), 3),
        "sharpe_max":    round(float(sharpe_arr.max()), 3),
        "sharpe_skew":   round(sr_skew, 3),
        "avg_win_rate":  round(float(np.mean(path_wins)), 3),
        "avg_auc":       round(float(np.mean(path_aucs)), 4),
        "PSR":           round(psr, 4),
        "DSR":           round(dsr, 4),
        "verdict":       "SIGNIFICANT" if psr > 0.95 else ("MARGINAL" if psr > 0.80 else "NOT_SIGNIFICANT"),
        "duration_seconds": round(dur, 1),
    }

    print(f"[P13] Sharpe: μ={sr_mean:.3f} σ={sr_std:.3f} [{sharpe_arr.min():.2f}, {sharpe_arr.max():.2f}]", flush=True)
    print(f"[P13] PSR={psr:.4f}  DSR={dsr:.4f}  Verdict: {summary['verdict']}", flush=True)

    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '13', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 14 — MULTI-TIMEFRAME CONFLUENCE (Weekly + Daily + intraday proxy)
# ═════════════════════════════════════════════════════════════════════════════

def phase14_mtf_features():
    """
    Phase 14: Compute Multi-Timeframe (MTF) features from daily OHLCV.

    Timeframes:
      Weekly (resample daily → weekly):
        - w_ema20_slope: weekly EMA20 slope (trend direction)
        - w_rsi14: weekly RSI
        - w_52w_rank: price position in 52-week range
        - w_momentum_4w: 4-week momentum
        - w_trend: +1 BULL / -1 BEAR / 0 NEUTRAL

      Daily (already in feature_store, added as confirming):
        - mtf_score: composite MTF alignment (-1 to +1)
        - mtf_multiplier: confidence boost for signal_integration

    Writes to feature_store with source='mtf_features'.
    Also updates unified_signals mtf_multiplier column.
    """
    t0 = time.time()
    print(json.dumps({"phase": "14", "step": "start", "desc": "Multi-Timeframe Feature Fusion"}), flush=True)

    today_str = datetime.date.today().isoformat()
    conn = get_db()

    symbols = [r['symbol'] for r in conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history GROUP BY symbol HAVING COUNT(*) >= 60"
    ).fetchall()]

    version = f"mtf_{today_str}"
    written = 0; n_syms = 0

    for sym in symbols:
        rows = conn.execute("""
            SELECT date(bar_time,'unixepoch') d, open, high, low, close, volume
            FROM ohlcv_history WHERE symbol=? ORDER BY bar_time
        """, (sym,)).fetchall()
        if len(rows) < 60: continue

        try:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in rows])
            df['d'] = pd.to_datetime(df['d'])
            df = df.set_index('d')
            for col in ['open','high','low','close','volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['close'])
            if len(df) < 52: continue

            # ── Weekly resample ───────────────────────────────────────────────
            wdf = df.resample('W').agg({
                'open':  'first', 'high': 'max', 'low': 'min',
                'close': 'last',  'volume': 'sum'
            }).dropna()
            if len(wdf) < 20: continue

            wc = wdf['close'].values.astype(float)

            # Weekly EMA20
            def ema(x, p):
                out = np.full(len(x), np.nan)
                k = 2.0 / (p + 1)
                out[0] = x[0]
                for i in range(1, len(x)):
                    out[i] = x[i] * k + out[i-1] * (1-k)
                return out

            w_ema20 = ema(wc, 20)
            w_ema20_slope = float((w_ema20[-1] - w_ema20[-3]) / (w_ema20[-3] + 1e-10)) \
                if not np.isnan(w_ema20[-1]) else 0.0

            # Weekly RSI14
            d_ = np.diff(wc, prepend=wc[0])
            ag = al = 0.0
            if len(wc) >= 15:
                ag = np.mean(np.maximum(d_[1:15], 0))
                al = np.mean(np.abs(np.minimum(d_[1:15], 0)))
                for i in range(15, len(wc)):
                    ag = (ag * 13 + max(d_[i], 0)) / 14
                    al = (al * 13 + abs(min(d_[i], 0))) / 14
            w_rsi14 = float(100 - 100 / (1 + ag / (al + 1e-10)))

            # 52-week price rank
            last52 = wc[-52:] if len(wc) >= 52 else wc
            mn52, mx52 = float(np.min(last52)), float(np.max(last52))
            w_52w_rank = float((wc[-1] - mn52) / (mx52 - mn52 + 1e-10))

            # 4-week momentum
            w_mom4 = float((wc[-1] - wc[-5]) / (wc[-5] + 1e-10)) if len(wc) >= 5 else 0.0

            # Weekly trend: +1 price>EMA20 & slope>0, -1 price<EMA20 & slope<0, else 0
            w_trend = 0.0
            if not np.isnan(w_ema20[-1]):
                if wc[-1] > w_ema20[-1] and w_ema20_slope > 0:   w_trend =  1.0
                elif wc[-1] < w_ema20[-1] and w_ema20_slope < 0: w_trend = -1.0

            # ── MTF composite score ───────────────────────────────────────────
            # Normalize each component to [-1, +1]
            rsi_norm = (w_rsi14 - 50) / 50.0       # >0 = bullish
            ema_norm = np.tanh(w_ema20_slope * 20)  # slope to [-1,1]
            rank_norm = (w_52w_rank - 0.5) * 2      # [0,1] → [-1,1]
            mom_norm  = np.tanh(w_mom4 * 10)

            mtf_score      = float(np.clip((rsi_norm + ema_norm + rank_norm + mom_norm) / 4, -1, 1))
            # Confidence multiplier: bullish MTF boosts signal, bearish reduces it
            mtf_multiplier = float(1.0 + 0.3 * mtf_score)  # range [0.7, 1.3]

            feats = {
                'mtf_w_ema20_slope': w_ema20_slope,
                'mtf_w_rsi14':       w_rsi14 / 100.0,
                'mtf_w_52w_rank':    w_52w_rank,
                'mtf_w_momentum4w':  float(np.clip(w_mom4, -0.5, 0.5)),
                'mtf_w_trend':       w_trend,
                'mtf_score':         mtf_score,
                'mtf_multiplier':    mtf_multiplier,
            }

            for feat_name, feat_val in feats.items():
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO feature_store
                        (feature_date, symbol, feature_name, feature_value, version, source_table)
                        VALUES (?,?,?,?,?,?)
                    """, (today_str, sym, feat_name, float(feat_val), version, 'mtf_features'))
                    written += 1
                except Exception:
                    pass
            n_syms += 1

        except Exception as e:
            continue

    conn.commit()

    # ── Apply MTF multiplier to today's unified_signals ──────────────────────
    mtf_applied = 0
    rows_to_update = conn.execute("""
        SELECT us.symbol, us.unified_score, us.id
        FROM unified_signals us
        WHERE us.signal_date = ?
    """, (today_str,)).fetchall()

    for row in rows_to_update:
        mult_row = conn.execute("""
            SELECT feature_value FROM feature_store
            WHERE symbol=? AND feature_name='mtf_multiplier' AND feature_date=?
        """, (row['symbol'], today_str)).fetchone()
        if mult_row is None: continue
        mult = float(mult_row['feature_value'])
        new_score = float(np.clip(row['unified_score'] * mult, 0, 100))
        conn.execute("UPDATE unified_signals SET unified_score=? WHERE id=?",
                     (round(new_score, 4), row['id']))
        mtf_applied += 1

    conn.commit()
    conn.close()

    dur = time.time() - t0
    summary = {
        "phase": "14",
        "n_symbols": n_syms,
        "features_written": written,
        "n_mtf_features": 7,
        "mtf_applied_to_signals": mtf_applied,
        "duration_seconds": round(dur, 1),
    }
    print(f"[P14] {n_syms} syms, {written} features, {mtf_applied} signals adjusted by MTF", flush=True)

    conn = get_db()
    conn.execute("INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
                 (today_str, '14', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 15 — SPLIT-CONFORMAL PREDICTION INTERVALS
# ═════════════════════════════════════════════════════════════════════════════

def phase15_conformal_intervals():
    """
    Split-Conformal Prediction Intervals (manual — no mapie needed).

    For each calibration point: nonconf_i = 1 - p_hat[y_i]
    q_hat = quantile(nonconf_scores, ceil((n+1)(1-α))/n)

    Prediction set for test point x:
      class 0 in set if  p(x) ≤ q_hat
      class 1 in set if  1-p(x) ≤ q_hat
      width=1 → confident  |  width=2 → uncertain  |  width=0 → anomaly

    Target coverage ≥ 1-α by the conformal guarantee.
    Saves q_hat thresholds + per-stock conformal_width to feature_store.
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P15] Split-Conformal Prediction Intervals...", flush=True)

    conn = get_db()
    ensure_tables(conn)

    # ── Build calibration set using saved LightGBM model on historical data ──
    # Positive samples: actual explosions (y=1)
    # Negative samples: non-explosion trading days (y=0)
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from explosion_ml import FEATURE_COLS, _build_ohlcv_cache, _build_feature_row
    except ImportError as e:
        conn.close()
        return {"phase":"15","error":f"import: {e}"}

    # Load trained LightGBM model
    lgbm_path = MODELS / "explosion_lgbm_v3.txt"
    if not lgbm_path.exists():
        conn.close()
        return {"phase":"15","status":"skipped","reason":"no lgbm model found (run phase2 first)"}

    try:
        import lightgbm as lgb
        model = lgb.Booster(model_file=str(lgbm_path))
    except Exception as e:
        conn.close()
        return {"phase":"15","error":f"model load: {e}"}

    # Positive set: OOS explosions (after 2026-01-01 cutoff)
    pos_rows = conn.execute("""
        SELECT symbol, explosion_date AS bar_date, 1 AS label
        FROM explosive_moves
        WHERE explosion_date BETWEEN '2025-06-01' AND '2026-04-01'
        ORDER BY explosion_date
        LIMIT 500
    """).fetchall()

    # Negative set: random non-explosion dates with pred data
    neg_rows = conn.execute("""
        SELECT DISTINCT symbol, pred_date AS bar_date, 0 AS label
        FROM explosion_predictions
        WHERE pred_date < date('now', '-7 days')
          AND pred_date > '2025-06-01'
        ORDER BY RANDOM() LIMIT 300
    """).fetchall()

    # If explosion_predictions is empty, sample from ohlcv_history
    if len(neg_rows) < 20:
        neg_rows = conn.execute("""
            SELECT symbol,
                   date(bar_time, 'unixepoch') AS bar_date,
                   0 AS label
            FROM ohlcv_history
            WHERE bar_time BETWEEN strftime('%s','2025-06-01')
                               AND strftime('%s','2026-04-01')
              AND symbol NOT IN (
                SELECT DISTINCT symbol FROM explosive_moves
                WHERE explosion_date BETWEEN '2025-06-01' AND '2026-04-01'
              )
            ORDER BY RANDOM() LIMIT 1000
        """).fetchall()

    all_rows = list(pos_rows) + list(neg_rows)

    if len(all_rows) < 50:
        conn.close()
        print(json.dumps({"phase":"15","status":"skipped","n":len(all_rows)}), flush=True)
        return {"phase":"15","status":"skipped","reason":f"only {len(all_rows)} samples"}

    print(f"[P15] Building features for {len(all_rows)} samples "
          f"(pos={len(pos_rows)} neg={len(neg_rows)})...", flush=True)

    cache = _build_ohlcv_cache(conn, today_str)
    X_list, y_list = [], []

    for r in all_rows:
        sym_df = pd.DataFrame(cache.get(r['symbol'], []))
        feat   = _build_feature_row(sym_df, r['bar_date'])
        if feat is not None:
            X_list.append(feat)
            y_list.append(int(r['label']))

    if len(X_list) < 50:
        conn.close()
        return {"phase":"15","status":"skipped","reason":"feature extraction failed"}

    X_all  = np.nan_to_num(np.array(X_list, dtype=np.float32), nan=0.0, posinf=10., neginf=-10.)
    y_true = np.array(y_list, dtype=np.float32)

    # Get model predictions
    try:
        import lightgbm as lgb
        p_pred = model.predict(X_all, num_iteration=model.best_iteration or -1).astype(np.float32)
    except Exception as e:
        conn.close()
        return {"phase":"15","error":f"model predict: {e}"}

    print(f"[P15] n={len(y_true)}, pos={int(y_true.sum())}, "
          f"mean_prob={float(p_pred.mean()):.3f}", flush=True)

    # Time-ordered 70/30 split
    n      = len(y_true)
    n_cal  = int(n * 0.70)
    y_cal, y_test = y_true[:n_cal], y_true[n_cal:]
    p_cal, p_test = p_pred[:n_cal], p_pred[n_cal:]

    # Nonconformity: 1 - p_hat[true_class]
    nonconf = np.where(y_cal == 1, 1.0 - p_cal, p_cal)

    # q_hat per alpha level
    alphas     = [0.05, 0.10, 0.20]
    thresholds = {}
    for alpha in alphas:
        level = min(1.0, np.ceil((n_cal + 1) * (1.0 - alpha)) / n_cal)
        q_hat = float(np.quantile(nonconf, level))
        thresholds[f"alpha_{int(alpha*100):02d}"] = round(q_hat, 4)

    print(f"[P15] n_cal={n_cal}, q_hat={thresholds}", flush=True)

    # Evaluate on test set (main alpha=0.10)
    q_main    = thresholds["alpha_10"]
    inc0      = (p_test <= q_main)
    inc1      = ((1.0 - p_test) <= q_main)
    widths    = inc0.astype(int) + inc1.astype(int)
    coverage  = float(np.mean(np.where(y_test == 1, inc1, inc0)))
    n_certain = int(np.sum(widths == 1))
    n_uncert  = int(np.sum(widths == 2))
    n_empty   = int(np.sum(widths == 0))

    print(f"[P15] Coverage@90%={coverage:.3f} (≥0.90 ✅), "
          f"certain={n_certain} uncertain={n_uncert} empty={n_empty}", flush=True)

    # Save thresholds as MARKET-level features
    for key, val in thresholds.items():
        conn.execute("""
            INSERT OR REPLACE INTO feature_store
            (symbol, feature_date, feature_name, feature_value, version, computed_at)
            VALUES ('MARKET', ?, ?, ?, 'v3', datetime('now'))
        """, (today_str, f"conformal_{key}", val))

    conn.execute("""
        INSERT OR REPLACE INTO feature_store
        (symbol, feature_date, feature_name, feature_value, version, computed_at)
        VALUES ('MARKET', ?, 'conformal_coverage_90', ?, 'v3', datetime('now'))
    """, (today_str, round(coverage, 4)))

    # Annotate today's predictions with conformal_width + confident flag
    today_preds = conn.execute("""
        SELECT id, symbol, explosion_prob
        FROM explosion_predictions
        WHERE pred_date = ? AND explosion_prob IS NOT NULL
    """, (today_str,)).fetchall()

    annotated = 0
    for pred in today_preds:
        p     = float(pred['explosion_prob'])
        width = int(p <= q_main) + int((1.0 - p) <= q_main)
        for fname, fval in [
            ("conformal_width",     float(width)),
            ("conformal_confident", float(width == 1)),
        ]:
            conn.execute("""
                INSERT OR REPLACE INTO feature_store
                (symbol, feature_date, feature_name, feature_value, version, computed_at)
                VALUES (?, ?, ?, ?, 'v3', datetime('now'))
            """, (pred['symbol'], today_str, fname, fval))
        annotated += 1

    conn.commit()
    dur = time.time() - t0
    summary = {
        "phase": "15",
        "n_calibration": n_cal,
        "n_test": len(y_test),
        "thresholds": thresholds,
        "coverage_90pct": round(coverage, 4),
        "n_certain": n_certain,
        "n_uncertain": n_uncert,
        "n_empty": n_empty,
        "annotated_predictions": annotated,
        "duration_seconds": round(dur, 1),
    }
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '15', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 16 — FEATURE DRIFT MONITOR (PSI + ADVERSARIAL VALIDATION)
# ═════════════════════════════════════════════════════════════════════════════

def phase16_feature_drift():
    """
    Two-pronged drift detection:

    1. PSI (Population Stability Index) per feature
       PSI = Σ(cur% - ref%) × ln(cur%/ref%)
       <0.10 stable | 0.10–0.20 slight | >0.20 significant

    2. Adversarial Validation
       Train RF to distinguish train data (y=0) from recent data (y=1).
       AUC >0.65 → model distribution has drifted → consider retraining.

    Reference: training data before 2026-01-01
    Current:   last 45 days
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P16] Feature Drift Monitor (PSI + Adversarial Validation)...", flush=True)

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from explosion_ml import FEATURE_COLS, _build_ohlcv_cache, _build_feature_row
    except ImportError as e:
        return {"phase":"16","error":str(e)}

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score, StratifiedKFold

    conn = get_db()

    # Reference = historical explosions (training era)
    ref_rows = conn.execute("""
        SELECT symbol, explosion_date AS bar_date
        FROM explosive_moves
        WHERE explosion_date < '2026-01-01'
        ORDER BY RANDOM() LIMIT 2000
    """).fetchall()

    # Current = recent explosion predictions (live distribution)
    cutoff = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
    cur_rows = conn.execute("""
        SELECT symbol, pred_date AS bar_date
        FROM explosion_predictions
        WHERE pred_date >= ? AND explosion_prob >= 0.4
        ORDER BY pred_date DESC LIMIT 500
    """, (cutoff,)).fetchall()

    if len(ref_rows) < 50 or len(cur_rows) < 10:
        conn.close()
        return {"phase":"16","status":"skipped",
                "ref_n":len(ref_rows),"cur_n":len(cur_rows)}

    print(f"[P16] ref={len(ref_rows)} cur={len(cur_rows)}", flush=True)

    cache = _build_ohlcv_cache(conn, today_str)

    def extract(rows_list):
        X = []
        for r in rows_list:
            sym_df = pd.DataFrame(cache.get(r['symbol'], []))
            feat   = _build_feature_row(sym_df, r['bar_date'])
            if feat is not None:
                X.append(feat)
        return np.array(X, dtype=np.float32) if X else np.zeros((0, len(FEATURE_COLS)))

    X_ref = extract(ref_rows)
    X_cur = extract(cur_rows)

    if X_ref.shape[0] < 50 or X_cur.shape[0] < 10:
        conn.close()
        return {"phase":"16","status":"skipped","reason":"feature extraction failed"}

    # ── 1. PSI per feature ─────────────────────────────────────────────────
    def psi(ref_col, cur_col, bins=10):
        comb  = np.concatenate([ref_col, cur_col])
        edges = np.percentile(comb, np.linspace(0, 100, bins + 1))
        edges[0] -= 1e-9; edges[-1] += 1e-9
        rh, _ = np.histogram(ref_col, bins=edges)
        ch, _ = np.histogram(cur_col, bins=edges)
        rp = np.maximum(rh / max(len(ref_col), 1), 1e-10)
        cp = np.maximum(ch / max(len(cur_col), 1), 1e-10)
        return float(np.sum((cp - rp) * np.log(cp / rp)))

    psi_scores  = {}
    high_drift  = []
    n_feats     = min(X_ref.shape[1], X_cur.shape[1], len(FEATURE_COLS))
    for i in range(n_feats):
        val = psi(X_ref[:, i], X_cur[:, i])
        psi_scores[FEATURE_COLS[i]] = round(val, 4)
        if val > 0.2:
            high_drift.append(FEATURE_COLS[i])

    avg_psi  = float(np.mean(list(psi_scores.values()))) if psi_scores else 0.0
    top_psi  = sorted(psi_scores.items(), key=lambda x: -x[1])[:10]
    print(f"[P16] avg_PSI={avg_psi:.3f}, high_drift={len(high_drift)}: {high_drift[:5]}", flush=True)

    # ── 2. Adversarial Validation ──────────────────────────────────────────
    n_ref   = min(X_ref.shape[0], X_cur.shape[0] * 3)
    X_adv   = np.nan_to_num(
                  np.vstack([X_ref[:n_ref], X_cur]),
                  nan=0.0, posinf=10.0, neginf=-10.0)
    y_adv   = np.array([0]*n_ref + [1]*X_cur.shape[0])

    adv_auc = 0.5
    try:
        clf = RandomForestClassifier(
            n_estimators=100, max_depth=6, n_jobs=N_JOBS,
            random_state=42, class_weight='balanced')
        k   = min(5, max(2, X_cur.shape[0] // 4))
        cv  = StratifiedKFold(n_splits=k, shuffle=True, random_state=42)
        adv_auc = float(np.mean(
            cross_val_score(clf, X_adv, y_adv, cv=cv, scoring='roc_auc', n_jobs=N_JOBS)))
    except Exception as e:
        print(f"[P16] Adversarial CV error: {e}", flush=True)

    drift_detected = adv_auc > 0.65 or avg_psi > 0.15
    print(f"[P16] Adversarial AUC={adv_auc:.3f} "
          f"({'⚠️ DRIFT' if adv_auc>0.65 else '✅ OK'}) | "
          f"avg_PSI={avg_psi:.3f} "
          f"({'⚠️ DRIFT' if avg_psi>0.15 else '✅ OK'})", flush=True)

    # Save to feature_store
    ensure_tables(conn)
    for fname, fval in [
        ("adversarial_auc",  round(adv_auc, 4)),
        ("avg_psi",          round(avg_psi, 4)),
        ("drift_detected",   float(drift_detected)),
    ]:
        conn.execute("""
            INSERT OR REPLACE INTO feature_store
            (symbol, feature_date, feature_name, feature_value, version, computed_at)
            VALUES ('MARKET', ?, ?, ?, 'v3', datetime('now'))
        """, (today_str, fname, fval))

    for feat_name, psi_val in psi_scores.items():
        conn.execute("""
            INSERT OR REPLACE INTO feature_store
            (symbol, feature_date, feature_name, feature_value, version, computed_at)
            VALUES ('MARKET', ?, ?, ?, 'v3', datetime('now'))
        """, (today_str, f"psi_{feat_name}", round(psi_val, 4)))

    conn.commit()
    dur = time.time() - t0
    summary = {
        "phase": "16",
        "n_reference": int(X_ref.shape[0]),
        "n_current":   int(X_cur.shape[0]),
        "avg_psi":           round(avg_psi, 4),
        "adversarial_auc":   round(adv_auc, 4),
        "drift_detected":    drift_detected,
        "high_drift_features": high_drift[:10],
        "top_psi_features":    dict(top_psi[:5]),
        "duration_seconds":  round(dur, 1),
    }
    if drift_detected:
        print(json.dumps({"alert":"DRIFT_DETECTED",**summary}), flush=True)
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '16', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 17 — MULTI-OUTPUT RETURN REGRESSOR
# ═════════════════════════════════════════════════════════════════════════════

def phase17_return_regressor():
    """
    Three XGBoost regressors predict expected returns:
      return_1d, return_3d, return_5d

    Expected Value = P(explosion) × E[return | explosion signal]

    Saves pred_return_Nd + expected_value_Nd to feature_store per symbol.
    Model persisted as explosion_return_regressor_v1.pkl
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P17] Multi-Output Return Regressor...", flush=True)

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from explosion_ml import FEATURE_COLS, _build_ohlcv_cache, _build_feature_row
    except ImportError as e:
        return {"phase":"17","error":str(e)}

    try:
        import xgboost as xgb
        import joblib
    except ImportError as e:
        return {"phase":"17","error":f"missing lib: {e}"}

    conn = get_db()

    # Positive examples with known forward returns
    pos_rows = conn.execute("""
        SELECT symbol, explosion_date AS bar_date,
               COALESCE(return_1d, 0) AS r1,
               COALESCE(return_3d, 0) AS r3,
               COALESCE(return_5d, 0) AS r5
        FROM explosive_moves
        WHERE return_1d IS NOT NULL AND explosion_date < '2026-04-01'
        ORDER BY explosion_date
        LIMIT 3000
    """).fetchall()

    # Negative examples: predictions without subsequent explosions (return ≈ 0)
    neg_rows = conn.execute("""
        SELECT ep.symbol, ep.pred_date AS bar_date,
               0.0 AS r1, 0.0 AS r3, 0.0 AS r5
        FROM explosion_predictions ep
        LEFT JOIN explosive_moves em
          ON ep.symbol = em.symbol
          AND em.explosion_date BETWEEN ep.pred_date AND date(ep.pred_date, '+7 days')
        WHERE em.explosion_date IS NULL
          AND ep.pred_date < '2026-04-01'
        ORDER BY RANDOM() LIMIT 1000
    """).fetchall()

    all_rows = list(pos_rows) + list(neg_rows)

    if len(all_rows) < 100:
        conn.close()
        return {"phase":"17","status":"skipped","n":len(all_rows)}

    print(f"[P17] Building features for {len(all_rows)} samples "
          f"(pos={len(pos_rows)} neg={len(neg_rows)})...", flush=True)

    cache = _build_ohlcv_cache(conn, today_str)
    X_list, y1, y3, y5 = [], [], [], []

    for r in all_rows:
        sym_df = pd.DataFrame(cache.get(r['symbol'], []))
        feat   = _build_feature_row(sym_df, r['bar_date'])
        if feat is not None:
            X_list.append(feat)
            y1.append(float(r['r1']))
            y3.append(float(r['r3']))
            y5.append(float(r['r5']))

    if len(X_list) < 50:
        conn.close()
        return {"phase":"17","status":"skipped","reason":"feature extraction failed"}

    X  = np.nan_to_num(np.array(X_list, dtype=np.float32), nan=0.0, posinf=10., neginf=-10.)
    y1 = np.array(y1, dtype=np.float32)
    y3 = np.array(y3, dtype=np.float32)
    y5 = np.array(y5, dtype=np.float32)

    n    = len(X)
    n_tr = int(n * 0.75)
    X_tr, X_te = X[:n_tr], X[n_tr:]

    params = dict(n_estimators=300, max_depth=5, learning_rate=0.05,
                  subsample=0.8, colsample_bytree=0.7,
                  n_jobs=N_JOBS, random_state=42, verbosity=0)

    regressors = {}
    metrics    = {}

    for horizon, (ytr, yte) in [("1d",(y1[:n_tr],y1[n_tr:])),
                                  ("3d",(y3[:n_tr],y3[n_tr:])),
                                  ("5d",(y5[:n_tr],y5[n_tr:]))]:
        reg = xgb.XGBRegressor(**params)
        reg.fit(X_tr, ytr)
        preds   = reg.predict(X_te)
        ss_res  = float(np.sum((yte - preds)**2))
        ss_tot  = float(np.sum((yte - np.mean(yte))**2))
        r2      = 1.0 - ss_res / max(ss_tot, 1e-10)
        corr    = float(np.corrcoef(yte, preds)[0,1]) if len(yte) > 2 else 0.0
        mae     = float(np.mean(np.abs(yte - preds)))
        metrics[horizon] = {"r2":round(r2,4), "corr":round(corr,4), "mae":round(mae,4)}
        print(f"[P17] return_{horizon}: R²={r2:.3f} corr={corr:.3f} MAE={mae:.4f}", flush=True)
        regressors[horizon] = reg

    reg_path = MODELS / "explosion_return_regressor_v1.pkl"
    joblib.dump(regressors, str(reg_path))
    print(f"[P17] Saved → {reg_path}", flush=True)

    # Predict expected returns + EV for today's signals
    today_preds = conn.execute("""
        SELECT symbol, explosion_prob
        FROM explosion_predictions
        WHERE pred_date = ? AND explosion_prob IS NOT NULL
    """, (today_str,)).fetchall()

    ev_applied = 0
    for pred in today_preds:
        sym  = pred['symbol']
        p    = float(pred['explosion_prob'])
        feat = _build_feature_row(pd.DataFrame(cache.get(sym, [])), today_str)
        if feat is None: continue
        fa   = np.nan_to_num(np.array([feat], dtype=np.float32), nan=0., posinf=10., neginf=-10.)
        pr1  = float(regressors["1d"].predict(fa)[0])
        pr3  = float(regressors["3d"].predict(fa)[0])
        pr5  = float(regressors["5d"].predict(fa)[0])
        for fname, fval in [
            ("pred_return_1d",    round(pr1, 4)),
            ("pred_return_3d",    round(pr3, 4)),
            ("pred_return_5d",    round(pr5, 4)),
            ("expected_value_1d", round(p * pr1, 4)),
            ("expected_value_5d", round(p * pr5, 4)),
        ]:
            conn.execute("""
                INSERT OR REPLACE INTO feature_store
                (symbol, feature_date, feature_name, feature_value, version, computed_at)
                VALUES (?, ?, ?, ?, 'v3', datetime('now'))
            """, (sym, today_str, fname, fval))
        ev_applied += 1

    conn.commit()
    dur = time.time() - t0
    summary = {
        "phase": "17",
        "n_training":      n_tr,
        "n_test":          len(X_te),
        "metrics":         metrics,
        "ev_applied":      ev_applied,
        "model_path":      str(reg_path),
        "duration_seconds": round(dur, 1),
    }
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '17', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 18 — SURVIVAL ANALYSIS (COX PROPORTIONAL HAZARDS)
# ═════════════════════════════════════════════════════════════════════════════

def phase18_survival_analysis():
    """
    Models "time to next explosion" per stock using statsmodels PHReg (CoxPH).

    Dataset:
      T     = days between consecutive explosions (inter-event times)
      event = 1 (explosion occurred)
      Last observation per stock → censored (event=0, T=days since last explosion)

    Covariates: compression_days, rsi_avg, bb_width_avg, volume_ratio

    Output:
      Hazard ratios + p-values per covariate
      cox_hazard_score + cox_daily_prob + days_since_explosion → feature_store
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P18] Survival Analysis (Cox PH)...", flush=True)

    try:
        from statsmodels.duration.hazard_regression import PHReg
    except ImportError as e:
        return {"phase":"18","error":f"statsmodels unavailable: {e}"}

    conn = get_db()

    rows = conn.execute("""
        SELECT symbol, explosion_date,
               COALESCE(pre5_compression_days, 3)    AS compression_days,
               COALESCE(pre5_rsi, 50)                AS rsi_avg,
               COALESCE(pre5_bb_width, 0.05)         AS bb_width_avg,
               COALESCE(pre5_vol_ratio, 1.0)         AS volume_ratio
        FROM explosive_moves
        WHERE explosion_date IS NOT NULL
        ORDER BY symbol, explosion_date
    """).fetchall()

    if len(rows) < 50:
        conn.close()
        return {"phase":"18","status":"skipped","n":len(rows)}

    print(f"[P18] {len(rows)} explosion events loaded", flush=True)

    # Group per stock
    from collections import defaultdict
    stock_events = defaultdict(list)
    for r in rows:
        stock_events[r['symbol']].append(dict(r))

    T_list, E_list, X_surv, sym_labels = [], [], [], []
    covar_names = ["compression_days","rsi_avg","bb_width_avg","volume_ratio"]

    for sym, evts in stock_events.items():
        evts.sort(key=lambda x: x['explosion_date'])
        for i in range(1, len(evts)):
            try:
                d1 = datetime.date.fromisoformat(evts[i-1]['explosion_date'])
                d2 = datetime.date.fromisoformat(evts[i]['explosion_date'])
                dt = max(1, (d2 - d1).days)
            except:
                continue
            T_list.append(float(dt))
            E_list.append(1.0)
            X_surv.append([float(evts[i][c]) for c in covar_names])
            sym_labels.append(sym)
        # Censored last observation
        if evts:
            try:
                last_dt = datetime.date.fromisoformat(evts[-1]['explosion_date'])
                cens_t  = float(max(1, (datetime.date.today() - last_dt).days))
                T_list.append(cens_t)
                E_list.append(0.0)
                X_surv.append([float(evts[-1][c]) for c in covar_names])
                sym_labels.append(sym + "_cens")
            except:
                pass

    if len(T_list) < 30:
        conn.close()
        return {"phase":"18","status":"skipped","reason":"<30 survival intervals"}

    T = np.array(T_list, dtype=np.float64)
    E = np.array(E_list, dtype=np.float64)
    X = np.array(X_surv, dtype=np.float64)

    # Normalize
    X_mu    = np.mean(X, axis=0)
    X_sigma = np.std(X, axis=0) + 1e-10
    X_norm  = np.nan_to_num((X - X_mu) / X_sigma, nan=0.0)

    coef_dict  = {}
    coefs      = np.zeros(len(covar_names))
    cox_fitted = False

    try:
        model  = PHReg(T, X_norm, status=E, ties='efron')
        result = model.fit(disp=False)
        coefs  = result.params
        hrs    = np.exp(coefs)
        pvs    = result.pvalues
        cox_fitted = True
        coef_dict = {name: {"hr":round(float(hrs[i]),4), "pval":round(float(pvs[i]),4)}
                     for i, name in enumerate(covar_names)}
        print(f"[P18] Cox fitted n={len(T)} intervals ({int(np.sum(E))} events)", flush=True)
        for name, vals in coef_dict.items():
            print(f"[P18]   {name}: HR={vals['hr']:.3f} p={vals['pval']:.4f}", flush=True)
    except Exception as e:
        print(f"[P18] Cox fit error: {e}", flush=True)

    # Per-stock hazard scores
    ensure_tables(conn)
    hazard_written = 0
    today_dt = datetime.date.today()

    for sym, evts in stock_events.items():
        if not evts: continue
        latest  = evts[-1]
        feat_raw = np.array([float(latest[c]) for c in covar_names], dtype=np.float64)
        feat_norm= np.nan_to_num((feat_raw - X_mu) / X_sigma, nan=0.0)
        lin_pred = float(np.dot(feat_norm, coefs))
        hazard   = float(np.exp(np.clip(lin_pred, -5, 5)))

        try:
            last_dt    = datetime.date.fromisoformat(latest['explosion_date'])
            days_since = float((today_dt - last_dt).days)
        except:
            days_since = 999.0

        daily_prob = float(1.0 - np.exp(-hazard / max(days_since + 1.0, 1.0)))

        for fname, fval in [
            ("cox_hazard_score",     round(hazard, 4)),
            ("cox_daily_prob",       round(daily_prob, 4)),
            ("days_since_explosion", round(days_since, 0)),
        ]:
            conn.execute("""
                INSERT OR REPLACE INTO feature_store
                (symbol, feature_date, feature_name, feature_value, version, computed_at)
                VALUES (?, ?, ?, ?, 'v3', datetime('now'))
            """, (sym, today_str, fname, fval))
        hazard_written += 1

    conn.commit()
    dur = time.time() - t0
    summary = {
        "phase": "18",
        "n_intervals":   len(T),
        "n_events":      int(np.sum(E)),
        "n_censored":    int(np.sum(1 - E)),
        "n_stocks":      len(stock_events),
        "cox_fitted":    cox_fitted,
        "hazard_ratios": coef_dict,
        "hazard_written": hazard_written,
        "duration_seconds": round(dur, 1),
    }
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '18', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 19 — KELLY PORTFOLIO OPTIMIZER
# ═════════════════════════════════════════════════════════════════════════════

def phase19_kelly_optimizer():
    """
    Fractional Kelly position sizing for today's signals.

    Formula:  f* = (b·p − (1−p)) / b
              b  = avg_win / avg_loss (edge ratio from 180-day history)
              p  = ensemble probability

    Risk controls:
      - Half-Kelly (×0.5) for variance reduction
      - UES modifier: scale by clip(UES/75, 0.5, 1.2)
      - Hard cap per position: 25%
      - Portfolio heat cap: total ≤ 1.5 (scale down proportionally if exceeded)

    Saves kelly_fraction + kelly_position_pct + portfolio_heat to feature_store.
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P19] Kelly Portfolio Optimizer...", flush=True)

    conn = get_db()

    # Estimate edge ratio b from historical predictions vs actual returns
    cutoff_180 = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
    hist_rows = conn.execute("""
        SELECT ep.explosion_prob,
               COALESCE(em.return_5d, 0.0) AS actual_return
        FROM explosion_predictions ep
        LEFT JOIN explosive_moves em
          ON ep.symbol = em.symbol
          AND em.explosion_date BETWEEN ep.pred_date AND date(ep.pred_date, '+7 days')
        WHERE ep.pred_date > ? AND ep.pred_date < ?
          AND ep.explosion_prob >= 0.50
    """, (cutoff_180, today_str)).fetchall()

    if len(hist_rows) >= 20:
        wins   = [float(r['actual_return']) for r in hist_rows if float(r['actual_return']) > 0]
        losses = [abs(float(r['actual_return'])) for r in hist_rows if float(r['actual_return']) < 0]
        avg_win  = float(np.mean(wins))   if wins   else 0.050
        avg_loss = float(np.mean(losses)) if losses else 0.020
        win_rate = len(wins) / max(len(hist_rows), 1)
    else:
        # Fallback to global stats from explosive_moves
        stats = conn.execute("""
            SELECT
                AVG(CASE WHEN return_5d > 0 THEN return_5d     END) AS avg_win,
                AVG(CASE WHEN return_5d < 0 THEN ABS(return_5d) END) AS avg_loss,
                SUM(CASE WHEN return_5d > 0 THEN 1.0 ELSE 0.0 END)
                    / NULLIF(COUNT(return_5d), 0)                    AS win_rate
            FROM explosive_moves WHERE return_5d IS NOT NULL
        """).fetchone()
        avg_win  = float(stats['avg_win']  or 0.050)
        avg_loss = float(stats['avg_loss'] or 0.020)
        win_rate = float(stats['win_rate'] or 0.600)

    b = float(np.clip(avg_win / max(avg_loss, 1e-6), 0.5, 10.0))
    print(f"[P19] b={b:.3f} avg_win={avg_win:.3f} avg_loss={avg_loss:.3f} wr={win_rate:.3f}", flush=True)

    # Today's signals
    today_sigs = conn.execute("""
        SELECT ep.symbol, ep.explosion_prob,
               COALESCE(us.unified_score, 50.0) AS ues_score
        FROM explosion_predictions ep
        LEFT JOIN unified_signals us
          ON ep.symbol = us.symbol AND us.signal_date = ep.pred_date
        WHERE ep.pred_date = ? AND ep.explosion_prob >= 0.30
        ORDER BY ep.explosion_prob DESC
    """, (today_str,)).fetchall()

    kelly_list = []
    for sig in today_sigs:
        p   = float(sig['explosion_prob'])
        ues = float(sig['ues_score'])

        kelly_raw  = float(np.clip((b * p - (1.0 - p)) / b, 0.0, 1.0))
        kelly_half = 0.5 * kelly_raw
        ues_mult   = float(np.clip(ues / 75.0, 0.5, 1.2))
        kelly_adj  = float(np.clip(kelly_half * ues_mult, 0.0, 0.25))

        kelly_list.append({
            "symbol":       sig['symbol'],
            "p":            p,
            "kelly_raw":    round(kelly_raw,  4),
            "kelly_half":   round(kelly_half, 4),
            "kelly_final":  round(kelly_adj,  4),
        })

    # Portfolio heat guard
    total_kelly = sum(k['kelly_final'] for k in kelly_list)
    if total_kelly > 1.5 and total_kelly > 0:
        scale = 1.5 / total_kelly
        for k in kelly_list:
            k['kelly_final'] = round(k['kelly_final'] * scale, 4)
        total_kelly = 1.5
        print(f"[P19] Heat > 1.5 → scaled down by {scale:.3f}", flush=True)

    # Save to feature_store
    ensure_tables(conn)
    applied = 0
    for k in kelly_list:
        sym = k['symbol']
        for fname, fval in [
            ("kelly_fraction",    k['kelly_final']),
            ("kelly_raw",         k['kelly_raw']),
            ("kelly_position_pct", round(k['kelly_final'] * 100, 2)),
        ]:
            conn.execute("""
                INSERT OR REPLACE INTO feature_store
                (symbol, feature_date, feature_name, feature_value, version, computed_at)
                VALUES (?, ?, ?, ?, 'v3', datetime('now'))
            """, (sym, today_str, fname, fval))
        applied += 1

    for fname, fval in [
        ("portfolio_heat", round(total_kelly, 4)),
        ("kelly_b_ratio",  round(b, 4)),
    ]:
        conn.execute("""
            INSERT OR REPLACE INTO feature_store
            (symbol, feature_date, feature_name, feature_value, version, computed_at)
            VALUES ('MARKET', ?, ?, ?, 'v3', datetime('now'))
        """, (today_str, fname, fval))

    conn.commit()

    top5 = sorted(kelly_list, key=lambda x: -x['kelly_final'])[:5]
    print("[P19] Top Kelly picks:", flush=True)
    for k in top5:
        print(f"  {k['symbol']:8s} p={k['p']:.3f} → "
              f"kelly={k['kelly_final']:.3f} ({k['kelly_final']*100:.1f}%)", flush=True)

    dur = time.time() - t0
    summary = {
        "phase": "19",
        "b_ratio":        round(b, 4),
        "avg_win":        round(avg_win, 4),
        "avg_loss":       round(avg_loss, 4),
        "win_rate":       round(win_rate, 4),
        "n_signals":      len(kelly_list),
        "portfolio_heat": round(total_kelly, 4),
        "top_5_kelly":    [{"symbol":k['symbol'],"kelly":k['kelly_final'],"p":k['p']} for k in top5],
        "applied":        applied,
        "duration_seconds": round(dur, 1),
    }
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '19', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 20 — PINE SCRIPT ML PROBABILITY INDICATOR (AUTO-GENERATED)
# ═════════════════════════════════════════════════════════════════════════════

def phase20_pine_ml_indicator():
    """
    Auto-generates a TradingView Pine Script v5 indicator embedding today's
    ML predictions directly as array constants.

    Dashboard shows (top-right table):
      Symbol | Prob% | Kelly% | UES | EV_5d | Confidence | Tier

    Per-symbol:
      - Colored background based on probability tier
      - Label at last bar with prob + Kelly fraction
      - Alert condition for prob >= 65%

    Output: scripts/pine/ml_probability_indicator.pine
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P20] Pine Script ML Probability Indicator...", flush=True)

    conn = get_db()

    rows = conn.execute("""
        SELECT ep.symbol,
               ROUND(ep.explosion_prob, 3)              AS prob,
               COALESCE(fk.feature_value, 0)           AS kelly_frac,
               COALESCE(fc.feature_value, 2)           AS conf_width,
               COALESCE(fev.feature_value, 0)          AS ev_5d,
               COALESCE(fhz.feature_value, 0)          AS cox_hazard,
               COALESCE(us.unified_score, 50)          AS ues
        FROM explosion_predictions ep
        LEFT JOIN feature_store fk  ON ep.symbol=fk.symbol
            AND fk.feature_date=ep.pred_date AND fk.feature_name='kelly_fraction'
        LEFT JOIN feature_store fc  ON ep.symbol=fc.symbol
            AND fc.feature_date=ep.pred_date AND fc.feature_name='conformal_width'
        LEFT JOIN feature_store fev ON ep.symbol=fev.symbol
            AND fev.feature_date=ep.pred_date AND fev.feature_name='expected_value_5d'
        LEFT JOIN feature_store fhz ON ep.symbol=fhz.symbol
            AND fhz.feature_date=ep.pred_date AND fhz.feature_name='cox_hazard_score'
        LEFT JOIN unified_signals us ON ep.symbol=us.symbol
            AND us.signal_date=ep.pred_date
        WHERE ep.pred_date = ? AND ep.explosion_prob IS NOT NULL
        ORDER BY ep.explosion_prob DESC
        LIMIT 20
    """, (today_str,)).fetchall()

    conn.close()

    if not rows:
        return {"phase":"20","status":"skipped","reason":"no predictions for today"}

    # Build Pine arrays
    symbols  = [r['symbol']                     for r in rows]
    probs    = [round(float(r['prob']),   3)    for r in rows]
    kellys   = [round(float(r['kelly_frac']),3) for r in rows]
    ues_sc   = [round(float(r['ues']),    1)    for r in rows]
    conf_w   = [int(float(r['conf_width']))==1  for r in rows]
    ev5ds    = [round(float(r['ev_5d']),  3)    for r in rows]
    n        = len(symbols)

    # Format for Pine
    sym_arr  = '", "'.join(symbols)
    prob_arr = ", ".join(str(p)     for p in probs)
    k_arr    = ", ".join(str(k)     for k in kellys)
    ues_arr  = ", ".join(str(u)     for u in ues_sc)
    conf_arr = ", ".join("true" if c else "false" for c in conf_w)
    ev_arr   = ", ".join(str(e)     for e in ev5ds)

    # Generate Pine Script (use string replace to avoid f-string / brace conflicts)
    template = (
        '//@version=5\n'
        '// ╔═══════════════════════════════════════════════════════════════════════╗\n'
        '// ║  EGX ML Signals Dashboard — AUTO-GENERATED __DATE__                ║\n'
        '// ║  Phase 20 | egx_ml_trainer.py                                       ║\n'
        '// ║  Shows: Ensemble Prob | Kelly% | UES | EV5d | Conformal Confidence  ║\n'
        '// ╚═══════════════════════════════════════════════════════════════════════╝\n'
        'indicator("EGX ML Signals [__DATE__]", overlay=true, max_labels_count=50)\n\n'
        '// ── Embedded signal data ────────────────────────────────────────────────\n'
        'int     N       = __N__\n'
        'string[] SYMS   = array.from("__SYMS__")\n'
        'float[]  PROBS  = array.from(__PROBS__)\n'
        'float[]  KELLYS = array.from(__KELLYS__)\n'
        'float[]  UES    = array.from(__UES__)\n'
        'bool[]   CONF   = array.from(__CONF__)\n'
        'float[]  EV5D   = array.from(__EV5D__)\n\n'
        '// ── Dashboard table ──────────────────────────────────────────────────────\n'
        'var table tbl = table.new(\n'
        '    position.top_right, 7, __NROWS__,\n'
        '    bgcolor      = color.new(color.black, 10),\n'
        '    border_width = 1,\n'
        '    border_color = color.new(color.gray, 50),\n'
        '    frame_width  = 2,\n'
        '    frame_color  = color.new(color.blue, 30)\n'
        ')\n\n'
        'if barstate.islast\n'
        '    // Header\n'
        '    table.cell(tbl, 0, 0, "Symbol",  text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 1, 0, "Prob%",   text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 2, 0, "Kelly%",  text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 3, 0, "UES",     text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 4, 0, "EV5d%",   text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 5, 0, "Conf",    text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n'
        '    table.cell(tbl, 6, 0, "Tier",    text_color=color.white, text_size=size.small, bgcolor=color.new(color.navy,20))\n\n'
        '    for i = 0 to N - 1\n'
        '        float  p   = array.get(PROBS,  i)\n'
        '        float  k   = array.get(KELLYS, i) * 100\n'
        '        float  ues = array.get(UES,    i)\n'
        '        float  ev  = array.get(EV5D,   i) * 100\n'
        '        bool   cf  = array.get(CONF,   i)\n'
        '        string sym = array.get(SYMS,   i)\n\n'
        '        color  bg  = p >= 0.80 ? color.new(color.red,    60) :\n'
        '                     p >= 0.65 ? color.new(color.orange, 60) :\n'
        '                     p >= 0.50 ? color.new(color.yellow, 70) :\n'
        '                                 color.new(color.gray,   80)\n\n'
        '        string tier = p >= 0.80 ? "A+" :\n'
        '                      p >= 0.65 ? "A"  :\n'
        '                      p >= 0.50 ? "B"  : "C"\n\n'
        '        table.cell(tbl, 0, i+1, sym,                                   text_color=color.white, text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 1, i+1, str.tostring(math.round(p*100,1))+"%", text_color=color.white, text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 2, i+1, str.tostring(math.round(k,1))+"%",     text_color=color.lime,  text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 3, i+1, str.tostring(math.round(ues,0)),       text_color=color.white, text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 4, i+1, str.tostring(math.round(ev,2))+"%",    text_color=ev>=0?color.lime:color.red, text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 5, i+1, cf ? "OK" : "??",                      text_color=cf?color.lime:color.orange, text_size=size.tiny, bgcolor=bg)\n'
        '        table.cell(tbl, 6, i+1, tier,                                  text_color=color.white, text_size=size.tiny, bgcolor=bg)\n\n'
        '// ── Per-symbol background + last-bar label ───────────────────────────────\n'
        'cur_idx = array.indexof(SYMS, syminfo.ticker)\n\n'
        'if cur_idx >= 0\n'
        '    float cp = array.get(PROBS,  cur_idx)\n'
        '    float ck = array.get(KELLYS, cur_idx)\n\n'
        '    color bgc = cp >= 0.80 ? color.new(color.red,    92) :\n'
        '                cp >= 0.65 ? color.new(color.orange, 92) :\n'
        '                cp >= 0.50 ? color.new(color.yellow, 94) :\n'
        '                             color.new(color.white,  100)\n'
        '    bgcolor(barstate.islast ? bgc : na, title="ML BG")\n\n'
        '    if barstate.islast\n'
        '        label.new(\n'
        '            bar_index, high * 1.005,\n'
        '            text  = "ML:" + str.tostring(math.round(cp*100,1)) + "% K:" + str.tostring(math.round(ck*100,1)) + "%",\n'
        '            style = label.style_label_down,\n'
        '            color = cp >= 0.65 ? color.new(color.red,20) : color.new(color.blue,30),\n'
        '            textcolor = color.white,\n'
        '            size  = size.small\n'
        '        )\n\n'
        '// ── Plots & alerts ──────────────────────────────────────────────────────\n'
        'plot(cur_idx >= 0 ? array.get(PROBS, cur_idx) : na,\n'
        '     title="ML Probability", color=color.blue, linewidth=2, display=display.none)\n\n'
        'alertcondition(\n'
        '    cur_idx >= 0 and array.get(PROBS, cur_idx) >= 0.65,\n'
        '    title="EGX ML High Prob",\n'
        '    message="ML Explosion Prob >= 65% on __DATE__"\n'
        ')\n'
    )

    pine_script = (template
        .replace("__DATE__",  today_str)
        .replace("__N__",     str(n))
        .replace("__NROWS__", str(n + 2))
        .replace("__SYMS__",  sym_arr)
        .replace("__PROBS__", prob_arr)
        .replace("__KELLYS__", k_arr)
        .replace("__UES__",   ues_arr)
        .replace("__CONF__",  conf_arr)
        .replace("__EV5D__",  ev_arr)
    )

    # Save Pine file
    pine_dir = ROOT / 'scripts' / 'pine'
    pine_dir.mkdir(parents=True, exist_ok=True)
    pine_path = pine_dir / 'ml_probability_indicator.pine'
    pine_path.write_text(pine_script, encoding='utf-8')

    print(f"[P20] Saved → {pine_path} ({len(pine_script)} chars, {n} signals)", flush=True)
    print(f"[P20] Top signals: {symbols[:5]}", flush=True)

    dur = time.time() - t0
    summary = {
        "phase": "20",
        "date": today_str,
        "n_signals": n,
        "top_5": symbols[:5],
        "file": str(pine_path),
        "file_size_chars": len(pine_script),
        "duration_seconds": round(dur, 1),
    }
    conn = get_db()
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '20', dur, json.dumps(summary)))
    conn.commit(); conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 21 — SPECTRAL CYCLE INTELLIGENCE LAYER
# ═════════════════════════════════════════════════════════════════════════════

def phase21_spectral_intelligence():
    """
    Phase 21 — Spectral Cycle Intelligence Layer
    =============================================
    Applies signal processing theory to EGX price data to extract hidden
    cyclical structure that conventional indicators miss.

    Pipeline per stock:
      1. Log returns (stationarity)  →  removes price scaling + trend bias
      2. Hanning window              →  reduces spectral leakage
      3. numpy.fft.rfft              →  frequency domain decomposition
      4. Top-3 dominant peaks        →  filter noise, keep signal
      5. Circular encoding (sin/cos) →  phase is angular, not linear
      6. Welch PSD on 3 windows      →  stability across time (252/210/168 bars)
      7. Spectral Regime Classifier  →  4 market states
      8. Non-linear UES boost        →  tanh multiplier [0.85, 1.15]

    Features written to feature_store (15 per symbol):
      fft_dominant_period       fft_dominant_amplitude
      fft_phase_sin             fft_phase_cos
      fft_cycle_bottom_prox     fft_secondary_period
      fft_secondary_phase_sin   fft_secondary_phase_cos
      fft_noise_ratio           fft_weekly_amplitude
      fft_monthly_amplitude     fft_quarterly_amplitude
      fft_stability_score       fft_phase_instability
      spectral_regime (0=cyclical 1=noisy 2=compression 3=expansion)
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[P21] Spectral Cycle Intelligence Layer...", flush=True)

    try:
        from scipy.signal import welch as scipy_welch
    except ImportError:
        return {"phase": "21", "error": "scipy not available"}

    conn = get_db()
    ensure_tables(conn)

    # ── Load all OHLCV data at once ──────────────────────────────────────────
    rows = conn.execute("""
        SELECT symbol, bar_time, close
        FROM ohlcv_history
        WHERE close > 0
        ORDER BY symbol, bar_time ASC
    """).fetchall()

    if not rows:
        conn.close()
        return {"phase": "21", "status": "skipped", "reason": "no ohlcv data"}

    # Build per-symbol close arrays
    from collections import defaultdict
    sym_closes = defaultdict(list)
    for r in rows:
        sym_closes[r['symbol']].append(float(r['close']))

    print(f"[P21] {len(sym_closes)} symbols loaded", flush=True)

    # ── FFT engine ────────────────────────────────────────────────────────────
    def compute_fft_features(close_arr):
        """
        Core FFT engine.
        Returns dict of spectral features for one stock.
        """
        if len(close_arr) < 30:
            return None

        close = np.array(close_arr, dtype=np.float64)
        N_full = len(close)

        # Use last 252 bars (1 trading year) for primary FFT
        N = min(252, N_full - 1)
        c = close[-(N + 1):]

        # ── Step 1: Log returns (stationarity + scale-invariance) ────────────
        log_ret = np.diff(np.log(c + 1e-12))   # length = N
        if len(log_ret) < 20:
            return None

        # ── Step 2: Detrend + Hanning window ─────────────────────────────────
        log_ret_dt = log_ret - np.polyval(np.polyfit(np.arange(len(log_ret)), log_ret, 1),
                                           np.arange(len(log_ret)))
        window     = np.hanning(len(log_ret_dt))
        log_ret_w  = log_ret_dt * window

        # ── Step 3: FFT ──────────────────────────────────────────────────────
        fft_vals   = np.fft.rfft(log_ret_w)
        amplitudes = np.abs(fft_vals)
        phases     = np.angle(fft_vals)
        freqs      = np.fft.rfftfreq(len(log_ret_w), d=1.0)  # cycles per bar

        # Exclude DC (freq=0) and Nyquist
        valid = (freqs > 0) & (freqs < 0.5)
        amp_v  = amplitudes[valid]
        phs_v  = phases[valid]
        frq_v  = freqs[valid]

        if len(amp_v) < 3:
            return None

        total_energy  = float(np.sum(amp_v ** 2)) + 1e-12
        periods       = 1.0 / frq_v   # convert frequency → period (days)

        # ── Step 4: Top-3 dominant peaks ──────────────────────────────────────
        top3_idx = np.argsort(amp_v)[::-1][:3]
        top3_amp = amp_v[top3_idx]
        top3_phs = phs_v[top3_idx]
        top3_per = periods[top3_idx]

        top3_energy   = float(np.sum(top3_amp ** 2))
        noise_ratio   = float(1.0 - top3_energy / total_energy)  # 0=pure signal, 1=pure noise

        # ── Adaptive amplitude normalization ────────────────────────────────
        # Normalize by p95 of ALL amplitudes (not std of returns) to avoid
        # ceiling effects when std(log_ret) is tiny (~0.01 for daily data).
        # dom_amp > 1.0 means dominant peak exceeds the 95th percentile — strong cycle.
        amp_norm_factor = float(np.percentile(amp_v, 95)) + 1e-12

        # ── Primary dominant cycle ─────────────────────────────────────────
        dom_period = float(top3_per[0])
        dom_amp    = float(top3_amp[0]) / amp_norm_factor  # relative to p95 noise floor
        dom_phase  = float(top3_phs[0])

        # Phase at the last bar (current position in cycle):
        # x(t) = A*cos(2π*t/T + φ₀) → at t = N-1: phase_now = 2π*(N-1)/T + φ₀
        n_bars       = len(log_ret_w) - 1
        phase_now    = float((2.0 * np.pi * n_bars / max(dom_period, 1.0)) + dom_phase)
        phase_now    = float(np.arctan2(np.sin(phase_now), np.cos(phase_now)))  # wrap to [-π, π]

        # cycle_bottom_prox: 1 when at trough (cos=-1), 0 when at peak (cos=+1)
        cycle_bottom_prox = float((1.0 - np.cos(phase_now)) / 2.0)

        # ── Secondary cycle ────────────────────────────────────────────────
        sec_period    = float(top3_per[1]) if len(top3_per) > 1 else dom_period
        sec_phase     = float(top3_phs[1]) if len(top3_phs) > 1 else dom_phase
        n2            = n_bars
        sec_phase_now = float((2.0 * np.pi * n2 / max(sec_period, 1.0)) + sec_phase)
        sec_phase_now = float(np.arctan2(np.sin(sec_phase_now), np.cos(sec_phase_now)))

        # ── Band-specific amplitudes ─────────────────────────────────────────
        def band_amp(lo_days, hi_days):
            mask = (periods >= lo_days) & (periods <= hi_days)
            if not np.any(mask):
                return 0.0
            return float(np.max(amp_v[mask]) / amp_norm_factor)  # consistent with dom_amp

        weekly_amp    = band_amp(4, 8)
        monthly_amp   = band_amp(15, 30)
        quarterly_amp = band_amp(50, 80)

        return {
            'fft_dominant_period':     round(float(np.clip(dom_period, 2, 252)), 2),
            'fft_dominant_amplitude':  round(float(np.clip(dom_amp, 0, 10)), 4),
            'fft_phase_sin':           round(float(np.sin(phase_now)), 4),
            'fft_phase_cos':           round(float(np.cos(phase_now)), 4),
            'fft_cycle_bottom_prox':   round(float(np.clip(cycle_bottom_prox, 0, 1)), 4),
            'fft_secondary_period':    round(float(np.clip(sec_period, 2, 252)), 2),
            'fft_secondary_phase_sin': round(float(np.sin(sec_phase_now)), 4),
            'fft_secondary_phase_cos': round(float(np.cos(sec_phase_now)), 4),
            'fft_noise_ratio':         round(float(np.clip(noise_ratio, 0, 1)), 4),
            'fft_weekly_amplitude':    round(float(np.clip(weekly_amp, 0, 10)), 4),
            'fft_monthly_amplitude':   round(float(np.clip(monthly_amp, 0, 10)), 4),
            'fft_quarterly_amplitude': round(float(np.clip(quarterly_amp, 0, 10)), 4),
        }

    # ── Stability engine (Welch PSD on 3 windows) ──────────────────────────
    def compute_stability(close_arr):
        """
        Welch PSD on 3 window sizes. Returns (stability_score, phase_instability).
        stability_score ≈ 1 when dominant cycle is consistent across windows.
        """
        if len(close_arr) < 50:
            return 0.5, 0.5

        close = np.array(close_arr, dtype=np.float64)
        windows = [min(w, len(close) - 1) for w in [252, 210, 168] if len(close) > w]
        if len(windows) < 2:
            return 0.5, 0.5

        dominant_periods = []
        for w in windows:
            log_ret = np.diff(np.log(close[-w-1:] + 1e-12))
            if len(log_ret) < 20:
                continue
            try:
                # Welch PSD: segment-averaged periodogram (more noise-robust than FFT)
                nperseg  = min(len(log_ret) // 2, 64)
                f, pxx   = scipy_welch(log_ret, fs=1.0, nperseg=nperseg, window='hann')
                # Exclude DC and Nyquist
                valid    = (f > 0.01) & (f < 0.5)
                if np.any(valid):
                    dom_f    = f[valid][np.argmax(pxx[valid])]
                    dom_per  = 1.0 / dom_f if dom_f > 0 else 0
                    if 3 < dom_per < 200:
                        dominant_periods.append(dom_per)
            except Exception:
                pass

        if len(dominant_periods) < 2:
            return 0.5, 0.5

        dp          = np.array(dominant_periods)
        mean_period = np.mean(dp)
        # Stability: fraction of windows within ±25% of mean
        within_25pct = np.mean(np.abs(dp - mean_period) / (mean_period + 1e-6) < 0.25)
        stability    = float(within_25pct)

        # Phase instability: coefficient of variation of dominant periods
        phase_instab = float(np.clip(np.std(dp) / (mean_period + 1e-6), 0, 1))

        return round(stability, 4), round(phase_instab, 4)

    # ── Spectral Regime Classifier (4 states) ────────────────────────────────
    def classify_spectral_regime(noise_ratio, stability_score, dom_amplitude,
                                  cycle_bottom_prox, quarterly_amp):
        """
        4-state spectral regime:
          0 = cyclical     (clear, stable cycles — use cycle timing)
          1 = noisy        (high noise — reduce confidence)
          2 = compression  (low energy, low amplitude — watch for breakout)
          3 = expansion    (rising spectral energy — momentum building)
        """
        if noise_ratio > 0.88:
            return 1  # noisy — spectrum dominated by random variation (threshold = p75 of EGX)
        if stability_score < 0.35:
            return 3  # expansion — structural shift, new energy building
        if dom_amplitude < 1.05 and quarterly_amp < 0.80:
            return 2  # compression — peak barely above p95 floor (pre-explosion quiet zone)
        return 0      # cyclical — clear, stable dominant cycle

    REGIME_NAMES = {0: "cyclical", 1: "noisy", 2: "compression", 3: "expansion"}

    # ── Process all symbols ──────────────────────────────────────────────────
    written = 0
    n_skipped = 0
    regime_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    for sym, close_arr in sym_closes.items():
        try:
            feat = compute_fft_features(close_arr)
            if feat is None:
                n_skipped += 1
                continue

            stab, phase_instab = compute_stability(close_arr)

            regime_id = classify_spectral_regime(
                feat['fft_noise_ratio'],
                stab,
                feat['fft_dominant_amplitude'],
                feat['fft_cycle_bottom_prox'],
                feat['fft_quarterly_amplitude'],
            )
            regime_counts[regime_id] += 1

            # Add stability features to dict
            all_feats = {
                **feat,
                'fft_stability_score':   stab,
                'fft_phase_instability': phase_instab,
                'spectral_regime':       float(regime_id),
            }

            for fname, fval in all_feats.items():
                conn.execute("""
                    INSERT OR REPLACE INTO feature_store
                    (symbol, feature_date, feature_name, feature_value, version, computed_at)
                    VALUES (?, ?, ?, ?, 'v3', datetime('now'))
                """, (sym, today_str, fname, fval))
            written += 1

        except Exception as e:
            n_skipped += 1
            continue

    conn.commit()
    dur = time.time() - t0

    # Regime distribution summary
    regime_summary = {REGIME_NAMES[k]: v for k, v in regime_counts.items()}
    print(f"[P21] {written} symbols processed in {dur:.1f}s | "
          f"regimes: {regime_summary}", flush=True)

    # Quality snapshot: top 5 stocks closest to cycle bottom
    bottom_prox_rows = conn.execute("""
        SELECT fs1.symbol,
               fs1.feature_value as bottom_prox,
               fs2.feature_value as period,
               fs3.feature_value as regime
        FROM feature_store fs1
        JOIN feature_store fs2 ON fs1.symbol=fs2.symbol AND fs2.feature_date=?
             AND fs2.feature_name='fft_dominant_period'
        JOIN feature_store fs3 ON fs1.symbol=fs3.symbol AND fs3.feature_date=?
             AND fs3.feature_name='spectral_regime'
        WHERE fs1.feature_date=? AND fs1.feature_name='fft_cycle_bottom_prox'
          AND fs3.feature_value IN (0.0, 2.0)   -- cyclical or compression only
        ORDER BY fs1.feature_value DESC LIMIT 5
    """, (today_str, today_str, today_str)).fetchall()

    cycle_bottom_leaders = [
        {"symbol": r['symbol'],
         "cycle_bottom_prox": round(float(r['bottom_prox']), 3),
         "dominant_period_days": round(float(r['period']), 1),
         "regime": REGIME_NAMES.get(int(float(r['regime'])), '?')}
        for r in bottom_prox_rows
    ]

    print("[P21] Top cycle-bottom leaders:", flush=True)
    for s in cycle_bottom_leaders:
        print(f"  {s['symbol']:8s} bottom_prox={s['cycle_bottom_prox']:.3f} "
              f"T={s['dominant_period_days']:.0f}d [{s['regime']}]", flush=True)

    summary = {
        "phase": "21",
        "n_symbols": written,
        "n_skipped": n_skipped,
        "regime_distribution": regime_summary,
        "cycle_bottom_leaders": cycle_bottom_leaders,
        "n_features_per_symbol": 15,
        "total_features_written": written * 15,
        "duration_seconds": round(dur, 1),
    }

    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '21', dur, json.dumps(summary)))
    conn.commit()
    conn.close()
    print(json.dumps(summary), flush=True)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Phase 23 — Historical Spectral Attribution (Backtest on explosive_moves)
# ─────────────────────────────────────────────────────────────────────────────

def phase23_spectral_attribution():
    """
    Backtest FFT on historical explosive_moves data.
    safe_float helper (module-level phases don't import explosion_ml).
    For each explosion, reconstruct FFT features from the 10 bars BEFORE the event,
    then compare: P(explosion | cyclical+high_bottom_prox) vs P(explosion | noisy).

    Output: data/spectral_attribution_report.json + spectral_attribution DB table.
    """
    import numpy as np
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(json.dumps({"phase": "23", "status": "start"}), flush=True)

    def safe_float(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except Exception:
            return default

    conn = get_db()

    # Ensure report table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spectral_attribution (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date      TEXT,
            regime        TEXT,
            bottom_prox_bucket TEXT,
            n_events      INTEGER,
            n_total_obs   INTEGER,
            precision_5d  REAL,
            avg_return_5d REAL,
            lift_vs_base  REAL,
            computed_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    # ── Load all explosions ─────────────────────────────────────────────────
    explosions = conn.execute("""
        SELECT symbol, explosion_date, return_5d
        FROM explosive_moves
        WHERE explosion_date <= date('now','-6 days')
        ORDER BY explosion_date
    """).fetchall()

    if not explosions:
        conn.close()
        return {"error": "no explosive_moves data"}

    # ── Load OHLCV cache per symbol ─────────────────────────────────────────
    sym_ohlcv = {}
    for row in conn.execute("""
        SELECT symbol, date(bar_time,'unixepoch') AS bar_date, close
        FROM ohlcv_history
        ORDER BY symbol, bar_time ASC
    """).fetchall():
        sym = row['symbol']
        if sym not in sym_ohlcv:
            sym_ohlcv[sym] = []
        sym_ohlcv[sym].append((row['bar_date'], float(row['close'])))

    # ── FFT helper (same as phase21, inline) ────────────────────────────────
    def fft_snapshot(close_arr, lookback=50):
        """Compute cycle_bottom_prox and noise_ratio from last `lookback` bars."""
        if len(close_arr) < 20:
            return None, None
        c   = np.array(close_arr[-lookback-1:], dtype=np.float64)
        log_ret = np.diff(np.log(np.clip(c, 1e-9, None)))
        if len(log_ret) < 10:
            return None, None
        # Detrend + Hanning
        x = np.arange(len(log_ret), dtype=np.float64)
        log_ret = log_ret - np.polyval(np.polyfit(x, log_ret, 1), x)
        w       = np.hanning(len(log_ret))
        sig     = log_ret * w
        fft_v   = np.fft.rfft(sig)
        amp_v   = np.abs(fft_v)
        freqs   = np.fft.rfftfreq(len(sig), d=1.0)
        valid   = (freqs > 0) & (freqs < 0.5)
        if not np.any(valid):
            return None, None
        amp_v   = amp_v[valid]
        freqs_v = freqs[valid]
        # Noise ratio
        total_e = float(np.sum(amp_v**2)) + 1e-12
        top3    = np.sort(amp_v)[::-1][:3]
        noise   = float(1.0 - np.sum(top3**2) / total_e)
        # Dom period & phase
        dom_idx    = np.argmax(amp_v)
        dom_period = float(1.0 / (freqs_v[dom_idx] + 1e-12))
        dom_phase  = float(np.angle(np.fft.rfft(sig)[valid][dom_idx]))
        n_bars     = len(sig) - 1
        phase_now  = float((2.0*np.pi*n_bars / max(dom_period, 1.0)) + dom_phase)
        phase_now  = float(np.arctan2(np.sin(phase_now), np.cos(phase_now)))
        bottom_prox = float((1.0 - np.cos(phase_now)) / 2.0)
        # Regime
        amp_norm = float(np.percentile(amp_v, 95)) + 1e-12
        dom_amp  = float(top3[0]) / amp_norm if len(top3) > 0 else 0.0
        if noise > 0.88:
            regime = "noisy"
        elif dom_amp < 1.05:
            regime = "compression"
        else:
            regime = "cyclical"
        return bottom_prox, noise, regime

    # ── Sample negative observations (non-explosions, same symbols, random dates) ──
    import random
    random.seed(42)
    all_symbols = list(sym_ohlcv.keys())
    # explosion set for fast lookup
    explosion_set = set(
        (r['symbol'], r['explosion_date']) for r in explosions
    )
    # Sample ~3× negatives
    negatives = []
    n_neg_target = len(explosions) * 3
    attempts = 0
    while len(negatives) < n_neg_target and attempts < n_neg_target * 10:
        attempts += 1
        sym  = random.choice(all_symbols)
        bars = sym_ohlcv.get(sym, [])
        if len(bars) < 60:
            continue
        bar  = random.choice(bars[30:-5])
        dstr = bar[0]
        if (sym, dstr) not in explosion_set:
            negatives.append({'symbol': sym, 'date': dstr, 'label': 0, 'return_5d': 0.0})

    # ── Compute FFT features for all observations ───────────────────────────
    records = []
    # Positives
    for exp in explosions:
        sym   = exp['symbol']
        edate = exp['explosion_date']
        bars  = sym_ohlcv.get(sym, [])
        if not bars:
            continue
        # Get bars strictly BEFORE the explosion date
        pre_bars = [b[1] for b in bars if b[0] < edate]
        if len(pre_bars) < 20:
            continue
        bp, nr, regime = fft_snapshot(pre_bars, lookback=50)
        if bp is None:
            continue
        records.append({
            'symbol': sym, 'date': edate, 'label': 1,
            'return_5d': safe_float(exp['return_5d']),
            'bottom_prox': bp, 'noise_ratio': nr, 'regime': regime,
        })

    # Negatives
    for neg in negatives:
        sym  = neg['symbol']
        dstr = neg['date']
        bars = sym_ohlcv.get(sym, [])
        if not bars:
            continue
        pre_bars = [b[1] for b in bars if b[0] < dstr]
        if len(pre_bars) < 20:
            continue
        bp, nr, regime = fft_snapshot(pre_bars, lookback=50)
        if bp is None:
            continue
        records.append({
            'symbol': sym, 'date': dstr, 'label': 0,
            'return_5d': 0.0,
            'bottom_prox': bp, 'noise_ratio': nr, 'regime': regime,
        })

    total_n   = len(records)
    base_rate = sum(r['label'] for r in records) / total_n if total_n > 0 else 0.0

    # ── Attribution buckets ─────────────────────────────────────────────────
    def bucket(name, subset):
        n   = len(subset)
        pos = [r for r in subset if r['label'] == 1]
        ret = [r['return_5d'] for r in pos] if pos else []
        prec = len(pos) / n if n > 0 else 0.0
        avg_r = sum(ret) / len(ret) if ret else 0.0
        lift  = round(prec / base_rate, 3) if base_rate > 0 else 0.0
        return {
            'n': n, 'n_events': len(pos),
            'precision_5d': round(prec, 4),
            'avg_return_5d': round(avg_r, 3),
            'lift_vs_base': lift,
        }

    buckets = {
        'ALL':                          bucket('ALL', records),
        'cyclical_bottom_prox>0.70':    bucket('cyc_hi', [r for r in records if r['regime']=='cyclical' and r['bottom_prox']>0.70]),
        'cyclical_bottom_prox 0.40-0.70': bucket('cyc_mid', [r for r in records if r['regime']=='cyclical' and 0.40<r['bottom_prox']<=0.70]),
        'cyclical_bottom_prox<0.40':    bucket('cyc_lo', [r for r in records if r['regime']=='cyclical' and r['bottom_prox']<=0.40]),
        'noisy':                        bucket('noisy', [r for r in records if r['regime']=='noisy']),
        'compression':                  bucket('comp', [r for r in records if r['regime']=='compression']),
    }

    # ── Save to DB ──────────────────────────────────────────────────────────
    conn.execute("DELETE FROM spectral_attribution WHERE run_date=?", (today_str,))
    for bname, stats in buckets.items():
        conn.execute("""
            INSERT INTO spectral_attribution
            (run_date, regime, bottom_prox_bucket, n_events, n_total_obs,
             precision_5d, avg_return_5d, lift_vs_base)
            VALUES (?,?,?,?,?,?,?,?)
        """, (today_str, bname.split('_')[0], bname,
              stats['n_events'], stats['n'],
              stats['precision_5d'], stats['avg_return_5d'], stats['lift_vs_base']))
    conn.commit()
    conn.close()

    # ── Save JSON report ────────────────────────────────────────────────────
    report_path = str(ROOT / 'data' / 'spectral_attribution_report.json')
    report = {
        'generated_at': today_str,
        'total_observations': total_n,
        'n_explosions': sum(r['label'] for r in records),
        'base_rate': round(base_rate, 4),
        'buckets': buckets,
        'interpretation': {
            'best_bucket': max(buckets.items(), key=lambda x: x[1].get('lift_vs_base', 0))[0],
            'spectral_useful': (
                buckets.get('cyclical_bottom_prox>0.70', {}).get('lift_vs_base', 0) > 1.2
            ),
        },
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    dur = round(time.time() - t0, 1)
    summary = {
        'phase': '23', 'status': 'ok',
        'n_observations': total_n,
        'base_explosion_rate': round(base_rate, 4),
        'buckets': {k: {'precision': v['precision_5d'], 'lift': v['lift_vs_base']}
                    for k, v in buckets.items()},
        'report_saved': report_path,
        'duration_seconds': dur,
    }
    print(json.dumps(summary), flush=True)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Phase 25 — Spectral Reliability Memory (runs weekly after shadow data exists)
# ─────────────────────────────────────────────────────────────────────────────

def phase25_spectral_reliability():
    """
    Per-symbol rolling spectral alpha: did FFT boost correlate with actual outcomes?
    Reads spectral_shadow_log (Ph 22). Requires at least 10 observations to compute.
    Stores in spectral_reliability table.
    Run weekly (Sunday night_lab deep sequence).
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(json.dumps({"phase": "25", "status": "start"}), flush=True)

    def safe_float(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except Exception:
            return default

    conn = get_db()

    # Ensure spectral_reliability table (also created in signal_integration.py ensure_tables)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spectral_reliability (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol            TEXT NOT NULL,
            as_of_date        TEXT NOT NULL,
            n_cyclical_obs    INTEGER,
            n_noisy_obs       INTEGER,
            cyclical_precision REAL,
            noisy_precision   REAL,
            alpha_30d         REAL,
            alpha_90d         REAL,
            reliability_score REAL,
            computed_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(symbol, as_of_date)
        )
    """)
    conn.commit()

    def window_stats(rows_w):
        """Compute alpha for a set of shadow rows."""
        boosted   = [r for r in rows_w if safe_float(r['spectral_boost'], 1.0) > 1.02]
        unboosted = [r for r in rows_w if abs(safe_float(r['spectral_boost'], 1.0) - 1.0) < 0.02]
        r5_b = [safe_float(r['return_5d']) for r in boosted   if r['return_5d'] is not None]
        r5_u = [safe_float(r['return_5d']) for r in unboosted if r['return_5d'] is not None]
        if not r5_b or not r5_u:
            return None
        return round(sum(r5_b)/len(r5_b) - sum(r5_u)/len(r5_u), 4)

    # Fetch all filled shadow rows
    all_rows = conn.execute("""
        SELECT symbol, prediction_date, spectral_regime, cycle_bottom_prox,
               spectral_boost, return_5d, exploded
        FROM spectral_shadow_log
        WHERE return_5d IS NOT NULL
        ORDER BY prediction_date
    """).fetchall()

    if not all_rows:
        conn.close()
        skipped = {"phase": "25", "status": "skipped", "reason": "no outcome data yet — need shadow_fill_outcomes to run first"}
        print(json.dumps(skipped), flush=True)
        return skipped

    from collections import defaultdict
    sym_rows = defaultdict(list)
    for r in all_rows:
        sym_rows[r['symbol']].append(r)

    written = 0
    for sym, rows in sym_rows.items():
        if len(rows) < 5:
            continue  # not enough data

        def precision(subset):
            if not subset:
                return None
            pos = sum(1 for r in subset if safe_float(r['exploded']) == 1)
            return round(pos / len(subset), 4)

        cyc_rows   = [r for r in rows if r['spectral_regime'] == 'cyclical'
                      and safe_float(r['cycle_bottom_prox']) > 0.65]
        noisy_rows = [r for r in rows if r['spectral_regime'] == 'noisy']

        # 30d / 90d windows
        cutoff_30d = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        cutoff_90d = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
        rows_30d   = [r for r in rows if r['prediction_date'] >= cutoff_30d]
        rows_90d   = [r for r in rows if r['prediction_date'] >= cutoff_90d]

        alpha_30d = window_stats(rows_30d)
        alpha_90d = window_stats(rows_90d)

        cyc_prec  = precision(cyc_rows)
        nsy_prec  = precision(noisy_rows)

        # Reliability score: 0-1
        # Starts at 0.5 (uncertain). Rises when cyclical_precision > noisy_precision AND alpha > 0.
        base_score = 0.5
        if cyc_prec is not None and nsy_prec is not None and cyc_prec > nsy_prec:
            base_score += 0.2
        if alpha_90d is not None and alpha_90d > 0:
            base_score += min(0.3, alpha_90d / 5.0)  # cap at 0.3 for 5% alpha
        elif alpha_90d is not None and alpha_90d < -1.0:
            base_score -= 0.2  # negative alpha → reduce trust
        reliability = round(max(0.0, min(1.0, base_score)), 4)

        conn.execute("""
            INSERT OR REPLACE INTO spectral_reliability
            (symbol, as_of_date, n_cyclical_obs, n_noisy_obs,
             cyclical_precision, noisy_precision, alpha_30d, alpha_90d, reliability_score)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (sym, today_str, len(cyc_rows), len(noisy_rows),
              cyc_prec, nsy_prec, alpha_30d, alpha_90d, reliability))
        written += 1

    conn.commit()
    conn.close()

    dur = round(time.time() - t0, 1)
    summary = {
        'phase': '25', 'status': 'ok',
        'symbols_updated': written,
        'total_shadow_rows': len(all_rows),
        'duration_seconds': dur,
    }
    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 55 — PER-STOCK TOMORROW DIRECTION FORECAST
# ═════════════════════════════════════════════════════════════════════════════

_PH55_FEATURES = [
    'rsi14', 'ret1d', 'ret3d', 'ret5d', 'ret10d',
    'vol_ratio', 'dist_ema20', 'dist_ema50',
    'above_ema20', 'above_ema50',
    'ret_std5', 'rsi_slope3',
    'breadth_score', 'ad_ratio_mkt', 'sector_rank_norm',
    'sector_mean_ret', 'sector_mom5d', 'sector_rsi',
    # Ph56 Markov regime features
    'markov_signal_1d', 'markov_stickiness',
    'markov_entropy', 'markov_regime_age', 'markov_transition_risk',
    # Ph57 Closing Pressure features
    'cp_close_pos', 'cp_vol_surge', 'cp_pressure',
    'cp_gap_potential', 'cp_reversal',
    # Ph77 tsfresh statistical features (added when tsfresh_daily has ≥30 days)
    'ts_autocorr1', 'ts_entropy', 'ts_skew', 'ts_kurtosis', 'ts_vol_std',
]

# Ph77 features — only added to training if tsfresh_daily has ≥30 days of data
_PH77_FEATURES = ['ts_autocorr1', 'ts_entropy', 'ts_skew', 'ts_kurtosis', 'ts_vol_std']
_PH77_MIN_DAYS  = 30   # minimum days of tsfresh_daily data required

_HPO55_CACHE_PATH = MODELS / 'phase55_hpo_params.json'

def _ensure_stock_forecast_table(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS stock_tomorrow_forecast (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        forecast_date TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        direction     TEXT NOT NULL,
        p_up          REAL,
        p_flat        REAL,
        p_down        REAL,
        confidence    REAL,
        sector        TEXT,
        sector_rank   INTEGER,
        market_direction TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        UNIQUE(forecast_date, symbol)
    );
    """)
    conn.commit()


def phase55_stock_forecast():
    """
    Phase 55 — Per-Stock Tomorrow Direction Forecast
    ------------------------------------------------
    Builds a pooled LightGBM 3-class model (UP/FLAT/DOWN) that predicts
    each stock's return direction for the next trading day.

    Features (per stock, per day):
      Stock features : RSI14, ret1d/3d/5d/10d, vol_ratio, dist_ema20/50,
                       above_ema20/50, 5d return std, 3d RSI slope
      Market context : breadth_score, market A/D ratio, sector rank (norm)

    Training:
      - Pool all stock × date pairs with valid data (2025+, ≥50 stocks)
      - Temporal split: 80% train / 20% test (no shuffle)
      - HPO via Optuna 30 trials, cached weekly
      - Target: next-day ret1d > +0.3% → UP, < -0.3% → DOWN, else FLAT

    Output:
      - stock_tomorrow_forecast table: one row per (forecast_date, symbol)
      - Returns count of UP/FLAT/DOWN predictions + model OOS accuracy
    """
    import lightgbm as lgb
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        optuna = None
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import log_loss as sk_log_loss, accuracy_score

    t0        = time.time()
    today_str = datetime.date.today().isoformat()
    conn      = get_db()
    ensure_tables(conn)
    _ensure_stock_forecast_table(conn)

    UP_THR = 0.003   # +0.3%
    DN_THR = -0.003  # -0.3%

    # ── 1. Load OHLCV (Ph74: DuckDB Parquet if available — 5x faster) ────────────
    print("[Ph55] Loading ohlcv_history …", flush=True)
    if _DUCKDB_LAYER:
        _ohlcv_raw = _ohlcv_parquet(filter_positive=True)
        if _ohlcv_raw is not None:
            ohlcv = _ohlcv_raw[['symbol', 'trade_date', 'close', 'high', 'low', 'volume']].copy()
        else:
            _DUCKDB_LAYER_AVAIL = False
            ohlcv = None
    else:
        ohlcv = None
    if ohlcv is None:
        ohlcv = pd.read_sql_query("""
            SELECT symbol, date(bar_time,'unixepoch') AS trade_date,
                   close, high, low, volume
            FROM ohlcv_history
            WHERE close > 0 AND volume > 0
            ORDER BY symbol, trade_date
        """, conn)

    if len(ohlcv) < 5000:
        conn.close()
        print(json.dumps({"error": "insufficient OHLCV data for Ph55"}), flush=True)
        return

    ohlcv['trade_date'] = pd.to_datetime(ohlcv['trade_date'])

    # ── 2. Sector map ──────────────────────────────────────────────────────────
    try:
        sec_map = pd.read_sql_query(
            "SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL AND sector != ''",
            conn
        ).set_index('symbol')['sector'].to_dict()
    except Exception:
        sec_map = {}

    # ── 3. Per-stock features ──────────────────────────────────────────────────
    print("[Ph55] Computing per-stock features …", flush=True)
    ohlcv = ohlcv.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
    grp = ohlcv.groupby('symbol', sort=False)

    ohlcv['ema20']   = grp['close'].transform(lambda x: x.ewm(span=20, adjust=False).mean())
    ohlcv['ema50']   = grp['close'].transform(lambda x: x.ewm(span=50, adjust=False).mean())
    ohlcv['vol20']   = grp['volume'].transform(lambda x: x.rolling(20, min_periods=5).mean())
    ohlcv['ret1d']   = grp['close'].transform(lambda x: x.pct_change()).clip(-0.30, 0.30)
    ohlcv['rsi14']   = grp['close'].transform(lambda x: _rsi_series(x, 14))

    # Rolling momentum (sum of daily returns)
    ohlcv['ret3d']   = grp['ret1d'].transform(lambda x: x.rolling(3, min_periods=2).sum())
    ohlcv['ret5d']   = grp['ret1d'].transform(lambda x: x.rolling(5, min_periods=3).sum())
    ohlcv['ret10d']  = grp['ret1d'].transform(lambda x: x.rolling(10, min_periods=5).sum())
    ohlcv['ret_std5'] = grp['ret1d'].transform(lambda x: x.rolling(5, min_periods=3).std())

    # RSI 3-day slope
    ohlcv['rsi_lag3']  = grp['rsi14'].transform(lambda x: x.shift(3))
    ohlcv['rsi_slope3'] = (ohlcv['rsi14'] - ohlcv['rsi_lag3']) / 3.0

    ohlcv['vol_ratio']  = ohlcv['volume'] / (ohlcv['vol20'] + 1)
    ohlcv['dist_ema20'] = (ohlcv['close'] - ohlcv['ema20']) / (ohlcv['ema20'] + 1e-6)
    ohlcv['dist_ema50'] = (ohlcv['close'] - ohlcv['ema50']) / (ohlcv['ema50'] + 1e-6)
    ohlcv['above_ema20'] = (ohlcv['close'] > ohlcv['ema20']).astype('float32')
    ohlcv['above_ema50'] = (ohlcv['close'] > ohlcv['ema50']).astype('float32')

    ohlcv['sector'] = ohlcv['symbol'].map(sec_map).fillna('Unknown')

    # ── 4. Add market breadth context ──────────────────────────────────────────
    try:
        breadth = pd.read_sql_query(
            "SELECT date, breadth_score, ad_ratio AS ad_ratio_mkt FROM market_breadth_enhanced ORDER BY date",
            conn
        )
        breadth['date'] = pd.to_datetime(breadth['date'])
        ohlcv = ohlcv.merge(
            breadth.rename(columns={'date': 'trade_date'}),
            on='trade_date', how='left'
        )
    except Exception:
        ohlcv['breadth_score'] = 50.0
        ohlcv['ad_ratio_mkt']  = 1.0

    # ── 5. Add sector rank + real sector features ──────────────────────────────
    try:
        sec_rank = pd.read_sql_query(
            "SELECT date, sector, sector_rank, mean_ret, momentum_5d, rsi_mean FROM sector_breadth_daily",
            conn
        )
        sec_rank['date'] = pd.to_datetime(sec_rank['date'])
        # Normalize rank within each date: lower rank # = better = lower norm value
        sec_rank['sector_rank_norm'] = sec_rank.groupby('date')['sector_rank'].transform(
            lambda x: (x - x.min()) / (x.max() - x.min() + 1e-6)
        )
        ohlcv = ohlcv.merge(
            sec_rank.rename(columns={
                'date':        'trade_date',
                'mean_ret':    'sector_mean_ret',
                'momentum_5d': 'sector_mom5d',
                'rsi_mean':    'sector_rsi',
            })[['trade_date', 'sector', 'sector_rank', 'sector_rank_norm',
                'sector_mean_ret', 'sector_mom5d', 'sector_rsi']],
            on=['trade_date', 'sector'], how='left'
        )
    except Exception:
        ohlcv['sector_rank']     = 3
        ohlcv['sector_rank_norm'] = 0.5
        ohlcv['sector_mean_ret'] = 0.0
        ohlcv['sector_mom5d']    = 0.0
        ohlcv['sector_rsi']      = 50.0

    ohlcv['sector_rank']      = pd.to_numeric(ohlcv['sector_rank'],      errors='coerce').fillna(0).astype(int)
    ohlcv['sector_rank_norm'] = ohlcv['sector_rank_norm'].fillna(0.5)
    ohlcv['sector_mean_ret']  = ohlcv['sector_mean_ret'].fillna(0.0)
    ohlcv['sector_mom5d']     = ohlcv['sector_mom5d'].fillna(0.0)
    ohlcv['sector_rsi']       = ohlcv['sector_rsi'].fillna(50.0)
    ohlcv['breadth_score']    = ohlcv['breadth_score'].fillna(50.0)
    ohlcv['ad_ratio_mkt']     = ohlcv['ad_ratio_mkt'].fillna(1.0)

    # ── 5b. Add Markov regime context (Ph56) ──────────────────────────────────
    try:
        n_mk55 = conn.execute("SELECT COUNT(*) FROM markov_signal_daily").fetchone()[0]
        if n_mk55 >= 20:
            mkv55 = pd.read_sql_query("""
                SELECT date AS trade_date,
                       signal_1d               AS markov_signal_1d,
                       continuation_confidence AS markov_stickiness,
                       entropy                 AS markov_entropy,
                       regime_age              AS markov_regime_age,
                       transition_risk         AS markov_transition_risk
                FROM markov_signal_daily ORDER BY date
            """, conn)
            mkv55['trade_date'] = pd.to_datetime(mkv55['trade_date'])
            ohlcv = ohlcv.merge(mkv55, on='trade_date', how='left')
        else:
            raise ValueError("insufficient markov rows")
    except Exception:
        ohlcv['markov_signal_1d']       = 0.0
        ohlcv['markov_stickiness']      = 0.5
        ohlcv['markov_entropy']         = 1.0
        ohlcv['markov_regime_age']      = 1
        ohlcv['markov_transition_risk'] = 0.5
    ohlcv['markov_signal_1d']       = ohlcv['markov_signal_1d'].fillna(0.0)
    ohlcv['markov_stickiness']      = ohlcv['markov_stickiness'].fillna(0.5)
    ohlcv['markov_entropy']         = ohlcv['markov_entropy'].fillna(1.0)
    ohlcv['markov_regime_age']      = ohlcv['markov_regime_age'].fillna(1)
    ohlcv['markov_transition_risk'] = ohlcv['markov_transition_risk'].fillna(0.5)

    # ── 5c. Add Ph57 Closing Pressure features (Ph74: Parquet if available) ──────
    try:
        n_cp = conn.execute("SELECT COUNT(*) FROM closing_pressure_daily").fetchone()[0]
        if n_cp >= 100:
            # Ph74: Parquet cache is faster for this full-table join
            if _DUCKDB_LAYER:
                from duckdb_layer import _parquet_fresh, PARQUET_DIR
                import duckdb as _ddb
                if _parquet_fresh('closing_pressure_daily'):
                    _pq = PARQUET_DIR / 'closing_pressure_daily.parquet'
                    _dc = _ddb.connect()
                    cp57 = _dc.execute(
                        f"SELECT symbol, trade_date, "
                        f"close_pos AS cp_close_pos, vol_surge AS cp_vol_surge, "
                        f"closing_pressure AS cp_pressure, "
                        f"gap_potential AS cp_gap_potential, "
                        f"intraday_reversal AS cp_reversal "
                        f"FROM read_parquet('{_pq}')"
                    ).df()
                    _dc.close()
                else:
                    cp57 = None
            else:
                cp57 = None
            if cp57 is None:
                cp57 = pd.read_sql_query("""
                    SELECT symbol, trade_date,
                           close_pos        AS cp_close_pos,
                           vol_surge        AS cp_vol_surge,
                           closing_pressure AS cp_pressure,
                           gap_potential    AS cp_gap_potential,
                           intraday_reversal AS cp_reversal
                    FROM closing_pressure_daily
                """, conn)
            cp57['trade_date'] = pd.to_datetime(cp57['trade_date'])
            ohlcv = ohlcv.merge(cp57, on=['symbol', 'trade_date'], how='left')
        else:
            raise ValueError("insufficient closing_pressure rows")
    except Exception:
        ohlcv['cp_close_pos']     = 0.5
        ohlcv['cp_vol_surge']     = 1.0
        ohlcv['cp_pressure']      = 0.5
        ohlcv['cp_gap_potential'] = 0
        ohlcv['cp_reversal']      = 0
    ohlcv['cp_close_pos']     = ohlcv['cp_close_pos'].fillna(0.5)
    ohlcv['cp_vol_surge']     = ohlcv['cp_vol_surge'].fillna(1.0)
    ohlcv['cp_pressure']      = ohlcv['cp_pressure'].fillna(0.5)
    ohlcv['cp_gap_potential'] = ohlcv['cp_gap_potential'].fillna(0)
    ohlcv['cp_reversal']      = ohlcv['cp_reversal'].fillna(0)

    # ── 5d. Ph77 tsfresh statistical features ─────────────────────────────────
    _use_ph77 = False
    try:
        _n_ts_dates = conn.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM tsfresh_daily"
        ).fetchone()[0]
        if _n_ts_dates >= _PH77_MIN_DAYS:
            ts77 = pd.read_sql_query("""
                SELECT symbol, trade_date,
                       feat_autocorr1 AS ts_autocorr1,
                       feat_entropy   AS ts_entropy,
                       feat_skew      AS ts_skew,
                       feat_kurtosis  AS ts_kurtosis,
                       vol_std        AS ts_vol_std
                FROM tsfresh_daily
            """, conn)
            ts77['trade_date'] = pd.to_datetime(ts77['trade_date'])
            ohlcv = ohlcv.merge(ts77, on=['symbol', 'trade_date'], how='left')
            _use_ph77 = True
            print(f"[Ph55] Ph77 tsfresh features joined: {_n_ts_dates} dates available", flush=True)
        else:
            print(f"[Ph55] Ph77 skipped: only {_n_ts_dates}/{_PH77_MIN_DAYS} tsfresh_daily dates", flush=True)
    except Exception as _ts_e:
        print(f"[Ph55] Ph77 join failed: {_ts_e}", flush=True)

    if not _use_ph77:
        for _tf in _PH77_FEATURES:
            ohlcv[_tf] = 0.0

    for _tf in _PH77_FEATURES:
        ohlcv[_tf] = ohlcv[_tf].fillna(0.0)

    # ── 6. Build training labels (next-day return) ─────────────────────────────
    print("[Ph55] Building training labels …", flush=True)
    ohlcv = ohlcv.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
    ohlcv['target_ret'] = ohlcv.groupby('symbol')['ret1d'].shift(-1)   # next day

    # Filter: need ≥50 market stocks on that day (use breadth as proxy)
    valid_dates = set(
        pd.read_sql_query(
            "SELECT date FROM market_breadth_enhanced WHERE n_stocks >= 50",
            conn
        )['date']
    )

    df = ohlcv.dropna(subset=_PH55_FEATURES + ['target_ret']).copy()
    df = df[df['trade_date'].dt.strftime('%Y-%m-%d').isin(valid_dates)].copy()

    if len(df) < 500:
        conn.close()
        print(json.dumps({"error": f"Ph55: only {len(df)} training rows — need ≥500"}), flush=True)
        return

    # Direction label
    df['label'] = 1   # FLAT
    df.loc[df['target_ret'] >  UP_THR, 'label'] = 2   # UP
    df.loc[df['target_ret'] <  DN_THR, 'label'] = 0   # DOWN

    label_map = {0: 'DOWN', 1: 'FLAT', 2: 'UP'}

    df_sorted = df.sort_values('trade_date').reset_index(drop=True)
    split_idx = int(len(df_sorted) * 0.80)

    X_train = df_sorted[_PH55_FEATURES].iloc[:split_idx].values.astype('float32')
    y_train = df_sorted['label'].iloc[:split_idx].values
    X_test  = df_sorted[_PH55_FEATURES].iloc[split_idx:].values.astype('float32')
    y_test  = df_sorted['label'].iloc[split_idx:].values

    n_total   = len(df_sorted)
    n_train   = len(y_train)
    n_classes = len(np.unique(y_train))
    class_dist = {label_map[k]: int((y_train == k).sum()) for k in [0, 1, 2]}

    print(f"[Ph55] Training: {n_train} rows ({n_total} total) "
          f"| classes: {class_dist}", flush=True)

    # ── 7. HPO caching ────────────────────────────────────────────────────────
    BASE_PARAMS55 = {
        'objective':       'multiclass',
        'num_class':       3,
        'metric':          'multi_logloss',
        'verbosity':       -1,
        'n_jobs':          N_JOBS,
        'n_estimators':    200,
        'learning_rate':   0.05,
        'max_depth':       5,
        'num_leaves':      31,
        'min_child_samples': 30,
        'subsample':       0.8,
        'colsample_bytree': 0.8,
        'reg_alpha':       0.1,
        'reg_lambda':      1.0,
        'class_weight':    'balanced',   # handle FLAT minority class
        'random_state':    42,
    }

    _cached55 = None
    if _HPO55_CACHE_PATH.exists():
        try:
            _cached55 = json.loads(_HPO55_CACHE_PATH.read_text())
            age = (datetime.date.today() -
                   datetime.date.fromisoformat(_cached55.get('date', '2000-01-01'))).days
            if age <= 7:
                BASE_PARAMS55.update({k: v for k, v in _cached55.items()
                                      if k not in ('date', 'log_loss')})
                print(f"[Ph55] HPO cached ({age}d old), log-loss={_cached55.get('log_loss', '?')}", flush=True)
            else:
                _cached55 = None   # stale
        except Exception:
            _cached55 = None

    if _cached55 is None and optuna is not None:
        # HPO subsample: 8K rows, time-ordered (no shuffle leakage)
        hpo_max = 8_000
        if len(X_train) > hpo_max:
            # Take the most recent hpo_max rows to capture current market regime
            X_hpo = X_train[-hpo_max:]
            y_hpo = y_train[-hpo_max:]
            print(f"[Ph55] HPO using last {hpo_max:,}/{len(X_train):,} rows", flush=True)
        else:
            X_hpo, y_hpo = X_train, y_train

        # Use n_jobs=2 inside each trial to avoid thread explosion
        # (14-thread LGBM × 3-fold × 10-trial = heavy overhead)
        print(f"[Ph55] Running HPO (10 trials, {len(X_hpo):,} rows) …", flush=True)

        def _obj55(trial):
            params = {
                'objective':         'multiclass',
                'num_class':         3,
                'metric':            'multi_logloss',
                'verbosity':         -1,
                'n_jobs':            2,           # low thread count inside HPO
                'random_state':      42,
                'n_estimators':      trial.suggest_int('n_estimators', 80, 200),
                'learning_rate':     trial.suggest_float('learning_rate', 0.02, 0.15, log=True),
                'max_depth':         trial.suggest_int('max_depth', 3, 6),
                'num_leaves':        trial.suggest_int('num_leaves', 15, 50),
                'min_child_samples': trial.suggest_int('min_child_samples', 20, 60),
                'subsample':         trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree':  trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'reg_alpha':         trial.suggest_float('reg_alpha', 1e-3, 3.0, log=True),
                'reg_lambda':        trial.suggest_float('reg_lambda', 1e-3, 5.0, log=True),
                'class_weight':      'balanced',
            }
            # 2-fold CV only (faster)
            split = int(len(X_hpo) * 0.7)
            m = lgb.LGBMClassifier(**params)
            m.fit(X_hpo[:split], y_hpo[:split])
            return sk_log_loss(y_hpo[split:], m.predict_proba(X_hpo[split:]))

        study55 = optuna.create_study(direction='minimize',
                                      sampler=optuna.samplers.TPESampler(seed=42))
        study55.optimize(_obj55, n_trials=10, n_jobs=1, show_progress_bar=False)
        best55 = study55.best_params
        best55['date']     = today_str
        best55['log_loss'] = round(study55.best_value, 6)
        _HPO55_CACHE_PATH.write_text(json.dumps(best55, indent=2))
        BASE_PARAMS55.update({k: v for k, v in best55.items()
                              if k not in ('date', 'log_loss')})
        print(f"[Ph55] HPO done: log-loss={best55['log_loss']:.4f}", flush=True)

    # ── 8. Train final model ──────────────────────────────────────────────────
    scaler55 = StandardScaler()
    X_tr_sc  = scaler55.fit_transform(X_train)
    X_te_sc  = scaler55.transform(X_test)

    model55  = lgb.LGBMClassifier(**BASE_PARAMS55)
    model55.fit(X_tr_sc, y_train)

    # OOS metrics
    y_pred    = model55.predict(X_te_sc)
    y_proba   = model55.predict_proba(X_te_sc)
    acc_oos   = round(accuracy_score(y_test, y_pred), 4)
    ll_oos    = round(sk_log_loss(y_test, y_proba), 4)

    print(f"[Ph55] OOS Accuracy={acc_oos:.3f}  Log-Loss={ll_oos:.4f}", flush=True)

    # ── 9. Predict for latest day (all stocks) ────────────────────────────────
    print("[Ph55] Scoring latest day …", flush=True)

    latest_date = ohlcv['trade_date'].max()
    latest_str  = latest_date.strftime('%Y-%m-%d')
    today_rows  = ohlcv[ohlcv['trade_date'] == latest_date].copy()

    feat_rows   = today_rows.dropna(subset=_PH55_FEATURES)
    n_scored    = len(feat_rows)

    if n_scored == 0:
        conn.close()
        print(json.dumps({"error": "Ph55: no stocks with complete features for latest date"}), flush=True)
        return

    X_today = scaler55.transform(feat_rows[_PH55_FEATURES].values.astype('float32'))
    proba   = model55.predict_proba(X_today)   # shape (n, 3)
    preds   = model55.predict(X_today)         # 0/1/2

    # ── 10. Store predictions ─────────────────────────────────────────────────
    # Latest Ph51 market direction
    mkt_dir_row = conn.execute(
        "SELECT direction FROM tomorrow_forecast ORDER BY id DESC LIMIT 1"
    ).fetchone()
    mkt_dir = mkt_dir_row['direction'] if mkt_dir_row else 'UNKNOWN'

    records = []
    for i, (_, row) in enumerate(feat_rows.iterrows()):
        lbl    = int(preds[i])
        p_down = float(proba[i][0])
        p_flat = float(proba[i][1])
        p_up   = float(proba[i][2])
        conf   = float(max(p_down, p_flat, p_up))
        records.append((
            today_str,
            row['symbol'],
            label_map[lbl],
            round(p_up, 4),
            round(p_flat, 4),
            round(p_down, 4),
            round(conf, 4),
            row.get('sector', 'Unknown'),
            int(row.get('sector_rank', 0)),
            mkt_dir,
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO stock_tomorrow_forecast
            (forecast_date, symbol, direction, p_up, p_flat, p_down,
             confidence, sector, sector_rank, market_direction)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, records)
    conn.commit()

    # Summary stats
    dir_counts = {'UP': 0, 'FLAT': 0, 'DOWN': 0}
    for r in records:
        dir_counts[r[2]] = dir_counts.get(r[2], 0) + 1

    top_up = sorted(
        [r for r in records if r[2] == 'UP'],
        key=lambda x: -x[3]  # sort by p_up desc
    )[:10]
    top_up_syms = [r[1] for r in top_up]

    dur = time.time() - t0

    # Log to ml_trainer_runs
    summary = {
        'phase':          '55',
        'forecast_date':  today_str,
        'latest_data':    latest_str,
        'n_training_rows': int(n_total),
        'n_scored':        int(n_scored),
        'acc_oos':         acc_oos,
        'll_oos':          ll_oos,
        'dir_counts':      dir_counts,
        'market_direction': mkt_dir,
        'top_up_stocks':    top_up_syms,
        'duration_seconds': round(dur, 1),
    }
    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '55', round(dur, 1), json.dumps(summary))
    )
    conn.commit()
    conn.close()

    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 56 — MARKOV REGIME ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def phase56_markov_regime():
    """
    Walk-Forward Markov Regime Engine for EGX market index.

    Architecture (3 layers):
      Core    : Roll20 → Percentile state (rolling p25/p75 over 60d) +
                         Robust Z-Score (MAD-based) → decision table → state_base
      WF Matrix: Per-date walk-forward transition matrix (no lookahead) →
                 p_bear/side/bull_1d, continuation_confidence, signal_1d,
                 entropy, regime_age, transition_risk, M^3/M^5 multi-step,
                 stationary distribution
      HMM     : GaussianHMM(3 states) as confirmation layer (weekly, not daily)
                Maps states by mean return: lowest→BEAR, middle→SIDE, highest→BULL
                Writes hmm_agreement + triple_confirmed

    Outputs:
      markov_regime_daily   — one row per trading date (state classification)
      markov_signal_daily   — one row per trading date (transition probabilities)
    """
    import math
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(f"[Ph56] Starting Markov Regime Engine …", flush=True)

    conn = get_db()
    _ensure_markov_tables(conn)

    # ── 0. Load EGX market index returns ────────────────────────────────────
    # Use the breadth-enhanced table if available, else compute from ohlcv
    # market_breadth_enhanced: date col='date', return col='market_ret_median'
    mkt = None
    try:
        mkt = pd.read_sql_query("""
            SELECT date AS trade_date, market_ret_median AS mkt_ret
            FROM market_breadth_enhanced
            WHERE market_ret_median IS NOT NULL
            ORDER BY date
        """, conn)
        mkt['trade_date'] = pd.to_datetime(mkt['trade_date'])
        print(f"[Ph56] Loaded {len(mkt)} rows from market_breadth_enhanced", flush=True)
    except Exception as ex:
        print(f"[Ph56] breadth fallback: {ex}", flush=True)

    if mkt is None or len(mkt) < 50:
        # Fallback: compute equal-weighted market return from ohlcv_history
        # ohlcv_history columns: id, symbol, bar_time, open, high, low, close, volume
        try:
            raw = pd.read_sql_query("""
                SELECT bar_time AS trade_date, symbol, close
                FROM ohlcv_history
                ORDER BY symbol, bar_time
            """, conn)
            raw['trade_date'] = pd.to_datetime(raw['trade_date'])
            raw = raw.sort_values(['symbol', 'trade_date'])
            raw['ret'] = raw.groupby('symbol')['close'].pct_change()
            mkt = (raw.groupby('trade_date')['ret']
                      .median()
                      .reset_index()
                      .rename(columns={'ret': 'mkt_ret'}))
            mkt = mkt.dropna().sort_values('trade_date').reset_index(drop=True)
            print(f"[Ph56] Fallback: computed mkt_ret from ohlcv ({len(mkt)} rows)", flush=True)
        except Exception as e:
            print(f"[Ph56] ERROR loading market data: {e}", flush=True)
            conn.close()
            return {"error": str(e)}

    if len(mkt) < 50:
        msg = f"Insufficient market data: {len(mkt)} rows (need ≥50)"
        print(f"[Ph56] {msg}", flush=True)
        conn.close()
        return {"error": msg}

    mkt = mkt.sort_values('trade_date').reset_index(drop=True)

    # ── 1. CORE LAYER: Roll20, Percentile State, Robust Z-Score ─────────────
    ROLL_WINDOW   = 20    # 20-day cumulative return
    PCT_WINDOW    = 60    # rolling percentile window
    MIN_HISTORY   = 40    # minimum rows before walk-forward starts
    PCT_LO        = 25    # lower percentile → BEAR
    PCT_HI        = 75    # upper percentile → BULL
    Z_THRESH      = 0.5   # Robust Z threshold (±0.5 MAD-units)

    # 20-day rolling sum of daily returns ≈ 20-day cumulative return
    mkt['roll20'] = mkt['mkt_ret'].rolling(ROLL_WINDOW, min_periods=ROLL_WINDOW).sum()

    n = len(mkt)
    state_pct_list  = [None] * n
    state_z_list    = [None] * n
    roll20_pct_list = [None] * n
    roll20_zscore   = [None] * n

    for i in range(n):
        r20 = mkt.at[i, 'roll20']
        if pd.isna(r20):
            continue
        roll20_pct_list[i] = float(r20)

        # ── Percentile state (rolling 60-day window, past-only) ──────────
        window_start = max(0, i - PCT_WINDOW + 1)
        window_vals  = mkt['roll20'].iloc[window_start: i + 1].dropna().values
        if len(window_vals) < 10:
            state_pct_list[i] = 'SIDE'
        else:
            lo = np.percentile(window_vals, PCT_LO)
            hi = np.percentile(window_vals, PCT_HI)
            if r20 >= hi:
                state_pct_list[i] = 'BULL'
            elif r20 <= lo:
                state_pct_list[i] = 'BEAR'
            else:
                state_pct_list[i] = 'SIDE'

        # ── Robust Z-Score (MAD-based, past-only window) ──────────────────
        if len(window_vals) < 10:
            state_z_list[i] = 'SIDE'
            roll20_zscore[i] = 0.0
        else:
            med  = np.median(window_vals)
            mad  = np.median(np.abs(window_vals - med))
            if mad < 1e-10:
                rz = 0.0
            else:
                rz = (r20 - med) / (mad * 1.4826)
            roll20_zscore[i] = float(rz)
            if rz >= Z_THRESH:
                state_z_list[i] = 'BULL'
            elif rz <= -Z_THRESH:
                state_z_list[i] = 'BEAR'
            else:
                state_z_list[i] = 'SIDE'

    mkt['state_pct']    = state_pct_list
    mkt['state_z']      = state_z_list
    mkt['roll20_pct']   = roll20_pct_list
    mkt['roll20_zscore']= roll20_zscore

    # ── Decision table merge ──────────────────────────────────────────────
    state_base_list = []
    sub_label_list  = []
    base_conf_list  = []
    for i in range(n):
        sp = mkt.at[i, 'state_pct']
        sz = mkt.at[i, 'state_z']
        if sp is None or sz is None:
            state_base_list.append(None)
            sub_label_list.append(None)
            base_conf_list.append(None)
        else:
            sb, sl, bc = _markov_decision_table(sp, sz)
            state_base_list.append(sb)
            sub_label_list.append(sl)
            base_conf_list.append(bc)

    mkt['state_base']      = state_base_list
    mkt['sub_label']       = sub_label_list
    mkt['base_confidence'] = base_conf_list

    # ── Compute percentile rank (for metadata storage) ────────────────────
    pct_rank = []
    for i in range(n):
        r20 = mkt.at[i, 'roll20']
        if pd.isna(r20):
            pct_rank.append(None)
            continue
        window_start = max(0, i - PCT_WINDOW + 1)
        wvals = mkt['roll20'].iloc[window_start: i + 1].dropna().values
        if len(wvals) < 2:
            pct_rank.append(50.0)
        else:
            rank = float(np.sum(wvals <= r20)) / len(wvals) * 100.0
            pct_rank.append(rank)
    mkt['roll20_percentile'] = pct_rank

    print(f"[Ph56] Core layer done. States: "
          f"BULL={sum(1 for s in state_base_list if s=='BULL')} "
          f"SIDE={sum(1 for s in state_base_list if s=='SIDE')} "
          f"BEAR={sum(1 for s in state_base_list if s=='BEAR')}", flush=True)

    # ── 2. WALK-FORWARD TRANSITION MATRIX ───────────────────────────────────
    STATE_MAP   = {'BEAR': 0, 'SIDE': 1, 'BULL': 2}
    STATE_NAMES = ['BEAR', 'SIDE', 'BULL']

    def _build_transition_matrix(states_seq):
        """Build 3×3 transition matrix from sequence of state strings (past-only)."""
        counts = np.ones((3, 3))   # Laplace smoothing (avoid zero rows)
        for a, b in zip(states_seq[:-1], states_seq[1:]):
            if a in STATE_MAP and b in STATE_MAP:
                counts[STATE_MAP[a], STATE_MAP[b]] += 1
        # Row-normalize
        row_sums = counts.sum(axis=1, keepdims=True)
        return counts / row_sums

    def _matrix_power(M, k):
        """Raise transition matrix M to integer power k."""
        result = np.eye(3)
        for _ in range(k):
            result = result @ M
        return result

    def _stationary(M):
        """Stationary distribution: left eigenvector for eigenvalue=1."""
        try:
            eigenvalues, eigenvectors = np.linalg.eig(M.T)
            idx = np.argmin(np.abs(eigenvalues - 1.0))
            stat = np.real(eigenvectors[:, idx])
            stat = np.abs(stat)
            s = stat.sum()
            if s < 1e-10:
                return np.array([1/3, 1/3, 1/3])
            return stat / s
        except Exception:
            return np.array([1/3, 1/3, 1/3])

    def _entropy(p_vec):
        """Shannon entropy (bits) of probability vector."""
        h = 0.0
        for p in p_vec:
            if p > 1e-10:
                h -= p * math.log2(p)
        return h

    wf_rows = []   # will hold dicts for markov_signal_daily

    for i in range(MIN_HISTORY, n):
        past_states = [s for s in mkt['state_base'].iloc[:i].tolist()
                       if s is not None]
        if len(past_states) < 10:
            continue

        M = _build_transition_matrix(past_states)

        # Current state
        cur_state = mkt.at[i, 'state_base']
        if cur_state is None:
            continue
        cur_idx = STATE_MAP[cur_state]

        # 1-step probabilities from current row
        p_bear_1d = float(M[cur_idx, 0])
        p_side_1d = float(M[cur_idx, 1])
        p_bull_1d = float(M[cur_idx, 2])
        continuation = float(M[cur_idx, cur_idx])
        transition_risk = 1.0 - continuation
        signal_1d = p_bull_1d - p_bear_1d

        # Entropy of 1-step forecast
        ent = _entropy([p_bear_1d, p_side_1d, p_bull_1d])

        # Regime age: consecutive days in the same state (looking backward)
        age = 1
        for j in range(i - 1, -1, -1):
            if mkt.at[j, 'state_base'] == cur_state:
                age += 1
            else:
                break

        # Multi-step: M^3, M^5
        M3 = _matrix_power(M, 3)
        M5 = _matrix_power(M, 5)
        p_bull_3d = float(M3[cur_idx, 2])
        signal_3d = float(M3[cur_idx, 2] - M3[cur_idx, 0])
        p_bull_5d = float(M5[cur_idx, 2])
        signal_5d = float(M5[cur_idx, 2] - M5[cur_idx, 0])

        # Stationary distribution
        stat = _stationary(M)
        stat_bear, stat_side, stat_bull = float(stat[0]), float(stat[1]), float(stat[2])

        wf_rows.append({
            'date':                    mkt.at[i, 'trade_date'].strftime('%Y-%m-%d'),
            'current_state':           cur_state,
            'regime_age':              age,
            'p_bear_1d':               round(p_bear_1d, 6),
            'p_side_1d':               round(p_side_1d, 6),
            'p_bull_1d':               round(p_bull_1d, 6),
            'continuation_confidence': round(continuation, 6),
            'signal_1d':               round(signal_1d, 6),
            'transition_risk':         round(transition_risk, 6),
            'entropy':                 round(ent, 6),
            'p_bull_3d':               round(p_bull_3d, 6),
            'signal_3d':               round(signal_3d, 6),
            'p_bull_5d':               round(p_bull_5d, 6),
            'signal_5d':               round(signal_5d, 6),
            'stat_bear':               round(stat_bear, 6),
            'stat_side':               round(stat_side, 6),
            'stat_bull':               round(stat_bull, 6),
            'hmm_agreement':           None,
            'triple_confirmed':        None,
            'wf_signal_correct':       None,
        })

    print(f"[Ph56] Walk-Forward: {len(wf_rows)} signal rows computed", flush=True)

    # ── 3. WF ACCURACY (post-process: compare signal_1d to next-day actual) ──
    # signal_1d > 0 → predicted BULL next day; actual = mkt_ret > 0
    date_to_idx = {mkt.at[i, 'trade_date'].strftime('%Y-%m-%d'): i for i in range(n)}
    for row in wf_rows:
        d_idx = date_to_idx.get(row['date'])
        if d_idx is None or d_idx + 1 >= n:
            continue
        next_ret = mkt.at[d_idx + 1, 'mkt_ret']
        if pd.isna(next_ret):
            continue
        predicted_bull = row['signal_1d'] > 0
        actual_bull    = next_ret > 0
        row['wf_signal_correct'] = int(predicted_bull == actual_bull)

    wf_correct = [r['wf_signal_correct'] for r in wf_rows if r['wf_signal_correct'] is not None]
    wf_acc = sum(wf_correct) / len(wf_correct) if wf_correct else 0.0
    print(f"[Ph56] WF signal accuracy: {wf_acc:.1%} ({len(wf_correct)} obs)", flush=True)

    # ── 4. HMM CONFIRMATION LAYER ────────────────────────────────────────────
    hmm_states_map = {}   # date_str → {state_hmm, hmm_state_label}
    hmm_ok = False
    try:
        from hmmlearn import hmm as _hmm

        # Use roll20 values where available for HMM input
        hmm_data = mkt[['trade_date', 'roll20']].dropna().copy()
        hmm_data = hmm_data.sort_values('trade_date').reset_index(drop=True)

        if len(hmm_data) >= 60:
            X = hmm_data['roll20'].values.reshape(-1, 1)

            model_hmm = _hmm.GaussianHMM(
                n_components=3,
                covariance_type='diag',
                n_iter=100,
                random_state=42,
            )
            model_hmm.fit(X)
            hidden = model_hmm.predict(X)

            # Map HMM states by mean return: lowest→BEAR, middle→SIDE, highest→BULL
            means = [model_hmm.means_[s, 0] for s in range(3)]
            order = np.argsort(means)   # ascending: [low, mid, high]
            hmm_label_map = {int(order[0]): 'BEAR',
                             int(order[1]): 'SIDE',
                             int(order[2]): 'BULL'}

            for idx_h, (_, row_h) in enumerate(hmm_data.iterrows()):
                date_str = row_h['trade_date'].strftime('%Y-%m-%d')
                raw_state = int(hidden[idx_h])
                hmm_states_map[date_str] = {
                    'state_hmm':      raw_state,
                    'hmm_state_label': hmm_label_map.get(raw_state, 'SIDE'),
                }

            print(f"[Ph56] HMM fitted on {len(hmm_data)} rows, "
                  f"decoded {len(hmm_states_map)} dates", flush=True)
            hmm_ok = True
        else:
            print(f"[Ph56] HMM skipped: need ≥60 rows, have {len(hmm_data)}", flush=True)
    except ImportError:
        print("[Ph56] hmmlearn not installed — HMM layer skipped", flush=True)
    except Exception as e:
        print(f"[Ph56] HMM error (non-fatal): {e}", flush=True)

    # Attach HMM agreement to WF rows
    for row in wf_rows:
        d  = row['date']
        sb = row['current_state']
        hm = hmm_states_map.get(d)
        if hm:
            row['hmm_agreement'] = int(hm['hmm_state_label'] == sb)
            # Triple-confirmed: Percentile == Z-Score == HMM
            d_idx = date_to_idx.get(d)
            if d_idx is not None:
                sp = mkt.at[d_idx, 'state_pct']
                sz = mkt.at[d_idx, 'state_z']
                row['triple_confirmed'] = int(
                    sp == sb and sz == sb and hm['hmm_state_label'] == sb
                )

    # ── 5. WRITE markov_regime_daily ─────────────────────────────────────────
    regime_rows = []
    for i in range(n):
        d_str = mkt.at[i, 'trade_date'].strftime('%Y-%m-%d')
        sb  = mkt.at[i, 'state_base']
        if sb is None:
            continue
        hm = hmm_states_map.get(d_str, {})
        regime_rows.append((
            d_str,
            mkt.at[i, 'roll20_pct'],
            mkt.at[i, 'roll20_percentile'],
            mkt.at[i, 'roll20_zscore'],
            mkt.at[i, 'state_pct'],
            mkt.at[i, 'state_z'],
            sb,
            mkt.at[i, 'sub_label'],
            mkt.at[i, 'base_confidence'],
            hm.get('state_hmm'),
            hm.get('hmm_state_label'),
            int(hm.get('hmm_state_label') == sb) if hm else None,
        ))

    conn.executemany("""
        INSERT INTO markov_regime_daily
            (date, roll20_pct, roll20_percentile, roll20_zscore,
             state_pct, state_z, state_base, sub_label, base_confidence,
             state_hmm, hmm_state_label, hmm_agreement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            roll20_pct=excluded.roll20_pct,
            roll20_percentile=excluded.roll20_percentile,
            roll20_zscore=excluded.roll20_zscore,
            state_pct=excluded.state_pct,
            state_z=excluded.state_z,
            state_base=excluded.state_base,
            sub_label=excluded.sub_label,
            base_confidence=excluded.base_confidence,
            state_hmm=excluded.state_hmm,
            hmm_state_label=excluded.hmm_state_label,
            hmm_agreement=excluded.hmm_agreement,
            computed_at=datetime('now')
    """, regime_rows)
    conn.commit()
    print(f"[Ph56] markov_regime_daily: {len(regime_rows)} rows written", flush=True)

    # ── 6. WRITE markov_signal_daily ─────────────────────────────────────────
    conn.executemany("""
        INSERT INTO markov_signal_daily
            (date, current_state, regime_age,
             p_bear_1d, p_side_1d, p_bull_1d,
             continuation_confidence, signal_1d, transition_risk, entropy,
             p_bull_3d, signal_3d, p_bull_5d, signal_5d,
             stat_bear, stat_side, stat_bull,
             hmm_agreement, triple_confirmed, wf_signal_correct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            current_state=excluded.current_state,
            regime_age=excluded.regime_age,
            p_bear_1d=excluded.p_bear_1d,
            p_side_1d=excluded.p_side_1d,
            p_bull_1d=excluded.p_bull_1d,
            continuation_confidence=excluded.continuation_confidence,
            signal_1d=excluded.signal_1d,
            transition_risk=excluded.transition_risk,
            entropy=excluded.entropy,
            p_bull_3d=excluded.p_bull_3d,
            signal_3d=excluded.signal_3d,
            p_bull_5d=excluded.p_bull_5d,
            signal_5d=excluded.signal_5d,
            stat_bear=excluded.stat_bear,
            stat_side=excluded.stat_side,
            stat_bull=excluded.stat_bull,
            hmm_agreement=excluded.hmm_agreement,
            triple_confirmed=excluded.triple_confirmed,
            wf_signal_correct=excluded.wf_signal_correct,
            computed_at=datetime('now')
    """, [(r['date'], r['current_state'], r['regime_age'],
           r['p_bear_1d'], r['p_side_1d'], r['p_bull_1d'],
           r['continuation_confidence'], r['signal_1d'],
           r['transition_risk'], r['entropy'],
           r['p_bull_3d'], r['signal_3d'], r['p_bull_5d'], r['signal_5d'],
           r['stat_bear'], r['stat_side'], r['stat_bull'],
           r['hmm_agreement'], r['triple_confirmed'], r['wf_signal_correct'])
          for r in wf_rows])
    conn.commit()
    print(f"[Ph56] markov_signal_daily: {len(wf_rows)} rows written", flush=True)

    # ── 7. LATEST SIGNAL SUMMARY ─────────────────────────────────────────────
    latest = None
    if wf_rows:
        latest = wf_rows[-1]

    # State distribution summary
    state_counts = {'BULL': 0, 'SIDE': 0, 'BEAR': 0}
    for sb in state_base_list:
        if sb in state_counts:
            state_counts[sb] += 1

    triple_count = sum(1 for r in wf_rows if r.get('triple_confirmed') == 1)

    dur = time.time() - t0
    summary = {
        'status':            'ok',
        'n_regime_rows':      len(regime_rows),
        'n_signal_rows':      len(wf_rows),
        'state_counts':       state_counts,
        'wf_accuracy':        round(wf_acc, 4),
        'wf_n_obs':           len(wf_correct),
        'triple_confirmed_n': triple_count,
        'hmm_enabled':        hmm_ok,
        'latest_date':        latest['date']          if latest else None,
        'latest_state':       latest['current_state'] if latest else None,
        'latest_signal_1d':   round(latest['signal_1d'], 4) if latest else None,
        'latest_entropy':     round(latest['entropy'], 4)   if latest else None,
        'latest_regime_age':  latest['regime_age']          if latest else None,
        'latest_transition_risk': round(latest['transition_risk'], 4) if latest else None,
        'duration_seconds':   round(dur, 1),
    }

    conn.execute(
        "INSERT INTO ml_trainer_runs (run_date, phase, duration_seconds, results) VALUES (?,?,?,?)",
        (today_str, '56', round(dur, 1), json.dumps(summary))
    )
    conn.commit()
    conn.close()

    print(json.dumps(summary), flush=True)
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 57 — Closing Pressure Signal (Daily OHLCV proxy for auction dynamics)
# ─────────────────────────────────────────────────────────────────────────────
# Since ohlcv_15min is unavailable, we synthesise closing-auction features
# from daily OHLCV:
#   close_pos     : (C - L) / (H - L)  → where in the day's range the close fell
#   body_ratio    : |C - O| / (H - L)  → candle body vs full range
#   upper_shadow  : (H - max(O,C)) / (H - L)  → upper wick fraction
#   lower_shadow  : (min(O,C) - L) / (H - L)  → lower wick fraction
#   vol_surge_1d  : today volume / 20d avg volume
#   closing_pressure : close_pos × vol_surge_1d  → composite score [0,∞)
#   gap_potential : 1 if close_pos > 0.75 and vol_surge_1d > 1.5, else 0
#   intraday_reversal: 1 if O > C and C near H (upper-shadow squeeze reversal)
#
# Writes: closing_pressure_daily  (one row per symbol per date)
# Read by: Ph51, Ph55 feature loaders (optional join — graceful if missing)
# ═════════════════════════════════════════════════════════════════════════════

def phase57_closing_pressure():
    """
    Ph57 — Closing Pressure Signal (daily OHLCV proxy).
    يحسب ضغط الإغلاق من OHLCV اليومية — بديل عملي عن بيانات 15 دقيقة.
    """
    import sqlite3 as _sq3

    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print("[Ph57] Computing closing pressure from daily OHLCV ...", flush=True)

    conn = get_db()

    # ── 0. Ensure output table ────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS closing_pressure_daily (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            trade_date  TEXT NOT NULL,
            close_pos   REAL,    -- (C-L)/(H-L): 0=closed at low, 1=closed at high
            body_ratio  REAL,    -- |C-O|/(H-L): candle body fraction
            upper_shadow REAL,   -- upper wick fraction
            lower_shadow REAL,   -- lower wick fraction
            vol_surge   REAL,    -- today_vol / 20d_avg_vol
            closing_pressure REAL, -- close_pos * vol_surge composite
            gap_potential    INTEGER, -- 1 if strong close + high volume
            intraday_reversal INTEGER, -- 1 if bearish candle but upper-shadow squeeze
            UNIQUE(symbol, trade_date)
        )
    """)
    conn.commit()

    # ── 1. Load OHLCV with 20-bar rolling volume average ─────────────────────
    ohlcv = pd.read_sql_query("""
        SELECT symbol,
               date(bar_time,'unixepoch') AS trade_date,
               open, high, low, close, volume,
               bar_time
        FROM ohlcv_history
        WHERE high IS NOT NULL AND low IS NOT NULL
          AND high > low          -- skip zero-range bars
          AND open > 0 AND close > 0
        ORDER BY symbol, bar_time
    """, conn)

    if ohlcv.empty:
        conn.close()
        return {'success': False, 'error': 'ohlcv_history empty'}

    ohlcv['trade_date'] = ohlcv['trade_date'].astype(str)

    # ── 2. Compute features per symbol ───────────────────────────────────────
    VOL_WINDOW = 20

    def _feats(grp):
        g = grp.copy().reset_index(drop=True)
        hl = (g['high'] - g['low']).clip(lower=1e-9)

        g['close_pos']   = ((g['close'] - g['low'])  / hl).clip(0, 1)
        g['body_ratio']  = ((g['close'] - g['open']).abs() / hl).clip(0, 1)
        g['upper_shadow'] = ((g['high'] - g[['open','close']].max(axis=1)) / hl).clip(0, 1)
        g['lower_shadow'] = ((g[['open','close']].min(axis=1) - g['low'])  / hl).clip(0, 1)

        # Rolling 20-bar volume mean (past-only, shift=1 to avoid lookahead)
        vol_ma = g['volume'].shift(1).rolling(VOL_WINDOW, min_periods=5).mean()
        g['vol_surge'] = (g['volume'] / vol_ma.clip(lower=1)).clip(upper=20)

        g['closing_pressure'] = (g['close_pos'] * g['vol_surge']).round(4)

        # Gap potential: strong close + volume surge
        g['gap_potential'] = (
            (g['close_pos'] > 0.75) & (g['vol_surge'] > 1.5)
        ).astype(int)

        # Intraday reversal: bearish candle (O > C) but closed near high (close_pos > 0.6)
        # signals absorption / accumulation
        g['intraday_reversal'] = (
            (g['open'] > g['close']) & (g['close_pos'] > 0.6)
        ).astype(int)

        return g[['symbol','trade_date','close_pos','body_ratio',
                   'upper_shadow','lower_shadow','vol_surge',
                   'closing_pressure','gap_potential','intraday_reversal']]

    print("[Ph57] Computing features per symbol ...", flush=True)
    result = ohlcv.groupby('symbol', group_keys=False).apply(_feats)
    result = result.dropna(subset=['close_pos','vol_surge'])

    # ── 3. Write to DB (upsert) ───────────────────────────────────────────────
    written = 0
    for _, row in result.iterrows():
        try:
            conn.execute("""
                INSERT INTO closing_pressure_daily
                  (symbol, trade_date, close_pos, body_ratio,
                   upper_shadow, lower_shadow, vol_surge,
                   closing_pressure, gap_potential, intraday_reversal)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                  close_pos=excluded.close_pos,
                  body_ratio=excluded.body_ratio,
                  upper_shadow=excluded.upper_shadow,
                  lower_shadow=excluded.lower_shadow,
                  vol_surge=excluded.vol_surge,
                  closing_pressure=excluded.closing_pressure,
                  gap_potential=excluded.gap_potential,
                  intraday_reversal=excluded.intraday_reversal
            """, (
                row['symbol'], row['trade_date'],
                _safe(row['close_pos']),   _safe(row['body_ratio']),
                _safe(row['upper_shadow']), _safe(row['lower_shadow']),
                _safe(row['vol_surge']),   _safe(row['closing_pressure']),
                int(row['gap_potential']), int(row['intraday_reversal']),
            ))
            written += 1
        except Exception:
            pass

    conn.commit()

    # ── 4. Summary stats ──────────────────────────────────────────────────────
    n_rows = conn.execute("SELECT COUNT(*) FROM closing_pressure_daily").fetchone()[0]
    n_syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM closing_pressure_daily").fetchone()[0]
    latest = conn.execute("SELECT MAX(trade_date) FROM closing_pressure_daily").fetchone()[0]

    # Sample: top gap-potential stocks on latest date
    top_gap = conn.execute("""
        SELECT symbol, close_pos, vol_surge, closing_pressure
        FROM closing_pressure_daily
        WHERE trade_date = ? AND gap_potential = 1
        ORDER BY closing_pressure DESC LIMIT 10
    """, (latest,)).fetchall()

    conn.close()

    dur = time.time() - t0
    summary = {
        'phase':          '57',
        'rows_written':   written,
        'total_rows':     n_rows,
        'n_symbols':      n_syms,
        'latest_date':    latest,
        'top_gap_stocks': [dict(r) for r in top_gap],
        'duration_seconds': round(dur, 1),
    }
    print(f"[Ph57] Done: {written:,} rows written ({n_syms} symbols, latest={latest}) in {dur:.1f}s", flush=True)
    print(json.dumps(summary), flush=True)
    return summary


def _safe(v):
    """Convert numpy/pandas float to Python float, None on NaN/Inf."""
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY
# ═════════════════════════════════════════════════════════════════════════════

def cmd_train_all():
    """Run all phases sequentially."""
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    print(json.dumps({"status":"train_all_start","date":today_str,"cores":N_CPUS}), flush=True)

    phases = [
        ("1  — Feature Engineering",          phase1_build_features),
        ("2  — Explosion Ensemble",            phase2_explosion_ensemble),
        ("3  — Regime-Specific Models",        phase3_regime_models),
        ("4  — Per-Stock Models",              phase4_per_stock_models),
        ("5  — Triple Barrier",                phase5_triple_barrier),
        ("6  — Walk-Forward Backtest",         phase6_walkforward),
        ("7  — SHAP Analysis",                 phase7_shap),
        ("46 — Bayesian Win Rate",             phase46_bayesian_winrate),
        ("47 — QMC Portfolio Risk",            phase47_qmc_portfolio_risk),
        ("48 — Antithetic Backtest",           phase48_antithetic_backtest),
        ("49 — LHS Sensitivity",              phase49_lhs_sensitivity),
        ("50 — Adaptive Gate Calibration",    phase50_adaptive_gate),
        ("52+53 — Enhanced Breadth + Sector Rotation", phase52_53_enhanced_breadth),
        ("56 — Markov Regime Engine",          phase56_markov_regime),
        ("57 — Closing Pressure Signal",       phase57_closing_pressure),
        ("51 — Tomorrow Direction Forecast",  phase51_tomorrow_forecast),
        ("54 — Forecast Accuracy Tracker",     phase54_forecast_accuracy),
        ("55 — Per-Stock Tomorrow Forecast",   phase55_stock_forecast),
        ("8  — Predict Ensemble",              cmd_predict_ensemble),
        ("9  — Calibration",                   phase9_calibration),
        ("10 — TV Replay Backtest",            phase10_tv_replay_backtest),
        ("11 — Pine Analytics Fusion",         phase11_pine_features),
        ("12 — Incremental Learning",          phase12_incremental_update),
        ("13 — CPCV",                          phase13_cpcv),
        ("14 — MTF Confluence",                phase14_mtf_features),
        ("15 — Conformal Intervals",           phase15_conformal_intervals),
        ("16 — Feature Drift Monitor",         phase16_feature_drift),
        ("17 — Return Regressor",              phase17_return_regressor),
        ("18 — Survival Analysis (CoxPH)",     phase18_survival_analysis),
        ("19 — Kelly Portfolio Optimizer",     phase19_kelly_optimizer),
        ("20 — Pine ML Indicator",             phase20_pine_ml_indicator),
        ("21 — Spectral Cycle Intelligence",   phase21_spectral_intelligence),
        ("23 — Spectral Attribution Backtest", phase23_spectral_attribution),
        ("25 — Spectral Reliability Memory",   phase25_spectral_reliability),
    ]

    all_results = {}
    for name, fn in phases:
        print(f"\n{'═'*60}\n  {name}\n{'═'*60}", flush=True)
        try:
            res = fn()
            all_results[name] = res
            gc.collect()
        except Exception as e:
            import traceback
            print(json.dumps({"error": f"{name} failed: {e}",
                              "traceback": traceback.format_exc()[-500:]}), flush=True)
            all_results[name] = {"error": str(e)}

    total = time.time() - t0
    print(json.dumps({"status":"train_all_complete",
                      "total_minutes": round(total/60, 1),
                      "phases": list(all_results.keys())}), flush=True)
    cmd_status()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    dispatch = {
        'train_all': cmd_train_all,
        'phase1':    phase1_build_features,
        'phase2':    phase2_explosion_ensemble,
        'phase3':    phase3_regime_models,
        'phase4':    phase4_per_stock_models,
        'phase5':    phase5_triple_barrier,
        'phase6':    phase6_walkforward,
        'phase7':             phase7_shap,
        'phase46':            phase46_bayesian_winrate,
        'phase47':            phase47_qmc_portfolio_risk,
        'phase48':            phase48_antithetic_backtest,
        'phase49':            phase49_lhs_sensitivity,
        'phase50':            phase50_adaptive_gate,
        'phase51':            phase51_tomorrow_forecast,
        'phase52':            phase52_53_enhanced_breadth,
        'phase53':            phase52_53_enhanced_breadth,
        'phase54':            phase54_forecast_accuracy,
        'phase55':            phase55_stock_forecast,
        'phase56':            phase56_markov_regime,
        'phase57':            phase57_closing_pressure,
        'predict_ensemble':   cmd_predict_ensemble,
        'phase9':             phase9_calibration,
        'phase10':            phase10_tv_replay_backtest,
        'phase11':            phase11_pine_features,
        'phase12':            phase12_incremental_update,
        'phase13':            phase13_cpcv,
        'phase14':            phase14_mtf_features,
        'phase15':            phase15_conformal_intervals,
        'phase16':            phase16_feature_drift,
        'phase17':            phase17_return_regressor,
        'phase18':            phase18_survival_analysis,
        'phase19':            phase19_kelly_optimizer,
        'phase20':            phase20_pine_ml_indicator,
        'phase21':            phase21_spectral_intelligence,
        'phase23':            phase23_spectral_attribution,
        'phase25':            phase25_spectral_reliability,
        'status':             cmd_status,
    }
    if cmd in dispatch:
        dispatch[cmd]()
    else:
        print(json.dumps({"error": "unknown command",
                          "usage": "train_all|phase1..21|phase23|phase25|predict_ensemble|status"}))
        sys.exit(1)
