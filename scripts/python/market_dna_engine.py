"""
market_dna_engine.py — Phase 25
EGX Market Intelligence: Market DNA Extended Analysis

Usage: python market_dna_engine.py <command> '<json_params>'
Commands: build_dna, detect_mutations, cluster_communities, get_profile,
          sector_dna_refresh
"""

import os
import sys
import json
import sqlite3
import datetime
import math

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS market_dna_extended (
        symbol TEXT PRIMARY KEY,
        sector TEXT,
        volatility_personality TEXT,
        liquidity_personality TEXT,
        trend_persistence TEXT,
        mean_reversion_tendency TEXT,
        contagion_sensitivity TEXT,
        macro_sensitivity TEXT,
        panic_profile TEXT,
        recovery_speed TEXT,
        dominant_cycle TEXT,
        regime_sensitivity TEXT,
        composite_archetype TEXT,
        dna_score REAL DEFAULT 0,
        percentile_rank REAL DEFAULT 50,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS dna_mutations (
        mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        mutation_date TEXT,
        dimension TEXT,
        old_value TEXT,
        new_value TEXT,
        mutation_significance REAL,
        mutation_cause TEXT,
        regime TEXT
    );
    CREATE TABLE IF NOT EXISTS dna_communities (
        community_id INTEGER PRIMARY KEY AUTOINCREMENT,
        community_name TEXT,
        archetype TEXT,
        member_symbols TEXT,
        centroid_features TEXT,
        n_members INTEGER DEFAULT 0,
        stability_score REAL DEFAULT 0,
        updated_at TEXT
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Pure Python math helpers
# ---------------------------------------------------------------------------

def pct_rank(val, series):
    """Percentile rank of val within series (0-100)."""
    if not series:
        return 50.0
    return 100.0 * sum(1 for x in series if x <= val) / len(series)


def safe_float(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def compute_returns(closes):
    """Daily log-ish returns from close series."""
    returns = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        curr = closes[i]
        if prev and prev != 0:
            returns.append((curr - prev) / prev)
    return returns


def compute_std(series):
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    var = sum((x - mean) ** 2 for x in series) / len(series)
    return var ** 0.5


def compute_autocorrelation(series, lag=1):
    """Lag-1 autocorrelation using Pearson formula."""
    n = len(series)
    if n <= lag + 1:
        return 0.0
    x = series[:-lag]
    y = series[lag:]
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    denom_x = (sum((xi - mx) ** 2 for xi in x)) ** 0.5
    denom_y = (sum((yi - my) ** 2 for yi in y)) ** 0.5
    denom = denom_x * denom_y
    if denom == 0:
        return 0.0
    return num / denom


def compute_mean_reversion(returns, threshold=0.05):
    """
    Fraction of times a large move (>threshold) is followed by a reversal.
    Returns probability of reversal.
    """
    reversals = 0
    total = 0
    for i in range(len(returns) - 1):
        if abs(returns[i]) > threshold:
            total += 1
            if returns[i] > 0 and returns[i + 1] < 0:
                reversals += 1
            elif returns[i] < 0 and returns[i + 1] > 0:
                reversals += 1
    if total == 0:
        return 0.5  # neutral
    return reversals / total


def classify_volatility(vol_std):
    """Classify volatility personality."""
    if vol_std < 0.015:
        return 'CALM'
    elif vol_std < 0.025:
        return 'MODERATE'
    elif vol_std < 0.040:
        return 'EXPLOSIVE'
    else:
        return 'ERRATIC'


def classify_liquidity(avg_vol, median_vol):
    """Classify liquidity personality."""
    if median_vol == 0:
        return 'ILLIQUID'
    ratio = avg_vol / median_vol
    if ratio > 2.0:
        return 'DEEP'
    elif ratio >= 0.5:
        return 'MODERATE'
    elif ratio >= 0.1:
        return 'THIN'
    else:
        return 'ILLIQUID'


def classify_trend_persistence(autocorr):
    """Classify trend persistence from lag-1 autocorrelation."""
    if autocorr > 0.2:
        return 'STRONG'
    elif autocorr > 0.05:
        return 'MODERATE'
    else:
        return 'WEAK'


def classify_mean_reversion(mr_prob):
    """Classify mean reversion tendency."""
    if mr_prob > 0.6:
        return 'STRONG'
    elif mr_prob >= 0.4:
        return 'MODERATE'
    else:
        return 'WEAK'


def classify_panic_profile(fbr_pct):
    """Classify panic profile from false breakout rate."""
    if fbr_pct > 40:
        return 'SEVERE'
    elif fbr_pct > 20:
        return 'MODERATE'
    else:
        return 'MILD'


def classify_recovery_speed(avg_return_3d, avg_return_1d):
    """
    Recovery speed: how quickly does the stock recover after a move?
    Ratio of 3d gain to 1d gain.
    """
    if avg_return_1d == 0 or avg_return_1d is None:
        return 'UNKNOWN'
    ratio = safe_float(avg_return_3d) / safe_float(avg_return_1d) if avg_return_1d else 0
    if ratio > 1.5:
        return 'RAPID'
    elif ratio > 1.0:
        return 'MODERATE'
    elif ratio > 0.5:
        return 'SLOW'
    else:
        return 'STALLED'


def estimate_dominant_cycle(closes):
    """
    Estimate dominant cycle using zero-crossing count.
    Fewer crossings of mean = longer cycle.
    """
    if len(closes) < 20:
        return 'UNKNOWN'
    mean_close = sum(closes) / len(closes)
    crossings = 0
    for i in range(1, len(closes)):
        prev_above = closes[i - 1] >= mean_close
        curr_above = closes[i] >= mean_close
        if prev_above != curr_above:
            crossings += 1
    if crossings == 0:
        return 'TRENDING'
    half_period = len(closes) / max(1, crossings / 2)
    if half_period > 30:
        return 'LONG_40D+'
    elif half_period > 15:
        return 'MEDIUM_20D'
    elif half_period > 7:
        return 'SHORT_10D'
    else:
        return 'MICRO_5D'


def classify_regime_sensitivity(returns, regime_history):
    """
    Simplified: compare return volatility in bull vs bear periods.
    Returns: HIGH / MODERATE / LOW.
    """
    if not regime_history or not returns:
        return 'MODERATE'
    # Without exact date alignment per bar, use proxy:
    # If std of returns is high, likely sensitive to regime changes
    std = compute_std(returns)
    if std > 0.03:
        return 'HIGH'
    elif std > 0.015:
        return 'MODERATE'
    else:
        return 'LOW'


def compute_dna_score(dims):
    """
    Numeric DNA score from qualitative dimensions.
    Higher = more explosive/active.
    """
    score = 50.0

    vol_map = {'CALM': -15, 'MODERATE': 0, 'EXPLOSIVE': 15, 'ERRATIC': 5}
    liq_map = {'ILLIQUID': -10, 'THIN': -5, 'MODERATE': 0, 'DEEP': 10}
    trend_map = {'WEAK': -5, 'MODERATE': 0, 'STRONG': 10}
    mr_map = {'WEAK': -5, 'MODERATE': 0, 'STRONG': 5}
    panic_map = {'MILD': 5, 'MODERATE': 0, 'SEVERE': -10}
    recovery_map = {'STALLED': -10, 'SLOW': -5, 'MODERATE': 0, 'RAPID': 10, 'UNKNOWN': 0}
    regime_map = {'LOW': -5, 'MODERATE': 0, 'HIGH': 5}

    score += vol_map.get(dims.get('volatility_personality', 'MODERATE'), 0)
    score += liq_map.get(dims.get('liquidity_personality', 'MODERATE'), 0)
    score += trend_map.get(dims.get('trend_persistence', 'MODERATE'), 0)
    score += mr_map.get(dims.get('mean_reversion_tendency', 'MODERATE'), 0)
    score += panic_map.get(dims.get('panic_profile', 'MODERATE'), 0)
    score += recovery_map.get(dims.get('recovery_speed', 'MODERATE'), 0)
    score += regime_map.get(dims.get('regime_sensitivity', 'MODERATE'), 0)

    return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# Composite archetype using percentile ranking
# ---------------------------------------------------------------------------

def assign_composite_archetype(expl_pct, fbr_pct, vol_pct, trend_pct, mr_pct):
    """Assign composite archetype based on relative percentile rankings."""
    if expl_pct >= 75 and fbr_pct <= 25:
        return 'PRECISION_EXPLODER'
    elif expl_pct >= 75 and fbr_pct >= 75:
        return 'VOLATILE_SPECULATOR'
    elif expl_pct <= 25 and vol_pct <= 25:
        return 'DEFENSIVE_STABLE'
    elif trend_pct >= 75:
        return 'MOMENTUM_LEADER'
    elif mr_pct >= 75:
        return 'MEAN_REVERTER'
    else:
        return 'BALANCED_PERFORMER'


# ---------------------------------------------------------------------------
# Command: build_dna
# ---------------------------------------------------------------------------

def build_dna(params):
    db = get_db()
    try:
        force = params.get('force', False)
        now_str = datetime.datetime.utcnow().isoformat()

        symbols = db.execute(
            "SELECT symbol, sector FROM stock_universe WHERE status='fetched'"
        ).fetchall()

        # Load existing DNA for comparison
        old_dna = {}
        try:
            old_rows = db.execute("SELECT * FROM market_dna_extended").fetchall()
            for r in old_rows:
                old_dna[r['symbol']] = dict(r)
        except Exception:
            pass

        # Load stock_dna for explosion metrics
        dna_map = {}
        try:
            dna_rows = db.execute(
                "SELECT symbol, sector, explosion_count, explosion_rate_pct, "
                "false_breakout_rate_pct, cycle_period_days, avg_return_1d, "
                "avg_return_3d, avg_return_5d, archetype FROM stock_dna"
            ).fetchall()
            for d in dna_rows:
                dna_map[d['symbol']] = dict(d)
        except Exception:
            pass

        # Load behavioral memory
        mem_map = {}
        try:
            mem_rows = db.execute(
                "SELECT symbol, large_explosion_count, explosion_rate_per_100, "
                "false_signal_rate, dominant_precursor, best_precursor_sr "
                "FROM stock_behavioral_memory"
            ).fetchall()
            for m in mem_rows:
                mem_map[m['symbol']] = dict(m)
        except Exception:
            pass

        # Load regime history
        regime_history = []
        try:
            rh = db.execute("SELECT date, regime FROM regime_history ORDER BY date").fetchall()
            regime_history = [dict(r) for r in rh]
        except Exception:
            pass

        # Get sector contagion
        contagion_sectors = set()
        try:
            ct = db.execute("SELECT DISTINCT sector FROM sector_contagion").fetchall()
            contagion_sectors = {r['sector'] for r in ct if r['sector']}
        except Exception:
            pass

        # --- Pass 1: compute raw metrics ---
        raw_metrics = {}

        for sym_row in symbols:
            symbol = sym_row['symbol']
            sector = sym_row['sector'] or 'Unknown'

            bars = db.execute(
                "SELECT bar_time, close, volume FROM ohlcv_history "
                "WHERE symbol=? ORDER BY bar_time DESC LIMIT 100",
                (symbol,)
            ).fetchall()
            bars = list(reversed(bars))

            if len(bars) < 20:
                continue

            closes = [safe_float(b['close']) for b in bars]
            volumes = [safe_float(b['volume']) for b in bars]

            returns = compute_returns(closes)
            if not returns:
                continue

            vol_std = compute_std(returns[-20:]) if len(returns) >= 20 else compute_std(returns)
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            sorted_vol = sorted(volumes)
            median_vol = sorted_vol[len(sorted_vol) // 2] if sorted_vol else 0
            autocorr = compute_autocorrelation(returns, lag=1)
            mr_prob = compute_mean_reversion(returns, threshold=0.05)

            dna = dna_map.get(symbol, {})
            mem = mem_map.get(symbol, {})

            fbr_pct = safe_float(dna.get('false_breakout_rate_pct', 0))
            explosion_rate = safe_float(dna.get('explosion_rate_pct', 0))
            avg_r1 = safe_float(dna.get('avg_return_1d', 0))
            avg_r3 = safe_float(dna.get('avg_return_3d', 0))
            cycle_days = safe_float(dna.get('cycle_period_days', 0))

            contagion_sens = 'HIGH' if sector in contagion_sectors else 'MODERATE'

            raw_metrics[symbol] = {
                'sector': sector,
                'vol_std': vol_std,
                'avg_vol': avg_vol,
                'median_vol': median_vol,
                'autocorr': autocorr,
                'mr_prob': mr_prob,
                'fbr_pct': fbr_pct,
                'explosion_rate': explosion_rate,
                'avg_r1': avg_r1,
                'avg_r3': avg_r3,
                'cycle_days': cycle_days,
                'returns': returns,
                'closes': closes,
                'contagion_sens': contagion_sens
            }

        if not raw_metrics:
            return {'error': 'No symbols with sufficient data', 'n_built': 0}

        # --- Pass 2: compute relative percentiles ---
        all_symbols = list(raw_metrics.keys())
        expl_rates = [raw_metrics[s]['explosion_rate'] for s in all_symbols]
        fbr_rates = [raw_metrics[s]['fbr_pct'] for s in all_symbols]
        vol_stds = [raw_metrics[s]['vol_std'] for s in all_symbols]
        autocorrs = [raw_metrics[s]['autocorr'] for s in all_symbols]
        mr_probs = [raw_metrics[s]['mr_prob'] for s in all_symbols]

        archetype_dist = {}
        n_built = 0
        vs_old_dna = {'changed': 0, 'unchanged': 0, 'new': 0}

        for symbol, m in raw_metrics.items():
            vol_std = m['vol_std']
            avg_vol = m['avg_vol']
            median_vol = m['median_vol']
            autocorr = m['autocorr']
            mr_prob = m['mr_prob']
            fbr_pct = m['fbr_pct']
            returns = m['returns']

            # Personality classifications
            vol_pers = classify_volatility(vol_std)
            liq_pers = classify_liquidity(avg_vol, median_vol)
            trend_pers = classify_trend_persistence(autocorr)
            mr_pers = classify_mean_reversion(mr_prob)
            panic_prof = classify_panic_profile(fbr_pct)
            recovery = classify_recovery_speed(m['avg_r3'], m['avg_r1'])
            dom_cycle = estimate_dominant_cycle(m['closes'])
            regime_sens = classify_regime_sensitivity(returns, regime_history)
            contagion_sens = m['contagion_sens']

            # Percentile ranks (relative to all symbols)
            expl_pct = pct_rank(m['explosion_rate'], expl_rates)
            fbr_pct_rank = pct_rank(fbr_pct, fbr_rates)
            vol_pct = pct_rank(vol_std, vol_stds)
            trend_pct = pct_rank(autocorr, autocorrs)
            mr_pct = pct_rank(mr_prob, mr_probs)

            # Composite archetype
            composite = assign_composite_archetype(expl_pct, fbr_pct_rank, vol_pct, trend_pct, mr_pct)

            # DNA dimensions dict for score
            dims = {
                'volatility_personality': vol_pers,
                'liquidity_personality': liq_pers,
                'trend_persistence': trend_pers,
                'mean_reversion_tendency': mr_pers,
                'panic_profile': panic_prof,
                'recovery_speed': recovery,
                'regime_sensitivity': regime_sens
            }
            dna_score = compute_dna_score(dims)

            # Overall percentile rank = percentile of DNA score (computed post-loop)
            # Store for now; we'll update after loop
            macro_sens = 'HIGH' if regime_sens == 'HIGH' else 'MODERATE' if regime_sens == 'MODERATE' else 'LOW'

            # Track vs old
            old = old_dna.get(symbol)
            if old is None:
                vs_old_dna['new'] += 1
            elif old.get('composite_archetype') != composite:
                vs_old_dna['changed'] += 1
            else:
                vs_old_dna['unchanged'] += 1

            db.execute("""
                INSERT OR REPLACE INTO market_dna_extended
                (symbol, sector, volatility_personality, liquidity_personality,
                 trend_persistence, mean_reversion_tendency, contagion_sensitivity,
                 macro_sensitivity, panic_profile, recovery_speed, dominant_cycle,
                 regime_sensitivity, composite_archetype, dna_score, percentile_rank, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, m['sector'],
                vol_pers, liq_pers, trend_pers, mr_pers,
                contagion_sens, macro_sens, panic_prof, recovery,
                dom_cycle, regime_sens, composite,
                round(dna_score, 2), 50.0, now_str  # percentile_rank updated below
            ))

            archetype_dist[composite] = archetype_dist.get(composite, 0) + 1
            n_built += 1

        db.commit()

        # --- Pass 3: update percentile ranks ---
        scores_all = db.execute(
            "SELECT symbol, dna_score FROM market_dna_extended"
        ).fetchall()
        all_scores = [safe_float(r['dna_score']) for r in scores_all]

        for r in scores_all:
            sym = r['symbol']
            score = safe_float(r['dna_score'])
            pct = pct_rank(score, all_scores)
            db.execute(
                "UPDATE market_dna_extended SET percentile_rank=? WHERE symbol=?",
                (round(pct, 1), sym)
            )
        db.commit()

        return {
            'n_built': n_built,
            'archetype_distribution': archetype_dist,
            'vs_old_dna': vs_old_dna
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: detect_mutations
# ---------------------------------------------------------------------------

def detect_mutations(params):
    db = get_db()
    try:
        now_str = datetime.datetime.utcnow().isoformat()
        today = datetime.date.today().isoformat()

        # Get current DNA
        current = db.execute("SELECT * FROM market_dna_extended").fetchall()
        current_map = {r['symbol']: dict(r) for r in current}

        # Get last mutation for each symbol (to compare as "old" state)
        last_mutations = db.execute(
            "SELECT symbol, dimension, new_value FROM dna_mutations "
            "WHERE mutation_id IN ("
            "  SELECT MAX(mutation_id) FROM dna_mutations GROUP BY symbol, dimension"
            ")"
        ).fetchall()

        # Build "old values" map from last mutations
        old_map = {}
        for m in last_mutations:
            sym = m['symbol']
            if sym not in old_map:
                old_map[sym] = {}
            old_map[sym][m['dimension']] = m['new_value']

        # Dimensions to track
        tracked_dims = [
            'volatility_personality', 'liquidity_personality', 'trend_persistence',
            'mean_reversion_tendency', 'contagion_sensitivity', 'panic_profile',
            'recovery_speed', 'regime_sensitivity', 'composite_archetype'
        ]

        # Tier order for significance
        tier_orders = {
            'volatility_personality': ['CALM', 'MODERATE', 'EXPLOSIVE', 'ERRATIC'],
            'liquidity_personality': ['ILLIQUID', 'THIN', 'MODERATE', 'DEEP'],
            'trend_persistence': ['WEAK', 'MODERATE', 'STRONG'],
            'mean_reversion_tendency': ['WEAK', 'MODERATE', 'STRONG'],
            'panic_profile': ['MILD', 'MODERATE', 'SEVERE'],
            'recovery_speed': ['STALLED', 'SLOW', 'MODERATE', 'RAPID'],
            'contagion_sensitivity': ['LOW', 'MODERATE', 'HIGH'],
            'regime_sensitivity': ['LOW', 'MODERATE', 'HIGH'],
            'composite_archetype': []
        }

        def tier_distance(dim, old_val, new_val):
            """Distance in tier steps between two values."""
            tiers = tier_orders.get(dim, [])
            if not tiers:
                return 1.0 if old_val != new_val else 0.0
            try:
                old_idx = tiers.index(old_val)
                new_idx = tiers.index(new_val)
                max_dist = len(tiers) - 1
                return abs(new_idx - old_idx) / max_dist if max_dist > 0 else 0.0
            except ValueError:
                return 0.5

        n_mutations = 0
        mutation_types = {}
        symbol_change_count = {}

        # Get current regime
        regime_row = db.execute(
            "SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
        current_regime = regime_row['regime'] if regime_row else 'UNKNOWN'

        for symbol, new_dna in current_map.items():
            old_dims = old_map.get(symbol, {})

            for dim in tracked_dims:
                new_val = new_dna.get(dim)
                if new_val is None:
                    continue

                old_val = old_dims.get(dim)
                if old_val is None:
                    # No prior record — skip (treated as new in build_dna)
                    continue

                if old_val == new_val:
                    continue

                dist = tier_distance(dim, old_val, new_val)
                if dist < 0.3:
                    continue  # Minor shift, not a mutation

                # Determine cause
                if dim == 'composite_archetype':
                    cause = 'ARCHETYPE_SHIFT'
                elif 'volatility' in dim:
                    cause = 'VOLATILITY_REGIME_CHANGE'
                elif 'panic' in dim or 'recovery' in dim:
                    cause = 'BEHAVIORAL_SHIFT'
                elif 'trend' in dim or 'reversion' in dim:
                    cause = 'MOMENTUM_SHIFT'
                else:
                    cause = 'STRUCTURAL_CHANGE'

                db.execute("""
                    INSERT INTO dna_mutations
                    (symbol, mutation_date, dimension, old_value, new_value,
                     mutation_significance, mutation_cause, regime)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    symbol, today, dim, old_val, new_val,
                    round(dist, 3), cause, current_regime
                ))

                n_mutations += 1
                mutation_types[cause] = mutation_types.get(cause, 0) + 1
                symbol_change_count[symbol] = symbol_change_count.get(symbol, 0) + 1

        db.commit()

        # Most changed symbols
        most_changed = sorted(
            symbol_change_count.items(),
            key=lambda kv: kv[1],
            reverse=True
        )[:10]

        return {
            'n_mutations': n_mutations,
            'mutation_types': mutation_types,
            'most_changed_symbols': [
                {'symbol': s, 'n_changes': c} for s, c in most_changed
            ],
            'mutation_date': today,
            'regime_at_mutation': current_regime
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# K-means style manual clustering (pure Python)
# ---------------------------------------------------------------------------

def vectorize_dna(dna_row):
    """Convert DNA row to numeric vector for clustering."""
    vol_map = {'CALM': 0, 'MODERATE': 1, 'EXPLOSIVE': 2, 'ERRATIC': 3}
    liq_map = {'ILLIQUID': 0, 'THIN': 1, 'MODERATE': 2, 'DEEP': 3}
    trend_map = {'WEAK': 0, 'MODERATE': 1, 'STRONG': 2}
    mr_map = {'WEAK': 0, 'MODERATE': 1, 'STRONG': 2}
    panic_map = {'MILD': 0, 'MODERATE': 1, 'SEVERE': 2}
    recovery_map = {'STALLED': 0, 'SLOW': 1, 'MODERATE': 2, 'RAPID': 3, 'UNKNOWN': 1}
    regime_map = {'LOW': 0, 'MODERATE': 1, 'HIGH': 2}
    contagion_map = {'LOW': 0, 'MODERATE': 1, 'HIGH': 2}

    return [
        vol_map.get(dna_row.get('volatility_personality', 'MODERATE'), 1),
        liq_map.get(dna_row.get('liquidity_personality', 'MODERATE'), 2),
        trend_map.get(dna_row.get('trend_persistence', 'MODERATE'), 1),
        mr_map.get(dna_row.get('mean_reversion_tendency', 'MODERATE'), 1),
        panic_map.get(dna_row.get('panic_profile', 'MODERATE'), 1),
        recovery_map.get(dna_row.get('recovery_speed', 'MODERATE'), 2),
        regime_map.get(dna_row.get('regime_sensitivity', 'MODERATE'), 1),
        contagion_map.get(dna_row.get('contagion_sensitivity', 'MODERATE'), 1),
        safe_float(dna_row.get('dna_score', 50)) / 100.0  # normalize
    ]


def euclidean_distance(v1, v2):
    return sum((a - b) ** 2 for a, b in zip(v1, v2)) ** 0.5


def kmeans_manual(data_vectors, symbols, k=8, max_iter=30):
    """
    Pure Python k-means clustering.
    Returns: list of (cluster_id -> [symbol]) dicts.
    """
    if not data_vectors or k <= 0:
        return []

    n = len(data_vectors)
    k = min(k, n)

    # Initialize centroids from evenly-spaced data points
    step = max(1, n // k)
    centroids = [list(data_vectors[i * step]) for i in range(k)]

    assignments = [0] * n

    for iteration in range(max_iter):
        # Assign each point to nearest centroid
        changed = False
        for i, vec in enumerate(data_vectors):
            dists = [euclidean_distance(vec, c) for c in centroids]
            new_assign = dists.index(min(dists))
            if new_assign != assignments[i]:
                assignments[i] = new_assign
                changed = True

        if not changed:
            break

        # Update centroids
        new_centroids = []
        for c_id in range(k):
            members = [data_vectors[i] for i in range(n) if assignments[i] == c_id]
            if members:
                dim = len(members[0])
                centroid = [sum(m[d] for m in members) / len(members) for d in range(dim)]
                new_centroids.append(centroid)
            else:
                new_centroids.append(list(centroids[c_id]))
        centroids = new_centroids

    # Group symbols by cluster
    clusters = {}
    for i, sym in enumerate(symbols):
        c_id = assignments[i]
        if c_id not in clusters:
            clusters[c_id] = []
        clusters[c_id].append(sym)

    return clusters, centroids, assignments


def name_community(members_dna, centroid):
    """Name a community based on dominant DNA features."""
    vol_levels = {'CALM': 0, 'MODERATE': 1, 'EXPLOSIVE': 2, 'ERRATIC': 3}
    vol_counts = {}
    arch_counts = {}
    for d in members_dna:
        v = d.get('volatility_personality', 'MODERATE')
        a = d.get('composite_archetype', 'BALANCED_PERFORMER')
        vol_counts[v] = vol_counts.get(v, 0) + 1
        arch_counts[a] = arch_counts.get(a, 0) + 1

    dom_vol = max(vol_counts, key=vol_counts.get) if vol_counts else 'MODERATE'
    dom_arch = max(arch_counts, key=arch_counts.get) if arch_counts else 'BALANCED_PERFORMER'

    names = {
        'PRECISION_EXPLODER': 'EXPLOSION_CLUSTER',
        'VOLATILE_SPECULATOR': 'SPECULATIVE_CLUSTER',
        'DEFENSIVE_STABLE': 'DEFENSIVE_CLUSTER',
        'MOMENTUM_LEADER': 'MOMENTUM_CLUSTER',
        'MEAN_REVERTER': 'OSCILLATOR_CLUSTER',
        'BALANCED_PERFORMER': 'CORE_CLUSTER'
    }
    base = names.get(dom_arch, 'MIXED_CLUSTER')

    vol_prefix = {
        'CALM': 'QUIET_',
        'EXPLOSIVE': 'HIGH_ENERGY_',
        'ERRATIC': 'UNSTABLE_',
        'MODERATE': ''
    }.get(dom_vol, '')

    return vol_prefix + base


# ---------------------------------------------------------------------------
# Command: cluster_communities
# ---------------------------------------------------------------------------

def cluster_communities(params):
    db = get_db()
    try:
        n_clusters = params.get('n_clusters', 8)
        now_str = datetime.datetime.utcnow().isoformat()

        dna_rows = db.execute("SELECT * FROM market_dna_extended").fetchall()
        if not dna_rows:
            return {'error': 'No DNA data. Run build_dna first.', 'n_communities': 0}

        dna_list = [dict(r) for r in dna_rows]
        symbols = [d['symbol'] for d in dna_list]
        vectors = [vectorize_dna(d) for d in dna_list]

        clusters, centroids, assignments = kmeans_manual(vectors, symbols, k=n_clusters)

        # Build dna lookup
        dna_lookup = {d['symbol']: d for d in dna_list}

        # Clear old communities
        db.execute("DELETE FROM dna_communities")
        db.commit()

        community_profiles = []
        for c_id, members in clusters.items():
            members_dna = [dna_lookup[s] for s in members if s in dna_lookup]
            centroid = centroids[c_id]
            community_name = name_community(members_dna, centroid)

            # Dominant archetype
            arch_counts = {}
            for d in members_dna:
                a = d.get('composite_archetype', 'BALANCED_PERFORMER')
                arch_counts[a] = arch_counts.get(a, 0) + 1
            dom_arch = max(arch_counts, key=arch_counts.get) if arch_counts else 'BALANCED_PERFORMER'

            # Stability: inverse of intra-cluster variance
            if len(members) > 1:
                member_vecs = [vectors[symbols.index(s)] for s in members if s in symbols]
                centroid_vec = centroids[c_id]
                avg_dist = sum(euclidean_distance(v, centroid_vec) for v in member_vecs) / len(member_vecs)
                stability = max(0.0, min(1.0, 1.0 - avg_dist / 5.0))
            else:
                stability = 1.0

            centroid_feats = json.dumps({
                f'dim_{i}': round(centroid[i], 3) for i in range(len(centroid))
            })

            db.execute("""
                INSERT INTO dna_communities
                (community_name, archetype, member_symbols, centroid_features,
                 n_members, stability_score, updated_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                community_name, dom_arch,
                json.dumps(members),
                centroid_feats,
                len(members),
                round(stability, 3),
                now_str
            ))

            community_profiles.append({
                'community_id': c_id,
                'community_name': community_name,
                'archetype': dom_arch,
                'n_members': len(members),
                'stability_score': round(stability, 3),
                'sample_members': members[:5]
            })

        db.commit()

        return {
            'n_communities': len(clusters),
            'n_symbols_clustered': len(symbols),
            'community_profiles': sorted(community_profiles, key=lambda x: x['n_members'], reverse=True)
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: get_profile
# ---------------------------------------------------------------------------

def get_profile(params):
    db = get_db()
    try:
        symbol = params.get('symbol', '')
        if not symbol:
            return {'error': 'symbol parameter required'}

        # Core DNA
        dna = db.execute(
            "SELECT * FROM market_dna_extended WHERE symbol=?", (symbol,)
        ).fetchone()

        if not dna:
            return {'error': f'No DNA profile for {symbol}. Run build_dna first.'}

        dna_dict = dict(dna)

        # Community membership
        communities = db.execute("SELECT * FROM dna_communities").fetchall()
        member_community = None
        for comm in communities:
            try:
                members = json.loads(comm['member_symbols'] or '[]')
                if symbol in members:
                    member_community = {
                        'community_name': comm['community_name'],
                        'archetype': comm['archetype'],
                        'n_members': comm['n_members'],
                        'stability_score': comm['stability_score']
                    }
                    break
            except Exception:
                pass

        # Historical mutations
        mutations = db.execute(
            "SELECT mutation_date, dimension, old_value, new_value, "
            "mutation_significance, mutation_cause, regime "
            "FROM dna_mutations WHERE symbol=? ORDER BY mutation_date DESC LIMIT 20",
            (symbol,)
        ).fetchall()
        mutation_list = [dict(m) for m in mutations]

        # Stock DNA context
        stock_dna = None
        try:
            sd = db.execute(
                "SELECT * FROM stock_dna WHERE symbol=?", (symbol,)
            ).fetchone()
            stock_dna = dict(sd) if sd else None
        except Exception:
            pass

        # Percentile context
        all_dna_scores = [safe_float(r['dna_score']) for r in db.execute(
            "SELECT dna_score FROM market_dna_extended"
        ).fetchall()]
        score_pct = pct_rank(safe_float(dna_dict.get('dna_score', 50)), all_dna_scores)

        # Summary
        profile = {
            'symbol': symbol,
            'sector': dna_dict.get('sector'),
            'composite_archetype': dna_dict.get('composite_archetype'),
            'dna_score': dna_dict.get('dna_score'),
            'percentile_rank': round(score_pct, 1),
            'updated_at': dna_dict.get('updated_at'),
            'dimensions': {
                'volatility_personality': dna_dict.get('volatility_personality'),
                'liquidity_personality': dna_dict.get('liquidity_personality'),
                'trend_persistence': dna_dict.get('trend_persistence'),
                'mean_reversion_tendency': dna_dict.get('mean_reversion_tendency'),
                'contagion_sensitivity': dna_dict.get('contagion_sensitivity'),
                'macro_sensitivity': dna_dict.get('macro_sensitivity'),
                'panic_profile': dna_dict.get('panic_profile'),
                'recovery_speed': dna_dict.get('recovery_speed'),
                'dominant_cycle': dna_dict.get('dominant_cycle'),
                'regime_sensitivity': dna_dict.get('regime_sensitivity')
            },
            'community': member_community,
            'n_mutations': len(mutation_list),
            'recent_mutations': mutation_list[:5],
            'stock_dna_context': stock_dna
        }

        return profile
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: sector_dna_refresh
# ---------------------------------------------------------------------------

def sector_dna_refresh(params):
    db = get_db()
    try:
        # Load extended DNA
        dna_rows = db.execute(
            "SELECT symbol, sector, composite_archetype, dna_score, "
            "volatility_personality, trend_persistence, panic_profile "
            "FROM market_dna_extended"
        ).fetchall()

        if not dna_rows:
            return {'error': 'No DNA data. Run build_dna first.'}

        # Group by sector
        sector_data = {}
        for r in dna_rows:
            sector = r['sector'] or 'Unknown'
            if sector not in sector_data:
                sector_data[sector] = {
                    'symbols': [],
                    'archetypes': [],
                    'dna_scores': [],
                    'vol_pers': [],
                    'trend_pers': [],
                    'panic_profs': []
                }
            sector_data[sector]['symbols'].append(r['symbol'])
            sector_data[sector]['archetypes'].append(r['composite_archetype'])
            sector_data[sector]['dna_scores'].append(safe_float(r['dna_score']))
            sector_data[sector]['vol_pers'].append(r['volatility_personality'])
            sector_data[sector]['trend_pers'].append(r['trend_persistence'])
            sector_data[sector]['panic_profs'].append(r['panic_profile'])

        # Global score series for percentile ranking
        all_sector_scores = []
        for sector, data in sector_data.items():
            avg_score = sum(data['dna_scores']) / len(data['dna_scores']) if data['dna_scores'] else 0
            all_sector_scores.append(avg_score)

        sectors_updated = 0
        archetype_dist = {}

        for sector, data in sector_data.items():
            n = len(data['symbols'])
            if n == 0:
                continue

            avg_score = sum(data['dna_scores']) / n if n else 50
            score_pct = pct_rank(avg_score, all_sector_scores)

            # Dominant archetype
            arch_counts = {}
            for a in data['archetypes']:
                if a:
                    arch_counts[a] = arch_counts.get(a, 0) + 1
            dom_arch = max(arch_counts, key=arch_counts.get) if arch_counts else 'BALANCED_PERFORMER'

            # Relative percentile-based sector archetype
            all_expl_rates = [safe_float(r['dna_score']) for r in dna_rows]
            # Explosion rate proxy: count PRECISION_EXPLODER / VOLATILE_SPECULATOR
            expl_count = sum(1 for a in data['archetypes']
                             if a in ('PRECISION_EXPLODER', 'VOLATILE_SPECULATOR'))
            fbr_count = sum(1 for a in data['archetypes']
                            if a in ('VOLATILE_SPECULATOR',))

            expl_pct = pct_rank(expl_count / n, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
            fbr_pct_rank = pct_rank(fbr_count / n, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
            vol_pct = score_pct  # proxy
            trend_pct = sum(1 for t in data['trend_pers'] if t == 'STRONG') / n * 100
            mr_pct = sum(1 for v in data['vol_pers'] if v in ('MODERATE', 'CALM')) / n * 100

            sector_archetype = assign_composite_archetype(expl_pct, fbr_pct_rank, vol_pct, trend_pct, mr_pct)

            # Try to update sector_dna table if it exists
            try:
                db.execute(
                    "UPDATE stock_dna SET archetype=? WHERE sector=?",
                    (sector_archetype, sector)
                )
            except Exception:
                pass

            archetype_dist[sector_archetype] = archetype_dist.get(sector_archetype, 0) + 1
            sectors_updated += 1

        db.commit()

        return {
            'sectors_updated': sectors_updated,
            'archetype_distribution': archetype_dist,
            'method': 'relative_percentile_ranking',
            'sectors_analyzed': list(sector_data.keys())
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'build_dna': build_dna,
    'detect_mutations': detect_mutations,
    'cluster_communities': cluster_communities,
    'get_profile': get_profile,
    'sector_dna_refresh': sector_dna_refresh,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: python market_dna_engine.py <command> [json_params]'}))
        sys.exit(1)

    command = sys.argv[1]
    params = {}
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({'error': f'Invalid JSON params: {e}'}))
            sys.exit(1)

    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys())
        }))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            'error': str(e),
            'traceback': traceback.format_exc()
        }, default=str))
        sys.exit(1)


if __name__ == '__main__':
    main()
