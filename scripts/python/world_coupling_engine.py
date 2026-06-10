#!/usr/bin/env python3
"""
EGX World–Market Coupling Engine  (Phase 8)
============================================
Discovers how external macroeconomic conditions reshape EGX market behaviour.

Commands:
  coupling_now      — live snapshot of all coupling dimensions
  fx_impact         — FX stress → sector sensitivity, panic amplification
  macro_regimes     — detect macro regime + behavioural modifications
  liquidity_cycle   — liquidity environment → propagation / energy effects
  sector_coupling   — sector-specific macro coupling maps
  shock_memory      — external shock decay and memory half-life
  contagion_scan    — cross-sector synchronisation / imported stress
  coupling_stability— are macro–market relationships stable or breaking?
  adaptive_world    — evolution of coupling strengths over time
  coupling_full     — synthesised world-market intelligence report
"""
import sys, json, time, statistics, math
from pathlib import Path
from collections import defaultdict, Counter

DB_PATH           = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')
COUPLING_LOG_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'world_coupling_log.json')

COMMANDS = {
    'coupling_now', 'fx_impact', 'macro_regimes', 'liquidity_cycle',
    'sector_coupling', 'shock_memory', 'contagion_scan',
    'coupling_stability', 'adaptive_world', 'coupling_full',
}

# ── Macro group membership (substring match, lowercase) ─────────────────────
FX_KEYWORDS        = ['banking', 'bank', 'finance', 'financ', 'investment', 'insurance']
RATE_KEYWORDS      = ['real estate', 'realestate', 'construction', 'housing', 'property', 'mortgage']
INFLATION_KEYWORDS = ['material', 'mining', 'chemical', 'cement', 'basic resource', 'steel', 'metal']
CONSUMER_KEYWORDS  = ['consumer', 'food', 'retail', 'personal care', 'beverages', 'household']
GLOBAL_KEYWORDS    = ['telecom', 'telecommunication', 'technology', 'healthcare', 'pharma', 'medical']
COMMODITY_KEYWORDS = ['oil', 'energy', 'utilities', 'gas', 'petroleum', 'power']
DOMESTIC_KEYWORDS  = ['transport', 'logistic', 'industrial', 'manufactur', 'textile']


def macro_group(sector):
    s = (sector or '').lower()
    if any(k in s for k in FX_KEYWORDS):        return 'FX_SENSITIVE'
    if any(k in s for k in RATE_KEYWORDS):       return 'RATE_SENSITIVE'
    if any(k in s for k in INFLATION_KEYWORDS):  return 'INFLATION_PROXY'
    if any(k in s for k in CONSUMER_KEYWORDS):   return 'CONSUMER'
    if any(k in s for k in GLOBAL_KEYWORDS):     return 'GLOBAL'
    if any(k in s for k in COMMODITY_KEYWORDS):  return 'COMMODITY'
    if any(k in s for k in DOMESTIC_KEYWORDS):   return 'DOMESTIC'
    return 'OTHER'


# ── Utilities ────────────────────────────────────────────────────────────────

def safe(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def pearson(xs, ys):
    """Safe Pearson correlation; returns 0 on degenerate input."""
    n = min(len(xs), len(ys))
    if n < 5:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(
        sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)
    )
    return num / den if den > 1e-12 else 0.0


def trend_direction(vals):
    """'RISING' | 'FALLING' | 'STABLE' from a series."""
    if len(vals) < 4:
        return 'INSUFFICIENT'
    half = len(vals) // 2
    a = sum(vals[:half]) / half
    b = sum(vals[half:]) / (len(vals) - half)
    delta = b - a
    if delta > 0.08:
        return 'RISING'
    if delta < -0.08:
        return 'FALLING'
    return 'STABLE'


# ── Data loading ─────────────────────────────────────────────────────────────

def load_data(db, days=180, max_per_sym=120):
    cutoff = int(time.time()) - days * 86400
    rows = db.execute("""
        SELECT h.symbol, h.bar_time,
               h.open, h.high, h.low, h.close, h.volume,
               u.sector
        FROM ohlcv_history h
        JOIN stock_universe u ON h.symbol = u.symbol
        WHERE h.bar_time >= ? AND h.close > 0
        ORDER BY h.symbol, h.bar_time
    """, [cutoff]).fetchall()

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r['symbol']].append({
            'symbol':  r['symbol'],
            'bar_time': r['bar_time'],
            'open':    safe(r['open']),
            'high':    safe(r['high']),
            'low':     safe(r['low']),
            'close':   safe(r['close']),
            'volume':  r['volume'] or 0,
            'sector':  r['sector'] or 'Unknown',
            'macro_group': macro_group(r['sector'] or ''),
        })

    for sym in list(by_sym):
        bars = by_sym[sym]
        if len(bars) > max_per_sym:
            by_sym[sym] = bars[-max_per_sym:]

    return by_sym


def enrich(data):
    """Add ret, vol_ratio, range_pct to every bar."""
    for sym, bars in data.items():
        for i, b in enumerate(bars):
            b['ret'] = (
                (b['close'] - bars[i - 1]['close']) / bars[i - 1]['close']
                if i > 0 and bars[i - 1]['close'] > 0 else 0.0
            )
        for i, b in enumerate(bars):
            vols = [bars[j]['volume'] for j in range(max(0, i - 20), i + 1)
                    if bars[j]['volume'] > 0]
            avg = (statistics.mean(vols[:-1]) if len(vols) > 1
                   else (vols[0] if vols else 1))
            b['vol_ratio'] = b['volume'] / avg if avg > 0 and b['volume'] > 0 else 0.0
        for b in bars:
            b['range_pct'] = (b['high'] - b['low']) / b['close'] if b['close'] > 0 else 0.0
    return data


# ── Daily market summary (cross-sectional aggregation) ──────────────────────

MACRO_GROUPS = ['FX_SENSITIVE', 'RATE_SENSITIVE', 'INFLATION_PROXY',
                'CONSUMER', 'GLOBAL', 'COMMODITY', 'DOMESTIC']


def build_daily(data, last_n=90):
    """
    Return list of day-dicts sorted by bar_time.
    Each dict: date, market_ret, vol_ratio, breadth, n_stocks,
               FX_SENSITIVE, RATE_SENSITIVE, … (None if no data for group).
    """
    ts_buckets = defaultdict(lambda: defaultdict(list))

    for sym, bars in data.items():
        for b in bars:
            ts = b['bar_time']
            ts_buckets[ts]['ALL'].append(b['ret'])
            ts_buckets[ts]['__vr__'].append(b['vol_ratio'])
            ts_buckets[ts]['__rng__'].append(b['range_pct'])
            ts_buckets[ts][b['macro_group']].append(b['ret'])

    sorted_ts = sorted(ts_buckets.keys())[-last_n:]
    result = []
    for ts in sorted_ts:
        dd = ts_buckets[ts]
        all_rets = dd['ALL']
        d = {
            'date':       ts,
            'market_ret': statistics.mean(all_rets) if all_rets else 0.0,
            'vol_ratio':  statistics.mean(dd['__vr__']) if dd['__vr__'] else 1.0,
            'range_pct':  statistics.mean(dd['__rng__']) if dd['__rng__'] else 0.0,
            'n_stocks':   len(all_rets),
            'breadth':    (sum(1 for r in all_rets if r > 0) / len(all_rets)
                          if all_rets else 0.5),
        }
        for grp in MACRO_GROUPS:
            rets = dd[grp]
            d[grp] = statistics.mean(rets) if rets else None
        result.append(d)

    return result


# ── Coupling dimension calculators ──────────────────────────────────────────

def _fx_stress(daily):
    """
    FX stress = how much FX_SENSITIVE sector underperforms the market.
    Banking underperforms → EGP / capital-flight pressure.
    """
    diffs = []
    for d in daily:
        if d['FX_SENSITIVE'] is None:
            continue
        diffs.append(d['FX_SENSITIVE'] - d['market_ret'])

    if not diffs:
        return 0.0, 'UNKNOWN', []

    recent = diffs[-15:] if len(diffs) >= 15 else diffs
    hist   = diffs[:-15] if len(diffs) > 15 else diffs

    r_avg = statistics.mean(recent)
    h_avg = statistics.mean(hist) if hist else 0.0

    # Negative relative performance of banks = FX stress
    raw_stress = max(0.0, -(r_avg - h_avg) * 40)
    score = min(1.0, raw_stress)

    state = ('ACUTE'    if score > 0.6 else
             'ELEVATED' if score > 0.3 else
             'MILD'     if score > 0.1 else 'STABLE')

    return score, state, recent


def _liquidity(daily):
    recent = daily[-15:] if len(daily) >= 15 else daily
    if not recent:
        return 0.5, 'NORMAL', 1.0, 0.5

    avg_vr      = statistics.mean([d['vol_ratio'] for d in recent])
    avg_breadth = statistics.mean([d['breadth']   for d in recent])

    score = (min(avg_vr, 2.0) / 2.0) * 0.5 + avg_breadth * 0.5
    state = ('ABUNDANT' if score > 0.70 else
             'NORMAL'   if score > 0.50 else
             'TIGHT'    if score > 0.30 else 'CRISIS')

    return score, state, avg_vr, avg_breadth


def _inflation(daily):
    diffs = []
    for d in daily:
        if d['INFLATION_PROXY'] is None or d['CONSUMER'] is None:
            continue
        diffs.append(d['INFLATION_PROXY'] - d['CONSUMER'])

    if not diffs:
        return 0.0, 'UNKNOWN', 0.0

    raw = statistics.mean(diffs[-15:] if len(diffs) >= 15 else diffs)

    state = ('HIGH'      if raw > 0.005  else
             'ELEVATED'  if raw > 0.002  else
             'DEFLATION' if raw < -0.003 else 'STABLE')

    return round(max(0.0, abs(raw) * 100), 4), state, raw


def _policy(daily):
    diffs = []
    for d in daily:
        if d['RATE_SENSITIVE'] is None:
            continue
        diffs.append(d['RATE_SENSITIVE'] - d['market_ret'])

    if not diffs:
        return 0.0, 'NEUTRAL'

    raw = statistics.mean(diffs[-15:] if len(diffs) >= 15 else diffs)

    state = ('TIGHTENING_FEAR' if raw < -0.004 else
             'MILD_TIGHTENING' if raw < -0.002 else
             'EASING'          if raw >  0.002 else 'NEUTRAL')

    return round(abs(raw) * 100, 4), state


def _contagion(daily):
    recent = daily[-20:] if len(daily) >= 20 else daily
    series = defaultdict(list)
    for d in recent:
        for grp in MACRO_GROUPS:
            if d[grp] is not None:
                series[grp].append(d[grp])

    avail = [g for g in MACRO_GROUPS if len(series[g]) >= 8]
    if len(avail) < 2:
        return 0.0, 'UNKNOWN', []

    cors = []
    for i in range(len(avail)):
        for j in range(i + 1, len(avail)):
            gi, gj = avail[i], avail[j]
            c = pearson(series[gi], series[gj])
            cors.append({'pair': f'{gi}↔{gj}', 'cor': round(c, 4)})

    avg_cor = statistics.mean(abs(c['cor']) for c in cors) if cors else 0.0
    state = ('CRISIS_SYNC'    if avg_cor > 0.70 else
             'HIGH_CONTAGION' if avg_cor > 0.50 else
             'MODERATE'       if avg_cor > 0.30 else 'INDEPENDENT')

    return round(avg_cor, 4), state, cors


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_coupling_now(db, data, daily):
    """Current world-market coupling snapshot across all dimensions."""
    fx_s,  fx_st,  _    = _fx_stress(daily)
    lq_s,  lq_st, vr, br = _liquidity(daily)
    in_s,  in_st,  _    = _inflation(daily)
    po_s,  po_st        = _policy(daily)
    cn_s,  cn_st,  _    = _contagion(daily)

    # Market regime from recent returns
    rets_20 = [d['market_ret'] for d in daily[-20:]]
    avg_r   = statistics.mean(rets_20) if rets_20 else 0.0
    mkt_regime = 'BULL' if avg_r > 0.001 else ('BEAR' if avg_r < -0.001 else 'NEUTRAL')

    # Coupling intensity: how much external forces are elevated
    forces = {
        'FX_STRESS':           fx_s,
        'LIQUIDITY_TIGHTNESS': 1.0 - lq_s,
        'CONTAGION':           cn_s,
        'INFLATION':           min(1.0, in_s / 5.0),
        'POLICY':              min(1.0, po_s / 5.0),
    }
    coupling_intensity = statistics.mean(forces.values())
    dominant_force     = max(forces, key=forces.get)

    # Persist to log
    entry = {
        'date': time.strftime('%Y-%m-%d'),
        'regime': mkt_regime,
        'coupling_intensity': round(coupling_intensity, 4),
        'dominant_force': dominant_force,
        'fx_stress': round(fx_s, 4),
        'liquidity': round(lq_s, 4),
        'contagion': round(cn_s, 4),
    }
    try:
        p = Path(COUPLING_LOG_PATH)
        log = json.loads(p.read_text()) if p.exists() else []
        log.append(entry)
        if len(log) > 365:
            log = log[-365:]
        p.write_text(json.dumps(log, indent=2))
    except Exception:
        pass

    return {
        'regime': mkt_regime,
        'coupling_intensity': round(coupling_intensity, 4),
        'dominant_force': dominant_force,
        'forces': {k: round(v, 4) for k, v in forces.items()},
        'fx':        {'score': round(fx_s, 4), 'state': fx_st},
        'liquidity': {'score': round(lq_s, 4), 'state': lq_st,
                      'vol_ratio': round(vr, 3), 'breadth': round(br, 3)},
        'inflation': {'score': round(in_s, 4), 'state': in_st},
        'policy':    {'score': round(po_s, 4), 'state': po_st},
        'contagion': {'score': round(cn_s, 4), 'state': cn_st},
        'n_stocks': len(data),
        'n_days':   len(daily),
    }


def cmd_fx_impact(db, data, daily):
    """FX dynamics and how they reshape panic probability and propagation."""
    fx_s, fx_st, signal_hist = _fx_stress(daily)

    # FX-sensitive sector breakdown (10-day performance vs market)
    mkt_10d = statistics.mean([d['market_ret'] for d in daily[-10:]]) if len(daily) >= 10 else 0.0

    sector_map = defaultdict(lambda: {'rets': [], 'n': 0})
    for sym, bars in data.items():
        if len(bars) < 10:
            continue
        if bars[0]['macro_group'] != 'FX_SENSITIVE':
            continue
        sect = bars[0]['sector']
        recent_ret = statistics.mean([b['ret'] for b in bars[-10:]])
        sector_map[sect]['rets'].append(recent_ret)
        sector_map[sect]['n'] += 1

    sector_impacts = []
    for sect, info in sector_map.items():
        avg = statistics.mean(info['rets'])
        rel = avg - mkt_10d
        sector_impacts.append({
            'sector':      sect,
            'avg_10d_ret': round(avg, 5),
            'vs_market':   round(rel, 5),
            'n_symbols':   info['n'],
            'direction':   ('OUTPERFORM'  if rel >  0.001 else
                            'UNDERPERFORM' if rel < -0.001 else 'NEUTRAL'),
        })
    sector_impacts.sort(key=lambda x: x['vs_market'])

    # Behavioural modifiers driven by FX stress
    panic_amp   = round(1.0 + fx_s * 1.5, 3)   # 1.0–2.5×
    prop_speed  = round(1.0 + fx_s * 0.8, 3)   # faster propagation
    energy_leak = round(1.0 + fx_s * 0.6, 3)   # faster energy dissipation
    causal_lag  = -1 if fx_s > 0.5 else 0       # compressed causal lag under acute stress

    return {
        'fx_stress_score': round(fx_s, 4),
        'fx_state':        fx_st,
        'behavioural_modifiers': {
            'panic_amplification':        panic_amp,
            'propagation_speed_modifier': prop_speed,
            'energy_leak_rate':           energy_leak,
            'causal_lag_shift_bars':      causal_lag,
        },
        'sector_impacts':  sector_impacts,
        'market_10d_ret':  round(mkt_10d, 5),
        'signal_history':  [round(s, 5) for s in signal_hist[-20:]],
    }


def cmd_macro_regimes(db, data, daily):
    """Detect current macro regime and its effect on all market dimensions."""
    fx_s,  fx_st, _     = _fx_stress(daily)
    lq_s,  lq_st, vr, br = _liquidity(daily)
    in_s,  in_st, _     = _inflation(daily)
    po_s,  po_st        = _policy(daily)
    cn_s,  cn_st, _     = _contagion(daily)

    # Macro regime classification
    if fx_s > 0.5 or cn_s > 0.60:
        macro_regime = 'EXTERNAL_SHOCK'
    elif lq_s < 0.30:
        macro_regime = 'LIQUIDITY_CRISIS'
    elif po_st in ('TIGHTENING_FEAR', 'MILD_TIGHTENING') and lq_s < 0.45:
        macro_regime = 'POLICY_TIGHTENING'
    elif in_st in ('HIGH', 'ELEVATED'):
        macro_regime = 'INFLATION_PRESSURE'
    elif lq_s > 0.65 and fx_s < 0.20:
        macro_regime = 'EASY_MONEY'
    else:
        macro_regime = 'STABLE'

    EFFECTS = {
        'EXTERNAL_SHOCK':     {'panic_prob_mult': 2.0, 'prop_speed': 1.8,
                               'causal_lag_shift': -1, 'energy_persist': 0.6,
                               'instability_threshold': 0.40},
        'LIQUIDITY_CRISIS':   {'panic_prob_mult': 1.8, 'prop_speed': 2.0,
                               'causal_lag_shift':  0, 'energy_persist': 0.4,
                               'instability_threshold': 0.30},
        'POLICY_TIGHTENING':  {'panic_prob_mult': 1.3, 'prop_speed': 1.2,
                               'causal_lag_shift': +1, 'energy_persist': 0.7,
                               'instability_threshold': 0.55},
        'INFLATION_PRESSURE': {'panic_prob_mult': 1.2, 'prop_speed': 1.1,
                               'causal_lag_shift':  0, 'energy_persist': 0.8,
                               'instability_threshold': 0.60},
        'EASY_MONEY':         {'panic_prob_mult': 0.6, 'prop_speed': 0.8,
                               'causal_lag_shift': +1, 'energy_persist': 1.3,
                               'instability_threshold': 0.75},
        'STABLE':             {'panic_prob_mult': 1.0, 'prop_speed': 1.0,
                               'causal_lag_shift':  0, 'energy_persist': 1.0,
                               'instability_threshold': 0.60},
    }
    effects = EFFECTS.get(macro_regime, EFFECTS['STABLE'])

    # Rolling 60-day regime history (lightweight proxy)
    regime_hist = []
    for d in daily[-60:]:
        if d['vol_ratio'] > 2.0 and d['breadth'] < 0.30:
            regime_hist.append('EXTERNAL_SHOCK')
        elif d['vol_ratio'] < 0.40:
            regime_hist.append('LIQUIDITY_CRISIS')
        elif d['breadth'] > 0.65 and d['vol_ratio'] > 1.2:
            regime_hist.append('EASY_MONEY')
        else:
            regime_hist.append('STABLE')

    return {
        'current_macro_regime': macro_regime,
        'behavioural_effects':  effects,
        'supporting_signals': {
            'fx_stress':  {'score': round(fx_s, 4), 'state': fx_st},
            'liquidity':  {'score': round(lq_s, 4), 'state': lq_st},
            'inflation':  {'score': round(in_s, 4), 'state': in_st},
            'policy':     {'score': round(po_s, 4), 'state': po_st},
            'contagion':  {'score': round(cn_s, 4), 'state': cn_st},
        },
        'regime_history_60d': dict(Counter(regime_hist)),
    }


def cmd_liquidity_cycle(db, data, daily):
    """Liquidity environment and how it reshapes market mechanics."""
    lq_s, lq_st, avg_vr, avg_br = _liquidity(daily)

    recent = daily[-20:] if len(daily) >= 20 else daily
    vr_series = [d['vol_ratio'] for d in recent]
    br_series = [d['breadth']   for d in recent]

    # Trend
    if len(vr_series) >= 10:
        first  = statistics.mean(vr_series[:len(vr_series) // 2])
        second = statistics.mean(vr_series[len(vr_series) // 2:])
        liq_trend = ('IMPROVING'    if second > first * 1.10 else
                     'DETERIORATING' if second < first * 0.90 else 'STABLE')
    else:
        liq_trend = 'INSUFFICIENT_DATA'

    # Behavioural effects of current liquidity
    prop_speed_mod     = round(2.0 - lq_s, 3)          # tight = faster panic spread
    recovery_lag_shift = round((1.0 - lq_s) * 3.0)     # extra days for recovery
    energy_drain_rate  = round(1.0 + (1.0 - lq_s) * 0.5, 3)
    panic_prob_mod     = round(2.0 - lq_s, 3)

    # Day-by-day series
    series_20d = []
    for d in recent:
        sc = (min(d['vol_ratio'], 2.0) / 2.0) * 0.5 + d['breadth'] * 0.5
        st = ('ABUNDANT' if sc > 0.70 else
              'NORMAL'   if sc > 0.50 else
              'TIGHT'    if sc > 0.30 else 'CRISIS')
        series_20d.append({
            'vol_ratio': round(d['vol_ratio'], 3),
            'breadth':   round(d['breadth'],   3),
            'state':     st,
        })

    return {
        'current_state':    lq_st,
        'liquidity_score':  round(lq_s, 4),
        'avg_vol_ratio':    round(avg_vr, 3),
        'avg_breadth':      round(avg_br, 3),
        'trend':            liq_trend,
        'behavioural_effects': {
            'propagation_speed_modifier': prop_speed_mod,
            'recovery_lag_shift_days':    int(recovery_lag_shift),
            'energy_drain_rate':          energy_drain_rate,
            'panic_probability_modifier': panic_prob_mod,
        },
        'series_20d': series_20d,
    }


def cmd_sector_coupling(db, data, daily):
    """Sector-specific macro coupling maps — beta, sensitivity, direction."""
    mkt_rets = [d['market_ret'] for d in daily[-40:]] if len(daily) >= 40 else [d['market_ret'] for d in daily]
    mkt_10d  = statistics.mean([d['market_ret'] for d in daily[-10:]]) if len(daily) >= 10 else 0.0

    # Aggregate sector daily returns using the pre-built daily cross-section
    # For each sector, collect returns across all symbols then bucket by date
    sector_ts = defaultdict(lambda: {'by_date': defaultdict(list), 'n': 0, 'group': 'OTHER'})

    for sym, bars in data.items():
        sect = bars[0]['sector']
        grp  = bars[0]['macro_group']
        sector_ts[sect]['group'] = grp
        sector_ts[sect]['n'] += 1
        for b in bars[-40:]:
            sector_ts[sect]['by_date'][b['bar_time']].append(b['ret'])

    # Sorted common dates
    all_dates = sorted(set(ts for d in daily[-40:] for ts in [d['date']]))

    result = []
    for sect, info in sector_ts.items():
        sect_rets = []
        for ts in all_dates:
            day_rets = info['by_date'].get(ts, [])
            if day_rets:
                sect_rets.append(statistics.mean(day_rets))

        if len(sect_rets) < 8:
            continue

        beta = pearson(mkt_rets[-len(sect_rets):], sect_rets)
        flat_rets = []
        for ts in all_dates[-10:]:
            flat_rets.extend(info['by_date'].get(ts, []))
        avg_10 = statistics.mean(flat_rets) if flat_rets else 0.0

        sens = ('HIGH'   if abs(beta) > 0.70 else
                'MEDIUM' if abs(beta) > 0.40 else 'LOW')

        result.append({
            'sector':       sect,
            'macro_group':  info['group'],
            'n_symbols':    info['n'],
            'market_beta':  round(beta, 3),
            'sensitivity':  sens,
            'avg_ret_10d':  round(avg_10, 5),
            'vs_market':    round(avg_10 - mkt_10d, 5),
        })

    result.sort(key=lambda x: abs(x['market_beta']), reverse=True)

    # Group-level summary
    group_betas = defaultdict(list)
    for s in result:
        group_betas[s['macro_group']].append(s['market_beta'])
    group_coupling = {g: round(statistics.mean(bs), 3) for g, bs in group_betas.items() if bs}

    return {
        'sector_map':      result[:20],
        'group_coupling':  group_coupling,
        'market_10d_ret':  round(mkt_10d, 5),
        'n_sectors':       len(result),
    }


def cmd_shock_memory(db, data, daily):
    """Measure how long external shock effects persist in market behaviour."""
    # Shock threshold: market down > 1.5% in a single day
    shock_idxs = [i for i, d in enumerate(daily) if d['market_ret'] < -0.015]

    if not shock_idxs:
        return {
            'n_shocks': 0,
            'message': 'No significant shock events (< -1.5%) in analysis window',
            'half_life_days': None,
        }

    # Post-shock average response at each lag
    post_shock = defaultdict(list)
    for idx in shock_idxs:
        for lag in range(1, 16):
            if idx + lag < len(daily):
                post_shock[lag].append(daily[idx + lag]['market_ret'])

    avg_response = {lag: statistics.mean(vals)
                    for lag, vals in post_shock.items() if vals}

    shock_rets  = [daily[i]['market_ret'] for i in shock_idxs]
    avg_shock   = statistics.mean(shock_rets)
    worst_shock = min(shock_rets)
    baseline    = statistics.mean(d['market_ret'] for d in daily)

    # Half-life: lag at which |response| < 50% of |shock|
    half_life = None
    for lag in range(1, 16):
        if lag in avg_response:
            if abs(avg_response[lag] - baseline) < abs(avg_shock) * 0.5:
                half_life = lag
                break

    n_recent = sum(1 for i in shock_idxs if i >= len(daily) - 30)
    n_hist   = len(shock_idxs) - n_recent

    shock_trend = ('INCREASING' if n_recent > n_hist / 3.0 else
                   'DECREASING' if n_recent < n_hist / 6.0 else 'STABLE')

    return {
        'n_shocks':               len(shock_idxs),
        'avg_shock_ret':          round(avg_shock,   5),
        'worst_shock_ret':        round(worst_shock, 5),
        'half_life_days':         half_life,
        'post_shock_response':    {str(k): round(v, 5)
                                   for k, v in sorted(avg_response.items())},
        'recent_30d_shocks':      n_recent,
        'historical_shocks':      n_hist,
        'shock_frequency_trend':  shock_trend,
    }


def cmd_contagion_scan(db, data, daily):
    """Cross-sector synchronisation and imported stress detection."""
    cn_s, cn_st, cors = _contagion(daily)

    cors_sorted = sorted(cors, key=lambda x: abs(x['cor']), reverse=True)

    # Rolling contagion series (10-day windows, step 5)
    con_series = []
    window = 10
    for i in range(window, len(daily), 5):
        wd = daily[max(0, i - window):i]
        cs, _, _ = _contagion(wd)
        con_series.append(round(cs, 4))

    con_trend = trend_direction(con_series)

    # Synchronized panic days: >80% of macro groups fell together
    sync_panics = []
    for d in daily[-30:]:
        present = [g for g in MACRO_GROUPS if d[g] is not None]
        falling = [g for g in present    if d[g] < -0.005]
        if present and len(falling) / len(present) > 0.80:
            sync_panics.append({
                'groups_falling': len(falling),
                'total_groups':   len(present),
                'market_ret':     round(d['market_ret'], 5),
            })

    return {
        'contagion_score':         round(cn_s, 4),
        'contagion_state':         cn_st,
        'top_correlations':        cors_sorted[:6],
        'contagion_trend':         con_trend,
        'contagion_series':        con_series[-12:],
        'synchronized_panics_30d': len(sync_panics),
        'sync_panic_details':      sync_panics[-3:],
    }


def cmd_coupling_stability(db, data, daily):
    """Are macro–market couplings stable or undergoing structural shifts?"""
    window = 20

    def rolling_corr(grp_key, last_n=60):
        pairs = [(d['market_ret'], d[grp_key])
                 for d in daily[-last_n:]
                 if d.get(grp_key) is not None]
        if len(pairs) < window:
            return []
        results = []
        for i in range(window, len(pairs)):
            sub = pairs[i - window:i]
            xs = [p[0] for p in sub]
            ys = [p[1] for p in sub]
            results.append(pearson(xs, ys))
        return results

    fx_cor   = rolling_corr('FX_SENSITIVE')
    rate_cor = rolling_corr('RATE_SENSITIVE')
    infl_cor = rolling_corr('INFLATION_PROXY')
    cons_cor = rolling_corr('CONSUMER')

    def detect_break(cors, thr=0.35):
        if len(cors) < 4:
            return False, 0.0
        recent = statistics.mean(cors[-4:])
        hist   = statistics.mean(cors[:-4]) if len(cors) > 4 else cors[0]
        delta  = recent - hist
        return abs(delta) > thr, round(delta, 3)

    breaks = {
        'fx_coupling':        detect_break(fx_cor),
        'rate_coupling':      detect_break(rate_cor),
        'inflation_coupling': detect_break(infl_cor),
        'consumer_coupling':  detect_break(cons_cor),
    }

    n_breaks     = sum(1 for b, _ in breaks.values() if b)
    stability    = round(1.0 - n_breaks * 0.25, 3)
    stab_state   = ('STABLE'     if stability > 0.80 else
                    'SHIFTING'   if stability > 0.50 else 'DECOUPLING')

    def fmt_break(key, cors, b_result):
        brk, delta = b_result
        return {
            'break_detected': brk,
            'delta':          delta,
            'recent_cor':     round(cors[-1], 3) if cors else None,
            'series':         [round(x, 3) for x in cors[-8:]],
        }

    return {
        'stability_score': stability,
        'stability_state': stab_state,
        'n_structural_breaks': n_breaks,
        'couplings': {
            'fx_coupling':        fmt_break('fx_coupling',        fx_cor,   breaks['fx_coupling']),
            'rate_coupling':      fmt_break('rate_coupling',      rate_cor, breaks['rate_coupling']),
            'inflation_coupling': fmt_break('inflation_coupling', infl_cor, breaks['inflation_coupling']),
            'consumer_coupling':  fmt_break('consumer_coupling',  cons_cor, breaks['consumer_coupling']),
        },
    }


def cmd_adaptive_world(db, data, daily):
    """How macro coupling strengths evolve over time — adaptation or stasis?"""
    window, step = 15, 5
    evolution = []

    for i in range(window, len(daily), step):
        wd = daily[max(0, i - window):i]
        if len(wd) < 5:
            continue
        fx_s,  _, _       = _fx_stress(wd)
        lq_s,  _, _, _    = _liquidity(wd)
        in_s,  _, _       = _inflation(wd)
        cn_s,  _, _       = _contagion(wd)
        avg_r = statistics.mean(d['market_ret'] for d in wd)

        evolution.append({
            'period_end': wd[-1]['date'],
            'fx_stress':  round(fx_s, 4),
            'liquidity':  round(lq_s, 4),
            'inflation':  round(in_s, 3),
            'contagion':  round(cn_s, 4),
            'market_ret': round(avg_r, 5),
        })

    if not evolution:
        return {'error': 'Insufficient data for evolution analysis'}

    # Trends
    trends = {
        'fx_stress':  trend_direction([e['fx_stress']  for e in evolution]),
        'liquidity':  trend_direction([e['liquidity']  for e in evolution]),
        'inflation':  trend_direction([e['inflation']  for e in evolution]),
        'contagion':  trend_direction([e['contagion']  for e in evolution]),
    }

    # Which force dominated each recent period
    dom_hist = []
    for e in evolution[-8:]:
        f = {'FX': e['fx_stress'],
             'ILLIQUIDITY': 1.0 - e['liquidity'],
             'CONTAGION':   e['contagion']}
        dom_hist.append(max(f, key=f.get))

    adaptation_status = ('ADAPTING' if any(v != 'STABLE' for v in trends.values())
                         else 'STATIC')

    return {
        'n_periods':             len(evolution),
        'trends':                trends,
        'force_dominance_recent': dict(Counter(dom_hist)),
        'adaptation_status':     adaptation_status,
        'evolution_series':      evolution[-10:],
    }


def cmd_coupling_full(db, data, daily):
    """Full world-market coupling intelligence synthesis."""
    steps = [
        ('coupling_now',       cmd_coupling_now),
        ('fx_impact',          cmd_fx_impact),
        ('macro_regimes',      cmd_macro_regimes),
        ('liquidity_cycle',    cmd_liquidity_cycle),
        ('contagion_scan',     cmd_contagion_scan),
        ('coupling_stability', cmd_coupling_stability),
        ('adaptive_world',     cmd_adaptive_world),
        ('shock_memory',       cmd_shock_memory),
        ('sector_coupling',    cmd_sector_coupling),
    ]

    results = {}
    for key, fn in steps:
        try:
            results[key] = fn(db, data, daily)
            results[key]['__ok__'] = True
        except Exception as e:
            results[key] = {'error': str(e), '__ok__': False}

    # Synthesis
    cn = results.get('coupling_now', {})
    mr = results.get('macro_regimes', {})
    sm = results.get('shock_memory', {})
    st = results.get('coupling_stability', {})
    aw = results.get('adaptive_world', {})

    risks = []
    ops   = []
    fx_st = cn.get('fx', {}).get('state', 'STABLE')
    lq_st = cn.get('liquidity', {}).get('state', 'NORMAL')
    cn_st = cn.get('contagion', {}).get('state', 'INDEPENDENT')

    if fx_st in ('ACUTE', 'ELEVATED'):    risks.append('FX_STRESS_ACTIVE')
    if lq_st in ('TIGHT', 'CRISIS'):      risks.append('LIQUIDITY_TIGHTENING')
    if cn_st in ('HIGH_CONTAGION', 'CRISIS_SYNC'): risks.append('CONTAGION_RISK')
    if sm.get('shock_frequency_trend') == 'INCREASING': risks.append('RISING_SHOCK_FREQUENCY')
    if st.get('n_structural_breaks', 0) >= 2:  risks.append('STRUCTURAL_COUPLING_SHIFT')
    if not risks: risks.append('NO_SIGNIFICANT_MACRO_RISK')

    if lq_st == 'ABUNDANT': ops.append('AMPLE_LIQUIDITY_SUPPORTS_ENTRIES')
    if fx_st == 'STABLE':   ops.append('FX_STABLE_REDUCES_NOISE')
    if aw.get('adaptation_status') == 'ADAPTING': ops.append('MACRO_RELATIONSHIPS_EVOLVING')
    if not ops: ops.append('STANDARD_ENVIRONMENT')

    results['synthesis'] = {
        'macro_regime':        mr.get('current_macro_regime', 'UNKNOWN'),
        'coupling_intensity':  cn.get('coupling_intensity', 0),
        'dominant_force':      cn.get('dominant_force', 'UNKNOWN'),
        'market_regime':       cn.get('regime', 'UNKNOWN'),
        'key_risks':           risks,
        'opportunities':       ops,
        'adaptation_status':   aw.get('adaptation_status', 'UNKNOWN'),
    }

    return results


# ── Dispatch ─────────────────────────────────────────────────────────────────

DISPATCH = {
    'coupling_now':       cmd_coupling_now,
    'fx_impact':          cmd_fx_impact,
    'macro_regimes':      cmd_macro_regimes,
    'liquidity_cycle':    cmd_liquidity_cycle,
    'sector_coupling':    cmd_sector_coupling,
    'shock_memory':       cmd_shock_memory,
    'contagion_scan':     cmd_contagion_scan,
    'coupling_stability': cmd_coupling_stability,
    'adaptive_world':     cmd_adaptive_world,
    'coupling_full':      cmd_coupling_full,
}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'coupling_now'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}',
                          'available': sorted(COMMANDS)}))
        sys.exit(1)

    try:
        params = json.loads(sys.stdin.read() or '{}')
    except Exception:
        params = {}

    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    con.row_factory = _sq.Row
    db = con.cursor()

    try:
        data  = load_data(db)
        data  = enrich(data)
        daily = build_daily(data)
        result = DISPATCH[cmd](db, data, daily)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()}))
    finally:
        con.close()


if __name__ == '__main__':
    main()
