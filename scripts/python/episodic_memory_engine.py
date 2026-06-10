"""
episodic_memory_engine.py — Phase 30
EGX Autonomous Quant System: Episodic Market Memory Engine

Encodes historical market periods into fingerprints, finds which past episodes
are most similar to today, and reports what happened after those similar periods.

Commands:
  encode_episodes   — scan OHLCV history, encode rolling 20-day windows as episodes
  find_similar      — find episodes most similar to the current period
  analogy_report    — human-readable analogy report (Arabic narrative)
  get_episode       — retrieve a specific episode by ID
  build_full        — encode_episodes + find_similar + analogy_report

Usage:
  python episodic_memory_engine.py <command> '<json_params>'

Example:
  python episodic_memory_engine.py encode_episodes '{}'
  python episodic_memory_engine.py find_similar '{"top_k": 5}'
  python episodic_memory_engine.py get_episode '{"episode_id": "2022-05-01_2022-05-20"}'
  python episodic_memory_engine.py build_full '{}'
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_db():
    """Open SQLite connection with WAL mode and row_factory."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    """Ensure required tables exist."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS market_episodes (
        episode_id TEXT PRIMARY KEY,
        start_date TEXT,
        end_date TEXT,
        breadth_score REAL,
        volatility_level REAL,
        volume_trend REAL,
        return_dispersion REAL,
        trend_strength REAL,
        fingerprint TEXT,
        outcome_7d REAL,
        outcome_30d REAL,
        outcome_label TEXT,
        n_symbols_used INTEGER,
        encoded_at TEXT
    );

    CREATE TABLE IF NOT EXISTS episode_similarity (
        query_date TEXT,
        episode_id TEXT,
        similarity_score REAL,
        rank INTEGER,
        computed_at TEXT,
        PRIMARY KEY (query_date, episode_id)
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Pure Python math helpers
# ---------------------------------------------------------------------------

def safe_float(val, default=0.0):
    """Safely cast value to float."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def compute_std(series):
    """Population standard deviation."""
    n = len(series)
    if n < 2:
        return 0.0
    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / n
    return math.sqrt(var)


def compute_median(series):
    """Median of a series."""
    if not series:
        return 0.0
    s = sorted(series)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    return float(s[mid])


def linear_regression_slope(x_vals, y_vals):
    """
    Pure Python OLS slope (no intercept returned).
    Returns slope of best-fit line through (x_vals, y_vals).
    """
    n = len(x_vals)
    if n < 2:
        return 0.0
    mx = sum(x_vals) / n
    my = sum(y_vals) / n
    num = sum((x_vals[i] - mx) * (y_vals[i] - my) for i in range(n))
    den = sum((x_vals[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def percentile_rank(val, series):
    """
    Percentile rank: fraction of series values <= val.
    Returns value in [0, 1].
    """
    if not series:
        return 0.5
    n = len(series)
    rank = sum(1 for x in series if x <= val)
    return rank / n


def normalize_series(values):
    """
    Normalize each value in `values` by its percentile rank within the list.
    Returns list of floats in [0, 1].
    """
    n = len(values)
    if n == 0:
        return []
    result = []
    for i, v in enumerate(values):
        rank = sum(1 for x in values if x <= v)
        result.append(rank / n)
    return result


def cosine_sim(a, b):
    """
    Cosine similarity between two equal-length float lists.
    Returns float in [-1, 1] (effectively [0, 1] for non-negative fingerprints).
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x ** 2 for x in a))
    nb = math.sqrt(sum(x ** 2 for x in b))
    return dot / (na * nb + 1e-9)


# ---------------------------------------------------------------------------
# OHLCV data loading
# ---------------------------------------------------------------------------

def load_ohlcv(db):
    """
    Load close prices and volumes from ohlcv table.

    Returns:
        dates_sorted: sorted list of unique dates (str)
        by_date: dict date -> list of (symbol, close) tuples
        vol_by_date: dict date -> total volume
    """
    try:
        cursor = db.execute(
            "SELECT symbol, date, close, volume FROM ohlcv ORDER BY date"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        # Table doesn't exist or is empty
        return [], {}, {}

    by_date = defaultdict(list)
    vol_by_date = defaultdict(float)
    for row in rows:
        symbol = row['symbol']
        date   = row['date']
        close  = safe_float(row['close'])
        volume = safe_float(row['volume'])
        if close > 0:
            by_date[date].append((symbol, close))
            vol_by_date[date] += volume

    dates_sorted = sorted(by_date.keys())
    return dates_sorted, dict(by_date), dict(vol_by_date)


def load_all_closes_by_symbol(db):
    """
    Returns dict: symbol -> [(date, close), ...] sorted by date.
    """
    try:
        cursor = db.execute(
            "SELECT symbol, date, close FROM ohlcv ORDER BY date"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        return {}

    by_symbol = defaultdict(list)
    for row in rows:
        symbol = row['symbol']
        date   = row['date']
        close  = safe_float(row['close'])
        if close > 0:
            by_symbol[symbol].append((date, close))

    return dict(by_symbol)


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------

def compute_raw_fingerprint(dates_window, by_date, vol_by_date, by_symbol):
    """
    Given a 20-day window (list of date strings), compute the 5 raw metric values
    (before normalization):

    breadth_score     = fraction of symbols with positive 20d return
    volatility_level  = median(20d ATR/price-equivalent = std of daily returns)
    volume_trend      = linear regression slope of total daily volume (normalized sign)
    return_dispersion = std of 20d returns across symbols
    trend_strength    = median abs(20d return)

    Returns dict with raw float values, or None if data insufficient.
    """
    if len(dates_window) < 2:
        return None

    start_date = dates_window[0]
    end_date   = dates_window[-1]

    # Get all symbols active on both start and end dates
    symbols_start = {sym for sym, _ in by_date.get(start_date, [])}
    symbols_end   = {sym for sym, _ in by_date.get(end_date, [])}
    common_symbols = symbols_start & symbols_end

    if len(common_symbols) < 3:
        return None

    # Map symbol -> close on start/end date
    close_start = dict(by_date.get(start_date, []))
    close_end   = dict(by_date.get(end_date, []))

    # 20d returns per symbol
    returns_20d = []
    for sym in common_symbols:
        cs = close_start.get(sym, 0.0)
        ce = close_end.get(sym, 0.0)
        if cs > 0 and ce > 0:
            returns_20d.append((ce - cs) / cs)

    if not returns_20d:
        return None

    # 1. Breadth score
    positive_count = sum(1 for r in returns_20d if r > 0)
    breadth_score = positive_count / len(returns_20d)

    # 2. Volatility level — median of per-symbol daily return std over window
    vol_per_symbol = []
    for sym in common_symbols:
        sym_data = by_symbol.get(sym, [])
        # Extract closes within window
        window_closes = [c for d, c in sym_data if start_date <= d <= end_date]
        if len(window_closes) >= 3:
            daily_rets = [(window_closes[i] - window_closes[i-1]) / window_closes[i-1]
                          for i in range(1, len(window_closes))
                          if window_closes[i-1] > 0]
            if daily_rets:
                vol_per_symbol.append(compute_std(daily_rets))

    volatility_level = compute_median(vol_per_symbol) if vol_per_symbol else 0.0

    # 3. Volume trend — OLS slope of total daily volume
    vols_in_window = [safe_float(vol_by_date.get(d, 0.0)) for d in dates_window]
    x_idx = list(range(len(vols_in_window)))
    raw_slope = linear_regression_slope(x_idx, vols_in_window)
    # Normalize slope by mean volume to get a dimensionless trend value
    mean_vol = sum(vols_in_window) / len(vols_in_window) if vols_in_window else 1.0
    if mean_vol > 0:
        volume_trend_raw = raw_slope / mean_vol  # fractional change per day
    else:
        volume_trend_raw = 0.0

    # 4. Return dispersion
    return_dispersion = compute_std(returns_20d)

    # 5. Trend strength
    trend_strength = compute_median([abs(r) for r in returns_20d])

    return {
        'breadth_score':     breadth_score,
        'volatility_level':  volatility_level,
        'volume_trend_raw':  volume_trend_raw,
        'return_dispersion': return_dispersion,
        'trend_strength':    trend_strength,
    }


def compute_outcome(dates_sorted, by_date, end_date, horizon_days):
    """
    Compute market-wide average return over `horizon_days` after `end_date`.

    Uses the average close across all symbols on end_date vs the date
    approximately `horizon_days` trading days later.

    Returns float or None if data insufficient.
    """
    try:
        end_idx = dates_sorted.index(end_date)
    except ValueError:
        return None

    # Find target date ~horizon_days later
    target_idx = end_idx + horizon_days
    if target_idx >= len(dates_sorted):
        return None

    target_date = dates_sorted[target_idx]

    closes_end    = [c for _, c in by_date.get(end_date, []) if c > 0]
    closes_target = [c for _, c in by_date.get(target_date, []) if c > 0]

    if not closes_end or not closes_target:
        return None

    avg_end    = sum(closes_end) / len(closes_end)
    avg_target = sum(closes_target) / len(closes_target)

    if avg_end <= 0:
        return None

    return (avg_target - avg_end) / avg_end


# ---------------------------------------------------------------------------
# Outcome label
# ---------------------------------------------------------------------------

def label_outcome(outcome_30d, volatility_normalized):
    """
    Assign outcome label based on 30d return and volatility.
    """
    if outcome_30d is None:
        return 'UNKNOWN'

    if outcome_30d > 0.05:
        base = 'BULL_BREAKOUT'
    elif outcome_30d > 0.01:
        base = 'RECOVERY'
    elif outcome_30d < -0.05:
        base = 'CRASH'
    elif outcome_30d < -0.01:
        base = 'DECLINE'
    else:
        base = 'SIDEWAYS'

    if volatility_normalized is not None and volatility_normalized > 0.7:
        return 'VOLATILE_' + base
    return base


# ---------------------------------------------------------------------------
# Normalization helper (percentile rank across a list of raw values)
# ---------------------------------------------------------------------------

def build_percentile_normalizers(raw_episodes):
    """
    Given list of raw dicts, return per-dimension sorted lists for percentile lookup.

    raw_episodes: list of dicts with keys:
        breadth_score, volatility_level, volume_trend_raw, return_dispersion, trend_strength
    """
    dims = ['breadth_score', 'volatility_level', 'volume_trend_raw',
            'return_dispersion', 'trend_strength']
    pools = {d: [] for d in dims}
    for ep in raw_episodes:
        for d in dims:
            v = ep.get(d)
            if v is not None:
                pools[d].append(v)
    return pools


def raw_to_fingerprint(raw, pools):
    """
    Convert a raw metrics dict to a normalized 5-float fingerprint using
    percentile rank within the provided pools.

    Returns list of 5 floats in [0, 1].
    """
    dims = ['breadth_score', 'volatility_level', 'volume_trend_raw',
            'return_dispersion', 'trend_strength']
    fp = []
    for d in dims:
        val  = raw.get(d, 0.0)
        pool = pools.get(d, [])
        fp.append(percentile_rank(val, pool) if pool else 0.5)
    return fp


# ---------------------------------------------------------------------------
# encode_episodes
# ---------------------------------------------------------------------------

def encode_episodes(params):
    """
    Scan OHLCV history, encode rolling 20-day windows as episodes.
    Stores each episode in market_episodes table.
    """
    window_size = int(params.get('window_size', 20))

    db = get_db()
    dates_sorted, by_date, vol_by_date = load_ohlcv(db)
    by_symbol = load_all_closes_by_symbol(db)

    if len(dates_sorted) < window_size + 2:
        db.close()
        return {
            'success': False,
            'error': f'Insufficient data: only {len(dates_sorted)} dates found, need at least {window_size + 2}',
            'n_episodes_encoded': 0,
        }

    # Step 1: Collect all raw metrics for all windows (for normalization)
    raw_windows = []
    window_meta = []  # [(start_date, end_date)]

    for i in range(len(dates_sorted) - window_size):
        window = dates_sorted[i: i + window_size]
        raw = compute_raw_fingerprint(window, by_date, vol_by_date, by_symbol)
        if raw is not None:
            raw_windows.append(raw)
            window_meta.append((window[0], window[-1]))

    if not raw_windows:
        db.close()
        return {
            'success': False,
            'error': 'No valid windows could be computed from OHLCV data',
            'n_episodes_encoded': 0,
        }

    # Step 2: Build percentile normalization pools
    pools = build_percentile_normalizers(raw_windows)

    # Step 3: Encode and store each episode
    now_str = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    n_encoded = 0
    outcomes_7d  = []
    outcomes_30d = []

    for raw, (start_date, end_date) in zip(raw_windows, window_meta):
        fp = raw_to_fingerprint(raw, pools)
        fp_norm_vol = fp[1]  # normalized volatility (index 1)

        outcome_7d  = compute_outcome(dates_sorted, by_date, end_date, 7)
        outcome_30d = compute_outcome(dates_sorted, by_date, end_date, 30)

        out_label = label_outcome(outcome_30d, fp_norm_vol)

        episode_id = f"{start_date}_{end_date}"

        # Get symbol count
        n_syms = len({sym for sym, _ in by_date.get(end_date, [])})

        # Count symbols from start for context
        n_syms_start = len({sym for sym, _ in by_date.get(start_date, [])})
        n_symbols_used = max(n_syms, n_syms_start)

        try:
            db.execute("""
                INSERT OR REPLACE INTO market_episodes
                    (episode_id, start_date, end_date,
                     breadth_score, volatility_level, volume_trend,
                     return_dispersion, trend_strength,
                     fingerprint, outcome_7d, outcome_30d, outcome_label,
                     n_symbols_used, encoded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                episode_id, start_date, end_date,
                raw['breadth_score'],
                raw['volatility_level'],
                raw['volume_trend_raw'],
                raw['return_dispersion'],
                raw['trend_strength'],
                json.dumps(fp),
                outcome_7d,
                outcome_30d,
                out_label,
                n_symbols_used,
                now_str,
            ))
            n_encoded += 1

            if outcome_7d is not None:
                outcomes_7d.append(outcome_7d)
            if outcome_30d is not None:
                outcomes_30d.append(outcome_30d)

        except sqlite3.Error:
            continue

    db.commit()

    avg_7d  = (sum(outcomes_7d)  / len(outcomes_7d))  if outcomes_7d  else None
    avg_30d = (sum(outcomes_30d) / len(outcomes_30d)) if outcomes_30d else None

    date_range = [dates_sorted[0], dates_sorted[-1]] if dates_sorted else [None, None]

    db.close()
    return {
        'success':          True,
        'n_episodes_encoded': n_encoded,
        'date_range':       date_range,
        'avg_outcome_7d':   round(avg_7d,  5) if avg_7d  is not None else None,
        'avg_outcome_30d':  round(avg_30d, 5) if avg_30d is not None else None,
        'n_windows_scanned': len(raw_windows),
    }


# ---------------------------------------------------------------------------
# Encode CURRENT period (last 20 days)
# ---------------------------------------------------------------------------

def encode_current_fingerprint(dates_sorted, by_date, vol_by_date, by_symbol, pools, window_size=20):
    """
    Encode the last `window_size` trading days as a fingerprint using the
    same normalization pools as the historical episodes.

    Returns (fingerprint_list, raw_dict) or (None, None).
    """
    if len(dates_sorted) < window_size:
        return None, None

    current_window = dates_sorted[-window_size:]
    raw = compute_raw_fingerprint(current_window, by_date, vol_by_date, by_symbol)
    if raw is None:
        return None, None

    fp = raw_to_fingerprint(raw, pools)
    return fp, raw


def load_episodes_from_db(db):
    """
    Load all episodes from market_episodes table.
    Returns list of dicts.
    """
    cursor = db.execute("""
        SELECT episode_id, start_date, end_date,
               breadth_score, volatility_level, volume_trend,
               return_dispersion, trend_strength,
               fingerprint, outcome_7d, outcome_30d, outcome_label,
               n_symbols_used
        FROM market_episodes
        ORDER BY start_date
    """)
    rows = cursor.fetchall()
    episodes = []
    for row in rows:
        fp_raw = row['fingerprint']
        try:
            fp = json.loads(fp_raw) if fp_raw else None
        except (json.JSONDecodeError, TypeError):
            fp = None

        episodes.append({
            'episode_id':       row['episode_id'],
            'start_date':       row['start_date'],
            'end_date':         row['end_date'],
            'breadth_score':    safe_float(row['breadth_score']),
            'volatility_level': safe_float(row['volatility_level']),
            'volume_trend':     safe_float(row['volume_trend']),
            'return_dispersion':safe_float(row['return_dispersion']),
            'trend_strength':   safe_float(row['trend_strength']),
            'fingerprint':      fp,
            'outcome_7d':       row['outcome_7d'],
            'outcome_30d':      row['outcome_30d'],
            'outcome_label':    row['outcome_label'],
            'n_symbols_used':   row['n_symbols_used'],
        })
    return episodes


def rebuild_pools_from_episodes(episodes):
    """
    Rebuild normalization pools from stored episode raw values.
    """
    raw_list = []
    for ep in episodes:
        raw_list.append({
            'breadth_score':    ep['breadth_score'],
            'volatility_level': ep['volatility_level'],
            'volume_trend_raw': ep['volume_trend'],
            'return_dispersion':ep['return_dispersion'],
            'trend_strength':   ep['trend_strength'],
        })
    return build_percentile_normalizers(raw_list)


# ---------------------------------------------------------------------------
# find_similar
# ---------------------------------------------------------------------------

def find_similar(params):
    """
    Find episodes most similar to the current 20-day period.
    """
    top_k = int(params.get('top_k', 5))

    db = get_db()
    dates_sorted, by_date, vol_by_date = load_ohlcv(db)
    by_symbol = load_all_closes_by_symbol(db)
    episodes  = load_episodes_from_db(db)

    if not episodes:
        db.close()
        return {
            'success': False,
            'error': 'No episodes in database. Run encode_episodes first.',
        }

    # Rebuild pools from stored episodes
    pools = rebuild_pools_from_episodes(episodes)

    # Encode current period
    current_fp, current_raw = encode_current_fingerprint(
        dates_sorted, by_date, vol_by_date, by_symbol, pools
    )

    if current_fp is None:
        db.close()
        return {
            'success': False,
            'error': 'Could not encode current period — insufficient OHLCV data.',
        }

    # Compute cosine similarity for all episodes
    scored = []
    for ep in episodes:
        fp = ep.get('fingerprint')
        if fp is None or len(fp) != 5:
            continue
        sim = cosine_sim(current_fp, fp)
        scored.append((sim, ep))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # Build result list
    similar_episodes = []
    for sim, ep in top:
        similar_episodes.append({
            'episode_id':    ep['episode_id'],
            'similarity':    round(sim, 4),
            'start_date':    ep['start_date'],
            'end_date':      ep['end_date'],
            'outcome_7d':    round(ep['outcome_7d'],  5) if ep['outcome_7d']  is not None else None,
            'outcome_30d':   round(ep['outcome_30d'], 5) if ep['outcome_30d'] is not None else None,
            'outcome_label': ep['outcome_label'],
        })

    # Compute consensus outlook
    labels = [ep['outcome_label'] for _, ep in top if ep['outcome_label']]
    bull_labels     = ['BULL_BREAKOUT', 'RECOVERY', 'VOLATILE_BULL_BREAKOUT', 'VOLATILE_RECOVERY']
    bear_labels     = ['CRASH', 'DECLINE', 'VOLATILE_CRASH', 'VOLATILE_DECLINE']
    sideways_labels = ['SIDEWAYS', 'VOLATILE_SIDEWAYS']

    n_total   = len(labels) if labels else 1
    n_bull     = sum(1 for l in labels if l in bull_labels)
    n_bear     = sum(1 for l in labels if l in bear_labels)
    n_sideways = sum(1 for l in labels if l in sideways_labels)

    prob_bull     = n_bull     / n_total
    prob_bear     = n_bear     / n_total
    prob_sideways = n_sideways / n_total

    # Consensus = modal category
    counts = {'BULL': n_bull, 'BEAR': n_bear, 'SIDEWAYS': n_sideways}
    consensus = max(counts, key=lambda k: counts[k])
    consensus_map = {
        'BULL':     'RECOVERY' if n_bull <= 2 else 'BULL_BREAKOUT',
        'BEAR':     'DECLINE'  if n_bear <= 2 else 'CRASH',
        'SIDEWAYS': 'SIDEWAYS',
    }
    consensus_outlook = consensus_map.get(consensus, 'SIDEWAYS')

    # Persist similarity results
    today_str  = datetime.utcnow().strftime('%Y-%m-%d')
    now_str    = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        db.execute("DELETE FROM episode_similarity WHERE query_date = ?", (today_str,))
        for rank_i, (sim, ep) in enumerate(top, start=1):
            db.execute("""
                INSERT OR REPLACE INTO episode_similarity
                    (query_date, episode_id, similarity_score, rank, computed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (today_str, ep['episode_id'], round(sim, 6), rank_i, now_str))
        db.commit()
    except sqlite3.Error:
        pass

    db.close()
    return {
        'success':             True,
        'current_fingerprint': [round(v, 4) for v in current_fp],
        'similar_episodes':    similar_episodes,
        'consensus_outlook':   consensus_outlook,
        'probability_bull':    round(prob_bull,     3),
        'probability_bear':    round(prob_bear,     3),
        'probability_sideways':round(prob_sideways, 3),
        'n_episodes_searched': len(scored),
    }


# ---------------------------------------------------------------------------
# Fingerprint description helpers
# ---------------------------------------------------------------------------

def describe_breadth(fp_val, raw_val):
    """Human-readable description of breadth dimension."""
    pct = int(raw_val * 100) if raw_val is not None else int(fp_val * 100)
    if fp_val >= 0.7:
        return f"STRONG — {pct}% of stocks advancing"
    elif fp_val >= 0.5:
        return f"MODERATE — {pct}% of stocks advancing"
    elif fp_val >= 0.3:
        return f"WEAK — {pct}% of stocks advancing"
    else:
        return f"VERY WEAK — {pct}% of stocks advancing"


def describe_volatility(fp_val):
    """Human-readable description of volatility dimension."""
    if fp_val >= 0.8:
        return "EXTREME"
    elif fp_val >= 0.6:
        return "HIGH"
    elif fp_val >= 0.4:
        return "MODERATE"
    elif fp_val >= 0.2:
        return "LOW"
    else:
        return "VERY LOW"


def describe_volume_trend(fp_val, raw_val):
    """Human-readable description of volume trend dimension."""
    if fp_val >= 0.65:
        return "RISING"
    elif fp_val >= 0.4:
        return "FLAT"
    else:
        return "FALLING"


def describe_dispersion(fp_val):
    """Human-readable description of return dispersion."""
    if fp_val >= 0.7:
        return "WIDE"
    elif fp_val >= 0.4:
        return "MODERATE"
    else:
        return "NARROW"


def describe_trend_strength(fp_val):
    """Human-readable description of trend strength."""
    if fp_val >= 0.7:
        return "STRONG"
    elif fp_val >= 0.45:
        return "MODERATE"
    elif fp_val >= 0.25:
        return "WEAK"
    else:
        return "VERY WEAK"


def build_fingerprint_description(fp, raw):
    """
    Build a dict of human-readable descriptions for each fingerprint dimension.
    fp: normalized 5-float list
    raw: raw metrics dict
    """
    if fp is None or len(fp) < 5:
        return {}
    return {
        'breadth':       describe_breadth(fp[0],       raw.get('breadth_score', fp[0]) if raw else fp[0]),
        'volatility':    describe_volatility(fp[1]),
        'volume_trend':  describe_volume_trend(fp[2],  raw.get('volume_trend_raw', fp[2]) if raw else fp[2]),
        'dispersion':    describe_dispersion(fp[3]),
        'trend_strength':describe_trend_strength(fp[4]),
    }


# ---------------------------------------------------------------------------
# Arabic narrative helpers
# ---------------------------------------------------------------------------

def arabic_month_from_date(date_str):
    """Convert a date string 'YYYY-MM-DD' to Arabic month name + year."""
    months_ar = {
        1: 'يناير', 2: 'فبراير', 3: 'مارس', 4: 'أبريل',
        5: 'مايو',  6: 'يونيو', 7: 'يوليو', 8: 'أغسطس',
        9: 'سبتمبر',10: 'أكتوبر',11: 'نوفمبر',12: 'ديسمبر',
    }
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return f"{months_ar.get(dt.month, '')} {dt.year}"
    except (ValueError, TypeError):
        return date_str


def build_analogy_text(similar_episodes):
    """Build Arabic analogy string naming the most similar episode."""
    if not similar_episodes:
        return "لا توجد فترات مشابهة كافية"
    best = similar_episodes[0]
    pct  = int(best['similarity'] * 100)
    month = arabic_month_from_date(best['start_date'])
    return f"الوضع الحالي أقرب إلى: {month} (تشابه: {pct}%)"


def build_historical_outcome_text(similar_episodes):
    """Build Arabic historical outcome narrative."""
    if not similar_episodes:
        return "لا توجد بيانات كافية لاستخراج نمط"

    bull_labels     = ['BULL_BREAKOUT', 'RECOVERY', 'VOLATILE_BULL_BREAKOUT', 'VOLATILE_RECOVERY']
    n_total         = len(similar_episodes)
    n_positive      = sum(1 for ep in similar_episodes
                         if ep.get('outcome_label') in bull_labels)

    outcomes_30d = [ep['outcome_30d'] for ep in similar_episodes
                    if ep.get('outcome_30d') is not None]
    avg_30d = (sum(outcomes_30d) / len(outcomes_30d)) if outcomes_30d else None

    direction = "ارتداد إيجابي" if (avg_30d is not None and avg_30d > 0) else "ضغط سلبي"

    return (
        f"ما تبع تلك الفترة: {direction} خلال 30 يوم "
        f"في {n_positive}/{n_total} حالات"
    )


def compute_confidence(similar_episodes):
    """
    Compute confidence level based on similarity scores and agreement of outcomes.
    Returns 'HIGH', 'MEDIUM', or 'LOW'.
    """
    if not similar_episodes:
        return 'LOW'

    avg_sim = sum(ep['similarity'] for ep in similar_episodes) / len(similar_episodes)

    labels = [ep.get('outcome_label', '') for ep in similar_episodes]
    bull_labels  = ['BULL_BREAKOUT', 'RECOVERY', 'VOLATILE_BULL_BREAKOUT', 'VOLATILE_RECOVERY']
    bear_labels  = ['CRASH', 'DECLINE', 'VOLATILE_CRASH', 'VOLATILE_DECLINE']
    n_bull = sum(1 for l in labels if l in bull_labels)
    n_bear = sum(1 for l in labels if l in bear_labels)
    n = len(labels) if labels else 1
    agreement = max(n_bull, n_bear) / n

    if avg_sim >= 0.90 and agreement >= 0.8:
        return 'HIGH'
    elif avg_sim >= 0.75 and agreement >= 0.6:
        return 'MEDIUM'
    else:
        return 'LOW'


# ---------------------------------------------------------------------------
# analogy_report
# ---------------------------------------------------------------------------

def analogy_report(params):
    """
    Human-readable analogy report using find_similar internally.
    """
    top_k = int(params.get('top_k', 5))

    # Run find_similar first
    sim_result = find_similar({'top_k': top_k})
    if not sim_result.get('success'):
        return {
            'success': False,
            'error':   sim_result.get('error', 'find_similar failed'),
        }

    similar_episodes   = sim_result['similar_episodes']
    current_fingerprint = sim_result['current_fingerprint']
    prob_bull           = sim_result['probability_bull']
    prob_bear           = sim_result['probability_bear']
    prob_sideways       = sim_result['probability_sideways']

    # We need the raw values to build descriptions — re-load from DB
    db = get_db()
    dates_sorted, by_date, vol_by_date = load_ohlcv(db)
    by_symbol  = load_all_closes_by_symbol(db)
    episodes   = load_episodes_from_db(db)
    db.close()

    pools = rebuild_pools_from_episodes(episodes)
    _, current_raw = encode_current_fingerprint(
        dates_sorted, by_date, vol_by_date, by_symbol, pools
    )

    fp_desc = build_fingerprint_description(current_fingerprint, current_raw)

    analogy_text   = build_analogy_text(similar_episodes)
    hist_outcome   = build_historical_outcome_text(similar_episodes)
    confidence     = compute_confidence(similar_episodes)
    today_str      = datetime.utcnow().strftime('%Y-%m-%d')

    return {
        'success':                       True,
        'date':                          today_str,
        'analogy':                       analogy_text,
        'historical_outcome':            hist_outcome,
        'similar_episodes':              similar_episodes,
        'current_fingerprint_description': fp_desc,
        'forward_probability': {
            'bull':     prob_bull,
            'bear':     prob_bear,
            'sideways': prob_sideways,
        },
        'confidence': confidence,
    }


# ---------------------------------------------------------------------------
# get_episode
# ---------------------------------------------------------------------------

def get_episode(params):
    """
    Retrieve a specific episode by episode_id.
    """
    episode_id = params.get('episode_id')
    if not episode_id:
        return {'success': False, 'error': 'episode_id parameter required'}

    db = get_db()
    cursor = db.execute("""
        SELECT * FROM market_episodes WHERE episode_id = ?
    """, (episode_id,))
    row = cursor.fetchone()
    db.close()

    if row is None:
        return {'success': False, 'error': f'Episode {episode_id!r} not found'}

    fp_raw = row['fingerprint']
    try:
        fp = json.loads(fp_raw) if fp_raw else None
    except (json.JSONDecodeError, TypeError):
        fp = None

    result = {
        'success':          True,
        'episode_id':       row['episode_id'],
        'start_date':       row['start_date'],
        'end_date':         row['end_date'],
        'breadth_score':    safe_float(row['breadth_score']),
        'volatility_level': safe_float(row['volatility_level']),
        'volume_trend':     safe_float(row['volume_trend']),
        'return_dispersion':safe_float(row['return_dispersion']),
        'trend_strength':   safe_float(row['trend_strength']),
        'fingerprint':      fp,
        'outcome_7d':       row['outcome_7d'],
        'outcome_30d':      row['outcome_30d'],
        'outcome_label':    row['outcome_label'],
        'n_symbols_used':   row['n_symbols_used'],
        'encoded_at':       row['encoded_at'],
    }

    # Add fingerprint description if available
    if fp:
        raw_dict = {
            'breadth_score':    result['breadth_score'],
            'volatility_level': result['volatility_level'],
            'volume_trend_raw': result['volume_trend'],
            'return_dispersion':result['return_dispersion'],
            'trend_strength':   result['trend_strength'],
        }
        result['fingerprint_description'] = build_fingerprint_description(fp, raw_dict)

    return result


# ---------------------------------------------------------------------------
# build_full
# ---------------------------------------------------------------------------

def build_full(params):
    """
    Run encode_episodes + find_similar + analogy_report and return combined result.
    """
    top_k = int(params.get('top_k', 5))

    # 1. Encode episodes
    enc_result = encode_episodes(params)

    # 2. Find similar
    sim_result = find_similar({'top_k': top_k})

    # 3. Analogy report
    ana_result = analogy_report({'top_k': top_k})

    return {
        'success':  True,
        'encoding': enc_result,
        'similar':  sim_result,
        'analogy':  ana_result,
        'status':   'complete',
    }


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'encode_episodes': encode_episodes,
    'find_similar':    find_similar,
    'analogy_report':  analogy_report,
    'get_episode':     get_episode,
    'build_full':      build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'success': False,
            'error':   'Usage: episodic_memory_engine.py <command> [json_params]',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    command = sys.argv[1].strip()

    # Parse optional JSON params
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except (json.JSONDecodeError, ValueError) as exc:
            print(json.dumps({
                'success': False,
                'error':   f'Invalid JSON params: {exc}',
            }))
            sys.exit(1)
    else:
        params = {}

    if command not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error':   f'Unknown command: {command!r}',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[command](params)
    except Exception as exc:
        import traceback
        print(json.dumps({
            'success':   False,
            'command':   command,
            'error':     str(exc),
            'traceback': traceback.format_exc(),
        }))
        sys.exit(1)

    # Last stdout line must be valid JSON
    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    main()
