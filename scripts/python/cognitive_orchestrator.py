#!/usr/bin/env python3
"""
EGX Autonomous Market Cognitive Orchestrator  (Phase 9)
=========================================================
Unifies all 8 intelligence layers into a single adaptive market cognition organism.
Uses real macro data (macro_snapshot), OHLCV, and indicators_cache.

Commands:
  data_health       — validate freshness, coverage, anomalies
  orchestrate_now   — run all layer proxies, build unified snapshot
  arbitrate         — resolve cross-layer conflicts with priority stack
  confidence_map    — per-layer + global confidence scores
  conflict_scan     — list all detected cross-layer conflicts
  posture           — exposure recommendation + sector guidance
  instability_watch — safety escalation monitoring
  evolution_sync    — sync memory logs, compute daily delta
  daily_report      — full 15-section institutional intelligence report
  orchestrate_full  — complete orchestration: all commands synthesised
"""
import sys, json, time, statistics, math
from pathlib import Path
from collections import defaultdict, Counter

DB_PATH       = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')
ORCH_LOG      = str(Path(__file__).parent.parent.parent / 'data' / 'orchestrator_log.json')
EVO_LOG       = str(Path(__file__).parent.parent.parent / 'data' / 'evolution_memory.json')
COUP_LOG      = str(Path(__file__).parent.parent.parent / 'data' / 'world_coupling_log.json')

COMMANDS = {
    'data_health', 'orchestrate_now', 'arbitrate', 'confidence_map',
    'conflict_scan', 'posture', 'instability_watch', 'evolution_sync',
    'daily_report', 'orchestrate_full',
}

# ── Layer weights for global confidence ─────────────────────────────────────
LAYER_WEIGHTS = {
    'latent':      0.11,
    'fields':      0.09,
    'propagation': 0.13,
    'energy':      0.13,
    'causality':   0.13,
    'decision':    0.13,
    'evolution':   0.09,
    'coupling':    0.11,
    'spectral':    0.08,   # Ph 21 — Spectral Cycle Intelligence (8%)
}

# ── Utilities ────────────────────────────────────────────────────────────────

def safe(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 4:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx = sum(xs) / n;  my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a-mx)**2 for a in xs) * sum((b-my)**2 for b in ys))
    return num / den if den > 1e-12 else 0.0


def health_label(h):
    return ('HEALTHY'  if h >= 0.70 else
            'DEGRADED' if h >= 0.40 else 'CRITICAL')


def load_json_log(path):
    try:
        p = Path(path)
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []


def append_json_log(path, entry, max_entries=365):
    log = load_json_log(path)
    log.append(entry)
    if len(log) > max_entries:
        log = log[-max_entries:]
    try:
        Path(path).write_text(json.dumps(log, indent=2))
    except Exception:
        pass


# ── Data loading ─────────────────────────────────────────────────────────────

def load_ohlcv(db, days=90, max_per_sym=60):
    cutoff = int(time.time()) - days * 86400
    rows = db.execute("""
        SELECT h.symbol, h.bar_time, h.open, h.high, h.low, h.close, h.volume,
               u.sector
        FROM ohlcv_history_features h
        JOIN stock_universe u ON h.symbol = u.symbol
        WHERE h.bar_time >= ? AND h.close > 0
        ORDER BY h.symbol, h.bar_time
    """, [cutoff]).fetchall()

    ind_rows = db.execute("""
        SELECT symbol, rsi14, vol_ratio_20, momentum_5d, atr14, bb_width,
               adx14, above_ema20, above_ema50, macd_hist, cci20
        FROM indicators_cache
    """).fetchall()
    cur_ind = {r['symbol']: dict(r) for r in ind_rows}

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r['symbol']].append({
            'symbol':   r['symbol'],
            'bar_time': r['bar_time'],
            'open':     safe(r['open']),
            'high':     safe(r['high']),
            'low':      safe(r['low']),
            'close':    safe(r['close']),
            'volume':   r['volume'] or 0,
            'sector':   r['sector'] or 'Unknown',
        })

    try:
        ex_rows = db.execute("""
            SELECT symbol, bar_time
            FROM data_quality_bar_exclusions
            WHERE source_table = 'ohlcv_history'
              AND status = 'ACTIVE'
        """).fetchall()
        excluded_by_sym = defaultdict(list)
        for er in ex_rows:
            excluded_by_sym[er['symbol']].append(int(er['bar_time']))
        for sym, times in excluded_by_sym.items():
            times.sort()
        for sym, bars in by_sym.items():
            ex_times = excluded_by_sym.get(sym, [])
            if not ex_times:
                continue
            for i in range(1, len(bars)):
                prev_t = int(bars[i-1]['bar_time'])
                cur_t = int(bars[i]['bar_time'])
                bars[i]['quality_gap'] = any(prev_t < t < cur_t for t in ex_times)
    except Exception:
        pass

    for sym in list(by_sym):
        bars = by_sym[sym]
        if len(bars) > max_per_sym:
            by_sym[sym] = bars[-max_per_sym:]
        # Enrich latest bar with current indicators
        if sym in cur_ind:
            ci = cur_ind[sym]
            by_sym[sym][-1].update({
                'rsi14':       safe(ci.get('rsi14'),       50.0),
                'vol_ratio_20': safe(ci.get('vol_ratio_20'), 1.0),
                'momentum_5d': safe(ci.get('momentum_5d'),  0.0),
                'atr14':       safe(ci.get('atr14'),        0.0),
                'bb_width':    safe(ci.get('bb_width'),     0.0),
                'adx14':       safe(ci.get('adx14'),        0.0),
                'above_ema20': safe(ci.get('above_ema20'),  0),
                'above_ema50': safe(ci.get('above_ema50'),  0),
                'macd_hist':   safe(ci.get('macd_hist'),    0.0),
                'cci20':       safe(ci.get('cci20'),        0.0),
            })

    return by_sym, cur_ind


def load_macro(db):
    try:
        row = db.execute("""
            SELECT usd_egp, inflation_yoy, cbe_rate, real_interest_rate,
                   gdp_yoy, fx_reserves_b, macro_regime, regime_score,
                   strategic_bias, inflation_momentum, rate_cycle,
                   fx_trend, growth_trend, fetched_at
            FROM macro_snapshot ORDER BY id DESC LIMIT 1
        """).fetchone()
        if not row:
            raise ValueError('no snapshot')
        return {
            'available':          True,
            'usd_egp':            safe(row['usd_egp'],            50.0),
            'inflation_yoy':      safe(row['inflation_yoy'],      20.0),
            'cbe_rate':           safe(row['cbe_rate'],           20.0),
            'real_interest_rate': safe(row['real_interest_rate'],  0.0),
            'gdp_yoy':            safe(row['gdp_yoy'],             4.0),
            'fx_reserves_b':      safe(row['fx_reserves_b'],      40.0),
            'macro_regime':       row['macro_regime'] or 'UNKNOWN',
            'regime_score':       safe(row['regime_score'],       50.0),
            'strategic_bias':     row['strategic_bias'] or '',
            'inflation_momentum': row['inflation_momentum'] or 'stable',
            'rate_cycle':         row['rate_cycle'] or 'stable',
            'fx_trend':           row['fx_trend'] or 'stable',
            'growth_trend':       row['growth_trend'] or 'stable',
            'fetched_at':         row['fetched_at'] or '',
        }
    except Exception as e:
        return {'available': False, 'error': str(e)}


# ── Enrichment ───────────────────────────────────────────────────────────────

def enrich(data):
    for sym, bars in data.items():
        for i, b in enumerate(bars):
            b['ret'] = ((b['close'] - bars[i-1]['close']) / bars[i-1]['close']
                        if i > 0 and bars[i-1]['close'] > 0 and not b.get('quality_gap') else 0.0)
        for i, b in enumerate(bars):
            vols = [bars[j]['volume'] for j in range(max(0,i-20), i+1)
                    if bars[j]['volume'] > 0]
            avg  = (statistics.mean(vols[:-1]) if len(vols) > 1
                    else (vols[0] if vols else 1))
            b['vol_ratio'] = b['volume'] / avg if avg > 0 and b['volume'] > 0 else 0.0
        for b in bars:
            b['range_pct'] = (b['high'] - b['low']) / b['close'] if b['close'] > 0 else 0.0
        # RSI proxy (Wilder)
        rets = []
        for b in bars:
            rets.append(b['ret'])
            if len(rets) >= 15:
                gains  = [max(0, r) for r in rets[-14:]]
                losses = [max(0,-r) for r in rets[-14:]]
                ag = statistics.mean(gains)  if any(g>0 for g in gains)  else 1e-9
                al = statistics.mean(losses) if any(l>0 for l in losses) else 1e-9
                b['rsi_proxy'] = (100.0 if al < 1e-12 else
                                   0.0  if ag < 1e-12 else
                                   100 - 100/(1+ag/al))
            else:
                b['rsi_proxy'] = 50.0
        # 5-day momentum
        for i, b in enumerate(bars):
            b['mom5'] = ((b['close'] - bars[i-5]['close']) / bars[i-5]['close']
                         if i >= 5 and bars[i-5]['close'] > 0
                         and not any(x.get('quality_gap') for x in bars[max(0, i-4):i+1])
                         else 0.0)
    return data


def latest_snapshot(data, cur_ind):
    """Return list of per-symbol snapshots using real indicators where available."""
    snaps = []
    for sym, bars in data.items():
        if not bars:
            continue
        lb = bars[-1]
        rsi     = lb.get('rsi14',        lb.get('rsi_proxy', 50.0))
        vol_r   = lb.get('vol_ratio_20', lb.get('vol_ratio', 1.0))
        mom     = lb.get('momentum_5d',  lb.get('mom5', 0.0))
        atr     = lb.get('atr14',        0.0)
        bb_w    = lb.get('bb_width',     0.0)
        adx     = lb.get('adx14',        0.0)
        ema20   = lb.get('above_ema20',  0)
        ema50   = lb.get('above_ema50',  0)
        macdh   = lb.get('macd_hist',    0.0)
        cci     = lb.get('cci20',        0.0)
        snaps.append({
            'symbol':  sym,
            'sector':  lb['sector'],
            'close':   lb['close'],
            'ret':     lb['ret'],
            'vol_ratio': vol_r,
            'rsi':     rsi,
            'mom5':    mom,
            'atr':     atr,
            'bb_width': bb_w,
            'adx':     adx,
            'above_ema20': ema20,
            'above_ema50': ema50,
            'macd_hist':   macdh,
            'cci20':    cci,
            'range_pct': lb.get('range_pct', 0.0),
        })
    return snaps


# ── Layer Proxies ────────────────────────────────────────────────────────────

def layer_latent(snaps):
    """Phase 1: Latent forces — RSI distribution + breadth."""
    rsi_vals = [s['rsi'] for s in snaps if s['rsi'] > 0]
    if not rsi_vals:
        return {'health': 0.5, 'state': 'DEGRADED', 'regime': 'UNKNOWN', 'detail': 'no data'}

    bull_pct    = sum(1 for r in rsi_vals if r > 55) / len(rsi_vals)
    bear_pct    = sum(1 for r in rsi_vals if r < 45) / len(rsi_vals)
    neutral_pct = 1.0 - bull_pct - bear_pct
    dominant    = max(bull_pct, bear_pct, neutral_pct)

    regime = ('BULL'    if bull_pct > bear_pct * 1.3 else
              'BEAR'    if bear_pct > bull_pct * 1.3 else 'MIXED')
    # Health = clarity of regime signal (dominant fraction)
    health = min(1.0, dominant * 1.4)

    ema20_pct = sum(1 for s in snaps if s['above_ema20']) / max(len(snaps), 1)
    ema50_pct = sum(1 for s in snaps if s['above_ema50']) / max(len(snaps), 1)

    return {
        'health':      round(health, 4),
        'state':       health_label(health),
        'regime':      regime,
        'bull_pct':    round(bull_pct, 3),
        'bear_pct':    round(bear_pct, 3),
        'above_ema20': round(ema20_pct, 3),
        'above_ema50': round(ema50_pct, 3),
        'avg_rsi':     round(statistics.mean(rsi_vals), 2),
        'detail':      f'RSI avg={statistics.mean(rsi_vals):.1f}, bull={bull_pct*100:.0f}%, ema20={ema20_pct*100:.0f}%',
    }


def layer_fields(snaps):
    """Phase 2: Force fields — sector momentum spread, ADX distribution."""
    sector_moms = defaultdict(list)
    for s in snaps:
        if s['sector'] and s['sector'] != 'Unknown':
            sector_moms[s['sector']].append(s['mom5'])

    if len(sector_moms) < 3:
        return {'health': 0.5, 'state': 'DEGRADED', 'detail': 'insufficient sectors'}

    sector_avgs = {sec: statistics.mean(ms) for sec, ms in sector_moms.items() if ms}
    spread = max(sector_avgs.values()) - min(sector_avgs.values())

    # High spread = strong sector divergence = active force field
    health = min(1.0, spread * 25)

    adx_vals = [s['adx'] for s in snaps if s['adx'] > 0]
    avg_adx  = statistics.mean(adx_vals) if adx_vals else 0.0

    top_sec  = max(sector_avgs, key=sector_avgs.get)
    bot_sec  = min(sector_avgs, key=sector_avgs.get)

    return {
        'health':       round(health, 4),
        'state':        health_label(health),
        'sector_spread': round(spread, 5),
        'avg_adx':      round(avg_adx, 2),
        'n_sectors':    len(sector_avgs),
        'top_sector':   top_sec,
        'bot_sector':   bot_sec,
        'top_mom':      round(sector_avgs[top_sec], 5),
        'bot_mom':      round(sector_avgs[bot_sec], 5),
        'detail':       f'spread={spread*100:.2f}%, top={top_sec}, adx={avg_adx:.1f}',
    }


def layer_propagation(data):
    """Phase 3: Propagation — cross-sector correlation, market synchronisation."""
    sector_ts = defaultdict(list)
    for sym, bars in data.items():
        sec = bars[0]['sector'] if bars else 'Unknown'
        for b in bars[-20:]:
            sector_ts[sec].append(b['ret'])

    avail = [s for s, rs in sector_ts.items() if len(rs) >= 8]
    if len(avail) < 2:
        return {'health': 0.5, 'state': 'DEGRADED', 'contagion_score': 0.0, 'detail': 'insufficient sectors'}

    cors = []
    for i in range(len(avail)):
        for j in range(i+1, len(avail)):
            si = sector_ts[avail[i]]
            sj = sector_ts[avail[j]]
            n  = min(len(si), len(sj))
            if n >= 8:
                cors.append(abs(pearson(si[-n:], sj[-n:])))

    avg_cor = statistics.mean(cors) if cors else 0.0

    # Optimal correlation for normal propagation: 0.3–0.5
    # Too high (>0.7) = crisis synchronisation
    # Too low (<0.2)  = fragmented / blocked
    health = (1.0 - abs(avg_cor - 0.40) / 0.50)
    health = max(0.0, min(1.0, health))

    state = ('CRISIS_SYNC'  if avg_cor > 0.70 else
             'HIGH_CONTAGION' if avg_cor > 0.55 else
             'NORMAL'       if avg_cor > 0.20 else 'FRAGMENTED')

    return {
        'health':          round(health, 4),
        'state':           health_label(health),
        'contagion_score': round(avg_cor, 4),
        'contagion_state': state,
        'n_sector_pairs':  len(cors),
        'detail':          f'cross-sector ρ={avg_cor:.3f} ({state})',
    }


def layer_energy(snaps, data):
    """Phase 4: Energy — return dispersion, vol distribution, range dynamics."""
    rets     = [s['ret']       for s in snaps]
    vol_rs   = [s['vol_ratio'] for s in snaps if s['vol_ratio'] > 0]
    ranges   = [s['range_pct'] for s in snaps if s['range_pct'] > 0]
    bb_ws    = [s['bb_width']  for s in snaps if s['bb_width']  > 0]

    if not rets:
        return {'health': 0.5, 'state': 'DEGRADED', 'detail': 'no data'}

    # Return dispersion: market has energy when stocks diverge
    ret_std     = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    avg_range   = statistics.mean(ranges)  if ranges  else 0.0
    avg_vol_r   = statistics.mean(vol_rs)  if vol_rs  else 1.0
    avg_bb_w    = statistics.mean(bb_ws)   if bb_ws   else 0.0

    # High std of returns = active energy; too low = energy absent
    energy_score = min(1.0, ret_std * 25)

    # Volume energy (vol_ratio distribution)
    high_vol_pct = sum(1 for v in vol_rs if v > 1.5) / max(len(vol_rs), 1)

    # BB width compression signal
    bb_compressed = avg_bb_w < 0.05 if avg_bb_w else False

    # Composite energy health
    health = (energy_score * 0.5 + min(1.0, avg_vol_r/1.5) * 0.3 +
              high_vol_pct * 0.2)
    health = max(0.0, min(1.0, health))

    energy_state = ('HIGH'       if health > 0.70 else
                    'NORMAL'     if health > 0.45 else
                    'LOW'        if health > 0.25 else 'EXHAUSTED')

    return {
        'health':          round(health, 4),
        'state':           health_label(health),
        'energy_state':    energy_state,
        'ret_dispersion':  round(ret_std * 100, 3),
        'avg_vol_ratio':   round(avg_vol_r, 3),
        'high_vol_pct':    round(high_vol_pct, 3),
        'avg_range_pct':   round(avg_range * 100, 3),
        'bb_compressed':   bb_compressed,
        'detail':          f'dispersion={ret_std*100:.2f}%, vol_r={avg_vol_r:.2f}, {energy_state}',
    }


def _classify_event(rsi, vol_ratio, ret, volume=1):
    if not volume or volume <= 0:
        return None
    rsi = safe(rsi, 50); vr = safe(vol_ratio, 1); ret = safe(ret, 0)
    if rsi < 32  and ret < -0.020:             return 'PANIC_ONSET'
    if rsi > 72  and vr  > 2.0:               return 'MOMENTUM_SURGE'
    if vr  < 0.30 and volume > 100:           return 'VOL_COMPRESSION'
    if vr  > 3.0  and volume > 100:           return 'VOL_EXPLOSION'
    if rsi < 38  and ret < -0.030:            return 'TREND_BREAK'
    if rsi > 70  and vr  < 0.55:             return 'EXHAUSTION_ONSET'
    if rsi > 62  and ret > 0.030 and volume > 100: return 'RECOVERY_ONSET'
    return None


def layer_causality(data):
    """Phase 5: Causality — event firing rate, diversity, causal chain activity."""
    n_total = 0
    n_events = 0
    event_counts = Counter()

    for sym, bars in data.items():
        for b in bars:
            n_total += 1
            rsi = b.get('rsi_proxy', 50.0)
            ev  = _classify_event(rsi, b.get('vol_ratio', 1.0), b['ret'], b['volume'])
            if ev:
                n_events   += 1
                event_counts[ev] += 1

    ev_rate   = n_events / max(n_total, 1)
    n_types   = len(event_counts)
    diversity = n_types / 7.0  # 7 possible event types

    # EGX baseline: ~20% event rate is healthy
    rate_health = max(0.0, 1.0 - abs(ev_rate - 0.20) / 0.20)
    # Diversity health: want at least 4-5 event types active
    div_health  = min(1.0, diversity * 1.4)

    health = rate_health * 0.6 + div_health * 0.4
    health = max(0.0, min(1.0, health))

    causal_state = ('ACTIVE'   if ev_rate > 0.15 and n_types >= 4 else
                    'NORMAL'   if ev_rate > 0.08 else
                    'DEGRADED' if ev_rate > 0.03 else 'INACTIVE')

    top_events = event_counts.most_common(3)

    return {
        'health':        round(health, 4),
        'state':         health_label(health),
        'causal_state':  causal_state,
        'event_rate':    round(ev_rate, 4),
        'n_event_types': n_types,
        'top_events':    [{'event': e, 'count': c} for e, c in top_events],
        'detail':        f'ev_rate={ev_rate*100:.1f}%, {n_types} types, {causal_state}',
    }


def layer_decision(snaps):
    """Phase 6: Decision engine — actionable state distribution."""
    states = Counter()
    for s in snaps:
        rsi    = s['rsi']
        vr     = s['vol_ratio']
        mom    = s['mom5']
        cci    = s['cci20']
        macdh  = s['macd_hist']
        adx    = s['adx']

        # Map to decision state
        if rsi > 55 and vr > 1.2 and mom > 0.005 and adx > 20:
            states['HIGH_CONVICTION'] += 1
        elif rsi > 48 and vr > 0.8 and mom > 0:
            states['CONDITIONAL'] += 1
        elif rsi < 40 or (vr < 0.4 and adx < 15):
            states['AVOID'] += 1
        elif rsi > 72 or vr > 3.0:
            states['FRAGILE'] += 1
        elif 40 <= rsi <= 55 and vr >= 0.6:
            states['TRANSITIONAL'] += 1
        else:
            states['UNSTABLE'] += 1

    total = max(sum(states.values()), 1)
    hc_pct  = states['HIGH_CONVICTION'] / total
    cond_pct = states['CONDITIONAL']    / total
    avoid_pct = states['AVOID']         / total

    # Health: high when actionable states dominate
    actionable = hc_pct + cond_pct * 0.5
    health     = min(1.0, actionable * 2.0)

    dec_state = ('DECISIVE'   if hc_pct > 0.10 else
                 'NORMAL'     if actionable > 0.25 else
                 'UNCERTAIN'  if avoid_pct > 0.40 else 'PARALYZED')

    return {
        'health':         round(health, 4),
        'state':          health_label(health),
        'decision_state': dec_state,
        'high_conviction_pct': round(hc_pct, 3),
        'conditional_pct':     round(cond_pct, 3),
        'avoid_pct':           round(avoid_pct, 3),
        'state_dist':          {k: round(v/total, 3) for k, v in states.most_common()},
        'detail':              f'HC={hc_pct*100:.1f}%, avoid={avoid_pct*100:.1f}%, {dec_state}',
    }


def layer_evolution():
    """Phase 7: Self-evolution — model trust from memory log."""
    raw = load_json_log(EVO_LOG)
    # raw can be a list of entries OR a dict with 'entries' key
    if isinstance(raw, dict):
        log = raw.get('entries', [])
    else:
        log = raw if isinstance(raw, list) else []

    if not log:
        return {'health': 0.6, 'state': 'DEGRADED', 'trust': 'UNKNOWN',
                'detail': 'no evolution log'}

    latest = log[-1] if isinstance(log[-1], dict) else {}
    trust  = latest.get('trust_level') or latest.get('decision') or 'UNKNOWN'
    confidence = safe(latest.get('confidence', 0.6), 0.6)

    health = {
        'TRUST':              1.0,
        'REDUCE_CONFIDENCE':  0.65,
        'REBUILD':            0.35,
        'INVALIDATE':         0.10,
        'UNKNOWN':            0.55,
    }.get(trust, 0.55)

    return {
        'health':     round(health, 4),
        'state':      health_label(health),
        'trust':      trust,
        'confidence': round(confidence, 4),
        'log_entries': len(log),
        'detail':     f'trust={trust}, conf={confidence:.2f}',
    }


def layer_coupling(macro):
    """Phase 8: World coupling — macro health from real data."""
    if not macro.get('available'):
        return {'health': 0.55, 'state': 'DEGRADED', 'detail': 'no macro data'}

    macro_regime   = macro.get('macro_regime', 'UNKNOWN')
    regime_score   = macro.get('regime_score', 50.0)
    inf_mom        = macro.get('inflation_momentum', 'stable')
    rate_cycle     = macro.get('rate_cycle', 'stable')
    fx_trend       = macro.get('fx_trend',  'stable')
    growth_trend   = macro.get('growth_trend', 'stable')
    real_rate      = macro.get('real_interest_rate', 0.0)
    gdp            = macro.get('gdp_yoy', 4.0)

    # Score from macro_snapshot.regime_score (0-100)
    health = min(1.0, regime_score / 100.0)

    # Adjustments from momentum signals
    if inf_mom == 'falling':   health += 0.05
    if rate_cycle == 'falling': health += 0.05
    if fx_trend == 'stable':    health += 0.03
    if growth_trend == 'stable': health += 0.02
    if real_rate > 2.0:         health -= 0.05  # tight real rates
    if real_rate < 0:           health -= 0.10  # negative real rates = inflation tax
    health = max(0.0, min(1.0, health))

    coupling_pressure = ('LOW'      if health > 0.70 else
                         'MODERATE' if health > 0.50 else
                         'HIGH'     if health > 0.30 else 'CRITICAL')

    return {
        'health':            round(health, 4),
        'state':             health_label(health),
        'macro_regime':      macro_regime,
        'regime_score':      regime_score,
        'coupling_pressure': coupling_pressure,
        'fx_trend':          fx_trend,
        'rate_cycle':        rate_cycle,
        'inf_momentum':      inf_mom,
        'real_rate':         real_rate,
        'gdp_yoy':           gdp,
        'detail':            f'{macro_regime} (score={regime_score}), FX={fx_trend}, rates={rate_cycle}',
    }


# ── All layers combined ──────────────────────────────────────────────────────

def layer_spectral():
    """
    Ph 21 — Spectral Cycle Intelligence Layer.

    Reads market-wide spectral regime distribution from feature_store.
    Summarises how many stocks are in each spectral state and derives
    an overall spectral health score for posture decisions.

    Spectral regimes:
      cyclical    (0) — clear, stable cycles → healthy
      expansion   (3) — structural shift / new energy → elevated
      compression (2) — pre-explosion compression → high alert
      noisy       (1) — spectrum dominated by noise → degraded
    """
    import sqlite3, datetime
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        today_str = datetime.date.today().isoformat()

        rows = conn.execute("""
            SELECT feature_value, COUNT(*) as cnt
            FROM feature_store
            WHERE feature_date = ? AND feature_name = 'spectral_regime'
            GROUP BY feature_value
        """, (today_str,)).fetchall()

        if not rows:
            # Try yesterday
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            rows = conn.execute("""
                SELECT feature_value, COUNT(*) as cnt
                FROM feature_store
                WHERE feature_date = ? AND feature_name = 'spectral_regime'
                GROUP BY feature_value
            """, (yesterday,)).fetchall()

        conn.close()

        if not rows:
            return {'health': 0.5, 'state': 'DEGRADED',
                    'spectral_market': 'unknown',
                    'detail': 'no spectral data — run phase21'}

        REGIME_NAMES = {0.0: "cyclical", 1.0: "noisy",
                        2.0: "compression", 3.0: "expansion"}
        regime_counts = {name: 0 for name in REGIME_NAMES.values()}
        total = 0
        for r in rows:
            name = REGIME_NAMES.get(float(r['feature_value']), 'cyclical')
            regime_counts[name] = int(r['cnt'])
            total += int(r['cnt'])

        if total == 0:
            return {'health': 0.5, 'state': 'DEGRADED',
                    'spectral_market': 'unknown', 'detail': 'zero regimes'}

        # Fractions
        pct_cyclical    = regime_counts['cyclical']    / total
        pct_noisy       = regime_counts['noisy']       / total
        pct_compression = regime_counts['compression'] / total
        pct_expansion   = regime_counts['expansion']   / total

        # Dominant spectral market state
        dominant = max(regime_counts, key=regime_counts.get)

        # Health score:
        #   cyclical → good (clear structure)
        #   expansion → elevated (building energy)
        #   compression → high alert (pre-breakout)
        #   noisy → degraded (no pattern)
        health = (
            1.00 * pct_cyclical +
            0.80 * pct_expansion +
            0.70 * pct_compression +
            0.35 * pct_noisy
        )
        health = float(max(0.0, min(1.0, health)))

        # Alert when many stocks in compression (potential mass breakout)
        compression_alert = pct_compression > 0.25

        detail = (
            f"cyc={regime_counts['cyclical']} "
            f"exp={regime_counts['expansion']} "
            f"cmp={regime_counts['compression']} "
            f"nsy={regime_counts['noisy']}"
        )
        if compression_alert:
            detail += " ⚠️ COMPRESSION_ALERT"

        return {
            'health':             round(health, 4),
            'state':              health_label(health),
            'spectral_market':    dominant,
            'regime_counts':      regime_counts,
            'pct_cyclical':       round(pct_cyclical, 3),
            'pct_noisy':          round(pct_noisy, 3),
            'pct_compression':    round(pct_compression, 3),
            'pct_expansion':      round(pct_expansion, 3),
            'compression_alert':  compression_alert,
            'n_stocks':           total,
            'detail':             detail,
        }

    except Exception as e:
        return {'health': 0.5, 'state': 'DEGRADED',
                'spectral_market': 'unknown', 'detail': f'error: {e}'}


def run_all_layers(data, cur_ind, macro):
    snaps = latest_snapshot(data, cur_ind)
    enrich(data)  # ensure returns computed
    snaps = latest_snapshot(data, cur_ind)  # refresh with enriched data

    return {
        'latent':      layer_latent(snaps),
        'fields':      layer_fields(snaps),
        'propagation': layer_propagation(data),
        'energy':      layer_energy(snaps, data),
        'causality':   layer_causality(data),
        'decision':    layer_decision(snaps),
        'evolution':   layer_evolution(),
        'coupling':    layer_coupling(macro),
        'spectral':    layer_spectral(),   # Ph 21 — Spectral Cycle Intelligence
    }


# ── Confidence ───────────────────────────────────────────────────────────────

def compute_confidence(layers):
    total = sum(
        LAYER_WEIGHTS[k] * layers[k]['health']
        for k in LAYER_WEIGHTS if k in layers
    )
    # Normalise
    weight_sum = sum(LAYER_WEIGHTS.values())
    confidence = total / weight_sum

    # Critical layer penalty
    critical_layers = [k for k in layers if layers[k]['state'] == 'CRITICAL']
    for _ in critical_layers:
        confidence *= 0.85

    return round(max(0.0, min(1.0, confidence)), 4)


# ── Conflict Detection ───────────────────────────────────────────────────────

CONFLICT_DEFS = [
    {
        'id': 'BULL_ENERGY_MISMATCH',
        'desc': 'BULL regime but energy exhausted — rally may be unsustainable',
        'test': lambda L: (L['latent']['regime'] == 'BULL' and
                           L['energy']['energy_state'] in ('EXHAUSTED', 'LOW')),
        'severity': 'HIGH',
        'resolution': 'Reduce exposure to 60% max; watch for reversal',
    },
    {
        'id': 'CONTAGION_RISK',
        'desc': 'High cross-sector synchronisation — propagation driven by external shock',
        'test': lambda L: L['propagation']['contagion_state'] in ('CRISIS_SYNC', 'HIGH_CONTAGION'),
        'severity': 'CRITICAL',
        'resolution': 'Prioritise world coupling layer; reduce single-stock exposure',
    },
    {
        'id': 'CAUSALITY_DEGRADED',
        'desc': 'Causal event chains inactive — signal-to-noise severely reduced',
        'test': lambda L: L['causality']['health'] < 0.35,
        'severity': 'HIGH',
        'resolution': 'Fall back to regime + macro for decisions; avoid causal-based timing',
    },
    {
        'id': 'EVOLUTION_TRUST_PENALTY',
        'desc': 'Model evolution signals REDUCE_CONFIDENCE or REBUILD',
        'test': lambda L: L['evolution']['trust'] in ('REDUCE_CONFIDENCE', 'REBUILD', 'INVALIDATE'),
        'severity': 'MEDIUM',
        'resolution': f'Apply confidence penalty; widen uncertainty bands',
    },
    {
        'id': 'PROPAGATION_FRAGMENTED',
        'desc': 'Propagation blocked — market fragmented, moves are idiosyncratic',
        'test': lambda L: L['propagation']['contagion_state'] == 'FRAGMENTED',
        'severity': 'MEDIUM',
        'resolution': 'Reduce correlated-pair strategies; favour sector-specific analysis',
    },
    {
        'id': 'MACRO_OVERRIDE',
        'desc': 'World coupling under HIGH or CRITICAL pressure',
        'test': lambda L: L['coupling']['coupling_pressure'] in ('HIGH', 'CRITICAL'),
        'severity': 'CRITICAL',
        'resolution': 'External conditions override internal signals; reduce all positions',
    },
    {
        'id': 'FIELD_FORCE_WEAK',
        'desc': 'Sector force fields weak — no clear leadership or divergence',
        'test': lambda L: L['fields']['health'] < 0.30,
        'severity': 'LOW',
        'resolution': 'Avoid momentum-based sector rotation; wait for divergence',
    },
    {
        'id': 'DECISION_PARALYSIS',
        'desc': 'Decision engine in UNCERTAIN or PARALYZED state — high avoid allocation',
        'test': lambda L: L['decision']['avoid_pct'] > 0.45,
        'severity': 'HIGH',
        'resolution': 'Wait for decision clarity; inaction recommended',
    },
    {
        'id': 'SPECTRAL_NOISE_DOMINANT',
        'desc': 'Ph21: >50% of stocks in noisy spectral regime — no reliable cyclical structure',
        'test': lambda L: (L.get('spectral', {}).get('pct_noisy', 0) or 0) > 0.50,
        'severity': 'MEDIUM',
        'resolution': 'Disable cycle-based entry timing; rely on technical + ML signals only',
    },
    {
        'id': 'SPECTRAL_MASS_COMPRESSION',
        'desc': 'Ph21: >25% of stocks in compression regime — mass breakout imminent',
        'test': lambda L: (L.get('spectral', {}).get('compression_alert', False)),
        'severity': 'HIGH',
        'resolution': 'Raise alert level; tighten entries; watch for explosion cluster event',
    },
    {
        'id': 'SPECTRAL_NOISY_BULL_DIVERGE',
        'desc': 'Ph21: Noisy spectrum despite BULL regime — cycle structure disintegrating',
        'test': lambda L: (L['latent']['regime'] == 'BULL' and
                           (L.get('spectral', {}).get('spectral_market') == 'noisy')),
        'severity': 'MEDIUM',
        'resolution': 'Shorten hold periods; rally may lack rhythmic support',
    },
]


def detect_conflicts(layers):
    active = []
    for cd in CONFLICT_DEFS:
        try:
            if cd['test'](layers):
                active.append({
                    'id':         cd['id'],
                    'desc':       cd['desc'],
                    'severity':   cd['severity'],
                    'resolution': cd['resolution'],
                })
        except Exception:
            pass
    return active


def arbitrate(layers, conflicts):
    """Determine which layer dominates under current conditions."""
    # Emergency overrides
    if any(c['id'] == 'MACRO_OVERRIDE' for c in conflicts):
        return {
            'winner': 'COUPLING',
            'reason': 'External macro pressure overrides all internal signals',
            'priority_stack': ['COUPLING', 'PROPAGATION', 'CAUSALITY', 'DECISION', 'LATENT'],
        }
    if any(c['id'] == 'CONTAGION_RISK' for c in conflicts):
        return {
            'winner': 'PROPAGATION',
            'reason': 'Crisis synchronisation — propagation dynamics dominate',
            'priority_stack': ['PROPAGATION', 'COUPLING', 'ENERGY', 'DECISION', 'LATENT'],
        }
    if any(c['id'] == 'CAUSALITY_DEGRADED' for c in conflicts):
        return {
            'winner': 'REGIME_MACRO',
            'reason': 'Causal chains inactive — fall back to regime + macro',
            'priority_stack': ['LATENT', 'COUPLING', 'FIELDS', 'ENERGY', 'DECISION'],
        }
    if any(c['id'] == 'DECISION_PARALYSIS' for c in conflicts):
        return {
            'winner': 'INACTION',
            'reason': 'Decision engine paralysed — inaction is the recommended action',
            'priority_stack': ['DECISION', 'COUPLING', 'LATENT', 'ENERGY', 'CAUSALITY'],
        }

    # Normal weighted consensus — highest health layer leads
    ranked = sorted(layers.items(), key=lambda x: x[1]['health'], reverse=True)
    winner = ranked[0][0].upper()

    return {
        'winner': winner,
        'reason': f'Highest health layer ({winner}, h={ranked[0][1]["health"]:.3f}) leads consensus',
        'priority_stack': [k.upper() for k, _ in ranked[:5]],
    }


# ── Posture ───────────────────────────────────────────────────────────────────

def compute_posture(layers, conflicts, confidence, macro):
    regime = layers['latent'].get('regime', 'MIXED')

    BASE_EXPOSURE = {
        'BULL': 78, 'BEAR': 22, 'MIXED': 50,
        'UNKNOWN': 40, 'CRISIS': 10, 'CALM': 62,
    }
    exposure = float(BASE_EXPOSURE.get(regime, 50))

    # Confidence scaling
    exposure *= (0.4 + confidence * 0.6)  # 40%–100% of base

    # Conflict penalties
    sev_pen = {'CRITICAL': 0.65, 'HIGH': 0.82, 'MEDIUM': 0.92, 'LOW': 0.97}
    for c in conflicts:
        exposure *= sev_pen.get(c['severity'], 1.0)

    # Evolution trust
    # UNKNOWN = model not yet calibrated; treat as near-neutral (0.90) not penalised (0.85)
    trust = layers['evolution'].get('trust', 'UNKNOWN')
    trust_pen = {'TRUST': 1.0, 'REDUCE_CONFIDENCE': 0.80, 'REBUILD': 0.55, 'INVALIDATE': 0.35}
    exposure  *= trust_pen.get(trust, 0.90)   # FIX: UNKNOWN → 0.90 (was 0.85)

    # Macro adjustment
    if macro.get('available'):
        regime_score = macro.get('regime_score', 50.0)
        exposure    *= (0.7 + (regime_score / 100.0) * 0.3)

    exposure = max(5.0, min(95.0, exposure))

    # FIX: Added BULLISH label between MODERATE_LONG and AGGRESSIVE_LONG
    posture = ('AGGRESSIVE_LONG' if exposure >= 75 else
               'BULLISH'         if exposure >= 62 else
               'MODERATE_LONG'   if exposure >= 50 else
               'NEUTRAL'         if exposure >= 38 else
               'DEFENSIVE'       if exposure >= 18 else 'AVOID')

    # Max single-position size
    max_pos = (7 if exposure >= 70 else
               5 if exposure >= 50 else
               3 if exposure >= 30 else 2)

    # Stop priority
    stop_p  = ('TIGHT' if exposure < 40 or len(conflicts) >= 3 else
               'NORMAL' if exposure < 65 else 'LOOSE')

    return {
        'posture':            posture,
        'exposure_pct':       round(exposure, 1),
        'max_position_pct':   max_pos,
        'stop_priority':      stop_p,
        'regime':             regime,
        'trust':              trust,
        'confidence':         round(confidence, 4),
        'active_conflicts':   len(conflicts),
    }


# ── Opportunities & Avoid ────────────────────────────────────────────────────

def find_opportunities(snaps, top_n=5):
    scored = []
    for s in snaps:
        rsi   = s['rsi']
        vr    = s['vol_ratio']
        mom   = s['mom5']
        ema20 = s['above_ema20']
        macdh = s['macd_hist']
        adx   = s['adx']

        # Opportunity: RSI 50-68, vol_ratio > 1.2, positive momentum, above EMA20
        if not (50 < rsi < 68 and vr > 1.2 and mom > 0.003 and ema20):
            continue

        score = (rsi / 100 * 0.2 + min(vr, 3) / 3 * 0.3 +
                 min(mom * 20, 1.0) * 0.25 +
                 min(adx, 40) / 40 * 0.15 +
                 (0.10 if macdh > 0 else 0))
        scored.append({'symbol': s['symbol'], 'sector': s['sector'],
                       'score': round(score, 4), 'rsi': round(rsi, 1),
                       'vol_ratio': round(vr, 3), 'mom5': round(mom * 100, 3),
                       'adx': round(adx, 1)})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_n]


def find_avoid_zones(snaps, top_n=5):
    avoids = []
    for s in snaps:
        rsi  = s['rsi']
        vr   = s['vol_ratio']
        mom  = s['mom5']
        adx  = s['adx']

        risky = False
        reasons = []
        if rsi > 75:  risky = True; reasons.append(f'RSI={rsi:.0f} overbought')
        if rsi < 35:  risky = True; reasons.append(f'RSI={rsi:.0f} oversold/panic')
        if vr  < 0.35: risky = True; reasons.append(f'vol_ratio={vr:.2f} compressed')
        if mom < -0.015: risky = True; reasons.append(f'mom5={mom*100:.2f}% falling')

        if risky:
            avoids.append({'symbol': s['symbol'], 'sector': s['sector'],
                           'rsi': round(rsi, 1), 'vol_ratio': round(vr, 3),
                           'mom5': round(mom * 100, 3), 'reasons': reasons})

    return avoids[:top_n]


# ── Delta vs yesterday ───────────────────────────────────────────────────────

def compute_delta(today_snap, log):
    if not log:
        return {'available': False}
    yesterday = None
    for entry in reversed(log):
        if entry.get('date') != today_snap.get('date'):
            yesterday = entry
            break
    if not yesterday:
        return {'available': False}

    def delta_str(a, b, key):
        va = safe(a.get(key)); vb = safe(b.get(key))
        if va == 0 and vb == 0:
            return None
        d = va - vb
        sign = '+' if d >= 0 else ''
        return f'{sign}{d:.4f}'

    changes = {}
    for key in ['global_confidence', 'exposure_pct']:
        d = delta_str(today_snap, yesterday, key)
        if d:
            changes[key] = d

    regime_change = (today_snap.get('regime') != yesterday.get('regime'))
    posture_change = (today_snap.get('posture') != yesterday.get('posture'))
    trust_change   = (today_snap.get('trust')   != yesterday.get('trust'))

    narrative = []
    if regime_change:
        narrative.append(f"Regime: {yesterday.get('regime')} → {today_snap.get('regime')}")
    if posture_change:
        narrative.append(f"Posture: {yesterday.get('posture')} → {today_snap.get('posture')}")
    if trust_change:
        narrative.append(f"Trust: {yesterday.get('trust')} → {today_snap.get('trust')}")

    conf_d = safe(today_snap.get('global_confidence', 0)) - safe(yesterday.get('global_confidence', 0))
    if abs(conf_d) > 0.02:
        narrative.append(f"Confidence: {conf_d:+.3f}")

    return {
        'available':       True,
        'yesterday_date':  yesterday.get('date'),
        'metric_changes':  changes,
        'regime_changed':  regime_change,
        'posture_changed': posture_change,
        'trust_changed':   trust_change,
        'narrative':       narrative or ['No significant changes from yesterday'],
    }


# ── Data Health ────────────────────────────────────────────────────────────

def cmd_data_health(db, data, cur_ind, macro):
    now = int(time.time())
    active_exclusions = set()
    try:
        rows = db.execute("""
            SELECT symbol, bar_time
            FROM data_quality_bar_exclusions
            WHERE source_table = 'ohlcv_history'
              AND status = 'ACTIVE'
        """).fetchall()
        active_exclusions = {(r['symbol'], int(r['bar_time'])) for r in rows}
    except Exception:
        active_exclusions = set()

    # OHLCV freshness
    max_ts = max((bars[-1]['bar_time'] for bars in data.values() if bars), default=0)
    data_age_hours = (now - max_ts) / 3600 if max_ts else 9999

    # Coverage
    n_symbols    = len(data)
    n_with_ind   = sum(1 for sym in data if sym in cur_ind)
    ind_coverage = n_with_ind / max(n_symbols, 1)

    # Anomaly detection: extreme returns
    # Filter out corporate-action artifacts (stock consolidations, reverse splits) which
    # show as massive one-time jumps (>500%) on the first bar of new OHLCV history.
    # Only flag genuine anomalies: moderate extreme returns (50%-500%) that aren't
    # isolated first-bar events (which are always corporate actions, not data errors).
    extreme_rets = []
    zero_vol_days = 0
    for sym, bars in data.items():
        bars_sorted = sorted(bars, key=lambda b: b['bar_time'])
        first_bar_time = bars_sorted[0]['bar_time'] if bars_sorted else 0
        for b in bars:
            if (sym, int(b['bar_time'])) in active_exclusions:
                continue
            ret_abs = abs(b.get('ret', 0))
            if ret_abs > 0.50:
                # Skip corporate-action artifacts: >500% single-bar and it's the first few bars
                if ret_abs > 5.0:  # >500% — almost certainly corporate action
                    continue
                # Skip if this is the first bar with OHLCV for this symbol (data loading artifact)
                if b['bar_time'] == first_bar_time:
                    continue
                extreme_rets.append({'symbol': sym, 'ret': round(b['ret'], 4),
                                     'bar_time': b['bar_time']})
            if b['volume'] == 0:
                zero_vol_days += 1

    # Macro freshness
    macro_age_days = 999
    if macro.get('available') and macro.get('fetched_at'):
        try:
            import datetime
            fetched_raw = str(macro['fetched_at'])
            if fetched_raw.endswith('Z'):
                fetched_raw = fetched_raw[:-1] + '+00:00'
            fetched = datetime.datetime.fromisoformat(fetched_raw)
            now_dt = datetime.datetime.now(fetched.tzinfo) if fetched.tzinfo else datetime.datetime.now()
            macro_age_days = (now_dt - fetched).days
        except Exception:
            pass

    # Health scoring
    ohlcv_health   = max(0, 1.0 - data_age_hours / 72)    # fresh if < 72h
    ind_health     = ind_coverage
    macro_health   = max(0, 1.0 - macro_age_days / 14)    # fresh if < 14 days
    anomaly_health = max(0, 1.0 - len(extreme_rets) / 50)

    overall = (ohlcv_health * 0.35 + ind_health * 0.30 +
               macro_health * 0.20 + anomaly_health * 0.15)

    checks = {
        'ohlcv_freshness':    {'score': round(ohlcv_health, 3),  'age_hours': round(data_age_hours, 1)},
        'indicator_coverage': {'score': round(ind_health, 3),    'coverage_pct': round(ind_coverage*100,1)},
        'macro_freshness':    {'score': round(macro_health, 3),  'age_days': macro_age_days},
        'anomaly_rate':       {'score': round(anomaly_health,3), 'extreme_rets': len(extreme_rets),
                               'zero_vol_days': zero_vol_days},
    }

    return {
        'overall_health':    round(overall, 4),
        'overall_state':     health_label(overall),
        'n_symbols':         n_symbols,
        'n_with_indicators': n_with_ind,
        'checks':            checks,
        'warnings':          ([f'OHLCV data {data_age_hours:.0f}h old — may be stale']
                               if data_age_hours > 48 else []) +
                             ([f'Macro data {macro_age_days}d old'] if macro_age_days > 7 else []) +
                             ([f'{len(extreme_rets)} extreme return anomalies'] if extreme_rets else []),
    }


# ── Instability Watch ────────────────────────────────────────────────────────

def cmd_instability_watch(layers, conflicts, confidence):
    alerts = []

    # Confidence collapse
    if confidence < 0.40:
        alerts.append({'level': 'CRITICAL', 'id': 'CONFIDENCE_COLLAPSE',
                       'msg': f'Global confidence collapsed to {confidence:.1%}'})
    elif confidence < 0.55:
        alerts.append({'level': 'WARNING', 'id': 'LOW_CONFIDENCE',
                       'msg': f'Global confidence low: {confidence:.1%}'})

    # Critical conflicts
    for c in conflicts:
        if c['severity'] == 'CRITICAL':
            alerts.append({'level': 'CRITICAL', 'id': c['id'], 'msg': c['desc']})

    # Causal collapse
    if layers['causality']['health'] < 0.25:
        alerts.append({'level': 'CRITICAL', 'id': 'CAUSAL_COLLAPSE',
                       'msg': 'Causal structures collapsed — event chains inactive'})

    # Energy exhaustion
    if layers['energy']['energy_state'] == 'EXHAUSTED':
        alerts.append({'level': 'WARNING', 'id': 'ENERGY_EXHAUSTED',
                       'msg': 'Market energy exhausted — expect stagnation or reversal'})

    # Fragmented propagation
    if layers['propagation']['contagion_state'] == 'FRAGMENTED':
        alerts.append({'level': 'INFO', 'id': 'PROPAGATION_FRAGMENTED',
                       'msg': 'Market fragmented — moves are idiosyncratic, not systemic'})

    # Layer count with CRITICAL state
    n_critical = sum(1 for l in layers.values() if l['state'] == 'CRITICAL')
    if n_critical >= 3:
        alerts.append({'level': 'CRITICAL', 'id': 'MULTI_LAYER_FAILURE',
                       'msg': f'{n_critical} layers in CRITICAL state simultaneously'})

    # Trust invalidation
    if layers['evolution']['trust'] == 'INVALIDATE':
        alerts.append({'level': 'CRITICAL', 'id': 'MODEL_INVALIDATED',
                       'msg': 'Evolution engine: model INVALIDATED — all signals unreliable'})

    escalation = ('CRISIS'  if any(a['level'] == 'CRITICAL' for a in alerts) else
                  'CAUTION' if any(a['level'] == 'WARNING'  for a in alerts) else 'NOMINAL')

    safety_action = {
        'CRISIS':  'Reduce all exposure immediately; await re-stabilisation',
        'CAUTION': 'Reduce exposure to DEFENSIVE level; tighten stops',
        'NOMINAL': 'Normal operations; maintain current posture',
    }[escalation]

    return {
        'escalation_level': escalation,
        'safety_action':    safety_action,
        'n_alerts':         len(alerts),
        'alerts':           alerts,
    }


# ── Evolution Sync ────────────────────────────────────────────────────────────

def cmd_evolution_sync(layers, confidence, posture_result):
    raw_evo  = load_json_log(EVO_LOG)
    evo_log  = raw_evo.get('entries', []) if isinstance(raw_evo, dict) else (raw_evo or [])
    coup_log = load_json_log(COUP_LOG)
    orch_log = load_json_log(ORCH_LOG)

    today = time.strftime('%Y-%m-%d')

    # Check latest evo log entry
    evo_latest  = evo_log[-1]  if evo_log  else {}
    coup_latest = coup_log[-1] if coup_log else {}

    # Build sync entry
    sync_entry = {
        'date':              today,
        'regime':            layers['latent']['regime'],
        'global_confidence': confidence,
        'posture':           posture_result.get('posture'),
        'exposure_pct':      posture_result.get('exposure_pct'),
        'trust':             layers['evolution']['trust'],
        'layer_health': {k: round(v['health'], 4) for k, v in layers.items()},
        'dominant_coupling': coup_latest.get('dominant_force', 'unknown'),
        'evo_trust':         evo_latest.get('trust_level', evo_latest.get('decision', 'UNKNOWN')),
    }

    append_json_log(ORCH_LOG, sync_entry)

    return {
        'sync_status':       'OK',
        'today':             today,
        'orch_log_entries':  len(orch_log) + 1,
        'evo_log_entries':   len(evo_log),
        'coup_log_entries':  len(coup_log),
        'synced_entry':      sync_entry,
        'evo_latest_date':   evo_latest.get('date', 'none'),
        'coup_latest_date':  coup_latest.get('date', 'none'),
    }


# ── Daily Report ─────────────────────────────────────────────────────────────

def cmd_daily_report(db, con, data, cur_ind, macro):
    snaps     = latest_snapshot(data, cur_ind)
    layers    = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    arb        = arbitrate(layers, conflicts)
    posture_r  = compute_posture(layers, conflicts, confidence, macro)
    watch      = cmd_instability_watch(layers, conflicts, confidence)
    opps       = find_opportunities(snaps)
    avoids     = find_avoid_zones(snaps)

    orch_log   = load_json_log(ORCH_LOG)
    today_snap = {
        'date':              time.strftime('%Y-%m-%d'),
        'regime':            layers['latent']['regime'],
        'global_confidence': confidence,
        'posture':           posture_r['posture'],
        'exposure_pct':      posture_r['exposure_pct'],
        'trust':             layers['evolution']['trust'],
    }
    delta = compute_delta(today_snap, orch_log)

    # Strategic outlook
    regime       = layers['latent']['regime']
    trust        = layers['evolution']['trust']
    mac_regime   = layers['coupling']['macro_regime']
    infl_mom     = macro.get('inflation_momentum', 'stable')
    rate_c       = macro.get('rate_cycle', 'stable')
    outlook_bias = ('BULLISH'  if regime == 'BULL' and trust in ('TRUST', 'REDUCE_CONFIDENCE') else
                    'BEARISH'  if regime == 'BEAR'  else
                    'CAUTIOUS' if len(conflicts) >= 2 else 'NEUTRAL')
    outlook_text = (
        f'{outlook_bias}: {mac_regime} macro backdrop with '
        f'inflation {infl_mom}, rates {rate_c}. '
        f'Model trust={trust}. '
        f'{len(conflicts)} active conflict(s). '
        f'Target exposure {posture_r["exposure_pct"]:.0f}%.'
    )

    # Save to DB daily_reports table
    report_text = json.dumps({
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'confidence':   confidence,
        'posture':      posture_r['posture'],
        'regime':       regime,
    })
    try:
        today_str = time.strftime('%Y-%m-%d')
        db.execute("""
            INSERT OR REPLACE INTO daily_reports
            (report_date, report_text, created_at)
            VALUES (?, ?, datetime('now'))
        """, [today_str, report_text])
        con.commit()
    except Exception:
        pass

    return {
        's01_regime': {
            'title': 'Market Regime Status',
            'regime': regime,
            'avg_rsi': layers['latent']['avg_rsi'],
            'bull_pct': layers['latent']['bull_pct'],
            'bear_pct': layers['latent']['bear_pct'],
            'above_ema20': layers['latent']['above_ema20'],
            'above_ema50': layers['latent']['above_ema50'],
        },
        's02_forces': {
            'title': 'Dominant Behavioral Forces',
            'sector_spread': layers['fields']['sector_spread'],
            'top_sector': layers['fields']['top_sector'],
            'bot_sector': layers['fields']['bot_sector'],
            'avg_adx': layers['fields']['avg_adx'],
            'n_sectors': layers['fields']['n_sectors'],
        },
        's03_propagation': {
            'title': 'Propagation & Contagion',
            'contagion_score': layers['propagation']['contagion_score'],
            'state': layers['propagation']['contagion_state'],
            'n_pairs': layers['propagation']['n_sector_pairs'],
        },
        's04_energy': {
            'title': 'Energy Dynamics',
            'energy_state':    layers['energy']['energy_state'],
            'ret_dispersion':  layers['energy']['ret_dispersion'],
            'avg_vol_ratio':   layers['energy']['avg_vol_ratio'],
            'high_vol_pct':    layers['energy']['high_vol_pct'],
            'bb_compressed':   layers['energy']['bb_compressed'],
        },
        's05_causality': {
            'title': 'Temporal Causal Graph',
            'causal_state':  layers['causality']['causal_state'],
            'event_rate':    layers['causality']['event_rate'],
            'n_event_types': layers['causality']['n_event_types'],
            'top_events':    layers['causality']['top_events'],
        },
        's06_world': {
            'title': 'World Coupling Effects',
            'macro_regime':      layers['coupling']['macro_regime'],
            'regime_score':      layers['coupling']['regime_score'],
            'coupling_pressure': layers['coupling']['coupling_pressure'],
            'fx_trend':          layers['coupling']['fx_trend'],
            'rate_cycle':        layers['coupling']['rate_cycle'],
            'inf_momentum':      layers['coupling']['inf_momentum'],
            'real_rate':         layers['coupling']['real_rate'],
            'gdp_yoy':           layers['coupling']['gdp_yoy'],
        },
        's07_confidence': {
            'title':       'Structural Confidence Assessment',
            'global':      confidence,
            'per_layer':   {k: round(v['health'], 4) for k, v in layers.items()},
            'arbitration': arb,
            'n_conflicts': len(conflicts),
        },
        's08_warnings': {
            'title':     'Key Instability Warnings',
            'escalation': watch['escalation_level'],
            'action':     watch['safety_action'],
            'alerts':     watch['alerts'],
        },
        's09_posture': {
            'title':         'Recommended Exposure Posture',
            'posture':        posture_r['posture'],
            'exposure_pct':   posture_r['exposure_pct'],
            'max_position':   posture_r['max_position_pct'],
            'stop_priority':  posture_r['stop_priority'],
        },
        's10_opportunities': {
            'title': 'Top Opportunities',
            'items': opps,
        },
        's11_avoid': {
            'title': 'Avoid / Risk Zones',
            'items': avoids,
        },
        's12_delta': {
            'title': 'What Changed vs Yesterday',
            **delta,
        },
        's13_evolution': {
            'title':        'Self-Evolution Observations',
            'trust':         layers['evolution']['trust'],
            'log_entries':   layers['evolution']['log_entries'],
            'active_hypos':  'see egx:evolve:hypotheses',
        },
        's14_trust': {
            'title':      'Model Trust Level',
            'trust':       layers['evolution']['trust'],
            'confidence':  confidence,
            'n_critical':  sum(1 for l in layers.values() if l['state'] == 'CRITICAL'),
        },
        's15_outlook': {
            'title':       'Strategic Outlook',
            'bias':        outlook_bias,
            'text':        outlook_text,
            'horizon':     '3-7 trading sessions',
        },
        '__meta__': {
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'n_symbols':    len(data),
            'n_snaps':      len(snaps),
        },
    }


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_orchestrate_now(db, data, cur_ind, macro):
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    arb        = arbitrate(layers, conflicts)
    posture_r  = compute_posture(layers, conflicts, confidence, macro)

    snapshot = {
        'date':              time.strftime('%Y-%m-%d'),
        'time':              time.strftime('%H:%M'),
        'regime':            layers['latent']['regime'],
        'global_confidence': confidence,
        'posture':           posture_r['posture'],
        'exposure_pct':      posture_r['exposure_pct'],
        'trust':             layers['evolution']['trust'],
        'dominant_layer':    arb['winner'],
        'n_conflicts':       len(conflicts),
        'n_symbols':         len(data),
        'layer_health':      {k: {'health': round(v['health'], 4), 'state': v['state'],
                                  'detail': v.get('detail', '')}
                              for k, v in layers.items()},
    }
    return snapshot


def cmd_arbitrate(db, data, cur_ind, macro):
    layers    = run_all_layers(data, cur_ind, macro)
    conflicts = detect_conflicts(layers)
    arb       = arbitrate(layers, conflicts)
    return {
        **arb,
        'active_conflicts': conflicts,
        'layer_states': {k: v['state'] for k, v in layers.items()},
    }


def cmd_confidence_map(db, data, cur_ind, macro):
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)

    per_layer = {k: {
        'health': round(v['health'], 4),
        'state':  v['state'],
        'weight': LAYER_WEIGHTS.get(k, 0),
        'contribution': round(v['health'] * LAYER_WEIGHTS.get(k, 0), 5),
        'detail': v.get('detail', ''),
    } for k, v in layers.items()}

    return {
        'global_confidence': confidence,
        'confidence_label':  ('HIGH' if confidence > 0.75 else
                              'MODERATE' if confidence > 0.55 else
                              'LOW' if confidence > 0.35 else 'CRITICAL'),
        'per_layer': per_layer,
        'n_critical_layers': sum(1 for v in layers.values() if v['state'] == 'CRITICAL'),
        'n_conflicts': len(conflicts),
    }


def cmd_conflict_scan(db, data, cur_ind, macro):
    layers    = run_all_layers(data, cur_ind, macro)
    conflicts = detect_conflicts(layers)
    return {
        'n_conflicts':   len(conflicts),
        'conflicts':     conflicts,
        'severity_dist': dict(Counter(c['severity'] for c in conflicts)),
        'all_clear':     len(conflicts) == 0,
    }


def cmd_posture(db, data, cur_ind, macro):
    snaps      = latest_snapshot(data, cur_ind)
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    posture_r  = compute_posture(layers, conflicts, confidence, macro)
    opps       = find_opportunities(snaps)
    avoids     = find_avoid_zones(snaps, top_n=3)

    # Sector guidance
    fields = layers['fields']
    sector_guidance = {
        'overweight':  [fields.get('top_sector', '—')],
        'underweight': [fields.get('bot_sector', '—')],
    }

    return {
        **posture_r,
        'sector_guidance':   sector_guidance,
        'top_opportunities': opps[:3],
        'avoid_zones':       avoids,
        'rationale': [
            f"Regime: {layers['latent']['regime']} ({layers['latent']['detail']})",
            f"Macro: {layers['coupling']['macro_regime']} (score={layers['coupling']['regime_score']:.0f})",
            f"Causality: {layers['causality']['causal_state']}",
            f"Energy: {layers['energy']['energy_state']}",
            f"Trust: {layers['evolution']['trust']}",
        ],
    }


def cmd_instability_watch_cmd(db, data, cur_ind, macro):
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    return cmd_instability_watch(layers, conflicts, confidence)


def cmd_evolution_sync_cmd(db, data, cur_ind, macro):
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    posture_r  = compute_posture(layers, conflicts, confidence, macro)
    return cmd_evolution_sync(layers, confidence, posture_r)


def cmd_orchestrate_full(db, data, cur_ind, macro):
    snaps      = latest_snapshot(data, cur_ind)
    layers     = run_all_layers(data, cur_ind, macro)
    confidence = compute_confidence(layers)
    conflicts  = detect_conflicts(layers)
    arb        = arbitrate(layers, conflicts)
    posture_r  = compute_posture(layers, conflicts, confidence, macro)
    watch      = cmd_instability_watch(layers, conflicts, confidence)
    opps       = find_opportunities(snaps)
    avoids     = find_avoid_zones(snaps)
    data_h     = cmd_data_health(db, data, cur_ind, macro)
    orch_log   = load_json_log(ORCH_LOG)
    today_snap = {
        'date': time.strftime('%Y-%m-%d'), 'regime': layers['latent']['regime'],
        'global_confidence': confidence,   'posture': posture_r['posture'],
        'exposure_pct': posture_r['exposure_pct'], 'trust': layers['evolution']['trust'],
    }
    delta  = compute_delta(today_snap, orch_log)
    sync_r = cmd_evolution_sync(layers, confidence, posture_r)

    spec_layer = layers.get('spectral', {})

    return {
        'orchestration_steps': {
            'data_health':    data_h['overall_state'],
            'layers':         '9/9 computed',
            'conflicts':      f"{len(conflicts)} detected",
            'arbitration':    arb['winner'],
            'confidence':     f"{confidence:.1%}",
            'posture':        posture_r['posture'],
            'instability':    watch['escalation_level'],
            'evolution_sync': sync_r['sync_status'],
            'spectral_market': spec_layer.get('spectral_market', 'unknown'),
        },
        'layers':     {k: {'health': round(v['health'],4), 'state': v['state'],
                           'detail': v.get('detail','')} for k, v in layers.items()},
        'conflicts':  conflicts,
        'arbitration': arb,
        'confidence': confidence,
        'posture':    posture_r,
        'watch':      watch,
        'opportunities': opps,
        'avoid_zones':   avoids,
        'delta':         delta,
        'data_health':   data_h,
        'macro':    {k: v for k, v in (macro or {}).items() if k != 'available'},
        'synthesis': {
            'regime':             layers['latent']['regime'],
            'macro_regime':       layers['coupling']['macro_regime'],
            'global_confidence':  confidence,
            'dominant_layer':     arb['winner'],
            'posture':            posture_r['posture'],
            'exposure_pct':       posture_r['exposure_pct'],
            'escalation':         watch['escalation_level'],
            'trust':              layers['evolution']['trust'],
            'spectral_market':    spec_layer.get('spectral_market', 'unknown'),
            'compression_alert':  spec_layer.get('compression_alert', False),
            'pct_at_cycle_bottom': spec_layer.get('pct_compression', 0),
        },
    }


# ── Dispatch ─────────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'orchestrate_now'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}',
                          'available': sorted(COMMANDS)}))
        sys.exit(1)

    try:
        json.loads(sys.stdin.read() or '{}')
    except Exception:
        pass

    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    con.row_factory = _sq.Row
    db = con.cursor()

    try:
        data, cur_ind = load_ohlcv(db)
        enrich(data)
        macro = load_macro(db)

        dispatch = {
            'data_health':       lambda: cmd_data_health(db, data, cur_ind, macro),
            'orchestrate_now':   lambda: cmd_orchestrate_now(db, data, cur_ind, macro),
            'arbitrate':         lambda: cmd_arbitrate(db, data, cur_ind, macro),
            'confidence_map':    lambda: cmd_confidence_map(db, data, cur_ind, macro),
            'conflict_scan':     lambda: cmd_conflict_scan(db, data, cur_ind, macro),
            'posture':           lambda: cmd_posture(db, data, cur_ind, macro),
            'instability_watch': lambda: cmd_instability_watch_cmd(db, data, cur_ind, macro),
            'evolution_sync':    lambda: cmd_evolution_sync_cmd(db, data, cur_ind, macro),
            'daily_report':      lambda: cmd_daily_report(db, con, data, cur_ind, macro),
            'orchestrate_full':  lambda: cmd_orchestrate_full(db, data, cur_ind, macro),
        }

        result = dispatch[cmd]()
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()}))
    finally:
        con.close()


if __name__ == '__main__':
    main()
