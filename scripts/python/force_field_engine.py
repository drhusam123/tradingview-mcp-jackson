#!/usr/bin/env python3
"""
EGX Latent Market Force Field Engine — Phase 2
================================================
Models INTERACTING latent forces driving market behavior.

Not indicators. Not signals. Not states.
→ Coupled behavioral forces with physics-like dynamics.

The 9 Latent Forces:
  F1  BUYING_PRESSURE       demand-side absorption
  F2  SELLING_PRESSURE      supply-side force
  F3  EXHAUSTION_FORCE      distance from equilibrium × duration
  F4  PANIC_FORCE           velocity × volume surge (downward)
  F5  VOLATILITY_EXPANSION  ATR z-score above norm
  F6  LIQUIDITY_ABSORPTION  signed volume flow
  F7  MOMENTUM_INERTIA      directional persistence
  F8  INSTABILITY_INDEX     force-field divergence measure
  F9  MEAN_REVERSION_TENSION pull toward equilibrium

Commands:
  force_field_now     — Current 9-force field for all stocks (~2s)
  force_interactions  — Coupling matrix: which forces reinforce/cancel (~15s)
  force_evolution     — Decay, acceleration, half-life per force (~10s)
  market_memory       — Behavioral adaptation: does alpha decay? (~10s)
  failure_physics     — Why reversals fail: dominant blocking force (~8s)
  force_attractors    — Latent manifold: attractors + instability zones (~5s)
  force_field_full    — Complete force field report

مالك: Dr. Husam | مايو 2026
"""

import json, sys, os, math, statistics, datetime, collections, itertools
import sqlite3

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ── DB ────────────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/egx_trading.db')

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ═══════════════════════════════════════════════════════════════════════════════
# CORE FORCE COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

# Force names (canonical)
FORCES = [
    'BUYING_PRESSURE', 'SELLING_PRESSURE', 'EXHAUSTION_FORCE', 'PANIC_FORCE',
    'VOLATILITY_EXPANSION', 'LIQUIDITY_ABSORPTION', 'MOMENTUM_INERTIA',
    'INSTABILITY_INDEX', 'MEAN_REVERSION_TENSION'
]
F = {name: i for i, name in enumerate(FORCES)}  # name → index

# High-force threshold (forces above this are "active")
HIGH_THRESH = 0.55
LOW_THRESH  = 0.25

# Key force pairs for coupling analysis (semantically meaningful)
COUPLING_PAIRS = [
    ('EXHAUSTION_FORCE',    'PANIC_FORCE',           'Exhaustion amplifies Panic?'),
    ('EXHAUSTION_FORCE',    'VOLATILITY_EXPANSION',  'Exhaustion under volatility?'),
    ('PANIC_FORCE',         'LIQUIDITY_ABSORPTION',  'Panic drains liquidity?'),
    ('MOMENTUM_INERTIA',    'MEAN_REVERSION_TENSION','Momentum vs. Mean Reversion'),
    ('BUYING_PRESSURE',     'SELLING_PRESSURE',      'Opposing pressures (distribution)'),
    ('VOLATILITY_EXPANSION','LIQUIDITY_ABSORPTION',  'Vol expands as liquidity contracts?'),
    ('EXHAUSTION_FORCE',    'MOMENTUM_INERTIA',      'Exhaustion fights momentum'),
    ('PANIC_FORCE',         'MEAN_REVERSION_TENSION','Panic vs. recovery tension'),
    ('INSTABILITY_INDEX',   'LIQUIDITY_ABSORPTION',  'Instability × liquidity vacuum'),
    ('BUYING_PRESSURE',     'MOMENTUM_INERTIA',      'Reinforcing uptrend'),
    ('SELLING_PRESSURE',    'PANIC_FORCE',            'Amplifying downtrend'),
    ('VOLATILITY_EXPANSION','MEAN_REVERSION_TENSION','Vol × mean reversion = explosive move'),
]


def _rolling_mean_std(values, n):
    """Rolling mean and std of last n values (returns current values)."""
    if len(values) < 3:
        return 0.0, 1.0
    window = values[-n:] if len(values) >= n else values
    m = sum(window) / len(window)
    s = statistics.stdev(window) if len(window) > 1 else 1.0
    return m, max(s, 1e-6)


def compute_force_vector(
    close, prev_close, high, low,
    rsi, atr, atr_mean, atr_std,
    volume, avg_volume,
    mom5, duration_in_state, prev_rsi=50.0
):
    """
    Compute the full 9-force vector for a single bar.
    Returns list of 9 float values in [0..1] (signed for directional forces).

    Forces are CONTINUOUS, not binary — they represent intensity.
    """
    pct_chg  = (close - prev_close) / prev_close * 100 if prev_close else 0
    vol_ratio = volume / avg_volume if avg_volume > 0 else 1.0
    rsi_val   = rsi if rsi is not None else 50.0
    mom5_val  = mom5 if mom5 is not None else 0.0

    # ATR z-score (volatility state)
    atr_z = (atr - atr_mean) / atr_std if (atr and atr_std > 0) else 0.0

    # True Range for current bar
    if None not in (high, low, prev_close):
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_pct = tr / prev_close * 100 if prev_close > 0 else abs(pct_chg)
    else:
        tr_pct = abs(pct_chg)

    # Duration factor (force amplified by time spent in state)
    dur_factor = min(1.0, math.log1p(duration_in_state) / math.log1p(10))

    # ── F1: BUYING_PRESSURE [0..1] ────────────────────────────────────────
    # Demand absorption: RSI above neutral + positive price + volume
    rsi_above = max(0, (rsi_val - 50) / 50)
    pct_pos   = max(0, min(pct_chg / 3, 1.0))
    vol_norm  = min(vol_ratio / 2, 1.0)
    f1 = round(rsi_above * 0.4 + pct_pos * 0.35 + vol_norm * 0.25, 4)

    # ── F2: SELLING_PRESSURE [0..1] ───────────────────────────────────────
    rsi_below = max(0, (50 - rsi_val) / 50)
    pct_neg   = max(0, min(-pct_chg / 3, 1.0))
    f2 = round(rsi_below * 0.4 + pct_neg * 0.35 + vol_norm * 0.25, 4)

    # ── F3: EXHAUSTION_FORCE [0..1] ───────────────────────────────────────
    # Distance from RSI equilibrium (both overbought AND oversold = exhaustion)
    # Amplified by duration and by velocity change (slowing down = exhaustion)
    rsi_deviation  = abs(rsi_val - 50) / 50
    rsi_velocity   = abs(rsi_val - prev_rsi) / 30  # rate of RSI change
    exhaustion_raw = rsi_deviation * (1 + dur_factor * 0.5)
    # Exhaustion increases when price moved a lot but RSI is plateauing
    plateau_signal = max(0, tr_pct / 3 - rsi_velocity)
    f3 = round(min(1.0, exhaustion_raw * 0.7 + min(plateau_signal, 0.5) * 0.3), 4)

    # ── F4: PANIC_FORCE [0..1] ────────────────────────────────────────────
    # Sharp downward move with volume surge = panic selling
    panic_pct = max(0, -pct_chg / 5)   # 0 at flat, 1 at -5% drop
    panic_vol = min(max(vol_ratio - 1, 0) / 2, 1.0)  # 0 at normal vol, 1 at 3x
    f4 = round(min(1.0, panic_pct * 0.6 + panic_vol * 0.4), 4)

    # ── F5: VOLATILITY_EXPANSION [0..1] ───────────────────────────────────
    # ATR z-score above norm = expanding volatility regime
    vol_exp_raw = max(0, atr_z / 3)   # 0 at norm, 1 at +3 std
    vol_surge   = min(max(vol_ratio - 1.5, 0) / 1.5, 1.0)
    f5 = round(min(1.0, vol_exp_raw * 0.6 + vol_surge * 0.4), 4)

    # ── F6: LIQUIDITY_ABSORPTION [-1..1] ─────────────────────────────────
    # Positive = institutional buying absorption
    # Negative = selling absorption (distribution)
    direction = 1 if pct_chg >= 0 else -1
    absorption_strength = min(vol_ratio / 2, 1.0)
    f6 = round(direction * absorption_strength, 4)

    # ── F7: MOMENTUM_INERTIA [-1..1] ─────────────────────────────────────
    # Strong momentum = high inertia (resists reversal)
    # Positive = upward inertia, negative = downward
    mom_sign  = 1 if mom5_val >= 0 else -1
    mom_mag   = min(abs(mom5_val) / 10, 1.0)
    f7 = round(mom_sign * mom_mag * (1 + dur_factor * 0.3), 4)
    f7 = max(-1.0, min(1.0, f7))

    # ── F8: INSTABILITY_INDEX [0..1] ─────────────────────────────────────
    # Force-field divergence: when OPPOSING forces are both high → instability
    # High exhaustion + high volatility + conflicting signals
    opposing_pressure = min(f1, f2)  # both pressures active = tug-of-war
    force_conflict    = min(f3, f5)  # exhaustion under volatility
    atr_extreme       = min(abs(atr_z) / 2, 1.0)
    f8 = round(min(1.0, opposing_pressure * 0.35 + force_conflict * 0.35 + atr_extreme * 0.30), 4)

    # ── F9: MEAN_REVERSION_TENSION [-1..1] ───────────────────────────────
    # Positive = oversold (tension pulling up)
    # Negative = overbought (tension pulling down)
    rsi_z = (50 - rsi_val) / 50   # +1 when RSI=0 (deep oversold), -1 when RSI=100
    # Amplify with momentum opposing the current direction
    rsi_tension = rsi_z
    if rsi_z > 0.3 and mom5_val > 0:   # oversold but momentum recovering
        rsi_tension *= 1.3
    elif rsi_z < -0.3 and mom5_val < 0: # overbought but momentum weakening
        rsi_tension *= 1.3
    f9 = round(max(-1.0, min(1.0, rsi_tension)), 4)

    return [f1, f2, f3, f4, f5, f6, f7, f8, f9]


def _compute_stock_forces(bars):
    """
    Compute force vectors for ALL bars of a single stock.
    Returns list of (bar_time, force_vector) tuples.
    """
    n = len(bars)
    if n < 20:
        return []

    closes  = [b['close'] for b in bars]
    highs   = [b['high']  for b in bars]
    lows    = [b['low']   for b in bars]
    volumes = [b['volume'] for b in bars]
    times   = [b['bar_time'] for b in bars]

    # Precompute rolling ATR (14-bar) and its rolling mean/std (60-bar)
    atrs = []
    for i in range(n):
        if i < 14:
            atrs.append(None)
            continue
        trs = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1]))
               for j in range(i-13, i+1)]
        atrs.append(sum(trs)/14)

    # Rolling avg volume (20 bar)
    avg_vols = []
    for i in range(n):
        w = volumes[max(0, i-19):i+1]
        avg_vols.append(sum(w)/len(w))

    # RSI-14
    rsis = []
    for i in range(n):
        if i < 14:
            rsis.append(50.0)
            continue
        gains  = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1)]
        losses = [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1)]
        ag = sum(gains)/14; al = sum(losses)/14
        rsis.append(100 - 100/(1+ag/al) if al > 0 else 100.0)

    # Momentum 5
    moms = []
    for i in range(n):
        if i < 5:
            moms.append(0.0)
        else:
            moms.append((closes[i]-closes[i-5])/closes[i-5]*100 if closes[i-5] > 0 else 0)

    # ATR rolling stats (60-bar window)
    def _atr_stats(i):
        window = [a for a in atrs[max(0,i-59):i+1] if a is not None]
        if len(window) < 3:
            return 0, 1
        return sum(window)/len(window), statistics.stdev(window)

    # State (simplified) for duration tracking
    prev_state = 'NEUTRAL'
    duration   = 1
    results    = []

    for i in range(1, n):
        if atrs[i] is None:
            continue
        atr_mean, atr_std = _atr_stats(i)
        prev_rsi = rsis[i-1] if i > 0 else 50.0

        fv = compute_force_vector(
            close=closes[i], prev_close=closes[i-1],
            high=highs[i], low=lows[i],
            rsi=rsis[i], atr=atrs[i],
            atr_mean=atr_mean, atr_std=atr_std,
            volume=volumes[i], avg_volume=avg_vols[i],
            mom5=moms[i], duration_in_state=duration,
            prev_rsi=prev_rsi
        )

        # Simple state for duration tracking
        pct = (closes[i]-closes[i-1])/closes[i-1]*100 if closes[i-1] > 0 else 0
        state = 'PANIC' if pct < -3 else 'UP' if pct > 2 else 'NEUTRAL'
        if state == prev_state:
            duration += 1
        else:
            duration  = 1
            prev_state = state

        # Forward return (for outcome labeling when needed)
        fwd_idx = i + 3
        if fwd_idx < n and closes[i] > 0:
            fwd_ret = (closes[fwd_idx] - closes[i]) / closes[i] * 100
        else:
            fwd_ret = None

        results.append({
            'time': times[i],
            'forces': fv,
            'fwd_ret': fwd_ret,
            'rsi': rsis[i],
            'pct_chg': round((closes[i]-closes[i-1])/closes[i-1]*100 if closes[i-1] > 0 else 0, 3),
            'duration': duration,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: force_field_now
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_force_field_now(params):
    """
    Current 9-force field for all stocks from indicators_cache.
    Classifies each stock into a Force Field Archetype.
    Shows force coupling patterns active NOW.
    """
    con = get_db()
    stocks = con.execute("""
        SELECT ic.symbol, ic.rsi14, ic.vol_ratio_20, ic.momentum_5d, ic.momentum_10d,
               ic.adx14, ic.macd_hist, ic.cci20, ic.close_position, ic.price_vs_ath,
               ic.atr14, ic.bb_width,
               u.sector
        FROM indicators_cache ic
        LEFT JOIN stock_universe u ON ic.symbol = u.symbol
    """).fetchall()
    con.close()

    if not stocks:
        return {'success': False, 'error': 'No indicators_cache data'}

    # Compute forces for each stock (current bar only)
    stock_forces = []
    archetype_counts = collections.defaultdict(int)
    sector_forces_agg = collections.defaultdict(lambda: [[] for _ in FORCES])

    for s in stocks:
        sym     = s['symbol']
        rsi     = s['rsi14'] or 50.0
        vol_r   = s['vol_ratio_20'] or 1.0
        mom5    = s['momentum_5d'] or 0.0
        mom10   = s['momentum_10d'] or 0.0
        adx     = s['adx14'] or 0.0
        macd_h  = s['macd_hist'] or 0.0
        cci     = s['cci20'] or 0.0
        bb_w    = s['bb_width'] or 0.0
        atr     = s['atr14'] or 0.0
        closep  = s['close_position'] or 0.5
        price_ath = s['price_vs_ath'] or 0.5
        sector  = s['sector'] or 'Unknown'

        # Synthetic force computation from latest indicators
        pct_chg_est = mom5 / 5  # approximate daily %

        # F1: BUYING_PRESSURE
        rsi_above = max(0, (rsi - 50) / 50)
        pct_pos   = max(0, min(pct_chg_est / 3, 1.0))
        f1 = min(1.0, rsi_above * 0.45 + pct_pos * 0.3 + min(vol_r/3, 1.0) * 0.25)

        # F2: SELLING_PRESSURE
        rsi_below = max(0, (50 - rsi) / 50)
        pct_neg   = max(0, min(-pct_chg_est / 3, 1.0))
        f2 = min(1.0, rsi_below * 0.45 + pct_neg * 0.3 + min(vol_r/3, 1.0) * 0.25)

        # F3: EXHAUSTION_FORCE
        rsi_dev  = abs(rsi - 50) / 50
        macd_slow= min(abs(macd_h) / 0.3, 1.0) * (1 if abs(macd_h) < 0.05 else 0)
        f3 = min(1.0, rsi_dev * 0.7 + macd_slow * 0.3)

        # F4: PANIC_FORCE
        panic_pct = max(0, -pct_chg_est / 3)
        panic_vol = min(max(vol_r - 1.5, 0) / 1.5, 1.0)
        f4 = min(1.0, panic_pct * 0.6 + panic_vol * 0.4)

        # F5: VOLATILITY_EXPANSION
        # bb_width as vol proxy (no ATR_z available in current cache)
        f5 = min(1.0, min(bb_w / 0.15, 1.0) * 0.5 + min(max(vol_r-1.5,0)/1.5, 1.0) * 0.5)

        # F6: LIQUIDITY_ABSORPTION [-1..1]
        direction = 1 if pct_chg_est >= 0 else -1
        f6 = direction * min(vol_r / 2, 1.0)
        f6 = max(-1.0, min(1.0, f6))

        # F7: MOMENTUM_INERTIA [-1..1]
        mom_sign = 1 if mom10 >= 0 else -1
        mom_mag  = min(abs(mom10) / 15, 1.0)
        f7 = mom_sign * mom_mag
        f7 = max(-1.0, min(1.0, f7))

        # F8: INSTABILITY_INDEX
        opposing_p = min(f1, f2)
        conflict   = min(f3, f5)
        f8 = min(1.0, opposing_p * 0.4 + conflict * 0.6)

        # F9: MEAN_REVERSION_TENSION [-1..1]
        rsi_z = (50 - rsi) / 50
        cci_z = max(-1, min(1, -cci / 150))  # CCI reinforces
        f9 = max(-1.0, min(1.0, rsi_z * 0.65 + cci_z * 0.35))

        fv = [round(f,3) for f in [f1, f2, f3, f4, f5, f6, f7, f8, f9]]

        # Classify force field archetype
        archetype = _classify_force_archetype(fv, rsi)
        archetype_counts[archetype] += 1

        # Aggregate by sector
        for fi, fval in enumerate(fv):
            sector_forces_agg[sector][fi].append(fval)

        stock_forces.append({
            'symbol':    sym,
            'sector':    sector,
            'rsi':       round(rsi, 1),
            'forces':    dict(zip(FORCES, fv)),
            'archetype': archetype,
            'dominant':  _dominant_forces(fv),
            'coupling':  _active_couplings(fv),
        })

    # ── Market-level force summary ────────────────────────────────────────
    market_forces = {}
    all_force_vecs = [s['forces'] for s in stock_forces]
    for fn in FORCES:
        vals = [f[fn] for f in all_force_vecs]
        market_forces[fn] = {
            'mean': round(sum(vals)/len(vals), 3),
            'pct_high': round(sum(1 for v in vals if abs(v) > HIGH_THRESH)/len(vals)*100, 1),
            'pct_low':  round(sum(1 for v in vals if abs(v) < LOW_THRESH) /len(vals)*100, 1),
        }

    # ── Active market-level couplings ─────────────────────────────────────
    active_couplings = _market_coupling_summary(stock_forces)

    # ── Sector force profiles ─────────────────────────────────────────────
    sector_summary = {}
    for sec, force_lists in sector_forces_agg.items():
        sector_summary[sec] = {
            FORCES[fi]: round(sum(fl)/len(fl), 3) if fl else 0
            for fi, fl in enumerate(force_lists)
        }

    # ── Instability zones (stocks with high F8) ────────────────────────────
    instability_zone = sorted(
        [s for s in stock_forces if s['forces']['INSTABILITY_INDEX'] > 0.5],
        key=lambda x: -x['forces']['INSTABILITY_INDEX']
    )

    # ── Reversal candidates (high F9 + low F4) ────────────────────────────
    reversal_candidates = sorted(
        [s for s in stock_forces
         if s['forces']['MEAN_REVERSION_TENSION'] > 0.4
         and s['forces']['PANIC_FORCE'] < 0.3
         and s['forces']['SELLING_PRESSURE'] < 0.5],
        key=lambda x: -x['forces']['MEAN_REVERSION_TENSION']
    )

    # ── Force field health summary ─────────────────────────────────────────
    exh_mean   = market_forces['EXHAUSTION_FORCE']['mean']
    panic_mean = market_forces['PANIC_FORCE']['mean']
    inst_mean  = market_forces['INSTABILITY_INDEX']['mean']
    mrt_mean   = market_forces['MEAN_REVERSION_TENSION']['mean']
    field_state = _field_state_description(exh_mean, panic_mean, inst_mean, mrt_mean)

    return {
        'success': True,
        'n_stocks': len(stock_forces),
        'field_state': field_state,
        'market_forces': market_forces,
        'archetype_distribution': dict(sorted(archetype_counts.items(), key=lambda x: -x[1])),
        'active_couplings': active_couplings,
        'instability_zone': [s['symbol'] for s in instability_zone[:15]],
        'reversal_candidates': [
            {'symbol': s['symbol'], 'rsi': s['rsi'],
             'tension': s['forces']['MEAN_REVERSION_TENSION'],
             'archetype': s['archetype']}
            for s in reversal_candidates[:12]
        ],
        'sector_profiles': sector_summary,
        'stock_forces': sorted(stock_forces, key=lambda x: -x['forces']['MEAN_REVERSION_TENSION'])[:40],
    }


def _classify_force_archetype(fv, rsi):
    """Classify a 9-force vector into a behavioral archetype."""
    f1,f2,f3,f4,f5,f6,f7,f8,f9 = fv
    # Priority order: extreme states first
    if f4 > 0.6 and f2 > 0.5:
        return 'PANIC_SELLING'
    if f3 > 0.7 and f2 > 0.5 and f7 < 0:
        return 'EXHAUSTION_COLLAPSE'
    if f3 > 0.7 and f1 > 0.5 and f7 > 0:
        return 'OVERBOUGHT_EXHAUSTION'
    if f8 > 0.6:
        return 'INSTABILITY_ZONE'
    if f9 > 0.5 and f4 < 0.3:
        return 'REVERSAL_TENSION'
    if f9 > 0.3 and f5 < 0.3:
        return 'QUIET_RECOVERY'
    if f1 > 0.5 and f7 > 0.4:
        return 'MOMENTUM_SURGE'
    if f6 > 0.5 and f7 > 0.3:
        return 'INSTITUTIONAL_ACCUMULATION'
    if f2 > 0.5 and f5 > 0.4:
        return 'DISTRIBUTION_PRESSURE'
    if f7 > 0.5 and abs(f9) < 0.2:
        return 'MOMENTUM_INERTIA'
    if f5 > 0.4 and abs(f7) < 0.2:
        return 'VOLATILITY_TRAP'
    if abs(f6) < 0.2 and f5 < 0.2:
        return 'LIQUIDITY_VACUUM'
    return 'TRANSITIONAL_NEUTRAL'


def _dominant_forces(fv):
    """Return the 2 strongest forces by absolute value."""
    indexed = [(FORCES[i], abs(fv[i])) for i in range(9)]
    indexed.sort(key=lambda x: -x[1])
    return [f[0] for f in indexed[:2]]


def _active_couplings(fv):
    """List coupling pairs where BOTH forces are active (|f| > HIGH_THRESH)."""
    active = []
    for fi, fj, desc in COUPLING_PAIRS:
        vi = abs(fv[F[fi]])
        vj = abs(fv[F[fj]])
        if vi > HIGH_THRESH and vj > HIGH_THRESH:
            active.append(f"{fi}×{fj}")
    return active


def _market_coupling_summary(stock_forces):
    """Summarize which couplings are most common across all stocks."""
    coupling_counts = collections.defaultdict(int)
    for s in stock_forces:
        for c in s['coupling']:
            coupling_counts[c] += 1
    n = len(stock_forces) or 1
    return {c: round(cnt/n*100, 1) for c, cnt in
            sorted(coupling_counts.items(), key=lambda x: -x[1])[:8]}


def _field_state_description(exh, panic, inst, mrt):
    """Describe the overall market force field state."""
    if panic > 0.3:
        return f"PANIC_ACTIVE (panic={panic:.3f}) — قوة الذعر سائدة"
    if inst > 0.4:
        return f"INSTABILITY_ZONE (inst={inst:.3f}) — قوى متعارضة"
    if exh > 0.5:
        return f"EXHAUSTION_DOMINANT (exh={exh:.3f}) — السوق منهك"
    if mrt > 0.2:
        return f"RECOVERY_TENSION (mrt={mrt:.3f}) — شد نحو الانتعاش"
    if mrt < -0.2:
        return f"OVERBOUGHT_TENSION (mrt={mrt:.3f}) — شد للتصحيح"
    return f"BALANCED_FIELD (exh={exh:.3f}) — السوق في توازن"


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: force_interactions
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_force_interactions(params):
    """
    Coupling Matrix: which forces REINFORCE each other, which CANCEL?
    For each force pair: P(TR | both_high) vs P(TR | one_high) vs baseline.
    Reveals the hidden force physics of EGX.
    """
    symbol_data = _load_ohlcv()
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    HORIZON   = int(params.get('horizon', 3))
    TR_THRESH = float(params.get('tr_threshold', 2.0))

    # Collect force vectors + outcomes for all bars
    force_outcomes = []  # (force_vector, is_tr)

    for sym, bars in symbol_data.items():
        bar_forces = _compute_stock_forces(bars)
        for bf in bar_forces:
            if bf['fwd_ret'] is not None:
                is_tr = 1 if bf['fwd_ret'] >= TR_THRESH else 0
                force_outcomes.append((bf['forces'], is_tr))

    if len(force_outcomes) < 200:
        return {'success': False, 'error': f'Too few force observations: {len(force_outcomes)}'}

    n_total  = len(force_outcomes)
    baseline = sum(fo[1] for fo in force_outcomes) / n_total

    # ── Single-force P(TR) ────────────────────────────────────────────────
    single_force_ptr = {}
    for fi, fname in enumerate(FORCES):
        high_outcomes = [fo[1] for fo in force_outcomes if abs(fo[0][fi]) > HIGH_THRESH]
        low_outcomes  = [fo[1] for fo in force_outcomes if abs(fo[0][fi]) < LOW_THRESH]
        if len(high_outcomes) >= 10:
            single_force_ptr[fname] = {
                'high_p_tr':  round(sum(high_outcomes)/len(high_outcomes)*100, 1),
                'high_n':     len(high_outcomes),
                'pct_stocks': round(len(high_outcomes)/n_total*100, 1),
            }
        if len(low_outcomes) >= 10:
            single_force_ptr.setdefault(fname, {}).update({
                'low_p_tr':   round(sum(low_outcomes)/len(low_outcomes)*100, 1),
                'low_n':      len(low_outcomes),
            })

    # ── Coupling analysis ─────────────────────────────────────────────────
    coupling_results = []
    for fi_name, fj_name, desc in COUPLING_PAIRS:
        fi = F[fi_name]; fj = F[fj_name]

        both_high    = [fo[1] for fo in force_outcomes if abs(fo[0][fi]) > HIGH_THRESH and abs(fo[0][fj]) > HIGH_THRESH]
        fi_only      = [fo[1] for fo in force_outcomes if abs(fo[0][fi]) > HIGH_THRESH and abs(fo[0][fj]) <= HIGH_THRESH]
        fj_only      = [fo[1] for fo in force_outcomes if abs(fo[0][fj]) > HIGH_THRESH and abs(fo[0][fi]) <= HIGH_THRESH]
        both_low     = [fo[1] for fo in force_outcomes if abs(fo[0][fi]) < LOW_THRESH and abs(fo[0][fj]) < LOW_THRESH]

        def _ptr(outs):
            return round(sum(outs)/len(outs)*100, 1) if len(outs) >= 5 else None

        p_both  = _ptr(both_high)
        p_fi    = _ptr(fi_only)
        p_fj    = _ptr(fj_only)
        p_neither = _ptr(both_low)

        if p_both is None:
            continue

        # Interaction effect: p_both vs max(p_fi, p_fj)
        max_single = max(p for p in [p_fi, p_fj] if p is not None) if any(p is not None for p in [p_fi, p_fj]) else baseline*100
        interaction_effect = round(p_both - max_single, 1)

        # Coupling type
        if interaction_effect > 8:
            coupling_type = 'SUPER_ADDITIVE'     # Together they're much stronger
        elif interaction_effect > 3:
            coupling_type = 'REINFORCING'         # Together they help
        elif interaction_effect < -8:
            coupling_type = 'CANCELLATION'        # Together they cancel
        elif interaction_effect < -3:
            coupling_type = 'SUPPRESSION'         # Together they weaken
        else:
            coupling_type = 'INDEPENDENT'         # Forces don't interact

        # Co-activation frequency
        co_act_freq = round(len(both_high) / n_total * 100, 1)

        coupling_results.append({
            'force_1':          fi_name,
            'force_2':          fj_name,
            'description':      desc,
            'p_both_high':      p_both,
            'p_f1_only':        p_fi,
            'p_f2_only':        p_fj,
            'p_neither':        p_neither,
            'baseline_p_tr':    round(baseline * 100, 1),
            'interaction_effect': interaction_effect,
            'coupling_type':    coupling_type,
            'co_activation_pct': co_act_freq,
            'n_both':           len(both_high),
            'insight':          _coupling_insight(fi_name, fj_name, coupling_type, p_both, interaction_effect),
        })

    coupling_results.sort(key=lambda x: -abs(x['interaction_effect']))

    # ── Force dominance analysis ───────────────────────────────────────────
    force_dominance = {}
    for fi, fname in enumerate(FORCES):
        dominant_tr   = [fo[1] for fo in force_outcomes
                         if abs(fo[0][fi]) > HIGH_THRESH
                         and all(abs(fo[0][fj]) <= HIGH_THRESH for fj in range(9) if fj != fi)]
        if len(dominant_tr) >= 10:
            force_dominance[fname] = {
                'solo_p_tr': round(sum(dominant_tr)/len(dominant_tr)*100, 1),
                'n': len(dominant_tr),
            }

    return {
        'success': True,
        'n_bars': n_total,
        'baseline_p_tr': round(baseline * 100, 1),
        'horizon': HORIZON,
        'single_force_ptr': single_force_ptr,
        'coupling_matrix': coupling_results,
        'force_dominance': force_dominance,
        'super_additive_pairs': [c for c in coupling_results if c['coupling_type'] == 'SUPER_ADDITIVE'],
        'cancellation_pairs':   [c for c in coupling_results if c['coupling_type'] == 'CANCELLATION'],
        'strongest_coupling':   coupling_results[0] if coupling_results else None,
    }


def _coupling_insight(f1, f2, ctype, p_both, effect):
    if ctype == 'SUPER_ADDITIVE':
        return f"عند تفعّل {f1}+{f2} معاً: P(TR)={p_both}% (أقوى بـ {effect:+.1f}% من الأفضل منفرداً)"
    if ctype == 'REINFORCING':
        return f"{f1}+{f2}: يُقويان بعضهما — P(TR)={p_both}% ({effect:+.1f}%)"
    if ctype == 'CANCELLATION':
        return f"⚠️ {f1}+{f2}: يتعارضان — P(TR)={p_both}% ({effect:+.1f}% تراجع)"
    if ctype == 'SUPPRESSION':
        return f"{f1}+{f2}: يُضعف كل منهما الآخر ({effect:+.1f}%)"
    return f"{f1}+{f2}: مستقلان — تأثير محدود ({effect:+.1f}%)"


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: force_evolution
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_force_evolution(params):
    """
    How do forces accumulate, peak, and decay?
    Measures: half-life, acceleration, exhaustion threshold, propagation speed.
    """
    symbol_data = _load_ohlcv()
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    # For each force, track sequences and measure dynamics
    force_sequences = {fn: [] for fn in FORCES}  # force → list of (sequence_values, outcome)
    TR_THRESH = float(params.get('tr_threshold', 2.0))

    for sym, bars in symbol_data.items():
        bar_forces = _compute_stock_forces(bars)
        if len(bar_forces) < 10:
            continue

        # Identify force rise-peak-fall sequences
        for fi, fname in enumerate(FORCES):
            force_vals = [bf['forces'][fi] for bf in bar_forces]
            fwd_rets   = [bf['fwd_ret'] for bf in bar_forces]
            n = len(force_vals)

            # Find peaks: local maxima above threshold
            for i in range(2, n-3):
                fv = force_vals[i]
                if fv < HIGH_THRESH:
                    continue
                if fv < force_vals[i-1] and fv < force_vals[i-2]:
                    continue  # not a peak
                if fv < force_vals[i+1] and i+1 < n:
                    continue  # still rising

                # Found a peak at i
                # Rise sequence: how many bars to reach peak
                rise_start = i
                for j in range(i-1, max(0, i-10)-1, -1):
                    if force_vals[j] < LOW_THRESH:
                        rise_start = j
                        break
                rise_bars = i - rise_start

                # Decay: how quickly does force return to LOW_THRESH after peak
                decay_bars = 0
                for j in range(i+1, min(n, i+20)):
                    decay_bars += 1
                    if force_vals[j] < LOW_THRESH:
                        break

                # Outcome at peak
                fwd = fwd_rets[i]
                is_tr = (fwd >= TR_THRESH) if fwd is not None else None

                force_sequences[fname].append({
                    'peak_value':  round(fv, 3),
                    'rise_bars':   rise_bars,
                    'decay_bars':  decay_bars,
                    'is_tr':       is_tr,
                    'peak_level':  'EXTREME' if fv > 0.8 else 'HIGH' if fv > HIGH_THRESH else 'MODERATE',
                })

    # ── Analyze each force ─────────────────────────────────────────────────
    evolution_results = {}
    for fname, seqs in force_sequences.items():
        if len(seqs) < 10:
            continue

        # Half-life: median decay bars
        decay_vals = [s['decay_bars'] for s in seqs]
        half_life  = round(statistics.median(decay_vals), 1) if decay_vals else None

        # Rise time: median rise bars
        rise_vals  = [s['rise_bars'] for s in seqs]
        rise_time  = round(statistics.median(rise_vals), 1) if rise_vals else None

        # Peak value distribution
        peak_vals  = [s['peak_value'] for s in seqs]
        peak_mean  = round(sum(peak_vals)/len(peak_vals), 3)
        peak_std   = round(statistics.stdev(peak_vals) if len(peak_vals) > 1 else 0, 3)

        # P(TR) at different decay stages
        early_seqs = [s for s in seqs if s['decay_bars'] <= 2]
        mid_seqs   = [s for s in seqs if 3 <= s['decay_bars'] <= 5]
        late_seqs  = [s for s in seqs if s['decay_bars'] > 5]

        def _seq_ptr(ss):
            valid = [s for s in ss if s['is_tr'] is not None]
            return round(sum(s['is_tr'] for s in valid)/len(valid)*100, 1) if len(valid) >= 3 else None

        # Extreme vs moderate peak P(TR)
        extreme_seqs  = [s for s in seqs if s['peak_level'] == 'EXTREME']
        moderate_seqs = [s for s in seqs if s['peak_level'] in ('HIGH','MODERATE')]

        evolution_results[fname] = {
            'n_peaks':      len(seqs),
            'half_life_bars': half_life,
            'rise_time_bars': rise_time,
            'peak_mean':    peak_mean,
            'peak_std':     peak_std,
            'p_tr_fast_decay': _seq_ptr(early_seqs),   # resolves quickly → P(TR)?
            'p_tr_slow_decay': _seq_ptr(late_seqs),    # lingers → P(TR)?
            'p_tr_extreme_peak': _seq_ptr(extreme_seqs),
            'p_tr_moderate_peak': _seq_ptr(moderate_seqs),
            'exhaustion_threshold': round(peak_mean + 1.5 * peak_std, 3),
            'insight': _evolution_insight(fname, half_life, rise_time, _seq_ptr(early_seqs), _seq_ptr(late_seqs)),
        }

    return {
        'success': True,
        'n_stocks': len(symbol_data),
        'tr_threshold_pct': TR_THRESH,
        'evolution': evolution_results,
        'fastest_decay':  sorted(evolution_results.items(), key=lambda x: x[1]['half_life_bars'] or 99)[:3],
        'slowest_decay':  sorted(evolution_results.items(), key=lambda x: -(x[1]['half_life_bars'] or 0))[:3],
        'key_insight':    _evolution_key_insights(evolution_results),
    }


def _evolution_insight(fname, hl, rise, ptr_fast, ptr_slow):
    parts = []
    if hl: parts.append(f"نصف عمر={hl}بار")
    if rise: parts.append(f"وقت الصعود={rise}بار")
    if ptr_fast and ptr_slow:
        if ptr_fast > ptr_slow + 5:
            parts.append(f"الذروة السريعة أقوى ({ptr_fast}% vs {ptr_slow}%)")
        elif ptr_slow > ptr_fast + 5:
            parts.append(f"القمة المطولة أقوى ({ptr_slow}% vs {ptr_fast}%)")
    return ' | '.join(parts) if parts else '—'


def _evolution_key_insights(results):
    insights = []
    for fname, res in results.items():
        if res.get('p_tr_fast_decay') and res.get('p_tr_slow_decay'):
            diff = (res['p_tr_fast_decay'] or 0) - (res['p_tr_slow_decay'] or 0)
            if diff > 8:
                insights.append(f"{fname}: الانحسار السريع → P(TR) أعلى ({res['p_tr_fast_decay']}%) — لا تنتظر")
            elif diff < -8:
                insights.append(f"{fname}: القوة المطولة → P(TR) أعلى ({res['p_tr_slow_decay']}%) — الصبر مُجدٍ")
    return insights[:5]


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: market_memory
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_market_memory(params):
    """
    Does the market REMEMBER and ADAPT?
    Compares P(TR) for 1st vs 2nd vs 3rd+ occurrence of a force state.
    Detects: alpha decay, behavioral conditioning, participant adaptation.
    """
    symbol_data = _load_ohlcv()
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    WINDOW_DAYS = int(params.get('memory_window_days', 90))  # recency window
    WINDOW_SECS = WINDOW_DAYS * 86400
    TR_THRESH   = float(params.get('tr_threshold', 2.0))
    HIGH_F      = HIGH_THRESH

    # For each stock, track PANIC and EXHAUSTION occurrences with timing
    force_memory = {
        'PANIC_FORCE':       collections.defaultdict(list),   # sym → [(time, is_tr)]
        'EXHAUSTION_FORCE':  collections.defaultdict(list),
        'MEAN_REVERSION_TENSION': collections.defaultdict(list),
    }

    for sym, bars in symbol_data.items():
        bar_forces = _compute_stock_forces(bars)
        for bf in bar_forces:
            if bf['fwd_ret'] is None:
                continue
            is_tr = 1 if bf['fwd_ret'] >= TR_THRESH else 0
            fv = bf['forces']
            t  = bf['time']

            if abs(fv[F['PANIC_FORCE']]) > HIGH_F:
                force_memory['PANIC_FORCE'][sym].append((t, is_tr))
            if abs(fv[F['EXHAUSTION_FORCE']]) > HIGH_F:
                force_memory['EXHAUSTION_FORCE'][sym].append((t, is_tr))
            if abs(fv[F['MEAN_REVERSION_TENSION']]) > 0.4:
                force_memory['MEAN_REVERSION_TENSION'][sym].append((t, is_tr))

    # ── Compute occurrence-conditioned P(TR) ──────────────────────────────
    memory_results = {}
    for force_name, sym_events in force_memory.items():
        occurrence_outcomes = {1: [], 2: [], 3: []}  # occurrence# → [is_tr]

        for sym, events in sym_events.items():
            events.sort(key=lambda x: x[0])
            for i, (t, is_tr) in enumerate(events):
                # Count how many times this force was active in the WINDOW_SECS before
                prior = sum(1 for pt, _ in events[:i] if t - pt <= WINDOW_SECS)
                occurrence_num = min(prior + 1, 3)
                occurrence_outcomes[occurrence_num].append(is_tr)

        # P(TR) per occurrence
        occurrence_ptr = {}
        for occ in [1, 2, 3]:
            outs = occurrence_outcomes[occ]
            if len(outs) >= 10:
                occurrence_ptr[occ] = {
                    'p_tr': round(sum(outs)/len(outs)*100, 1),
                    'n':    len(outs),
                }

        # Compute memory decay: does P(TR) change from 1st to 3rd?
        if 1 in occurrence_ptr and 3 in occurrence_ptr:
            decay = round(occurrence_ptr[3]['p_tr'] - occurrence_ptr[1]['p_tr'], 1)
            memory_type = 'ADAPTING' if decay < -5 else ('SENSITIZING' if decay > 5 else 'STABLE')
        else:
            decay = None
            memory_type = 'INSUFFICIENT_DATA'

        memory_results[force_name] = {
            'occurrences': occurrence_ptr,
            'memory_decay': decay,
            'memory_type': memory_type,
            'insight': _memory_insight(force_name, occurrence_ptr, decay, memory_type),
        }

    # ── Cross-market contagion analysis ───────────────────────────────────
    # After a market-wide panic (many stocks in PANIC), do individual stocks have higher/lower P(TR)?
    contagion_result = _compute_contagion(symbol_data, TR_THRESH)

    return {
        'success': True,
        'memory_window_days': WINDOW_DAYS,
        'force_memory': memory_results,
        'contagion': contagion_result,
        'meta_insight': _meta_memory_insight(memory_results),
    }


def _memory_insight(force, occ_ptr, decay, mem_type):
    if mem_type == 'ADAPTING':
        p1 = occ_ptr.get(1,{}).get('p_tr','?')
        p3 = occ_ptr.get(3,{}).get('p_tr','?')
        return f"{force}: المشاركون يتكيّفون! P(TR) يتراجع {p1}%→{p3}% بعد 3 تكرارات ({decay:+.1f}%)"
    if mem_type == 'SENSITIZING':
        p1 = occ_ptr.get(1,{}).get('p_tr','?')
        p3 = occ_ptr.get(3,{}).get('p_tr','?')
        return f"{force}: التكرار يُقوي الاستجابة! P(TR) يرتفع {p1}%→{p3}% ({decay:+.1f}%)"
    if occ_ptr:
        return f"{force}: لا تكيّف واضح عبر التكرارات"
    return f"{force}: بيانات غير كافية"


def _meta_memory_insight(memory_results):
    insights = []
    for fn, res in memory_results.items():
        if res['memory_type'] == 'ADAPTING':
            insights.append(f"⚠️ {fn}: alpha يتراجع مع التكرار ({res['memory_decay']:+.1f}%)")
        elif res['memory_type'] == 'SENSITIZING':
            insights.append(f"🔥 {fn}: التكرار يُعمّق الاستجابة — عكس الحكمة التقليدية!")
    return insights or ['لا يوجد تكيّف سلوكي واضح — السوق يستجيب بشكل ثابت للتكرار']


def _compute_contagion(symbol_data, tr_thresh):
    """Simple contagion: after high-breadth panic, do individual stocks bounce more?"""
    # This requires market-wide panic detection — simplified here
    return {'note': 'تحليل العدوى: يحتاج بيانات Breadth يومية — قيد التطوير'}


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: failure_physics
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_failure_physics(params):
    """
    Why do reversals FAIL?
    For each failed reversal event, identify the DOMINANT BLOCKING FORCE.
    Build the 'failure force profile' — which forces prevent recovery.
    """
    symbol_data = _load_ohlcv()
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    TR_THRESH   = float(params.get('tr_threshold', 2.0))
    FAIL_THRESH = float(params.get('fail_threshold', -1.5))

    # Collect: reversal zone events where the outcome was FAILURE
    true_rev_forces  = [[] for _ in FORCES]   # forces at true reversals
    failed_rev_forces= [[] for _ in FORCES]   # forces at failed reversals

    failure_blocking = collections.defaultdict(int)  # blocking force → count
    failure_typology = collections.defaultdict(int)  # failure type → count

    for sym, bars in symbol_data.items():
        bar_forces = _compute_stock_forces(bars)
        for bf in bar_forces:
            if bf['fwd_ret'] is None:
                continue

            fv  = bf['forces']
            rsi = bf['rsi']

            # Only analyze oversold/exhaustion candidates (potential reversal zone)
            f_mr_tension = fv[F['MEAN_REVERSION_TENSION']]
            if f_mr_tension < 0.3:  # not in reversal zone
                continue

            is_true_rev   = bf['fwd_ret'] >= TR_THRESH
            is_failed_rev = bf['fwd_ret'] <= FAIL_THRESH

            if is_true_rev:
                for fi in range(9):
                    true_rev_forces[fi].append(fv[fi])
            elif is_failed_rev:
                for fi in range(9):
                    failed_rev_forces[fi].append(fv[fi])

                # Identify blocking force: which force was anomalously high?
                blocking = _identify_blocking_force(fv, rsi)
                if blocking:
                    failure_blocking[blocking] += 1

                # Classify failure type
                ftype = _classify_failure_type(fv, bf['fwd_ret'])
                failure_typology[ftype] += 1

    n_tr   = len(true_rev_forces[0]) if true_rev_forces[0] else 0
    n_fail = len(failed_rev_forces[0]) if failed_rev_forces[0] else 0

    if n_tr < 5 or n_fail < 5:
        return {
            'success': True,
            'n_true_reversal': n_tr, 'n_failed': n_fail,
            'note': f'بيانات غير كافية (TR={n_tr}, Failed={n_fail})',
        }

    # ── Force discriminants: which forces DIFFER most between TR and Failed ─
    discriminants = []
    for fi, fname in enumerate(FORCES):
        tr_vals   = true_rev_forces[fi]
        fail_vals = failed_rev_forces[fi]
        if not tr_vals or not fail_vals:
            continue

        tr_mean   = sum(tr_vals) / len(tr_vals)
        fail_mean = sum(fail_vals) / len(fail_vals)
        diff      = tr_mean - fail_mean

        # Cohen's d effect size
        tr_std   = statistics.stdev(tr_vals) if len(tr_vals) > 1 else 0.1
        fail_std = statistics.stdev(fail_vals) if len(fail_vals) > 1 else 0.1
        pooled   = math.sqrt((tr_std**2 + fail_std**2) / 2) or 0.01
        d        = round(diff / pooled, 3)

        discriminants.append({
            'force':        fname,
            'tr_mean':      round(tr_mean, 3),
            'fail_mean':    round(fail_mean, 3),
            'difference':   round(diff, 3),
            'effect_d':     d,
            'interpretation': _force_discriminant_insight(fname, diff, d),
        })

    discriminants.sort(key=lambda x: -abs(x['effect_d']))

    # ── Failure topology ──────────────────────────────────────────────────
    total_failures = sum(failure_typology.values()) or 1
    failure_topology = {
        ftype: {'n': cnt, 'pct': round(cnt/total_failures*100, 1)}
        for ftype, cnt in sorted(failure_typology.items(), key=lambda x: -x[1])
    }

    # ── Blocking forces ───────────────────────────────────────────────────
    total_blocks = sum(failure_blocking.values()) or 1
    blocking_profile = {
        force: {'n': cnt, 'pct': round(cnt/total_blocks*100, 1)}
        for force, cnt in sorted(failure_blocking.items(), key=lambda x: -x[1])
    }

    return {
        'success': True,
        'n_true_reversal': n_tr,
        'n_failed':        n_fail,
        'discriminants':   discriminants[:8],
        'failure_topology': failure_topology,
        'blocking_profile': blocking_profile,
        'composite_insight': _failure_composite_insight(discriminants[:3], failure_topology, blocking_profile),
        'methodology': f'Reversal zone (MRT>0.3) split by {TR_THRESH}% (TR) / {FAIL_THRESH}% (Fail). Effect size = Cohen\'s d.',
    }


def _identify_blocking_force(fv, rsi):
    """Which force was anomalously high in a failed reversal?"""
    # Momentum inertia: if negative (downward) and strong → blocking recovery
    if abs(fv[F['MOMENTUM_INERTIA']]) > 0.5 and fv[F['MOMENTUM_INERTIA']] < 0:
        return 'MOMENTUM_INERTIA'
    if fv[F['SELLING_PRESSURE']] > 0.6:
        return 'SELLING_PRESSURE'
    if fv[F['VOLATILITY_EXPANSION']] > 0.6 and fv[F['MOMENTUM_INERTIA']] < 0:
        return 'VOLATILITY_EXPANSION'
    if fv[F['INSTABILITY_INDEX']] > 0.6:
        return 'INSTABILITY_INDEX'
    if fv[F['PANIC_FORCE']] > 0.4:
        return 'PANIC_FORCE'
    return None


def _classify_failure_type(fv, fwd_ret):
    """Classify the type of failed reversal."""
    if fwd_ret >= 0:  # slight positive but not enough = dead cat
        return 'DEAD_CAT_BOUNCE'
    if abs(fv[F['MOMENTUM_INERTIA']]) > 0.5 and fv[F['MOMENTUM_INERTIA']] < 0:
        return 'MOMENTUM_CONTINUATION'
    if fv[F['VOLATILITY_EXPANSION']] > 0.5:
        return 'VOLATILITY_TRAP'
    if fv[F['SELLING_PRESSURE']] > 0.5 and fv[F['LIQUIDITY_ABSORPTION']] < -0.3:
        return 'LIQUIDITY_VACUUM'
    if fv[F['INSTABILITY_INDEX']] > 0.5:
        return 'FORCE_FIELD_CONFLICT'
    return 'GRADUAL_DRIFT'


def _force_discriminant_insight(fname, diff, d):
    """Explain what differentiates true vs failed reversals for this force."""
    if fname == 'MOMENTUM_INERTIA':
        if diff > 0.1:
            return "الارتداد الحقيقي: زخم إيجابي أقوى — التحوّل الزخمي هو المفتاح"
        else:
            return "الفشل يأتي مع زخم سلبي قوي يقاوم الارتداد (inertia)"
    if fname == 'MEAN_REVERSION_TENSION':
        if diff > 0.05:
            return "الارتداد الحقيقي: شد انتعاش أعلى — الإفراط في البيع أكثر حدة"
        return "الفشل: شد الانتعاش لم يكن كافياً"
    if fname == 'SELLING_PRESSURE':
        if diff < -0.05:
            return "ضغط البيع أعلى في الفشل — المؤسسيون لا يزالون يبيعون"
        return "ضغط البيع مماثل — ليس العامل الحاسم"
    if fname == 'VOLATILITY_EXPANSION':
        if diff < -0.05:
            return "التقلب المرتفع يعيق الارتداد — عدم الاستقرار يمنع الانتعاش"
        return "التقلب لا يُفرّق بين TR والفشل"
    return f"{fname}: فرق={diff:+.3f} (Cohen's d={d:.2f})"


def _failure_composite_insight(discriminants, topology, blocking):
    """Build the main insight from failure physics."""
    if not discriminants:
        return "بيانات غير كافية لتحليل الفشل"
    top = discriminants[0]
    top_block = list(blocking.keys())[0] if blocking else 'UNKNOWN'
    top_fail  = list(topology.keys())[0] if topology else 'UNKNOWN'
    return (
        f"المحرك الأول للفشل: {top['force']} (d={top['effect_d']:.2f}) | "
        f"أكثر قوة عائقة: {top_block} ({blocking.get(top_block,{}).get('pct',0):.0f}%) | "
        f"أغلب نوع الفشل: {top_fail} ({topology.get(top_fail,{}).get('pct',0):.0f}%)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: force_attractors
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_force_attractors(params):
    """
    Latent behavioral manifold: where does the market CONVERGE?
    Uses force field vectors to find:
    - Stability attractors (where market spends most time)
    - Instability zones (where market transitions rapidly)
    - Repeller zones (market never stays)
    """
    if not HAS_NUMPY:
        return {'success': False, 'error': 'numpy required for manifold analysis'}

    symbol_data = _load_ohlcv()
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    # Collect all force vectors
    all_vecs = []
    all_times= []
    for sym, bars in list(symbol_data.items())[:80]:  # limit for speed
        bar_forces = _compute_stock_forces(bars)
        for bf in bar_forces:
            all_vecs.append(bf['forces'])
            all_times.append(bf['time'])

    if len(all_vecs) < 100:
        return {'success': False, 'error': f'Too few vectors: {len(all_vecs)}'}

    X = np.array(all_vecs, dtype=float)  # n × 9

    # Standardize
    means = X.mean(axis=0)
    stds  = X.std(axis=0)
    stds[stds < 0.01] = 0.01
    Xz = (X - means) / stds

    # PCA to 3D for manifold analysis
    cov = np.cov(Xz.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    idx     = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]

    exp_var = (eigvals[:3] / eigvals.sum() * 100).round(1).tolist()
    proj    = Xz @ eigvecs[:, :3]  # project to 3D

    # Name the latent dimensions from loadings
    loadings = eigvecs[:, :3]
    dim_names = []
    for di in range(3):
        top_idx = np.argsort(np.abs(loadings[:, di]))[::-1]
        top3    = [FORCES[i] for i in top_idx[:2]]
        dim_names.append(_name_force_dimension(top3, loadings[top_idx[0], di]))

    # K-means clustering in 3D force space (k=6 attractors)
    k = 6
    from scripts_latent_kmeans_simple import _kmeans_simple_np
    cluster_ids = _kmeans_simple_np(proj.tolist(), k=k, n_iter=30)

    # Characterize each attractor
    clusters = collections.defaultdict(list)
    for i, cid in enumerate(cluster_ids):
        clusters[cid].append(i)

    attractor_profiles = {}
    for cid, indices in clusters.items():
        cluster_vecs = X[indices]
        cluster_means= cluster_vecs.mean(axis=0)
        cluster_proj = proj[indices].mean(axis=0)

        # Dominant forces
        dom_forces = [(FORCES[fi], round(float(cluster_means[fi]), 3))
                      for fi in np.argsort(np.abs(cluster_means))[::-1][:3]]

        # Stability: how much time market spends here
        pct_time = round(len(indices)/len(all_vecs)*100, 1)

        # Transition speed: how quickly does market leave this attractor?
        transition_times = []
        for idx_start in indices:
            # Find next bar not in this cluster
            for offset in range(1, 6):
                if idx_start + offset < len(cluster_ids):
                    if cluster_ids[idx_start + offset] != cid:
                        transition_times.append(offset)
                        break
        avg_stay = round(statistics.mean(transition_times), 1) if transition_times else 0

        # P(TR) in this cluster
        tr_in_cluster = []
        for idx_i in indices:
            bf_idx = idx_i  # approximate
            if bf_idx < len(all_vecs):
                # Can't get fwd_ret easily here, skip
                pass

        # Classify attractor type
        f3_mean = float(cluster_means[F['EXHAUSTION_FORCE']])
        f4_mean = float(cluster_means[F['PANIC_FORCE']])
        f8_mean = float(cluster_means[F['INSTABILITY_INDEX']])
        f9_mean = float(cluster_means[F['MEAN_REVERSION_TENSION']])
        attractor_type = _classify_attractor(f3_mean, f4_mean, f8_mean, f9_mean, pct_time)

        attractor_profiles[f'A{cid}'] = {
            'type':         attractor_type,
            'pct_time':     pct_time,
            'avg_stay_bars': avg_stay,
            'n_observations': len(indices),
            'dominant_forces': dom_forces,
            'centroid_forces': {FORCES[fi]: round(float(cluster_means[fi]), 3) for fi in range(9)},
            'stability': 'STABLE_ATTRACTOR' if avg_stay > 2 else 'TRANSITION_ZONE',
        }

    # ── Instability manifold ───────────────────────────────────────────────
    # Points with high F8 (INSTABILITY_INDEX) form the instability zone
    instability_indices = [i for i, v in enumerate(all_vecs) if abs(v[F['INSTABILITY_INDEX']]) > 0.5]
    instability_pct = round(len(instability_indices)/len(all_vecs)*100, 1)

    return {
        'success': True,
        'n_observations': len(all_vecs),
        'n_stocks': len(symbol_data),
        'explained_variance_3d': exp_var,
        'total_explained': round(sum(exp_var), 1),
        'dimension_names': dim_names,
        'attractors': attractor_profiles,
        'instability_zone_pct': instability_pct,
        'methodology': f'9D Force Field → PCA({sum(exp_var):.0f}% var) → K-Means(k={k}) | {len(all_vecs)} observations',
    }


def _name_force_dimension(top_forces, sign):
    """Name a latent dimension based on its top contributing forces."""
    if 'PANIC_FORCE' in top_forces and 'SELLING_PRESSURE' in top_forces:
        return 'Crisis-Selling Axis' if sign > 0 else 'Anti-Crisis Axis'
    if 'EXHAUSTION_FORCE' in top_forces and 'MEAN_REVERSION_TENSION' in top_forces:
        return 'Exhaustion-Recovery Continuum'
    if 'MOMENTUM_INERTIA' in top_forces and 'BUYING_PRESSURE' in top_forces:
        return 'Momentum-Accumulation Axis'
    if 'VOLATILITY_EXPANSION' in top_forces:
        return 'Volatility Regime Axis'
    if 'INSTABILITY_INDEX' in top_forces:
        return 'Force-Field Instability Axis'
    if 'LIQUIDITY_ABSORPTION' in top_forces:
        return 'Liquidity Flow Axis'
    return 'Latent Force Axis'


def _classify_attractor(f3, f4, f8, f9, pct_time):
    """Classify a force-field attractor."""
    if f4 > 0.3 and f3 > 0.4:
        return 'PANIC_ZONE'
    if f8 > 0.4:
        return 'INSTABILITY_ZONE'
    if f9 > 0.3:
        return 'RECOVERY_ATTRACTOR'
    if f3 > 0.5:
        return 'EXHAUSTION_BASIN'
    if pct_time > 20:
        return 'NEUTRAL_EQUILIBRIUM'
    return 'TRANSITIONAL_ZONE'


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: force_field_full
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_force_field_full(params):
    """
    Complete Force Field Report: runs all sub-analyses and synthesizes.
    """
    _t0 = datetime.datetime.utcnow()

    results = {}
    for name, fn, p in [
        ('force_field_now',    cmd_force_field_now,    {}),
        ('force_interactions', cmd_force_interactions, {}),
        ('force_evolution',    cmd_force_evolution,    {}),
        ('market_memory',      cmd_market_memory,      {}),
        ('failure_physics',    cmd_failure_physics,    {}),
    ]:
        try:
            results[name] = fn(p)
        except Exception as e:
            results[name] = {'success': False, 'error': str(e)}

    elapsed = (datetime.datetime.utcnow() - _t0).total_seconds()

    # ── Synthesize ────────────────────────────────────────────────────────
    synthesis = _synthesize_force_field(results)

    return {
        'success': True,
        'elapsed_sec': round(elapsed, 1),
        'synthesis': synthesis,
        'sub_results': {k: v.get('success', False) for k, v in results.items()},
        'force_field_now': results.get('force_field_now', {}),
        'interactions_summary': {
            'strongest': results.get('force_interactions', {}).get('strongest_coupling'),
            'super_additive': [c['force_1']+'×'+c['force_2'] for c in results.get('force_interactions', {}).get('super_additive_pairs', [])],
            'cancellation': [c['force_1']+'×'+c['force_2'] for c in results.get('force_interactions', {}).get('cancellation_pairs', [])],
        },
        'memory_summary': results.get('market_memory', {}).get('meta_insight', []),
        'failure_summary': results.get('failure_physics', {}).get('composite_insight', ''),
        'evolution_summary': results.get('force_evolution', {}).get('key_insight', []),
    }


def _synthesize_force_field(results):
    """Synthesize all force field results into key insights."""
    insights = []

    # Current market state
    ffn = results.get('force_field_now', {})
    if ffn.get('success'):
        insights.append(f"حالة حقل القوى: {ffn.get('field_state','UNKNOWN')}")
        rev = ffn.get('reversal_candidates', [])
        if rev:
            syms = [r['symbol'] for r in rev[:5]]
            insights.append(f"مرشحو الارتداد الآن: {', '.join(syms)}")

    # Strongest coupling
    fi = results.get('force_interactions', {})
    if fi.get('success') and fi.get('strongest_coupling'):
        sc = fi['strongest_coupling']
        insights.append(f"أقوى تفاعل قوى: {sc['force_1']}×{sc['force_2']} = {sc['coupling_type']} ({sc['interaction_effect']:+.1f}%)")

    # Failure physics
    fp = results.get('failure_physics', {})
    if fp.get('success') and fp.get('composite_insight'):
        insights.append(f"فيزياء الفشل: {fp['composite_insight']}")

    # Memory
    mm = results.get('market_memory', {})
    if mm.get('success'):
        meta = mm.get('meta_insight', [])
        insights.extend(meta[:2])

    return insights


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADER
# ═══════════════════════════════════════════════════════════════════════════════

def _load_ohlcv(min_bars=60):
    con = get_db()
    rows = con.execute("""
        SELECT o.symbol, o.bar_time, o.open, o.high, o.low, o.close, o.volume,
               u.sector
        FROM ohlcv_history_execution o
        LEFT JOIN stock_universe u ON o.symbol = u.symbol
        ORDER BY o.symbol, o.bar_time
    """).fetchall()
    con.close()

    out = collections.defaultdict(list)
    for r in rows:
        out[r['symbol']].append({
            'symbol': r['symbol'], 'bar_time': r['bar_time'],
            'open': r['open'], 'high': r['high'],
            'low': r['low'],   'close': r['close'],
            'volume': r['volume'],
            'sector': r['sector'] or 'Unknown',
        })

    return {sym: bars for sym, bars in out.items() if len(bars) >= min_bars}


# ── Simple K-Means helper (numpy version) ──────────────────────────────────
import sys as _sys
# Expose for force_attractors
def _kmeans_simple_np(points, k=6, n_iter=30):
    import random
    n = len(points)
    dim = len(points[0])
    centroids = [list(points[i]) for i in random.sample(range(n), min(k, n))]
    assignments = [0] * n
    for _ in range(n_iter):
        for i, pt in enumerate(points):
            dists = [sum((pt[d]-c[d])**2 for d in range(dim)) for c in centroids]
            assignments[i] = dists.index(min(dists))
        for j in range(k):
            members = [points[i] for i, a in enumerate(assignments) if a == j]
            if members:
                centroids[j] = [sum(m[d] for m in members)/len(members) for d in range(dim)]
    return assignments

# Make available for import in force_attractors
import importlib, types
_mod = types.ModuleType('scripts_latent_kmeans_simple')
_mod._kmeans_simple_np = _kmeans_simple_np
_sys.modules['scripts_latent_kmeans_simple'] = _mod


# ═══════════════════════════════════════════════════════════════════════════════
# DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    'force_field_now':    cmd_force_field_now,
    'force_interactions': cmd_force_interactions,
    'force_evolution':    cmd_force_evolution,
    'market_memory':      cmd_market_memory,
    'failure_physics':    cmd_failure_physics,
    'force_attractors':   cmd_force_attractors,
    'force_field_full':   cmd_force_field_full,
}

def main():
    try:
        if len(sys.argv) >= 2 and sys.argv[1] in COMMANDS:
            command = sys.argv[1]
            params  = json.loads(sys.argv[2]) if len(sys.argv) >= 3 else {}
        else:
            inp     = json.loads(sys.stdin.read())
            command = inp.get('command', '')
            params  = inp.get('params', {})
    except Exception as e:
        print(json.dumps({'success': False, 'error': f'JSON parse: {e}'}))
        sys.exit(1)
    fn = COMMANDS.get(command)
    if not fn:
        print(json.dumps({'success': False, 'error': f'Unknown: {command}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = fn(params)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)

if __name__ == '__main__':
    main()
