"""
Phase 52 — Liquidity Microstructure Engine
EGX Autonomous Quant System

Builds a liquidity profile for every symbol and enforces liquidity-gated
recommendations. Addresses the critical gap where MEGM (EGP 5K/day) was
treated identically to COMI (EGP 50M/day).
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import datetime
import collections

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

LIQUIDITY_TIERS = {
    'TIER1':    {'min_advt_egp': 5_000_000,  'max_position_pct_adv': 0.10, 'label': 'Highly Liquid',  'arabic': 'سيولة عالية'},
    'TIER2':    {'min_advt_egp': 1_000_000,  'max_position_pct_adv': 0.05, 'label': 'Liquid',          'arabic': 'سائل'},
    'TIER3':    {'min_advt_egp':   500_000,  'max_position_pct_adv': 0.03, 'label': 'Semi-Liquid',     'arabic': 'شبه سائل'},
    'TIER4':    {'min_advt_egp':   100_000,  'max_position_pct_adv': 0.01, 'label': 'Illiquid',        'arabic': 'غير سائل'},
    'ILLIQUID': {'min_advt_egp':         0,  'max_position_pct_adv': 0.00, 'label': 'Avoid',           'arabic': 'تجنب'},
}

# Tier order from best to worst (for min_tier filtering)
TIER_ORDER = ['TIER1', 'TIER2', 'TIER3', 'TIER4', 'ILLIQUID']

# Absolute cap on a single order regardless of tier
MAX_SINGLE_ORDER_EGP = 500_000

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_liquidity_profile_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_profile (
            symbol             TEXT NOT NULL,
            computed_date      TEXT NOT NULL,
            advt_30d           REAL,
            advt_10d           REAL,
            amihud_ratio       REAL,
            turnover_velocity  REAL,
            bid_ask_spread_est REAL,
            dom_spread_pct     REAL,
            max_safe_order_egp REAL,
            liquidity_tier     TEXT,
            liquidity_score    REAL,
            PRIMARY KEY (symbol, computed_date)
        )
    """)
    conn.commit()


def ensure_dom_snapshots_table(conn):
    """Create dom_snapshots if it doesn't exist yet (may be populated later)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dom_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            snapshot_time INTEGER NOT NULL,
            bids          TEXT,
            asks          TEXT,
            spread_pct    REAL
        )
    """)
    conn.commit()

# ---------------------------------------------------------------------------
# Pure computation helpers
# ---------------------------------------------------------------------------

def _classify_tier(advt_10d: float) -> str:
    """Return tier string based on ADVT_10d."""
    for tier in TIER_ORDER:
        if advt_10d >= LIQUIDITY_TIERS[tier]['min_advt_egp']:
            return tier
    return 'ILLIQUID'


def _liquidity_score(advt_10d: float) -> float:
    """
    Log-scale 0-100 score.
    0  → EGP 0
    50 → ~EGP 100K   (TIER4 threshold)
    75 → ~EGP 1M     (TIER2 threshold)
    100→  EGP 50M+
    """
    if advt_10d <= 0:
        return 0.0
    # Use log10 normalised between 0 (log10(1)=0) and cap at log10(50M)=7.7
    LOG_MIN = 0.0
    LOG_MAX = math.log10(50_000_000)
    score = (math.log10(max(advt_10d, 1)) - LOG_MIN) / (LOG_MAX - LOG_MIN) * 100.0
    return round(min(max(score, 0.0), 100.0), 2)


def _amihud(returns: list, values: list) -> float:
    """
    Amihud illiquidity = mean(|r_t| / volume_value_t) × 1e6
    Filters out zero-value days.
    """
    ratios = [abs(r) / v for r, v in zip(returns, values) if v > 0]
    if not ratios:
        return 0.0
    return round(statistics.mean(ratios) * 1e6, 6)


def _safe_mean(values: list) -> float:
    if not values:
        return 0.0
    return statistics.mean(values)

# ---------------------------------------------------------------------------
# Command: compute_symbol_liquidity
# ---------------------------------------------------------------------------

def compute_symbol_liquidity(params: dict) -> dict:
    symbol   = params.get('symbol', '').strip().upper()
    lookback = int(params.get('lookback_days', 30))

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}

    db = get_db()
    ensure_liquidity_profile_table(db)
    ensure_dom_snapshots_table(db)

    today_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')

    # ---- Fetch bars --------------------------------------------------------
    # Fetch lookback+10 to ensure we have enough rows after filtering
    cutoff_dt = datetime.datetime.utcnow() - datetime.timedelta(days=lookback + 10)
    cutoff_ts = int(cutoff_dt.timestamp())

    rows = db.execute("""
        SELECT bar_time, open, high, low, close, volume
        FROM ohlcv_history
        WHERE symbol = ? AND bar_time >= ? AND close > 0 AND volume > 0
        ORDER BY bar_time ASC
    """, (symbol, cutoff_ts)).fetchall()

    if len(rows) < 3:
        return {
            'success':    False,
            'symbol':     symbol,
            'error':      f'Insufficient data: only {len(rows)} bars available',
            'bars_found': len(rows),
        }

    # Convert to plain lists for calculation
    bar_times  = [r['bar_time'] for r in rows]
    closes     = [r['close']    for r in rows]
    volumes    = [r['volume']   for r in rows]
    highs      = [r['high']     for r in rows]
    lows       = [r['low']      for r in rows]

    # Daily value traded (close × volume)
    daily_values = [c * v for c, v in zip(closes, volumes)]

    # Daily returns (price change / prev_close)
    returns = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        ret  = (closes[i] - prev) / prev if prev > 0 else 0.0
        returns.append(ret)

    # Align: returns[i] corresponds to daily_values[i+1]
    aligned_values = daily_values[1:]

    # ---- ADVT calculations ------------------------------------------------
    n = len(daily_values)

    # Last 30 trading days
    vals_30 = daily_values[-30:] if n >= 30 else daily_values
    advt_30d = sum(vals_30) / max(len(vals_30), 1)

    # Last 10 trading days
    vals_10 = daily_values[-10:] if n >= 10 else daily_values
    advt_10d = sum(vals_10) / max(len(vals_10), 1)

    # ---- Amihud ratio -----------------------------------------------------
    amihud = _amihud(returns[-30:], aligned_values[-30:])

    # ---- Spread estimate (half bid-ask proxy) in bps ----------------------
    spreads_bps = []
    for h, l, c in zip(highs, lows, closes):
        if c > 0 and h >= l:
            half_spread_bps = ((h - l) / c) * 10_000 / 2.0
            spreads_bps.append(half_spread_bps)
    bid_ask_spread_est = round(_safe_mean(spreads_bps[-30:]), 2)

    # ---- Turnover velocity ------------------------------------------------
    # Current 10d avg volume vs 30d avg volume
    vol_30d = _safe_mean(volumes[-30:])
    vol_10d = _safe_mean(volumes[-10:]) if n >= 10 else _safe_mean(volumes)
    turnover_velocity = round(vol_10d / vol_30d, 4) if vol_30d > 0 else 1.0

    # ---- DOM spread (if available) ----------------------------------------
    dom_spread_pct = None
    dom_row = db.execute("""
        SELECT spread_pct FROM dom_snapshots
        WHERE symbol = ?
        ORDER BY snapshot_time DESC
        LIMIT 1
    """, (symbol,)).fetchone()
    if dom_row and dom_row['spread_pct'] is not None:
        dom_spread_pct = round(float(dom_row['spread_pct']), 4)

    # ---- Tier & derived metrics -------------------------------------------
    tier     = _classify_tier(advt_10d)
    tier_cfg = LIQUIDITY_TIERS[tier]
    pct_adv  = tier_cfg['max_position_pct_adv']

    max_safe_order_egp = round(min(advt_10d * pct_adv, MAX_SINGLE_ORDER_EGP), 2)
    score              = _liquidity_score(advt_10d)

    # ---- Persist to DB ----------------------------------------------------
    db.execute("""
        INSERT OR REPLACE INTO liquidity_profile
            (symbol, computed_date, advt_30d, advt_10d, amihud_ratio,
             turnover_velocity, bid_ask_spread_est, dom_spread_pct,
             max_safe_order_egp, liquidity_tier, liquidity_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        symbol, today_str,
        round(advt_30d, 2), round(advt_10d, 2),
        amihud, turnover_velocity, bid_ask_spread_est,
        dom_spread_pct, max_safe_order_egp, tier, score,
    ))
    db.commit()

    return {
        'success':            True,
        'symbol':             symbol,
        'computed_date':      today_str,
        'advt_30d_egp':       round(advt_30d, 2),
        'advt_10d_egp':       round(advt_10d, 2),
        'amihud_ratio':       amihud,
        'turnover_velocity':  turnover_velocity,
        'bid_ask_spread_bps': bid_ask_spread_est,
        'dom_spread_pct':     dom_spread_pct,
        'max_safe_order_egp': max_safe_order_egp,
        'liquidity_tier':     tier,
        'tier_label':         tier_cfg['label'],
        'tier_label_ar':      tier_cfg['arabic'],
        'liquidity_score':    score,
        'bars_used':          n,
    }

# ---------------------------------------------------------------------------
# Command: tier_classification
# ---------------------------------------------------------------------------

def tier_classification(params: dict) -> dict:
    db = get_db()
    ensure_liquidity_profile_table(db)

    # Latest profile per symbol
    rows = db.execute("""
        SELECT lp.symbol, lp.liquidity_tier, lp.advt_10d, lp.liquidity_score
        FROM liquidity_profile lp
        INNER JOIN (
            SELECT symbol, MAX(computed_date) AS latest
            FROM liquidity_profile
            GROUP BY symbol
        ) latest_dates ON lp.symbol = latest_dates.symbol
                      AND lp.computed_date = latest_dates.latest
        ORDER BY lp.advt_10d DESC
    """).fetchall()

    # Universe size (symbols with any ohlcv data)
    total_symbols = db.execute(
        "SELECT COUNT(DISTINCT symbol) AS cnt FROM ohlcv_history"
    ).fetchone()['cnt']

    tier_counts     = collections.Counter()
    liquid_universe = []
    semi_liquid     = []
    avoid_list      = []

    for r in rows:
        t = r['liquidity_tier'] or 'ILLIQUID'
        tier_counts[t] += 1
        entry = {
            'symbol':          r['symbol'],
            'tier':            t,
            'advt_10d_egp':    round(r['advt_10d'] or 0, 2),
            'liquidity_score': round(r['liquidity_score'] or 0, 2),
        }
        if t in ('TIER1', 'TIER2'):
            liquid_universe.append(entry)
        elif t == 'TIER3':
            semi_liquid.append(entry)
        else:
            avoid_list.append(entry)

    profiled    = len(rows)
    coverage    = round(profiled / total_symbols * 100, 1) if total_symbols else 0.0

    return {
        'success':         True,
        'tier_counts':     dict(tier_counts),
        'liquid_universe': liquid_universe,
        'semi_liquid':     semi_liquid,
        'avoid_list':      avoid_list,
        'profiled_count':  profiled,
        'total_universe':  total_symbols,
        'coverage_pct':    coverage,
    }

# ---------------------------------------------------------------------------
# Command: liquidity_filter
# ---------------------------------------------------------------------------

def liquidity_filter(params: dict) -> dict:
    min_tier     = params.get('min_tier', 'TIER2').upper()
    min_advt_egp = float(params.get('min_advt_egp', 0))

    if min_tier not in TIER_ORDER:
        return {'success': False, 'error': f'Invalid min_tier: {min_tier}. Valid: {TIER_ORDER}'}

    # All tiers at or better than min_tier
    cutoff_idx       = TIER_ORDER.index(min_tier)
    acceptable_tiers = TIER_ORDER[:cutoff_idx + 1]

    db = get_db()
    ensure_liquidity_profile_table(db)

    rows = db.execute("""
        SELECT lp.symbol, lp.liquidity_tier, lp.advt_10d,
               lp.advt_30d, lp.max_safe_order_egp, lp.liquidity_score
        FROM liquidity_profile lp
        INNER JOIN (
            SELECT symbol, MAX(computed_date) AS latest
            FROM liquidity_profile
            GROUP BY symbol
        ) ld ON lp.symbol = ld.symbol AND lp.computed_date = ld.latest
        WHERE lp.liquidity_tier IN ({placeholders})
          AND lp.advt_10d >= ?
        ORDER BY lp.advt_10d DESC
    """.format(
        placeholders=','.join('?' * len(acceptable_tiers))
    ), (*acceptable_tiers, min_advt_egp)).fetchall()

    filtered = []
    for r in rows:
        tier     = r['liquidity_tier']
        tier_cfg = LIQUIDITY_TIERS.get(tier, LIQUIDITY_TIERS['ILLIQUID'])
        filtered.append({
            'symbol':             r['symbol'],
            'tier':               tier,
            'tier_label':         tier_cfg['label'],
            'advt_10d_egp':       round(r['advt_10d'] or 0, 2),
            'advt_30d_egp':       round(r['advt_30d'] or 0, 2),
            'max_safe_order_egp': round(r['max_safe_order_egp'] or 0, 2),
            'liquidity_score':    round(r['liquidity_score'] or 0, 2),
        })

    return {
        'success':          True,
        'filter_criteria':  {'min_tier': min_tier, 'min_advt_egp': min_advt_egp},
        'filtered_count':   len(filtered),
        'filtered_symbols': filtered,
    }

# ---------------------------------------------------------------------------
# Command: max_position_size
# ---------------------------------------------------------------------------

def max_position_size(params: dict) -> dict:
    symbol      = params.get('symbol', '').strip().upper()
    capital_egp = float(params.get('capital_egp', 100_000))
    risk_pct    = float(params.get('risk_pct', 0.02))
    stop_pct    = float(params.get('stop_loss_pct', 0.05))  # default 5% stop

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}

    db = get_db()
    ensure_liquidity_profile_table(db)

    profile = db.execute("""
        SELECT lp.advt_10d, lp.max_safe_order_egp, lp.liquidity_tier,
               lp.liquidity_score, lp.computed_date
        FROM liquidity_profile lp
        INNER JOIN (
            SELECT symbol, MAX(computed_date) AS latest
            FROM liquidity_profile
            WHERE symbol = ?
        ) ld ON lp.symbol = ld.symbol AND lp.computed_date = ld.latest
    """, (symbol,)).fetchone()

    if not profile:
        return {
            'success': False,
            'symbol':  symbol,
            'error':   'No liquidity profile found. Run compute_symbol_liquidity first.',
        }

    tier            = profile['liquidity_tier']
    tier_cfg        = LIQUIDITY_TIERS.get(tier, LIQUIDITY_TIERS['ILLIQUID'])
    max_safe_order  = float(profile['max_safe_order_egp'] or 0)
    advt_10d        = float(profile['advt_10d'] or 0)

    # --- Risk-based position size ---
    # Max loss = capital × risk_pct
    # Position = max_loss / stop_pct
    if stop_pct > 0:
        risk_based_egp = (capital_egp * risk_pct) / stop_pct
    else:
        risk_based_egp = capital_egp * risk_pct

    # --- Liquidity constraint ---
    if tier == 'ILLIQUID':
        recommended_egp   = 0.0
        constraint_reason = f'Symbol is ILLIQUID (ADVT={advt_10d:,.0f} EGP). Trade avoided.'
    elif max_safe_order <= 0:
        recommended_egp   = 0.0
        constraint_reason = 'Max safe order is zero for this tier. Avoid trading.'
    elif risk_based_egp <= max_safe_order:
        recommended_egp   = risk_based_egp
        constraint_reason = 'Risk-based sizing is within liquidity limits.'
    else:
        recommended_egp   = max_safe_order
        constraint_reason = (
            f'Risk-based size ({risk_based_egp:,.0f} EGP) exceeds liquidity cap '
            f'({max_safe_order:,.0f} EGP). Capped by ADVT constraint.'
        )

    # Estimate shares (need a current price — use advt_10d / 10-day avg volume)
    latest_close = db.execute("""
        SELECT close FROM ohlcv_history
        WHERE symbol = ? AND close > 0
        ORDER BY bar_time DESC LIMIT 1
    """, (symbol,)).fetchone()

    shares_est = None
    if latest_close and latest_close['close'] > 0:
        price      = float(latest_close['close'])
        shares_est = int(recommended_egp / price) if recommended_egp > 0 else 0

    return {
        'success':              True,
        'symbol':               symbol,
        'capital_egp':          capital_egp,
        'risk_pct':             risk_pct,
        'stop_pct':             stop_pct,
        'risk_based_egp':       round(risk_based_egp, 2),
        'liquidity_max_egp':    round(max_safe_order, 2),
        'recommended_egp':      round(recommended_egp, 2),
        'recommended_shares_est': shares_est,
        'constraint_reason':    constraint_reason,
        'liquidity_tier':       tier,
        'tier_label':           tier_cfg['label'],
        'tier_label_ar':        tier_cfg['arabic'],
        'liquidity_score':      round(profile['liquidity_score'] or 0, 2),
        'profile_date':         profile['computed_date'],
    }

# ---------------------------------------------------------------------------
# Command: build_liquidity_profiles
# ---------------------------------------------------------------------------

def build_liquidity_profiles(params: dict) -> dict:
    force_recompute = bool(params.get('force_recompute', False))

    db = get_db()
    ensure_liquidity_profile_table(db)

    today_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')

    # All symbols with data
    symbols_rows = db.execute(
        "SELECT DISTINCT symbol FROM ohlcv_history ORDER BY symbol"
    ).fetchall()
    all_symbols = [r['symbol'] for r in symbols_rows]

    # Which already have today's profile
    already_done = set()
    if not force_recompute:
        done_rows = db.execute(
            "SELECT symbol FROM liquidity_profile WHERE computed_date = ?",
            (today_str,)
        ).fetchall()
        already_done = {r['symbol'] for r in done_rows}

    n_computed  = 0
    n_skipped   = 0
    errors      = []
    tier_counts = collections.Counter()

    for symbol in all_symbols:
        if symbol in already_done:
            n_skipped += 1
            # Still count existing tier for summary
            row = db.execute(
                "SELECT liquidity_tier FROM liquidity_profile WHERE symbol = ? AND computed_date = ?",
                (symbol, today_str)
            ).fetchone()
            if row:
                tier_counts[row['liquidity_tier']] += 1
            continue

        try:
            result = compute_symbol_liquidity({'symbol': symbol, 'lookback_days': 30})
            if result.get('success'):
                tier_counts[result['liquidity_tier']] += 1
                n_computed += 1
            else:
                errors.append({'symbol': symbol, 'error': result.get('error', 'unknown')})
        except Exception as exc:
            errors.append({'symbol': symbol, 'error': str(exc)})

    return {
        'success':      True,
        'date':         today_str,
        'total_symbols': len(all_symbols),
        'n_computed':   n_computed,
        'n_skipped':    n_skipped,
        'n_errors':     len(errors),
        'tier_summary': dict(tier_counts),
        'errors':       errors[:20],  # cap for readability
    }

# ---------------------------------------------------------------------------
# Command: liquidity_report
# ---------------------------------------------------------------------------

def liquidity_report(params: dict) -> dict:
    db = get_db()
    ensure_liquidity_profile_table(db)

    # All latest profiles
    rows = db.execute("""
        SELECT lp.symbol, lp.liquidity_tier, lp.advt_10d, lp.advt_30d,
               lp.liquidity_score, lp.amihud_ratio,
               lp.bid_ask_spread_est, lp.max_safe_order_egp, lp.computed_date
        FROM liquidity_profile lp
        INNER JOIN (
            SELECT symbol, MAX(computed_date) AS latest
            FROM liquidity_profile
            GROUP BY symbol
        ) ld ON lp.symbol = ld.symbol AND lp.computed_date = ld.latest
        ORDER BY lp.advt_10d DESC
    """).fetchall()

    if not rows:
        return {
            'success': False,
            'error':   'No liquidity profiles found. Run build_liquidity_profiles first.',
        }

    # --- Tier distribution ---
    tier_counts = collections.Counter(r['liquidity_tier'] for r in rows)

    # --- Top 20 most liquid ---
    top_20 = []
    for r in rows[:20]:
        tier = r['liquidity_tier'] or 'ILLIQUID'
        top_20.append({
            'symbol':        r['symbol'],
            'tier':          tier,
            'tier_label':    LIQUIDITY_TIERS[tier]['label'],
            'advt_10d_egp':  round(r['advt_10d'] or 0, 2),
            'score':         round(r['liquidity_score'] or 0, 2),
        })

    # --- Bottom 10 (illiquid end, only those with profiles) ---
    sorted_asc = sorted(rows, key=lambda r: r['advt_10d'] or 0)
    bottom_10  = []
    for r in sorted_asc[:10]:
        tier = r['liquidity_tier'] or 'ILLIQUID'
        bottom_10.append({
            'symbol':        r['symbol'],
            'tier':          tier,
            'tier_label':    LIQUIDITY_TIERS[tier]['label'],
            'advt_10d_egp':  round(r['advt_10d'] or 0, 2),
            'score':         round(r['liquidity_score'] or 0, 2),
        })

    # --- Liquidity trend: compare advt_10d vs advt_30d ---
    adv10_vals = [r['advt_10d'] or 0 for r in rows if r['advt_10d']]
    adv30_vals = [r['advt_30d'] or 0 for r in rows if r['advt_30d']]

    avg_10 = _safe_mean(adv10_vals)
    avg_30 = _safe_mean(adv30_vals)

    if avg_30 > 0:
        trend_pct = round((avg_10 - avg_30) / avg_30 * 100, 2)
    else:
        trend_pct = 0.0

    if trend_pct > 5:
        trend_dir = 'IMPROVING'
    elif trend_pct < -5:
        trend_dir = 'DECLINING'
    else:
        trend_dir = 'STABLE'

    # --- Market-wide liquidity score ---
    scores = [r['liquidity_score'] or 0 for r in rows]
    market_score = round(_safe_mean(scores), 2)

    # --- Tier percentages ---
    total = len(rows)
    tier_pct = {
        t: round(tier_counts.get(t, 0) / total * 100, 1)
        for t in TIER_ORDER
    }

    # Tradeable = TIER1 + TIER2
    tradeable_count = tier_counts.get('TIER1', 0) + tier_counts.get('TIER2', 0)
    tradeable_pct   = round(tradeable_count / total * 100, 1) if total else 0.0

    return {
        'success':            True,
        'profiled_symbols':   total,
        'tier_distribution':  dict(tier_counts),
        'tier_percentages':   tier_pct,
        'tradeable_count':    tradeable_count,
        'tradeable_pct':      tradeable_pct,
        'top_20_liquid':      top_20,
        'bottom_10_illiquid': bottom_10,
        'liquidity_trend': {
            'avg_advt_10d_egp': round(avg_10, 2),
            'avg_advt_30d_egp': round(avg_30, 2),
            'trend_pct':        trend_pct,
            'direction':        trend_dir,
        },
        'market_liquidity_score': market_score,
    }

# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params: dict) -> dict:
    today_str = datetime.datetime.utcnow().strftime('%Y-%m-%d')

    # Step 1: Build profiles
    build_result = build_liquidity_profiles({'force_recompute': False})

    # Step 2: Full report
    report_result = liquidity_report({})

    # Step 3: Save snapshot to a JSON file alongside the DB
    snapshot_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'liquidity_snapshots')
    os.makedirs(snapshot_dir, exist_ok=True)
    snapshot_path = os.path.join(snapshot_dir, f'liquidity_{today_str}.json')

    snapshot = {
        'generated_at': datetime.datetime.utcnow().isoformat() + 'Z',
        'build_summary': build_result,
        'report':        report_result,
    }

    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    return {
        'success':       True,
        'date':          today_str,
        'snapshot_path': snapshot_path,
        'build_summary': {
            'n_computed':  build_result.get('n_computed'),
            'n_skipped':   build_result.get('n_skipped'),
            'n_errors':    build_result.get('n_errors'),
            'tier_summary': build_result.get('tier_summary'),
        },
        'report_summary': {
            'profiled_symbols':       report_result.get('profiled_symbols'),
            'tradeable_count':        report_result.get('tradeable_count'),
            'tradeable_pct':          report_result.get('tradeable_pct'),
            'market_liquidity_score': report_result.get('market_liquidity_score'),
            'liquidity_trend':        report_result.get('liquidity_trend'),
        } if report_result.get('success') else {'error': report_result.get('error')},
    }

# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'compute_symbol_liquidity': compute_symbol_liquidity,
    'tier_classification':      tier_classification,
    'liquidity_filter':         liquidity_filter,
    'max_position_size':        max_position_size,
    'build_liquidity_profiles': build_liquidity_profiles,
    'liquidity_report':         liquidity_report,
    'build_full':               build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success':           False,
            'error':             'Usage: python liquidity_microstructure.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd_name    = sys.argv[1]
    params_raw  = sys.argv[2]

    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({'success': False, 'error': f'Invalid JSON params: {exc}'}))
        sys.exit(1)

    handler = COMMANDS.get(cmd_name)
    if handler is None:
        print(json.dumps({
            'success':            False,
            'error':              f'Unknown command: {cmd_name}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as exc:
        import traceback
        print(json.dumps({
            'success':   False,
            'command':   cmd_name,
            'error':     str(exc),
            'traceback': traceback.format_exc(),
        }))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
