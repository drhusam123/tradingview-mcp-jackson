#!/usr/bin/env python3
"""
EGX Heavy Analysis — Python/pandas/scipy Bridge
=================================================
يُستدعى من Node.js عبر subprocess عندما تحتاج تحليلات أثقل مما تتحمله JS.
يقبل أوامر عبر stdin (JSON) ويُعيد النتائج عبر stdout (JSON).

الأوامر:
  full_stats    — إحصاء شامل على كل الـ 75K شمعة
  sector_heatmap — حرارة القطاعات (momentum 5/10/20d)
  rolling_stats  — mean/std/sharpe متحركة
  granger_test   — Granger Causality بين سهمين
  volatility_regime — تحليل EWMA volatility regimes
  export_csv    — تصدير indicators_cache كـ CSV

الاستخدام من Node:
  import { runPythonAnalysis } from './src/egx/python_bridge.js';
  const result = await runPythonAnalysis('full_stats', { sample: 5000 });

المالك: Dr. Husam | إنشاء: مايو 2026
"""

import sys
import json
import sqlite3
import os
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from scipy import stats

DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/egx_trading.db')


def get_connection():
    return sqlite3.connect(DB_PATH)


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: full_stats
# ═══════════════════════════════════════════════════════════════════════════

def cmd_full_stats(params):
    """إحصاء شامل على indicators_cache + OHLCV — الـ pandas يتفوق على JS هنا"""
    con = get_connection()

    # تحميل indicators_cache
    ic = pd.read_sql("""
        WITH latest AS (
            SELECT symbol, MAX(bar_date) as max_date FROM indicators_cache GROUP BY symbol
        )
        SELECT ic.*
        FROM indicators_cache ic
        JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
    """, con)

    if ic.empty:
        return {"error": "indicators_cache فارغ"}

    # إحصاء وصفي لكل مؤشر رقمي
    numeric_cols = ['rsi14', 'adx14', 'atr14', 'vol_ratio_20', 'bb_position',
                    'momentum_5d', 'momentum_10d', 'momentum_20d',
                    'macd_hist', 'stoch_k', 'cci20']
    available = [c for c in numeric_cols if c in ic.columns]

    desc = ic[available].describe(percentiles=[.05, .25, .5, .75, .95]).round(3)

    # توزيع RSI
    rsi_dist = {}
    if 'rsi14' in ic.columns:
        rsi_series = ic['rsi14'].dropna()
        bins = [0, 30, 40, 50, 60, 70, 80, 100]
        labels = ['<30', '30-40', '40-50', '50-60', '60-70', '70-80', '>80']
        rsi_buckets = pd.cut(rsi_series, bins=bins, labels=labels)
        rsi_dist = rsi_buckets.value_counts().sort_index().to_dict()
        rsi_dist = {str(k): int(v) for k, v in rsi_dist.items()}

    # OBV distribution
    obv_dist = {}
    if 'obv_divergence' in ic.columns:
        obv_dist = ic['obv_divergence'].fillna('none').value_counts().to_dict()
        obv_dist = {str(k): int(v) for k, v in obv_dist.items()}

    # أفضل momentum
    top_momentum = []
    if 'momentum_5d' in ic.columns:
        top = ic[['symbol', 'rsi14', 'adx14', 'momentum_5d', 'vol_ratio_20', 'obv_divergence']]\
            .dropna(subset=['momentum_5d'])\
            .nlargest(10, 'momentum_5d')
        top_momentum = top.round(2).to_dict('records')

    # RSI+OBV Combos
    combos = []
    if 'rsi14' in ic.columns and 'obv_divergence' in ic.columns:
        mask = (ic['rsi14'] <= 35) & (ic['obv_divergence'] == 'bullish')
        combo_df = ic[mask][['symbol', 'bar_date', 'rsi14', 'adx14', 'vol_ratio_20']]
        combos = combo_df.round(2).to_dict('records')

    con.close()
    return {
        "symbols_count": int(len(ic)),
        "rsi_distribution": rsi_dist,
        "obv_distribution": obv_dist,
        "top_momentum_5d": top_momentum,
        "rsi_obv_combos": combos,
        "describe": {col: desc[col].to_dict() for col in available if col in desc.columns},
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: rolling_stats
# ═══════════════════════════════════════════════════════════════════════════

def cmd_rolling_stats(params):
    """
    Rolling mean/std/sharpe على 75K شمعة
    أفضل من JS لأن pandas vectorized rolling operations أسرع بـ 100x
    """
    symbol = params.get('symbol')
    window = params.get('window', 20)
    limit  = params.get('limit', 500)

    con = get_connection()
    sql = """
        SELECT symbol,
               date(bar_time,'unixepoch') as date,
               close, volume
        FROM ohlcv_history
        WHERE volume > 0
    """
    if symbol:
        sql += f" AND symbol = '{symbol}'"
    sql += f" ORDER BY bar_time DESC LIMIT {limit}"

    df = pd.read_sql(sql, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df = df.sort_values('date')
    df['return'] = df.groupby('symbol')['close'].pct_change() * 100

    result = {
        "symbol": symbol or "EGX sample",
        "window": window,
        "data_points": int(len(df)),
    }

    if symbol:
        df['rolling_mean']   = df['return'].rolling(window).mean().round(3)
        df['rolling_std']    = df['return'].rolling(window).std().round(3)
        df['rolling_sharpe'] = (df['rolling_mean'] / df['rolling_std'].replace(0, np.nan)).round(3)
        df['rolling_vol20']  = df['volume'].rolling(window).mean().round(0)

        # آخر N صفوف
        tail = df.tail(30)[['date', 'close', 'return', 'rolling_mean', 'rolling_std', 'rolling_sharpe']]\
                 .dropna().to_dict('records')

        result['latest_30_days'] = tail
        result['current_rolling'] = {
            "mean":   float(df['rolling_mean'].iloc[-1]) if not df['rolling_mean'].empty else None,
            "std":    float(df['rolling_std'].iloc[-1])  if not df['rolling_std'].empty  else None,
            "sharpe": float(df['rolling_sharpe'].iloc[-1]) if not df['rolling_sharpe'].empty else None,
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: return_analysis
# ═══════════════════════════════════════════════════════════════════════════

def cmd_return_analysis(params):
    """
    تحليل شامل للعوائد على كامل الـ 75K شمعة
    - توزيع العوائد حسب اليوم
    - توزيع العوائد حسب RSI bucket
    - T+1 / T+3 / T+5 returns بعد كل signal
    """
    limit  = params.get('limit', 75000)
    con    = get_connection()

    # جلب العوائد مع اليوم ونوع الـ signal
    df = pd.read_sql(f"""
        SELECT symbol,
               date(bar_time,'unixepoch') as date,
               strftime('%w', bar_time,'unixepoch') as dow,
               close, volume
        FROM ohlcv_history
        WHERE volume > 0
        ORDER BY symbol, bar_time
        LIMIT {limit}
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df['return'] = df.groupby('symbol')['close'].pct_change() * 100
    df = df.dropna(subset=['return'])
    df = df[df['return'].abs() < 20]  # تصفية outliers

    # 1. توزيع حسب يوم الأسبوع
    DAY_NAMES = {'0':'الأحد','1':'الاثنين','2':'الثلاثاء','3':'الأربعاء','4':'الخميس'}
    day_stats = df[df['dow'].isin(['0','1','2','3','4'])]\
        .groupby('dow')['return']\
        .agg(mean='mean', std='std', count='count', positive=lambda x: (x>0).mean()*100)\
        .round(4)\
        .reset_index()
    day_stats['day_name'] = day_stats['dow'].map(DAY_NAMES)
    day_stats_list = day_stats.to_dict('records')

    # 2. إحصاء عام
    general = {
        "total_returns":    int(len(df)),
        "mean":             float(df['return'].mean().round(4)),
        "std":              float(df['return'].std().round(4)),
        "skewness":         float(df['return'].skew().round(4)),
        "kurtosis":         float(df['return'].kurtosis().round(4)),
        "pct_positive":     float((df['return'] > 0).mean().round(4) * 100),
        "pct_over_1pct":    float((df['return'] > 1).mean().round(4) * 100),
        "pct_under_neg1pct":float((df['return'] < -1).mean().round(4) * 100),
        "fat_tail_3sigma":  float((df['return'].abs() > 3*df['return'].std()).mean().round(4) * 100),
    }

    # 3. Normality Tests
    sample = df['return'].sample(min(2000, len(df)), random_state=42).values
    ks_stat, ks_pval = stats.kstest(sample, 'norm',
                                     args=(sample.mean(), sample.std()))
    sw_stat, sw_pval = stats.shapiro(sample[:5000])

    normality = {
        "ks_stat":    float(round(ks_stat, 4)),
        "ks_pvalue":  float(round(ks_pval, 6)),
        "shapiro_stat":  float(round(sw_stat, 4)),
        "shapiro_pvalue":float(round(sw_pval, 6)),
        "is_normal":  bool(ks_pval > 0.05),
        "verdict":    "❌ ليس Normal — fat tails" if ks_pval < 0.05 else "✅ Normal Distribution",
    }

    # 4. Percentile Distribution
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pct_values  = {f"p{p}": float(round(float(np.percentile(df['return'].values, p)), 3))
                   for p in percentiles}

    return {
        "general":     general,
        "by_day":      day_stats_list,
        "normality":   normality,
        "percentiles": pct_values,
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: signal_backtest_pandas
# ═══════════════════════════════════════════════════════════════════════════

def cmd_signal_backtest(params):
    """
    Backtest سريع لإشارات RSI/BB/OBV على كل الـ 75K شمعة
    يحسب RSI/BB/OBV مباشرة من OHLCV بـ pandas EWM — لا يعتمد على indicators_cache
    يحسب: T+1, T+3, T+5 forward returns لكل signal
    """
    rsi_threshold = params.get('rsi_threshold', 35)
    limit         = params.get('limit', 75000)

    con = get_connection()
    df = pd.read_sql(f"""
        SELECT symbol, bar_time,
               date(bar_time,'unixepoch') as date,
               close, high, low, volume
        FROM ohlcv_history
        WHERE volume > 0
        ORDER BY symbol, bar_time
        LIMIT {limit}
    """, con)
    con.close()

    if df.empty or len(df) < 100:
        return {"error": "بيانات غير كافية"}

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)

    # RSI(14) — Wilder EWM
    delta = df.groupby('symbol')['close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean())
    avg_l = loss.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean())
    rs    = avg_g / avg_l.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # BB(20) position [0=lower, 1=upper]
    roll_m = df.groupby('symbol')['close'].transform(lambda x: x.rolling(20).mean())
    roll_s = df.groupby('symbol')['close'].transform(lambda x: x.rolling(20).std())
    bb_lo  = roll_m - 2 * roll_s
    bb_hi  = roll_m + 2 * roll_s
    df['bb_pos'] = (df['close'] - bb_lo) / (bb_hi - bb_lo).replace(0, np.nan)

    # OBV 5-bar divergence: price↓ + OBV↑ = bullish
    obv_raw = df.groupby('symbol').apply(
        lambda g: (np.sign(g['close'].diff()) * g['volume']).cumsum()
    ).reset_index(level=0, drop=True)
    df['obv']     = obv_raw
    df['p_5d']    = df.groupby('symbol')['close'].transform(lambda x: x - x.shift(5))
    df['obv_5d']  = df.groupby('symbol')['obv'].transform(lambda x: x - x.shift(5))
    df['obv_div'] = (df['p_5d'] < 0) & (df['obv_5d'] > 0)

    # ADX(14)
    hi_d = df.groupby('symbol')['high'].diff()
    lo_d = -df.groupby('symbol')['low'].diff()
    pdm  = hi_d.where((hi_d > lo_d) & (hi_d > 0), 0.0)
    mdm  = lo_d.where((lo_d > hi_d) & (lo_d > 0), 0.0)
    tr   = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df.groupby('symbol')['close'].shift()).abs(),
        (df['low']  - df.groupby('symbol')['close'].shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = tr.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean())
    pdi   = 100 * pdm.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean()) / atr14.replace(0, np.nan)
    mdi   = 100 * mdm.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean()) / atr14.replace(0, np.nan)
    dx    = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df['adx'] = dx.groupby(df['symbol']).transform(lambda x: x.ewm(com=13, min_periods=14).mean())

    # Forward Returns T+1, T+3, T+5
    for t in [1, 3, 5]:
        df[f'ret_t{t}'] = df.groupby('symbol')['close'].transform(
            lambda x: (x.shift(-t) / x - 1) * 100
        )

    df = df.dropna(subset=['rsi', 'ret_t5'])
    df = df[df['rsi'].between(0, 100)]
    total = len(df)

    if total < 100:
        return {"error": f"بعد الحساب: {total} صف فقط"}

    def sig_stats(mask, name):
        s = df[mask].copy()
        n = len(s)
        if n < 15:
            return {"name": name, "count": n, "insufficient": True}
        return {
            "name":      name,
            "count":     int(n),
            "t1_avg":    float(round(s['ret_t1'].mean(), 3)),
            "t3_avg":    float(round(s['ret_t3'].mean(), 3)),
            "t5_avg":    float(round(s['ret_t5'].mean(), 3)),
            "t5_wr":     float(round((s['ret_t5'] > 0).mean() * 100, 1)),
            "t5_median": float(round(s['ret_t5'].median(), 3)),
            "t5_p25":    float(round(s['ret_t5'].quantile(0.25), 3)),
            "t5_p75":    float(round(s['ret_t5'].quantile(0.75), 3)),
        }

    baseline  = pd.Series(True, index=df.index)
    rsi_m     = df['rsi'] <= rsi_threshold
    rsi30_m   = df['rsi'] <= 30
    obv_m     = df['obv_div'] == True
    combo_m   = rsi_m & obv_m
    bb_m      = df['bb_pos'] <= 0.05
    adx_sw    = df['adx'].between(20, 30)
    adx_hi    = df['adx'] >= 30

    results = [
        sig_stats(baseline,         "Baseline (كل الشمعات)"),
        sig_stats(rsi_m,            f"RSI ≤ {rsi_threshold}"),
        sig_stats(rsi30_m,          "RSI ≤ 30 (Oversold حاد)"),
        sig_stats(obv_m,            "OBV Bullish Divergence"),
        sig_stats(combo_m,          f"🔥 RSI≤{rsi_threshold} + OBV Combo"),
        sig_stats(bb_m,             "BB أسفل الباند السفلي"),
        sig_stats(adx_sw,           "ADX 20-30 (النطاق المثالي)"),
        sig_stats(adx_hi,           "ADX ≥ 30 (للمقارنة)"),
        sig_stats(rsi_m & adx_sw,   "RSI≤35 + ADX 20-30"),
    ]

    return {
        "signals":    results,
        "total_rows": int(total),
        "symbols":    int(df['symbol'].nunique()),
        "date_range": f"{df['date'].min()} → {df['date'].max()}",
        "note":       "RSI/BB/OBV محسوبة مباشرة من OHLCV — pandas vectorized",
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: export_csv
# ═══════════════════════════════════════════════════════════════════════════

def cmd_export_csv(params):
    """تصدير indicators_cache أو OHLCV إلى CSV للتحليل الخارجي"""
    table   = params.get('table', 'indicators_cache')
    outpath = params.get('output', f'/tmp/egx_{table}.csv')
    limit   = params.get('limit', 100000)

    con = get_connection()
    df  = pd.read_sql(f"SELECT * FROM {table} LIMIT {limit}", con)
    con.close()

    df.to_csv(outpath, index=False, encoding='utf-8-sig')  # utf-8-sig يعمل مع Excel
    return {
        "saved": outpath,
        "rows":  int(len(df)),
        "cols":  int(len(df.columns)),
        "columns": list(df.columns),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: sector_momentum
# ═══════════════════════════════════════════════════════════════════════════

def cmd_sector_momentum(params):
    """
    تحليل momentum مجمّع — يصنّف الأسهم حسب أداءها 5d/10d/20d
    يُستخدم لاكتشاف sector rotation
    """
    con = get_connection()
    ic  = pd.read_sql("""
        WITH latest AS (SELECT symbol, MAX(bar_date) d FROM indicators_cache GROUP BY symbol)
        SELECT ic.symbol, ic.momentum_5d, ic.momentum_10d, ic.momentum_20d,
               ic.rsi14, ic.adx14, ic.vol_ratio_20, ic.above_ema200
        FROM indicators_cache ic JOIN latest l ON ic.symbol=l.symbol AND ic.bar_date=l.d
        WHERE ic.momentum_5d IS NOT NULL
    """, con)
    con.close()

    if ic.empty:
        return {"error": "لا بيانات momentum"}

    # تصنيف كل سهم
    ic['momentum_score'] = (
        ic['momentum_5d'].fillna(0) * 0.5 +
        ic['momentum_10d'].fillna(0) * 0.3 +
        ic['momentum_20d'].fillna(0) * 0.2
    )

    ic['regime'] = pd.cut(
        ic['momentum_score'],
        bins=[-np.inf, -10, -3, 3, 10, np.inf],
        labels=['⬇️ هابط قوي', '⬇️ هابط', '➡️ تعزيز', '⬆️ صاعد', '⬆️ صاعد قوي']
    )

    regime_counts = ic['regime'].value_counts().sort_index().to_dict()
    regime_counts = {str(k): int(v) for k, v in regime_counts.items()}

    top10    = ic.nlargest(10, 'momentum_score')[
        ['symbol','momentum_5d','momentum_10d','momentum_20d','momentum_score','rsi14','adx14']
    ].round(2).to_dict('records')

    bottom10 = ic.nsmallest(10, 'momentum_score')[
        ['symbol','momentum_5d','momentum_10d','momentum_20d','momentum_score','rsi14','adx14']
    ].round(2).to_dict('records')

    # Market breadth
    breadth = {
        "above_ema200_pct": float(round(ic['above_ema200'].mean() * 100, 1)) if 'above_ema200' in ic else None,
        "pct_positive_5d":  float(round((ic['momentum_5d'] > 0).mean() * 100, 1)),
        "pct_positive_10d": float(round((ic['momentum_10d'] > 0).mean() * 100, 1)),
        "avg_momentum_5d":  float(round(ic['momentum_5d'].mean(), 2)),
        "market_tone": "🔴 هابط" if ic['momentum_5d'].mean() < -1 else
                       "🟢 صاعد" if ic['momentum_5d'].mean() > 1 else "🟡 محايد",
    }

    return {
        "total_symbols": int(len(ic)),
        "regime_distribution": regime_counts,
        "top10_momentum": top10,
        "bottom10_momentum": bottom10,
        "market_breadth": breadth,
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: param_sweep  (Grid Search موازي)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_param_sweep(params):
    """
    Grid Search على 73K شمعة لإيجاد أفضل parameters للاستراتيجية.
    يختبر: RSI_threshold × ADX_min × ADX_max × Hold_days
    يستخدم joblib.Parallel على 8 cores
    """
    import itertools
    from joblib import Parallel, delayed

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات OHLCV"}

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)

    # ── حساب RSI(14) و ADX(14) مرة واحدة ──────────────────────────────────
    def compute_rsi(series, window=14):
        delta = series.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_g = gain.ewm(com=window-1, min_periods=window).mean()
        avg_l = loss.ewm(com=window-1, min_periods=window).mean()
        rs    = avg_g / avg_l.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def compute_adx(grp, window=14):
        h, l, c = grp['high'], grp['low'], grp['close']
        prev_c  = c.shift(1)
        tr      = pd.concat([h-l, (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
        dm_plus = np.where((h - h.shift(1)) > (l.shift(1) - l), (h - h.shift(1)).clip(lower=0), 0)
        dm_minus= np.where((l.shift(1) - l) > (h - h.shift(1)), (l.shift(1) - l).clip(lower=0), 0)
        atr     = tr.ewm(com=window-1, min_periods=window).mean()
        di_plus = 100 * pd.Series(dm_plus, index=grp.index).ewm(com=window-1, min_periods=window).mean() / atr.replace(0, np.nan)
        di_minus= 100 * pd.Series(dm_minus,index=grp.index).ewm(com=window-1, min_periods=window).mean() / atr.replace(0, np.nan)
        dx      = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        return dx.ewm(com=window-1, min_periods=window).mean()

    print("[param_sweep] حساب RSI و ADX...", file=sys.stderr)
    df['rsi'] = df.groupby('symbol')['close'].transform(compute_rsi)
    df['adx'] = df.groupby('symbol', group_keys=False).apply(compute_adx).values

    # ── حساب العوائد المستقبلية ──────────────────────────────────────────
    for h in [1, 3, 5, 7, 10]:
        df[f'ret_{h}'] = df.groupby('symbol')['close'].transform(
            lambda x: x.shift(-h) / x - 1
        ) * 100

    df = df.dropna(subset=['rsi', 'adx'])

    # ── تعريف شبكة البحث ────────────────────────────────────────────────
    rsi_thresholds = [25, 30, 35, 40, 45]
    adx_mins       = [10, 15, 20, 25]
    adx_maxs       = [25, 30, 35, 40, 50, 100]
    hold_days      = [1, 3, 5, 7, 10]
    # فلتر: adx_max > adx_min
    grid = [(r, a_min, a_max, h)
            for r, a_min, a_max, h in itertools.product(rsi_thresholds, adx_mins, adx_maxs, hold_days)
            if a_max > a_min]

    print(f"[param_sweep] {len(grid)} تركيبة × {len(df):,} صف...", file=sys.stderr)

    def eval_combo(rsi_t, adx_min, adx_max, hold):
        mask = (df['rsi'] <= rsi_t) & (df['adx'] >= adx_min) & (df['adx'] <= adx_max)
        sub  = df[mask][f'ret_{hold}'].dropna()
        n    = len(sub)
        if n < 20:
            return None
        return {
            'rsi_threshold': rsi_t,
            'adx_min':       adx_min,
            'adx_max':       adx_max,
            'hold_days':     hold,
            'n':             n,
            'avg_return':    round(float(sub.mean()), 3),
            'win_rate':      round(float((sub > 0).mean() * 100), 1),
            'sharpe_proxy':  round(float(sub.mean() / sub.std()) if sub.std() > 0 else 0, 3),
        }

    results = Parallel(n_jobs=8, prefer='threads')(
        delayed(eval_combo)(r, a_min, a_max, h)
        for r, a_min, a_max, h in grid
    )
    results = [r for r in results if r is not None]

    # ── ترتيب النتائج ───────────────────────────────────────────────────
    df_res = pd.DataFrame(results)
    if df_res.empty:
        return {"error": "لا نتائج كافية"}

    # أفضل 20 بـ Sharpe proxy
    best_sharpe = df_res.nlargest(20, 'sharpe_proxy').to_dict('records')
    # أفضل 20 بـ WR (n >= 50)
    best_wr     = df_res[df_res['n'] >= 50].nlargest(20, 'win_rate').to_dict('records')
    # أفضل 5-day hold تحديداً
    best_5d     = df_res[df_res['hold_days'] == 5].nlargest(10, 'sharpe_proxy').to_dict('records')

    return {
        'total_combos':    len(grid),
        'valid_combos':    len(results),
        'best_by_sharpe':  best_sharpe,
        'best_by_winrate': best_wr,
        'best_5d_hold':    best_5d,
        'current_best': {
            'params': 'RSI≤35 + ADX 20-30',
            'wr_t5':  64.1,
            'avg_t5': 2.97,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: walk_forward  (التحقق من الاستراتيجية عبر الزمن)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_walk_forward(params):
    """
    Walk-Forward Validation: تحقق أن الاستراتيجية لم تكن overfit.
    نوافذ: train 18 شهر → test 6 شهر × 4 دورات
    يبحث عن أفضل RSI threshold على train ثم يختبره على test.
    """
    from joblib import Parallel, delayed

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, close FROM ohlcv_history
        ORDER BY symbol, bar_time
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df['bar_time'] = pd.to_datetime(df['bar_time'], unit='s')

    # RSI(14)
    def compute_rsi(series):
        d = series.diff()
        g = d.clip(lower=0).ewm(com=13, min_periods=14).mean()
        l = (-d).clip(lower=0).ewm(com=13, min_periods=14).mean()
        return 100 - (100 / (1 + g / l.replace(0, np.nan)))

    df['rsi'] = df.groupby('symbol')['close'].transform(compute_rsi)
    df['ret5'] = df.groupby('symbol')['close'].transform(lambda x: x.shift(-5) / x - 1) * 100

    # نوافذ walk-forward (timestamp)
    windows = [
        ('2022-01', '2023-06', '2023-07', '2023-12'),
        ('2022-07', '2024-01', '2024-02', '2024-07'),
        ('2023-01', '2024-07', '2024-08', '2025-01'),
        ('2023-07', '2025-01', '2025-02', '2025-07'),
        ('2024-01', '2025-07', '2025-08', '2026-04'),
    ]

    def run_window(train_start, train_end, test_start, test_end):
        ts, te = pd.Timestamp(train_start), pd.Timestamp(train_end)
        xs, xe = pd.Timestamp(test_start),  pd.Timestamp(test_end)

        train = df[(df['bar_time'] >= ts) & (df['bar_time'] <= te)].dropna()
        test  = df[(df['bar_time'] >= xs) & (df['bar_time'] <= xe)].dropna()

        if len(train) < 500 or len(test) < 100:
            return None

        # إيجاد أفضل RSI threshold على train
        best_thresh, best_sharpe = 35, -np.inf
        for thresh in [25, 28, 30, 32, 35, 38, 40]:
            sub = train[train['rsi'] <= thresh]['ret5'].dropna()
            if len(sub) < 30:
                continue
            sharpe = sub.mean() / sub.std() if sub.std() > 0 else 0
            if sharpe > best_sharpe:
                best_sharpe, best_thresh = sharpe, thresh

        # تطبيق على test
        test_signal = test[test['rsi'] <= best_thresh]['ret5'].dropna()
        test_base   = test['ret5'].dropna()

        return {
            'window':      f"{train_start}→{test_end}",
            'best_thresh': best_thresh,
            'train_n':     len(train),
            'test_n':      len(test_signal),
            'test_wr':     round(float((test_signal > 0).mean() * 100), 1) if len(test_signal) > 0 else None,
            'test_avg_r5': round(float(test_signal.mean()), 2)             if len(test_signal) > 0 else None,
            'base_wr':     round(float((test_base > 0).mean() * 100), 1),
            'base_avg_r5': round(float(test_base.mean()), 2),
            'edge':        round(float(test_signal.mean() - test_base.mean()), 2) if len(test_signal) > 0 else None,
        }

    results = Parallel(n_jobs=5, prefer='threads')(
        delayed(run_window)(ts, te, xs, xe) for ts, te, xs, xe in windows
    )
    results = [r for r in results if r is not None]

    if not results:
        return {"error": "بيانات غير كافية للنوافذ"}

    avg_wr   = round(np.mean([r['test_wr']     for r in results if r['test_wr']     is not None]), 1)
    avg_edge = round(np.mean([r['edge']         for r in results if r['edge']        is not None]), 2)
    avg_r5   = round(np.mean([r['test_avg_r5']  for r in results if r['test_avg_r5'] is not None]), 2)

    macro        = get_latest_macro()
    real_rate    = macro.get('real_interest_rate')
    macro_thresh = None
    macro_tip    = None

    # تعديل RSI threshold المُقترح بناءً على البيئة الكلية
    if real_rate is not None:
        if real_rate < -5:
            macro_thresh = 35   # بيئة محفّزة — يمكن شراء RSI أعلى قليلاً
            macro_tip = f'📉 فائدة حقيقية {real_rate:.1f}% — يُنصح بـ RSI≤35 في بيئة تيسيرية'
        elif real_rate < 0:
            macro_thresh = 30
            macro_tip = f'↔️  فائدة حقيقية {real_rate:.1f}% — RSI≤30 مناسب'
        else:
            macro_thresh = 25
            macro_tip = f'📈 فائدة حقيقية {real_rate:.1f}% — الاحتياط مطلوب، استخدم RSI≤25 فقط'

    return {
        'windows':       results,
        'avg_test_wr':   avg_wr,
        'avg_edge':      avg_edge,
        'avg_ret5':      avg_r5,
        'conclusion':    'مستقرة ✅' if avg_wr >= 55 and avg_edge >= 0.5 else 'تراجع في بعض النوافذ ⚠️',
        'macro_context': {
            'real_interest_rate':       round(real_rate, 2) if real_rate is not None else None,
            'recommended_rsi_threshold': macro_thresh,
            'tip':                       macro_tip or '📊 لا بيانات ماكرو',
            'usd_egp':                   round(macro.get('usd_egp', 0) or 0, 4),
            'inflation_pct':             round(macro.get('inflation', 0) or 0, 2),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: ml_signal  (Random Forest + HistGradientBoosting)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_ml_signal(params):
    """
    نموذج ML لاكتشاف إشارات الشراء على EGX.
    Features: RSI, ADX, BB_position, momentum, volume_ratio, day_of_week, stoch_k
    Target: هل العائد T+5 > 3%؟
    يستخدم TimeSeriesSplit لتفادي look-ahead bias.
    """
    from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import classification_report, precision_score, recall_score
    from sklearn.inspection import permutation_importance
    import warnings; warnings.filterwarnings('ignore')

    target_pct = float(params.get('target_pct', 3.0))
    n_splits   = int(params.get('n_splits', 5))

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)
    df['bar_time'] = pd.to_datetime(df['bar_time'], unit='s')
    df['dow'] = df['bar_time'].dt.dayofweek  # 0=Mon, 6=Sun

    # ── Feature Engineering ──────────────────────────────────────────────
    def feat(grp):
        c, h, l, v = grp['close'], grp['high'], grp['low'], grp['volume']

        # RSI(14)
        d  = c.diff()
        g  = d.clip(lower=0).ewm(com=13, min_periods=14).mean()
        lo = (-d).clip(lower=0).ewm(com=13, min_periods=14).mean()
        rsi= 100 - (100 / (1 + g / lo.replace(0, np.nan)))

        # RSI(7) — shorter
        g7  = d.clip(lower=0).ewm(com=6,  min_periods=7).mean()
        lo7 = (-d).clip(lower=0).ewm(com=6, min_periods=7).mean()
        rsi7= 100 - (100 / (1 + g7 / lo7.replace(0, np.nan)))

        # Bollinger Bands(20)
        sma20 = c.rolling(20).mean()
        std20 = c.rolling(20).std()
        bb_pos= (c - (sma20 - 2*std20)) / (4*std20).replace(0, np.nan)  # 0=lower, 1=upper

        # EMA positions
        ema20 = c.ewm(span=20).mean()
        ema50 = c.ewm(span=50).mean()
        above_ema20 = (c > ema20).astype(int)
        above_ema50 = (c > ema50).astype(int)

        # ADX(14) — simplified DX
        prev_c = c.shift(1)
        tr     = pd.concat([h-l, (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
        dm_p   = np.where((h-h.shift(1)) > (l.shift(1)-l), (h-h.shift(1)).clip(lower=0), 0)
        dm_m   = np.where((l.shift(1)-l) > (h-h.shift(1)), (l.shift(1)-l).clip(lower=0), 0)
        atr    = tr.ewm(com=13, min_periods=14).mean()
        di_p   = 100 * pd.Series(dm_p, index=grp.index).ewm(com=13, min_periods=14).mean() / atr.replace(0, np.nan)
        di_m   = 100 * pd.Series(dm_m, index=grp.index).ewm(com=13, min_periods=14).mean() / atr.replace(0, np.nan)
        dx     = 100 * (di_p-di_m).abs() / (di_p+di_m).replace(0, np.nan)
        adx    = dx.ewm(com=13, min_periods=14).mean()

        # Stochastic %K(14)
        lo14   = l.rolling(14).min()
        hi14   = h.rolling(14).max()
        stoch_k= 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)

        # Volume Ratio(20)
        vol_ma = v.rolling(20).mean()
        vol_r  = v / vol_ma.replace(0, np.nan)

        # Momentum
        mom5   = c / c.shift(5)  - 1
        mom10  = c / c.shift(10) - 1
        mom20  = c / c.shift(20) - 1

        # ATR normalized
        atr_pct= atr / c * 100

        grp = grp.copy()
        grp['f_rsi']        = rsi
        grp['f_rsi7']       = rsi7
        grp['f_bb_pos']     = bb_pos
        grp['f_adx']        = adx
        grp['f_stoch_k']    = stoch_k
        grp['f_vol_ratio']  = vol_r
        grp['f_mom5']       = mom5  * 100
        grp['f_mom10']      = mom10 * 100
        grp['f_mom20']      = mom20 * 100
        grp['f_atr_pct']    = atr_pct
        grp['f_above_ema20']= above_ema20
        grp['f_above_ema50']= above_ema50
        return grp

    print("[ml_signal] حساب الـ features...", file=sys.stderr)
    df = df.groupby('symbol', group_keys=False).apply(feat)

    # ── Target: T+5 return > target_pct% ────────────────────────────────
    df['ret5']  = df.groupby('symbol')['close'].transform(lambda x: x.shift(-5)/x-1)*100
    df['target']= (df['ret5'] > target_pct).astype(int)

    feature_cols = [c for c in df.columns if c.startswith('f_')]
    feature_cols += ['dow']

    df_ml = df[feature_cols + ['target', 'bar_time']].dropna()

    # ترتيب زمني (مهم لـ TimeSeriesSplit)
    df_ml = df_ml.sort_values('bar_time').reset_index(drop=True)

    X = df_ml[feature_cols].values
    y = df_ml['target'].values

    print(f"[ml_signal] {len(X):,} عينة | {y.mean()*100:.1f}% positive | {len(feature_cols)} features", file=sys.stderr)

    tscv = TimeSeriesSplit(n_splits=n_splits)

    # ── Random Forest ────────────────────────────────────────────────────
    print("[ml_signal] تدريب Random Forest...", file=sys.stderr)
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=30,
        class_weight='balanced', n_jobs=8, random_state=42
    )
    rf_scores = cross_val_score(rf, X, y, cv=tscv, scoring='precision', n_jobs=1)
    rf.fit(X, y)

    # Feature importance
    feat_imp = sorted(
        zip(feature_cols, rf.feature_importances_),
        key=lambda x: x[1], reverse=True
    )

    # ── HistGradientBoosting (LightGBM-equivalent) ───────────────────────
    print("[ml_signal] تدريب HistGradientBoosting...", file=sys.stderr)
    hgb = HistGradientBoostingClassifier(
        max_iter=200, max_depth=5, min_samples_leaf=30,
        random_state=42, class_weight='balanced'
    )
    hgb_scores = cross_val_score(hgb, X, y, cv=tscv, scoring='precision', n_jobs=1)
    hgb.fit(X, y)

    # ── أفضل ثلاثة مؤشرات مشتركة ────────────────────────────────────────
    top_features = [{'feature': f.replace('f_',''), 'importance': round(float(imp)*100, 1)}
                    for f, imp in feat_imp[:15]]

    # ── قاعدة بسيطة من ML ───────────────────────────────────────────────
    # RF predict على آخر 1000 صف
    last_signals = df_ml.tail(1000)
    X_last = last_signals[feature_cols].values
    rf_preds   = rf.predict(X_last)
    rf_proba   = rf.predict_proba(X_last)[:,1]

    # أسهم تتلقى إشارة شراء بثقة >= 65%
    high_conf = df_ml.tail(1000).copy()
    high_conf['rf_proba'] = rf_proba
    high_conf['rf_signal']= rf_preds
    high_conf = high_conf[high_conf['rf_proba'] >= 0.65]

    macro         = get_latest_macro()
    real_rate_ml  = macro.get('real_interest_rate')
    ml_macro_tip  = None
    if real_rate_ml is not None:
        if real_rate_ml < -5:
            ml_macro_tip = f'📉 فائدة حقيقية {real_rate_ml:.1f}% — النموذج في بيئة محفّزة، قد ترتفع الدقة'
        elif real_rate_ml > 5:
            ml_macro_tip = f'📈 فائدة حقيقية {real_rate_ml:.1f}% — راجع إشارات النموذج بحذر مع منافسة الودائع'

    return {
        'n_samples':      int(len(X)),
        'n_features':     len(feature_cols),
        'positive_rate':  round(float(y.mean()*100), 1),
        'target_def':     f'T+5 return > {target_pct}%',
        'random_forest': {
            'cv_precision_mean': round(float(rf_scores.mean()), 3),
            'cv_precision_std':  round(float(rf_scores.std()),  3),
            'cv_scores':         [round(float(s), 3) for s in rf_scores],
        },
        'hist_gradient_boosting': {
            'cv_precision_mean': round(float(hgb_scores.mean()), 3),
            'cv_precision_std':  round(float(hgb_scores.std()),  3),
            'cv_scores':         [round(float(s), 3) for s in hgb_scores],
        },
        'top_features':   top_features,
        'high_conf_signals': int(len(high_conf)),
        'interpretation': (
            'النموذج يكتشف إشارات بثقة عالية ✅'
            if float(rf_scores.mean()) >= 0.60 else
            'إشارات متوسطة — استخدم مع فلاتر إضافية ⚠️'
        ),
        'macro_context': {
            'real_interest_rate': round(real_rate_ml, 2) if real_rate_ml is not None else None,
            'usd_egp':            round(macro.get('usd_egp', 0) or 0, 4),
            'inflation_pct':      round(macro.get('inflation', 0) or 0, 2),
            'strategic_bias':     macro.get('strategic_bias', 'NEUTRAL'),
            'tip':                ml_macro_tip or '📊 لا بيانات ماكرو',
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: egx_patterns  (استراتيجيات خاصة بـ EGX)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_egx_patterns(params):
    """
    اكتشاف patterns خاصة بسوق EGX:
      1. Circuit Breaker Reversal  — سهم يصل ±10% غالباً يرتد
      2. Gap Fill                  — فجوات الفتح تُملأ
      3. Earnings Season Effect    — أبريل/يوليو/أكتوبر/يناير
      4. Thin Volume Reversal      — حجم أقل من 0.3x المتوسط
      5. Ramadan Effect            — ما قبل/بعد رمضان
      6. Day-of-Week               — أفضل/أسوأ أيام
    """
    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time
    """, con)
    con.close()

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)
    df['dt']      = pd.to_datetime(df['bar_time'], unit='s')
    df['dow']     = df['dt'].dt.dayofweek     # 0=Mon, 6=Sun
    df['dow_name']= df['dt'].dt.day_name()
    df['month']   = df['dt'].dt.month
    df['week']    = df['dt'].dt.isocalendar().week.astype(int)
    df['year']    = df['dt'].dt.year

    # العوائد
    df['daily_ret'] = df.groupby('symbol')['close'].transform(lambda x: x.pct_change()*100)
    for h in [1, 3, 5]:
        df[f'ret_{h}'] = df.groupby('symbol')['close'].transform(
            lambda x: x.shift(-h)/x - 1
        )*100

    # ── 1. Circuit Breaker Reversal ───────────────────────────────────────
    cb_thresh = float(params.get('cb_threshold', 9.0))
    cb_up     = df[df['daily_ret'] >=  cb_thresh].copy()
    cb_down   = df[df['daily_ret'] <= -cb_thresh].copy()

    # ⚠️ COUNTER-INTUITIVE DISCOVERY: CB Down + CLOSE BELOW OPEN → WR5=61.2% !!
    # (الانهيار الحاد + البيع مستمر حتى الإغلاق = البائعون لم ينتهوا بعد = ارتداد قادم)
    cb_down_close_up   = cb_down[cb_down['close'] >  cb_down['open']]
    cb_down_close_down = cb_down[cb_down['close'] <= cb_down['open']]

    def _cb_stats(sub, key):
        s = sub[['ret_1','ret_3','ret_5']].dropna()
        if len(s) == 0: return {'n': 0}
        return {
            'n':      int(len(s)),
            't1_avg': round(float(s['ret_1'].mean()), 2),
            't3_avg': round(float(s['ret_3'].mean()), 2),
            't5_avg': round(float(s['ret_5'].mean()), 2),
            't5_wr':  round(float((s['ret_5'] > 0).mean()*100), 1),
        }

    circuit_breaker = {
        'threshold_pct': cb_thresh,
        'up_limit':   _cb_stats(cb_up, 'up'),
        'down_limit':  _cb_stats(cb_down, 'down'),
        # إشارة المناقضة المُثبَتة (n=98, WR5=61.2%, avg5=+53%)
        'down_close_below_open': {
            **_cb_stats(cb_down_close_down, 'dcb'),
            'note': '🔥 COUNTER-INTUITIVE: CB-9%+يُغلق أسفل الفتح → WR5=61% (البائعون لم ينتهوا=ارتداد قادم)',
        },
        'down_close_above_open': {
            **_cb_stats(cb_down_close_up, 'dca'),
            'note': '⚠️  CB-9%+يُغلق فوق الفتح → WR5=21% فقط (يبدو أن الإنقاذ يفشل)',
        },
    }

    # ── 2. Gap Fill ───────────────────────────────────────────────────────
    df['prev_close'] = df.groupby('symbol')['close'].shift(1)
    df['gap_pct']    = (df['open'] - df['prev_close']) / df['prev_close'].replace(0, np.nan) * 100
    df['intraday_r'] = (df['close'] - df['open']) / df['open'].replace(0, np.nan) * 100

    gap_up   = df[(df['gap_pct'] >= 1.5)].dropna(subset=['gap_pct','intraday_r','ret_1','ret_5'])
    gap_down = df[(df['gap_pct'] <= -1.5)].dropna(subset=['gap_pct','intraday_r','ret_1','ret_5'])

    # هل تُملأ الفجوة؟ (intraday move عكسي للفجوة)
    gap_up_filled   = (gap_up['intraday_r']   < 0).mean() * 100
    gap_down_filled = (gap_down['intraday_r'] > 0).mean() * 100

    gap_fill = {
        'gap_up': {
            'n': int(len(gap_up)),
            'pct_filled_same_day': round(float(gap_up_filled), 1),
            't1_avg': round(float(gap_up['ret_1'].mean()), 2) if len(gap_up) > 0 else None,
            't5_wr':  round(float((gap_up['ret_5'] > 0).mean()*100), 1) if len(gap_up) > 0 else None,
            'note':   'فجوة صاعدة ≥1.5% — هل تُملأ؟',
        },
        'gap_down': {
            'n': int(len(gap_down)),
            'pct_filled_same_day': round(float(gap_down_filled), 1),
            't1_avg': round(float(gap_down['ret_1'].mean()), 2) if len(gap_down) > 0 else None,
            't5_wr':  round(float((gap_down['ret_5'] > 0).mean()*100), 1) if len(gap_down) > 0 else None,
            'note':   'فجوة هابطة ≥1.5% — فرصة انتعاش؟',
        },
    }

    # ── 3. Earnings Season Effect ─────────────────────────────────────────
    # أسابيع النتائج: أول 3 أسابيع من أبريل/يوليو/أكتوبر/يناير
    earnings_months = [1, 4, 7, 10]
    df['earnings_week'] = (df['month'].isin(earnings_months)) & (df['week'] % 52 <= 3)
    # تقريب: أسبوع 1-3 من الشهر = أيام 1-21
    df['earnings_week'] = df['month'].isin(earnings_months) & (df['dt'].dt.day <= 21)

    earn_season = df[df['earnings_week']][['ret_1','ret_3','ret_5']].dropna()
    normal      = df[~df['earnings_week']][['ret_1','ret_3','ret_5']].dropna()

    earnings_effect = {
        'earnings_n':   int(len(earn_season)),
        'normal_n':     int(len(normal)),
        'earnings_t5_avg': round(float(earn_season['ret_5'].mean()), 2),
        'normal_t5_avg':   round(float(normal['ret_5'].mean()), 2),
        'earnings_t5_wr':  round(float((earn_season['ret_5'] > 0).mean()*100), 1),
        'normal_t5_wr':    round(float((normal['ret_5'] > 0).mean()*100), 1),
        'edge':            round(float(earn_season['ret_5'].mean() - normal['ret_5'].mean()), 2),
        'note':            'يناير/أبريل/يوليو/أكتوبر — أول 3 أسابيع',
    }

    # ── 4. Thin Volume Reversal ───────────────────────────────────────────
    df['vol_ma20'] = df.groupby('symbol')['volume'].transform(lambda x: x.rolling(20).mean())
    df['vol_ratio']= df['volume'] / df['vol_ma20'].replace(0, np.nan)

    # RSI لفلتر oversold
    def rsi14(series):
        d  = series.diff()
        g  = d.clip(lower=0).ewm(com=13, min_periods=14).mean()
        lo = (-d).clip(lower=0).ewm(com=13, min_periods=14).mean()
        return 100 - (100/(1 + g/lo.replace(0, np.nan)))

    df['rsi'] = df.groupby('symbol')['close'].transform(rsi14)

    thin_oversold = df[(df['vol_ratio'] < 0.3) & (df['rsi'] < 40)][['ret_1','ret_3','ret_5']].dropna()
    thin_only     = df[(df['vol_ratio'] < 0.3)][['ret_1','ret_3','ret_5']].dropna()

    thin_volume = {
        'thin_oversold': {
            'n':     int(len(thin_oversold)),
            't5_avg': round(float(thin_oversold['ret_5'].mean()), 2) if len(thin_oversold) > 0 else None,
            't5_wr':  round(float((thin_oversold['ret_5'] > 0).mean()*100), 1) if len(thin_oversold) > 0 else None,
            'note':   'حجم <0.3x + RSI<40 — الإرهاق يسبق الارتداد',
        },
        'thin_only': {
            'n':     int(len(thin_only)),
            't5_avg': round(float(thin_only['ret_5'].mean()), 2) if len(thin_only) > 0 else None,
            't5_wr':  round(float((thin_only['ret_5'] > 0).mean()*100), 1) if len(thin_only) > 0 else None,
        },
    }

    # ── 5. Ramadan Effect ─────────────────────────────────────────────────
    # تواريخ رمضان (تقريبية)
    ramadan_ranges = [
        ('2021-04-13', '2021-05-12'),
        ('2022-04-02', '2022-05-01'),
        ('2023-03-23', '2023-04-20'),
        ('2024-03-11', '2024-04-09'),
        ('2025-03-01', '2025-03-29'),
        ('2026-02-18', '2026-03-19'),
    ]
    ramadan_mask = pd.Series(False, index=df.index)
    for start, end in ramadan_ranges:
        ramadan_mask |= (df['dt'] >= pd.Timestamp(start)) & (df['dt'] <= pd.Timestamp(end))

    # أسبوع بعد رمضان (عيد الفطر)
    post_ramadan_mask = pd.Series(False, index=df.index)
    for _, end in ramadan_ranges:
        end_dt = pd.Timestamp(end)
        post_ramadan_mask |= (df['dt'] > end_dt) & (df['dt'] <= end_dt + pd.Timedelta(days=14))

    ram_ret     = df[ramadan_mask][['ret_1','ret_3','ret_5']].dropna()
    post_ret    = df[post_ramadan_mask][['ret_1','ret_3','ret_5']].dropna()
    non_ram_ret = df[~ramadan_mask & ~post_ramadan_mask][['ret_1','ret_3','ret_5']].dropna()

    ramadan_effect = {
        'during_ramadan': {
            'n':     int(len(ram_ret)),
            't5_avg': round(float(ram_ret['ret_5'].mean()), 2)          if len(ram_ret) > 0 else None,
            't5_wr':  round(float((ram_ret['ret_5'] > 0).mean()*100), 1) if len(ram_ret) > 0 else None,
        },
        'post_ramadan_2weeks': {
            'n':     int(len(post_ret)),
            't5_avg': round(float(post_ret['ret_5'].mean()), 2)           if len(post_ret) > 0 else None,
            't5_wr':  round(float((post_ret['ret_5'] > 0).mean()*100), 1) if len(post_ret) > 0 else None,
        },
        'non_ramadan': {
            'n':     int(len(non_ram_ret)),
            't5_avg': round(float(non_ram_ret['ret_5'].mean()), 2)              if len(non_ram_ret) > 0 else None,
            't5_wr':  round(float((non_ram_ret['ret_5'] > 0).mean()*100), 1)    if len(non_ram_ret) > 0 else None,
        },
    }

    # ── 6. Day-of-Week ────────────────────────────────────────────────────
    dow_stats = []
    day_names = {0:'Monday',1:'Tuesday',2:'Wednesday',3:'Thursday',6:'Sunday'}
    for dow_val, day_name in day_names.items():
        sub = df[df['dow'] == dow_val][['ret_1','ret_3','ret_5']].dropna()
        if len(sub) < 50:
            continue
        dow_stats.append({
            'day':    day_name,
            'n':      int(len(sub)),
            't1_avg': round(float(sub['ret_1'].mean()), 2),
            't5_avg': round(float(sub['ret_5'].mean()), 2),
            't5_wr':  round(float((sub['ret_5'] > 0).mean()*100), 1),
        })
    dow_stats.sort(key=lambda x: x['t5_wr'], reverse=True)

    # ── 7. NEW: ATR Regime × RSI — متى يفشل RSI ──────────────────────
    df['atr14'] = df.groupby('symbol', group_keys=False).apply(
        lambda g: ((g['high'] - g['low']).rolling(14).mean() / g['close'].replace(0, np.nan) * 100)
    ).values
    atr_regime_results = {}
    for (lo, hi, label) in [(0, 1, 'LOW'), (1, 2, 'MED'), (2, 3, 'HIGH'), (3, 99, 'EXTREME')]:
        sub = df[(df['rsi'] <= 30) & (df['atr14'] >= lo) & (df['atr14'] < hi)][['ret_5']].dropna()
        if len(sub) < 10:
            continue
        atr_regime_results[label] = {
            'n':     int(len(sub)),
            't5_avg': round(float(sub['ret_5'].mean()), 2),
            't5_wr':  round(float((sub['ret_5'] > 3).mean() * 100), 1),
            'note':   f'ATR {lo}-{hi}%: RSI≤30 performance',
        }

    # ── 8. NEW: Panic Gap + RSI — الانهيار الحقيقي ───────────────────
    panic_levels = {}
    for (thresh, label) in [(-3, 'gap_3pct'), (-5, 'gap_5pct'), (-7, 'gap_7pct')]:
        base = df[(df['gap_pct'] <= thresh) & (df['rsi'] <= 35)][['ret_3','ret_5']].dropna()
        with_vol = df[(df['gap_pct'] <= thresh) & (df['rsi'] <= 35) & (df['vol_ratio'] >= 1.0)][['ret_3','ret_5']].dropna()
        if len(base) < 5:
            continue
        panic_levels[label] = {
            'n_all':      int(len(base)),
            't5_avg_all': round(float(base['ret_5'].mean()), 2),
            't5_wr_all':  round(float((base['ret_5'] > 3).mean() * 100), 1),
            'n_vol':      int(len(with_vol)),
            't5_avg_vol': round(float(with_vol['ret_5'].mean()), 2) if len(with_vol) > 0 else None,
            't5_wr_vol':  round(float((with_vol['ret_5'] > 3).mean() * 100), 1) if len(with_vol) > 0 else None,
            'note': f'gap≤{thresh}% + RSI≤35 (all / +vol≥1x)',
        }

    # ── 9. NEW: RSI + Momentum Divergence (RSI≤30 + mom5≤-5%) ─────────
    mom_reversal = df[(df['rsi'] <= 30) & (df['gap_pct'].shift(1).fillna(0) + df['gap_pct'].fillna(0) <= -5)][['ret_5']].dropna()
    mom_oversold = df[(df['rsi'] <= 30)].copy()
    mom_oversold['mom5'] = mom_oversold.groupby('symbol')['close'].transform(lambda x: x.pct_change(5) * 100)
    mom_rsi_combo = mom_oversold[mom_oversold['mom5'] <= -5][['ret_5']].dropna()
    momentum_reversal = {
        'rsi30_mom5_neg5': {
            'n':      int(len(mom_rsi_combo)),
            't5_avg': round(float(mom_rsi_combo['ret_5'].mean()), 2) if len(mom_rsi_combo) > 0 else None,
            't5_wr':  round(float((mom_rsi_combo['ret_5'] > 3).mean() * 100), 1) if len(mom_rsi_combo) > 0 else None,
            'note':   'RSI≤30 + 5-day momentum ≤ -5% — momentum exhaustion reversal',
        }
    }

    # ── 10. NEW: Market Regime Impact on RSI signals ──────────────────
    mkt_mom5 = df.groupby('bar_time')['close'].transform(lambda x: x.pct_change(5) * 100)
    df['mkt_mom5'] = mkt_mom5
    mkt_regime_results = {}
    for (lo, hi, label) in [(-999,-5,'CRASH'),(-5,-2,'DOWN'),(-2,0,'FLAT_NEG'),(0,2,'FLAT_POS'),(2,5,'UP'),(5,999,'SURGE')]:
        sub = df[(df['rsi'] <= 30) & (df['mkt_mom5'] >= lo) & (df['mkt_mom5'] < hi)][['ret_5']].dropna()
        if len(sub) < 10:
            continue
        mkt_regime_results[label] = {
            'n':     int(len(sub)),
            't5_avg': round(float(sub['ret_5'].mean()), 2),
            't5_wr':  round(float((sub['ret_5'] > 3).mean() * 100), 1),
            'signal_quality': ('🟢 STRONG' if (sub['ret_5'] > 3).mean() > 0.45
                               else '🟡 MODERATE' if (sub['ret_5'] > 3).mean() > 0.30
                               else '🔴 WEAK'),
        }

    return {
        'circuit_breaker':   circuit_breaker,
        'gap_fill':          gap_fill,
        'earnings_effect':   earnings_effect,
        'thin_volume':       thin_volume,
        'ramadan_effect':    ramadan_effect,
        'day_of_week':       dow_stats,
        'atr_regime':        atr_regime_results,
        'panic_gap_rsi':     panic_levels,
        'momentum_reversal': momentum_reversal,
        'market_regime':     mkt_regime_results,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: sector_rotation  — Sector Rotation Alpha (Leading/Lagging/Improving/Declining)
# ═══════════════════════════════════════════════════════════════════════════

# خريطة القطاعات (مطابقة لـ EGX_SECTORS في index.js)
SECTOR_MAP = {
    'COMI':'Banking','HDBK':'Banking','CIEB':'Banking','QNBE':'Banking',
    'SAIB':'Banking','ADIB':'Banking','AIDC':'Finance',
    'TMGH':'RealEstate','PHDC':'RealEstate','OCDI':'RealEstate','HELI':'RealEstate',
    'CLHO':'RealEstate','ORHD':'RealEstate','RREI':'RealEstate','MASR':'RealEstate',
    'ETEL':'Telecom','MENA':'Telecom','VALU':'Telecom',
    'POUL':'Food','JUFO':'Food','SUGR':'Food','DCRC':'Food','AMIA':'Food','NAHO':'Food',
    'ARCC':'Construction','ACGC':'Construction','IRON':'Construction',
    'SWDY':'Construction','ELKA':'Construction','EGCH':'Construction',
    'AMOC':'Chemicals','SKPC':'Chemicals','KZPC':'Chemicals','COPR':'Chemicals',
    'ISPH':'Pharma','PHAR':'Pharma','OCPH':'Pharma','AXPH':'Pharma','MIPH':'Pharma',
    'SPIN':'Textile','ORWE':'Textile','ARVA':'Textile',
    'MCRO':'Technology','RTVC':'Technology','MKIT':'Technology','DGTZ':'Technology',
    'EFID':'Finance','EFIC':'Finance','EFIH':'Finance','INFI':'Finance','ACAP':'Finance',
    'TAQA':'Energy','EGAS':'Energy',
    'CIRA':'Retail','RAYA':'Retail','ORAS':'Retail',
    'ELWA':'Media','PHTV':'Media',
    'ABUK':'Diversified','GBCO':'Diversified','EAST':'Diversified','AREH':'Diversified',
    'TMKC':'RealEstate',
}

def cmd_sector_rotation(params):
    """
    Sector Rotation Alpha — يحدّد القطاعات الرائدة وتلك المتأخرة.
    يحسب:
      - Relative Strength كل قطاع (momentum vs. market avg)
      - تسارع/تباطؤ الـ momentum (هل يتحسن؟)
      - تصنيف: LEADING | IMPROVING | LAGGING | DECLINING
    يُساعد في: rotation strategy، sector ETF timing، تجنب القطاعات الهابطة.
    """
    con = get_connection()
    ic  = pd.read_sql("""
        WITH latest AS (SELECT symbol, MAX(bar_date) d FROM indicators_cache GROUP BY symbol)
        SELECT ic.symbol, ic.momentum_5d, ic.momentum_10d, ic.momentum_20d,
               ic.rsi14, ic.adx14, ic.vol_ratio_20, ic.bar_date
        FROM indicators_cache ic JOIN latest l ON ic.symbol=l.symbol AND ic.bar_date=l.d
        WHERE ic.momentum_5d IS NOT NULL
    """, con)
    con.close()

    if ic.empty:
        return {"error": "لا بيانات في indicators_cache"}

    # ── تعيين القطاعات ───────────────────────────────────────────────────
    ic['sector'] = ic['symbol'].map(SECTOR_MAP).fillna('Other')

    # ── حساب مقاييس السوق الكلية (المعيار) ──────────────────────────────
    market_mom5  = float(ic['momentum_5d'].mean())
    market_mom10 = float(ic['momentum_10d'].mean())
    market_mom20 = float(ic['momentum_20d'].mean())

    # ── تجميع بالقطاع ────────────────────────────────────────────────────
    def sector_agg(g):
        return pd.Series({
            'n_stocks':        len(g),
            'avg_mom5':        g['momentum_5d'].mean(),
            'avg_mom10':       g['momentum_10d'].mean(),
            'avg_mom20':       g['momentum_20d'].mean(),
            'avg_rsi':         g['rsi14'].mean(),
            'avg_adx':         g['adx14'].mean(),
            'pct_positive_5d': (g['momentum_5d'] > 0).mean() * 100,
            'avg_vol_ratio':   g['vol_ratio_20'].mean(),
        })

    sectors = ic.groupby('sector').apply(sector_agg).reset_index()
    sectors = sectors[sectors['n_stocks'] >= 2].copy()  # فلتر القطاعات الصغيرة

    # ── Relative Strength (momentum vs. market) ───────────────────────────
    sectors['rs5']  = sectors['avg_mom5']  - market_mom5
    sectors['rs10'] = sectors['avg_mom10'] - market_mom10
    sectors['rs20'] = sectors['avg_mom20'] - market_mom20

    # ── Momentum Acceleration (هل الـ momentum يتسارع؟) ──────────────────
    # إذا mom5 > mom10 → momentum يتسارع (تحسين)
    sectors['momentum_accel'] = sectors['avg_mom5'] - sectors['avg_mom10']

    # ── Composite Rotation Score ──────────────────────────────────────────
    # RS5 × 50% + RS10 × 30% + Acceleration × 20%
    sectors['rotation_score'] = (
        sectors['rs5']            * 0.50 +
        sectors['rs10']           * 0.30 +
        sectors['momentum_accel'] * 0.20
    )

    # ── Classify ─────────────────────────────────────────────────────────
    def classify_sector(row):
        score = row['rotation_score']
        accel = row['momentum_accel']
        rs5   = row['rs5']

        if   rs5 > 2 and accel > 0:   return 'LEADING'      # قوي ويتسارع ✅
        elif rs5 > 0 and accel > 0:   return 'IMPROVING'    # يتحسن ⬆️
        elif rs5 > 0 and accel <= 0:  return 'WEAKENING'    # قوي لكن يتباطأ ⚠️
        elif rs5 <= 0 and accel > 0:  return 'RECOVERING'   # كان ضعيف لكن يتحسن 🔄
        else:                          return 'LAGGING'      # ضعيف ❌

    sectors['classification'] = sectors.apply(classify_sector, axis=1)
    sectors = sectors.sort_values('rotation_score', ascending=False)

    # ── أفضل وأسوأ أسهم في كل قطاع رائد ────────────────────────────────
    leading_sectors = sectors[sectors['classification'].isin(['LEADING', 'IMPROVING'])]['sector'].tolist()
    best_in_sector  = {}
    for sec in leading_sectors[:5]:
        top = ic[ic['sector'] == sec].nlargest(3, 'momentum_5d')
        best_in_sector[sec] = top[['symbol', 'momentum_5d', 'rsi14']].round(1).to_dict('records')

    def fmt_sec(r):
        return {
            'sector':         r['sector'],
            'classification': r['classification'],
            'rotation_score': round(float(r['rotation_score']), 2),
            'avg_mom5':       round(float(r['avg_mom5']),  2),
            'avg_mom10':      round(float(r['avg_mom10']), 2),
            'rs_vs_market':   round(float(r['rs5']),       2),
            'momentum_accel': round(float(r['momentum_accel']), 2),
            'avg_rsi':        round(float(r['avg_rsi']),   1),
            'pct_positive':   round(float(r['pct_positive_5d']), 1),
            'n_stocks':       int(r['n_stocks']),
        }

    return {
        'market_avg_mom5':  round(market_mom5, 2),
        'market_avg_mom10': round(market_mom10, 2),
        'sector_ranking':   [fmt_sec(r) for _, r in sectors.iterrows()],
        'leading_sectors':  [s for s in sectors[sectors['classification']=='LEADING']['sector'].tolist()],
        'improving_sectors':[s for s in sectors[sectors['classification']=='IMPROVING']['sector'].tolist()],
        'lagging_sectors':  [s for s in sectors[sectors['classification']=='LAGGING']['sector'].tolist()],
        'best_in_leading':  best_in_sector,
        'rotation_insight': (
            f"🔥 القطاعات الرائدة: {', '.join(sectors[sectors['classification']=='LEADING']['sector'].tolist()[:3]) or 'لا يوجد'} | "
            f"⬆️ المتحسنة: {', '.join(sectors[sectors['classification']=='IMPROVING']['sector'].tolist()[:3]) or 'لا يوجد'} | "
            f"❌ المتأخرة: {', '.join(sectors[sectors['classification']=='LAGGING']['sector'].tolist()[:3]) or 'لا يوجد'}"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: pairs_trading  — Cointegration-based Pairs (Engle-Granger)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_pairs_trading(params):
    """
    يكتشف أزواج الأسهم المترابطة (Cointegrated) على EGX باستخدام Engle-Granger test.
    خطوات العمل:
      1. تصفية الأسهم الأكثر سيولة (last 120 يوم)
      2. حساب Pearson correlation matrix
      3. اختبار Cointegration على الأزواج ذات correlation > 0.65
      4. حساب spread z-score للأزواج المتكاملة
      5. إشارة: z < -2 → Long pair | z > +2 → Short pair
    """
    from statsmodels.tsa.stattools import coint
    import warnings; warnings.filterwarnings('ignore')

    min_bars     = int(params.get('min_bars',    120))
    min_corr     = float(params.get('min_corr',  0.65))
    max_pairs    = int(params.get('max_pairs',   30))
    coint_pval   = float(params.get('coint_pval', 0.10))   # نسبة الرفض المقبولة

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, close
        FROM ohlcv_history
        ORDER BY symbol, bar_time
    """, con)
    con.close()

    df = df.sort_values(['symbol', 'bar_time'])
    df['bar_time'] = pd.to_datetime(df['bar_time'], unit='s')

    # آخر 120 يوم تداول
    df = df.groupby('symbol').tail(min_bars + 10)

    # أسهم بها >= min_bars شمعة
    bar_counts = df.groupby('symbol').size()
    valid_syms = bar_counts[bar_counts >= min_bars].index.tolist()
    df = df[df['symbol'].isin(valid_syms)]

    # Pivot: columns = symbols, rows = dates
    pivot_raw = df.pivot(index='bar_time', columns='symbol', values='close')
    # احتفظ بالأسهم التي لديها على الأقل min_bars قراءة
    pivot = pivot_raw.loc[:, pivot_raw.count() >= min_bars]
    # Forward-fill فجوات قصيرة (holidays/circuit breaker)
    pivot = pivot.ffill().bfill()
    # بعد الـ fill، احذف الأسهم التي لا تزال بها NaN
    pivot = pivot.dropna(axis=1)
    pivot = pivot.iloc[-min_bars:]  # أحدث min_bars صف

    print(f"[pairs] {pivot.shape[1]} أسهم × {pivot.shape[0]} شمعة...", file=sys.stderr)

    if pivot.shape[1] < 4:
        return {"error": f"عدد الأسهم غير كافٍ ({pivot.shape[1]}) — يحتاج >= 4"}

    # ── Correlation Matrix ────────────────────────────────────────────────
    # استخدم log returns للـ correlation
    log_ret = np.log(pivot / pivot.shift(1)).dropna()
    corr    = log_ret.corr()
    symbols = list(pivot.columns)

    # ── إيجاد الأزواج المرشحة ─────────────────────────────────────────────
    candidates = []
    for i in range(len(symbols)):
        for j in range(i+1, len(symbols)):
            c = corr.iloc[i, j]
            if c >= min_corr:
                candidates.append((symbols[i], symbols[j], c))

    print(f"[pairs] {len(candidates)} زوج مرشح بـ corr >= {min_corr}...", file=sys.stderr)

    if not candidates:
        return {
            "error": f"لا أزواج بـ correlation >= {min_corr}",
            "hint": "جرب تخفيض min_corr إلى 0.55",
        }

    # ── Cointegration Test (Engle-Granger) ───────────────────────────────
    cointegrated_pairs = []
    for s1, s2, corr_val in candidates[:150]:  # cap لتجنب timeout
        try:
            p1 = pivot[s1].values
            p2 = pivot[s2].values
            _, pval, _ = coint(p1, p2)

            if pval <= coint_pval:
                # حساب Spread و Z-score
                # نسبة التحوط = beta من OLS
                from numpy.linalg import lstsq
                X = np.column_stack([p2, np.ones(len(p2))])
                beta, _, _, _ = lstsq(X, p1, rcond=None)
                hedge_ratio = beta[0]

                spread    = p1 - hedge_ratio * p2
                spread_s  = pd.Series(spread)
                mean_spr  = spread_s.rolling(20).mean().iloc[-1]
                std_spr   = spread_s.rolling(20).std().iloc[-1]
                z_score   = (spread[-1] - mean_spr) / (std_spr if std_spr > 0 else 1)

                # Signal
                if   z_score < -2.0: signal = 'LONG_SPREAD'    # buy s1, sell s2
                elif z_score >  2.0: signal = 'SHORT_SPREAD'   # sell s1, buy s2
                elif z_score < -1.5: signal = 'APPROACHING_BUY'
                elif z_score >  1.5: signal = 'APPROACHING_SELL'
                else:                signal = 'NEUTRAL'

                # نسبة القطاعات
                sec1 = SECTOR_MAP.get(s1, 'Other')
                sec2 = SECTOR_MAP.get(s2, 'Other')

                cointegrated_pairs.append({
                    'pair':        f"{s1}/{s2}",
                    's1':          s1, 's2': s2,
                    'sector1':     sec1, 'sector2': sec2,
                    'correlation': round(float(corr_val), 3),
                    'coint_pval':  round(float(pval), 4),
                    'hedge_ratio': round(float(hedge_ratio), 3),
                    'z_score':     round(float(z_score), 2),
                    'signal':      signal,
                    'actionable':  bool(abs(z_score) >= 1.5),  # convert numpy bool_
                })

        except Exception:
            continue

    # ترتيب: الأزواج ذات إشارة أولاً، ثم حسب coint_pval
    cointegrated_pairs.sort(key=lambda x: (not x['actionable'], x['coint_pval']))

    actionable = [p for p in cointegrated_pairs if p['actionable']]
    neutral    = [p for p in cointegrated_pairs if not p['actionable']]

    return {
        'params': {
            'min_bars': min_bars, 'min_corr': min_corr,
            'coint_pval_threshold': coint_pval,
        },
        'candidates_screened': len(candidates),
        'cointegrated_count':  len(cointegrated_pairs),
        'actionable_pairs':    actionable[:max_pairs],
        'neutral_pairs':       neutral[:10],
        'summary': (
            f"🎯 {len(actionable)} أزواج بإشارة نشطة من {len(cointegrated_pairs)} متكاملة"
        ),
        'strategy_note': (
            'Pairs Trading: z < -2 = Long spread (شراء S1 + بيع S2) | '
            'z > +2 = Short spread (بيع S1 + شراء S2) | '
            'خروج عند z → 0'
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: shap_analysis  — SHAP values + advanced features (Garman-Klass, Amihud, ATR rank)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_shap_analysis(params):
    """
    SHAP (SHapley Additive exPlanations) لنموذج Random Forest.
    يضيف features متقدمة غير موجودة في ml_signal:
      - Garman-Klass Volatility  (أدق من std)
      - Amihud Illiquidity Ratio (سيولة حقيقية)
      - ATR Percentile Rank      (رتبة التذبذب)
      - Bollinger Band Width     (squeeze detector)
      - Month                    (موسمية)
    يُرجع mean|SHAP| بدلاً من impurity-based importance (أدق بكثير).
    """
    import shap
    from sklearn.ensemble import RandomForestClassifier

    target_pct  = float(params.get('target_pct', 3.0))
    sample_size = int(params.get('sample_size', 8000))

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)
    df['bar_time'] = pd.to_datetime(df['bar_time'], unit='s')
    df['dow']   = df['bar_time'].dt.dayofweek
    df['month'] = df['bar_time'].dt.month

    def feat_advanced(grp):
        c, h, l, v, o = grp['close'], grp['high'], grp['low'], grp['volume'], grp['open']

        # RSI(14)
        d  = c.diff()
        g  = d.clip(lower=0).ewm(com=13, min_periods=14).mean()
        lo = (-d).clip(lower=0).ewm(com=13, min_periods=14).mean()
        rsi = 100 - (100 / (1 + g / lo.replace(0, np.nan)))

        # ATR + ADX
        prev_c = c.shift(1)
        tr     = pd.concat([h-l, (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
        atr    = tr.ewm(com=13, min_periods=14).mean()
        atr_pct = atr / c * 100

        dm_p = np.where((h-h.shift(1)) > (l.shift(1)-l), (h-h.shift(1)).clip(lower=0), 0)
        dm_m = np.where((l.shift(1)-l) > (h-h.shift(1)), (l.shift(1)-l).clip(lower=0), 0)
        di_p = 100 * pd.Series(dm_p, index=grp.index).ewm(com=13).mean() / atr.replace(0, np.nan)
        di_m = 100 * pd.Series(dm_m, index=grp.index).ewm(com=13).mean() / atr.replace(0, np.nan)
        dx   = 100 * (di_p-di_m).abs() / (di_p+di_m).replace(0, np.nan)
        adx  = dx.ewm(com=13).mean()

        # BB
        sma20   = c.rolling(20).mean()
        std20   = c.rolling(20).std()
        bb_pos  = (c - (sma20 - 2*std20)) / (4*std20).replace(0, np.nan)
        bb_width = (4*std20) / sma20.replace(0, np.nan) * 100   # squeeze → low bb_width

        # ATR Percentile Rank (60d rolling)
        atr_rank = atr_pct.rolling(60).rank(pct=True) * 100

        # Garman-Klass Volatility (20d mean)
        log_hl = np.log((h / l.replace(0, np.nan)).replace(0, np.nan))
        log_co = np.log((c / o.replace(0, np.nan)).replace(0, np.nan))
        gk_var = 0.5 * log_hl**2 - (2*np.log(2)-1) * log_co**2
        gk_vol = gk_var.rolling(20).mean() * 252  # annualised

        # Amihud Illiquidity (20d rolling average × 1e6)
        ret_abs = c.pct_change().abs()
        value   = v * c
        amihud  = (ret_abs / value.replace(0, np.nan)).rolling(20).mean() * 1e6

        # Volume ratio & momentum
        vol_r  = v / v.rolling(20).mean().replace(0, np.nan)
        mom5   = (c / c.shift(5)  - 1) * 100
        mom10  = (c / c.shift(10) - 1) * 100
        mom20  = (c / c.shift(20) - 1) * 100

        grp = grp.copy()
        grp['f_rsi']       = rsi
        grp['f_adx']       = adx
        grp['f_atr_pct']   = atr_pct
        grp['f_atr_rank']  = atr_rank
        grp['f_gk_vol']    = gk_vol
        grp['f_amihud']    = amihud
        grp['f_bb_pos']    = bb_pos
        grp['f_bb_width']  = bb_width
        grp['f_vol_ratio'] = vol_r
        grp['f_mom5']      = mom5
        grp['f_mom10']     = mom10
        grp['f_mom20']     = mom20
        return grp

    print("[shap] Computing advanced features...", file=sys.stderr)
    df = df.groupby('symbol', group_keys=False).apply(feat_advanced)
    df['ret5']   = df.groupby('symbol')['close'].transform(lambda x: x.shift(-5)/x-1) * 100
    df['target'] = (df['ret5'] > target_pct).astype(int)

    feature_cols = [c for c in df.columns if c.startswith('f_')]
    feature_cols += ['dow', 'month']

    df_ml = df[feature_cols + ['target']].dropna()
    df_ml = df_ml.sort_index().tail(sample_size)

    X = df_ml[feature_cols].values
    y = df_ml['target'].values

    print(f"[shap] Training RF on {len(X)} samples, {len(feature_cols)} features...", file=sys.stderr)
    rf = RandomForestClassifier(
        n_estimators=150, max_depth=7, min_samples_leaf=25,
        class_weight='balanced', n_jobs=8, random_state=42
    )
    rf.fit(X, y)

    print("[shap] Computing SHAP values (TreeExplainer)...", file=sys.stderr)
    explainer   = shap.TreeExplainer(rf)
    X_shap      = X[:min(2000, len(X))]
    shap_values = explainer.shap_values(X_shap)

    # SHAP >= 0.40: returns ndarray(n_samples, n_features, n_classes) for RF
    # SHAP <  0.40: returns list of [class0_arr, class1_arr]
    sv_arr = np.array(shap_values)
    if sv_arr.ndim == 3:
        # New format: (n_samples, n_features, n_classes) → take class 1
        sv = sv_arr[:, :, 1]
    elif isinstance(shap_values, list):
        # Old format: list[class] → shape (n_samples, n_features) each
        sv = np.array(shap_values[1])
    else:
        sv = sv_arr  # fallback

    mean_abs = np.abs(sv).mean(axis=0)  # shape: (n_features,)
    total    = mean_abs.sum()

    FEAT_DESC = {
        'rsi':       'RSI(14) — oversold momentum',
        'adx':       'قوة الاتجاه ADX',
        'atr_pct':   'ATR% — تذبذب نسبي',
        'atr_rank':  'ATR Percentile Rank (60d)',
        'gk_vol':    'Garman-Klass Vol — دقة عالية للتذبذب',
        'amihud':    'Amihud Illiquidity — سيولة حقيقية',
        'bb_pos':    'Bollinger Band Position',
        'bb_width':  'BB Width — Squeeze Detector',
        'vol_ratio': 'Volume Ratio vs. 20d avg',
        'mom5':      'Momentum 5 أيام',
        'mom10':     'Momentum 10 أيام',
        'mom20':     'Momentum 20 أيام',
        'dow':       'Day of Week',
        'month':     'Month (موسمية)',
    }

    shap_table = sorted(
        [{'feature':     f.replace('f_', ''),
          'mean_abs_shap': round(float(v), 4),
          'contribution_pct': round(float(v)/float(total)*100, 1),
          'description':  FEAT_DESC.get(f.replace('f_',''), f)}
         for f, v in zip(feature_cols, mean_abs)],
        key=lambda x: x['mean_abs_shap'], reverse=True
    )

    top3 = shap_table[:3]
    insight = (
        '⚡ التذبذب (ATR/GK) يتصدر — استهدف أسهم ATR_rank 30-60% مع RSI منخفض'
        if top3[0]['feature'] in ('atr_pct', 'atr_rank', 'gk_vol') else
        '📉 RSI يتصدر — الاستراتيجية الحالية RSI+ADX صحيحة'
        if top3[0]['feature'] == 'rsi' else
        f"🔍 {top3[0]['feature']} هو المحرك الأكبر — راجع الاستراتيجية"
    )

    return {
        'n_samples':      int(len(X)),
        'n_features':     len(feature_cols),
        'positive_rate':  round(float(y.mean()*100), 1),
        'target_def':     f'T+5 > {target_pct}%',
        'shap_importance': shap_table,
        'top_3_features': [f"{r['feature']} ({r['contribution_pct']}%)" for r in top3],
        'key_insight':    insight,
        'vs_simple_ml':   'SHAP أدق من feature_importances_ (impurity bias مُصحَّح)',
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: regime_detection  — تحديد regime السوق (Trending/Ranging/HighVol)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_regime_detection(params):
    """
    يُحدّد regime كل سهم وregime السوق الكلي بناءً على:
      - ADX(14):  >25 trending, <20 ranging
      - EMA20 slope: up/down trend direction
      - ATR Percentile Rank: >80 = high vol
    Regimes: TRENDING_UP | TRENDING_DOWN | RANGING | HIGH_VOL | NEUTRAL
    """
    min_bars = int(params.get('min_bars', 30))

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, high, low, close
        FROM ohlcv_history ORDER BY symbol, bar_time
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات"}

    df = df.sort_values(['symbol', 'bar_time']).reset_index(drop=True)
    df['bar_time'] = pd.to_datetime(df['bar_time'], unit='s')

    def calc_regime(grp):
        if len(grp) < min_bars:
            return grp
        c, h, l = grp['close'], grp['high'], grp['low']

        # ADX
        prev_c = c.shift(1)
        tr     = pd.concat([h-l, (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
        atr    = tr.ewm(com=13, min_periods=14).mean()
        dm_p   = np.where((h-h.shift(1)) > (l.shift(1)-l), (h-h.shift(1)).clip(lower=0), 0)
        dm_m   = np.where((l.shift(1)-l) > (h-h.shift(1)), (l.shift(1)-l).clip(lower=0), 0)
        di_p   = 100 * pd.Series(dm_p, index=grp.index).ewm(com=13).mean() / atr.replace(0, np.nan)
        di_m   = 100 * pd.Series(dm_m, index=grp.index).ewm(com=13).mean() / atr.replace(0, np.nan)
        dx     = 100 * (di_p-di_m).abs() / (di_p+di_m).replace(0, np.nan)
        adx    = dx.ewm(com=13).mean()

        # EMA slope (5-bar)
        ema20 = c.ewm(span=20).mean()
        ema_slope = (ema20 - ema20.shift(5)) / ema20.shift(5) * 100

        # ATR rank (60d percentile)
        atr_pct  = atr / c * 100
        atr_rank = atr_pct.rolling(60).rank(pct=True) * 100

        grp = grp.copy()
        grp['adx']       = adx
        grp['ema_slope'] = ema_slope
        grp['atr_rank']  = atr_rank
        return grp

    print("[regime] Computing ADX + ATR rank per symbol...", file=sys.stderr)
    df = df.groupby('symbol', group_keys=False).apply(calc_regime)

    latest = df.groupby('symbol').last().reset_index()
    latest = latest.dropna(subset=['adx', 'ema_slope', 'atr_rank'])

    def classify(row):
        adx       = row['adx']
        slope     = row['ema_slope']
        atr_rank  = row['atr_rank']
        if atr_rank  > 80:                       return 'HIGH_VOL'
        if adx > 25 and slope >  0.5:            return 'TRENDING_UP'
        if adx > 25 and slope < -0.5:            return 'TRENDING_DOWN'
        if adx < 20:                             return 'RANGING'
        return 'NEUTRAL'

    latest['regime'] = latest.apply(classify, axis=1)

    counts = latest['regime'].value_counts().to_dict()
    total  = len(latest)

    by_regime = {}
    for r in ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'HIGH_VOL', 'NEUTRAL']:
        syms = latest[latest['regime'] == r]['symbol'].tolist()
        by_regime[r] = {'count': len(syms), 'symbols': syms[:20]}  # cap at 20

    trending_n = counts.get('TRENDING_UP', 0) + counts.get('TRENDING_DOWN', 0)
    high_vol_n = counts.get('HIGH_VOL', 0)
    ranging_n  = counts.get('RANGING', 0)

    if high_vol_n / total > 0.35:
        market_regime = 'HIGH_VOL'
        rec = '🚨 تذبذب عالٍ — خفف الأحجام 50%، stop loss أضيق'
    elif trending_n / total > 0.45:
        market_regime = 'TRENDING'
        up_n = counts.get('TRENDING_UP', 0)
        dn_n = counts.get('TRENDING_DOWN', 0)
        bias = 'صاعد' if up_n > dn_n else 'هابط'
        rec  = f'✅ سوق trending {bias} ({trending_n}/{total}) — استراتيجيات Trend-Following فعّالة'
    elif ranging_n / total > 0.45:
        market_regime = 'RANGING'
        rec = '⚠️ سوق ranging — ابحث عن Breakout setups، تجنب Trend-Following'
    else:
        market_regime = 'MIXED'
        rec = '📊 سوق مختلط — انتقائية عالية، ركز على أقوى الإشارات فقط'

    # ── Regime Transition Matrix (احتمالية الانتقال بين regimes) ─────────
    # نحتاج series تاريخية من الـ regime لكل يوم — نستخدم market aggregate
    # نبني daily market breadth: pct TRENDING_UP per day
    all_regimes_list = ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'HIGH_VOL', 'NEUTRAL']

    # بسيطة: استخدم latest.regime كـ state snapshot
    # لـ transition matrix نحتاج time series → نستخدم نافذة متحركة من OHLCV أبسط
    # نُبسّط: نحسب regime لكل سهم في T و T-5 ونبني transition counts

    # نجلب آخر 10 bars per symbol لتقدير transitions
    con2 = get_connection()
    df_hist = pd.read_sql("""
        SELECT symbol, bar_time, high, low, close
        FROM ohlcv_history
        WHERE bar_time >= (SELECT MAX(bar_time) - 90*86400 FROM ohlcv_history)
        ORDER BY symbol, bar_time
    """, con2)
    con2.close()

    transition_matrix = {}
    if not df_hist.empty and len(df_hist) > 100:
        try:
            df_hist = df_hist.sort_values(['symbol','bar_time'])
            df_hist['bar_time'] = pd.to_datetime(df_hist['bar_time'], unit='s')

            def quick_regime(grp):
                if len(grp) < 15: return grp
                c, h, l = grp['close'], grp['high'], grp['low']
                prev_c = c.shift(1)
                tr = pd.concat([h-l,(h-prev_c).abs(),(l-prev_c).abs()],axis=1).max(axis=1)
                atr = tr.ewm(com=13).mean()
                dm_p = np.where((h-h.shift(1))>(l.shift(1)-l),(h-h.shift(1)).clip(lower=0),0)
                dm_m = np.where((l.shift(1)-l)>(h-h.shift(1)),(l.shift(1)-l).clip(lower=0),0)
                di_p = 100*pd.Series(dm_p,index=grp.index).ewm(com=13).mean()/atr.replace(0,np.nan)
                di_m = 100*pd.Series(dm_m,index=grp.index).ewm(com=13).mean()/atr.replace(0,np.nan)
                adx  = (100*(di_p-di_m).abs()/(di_p+di_m).replace(0,np.nan)).ewm(com=13).mean()
                ema20 = c.ewm(span=20).mean()
                slope = (ema20 - ema20.shift(5)) / ema20.shift(5) * 100
                atr_rank = (atr/c*100).rolling(20).rank(pct=True)*100
                grp = grp.copy()
                grp['adx'] = adx; grp['slope'] = slope; grp['atr_rank'] = atr_rank
                return grp

            df_hist = df_hist.groupby('symbol', group_keys=False).apply(quick_regime)
            df_hist = df_hist.dropna(subset=['adx','slope','atr_rank'])

            def cls(row):
                if row['atr_rank'] > 80:               return 'HIGH_VOL'
                if row['adx'] > 25 and row['slope'] > 0.5: return 'TRENDING_UP'
                if row['adx'] > 25 and row['slope'] < -0.5: return 'TRENDING_DOWN'
                if row['adx'] < 20:                    return 'RANGING'
                return 'NEUTRAL'

            df_hist['regime_label'] = df_hist.apply(cls, axis=1)

            # Daily aggregate regime = mode of all symbols that day
            daily = df_hist.groupby(df_hist['bar_time'].dt.date)['regime_label'].agg(
                lambda x: x.value_counts().index[0] if len(x)>0 else 'NEUTRAL'
            )

            if len(daily) >= 5:
                prev_r = daily.iloc[:-1].values
                next_r = daily.iloc[1:].values

                # Count transitions
                from_states = all_regimes_list
                tm = {r: {s: 0 for s in all_regimes_list} for r in all_regimes_list}
                for fr, to in zip(prev_r, next_r):
                    if fr in tm and to in tm:
                        tm[fr][to] += 1

                # Normalize to probabilities
                for r in from_states:
                    row_sum = sum(tm[r].values())
                    if row_sum > 0:
                        for s in all_regimes_list:
                            tm[r][s] = round(tm[r][s] / row_sum, 2)

                transition_matrix = tm
        except Exception:
            pass  # transition matrix is optional — don't break main output

    # ── Macro Context ─────────────────────────────────────────────────────
    macro_ctx  = get_latest_macro()
    macro_rec  = []
    real_rate  = macro_ctx.get('real_interest_rate')
    m_usd_egp  = macro_ctx.get('usd_egp')
    m_infl     = macro_ctx.get('inflation')
    m_bias     = macro_ctx.get('strategic_bias', 'NEUTRAL')

    if real_rate is not None:
        if real_rate < -5:
            macro_rec.append(f'📉 الفائدة الحقيقية سلبية ({real_rate:.1f}%) — البيئة الكلية تدعم الأسهم')
        elif real_rate > 5:
            macro_rec.append(f'📈 الفائدة الحقيقية إيجابية ({real_rate:.1f}%) — الودائع تنافس الأسهم')
        else:
            macro_rec.append(f'↔️  الفائدة الحقيقية محايدة ({real_rate:.1f}%)')

    if m_infl is not None and m_infl > 30:
        macro_rec.append(f'🔥 تضخم مرتفع ({m_infl:.1f}%) — يفضّل الأسهم القائدة بالتضخم (طاقة، غذاء، مواد بناء)')

    if m_usd_egp is not None and m_usd_egp > 50:
        macro_rec.append(f'💵 USD/EGP={m_usd_egp:.2f} — يدعم المُصدِّرين')

    # تعديل rec النظام بناءً على ماكرو
    if market_regime in ('TRENDING', 'TRENDING_UP') and real_rate is not None and real_rate < -5:
        rec += ' | 🌍 البيئة الكلية تعزز الاتجاه الصاعد'
    elif market_regime in ('RANGING', 'MIXED') and real_rate is not None and real_rate < -3:
        rec += ' | 💡 الفائدة الحقيقية السلبية قد تحرّك السوق من مرحلة الترقب'

    return {
        'market_regime':         market_regime,
        'market_recommendation': rec,
        'total_symbols':         total,
        'regime_distribution':   counts,
        'regime_pcts': {r: round(counts.get(r,0)/total*100, 1) for r in counts},
        'by_regime':             by_regime,
        'trending_up_top10': latest[latest['regime']=='TRENDING_UP'].nlargest(10,'adx')[['symbol','adx','ema_slope']].round(1).to_dict('records'),
        'ranging_top10':     latest[latest['regime']=='RANGING'].nsmallest(10,'adx')[['symbol','adx','atr_rank']].round(1).to_dict('records'),
        'transition_matrix': transition_matrix,
        'transition_note':   'احتمالية الانتقال من regime اليوم إلى regime الغد (من 30 يوم تاريخية)',
        'macro_context': {
            'notes':              macro_rec if macro_rec else ['📊 لا بيانات ماكرو متاحة'],
            'real_interest_rate': round(real_rate, 2) if real_rate is not None else None,
            'usd_egp':            round(m_usd_egp, 4)  if m_usd_egp is not None else None,
            'inflation_pct':      round(m_infl, 2)      if m_infl is not None else None,
            'strategic_bias':     m_bias,
            'fetched_at':         macro_ctx.get('fetched_at', ''),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: get_latest_macro — يقرأ آخر snapshot ماكرو من DB
# ═══════════════════════════════════════════════════════════════════════════

def get_latest_macro():
    """
    يقرأ آخر سجل من macro_data ويُعيد dict بـ:
      usd_egp, inflation, cbe_rate, real_interest_rate, strategic_bias
    يُعيد {} إذا لم تكن الجدول موجودة أو فارغة.
    الجدول يستخدم lending_rate (قد لا تحتوي على cbe_rate كعمود).
    """
    try:
        import json as _json
        con = get_connection()

        # اكتشاف الأعمدة المتاحة ديناميكياً
        cols_info = con.execute("PRAGMA table_info(macro_data)").fetchall()
        if not cols_info:
            con.close()
            return {}
        avail = {c[1] for c in cols_info}  # أسماء الأعمدة

        # بناء SELECT بحسب الأعمدة المتاحة
        cbe_col  = 'cbe_rate' if 'cbe_rate' in avail else 'lending_rate'
        src_cond = "WHERE source = 'tradingview_live' AND fetched_at >= datetime('now', '-48 hours')" if 'source' in avail else ''
        row = con.execute(
            f"SELECT usd_egp, inflation, {cbe_col}, raw_json, fetched_at "
            f"FROM macro_data {src_cond} ORDER BY id DESC LIMIT 1"
        ).fetchone()
        # fallback: أي صف أخير إن لم يكن هناك TradingView Live
        if not row and src_cond:
            row = con.execute(
                f"SELECT usd_egp, inflation, {cbe_col}, raw_json, fetched_at "
                "FROM macro_data ORDER BY id DESC LIMIT 1"
            ).fetchone()
        con.close()

        if not row:
            return {}

        base = {
            'usd_egp':    row[0],
            'inflation':  row[1],
            'cbe_rate':   row[2],
            'fetched_at': row[4],
        }
        # استخراج حقول إضافية من raw_json
        try:
            extra = _json.loads(row[3] or '{}')
            base['real_interest_rate'] = extra.get('real_interest_rate')
            sb = extra.get('strategic_bias')
            base['strategic_bias']     = sb if sb and sb != 'None' else None
            base['usd_egp_source']     = extra.get('usd_egp_source')
        except Exception:
            pass

        # حساب الفائدة الحقيقية إذا لم تكن محفوظة
        if base.get('real_interest_rate') is None:
            cbe = base.get('cbe_rate')
            inf = base.get('inflation')
            if cbe is not None and inf is not None:
                base['real_interest_rate'] = cbe - inf

        # حساب التوجه الاستراتيجي إذا لم يكن محفوظاً (لأن Python يحفظ قبل الحساب)
        if not base.get('strategic_bias'):
            rr  = base.get('real_interest_rate')
            usd = base.get('usd_egp')
            inf = base.get('inflation')
            bias = 'NEUTRAL'
            if usd and usd > 50 and inf and inf > 15:
                bias = 'FAVOUR_EXPORTERS'
            if rr is not None and rr < -5:
                bias = 'EQUITY_POSITIVE'
            elif rr is not None and rr > 5:
                bias = 'EQUITY_NEGATIVE'
            base['strategic_bias'] = bias

        return base
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: get_market_regime — نظام الـ Regime Engine الحقيقي (4 حالات)
# ═══════════════════════════════════════════════════════════════════════════

def get_market_regime():
    """
    يُحدّد الـ Regime الحالي للسوق الكلي بناءً على:
      - متوسط mom5 (زخم 5 أيام) لجميع الأسهم
      - متوسط ATR اليومي عبر الكون

    4 حالات:
      CRASH   : mom5 ≤ -5% → RSI WR5=62.2% (الأفضل)
      DOWN    : mom5 -5 إلى -2% → RSI WR5=46.5%
      SIDEWAYS: mom5 -2 إلى +2% → RSI WR5=26.9% (تجنّب)
      UP      : mom5 +2 إلى +5% → RSI WR5=27.5% (تجنّب)
      SURGE   : mom5 > +5% → RSI WR5=21.0% (أسوأ وقت)

    مصدر: تحليل 65,913 شمعة × 249 سهم
    """
    try:
        con = get_connection()
        # آخر 5 أيام من بيانات OHLCV
        recent = pd.read_sql("""
            SELECT symbol, bar_time, close
            FROM ohlcv_history
            ORDER BY bar_time DESC
            LIMIT 6000
        """, con)
        con.close()

        if recent.empty or len(recent) < 50:
            return {'regime': 'UNKNOWN', 'mkt_mom5': None, 'n_symbols': 0}

        recent = recent.sort_values(['symbol', 'bar_time'])
        # آخر يومان من كل سهم لحساب mom5 قريب
        latest_prices = recent.groupby('symbol').apply(
            lambda g: g.sort_values('bar_time').tail(6)
        ).reset_index(drop=True)

        # mom5 لكل سهم (من آخر 6 نقاط)
        mom5_list = []
        for sym, g in latest_prices.groupby('symbol'):
            g = g.sort_values('bar_time')
            if len(g) < 2: continue
            oldest = g['close'].iloc[0]
            newest = g['close'].iloc[-1]
            if oldest > 0:
                mom5_list.append((newest / oldest - 1) * 100)

        if not mom5_list:
            return {'regime': 'UNKNOWN', 'mkt_mom5': None, 'n_symbols': 0}

        mkt_mom5 = float(np.median(mom5_list))  # median أكثر استقراراً من mean
        n_syms   = len(mom5_list)
        adv_ratio = sum(1 for m in mom5_list if m > 0) / len(mom5_list) * 100

        # تصنيف الـ Regime
        if mkt_mom5 <= -5:
            regime = 'CRASH'
            signal_quality = '🟢 BUY — أفضل وقت لـ RSI oversold'
            rsi_threshold  = 40
        elif mkt_mom5 <= -2:
            regime = 'DOWN'
            signal_quality = '🟡 SELECTIVE — RSI≤30 فقط'
            rsi_threshold  = 32
        elif mkt_mom5 >= 5:
            regime = 'SURGE'
            signal_quality = '🔴 AVOID — WR5=21% فقط'
            rsi_threshold  = 20  # مستحيل تقريباً = لا تدخل
        elif mkt_mom5 >= 2:
            regime = 'UP'
            signal_quality = '🔴 AVOID — WR5=27% فقط'
            rsi_threshold  = 25
        else:
            regime = 'SIDEWAYS'
            signal_quality = '🟠 WAIT — WR5=26.9%، انتظر DOWN'
            rsi_threshold  = 30

        return {
            'regime':         regime,
            'mkt_mom5':       round(mkt_mom5, 2),
            'adv_ratio':      round(adv_ratio, 1),
            'n_symbols':      n_syms,
            'signal_quality': signal_quality,
            'rsi_threshold':  rsi_threshold,  # الحد المُوصى به في هذا الـ Regime
        }
    except Exception as e:
        return {'regime': 'UNKNOWN', 'error': str(e), 'mkt_mom5': None}


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: ensemble_signal  — إشارة مجمّعة (Rules + ML Proxy + Macro + Calendar)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_ensemble_signal(params):
    """
    Meta-Signal يجمع 4 مصادر للإشارة:
      1. Rule Score   (45%): RSI+ADX Grid + OBV + Volume
      2. ML Proxy     (30%): approximation of RF probability
      3. Macro Factor (15%): Real rate + USD/EGP + Inflation environment
      4. Calendar     (10%): Earnings season / Ramadan effects
    Output: composite_score (0-100), signal class (STRONG_BUY/BUY/WATCH/NEUTRAL)
    """
    import datetime

    con = get_connection()
    ic  = pd.read_sql("""
        WITH latest AS (
            SELECT symbol, MAX(bar_date) as max_date
            FROM indicators_cache GROUP BY symbol
        )
        SELECT ic.*
        FROM indicators_cache ic
        JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
    """, con)
    con.close()

    if ic.empty:
        return {"error": "indicators_cache فارغ — شغّل rebuild_indicators أولاً"}

    # ── 1. Rule-Based Score ──────────────────────────────────────────────
    def rule_score(row):
        score = 0
        rsi       = row.get('rsi14',        50) or 50
        adx       = row.get('adx14',        15) or 15
        obv       = row.get('obv_divergence','')
        vol_r     = row.get('vol_ratio_20',  1) or 1
        mom5      = row.get('momentum_5d',   0) or 0
        bb_pos    = row.get('bb_position',  0.5) or 0.5

        # Grid-search optimal: RSI≤25 + ADX 20-25 → WR=78.4%
        if rsi <= 25 and 20 <= adx <= 25:
            score += 55
        elif rsi <= 30 and adx >= 18:
            score += 38
        elif rsi <= 35 and adx >= 20:
            score += 25
        elif rsi <= 40:
            score += 12

        # OBV confirmation (WR boost +10%)
        if obv == 'bullish':
            score += 18

        # Volume expansion
        if   vol_r > 2.0: score += 18
        elif vol_r > 1.5: score += 12
        elif vol_r > 1.2: score +=  6

        # BB oversold
        if   bb_pos < 0.1: score += 15
        elif bb_pos < 0.2: score +=  8

        # Momentum not deeply negative (avoid falling knives)
        if  -1 <= mom5 <= 3:  score += 8
        elif mom5 < -8:       score -= 15
        elif mom5 < -5:       score -= 8

        return float(min(max(score, 0), 100))

    # ── 2. ML Proxy (ATR-weighted RSI signal) ───────────────────────────
    def ml_proxy(row):
        """Feature-importance-weighted probability approximation.
           Mirrors the top SHAP features: ATR% > Momentum > RSI > ADX"""
        rsi    = row.get('rsi14',       50) or 50
        adx    = row.get('adx14',       15) or 15
        mom5   = row.get('momentum_5d',  0) or 0
        vol_r  = row.get('vol_ratio_20', 1) or 1
        bb_pos = row.get('bb_position', 0.5) or 0.5

        p = 0.30  # base rate

        # RSI contribution (feature rank 1-2)
        if   rsi <= 25: p += 0.22
        elif rsi <= 30: p += 0.16
        elif rsi <= 35: p += 0.10
        elif rsi <= 40: p += 0.05

        # ADX contribution
        if   20 <= adx <= 25: p += 0.12
        elif 25 <  adx <= 35: p += 0.08
        elif adx > 35:        p += 0.04

        # Momentum (not a falling knife)
        if   -1 <= mom5 <= 2: p += 0.10
        elif  2 <  mom5 <= 5: p += 0.06
        elif mom5 < -5:       p -= 0.10

        # Volume expansion
        if vol_r > 1.5: p += 0.06
        elif vol_r > 1.2: p += 0.03

        # BB oversold
        if bb_pos < 0.15: p += 0.08

        return min(max(p, 0.0), 0.95)

    # ── 3. Calendar Bonus ────────────────────────────────────────────────
    today     = datetime.date.today()
    month     = today.month
    day       = today.day
    today_str = today.isoformat()

    calendar_bonus = 0
    calendar_notes = []

    if month in (1, 4, 7, 10) and day <= 21:
        calendar_bonus += 8
        calendar_notes.append('📅 موسم النتائج (+8)')

    RAMADAN_RANGES = [
        ('2026-02-18', '2026-03-19'), ('2025-03-01', '2025-03-29'),
        ('2024-03-11', '2024-04-09'), ('2023-03-22', '2023-04-20'),
    ]
    POST_RAMADAN = [
        ('2026-03-20', '2026-04-02'), ('2025-03-30', '2025-04-12'),
        ('2024-04-10', '2024-04-24'), ('2023-04-21', '2023-05-05'),
    ]

    for s, e in RAMADAN_RANGES:
        if s <= today_str <= e:
            calendar_bonus -= 6
            calendar_notes.append('🌙 رمضان (-6 — سوق أبطأ)')
            break

    for s, e in POST_RAMADAN:
        if s <= today_str <= e:
            calendar_bonus += 10
            calendar_notes.append('🎉 ما بعد رمضان (+10 — أقوى أسبوعين)')
            break

    # ── 3b. Market Regime Gate (NEW — مُثبَت بـ 65,913 شمعة) ────────────
    mkt_regime   = get_market_regime()
    regime_name  = mkt_regime.get('regime', 'UNKNOWN')
    regime_rsi_t = mkt_regime.get('rsi_threshold', 30)
    regime_mult  = {
        'CRASH':    1.30,   # WR5=62.2% → ضاعف الأوزان
        'DOWN':     1.10,   # WR5=46.5% → زيادة طفيفة
        'SIDEWAYS': 0.70,   # WR5=26.9% → خفّف الإشارات
        'UP':       0.60,   # WR5=27.5% → تجنّب
        'SURGE':    0.40,   # WR5=21.0% → تجنّب تقريباً
        'UNKNOWN':  0.85,
    }.get(regime_name, 0.85)

    # أعد حساب rule_score بـ RSI threshold ديناميكي من الـ Regime
    def rule_score_regime(row):
        score = 0
        rsi    = row.get('rsi14',        50) or 50
        adx    = row.get('adx14',        15) or 15
        obv    = row.get('obv_divergence','')
        vol_r  = row.get('vol_ratio_20',  1) or 1
        mom5   = row.get('momentum_5d',   0) or 0
        bb_pos = row.get('bb_position', 0.5) or 0.5
        atr    = row.get('atr14',         2) or 2

        # ATR gate: إذا ATR < 1% → إشارة ضعيفة جداً (مُثبَت: WR5=13.5%)
        atr_factor = 1.0 if atr >= 2 else (0.6 if atr >= 1 else 0.2)

        # RSI: عتبة ديناميكية من الـ Regime
        if rsi <= regime_rsi_t - 5:   score += 55 * atr_factor
        elif rsi <= regime_rsi_t:      score += 35 * atr_factor
        elif rsi <= regime_rsi_t + 5:  score += 15 * atr_factor
        elif rsi <= 40:                score +=  8 * atr_factor

        # Momentum exhaustion (الدرايفر الحقيقي — AUC=0.639 أعلى من ADX=0.460)
        if   mom5 <= -10: score += 25; # momentum exhaustion قوي
        elif mom5 <= -5:  score += 15
        elif mom5 <= -2:  score +=  5
        elif mom5 > 2:    score -= 10  # لا تشترِ في صعود

        # OBV confirmation
        if obv == 'bullish': score += 18

        # BB oversold (bb_width هو #1 SHAP feature)
        if   bb_pos < 0.05: score += 20
        elif bb_pos < 0.1:  score += 12
        elif bb_pos < 0.2:  score +=  6

        # Volume expansion
        if   vol_r > 2.0: score += 15
        elif vol_r > 1.5: score += 10
        elif vol_r > 1.2: score +=  5

        # ADX: نافع قليلاً (AUC=0.460 فقط — ليس causal)
        if 20 <= adx <= 30: score += 5  # خُفِّض من 12 إلى 5

        return float(min(max(score * regime_mult, 0), 100))

    # ── 4. Macro Factor ──────────────────────────────────────────────────
    macro          = get_latest_macro()
    macro_score    = 50.0   # neutral baseline (maps to 0 adjustment after centering)
    macro_notes    = []
    real_rate      = macro.get('real_interest_rate')
    usd_egp        = macro.get('usd_egp')
    inflation      = macro.get('inflation')
    strategic_bias = macro.get('strategic_bias', 'NEUTRAL')

    if real_rate is not None:
        if   real_rate < -10: macro_score += 20; macro_notes.append(f'📉 فائدة حقيقية {real_rate:.1f}% — بيئة محفّزة جداً للأسهم')
        elif real_rate < -5:  macro_score += 14; macro_notes.append(f'📉 فائدة حقيقية {real_rate:.1f}% — بيئة محفّزة للأسهم')
        elif real_rate < 0:   macro_score +=  7; macro_notes.append(f'📉 فائدة حقيقية {real_rate:.1f}% — ملائمة للأسهم')
        elif real_rate < 3:   macro_score +=  0; macro_notes.append(f'↔️  فائدة حقيقية {real_rate:.1f}% — محايدة')
        elif real_rate < 7:   macro_score -= 10; macro_notes.append(f'📈 فائدة حقيقية {real_rate:.1f}% — الودائع منافسة للأسهم')
        else:                 macro_score -= 18; macro_notes.append(f'📈 فائدة حقيقية {real_rate:.1f}% — بيئة سلبية للأسهم')

    if usd_egp is not None:
        if usd_egp > 55:    macro_score +=  6; macro_notes.append(f'💵 USD/EGP={usd_egp:.2f} — المُصدِّرون يستفيدون')
        elif usd_egp > 50:  macro_score +=  3; macro_notes.append(f'💵 USD/EGP={usd_egp:.2f}')

    if inflation is not None and inflation > 35:
        macro_score -= 5; macro_notes.append(f'🔥 تضخم {inflation:.1f}% — يضغط على الهوامش')

    macro_score = max(0.0, min(100.0, macro_score))
    macro_adj   = (macro_score - 50.0)  # range −50..+50

    if not macro_notes:
        macro_notes = ['📊 لا بيانات ماكرو — تأثير محايد']

    # ── 5a. Hybrid Regime v2 (leading — replaces lagging mom5 regime) ───────
    regime_v2_data = get_market_regime_v2()
    r2_name   = regime_v2_data.get('regime_v2', regime_name)
    r2_mult   = regime_v2_data.get('regime_mult', regime_mult)
    r2_rsi_t  = regime_v2_data.get('rsi_threshold', regime_rsi_t)
    # Blend: 60% Hybrid v2 + 40% legacy mom5 regime (safety)
    blended_mult = r2_mult * 0.60 + regime_mult * 0.40

    # ── 5b. Event Sequence Layer 0 (per-stock path bonus) ──────────────────
    # Load OHLCV for event detection (last 20 bars each stock)
    try:
        con2 = get_connection()
        ohlcv_ev = pd.read_sql("""
            SELECT symbol, bar_time, close, volume
            FROM ohlcv_history
            ORDER BY bar_time DESC
            LIMIT 15000
        """, con2)
        con2.close()
        ohlcv_ev = ohlcv_ev.sort_values(['symbol', 'bar_time'])
        ohlcv_by_sym_ev = {sym: g.tail(20).reset_index(drop=True)
                           for sym, g in ohlcv_ev.groupby('symbol')}
    except Exception:
        ohlcv_by_sym_ev = {}

    def get_event_bonus(row):
        sym = row.get('symbol', '')
        df_s = ohlcv_by_sym_ev.get(sym)
        if df_s is None or len(df_s) < 6:
            return 0, 'NO_DATA', []
        # inject indicators
        df_s = df_s.copy()
        for col in ['rsi14', 'adx14', 'atr14', 'vol_ratio_20', 'bb_position',
                    'momentum_5d', 'momentum_10d']:
            if col in row:
                df_s[col] = row.get(col)
        seq = detect_event_sequence(df_s)
        state = seq.get('state', 'NEUTRAL')
        # Event bonus (additive to composite score)
        bonus = {
            'HIGH_PROB_REVERSAL': 25,   # التسلسل الذهبي → +25 pts!
            'LIKELY_REVERSAL':    15,
            'POSSIBLE_REVERSAL':   8,
            'OVERSOLD_MODERATE':   4,
            'OVERSOLD_WEAK':       2,
            'RECOVERY':            5,
            'FALLING_KNIFE':      -10,  # خطر!
            'DISTRIBUTION':        -5,
            'RANGE_BOUND':          0,
            'NEUTRAL':              0,
        }.get(state, 0)
        return bonus, state, seq.get('events', [])

    ic_dict_temp = ic.to_dict('records')
    event_bonuses  = []
    event_states   = []
    event_seqs     = []
    for row_dict in ic_dict_temp:
        b, s, e = get_event_bonus(row_dict)
        event_bonuses.append(b)
        event_states.append(s)
        event_seqs.append(e)

    ic['event_bonus'] = event_bonuses
    ic['event_state'] = event_states
    ic['event_seq']   = event_seqs

    # ── 6. Composite Score (6-factor: Rules + ML + Macro + Calendar + Regime + Event) ──
    ic['rule_score']      = ic.apply(rule_score_regime, axis=1)
    ic['ml_proxy_raw']    = ic.apply(ml_proxy,    axis=1)
    # macro_adj is a market-wide additive bonus/penalty (scaled to ±10 pts on composite)
    macro_contribution    = macro_adj * 0.20   # ±10 pts max contribution
    ic['composite_score'] = (
        ic['rule_score']   * 0.40 * blended_mult +  # rules × blended regime
        ic['ml_proxy_raw'] * 100  * 0.28 +
        ic['event_bonus']                        +  # Layer 0: path-dependent (+25 to -10)
        macro_contribution                       +
        calendar_bonus     * 0.10
    ).clip(0, 100).round(1)

    def classify(s):
        if s >= 75: return 'STRONG_BUY'
        if s >= 60: return 'BUY'
        if s >= 45: return 'WATCH'
        return 'NEUTRAL'

    ic['signal'] = ic['composite_score'].apply(classify)

    def fmt(row):
        return {
            'symbol':          row['symbol'],
            'composite_score': row['composite_score'],
            'rule_score':      round(row['rule_score'], 1),
            'ml_proxy_pct':    round(row['ml_proxy_raw'] * 100, 1),
            'event_state':     row.get('event_state', 'NEUTRAL'),
            'event_bonus':     row.get('event_bonus', 0),
            'event_seq':       row.get('event_seq', []),
            'rsi14':           round(row['rsi14'], 1)  if row.get('rsi14') is not None else None,
            'adx14':           round(row['adx14'], 1)  if row.get('adx14') is not None else None,
            'momentum_5d':     round(row['momentum_5d'], 2) if row.get('momentum_5d') is not None else None,
            'obv':             row.get('obv_divergence',''),
        }

    strong = ic[ic['signal']=='STRONG_BUY'].sort_values('composite_score', ascending=False)
    buys   = ic[ic['signal']=='BUY'].sort_values('composite_score', ascending=False)
    watch  = ic[ic['signal']=='WATCH'].sort_values('composite_score', ascending=False)

    # إحصاء إشارات التسلسل الذهبي
    gold_signals = ic[ic['event_state'] == 'HIGH_PROB_REVERSAL']

    return {
        'date':             today_str,
        'calendar_context': calendar_notes if calendar_notes else ['📆 لا أحداث موسمية'],
        'calendar_bonus':   calendar_bonus,
        'macro_context':    macro_notes,
        'macro_score':      round(macro_score, 1),
        'macro_adj':        round(macro_adj, 1),
        'macro_data': {
            'real_interest_rate': round(real_rate, 2) if real_rate is not None else None,
            'usd_egp':            round(usd_egp, 4)   if usd_egp is not None else None,
            'inflation_pct':      round(inflation, 2)  if inflation is not None else None,
            'strategic_bias':     strategic_bias,
            'fetched_at':         macro.get('fetched_at', ''),
        },
        'signal_counts':    ic['signal'].value_counts().to_dict(),
        'strong_buy': [fmt(r) for _, r in strong.head(10).iterrows()],
        'buy':        [fmt(r) for _, r in buys.head(10).iterrows()],
        'watch':      [fmt(r) for _, r in watch.head(10).iterrows()],
        'gold_reversal_signals': [fmt(r) for _, r in gold_signals.head(10).iterrows()],
        'market_regime':    mkt_regime,
        'regime_v2':        regime_v2_data,
        'regime_multiplier': round(blended_mult, 2),
        'methodology': (
            f'Rule×40%×Regime + ML×28% + Event_Path + Macro×15% + Calendar×10% | '
            f'Regime_v2={r2_name}(×{r2_mult:.2f}) | Regime_v1={regime_name}(×{regime_mult:.2f}) | '
            f'Blended={blended_mult:.2f} | Breadth={regime_v2_data.get("breadth_pct","?")}% | '
            f'Confidence={regime_v2_data.get("confidence","?")}'
        ),
        'note': (
            f'🔥 {len(gold_signals)} إشارة ذهبية (HIGH_PROB_REVERSAL)! + '
            f'{len(strong)} STRONG_BUY | {len(buys)} BUY | {len(watch)} WATCH'
            if len(gold_signals) > 0 else
            f'⚠️  لا إشارات اليوم — Regime={r2_name} يُضعف الإشارات'
            if len(strong) == 0 and blended_mult < 0.8 else
            '⚠️  لا إشارات STRONG_BUY اليوم — RSI مرتفع'
            if len(strong) == 0 else
            f'🔥 {len(strong)} إشارة STRONG_BUY | {len(buys)} BUY | {len(watch)} WATCH'
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: active_universe  — فلتر الأسهم النشطة (سيولة + حجم)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_active_universe(params):
    """
    يحسب مقاييس السيولة لكل سهم من بيانات OHLCV:
      - avg_value_30d  (متوسط قيمة التداول اليومية EGP)
      - avg_vol_30d    (متوسط الحجم)
      - days_traded    (عدد أيام التداول الفعلية)
      - price_latest   (آخر سعر)
      - amihud_30d     (illiquidity ratio)
    يُصنّف: LIQUID / ILLIQUID / THIN / DEAD
    """
    min_value  = float(params.get('min_value', 500_000))   # EGP/day
    min_days   = int(params.get('min_days',    30))

    con = get_connection()
    df  = pd.read_sql("""
        SELECT symbol, bar_time, close, volume
        FROM ohlcv_history
        WHERE bar_time >= strftime('%s', date('now', '-90 days'))
        ORDER BY symbol, bar_time DESC
    """, con)
    con.close()

    if df.empty:
        return {"error": "لا بيانات OHLCV"}

    df['value']    = df['close'] * df['volume']
    df['ret_abs']  = df.groupby('symbol')['close'].pct_change().abs()

    # Last 30 trading days
    df30 = df.groupby('symbol').head(30)

    agg = df30.groupby('symbol').agg(
        avg_value_30d=('value',   'mean'),
        avg_vol_30d  =('volume',  'mean'),
        days_traded  =('value',   'count'),
        price_latest =('close',   'first'),
        amihud_30d   =('ret_abs', lambda x: (x / df30.loc[x.index, 'value'].replace(0, np.nan)).mean() * 1e6),
    ).reset_index()

    def classify_liq(row):
        val   = row['avg_value_30d']
        days  = row['days_traded']
        if days < 10:                      return 'DEAD'
        if val < 100_000:                  return 'DEAD'
        if val < 500_000 or days < 20:     return 'THIN'
        if val < 2_000_000:                return 'ILLIQUID'
        return 'LIQUID'

    agg['liquidity_class'] = agg.apply(classify_liq, axis=1)

    counts  = agg['liquidity_class'].value_counts().to_dict()
    liquid  = agg[agg['liquidity_class']=='LIQUID'].sort_values('avg_value_30d', ascending=False)
    illiquid= agg[agg['liquidity_class']=='ILLIQUID'].sort_values('avg_value_30d', ascending=False)
    thin    = agg[agg['liquidity_class']=='THIN']
    dead    = agg[agg['liquidity_class']=='DEAD']

    def fmt_row(r):
        return {
            'symbol':         r['symbol'],
            'avg_value_30d':  round(float(r['avg_value_30d']), 0),
            'avg_value_30d_k': round(float(r['avg_value_30d'])/1000, 0),
            'days_traded':    int(r['days_traded']),
            'price_latest':   round(float(r['price_latest']), 2),
            'amihud_30d':     round(float(r['amihud_30d']), 4) if r['amihud_30d'] == r['amihud_30d'] else None,
            'liquidity_class':r['liquidity_class'],
        }

    return {
        'summary':       counts,
        'total_symbols': len(agg),
        'liquid_universe': [fmt_row(r) for _, r in liquid.iterrows()],
        'illiquid_symbols':[fmt_row(r) for _, r in illiquid.head(20).iterrows()],
        'thin_symbols':  thin['symbol'].tolist(),
        'dead_symbols':  dead['symbol'].tolist(),
        'recommended_core': liquid.head(30)['symbol'].tolist(),
        'filters_used': {
            'min_avg_daily_value': min_value,
            'min_trading_days':    min_days,
            'window_days':         30,
        },
        'note': (
            f"✅ {counts.get('LIQUID',0)} سهم سائل (قيمة يومية > 2M EGP) | "
            f"⚠️ {counts.get('ILLIQUID',0)+counts.get('THIN',0)} محدود السيولة | "
            f"❌ {counts.get('DEAD',0)} غير نشط"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# MACRO CONTEXT HELPER — مشترك بين كل الأوامر
# ═══════════════════════════════════════════════════════════════════════════

def _load_macro_context(max_age_hours=72):
    """
    يُعيد dict بالسياق الاقتصادي الكلي الكامل من macro_snapshot أو macro_data.
    يُستخدم في كل الأوامر التحليلية لتكييف النتائج مع البيئة الاقتصادية.

    يُعيد:
      inflation_yoy, cbe_rate, usd_egp, core_inflation,
      gdp_yoy, unemployment, fx_reserves_b, trade_balance_m,
      remittances_q, current_account_b, govt_debt_gdp,
      external_debt_b, fdi_q_b, oil_production_kbd,
      real_interest_rate, macro_regime, regime_score,
      strategic_bias, equity_multiplier,
      inflation_momentum, rate_cycle, fx_trend, growth_trend,
      _source, _fetched_at
    أو None إذا لا توجد بيانات
    """
    try:
        con = get_connection()

        # ── أولوية: macro_snapshot (الشامل) ──────────────────────────────
        try:
            cols = {c[1] for c in con.execute("PRAGMA table_info(macro_snapshot)").fetchall()}
            if cols:
                row = con.execute(
                    f"SELECT * FROM macro_snapshot "
                    f"WHERE fetched_at >= datetime('now', '-{max_age_hours} hours') "
                    f"ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    col_names = [d[1] for d in con.execute("PRAGMA table_info(macro_snapshot)").fetchall()]
                    d = dict(zip(col_names, row))
                    raw = json.loads(d.get('raw_json') or '{}')
                    d.update(raw)
                    d['_source']     = 'macro_snapshot'
                    d['_fetched_at'] = d.get('fetched_at', '')
                    # backward-compat
                    d.setdefault('inflation_pct',    d.get('inflation_yoy'))
                    d.setdefault('cbe_rate_pct',     d.get('cbe_rate'))
                    d.setdefault('lending_rate_pct', d.get('cbe_rate'))
                    con.close()
                    return d
        except Exception:
            pass

        # ── fallback: macro_data (القديم) ────────────────────────────────
        try:
            tv_row = con.execute(
                "SELECT usd_egp, inflation, cbe_rate, lending_rate, raw_json, fetched_at "
                "FROM macro_data "
                f"WHERE fetched_at >= datetime('now', '-{max_age_hours} hours') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if tv_row:
                raw = json.loads(tv_row[4] or '{}')
                infl = tv_row[1]
                cbe  = tv_row[2] or tv_row[3]
                usd  = tv_row[0]
                rr   = round(cbe - infl, 2) if (cbe and infl) else raw.get('real_interest_rate')
                d = {
                    **raw,
                    'usd_egp': usd, 'inflation_yoy': infl, 'inflation_pct': infl,
                    'cbe_rate': cbe, 'cbe_rate_pct': cbe, 'lending_rate_pct': cbe,
                    'real_interest_rate': rr,
                    'macro_regime': raw.get('macro_regime', 'UNKNOWN'),
                    'regime_score': raw.get('regime_score', 50),
                    'equity_multiplier': raw.get('equity_multiplier', 1.0),
                    'strategic_bias': raw.get('strategic_bias', 'NEUTRAL'),
                    'inflation_momentum': raw.get('inflation_momentum'),
                    'rate_cycle': raw.get('rate_cycle'),
                    '_source': 'macro_data',
                    '_fetched_at': tv_row[5],
                }
                con.close()
                return d
        except Exception:
            pass

        con.close()
    except Exception:
        pass
    return None


def _macro_regime_factor(macro_ctx):
    """
    يُعيد معامل تعديل P(TR) بناءً على الريجيم الاقتصادي.
    أعلى من 1 = أسهم مدعومة، أقل من 1 = ضغط على الأسهم.
    """
    if not macro_ctx:
        return 1.0, 'NO_MACRO_DATA'
    eq_mult = macro_ctx.get('equity_multiplier')
    if eq_mult and eq_mult != 1.0:
        return float(eq_mult), macro_ctx.get('macro_regime', 'UNKNOWN')
    # حساب بسيط إذا لم يكن equity_multiplier محسوباً
    rr   = macro_ctx.get('real_interest_rate')
    infl = macro_ctx.get('inflation_yoy') or macro_ctx.get('inflation_pct')
    if rr is None and infl is not None:
        cbe = macro_ctx.get('cbe_rate') or macro_ctx.get('cbe_rate_pct')
        rr  = (cbe - infl) if (cbe and infl) else None
    if rr is None:
        return 1.0, 'NO_REAL_RATE'
    if   rr < -5:  factor = 1.08
    elif rr < 0:   factor = 1.04
    elif rr < 3:   factor = 1.01
    elif rr < 6:   factor = 0.97
    elif rr < 10:  factor = 0.93
    else:          factor = 0.88
    regime = macro_ctx.get('macro_regime', 'UNKNOWN')
    return round(factor, 3), regime


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: macro_regime — ريجيم الاقتصاد الكلي الكامل
# ═══════════════════════════════════════════════════════════════════════════

def cmd_macro_regime(params):
    """
    يُعيد صورة كاملة لبيئة الاقتصاد الكلي المصري مع:
    - كل المؤشرات من macro_snapshot (22 مؤشر)
    - تصنيف الريجيم (DISINFLATION_EASING, STAGFLATION_TIGHT, etc.)
    - معامل تعديل الأسهم (equity_multiplier)
    - تاريخية المؤشرات الرئيسية من macro_economics
    """
    max_age = params.get('max_age_hours', 168)   # 7 أيام افتراضياً
    macro   = _load_macro_context(max_age)

    if not macro:
        return {'success': False, 'error': 'لا توجد بيانات macro — شغّل: node scripts/fetch_economics.mjs'}

    # ── تاريخية المؤشرات ──────────────────────────────────────────────
    history = {}
    try:
        con = get_connection()
        for field in ['inflation_yoy', 'cbe_rate', 'usd_egp', 'gdp_yoy', 'fx_reserves_b']:
            rows = con.execute(
                "SELECT period_date, value FROM macro_economics "
                "WHERE field_name = ? ORDER BY period_date DESC LIMIT 24",
                (field,)
            ).fetchall()
            if rows:
                history[field] = [{'date': r[0], 'value': r[1]} for r in reversed(rows)]
        con.close()
    except Exception:
        pass

    # ── ملخص تحليلي ────────────────────────────────────────────────────
    rr     = macro.get('real_interest_rate')
    infl   = macro.get('inflation_yoy') or macro.get('inflation_pct')
    cbe    = macro.get('cbe_rate') or macro.get('cbe_rate_pct')
    usd    = macro.get('usd_egp')
    gdp    = macro.get('gdp_yoy')
    res    = macro.get('fx_reserves_b')
    trade  = macro.get('trade_balance_m')
    regime = macro.get('macro_regime', 'UNKNOWN')
    mult   = macro.get('equity_multiplier', 1.0)

    interpretation = []
    if infl  and infl > 15:   interpretation.append(f'⚠️ تضخم مرتفع {infl:.1f}% — يضغط على المستهلك')
    elif infl and infl < 10:  interpretation.append(f'✅ تضخم منخفض {infl:.1f}% — إيجابي للاستهلاك')
    if rr   is not None:
        if rr < 0:  interpretation.append(f'✅ فائدة حقيقية سالبة ({rr:.1f}%) — يدعم الأسهم على الودائع')
        elif rr > 5: interpretation.append(f'⚠️ فائدة حقيقية عالية ({rr:.1f}%) — الودائع منافسة للأسهم')
    if gdp  and gdp > 4:     interpretation.append(f'✅ نمو قوي {gdp:.1f}% — إيجابي لأرباح الشركات')
    if res  and res > 40:    interpretation.append(f'✅ احتياطيات قوية ${res:.1f}B — استقرار EGP')
    elif res and res < 20:   interpretation.append(f'⚠️ احتياطيات ضعيفة ${res:.1f}B — خطر EGP')
    if trade and trade < -8: interpretation.append(f'⚠️ عجز تجاري كبير ${trade:.1f}B/شهر')
    if macro.get('rate_cycle') == 'falling':
        interpretation.append(f'✅ دورة خفض الفائدة — إيجابي جداً للتقييم')
    if macro.get('inflation_momentum') == 'falling':
        interpretation.append(f'✅ التضخم في تراجع — دعم استمرار خفض الفائدة')

    return {
        'success': True,
        'macro_regime': regime,
        'regime_score': macro.get('regime_score', 50),
        'equity_multiplier': mult,
        'strategic_bias': macro.get('strategic_bias', 'NEUTRAL'),
        # المؤشرات الأساسية
        'core': {
            'usd_egp':           usd,
            'inflation_yoy':     infl,
            'core_inflation':    macro.get('core_inflation'),
            'cbe_rate':          cbe,
            'real_interest_rate': rr,
            'gdp_yoy':           gdp,
            'unemployment':      macro.get('unemployment'),
        },
        # الاحتياطيات والتجارة
        'external': {
            'fx_reserves_b':     res,
            'trade_balance_m':   trade,
            'exports_m':         macro.get('exports_m'),
            'imports_m':         macro.get('imports_m'),
            'remittances_q':     macro.get('remittances_q'),
            'current_account_b': macro.get('current_account_b'),
            'external_debt_b':   macro.get('external_debt_b'),
            'fdi_q_b':           macro.get('fdi_q_b'),
        },
        # المالية العامة
        'fiscal': {
            'govt_debt_gdp':       macro.get('govt_debt_gdp'),
            'budget_balance_egp_t': macro.get('budget_balance_egp_t'),
            'govt_revenue_egp_t':  macro.get('govt_revenue_egp_t'),
            'fiscal_exp_egp_t':    macro.get('fiscal_exp_egp_t'),
        },
        # الطاقة
        'energy': {
            'oil_production_kbd': macro.get('oil_production_kbd'),
        },
        # الاتجاهات
        'trends': {
            'inflation_momentum': macro.get('inflation_momentum'),
            'rate_cycle':         macro.get('rate_cycle'),
            'fx_trend':           macro.get('fx_trend'),
            'growth_trend':       macro.get('growth_trend'),
        },
        'history':         history,
        'interpretation':  interpretation,
        '_source':         macro.get('_source', '?'),
        '_fetched_at':     macro.get('_fetched_at', '?'),
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: macro_data
# ═══════════════════════════════════════════════════════════════════════════

def cmd_macro_data(params):
    """
    جلب البيانات الاقتصادية الكلية لمصر بمصادر متعددة وfallback chain ذكي:

    الأولوية:
      0. TradingView Live (من DB — يُجمع بـ fetch_economics.mjs) — خلال 72 ساعة ← الأفضل
      1. USD/EGP: Open.er-api.com ثم ExchangeRate-API — يومي
      2. تضخم: World Bank FP.CPI.TOTL.ZG — سنوي (fallback إذا لم يكن TradingView Live)
      3. فائدة: World Bank FR.INR.DPST / FR.INR.LEND — سنوي (fallback)
      4. cache: آخر قيمة محفوظة في macro_data DB

    هام: إذا كانت بيانات TradingView Live حديثة (< 72 ساعة) نُعيدها مباشرة بدون
         كتابة صف جديد من World Bank.
    """
    import urllib.request
    import urllib.error
    import time

    # ── PRIORITY 0: macro_snapshot الشامل (22 مؤشر) ── أحدث من 72 ساعة ───
    macro_full = _load_macro_context(max_age_hours=72)
    if macro_full:
        infl = macro_full.get('inflation_yoy') or macro_full.get('inflation_pct')
        cbe  = macro_full.get('cbe_rate') or macro_full.get('cbe_rate_pct')
        usd  = macro_full.get('usd_egp')
        rr   = macro_full.get('real_interest_rate')
        if rr is None and cbe and infl:
            rr = round(cbe - infl, 2)
        result_tv = {
            **macro_full,
            'inflation_pct':     infl,
            'cbe_rate_pct':      cbe,
            'lending_rate_pct':  cbe,
            'real_interest_rate': rr,
            'tradingview_data':  macro_full.get('_source') != 'macro_data',
        }
        return result_tv

    HDRS = {"User-Agent": "EGX-System/1.0", "Accept": "application/json"}

    def fetch_json(url, timeout=12):
        req = urllib.request.Request(url, headers=HDRS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def wb_latest(indicator, timeout=12):
        """World Bank API — آخر قيمة غير null"""
        url = (
            f"https://api.worldbank.org/v2/country/EG/indicator/{indicator}"
            f"?format=json&mrv=5&per_page=5"
        )
        data = fetch_json(url, timeout)
        records = data[1] if isinstance(data, list) and len(data) > 1 else []
        for rec in records:
            if rec.get("value") is not None:
                return round(float(rec["value"]), 4), str(rec.get("date", ""))
        return None, None

    def db_last(field):
        """آخر قيمة محفوظة في macro_data"""
        try:
            con = get_connection()
            row = con.execute(
                f"SELECT {field}, fetched_at FROM macro_data WHERE {field} IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            con.close()
            if row:
                return round(float(row[0]), 4), str(row[1])[:10] + " (cached)"
        except Exception:
            pass
        return None, None

    results = {}
    errors  = []

    # ── 1. USD/EGP — أولوية: open.er-api ثم exchangerate-api ──────────────
    for usd_url, usd_name in [
        ("https://open.er-api.com/v6/latest/USD",          "open.er-api.com"),
        ("https://api.exchangerate-api.com/v4/latest/USD", "exchangerate-api.com"),
    ]:
        try:
            data     = fetch_json(usd_url, timeout=12)
            rates    = data.get("rates") or data.get("conversion_rates", {})
            egp_rate = rates.get("EGP")
            if egp_rate:
                results["usd_egp"]        = round(float(egp_rate), 4)
                results["usd_egp_date"]   = data.get("date") or data.get("time_last_update_utc", "")[:10]
                results["usd_egp_source"] = usd_name
                break
        except Exception as e:
            errors.append(f"USD/EGP ({usd_name}): {e}")

    if "usd_egp" not in results:
        v, d = db_last("usd_egp")
        results["usd_egp"] = v
        results["usd_egp_date"] = d or ""
        results["usd_egp_source"] = "cache"

    # ── 2. Egypt Inflation ─────────────────────────────────────────────────
    # أولوية: World Bank CPI YoY (FP.CPI.TOTL.ZG) — سنوي
    # ثم: Deposit Rate (FR.INR.DPST) كـ proxy لبيئة التضخم
    inflation_set = False
    for ind, label in [("FP.CPI.TOTL.ZG", "World Bank CPI YoY"), ("NY.GDP.DEFL.KD.ZG", "World Bank GDP Deflator")]:
        if inflation_set:
            break
        try:
            val, period = wb_latest(ind, timeout=12)
            if val is not None:
                results["inflation_pct"]    = val
                results["inflation_year"]   = period
                results["inflation_source"] = label
                inflation_set = True
        except Exception as e:
            errors.append(f"Inflation ({label}): {e}")

    if not inflation_set:
        v, d = db_last("inflation")
        results["inflation_pct"]    = v
        results["inflation_year"]   = d or ""
        results["inflation_source"] = "cache (World Bank يتأخر)"

    # ── 3. CBE Rate ────────────────────────────────────────────────────────
    # أولوية: World Bank Deposit Rate (FR.INR.DPST) — أحدث من Lending
    # ثم: World Bank Lending Rate (FR.INR.LEND)
    # ملاحظة: World Bank يعكس متوسط سنوي — للسعر الآني راجع cbe.org.eg
    cbe_set = False
    for ind, label in [("FR.INR.DPST", "World Bank Deposit Rate"), ("FR.INR.LEND", "World Bank Lending Rate")]:
        if cbe_set:
            break
        try:
            val, period = wb_latest(ind, timeout=12)
            if val is not None:
                results["cbe_rate_pct"]    = val
                results["cbe_rate_year"]   = period
                results["cbe_rate_source"] = label
                cbe_set = True
        except Exception as e:
            errors.append(f"CBE Rate ({label}): {e}")

    if not cbe_set:
        v, d = db_last("lending_rate")
        results["cbe_rate_pct"]    = v
        results["cbe_rate_year"]   = d or ""
        results["cbe_rate_source"] = "cache"

    # backward-compat: احتفظ بـ lending_rate_pct للكود القديم
    results["lending_rate_pct"]  = results.get("cbe_rate_pct")
    results["lending_rate_year"] = results.get("cbe_rate_year")

    # ── 4. حفظ في SQLite ────────────────────────────────────────────────────
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS macro_data (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at   TEXT NOT NULL,
                usd_egp      REAL,
                inflation    REAL,
                lending_rate REAL,
                raw_json     TEXT
            )
        """)
        # أضف source='python_worldbank' حتى لا تُلوّث بيانات TradingView Live
        src_col = ''
        src_val = ''
        try:
            existing_cols = [c[1] for c in cur.execute("PRAGMA table_info(macro_data)").fetchall()]
            if 'source' in existing_cols:
                src_col = ', source'
                src_val = ', ?'
        except Exception:
            pass
        cur.execute(
            f"INSERT INTO macro_data (fetched_at, usd_egp, inflation, lending_rate, raw_json{src_col}) "
            f"VALUES (?, ?, ?, ?, ?{src_val})",
            (
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                results.get("usd_egp"),
                results.get("inflation_pct"),
                results.get("cbe_rate_pct"),
                json.dumps(results, ensure_ascii=False),
                *(['python_worldbank'] if src_val else []),
            )
        )
        con.commit()
        con.close()
        results["saved_to_db"] = True
    except Exception as e:
        errors.append(f"DB save: {e}")
        results["saved_to_db"] = False

    # ── 5. حساب مؤشرات مشتقة + تفسير ────────────────────────────────────
    usd = results.get("usd_egp")
    inf = results.get("inflation_pct")
    cbe = results.get("cbe_rate_pct")

    # فائدة حقيقية = فائدة إيداع - تضخم
    real_rate = round(cbe - inf, 2) if (cbe and inf) else None
    results["real_interest_rate"] = real_rate

    interpretation = []
    if usd:
        interpretation.append(
            f"الدولار={usd:.2f} جنيه — "
            + ("ضغط تضخمي على الواردات" if usd > 50 else "استقرار نسبي")
        )
    if inf:
        level = ("مرتفع جداً: تفضيل التصدير والسلع الأساسية" if inf > 20
                 else "مرتفع: تحوط بالأصول العينية" if inf > 10
                 else "معتدل: بيئة متوازنة")
        interpretation.append(f"تضخم={inf:.1f}% — {level}")
    if cbe:
        interpretation.append(
            f"فائدة CBE={cbe:.1f}% (متوسط سنوي) | "
            + (f"فائدة حقيقية={real_rate:.1f}% (سلبية → أسهم أفضل من ودائع)" if real_rate and real_rate < 0
               else f"فائدة حقيقية={real_rate:.1f}% (موجبة → ودائع منافسة)" if real_rate
               else "")
        )
        interpretation.append(
            "⚠️  ملاحظة: فائدة CBE الفعلية الآن راجع cbe.org.eg — World Bank يعكس متوسط سنوي"
        )

    # التوجه الاستراتيجي
    strategic_bias = "NEUTRAL"
    if usd and usd > 50 and inf and inf > 15:
        strategic_bias = "FAVOUR_EXPORTERS"   # مصدّرون + عملة أجنبية
    if real_rate is not None and real_rate < -5:
        strategic_bias = "EQUITY_POSITIVE"    # فائدة حقيقية سلبية → الأسهم جذابة
    elif real_rate is not None and real_rate > 5:
        strategic_bias = "EQUITY_NEGATIVE"    # فائدة حقيقية عالية → ودائع أفضل

    return {
        **results,
        "errors":              errors,
        "interpretation":      interpretation,
        "strategic_bias":      strategic_bias,
        "data_quality": {
            "usd_egp":    "daily"   if results.get("usd_egp_source","") not in ("cache",) else "cached",
            "inflation":  "annual"  if "World Bank" in results.get("inflation_source","") else "cached",
            "cbe_rate":   "annual"  if "World Bank" in results.get("cbe_rate_source","")  else "cached",
        },
        "summary": (
            f"USD/EGP={usd or 'N/A'} | "
            f"تضخم={inf or 'N/A'}% | "
            f"فائدة CBE={cbe or 'N/A'}% | "
            f"فائدة حقيقية={real_rate or 'N/A'}% | "
            f"توجّه: {strategic_bias}"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EVENT-BASED ENGINE — Path-Dependent Signal Detection
# بدلاً من Snapshot ثابت → نكشف تسلسل الأحداث (Sequence Detection)
# ═══════════════════════════════════════════════════════════════════════════

def detect_event_sequence(df_stock):
    """
    يكشف تسلسل الأحداث السوقية لسهم واحد (Path-Dependent).

    المدخلات:
      df_stock — DataFrame مُرتَّب زمنياً لسهم واحد مع:
        close, rsi14, adx14, atr14, volume, momentum_5d, momentum_10d, vol_ratio_20, bb_position

    المخرجات:
      list of events detected (UPTREND, SHARP_DROP, PANIC, EXHAUSTION, RECOVERY, RANGE_BOUND)
      + state string
      + sequence_score (0-100) — قوة التسلسل

    الأحداث المعروّفة والأوزانها:
      UPTREND     → mom10 > +5% قبل 5-10 أيام
      SHARP_DROP  → mom5  ≤ -5% في آخر 3 أيام
      PANIC       → ATR z-score ≥ 1.5  + vol_ratio ≥ 2x
      EXHAUSTION  → rsi ≤ 35 اليوم
      DISTRIBUTION→ mom10 > +5% + rsi قريب من 70
      RECOVERY    → mom5 ≥ +3% بعد rsi≤30 سابق

    التسلسل الذهبي (WR5 ~62%):
      UPTREND → SHARP_DROP → PANIC → EXHAUSTION  → HIGH_PROB_REVERSAL
    """
    if df_stock is None or len(df_stock) < 12:
        return {'events': [], 'state': 'INSUFFICIENT_DATA', 'sequence_score': 0}

    df = df_stock.copy().reset_index(drop=True)
    n  = len(df)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _get(col, i=-1, default=None):
        if col not in df.columns: return default
        val = df[col].iloc[i]
        return default if (val != val or val is None) else val  # NaN check

    def _safe_mom(days):
        """عائد من -days bars حتى الآن"""
        if n <= days: return None
        old = _get('close', -(days+1))
        new = _get('close', -1)
        if old and old > 0: return (new / old - 1) * 100
        return None

    # ── قراءات حالية ────────────────────────────────────────────────────────
    rsi_now   = _get('rsi14',       -1, 50)
    adx_now   = _get('adx14',       -1, 15)
    atr_now   = _get('atr14',       -1, 2)
    vol_r_now = _get('vol_ratio_20', -1, 1)
    mom5_now  = _get('momentum_5d', -1) or _safe_mom(5) or 0
    mom10_now = _get('momentum_10d',-1) or _safe_mom(10) or 0
    bb_now    = _get('bb_position', -1, 0.5)

    # ── قراءات تاريخية ──────────────────────────────────────────────────────
    rsi_3d_ago  = _get('rsi14',       -4, 50) if n >= 4 else rsi_now
    rsi_10d_ago = _get('rsi14',      -11, 50) if n >= 11 else rsi_now
    mom10_6d    = None  # mom10 قبل 6 أيام
    if 'momentum_10d' in df.columns and n >= 7:
        v = df['momentum_10d'].iloc[-7]
        mom10_6d = None if v != v else v

    # ── ATR z-score (volatility spike detector) ──────────────────────────────
    if 'atr14' in df.columns and n >= 20:
        atr_series = df['atr14'].tail(20).dropna()
        atr_mean = atr_series.mean()
        atr_std  = atr_series.std()
        atr_z    = (atr_now - atr_mean) / atr_std if atr_std > 0 else 0
    else:
        atr_z = 0

    # ── RSI Slope (آخر 3 أيام) — UPGRADE: RSI timing signal ─────────────────
    # Discovered: RSI STILL FALLING → WR5=71.8% vs RSI FLAT → WR5=45.9%
    rsi_slope3 = None
    if 'rsi14' in df.columns and n >= 4:
        r_now = df['rsi14'].iloc[-1]
        r_3d  = df['rsi14'].iloc[-4] if n >= 4 else r_now
        if r_now == r_now and r_3d == r_3d:
            rsi_slope3 = r_now - r_3d  # نيغاتيف = RSI يسقط

    # ── Drop Acceleration (NEW alpha — Velocity Exhaustion) ──────────────────
    # Formula: drop_accel = mom5 - mom10/2
    # If < -3 → drop is accelerating faster than the trend → exhaustion physics
    # Proven: accel<-3 + RSI≤30 → WR5=74.4% (stable across 2021-2026!)
    drop_accel = (mom5_now - mom10_now / 2.0) if mom10_now != 0 else mom5_now

    # ══════════════════════════════════════════════════════════════════════════
    # HARD GATE v2: FLAT MARKET FALSE SIGNAL REJECTION
    # Discovered: RSI≤30 in mom10 FLAT (-2:+2) → WR5=23.5% (worse than random!)
    # This was RSI≤30 alone's major source of false signals pre-2024
    # ══════════════════════════════════════════════════════════════════════════
    if rsi_now <= 35 and -2 <= mom10_now <= 2 and abs(mom5_now) <= 2:
        # RSI oversold in a FLAT market — no real downtrend → false signal
        return {
            'events':         ['FLAT_MARKET_TRAP'],
            'state':          'FLAT_RSI_FALSE',
            'sequence_score': 0,
            'full_gold':      False,
            'drop_accel':     round(drop_accel, 2),
            'rsi_slope3':     round(rsi_slope3, 2) if rsi_slope3 is not None else None,
            'details': {
                'rsi_now': round(rsi_now, 1), 'mom5_now': round(mom5_now, 2),
                'mom10_now': round(mom10_now, 2), 'note': 'RSI≤35 in flat market = WR23.5% — REJECTED',
            },
        }

    # ══════════════════════════════════════════════════════════════════════════
    # DETECT EVENTS
    # ══════════════════════════════════════════════════════════════════════════
    events = []

    # 1. UPTREND — صعود سابق (قبل 5-10 أيام)
    uptrend = False
    if mom10_6d is not None and mom10_6d > 5:
        events.append('UPTREND')
        uptrend = True
    elif mom10_now > 3 and rsi_10d_ago >= 55:
        events.append('UPTREND')
        uptrend = True

    # 2. DISTRIBUTION — توزيع
    if mom10_now > 5 and rsi_now >= 65 and vol_r_now > 1.5:
        events.append('DISTRIBUTION')

    # 3. SHARP_DROP — هبوط حاد مؤخراً
    sharp_drop = False
    if mom5_now <= -5:
        events.append('SHARP_DROP')
        sharp_drop = True
    elif mom5_now <= -3 and rsi_3d_ago >= 45:
        events.append('SHARP_DROP')
        sharp_drop = True

    # 4. VELOCITY_EXHAUSTION — تسارع الهبوط (NEW alpha v2)
    # drop_accel < -3 = هبط أسرع من اتجاهه → إرهاق وشيك
    # مثبَت: WR5=74.4%, stable 78.6%(2021-23) vs 74.3%(2024-26)
    vel_exhaustion = False
    if drop_accel < -3 and mom10_now < -3:
        events.append('VELOCITY_EXHAUSTION')
        vel_exhaustion = True
    elif drop_accel < -5:
        events.append('VELOCITY_EXHAUSTION')
        vel_exhaustion = True

    # 5. PANIC — ذعر (ATR spike — أهم من Vol spike)
    # Discovered: ATR spike أفضل كاشف للذعر من Vol (Vol EXTREME → flip coin)
    panic = False
    if atr_z >= 1.5:
        events.append('PANIC')
        panic = True
    elif atr_z >= 1.0 and vol_r_now >= 2.0:
        events.append('PANIC')
        panic = True

    # 6. EXHAUSTION — إرهاق البائعين (RSI oversold + RSI STILL FALLING)
    # Timing upgrade: RSI falling (slope<-3) = more exhaustion force than RSI flat
    exhaustion = False
    exhaustion_strong = False
    if rsi_now <= 30:
        events.append('EXHAUSTION')
        exhaustion = True
        # Timing bonus: RSI still falling = higher probability
        if rsi_slope3 is not None and rsi_slope3 < -3:
            events.append('RSI_STILL_FALLING')
            exhaustion_strong = True
    elif rsi_now <= 35 and mom5_now <= -5:
        events.append('EXHAUSTION')
        exhaustion = True

    # 7. RANGE_BOUND
    if not uptrend and not sharp_drop and abs(mom10_now) <= 2 and abs(mom5_now) <= 2:
        events.append('RANGE_BOUND')

    # 8. RECOVERY
    if mom5_now >= 3 and rsi_now >= 35 and rsi_10d_ago <= 30:
        events.append('RECOVERY')

    # ══════════════════════════════════════════════════════════════════════════
    # SEQUENCE SCORING v2
    # Weights updated from empirical data (68,949 bars × 251 symbols)
    # ══════════════════════════════════════════════════════════════════════════
    seq_score = 0

    # ── VELOCITY EXHAUSTION SIGNAL (NEW — WR5=74.4%, stable 2021-2026) ───────
    # RSI≤30 + drop_accel<-3 + RSI_slope<-3
    # This replaces "PANIC" in the golden sequence with physics-based exhaustion
    vel_gold = vel_exhaustion and exhaustion and sharp_drop and (uptrend or mom10_now < -5)
    if vel_gold:
        seq_score = 92   # حتى أفضل من golden standard
        state = 'VELOCITY_REVERSAL'   # أقوى إشارة في النظام

    # التسلسل الذهبي الكلاسيكي مع timing bonus
    elif uptrend and sharp_drop and panic and exhaustion:
        seq_score = 90 if exhaustion_strong else 85
        state = 'HIGH_PROB_REVERSAL'

    # VELOCITY_EXHAUSTION بدون UPTREND مؤكَّد
    elif vel_exhaustion and exhaustion and sharp_drop:
        seq_score = 80
        state = 'VELOCITY_REVERSAL_LIKELY'

    # 3-أحداث: DROP+PANIC+EXHAUSTION
    elif sharp_drop and panic and exhaustion:
        seq_score = 75 if exhaustion_strong else 68
        state = 'LIKELY_REVERSAL'

    # VELOCITY + EXHAUSTION (بدون sharp_drop صريح)
    elif vel_exhaustion and exhaustion:
        seq_score = 65
        state = 'VELOCITY_EXHAUSTION_SETUP'

    # 2-أحداث: PANIC + EXHAUSTION
    elif panic and exhaustion:
        seq_score = 55
        state = 'POSSIBLE_REVERSAL'

    # EXHAUSTION وحده مع timing (RSI still falling)
    elif exhaustion and exhaustion_strong and not panic:
        seq_score = 45
        state = 'OVERSOLD_TIMING'   # WR5≈69% due to RSI still falling

    # EXHAUSTION وحده (RSI flat) → WR=45.9% — منخفض
    elif exhaustion and not panic and not exhaustion_strong:
        seq_score = 25 if not sharp_drop else 35
        state = 'OVERSOLD_WEAK' if not sharp_drop else 'OVERSOLD_MODERATE'

    # SHARP_DROP بدون exhaustion
    elif sharp_drop and not exhaustion:
        seq_score = 10
        state = 'FALLING_KNIFE'

    # DISTRIBUTION
    elif 'DISTRIBUTION' in events:
        seq_score = 20
        state = 'DISTRIBUTION'

    # RECOVERY
    elif 'RECOVERY' in events:
        seq_score = 50
        state = 'RECOVERY'

    else:
        seq_score = 5
        state = 'NEUTRAL'

    # ── Bonus adjustments (data-driven) ──────────────────────────────────────
    # BB oversold يرفع الـ score
    if bb_now < 0.1 and seq_score > 20:
        seq_score = min(seq_score + 8, 100)

    # ATR > 2% (medium volatility — optimal zone for reversals)
    # مُثبَت: ATR 1-2%: WR=66.3%, ATR 2-3%: WR=64.9%
    if atr_now > 1 and seq_score > 20:
        seq_score = min(seq_score + 5, 100)

    # Volume DEAD (<0.5x): إشارة تساؤل — قد تكون CB-forced
    # Volume EXTREME (>3x): flip-coin → لا bonus (بخلاف مفهومنا القديم)
    # ONLY moderate vol (0.5-2x) يُعطَى bonus
    if 0.5 <= vol_r_now <= 2.0 and seq_score > 30:
        seq_score = min(seq_score + 4, 100)

    # Regime flat rejection: حتى لو اجتاز كل الفلاتر، إذا mom10 قريب من صفر
    if -1 <= mom10_now <= 1 and seq_score > 50:
        seq_score = int(seq_score * 0.6)  # تخفيض 40%

    return {
        'events':          events,
        'state':           state,
        'sequence_score':  int(seq_score),
        'full_gold':       bool(uptrend and sharp_drop and panic and exhaustion),
        'vel_gold':        bool(vel_gold),
        'drop_accel':      round(drop_accel, 2),
        'rsi_slope3':      round(rsi_slope3, 2) if rsi_slope3 is not None else None,
        'details': {
            'rsi_now':    round(rsi_now, 1),
            'mom5_now':   round(mom5_now, 2),
            'mom10_now':  round(mom10_now, 2),
            'atr_z':      round(atr_z, 2),
            'vol_r_now':  round(vol_r_now, 2),
            'bb_now':     round(bb_now, 3),
            'rsi_10d_ago':round(rsi_10d_ago, 1),
            'drop_accel': round(drop_accel, 2),
            'rsi_slope3': round(rsi_slope3, 2) if rsi_slope3 is not None else None,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# HYBRID REGIME ENGINE v2 — Multi-Factor, Leading (not Lagging)
# يجمع: macro_regime + market_breadth + volatility_spike
# ═══════════════════════════════════════════════════════════════════════════

def get_market_regime_v2():
    """
    Hybrid Regime v2 — يتفوق على mom5 اللاحق بإضافة:

    Factor 1 — Market Breadth (LEADING):
      % الأسهم التي سعرها > MA20
      عندما تنخفض من >70% إلى <40% → signal قبل الانعكاس بـ 2-3 أيام

    Factor 2 — Cross-Sectional Volatility Spike:
      متوسط ATR z-score عبر الكون
      Spike > 1.5 → ذعر → فرصة (leading)

    Factor 3 — macro_regime (mom5 — lagging):
      نفس get_market_regime() كـ sanity check

    المخرج:
      regime_v2: CRASH / DOWN / SIDEWAYS / UP / SURGE / UNKNOWN
      confidence: HIGH / MEDIUM / LOW (كم من العوامل تتفق)
      leading_signal: True إذا breadth أو volatility يختلفان عن mom5
    """
    try:
        con = get_connection()

        # ── تحميل بيانات الأسهم (آخر 30 يوم) ──────────────────────────────
        df = pd.read_sql("""
            SELECT symbol, bar_time, close, volume
            FROM ohlcv_history
            ORDER BY bar_time DESC
            LIMIT 20000
        """, con)

        # ── تحميل ATR من indicators_cache ───────────────────────────────────
        atr_df = pd.read_sql("""
            WITH latest AS (
                SELECT symbol, MAX(bar_date) as max_date
                FROM indicators_cache GROUP BY symbol
            )
            SELECT ic.symbol, ic.atr14, ic.momentum_5d, ic.rsi14
            FROM indicators_cache ic
            JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
        """, con)
        con.close()

        if df.empty or len(df) < 200:
            return {'regime_v2': 'UNKNOWN', 'confidence': 'LOW', 'error': 'بيانات غير كافية'}

        df = df.sort_values(['symbol', 'bar_time'])

        # ══════════════════════════════════════════════════════════════════════
        # FACTOR 1: Market Breadth — % فوق MA20
        # ══════════════════════════════════════════════════════════════════════
        breadth_scores = []
        for sym, g in df.groupby('symbol'):
            g = g.sort_values('bar_time')
            if len(g) < 22: continue
            close_vals = g['close'].values
            ma20 = np.mean(close_vals[-20:])
            last = close_vals[-1]
            breadth_scores.append(1 if last > ma20 else 0)

        breadth_pct = (sum(breadth_scores) / len(breadth_scores) * 100) if breadth_scores else 50

        # تفسير الـ breadth
        if breadth_pct < 25:
            breadth_regime = 'CRASH'
            breadth_note   = f'❌ {breadth_pct:.0f}% فوق MA20 — سوق في انهيار'
        elif breadth_pct < 40:
            breadth_regime = 'DOWN'
            breadth_note   = f'📉 {breadth_pct:.0f}% فوق MA20 — سوق في هبوط'
        elif breadth_pct < 55:
            breadth_regime = 'SIDEWAYS'
            breadth_note   = f'↔️  {breadth_pct:.0f}% فوق MA20 — سوق محايد'
        elif breadth_pct < 70:
            breadth_regime = 'UP'
            breadth_note   = f'📈 {breadth_pct:.0f}% فوق MA20 — سوق في صعود'
        else:
            breadth_regime = 'SURGE'
            breadth_note   = f'🚀 {breadth_pct:.0f}% فوق MA20 — سوق في اندفاع'

        # ══════════════════════════════════════════════════════════════════════
        # FACTOR 2: Cross-Sectional Volatility Spike (ATR z-scores)
        # ══════════════════════════════════════════════════════════════════════
        vol_regime    = 'NORMAL'
        vol_note      = ''
        avg_atr_z     = None

        if not atr_df.empty and 'atr14' in atr_df.columns:
            atrs = atr_df['atr14'].dropna()
            if len(atrs) >= 20:
                atr_mean  = atrs.mean()
                atr_std   = atrs.std()
                atr_zs    = ((atrs - atr_mean) / atr_std).dropna()
                avg_atr_z = float(atr_zs.mean())
                # % الأسهم ذات ATR z > 1.5 (ذعر محلي)
                pct_panicking = (atr_zs > 1.5).mean() * 100

                if pct_panicking >= 30:
                    vol_regime = 'CRASH'
                    vol_note   = f'🚨 {pct_panicking:.0f}% أسهم في ذعر (ATR spike) — فرصة قريبة'
                elif pct_panicking >= 15:
                    vol_regime = 'DOWN'
                    vol_note   = f'⚠️  {pct_panicking:.0f}% أسهم ATR مرتفع — ضغط بيع'
                elif pct_panicking <= 5 and avg_atr_z < -0.5:
                    vol_regime = 'SURGE'
                    vol_note   = f'📊 تقلب منخفض جداً — سوق هادئ/ممتد'
                else:
                    vol_note   = f'📊 {pct_panicking:.0f}% أسهم ATR عالٍ — طبيعي'

        # ══════════════════════════════════════════════════════════════════════
        # FACTOR 3: Momentum (legacy — lagging sanity check)
        # ══════════════════════════════════════════════════════════════════════
        mom5_list = []
        if not atr_df.empty and 'momentum_5d' in atr_df.columns:
            mom5_list = atr_df['momentum_5d'].dropna().tolist()
        mkt_mom5 = float(np.median(mom5_list)) if mom5_list else 0

        if mkt_mom5 <= -5:   mom_regime = 'CRASH'
        elif mkt_mom5 <= -2: mom_regime = 'DOWN'
        elif mkt_mom5 >= 5:  mom_regime = 'SURGE'
        elif mkt_mom5 >= 2:  mom_regime = 'UP'
        else:                mom_regime = 'SIDEWAYS'

        # ══════════════════════════════════════════════════════════════════════
        # HYBRID COMBINATION — أولوية: Breadth > Volatility > Momentum
        # ══════════════════════════════════════════════════════════════════════
        REGIME_ORDER = {'CRASH': 0, 'DOWN': 1, 'SIDEWAYS': 2, 'UP': 3, 'SURGE': 4}

        scores = {'CRASH': 0, 'DOWN': 0, 'SIDEWAYS': 0, 'UP': 0, 'SURGE': 0, 'UNKNOWN': 0}
        # Breadth يُعطَى وزن 40%
        scores[breadth_regime] = scores.get(breadth_regime, 0) + 40
        # Volatility يُعطَى وزن 35% (إذا محدَّد)
        if vol_regime != 'NORMAL':
            scores[vol_regime] = scores.get(vol_regime, 0) + 35
        else:
            # Volatility عادي → يُصوّت لـ breadth
            scores[breadth_regime] = scores.get(breadth_regime, 0) + 17
        # Momentum يُعطَى وزن 25%
        scores[mom_regime] = scores.get(mom_regime, 0) + 25

        regime_v2 = max(scores, key=scores.get)

        # ── Confidence — كم من الععوامل تتفق؟ ──────────────────────────────
        regimes_voted = [breadth_regime, vol_regime if vol_regime != 'NORMAL' else mom_regime, mom_regime]
        unique_votes  = set(regimes_voted)
        if len(unique_votes) == 1:
            confidence = 'HIGH'
        elif len(unique_votes) == 2:
            confidence = 'MEDIUM'
        else:
            confidence = 'LOW'

        # ── Leading Signal — هل Breadth تتعارض مع Momentum? ────────────────
        leading_signal = (REGIME_ORDER.get(breadth_regime, 2) != REGIME_ORDER.get(mom_regime, 2))
        if leading_signal:
            b_idx = REGIME_ORDER.get(breadth_regime, 2)
            m_idx = REGIME_ORDER.get(mom_regime, 2)
            leading_note = (
                f'⚡ LEADING: Breadth={breadth_regime} أكثر هبوطاً من Momentum={mom_regime} → تحذير مبكر'
                if b_idx < m_idx else
                f'⚡ LEADING: Breadth={breadth_regime} أكثر صعوداً من Momentum={mom_regime} → تعافٍ محتمل'
            )
        else:
            leading_note = f'✅ Breadth و Momentum متفقان → {regime_v2}'

        # ── RSI threshold + multiplier ───────────────────────────────────────
        regime_config = {
            'CRASH':    {'rsi_t': 40, 'mult': 1.35, 'quality': '🟢 STRONG BUY — breadth + panic'},
            'DOWN':     {'rsi_t': 32, 'mult': 1.15, 'quality': '🟡 SELECTIVE BUY'},
            'SIDEWAYS': {'rsi_t': 30, 'mult': 0.70, 'quality': '🟠 WAIT'},
            'UP':       {'rsi_t': 25, 'mult': 0.55, 'quality': '🔴 AVOID'},
            'SURGE':    {'rsi_t': 20, 'mult': 0.40, 'quality': '🔴 AVOID — WR5=21%'},
            'UNKNOWN':  {'rsi_t': 30, 'mult': 0.85, 'quality': '❓ UNCERTAIN'},
        }.get(regime_v2, {'rsi_t': 30, 'mult': 0.85, 'quality': '❓'})

        return {
            'regime_v2':      regime_v2,
            'confidence':     confidence,
            'leading_signal': leading_signal,
            'leading_note':   leading_note,
            # Factor details
            'breadth_pct':    round(breadth_pct, 1),
            'breadth_regime': breadth_regime,
            'breadth_note':   breadth_note,
            'vol_regime':     vol_regime,
            'vol_note':       vol_note,
            'avg_atr_z':      round(avg_atr_z, 2) if avg_atr_z is not None else None,
            'mom_regime':     mom_regime,
            'mkt_mom5':       round(mkt_mom5, 2),
            # Signal config
            'rsi_threshold':  regime_config['rsi_t'],
            'regime_mult':    regime_config['mult'],
            'signal_quality': regime_config['quality'],
            'n_breadth_stocks': len(breadth_scores),
        }

    except Exception as e:
        import traceback
        return {'regime_v2': 'UNKNOWN', 'confidence': 'LOW', 'error': str(e),
                'traceback': traceback.format_exc()[-300:]}


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND: event_signals — إشارات مبنية على تسلسل الأحداث (Event-Based Engine)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_event_signals(params):
    """
    يُشغِّل محرك الأحداث على كل الأسهم ويُعيد:
      - HIGH_PROB_REVERSAL: التسلسل الذهبي (UPTREND+DROP+PANIC+EXHAUSTION) → WR5~62%
      - LIKELY_REVERSAL:    3-أحداث → WR5~44.5%
      - POSSIBLE_REVERSAL:  PANIC+EXHAUSTION → WR5~39.2%
      - OVERSOLD_MODERATE:  DROP+EXHAUSTION → WR5~37%
      - OVERSOLD_WEAK:      RSI وحده → WR5~35.3%

    يستخدم OHLCV الكاملة (path-aware) + indicators_cache
    يدمج مع Hybrid Regime v2 للفلترة النهائية

    Params:
      min_score  (default 45) — أدنى sequence_score للإدراج في النتائج
      top_n      (default 20) — أعلى N إشارة
      only_gold  (bool)       — أعد التسلسل الذهبي فقط
    """
    min_score = int(params.get('min_score', 45))
    top_n     = int(params.get('top_n',     20))
    only_gold = bool(params.get('only_gold', False))

    con = get_connection()

    # تحميل indicators_cache (آخر يوم لكل سهم)
    ic = pd.read_sql("""
        WITH latest AS (
            SELECT symbol, MAX(bar_date) as max_date
            FROM indicators_cache GROUP BY symbol
        )
        SELECT ic.*
        FROM indicators_cache ic
        JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
    """, con)

    # تحميل OHLCV الكاملة (آخر 30 يوم لكل سهم) للمسار التاريخي
    ohlcv = pd.read_sql("""
        SELECT symbol, bar_time, close, volume
        FROM ohlcv_history
        ORDER BY bar_time DESC
        LIMIT 30000
    """, con)
    con.close()

    if ic.empty:
        return {"error": "indicators_cache فارغ"}

    # فهرسة OHLCV بالسهم
    ohlcv = ohlcv.sort_values(['symbol', 'bar_time'])
    ohlcv_by_sym = {sym: g.tail(30).reset_index(drop=True) for sym, g in ohlcv.groupby('symbol')}

    # دمج indicators مع OHLCV لكل سهم
    ic_dict = ic.set_index('symbol').to_dict('index')

    # ── Hybrid Regime v2 ──────────────────────────────────────────────────
    regime_v2 = get_market_regime_v2()
    r2_name   = regime_v2.get('regime_v2', 'UNKNOWN')
    r2_mult   = regime_v2.get('regime_mult', 0.85)
    r2_rsi_t  = regime_v2.get('rsi_threshold', 30)

    # ── Macro Regime Factor ────────────────────────────────────────────────
    macro_ctx  = _load_macro_context(max_age_hours=168)   # 7 أيام
    macro_mult, macro_regime_name = _macro_regime_factor(macro_ctx)
    # دمج الـ mult (ريجيم السوق × ريجيم الاقتصاد الكلي)
    combined_mult = round(r2_mult * macro_mult, 3)

    results = []

    for sym, ic_row in ic_dict.items():
        df_stock = ohlcv_by_sym.get(sym)

        if df_stock is None or len(df_stock) < 6:
            continue

        # دمج indicators في df_stock
        for col in ['rsi14', 'adx14', 'atr14', 'vol_ratio_20', 'bb_position',
                    'momentum_5d', 'momentum_10d']:
            if col in ic_row:
                df_stock = df_stock.copy()
                df_stock[col] = ic_row.get(col)

        seq = detect_event_sequence(df_stock)

        if only_gold and not seq.get('full_gold'):
            continue

        score = seq['sequence_score']
        if score < min_score:
            continue

        # ── Transition-Aware Score Upgrade (Markov Engine) ────────────────
        # P(TRUE_REVERSAL) تجريبياً من نموذج انتقال الحالات (66K شمعة)
        # يُرفع الـ score بناءً على حالة ماركوف + regime فعلياً لا rules جامدة
        ev_state  = seq.get('state', 'NEUTRAL')
        cur_regime= r2_name  # CRASH / DOWN / NEUTRAL / UP / SURGE

        # P_TR المُبرمجة تجريبياً من cmd_state_transitions
        # (يُحدَّث دورياً من نتائج التحليل)
        EMPIRICAL_PTR = {
            # (state, regime) → posterior P(TR)%  ← Bayesian adaptive memory (2026-05-07)
            # VELOCITY_EXHAUSTION — P_hist كانت أعلى بكثير، لكن Posterior بعد decay=0.7 أصغر
            ('VELOCITY_EXHAUSTION', 'CRASH'):    40.7,   # WEAKENING! (P_hist=56.1 → now 40.7, Δ=-20.1%)
            ('VELOCITY_EXHAUSTION', 'DOWN'):     39.5,   # WEAKENING  (Δ=-33.4%)
            ('VELOCITY_EXHAUSTION', 'NEUTRAL'):  43.7,   # STRENGTHENING (Δ=+18.9%)
            ('VELOCITY_EXHAUSTION', 'UP'):       50.7,   # STABLE
            ('EXHAUSTION',          'CRASH'):    50.9,   # STABLE ← أقوى حافة (HIGH conf, IMPROVING fail)
            ('EXHAUSTION',          'DOWN'):     44.0,   # MILD_STRENGTH
            ('EXHAUSTION',          'NEUTRAL'):  30.5,   # الأضعف
            ('EXHAUSTION',          'UP'):       40.2,   # STRENGTHENING (n_recent صغير)
            ('PANIC',               'CRASH'):    35.4,   # WEAKENING  (Δ=-26.9%)
            ('PANIC',               'DOWN'):     39.5,   # WEAKENING  (Δ=-12%)
            ('PANIC',               'UP'):       43.8,   # MILD_STRENGTH
            ('POTENTIAL_BOUNCE',    'CRASH'):    48.0,   # STABLE
            ('POTENTIAL_BOUNCE',    'UP'):       48.1,   # MODERATE edge
            ('STABILIZATION',       'CRASH'):    38.2,   # MILD_STRENGTH
            ('STABILIZATION',       'DOWN'):     40.0,   # —
        }

        # المسار الذهبي: PANIC في آخر 3 أيام + VEL_EXHAUS اليوم + CRASH/DOWN
        prior_events = seq.get('events', [])
        is_golden_path = (
            'VELOCITY_EXHAUSTION' in ev_state and
            cur_regime in ('CRASH', 'DOWN') and
            'PANIC' in prior_events
        )

        p_tr = EMPIRICAL_PTR.get((ev_state, cur_regime))
        transition_label = None

        if is_golden_path:
            # المسار الذهبي PANIC→VEL_EXHAUS→CRASH — P(TR)=88.5% تجريبياً
            score        = max(score, 92)
            transition_label = 'GOLDEN_MARKOV'
        elif p_tr is not None and p_tr >= 48.0:
            # Bayesian posterior >= 48% → حافة موثوقة
            # EXHAUSTION+CRASH=50.9%, POTENTIAL_BOUNCE+UP=48.1%, VEL_EXHAUS+UP=50.7%
            score_boost  = int((p_tr - 42) * 0.9)   # +5 لـ 48%, +8 لـ 51%
            score        = min(score + score_boost, 90)
            transition_label = f'MARKOV_P{int(p_tr)}'
        elif p_tr is not None and p_tr < 36.0:
            # posterior منخفض — الحالة ضعيفة أو تضعف
            # PANIC+CRASH=35.4% (WEAKENING), EXHAUSTION+NEUTRAL=30.5%
            score        = int(score * 0.78)
            transition_label = 'MARKOV_WEAK'

        if score < min_score:
            continue

        # تعديل الـ score: ريجيم السوق × ريجيم الاقتصاد الكلي
        adj_score = min(int(score * combined_mult), 100)

        results.append({
            'symbol':           sym,
            'state':            seq['state'],
            'events':           seq['events'],
            'sequence_score':   score,
            'adj_score':        adj_score,
            'full_gold':        seq.get('full_gold', False),
            'vel_gold':         is_golden_path,
            'transition_label': transition_label,
            'p_true_rev':       p_tr,
            'rsi14':            round(ic_row.get('rsi14', 50) or 50, 1),
            'mom5':             round(ic_row.get('momentum_5d', 0) or 0, 2),
            'atr14':            round(ic_row.get('atr14', 2) or 2, 2),
            'vol_ratio_20':     round(ic_row.get('vol_ratio_20', 1) or 1, 2),
            'bb_position':      round(ic_row.get('bb_position', 0.5) or 0.5, 3),
            'atr_z':            seq['details']['atr_z'],
            'sector':           ic_row.get('sector', ''),
        })

    # ترتيب: vel_gold أولاً ثم full_gold ثم adj_score
    results.sort(key=lambda x: (
        not x.get('vel_gold', False),
        not x['full_gold'],
        -x['adj_score']
    ))

    # إحصائيات v2 — تشمل VELOCITY_REVERSAL كـ tier أعلى
    vel_gold_count  = sum(1 for r in results if r.get('vel_gold', False))
    gold_count      = sum(1 for r in results if r['full_gold'])
    vel_rev_count   = sum(1 for r in results if 'VELOCITY_REVERSAL' in r['state'])
    likely_count    = sum(1 for r in results if r['state'] in ('LIKELY_REVERSAL','VELOCITY_REVERSAL_LIKELY'))
    possible_count  = sum(1 for r in results if r['state'] == 'POSSIBLE_REVERSAL')
    weak_count      = sum(1 for r in results if 'OVERSOLD' in r['state'])
    timing_count    = sum(1 for r in results if r['state'] == 'OVERSOLD_TIMING')

    import datetime
    today_str = datetime.date.today().isoformat()

    return {
        'date':           today_str,
        'regime_v2':      regime_v2,
        'macro_context': {
            'macro_regime':      macro_regime_name,
            'equity_multiplier': macro_mult,
            'combined_mult':     combined_mult,
            'real_interest_rate': macro_ctx.get('real_interest_rate') if macro_ctx else None,
            'inflation_yoy':     (macro_ctx.get('inflation_yoy') or macro_ctx.get('inflation_pct')) if macro_ctx else None,
            'cbe_rate':          (macro_ctx.get('cbe_rate') or macro_ctx.get('cbe_rate_pct')) if macro_ctx else None,
            'usd_egp':           macro_ctx.get('usd_egp') if macro_ctx else None,
            'gdp_yoy':           macro_ctx.get('gdp_yoy') if macro_ctx else None,
            'strategic_bias':    macro_ctx.get('strategic_bias') if macro_ctx else None,
            'inflation_momentum': macro_ctx.get('inflation_momentum') if macro_ctx else None,
            'rate_cycle':        macro_ctx.get('rate_cycle') if macro_ctx else None,
            'fx_reserves_b':     macro_ctx.get('fx_reserves_b') if macro_ctx else None,
        } if macro_ctx else {'error': 'no macro data'},
        'summary': {
            'total_screened':      len(ic_dict),
            'total_signals':       len(results),
            'velocity_reversal':   vel_rev_count,   # NEW — WR5~74-79%
            'high_prob_reversal':  gold_count,       # classic gold
            'likely_reversal':     likely_count,
            'possible_reversal':   possible_count,
            'oversold_timing':     timing_count,     # RSI still falling
            'oversold_weak':       weak_count,
        },
        'signals':        results[:top_n],
        'gold_signals':   [r for r in results if r['full_gold'] or r.get('vel_gold')][:10],
        'methodology': (
            f'Event-Based Engine v2 + Markov + Macro | Regime={r2_name}(×{r2_mult:.2f}) × '
            f'Macro={macro_regime_name}(×{macro_mult:.3f}) = CombMult={combined_mult:.3f} | '
            f'RSI_t={r2_rsi_t} | Breadth={regime_v2.get("breadth_pct","?")}% | '
            f'RealRate={macro_ctx.get("real_interest_rate") if macro_ctx else "?"}'
        ),
        'note': (
            f'🚀 {vel_rev_count} VELOCITY_REVERSAL (WR~75%)! + {gold_count} HIGH_PROB! | '
            f'{likely_count} LIKELY | {possible_count} POSSIBLE'
            if vel_rev_count > 0 or gold_count > 0 else
            f'📊 {likely_count} LIKELY | {possible_count} POSSIBLE | '
            f'{timing_count} OVERSOLD_TIMING | Regime={r2_name}'
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# META-VALIDATION: Temporal Stability Test
# يرفض أي strategy غير مستقرة عبر الزمن (overfit في regime معين)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_stability_test(params):
    """
    يُقيّم استقرار الاستراتيجيات عبر فترتين زمنيتين مختلفتين:
      Period A: 2021-2023 (EGX في بيئة مختلطة — تضخم صاعد، تثبيت سعر صرف)
      Period B: 2024-2026 (EGX bull — تعويم + تدفقات)

    الحكم:
      ✅ STABLE:   |WR_A - WR_B| < 10%  → alpha حقيقي
      ⚠️  MILD:    |WR_A - WR_B| 10-20% → يعمل لكن حذر
      ❌ UNSTABLE: |WR_A - WR_B| > 20%  → regime artifact → رفض

    المخرجات:
      - استقرار كل strategy
      - الأفضل عبر كلا الفترتين
      - قائمة بالاستراتيجيات المرفوضة
    """
    con = get_connection()
    oh = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)
    con.close()

    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية"}

    def rsi14(c):
        d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
        ag=g.ewm(com=13,min_periods=14).mean(); al=l.ewm(com=13,min_periods=14).mean()
        return 100-100/(1+ag/al.replace(0,np.nan))

    def atr14(h,l,c):
        tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        return tr.ewm(com=13,min_periods=14).mean()

    parts=[]
    for sym,g in oh.groupby('symbol'):
        g=g.sort_values('bar_time').reset_index(drop=True)
        if len(g)<30: continue
        g['rsi']=rsi14(g['close'])
        g['atr_pct']=atr14(g['high'],g['low'],g['close'])/g['close']*100
        g['mom5']=g['close'].pct_change(5)*100
        g['mom10']=g['close'].pct_change(10)*100
        g['vol_r']=g['volume']/g['volume'].rolling(20,min_periods=5).mean()
        g['drop_accel']=g['mom5']-g['mom10']/2
        g['rsi_slope3']=g['rsi'].diff(3)
        g['year']=pd.to_datetime(g['bar_time'],unit='s').dt.year
        for d in [5,10]:
            g[f'fwd{d}']=g['close'].shift(-d)/g['close']-1
        parts.append(g)

    df=pd.concat(parts,ignore_index=True).dropna(subset=['rsi','mom5','mom10','drop_accel','fwd5'])
    n_sym=df['symbol'].nunique()
    yrs=(df['bar_time'].max()-df['bar_time'].min())/(365.25*86400)

    # تعريف الاستراتيجيات المُختبَرة
    strategies = {
        'RSI≤30_alone':           (df['rsi']<=30),
        'RSI≤30+mom10<-5':        (df['rsi']<=30)&(df['mom10']<-5),
        'RSI≤30+mom5<-5+ATR>1':   (df['rsi']<=30)&(df['mom5']<-5)&(df['atr_pct']>1),
        'VEL_ACCEL(accel<-3+rsi30+atr1)':
            (df['drop_accel']<-3)&(df['rsi']<=30)&(df['atr_pct']>1),
        'PATH(rsi30+m10<-5+m5<-7)':
            (df['rsi']<=30)&(df['mom10']<-5)&(df['mom5']<-7)&(df['atr_pct']>1),
        'RSI≤35+mom5<-5+ATR>1':   (df['rsi']<=35)&(df['mom5']<-5)&(df['atr_pct']>1),
        'RSI≤35+m10<-5+accel<-2':
            (df['rsi']<=35)&(df['mom10']<-5)&(df['drop_accel']<-2)&(df['atr_pct']>1),
        'VEL+RSI_FALLING(accel<-3+slope<-3)':
            (df['drop_accel']<-3)&(df['rsi']<=30)&(df['rsi_slope3']<-3)&(df['atr_pct']>1),
        # PROPOSED: Momentum Flip (صاعد طويل + هبوط قصير)
        'MOM_FLIP(mom20>0+m10<-5+rsi30)':
            (df['rsi']<=30)&(df['mom10']<-5)&(df['mom5']<-5)&df['mom10'].notna(),
    }

    results = []
    stable  = []
    unstable= []

    for label, mask in strategies.items():
        s      = df[mask]
        early  = s[s['year'].isin([2021,2022,2023])]
        late   = s[s['year'].isin([2024,2025,2026])]
        n_all  = len(s)
        freq   = n_all / (n_sym * yrs) if n_sym > 0 else 0

        if n_all < 20:
            continue

        wr_all = float((s['fwd5']>0).mean()*100)
        avg_all= float(s['fwd5'].mean()*100)

        wr_e   = float((early['fwd5']>0).mean()*100) if len(early)>=10 else None
        wr_l   = float((late['fwd5']>0).mean()*100)  if len(late)>=10  else None

        if wr_e is not None and wr_l is not None:
            gap    = abs(wr_e - wr_l)
            if gap < 10:   stability = 'STABLE'
            elif gap < 20: stability = 'MILD'
            else:          stability = 'UNSTABLE'
        elif wr_l is not None:
            gap = None; stability = 'NEW_STRATEGY'  # not enough early data
        else:
            gap = None; stability = 'UNKNOWN'

        row = {
            'strategy':    label,
            'n':           n_all,
            'wr5_all':     round(wr_all, 1),
            'avg5_all':    round(avg_all, 2),
            'wr5_2021_23': round(wr_e, 1) if wr_e is not None else None,
            'wr5_2024_26': round(wr_l, 1) if wr_l is not None else None,
            'gap_pct':     round(gap, 1) if gap is not None else None,
            'stability':   stability,
            'freq_per_yr': round(freq, 2),
            'tradeable':   freq >= 0.5,
        }
        results.append(row)
        if stability == 'STABLE' and freq >= 0.5:
            stable.append(label)
        elif stability == 'UNSTABLE':
            unstable.append(label)

    # ترتيب: stable أولاً ثم WR
    results.sort(key=lambda x: (
        0 if x['stability']=='STABLE' else (1 if x['stability']=='MILD' else 2),
        -x['wr5_all']
    ))

    return {
        'results':           results,
        'stable_strategies': stable,
        'unstable_rejected': unstable,
        'dataset': {
            'n_rows': len(df), 'n_symbols': n_sym,
            'years': round(yrs, 1),
        },
        'recommendation': (
            f"✅ أفضل استراتيجيات مستقرة: {' | '.join(stable[:3])}"
            if stable else
            '⚠️  لا توجد استراتيجيات مستقرة بتردد كافٍ — راجع المعايير'
        ),
        'note': (
            f"🚨 مرفوضة لعدم الاستقرار عبر الزمن: {', '.join(unstable)}"
            if unstable else
            '✅ كل الاستراتيجيات تجتاز اختبار الاستقرار'
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# MARKET STATE TRANSITION ENGINE — Markov Chain Behavioral Model
# يُصنّف كل شمعة إلى 10 حالات ويبني مصفوفة الانتقال الاحتمالية
# P(next_state | current_state) + P(TRUE_REVERSAL | state + regime)
# ═══════════════════════════════════════════════════════════════════════════

def cmd_state_transitions(params):
    """
    نموذج انتقال الحالات السوقية — Markov Chain على 68K+ شمعة.

    المخرجات:
      transition_matrix   — P(next_state | current_state) لـ 5 أشرطة للأمام
      regime_conditional  — P(TRUE_REVERSAL | state + market_regime)
      discriminant        — الشروط المُفرِّقة بين TRUE_REVERSAL و DEAD_CAT_BOUNCE
      state_duration      — متوسط/وسيط مدة كل حالة
      current_states      — الحالة الحالية لكل سهم + احتمال الانعكاس
      golden_sequences    — مسارات الانتقال عالية الاحتمال
      dataset             — إحصاء البيانات
    """
    fwd_bars = int(params.get('fwd_bars', 5))   # نافذة الانعكاس الأمامية

    con = get_connection()
    oh  = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)
    con.close()

    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية للنموذج"}

    # ── حساب المؤشرات ───────────────────────────────────────────────────────
    def _rsi14(c):
        d  = c.diff()
        g  = d.clip(lower=0); l = -d.clip(upper=0)
        ag = g.ewm(com=13, min_periods=14).mean()
        al = l.ewm(com=13, min_periods=14).mean()
        return 100 - 100 / (1 + ag / al.replace(0, np.nan))

    def _atr14(h, lo, c):
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(com=13, min_periods=14).mean()

    parts = []
    for sym, g in oh.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        if len(g) < 40:
            continue
        g['rsi']       = _rsi14(g['close'])
        atr_abs        = _atr14(g['high'], g['low'], g['close'])
        g['atr_pct']   = atr_abs / g['close'] * 100
        g['vol_r']     = g['volume'] / g['volume'].rolling(20, min_periods=5).mean()
        g['mom5']      = g['close'].pct_change(5) * 100
        g['mom10']     = g['close'].pct_change(10) * 100
        g['mom20']     = g['close'].pct_change(20) * 100
        g['drop_accel']= g['mom5'] - g['mom10'] / 2.0
        g['rsi_slope'] = g['rsi'].diff(3)
        # ATR z-score (rolling 20-bar)
        atr_mu         = atr_abs.rolling(20, min_periods=10).mean()
        atr_sd         = atr_abs.rolling(20, min_periods=10).std()
        g['atr_z']     = (atr_abs - atr_mu) / atr_sd.replace(0, np.nan)
        # Forward returns
        for d in [fwd_bars, 10]:
            g[f'fwd{d}'] = g['close'].shift(-d) / g['close'] - 1
        g['symbol'] = sym
        parts.append(g)

    if not parts:
        return {"error": "لا توجد أسهم بيانات كافية"}

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=['rsi', 'mom5', 'mom10', 'atr_z', f'fwd{fwd_bars}']).copy()
    n_total = len(df)
    n_sym   = df['symbol'].nunique()

    # ── مُصنِّف الحالات (10 حالات) ──────────────────────────────────────────
    def classify_state(row):
        rsi     = row['rsi']
        mom5    = row['mom5']
        mom10   = row['mom10']
        mom20   = row.get('mom20', 0) or 0
        atr_z   = row['atr_z']
        atr_pct = row['atr_pct']
        vol_r   = row.get('vol_r', 1.0) or 1.0
        daccel  = row['drop_accel']
        rsi_sl  = row['rsi_slope']

        # TIER 1: EXTREME STATES
        if daccel < -5 and atr_z > 1.5 and rsi_sl < -5:
            return 'PANIC'
        if mom5 > 5 and mom10 > 5 and atr_z > 0.5 and rsi > 60:
            return 'ACCELERATING_UP'

        # TIER 2: EXHAUSTION / VELOCITY
        if daccel < -3 and rsi <= 35 and mom10 < -3:
            return 'VELOCITY_EXHAUSTION'
        if rsi <= 32 and mom5 < -3 and mom10 < -2:
            return 'EXHAUSTION'

        # TIER 3: DIRECTIONAL
        if mom5 < -7 and daccel < -2:
            return 'SHARP_DROP'
        if mom5 < -4 and mom10 < -5 and atr_z < 0.5 and rsi < 40:
            return 'CONTINUATION_DOWN'
        if mom20 > 5 and rsi > 60 and mom5 < mom10 * 0.5 and vol_r > 1.3:
            return 'DISTRIBUTION'

        # TIER 4: STABILIZATION / TRANSITION
        if 30 < rsi <= 45 and abs(mom5) < 3 and atr_z < 0 and rsi_sl >= -1:
            return 'STABILIZATION'
        if 35 < rsi <= 50 and mom5 > 2 and mom10 < -5:
            return 'POTENTIAL_BOUNCE'

        # TIER 5: UPTREND
        if mom5 > 2 and mom10 > 2 and rsi > 50:
            return 'TRENDING_UP'

        return 'NEUTRAL'

    df['state'] = df.apply(classify_state, axis=1)

    # ── حساب Market Regime لكل شريط ─────────────────────────────────────────
    # نستخدم snapshot بسيط: اتجاه mom5 الوسيطي cross-sectional
    # نُقسّم البيانات إلى نوافذ زمنية ثم نُحدّد الـ regime
    df['bar_dt'] = pd.to_datetime(df['bar_time'], unit='s').dt.date
    mkt_agg = (df.groupby('bar_dt')['mom5']
               .agg(mkt_mom5='median', n_sym='count')
               .reset_index())
    mkt_agg['breadth'] = (
        df[df['rsi'] > 50].groupby('bar_dt').size() /
        df.groupby('bar_dt').size()
    ).reindex(mkt_agg['bar_dt']).fillna(0.5).values

    def mkt_regime(m5, br):
        if   br >= 0.65 and m5 > 3:   return 'SURGE'
        elif br >= 0.55 and m5 > 1:   return 'UP'
        elif br <= 0.30 and m5 < -3:  return 'CRASH'
        elif br <= 0.40 and m5 < -1:  return 'DOWN'
        else:                          return 'NEUTRAL'

    mkt_agg['regime'] = mkt_agg.apply(
        lambda r: mkt_regime(r['mkt_mom5'], r['breadth']), axis=1)
    mkt_map = dict(zip(mkt_agg['bar_dt'], mkt_agg['regime']))
    df['regime'] = df['bar_dt'].map(mkt_map).fillna('NEUTRAL')

    # ── مصفوفة الانتقال ──────────────────────────────────────────────────────
    ALL_STATES = [
        'PANIC', 'ACCELERATING_UP', 'VELOCITY_EXHAUSTION', 'EXHAUSTION',
        'SHARP_DROP', 'CONTINUATION_DOWN', 'DISTRIBUTION',
        'STABILIZATION', 'POTENTIAL_BOUNCE', 'TRENDING_UP', 'NEUTRAL',
    ]

    # لكل سهم نُنشئ الانتقال: حالة الآن → حالة بعد fwd_bars
    trans_rows = []
    for sym, g in df.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        states = g['state'].values
        regimes= g['regime'].values
        n_g    = len(g)
        for i in range(n_g - fwd_bars):
            trans_rows.append({
                'from_state':  states[i],
                'to_state':    states[i + fwd_bars],
                'regime_now':  regimes[i],
                'fwd_ret':     float(g[f'fwd{fwd_bars}'].iloc[i]),
            })

    if not trans_rows:
        return {"error": "لا توجد انتقالات كافية"}

    tdf = pd.DataFrame(trans_rows)

    # مصفوفة الانتقال الاحتمالية P(to | from)
    trans_matrix = {}
    for fs in ALL_STATES:
        sub = tdf[tdf['from_state'] == fs]
        if len(sub) < 5:
            continue
        counts = sub['to_state'].value_counts()
        total  = len(sub)
        trans_matrix[fs] = {
            'total':       total,
            'transitions': {
                ts: {
                    'prob':    round(float(counts.get(ts, 0)) / total * 100, 1),
                    'count':   int(counts.get(ts, 0)),
                }
                for ts in ALL_STATES if counts.get(ts, 0) > 0
            },
            'top3': [
                {'state': s, 'prob': round(float(c) / total * 100, 1)}
                for s, c in counts.head(3).items()
            ],
            'avg_fwd_ret':  round(float(sub['fwd_ret'].mean() * 100), 2),
            'wr':           round(float((sub['fwd_ret'] > 0).mean() * 100), 1),
        }

    # ── الانعكاس الحقيقي: fwd > +3% ─────────────────────────────────────────
    TRUE_REV_THR = 0.03
    tdf['true_reversal']   = tdf['fwd_ret'] > TRUE_REV_THR
    tdf['dead_cat']        = (tdf['fwd_ret'] > 0.005) & (tdf['fwd_ret'] <= TRUE_REV_THR)
    tdf['cont_down']       = tdf['fwd_ret'] < -0.02

    # P(TRUE_REVERSAL | state + regime)
    reversal_states = ['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION', 'STABILIZATION']
    regime_conditional = {}
    for fs in reversal_states:
        regime_conditional[fs] = {}
        sub_s = tdf[tdf['from_state'] == fs]
        if len(sub_s) < 10:
            continue
        for reg in ['CRASH', 'DOWN', 'NEUTRAL', 'UP', 'SURGE']:
            sub_r = sub_s[sub_s['regime_now'] == reg]
            if len(sub_r) < 5:
                continue
            n_r = len(sub_r)
            regime_conditional[fs][reg] = {
                'n':             n_r,
                'p_true_rev':    round(float(sub_r['true_reversal'].mean() * 100), 1),
                'p_dead_cat':    round(float(sub_r['dead_cat'].mean() * 100), 1),
                'p_cont_down':   round(float(sub_r['cont_down'].mean() * 100), 1),
                'avg_fwd_ret':   round(float(sub_r['fwd_ret'].mean() * 100), 2),
            }

    # ── Discriminant: ما الذي يُفرِّق TRUE_REVERSAL من DEAD_CAT؟ ────────────
    exh_all = tdf[tdf['from_state'].isin(['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION'])]
    discriminant = {}
    if len(exh_all) > 50:
        # اندماج بيانات الـ indicators للفلترة
        exh_all = exh_all.copy()
        # GOLDEN PATH: PANIC قبل 3 أيام + VEL_EXHAUSTION اليوم + CRASH
        # نحتاج تعقُّب تسلسل الحالات لكل سهم
        golden_rows = []
        for sym, g in df.groupby('symbol'):
            g = g.sort_values('bar_time').reset_index(drop=True)
            st = g['state'].values
            re = g['regime'].values
            fr = g[f'fwd{fwd_bars}'].values
            n_g = len(g)
            for i in range(3, n_g - fwd_bars):
                if (st[i] in ('VELOCITY_EXHAUSTION', 'EXHAUSTION') and
                        re[i] in ('CRASH', 'DOWN') and
                        'PANIC' in st[max(0, i-3):i]):
                    golden_rows.append({
                        'from_state': st[i],
                        'regime':     re[i],
                        'fwd_ret':    float(fr[i]),
                        'true_rev':   float(fr[i]) > TRUE_REV_THR,
                    })

        if golden_rows:
            gdf = pd.DataFrame(golden_rows)
            n_g  = len(gdf)
            wr_g = float(gdf['true_rev'].mean() * 100)
            avg_g= float(gdf['fwd_ret'].mean() * 100)
        else:
            n_g = 0; wr_g = 0; avg_g = 0

        # RSI zone discriminant on exhaustion bars
        exh_merge = df[df['state'].isin(['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION'])].copy()
        # merge forward returns
        exh_merge = exh_merge[[
            'symbol', 'bar_time', 'rsi', 'atr_z', 'atr_pct',
            'drop_accel', 'rsi_slope', 'regime', f'fwd{fwd_bars}',
        ]].dropna()

        def disc_cond(mask, label):
            sub = exh_merge[mask]
            n   = len(sub)
            if n < 5:
                return None
            return {
                'label':       label,
                'n':           n,
                'p_true_rev':  round(float((sub[f'fwd{fwd_bars}'] > TRUE_REV_THR).mean() * 100), 1),
                'avg_fwd_ret': round(float(sub[f'fwd{fwd_bars}'].mean() * 100), 2),
            }

        em = exh_merge
        discriminant = {
            'all_exhaustion_baseline': disc_cond(
                em['rsi'] <= 35, 'ALL_EXHAUSTION'),
            'panic_vel_exh_crash': {
                'label': 'GOLDEN_SEQUENCE',
                'n': n_g,
                'p_true_rev': round(wr_g, 1),
                'avg_fwd_ret': round(avg_g, 2),
                'description': 'PANIC(3d ago) + VEL_EXHAUS + CRASH/DOWN regime',
            },
            'rsi_lt25_atr_crash': disc_cond(
                (em['rsi'] < 25) & (em['atr_z'] > 1.0) & (em['regime'].isin(['CRASH', 'DOWN'])),
                'RSI<25 + ATR_z>1 + CRASH'),
            'vel_exh_crash': disc_cond(
                (em['drop_accel'] < -3) & (em['regime'].isin(['CRASH', 'DOWN'])),
                'DROP_ACCEL<-3 + CRASH/DOWN'),
            'rsi_still_falling': disc_cond(
                (em['rsi'] <= 35) & (em['rsi_slope'] < -3),
                'RSI_STILL_FALLING (slope<-3)'),
            'rsi_flat_trap': disc_cond(
                (em['rsi'] <= 35) & (em['rsi_slope'].between(-1, 1)),
                'RSI_FLAT (false signal)'),
            'surge_regime_trap': disc_cond(
                (em['rsi'] <= 35) & (em['regime'] == 'SURGE'),
                'SURGE_REGIME_TRAP (avoid!)'),
        }
        # إزالة None
        discriminant = {k: v for k, v in discriminant.items() if v is not None}

    # ── مدة الحالة (state duration) ──────────────────────────────────────────
    duration_stats = {}
    for sym, g in df.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        st = g['state'].values
        n_g = len(st)
        i   = 0
        while i < n_g:
            s = st[i]
            j = i
            while j < n_g and st[j] == s:
                j += 1
            dur = j - i
            if s not in duration_stats:
                duration_stats[s] = []
            duration_stats[s].append(dur)
            i = j

    state_duration = {}
    for s, durs in duration_stats.items():
        arr = np.array(durs)
        state_duration[s] = {
            'median': round(float(np.median(arr)), 1),
            'mean':   round(float(arr.mean()), 1),
            'p90':    round(float(np.percentile(arr, 90)), 1),
            'count':  int(len(arr)),
        }

    # ── الحالة الحالية لكل سهم ───────────────────────────────────────────────
    current_states = []
    for sym, g in df.groupby('symbol'):
        g  = g.sort_values('bar_time').reset_index(drop=True)
        if g.empty:
            continue
        last = g.iloc[-1]
        st   = last['state']
        reg  = last['regime']
        # احتمال الانعكاس بناءً على الـ regime_conditional
        p_tr = None
        if st in regime_conditional and reg in regime_conditional[st]:
            p_tr = regime_conditional[st][reg].get('p_true_rev')

        # هل الحالة السابقة PANIC؟ (تحسين الإشارة)
        has_prior_panic = False
        if len(g) >= 4:
            prior_states = g['state'].iloc[-4:-1].values
            has_prior_panic = 'PANIC' in prior_states

        current_states.append({
            'symbol':         sym,
            'state':          st,
            'regime':         reg,
            'p_true_rev':     p_tr,
            'prior_panic':    has_prior_panic,
            'rsi':            round(float(last['rsi']), 1) if last['rsi'] == last['rsi'] else None,
            'mom5':           round(float(last['mom5']), 1) if last['mom5'] == last['mom5'] else None,
            'drop_accel':     round(float(last['drop_accel']), 2) if last['drop_accel'] == last['drop_accel'] else None,
        })

    # ترتيب: الحالات الأكثر إثارة أولاً
    STATE_PRIORITY = {
        'PANIC': 0, 'VELOCITY_EXHAUSTION': 1, 'EXHAUSTION': 2,
        'STABILIZATION': 3, 'POTENTIAL_BOUNCE': 4,
        'ACCELERATING_UP': 5, 'TRENDING_UP': 6,
        'SHARP_DROP': 7, 'DISTRIBUTION': 8,
        'CONTINUATION_DOWN': 9, 'NEUTRAL': 10,
    }
    current_states.sort(key=lambda x: (
        STATE_PRIORITY.get(x['state'], 10),
        -(x['p_true_rev'] or 0),
    ))

    # ── توزيع الحالات الكلي ──────────────────────────────────────────────────
    state_dist = df['state'].value_counts().to_dict()
    state_dist = {k: int(v) for k, v in state_dist.items()}
    state_dist_pct = {
        k: round(int(v) / n_total * 100, 1)
        for k, v in state_dist.items()
    }

    # ── Golden Sequences (احتمالية مسارات الانتقال عالية الجودة) ─────────────
    tm = trans_matrix
    def tp(frm, to):
        return tm.get(frm, {}).get('transitions', {}).get(to, {}).get('prob', 0)

    golden_paths = [
        {
            'path':  'ACCELERATING_UP → PANIC',
            'prob':  tp('ACCELERATING_UP', 'PANIC'),
            'meaning': 'الذروة → انهيار مفاجئ',
        },
        {
            'path':  'PANIC → VELOCITY_EXHAUSTION',
            'prob':  tp('PANIC', 'VELOCITY_EXHAUSTION'),
            'meaning': 'الذعر → نضوب البائعين',
        },
        {
            'path':  'VELOCITY_EXHAUSTION → EXHAUSTION',
            'prob':  tp('VELOCITY_EXHAUSTION', 'EXHAUSTION'),
            'meaning': 'نضوب الزخم → إرهاق كامل',
        },
        {
            'path':  'EXHAUSTION → STABILIZATION',
            'prob':  tp('EXHAUSTION', 'STABILIZATION'),
            'meaning': 'الإرهاق → استقرار',
        },
        {
            'path':  'STABILIZATION → TRENDING_UP',
            'prob':  tp('STABILIZATION', 'TRENDING_UP'),
            'meaning': 'الاستقرار → بداية صعود',
        },
        {
            'path':  'SHARP_DROP → CONTINUATION_DOWN',
            'prob':  tp('SHARP_DROP', 'CONTINUATION_DOWN'),
            'meaning': 'هبوط حاد → استمرار الضغط',
        },
        {
            'path':  'TRENDING_UP → DISTRIBUTION',
            'prob':  tp('TRENDING_UP', 'DISTRIBUTION'),
            'meaning': 'الصعود → بداية توزيع',
        },
    ]

    # احتمالية المسار الذهبي الكامل
    golden_full_prob = (
        tp('ACCELERATING_UP', 'PANIC') *
        tp('PANIC', 'VELOCITY_EXHAUSTION') *
        tp('VELOCITY_EXHAUSTION', 'EXHAUSTION') *
        tp('EXHAUSTION', 'STABILIZATION') *
        tp('STABILIZATION', 'TRENDING_UP')
    ) / 100**4  # تصحيح وحدات الاحتمال

    # ── الإشارات الحالية عالية الأولوية ──────────────────────────────────────
    high_priority = [
        s for s in current_states
        if s['state'] in ('PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION', 'STABILIZATION')
        and s['regime'] in ('CRASH', 'DOWN')
    ]
    golden_now = [
        s for s in current_states
        if s['state'] in ('VELOCITY_EXHAUSTION', 'EXHAUSTION')
        and s['regime'] in ('CRASH', 'DOWN')
        and s['prior_panic']
    ]

    # ── ملخص تنفيذي ──────────────────────────────────────────────────────────
    mkt_regime_now = df[df['bar_time'] == df['bar_time'].max()]['regime'].mode()
    mkt_regime_now = mkt_regime_now.iloc[0] if len(mkt_regime_now) > 0 else 'UNKNOWN'

    summary = (
        f"🔮 نموذج انتقال الحالات | {n_total:,} شمعة | {n_sym} سهم\n"
        f"📊 السوق الآن: {mkt_regime_now}\n"
        f"🔴 PANIC/VEL_EXHAUS: {state_dist.get('PANIC',0) + state_dist.get('VELOCITY_EXHAUSTION',0)} سهم\n"
        f"🟡 EXHAUSTION: {state_dist.get('EXHAUSTION',0)} سهم\n"
        f"🟢 TRENDING_UP: {state_dist.get('TRENDING_UP',0)} سهم\n"
        f"⭐ إشارات ذهبية الآن: {len(golden_now)}"
    )

    return {
        'transition_matrix':   trans_matrix,
        'regime_conditional':  regime_conditional,
        'discriminant':        discriminant,
        'state_duration':      state_duration,
        'state_distribution':  state_dist,
        'state_dist_pct':      state_dist_pct,
        'current_states':      current_states[:50],   # أول 50 سهم (مُرتَّبة بالأولوية)
        'golden_sequences':    golden_paths,
        'golden_full_prob_pct': round(golden_full_prob * 100, 4),
        'high_priority_now':   high_priority,
        'golden_signals_now':  golden_now,
        'market_regime_now':   mkt_regime_now,
        'summary':             summary,
        'dataset': {
            'n_rows':      n_total,
            'n_symbols':   n_sym,
            'fwd_bars':    fwd_bars,
            'true_rev_thr': f'>{TRUE_REV_THR*100:.0f}%',
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# CONDITIONAL MARKET STATE EVOLUTION
# نموذج انتقال الحالات الشرطي متعدد الأبعاد
#
# P(REVERSAL | state_sequence, regime, sector, ATR_state,
#              liquidity_tier, duration_in_state, breadth_condition)
#
# يتضمن:
#   1. Duration Surface       — P(next | state, duration_in_state)
#   2. Sector Conditionality  — ثبات الانتقالات عبر القطاعات
#   3. Regime Stability       — ثبات عبر BULL/DOWN/CRASH
#   4. Failure Engine         — P(DEAD_CAT | transition, conditions)
#   5. Full 5D Surface        — (state, regime, sector, ATR, liquidity)
#   6. Self-Learning Loop     — اكتشاف المتغيرات الخفية
# ═══════════════════════════════════════════════════════════════════════════

def cmd_conditional_transitions(params):
    """
    نموذج تطور الحالات السوقية الشرطي.

    بدلاً من P(REVERSAL | PANIC) البسيط، يبني:
    P(REVERSAL | state, regime, sector, ATR_tier, liq_tier, duration_in_state)

    المخرجات الرئيسية:
      duration_surface    — P(TR | state, duration_bucket) — متى تصبح الحالة مُنهكة؟
      sector_surface      — P(TR | state, sector_group) — هل القطاع مهم؟
      regime_stability    — هل الانتقال مستقر عبر الـ regimes؟
      failure_engine      — P(DEAD_CAT | transition, regime) — تشخيص الفخاخ
      full_surface        — المصفوفة 5D الكاملة (للحالات العالية الأهمية)
      self_learning       — متغيرات خفية + مرشحو الدمج/التقسيم
      physics             — ضغط + تسارع + استنزاف البائعين
    """
    fwd_bars = int(params.get('fwd_bars', 5))
    TRUE_REV_THR = float(params.get('true_rev_thr', 0.03))  # +3%
    DEAD_CAT_MAX = float(params.get('dead_cat_max', 0.01))  # +0 to +1%

    con = get_connection()
    oh  = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)

    # تحميل بيانات القطاعات من stock_universe
    ic_sectors = pd.read_sql(
        "SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL", con)
    con.close()

    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية"}

    # ── تعيين القطاعات ───────────────────────────────────────────────────────
    sector_map_raw = dict(zip(ic_sectors['symbol'], ic_sectors['sector'].fillna('')))

    def map_sector_group(sec):
        s = str(sec).lower()
        if any(x in s for x in ['بنك', 'bank', 'تمويل', 'مالي', 'finance', 'insurance', 'تأمين']):
            return 'BANKS'
        if any(x in s for x in ['عقار', 'real_estate', 'real estate', 'housing', 'إسكان']):
            return 'REAL_ESTATE'
        if any(x in s for x in ['اتصال', 'telecom', 'media', 'tech', 'تقنية', 'إعلام']):
            return 'TELECOM_TECH'
        if any(x in s for x in ['صناع', 'industri', 'chemical', 'كيماو', 'بناء', 'cement']):
            return 'INDUSTRIALS'
        if any(x in s for x in ['غذاء', 'food', 'consumer', 'استهلاك', 'retail', 'pharma', 'دواء']):
            return 'CONSUMER'
        return 'OTHER'

    # ── حساب المؤشرات ─────────────────────────────────────────────────────────
    def _rsi14(c):
        d = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
        ag = g.ewm(com=13, min_periods=14).mean()
        al = l.ewm(com=13, min_periods=14).mean()
        return 100 - 100 / (1 + ag / al.replace(0, np.nan))

    def _atr14(h, lo, c):
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(com=13, min_periods=14).mean()

    parts = []
    for sym, g in oh.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        if len(g) < 40:
            continue
        g = g.copy()
        g['rsi']        = _rsi14(g['close'])
        atr_abs         = _atr14(g['high'], g['low'], g['close'])
        g['atr_pct']    = atr_abs / g['close'] * 100
        g['vol_r']      = g['volume'] / g['volume'].rolling(20, min_periods=5).mean()
        g['mom5']       = g['close'].pct_change(5) * 100
        g['mom10']      = g['close'].pct_change(10) * 100
        g['mom20']      = g['close'].pct_change(20) * 100
        g['drop_accel'] = g['mom5'] - g['mom10'] / 2.0
        g['rsi_slope']  = g['rsi'].diff(3)
        atr_mu          = atr_abs.rolling(20, min_periods=10).mean()
        atr_sd          = atr_abs.rolling(20, min_periods=10).std()
        g['atr_z']      = (atr_abs - atr_mu) / atr_sd.replace(0, np.nan)
        # قيمة التداول اليومية للسيولة
        g['daily_val']  = g['close'] * g['volume']
        g['avg_val20']  = g['daily_val'].rolling(20, min_periods=5).mean()
        # Forward returns
        g[f'fwd{fwd_bars}'] = g['close'].shift(-fwd_bars) / g['close'] - 1
        g['fwd10']      = g['close'].shift(-10) / g['close'] - 1
        g['symbol']     = sym
        g['sector_grp'] = map_sector_group(sector_map_raw.get(sym, ''))
        parts.append(g)

    if not parts:
        return {"error": "لا توجد بيانات"}

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=['rsi', 'mom5', 'mom10', 'atr_z', f'fwd{fwd_bars}']).copy()

    # ── تصنيف الحالات (نفس classify_state من cmd_state_transitions) ────────
    def classify_state(rsi, mom5, mom10, mom20, atr_z, vol_r, daccel, rsi_sl):
        if daccel < -5 and atr_z > 1.5 and rsi_sl < -5:          return 'PANIC'
        if mom5 > 5 and mom10 > 5 and atr_z > 0.5 and rsi > 60:  return 'ACCELERATING_UP'
        if daccel < -3 and rsi <= 35 and mom10 < -3:              return 'VELOCITY_EXHAUSTION'
        if rsi <= 32 and mom5 < -3 and mom10 < -2:                return 'EXHAUSTION'
        if mom5 < -7 and daccel < -2:                             return 'SHARP_DROP'
        if mom5 < -4 and mom10 < -5 and atr_z < 0.5 and rsi < 40:return 'CONTINUATION_DOWN'
        if mom20 > 5 and rsi > 60 and mom5 < mom10*0.5 and vol_r > 1.3: return 'DISTRIBUTION'
        if 30 < rsi <= 45 and abs(mom5) < 3 and atr_z < 0 and rsi_sl >= -1: return 'STABILIZATION'
        if 35 < rsi <= 50 and mom5 > 2 and mom10 < -5:           return 'POTENTIAL_BOUNCE'
        if mom5 > 2 and mom10 > 2 and rsi > 50:                  return 'TRENDING_UP'
        return 'NEUTRAL'

    df['rsi']      = df['rsi'].fillna(50)
    df['vol_r']    = df['vol_r'].fillna(1.0)
    df['mom20']    = df['mom20'].fillna(0)
    df['rsi_slope']= df['rsi_slope'].fillna(0)
    df['state']    = df.apply(lambda r: classify_state(
        r['rsi'], r['mom5'], r['mom10'], r['mom20'],
        r['atr_z'], r['vol_r'], r['drop_accel'], r['rsi_slope']
    ), axis=1)

    # ── حساب duration_in_state لكل شريط ─────────────────────────────────────
    # نستخدم df.loc مباشرةً لتفادي تكرار الـ index عند concat
    df['dur_in'] = 1
    for sym, g in df.groupby('symbol'):
        g_sorted = g.sort_values('bar_time')
        st  = g_sorted['state'].values
        dur = np.ones(len(st), dtype=int)
        for i in range(1, len(st)):
            dur[i] = dur[i-1] + 1 if st[i] == st[i-1] else 1
        df.loc[g_sorted.index, 'dur_in'] = dur

    def dur_bucket(d):
        if d == 1: return '1'
        if d == 2: return '2'
        if d == 3: return '3'
        if d <= 5: return '4-5'
        return '6+'
    df['dur_bucket'] = df['dur_in'].apply(dur_bucket)

    # ── ATR tier ─────────────────────────────────────────────────────────────
    def atr_tier(z):
        if z < -0.5:  return 'LOW'
        if z < 0.5:   return 'NORMAL'
        if z < 1.5:   return 'HIGH'
        return 'EXTREME'
    df['atr_tier'] = df['atr_z'].apply(atr_tier)

    # ── Liquidity tier ────────────────────────────────────────────────────────
    def liq_tier(v):
        if v < 50_000:   return 'DEAD'
        if v < 500_000:  return 'THIN'
        if v < 5_000_000:return 'LIQUID'
        return 'LARGE'
    df['avg_val20'] = df['avg_val20'].fillna(0)
    df['liq_tier']  = df['avg_val20'].apply(liq_tier)

    # ── Market Regime per day ────────────────────────────────────────────────
    df['bar_dt'] = pd.to_datetime(df['bar_time'], unit='s').dt.date
    daily_agg = df.groupby('bar_dt').agg(
        mkt_mom5  = ('mom5',  'median'),
        breadth   = ('rsi',   lambda x: (x > 50).mean()),
        n_panicking = ('atr_z', lambda x: (x > 1.5).mean() * 100),
    ).reset_index()

    def regime_from(m5, br, n_panic):
        if   br >= 0.65 and m5 > 3:              return 'SURGE'
        elif br >= 0.55 and m5 > 1:              return 'UP'
        elif n_panic >= 20 and br <= 0.35:       return 'CRASH'
        elif br <= 0.40 and m5 < -1:             return 'DOWN'
        elif abs(m5) < 1.5 and 0.40 < br < 0.60:return 'SIDEWAYS'
        else:                                     return 'NEUTRAL'

    daily_agg['regime'] = daily_agg.apply(
        lambda r: regime_from(r['mkt_mom5'], r['breadth'], r['n_panicking']), axis=1)
    regime_map   = dict(zip(daily_agg['bar_dt'], daily_agg['regime']))
    breadth_map  = dict(zip(daily_agg['bar_dt'], daily_agg['breadth']))
    df['regime']  = df['bar_dt'].map(regime_map).fillna('NEUTRAL')
    df['breadth'] = df['bar_dt'].map(breadth_map).fillna(0.5)

    # ── Outcome labels ────────────────────────────────────────────────────────
    fwd_col = f'fwd{fwd_bars}'
    df['true_rev']  = df[fwd_col] > TRUE_REV_THR
    df['dead_cat']  = (df[fwd_col] > 0) & (df[fwd_col] <= DEAD_CAT_MAX)
    df['failure']   = df[fwd_col] <= 0
    df['hard_fail'] = df[fwd_col] < -0.03

    # ── Aggregate helper ──────────────────────────────────────────────────────
    def agg_group(sub):
        n = len(sub)
        if n < 5:
            return None
        return {
            'n':          n,
            'p_tr':       round(float(sub['true_rev'].mean() * 100), 1),
            'p_dead_cat': round(float(sub['dead_cat'].mean() * 100), 1),
            'p_failure':  round(float(sub['failure'].mean() * 100), 1),
            'p_hard_fail':round(float(sub['hard_fail'].mean() * 100), 1),
            'avg_fwd':    round(float(sub[fwd_col].mean() * 100), 2),
            'wr':         round(float((sub[fwd_col] > 0).mean() * 100), 1),
            'sharpe':     round(
                float(sub[fwd_col].mean() / sub[fwd_col].std())
                if sub[fwd_col].std() > 0 else 0, 2),
        }

    REVERSAL_STATES = [
        'PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION',
        'STABILIZATION', 'POTENTIAL_BOUNCE', 'SHARP_DROP',
    ]

    # ════════════════════════════════════════════════════════════════════════
    # 1. DURATION SURFACE — P(TR | state, duration_bucket)
    # متى تصبح الحالة "مُنهكة"؟ الـ exhaustion threshold
    # ════════════════════════════════════════════════════════════════════════
    duration_surface = {}
    for st in REVERSAL_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        duration_surface[st] = {}
        for bkt in ['1', '2', '3', '4-5', '6+']:
            sub = sub_st[sub_st['dur_bucket'] == bkt]
            r   = agg_group(sub)
            if r:
                duration_surface[st][bkt] = r

        # exhaustion_threshold = bucket حيث P(TR) أعلى ما يكون
        best_bkt  = None
        best_ptr  = -1
        worst_bkt = None
        worst_ptr = 200
        for bkt, vals in duration_surface[st].items():
            if vals['p_tr'] > best_ptr:
                best_ptr = vals['p_tr']; best_bkt = bkt
            if vals['p_tr'] < worst_ptr:
                worst_ptr = vals['p_tr']; worst_bkt = bkt
        duration_surface[st]['_meta'] = {
            'exhaustion_at':    best_bkt,
            'exhaustion_p_tr':  best_ptr,
            'continuation_at':  worst_bkt,
            'continuation_p_tr':worst_ptr,
            'duration_matters': abs(best_ptr - worst_ptr) > 10,
        }

    # ════════════════════════════════════════════════════════════════════════
    # 2. SECTOR SURFACE — هل الانتقال ثابت عبر القطاعات؟
    # ════════════════════════════════════════════════════════════════════════
    sector_surface = {}
    SECTOR_GROUPS_LIST = ['BANKS', 'REAL_ESTATE', 'TELECOM_TECH', 'INDUSTRIALS', 'CONSUMER', 'OTHER']
    for st in REVERSAL_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        sector_surface[st] = {}
        present_secs = []
        ptr_vals     = []
        for sec in SECTOR_GROUPS_LIST:
            sub = sub_st[sub_st['sector_grp'] == sec]
            r   = agg_group(sub)
            if r:
                sector_surface[st][sec] = r
                present_secs.append(sec)
                ptr_vals.append(r['p_tr'])

        if len(ptr_vals) >= 2:
            sector_surface[st]['_meta'] = {
                'p_tr_range':    round(max(ptr_vals) - min(ptr_vals), 1),
                'sector_stable': (max(ptr_vals) - min(ptr_vals)) < 15,
                'best_sector':   present_secs[ptr_vals.index(max(ptr_vals))],
                'worst_sector':  present_secs[ptr_vals.index(min(ptr_vals))],
            }

    # ════════════════════════════════════════════════════════════════════════
    # 3. REGIME STABILITY — هل الانتقال ثابت عبر الـ regimes؟
    # ════════════════════════════════════════════════════════════════════════
    REGIMES = ['CRASH', 'DOWN', 'SIDEWAYS', 'NEUTRAL', 'UP', 'SURGE']
    regime_stability = {}
    for st in REVERSAL_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        regime_stability[st] = {}
        ptr_by_regime = {}
        for reg in REGIMES:
            sub = sub_st[sub_st['regime'] == reg]
            r   = agg_group(sub)
            if r:
                regime_stability[st][reg] = r
                ptr_by_regime[reg] = r['p_tr']

        if len(ptr_by_regime) >= 2:
            vals = list(ptr_by_regime.values())
            regime_stability[st]['_meta'] = {
                'p_tr_range':      round(max(vals) - min(vals), 1),
                'regime_stable':   (max(vals) - min(vals)) < 20,
                'best_regime':     max(ptr_by_regime, key=ptr_by_regime.get),
                'worst_regime':    min(ptr_by_regime, key=ptr_by_regime.get),
                'crash_vs_surge':  round(
                    ptr_by_regime.get('CRASH', 0) - ptr_by_regime.get('SURGE', 0), 1),
                'verdict': (
                    'STABLE'   if max(vals)-min(vals) < 15 else
                    'MILD'     if max(vals)-min(vals) < 25 else
                    'UNSTABLE'
                ),
            }

    # ════════════════════════════════════════════════════════════════════════
    # 4. FAILURE ENGINE — anatomy of failed reversals
    # P(DEAD_CAT | state, regime) — الانعكاس الزائف
    # ════════════════════════════════════════════════════════════════════════
    failure_engine = {}
    for st in REVERSAL_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        failure_engine[st] = {'by_regime': {}, 'by_atr': {}, 'by_liq': {}}

        # تشريح الفشل per regime
        for reg in REGIMES:
            sub = sub_st[sub_st['regime'] == reg]
            if len(sub) < 5:
                continue
            n   = len(sub)
            fv  = sub[fwd_col]
            # توزيع الإخفاق المُفصَّل
            failure_engine[st]['by_regime'][reg] = {
                'n':             n,
                'p_true_rev':    round(float((fv > TRUE_REV_THR).mean() * 100), 1),
                'p_dead_cat':    round(float(((fv > 0) & (fv <= DEAD_CAT_MAX)).mean() * 100), 1),
                'p_modest_win':  round(float(((fv > DEAD_CAT_MAX) & (fv <= TRUE_REV_THR)).mean() * 100), 1),
                'p_small_loss':  round(float(((fv >= -0.02) & (fv <= 0)).mean() * 100), 1),
                'p_hard_fail':   round(float((fv < -0.03).mean() * 100), 1),
                'avg_fwd':       round(float(fv.mean() * 100), 2),
                'worst_5pct':    round(float(np.percentile(fv, 5) * 100), 2),
            }

        # تشريح per ATR tier
        for tier in ['LOW', 'NORMAL', 'HIGH', 'EXTREME']:
            sub = sub_st[sub_st['atr_tier'] == tier]
            if len(sub) < 5:
                continue
            fv  = sub[fwd_col]
            failure_engine[st]['by_atr'][tier] = {
                'n':           len(sub),
                'p_true_rev':  round(float((fv > TRUE_REV_THR).mean() * 100), 1),
                'p_failure':   round(float((fv <= 0).mean() * 100), 1),
                'avg_fwd':     round(float(fv.mean() * 100), 2),
            }

        # تشريح per liquidity tier
        for tier in ['DEAD', 'THIN', 'LIQUID', 'LARGE']:
            sub = sub_st[sub_st['liq_tier'] == tier]
            if len(sub) < 5:
                continue
            fv  = sub[fwd_col]
            failure_engine[st]['by_liq'][tier] = {
                'n':           len(sub),
                'p_true_rev':  round(float((fv > TRUE_REV_THR).mean() * 100), 1),
                'p_failure':   round(float((fv <= 0).mean() * 100), 1),
                'avg_fwd':     round(float(fv.mean() * 100), 2),
            }

    # ════════════════════════════════════════════════════════════════════════
    # 5. FULL 5D TRANSITION SURFACE
    # (state, regime, sector, atr_tier, liq_tier) → outcomes
    # نقصر على الحالات المهمة + min_n=8 لتجنب التشتيت
    # ════════════════════════════════════════════════════════════════════════
    full_surface = {}
    FOCUS_STATES = ['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION']
    for st in FOCUS_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        full_surface[st] = []
        # نجمع groupby 3D أولاً (regime + sector + atr_tier)
        for (reg, sec, atr), grp in sub_st.groupby(['regime', 'sector_grp', 'atr_tier']):
            if len(grp) < 8:
                continue
            fv = grp[fwd_col]
            full_surface[st].append({
                'regime':      reg,
                'sector':      sec,
                'atr_tier':    atr,
                'n':           len(grp),
                'p_tr':        round(float((fv > TRUE_REV_THR).mean() * 100), 1),
                'p_failure':   round(float((fv <= 0).mean() * 100), 1),
                'avg_fwd':     round(float(fv.mean() * 100), 2),
                'sharpe':      round(float(fv.mean() / fv.std()) if fv.std() > 0 else 0, 2),
            })
        # ترتيب بـ p_tr desc
        full_surface[st].sort(key=lambda x: -x['p_tr'])

    # ════════════════════════════════════════════════════════════════════════
    # 6. SELF-LEARNING LOOP
    # ════════════════════════════════════════════════════════════════════════

    # 6a. State Instability Score — variance of P(TR) across all conditioning
    state_instability = {}
    for st in REVERSAL_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue

        ptr_regime  = [v['p_tr'] for v in regime_stability.get(st, {}).values()
                       if isinstance(v, dict) and 'p_tr' in v]
        ptr_sector  = [v['p_tr'] for v in sector_surface.get(st, {}).values()
                       if isinstance(v, dict) and 'p_tr' in v]
        ptr_dur     = [v['p_tr'] for v in duration_surface.get(st, {}).values()
                       if isinstance(v, dict) and 'p_tr' in v]

        var_regime  = round(float(np.var(ptr_regime)), 1) if len(ptr_regime) >= 2 else 0
        var_sector  = round(float(np.var(ptr_sector)),  1) if len(ptr_sector) >= 2 else 0
        var_dur     = round(float(np.var(ptr_dur)),     1) if len(ptr_dur) >= 2 else 0

        total_var   = round(var_regime + var_sector + var_dur, 1)
        split_candidate = total_var > 150  # عالي التباين = يحتاج تقسيم
        hidden_var = max(
            [('regime', var_regime), ('sector', var_sector), ('duration', var_dur)],
            key=lambda x: x[1]
        )[0] if max(var_regime, var_sector, var_dur) > 0 else None

        state_instability[st] = {
            'total_variance':       total_var,
            'var_by_regime':        var_regime,
            'var_by_sector':        var_sector,
            'var_by_duration':      var_dur,
            'split_candidate':      split_candidate,
            'most_important_dim':   hidden_var,
            'diagnosis': (
                f'SPLIT: {hidden_var} يُفسِّر أكبر تباين → يجب تقسيم {st} بـ {hidden_var}'
                if split_candidate else
                f'STABLE: {st} ثابت بما يكفي عبر الأبعاد'
            ),
        }

    # 6b. Merge Candidates — حالات بتوزيع متشابه
    merge_candidates = []
    state_ptrs = {}
    for st in REVERSAL_STATES:
        base = agg_group(df[df['state'] == st])
        if base:
            state_ptrs[st] = base['p_tr']

    pairs = [(a, b) for i, a in enumerate(REVERSAL_STATES)
             for b in REVERSAL_STATES[i+1:]]
    for a, b in pairs:
        if a in state_ptrs and b in state_ptrs:
            diff = abs(state_ptrs[a] - state_ptrs[b])
            if diff < 5:  # P(TR) متشابه جداً
                merge_candidates.append({
                    'states': [a, b],
                    'p_tr_a': state_ptrs[a],
                    'p_tr_b': state_ptrs[b],
                    'diff':   round(diff, 1),
                    'suggestion': f'MERGE: {a} و {b} لديهم P(TR) متشابه ({diff:.1f}%)',
                })

    # 6c. Hidden Variable Detection
    # لكل حالة: أي متغير يُخفِّض التباين أكثر عند الشرط عليه؟
    hidden_variables = {}
    for st in FOCUS_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 30:
            continue
        baseline_var = float(sub_st[fwd_col].var())
        residuals    = {}
        for dim, col in [('regime', 'regime'), ('sector', 'sector_grp'),
                         ('atr', 'atr_tier'), ('liquidity', 'liq_tier'),
                         ('duration', 'dur_bucket')]:
            # variance بعد الشرط: متوسط التباين داخل كل مجموعة
            within_var = sub_st.groupby(col)[fwd_col].var().mean()
            pct_reduced = round((1 - float(within_var) / baseline_var) * 100, 1) \
                if baseline_var > 0 else 0
            residuals[dim] = pct_reduced
        best_dim = max(residuals, key=residuals.get) if residuals else 'none'
        hidden_variables[st] = {
            'baseline_var':    round(baseline_var * 100, 4),
            'variance_reduced': residuals,
            'most_powerful_dim': best_dim,
            'improvement_pct':   residuals.get(best_dim, 0),
            'diagnosis': (
                f'🔑 {best_dim} يُخفِّض تباين العوائد بـ {residuals.get(best_dim,0):.0f}% '
                f'في حالة {st}'
            ),
        }

    # ════════════════════════════════════════════════════════════════════════
    # 7. MARKET PHYSICS
    # نمذجة الضغط + التسارع + استنزاف البائعين
    # ════════════════════════════════════════════════════════════════════════

    # Seller Exhaustion Metric: كلما تسارع الهبوط + انخفض ATR → البائعون ينفدون
    panic_df = df[df['state'].isin(['PANIC', 'VELOCITY_EXHAUSTION'])].copy()
    if len(panic_df) >= 20:
        # Pressure Score = |drop_accel| × ATR_z (force = momentum × volatility)
        panic_df['pressure'] = panic_df['drop_accel'].abs() * panic_df['atr_z'].clip(lower=0)

        # Velocity Release: عندما يتوقف البائعون → ATR يبدأ في الانخفاض
        # نقيس: فرق ATR_z بين اليوم وبعد fwd_bars أيام
        # (لا نملكه هنا مباشرة لكن نستنتج من fwd_ret)
        pressure_high = panic_df[panic_df['pressure'] > panic_df['pressure'].quantile(0.7)]
        pressure_low  = panic_df[panic_df['pressure'] <= panic_df['pressure'].quantile(0.3)]

        physics = {
            'pressure_high': agg_group(pressure_high),   # ضغط عالٍ → استنزاف أسرع
            'pressure_low':  agg_group(pressure_low),    # ضغط خفيف → قد يستمر
            'interpretation': (
                'ضغط عالٍ (drop_accel × ATR_z): استنزاف البائعين يتسارع → احتمال انعكاس أعلى\n'
                'ضغط منخفض: بيع منظَّم → احتمال استمرار الهبوط أعلى'
            ),
        }
    else:
        physics = {'note': 'بيانات PANIC غير كافية لتحليل الفيزياء'}

    # ════════════════════════════════════════════════════════════════════════
    # 8. BREADTH CONDITIONALITY
    # P(TR | state, breadth_condition)
    # ════════════════════════════════════════════════════════════════════════
    breadth_surface = {}
    BREADTH_BUCKETS = [
        ('EXTREME_WEAK', 0.0, 0.25),
        ('WEAK',         0.25, 0.40),
        ('NEUTRAL',      0.40, 0.60),
        ('STRONG',       0.60, 0.75),
        ('EXTREME_STRONG', 0.75, 1.01),
    ]
    for st in FOCUS_STATES:
        sub_st = df[df['state'] == st]
        if len(sub_st) < 20:
            continue
        breadth_surface[st] = {}
        for label, lo, hi in BREADTH_BUCKETS:
            sub = sub_st[(sub_st['breadth'] >= lo) & (sub_st['breadth'] < hi)]
            r   = agg_group(sub)
            if r:
                breadth_surface[st][label] = r

    # ════════════════════════════════════════════════════════════════════════
    # ملخص تنفيذي
    # ════════════════════════════════════════════════════════════════════════
    # أفضل شروط للانعكاس (من الـ 5D surface)
    best_conditions = []
    for st, rows in full_surface.items():
        for row in rows[:3]:  # أعلى 3 شروط لكل حالة
            if row['p_tr'] >= 50:
                best_conditions.append({
                    'state':   st,
                    'regime':  row['regime'],
                    'sector':  row['sector'],
                    'atr':     row['atr_tier'],
                    'n':       row['n'],
                    'p_tr':    row['p_tr'],
                    'avg_fwd': row['avg_fwd'],
                })
    best_conditions.sort(key=lambda x: -x['p_tr'])

    # أخطر فخاخ (dead cat)
    worst_traps = []
    for st, data in failure_engine.items():
        for reg, vals in data.get('by_regime', {}).items():
            if vals.get('p_hard_fail', 0) > 30 and vals.get('n', 0) >= 10:
                worst_traps.append({
                    'state':       st,
                    'regime':      reg,
                    'n':           vals['n'],
                    'p_hard_fail': vals['p_hard_fail'],
                    'avg_fwd':     vals['avg_fwd'],
                })
    worst_traps.sort(key=lambda x: -x['p_hard_fail'])

    n_total = len(df)
    n_sym   = df['symbol'].nunique()

    summary = (
        f"🧠 Conditional Transition Surface | {n_total:,} شمعة | {n_sym} سهم\n"
        f"📐 أبعاد النموذج: Regime × Sector × ATR × Liquidity × Duration\n"
        f"⭐ أفضل شرط: {best_conditions[0]['state']}+{best_conditions[0]['regime']}+{best_conditions[0]['sector']} → {best_conditions[0]['p_tr']}% P(TR)"
        if best_conditions else
        f"🧠 Conditional Transition Surface | {n_total:,} شمعة | {n_sym} سهم"
    )

    return {
        'duration_surface':   duration_surface,
        'sector_surface':     sector_surface,
        'regime_stability':   regime_stability,
        'failure_engine':     failure_engine,
        'full_surface':       full_surface,
        'breadth_surface':    breadth_surface,
        'physics':            physics,
        'self_learning': {
            'state_instability':   state_instability,
            'merge_candidates':    merge_candidates,
            'hidden_variables':    hidden_variables,
        },
        'best_conditions':    best_conditions[:10],
        'worst_traps':        worst_traps[:8],
        'summary':            summary,
        'dataset': {
            'n_rows':      n_total,
            'n_symbols':   n_sym,
            'fwd_bars':    fwd_bars,
            'true_rev_thr': f'>{TRUE_REV_THR*100:.0f}%',
            'dead_cat_max': f'<{DEAD_CAT_MAX*100:.0f}%',
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE MARKET MEMORY SYSTEM
# ذاكرة السوق التكيُّفية — Bayesian Beliefs مع Temporal Decay
#
# بدلاً من الاحتماليات الثابتة، يُحافظ على:
#   posterior probability + confidence interval + drift direction
#
# المبادئ:
#   1. البيانات الحديثة تُعطى وزناً أكبر (exponential decay)
#   2. Bayesian Beta-Binomial: prior + likelihood → posterior + CI
#   3. Hierarchical multipliers بدلاً من مصفوفة 5D
#   4. Failure memory: هل الـ edges تتدهور مؤخراً؟
#   5. Drift detection: 90d vs 365d vs all-time
#   6. Regime shift: هل بنية السوق تغيَّرت؟
# ═══════════════════════════════════════════════════════════════════════════

def cmd_adaptive_memory(params):
    """
    ذاكرة السوق التكيُّفية — Bayesian posterior beliefs.

    لكل حالة رئيسية، يُعيد:
      posterior_p_tr  — الاحتمالية بعد تطبيق الـ prior + recent evidence
      ci_80           — مجال الثقة 80%
      drift           — STRENGTHENING / WEAKENING / STABLE
      failure_memory  — NORMAL / CAUTION / DANGER
      edge_quality    — STRONG / MODERATE / WEAK / UNRELIABLE
      hierarchical_adjustments — معاملات التعديل لكل بُعد
    """
    from scipy.stats import beta as beta_dist

    fwd_bars     = int(params.get('fwd_bars', 5))
    decay_lambda = float(params.get('decay_lambda', 0.7))   # وزن التدهور السنوي
    RECENT_DAYS  = int(params.get('recent_days',  90))       # "حديث" = آخر 90 يوم
    MID_DAYS     = int(params.get('mid_days',    365))       # "متوسط" = آخر سنة
    TRUE_REV_THR = 0.03
    PRIOR_ALPHA  = 8    # pseudo-successes (تفاؤل ضعيف)
    PRIOR_BETA   = 12   # pseudo-failures  (شك ضعيف)

    con = get_connection()
    oh  = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)
    su  = pd.read_sql("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL", con)
    con.close()

    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية"}

    now_ts = int(oh['bar_time'].max())

    # ── بناء المؤشرات ──────────────────────────────────────────────────────
    sector_map = dict(zip(su['symbol'], su['sector'].fillna('')))
    def _sec(sym):
        s = str(sector_map.get(sym, '')).lower()
        if any(x in s for x in ['بنك','bank','مالي','finance','تأمين']):  return 'BANKS'
        if any(x in s for x in ['عقار','real_estate','housing']):         return 'REAL_ESTATE'
        if any(x in s for x in ['اتصال','telecom','tech','إعلام']):       return 'TELECOM_TECH'
        if any(x in s for x in ['صناع','industri','cement','كيماو']):     return 'INDUSTRIALS'
        if any(x in s for x in ['غذاء','food','consumer','دواء']):        return 'CONSUMER'
        return 'OTHER'

    def _rsi14(c):
        d  = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
        ag = g.ewm(com=13, min_periods=14).mean()
        al = l.ewm(com=13, min_periods=14).mean()
        return 100 - 100 / (1 + ag / al.replace(0, np.nan))

    def _atr14(h, lo, c):
        tr = pd.concat([h-lo,(h-c.shift()).abs(),(lo-c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(com=13, min_periods=14).mean()

    parts = []
    for sym, g in oh.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        if len(g) < 40: continue
        g = g.copy()
        g['rsi']        = _rsi14(g['close'])
        atr             = _atr14(g['high'], g['low'], g['close'])
        g['atr_pct']    = atr / g['close'] * 100
        g['vol_r']      = g['volume'] / g['volume'].rolling(20, min_periods=5).mean()
        g['mom5']       = g['close'].pct_change(5)  * 100
        g['mom10']      = g['close'].pct_change(10) * 100
        g['mom20']      = g['close'].pct_change(20) * 100
        g['drop_accel'] = g['mom5'] - g['mom10'] / 2.0
        g['rsi_slope']  = g['rsi'].diff(3)
        atr_mu          = atr.rolling(20, min_periods=10).mean()
        atr_sd          = atr.rolling(20, min_periods=10).std()
        g['atr_z']      = (atr - atr_mu) / atr_sd.replace(0, np.nan)
        g['daily_val']  = g['close'] * g['volume']
        g['avg_val20']  = g['daily_val'].rolling(20, min_periods=5).mean()
        g[f'fwd{fwd_bars}'] = g['close'].shift(-fwd_bars) / g['close'] - 1
        # Temporal decay weight: exp(-lambda * age_years)
        age_days        = (now_ts - g['bar_time']) / 86400.0
        g['tw']         = np.exp(-decay_lambda * age_days / 365.0)
        g['symbol']     = sym
        g['sector_grp'] = _sec(sym)
        parts.append(g)

    if not parts:
        return {"error": "لا توجد بيانات"}

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=['rsi','mom5','mom10','atr_z',f'fwd{fwd_bars}']).copy()
    df['rsi']        = df['rsi'].fillna(50)
    df['vol_r']      = df['vol_r'].fillna(1.0)
    df['mom20']      = df['mom20'].fillna(0)
    df['rsi_slope']  = df['rsi_slope'].fillna(0)
    df['avg_val20']  = df['avg_val20'].fillna(0)

    # ── تصنيف الحالات ────────────────────────────────────────────────────
    def _classify(r):
        rsi=r['rsi']; m5=r['mom5']; m10=r['mom10']; m20=r['mom20']
        az=r['atr_z']; vr=r['vol_r']; da=r['drop_accel']; rs=r['rsi_slope']
        if da<-5 and az>1.5 and rs<-5:                 return 'PANIC'
        if m5>5 and m10>5 and az>0.5 and rsi>60:       return 'ACCELERATING_UP'
        if da<-3 and rsi<=35 and m10<-3:               return 'VELOCITY_EXHAUSTION'
        if rsi<=32 and m5<-3 and m10<-2:               return 'EXHAUSTION'
        if m5<-7 and da<-2:                            return 'SHARP_DROP'
        if m5<-4 and m10<-5 and az<0.5 and rsi<40:    return 'CONTINUATION_DOWN'
        if m20>5 and rsi>60 and m5<m10*0.5 and vr>1.3:return 'DISTRIBUTION'
        if 30<rsi<=45 and abs(m5)<3 and az<0 and rs>=-1:return 'STABILIZATION'
        if 35<rsi<=50 and m5>2 and m10<-5:            return 'POTENTIAL_BOUNCE'
        if m5>2 and m10>2 and rsi>50:                  return 'TRENDING_UP'
        return 'NEUTRAL'

    df['state'] = df.apply(_classify, axis=1)

    # ── Duration in state ──────────────────────────────────────────────────
    df['dur_in'] = 1
    for sym, g in df.groupby('symbol'):
        g2  = g.sort_values('bar_time')
        st  = g2['state'].values
        dur = np.ones(len(st), dtype=int)
        for i in range(1, len(st)):
            dur[i] = dur[i-1]+1 if st[i]==st[i-1] else 1
        df.loc[g2.index, 'dur_in'] = dur

    def _dur_bkt(d):
        if d<=1: return '1';
        if d<=2: return '2'
        if d<=3: return '3'
        if d<=5: return '4-5'
        return '6+'

    df['dur_bkt'] = df['dur_in'].apply(_dur_bkt)

    # ── ATR / Liquidity tiers ────────────────────────────────────────────
    df['atr_tier'] = pd.cut(df['atr_z'],
        bins=[-np.inf,-0.5,0.5,1.5,np.inf],
        labels=['LOW','NORMAL','HIGH','EXTREME'])
    df['liq_tier'] = pd.cut(df['avg_val20'],
        bins=[-np.inf,50_000,500_000,5_000_000,np.inf],
        labels=['DEAD','THIN','LIQUID','LARGE'])

    # ── Market Regime per day ─────────────────────────────────────────────
    df['bar_dt'] = pd.to_datetime(df['bar_time'], unit='s').dt.date
    agg = df.groupby('bar_dt').agg(
        mkt_m5=('mom5','median'),
        br=('rsi', lambda x: (x>50).mean()),
        n_pan=('atr_z', lambda x: (x>1.5).mean()*100),
    ).reset_index()
    def _reg(m5,br,np_):
        if   br>=0.65 and m5>3:             return 'SURGE'
        elif br>=0.55 and m5>1:             return 'UP'
        elif np_>=20  and br<=0.35:         return 'CRASH'
        elif br<=0.40 and m5<-1:            return 'DOWN'
        elif abs(m5)<1.5 and 0.40<br<0.60: return 'SIDEWAYS'
        return 'NEUTRAL'
    agg['regime']  = agg.apply(lambda r: _reg(r['mkt_m5'], r['br'], r['n_pan']), axis=1)
    agg['breadth'] = agg['br']
    df['regime']   = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['regime']))).fillna('NEUTRAL')
    df['breadth']  = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['breadth']))).fillna(0.5)

    # ── Time windows ──────────────────────────────────────────────────────
    fwd_col   = f'fwd{fwd_bars}'
    df['true_rev'] = df[fwd_col] > TRUE_REV_THR
    df['failure']  = df[fwd_col] <= 0
    recent_cut = now_ts - RECENT_DAYS * 86400
    mid_cut    = now_ts - MID_DAYS   * 86400
    df['era']  = np.where(df['bar_time'] >= recent_cut, 'recent',
                 np.where(df['bar_time'] >= mid_cut,     'mid', 'old'))

    # ════════════════════════════════════════════════════════════════════════
    # A. Bayesian Posterior per (state, regime)
    # ════════════════════════════════════════════════════════════════════════
    def bayesian_belief(old_s, old_f, rec_s, rec_f, prior_a=PRIOR_ALPHA, prior_b=PRIOR_BETA):
        """Beta-Binomial conjugate: prior ← old data, likelihood ← recent"""
        # prior derived from old data, rescaled to pseudo-counts
        old_n = old_s + old_f
        if old_n > 0:
            scale = max(prior_a + prior_b, min(old_n * 0.3, 50))  # soft prior
            pa = prior_a + old_s / old_n * scale
            pb = prior_b + old_f / old_n * scale
        else:
            pa, pb = prior_a, prior_b
        # posterior after recent evidence
        post_a = pa + rec_s
        post_b = pb + rec_f
        mean   = post_a / (post_a + post_b)
        ci80_lo = beta_dist.ppf(0.10, post_a, post_b)
        ci80_hi = beta_dist.ppf(0.90, post_a, post_b)
        ci95_lo = beta_dist.ppf(0.025, post_a, post_b)
        ci95_hi = beta_dist.ppf(0.975, post_a, post_b)
        n_eff   = old_n + rec_s + rec_f
        conf    = 'HIGH' if n_eff > 150 else 'MEDIUM' if n_eff > 40 else 'LOW'
        return {
            'posterior_p_tr': round(float(mean)*100, 1),
            'ci_80':  [round(float(ci80_lo)*100,1), round(float(ci80_hi)*100,1)],
            'ci_95':  [round(float(ci95_lo)*100,1), round(float(ci95_hi)*100,1)],
            'ci_width_80': round((ci80_hi - ci80_lo)*100, 1),
            'n_total': int(n_eff),
            'n_recent': int(rec_s + rec_f),
            'confidence': conf,
        }

    REVERSAL_STATES = ['PANIC','VELOCITY_EXHAUSTION','EXHAUSTION',
                       'STABILIZATION','POTENTIAL_BOUNCE']
    REGIMES         = ['CRASH','DOWN','SIDEWAYS','NEUTRAL','UP','SURGE']

    bayesian_beliefs = {}
    for st in REVERSAL_STATES:
        sub_all = df[df['state']==st]
        if len(sub_all) < 10: continue
        bayesian_beliefs[st] = {}

        for reg in REGIMES:
            sub = sub_all[sub_all['regime']==reg]
            if len(sub) < 8: continue

            old = sub[sub['era']=='old']
            rec = sub[sub['era']=='recent']

            b = bayesian_belief(
                int(old['true_rev'].sum()), int((~old['true_rev']).sum()),
                int(rec['true_rev'].sum()), int((~rec['true_rev']).sum()),
            )

            # Drift: recent weighted_p_tr vs mid weighted_p_tr
            mid    = sub[sub['era'].isin(['mid','recent'])]
            w_rec  = rec['tw'].sum()
            p_rec  = float((rec['true_rev']*rec['tw']).sum()/w_rec) if w_rec>0 else None
            w_mid  = mid['tw'].sum()
            p_mid  = float((mid['true_rev']*mid['tw']).sum()/w_mid) if w_mid>0 else None
            w_all  = sub['tw'].sum()
            p_all  = float((sub['true_rev']*sub['tw']).sum()/w_all) if w_all>0 else None

            drift_mag  = round((p_rec - p_all)*100, 1) if (p_rec is not None and p_all is not None) else 0
            if   drift_mag >  8:  drift = 'STRENGTHENING'
            elif drift_mag < -8:  drift = 'WEAKENING'
            elif drift_mag >  3:  drift = 'MILD_STRENGTH'
            elif drift_mag < -3:  drift = 'MILD_DECAY'
            else:                 drift = 'STABLE'

            # Edge quality: posterior + confidence + drift
            ppt = b['posterior_p_tr']
            if ppt >= 55 and drift in ('STRENGTHENING','STABLE','MILD_STRENGTH') and b['confidence'] != 'LOW':
                edge_q = 'STRONG'
            elif ppt >= 45 and drift != 'WEAKENING':
                edge_q = 'MODERATE'
            elif ppt >= 35 or drift == 'STRENGTHENING':
                edge_q = 'WEAK'
            else:
                edge_q = 'UNRELIABLE'

            b.update({
                'p_all_tw':       round(p_all*100, 1) if p_all is not None else None,
                'p_recent_tw':    round(p_rec*100, 1) if p_rec is not None else None,
                'drift':          drift,
                'drift_magnitude':drift_mag,
                'edge_quality':   edge_q,
            })
            bayesian_beliefs[st][reg] = b

    # ════════════════════════════════════════════════════════════════════════
    # B. Hierarchical Adjustment Weights
    # Base P(TR | state) × regime_mult × duration_mult × breadth_mult × atr_mult
    # ════════════════════════════════════════════════════════════════════════
    # أضف br_bkt على المستوى العام قبل الحلقات
    df['br_bkt'] = pd.cut(df['breadth'],
        bins=[0, 0.25, 0.40, 0.60, 0.75, 1.01],
        labels=['EXTREME_WEAK','WEAK','NEUTRAL','STRONG','EXTREME_STRONG'])

    hierarchical = {}
    for st in REVERSAL_STATES:
        sub = df[df['state']==st]
        if len(sub) < 20: continue

        # Weighted base
        w    = sub['tw']
        wsum = w.sum()
        base = float((sub['true_rev']*w).sum()/wsum) if wsum > 0 else 0
        if base <= 0: continue

        def _adj(grp_col, labels):
            adjs = {}
            for lbl in labels:
                sg   = sub[sub[grp_col] == lbl]
                if len(sg) < 8: continue
                wg   = sg['tw'].sum()
                p    = float((sg['true_rev']*sg['tw']).sum()/wg) if wg > 0 else 0
                adjs[lbl] = round(p / base, 3)
            return adjs

        hierarchical[st] = {
            'base_p_tr':     round(base*100, 1),
            'regime':        _adj('regime',   ['CRASH','DOWN','SIDEWAYS','NEUTRAL','UP','SURGE']),
            'duration':      _adj('dur_bkt',  ['1','2','3','4-5','6+']),
            'atr':           _adj('atr_tier', ['LOW','NORMAL','HIGH','EXTREME']),
            'sector':        _adj('sector_grp',['BANKS','REAL_ESTATE','TELECOM_TECH','INDUSTRIALS','CONSUMER','OTHER']),
        }
        hierarchical[st]['breadth'] = _adj('br_bkt', ['EXTREME_WEAK','WEAK','NEUTRAL','STRONG','EXTREME_STRONG'])

    # ════════════════════════════════════════════════════════════════════════
    # C. Failure Memory — هل الـ edges تتدهور مؤخراً؟
    # ════════════════════════════════════════════════════════════════════════
    failure_memory = {}
    for st in REVERSAL_STATES:
        sub_all = df[df['state']==st]
        if len(sub_all) < 10: continue

        hist_fail = float((sub_all[sub_all['era']=='old']['failure']).mean()) if len(sub_all[sub_all['era']=='old']) >= 10 else None
        rec_fail  = float((sub_all[sub_all['era']=='recent']['failure']).mean()) if len(sub_all[sub_all['era']=='recent']) >= 5 else None
        mid_fail  = float((sub_all[sub_all['era'].isin(['mid','recent'])]['failure']).mean()) \
                    if len(sub_all[sub_all['era'].isin(['mid','recent'])]) >= 10 else None

        if hist_fail is not None and rec_fail is not None:
            fail_drift = rec_fail - hist_fail
            if   fail_drift >  0.15:  mem_status = 'DANGER'   # failures spiked +15%
            elif fail_drift >  0.08:  mem_status = 'CAUTION'  # failures up +8%
            elif fail_drift < -0.08:  mem_status = 'IMPROVING'
            else:                     mem_status = 'NORMAL'
        else:
            fail_drift = None; mem_status = 'UNKNOWN'

        failure_memory[st] = {
            'historical_fail_rate': round(hist_fail*100,1) if hist_fail is not None else None,
            'recent_fail_rate':     round(rec_fail*100,1)  if rec_fail  is not None else None,
            'mid_fail_rate':        round(mid_fail*100,1)  if mid_fail  is not None else None,
            'fail_drift':           round(fail_drift*100,1) if fail_drift is not None else None,
            'memory_status':        mem_status,
            'n_recent':             int(len(sub_all[sub_all['era']=='recent'])),
        }

    # ════════════════════════════════════════════════════════════════════════
    # D. Regime Shift Detection
    # هل بنية السوق تغيَّرت مقارنةً بالسنة الماضية؟
    # ════════════════════════════════════════════════════════════════════════
    df_rec  = df[df['bar_time'] >= recent_cut]
    df_hist = df[df['bar_time'] <  mid_cut]

    def _regime_dist(sub):
        if len(sub) == 0: return {}
        cnt = sub.groupby('bar_dt')['regime'].first().value_counts(normalize=True)
        return {k: round(float(v)*100,1) for k,v in cnt.items()}

    reg_dist_recent = _regime_dist(df_rec)
    reg_dist_hist   = _regime_dist(df_hist)

    regime_shifts = []
    for reg in REGIMES:
        r = reg_dist_recent.get(reg, 0)
        h = reg_dist_hist.get(reg, 0)
        diff = r - h
        if abs(diff) >= 5:
            regime_shifts.append({
                'regime': reg,
                'historical_pct': h,
                'recent_pct':     r,
                'shift':          round(diff,1),
                'direction':      'MORE' if diff>0 else 'LESS',
            })
    regime_shifts.sort(key=lambda x: -abs(x['shift']))

    recent_breadth = float(df_rec.groupby('bar_dt')['breadth'].mean().mean()) if len(df_rec)>0 else 0.5
    hist_breadth   = float(df_hist.groupby('bar_dt')['breadth'].mean().mean()) if len(df_hist)>0 else 0.5
    breadth_shift  = round((recent_breadth - hist_breadth)*100, 1)

    structure_changed = len(regime_shifts) >= 2 or abs(breadth_shift) >= 10
    current_regime_now = agg['regime'].iloc[-1] if len(agg)>0 else 'UNKNOWN'

    # ════════════════════════════════════════════════════════════════════════
    # E. Temporal Decay Curve — مساهمة كل حقبة زمنية
    # ════════════════════════════════════════════════════════════════════════
    df['year'] = pd.to_datetime(df['bar_time'], unit='s').dt.year
    decay_curve = {}
    for yr, g in df.groupby('year'):
        total_tw   = float(g['tw'].sum())
        raw_weight = len(g) / len(df) * 100
        decay_curve[int(yr)] = {
            'n_bars':     int(len(g)),
            'raw_pct':    round(raw_weight, 1),
            'decay_pct':  round(total_tw / df['tw'].sum() * 100, 1) if df['tw'].sum() > 0 else 0,
        }

    # ════════════════════════════════════════════════════════════════════════
    # F. Market Physics — ضغط التراكم والإرهاق
    # ════════════════════════════════════════════════════════════════════════
    physics_states = df[df['state'].isin(['PANIC','VELOCITY_EXHAUSTION'])].copy()
    physics = {}
    if len(physics_states) >= 30:
        ps = physics_states.copy()
        ps['pressure']   = ps['drop_accel'].abs() * ps['atr_z'].clip(lower=0)
        ps['vel_release']= (ps['atr_z'] - ps['atr_z'].shift(1)).fillna(0)  # ATR delta

        # Exhaustion signature: pressure decelerating + RSI slope flattening
        ps['exhausting'] = (
            (ps['drop_accel'] < -3) &
            (ps['drop_accel'] > ps['drop_accel'].shift(1).fillna(-999)) &  # accel slowing
            (ps['rsi_slope'].abs() < ps['rsi_slope'].shift(1).abs().fillna(0))  # RSI flattening
        )
        ps['true_rev'] = ps[fwd_col] > TRUE_REV_THR

        exhausting_sub = ps[ps['exhausting']]
        normal_sub     = ps[~ps['exhausting']]

        physics['exhaustion_signature'] = {
            'n_exhausting':   int(len(exhausting_sub)),
            'p_tr_exhausting':round(float(exhausting_sub['true_rev'].mean()*100),1) if len(exhausting_sub)>=5 else None,
            'n_normal':       int(len(normal_sub)),
            'p_tr_normal':    round(float(normal_sub['true_rev'].mean()*100),1) if len(normal_sub)>=5 else None,
            'interpretation': 'حين يتباطأ التسارع + يستقر RSI → البائعون ينفدون',
        }

        # Volatility release: P(TR) when ATR starts DROPPING after spike
        ps['atr_falling'] = (ps['atr_z'] < ps['atr_z'].shift(1).fillna(0)) & (ps['atr_z'] > 0.5)
        atr_fall_sub = ps[ps['atr_falling']]
        physics['volatility_release'] = {
            'n':   int(len(atr_fall_sub)),
            'p_tr':round(float(atr_fall_sub['true_rev'].mean()*100),1) if len(atr_fall_sub)>=5 else None,
            'interpretation': 'ATR يبدأ في الانخفاض بعد ذروة → ضغط التقلب يُفرَّج',
        }

        # Liquidity absorption: volume below avg after panic
        ps['vol_absorb'] = (ps['vol_r'] < 0.8) & (ps['atr_z'] > 0.5)
        vol_abs_sub = ps[ps['vol_absorb']]
        physics['liquidity_absorption'] = {
            'n':   int(len(vol_abs_sub)),
            'p_tr':round(float(vol_abs_sub['true_rev'].mean()*100),1) if len(vol_abs_sub)>=5 else None,
            'interpretation': 'حجم يتراجع مع تقلب مرتفع → امتصاص البيع من المشترين الكبار',
        }

    # ════════════════════════════════════════════════════════════════════════
    # G. Living Beliefs — الخلاصة التشغيلية
    # أفضل فرص الآن بناءً على posterior + failure_memory + drift
    # ════════════════════════════════════════════════════════════════════════
    living_beliefs = []
    for st, reg_beliefs in bayesian_beliefs.items():
        for reg, b in reg_beliefs.items():
            fm  = failure_memory.get(st, {})
            adj = 1.0
            # طبّق تعديل failure_memory
            if fm.get('memory_status') == 'DANGER':   adj *= 0.75
            elif fm.get('memory_status') == 'CAUTION': adj *= 0.88
            elif fm.get('memory_status') == 'IMPROVING': adj *= 1.05

            adj_p_tr = round(b['posterior_p_tr'] * adj, 1)
            living_beliefs.append({
                'state':           st,
                'regime':          reg,
                'posterior_p_tr':  b['posterior_p_tr'],
                'adj_p_tr':        adj_p_tr,
                'ci_80':           b['ci_80'],
                'ci_width':        b['ci_width_80'],
                'drift':           b['drift'],
                'drift_magnitude': b['drift_magnitude'],
                'edge_quality':    b['edge_quality'],
                'failure_memory':  fm.get('memory_status','UNKNOWN'),
                'confidence':      b['confidence'],
                'n_total':         b['n_total'],
                'n_recent':        b['n_recent'],
            })

    living_beliefs.sort(key=lambda x: (
        {'STRONG':0,'MODERATE':1,'WEAK':2,'UNRELIABLE':3}.get(x['edge_quality'],3),
        -x['adj_p_tr'],
    ))

    # ─── ملخص النظام ─────────────────────────────────────────────────────
    strong_edges    = [b for b in living_beliefs if b['edge_quality']=='STRONG']
    weakening_edges = [b for b in living_beliefs if b['drift']=='WEAKENING']
    danger_states   = [st for st,fm in failure_memory.items() if fm['memory_status']=='DANGER']

    n_total = len(df); n_sym = df['symbol'].nunique()
    summary = (
        f"🧠 Adaptive Memory | {n_total:,} obs | λ={decay_lambda} | "
        f"RECENT={RECENT_DAYS}d\n"
        f"✅ {len(strong_edges)} STRONG edges | "
        f"⚠️  {len(weakening_edges)} WEAKENING | "
        f"🔴 {len(danger_states)} DANGER states\n"
        f"Regime now: {current_regime_now} | "
        f"Structure changed: {'YES ⚠️' if structure_changed else 'NO ✅'} | "
        f"Breadth shift: {'+' if breadth_shift>=0 else ''}{breadth_shift}%"
    )

    # Macro context
    macro_ctx_am = _load_macro_context(168)
    macro_mult_am, macro_regime_am = _macro_regime_factor(macro_ctx_am)

    return {
        'bayesian_beliefs':     bayesian_beliefs,
        'hierarchical':         hierarchical,
        'failure_memory':       failure_memory,
        'regime_shifts':        regime_shifts,
        'living_beliefs':       living_beliefs[:20],
        'strong_edges':         strong_edges[:8],
        'decay_curve':          decay_curve,
        'physics':              physics,
        'current_regime':       current_regime_now,
        'structure_changed':    structure_changed,
        'breadth_shift':        breadth_shift,
        'recent_breadth':       round(recent_breadth*100,1),
        'hist_breadth':         round(hist_breadth*100,1),
        'summary':              summary,
        'macro_context': {
            'macro_regime':      macro_regime_am,
            'equity_multiplier': macro_mult_am,
            'real_interest_rate': macro_ctx_am.get('real_interest_rate') if macro_ctx_am else None,
            'inflation_yoy':     (macro_ctx_am.get('inflation_yoy') or macro_ctx_am.get('inflation_pct')) if macro_ctx_am else None,
            'cbe_rate':          (macro_ctx_am.get('cbe_rate') or macro_ctx_am.get('cbe_rate_pct')) if macro_ctx_am else None,
            'gdp_yoy':           macro_ctx_am.get('gdp_yoy') if macro_ctx_am else None,
            'fx_reserves_b':     macro_ctx_am.get('fx_reserves_b') if macro_ctx_am else None,
            'inflation_momentum': macro_ctx_am.get('inflation_momentum') if macro_ctx_am else None,
            'rate_cycle':        macro_ctx_am.get('rate_cycle') if macro_ctx_am else None,
            'strategic_bias':    macro_ctx_am.get('strategic_bias') if macro_ctx_am else None,
            'note':              f'Macro ×{macro_mult_am} على P(TR) — {macro_regime_am}',
        } if macro_ctx_am else {'error': 'no macro data — شغّل fetch_economics'},
        'dataset': {
            'n_rows':       n_total,
            'n_symbols':    n_sym,
            'fwd_bars':     fwd_bars,
            'decay_lambda': decay_lambda,
            'recent_days':  RECENT_DAYS,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# EVOLVING MARKET STRUCTURE ENGINE
# Multi-horizon memory, structural drift, alpha decay, failure typing,
# adaptive cognitive map
# ═══════════════════════════════════════════════════════════════════════════

def cmd_evolving_structure(params):
    """
    خريطة إدراكية حيّة لسوق EGX.
    تتتبع:
      - Alpha Decay per edge (multi-horizon)
      - Structural Drift (transition matrix + regime frequency)
      - Failure Typing (continuation trap / dead cat / drift failure)
      - Adaptive Cognitive Map (combined P_TR with adaptive weights)
      - Self-Evolution Loop
    """
    from scipy.stats import beta as beta_dist

    fwd_bars      = int(params.get('fwd_bars', 5))
    TRUE_REV_THR  = float(params.get('true_rev_thr', 0.03))
    SHORT_DAYS    = int(params.get('short_days',  30))
    MEDIUM_DAYS   = int(params.get('medium_days', 180))
    decay_lambda  = float(params.get('decay_lambda', 0.7))

    # ── Load raw OHLCV ──────────────────────────────────────────────────
    con = get_connection()
    oh  = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)
    su  = pd.read_sql("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL", con)
    con.close()

    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية"}

    now_ts = int(oh['bar_time'].max())

    # ── Sector map ──────────────────────────────────────────────────────
    sector_map = dict(zip(su['symbol'], su['sector'].fillna('')))
    def _sec(sym):
        s = str(sector_map.get(sym, '')).lower()
        if any(x in s for x in ['بنك','bank','مالي','finance','تأمين']):  return 'BANKS'
        if any(x in s for x in ['عقار','real_estate','housing']):         return 'REAL_ESTATE'
        if any(x in s for x in ['اتصال','telecom','tech','إعلام']):       return 'TELECOM_TECH'
        if any(x in s for x in ['صناع','industri','cement','كيماو']):     return 'INDUSTRIALS'
        if any(x in s for x in ['غذاء','food','consumer','دواء']):        return 'CONSUMER'
        return 'OTHER'

    # ── Technical indicators (same pipeline as cmd_adaptive_memory) ─────
    def _rsi14(c):
        d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
        ag=g.ewm(com=13,min_periods=14).mean()
        al=l.ewm(com=13,min_periods=14).mean()
        return 100-100/(1+ag/al.replace(0,np.nan))

    def _atr14(h,lo,c):
        tr=pd.concat([h-lo,(h-c.shift()).abs(),(lo-c.shift()).abs()],axis=1).max(axis=1)
        return tr.ewm(com=13,min_periods=14).mean()

    parts = []
    for sym, g in oh.groupby('symbol'):
        g = g.sort_values('bar_time').reset_index(drop=True)
        if len(g) < 40: continue
        g = g.copy()
        g['rsi']        = _rsi14(g['close'])
        atr             = _atr14(g['high'],g['low'],g['close'])
        g['atr_pct']    = atr/g['close']*100
        g['vol_r']      = g['volume']/g['volume'].rolling(20,min_periods=5).mean()
        g['mom5']       = g['close'].pct_change(5)*100
        g['mom10']      = g['close'].pct_change(10)*100
        g['mom20']      = g['close'].pct_change(20)*100
        g['drop_accel'] = g['mom5']-g['mom10']/2.0
        g['rsi_slope']  = g['rsi'].diff(3)
        atr_mu          = atr.rolling(20,min_periods=10).mean()
        atr_sd          = atr.rolling(20,min_periods=10).std()
        g['atr_z']      = (atr-atr_mu)/atr_sd.replace(0,np.nan)
        g['daily_val']  = g['close']*g['volume']
        g['avg_val20']  = g['daily_val'].rolling(20,min_periods=5).mean()
        g[f'fwd{fwd_bars}'] = g['close'].shift(-fwd_bars)/g['close']-1
        age_days        = (now_ts-g['bar_time'])/86400.0
        g['tw']         = np.exp(-decay_lambda*age_days/365.0)
        g['symbol']     = sym
        g['sector_grp'] = _sec(sym)
        parts.append(g)

    if not parts:
        return {"error": "لا توجد بيانات"}

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=['rsi','mom5','mom10','atr_z',f'fwd{fwd_bars}']).copy()
    df['rsi']       = df['rsi'].fillna(50)
    df['vol_r']     = df['vol_r'].fillna(1.0)
    df['mom20']     = df['mom20'].fillna(0)
    df['rsi_slope'] = df['rsi_slope'].fillna(0)
    df['avg_val20'] = df['avg_val20'].fillna(0)

    # ── State classification ─────────────────────────────────────────────
    def _classify(r):
        rsi=r['rsi']; m5=r['mom5']; m10=r['mom10']; m20=r['mom20']
        az=r['atr_z']; vr=r['vol_r']; da=r['drop_accel']; rs=r['rsi_slope']
        if da<-5  and az>1.5  and rs<-5:                  return 'PANIC'
        if m5>5   and m10>5   and az>0.5  and rsi>60:     return 'ACCELERATING_UP'
        if da<-3  and rsi<=35 and m10<-3:                 return 'VELOCITY_EXHAUSTION'
        if rsi<=32 and m5<-3  and m10<-2:                 return 'EXHAUSTION'
        if m5<-7  and da<-2:                              return 'SHARP_DROP'
        if m5<-4  and m10<-5  and az<0.5  and rsi<40:     return 'CONTINUATION_DOWN'
        if m20>5  and rsi>60  and m5<m10*0.5 and vr>1.3:  return 'DISTRIBUTION'
        if 30<rsi<=45 and abs(m5)<3 and az<0 and rs>=-1:  return 'STABILIZATION'
        if 35<rsi<=50 and m5>2 and m10<-5:                return 'POTENTIAL_BOUNCE'
        if m5>2   and m10>2   and rsi>50:                  return 'TRENDING_UP'
        return 'NEUTRAL'

    df['state'] = df.apply(_classify, axis=1)

    # ── Market regime per calendar day ──────────────────────────────────
    df['bar_dt'] = pd.to_datetime(df['bar_time'], unit='s').dt.date
    agg = df.groupby('bar_dt').agg(
        mkt_m5=('mom5','median'),
        br=('rsi', lambda x: (x>50).mean()),
        n_pan=('atr_z', lambda x: (x>1.5).mean()*100),
    ).reset_index()
    def _reg(m5,br,np_):
        if   br>=0.65 and m5>3:             return 'SURGE'
        elif br>=0.55 and m5>1:             return 'UP'
        elif np_>=20  and br<=0.35:         return 'CRASH'
        elif br<=0.40 and m5<-1:            return 'DOWN'
        elif abs(m5)<1.5 and 0.40<br<0.60: return 'SIDEWAYS'
        return 'NEUTRAL'
    agg['regime']  = agg.apply(lambda r: _reg(r['mkt_m5'],r['br'],r['n_pan']), axis=1)
    agg['breadth'] = agg['br']
    df['regime']   = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['regime']))).fillna('NEUTRAL')
    df['breadth']  = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['breadth']))).fillna(0.5)

    fwd_col       = f'fwd{fwd_bars}'
    df['fwd_ret'] = df[fwd_col]
    df['true_rev'] = df['fwd_ret'] > TRUE_REV_THR
    df['failure']  = df['fwd_ret'] <= 0

    # ── Horizon cuts ────────────────────────────────────────────────────
    short_cut  = now_ts - SHORT_DAYS  * 86400
    medium_cut = now_ts - MEDIUM_DAYS * 86400

    df_short  = df[df['bar_time'] >= short_cut].copy()
    df_medium = df[df['bar_time'] >= medium_cut].copy()
    df_long   = df.copy()   # all history

    # ════════════════════════════════════════════════════════════════════
    # 1. ALPHA DECAY MODEL — per (state, regime) across three horizons
    # ════════════════════════════════════════════════════════════════════
    KEY_COMBOS = [
        ('VELOCITY_EXHAUSTION', 'CRASH'),
        ('VELOCITY_EXHAUSTION', 'DOWN'),
        ('VELOCITY_EXHAUSTION', 'UP'),
        ('VELOCITY_EXHAUSTION', 'NEUTRAL'),
        ('EXHAUSTION',          'CRASH'),
        ('EXHAUSTION',          'DOWN'),
        ('EXHAUSTION',          'UP'),
        ('PANIC',               'CRASH'),
        ('PANIC',               'DOWN'),
        ('PANIC',               'UP'),
        ('POTENTIAL_BOUNCE',    'CRASH'),
        ('POTENTIAL_BOUNCE',    'UP'),
        ('STABILIZATION',       'CRASH'),
        ('STABILIZATION',       'DOWN'),
    ]

    def _horizon_stats(slice_df, state, regime):
        sub = slice_df[(slice_df['state']==state) & (slice_df['regime']==regime)]
        n   = len(sub)
        if n < 3:
            return {'p': None, 'n': n}
        p = round(float(sub['true_rev'].mean())*100, 1)
        tw = sub['tw'].sum()
        p_tw = round(float((sub['true_rev']*sub['tw']).sum()/tw)*100, 1) if tw>0 else p
        return {'p': p, 'p_tw': p_tw, 'n': n}

    def _failure_cluster_score(slice_df, state, regime):
        """
        Compute failure clustering: are failures arriving faster recently?
        Returns (is_clustered: bool, recent_fail_density: float, hist_fail_density: float)
        """
        sub = slice_df[(slice_df['state']==state) & (slice_df['regime']==regime)]
        if len(sub) < 6:
            return False, None, None
        sub = sub.sort_values('bar_time')
        # Split into two halves by time
        mid_ts = sub['bar_time'].median()
        early  = sub[sub['bar_time'] <  mid_ts]
        late   = sub[sub['bar_time'] >= mid_ts]
        # Failure density = failures / total obs per half
        e_fail = early['failure'].mean() if len(early) > 0 else 0
        l_fail = late['failure'].mean()  if len(late)  > 0 else 0
        clustered = (l_fail > e_fail * 1.35) and len(late) >= 5
        return clustered, round(float(l_fail)*100,1), round(float(e_fail)*100,1)

    alpha_decay = {}
    for (state, regime) in KEY_COMBOS:
        h_long   = _horizon_stats(df_long,   state, regime)
        h_medium = _horizon_stats(df_medium, state, regime)
        h_short  = _horizon_stats(df_short,  state, regime)

        p_l = h_long['p'];   p_m = h_medium['p'];   p_s = h_short['p']

        # Decay velocity: recent – long (positive = strengthening)
        decay_v = round(p_s - p_l, 1) if (p_l is not None and p_s is not None) else None
        # Decay acceleration: is decay accelerating? (recent half-slope vs early half-slope)
        decay_a = None
        if p_l is not None and p_m is not None and p_s is not None:
            slope_early = round(p_m - p_l, 1)   # change over long→medium window
            slope_late  = round(p_s - p_m, 1)   # change over medium→short window
            decay_a     = round(slope_late - slope_early, 1)

        clustered, l_fail_den, e_fail_den = _failure_cluster_score(df_long, state, regime)

        # Alpha status
        n_short = h_short['n']
        if decay_v is None:
            alpha_status = 'UNKNOWN'
        elif decay_v > 8  and n_short >= 8:
            alpha_status = 'STRENGTHENING'
        elif decay_v < -18 and clustered:
            alpha_status = 'COLLAPSING'
        elif decay_v < -12:
            alpha_status = 'WEAKENING'
        elif abs(decay_v) <= 8:
            alpha_status = 'STABLE'
        else:
            alpha_status = 'WEAKENING'

        key = f"{state}|{regime}"
        alpha_decay[key] = {
            'state':             state,
            'regime':            regime,
            'long':              h_long,
            'medium':            h_medium,
            'short':             h_short,
            'decay_velocity':    decay_v,
            'decay_acceleration':decay_a,
            'failure_clustering':clustered,
            'late_fail_density': l_fail_den,
            'early_fail_density':e_fail_den,
            'alpha_status':      alpha_status,
        }

    # ════════════════════════════════════════════════════════════════════
    # 2. STRUCTURAL DRIFT ENGINE
    # ════════════════════════════════════════════════════════════════════

    # 2a. Transition matrix drift (short vs long)
    def _build_tm(slice_df):
        rows = []
        for sym, g in slice_df.groupby('symbol'):
            g2 = g.sort_values('bar_time')
            sts = g2['state'].values
            for i in range(len(sts)-1):
                rows.append({'from': sts[i], 'to': sts[i+1]})
        if not rows:
            return {}
        tdf = pd.DataFrame(rows)
        tm  = {}
        for fs, grp in tdf.groupby('from'):
            total = len(grp)
            tm[fs] = {to: round(cnt/total*100,1) for to, cnt in grp['to'].value_counts().items()}
        return tm

    tm_long  = _build_tm(df_long)
    tm_short = _build_tm(df_short)

    DRIFT_STATES = ['VELOCITY_EXHAUSTION','EXHAUSTION','PANIC','POTENTIAL_BOUNCE',
                    'STABILIZATION','TRENDING_UP','CONTINUATION_DOWN','SHARP_DROP']
    transition_drift = {}
    for state in DRIFT_STATES:
        if state not in tm_long:
            continue
        ld = tm_long[state];  sd = tm_short.get(state, {})
        all_states = set(ld) | set(sd)
        l1 = sum(abs(sd.get(s,0)/100 - ld.get(s,0)/100) for s in all_states)
        movers = []
        for s in all_states:
            delta = round(sd.get(s,0) - ld.get(s,0), 1)
            if abs(delta) >= 4:
                movers.append({'to': s, 'delta': delta,
                               'short_pct': sd.get(s,0), 'long_pct': ld.get(s,0)})
        movers.sort(key=lambda x: abs(x['delta']), reverse=True)
        transition_drift[state] = {
            'l1_norm': round(l1, 3),
            'drifting': l1 > 0.15,
            'top_movers': movers[:4],
        }

    # 2b. Regime frequency drift across three horizons
    def _regime_freq(slice_df):
        if len(slice_df) == 0: return {}
        vc = slice_df['regime'].value_counts(normalize=True)*100
        return {r: round(float(v),1) for r,v in vc.items()}

    rf_long   = _regime_freq(df_long)
    rf_medium = _regime_freq(df_medium)
    rf_short  = _regime_freq(df_short)
    all_regs  = set(rf_long)|set(rf_medium)|set(rf_short)
    regime_drift = {}
    for reg in sorted(all_regs):
        p_l = rf_long.get(reg,0);   p_m = rf_medium.get(reg,0);   p_s = rf_short.get(reg,0)
        regime_drift[reg] = {
            'long_pct':   p_l,
            'medium_pct': p_m,
            'short_pct':  p_s,
            'delta_l_s':  round(p_s-p_l, 1),
            'status':     'SHIFTING' if abs(p_s-p_l) > 8 else 'MODERATE' if abs(p_s-p_l) > 4 else 'STABLE',
        }

    # 2c. Volatility persistence (ATR_z lag-1 autocorrelation)
    def _vol_persist(slice_df):
        if len(slice_df) < 20: return None
        try:
            ac = slice_df.groupby('symbol')['atr_z'].apply(
                lambda x: x.dropna().autocorr(lag=1) if len(x)>5 else np.nan)
            return round(float(ac.dropna().mean()), 3)
        except: return None

    vol_persist = {
        'short':  _vol_persist(df_short),
        'medium': _vol_persist(df_medium),
        'long':   _vol_persist(df_long),
    }
    vp_delta = None
    if vol_persist['short'] is not None and vol_persist['long'] is not None:
        vp_delta = round(vol_persist['short'] - vol_persist['long'], 3)

    # 2d. Breadth-reversal correlation
    def _br_corr(slice_df):
        if len(slice_df) < 20: return None
        try:
            return round(float(slice_df['breadth'].corr(slice_df['true_rev'].astype(float))), 3)
        except: return None

    breadth_rev_corr = {
        'short':  _br_corr(df_short),
        'medium': _br_corr(df_medium),
        'long':   _br_corr(df_long),
    }

    # 2e. Reversal strength distribution (fwd_ret among true reversals)
    def _rev_strength(slice_df):
        sub = slice_df[slice_df['true_rev']]
        if len(sub) < 5: return None
        fr = sub['fwd_ret']*100
        return {
            'n': len(sub),
            'median': round(float(fr.median()),1),
            'p75':    round(float(fr.quantile(0.75)),1),
            'p25':    round(float(fr.quantile(0.25)),1),
            'mean':   round(float(fr.mean()),1),
        }

    reversal_strength = {
        'short':  _rev_strength(df_short),
        'medium': _rev_strength(df_medium),
        'long':   _rev_strength(df_long),
    }

    # ════════════════════════════════════════════════════════════════════
    # 3. FAILURE TYPING — classify what happens when edge fails
    # ════════════════════════════════════════════════════════════════════
    # fwd_ret > 0 but < TRUE_REV_THR → DEAD_CAT (moved up but not enough)
    # fwd_ret <= -0.02               → CONTINUATION_TRAP (kept falling hard)
    # fwd_ret in (-0.02, 0]          → DRIFT_FAILURE (drifted lower slowly)
    # fake_reversal: fwd_ret > 0 but returned back (proxy: fwd_ret < 0.5 * atr_pct)

    TYPED_STATES = ['VELOCITY_EXHAUSTION','EXHAUSTION','PANIC','POTENTIAL_BOUNCE','STABILIZATION']
    failure_types = {}
    for state in TYPED_STATES:
        sub     = df_long[df_long['state']==state]
        sub_f   = sub[sub['true_rev']==False]
        n_total = len(sub_f)
        if n_total < 5:
            continue
        dead_cat    = ((sub_f['fwd_ret'] > 0) & (sub_f['fwd_ret'] < TRUE_REV_THR)).sum()
        cont_trap   = (sub_f['fwd_ret'] <= -0.02).sum()
        drift_fail  = ((sub_f['fwd_ret'] > -0.02) & (sub_f['fwd_ret'] <= 0)).sum()
        fake_rev    = ((sub_f['fwd_ret'] > TRUE_REV_THR * 0.3) &
                       (sub_f['fwd_ret'] < sub_f['atr_pct']/100)).sum()

        # Recent vs historical clustering
        sub_rec  = sub[sub['bar_time'] >= short_cut]
        sub_hist = sub[sub['bar_time'] <  short_cut]
        rec_fr   = sub_rec['failure'].mean()  if len(sub_rec)  > 0 else 0
        hist_fr  = sub_hist['failure'].mean() if len(sub_hist) > 0 else 0
        clustering = bool(rec_fr > hist_fr * 1.3 and len(sub_rec) >= 5)

        failure_types[state] = {
            'n_failures': int(n_total),
            'dead_cat_pct':       round(dead_cat/n_total*100, 1),
            'continuation_trap_pct': round(cont_trap/n_total*100, 1),
            'drift_failure_pct':  round(drift_fail/n_total*100, 1),
            'fake_reversal_pct':  round(fake_rev/n_total*100, 1),
            'worst_type':         max([
                ('DEAD_CAT',          dead_cat),
                ('CONTINUATION_TRAP', cont_trap),
                ('DRIFT_FAILURE',     drift_fail),
            ], key=lambda x: x[1])[0],
            'recent_fail_rate':   round(float(rec_fr)*100, 1),
            'hist_fail_rate':     round(float(hist_fr)*100, 1),
            'clustering':         clustering,
            'cluster_severity':   'HIGH' if rec_fr > hist_fr*1.5 else 'MODERATE' if clustering else 'NONE',
        }

    # ════════════════════════════════════════════════════════════════════
    # 4. ADAPTIVE COGNITIVE MAP — combined P_TR with adaptive weights
    # ════════════════════════════════════════════════════════════════════

    def _adaptive_weights(alpha_status, n_short):
        """
        Returns (w_short, w_medium, w_long) based on edge dynamics.
        If weakening/collapsing → trust recent more.
        If stable or unknown   → trust long-term structure more.
        If strengthening       → blend with medium weight on short.
        """
        if alpha_status == 'COLLAPSING':
            return (0.65, 0.25, 0.10)
        elif alpha_status == 'WEAKENING':
            ws = 0.50 if n_short >= 8 else 0.35
            wl = 0.15
            return (ws, round(1-ws-wl,2), wl)
        elif alpha_status == 'STRENGTHENING':
            ws = 0.45 if n_short >= 10 else 0.30
            wl = 0.20
            return (ws, round(1-ws-wl,2), wl)
        else:  # STABLE / UNKNOWN
            ws = 0.20 if n_short >= 5 else 0.10
            wl = 0.40
            return (ws, round(1-ws-wl,2), wl)

    cognitive_map  = {}
    suppressed     = []
    promoted       = []

    for key, ad in alpha_decay.items():
        p_l = ad['long']['p'];   p_m = ad['medium']['p'];   p_s = ad['short']['p']
        status  = ad['alpha_status']
        n_short = ad['short']['n']

        ws, wm, wl = _adaptive_weights(status, n_short)

        available = [(p_s, ws), (p_m, wm), (p_l, wl)]
        valid      = [(p, w) for p, w in available if p is not None]
        total_w    = sum(w for _, w in valid)
        combined   = round(sum(p*w for p,w in valid)/total_w, 1) if total_w > 0 else p_l

        # Bayesian posterior for the combined (uses medium+long as prior, short as evidence)
        if p_l is not None and p_s is not None:
            n_l = ad['long']['n'];    n_s = ad['short']['n']
            old_s = round(p_l/100 * n_l); old_f = n_l - old_s
            rec_s = round(p_s/100 * n_s); rec_f = n_s - rec_s
            if n_l > 0 and n_s > 0:
                old_n  = old_s + old_f
                scale  = max(20, min(old_n*0.3, 60))
                pa     = 8  + old_s/old_n * scale
                pb     = 12 + old_f/old_n * scale
                post_a = pa + rec_s;   post_b = pb + rec_f
                mean   = post_a/(post_a+post_b)
                ci80lo = beta_dist.ppf(0.10, post_a, post_b)
                ci80hi = beta_dist.ppf(0.90, post_a, post_b)
                bayesian_p = round(float(mean)*100,1)
                ci80 = [round(float(ci80lo)*100,1), round(float(ci80hi)*100,1)]
            else:
                bayesian_p = combined;  ci80 = [None, None]
        else:
            bayesian_p = combined;  ci80 = [None, None]

        # Edge confidence
        n_total = ad['long']['n']
        if   n_total > 200 and abs(ad['decay_velocity'] or 0) < 8:
            confidence = 'HIGH'
        elif n_total > 60:
            confidence = 'MEDIUM'
        else:
            confidence = 'LOW'

        cognitive_map[key] = {
            'state':            ad['state'],
            'regime':           ad['regime'],
            'combined_p_tr':    combined,
            'bayesian_p_tr':    bayesian_p,
            'ci_80':            ci80,
            'p_long':           p_l,
            'p_medium':         p_m,
            'p_short':          p_s,
            'alpha_status':     status,
            'decay_velocity':   ad['decay_velocity'],
            'decay_accel':      ad['decay_acceleration'],
            'failure_clustering': ad['failure_clustering'],
            'adaptive_weights': {'short': ws, 'medium': wm, 'long': wl},
            'confidence':       confidence,
            'n_long':           n_total,
            'n_short':          n_short,
        }

        if status in ('COLLAPSING','WEAKENING'):
            suppressed.append({'key': key, 'status': status, 'decay_v': ad['decay_velocity']})
        elif status == 'STRENGTHENING':
            promoted.append({'key': key, 'combined_p': combined})

    # Sort suppressed/promoted
    suppressed.sort(key=lambda x: x['decay_v'] or 0)
    promoted.sort(key=lambda x: x['combined_p'] or 0, reverse=True)

    # ════════════════════════════════════════════════════════════════════
    # 5. ADAPTIVE MARKET PHYSICS (extended multi-horizon)
    # ════════════════════════════════════════════════════════════════════

    def _physics(slice_df, label):
        out = {}
        # a. Pressure accumulation: |drop_accel| × atr_z.clip(0) — high = sellers pushing hard
        slice_df = slice_df.copy()
        slice_df['pressure'] = slice_df['drop_accel'].abs() * slice_df['atr_z'].clip(lower=0)
        high_p = slice_df['pressure'] > slice_df['pressure'].quantile(0.75)
        out['pressure_high'] = {
            'threshold_q75': round(float(slice_df['pressure'].quantile(0.75)),3),
            'p_tr': round(float(slice_df.loc[high_p,'true_rev'].mean())*100,1) if high_p.sum()>5 else None,
            'n':    int(high_p.sum()),
        }
        out['pressure_low'] = {
            'p_tr': round(float(slice_df.loc[~high_p,'true_rev'].mean())*100,1) if (~high_p).sum()>5 else None,
            'n':    int((~high_p).sum()),
        }

        # b. Exhaustion timing: among VEL_EXHAUSTION/EXHAUSTION/PANIC, what fwd_ret distribution?
        exhaust = slice_df[slice_df['state'].isin(['VELOCITY_EXHAUSTION','EXHAUSTION','PANIC'])]
        true_rev_ex = exhaust[exhaust['true_rev']]
        if len(true_rev_ex) >= 5:
            fr = true_rev_ex['fwd_ret']*100
            out['exhaustion_timing'] = {
                'n': len(true_rev_ex),
                'median_gain': round(float(fr.median()),1),
                'p25_gain':    round(float(fr.quantile(0.25)),1),
                'p75_gain':    round(float(fr.quantile(0.75)),1),
            }

        # c. Behavioral persistence: % of consecutive same-state bars
        total_bars = len(slice_df)
        if total_bars > 0:
            changes = 0
            for sym, g in slice_df.groupby('symbol'):
                g2 = g.sort_values('bar_time')
                sts = g2['state'].values
                changes += (sts[1:] != sts[:-1]).sum()
            persistence = round(1 - changes/total_bars, 3)
            out['behavioral_persistence'] = persistence

        # d. Liquidity absorption
        la = slice_df[(slice_df['vol_r'] < 0.7) & (slice_df['atr_z'] > 0.5)]
        out['liquidity_absorption'] = {
            'n': len(la),
            'p_tr': round(float(la['true_rev'].mean())*100,1) if len(la)>=5 else None,
        }
        return out

    market_physics = {
        'short':  _physics(df_short,  'short'),
        'medium': _physics(df_medium, 'medium'),
        'long':   _physics(df_long,   'long'),
    }

    # ════════════════════════════════════════════════════════════════════
    # 6. SELF-EVOLUTION LOOP — summary of what the system learned
    # ════════════════════════════════════════════════════════════════════

    structure_alerts = []
    # Regime shift alerts
    for reg, rd in regime_drift.items():
        if rd['status'] == 'SHIFTING':
            structure_alerts.append({
                'type':      'REGIME_SHIFT',
                'regime':    reg,
                'long_pct':  rd['long_pct'],
                'short_pct': rd['short_pct'],
                'delta':     rd['delta_l_s'],
                'severity':  'HIGH' if abs(rd['delta_l_s']) > 15 else 'MODERATE',
            })
    # Transition drift alerts
    for state, td in transition_drift.items():
        if td['drifting'] and td['top_movers']:
            structure_alerts.append({
                'type':      'TRANSITION_DRIFT',
                'state':     state,
                'l1_norm':   td['l1_norm'],
                'top_mover': td['top_movers'][0],
                'severity':  'HIGH' if td['l1_norm'] > 0.3 else 'MODERATE',
            })
    # Volatility persistence shift
    if vp_delta is not None and abs(vp_delta) > 0.08:
        structure_alerts.append({
            'type':       'VOL_PERSISTENCE_CHANGE',
            'long':       vol_persist['long'],
            'short':      vol_persist['short'],
            'delta':      vp_delta,
            'direction':  'INCREASING' if vp_delta > 0 else 'DECREASING',
            'severity':   'HIGH' if abs(vp_delta) > 0.15 else 'MODERATE',
        })
    # Failure clustering alerts
    for state, ft in failure_types.items():
        if ft['clustering'] and ft['cluster_severity'] == 'HIGH':
            structure_alerts.append({
                'type':      'FAILURE_CLUSTER',
                'state':     state,
                'recent_fr': ft['recent_fail_rate'],
                'hist_fr':   ft['hist_fail_rate'],
                'worst_type':ft['worst_type'],
                'severity':  'HIGH',
            })

    structure_alerts.sort(key=lambda x: {'HIGH':0,'MODERATE':1,'LOW':2}.get(x.get('severity','LOW'),2))

    # Determine overall structure stability
    high_alerts = sum(1 for a in structure_alerts if a.get('severity')=='HIGH')
    mod_alerts  = sum(1 for a in structure_alerts if a.get('severity')=='MODERATE')
    if high_alerts >= 3:
        structure_stability = 'UNSTABLE'
    elif high_alerts >= 1 or mod_alerts >= 3:
        structure_stability = 'SHIFTING'
    else:
        structure_stability = 'STABLE'

    # Top edges from cognitive map (by combined P and confidence)
    top_edges = sorted(
        [(k,v) for k,v in cognitive_map.items() if v.get('p_long') is not None],
        key=lambda x: (x[1].get('combined_p_tr') or 0),
        reverse=True
    )[:6]

    # Dominant failure mode
    all_failure_counts = {}
    for ft in failure_types.values():
        for typ in ['dead_cat_pct','continuation_trap_pct','drift_failure_pct']:
            k = typ.replace('_pct','').upper()
            all_failure_counts[k] = all_failure_counts.get(k,0) + ft.get(typ,0)
    dominant_failure = max(all_failure_counts, key=all_failure_counts.get) if all_failure_counts else 'UNKNOWN'

    summary = (
        f"🧬 EGX Evolving Structure | {len(cognitive_map)} edges | "
        f"{len(suppressed)} suppressed | {len(promoted)} promoted | "
        f"{len(structure_alerts)} alerts | stability={structure_stability} | "
        f"dominant_failure={dominant_failure}"
    )

    return {
        'success':              True,
        'alpha_decay':          alpha_decay,
        'cognitive_map':        cognitive_map,
        'suppressed_edges':     suppressed,
        'promoted_edges':       promoted,
        'structural_drift': {
            'transition_drift': transition_drift,
            'regime_drift':     regime_drift,
            'vol_persistence':  vol_persist,
            'vol_persistence_delta': vp_delta,
            'breadth_rev_corr': breadth_rev_corr,
            'reversal_strength':reversal_strength,
        },
        'failure_types':        failure_types,
        'market_physics':       market_physics,
        'structure_alerts':     structure_alerts,
        'structure_stability':  structure_stability,
        'top_edges':            [{'key':k, **v} for k,v in top_edges],
        'dominant_failure_mode':dominant_failure,
        'horizons': {
            'short_days':  SHORT_DAYS,
            'medium_days': MEDIUM_DAYS,
            'total_bars':  len(df_long),
        },
        'summary': summary,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MARKET EVOLUTION ENGINE
# Meta-Learning: discovers which edges are DURABLE vs COLLAPSING,
# models causal physics, reversal half-life, behavioral attractors,
# structural evolution timeline, and failure topology.
# ═══════════════════════════════════════════════════════════════════════════

def cmd_market_evolution(params):
    """
    نموذج تطور السوق الكلي — Meta-Learning Engine.

    يكتشف:
      1. Edge Classification (DURABLE/ADAPTIVE/CYCLICAL/FRAGILE/COLLAPSING)
      2. Reversal Half-Life   — P(TR | duration_in_state=k) عبر الأفق
      3. Structural Evolution Timeline — P(TR) الشهري لكل edge
      4. Causal Physics       — ما الذي يسبق الانعكاس فعلياً؟
      5. Failure Topology     — كيف تفشل كل حافة؟ (5 أنواع)
      6. Behavioral Attractors — ما هي جاذبية كل حالة؟
      7. Instability Zones    — أين الثقة أدنى؟
      8. Cognitive Map        — الخريطة الإدراكية الشاملة
    """
    from scipy.stats import pearsonr, beta as beta_dist

    fwd_bars     = int(params.get('fwd_bars', 5))
    TRUE_REV_THR = float(params.get('true_rev_thr', 0.03))
    SHORT_DAYS   = int(params.get('short_days',  30))
    MEDIUM_DAYS  = int(params.get('medium_days', 180))
    decay_lambda = float(params.get('decay_lambda', 0.7))

    # ── Shared data pipeline (identical to cmd_evolving_structure) ─────
    con = get_connection()
    oh  = pd.read_sql(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time", con)
    su  = pd.read_sql("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL", con)
    con.close()
    if oh.empty or len(oh) < 1000:
        return {"error": "بيانات OHLCV غير كافية"}

    now_ts = int(oh['bar_time'].max())
    sector_map = dict(zip(su['symbol'], su['sector'].fillna('')))
    def _sec(sym):
        s = str(sector_map.get(sym, '')).lower()
        if any(x in s for x in ['بنك','bank','مالي','finance','تأمين']):  return 'BANKS'
        if any(x in s for x in ['عقار','real_estate','housing']):         return 'REAL_ESTATE'
        if any(x in s for x in ['اتصال','telecom','tech','إعلام']):       return 'TELECOM_TECH'
        if any(x in s for x in ['صناع','industri','cement','كيماو']):     return 'INDUSTRIALS'
        if any(x in s for x in ['غذاء','food','consumer','دواء']):        return 'CONSUMER'
        return 'OTHER'

    def _rsi14(c):
        d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
        ag=g.ewm(com=13,min_periods=14).mean(); al=l.ewm(com=13,min_periods=14).mean()
        return 100-100/(1+ag/al.replace(0,np.nan))
    def _atr14(h,lo,c):
        tr=pd.concat([h-lo,(h-c.shift()).abs(),(lo-c.shift()).abs()],axis=1).max(axis=1)
        return tr.ewm(com=13,min_periods=14).mean()

    parts = []
    for sym, g in oh.groupby('symbol'):
        g=g.sort_values('bar_time').reset_index(drop=True)
        if len(g)<40: continue
        g=g.copy()
        g['rsi']        = _rsi14(g['close'])
        atr             = _atr14(g['high'],g['low'],g['close'])
        g['atr_pct']    = atr/g['close']*100
        g['vol_r']      = g['volume']/g['volume'].rolling(20,min_periods=5).mean()
        g['mom5']       = g['close'].pct_change(5)*100
        g['mom10']      = g['close'].pct_change(10)*100
        g['mom20']      = g['close'].pct_change(20)*100
        g['drop_accel'] = g['mom5']-g['mom10']/2.0
        g['rsi_slope']  = g['rsi'].diff(3)
        atr_mu          = atr.rolling(20,min_periods=10).mean()
        atr_sd          = atr.rolling(20,min_periods=10).std()
        g['atr_z']      = (atr-atr_mu)/atr_sd.replace(0,np.nan)
        g['daily_val']  = g['close']*g['volume']
        g['avg_val20']  = g['daily_val'].rolling(20,min_periods=5).mean()
        g[f'fwd{fwd_bars}'] = g['close'].shift(-fwd_bars)/g['close']-1
        age_days        = (now_ts-g['bar_time'])/86400.0
        g['tw']         = np.exp(-decay_lambda*age_days/365.0)
        g['symbol']     = sym
        g['sector_grp'] = _sec(sym)
        parts.append(g)
    if not parts: return {"error": "لا توجد بيانات"}

    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=['rsi','mom5','mom10','atr_z',f'fwd{fwd_bars}']).copy()
    df['rsi']       = df['rsi'].fillna(50)
    df['vol_r']     = df['vol_r'].fillna(1.0)
    df['mom20']     = df['mom20'].fillna(0)
    df['rsi_slope'] = df['rsi_slope'].fillna(0)
    df['avg_val20'] = df['avg_val20'].fillna(0)

    def _classify(r):
        rsi=r['rsi']; m5=r['mom5']; m10=r['mom10']; m20=r['mom20']
        az=r['atr_z']; vr=r['vol_r']; da=r['drop_accel']; rs=r['rsi_slope']
        if da<-5  and az>1.5  and rs<-5:                  return 'PANIC'
        if m5>5   and m10>5   and az>0.5  and rsi>60:     return 'ACCELERATING_UP'
        if da<-3  and rsi<=35 and m10<-3:                 return 'VELOCITY_EXHAUSTION'
        if rsi<=32 and m5<-3  and m10<-2:                 return 'EXHAUSTION'
        if m5<-7  and da<-2:                              return 'SHARP_DROP'
        if m5<-4  and m10<-5  and az<0.5  and rsi<40:     return 'CONTINUATION_DOWN'
        if m20>5  and rsi>60  and m5<m10*0.5 and vr>1.3:  return 'DISTRIBUTION'
        if 30<rsi<=45 and abs(m5)<3 and az<0 and rs>=-1:  return 'STABILIZATION'
        if 35<rsi<=50 and m5>2 and m10<-5:                return 'POTENTIAL_BOUNCE'
        if m5>2   and m10>2   and rsi>50:                  return 'TRENDING_UP'
        return 'NEUTRAL'

    df['state'] = df.apply(_classify, axis=1)
    df['bar_dt'] = pd.to_datetime(df['bar_time'], unit='s').dt.date

    agg = df.groupby('bar_dt').agg(
        mkt_m5=('mom5','median'),
        br=('rsi', lambda x: (x>50).mean()),
        n_pan=('atr_z', lambda x: (x>1.5).mean()*100),
    ).reset_index()
    def _reg(m5,br,np_):
        if br>=0.65 and m5>3:             return 'SURGE'
        elif br>=0.55 and m5>1:           return 'UP'
        elif np_>=20  and br<=0.35:       return 'CRASH'
        elif br<=0.40 and m5<-1:          return 'DOWN'
        elif abs(m5)<1.5 and 0.40<br<0.60: return 'SIDEWAYS'
        return 'NEUTRAL'
    agg['regime']  = agg.apply(lambda r: _reg(r['mkt_m5'],r['br'],r['n_pan']), axis=1)
    agg['breadth'] = agg['br']
    df['regime']   = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['regime']))).fillna('NEUTRAL')
    df['breadth']  = df['bar_dt'].map(dict(zip(agg['bar_dt'],agg['breadth']))).fillna(0.5)

    fwd_col        = f'fwd{fwd_bars}'
    df['fwd_ret']  = df[fwd_col]
    df['true_rev'] = df['fwd_ret'] > TRUE_REV_THR
    df['failure']  = df['fwd_ret'] <= 0
    df['pressure'] = df['drop_accel'].abs() * df['atr_z'].clip(lower=0)

    # ── Duration in state ───────────────────────────────────────────────
    df['dur_in'] = 1
    for sym, g in df.groupby('symbol'):
        g2 = g.sort_values('bar_time')
        st = g2['state'].values
        dur = np.ones(len(st), dtype=int)
        for i in range(1, len(st)): dur[i] = dur[i-1]+1 if st[i]==st[i-1] else 1
        df.loc[g2.index, 'dur_in'] = dur

    # ── Monthly period for timeline ─────────────────────────────────────
    df['year_month'] = pd.to_datetime(df['bar_time'], unit='s').dt.to_period('M')

    # ── Horizon slices ──────────────────────────────────────────────────
    short_cut  = now_ts - SHORT_DAYS  * 86400
    medium_cut = now_ts - MEDIUM_DAYS * 86400
    df_short   = df[df['bar_time'] >= short_cut].copy()
    df_medium  = df[df['bar_time'] >= medium_cut].copy()
    df_long    = df.copy()

    REVERSAL_STATES = ['VELOCITY_EXHAUSTION','EXHAUSTION','PANIC','POTENTIAL_BOUNCE','STABILIZATION']
    REGIMES_ALL     = ['CRASH','DOWN','SIDEWAYS','NEUTRAL','UP','SURGE']
    KEY_COMBOS      = [
        ('VELOCITY_EXHAUSTION','CRASH'), ('VELOCITY_EXHAUSTION','DOWN'),
        ('VELOCITY_EXHAUSTION','UP'),    ('VELOCITY_EXHAUSTION','NEUTRAL'),
        ('EXHAUSTION','CRASH'),          ('EXHAUSTION','DOWN'),
        ('EXHAUSTION','UP'),             ('EXHAUSTION','SIDEWAYS'),
        ('PANIC','CRASH'),               ('PANIC','DOWN'),
        ('PANIC','UP'),
        ('POTENTIAL_BOUNCE','CRASH'),    ('POTENTIAL_BOUNCE','UP'),
        ('STABILIZATION','CRASH'),       ('STABILIZATION','DOWN'),
    ]

    # ════════════════════════════════════════════════════════════════════
    # 1. REGIME VARIANCE per state — how much does P(TR) spread across regimes?
    # ════════════════════════════════════════════════════════════════════
    state_regime_variance = {}
    for state in REVERSAL_STATES:
        p_vals = []
        for reg in REGIMES_ALL:
            sub = df_long[(df_long['state']==state)&(df_long['regime']==reg)]
            if len(sub) >= 8: p_vals.append(float(sub['true_rev'].mean())*100)
        state_regime_variance[state] = round(float(np.std(p_vals)), 1) if len(p_vals)>=2 else None

    # ════════════════════════════════════════════════════════════════════
    # 2. EDGE CLASSIFICATION — 5-tier meta-learning
    # ════════════════════════════════════════════════════════════════════
    def _tier(p_l, p_m, p_s, n_l, n_s, decay_v, fc, rv, consistency):
        if p_l is None or n_l < 8:            return 'UNKNOWN'
        # Collapsing: strong downward velocity + failure clustering
        if decay_v is not None and decay_v < -15 and fc:
            return 'COLLAPSING'
        # Fragile: high inconsistency across horizons + moderate decay
        if consistency is not None and consistency > 12 and (decay_v is None or decay_v < -5):
            return 'FRAGILE'
        # Durable: small std across horizons, slow decay, sufficient sample
        if consistency is not None and consistency <= 6 and abs(decay_v or 0) <= 8 and n_l >= 50:
            return 'DURABLE'
        # Adaptive: regime-dependent but consistent within regime
        if rv is not None and rv > 12 and consistency is not None and consistency < 10 and abs(decay_v or 0) <= 12:
            return 'ADAPTIVE'
        # Cyclical: moderate inconsistency correlated with regime cycles
        if consistency is not None and 6 < consistency <= 12:
            return 'CYCLICAL'
        # Fragile: clear but unstable decline
        if decay_v is not None and decay_v < -12:
            return 'FRAGILE'
        return 'DURABLE'

    def _horizon_p(sdf, state, regime):
        sub = sdf[(sdf['state']==state)&(sdf['regime']==regime)]
        n   = len(sub)
        if n < 3: return None, n
        return round(float(sub['true_rev'].mean())*100, 1), n

    edge_meta = {}
    for (state, regime) in KEY_COMBOS:
        p_l, n_l = _horizon_p(df_long,   state, regime)
        p_m, n_m = _horizon_p(df_medium, state, regime)
        p_s, n_s = _horizon_p(df_short,  state, regime)

        # Decay velocity
        dv = None
        if p_l and p_s and n_s >= 5:   dv = round(p_s - p_l, 1)
        elif p_l and p_m and n_m >= 10: dv = round((p_m - p_l) * 0.6, 1)

        # Decay acceleration: is the decay speeding up?
        da = None
        if p_l and p_m and p_s and n_s >= 5:
            da = round((p_s - p_m) - (p_m - p_l), 1)

        # Horizon consistency (std across available horizons)
        ps_avail = [p for p in [p_l, p_m, p_s] if p is not None]
        consistency = round(float(np.std(ps_avail)), 1) if len(ps_avail) >= 2 else None

        # Failure clustering (late vs early half of data)
        sub_all = df_long[(df_long['state']==state)&(df_long['regime']==regime)]
        fc = False
        if len(sub_all) >= 10:
            mid_ts = sub_all['bar_time'].median()
            e_fr = sub_all[sub_all['bar_time']< mid_ts]['failure'].mean()
            l_fr = sub_all[sub_all['bar_time']>=mid_ts]['failure'].mean()
            fc   = bool(l_fr > e_fr * 1.35 and
                        len(sub_all[sub_all['bar_time']>=mid_ts]) >= 5)

        # Failure persistence: lag-1 autocorr of failure sequence
        fp = None
        if len(sub_all) >= 15:
            try:
                fseries = sub_all.sort_values('bar_time')['failure'].astype(float).values
                r, _ = pearsonr(fseries[:-1], fseries[1:])
                fp = round(float(r), 3) if not np.isnan(r) else None
            except: pass

        rv = state_regime_variance.get(state)
        tier = _tier(p_l, p_m, p_s, n_l, n_s, dv, fc, rv, consistency)

        # Combined P with Bayesian posterior (for ranking)
        bayes_p = None
        if p_l and p_s and n_s >= 5 and n_l > 0:
            os = round(p_l/100*n_l); of_ = n_l - os
            rs = round(p_s/100*n_s); rf = n_s - rs
            scale = max(20, min(n_l*0.25, 60))
            pa = 8 + os/n_l*scale; pb = 12 + of_/n_l*scale
            post_a = pa+rs; post_b = pb+rf
            mean   = post_a/(post_a+post_b)
            bayes_p = round(float(mean)*100, 1)
        elif p_l:
            # Weight medium more heavily when no short data
            parts_b = [(v,w) for v,w in [(p_m, 0.55),(p_l, 0.45)] if v]
            total_w = sum(w for _,w in parts_b)
            bayes_p = round(sum(v*w for v,w in parts_b)/total_w, 1) if total_w else p_l

        edge_meta[f"{state}|{regime}"] = {
            'state':               state,
            'regime':              regime,
            'tier':                tier,
            'p_long':              p_l,
            'p_medium':            p_m,
            'p_short':             p_s,
            'n_long':              n_l,
            'n_short':             n_s,
            'bayes_p':             bayes_p,
            'decay_velocity':      dv,
            'decay_acceleration':  da,
            'horizon_consistency': consistency,
            'drift_sensitivity':   rv,
            'failure_clustering':  fc,
            'failure_persistence': fp,
        }

    # Tier summary
    tier_counts = {}
    for em in edge_meta.values():
        t = em['tier']
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # ════════════════════════════════════════════════════════════════════
    # 3. REVERSAL HALF-LIFE — P(TR | dur_in_state=k) decay profile
    # ════════════════════════════════════════════════════════════════════
    reversal_halflife = {}
    for state in REVERSAL_STATES:
        sub = df_long[df_long['state']==state]
        if len(sub) < 15: continue
        sub_s = df_short[df_short['state']==state]

        def _dur_profile(slice_df, max_dur=7):
            profile = []
            peak_p = 0; peak_d = 1
            for d in range(1, max_dur+1):
                grp = slice_df[slice_df['dur_in']==d]
                n   = len(grp)
                if n < 3:
                    profile.append({'dur': d, 'p_tr': None, 'n': 0})
                    continue
                p = round(float(grp['true_rev'].mean())*100, 1)
                profile.append({'dur': d, 'p_tr': p, 'n': n})
                if p > peak_p: peak_p = p; peak_d = d
            # 8+ bucket
            grp8 = slice_df[slice_df['dur_in'] >= 8]
            if len(grp8) >= 3:
                profile.append({'dur': '8+', 'p_tr': round(float(grp8['true_rev'].mean())*100,1), 'n': len(grp8)})
            return profile, peak_p, peak_d

        profile_long, peak_p, peak_d = _dur_profile(sub)
        profile_short, _, _          = _dur_profile(sub_s, max_dur=5)

        # Half-life: first dur where P(TR) drops below peak/2 (after peak)
        half_tgt = peak_p / 2 if peak_p > 0 else 0
        half_life = None
        past_peak = False
        for dp in profile_long:
            if dp['dur'] == peak_d: past_peak = True; continue
            if past_peak and dp['p_tr'] is not None and dp['p_tr'] <= half_tgt:
                half_life = dp['dur']; break

        # Optimal entry duration: duration bucket with highest P(TR)
        valid = [(dp['dur'], dp['p_tr']) for dp in profile_long if dp['p_tr'] is not None]
        opt_dur, opt_p = max(valid, key=lambda x: x[1]) if valid else (None, None)

        reversal_halflife[state] = {
            'profile_long':   profile_long,
            'profile_short':  profile_short,
            'peak_p_tr':      peak_p,
            'peak_duration':  peak_d,
            'half_life':      half_life,
            'optimal_entry_dur': opt_dur,
            'optimal_entry_p':   opt_p,
        }

    # ════════════════════════════════════════════════════════════════════
    # 4. STRUCTURAL EVOLUTION TIMELINE — monthly P(TR) per key edge
    # ════════════════════════════════════════════════════════════════════
    TIMELINE_COMBOS = [
        ('VELOCITY_EXHAUSTION','CRASH'),
        ('EXHAUSTION','CRASH'),
        ('PANIC','CRASH'),
        ('POTENTIAL_BOUNCE','UP'),
    ]
    evolution_timeline = {}
    for (state, regime) in TIMELINE_COMBOS:
        sub = df_long[(df_long['state']==state)&(df_long['regime']==regime)]
        if len(sub) < 10: continue
        monthly = (sub.groupby('year_month')
                      .agg(p_tr=('true_rev', lambda x: round(float(x.mean())*100,1)),
                           n=('true_rev','count'))
                      .reset_index())
        monthly = monthly[monthly['n'] >= 3]
        evolution_timeline[f"{state}|{regime}"] = [
            {'period': str(r['year_month']), 'p_tr': r['p_tr'], 'n': int(r['n'])}
            for _, r in monthly.iterrows()
        ]

    # ════════════════════════════════════════════════════════════════════
    # 5. CAUSAL PHYSICS — what distinguishes reversals from failures?
    # ════════════════════════════════════════════════════════════════════
    def _causal_discriminants(az_r, az_f, rs_r, rs_f, vr_r, vr_f, da_r, da_f, br_r, br_f):
        """Identify which factors most strongly discriminate reversal from failure."""
        discs = []
        if az_r and az_f and abs(az_r - az_f) > 0.3:
            d = 'HIGH_ATR_Z → reversal' if az_r > az_f else 'LOW_ATR_Z → reversal'
            discs.append(d)
        if rs_r and rs_f and abs(rs_r - rs_f) > 0.5:
            d = 'RSI_SLOPE_UP → reversal' if rs_r > rs_f else 'RSI_SLOPE_DOWN → reversal'
            discs.append(d)
        if vr_r and vr_f and abs(vr_r - vr_f) > 0.15:
            d = 'HIGH_VOLUME → reversal' if vr_r > vr_f else 'LOW_VOLUME → reversal'
            discs.append(d)
        if da_r and da_f and abs(da_r - da_f) > 1:
            d = 'DECEL_SELLING → reversal' if da_r > da_f else 'ACCEL_SELLING → reversal'
            discs.append(d)
        if br_r and br_f and abs(br_r - br_f) > 0.05:
            d = 'HIGH_BREADTH → reversal' if br_r > br_f else 'LOW_BREADTH → reversal'
            discs.append(d)
        return discs[:3]

    causal_physics = {}
    for state in ['VELOCITY_EXHAUSTION','EXHAUSTION','PANIC']:
        sub = df_long[df_long['state']==state].copy()
        if len(sub) < 20: continue

        rev  = sub[sub['true_rev']]
        fail = sub[~sub['true_rev']]
        if len(rev) < 5 or len(fail) < 5: continue

        def _med(s): return round(float(s.median()),3) if len(s)>0 else None
        def _pct(s): return round(float(s.mean())*100,1) if len(s)>0 else None

        # Pressure at signal bar
        pq75     = sub['pressure'].quantile(0.75)
        hi_press = sub['pressure'] >= pq75
        p_hi     = _pct(sub[hi_press]['true_rev'])
        p_lo     = _pct(sub[~hi_press]['true_rev'])

        # ATR_z signature
        atr_rev  = _med(rev['atr_z']);   atr_fail = _med(fail['atr_z'])

        # RSI slope (positive = RSI curling up = early reversal sign)
        rsi_rev  = _med(rev['rsi_slope']); rsi_fail = _med(fail['rsi_slope'])

        # Volume ratio (capitulation = high vol)
        vol_rev  = _med(rev['vol_r']);   vol_fail = _med(fail['vol_r'])

        # Momentum deceleration: drop_accel (less negative = decelerating sell-off)
        da_rev   = _med(rev['drop_accel']); da_fail = _med(fail['drop_accel'])

        # Breadth (market-wide RSI>50 fraction)
        br_rev   = _med(rev['breadth']); br_fail = _med(fail['breadth'])

        # Vol compression failure: atr_z < 0 at signal (compressed vol)
        vol_comp_sub = sub[sub['atr_z'] < 0]
        p_vol_comp   = _pct(vol_comp_sub['true_rev']) if len(vol_comp_sub) >= 5 else None

        causal_physics[state] = {
            'n_rev':               len(rev),
            'n_fail':              len(fail),
            'pressure_edge':       round(p_hi-p_lo,1) if p_hi and p_lo else None,
            'p_tr_high_pressure':  p_hi,
            'p_tr_low_pressure':   p_lo,
            'atr_z_at_reversal':   atr_rev,
            'atr_z_at_failure':    atr_fail,
            'rsi_slope_at_rev':    rsi_rev,
            'rsi_slope_at_fail':   rsi_fail,
            'vol_r_at_rev':        vol_rev,
            'vol_r_at_fail':       vol_fail,
            'drop_accel_at_rev':   da_rev,
            'drop_accel_at_fail':  da_fail,
            'breadth_at_rev':      br_rev,
            'breadth_at_fail':     br_fail,
            'p_tr_vol_compressed': p_vol_comp,
            'key_discriminants': _causal_discriminants(
                atr_rev, atr_fail, rsi_rev, rsi_fail, vol_rev, vol_fail, da_rev, da_fail, br_rev, br_fail),
        }

    # ════════════════════════════════════════════════════════════════════
    # 6. FAILURE TOPOLOGY — 5-type failure classification per state
    # ════════════════════════════════════════════════════════════════════
    failure_topology = {}
    for state in REVERSAL_STATES:
        sub  = df_long[df_long['state']==state]
        sub_f = sub[~sub['true_rev']]
        n_total = len(sub_f)
        if n_total < 10: continue
        n_all   = len(sub)

        # Type 1: DEAD_CAT — moved up briefly but not enough
        dead_cat   = ((sub_f['fwd_ret'] > 0) & (sub_f['fwd_ret'] < TRUE_REV_THR)).sum()
        # Type 2: CONTINUATION_TRAP — market continued falling hard
        cont_trap  = (sub_f['fwd_ret'] <= -0.025).sum()
        # Type 3: DRIFT_FAILURE — slowly bled lower
        drift_fail = ((sub_f['fwd_ret'] > -0.025) & (sub_f['fwd_ret'] <= 0)).sum()
        # Type 4: VOL_COMPRESSION — atr_z < 0 at signal (compressed, no energy)
        vol_comp   = (sub_f[sub_f['atr_z'] < 0]).shape[0]
        # Type 5: REGIME_TRAP — regime was CRASH/DOWN but regime changed before fwd bar
        # (approximate: signal bar's regime was bearish but fwd_ret was near zero)
        regime_trap = ((sub_f['regime'].isin(['CRASH','DOWN'])) &
                       (sub_f['fwd_ret'] > -0.01) & (sub_f['fwd_ret'] <= 0)).sum()

        # Severity of each type
        worst = max([
            ('DEAD_CAT', dead_cat), ('CONTINUATION_TRAP', cont_trap),
            ('DRIFT_FAILURE', drift_fail), ('VOL_COMPRESSION', vol_comp)
        ], key=lambda x: x[1])[0]

        # Recent vs historical failure rate
        sub_rec  = sub[sub['bar_time'] >= short_cut]
        sub_hist = sub[sub['bar_time'] <  short_cut]
        rec_fr   = float(sub_rec['failure'].mean())  if len(sub_rec)  > 3 else None
        hist_fr  = float(sub_hist['failure'].mean()) if len(sub_hist) > 3 else None
        cluster  = bool(rec_fr and hist_fr and rec_fr > hist_fr * 1.3 and len(sub_rec) >= 5)

        # Average magnitude of failure (how bad is it when it fails?)
        avg_fail_ret = round(float(sub_f['fwd_ret'].mean()*100), 1)
        p90_fail_ret = round(float(sub_f['fwd_ret'].quantile(0.10)*100), 1)  # worst 10%

        failure_topology[state] = {
            'n_signals':          n_all,
            'n_failures':         n_total,
            'overall_fail_rate':  round(n_total/n_all*100, 1),
            'dead_cat_pct':       round(dead_cat/n_total*100, 1),
            'continuation_trap_pct': round(cont_trap/n_total*100, 1),
            'drift_failure_pct':  round(drift_fail/n_total*100, 1),
            'vol_compression_pct':round(vol_comp/n_total*100, 1),
            'regime_trap_pct':    round(regime_trap/n_total*100, 1),
            'dominant_failure':   worst,
            'avg_fail_ret':       avg_fail_ret,
            'worst_10pct_ret':    p90_fail_ret,
            'recent_fail_rate':   round(rec_fr*100,1) if rec_fr else None,
            'hist_fail_rate':     round(hist_fr*100,1) if hist_fr else None,
            'failure_clustering': cluster,
        }

    # ════════════════════════════════════════════════════════════════════
    # 7. BEHAVIORAL ATTRACTORS — state persistence + gravity wells
    # ════════════════════════════════════════════════════════════════════
    pairs = []
    for sym, g in df_long.groupby('symbol'):
        g2 = g.sort_values('bar_time')
        sts = g2['state'].values
        for i in range(len(sts)-1): pairs.append((sts[i], sts[i+1]))

    behavioral_attractors = {}
    if pairs:
        tdf = pd.DataFrame(pairs, columns=['from','to'])
        total_trans = len(tdf)

        # State persistence = P(state → same state)
        persistence = {}
        for state, grp in tdf.groupby('from'):
            total = len(grp)
            same  = (grp['to'] == state).sum()
            persistence[state] = round(same/total*100, 1)

        # Attractor score = % of all transitions that arrive at this state
        # (from other states — measures magnetic pull)
        attractor = {}
        for state in tdf['to'].unique():
            incoming = ((tdf['to']==state) & (tdf['from']!=state)).sum()
            attractor[state] = round(incoming/total_trans*100, 1)

        # Escape velocity: once in state, how fast does it exit?
        # = 1 - persistence (% of time it leaves per bar)
        escape = {s: round(100-p, 1) for s,p in persistence.items()}

        # Compare short vs long persistence
        pairs_short = []
        for sym, g in df_short.groupby('symbol'):
            g2 = g.sort_values('bar_time')
            sts = g2['state'].values
            for i in range(len(sts)-1): pairs_short.append((sts[i], sts[i+1]))
        persist_short = {}
        if pairs_short:
            tdf_s = pd.DataFrame(pairs_short, columns=['from','to'])
            for state, grp in tdf_s.groupby('from'):
                same = (grp['to']==state).sum()
                persist_short[state] = round(same/len(grp)*100,1)

        top_attractors = sorted(attractor.items(), key=lambda x: x[1], reverse=True)[:6]
        behavioral_attractors = {
            'persistence':       persistence,
            'persistence_short': persist_short,
            'attractor_score':   dict(attractor),
            'escape_velocity':   escape,
            'top_attractors':    [{'state': s, 'score': v} for s,v in top_attractors],
            'reversal_state_persistence': {
                s: persistence.get(s) for s in REVERSAL_STATES if s in persistence
            },
            'trend_stickiness':  persistence.get('TRENDING_UP'),
            'trend_stickiness_short': persist_short.get('TRENDING_UP'),
        }

    # ════════════════════════════════════════════════════════════════════
    # 8. INSTABILITY ZONES — where model confidence is lowest
    # ════════════════════════════════════════════════════════════════════
    instability_zones = []
    for key, em in edge_meta.items():
        score   = 0
        reasons = []
        hc = em['horizon_consistency']
        if hc is not None and hc > 10: score += 2; reasons.append(f'inconsistency={hc}%')
        if em['n_long'] < 25:          score += 2; reasons.append(f'n={em["n_long"]}')
        if em['drift_sensitivity'] and em['drift_sensitivity'] > 15:
            score += 1; reasons.append(f'regime_var={em["drift_sensitivity"]}')
        if em['failure_clustering']:   score += 2; reasons.append('fail_cluster')
        if em['tier'] in ('FRAGILE','COLLAPSING'): score += 1; reasons.append(f'tier={em["tier"]}')
        if em['failure_persistence'] and em['failure_persistence'] > 0.2:
            score += 1; reasons.append(f'fail_persist={em["failure_persistence"]}')
        if score >= 2:
            instability_zones.append({
                'key': key, 'score': score, 'tier': em['tier'],
                'reasons': reasons, 'p_long': em['p_long'],
            })
    instability_zones.sort(key=lambda x: x['score'], reverse=True)

    # ════════════════════════════════════════════════════════════════════
    # 9. STRUCTURAL EVOLUTION INDICATORS
    # ════════════════════════════════════════════════════════════════════
    # Volatility clustering: ACF of ATR_z across horizons
    def _vol_acf(sdf, lag=1):
        if len(sdf) < 20: return None
        try:
            ac = sdf.groupby('symbol')['atr_z'].apply(
                lambda x: x.dropna().autocorr(lag) if len(x)>lag+1 else np.nan)
            return round(float(ac.dropna().mean()), 3)
        except: return None

    # Trend stickiness evolution (quarterly)
    def _trend_stick_quarterly(df_all):
        df_all = df_all.copy()
        df_all['quarter'] = pd.to_datetime(df_all['bar_time'],unit='s').dt.to_period('Q')
        result = {}
        for sym, g in df_all.groupby('symbol'):
            g2 = g.sort_values('bar_time')
            sts = g2['state'].values
            qts = g2['quarter'].values
            for i in range(len(sts)-1):
                q  = str(qts[i])
                is_tu_stay = int(sts[i]=='TRENDING_UP' and sts[i+1]=='TRENDING_UP')
                is_tu      = int(sts[i]=='TRENDING_UP')
                if q not in result: result[q] = [0, 0]
                result[q][0] += is_tu_stay
                result[q][1] += is_tu
        return {q: round(v[0]/v[1]*100,1) if v[1]>0 else None for q,v in result.items()}

    vol_acf = {
        'long':   _vol_acf(df_long),
        'medium': _vol_acf(df_medium),
        'short':  _vol_acf(df_short),
    }
    trend_stickiness_quarterly = _trend_stick_quarterly(df_long)

    struct_evolution = {
        'vol_clustering_acf':        vol_acf,
        'trend_stickiness_quarterly':trend_stickiness_quarterly,
    }

    # ════════════════════════════════════════════════════════════════════
    # 10. COGNITIVE MAP SYNTHESIS
    # ════════════════════════════════════════════════════════════════════
    dominant_tier  = max(tier_counts, key=tier_counts.get) if tier_counts else 'UNKNOWN'
    durable_edges  = sorted([(k,v) for k,v in edge_meta.items() if v['tier']=='DURABLE'],
                             key=lambda x: x[1].get('bayes_p') or 0, reverse=True)
    collapse_edges = [(k,v) for k,v in edge_meta.items() if v['tier']=='COLLAPSING']
    fragile_edges  = [(k,v) for k,v in edge_meta.items() if v['tier']=='FRAGILE']

    # Invariant behaviors: edges that work consistently across all regimes for a state
    invariant = []
    for state in REVERSAL_STATES:
        p_vals = []
        for reg in REGIMES_ALL:
            sub = df_long[(df_long['state']==state)&(df_long['regime']==reg)]
            if len(sub) >= 10: p_vals.append(float(sub['true_rev'].mean())*100)
        if len(p_vals) >= 4 and np.std(p_vals) < 5 and np.mean(p_vals) > 35:
            invariant.append({'state': state,
                               'mean_p_tr': round(float(np.mean(p_vals)),1),
                               'std': round(float(np.std(p_vals)),1),
                               'n_regimes': len(p_vals)})

    # Current market state
    cur_regime     = df_short['regime'].mode()[0] if len(df_short)>0 else df_long['regime'].mode()[0]
    cur_state_dist = (df_short['state'].value_counts(normalize=True)*100
                      if len(df_short)>0 else df_long['state'].value_counts(normalize=True)*100)
    top_states_now = [{'state': s, 'pct': round(float(v),1)}
                      for s,v in cur_state_dist.head(5).items()]

    # Narrative
    lines = []
    n_dur  = tier_counts.get('DURABLE', 0)
    n_coll = tier_counts.get('COLLAPSING', 0)
    n_frag = tier_counts.get('FRAGILE', 0)
    n_adap = tier_counts.get('ADAPTIVE', 0)
    n_cycl = tier_counts.get('CYCLICAL', 0)
    if n_dur >= 3:   lines.append(f"{n_dur} حواف متينة — هيكل موثوق")
    elif n_dur == 0: lines.append("لا حواف متينة — السوق في تحول هيكلي عميق")
    if n_coll > 0:   lines.append(f"{n_coll} حواف تنهار — احذر الإشارات القديمة")
    if n_frag > 0:   lines.append(f"{n_frag} حواف هشة — ظروف محدودة فقط")
    if n_adap > 0:   lines.append(f"{n_adap} حواف تكيُّفية — تتبع النظام الحالي ({cur_regime})")
    if invariant:    lines.append(f"{len(invariant)} سلوك لا-متغير عبر الأنظمة")
    narrative = " | ".join(lines) if lines else "بنية مستقرة"

    cognitive_map = {
        'current_regime':      cur_regime,
        'top_states_now':      top_states_now,
        'dominant_tier':       dominant_tier,
        'tier_distribution':   tier_counts,
        'durable_edges':       [{'key': k, 'p_long': v['p_long'], 'bayes_p': v['bayes_p'],
                                  'consistency': v['horizon_consistency']} for k,v in durable_edges[:4]],
        'collapsing_edges':    [{'key': k, 'p_long': v['p_long'], 'dv': v['decay_velocity']}
                                 for k,v in collapse_edges],
        'fragile_edges':       [{'key': k, 'p_long': v['p_long']} for k,v in fragile_edges[:4]],
        'invariant_behaviors': invariant,
        'worst_instability':   instability_zones[0] if instability_zones else None,
        'narrative':           narrative,
    }

    summary = (
        f"🧠 EGX Market Evolution | {len(df_long)} bars | {len(edge_meta)} edges | "
        f"DURABLE={n_dur} ADAPTIVE={n_adap} CYCLICAL={n_cycl} FRAGILE={n_frag} COLLAPSING={n_coll} | "
        f"regime={cur_regime} | invariant={len(invariant)}"
    )

    return {
        'success':           True,
        'edge_meta':         edge_meta,
        'tier_counts':       tier_counts,
        'cognitive_map':     cognitive_map,
        'reversal_halflife': reversal_halflife,
        'evolution_timeline':evolution_timeline,
        'causal_physics':    causal_physics,
        'failure_topology':  failure_topology,
        'behavioral_attractors': behavioral_attractors,
        'instability_zones': instability_zones,
        'structural_evolution': struct_evolution,
        'horizons': {'short_days': SHORT_DAYS, 'medium_days': MEDIUM_DAYS, 'total_bars': len(df_long)},
        'summary': summary,
        'macro_context': (lambda mc: {
            'macro_regime':       mc.get('macro_regime', 'UNKNOWN'),
            'equity_multiplier':  mc.get('equity_multiplier', 1.0),
            'real_interest_rate': mc.get('real_interest_rate'),
            'inflation_yoy':      mc.get('inflation_yoy') or mc.get('inflation_pct'),
            'cbe_rate':           mc.get('cbe_rate') or mc.get('cbe_rate_pct'),
            'gdp_yoy':            mc.get('gdp_yoy'),
            'fx_reserves_b':      mc.get('fx_reserves_b'),
            'trade_balance_m':    mc.get('trade_balance_m'),
            'external_debt_b':    mc.get('external_debt_b'),
            'inflation_momentum': mc.get('inflation_momentum'),
            'rate_cycle':         mc.get('rate_cycle'),
            'growth_trend':       mc.get('growth_trend'),
            'strategic_bias':     mc.get('strategic_bias'),
            '_fetched_at':        mc.get('_fetched_at'),
        } if mc else {'error': 'no macro data'})(_load_macro_context(168)),
    }


# ═══════════════════════════════════════════════════════════════════════════
COMMANDS = {
    'full_stats':         cmd_full_stats,
    'rolling_stats':      cmd_rolling_stats,
    'return_analysis':    cmd_return_analysis,
    'signal_backtest':    cmd_signal_backtest,
    'export_csv':         cmd_export_csv,
    'sector_momentum':    cmd_sector_momentum,
    'param_sweep':        cmd_param_sweep,
    'walk_forward':       cmd_walk_forward,
    'ml_signal':          cmd_ml_signal,
    'egx_patterns':       cmd_egx_patterns,
    'shap_analysis':      cmd_shap_analysis,
    'regime_detection':   cmd_regime_detection,
    'ensemble_signal':    cmd_ensemble_signal,
    'active_universe':    cmd_active_universe,
    'sector_rotation':    cmd_sector_rotation,
    'pairs_trading':      cmd_pairs_trading,
    'macro_data':         cmd_macro_data,
    'event_signals':            cmd_event_signals,
    'stability_test':           cmd_stability_test,
    'state_transitions':        cmd_state_transitions,
    'conditional_transitions':  cmd_conditional_transitions,
    'adaptive_memory':          cmd_adaptive_memory,
    'evolving_structure':       cmd_evolving_structure,
    'market_evolution':         cmd_market_evolution,
    'macro_regime':             cmd_macro_regime,
}


def main():
    # قراءة الأمر من stdin كـ JSON
    try:
        raw   = sys.stdin.read().strip()
        req   = json.loads(raw) if raw else {}
        cmd   = req.get('command', 'full_stats')
        params = req.get('params', {})
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"JSON parse error: {e}"}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {cmd}", "available": list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
        # استبدل NaN/Inf/numpy types بـ Python types قبل الإرسال
        def nan_to_null(obj):
            # None stays None (JSON null)
            if obj is None:
                return None
            # numpy scalar types → Python native
            if hasattr(obj, 'item'):
                try: obj = obj.item()
                except: pass
            if isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
                return None
            if isinstance(obj, bool):      return obj        # bool قبل int
            if isinstance(obj, (int, float, str)): return obj
            if isinstance(obj, dict):
                return {k: nan_to_null(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [nan_to_null(i) for i in obj]
            # numpy array fallback
            try:
                return float(obj)
            except:
                return str(obj)
        print(json.dumps(nan_to_null(result), ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()[-500:]}))
        sys.exit(1)


if __name__ == '__main__':
    main()
