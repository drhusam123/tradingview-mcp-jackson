#!/usr/bin/env python3
"""
Market Energy Flow Engine — Phase 4
=====================================
Markets behave like behavioral thermodynamics systems.
Pressure is CREATED, STORED, TRANSFERRED, AMPLIFIED, ABSORBED, and RELEASED.

NOT indicators. NOT states. NOT forces.
ENERGY FLOW — how behavioral energy governs market dynamics.

Phase 1: Latent Market Behavior Engine   (latent_engine.py)
Phase 2: Force Field Engine              (force_field_engine.py)
Phase 3: Propagation Engine              (propagation_engine.py)
Phase 4: THIS — Behavioral Thermodynamics

Commands (stdin JSON: {"command": "...", "params": {...}}):
  energy_now            — current energy snapshot from indicators (~2s)
  energy_flow           — sector-to-sector energy transfer analysis (~15s)
  energy_accumulation   — buildup zones, coiled springs, reservoirs (~15s)
  energy_transformation — how energy converts between forms (~20s)
  energy_persistence    — half-life, decay curves, release probability (~15s)
  regime_energy         — energy dynamics per market regime (~20s)
  failure_physics       — why energy failed to release (~15s)
  energy_invariants     — universal energy laws & thresholds (~20s)
  energy_full           — complete thermodynamics report (~2min)
"""

import json, sys, time, sqlite3, math
from pathlib import Path
from collections import defaultdict

DB_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')

# ─── Energy Dimensions ────────────────────────────────────────────────────────

ENERGY_DIMS = [
    'MOMENTUM_ENERGY',
    'PANIC_ENERGY',
    'EXHAUSTION_ENERGY',
    'VOLATILITY_ENERGY',
    'LIQUIDITY_STRESS',
    'MEAN_REVERSION_PRESSURE',
    'INSTABILITY_ENERGY',
    'TREND_PERSISTENCE_ENERGY',
]

# Thresholds
HIGH_E   = 0.35
MEDIUM_E = 0.20
LOW_E    = 0.10

# Energy state labels (short form)
STATE_LABELS = {
    'MOMENTUM_ENERGY':          'HIGH_MOMENTUM',
    'PANIC_ENERGY':             'PANIC',
    'EXHAUSTION_ENERGY':        'EXHAUSTION',
    'VOLATILITY_ENERGY':        'COMPRESSED_VOLATILITY',
    'LIQUIDITY_STRESS':         'LIQUIDITY_CRISIS',
    'MEAN_REVERSION_PRESSURE':  'REVERSAL_PRESSURE',
    'INSTABILITY_ENERGY':       'INSTABILITY',
    'TREND_PERSISTENCE_ENERGY': 'TRENDING',
}

# ─── Math Utilities ───────────────────────────────────────────────────────────

def _mean(xs):
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    return sum(xs) / len(xs) if xs else 0.0

def _std(xs):
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    if len(xs) < 2: return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))

def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 5: return 0.0
    x, y = list(x[:n]), list(y[:n])
    mx, my = sum(x) / n, sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx  = math.sqrt(sum((v - mx) ** 2 for v in x))
    dy  = math.sqrt(sum((v - my) ** 2 for v in y))
    return round(num / (dx * dy), 4) if dx * dy > 1e-12 else 0.0

def _lag_corr(a, b, max_lag=5):
    """Cross-correlation: positive peak_lag → a leads b."""
    best_lag, best_r = 0, -999
    n = min(len(a), len(b))
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            ai, bi = a[:n-lag] if lag else a[:n], b[lag:n] if lag else b[:n]
        else:
            ai, bi = a[-lag:n], b[:n+lag]
        r = _pearson(ai, bi)
        if r > best_r:
            best_r, best_lag = r, lag
    return best_lag, round(best_r, 4)

def _compute_rsi(closes, period=14):
    rsi = [None] * len(closes)
    if len(closes) < period + 1: return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i-1]
            avg_g = (avg_g * (period - 1) + max(d, 0)) / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        rsi[i] = round(100 - 100 / (1 + avg_g / avg_l), 2) if avg_l > 1e-10 else 100.0
    return rsi

def _compute_atr(highs, lows, closes, period=14):
    atr = [None] * len(closes)
    if len(closes) < period + 1: return atr
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i]  - closes[i-1]))
        trs.append(tr)
    avg = sum(trs[:period]) / period
    atr[period - 1] = avg
    for i in range(period, len(closes)):
        avg = (avg * (period - 1) + trs[i]) / period
        atr[i] = avg
    return atr

# ─── Core Energy Computation ──────────────────────────────────────────────────

def _compute_energy_series(bars):
    """
    Compute 8-dimensional energy vector per bar from OHLCV.
    Requires ≥ 30 bars. Returns list of {time, MOMENTUM_ENERGY, ...}
    """
    if len(bars) < 30:
        return []

    closes = [b['close'] for b in bars]
    highs  = [b['high']  for b in bars]
    lows   = [b['low']   for b in bars]
    vols   = [b['volume'] for b in bars]

    rsi_vals = _compute_rsi(closes, 14)
    atr_vals = _compute_atr(highs, lows, closes, 14)

    result = []
    for i in range(25, len(bars)):
        c   = closes[i]
        c1  = closes[i-1] or c
        c5  = closes[i-5]  or c
        c10 = closes[i-10] or c
        c20 = closes[i-20] or c

        daily_ret = (c - c1) / c1 if c1 else 0
        mom5      = (c - c5)  / c5  if c5  else 0
        mom10     = (c - c10) / c10 if c10 else 0

        # Volume ratio vs 20-bar mean
        avg_vol = _mean(vols[max(0, i-20):i]) or 1.0
        vol_ratio = vols[i] / avg_vol if avg_vol > 0 else 1.0

        # ATR compression: ratio of historical ATR to current ATR
        atr_i    = atr_vals[i] or 0.001
        hist_atr = _mean([v for v in atr_vals[max(0, i-50):i] if v]) or atr_i
        compression = hist_atr / atr_i  # > 1 = compressed (stored energy)

        # RSI
        rsi_i = rsi_vals[i] if rsi_vals[i] is not None else 50.0

        # Mean20 for reversion pressure
        mean20 = _mean(closes[i-20:i]) or c

        # Direction consistency (last 5 bars)
        rets = [(closes[j] - closes[j-1]) / closes[j-1]
                for j in range(i-4, i+1) if closes[j-1]]
        n_up   = sum(1 for r in rets if r >  0.001)
        n_down = sum(1 for r in rets if r < -0.001)
        dir_consistency = max(n_up, n_down) / len(rets) if rets else 0.0

        # ── 1. MOMENTUM_ENERGY: strength × persistence ──
        mom_strength    = min(1.0, abs(mom5) / 0.05)
        mom_persistence = min(1.0, (abs(mom10) / 0.10 + dir_consistency) / 2.0)
        momentum_energy = round(mom_strength * mom_persistence, 4)

        # ── 2. PANIC_ENERGY: sharp drop × volume spike × rsi fear ──
        panic_drop = max(0.0, -daily_ret / 0.04)      # -4% daily = max
        panic_vol  = min(2.0, vol_ratio) / 2.0
        panic_rsi  = max(0.0, (25 - rsi_i) / 25) if rsi_i < 25 else 0.0
        panic_energy = round(min(1.0, panic_drop * panic_vol * 0.7 + panic_rsi * 0.3), 4)

        # ── 3. EXHAUSTION_ENERGY: overextension at either extreme ──
        if rsi_i >= 70:
            exhaustion_energy = min(1.0, (rsi_i - 70) / 30 + abs(mom10) / 0.15)
        elif rsi_i <= 30:
            exhaustion_energy = min(1.0, (30 - rsi_i) / 30 + abs(mom10) / 0.15)
        else:
            exhaustion_energy = 0.0
        exhaustion_energy = round(exhaustion_energy, 4)

        # ── 4. VOLATILITY_ENERGY: compression = stored potential ──
        vol_energy = round(min(1.0, max(0.0, (compression - 1.0) / 2.0)), 4)

        # ── 5. LIQUIDITY_STRESS: volume drought ──
        liq_stress = round(min(1.0, max(0.0, (1.0 - vol_ratio) / 0.8)), 4)

        # ── 6. MEAN_REVERSION_PRESSURE: rubber-band tension ──
        rev_pressure = abs(c - mean20) / (mean20 * 0.08) if mean20 > 0 else 0.0
        rev_pressure = round(min(1.0, rev_pressure), 4)

        # ── 7. INSTABILITY_ENERGY: weighted composite of destabilizing forces ──
        instability = round(min(1.0,
            panic_energy      * 0.35 +
            exhaustion_energy * 0.25 +
            vol_energy        * 0.20 +
            liq_stress        * 0.10 +
            rev_pressure      * 0.10
        ), 4)

        # ── 8. TREND_PERSISTENCE_ENERGY: inertia (consistent direction) ──
        trend_persistence = round(min(1.0,
            dir_consistency * min(1.0, abs(mom5) / 0.03 + 0.2)
        ), 4)

        result.append({
            'time': bars[i]['time'],
            'MOMENTUM_ENERGY':          momentum_energy,
            'PANIC_ENERGY':             panic_energy,
            'EXHAUSTION_ENERGY':        exhaustion_energy,
            'VOLATILITY_ENERGY':        vol_energy,
            'LIQUIDITY_STRESS':         liq_stress,
            'MEAN_REVERSION_PRESSURE':  rev_pressure,
            'INSTABILITY_ENERGY':       instability,
            'TREND_PERSISTENCE_ENERGY': trend_persistence,
        })
    return result

def _classify_energy_state(ev):
    """Dominant energy form label from an energy vector dict."""
    if not ev:
        return 'NEUTRAL'
    vals = {d: ev.get(d, 0) for d in ENERGY_DIMS}
    mx = max(vals.values())
    if mx < LOW_E:
        return 'NEUTRAL'
    return STATE_LABELS.get(max(vals, key=vals.get), 'NEUTRAL')

def _total_energy(ev):
    """Scalar total energy (mean of all 8 dims)."""
    return _mean([ev.get(d, 0) for d in ENERGY_DIMS])

# ─── DB Loaders ───────────────────────────────────────────────────────────────

def _load_ohlcv_all(min_bars=50, max_bars=None):
    """
    Load OHLCV. If max_bars is set, keep only the most recent N bars per symbol.
    This dramatically speeds up heavy analysis commands.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT symbol, bar_time, open, high, low, close, volume "
            "FROM ohlcv_history ORDER BY symbol, bar_time"
        ).fetchall()
    finally:
        con.close()
    data = defaultdict(list)
    for r in rows:
        data[r[0]].append({
            'time': r[1], 'open': r[2], 'high': r[3],
            'low': r[4],  'close': r[5], 'volume': r[6],
        })
    result = {}
    for s, bars in data.items():
        if len(bars) < min_bars:
            continue
        if max_bars and len(bars) > max_bars:
            bars = bars[-max_bars:]
        result[s] = bars
    return result

def _load_sector_map():
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("SELECT symbol, sector FROM stock_universe").fetchall()
        return {r[0]: (r[1] or 'Unknown') for r in rows}
    finally:
        con.close()

def _load_indicators_now():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT ic.symbol, ic.rsi14, ic.vol_ratio_20, ic.momentum_5d, "
            "ic.momentum_10d, ic.momentum_20d, ic.adx14, ic.bb_width, "
            "ic.atr14, ic.close_position, ic.above_ema20, ic.above_ema50, "
            "ic.macd_hist, ic.stoch_k, ic.williams_r, ic.cci20, "
            "COALESCE(su.sector, 'Unknown') AS sector "
            "FROM indicators_cache ic "
            "LEFT JOIN stock_universe su ON ic.symbol = su.symbol "
            "INNER JOIN ("
            "  SELECT symbol, MAX(bar_date) AS max_date "
            "  FROM indicators_cache GROUP BY symbol"
            ") latest ON ic.symbol = latest.symbol AND ic.bar_date = latest.max_date "
            "WHERE ic.rsi14 IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()

# ─── Shared Build ─────────────────────────────────────────────────────────────

def _build_energy_history(ohlcv_data, sector_map):
    """
    Compute energy series for all stocks.
    Returns:
        stock_energy  — {symbol: [{time, dim...}]}
        dates         — sorted unique timestamps
        sectors       — {sector: [symbols]}  (≥3 stocks)
    """
    stock_energy = {}
    all_dates = set()
    for sym, bars in ohlcv_data.items():
        series = _compute_energy_series(bars)
        if series:
            stock_energy[sym] = series
            all_dates.update(e['time'] for e in series)
    dates = sorted(all_dates)
    sectors = defaultdict(list)
    for sym in stock_energy:
        sectors[sector_map.get(sym, 'Unknown')].append(sym)
    sectors = {s: syms for s, syms in sectors.items() if len(syms) >= 3}
    return stock_energy, dates, sectors

def _sector_energy_series(stock_energy, sectors, dates):
    """
    Aggregate stock→sector energy per date.
    Returns {sector: {date: {dim: mean_val}}}
    """
    result = {}
    date_set = set(dates)
    for sector, syms in sectors.items():
        by_date = defaultdict(lambda: defaultdict(list))
        for sym in syms:
            for ev in stock_energy.get(sym, []):
                if ev['time'] in date_set:
                    for d in ENERGY_DIMS:
                        by_date[ev['time']][d].append(ev[d])
        result[sector] = {
            t: {d: round(_mean(vals), 4) for d, vals in dims.items()}
            for t, dims in by_date.items()
        }
    return result

def _market_energy_series(stock_energy, dates):
    """Market-level energy per date (min 10 stocks)."""
    by_date = defaultdict(lambda: defaultdict(list))
    for sym, series in stock_energy.items():
        for ev in series:
            for d in ENERGY_DIMS:
                by_date[ev['time']][d].append(ev[d])
    result = {}
    for t, dims in by_date.items():
        n = _mean([len(v) for v in dims.values()])
        if n >= 10:
            result[t] = {d: round(_mean(vals), 4) for d, vals in dims.items()}
    return result

def _classify_regime(ev):
    """CRISIS/STRESS/MODERATE/CALM from energy vector."""
    if not ev:
        return 'MODERATE'
    instab = ev.get('INSTABILITY_ENERGY', 0)
    panic  = ev.get('PANIC_ENERGY', 0)
    trend  = ev.get('TREND_PERSISTENCE_ENERGY', 0)
    score  = panic * 0.5 + instab * 0.5
    if score > 0.45:  return 'CRISIS'
    if score > 0.28:  return 'STRESS'
    if trend > 0.35:  return 'BULL'
    if score > 0.14:  return 'MODERATE'
    return 'CALM'

# ─── Command: energy_now ──────────────────────────────────────────────────────

def cmd_energy_now(params):
    """Current energy state from indicators_cache — fast path ~2s."""
    t0 = time.time()
    stocks = _load_indicators_now()
    if not stocks:
        return {'error': 'no indicator data available'}

    all_evs = []
    sector_data = defaultdict(list)

    for s in stocks:
        rsi      = s.get('rsi14', 50) or 50.0
        mom5     = (s.get('momentum_5d',  0) or 0) / 100.0
        mom10    = (s.get('momentum_10d', 0) or 0) / 100.0
        mom20    = (s.get('momentum_20d', 0) or 0) / 100.0
        vol_r    = s.get('vol_ratio_20', 1.0) or 1.0
        adx      = s.get('adx14', 25) or 25.0
        bb_w     = s.get('bb_width', 0.05) or 0.05
        macd_h   = s.get('macd_hist', 0) or 0.0
        stoch    = s.get('stoch_k', 50) or 50.0
        cci      = s.get('cci20', 0) or 0.0
        will_r   = s.get('williams_r', -50) or -50.0

        # ── MOMENTUM_ENERGY ──
        mom_strength    = min(1.0, abs(mom5) / 0.05)
        mom_persistence = min(1.0, abs(mom10) / 0.10 + 0.2)
        macd_contrib    = min(0.3, abs(macd_h) / 2.0)
        momentum_energy = round(min(1.0, mom_strength * mom_persistence + macd_contrib), 4)

        # ── PANIC_ENERGY ──
        panic_rsi = max(0.0, (25.0 - rsi) / 25.0) if rsi < 25 else 0.0
        panic_mom = max(0.0, -mom5 / 0.05) if mom5 < 0 else 0.0
        panic_cci = max(0.0, (-cci - 100) / 100.0) if cci < -100 else 0.0
        panic_energy = round(min(1.0, panic_rsi * 0.4 + panic_mom * 0.4 + panic_cci * 0.2), 4)

        # ── EXHAUSTION_ENERGY ──
        if rsi >= 70:
            exhaustion_rsi = min(1.0, (rsi - 70) / 30.0)
            exhaustion_stoch = min(0.4, (stoch - 80) / 20.0) if stoch > 80 else 0.0
        elif rsi <= 30:
            exhaustion_rsi = min(1.0, (30.0 - rsi) / 30.0)
            exhaustion_stoch = min(0.4, (20 - stoch) / 20.0) if stoch < 20 else 0.0
        else:
            exhaustion_rsi   = 0.0
            exhaustion_stoch = 0.0
        exhaustion_energy = round(min(1.0, exhaustion_rsi + exhaustion_stoch + abs(mom10) / 0.15 * 0.3), 4)

        # ── VOLATILITY_ENERGY (BB compression) ──
        # Normal BB width ~0.06; lower = more compressed = more stored energy
        typical_bb = 0.06
        compression = typical_bb / bb_w if bb_w > 0 else 1.0
        vol_energy = round(min(1.0, max(0.0, (compression - 0.8) / 1.5)), 4)

        # ── LIQUIDITY_STRESS ──
        liq_stress = round(min(1.0, max(0.0, (1.0 - vol_r) / 0.8)), 4)

        # ── MEAN_REVERSION_PRESSURE ──
        will_r_norm = abs(will_r + 50) / 50.0         # -100..0 → 0..1 (extremes = high)
        rev_pressure = round(min(1.0, abs(rsi - 50) / 30.0 * 0.6 + will_r_norm * 0.4), 4)

        # ── INSTABILITY_ENERGY ──
        instability = round(min(1.0,
            panic_energy      * 0.35 +
            exhaustion_energy * 0.25 +
            vol_energy        * 0.20 +
            liq_stress        * 0.10 +
            rev_pressure      * 0.10
        ), 4)

        # ── TREND_PERSISTENCE_ENERGY ──
        adx_norm   = min(1.0, adx / 50.0)
        trend_mom  = min(1.0, abs(mom5) / 0.03 + 0.2)
        trend_persistence = round(min(1.0, adx_norm * trend_mom), 4)

        ev = {
            'MOMENTUM_ENERGY':          momentum_energy,
            'PANIC_ENERGY':             panic_energy,
            'EXHAUSTION_ENERGY':        exhaustion_energy,
            'VOLATILITY_ENERGY':        vol_energy,
            'LIQUIDITY_STRESS':         liq_stress,
            'MEAN_REVERSION_PRESSURE':  rev_pressure,
            'INSTABILITY_ENERGY':       instability,
            'TREND_PERSISTENCE_ENERGY': trend_persistence,
        }
        ev['symbol']       = s['symbol']
        ev['sector']       = s['sector']
        ev['energy_state'] = _classify_energy_state(ev)
        ev['total_energy'] = round(_total_energy(ev), 4)

        all_evs.append(ev)
        sector_data[s['sector']].append(ev)

    # Market energy
    market_ev = {d: round(_mean([e[d] for e in all_evs]), 4) for d in ENERGY_DIMS}
    market_state   = _classify_energy_state(market_ev)
    total_sys      = round(_total_energy(market_ev), 4)
    dominant_dim   = max(market_ev, key=market_ev.get)

    # Sector profiles
    sector_profiles = {}
    for sector, evs in sector_data.items():
        sec_ev = {d: round(_mean([e[d] for e in evs]), 4) for d in ENERGY_DIMS}
        top3   = sorted(evs, key=lambda e: e['INSTABILITY_ENERGY'] + e['MOMENTUM_ENERGY'],
                        reverse=True)[:3]
        sector_profiles[sector] = {
            'n_stocks':     len(evs),
            'energy_state': _classify_energy_state(sec_ev),
            'total_energy': round(_total_energy(sec_ev), 4),
            'dominant':     STATE_LABELS.get(max(sec_ev, key=sec_ev.get), 'NEUTRAL'),
            **sec_ev,
            'hot_stocks': [{'symbol': e['symbol'], 'state': e['energy_state'],
                            'total': e['total_energy']} for e in top3],
        }

    # Rankings
    by_instab  = sorted(sector_profiles.items(), key=lambda x: x[1]['INSTABILITY_ENERGY'],  reverse=True)
    by_comp    = sorted(sector_profiles.items(), key=lambda x: x[1]['VOLATILITY_ENERGY'],   reverse=True)
    by_trend   = sorted(sector_profiles.items(), key=lambda x: x[1]['TREND_PERSISTENCE_ENERGY'], reverse=True)
    by_exhaust = sorted(sector_profiles.items(), key=lambda x: x[1]['EXHAUSTION_ENERGY'],   reverse=True)

    return {
        'elapsed_sec':         round(time.time() - t0, 2),
        'n_stocks':            len(all_evs),
        'n_sectors':           len(sector_profiles),
        'market_energy':       market_ev,
        'market_state':        market_state,
        'total_system_energy': total_sys,
        'dominant_energy':     dominant_dim,
        'sector_profiles':     sector_profiles,
        'hottest_sectors':     [k for k, _ in by_instab[:5]],
        'compressed_sectors':  [k for k, _ in by_comp[:5]],
        'trending_sectors':    [k for k, _ in by_trend[:5]],
        'exhausted_sectors':   [k for k, _ in by_exhaust[:5]],
    }

# ─── Command: energy_flow ─────────────────────────────────────────────────────

def cmd_energy_flow(params):
    """Sector-to-sector energy transfer: who emits, who receives, bottlenecks."""
    t0 = time.time()
    ohlcv  = _load_ohlcv_all()
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)
    if not sectors:
        return {'error': 'insufficient historical data'}

    sec_series = _sector_energy_series(se, sectors, dates)
    mkt_series = _market_energy_series(se, dates)

    # Build total-energy time series per sector
    sorted_dates = sorted(set(dates))
    def _total_ts(sector):
        sd = sec_series.get(sector, {})
        return [_total_energy(sd.get(t, {})) for t in sorted_dates if t in sd]

    # Market total series
    mkt_ts = {t: _total_energy(mkt_series[t]) for t in sorted_dates if t in mkt_series}

    # ── Flow matrix: cross-corr between sector total-energy series ──
    flow_matrix = {}  # {s1: {s2: {lag, corr}}}
    sec_list = list(sectors.keys())

    for s1 in sec_list:
        ts1 = _total_ts(s1)
        flow_matrix[s1] = {}
        for s2 in sec_list:
            if s1 == s2: continue
            ts2 = _total_ts(s2)
            lag, corr = _lag_corr(ts1, ts2, max_lag=5)
            flow_matrix[s1][s2] = {'lag': lag, 'corr': round(corr, 3)}

    # ── Net flow: how much does each sector emit vs receive ──
    # A leads B (positive lag in flow_matrix[A][B]) = A is emitting to B
    net_flow = {s: {'outflow': 0, 'inflow': 0, 'pairs': 0} for s in sec_list}
    top_flow_pairs = []

    for s1 in sec_list:
        for s2 in sec_list:
            if s1 == s2: continue
            pair = flow_matrix[s1].get(s2, {})
            lag  = pair.get('lag', 0)
            corr = pair.get('corr', 0)
            if abs(corr) < 0.20: continue
            if lag > 0:   # s1 leads s2
                net_flow[s1]['outflow'] += corr
                net_flow[s2]['inflow']  += corr
                net_flow[s1]['pairs']   += 1
                top_flow_pairs.append({
                    's1': s1, 's2': s2,
                    'lag': lag, 'corr': corr,
                    'direction': f'{s1} ──▶ {s2}',
                })

    # Sort by correlation strength
    top_flow_pairs.sort(key=lambda x: -x['corr'])

    # ── Per-dimension flow leader ──
    dim_leaders = {}
    for d in ENERGY_DIMS:
        def _dim_ts(sector, dim):
            sd = sec_series.get(sector, {})
            return [sd[t].get(dim, 0) for t in sorted_dates if t in sd]
        best_leader, best_corr, best_lag = None, -1, 0
        for s1 in sec_list:
            ts1 = _dim_ts(s1, d)
            for s2 in sec_list:
                if s1 == s2: continue
                ts2  = _dim_ts(s2, d)
                lag, corr = _lag_corr(ts1, ts2, max_lag=3)
                if lag > 0 and corr > best_corr:
                    best_corr, best_lag, best_leader = corr, lag, (s1, s2)
        if best_leader:
            dim_leaders[STATE_LABELS.get(d, d)] = {
                'source': best_leader[0],
                'target': best_leader[1],
                'lag_bars': best_lag,
                'corr': best_corr,
            }

    # ── Sector roles from net flow ──
    sector_roles = {}
    for s, nf in net_flow.items():
        out = nf['outflow']
        inp = nf['inflow']
        net = out - inp
        if   net >  0.5:   role = 'ENERGY_SOURCE'
        elif net < -0.5:   role = 'ENERGY_SINK'
        elif out > 1.5:    role = 'ENERGY_AMPLIFIER'
        elif inp > 1.5:    role = 'ENERGY_STORAGE'
        elif nf['pairs'] >= 3 and abs(net) < 0.3: role = 'ENERGY_DISSIPATOR'
        else:               role = 'ENERGY_CONVERTER'
        sector_roles[s] = {
            'role':    role,
            'outflow': round(out, 3),
            'inflow':  round(inp, 3),
            'net':     round(net, 3),
        }

    # ── Bottlenecks: high inflow but low outflow ──
    bottlenecks = sorted(
        [(s, d) for s, d in sector_roles.items() if d['inflow'] > 0.3 and d['outflow'] < d['inflow'] * 0.5],
        key=lambda x: -x[1]['inflow']
    )

    return {
        'elapsed_sec':      round(time.time() - t0, 2),
        'n_sectors':        len(sec_list),
        'n_dates':          len(sorted_dates),
        'sector_roles':     sector_roles,
        'top_flow_pairs':   top_flow_pairs[:12],
        'dim_leaders':      dim_leaders,
        'bottlenecks':      [{'sector': s, **d} for s, d in bottlenecks[:5]],
        'sources':          sorted([k for k,v in sector_roles.items() if v['role']=='ENERGY_SOURCE']),
        'sinks':            sorted([k for k,v in sector_roles.items() if v['role']=='ENERGY_SINK']),
        'amplifiers':       sorted([k for k,v in sector_roles.items() if v['role']=='ENERGY_AMPLIFIER']),
    }

# ─── Command: energy_accumulation ────────────────────────────────────────────

def cmd_energy_accumulation(params):
    """Detect where energy is building up: coiled springs, reservoirs, overextension."""
    t0 = time.time()
    ohlcv  = _load_ohlcv_all()
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    sec_series = _sector_energy_series(se, sectors, dates)
    sorted_dates = sorted(dates)

    accumulation = {}
    for sector, syms in sectors.items():
        sd = sec_series.get(sector, {})
        recent_dates = sorted_dates[-20:]   # last 20 bars
        old_dates    = sorted_dates[-50:-20] # prior 30 bars

        recent_evs = [sd[t] for t in recent_dates if t in sd]
        old_evs    = [sd[t] for t in old_dates    if t in sd]

        if not recent_evs:
            continue

        recent_avg = {d: _mean([e.get(d, 0) for e in recent_evs]) for d in ENERGY_DIMS}
        old_avg    = {d: _mean([e.get(d, 0) for e in old_evs])    for d in ENERGY_DIMS} if old_evs else {}

        # Buildup velocity: Δenergy per dimension over last 20 vs prior 30
        velocity = {}
        for d in ENERGY_DIMS:
            delta = recent_avg[d] - old_avg.get(d, recent_avg[d])
            velocity[d] = round(delta, 4)

        # Total buildup score
        buildup_score = round(sum(max(0, v) for v in velocity.values()), 4)

        # Compression ratio (VOLATILITY_ENERGY buildup)
        compression_buildup = velocity.get('VOLATILITY_ENERGY', 0)

        # Is it a coiled spring? (vol_energy building + panic_energy stable)
        is_coiled = (
            recent_avg.get('VOLATILITY_ENERGY', 0) > MEDIUM_E and
            compression_buildup > 0 and
            recent_avg.get('PANIC_ENERGY', 0) < MEDIUM_E
        )

        # Overextension check (exhaustion building, momentum high)
        is_overextended = (
            recent_avg.get('EXHAUSTION_ENERGY', 0) > HIGH_E and
            recent_avg.get('MOMENTUM_ENERGY', 0) > MEDIUM_E
        )

        # Instability reservoir (instability building without release)
        is_reservoir = (
            recent_avg.get('INSTABILITY_ENERGY', 0) > HIGH_E and
            buildup_score > 0.3
        )

        # Last 5-bar trend in total energy
        last5_totals = [_total_energy(sd[t]) for t in sorted_dates[-5:] if t in sd]
        acceleration = (last5_totals[-1] - last5_totals[0]) if len(last5_totals) >= 2 else 0

        accumulation[sector] = {
            'current_energy':  {d: round(v, 4) for d, v in recent_avg.items()},
            'buildup_velocity':velocity,
            'buildup_score':   buildup_score,
            'is_coiled_spring':is_coiled,
            'is_overextended': is_overextended,
            'is_reservoir':    is_reservoir,
            'acceleration_5bar': round(acceleration, 4),
            'dominant_buildup': max(velocity, key=lambda d: max(velocity.get(d, 0), 0)),
        }

    # Rankings
    coiled_springs  = sorted([(s, d) for s, d in accumulation.items() if d['is_coiled_spring']],
                              key=lambda x: -x[1]['current_energy'].get('VOLATILITY_ENERGY', 0))
    reservoirs      = sorted([(s, d) for s, d in accumulation.items() if d['is_reservoir']],
                              key=lambda x: -x[1]['buildup_score'])
    overextended    = sorted([(s, d) for s, d in accumulation.items() if d['is_overextended']],
                              key=lambda x: -x[1]['current_energy'].get('EXHAUSTION_ENERGY', 0))
    fastest_buildup = sorted(accumulation.items(), key=lambda x: -x[1]['buildup_score'])

    # Market-level accumulation
    mkt_series = _market_energy_series(se, dates)
    recent_mkt  = [mkt_series[t] for t in sorted_dates[-20:] if t in mkt_series]
    old_mkt     = [mkt_series[t] for t in sorted_dates[-50:-20] if t in mkt_series]
    market_velocity = {}
    if recent_mkt:
        r_avg = {d: _mean([e.get(d,0) for e in recent_mkt]) for d in ENERGY_DIMS}
        o_avg = {d: _mean([e.get(d,0) for e in old_mkt])    for d in ENERGY_DIMS} if old_mkt else {}
        market_velocity = {d: round(r_avg[d] - o_avg.get(d, r_avg[d]), 4) for d in ENERGY_DIMS}

    return {
        'elapsed_sec':      round(time.time() - t0, 2),
        'n_sectors':        len(accumulation),
        'n_dates':          len(sorted_dates),
        'market_velocity':  market_velocity,
        'sector_accumulation': accumulation,
        'coiled_springs':   [{'sector': s, 'vol_energy': d['current_energy'].get('VOLATILITY_ENERGY'),
                               'buildup': d['buildup_score']} for s, d in coiled_springs],
        'instability_reservoirs': [{'sector': s, 'instability': d['current_energy'].get('INSTABILITY_ENERGY'),
                                    'buildup_score': d['buildup_score']} for s, d in reservoirs],
        'overextended':     [{'sector': s, 'exhaustion': d['current_energy'].get('EXHAUSTION_ENERGY'),
                               'momentum': d['current_energy'].get('MOMENTUM_ENERGY')} for s, d in overextended],
        'fastest_buildup':  [{'sector': s, 'score': d['buildup_score'],
                               'dim': d['dominant_buildup']} for s, d in fastest_buildup[:8]],
    }

# ─── Command: energy_transformation ──────────────────────────────────────────

def cmd_energy_transformation(params):
    """How energy converts between forms — Markov transition matrix. Last 200 bars/symbol."""
    t0 = time.time()
    ohlcv  = _load_ohlcv_all(max_bars=200)
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    # Build state sequence per stock
    transitions   = defaultdict(lambda: defaultdict(int))
    duration_log  = defaultdict(list)   # state → [run lengths]
    n_total_bars  = 0

    for sym, series in se.items():
        states = [_classify_energy_state(ev) for ev in series]
        n_total_bars += len(states)

        # Run-length encode
        if not states: continue
        cur, dur = states[0], 1
        for s in states[1:]:
            if s == cur:
                dur += 1
            else:
                duration_log[cur].append(dur)
                transitions[cur][s] += 1
                cur, dur = s, 1
        duration_log[cur].append(dur)

    # Build transition probability matrix
    transition_probs = {}
    for from_s, to_counts in transitions.items():
        total = sum(to_counts.values())
        avg_d = round(_mean(duration_log.get(from_s, [1])), 1)
        top5  = sorted(to_counts.items(), key=lambda x: -x[1])[:5]
        transition_probs[from_s] = {
            'total_events':    total,
            'avg_duration_bars': avg_d,
            'median_duration_bars': sorted(duration_log.get(from_s, [1]))[len(duration_log.get(from_s, [1]))//2],
            'next_states': {t: round(c / total, 3) for t, c in top5},
        }

    # ── Key conversion pathways ──
    pathway_defs = [
        ('HIGH_MOMENTUM',       'EXHAUSTION',          'Momentum burns out → Exhaustion'),
        ('EXHAUSTION',          'REVERSAL_PRESSURE',   'Exhaustion coils spring → Reversal'),
        ('REVERSAL_PRESSURE',   'HIGH_MOMENTUM',       'Reversal releases → New momentum'),
        ('PANIC',               'COMPRESSED_VOLATILITY','Panic compresses vol → Stored energy'),
        ('COMPRESSED_VOLATILITY','PANIC',              'Vol compression explodes → Panic'),
        ('INSTABILITY',         'PANIC',               'Instability tips into Panic'),
        ('PANIC',               'INSTABILITY',         'Panic feeds Instability loop'),
        ('HIGH_MOMENTUM',       'REVERSAL_PRESSURE',   'Overextended momentum → Reversal pressure'),
        ('TRENDING',            'HIGH_MOMENTUM',       'Trend confirms → Momentum surge'),
        ('LIQUIDITY_CRISIS',    'INSTABILITY',         'Liquidity drought → Instability'),
    ]

    pathways = []
    for (from_s, to_s, label) in pathway_defs:
        from_info = transition_probs.get(from_s, {})
        if not from_info: continue
        p = from_info.get('next_states', {}).get(to_s, 0)
        total = from_info.get('total_events', 0)
        n = round(p * total)
        pathways.append({
            'pathway':  label,
            'from':     from_s,
            'to':       to_s,
            'probability': p,
            'n_observed':  n,
            'avg_duration_before': from_info.get('avg_duration_bars', '—'),
        })
    pathways.sort(key=lambda x: -x['probability'])

    # ── Energy conversion cycles ──
    # Detect common 3-step cycles e.g. MOMENTUM → EXHAUSTION → REVERSAL → MOMENTUM
    cycle_counts = defaultdict(int)
    for sym, series in se.items():
        states = [_classify_energy_state(ev) for ev in series]
        for i in range(len(states) - 2):
            trip = (states[i], states[i+1], states[i+2])
            if trip[0] != trip[1] and trip[1] != trip[2]:
                cycle_counts[trip] += 1
    top_cycles = sorted(cycle_counts.items(), key=lambda x: -x[1])[:8]

    return {
        'elapsed_sec':          round(time.time() - t0, 2),
        'n_total_bars':         n_total_bars,
        'n_states':             len(transition_probs),
        'transition_matrix':    transition_probs,
        'key_pathways':         pathways[:10],
        'top_3step_cycles':     [{'cycle': ' → '.join(c), 'n': n} for c, n in top_cycles],
    }

# ─── Command: energy_persistence ─────────────────────────────────────────────

def cmd_energy_persistence(params):
    """Half-life and decay of each energy type — how long does energy survive? Last 200 bars/symbol."""
    t0 = time.time()
    ohlcv  = _load_ohlcv_all(max_bars=200)
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    dim_runs      = defaultdict(list)   # dim → [run lengths (bars above HIGH_E)]
    dim_release   = defaultdict(list)   # dim → [1 if released sharply, 0 if slow decay]
    dim_buildup   = defaultdict(list)   # dim → [bars until first HIGH_E from NEUTRAL]
    dim_intensity = defaultdict(list)   # dim → [peak value during high-energy episode]

    for sym, series in se.items():
        for d in ENERGY_DIMS:
            vals = [ev[d] for ev in series]
            # Find high-energy runs (above HIGH_E)
            in_run, run_len, peak_val = False, 0, 0.0
            for i, v in enumerate(vals):
                if v >= HIGH_E:
                    in_run    = True
                    run_len  += 1
                    peak_val  = max(peak_val, v)
                else:
                    if in_run and run_len >= 1:
                        dim_runs[d].append(run_len)
                        dim_intensity[d].append(round(peak_val, 4))
                        # Sharp release: drops from HIGH_E to < LOW_E in 1 bar
                        release = 1 if v < LOW_E else 0
                        dim_release[d].append(release)
                    in_run, run_len, peak_val = False, 0, 0.0
            if in_run and run_len >= 1:
                dim_runs[d].append(run_len)
                dim_intensity[d].append(round(peak_val, 4))

            # Build-up time: bars from LOW_E to first HIGH_E crossing
            crossing = False
            buildup_bars = 0
            for v in vals:
                if not crossing:
                    if v < LOW_E:
                        buildup_bars = 0
                        crossing = True
                else:
                    buildup_bars += 1
                    if v >= HIGH_E:
                        dim_buildup[d].append(buildup_bars)
                        crossing = False

    persistence = {}
    for d in ENERGY_DIMS:
        runs = dim_runs[d]
        if not runs:
            continue
        sorted_runs = sorted(runs)
        n = len(runs)
        # Half-life: median duration
        half_life   = sorted_runs[n // 2]
        avg_dur     = round(_mean(runs), 2)
        p_release   = round(_mean(dim_release[d]), 3)
        avg_buildup = round(_mean(dim_buildup.get(d, [0])), 1)
        avg_peak    = round(_mean(dim_intensity.get(d, [0])), 3)

        # Survival curve: P(still high at bar t | high at bar 1)
        max_bar = min(10, max(runs) if runs else 1)
        survival = {}
        for t in range(1, max_bar + 1):
            still_high = sum(1 for r in runs if r >= t)
            survival[str(t)] = round(still_high / n, 3)

        persistence[d] = {
            'energy_name':          STATE_LABELS.get(d, d),
            'n_episodes':           n,
            'half_life_bars':       half_life,
            'avg_duration_bars':    avg_dur,
            'p_sharp_release':      p_release,
            'avg_buildup_bars':     avg_buildup,
            'avg_peak_intensity':   avg_peak,
            'survival_curve':       survival,
        }

    # Rankings
    by_duration = sorted(persistence.items(), key=lambda x: -x[1]['avg_duration_bars'])
    by_intensity = sorted(persistence.items(), key=lambda x: -x[1]['avg_peak_intensity'])
    by_buildup   = sorted(persistence.items(), key=lambda x: x[1]['avg_buildup_bars'])

    return {
        'elapsed_sec':       round(time.time() - t0, 2),
        'persistence':       persistence,
        'most_persistent':   by_duration[0][0] if by_duration else None,
        'fastest_release':   min(persistence.items(), key=lambda x: x[1]['avg_duration_bars'])[0]
                             if persistence else None,
        'highest_intensity': by_intensity[0][0] if by_intensity else None,
        'fastest_buildup':   by_buildup[0][0] if by_buildup else None,
        'duration_ranking':  [{'energy': k, 'half_life': v['half_life_bars'],
                                'avg_bars': v['avg_duration_bars'],
                                'p_release': v['p_sharp_release'],
                                'episodes': v['n_episodes']}
                               for k, v in by_duration],
    }

# ─── Command: regime_energy ───────────────────────────────────────────────────

def cmd_regime_energy(params):
    """Energy dynamics per market regime: CRISIS/STRESS/BULL/MODERATE/CALM."""
    t0 = time.time()
    ohlcv  = _load_ohlcv_all()
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    mkt_series   = _market_energy_series(se, dates)
    sorted_dates = sorted(mkt_series.keys())

    # Classify each date into a regime
    date_regimes = {t: _classify_regime(mkt_series[t]) for t in sorted_dates}

    # Regime energy profiles
    regime_energy  = defaultdict(lambda: defaultdict(list))
    regime_dates   = defaultdict(list)

    for t, regime in date_regimes.items():
        ev = mkt_series[t]
        for d in ENERGY_DIMS:
            regime_energy[regime][d].append(ev.get(d, 0))
        regime_dates[regime].append(t)

    profiles = {}
    all_regimes = ['CRISIS', 'STRESS', 'MODERATE', 'BULL', 'CALM']
    for regime in all_regimes:
        if regime not in regime_energy:
            continue
        avg  = {d: round(_mean(vs), 4) for d, vs in regime_energy[regime].items()}
        n    = len(regime_dates[regime])
        profiles[regime] = {
            'n_dates':        n,
            'pct_history':    round(n / len(sorted_dates), 3),
            'avg_energy':     avg,
            'dominant':       STATE_LABELS.get(max(avg, key=avg.get), 'NEUTRAL'),
            'total_energy':   round(_total_energy(avg), 4),
        }

    # ── Regime transitions ──
    # Average energy in the N bars BEFORE a regime change
    transitions_before = defaultdict(lambda: defaultdict(list))
    for i in range(1, len(sorted_dates)):
        prev_t = sorted_dates[i-1]
        curr_t = sorted_dates[i]
        from_r = date_regimes.get(prev_t)
        to_r   = date_regimes.get(curr_t)
        if from_r and to_r and from_r != to_r:
            ev_prev = mkt_series.get(prev_t, {})
            key = f'{from_r}→{to_r}'
            for d in ENERGY_DIMS:
                transitions_before[key][d].append(ev_prev.get(d, 0))

    transition_signatures = {}
    for key, dim_vals in transitions_before.items():
        avg_sig = {d: round(_mean(vs), 4) for d, vs in dim_vals.items()}
        n = max(len(v) for v in dim_vals.values())
        if n >= 3:
            transition_signatures[key] = {
                'n_events':  n,
                'avg_energy_before': avg_sig,
                'warning_signal': max(avg_sig, key=avg_sig.get),
            }

    # ── Energy amplification per regime ──
    # Compare energy levels across regimes (CRISIS vs CALM)
    if 'CRISIS' in profiles and 'CALM' in profiles:
        amplification = {
            d: round(profiles['CRISIS']['avg_energy'][d] /
                     max(profiles['CALM']['avg_energy'][d], 0.01), 2)
            for d in ENERGY_DIMS
        }
        most_amplified = max(amplification, key=amplification.get)
    else:
        amplification    = {}
        most_amplified   = None

    return {
        'elapsed_sec':            round(time.time() - t0, 2),
        'n_dates':                len(sorted_dates),
        'regime_distribution':    {r: len(regime_dates[r]) for r in all_regimes if r in regime_dates},
        'profiles':               profiles,
        'transition_signatures':  transition_signatures,
        'crisis_vs_calm_amplification': amplification,
        'most_amplified_in_crisis': most_amplified,
    }

# ─── Command: failure_physics ─────────────────────────────────────────────────

def cmd_failure_physics(params):
    """
    Why did energy fail to release?
    Study suppressed volatility, absorbed panic, persistent momentum.
    Uses last 150 bars per symbol for speed.
    """
    t0 = time.time()
    ohlcv  = _load_ohlcv_all(max_bars=150)
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    # ── Failure taxonomy ──
    # Type 1: HIGH INSTABILITY that persisted > 5 bars without causing a panic event
    # Type 2: HIGH VOLATILITY_ENERGY (compression) that didn't expand (no panic_energy spike)
    # Type 3: HIGH EXHAUSTION that didn't lead to reversal
    # Type 4: HIGH PANIC that absorbed (market recovered without vol expansion)

    failures = {
        'absorbed_instability':   [],  # instability high but no cascade
        'suppressed_volatility':  [],  # compression stayed compressed
        'exhaustion_persistence': [],  # exhaustion didn't reverse
        'absorbed_panic':         [],  # panic dissolved without explosion
    }

    # Per-sector failure analysis
    sec_series = _sector_energy_series(se, sectors, dates)
    sorted_dates = sorted(dates)

    sector_failures = {}
    for sector, syms in sectors.items():
        sd = sec_series.get(sector, {})
        ts = [sd.get(t) for t in sorted_dates if t in sd]
        if len(ts) < 10: continue

        n_abs_instab = 0   # absorbed instability
        n_sup_vol    = 0   # suppressed volatility
        n_exh_persist= 0   # exhaustion persistence
        n_abs_panic  = 0   # absorbed panic

        for i in range(5, len(ts) - 5):
            ev = ts[i]
            if not ev: continue

            # Type 1: Instability above HIGH_E for 5 bars, no panic spike
            window = [ts[j] for j in range(i-4, i+1) if ts[j]]
            if len(window) >= 5:
                avg_instab = _mean([e.get('INSTABILITY_ENERGY', 0) for e in window])
                future5    = [ts[j] for j in range(i+1, min(i+6, len(ts))) if ts[j]]
                max_panic  = max([e.get('PANIC_ENERGY', 0) for e in future5], default=0)
                if avg_instab > HIGH_E and max_panic < MEDIUM_E:
                    n_abs_instab += 1
                    failures['absorbed_instability'].append({
                        'sector':   sector,
                        'avg_instability': round(avg_instab, 3),
                        'max_future_panic': round(max_panic, 3),
                    })

            # Type 2: Volatility compression persisted (no expansion in next 5)
            if ev.get('VOLATILITY_ENERGY', 0) > HIGH_E:
                future5 = [ts[j] for j in range(i+1, min(i+6, len(ts))) if ts[j]]
                next_vol = [e.get('VOLATILITY_ENERGY', 0) for e in future5]
                if future5 and max(next_vol) > ev.get('VOLATILITY_ENERGY', 0) * 0.9:
                    # vol stayed compressed
                    n_sup_vol += 1

            # Type 3: Exhaustion persisted > 3 bars (no reversal)
            if ev.get('EXHAUSTION_ENERGY', 0) > HIGH_E:
                ahead3 = [ts[j] for j in range(i+1, min(i+4, len(ts))) if ts[j]]
                still_ex = sum(1 for e in ahead3 if e.get('EXHAUSTION_ENERGY', 0) > MEDIUM_E)
                if still_ex >= 2:
                    n_exh_persist += 1

            # Type 4: Panic absorbed (panic high but total energy drops within 3 bars)
            if ev.get('PANIC_ENERGY', 0) > HIGH_E:
                ahead3 = [ts[j] for j in range(i+1, min(i+4, len(ts))) if ts[j]]
                future_panic = _mean([e.get('PANIC_ENERGY', 0) for e in ahead3])
                if future_panic < MEDIUM_E:
                    n_abs_panic += 1

        sector_failures[sector] = {
            'n_absorbed_instability':   n_abs_instab,
            'n_suppressed_volatility':  n_sup_vol,
            'n_exhaustion_persistence': n_exh_persist,
            'n_absorbed_panic':         n_abs_panic,
            'total_failures':           n_abs_instab + n_sup_vol + n_exh_persist + n_abs_panic,
        }

    # ── Structural dampeners ──
    # Sectors with highest failure rate are structural dampeners
    dampeners = sorted(sector_failures.items(), key=lambda x: -x[1]['total_failures'])

    # ── Market-level failure stats ──
    failures_summary = {}
    for key in ['absorbed_instability', 'suppressed_volatility', 'exhaustion_persistence', 'absorbed_panic']:
        failures_summary[key] = sum(d.get(f'n_{key}', 0) for d in sector_failures.values())

    # Top failure sectors per type
    top_suppressors = sorted(sector_failures.items(),
                              key=lambda x: -x[1]['n_suppressed_volatility'])[:5]
    top_absorbers   = sorted(sector_failures.items(),
                              key=lambda x: -x[1]['n_absorbed_instability'])[:5]

    return {
        'elapsed_sec':      round(time.time() - t0, 2),
        'n_sectors':        len(sector_failures),
        'failure_summary':  failures_summary,
        'sector_failures':  sector_failures,
        'structural_dampeners': [{'sector': s, 'total_failures': d['total_failures'],
                                   'types': d} for s, d in dampeners[:8]],
        'top_vol_suppressors': [s for s, _ in top_suppressors if _['n_suppressed_volatility'] > 0],
        'top_panic_absorbers': [s for s, _ in top_absorbers  if _['n_absorbed_instability'] > 0],
        'insight': f"أبرز كاسر: {dampeners[0][0] if dampeners else '—'} | "
                   f"انهيار مكبوت: {failures_summary.get('suppressed_volatility',0)} حدثة | "
                   f"ذعر ممتص: {failures_summary.get('absorbed_panic',0)} حدثة",
    }

# ─── Command: energy_invariants ───────────────────────────────────────────────

def cmd_energy_invariants(params):
    """
    Universal energy laws: recurring thresholds, persistent patterns,
    structural constraints that always hold.
    Uses last 150 bars per symbol for speed.
    """
    t0 = time.time()
    ohlcv  = _load_ohlcv_all(max_bars=150)
    smap   = _load_sector_map()
    se, dates, sectors = _build_energy_history(ohlcv, smap)

    # Hypotheses to test
    # Each invariant: (trigger_condition, consequence_within_N_bars, label)
    invariants_tested = []

    for sym, series in se.items():
        if len(series) < 20:
            continue
        for i in range(5, len(series) - 10):
            ev  = series[i]
            ahead5  = series[i+1:i+6]
            ahead10 = series[i+1:i+11]
            prev5   = series[max(0,i-5):i]

            # ── Invariant 1: EXHAUSTION > 0.5 for 3+ bars → reversal within 5 ──
            prev_ex = [e.get('EXHAUSTION_ENERGY', 0) for e in prev5[-3:]]
            if len(prev_ex) >= 3 and min(prev_ex) > 0.50:
                # Check for reversal signal
                rev_ahead = max([e.get('MEAN_REVERSION_PRESSURE', 0) for e in ahead5], default=0)
                _record_test(invariants_tested, 'EX>0.5 3bars → Reversal<5bars',
                             1 if rev_ahead > MEDIUM_E else 0)

            # ── Invariant 2: PANIC > 0.6 → VOLATILITY_ENERGY spike within 3 bars ──
            if ev.get('PANIC_ENERGY', 0) > 0.60:
                vol_ahead = max([e.get('VOLATILITY_ENERGY', 0) for e in ahead5[:3]], default=0)
                _record_test(invariants_tested, 'PANIC>0.6 → VOL_ENERGY spike<3bars',
                             1 if vol_ahead > ev.get('VOLATILITY_ENERGY', 0) * 1.3 else 0)

            # ── Invariant 3: COMPRESSED_VOL > 0.4 → release within 8 bars ──
            if ev.get('VOLATILITY_ENERGY', 0) > 0.40:
                panic_ahead = max([e.get('PANIC_ENERGY', 0) for e in ahead10[:8]], default=0)
                mom_ahead   = max([e.get('MOMENTUM_ENERGY', 0) for e in ahead10[:8]], default=0)
                _record_test(invariants_tested, 'COMPRESSED_VOL>0.4 → release<8bars',
                             1 if max(panic_ahead, mom_ahead) > HIGH_E else 0)

            # ── Invariant 4: MOMENTUM > 0.6 for 5 bars → EXHAUSTION within 5 bars ──
            prev_mom = [e.get('MOMENTUM_ENERGY', 0) for e in prev5]
            if len(prev_mom) >= 5 and min(prev_mom) > 0.60:
                ex_ahead = max([e.get('EXHAUSTION_ENERGY', 0) for e in ahead5], default=0)
                _record_test(invariants_tested, 'MOMENTUM>0.6 5bars → EXHAUSTION<5bars',
                             1 if ex_ahead > MEDIUM_E else 0)

            # ── Invariant 5: INSTABILITY > 0.5 + PANIC > 0.3 → CASCADE within 3 bars ──
            if (ev.get('INSTABILITY_ENERGY', 0) > 0.50 and
                ev.get('PANIC_ENERGY', 0) > 0.30):
                cascade = max([e.get('INSTABILITY_ENERGY', 0) for e in ahead5[:3]], default=0)
                _record_test(invariants_tested, 'INSTABILITY+PANIC combo → CASCADE<3bars',
                             1 if cascade > ev.get('INSTABILITY_ENERGY', 0) * 1.2 else 0)

            # ── Invariant 6: LIQUIDITY_STRESS > 0.5 → INSTABILITY within 5 bars ──
            if ev.get('LIQUIDITY_STRESS', 0) > 0.50:
                instab_ahead = max([e.get('INSTABILITY_ENERGY', 0) for e in ahead5], default=0)
                _record_test(invariants_tested, 'LIQUIDITY_DROUGHT>0.5 → INSTABILITY<5bars',
                             1 if instab_ahead > MEDIUM_E else 0)

            # ── Invariant 7: REVERSAL_PRESSURE > 0.6 + MOMENTUM low → Momentum swing ──
            if (ev.get('MEAN_REVERSION_PRESSURE', 0) > 0.60 and
                ev.get('MOMENTUM_ENERGY', 0) < MEDIUM_E):
                mom_ahead = max([e.get('MOMENTUM_ENERGY', 0) for e in ahead5], default=0)
                _record_test(invariants_tested, 'HIGH_REV_PRESSURE → MOM swing<5bars',
                             1 if mom_ahead > MEDIUM_E else 0)

    # Aggregate results
    inv_agg = defaultdict(lambda: {'n_triggered': 0, 'n_confirmed': 0})
    for label, confirmed in invariants_tested:
        inv_agg[label]['n_triggered'] += 1
        inv_agg[label]['n_confirmed'] += confirmed

    invariant_results = []
    for label, stats in inv_agg.items():
        n  = stats['n_triggered']
        nc = stats['n_confirmed']
        if n < 5: continue
        p = round(nc / n, 3)
        invariant_results.append({
            'invariant':      label,
            'p_confirmed':    p,
            'n_triggered':    n,
            'n_confirmed':    nc,
            'strength':       'STRONG' if p > 0.65 else 'MODERATE' if p > 0.45 else 'WEAK',
        })
    invariant_results.sort(key=lambda x: -x['p_confirmed'])

    # Strong invariants only
    strong = [i for i in invariant_results if i['strength'] in ('STRONG', 'MODERATE')]

    return {
        'elapsed_sec':     round(time.time() - t0, 2),
        'n_invariants':    len(invariant_results),
        'invariants':      invariant_results,
        'strong_laws':     strong,
        'best_predictor':  invariant_results[0]['invariant'] if invariant_results else '—',
    }

def _record_test(lst, label, result):
    lst.append((label, result))

# ─── Command: energy_full ─────────────────────────────────────────────────────

def cmd_energy_full(params):
    """Run all 8 energy commands sequentially."""
    t0 = time.time()
    results = {}
    commands = [
        ('energy_now',           cmd_energy_now),
        ('energy_flow',          cmd_energy_flow),
        ('energy_accumulation',  cmd_energy_accumulation),
        ('energy_transformation',cmd_energy_transformation),
        ('energy_persistence',   cmd_energy_persistence),
        ('regime_energy',        cmd_regime_energy),
        ('failure_physics',      cmd_failure_physics),
        ('energy_invariants',    cmd_energy_invariants),
    ]
    for key, fn in commands:
        try:
            results[key] = fn({})
        except Exception as ex:
            results[key] = {'error': str(ex)}
    results['elapsed_sec'] = round(time.time() - t0, 2)
    return results

# ─── Dispatch ────────────────────────────────────────────────────────────────

COMMANDS = {
    'energy_now':           cmd_energy_now,
    'energy_flow':          cmd_energy_flow,
    'energy_accumulation':  cmd_energy_accumulation,
    'energy_transformation':cmd_energy_transformation,
    'energy_persistence':   cmd_energy_persistence,
    'regime_energy':        cmd_regime_energy,
    'failure_physics':      cmd_failure_physics,
    'energy_invariants':    cmd_energy_invariants,
    'energy_full':          cmd_energy_full,
}

if __name__ == '__main__':
    try:
        # Support both argv (night_lab style) and stdin (legacy)
        if len(sys.argv) >= 2 and sys.argv[1] in COMMANDS:
            cmd = sys.argv[1]
            par = json.loads(sys.argv[2]) if len(sys.argv) >= 3 else {}
        else:
            inp = json.loads(sys.stdin.read())
            cmd = inp.get('command', '')
            par = inp.get('params', {})
        fn = COMMANDS.get(cmd)
        if fn is None:
            print(json.dumps({'error': f'unknown command: {cmd}',
                              'available': list(COMMANDS.keys())}))
        else:
            print(json.dumps(fn(par)))
    except Exception as ex:
        import traceback
        print(json.dumps({'error': str(ex), 'traceback': traceback.format_exc()}))
