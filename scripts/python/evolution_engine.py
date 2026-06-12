#!/usr/bin/env python3
"""
Phase 7: Self-Evolving Market Intelligence Engine
Evaluates model health, detects decay, generates hypotheses, evolves architectures.
"""

import json, sys, sqlite3, math, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics

DB_PATH      = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')
EVO_LOG_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'evolution_memory.json')

# ── Inherited phase assumptions ───────────────────────────────────────────────
PHASE5_INVARIANTS = [
    {'from':'PANIC_ONSET',      'to':'RECOVERY_ONSET',   'lag':1, 'lift':11.08},
    {'from':'VOL_COMPRESSION',  'to':'VOL_EXPLOSION',    'lag':1, 'lift':4.71},
    {'from':'MOMENTUM_SURGE',   'to':'EXHAUSTION_ONSET', 'lag':2, 'lift':2.96},
    {'from':'PANIC_ONSET',      'to':'VOL_EXPLOSION',    'lag':0, 'lift':3.47},
    {'from':'TREND_BREAK',      'to':'PANIC_ONSET',      'lag':1, 'lift':2.15},
    {'from':'EXHAUSTION_ONSET', 'to':'VOL_COMPRESSION',  'lag':2, 'lift':1.87},
    {'from':'VOL_EXPLOSION',    'to':'MOMENTUM_SURGE',   'lag':1, 'lift':1.62},
]

PHASE6_THRESHOLDS = {
    'EBP_HIGH_CONVICTION': 0.35,
    'EBP_CONDITIONAL':     0.20,
    'EBP_FRAGILE':         0.10,
    'UNCERT_MEDIUM':       0.45,
}

COMMANDS = {
    'meta_status', 'decay_scan', 'hypothesis_gen', 'arch_compete',
    'taxonomy_audit', 'regime_intelligence', 'evolution_memory',
    'meta_decision', 'self_rewrite', 'evolution_full'
}

# ── Core helpers ──────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def safe(v, default=0.0):
    return v if v is not None else default

def pearson(xs, ys):
    n = len(xs)
    if n < 5: return 0.0
    mx, my = sum(xs)/n, sum(ys)/n
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx  = math.sqrt(sum((x-mx)**2 for x in xs) + 1e-12)
    dy  = math.sqrt(sum((y-my)**2 for y in ys) + 1e-12)
    return num / (dx * dy)

def rsi_from_rets(rets, period=14):
    """Compute RSI from a list of returns."""
    if len(rets) < period + 1: return 50.0
    gains = [max(0, r) for r in rets[-period:]]
    losses= [max(0,-r) for r in rets[-period:]]
    ag = statistics.mean(gains)  if any(g > 0 for g in gains)  else 1e-9
    al = statistics.mean(losses) if any(l > 0 for l in losses) else 1e-9
    if al < 1e-12: return 100.0
    if ag < 1e-12: return 0.0
    return 100 - 100/(1 + ag/al)

def classify_event(rsi, vol_ratio, ret, volume=1):
    """Conservative thresholds — only extreme/distinctive bars get labeled.
    Requires non-zero volume to avoid phantom events on untreaded days."""
    if not volume or volume <= 0: return None  # skip zero-volume bars
    rsi       = safe(rsi, 50.0)
    vol_ratio = safe(vol_ratio, 1.0)
    ret       = safe(ret, 0.0)
    if rsi < 32  and ret < -0.020:                  return 'PANIC_ONSET'
    if rsi > 72  and vol_ratio > 2.0:               return 'MOMENTUM_SURGE'
    if vol_ratio < 0.30 and volume > 100:           return 'VOL_COMPRESSION'
    if vol_ratio > 3.0  and volume > 100:           return 'VOL_EXPLOSION'
    if rsi < 38  and ret < -0.030:                  return 'TREND_BREAK'
    if rsi > 70  and vol_ratio < 0.55:              return 'EXHAUSTION_ONSET'
    if rsi > 62  and ret > 0.030 and volume > 100:  return 'RECOVERY_ONSET'
    return None

def compute_regime(avg_rsi, avg_ret, avg_vol):
    if avg_ret > 0.004  and avg_rsi > 55: return 'BULL'
    if avg_ret < -0.004 and avg_rsi < 42: return 'CRISIS'
    if avg_vol > 1.6:                     return 'STRESS'
    if avg_vol < 0.65:                    return 'CALM'
    return 'NEUTRAL'

def load_data(db, days=200, max_per_sym=120):
    """Load OHLCV history + current indicators (separately), merged in Python."""
    cutoff = int(time.time()) - days * 86400

    # OHLCV history
    ohlcv_rows = db.execute("""
        SELECT h.symbol, h.bar_time, h.close, h.volume, u.sector
        FROM ohlcv_history_execution h
        JOIN stock_universe u ON h.symbol = u.symbol
        WHERE h.bar_time >= ? AND h.close IS NOT NULL AND h.close > 0
        ORDER BY h.symbol, h.bar_time
    """, [cutoff]).fetchall()

    # Current indicators (one per symbol)
    ind_rows = db.execute("""
        SELECT symbol, rsi14, macd_line, vol_ratio_20, bb_middle, momentum_5d
        FROM indicators_cache
    """).fetchall()
    cur_ind = {r['symbol']: dict(r) for r in ind_rows}

    by_sym = defaultdict(list)
    for r in ohlcv_rows:
        by_sym[r['symbol']].append({
            'symbol': r['symbol'], 'bar_time': r['bar_time'],
            'close': r['close'], 'volume': r['volume'] or 0,
            'sector': r['sector'],
        })

    for sym in list(by_sym):
        if len(by_sym[sym]) > max_per_sym:
            by_sym[sym] = by_sym[sym][-max_per_sym:]
        # Attach current indicators to the most recent bar
        if by_sym[sym] and sym in cur_ind:
            by_sym[sym][-1].update(cur_ind[sym])

    return by_sym

def enrich(data):
    """Compute returns, rolling vol_ratio proxy, RSI proxy, and event labels."""
    for sym, bars in data.items():
        n = len(bars)
        # Returns
        for i, b in enumerate(bars):
            if i == 0 or not bars[i-1]['close'] or bars[i-1]['close'] <= 0:
                b['ret'] = 0.0
            else:
                b['ret'] = (b['close'] - bars[i-1]['close']) / bars[i-1]['close']

        # Rolling vol_ratio proxy (volume / 20-bar avg)
        for i, b in enumerate(bars):
            start = max(0, i-20)
            vols  = [bars[j]['volume'] for j in range(start, i+1) if bars[j]['volume'] > 0]
            if len(vols) >= 3:
                avg = statistics.mean(vols[:-1]) if len(vols) > 1 else vols[-1]
                b['vol_ratio'] = b['volume'] / avg if avg > 0 else 1.0
            else:
                b['vol_ratio'] = 1.0

        # RSI proxy from returns (last 14 bars)
        rets_so_far = []
        for i, b in enumerate(bars):
            rets_so_far.append(b['ret'])
            b['rsi_proxy'] = rsi_from_rets(rets_so_far)

        # Prefer real RSI14 for most recent bar
        if n > 0 and bars[-1].get('rsi14'):
            bars[-1]['rsi_proxy'] = bars[-1]['rsi14']
        if n > 0 and bars[-1].get('vol_ratio_20'):
            bars[-1]['vol_ratio'] = bars[-1]['vol_ratio_20']

        # 5-day momentum proxy
        for i, b in enumerate(bars):
            if i >= 5 and bars[i-5]['close'] > 0:
                b['mom5'] = (b['close'] - bars[i-5]['close']) / bars[i-5]['close']
            else:
                b['mom5'] = 0.0

        # Event classification using rolling proxies (pass volume to filter zero-vol bars)
        for b in bars:
            b['event'] = classify_event(b['rsi_proxy'], b['vol_ratio'], b['ret'], b.get('volume',0))

    return data

def current_regime(data):
    all_rsi = [b['rsi_proxy'] for bars in data.values() for b in bars[-5:] if b.get('rsi_proxy')]
    all_ret = [b['ret']       for bars in data.values() for b in bars[-3:]]
    all_vol = [b['vol_ratio'] for bars in data.values() for b in bars[-5:] if b.get('vol_ratio')]
    return compute_regime(
        statistics.mean(all_rsi) if all_rsi else 50,
        statistics.mean(all_ret) if all_ret else 0,
        statistics.mean(all_vol) if all_vol else 1.0
    )

# ── 1. meta_status ────────────────────────────────────────────────────────────

def meta_status(db):
    t0 = time.time()
    data = load_data(db, days=180, max_per_sym=120)
    data = enrich(data)
    n_syms = len(data)

    # Phase 1 — Latent: RSI variance across symbols (well-spread = healthy dimensions)
    rsi_vals = [b['rsi_proxy'] for bars in data.values() for b in bars[-5:] if b.get('rsi_proxy')]
    rsi_var  = statistics.stdev(rsi_vals) if len(rsi_vals) > 2 else 0
    p1 = min(1.0, rsi_var / 15.0)

    # Phase 2 — Forces: vol_ratio predictive consistency
    # Healthy = vol_ratio occasionally spikes (signals present) but not constantly extreme
    vol_vals = [b['vol_ratio'] for bars in data.values() for b in bars[-30:]
                if b.get('vol_ratio') and b.get('volume',0) > 0]
    if len(vol_vals) > 10:
        med_vol = statistics.median(vol_vals)
        # Median vol_ratio near 1.0 = stable baseline; extreme spikes = signal presence
        stability = max(0, 1.0 - abs(med_vol - 1.0))
        p2 = min(1.0, stability * 0.6 + 0.4)  # floor at 0.4 to avoid CRITICAL for normal vol
    else:
        p2 = 0.5

    # Phase 3 — Propagation: cross-sector return divergence
    sec_rets = defaultdict(list)
    for sym, bars in data.items():
        sec = bars[-1].get('sector','?') if bars else '?'
        rets = [b['ret'] for b in bars[-20:] if b['ret'] != 0]
        if rets: sec_rets[sec].append(statistics.mean(rets))
    flat = [v for vl in sec_rets.values() for v in vl]
    sec_var = statistics.stdev(flat) if len(flat) > 2 else 0
    p3 = min(1.0, 0.3 + sec_var * 400)

    # Phase 4 — Energy: cross-symbol return dispersion (energy is flowing)
    rec_rets = [b['ret'] for bars in data.values() for b in bars[-3:]
                if 0 < abs(b['ret']) < 0.15]
    e_disp = statistics.stdev(rec_rets) if len(rec_rets) > 5 else 0
    p4 = min(1.0, 0.35 + e_disp * 60)

    # Phase 5 — Causal: event firing rate (EGX baseline ~15-25%; frontier market has many thin/volatile days)
    ev_count = sum(1 for bars in data.values() for b in bars[-40:] if b['event'])
    tot_bars = sum(min(40, len(bars)) for bars in data.values())
    ev_rate  = ev_count / max(tot_bars, 1)
    # EGX healthy range: 10-30%. Score peaks at 20%, degrades at extremes.
    p5 = 1.0 - min(1.0, abs(ev_rate - 0.20) / 0.20)

    # Phase 6 — Decision: RSI spread (diverse RSI → diverse decision states reachable)
    rsi_snap   = [b['rsi_proxy'] for bars in data.values() for b in bars[-3:] if b.get('rsi_proxy')]
    rsi_spread = (max(rsi_snap) - min(rsi_snap)) if len(rsi_snap) > 5 else 0
    p6 = min(1.0, rsi_spread / 50.0)

    def hlabel(s):
        if s >= 0.75: return 'HEALTHY'
        if s >= 0.55: return 'STABLE'
        if s >= 0.35: return 'DEGRADING'
        return 'CRITICAL'

    phases = {
        'phase1_latent':      {'score': round(p1,3), 'status': hlabel(p1), 'detail': f'RSI stdev={rsi_var:.1f}'},
        'phase2_forces':      {'score': round(p2,3), 'status': hlabel(p2), 'detail': f'vol_ratio median={statistics.median(vol_vals):.2f}' if vol_vals else 'no data'},
        'phase3_propagation': {'score': round(p3,3), 'status': hlabel(p3), 'detail': f'sector divergence={sec_var*400:.3f}'},
        'phase4_energy':      {'score': round(p4,3), 'status': hlabel(p4), 'detail': f'ret dispersion={e_disp*100:.3f}%'},
        'phase5_causal':      {'score': round(p5,3), 'status': hlabel(p5), 'detail': f'event_rate={ev_rate:.3f} (EGX target~0.20)'},
        'phase6_decision':    {'score': round(p6,3), 'status': hlabel(p6), 'detail': f'RSI spread={rsi_spread:.1f}pts'},
    }

    overall = statistics.mean(p['score'] for p in phases.values())

    def tlabel(s):
        if s >= 0.70: return 'TRUST'
        if s >= 0.55: return 'REDUCE_CONFIDENCE'
        if s >= 0.35: return 'REBUILD'
        return 'INVALIDATE'

    flags = [f"{k}: {v['status']}" for k, v in phases.items() if v['status'] in ('DEGRADING','CRITICAL')]

    return {
        'elapsed_sec':   round(time.time()-t0, 2),
        'market_regime': current_regime(data),
        'n_symbols':     n_syms,
        'overall_health': round(overall, 3),
        'phase_health':  phases,
        'trust_level':   tlabel(overall),
        'flags':         flags if flags else ['All phases within acceptable bounds'],
    }

# ── 2. decay_scan ─────────────────────────────────────────────────────────────

def decay_scan(db):
    t0 = time.time()
    data = load_data(db, days=200, max_per_sym=120)
    data = enrich(data)

    # Alpha decay: indicator proxy → 1-bar-ahead return correlation, recent vs historical
    alpha_decay = {}
    for ind in ['rsi_proxy', 'vol_ratio', 'mom5']:
        r_x, r_y, h_x, h_y = [], [], [], []
        for sym, bars in data.items():
            n = len(bars)
            if n < 10: continue
            split = n * 2 // 3
            for i in range(1, n-1):
                v = bars[i].get(ind)
                if v is None: continue
                fwd = bars[i+1]['ret']
                if i >= split: r_x.append(v); r_y.append(fwd)
                else:          h_x.append(v); h_y.append(fwd)
        rc = pearson(r_x, r_y)
        hc = pearson(h_x, h_y)
        drift = abs(rc - hc)
        status = 'STABLE' if drift < 0.05 else ('DEGRADING' if drift < 0.12 else 'DECAYED')
        alpha_decay[ind] = {
            'recent_corr': round(rc, 4), 'hist_corr': round(hc, 4),
            'drift': round(drift, 4), 'status': status,
        }

    # Invariant re-validation: recompute lift for Phase 5 causal laws
    inv_results = []
    for inv in PHASE5_INVARIANTS:
        ev_from, ev_to, lag = inv['from'], inv['to'], inv['lag']
        r_n = r_h = h_n = h_h = 0
        for sym, bars in data.items():
            n = len(bars)
            if n < lag + 3: continue
            split = n * 2 // 3
            for i in range(1, n - lag - 1):
                ev_cur = bars[i]['event']
                ev_fut = bars[i + lag]['event']
                if i >= split:
                    if ev_cur == ev_from: r_n += 1; r_h += (1 if ev_fut == ev_to else 0)
                else:
                    if ev_cur == ev_from: h_n += 1; h_h += (1 if ev_fut == ev_to else 0)

        base = 0.07  # approximate P(any specific event)
        p_r = r_h / max(r_n, 1)
        p_h = h_h / max(h_n, 1)
        lift_r = p_r / base
        lift_h = p_h / base
        decay_pct = (inv['lift'] - lift_r) / inv['lift'] * 100 if inv['lift'] > 0 else 0
        status = 'VALID' if lift_r > inv['lift']*0.4 else ('DEGRADED' if lift_r > inv['lift']*0.15 else 'INVALID')
        inv_results.append({
            'invariant':     f"{ev_from}→{ev_to}",
            'original_lift': inv['lift'],
            'recent_lift':   round(max(0, lift_r), 2),
            'hist_lift':     round(max(0, lift_h), 2),
            'decay_pct':     round(decay_pct, 1),
            'recent_n':      r_n,
            'status':        status,
        })

    # Regime stability: entropy drift
    def win_regime(bars, i):
        w = bars[max(0,i-5):i+5]
        rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
        rets = [b['ret'] for b in w]
        vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
        if not rsis: return None
        return compute_regime(statistics.mean(rsis), statistics.mean(rets),
                              statistics.mean(vols) if vols else 1.0)

    r_reg, h_reg = [], []
    for sym, bars in data.items():
        n = len(bars)
        split = n * 2 // 3
        for i in range(5, n-5, 5):
            r = win_regime(bars, i)
            if r:
                (r_reg if i >= split else h_reg).append(r)

    def entropy(regimes):
        from collections import Counter
        c = Counter(regimes); total = sum(c.values())
        return -sum((v/total)*math.log(v/total+1e-9) for v in c.values()) if total > 0 else 0

    h_r = entropy(r_reg); h_h = entropy(h_reg)
    reg_drift = abs(h_r - h_h)

    # Overall decay score
    avg_drift = statistics.mean(v['drift'] for v in alpha_decay.values())
    n_decayed = sum(1 for r in inv_results if r['status'] != 'VALID')
    decay_score = min(1.0, (avg_drift*3 + n_decayed/max(len(inv_results),1) + reg_drift*0.5) / 4.5)

    decayed = []
    for k, v in alpha_decay.items():
        if v['status'] != 'STABLE': decayed.append(f"{k}: {v['status'].lower()}")
    for r in inv_results:
        if r['status'] != 'VALID': decayed.append(f"invariant {r['invariant']}: {r['status'].lower()}")

    return {
        'elapsed_sec':         round(time.time()-t0, 2),
        'alpha_decay':         alpha_decay,
        'invariant_validation': inv_results,
        'regime_stability': {
            'recent_entropy': round(h_r, 3), 'hist_entropy': round(h_h, 3),
            'drift': round(reg_drift, 3),
            'status': 'STABLE' if reg_drift < 0.3 else 'DRIFTING',
        },
        'overall_decay_score': round(decay_score, 3),
        'decayed_components':  decayed if decayed else ['No significant decay detected'],
    }

# ── 3. hypothesis_gen ─────────────────────────────────────────────────────────

def hypothesis_gen(db):
    t0 = time.time()
    data = load_data(db, days=200, max_per_sym=100)
    data = enrich(data)

    hypotheses = []
    challenges = []
    h_id = 1

    # H1: Sector divergence — may need independent force dimension
    sec_means = {}
    for sym, bars in data.items():
        sec = bars[-1].get('sector','?') if bars else '?'
        ret = statistics.mean(b['ret'] for b in bars[-20:]) if len(bars) >= 5 else 0
        sec_means.setdefault(sec, []).append(ret)
    sec_avg = {s: statistics.mean(v) for s, v in sec_means.items() if len(v) >= 3}
    if len(sec_avg) >= 3:
        spread = max(sec_avg.values()) - min(sec_avg.values())
        if spread > 0.006:
            best  = max(sec_avg, key=sec_avg.get)
            worst = min(sec_avg, key=sec_avg.get)
            hypotheses.append({'id': f'H{h_id:03d}', 'type': 'SECTOR_DIVERGENCE_FORCE',
                'description': f'Sector divergence ({best} vs {worst}) exceeds model capacity',
                'evidence': f'Spread={spread:.5f} across {len(sec_avg)} sectors',
                'confidence': round(min(0.88, spread*100), 2),
                'recommendation': 'Add SECTOR_DIVERGENCE as 6th latent force dimension'})
            h_id += 1

    # H2: PANIC→RECOVERY lag drift (Phase 5 assumed lag=1)
    lag_hits = defaultdict(int)
    panic_n  = 0
    for sym, bars in data.items():
        for i in range(len(bars)-4):
            if bars[i]['event'] == 'PANIC_ONSET':
                panic_n += 1
                for lg in [1,2,3]:
                    if i+lg < len(bars) and bars[i+lg]['event'] == 'RECOVERY_ONSET':
                        lag_hits[lg] += 1; break
    if panic_n >= 5:
        best_lag = max(lag_hits, key=lag_hits.get) if lag_hits else 1
        hypotheses.append({'id': f'H{h_id:03d}', 'type': 'LAG_DRIFT',
            'description': f'PANIC→RECOVERY optimal lag may have shifted from 1→{best_lag}',
            'evidence': 'lag_hits: ' + ', '.join(f'lag{k}={v}' for k,v in sorted(lag_hits.items())),
            'confidence': round(0.65 if panic_n > 20 else 0.45, 2),
            'recommendation': f'Recalibrate causal lag for PANIC_ONSET→RECOVERY_ONSET to {best_lag}'})
        if best_lag != 1:
            challenges.append({'assumption': 'PANIC_ONSET→RECOVERY_ONSET has fixed lag=1',
                'challenge': f'Empirical best lag={best_lag} in current data (n={panic_n} panic events)',
                'evidence': str(dict(lag_hits)), 'severity': 'MODERATE' if best_lag == 2 else 'HIGH'})
        h_id += 1

    # H3: Volatility distribution skew → asymmetric thresholds needed
    vol_vals = [b.get('vol_ratio',1.0) for bars in data.values() for b in bars[-40:] if b.get('vol_ratio')]
    if len(vol_vals) > 50:
        mu  = statistics.mean(vol_vals)
        med = statistics.median(vol_vals)
        sig = statistics.stdev(vol_vals)
        skew = (mu - med) / (sig + 1e-9)
        if abs(skew) > 0.8:
            hypotheses.append({'id': f'H{h_id:03d}', 'type': 'DISTRIBUTION_ANOMALY',
                'description': f'Vol_ratio distribution skewed (skew={skew:.2f}) — symmetric thresholds invalid',
                'evidence': f'mean={mu:.3f} median={med:.3f} std={sig:.3f}',
                'confidence': round(min(0.85, abs(skew)/3), 2),
                'recommendation': 'Replace symmetric VOL_COMPRESSION/VOL_EXPLOSION thresholds with percentile-based'})
            h_id += 1

    # H4: BULL regime may split into BULL_VOLATILE / BULL_QUIET
    hvb, lvb = [], []
    for sym, bars in data.items():
        for i in range(5, len(bars)-1):
            w = bars[i-5:i]
            rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
            vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
            rets = [b['ret'] for b in w]
            if not rsis: continue
            if compute_regime(statistics.mean(rsis), statistics.mean(rets),
                              statistics.mean(vols) if vols else 1.0) == 'BULL':
                fwd = bars[i]['ret']
                (hvb if (statistics.mean(vols) if vols else 1.0) > 1.2 else lvb).append(fwd)
    if len(hvb) >= 10 and len(lvb) >= 10:
        diff = abs(statistics.mean(hvb) - statistics.mean(lvb))
        if diff > 0.001:
            hypotheses.append({'id': f'H{h_id:03d}', 'type': 'REGIME_SPLIT',
                'description': 'BULL regime has two sub-states with different return profiles',
                'evidence': f'BULL_VOLATILE avg_ret={statistics.mean(hvb):.5f}, BULL_QUIET avg_ret={statistics.mean(lvb):.5f} (Δ={diff:.5f})',
                'confidence': round(min(0.80, diff*400), 2),
                'recommendation': 'Split BULL → BULL_VOLATILE | BULL_QUIET for sharper EBP calibration'})
            h_id += 1

    # H5: RSI predictive edge declining?
    rp, hp = [], []
    for sym, bars in data.items():
        n = len(bars)
        if n < 10: continue
        split = n * 2 // 3
        for i in range(1, n-1):
            v = bars[i].get('rsi14')
            if v is None: continue
            fwd = bars[i+1]['ret']
            (rp if i >= split else hp).append((v, fwd))
    if len(rp) > 50 and len(hp) > 50:
        c_r = pearson([p[0] for p in rp], [p[1] for p in rp])
        c_h = pearson([p[0] for p in hp], [p[1] for p in hp])
        if abs(c_h) > 0.01 and abs(c_r) < abs(c_h) * 0.55:
            challenges.append({
                'assumption': 'RSI is a primary predictive force dimension',
                'challenge': f'RSI→return correlation dropped {abs(c_h):.3f}→{abs(c_r):.3f} (−{(1-abs(c_r)/abs(c_h))*100:.0f}%)',
                'evidence': f'recent_corr={c_r:.4f} vs hist_corr={c_h:.4f}',
                'severity': 'HIGH' if abs(c_r) < abs(c_h)*0.35 else 'MODERATE'})

    return {
        'elapsed_sec':          round(time.time()-t0, 2),
        'new_hypotheses':       hypotheses,
        'challenged_assumptions': challenges,
        'total_hypotheses':     len(hypotheses),
        'total_challenges':     len(challenges),
    }

# ── 4. arch_compete ───────────────────────────────────────────────────────────

def arch_compete(db):
    t0 = time.time()
    data = load_data(db, days=180, max_per_sym=100)
    data = enrich(data)

    windows = []
    for sym, bars in data.items():
        n = len(bars)
        for i in range(5, n-2, 3):
            w = bars[max(0,i-5):i]
            rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
            vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
            rets = [b['ret'] for b in w]
            if not rsis: continue
            windows.append({
                'avg_rsi': statistics.mean(rsis),
                'avg_vol': statistics.mean(vols) if vols else 1.0,
                'avg_ret': statistics.mean(rets),
                'std_ret': statistics.stdev(rets) if len(rets) > 1 else 0.01,
                'fwd_ret': bars[i]['ret'] if i < n else 0,
            })

    if not windows:
        return {'elapsed_sec': round(time.time()-t0,2), 'architectures':[], 'winner':'UNKNOWN', 'incumbent_rank':1}

    def classify_A(w): return compute_regime(w['avg_rsi'], w['avg_ret'], w['avg_vol'])
    def classify_B(w):
        if w['avg_ret'] > 0.004: return 'RISK_ON'
        if w['avg_ret'] < -0.004: return 'RISK_OFF'
        return 'NEUTRAL_3'
    def classify_C(w):
        if w['avg_vol'] > 1.4: return 'HIGH_VOL'
        if w['avg_vol'] < 0.7: return 'LOW_VOL'
        return 'MEDIUM_VOL'
    def classify_D(w):
        if w['avg_ret'] > 0.003 and w['std_ret'] < 0.012: return 'TRENDING_UP'
        if w['avg_ret'] < -0.003 and w['std_ret'] < 0.012: return 'TRENDING_DOWN'
        return 'RANGING'

    def score_arch(fn, name):
        groups = defaultdict(list)
        for w in windows:
            groups[fn(w)].append(w['fwd_ret'])
        n_states = len(groups)
        if n_states < 2:
            return {'name': name, 'n_states': n_states, 'overall_score': 0.2, 'rank': 99,
                    'state_distribution': {k: len(v) for k,v in groups.items()}}
        # Within-group std (lower = more homogeneous)
        w_stds = [statistics.stdev(v) for v in groups.values() if len(v) > 2]
        homog  = max(0, 1.0 - (statistics.mean(w_stds) if w_stds else 1.0) * 20)
        # Between-group separation (higher = more distinct states)
        gmeans = [statistics.mean(v) for v in groups.values() if len(v) > 1]
        sep    = min(1.0, statistics.stdev(gmeans) * 200) if len(gmeans) > 1 else 0
        # Balance (uniform distribution across states)
        total  = sum(len(v) for v in groups.values())
        fracs  = [len(v)/total for v in groups.values()]
        bal    = max(0, 1.0 - (statistics.stdev(fracs) if len(fracs) > 1 else 0))
        overall = homog*0.4 + sep*0.4 + bal*0.2
        return {'name': name, 'n_states': n_states,
                'homogeneity': round(homog,3), 'separation': round(sep,3), 'balance': round(bal,3),
                'overall_score': round(overall,3),
                'state_distribution': {k: len(v) for k,v in groups.items()}}

    archs = [
        score_arch(classify_A, 'CURRENT_5STATE'),
        score_arch(classify_B, 'RISK_ON_OFF_3'),
        score_arch(classify_C, 'VOLATILITY_REGIME'),
        score_arch(classify_D, 'MOMENTUM_REGIME'),
    ]
    archs.sort(key=lambda x: x['overall_score'], reverse=True)
    for i, a in enumerate(archs): a['rank'] = i+1

    winner = archs[0]['name']
    incumbent_rank = next(a['rank'] for a in archs if a['name'] == 'CURRENT_5STATE')

    return {
        'elapsed_sec':     round(time.time()-t0, 2),
        'architectures':   archs,
        'winner':          winner,
        'incumbent_rank':  incumbent_rank,
        'recommendation':  f"Keep CURRENT_5STATE" if winner == 'CURRENT_5STATE'
                           else f"Consider adopting {winner} (incumbent ranked #{incumbent_rank})",
    }

# ── 5. taxonomy_audit ─────────────────────────────────────────────────────────

def taxonomy_audit(db):
    t0 = time.time()
    data = load_data(db, days=180, max_per_sym=120)
    data = enrich(data)

    # Event firing rates
    ev_counts = defaultdict(int)
    tot = 0
    for bars in data.values():
        for b in bars:
            tot += 1
            if b['event']: ev_counts[b['event']] += 1

    ALL_EVENTS = ['PANIC_ONSET','MOMENTUM_SURGE','VOL_COMPRESSION','VOL_EXPLOSION',
                  'TREND_BREAK','EXHAUSTION_ONSET','RECOVERY_ONSET']
    event_audit = {}
    for ev in ALL_EVENTS:
        cnt  = ev_counts.get(ev, 0)
        rate = cnt / max(tot, 1)
        status = 'FREQUENT' if rate > 0.06 else ('ACTIVE' if rate > 0.01 else ('RARE' if rate > 0.002 else 'DEAD'))
        rec    = 'SPLIT' if rate > 0.12 else ('KEEP' if rate > 0.01 else ('REVIEW' if rate > 0.002 else 'REMOVE_CANDIDATE'))
        event_audit[ev] = {'count': cnt, 'fire_rate': round(rate,4), 'status': status, 'recommendation': rec}

    # Force correlation (proxied by indicator correlation)
    FORCES = {'MOMENTUM_FORCE':'mom5', 'PANIC_FORCE':'vol_ratio', 'TREND_FORCE':'rsi_proxy'}
    fvals  = defaultdict(list)
    for bars in data.values():
        for b in bars[-30:]:
            for fname, col in FORCES.items():
                v = b.get(col)
                if v is not None: fvals[fname].append(v)

    force_corrs = []
    fnames = list(fvals.keys())
    for i in range(len(fnames)):
        for j in range(i+1, len(fnames)):
            fa, fb = fnames[i], fnames[j]
            n = min(len(fvals[fa]), len(fvals[fb]))
            if n < 20: continue
            c = abs(pearson(fvals[fa][:n], fvals[fb][:n]))
            rec = 'CONSIDER_MERGE' if c > 0.70 else ('WATCH' if c > 0.50 else 'INDEPENDENT')
            force_corrs.append({'pair': f'{fa}↔{fb}', 'corr': round(c,3), 'recommendation': rec})

    # Decision state reachability (proxy via simplified EBP)
    state_counts = defaultdict(int)
    for bars in data.values():
        for b in bars[-5:]:
            rsi = safe(b.get('rsi_proxy'), 50)
            vol = safe(b.get('vol_ratio'), 1.0)
            p_s = max(0.3, min(0.9, (rsi-30)/70))
            stab = max(0.3, min(1.0, 1.5-vol))
            inst = max(0, vol-1.0)*0.3
            ebp  = p_s * stab * 0.5 - inst
            if   ebp > 0.35:  state_counts['HIGH_CONVICTION'] += 1
            elif ebp > 0.20:  state_counts['CONDITIONAL'] += 1
            elif ebp > 0.10:  state_counts['FRAGILE'] += 1
            elif ebp > 0.05:  state_counts['TRANSITIONAL'] += 1
            elif ebp > -0.05: state_counts['UNSTABLE'] += 1
            else:             state_counts['AVOID'] += 1

    state_tot = sum(state_counts.values()) or 1
    state_audit = {}
    for st in ['HIGH_CONVICTION','CONDITIONAL','FRAGILE','TRANSITIONAL','UNSTABLE','AVOID']:
        cnt  = state_counts.get(st, 0)
        rate = cnt / state_tot
        rec  = 'LOWER_THRESHOLD' if rate < 0.02 else ('SPLIT' if rate > 0.55 else 'KEEP')
        state_audit[st] = {'count': cnt, 'rate': round(rate,3), 'recommendation': rec}

    suggestions = []
    for ev, info in event_audit.items():
        if info['status'] == 'DEAD':
            suggestions.append(f"Remove {ev} — fires only {info['count']}× total")
    for fc in force_corrs:
        if fc['recommendation'] == 'CONSIDER_MERGE':
            suggestions.append(f"Merge {fc['pair']} — corr={fc['corr']:.2f} suggests redundancy")
    for st, info in state_audit.items():
        if info['recommendation'] == 'LOWER_THRESHOLD':
            suggestions.append(f"Lower EBP threshold for {st} — only {info['rate']:.1%} of bars reach it")

    return {
        'elapsed_sec':           round(time.time()-t0, 2),
        'total_bars_analyzed':   tot,
        'event_audit':           event_audit,
        'force_correlations':    force_corrs,
        'decision_state_audit':  state_audit,
        'redesign_suggestions':  suggestions if suggestions else ['Taxonomy healthy — no urgent redesign needed'],
    }

# ── 6. regime_intelligence ────────────────────────────────────────────────────

def regime_intelligence(db):
    t0 = time.time()
    data = load_data(db, days=200, max_per_sym=100)
    data = enrich(data)

    reg_windows = defaultdict(list)
    for sym, bars in data.items():
        n = len(bars)
        for i in range(10, n-2, 5):
            w = bars[max(0,i-10):i]
            rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
            vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
            rets = [b['ret'] for b in w]
            if not rsis: continue
            regime = compute_regime(statistics.mean(rsis), statistics.mean(rets),
                                    statistics.mean(vols) if vols else 1.0)
            rsi_sp = max(rsis) - min(rsis) if len(rsis) > 1 else 0
            vol_cv = (statistics.stdev(vols)/(statistics.mean(vols)+1e-9)) if len(vols) > 2 else 0.5
            ret_std = statistics.stdev(rets) if len(rets) > 2 else 0.01
            avg_ret = statistics.mean(rets)
            ev_rate = sum(1 for b in w if b['event']) / max(len(w), 1)

            reg_windows[regime].append({
                'p1': min(1.0, rsi_sp/20.0),
                'p2': max(0, 1.0 - vol_cv),
                'p3': max(0, 1.0 - ret_std*30),
                'p4': min(1.0, abs(avg_ret)*100 + 0.3),
                'p5': min(1.0, ev_rate/0.10),
                'p6': min(1.0, rsi_sp/20.0),
            })

    phase_labels = {1:'latent',2:'forces',3:'propagation',4:'energy',5:'causal',6:'decision'}
    reliability = {}
    coverage    = {}
    for regime, wins in reg_windows.items():
        n = len(wins)
        coverage[regime] = n
        if n < 3:
            reliability[regime] = {f'phase{i}_{phase_labels[i]}': None for i in range(1,7)}
        else:
            reliability[regime] = {
                f'phase{i}_{phase_labels[i]}': round(statistics.mean(w[f'p{i}'] for w in wins), 3)
                for i in range(1, 7)
            }

    cur = current_regime(data)
    warnings = [f"{r}: only {n} windows — results unreliable"
                for r, n in coverage.items() if n < 15]

    return {
        'elapsed_sec':                 round(time.time()-t0, 2),
        'current_regime':              cur,
        'phase_reliability_by_regime': reliability,
        'current_regime_reliability':  reliability.get(cur, {}),
        'regime_data_coverage':        coverage,
        'low_coverage_warnings':       warnings if warnings else ['All regimes have adequate coverage'],
    }

# ── 7. evolution_memory ───────────────────────────────────────────────────────

def evolution_memory_cmd(db):
    t0 = time.time()
    log_path = Path(EVO_LOG_PATH)
    if log_path.exists():
        try:
            with open(log_path) as f: log = json.load(f)
        except:
            log = {'entries': [], 'architecture_history': []}
    else:
        log = {'entries': [], 'architecture_history': []}

    # Auto-checkpoint today
    today = datetime.now().strftime('%Y-%m-%d')
    if not any(e.get('date') == today for e in log.get('entries', [])):
        cutoff = int(time.time()) - 30*86400
        rows = db.execute("""
            SELECT AVG(i.vol_ratio_20) AS avg_vol, AVG(i.rsi14) AS avg_rsi
            FROM indicators_cache i
            JOIN stock_universe u ON i.symbol=u.symbol
            WHERE i.bar_date >= date('now','-30 days')
        """).fetchone()
        avg_vol = round(rows['avg_vol'] or 1.0, 3)
        avg_rsi = round(rows['avg_rsi'] or 50.0, 1)
        # Compute trust_level so cognitive_orchestrator can read it
        _ms = {}
        try:
            _ms = meta_status(db)
        except Exception: pass
        _trust = _ms.get('trust_level', 'UNKNOWN')

        log.setdefault('entries', []).append({
            'date': today, 'action': 'AUTO_CHECKPOINT',
            'metric': 'avg_vol_ratio', 'metric_value': avg_vol,
            'avg_rsi': avg_rsi,
            'trust_level': _trust,          # ← FIX: write trust so orchestrator reads TRUST not UNKNOWN
            'note': f'Auto: vol_ratio={avg_vol}, rsi={avg_rsi}, trust={_trust}'
        })
        log['entries'] = log['entries'][-90:]
        try:
            with open(log_path, 'w') as f: json.dump(log, f, indent=2)
        except: pass

    entries     = log.get('entries', [])
    arch_hist   = log.get('architecture_history', [])
    metric_vals = [e.get('metric_value') for e in entries if e.get('metric_value') is not None]
    improvement = 0.0
    if len(metric_vals) >= 4:
        half = len(metric_vals)//2
        improvement = round(statistics.mean(metric_vals[:half]) - statistics.mean(metric_vals[half:]), 4)

    return {
        'elapsed_sec':          round(time.time()-t0, 2),
        'log_path':             str(log_path),
        'log_exists':           log_path.exists(),
        'total_entries':        len(entries),
        'architecture_history': arch_hist,
        'recent_entries':       entries[-6:],
        'net_improvement':      improvement,
        'note':                 'Positive = vol_ratio decreased (calmer market) over evolution history',
    }

# ── 8. meta_decision ──────────────────────────────────────────────────────────

def meta_decision(db):
    t0 = time.time()
    data = load_data(db, days=120, max_per_sym=80)
    data = enrich(data)

    # 1. Alpha drift (RSI→return)
    r_x, r_y, h_x, h_y = [], [], [], []
    for sym, bars in data.items():
        n = len(bars)
        if n < 8: continue
        split = n*2//3
        for i in range(1, n-1):
            v = bars[i].get('rsi_proxy')  # use proxy, not raw rsi14
            if v is None: continue
            fwd = bars[i+1]['ret']
            if i >= split: r_x.append(v); r_y.append(fwd)
            else:          h_x.append(v); h_y.append(fwd)
    alpha_drift = abs(pearson(r_x,r_y) - pearson(h_x,h_y)) if len(r_x) >= 10 else 0

    # 2. Event health (EGX baseline ~20%)
    ev_total  = sum(1 for bars in data.values() for b in bars[-30:] if b['event'])
    bar_total = sum(min(30, len(bars)) for bars in data.values())
    ev_rate   = ev_total / max(bar_total, 1)
    ev_health = max(0, 1.0 - abs(ev_rate - 0.20) / 0.20)  # EGX target ~20%

    # 3. Regime stability (check if regime is consistently classifying)
    regimes_r, regimes_h = [], []
    for sym, bars in data.items():
        n = len(bars)
        if n < 10: continue
        split = n*2//3
        for i in range(5, n-5, 5):
            w = bars[i-5:i]
            rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
            rets = [b['ret'] for b in w]
            vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
            if not rsis: continue
            r = compute_regime(statistics.mean(rsis), statistics.mean(rets),
                               statistics.mean(vols) if vols else 1.0)
            (regimes_r if i >= split else regimes_h).append(r)
    # Stability = consistency of most-common regime
    from collections import Counter
    if regimes_r:
        top_r = Counter(regimes_r).most_common(1)[0][1] / len(regimes_r)
        reg_stability = top_r  # e.g. 0.6 = 60% of windows same regime
    else:
        reg_stability = 0.5

    # 4. Invariant validity: use ANY causal pair (not just lag-1 PANIC→RECOVERY)
    # Check VOL_COMPRESSION→VOL_EXPLOSION (lag 1) as a simpler test
    vc_n = vc_h = 0
    for sym in list(data.keys())[:80]:
        bars = data[sym]
        for i in range(len(bars)-2):
            if bars[i]['event'] == 'VOL_COMPRESSION':
                vc_n += 1
                if bars[i+1]['event'] == 'VOL_EXPLOSION': vc_h += 1
    p_vx = vc_h / max(vc_n, 1)
    base_vx = 0.045  # rough base rate of VOL_EXPLOSION
    lift_vx  = p_vx / base_vx if base_vx > 0 else 1.0
    inv_validity = min(1.0, lift_vx / 4.71)  # Phase 5 claimed 4.71

    composite = ((1-alpha_drift)*0.25 + reg_stability*0.30 +
                 ev_health*0.20 + min(1.0, inv_validity)*0.15 + 0.70*0.10)

    decision = ('TRUST' if composite >= 0.70 else
                'REDUCE_CONFIDENCE' if composite >= 0.55 else
                'REBUILD' if composite >= 0.35 else 'INVALIDATE')

    rationale = []
    if alpha_drift > 0.08: rationale.append(f'Alpha drift elevated ({alpha_drift:.3f})')
    if reg_stability < 0.5: rationale.append(f'Regime unstable (top-regime frequency={reg_stability:.2f})')
    if inv_validity < 0.4:  rationale.append(f'Causal invariants degraded (VC→VX lift={lift_vx:.2f} vs expected 4.71)')
    if not rationale: rationale.append('All metrics within acceptable bounds')

    return {
        'elapsed_sec':     round(time.time()-t0, 2),
        'market_regime':   current_regime(data),
        'decision':        decision,
        'confidence':      round(composite, 3),
        'scores': {
            'alpha_drift':        round(alpha_drift, 3),
            'regime_stability':   round(reg_stability, 3),
            'event_health':       round(ev_health, 3),
            'invariant_validity': round(min(1.0, inv_validity), 3),
            'vc_vx_lift':         round(lift_vx, 2),
        },
        'rationale':       rationale,
        'next_review_bars': 30 if decision == 'TRUST' else (15 if decision == 'REDUCE_CONFIDENCE' else 5),
    }

# ── 9. self_rewrite ───────────────────────────────────────────────────────────

def self_rewrite(db):
    t0 = time.time()
    data = load_data(db, days=180, max_per_sym=80)
    data = enrich(data)

    proposals      = []
    merge_cands    = []
    split_cands    = []
    new_dims       = []

    # Proposal 1: Regime split (BULL_VOLATILE vs BULL_QUIET)
    hvb, lvb = [], []
    for sym, bars in data.items():
        for i in range(5, len(bars)-1):
            w = bars[i-5:i]
            rsis = [b.get('rsi_proxy',50) for b in w if b.get('rsi_proxy')]
            vols = [b.get('vol_ratio',1.0) for b in w if b.get('vol_ratio')]
            rets = [b['ret'] for b in w]
            if not rsis: continue
            avg_v = statistics.mean(vols) if vols else 1.0
            if compute_regime(statistics.mean(rsis), statistics.mean(rets), avg_v) == 'BULL':
                (hvb if avg_v > 1.2 else lvb).append(bars[i]['ret'])
    if len(hvb) >= 8 and len(lvb) >= 8:
        diff = abs(statistics.mean(hvb) - statistics.mean(lvb))
        if diff > 0.001:
            split_cands.append({'state':'BULL', 'proposed':['BULL_VOLATILE','BULL_QUIET'],
                'rationale': f'Forward return profiles differ by {diff:.5f}',
                'confidence': round(min(0.85, diff*500), 2)})

    # Proposal 2: Adaptive EBP threshold
    vol_rec = [b.get('vol_ratio',1.0) for bars in data.values() for b in bars[-20:] if b.get('vol_ratio')]
    avg_vol = statistics.mean(vol_rec) if vol_rec else 1.0
    cur_thr = PHASE6_THRESHOLDS['EBP_CONDITIONAL']
    rec_thr = round(cur_thr * (1 - max(0, avg_vol-1.0)*0.15), 3)
    if abs(rec_thr - cur_thr) > 0.015:
        proposals.append({'component':'PHASE6_EBP_THRESHOLD', 'action':'ADAPT',
            'current': cur_thr, 'proposed': rec_thr,
            'rationale': f'vol_ratio={avg_vol:.2f} — threshold should contract in high-vol environments',
            'confidence': 0.70})

    # Proposal 3: Causal lag recalibration
    lag_hits = defaultdict(int)
    for sym, bars in data.items():
        for i in range(len(bars)-4):
            if bars[i]['event'] == 'PANIC_ONSET':
                for lg in [1,2,3]:
                    if i+lg < len(bars) and bars[i+lg]['event'] == 'RECOVERY_ONSET':
                        lag_hits[lg] += 1; break
    best_lag = max(lag_hits, key=lag_hits.get) if lag_hits else 1
    if best_lag != 1 and sum(lag_hits.values()) >= 3:
        proposals.append({'component':'CAUSAL_LAG_PANIC_RECOVERY', 'action':'RECALIBRATE',
            'current': 1, 'proposed': best_lag,
            'rationale': f'Empirical best lag={best_lag} (counts: {dict(lag_hits)})',
            'confidence': 0.62 if sum(lag_hits.values()) > 8 else 0.45})

    # New dimension: sector momentum independence
    sec_rets = defaultdict(list)
    for sym, bars in data.items():
        sec = bars[-1].get('sector','?') if bars else '?'
        if len(bars) >= 5:
            sec_rets[sec].append(statistics.mean(b['ret'] for b in bars[-10:]))
    if sec_rets:
        mkt_ret  = statistics.mean(r for vl in sec_rets.values() for r in vl)
        max_div  = max(abs(statistics.mean(v)-mkt_ret) for v in sec_rets.values() if len(v) >= 3)
        if max_div > 0.004:
            new_dims.append({'dimension':'SECTOR_INDEPENDENCE_FORCE',
                'description':'Systematic sector deviation from market — requires independent force axis',
                'evidence': f'Max divergence={max_div:.5f}', 'confidence': round(min(0.75, max_div*200),2)})

    # Merge candidates: VOL_EXPLOSION + MOMENTUM_SURGE
    ev_counts = defaultdict(int)
    tot = 0
    cooccur = 0
    for bars in data.values():
        for i, b in enumerate(bars):
            tot += 1
            if b['event']: ev_counts[b['event']] += 1
            if i > 0 and bars[i-1]['event'] == 'VOL_EXPLOSION' and b['event'] == 'MOMENTUM_SURGE':
                cooccur += 1
    if (cooccur > 3 and
        ev_counts.get('VOL_EXPLOSION',0)/max(tot,1) < 0.015 and
        ev_counts.get('MOMENTUM_SURGE',0)/max(tot,1) < 0.015):
        merge_cands.append({'states':['VOL_EXPLOSION','MOMENTUM_SURGE'],
            'rationale': f'Both rare + co-occur {cooccur}× — may be same phenomenon',
            'confidence': 0.58})

    # Load evolutionary generation number
    gen = 1
    lp = Path(EVO_LOG_PATH)
    if lp.exists():
        try:
            with open(lp) as f: gen = len(json.load(f).get('architecture_history',[])) + 1
        except: pass

    priority = []
    if split_cands:  priority.append(f"SPLIT {split_cands[0]['state']} → {split_cands[0]['proposed']}")
    if proposals:    priority.append(f"ADAPT {proposals[0]['component']}")
    if new_dims:     priority.append(f"ADD {new_dims[0]['dimension']}")
    if merge_cands:  priority.append(f"REVIEW merge {merge_cands[0]['states']}")
    if not priority: priority.append('No urgent redesign — system structurally stable')

    return {
        'elapsed_sec':        round(time.time()-t0, 2),
        'current_generation': gen,
        'redesign_proposals': proposals,
        'merge_candidates':   merge_cands,
        'split_candidates':   split_cands,
        'new_dimensions':     new_dims,
        'priority_actions':   priority,
    }

# ── 10. evolution_full ────────────────────────────────────────────────────────

def evolution_full(db):
    t0 = time.time()
    steps = [
        ('meta_status',          meta_status),
        ('decay_scan',           decay_scan),
        ('hypothesis_gen',       hypothesis_gen),
        ('arch_compete',         arch_compete),
        ('taxonomy_audit',       taxonomy_audit),
        ('regime_intelligence',  regime_intelligence),
        ('evolution_memory',     evolution_memory_cmd),
        ('meta_decision',        meta_decision),
        ('self_rewrite',         self_rewrite),
    ]
    results = {}
    for key, fn in steps:
        try:    results[key] = fn(db)
        except Exception as e: results[key] = {'error': str(e)}

    ms = results.get('meta_status',   {})
    ds = results.get('decay_scan',    {})
    hg = results.get('hypothesis_gen',{})
    ac = results.get('arch_compete',  {})
    md = results.get('meta_decision', {})
    sr = results.get('self_rewrite',  {})

    return {
        'elapsed_sec': round(time.time()-t0, 2),
        'components':  results,
        'synthesis': {
            'overall_health':       ms.get('overall_health', 0),
            'trust_level':          md.get('decision', ms.get('trust_level','UNKNOWN')),
            'decay_score':          ds.get('overall_decay_score', 0),
            'n_hypotheses':         hg.get('total_hypotheses', 0),
            'n_challenges':         hg.get('total_challenges', 0),
            'winning_arch':         ac.get('winner','UNKNOWN'),
            'incumbent_rank':       ac.get('incumbent_rank', 1),
            'n_proposals':          len(sr.get('redesign_proposals',[])),
            'priority_actions':     sr.get('priority_actions', []),
            'market_regime':        ms.get('market_regime','UNKNOWN'),
            'total_elapsed_sec':    round(time.time()-t0, 2),
        }
    }

# ── Main dispatcher ───────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'meta_status'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'valid': sorted(COMMANDS)}))
        sys.exit(1)
    db = get_db()
    try:
        dispatch = {
            'meta_status':         meta_status,
            'decay_scan':          decay_scan,
            'hypothesis_gen':      hypothesis_gen,
            'arch_compete':        arch_compete,
            'taxonomy_audit':      taxonomy_audit,
            'regime_intelligence': regime_intelligence,
            'evolution_memory':    evolution_memory_cmd,
            'meta_decision':       meta_decision,
            'self_rewrite':        self_rewrite,
            'evolution_full':      evolution_full,
        }
        print(json.dumps(dispatch[cmd](db), default=str))
    finally:
        db.close()

if __name__ == '__main__':
    main()
