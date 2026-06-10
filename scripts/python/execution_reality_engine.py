"""
Execution Reality Engine — Phase 44
EGX Autonomous Quant System

Upgrades the naive 50bps commission model to a full reality-adjusted execution
cost model covering T+3 settlement dynamics, circuit breaker cascades,
depth-adjusted slippage, time-of-day effects, and ownership constraints.
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ─── DB Path ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ─── EGX Reality Parameters ───────────────────────────────────────────────────
EGX_REALITY = {
    # === Base costs ===
    'commission_bps': 50,        # 0.50% round-trip
    'base_spread_bps': 50,       # 0.50% mid-spread

    # === T+3 Settlement Effects ===
    't3_settlement_days': 3,
    't3_liquidity_discount': 0.15,        # 15% of liquidity locked in pending settlements
    'tuesday_pressure_multiplier': 1.25,  # Sunday T+3 settles Tuesday → more pressure
    'wednesday_pressure_multiplier': 1.20,

    # === Circuit Breaker Cascade ===
    'circuit_breaker_threshold': 0.10,     # 10% daily limit
    'cb_spread_multiplier_at_limit': 3.5,  # spread × 3.5 when stock near limit
    'cb_liquidity_drain': 0.40,            # 40% liquidity loss after halt

    # === Depth-Adjusted Slippage (as fraction of price) ===
    'slippage_1pct_adv':  0.0050,          # 0.5%  slippage for 1%  of ADV order
    'slippage_5pct_adv':  0.0200,          # 2.0%  slippage for 5%  of ADV order
    'slippage_10pct_adv': 0.0500,          # 5.0%  slippage for 10% of ADV order
    'slippage_20pct_adv': 0.1000,          # 10.0% slippage for 20% of ADV order

    # === Time-of-Day Effects ===
    'open_30min_spread_mult': 1.80,        # opening 30 min: wider spread
    'close_30min_liquidity_disc': 0.25,    # last 30 min: 25% less liquidity
    'midday_optimal_window': (600, 840),   # 10:00–14:00 Cairo = optimal (minutes from midnight)

    # === Ownership Constraints ===
    'max_order_pct_daily_volume': 0.05,    # cannot buy > 5% of day's volume
    'large_order_threshold_pct': 0.02,     # orders > 2% of ADV = "large"

    # === T+3 Portfolio Impact ===
    'max_simultaneous_open_trades': 8,
    'capital_locked_per_trade_days': 3,
}

# EGX trading calendar: Sunday–Thursday
EGX_TRADING_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday']

# T+3 settlement pressure map: day traded → day settlement pressure peaks
# If you buy on Sunday, settlement is Wednesday → Wednesday has extra pressure
T3_SETTLEMENT_PRESSURE = {
    'Sunday':    'Wednesday',
    'Monday':    'Thursday',
    'Tuesday':   'Sunday',    # next week
    'Wednesday': 'Monday',    # next week
    'Thursday':  'Tuesday',   # next week
}

# Reverse: which day feels pressure FROM prior trades
T3_PRESSURE_RECEIVER = {
    'Wednesday': ('Sunday',   EGX_REALITY['wednesday_pressure_multiplier']),
    'Tuesday':   ('Thursday', EGX_REALITY['tuesday_pressure_multiplier']),
    'Monday':    ('Thursday', EGX_REALITY['wednesday_pressure_multiplier']),  # prior week
}

DEFAULT_ADV = 100_000   # default average daily volume when no data available
TYPICAL_ROUND_TRIP_BPS_LOW  = 130
TYPICAL_ROUND_TRIP_BPS_HIGH = 180

# ─── DB Helper ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_reality_checks (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            law_name          TEXT,
            theoretical_eae   REAL,
            realistic_eae     REAL,
            cost_drag_bps     REAL,
            survives_reality  INTEGER,
            checked_at        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_calendar_cache (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            day                 TEXT,
            liquidity_score     REAL,
            spread_estimate_bps REAL,
            optimal_window      TEXT,
            cached_at           TEXT
        )
    """)
    conn.commit()

# ─── Slippage Interpolation ───────────────────────────────────────────────────

def interpolate_slippage(order_pct_adv: float) -> float:
    """
    Linearly interpolate slippage rate from the EGX_REALITY ADV table.
    Returns slippage as a fraction of price (not bps).
    """
    r = EGX_REALITY
    # Knots: (pct_adv, slippage_rate)
    knots = [
        (0.00,  0.0000),
        (0.01,  r['slippage_1pct_adv']),
        (0.05,  r['slippage_5pct_adv']),
        (0.10,  r['slippage_10pct_adv']),
        (0.20,  r['slippage_20pct_adv']),
        (1.00,  r['slippage_20pct_adv'] * 2.0),  # extrapolation cap
    ]
    x = max(0.0, order_pct_adv)
    for i in range(len(knots) - 1):
        x0, y0 = knots[i]
        x1, y1 = knots[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + t * (y1 - y0)
    return knots[-1][1]


# ─── ADV Lookup ───────────────────────────────────────────────────────────────

def get_adv(symbol: str) -> float:
    """
    Fetch average daily volume for symbol from DB.
    Falls back to DEFAULT_ADV if unavailable.
    """
    try:
        conn = get_db()
        # First try symbol_liquidity_profile (most accurate)
        row = conn.execute(
            "SELECT avg_daily_volume FROM symbol_liquidity_profile WHERE symbol = ?",
            (symbol,)
        ).fetchone()
        if row and row['avg_daily_volume'] and row['avg_daily_volume'] > 0:
            conn.close()
            return float(row['avg_daily_volume'])
        # Fallback: compute from ohlcv_history (last 20 bars)
        rows = conn.execute(
            "SELECT volume FROM ohlcv_history WHERE symbol = ? ORDER BY bar_time DESC LIMIT 20",
            (symbol,)
        ).fetchall()
        conn.close()
        if rows:
            vols = [r['volume'] for r in rows if r['volume'] and r['volume'] > 0]
            if vols:
                return statistics.mean(vols)
    except Exception:
        pass
    return DEFAULT_ADV


def get_recent_price_change(symbol: str) -> float:
    """
    Return today's approximate price change fraction (close/open - 1).
    Returns 0.0 if unavailable.
    """
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT open, close FROM ohlcv_history WHERE symbol = ? ORDER BY bar_time DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        conn.close()
        if row and row['open'] and row['open'] != 0:
            return (row['close'] - row['open']) / row['open']
    except Exception:
        pass
    return 0.0


# ─── Time-of-Day Multiplier ───────────────────────────────────────────────────

def time_of_day_multiplier(time_of_day: str) -> tuple:
    """
    Returns (spread_multiplier, liquidity_discount_factor, label).
    """
    tod = time_of_day.lower().strip()
    if tod in ('open', 'opening', 'open_30min', 'open30'):
        return (EGX_REALITY['open_30min_spread_mult'], 1.0, 'opening_auction')
    elif tod in ('close', 'closing', 'close_30min', 'close30', 'eod'):
        # Liquidity discount means less depth → higher effective slippage
        disc = EGX_REALITY['close_30min_liquidity_disc']
        spread_mult = 1.0 + disc  # proxy: spread widens proportionally
        return (spread_mult, 1.0 - disc, 'close_auction')
    elif tod in ('midday', 'mid', 'noon', 'optimal'):
        return (1.0, 1.0, 'midday_optimal')
    else:
        # Default: moderate intraday session
        return (1.15, 0.95, 'intraday')


# ─── Day-of-Week Multiplier ───────────────────────────────────────────────────

def day_of_week_multiplier(day_of_week: str) -> tuple:
    """
    Returns (multiplier, notes).
    """
    day = day_of_week.strip().capitalize()
    if day == 'Tuesday':
        return (EGX_REALITY['tuesday_pressure_multiplier'],
                'T+3 settlement pressure: Sunday trades settling')
    elif day == 'Wednesday':
        return (EGX_REALITY['wednesday_pressure_multiplier'],
                'T+3 settlement pressure: Monday trades settling')
    elif day == 'Sunday':
        return (1.05, 'Week open: moderate uncertainty premium')
    elif day == 'Thursday':
        return (1.10, 'Week close: position squaring pressure')
    else:
        return (1.0, '')


# ─── Circuit Breaker Check ────────────────────────────────────────────────────

def circuit_breaker_check(symbol: str, price_change: float = None) -> dict:
    """
    Returns cb_spread_mult, cb_liquidity_mult, and warning strings.
    """
    if price_change is None:
        price_change = get_recent_price_change(symbol)

    abs_change = abs(price_change)
    threshold  = EGX_REALITY['circuit_breaker_threshold']
    warnings   = []
    cb_spread_mult     = 1.0
    cb_liquidity_mult  = 1.0

    if abs_change >= threshold:
        cb_spread_mult    = EGX_REALITY['cb_spread_multiplier_at_limit']
        cb_liquidity_mult = 1.0 - EGX_REALITY['cb_liquidity_drain']
        warnings.append(
            f"CIRCUIT_BREAKER: {symbol} moved {abs_change*100:.1f}% "
            f"(threshold {threshold*100:.0f}%) — spread x{cb_spread_mult:.1f}, "
            f"liquidity -{EGX_REALITY['cb_liquidity_drain']*100:.0f}%"
        )
    elif abs_change >= threshold * 0.80:
        cb_spread_mult = 2.0
        warnings.append(
            f"CB_WARNING: {symbol} near circuit breaker ({abs_change*100:.1f}%) "
            "— elevated spread risk"
        )
    elif abs_change >= threshold * 0.60:
        cb_spread_mult = 1.50
        warnings.append(
            f"CB_ELEVATED: {symbol} at {abs_change*100:.1f}% — moderate spread widening"
        )

    return {
        'price_change_pct': round(price_change * 100, 2),
        'cb_spread_mult':    cb_spread_mult,
        'cb_liquidity_mult': cb_liquidity_mult,
        'warnings':          warnings,
    }


# ─── Ownership Check ─────────────────────────────────────────────────────────

def ownership_check(symbol: str, shares: int, adv: float) -> list:
    """
    Returns list of warning strings for ownership/volume limit violations.
    """
    warnings = []
    max_pct   = EGX_REALITY['max_order_pct_daily_volume']
    large_pct = EGX_REALITY['large_order_threshold_pct']

    if adv > 0:
        order_pct = shares / adv
        if order_pct > max_pct:
            warnings.append(
                f"OWNERSHIP_LIMIT: Order ({shares:,} shares) is {order_pct*100:.1f}% of ADV "
                f"({adv:,.0f}) — exceeds {max_pct*100:.0f}% daily volume cap. "
                "Split across multiple sessions."
            )
        elif order_pct > large_pct:
            warnings.append(
                f"LARGE_ORDER: Order is {order_pct*100:.1f}% of ADV — "
                "expect meaningful market impact."
            )
    return warnings


# ─── Command: simulate_entry ──────────────────────────────────────────────────

def simulate_entry(params: dict) -> dict:
    symbol      = params.get('symbol', 'UNKNOWN')
    price       = float(params.get('price', 0))
    shares      = int(params.get('shares', 0))
    time_of_day = params.get('time_of_day', 'midday')
    day_of_week = params.get('day_of_week', 'Monday')

    if price <= 0 or shares <= 0:
        return {'error': 'price and shares must be positive', 'feasible': False}

    notional = price * shares
    warnings = []

    # 1. Base commission (one-way: half of round-trip)
    base_commission = notional * (EGX_REALITY['commission_bps'] / 10000) / 2

    # 2. Base half-spread on entry
    base_spread_raw = notional * (EGX_REALITY['base_spread_bps'] / 10000) / 2

    # 3. Depth-adjusted slippage
    adv         = get_adv(symbol)
    order_pct   = shares / adv if adv > 0 else 0.0
    slip_rate   = interpolate_slippage(order_pct)
    slippage_cost = notional * slip_rate

    # 4. Time-of-day multiplier
    tod_spread_mult, tod_liq_mult, tod_label = time_of_day_multiplier(time_of_day)

    # 5. Day-of-week multiplier
    dow_mult, dow_note = day_of_week_multiplier(day_of_week)
    if dow_note:
        warnings.append(f"DAY_PRESSURE: {dow_note}")

    # 6. Circuit breaker check
    cb = circuit_breaker_check(symbol)
    warnings.extend(cb['warnings'])

    # Combined spread multiplier: tod x dow x cb
    final_spread_mult = tod_spread_mult * dow_mult * cb['cb_spread_mult']
    adjusted_spread   = base_spread_raw * final_spread_mult

    # Slippage also affected by liquidity conditions
    liq_multiplier    = (1.0 / tod_liq_mult) if tod_liq_mult > 0 else 1.5
    liq_multiplier   *= (1.0 / cb['cb_liquidity_mult']) if cb['cb_liquidity_mult'] > 0 else 2.0
    # Apply T+3 liquidity discount
    liq_multiplier   *= (1.0 + EGX_REALITY['t3_liquidity_discount'] * 0.5)
    adjusted_slippage = slippage_cost * liq_multiplier

    # Decompose premiums for reporting
    time_premium = base_spread_raw * (tod_spread_mult - 1.0) * dow_mult
    day_premium  = base_spread_raw * tod_spread_mult * (dow_mult - 1.0)

    total_entry_cost  = base_commission + adjusted_spread + adjusted_slippage
    entry_cost_bps    = (total_entry_cost / notional) * 10000 if notional > 0 else 0.0

    # 7. Ownership check
    warnings.extend(ownership_check(symbol, shares, adv))

    # Feasibility: entry is infeasible if cb is triggered AND order > 1% ADV
    feasible = not (cb['cb_spread_mult'] >= EGX_REALITY['cb_spread_multiplier_at_limit']
                    and order_pct > 0.01)

    return {
        'symbol':           symbol,
        'shares':           shares,
        'price':            price,
        'notional':         round(notional, 2),
        'adv':              round(adv, 0),
        'order_pct_adv':    round(order_pct * 100, 3),
        'time_of_day':      tod_label,
        'day_of_week':      day_of_week,
        'total_entry_cost': round(total_entry_cost, 2),
        'entry_cost_bps':   round(entry_cost_bps, 1),
        'components': {
            'commission':   round(base_commission, 2),
            'spread':       round(adjusted_spread, 2),
            'slippage':     round(adjusted_slippage, 2),
            'time_premium': round(time_premium, 2),
            'day_premium':  round(day_premium, 2),
        },
        'multipliers': {
            'spread': round(final_spread_mult, 3),
            'cb_spread': cb['cb_spread_mult'],
            'cb_liquidity': cb['cb_liquidity_mult'],
        },
        'warnings':  warnings,
        'feasible':  feasible,
    }


# ─── Command: simulate_exit ───────────────────────────────────────────────────

def simulate_exit(params: dict) -> dict:
    symbol      = params.get('symbol', 'UNKNOWN')
    price       = float(params.get('exit_price', params.get('price', 0)))
    shares      = int(params.get('shares', 0))
    hold_days   = int(params.get('hold_days', 3))
    time_of_day = params.get('time_of_day', 'close')
    entry_cost_bps = float(params.get('entry_cost_bps', 0))

    if price <= 0 or shares <= 0:
        return {'error': 'price and shares must be positive', 'feasible': False}

    notional = price * shares
    warnings = []

    # 1. Base commission (one-way exit)
    base_commission = notional * (EGX_REALITY['commission_bps'] / 10000) / 2

    # 2. Base half-spread on exit
    base_spread_raw = notional * (EGX_REALITY['base_spread_bps'] / 10000) / 2

    # 3. Depth-adjusted slippage — exits have 20% extra price impact reversal
    adv           = get_adv(symbol)
    order_pct     = shares / adv if adv > 0 else 0.0
    slip_rate     = interpolate_slippage(order_pct)
    slippage_cost = notional * slip_rate * 1.20   # 20% extra for exit impact reversal

    # 4. Time-of-day multiplier
    tod_spread_mult, tod_liq_mult, tod_label = time_of_day_multiplier(time_of_day)

    # 5. Circuit breaker
    cb = circuit_breaker_check(symbol)
    warnings.extend(cb['warnings'])

    # 6. Exit timing cost: closing near EOD amplifies illiquidity
    exit_timing_cost = 0.0
    if time_of_day.lower() in ('close', 'closing', 'close_30min', 'close30', 'eod'):
        disc = EGX_REALITY['close_30min_liquidity_disc']
        exit_timing_cost = notional * (disc * 0.5) * (EGX_REALITY['base_spread_bps'] / 10000)
        warnings.append(
            f"EXIT_TIMING: Closing position near EOD adds {disc*100:.0f}% liquidity discount"
        )

    # Combined spread multiplier
    final_spread_mult = tod_spread_mult * cb['cb_spread_mult']
    adjusted_spread   = base_spread_raw * final_spread_mult

    # Slippage: liquidity discount on exit
    liq_multiplier    = (1.0 / tod_liq_mult) if tod_liq_mult > 0 else 1.5
    liq_multiplier   *= (1.0 / cb['cb_liquidity_mult']) if cb['cb_liquidity_mult'] > 0 else 2.0
    adjusted_slippage = slippage_cost * liq_multiplier

    total_exit_cost = base_commission + adjusted_spread + adjusted_slippage + exit_timing_cost
    exit_cost_bps   = (total_exit_cost / notional) * 10000 if notional > 0 else 0.0

    # T+3 cycles
    t3_cycles = hold_days // EGX_REALITY['t3_settlement_days']

    # Capital locked informationally
    t3_locked_capital = notional  # full notional locked until settlement

    # Ownership check
    warnings.extend(ownership_check(symbol, shares, adv))

    # Total round-trip if entry_cost_bps provided
    total_roundtrip_bps = None
    if entry_cost_bps > 0:
        total_roundtrip_bps = round(entry_cost_bps + exit_cost_bps, 1)

    return {
        'symbol':           symbol,
        'shares':           shares,
        'exit_price':       price,
        'notional':         round(notional, 2),
        'hold_days':        hold_days,
        't3_cycles':        t3_cycles,
        't3_locked_capital': round(t3_locked_capital, 2),
        'time_of_day':      tod_label,
        'total_exit_cost':  round(total_exit_cost, 2),
        'exit_cost_bps':    round(exit_cost_bps, 1),
        'components': {
            'commission':       round(base_commission, 2),
            'spread':           round(adjusted_spread, 2),
            'slippage':         round(adjusted_slippage, 2),
            'exit_timing_cost': round(exit_timing_cost, 2),
        },
        'total_roundtrip_cost_bps': total_roundtrip_bps,
        'warnings':  warnings,
        'feasible':  True,
    }


# ─── Command: realistic_pnl ───────────────────────────────────────────────────

def realistic_pnl(params: dict) -> dict:
    symbol_filter = params.get('symbol', None)

    try:
        conn = get_db()
        if symbol_filter:
            rows = conn.execute(
                """SELECT symbol, entry_price, exit_price, position_size, hold_days,
                          pnl_pct, pnl_egp
                   FROM trades
                   WHERE symbol = ? AND entry_price IS NOT NULL AND exit_price IS NOT NULL
                   ORDER BY scan_date DESC LIMIT 200""",
                (symbol_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT symbol, entry_price, exit_price, position_size, hold_days,
                          pnl_pct, pnl_egp
                   FROM trades
                   WHERE entry_price IS NOT NULL AND exit_price IS NOT NULL
                   ORDER BY scan_date DESC LIMIT 500"""
            ).fetchall()
        conn.close()
    except Exception as e:
        return {'error': f'DB read failed: {str(e)}', 'n_trades': 0}

    if not rows:
        return {
            'error': 'No qualifying trades found',
            'n_trades': 0,
            'symbol_filter': symbol_filter,
        }

    theoretical_returns_bps = []
    realistic_returns_bps   = []
    cost_drags_bps          = []
    trades_flip_negative    = 0

    for row in rows:
        ep  = row['entry_price']  or 0.0
        xp  = row['exit_price']   or 0.0
        pos = row['position_size'] or (ep * 100)   # fallback: 100 shares
        hd  = row['hold_days']    or 3
        sym = row['symbol']

        if ep <= 0 or xp <= 0:
            continue

        # Estimate shares from position size
        shares = int(pos / ep) if ep > 0 else 100
        if shares <= 0:
            shares = 100

        # Theoretical P&L in bps
        theoretical_bps = ((xp - ep) / ep) * 10000

        # Realistic costs
        entry_sim = simulate_entry({
            'symbol':      sym,
            'price':       ep,
            'shares':      shares,
            'time_of_day': 'midday',
            'day_of_week': 'Monday',
        })
        exit_sim = simulate_exit({
            'symbol':     sym,
            'price':      xp,
            'shares':     shares,
            'hold_days':  hd,
            'time_of_day': 'midday',
        })

        entry_cost_bps = entry_sim.get('entry_cost_bps', 0.0)
        exit_cost_bps  = exit_sim.get('exit_cost_bps', 0.0)
        total_cost_bps = entry_cost_bps + exit_cost_bps

        realistic_bps  = theoretical_bps - total_cost_bps
        drag_bps       = total_cost_bps

        theoretical_returns_bps.append(theoretical_bps)
        realistic_returns_bps.append(realistic_bps)
        cost_drags_bps.append(drag_bps)

        if theoretical_bps > 0 and realistic_bps < 0:
            trades_flip_negative += 1

    n = len(theoretical_returns_bps)
    if n == 0:
        return {'error': 'Could not compute any trade costs', 'n_trades': 0}

    avg_theoretical = statistics.mean(theoretical_returns_bps)
    avg_realistic   = statistics.mean(realistic_returns_bps)
    avg_drag        = statistics.mean(cost_drags_bps)
    edge            = avg_realistic

    # Assessment
    if edge > 100:
        assessment = "STRONG_EDGE: Strategy survives realistic execution — solid alpha above costs"
    elif edge > 30:
        assessment = "MARGINAL_EDGE: Positive after costs but thin — execution discipline critical"
    elif edge > 0:
        assessment = "BARELY_VIABLE: Edge nearly consumed by costs — optimization needed"
    elif edge > -50:
        assessment = "EDGE_DESTROYED: Realistic costs eliminate the apparent edge"
    else:
        assessment = "LOSING_STRATEGY: Costs turn strategy significantly negative"

    flip_pct = (trades_flip_negative / n) * 100 if n > 0 else 0.0

    return {
        'symbol_filter':              symbol_filter,
        'n_trades':                   n,
        'avg_theoretical_return_bps': round(avg_theoretical, 1),
        'avg_realized_return_bps':    round(avg_realistic, 1),
        'avg_cost_drag_bps':          round(avg_drag, 1),
        'trades_that_flip_negative':  trades_flip_negative,
        'flip_negative_pct':          round(flip_pct, 1),
        'reality_adjusted_edge':      round(edge, 1),
        'assessment':                 assessment,
        'cost_breakdown': {
            'min_drag_bps':  round(min(cost_drags_bps), 1),
            'max_drag_bps':  round(max(cost_drags_bps), 1),
            'p25_drag_bps':  round(_percentile(cost_drags_bps, 25), 1),
            'p75_drag_bps':  round(_percentile(cost_drags_bps, 75), 1),
        },
    }


def _percentile(data: list, pct: float) -> float:
    """Simple percentile without numpy."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * pct / 100.0
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


# ─── Command: liquidity_calendar ─────────────────────────────────────────────

def liquidity_calendar(params: dict) -> dict:
    r = EGX_REALITY
    days = EGX_TRADING_DAYS  # Sun–Thu

    # Load any known catalyst events from DB
    catalyst_map = defaultdict(list)
    try:
        conn = get_db()
        # Check if catalyst_events table exists
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalyst_events'"
        ).fetchone()
        if tbl:
            cats = conn.execute(
                "SELECT event_date, event_type, symbol FROM catalyst_events LIMIT 100"
            ).fetchall()
            for c in cats:
                try:
                    dt = datetime.fromisoformat(c['event_date'])
                    day_name = dt.strftime('%A')
                    catalyst_map[day_name].append(c['event_type'])
                except Exception:
                    pass
        conn.close()
    except Exception:
        pass

    calendar = []
    for day in days:
        # Base liquidity index
        liq_index = 1.0
        spread_mult = 1.0
        notes = []

        # T+3 settlement pressure
        if day in T3_PRESSURE_RECEIVER:
            prior_day, mult = T3_PRESSURE_RECEIVER[day]
            liq_index  *= (2.0 - mult)           # pressure reduces liquidity
            spread_mult *= mult
            notes.append(f"T+3 settlement from {prior_day} trades arriving — higher pressure")

        # Sunday: week-open uncertainty
        if day == 'Sunday':
            liq_index   *= 0.88
            spread_mult *= 1.12
            notes.append("Week open — gap risk from weekend macro developments")

        # Thursday: position squaring
        if day == 'Thursday':
            liq_index   *= 0.90
            spread_mult *= 1.10
            notes.append("Week close — position squaring, reduced participation")

        # Monday/Tuesday: typically good liquidity (when no T+3 pressure)
        if day in ('Monday', 'Tuesday') and day not in T3_PRESSURE_RECEIVER:
            liq_index  *= 1.05
            notes.append("Mid-week window — generally good depth")

        # Catalyst events
        if day in catalyst_map:
            liq_index   *= 0.80
            spread_mult *= 1.30
            for evt in catalyst_map[day]:
                notes.append(f"Catalyst: {evt}")

        # Apply T+3 discount to base liquidity
        liq_index *= (1.0 - r['t3_liquidity_discount'])

        # Liquidity score 0–100
        liq_score = min(100.0, max(0.0, liq_index * 100.0))

        # Spread estimate
        base_spread = r['base_spread_bps']
        spread_est  = base_spread * spread_mult

        # Recommended max order size: inversely proportional to spread multiplier
        base_max_pct  = r['max_order_pct_daily_volume']
        rec_max_pct   = max(0.005, base_max_pct / spread_mult)

        # Optimal entry window
        if liq_score >= 80:
            opt_window = "10:00-14:00 Cairo (full midday window)"
        elif liq_score >= 60:
            opt_window = "10:30-13:30 Cairo (avoid first/last 30 min)"
        elif liq_score >= 40:
            opt_window = "11:00-13:00 Cairo (core session only)"
        else:
            opt_window = "11:30-12:30 Cairo (peak liquidity hour only)"

        calendar.append({
            'day':                          day,
            'liquidity_score':              round(liq_score, 1),
            'spread_estimate_bps':          round(spread_est, 1),
            'recommended_max_order_pct_adv': round(rec_max_pct * 100, 2),
            'optimal_entry_window':         opt_window,
            'notes':                        notes,
        })

    # Best / worst day
    best  = max(calendar, key=lambda x: x['liquidity_score'])
    worst = min(calendar, key=lambda x: x['liquidity_score'])

    return {
        'calendar':   calendar,
        'best_day':   best['day'],
        'best_score': best['liquidity_score'],
        'worst_day':  worst['day'],
        'worst_score': worst['liquidity_score'],
        'methodology': (
            'Scores reflect T+3 settlement pressure, week-open/close dynamics, '
            'and known catalyst events. 100 = maximum liquidity, 0 = no liquidity.'
        ),
    }


# ─── Command: reality_check ───────────────────────────────────────────────────

def reality_check(params: dict) -> dict:
    # Load all law grades
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT law_id, law_name, eae, grade, recommendation FROM law_grades ORDER BY eae DESC"
        ).fetchall()
        conn.close()
    except Exception as e:
        return {'error': f'Cannot read law_grades: {str(e)}', 'n_laws': 0}

    if not rows:
        return {'error': 'law_grades table is empty', 'n_laws': 0}

    # Typical cost drag: midpoint of realistic range
    typical_cost_drag = (TYPICAL_ROUND_TRIP_BPS_LOW + TYPICAL_ROUND_TRIP_BPS_HIGH) / 2.0

    survived  = []
    killed    = []
    all_drags = []

    for row in rows:
        law_name        = row['law_name'] or row['law_id'] or 'unknown'
        theoretical_eae = row['eae'] or 0.0

        # Cost drag depends on edge size — larger-edge laws have slightly better execution
        if abs(theoretical_eae) < 50:
            cost_drag = typical_cost_drag           # full drag on marginal laws
        elif abs(theoretical_eae) < 200:
            cost_drag = typical_cost_drag * 0.90    # slightly better execution at scale
        else:
            cost_drag = typical_cost_drag * 0.80    # best achievable for large-edge laws

        realistic_eae = theoretical_eae - cost_drag
        all_drags.append(cost_drag)

        entry = {
            'law_name':        law_name,
            'theoretical_eae': round(theoretical_eae, 1),
            'realistic_eae':   round(realistic_eae, 1),
            'cost_drag_bps':   round(cost_drag, 1),
            'grade':           row['grade'] or 'N/A',
        }
        if realistic_eae > 0:
            entry['margin']   = round(realistic_eae, 1)
            entry['verdict']  = 'SURVIVES_REALITY'
            survived.append(entry)
        else:
            entry['shortfall_bps'] = round(abs(realistic_eae), 1)
            entry['verdict']       = 'REALITY_KILLS_EDGE'
            killed.append(entry)

    n_total    = len(rows)
    n_survived = len(survived)
    n_killed   = len(killed)
    avg_drag   = statistics.mean(all_drags) if all_drags else 0.0
    survival_rate = (n_survived / n_total) * 100 if n_total > 0 else 0.0

    # Sort by realism
    survived.sort(key=lambda x: x['realistic_eae'], reverse=True)
    killed.sort(key=lambda x: x['shortfall_bps'])

    # Overall assessment
    if survival_rate >= 70:
        assessment = "ROBUST_SYSTEM: Majority of laws retain positive edge after full execution costs"
    elif survival_rate >= 50:
        assessment = "MODERATE_VIABILITY: Half the laws survive — focus on top quintile only"
    elif survival_rate >= 30:
        assessment = "MARGINAL_SYSTEM: Only strong laws survive reality — need cost reduction or edge enhancement"
    elif survival_rate >= 10:
        assessment = "WEAK_SYSTEM: Very few laws survive — execution costs nearly eliminate all alpha"
    else:
        assessment = "COST_DOMINATED: Laws have insufficient edge to cover realistic EGX execution costs"

    return {
        'n_laws':                n_total,
        'n_survive_reality':     n_survived,
        'n_killed_by_costs':     n_killed,
        'avg_cost_drag_bps':     round(avg_drag, 1),
        'reality_survival_rate': round(survival_rate, 1),
        'typical_roundtrip_range_bps': f"{TYPICAL_ROUND_TRIP_BPS_LOW}-{TYPICAL_ROUND_TRIP_BPS_HIGH}",
        'best_surviving_laws':   survived[:10],
        'laws_killed':           killed[:10],
        'reality_assessment':    assessment,
    }


# ─── Command: build_full ─────────────────────────────────────────────────────

def build_full(params: dict) -> dict:
    # Run both sub-commands
    rc = reality_check(params)
    lc = liquidity_calendar(params)

    if 'error' in rc:
        return {'status': 'error', 'detail': rc['error']}

    now = datetime.utcnow().isoformat()

    try:
        conn = get_db()
        ensure_tables(conn)

        # Clear old records before inserting fresh data
        conn.execute("DELETE FROM execution_reality_checks")
        conn.execute("DELETE FROM liquidity_calendar_cache")

        # Insert law reality checks — survived + killed
        surviving = rc.get('best_surviving_laws', [])
        killed    = rc.get('laws_killed', [])
        all_laws  = [(law, 1) for law in surviving] + [(law, 0) for law in killed]

        for law_dict, survives in all_laws:
            conn.execute(
                """INSERT INTO execution_reality_checks
                   (law_name, theoretical_eae, realistic_eae, cost_drag_bps, survives_reality, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    law_dict.get('law_name', 'unknown'),
                    law_dict.get('theoretical_eae', 0.0),
                    law_dict.get('realistic_eae', 0.0),
                    law_dict.get('cost_drag_bps', 0.0),
                    survives,
                    now,
                )
            )

        # Insert liquidity calendar
        for day_entry in lc.get('calendar', []):
            conn.execute(
                """INSERT INTO liquidity_calendar_cache
                   (day, liquidity_score, spread_estimate_bps, optimal_window, cached_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    day_entry['day'],
                    day_entry['liquidity_score'],
                    day_entry['spread_estimate_bps'],
                    day_entry['optimal_entry_window'],
                    now,
                )
            )

        conn.commit()
        conn.close()
        db_status = 'saved'
    except Exception as e:
        db_status = f'db_error: {str(e)}'

    return {
        'status':             'built',
        'db_status':          db_status,
        'n_laws_checked':     rc.get('n_laws', 0),
        'n_survive_reality':  rc.get('n_survive_reality', 0),
        'n_killed_by_costs':  rc.get('n_killed_by_costs', 0),
        'survival_rate':      rc.get('reality_survival_rate', 0.0),
        'avg_cost_drag_bps':  rc.get('avg_cost_drag_bps', 0.0),
        'best_day':           lc.get('best_day', 'N/A'),
        'worst_day':          lc.get('worst_day', 'N/A'),
        'reality_assessment': rc.get('reality_assessment', ''),
        'checked_at':         now,
        'top_surviving_laws': rc.get('best_surviving_laws', [])[:5],
        'top_killed_laws':    rc.get('laws_killed', [])[:5],
        'liquidity_calendar': lc.get('calendar', []),
    }


# ─── Dispatch ─────────────────────────────────────────────────────────────────

COMMANDS = {
    'simulate_entry':     simulate_entry,
    'simulate_exit':      simulate_exit,
    'realistic_pnl':      realistic_pnl,
    'liquidity_calendar': liquidity_calendar,
    'reality_check':      reality_check,
    'build_full':         build_full,
}


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'Usage: execution_reality_engine.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {str(e)}'}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({
            'error': f'Unknown command: {cmd}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        result = {'error': f'Command failed: {str(e)}', 'command': cmd}

    print(json.dumps(result))
