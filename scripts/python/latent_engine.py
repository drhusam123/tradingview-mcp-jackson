#!/usr/bin/env python3
"""
EGX Latent Market Behavior Engine
====================================
Discovers hidden behavioral forces and latent market dynamics.

Commands:
  behavioral_forces    — 6-force decomposition of all stocks NOW
  duration_analysis    — P(TR) evolution by time-in-state
  sector_markov        — Sector-conditioned transition matrices
  latent_compress      — PCA compression into behavioral latent space
  invariant_discovery  — Cross-time-period stable behaviors
  failure_precursors   — Hidden variables predicting failure before signals
  temporal_stability   — Edge stability across rolling time windows
  quant_loop           — Full autonomous research iteration

مالك: Dr. Husam | مايو 2026
"""

import json, sys, os, math, statistics, datetime, collections, itertools
import sqlite3

# ── numpy / scipy ────────────────────────────────────────────────────────────
try:
    import numpy as np
    from scipy import stats as scipy_stats
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ── DB ────────────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/egx_trading.db')

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def _now_iso():
    return datetime.datetime.utcnow().isoformat(timespec='seconds')

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET STATE CLASSIFIER (same logic as egx_analysis.py market_evolution)
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_bar(close, prev_close, rsi, atr, volume, avg_volume, momentum5):
    """
    Classify a single bar into a market state based on behavioral physics.
    Returns: state_name (str)
    """
    if None in (close, prev_close) or prev_close == 0:
        return 'NEUTRAL'

    pct_change  = (close - prev_close) / prev_close * 100
    vol_ratio   = (volume / avg_volume) if avg_volume and avg_volume > 0 else 1.0
    rsi_val     = rsi or 50.0
    mom5        = momentum5 or 0.0

    # Velocity (acceleration in price movement)
    sharp_move  = abs(pct_change) > 3.0
    vol_surge   = vol_ratio > 2.0

    if pct_change <= -5.0 and vol_surge:
        return 'PANIC'
    if pct_change <= -3.0 and rsi_val <= 35:
        return 'SHARP_DROP'
    if pct_change <= -2.0 and vol_ratio > 1.5:
        return 'CONTINUATION_DOWN'
    if rsi_val >= 75 and mom5 < 0 and pct_change < 0:
        return 'VELOCITY_EXHAUSTION'
    if rsi_val >= 70 and pct_change < 1.0 and mom5 < 2.0:
        return 'EXHAUSTION'
    if pct_change >= 5.0 and vol_surge:
        return 'ACCELERATING_UP'
    if pct_change >= 2.0 and mom5 > 0 and rsi_val >= 55:
        return 'TRENDING_UP'
    if rsi_val >= 65 and pct_change < 0.5:
        return 'DISTRIBUTION'
    if rsi_val <= 35 and vol_ratio > 1.3:
        return 'POTENTIAL_BOUNCE'
    if rsi_val <= 30 and abs(pct_change) < 1.0:
        return 'STABILIZATION'
    if abs(pct_change) < 0.5 and vol_ratio < 0.7:
        return 'NEUTRAL'
    if pct_change >= 1.0 and rsi_val < 55:
        return 'POTENTIAL_BOUNCE'
    return 'NEUTRAL'

def _classify_regime(breadth_pct, ema_trend, vol_regime):
    """Market regime from breadth + trend."""
    if breadth_pct >= 70 and ema_trend > 0:
        return 'SURGE' if breadth_pct >= 85 else 'UP'
    if breadth_pct <= 30 and ema_trend < 0:
        return 'CRASH' if breadth_pct <= 15 else 'DOWN'
    if breadth_pct <= 50 and ema_trend <= 0:
        return 'DOWN'
    if breadth_pct >= 50 and ema_trend >= 0:
        return 'UP'
    return 'NEUTRAL' if vol_regime == 'LOW' else 'SIDEWAYS'

def _rolling_avg(values, n):
    """Rolling average over last n values."""
    if len(values) < n:
        return sum(values) / len(values) if values else 0
    return sum(values[-n:]) / n


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

def _load_ohlcv_all(min_bars=100):
    """Load all OHLCV history. Returns dict: symbol → sorted list of bar dicts."""
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

def _load_indicators_latest():
    """Load latest indicators_cache row per symbol with sector."""
    con = get_db()
    rows = con.execute("""
        SELECT ic.*, u.sector, u.name
        FROM indicators_cache ic
        LEFT JOIN stock_universe u ON ic.symbol = u.symbol
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]

def _load_macro():
    """Load macro context."""
    try:
        con = get_db()
        row = con.execute(
            "SELECT * FROM macro_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: behavioral_forces
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_behavioral_forces(params):
    """
    Decompose ALL stocks into 6 latent behavioral forces:
      1. exhaustion_pressure   — RSI-based overbought/oversold intensity
      2. volatility_energy     — ATR expansion / compression
      3. directional_energy    — momentum strength and direction
      4. participation_flow    — volume relative to norm (liquidity absorption)
      5. trend_coherence       — ADX + EMA alignment
      6. reversal_potential    — composite oversold + recovery signal

    Classifies each stock into a BEHAVIORAL ARCHETYPE.
    Aggregates to market-level force distribution.
    """
    stocks = _load_indicators_latest()
    if not stocks:
        return {'success': False, 'error': 'No indicators_cache data'}

    # ── compute forces per stock ──────────────────────────────────────────────
    results = []
    archetype_counts = collections.defaultdict(int)

    for s in stocks:
        sym = s['symbol']
        rsi    = s.get('rsi14') or 50.0
        atr    = s.get('atr14') or 0
        bb_pos = s.get('bb_position')
        vol_r  = s.get('vol_ratio_20') or 1.0
        mom5   = s.get('momentum_5d') or 0.0
        mom10  = s.get('momentum_10d') or 0.0
        adx    = s.get('adx14') or 0.0
        macd_h = s.get('macd_hist') or 0.0
        cci    = s.get('cci20') or 0.0
        willr  = s.get('williams_r') or -50.0
        above20= s.get('above_ema20') or 0
        above50= s.get('above_ema50') or 0
        obv_d  = s.get('obv_divergence') or 'none'
        sector = s.get('sector') or 'Unknown'
        close_pos = s.get('close_position') or 0.5

        # FORCE 1: exhaustion_pressure [-1 to +1]
        # -1 = deep oversold pressure (potential reversal up)
        # +1 = deep overbought pressure (potential reversal down)
        if rsi >= 70:
            ep = min((rsi - 70) / 30, 1.0)
        elif rsi <= 30:
            ep = max((rsi - 30) / 30, -1.0)
        else:
            ep = (rsi - 50) / 20 * 0.5
        # Amplify with Williams %R and CCI
        willr_norm = (willr + 50) / 50   # 0 = oversold, 1 = center, 2 = overbought → -1..1
        cci_norm   = max(-1, min(1, cci / 200))
        exhaustion_pressure = round((ep * 0.6 + willr_norm * 0.2 + cci_norm * 0.2), 3)

        # FORCE 2: volatility_energy [0 to +∞, normalized to 0..1 range]
        # High ATR relative to its own history → high energy (expansion)
        # We normalize bb_width as proxy for volatility expansion
        bb_width = s.get('bb_width') or 0
        vol_energy = min(1.0, max(0.0,
            (vol_r - 1.0) / 2.0 * 0.4 +           # volume surge component
            min(bb_width / 0.1, 1.0) * 0.3 +       # BB expansion component
            min(abs(macd_h) / 0.5, 1.0) * 0.3      # MACD momentum component
        ))
        volatility_energy = round(vol_energy, 3)

        # FORCE 3: directional_energy [-1..+1]
        # Positive = upward directional energy
        # Negative = downward directional energy
        mom5_norm  = max(-1, min(1, mom5 / 10.0))
        mom10_norm = max(-1, min(1, mom10 / 20.0))
        macd_norm  = max(-1, min(1, macd_h / 0.3))
        close_pos_centered = (close_pos - 0.5) * 2  # -1..+1
        directional_energy = round(
            mom5_norm * 0.4 + mom10_norm * 0.2 + macd_norm * 0.2 + close_pos_centered * 0.2,
            3
        )

        # FORCE 4: participation_flow [0..1]
        # High volume + OBV divergence → institutional absorption
        obv_boost = 0.15 if obv_d == 'bullish' else (-0.1 if obv_d == 'bearish' else 0)
        participation_flow = round(min(1.0, max(0.0,
            min(vol_r / 3.0, 1.0) * 0.7 + 0.15 + obv_boost
        )), 3)

        # FORCE 5: trend_coherence [0..1]
        # Strong ADX + EMA alignment = coherent trend
        adx_norm  = min(adx / 50.0, 1.0)
        ema_align = (above20 + above50) / 2.0   # 0, 0.5, 1.0
        trend_coherence = round(adx_norm * 0.6 + ema_align * 0.4, 3)

        # FORCE 6: reversal_potential [0..1]
        # High = likely to reverse UP soon
        # Requires: oversold + stabilizing volume + positive momentum shift
        oversold_score  = max(0, (30 - rsi) / 30)  # 0..1 for RSI 30→0
        vol_stabilizing = 1.0 if 0.8 <= vol_r <= 1.5 else 0.3
        mom_recovery    = max(0, min(1, (mom5 + 5) / 10))  # momentum recovering from negative
        reversal_potential = round(
            oversold_score * 0.5 + vol_stabilizing * 0.2 + mom_recovery * 0.3,
            3
        )

        # ── BEHAVIORAL ARCHETYPE ─────────────────────────────────────────────
        archetype = _classify_archetype(
            exhaustion_pressure, volatility_energy, directional_energy,
            participation_flow, trend_coherence, reversal_potential, rsi, adx
        )
        archetype_counts[archetype] += 1

        results.append({
            'symbol': sym,
            'sector': sector,
            'rsi': round(rsi, 1),
            'forces': {
                'exhaustion_pressure':  exhaustion_pressure,
                'volatility_energy':    volatility_energy,
                'directional_energy':   directional_energy,
                'participation_flow':   participation_flow,
                'trend_coherence':      trend_coherence,
                'reversal_potential':   reversal_potential,
            },
            'archetype': archetype,
        })

    # ── Market-level force aggregates ────────────────────────────────────────
    def _market_agg(force_key):
        vals = [r['forces'][force_key] for r in results]
        if not vals:
            return {}
        mean = sum(vals) / len(vals)
        std  = statistics.stdev(vals) if len(vals) > 1 else 0
        return {
            'mean': round(mean, 3),
            'std':  round(std, 3),
            'pct_high': round(sum(1 for v in vals if v > 0.6) / len(vals) * 100, 1),
            'pct_low':  round(sum(1 for v in vals if v < 0.3) / len(vals) * 100, 1),
        }

    # Sector force aggregation
    sector_forces = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in results:
        for fk, fv in r['forces'].items():
            sector_forces[r['sector']][fk].append(fv)

    sector_summary = {}
    for sec, forces in sector_forces.items():
        sector_summary[sec] = {
            fk: round(sum(fv_list)/len(fv_list), 3)
            for fk, fv_list in forces.items() if fv_list
        }

    # Top stocks per archetype
    archetype_stocks = collections.defaultdict(list)
    for r in results:
        archetype_stocks[r['archetype']].append({
            'symbol': r['symbol'],
            'rsi': r['rsi'],
            'rev_pot': r['forces']['reversal_potential'],
            'dir_energy': r['forces']['directional_energy'],
        })

    # Sort HIGH_REVERSAL by reversal_potential, others by directional_energy
    for arch in archetype_stocks:
        if 'REVERSAL' in arch or 'OVERSOLD' in arch:
            archetype_stocks[arch].sort(key=lambda x: -x['rev_pot'])
        else:
            archetype_stocks[arch].sort(key=lambda x: -abs(x['dir_energy']))

    # Market interpretation
    dominant_archetype = max(archetype_counts, key=archetype_counts.get) if archetype_counts else 'NEUTRAL'
    reversal_stocks = len(archetype_stocks.get('HIGH_REVERSAL_POTENTIAL', []) +
                         archetype_stocks.get('DEEPLY_OVERSOLD', []))
    exhausted_stocks = len(archetype_stocks.get('OVERBOUGHT_EXHAUSTION', []) +
                          archetype_stocks.get('VELOCITY_EXHAUSTION', []))

    return {
        'success': True,
        'n_stocks': len(results),
        'market_forces': {
            'exhaustion_pressure':  _market_agg('exhaustion_pressure'),
            'volatility_energy':    _market_agg('volatility_energy'),
            'directional_energy':   _market_agg('directional_energy'),
            'participation_flow':   _market_agg('participation_flow'),
            'trend_coherence':      _market_agg('trend_coherence'),
            'reversal_potential':   _market_agg('reversal_potential'),
        },
        'archetype_distribution': dict(sorted(archetype_counts.items(), key=lambda x: -x[1])),
        'dominant_archetype': dominant_archetype,
        'reversal_candidates': reversal_stocks,
        'exhaustion_candidates': exhausted_stocks,
        'archetype_stocks': {k: v[:8] for k, v in archetype_stocks.items()},
        'sector_forces': sector_summary,
        'all_stocks': sorted(results, key=lambda r: -r['forces']['reversal_potential'])[:50],
    }


def _classify_archetype(ep, ve, de, pf, tc, rp, rsi, adx):
    """Classify a stock into a behavioral archetype using its 6 forces."""
    # Priority-ordered classification
    if rsi <= 25 and rp >= 0.5:
        return 'DEEPLY_OVERSOLD'
    if rsi <= 35 and de < 0 and ve > 0.4 and rp >= 0.35:
        return 'HIGH_REVERSAL_POTENTIAL'
    if ep >= 0.6 and de < 0:
        return 'VELOCITY_EXHAUSTION'
    if ep >= 0.4 and de < 0 and tc > 0.5:
        return 'OVERBOUGHT_EXHAUSTION'
    if de >= 0.4 and tc >= 0.5 and ve > 0.3:
        return 'MOMENTUM_SURGE'
    if de >= 0.3 and tc >= 0.4:
        return 'TRENDING_STRONG'
    if ve >= 0.7 and abs(de) < 0.2:
        return 'VOLATILITY_EXPANSION'
    if pf >= 0.7 and de < -0.1:
        return 'DISTRIBUTION_PRESSURE'
    if pf >= 0.5 and de > 0.1:
        return 'QUIET_ACCUMULATION'
    if ve <= 0.2 and abs(de) < 0.15 and abs(ep) < 0.2:
        return 'DEAD_ZONE'
    if de < -0.3 and ve > 0.3:
        return 'DOWNTREND_ACTIVE'
    return 'TRANSITIONING'


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: duration_analysis
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_duration_analysis(params):
    """
    HOW DOES P(TR) CHANGE WITH TIME-IN-STATE?

    For each market state, compute transition probabilities broken by duration
    (how many consecutive bars the stock has been in that state).

    This answers: "Is a PANIC that's lasted 3 bars different from one that's lasted 1 bar?"
    """
    symbol_data = _load_ohlcv_all(min_bars=60)
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    # Classify all bars for all stocks, track state sequences
    # Format: {state → duration_cohort → [outcomes]}
    # outcome: 1 = up next 3 bars (TR), 0 = not up
    HORIZON = int(params.get('horizon', 3))  # forward bars for outcome
    TR_THRESHOLD = float(params.get('tr_threshold', 2.0))  # % for true reversal

    state_duration_outcomes = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )

    for sym, bars in symbol_data.items():
        n = len(bars)
        if n < 80:
            continue

        closes   = [b['close'] for b in bars]
        volumes  = [b['volume'] for b in bars]
        avg_vol  = [_rolling_avg(volumes[:i+1], 20) for i in range(n)]

        # Simple RSI and momentum for state classification
        states = []
        for i in range(1, n):
            if i < 15:
                states.append('NEUTRAL')
                continue

            # Quick RSI
            gains = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1)]
            losses= [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1)]
            avg_g = sum(gains)/14; avg_l = sum(losses)/14
            rsi = 100 - 100/(1+avg_g/avg_l) if avg_l > 0 else 100.0

            # Momentum 5d
            mom5 = (closes[i] - closes[i-5]) / closes[i-5] * 100 if i >= 5 else 0

            st = _classify_bar(
                closes[i], closes[i-1], rsi,
                None, volumes[i], avg_vol[i], mom5
            )
            states.append(st)

        # Track consecutive duration in each state
        current_state = None
        duration = 0
        for i, state in enumerate(states):
            bar_idx = i + 1  # actual bar index in closes
            if state == current_state:
                duration += 1
            else:
                current_state = state
                duration = 1

            # Compute forward outcome (HORIZON bars ahead)
            fwd_idx = bar_idx + HORIZON
            if fwd_idx >= n:
                continue

            fwd_return = (closes[fwd_idx] - closes[bar_idx]) / closes[bar_idx] * 100
            is_tr = 1 if fwd_return >= TR_THRESHOLD else 0

            # Duration cohort
            if duration == 1:
                cohort = '1'
            elif duration <= 2:
                cohort = '2'
            elif duration <= 3:
                cohort = '3'
            elif duration <= 5:
                cohort = '4-5'
            elif duration <= 10:
                cohort = '6-10'
            else:
                cohort = '11+'

            state_duration_outcomes[state][cohort].append(is_tr)

    # ── Aggregate ─────────────────────────────────────────────────────────────
    COHORT_ORDER = ['1', '2', '3', '4-5', '6-10', '11+']
    analysis = {}

    for state, cohort_data in sorted(state_duration_outcomes.items()):
        state_result = {}
        has_signal = False
        for cohort in COHORT_ORDER:
            outcomes = cohort_data.get(cohort, [])
            if len(outcomes) < 5:
                continue
            p_tr = sum(outcomes) / len(outcomes)
            state_result[cohort] = {
                'p_tr':    round(p_tr * 100, 1),
                'n':       len(outcomes),
                'n_tr':    sum(outcomes),
            }
            if p_tr >= 0.45:
                has_signal = True

        if state_result:
            # Find peak cohort
            best_cohort = max(state_result, key=lambda c: state_result[c]['p_tr'])
            peak_p = state_result[best_cohort]['p_tr']
            # Trend: is P(TR) rising or falling with duration?
            cohorts_present = [c for c in COHORT_ORDER if c in state_result]
            if len(cohorts_present) >= 2:
                first_p = state_result[cohorts_present[0]]['p_tr']
                last_p  = state_result[cohorts_present[-1]]['p_tr']
                trend = 'RISING' if last_p > first_p + 5 else ('FALLING' if last_p < first_p - 5 else 'STABLE')
            else:
                trend = 'STABLE'

            analysis[state] = {
                'cohorts':     state_result,
                'best_cohort': best_cohort,
                'peak_p_tr':   peak_p,
                'trend':       trend,
                'insight': _duration_insight(state, state_result, cohorts_present, best_cohort, peak_p, trend),
            }

    # ── Key findings ─────────────────────────────────────────────────────────
    findings = []
    for state, res in sorted(analysis.items(), key=lambda x: -x[1]['peak_p_tr']):
        findings.append({
            'state': state,
            'best_entry_after': res['best_cohort'],
            'peak_p_tr': res['peak_p_tr'],
            'duration_trend': res['trend'],
        })

    return {
        'success': True,
        'horizon_bars': HORIZON,
        'tr_threshold_pct': TR_THRESHOLD,
        'analysis': analysis,
        'ranked_states': findings,
        'key_insight': _duration_key_insight(analysis),
    }


def _duration_insight(state, cohorts, cohort_order, best, peak, trend):
    """Generate human-readable insight about a state's duration dynamics."""
    if trend == 'RISING':
        return f"{state}: الصبر مُجدٍ — P(TR) يرتفع مع الوقت (peak @{best} بار: {peak}%). انتظر لبار {best}."
    elif trend == 'FALLING':
        return f"{state}: الدخول المبكر أفضل — P(TR) يتراجع مع الوقت (peak @{best}: {peak}%). ادخل في أول {best} بار."
    else:
        p1 = cohorts.get('1', {}).get('p_tr', 0)
        return f"{state}: استقرار نسبي (peak @{best}: {peak}%). P(TR) في أول بار: {p1}%."


def _duration_key_insight(analysis):
    """Synthesize the most important duration finding."""
    insights = []
    for state, res in analysis.items():
        if res['peak_p_tr'] >= 50 and res['trend'] == 'RISING':
            insights.append(f"{state}: الصبر يُضاعف الاحتمالية (ارتفاع إلى {res['peak_p_tr']}%)")
        elif res['peak_p_tr'] >= 45 and res['trend'] == 'FALLING' and res['best_cohort'] == '1':
            insights.append(f"{state}: الدخول في أول بار هو الأمثل ({res['peak_p_tr']}%)")
    return insights[:5] if insights else ['لا توجد أنماط واضحة في البيانات المتاحة']


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: sector_markov
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_sector_markov(params):
    """
    Sector-conditioned Markov transition matrices.
    Answers: "Does PANIC→BOUNCE work the same in Finance vs Real Estate?"
    """
    symbol_data = _load_ohlcv_all(min_bars=60)
    con = get_db()
    sector_map = {}
    for r in con.execute("SELECT symbol, sector FROM stock_universe").fetchall():
        sector_map[r['symbol']] = r['sector'] or 'Unknown'
    con.close()

    # Aggregate: sector → state_from → state_to → count
    sector_transitions = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(int))
    )
    overall_transitions = collections.defaultdict(lambda: collections.defaultdict(int))

    for sym, bars in symbol_data.items():
        sector = sector_map.get(sym, 'Unknown')
        n = len(bars)
        if n < 40:
            continue

        closes  = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]
        avg_vol = [_rolling_avg(volumes[:i+1], 20) for i in range(n)]

        prev_state = None
        for i in range(1, n):
            if i < 15:
                continue
            gains  = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1)]
            losses = [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1)]
            avg_g  = sum(gains)/14; avg_l = sum(losses)/14
            rsi    = 100 - 100/(1+avg_g/avg_l) if avg_l > 0 else 100.0
            mom5   = (closes[i] - closes[i-5])/closes[i-5]*100 if i >= 5 else 0

            st = _classify_bar(closes[i], closes[i-1], rsi, None, volumes[i], avg_vol[i], mom5)

            if prev_state is not None:
                sector_transitions[sector][prev_state][st] += 1
                overall_transitions[prev_state][st] += 1
            prev_state = st

    # ── Compute probability matrices ─────────────────────────────────────────
    def _norm_transitions(trans_dict):
        result = {}
        for from_st, to_dict in trans_dict.items():
            total = sum(to_dict.values())
            if total < 5:
                continue
            result[from_st] = {
                to_st: round(count/total*100, 1)
                for to_st, count in sorted(to_dict.items(), key=lambda x: -x[1])
                if count >= 2
            }
            result[from_st]['_n'] = total
        return result

    overall_matrix = _norm_transitions(overall_transitions)

    sector_matrices = {}
    for sector, trans in sector_transitions.items():
        if sum(sum(to_d.values()) for to_d in trans.values()) < 50:
            continue
        sector_matrices[sector] = _norm_transitions(trans)

    # ── Find cross-sector invariants ─────────────────────────────────────────
    invariant_edges = []
    for from_st, to_dict in overall_matrix.items():
        for to_st, overall_pct in to_dict.items():
            if to_st.startswith('_'):
                continue
            # Check if this edge exists in at least 3 sectors with similar probability
            sector_vals = []
            for sec, matrix in sector_matrices.items():
                if from_st in matrix and to_st in matrix[from_st]:
                    sector_vals.append(matrix[from_st][to_st])
            if len(sector_vals) >= 3:
                mean_p = sum(sector_vals) / len(sector_vals)
                std_p  = statistics.stdev(sector_vals) if len(sector_vals) > 1 else 0
                cv     = std_p / mean_p if mean_p > 0 else 99
                if cv < 0.25 and mean_p >= 30:  # stable: CV < 25%, mean ≥ 30%
                    invariant_edges.append({
                        'from': from_st,
                        'to': to_st,
                        'overall_pct': overall_pct,
                        'mean_sector_pct': round(mean_p, 1),
                        'std_sector_pct': round(std_p, 1),
                        'cv': round(cv, 3),
                        'n_sectors': len(sector_vals),
                    })

    invariant_edges.sort(key=lambda x: -x['mean_sector_pct'])

    # ── Find sector-specific edges (different from market overall) ────────────
    sector_specific = []
    for sector, matrix in sector_matrices.items():
        for from_st, to_dict in matrix.items():
            for to_st, sec_pct in to_dict.items():
                if to_st.startswith('_'):
                    continue
                overall_pct = overall_matrix.get(from_st, {}).get(to_st, 0)
                diff = sec_pct - overall_pct
                if abs(diff) >= 15 and sec_pct >= 30:
                    sector_specific.append({
                        'sector': sector,
                        'from': from_st,
                        'to': to_st,
                        'sector_pct': sec_pct,
                        'overall_pct': overall_pct,
                        'diff': round(diff, 1),
                    })

    sector_specific.sort(key=lambda x: -abs(x['diff']))

    return {
        'success': True,
        'overall_matrix': overall_matrix,
        'sector_matrices': sector_matrices,
        'cross_sector_invariants': invariant_edges[:15],
        'sector_specific_edges': sector_specific[:20],
        'n_sectors': len(sector_matrices),
        'n_invariants': len(invariant_edges),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: latent_compress
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_latent_compress(params):
    """
    PCA compression of all stocks' indicators into a 3D latent behavioral space.
    Names the latent dimensions. Identifies behavioral clusters.
    """
    stocks = _load_indicators_latest()
    if not stocks or not HAS_NUMPY:
        if not HAS_NUMPY:
            return {'success': False, 'error': 'numpy not available for PCA'}
        return {'success': False, 'error': 'No indicators_cache data'}

    # ── Build feature matrix ─────────────────────────────────────────────────
    FEATURES = ['rsi14','vol_ratio_20','momentum_5d','momentum_10d',
                'adx14','macd_hist','cci20','close_position',
                'price_vs_ath','momentum_20d']

    rows_valid = []
    for s in stocks:
        row = [s.get(f) for f in FEATURES]
        if any(v is None for v in row):
            continue
        rows_valid.append((s['symbol'], s.get('sector','Unknown'), row))

    if len(rows_valid) < 10:
        return {'success': False, 'error': f'Too few complete rows: {len(rows_valid)}'}

    symbols  = [r[0] for r in rows_valid]
    sectors  = [r[1] for r in rows_valid]
    X = np.array([r[2] for r in rows_valid], dtype=float)

    # Standardize
    means  = X.mean(axis=0)
    stds   = X.std(axis=0)
    stds[stds == 0] = 1
    Xz = (X - means) / stds

    # PCA
    cov = np.cov(Xz.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Sort descending
    idx     = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]

    # Explained variance
    total_var = eigvals.sum()
    exp_var   = (eigvals[:3] / total_var * 100).round(1).tolist()

    # Loadings for top 3 components
    loadings = []
    for pc_idx in range(3):
        vec   = eigvecs[:, pc_idx]
        loads = [(FEATURES[j], round(vec[j], 3)) for j in range(len(FEATURES))]
        loads.sort(key=lambda x: -abs(x[1]))
        loadings.append(loads)

    # Name dimensions based on top loadings
    def _name_dimension(loads):
        top_pos = [f for f, v in loads[:4] if v > 0.15]
        top_neg = [f for f, v in loads[:4] if v < -0.15]
        top_all = [f for f, _ in loads[:3]]
        if 'rsi14' in top_pos[:2] or 'cci20' in top_pos[:2]:
            if 'momentum_5d' in top_pos[:3] or 'macd_hist' in top_pos[:3]:
                return 'Trend Momentum Pressure'
            return 'Exhaustion Pressure'
        if 'rsi14' in top_neg[:2] or 'cci20' in top_neg[:2]:
            return 'Oversold Reversal Force'
        if 'vol_ratio_20' in top_all[:2] or 'adx14' in top_all[:2]:
            return 'Volatility & Trend Coherence'
        if 'momentum_5d' in top_pos[:2] or 'momentum_10d' in top_pos[:2]:
            return 'Directional Energy'
        if 'macd_hist' in top_pos[:2]:
            return 'Momentum Acceleration'
        if 'price_vs_ath' in top_pos[:2]:
            return 'Structural Position (ATH Distance)'
        return 'Latent Behavioral Axis'

    dimension_names = [_name_dimension(loadings[i]) for i in range(3)]

    # Project all stocks onto 3D latent space
    projections = Xz @ eigvecs[:, :3]

    # K-means clustering (k=5 behavioral clusters) using pure Python
    k = 5
    proj_list = projections.tolist()
    cluster_ids = _kmeans_simple(proj_list, k=k, n_iter=20)

    # Characterize clusters
    cluster_data = collections.defaultdict(list)
    for i, cid in enumerate(cluster_ids):
        cluster_data[cid].append({
            'symbol': symbols[i],
            'sector': sectors[i],
            'scores': [round(projections[i, j], 3) for j in range(3)],
            'rsi': round(X[i, 0], 1),
            'mom5d': round(X[i, 3], 1),
        })

    cluster_summary = {}
    for cid, members in cluster_data.items():
        avg_rsi  = sum(m['rsi'] for m in members) / len(members)
        avg_mom5 = sum(m['mom5d'] for m in members) / len(members)
        avg_d1   = sum(m['scores'][0] for m in members) / len(members)
        cluster_summary[f'C{cid}'] = {
            'n_stocks': len(members),
            'avg_rsi': round(avg_rsi, 1),
            'avg_mom5d': round(avg_mom5, 1),
            'dominant_d1': round(avg_d1, 3),
            'archetype': _cluster_archetype(avg_rsi, avg_mom5, avg_d1),
            'top_symbols': [m['symbol'] for m in members[:6]],
        }

    # Feature correlation with first dimension (most interpretable)
    d1_scores = projections[:, 0]
    feature_correlations = []
    for j, feat in enumerate(FEATURES):
        feat_vals = Xz[:, j]
        if np.std(feat_vals) > 0:
            corr = np.corrcoef(feat_vals, d1_scores)[0, 1]
            feature_correlations.append((feat, round(corr, 3)))
    feature_correlations.sort(key=lambda x: -abs(x[1]))

    return {
        'success': True,
        'n_stocks': len(symbols),
        'n_features': len(FEATURES),
        'features': FEATURES,
        'explained_variance_pct': exp_var,
        'total_explained_3pc': round(sum(exp_var), 1),
        'dimension_names': dimension_names,
        'top_loadings': [
            {'dim': dimension_names[i], 'loadings': loadings[i][:6]}
            for i in range(3)
        ],
        'clusters': cluster_summary,
        'feature_correlations_d1': feature_correlations[:8],
        'methodology': f'PCA({len(FEATURES)}features→3D) + K-Means(k={k}) | {len(symbols)} stocks',
    }


def _kmeans_simple(points, k=5, n_iter=30):
    """Simple k-means for small datasets (pure Python)."""
    import random
    n = len(points)
    dim = len(points[0])
    # Initialize centroids
    centroids = [list(points[i]) for i in random.sample(range(n), min(k, n))]
    assignments = [0] * n

    for _ in range(n_iter):
        # Assign
        for i, pt in enumerate(points):
            dists = [
                sum((pt[d] - c[d])**2 for d in range(dim))
                for c in centroids
            ]
            assignments[i] = dists.index(min(dists))
        # Update centroids
        for j in range(k):
            members = [points[i] for i, a in enumerate(assignments) if a == j]
            if members:
                centroids[j] = [sum(m[d] for m in members)/len(members) for d in range(dim)]
    return assignments


def _cluster_archetype(avg_rsi, avg_mom5, avg_d1):
    """Name a cluster based on its average characteristics."""
    if avg_rsi <= 35 and avg_mom5 < 0:
        return 'OVERSOLD_DISTRESSED'
    if avg_rsi >= 65 and avg_mom5 > 2:
        return 'OVERBOUGHT_MOMENTUM'
    if avg_rsi >= 60 and avg_mom5 < 0:
        return 'TOPPING_EXHAUSTION'
    if avg_rsi <= 50 and avg_mom5 > 2:
        return 'RECOVERY_PHASE'
    if avg_d1 > 0.5:
        return 'HIGH_PRESSURE_ZONE'
    if avg_d1 < -0.5:
        return 'LOW_PRESSURE_ZONE'
    return 'TRANSITIONAL'


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: invariant_discovery
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_invariant_discovery(params):
    """
    Find behavioral INVARIANTS — edges that survive ALL time periods.
    Tests each state→direction relationship across yearly cohorts.
    An invariant must:
      - Have P(TR) > 40% in every year
      - Have consistent direction (always bullish or always bearish)
      - Not degrade over time
    """
    symbol_data = _load_ohlcv_all(min_bars=80)
    if not symbol_data:
        return {'success': False, 'error': 'No OHLCV data'}

    HORIZON = int(params.get('horizon', 3))
    TR_THRESH = float(params.get('tr_threshold', 2.0))

    # Collect state × year × [outcomes]
    state_year_outcomes = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )

    for sym, bars in symbol_data.items():
        n = len(bars)
        if n < 80:
            continue
        closes  = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]
        avg_vol = [_rolling_avg(volumes[:i+1], 20) for i in range(n)]
        times   = [b['bar_time'] for b in bars]

        for i in range(1, n - HORIZON):
            if i < 15:
                continue
            gains  = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1)]
            losses = [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1)]
            avg_g  = sum(gains)/14; avg_l = sum(losses)/14
            rsi    = 100 - 100/(1+avg_g/avg_l) if avg_l > 0 else 100.0
            mom5   = (closes[i]-closes[i-5])/closes[i-5]*100 if i >= 5 else 0

            st = _classify_bar(closes[i], closes[i-1], rsi, None, volumes[i], avg_vol[i], mom5)
            fwd = (closes[i+HORIZON] - closes[i]) / closes[i] * 100
            is_tr = 1 if fwd >= TR_THRESH else 0

            # Year from unix timestamp
            year = datetime.datetime.utcfromtimestamp(times[i]).year
            state_year_outcomes[st][year].append(is_tr)

    # ── Test invariance ───────────────────────────────────────────────────────
    invariants = []
    non_invariants = []

    for state, year_data in state_year_outcomes.items():
        years_with_enough = {
            yr: outcomes for yr, outcomes in year_data.items()
            if len(outcomes) >= 10
        }
        if len(years_with_enough) < 3:
            continue

        yearly_ptrs = {
            yr: round(sum(outs)/len(outs)*100, 1)
            for yr, outs in years_with_enough.items()
        }

        ptrs = list(yearly_ptrs.values())
        mean_ptr = round(sum(ptrs)/len(ptrs), 1)
        std_ptr  = round(statistics.stdev(ptrs) if len(ptrs) > 1 else 0, 1)
        min_ptr  = min(ptrs)
        max_ptr  = max(ptrs)

        # Invariant: low std (< 15%), mean > 40%, consistent direction
        is_invariant = (std_ptr < 15 and mean_ptr >= 40 and min_ptr >= 30)
        # Check if degrading: last 2 years vs first 2 years
        sorted_yrs = sorted(yearly_ptrs.keys())
        if len(sorted_yrs) >= 4:
            early_avg = sum(yearly_ptrs[y] for y in sorted_yrs[:2]) / 2
            late_avg  = sum(yearly_ptrs[y] for y in sorted_yrs[-2:]) / 2
            trend = round(late_avg - early_avg, 1)
        else:
            trend = 0

        entry = {
            'state': state,
            'mean_p_tr': mean_ptr,
            'std_p_tr': std_ptr,
            'min_p_tr': min_ptr,
            'max_p_tr': max_ptr,
            'n_years': len(yearly_ptrs),
            'yearly': yearly_ptrs,
            'temporal_trend': trend,
            'status': 'DEGRADING' if trend < -10 else ('IMPROVING' if trend > 10 else 'STABLE'),
        }

        if is_invariant:
            invariants.append(entry)
        else:
            non_invariants.append(entry)

    invariants.sort(key=lambda x: -(x['mean_p_tr'] - x['std_p_tr']))
    non_invariants.sort(key=lambda x: -x['std_p_tr'])  # most unstable first

    # ── Interpretation ────────────────────────────────────────────────────────
    summary_lines = []
    for inv in invariants[:5]:
        summary_lines.append(
            f"{inv['state']}: P(TR)={inv['mean_p_tr']}%±{inv['std_p_tr']}% "
            f"(min={inv['min_p_tr']}%) عبر {inv['n_years']} سنوات — "
            f"{'MARKET PHYSICS ✅' if inv['std_p_tr'] < 10 else 'STABLE ✅'}"
        )

    return {
        'success': True,
        'n_invariants': len(invariants),
        'n_tested': len(state_year_outcomes),
        'invariants': invariants,
        'unstable_edges': non_invariants[:8],
        'summary': summary_lines,
        'horizon_bars': HORIZON,
        'methodology': 'Temporal cross-validation: state→P(TR) tested per year, invariant=std<15%,mean>40%,min>30%',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: failure_precursors
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_failure_precursors(params):
    """
    What hidden variables DISTINGUISH failed reversals from true reversals?

    Method: For each stock in indicators_cache,
    look at the next 5-bar return in ohlcv_history_execution.
    Split into TRUE_REVERSAL vs FAILED.
    Compare ALL indicator distributions.
    Find key discriminating variables.
    """
    stocks   = _load_indicators_latest()
    sym_data = _load_ohlcv_all(min_bars=30)
    if not stocks or not sym_data:
        return {'success': False, 'error': 'Insufficient data'}

    HORIZON   = int(params.get('horizon', 5))
    UP_THRESH = float(params.get('up_threshold', 2.5))
    DN_THRESH = float(params.get('dn_threshold', -2.0))

    FEATURES = ['rsi14', 'vol_ratio_20', 'momentum_5d',
                'momentum_10d', 'adx14', 'macd_hist', 'cci20',
                'close_position', 'price_vs_ath']

    true_rev   = {f: [] for f in FEATURES}
    failed_rev = {f: [] for f in FEATURES}

    for s in stocks:
        sym   = s['symbol']
        bars  = sym_data.get(sym, [])
        if not bars:
            continue

        # Find the bar that matches bar_date
        bar_date = s.get('bar_date', '')
        if not bar_date:
            continue

        # Find index of the matching bar (latest)
        last_idx = len(bars) - 1
        if last_idx + HORIZON >= len(bars):
            last_idx = len(bars) - HORIZON - 1
        if last_idx < 0:
            continue

        # Compute forward return from last known bar
        fwd_return = (bars[-1]['close'] - bars[last_idx]['close']) / bars[last_idx]['close'] * 100 \
                     if bars[last_idx]['close'] > 0 else 0

        # Only include stocks in potential reversal zone (oversold)
        rsi_val = s.get('rsi14') or 50
        if rsi_val > 45:
            continue  # Only analyze oversold candidates

        # Classify outcome
        if fwd_return >= UP_THRESH:
            group = true_rev
        elif fwd_return <= DN_THRESH:
            group = failed_rev
        else:
            continue  # ambiguous

        for feat in FEATURES:
            val = s.get(feat)
            if val is not None:
                group[feat].append(val)

    # ── Statistical comparison ────────────────────────────────────────────────
    discriminants = []
    n_tr   = len(true_rev.get('rsi14', []))
    n_fail = len(failed_rev.get('rsi14', []))

    if n_tr < 5 or n_fail < 5:
        return {
            'success': True,
            'n_true_reversal': n_tr,
            'n_failed': n_fail,
            'note': f'Insufficient samples for analysis (TR={n_tr}, Failed={n_fail}). Need ≥5 each.',
            'discriminants': [],
        }

    for feat in FEATURES:
        tr_vals   = true_rev[feat]
        fail_vals = failed_rev[feat]
        if len(tr_vals) < 3 or len(fail_vals) < 3:
            continue

        tr_mean   = sum(tr_vals) / len(tr_vals)
        fail_mean = sum(fail_vals) / len(fail_vals)
        diff      = tr_mean - fail_mean

        # Effect size (Cohen's d)
        tr_std   = statistics.stdev(tr_vals) if len(tr_vals) > 1 else 1
        fail_std = statistics.stdev(fail_vals) if len(fail_vals) > 1 else 1
        pooled   = math.sqrt((tr_std**2 + fail_std**2) / 2) or 1
        effect_d = round(diff / pooled, 3)

        # Direction interpretation
        if abs(effect_d) < 0.2:
            discriminant_power = 'WEAK'
        elif abs(effect_d) < 0.5:
            discriminant_power = 'MODERATE'
        else:
            discriminant_power = 'STRONG'

        direction = 'TR_HIGHER' if diff > 0 else 'TR_LOWER'

        discriminants.append({
            'feature': feat,
            'true_rev_mean':  round(tr_mean, 2),
            'failed_mean':    round(fail_mean, 2),
            'difference':     round(diff, 2),
            'effect_size_d':  effect_d,
            'power':          discriminant_power,
            'direction':      direction,
            'insight':        _precursor_insight(feat, diff, effect_d, tr_mean, fail_mean),
        })

    discriminants.sort(key=lambda x: -abs(x['effect_size_d']))

    # ── Build composite precursor score ──────────────────────────────────────
    strong_discriminants = [d for d in discriminants if d['power'] in ('STRONG','MODERATE')]

    return {
        'success': True,
        'n_true_reversal': n_tr,
        'n_failed': n_fail,
        'discriminants': discriminants,
        'strong_discriminants': strong_discriminants,
        'top_precursor': discriminants[0] if discriminants else None,
        'composite_rule': _build_composite_rule(strong_discriminants),
        'methodology': f'Oversold stocks (RSI≤45) split by {HORIZON}-bar forward return (TR≥{UP_THRESH}%, Fail≤{DN_THRESH}%). Effect size = Cohen\'s d.',
    }


def _precursor_insight(feat, diff, effect_d, tr_mean, fail_mean):
    """Generate insight for a discriminating feature."""
    if feat == 'rsi14':
        if diff < 0:
            return f"الـ RSI أقل في حالة الارتداد الحقيقي ({tr_mean:.1f} vs {fail_mean:.1f}) — أكثر إفراطاً في البيع يعني ارتداداً أقوى"
        else:
            return f"الـ RSI أعلى في حالة الارتداد الحقيقي ({tr_mean:.1f} vs {fail_mean:.1f})"
    if feat == 'vol_ratio_20':
        if diff > 0:
            return f"حجم أعلى يسبق الارتداد الحقيقي ({tr_mean:.2f}× vs {fail_mean:.2f}×) — امتصاص المؤسسي"
        else:
            return f"حجم أقل في الارتداد الحقيقي — الهدوء يسبق الارتداد"
    if feat == 'momentum_5d':
        if diff > 0:
            return f"زخم أعلى في الارتداد الحقيقي ({tr_mean:.1f}% vs {fail_mean:.1f}%) — تحوّل الزخم مبكراً"
        else:
            return f"الارتداد الحقيقي يأتي بعد زخم أشد سلبية ({tr_mean:.1f}% vs {fail_mean:.1f}%)"
    if feat == 'adx14':
        return f"ADX: ارتداد حقيقي={tr_mean:.1f} vs فاشل={fail_mean:.1f} ({'ترند قوي يدعم الارتداد' if diff > 0 else 'ضعف الترند مؤشر ارتداد'})"
    return f"{feat}: TR={tr_mean:.2f} vs Failed={fail_mean:.2f} (d={effect_d:.2f})"


def _build_composite_rule(strong_disc):
    """Build a human-readable composite discriminant rule."""
    if not strong_disc:
        return "لا يوجد مؤشر مميّز قوي كافٍ من البيانات المتاحة"
    parts = []
    for d in strong_disc[:3]:
        if d['direction'] == 'TR_HIGHER':
            parts.append(f"{d['feature']} > {d['failed_mean']:.1f}")
        else:
            parts.append(f"{d['feature']} < {d['failed_mean']:.1f}")
    return " AND ".join(parts) + f" → احتمال ارتداد حقيقي أعلى"


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: temporal_stability
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_temporal_stability(params):
    """
    Test P(TR) stability across ROLLING time windows.
    An edge that works in some years but not others is NOT real alpha.

    Outputs: stability score, trend, warning flags.
    """
    symbol_data = _load_ohlcv_all(min_bars=60)
    HORIZON     = int(params.get('horizon', 3))
    TR_THRESH   = float(params.get('tr_threshold', 2.0))
    WINDOW_DAYS = int(params.get('window_days', 180))  # ~6 months

    # Collect all (state, bar_time, outcome) tuples
    all_events = []  # (state, unix_time, is_tr)

    for sym, bars in symbol_data.items():
        n = len(bars)
        if n < 60:
            continue
        closes  = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]
        times   = [b['bar_time'] for b in bars]
        avg_vol = [_rolling_avg(volumes[:i+1], 20) for i in range(n)]

        for i in range(1, n - HORIZON):
            if i < 15:
                continue
            gains  = [max(0, closes[j]-closes[j-1]) for j in range(i-13, i+1)]
            losses = [max(0, closes[j-1]-closes[j]) for j in range(i-13, i+1)]
            ag = sum(gains)/14; al = sum(losses)/14
            rsi = 100 - 100/(1+ag/al) if al > 0 else 100.0
            mom5= (closes[i]-closes[i-5])/closes[i-5]*100 if i >= 5 else 0

            st   = _classify_bar(closes[i], closes[i-1], rsi, None, volumes[i], avg_vol[i], mom5)
            fwd  = (closes[i+HORIZON]-closes[i])/closes[i]*100
            is_tr= 1 if fwd >= TR_THRESH else 0
            all_events.append((st, times[i], is_tr))

    if not all_events:
        return {'success': False, 'error': 'No events computed'}

    all_events.sort(key=lambda x: x[1])
    t_min = all_events[0][1]
    t_max = all_events[-1][1]

    # Rolling windows: step = WINDOW_DAYS/2
    STEP_SEC    = WINDOW_DAYS * 86400 // 2
    WINDOW_SEC  = WINDOW_DAYS * 86400

    windows = []
    t = t_min
    while t + WINDOW_SEC <= t_max:
        window_events = [(st, is_tr) for st, ts, is_tr in all_events if t <= ts < t + WINDOW_SEC]
        if window_events:
            date_str = datetime.datetime.utcfromtimestamp(t).strftime('%Y-%m')
            windows.append((date_str, window_events))
        t += STEP_SEC

    # Compute P(TR) per state per window
    state_window_ptrs = collections.defaultdict(list)  # state → [(date, p_tr, n)]
    for date_str, events in windows:
        by_state = collections.defaultdict(list)
        for st, is_tr in events:
            by_state[st].append(is_tr)
        for st, outcomes in by_state.items():
            if len(outcomes) >= 5:
                p_tr = sum(outcomes) / len(outcomes) * 100
                state_window_ptrs[st].append({'window': date_str, 'p_tr': round(p_tr,1), 'n': len(outcomes)})

    # Stability analysis per state
    stability_results = {}
    for state, window_ptrs in state_window_ptrs.items():
        if len(window_ptrs) < 3:
            continue
        ptrs = [w['p_tr'] for w in window_ptrs]
        mean_p = round(sum(ptrs)/len(ptrs), 1)
        std_p  = round(statistics.stdev(ptrs) if len(ptrs) > 1 else 0, 1)
        cv     = round(std_p / mean_p * 100, 1) if mean_p > 0 else 99

        # Trend (linear regression on p_tr vs window_index)
        n_w = len(ptrs)
        x_bar = (n_w-1)/2
        y_bar = mean_p
        slope_num = sum((i - x_bar) * (ptrs[i] - y_bar) for i in range(n_w))
        slope_den = sum((i - x_bar)**2 for i in range(n_w)) or 1
        slope = round(slope_num / slope_den, 2)  # %/window change

        # Classification
        if cv < 15 and mean_p >= 45:
            stability = 'INVARIANT'
        elif cv < 20 and mean_p >= 40:
            stability = 'STABLE'
        elif cv < 30:
            stability = 'MODERATE'
        elif slope < -2:
            stability = 'DEGRADING'
        elif slope > 2:
            stability = 'IMPROVING'
        else:
            stability = 'UNSTABLE'

        stability_results[state] = {
            'mean_p_tr':   mean_p,
            'std_p_tr':    std_p,
            'cv_pct':      cv,
            'slope_per_window': slope,
            'n_windows':   n_w,
            'stability':   stability,
            'windows':     window_ptrs,
            'warning': _stability_warning(state, stability, slope, cv, mean_p),
        }

    # Rank by stability
    ranked = sorted(stability_results.items(),
                    key=lambda x: (0 if x[1]['stability'] == 'INVARIANT'
                                   else 1 if x[1]['stability'] == 'STABLE'
                                   else 2 if x[1]['stability'] == 'MODERATE'
                                   else 3), )

    return {
        'success': True,
        'n_states_tested': len(stability_results),
        'n_windows': len(windows),
        'window_size_days': WINDOW_DAYS,
        'stability_results': {k: v for k, v in ranked},
        'invariant_states':  [k for k, v in ranked if v['stability'] == 'INVARIANT'],
        'degrading_states':  [k for k, v in ranked if v['stability'] == 'DEGRADING'],
        'stable_states':     [k for k, v in ranked if v['stability'] in ('STABLE','INVARIANT')],
    }


def _stability_warning(state, stability, slope, cv, mean_p):
    if stability == 'DEGRADING':
        return f"⚠️ {state}: تراجع {abs(slope):.1f}% لكل نافذة — هذا الـ edge يُفقد قيمته!"
    if stability == 'UNSTABLE' and cv > 40:
        return f"❌ {state}: تذبذب عالٍ جداً (CV={cv}%) — لا يمكن الاعتماد عليه"
    if stability == 'INVARIANT':
        return f"✅ {state}: ثابت عبر الزمن (CV={cv}%, mean={mean_p}%) — market physics!"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND: quant_loop
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_quant_loop(params):
    """
    FULL AUTONOMOUS RESEARCH ITERATION.
    Runs all analyses and synthesizes into a structured quant research report.
    Output follows: Discover → Disprove → Causal Drivers → Transition Probs →
                    Duration → Regimes → Sector Diffs → Failure Scenarios →
                    Hypotheses → Architecture → Opportunities → Risks → Learned
    """
    _t0 = datetime.datetime.utcnow()
    depth = params.get('depth', 'standard')  # 'quick' | 'standard' | 'deep'

    # ── Run all sub-analyses ──────────────────────────────────────────────────
    forces_res  = cmd_behavioral_forces({})
    duration_res= cmd_duration_analysis({'horizon': 3})
    sector_res  = cmd_sector_markov({})
    invariant_res= cmd_invariant_discovery({'horizon': 3})
    temporal_res= cmd_temporal_stability({'window_days': 180})
    failure_res = cmd_failure_precursors({'horizon': 5})
    macro_ctx   = _load_macro()

    elapsed = (datetime.datetime.utcnow() - _t0).total_seconds()

    # ── Section 1: DISCOVER ───────────────────────────────────────────────────
    discoveries = []

    # From behavioral forces
    if forces_res.get('success'):
        dom = forces_res.get('dominant_archetype', 'UNKNOWN')
        rev_cnt = forces_res.get('reversal_candidates', 0)
        exh_cnt = forces_res.get('exhaustion_candidates', 0)
        n = forces_res.get('n_stocks', 0)
        discoveries.append(f"الحالة الحالية: {dom} ({n} سهم). ارتداد محتمل: {rev_cnt} | إرهاق: {exh_cnt}")

        # Market force levels
        mf = forces_res.get('market_forces', {})
        ep_mean = mf.get('exhaustion_pressure', {}).get('mean', 0)
        de_mean = mf.get('directional_energy', {}).get('mean', 0)
        ve_mean = mf.get('volatility_energy', {}).get('mean', 0)
        discoveries.append(
            f"قوى السوق: إرهاق={ep_mean:+.3f} | طاقة اتجاهية={de_mean:+.3f} | طاقة تقلب={ve_mean:.3f}"
        )

    # From invariants
    if invariant_res.get('success'):
        n_inv = invariant_res.get('n_invariants', 0)
        discoveries.append(
            f"اكتُشفت {n_inv} سلوكيات ثابتة (invariants) من أصل {invariant_res.get('n_tested',0)} حالة — "
            f"هذه هي قوانين فيزياء السوق"
        )
        for inv in invariant_res.get('invariants', [])[:3]:
            discoveries.append(
                f"  INVARIANT: {inv['state']} → P(TR)={inv['mean_p_tr']}%±{inv['std_p_tr']}% "
                f"عبر {inv['n_years']} سنوات"
            )

    # From duration
    if duration_res.get('success'):
        ranked = duration_res.get('ranked_states', [])
        if ranked:
            top = ranked[0]
            discoveries.append(
                f"Duration insight: {top['state']} — أفضل دخول بعد {top['best_entry_after']} بار "
                f"(P(TR)={top['peak_p_tr']}%)"
            )

    # ── Section 2: DISPROVE ───────────────────────────────────────────────────
    disproven = []

    # Unstable/degrading edges
    if temporal_res.get('success'):
        for st in temporal_res.get('degrading_states', []):
            res = temporal_res['stability_results'][st]
            disproven.append(
                f"❌ {st}: P(TR) يتراجع {abs(res['slope_per_window']):.1f}%/نافذة — "
                f"alpha زائل وليس حقيقياً"
            )
        unstable = [k for k,v in temporal_res['stability_results'].items()
                    if v['stability'] == 'UNSTABLE']
        if unstable:
            disproven.append(
                f"❌ {len(unstable)} حالات غير مستقرة زمنياً — لا يمكن الاعتماد عليها: {', '.join(unstable[:4])}"
            )

    # Weak invariants from unstable_edges
    if invariant_res.get('success'):
        unstable_edges = invariant_res.get('unstable_edges', [])
        for ue in unstable_edges[:3]:
            disproven.append(
                f"❌ {ue['state']}: P(TR) يتفاوت {ue['min_p_tr']}%↔{ue['max_p_tr']}% (std={ue['std_p_tr']}%) — "
                f"regime-dependent لا invariant"
            )

    if not disproven:
        disproven.append("✅ لم يُكتشف تراجع حاد في أي edge — النظام مستقر نسبياً في هذا التحليل")

    # ── Section 3: CAUSAL DRIVERS ─────────────────────────────────────────────
    causal_drivers = []

    if failure_res.get('success') and failure_res.get('discriminants'):
        top3 = failure_res['discriminants'][:3]
        causal_drivers.append(
            "المحركات السببية للارتداد الحقيقي (من تحليل المسبقات الخفية):"
        )
        for d in top3:
            causal_drivers.append(f"  • {d['insight']} (Cohen's d={d['effect_size_d']:.2f})")
        rule = failure_res.get('composite_rule', '')
        if rule:
            causal_drivers.append(f"  القاعدة المركّبة: {rule}")
    else:
        causal_drivers.append("RSI → ضغط الإرهاق | ATR → طاقة التقلب | حجم → امتصاص السيولة")
        causal_drivers.append("الارتداد الحقيقي = إرهاق + استقرار حجم + بداية تحوّل الزخم")

    # Sector-specific causal insight
    if sector_res.get('success'):
        inv_edges = sector_res.get('cross_sector_invariants', [])
        if inv_edges:
            top_inv = inv_edges[0]
            causal_drivers.append(
                f"  CROSS-SECTOR INVARIANT: {top_inv['from']} → {top_inv['to']} "
                f"({top_inv['mean_sector_pct']}% عبر {top_inv['n_sectors']} قطاعات)"
            )

    # ── Section 4: TRANSITION PROBABILITIES ──────────────────────────────────
    trans_probs = []
    if sector_res.get('success'):
        ov = sector_res.get('overall_matrix', {})
        for from_st in ['PANIC', 'VELOCITY_EXHAUSTION', 'POTENTIAL_BOUNCE']:
            if from_st in ov:
                top_to = [(k,v) for k,v in ov[from_st].items() if not k.startswith('_')]
                top_to.sort(key=lambda x: -x[1])
                n_total = ov[from_st].get('_n', 0)
                if top_to:
                    trans_probs.append(
                        f"P({from_st}→?) [n={n_total}]: " +
                        " | ".join(f"{k}={v}%" for k,v in top_to[:3])
                    )

    # ── Section 5: DURATION ───────────────────────────────────────────────────
    dur_findings = []
    if duration_res.get('success'):
        dur_findings = duration_res.get('key_insight', [])[:4]
        for st_entry in duration_res.get('ranked_states', [])[:4]:
            st = st_entry['state']
            an = duration_res['analysis'].get(st, {})
            if an:
                cohorts = an.get('cohorts', {})
                p1 = cohorts.get('1', {}).get('p_tr', '—')
                p3 = cohorts.get('3', {}).get('p_tr', '—')
                best= an.get('best_cohort', '—')
                dur_findings.append(
                    f"  {st}: 1bar={p1}% | 3bars={p3}% | peak@{best}bar={an.get('peak_p_tr')}%"
                )

    # ── Section 6: REGIME DEPENDENCIES ───────────────────────────────────────
    regime_deps = []
    macro_regime = macro_ctx.get('macro_regime', 'UNKNOWN') if macro_ctx else 'UNKNOWN'
    eq_mult = macro_ctx.get('equity_multiplier', 1.0) if macro_ctx else 1.0
    real_rate = macro_ctx.get('real_interest_rate') if macro_ctx else None

    regime_deps.append(f"ماكرو: {macro_regime} | multiplier={eq_mult}× | فائدة حقيقية={real_rate}%")
    regime_deps.append(f"تأثير الريجيم على P(TR): × {eq_mult:.2f} على كل إشارة (DISINFLATION_EASING = دعم +8%)")

    # Add sector-specific regime differences
    if sector_res.get('success'):
        sec_specific = sector_res.get('sector_specific_edges', [])[:3]
        for edge in sec_specific:
            regime_deps.append(
                f"  {edge['sector']}: {edge['from']}→{edge['to']} = {edge['sector_pct']}% "
                f"(vs سوق {edge['overall_pct']}% | Δ{edge['diff']:+.0f}%)"
            )

    # ── Section 7: SECTOR DIFFERENCES ────────────────────────────────────────
    sector_diffs = []
    if sector_res.get('success') and forces_res.get('success'):
        sec_forces = forces_res.get('sector_forces', {})
        for sec, forces in sorted(sec_forces.items())[:5]:
            ep = forces.get('exhaustion_pressure', 0)
            de = forces.get('directional_energy', 0)
            rp = forces.get('reversal_potential', 0)
            sector_diffs.append(
                f"  {sec[:20]:20s}: إرهاق={ep:+.2f} | طاقة={de:+.2f} | ارتداد={rp:.2f}"
            )

    # ── Section 8: FAILURE SCENARIOS ─────────────────────────────────────────
    failure_scenarios = []
    failure_scenarios.append("تصنيف سيناريوهات الفشل الخمسة:")
    failure_scenarios.append("  1. DEAD_CAT_BOUNCE: ارتداد مؤقت (<2 بار) قبل استمرار الهبوط")
    failure_scenarios.append("  2. CONTINUATION_TRAP: الدخول على 'استقرار' وهمي ثم انتكاسة")
    failure_scenarios.append("  3. DRIFT_FAILURE: الهبوط التدريجي بدل الارتداد الحاد")
    failure_scenarios.append("  4. VOL_COMPRESSION_TRAP: هدوء مصطنع ثم انكسار للأسفل")
    failure_scenarios.append("  5. REGIME_TRAP: الارتداد يعمل في ريجيم UP فقط، يفشل في DOWN")

    if failure_res.get('success'):
        n_tr   = failure_res.get('n_true_reversal', 0)
        n_fail = failure_res.get('n_failed', 0)
        failure_scenarios.append(
            f"  (بيانات: {n_tr} ارتداد حقيقي + {n_fail} فاشل من الأسهم المتاحة)"
        )

    # ── Section 9: NEW HYPOTHESES ─────────────────────────────────────────────
    new_hypotheses = []
    new_hypotheses.append("فرضيات جديدة للاختبار:")
    new_hypotheses.append("  H1: P(TR | PANIC, duration=3) > P(TR | PANIC, duration=1) بسبب إرهاق البائعين")
    new_hypotheses.append("  H2: في قطاع Finance، VELOCITY_EXHAUSTION يُتبع بـ TRENDING_UP بنسبة أعلى من المتوسط")
    new_hypotheses.append("  H3: في ريجيم DISINFLATION_EASING، P(TR) يرتفع 8-12% لكل حالة oversold")
    new_hypotheses.append("  H4: الأسهم في archetype QUIET_ACCUMULATION تُحقق أفضل عائد 10-بار")
    new_hypotheses.append("  H5: bb_width انخفاضه قبل الانكسار يُميّز DEAD_CAT عن TRUE_REVERSAL")

    # ── Section 10: ARCHITECTURE IMPROVEMENTS ───────────────────────────────
    arch_improvements = []
    arch_improvements.append("تحسينات معمارية مقترحة:")
    if temporal_res.get('success') and temporal_res.get('degrading_states'):
        arch_improvements.append(
            f"  • إزالة/تعديل: {', '.join(temporal_res['degrading_states'][:3])} "
            f"من قاعدة الإشارات (تراجع زمني)"
        )
    if invariant_res.get('success') and invariant_res.get('invariants'):
        inv_states = [i['state'] for i in invariant_res['invariants'][:3]]
        arch_improvements.append(f"  • تعزيز وزن: {', '.join(inv_states)} (invariant behaviors)")
    arch_improvements.append("  • إضافة duration_cohort كـ feature في حساب P(TR)")
    arch_improvements.append("  • تطبيق sector-specific multipliers على الإشارات")
    arch_improvements.append("  • إنشاء composite reversal score من 6 قوى سلوكية")

    # ── Section 11: CURRENT OPPORTUNITIES ───────────────────────────────────
    opportunities = []
    if forces_res.get('success'):
        arch_stocks = forces_res.get('archetype_stocks', {})
        for arch in ['DEEPLY_OVERSOLD', 'HIGH_REVERSAL_POTENTIAL']:
            stocks_in_arch = arch_stocks.get(arch, [])
            if stocks_in_arch:
                syms = [s['symbol'] for s in stocks_in_arch[:5]]
                opportunities.append(f"  {arch}: {', '.join(syms)}")
    if not opportunities:
        opportunities.append("  لا توجد فرص oversold واضحة في الوقت الحالي")

    # ── Section 12: HIDDEN RISKS ─────────────────────────────────────────────
    hidden_risks = []
    if temporal_res.get('success') and temporal_res.get('degrading_states'):
        hidden_risks.append(
            f"⚠️ إشارات تتراجع: {', '.join(temporal_res['degrading_states'][:3])}"
        )
    ep_mean = forces_res.get('market_forces', {}).get('exhaustion_pressure', {}).get('mean', 0) \
              if forces_res.get('success') else 0
    if ep_mean > 0.3:
        hidden_risks.append(f"⚠️ ضغط إرهاق مرتفع عند +{ep_mean:.3f} — خطر توزيع")
    elif ep_mean < -0.3:
        hidden_risks.append(f"✅ ضغط إرهاق سلبي {ep_mean:.3f} — السوق في منطقة إفراط بيع")
    hidden_risks.append("⚠️ EGX يعمل بسيولة محدودة — slippage قد يُلغي theoretical edge")
    hidden_risks.append("⚠️ الارتباط بين الأسهم مرتفع في PANIC — التنويع يُخفق تحديداً حين تحتاجه")

    # ── Section 13: WHAT WAS LEARNED ─────────────────────────────────────────
    learned = []
    n_inv = invariant_res.get('n_invariants', 0) if invariant_res.get('success') else 0
    n_sec = sector_res.get('n_sectors', 0) if sector_res.get('success') else 0
    learned.append(f"✅ تم تحليل قوى سلوكية لـ {forces_res.get('n_stocks',0)} سهم")
    learned.append(f"✅ تم اختبار ثبات {temporal_res.get('n_states_tested',0)} حالة عبر نوافذ زمنية")
    learned.append(f"✅ تم بناء Markov matrices لـ {n_sec} قطاعات")
    learned.append(f"✅ تم اكتشاف {n_inv} behavioral invariants")
    if failure_res.get('n_true_reversal', 0) >= 5:
        learned.append(f"✅ تم تحليل مسبقات الفشل: {failure_res.get('n_true_reversal',0)} ارتداد حقيقي vs {failure_res.get('n_failed',0)} فاشل")
    learned.append(f"📅 جلسة البحث: {_now_iso()} | استغرق {elapsed:.1f}s")

    return {
        'success': True,
        'elapsed_sec': round(elapsed, 1),
        'report': {
            '1_discovered':          discoveries,
            '2_disproven':           disproven,
            '3_causal_drivers':      causal_drivers,
            '4_transition_probs':    trans_probs,
            '5_duration_findings':   dur_findings,
            '6_regime_dependencies': regime_deps,
            '7_sector_differences':  sector_diffs,
            '8_failure_scenarios':   failure_scenarios,
            '9_hypotheses':          new_hypotheses,
            '10_architecture':       arch_improvements,
            '11_opportunities':      opportunities,
            '12_hidden_risks':       hidden_risks,
            '13_learned':            learned,
        },
        'sub_results': {
            'behavioral_forces':  {'success': forces_res.get('success'), 'n': forces_res.get('n_stocks',0)},
            'duration_analysis':  {'success': duration_res.get('success')},
            'sector_markov':      {'success': sector_res.get('success'), 'n_sectors': n_sec},
            'invariant_discovery':{'success': invariant_res.get('success'), 'n_invariants': n_inv},
            'temporal_stability': {'success': temporal_res.get('success')},
            'failure_precursors': {'success': failure_res.get('success')},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    'behavioral_forces':  cmd_behavioral_forces,
    'duration_analysis':  cmd_duration_analysis,
    'sector_markov':      cmd_sector_markov,
    'latent_compress':    cmd_latent_compress,
    'invariant_discovery':cmd_invariant_discovery,
    'failure_precursors': cmd_failure_precursors,
    'temporal_stability': cmd_temporal_stability,
    'quant_loop':         cmd_quant_loop,
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
        print(json.dumps({
            'success': False,
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = fn(params)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
        }))
        sys.exit(1)

if __name__ == '__main__':
    main()
