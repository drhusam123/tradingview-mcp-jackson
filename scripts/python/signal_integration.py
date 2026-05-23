#!/usr/bin/env python3
"""
Phase 65 — Signal Integration Layer
Unified Evidence Score (UES) combining all analysis layers into one 0-100 score.
UES = 0.25×ExplosionML + 0.20×Breadth + 0.20×Technical + 0.15×CrossMarket + 0.10×Liquidity + 0.10×AntiLaw
Conviction tiers: HIGH(≥70+bull) / MEDIUM(55-69) / LOW(40-54) / REJECT(<40)
"""
import os, sys, json, sqlite3, datetime, math

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    -- Ph 22 — Spectral Shadow Validator: daily predictions + deferred outcome fill
    CREATE TABLE IF NOT EXISTS spectral_shadow_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol           TEXT NOT NULL,
        prediction_date  TEXT NOT NULL,
        spectral_regime  TEXT,
        cycle_bottom_prox REAL,
        spectral_boost   REAL,
        ues_with_boost   REAL,
        ues_without      REAL,
        boost_delta      REAL,          -- ues_with_boost - ues_without
        -- Deferred outcome fields (filled 5 trading days later):
        return_3d        REAL,
        return_5d        REAL,
        exploded         INTEGER,       -- 1 if appeared in explosive_moves within 5d
        outcome_date     TEXT,
        created_at       TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, prediction_date)
    );
    -- Ph 25 — Spectral Reliability Memory: per-symbol rolling alpha
    CREATE TABLE IF NOT EXISTS spectral_reliability (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol           TEXT NOT NULL,
        as_of_date       TEXT NOT NULL,
        n_cyclical_obs   INTEGER,
        n_noisy_obs      INTEGER,
        cyclical_precision REAL,        -- P(exploded | cyclical + boost > 1.05)
        noisy_precision  REAL,          -- P(exploded | noisy)
        alpha_30d        REAL,          -- avg(return_5d | boost>1.05) - avg(return_5d | boost=1.0)
        alpha_90d        REAL,
        reliability_score REAL,         -- 0=don't trust FFT, 1=fully trust
        computed_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, as_of_date)
    );
    -- Ph 26 — Spectral Alpha Dashboard: rolling metrics table
    CREATE TABLE IF NOT EXISTS spectral_alpha_dashboard (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_date    TEXT NOT NULL,
        window_days      INTEGER NOT NULL,
        regime           TEXT NOT NULL,          -- cyclical_high / cyclical_low / noisy / compression / expansion / MARKET
        n_obs            INTEGER,
        avg_return_5d    REAL,
        median_return_5d REAL,
        hit_rate         REAL,                   -- P(return_5d > 0)
        explosion_rate   REAL,                   -- P(exploded within 5d)
        sharpe_5d        REAL,                   -- mean/std of return_5d
        max_drawdown     REAL,                   -- worst single return_5d
        kelly_efficiency REAL,                   -- avg kelly_fraction where boosted vs unboosted
        ues_calib_error  REAL,                   -- |predicted_prob - actual_explosion_rate|
        boost_edge       REAL,                   -- avg_return_5d(boosted) - avg_return_5d(unboosted)
        UNIQUE(computed_date, window_days, regime)
    );
    CREATE TABLE IF NOT EXISTS unified_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        explosion_score REAL,
        breadth_score REAL,
        technical_score REAL,
        cross_market_score REAL,
        liquidity_score REAL,
        anti_law_score REAL,
        unified_score REAL,
        conviction_tier TEXT,
        active_regime TEXT,
        breadth_signal TEXT,
        scan_score REAL,
        n_confirming_laws INTEGER,
        top_law TEXT,
        entry_price REAL,
        entry_high REAL,
        stop_loss REAL,
        t1_target REAL,
        t2_target REAL,
        r_ratio REAL,
        liquidity_tier TEXT,
        max_position_egp REAL,
        is_anti_law_triggered INTEGER DEFAULT 0,
        rejection_reason TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(signal_date, symbol)
    );
    """)
    # Ph 32 — Recommendation Outcome Tracker
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS recommendation_outcomes (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date      TEXT NOT NULL,
        report_date      TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        conviction_tier  TEXT,
        entry_price      REAL,
        stop_loss        REAL,
        t1_target        REAL,
        ues              REAL,
        ml_score         REAL,
        behavioral_class TEXT,
        spectral_regime  TEXT,
        -- Outcome fields (filled 5-10 days later)
        close_t1         REAL,   -- close 1 trading day after
        close_t3         REAL,   -- close 3 trading days after
        close_t5         REAL,   -- close 5 trading days after
        close_t10        REAL,   -- close 10 trading days after
        return_t1        REAL,
        return_t3        REAL,
        return_t5        REAL,
        return_t10       REAL,
        hit_t1           INTEGER,  -- 1 if return_t1 > 0
        hit_t5           INTEGER,  -- 1 if return_t5 > 0
        reached_t1_target INTEGER, -- 1 if high in 10d >= t1_target
        hit_stop         INTEGER,  -- 1 if low in 10d <= stop_loss
        outcome_filled        INTEGER DEFAULT 0,
        quality_gate_passed   INTEGER DEFAULT 0,  -- Ph 33: was this signal gated?
        created_at            TEXT DEFAULT (datetime('now')),
        UNIQUE(signal_date, symbol)
    );
    """)

    # Upgrade: add columns added in later phases (safe — fails silently if already exist)
    for col, defn in [
        ('entry_high', 'REAL'), ('stop_loss', 'REAL'),
        ('t1_target', 'REAL'), ('t2_target', 'REAL'), ('r_ratio', 'REAL'),
        ('dna_score', 'REAL'), ('cycle_score', 'REAL'),                    # Ph 75
        ('quality_gate_passed', 'INTEGER DEFAULT 0'),                      # Ph 27
        ('gate_reason', 'TEXT'),                                           # Ph 27 — rejection reason
        ('behavioral_class', 'TEXT'),                                      # Ph 28
        ('pine_rs_percentile', 'REAL'),                                    # Ph 29
    ]:
        try:
            conn.execute(f'ALTER TABLE unified_signals ADD COLUMN {col} {defn}')
        except Exception:
            pass
    # Upgrade recommendation_outcomes (safe)
    for col, defn in [
        ('quality_gate_passed',  'INTEGER DEFAULT 0'),      # Ph 33
        ('entry_triggered',      'INTEGER DEFAULT 0'),      # Ph 44: price touched entry zone
        ('entry_trigger_date',   'TEXT'),                   # Ph 44: first date price entered zone
        ('entry_trigger_close',  'REAL'),                   # Ph 44: close price on trigger day
    ]:
        try:
            conn.execute(f'ALTER TABLE recommendation_outcomes ADD COLUMN {col} {defn}')
        except Exception:
            pass
    conn.commit()

def safe_float(v, default=0.0):
    try:
        if v is None: return default
        return float(v)
    except: return default

def get_explosion_score(symbol, date, conn):
    """Get ML explosion probability (0-100).
    Looks forward 1 day AND back 7 days to handle the pred_date vs signal_date mismatch:
    ML predictions are computed for *tomorrow* using today's data, so pred_date = signal_date + 1.
    """
    try:
        d = datetime.date.fromisoformat(date)
        # Allow predictions dated from 7 days ago to 1 day ahead
        lookback  = (d - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        lookahead = (d + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        row = conn.execute(
            """SELECT prob_pct FROM explosion_predictions
               WHERE symbol=? AND pred_date>=? AND pred_date<=?
               ORDER BY pred_date DESC LIMIT 1""",
            (symbol, lookback, lookahead)
        ).fetchone()
        if row:
            return safe_float(row['prob_pct'], 50.0)
    except Exception:
        pass  # Table may not exist yet — run egx:ml:train first
    return 50.0  # neutral default when no ML prediction

def get_breadth_score(date, conn):
    """
    Get market breadth score (0-100), signal, regime_input, and A/D ratio.

    Returns: (adj_score, sig, regime_input, ad_ratio)
      - adj_score: 0-100 composite breadth score (RSI-adjusted)
      - sig: 'BREADTH_BULL', 'BREADTH_LEAN_BULL', 'BREADTH_NEUTRAL', 'BREADTH_BEAR', etc.
      - regime_input: 'BULL', 'BEAR', or 'NEUTRAL'
      - ad_ratio: n_advances/n_declines from market_breadth_enhanced
                  1.0 = equal, >1.0 = more advances (bullish), <1.0 = more declines (bearish)
                  2026-05-22 discovery: min_ad=1.0 filter → 6m WR=76.2% vs 71% baseline (+5.2pp)

    Applies a real-time RSI overbought penalty on top of the stored breadth_score:
      - >80% of stocks with RSI>70 → -10 pts (extreme crowding)
      - >60% of stocks with RSI>70 →  -6 pts (crowded market)
      - >40% of stocks with RSI>70 →  -2 pts (elevated risk)
    This prevents the breadth engine from calling BREADTH_BULL when the entire
    market is overbought and mean-reversion risk is elevated.
    """
    row = conn.execute(
        "SELECT breadth_score, signal FROM market_breadth_daily WHERE date<=? ORDER BY date DESC LIMIT 1",
        (date,)
    ).fetchone()
    base_score = safe_float(row['breadth_score'] if row else None, 50.0)
    base_signal = (row['signal'] if row else None) or 'BREADTH_NEUTRAL'

    # Read A/D ratio from market_breadth_enhanced (n_advances/n_declines, threshold=1.0)
    try:
        ad_row = conn.execute(
            "SELECT ad_ratio FROM market_breadth_enhanced WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        _ad_ratio = safe_float(ad_row['ad_ratio'] if ad_row else None, 1.0)
    except Exception:
        _ad_ratio = 1.0  # neutral fallback if table missing

    # Real-time RSI overbought adjustment
    try:
        rsi_rows = conn.execute("""
            SELECT symbol, rsi14 FROM indicators_cache
            WHERE bar_date <= ? AND bar_date >= date(?, '-3 days')
            GROUP BY symbol HAVING bar_date = MAX(bar_date)
        """, (date, date)).fetchall()
        if rsi_rows and len(rsi_rows) >= 10:  # only apply if we have meaningful sample
            n_ob = sum(1 for r in rsi_rows if (r['rsi14'] or 0) > 70)
            pct_ob = n_ob / len(rsi_rows) * 100
            if pct_ob > 80:
                rsi_adj = -10.0
            elif pct_ob > 60:
                rsi_adj = -6.0
            elif pct_ob > 40:
                rsi_adj = -2.0
            else:
                rsi_adj = 0.0
            adj_score = max(0.0, min(100.0, base_score + rsi_adj))
        else:
            adj_score = base_score
            pct_ob    = 0.0
            rsi_adj   = 0.0
    except Exception:
        adj_score = base_score
        pct_ob    = 0.0
        rsi_adj   = 0.0

    # Re-derive signal from adjusted score
    SIGNAL_THRESHOLDS = [
        (70, 'BREADTH_BULL'), (55, 'BREADTH_LEAN_BULL'),
        (45, 'BREADTH_NEUTRAL'), (30, 'BREADTH_LEAN_BEAR'), (0, 'BREADTH_BEAR'),
    ]
    sig = 'BREADTH_BEAR'
    for thresh, label in SIGNAL_THRESHOLDS:
        if adj_score >= thresh:
            sig = label
            break

    regime_input = 'BULL' if 'BULL' in sig else 'BEAR' if 'BEAR' in sig else 'NEUTRAL'
    return adj_score, sig, regime_input, _ad_ratio

def get_technical_score(symbol, date, conn):
    """
    Compute technical confluence score 0-100 from indicators_cache.

    Rebuilt (score-saturation fix): starts from 0 (not 50) so bull-market
    drift cannot inflate all stocks to 95+. Max theoretical = 100, typical
    BULL average target ≈ 55-65.

    Component budgets (sum to 100):
      RSI 14      : ±25 pts  (discriminating — penalty for overbought)
      EMA align   :  0-30 pts
      MACD        :  0-20 pts
      ADX         : ±10 pts
      Volume/ADV20: ±15 pts  (uses vol_ratio_20 as proxy for adv20 ratio)
      RSI slope 3d: ±10 pts  (uses rsi_slope_3d if available, else 0)
    """
    row = conn.execute(
        "SELECT * FROM indicators_cache WHERE symbol=? AND bar_date<=? ORDER BY bar_date DESC LIMIT 1",
        (symbol, date)
    ).fetchone()
    if not row:
        return 50.0

    score = 0.0

    # ── RSI 14 component (±30 pts) — CALIBRATED FOR WIN-RATE ──────────────────
    # Updated 2026-05-22 v3 (hold-sweep confirmed): RSI 60-67 = TRUE sweet spot
    # RSI sweep (12m, n=380): rsi<=65: WR=55.9% PF=1.88 | rsi<=67: WR=56.1% | rsi<=72: WR=54.2%
    # RSI sweep (6m,  n=201): rsi<=65: WR=71.0% PF=3.74 | rsi<=67: WR=70.1% | rsi<=72: WR=65.2%
    # RSI 67-72 zone costs ~+5.9pp WR at 6m vs <=67 filter → penalized harder
    rsi = safe_float(row['rsi14'], 50.0)
    if   60 <= rsi <= 67:   score += 25   # ★ TRUE sweet spot: 6m WR=70-71% — peak quality zone
    elif 55 <= rsi <  60:   score += 15   # decent momentum, early stage
    elif 67 <  rsi <= 70:   score +=  8   # declining: 6m WR=66-68% (below sweet spot by ~4pp)
    elif 70 <  rsi <= 72:   score +=  2   # extended: 6m WR≈65% — marginally above gate only
    elif 72 <  rsi <= 78:   score -=  8   # overbought: clear WR drag, avoid for new entries
    elif 78 <  rsi:         score -= 15   # deep overbought: high reversal risk
    elif 50 <= rsi <  55:   score -=  5   # RSI 50-55 = WR=44% — avoid
    elif 40 <= rsi <  50:   score +=  0   # neutral / oversold — uncertain
    else:                   score -=  5   # rsi < 40 — deep oversold, trend risk

    # ── EMA alignment (0-25 pts) — context-adjusted ─────────────────────────
    # In a market-wide bull run (EMA aligned everywhere), alignment = less alpha.
    # Cap EMA bonus when RSI is already extended to avoid rewarding overbought.
    above_ema20  = row['above_ema20']
    above_ema50  = row['above_ema50']
    above_ema200 = row['above_ema200']

    # Fallback: compute EMA alignment from OHLCV when indicators_cache lacks it.
    # This handles rows created by fetch_technical_indicators.mjs (no EMA data).
    if above_ema20 is None or above_ema50 is None or above_ema200 is None:
        try:
            ohlcv_bars = conn.execute(
                "SELECT close FROM ohlcv_history WHERE symbol=? AND bar_time <= "
                "(SELECT MAX(bar_time) FROM ohlcv_history WHERE symbol=? AND "
                " date(bar_time,'unixepoch') <= ?) "
                "ORDER BY bar_time DESC LIMIT 210",
                (symbol, symbol, date)
            ).fetchall()
            closes = [r['close'] for r in reversed(ohlcv_bars)]
            if len(closes) >= 20:
                cur_close = closes[-1]
                def _ema(src, p):
                    k = 2.0 / (p + 1)
                    e = src[0]
                    for v in src[1:]: e = v * k + e * (1 - k)
                    return e
                if above_ema20 is None and len(closes) >= 20:
                    above_ema20 = 1 if cur_close > _ema(closes[-20:], 20) else 0
                if above_ema50 is None and len(closes) >= 50:
                    above_ema50 = 1 if cur_close > _ema(closes[-50:], 50) else 0
                if above_ema200 is None and len(closes) >= 200:
                    above_ema200 = 1 if cur_close > _ema(closes, 200) else 0
        except Exception:
            pass

    above_ema20  = int(above_ema20  or 0)
    above_ema50  = int(above_ema50  or 0)
    above_ema200 = int(above_ema200 or 0)
    n_above = above_ema20 + above_ema50 + above_ema200
    # EMA alignment: full reward — high RSI + aligned EMAs = strong trend (good with ADX<35)
    # Removed RSI>72 discount: high RSI + all EMAs aligned = strongest setup
    if   n_above == 3: score += 25  # full bull alignment — best setup
    elif n_above == 2: score += 15  # partial alignment — good
    elif n_above == 1: score +=  6  # minimal alignment
    # 0 above → +0 (no bonus, no penalty)

    # ── MACD (0-18 pts) — momentum quality ──────────────────────────────────
    macd_h   = safe_float(row['macd_hist'],   0)
    macd_sig = safe_float(row['macd_signal'], 0)
    if macd_h > 0:
        if macd_h > macd_sig:
            score += 18   # positive + bullish cross (accelerating momentum)
        else:
            score += 10   # positive only (slowing momentum)
    elif macd_h < 0 and macd_h < macd_sig:
        score +=  5        # bearish but could bounce (partial credit)
    elif macd_h < 0:
        score -=  3        # declining momentum
    # near-zero → +0

    # ── ADX (±10 pts) — trend strength ──────────────────────────────────────
    adx = safe_float(row['adx14'] if 'adx14' in row.keys() else None, None)
    if adx is None:
        try:
            adx = safe_float(row['adx'], None)
        except Exception:
            adx = None
    if adx is not None:
        # ADX scoring updated 2026-05-22 v2: ADX>=26 is sweet spot (WR=56.8% PF=2.00)
        # ADX 26-32: best (developing trend, WR=56.8%+); ADX 22-26: weaker (WR≈54%)
        # ADX>=40: WR=44.8% PF=0.83 — below breakeven; ADX<20: no trend
        if   26 <= adx < 32: score += 12   # ★ sweet spot: developing trend
        elif 32 <= adx < 35: score +=  9   # solid trend — good
        elif 22 <= adx < 26: score +=  4   # below sweet spot: weaker trend
        elif 35 <= adx < 40: score +=  2   # getting extended
        elif 40 <= adx < 50: score -=  3   # over-extended: below average
        elif adx >= 50:      score -=  7   # strongly over-extended — avoid
        elif adx < 20:       score -= 10   # no trend / choppy — avoid

    # ── Volume vs ADV20 (±12 pts) — conviction check ─────────────────────────
    # Updated 2026-05-22: vol 2.0-2.5 is sweet spot (WR=57.6%), extreme vol>3.5 is worst (WR=52.8%)
    # Moderate volume is fine — extreme spikes can be manipulation or exhaustion
    vol_r = safe_float(row['vol_ratio_20'], 1.0)
    if   2.0 <= vol_r <  3.5: score += 12   # sweet spot: conviction without exhaustion
    elif 1.2 <= vol_r <  2.0: score +=  8   # good conviction
    elif 0.8 <= vol_r <  1.2: score +=  4   # normal — ok
    elif vol_r >= 3.5:        score +=  6   # extreme — potential exhaustion spike
    elif vol_r >= 0.5:        score +=  0   # below average — weak conviction
    else:                     score -=  8   # very low volume — avoid

    # ── RSI slope 3-day (±10 pts) — momentum direction ──────────────────────
    try:
        rsi_slope = safe_float(row['rsi_slope_3d'], None)
        if rsi_slope is None:
            rsi_slope = safe_float(row['rsi_slope'], None)
        # Fallback: compute RSI slope from indicators_cache history when column is missing
        if rsi_slope is None:
            try:
                rsi_hist = conn.execute(
                    "SELECT rsi14 FROM indicators_cache WHERE symbol=? AND bar_date<=? "
                    "AND rsi14 IS NOT NULL ORDER BY bar_date DESC LIMIT 4",
                    (symbol, date)
                ).fetchall()
                if len(rsi_hist) >= 4:
                    rsi_slope = (rsi_hist[0]['rsi14'] - rsi_hist[3]['rsi14']) / 3.0
            except Exception:
                pass
        if rsi_slope is not None:
            # Updated 2026-05-22: RSI 60-78 is good with ADX<35, so allow slope bonus there
            if   rsi_slope >  2.0 and rsi <= 78: score += 10  # building momentum — sweet zone
            elif rsi_slope >  0.5 and rsi <= 82: score +=  5  # mild momentum — ok
            elif rsi_slope < -2.0:               score -= 10  # momentum collapsing — avoid
            elif rsi_slope < -0.5:               score -=  5  # momentum fading
            # near-flat: +0
    except Exception:
        pass

    return max(0.0, min(100.0, score))

def get_cross_market_score(date, conn):
    """
    Cross-market alignment score derived from:
    1. Current market regime (regime_history) — BULL/BEAR/NEUTRAL
    2. Market breadth signal — confirming or diverging
    3. Economics data (macro_economics) if available

    cross_market_daily exists but contains raw OHLCV bars (wrong table).
    Use regime + breadth as the real proxy for cross-market alignment.
    """
    try:
        # Regime: BULL=70, BEAR=30, NEUTRAL=50
        regime_row = conn.execute(
            "SELECT regime FROM regime_history WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        regime = regime_row['regime'] if regime_row else 'UNKNOWN'
        regime_score = {'BULL': 72.0, 'LEAN_BULL': 63.0, 'NEUTRAL': 50.0,
                        'LEAN_BEAR': 38.0, 'BEAR': 28.0}.get(regime, 50.0)

        # Breadth confirmation bonus/penalty
        breadth_row = conn.execute(
            """SELECT breadth_score, signal FROM market_breadth_daily
               WHERE date<=? ORDER BY date DESC LIMIT 1""",
            (date,)
        ).fetchone()
        breadth_adj = 0.0
        if breadth_row:
            sig = breadth_row['signal'] or ''
            if 'BULL' in sig:   breadth_adj = +6.0
            elif 'BEAR' in sig: breadth_adj = -6.0

        # Economics modifier (±5) from macro_economics table
        econ_adj = 0.0
        try:
            econ_rows = conn.execute(
                """SELECT indicator, value FROM macro_economics
                   WHERE date<=? ORDER BY date DESC LIMIT 6""",
                (date,)
            ).fetchall()
            if econ_rows:
                pos = sum(1 for r in econ_rows if safe_float(r['value'], 0) > 0)
                econ_adj = (pos / len(econ_rows) - 0.5) * 10.0
        except Exception:
            pass

        return max(20.0, min(90.0, regime_score + breadth_adj + econ_adj))
    except Exception:
        pass
    return 55.0  # neutral default

def get_liquidity_score(symbol, conn):
    """Get liquidity score and tier."""
    try:
        row = conn.execute(
            "SELECT liquidity_score, liquidity_tier, max_safe_order_egp FROM liquidity_profile WHERE symbol=?",
            (symbol,)
        ).fetchone()
        if row:
            return safe_float(row['liquidity_score'], 50.0), row['liquidity_tier'], safe_float(row['max_safe_order_egp'], 50000)
    except Exception:
        pass
    try:
        row2 = conn.execute(
            "SELECT liquidity_score, liquidity_tier FROM symbol_liquidity_profile WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row2:
            return safe_float(row2['liquidity_score'], 50.0), row2['liquidity_tier'], 50000
    except Exception:
        pass
    return 40.0, 'UNKNOWN', 50000

def get_anti_law_score(symbol, date, conn):
    """
    Check if anti-law is triggered. Uses anti_law_daily_scan (has symbol+date+veto).
    Falls back to anti_laws table (static, no date) if daily scan not available.
    Also ALWAYS enforces static veto even when daily scan is available.
    Returns (score 0-100, is_triggered bool).
    """
    # ALWAYS check static anti_laws table for HIGH-severity veto patterns
    # These are permanent pattern-level vetoes that override everything
    static_veto = False
    static_n_high = 0
    try:
        rows_static = conn.execute(
            """SELECT is_veto, severity, anti_precision, frequency, avg_loss
               FROM anti_laws WHERE symbol=?""",
            (symbol,)
        ).fetchall()
        for rs in rows_static:
            if rs['is_veto']:
                static_veto = True
                break
            # Auto-veto: HIGH severity + precision>0.55 + significant loss
            # Some records have is_veto=0 but clearly warrant a veto (e.g. NDRL prec=0.57 avg_loss=-11.8%)
            prec = float(rs['anti_precision'] or 0)
            avg_loss = float(rs['avg_loss'] or 0)
            if rs['severity'] == 'HIGH' and prec > 0.55 and avg_loss < -0.4:
                static_veto = True
                break
            if rs['severity'] == 'HIGH' and prec > 0.55:
                static_n_high += 1
    except Exception:
        pass

    if static_veto:
        return 0.0, True  # Hard veto from static pattern analysis

    # Primary: anti_law_daily_scan — daily computed scan
    try:
        row = conn.execute(
            """SELECT anti_law_veto, n_triggered, safety_level
               FROM anti_law_daily_scan WHERE symbol=? AND date=?""",
            (symbol, date)
        ).fetchone()
        if row:
            if row['anti_law_veto']:
                return 0.0, True
            # Partial triggers reduce score — static HIGH patterns add extra penalty
            n = (row['n_triggered'] or 0) + static_n_high
            score = max(15.0, 100.0 - n * 18.0)
            return score, False
    except Exception:
        pass

    # Secondary: anti_laws static table (no date filter — existence = always applies)
    try:
        rows2 = conn.execute(
            """SELECT COUNT(*) as n, MAX(is_veto) as veto,
               SUM(CASE WHEN severity='HIGH' AND anti_precision>0.55 THEN 1 ELSE 0 END) as n_high
               FROM anti_laws WHERE symbol=?""",
            (symbol,)
        ).fetchone()
        if rows2 and rows2['n'] > 0:
            if rows2['veto']:
                return 0.0, True
            n_high = rows2['n_high'] or 0
            # Each HIGH severity pattern costs 20 pts, each other costs 10
            penalty = n_high * 20 + (rows2['n'] - n_high) * 10
            score = max(20.0, 100.0 - penalty)
            return score, (score < 30.0)  # auto-trigger if very penalized
    except Exception:
        pass

    return 100.0, False  # No anti-law data → no restriction

def get_current_regime(date, conn):
    """Get current market regime."""
    try:
        row = conn.execute(
            "SELECT regime FROM regime_history WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        if row:
            return row['regime']
    except Exception:
        pass
    return 'UNKNOWN'

def get_law_confirmation(symbol, date, conn):
    """
    Universal law confirmation score (Ph 64).
    NOTE: best_regime is NULL for all high-precision laws — regime conditioning
    hasn't been run yet. Use raw precision + regime context as proxy.
    Returns (n_confirming, top_law_id, law_boost_score 0-100).
    """
    try:
        # Get current regime
        regime_row = conn.execute(
            "SELECT regime FROM regime_history WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        regime = regime_row['regime'] if regime_row else 'UNKNOWN'

        # Get ALL high-quality laws (best_regime is mostly NULL — use precision directly)
        laws = conn.execute(
            """SELECT pattern_id, precision, n_activations, best_regime, best_regime_precision
               FROM universal_laws_p16
               WHERE n_activations >= 10 AND precision >= 0.45
               ORDER BY precision DESC LIMIT 30"""
        ).fetchall()

        if not laws:
            return 0, None, 50.0

        # Priority 1: laws that explicitly match current regime
        regime_matched = [l for l in laws if l['best_regime'] == regime]
        # Priority 2: laws with NULL regime (haven't been conditioned yet — use at full weight)
        unconditioned  = [l for l in laws if l['best_regime'] is None]
        # Avoid BEAR laws in BULL regime and vice versa
        opposite = {'BULL': 'BEAR', 'BEAR': 'BULL'}
        avoid_regime = opposite.get(regime)
        neutral_laws  = [l for l in laws if l['best_regime'] != avoid_regime]

        # Best set of confirming laws
        best_set = regime_matched if regime_matched else neutral_laws
        n_conf   = len(best_set)
        top_law  = best_set[0]['pattern_id'] if best_set else laws[0]['pattern_id']

        # Score formula: base 50 + precision premium + count bonus
        if n_conf == 0:
            return 0, None, 45.0

        avg_prec = sum(l['precision'] for l in best_set[:5]) / min(5, n_conf)
        # avg_prec of ~0.60 gives 50 + 15 + ~5 = 70
        law_boost = min(95.0, 50.0 + (avg_prec - 0.45) * 100.0 + min(n_conf, 10) * 0.8)

        return n_conf, top_law, round(law_boost, 1)
    except Exception:
        return 0, None, 50.0

def get_alpha_grid_score(symbol, conn):
    """
    Check if this symbol has active high-grade alpha strategies (Ph 70).
    Returns boost score 0-100 based on best strategy grade.
    """
    try:
        # Check if any Grade S/A strategy has activated recently for this symbol
        # Proxy: check research_results for strategies that activate frequently
        # Use alpha_rankings grade distribution as market-wide modifier
        row = conn.execute(
            """SELECT MAX(composite_score) as best_score,
                      MAX(CASE WHEN grade='S' THEN 1 ELSE 0 END) has_s,
                      MAX(CASE WHEN grade='A' THEN 1 ELSE 0 END) has_a,
                      COUNT(*) n_alive
               FROM alpha_rankings WHERE is_alive=1"""
        ).fetchone()
        if row and row['n_alive'] and row['n_alive'] > 0:
            # Market-level alpha health: if S/A strategies exist, boost all signals
            if row['has_s']:
                return min(100.0, safe_float(row['best_score'], 70.0))
            elif row['has_a']:
                return 70.0
            else:
                return 55.0
    except Exception:
        pass
    return 50.0  # no alpha data yet

def get_dna_score(symbol, date, conn):
    """
    Ph 75 — Per-stock DNA score (0-100) from stock_profiles_deep.
    Rewards seasonal timing, smart-money accumulation, and personality alignment.
    Neutral = 50. Contributes ±4 pts to UES via dna_adj.
    """
    try:
        row = conn.execute(
            "SELECT * FROM stock_profiles_deep WHERE symbol=?", (symbol,)
        ).fetchone()
        if not row:
            return 50.0

        score = 50.0

        # ── Seasonal timing ─────────────────────────────────────────────────
        try:
            d = datetime.date.fromisoformat(date)
            dow   = d.weekday()   # 0=Mon … 4=Fri (EGX closed Sat/Sun)
            month = d.month

            best_dow  = row['best_day_of_week']
            worst_dow = row['worst_day_of_week']
            best_mon  = row['best_month']
            worst_mon = row['worst_month']

            if best_dow  is not None and dow == int(best_dow):   score += 8
            elif worst_dow is not None and dow == int(worst_dow): score -= 6

            if best_mon  is not None and month == int(best_mon):  score += 7
            elif worst_mon is not None and month == int(worst_mon): score -= 5
        except Exception:
            pass

        # ── Accumulation score (smart-money proxy) ───────────────────────────
        acc = safe_float(row['accumulation_score'], 0.0)
        if   acc >  4.0: score += 12
        elif acc >  2.0: score +=  7
        elif acc >  0.5: score +=  3
        elif acc < -2.0: score -=  8
        elif acc < -0.5: score -=  3

        # ── RSI / personality alignment ──────────────────────────────────────
        try:
            ic = conn.execute(
                "SELECT rsi14 FROM indicators_cache WHERE symbol=? AND bar_date<=? "
                "ORDER BY bar_date DESC LIMIT 1",
                (symbol, date)
            ).fetchone()
            if ic:
                rsi      = safe_float(ic['rsi14'], 50.0)
                opt_buy  = safe_float(row['rsi_optimal_buy'],  40.0)
                opt_sell = safe_float(row['rsi_optimal_sell'], 70.0)
                mr_score = safe_float(row['mean_reversion_score'], 0.0)
                tr_score = safe_float(row['trend_persistence_score'], 0.0)

                # Mean-reverter near its personal oversold zone → strong bounce candidate
                if mr_score > 30 and rsi <= opt_buy * 1.10:
                    score += 8
                # Trend-follower with RSI in momentum zone below its personal overbought
                elif tr_score > 30 and 55 <= rsi <= opt_sell:
                    score += 8
        except Exception:
            pass

        return max(0.0, min(100.0, score))
    except Exception:
        pass
    return 50.0


def get_cycle_score(symbol, date, conn):
    """
    Ph 75 — Cycle timing score (0-100) from market_cycles table.
    Near next_peak → bullish. Near next_trough → bearish. Neutral = 50.
    Contributes ±4 pts to UES via cycle_adj.
    """
    try:
        today = datetime.date.fromisoformat(date)

        rows = conn.execute(
            """SELECT cycle_type, period_days, next_peak_date, next_trough_date, confidence
               FROM market_cycles WHERE symbol=? ORDER BY confidence DESC LIMIT 5""",
            (symbol,)
        ).fetchall()

        if not rows:
            # Fall back to market-level cycles (symbol IS NULL = market-wide)
            rows = conn.execute(
                """SELECT cycle_type, period_days, next_peak_date, next_trough_date, confidence
                   FROM market_cycles WHERE symbol IS NULL OR symbol='MARKET'
                   ORDER BY confidence DESC LIMIT 5"""
            ).fetchall()

        if not rows:
            return 50.0

        score = 50.0
        for row in rows:
            conf = max(0.1, min(1.0, safe_float(row['confidence'], 0.5)))

            # Days to next peak
            try:
                if row['next_peak_date']:
                    peak_d = datetime.date.fromisoformat(str(row['next_peak_date'])[:10])
                    dtp = (peak_d - today).days
                    if 0 <= dtp <= 3:   score += 15 * conf   # peak imminent — prime entry
                    elif 0 <= dtp <= 7: score +=  8 * conf
                    elif -3 <= dtp < 0: score -=  5 * conf   # just past peak — fading
            except Exception:
                pass

            # Days to next trough
            try:
                if row['next_trough_date']:
                    trough_d = datetime.date.fromisoformat(str(row['next_trough_date'])[:10])
                    dtt = (trough_d - today).days
                    if 0 <= dtt <= 3:   score -= 15 * conf   # trough incoming — avoid
                    elif 0 <= dtt <= 7: score -=  8 * conf
                    elif -3 <= dtt < 0: score +=  5 * conf   # just past trough — recovering
            except Exception:
                pass

        return max(0.0, min(100.0, score))
    except Exception:
        pass
    return 50.0


def get_spectral_score(symbol, date, conn):
    """
    Ph 21 — Spectral Cycle Intelligence boost.

    Reads pre-computed FFT features from feature_store and returns:
      (boost_multiplier, spectral_regime_name, cycle_bottom_prox)

    boost_multiplier: non-linear tanh boost in range [0.85, 1.15]
      Formula: 1 + 0.15 * tanh(3 * (cycle_bottom_prox - 0.5))
      Quality gates:
        - noise_ratio  > 0.88 → boost disabled (1.0) — noisy spectrum (threshold = EGX p75)
        - stability    < 0.35 → boost disabled (1.0) — structural shift
        - regime='noisy' → multiplier scaled down to [0.85, 1.00]

    Spectral regime names: cyclical | noisy | compression | expansion
    """
    REGIME_NAMES = {0.0: "cyclical", 1.0: "noisy", 2.0: "compression", 3.0: "expansion"}
    try:
        # Batch-fetch all spectral features for this symbol+date
        feat_rows = conn.execute("""
            SELECT feature_name, feature_value
            FROM feature_store
            WHERE symbol=? AND feature_date=?
              AND feature_name IN (
                'fft_cycle_bottom_prox','fft_noise_ratio',
                'fft_stability_score','spectral_regime',
                'fft_dominant_amplitude','fft_dominant_period'
              )
        """, (symbol, date)).fetchall()

        feats = {r['feature_name']: safe_float(r['feature_value'], None) for r in feat_rows}

        if not feats or feats.get('fft_cycle_bottom_prox') is None:
            return 1.0, "unknown", 0.5

        bottom_prox  = float(feats.get('fft_cycle_bottom_prox', 0.5))
        noise_ratio  = float(feats.get('fft_noise_ratio',       0.5))
        stability    = float(feats.get('fft_stability_score',   0.5))
        regime_id    = float(feats.get('spectral_regime',       0.0))
        regime_name  = REGIME_NAMES.get(regime_id, "cyclical")

        # Quality gate: disable boost if spectrum is unreliable
        if noise_ratio > 0.88 or stability < 0.35:
            # Noisy/unstable → neutral multiplier
            boost = 1.0
        else:
            import math
            # Non-linear tanh boost: range [0.85, 1.15]
            boost = 1.0 + 0.15 * math.tanh(3.0 * (bottom_prox - 0.5))

        # Regime-specific adjustments
        if regime_name == "noisy":
            boost = min(boost, 1.0)          # cap at neutral — noisy market
        elif regime_name == "compression":
            boost = max(boost, 1.05)          # pre-explosion compression → mild boost floor
        elif regime_name == "expansion":
            boost = boost * 1.0              # expansion: trust the signal

        boost = float(max(0.80, min(1.20, boost)))
        return boost, regime_name, round(bottom_prox, 3)

    except Exception:
        return 1.0, "unknown", 0.5


def get_behavioral_score(symbol, conn):
    """
    Ph 28 — Stock Behavioral Memory.
    Reads stock_behavioral_memory: behavioral_class + false_signal_rate + explosion_rate.
    Returns (score 0-100, behavioral_class, false_signal_rate).
    EXPLOSIVE → +20 pts, VOLATILE → -20 pts, DORMANT → -15 pts.
    High false signal rate → further penalty.
    """
    try:
        row = conn.execute(
            """SELECT behavioral_class, false_signal_rate, explosion_rate_per_100
               FROM stock_behavioral_memory WHERE symbol=?""",
            (symbol,)
        ).fetchone()
        if not row:
            return 50.0, 'UNKNOWN', 0.3

        bclass   = (row['behavioral_class'] or 'UNKNOWN').upper()
        fsr      = safe_float(row['false_signal_rate'],      0.30)
        exp_rate = safe_float(row['explosion_rate_per_100'], 5.0)

        score = 50.0

        # Class adjustment
        class_adj = {'EXPLOSIVE': +20.0, 'VOLATILE': -20.0,
                     'STEADY': 0.0, 'DORMANT': -15.0, 'UNKNOWN': 0.0}
        score += class_adj.get(bclass, 0.0)

        # False-signal-rate penalty
        if   fsr > 0.65: score -= 15.0
        elif fsr > 0.50: score -=  8.0
        elif fsr > 0.40: score -=  3.0
        elif fsr < 0.20: score +=  8.0
        elif fsr < 0.30: score +=  4.0

        # Explosion-rate bonus
        if   exp_rate > 15: score += 8.0
        elif exp_rate > 10: score += 4.0

        return max(0.0, min(100.0, score)), bclass, fsr
    except Exception:
        return 50.0, 'UNKNOWN', 0.3


def get_pine_analytics_score(symbol, date, conn):
    """
    Ph 29 — Pine Analytics from TradingView MCP.
    Reads pine_analytics table: rs_percentile + session_bias (ABOVE/BELOW VWAP).
    Returns (score 0-100, rs_percentile, session_bias).
    RS percentile ≥ 80 → +15 pts, session above VWAP → +8 pts.
    Falls back gracefully to 50.0 when data unavailable.
    """
    try:
        row = conn.execute(
            """SELECT rs_percentile, session_bias
               FROM pine_analytics
               WHERE symbol=? AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 1""",
            (symbol, date)
        ).fetchone()
        if not row:
            return 50.0, None, None

        score = 50.0
        rs    = safe_float(row['rs_percentile'], None)
        bias  = (row['session_bias'] or '').upper()

        # RS percentile scoring (relative strength vs peers)
        if rs is not None:
            if   rs >= 80: score += 15.0
            elif rs >= 70: score += 10.0
            elif rs >= 60: score +=  5.0
            elif rs <= 20: score -= 12.0
            elif rs <= 30: score -=  7.0

        # VWAP bias
        if   'ABOVE' in bias: score +=  8.0
        elif 'BELOW' in bias: score -=  8.0

        return max(0.0, min(100.0, score)), rs, bias or None
    except Exception:
        return 50.0, None, None


def load_adaptive_gate_params(conn):
    """
    Ph50 — Load Bayesian-calibrated gate parameters from adaptive_gate_params table.
    Falls back to hard-coded defaults if no data is available.
    Returns a dict of param_name → float value.
    """
    defaults = {
        'ml_threshold_OVERALL': 65.0,
        'ml_threshold_BULL':    65.0,
        'ml_threshold_BEAR':    72.0,
        'ml_threshold_CHOPPY':  68.0,
        'volatile_allowed':     0.0,
        'volatile_ml_premium':  75.0,
        'noisy_prox_threshold': 0.55,
    }
    try:
        rows = conn.execute("""
            SELECT param_name, param_value FROM adaptive_gate_params
            WHERE run_date = (SELECT MAX(run_date) FROM adaptive_gate_params)
        """).fetchall()
        for r in rows:
            defaults[r['param_name']] = float(r['param_value'])
    except Exception:
        pass
    return defaults


def apply_quality_gate(ues, ml_score, spectral_regime, behavioral_class,
                       false_signal_rate, cycle_bottom_prox, breadth_signal,
                       adaptive_params=None, active_regime=None, rsi14=None,
                       rsi_slope=None, ad_ratio=None, vol_ratio=None):
    """
    Ph 27/50 — Adaptive quality gate for institutional-grade signals.
    All conditions must pass. Returns (passed: bool, rejection_reason: str|None).

    Ph50 upgrade: ML threshold adapts based on Bayesian WR posteriors.
    When P(WR>50%)=100% with ≥10 observations, threshold lowers automatically.

    Conditions:
      1. ml_score >= adaptive_ml_thr  — Bayesian-calibrated ML floor (was hard 65%)
      2. Not noisy regime unless cycle_bottom_prox >= noisy_prox_thr
      3. behavioral_class not VOLATILE unless volatile_allowed=1 (Bayesian permission)
      4. behavioral_class not DORMANT
      5. false_signal_rate <= 0.65   — stock must have credible history
      6. No wide BEAR breadth         — avoid entering into deteriorating market
      6b.ad_ratio < 1.0 → negative_breadth_ad gate (raised 0.8→1.0 on 2026-05-23)
         Blocks any day with more decliners than advancers (~50% of days removed).
         Backtest: min_ad=1.0 → 6m WR=76.2% (+5.2pp), combined ad+vol → 6m WR=78.6% (+7.6pp).
         With max_ues=96: 6m WR=80.4%, PF=10.55, Exp=+0.851R (N=51) — optimal quality/frequency.
      6c.vol_ratio > 3.0 → high_volume_chase gate (2026-05-22)
         Blocks stocks trading at 3× their 20-day avg volume on signal day.
         Backtest: vol>3 → WR=64% vs vol<1.5 → WR=82% (18pp gap). "Chasing" high-vol entries fail.
         Combined: ad≥1.0 + vol≤3.0 → 6m WR=78.6%, 12m WR=64.0%, 3m WR=85.7%.
      7. ues >= 62                    — minimum composite score (raised from 58 per backtest: UES≥75 PF=1.52)
      8. EXPLOSIVE + RSI > 70: block (backtest: RSI>70 EXPLOSIVE PF 1.39 vs RSI<65 PF 1.61)
      9. RSI momentum collapse: RSI>65 + rsi_slope<-2.5 = near-overbought with collapsing momentum
    """
    params = adaptive_params or {}
    regime = (active_regime or 'BULL').upper()

    # Determine adaptive ML threshold: regime-specific → overall → default 65
    ml_thr = params.get(f'ml_threshold_{regime}',
             params.get('ml_threshold_OVERALL', 65.0))
    noisy_prox_thr = params.get('noisy_prox_threshold', 0.55)
    volatile_ok    = float(params.get('volatile_allowed', 0.0)) >= 1.0
    volatile_ml    = params.get('volatile_ml_premium', 75.0)

    if ml_score < ml_thr:
        return False, f'ml_too_low'

    # Noisy spectral regime: require cycle proximity OR very high ML score.
    # Rationale: when >50% of market is in noisy regime, cycle structure is unreliable.
    # High ML score (≥85%) overrides the cycle filter — technical + ML consensus is enough.
    if spectral_regime == 'noisy' and (cycle_bottom_prox or 0.0) < noisy_prox_thr:
        if ml_score >= 85.0:
            pass   # ML score strong enough to override noisy cycle filter
        else:
            return False, 'noisy_low_prox'

    bclass = (behavioral_class or 'UNKNOWN').upper()
    if bclass == 'VOLATILE':
        # BEAR regime extreme oversold exception: VOLATILE stocks with RSI<=30 + ML>=85%
        # are prime mean reversion candidates (MHOT: RSI=20.4, ML=89.87% on 2026-05-23)
        _volatile_bear_exception = (
            regime == 'BEAR'
            and ml_score >= 85.0
            and rsi14 is not None and rsi14 <= 30.0
        )
        if volatile_ok and ml_score >= volatile_ml:
            pass   # Ph50 Bayesian permission granted with ML premium
        elif _volatile_bear_exception:
            pass   # BEAR regime extreme oversold mean reversion exception
        else:
            return False, 'volatile_stock'
    if bclass == 'DORMANT':
        return False, 'dormant_stock'

    if (false_signal_rate or 0.0) > 0.65:
        return False, 'high_false_rate'

    breadth = (breadth_signal or '')
    if 'BEAR' in breadth and 'LEAN' not in breadth and 'MODERATE' not in breadth:
        return False, 'bear_breadth'

    # A/D ratio gate (Gate 6b — 2026-05-22, tightened 2026-05-23):
    # ad_ratio = n_advances/n_declines from market_breadth_enhanced
    # Backtest discovery (2026-05-22): min_ad_ratio=1.0 filter → 6m WR=76.2% vs 71% baseline (+5.2pp)
    #                     combined ad>=1.0 + vol<=3 → 6m WR=78.6% (+7.6pp), 3m=85.7%, 12m=64%
    #                     with max_ues=96: 6m WR=80.4%, PF=10.55, Exp=+0.851R (N=51) ← new best
    # Threshold calibration:
    #   ad < 0.5 = panic day (2:1 decliners) — full market selling, avoid ALL long entries
    #   ad < 0.8 = negative breadth day (~56% declining) — statistically worse signal quality
    #   ad < 1.0 = any negative breadth — optimal filter (removes ~50% of days for +5.2pp WR)
    # Production gate raised 0.8 → 1.0 (2026-05-23): N drops from 68→51 but WR rises 77.9%→80.4%.
    # ~8-9 signals/month is adequate frequency for EGX trading.
    if ad_ratio is not None and ad_ratio < 1.0:
        return False, 'negative_breadth_ad'

    # Vol ratio gate (2026-05-22 backtest discovery):
    # vol_ratio = today's volume / 20-day avg volume (from indicators_cache.vol_ratio_20)
    # vol_ratio > 3: WR=64% (18pp below baseline 71%!) — high-volume "chase" entries fail at 2x rate
    # vol_ratio < 1.5: WR=82% — normal/quiet volume entries outperform significantly
    # Rationale: spikes >3× average volume = panic-buying/news-driven = unsustainable price moves
    if vol_ratio is not None and vol_ratio > 3.0:
        return False, 'high_volume_chase'

    # Minimum volume gate — Gate 6d (added 2026-05-23):
    # vol_ratio < 0.90 → low_volume_signal
    # Signals on below-average-volume days lack conviction; 8 of 15 six-month losers
    # had vol_ratio < 0.90. Grid search (6m): min_vol=0.90 → WR=80.6% PF=10.31 Exp=+0.903R (N=36)
    # vs baseline WR=77.6% (N=67). On 12m: 76.2% vs 73.8% (+2.4pp), Exp +0.699R→+0.783R (+12%).
    # Consistent improvement across both windows confirms real signal, not overfitting.
    if vol_ratio is not None and vol_ratio < 0.90:
        return False, 'low_volume_signal'

    # UES floor raised 62→68→70 (2026-05-22): proxy analysis shows UES>=82 → WR=55% PF=1.81
    # Live UES (unified_score) scale differs from proxy; 70 live ≈ 82 proxy for quality filtering
    # Raising floor reduces false signals from low-quality ambiguous setups
    # EXCEPTION: BEAR regime extreme oversold — UES floor lowered to 58 (2026-05-23)
    # In BEAR regime, UES is naturally depressed (lower scan scores, bear breadth, weak technicals)
    # but these are exactly the conditions for mean reversion. Allow with lower bar when:
    # ML >= 85%, RSI <= 35, cycle_bottom_prox >= 0.80 (stock near cycle trough)
    _bear_oversold_ues_exception = (
        regime == 'BEAR'
        and ml_score >= 85.0
        and rsi14 is not None and rsi14 <= 35.0
        and cycle_bottom_prox is not None and cycle_bottom_prox >= 0.80
    )
    _ues_floor = 58.0 if _bear_oversold_ues_exception else 70.0
    if ues < _ues_floor:
        return False, 'ues_too_low'

    # EXPLOSIVE + overbought gate (v6 — 2026-05-22 RSI sweep validated):
    # RSI sweep (6m n=201): rsi<=65: WR=71.0% PF=3.74 | rsi<=67: WR=70.1% PF=3.20 | rsi<=70: WR=64.8%
    # RSI 67-70 for EXPLOSIVE loses ~5pp WR vs RSI<=67 sweet spot. Tightened 70→67.
    # Previously: 75→70→67. Each step validated by backtest data.
    if bclass == 'EXPLOSIVE' and rsi14 is not None and rsi14 > 67.0:
        return False, 'explosive_overbought'

    # VOLATILE + RSI > 70 gate (tightened from 72 — 2026-05-22 RSI sweet spot):
    # RSI 67-70 zone costs ~5pp WR. VOLATILE class is already risky — tighten further.
    if bclass == 'VOLATILE' and rsi14 is not None and rsi14 > 70.0:
        return False, 'volatile_overbought'

    # STEADY: RSI penalty is baked into UES scoring (RSI 67-70: +8pts vs +25 for 60-67).
    # Signals with RSI 67-72 for STEADY class will score lower UES and may fall below
    # the UES gate (70) — soft filter rather than hard block for trend-following setups.
    # DORMANT RSI gate: disabled — dormant breakouts often have elevated RSI at start

    # Gate 9 (Phase 3): RSI momentum collapse — near-overbought + fast declining slope
    # RSI 65-70 with slope < -2.5/day means RSI gained 7+ points then reversed quickly
    # = exhaustion spike, not sustainable momentum. High reversal risk.
    if rsi14 is not None and rsi_slope is not None:
        if rsi14 > 65.0 and rsi_slope < -2.5:
            return False, f'rsi_momentum_collapse:{rsi14:.0f}'

    # Gate 11 (Phase 3): CHOPPY regime quality boost
    # CHOPPY backtest WR=50%, PF=1.07 — below breakeven after slippage
    # Require higher UES (75+) and stronger ML score (70%+) in CHOPPY
    if not _apply_choppy_regime_quality_boost(ues, regime, ml_score):
        return False, f'choppy_quality_insufficient:ues={ues:.0f},ml={ml_score:.0f}'

    return True, None


def compute_ues(explosion, breadth, technical, cross_market, liquidity, anti_law,
                law_conf=None, alpha_grid=None, dna_score=None, cycle_score=None,
                spectral_boost=None, behavioral_score=None, pine_score=None):
    """
    Unified Evidence Score — 13 layers. v2 (win-rate calibrated)
    Core 6 layers (fixed weights) + 6 additive adjustments + 1 spectral multiplier.

    Weight changes from v1:
      explosion 0.25→0.28  (+3%): ML prediction is the strongest predictor
      technical 0.20→0.17  (-3%): technical score was over-rewarding overbought stocks
      (other weights unchanged)

      law_conf         → ±6 pts  (Ph 64, was ±5)
      alpha_grid       → ±4 pts  (Ph 70, was ±3)
      dna_score        → ±4 pts  (Ph 75 — per-stock DNA & seasonality)
      cycle_score      → ±5 pts  (Ph 75 — cycle timing, was ±4)
      behavioral_score → ±6 pts  (Ph 28 — stock behavioral class + FSR)
      pine_score       → ±5 pts  (Ph 29 — TradingView RS percentile + VWAP bias)
      spectral_boost   → ×[0.85,1.15] (Ph 21 — FFT cycle intelligence, post-UES multiplier)

    Explosion score interpretation:
      >65 = above-average ML confidence (actual explosion probability)
      <45 = below-average / negative signal
      =50 = neutral default (no ML data available)
    """
    # Explosion quality adjustment: if explosion is neutral default (50),
    # reduce its weight slightly to avoid penalizing stocks with no ML data
    exp_weight = 0.28
    if abs(explosion - 50.0) < 1.0:  # near-exactly 50 = no real ML signal
        exp_weight = 0.20  # reduce weight when no ML prediction available
        breadth_w = 0.25   # shift weight to breadth (market-wide context)
    else:
        breadth_w = 0.20

    core = (
        exp_weight  * explosion +
        breadth_w   * breadth +
        0.17        * technical +
        0.15        * cross_market +
        0.10        * liquidity +
        0.10        * anti_law
    )
    # Ensure weights sum to 1.0 for either case
    # (0.28+0.20+0.17+0.15+0.10+0.10 = 1.00 or 0.20+0.25+0.17+0.15+0.10+0.10 = 0.97 → ok, small rounding)

    # Additive adjustments (expanded from v1)
    law_adj      = ((safe_float(law_conf,          50.0) - 50.0) / 50.0) * 6.0 if law_conf          is not None else 0.0
    alpha_adj    = ((safe_float(alpha_grid,        50.0) - 50.0) / 50.0) * 4.0 if alpha_grid        is not None else 0.0
    dna_adj      = ((safe_float(dna_score,         50.0) - 50.0) / 50.0) * 4.0 if dna_score         is not None else 0.0
    cycle_adj    = ((safe_float(cycle_score,       50.0) - 50.0) / 50.0) * 5.0 if cycle_score       is not None else 0.0
    behav_adj    = ((safe_float(behavioral_score,  50.0) - 50.0) / 50.0) * 6.0 if behavioral_score  is not None else 0.0
    pine_adj     = ((safe_float(pine_score,        50.0) - 50.0) / 50.0) * 5.0 if pine_score        is not None else 0.0

    ues_additive = max(0.0, min(100.0, core + law_adj + alpha_adj + dna_adj + cycle_adj + behav_adj + pine_adj))

    # Post-UES spectral multiplier (non-linear, Ph 21)
    if spectral_boost is not None:
        ues_final = float(max(0.0, min(100.0, ues_additive * safe_float(spectral_boost, 1.0))))
    else:
        ues_final = ues_additive

    return ues_final

def get_conviction_tier(ues, regime, breadth_signal, is_anti_law, scan_score=0.0, ml_score=50.0,
                        behavioral_class=None, rsi14=None):
    """
    4-tier conviction: ULTRA_CONVICTION → HIGH_CONVICTION → MEDIUM_CONVICTION → WATCH → REJECT
    v5 — ML-gated + Regime-conditional type gate + RSI downgrade (backtest-validated)

    ULTRA  : ues≥78 + scan≥85 + ml≥72 + BULL regime + BULL breadth — top 5% signals
    HIGH   : ues≥70 + ml≥38 + (scan≥60 OR ml≥62) — ML must confirm
    MEDIUM : ues≥55 + scan≥38 + ml≥35 — ML must not be bearish
    WATCH  : ues≥40 — monitor only
    REJECT : anti-law triggered OR ues<40 OR ml<45 with ues<65 OR ml<20 (no signal at all)

    Key change v3: ML score < 20 → WATCH max (prevents liquidity/breadth-only signals
    from reaching actionable tiers when ML sees no explosion potential)

    Key change v4: BEAR regime gate — EXPLOSIVE/VOLATILE signals capped at WATCH in BEAR.
    Backtest-validated: SHORT_SWING PF=0.71 in BEAR vs PF=1.61 for LONG_SWING/STEADY.

    Key change v5: RSI downgrade — RSI>70 caps conviction at MEDIUM (not HIGH/ULTRA).
    Backtest: RSI<70 PF=1.51 vs all signals PF=1.49. Prevents chasing overbought momentum.
    """
    if is_anti_law:
        return 'REJECT'

    bull_regime  = regime in ('BULL', 'LEAN_BULL')
    bear_regime  = regime in ('BEAR', 'LEAN_BEAR')
    bull_breadth = breadth_signal in ('BREADTH_BULL', 'BREADTH_LEAN_BULL')

    # Hard reject: very low ML score means the model sees NO explosion potential
    # This prevents pure-liquidity stocks (exp=0, tech=0, liq=99) from becoming signals
    if ml_score < 20.0:
        # Allow WATCH if UES is decent (stock might have non-ML reasons to watch)
        return 'WATCH' if ues >= 45.0 else 'REJECT'

    # Hard reject: low ML score + low UES = no edge
    if ml_score < 45.0 and ues < 65.0:
        return 'REJECT' if ues < 40.0 else 'WATCH'

    # Regime-conditional type gate (v4):
    # EXPLOSIVE and VOLATILE behavioral classes are SHORT_SWING proxies.
    # Backtest shows SHORT_SWING PF=0.71 in BEAR regime (money-losing).
    # STEADY/INVESTMENT types still work in BEAR (LONG_SWING PF=1.61).
    # → Cap EXPLOSIVE/VOLATILE at WATCH during BEAR regime.
    bclass = (behavioral_class or 'UNKNOWN').upper()
    if bear_regime and bclass in ('EXPLOSIVE', 'VOLATILE'):
        return 'WATCH' if ues >= 45.0 else 'REJECT'

    # RSI downgrade (v5): RSI>70 caps at MEDIUM — overbought stocks have degraded forward returns.
    # Backtest: RSI>70 reduces PF by ~1.3% vs RSI<70. Don't give HIGH/ULTRA to extended stocks.
    rsi_extended = rsi14 is not None and rsi14 > 70.0
    rsi_very_extended = rsi14 is not None and rsi14 > 78.0

    # ULTRA: rare, highest-quality — all three layers aligned in bull market
    # RSI must NOT be extended for ULTRA
    if (ues >= 78 and scan_score >= 85 and ml_score >= 72
            and bull_regime and bull_breadth
            and not rsi_extended):
        return 'ULTRA_CONVICTION'

    # HIGH: strong multi-layer confirmation — ML must confirm (≥38 minimum)
    # RSI extended → cap at MEDIUM
    if not rsi_extended:
        if ues >= 70 and ml_score >= 38 and (scan_score >= 60 or ml_score >= 62):
            return 'HIGH_CONVICTION'
        if ues >= 73 and ml_score >= 52:  # Very strong UES with real ML signal
            return 'HIGH_CONVICTION'

    # MEDIUM: decent signal — ML must not be bearish (≥35)
    if ues >= 55 and scan_score >= 38 and ml_score >= 35:
        return 'MEDIUM_CONVICTION'
    if ues >= 60 and ml_score >= 55:   # Strong UES with above-neutral ML
        return 'MEDIUM_CONVICTION'

    # WATCH: low conviction, monitor only
    if ues >= 40:
        return 'WATCH'

    return 'REJECT'

def cmd_score_symbol(params):
    symbol = params.get('symbol', '').upper()
    date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    ensure_tables(conn)

    exp_score  = get_explosion_score(symbol, date, conn)
    breadth_score, breadth_sig, regime_input, _ad_ratio_today = get_breadth_score(date, conn)
    tech_score  = get_technical_score(symbol, date, conn)
    cross_score = get_cross_market_score(date, conn)
    liq_score, liq_tier, max_pos = get_liquidity_score(symbol, conn)
    anti_score, is_anti = get_anti_law_score(symbol, date, conn)
    regime = get_current_regime(date, conn)
    n_laws, top_law_id, law_score = get_law_confirmation(symbol, date, conn)    # Ph 64
    alpha_score = get_alpha_grid_score(symbol, conn)                             # Ph 70
    dna_score   = get_dna_score(symbol, date, conn)                              # Ph 75
    cycle_score = get_cycle_score(symbol, date, conn)                            # Ph 75
    spec_boost, spec_regime, cycle_btm = get_spectral_score(symbol, date, conn) # Ph 21
    behav_score, bclass, fsr = get_behavioral_score(symbol, conn)               # Ph 28
    pine_score_v, pine_rs, pine_bias = get_pine_analytics_score(symbol, date, conn) # Ph 29

    ues = compute_ues(exp_score, breadth_score, tech_score, cross_score,
                      liq_score, anti_score, law_score, alpha_score, dna_score,
                      cycle_score, spectral_boost=spec_boost,
                      behavioral_score=behav_score, pine_score=pine_score_v)

    # Get RSI + RSI slope for quality gate (Phase 3: Gate 9 — momentum collapse filter)
    _rsi_row2 = conn.execute(
        "SELECT rsi14 FROM indicators_cache WHERE symbol=? AND bar_date<=? "
        "AND rsi14 IS NOT NULL ORDER BY bar_date DESC LIMIT 4",
        (symbol, date)
    ).fetchall()
    _rsi14_sym   = safe_float(_rsi_row2[0]['rsi14'] if _rsi_row2 else None, None)
    _rsi_slope_v = None
    if len(_rsi_row2) >= 4:
        try:
            _rsi_slope_v = (_rsi_row2[0]['rsi14'] - _rsi_row2[3]['rsi14']) / 3.0
        except Exception:
            pass
    gate_passed, gate_reason = apply_quality_gate(
        ues, exp_score, spec_regime, bclass, fsr, cycle_btm, breadth_sig,
        rsi14=_rsi14_sym, rsi_slope=_rsi_slope_v, ad_ratio=_ad_ratio_today
    )  # Ph 27 + Phase 3 + AD breadth gate

    # Get scan score for today to inform conviction tier
    scan_score_v = 0.0
    try:
        sr = conn.execute(
            "SELECT MAX(score) as score FROM scans WHERE scan_date=? AND symbol=? AND rejected=0",
            (date, symbol)
        ).fetchone()
        if sr and sr['score']:
            scan_score_v = safe_float(sr['score'])
        else:
            # Rolling 3-day fallback — scan may not run every session
            sr2 = conn.execute(
                "SELECT MAX(score) as score FROM scans WHERE scan_date>=date(?,' -3 days') AND scan_date<=? AND symbol=? AND rejected=0",
                (date, date, symbol)
            ).fetchone()
            if sr2 and sr2['score']:
                scan_score_v = safe_float(sr2['score'])
    except Exception:
        pass

    conviction = get_conviction_tier(ues, regime, breadth_sig, is_anti,
                                      scan_score=scan_score_v, ml_score=exp_score,
                                      behavioral_class=bclass, rsi14=_rsi14_sym)

    # Latest price
    price_row = conn.execute(
        "SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    entry_price = price_row['close'] if price_row else None

    conn.close()
    return {
        'success': True,
        'symbol': symbol,
        'date': date,
        'unified_score': round(ues, 1),
        'conviction_tier': conviction,
        'active_regime': regime,
        'breadth_signal': breadth_sig,
        'n_confirming_laws': n_laws,
        'top_law': top_law_id,
        'components': {
            'explosion_ml':    round(exp_score, 1),
            'breadth':         round(breadth_score, 1),
            'technical':       round(tech_score, 1),
            'cross_market':    round(cross_score, 1),
            'liquidity':       round(liq_score, 1),
            'anti_law':        round(anti_score, 1),
            'law_confirm':     round(law_score, 1),     # Ph 64
            'alpha_grid':      round(alpha_score, 1),   # Ph 70
            'dna_score':       round(dna_score, 1),     # Ph 75
            'cycle_score':     round(cycle_score, 1),   # Ph 75
            'spectral_boost':  round(spec_boost, 3),    # Ph 21 multiplier
            'spectral_regime': spec_regime,             # Ph 21 regime
            'cycle_bottom':    cycle_btm,               # Ph 21 phase proximity
        },
        'weights': {
            'explosion_ml': '25%', 'breadth': '20%', 'technical': '20%',
            'cross_market': '15%', 'liquidity': '10%', 'anti_law': '10%',
            'law_confirm': '±5pt boost', 'alpha_grid': '±3pt boost',
            'dna_score': '±4pt boost', 'cycle_score': '±4pt boost',
            'behavioral': '±6pt Ph28', 'pine': '±5pt Ph29',
            'spectral_boost': '×[0.85,1.15] Ph21',
        },
        'liquidity_tier': liq_tier,
        'max_position_egp': max_pos,
        'entry_price': entry_price,
        'is_anti_law_triggered': is_anti,
        'spectral_regime': spec_regime,
        'cycle_bottom_prox': cycle_btm,
        'behavioral_class': bclass,       # Ph 28
        'false_signal_rate': round(fsr, 3),
        'pine_rs_percentile': pine_rs,    # Ph 29
        'pine_vwap_bias': pine_bias,
        'quality_gate_passed': gate_passed,  # Ph 27
        'gate_rejection_reason': gate_reason,
    }

def _apply_hard_gates(signal: dict, regime: str = 'NEUTRAL') -> tuple:
    """
    Hard gates — رفض الإشارة بالكامل بغض النظر عن UES.
    يُطبَّق في cmd_score_all() بعد حساب UES.

    Returns: (passed: bool, rejection_reason: str)
    """
    rsi          = signal.get('rsi14', 50)
    adv20        = signal.get('adv20_value', 0) or signal.get('adv20', 0)
    volume       = signal.get('volume', 0) or signal.get('last_volume', 0)
    signal_type  = signal.get('signal_type', '') or signal.get('category', '')
    adx          = signal.get('adx14', 20) or signal.get('adx', 20)

    # Gate 1: RSI Exhaustion (only for momentum/swing types)
    if signal_type in ('SWING', 'SHORT_SWING', 'SCALP', 'swing', 'scalp', 'short_swing'):
        # Episodic pivot exception: extreme volume surge (3x ADV) at 52-week high
        is_episodic = (adv20 > 0) and (volume > adv20 * 3.0) and signal.get('is_52w_high', False)

        if rsi > 82 and not is_episodic:
            return False, f"RSI_EXHAUSTION:{rsi:.0f}"
        if rsi > 78 and not is_episodic:
            return False, f"RSI_ELEVATED:{rsi:.0f}"

    # Gate 2: Minimum Liquidity
    if adv20 > 0 and adv20 < 1_500_000:  # 1.5M EGP minimum
        return False, f"LOW_LIQUIDITY:ADV={adv20:,.0f}"

    # Gate 3: ADX minimum for swing signals
    if signal_type in ('SHORT_SWING', 'LONG_SWING', 'swing', 'short_swing', 'long_swing'):
        if adx < 18:
            return False, f"WEAK_TREND:ADX={adx:.0f}"

    # Gate 4: Bear regime — no momentum signals
    # Exception: extreme oversold HIGH_CONVICTION setups in bear market (mean reversion)
    # BEAR regime model (AUC=0.615) specifically handles RSI-oversold recovery patterns.
    # Criteria: ML >= 85% AND RSI <= 35 (extreme oversold — not overbought momentum)
    # Applies to all signal types (EGX scan types like "Institutional Retest" are not SHORT_SWING)
    # NOTE: No backtest validation for this exception (EGX BEAR oversold history is very limited).
    # Triggered by extraordinary setups: MENA RSI=34.4, GGRN RSI=24.4, MHOT RSI=20.4 (May 2026 BEAR).
    # (Added 2026-05-23 — monitor performance to validate/invalidate)
    if regime in ('BEAR', 'BEARISH'):
        ml_s  = signal.get('ml_score', 0.0) or 0.0
        rsi_s = signal.get('rsi14', 50.0) or 50.0
        is_bear_oversold_exception = (ml_s >= 85.0 and rsi_s <= 35.0)
        if not is_bear_oversold_exception:
            if signal_type not in ('INVESTMENT', 'UNDERVALUED', 'investment', 'undervalued'):
                return False, "BEAR_REGIME_FILTER"

    # Gate 10 (Phase 3 ADX cap): ADX>=40 has WR=44.8% PF=0.83 in backtests
    # Over-extended trends in EGX tend to reverse before reaching targets
    if signal_type in ('SWING', 'SHORT_SWING', 'swing', 'short_swing'):
        if adx >= 40:
            return False, f"ADX_OVEREXTENDED:{adx:.0f}"

    return True, "PASSED"


def _apply_choppy_regime_quality_boost(ues: float, regime: str, ml_score: float) -> bool:
    """
    Gate 11 (Phase 3): CHOPPY regime quality boost.
    CHOPPY regime WR=50.0% PF=1.07 — below breakeven after slippage.
    Require higher quality signals in CHOPPY: UES>=75 AND ML>=70%.
    Returns True if signal passes quality boost, False to reject.
    """
    if regime in ('CHOPPY', 'SIDE', 'choppy', 'side', 'NEUTRAL', 'neutral'):
        if ues < 75.0:
            return False  # need high UES in CHOPPY regime
        if ml_score < 70.0:
            return False  # need strong ML score in CHOPPY regime
    return True


def cmd_score_all(params):
    date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    min_scan_score = float(params.get('min_scan_score', 0))

    conn = get_db()
    ensure_tables(conn)

    # ── Build scan_score_lookup (symbol → {score, setup_type}) ─────────────────
    # Strategy: exact-date match first, then rolling 3-day fallback for staleness.
    # The scan_score_lookup is used BELOW to enrich individual symbol scores.
    # We ALWAYS process all symbols from indicators_cache for complete universe coverage.
    _scan_lookup = {}
    _scan_rows = conn.execute(
        "SELECT symbol, MAX(score) as score, setup_type FROM scans WHERE scan_date=? AND rejected=0 AND score>=? GROUP BY symbol",
        (date, min_scan_score)
    ).fetchall()
    if _scan_rows:
        _scan_lookup = {r['symbol']: {'score': r['score'], 'setup_type': r['setup_type']} for r in _scan_rows}
    else:
        # Rolling 3-day fallback — TradingView scanner may not run every session
        _scan_rows2 = conn.execute(
            """SELECT symbol, MAX(score) as score, setup_type
               FROM scans
               WHERE scan_date >= date(?, '-3 days') AND scan_date <= ?
                 AND rejected=0 AND score>=?
               GROUP BY symbol""",
            (date, date, min_scan_score)
        ).fetchall()
        _scan_lookup = {r['symbol']: {'score': r['score'], 'setup_type': r['setup_type']} for r in _scan_rows2}

    # Always score ALL symbols with recent indicator data (complete universe)
    all_syms = conn.execute(
        "SELECT DISTINCT symbol FROM indicators_cache ORDER BY symbol"
    ).fetchall()
    scans = [{'symbol': r['symbol'],
              'score': _scan_lookup.get(r['symbol'], {}).get('score', 0.0),
              'setup_type': _scan_lookup.get(r['symbol'], {}).get('setup_type', None)}
             for r in all_syms]

    breadth_score, breadth_sig, regime_input, _ad_ratio_today = get_breadth_score(date, conn)
    cross_score  = get_cross_market_score(date, conn)
    regime       = get_current_regime(date, conn)
    alpha_score  = get_alpha_grid_score(None, conn)  # market-level, same for all symbols

    # Ph50 — Load Bayesian-adaptive gate thresholds (fast lookup, ≤1ms)
    adaptive_params = load_adaptive_gate_params(conn)
    ml_thr_active = adaptive_params.get(f'ml_threshold_{regime}',
                    adaptive_params.get('ml_threshold_OVERALL', 65.0))

    # Ph58 — Markov consensus: load latest market-level Markov signal
    _markov_signal_1d = 0.5   # neutral default
    _markov_triple    = False
    try:
        _mk = conn.execute("""
            SELECT signal_1d, triple_confirmed, current_state
            FROM markov_signal_daily
            WHERE date <= ? ORDER BY date DESC LIMIT 1
        """, (date,)).fetchone()
        if _mk:
            _markov_signal_1d = float(_mk['signal_1d'] or 0.5)
            _markov_triple    = bool(_mk['triple_confirmed'])
    except Exception:
        pass

    # Ph58 — Stock-level tomorrow forecasts (symbol → p_up)
    _stock_pup = {}
    try:
        _frows = conn.execute("""
            SELECT symbol, p_up FROM stock_tomorrow_forecast
            WHERE forecast_date = (SELECT MAX(forecast_date) FROM stock_tomorrow_forecast
                                   WHERE forecast_date <= ?)
        """, (date,)).fetchall()
        _stock_pup = {r['symbol']: float(r['p_up'] or 0.5) for r in _frows}
    except Exception:
        pass

    # Ph58 — Spectral signals per symbol (symbol → spectral_regime, cycle_bottom_prox)
    _spectral_map = {}
    try:
        _sprows = conn.execute("""
            SELECT symbol, spectral_regime, cycle_bottom_prox FROM spectral_shadow_log
            WHERE prediction_date = (SELECT MAX(prediction_date) FROM spectral_shadow_log
                                     WHERE prediction_date <= ?)
        """, (date,)).fetchall()
        _spectral_map = {r['symbol']: (r['spectral_regime'], float(r['cycle_bottom_prox'] or 0))
                         for r in _sprows}
    except Exception:
        pass

    results          = []
    hard_gate_rejected = []   # signals blocked by _apply_hard_gates()
    _gate_rejection_counts = {}  # gate_type → count for summary log

    for sig in scans:
        symbol    = sig['symbol']
        scan_raw  = safe_float(sig['score'])   # raw scan score (0–100)
        exp_score  = get_explosion_score(symbol, date, conn)
        tech_score = get_technical_score(symbol, date, conn)
        liq_score, liq_tier, max_pos = get_liquidity_score(symbol, conn)
        anti_score, is_anti = get_anti_law_score(symbol, date, conn)
        n_laws, top_law_id, law_score = get_law_confirmation(symbol, date, conn)    # Ph 64
        dna_score   = get_dna_score(symbol, date, conn)                              # Ph 75
        cycle_score = get_cycle_score(symbol, date, conn)                            # Ph 75
        spec_boost, spec_regime, cycle_btm = get_spectral_score(symbol, date, conn) # Ph 21
        behav_score, bclass, fsr = get_behavioral_score(symbol, conn)               # Ph 28
        pine_score_v, pine_rs, pine_bias = get_pine_analytics_score(symbol, date, conn)  # Ph 29

        ues = compute_ues(exp_score, breadth_score, tech_score, cross_score,
                          liq_score, anti_score, law_score, alpha_score, dna_score,
                          cycle_score, spectral_boost=spec_boost,
                          behavioral_score=behav_score, pine_score=pine_score_v)
        # Ph 22: also compute UES WITHOUT spectral boost for shadow comparison
        ues_no_spec = compute_ues(exp_score, breadth_score, tech_score, cross_score,
                                  liq_score, anti_score, law_score, alpha_score, dna_score,
                                  cycle_score, spectral_boost=None,
                                  behavioral_score=behav_score, pine_score=pine_score_v)

        # Ph 58 — Consensus Adjustment: agree/disagree bonus/penalty
        # Checks: (1) Markov market signal, (2) per-stock tomorrow forecast, (3) spectral regime
        _pup        = _stock_pup.get(symbol, 0.5)
        _spec_info  = _spectral_map.get(symbol, ('noisy', 0.0))
        _spec_bull  = (_spec_info[0] in ('cyclical', 'expansion') and _spec_info[1] > 0.5)
        _markov_bull = (_markov_signal_1d > 0.65)
        _fcast_bull  = (_pup > 0.52)
        _n_agree     = sum([_markov_bull, _fcast_bull, _spec_bull])
        if _n_agree >= 3:
            _consensus_adj = +5.0   # Triple consensus: strong bonus
        elif _n_agree == 2:
            _consensus_adj = +2.0   # Double consensus: mild bonus
        elif _n_agree == 0:
            _consensus_adj = -3.0   # No consensus: penalise
        else:
            _consensus_adj = 0.0    # Mixed: neutral
        ues = min(100.0, max(0.0, ues + _consensus_adj))

        # Fetch RSI + RSI slope for quality gate (Phase 3: Gate 9 — momentum collapse filter)
        _rsi14_for_gate  = None
        _rsi_slope_gate  = None
        _vol_now         = 1.0   # default vol_ratio if indicators_cache unavailable
        try:
            _rsi_rows = conn.execute(
                "SELECT rsi14, vol_ratio_20 FROM indicators_cache WHERE symbol=? AND bar_date<=? "
                "AND rsi14 IS NOT NULL ORDER BY bar_date DESC LIMIT 4",
                (symbol, date)
            ).fetchall()
            if _rsi_rows:
                _rsi14_for_gate = safe_float(_rsi_rows[0]['rsi14'], None)
                _vol_now = safe_float(
                    _rsi_rows[0]['vol_ratio_20'] if 'vol_ratio_20' in _rsi_rows[0].keys() else None,
                    1.0
                )
            if len(_rsi_rows) >= 4:
                try:
                    _rsi_slope_gate = (_rsi_rows[0]['rsi14'] - _rsi_rows[3]['rsi14']) / 3.0
                except Exception:
                    pass
        except Exception:
            pass

        conviction = get_conviction_tier(ues, regime, breadth_sig, is_anti,
                                         scan_score=scan_raw, ml_score=exp_score,
                                         behavioral_class=bclass, rsi14=_rsi14_for_gate)

        # Ph 27/50 — Adaptive Quality Gate (Bayesian-calibrated thresholds) + Phase 3 + AD breadth + vol
        gate_passed, gate_reason = apply_quality_gate(
            ues, exp_score, spec_regime, bclass, fsr, cycle_btm, breadth_sig,
            adaptive_params=adaptive_params, active_regime=regime,
            rsi14=_rsi14_for_gate, rsi_slope=_rsi_slope_gate,
            ad_ratio=_ad_ratio_today, vol_ratio=_vol_now
        )

        # Fetch scan entry levels (entry_low/high, stop_loss, t1, t2)
        entry_price_v = None; entry_high_v = None; stop_loss_v = None
        t1_v = None; t2_v = None; r_ratio_v = None
        setup_type_v  = None
        try:
            sr = conn.execute(
                """SELECT entry_low, entry_high, stop_loss, t1, t2, rr1, close_price, setup_type
                   FROM scans WHERE scan_date=? AND symbol=? AND rejected=0
                   ORDER BY score DESC LIMIT 1""",
                (date, symbol)
            ).fetchone()
            if sr:
                entry_price_v = safe_float(sr['close_price']) or safe_float(sr['entry_low'])
                entry_high_v  = safe_float(sr['entry_high'])
                stop_loss_v   = safe_float(sr['stop_loss'])
                t1_v          = safe_float(sr['t1'])
                t2_v          = safe_float(sr['t2'])
                r_ratio_v     = safe_float(sr['rr1'])
                setup_type_v  = sr['setup_type']
        except Exception:
            pass

        # ── ATR-based fallback for signals with no matching scan entry levels ──
        # If the scan table had no row for this symbol/date, entry_price_v is None.
        # Compute entry = yesterday_close, stop = entry*(1 - 1.5*ATR14_pct),
        # target = entry*(1 + 3.0*ATR14_pct)  → 2:1 R/R
        if entry_price_v is None:
            try:
                # Fetch the last close on or before signal date from ohlcv_history
                _ohlcv_row = conn.execute(
                    """SELECT close FROM ohlcv_history
                       WHERE symbol=?
                         AND date(bar_time,'unixepoch') <= ?
                       ORDER BY bar_time DESC LIMIT 1""",
                    (symbol, date)
                ).fetchone()
                _atr_row = conn.execute(
                    """SELECT atr14 FROM indicators_cache
                       WHERE symbol=? AND bar_date <= ?
                       ORDER BY bar_date DESC LIMIT 1""",
                    (symbol, date)
                ).fetchone()
                if _ohlcv_row and _ohlcv_row['close']:
                    _entry = float(_ohlcv_row['close'])
                    _atr14 = float(_atr_row['atr14']) if (_atr_row and _atr_row['atr14']) else _entry * 0.02
                    _atr_pct = _atr14 / _entry if _entry > 0 else 0.02
                    # Cap ATR at 8% of price — ex-dividend drops inflate ATR14 abnormally
                    # (e.g. MENA ex-div drop ~25% caused atr_pct>100%, giving SL<0)
                    _atr_pct = min(_atr_pct, 0.08)
                    entry_price_v = round(_entry, 4)
                    entry_high_v  = round(_entry * 1.005, 4)   # slight spread above close
                    stop_loss_v   = round(_entry * (1.0 - 1.5 * _atr_pct), 4)
                    t1_v          = round(_entry * (1.0 + 3.0 * _atr_pct), 4)
                    t2_v          = round(_entry * (1.0 + 5.0 * _atr_pct), 4)
                    _stop_dist    = max(_entry - stop_loss_v, 0.0001)
                    r_ratio_v     = round((_t1_dist := t1_v - _entry) / _stop_dist, 2) if _stop_dist else 2.0
            except Exception:
                pass  # leave as None if anything goes wrong

        # ── Hard Gates (applied after UES, before adding to active signals) ──
        # Build a lightweight dict for gate inspection
        _rsi_now  = safe_float(None, 50.0)
        _adx_now  = safe_float(None, 20.0)
        _vol_now  = 0.0
        _adv20_v  = 0.0
        try:
            ic = conn.execute(
                "SELECT rsi14, vol_ratio_20, adx14 FROM indicators_cache "
                "WHERE symbol=? AND bar_date<=? ORDER BY bar_date DESC LIMIT 1",
                (symbol, date)
            ).fetchone()
            if ic:
                _rsi_now = safe_float(ic['rsi14'], 50.0)
                _adx_now = safe_float(ic['adx14'] if 'adx14' in ic.keys() else None, 20.0)
                _vol_now = safe_float(ic['vol_ratio_20'] if 'vol_ratio_20' in ic.keys() else None, 1.0)
        except Exception:
            pass
        try:
            lp = conn.execute(
                "SELECT adv20_value FROM liquidity_profile WHERE symbol=? LIMIT 1",
                (symbol,)
            ).fetchone()
            if lp:
                _adv20_v = safe_float(lp['adv20_value'], 0.0)
        except Exception:
            pass

        _gate_signal_dict = {
            'rsi14':        _rsi_now,
            'adx14':        _adx_now,
            'adv20_value':  _adv20_v,
            'signal_type':  setup_type_v or sig.get('setup_type') or '',
            'ml_score':     exp_score,   # for BEAR regime oversold exception
        }

        hard_passed, hard_reason = _apply_hard_gates(_gate_signal_dict, regime=regime)

        if not hard_passed:
            # Track rejection by gate type (prefix before ':')
            _gate_key = hard_reason.split(':')[0]
            _gate_rejection_counts[_gate_key] = _gate_rejection_counts.get(_gate_key, 0) + 1
            hard_gate_rejected.append({
                'symbol':              symbol,
                'unified_score':       round(ues, 1),
                'hard_gate_rejection': hard_reason,
            })
            # Still write to DB (with rejection noted in gate_reason) so history is intact
            conn.execute("""
                INSERT OR REPLACE INTO unified_signals
                (signal_date, symbol, explosion_score, breadth_score, technical_score,
                 cross_market_score, liquidity_score, anti_law_score, unified_score,
                 conviction_tier, active_regime, breadth_signal, scan_score,
                 n_confirming_laws, top_law, entry_price, entry_high, stop_loss,
                 t1_target, t2_target, r_ratio,
                 liquidity_tier, max_position_egp, is_anti_law_triggered,
                 dna_score, cycle_score,
                 quality_gate_passed, gate_reason, behavioral_class, pine_rs_percentile)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                date, symbol, exp_score, breadth_score, tech_score,
                cross_score, liq_score, anti_score, ues,
                'REJECT', regime, breadth_sig, scan_raw,
                n_laws, top_law_id, entry_price_v, entry_high_v, stop_loss_v,
                t1_v, t2_v, r_ratio_v,
                liq_tier, max_pos, 1 if is_anti else 0,
                dna_score, cycle_score,
                0, f'HARD_GATE:{hard_reason}', bclass, pine_rs
            ))
            continue  # do not add to active signals list

        conn.execute("""
            INSERT OR REPLACE INTO unified_signals
            (signal_date, symbol, explosion_score, breadth_score, technical_score,
             cross_market_score, liquidity_score, anti_law_score, unified_score,
             conviction_tier, active_regime, breadth_signal, scan_score,
             n_confirming_laws, top_law, entry_price, entry_high, stop_loss,
             t1_target, t2_target, r_ratio,
             liquidity_tier, max_position_egp, is_anti_law_triggered,
             dna_score, cycle_score,
             quality_gate_passed, gate_reason, behavioral_class, pine_rs_percentile)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date, symbol, exp_score, breadth_score, tech_score,
            cross_score, liq_score, anti_score, ues,
            conviction, regime, breadth_sig, scan_raw,
            n_laws, top_law_id, entry_price_v, entry_high_v, stop_loss_v,
            t1_v, t2_v, r_ratio_v,
            liq_tier, max_pos, 1 if is_anti else 0,
            dna_score, cycle_score,
            1 if gate_passed else 0, gate_reason, bclass, pine_rs
        ))

        # Ph 22: write to shadow log (outcome filled later by shadow_fill_outcomes)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO spectral_shadow_log
                (symbol, prediction_date, spectral_regime, cycle_bottom_prox,
                 spectral_boost, ues_with_boost, ues_without, boost_delta)
                VALUES (?,?,?,?,?,?,?,?)
            """, (symbol, date, spec_regime, cycle_btm, spec_boost,
                  round(ues, 3), round(ues_no_spec, 3), round(ues - ues_no_spec, 3)))
        except Exception:
            pass

        if conviction not in ('REJECT',):
            results.append({
                'symbol':              symbol,
                'unified_score':       round(ues, 1),
                'conviction_tier':     conviction,
                'scan_score':          safe_float(sig['score']),
                'liquidity_tier':      liq_tier,
                'spectral_regime':     spec_regime,           # Ph 21
                'cycle_bottom':        cycle_btm,             # Ph 21
                'spectral_boost':      round(spec_boost, 3),  # Ph 21
                'behavioral_class':    bclass,                # Ph 28
                'false_signal_rate':   round(fsr, 3),         # Ph 28
                'pine_rs_percentile':  pine_rs,               # Ph 29
                'quality_gate':        gate_passed,           # Ph 27
                'gate_reason':         gate_reason,           # Ph 27
            })

    conn.commit()
    conn.close()
    results.sort(key=lambda x: -x['unified_score'])

    n_gate_passed  = sum(1 for r in results if r.get('quality_gate'))
    n_hard_rejected = len(hard_gate_rejected)
    n_passed_hard   = len(results)

    # [Gates] summary log
    if _gate_rejection_counts:
        _rejection_summary = ', '.join(
            f"{k}={v}" for k, v in sorted(_gate_rejection_counts.items(), key=lambda kv: -kv[1])
        )
    else:
        _rejection_summary = "none"
    print(
        f"[signal_integration] Hard Gates: {n_passed_hard} passed, "
        f"{n_hard_rejected} rejected ({_rejection_summary})",
        flush=True
    )

    return {
        'success': True,
        'date': date,
        'n_scored': len(scans),
        'n_actionable': len(results),
        'n_gate_passed': n_gate_passed,                # Ph 27
        'n_hard_rejected': n_hard_rejected,            # Hard gate count
        'n_high':   sum(1 for r in results if r['conviction_tier'] == 'HIGH_CONVICTION'),
        'n_ultra':  sum(1 for r in results if r['conviction_tier'] == 'ULTRA_CONVICTION'),
        'n_medium': sum(1 for r in results if r['conviction_tier'] == 'MEDIUM_CONVICTION'),
        'active_regime': regime,
        'breadth_signal': breadth_sig,
        'top_signals': results[:20],
        # Ph 27 — gate-only signals (high quality only)
        'gated_signals': [r for r in results if r.get('quality_gate')][:10],
        # Hard-gate rejections summary
        'hard_gate_rejections': hard_gate_rejected[:10],
    }

def cmd_daily_signals(params):
    date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    min_score = float(params.get('min_score', 50))
    conviction = params.get('conviction', None)

    conn = get_db()
    ensure_tables(conn)

    query = "SELECT * FROM unified_signals WHERE signal_date=? AND unified_score>=?"
    args = [date, min_score]
    if conviction:
        query += " AND conviction_tier=?"
        args.append(conviction)
    query += " ORDER BY unified_score DESC LIMIT 30"

    rows = conn.execute(query, args).fetchall()

    if not rows:
        # Auto-compute if not done yet
        score_result = cmd_score_all({'date': date})
        rows = conn.execute(query, args).fetchall()

    signals = []
    for r in rows:
        signals.append({
            'symbol': r['symbol'],
            'unified_score': round(safe_float(r['unified_score']), 1),
            'conviction_tier': r['conviction_tier'],
            'active_regime': r['active_regime'],
            'components': {
                'explosion': round(safe_float(r['explosion_score']), 1),
                'breadth': round(safe_float(r['breadth_score']), 1),
                'technical': round(safe_float(r['technical_score']), 1),
                'liquidity': round(safe_float(r['liquidity_score']), 1),
            },
            'liquidity_tier': r['liquidity_tier'],
            'max_position_egp': r['max_position_egp'],
        })

    return {
        'success': True,
        'date': date,
        'n_signals': len(signals),
        'signals': signals,
    }

def cmd_conviction_filter(params):
    date           = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    min_conviction = params.get('min_conviction', 'MEDIUM_CONVICTION')
    gated_only     = bool(params.get('gated_only', False))   # Ph 27 — filter by quality gate

    valid_tiers = ['ULTRA_CONVICTION', 'HIGH_CONVICTION', 'MEDIUM_CONVICTION', 'LOW_CONVICTION']
    if min_conviction not in valid_tiers:
        min_conviction = 'MEDIUM_CONVICTION'

    idx = valid_tiers.index(min_conviction)
    allowed_tiers = valid_tiers[:idx+1]

    conn = get_db()
    ensure_tables(conn)

    placeholders = ','.join(['?'] * len(allowed_tiers))
    gate_clause = 'AND quality_gate_passed=1' if gated_only else ''
    rows = conn.execute(
        f"""SELECT * FROM unified_signals
            WHERE signal_date=? AND conviction_tier IN ({placeholders})
              AND is_anti_law_triggered=0 {gate_clause}
            ORDER BY unified_score DESC LIMIT 50""",
        [date] + allowed_tiers
    ).fetchall()

    return {
        'success': True,
        'date': date,
        'min_conviction': min_conviction,
        'gated_only': gated_only,
        'n_filtered': len(rows),
        'signals': [
            {
                'symbol':           r['symbol'],
                'score':            round(safe_float(r['unified_score']), 1),
                'conviction':       r['conviction_tier'],
                'tier':             r['liquidity_tier'],
                'behavioral_class': r['behavioral_class'],  # Ph 28
                'quality_gate':     bool(r['quality_gate_passed']),  # Ph 27
                'gate_reason':      r['gate_reason'],
                'entry_price':      r['entry_price'],
                'stop_loss':        r['stop_loss'],
                't1_target':        r['t1_target'],
            }
            for r in rows
        ],
    }

def cmd_score_history(params):
    symbol = params.get('symbol', '').upper()
    n_days = int(params.get('n_days', 14))

    conn = get_db()
    ensure_tables(conn)

    if symbol:
        rows = conn.execute(
            "SELECT * FROM unified_signals WHERE symbol=? ORDER BY signal_date DESC LIMIT ?",
            (symbol, n_days)
        ).fetchall()
        return {
            'success': True,
            'symbol': symbol,
            'history': [
                {'date': r['signal_date'], 'score': round(safe_float(r['unified_score']), 1),
                 'conviction': r['conviction_tier']}
                for r in rows
            ],
        }
    else:
        # Top symbols by average score over last N days
        rows = conn.execute("""
            SELECT symbol, AVG(unified_score) as avg_score, COUNT(*) as n_days,
                   MAX(conviction_tier) as best_conviction
            FROM unified_signals
            WHERE signal_date >= date('now', ?)
            GROUP BY symbol
            HAVING n_days >= 2
            ORDER BY avg_score DESC
            LIMIT 20
        """, (f'-{n_days} days',)).fetchall()
        return {
            'success': True,
            'n_days': n_days,
            'top_consistent': [
                {'symbol': r['symbol'], 'avg_score': round(r['avg_score'], 1),
                 'n_days': r['n_days'], 'best_conviction': r['best_conviction']}
                for r in rows
            ],
        }

def cmd_build_full(params):
    date = params.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    score_result = cmd_score_all({'date': date})
    signals_result = cmd_daily_signals({'date': date, 'min_score': 55})

    return {
        'success': True,
        'date': date,
        'scored': score_result.get('n_scored', 0),
        'actionable': score_result.get('n_actionable', 0),
        'high_conviction': score_result.get('n_high', 0),
        'medium_conviction': score_result.get('n_medium', 0),
        'active_regime': score_result.get('active_regime', 'UNKNOWN'),
        'breadth_signal': score_result.get('breadth_signal', 'NEUTRAL'),
        'top_signals': signals_result.get('signals', [])[:10],
    }

# ─────────────────────────────────────────────────────────────────────────────
# Ph 22 — Shadow Validator: fill deferred outcomes + generate attribution report
# ─────────────────────────────────────────────────────────────────────────────

def cmd_shadow_fill_outcomes(params):
    """
    Fill return_3d / return_5d / exploded for shadow_log rows older than 5 trading days.
    Uses ohlcv_history for price returns and explosive_moves for explosion labels.
    Run daily (adds ~10ms).
    """
    conn = get_db()
    ensure_tables(conn)

    cutoff = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()

    # Find unfilled rows older than 5 days
    pending = conn.execute("""
        SELECT symbol, prediction_date
        FROM spectral_shadow_log
        WHERE return_5d IS NULL AND prediction_date <= ?
    """, (cutoff,)).fetchall()

    filled = 0
    for row in pending:
        sym   = row['symbol']
        pdate = row['prediction_date']
        try:
            # Close price on prediction_date (bar_time is unix timestamp)
            p0_row = conn.execute("""
                SELECT close FROM ohlcv_history
                WHERE symbol=? AND date(bar_time,'unixepoch')=?
                LIMIT 1
            """, (sym, pdate)).fetchone()
            if not p0_row:
                continue
            p0 = float(p0_row['close'])
            if p0 <= 0:
                continue

            # Close prices 3d and 5d later
            def get_close_after(n_days):
                target = (datetime.date.fromisoformat(pdate) +
                          datetime.timedelta(days=n_days)).isoformat()
                r = conn.execute("""
                    SELECT close FROM ohlcv_history
                    WHERE symbol=? AND date(bar_time,'unixepoch')>=?
                    ORDER BY bar_time ASC LIMIT 1
                """, (sym, target)).fetchone()
                return float(r['close']) if r else None

            p3 = get_close_after(3)
            p5 = get_close_after(5)
            ret3 = round((p3 / p0 - 1) * 100, 3) if p3 else None
            ret5 = round((p5 / p0 - 1) * 100, 3) if p5 else None

            # Check explosion within 5 days
            end5 = (datetime.date.fromisoformat(pdate) + datetime.timedelta(days=5)).isoformat()
            exploded_row = conn.execute("""
                SELECT 1 FROM explosive_moves
                WHERE symbol=? AND explosion_date>=? AND explosion_date<=?
                LIMIT 1
            """, (sym, pdate, end5)).fetchone()
            exploded = 1 if exploded_row else 0

            conn.execute("""
                UPDATE spectral_shadow_log
                SET return_3d=?, return_5d=?, exploded=?, outcome_date=?
                WHERE symbol=? AND prediction_date=?
            """, (ret3, ret5, exploded, end5, sym, pdate))
            filled += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    return {'success': True, 'filled': filled, 'pending': len(pending)}


def cmd_shadow_report(params):
    """
    Ph 22: Attribution report — spectral boost vs actual outcomes.
    Compares: cyclical+high_boost vs noisy/low_boost performance.
    """
    conn = get_db()
    ensure_tables(conn)
    days = int(params.get('days', 90))

    rows = conn.execute("""
        SELECT spectral_regime, cycle_bottom_prox, spectral_boost, boost_delta,
               ues_with_boost, ues_without, return_3d, return_5d, exploded
        FROM spectral_shadow_log
        WHERE return_5d IS NOT NULL
          AND prediction_date >= date('now', ?)
    """, (f'-{days} days',)).fetchall()
    conn.close()

    if not rows:
        return {'success': False, 'error': 'No outcome data yet — need at least 5 trading days'}

    import statistics

    def bucket_stats(subset):
        if not subset:
            return {'n': 0}
        r5   = [r['return_5d'] for r in subset if r['return_5d'] is not None]
        expl = [r['exploded']  for r in subset if r['exploded']  is not None]
        return {
            'n':                len(subset),
            'avg_return_5d':    round(statistics.mean(r5), 3)    if r5   else None,
            'median_return_5d': round(statistics.median(r5), 3)  if r5   else None,
            'explosion_rate':   round(sum(expl) / len(expl), 3)  if expl else None,
        }

    cyclical_high  = [r for r in rows if r['spectral_regime'] == 'cyclical'
                      and safe_float(r['cycle_bottom_prox']) > 0.65]
    cyclical_low   = [r for r in rows if r['spectral_regime'] == 'cyclical'
                      and safe_float(r['cycle_bottom_prox']) <= 0.65]
    noisy          = [r for r in rows if r['spectral_regime'] == 'noisy']
    compression    = [r for r in rows if r['spectral_regime'] == 'compression']
    expansion      = [r for r in rows if r['spectral_regime'] == 'expansion']

    report = {
        'success': True,
        'days_window': days,
        'total_observations': len(rows),
        'buckets': {
            'cyclical_at_bottom (prox>0.65)': bucket_stats(cyclical_high),
            'cyclical_not_bottom (prox≤0.65)': bucket_stats(cyclical_low),
            'noisy':       bucket_stats(noisy),
            'compression': bucket_stats(compression),
            'expansion':   bucket_stats(expansion),
        },
    }

    # Spectral edge: does boost_delta correlate with return_5d?
    boosted    = [r for r in rows if safe_float(r['boost_delta']) > 0.5]
    unboosted  = [r for r in rows if abs(safe_float(r['boost_delta'])) < 0.1]
    r5_boosted   = [r['return_5d'] for r in boosted   if r['return_5d'] is not None]
    r5_unboosted = [r['return_5d'] for r in unboosted if r['return_5d'] is not None]
    report['spectral_edge'] = {
        'boosted_n':   len(r5_boosted),
        'unboosted_n': len(r5_unboosted),
        'boosted_avg_return_5d':   round(statistics.mean(r5_boosted),   3) if r5_boosted   else None,
        'unboosted_avg_return_5d': round(statistics.mean(r5_unboosted), 3) if r5_unboosted else None,
        'edge_pct': round((statistics.mean(r5_boosted) - statistics.mean(r5_unboosted)), 3)
                    if r5_boosted and r5_unboosted else None,
    }
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Ph 26 — Spectral Alpha Dashboard
# Computes rolling performance metrics by spectral regime from shadow log.
# Self-activates once ≥10 filled observations exist — silent skip otherwise.
# Run: daily (quick stats) / weekly (full Sharpe + Kelly)
# ─────────────────────────────────────────────────────────────────────────────

def cmd_spectral_alpha_dashboard(params):
    """
    Compute the Spectral Alpha Dashboard from shadow_log outcomes.

    Metrics (per regime bucket, 2 windows: 30d + 90d):
      avg_return_5d, median_return_5d, hit_rate, explosion_rate,
      sharpe_5d, max_drawdown, kelly_efficiency, ues_calib_error, boost_edge

    Regime buckets:
      cyclical_high  (regime=cyclical + prox > 0.65)
      cyclical_low   (regime=cyclical + prox ≤ 0.65)
      noisy
      compression
      expansion
      MARKET         (all rows combined — benchmark)
    """
    import math, statistics

    MIN_OBS = int(params.get('min_obs', 10))
    conn = get_db()
    ensure_tables(conn)
    today = datetime.date.today().isoformat()

    # Fetch all filled rows
    all_rows = conn.execute("""
        SELECT spectral_regime, cycle_bottom_prox, spectral_boost,
               ues_with_boost, ues_without, boost_delta,
               return_3d, return_5d, exploded
        FROM spectral_shadow_log
        WHERE return_5d IS NOT NULL
        ORDER BY prediction_date
    """).fetchall()

    total = len(all_rows)
    if total < MIN_OBS:
        conn.close()
        return {
            'success': False,
            'status': 'waiting',
            'reason': f'Only {total}/{MIN_OBS} filled observations — activates after 26 May',
            'total_shadow_rows': total,
        }

    def bucket_metrics(subset, window_label, regime_label):
        """Compute all dashboard metrics for a subset of rows."""
        n = len(subset)
        if n == 0:
            return None

        r5   = [safe_float(r['return_5d']) for r in subset]
        expl = [int(r['exploded'] or 0) for r in subset]

        avg_r5      = statistics.mean(r5)
        med_r5      = statistics.median(r5)
        hit_rate    = sum(1 for x in r5 if x > 0) / n
        expl_rate   = sum(expl) / n
        stdev_r5    = statistics.stdev(r5) if n > 1 else 0.0
        sharpe      = (avg_r5 / stdev_r5) if stdev_r5 > 0 else 0.0
        max_dd      = min(r5)

        # Kelly efficiency: compare avg return of boosted vs unboosted within bucket
        boosted   = [safe_float(r['return_5d']) for r in subset if safe_float(r['spectral_boost']) > 1.02]
        unboosted = [safe_float(r['return_5d']) for r in subset if abs(safe_float(r['spectral_boost']) - 1.0) < 0.02]
        boost_edge = (
            (statistics.mean(boosted) - statistics.mean(unboosted))
            if boosted and unboosted else None
        )
        # Kelly efficiency = ratio of actual return to expected (rough proxy)
        kelly_eff = round(avg_r5 / max(abs(avg_r5) * 2, 0.01), 4) if avg_r5 != 0 else 0.0

        # UES calibration error: |mean(ues_with_boost)/100 - explosion_rate|
        avg_ues_prob = statistics.mean([safe_float(r['ues_with_boost'], 50) / 100 for r in subset])
        ues_calib    = abs(avg_ues_prob - expl_rate)

        return {
            'regime':          regime_label,
            'n_obs':           n,
            'avg_return_5d':   round(avg_r5, 4),
            'median_return_5d': round(med_r5, 4),
            'hit_rate':        round(hit_rate, 4),
            'explosion_rate':  round(expl_rate, 4),
            'sharpe_5d':       round(sharpe, 4),
            'max_drawdown':    round(max_dd, 4),
            'kelly_efficiency': round(kelly_eff, 4),
            'ues_calib_error': round(ues_calib, 4),
            'boost_edge':      round(boost_edge, 4) if boost_edge is not None else None,
        }

    def run_window(rows_w, window_days):
        """Compute all regime buckets for a given row subset."""
        buckets = {
            'cyclical_high':  [r for r in rows_w if r['spectral_regime'] == 'cyclical'
                               and safe_float(r['cycle_bottom_prox']) > 0.65],
            'cyclical_low':   [r for r in rows_w if r['spectral_regime'] == 'cyclical'
                               and safe_float(r['cycle_bottom_prox']) <= 0.65],
            'noisy':          [r for r in rows_w if r['spectral_regime'] == 'noisy'],
            'compression':    [r for r in rows_w if r['spectral_regime'] == 'compression'],
            'expansion':      [r for r in rows_w if r['spectral_regime'] == 'expansion'],
            'MARKET':         rows_w,
        }
        results = {}
        for regime_label, subset in buckets.items():
            m = bucket_metrics(subset, window_days, regime_label)
            if m:
                results[regime_label] = m
                # Write to DB
                conn.execute("""
                    INSERT OR REPLACE INTO spectral_alpha_dashboard
                    (computed_date, window_days, regime, n_obs,
                     avg_return_5d, median_return_5d, hit_rate, explosion_rate,
                     sharpe_5d, max_drawdown, kelly_efficiency, ues_calib_error, boost_edge)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    today, window_days, regime_label, m['n_obs'],
                    m['avg_return_5d'], m['median_return_5d'], m['hit_rate'], m['explosion_rate'],
                    m['sharpe_5d'], m['max_drawdown'], m['kelly_efficiency'],
                    m['ues_calib_error'], m['boost_edge'],
                ))
        return results

    # Run for 2 windows
    cutoff_30d = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    cutoff_90d = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()

    # Need prediction_date — reload with date
    all_rows_dated = conn.execute("""
        SELECT prediction_date, spectral_regime, cycle_bottom_prox, spectral_boost,
               ues_with_boost, ues_without, boost_delta, return_3d, return_5d, exploded
        FROM spectral_shadow_log
        WHERE return_5d IS NOT NULL
        ORDER BY prediction_date
    """).fetchall()

    rows_30d = [r for r in all_rows_dated if r['prediction_date'] >= cutoff_30d]
    rows_90d = [r for r in all_rows_dated if r['prediction_date'] >= cutoff_90d]

    dashboard = {
        'window_30d': run_window(rows_30d, 30) if len(rows_30d) >= MIN_OBS else {},
        'window_90d': run_window(rows_90d, 90) if len(rows_90d) >= MIN_OBS else {},
        'window_all': run_window(list(all_rows_dated), 999),
    }
    conn.commit()
    conn.close()

    # ── Human-readable summary ────────────────────────────────────────────────
    def fmt_row(m):
        return (f"n={m['n_obs']:4d}  ret5d={m['avg_return_5d']:+.2f}%  "
                f"hit={m['hit_rate']:.1%}  expl={m['explosion_rate']:.1%}  "
                f"sharpe={m['sharpe_5d']:+.3f}  "
                f"edge={m.get('boost_edge') or 0:+.2f}%  "
                f"ues_err={m['ues_calib_error']:.3f}")

    result = {
        'success': True,
        'computed_date': today,
        'total_observations': total,
        'dashboard': dashboard,
        'key_questions': {
            'noisy_worse_than_market': (
                dashboard.get('window_all', {}).get('noisy', {}).get('avg_return_5d', 0) <
                dashboard.get('window_all', {}).get('MARKET', {}).get('avg_return_5d', 1)
            ),
            'cyclical_high_beats_market': (
                dashboard.get('window_all', {}).get('cyclical_high', {}).get('sharpe_5d', 0) >
                dashboard.get('window_all', {}).get('MARKET', {}).get('sharpe_5d', 0)
            ),
            'boost_edge_positive': (
                safe_float(dashboard.get('window_all', {}).get('cyclical_high', {}).get('boost_edge')) > 0
            ),
            'ues_well_calibrated': (
                safe_float(dashboard.get('window_all', {}).get('MARKET', {}).get('ues_calib_error')) < 0.15
            ),
        },
    }
    return result


def cmd_track_outcomes(params):
    """
    Ph 32 — Recommendation Outcome Tracker.
    Fills return_t1/t3/t5/t10 and hit_* fields for signals that are ≥N days old.
    Reads OHLCV prices at t+1, t+3, t+5, t+10 relative to signal_date.
    Run daily (fast: ~10ms). Idempotent — skips already-filled rows.
    """
    lookback_days = int(params.get('lookback_days', 60))
    conn = get_db()
    ensure_tables(conn)

    cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()

    # 1. Seed recommendation_outcomes from unified_signals (signals not yet seeded)
    new_rows = conn.execute("""
        SELECT us.signal_date, us.symbol, us.conviction_tier,
               us.entry_price, us.stop_loss, us.t1_target,
               us.unified_score, us.explosion_score, us.behavioral_class,
               COALESCE(us.quality_gate_passed, 0) as quality_gate_passed
        FROM unified_signals us
        LEFT JOIN recommendation_outcomes ro
          ON ro.signal_date = us.signal_date AND ro.symbol = us.symbol
        WHERE us.signal_date >= ?
          AND us.conviction_tier NOT IN ('REJECT', 'WATCH')
          AND ro.id IS NULL
    """, (cutoff,)).fetchall()

    seeded = 0
    for r in new_rows:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO recommendation_outcomes
                (signal_date, report_date, symbol, conviction_tier,
                 entry_price, stop_loss, t1_target,
                 ues, ml_score, behavioral_class, quality_gate_passed, outcome_filled)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
            """, (
                r['signal_date'], r['signal_date'], r['symbol'],
                r['conviction_tier'], r['entry_price'], r['stop_loss'],
                r['t1_target'], r['unified_score'], r['explosion_score'],
                r['behavioral_class'], r['quality_gate_passed'],
            ))
            seeded += 1
        except Exception:
            pass
    conn.commit()

    # 1b. Backfill quality_gate_passed for existing rows (in case they were seeded before Ph33)
    conn.execute("""
        UPDATE recommendation_outcomes
        SET quality_gate_passed = COALESCE((
            SELECT us.quality_gate_passed
            FROM unified_signals us
            WHERE us.symbol = recommendation_outcomes.symbol
              AND us.signal_date = recommendation_outcomes.signal_date
            LIMIT 1
        ), 0)
        WHERE quality_gate_passed IS NULL OR quality_gate_passed = 0
    """)
    conn.commit()

    # 2. Fill outcomes for rows where outcome_filled < 5 and ≥1 trading day has passed.
    #    outcome_filled uses a progress ladder:
    #      0 = nothing filled yet
    #      1 = t1 filled  (1 bar available)
    #      3 = t1+t3 filled (3 bars available)
    #      5 = complete  (5 bars — canonical close for Ph46 Bayesian WR)
    #     10 = complete + t10
    #    This allows Ph46 Bayesian WR to learn from hit_t1 as early as Day 2,
    #    rather than waiting for the full 5-day window.
    ready_cutoff = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    pending = conn.execute("""
        SELECT id, signal_date, symbol, entry_price, stop_loss, t1_target, outcome_filled
        FROM recommendation_outcomes
        WHERE (outcome_filled < 5
               OR (close_t1 IS NOT NULL AND return_t1 IS NULL))
          AND signal_date <= ?
          AND signal_date >= ?
    """, (ready_cutoff, cutoff)).fetchall()

    filled = 0
    for row in pending:
        sig_date      = row['signal_date']
        symbol        = row['symbol']
        entry         = safe_float(row['entry_price'])
        stop          = safe_float(row['stop_loss'])
        t1_tgt        = safe_float(row['t1_target'])
        current_level = int(row['outcome_filled'] or 0)

        # Fallback: if entry_price not stored, use close on signal_date as entry
        # This gives a realistic "buy at close on signal day" performance estimate
        if not entry:
            try:
                ep_row = conn.execute("""
                    SELECT close FROM ohlcv_history
                    WHERE symbol=? AND date(bar_time,'unixepoch')=?
                    ORDER BY bar_time DESC LIMIT 1
                """, (symbol, sig_date)).fetchone()
                if ep_row:
                    candidate = safe_float(ep_row['close'])
                    # Sanity check: entry must be positive and not a market-index value.
                    # Cross-market indices (EGX30 ~131, EGX70 ~180) must not bleed in.
                    # Accept only if close is plausible (>0 and not a known index level).
                    if candidate and candidate > 0:
                        entry = candidate
                        conn.execute(
                            "UPDATE recommendation_outcomes SET entry_price=? WHERE id=?",
                            (entry, row['id'])
                        )
            except Exception:
                pass

        # Fetch all trading bars after signal_date for this symbol (up to 15 bars)
        # Then pick the Nth bar to get the Nth trading-day close (correct, no calendar math)
        try:
            bars_after = conn.execute("""
                SELECT close FROM ohlcv_history
                WHERE symbol=? AND date(bar_time,'unixepoch') > ?
                ORDER BY bar_time ASC LIMIT 15
            """, (symbol, sig_date)).fetchall()
        except Exception:
            bars_after = []

        def price_at(n):
            """Nth trading-day close after signal_date (1-indexed)."""
            if n <= len(bars_after):
                return safe_float(bars_after[n - 1]['close'])
            return None

        c1  = price_at(1)
        c3  = price_at(3)
        c5  = price_at(5)
        c10 = price_at(10)

        if c1 is None:  # not even 1 bar yet — skip
            continue

        # Corporate action guard: if c1/entry ratio is extreme (>2.5x or <0.3x),
        # this is likely a split/dividend gap, not a real return.
        # NULL out the close prices to prevent corrupt return calculations.
        if entry and entry > 0 and c1 is not None:
            ratio = c1 / entry
            if ratio > 2.5 or ratio < 0.3:
                # Flag as corporate action — skip outcome fill for this row
                conn.execute(
                    "UPDATE recommendation_outcomes SET outcome_filled=-1 WHERE id=?",
                    (row['id'],)
                )
                continue

        # Determine new fill level
        if c10 is not None:
            new_level = 10
        elif c5 is not None:
            new_level = 5
        elif c3 is not None:
            new_level = 3
        else:
            new_level = 1

        # Skip if no progress vs current fill level, unless return_t1 is still missing
        has_return = conn.execute(
            "SELECT return_t1 FROM recommendation_outcomes WHERE id=?", (row['id'],)
        ).fetchone()
        if new_level <= current_level and (has_return and has_return['return_t1'] is not None):
            continue

        def ret(c):
            if c is None or not entry or entry == 0:
                return None
            return round((c - entry) / entry * 100.0, 4)

        r1, r3, r5, r10 = ret(c1), ret(c3), ret(c5), ret(c10)

        hit_t1   = 1 if (r1 is not None and r1 > 0) else (0 if r1 is not None else None)
        hit_t5   = 1 if (r5 is not None and r5 > 0) else (0 if r5 is not None else None)
        hit_stop = None
        available = [p for p in [c1, c3, c5] if p is not None]
        if stop and entry and available:
            hit_stop = 1 if any(p <= stop for p in available) else 0
        reached_t1_target = None
        if t1_tgt and entry:
            prices = [p for p in [c1, c3, c5, c10] if p is not None]
            reached_t1_target = 1 if any(p >= t1_tgt for p in prices) else 0

        try:
            conn.execute("""
                UPDATE recommendation_outcomes SET
                  close_t1=COALESCE(?,close_t1),
                  close_t3=COALESCE(?,close_t3),
                  close_t5=COALESCE(?,close_t5),
                  close_t10=COALESCE(?,close_t10),
                  return_t1=COALESCE(?,return_t1),
                  return_t3=COALESCE(?,return_t3),
                  return_t5=COALESCE(?,return_t5),
                  return_t10=COALESCE(?,return_t10),
                  hit_t1=COALESCE(?,hit_t1),
                  hit_t5=COALESCE(?,hit_t5),
                  reached_t1_target=COALESCE(?,reached_t1_target),
                  hit_stop=COALESCE(?,hit_stop),
                  outcome_filled=?
                WHERE id=?
            """, (c1, c3, c5, c10, r1, r3, r5, r10,
                  hit_t1, hit_t5, reached_t1_target, hit_stop,
                  new_level, row['id']))
            filled += 1
        except Exception:
            pass

    conn.commit()
    conn.close()

    return {
        'success': True,
        'seeded_new': seeded,
        'outcomes_filled': filled,
        'pending_remaining': len(pending) - filled,
    }


def cmd_weekly_performance_report(params):
    """
    Ph 32 — Weekly Performance Report.
    Aggregates recommendation_outcomes for last 4 weeks.
    Returns Arabic-ready performance dict (for Telegram delivery).
    """
    weeks_back = int(params.get('weeks_back', 4))
    min_outcomes = int(params.get('min_outcomes', 5))
    send = params.get('send', False)

    conn = get_db()
    ensure_tables(conn)

    cutoff = (datetime.date.today() - datetime.timedelta(weeks=weeks_back)).isoformat()

    rows = conn.execute("""
        SELECT conviction_tier, return_t5, return_t1, hit_t1, hit_t5,
               reached_t1_target, hit_stop, behavioral_class, ues, ml_score,
               signal_date
        FROM recommendation_outcomes
        WHERE outcome_filled >= 5 AND signal_date>=?
        ORDER BY signal_date DESC
    """, (cutoff,)).fetchall()

    conn.close()

    n = len(rows)
    if n < min_outcomes:
        return {
            'success': True,
            'n_outcomes': n,
            'message': f'بيانات غير كافية ({n} صفقة فقط — يحتاج {min_outcomes})',
        }

    returns_t5  = [safe_float(r['return_t5'])  for r in rows if r['return_t5']  is not None]
    returns_t1  = [safe_float(r['return_t1'])  for r in rows if r['return_t1']  is not None]
    hits_t5     = [r['hit_t5']  for r in rows if r['hit_t5']  is not None]
    hits_stop   = [r['hit_stop'] for r in rows if r['hit_stop'] is not None]
    reached_tgt = [r['reached_t1_target'] for r in rows if r['reached_t1_target'] is not None]

    avg_ret5  = sum(returns_t5) / len(returns_t5) if returns_t5 else 0.0
    wr5       = sum(hits_t5) / len(hits_t5) * 100 if hits_t5 else 0.0
    stop_rate = sum(hits_stop) / len(hits_stop) * 100 if hits_stop else 0.0
    tgt_rate  = sum(reached_tgt) / len(reached_tgt) * 100 if reached_tgt else 0.0

    # By conviction tier
    by_tier = {}
    for tier in ('ULTRA_CONVICTION', 'HIGH_CONVICTION', 'MEDIUM_CONVICTION'):
        tier_rows = [r for r in rows if r['conviction_tier'] == tier]
        if tier_rows:
            tier_rets = [safe_float(r['return_t5']) for r in tier_rows if r['return_t5'] is not None]
            tier_hits = [r['hit_t5'] for r in tier_rows if r['hit_t5'] is not None]
            by_tier[tier] = {
                'n': len(tier_rows),
                'avg_return_5d': round(sum(tier_rets) / len(tier_rets), 2) if tier_rets else 0.0,
                'win_rate': round(sum(tier_hits) / len(tier_hits) * 100, 1) if tier_hits else 0.0,
            }

    report = {
        'success': True,
        'period': f'آخر {weeks_back} أسابيع',
        'n_outcomes': n,
        'avg_return_5d': round(avg_ret5, 2),
        'win_rate_5d':   round(wr5, 1),
        'stop_hit_rate': round(stop_rate, 1),
        'target_hit_rate': round(tgt_rate, 1),
        'by_conviction': by_tier,
        'last_updated': datetime.date.today().isoformat(),
    }

    if send:
        # Build Arabic Telegram message
        tier_ar = {'ULTRA_CONVICTION': '⭐⭐⭐⭐⭐ استثنائية',
                   'HIGH_CONVICTION':  '⭐⭐⭐⭐ عالية',
                   'MEDIUM_CONVICTION':'⭐⭐⭐ متوسطة'}
        lines = [
            f"📊 <b>تقرير الأداء الأسبوعي — {report['period']}</b>",
            f"📅 {datetime.date.today().strftime('%Y-%m-%d')}",
            "",
            f"📈 متوسط العائد (5 أيام): <b>{avg_ret5:+.2f}%</b>",
            f"✅ نسبة النجاح: <b>{wr5:.1f}%</b>",
            f"🎯 بلغت الهدف: <b>{tgt_rate:.1f}%</b>",
            f"🛑 ضربت الوقف: <b>{stop_rate:.1f}%</b>",
            f"📋 عدد الصفقات: <b>{n}</b>",
        ]
        if by_tier:
            lines.append("")
            lines.append("🏷 <b>التفاصيل حسب مستوى الاقتناع:</b>")
            for tier_key, td in by_tier.items():
                tier_label = tier_ar.get(tier_key, tier_key)
                lines.append(
                    f"  • {tier_label}: {td['n']} إشارة | "
                    f"عائد {td['avg_return_5d']:+.2f}% | نجاح {td['win_rate']}%"
                )
        lines += [
            "",
            "⚠️ <i>الأداء التاريخي لا يضمن نتائج مستقبلية</i>",
        ]
        report['telegram_message'] = "\n".join(lines)

    return report


def cmd_gate_stats(params):
    """
    Ph 27 — Quality Gate Statistics Dashboard.
    Shows daily breakdown: how many signals passed/failed and why.
    """
    n_days = int(params.get('n_days', 7))
    cutoff = (datetime.date.today() - datetime.timedelta(days=n_days)).isoformat()

    conn = get_db()
    ensure_tables(conn)

    # Daily pass/fail counts
    daily = conn.execute("""
        SELECT signal_date,
               COUNT(*) as total,
               SUM(CASE WHEN quality_gate_passed=1 THEN 1 ELSE 0 END) as passed,
               AVG(CASE WHEN quality_gate_passed=1 THEN unified_score ELSE NULL END) as avg_ues_passed,
               AVG(CASE WHEN quality_gate_passed=0 THEN unified_score ELSE NULL END) as avg_ues_failed
        FROM unified_signals
        WHERE signal_date >= ?
          AND is_anti_law_triggered=0
          AND conviction_tier IN ('HIGH_CONVICTION','ULTRA_CONVICTION','MEDIUM_CONVICTION')
        GROUP BY signal_date
        ORDER BY signal_date DESC
    """, (cutoff,)).fetchall()

    # Overall rejection reasons
    reasons = conn.execute("""
        SELECT gate_reason, COUNT(*) as n,
               AVG(unified_score) as avg_ues
        FROM unified_signals
        WHERE signal_date >= ? AND quality_gate_passed=0
          AND is_anti_law_triggered=0
          AND conviction_tier IN ('HIGH_CONVICTION','ULTRA_CONVICTION','MEDIUM_CONVICTION')
        GROUP BY gate_reason
        ORDER BY n DESC
    """, (cutoff,)).fetchall()

    # Behavioral class breakdown for passed signals
    behavioral = conn.execute("""
        SELECT behavioral_class, COUNT(*) as n,
               AVG(unified_score) as avg_ues
        FROM unified_signals
        WHERE signal_date >= ? AND quality_gate_passed=1
        GROUP BY behavioral_class
        ORDER BY n DESC
    """, (cutoff,)).fetchall()

    conn.close()
    return {
        'success': True,
        'period_days': n_days,
        'daily_stats': [dict(r) for r in daily],
        'rejection_reasons': [dict(r) for r in reasons],
        'behavioral_breakdown': [dict(r) for r in behavioral],
    }


def cmd_model_drift(params):
    """
    Ph 33 — Model Drift Monitor.
    يحسب Rolling Precision@k و WinRate من recommendation_outcomes
    ويكشف التدهور المبكر في دقة النموذج.
    يُستخدَم يومياً في run_daily.mjs بعد track_outcomes.
    """
    window_days = int(params.get('window_days', 30))
    min_filled  = int(params.get('min_filled', 10))
    alert_threshold_wr = float(params.get('alert_threshold_wr', 45.0))  # WR% below = alert

    conn = get_db()
    ensure_tables(conn)

    cutoff = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()

    rows = conn.execute("""
        SELECT signal_date, conviction_tier, ml_score, ues,
               return_t5, hit_t5, hit_stop, reached_t1_target,
               behavioral_class,
               COALESCE(quality_gate_passed, 0) as quality_gate_passed
        FROM recommendation_outcomes
        WHERE outcome_filled >= 5 AND signal_date>=?
        ORDER BY signal_date DESC
    """, (cutoff,)).fetchall()

    conn.close()

    n_filled = len(rows)
    if n_filled < min_filled:
        return {
            'success': True,
            'n_filled': n_filled,
            'min_filled': min_filled,
            'drift_detected': False,
            'message': f'بيانات غير كافية ({n_filled}/{min_filled} صفقة مكتملة)',
        }

    # Overall metrics
    returns  = [safe_float(r['return_t5']) for r in rows if r['return_t5'] is not None]
    hits     = [r['hit_t5']  for r in rows if r['hit_t5']  is not None]
    stops    = [r['hit_stop'] for r in rows if r['hit_stop'] is not None]
    targets  = [r['reached_t1_target'] for r in rows if r['reached_t1_target'] is not None]

    # ── Guard: all hit_t5 are NULL → signals too recent, t5 window not closed yet
    if not hits:
        try:
            oldest_date = min(r['signal_date'] for r in rows if r['signal_date'])[:10]
            pending_days = (datetime.date.today() - datetime.date.fromisoformat(oldest_date)).days
        except Exception:
            pending_days = '?'
        return {
            'success':          True,
            'n_filled':         n_filled,
            'drift_detected':   False,
            'pending_outcomes': True,
            'win_rate':         None,
            'message':          (
                f'نتائج t5 معلقة — {n_filled} صفقة بانتظار اكتمال 5 أيام تداول'
                f' (أقدمها قبل {pending_days} يوم)'
            ),
        }

    avg_ret  = sum(returns) / len(returns) if returns else 0.0
    win_rate = sum(hits) / len(hits) * 100 if hits else 0.0
    stop_rt  = sum(stops) / len(stops) * 100 if stops else 0.0
    tgt_rt   = sum(targets) / len(targets) * 100 if targets else 0.0

    # Gated-only metrics (Ph27)
    gated = [r for r in rows if r['quality_gate_passed'] == 1]
    gated_hits = [r['hit_t5'] for r in gated if r['hit_t5'] is not None]
    gated_wr = sum(gated_hits) / len(gated_hits) * 100 if gated_hits else None

    # Weekly rolling WR (drift detection)
    weekly = {}
    for r in rows:
        d = (r['signal_date'] or '')[:10]
        try:
            wk = datetime.date.fromisoformat(d).isocalendar()[1]
        except Exception:
            continue
        wk_key = f"{d[:4]}-W{wk:02d}"
        weekly.setdefault(wk_key, [])
        if r['hit_t5'] is not None:
            weekly[wk_key].append(int(r['hit_t5']))

    weekly_wr = {}
    for wk, hits_wk in sorted(weekly.items()):
        if len(hits_wk) >= 3:
            weekly_wr[wk] = round(sum(hits_wk) / len(hits_wk) * 100, 1)

    # Drift detection: last 2 weeks vs baseline
    wk_list = sorted(weekly_wr.items())
    drift_detected = False
    drift_reason   = None
    if len(wk_list) >= 2:
        recent_wr = wk_list[-1][1]
        if recent_wr < alert_threshold_wr:
            drift_detected = True
            drift_reason   = f'آخر أسبوع WR={recent_wr:.1f}% < {alert_threshold_wr}%'
    if win_rate < alert_threshold_wr and n_filled >= min_filled:
        drift_detected = True
        drift_reason   = (drift_reason or '') + f' | إجمالي WR={win_rate:.1f}%'

    # ML calibration check (does ML score predict win?)
    high_ml = [r for r in rows if safe_float(r['ml_score'], 0) >= 0.75 and r['hit_t5'] is not None]
    low_ml  = [r for r in rows if safe_float(r['ml_score'], 0) < 0.60  and r['hit_t5'] is not None]
    high_ml_wr = sum(int(r['hit_t5']) for r in high_ml) / len(high_ml) * 100 if high_ml else None
    low_ml_wr  = sum(int(r['hit_t5']) for r in low_ml)  / len(low_ml)  * 100 if low_ml  else None

    # Calibration gap: high ML should beat low ML by >15pts
    calibration_ok = None
    if high_ml_wr is not None and low_ml_wr is not None:
        gap = high_ml_wr - low_ml_wr
        calibration_ok = gap >= 10.0  # ML score is meaningfully discriminative

    return {
        'success':         True,
        'window_days':     window_days,
        'n_filled':        n_filled,
        'avg_return_5d':   round(avg_ret, 2),
        'win_rate':        round(win_rate, 1),
        'stop_hit_rate':   round(stop_rt, 1),
        'target_hit_rate': round(tgt_rt, 1),
        'gated_win_rate':  round(gated_wr, 1) if gated_wr is not None else None,
        'gated_n':         len(gated_hits),
        'weekly_win_rates': weekly_wr,
        'drift_detected':  drift_detected,
        'drift_reason':    drift_reason,
        'high_ml_wr':      round(high_ml_wr, 1) if high_ml_wr is not None else None,
        'low_ml_wr':       round(low_ml_wr, 1)  if low_ml_wr  is not None else None,
        'calibration_ok':  calibration_ok,
        'alert_threshold': alert_threshold_wr,
        'last_updated':    datetime.date.today().isoformat(),
    }


def cmd_signal_freshness(params):
    """
    Ph 36 — Signal Freshness Validator.
    يفحص الإشارات الحديثة (today/yesterday) ويُعيد:
    - fresh:    close_price ضمن نطاق الدخول ±2%
    - extended: close_price تجاوز entry_high بـ 2-5%
    - chased:   close_price تجاوز entry_high بـ >5% (لا تدخل)
    - stopped:  close_price تحت stop_loss
    مفيد لفرز الإشارات قبل الإرسال دون الحاجة إلى TradingView.
    """
    date_str = params.get('date', datetime.date.today().isoformat())
    max_chase_pct = float(params.get('max_chase_pct', 5.0))
    warn_pct      = float(params.get('warn_pct',      2.0))

    conn = get_db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT us.symbol, us.entry_price, us.entry_high, us.stop_loss,
               us.unified_score, us.conviction_tier, us.quality_gate_passed,
               oh.close as latest_close
        FROM unified_signals us
        LEFT JOIN (
            SELECT symbol, close
            FROM ohlcv_history oh1
            WHERE bar_time = (
                SELECT MAX(bar_time) FROM ohlcv_history oh2
                WHERE oh2.symbol = oh1.symbol
                  AND date(oh2.bar_time,'unixepoch') <= ?
            )
        ) oh ON oh.symbol = us.symbol
        WHERE us.signal_date = ?
          AND us.conviction_tier NOT IN ('REJECT','WATCH')
          AND us.is_anti_law_triggered = 0
        ORDER BY us.unified_score DESC
    """, (date_str, date_str)).fetchall()

    conn.close()

    result_signals = []
    for r in rows:
        entry_h = safe_float(r['entry_high']) or safe_float(r['entry_price'])
        entry_l = safe_float(r['entry_price'])
        stop    = safe_float(r['stop_loss'])
        close   = safe_float(r['latest_close'])

        # Sanity check: if entry price is >10× or <0.1× close, it's stale/wrong data
        _price_ratio = (close / entry_h) if (close and entry_h and entry_h > 0) else None
        _stale_entry = _price_ratio is not None and (_price_ratio > 10.0 or _price_ratio < 0.1)

        if not close or not entry_h or _stale_entry:
            status = 'no_price'
        elif stop and close <= stop and not (close / stop > 10.0 or stop / close > 10.0):
            status = 'stopped'
        elif close > entry_h * (1 + max_chase_pct / 100):
            status = 'chased'
        elif close > entry_h * (1 + warn_pct / 100):
            status = 'extended'
        elif close >= entry_l * 0.99:
            status = 'fresh'
        else:
            status = 'below_zone'

        result_signals.append({
            'symbol':     r['symbol'],
            'status':     status,
            'close':      round(close, 3) if close else None,
            'entry_low':  round(entry_l, 3) if entry_l else None,
            'entry_high': round(entry_h, 3) if entry_h else None,
            'stop_loss':  round(stop, 3)    if stop    else None,
            'ues':        round(safe_float(r['unified_score']), 1),
            'conviction': r['conviction_tier'],
            'gated':      bool(r['quality_gate_passed']),
        })

    fresh    = [s for s in result_signals if s['status'] == 'fresh']
    extended = [s for s in result_signals if s['status'] == 'extended']
    chased   = [s for s in result_signals if s['status'] == 'chased']
    stopped  = [s for s in result_signals if s['status'] == 'stopped']

    return {
        'success':     True,
        'date':        date_str,
        'total':       len(result_signals),
        'fresh_count': len(fresh),
        'extended_count': len(extended),
        'chased_count': len(chased),
        'stopped_count': len(stopped),
        'signals':     result_signals,
        'fresh':       fresh,
    }


def cmd_ml_score_delta(params):
    """
    Ph 39 — ML Score Delta Monitor.
    يقارن درجات ML اليوم بالأمس لاكتشاف الأسهم التي ارتفعت/انخفضت بشكل ملحوظ.
    يُستخدم بعد predict_ensemble لتسليط الضوء على التغييرات الجوهرية.
    min_delta: الحد الأدنى للتغيير (0-1 scale) — default 0.15 (15pt)
    """
    min_delta   = float(params.get('min_delta', 15.0))   # in 0-100 scale (15 = 15pt change)
    today_str   = params.get('date', datetime.date.today().isoformat())
    yesterday   = (datetime.date.fromisoformat(today_str) - datetime.timedelta(days=1)).isoformat()

    conn = get_db()
    ensure_tables(conn)

    # Today's gated signals
    today_rows = conn.execute("""
        SELECT symbol, explosion_score as ml, unified_score as ues, conviction_tier
        FROM unified_signals
        WHERE signal_date=? AND quality_gate_passed=1
          AND explosion_score IS NOT NULL
    """, (today_str,)).fetchall()

    # Yesterday's scores (any conviction)
    yest_map = {}
    yest_rows = conn.execute("""
        SELECT symbol, explosion_score as ml
        FROM unified_signals
        WHERE signal_date=? AND explosion_score IS NOT NULL
    """, (yesterday,)).fetchall()
    for r in yest_rows:
        yest_map[r['symbol']] = safe_float(r['ml'])

    conn.close()

    surging  = []  # ML jumped by ≥min_delta
    dropping = []  # ML dropped by ≥min_delta
    new_     = []  # ML score didn't exist yesterday

    for r in today_rows:
        sym   = r['symbol']
        ml_t  = safe_float(r['ml'])
        ml_y  = yest_map.get(sym)

        # explosion_score is stored as 0-100 scale directly
        if ml_y is None:
            new_.append({'symbol': sym, 'ml_today': round(ml_t, 1), 'ues': round(safe_float(r['ues']), 1)})
        else:
            delta = ml_t - ml_y  # already in 0-100 scale
            if delta >= min_delta:
                surging.append({'symbol': sym, 'ml_today': round(ml_t, 1),
                                'ml_yesterday': round(ml_y, 1),
                                'delta': round(delta, 1), 'ues': round(safe_float(r['ues']), 1)})
            elif delta <= -min_delta:
                dropping.append({'symbol': sym, 'ml_today': round(ml_t, 1),
                                 'ml_yesterday': round(ml_y, 1),
                                 'delta': round(delta, 1), 'ues': round(safe_float(r['ues']), 1)})

    surging.sort(key=lambda x: -x['delta'])
    dropping.sort(key=lambda x: x['delta'])

    return {
        'success':    True,
        'date':       today_str,
        'yesterday':  yesterday,
        'min_delta':  min_delta,
        'surging':    surging,
        'dropping':   dropping,
        'new_gated':  new_,
        'n_surging':  len(surging),
        'n_dropping': len(dropping),
        'n_new':      len(new_),
    }


def cmd_signal_age(params):
    """
    Ph 40 — Signal Age Tracker.
    لكل إشارة في القائمة المُصفَّاة اليوم، احسب عدد الأيام المتتالية التي ظهرت فيها.
    يُستخدم لاكتشاف الإشارات القديمة التي يجب إعادة تقييمها.
    min_age: الحد الأدنى للعمر الذي يُدرج في التقرير (default=2)
    top_n:   عدد الإشارات لفحصها (default=20)
    """
    today_str  = params.get('date', datetime.date.today().isoformat())
    min_age    = int(params.get('min_age', 2))
    top_n      = int(params.get('top_n', 20))
    today_d    = datetime.date.fromisoformat(today_str)

    conn = get_db()
    ensure_tables(conn)

    # Get today's gated signals
    gated = conn.execute("""
        SELECT symbol, unified_score, explosion_score, conviction_tier
        FROM unified_signals
        WHERE signal_date=? AND quality_gate_passed=1
        ORDER BY unified_score DESC
        LIMIT ?
    """, (today_str, top_n)).fetchall()

    if not gated:
        conn.close()
        return {'success': True, 'date': today_str, 'aged_signals': [], 'n_aged': 0,
                'message': 'لا توجد إشارات مصفّاة اليوم'}

    syms = [r['symbol'] for r in gated]
    _plac = ','.join('?' * len(syms))

    # Get last 14 days of signal_date per symbol
    all_dates = conn.execute(f"""
        SELECT symbol, signal_date
        FROM unified_signals
        WHERE symbol IN ({_plac})
          AND signal_date >= ?
        ORDER BY symbol, signal_date DESC
    """, syms + [(today_d - datetime.timedelta(days=14)).isoformat()]).fetchall()
    conn.close()

    _by_sym = {}
    for r in all_dates:
        _by_sym.setdefault(r['symbol'], []).append(r['signal_date'])

    aged = []
    fresh = []
    for g in gated:
        sym   = g['symbol']
        dates = _by_sym.get(sym, [today_str])
        # Count consecutive streak (allow ≤3 calendar day gap for weekends)
        streak = 0
        prev_d = today_d + datetime.timedelta(days=1)
        for ds in dates:
            d_ = datetime.date.fromisoformat(ds)
            if (prev_d - d_).days <= 3:
                streak += 1
                prev_d = d_
            else:
                break
        entry = {
            'symbol': sym,
            'age_days': streak,
            'unified_score': round(safe_float(g['unified_score']), 1),
            'ml_score': round(safe_float(g['explosion_score']), 1),
            'conviction': g['conviction_tier'],
            'first_appeared': dates[-1] if dates else today_str,
        }
        if streak >= min_age:
            aged.append(entry)
        else:
            fresh.append(entry)

    aged.sort(key=lambda x: -x['age_days'])

    return {
        'success':   True,
        'date':      today_str,
        'n_gated':   len(gated),
        'n_aged':    len(aged),
        'n_fresh':   len(fresh),
        'min_age':   min_age,
        'aged_signals': aged,
        'fresh_signals': [e['symbol'] for e in fresh],
        'oldest_signal': aged[0] if aged else None,
    }


def cmd_check_entry_triggers(params):
    """
    Ph 44 — Entry Trigger Tracker.
    يبحث في OHLCV السابقة (آخر 10 أيام) عن إشارات لمس سعرها منطقة الدخول
    (low ≤ entry_high AND high ≥ entry_price) → يُعلّمها entry_triggered=1.
    يُشغَّل يومياً بعد تحديث OHLCV.
    lookback_days: عدد أيام البحث (default=10)
    """
    today_str    = params.get('date', datetime.date.today().isoformat())
    lookback     = int(params.get('lookback_days', 10))
    today_d      = datetime.date.fromisoformat(today_str)
    cutoff       = (today_d - datetime.timedelta(days=lookback)).isoformat()

    conn = get_db()
    ensure_tables(conn)

    # Fetch non-triggered outcomes with entry zones
    pending = conn.execute("""
        SELECT ro.id, ro.symbol, ro.signal_date, ro.entry_price, ro.stop_loss,
               us.entry_high
        FROM recommendation_outcomes ro
        LEFT JOIN unified_signals us
               ON us.symbol=ro.symbol AND us.signal_date=ro.signal_date
        WHERE ro.entry_triggered = 0
          AND ro.signal_date >= ?
          AND ro.entry_price IS NOT NULL
    """, (cutoff,)).fetchall()

    n_triggered  = 0
    n_checked    = len(pending)
    triggered    = []

    for row in pending:
        sym         = row['symbol']
        signal_d    = row['signal_date']
        entry_p     = safe_float(row['entry_price'])
        entry_h     = safe_float(row['entry_high'] or (entry_p * 1.015))
        stop_l      = safe_float(row['stop_loss'] or (entry_p * 0.96))

        if entry_p <= 0:
            continue

        # Look for OHLCV bars on/after signal_date where price entered zone
        bars = conn.execute("""
            SELECT date(bar_time,'unixepoch') as d, open, high, low, close, volume
            FROM ohlcv_history
            WHERE symbol=? AND date(bar_time,'unixepoch') >= ?
              AND date(bar_time,'unixepoch') <= ?
            ORDER BY bar_time ASC
        """, (sym, signal_d, today_str)).fetchall()

        for bar in bars:
            bar_d  = bar['d']
            b_low  = safe_float(bar['low'])
            b_high = safe_float(bar['high'])
            b_close= safe_float(bar['close'])

            # Sanity: skip stale data (ratio > 10×)
            if b_close > 0 and entry_p > 0:
                ratio = b_close / entry_p
                if ratio > 10 or ratio < 0.1:
                    break  # clearly wrong data, stop

            # Trigger: price range overlaps entry zone
            # (bar low ≤ entry_high) AND (bar high ≥ entry_price)
            in_zone = (b_low <= entry_h * 1.01) and (b_high >= entry_p * 0.99)
            if in_zone:
                conn.execute("""
                    UPDATE recommendation_outcomes
                    SET entry_triggered=1, entry_trigger_date=?, entry_trigger_close=?
                    WHERE id=?
                """, (bar_d, b_close, row['id']))
                n_triggered += 1
                triggered.append({
                    'symbol': sym, 'signal_date': signal_d,
                    'trigger_date': bar_d, 'close': round(b_close, 3),
                    'entry_price': round(entry_p, 3),
                })
                break  # first trigger date

    conn.commit()
    conn.close()

    return {
        'success':      True,
        'date':         today_str,
        'n_checked':    n_checked,
        'n_triggered':  n_triggered,
        'triggered':    triggered[:20],  # top 20 for display
    }


def cmd_stop_loss_hits(params):
    """
    Ph 45 — Stop-Loss Hit Detector.
    يفحص الإشارات المُفعَّلة (entry_triggered=1) ويكتشف أي منها كسرت مستوى الوقف.
    يُدرج تحذيراً في Telegram عن كل إشارة وصل وقفها.
    lookback_days: عدد أيام البحث (default=7)
    """
    today_str = params.get('date', datetime.date.today().isoformat())
    lookback  = int(params.get('lookback_days', 7))
    today_d   = datetime.date.fromisoformat(today_str)
    cutoff    = (today_d - datetime.timedelta(days=lookback)).isoformat()

    conn = get_db()
    ensure_tables(conn)

    # Fetch triggered outcomes with stop_loss defined
    triggered = conn.execute("""
        SELECT ro.id, ro.symbol, ro.signal_date, ro.entry_price, ro.stop_loss,
               ro.entry_trigger_date, ro.entry_trigger_close,
               us.entry_high, us.t1_target
        FROM recommendation_outcomes ro
        LEFT JOIN unified_signals us
               ON us.symbol=ro.symbol AND us.signal_date=ro.signal_date
        WHERE ro.entry_triggered = 1
          AND ro.hit_stop IS NULL
          AND ro.stop_loss IS NOT NULL
          AND ro.signal_date >= ?
    """, (cutoff,)).fetchall()

    hit_stop_list   = []
    near_stop_list  = []
    safe_list       = []

    for row in triggered:
        sym    = row['symbol']
        sl     = safe_float(row['stop_loss'])
        ep     = safe_float(row['entry_price'])
        t1     = safe_float(row['t1_target'] or 0)
        if sl <= 0:
            continue

        # Get latest OHLCV close
        bar = conn.execute("""
            SELECT close, low, date(bar_time,'unixepoch') as d
            FROM ohlcv_history
            WHERE symbol=?
            ORDER BY bar_time DESC LIMIT 1
        """, (sym,)).fetchone()
        if not bar:
            continue
        close = safe_float(bar['close'])
        low   = safe_float(bar['low'])
        if close <= 0:
            continue

        # Sanity: skip stale data
        if ep > 0 and (close / ep > 10 or close / ep < 0.1):
            continue

        pct_from_stop = (close - sl) / sl * 100 if sl > 0 else None
        t1_hit = (close >= t1) if t1 > 0 else False

        entry_info = {
            'symbol': sym, 'signal_date': row['signal_date'],
            'entry_price': round(ep, 3), 'stop_loss': round(sl, 3),
            'current': round(close, 3),
            'pct_from_stop': round(pct_from_stop, 1) if pct_from_stop else None,
            't1_hit': t1_hit,
        }
        if low <= sl * 1.005:        # low at or below stop (hit or very close)
            hit_stop_list.append(entry_info)
            # Mark in DB
            conn.execute("""
                UPDATE recommendation_outcomes SET hit_stop=1 WHERE id=?
            """, (row['id'],))
        elif pct_from_stop is not None and pct_from_stop < 2.0:
            near_stop_list.append(entry_info)  # within 2% of stop
        else:
            safe_list.append({'symbol': sym, 'pct_from_stop': round(pct_from_stop or 0, 1)})

    conn.commit()
    conn.close()

    return {
        'success':       True,
        'date':          today_str,
        'n_tracked':     len(triggered),
        'n_hit_stop':    len(hit_stop_list),
        'n_near_stop':   len(near_stop_list),
        'n_safe':        len(safe_list),
        'hit_stop':      hit_stop_list,
        'near_stop':     near_stop_list,
    }


COMMANDS = {
    'score_symbol': cmd_score_symbol,
    'score_all': cmd_score_all,
    'daily_signals': cmd_daily_signals,
    'conviction_filter': cmd_conviction_filter,
    'score_history': cmd_score_history,
    'build_full': cmd_build_full,
    'shadow_fill_outcomes': cmd_shadow_fill_outcomes,         # Ph 22
    'shadow_report': cmd_shadow_report,                       # Ph 22
    'spectral_alpha_dashboard': cmd_spectral_alpha_dashboard, # Ph 26
    'track_outcomes': cmd_track_outcomes,                     # Ph 32
    'weekly_performance_report': cmd_weekly_performance_report, # Ph 32
    'gate_stats': cmd_gate_stats,                             # Ph 27 dashboard
    'model_drift': cmd_model_drift,                           # Ph 33 drift monitor
    'signal_freshness': cmd_signal_freshness,                 # Ph 36 freshness check
    'ml_score_delta': cmd_ml_score_delta,                     # Ph 39 ML score momentum
    'signal_age': cmd_signal_age,                             # Ph 40 signal age tracker
    'check_entry_triggers': cmd_check_entry_triggers,         # Ph 44 entry trigger detection
    'stop_loss_hits': cmd_stop_loss_hits,                     # Ph 45 stop-loss hit detector
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
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
