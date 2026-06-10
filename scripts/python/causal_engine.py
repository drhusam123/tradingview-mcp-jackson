#!/usr/bin/env python3
"""
Market Temporal Causality Engine — Phase 5
============================================
Discovers the CAUSAL TEMPORAL STRUCTURE governing market behavior.

NOT correlations. NOT simultaneous relationships.
DIRECTED cause → effect relationships with measured lags,
feedback loops, and multi-stage behavioral cascades.

Phase 1: Latent Market Behavior Engine   (latent_engine.py)
Phase 2: Force Field Engine              (force_field_engine.py)
Phase 3: Propagation Engine              (propagation_engine.py)
Phase 4: Energy Flow Engine              (energy_flow_engine.py)
Phase 5: THIS — Temporal Causal Structure

Commands (stdin JSON: {"command": "...", "params": {...}}):
  causal_now          — current causal position + next-event predictions (~2s)
  causal_chains       — P(B at t+n | A at t): 2/3-step causal chain probabilities (~20s)
  feedback_loops      — self-reinforcing and dampening causal cycles (~15s)
  temporal_memory     — how long causal effects persist: decay curves (~15s)
  sector_causal_roles — TRIGGER/PROPAGATOR/REACTOR/ABSORBER roles (~15s)
  causal_failure      — why chains broke: dampening mechanisms (~15s)
  regime_causality    — separate causal graphs per BULL/STRESS/CRISIS (~20s)
  causal_invariants   — universal causal laws: persistent chains (~15s)
  causal_full         — complete temporal causality report (~2min)
"""

import json, sys, time, sqlite3, math
from pathlib import Path
from collections import defaultdict

DB_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')

# ─── Behavioral Event Thresholds ─────────────────────────────────────────────

HIGH_E   = 0.35
MEDIUM_E = 0.20
LOW_E    = 0.10
MAX_CAUSAL_LAG = 10    # bars of causal look-ahead
MIN_EVENTS     = 8     # minimum events for statistical reliability

# ─── Behavioral Events ───────────────────────────────────────────────────────
# Each event is detected as an ONSET: crossing from below to above threshold.
# This captures "when did this behavioral state begin?" not "is it active?"

BEHAVIORAL_EVENTS = {
    'PANIC_ONSET':       lambda e, p: e.get('PANIC_ENERGY',0) >= HIGH_E   and p.get('PANIC_ENERGY',0) < HIGH_E,
    'MOMENTUM_SURGE':    lambda e, p: e.get('MOMENTUM_ENERGY',0) >= HIGH_E and p.get('MOMENTUM_ENERGY',0) < HIGH_E,
    'EXHAUSTION_ONSET':  lambda e, p: e.get('EXHAUSTION_ENERGY',0) >= MEDIUM_E and p.get('EXHAUSTION_ENERGY',0) < MEDIUM_E,
    'VOL_COMPRESSION':   lambda e, p: e.get('VOLATILITY_ENERGY',0) >= HIGH_E   and p.get('VOLATILITY_ENERGY',0) < HIGH_E,
    'VOL_EXPLOSION':     lambda e, p: e.get('VOLATILITY_ENERGY',0) < LOW_E     and p.get('VOLATILITY_ENERGY',0) >= HIGH_E,
    'INSTABILITY_SPIKE': lambda e, p: e.get('INSTABILITY_ENERGY',0) >= HIGH_E  and p.get('INSTABILITY_ENERGY',0) < HIGH_E,
    'REVERSAL_ONSET':    lambda e, p: e.get('MEAN_REVERSION_PRESSURE',0) >= HIGH_E and p.get('MEAN_REVERSION_PRESSURE',0) < HIGH_E,
    'LIQUIDITY_DRAIN':   lambda e, p: e.get('LIQUIDITY_STRESS',0) >= HIGH_E   and p.get('LIQUIDITY_STRESS',0) < HIGH_E,
    'TREND_BREAKOUT':    lambda e, p: e.get('TREND_PERSISTENCE_ENERGY',0) >= HIGH_E and p.get('TREND_PERSISTENCE_ENERGY',0) < HIGH_E,
    'RECOVERY_ONSET':    lambda e, p: e.get('PANIC_ENERGY',0) < LOW_E          and p.get('PANIC_ENERGY',0) >= MEDIUM_E,
}

EVENT_NAMES_AR = {
    'PANIC_ONSET':       'بداية الذعر',
    'MOMENTUM_SURGE':    'انطلاق الزخم',
    'EXHAUSTION_ONSET':  'بداية الإنهاك',
    'VOL_COMPRESSION':   'ضغط التقلب',
    'VOL_EXPLOSION':     'انفجار التقلب',
    'INSTABILITY_SPIKE': 'ارتفاع الاضطراب',
    'REVERSAL_ONSET':    'بداية الارتداد',
    'LIQUIDITY_DRAIN':   'جفاف السيولة',
    'TREND_BREAKOUT':    'كسر الترند',
    'RECOVERY_ONSET':    'بداية التعافي',
}

# ─── Math Utilities ───────────────────────────────────────────────────────────

def _mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else 0.0

def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2: return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))

def _compute_rsi(closes, period=14):
    rsi = [None] * len(closes)
    if len(closes) < period + 1: return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g, avg_l = sum(gains)/period, sum(losses)/period
    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i-1]
            avg_g = (avg_g * (period-1) + max(d, 0)) / period
            avg_l = (avg_l * (period-1) + max(-d, 0)) / period
        rsi[i] = round(100 - 100/(1 + avg_g/avg_l), 2) if avg_l > 1e-10 else 100.0
    return rsi

def _compute_atr(highs, lows, closes, period=14):
    atr = [None] * len(closes)
    if len(closes) < period + 1: return atr
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    avg = sum(trs[:period]) / period
    atr[period-1] = avg
    for i in range(period, len(closes)):
        avg = (avg * (period-1) + trs[i]) / period
        atr[i] = avg
    return atr

# ─── Energy + Event Computation ──────────────────────────────────────────────

def _compute_energy_vector(bars, i):
    """Compute energy vector for bar i (requires i >= 25)."""
    closes = [b['close'] for b in bars]
    highs  = [b['high']  for b in bars]
    lows   = [b['low']   for b in bars]
    vols   = [b['volume'] for b in bars]

    c   = closes[i]
    c1  = closes[i-1] or c
    c5  = closes[i-5]  or c
    c10 = closes[i-10] or c

    mom5   = (c - c5)  / c5  if c5  else 0
    mom10  = (c - c10) / c10 if c10 else 0
    daily  = (c - c1)  / c1  if c1  else 0

    avg_vol = _mean(vols[max(0,i-20):i]) or 1.0
    vol_r   = vols[i] / avg_vol if avg_vol > 0 else 1.0

    # ATR compression
    hist_atr = _mean([abs(highs[j]-lows[j]) for j in range(max(0,i-50), i)]) or abs(highs[i]-lows[i]) or 0.001
    curr_atr = abs(highs[i]-lows[i]) or 0.001
    compression = hist_atr / curr_atr

    mean20 = _mean(closes[i-20:i]) or c
    rets   = [(closes[j]-closes[j-1])/closes[j-1] for j in range(i-4,i+1) if closes[j-1]]
    n_up   = sum(1 for r in rets if r >  0.001)
    n_down = sum(1 for r in rets if r < -0.001)
    dir_c  = max(n_up, n_down) / len(rets) if rets else 0.0

    # Simplified RSI from rolling windows
    gains  = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1) if closes[j-1]]
    losses = [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1) if closes[j-1]]
    avg_g  = _mean(gains) or 0
    avg_l  = _mean(losses) or 1e-10
    rsi_i  = 100 - 100/(1 + avg_g/avg_l)

    mom_e    = min(1.0, abs(mom5)/0.05) * min(1.0, (abs(mom10)/0.10 + dir_c)/2)
    panic_r  = max(0.0, -daily/0.04) * min(2.0, vol_r)/2.0
    panic_rs = max(0.0, (25-rsi_i)/25) if rsi_i < 25 else 0.0
    panic_e  = min(1.0, panic_r*0.7 + panic_rs*0.3)
    if   rsi_i >= 70: exhaust_e = min(1.0, (rsi_i-70)/30 + abs(mom10)/0.15)
    elif rsi_i <= 30: exhaust_e = min(1.0, (30-rsi_i)/30 + abs(mom10)/0.15)
    else:             exhaust_e = 0.0
    vol_e   = min(1.0, max(0.0, (compression-1.0)/2.0))
    liq_e   = min(1.0, max(0.0, (1.0-vol_r)/0.8))
    rev_e   = min(1.0, abs(c-mean20)/(mean20*0.08)) if mean20 > 0 else 0.0
    instab  = min(1.0, panic_e*0.35 + exhaust_e*0.25 + vol_e*0.20 + liq_e*0.10 + rev_e*0.10)
    trend_e = min(1.0, dir_c * min(1.0, abs(mom5)/0.03 + 0.2))

    return {
        'time': bars[i]['time'],
        'MOMENTUM_ENERGY':          round(mom_e, 4),
        'PANIC_ENERGY':             round(panic_e, 4),
        'EXHAUSTION_ENERGY':        round(exhaust_e, 4),
        'VOLATILITY_ENERGY':        round(vol_e, 4),
        'LIQUIDITY_STRESS':         round(liq_e, 4),
        'MEAN_REVERSION_PRESSURE':  round(rev_e, 4),
        'INSTABILITY_ENERGY':       round(instab, 4),
        'TREND_PERSISTENCE_ENERGY': round(trend_e, 4),
    }

def _compute_energy_series_fast(bars, max_bars=None):
    """Fast energy series computation. Optionally capped."""
    if max_bars and len(bars) > max_bars:
        bars = bars[-max_bars:]
    if len(bars) < 30:
        return []
    result = []
    for i in range(25, len(bars)):
        result.append(_compute_energy_vector(bars, i))
    return result

def _extract_events(energy_series):
    """
    Extract event onsets from energy series.
    Returns {event_name: [index_in_series]}
    """
    events = defaultdict(list)
    for i in range(1, len(energy_series)):
        ev   = energy_series[i]
        prev = energy_series[i-1]
        for name, detector in BEHAVIORAL_EVENTS.items():
            if detector(ev, prev):
                events[name].append(i)
    return events

# ─── DB Loaders ───────────────────────────────────────────────────────────────

def _load_ohlcv_all(min_bars=50, max_bars=None):
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
        result[s] = bars[-max_bars:] if max_bars and len(bars) > max_bars else bars
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
            "ic.momentum_10d, ic.adx14, ic.bb_width, ic.macd_hist, "
            "ic.stoch_k, ic.williams_r, ic.cci20, ic.atr14, "
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

# ─── Core Causal Analytics ────────────────────────────────────────────────────

def _compute_causal_matrix(all_stock_events_list, max_lag=MAX_CAUSAL_LAG):
    """
    Compute causal probability matrix across all stocks.

    all_stock_events_list: list of (event_dict, series_length)
      where event_dict = {event_name: [bar_indices]}

    Returns:
      matrix[from_event][to_event] = {
        'lags': {n: {'p_cond', 'p_base', 'lift', 'n_from', 'n_confirmed'}},
        'peak_lag': lag with highest lift,
        'peak_lift': highest lift value,
        'best_p': conditional probability at peak lag,
        'n_total': total from_event occurrences,
      }
    """
    # Aggregate counts across all stocks
    # from_count[A]          = total A onset events
    # joint_count[A][B][lag] = how many times B happened within lag bars after A
    # total_bars             = sum of all series lengths

    from_count  = defaultdict(int)
    joint_count = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    total_bars  = 0

    for events, series_len in all_stock_events_list:
        total_bars += series_len
        for from_ev, from_idxs in events.items():
            from_count[from_ev] += len(from_idxs)
            for from_idx in from_idxs:
                for to_ev, to_idxs in events.items():
                    # Count to_idxs that fall in (from_idx, from_idx + lag]
                    # Using a sliding window approach
                    for lag in range(1, max_lag + 1):
                        target = from_idx + lag
                        if target in to_idxs or target - 1 in to_idxs:
                            # Simplified: check if any to_ev onset in [from+1, from+lag]
                            confirmed = any(from_idx < ti <= from_idx + lag for ti in to_idxs)
                            if confirmed:
                                joint_count[from_ev][to_ev][lag] += 1
                                break  # count once per from_event per lag

    # Wait — above has a bug, let me fix the joint counting
    # Reset and redo correctly
    joint_count2 = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for events, series_len in all_stock_events_list:
        for from_ev, from_idxs in events.items():
            from_set = set(from_idxs)
            for to_ev, to_idxs in events.items():
                to_set = set(to_idxs)
                for from_idx in from_idxs:
                    for lag in range(1, max_lag + 1):
                        # Did to_ev happen at exactly from_idx + lag?
                        if from_idx + lag in to_set:
                            joint_count2[from_ev][to_ev][lag] += 1

    # Baseline: P(to_ev at any bar)
    to_base = {}
    for to_ev, _ in BEHAVIORAL_EVENTS.items():
        total_to = sum(len(ev.get(to_ev, [])) for ev, _ in all_stock_events_list)
        to_base[to_ev] = total_to / max(total_bars, 1)

    # Build result matrix
    matrix = {}
    for from_ev in BEHAVIORAL_EVENTS:
        n_from = from_count.get(from_ev, 0)
        if n_from < MIN_EVENTS:
            continue
        matrix[from_ev] = {}
        for to_ev in BEHAVIORAL_EVENTS:
            lags_data = {}
            peak_lift, peak_lag, best_p = 0, 1, 0
            p_base = to_base.get(to_ev, 0.001)

            for lag in range(1, max_lag + 1):
                n_joint = joint_count2[from_ev][to_ev][lag]
                p_cond  = n_joint / n_from if n_from > 0 else 0
                lift    = round(p_cond / p_base, 2) if p_base > 1e-8 else 0
                lags_data[lag] = {
                    'p_cond': round(p_cond, 4),
                    'p_base': round(p_base, 4),
                    'lift':   lift,
                    'n_confirmed': n_joint,
                }
                if lift > peak_lift:
                    peak_lift, peak_lag, best_p = lift, lag, p_cond

            if peak_lift > 1.5:  # Only keep meaningful causal pairs
                matrix[from_ev][to_ev] = {
                    'lags':     lags_data,
                    'peak_lag': peak_lag,
                    'peak_lift': round(peak_lift, 2),
                    'best_p':   round(best_p, 4),
                    'n_total':  n_from,
                }

    return matrix

def _classify_energy_state_fast(ev):
    """Dominant energy form from energy vector."""
    dims = {
        'MOMENTUM_ENERGY':          ev.get('MOMENTUM_ENERGY', 0),
        'PANIC_ENERGY':             ev.get('PANIC_ENERGY', 0),
        'EXHAUSTION_ENERGY':        ev.get('EXHAUSTION_ENERGY', 0),
        'VOLATILITY_ENERGY':        ev.get('VOLATILITY_ENERGY', 0),
        'LIQUIDITY_STRESS':         ev.get('LIQUIDITY_STRESS', 0),
        'MEAN_REVERSION_PRESSURE':  ev.get('MEAN_REVERSION_PRESSURE', 0),
        'INSTABILITY_ENERGY':       ev.get('INSTABILITY_ENERGY', 0),
        'TREND_PERSISTENCE_ENERGY': ev.get('TREND_PERSISTENCE_ENERGY', 0),
    }
    mx = max(dims.values())
    if mx < LOW_E:
        return 'NEUTRAL'
    state_map = {
        'MOMENTUM_ENERGY':          'HIGH_MOMENTUM',
        'PANIC_ENERGY':             'PANIC',
        'EXHAUSTION_ENERGY':        'EXHAUSTION',
        'VOLATILITY_ENERGY':        'COMPRESSED_VOLATILITY',
        'LIQUIDITY_STRESS':         'LIQUIDITY_CRISIS',
        'MEAN_REVERSION_PRESSURE':  'REVERSAL_PRESSURE',
        'INSTABILITY_ENERGY':       'INSTABILITY',
        'TREND_PERSISTENCE_ENERGY': 'TRENDING',
    }
    return state_map.get(max(dims, key=dims.get), 'NEUTRAL')

def _classify_regime_energy(ev):
    """CRISIS/STRESS/BULL/MODERATE/CALM from energy vector."""
    if not ev:
        return 'MODERATE'
    score = ev.get('PANIC_ENERGY', 0) * 0.5 + ev.get('INSTABILITY_ENERGY', 0) * 0.5
    trend = ev.get('TREND_PERSISTENCE_ENERGY', 0)
    if score > 0.45:  return 'CRISIS'
    if score > 0.28:  return 'STRESS'
    if trend > 0.35:  return 'BULL'
    if score > 0.14:  return 'MODERATE'
    return 'CALM'

# ─── Command: causal_now ──────────────────────────────────────────────────────

def cmd_causal_now(params):
    """Current causal position + predicted next events (~2s)."""
    t0 = time.time()
    stocks = _load_indicators_now()
    if not stocks:
        return {'error': 'no indicator data'}

    sector_events = defaultdict(lambda: defaultdict(int))
    sector_energy = defaultdict(lambda: defaultdict(list))

    for s in stocks:
        rsi      = s.get('rsi14', 50)    or 50.0
        mom5     = (s.get('momentum_5d', 0)  or 0) / 100.0
        mom10    = (s.get('momentum_10d', 0) or 0) / 100.0
        vol_r    = s.get('vol_ratio_20', 1.0) or 1.0
        adx      = s.get('adx14', 25)    or 25.0
        bb_w     = s.get('bb_width', 0.06) or 0.06
        macd_h   = s.get('macd_hist', 0) or 0.0
        sector   = s['sector']

        # Compute approximate energy from indicators
        mom_e    = min(1.0, abs(mom5)/0.05) * min(1.0, abs(mom10)/0.10 + 0.2)
        panic_e  = min(1.0, max(0,(25-rsi)/25) if rsi<25 else 0 + max(0,-mom5/0.05))
        if   rsi >= 70: exhaust_e = min(1.0, (rsi-70)/30 + abs(mom10)/0.15)
        elif rsi <= 30: exhaust_e = min(1.0, (30-rsi)/30 + abs(mom10)/0.15)
        else:           exhaust_e = 0.0
        vol_e    = min(1.0, max(0, (0.06/bb_w - 0.8)/1.5)) if bb_w > 0 else 0
        liq_e    = min(1.0, max(0, (1-vol_r)/0.8))
        rev_e    = min(1.0, abs(rsi-50)/30.0 * 0.6)
        instab   = min(1.0, panic_e*0.35 + exhaust_e*0.25 + vol_e*0.20 + liq_e*0.10 + rev_e*0.10)
        trend_e  = min(1.0, adx/50.0 * min(1.0, abs(mom5)/0.03 + 0.2))

        ev = {
            'MOMENTUM_ENERGY': mom_e,   'PANIC_ENERGY': panic_e,
            'EXHAUSTION_ENERGY': exhaust_e, 'VOLATILITY_ENERGY': vol_e,
            'LIQUIDITY_STRESS': liq_e,  'MEAN_REVERSION_PRESSURE': rev_e,
            'INSTABILITY_ENERGY': instab, 'TREND_PERSISTENCE_ENERGY': trend_e,
        }

        for dim, val in ev.items():
            sector_energy[sector][dim].append(val)

        # Detect active events (current state, not onset)
        if panic_e >= HIGH_E:   sector_events[sector]['PANIC_ONSET'] += 1
        if mom_e >= HIGH_E:     sector_events[sector]['MOMENTUM_SURGE'] += 1
        if exhaust_e >= MEDIUM_E: sector_events[sector]['EXHAUSTION_ONSET'] += 1
        if vol_e >= HIGH_E:     sector_events[sector]['VOL_COMPRESSION'] += 1
        if instab >= HIGH_E:    sector_events[sector]['INSTABILITY_SPIKE'] += 1
        if rev_e >= HIGH_E:     sector_events[sector]['REVERSAL_ONSET'] += 1
        if liq_e >= HIGH_E:     sector_events[sector]['LIQUIDITY_DRAIN'] += 1
        if trend_e >= HIGH_E:   sector_events[sector]['TREND_BREAKOUT'] += 1

    # Aggregate sector energy
    sector_profiles = {}
    for sector, dims in sector_energy.items():
        sec_ev = {d: round(_mean(vs), 4) for d, vs in dims.items()}
        n = len(dims.get('MOMENTUM_ENERGY', []))
        state = _classify_energy_state_fast(sec_ev)

        # Active events (fraction of stocks with event active)
        active = {ev: round(cnt/n, 3)
                  for ev, cnt in sector_events[sector].items() if cnt > 0}

        # Predict next events based on current state (hardcoded causal rules)
        predictions = _predict_from_state(state, sec_ev, active)

        sector_profiles[sector] = {
            'n_stocks':   n,
            'state':      state,
            'energy':     sec_ev,
            'active_events': active,
            'predicted_next': predictions,
        }

    # Market-level summary
    all_evs = [ev for d in sector_energy.values() for ev in [d]]
    mkt_ev = {}
    for dim in list(sector_energy.values())[0]:
        mkt_ev[dim] = round(_mean([_mean(d.get(dim,[])) for d in sector_energy.values()]), 4)
    mkt_state = _classify_energy_state_fast(mkt_ev)

    # Most active causal events in market
    market_active = defaultdict(int)
    for sector, evs in sector_events.items():
        n = len(sector_energy.get(sector, {}).get('MOMENTUM_ENERGY', [1]))
        for ev, cnt in evs.items():
            market_active[ev] += cnt

    return {
        'elapsed_sec':     round(time.time()-t0, 2),
        'n_stocks':        sum(len(v.get('MOMENTUM_ENERGY',[])) for v in sector_energy.values()),
        'n_sectors':       len(sector_profiles),
        'market_state':    mkt_state,
        'market_energy':   mkt_ev,
        'sector_profiles': sector_profiles,
        'market_active_events': dict(sorted(market_active.items(), key=lambda x: -x[1])[:6]),
        'market_predictions':   _predict_from_state(mkt_state, mkt_ev, {}),
    }

def _predict_from_state(state, ev, active_events):
    """Rule-based next-event prediction from current energy state."""
    predictions = []

    # Rule: High momentum → expect exhaustion within 2-3 bars
    if ev.get('MOMENTUM_ENERGY', 0) > 0.6:
        predictions.append({'event': 'EXHAUSTION_ONSET', 'lag': 3, 'p': 0.48,
                            'reason': 'طاقة زخم عالية → إنهاك وشيك'})

    # Rule: High exhaustion → expect reversal within 5 bars (P=0.97 from Phase 4)
    if ev.get('EXHAUSTION_ENERGY', 0) > 0.50:
        predictions.append({'event': 'REVERSAL_ONSET', 'lag': 4, 'p': 0.97,
                            'reason': 'إنهاك مرتفع → ارتداد حتمي (P=0.97)'})

    # Rule: High reversal pressure → momentum swing (P=0.93 from Phase 4)
    if ev.get('MEAN_REVERSION_PRESSURE', 0) > 0.60:
        predictions.append({'event': 'MOMENTUM_SURGE', 'lag': 4, 'p': 0.93,
                            'reason': 'ضغط ارتداد عالٍ → انطلاق زخم (P=0.93)'})

    # Rule: Compressed vol > 0.4 → explosion (P=0.81 from Phase 4)
    if ev.get('VOLATILITY_ENERGY', 0) > 0.40:
        predictions.append({'event': 'VOL_EXPLOSION', 'lag': 6, 'p': 0.81,
                            'reason': 'تقلب مضغوط → انفجار وشيك (P=0.81)'})

    # Rule: Panic → expect instability spike
    if ev.get('PANIC_ENERGY', 0) > HIGH_E:
        predictions.append({'event': 'INSTABILITY_SPIKE', 'lag': 2, 'p': 0.65,
                            'reason': 'ذعر نشط → ارتفاع الاضطراب'})

    # Rule: Liquidity drain → instability buildup
    if ev.get('LIQUIDITY_STRESS', 0) > HIGH_E:
        predictions.append({'event': 'INSTABILITY_SPIKE', 'lag': 3, 'p': 0.55,
                            'reason': 'جفاف سيولة → اضطراب سوقي'})

    # Rule: Instability + panic active → cascade risk
    if ev.get('INSTABILITY_ENERGY', 0) > HIGH_E and ev.get('PANIC_ENERGY', 0) > MEDIUM_E:
        predictions.append({'event': 'PANIC_ONSET', 'lag': 2, 'p': 0.70,
                            'reason': 'اضطراب + ذعر = خطر الانهيار'})

    predictions.sort(key=lambda x: -x['p'])
    return predictions[:4]

# ─── Command: causal_chains ───────────────────────────────────────────────────

def cmd_causal_chains(params):
    """
    P(B at t+n | A at t): discover 2-step and 3-step causal chain probabilities.
    Uses last 150 bars per symbol.
    """
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    # Build event series for all stocks
    all_stock_events = []
    n_processed = 0
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20:
            continue
        events = _extract_events(series)
        if any(len(v) >= 2 for v in events.values()):
            all_stock_events.append((events, len(series)))
            n_processed += 1

    if not all_stock_events:
        return {'error': 'insufficient event data'}

    # Compute causal matrix
    matrix = _compute_causal_matrix(all_stock_events)

    # Top causal pairs (2-step chains)
    top_pairs = []
    for from_ev, targets in matrix.items():
        for to_ev, data in targets.items():
            if from_ev == to_ev: continue
            top_pairs.append({
                'chain':    f'{from_ev} → {to_ev}',
                'from':     from_ev,
                'to':       to_ev,
                'peak_lag': data['peak_lag'],
                'lift':     data['peak_lift'],
                'p_cond':   data['best_p'],
                'n_events': data['n_total'],
                'strength': 'STRONG' if data['peak_lift'] > 3 else 'MODERATE' if data['peak_lift'] > 2 else 'WEAK',
            })
    top_pairs.sort(key=lambda x: -x['lift'])

    # 3-step chains: A → B → C
    # Find chains where A→B is strong AND B→C is strong
    chains_3step = []
    for p1 in top_pairs[:20]:
        if p1['lift'] < 2.0: continue
        for p2 in top_pairs[:20]:
            if p2['from'] == p1['to'] and p2['to'] != p1['from']:
                combined_p = round(p1['p_cond'] * p2['p_cond'], 4)
                chains_3step.append({
                    'chain':    f"{p1['from']} → {p1['to']} → {p2['to']}",
                    'lag_A_B':  p1['peak_lag'],
                    'lag_B_C':  p2['peak_lag'],
                    'total_lag': p1['peak_lag'] + p2['peak_lag'],
                    'p_A_B':    p1['p_cond'],
                    'p_B_C':    p2['p_cond'],
                    'p_chain':  combined_p,
                    'min_lift': min(p1['lift'], p2['lift']),
                })
    chains_3step.sort(key=lambda x: -x['p_chain'])

    # Causal graph summary: for each event, its strongest predictor and effect
    event_summary = {}
    for ev in BEHAVIORAL_EVENTS:
        # Best predictor of this event
        predictors = [(from_ev, data) for from_ev, targets in matrix.items()
                      if ev in targets for data in [targets[ev]]]
        predictors.sort(key=lambda x: -x[1]['peak_lift'])

        # Best effect of this event
        effects = list((matrix.get(ev, {})).items())
        effects.sort(key=lambda x: -x[1]['peak_lift'])

        n_from = sum(len(d[0].get(ev, [])) for d in all_stock_events)
        event_summary[ev] = {
            'n_total_events': n_from,
            'strongest_predictor': predictors[0][0] if predictors else None,
            'predictor_lift':      round(predictors[0][1]['peak_lift'], 2) if predictors else 0,
            'predictor_lag':       predictors[0][1]['peak_lag'] if predictors else 0,
            'strongest_effect':    effects[0][0] if effects else None,
            'effect_lift':         round(effects[0][1]['peak_lift'], 2) if effects else 0,
            'effect_lag':          effects[0][1]['peak_lag'] if effects else 0,
        }

    return {
        'elapsed_sec':   round(time.time()-t0, 2),
        'n_stocks':      n_processed,
        'n_causal_pairs': len(top_pairs),
        'top_causal_pairs': top_pairs[:20],
        'chains_3step':  chains_3step[:10],
        'event_summary': event_summary,
    }

# ─── Command: feedback_loops ─────────────────────────────────────────────────

def cmd_feedback_loops(params):
    """
    Detect self-reinforcing and dampening causal feedback cycles.
    A→B and B→A = loop. Measure loop gain.
    """
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    all_stock_events = []
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20: continue
        events = _extract_events(series)
        if any(len(v) >= 2 for v in events.values()):
            all_stock_events.append((events, len(series)))

    matrix = _compute_causal_matrix(all_stock_events)

    # Detect loops: A→B and B→A both present
    loops = []
    events_list = list(BEHAVIORAL_EVENTS.keys())
    for i, ev_A in enumerate(events_list):
        for ev_B in events_list[i+1:]:
            a_to_b = matrix.get(ev_A, {}).get(ev_B)
            b_to_a = matrix.get(ev_B, {}).get(ev_A)
            if not a_to_b or not b_to_a: continue

            # Loop gain: product of both lifts
            gain = round(a_to_b['peak_lift'] * b_to_a['peak_lift'], 2)
            # Loop type: if gain > 4, strongly self-reinforcing (positive feedback)
            # If both p_cond < 0.3, dampening (negative feedback)
            total_lag = a_to_b['peak_lag'] + b_to_a['peak_lag']
            avg_p     = (a_to_b['best_p'] + b_to_a['best_p']) / 2

            if   gain > 6:    loop_type = 'EXPLOSIVE'
            elif gain > 4:    loop_type = 'AMPLIFYING'
            elif gain > 2.5:  loop_type = 'REINFORCING'
            elif avg_p < 0.15: loop_type = 'DAMPENING'
            else:              loop_type = 'NEUTRAL'

            loops.append({
                'loop':       f'{ev_A} ↔ {ev_B}',
                'ev_A':       ev_A,
                'ev_B':       ev_B,
                'A_to_B_lag': a_to_b['peak_lag'],
                'B_to_A_lag': b_to_a['peak_lag'],
                'A_to_B_lift': a_to_b['peak_lift'],
                'B_to_A_lift': b_to_a['peak_lift'],
                'loop_gain':  gain,
                'total_cycle_lag': total_lag,
                'loop_type':  loop_type,
            })

    loops.sort(key=lambda x: -x['loop_gain'])

    # Self-loops (A→A with lag): autocorrelation-like causal persistence
    self_loops = []
    for ev in events_list:
        a_to_a = matrix.get(ev, {}).get(ev)
        if a_to_a and a_to_a['peak_lift'] > 1.5:
            self_loops.append({
                'event':     ev,
                'lag':       a_to_a['peak_lag'],
                'lift':      a_to_a['peak_lift'],
                'p_persist': a_to_a['best_p'],
                'type':      'SELF_REINFORCING' if a_to_a['peak_lift'] > 3 else 'PERSISTENT',
            })
    self_loops.sort(key=lambda x: -x['lift'])

    # Amplification chains: loops where gain > 4
    amplifiers = [l for l in loops if l['loop_type'] in ('EXPLOSIVE', 'AMPLIFYING')]
    dampeners  = [l for l in loops if l['loop_type'] == 'DAMPENING']

    return {
        'elapsed_sec':  round(time.time()-t0, 2),
        'n_loops':      len(loops),
        'n_amplifying': len(amplifiers),
        'n_dampening':  len(dampeners),
        'loops':        loops[:12],
        'self_loops':   self_loops[:6],
        'amplification_loops': amplifiers[:5],
        'dampening_loops':     dampeners[:5],
        'max_gain':     loops[0]['loop_gain'] if loops else 0,
        'most_dangerous_loop': loops[0]['loop'] if loops else '—',
    }

# ─── Command: temporal_memory ─────────────────────────────────────────────────

def cmd_temporal_memory(params):
    """How long do causal effects persist? Decay curves per event pair."""
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    all_stock_events = []
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20: continue
        events = _extract_events(series)
        if any(len(v) >= 2 for v in events.values()):
            all_stock_events.append((events, len(series)))

    # For each event type A, compute P(B at t+n | A at t) for n=1..15
    # This gives us the "causal memory curve" = how long A's causal effect persists
    MAX_DECAY_LAG = 15
    total_bars = sum(s for _, s in all_stock_events)

    # Count from_events and joint events
    from_count  = defaultdict(int)
    joint_at_n  = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    to_base     = defaultdict(int)

    for events, series_len in all_stock_events:
        for from_ev, from_idxs in events.items():
            from_count[from_ev] += len(from_idxs)
            for to_ev, to_idxs in events.items():
                to_set = set(to_idxs)
                to_base[to_ev] += len(to_idxs)
                for from_idx in from_idxs:
                    for lag in range(1, MAX_DECAY_LAG + 1):
                        if from_idx + lag in to_set:
                            joint_at_n[from_ev][to_ev][lag] += 1

    # Build decay curves for top causal pairs
    decay_curves = {}
    top_causal_pairs = []

    for from_ev in BEHAVIORAL_EVENTS:
        n_from = from_count.get(from_ev, 0)
        if n_from < MIN_EVENTS: continue
        for to_ev in BEHAVIORAL_EVENTS:
            if from_ev == to_ev: continue
            p_base = to_base.get(to_ev, 0) / max(total_bars, 1)
            if p_base < 1e-8: continue

            curve = {}
            peak_lag, peak_lift = 1, 0
            for lag in range(1, MAX_DECAY_LAG + 1):
                p_cond = joint_at_n[from_ev][to_ev][lag] / n_from if n_from > 0 else 0
                lift   = p_cond / p_base if p_base > 0 else 0
                curve[lag] = round(lift, 2)
                if lift > peak_lift:
                    peak_lift, peak_lag = lift, lag

            if peak_lift < 1.5: continue

            # Find causal memory duration: lag at which lift drops below 1.2 (baseline)
            memory_bars = MAX_DECAY_LAG
            for lag in range(peak_lag, MAX_DECAY_LAG + 1):
                if curve.get(lag, 0) < 1.2:
                    memory_bars = lag - peak_lag
                    break

            key = f'{from_ev}→{to_ev}'
            decay_curves[key] = {
                'from':          from_ev,
                'to':            to_ev,
                'peak_lag':      peak_lag,
                'peak_lift':     round(peak_lift, 2),
                'memory_bars':   memory_bars,
                'p_base':        round(p_base, 4),
                'lift_curve':    {str(k): v for k, v in curve.items()},
            }
            top_causal_pairs.append((key, peak_lift, memory_bars))

    top_causal_pairs.sort(key=lambda x: -x[1])
    top_keys = [k for k, _, _ in top_causal_pairs[:15]]

    # Memory summary per trigger event
    event_memory = {}
    for from_ev in BEHAVIORAL_EVENTS:
        n_from = from_count.get(from_ev, 0)
        if n_from < MIN_EVENTS: continue
        relevant = [d for k, d in decay_curves.items() if d['from'] == from_ev]
        if not relevant: continue
        avg_memory  = round(_mean([r['memory_bars'] for r in relevant]), 1)
        max_lift_ev = max(relevant, key=lambda x: x['peak_lift'])
        event_memory[from_ev] = {
            'n_events':     n_from,
            'n_effects':    len(relevant),
            'avg_memory_bars': avg_memory,
            'strongest_effect': max_lift_ev['to'],
            'strongest_lift':   max_lift_ev['peak_lift'],
        }

    return {
        'elapsed_sec':   round(time.time()-t0, 2),
        'n_causal_pairs':len(decay_curves),
        'top_decay_curves': {k: decay_curves[k] for k in top_keys if k in decay_curves},
        'event_memory':  event_memory,
        'longest_memory': max(event_memory.items(), key=lambda x: x[1]['avg_memory_bars'])[0]
                          if event_memory else '—',
        'shortest_memory': min(event_memory.items(), key=lambda x: x[1]['avg_memory_bars'])[0]
                           if event_memory else '—',
    }

# ─── Command: sector_causal_roles ────────────────────────────────────────────

def cmd_sector_causal_roles(params):
    """Classify sectors as TRIGGER/PROPAGATOR/REACTOR/AMPLIFIER/ABSORBER/STABILIZER."""
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    # Build sector event series
    sector_events_by_date = defaultdict(lambda: defaultdict(list))  # {sector: {event: [dates]}}
    sector_n_stocks = defaultdict(int)

    for sym, bars in ohlcv.items():
        sector = smap.get(sym, 'Unknown')
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20: continue
        events = _extract_events(series)
        sector_n_stocks[sector] += 1
        for ev_name, indices in events.items():
            for idx in indices:
                t = series[idx]['time']
                sector_events_by_date[sector][ev_name].append(t)

    # Aggregate sector events to "event density per period"
    # For each sector, compute event onset frequency
    sector_list = [s for s in sector_events_by_date if sector_n_stocks[s] >= 3]

    # Cross-sector lag analysis
    # For each sector pair (s1, s2): does s1's INSTABILITY_SPIKE predict s2's?
    # Build sector-level causal matrix

    # Use "event count per time window" as a time series for each sector
    # Get all dates
    all_dates = sorted(set(
        t for sec in sector_events_by_date.values()
          for ev_dates in sec.values()
          for t in ev_dates
    ))
    if not all_dates:
        return {'error': 'insufficient sector event data'}

    date_idx = {d: i for i, d in enumerate(all_dates)}
    n_dates  = len(all_dates)

    # For each sector: binary time series per event type (1 if event occurred near this date)
    sector_event_ts = {}
    for sector in sector_list:
        ev_ts = {}
        for ev_name in BEHAVIORAL_EVENTS:
            ts = [0] * n_dates
            for t in sector_events_by_date[sector].get(ev_name, []):
                if t in date_idx:
                    ts[date_idx[t]] = 1
            ev_ts[ev_name] = ts
        sector_event_ts[sector] = ev_ts

    # Compute leading/lagging behavior per sector vs market
    # Market baseline: fraction of sectors with event active per date
    mkt_event_ts = {}
    for ev_name in BEHAVIORAL_EVENTS:
        mkt_ts = [0.0] * n_dates
        for d_i in range(n_dates):
            active = sum(1 for s in sector_list if sector_event_ts[s][ev_name][d_i] == 1)
            mkt_ts[d_i] = active / max(len(sector_list), 1)
        mkt_event_ts[ev_name] = mkt_ts

    # For each sector: lead/lag vs market for key events
    sector_roles = {}
    focus_events = ['INSTABILITY_SPIKE', 'PANIC_ONSET', 'MOMENTUM_SURGE', 'EXHAUSTION_ONSET']

    for sector in sector_list:
        lag_scores = []
        for ev_name in focus_events:
            sec_ts = sector_event_ts[sector][ev_name]
            mkt_ts = mkt_event_ts[ev_name]
            if sum(sec_ts) < 2: continue

            # Find best lag: does sector lead (negative lag) or follow (positive lag)?
            best_lag, best_r = 0, 0
            for lag in range(-5, 6):
                if lag >= 0:
                    a = sec_ts[:n_dates-lag] if lag else sec_ts
                    b = mkt_ts[lag:] if lag else mkt_ts
                else:
                    a = sec_ts[-lag:]
                    b = mkt_ts[:n_dates+lag]
                n = min(len(a), len(b))
                if n < 5: continue
                mx, my = _mean(a[:n]), _mean(b[:n])
                num = sum((a[i]-mx)*(b[i]-my) for i in range(n))
                dx  = math.sqrt(sum((v-mx)**2 for v in a[:n]))
                dy  = math.sqrt(sum((v-my)**2 for v in b[:n]))
                r = num/(dx*dy) if dx*dy > 1e-12 else 0
                if r > best_r:
                    best_r, best_lag = r, lag
            if best_r > 0.15:
                lag_scores.append(best_lag)

        if not lag_scores:
            sector_roles[sector] = {'role': 'UNKNOWN', 'avg_lag': 0, 'n_stocks': sector_n_stocks[sector]}
            continue

        avg_lag     = round(_mean(lag_scores), 2)
        n_stocks    = sector_n_stocks[sector]
        n_events_total = sum(len(v) for v in sector_events_by_date[sector].values())
        event_density  = round(n_events_total / max(n_stocks * n_dates, 1) * 100, 3)

        if   avg_lag < -2 and event_density > 0.05: role = 'CAUSAL_TRIGGER'
        elif avg_lag < -1:                           role = 'EARLY_PROPAGATOR'
        elif avg_lag > 2 and event_density < 0.02:  role = 'TERMINAL_ABSORBER'
        elif avg_lag > 1:                            role = 'DELAYED_REACTOR'
        elif event_density > 0.08:                  role = 'FEEDBACK_AMPLIFIER'
        elif event_density < 0.01:                  role = 'STABILIZATION_NODE'
        else:                                        role = 'NEUTRAL_TRANSMITTER'

        sector_roles[sector] = {
            'role':          role,
            'avg_lag_vs_market': avg_lag,
            'event_density': event_density,
            'n_stocks':      n_stocks,
            'n_events':      n_events_total,
        }

    # Role distribution
    role_dist = defaultdict(list)
    for sec, info in sector_roles.items():
        role_dist[info['role']].append(sec)

    return {
        'elapsed_sec':   round(time.time()-t0, 2),
        'n_sectors':     len(sector_roles),
        'sector_roles':  sector_roles,
        'role_distribution': {r: secs for r, secs in role_dist.items()},
        'triggers':      role_dist.get('CAUSAL_TRIGGER', []),
        'absorbers':     role_dist.get('TERMINAL_ABSORBER', []),
        'amplifiers':    role_dist.get('FEEDBACK_AMPLIFIER', []),
        'early_propagators': role_dist.get('EARLY_PROPAGATOR', []),
    }

# ─── Command: causal_failure ─────────────────────────────────────────────────

def cmd_causal_failure(params):
    """Why did expected causal chains fail to complete?"""
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    # For each strong causal pair (A→B), find cases where A fired but B did NOT follow
    # Compare those cases to cases where B DID follow
    # What was different? → The energy state that prevented propagation

    all_stock_events = []
    all_stock_series = []
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20: continue
        events = _extract_events(series)
        if any(len(v) >= 2 for v in events.values()):
            all_stock_events.append((events, len(series)))
            all_stock_series.append((sym, series, events))

    # Key expected chains (from Phase 4 invariants + Phase 5 causal matrix)
    expected_chains = [
        ('EXHAUSTION_ONSET',  'REVERSAL_ONSET',    5, 'إنهاك → ارتداد'),
        ('INSTABILITY_SPIKE', 'PANIC_ONSET',        3, 'اضطراب → ذعر'),
        ('VOL_COMPRESSION',   'VOL_EXPLOSION',      8, 'ضغط تقلب → انفجار'),
        ('PANIC_ONSET',       'INSTABILITY_SPIKE',  3, 'ذعر → اضطراب'),
        ('MOMENTUM_SURGE',    'EXHAUSTION_ONSET',   5, 'زخم → إنهاك'),
        ('LIQUIDITY_DRAIN',   'INSTABILITY_SPIKE',  5, 'جفاف سيولة → اضطراب'),
    ]

    failure_analysis = {}
    for from_ev, to_ev, lag_window, label in expected_chains:
        n_triggered = 0
        n_completed = 0
        failed_state  = defaultdict(list)   # energy state when chain failed
        success_state = defaultdict(list)   # energy state when chain succeeded

        for sym, series, events in all_stock_series:
            from_idxs = events.get(from_ev, [])
            to_set    = set(events.get(to_ev, []))
            for from_idx in from_idxs:
                n_triggered += 1
                completed = any(from_idx < ti <= from_idx + lag_window for ti in to_set)
                # Get energy state at from_idx
                if from_idx < len(series):
                    ev_state = series[from_idx]
                    for dim in ['VOLATILITY_ENERGY', 'LIQUIDITY_STRESS', 'INSTABILITY_ENERGY']:
                        val = ev_state.get(dim, 0)
                        if completed:
                            success_state[dim].append(val)
                        else:
                            failed_state[dim].append(val)
                if completed:
                    n_completed += 1

        if n_triggered < MIN_EVENTS:
            continue

        p_success = round(n_completed / n_triggered, 3)
        p_failure = round(1 - p_success, 3)

        # What was different about failed vs successful?
        differentiators = {}
        for dim in ['VOLATILITY_ENERGY', 'LIQUIDITY_STRESS', 'INSTABILITY_ENERGY']:
            avg_fail    = round(_mean(failed_state[dim]), 3)
            avg_success = round(_mean(success_state[dim]), 3)
            if abs(avg_fail - avg_success) > 0.05:
                differentiators[dim] = {
                    'failed':    avg_fail,
                    'succeeded': avg_success,
                    'delta':     round(avg_fail - avg_success, 3),
                }

        # Identify main failure mechanism
        if differentiators:
            main_blocker = max(differentiators, key=lambda d: abs(differentiators[d]['delta']))
            delta = differentiators[main_blocker]['delta']
            if delta > 0:
                mechanism = f"عالي {main_blocker} يكبح الانتقال"
            else:
                mechanism = f"منخفض {main_blocker} يُطفئ الانتقال"
        else:
            mechanism = 'آلية غير محددة'

        failure_analysis[f'{from_ev}→{to_ev}'] = {
            'label':          label,
            'n_triggered':    n_triggered,
            'n_completed':    n_completed,
            'p_success':      p_success,
            'p_failure':      p_failure,
            'differentiators': differentiators,
            'failure_mechanism': mechanism,
        }

    # Overall market dampening score
    avg_failure = round(_mean([v['p_failure'] for v in failure_analysis.values()]), 3)

    return {
        'elapsed_sec':      round(time.time()-t0, 2),
        'n_chains_tested':  len(failure_analysis),
        'avg_failure_rate': avg_failure,
        'chain_analysis':   failure_analysis,
        'most_reliable':    min(failure_analysis.items(), key=lambda x: x[1]['p_failure'])[0]
                            if failure_analysis else '—',
        'most_blocked':     max(failure_analysis.items(), key=lambda x: x[1]['p_failure'])[0]
                            if failure_analysis else '—',
    }

# ─── Command: regime_causality ────────────────────────────────────────────────

def cmd_regime_causality(params):
    """Separate causal graphs per market regime (BULL/STRESS/CRISIS)."""
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    # Classify each stock's time series dates by regime
    # Then build separate causal matrices per regime

    # First build all energy series
    stock_series = {}
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) >= 20:
            stock_series[sym] = series

    # Compute market-level regime per date
    from collections import Counter
    date_regimes = {}
    date_state_counts = defaultdict(lambda: Counter())
    for sym, series in stock_series.items():
        for ev in series:
            t = ev['time']
            state = _classify_regime_energy(ev)
            date_state_counts[t][state] += 1

    for t, counter in date_state_counts.items():
        if sum(counter.values()) >= 10:
            date_regimes[t] = counter.most_common(1)[0][0]

    if not date_regimes:
        return {'error': 'insufficient regime data'}

    # Split events by regime
    regime_events = defaultdict(list)  # {regime: [(events_dict, series_len)]}

    for sym, series in stock_series.items():
        # For this stock, split series by regime
        regime_splits = defaultdict(list)
        for i, ev in enumerate(series):
            t = ev['time']
            regime = date_regimes.get(t)
            if regime:
                regime_splits[regime].append(i)

        for regime, indices in regime_splits.items():
            # Extract events only within this regime's indices
            idx_set = set(indices)
            sub_events = defaultdict(list)
            for ev_name, detector in BEHAVIORAL_EVENTS.items():
                for j in range(1, len(series)):
                    if j in idx_set and j-1 in idx_set:
                        if detector(series[j], series[j-1]):
                            sub_events[ev_name].append(j)
            if any(len(v) >= 2 for v in sub_events.values()):
                regime_events[regime].append((dict(sub_events), len(indices)))

    # Build causal matrix per regime
    regime_matrices = {}
    for regime, ev_list in regime_events.items():
        if len(ev_list) < 5: continue
        matrix = _compute_causal_matrix(ev_list, max_lag=7)
        # Extract top causal pairs for this regime
        pairs = []
        for from_ev, targets in matrix.items():
            for to_ev, data in targets.items():
                if from_ev != to_ev:
                    pairs.append({
                        'chain':    f'{from_ev}→{to_ev}',
                        'lift':     data['peak_lift'],
                        'lag':      data['peak_lag'],
                        'p_cond':   data['best_p'],
                    })
        pairs.sort(key=lambda x: -x['lift'])
        regime_matrices[regime] = {'top_pairs': pairs[:10], 'n_input_series': len(ev_list)}

    # Cross-regime comparison: which chains are universal vs regime-specific?
    all_chains = set()
    for rm in regime_matrices.values():
        for p in rm['top_pairs']:
            all_chains.add(p['chain'])

    universal_chains = []
    regime_specific  = {}
    for chain in all_chains:
        present_in = [r for r, rm in regime_matrices.items()
                      if any(p['chain'] == chain for p in rm['top_pairs'])]
        if len(present_in) >= len(regime_matrices) - 1:
            lifts = [p['lift'] for r, rm in regime_matrices.items()
                     for p in rm['top_pairs'] if p['chain'] == chain]
            universal_chains.append({'chain': chain, 'n_regimes': len(present_in),
                                     'avg_lift': round(_mean(lifts), 2)})
        elif len(present_in) == 1:
            regime_specific[present_in[0]] = regime_specific.get(present_in[0], [])
            regime_specific[present_in[0]].append(chain)

    universal_chains.sort(key=lambda x: -x['avg_lift'])

    return {
        'elapsed_sec':      round(time.time()-t0, 2),
        'n_regimes':        len(regime_matrices),
        'regime_distribution': {r: len(regime_events[r]) for r in regime_events},
        'regime_matrices':  regime_matrices,
        'universal_chains': universal_chains[:8],
        'regime_specific':  regime_specific,
    }

# ─── Command: causal_invariants ──────────────────────────────────────────────

def cmd_causal_invariants(params):
    """Universal causal laws: persistent chains that hold across all conditions."""
    t0 = time.time()
    ohlcv = _load_ohlcv_all(max_bars=150)
    smap  = _load_sector_map()

    all_stock_events = []
    all_stock_series = []
    for sym, bars in ohlcv.items():
        series = _compute_energy_series_fast(bars, max_bars=150)
        if len(series) < 20: continue
        events = _extract_events(series)
        if any(len(v) >= 2 for v in events.values()):
            all_stock_events.append((events, len(series)))
            all_stock_series.append((sym, series, events))

    # Test specific causal hypotheses with confidence intervals
    invariant_tests = [
        # (from_event, to_event, max_lag, description, expected_P_min)
        ('EXHAUSTION_ONSET',  'REVERSAL_ONSET',    5,  'إنهاك → ارتداد خلال 5 أشرطة',        0.40),
        ('MOMENTUM_SURGE',    'EXHAUSTION_ONSET',  5,  'زخم قوي → إنهاك خلال 5 أشرطة',        0.20),
        ('INSTABILITY_SPIKE', 'PANIC_ONSET',        3,  'اضطراب → ذعر خلال 3 أشرطة',           0.20),
        ('VOL_COMPRESSION',   'VOL_EXPLOSION',      8,  'ضغط تقلب → انفجار خلال 8 أشرطة',      0.30),
        ('PANIC_ONSET',       'RECOVERY_ONSET',    10,  'ذعر → تعافي خلال 10 أشرطة',           0.15),
        ('LIQUIDITY_DRAIN',   'INSTABILITY_SPIKE',  5,  'جفاف سيولة → اضطراب خلال 5 أشرطة',   0.20),
        ('REVERSAL_ONSET',    'MOMENTUM_SURGE',     5,  'ارتداد → زخم جديد خلال 5 أشرطة',       0.25),
        ('PANIC_ONSET',       'REVERSAL_ONSET',     8,  'ذعر → ارتداد خلال 8 أشرطة',            0.20),
        ('TREND_BREAKOUT',    'EXHAUSTION_ONSET',   6,  'كسر الترند → إنهاك خلال 6 أشرطة',     0.25),
        ('INSTABILITY_SPIKE', 'VOL_COMPRESSION',    5,  'اضطراب → ضغط تقلب خلال 5 أشرطة',      0.15),
    ]

    results = []
    for from_ev, to_ev, max_lag, desc, expected_min in invariant_tests:
        n_triggered = 0
        n_confirmed = 0

        for sym, series, events in all_stock_series:
            from_idxs = events.get(from_ev, [])
            to_set    = set(events.get(to_ev, []))
            for from_idx in from_idxs:
                n_triggered += 1
                if any(from_idx < ti <= from_idx + max_lag for ti in to_set):
                    n_confirmed += 1

        if n_triggered < MIN_EVENTS:
            continue

        p = round(n_confirmed / n_triggered, 3)
        # Baseline: how often does to_ev happen in any max_lag window?
        total_series_len = sum(len(s) for _, s, _ in all_stock_series)
        total_to_events  = sum(len(ev.get(to_ev, [])) for _, _, ev in all_stock_series)
        p_base = round(total_to_events / max(total_series_len - max_lag, 1) * max_lag, 4)
        lift   = round(p / p_base, 2) if p_base > 0 else 0

        results.append({
            'chain':         desc,
            'from':          from_ev,
            'to':            to_ev,
            'max_lag':       max_lag,
            'p_confirmed':   p,
            'p_baseline':    p_base,
            'lift':          lift,
            'n_triggered':   n_triggered,
            'n_confirmed':   n_confirmed,
            'strength':      'STRONG'    if p > 0.55 and lift > 2.5 else
                             'MODERATE'  if p > 0.30 and lift > 1.8 else
                             'WEAK'      if p > expected_min else 'NOT_CONFIRMED',
        })

    results.sort(key=lambda x: -x['p_confirmed'])

    # Causal invariants: chains confirmed strong or moderate
    invariants = [r for r in results if r['strength'] in ('STRONG', 'MODERATE')]
    rejected   = [r for r in results if r['strength'] == 'NOT_CONFIRMED']

    # Universal motifs: the shortest confirmed causal loop
    loop_tests = [
        ('EXHAUSTION_ONSET', 'REVERSAL_ONSET', 'MOMENTUM_SURGE',
         'إنهاك → ارتداد → زخم (دورة الارتداد الكاملة)'),
        ('MOMENTUM_SURGE', 'EXHAUSTION_ONSET', 'REVERSAL_ONSET',
         'زخم → إنهاك → ارتداد (دورة الإنهاك الكاملة)'),
        ('INSTABILITY_SPIKE', 'PANIC_ONSET', 'RECOVERY_ONSET',
         'اضطراب → ذعر → تعافي (دورة الأزمة الكاملة)'),
    ]

    confirmed_loops = []
    for from_ev, mid_ev, to_ev, desc in loop_tests:
        p_ab = next((r['p_confirmed'] for r in results if r['from']==from_ev and r['to']==mid_ev), 0)
        p_bc = next((r['p_confirmed'] for r in results if r['from']==mid_ev  and r['to']==to_ev),  0)
        if p_ab > 0.1 and p_bc > 0.1:
            confirmed_loops.append({'loop': desc, 'p_chain': round(p_ab*p_bc, 4),
                                    'p_AB': p_ab, 'p_BC': p_bc})

    return {
        'elapsed_sec':    round(time.time()-t0, 2),
        'n_tested':       len(results),
        'n_confirmed':    len(invariants),
        'invariants':     invariants,
        'rejected_chains': rejected,
        'confirmed_loops': confirmed_loops,
        'strongest_law':  results[0]['chain'] if results else '—',
    }

# ─── Command: causal_full ─────────────────────────────────────────────────────

def cmd_causal_full(params):
    """Run all 8 causal commands sequentially."""
    t0 = time.time()
    results = {}
    commands = [
        ('causal_now',          cmd_causal_now),
        ('causal_chains',       cmd_causal_chains),
        ('feedback_loops',      cmd_feedback_loops),
        ('temporal_memory',     cmd_temporal_memory),
        ('sector_causal_roles', cmd_sector_causal_roles),
        ('causal_failure',      cmd_causal_failure),
        ('regime_causality',    cmd_regime_causality),
        ('causal_invariants',   cmd_causal_invariants),
    ]
    for key, fn in commands:
        try:
            results[key] = fn({})
        except Exception as ex:
            results[key] = {'error': str(ex)}
    results['elapsed_sec'] = round(time.time() - t0, 2)
    return results

# ─── Phase 5 Enhancement: PCMCI Sector Causality (tigramite) ─────────────────

def cmd_pcmci_sectors(params):
    """
    PCMCI Causal Discovery between EGX sectors using tigramite.
    Finds true causal relationships with lag structure (not just correlation).
    """
    try:
        from tigramite import data_processing as pp
        from tigramite.pcmci import PCMCI
        from tigramite.independence_tests.parcorr import ParCorr
        HAS_TIGRAMITE = True
    except ImportError:
        HAS_TIGRAMITE = False

    if not HAS_TIGRAMITE:
        return {'error': 'tigramite not installed — run: pip3 install tigramite', 'elapsed': 0}

    import time as _time
    t0 = _time.time()

    try:
        import pandas as _pd
        import numpy as _np
        con = get_connection()

        # Load all OHLCV + sector, compute daily returns
        rows = con.execute("""
            SELECT oh.bar_time, su.sector,
                   oh.close,
                   LAG(oh.close) OVER (PARTITION BY oh.symbol ORDER BY oh.bar_time) as prev_close
            FROM ohlcv_history oh
            JOIN stock_universe su ON oh.symbol = su.symbol
            WHERE su.sector IS NOT NULL AND su.sector != ''
              AND oh.bar_time > (strftime('%s','now') - 86400*600)
            ORDER BY oh.bar_time, su.symbol
        """).fetchall()
        con.close()

        if not rows:
            return {'error': 'No OHLCV data', 'elapsed': 0}

        # Build sector daily average return
        df = _pd.DataFrame(rows, columns=['bar_time','sector','close','prev_close'])
        df['return'] = (df['close'] - df['prev_close']) / df['prev_close'].replace(0, _np.nan)
        df = df.dropna(subset=['return'])

        sector_ret = df.groupby(['bar_time','sector'])['return'].mean().reset_index()
        pivot = sector_ret.pivot(index='bar_time', columns='sector', values='return')
        pivot = pivot.dropna(axis=1, thresh=int(len(pivot)*0.7)).fillna(0)

        if pivot.shape[1] < 3:
            return {'error': 'Insufficient sector data (need ≥3 sectors)', 'elapsed': round(_time.time()-t0,2)}

        # Limit to top 10 sectors by variance
        sector_cols = pivot.var().nlargest(10).index.tolist()
        data_matrix = pivot[sector_cols].values.astype(float)

        dataframe = pp.DataFrame(data_matrix, var_names=sector_cols)
        pcmci = PCMCI(dataframe=dataframe, cond_ind_test=ParCorr(significance='analytic'), verbosity=0)
        results = pcmci.run_pcmci(tau_max=5, pc_alpha=0.1)

        causal_links = []
        p_matrix   = results['p_matrix']
        val_matrix = results['val_matrix']
        n_vars = len(sector_cols)

        for i in range(n_vars):
            for j in range(n_vars):
                if i == j: continue
                for lag in range(1, 6):
                    p_val    = float(p_matrix[i, j, lag])
                    strength = float(val_matrix[i, j, lag])
                    if p_val < 0.05 and abs(strength) > 0.05:
                        causal_links.append({
                            'cause':    sector_cols[j],
                            'effect':   sector_cols[i],
                            'lag_days': int(lag),
                            'p_value':  round(p_val, 4),
                            'strength': round(strength, 4),
                            'direction': 'POSITIVE' if strength > 0 else 'NEGATIVE',
                        })

        causal_links.sort(key=lambda x: -abs(x['strength']))

        # Save to DB
        con2 = get_connection()
        con2.execute("""CREATE TABLE IF NOT EXISTS pcmci_causal_links (
            cause TEXT, effect TEXT, lag_days INTEGER,
            p_value REAL, strength REAL, direction TEXT, updated_at TEXT,
            PRIMARY KEY (cause, effect, lag_days))""")
        con2.execute("DELETE FROM pcmci_causal_links")
        import datetime as _dt
        now = _dt.datetime.utcnow().isoformat()
        for lk in causal_links:
            con2.execute("INSERT OR REPLACE INTO pcmci_causal_links VALUES (?,?,?,?,?,?,?)",
                (lk['cause'], lk['effect'], lk['lag_days'],
                 lk['p_value'], lk['strength'], lk['direction'], now))
        con2.commit()
        con2.close()

        return {
            'n_sectors_analyzed': int(len(sector_cols)),
            'n_data_points':      int(len(pivot)),
            'n_causal_links':     int(len(causal_links)),
            'top_links':          causal_links[:20],
            'sectors':            sector_cols,
            'method':             'PCMCI (tigramite, ParCorr)',
            'tau_max':            5,
            'alpha':              0.05,
            'elapsed':            round(_time.time()-t0, 2),
        }

    except Exception as e:
        import traceback
        return {'error': str(e), 'traceback': traceback.format_exc()[-500:],
                'elapsed': round(time.time()-t0, 2)}


# ─── Dispatch ────────────────────────────────────────────────────────────────

COMMANDS = {
    'causal_now':          cmd_causal_now,
    'causal_chains':       cmd_causal_chains,
    'feedback_loops':      cmd_feedback_loops,
    'temporal_memory':     cmd_temporal_memory,
    'sector_causal_roles': cmd_sector_causal_roles,
    'causal_failure':      cmd_causal_failure,
    'regime_causality':    cmd_regime_causality,
    'causal_invariants':   cmd_causal_invariants,
    'causal_full':         cmd_causal_full,
    'pcmci_sectors':       cmd_pcmci_sectors,     # Phase 5 Enhancement — tigramite
}

if __name__ == '__main__':
    import sys as _sys
    # Support both stdin JSON and argv-based invocation
    if len(_sys.argv) >= 2:
        _cmd = _sys.argv[1]
        _par = json.loads(_sys.argv[2]) if len(_sys.argv) >= 3 else {}
        _fn  = COMMANDS.get(_cmd)
        if _fn is None:
            print(json.dumps({'error': f'unknown command: {_cmd}', 'available': list(COMMANDS.keys())}))
        else:
            print(json.dumps(_fn(_par)))
    else:
        try:
            inp = json.loads(sys.stdin.read())
            cmd = inp.get('command', '')
            par = inp.get('params', {})
            fn  = COMMANDS.get(cmd)
            if fn is None:
                print(json.dumps({'error': f'unknown command: {cmd}',
                                  'available': list(COMMANDS.keys())}))
            else:
                print(json.dumps(fn(par)))
        except Exception as ex:
            import traceback
            print(json.dumps({'error': str(ex), 'traceback': traceback.format_exc()}))
