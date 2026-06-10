#!/usr/bin/env python3
"""
decision_engine.py — Phase 6: Adaptive Market Decision Engine
═════════════════════════════════════════════════════════════
Transforms market intelligence (Phases 1-5) into adaptive decisions.

EXPECTED_BEHAVIORAL_PAYOFF (EBP) =
    (p_success × magnitude × structural_stability × causal_confidence × regime_alignment)
  - (instability_risk × propagation_risk × liquidity_cost × uncertainty_penalty × tail_risk)

Decision States: HIGH_CONVICTION | CONDITIONAL | FRAGILE | TRANSITIONAL | UNSTABLE | AVOID
"""

import sys, json, time, math
from pathlib import Path
from collections import defaultdict

try:
    import sqlite3
    import statistics
except ImportError as e:
    print(json.dumps({"error": f"Missing module: {e}"}))
    sys.exit(1)

DB_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')

# ── Thresholds ─────────────────────────────────────────────────────────────
HIGH_E    = 0.35
MEDIUM_E  = 0.20
LOW_E     = 0.10

EBP_HIGH_CONVICTION = 0.35
EBP_CONDITIONAL     = 0.20
EBP_FRAGILE         = 0.10
EBP_AVOID           = 0.0

UNCERT_LOW    = 0.25
UNCERT_MEDIUM = 0.45
UNCERT_HIGH   = 0.65

# ── DB helpers ─────────────────────────────────────────────────────────────

def _get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def _load_indicators_now():
    con = _get_db()
    rows = con.execute("""
        SELECT ic.symbol, ic.rsi14, ic.ema20, ic.ema50, ic.atr14,
               ic.obv, ic.bb_upper, ic.bb_lower, ic.bb_middle,
               ic.macd_line, ic.macd_signal, ic.vol_ratio_20,
               ic.bar_date, ic.momentum_5d, ic.adx14,
               COALESCE(su.sector, 'Unknown') AS sector
        FROM indicators_cache ic
        LEFT JOIN stock_universe su ON ic.symbol = su.symbol
        INNER JOIN (
            SELECT symbol, MAX(bar_date) AS max_date
            FROM indicators_cache GROUP BY symbol
        ) latest ON ic.symbol = latest.symbol AND ic.bar_date = latest.max_date
        WHERE ic.rsi14 IS NOT NULL
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]

def _load_ohlcv_all(min_bars=30, max_bars=None):
    con = _get_db()
    rows = con.execute(
        "SELECT symbol, bar_time, open, high, low, close, volume "
        "FROM ohlcv_history ORDER BY symbol, bar_time"
    ).fetchall()
    con.close()
    result = defaultdict(list)
    for r in rows:
        result[r[0]].append({
            'time': r[1], 'open': r[2], 'high': r[3],
            'low': r[4], 'close': r[5], 'volume': r[6] or 0
        })
    filtered = {}
    for s, bars in result.items():
        if len(bars) >= min_bars:
            filtered[s] = bars[-max_bars:] if max_bars and len(bars) > max_bars else bars
    return filtered

def _load_sector_map():
    con = _get_db()
    rows = con.execute("SELECT symbol, sector FROM stock_universe").fetchall()
    con.close()
    return {r[0]: (r[1] or 'Unknown') for r in rows}

# ── Energy computation (fast 5-dim, reuses Phase 4 patterns) ─────────────

def _compute_energy_quick(bars):
    if len(bars) < 10:
        return None
    recent = bars[-20:]
    closes = [b['close'] for b in recent]
    highs  = [b['high']  for b in recent]
    lows   = [b['low']   for b in recent]
    vols   = [b['volume'] for b in recent]

    atr_vals  = [highs[i] - lows[i] for i in range(len(recent))]
    curr_atr  = statistics.mean(atr_vals[-5:]) if len(atr_vals) >= 5 else atr_vals[-1]
    hist_atr  = statistics.mean(atr_vals) if atr_vals else 1
    vol_ratio = hist_atr / max(curr_atr, 1e-8)
    volatility_energy = min(1.0, max(0.0, (vol_ratio - 1.0) / 2.0))

    ret5 = (closes[-1] - closes[-5]) / max(closes[-5], 1e-8) if len(closes) >= 5 else 0
    momentum_energy = min(1.0, abs(ret5) / 0.05)

    daily_rets = [(closes[i] - closes[i-1]) / max(closes[i-1], 1e-8) for i in range(1, len(closes))]
    neg_rets   = [r for r in daily_rets if r < 0]
    panic_raw  = abs(statistics.mean(neg_rets)) / 0.02 if neg_rets else 0
    panic_energy = min(1.0, panic_raw)

    avg_vol     = statistics.mean(vols) if vols else 1
    recent_vol  = statistics.mean(vols[-3:]) if len(vols) >= 3 else vols[-1]
    liquidity_stress = min(1.0, max(0.0, 1.0 - recent_vol / max(avg_vol, 1)))

    if len(closes) >= 10:
        ret_early = (closes[-5] - closes[-10]) / max(closes[-10], 1e-8)
        ret_late  = (closes[-1] - closes[-5])  / max(closes[-5],  1e-8)
        decel = (abs(ret_early) - abs(ret_late)) / max(abs(ret_early), 1e-8)
        exhaustion_energy = min(1.0, max(0.0, decel))
    else:
        exhaustion_energy = 0.0

    return {
        'MOMENTUM_ENERGY':   momentum_energy,
        'PANIC_ENERGY':      panic_energy,
        'EXHAUSTION_ENERGY': exhaustion_energy,
        'VOLATILITY_ENERGY': volatility_energy,
        'LIQUIDITY_STRESS':  liquidity_stress,
    }

def _energy_from_indicators(row):
    """Fallback energy estimate from indicators_cache when OHLCV not available."""
    rsi = row.get('rsi14', 50) or 50
    vol_ratio = row.get('vol_ratio_20', 1.0) or 1.0
    return {
        'MOMENTUM_ENERGY':   max(0.0, min(1.0, (rsi - 50) / 50)) if rsi > 50 else 0.0,
        'PANIC_ENERGY':      max(0.0, min(1.0, (40 - rsi) / 40)) if rsi < 40 else 0.0,
        'EXHAUSTION_ENERGY': max(0.0, min(1.0, (rsi - 65) / 35)) if rsi > 65 else 0.0,
        'VOLATILITY_ENERGY': 0.15,
        'LIQUIDITY_STRESS':  max(0.0, min(1.0, 1.0 - vol_ratio)) if vol_ratio < 1 else 0.0,
    }

# ── Causal invariants from Phase 5 ────────────────────────────────────────

CAUSAL_INVARIANTS = [
    {'from': 'PANIC_ONSET',       'to': 'RECOVERY_ONSET',    'lag': 1, 'lift': 11.08, 'p': 0.713},
    {'from': 'VOL_COMPRESSION',   'to': 'VOL_EXPLOSION',     'lag': 1, 'lift': 4.71,  'p': 0.344},
    {'from': 'INSTABILITY_SPIKE', 'to': 'RECOVERY_ONSET',    'lag': 1, 'lift': 2.53,  'p': 0.163},
    {'from': 'RECOVERY_ONSET',    'to': 'PANIC_ONSET',       'lag': 1, 'lift': 2.08,  'p': 0.077},
    {'from': 'EXHAUSTION_ONSET',  'to': 'INSTABILITY_SPIKE', 'lag': 1, 'lift': 2.01,  'p': 0.120},
    {'from': 'PANIC_ONSET',       'to': 'LIQUIDITY_DRAIN',   'lag': 1, 'lift': 1.95,  'p': 0.323},
    {'from': 'EXHAUSTION_ONSET',  'to': 'PANIC_ONSET',       'lag': 1, 'lift': 1.80,  'p': 0.067},
]

def _detect_causal_events(energy):
    active = []
    if energy.get('PANIC_ENERGY', 0)      >= HIGH_E:   active.append('PANIC_ONSET')
    if energy.get('MOMENTUM_ENERGY', 0)   >= HIGH_E:   active.append('MOMENTUM_SURGE')
    if energy.get('EXHAUSTION_ENERGY', 0) >= MEDIUM_E: active.append('EXHAUSTION_ONSET')
    if energy.get('VOLATILITY_ENERGY', 0) >= HIGH_E:   active.append('VOL_COMPRESSION')
    if energy.get('LIQUIDITY_STRESS', 0)  >= HIGH_E:   active.append('LIQUIDITY_DRAIN')
    if energy.get('PANIC_ENERGY', 0) < LOW_E and energy.get('MOMENTUM_ENERGY', 0) > MEDIUM_E:
        active.append('RECOVERY_ONSET')

    predictions = []
    for inv in CAUSAL_INVARIANTS:
        if inv['from'] in active:
            predictions.append({
                'event':      inv['to'],
                'lag':        inv['lag'],
                'p':          inv['p'],
                'lift':       inv['lift'],
                'confidence': min(1.0, inv['lift'] / 12.0),
            })
    predictions.sort(key=lambda x: x['confidence'], reverse=True)
    return active, predictions

# ── EBP computation ────────────────────────────────────────────────────────

REGIME_ALIGNMENT = {'BULL': 0.90, 'STRESS': 0.65, 'CRISIS': 0.45, 'CALM': 0.75, 'NEUTRAL': 0.80}

def _compute_ebp(row, energy, active_events, predictions, regime='BULL'):
    if not energy:
        return None

    rsi = row.get('rsi14', 50) or 50

    # ── Positive ──
    if rsi < 30:
        p_success = min(0.78, 0.55 + (30 - rsi) / 100)
    elif rsi > 70:
        p_success = min(0.72, 0.50 + (rsi - 70) / 100)
    else:
        p_success = 0.35 + (50 - abs(rsi - 50)) / 200
    p_success = max(0.20, p_success)

    mom   = energy.get('MOMENTUM_ENERGY', 0)
    vol_e = energy.get('VOLATILITY_ENERGY', 0)
    expected_magnitude = min(1.0, 0.30 + mom * 0.40 + vol_e * 0.20)

    panic   = energy.get('PANIC_ENERGY', 0)
    exhaust = energy.get('EXHAUSTION_ENERGY', 0)
    structural_stability = max(0.10, 1.0 - (panic * 0.40 + exhaust * 0.30))

    if predictions:
        top_conf = predictions[0]['confidence']
        causal_confidence = min(1.0, 0.40 + top_conf * 0.55)
    else:
        causal_confidence = 0.28

    regime_alignment = REGIME_ALIGNMENT.get(regime, 0.80)

    positive = (p_success * expected_magnitude * structural_stability *
                causal_confidence * regime_alignment)

    # ── Negative ──
    liq_stress    = energy.get('LIQUIDITY_STRESS', 0)
    instab        = max(panic, exhaust, liq_stress * 0.50)
    instability_risk = instab * 0.60

    propagation_risk = 0.08
    if 'PANIC_ONSET' in active_events:       propagation_risk += 0.22
    if 'INSTABILITY_SPIKE' in active_events: propagation_risk += 0.15
    propagation_risk = min(0.60, propagation_risk)

    liquidity_cost = 0.10 + liq_stress * 0.40

    n_active = len(active_events)
    if n_active == 0:
        uncertainty_penalty = 0.30
    elif n_active >= 4:
        uncertainty_penalty = 0.42
    else:
        uncertainty_penalty = max(0.12, 0.28 - n_active * 0.05)

    tail_risk = min(0.80, panic * 0.50 + liq_stress * 0.30 + exhaust * 0.15)

    negative = (instability_risk * propagation_risk * liquidity_cost *
                uncertainty_penalty * tail_risk)

    ebp = positive - negative

    return {
        'ebp':                  round(ebp, 4),
        'positive':             round(positive, 4),
        'negative':             round(negative, 4),
        'p_success':            round(p_success, 3),
        'expected_magnitude':   round(expected_magnitude, 3),
        'structural_stability': round(structural_stability, 3),
        'causal_confidence':    round(causal_confidence, 3),
        'regime_alignment':     round(regime_alignment, 3),
        'instability_risk':     round(instability_risk, 3),
        'propagation_risk':     round(propagation_risk, 3),
        'liquidity_cost':       round(liquidity_cost, 3),
        'uncertainty_penalty':  round(uncertainty_penalty, 3),
        'tail_risk':            round(tail_risk, 3),
    }

# ── Decision classification ────────────────────────────────────────────────

def _classify_decision(ebp_data, uncertainty):
    if not ebp_data:
        return 'AVOID'
    ebp    = ebp_data['ebp']
    instab = ebp_data['instability_risk']
    tail   = ebp_data['tail_risk']

    if uncertainty > UNCERT_HIGH or tail > 0.55:
        return 'AVOID'
    if instab > 0.40 and ebp < EBP_CONDITIONAL:
        return 'UNSTABLE'
    if ebp >= EBP_HIGH_CONVICTION and uncertainty < UNCERT_LOW:
        return 'HIGH_CONVICTION'
    if ebp >= EBP_CONDITIONAL and uncertainty < UNCERT_MEDIUM:
        return 'CONDITIONAL'
    if ebp >= EBP_FRAGILE and instab < 0.30:
        return 'FRAGILE'
    if ebp >= EBP_FRAGILE:
        return 'TRANSITIONAL'
    if ebp >= EBP_AVOID:
        return 'UNSTABLE'
    return 'AVOID'

# ── Uncertainty decomposition ─────────────────────────────────────────────

def _compute_uncertainty(energy, row, active_events):
    if not energy:
        return 0.80

    signal_strength  = energy.get('MOMENTUM_ENERGY', 0) + energy.get('PANIC_ENERGY', 0)
    noise_level      = min(1.0, len(active_events) / 8.0)
    confidence_decay = min(1.0, noise_level * 0.60 + (1.0 - min(1.0, signal_strength)) * 0.30)

    causal_uncertainty = 0.45 if not active_events else 0.18

    panic = energy.get('PANIC_ENERGY', 0)
    liq   = energy.get('LIQUIDITY_STRESS', 0)
    structural_instability = (panic + liq) / 2.0

    vol_e = energy.get('VOLATILITY_ENERGY', 0)
    regime_ambiguity = vol_e * 0.50

    uncertainty = (confidence_decay      * 0.30 +
                   causal_uncertainty    * 0.25 +
                   structural_instability* 0.25 +
                   regime_ambiguity      * 0.20)
    return min(1.0, max(0.0, uncertainty))

# ── Market regime detection ───────────────────────────────────────────────

def _detect_regime(indicators):
    if not indicators:
        return 'NEUTRAL'
    rsis    = [r.get('rsi14', 50) or 50 for r in indicators]
    avg_rsi = statistics.mean(rsis)
    bullish = sum(1 for r in rsis if r > 55) / len(rsis)
    bearish = sum(1 for r in rsis if r < 40) / len(rsis)
    if avg_rsi > 60 and bullish > 0.50: return 'BULL'
    if avg_rsi < 35 and bearish > 0.50: return 'CRISIS'
    if bearish > 0.35:                   return 'STRESS'
    if avg_rsi > 52:                     return 'BULL'
    return 'CALM'

# ── Kelly position sizing ─────────────────────────────────────────────────

def _kelly_size(p_success, b=1.5, max_size=0.12, fraction=0.25):
    q = 1.0 - p_success
    kelly_full = (b * p_success - q) / b
    return min(max_size, max(0.01, kelly_full * fraction))

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 1 — decision_now
# ═══════════════════════════════════════════════════════════════════════════

def cmd_decision_now(params):
    t0 = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=15, max_bars=30)
    regime     = _detect_regime(indicators)

    results       = []
    decision_dist = defaultdict(int)

    for row in indicators:
        sym   = row['symbol']
        bars  = ohlcv.get(sym, [])
        energy = _compute_energy_quick(bars) if len(bars) >= 10 else _energy_from_indicators(row)

        active_events, predictions = _detect_causal_events(energy)
        uncertainty = _compute_uncertainty(energy, row, active_events)
        ebp_data    = _compute_ebp(row, energy, active_events, predictions, regime)
        decision    = _classify_decision(ebp_data, uncertainty)
        decision_dist[decision] += 1

        results.append({
            'symbol':          sym,
            'sector':          row.get('sector', 'Unknown'),
            'decision':        decision,
            'ebp':             ebp_data['ebp'] if ebp_data else 0.0,
            'p_success':       ebp_data['p_success'] if ebp_data else 0.0,
            'uncertainty':     round(uncertainty, 3),
            'rsi':             round(row.get('rsi14', 50) or 50, 1),
            'active_events':   active_events,
            'top_prediction':  predictions[0]['event'] if predictions else None,
            'pred_lag':        predictions[0]['lag']   if predictions else None,
        })

    results.sort(key=lambda x: x['ebp'], reverse=True)
    avg_ebp = statistics.mean([r['ebp'] for r in results]) if results else 0
    market_decision = ('PROCEED'  if avg_ebp > EBP_CONDITIONAL else
                       'CAUTIOUS' if avg_ebp > EBP_AVOID       else 'WAIT')

    top_opps  = [r for r in results if r['decision'] in ('HIGH_CONVICTION', 'CONDITIONAL')]
    avoid_lst = [r['symbol'] for r in results if r['decision'] == 'AVOID']

    return {
        'elapsed_sec':           round(time.time() - t0, 2),
        'n_stocks':              len(results),
        'market_regime':         regime,
        'market_decision':       market_decision,
        'avg_market_ebp':        round(avg_ebp, 4),
        'decision_distribution': dict(decision_dist),
        'top_opportunities':     top_opps[:20],
        'avoid_list':            avoid_lst[:10],
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 2 — opportunity_scan
# ═══════════════════════════════════════════════════════════════════════════

def cmd_opportunity_scan(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=25, max_bars=120)
    regime     = _detect_regime(indicators)
    ind_map    = {r['symbol']: r for r in indicators}

    opportunities = []
    for sym, bars in ohlcv.items():
        row    = ind_map.get(sym, {'symbol': sym, 'rsi14': 50, 'sector': 'Unknown', 'volume_ratio': 1})
        energy = _compute_energy_quick(bars)
        if not energy:
            continue
        active_events, predictions = _detect_causal_events(energy)
        uncertainty = _compute_uncertainty(energy, row, active_events)
        ebp_data    = _compute_ebp(row, energy, active_events, predictions, regime)
        if not ebp_data:
            continue
        decision = _classify_decision(ebp_data, uncertainty)
        kelly    = _kelly_size(ebp_data['p_success'])

        opp_score = (ebp_data['ebp']             * 0.40 +
                     ebp_data['causal_confidence'] * 0.30 +
                     (1 - uncertainty)             * 0.30)

        opportunities.append({
            'symbol':               sym,
            'sector':               row.get('sector', 'Unknown'),
            'decision':             decision,
            'opp_score':            round(opp_score, 4),
            'ebp':                  ebp_data['ebp'],
            'uncertainty':          round(uncertainty, 3),
            'kelly_pct':            round(kelly * 100, 1),
            'p_success':            ebp_data['p_success'],
            'causal_confidence':    ebp_data['causal_confidence'],
            'structural_stability': ebp_data['structural_stability'],
            'instability_risk':     ebp_data['instability_risk'],
            'tail_risk':            ebp_data['tail_risk'],
            'active_events':        active_events,
            'predicted_next':       predictions[0]['event'] if predictions else None,
            'pred_p':               round(predictions[0]['p'], 3) if predictions else 0,
            'pred_lag':             predictions[0]['lag']   if predictions else None,
            'rsi':                  round(row.get('rsi14', 50) or 50, 1),
            'dominant_energy':      max(energy, key=energy.get),
        })

    opportunities.sort(key=lambda x: x['opp_score'], reverse=True)

    high_conviction = [o for o in opportunities if o['decision'] == 'HIGH_CONVICTION']
    conditional     = [o for o in opportunities if o['decision'] == 'CONDITIONAL']
    fragile         = [o for o in opportunities if o['decision'] == 'FRAGILE']
    unstable        = [o for o in opportunities if o['decision'] in ('UNSTABLE', 'AVOID')]

    sector_counts = defaultdict(int)
    for o in high_conviction + conditional:
        sector_counts[o['sector']] += 1

    return {
        'elapsed_sec':          round(time.time() - t0, 2),
        'n_scanned':            len(opportunities),
        'market_regime':        regime,
        'n_high_conviction':    len(high_conviction),
        'n_conditional':        len(conditional),
        'n_fragile':            len(fragile),
        'n_avoid':              len(unstable),
        'high_conviction':      high_conviction[:15],
        'conditional':          conditional[:15],
        'fragile':              fragile[:10],
        'sector_concentration': dict(sector_counts),
        'top_20':               opportunities[:20],
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 3 — portfolio_optimize
# ═══════════════════════════════════════════════════════════════════════════

def cmd_portfolio_optimize(params):
    t0              = time.time()
    max_positions   = params.get('max_positions', 15)
    max_sector_pct  = params.get('max_sector_pct', 0.30)
    total_cap_pct   = params.get('total_capital_pct', 0.60)

    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=25, max_bars=120)
    regime     = _detect_regime(indicators)
    ind_map    = {r['symbol']: r for r in indicators}

    candidates = []
    for sym, bars in ohlcv.items():
        row    = ind_map.get(sym, {'symbol': sym, 'rsi14': 50, 'sector': 'Unknown'})
        energy = _compute_energy_quick(bars)
        if not energy:
            continue
        active_events, predictions = _detect_causal_events(energy)
        uncertainty = _compute_uncertainty(energy, row, active_events)
        ebp_data    = _compute_ebp(row, energy, active_events, predictions, regime)
        if not ebp_data or ebp_data['ebp'] < EBP_FRAGILE:
            continue
        decision = _classify_decision(ebp_data, uncertainty)
        if decision == 'AVOID':
            continue

        candidates.append({
            'symbol':    sym,
            'sector':    row.get('sector', 'Unknown'),
            'decision':  decision,
            'ebp':       ebp_data['ebp'],
            'p_success': ebp_data['p_success'],
            'tail_risk': ebp_data['tail_risk'],
            'kelly':     _kelly_size(ebp_data['p_success']),
            'uncertainty': uncertainty,
            'instability': ebp_data['instability_risk'],
        })

    candidates.sort(key=lambda x: x['ebp'], reverse=True)

    portfolio         = []
    sector_alloc      = defaultdict(float)
    total_allocated   = 0.0

    STATE_MULT = {
        'HIGH_CONVICTION': 1.00,
        'CONDITIONAL':     0.65,
        'FRAGILE':         0.35,
        'TRANSITIONAL':    0.25,
    }

    for c in candidates:
        if len(portfolio) >= max_positions or total_allocated >= total_cap_pct:
            break
        sec = c['sector']
        if sector_alloc[sec] >= max_sector_pct:
            continue

        state_mult   = STATE_MULT.get(c['decision'], 0.20)
        uncert_mult  = 1.0 - c['uncertainty'] * 0.50
        regime_mult  = {'BULL': 1.0, 'CALM': 0.75, 'STRESS': 0.55, 'CRISIS': 0.30, 'NEUTRAL': 0.80}.get(regime, 0.80)
        final_alloc  = min(0.12, max(0.02, c['kelly'] * state_mult * uncert_mult * regime_mult))
        remaining    = total_cap_pct - total_allocated
        final_alloc  = min(final_alloc, remaining)

        portfolio.append({
            'symbol':        c['symbol'],
            'sector':        sec,
            'decision':      c['decision'],
            'allocation_pct':round(final_alloc * 100, 1),
            'ebp':           round(c['ebp'], 4),
            'p_success':     c['p_success'],
            'kelly_raw_pct': round(c['kelly'] * 100, 1),
            'uncertainty':   round(c['uncertainty'], 3),
            'tail_risk':     round(c['tail_risk'], 3),
        })
        sector_alloc[sec] += final_alloc
        total_allocated   += final_alloc

    avg_ebp    = statistics.mean([p['ebp']       for p in portfolio]) if portfolio else 0
    avg_p_succ = statistics.mean([p['p_success'] for p in portfolio]) if portfolio else 0
    max_sec    = max(sector_alloc.values(), default=0)
    div_score  = round(1.0 - max_sec / max(total_allocated, 1e-8), 3)

    regime_advice_map = {
        'BULL':    'سوق صاعد — توسيع التعرض مقبول حتى 75%',
        'STRESS':  'ضغط — تقليل الحجم وزيادة النقدية',
        'CRISIS':  'أزمة — حد أقصى 25%، انتظر علامات التعافي',
        'CALM':    'هادئ — حجم معتدل، ابحث عن الضغط',
        'NEUTRAL': 'محايد — معايير قياسية',
    }

    return {
        'elapsed_sec':         round(time.time() - t0, 2),
        'market_regime':       regime,
        'regime_advice':       regime_advice_map.get(regime, 'معايير قياسية'),
        'n_positions':         len(portfolio),
        'total_allocated_pct': round(total_allocated * 100, 1),
        'cash_reserve_pct':    round((1.0 - total_allocated) * 100, 1),
        'avg_portfolio_ebp':   round(avg_ebp, 4),
        'avg_p_success':       round(avg_p_succ, 3),
        'diversification_score': div_score,
        'portfolio':           portfolio,
        'sector_allocation':   {k: round(v * 100, 1) for k, v in sector_alloc.items()},
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 4 — uncertainty_map
# ═══════════════════════════════════════════════════════════════════════════

def cmd_uncertainty_map(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=15, max_bars=80)

    stock_list   = []
    sector_vals  = defaultdict(list)

    for row in indicators:
        sym   = row['symbol']
        bars  = ohlcv.get(sym, [])
        energy = _compute_energy_quick(bars) if len(bars) >= 10 else _energy_from_indicators(row)

        active_events, _ = _detect_causal_events(energy)
        unc = _compute_uncertainty(energy, row, active_events)
        sec = row.get('sector', 'Unknown')

        signal = energy.get('MOMENTUM_ENERGY', 0) + energy.get('PANIC_ENERGY', 0)
        noise  = min(1.0, len(active_events) / 8.0)

        stock_list.append({
            'symbol':                sym,
            'sector':                sec,
            'uncertainty':           round(unc, 3),
            'confidence_decay':      round(min(1.0, noise * 0.60 + (1 - min(1.0, signal)) * 0.30), 3),
            'structural_instability':round((energy.get('PANIC_ENERGY',0) + energy.get('LIQUIDITY_STRESS',0)) / 2, 3),
            'regime_ambiguity':      round(energy.get('VOLATILITY_ENERGY', 0) * 0.50, 3),
        })
        sector_vals[sec].append(unc)

    stock_list.sort(key=lambda x: x['uncertainty'], reverse=True)

    sector_summary = {}
    for sec, vals in sector_vals.items():
        avg = statistics.mean(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0
        sector_summary[sec] = {
            'avg_uncertainty': round(avg, 3),
            'std':             round(std, 3),
            'n':               len(vals),
            'level':           'HIGH' if avg > UNCERT_HIGH else 'MEDIUM' if avg > UNCERT_MEDIUM else 'LOW',
        }

    all_unc = [s['uncertainty'] for s in stock_list]
    market_unc = statistics.mean(all_unc) if all_unc else 0.50

    sec_sorted       = sorted(sector_summary.items(), key=lambda x: x[1]['avg_uncertainty'])
    most_certain     = [s[0] for s in sec_sorted[:3]]
    most_uncertain   = [s[0] for s in sec_sorted[-3:]]

    return {
        'elapsed_sec':                round(time.time() - t0, 2),
        'n_stocks':                   len(stock_list),
        'market_uncertainty':         round(market_unc, 3),
        'market_unc_level':           'HIGH' if market_unc > UNCERT_HIGH else 'MEDIUM' if market_unc > UNCERT_MEDIUM else 'LOW',
        'sector_uncertainty':         sector_summary,
        'most_certain_sectors':       most_certain,
        'most_uncertain_sectors':     most_uncertain,
        'highest_uncertainty_stocks': stock_list[:10],
        'lowest_uncertainty_stocks':  stock_list[-10:][::-1],
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 5 — regime_decisions
# ═══════════════════════════════════════════════════════════════════════════

REGIME_POLICIES = {
    'BULL': {
        'max_exposure':     0.75, 'min_ebp': 0.18, 'max_uncertainty': 0.50,
        'position_mult':    1.00, 'preferred': ['MOMENTUM_SURGE', 'TREND_BREAKOUT'],
        'avoid_states':     ['AVOID'],
        'description':      'سوق صاعد: توسيع التعرض، متابعة الزخم',
        'sizing_guideline': 'كيلي كامل مضروب × 0.25',
    },
    'STRESS': {
        'max_exposure':     0.45, 'min_ebp': 0.26, 'max_uncertainty': 0.38,
        'position_mult':    0.60, 'preferred': ['RECOVERY_ONSET', 'VOL_COMPRESSION'],
        'avoid_states':     ['UNSTABLE', 'AVOID'],
        'description':      'سوق ضغط: تقليل التعرض، انتظر هدوء الاضطراب',
        'sizing_guideline': 'كيلي × 0.15، حد أقصى 7% للمركز',
    },
    'CRISIS': {
        'max_exposure':     0.25, 'min_ebp': 0.38, 'max_uncertainty': 0.28,
        'position_mult':    0.30, 'preferred': ['RECOVERY_ONSET'],
        'avoid_states':     ['UNSTABLE', 'FRAGILE', 'TRANSITIONAL', 'AVOID'],
        'description':      'أزمة: حد أدنى التعرض، تأكيد التعافي أولاً',
        'sizing_guideline': 'كيلي × 0.08، حد أقصى 3% للمركز',
    },
    'CALM': {
        'max_exposure':     0.55, 'min_ebp': 0.20, 'max_uncertainty': 0.45,
        'position_mult':    0.75, 'preferred': ['VOL_COMPRESSION', 'MOMENTUM_SURGE'],
        'avoid_states':     ['AVOID'],
        'description':      'سوق هادئ: حجم معتدل، ابحث عن الضغط المنخفض',
        'sizing_guideline': 'كيلي × 0.20',
    },
    'NEUTRAL': {
        'max_exposure':     0.50, 'min_ebp': 0.22, 'max_uncertainty': 0.42,
        'position_mult':    0.80, 'preferred': ['MOMENTUM_SURGE', 'REVERSAL_ONSET'],
        'avoid_states':     ['UNSTABLE', 'AVOID'],
        'description':      'سوق محايد: معايير قياسية',
        'sizing_guideline': 'كيلي × 0.22',
    },
}

def cmd_regime_decisions(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=20, max_bars=80)
    regime     = _detect_regime(indicators)
    policy     = REGIME_POLICIES.get(regime, REGIME_POLICIES['NEUTRAL'])
    ind_map    = {r['symbol']: r for r in indicators}

    qualifying = []
    for sym, bars in ohlcv.items():
        row    = ind_map.get(sym, {'symbol': sym, 'rsi14': 50, 'sector': 'Unknown'})
        energy = _compute_energy_quick(bars)
        if not energy:
            continue
        active_events, predictions = _detect_causal_events(energy)
        uncertainty = _compute_uncertainty(energy, row, active_events)
        ebp_data    = _compute_ebp(row, energy, active_events, predictions, regime)
        if not ebp_data:
            continue
        decision = _classify_decision(ebp_data, uncertainty)

        if decision in policy['avoid_states']:
            continue
        if ebp_data['ebp']  < policy['min_ebp']:
            continue
        if uncertainty      > policy['max_uncertainty']:
            continue

        qualifying.append({
            'symbol':        sym,
            'sector':        row.get('sector', 'Unknown'),
            'decision':      decision,
            'ebp':           round(ebp_data['ebp'], 4),
            'uncertainty':   round(uncertainty, 3),
            'p_success':     ebp_data['p_success'],
            'active_events': active_events,
        })

    qualifying.sort(key=lambda x: x['ebp'], reverse=True)

    return {
        'elapsed_sec':      round(time.time() - t0, 2),
        'current_regime':   regime,
        'current_policy':   policy,
        'n_qualifying':     len(qualifying),
        'qualifying_stocks':qualifying[:20],
        'all_policies':     REGIME_POLICIES,
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 6 — inaction_analysis
# ═══════════════════════════════════════════════════════════════════════════

def cmd_inaction_analysis(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=15, max_bars=60)
    regime     = _detect_regime(indicators)

    energy_acc = defaultdict(list)
    for row in indicators:
        sym   = row['symbol']
        bars  = ohlcv.get(sym, [])
        energy = _compute_energy_quick(bars) if len(bars) >= 10 else _energy_from_indicators(row)
        for k, v in energy.items():
            energy_acc[k].append(v)

    mkt = {k: round(statistics.mean(v), 3) for k, v in energy_acc.items() if v}

    reasons_wait    = []
    reasons_proceed = []

    avg_panic = mkt.get('PANIC_ENERGY', 0)
    if avg_panic > MEDIUM_E:
        reasons_wait.append({
            'signal':    'HIGH_MARKET_PANIC',
            'value':     avg_panic,
            'threshold': MEDIUM_E,
            'message':   f'ذعر واسع ({avg_panic:.3f} > {MEDIUM_E}) — انتظر هدوء الأسعار',
        })

    avg_liq = mkt.get('LIQUIDITY_STRESS', 0)
    if avg_liq > MEDIUM_E:
        reasons_wait.append({
            'signal':    'LIQUIDITY_STRESS',
            'value':     avg_liq,
            'threshold': MEDIUM_E,
            'message':   f'سيولة منخفضة ({avg_liq:.3f}) — تكلفة التداول مرتفعة',
        })

    avg_mom = mkt.get('MOMENTUM_ENERGY', 0)
    avg_exh = mkt.get('EXHAUSTION_ENERGY', 0)
    if abs(avg_mom - avg_exh) < 0.05 and avg_mom > MEDIUM_E:
        reasons_wait.append({
            'signal':    'CONFLICTING_SIGNALS',
            'value':     round((avg_mom + avg_exh) / 2, 3),
            'threshold': 0.0,
            'message':   'توازن زخم/إنهاك — إشارات متعارضة لا يمكن البت فيها',
        })

    if regime == 'CRISIS':
        reasons_wait.append({
            'signal':    'CRISIS_REGIME',
            'value':     1.0,
            'threshold': 1.0,
            'message':   'السوق في أزمة — أوقف الشراء حتى تتأكد علامات التعافي',
        })

    avg_vol_e = mkt.get('VOLATILITY_ENERGY', 0)
    if avg_vol_e > HIGH_E:
        reasons_proceed.append({
            'signal':  'COMPRESSED_VOLATILITY',
            'value':   avg_vol_e,
            'message': f'تقلب مضغوط ({avg_vol_e:.3f}) — طاقة مخزّنة جاهزة للإطلاق',
        })
    if avg_mom > HIGH_E and avg_panic < LOW_E:
        reasons_proceed.append({
            'signal':  'STRONG_CLEAN_MOMENTUM',
            'value':   avg_mom,
            'message': f'زخم قوي ({avg_mom:.3f}) بدون ذعر — بيئة ملائمة',
        })
    if regime == 'BULL':
        reasons_proceed.append({
            'signal':  'BULL_REGIME',
            'value':   1.0,
            'message': 'سوق صاعد — الريجيم داعم للقرارات الهجومية',
        })

    nw = len(reasons_wait)
    np_ = len(reasons_proceed)
    if nw >= 2 and np_ == 0:
        rec, conf = 'WAIT', 0.82
    elif nw >= 1 and np_ == 0:
        rec, conf = 'CAUTIOUS', 0.65
    elif np_ >= 2 and nw == 0:
        rec, conf = 'PROCEED', 0.82
    elif np_ >= 1 and nw <= 1:
        rec, conf = 'PROCEED_WITH_CAUTION', 0.60
    else:
        rec, conf = 'CAUTIOUS', 0.50

    return {
        'elapsed_sec':        round(time.time() - t0, 2),
        'market_regime':      regime,
        'market_energy':      mkt,
        'recommendation':     rec,
        'confidence':         round(conf, 2),
        'inaction_score':     round(nw / max(nw + np_, 1), 2),
        'reasons_to_wait':    reasons_wait,
        'reasons_to_proceed': reasons_proceed,
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 7 — failure_analysis
# ═══════════════════════════════════════════════════════════════════════════

def cmd_failure_analysis(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=40, max_bars=120)
    ind_map    = {r['symbol']: r for r in indicators}

    failures = []
    successes = []
    MAX_RECORDS = 4000

    for sym, bars in ohlcv.items():
        if len(bars) < 40:
            continue
        row = ind_map.get(sym, {'symbol': sym, 'rsi14': 50, 'sector': 'Unknown'})
        for i in range(8, min(40, len(bars) - 7)):
            if len(failures) + len(successes) >= MAX_RECORDS:
                break
            past = bars[max(0, i - 18):i + 1]
            energy = _compute_energy_quick(past)
            if not energy:
                continue
            active_events, predictions = _detect_causal_events(energy)
            ebp_data = _compute_ebp(row, energy, active_events, predictions, 'BULL')
            if not ebp_data or ebp_data['ebp'] < EBP_CONDITIONAL:
                continue

            entry = bars[i]['close']
            exit_ = bars[i + 5]['close']
            ret   = (exit_ - entry) / max(entry, 1e-8)

            rec = {
                'ebp':        ebp_data['ebp'],
                'p_success':  ebp_data['p_success'],
                'actual_ret': round(ret, 4),
                'tail_risk':  ebp_data['tail_risk'],
                'instability':ebp_data['instability_risk'],
                'uncertainty':_compute_uncertainty(energy, row, active_events),
                'panic':      energy.get('PANIC_ENERGY', 0),
                'liq':        energy.get('LIQUIDITY_STRESS', 0),
                'exhaustion': energy.get('EXHAUSTION_ENERGY', 0),
            }
            (successes if ret > 0.01 else failures).append(rec)

    if not failures and not successes:
        return {'elapsed_sec': round(time.time() - t0, 2), 'error': 'لا توجد بيانات كافية'}

    def avg(lst, k):
        vals = [r[k] for r in lst if r[k] is not None]
        return round(statistics.mean(vals), 4) if vals else 0

    f_prof = {k: avg(failures,  k) for k in ('ebp','panic','liq','exhaustion','instability','uncertainty','actual_ret')}
    s_prof = {k: avg(successes, k) for k in ('ebp','panic','liq','exhaustion','instability','uncertainty','actual_ret')}

    discriminants = []
    for feat, fname in [('panic','PANIC_ENERGY'),('liq','LIQUIDITY_STRESS'),
                        ('instability','INSTABILITY_RISK'),('uncertainty','UNCERTAINTY'),
                        ('exhaustion','EXHAUSTION_ENERGY')]:
        delta = f_prof[feat] - s_prof[feat]
        if abs(delta) > 0.025:
            discriminants.append({
                'feature':  fname,
                'fail_avg': f_prof[feat],
                'succ_avg': s_prof[feat],
                'delta':    round(delta, 3),
                'direction':'مرتفع عند الفشل' if delta > 0 else 'منخفض عند الفشل',
            })
    discriminants.sort(key=lambda x: abs(x['delta']), reverse=True)

    n_f = len(failures)
    n_s = len(successes)

    failure_types = [
        {'type': 'INSTABILITY_OVERWHELM',
         'n':    sum(1 for f in failures if f['instability'] > 0.40),
         'desc': 'الاضطراب تغلّب على البنية — EBP مرتفع لكن السعر عكس التوقع'},
        {'type': 'TIMING_DEGRADATION',
         'n':    sum(1 for f in failures if f['ebp'] > EBP_HIGH_CONVICTION),
         'desc': 'إشارة قوية لكن توقيت الدخول متأخر — حركة السعر انتهت'},
        {'type': 'NOISE_DOMINANCE',
         'n':    sum(1 for f in failures if f['uncertainty'] > UNCERT_MEDIUM),
         'desc': 'عدم اليقين العالي أبطل الإشارة السببية'},
        {'type': 'LIQUIDITY_TRAP',
         'n':    sum(1 for f in failures if f['liq'] > 0.30),
         'desc': 'السيولة المنخفضة رفعت تكلفة الخروج وضاعفت الخسارة'},
    ]

    recs = []
    if f_prof['panic'] > 0.15:      recs.append('تجنّب الدخول عند PANIC_ENERGY > 0.15')
    if f_prof['liq'] > 0.25:        recs.append('اشترط LIQUIDITY_STRESS < 0.25 قبل الدخول')
    if f_prof['uncertainty'] > 0.42: recs.append('قلّل الحجم بنسبة 50% عند uncertainty > 0.42')
    if f_prof['exhaustion'] > 0.30:  recs.append('لا تدخل عند EXHAUSTION_ENERGY > 0.30 — الحركة قاربت نهايتها')

    return {
        'elapsed_sec':         round(time.time() - t0, 2),
        'n_analyzed':          n_f + n_s,
        'n_failures':          n_f,
        'n_successes':         n_s,
        'success_rate':        round(n_s / max(n_f + n_s, 1), 3),
        'failure_profile':     f_prof,
        'success_profile':     s_prof,
        'discriminants':       discriminants,
        'failure_types':       failure_types,
        'recommendations':     recs,
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 8 — adaptive_thresholds
# ═══════════════════════════════════════════════════════════════════════════

def cmd_adaptive_thresholds(params):
    t0         = time.time()
    indicators = _load_indicators_now()
    ohlcv      = _load_ohlcv_all(min_bars=40, max_bars=120)
    ind_map    = {r['symbol']: r for r in indicators}
    MAX_SIGNALS = 800

    # EBP threshold calibration
    threshold_results = {}
    for ebp_thresh in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        hits = 0; total = 0
        for sym, bars in ohlcv.items():
            if total >= MAX_SIGNALS: break
            if len(bars) < 35: continue
            row = ind_map.get(sym, {'symbol': sym, 'rsi14': 50})
            for i in range(8, min(30, len(bars) - 6)):
                past = bars[max(0, i-18):i+1]
                energy = _compute_energy_quick(past)
                if not energy: continue
                active_events, preds = _detect_causal_events(energy)
                ebp_data = _compute_ebp(row, energy, active_events, preds, 'BULL')
                if not ebp_data or ebp_data['ebp'] < ebp_thresh: continue
                ret = (bars[i+5]['close'] - bars[i]['close']) / max(bars[i]['close'], 1e-8)
                if ret > 0.01: hits += 1
                total += 1
                if total >= MAX_SIGNALS: break
        threshold_results[str(ebp_thresh)] = {
            'win_rate':  round(hits / max(total, 1), 3),
            'n_signals': total,
            'score':     round(hits / max(total, 1) * (total ** 0.3), 3),
        }

    best_thresh = float(max(threshold_results, key=lambda t: threshold_results[t]['score']))

    # Uncertainty calibration
    unc_calibration = {}
    for unc_thresh in [0.20, 0.30, 0.40, 0.50]:
        hits = 0; total = 0
        for sym, bars in list(ohlcv.items())[:40]:
            if len(bars) < 30: continue
            row = ind_map.get(sym, {'symbol': sym, 'rsi14': 50})
            for i in range(5, min(25, len(bars) - 6)):
                past = bars[max(0, i-15):i+1]
                energy = _compute_energy_quick(past)
                if not energy: continue
                active_events, _ = _detect_causal_events(energy)
                unc = _compute_uncertainty(energy, row, active_events)
                if unc > unc_thresh: continue
                ret = (bars[i+5]['close'] - bars[i]['close']) / max(bars[i]['close'], 1e-8)
                if ret > 0.01: hits += 1
                total += 1
        unc_calibration[str(unc_thresh)] = {
            'win_rate': round(hits / max(total, 1), 3),
            'n_trials': total,
        }

    best_unc_thresh = float(max(unc_calibration, key=lambda u: unc_calibration[u]['win_rate']))

    return {
        'elapsed_sec':          round(time.time() - t0, 2),
        'ebp_calibration':      threshold_results,
        'best_ebp_threshold':   best_thresh,
        'unc_calibration':      unc_calibration,
        'best_unc_threshold':   best_unc_thresh,
        'recommended': {
            'ebp_high_conviction': round(best_thresh + 0.10, 2),
            'ebp_conditional':     round(best_thresh, 2),
            'ebp_fragile':         round(max(0.05, best_thresh - 0.08), 2),
            'max_uncertainty':     round(best_unc_thresh, 2),
            'kelly_fraction':      0.25,
            'max_position_pct':    12.0,
            'max_sector_pct':      30.0,
        },
        'current': {
            'EBP_HIGH_CONVICTION': EBP_HIGH_CONVICTION,
            'EBP_CONDITIONAL':     EBP_CONDITIONAL,
            'EBP_FRAGILE':         EBP_FRAGILE,
            'UNCERT_LOW':          UNCERT_LOW,
            'UNCERT_MEDIUM':       UNCERT_MEDIUM,
            'UNCERT_HIGH':         UNCERT_HIGH,
        },
    }

# ═══════════════════════════════════════════════════════════════════════════
# COMMAND 9 — decision_full
# ═══════════════════════════════════════════════════════════════════════════

def cmd_decision_full(params):
    t0 = time.time()
    results = {}
    for name, fn in [
        ('decision_now',      cmd_decision_now),
        ('opportunity_scan',  cmd_opportunity_scan),
        ('portfolio',         cmd_portfolio_optimize),
        ('uncertainty',       cmd_uncertainty_map),
        ('regime_decisions',  cmd_regime_decisions),
        ('inaction',          cmd_inaction_analysis),
        ('failure_analysis',  cmd_failure_analysis),
        ('thresholds',        cmd_adaptive_thresholds),
    ]:
        try:
            results[name] = fn({})
        except Exception as e:
            results[name] = {'error': str(e)}
    results['elapsed_sec'] = round(time.time() - t0, 2)
    return results

# ── COMMANDS registry ──────────────────────────────────────────────────────

COMMANDS = {
    'decision_now':        cmd_decision_now,
    'opportunity_scan':    cmd_opportunity_scan,
    'portfolio_optimize':  cmd_portfolio_optimize,
    'uncertainty_map':     cmd_uncertainty_map,
    'regime_decisions':    cmd_regime_decisions,
    'inaction_analysis':   cmd_inaction_analysis,
    'failure_analysis':    cmd_failure_analysis,
    'adaptive_thresholds': cmd_adaptive_thresholds,
    'decision_full':       cmd_decision_full,
}

# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    try:
        if len(sys.argv) >= 2 and sys.argv[1] in COMMANDS:
            command = sys.argv[1]
            params  = json.loads(sys.argv[2]) if len(sys.argv) >= 3 else {}
        else:
            req     = json.loads(sys.stdin.read())
            command = req.get('command', 'decision_now')
            params  = req.get('params', {})
    except Exception:
        print(json.dumps({'error': 'Invalid JSON input'}))
        sys.exit(1)

    fn = COMMANDS.get(command)
    if not fn:
        print(json.dumps({'error': f'Unknown command: {command}'}))
        sys.exit(1)

    try:
        result = fn(params)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()[-600:]}))
        sys.exit(1)
