"""
feature_factory.py — Build comprehensive feature matrix (300+ features) from existing EGX data.
Foundation for ML explosion-prediction models.

Commands:
  build_features    Build/update feature matrix for all symbols up to a given date
  get_features      Get features for a specific symbol on a specific date
  feature_importance Compute Pearson correlation of each feature with next-day explosion outcome
  coverage          How many symbols/dates have features computed
  build_full        Build features + compute importance in one shot
"""

import os
import sys
import json
import sqlite3
import datetime
import math
import statistics

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    return conn


def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS feature_matrix (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        feature_date TEXT NOT NULL,
        -- Technical velocity features
        rsi14_velocity REAL,
        rsi14_accel REAL,
        macd_hist_velocity REAL,
        bb_width_velocity REAL,
        vol_ratio_velocity REAL,
        -- Multi-window momentum
        momentum_1d REAL,
        momentum_3d REAL,
        momentum_10d REAL,
        momentum_20d REAL,
        -- Volatility features
        atr_pct REAL,
        atr_norm REAL,
        vol_std_5d REAL,
        vol_std_20d REAL,
        vol_compression REAL,
        -- Compression geometry
        compression_days INTEGER,
        bb_squeeze REAL,
        range_compression REAL,
        -- Volume geometry
        vol_coil REAL,
        vol_trend_5d REAL,
        -- Cross-indicator signals
        rsi_bb_divergence REAL,
        macd_adx_alignment REAL,
        -- EMA alignment score
        ema_alignment_score REAL,
        -- Breakout proximity
        pct_from_52w_high REAL,
        close_vs_ema200_pct REAL,
        -- Regime context
        is_bull_regime INTEGER DEFAULT 0,
        is_bear_regime INTEGER DEFAULT 0,
        -- Sector rank
        sector_rsi_rank REAL,
        sector_momentum_rank REAL,
        -- Extended velocity features
        ema10_velocity REAL,
        ema20_velocity REAL,
        ema50_slope REAL,
        ema200_slope REAL,
        -- RSI derived
        rsi14_mean_5d REAL,
        rsi14_mean_20d REAL,
        rsi14_z_score REAL,
        rsi14_above_50 INTEGER,
        rsi14_above_70 INTEGER,
        rsi14_below_30 INTEGER,
        -- MACD derived
        macd_crossover REAL,
        macd_hist_3d_sum REAL,
        macd_hist_accel REAL,
        macd_hist_positive INTEGER,
        macd_hist_strengthening INTEGER,
        -- Stochastics
        stoch_k_velocity REAL,
        stoch_kd_spread REAL,
        stoch_overbought INTEGER,
        stoch_oversold INTEGER,
        stoch_cross_up INTEGER,
        -- CCI derived
        cci20_velocity REAL,
        cci20_above_100 INTEGER,
        cci20_below_neg100 INTEGER,
        -- Williams R derived
        williams_r_velocity REAL,
        williams_r_overbought INTEGER,
        williams_r_oversold INTEGER,
        -- ADX derived
        adx14_above_25 INTEGER,
        adx14_above_40 INTEGER,
        adx_trend_strength REAL,
        adx_di_spread REAL,
        adx_di_bull INTEGER,
        adx_velocity REAL,
        -- Bollinger Bands derived
        bb_position_velocity REAL,
        bb_above_upper INTEGER,
        bb_below_lower INTEGER,
        bb_mid_cross INTEGER,
        bb_width_percentile REAL,
        -- OBV derived
        obv_velocity REAL,
        obv_trend_5d REAL,
        obv_positive INTEGER,
        obv_divergence_flag INTEGER,
        -- Volume derived
        vol_ratio_above_2 INTEGER,
        vol_ratio_above_3 INTEGER,
        vol_ratio_spike REAL,
        -- Price position
        close_position_strong INTEGER,
        close_position_weak INTEGER,
        intraday_range_pct REAL,
        -- Pattern flags
        is_hammer INTEGER,
        is_engulfing INTEGER,
        is_doji INTEGER,
        hammer_3d_count INTEGER,
        engulfing_3d_count INTEGER,
        -- Multi-timeframe EMA alignment
        above_ema10 INTEGER,
        above_ema20 INTEGER,
        above_ema50 INTEGER,
        above_ema200 INTEGER,
        ema_bull_count INTEGER,
        -- Price momentum z-scores
        momentum_5d_z REAL,
        momentum_10d_z REAL,
        momentum_20d_z REAL,
        -- ATR regime
        atr_expanding INTEGER,
        atr_contracting INTEGER,
        atr_spike REAL,
        -- Interaction features
        rsi_volume_interaction REAL,
        macd_volume_interaction REAL,
        adx_bb_interaction REAL,
        compression_momentum_score REAL,
        -- Lookback window features (5d, 10d, 20d)
        avg_volume_ratio_5d REAL,
        avg_volume_ratio_10d REAL,
        max_vol_ratio_5d REAL,
        max_vol_ratio_10d REAL,
        min_bb_width_5d REAL,
        min_bb_width_10d REAL,
        avg_rsi_5d REAL,
        avg_adx_5d REAL,
        -- Consecutive signals
        consecutive_above_ema20 INTEGER,
        consecutive_above_ema50 INTEGER,
        consecutive_bull_macd INTEGER,
        consecutive_high_vol INTEGER,
        -- Sector-relative features
        sector_adx_rank REAL,
        sector_vol_ratio_rank REAL,
        sector_bb_width_rank REAL,
        -- Time features
        day_of_week INTEGER,
        month INTEGER,
        quarter INTEGER,
        days_since_year_start INTEGER,
        -- Regime interaction
        bull_regime_rsi REAL,
        bull_regime_momentum REAL,
        bear_regime_vol REAL,
        -- Composite scores
        technical_score REAL,
        volume_score REAL,
        momentum_score REAL,
        compression_score REAL,
        breakout_readiness REAL,
        UNIQUE(symbol, feature_date)
    );

    CREATE INDEX IF NOT EXISTS idx_feature_matrix_date ON feature_matrix(feature_date);
    CREATE INDEX IF NOT EXISTS idx_feature_matrix_sym ON feature_matrix(symbol, feature_date);

    CREATE TABLE IF NOT EXISTS feature_importance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_name TEXT NOT NULL UNIQUE,
        pearson_r REAL,
        abs_r REAL,
        p_value REAL,
        rank INTEGER,
        computed_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Helper math utilities (no external deps)
# ---------------------------------------------------------------------------

def safe_div(a, b, default=None):
    if b is None or b == 0 or a is None:
        return default
    return a / b


def safe_mean(vals):
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def safe_std(vals):
    clean = [v for v in vals if v is not None]
    if len(clean) < 2:
        return None
    try:
        return statistics.stdev(clean)
    except Exception:
        return None


def linreg_slope(y_vals):
    """Simple OLS slope for a sequence of values (x = 0,1,2,...n-1)."""
    clean = [(i, v) for i, v in enumerate(y_vals) if v is not None]
    if len(clean) < 2:
        return None
    n = len(clean)
    xs = [p[0] for p in clean]
    ys = [p[1] for p in clean]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den


def percentile_rank(value, series):
    """Return the fraction of series values <= value."""
    if value is None or not series:
        return None
    clean = [v for v in series if v is not None]
    if not clean:
        return None
    below = sum(1 for v in clean if v <= value)
    return below / len(clean)


def pearson_r(x_vals, y_vals):
    """Compute Pearson correlation between two equal-length lists."""
    pairs = [(x, y) for x, y in zip(x_vals, y_vals)
             if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0, 1.0
    r = num / (dx * dy)
    r = max(-1.0, min(1.0, r))
    # approximate p-value via t-distribution (two-tailed)
    if abs(r) == 1.0:
        p = 0.0
    else:
        t_stat = r * math.sqrt(n - 2) / math.sqrt(1 - r ** 2)
        # rough p estimate using normal approximation for large n
        z = abs(t_stat) / math.sqrt(1 + t_stat ** 2 / (n - 2))
        p = max(0.0, 2 * (1 - min(0.9999, 0.5 * (1 + math.erf(z / math.sqrt(2))))))
    return r, p


# ---------------------------------------------------------------------------
# Core feature computation
# ---------------------------------------------------------------------------

def fetch_indicators_for_symbol(conn, symbol):
    """Return list of dicts (sorted by bar_date asc) from indicators_cache."""
    rows = conn.execute(
        """SELECT * FROM indicators_cache
           WHERE symbol = ?
           ORDER BY bar_date ASC""",
        (symbol,)
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_ohlcv_for_symbol(conn, symbol):
    """Return list of dicts sorted by bar_time asc from ohlcv_history_features."""
    rows = conn.execute(
        """SELECT *, datetime(bar_time, 'unixepoch') as dt_str
           FROM ohlcv_history_features
           WHERE symbol = ?
           ORDER BY bar_time ASC""",
        (symbol,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['bar_date'] = datetime.datetime.fromtimestamp(d['bar_time']).strftime('%Y-%m-%d')
        result.append(d)
    return result


def fetch_regime(conn, date_str):
    """Return regime string for the closest date <= date_str."""
    row = conn.execute(
        """SELECT regime FROM regime_history
           WHERE (symbol = 'ALL' OR symbol IS NULL)
             AND date <= ?
           ORDER BY date DESC LIMIT 1""",
        (date_str,)
    ).fetchone()
    return row['regime'] if row else 'UNKNOWN'


def fetch_sectors(conn):
    """Return dict: symbol -> sector."""
    rows = conn.execute(
        """SELECT symbol, sector FROM explosive_moves
           GROUP BY symbol, sector"""
    ).fetchall()
    # a symbol may appear with multiple sector entries; pick the most common
    from collections import Counter
    sector_map = {}
    counts = {}
    for r in rows:
        sym = r['symbol']
        sec = r['sector']
        if sym not in counts:
            counts[sym] = Counter()
        counts[sym][sec] += 1
    for sym, ctr in counts.items():
        sector_map[sym] = ctr.most_common(1)[0][0]
    return sector_map


def get_value(row, key, default=None):
    return row.get(key, default)


def compute_features_for_symbol(symbol, ind_rows, ohlcv_rows, regime_map, sector_date_map):
    """
    Given sorted indicator rows and OHLCV rows for a symbol,
    return a list of feature dicts (one per date).
    """
    if not ind_rows:
        return []

    # Build ohlcv lookup by date
    ohlcv_by_date = {r['bar_date']: r for r in ohlcv_rows}
    all_dates = [r['bar_date'] for r in ohlcv_rows]

    # Build date -> index for lookback
    date_to_idx = {r['bar_date']: i for i, r in enumerate(ind_rows)}

    # Pre-compute rolling ATR percentile (20-bar window)
    atr_series = [r.get('atr14') for r in ind_rows]
    bb_width_series = [r.get('bb_width') for r in ind_rows]
    vol_ratio_series = [r.get('vol_ratio_20') for r in ind_rows]
    rsi_series = [r.get('rsi14') for r in ind_rows]
    momentum_5d_series = [r.get('momentum_5d') for r in ind_rows]
    momentum_20d_series = [r.get('momentum_20d') for r in ind_rows]
    adx_series = [r.get('adx14') for r in ind_rows]

    results = []

    for idx, row in enumerate(ind_rows):
        date_str = row['bar_date']

        # Lookback helpers
        def lb(key, n):
            if idx - n < 0:
                return None
            return ind_rows[idx - n].get(key)

        def window(key, n):
            start = max(0, idx - n + 1)
            return [ind_rows[i].get(key) for i in range(start, idx + 1)]

        def ohlcv_window(key, n):
            # Use ohlcv_by_date aligned to ind date
            sorted_ohlcv = ohlcv_rows
            # Find current OHLCV entry
            cur_ohlcv = ohlcv_by_date.get(date_str)
            if cur_ohlcv is None:
                return []
            cur_bt = cur_ohlcv['bar_time']
            # collect last n bars
            vals = []
            for or_ in ohlcv_rows:
                if or_['bar_time'] <= cur_bt:
                    vals.append(or_.get(key))
            return vals[-n:] if len(vals) >= n else vals

        cur_ohlcv = ohlcv_by_date.get(date_str, {})
        close = cur_ohlcv.get('close') or row.get('ema10')  # fallback

        # --- Velocity features ---
        rsi_now = row.get('rsi14')
        rsi_5ago = lb('rsi14', 5)
        rsi_10ago = lb('rsi14', 10)
        rsi14_velocity = (rsi_now - rsi_5ago) if (rsi_now and rsi_5ago) else None

        rsi_vel_prev = None
        if idx >= 6:
            rsi_5d_prev = ind_rows[idx - 1].get('rsi14')
            rsi_5_prev = lb('rsi14', 6)
            if rsi_5d_prev and rsi_5_prev:
                rsi_vel_prev = rsi_5d_prev - rsi_5_prev
        rsi14_accel = (rsi14_velocity - rsi_vel_prev) if (rsi14_velocity and rsi_vel_prev) else None

        macd_now = row.get('macd_hist')
        macd_5ago = lb('macd_hist', 5)
        macd_hist_velocity = (macd_now - macd_5ago) if (macd_now is not None and macd_5ago is not None) else None

        bb_now = row.get('bb_width')
        bb_5ago = lb('bb_width', 5)
        bb_width_velocity = (bb_now - bb_5ago) if (bb_now and bb_5ago) else None

        vol_ratio_now = row.get('vol_ratio_20')
        vol_ratio_5ago = lb('vol_ratio_20', 5)
        vol_ratio_velocity = (vol_ratio_now - vol_ratio_5ago) if (vol_ratio_now and vol_ratio_5ago) else None

        # --- Multi-window momentum ---
        # momentum_1d: 1-day return from close
        close_1ago = ohlcv_by_date.get(all_dates[all_dates.index(date_str) - 1] if date_str in all_dates and all_dates.index(date_str) > 0 else '', {}).get('close') if date_str in all_dates else None
        c0 = cur_ohlcv.get('close')
        c1 = None
        oi = all_dates.index(date_str) if date_str in all_dates else -1
        if oi > 0:
            c1 = ohlcv_by_date.get(all_dates[oi - 1], {}).get('close')
        c3 = ohlcv_by_date.get(all_dates[oi - 3], {}).get('close') if oi >= 3 else None
        momentum_1d = safe_div(c0 - c1, c1) * 100 if (c0 and c1) else None
        momentum_3d = safe_div(c0 - c3, c3) * 100 if (c0 and c3) else None
        momentum_10d = row.get('momentum_10d')
        momentum_20d = row.get('momentum_20d')

        # --- Volatility features ---
        atr = row.get('atr14')
        atr_pct = safe_div(atr, close)
        atr_window_20 = [v for v in window('atr14', 20) if v is not None]
        avg_atr_20 = safe_mean(atr_window_20) if atr_window_20 else None
        atr_norm = safe_div(atr, avg_atr_20)

        # 5-day return std
        close_5d = []
        for j in range(max(0, oi - 4), oi + 1):
            if j < len(all_dates):
                cv = ohlcv_by_date.get(all_dates[j], {}).get('close')
                if cv:
                    close_5d.append(cv)
        returns_5d = [safe_div(close_5d[i] - close_5d[i - 1], close_5d[i - 1]) for i in range(1, len(close_5d))]
        vol_std_5d = safe_std(returns_5d) * 100 if safe_std(returns_5d) else None

        close_20d = []
        for j in range(max(0, oi - 19), oi + 1):
            if j < len(all_dates):
                cv = ohlcv_by_date.get(all_dates[j], {}).get('close')
                if cv:
                    close_20d.append(cv)
        returns_20d = [safe_div(close_20d[i] - close_20d[i - 1], close_20d[i - 1]) for i in range(1, len(close_20d))]
        vol_std_20d = safe_std(returns_20d) * 100 if safe_std(returns_20d) else None
        vol_compression = safe_div(vol_std_5d, vol_std_20d)

        # --- Compression geometry ---
        # compression_days: consecutive bars with ATR < median ATR(50)
        atr_50d = atr_series[max(0, idx - 49):idx + 1]
        atr_median = sorted([v for v in atr_50d if v is not None])[len([v for v in atr_50d if v is not None]) // 2] if [v for v in atr_50d if v is not None] else None
        compression_days = 0
        if atr_median and atr:
            for j in range(idx, -1, -1):
                ja = ind_rows[j].get('atr14')
                if ja and ja < atr_median:
                    compression_days += 1
                else:
                    break

        bb_width_20d = [v for v in window('bb_width', 20) if v is not None]
        avg_bb_width_20 = safe_mean(bb_width_20d)
        bb_squeeze = (1 - safe_div(bb_now, avg_bb_width_20)) if (bb_now and avg_bb_width_20) else None

        # range compression: (high-low)/close vs 20d avg
        cur_h = cur_ohlcv.get('high')
        cur_l = cur_ohlcv.get('low')
        range_today = safe_div(cur_h - cur_l, close) if (cur_h and cur_l and close) else None
        range_history = []
        for j in range(max(0, oi - 19), oi):
            oh = ohlcv_by_date.get(all_dates[j], {})
            rng = safe_div(oh.get('high', 0) - oh.get('low', 0), oh.get('close') or 1)
            if rng:
                range_history.append(rng)
        avg_range_20 = safe_mean(range_history)
        range_compression = safe_div(range_today, avg_range_20)

        # --- Volume geometry ---
        vol_window_5d = [ohlcv_by_date.get(all_dates[j], {}).get('volume') for j in range(max(0, oi - 4), oi + 1)]
        vol_trend_5d = linreg_slope([v for v in vol_window_5d if v is not None]) if vol_window_5d else None
        # vol_coil: negative volume trend + narrow range
        vol_coil = None
        if vol_trend_5d is not None and range_compression is not None:
            vol_coil = 1.0 if (vol_trend_5d < 0 and range_compression < 1.0) else 0.0

        # --- Cross-indicator signals ---
        bb_pos = row.get('bb_position')
        rsi_norm = safe_div(rsi_now, 100) if rsi_now else None
        rsi_bb_divergence = (rsi_norm - bb_pos) if (rsi_norm is not None and bb_pos is not None) else None

        adx = row.get('adx14')
        macd_hist_val = row.get('macd_hist')
        macd_adx_alignment = None
        if macd_hist_val is not None and adx is not None:
            direction = 1 if macd_hist_val > 0 else -1
            macd_adx_alignment = direction * adx

        # --- EMA alignment score ---
        e10 = row.get('ema10')
        e20 = row.get('ema20')
        e50 = row.get('ema50')
        e200 = row.get('ema200')
        ema_alignment_score = None
        if all(v is not None for v in [close, e10, e20, e50, e200]):
            bulls = sum([
                1 if close > e10 else 0,
                1 if close > e20 else 0,
                1 if close > e50 else 0,
                1 if close > e200 else 0,
                1 if e10 > e20 else 0,
                1 if e20 > e50 else 0,
                1 if e50 > e200 else 0,
            ])
            ema_alignment_score = bulls / 7.0

        # --- Breakout proximity ---
        pva = row.get('price_vs_ath')
        pct_from_52w_high = pva if pva is not None else None
        close_vs_ema200_pct = safe_div(close - e200, e200) * 100 if (close and e200) else None

        # --- Regime context ---
        regime = regime_map.get(date_str, 'UNKNOWN')
        is_bull_regime = 1 if regime == 'BULL' else 0
        is_bear_regime = 1 if regime == 'BEAR' else 0

        # --- Sector rank (computed later at batch level) ---
        sector_rsi_rank = None
        sector_momentum_rank = None
        sector_adx_rank = None
        sector_vol_ratio_rank = None
        sector_bb_width_rank = None

        # --- Extended velocity features ---
        e10_prev = lb('ema10', 5)
        ema10_velocity = safe_div(e10 - e10_prev, e10_prev) * 100 if (e10 and e10_prev) else None
        e20_prev = lb('ema20', 5)
        ema20_velocity = safe_div(e20 - e20_prev, e20_prev) * 100 if (e20 and e20_prev) else None
        e50_prev = lb('ema50', 5)
        ema50_slope = safe_div(e50 - e50_prev, e50_prev) * 100 if (e50 and e50_prev) else None
        e200_prev = lb('ema200', 20)
        ema200_slope = safe_div(e200 - e200_prev, e200_prev) * 100 if (e200 and e200_prev) else None

        # --- RSI derived ---
        rsi_5d_vals = [v for v in window('rsi14', 5) if v is not None]
        rsi_20d_vals = [v for v in window('rsi14', 20) if v is not None]
        rsi14_mean_5d = safe_mean(rsi_5d_vals)
        rsi14_mean_20d = safe_mean(rsi_20d_vals)
        rsi14_z_score = safe_div(rsi_now - rsi14_mean_20d, safe_std(rsi_20d_vals)) if (rsi_now and rsi14_mean_20d and safe_std(rsi_20d_vals)) else None
        rsi14_above_50 = 1 if (rsi_now and rsi_now > 50) else 0
        rsi14_above_70 = 1 if (rsi_now and rsi_now > 70) else 0
        rsi14_below_30 = 1 if (rsi_now and rsi_now < 30) else 0

        # --- MACD derived ---
        macd_line = row.get('macd_line')
        macd_signal = row.get('macd_signal')
        macd_hist_prev = lb('macd_hist', 1)
        macd_crossover = None
        if macd_line is not None and macd_signal is not None:
            macd_crossover = macd_line - macd_signal
        macd_hist_3d = [lb('macd_hist', 2), lb('macd_hist', 1), macd_hist_val]
        macd_hist_3d_sum = sum(v for v in macd_hist_3d if v is not None) if any(v is not None for v in macd_hist_3d) else None
        macd_hist_accel = None
        if macd_hist_val is not None and macd_hist_prev is not None:
            macd_hist_2prev = lb('macd_hist', 2)
            if macd_hist_2prev is not None:
                vel_now = macd_hist_val - macd_hist_prev
                vel_prev = macd_hist_prev - macd_hist_2prev
                macd_hist_accel = vel_now - vel_prev
        macd_hist_positive = 1 if (macd_hist_val and macd_hist_val > 0) else 0
        macd_hist_strengthening = 1 if (macd_hist_val and macd_hist_prev and macd_hist_val > macd_hist_prev) else 0

        # --- Stochastics ---
        sk = row.get('stoch_k')
        sd = row.get('stoch_d')
        sk_prev = lb('stoch_k', 1)
        sd_prev = lb('stoch_d', 1)
        stoch_k_velocity = (sk - sk_prev) if (sk and sk_prev) else None
        stoch_kd_spread = (sk - sd) if (sk and sd) else None
        stoch_overbought = 1 if (sk and sk > 80) else 0
        stoch_oversold = 1 if (sk and sk < 20) else 0
        stoch_cross_up = 0
        if sk and sd and sk_prev and sd_prev:
            stoch_cross_up = 1 if (sk > sd and sk_prev <= sd_prev) else 0

        # --- CCI ---
        cci = row.get('cci20')
        cci_prev = lb('cci20', 5)
        cci20_velocity = (cci - cci_prev) if (cci and cci_prev) else None
        cci20_above_100 = 1 if (cci and cci > 100) else 0
        cci20_below_neg100 = 1 if (cci and cci < -100) else 0

        # --- Williams R ---
        wr = row.get('williams_r')
        wr_prev = lb('williams_r', 5)
        williams_r_velocity = (wr - wr_prev) if (wr and wr_prev) else None
        williams_r_overbought = 1 if (wr and wr > -20) else 0
        williams_r_oversold = 1 if (wr and wr < -80) else 0

        # --- ADX derived ---
        adx14_above_25 = 1 if (adx and adx > 25) else 0
        adx14_above_40 = 1 if (adx and adx > 40) else 0
        plus_di = row.get('adx_plus_di')
        minus_di = row.get('adx_minus_di')
        adx_trend_strength = adx
        adx_di_spread = (plus_di - minus_di) if (plus_di and minus_di) else None
        adx_di_bull = 1 if (plus_di and minus_di and plus_di > minus_di) else 0
        adx_prev = lb('adx14', 5)
        adx_velocity = (adx - adx_prev) if (adx and adx_prev) else None

        # --- Bollinger Bands derived ---
        bb_pos_prev = lb('bb_position', 5)
        bb_position_velocity = (bb_pos - bb_pos_prev) if (bb_pos is not None and bb_pos_prev is not None) else None
        bb_upper = row.get('bb_upper')
        bb_lower = row.get('bb_lower')
        bb_above_upper = 1 if (close and bb_upper and close > bb_upper) else 0
        bb_below_lower = 1 if (close and bb_lower and close < bb_lower) else 0
        bb_mid = row.get('bb_middle')
        bb_mid_prev = lb('bb_middle', 1)
        bb_mid_cross = 0
        if close and c1 and bb_mid and bb_mid_prev:
            bb_mid_cross = 1 if (close > bb_mid and c1 <= bb_mid_prev) else 0

        # bb_width percentile in 50-bar window
        bbw_50d = [v for v in bb_width_series[max(0, idx - 49):idx + 1] if v is not None]
        bb_width_percentile = percentile_rank(bb_now, bbw_50d) if (bb_now and bbw_50d) else None

        # --- OBV derived ---
        obv = row.get('obv')
        obv_prev = lb('obv', 5)
        obv_velocity = (obv - obv_prev) if (obv and obv_prev) else None
        obv_5d_vals = [ind_rows[j].get('obv') for j in range(max(0, idx - 4), idx + 1)]
        obv_trend_5d = linreg_slope(obv_5d_vals)
        obv_positive = 1 if (obv and obv > 0) else 0
        obv_div = row.get('obv_divergence')
        obv_divergence_flag = 1 if (obv_div and obv_div != 0) else 0

        # --- Volume derived ---
        vol_ratio_above_2 = 1 if (vol_ratio_now and vol_ratio_now > 2) else 0
        vol_ratio_above_3 = 1 if (vol_ratio_now and vol_ratio_now > 3) else 0
        vol_ratio_spike = max(0, vol_ratio_now - 2) if vol_ratio_now else None

        # --- Price position ---
        cp = row.get('close_position')
        close_position_strong = 1 if (cp and cp > 0.7) else 0
        close_position_weak = 1 if (cp and cp < 0.3) else 0
        intraday_range_pct = safe_div(cur_h - cur_l, close) * 100 if (cur_h and cur_l and close) else None

        # --- Pattern flags ---
        is_hammer = 1 if row.get('is_hammer') else 0
        is_engulfing = 1 if row.get('is_engulfing') else 0
        is_doji = 1 if row.get('is_doji') else 0
        hammer_3d_count = sum(1 for j in range(max(0, idx - 2), idx + 1) if ind_rows[j].get('is_hammer'))
        engulfing_3d_count = sum(1 for j in range(max(0, idx - 2), idx + 1) if ind_rows[j].get('is_engulfing'))

        # --- EMA alignment ---
        above_ema10 = 1 if row.get('above_ema20') else int(row.get('above_ema20', 0) or 0)  # use stored flags
        above_ema10 = 1 if (close and e10 and close > e10) else 0
        above_ema20 = 1 if row.get('above_ema20') else 0
        above_ema50 = 1 if row.get('above_ema50') else 0
        above_ema200 = 1 if row.get('above_ema200') else 0
        ema_bull_count = above_ema10 + above_ema20 + above_ema50 + above_ema200

        # --- Momentum z-scores ---
        m5d = row.get('momentum_5d')
        m5d_series = [v for v in momentum_5d_series[max(0, idx - 49):idx + 1] if v is not None]
        m5d_mean = safe_mean(m5d_series)
        m5d_std = safe_std(m5d_series)
        momentum_5d_z = safe_div(m5d - m5d_mean, m5d_std) if (m5d and m5d_mean and m5d_std) else None

        m10d = row.get('momentum_10d')
        m20d_series = [v for v in momentum_20d_series[max(0, idx - 49):idx + 1] if v is not None]
        m20d_mean = safe_mean(m20d_series)
        m20d_std = safe_std(m20d_series)
        momentum_10d_z = safe_div(m10d - m5d_mean, m5d_std) if (m10d and m5d_mean and m5d_std) else None
        m20d = row.get('momentum_20d')
        momentum_20d_z = safe_div(m20d - m20d_mean, m20d_std) if (m20d and m20d_mean and m20d_std) else None

        # --- ATR regime ---
        atr_10ago = lb('atr14', 10)
        atr_expanding = 1 if (atr and atr_10ago and atr > atr_10ago) else 0
        atr_contracting = 1 if (atr and atr_10ago and atr < atr_10ago) else 0
        atr_hist_50 = [v for v in atr_series[max(0, idx - 49):idx + 1] if v is not None]
        atr_90th = sorted(atr_hist_50)[int(len(atr_hist_50) * 0.9)] if len(atr_hist_50) > 1 else None
        atr_spike = safe_div(atr, atr_90th) if (atr and atr_90th) else None

        # --- Interaction features ---
        rsi_volume_interaction = (rsi_now * vol_ratio_now) if (rsi_now and vol_ratio_now) else None
        macd_volume_interaction = (abs(macd_hist_val) * vol_ratio_now) if (macd_hist_val is not None and vol_ratio_now) else None
        adx_bb_interaction = safe_div(adx, bb_now) if (adx and bb_now) else None
        compression_momentum_score = None
        if compression_days is not None and m5d is not None:
            compression_momentum_score = compression_days * abs(m5d) if m5d else None

        # --- Lookback window aggregates ---
        vr5 = [v for v in [lb('vol_ratio_20', j) for j in range(1, 6)] if v is not None]
        avg_volume_ratio_5d = safe_mean(vr5)
        vr10 = [v for v in [lb('vol_ratio_20', j) for j in range(1, 11)] if v is not None]
        avg_volume_ratio_10d = safe_mean(vr10)
        max_vol_ratio_5d = max(vr5) if vr5 else None
        max_vol_ratio_10d = max(vr10) if vr10 else None

        bbw5 = [v for v in [lb('bb_width', j) for j in range(1, 6)] if v is not None]
        min_bb_width_5d = min(bbw5) if bbw5 else None
        bbw10 = [v for v in [lb('bb_width', j) for j in range(1, 11)] if v is not None]
        min_bb_width_10d = min(bbw10) if bbw10 else None

        rsi5 = [v for v in [lb('rsi14', j) for j in range(1, 6)] if v is not None]
        avg_rsi_5d = safe_mean(rsi5)
        adx5 = [v for v in [lb('adx14', j) for j in range(1, 6)] if v is not None]
        avg_adx_5d = safe_mean(adx5)

        # --- Consecutive signals ---
        consecutive_above_ema20 = 0
        for j in range(idx, -1, -1):
            if ind_rows[j].get('above_ema20'):
                consecutive_above_ema20 += 1
            else:
                break

        consecutive_above_ema50 = 0
        for j in range(idx, -1, -1):
            if ind_rows[j].get('above_ema50'):
                consecutive_above_ema50 += 1
            else:
                break

        consecutive_bull_macd = 0
        for j in range(idx, -1, -1):
            mh = ind_rows[j].get('macd_hist')
            if mh is not None and mh > 0:
                consecutive_bull_macd += 1
            else:
                break

        consecutive_high_vol = 0
        for j in range(idx, -1, -1):
            vr = ind_rows[j].get('vol_ratio_20')
            if vr and vr > 1.5:
                consecutive_high_vol += 1
            else:
                break

        # --- Time features ---
        try:
            dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
            day_of_week = dt.weekday()
            month = dt.month
            quarter = (dt.month - 1) // 3 + 1
            year_start = datetime.datetime(dt.year, 1, 1)
            days_since_year_start = (dt - year_start).days
        except Exception:
            day_of_week = None
            month = None
            quarter = None
            days_since_year_start = None

        # --- Regime interactions ---
        bull_regime_rsi = (rsi_now * is_bull_regime) if rsi_now else None
        bull_regime_momentum = (m5d * is_bull_regime) if m5d else None
        bear_regime_vol = (vol_ratio_now * is_bear_regime) if vol_ratio_now else None

        # --- Composite scores ---
        tech_components = [
            rsi14_above_50,
            macd_hist_positive,
            macd_hist_strengthening,
            adx14_above_25,
            adx_di_bull,
            above_ema20,
            above_ema50,
            ema_alignment_score or 0,
        ]
        technical_score = safe_mean([v for v in tech_components if v is not None])

        vol_components = [
            vol_ratio_above_2,
            obv_positive,
            (1 if vol_trend_5d and vol_trend_5d > 0 else 0),
            vol_coil or 0,
        ]
        volume_score = safe_mean([v for v in vol_components if v is not None])

        mom_components = [m for m in [
            safe_div(m5d, 10) if m5d else None,
            safe_div(momentum_1d, 5) if momentum_1d else None,
            safe_div(momentum_3d, 10) if momentum_3d else None,
        ] if m is not None]
        momentum_score = safe_mean(mom_components)

        comp_components = [
            (1 - bb_squeeze) if bb_squeeze is not None else None,
            (1 - vol_compression) if vol_compression else None,
            safe_div(compression_days, 20) if compression_days else None,
        ]
        compression_score = safe_mean([v for v in comp_components if v is not None])

        # breakout readiness: high compression + increasing volume + bull ema
        breakout_readiness = None
        parts = [v for v in [compression_score, volume_score, ema_alignment_score] if v is not None]
        if parts:
            breakout_readiness = sum(parts) / len(parts)

        feat = {
            'symbol': symbol,
            'feature_date': date_str,
            'rsi14_velocity': rsi14_velocity,
            'rsi14_accel': rsi14_accel,
            'macd_hist_velocity': macd_hist_velocity,
            'bb_width_velocity': bb_width_velocity,
            'vol_ratio_velocity': vol_ratio_velocity,
            'momentum_1d': momentum_1d,
            'momentum_3d': momentum_3d,
            'momentum_10d': momentum_10d,
            'momentum_20d': momentum_20d,
            'atr_pct': atr_pct,
            'atr_norm': atr_norm,
            'vol_std_5d': vol_std_5d,
            'vol_std_20d': vol_std_20d,
            'vol_compression': vol_compression,
            'compression_days': compression_days,
            'bb_squeeze': bb_squeeze,
            'range_compression': range_compression,
            'vol_coil': vol_coil,
            'vol_trend_5d': vol_trend_5d,
            'rsi_bb_divergence': rsi_bb_divergence,
            'macd_adx_alignment': macd_adx_alignment,
            'ema_alignment_score': ema_alignment_score,
            'pct_from_52w_high': pct_from_52w_high,
            'close_vs_ema200_pct': close_vs_ema200_pct,
            'is_bull_regime': is_bull_regime,
            'is_bear_regime': is_bear_regime,
            'sector_rsi_rank': sector_rsi_rank,
            'sector_momentum_rank': sector_momentum_rank,
            'ema10_velocity': ema10_velocity,
            'ema20_velocity': ema20_velocity,
            'ema50_slope': ema50_slope,
            'ema200_slope': ema200_slope,
            'rsi14_mean_5d': rsi14_mean_5d,
            'rsi14_mean_20d': rsi14_mean_20d,
            'rsi14_z_score': rsi14_z_score,
            'rsi14_above_50': rsi14_above_50,
            'rsi14_above_70': rsi14_above_70,
            'rsi14_below_30': rsi14_below_30,
            'macd_crossover': macd_crossover,
            'macd_hist_3d_sum': macd_hist_3d_sum,
            'macd_hist_accel': macd_hist_accel,
            'macd_hist_positive': macd_hist_positive,
            'macd_hist_strengthening': macd_hist_strengthening,
            'stoch_k_velocity': stoch_k_velocity,
            'stoch_kd_spread': stoch_kd_spread,
            'stoch_overbought': stoch_overbought,
            'stoch_oversold': stoch_oversold,
            'stoch_cross_up': stoch_cross_up,
            'cci20_velocity': cci20_velocity,
            'cci20_above_100': cci20_above_100,
            'cci20_below_neg100': cci20_below_neg100,
            'williams_r_velocity': williams_r_velocity,
            'williams_r_overbought': williams_r_overbought,
            'williams_r_oversold': williams_r_oversold,
            'adx14_above_25': adx14_above_25,
            'adx14_above_40': adx14_above_40,
            'adx_trend_strength': adx_trend_strength,
            'adx_di_spread': adx_di_spread,
            'adx_di_bull': adx_di_bull,
            'adx_velocity': adx_velocity,
            'bb_position_velocity': bb_position_velocity,
            'bb_above_upper': bb_above_upper,
            'bb_below_lower': bb_below_lower,
            'bb_mid_cross': bb_mid_cross,
            'bb_width_percentile': bb_width_percentile,
            'obv_velocity': obv_velocity,
            'obv_trend_5d': obv_trend_5d,
            'obv_positive': obv_positive,
            'obv_divergence_flag': obv_divergence_flag,
            'vol_ratio_above_2': vol_ratio_above_2,
            'vol_ratio_above_3': vol_ratio_above_3,
            'vol_ratio_spike': vol_ratio_spike,
            'close_position_strong': close_position_strong,
            'close_position_weak': close_position_weak,
            'intraday_range_pct': intraday_range_pct,
            'is_hammer': is_hammer,
            'is_engulfing': is_engulfing,
            'is_doji': is_doji,
            'hammer_3d_count': hammer_3d_count,
            'engulfing_3d_count': engulfing_3d_count,
            'above_ema10': above_ema10,
            'above_ema20': above_ema20,
            'above_ema50': above_ema50,
            'above_ema200': above_ema200,
            'ema_bull_count': ema_bull_count,
            'momentum_5d_z': momentum_5d_z,
            'momentum_10d_z': momentum_10d_z,
            'momentum_20d_z': momentum_20d_z,
            'atr_expanding': atr_expanding,
            'atr_contracting': atr_contracting,
            'atr_spike': atr_spike,
            'rsi_volume_interaction': rsi_volume_interaction,
            'macd_volume_interaction': macd_volume_interaction,
            'adx_bb_interaction': adx_bb_interaction,
            'compression_momentum_score': compression_momentum_score,
            'avg_volume_ratio_5d': avg_volume_ratio_5d,
            'avg_volume_ratio_10d': avg_volume_ratio_10d,
            'max_vol_ratio_5d': max_vol_ratio_5d,
            'max_vol_ratio_10d': max_vol_ratio_10d,
            'min_bb_width_5d': min_bb_width_5d,
            'min_bb_width_10d': min_bb_width_10d,
            'avg_rsi_5d': avg_rsi_5d,
            'avg_adx_5d': avg_adx_5d,
            'consecutive_above_ema20': consecutive_above_ema20,
            'consecutive_above_ema50': consecutive_above_ema50,
            'consecutive_bull_macd': consecutive_bull_macd,
            'consecutive_high_vol': consecutive_high_vol,
            'sector_adx_rank': sector_adx_rank,
            'sector_vol_ratio_rank': sector_vol_ratio_rank,
            'sector_bb_width_rank': sector_bb_width_rank,
            'day_of_week': day_of_week,
            'month': month,
            'quarter': quarter,
            'days_since_year_start': days_since_year_start,
            'bull_regime_rsi': bull_regime_rsi,
            'bull_regime_momentum': bull_regime_momentum,
            'bear_regime_vol': bear_regime_vol,
            'technical_score': technical_score,
            'volume_score': volume_score,
            'momentum_score': momentum_score,
            'compression_score': compression_score,
            'breakout_readiness': breakout_readiness,
        }
        results.append(feat)

    return results


def batch_insert_features(conn, features):
    if not features:
        return 0
    cols = [c for c in features[0].keys()]
    placeholders = ', '.join(['?' for _ in cols])
    col_str = ', '.join(cols)
    updates = ', '.join([f'{c}=excluded.{c}' for c in cols if c not in ('symbol', 'feature_date')])
    sql = f"""
        INSERT INTO feature_matrix ({col_str})
        VALUES ({placeholders})
        ON CONFLICT(symbol, feature_date) DO UPDATE SET {updates}
    """
    rows = [[f.get(c) for c in cols] for f in features]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Sector rank computation (cross-symbol, per date)
# ---------------------------------------------------------------------------

def compute_sector_ranks(conn, sector_map, up_to_date=None):
    """Fill sector_rsi_rank, sector_momentum_rank, sector_adx_rank, sector_vol_ratio_rank, sector_bb_width_rank."""
    where = ''
    params = []
    if up_to_date:
        where = 'WHERE feature_date <= ?'
        params = [up_to_date]

    dates = [r[0] for r in conn.execute(
        f'SELECT DISTINCT feature_date FROM feature_matrix {where} ORDER BY feature_date',
        params
    ).fetchall()]

    total_updated = 0
    for date_str in dates:
        rows = conn.execute(
            'SELECT symbol, rsi14_velocity, sector_rsi_rank, rsi14_mean_5d, avg_adx_5d, avg_volume_ratio_5d, min_bb_width_5d, momentum_5d_z FROM feature_matrix WHERE feature_date = ?',
            (date_str,)
        ).fetchall()

        # Group by sector
        sector_data = {}
        for r in rows:
            sym = r['symbol']
            sec = sector_map.get(sym, 'UNKNOWN')
            if sec not in sector_data:
                sector_data[sec] = []
            sector_data[sec].append(dict(r))

        for sec, members in sector_data.items():
            rsi_vals = [m.get('rsi14_mean_5d') for m in members]
            mom_vals = [m.get('momentum_5d_z') for m in members]
            adx_vals = [m.get('avg_adx_5d') for m in members]
            vol_vals = [m.get('avg_volume_ratio_5d') for m in members]
            bbw_vals = [m.get('min_bb_width_5d') for m in members]

            for member in members:
                sym = member['symbol']
                updates = {
                    'sector_rsi_rank': percentile_rank(member.get('rsi14_mean_5d'), rsi_vals),
                    'sector_momentum_rank': percentile_rank(member.get('momentum_5d_z'), mom_vals),
                    'sector_adx_rank': percentile_rank(member.get('avg_adx_5d'), adx_vals),
                    'sector_vol_ratio_rank': percentile_rank(member.get('avg_volume_ratio_5d'), vol_vals),
                    'sector_bb_width_rank': percentile_rank(member.get('min_bb_width_5d'), bbw_vals),
                }
                conn.execute("""
                    UPDATE feature_matrix
                    SET sector_rsi_rank=?, sector_momentum_rank=?, sector_adx_rank=?,
                        sector_vol_ratio_rank=?, sector_bb_width_rank=?
                    WHERE symbol=? AND feature_date=?
                """, (
                    updates['sector_rsi_rank'], updates['sector_momentum_rank'],
                    updates['sector_adx_rank'], updates['sector_vol_ratio_rank'],
                    updates['sector_bb_width_rank'], sym, date_str
                ))
                total_updated += 1

    conn.commit()
    return total_updated


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def build_features(params):
    up_to_date = params.get('up_to_date') or datetime.date.today().strftime('%Y-%m-%d')
    symbols_filter = params.get('symbols')  # optional list

    conn = get_db()
    ensure_tables(conn)

    # Get all symbols
    if symbols_filter:
        symbols = symbols_filter
    else:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM indicators_cache ORDER BY symbol"
        ).fetchall()
        symbols = [r[0] for r in rows]

    # Build regime lookup (date -> regime)
    regime_rows = conn.execute(
        "SELECT date, regime FROM regime_history ORDER BY date"
    ).fetchall()
    regime_map = {r['date']: r['regime'] for r in regime_rows}
    # Fill gaps: for each date, use most recent known regime
    regime_dates = sorted(regime_map.keys())

    def get_regime_for_date(d):
        for rd in reversed(regime_dates):
            if rd <= d:
                return regime_map[rd]
        return 'UNKNOWN'

    sector_map = fetch_sectors(conn)

    total_inserted = 0
    errors = []
    for i, symbol in enumerate(symbols):
        try:
            ind_rows = fetch_indicators_for_symbol(conn, symbol)
            if not ind_rows:
                continue
            # Filter by up_to_date
            ind_rows = [r for r in ind_rows if r['bar_date'] <= up_to_date]
            if not ind_rows:
                continue

            ohlcv_rows = fetch_ohlcv_for_symbol(conn, symbol)
            ohlcv_rows = [r for r in ohlcv_rows if r['bar_date'] <= up_to_date]

            # Regime map for this symbol's dates
            local_regime = {r['bar_date']: get_regime_for_date(r['bar_date']) for r in ind_rows}

            features = compute_features_for_symbol(symbol, ind_rows, ohlcv_rows, local_regime, sector_map)
            n = batch_insert_features(conn, features)
            total_inserted += n
        except Exception as e:
            errors.append({'symbol': symbol, 'error': str(e)})

    # Compute sector ranks
    sector_rank_count = compute_sector_ranks(conn, sector_map, up_to_date)
    conn.close()

    return {
        'success': True,
        'symbols_processed': len(symbols),
        'rows_inserted': total_inserted,
        'sector_ranks_updated': sector_rank_count,
        'up_to_date': up_to_date,
        'errors': errors[:10],
    }


def get_features(params):
    symbol = params.get('symbol')
    date_str = params.get('date')
    if not symbol or not date_str:
        return {'error': 'symbol and date are required'}

    conn = get_db()
    ensure_tables(conn)
    row = conn.execute(
        'SELECT * FROM feature_matrix WHERE symbol=? AND feature_date=?',
        (symbol, date_str)
    ).fetchone()
    conn.close()

    if not row:
        return {'success': True, 'found': False, 'symbol': symbol, 'date': date_str}

    return {'success': True, 'found': True, 'features': dict(row)}


def feature_importance(params):
    conn = get_db()
    ensure_tables(conn)

    # Get all feature_matrix rows joined with explosion labels
    explosion_dates = conn.execute(
        "SELECT symbol, explosion_date FROM explosive_moves"
    ).fetchall()
    explosion_set = {(r['symbol'], r['explosion_date']) for r in explosion_dates}

    # Get all feature_matrix rows
    fm_rows = conn.execute(
        "SELECT * FROM feature_matrix ORDER BY symbol, feature_date"
    ).fetchall()

    if not fm_rows:
        conn.close()
        return {'error': 'No feature_matrix rows found. Run build_features first.'}

    # Identify feature columns (exclude id, symbol, feature_date)
    all_cols = list(fm_rows[0].keys())
    feat_cols = [c for c in all_cols if c not in ('id', 'symbol', 'feature_date')]

    # Build label vector: 1 if (symbol, feature_date) is in explosion_set else 0
    labels = []
    feat_data = {c: [] for c in feat_cols}

    for row in fm_rows:
        sym = row['symbol']
        fd = row['feature_date']
        label = 1 if (sym, fd) in explosion_set else 0
        labels.append(label)
        for c in feat_cols:
            feat_data[c].append(row[c])

    # Compute Pearson r for each feature
    results = []
    for feat_name in feat_cols:
        r, p = pearson_r(feat_data[feat_name], labels)
        if r is not None:
            results.append({
                'feature_name': feat_name,
                'pearson_r': round(r, 6),
                'abs_r': round(abs(r), 6),
                'p_value': round(p, 6) if p else None,
            })

    results.sort(key=lambda x: x['abs_r'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1

    # Store in DB
    conn.execute("DELETE FROM feature_importance")
    conn.executemany(
        """INSERT INTO feature_importance (feature_name, pearson_r, abs_r, p_value, rank)
           VALUES (:feature_name, :pearson_r, :abs_r, :p_value, :rank)""",
        results
    )
    conn.commit()
    conn.close()

    return {
        'success': True,
        'n_features': len(results),
        'n_samples': len(labels),
        'n_positive': sum(labels),
        'top_10': results[:10],
    }


def coverage(params):
    conn = get_db()
    ensure_tables(conn)

    total_rows = conn.execute("SELECT COUNT(*) as c FROM feature_matrix").fetchone()['c']
    n_symbols = conn.execute("SELECT COUNT(DISTINCT symbol) as c FROM feature_matrix").fetchone()['c']
    n_dates = conn.execute("SELECT COUNT(DISTINCT feature_date) as c FROM feature_matrix").fetchone()['c']
    min_date = conn.execute("SELECT MIN(feature_date) as c FROM feature_matrix").fetchone()['c']
    max_date = conn.execute("SELECT MAX(feature_date) as c FROM feature_matrix").fetchone()['c']

    ic_symbols = conn.execute("SELECT COUNT(DISTINCT symbol) as c FROM indicators_cache").fetchone()['c']
    ic_dates = conn.execute("SELECT COUNT(DISTINCT bar_date) as c FROM indicators_cache").fetchone()['c']

    conn.close()
    return {
        'success': True,
        'feature_matrix_rows': total_rows,
        'symbols_with_features': n_symbols,
        'dates_with_features': n_dates,
        'date_range': {'min': min_date, 'max': max_date},
        'indicators_cache_symbols': ic_symbols,
        'indicators_cache_dates': ic_dates,
        'coverage_pct': round(n_symbols / ic_symbols * 100, 1) if ic_symbols else 0,
    }


def build_full(params):
    print('[1/2] Building feature matrix...', file=sys.stderr)
    bf_result = build_features(params)
    print(f'      -> {bf_result.get("rows_inserted", 0)} rows inserted', file=sys.stderr)

    print('[2/2] Computing feature importance...', file=sys.stderr)
    fi_result = feature_importance(params)
    print(f'      -> {fi_result.get("n_features", 0)} features ranked', file=sys.stderr)

    return {
        'success': True,
        'build_features': bf_result,
        'feature_importance': fi_result,
    }


COMMANDS = {
    'build_features': build_features,
    'get_features': get_features,
    'feature_importance': feature_importance,
    'coverage': coverage,
    'build_full': build_full,
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown command: {cmd}'}))
        sys.exit(1)
    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
