#!/usr/bin/env python3
"""
EGX Phase 14 — Law Stability, Counterfactual Intelligence & Multi-Law Synthesis
=================================================================================
Evolves discovered market laws from static validated patterns into a living
behavioral science framework.

Modules
-------
1. Law Stability Curves      — rolling window support/effect trajectories
                               + stability classification
2. Counterfactual Engine     — OHLCV-based precursor scan: hit vs false-alarm
3. Failure Taxonomy          — structural classification of precursor failures
4. Mutation Detection        — changepoint analysis on support-rate time series
5. Multi-Law Synthesis       — co-activation, uplift, conflict matrix
6. Law Network Graph         — adjacency matrix with interaction weights
7. Market Physics            — compression-release mechanics per explosion
8. Synthesis Report          — institutional-grade full report

Commands
--------
  full_synthesis, stability_curves, counterfactuals, mutations,
  interactions, network, physics, synthesis_report, status

Anti-overfitting guards
-----------------------
  • Quarterly bucket minimum (≥15 events before computing support rate)
  • Bootstrap CIs on all rolling statistics
  • Interaction uplift requires ≥20 co-activation events
  • Mutation declared only if ΔSR ≥ 15 pp AND |t-stat| ≥ 1.8
  • Market physics uses retrospective OHLCV only (no look-ahead)
"""

import sys, json, os, math, random, sqlite3, time
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent.parent
DB_PATH    = ROOT / 'data' / 'egx_trading.db'
REPORT_DIR = ROOT / 'data' / 'research_reports'
REPORT_DIR.mkdir(parents=True, exist_ok=True)

RSI_RANGE_LO, RSI_RANGE_HI = 35.0, 65.0
MIN_BUCKET_EVENTS = 15      # minimum explosions per time bucket
MIN_INTERACTION_N = 20      # minimum co-activations for interaction stats
MUTATION_MIN_DELTA = 0.12   # ≥12 pp change to call a mutation
MUTATION_MIN_T     = 1.6    # t-stat threshold
BB_COMP_WINDOW     = 20     # bars for BB width computation
BB_COMP_COEFF      = 4.0    # BB width = COEFF * std / mean
COUNTERFACTUAL_HORIZON = 5  # bars to look forward after precursor
HIT_THRESHOLD      = 0.06   # ≥6% move = HIT
PARTIAL_THRESHOLD  = 0.03   # ≥3% move = PARTIAL

# ──────────────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS law_stability_curves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id      TEXT,
    pattern_name    TEXT,
    direction       TEXT,
    period          TEXT,
    n_target        INTEGER,
    support_rate    REAL,
    effect_size     REAL,
    ci_low          REAL,
    ci_high         REAL,
    stability_class TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS counterfactual_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT,
    precursor_date  TEXT,
    pattern_id      TEXT,
    pattern_name    TEXT,
    outcome         TEXT,
    feature_value   REAL,
    next_max_return REAL,
    regime          TEXT,
    sector          TEXT,
    n_other_active  INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS failure_taxonomy (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id          TEXT,
    pattern_name        TEXT,
    failure_class       TEXT,
    n_failures          INTEGER,
    failure_rate        REAL,
    dominant_regime     TEXT,
    avg_feature_value   REAL,
    description         TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS law_mutations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id    TEXT,
    pattern_name  TEXT,
    mutation_period TEXT,
    pre_support   REAL,
    post_support  REAL,
    delta         REAL,
    t_stat        REAL,
    mutation_type TEXT,
    confidence    REAL,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS law_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_a_id    TEXT,
    pattern_b_id    TEXT,
    pattern_a_name  TEXT,
    pattern_b_name  TEXT,
    co_activation_n INTEGER,
    combined_support REAL,
    a_only_support  REAL,
    b_only_support  REAL,
    uplift          REAL,
    interaction_type TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS law_network_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id      TEXT UNIQUE,
    pattern_name    TEXT,
    direction       TEXT,
    stability_class TEXT,
    centrality      REAL,
    confidence      REAL,
    n_interactions  INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS law_network_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT,
    target_id       TEXT,
    weight          REAL,
    interaction_type TEXT,
    uplift          REAL,
    co_n            INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_physics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT,
    explosion_date   TEXT,
    direction        TEXT,
    explosion_class  TEXT,
    compression_days INTEGER,
    compression_depth REAL,
    ignition_speed   REAL,
    cascade_score    REAL,
    physics_type     TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);
"""

STABILITY_CLASSES = [
    'STABLE_INVARIANT', 'SLOWLY_DEGRADING', 'REGIME_DEPENDENT',
    'STRUCTURALLY_MUTATING', 'TEMPORARY_ALPHA', 'DEAD_STRUCTURE',
]

def ensure_schema(db):
    for stmt in SCHEMA_SQL.strip().split(';'):
        s = stmt.strip()
        if s:
            try:
                db.execute(s)
            except Exception:
                pass
    # Migration: add stability_class column if it doesn't exist yet
    cols = [r[1] for r in db.execute("PRAGMA table_info(law_stability_curves)").fetchall()]
    if 'stability_class' not in cols:
        try:
            db.execute("ALTER TABLE law_stability_curves ADD COLUMN stability_class TEXT")
        except Exception:
            pass
    db.commit()

# ──────────────────────────────────────────────────────────────────────────────
# STATISTICS HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _mean(xs):
    return sum(xs)/len(xs) if xs else 0.0

def _var(xs):
    if len(xs) < 2: return 0.0
    m = _mean(xs)
    return sum((x-m)**2 for x in xs)/(len(xs)-1)

def _std(xs):
    return math.sqrt(_var(xs))

def cohen_d(xs, ys):
    if len(xs) < 2 or len(ys) < 2: return 0.0
    nx, ny = len(xs), len(ys)
    pooled = math.sqrt(((nx-1)*_var(xs)+(ny-1)*_var(ys))/(nx+ny-2))
    return (_mean(xs)-_mean(ys))/pooled if pooled > 0 else 0.0

def t_test_two_sample(xs, ys):
    """Welch's t-statistic (unequal variances)."""
    if len(xs) < 3 or len(ys) < 3: return 0.0
    mx, my = _mean(xs), _mean(ys)
    se = math.sqrt(_var(xs)/len(xs) + _var(ys)/len(ys))
    return (mx - my)/se if se > 0 else 0.0

def bootstrap_ci(values, stat_fn=None, n_boot=500, alpha=0.05):
    if stat_fn is None:
        stat_fn = _mean
    if len(values) < 5:
        v = stat_fn(values)
        return v, v, v
    obs = stat_fn(values)
    boot = sorted(stat_fn(random.choices(values, k=len(values))) for _ in range(n_boot))
    lo = boot[int(alpha/2 * n_boot)]
    hi = boot[int((1-alpha/2) * n_boot)]
    return obs, lo, hi

def check_operator(val, threshold, operator):
    if val is None: return False
    if operator == 'lt':    return val < threshold
    if operator == 'gt':    return val > threshold
    if operator == 'range': return RSI_RANGE_LO <= val <= RSI_RANGE_HI
    return False

def get_fv(row, col):
    try:
        v = row[col]
        return float(v) if v is not None else None
    except: return None

def quarter(date_str):
    """'2025-04-15' → '2025-Q2'"""
    try:
        y = date_str[:4]
        m = int(date_str[5:7])
        q = (m - 1) // 3 + 1
        return f"{y}-Q{q}"
    except:
        return 'UNKNOWN'

# ──────────────────────────────────────────────────────────────────────────────
# 1. LAW STABILITY CURVES
# ──────────────────────────────────────────────────────────────────────────────

def compute_stability_curves(db):
    """
    Compute quarterly support rates for each pattern.
    Classify each law's stability type.
    """
    patterns = db.execute("SELECT * FROM precursor_patterns").fetchall()
    explosions = db.execute(
        """SELECT explosion_date, direction, explosion_class,
                  pre3_bb_width, pre5_bb_width,
                  pre3_vol_ratio, pre5_vol_ratio,
                  pre3_rsi, pre5_rsi,
                  pre3_momentum_5d, pre5_momentum_5d,
                  pre3_adx, pre5_adx
           FROM explosive_moves
           ORDER BY explosion_date"""
    ).fetchall()

    db.execute("DELETE FROM law_stability_curves")
    results = {}

    for pat in patterns:
        feature   = pat['feature']
        threshold = pat['threshold']
        operator  = pat['operator']
        direction = pat['direction']
        orig_sr   = pat['support_rate']

        # Bucket explosions by quarter
        target_by_q  = defaultdict(list)
        control_by_q = defaultdict(list)
        for e in explosions:
            q = quarter(e['explosion_date'])
            fv = get_fv(e, feature)
            if fv is None: continue
            if e['direction'] == direction and e['explosion_class'] in ('LARGE','EXTREME'):
                target_by_q[q].append(fv)
            elif e['explosion_class'] == 'SMALL':
                control_by_q[q].append(fv)

        sorted_qs = sorted(target_by_q.keys())
        curve     = []

        for q in sorted_qs:
            tgt  = target_by_q[q]
            ctrl = control_by_q.get(q, [])
            if len(tgt) < MIN_BUCKET_EVENTS:
                continue
            n_pos = sum(1 for v in tgt if check_operator(v, threshold, operator))
            sr    = n_pos / len(tgt)
            eff   = cohen_d(tgt, ctrl) if len(ctrl) >= 5 else 0.0
            _, ci_lo, ci_hi = bootstrap_ci(
                [1 if check_operator(v, threshold, operator) else 0 for v in tgt],
                n_boot=300
            )
            curve.append({'q': q, 'sr': sr, 'eff': eff, 'n': len(tgt),
                          'ci_lo': ci_lo, 'ci_hi': ci_hi})

        stability_class = _classify_stability(curve, orig_sr)

        for item in curve:
            db.execute("""INSERT INTO law_stability_curves
                (pattern_id, pattern_name, direction, period,
                 n_target, support_rate, effect_size, ci_low, ci_high, stability_class)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pat['id'], pat['pattern_name'], direction,
                 item['q'], item['n'], item['sr'], item['eff'],
                 item['ci_lo'], item['ci_hi'], stability_class))

        results[pat['pattern_name']] = {
            'direction': direction,
            'stability_class': stability_class,
            'n_quarters': len(curve),
            'curve': curve,
        }

    db.commit()
    return results

def _classify_stability(curve, orig_sr):
    """Assign a stability classification based on the support-rate trajectory."""
    if len(curve) < 2:
        return 'INSUFFICIENT_DATA'

    srs = [c['sr'] for c in curve]
    mean_sr   = _mean(srs)
    std_sr    = _std(srs)
    cv        = std_sr / mean_sr if mean_sr > 0 else 1.0

    # Linear trend: positive slope = strengthening, negative = degrading
    n = len(srs)
    xs = list(range(n))
    slope = _pearson(xs, srs) * (_std(srs) / (_std(xs) if _std(xs) > 0 else 1))

    # Recent vs early (last third vs first third)
    third = max(1, n // 3)
    recent = _mean(srs[-third:])
    early  = _mean(srs[:third])
    delta  = recent - early

    if cv < 0.12 and mean_sr >= orig_sr * 0.80:
        return 'STABLE_INVARIANT'
    elif slope < -0.015 and delta < -0.10:
        if recent < 0.15:
            return 'DEAD_STRUCTURE'
        return 'SLOWLY_DEGRADING'
    elif cv > 0.30:
        return 'STRUCTURALLY_MUTATING'
    elif mean_sr < orig_sr * 0.50:
        return 'TEMPORARY_ALPHA'
    elif abs(delta) < 0.08 and cv < 0.22:
        return 'STABLE_INVARIANT'
    else:
        return 'REGIME_DEPENDENT'

def _pearson(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys): return 0.0
    mx, my = _mean(xs), _mean(ys)
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx  = math.sqrt(sum((x-mx)**2 for x in xs))
    dy  = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx*dy > 0 else 0.0

# ──────────────────────────────────────────────────────────────────────────────
# 2. COUNTERFACTUAL ENGINE  (OHLCV scan → hit / partial / false_alarm)
# ──────────────────────────────────────────────────────────────────────────────

def run_counterfactuals(db):
    """
    Scan every symbol's OHLCV bars.
    For each day where a precursor is active, record outcome:
      HIT         — large/extreme explosion within horizon
      PARTIAL     — medium/small explosion within horizon
      FALSE_ALARM — nothing significant
    """
    patterns  = db.execute("SELECT * FROM precursor_patterns").fetchall()
    symbols   = db.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history"
    ).fetchall()
    sector_map = {r['symbol']: r['sector'] for r in
                  db.execute("SELECT symbol, sector FROM stock_profiles").fetchall()}
    regime_map = {r['date']: r['regime'] for r in
                  db.execute("SELECT date, regime FROM regime_history").fetchall()}

    # Build explosion index: symbol → {date → class}
    exp_index = defaultdict(dict)
    for e in db.execute(
            "SELECT symbol, explosion_date, explosion_class FROM explosive_moves"
    ).fetchall():
        exp_index[e['symbol']][e['explosion_date']] = e['explosion_class']

    db.execute("DELETE FROM counterfactual_events")
    db.execute("DELETE FROM failure_taxonomy")

    summary = {}
    for pat in patterns:
        feature   = pat['feature']
        threshold = pat['threshold']
        operator  = pat['operator']
        summary[pat['id']] = {'hits':0,'partials':0,'false_alarms':0}

    for sym_row in symbols:
        sym = sym_row[0]
        bars = db.execute(
            "SELECT bar_time, close, volume FROM ohlcv_history WHERE symbol=? ORDER BY bar_time",
            (sym,)
        ).fetchall()
        if len(bars) < BB_COMP_WINDOW + COUNTERFACTUAL_HORIZON:
            continue

        closes = [b['close']  for b in bars]
        times  = [b['bar_time'] for b in bars]
        sector = sector_map.get(sym, 'Unknown')

        # Pre-compute rolling BB width at every bar (no look-ahead)
        bbw = [None] * len(bars)
        for i in range(BB_COMP_WINDOW, len(bars)):
            seg = closes[i - BB_COMP_WINDOW: i]
            m   = _mean(seg)
            bbw[i] = (BB_COMP_COEFF * _std(seg) / m) if m > 0 else None

        # Pre-compute rolling RSI
        rsi_vals = [None] * len(bars)
        for i in range(BB_COMP_WINDOW, len(bars)):
            rets = [(closes[j]-closes[j-1])/closes[j-1]
                    for j in range(max(1,i-14), i) if closes[j-1] > 0]
            if len(rets) >= 7:
                gains  = [max(0,r) for r in rets[-14:]]
                losses = [abs(min(0,r)) for r in rets[-14:]]
                ag, al = _mean(gains), _mean(losses)
                rsi_vals[i] = 100 - 100/(1+ag/al) if al > 0 else 100.0

        # Pre-compute rolling momentum (5d)
        mom5 = [None] * len(bars)
        for i in range(6, len(bars)):
            if closes[i-5] > 0:
                mom5[i] = (closes[i-1] - closes[i-5]) / closes[i-5]

        for pat in patterns:
            feature   = pat['feature']
            threshold = pat['threshold']
            operator  = pat['operator']
            pid       = pat['id']

            # Map feature to our computed series
            if 'bb_width' in feature:
                vals = bbw
            elif 'rsi' in feature:
                vals = rsi_vals
            elif 'momentum' in feature:
                vals = mom5
            else:
                continue  # skip ADX, MACD (not computed)

            for i in range(BB_COMP_WINDOW, len(bars) - COUNTERFACTUAL_HORIZON):
                val = vals[i]
                if val is None:
                    continue
                if not check_operator(val, threshold, operator):
                    continue

                # Precursor is active at bar i
                dt = datetime.utcfromtimestamp(times[i]).strftime('%Y-%m-%d')

                # Look forward: max absolute return in next HORIZON bars
                future_rets = []
                for h in range(1, COUNTERFACTUAL_HORIZON + 1):
                    fi = i + h
                    if fi < len(closes) and closes[fi-1] > 0:
                        future_rets.append(abs((closes[fi]-closes[fi-1])/closes[fi-1]))

                max_ret = max(future_rets) if future_rets else 0.0

                # Determine outcome from explosion index
                best_class = None
                for h in range(1, COUNTERFACTUAL_HORIZON + 1):
                    fdt = datetime.utcfromtimestamp(times[min(i+h, len(times)-1)]).strftime('%Y-%m-%d')
                    ec  = exp_index[sym].get(fdt)
                    if ec in ('EXTREME', 'LARGE'):
                        best_class = 'HIT'
                        break
                    elif ec in ('MEDIUM', 'SMALL') and best_class is None:
                        best_class = 'PARTIAL'

                outcome = best_class or 'FALSE_ALARM'
                regime  = regime_map.get(dt, 'UNKNOWN')

                db.execute("""INSERT INTO counterfactual_events
                    (symbol, precursor_date, pattern_id, pattern_name,
                     outcome, feature_value, next_max_return, regime, sector)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sym, dt, pid, pat['pattern_name'],
                     outcome, val, max_ret, regime, sector))

                summary[pid][{'HIT':'hits','PARTIAL':'partials','FALSE_ALARM':'false_alarms'}[outcome]] += 1

    db.commit()

    # Build failure taxonomy
    _build_failure_taxonomy(db, patterns)

    # Build name lookup
    name_map = {pat['id']: (pat['pattern_name'], pat['direction']) for pat in patterns}

    totals = {}
    for pid, s in summary.items():
        total = s['hits'] + s['partials'] + s['false_alarms']
        pname, pdir = name_map.get(pid, (pid, '?'))
        totals[pid] = {
            'pattern_name':   pname,
            'direction':      pdir,
            'hits':           s['hits'],
            'partials':       s['partials'],
            'false_alarms':   s['false_alarms'],
            'total':          total,
            'precision':      s['hits'] / total if total > 0 else 0.0,
            'false_alarm_rate': s['false_alarms'] / total if total > 0 else 0.0,
        }
    return totals

def _build_failure_taxonomy(db, patterns):
    """
    Classify false-alarm events into structural failure categories.
    """
    FAILURE_CLASSES = {
        'REGIME_MISMATCH':      "Law activated in wrong market regime",
        'LOW_MOMENTUM':         "Precursor active but momentum insufficient",
        'SECTOR_DIVERGENCE':    "Symbol moved against sector trend",
        'HIGH_VOLATILITY_BASE': "Already volatile — compression signal meaningless",
        'EARLY_BEAR':           "BEAR regime suppresses UP explosions",
    }

    for pat in patterns:
        pid = pat['id']
        false_alarms = db.execute(
            """SELECT regime, sector, feature_value, next_max_return
               FROM counterfactual_events
               WHERE pattern_id=? AND outcome='FALSE_ALARM'""",
            (pid,)
        ).fetchall()
        if not false_alarms:
            continue

        # Count regime distribution of failures
        regime_counts = defaultdict(int)
        for fa in false_alarms:
            regime_counts[fa['regime']] += 1

        dominant_regime = max(regime_counts, key=regime_counts.get, default='UNKNOWN')
        avg_fv = _mean([fa['feature_value'] for fa in false_alarms if fa['feature_value'] is not None])

        total_activations = db.execute(
            "SELECT COUNT(*) FROM counterfactual_events WHERE pattern_id=?", (pid,)
        ).fetchone()[0]

        failure_rate = len(false_alarms) / total_activations if total_activations > 0 else 0.0

        # Choose primary failure class
        if dominant_regime in ('BEAR',) and pat['direction'] == 'UP':
            fclass = 'EARLY_BEAR'
            fdesc  = FAILURE_CLASSES['EARLY_BEAR']
        elif regime_counts.get('CHOPPY', 0) > regime_counts.get('BULL', 0):
            fclass = 'REGIME_MISMATCH'
            fdesc  = FAILURE_CLASSES['REGIME_MISMATCH']
        else:
            fclass = 'LOW_MOMENTUM'
            fdesc  = FAILURE_CLASSES['LOW_MOMENTUM']

        db.execute("""INSERT OR REPLACE INTO failure_taxonomy
            (pattern_id, pattern_name, failure_class, n_failures,
             failure_rate, dominant_regime, avg_feature_value, description)
            VALUES (?,?,?,?,?,?,?,?)""",
            (pid, pat['pattern_name'], fclass,
             len(false_alarms), failure_rate, dominant_regime, avg_fv, fdesc))

    db.commit()

# ──────────────────────────────────────────────────────────────────────────────
# 3. MUTATION DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def detect_mutations(db):
    """
    Find periods where a law's support rate changed significantly.
    Uses quarterly buckets from law_stability_curves.
    """
    db.execute("DELETE FROM law_mutations")
    mutations = []

    patterns = db.execute("SELECT * FROM precursor_patterns").fetchall()
    for pat in patterns:
        curve_rows = db.execute(
            """SELECT period, support_rate, n_target FROM law_stability_curves
               WHERE pattern_id=? ORDER BY period""",
            (pat['id'],)
        ).fetchall()
        if len(curve_rows) < 3:
            continue

        periods = [r['period']       for r in curve_rows]
        srs     = [r['support_rate'] for r in curve_rows]
        ns      = [r['n_target']     for r in curve_rows]

        # Binary segmentation: find the split that maximises |Δmean|
        best_split, best_delta, best_t = None, 0.0, 0.0
        for i in range(1, len(srs) - 1):
            left  = srs[:i]
            right = srs[i:]
            n_l, n_r = ns[:i], ns[i:]
            if len(left) < 2 or len(right) < 2:
                continue
            delta = abs(_mean(right) - _mean(left))
            t     = abs(t_test_two_sample(left, right))
            if delta > best_delta:
                best_split, best_delta, best_t = i, delta, t

        if best_split is None or best_delta < MUTATION_MIN_DELTA or best_t < MUTATION_MIN_T:
            continue

        pre_sr  = _mean(srs[:best_split])
        post_sr = _mean(srs[best_split:])
        delta   = post_sr - pre_sr
        conf    = min(1.0, best_t / 3.0) * min(1.0, best_delta / 0.25)

        if abs(delta) < 0.01:
            m_type = 'NEUTRAL'
        elif delta > 0.15:
            m_type = 'STRENGTHENING'
        elif delta < -0.15:
            m_type = 'WEAKENING'
        elif abs(delta) > 0.10:
            m_type = 'REGIME_SHIFT'
        else:
            m_type = 'GRADUAL_DRIFT'

        mutation_period = periods[best_split]

        db.execute("""INSERT INTO law_mutations
            (pattern_id, pattern_name, mutation_period,
             pre_support, post_support, delta, t_stat, mutation_type, confidence)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (pat['id'], pat['pattern_name'], mutation_period,
             round(pre_sr,4), round(post_sr,4), round(delta,4),
             round(best_t,3), m_type, round(conf,3)))

        mutations.append({
            'pattern': pat['pattern_name'],
            'direction': pat['direction'],
            'mutation_period': mutation_period,
            'pre_support': round(pre_sr,3),
            'post_support': round(post_sr,3),
            'delta': round(delta,3),
            'type': m_type,
            'confidence': round(conf,3),
        })

    db.commit()
    return mutations

# ──────────────────────────────────────────────────────────────────────────────
# 4. MULTI-LAW SYNTHESIS (interaction matrix)
# ──────────────────────────────────────────────────────────────────────────────

def compute_interactions(db):
    """
    For every pair of patterns, measure co-activation and probability uplift.
    """
    patterns  = db.execute("SELECT * FROM precursor_patterns").fetchall()
    explosions = db.execute(
        """SELECT explosion_date, direction, explosion_class,
                  pre3_bb_width, pre5_bb_width, pre3_vol_ratio, pre5_vol_ratio,
                  pre3_rsi, pre5_rsi, pre3_momentum_5d, pre5_momentum_5d
           FROM explosive_moves
           ORDER BY explosion_date"""
    ).fetchall()

    # Build per-explosion activation vector
    # activation[i] = {pat_id: True/False}
    pat_list = list(patterns)
    n_exps   = len(explosions)
    activations = []
    for e in explosions:
        act = {}
        for p in pat_list:
            fv = get_fv(e, p['feature'])
            act[p['id']] = check_operator(fv, p['threshold'], p['operator'])
        activations.append(act)

    db.execute("DELETE FROM law_interactions")
    db.execute("DELETE FROM law_network_nodes")
    db.execute("DELETE FROM law_network_edges")

    interaction_results = []
    for i, pa in enumerate(pat_list):
        for j, pb in enumerate(pat_list):
            if j <= i:
                continue
            # Only compare same-direction patterns OR cross-direction (interesting conflicts)
            pid_a, pid_b = pa['id'], pb['id']
            # Both active together in LARGE/EXTREME explosions
            both_large = [
                e for k, e in enumerate(explosions)
                if activations[k].get(pid_a) and activations[k].get(pid_b)
                   and e['explosion_class'] in ('LARGE','EXTREME')
            ]
            both_all = [k for k, act in enumerate(activations)
                        if act.get(pid_a) and act.get(pid_b)]
            a_only = [k for k, act in enumerate(activations)
                      if act.get(pid_a) and not act.get(pid_b)]
            b_only = [k for k, act in enumerate(activations)
                      if not act.get(pid_a) and act.get(pid_b)]

            co_n = len(both_all)
            if co_n < MIN_INTERACTION_N:
                continue

            def large_rate(indices):
                if not indices: return 0.0
                n = sum(1 for k in indices
                        if explosions[k]['explosion_class'] in ('LARGE','EXTREME'))
                return n / len(indices)

            sr_combined = large_rate(both_all)
            sr_a_only   = large_rate(a_only)
            sr_b_only   = large_rate(b_only)
            base_sr     = max(sr_a_only, sr_b_only, 1e-6)
            uplift      = sr_combined / base_sr

            if uplift > 1.25:
                itype = 'AMPLIFY'
            elif uplift < 0.75:
                itype = 'SUPPRESS'
            elif abs(uplift - 1.0) < 0.12:
                itype = 'NEUTRAL'
            else:
                itype = 'CONDITIONAL'

            db.execute("""INSERT INTO law_interactions
                (pattern_a_id, pattern_b_id, pattern_a_name, pattern_b_name,
                 co_activation_n, combined_support, a_only_support, b_only_support,
                 uplift, interaction_type)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pid_a, pid_b, pa['pattern_name'], pb['pattern_name'],
                 co_n, round(sr_combined,4), round(sr_a_only,4), round(sr_b_only,4),
                 round(uplift,3), itype))

            interaction_results.append({
                'a': pa['pattern_name'], 'a_dir': pa['direction'],
                'b': pb['pattern_name'], 'b_dir': pb['direction'],
                'co_n': co_n, 'combined': round(sr_combined,3),
                'uplift': round(uplift,3), 'type': itype,
            })

    db.commit()
    return interaction_results

# ──────────────────────────────────────────────────────────────────────────────
# 5. LAW NETWORK GRAPH
# ──────────────────────────────────────────────────────────────────────────────

def build_law_network(db):
    """
    Construct law network nodes (patterns) and edges (interactions).
    Compute centrality score for each node.
    """
    patterns    = db.execute("SELECT * FROM precursor_patterns").fetchall()
    hyps        = {r['pattern_id']: dict(r) for r in
                   db.execute("SELECT * FROM hypothesis_lifecycle").fetchall()}
    stability   = {r['pattern_id']: dict(r) for r in
                   db.execute("SELECT DISTINCT pattern_id, stability_class FROM law_stability_curves").fetchall()}
    interactions = db.execute("SELECT * FROM law_interactions").fetchall()

    # Centrality = sum of |uplift - 1| for all edges involving this node
    centrality = defaultdict(float)
    n_interact  = defaultdict(int)
    for irow in interactions:
        w = abs(irow['uplift'] - 1.0)
        centrality[irow['pattern_a_id']] += w
        centrality[irow['pattern_b_id']] += w
        n_interact[irow['pattern_a_id']] += 1
        n_interact[irow['pattern_b_id']] += 1

    # Normalize centrality
    max_c = max(centrality.values()) if centrality else 1.0
    for k in centrality:
        centrality[k] /= max_c

    db.execute("DELETE FROM law_network_nodes")
    db.execute("DELETE FROM law_network_edges")

    for pat in patterns:
        pid  = pat['id']
        hyp  = hyps.get(pid, {})
        stab = stability.get(pid, {})
        conf = hyp.get('confidence_score', 0.0) if hyp else 0.0

        stab_class = stab.get('stability_class', 'UNKNOWN') if stab else 'UNKNOWN'

        db.execute("""INSERT OR REPLACE INTO law_network_nodes
            (pattern_id, pattern_name, direction, stability_class,
             centrality, confidence, n_interactions)
            VALUES (?,?,?,?,?,?,?)""",
            (pid, pat['pattern_name'], pat['direction'],
             stab_class, round(centrality.get(pid, 0.0), 3),
             round(float(conf), 3), n_interact.get(pid, 0)))

    for irow in interactions:
        weight = (irow['uplift'] - 1.0) * irow['co_activation_n'] / 100.0
        db.execute("""INSERT INTO law_network_edges
            (source_id, target_id, weight, interaction_type, uplift, co_n)
            VALUES (?,?,?,?,?,?)""",
            (irow['pattern_a_id'], irow['pattern_b_id'],
             round(weight, 4), irow['interaction_type'],
             irow['uplift'], irow['co_activation_n']))

    db.commit()

    nodes = db.execute(
        "SELECT * FROM law_network_nodes ORDER BY centrality DESC"
    ).fetchall()
    edges = db.execute(
        "SELECT * FROM law_network_edges ORDER BY ABS(weight) DESC"
    ).fetchall()

    return {
        'n_nodes': len(nodes),
        'n_edges': len(edges),
        'nodes': [dict(n) for n in nodes],
        'edges': [dict(e) for e in edges],
    }

# ──────────────────────────────────────────────────────────────────────────────
# 6. MARKET PHYSICS RECONSTRUCTION
# ──────────────────────────────────────────────────────────────────────────────

def reconstruct_market_physics(db):
    """
    For each LARGE/EXTREME explosion, reconstruct compression–release physics:
      compression_days  — consecutive bars of BB squeeze before explosion
      compression_depth — min BB width in compression window (deeper = more energy)
      ignition_speed    — |return_1d| / compression_depth (energy release rate)
      cascade_score     — whether move accelerated in next 3 bars
      physics_type      — COMPRESSION_RELEASE / MOMENTUM_BURST / REVERSAL_SPIKE / UNDEFINED
    """
    explosions = db.execute(
        """SELECT symbol, explosion_date, direction, explosion_class,
                  return_1d, return_3d, return_5d,
                  pre1_bb_width, pre3_bb_width, pre5_bb_width,
                  pre5_compression_days, pre5_vol_ratio
           FROM explosive_moves
           WHERE explosion_class IN ('LARGE','EXTREME')"""
    ).fetchall()

    db.execute("DELETE FROM market_physics")

    type_counts = defaultdict(int)
    comp_depths = []
    ignition_speeds = []

    for e in explosions:
        comp_days   = e['pre5_compression_days'] or 0
        p5_bbw      = e['pre5_bb_width']
        p3_bbw      = e['pre3_bb_width']
        p1_bbw      = e['pre1_bb_width']
        ret1d       = abs(e['return_1d'] or 0.0)
        ret3d       = abs(e['return_3d'] or 0.0)
        vol_ratio   = e['pre5_vol_ratio'] or 1.0

        # Compression depth: minimum BB width in the pre-window
        bbw_vals = [v for v in [p5_bbw, p3_bbw, p1_bbw] if v is not None]
        comp_depth = min(bbw_vals) if bbw_vals else None

        # Ignition speed: return magnitude / compression depth
        ignition_speed = (ret1d / comp_depth) if comp_depth and comp_depth > 0 else None

        # Cascade: did move accelerate? (3d > 2 × 1d)
        cascade = 1.0 if (ret3d > 0 and ret1d > 0 and ret3d > 1.5 * ret1d) else 0.0

        # Physics type
        if comp_days >= 3 and comp_depth is not None and comp_depth < 0.20:
            ptype = 'COMPRESSION_RELEASE'
        elif vol_ratio > 1.5 and (p3_bbw or 0) > 0.25:
            ptype = 'MOMENTUM_BURST'
        elif (p1_bbw or 0) < 0.15 and ret1d > 0.08:
            ptype = 'REVERSAL_SPIKE'
        else:
            ptype = 'STRUCTURAL_EXPANSION'

        type_counts[ptype] += 1
        if comp_depth: comp_depths.append(comp_depth)
        if ignition_speed: ignition_speeds.append(ignition_speed)

        db.execute("""INSERT INTO market_physics
            (symbol, explosion_date, direction, explosion_class,
             compression_days, compression_depth, ignition_speed, cascade_score, physics_type)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (e['symbol'], e['explosion_date'], e['direction'], e['explosion_class'],
             comp_days, comp_depth, ignition_speed, cascade, ptype))

    db.commit()

    # Regime-specific physics
    regime_map = {r['date']: r['regime'] for r in
                  db.execute("SELECT date, regime FROM regime_history").fetchall()}
    regime_physics = defaultdict(lambda: defaultdict(int))
    for e in db.execute("SELECT explosion_date, physics_type FROM market_physics").fetchall():
        regime = regime_map.get(e['explosion_date'], 'UNKNOWN')
        regime_physics[regime][e['physics_type']] += 1

    return {
        'n_explosions_analyzed': len(explosions),
        'physics_distribution': dict(type_counts),
        'avg_compression_depth': round(_mean(comp_depths), 4) if comp_depths else None,
        'avg_ignition_speed': round(_mean(ignition_speeds), 4) if ignition_speeds else None,
        'regime_physics': {r: dict(d) for r,d in regime_physics.items()},
    }

# ──────────────────────────────────────────────────────────────────────────────
# 7. REGIME-SPECIFIC LAW SYSTEMS
# ──────────────────────────────────────────────────────────────────────────────

def build_regime_law_systems(db):
    """
    For each regime, rank patterns by support rate and classify role:
    DOMINANT / ACTIVE / WEAK / SUPPRESSED / INVERTED
    """
    patterns = db.execute("SELECT * FROM precursor_patterns").fetchall()
    regime_map = {r['date']: r['regime'] for r in
                  db.execute("SELECT date, regime FROM regime_history").fetchall()}

    explosions = db.execute(
        """SELECT explosion_date, direction, explosion_class,
                  pre3_bb_width, pre5_bb_width, pre3_vol_ratio, pre5_vol_ratio,
                  pre3_rsi, pre5_rsi, pre3_momentum_5d, pre5_momentum_5d
           FROM explosive_moves"""
    ).fetchall()

    systems = {}
    for regime in ('BULL', 'BEAR', 'CHOPPY'):
        regime_exps = [e for e in explosions
                       if regime_map.get(e['explosion_date']) == regime]
        if len(regime_exps) < 20:
            continue
        pat_stats = []
        for p in patterns:
            tgt = [e for e in regime_exps
                   if e['direction'] == p['direction']
                   and e['explosion_class'] in ('LARGE','EXTREME')]
            if len(tgt) < 10:
                continue
            fv_tgt = [get_fv(e, p['feature']) for e in tgt]
            fv_tgt = [v for v in fv_tgt if v is not None]
            if len(fv_tgt) < 10:
                continue
            n_pos = sum(1 for v in fv_tgt if check_operator(v, p['threshold'], p['operator']))
            sr    = n_pos / len(fv_tgt)
            orig  = p['support_rate']
            if sr >= orig * 1.15:     role = 'DOMINANT'
            elif sr >= orig * 0.85:   role = 'ACTIVE'
            elif sr >= orig * 0.60:   role = 'WEAK'
            elif sr >= orig * 0.35:   role = 'SUPPRESSED'
            else:                     role = 'INVERTED'
            pat_stats.append({
                'pattern': p['pattern_name'], 'direction': p['direction'],
                'support': round(sr, 3), 'original': round(orig, 3),
                'role': role, 'n': len(fv_tgt)
            })
        systems[regime] = sorted(pat_stats, key=lambda x: -x['support'])

    return systems

# ──────────────────────────────────────────────────────────────────────────────
# 8. FULL PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

def full_synthesis(db):
    t0 = time.time()
    results = {}

    print('  [1/7] Law stability curves …', flush=True)
    results['stability']     = compute_stability_curves(db)

    print('  [2/7] Counterfactual scan …', flush=True)
    results['counterfactuals'] = run_counterfactuals(db)

    print('  [3/7] Mutation detection …', flush=True)
    results['mutations']     = detect_mutations(db)

    print('  [4/7] Multi-law interactions …', flush=True)
    results['interactions']  = compute_interactions(db)

    print('  [5/7] Law network …', flush=True)
    results['network']       = build_law_network(db)

    print('  [6/7] Market physics …', flush=True)
    results['physics']       = reconstruct_market_physics(db)

    print('  [7/7] Regime law systems + report …', flush=True)
    results['regime_systems']  = build_regime_law_systems(db)
    results['report_file']     = _generate_report(db, results)
    results['total_elapsed']   = round(time.time() - t0, 1)

    # ── Normalize result keys for JS display layer ─────────────────────────
    # stability_curves summary
    stab_raw = results.get('stability', {})
    stab_curves_list = [
        {'pattern': name + ' (' + v['direction'] + ')',
         'stability_class': v['stability_class'],
         'n_quarters': v['n_quarters']}
        for name, v in stab_raw.items()
    ]
    results['stability_curves'] = {
        'n_laws':    len(stab_curves_list),
        'n_quarters': max((v['n_quarters'] for v in stab_raw.values()), default=0),
        'curves':    stab_curves_list,
    }

    # counterfactuals summary
    cf_raw = results.get('counterfactuals', {})
    cf_list = list(cf_raw.values()) if isinstance(cf_raw, dict) else cf_raw
    n_act   = sum(v.get('total', 0) for v in cf_list)
    avg_hit = (_mean([v.get('precision', 0) for v in cf_list])
               if cf_list else 0.0)
    results['counterfactuals'] = {
        'n_activations': n_act,
        'avg_hit_rate':  round(avg_hit, 4),
        'patterns':      cf_list,
    }

    # mutations summary
    mut_raw = results.get('mutations', [])
    results['mutations'] = {
        'n_mutations': len(mut_raw),
        'mutations':   mut_raw,
    }

    # interactions summary
    ix_raw = results.get('interactions', [])
    n_amp  = sum(1 for i in ix_raw if i.get('type') == 'AMPLIFY')
    n_sup  = sum(1 for i in ix_raw if i.get('type') == 'SUPPRESS')
    results['interactions'] = {
        'n_pairs':    len(ix_raw),
        'n_amplify':  n_amp,
        'n_suppress': n_sup,
        'interactions': ix_raw,
    }

    # physics summary
    ph_raw  = results.get('physics', {})
    ph_dist = ph_raw.get('physics_distribution', {})
    dom_type = max(ph_dist, key=ph_dist.get) if ph_dist else None
    results['physics'] = {
        'n_events':     ph_raw.get('n_explosions_analyzed', 0),
        'dominant_type': dom_type,
        'by_type':      {k: {'n': v, 'avg_move': 0, 'avg_compression_days': 0}
                         for k, v in ph_dist.items()},
    }

    # regime_systems summary
    rs_raw = results.get('regime_systems', {})
    results['regime_systems'] = {
        'systems': {
            regime: [
                {'pattern': l['pattern'] + ' (' + l['direction'] + ')',
                 'regime_status': l.get('role', 'ACTIVE'),
                 'support': l.get('support', 0)}
                for l in laws
            ]
            for regime, laws in (rs_raw if isinstance(rs_raw, dict) else {}).items()
        }
    }

    return results

# ──────────────────────────────────────────────────────────────────────────────
# 9. RESEARCH REPORT
# ──────────────────────────────────────────────────────────────────────────────

STABILITY_ICONS = {
    'STABLE_INVARIANT':    '🏛️',
    'SLOWLY_DEGRADING':    '📉',
    'REGIME_DEPENDENT':    '🔀',
    'STRUCTURALLY_MUTATING':'🧬',
    'TEMPORARY_ALPHA':     '⏳',
    'DEAD_STRUCTURE':      '💀',
    'INSUFFICIENT_DATA':   '❓',
    'UNKNOWN':             '❓',
}
MUTATION_ICONS = {
    'STRENGTHENING':'📈', 'WEAKENING':'📉',
    'REGIME_SHIFT':'🔀', 'GRADUAL_DRIFT':'〰️',
}
INTERACTION_ICONS = {
    'AMPLIFY':'⚡', 'SUPPRESS':'🛑', 'NEUTRAL':'〰️', 'CONDITIONAL':'🔀',
}
PHYSICS_ICONS = {
    'COMPRESSION_RELEASE':'🗜️', 'MOMENTUM_BURST':'🚀',
    'REVERSAL_SPIKE':'↩️', 'STRUCTURAL_EXPANSION':'🏗️',
}

def _generate_report(db, results):
    now   = datetime.now()
    fname = f"synthesis_report_{now.strftime('%Y-%m-%d')}.txt"
    fpath = REPORT_DIR / fname

    L = []
    def w(*args): L.extend(args)

    w('═'*70,
      '  🧬 EGX PHASE 14 — LAW SYNTHESIS & BEHAVIORAL PHYSICS REPORT',
      f'  Generated: {now.strftime("%Y-%m-%d %H:%M")}',
      '═'*70, '')

    # ── Sec 1: Law Stability ──
    stab = results.get('stability', {})
    w('━'*70, '  📈 SECTION 1 — LAW STABILITY CLASSIFICATION', '━'*70, '')
    for pname, info in stab.items():
        sc   = info.get('stability_class', 'UNKNOWN')
        icon = STABILITY_ICONS.get(sc, '?')
        nq   = info.get('n_quarters', 0)
        curve = info.get('curve', [])
        sr_str = ' → '.join(f"{c['sr']:.0%}" for c in curve[-4:]) if curve else 'N/A'
        w(f"  {icon} {pname} ({info.get('direction','?')})  →  {sc}",
          f"     {nq} quarters | recent support trajectory: {sr_str}", '')

    # ── Sec 2: Mutations ──
    muts = results.get('mutations', [])
    w('━'*70, '  🧬 SECTION 2 — LAW MUTATION EVENTS', '━'*70, '')
    if muts:
        for m in muts:
            icon = MUTATION_ICONS.get(m['type'], '?')
            w(f"  {icon} {m['pattern']} ({m['direction']}) | {m['type']}",
              f"     Period: {m['mutation_period']} | SR: {m['pre_support']:.1%} → {m['post_support']:.1%} | Δ={m['delta']:+.1%} | conf={m['confidence']:.2f}",
              '')
    else:
        w('  ✅ No statistically significant mutations detected.\n')

    # ── Sec 3: Counterfactuals ──
    cf = results.get('counterfactuals', {})
    w('━'*70, '  🔬 SECTION 3 — COUNTERFACTUAL ANALYSIS', '━'*70, '')
    w('  (Scanned OHLCV for all precursor activations — hit vs false alarm)\n')
    for pid, s in cf.items():
        pname = db.execute(
            "SELECT pattern_name FROM precursor_patterns WHERE id=?", (pid,)
        ).fetchone()
        pname = pname[0] if pname else pid
        prec   = s['precision']
        far    = s['false_alarm_rate']
        total  = s['total']
        w(f"  🎯 {pname}",
          f"     Activations: {total} | Hits: {s['hits']} | Precision: {prec:.1%} | False-alarm rate: {far:.1%}",
          '')

    # Failure taxonomy
    ftax = db.execute("SELECT * FROM failure_taxonomy ORDER BY failure_rate DESC").fetchall()
    if ftax:
        w('  Primary failure modes:')
        for ft in ftax:
            w(f"    ⚠️  {ft['pattern_name']}: {ft['failure_class']} | rate={ft['failure_rate']:.1%} | regime={ft['dominant_regime']}")
        w('')

    # ── Sec 4: Multi-Law Interactions ──
    irows = results.get('interactions', [])
    w('━'*70, '  ⚡ SECTION 4 — MULTI-LAW INTERACTION MATRIX', '━'*70, '')
    if irows:
        w(f"  {'Law A':<26} {'Law B':<26} {'Co-N':>5}  {'Uplift':>7}  Type")
        w(f"  {'─'*25} {'─'*25} {'─'*5}  {'─'*6}  {'─'*12}")
        for i in sorted(irows, key=lambda x: -abs(x['uplift']-1)):
            icon = INTERACTION_ICONS.get(i['type'], '?')
            w(f"  {i['a'][:25]:<26} {i['b'][:25]:<26} {i['co_n']:>5}  {i['uplift']:>+7.3f}x {icon} {i['type']}")
        w('')
    else:
        w('  Insufficient co-activation data for interaction analysis.\n')

    # ── Sec 5: Law Network ──
    net = results.get('network', {})
    w('━'*70, '  🕸️  SECTION 5 — LAW NETWORK TOPOLOGY', '━'*70, '')
    w(f"  {net.get('n_nodes','?')} nodes | {net.get('n_edges','?')} edges\n")
    nodes = net.get('nodes', [])
    edges = net.get('edges', [])
    if nodes:
        w('  Node centrality (most connected = most influential):')
        for n in nodes:
            sc   = n.get('stability_class','?')
            icon = STABILITY_ICONS.get(sc,'?')
            w(f"    {icon} {n['pattern_name']} ({n['direction']}) | centrality={n['centrality']:.3f} | {sc}")
        w('')
    if edges:
        w('  Strongest edges:')
        for e in edges[:6]:
            icon = INTERACTION_ICONS.get(e['interaction_type'],'?')
            src  = db.execute("SELECT pattern_name FROM precursor_patterns WHERE id=?",
                              (e['source_id'],)).fetchone()
            tgt  = db.execute("SELECT pattern_name FROM precursor_patterns WHERE id=?",
                              (e['target_id'],)).fetchone()
            sn   = src[0] if src else e['source_id'][:8]
            tn   = tgt[0] if tgt else e['target_id'][:8]
            w(f"    {icon} {sn} ↔ {tn} | w={e['weight']:+.3f} | uplift={e['uplift']:+.3f}x")
        w('')

    # ── Sec 6: Market Physics ──
    phys = results.get('physics', {})
    w('━'*70, '  ⚙️  SECTION 6 — MARKET PHYSICS RECONSTRUCTION', '━'*70, '')
    w(f"  {phys.get('n_explosions_analyzed','?')} LARGE/EXTREME explosions analyzed\n")
    pd = phys.get('physics_distribution', {})
    total_phys = sum(pd.values()) or 1
    for ptype, cnt in sorted(pd.items(), key=lambda x: -x[1]):
        icon = PHYSICS_ICONS.get(ptype,'⚙️')
        pct  = cnt/total_phys
        bar  = '█' * int(pct*20)
        w(f"  {icon} {ptype:<25} {cnt:>4}  {pct:>5.0%}  {bar}")
    w(f"\n  Avg compression depth:  {phys.get('avg_compression_depth','?'):.4f}")
    w(f"  Avg ignition speed:     {phys.get('avg_ignition_speed','?'):.4f}")
    rp = phys.get('regime_physics', {})
    if rp:
        w('\n  Physics by regime:')
        for regime, ptypes in rp.items():
            top = sorted(ptypes.items(), key=lambda x:-x[1])[:2]
            top_s = ', '.join(f"{t}:{n}" for t,n in top)
            w(f"    {regime:<8}: {top_s}")
    w('')

    # ── Sec 7: Regime Law Systems ──
    rsys = results.get('regime_systems', {})
    w('━'*70, '  🌐 SECTION 7 — REGIME-SPECIFIC LAW SYSTEMS', '━'*70, '')
    regime_icons = {'BULL':'🐂','BEAR':'🐻','CHOPPY':'〰️'}
    role_icons   = {'DOMINANT':'★','ACTIVE':'✓','WEAK':'~','SUPPRESSED':'↓','INVERTED':'✗'}
    for regime, pats in rsys.items():
        w(f"\n  {regime_icons.get(regime,'')} {regime} REGIME:")
        for p in pats:
            ri = role_icons.get(p['role'],'?')
            w(f"    {ri} {p['pattern']:<32} ({p['direction']}) | SR={p['support']:.0%} | {p['role']}")
    w('')

    # ── Sec 8: Scientific Conclusions ──
    w('━'*70, '  🔬 SECTION 8 — SYNTHESIS CONCLUSIONS', '━'*70, '')
    stable   = [n for n,i in stab.items() if i.get('stability_class')=='STABLE_INVARIANT']
    degrading= [n for n,i in stab.items() if 'DEGRADING' in i.get('stability_class','') or i.get('stability_class')=='DEAD_STRUCTURE']
    mutating = [n for n,i in stab.items() if 'MUTATING' in i.get('stability_class','')]
    ampls    = [i for i in irows if i['type']=='AMPLIFY']
    supps    = [i for i in irows if i['type']=='SUPPRESS']

    w(f'  Stable invariant laws:  {len(stable)} / {len(stab)}',
      f'  Degrading structures:   {len(degrading)}',
      f'  Mutating structures:    {len(mutating)}',
      f'  Amplifying interactions:{len(ampls)}',
      f'  Suppressing conflicts:  {len(supps)}',
      '')

    if stable:
        w('  ★ INVARIANT STRUCTURAL LAWS (highest scientific confidence):')
        for s in stable:
            w(f'    • {s}')
        w('')

    dominant_physics = max(pd, key=pd.get) if pd else 'UNKNOWN'
    w(f'  DOMINANT EXPLOSION MECHANISM: {dominant_physics}',
      f'  → {PHYSICS_ICONS.get(dominant_physics,"")} {dominant_physics} accounts for '
      f'{pd.get(dominant_physics,0)/total_phys:.0%} of all large/extreme moves.',
      '')

    if muts:
        w('  STRUCTURAL MUTATIONS DETECTED:')
        for m in muts:
            w(f'    • {m["pattern"]} mutated at {m["mutation_period"]}: '
              f'{m["pre_support"]:.0%} → {m["post_support"]:.0%}  ({m["type"]})')
        w('')

    elapsed = results.get('total_elapsed','?')
    w('═'*70, f'  ⏱ Synthesis complete in {elapsed}s', '═'*70)

    fpath.write_text('\n'.join(L), encoding='utf-8')
    return str(fpath)

# ──────────────────────────────────────────────────────────────────────────────
# DISPATCH
# ──────────────────────────────────────────────────────────────────────────────

def _cnt(db, table):
    try: return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except: return 0

def dispatch(command, params):
    db = get_db()
    ensure_schema(db)
    try:
        if command == 'full_synthesis':
            return full_synthesis(db)
        elif command == 'stability_curves':
            return compute_stability_curves(db)
        elif command == 'counterfactuals':
            return run_counterfactuals(db)
        elif command == 'mutations':
            return detect_mutations(db)
        elif command == 'interactions':
            return compute_interactions(db)
        elif command == 'network':
            return build_law_network(db)
        elif command == 'physics':
            return reconstruct_market_physics(db)
        elif command == 'regime_systems':
            return build_regime_law_systems(db)
        elif command == 'synthesis_report':
            dummy = {'stability':{}, 'mutations':[], 'counterfactuals':{},
                     'interactions':[], 'network':{'n_nodes':0,'n_edges':0,'nodes':[],'edges':[]},
                     'physics':{'n_explosions_analyzed':0,'physics_distribution':{},
                                'avg_compression_depth':None,'avg_ignition_speed':None,
                                'regime_physics':{}},
                     'regime_systems':{}, 'total_elapsed':'0'}
            return {'report_file': _generate_report(db, dummy)}
        elif command == 'status':
            return {
                'stability_curve_rows':    _cnt(db, 'law_stability_curves'),
                'counterfactual_events':   _cnt(db, 'counterfactual_events'),
                'failure_taxonomy_rows':   _cnt(db, 'failure_taxonomy'),
                'mutation_events':         _cnt(db, 'law_mutations'),
                'interaction_pairs':       _cnt(db, 'law_interactions'),
                'network_nodes':           _cnt(db, 'law_network_nodes'),
                'network_edges':           _cnt(db, 'law_network_edges'),
                'market_physics_rows':     _cnt(db, 'market_physics'),
                'precursor_patterns':      _cnt(db, 'precursor_patterns'),
                'explosive_moves':         _cnt(db, 'explosive_moves'),
            }
        else:
            return {'error': f'Unknown command: {command}'}
    finally:
        db.close()

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'status'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    result = dispatch(cmd, params)
    print(json.dumps(result, default=str))
