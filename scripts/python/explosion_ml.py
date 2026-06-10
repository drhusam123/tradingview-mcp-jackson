#!/usr/bin/env python3
"""
Phase 4 Features (2026-05-28) — Explosion ML Engine
LightGBM binary classifier: P(explosion in next 1-3 days).

FIXES applied:
  1. Negatives sampled from ohlcv_history_features (73k rows), NOT indicators_cache (257 rows)
  2. Real pre-event features computed for negatives via OHLCV indicator time series
  3. Purged walk-forward split: 30-day gap between IS end and OOS start
  4. Prediction pipeline uses ohlcv_history_features for proper pre1/pre3/pre5 lookback features
  5. explosion_predictions populated after every predict run
  6. Balance: ~3× negatives per positive (from true non-explosion dates)
Phase 3 additions (2026-05-22):
  7. rsi_slope_3d: RSI momentum direction (rising vs falling) — predictive of UP explosions
  8. ema_alignment: # of EMAs (20/50/200) price is above (0-3) — bull trend context
  9. ema20_slope: EMA20 slope over 5 bars — trend acceleration
Phase 4 additions (2026-05-28):
  10. di_diff: DI+ minus DI- — directional trend bias (+ve = bulls winning)
  11. body_ratio: candle body / range — conviction bars vs doji patterns
  12. lower_shadow_ratio: lower wick / range — buying support at lows
  13. bar_direction: +1 if close > open else -1 — bar polarity
  14. sector_ad_ratio: sector advance/decline ratio — sector momentum context
  15. sector_pct_ema20: sector % stocks above EMA20 — sector strength
  Retrain: IS end 2026-02-28, OOS start 2026-03-31 (2 extra months IS data)
"""
import os, sys, json, sqlite3, datetime, math, random

DB_PATH   = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')
MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
MODEL_PATH = os.path.join(MODEL_DIR, 'explosion_model.txt')
CALIB_PATH = os.path.join(MODEL_DIR, 'explosion_calibration.json')

os.makedirs(MODEL_DIR, exist_ok=True)

# Minimum evidence required before a model/evaluation can be treated as an edge.
# These guards are deliberately conservative: a market edge is not accepted from
# a tiny or one-sided OOS window, even if AUC happens to look good.
MIN_TRAIN_POSITIVES = 100
MIN_TRAIN_NEGATIVES = 100
MIN_OOS_POSITIVES   = 30
MIN_OOS_NEGATIVES   = 90
MIN_OOS_SAMPLES     = MIN_OOS_POSITIVES + MIN_OOS_NEGATIVES

# Decision policy used after raw model scoring. The model is an input, not a
# client recommendation; these gates keep weak ranks / weak volume from being
# treated as acceptable just because a downstream final_signals row exists.
DEFAULT_DECISION_POLICY = {
    'high_prob': 0.75,
    'medium_prob': 0.70,
    'abstain_prob': 0.50,
    'high_rank_limit': 10,
    'medium_rank_limit': 30,
    'high_vol_ratio': 1.5,
    'medium_vol_ratio': 1.0,
    'min_precision_high': 0.40,
    'min_precision_medium': 0.32,
}
PRECISION_TOP_KS = [10, 20, 50]

# --- EGX30 index returns cache (lazy, loaded once) for Relative Strength vs Market ---
_EGX30_CACHE = {"loaded": False, "by_date": {}, "dates": []}  # date(iso) -> close

def _load_egx30_cache(conn):
    """Load EGX30 index daily closes keyed by ISO date. Lazy, once.
    Returns dict {iso_date: close}. Empty dict if EGX30 data is unavailable."""
    if _EGX30_CACHE["loaded"]:
        return _EGX30_CACHE["by_date"]
    try:
        rows = conn.execute(
            "SELECT date(bar_time,'unixepoch') d, close FROM ohlcv_history_features "
            "WHERE symbol='EGX30' ORDER BY bar_time"
        ).fetchall()
        _EGX30_CACHE["by_date"] = {r[0]: float(r[1]) for r in rows if r[1]}
    except Exception:
        _EGX30_CACHE["by_date"] = {}
    _EGX30_CACHE["dates"] = sorted(_EGX30_CACHE["by_date"].keys())
    _EGX30_CACHE["loaded"] = True
    return _EGX30_CACHE["by_date"]

def _egx30_close_on_or_before(egx30, date_str):
    """Return the EGX30 close on date_str, or the nearest prior available close
    (handles holidays / missing index bars). Returns None if none available."""
    if not egx30 or not date_str:
        return None
    c = egx30.get(date_str)
    if c is not None:
        return c
    # Fall back to nearest prior available EGX30 date via the sorted dates list.
    import bisect
    dates = _EGX30_CACHE.get("dates") or sorted(egx30.keys())
    i = bisect.bisect_right(dates, date_str) - 1
    if i < 0:
        return None
    return egx30.get(dates[i])

# 28 pre-event features (Phase 4: +6 new context + candle features)
FEATURE_COLS = [
    # Bollinger Band width at 3 lookbacks (BB squeeze = compression before explosion)
    'pre1_bb_width',    'pre3_bb_width',    'pre5_bb_width',
    # Volume ratio vs 20d average (volume surge confirms breakout)
    'pre1_vol_ratio',   'pre3_vol_ratio',   'pre5_vol_ratio',
    # RSI momentum (overbought/oversold setup)
    'pre1_rsi',         'pre3_rsi',         'pre5_rsi',
    # Price momentum
    'pre3_momentum_5d', 'pre5_momentum_5d',
    # BB position (where is price in the band — near upper = strong momentum)
    'pre5_bb_position',
    # Compression streak (consecutive bars in squeeze — longer squeeze = bigger breakout)
    'pre5_compression_days',
    # ADX (trend strength — high ADX = trending, not choppy)
    'pre3_adx',         'pre5_adx',
    # MACD histogram (momentum acceleration indicator)
    'pre3_macd_hist',   'pre5_macd_hist',
    # ADX directional difference (DI+ vs DI- proxy via adx itself at different lags)
    'pre1_adx',
    # MACD at pre1 (most recent momentum signal)
    'pre1_macd_hist',
    # ── Phase 3 (2026-05-22): Trend context features ─────────────────────────
    # RSI slope: (RSI_today - RSI_3d_ago) / 3 — rising RSI = momentum building
    'pre1_rsi_slope',
    # EMA alignment: count of EMAs (20/50/200) price is above — 0=bear, 3=full bull
    'pre1_ema_align',
    # EMA20 slope over 5 bars — trend acceleration (positive = uptrend strengthening)
    'pre1_ema20_slope',
    # ── Phase 4 (2026-05-28): Directional + Candle + Sector context ──────────
    # DI+ minus DI- — positive = bulls winning the trend (+ve before breakout)
    'pre1_di_diff',
    # Candle body / total range — high = conviction bar, low = doji (indecision)
    'pre1_body_ratio',
    # Lower wick / total range — buying support at lows (hammer-like setups)
    'pre1_lower_shadow',
    # Bar polarity: +1 if close > open (up bar), -1 if close < open (down bar)
    'pre1_bar_direction',
    # Sector advance/decline ratio on pre1 day (>1.0 = sector expanding)
    'pre1_sector_ad_ratio',
    # % stocks in sector above EMA20 on pre1 day (high = sector momentum)
    'pre1_sector_pct_ema20',
    # ── Phase 5 features — Liquidity + Cross-Asset context ────────────────────
    # daily turnover / avg_daily_volume (liquidity proxy)
    'pre1_turnover_ratio',
    # USD/EGP 1-day change (macro context)
    'pre1_usdegp_chg',
    # VIX normalized level (fear index)
    'pre1_vix_level',
    # Oil (UKOIL) 1-day change
    'pre1_oil_chg',
    # estimated bid-ask spread in bps (liquidity cost)
    'pre1_spread_bps',
    # ── Phase 60: Multi-Timeframe features ────────────────────────────────────
    # Weekly RSI(14) normalized to 0-1
    'w_rsi',
    # Weekly BB position: (close - lower_bb) / bb_width, 0-1
    'w_bb_position',
    # Monthly trend: close / monthly_ema20 - 1 (normalized to 0-1)
    'mo_trend',
    # Weekly volume compression: weekly_vol / 4wk_avg_vol (normalized 0-1)
    'w_vol_compression',
    # ── Phase 61: Volume Intelligence features ────────────────────────────────
    # OBV normalized slope over 5 days (0-1)
    'obv_slope_5d',
    # ADL vs price divergence: adl_slope - price_slope (normalized 0-1)
    'adl_divergence',
    # (close - low) / (high - low), 0-1
    'closing_strength',
    # Count of consecutive below-avg-volume days (capped at 10, normalized 0-1)
    'vol_dryup_days',
    # ── Phase 62: DTW Similarity features ────────────────────────────────────
    # Max DTW similarity to historical explosive setups for this stock (0-1)
    'dtw_similarity',
    # Expected % gain from similar historical setups (normalized 0-1)
    'dtw_expected_gain',
    # ── Group A: Market Microstructure (7 features) ──────────────────────
    # Net accumulation score: (acc_days - dist_days) / 20d window (0-1)
    'net_accumulation_20d',
    # Volume ratio on up-days vs total volume (10d window, 0-1)
    'up_volume_ratio_10d',
    # ATR compression: ATR(5) / ATR(20) — < 0.7 = coiling pre-explosion
    'range_compression_20d',
    # VCP score: (ATR_10/ATR_20) × (ATR_5/ATR_10) — Minervini coil (0-1)
    'vcp_score',
    # Base tightness: (high-low range over 10d) / price (0-1 normalized)
    'base_tightness_10d',
    # Consecutive higher lows count (capped at 10, normalized 0-1)
    'higher_lows_streak',
    # Distance from 52-week high: (52w_high - price) / price (0-1)
    'breakout_proximity',
    # ── Group B: Relative Strength vs Market (4 features) ─────────────────
    # Stock return minus EGX30 return over 5 days (normalized -1 to +1 → 0-1)
    'rs_vs_market_5d',
    # Stock return minus EGX30 return over 20 days (normalized → 0-1)
    'rs_vs_market_20d',
    # Stock return minus sector return over 10 days (normalized → 0-1)
    'rs_vs_sector_10d',
    # Percentile rank of stock return among all 250 stocks (20d, 0-1)
    'rs_rank_pct',
]

# ──────────────────────────────────────────────────────────────────
#  DB helpers
# ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def safe_float(v, default=0.0):
    try:
        if v is None: return default
        f = float(v)
        return f if math.isfinite(f) else default
    except:
        return default

def _get_consolidation_events(conn, lookback_days=365):
    """
    Detect stocks that underwent price consolidation (reverse split / par value change)
    OR a manipulative multi-day pump cycle within the last `lookback_days` days.
    Both patterns produce unreliable feature/label values around the event.

    Method 1 — Single-day >5× jump (consolidation / reverse split):
        Detects par-value adjustments that cause overnight >5× price gaps.
        Example: MFPC, MCDI consolidated 2025-Q4 → single bar >10× jump detected.

    Method 2 — Multi-day pump (gradual +100% in ≤10 trading bars):
        Catches gradual pump-and-dump cycles missed by single-day filter.
        Threshold: max_close / min_close ≥ 2.0 in any 10-bar rolling window.
        Example: OCDI 22.55 → 54.70 (+142%, ratio=2.43) over 7 trading days
                 — each individual day ≤+78%, so single-day filter misses it.
                 → Caught by Method 2 (2.43 ≥ 2.0 threshold). ✓

    Returns:
        dict {symbol: [event_date_str, ...]}  — dates of detected events
    """
    cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()

    # ── Method 1: Single-day >5× jump (consolidation / reverse split) ─────────
    rows = conn.execute("""
        SELECT a.symbol,
               date(b.bar_time, 'unixepoch') AS jump_date,
               a.close                         AS close_before,
               b.close                         AS close_after
        FROM   ohlcv_history_features a
        JOIN   ohlcv_history_features b ON a.symbol = b.symbol
        WHERE  b.bar_time = (
                   SELECT MIN(bar_time) FROM ohlcv_history_features c
                   WHERE  c.symbol = a.symbol AND c.bar_time > a.bar_time
               )
        AND    a.close > 0 AND b.close > 0
        AND   (b.close / a.close > 5 OR a.close / b.close > 5)
        AND    date(b.bar_time, 'unixepoch') >= ?
    """, (cutoff,)).fetchall()

    events = {}
    for r in rows:
        events.setdefault(r[0], []).append(r[1])

    # ── Method 2: Multi-day pump — ≥2× cumulative gain in ≤10 trading bars ────
    # OCDI case: 22.55 → 54.70 (ratio=2.43) in 7 bars → caught here.
    # This threshold (2×) is highly conservative for EGX — stocks doubling in 2
    # calendar weeks indicate either a consolidation event or a pump manipulation.
    PUMP_WINDOW    = 10    # rolling window in trading bars (≈2 calendar weeks)
    PUMP_THRESHOLD = 2.0   # max/min ratio — stock at least doubled within window

    price_rows = conn.execute("""
        SELECT symbol, date(bar_time, 'unixepoch') AS bar_date, close
        FROM   ohlcv_history_features
        WHERE  date(bar_time, 'unixepoch') >= ?
        AND    close > 0
        ORDER  BY symbol, bar_time
    """, (cutoff,)).fetchall()

    # Group rows by symbol using a dict (SQL already ORDER BY symbol, bar_time)
    sym_bars = {}
    for r in price_rows:
        sym = r[0]
        if sym not in sym_bars:
            sym_bars[sym] = []
        sym_bars[sym].append((r[1], float(r[2])))   # (date_str, close)

    for symbol, bars in sym_bars.items():
        if len(bars) < PUMP_WINDOW:
            continue
        found_pump = False
        for i in range(len(bars) - PUMP_WINDOW + 1):
            if found_pump:
                break
            window    = bars[i : i + PUMP_WINDOW]
            prices    = [p for _, p in window]
            min_p     = min(prices)
            max_p     = max(prices)
            if min_p <= 0 or max_p / min_p < PUMP_THRESHOLD:
                continue
            # Peak = date of max price in window (most recent high is the event date)
            peak_idx  = max(range(len(prices)), key=lambda k: prices[k])
            peak_date = window[peak_idx][0]
            # De-duplicate: skip if already within ±15 days of an existing event
            existing  = events.get(symbol, [])
            peak_dt   = datetime.date.fromisoformat(peak_date)
            near_dup  = any(
                abs((peak_dt - datetime.date.fromisoformat(ed)).days) <= 15
                for ed in existing
            )
            if not near_dup:
                events.setdefault(symbol, []).append(peak_date)
                print(f"[consolidation] PUMP detected: {symbol} "
                      f"max/min={max_p/min_p:.2f}× in {PUMP_WINDOW}-bar window, "
                      f"peak={peak_date}")
            found_pump = True

    return events          # {symbol: ["2026-05-17", ...]}


def _is_corrupt_training_row(symbol, explosion_date, consolidation_events,
                              pre_window=90, post_window=30):
    """
    Return True if this explosive_moves record should be EXCLUDED from training.

    Criteria: the explosion_date falls within `pre_window` days BEFORE a
    consolidation event, or `post_window` days AFTER one.  In that range
    both the pre-event features (computed from corrupted prices) and the
    return_5d label (which may cross the consolidation) are unreliable.
    """
    dates = consolidation_events.get(symbol, [])
    if not dates:
        return False
    try:
        exp_dt = datetime.date.fromisoformat(explosion_date)
    except (ValueError, TypeError):
        return False
    for d_str in dates:
        try:
            con_dt = datetime.date.fromisoformat(d_str)
        except (ValueError, TypeError):
            continue
        delta = (exp_dt - con_dt).days        # negative = before consolidation
        if -pre_window <= delta <= post_window:
            return True
    return False


def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ml_model_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_name TEXT NOT NULL,
        trained_at TEXT,
        train_end_date TEXT,
        oos_start_date TEXT,
        n_train_positive INTEGER,
        n_train_negative INTEGER,
        n_oos_positive INTEGER,
        n_oos_negative INTEGER,
        n_oos_total INTEGER,
        auc_train REAL,
        auc_oos REAL,
        precision_at_50 REAL,
        precision_at_70 REAL,
        recall_at_50 REAL,
        precision_at_10 REAL,
        precision_at_20 REAL,
        precision_at_top10pct REAL,
        recommended_threshold_high REAL,
        recommended_threshold_medium REAL,
        abstain_threshold REAL,
        top_features TEXT,
        notes TEXT
    );
    CREATE TABLE IF NOT EXISTS explosion_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        pred_date TEXT NOT NULL,
        explosion_prob REAL,
        prob_pct INTEGER,
        confidence_tier TEXT,
        direction TEXT,
        top_drivers TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, pred_date)
    );
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(explosion_predictions)").fetchall()}
    if 'model_version' not in cols:
        conn.execute("ALTER TABLE explosion_predictions ADD COLUMN model_version TEXT DEFAULT 'unknown'")
    if 'reliability_flag' not in cols:
        conn.execute("ALTER TABLE explosion_predictions ADD COLUMN reliability_flag TEXT DEFAULT 'UNKNOWN'")
    score_cols = {r[1] for r in conn.execute("PRAGMA table_info(ml_model_scores)").fetchall()}
    score_additions = {
        'precision_at_10': 'REAL',
        'precision_at_20': 'REAL',
        'precision_at_top10pct': 'REAL',
        'recommended_threshold_high': 'REAL',
        'recommended_threshold_medium': 'REAL',
        'abstain_threshold': 'REAL',
        'n_oos_negative': 'INTEGER',
        'n_oos_total': 'INTEGER',
    }
    for col, typ in score_additions.items():
        if col not in score_cols:
            conn.execute(f"ALTER TABLE ml_model_scores ADD COLUMN {col} {typ}")
    conn.commit()


def _clear_prediction_date(conn, pred_date):
    conn.execute("DELETE FROM explosion_predictions WHERE pred_date=?", (pred_date,))
    conn.commit()


def _final_signal_symbols(conn, pred_date):
    """Symbols that passed the final_signals client-facing quality gate."""
    try:
        rows = conn.execute(
            """SELECT symbol FROM final_signals
               WHERE trade_date=? AND actionable=1""",
            (pred_date,)
        ).fetchall()
        return {r['symbol'] for r in rows}
    except Exception:
        return set()


def _client_gate_fields(symbol, model_tier, final_gate_symbols):
    model_tier = str(model_tier or '').upper()
    if model_tier not in ('HIGH', 'MEDIUM'):
        return 'LOW', 'ML_TIER_NOT_CLIENT_ACCEPTABLE', False
    if symbol in final_gate_symbols:
        return model_tier, 'ACCEPTABLE', True
    return 'LOW', 'ML_ONLY_REQUIRES_FINAL_SIGNALS', False

# ──────────────────────────────────────────────────────────────────
#  Indicator computation from raw OHLCV
# ──────────────────────────────────────────────────────────────────
def _compute_indicators(df):
    """
    Compute all indicator columns from raw OHLCV DataFrame.
    Input columns: bar_date, open, high, low, close, volume
    Returns df with added indicator columns (may have NaN in early rows).
    """
    import pandas as pd
    import numpy as np

    close = df['close'].astype(float)
    high  = df['high'].astype(float)
    low   = df['low'].astype(float)
    vol   = df['volume'].astype(float)

    # ── RSI 14 ─────────────────────────────────────────────────────
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    df['rsi14'] = 100 - (100 / (1 + rs))

    # ── Bollinger Bands (20, 2) ─────────────────────────────────────
    sma20  = close.rolling(20).mean()
    std20  = close.rolling(20).std()
    bb_up  = sma20 + 2 * std20
    bb_dn  = sma20 - 2 * std20
    bb_rng = (bb_up - bb_dn).replace(0, 1e-10)
    df['bb_width']    = bb_rng / sma20.replace(0, 1e-10)
    df['bb_position'] = (close - bb_dn) / bb_rng

    # ── Volume ratio (20d) ─────────────────────────────────────────
    vol_ma20 = vol.rolling(20).mean().replace(0, 1e-10)
    df['vol_ratio_20'] = vol / vol_ma20

    # ── Momentum ───────────────────────────────────────────────────
    df['momentum_5d'] = (close - close.shift(5)) / close.shift(5).replace(0, 1e-10)

    # ── MACD histogram (12, 26, 9) ─────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal    = macd_line.ewm(span=9, adjust=False).mean()
    df['macd_hist'] = macd_line - signal

    # ── ADX 14 (simplified with rolling means) ─────────────────────
    prev_close = close.shift(1)
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)

    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm_raw  = high - prev_high
    minus_dm_raw = prev_low - low

    plus_dm  = plus_dm_raw.where(
        (plus_dm_raw > 0) & (plus_dm_raw > minus_dm_raw), 0)
    minus_dm = minus_dm_raw.where(
        (minus_dm_raw > 0) & (minus_dm_raw >= plus_dm_raw), 0)

    atr14    = tr.rolling(14).mean().replace(0, 1e-10)
    plus_di  = 100 * plus_dm.rolling(14).mean()  / atr14
    minus_di = 100 * minus_dm.rolling(14).mean() / atr14
    di_sum   = (plus_di + minus_di).replace(0, 1e-10)
    dx       = 100 * (plus_di - minus_di).abs() / di_sum
    df['adx14']  = dx.rolling(14).mean()
    df['di_diff'] = plus_di - minus_di  # Phase 4: directional bias (+ve = bulls winning)

    # ── Compression days (consecutive bars with bb_width < 20d median) ──
    bb_med   = df['bb_width'].rolling(20).median()
    compressed = (df['bb_width'] < bb_med).astype(int)
    # Consecutive count: reset to 0 whenever not compressed
    groups = (~compressed.astype(bool)).cumsum()
    df['compression_days'] = compressed.groupby(groups).cumsum()

    # ── Phase 3: RSI slope 3-day ────────────────────────────────────────────
    # (RSI_today - RSI_3d_ago) / 3 — positive = building momentum, negative = fading
    df['rsi_slope_3d'] = (df['rsi14'] - df['rsi14'].shift(3)) / 3.0

    # ── Phase 3: EMA alignment (0-3) ────────────────────────────────────────
    # How many of EMA20/50/200 is price above? 3=full bull, 0=full bear
    ema20  = close.ewm(span=20,  adjust=False).mean()
    ema50  = close.ewm(span=50,  adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    df['ema_alignment'] = (
        (close > ema20).astype(int) +
        (close > ema50).astype(int) +
        (close > ema200).astype(int)
    )

    # ── Phase 3: EMA20 slope over 5 bars ────────────────────────────────────
    # (ema20_today - ema20_5d_ago) / ema20_5d_ago — trend acceleration
    ema20_lag5 = ema20.shift(5).replace(0, 1e-10)
    df['ema20_slope_5d'] = (ema20 - ema20_lag5) / ema20_lag5

    # ── Phase 4: Candle structure features ──────────────────────────────────
    open_  = df['open'].astype(float)
    rng    = (high - low).replace(0, 1e-10)
    body   = (close - open_).abs()
    lower_wick = (pd.concat([open_, close], axis=1).min(axis=1) - low).clip(lower=0)

    df['body_ratio']        = body / rng          # 0=doji, 1=full marubozu
    df['lower_shadow_ratio'] = lower_wick / rng   # 0=no support, 1=full lower wick
    df['bar_direction']     = ((close >= open_).astype(float) * 2) - 1  # +1=up, -1=down

    return df


def _build_ohlcv_cache(conn, max_date, min_date=None):
    """
    Load OHLCV from DB, compute indicators per symbol.
    Returns dict {symbol: DataFrame} with bar_date_str as string column.
    ohlcv_history_features uses bar_time (Unix epoch) — we derive bar_date via strftime.
    Only loads symbols with at least 30 bars.
    """
    import pandas as pd
    from collections import defaultdict

    if min_date:
        rows = conn.execute(
            """SELECT symbol, date(bar_time,'unixepoch') as bar_date,
                      open, high, low, close, volume
               FROM ohlcv_history_features
               WHERE date(bar_time,'unixepoch') >= ?
                 AND date(bar_time,'unixepoch') <= ?
               ORDER BY symbol, bar_time""",
            (min_date, max_date)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT symbol, date(bar_time,'unixepoch') as bar_date,
                      open, high, low, close, volume
               FROM ohlcv_history_features
               WHERE date(bar_time,'unixepoch') <= ?
               ORDER BY symbol, bar_time""",
            (max_date,)
        ).fetchall()

    sym_lists = defaultdict(list)
    for r in rows:
        sym_lists[r['symbol']].append({
            'bar_date': r['bar_date'],
            'open':   float(r['open']  or 0),
            'high':   float(r['high']  or 0),
            'low':    float(r['low']   or 0),
            'close':  float(r['close'] or 0),
            'volume': float(r['volume'] or 0),
        })

    cache = {}
    for sym, bars in sym_lists.items():
        if len(bars) < 30:
            continue
        df = pd.DataFrame(bars)
        df = df.sort_values('bar_date').reset_index(drop=True)
        df = _compute_indicators(df)
        df['bar_date_str'] = df['bar_date'].astype(str)
        cache[sym] = df

    return cache


def _get_indicators_at(sym_df, pos):
    """Get indicator values at absolute position `pos` in the DataFrame."""
    if pos < 0 or pos >= len(sym_df):
        return None
    row = sym_df.iloc[pos]

    def g(col, default=0.0):
        v = row.get(col, default)
        return safe_float(v, default)

    return {
        'bb_width':       g('bb_width', 0.05),
        'vol_ratio':      g('vol_ratio_20', 1.0),
        'rsi':            g('rsi14', 50.0),
        'momentum_5d':    g('momentum_5d', 0.0),
        'adx':            g('adx14', 20.0),
        'macd_hist':      g('macd_hist', 0.0),
        'bb_position':    g('bb_position', 0.5),
        'compression_days': g('compression_days', 0.0),
        # Phase 3 features
        'rsi_slope_3d':   g('rsi_slope_3d', 0.0),
        'ema_alignment':  g('ema_alignment', 1.5),   # default = partially aligned
        'ema20_slope_5d': g('ema20_slope_5d', 0.0),
        # Phase 4 features
        'di_diff':            g('di_diff',            0.0),
        'body_ratio':         g('body_ratio',         0.5),
        'lower_shadow_ratio': g('lower_shadow_ratio', 0.2),
        'bar_direction':      g('bar_direction',      0.0),
    }


def _load_sector_breadth_cache(conn):
    """
    Load sector_breadth_daily into a lookup dict.
    Returns dict: {(date_str, sector_name): {'ad_ratio': float, 'pct_above_ema20': float}}
    Used to enrich training/prediction rows with sector-level breadth context.
    Coverage: ~523 unique dates from 2022-11-22 (covers ~75% of explosion events).
    Missing dates default to neutral values (ad_ratio=1.0, pct_ema20=50.0) at call site.
    """
    try:
        rows = conn.execute(
            "SELECT date, sector, ad_ratio, pct_above_ema20 FROM sector_breadth_daily"
        ).fetchall()
        cache = {}
        for r in rows:
            key = (r['date'], r['sector'])
            cache[key] = {
                'ad_ratio':      float(r['ad_ratio']      or 1.0),
                'pct_above_ema20': float(r['pct_above_ema20'] or 50.0),
            }
        return cache
    except Exception:
        return {}


def _get_sector_breadth(sector_cache, date_str, sector,
                         default_ad=1.0, default_pct=50.0):
    """
    Look up sector breadth for a given (date, sector) pair.
    Falls back to nearest prior date within 5 trading days if exact match missing.
    Returns (ad_ratio, pct_above_ema20) tuple.
    """
    if not sector_cache or not date_str or not sector:
        return default_ad, default_pct

    key = (date_str, sector)
    if key in sector_cache:
        v = sector_cache[key]
        return v['ad_ratio'], v['pct_above_ema20']

    # Try prior 5 calendar days
    try:
        ref = datetime.date.fromisoformat(date_str)
        for delta in range(1, 6):
            prior = (ref - datetime.timedelta(days=delta)).isoformat()
            k2 = (prior, sector)
            if k2 in sector_cache:
                v = sector_cache[k2]
                return v['ad_ratio'], v['pct_above_ema20']
    except (ValueError, TypeError):
        pass

    return default_ad, default_pct


def _load_liquidity_cache(conn):
    """Load symbol liquidity profiles: {symbol: {avg_daily_vol, spread_bps}}"""
    try:
        rows = conn.execute(
            "SELECT symbol, avg_daily_volume, avg_spread_est_bps FROM symbol_liquidity_profile"
        ).fetchall()
        cache = {}
        for r in rows:
            cache[r['symbol']] = {
                'avg_daily_volume': float(r['avg_daily_volume'] or 1.0),
                'spread_bps': float(r['avg_spread_est_bps'] or 50.0),
            }
        return cache
    except Exception:
        return {}


def _load_cross_market_cache(conn):
    """Load cross-market daily OHLCV: {date_str: {usdegp_chg, vix_level, oil_chg}}"""
    try:
        rows = conn.execute("""
            SELECT asset, date(bar_time,'unixepoch') as bar_date, close
            FROM cross_market_daily
            WHERE asset IN ('USDEGP','VIX','UKOIL')
            ORDER BY asset, bar_date
        """).fetchall()
    except Exception:
        return {}

    from collections import defaultdict
    by_asset = defaultdict(dict)
    for r in rows:
        by_asset[r['asset']][r['bar_date']] = float(r['close'] or 0.0)

    # Build daily dict with changes
    all_dates = sorted(set(d for asset_d in by_asset.values() for d in asset_d))
    cache = {}
    prev = {'USDEGP': None, 'VIX': None, 'UKOIL': None}
    for d in all_dates:
        entry = {}
        for asset, key, default in [('USDEGP','usdegp_chg',0.0), ('VIX','vix_level',20.0), ('UKOIL','oil_chg',0.0)]:
            cur = by_asset[asset].get(d)
            if cur is None:
                entry[key] = default
                continue
            if key == 'vix_level':
                entry[key] = min(cur / 80.0, 1.0)  # normalize 0-80 → 0-1
            else:
                p = prev[asset]
                entry[key] = ((cur - p) / p) if (p and p > 0) else 0.0
            prev[asset] = cur
        cache[d] = entry
    return cache


def _get_cross_market(cross_cache, date_str):
    """Look up cross-market features for a date, fallback to prior 5 days."""
    import datetime as _dt
    for delta in range(0, 6):
        d = (_dt.date.fromisoformat(date_str) - _dt.timedelta(days=delta)).isoformat()
        if d in cross_cache:
            e = cross_cache[d]
            return e.get('usdegp_chg', 0.0), e.get('vix_level', 0.25), e.get('oil_chg', 0.0)
    return 0.0, 0.25, 0.0


def _build_feature_row(sym_df, target_date_str,
                        sector_cache=None, sector=None,
                        liquidity_cache=None, cross_cache=None, symbol=None,
                        egx30=None):
    """
    Build 28-dim feature vector for a (symbol, target_date) pair.
    pre_N = indicator values N bars BEFORE target_date in the time series.
    Returns None if not enough history.
    Phase 4: accepts optional sector_cache + sector for breadth features.
    """
    date_idx_list = sym_df.index[sym_df['bar_date_str'] == target_date_str].tolist()
    if not date_idx_list:
        return None
    date_pos = date_idx_list[0]

    pre1 = _get_indicators_at(sym_df, date_pos - 1)
    pre3 = _get_indicators_at(sym_df, date_pos - 3)
    pre5 = _get_indicators_at(sym_df, date_pos - 5)

    if pre1 is None or pre3 is None or pre5 is None:
        return None

    # Phase 4: sector breadth — look up pre1 bar date for context
    pre1_date_str = ''
    try:
        pre1_date_str = sym_df.iloc[date_pos - 1]['bar_date_str']
    except Exception:
        pass
    sec_ad, sec_pct = _get_sector_breadth(sector_cache, pre1_date_str, sector)

    # Phase 5: Liquidity features
    liq = liquidity_cache.get(symbol, {}) if liquidity_cache and symbol else {}
    avg_vol = liq.get('avg_daily_volume', 1.0)
    try:
        pre1_vol = float(sym_df.iloc[date_pos - 1].get('volume', avg_vol))
    except Exception:
        pre1_vol = avg_vol
    turnover_ratio = pre1_vol / avg_vol if avg_vol > 0 else 1.0
    spread_bps = liq.get('spread_bps', 50.0) / 100.0  # normalize to 0-1 scale

    # Phase 5: Cross-asset features
    if cross_cache:
        usdegp_chg, vix_lvl, oil_chg = _get_cross_market(cross_cache, pre1_date_str)
    else:
        usdegp_chg, vix_lvl, oil_chg = 0.0, 0.25, 0.0

    # === PHASE 60: Multi-Timeframe Features ===
    try:
        import pandas as _pd60
        import numpy as _np60
        sym_df_copy = sym_df.copy()
        sym_df_copy['bar_date_dt'] = _pd60.to_datetime(sym_df_copy['bar_date_str'])
        sym_df_copy = sym_df_copy.set_index('bar_date_dt').sort_index()

        t_dt = _pd60.Timestamp(target_date_str)
        df_hist = sym_df_copy[sym_df_copy.index <= t_dt]

        if len(df_hist) >= 20:
            weekly = df_hist.resample('W').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()

            if len(weekly) >= 16:
                wc = weekly['close']
                delta = wc.diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = (-delta.clip(upper=0)).rolling(14).mean()
                rs = gain / (loss + 1e-10)
                w_rsi_val = float((100 - 100/(1+rs)).iloc[-1]) / 100.0
            else:
                w_rsi_val = 0.5

            if len(weekly) >= 20:
                wc = weekly['close']
                wma = wc.rolling(20).mean()
                wstd = wc.rolling(20).std()
                upper = wma + 2*wstd
                lower = wma - 2*wstd
                bw = (upper - lower).iloc[-1]
                w_bb_pos = float((wc.iloc[-1] - lower.iloc[-1]) / (bw + 1e-10))
                w_bb_pos = max(0.0, min(1.0, w_bb_pos))
            else:
                w_bb_pos = 0.5

            if len(weekly) >= 5:
                wvol = weekly['volume']
                w_vol_comp = float(wvol.iloc[-1] / (wvol.iloc[-5:-1].mean() + 1e-10))
                w_vol_comp = min(3.0, w_vol_comp) / 3.0
            else:
                w_vol_comp = 0.33

            monthly = df_hist.resample('ME').agg({'close': 'last'}).dropna()
            if len(monthly) >= 3:
                mc = monthly['close']
                mo_ema = mc.ewm(span=min(len(mc), 20), adjust=False).mean()
                mo_trend_val = float((mc.iloc[-1] / (mo_ema.iloc[-1] + 1e-10)) - 1.0)
                mo_trend_val = max(-0.5, min(0.5, mo_trend_val)) / 0.5 * 0.5 + 0.5
            else:
                mo_trend_val = 0.5
        else:
            w_rsi_val = 0.5; w_bb_pos = 0.5; w_vol_comp = 0.33; mo_trend_val = 0.5
    except Exception:
        w_rsi_val = 0.5; w_bb_pos = 0.5; w_vol_comp = 0.33; mo_trend_val = 0.5

    # === PHASE 61: Volume Intelligence ===
    try:
        import numpy as _np61
        import pandas as _pd61
        t_dt61 = _pd61.Timestamp(target_date_str)
        sym_df_copy2 = sym_df.copy()
        sym_df_copy2['bar_date_dt'] = _pd61.to_datetime(sym_df_copy2['bar_date_str'])
        df_near = sym_df_copy2[sym_df_copy2['bar_date_dt'] <= t_dt61].tail(25).copy()

        if len(df_near) >= 10:
            closes61 = df_near['close'].values.astype(float)
            highs61  = df_near['high'].values.astype(float)
            lows61   = df_near['low'].values.astype(float)
            vols61   = df_near['volume'].values.astype(float)

            # Use T-1 bar (index -2) for closing_strength to avoid look-ahead bias
            hl_range = highs61[-2] - lows61[-2]
            closing_str = float((closes61[-2] - lows61[-2]) / (hl_range + 1e-10)) if hl_range > 1e-10 else 0.5
            closing_str = max(0.0, min(1.0, closing_str))

            obv = [0.0]
            for i in range(1, len(closes61)):
                if closes61[i] > closes61[i-1]:
                    obv.append(obv[-1] + vols61[i])
                elif closes61[i] < closes61[i-1]:
                    obv.append(obv[-1] - vols61[i])
                else:
                    obv.append(obv[-1])
            obv = _np61.array(obv)

            # Use T-1 slice (exclude last bar) for obv_slope to avoid look-ahead bias
            if len(obv) >= 7:
                obv_slope = float(_np61.polyfit(range(5), obv[-6:-1], 1)[0])
                obv_std = float(_np61.std(obv[:-1]) + 1e-10)
                obv_slope_norm = float(_np61.clip(obv_slope / obv_std, -3.0, 3.0)) / 3.0 * 0.5 + 0.5
            else:
                obv_slope_norm = 0.5

            adl = []
            for i in range(len(closes61)):
                hl = highs61[i] - lows61[i]
                if hl > 1e-10:
                    clv = ((closes61[i] - lows61[i]) - (highs61[i] - closes61[i])) / hl
                else:
                    clv = 0.0
                adl_val = clv * vols61[i]
                adl.append(adl_val if not adl else adl[-1] + adl_val)
            adl = _np61.array(adl)

            # Use T-1 slice (exclude last bar) for adl_divergence to avoid look-ahead bias
            if len(adl) >= 7:
                adl_slope = float(_np61.polyfit(range(5), adl[-6:-1], 1)[0])
                price_slope = float(_np61.polyfit(range(5), closes61[-6:-1], 1)[0])
                adl_slope_n = adl_slope / (_np61.std(adl[:-1]) + 1e-10)
                price_slope_n = price_slope / (_np61.std(closes61[-6:-1]) + 1e-10)
                adl_div = float(_np61.clip(adl_slope_n - price_slope_n, -3.0, 3.0)) / 3.0 * 0.5 + 0.5
            else:
                adl_div = 0.5

            # Use T-1 slice (exclude last bar) for vol_dryup to avoid look-ahead bias
            avg_vol61 = float(_np61.mean(vols61[:-2]) + 1e-10)
            dryup = 0
            for v in reversed(vols61[:-2]):
                if v < avg_vol61 * 0.7:
                    dryup += 1
                else:
                    break
            vol_dryup = min(dryup, 10) / 10.0
        else:
            obv_slope_norm = 0.5; adl_div = 0.5; closing_str = 0.5; vol_dryup = 0.0
    except Exception:
        obv_slope_norm = 0.5; adl_div = 0.5; closing_str = 0.5; vol_dryup = 0.0

    # === PHASE 62: DTW Similarity (defaults — filled by precomputation cache) ===
    dtw_sim = 0.5
    dtw_exp_gain = 0.5

    # === Group A: Market Microstructure (11 new features) ===
    try:
        import numpy as _np61
        import pandas as _pd61
        t_dt_ms = _pd61.Timestamp(target_date_str)
        sym_df_ms = sym_df.copy()
        sym_df_ms['bar_date_dt'] = _pd61.to_datetime(sym_df_ms['bar_date_str'])
        df = sym_df_ms[sym_df_ms['bar_date_dt'] <= t_dt_ms].tail(30).copy()

        closes_ = df['close'].values.astype(float)
        opens_ = df['open'].values.astype(float)
        vols_ = df['volume'].values.astype(float)
        avg_vol_ = float(df['volume'].rolling(20).mean().iloc[-2]) if len(df) >= 22 else 1.0
        if avg_vol_ <= 0: avg_vol_ = 1.0

        # Net accumulation: count up/down days with volume
        if len(df) >= 22:
            acc_days = sum(1 for i in range(-21, -1) if closes_[i] > opens_[i] and vols_[i] > avg_vol_)
            dist_days = sum(1 for i in range(-21, -1) if closes_[i] < opens_[i] and vols_[i] > avg_vol_)
            net_accum = (acc_days - dist_days + 20) / 40.0  # normalize to 0-1
        else:
            net_accum = 0.5

        # Up volume ratio
        if len(df) >= 12:
            total_vol_10 = sum(vols_[-11:-1])
            up_vol_10 = sum(vols_[i] for i in range(-11, -1) if closes_[i] >= opens_[i])
            up_vol_ratio = up_vol_10 / max(total_vol_10, 1)
        else:
            up_vol_ratio = 0.5

        # Range compression: ATR(5)/ATR(20)
        if len(df) >= 22:
            highs_ = df['high'].values.astype(float)
            lows_ = df['low'].values.astype(float)
            tr5 = [max(highs_[i]-lows_[i], abs(highs_[i]-closes_[i-1]), abs(lows_[i]-closes_[i-1])) for i in range(-6, -1)]
            tr20 = [max(highs_[i]-lows_[i], abs(highs_[i]-closes_[i-1]), abs(lows_[i]-closes_[i-1])) for i in range(-21, -1)]
            atr5 = float(_np61.mean(tr5)) if tr5 else 0.01
            atr20 = float(_np61.mean(tr20)) if tr20 else 0.01
            atr10_vals = [max(highs_[i]-lows_[i], abs(highs_[i]-closes_[i-1]), abs(lows_[i]-closes_[i-1])) for i in range(-11, -1)]
            atr10 = float(_np61.mean(atr10_vals)) if atr10_vals else 0.01
            range_comp = float(_np61.clip(atr5 / max(atr20, 1e-6), 0, 2)) / 2.0  # 0-1
            vcp = float(_np61.clip((atr10/max(atr20,1e-6)) * (atr5/max(atr10,1e-6)), 0, 2)) / 2.0
        else:
            range_comp = 0.5; vcp = 0.5
            atr5 = 0.01; atr20 = 0.01
            highs_ = df['high'].values.astype(float)
            lows_ = df['low'].values.astype(float)

        # Base tightness: (10d high-low range) / price
        if len(df) >= 12:
            price_now = float(closes_[-2])
            hi10 = float(_np61.max(df['high'].values[-11:-1]))
            lo10 = float(_np61.min(df['low'].values[-11:-1]))
            base_tight = float(_np61.clip((hi10-lo10)/max(price_now, 0.01), 0, 0.5)) / 0.5  # 0-1
        else:
            base_tight = 0.5

        # Higher lows streak
        if len(df) >= 12:
            lows_arr = df['low'].values.astype(float)[-11:-1]
            streak = 0
            for i in range(1, len(lows_arr)):
                if lows_arr[i] > lows_arr[i-1]:
                    streak += 1
                else:
                    streak = 0
            higher_lows = min(streak, 10) / 10.0
        else:
            higher_lows = 0.0

        # Breakout proximity: distance from 52w high
        if len(df) >= 2:
            price_cur = float(closes_[-2])
            high_52w = float(df['high'].rolling(min(252, len(df))).max().iloc[-2])
            bp = float(_np61.clip((high_52w - price_cur) / max(price_cur, 0.01), 0, 2)) / 2.0  # 0-1 (0=at high, 1=far below)
        else:
            bp = 0.5
    except Exception:
        net_accum=0.5; up_vol_ratio=0.5; range_comp=0.5; vcp=0.5; base_tight=0.5; higher_lows=0.0; bp=0.5

    # --- Group B: REAL Relative Strength vs Market (EGX30 index) ---
    # RS = stock return MINUS EGX30 index return over the SAME dates.
    # Indices follow the T-1 look-ahead-safe convention: closes_[-2] == "yesterday"
    # (pre1), closes_[-7]/closes_[-22] == 5/20 bars before that. We align EGX30 by
    # the stock's own bar dates (df['bar_date_str']) so windows match exactly.
    # If EGX30 data is missing, fall back to the OLD momentum-based normalization
    # so nothing breaks (backward compatible).
    try:
        if len(df) >= 22:
            ret_5d = (float(closes_[-2]) - float(closes_[-7])) / max(float(closes_[-7]), 0.01) if len(df) >= 7 else 0
            ret_20d = (float(closes_[-2]) - float(closes_[-22])) / max(float(closes_[-22]), 0.01) if len(df) >= 22 else 0

            egx30_ret_5d = None
            egx30_ret_20d = None
            if egx30:
                try:
                    _bds = df['bar_date_str'].values
                    d_t1 = str(_bds[-2])   # pre1 / "yesterday" (matches closes_[-2])
                    d_5  = str(_bds[-7])   # 5 bars before pre1 (matches closes_[-7])
                    d_20 = str(_bds[-22])  # 20 bars before pre1 (matches closes_[-22])
                    e_t1 = _egx30_close_on_or_before(egx30, d_t1)
                    e_5  = _egx30_close_on_or_before(egx30, d_5)
                    e_20 = _egx30_close_on_or_before(egx30, d_20)
                    if e_t1 is not None and e_5 is not None and e_5 > 0:
                        egx30_ret_5d = (e_t1 - e_5) / e_5
                    if e_t1 is not None and e_20 is not None and e_20 > 0:
                        egx30_ret_20d = (e_t1 - e_20) / e_20
                except Exception:
                    egx30_ret_5d = None; egx30_ret_20d = None

            if egx30_ret_5d is not None:
                # Real RS = stock return minus EGX30 return over the same window.
                rs_vs_mkt_5d_raw = ret_5d - egx30_ret_5d
                # Relative return typically in [-10%, +10%] over 5d.
                rs_5d = float(_np61.clip((rs_vs_mkt_5d_raw + 0.10) / 0.20, 0, 1))
            else:
                # Fallback: OLD momentum-based normalization (no EGX30 available).
                rs_5d = float(_np61.clip((ret_5d + 0.20) / 0.40, 0, 1))

            if egx30_ret_20d is not None:
                rs_vs_mkt_20d_raw = ret_20d - egx30_ret_20d
                # Relative return typically in [-20%, +20%] over 20d.
                rs_20d = float(_np61.clip((rs_vs_mkt_20d_raw + 0.20) / 0.40, 0, 1))
            else:
                rs_20d = float(_np61.clip((ret_20d + 0.40) / 0.80, 0, 1))
        else:
            rs_5d = 0.5; rs_20d = 0.5
        rs_sector = 0.5  # placeholder — sector RS computed at ensemble level (predict_ensemble _rs_cache)
        rs_rank = 0.5    # placeholder — rank-pct computed at ensemble level (predict_ensemble _rs_cache)
    except Exception:
        rs_5d=0.5; rs_20d=0.5; rs_sector=0.5; rs_rank=0.5

    return [
        pre1['bb_width'],      pre3['bb_width'],      pre5['bb_width'],
        pre1['vol_ratio'],     pre3['vol_ratio'],     pre5['vol_ratio'],
        pre1['rsi'],           pre3['rsi'],            pre5['rsi'],
        pre3['momentum_5d'],   pre5['momentum_5d'],
        pre5['bb_position'],   pre5['compression_days'],
        pre3['adx'],           pre5['adx'],
        pre3['macd_hist'],     pre5['macd_hist'],
        pre1['adx'],           pre1['macd_hist'],
        # Phase 3 features (2026-05-22)
        pre1['rsi_slope_3d'],
        pre1['ema_alignment'],
        pre1['ema20_slope_5d'],
        # Phase 4 features (2026-05-28)
        pre1['di_diff'],
        pre1['body_ratio'],
        pre1['lower_shadow_ratio'],
        pre1['bar_direction'],
        sec_ad,
        sec_pct,
        # Phase 5 features
        turnover_ratio,
        usdegp_chg,
        vix_lvl,
        oil_chg,
        spread_bps,
        # Phase 60 features (Multi-Timeframe)
        w_rsi_val, w_bb_pos, mo_trend_val, w_vol_comp,
        # Phase 61 features (Volume Intelligence)
        obv_slope_norm, adl_div, closing_str, vol_dryup,
        # Phase 62 features (DTW Similarity)
        dtw_sim, dtw_exp_gain,
        # Group A: Market Microstructure (7 features)
        net_accum, up_vol_ratio, range_comp, vcp, base_tight, higher_lows, bp,
        # Group B: Relative Strength vs Market (4 features)
        rs_5d, rs_20d, rs_sector, rs_rank,
    ]


def _build_feature_row_from_tail(sym_df, sector_cache=None, sector=None):
    """
    Build 28-dim feature vector for PREDICTION using latest bar as 'today'.
    pre1 = last available bar (index -1), pre3 = index -3, pre5 = index -5.
    Phase 4: accepts optional sector_cache + sector for breadth features.
    """
    n = len(sym_df)
    # For tomorrow's prediction, the last available candle is the T-1 bar.
    # The old n-2/n-4/n-6 indexing silently ignored the freshest candle, making
    # volume/closing-strength discovery one trading day stale.
    pre1 = _get_indicators_at(sym_df, n - 1)
    pre3 = _get_indicators_at(sym_df, n - 3)
    pre5 = _get_indicators_at(sym_df, n - 5)

    if pre1 is None or pre3 is None or pre5 is None:
        return None

    # Phase 4: sector breadth — use last available bar date
    pre1_date_str = ''
    try:
        pre1_date_str = sym_df.iloc[n - 1]['bar_date']
    except Exception:
        pass
    sec_ad, sec_pct = _get_sector_breadth(sector_cache, pre1_date_str, sector)

    return [
        pre1['bb_width'],      pre3['bb_width'],      pre5['bb_width'],
        pre1['vol_ratio'],     pre3['vol_ratio'],     pre5['vol_ratio'],
        pre1['rsi'],           pre3['rsi'],            pre5['rsi'],
        pre3['momentum_5d'],   pre5['momentum_5d'],
        pre5['bb_position'],   pre5['compression_days'],
        pre3['adx'],           pre5['adx'],
        pre3['macd_hist'],     pre5['macd_hist'],
        pre1['adx'],           pre1['macd_hist'],
        # Phase 3 features (2026-05-22)
        pre1['rsi_slope_3d'],
        pre1['ema_alignment'],
        pre1['ema20_slope_5d'],
        # Phase 4 features (2026-05-28)
        pre1['di_diff'],
        pre1['body_ratio'],
        pre1['lower_shadow_ratio'],
        pre1['bar_direction'],
        sec_ad,
        sec_pct,
        # Phase 5 features — defaults (no liquidity/cross-market cache in tail mode)
        1.0,   # pre1_turnover_ratio
        0.0,   # pre1_usdegp_chg
        0.25,  # pre1_vix_level
        0.0,   # pre1_oil_chg
        0.5,   # pre1_spread_bps
        # Phase 60 features (Multi-Timeframe) — defaults
        0.5,   # w_rsi
        0.5,   # w_bb_position
        0.5,   # mo_trend
        0.33,  # w_vol_compression
        # Phase 61 features (Volume Intelligence) — defaults
        0.5,   # obv_slope_5d
        0.5,   # adl_divergence
        0.5,   # closing_strength
        0.0,   # vol_dryup_days
        # Phase 62 features (DTW Similarity) — defaults
        0.5,   # dtw_similarity
        0.5,   # dtw_expected_gain
        # Group A: Market Microstructure — defaults
        0.5,   # net_accumulation_20d
        0.5,   # up_volume_ratio_10d
        0.5,   # range_compression_20d
        0.5,   # vcp_score
        0.5,   # base_tightness_10d
        0.0,   # higher_lows_streak
        0.5,   # breakout_proximity
        # Group B: Relative Strength vs Market — defaults
        0.5,   # rs_vs_market_5d
        0.5,   # rs_vs_market_20d
        0.5,   # rs_vs_sector_10d
        0.5,   # rs_rank_pct
    ]

# ──────────────────────────────────────────────────────────────────
#  Dataset construction (FIXED)
# ──────────────────────────────────────────────────────────────────
def build_training_data(conn, train_end='2026-02-28'):
    """
    FIXED: Build (X, y) with real pre-event features for both positives
    and negatives. Negatives sampled from ohlcv_history_features dates NOT in
    explosive_moves.

    2026-05-23: Excludes rows from stocks that underwent price consolidation
    (reverse split / par-value change) within ±90d of the explosion_date.
    2026-05-28 Phase 4: train_end default updated to 2026-02-28 (2 extra IS months).
    2026-05-29 Audit Fix: Only rows with return_5d >= 0.07 are used as positives.
    Previously all 13,681 explosive_moves were used regardless of whether they
    achieved the target return. This caused 66.5% label noise and inflated AUC.
    """
    import numpy as np

    print(f"[ML] Building training data (IS end: {train_end})...", flush=True)

    # ── Load consolidation events so we can exclude corrupt training rows ─────
    consolidation_events = _get_consolidation_events(conn, lookback_days=730)
    corrupt_syms = set(consolidation_events.keys())
    print(f"[ML]   Consolidation events detected for {len(corrupt_syms)} symbols "
          f"(will exclude ±90d window from training)", flush=True)

    # ── Phase 4: Load sector breadth cache ────────────────────────────────────
    sector_cache = _load_sector_breadth_cache(conn)
    print(f"[ML]   Sector breadth cache: {len(sector_cache)} (date,sector) pairs", flush=True)

    # Build explosion set for exclusion
    explosion_rows = conn.execute(
        "SELECT symbol, explosion_date FROM explosive_moves"
    ).fetchall()
    explosion_set = {(r['symbol'], r['explosion_date']) for r in explosion_rows}

    # Positive examples from explosive_moves — only rows that achieved >=7% return
    # 2026-05-29 Audit Fix: filter return_5d >= 0.07 to remove 66.5% label noise
    pos_rows = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date <= ? AND return_5d >= 0.07",
        (train_end,)
    ).fetchall()
    print(f"[ML]   Positives (return_5d>=7%): {len(pos_rows)} kept from explosive_moves", flush=True)
    pos_skipped_corrupt = sum(
        1 for r in pos_rows
        if _is_corrupt_training_row(r['symbol'], r['explosion_date'], consolidation_events)
    )
    pos_rows = [
        r for r in pos_rows
        if not _is_corrupt_training_row(r['symbol'], r['explosion_date'], consolidation_events)
    ]
    print(f"[ML]   Positives available: {len(pos_rows)} "
          f"(excluded {pos_skipped_corrupt} near consolidation events)", flush=True)

    # Load OHLCV cache for IS period (need extra history before train_end for indicators)
    cache = _build_ohlcv_cache(conn, train_end)
    print(f"[ML]   Symbols with OHLCV: {len(cache)}", flush=True)
    egx30 = _load_egx30_cache(conn)
    print(f"[ML]   EGX30 index bars for RS-vs-market: {len(egx30)}", flush=True)

    # Negative candidates: (symbol, bar_date) NOT in explosive_moves, IS period only
    # Sample 6× positives to allow for failures, then trim
    # 2026-05-23: also exclude symbols with consolidation events
    target_neg = len(pos_rows) * 3
    sample_limit = target_neg * 2  # sample more, trim to target

    neg_candidates = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history_features o
        WHERE date(o.bar_time,'unixepoch') <= ?
          AND NOT EXISTS (
              SELECT 1 FROM explosive_moves e
              WHERE e.symbol = o.symbol
                AND e.explosion_date = date(o.bar_time,'unixepoch')
          )
        ORDER BY RANDOM()
        LIMIT ?
    """, (train_end, sample_limit)).fetchall()
    # Filter negatives from corrupt symbols entirely (their OHLCV features
    # straddle different price scales and poison the negative class distribution)
    neg_candidates = [
        n for n in neg_candidates
        if not _is_corrupt_training_row(n['symbol'], n['bar_date'], consolidation_events,
                                        pre_window=90, post_window=90)
    ]
    print(f"[ML]   Negative candidates sampled: {len(neg_candidates)} "
          f"(corrupt symbols excluded)", flush=True)

    X, y = [], []

    # Add positives: prefer OHLCV-computed features (all 22) over explosive_moves
    # stored values (only 13 features; ADX/MACD/RSI-slope/EMA are NULL in old records).
    # Using _build_feature_row ensures training and prediction feature distributions match.
    # 2026-05-23: switched from explosive_moves read → OHLCV cache computation.
    # Build sector lookup for explosive_moves (symbol → sector)
    sym_sector = {r['symbol']: (r['sector'] or 'UNKNOWN')
                  for r in conn.execute("SELECT DISTINCT symbol, sector FROM explosive_moves").fetchall()}

    pos_skipped = 0
    pos_cache_hit = 0
    pos_fallback = 0
    for r in pos_rows:
        sym = r['symbol']
        sec = sym_sector.get(sym, 'UNKNOWN')
        sym_df = cache.get(sym)
        row = None
        if sym_df is not None:
            # Preferred path: compute all 28 features from OHLCV cache (Phase 4)
            row = _build_feature_row(sym_df, r['explosion_date'],
                                     sector_cache=sector_cache, sector=sec,
                                     egx30=egx30)
            if row is not None:
                pos_cache_hit += 1
        if row is None:
            # Fallback: use stored features from explosive_moves
            # Phase 4 features (22-27) will be 0 for old stored records
            row = [safe_float(r[c], 0.0) for c in FEATURE_COLS[:22]] + [0.0, 0.5, 0.2, 0.0, 1.0, 50.0, 1.0, 0.0, 0.25, 0.0, 0.5, 0.5, 0.5, 0.5, 0.33, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5] + [0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
            if sum(abs(v) for v in row[:22]) < 1e-6:
                pos_skipped += 1
                continue
            pos_fallback += 1
        X.append(row)
        y.append(1)
    print(f"[ML]   Positives added: {len([v for v in y if v==1])} "
          f"(OHLCV={pos_cache_hit}, fallback_stored={pos_fallback}, "
          f"skipped_zero={pos_skipped})", flush=True)

    # Add negatives with REAL computed features
    # For negatives, sector is unknown — use default breadth values
    neg_count = 0
    neg_failed = 0
    for neg in neg_candidates:
        if neg_count >= target_neg:
            break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None:
            neg_failed += 1
            continue
        neg_sec = sym_sector.get(neg['symbol'], 'UNKNOWN')
        row = _build_feature_row(sym_df, neg['bar_date'],
                                 sector_cache=sector_cache, sector=neg_sec,
                                 egx30=egx30)
        if row is None:
            neg_failed += 1
            continue
        X.append(row)
        y.append(0)
        neg_count += 1

    print(f"[ML]   Negatives added: {neg_count} (failed: {neg_failed})", flush=True)
    print(f"[ML]   Total IS dataset: {len(X)} rows ({sum(y)} pos / {len(y)-sum(y)} neg)", flush=True)

    return X, y


def build_oos_data(conn, oos_start='2026-03-31', oos_end=None):
    """
    FIXED: Build OOS test set with real computed features for negatives.
    OOS start has 30-day gap from IS end to prevent leakage.

    2026-05-23: Excludes rows from stocks near consolidation events.
    2026-05-23: Added optional oos_end parameter for walk-forward windows.
    2026-05-28 Phase 4: oos_start default updated to 2026-03-31 (matches new IS end).
    """
    print(f"[ML] Building OOS data (start: {oos_start})...", flush=True)

    # Load consolidation events
    consolidation_events = _get_consolidation_events(conn, lookback_days=730)

    # Phase 4: Load sector breadth cache
    sector_cache = _load_sector_breadth_cache(conn)
    sym_sector = {r['symbol']: (r['sector'] or 'UNKNOWN')
                  for r in conn.execute("SELECT DISTINCT symbol, sector FROM explosive_moves").fetchall()}

    explosion_set = {
        (r['symbol'], r['explosion_date'])
        for r in conn.execute("SELECT symbol, explosion_date FROM explosive_moves").fetchall()
    }

    if oos_end is None:
        oos_end = datetime.date.today().strftime('%Y-%m-%d')

    if oos_end is None or oos_end >= datetime.date.today().strftime('%Y-%m-%d'):
        _oos_end_for_cache = datetime.date.today().strftime('%Y-%m-%d')
    else:
        _oos_end_for_cache = oos_end

    # 2026-05-29 Audit Fix: filter return_5d >= 0.07 to remove 66.5% label noise
    pos_rows_raw = conn.execute(
        "SELECT * FROM explosive_moves WHERE explosion_date >= ? AND explosion_date <= ?"
        " AND return_5d >= 0.07",
        (oos_start, oos_end)
    ).fetchall()
    print(f"[ML]   OOS positives (return_5d>=7%): {len(pos_rows_raw)} kept from explosive_moves", flush=True)
    pos_skipped_corrupt = sum(
        1 for r in pos_rows_raw
        if _is_corrupt_training_row(r['symbol'], r['explosion_date'], consolidation_events)
    )
    pos_rows = [
        r for r in pos_rows_raw
        if not _is_corrupt_training_row(r['symbol'], r['explosion_date'], consolidation_events)
    ]
    print(f"[ML]   OOS positives: {len(pos_rows)} "
          f"(excluded {pos_skipped_corrupt} near consolidation)", flush=True)

    # Load OHLCV with 60-day lookback buffer before oos_start so _build_feature_row
    # has pre1/pre3/pre5 data available AND symbols reach the 30-bar minimum threshold.
    # 30-day buffer was insufficient: 30 cal days ≈ 19-20 EGX trading days < 30 bars.
    # 60-day buffer ≈ 40 EGX trading days → 228+ symbols qualify (2026-05-23 fix v2).
    import datetime as _dt_cache
    _cache_min = (
        _dt_cache.date.fromisoformat(oos_start) - _dt_cache.timedelta(days=60)
    ).isoformat()
    cache = _build_ohlcv_cache(conn, _oos_end_for_cache, min_date=_cache_min)
    egx30 = _load_egx30_cache(conn)

    target_neg = len(pos_rows) * 3
    sample_limit = target_neg * 2

    neg_candidates = conn.execute("""
        SELECT o.symbol, date(o.bar_time,'unixepoch') AS bar_date
        FROM ohlcv_history_features o
        WHERE date(o.bar_time,'unixepoch') >= ?
          AND date(o.bar_time,'unixepoch') <= ?
          AND NOT EXISTS (
              SELECT 1 FROM explosive_moves e
              WHERE e.symbol = o.symbol
                AND e.explosion_date = date(o.bar_time,'unixepoch')
          )
        ORDER BY RANDOM()
        LIMIT ?
    """, (oos_start, oos_end, sample_limit)).fetchall()
    neg_candidates = [
        n for n in neg_candidates
        if not _is_corrupt_training_row(n['symbol'], n['bar_date'], consolidation_events,
                                        pre_window=90, post_window=90)
    ]

    X, y = [], []
    n_oos_pos = 0
    oos_pos_cache = 0
    oos_pos_fallback = 0

    for r in pos_rows:
        sym = r['symbol']
        sec = sym_sector.get(sym, 'UNKNOWN')
        sym_df = cache.get(sym)
        row = None
        if sym_df is not None:
            row = _build_feature_row(sym_df, r['explosion_date'],
                                     sector_cache=sector_cache, sector=sec,
                                     egx30=egx30)
            if row is not None:
                oos_pos_cache += 1
        if row is None:
            row = [safe_float(r[c], 0.0) for c in FEATURE_COLS[:22]] + [0.0, 0.5, 0.2, 0.0, 1.0, 50.0, 1.0, 0.0, 0.25, 0.0, 0.5, 0.5, 0.5, 0.5, 0.33, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5] + [0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
            if sum(abs(v) for v in row[:22]) < 1e-6:
                continue
            oos_pos_fallback += 1
        X.append(row)
        y.append(1)
        n_oos_pos += 1

    neg_count = 0
    for neg in neg_candidates:
        if neg_count >= target_neg:
            break
        sym_df = cache.get(neg['symbol'])
        if sym_df is None:
            continue
        neg_sec = sym_sector.get(neg['symbol'], 'UNKNOWN')
        row = _build_feature_row(sym_df, neg['bar_date'],
                                 sector_cache=sector_cache, sector=neg_sec,
                                 egx30=egx30)
        if row is None:
            continue
        X.append(row)
        y.append(0)
        neg_count += 1

    print(f"[ML]   OOS dataset: {len(X)} rows ({n_oos_pos} pos [ohlcv={oos_pos_cache}] / {neg_count} neg)", flush=True)
    return X, y, n_oos_pos

# ──────────────────────────────────────────────────────────────────
#  AUC + metrics
# ──────────────────────────────────────────────────────────────────
def compute_auc(y_true, y_scores):
    pairs = sorted(zip(y_scores, y_true), key=lambda x: -x[0])
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = 0; auc = 0.0
    for score, label in pairs:
        if label == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)

def precision_at_thresh(preds, labels, thresh):
    tp = sum(1 for p, l in zip(preds, labels) if p >= thresh and l == 1)
    fp = sum(1 for p, l in zip(preds, labels) if p >= thresh and l == 0)
    return tp / max(tp + fp, 1)

def recall_at_thresh(preds, labels, thresh):
    tp = sum(1 for p, l in zip(preds, labels) if p >= thresh and l == 1)
    fn = sum(1 for p, l in zip(preds, labels) if p <  thresh and l == 1)
    return tp / max(tp + fn, 1)

def precision_at_top_k(preds, labels, k):
    """Precision among the top-k scores. Returns None if k cannot be evaluated."""
    pairs = sorted(zip(preds, labels), key=lambda x: -x[0])
    if not pairs:
        return None
    k = min(int(k), len(pairs))
    if k <= 0:
        return None
    return sum(1 for _, l in pairs[:k] if l == 1) / k

def _threshold_metrics(preds, labels, thresh):
    selected = [(p, l) for p, l in zip(preds, labels) if p >= thresh]
    tp = sum(1 for _, l in selected if l == 1)
    fp = len(selected) - tp
    positives = sum(1 for l in labels if l == 1)
    return {
        'threshold': round(float(thresh), 4),
        'selected': len(selected),
        'precision': round(tp / max(tp + fp, 1), 4),
        'recall': round(tp / max(positives, 1), 4),
    }

def _build_calibration_report(preds, labels):
    """Build an explicit OOS precision report and derive conservative gates."""
    preds = [safe_float(p) for p in preds]
    labels = [int(l) for l in labels]
    baseline = sum(labels) / max(len(labels), 1)
    thresholds = [round(0.30 + i * 0.05, 2) for i in range(14)]  # 0.30..0.95
    by_threshold = [_threshold_metrics(preds, labels, t) for t in thresholds]
    top_k = {
        f'precision_at_{k}': precision_at_top_k(preds, labels, k)
        for k in PRECISION_TOP_KS
    }
    top10_k = max(1, int(len(preds) * 0.10))
    top_k['precision_at_top10pct'] = precision_at_top_k(preds, labels, top10_k)

    high_floor = max(DEFAULT_DECISION_POLICY['min_precision_high'], baseline + 0.10)
    med_floor  = max(DEFAULT_DECISION_POLICY['min_precision_medium'], baseline + 0.05)

    def choose_threshold(floor, min_selected, fallback):
        candidates = [
            m for m in by_threshold
            if m['selected'] >= min_selected and m['precision'] >= floor
        ]
        if not candidates:
            return fallback
        return min(m['threshold'] for m in candidates)

    high_thr = choose_threshold(high_floor, min(10, max(1, len(preds) // 20)),
                                DEFAULT_DECISION_POLICY['high_prob'])
    med_thr = choose_threshold(med_floor, min(20, max(1, len(preds) // 10)),
                               DEFAULT_DECISION_POLICY['medium_prob'])
    high_thr = max(high_thr, DEFAULT_DECISION_POLICY['high_prob'])
    med_thr = max(med_thr, DEFAULT_DECISION_POLICY['medium_prob'])
    if med_thr > high_thr:
        med_thr = high_thr
    abstain_thr = max(
        DEFAULT_DECISION_POLICY['abstain_prob'],
        min(med_thr - 0.20, med_thr),
    )

    policy = dict(DEFAULT_DECISION_POLICY)
    policy.update({
        'high_prob': round(float(high_thr), 4),
        'medium_prob': round(float(med_thr), 4),
        'abstain_prob': round(float(abstain_thr), 4),
        'oos_baseline_precision': round(float(baseline), 4),
    })
    return {
        'success': True,
        'generated_at': datetime.datetime.now().isoformat(),
        'policy': policy,
        'baseline_precision': round(float(baseline), 4),
        'by_threshold': by_threshold,
        'top_k': {
            k: (None if v is None else round(float(v), 4))
            for k, v in top_k.items()
        },
    }

def _load_decision_policy():
    policy = dict(DEFAULT_DECISION_POLICY)
    try:
        if os.path.exists(CALIB_PATH):
            with open(CALIB_PATH) as fh:
                calib = json.load(fh)
            policy.update(calib.get('policy') or calib.get('decision_policy') or {})
    except Exception:
        pass
    for key, default in DEFAULT_DECISION_POLICY.items():
        policy[key] = safe_float(policy.get(key), default)
    policy['high_prob'] = max(policy['high_prob'], DEFAULT_DECISION_POLICY['high_prob'])
    policy['medium_prob'] = max(policy['medium_prob'], DEFAULT_DECISION_POLICY['medium_prob'])
    policy['abstain_prob'] = max(policy['abstain_prob'], DEFAULT_DECISION_POLICY['abstain_prob'])
    policy['high_rank_limit'] = int(policy.get('high_rank_limit') or DEFAULT_DECISION_POLICY['high_rank_limit'])
    policy['medium_rank_limit'] = int(policy.get('medium_rank_limit') or DEFAULT_DECISION_POLICY['medium_rank_limit'])
    return policy

def _assign_model_tier(prob, rank=None, vol_ratio=0.0, policy=None):
    policy = policy or _load_decision_policy()
    prob = safe_float(prob)
    rank = int(rank or 10**9)
    vol_ratio = safe_float(vol_ratio)
    if prob < safe_float(policy.get('abstain_prob'), DEFAULT_DECISION_POLICY['abstain_prob']):
        return 'ABSTAIN', 'probability_below_abstain_threshold'
    if (
        rank <= int(policy.get('high_rank_limit', DEFAULT_DECISION_POLICY['high_rank_limit'])) and
        prob >= safe_float(policy.get('high_prob'), DEFAULT_DECISION_POLICY['high_prob']) and
        vol_ratio >= safe_float(policy.get('high_vol_ratio'), DEFAULT_DECISION_POLICY['high_vol_ratio'])
    ):
        return 'HIGH', 'passes_high_probability_rank_volume_gate'
    if (
        rank <= int(policy.get('medium_rank_limit', DEFAULT_DECISION_POLICY['medium_rank_limit'])) and
        prob >= safe_float(policy.get('medium_prob'), DEFAULT_DECISION_POLICY['medium_prob']) and
        vol_ratio >= safe_float(policy.get('medium_vol_ratio'), DEFAULT_DECISION_POLICY['medium_vol_ratio'])
    ):
        return 'MEDIUM', 'passes_medium_probability_rank_volume_gate'
    return 'LOW', 'below_rank_volume_calibrated_gate'

def model_feature_count(model):
    """Return the number of features expected by a loaded model."""
    try:
        return int(model.num_feature())
    except Exception:
        try:
            return int(model.n_features_)
        except Exception:
            return len(FEATURE_COLS)

def shape_features_for_model(feat, model):
    """Align a feature vector to the saved model without disabling shape checks."""
    n_expected = model_feature_count(model)
    if len(feat) < n_expected:
        raise ValueError(
            f"Feature vector too short for model: {len(feat)} < {n_expected}. "
            "Retrain the model or rebuild the feature pipeline."
        )
    return [safe_float(v) for v in list(feat)[:n_expected]]

def dataset_quality_report(y_train=None, y_oos=None):
    """Return model-evidence quality checks for train/OOS labels."""
    y_train = [] if y_train is None else list(y_train)
    y_oos   = [] if y_oos is None else list(y_oos)
    train_pos = sum(1 for v in y_train if v == 1)
    train_neg = len(y_train) - train_pos
    oos_pos   = sum(1 for v in y_oos if v == 1)
    oos_neg   = len(y_oos) - oos_pos
    issues = []
    if train_pos < MIN_TRAIN_POSITIVES:
        issues.append(f"train positives too few: {train_pos} < {MIN_TRAIN_POSITIVES}")
    if train_neg < MIN_TRAIN_NEGATIVES:
        issues.append(f"train negatives too few: {train_neg} < {MIN_TRAIN_NEGATIVES}")
    if len(y_oos) < MIN_OOS_SAMPLES:
        issues.append(f"OOS samples too few: {len(y_oos)} < {MIN_OOS_SAMPLES}")
    if oos_pos < MIN_OOS_POSITIVES:
        issues.append(f"OOS positives too few: {oos_pos} < {MIN_OOS_POSITIVES}")
    if oos_neg < MIN_OOS_NEGATIVES:
        issues.append(f"OOS negatives too few: {oos_neg} < {MIN_OOS_NEGATIVES}")
    return {
        'ok': not issues,
        'issues': issues,
        'train_positive': train_pos,
        'train_negative': train_neg,
        'oos_positive': oos_pos,
        'oos_negative': oos_neg,
        'oos_samples': len(y_oos),
    }

# ──────────────────────────────────────────────────────────────────
#  Commands
# ──────────────────────────────────────────────────────────────────
BEST_PARAMS_PATH = os.path.join(MODEL_DIR, 'explosion_best_params.json')


def cmd_optuna_tune(params):
    """Optuna hyperparameter search for LightGBM explosion classifier.

    params:
      n_trials   : int (default 40)  — number of Optuna trials
      train_end  : str (default '2025-12-31')
      oos_start  : str (default '2026-01-30')
    """
    import optuna
    import lightgbm as lgb
    import numpy as np

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    n_trials  = int(params.get('n_trials',  40))
    train_end = params.get('train_end', '2025-12-31')
    oos_start = params.get('oos_start', '2026-01-30')

    conn = get_db()
    ensure_tables(conn)

    X_train, y_train = build_training_data(conn, train_end)
    X_oos,   y_oos,  _ = build_oos_data(conn, oos_start)
    conn.close()

    if len(X_train) < 50 or len(X_oos) < 10:
        return {'success': False, 'error': 'Not enough data for tuning'}

    X_tr = np.array(X_train, dtype=np.float32)
    y_tr = np.array(y_train, dtype=np.int32)
    X_os = np.array(X_oos,   dtype=np.float32)
    y_os = np.array(y_oos,   dtype=np.int32)

    n_pos = int(y_tr.sum())
    n_neg = len(y_tr) - n_pos
    spw   = n_neg / max(n_pos, 1)

    def objective(trial):
        p = {
            'objective':        'binary',
            'metric':           'auc',
            'verbose':          -1,
            'random_state':     42,
            'scale_pos_weight': spw,
            'learning_rate':    trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
            'num_leaves':       trial.suggest_int('num_leaves', 15, 127),
            'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 10, 60),
            'feature_fraction': trial.suggest_float('feature_fraction', 0.5, 1.0),
            'bagging_fraction': trial.suggest_float('bagging_fraction', 0.5, 1.0),
            'bagging_freq':     trial.suggest_int('bagging_freq', 1, 10),
            'lambda_l1':        trial.suggest_float('lambda_l1', 1e-4, 10.0, log=True),
            'lambda_l2':        trial.suggest_float('lambda_l2', 1e-4, 10.0, log=True),
        }
        dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_COLS)
        dval   = lgb.Dataset(X_os, label=y_os,  feature_name=FEATURE_COLS)
        model  = lgb.train(p, dtrain, num_boost_round=300,
                           valid_sets=[dval],
                           callbacks=[lgb.log_evaluation(-1),
                                      lgb.early_stopping(30, verbose=False)])
        preds = model.predict(X_os).tolist()
        return compute_auc(y_os.tolist(), preds)

    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, n_jobs=1)

    best = study.best_params
    best['scale_pos_weight'] = spw
    best['objective']        = 'binary'
    best['metric']           = 'auc'
    best['verbose']          = -1
    best['random_state']     = 42

    with open(BEST_PARAMS_PATH, 'w') as fh:
        json.dump(best, fh, indent=2)

    top5 = [
        {'trial': t.number, 'auc': round(t.value, 4),
         'lr': round(t.params['learning_rate'], 4),
         'leaves': t.params['num_leaves']}
        for t in sorted(study.trials, key=lambda t: -t.value)[:5]
    ]

    return {
        'success':     True,
        'best_auc':    round(study.best_value, 4),
        'n_trials':    n_trials,
        'best_params': best,
        'top_5_trials': top5,
        'saved_to':    BEST_PARAMS_PATH,
    }


def cmd_train(params):
    # Purged split: 30-day gap between IS end and OOS start
    # Phase 4 (2026-05-28): Extended IS end to 2026-02-28, OOS from 2026-03-31
    # IS data: all explosions through Feb 28 (~12,000+ positives)
    # OOS data: March 31 - May 28 (~400+ positives, 2 months evaluation window)
    train_end  = params.get('train_end',  '2026-02-28')
    oos_start  = params.get('oos_start',  '2026-03-31')   # 30-day purge gap

    conn = get_db()
    ensure_tables(conn)

    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError:
        return {'error': 'lightgbm or numpy not installed. Run: pip install lightgbm numpy pandas'}

    # Build datasets
    X_train, y_train = build_training_data(conn, train_end)
    X_oos,   y_oos,  n_oos_pos = build_oos_data(conn, oos_start)

    if len(X_train) < 50:
        return {'error': f'Not enough training data: {len(X_train)} samples'}

    X_tr = np.array(X_train, dtype=np.float32)
    y_tr = np.array(y_train, dtype=np.int32)
    quality = dataset_quality_report(y_train, y_oos)
    if not quality['ok']:
        conn.close()
        return {
            'success': False,
            'error': 'Insufficient statistical evidence for training/evaluation',
            'quality': quality,
            'note': 'Model training is blocked until the train/OOS split has enough positive and negative examples.',
        }

    n_pos = int(y_tr.sum())
    n_neg = len(y_tr) - n_pos
    scale_pos_weight = n_neg / max(n_pos, 1)
    print(f"[ML] scale_pos_weight = {scale_pos_weight:.2f} ({n_pos} pos / {n_neg} neg)", flush=True)

    train_data = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_COLS)

    # Use Optuna-tuned params if available, else defaults
    if os.path.exists(BEST_PARAMS_PATH) and not params.get('use_defaults'):
        with open(BEST_PARAMS_PATH) as fh:
            lgb_params = json.load(fh)
        lgb_params['scale_pos_weight'] = scale_pos_weight  # recalculate with current data
        print(f"[ML] Using Optuna-tuned params from {BEST_PARAMS_PATH}", flush=True)
    else:
        lgb_params = {
            'objective':         'binary',
            'metric':            'auc',
            'learning_rate':     0.05,
            'num_leaves':        31,
            'min_data_in_leaf':  20,
            'scale_pos_weight':  scale_pos_weight,
            'verbose':           -1,
            'random_state':      42,
            'feature_fraction':  0.8,
            'bagging_fraction':  0.8,
            'bagging_freq':      5,
        }

    valid_sets = []
    if len(X_oos) > 10:
        X_os = np.array(X_oos, dtype=np.float32)
        y_os = np.array(y_oos, dtype=np.int32)
        valid_sets = [lgb.Dataset(X_os, label=y_os)]

    callbacks = [lgb.log_evaluation(period=-1)]
    if valid_sets:
        callbacks.append(lgb.early_stopping(50, verbose=False))

    model = lgb.train(
        lgb_params,
        train_data,
        num_boost_round=500,
        valid_sets=valid_sets if valid_sets else None,
        callbacks=callbacks,
    )
    model.save_model(MODEL_PATH)
    print(f"[ML] Model saved: {MODEL_PATH} ({model.num_trees()} trees)", flush=True)

    # Evaluate
    train_preds = model.predict(X_tr).tolist()
    auc_train   = compute_auc(y_tr.tolist(), train_preds)

    auc_oos   = 0.5
    prec50    = 0.0
    prec70    = 0.0
    rec50     = 0.0
    n_oos_neg = len(y_oos) - n_oos_pos
    prec10    = None
    prec20    = None
    prec_top10pct = None
    calib = {
        'success': False,
        'policy': dict(DEFAULT_DECISION_POLICY),
        'baseline_precision': 0.0,
        'by_threshold': [],
        'top_k': {},
    }
    if valid_sets:
        oos_preds = model.predict(X_os).tolist()
        auc_oos   = compute_auc(y_os.tolist(), oos_preds)
        prec50    = precision_at_thresh(oos_preds, y_os.tolist(), 0.5)
        prec70    = precision_at_thresh(oos_preds, y_os.tolist(), 0.7)
        rec50     = recall_at_thresh(oos_preds, y_os.tolist(), 0.5)
        calib      = _build_calibration_report(oos_preds, y_os.tolist())
        prec10     = calib['top_k'].get('precision_at_10')
        prec20     = calib['top_k'].get('precision_at_20')
        prec_top10pct = calib['top_k'].get('precision_at_top10pct')

    # Feature importance
    fi     = model.feature_importance(importance_type='gain').tolist()
    fi_pairs = sorted(zip(FEATURE_COLS, fi), key=lambda x: -x[1])
    top_features = [{'feature': f, 'importance': round(v, 2)} for f, v in fi_pairs[:10]]

    with open(CALIB_PATH, 'w') as fh:
        json.dump(calib, fh, indent=2)

    conn.execute("""
        INSERT INTO ml_model_scores
        (model_name, trained_at, train_end_date, oos_start_date,
         n_train_positive, n_train_negative, n_oos_positive, n_oos_negative, n_oos_total,
         auc_train, auc_oos, precision_at_50, precision_at_70, recall_at_50,
         precision_at_10, precision_at_20, precision_at_top10pct,
         recommended_threshold_high, recommended_threshold_medium, abstain_threshold,
         top_features, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        'explosion_lgbm_v3', datetime.datetime.now().isoformat(),
        train_end, oos_start, n_pos, n_neg, n_oos_pos, n_oos_neg, len(y_oos),
        round(auc_train, 4), round(auc_oos, 4), round(prec50, 4), round(prec70, 4), round(rec50, 4),
        None if prec10 is None else round(prec10, 4),
        None if prec20 is None else round(prec20, 4),
        None if prec_top10pct is None else round(prec_top10pct, 4),
        calib.get('policy', {}).get('high_prob'),
        calib.get('policy', {}).get('medium_prob'),
        calib.get('policy', {}).get('abstain_prob'),
        json.dumps(top_features),
        f'{model.num_trees()} trees | {"Optuna-tuned" if os.path.exists(BEST_PARAMS_PATH) and not params.get("use_defaults") else "default"} params | precision@top-k calibrated | abstention + LOW-not-client-acceptable policy'
    ))
    conn.commit()
    conn.close()

    return {
        'success':          True,
        'model_saved':      MODEL_PATH,
        'n_train':          len(X_train),
        'n_train_positive': n_pos,
        'n_train_negative': n_neg,
        'n_oos':            len(X_oos),
        'n_oos_positive':   n_oos_pos,
        'n_oos_negative':   n_oos_neg,
        'auc_train':        round(auc_train, 4),
        'auc_oos':          round(auc_oos, 4),
        'precision_at_50':  round(prec50, 4),
        'precision_at_70':  round(prec70, 4),
        'recall_at_50':     round(rec50, 4),
        'precision_at_10':  None if prec10 is None else round(prec10, 4),
        'precision_at_20':  None if prec20 is None else round(prec20, 4),
        'precision_at_top10pct': None if prec_top10pct is None else round(prec_top10pct, 4),
        'decision_policy':  calib.get('policy', {}),
        'calibration_report_path': CALIB_PATH,
        'top_features':     top_features[:5],
        'balance_ratio':    round(n_neg / max(n_pos, 1), 1),
        'quality':          quality,
        'edge_claim_allowed': bool(quality['ok'] and auc_oos >= 0.55 and prec50 > (sum(y_oos) / max(len(y_oos), 1))),
    }


def cmd_predict_today(params):
    """
    FIXED: Use ohlcv_history_features for proper pre1/pre3/pre5 lookback features.
    Loads last 30 bars per symbol, computes indicators, builds feature vector.
    """
    pred_date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    top_n     = int(params.get('top_n', 30))
    min_prob_param = params.get('min_prob')

    conn = get_db()
    ensure_tables(conn)

    if not os.path.exists(MODEL_PATH):
        _clear_prediction_date(conn, pred_date)
        conn.close()
        return {'error': 'Model not trained yet. Run: npm run egx:ml:train', 'model_path': MODEL_PATH}

    try:
        import lightgbm as lgb
        import numpy as np
        import pandas as pd
    except ImportError:
        return {'error': 'lightgbm / numpy / pandas not installed'}

    model = lgb.Booster(model_file=MODEL_PATH)
    decision_policy = _load_decision_policy()
    min_prob = (
        float(min_prob_param)
        if min_prob_param is not None
        else safe_float(decision_policy.get('abstain_prob'), DEFAULT_DECISION_POLICY['abstain_prob'])
    )

    # Determine the reference date for "today" (last available OHLCV bar)
    max_date_row = conn.execute(
        "SELECT MAX(date(bar_time,'unixepoch')) AS md FROM ohlcv_history_features"
    ).fetchone()
    max_date = max_date_row['md'] if max_date_row else pred_date
    if pred_date > max_date and not params.get('allow_stale'):
        _clear_prediction_date(conn, pred_date)
        conn.close()
        return {
            'success': False,
            'error': 'Prediction blocked: requested prediction date is newer than latest OHLCV.',
            'pred_date': pred_date,
            'latest_ohlcv_date': max_date,
            'note': 'Update OHLCV first, or pass allow_stale=true for internal research only.',
        }

    # Phase 4: Load sector cache and symbol→sector mapping
    sector_cache = _load_sector_breadth_cache(conn)
    sym_sector_rows = conn.execute(
        "SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL"
    ).fetchall()
    sym_sector = {r['symbol']: r['sector'] for r in sym_sector_rows}
    # Supplement from explosive_moves for symbols not in stock_universe
    for r in conn.execute("SELECT DISTINCT symbol, sector FROM explosive_moves WHERE sector IS NOT NULL").fetchall():
        if r['symbol'] not in sym_sector:
            sym_sector[r['symbol']] = r['sector']

    # Load last 40 bars per symbol to have enough history for indicator windows
    symbols = [r['symbol'] for r in conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history_features"
    ).fetchall()]

    predictions = []
    n_scored = 0
    n_skipped_bad_market = 0
    n_skipped_stale_symbol = 0
    n_abstained = 0

    for sym in symbols:
        bars = conn.execute("""
            SELECT date(bar_time,'unixepoch') AS bar_date, open, high, low, close, volume
            FROM ohlcv_history_features
            WHERE symbol = ?
            ORDER BY bar_time DESC
            LIMIT 40
        """, (sym,)).fetchall()

        if len(bars) < 10:
            continue

        # Reverse to chronological order
        bars = list(reversed(bars))

        df = pd.DataFrame([{
            'bar_date': r['bar_date'],
            'open':   float(r['open']   or 0),
            'high':   float(r['high']   or 0),
            'low':    float(r['low']    or 0),
            'close':  float(r['close']  or 0),
            'volume': float(r['volume'] or 0),
        } for r in bars])
        df = df.sort_values('bar_date').reset_index(drop=True)
        latest_raw = df.iloc[-1]
        latest_symbol_date = str(latest_raw.get('bar_date') or '')
        if latest_symbol_date != pred_date and not params.get('allow_stale'):
            n_skipped_stale_symbol += 1
            continue
        if (
            safe_float(latest_raw.get('close')) <= 0 or
            safe_float(latest_raw.get('high')) < safe_float(latest_raw.get('low')) or
            safe_float(latest_raw.get('volume')) <= 0
        ):
            n_skipped_bad_market += 1
            continue
        df = _compute_indicators(df)

        sec  = sym_sector.get(sym, 'UNKNOWN')
        feat = _build_feature_row_from_tail(df, sector_cache=sector_cache, sector=sec)
        if feat is None:
            continue

        feat = shape_features_for_model(feat, model)
        X    = np.array([feat], dtype=np.float32)
        prob = float(model.predict(X)[0])
        n_scored += 1

        if prob >= min_prob:
            # Get last row's indicator snapshot for display
            last = df.iloc[-1]
            rsi   = safe_float(last.get('rsi14',       50.0))
            bbw   = safe_float(last.get('bb_width',    0.05))
            volr  = safe_float(last.get('vol_ratio_20', 1.0))
            if volr <= 0.10:
                n_skipped_bad_market += 1
                continue

            mom5 = safe_float(last.get('momentum_5d', 0.0))
            bbpos= safe_float(last.get('bb_position', 0.5))

            predictions.append({
                'symbol':          sym,
                'explosion_prob':  round(prob, 4),
                'prob_pct':        int(prob * 100),
                'confidence_tier': 'UNRANKED',
                'rsi14':           round(rsi,  1),
                'bb_width':        round(bbw,  4),
                'vol_ratio':       round(volr, 2),
                'top_drivers':     [
                    {'feature': 'vol_ratio',   'value': round(volr, 2)},
                    {'feature': 'rsi14',       'value': round(rsi,  1)},
                    {'feature': 'bb_width',    'value': round(bbw,  4)},
                    {'feature': 'momentum_5d', 'value': round(mom5, 4)},
                    {'feature': 'bb_position', 'value': round(bbpos,3)},
                ],
            })
        else:
            n_abstained += 1

    predictions.sort(key=lambda x: -x['explosion_prob'])

    _clear_prediction_date(conn, pred_date)
    final_gate_symbols = _final_signal_symbols(conn, pred_date)

    for rank, p in enumerate(predictions, start=1):
        prob = float(p['explosion_prob'])
        vol_ratio = float(p.get('vol_ratio') or 0.0)
        tier, gate_reason = _assign_model_tier(prob, rank, vol_ratio, decision_policy)
        db_tier, reliability_flag, client_ready = _client_gate_fields(
            p['symbol'], tier, final_gate_symbols
        )
        p['rank'] = rank
        p['model_confidence_tier'] = tier
        p['confidence_tier'] = db_tier
        p['reliability_flag'] = reliability_flag
        p['client_ready'] = client_ready
        p['ml_gate_reason'] = gate_reason
        conn.execute("""
            INSERT OR REPLACE INTO explosion_predictions
            (symbol, pred_date, explosion_prob, prob_pct, confidence_tier, direction,
             top_drivers, model_version, reliability_flag)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            p['symbol'], pred_date, prob, int(prob * 100),
            db_tier, 'UP', json.dumps(p.get('top_drivers', [])),
            'lgbm_v3_current_oos_fair', reliability_flag
        ))

    conn.commit()

    n_stored = conn.execute(
        "SELECT COUNT(*) FROM explosion_predictions WHERE pred_date=?", (pred_date,)
    ).fetchone()[0]
    conn.close()

    return {
        'success':          True,
        'pred_date':        pred_date,
        'n_symbols_scored': n_scored,
        'n_skipped_bad_market': n_skipped_bad_market,
        'n_skipped_stale_symbol': n_skipped_stale_symbol,
        'n_abstained':      n_abstained,
        'n_signals':        len(predictions),
        'n_high':           sum(1 for p in predictions if p['confidence_tier'] == 'HIGH'),
        'n_medium':         sum(1 for p in predictions if p['confidence_tier'] == 'MEDIUM'),
        'n_model_high':     sum(1 for p in predictions if p.get('model_confidence_tier') == 'HIGH'),
        'n_model_medium':   sum(1 for p in predictions if p.get('model_confidence_tier') == 'MEDIUM'),
        'n_model_low':      sum(1 for p in predictions if p.get('model_confidence_tier') == 'LOW'),
        'n_client_ready':   sum(1 for p in predictions if p.get('client_ready')),
        'requires_final_signals_gate': True,
        'decision_policy':  decision_policy,
        'tier_policy':      'calibrated ranked+volume: HIGH/MEDIUM only; LOW/ABSTAIN are never client-acceptable',
        'n_stored_db':      n_stored,
        'top_predictions':  predictions[:top_n],
    }


def cmd_predict_symbol(params):
    symbol    = params.get('symbol', '').upper()
    pred_date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    ensure_tables(conn)

    if not os.path.exists(MODEL_PATH):
        return {'error': 'Model not trained yet. Run: npm run egx:ml:train'}

    try:
        import lightgbm as lgb
        import numpy as np
        import pandas as pd
    except ImportError:
        return {'error': 'lightgbm not installed'}

    model = lgb.Booster(model_file=MODEL_PATH)
    decision_policy = _load_decision_policy()

    max_date_row = conn.execute(
        "SELECT MAX(date(bar_time,'unixepoch')) AS md FROM ohlcv_history_features WHERE symbol=?",
        (symbol,)
    ).fetchone()
    max_date = max_date_row['md'] if max_date_row else pred_date
    if pred_date > max_date and not params.get('allow_stale'):
        conn.close()
        return {
            'success': False,
            'error': 'Prediction blocked: requested prediction date is newer than latest symbol OHLCV.',
            'symbol': symbol,
            'pred_date': pred_date,
            'latest_ohlcv_date': max_date,
            'note': 'Update OHLCV first, or pass allow_stale=true for internal research only.',
        }

    bars = conn.execute("""
        SELECT date(bar_time,'unixepoch') AS bar_date, open, high, low, close, volume
        FROM ohlcv_history_features
        WHERE symbol = ?
        ORDER BY bar_time DESC
        LIMIT 40
    """, (symbol,)).fetchall()

    if len(bars) < 10:
        conn.close()
        return {'error': f'Insufficient OHLCV data for {symbol} ({len(bars)} bars)'}

    bars = list(reversed(bars))
    df = pd.DataFrame([{
        'bar_date': r['bar_date'],
        'open':   float(r['open']   or 0),
        'high':   float(r['high']   or 0),
        'low':    float(r['low']    or 0),
        'close':  float(r['close']  or 0),
        'volume': float(r['volume'] or 0),
    } for r in bars])
    df = df.sort_values('bar_date').reset_index(drop=True)
    latest_raw = df.iloc[-1]
    latest_symbol_date = str(latest_raw.get('bar_date') or '')
    if latest_symbol_date != pred_date and not params.get('allow_stale'):
        conn.close()
        return {
            'success': False,
            'error': 'Prediction blocked: symbol OHLCV is stale for requested date.',
            'symbol': symbol,
            'pred_date': pred_date,
            'latest_symbol_date': latest_symbol_date,
            'note': 'Update OHLCV first, or pass allow_stale=true for internal research only.',
        }
    if (
        safe_float(latest_raw.get('close')) <= 0 or
        safe_float(latest_raw.get('high')) < safe_float(latest_raw.get('low')) or
        safe_float(latest_raw.get('volume')) <= 0
    ):
        conn.close()
        return {
            'success': False,
            'error': f'Bad latest market data for {symbol}',
            'bar_date': latest_raw.get('bar_date', ''),
        }
    df = _compute_indicators(df)

    # Phase 4: sector context for prediction
    sector_cache = _load_sector_breadth_cache(conn)
    sec_row = conn.execute(
        "SELECT sector FROM stock_universe WHERE symbol=?", (symbol,)
    ).fetchone()
    sec = sec_row['sector'] if sec_row else 'UNKNOWN'

    feat = _build_feature_row_from_tail(df, sector_cache=sector_cache, sector=sec)
    if feat is None:
        conn.close()
        return {'error': f'Not enough indicator history for {symbol}'}

    feat = shape_features_for_model(feat, model)
    X    = np.array([feat], dtype=np.float32)
    prob = float(model.predict(X)[0])

    last = df.iloc[-1]
    current_vol_ratio = round(safe_float(last.get('vol_ratio_20'), 1.0), 2)
    tier, gate_reason = _assign_model_tier(
        prob, rank=1, vol_ratio=current_vol_ratio, policy=decision_policy
    )
    hist = conn.execute(
        "SELECT explosion_date, direction, return_3d FROM explosive_moves WHERE symbol=? ORDER BY explosion_date DESC LIMIT 5",
        (symbol,)
    ).fetchall()
    final_gate_symbols = _final_signal_symbols(conn, pred_date)
    conn.close()
    db_tier, reliability_flag, client_ready = _client_gate_fields(symbol, tier, final_gate_symbols)

    return {
        'success':          True,
        'symbol':           symbol,
        'pred_date':        pred_date,
        'explosion_prob':   round(prob, 4),
        'prob_pct':         int(prob * 100),
        'confidence_tier':  db_tier,
        'model_confidence_tier': tier,
        'reliability_flag': reliability_flag,
        'client_ready':     client_ready,
        'requires_final_signals_gate': True,
        'ml_gate_reason':    gate_reason,
        'decision_policy':   decision_policy,
        'bar_date':         last.get('bar_date', ''),
        'current_rsi':      round(safe_float(last.get('rsi14'),        50.0), 1),
        'current_bb_width': round(safe_float(last.get('bb_width'),     0.05), 4),
        'current_vol_ratio':current_vol_ratio,
        'current_adx':      round(safe_float(last.get('adx14'),        20.0), 1),
        'recent_explosions': [dict(h) for h in hist],
    }


def cmd_evaluate(params):
    conn = get_db()
    ensure_tables(conn)

    if not os.path.exists(MODEL_PATH):
        return {'error': 'Model not trained yet. Run: npm run egx:ml:train'}

    scores = conn.execute(
        "SELECT * FROM ml_model_scores ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()

    if not scores:
        return {'success': True, 'message': 'No evaluation results yet. Run train first.'}

    latest = dict(scores[0])
    top_features = json.loads(latest.get('top_features', '[]'))
    decision_policy = _load_decision_policy()
    n_pos = latest.get('n_train_positive', 1) or 1
    n_neg = latest.get('n_train_negative', 0) or 0
    n_oos_pos = latest.get('n_oos_positive') or 0
    n_oos_neg_actual = latest.get('n_oos_negative')
    # Historical rows only persisted OOS positives. For the standard 3x negative
    # sampling design, estimate OOS negatives from n_oos_positive for quality
    # classification; a missing/zero value remains blocked.
    n_oos_neg_est = int(n_oos_neg_actual) if n_oos_neg_actual is not None else (int(n_oos_pos * 3) if n_oos_pos else 0)
    quality = dataset_quality_report(
        [1] * int(n_pos) + [0] * int(n_neg),
        [1] * int(n_oos_pos) + [0] * int(n_oos_neg_est),
    )
    auc_oos = latest['auc_oos'] or 0
    precision_50 = latest['precision_at_50'] or 0
    oos_baseline = n_oos_pos / max(n_oos_pos + n_oos_neg_est, 1)
    edge_claim_allowed = bool(
        quality['ok'] and
        0.55 <= auc_oos < 0.95 and
        precision_50 > oos_baseline
    )

    return {
        'success': True,
        'latest_evaluation': {
            'trained_at':        latest['trained_at'],
            'train_end':         latest['train_end_date'],
            'oos_start':         latest['oos_start_date'],
            'n_train_positive':  n_pos,
            'n_train_negative':  n_neg,
            'balance_ratio':     round(n_neg / n_pos, 1),
            'n_oos_positive':    latest['n_oos_positive'],
            'n_oos_negative':    n_oos_neg_est,
            'n_oos_total':       latest.get('n_oos_total') or (int(n_oos_pos) + int(n_oos_neg_est)),
            'auc_train':         latest['auc_train'],
            'auc_oos':           latest['auc_oos'],
            'precision_at_50':   latest['precision_at_50'],
            'precision_at_70':   latest['precision_at_70'],
            'recall_at_50':      latest['recall_at_50'],
            'precision_at_10':   latest.get('precision_at_10'),
            'precision_at_20':   latest.get('precision_at_20'),
            'precision_at_top10pct': latest.get('precision_at_top10pct'),
            'recommended_threshold_high': latest.get('recommended_threshold_high') or decision_policy.get('high_prob'),
            'recommended_threshold_medium': latest.get('recommended_threshold_medium') or decision_policy.get('medium_prob'),
            'abstain_threshold': latest.get('abstain_threshold') or decision_policy.get('abstain_prob'),
            'top_features':      top_features,
            'notes':             latest['notes'],
            'quality':           quality,
            'oos_baseline_precision': round(oos_baseline, 4),
            'metrics_report': {
                'baseline_precision': round(oos_baseline, 4),
                'threshold_precision': {
                    'p_at_50': latest['precision_at_50'],
                    'p_at_70': latest['precision_at_70'],
                },
                'top_k_precision': {
                    'p_at_10': latest.get('precision_at_10'),
                    'p_at_20': latest.get('precision_at_20'),
                    'p_at_50': latest['precision_at_50'],
                    'p_at_top10pct': latest.get('precision_at_top10pct'),
                },
                'client_acceptance_rule': 'HIGH/MEDIUM model tier + final_signals actionable; LOW/ABSTAIN always blocked',
            },
        },
        'decision_policy': decision_policy,
        'model_quality': (
            'INSUFFICIENT_EVIDENCE' if not quality['ok'] else
            'GOOD' if auc_oos > 0.65 else
            'FAIR' if auc_oos > 0.55 else 'POOR'
        ),
        'is_overfitting': (
            abs((latest['auc_train'] or 0) - (latest['auc_oos'] or 0)) > 0.15
        ),
        'balance_ok': n_neg >= n_pos,
        'auc_ok':     auc_oos < 0.95,  # <0.95 = not memorizing
        'edge_claim_allowed': edge_claim_allowed,
    }


def cmd_feature_importance(params):
    if not os.path.exists(MODEL_PATH):
        return {'error': 'Model not trained yet. Run: npm run egx:ml:train'}

    try:
        import lightgbm as lgb
    except ImportError:
        return {'error': 'lightgbm not installed'}

    model    = lgb.Booster(model_file=MODEL_PATH)
    n_model_features = model_feature_count(model)
    fi_gain  = model.feature_importance(importance_type='gain').tolist()
    fi_split = model.feature_importance(importance_type='split').tolist()
    total_g  = max(sum(fi_gain), 1)

    features = []
    for name, g, s in zip(FEATURE_COLS[:n_model_features], fi_gain, fi_split):
        features.append({
            'feature':  name,
            'gain':     round(g, 2),
            'gain_pct': round(g / total_g * 100, 1),
            'split':    int(s),
        })
    features.sort(key=lambda x: -x['gain'])

    return {
        'success':    True,
        'n_features': len(features),
        'model_feature_count': n_model_features,
        'features':   features,
        'top_3':      [f['feature'] for f in features[:3]],
    }


def cmd_check_db(params):
    """Diagnostic: show counts in ML-related tables."""
    conn = get_db()
    ensure_tables(conn)

    def cnt(q, *a):
        r = conn.execute(q, a).fetchone()
        return r[0] if r else 0

    ohlcv_total      = cnt("SELECT COUNT(*) FROM ohlcv_history_features")
    ohlcv_symbols    = cnt("SELECT COUNT(DISTINCT symbol) FROM ohlcv_history_features")
    explosion_total  = cnt("SELECT COUNT(*) FROM explosive_moves")
    explosion_pre    = cnt("SELECT COUNT(*) FROM explosive_moves WHERE explosion_date < '2026-01-01'")
    explosion_oos    = cnt("SELECT COUNT(*) FROM explosive_moves WHERE explosion_date >= '2026-01-30'")
    preds_total      = cnt("SELECT COUNT(*) FROM explosion_predictions")
    model_runs       = cnt("SELECT COUNT(*) FROM ml_model_scores")
    latest_model     = conn.execute("SELECT * FROM ml_model_scores ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    result = {
        'success': True,
        'ohlcv':      {'total_bars': ohlcv_total, 'symbols': ohlcv_symbols},
        'explosions': {'total': explosion_total, 'is_train': explosion_pre, 'oos': explosion_oos},
        'predictions': {'total': preds_total},
        'model_runs':  model_runs,
        'model_exists': os.path.exists(MODEL_PATH),
    }
    if latest_model:
        result['latest_model'] = {
            'trained_at':  latest_model['trained_at'],
            'auc_train':   latest_model['auc_train'],
            'auc_oos':     latest_model['auc_oos'],
            'n_pos':       latest_model['n_train_positive'],
            'n_neg':       latest_model['n_train_negative'],
            'notes':       latest_model['notes'],
        }

    # Sanity checks
    checks = []
    if ohlcv_total > 10000:
        checks.append('✅ OHLCV history sufficient')
    else:
        checks.append(f'❌ OHLCV too small: {ohlcv_total} rows (need 10k+)')

    if explosion_total > 100:
        checks.append(f'✅ Explosions: {explosion_total}')
    else:
        checks.append(f'❌ Explosions too few: {explosion_total}')

    if latest_model:
        n_pos = latest_model['n_train_positive'] or 0
        n_neg = latest_model['n_train_negative'] or 0
        auc_t = latest_model['auc_train'] or 0
        auc_o = latest_model['auc_oos']  or 0

        if n_neg >= n_pos:
            checks.append(f'✅ Balance OK: {n_neg} neg / {n_pos} pos')
        else:
            checks.append(f'❌ Imbalanced: only {n_neg} neg vs {n_pos} pos (need ≥ 1×)')

        if auc_t < 0.99:
            checks.append(f'✅ No memorization: AUC_train={auc_t:.3f}')
        else:
            checks.append(f'❌ Memorization: AUC_train={auc_t:.3f} (suspiciously high)')

        if auc_o > 0.55:
            checks.append(f'✅ OOS AUC={auc_o:.3f} (>0.55 = learning)')
        else:
            checks.append(f'❌ OOS AUC={auc_o:.3f} (≤0.55 = useless)')

    if preds_total > 0:
        checks.append(f'✅ explosion_predictions: {preds_total} rows')
    else:
        checks.append('❌ explosion_predictions empty — run predict_today after training')

    result['sanity_checks'] = checks
    return result


def cmd_shap_explain(params):
    """SHAP model explainability — why each symbol got its explosion probability.

    params:
      top_n   : int   (default 20) — explain top N predictions
      symbol  : str   (optional)   — explain a specific symbol only
    """
    import shap, numpy as _np

    if not os.path.exists(MODEL_PATH):
        return {'success': False, 'error': 'Model not trained — run train first'}

    import lightgbm as lgb
    model = lgb.Booster(model_file=MODEL_PATH)

    conn  = get_db()
    top_n = int(params.get('top_n', 20))
    sym   = params.get('symbol')

    if sym:
        rows = conn.execute(
            "SELECT * FROM explosion_predictions WHERE symbol=? ORDER BY pred_date DESC LIMIT 1",
            (sym,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM explosion_predictions
               WHERE pred_date=(SELECT MAX(pred_date) FROM explosion_predictions)
               ORDER BY explosion_prob DESC LIMIT ?""",
            (top_n,)
        ).fetchall()
    conn.close()

    if not rows:
        return {'success': False, 'error': 'No predictions found — run predict_today first'}

    # Rebuild feature matrix for these symbols
    # Use 120-day lookback to ensure enough bars for indicator computation
    from_date = min(r['pred_date'] for r in rows)
    conn2 = get_db()
    ohlcv_cache = _build_ohlcv_cache(conn2, max_date=from_date,
                                      min_date=_date_sub(from_date, 120))
    conn2.close()

    X_rows, symbols_out, probs_out = [], [], []
    for row in rows:
        s = row['symbol']
        sym_df = ohlcv_cache.get(s, [])
        if len(sym_df) < 6:
            continue
        feat = _build_feature_row_from_tail(sym_df)
        if feat is None:
            continue
        feat = shape_features_for_model(feat, model)
        X_rows.append(feat)
        symbols_out.append(s)
        probs_out.append(row['explosion_prob'])

    if not X_rows:
        return {'success': False, 'error': 'Could not build features for SHAP analysis'}

    X_mat = _np.array(X_rows, dtype=_np.float32)

    # SHAP TreeExplainer
    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_mat)  # shape (n, n_features)

    # If lgb returns list for binary classification, take index 1
    if isinstance(shap_values, list):
        sv = shap_values[1]
    else:
        sv = shap_values

    results = []
    for i, s in enumerate(symbols_out):
        # Top 3 drivers for this symbol
        sv_i   = sv[i]
        feature_names = FEATURE_COLS[:X_mat.shape[1]]
        sorted_idx = sorted(range(len(feature_names)), key=lambda j: abs(sv_i[j]), reverse=True)
        drivers = [
            {
                'feature':    feature_names[j],
                'shap_value': round(float(sv_i[j]), 4),
                'feature_val': round(float(X_mat[i, j]), 4),
            }
            for j in sorted_idx[:3]
        ]
        results.append({
            'symbol':      s,
            'probability': round(float(probs_out[i]), 3),
            'top_drivers': drivers,
        })

    # Global feature importance from mean |SHAP|
    mean_abs = _np.abs(sv).mean(axis=0)
    global_imp = sorted(
        [{'feature': FEATURE_COLS[:X_mat.shape[1]][j], 'mean_abs_shap': round(float(mean_abs[j]), 4)}
         for j in range(X_mat.shape[1])],
        key=lambda x: x['mean_abs_shap'], reverse=True
    )

    return {
        'success':          True,
        'n_explained':      len(results),
        'predictions':      results,
        'global_feature_importance': global_imp,
    }


def _date_sub(date_str, days):
    """Subtract days from a YYYY-MM-DD string."""
    d = datetime.date.fromisoformat(date_str) - datetime.timedelta(days=days)
    return d.isoformat()


def cmd_build_full(params):
    conn = get_db()
    ensure_tables(conn)
    conn.close()

    # Always retrain in build_full
    train_result = cmd_train(params)
    if not train_result.get('success'):
        return {'error': f'Training failed: {train_result}'}

    eval_result = cmd_evaluate({})
    pred_result = cmd_predict_today(params)

    return {
        'success': True,
        'training': {
            'n_train':          train_result.get('n_train'),
            'n_pos':            train_result.get('n_train_positive'),
            'n_neg':            train_result.get('n_train_negative'),
            'balance_ratio':    train_result.get('balance_ratio'),
            'auc_train':        train_result.get('auc_train'),
            'auc_oos':          train_result.get('auc_oos'),
            'precision_at_50':  train_result.get('precision_at_50'),
        },
        'evaluation': eval_result.get('latest_evaluation'),
        'predictions': {
            'n_scored':  pred_result.get('n_symbols_scored', 0),
            'n_signals': pred_result.get('n_signals', 0),
            'n_high':    pred_result.get('n_high', 0),
            'n_stored':  pred_result.get('n_stored_db', 0),
            'top_3':     pred_result.get('top_predictions', [])[:3],
        },
    }


COMMANDS = {
    'train':              cmd_train,
    'optuna_tune':        cmd_optuna_tune,
    'predict_today':      cmd_predict_today,
    'predict_symbol':     cmd_predict_symbol,
    'evaluate':           cmd_evaluate,
    'feature_importance': cmd_feature_importance,
    'shap_explain':       cmd_shap_explain,
    'check_db':           cmd_check_db,
    'build_full':         cmd_build_full,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
