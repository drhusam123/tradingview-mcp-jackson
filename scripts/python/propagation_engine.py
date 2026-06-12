#!/usr/bin/env python3
"""
Market Force Propagation Engine — Phase 3
==========================================
Models how behavioral forces SPREAD through the EGX market over time.

Phase 1: Latent Market Behavior Engine  (latent_engine.py)
Phase 2: Force Field Engine             (force_field_engine.py)
Phase 3: THIS — Propagation, Contagion, Transmission Dynamics

Core Principle:
  Markets behave like interconnected adaptive systems.
  Fear spreads. Momentum spreads. Exhaustion spreads.
  This engine models WHERE forces originate, HOW they propagate,
  WHICH structures amplify them, and WHICH absorb them.

Commands (stdin JSON: {"command": "...", "params": {...}}):
  propagation_now      — current transmission snapshot (~2s)
  contagion_chains     — P(sector B follows A | lag) (~25s)
  sector_transmission  — sector leadership & lag matrix (~15s)
  instability_cascades — cascade events, triggers, breakers (~15s)
  role_classification  — stock roles: SOURCE/AMPLIFIER/ABSORBER/etc (~12s)
  diffusion_analysis   — force diffusion half-life & radius (~15s)
  regime_networks      — regime-conditioned propagation topology (~20s)
  propagation_full     — all combined (~90s)

Owner: Dr. Husam | Created: May 2026
"""

import sys, json, time, math, sqlite3
from pathlib import Path
from collections import defaultdict

# ─── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

STATES = [
    'PANIC', 'SHARP_DROP', 'CONTINUATION_DOWN', 'VELOCITY_EXHAUSTION',
    'EXHAUSTION', 'ACCELERATING_UP', 'TRENDING_UP', 'DISTRIBUTION',
    'POTENTIAL_BOUNCE', 'STABILIZATION', 'NEUTRAL',
]
STATE_IDX = {s: i for i, s in enumerate(STATES)}

# Force category groupings
STRESS_STATES    = {'PANIC', 'SHARP_DROP', 'CONTINUATION_DOWN', 'VELOCITY_EXHAUSTION'}
MOMENTUM_STATES  = {'ACCELERATING_UP', 'TRENDING_UP', 'DISTRIBUTION'}
REVERSAL_STATES  = {'POTENTIAL_BOUNCE', 'EXHAUSTION', 'STABILIZATION'}
CASCADE_STATES   = {'PANIC', 'SHARP_DROP', 'CONTINUATION_DOWN'}   # Severe stress only

# Transmission analysis
MAX_LAG = 5   # Trading days

# ─── State Classifier (identical to latent_engine.py) ────────────────────────

def _classify_bar(close, prev_close, rsi, atr, volume, avg_volume, momentum5):
    if prev_close <= 0 or close <= 0:
        return 'NEUTRAL'
    pct = (close - prev_close) / prev_close

    if pct <= -0.07 and rsi < 35:                          return 'PANIC'
    if pct <= -0.04 and rsi < 45:                          return 'SHARP_DROP'
    if pct <= -0.02 and momentum5 < -0.05:                 return 'CONTINUATION_DOWN'
    if rsi > 80 and momentum5 > 0.10 and pct < 0:          return 'VELOCITY_EXHAUSTION'
    if rsi > 75 and volume < avg_volume * 0.7:             return 'EXHAUSTION'
    if pct >= 0.03 and volume > avg_volume * 1.5:          return 'ACCELERATING_UP'
    if pct >= 0.015 and momentum5 > 0.03:                  return 'TRENDING_UP'
    if rsi > 65 and volume > avg_volume * 1.3:             return 'DISTRIBUTION'
    if pct <= -0.03 and rsi < 40:                          return 'POTENTIAL_BOUNCE'
    if abs(pct) < 0.01 and 45 < rsi < 55:                  return 'STABILIZATION'
    return 'NEUTRAL'

def _compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    rsi_vals = [50.0] * period
    gains = [max(prices[i]-prices[i-1], 0.0) for i in range(1, period+1)]
    losses= [max(prices[i-1]-prices[i], 0.0) for i in range(1, period+1)]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period

    def _r(ag, al):
        return 100.0 if al == 0 else 100 - 100/(1 + ag/al)

    rsi_vals.append(_r(avg_g, avg_l))
    for i in range(period+1, len(prices)):
        d = prices[i] - prices[i-1]
        avg_g = (avg_g*(period-1) + max(d,  0.0)) / period
        avg_l = (avg_l*(period-1) + max(-d, 0.0)) / period
        rsi_vals.append(_r(avg_g, avg_l))
    return rsi_vals

def _compute_atr(highs, lows, closes, period=14):
    if len(closes) < 2:
        return [0.0] * len(closes)
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i]-closes[i-1]))
        trs.append(tr)
    avg = sum(trs[:period]) / min(period, len(trs))
    atrs = list(trs[:period])
    for i in range(period, len(trs)):
        avg = (avg*(period-1) + trs[i]) / period
        atrs.append(avg)
    while len(atrs) < len(closes):
        atrs.append(atrs[-1] if atrs else 0.0)
    return atrs

def _compute_stock_states(bars):
    """Compute market state for each bar. Returns list of dicts."""
    if len(bars) < 16:
        return []
    closes  = [b['close']  for b in bars]
    highs   = [b['high']   for b in bars]
    lows    = [b['low']    for b in bars]
    volumes = [b['volume'] for b in bars]

    rsi_vals = _compute_rsi(closes, 14)
    atr_vals = _compute_atr(highs, lows, closes, 14)

    result = []
    for i in range(14, len(bars)):
        avg_vol = sum(volumes[max(0,i-20):i]) / min(20, i) or 1
        mom5    = (closes[i] - closes[max(0,i-5)]) / closes[max(0,i-5)] \
                   if closes[max(0,i-5)] else 0
        state   = _classify_bar(
            closes[i], closes[i-1],
            rsi_vals[i], atr_vals[i],
            volumes[i], avg_vol, mom5,
        )
        pct = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] else 0
        result.append({
            'time':       bars[i]['time'],
            'state':      state,
            'pct_change': pct,
            'rsi':        rsi_vals[i],
            'close':      closes[i],
            'volume':     volumes[i],
            'avg_volume': avg_vol,
        })
    return result

# ─── DB Loaders ───────────────────────────────────────────────────────────────

def _load_ohlcv_all(min_bars=60):
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT symbol,bar_time,open,high,low,close,volume "
            "FROM ohlcv_history_execution ORDER BY symbol,bar_time"
        ).fetchall()
    finally:
        con.close()
    data = defaultdict(list)
    for r in rows:
        data[r[0]].append({
            'time': r[1], 'open': r[2], 'high': r[3],
            'low':  r[4], 'close':r[5], 'volume':r[6],
        })
    return {s: b for s, b in data.items() if len(b) >= min_bars}

def _load_sector_map():
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT symbol, sector FROM stock_universe"
        ).fetchall()
        return {r[0]: (r[1] or 'Unknown') for r in rows}
    finally:
        con.close()

def _load_indicators_now():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT ic.symbol, ic.rsi14, ic.vol_ratio_20, ic.momentum_5d, "
            "ic.momentum_10d, ic.momentum_20d, ic.adx14, "
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

# ─── Core Analytics ───────────────────────────────────────────────────────────

def _build_state_history(ohlcv_data, sector_map):
    """
    Compute state for every stock × day.
    Returns:
        stock_states  — {symbol: {timestamp: state_str}}
        dates         — sorted list of unique timestamps
        sectors       — {sector_name: [symbols]}  (≥3 stocks)
    """
    stock_states = {}
    all_dates    = set()
    for symbol, bars in ohlcv_data.items():
        computed = _compute_stock_states(bars)
        if computed:
            stock_states[symbol] = {s['time']: s['state'] for s in computed}
            all_dates.update(s['time'] for s in computed)
    dates   = sorted(all_dates)
    sectors = defaultdict(list)
    for sym in stock_states:
        sectors[sector_map.get(sym, 'Unknown')].append(sym)
    sectors = {s: syms for s, syms in sectors.items() if len(syms) >= 3}
    return stock_states, dates, sectors

def _compute_sector_breadth(stock_states, sectors, target_states):
    """
    Returns {sector: {date: fraction_of_stocks_in_target_states}}.
    """
    result = {}
    for sector, syms in sectors.items():
        breadth   = {}
        all_dates = set()
        for sym in syms:
            all_dates.update(stock_states.get(sym, {}).keys())
        for date in sorted(all_dates):
            obs = hits = 0
            for sym in syms:
                st = stock_states.get(sym, {}).get(date)
                if st is not None:
                    obs  += 1
                    hits += (1 if st in target_states else 0)
            if obs >= 2:
                breadth[date] = hits / obs
        result[sector] = breadth
    return result

def _compute_market_breadth(stock_states, target_states):
    """Overall market breadth per date."""
    d_obs  = defaultdict(int)
    d_hits = defaultdict(int)
    for sym, st_map in stock_states.items():
        for date, st in st_map.items():
            d_obs[date]  += 1
            d_hits[date] += (1 if st in target_states else 0)
    return {d: d_hits[d]/d_obs[d] for d in d_obs if d_obs[d] >= 10}

def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x, y = x[:n], y[:n]
    mx, my = sum(x)/n, sum(y)/n
    num  = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    sdx  = math.sqrt(sum((v-mx)**2 for v in x))
    sdy  = math.sqrt(sum((v-my)**2 for v in y))
    return num/(sdx*sdy) if sdx*sdy > 0 else 0.0

def _transmission_pair(b1_dict, b2_dict, max_lag=MAX_LAG):
    """
    Lagged cross-correlation between two sector breadth series.
    Positive peak_lag → b1 leads b2 (b1 changes first).
    Negative peak_lag → b2 leads b1.
    """
    common = sorted(set(b1_dict) & set(b2_dict))
    if len(common) < 30:
        return {'peak_lag': None, 'peak_corr': 0.0, 'lags': {}}
    v1 = [b1_dict[d] for d in common]
    v2 = [b2_dict[d] for d in common]
    # First differences
    dv1 = [v1[i]-v1[i-1] for i in range(1, len(v1))]
    dv2 = [v2[i]-v2[i-1] for i in range(1, len(v2))]

    best_lag, best_corr, lags = 0, 0.0, {}
    for lag in range(-max_lag, max_lag+1):
        if lag >= 0:
            x = dv1[:len(dv1)-lag] if lag else dv1
            y = dv2[lag:] if lag else dv2
        else:
            al = -lag
            x = dv1[al:]
            y = dv2[:len(dv2)-al] if al else dv2
        c = _pearson(x, y)
        lags[lag] = round(c, 3)
        if abs(c) > abs(best_corr):
            best_corr, best_lag = c, lag
    return {'peak_lag': best_lag, 'peak_corr': round(best_corr, 3), 'lags': lags}

def _detect_cascades(stock_states, dates, sectors, market_breadth,
                     cascade_states=None, rise_threshold=0.08, window=3):
    """
    Detect market stress cascade events:
    breadth in cascade_states rises by > rise_threshold over `window` bars.
    """
    if cascade_states is None:
        cascade_states = CASCADE_STATES
    mb = [(d, market_breadth.get(d, 0)) for d in dates if d in market_breadth]
    if len(mb) < 15:
        return []

    cascades   = []
    in_cascade = False

    for i in range(window, len(mb)):
        date, brd   = mb[i]
        prev_brd    = mb[i-window][1]
        delta       = brd - prev_brd

        if not in_cascade and delta > rise_threshold and brd > 0.08:
            in_cascade = True
            cascades.append({
                'start_date':    mb[i-window][0],
                'peak_date':     date,
                'start_breadth': round(prev_brd, 3),
                'peak_breadth':  round(brd, 3),
                'amplitude':     round(delta, 3),
                'duration_bars': window,
            })
        elif in_cascade:
            last = cascades[-1]
            if brd > last['peak_breadth']:
                last['peak_breadth'] = round(brd, 3)
                last['peak_date']    = date
                last['duration_bars'] += 1
                last['amplitude']    = round(last['peak_breadth'] - last['start_breadth'], 3)
            elif brd < last['peak_breadth'] * 0.6:
                in_cascade = False

    # Annotate with trigger sector info
    sec_breadth = _compute_sector_breadth(stock_states, sectors, cascade_states)
    for c in cascades:
        sec_stress = {}
        for sec, sb in sec_breadth.items():
            sec_stress[sec] = sb.get(c['start_date'], 0)
        if sec_stress:
            trig = max(sec_stress, key=sec_stress.get)
            c['trigger_sector']  = trig
            c['trigger_breadth'] = round(sec_stress[trig], 3)
            c['sector_snapshot'] = {
                k: round(v, 3)
                for k, v in sorted(sec_stress.items(), key=lambda x:-x[1])[:6]
            }
        else:
            c['trigger_sector'] = None

    return [c for c in cascades if c['amplitude'] > rise_threshold * 0.8]

def _score_stock_roles(stock_states, sectors, stress_breadth, dates, min_obs=15):
    """
    Score and classify each stock into one of 6 propagation roles.

    Dimensions:
      lead_score    — enters stress BEFORE sector threshold crossing
      absorb_score  — stays non-stressed when sector IS stressed
      anchor_score  — neutral/stable when market-wide stress is high
      amplify_score — sector breadth grows FASTER after stock enters stress
    """
    date_to_idx = {d: i for i, d in enumerate(dates)}
    roles = {}

    for sector, syms in sectors.items():
        sb = stress_breadth.get(sector, {})
        sb_dates = sorted(sb)
        if len(sb_dates) < 20:
            continue

        # Find sector stress-onset events (breadth crosses 0.20 from below)
        onset_dates = []
        prev_above  = sb.get(sb_dates[0], 0) > 0.20
        for d in sb_dates[1:]:
            cur = sb.get(d, 0) > 0.20
            if cur and not prev_above:
                onset_dates.append(d)
            prev_above = cur

        if len(onset_dates) < 3:
            continue

        for sym in syms:
            sym_st = stock_states.get(sym, {})
            if len(sym_st) < min_obs:
                continue

            lead_h = lead_t = absorb_h = absorb_t = 0
            anchor_h = anchor_t = 0
            amp_sum  = amp_n = 0

            for ev in onset_dates:
                idx = date_to_idx.get(ev)
                if idx is None or idx < 2:
                    continue

                # LEAD: was stock stressed 1–2 bars before sector crossed threshold?
                prev1 = dates[idx-1] if idx >= 1 else None
                prev2 = dates[idx-2] if idx >= 2 else None
                early = any(
                    sym_st.get(d) in STRESS_STATES
                    for d in [prev1, prev2] if d is not None
                )
                lead_t += 1
                if early:
                    lead_h += 1

                # ABSORB: stock NOT stressed at event date
                absorb_t += 1
                if sym_st.get(ev) not in STRESS_STATES:
                    absorb_h += 1

                # AMPLIFY: if stock is stressed, does sector breadth grow next bar?
                if sym_st.get(ev) in STRESS_STATES and idx+1 < len(dates):
                    nxt = dates[idx+1]
                    amp_sum += sb.get(nxt, 0) - sb.get(ev, 0)
                    amp_n   += 1

            # ANCHOR: stock calm when market is broadly stressed
            high_stress_dates = [d for d, v in sb.items() if v > 0.40]
            for d in high_stress_dates[:40]:
                st = sym_st.get(d)
                if st is not None:
                    anchor_t += 1
                    if st in ('NEUTRAL', 'STABILIZATION', 'TRENDING_UP', 'ACCELERATING_UP'):
                        anchor_h += 1

            ls = lead_h    / lead_t    if lead_t    else 0
            ab = absorb_h  / absorb_t  if absorb_t  else 0
            an = anchor_h  / anchor_t  if anchor_t  else 0
            am = amp_sum   / amp_n     if amp_n     else 0

            # Role classification
            if   ls > 0.40 and ab < 0.50:       role = 'FORCE_SOURCE'
            elif am > 0.06 and ls > 0.20:        role = 'FORCE_AMPLIFIER'
            elif ab > 0.70:                       role = 'FORCE_ABSORBER'
            elif an > 0.65:                       role = 'STABILITY_ANCHOR'
            elif ls < 0.10 and ab < 0.40:        role = 'DELAYED_REACTOR'
            elif am < -0.04:                      role = 'INSTABILITY_GENERATOR'
            else:                                 role = 'NEUTRAL_PARTICIPANT'

            roles[sym] = {
                'role':          role,
                'sector':        sector,
                'lead_score':    round(ls, 3),
                'absorb_score':  round(ab, 3),
                'anchor_score':  round(an, 3),
                'amplify_score': round(am, 4),
            }
    return roles

def _diffusion_profile(sector_breadth, dates, max_track=10):
    """
    After each sector stress onset, track how breadth evolves for max_track bars.
    Returns: {sector: {half_life, peak_lag, diffusion_curve, propagation_type}}
    """
    result = {}
    for sector, sb in sector_breadth.items():
        timed = [(d, sb.get(d, 0)) for d in dates if d in sb]
        if len(timed) < 20:
            continue
        # Find onset events (breadth crosses 0.25)
        events = [
            i for i in range(1, len(timed)-max_track)
            if timed[i][1] > 0.25 and timed[i-1][1] <= 0.25
        ]
        if len(events) < 3:
            continue
        curves = []
        for ei in events:
            seed = timed[ei][1]
            if seed <= 0:
                continue
            curve = [timed[ei+lag][1]/seed for lag in range(min(max_track+1, len(timed)-ei))]
            if len(curve) >= 3:
                curves.append(curve)
        if not curves:
            continue
        max_len = min(max(len(c) for c in curves), max_track+1)
        avg_curve = []
        for lag in range(max_len):
            vs = [c[lag] for c in curves if lag < len(c)]
            avg_curve.append(round(sum(vs)/len(vs), 3) if vs else 0)

        peak   = max(avg_curve)
        pk_lag = avg_curve.index(peak)
        hl     = next((i for i, v in enumerate(avg_curve) if i > 0 and v < 0.5), None)
        result[sector] = {
            'n_events':        len(events),
            'half_life_bars':  hl,
            'peak_lag_bars':   pk_lag,
            'peak_relative':   round(peak, 3),
            'diffusion_curve': avg_curve[:8],
            'type': 'FAST'      if (hl or 6) <= 2
               else 'SUSTAINED' if (hl or 6) <= 4
               else 'PERSISTENT',
        }
    return result

# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_propagation_now(params):
    """
    Fast current-state propagation snapshot from indicators_cache.
    Identifies force sources, sector coordination, and live transmission alerts.
    """
    t0 = time.time()
    stocks = _load_indicators_now()
    if not stocks:
        return {'error': 'No indicator data'}

    # Group by sector
    sec_stocks = defaultdict(list)
    for s in stocks:
        sec_stocks[s.get('sector') or 'Unknown'].append(s)

    # ── Per-sector profiles ───────────────────────────────────────────────
    sector_profiles = {}
    for sector, ss in sec_stocks.items():
        if len(ss) < 3:
            continue
        rsps = [s['rsi14']        for s in ss if s.get('rsi14')        is not None]
        moms = [s['momentum_5d']  for s in ss if s.get('momentum_5d')  is not None]
        vols = [s['vol_ratio_20'] for s in ss if s.get('vol_ratio_20') is not None]
        adxs = [s['adx14']        for s in ss if s.get('adx14')        is not None]
        if not rsps:
            continue
        n = len(rsps)
        avg_rsi  = sum(rsps)/n
        avg_mom  = sum(moms)/len(moms) if moms else 0
        avg_vol  = sum(vols)/len(vols) if vols else 1
        avg_adx  = sum(adxs)/len(adxs) if adxs else 0
        rsi_std  = math.sqrt(sum((r-avg_rsi)**2 for r in rsps)/n) if n > 1 else 0

        stress_n      = sum(1 for s in ss if (s.get('rsi14') or 50) < 40
                                          or (s.get('momentum_5d') or 0) < -0.03)
        overbought_n  = sum(1 for s in ss if (s.get('rsi14') or 50) > 70)
        stress_frac   = stress_n / n
        ob_frac       = overbought_n / n

        # Coherence: synchronized sector = high contagion potential
        coherence = max(0.0, 1.0 - rsi_std / 25.0)

        if   stress_frac > 0.40:       state = 'STRESSED'
        elif ob_frac     > 0.40:       state = 'OVERBOUGHT'
        elif avg_adx     > 25 and avg_mom > 0.02: state = 'TRENDING'
        elif stress_frac < 0.10 and ob_frac < 0.15: state = 'NEUTRAL'
        else:                          state = 'TRANSITIONING'

        sector_profiles[sector] = {
            'n_stocks':        n,
            'avg_rsi':         round(avg_rsi,  1),
            'avg_momentum':    round(avg_mom,  3),
            'avg_vol_ratio':   round(avg_vol,  2),
            'stress_fraction': round(stress_frac, 3),
            'ob_fraction':     round(ob_frac,     3),
            'rsi_std':         round(rsi_std,  1),
            'coherence':       round(coherence, 3),
            'trend_strength':  round(avg_adx,  1),
            'state':           state,
        }

    # ── Force sources: stocks that are outliers vs their sector ──────────
    force_sources = []
    for s in stocks:
        sector = s.get('sector') or 'Unknown'
        prof   = sector_profiles.get(sector)
        if not prof or not s.get('rsi14'):
            continue
        rsi    = s['rsi14']
        mom    = s.get('momentum_5d') or 0
        vol    = s.get('vol_ratio_20') or 1
        rsi_dev = abs(rsi - prof['avg_rsi']) / max(prof['rsi_std'], 5)
        mom_dev = abs(mom - prof['avg_momentum']) / 0.05

        # Source score: deviation × sector coherence
        score = (rsi_dev * 0.45 + mom_dev * 0.35 + min(vol, 3) * 0.20) * prof['coherence']
        if score < 0.60:
            continue

        if   rsi < 35 or mom < -0.06:  role = 'STRESS_SOURCE'
        elif rsi > 75 or mom > 0.08:   role = 'MOMENTUM_SOURCE'
        elif abs(rsi - 50) < 8 and vol > 2: role = 'VOLUME_ANOMALY'
        else:                           role = 'INSTABILITY_SOURCE'

        force_sources.append({
            'symbol':       s['symbol'],
            'sector':       sector,
            'role':         role,
            'rsi':          round(rsi, 1),
            'momentum':     round(mom, 3),
            'vol_ratio':    round(vol, 2),
            'source_score': round(score, 3),
            'sector_avg_rsi': prof['avg_rsi'],
        })
    force_sources.sort(key=lambda x: -x['source_score'])

    # ── Transmission alerts: stressed × coherent-neutral = contagion risk ─
    stressed = [s for s, p in sector_profiles.items() if p['state'] == 'STRESSED']
    neutral  = [s for s, p in sector_profiles.items() if p['state'] in ('NEUTRAL','TRENDING')]
    alerts   = []
    for src in stressed:
        for tgt in neutral:
            risk = (sector_profiles[src]['stress_fraction']
                    * sector_profiles[tgt]['coherence']
                    * (1 + sector_profiles[src]['coherence']))
            if risk > 0.12:
                alerts.append({
                    'from':       src,
                    'to':         tgt,
                    'risk':       round(risk, 3),
                    'mechanism':  f'coherent stress ({sector_profiles[src]["stress_fraction"]:.0%}) → '
                                  f'synchronized neutral ({sector_profiles[tgt]["coherence"]:.2f} coherence)',
                })
    alerts.sort(key=lambda x: -x['risk'])

    # ── Market-wide summary ───────────────────────────────────────────────
    stresses = [p['stress_fraction'] for p in sector_profiles.values()]
    market_stress = sum(stresses)/len(stresses) if stresses else 0
    coherences    = [p['coherence']    for p in sector_profiles.values()]
    avg_coherence = sum(coherences)/len(coherences) if coherences else 0

    # Propagation readiness: high market coherence = one sector panic can spread
    prop_readiness = market_stress * avg_coherence
    if   prop_readiness > 0.25: prop_state = 'HIGH_CONTAGION_RISK'
    elif prop_readiness > 0.10: prop_state = 'MODERATE_RISK'
    else:                       prop_state = 'LOW_RISK'

    return {
        'elapsed_sec':          round(time.time()-t0, 2),
        'n_stocks':             len(stocks),
        'n_sectors':            len(sector_profiles),
        'market_stress':        round(market_stress,    3),
        'market_coherence':     round(avg_coherence,    3),
        'propagation_readiness':round(prop_readiness,   3),
        'propagation_state':    prop_state,
        'sector_profiles':      sector_profiles,
        'force_sources':        force_sources[:25],
        'transmission_alerts':  alerts[:8],
    }


def cmd_contagion_chains(params):
    """
    Historical contagion analysis: P(sector B stress increases | sector A had stress onset, lag L).
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    stress_brd  = _compute_sector_breadth(stock_states, sectors, STRESS_STATES)
    sec_names   = list(stress_brd.keys())

    ONSET_THR   = 0.22   # Source sector: >22% stressed = onset event

    # Baseline: probability of any sector stress increase in a random bar
    all_vals_flat = []
    for sb in stress_brd.values():
        svs = [sb[d] for d in sorted(sb)]
        all_vals_flat.extend([svs[i]-svs[i-1] for i in range(1, len(svs))])
    baseline_inc = sum(1 for v in all_vals_flat if v > 0.05) / max(len(all_vals_flat), 1)

    contagion_matrix = {}
    for src in sec_names:
        sb_src   = stress_brd[src]
        src_dts  = sorted(sb_src)
        src_vals = [sb_src[d] for d in src_dts]
        # Find onset events
        onset_idxs = [
            i for i in range(1, len(src_vals)-MAX_LAG)
            if src_vals[i] > ONSET_THR and src_vals[i-1] <= ONSET_THR
        ]
        if len(onset_idxs) < 4:
            continue

        for tgt in sec_names:
            if tgt == src:
                continue
            sb_tgt  = stress_brd.get(tgt, {})
            # Build aligned target values at source event dates
            lag_results = {}
            for lag in [1, 2, 3]:
                hits = total = 0
                for ei in onset_idxs:
                    src_date = src_dts[ei]
                    lag_idx  = ei + lag
                    if lag_idx >= len(src_dts):
                        continue
                    lag_date  = src_dts[lag_idx]
                    prev_date = src_dts[ei]
                    v_now  = sb_tgt.get(lag_date,  0)
                    v_prev = sb_tgt.get(prev_date, 0)
                    total += 1
                    if v_now > v_prev + 0.05:
                        hits += 1
                if total >= 4:
                    p_c  = hits / total
                    lift = p_c / baseline_inc if baseline_inc > 0 else None
                    lag_results[str(lag)] = {
                        'p_contagion': round(p_c,  3),
                        'baseline':    round(baseline_inc, 3),
                        'lift':        round(lift, 2) if lift else None,
                        'n_events':    total,
                    }
            if lag_results:
                peak_p   = max(v['p_contagion'] for v in lag_results.values())
                peak_lag = min(lag_results, key=lambda l: -lag_results[l]['p_contagion'])
                strength = ('STRONG'   if peak_p > 0.45
                       else 'MODERATE' if peak_p > 0.25
                       else 'WEAK')
                contagion_matrix[f'{src}→{tgt}'] = {
                    'lags':      lag_results,
                    'peak_p':    round(peak_p, 3),
                    'peak_lag':  int(peak_lag),
                    'strength':  strength,
                }

    # Top contagion chains
    top = sorted(contagion_matrix.items(), key=lambda x: -x[1]['peak_p'])[:20]

    # Sector contagion power (how much each spreads vs receives)
    spread  = defaultdict(list)
    receive = defaultdict(list)
    for key, d in contagion_matrix.items():
        s, t = key.split('→')
        spread[s].append(d['peak_p'])
        receive[t].append(d['peak_p'])

    sector_power = {}
    for sec in sec_names:
        sp = sum(spread[sec])  / max(len(spread[sec]),  1) if spread[sec]  else 0
        rp = sum(receive[sec]) / max(len(receive[sec]), 1) if receive[sec] else 0
        sector_power[sec] = {
            'spread_power':  round(sp, 3),
            'receive_power': round(rp, 3),
            'net_role':      'SPREADER'  if sp > rp + 0.05
                        else 'RECEIVER' if rp > sp + 0.05
                        else 'BALANCED',
        }

    # Contagion chains: sequences of sectors
    chains = []
    for src in sec_names:
        chain = [src]
        current = src
        for _ in range(3):
            # Next hop: highest contagion from current
            next_hops = [
                (key.split('→')[1], d['peak_p'])
                for key, d in contagion_matrix.items()
                if key.startswith(f'{current}→')
                   and key.split('→')[1] not in chain
                   and d['strength'] in ('STRONG', 'MODERATE')
            ]
            if not next_hops:
                break
            nxt = max(next_hops, key=lambda x: x[1])
            chain.append(nxt[0])
            current = nxt[0]
        if len(chain) >= 3:
            chains.append({
                'chain':       ' → '.join(chain),
                'length':      len(chain),
                'start':       chain[0],
                'end':         chain[-1],
            })

    chains = sorted(chains, key=lambda x: -x['length'])[:8]

    return {
        'elapsed_sec':       round(time.time()-t0, 2),
        'n_stocks':          len(stock_states),
        'n_sectors':         len(sec_names),
        'n_dates':           len(dates),
        'onset_threshold':   ONSET_THR,
        'baseline_increase': round(baseline_inc, 3),
        'top_contagion':     [{'pair': k, **v} for k, v in top],
        'sector_power':      sector_power,
        'contagion_chains':  chains,
    }


def cmd_sector_transmission(params):
    """
    Sector leadership: which sectors LEAD market transitions, FOLLOW, or ABSORB.
    Builds full cross-sector transmission lag matrix.
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    stress_brd    = _compute_sector_breadth(stock_states, sectors, STRESS_STATES)
    market_brd    = _compute_market_breadth(stock_states, STRESS_STATES)
    sec_names     = list(stress_brd.keys())

    # ── Each sector vs market ─────────────────────────────────────────────
    sector_vs_market = {}
    for sec in sec_names:
        res = _transmission_pair(stress_brd[sec], market_brd, MAX_LAG)
        sector_vs_market[sec] = {
            'lag_vs_market':  res['peak_lag'],   # negative = sector LEADS market
            'corr_vs_market': res['peak_corr'],
        }

    # ── Cross-sector transmission matrix (compact: top pairs only) ────────
    trans_pairs = []
    for i, s1 in enumerate(sec_names):
        for j, s2 in enumerate(sec_names):
            if i >= j:
                continue
            res = _transmission_pair(stress_brd[s1], stress_brd[s2], MAX_LAG)
            if res['peak_lag'] is None or abs(res['peak_corr']) < 0.15:
                continue
            lag  = res['peak_lag']
            corr = res['peak_corr']
            trans_pairs.append({
                's1':        s1,
                's2':        s2,
                'peak_lag':  lag,
                'peak_corr': corr,
                'direction': (f'{s1} LEADS {s2}' if lag > 0
                         else f'{s2} LEADS {s1}' if lag < 0
                         else 'SIMULTANEOUS'),
                'strength': 'STRONG' if abs(corr) > 0.40 else 'MODERATE',
            })
    trans_pairs.sort(key=lambda x: -abs(x['peak_corr']))

    # ── Classify sector roles ─────────────────────────────────────────────
    sector_roles = {}
    for sec in sec_names:
        vm  = sector_vs_market.get(sec, {})
        lag = vm.get('lag_vs_market')
        cor = vm.get('corr_vs_market', 0)
        sb  = stress_brd.get(sec, {})

        avg_stress = sum(sb.values()) / len(sb) if sb else 0
        max_stress = max(sb.values()) if sb else 0

        # Amplification: sector stress vs market stress
        common  = [d for d in sb if d in market_brd]
        amp_ratio = (sum(sb[d]/max(market_brd[d], 0.01) for d in common) / len(common)
                     if common else 1.0)

        if   lag is not None and lag < -1 and cor > 0.20:  role = 'LEAD_SECTOR'
        elif lag is not None and lag > +1 and cor > 0.20:  role = 'FOLLOW_SECTOR'
        elif avg_stress < 0.04 and max_stress < 0.15:      role = 'ABSORBER'
        elif amp_ratio > 1.5:                               role = 'AMPLIFIER'
        else:                                               role = 'NEUTRAL_TRANSMITTER'

        sector_roles[sec] = {
            'role':           role,
            'lag_vs_market':  lag,
            'corr_vs_market': round(cor, 3),
            'avg_stress':     round(avg_stress, 3),
            'max_stress':     round(max_stress, 3),
            'amplification':  round(amp_ratio,  2),
        }

    # Sector lead ranking (most negative lag = most leading)
    lead_ranking = sorted(
        [{'sector': s, **sector_roles[s]}
         for s in sec_names
         if sector_roles[s]['lag_vs_market'] is not None],
        key=lambda x: x['lag_vs_market']
    )

    return {
        'elapsed_sec':         round(time.time()-t0, 2),
        'n_sectors':           len(sec_names),
        'n_dates':             len(dates),
        'sector_roles':        sector_roles,
        'sector_lead_ranking': lead_ranking,
        'transmission_pairs':  trans_pairs[:20],
        'insight': (
            f"قطاع LEAD: "
            f"{lead_ranking[0]['sector'] if lead_ranking else '—'} "
            f"(يقود السوق بـ {abs(lead_ranking[0]['lag_vs_market']) if lead_ranking else 0} يوم)"
        ),
    }


def cmd_instability_cascades(params):
    """
    Detect and analyze historical instability cascade events.
    Identifies triggers, propagation speed, and structural breakers.
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    market_brd = _compute_market_breadth(stock_states, CASCADE_STATES)

    cascades = _detect_cascades(
        stock_states, dates, sectors, market_brd,
        cascade_states=CASCADE_STATES, rise_threshold=0.07, window=3,
    )

    if not cascades:
        return {
            'elapsed_sec': round(time.time()-t0, 2),
            'n_cascades': 0,
            'insight': 'No significant cascades detected',
        }

    # ── Aggregate statistics ──────────────────────────────────────────────
    amps  = [c['amplitude']    for c in cascades]
    durs  = [c['duration_bars']for c in cascades]
    triggers = defaultdict(int)
    for c in cascades:
        if c.get('trigger_sector'):
            triggers[c['trigger_sector']] += 1

    # Speed: fraction of cascades that hit >25% breadth within 2 bars
    fast  = sum(1 for c in cascades if c['amplitude'] > 0.20 and c['duration_bars'] <= 3)
    slow  = len(cascades) - fast

    # Cascade breakers: sectors that stay calm during cascades
    sec_brd = _compute_sector_breadth(stock_states, sectors, CASCADE_STATES)
    breakers = []
    for sec, sb in sec_brd.items():
        c_stresses = [sb.get(c['start_date'], 0) for c in cascades]
        if c_stresses:
            avg_cs = sum(c_stresses) / len(c_stresses)
            if avg_cs < 0.08:
                breakers.append({'sector': sec, 'avg_cascade_stress': round(avg_cs, 3)})

    # P(cascade reaches SEVERE | trigger sector X)
    trigger_analysis = []
    for sec, cnt in sorted(triggers.items(), key=lambda x: -x[1]):
        sec_cascades = [c for c in cascades if c.get('trigger_sector') == sec]
        severe = sum(1 for c in sec_cascades if c['amplitude'] > 0.25)
        trigger_analysis.append({
            'sector':        sec,
            'n_triggers':    cnt,
            'p_severe':      round(severe/cnt, 2) if cnt else 0,
            'avg_amplitude': round(sum(c['amplitude'] for c in sec_cascades)/len(sec_cascades), 3),
        })

    # Cascade taxonomy
    for c in cascades:
        if c['amplitude'] > 0.30:  c['category'] = 'SYSTEMIC'
        elif c['amplitude'] > 0.15: c['category'] = 'SECTOR_WIDE'
        else:                        c['category'] = 'LOCALIZED'

    return {
        'elapsed_sec':          round(time.time()-t0, 2),
        'n_cascades':           len(cascades),
        'n_systemic':           sum(1 for c in cascades if c['amplitude'] > 0.30),
        'n_sector_wide':        sum(1 for c in cascades if 0.15 < c['amplitude'] <= 0.30),
        'n_localized':          sum(1 for c in cascades if c['amplitude'] <= 0.15),
        'avg_amplitude':        round(sum(amps)/len(amps), 3),
        'avg_duration_bars':    round(sum(durs)/len(durs), 1),
        'fast_cascades_pct':    round(fast/len(cascades), 2),
        'trigger_analysis':     trigger_analysis[:8],
        'cascade_breakers':     sorted(breakers, key=lambda x: x['avg_cascade_stress'])[:5],
        'cascades':             sorted(cascades, key=lambda x: -x['amplitude'])[:20],
        'insight': (
            f"{len(cascades)} cascades | "
            f"{sum(1 for c in cascades if c['amplitude']>0.30)} systemic | "
            f"أبرز محرك: {trigger_analysis[0]['sector'] if trigger_analysis else '—'}"
        ),
    }


def cmd_role_classification(params):
    """
    Classify every stock as: FORCE_SOURCE / FORCE_AMPLIFIER / FORCE_ABSORBER /
    DELAYED_REACTOR / STABILITY_ANCHOR / INSTABILITY_GENERATOR.
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    stress_brd  = _compute_sector_breadth(stock_states, sectors, STRESS_STATES)
    roles       = _score_stock_roles(stock_states, sectors, stress_brd, dates)

    # Aggregate by role
    role_counts  = defaultdict(int)
    role_by_sec  = defaultdict(lambda: defaultdict(int))
    by_type      = defaultdict(list)

    for sym, data in roles.items():
        r, s = data['role'], data['sector']
        role_counts[r]    += 1
        role_by_sec[s][r] += 1
        by_type[r].append({'symbol': sym, **data})

    # Top stocks per role (sorted by primary score)
    sort_key = {
        'FORCE_SOURCE':          lambda x: -x['lead_score'],
        'FORCE_AMPLIFIER':       lambda x: -x['amplify_score'],
        'FORCE_ABSORBER':        lambda x: -x['absorb_score'],
        'STABILITY_ANCHOR':      lambda x: -x['anchor_score'],
        'DELAYED_REACTOR':       lambda x:  x['lead_score'],
        'INSTABILITY_GENERATOR': lambda x:  x['amplify_score'],
        'NEUTRAL_PARTICIPANT':   lambda x: -x['lead_score'],
    }
    top_per_role = {
        r: sorted(items, key=sort_key.get(r, lambda x: 0))[:12]
        for r, items in by_type.items()
    }

    # Sector role composition
    sec_composition = {}
    for sec, rc in role_by_sec.items():
        total = sum(rc.values())
        sec_composition[sec] = {
            r: round(cnt/total, 2)
            for r, cnt in sorted(rc.items(), key=lambda x:-x[1])
        }

    # Insight: most dangerous sector (highest SOURCE + AMPLIFIER fraction)
    danger_scores = {}
    for sec, comp in sec_composition.items():
        danger_scores[sec] = (comp.get('FORCE_SOURCE', 0)
                              + comp.get('FORCE_AMPLIFIER', 0)
                              + comp.get('INSTABILITY_GENERATOR', 0))
    safest = min(danger_scores, key=danger_scores.get) if danger_scores else '—'
    riskiest = max(danger_scores, key=danger_scores.get) if danger_scores else '—'

    return {
        'elapsed_sec':           round(time.time()-t0, 2),
        'n_classified':          len(roles),
        'role_distribution':     dict(sorted(role_counts.items(), key=lambda x:-x[1])),
        'top_per_role':          top_per_role,
        'sector_composition':    sec_composition,
        'riskiest_sector':       riskiest,
        'safest_sector':         safest,
        'insight': (
            f"أكثر سهم كـ SOURCE: "
            f"{top_per_role.get('FORCE_SOURCE',[{}])[0].get('symbol','—')} | "
            f"أكثر سهم كـ ANCHOR: "
            f"{top_per_role.get('STABILITY_ANCHOR',[{}])[0].get('symbol','—')}"
        ),
    }


def cmd_diffusion_analysis(params):
    """
    Force diffusion mechanics: half-life, propagation radius, amplification coefficients.
    Tracks how stress spreads across sectors after initial onset.
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    stress_brd = _compute_sector_breadth(stock_states, sectors, STRESS_STATES)
    market_brd = _compute_market_breadth(stock_states, STRESS_STATES)
    sec_names  = list(stress_brd.keys())

    # ── Market-level diffusion curve ──────────────────────────────────────
    mb_series   = [(d, market_brd[d]) for d in sorted(market_brd)]
    mkt_events  = [i for i in range(1, len(mb_series)-10)
                   if mb_series[i][1] > 0.18 and mb_series[i-1][1] <= 0.18]

    mkt_curves = []
    for ei in mkt_events:
        seed = mb_series[ei][1]
        if seed <= 0: continue
        curve = [mb_series[ei+lag][1]/seed for lag in range(min(12, len(mb_series)-ei))]
        if len(curve) >= 3:
            mkt_curves.append(curve)

    avg_mkt_curve = []
    if mkt_curves:
        ml = min(max(len(c) for c in mkt_curves), 10)
        for lag in range(ml):
            vs = [c[lag] for c in mkt_curves if lag < len(c)]
            avg_mkt_curve.append(round(sum(vs)/len(vs), 3) if vs else 0)

    mkt_hl = next((i for i, v in enumerate(avg_mkt_curve) if i > 0 and v < 0.50), None)

    # ── Per-sector diffusion profiles ─────────────────────────────────────
    sec_diffusion = _diffusion_profile(stress_brd, dates)

    # ── Propagation radius ────────────────────────────────────────────────
    # After stress onset in sector S, how many other sectors show elevated stress?
    prop_radius = {}
    for src in sec_names:
        sb_src  = stress_brd[src]
        src_dts = sorted(sb_src)
        src_v   = [sb_src[d] for d in src_dts]
        onsets  = [i for i in range(1, len(src_v)-4)
                   if src_v[i] > 0.25 and src_v[i-1] <= 0.25]
        if len(onsets) < 3:
            continue
        radii = []
        for ei in onsets:
            count = 0
            for tgt in sec_names:
                if tgt == src: continue
                sb_tgt = stress_brd[tgt]
                for lag in [1, 2, 3]:
                    lag_idx = ei + lag
                    if lag_idx < len(src_dts):
                        lag_d = src_dts[lag_idx]
                        if sb_tgt.get(lag_d, 0) > 0.20:
                            count += 1
                            break
            radii.append(count)
        prop_radius[src] = {
            'avg_radius': round(sum(radii)/len(radii), 1),
            'max_radius': max(radii) if radii else 0,
            'n_sectors':  len(sec_names)-1,
            'coverage':   round(sum(radii)/(len(radii)*max(len(sec_names)-1,1)), 2),
            'n_events':   len(onsets),
        }

    # ── Amplification coefficients ────────────────────────────────────────
    amplification = {}
    for sec in sec_names:
        sb     = stress_brd[sec]
        common = [d for d in sb if d in market_brd]
        if len(common) < 15:
            continue
        ratios = [sb[d] / max(market_brd[d], 0.01) for d in common]
        m = sum(ratios)/len(ratios)
        amplification[sec] = {
            'coefficient': round(m, 2),
            'max_ratio':   round(max(ratios), 2),
            'type':  ('AMPLIFIER' if m > 1.25
                 else 'ABSORBER'  if m < 0.75
                 else 'NEUTRAL'),
        }

    # ── Cross-sector diffusion speed ranking ─────────────────────────────
    speed_rank = sorted(
        [{'sector': s, **sec_diffusion[s]} for s in sec_diffusion],
        key=lambda x: (x.get('half_life_bars') or 99)
    )

    return {
        'elapsed_sec':              round(time.time()-t0, 2),
        'n_dates':                  len(dates),
        'market_stress_events':     len(mkt_events),
        'market_half_life_bars':    mkt_hl,
        'market_diffusion_curve':   avg_mkt_curve[:10],
        'sector_diffusion':         sec_diffusion,
        'propagation_radius':       prop_radius,
        'amplification':            amplification,
        'speed_ranking':            speed_rank[:8],
        'top_amplifiers': sorted(
            [{'sector': s, **amplification[s]}
             for s in amplification if amplification[s]['type'] == 'AMPLIFIER'],
            key=lambda x: -x['coefficient']
        )[:5],
        'top_absorbers': sorted(
            [{'sector': s, **amplification[s]}
             for s in amplification if amplification[s]['type'] == 'ABSORBER'],
            key=lambda x: x['coefficient']
        )[:5],
    }


def cmd_regime_networks(params):
    """
    Regime-conditioned propagation topology.
    How does the transmission network change across CRISIS / STRESS / CALM regimes?
    """
    t0 = time.time()
    ohlcv      = _load_ohlcv_all(60)
    sector_map = _load_sector_map()
    if not ohlcv:
        return {'error': 'No OHLCV data'}

    stock_states, dates, sectors = _build_state_history(ohlcv, sector_map)
    market_brd  = _compute_market_breadth(stock_states, STRESS_STATES)
    stress_brd  = _compute_sector_breadth(stock_states, sectors, STRESS_STATES)
    sec_names   = list(stress_brd.keys())

    # ── Classify each date into a regime ─────────────────────────────────
    mb_ts   = [(d, market_brd[d]) for d in sorted(market_brd)]
    WIN     = 10   # Rolling window for regime smoothing
    date_regime = {}

    for i, (date, brd) in enumerate(mb_ts):
        recent = [mb_ts[j][1] for j in range(max(0,i-WIN), i+1)]
        avg_r  = sum(recent) / len(recent)
        prev10 = [mb_ts[j][1] for j in range(max(0,i-WIN*2), max(0,i-WIN)+1)]
        avg_p  = sum(prev10) / len(prev10) if prev10 else avg_r

        if   avg_r > 0.30:                       regime = 'CRISIS'
        elif avg_r > 0.15:                       regime = 'STRESS'
        elif avg_r < 0.05 and avg_p > 0.15:     regime = 'RECOVERY'
        elif avg_r < 0.07:                       regime = 'CALM'
        else:                                    regime = 'MODERATE'
        date_regime[date] = regime

    regime_counts = defaultdict(int)
    for r in date_regime.values():
        regime_counts[r] += 1

    # ── Per-regime sector analysis ────────────────────────────────────────
    regime_dates = defaultdict(list)
    for date, r in date_regime.items():
        regime_dates[r].append(date)

    REGIME_ORDER = ['CRISIS', 'STRESS', 'MODERATE', 'CALM', 'RECOVERY']
    regime_profiles = {}

    for regime in REGIME_ORDER:
        r_dates = regime_dates.get(regime, [])
        if len(r_dates) < 8:
            continue
        r_set = set(r_dates)

        # Sector stress in this regime
        sec_stress_in_r = {}
        for sec in sec_names:
            sb  = stress_brd[sec]
            vs  = [sb[d] for d in r_dates if d in sb]
            sec_stress_in_r[sec] = round(sum(vs)/len(vs), 3) if vs else 0

        sorted_secs = sorted(sec_stress_in_r.items(), key=lambda x:-x[1])

        # Within-regime sector correlations (top pairs)
        corr_pairs = []
        for i, s1 in enumerate(sec_names[:10]):
            for j, s2 in enumerate(sec_names[:10]):
                if i >= j:
                    continue
                sb1 = stress_brd[s1]
                sb2 = stress_brd[s2]
                common = sorted(set(r_dates) & set(sb1) & set(sb2))
                if len(common) < 8:
                    continue
                v1 = [sb1[d] for d in common]
                v2 = [sb2[d] for d in common]
                c  = _pearson(v1, v2)
                if abs(c) > 0.30:
                    corr_pairs.append({'s1': s1, 's2': s2, 'corr': round(c, 2)})
        corr_pairs.sort(key=lambda x: -abs(x['corr']))

        # Network density: average cross-sector correlation in this regime
        all_corrs = [abs(p['corr']) for p in corr_pairs] if corr_pairs else [0]
        net_density = round(sum(all_corrs)/len(all_corrs), 3)

        regime_profiles[regime] = {
            'n_dates':           len(r_dates),
            'pct_of_history':    round(len(r_dates)/len(date_regime), 2),
            'top_stressed':      [{'sector': s, 'avg': v} for s,v in sorted_secs[:4]],
            'calmest':           [{'sector': s, 'avg': v} for s,v in sorted_secs[-3:]],
            'top_correlations':  corr_pairs[:5],
            'network_density':   net_density,
        }

    # ── Topology evolution: what changes between CALM and CRISIS? ─────────
    topology_changes = []
    calm_top   = set(s['sector'] for s in regime_profiles.get('CALM',   {}).get('top_stressed', [])[:3])
    crisis_top = set(s['sector'] for s in regime_profiles.get('CRISIS', {}).get('top_stressed', [])[:3])

    if calm_top and crisis_top:
        always_stressed = calm_top & crisis_top
        crisis_only     = crisis_top - calm_top
        calm_only       = calm_top - crisis_top
        if always_stressed: topology_changes.append(f'دائماً مجهد: {", ".join(always_stressed)}')
        if crisis_only:     topology_changes.append(f'يظهر في الأزمات فقط: {", ".join(crisis_only)}')
        if calm_only:       topology_changes.append(f'مجهد حتى في الهدوء: {", ".join(calm_only)}')

    # Network density comparison: CRISIS should be denser (more correlated)
    crisis_density = regime_profiles.get('CRISIS', {}).get('network_density', 0)
    calm_density   = regime_profiles.get('CALM',   {}).get('network_density', 0)
    if crisis_density and calm_density:
        topology_changes.append(
            f'كثافة الشبكة: CRISIS={crisis_density} vs CALM={calm_density} '
            f'({"تضاعف" if crisis_density > calm_density*1.5 else "ارتفع"} في الأزمات)'
        )

    return {
        'elapsed_sec':          round(time.time()-t0, 2),
        'n_dates':              len(date_regime),
        'regime_distribution':  dict(sorted(regime_counts.items(), key=lambda x:-x[1])),
        'regime_profiles':      regime_profiles,
        'topology_changes':     topology_changes,
        'network_invariants': [
            f'{s["sector"]} مجهد دائماً بـ {s["avg"]:.0%} في الأزمات'
            for s in regime_profiles.get('CRISIS', {}).get('top_stressed', [])[:2]
        ],
    }


def cmd_propagation_full(params):
    """Run all propagation analyses and return combined results."""
    t0 = time.time()
    results = {}
    plan = [
        ('propagation_now',      cmd_propagation_now),
        ('contagion_chains',     cmd_contagion_chains),
        ('sector_transmission',  cmd_sector_transmission),
        ('instability_cascades', cmd_instability_cascades),
        ('role_classification',  cmd_role_classification),
        ('diffusion_analysis',   cmd_diffusion_analysis),
        ('regime_networks',      cmd_regime_networks),
    ]
    for key, fn in plan:
        try:
            results[key] = fn(params)
        except Exception as e:
            import traceback
            results[key] = {'error': str(e), 'trace': traceback.format_exc()[-300:]}
    results['elapsed_sec'] = round(time.time()-t0, 2)
    return results

# ─── Dispatcher ───────────────────────────────────────────────────────────────

COMMANDS = {
    'propagation_now':      cmd_propagation_now,
    'contagion_chains':     cmd_contagion_chains,
    'sector_transmission':  cmd_sector_transmission,
    'instability_cascades': cmd_instability_cascades,
    'role_classification':  cmd_role_classification,
    'diffusion_analysis':   cmd_diffusion_analysis,
    'regime_networks':      cmd_regime_networks,
    'propagation_full':     cmd_propagation_full,
}

if __name__ == '__main__':
    try:
        if len(sys.argv) >= 2 and sys.argv[1] in COMMANDS:
            command = sys.argv[1]
            params  = json.loads(sys.argv[2]) if len(sys.argv) >= 3 else {}
        else:
            payload = json.loads(sys.stdin.read())
            command = payload.get('command', '')
            params  = payload.get('params', {})
        fn = COMMANDS.get(command)
        if fn is None:
            out = {'error': f'Unknown command: {command}. Available: {list(COMMANDS)}'}
        else:
            out = fn(params)
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
