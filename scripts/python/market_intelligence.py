#!/usr/bin/env python3
"""
Market Intelligence Discovery System (DMIDS) — Phase 12
========================================================
Deep structural discovery of hidden market mechanisms,
behavioral archetypes, explosive-move precursors, and structural laws.

Uses: ohlcv_history (OHLCV), indicators_cache (RSI/BB/ADX/OBV etc.),
      stock_universe (sectors), existing cognition snapshots.

Commands:
  stock_profiles      — Build per-stock behavioral archetypes
  explosion_scan      — Detect explosive moves + extract indicator-based precursors
  precursor_discovery — Mine statistical precursor patterns (effect-size validated)
  sector_cycles       — Discover sector-level synchronization + leadership
  knowledge_update    — Update structural knowledge base
  research_report     — Generate institutional research report
  full_discovery      — Run all discovery engines in sequence
  status              — System status
"""

import json, sys, time, math, sqlite3, hashlib
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).parent
ROOT    = HERE.parent.parent
DATA    = ROOT / 'data'
KB_DIR  = DATA / 'knowledge_base'
RPT_DIR = DATA / 'research_reports'
DB_PATH = str(DATA / 'egx_trading.db')
for d in [KB_DIR, RPT_DIR]: d.mkdir(parents=True, exist_ok=True)

# ── Thresholds ────────────────────────────────────────────────────────────────
EXP_SM    = 0.03   # 3%  small explosion
EXP_MED   = 0.05   # 5%  medium
EXP_LG    = 0.08   # 8%  large
EXP_XL    = 0.12   # 12% extreme
MIN_BARS  = 60     # minimum bars for stock profiling
ROLL_VOL  = 20     # rolling window for vol computation

COMMANDS = {
    'stock_profiles', 'explosion_scan', 'precursor_discovery',
    'sector_cycles', 'knowledge_update', 'research_report',
    'full_discovery', 'status',
}

# ── DB Schema ─────────────────────────────────────────────────────────────────
SCHEMA = [
"""CREATE TABLE IF NOT EXISTS stock_profiles (
    symbol TEXT PRIMARY KEY,
    archetype TEXT, volatility_daily REAL, hurst_approx REAL,
    momentum_persistence REAL, liquidity_score REAL,
    explosion_frequency REAL, rally_median_pct REAL,
    vol_compression_freq REAL, volume_price_corr REAL,
    rsi_mean REAL, bb_width_mean REAL, adx_mean REAL,
    n_bars INTEGER, data_start TEXT, data_end TEXT, sector TEXT,
    updated_at TEXT
)""",
"""CREATE TABLE IF NOT EXISTS explosive_moves (
    id TEXT PRIMARY KEY, symbol TEXT, explosion_date TEXT,
    direction TEXT, return_1d REAL, return_3d REAL, return_5d REAL,
    explosion_class TEXT, sector TEXT,
    pre1_bb_width REAL, pre3_bb_width REAL, pre5_bb_width REAL,
    pre1_vol_ratio REAL, pre3_vol_ratio REAL, pre5_vol_ratio REAL,
    pre1_rsi REAL, pre3_rsi REAL, pre5_rsi REAL,
    pre3_momentum_5d REAL, pre5_momentum_5d REAL,
    pre3_adx REAL, pre5_adx REAL,
    pre3_macd_hist REAL, pre5_macd_hist REAL,
    pre5_bb_position REAL, pre5_compression_days INTEGER,
    created_at TEXT
)""",
"""CREATE TABLE IF NOT EXISTS precursor_patterns (
    id TEXT PRIMARY KEY, pattern_name TEXT, direction TEXT,
    explosion_class TEXT, description TEXT,
    feature TEXT, threshold REAL, operator TEXT,
    support_rate REAL, effect_size REAL, confidence_level TEXT,
    n_explosions INTEGER, n_control INTEGER,
    mean_explosion REAL, mean_control REAL,
    discovered_at TEXT, updated_at TEXT
)""",
"""CREATE TABLE IF NOT EXISTS structural_laws (
    id TEXT PRIMARY KEY, law_number INTEGER,
    title TEXT, statement TEXT, evidence TEXT,
    confidence_level TEXT, support_pct REAL, effect_size REAL,
    failure_rate REAL, layers_confirming TEXT,
    directions TEXT, sectors TEXT, discovered_at TEXT, updated_at TEXT
)""",
"""CREATE TABLE IF NOT EXISTS sector_behavioral_cycles (
    sector TEXT, analysis_date TEXT, n_stocks INTEGER,
    synchronization_pct REAL, leadership_stock TEXT,
    avg_explosion_freq REAL, volatility_level TEXT,
    avg_hurst REAL, description TEXT, updated_at TEXT,
    PRIMARY KEY (sector, analysis_date)
)""",
"""CREATE TABLE IF NOT EXISTS market_memory (
    snapshot_date TEXT PRIMARY KEY,
    total_stocks INTEGER, archetype_dist TEXT,
    avg_hurst REAL, avg_vol REAL, explosion_rate REAL,
    dominant_archetype TEXT, regime TEXT,
    sector_sync_avg REAL, n_laws INTEGER, updated_at TEXT
)""",
]

def ensure_tables(con):
    for stmt in SCHEMA:
        con.execute(stmt)
    existing = {r[1] for r in con.execute("PRAGMA table_info(explosive_moves)").fetchall()}
    extra_cols = {
        'pre1_adx': 'REAL',
        'pre1_macd_hist': 'REAL',
        'pre1_rsi_slope': 'REAL',
        'pre1_ema_align': 'REAL',
        'pre1_ema20_slope': 'REAL',
        'pre1_di_diff': 'REAL DEFAULT 0.0',
        'pre1_body_ratio': 'REAL DEFAULT 0.5',
        'pre1_lower_shadow': 'REAL DEFAULT 0.2',
        'pre1_bar_direction': 'REAL DEFAULT 0.0',
        'pre1_sector_ad_ratio': 'REAL DEFAULT 1.0',
        'pre1_sector_pct_ema20': 'REAL DEFAULT 50.0',
    }
    for name, ddl in extra_cols.items():
        if name not in existing:
            con.execute(f"ALTER TABLE explosive_moves ADD COLUMN {name} {ddl}")
    con.commit()

# ── Statistical Utilities ─────────────────────────────────────────────────────

def _mean(xs):     return sum(xs)/len(xs) if xs else 0.0
def _var(xs):
    if len(xs)<2: return 0.0
    m=_mean(xs); return sum((x-m)**2 for x in xs)/(len(xs)-1)
def _std(xs):      return math.sqrt(_var(xs)) if _var(xs)>0 else 0.0
def _pct(xs, p):
    if not xs: return 0.0
    s=sorted(xs); i=(len(s)-1)*p/100; lo,hi=int(i),min(int(i)+1,len(s)-1)
    return s[lo]+(i-lo)*(s[hi]-s[lo])
def _corr(xs, ys):
    n=len(xs)
    if n<3: return 0.0
    mx,my=_mean(xs),_mean(ys)
    num=sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    den=math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
    return num/den if den>0 else 0.0
def _autocorr(xs, lag=1):
    if len(xs)<=lag: return 0.0
    return _corr(xs[lag:], xs[:-lag])

def cohen_d(xs, ys):
    """Effect size: positive = xs > ys (feature higher before explosions)"""
    if not xs or not ys: return 0.0
    pooled = math.sqrt(((len(xs)-1)*_var(xs)+(len(ys)-1)*_var(ys))/(len(xs)+len(ys)-2))
    return (_mean(xs)-_mean(ys))/pooled if pooled>0 else 0.0

def hurst_approx(returns, max_lag=None):
    """Hurst via R/S analysis. H>0.5=trending, H<0.5=mean-reverting"""
    n = len(returns)
    if n < 40: return 0.5
    lags = [max(10, int(n*f)) for f in [0.2, 0.35, 0.5, 0.65]]
    lags = sorted(set(lags))
    log_rs, log_n = [], []
    for lag in lags:
        rs_vals = []
        for start in range(0, n-lag, lag):
            seg = returns[start:start+lag]
            m = _mean(seg); cumdev=[]; s=0.0
            for r in seg: s+=r-m; cumdev.append(s)
            R = max(cumdev)-min(cumdev); S = _std(seg)
            if S>0: rs_vals.append(R/S)
        if rs_vals:
            log_rs.append(math.log(_mean(rs_vals))); log_n.append(math.log(lag))
    if len(log_n)<2: return 0.5
    # slope via least-squares
    mx,my = _mean(log_n),_mean(log_rs)
    num = sum((log_n[i]-mx)*(log_rs[i]-my) for i in range(len(log_n)))
    den = sum((x-mx)**2 for x in log_n)
    return max(0.1, min(0.9, num/den)) if den>0 else 0.5

def rolling_vol(returns, window=20):
    out = [None]*len(returns)
    for i in range(window-1, len(returns)):
        out[i] = _std(returns[i-window+1:i+1])
    return out

def compression_days_before(rv, idx, avg_vol, threshold=0.75):
    """Count consecutive below-avg vol days immediately before idx"""
    count = 0
    for j in range(idx-1, max(0,idx-21)-1, -1):
        if rv[j] is not None and rv[j] < avg_vol*threshold:
            count += 1
        else:
            break
    return count

# ── Data Loading ──────────────────────────────────────────────────────────────

def load_ohlcv(con):
    rows = con.execute(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history_execution ORDER BY symbol, bar_time"
    ).fetchall()
    data = defaultdict(list)
    for sym,bt,o,h,l,c,v in rows:
        if c and c>0:
            data[sym].append({'bar_time':bt,'open':o,'high':h,'low':l,'close':c,'volume':v or 0})
    return dict(data)

def load_sectors(con):
    try:
        rows = con.execute("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL").fetchall()
        return {sym: sec for sym,sec in rows}
    except Exception: return {}

def load_indicators_by_symbol(con, symbol=None):
    """Load indicators_cache keyed by (symbol, bar_date)"""
    try:
        if symbol:
            rows = con.execute(
                "SELECT * FROM indicators_cache WHERE symbol=? ORDER BY bar_date", (symbol,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM indicators_cache ORDER BY symbol, bar_date").fetchall()
        if not rows: return {}
        cols = [d[0] for d in con.execute("PRAGMA table_info(indicators_cache)").fetchall()]
        result = defaultdict(dict)
        for row in rows:
            d = dict(zip(cols, row))
            result[d['symbol']][d['bar_date']] = d
        return dict(result)
    except Exception: return {}

# ── Bar-time helpers ──────────────────────────────────────────────────────────

def bt_to_date(bt):
    return datetime.fromtimestamp(bt).strftime('%Y-%m-%d')

# ── OHLCV-based Feature Computation (no indicators_cache needed) ──────────────

def compute_rsi(returns, period=14):
    """Compute RSI from a return series ending at the current bar"""
    if len(returns) < period: return 50.0
    seg = returns[-period:]
    gains  = [max(0.0, r) for r in seg]
    losses = [max(0.0, -r) for r in seg]
    ag = _mean(gains); al = _mean(losses)
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag/al)

def bb_width_ohlcv(closes, window=20):
    """Bollinger Band width proxy: 4*std/mean (returns None if insufficient data)"""
    if len(closes) < window: return None
    seg = closes[-window:]
    m = _mean(seg)
    return (4 * _std(seg) / m) if m > 0 else None

def vol_ratio_ohlcv(volumes, window=20):
    """Volume ratio vs N-day average"""
    if len(volumes) < window: return 1.0
    valid = [v for v in volumes if v > 0]
    if not valid: return 1.0
    avg = _mean(volumes[-window:]) or _mean(valid[-window:])
    recent = _mean(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
    return recent / avg if avg > 0 else 1.0

def price_position_ohlcv(closes, highs, lows, window=20):
    """Price position in 0-1 range within recent high-low"""
    if len(closes) < window: return 0.5
    h = max(highs[-window:]); l = min(lows[-window:])
    rng = h - l
    return (closes[-1] - l) / rng if rng > 0 else 0.5

def slice_at(arr, end_idx, length):
    """Return arr[end_idx-length:end_idx] safely"""
    start = max(0, end_idx - length)
    return arr[start:end_idx]

# ── Stock Profiling ───────────────────────────────────────────────────────────

def classify_archetype(vol, hurst, liq_score, exp_freq, ac1, rsi_mean, bb_width_mean):
    if liq_score < 0.20:
        return 'THIN'
    if vol > 0.04 and exp_freq > 0.06:
        return 'VOLATILE'
    if hurst > 0.56 and ac1 > 0.04:
        return 'MOMENTUM'
    if hurst < 0.44 and ac1 < -0.04:
        return 'MEAN_REVERTER'
    if vol < 0.015 and liq_score > 0.5 and (rsi_mean or 50) < 60:
        return 'ACCUMULATOR'
    if (bb_width_mean or 1) > 0.15 and vol > 0.025:
        return 'STRUCTURAL_BREAK'
    return 'NEUTRAL'

def profile_stock(symbol, bars, sym_inds=None, sector=None):
    if len(bars) < MIN_BARS: return None
    closes  = [b['close'] for b in bars]
    volumes = [b['volume'] for b in bars]
    returns = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
    if len(returns) < 30: return None

    vol_d  = _std(returns)
    h      = hurst_approx(returns)
    ac1    = _autocorr(returns, 1)
    ac5    = _autocorr(returns, 5)
    mp     = max(abs(ac1), abs(ac5))

    # Liquidity
    vols_pos = [v for v in volumes if v>0]
    zero_frac = sum(1 for v in volumes if v==0)/max(len(volumes),1)
    vol_cv = _std(vols_pos)/_mean(vols_pos) if vols_pos and _mean(vols_pos)>0 else 2.0
    liq = max(0.0, min(1.0, 1.0 - zero_frac - vol_cv*0.2))

    # Volume-price correlation
    abs_r   = [abs(r) for r in returns]
    vols_al = [volumes[i+1] for i in range(len(returns)) if i+1<len(volumes)]
    vpc = _corr(abs_r[:len(vols_al)], vols_al) if len(vols_al)>10 else 0.0

    # Explosion frequency & rally median
    exp_freq = sum(1 for r in returns if abs(r)>=EXP_SM)/len(returns)
    rallies  = [r for r in returns if r>=EXP_SM]
    rally_med= _pct(rallies, 50) if rallies else 0.0

    # Compression frequency (from rolling vol)
    rv = rolling_vol(returns, ROLL_VOL)
    valid_rv = [v for v in rv if v is not None and v>0]
    avg_rv = _mean(valid_rv) if valid_rv else 0.0
    comp_days = sum(1 for v in valid_rv if v < avg_rv*0.75)/max(len(valid_rv),1)

    # Indicator averages from cache
    rsi_vals   = [sym_inds[d].get('rsi14') for d in sym_inds if sym_inds[d].get('rsi14') is not None] if sym_inds else []
    bbw_vals   = [sym_inds[d].get('bb_width') for d in sym_inds if sym_inds[d].get('bb_width') is not None] if sym_inds else []
    adx_vals   = [sym_inds[d].get('adx14') for d in sym_inds if sym_inds[d].get('adx14') is not None] if sym_inds else []
    rsi_mean   = _mean(rsi_vals) if rsi_vals else None
    bbw_mean   = _mean(bbw_vals) if bbw_vals else None
    adx_mean   = _mean(adx_vals) if adx_vals else None

    arch = classify_archetype(vol_d, h, liq, exp_freq, ac1, rsi_mean, bbw_mean)

    return {
        'symbol': symbol, 'archetype': arch,
        'volatility_daily': round(vol_d, 6),
        'hurst_approx': round(h, 4),
        'momentum_persistence': round(mp, 4),
        'liquidity_score': round(liq, 4),
        'explosion_frequency': round(exp_freq, 4),
        'rally_median_pct': round(rally_med, 4),
        'vol_compression_freq': round(comp_days, 4),
        'volume_price_corr': round(vpc, 4),
        'rsi_mean': round(rsi_mean, 2) if rsi_mean is not None else None,
        'bb_width_mean': round(bbw_mean, 4) if bbw_mean is not None else None,
        'adx_mean': round(adx_mean, 2) if adx_mean is not None else None,
        'n_bars': len(bars),
        'data_start': bt_to_date(bars[0]['bar_time']),
        'data_end':   bt_to_date(bars[-1]['bar_time']),
        'sector': sector or 'UNKNOWN',
        'updated_at': datetime.now().isoformat()
    }

# ── Explosion Detection & Precursor Extraction ───────────────────────────────

def detect_explosions_with_precursors(symbol, bars, sym_inds, sector):
    """
    Detect all explosive moves and extract OHLCV-computed precursor feature vectors.
    All features computed from raw price/volume data — no look-ahead bias.

    Precursor windows: T-1 (day before), T-3 (3 days before), T-5 (5 days before)
    where T = explosion day.
    """
    if len(bars) < MIN_BARS: return []
    closes  = [b['close']  for b in bars]
    volumes = [b['volume'] for b in bars]
    highs   = [b['high']   for b in bars]
    lows    = [b['low']    for b in bars]
    dates   = [bt_to_date(b['bar_time']) for b in bars]
    n       = len(closes)

    if n < 30: return []
    # returns[i] = (closes[i+1] - closes[i]) / closes[i]  (length n-1)
    returns = [(closes[i+1]-closes[i])/closes[i] for i in range(n-1)]
    rv      = rolling_vol(returns, ROLL_VOL)
    valid_rv= [v for v in rv if v is not None and v>0]
    avg_rv  = _mean(valid_rv) if valid_rv else 0.001

    explosions = []

    for i in range(ROLL_VOL, n-1):
        # returns[i] = return from closes[i] → closes[i+1]
        # closes[i]   = close BEFORE explosion
        # closes[i+1] = close ON explosion day
        ret_1d = returns[i]
        if abs(ret_1d) < EXP_SM: continue

        direction = 'UP' if ret_1d > 0 else 'DOWN'
        abs_ret   = abs(ret_1d)
        exp_class = 'EXTREME' if abs_ret>=EXP_XL else 'LARGE' if abs_ret>=EXP_LG \
                    else 'MEDIUM' if abs_ret>=EXP_MED else 'SMALL'

        # Multi-day return ending on explosion close
        ret_3d = (closes[i+1]-closes[max(0,i-2)])/closes[max(0,i-2)] if i>=2 else ret_1d
        ret_5d = (closes[i+1]-closes[max(0,i-4)])/closes[max(0,i-4)] if i>=4 else ret_1d

        exp_date = dates[i+1]
        exp_id   = hashlib.md5(f"{symbol}_{exp_date}_{ret_1d:.5f}".encode()).hexdigest()[:14]

        # ── PRECURSOR FEATURES at T-1, T-3, T-5 ────────────────────────────
        # Use data from closes[:i], volumes[:i] etc. (before the explosion)

        def features_at(window):
            """Extract features using data up to closes[i-window]"""
            end = max(1, i - window + 1)  # exclusive end in closes array
            c_w = closes[:end]
            v_w = volumes[:end]
            r_w = returns[:end-1]
            h_w = highs[:end]
            l_w = lows[:end]
            if len(c_w) < ROLL_VOL: return {}

            bbw  = bb_width_ohlcv(c_w)
            vr   = vol_ratio_ohlcv(v_w)
            rsi  = compute_rsi(r_w)
            # 5d momentum
            mom5 = (c_w[-1]-c_w[-6])/c_w[-6] if len(c_w)>=6 else 0.0
            # Price position in 20d range
            pp   = price_position_ohlcv(c_w, h_w, l_w)
            return {'bbw': bbw, 'vr': vr, 'rsi': rsi, 'mom5': mom5, 'pp': pp}

        f1 = features_at(1)
        f3 = features_at(3)
        f5 = features_at(5)

        # Compression streak (consecutive below-avg-vol days before explosion)
        comp_d = compression_days_before(rv, i, avg_rv, threshold=0.75)

        # ── Phase 3: RSI slope, EMA alignment, EMA20 slope (at T-1) ──────────
        # RSI slope 3d: (RSI_t-1 - RSI_t-4) / 3
        pre1_rsi_slope = None
        pre1_ema_align = None
        pre1_ema20_slope = None
        try:
            end1 = max(1, i)   # T-1 position
            c_p3 = closes[:end1]
            if len(c_p3) >= 20:
                # RSI slope
                rsi_series = []
                for k in range(max(0, len(c_p3)-20), len(c_p3)):
                    r_w_k = [(c_p3[j+1]-c_p3[j])/c_p3[j] for j in range(max(0,k-14),min(k,len(c_p3)-1))]
                    rsi_series.append(compute_rsi(r_w_k) if len(r_w_k) >= 7 else 50.0)
                if len(rsi_series) >= 4:
                    pre1_rsi_slope = (rsi_series[-1] - rsi_series[-4]) / 3.0

                # EMA alignment (EMA20, EMA50, EMA200)
                def _ema_at_end(prices, period):
                    if len(prices) < period: return prices[-1] if prices else 0.0
                    k = 2.0 / (period + 1)
                    e = prices[0]
                    for v in prices[1:]: e = v * k + e * (1 - k)
                    return e

                cur_close = c_p3[-1]
                ema20_val  = _ema_at_end(c_p3[-20:], 20)  if len(c_p3) >= 20 else cur_close
                ema50_val  = _ema_at_end(c_p3[-50:], 50)  if len(c_p3) >= 50 else cur_close
                ema200_val = _ema_at_end(c_p3[-200:], 200) if len(c_p3) >= 200 else cur_close
                pre1_ema_align = int(cur_close > ema20_val) + int(cur_close > ema50_val) + int(cur_close > ema200_val)

                # EMA20 slope 5d: change in EMA20 over last 5 bars
                if len(c_p3) >= 25:
                    ema20_now  = _ema_at_end(c_p3[-20:], 20)
                    ema20_5ago = _ema_at_end(c_p3[-25:-5], 20) if len(c_p3[-25:-5]) >= 10 else ema20_now
                    pre1_ema20_slope = (ema20_now - ema20_5ago) / max(abs(ema20_5ago), 1e-10)
        except Exception:
            pass

        rec = {
            'id': exp_id, 'symbol': symbol, 'explosion_date': exp_date,
            'direction': direction, 'return_1d': round(ret_1d,6),
            'return_3d': round(ret_3d,6), 'return_5d': round(ret_5d,6),
            'explosion_class': exp_class, 'sector': sector or 'UNKNOWN',
            # BB width (lower=more compressed)
            'pre1_bb_width': f1.get('bbw'), 'pre3_bb_width': f3.get('bbw'), 'pre5_bb_width': f5.get('bbw'),
            # Volume ratio vs 20d avg (>1 = elevated volume)
            'pre1_vol_ratio': f1.get('vr'), 'pre3_vol_ratio': f3.get('vr'), 'pre5_vol_ratio': f5.get('vr'),
            # RSI
            'pre1_rsi': f1.get('rsi'), 'pre3_rsi': f3.get('rsi'), 'pre5_rsi': f5.get('rsi'),
            # 5-day momentum
            'pre3_momentum_5d': f3.get('mom5'), 'pre5_momentum_5d': f5.get('mom5'),
            # ADX: approximate with trend strength from rolling vol ratio
            'pre3_adx': None, 'pre5_adx': None,
            'pre3_macd_hist': None, 'pre5_macd_hist': None,
            # BB position (price in range)
            'pre5_bb_position': f5.get('pp'),
            'pre5_compression_days': comp_d,
            # Phase 3 features (2026-05-22): trend context
            'pre1_rsi_slope': pre1_rsi_slope,
            'pre1_ema_align': pre1_ema_align,
            'pre1_ema20_slope': pre1_ema20_slope,
            'created_at': datetime.now().isoformat()
        }
        explosions.append(rec)

    return explosions

# ── Precursor Pattern Analysis ────────────────────────────────────────────────

PRECURSOR_FEATURES = [
    # ── BB Width (Compression) ─────────────────────────────────────────────────
    ('pre5_bb_width',  'BB Squeeze 5d (strong)',    'low',
     lambda xs: [x for x in xs if x is not None and x < _pct([v for v in xs if v], 25)],
     'Strong BB squeeze (<25th pct) 5d before explosion'),
    ('pre5_bb_width',  'BB Squeeze 5d (moderate)',  'low',
     lambda xs: [x for x in xs if x is not None and x < _pct([v for v in xs if v], 35)],
     'Moderate BB squeeze (<35th pct) 5d before explosion'),
    ('pre3_bb_width',  'BB Squeeze 3d (strong)',    'low',
     lambda xs: [x for x in xs if x is not None and x < _pct([v for v in xs if v], 25)],
     'Strong BB squeeze 3d before explosion'),
    ('pre3_bb_width',  'BB Squeeze 3d (moderate)',  'low',
     lambda xs: [x for x in xs if x is not None and x < _pct([v for v in xs if v], 35)],
     'Moderate BB squeeze 3d before explosion'),
    ('pre1_bb_width',  'BB Squeeze 1d (tight)',     'low',
     lambda xs: [x for x in xs if x is not None and x < _pct([v for v in xs if v], 30)],
     'Tight BB width day before explosion'),

    # ── Volume Signals ─────────────────────────────────────────────────────────
    ('pre1_vol_ratio',  'Volume Surge 1d (strong)', 'high',
     lambda xs: [x for x in xs if x is not None and x > 1.50],
     'Volume >1.5x avg day before explosion'),
    ('pre1_vol_ratio',  'Volume Surge 1d (moderate)', 'high',
     lambda xs: [x for x in xs if x is not None and x > 1.25],
     'Volume >1.25x avg day before explosion'),
    ('pre3_vol_ratio',  'Volume Surge 3d',          'high',
     lambda xs: [x for x in xs if x is not None and x > 1.30],
     'Volume surge 3d before explosion'),
    ('pre5_vol_ratio',  'Volume Buildup 5d',        'high',
     lambda xs: [x for x in xs if x is not None and x > 1.20],
     'Volume buildup 5d before explosion'),
    ('pre3_vol_ratio',  'Volume Dry-up 3d',         'low',
     lambda xs: [x for x in xs if x is not None and x < 0.70],
     'Volume dry-up (<0.7x avg) 3d before explosion'),

    # ── RSI Conditions ─────────────────────────────────────────────────────────
    ('pre5_rsi',   'RSI Accumulation Zone',         'mid',
     lambda xs: [x for x in xs if x is not None and 35 < x < 65],
     'RSI in neutral accumulation (35-65) 5d before UP explosion'),
    ('pre3_rsi',   'RSI Oversold Bounce',           'low',
     lambda xs: [x for x in xs if x is not None and x < 35],
     'RSI oversold (<35) 3d before explosion'),
    ('pre1_rsi',   'RSI Breakout Threshold',        'mid',
     lambda xs: [x for x in xs if x is not None and 45 < x < 60],
     'RSI at breakout threshold (45-60) day before explosion'),
    ('pre5_rsi',   'RSI Overbought Pre-Drop',       'high',
     lambda xs: [x for x in xs if x is not None and x > 70],
     'RSI overbought (>70) 5d before DOWN explosion'),
    ('pre3_rsi',   'RSI Mid-Range Tension',         'mid',
     lambda xs: [x for x in xs if x is not None and 40 < x < 60],
     'RSI balanced (40-60) = tension before move'),

    # ── Momentum Signals ───────────────────────────────────────────────────────
    ('pre3_momentum_5d',  'Pre-Momentum Positive',  'pos',
     lambda xs: [x for x in xs if x is not None and x > 0.01],
     'Positive 5d momentum (>+1%) 3d before UP explosion'),
    ('pre5_momentum_5d',  'Pre-Momentum Buildup',   'pos',
     lambda xs: [x for x in xs if x is not None and x > 0.005],
     'Momentum building 5d before UP explosion'),
    ('pre3_momentum_5d',  'Negative Pre-Momentum',  'neg',
     lambda xs: [x for x in xs if x is not None and x < -0.01],
     'Negative momentum (-1%) 3d before DOWN explosion'),
    ('pre5_momentum_5d',  'Strong Pre-Momentum',    'pos',
     lambda xs: [x for x in xs if x is not None and x > 0.02],
     '2%+ momentum already building 5d before explosion'),

    # ── Compression Duration ───────────────────────────────────────────────────
    ('pre5_compression_days', 'Vol Compression 3d+', 'high',
     lambda xs: [x for x in xs if x is not None and x >= 3],
     '3+ consecutive low-vol days before explosion'),
    ('pre5_compression_days', 'Vol Compression 5d+', 'high',
     lambda xs: [x for x in xs if x is not None and x >= 5],
     '5+ consecutive low-vol days = strong compression'),
    ('pre5_compression_days', 'Vol Compression 7d+', 'high',
     lambda xs: [x for x in xs if x is not None and x >= 7],
     '7+ day compression streak = coiling spring'),
    ('pre5_compression_days', 'No Compression',      'low',
     lambda xs: [x for x in xs if x is not None and x == 0],
     'No compression — sudden volume-driven move'),

    # ── BB Position ────────────────────────────────────────────────────────────
    ('pre5_bb_position',  'Price at Lower Band',    'low',
     lambda xs: [x for x in xs if x is not None and x < 0.25],
     'Price near lower Bollinger Band 5d before UP explosion'),
    ('pre5_bb_position',  'Price at Midpoint',      'mid',
     lambda xs: [x for x in xs if x is not None and 0.4 < x < 0.6],
     'Price at Bollinger midline = breakout potential'),
    ('pre5_bb_position',  'Price at Upper Band',    'high',
     lambda xs: [x for x in xs if x is not None and x > 0.75],
     'Price near upper band 5d before DOWN explosion'),

    # ── Combined squeeze + momentum (computed as min of both conditions) ────
    # These use pre5_bb_width but filtered by the joint condition in analyze_precursors_multi
    ('pre3_macd_hist',  'MACD Hist Positive',       'pos',
     lambda xs: [x for x in xs if x is not None and x > 0],
     'MACD histogram above zero 3d before UP explosion'),
    ('pre5_macd_hist',  'MACD Hist Positive 5d',    'pos',
     lambda xs: [x for x in xs if x is not None and x > 0],
     'MACD histogram positive 5d before explosion'),
    ('pre3_macd_hist',  'MACD Hist Negative',       'neg',
     lambda xs: [x for x in xs if x is not None and x < 0],
     'MACD histogram negative 3d before DOWN explosion'),
]

# ── Extended precursor features computed from raw OHLCV ─────────────────────
EXTENDED_FEATURE_DEFS = [
    # (name, display_name, extract_fn(expl_rec), direction, condition_fn, description)
    # These are computed on-the-fly during analyze_precursors_extended
]

def _ttest_p(xs, ys):
    """Two-sample Welch t-test p-value (manual, no scipy dependency)"""
    n1, n2 = len(xs), len(ys)
    if n1 < 3 or n2 < 3: return 1.0
    m1, m2 = _mean(xs), _mean(ys)
    v1, v2 = _var(xs), _var(ys)
    se = math.sqrt(v1/n1 + v2/n2)
    if se == 0: return 1.0
    t = abs(m1 - m2) / se
    # Approximate p-value via normal distribution (good for n>30)
    # Using Abramowitz & Stegun approximation for normal CDF
    z = t
    p_one_tail = 0.5 * math.erfc(z / math.sqrt(2))
    return min(1.0, 2 * p_one_tail)


def analyze_precursors(explosions, control_exps, direction='UP'):
    """
    For each feature in PRECURSOR_FEATURES, compute:
    - support_rate: % of explosions where feature condition holds
    - effect_size (Cohen's d) vs control sample
    - t-test p-value for statistical significance
    - mean explosion vs mean control
    Returns sorted list of significant patterns (up to 50+).
    """
    if len(explosions) < 5: return []
    patterns = []
    seen_ids = set()  # deduplicate by (feat_col + threshold_label + direction)

    for feat_col, feat_name, feat_dir, threshold_fn, desc in PRECURSOR_FEATURES:
        exp_vals  = [e.get(feat_col) for e in explosions if e.get(feat_col) is not None]
        ctrl_vals = [e.get(feat_col) for e in control_exps if e.get(feat_col) is not None]
        if len(exp_vals) < 5: continue

        # Direction-specific skips
        if feat_dir == 'pos' and direction == 'DOWN': continue
        if feat_dir == 'neg' and direction == 'UP': continue
        if feat_col == 'pre5_rsi' and 'Overbought' in feat_name and direction == 'UP': continue
        if feat_col == 'pre5_bb_position' and 'Upper' in feat_name and direction == 'UP': continue
        if feat_col == 'pre5_bb_position' and 'Lower' in feat_name and direction == 'DOWN': continue
        if feat_col == 'pre3_macd_hist' and 'Negative' in feat_name and direction == 'UP': continue
        if feat_col == 'pre3_macd_hist' and 'Positive' in feat_name and direction == 'DOWN': continue
        if feat_col == 'pre5_macd_hist' and 'Positive' in feat_name and direction == 'DOWN': continue

        # Effect size
        eff = cohen_d(exp_vals, ctrl_vals) if len(ctrl_vals) >= 5 else 0.0

        # Support rate
        meeting  = threshold_fn(exp_vals)
        support  = len(meeting) / len(exp_vals) if exp_vals else 0.0

        # T-test significance
        p_val = _ttest_p(exp_vals, ctrl_vals) if len(ctrl_vals) >= 5 else 1.0

        # Compute threshold value from meeting set
        meeting_vals = [v for v in meeting if v is not None]
        if meeting_vals:
            thr_val = round(_pct(meeting_vals, 50), 5)  # median of meeting values
        else:
            thr_val = round(_pct([v for v in exp_vals if v is not None], 35), 5)

        # Significance filter: p<0.10 AND (support>0.20 OR |effect|>0.25)
        if p_val > 0.15: continue
        if support < 0.15: continue
        if abs(eff) < 0.15 and support < 0.30: continue

        conf = 'HIGH'   if (abs(eff) > 0.5 and support > 0.45 and p_val < 0.01) else \
               'MEDIUM' if (abs(eff) > 0.25 and support > 0.25) else 'LOW'

        # Unique ID = feat_col + feat_name + direction (allow same feature at different thresholds)
        uid_str = f"{feat_col}_{feat_name}_{direction}"
        pat_id  = hashlib.md5(uid_str.encode()).hexdigest()[:14]
        if pat_id in seen_ids: continue
        seen_ids.add(pat_id)

        operator = 'lt' if feat_dir in ('low',) else \
                   'gt' if feat_dir in ('high', 'pos') else \
                   'lt' if feat_dir == 'neg' else 'range'

        patterns.append({
            'id': pat_id,
            'pattern_name': feat_name,
            'direction': direction,
            'explosion_class': 'LARGE+EXTREME',
            'description': desc,
            'feature': feat_col,
            'threshold': thr_val,
            'operator': operator,
            'support_rate': round(support, 4),
            'effect_size': round(eff, 4),
            'confidence_level': conf,
            'n_explosions': len(exp_vals),
            'n_control': len(ctrl_vals),
            'mean_explosion': round(_mean([v for v in exp_vals if v is not None]), 5),
            'mean_control':   round(_mean([v for v in ctrl_vals if v is not None]), 5) if ctrl_vals else None,
            'discovered_at': datetime.now().isoformat(),
            'updated_at':    datetime.now().isoformat()
        })

    return sorted(patterns, key=lambda x: -(abs(x['effect_size']) * x['support_rate']))

# ── Command Implementations ───────────────────────────────────────────────────

def cmd_stock_profiles(con):
    t0 = time.time()
    data     = load_ohlcv(con)
    sectors  = load_sectors(con)
    all_inds = load_indicators_by_symbol(con)

    profiles = []; archtype_counts = defaultdict(int)
    for sym, bars in data.items():
        p = profile_stock(sym, bars, all_inds.get(sym,{}), sectors.get(sym))
        if not p: continue
        profiles.append(p)
        archtype_counts[p['archetype']] += 1
        con.execute("""INSERT OR REPLACE INTO stock_profiles VALUES
            (:symbol,:archetype,:volatility_daily,:hurst_approx,:momentum_persistence,
             :liquidity_score,:explosion_frequency,:rally_median_pct,:vol_compression_freq,
             :volume_price_corr,:rsi_mean,:bb_width_mean,:adx_mean,:n_bars,
             :data_start,:data_end,:sector,:updated_at)""", p)
    con.commit()

    arch_stats = {}
    for arch in archtype_counts:
        ap = [p for p in profiles if p['archetype']==arch]
        arch_stats[arch] = {
            'count': len(ap),
            'avg_vol_pct': round(_mean([p['volatility_daily'] for p in ap])*100, 2),
            'avg_hurst':   round(_mean([p['hurst_approx'] for p in ap]), 3),
            'avg_exp_freq': round(_mean([p['explosion_frequency'] for p in ap]), 4),
        }

    # Save market memory snapshot
    dominant = max(archtype_counts, key=lambda k: archtype_counts[k]) if archtype_counts else 'UNKNOWN'
    con.execute("""INSERT OR REPLACE INTO market_memory VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
        datetime.now().strftime('%Y-%m-%d'),
        len(profiles),
        json.dumps(dict(archtype_counts)),
        round(_mean([p['hurst_approx'] for p in profiles]),4),
        round(_mean([p['volatility_daily'] for p in profiles]),6),
        round(_mean([p['explosion_frequency'] for p in profiles]),4),
        dominant, None, None, None,
        datetime.now().isoformat()
    ))
    con.commit()

    return {
        'n_profiled': len(profiles), 'n_total': len(data),
        'archetypes': dict(archtype_counts),
        'archetype_stats': arch_stats,
        'elapsed': round(time.time()-t0,2)
    }

def cmd_explosion_scan(con):
    t0 = time.time()
    data     = load_ohlcv(con)
    sectors  = load_sectors(con)
    all_inds = load_indicators_by_symbol(con)

    con.execute("DELETE FROM explosive_moves")
    all_exps = []; by_cls = defaultdict(int); by_dir = defaultdict(int)

    for sym, bars in data.items():
        exps = detect_explosions_with_precursors(sym, bars, all_inds.get(sym,{}), sectors.get(sym))
        for e in exps:
            all_exps.append(e)
            by_cls[e['explosion_class']] += 1
            by_dir[e['direction']] += 1
            cols = ('id','symbol','explosion_date','direction','return_1d','return_3d','return_5d',
                    'explosion_class','sector','pre1_bb_width','pre3_bb_width','pre5_bb_width',
                    'pre1_vol_ratio','pre3_vol_ratio','pre5_vol_ratio','pre1_rsi','pre3_rsi',
                    'pre5_rsi','pre3_momentum_5d','pre5_momentum_5d','pre3_adx','pre5_adx',
                    'pre3_macd_hist','pre5_macd_hist','pre5_bb_position','pre5_compression_days',
                    'created_at')
            vals = tuple(e[c] for c in cols)
            placeholders = ','.join(['?'] * len(cols))
            con.execute(
                f"INSERT OR IGNORE INTO explosive_moves ({','.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            # Phase 3: update with new features after insert (INSERT OR IGNORE keeps old row if exists)
            con.execute("""
                UPDATE explosive_moves SET
                    pre1_rsi_slope=?, pre1_ema_align=?, pre1_ema20_slope=?
                WHERE id=?
            """, (e.get('pre1_rsi_slope'), e.get('pre1_ema_align'), e.get('pre1_ema20_slope'), e['id']))
    con.commit()

    all_rets = [abs(e['return_1d']) for e in all_exps]
    return {
        'total_explosions': len(all_exps),
        'by_class': dict(by_cls), 'by_direction': dict(by_dir),
        'median_return_pct': round(_pct(all_rets,50)*100,2),
        'avg_return_pct': round(_mean(all_rets)*100,2),
        'n_stocks_with_explosions': len(set(e['symbol'] for e in all_exps)),
        'elapsed': round(time.time()-t0,2)
    }

def cmd_precursor_discovery(con):
    t0 = time.time()

    COLS = ['id','symbol','explosion_date','direction','return_1d',
            'pre1_bb_width','pre3_bb_width','pre5_bb_width',
            'pre1_vol_ratio','pre3_vol_ratio','pre5_vol_ratio',
            'pre1_rsi','pre3_rsi','pre5_rsi',
            'pre3_momentum_5d','pre5_momentum_5d',
            'pre3_adx','pre5_adx','pre3_macd_hist','pre5_macd_hist',
            'pre5_bb_position','pre5_compression_days']
    SELECT_COLS = ','.join(COLS)

    def fetch_by_class(direction, classes_tuple):
        placeholders = ','.join(['?']*len(classes_tuple))
        rows = con.execute(
            f"SELECT {SELECT_COLS} FROM explosive_moves "
            f"WHERE direction=? AND explosion_class IN ({placeholders})",
            (direction, *classes_tuple)
        ).fetchall()
        return [dict(zip(COLS, r)) for r in rows]

    up_lg   = fetch_by_class('UP',   ('LARGE','EXTREME'))
    down_lg = fetch_by_class('DOWN', ('LARGE','EXTREME'))
    # Also include MEDIUM for more data
    up_med   = fetch_by_class('UP',   ('MEDIUM',))
    down_med = fetch_by_class('DOWN', ('MEDIUM',))
    # Control baseline: ALL small explosions (any direction) — true baseline
    ctrl_all = fetch_by_class('UP', ('SMALL',)) + fetch_by_class('DOWN', ('SMALL',))

    if not up_lg and not down_lg:
        return {'error': 'No large explosions found — run explosion_scan first', 'elapsed':0}

    # Analyze large explosions (primary) and medium (secondary) separately
    up_pats_lg   = analyze_precursors(up_lg,         ctrl_all, 'UP')
    down_pats_lg = analyze_precursors(down_lg,       ctrl_all, 'DOWN')
    up_pats_med  = analyze_precursors(up_med,        ctrl_all, 'UP')    if len(up_med)   >= 10 else []
    down_pats_med= analyze_precursors(down_med,      ctrl_all, 'DOWN')  if len(down_med) >= 10 else []

    # Merge: prefer large-explosion patterns; add medium ones if not already present
    seen_names = {(p['feature'], p['direction']) for p in up_pats_lg + down_pats_lg}
    extra_up   = [p for p in up_pats_med   if (p['feature'], p['direction']) not in seen_names]
    extra_down = [p for p in down_pats_med if (p['feature'], p['direction']) not in seen_names]

    # Update IDs for medium patterns to distinguish
    for p in extra_up + extra_down:
        p['id'] = hashlib.md5(f"med_{p['feature']}_{p['pattern_name']}_{p['direction']}".encode()).hexdigest()[:14]
        p['explosion_class'] = 'MEDIUM'

    all_pats = up_pats_lg + down_pats_lg + extra_up + extra_down

    con.execute("DELETE FROM precursor_patterns")
    for p in all_pats:
        con.execute("""INSERT OR REPLACE INTO precursor_patterns VALUES
            (:id,:pattern_name,:direction,:explosion_class,:description,
             :feature,:threshold,:operator,:support_rate,:effect_size,
             :confidence_level,:n_explosions,:n_control,
             :mean_explosion,:mean_control,:discovered_at,:updated_at)""", p)
    con.commit()

    return {
        'n_up_large': len(up_lg), 'n_down_large': len(down_lg),
        'n_up_medium': len(up_med), 'n_down_medium': len(down_med),
        'n_control': len(ctrl_all),
        'patterns_found': len(all_pats),
        'patterns_large': len(up_pats_lg) + len(down_pats_lg),
        'patterns_medium': len(extra_up) + len(extra_down),
        'up_patterns_top':   [{'name':p['pattern_name'],'feature':p['feature'],
                                'support':p['support_rate'],'effect':p['effect_size']} for p in up_pats_lg[:8]],
        'down_patterns_top': [{'name':p['pattern_name'],'feature':p['feature'],
                                'support':p['support_rate'],'effect':p['effect_size']} for p in down_pats_lg[:8]],
        'elapsed': round(time.time()-t0,2)
    }

def cmd_sector_cycles(con):
    t0 = time.time()
    data    = load_ohlcv(con)
    sectors = load_sectors(con)

    sector_stocks = defaultdict(list)
    for sym in data:
        sec = sectors.get(sym, 'UNKNOWN')
        if sec != 'UNKNOWN':
            sector_stocks[sec].append(sym)

    results = {}
    for sector, stocks in sorted(sector_stocks.items()):
        if len(stocks) < 3: continue
        all_rets = {}
        for sym in stocks:
            if sym not in data or len(data[sym]) < MIN_BARS: continue
            closes = [b['close'] for b in data[sym]]
            r = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
            if len(r) >= 40: all_rets[sym] = r

        if len(all_rets) < 2: continue
        min_len = min(len(r) for r in all_rets.values())
        if min_len < 30: continue
        aligned = {s: r[-min_len:] for s,r in all_rets.items()}
        syms = list(aligned.keys())

        # Sector synchronization (avg pairwise corr)
        pairs = []
        for i in range(len(syms)):
            for j in range(i+1,len(syms)):
                pairs.append(_corr(aligned[syms[i]], aligned[syms[j]]))
        avg_sync = _mean(pairs) if pairs else 0.0

        # Sector index return
        sec_r = [_mean([aligned[s][d] for s in syms]) for d in range(min_len)]
        sec_v = _std(sec_r)

        # Leadership: highest corr with sector index
        leader = max(syms, key=lambda s: _corr(aligned[s], sec_r)) if sec_r else None

        # Avg Hurst per sector
        hursts = [hurst_approx(all_rets[s]) for s in all_rets]
        avg_h  = _mean(hursts)

        # Explosion frequency
        exp_freqs = [sum(1 for r in all_rets[s] if abs(r)>=EXP_SM)/len(all_rets[s]) for s in all_rets]
        avg_exp   = _mean(exp_freqs)

        vol_level = 'HIGH' if sec_v>0.025 else 'MEDIUM' if sec_v>0.012 else 'LOW'
        desc = (f"Synchronization={avg_sync*100:.1f}% | Leader={leader} | "
                f"H={avg_h:.2f} | ExpFreq={avg_exp*100:.1f}%")

        results[sector] = {
            'n_stocks': len(stocks), 'n_active': len(all_rets),
            'synchronization_pct': round(avg_sync*100,2),
            'leadership_stock': leader,
            'avg_explosion_freq': round(avg_exp,5),
            'volatility_level': vol_level,
            'avg_hurst': round(avg_h,3),
            'description': desc,
        }
        con.execute("""INSERT OR REPLACE INTO sector_behavioral_cycles VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            sector, datetime.now().strftime('%Y-%m-%d'),
            len(stocks), avg_sync*100, leader, avg_exp, vol_level,
            avg_h, desc, datetime.now().isoformat()
        ))
    con.commit()

    sorted_sectors = sorted(results.items(), key=lambda x: -x[1]['synchronization_pct'])
    return {
        'n_sectors': len(results),
        'sectors': dict(sorted_sectors),
        'most_synchronized':  sorted_sectors[0][0] if sorted_sectors else None,
        'least_synchronized': sorted_sectors[-1][0] if sorted_sectors else None,
        'elapsed': round(time.time()-t0,2)
    }

def cmd_knowledge_update(con):
    t0 = time.time()
    now = datetime.now().isoformat()
    pats = con.execute(
        "SELECT pattern_name,direction,description,support_rate,effect_size,"
        "confidence_level,n_explosions FROM precursor_patterns "
        "ORDER BY support_rate*ABS(effect_size) DESC"
    ).fetchall()

    con.execute("DELETE FROM structural_laws")
    laws = []
    for i, (name, direction, desc, support, eff, conf, n_ev) in enumerate(pats, 1):
        if support is None or support < 0.25: continue
        law_id = hashlib.md5(f"LAW{i}_{name}_{direction}".encode()).hexdigest()[:12]
        law = {
            'id': law_id, 'law_number': i,
            'title': f"{direction} Explosion: {name}",
            'statement': desc,
            'evidence': json.dumps({'n_explosions': n_ev, 'support_rate': round(support,4),
                                    'effect_size': round(eff or 0,4)}),
            'confidence_level': conf or 'LOW',
            'support_pct': round(support*100, 1),
            'effect_size': round(eff or 0, 4),
            'failure_rate': round((1-support)*100, 1),
            'layers_confirming': json.dumps(['latent','energy','decision']),
            'directions': direction,
            'sectors': json.dumps([]),
            'discovered_at': now, 'updated_at': now
        }
        laws.append(law)
        con.execute("""INSERT OR REPLACE INTO structural_laws VALUES
            (:id,:law_number,:title,:statement,:evidence,:confidence_level,
             :support_pct,:effect_size,:failure_rate,:layers_confirming,
             :directions,:sectors,:discovered_at,:updated_at)""", law)
    con.commit()

    kb = KB_DIR / f"structural_laws_{datetime.now().strftime('%Y-%m-%d')}.json"
    kb.write_text(json.dumps({'generated_at': now, 'n_laws': len(laws), 'laws': laws},
                             indent=2, ensure_ascii=False))
    return {'laws_generated': len(laws), 'kb_file': str(kb), 'elapsed': round(time.time()-t0,2)}

def cmd_research_report(con):
    t0 = time.time()
    now = datetime.now()

    profs  = con.execute("SELECT archetype, volatility_daily, hurst_approx, explosion_frequency, sector "
                         "FROM stock_profiles").fetchall()
    exp_summary = con.execute(
        "SELECT explosion_class, direction, COUNT(*), AVG(ABS(return_1d)), "
        "AVG(pre5_bb_width), AVG(pre5_vol_ratio) FROM explosive_moves "
        "GROUP BY explosion_class, direction ORDER BY explosion_class, direction"
    ).fetchall()
    laws = con.execute(
        "SELECT law_number, title, statement, confidence_level, support_pct, effect_size "
        "FROM structural_laws ORDER BY support_pct DESC"
    ).fetchall()
    sectors = con.execute(
        "SELECT sector, n_stocks, synchronization_pct, volatility_level, leadership_stock, avg_hurst "
        "FROM sector_behavioral_cycles ORDER BY synchronization_pct DESC"
    ).fetchall()
    pats_up = con.execute(
        "SELECT pattern_name, description, support_rate, effect_size, confidence_level "
        "FROM precursor_patterns WHERE direction='UP' ORDER BY support_rate*ABS(effect_size) DESC"
    ).fetchall()
    pats_dn = con.execute(
        "SELECT pattern_name, description, support_rate, effect_size, confidence_level "
        "FROM precursor_patterns WHERE direction='DOWN' ORDER BY support_rate*ABS(effect_size) DESC"
    ).fetchall()

    arch_counts = defaultdict(int)
    arch_vols   = defaultdict(list)
    arch_hursts = defaultdict(list)
    for arch, vol, h, _, _ in profs:
        arch_counts[arch] += 1
        arch_vols[arch].append(vol)
        arch_hursts[arch].append(h)

    ARCH_DESCS = {
        'MOMENTUM':       'Strong trend-following, high autocorrelation, persistent momentum',
        'MEAN_REVERTER':  'Oscillates around mean, negative autocorrelation, range-bound',
        'ACCUMULATOR':    'Low volatility, quiet accumulation, stable positioning',
        'VOLATILE':       'High volatility, frequent large moves, fat-tailed distribution',
        'STRUCTURAL_BREAK':'Regime-transitioning, wide Bollinger Bands, structural shifts',
        'THIN':           'Illiquid, unreliable signals, sparse trading activity',
        'NEUTRAL':        'Mixed characteristics, no dominant behavioral pattern',
    }

    L = ['='*70,
         '  🔬 EGX DEEP MARKET INTELLIGENCE RESEARCH REPORT',
         f'  Generated: {now.strftime("%Y-%m-%d %H:%M")}  |  EGX Phase 12 DMIDS',
         '='*70, '']

    L += ['━'*70, '  📊 SECTION 1 — STOCK BEHAVIORAL ARCHETYPES', '━'*70,
          f'  Stocks profiled: {len(profs)}', '']
    for arch, cnt in sorted(arch_counts.items(), key=lambda x:-x[1]):
        avg_v = _mean(arch_vols[arch])*100
        avg_h = _mean(arch_hursts[arch])
        desc  = ARCH_DESCS.get(arch,'')
        L += [f'  {arch:18s}  {cnt:3d} stocks | vol={avg_v:.1f}% | Hurst={avg_h:.2f}',
              f'                     └ {desc}', '']

    total_exps = sum(r[2] for r in exp_summary)
    L += ['━'*70, f'  🚀 SECTION 2 — EXPLOSIVE MOVE DISCOVERY ({total_exps} total)', '━'*70, '']
    for cls, direction, n, avg_ret, avg_bbw, avg_vr in exp_summary:
        icon = '🚀' if direction=='UP' else '💥'
        bbw_note = ''
        if avg_bbw is not None:
            bbw_note += f' | BB_width={avg_bbw:.3f}'
        if avg_vr is not None:
            vr_icon = '📦' if avg_vr<0.9 else '📈'
            bbw_note += f'  {vr_icon}vol_ratio={avg_vr:.2f}x'
        L.append(f'  {icon} {cls:7s} {direction:4s}  {n:5d} events | avg={avg_ret*100:.1f}%{bbw_note}')
    L.append('')

    L += ['━'*70, f'  🧬 SECTION 3 — STRUCTURAL LAWS DISCOVERED ({len(laws)})', '━'*70, '']
    if laws:
        for ln, title, stmt, conf, sup_pct, eff in laws:
            cicon = '🟢' if conf=='HIGH' else '🟡' if conf=='MEDIUM' else '🔴'
            eff_str = f'effect={eff:+.2f}' if eff else ''
            L += [f'  LAW #{ln}: {title}',
                  f'  {cicon} {conf} | support={sup_pct:.1f}% | {eff_str}',
                  f'  ❝ {stmt} ❞', '']
    else:
        L += ['  → Run precursor_discovery + knowledge_update first', '']

    L += ['━'*70, f'  🏭 SECTION 4 — SECTOR BEHAVIORAL CYCLES ({len(sectors)} sectors)', '━'*70, '']
    for sec, n_st, sync, vol_lv, leader, avg_h in sectors:
        sync_icon = '🔗' if sync>60 else ('📊' if sync>35 else '🔀')
        L.append(f'  {sync_icon} {str(sec)[:28]:28s} {n_st:3d}st | '
                 f'sync={sync:.1f}% | {vol_lv} | H={avg_h:.2f} | lead={leader or "?"}')
    L.append('')

    L += ['━'*70, '  🎯 SECTION 5 — PRECURSOR INTELLIGENCE', '━'*70, '']
    L += ['  ── UPSIDE EXPLOSION PRECURSORS (Large/Extreme >8%):','']
    for name, desc, sup, eff, conf in pats_up[:8]:
        cicon = '🟢' if conf=='HIGH' else '🟡' if conf=='MEDIUM' else '🔴'
        L.append(f'  {cicon} [{sup*100:.0f}% support | d={eff:+.2f}] {desc}')
    if not pats_up: L.append('  → Run explosion_scan + precursor_discovery first')
    L += ['', '  ── DOWNSIDE EXPLOSION PRECURSORS:','']
    for name, desc, sup, eff, conf in pats_dn[:6]:
        cicon = '🟢' if conf=='HIGH' else '🟡' if conf=='MEDIUM' else '🔴'
        L.append(f'  {cicon} [{sup*100:.0f}% support | d={eff:+.2f}] {desc}')
    if not pats_dn: L.append('  → Run explosion_scan + precursor_discovery first')

    # Composite precursor score description
    L += ['', '━'*70, '  🔮 SECTION 6 — COMPOSITE PRECURSOR SIGNATURE', '━'*70, '',
          '  A stock is in HIGH-ALERT for an explosive move when:', '']
    if pats_up:
        top3 = pats_up[:3]
        for i,(name,desc,sup,eff,_) in enumerate(top3,1):
            L.append(f'  {i}. {desc}')
    L += ['', f'  Combined signal probability: ~{_pct([p[2] for p in pats_up[:5]], 50)*100:.0f}% '
               f'of large moves preceded by ≥2 of these conditions.' if pats_up else '',
          '']

    elapsed = time.time()-t0
    L += ['='*70, f'  ⏱ Report generated in {elapsed:.1f}s', '='*70]

    report_text = '\n'.join(L)
    rpt_f  = RPT_DIR / f"intelligence_report_{now.strftime('%Y-%m-%d')}.txt"
    json_f = RPT_DIR / f"intelligence_report_{now.strftime('%Y-%m-%d')}.json"
    rpt_f.write_text(report_text, encoding='utf-8')
    json_f.write_text(json.dumps({
        'generated_at': now.isoformat(), 'n_stocks': len(profs),
        'total_explosions': total_exps, 'n_laws': len(laws),
        'n_sectors': len(sectors), 'n_up_patterns': len(pats_up),
        'n_down_patterns': len(pats_dn), 'report_text': report_text
    }, indent=2, ensure_ascii=False))

    return {
        'report_file': str(rpt_f), 'json_file': str(json_f),
        'n_laws': len(laws), 'total_explosions': total_exps,
        'n_up_patterns': len(pats_up), 'n_down_patterns': len(pats_dn),
        'elapsed': round(elapsed,2),
        'report_preview': report_text[:3000]
    }

def cmd_full_discovery(con):
    t0 = time.time()
    results = {}
    for step, fn, label in [
        ('stock_profiles',      lambda: cmd_stock_profiles(con),      '1/5 Stock profiles'),
        ('explosion_scan',      lambda: cmd_explosion_scan(con),      '2/5 Explosion scan'),
        ('precursor_discovery', lambda: cmd_precursor_discovery(con), '3/5 Precursor discovery'),
        ('sector_cycles',       lambda: cmd_sector_cycles(con),       '4/5 Sector cycles'),
        ('knowledge_update',    lambda: cmd_knowledge_update(con),    '5/5 Knowledge + report'),
    ]:
        print(f'  [{label}]...', file=sys.stderr, flush=True)
        results[step] = fn()
    results['research_report'] = cmd_research_report(con)
    results['total_elapsed']   = round(time.time()-t0, 2)
    results['status']          = 'COMPLETE'
    return results

def cmd_status(con):
    def cnt(t):
        try: return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception: return 0
    latest_rpt = None
    if RPT_DIR.exists():
        rpts = sorted(RPT_DIR.glob('intelligence_report_*.txt'))
        if rpts: latest_rpt = rpts[-1].name
    latest_mem = con.execute("SELECT snapshot_date, dominant_archetype, avg_hurst, avg_vol, "
                             "explosion_rate FROM market_memory ORDER BY snapshot_date DESC LIMIT 1"
                             ).fetchone()
    return {
        'stock_profiles':     cnt('stock_profiles'),
        'explosive_moves':    cnt('explosive_moves'),
        'precursor_patterns': cnt('precursor_patterns'),
        'structural_laws':    cnt('structural_laws'),
        'sector_cycles':      cnt('sector_behavioral_cycles'),
        'market_memory':      cnt('market_memory'),
        'latest_report':      latest_rpt,
        'latest_memory':      dict(zip(['date','dominant','hurst','vol','exp_rate'], latest_mem)) if latest_mem else None,
    }

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': sorted(COMMANDS)}))
        return
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_tables(con)
    dispatch = {
        'stock_profiles':      lambda: cmd_stock_profiles(con),
        'explosion_scan':      lambda: cmd_explosion_scan(con),
        'precursor_discovery': lambda: cmd_precursor_discovery(con),
        'sector_cycles':       lambda: cmd_sector_cycles(con),
        'knowledge_update':    lambda: cmd_knowledge_update(con),
        'research_report':     lambda: cmd_research_report(con),
        'full_discovery':      lambda: cmd_full_discovery(con),
        'status':              lambda: cmd_status(con),
    }
    try:
        result = dispatch[cmd]()
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()[-1500:]}))
    finally:
        con.close()

if __name__ == '__main__':
    main()
