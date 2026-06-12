"""
realistic_backtest.py
=====================
Walk-forward backtesting engine applying realistic EGX transaction costs
to measure true alpha vs theoretical alpha.

Commands:
  backtest_symbol    -- Backtest a single symbol
  backtest_universe  -- Backtest full universe for a date range
  oos_validation     -- Train/OOS split analysis
  compare_laws       -- Gross vs net alpha per law
  law_cost_hurdle    -- Minimum gross return to break even per tier
  build_full         -- Full pipeline

Usage:
  python realistic_backtest.py <command> [<json_params>]
"""

import os
import sys
import json
import sqlite3
import datetime
import math
import statistics

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS realistic_backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT DEFAULT (date('now')),
    symbol TEXT,
    from_date TEXT,
    to_date TEXT,
    n_signals INTEGER,
    n_wins INTEGER,
    n_losses INTEGER,
    win_rate_gross REAL,
    win_rate_net REAL,
    avg_gross_return REAL,
    avg_net_return REAL,
    avg_spread_cost REAL,
    avg_commission_cost REAL,
    avg_impact_cost REAL,
    total_cost_drag REAL,
    profit_factor_gross REAL,
    profit_factor_net REAL,
    max_drawdown REAL,
    sharpe_ratio_net REAL,
    setup_filter TEXT,
    liquidity_tier TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS law_alpha_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analyzed_at TEXT DEFAULT (datetime('now')),
    law_id TEXT NOT NULL,
    law_name TEXT,
    direction TEXT,
    n_signals INTEGER,
    gross_precision REAL,
    net_precision REAL,
    avg_gross_return REAL,
    avg_net_return REAL,
    cost_drag REAL,
    survives_tier1_cost INTEGER DEFAULT 0,
    survives_tier2_cost INTEGER DEFAULT 0,
    recommended_tiers TEXT,
    grade TEXT
);
"""


def ensure_schema(conn):
    for stmt in DDL.strip().split(';'):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers: OHLCV lookups
# ---------------------------------------------------------------------------

def _date_to_ts(date_str):
    """Convert YYYY-MM-DD to midnight UTC unix timestamp."""
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    return int(dt.timestamp())


def _ts_to_date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')


def get_bars_for_symbol(conn, symbol: str, from_date: str, to_date: str):
    """Return list of ohlcv rows sorted by bar_time."""
    from_ts = _date_to_ts(from_date)
    to_ts = _date_to_ts(to_date) + 86400  # inclusive of to_date
    rows = conn.execute(
        """
        SELECT bar_time, open, high, low, close, volume
        FROM ohlcv_history
        WHERE symbol = ? AND bar_time >= ? AND bar_time <= ?
        ORDER BY bar_time ASC
        """,
        (symbol, from_ts, to_ts)
    ).fetchall()
    return rows


def get_bar_after(bars_sorted, signal_date: str, offset_days: int):
    """
    From a pre-fetched sorted bar list, return the bar that is >= offset_days
    calendar days after signal_date. Returns dict or None.
    """
    target_ts = _date_to_ts(signal_date) + offset_days * 86400
    for bar in bars_sorted:
        if bar['bar_time'] >= target_ts:
            return bar
    return None


def get_bars_map(conn, symbol):
    """Fetch all bars for a symbol, return sorted list."""
    rows = conn.execute(
        "SELECT bar_time, open, high, low, close, volume FROM ohlcv_history_execution WHERE symbol=? ORDER BY bar_time ASC",
        (symbol,)
    ).fetchall()
    return list(rows)


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

COMMISSION_ROUND_TRIP = 0.003   # 0.30%
DEFAULT_SPREAD_BPS = 150        # 150 bps fallback
DEFAULT_IMPACT_PCT = 0.001      # simplified market impact 0.1%

TIER_SPREAD_BPS = {
    'TIER1':   75,
    'TIER2':  200,
    'TIER3':  500,
    'ILLIQUID': 800,
}


def get_liquidity_row(conn, symbol: str):
    row = conn.execute(
        "SELECT * FROM liquidity_profile WHERE symbol=? ORDER BY computed_date DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    return row


def compute_cost(spread_bps, position_size_egp=50000, advt_30d=None):
    """
    Returns a dict with cost components in decimal (not percent).
    spread_bps: basis points (e.g. 125 bps)
    """
    commission = COMMISSION_ROUND_TRIP
    spread_pct = spread_bps / 10000.0

    if advt_30d and advt_30d > 0:
        order_fraction = position_size_egp / advt_30d
        impact = 0.001 * math.sqrt(max(order_fraction, 0))
    else:
        impact = DEFAULT_IMPACT_PCT

    total = commission + spread_pct + impact
    return {
        'commission': commission,
        'spread': spread_pct,
        'impact': impact,
        'total': total,
    }


def compute_realistic_return(signal_row, bars_map, liquidity_row, position_size_egp=50000):
    """
    Entry: T+1 open (day after signal)
    Exit:  T+6 close (5 trading days after entry)
    Returns dict with gross/net return, cost breakdown, or None if bars missing.
    """
    signal_date = signal_row['scan_date']

    entry_bar = get_bar_after(bars_map, signal_date, offset_days=1)
    exit_bar  = get_bar_after(bars_map, signal_date, offset_days=6)

    if entry_bar is None or exit_bar is None:
        return None
    if entry_bar['open'] is None or entry_bar['open'] == 0:
        return None
    if exit_bar['close'] is None:
        return None

    gross_return = (exit_bar['close'] - entry_bar['open']) / entry_bar['open']

    if liquidity_row:
        spread_bps = liquidity_row['bid_ask_spread_est'] or DEFAULT_SPREAD_BPS
        advt = liquidity_row['advt_30d']
    else:
        spread_bps = DEFAULT_SPREAD_BPS
        advt = None

    costs = compute_cost(spread_bps, position_size_egp, advt)
    net_return = gross_return - costs['total']

    return {
        'gross_return': gross_return,
        'net_return': net_return,
        'commission': costs['commission'],
        'spread': costs['spread'],
        'impact': costs['impact'],
        'total_cost': costs['total'],
        'is_win_gross': gross_return > 0,
        'is_win_net': net_return > 0,
        'entry_date': _ts_to_date(entry_bar['bar_time']),
        'exit_date':  _ts_to_date(exit_bar['bar_time']),
        'entry_price': entry_bar['open'],
        'exit_price':  exit_bar['close'],
    }


# ---------------------------------------------------------------------------
# Metrics aggregation
# ---------------------------------------------------------------------------

def compute_metrics(results):
    """Aggregate a list of result dicts from compute_realistic_return."""
    if not results:
        return {
            'n_signals': 0,
            'n_wins': 0,
            'n_losses': 0,
            'win_rate_gross': None,
            'win_rate_net': None,
            'avg_gross_return': None,
            'avg_net_return': None,
            'avg_spread_cost': None,
            'avg_commission_cost': None,
            'avg_impact_cost': None,
            'total_cost_drag': None,
            'profit_factor_gross': None,
            'profit_factor_net': None,
            'max_drawdown': None,
            'sharpe_ratio_net': None,
        }

    n = len(results)
    gross_returns  = [r['gross_return'] for r in results]
    net_returns    = [r['net_return']   for r in results]
    spread_costs   = [r['spread']       for r in results]
    commission_costs = [r['commission'] for r in results]
    impact_costs   = [r['impact']       for r in results]
    total_costs    = [r['total_cost']   for r in results]

    n_wins_gross = sum(1 for r in results if r['is_win_gross'])
    n_wins_net   = sum(1 for r in results if r['is_win_net'])
    n_losses     = n - n_wins_net

    win_rate_gross = n_wins_gross / n if n > 0 else None
    win_rate_net   = n_wins_net   / n if n > 0 else None

    avg_gross  = sum(gross_returns) / n
    avg_net    = sum(net_returns) / n
    avg_spread = sum(spread_costs) / n
    avg_commission = sum(commission_costs) / n
    avg_impact = sum(impact_costs) / n
    avg_cost   = sum(total_costs) / n

    # Profit factor (gross)
    gross_wins  = [r['gross_return'] for r in results if r['gross_return'] > 0]
    gross_losses = [abs(r['gross_return']) for r in results if r['gross_return'] <= 0]
    _gl_sum = sum(gross_losses)
    pf_gross = (sum(gross_wins) / _gl_sum) if _gl_sum > 0 else (999.0 if gross_wins else 0.0)

    # Profit factor (net)
    net_wins   = [r['net_return'] for r in results if r['net_return'] > 0]
    net_losses = [abs(r['net_return']) for r in results if r['net_return'] <= 0]
    _nl_sum = sum(net_losses)
    pf_net = (sum(net_wins) / _nl_sum) if _nl_sum > 0 else (999.0 if net_wins else 0.0)

    # Max drawdown (equity curve on net)
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    for nr in net_returns:
        equity *= (1 + nr)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualised, assume 250 trading days, ~5-day holding period → ~50 trades/year per signal)
    sharpe_net = None
    if len(net_returns) >= 2:
        mean_r = statistics.mean(net_returns)
        std_r  = statistics.stdev(net_returns)
        if std_r > 0:
            # Annualise: scale by sqrt(250/5) = sqrt(50)
            sharpe_net = (mean_r / std_r) * math.sqrt(50)

    return {
        'n_signals':          n,
        'n_wins':             n_wins_net,
        'n_losses':           n_losses,
        'win_rate_gross':     win_rate_gross,
        'win_rate_net':       win_rate_net,
        'avg_gross_return':   avg_gross,
        'avg_net_return':     avg_net,
        'avg_spread_cost':    avg_spread,
        'avg_commission_cost': avg_commission,
        'avg_impact_cost':    avg_impact,
        'total_cost_drag':    avg_cost,
        'profit_factor_gross': pf_gross,
        'profit_factor_net':  pf_net,
        'max_drawdown':       max_dd,
        'sharpe_ratio_net':   sharpe_net,
    }


# ---------------------------------------------------------------------------
# Command: backtest_symbol
# ---------------------------------------------------------------------------

def backtest_symbol(params):
    """
    Params: symbol (str), from_date (str, optional), to_date (str, optional),
            setup_filter (str, optional), position_size (float, optional)
    """
    symbol = params.get('symbol')
    if not symbol:
        return {'error': 'symbol is required'}

    from_date = params.get('from_date', '2021-08-01')
    to_date   = params.get('to_date',   '2026-05-06')
    setup_filter = params.get('setup_filter')
    position_size = float(params.get('position_size', 50000))

    conn = get_db()
    ensure_schema(conn)

    # Fetch signals
    query = "SELECT * FROM scans WHERE symbol=? AND scan_date BETWEEN ? AND ? AND rejected=0"
    args  = [symbol, from_date, to_date]
    if setup_filter:
        query += " AND setup_type LIKE ?"
        args.append(f'%{setup_filter}%')
    query += " ORDER BY scan_date ASC"
    signals = conn.execute(query, args).fetchall()

    if not signals:
        conn.close()
        return {
            'symbol': symbol,
            'from_date': from_date,
            'to_date': to_date,
            'n_signals': 0,
            'message': 'No signals found for this symbol/filter.',
        }

    # Fetch all bars once
    bars_map    = get_bars_map(conn, symbol)
    liq_row     = get_liquidity_row(conn, symbol)
    liq_tier    = liq_row['liquidity_tier'] if liq_row else 'UNKNOWN'

    results = []
    skipped = 0
    for sig in signals:
        r = compute_realistic_return(sig, bars_map, liq_row, position_size)
        if r is None:
            skipped += 1
            continue
        results.append(r)

    metrics = compute_metrics(results)

    # Persist
    conn.execute(
        """
        INSERT INTO realistic_backtest_results
            (symbol, from_date, to_date, n_signals, n_wins, n_losses,
             win_rate_gross, win_rate_net, avg_gross_return, avg_net_return,
             avg_spread_cost, avg_commission_cost, avg_impact_cost, total_cost_drag,
             profit_factor_gross, profit_factor_net, max_drawdown, sharpe_ratio_net,
             setup_filter, liquidity_tier, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            symbol, from_date, to_date,
            metrics['n_signals'], metrics['n_wins'], metrics['n_losses'],
            metrics['win_rate_gross'], metrics['win_rate_net'],
            metrics['avg_gross_return'], metrics['avg_net_return'],
            metrics['avg_spread_cost'], metrics['avg_commission_cost'],
            metrics['avg_impact_cost'], metrics['total_cost_drag'],
            metrics['profit_factor_gross'], metrics['profit_factor_net'],
            metrics['max_drawdown'], metrics['sharpe_ratio_net'],
            setup_filter, liq_tier,
            f'skipped={skipped} bars missing',
        )
    )
    conn.commit()
    conn.close()

    return {
        'symbol': symbol,
        'from_date': from_date,
        'to_date': to_date,
        'liquidity_tier': liq_tier,
        'skipped_missing_bars': skipped,
        **metrics,
    }


# ---------------------------------------------------------------------------
# Command: backtest_universe
# ---------------------------------------------------------------------------

def backtest_universe(params):
    """
    Params: from_date, to_date, liquidity_tier (optional filter), top_n (int)
    """
    from_date = params.get('from_date', '2021-08-01')
    to_date   = params.get('to_date',   '2026-05-06')
    tier_filter = params.get('liquidity_tier')  # optional
    top_n     = int(params.get('top_n', 999))

    conn = get_db()
    ensure_schema(conn)

    # Discover all symbols with signals
    symbol_rows = conn.execute(
        "SELECT DISTINCT symbol FROM scans WHERE scan_date BETWEEN ? AND ? AND rejected=0",
        (from_date, to_date)
    ).fetchall()

    symbols = [r['symbol'] for r in symbol_rows]

    if not symbols:
        conn.close()
        return {'error': 'No signals found in date range', 'from_date': from_date, 'to_date': to_date}

    universe_results = []
    symbol_summaries = []

    for sym in symbols[:top_n]:
        signals = conn.execute(
            "SELECT * FROM scans WHERE symbol=? AND scan_date BETWEEN ? AND ? AND rejected=0 ORDER BY scan_date ASC",
            (sym, from_date, to_date)
        ).fetchall()

        liq_row  = get_liquidity_row(conn, sym)
        liq_tier = liq_row['liquidity_tier'] if liq_row else 'UNKNOWN'

        if tier_filter and liq_tier != tier_filter:
            continue

        bars_map = get_bars_map(conn, sym)

        results = []
        for sig in signals:
            r = compute_realistic_return(sig, bars_map, liq_row)
            if r:
                results.append(r)

        if not results:
            continue

        m = compute_metrics(results)
        universe_results.extend(results)
        symbol_summaries.append({
            'symbol': sym,
            'liquidity_tier': liq_tier,
            **m,
        })

    # Universe-wide aggregate
    agg = compute_metrics(universe_results)

    # Distribution stats
    net_returns = [r['avg_net_return'] for r in symbol_summaries if r.get('avg_net_return') is not None]
    gross_returns = [r['avg_gross_return'] for r in symbol_summaries if r.get('avg_gross_return') is not None]

    distribution = {}
    if net_returns:
        distribution['net_median'] = statistics.median(net_returns)
        distribution['net_stdev']  = statistics.stdev(net_returns) if len(net_returns) > 1 else 0
        distribution['pct_positive_net'] = sum(1 for x in net_returns if x > 0) / len(net_returns)
    if gross_returns:
        distribution['gross_median'] = statistics.median(gross_returns)

    # Persist universe row
    conn.execute(
        """
        INSERT INTO realistic_backtest_results
            (symbol, from_date, to_date, n_signals, n_wins, n_losses,
             win_rate_gross, win_rate_net, avg_gross_return, avg_net_return,
             avg_spread_cost, avg_commission_cost, avg_impact_cost, total_cost_drag,
             profit_factor_gross, profit_factor_net, max_drawdown, sharpe_ratio_net,
             liquidity_tier, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            None, from_date, to_date,
            agg['n_signals'], agg['n_wins'], agg['n_losses'],
            agg['win_rate_gross'], agg['win_rate_net'],
            agg['avg_gross_return'], agg['avg_net_return'],
            agg['avg_spread_cost'], agg['avg_commission_cost'],
            agg['avg_impact_cost'], agg['total_cost_drag'],
            agg['profit_factor_gross'], agg['profit_factor_net'],
            agg['max_drawdown'], agg['sharpe_ratio_net'],
            tier_filter or 'ALL',
            f'universe={len(symbol_summaries)} symbols',
        )
    )
    conn.commit()
    conn.close()

    # Sort symbols by net return descending
    symbol_summaries.sort(key=lambda x: x.get('avg_net_return') or -99, reverse=True)

    return {
        'from_date': from_date,
        'to_date': to_date,
        'tier_filter': tier_filter or 'ALL',
        'n_symbols': len(symbol_summaries),
        'universe_aggregate': agg,
        'distribution': distribution,
        'top_symbols': symbol_summaries[:20],
        'bottom_symbols': symbol_summaries[-10:] if len(symbol_summaries) > 10 else [],
    }


# ---------------------------------------------------------------------------
# Command: oos_validation
# ---------------------------------------------------------------------------

def oos_validation(params):
    """
    Train: scan_date < 2024-01-01
    OOS:   scan_date >= 2024-01-01
    """
    train_cutoff = params.get('train_cutoff', '2024-01-01')
    oos_threshold = float(params.get('oos_pass_threshold', 0.40))

    conn = get_db()
    ensure_schema(conn)

    def _run_period(label, date_filter, extra_args):
        signals = conn.execute(
            f"SELECT * FROM scans WHERE {date_filter} AND rejected=0 ORDER BY scan_date ASC",
            extra_args
        ).fetchall()

        if not signals:
            return {'label': label, 'n_signals': 0, 'message': 'No signals'}

        results = []
        # Group by symbol for efficiency
        syms = list(set(s['symbol'] for s in signals))
        bars_cache = {}
        liq_cache  = {}
        for sym in syms:
            bars_cache[sym] = get_bars_map(conn, sym)
            liq_cache[sym]  = get_liquidity_row(conn, sym)

        for sig in signals:
            sym = sig['symbol']
            r = compute_realistic_return(sig, bars_cache[sym], liq_cache[sym])
            if r:
                results.append(r)

        m = compute_metrics(results)
        return {'label': label, **m}

    train_result = _run_period('TRAIN', "scan_date < ?", [train_cutoff])
    oos_result   = _run_period('OOS',   "scan_date >= ?", [train_cutoff])

    # Assess OOS
    oos_net_wr = oos_result.get('win_rate_net')
    is_real_alpha = oos_net_wr is not None and oos_net_wr > oos_threshold

    # Degradation
    train_net_wr = train_result.get('win_rate_net')
    degradation  = None
    if train_net_wr and oos_net_wr:
        degradation = (train_net_wr - oos_net_wr) / train_net_wr if train_net_wr != 0 else None

    verdict = 'PASS — OOS alpha is real' if is_real_alpha else 'FAIL — OOS alpha below threshold'

    conn.close()
    return {
        'train_cutoff': train_cutoff,
        'oos_pass_threshold': oos_threshold,
        'train_period': train_result,
        'oos_period':   oos_result,
        'win_rate_degradation_pct': round(degradation * 100, 2) if degradation else None,
        'is_real_alpha': is_real_alpha,
        'verdict': verdict,
    }


# ---------------------------------------------------------------------------
# Command: compare_laws
# ---------------------------------------------------------------------------

def _grade_law(net_precision, gross_precision, n_signals):
    """Assign A/B/C/D/F grade."""
    if n_signals < 5:
        return 'N/A'
    if net_precision is None:
        return 'F'
    if net_precision >= 0.65:
        return 'A'
    elif net_precision >= 0.55:
        return 'B'
    elif net_precision >= 0.45:
        return 'C'
    elif net_precision >= 0.35:
        return 'D'
    else:
        return 'F'


def compare_laws(params):
    """
    For each law in universal_laws_p16, find market_experience records
    and compute gross vs net alpha via cost model.
    """
    conn = get_db()
    ensure_schema(conn)

    laws = conn.execute("SELECT * FROM universal_laws_p16").fetchall()
    if not laws:
        conn.close()
        return {'error': 'No laws found in universal_laws_p16', 'n_laws': 0}

    # Preload liquidity profiles keyed by symbol
    liq_rows = conn.execute("SELECT * FROM liquidity_profile").fetchall()
    liq_map  = {}
    for lr in liq_rows:
        sym = lr['symbol']
        if sym not in liq_map:
            liq_map[sym] = lr

    results = []

    for law in laws:
        law_id   = law['pattern_id']
        law_name = law['pattern_name']
        direction = law['direction']

        # Fetch market experience rows for this law
        exp_rows = conn.execute(
            "SELECT * FROM market_experience WHERE law_id=? ORDER BY event_date ASC",
            (str(law_id),)
        ).fetchall()

        if not exp_rows:
            # Try matching by law_name
            exp_rows = conn.execute(
                "SELECT * FROM market_experience WHERE law_name=? ORDER BY event_date ASC",
                (law_name,)
            ).fetchall()

        n_total = len(exp_rows)
        if n_total == 0:
            # Use law's own precision stats
            gross_prec = law['precision'] if law['precision'] else 0.0
            results.append({
                'law_id': law_id,
                'law_name': law_name,
                'direction': direction,
                'n_signals': 0,
                'gross_precision': gross_prec,
                'net_precision': None,
                'avg_gross_return': None,
                'avg_net_return': None,
                'cost_drag': None,
                'survives_tier1_cost': 0,
                'survives_tier2_cost': 0,
                'recommended_tiers': json.dumps([]),
                'grade': 'N/A',
            })
            continue

        # Compute gross and net per event
        gross_returns = []
        net_returns   = []
        hits_gross    = 0
        hits_net      = 0

        tier1_net_pos = 0
        tier1_count   = 0
        tier2_net_pos = 0
        tier2_count   = 0

        for row in exp_rows:
            sym = row['symbol']
            liq = liq_map.get(sym)

            # Use next_max_return as gross return proxy
            next_ret = row['next_max_return']
            if next_ret is None:
                continue

            gross_ret = float(next_ret)

            spread_bps = liq['bid_ask_spread_est'] if liq else DEFAULT_SPREAD_BPS
            costs = compute_cost(spread_bps)
            net_ret = gross_ret - costs['total']

            gross_returns.append(gross_ret)
            net_returns.append(net_ret)

            if gross_ret > 0:
                hits_gross += 1
            if net_ret > 0:
                hits_net += 1

            tier = liq['liquidity_tier'] if liq else 'UNKNOWN'
            if tier == 'TIER1':
                tier1_count += 1
                if net_ret > 0:
                    tier1_net_pos += 1
            elif tier == 'TIER2':
                tier2_count += 1
                if net_ret > 0:
                    tier2_net_pos += 1

        n = len(gross_returns)
        if n == 0:
            gross_prec = law['precision'] if law['precision'] else 0.0
            results.append({
                'law_id': law_id,
                'law_name': law_name,
                'direction': direction,
                'n_signals': n_total,
                'gross_precision': gross_prec,
                'net_precision': None,
                'avg_gross_return': None,
                'avg_net_return': None,
                'cost_drag': None,
                'survives_tier1_cost': 0,
                'survives_tier2_cost': 0,
                'recommended_tiers': json.dumps([]),
                'grade': 'N/A',
            })
            continue

        gross_prec = hits_gross / n
        net_prec   = hits_net   / n
        avg_gross  = sum(gross_returns) / n
        avg_net    = sum(net_returns)   / n
        cost_drag  = avg_gross - avg_net

        t1_survives = (tier1_net_pos / tier1_count) > 0.50 if tier1_count > 0 else False
        t2_survives = (tier2_net_pos / tier2_count) > 0.50 if tier2_count > 0 else False

        rec_tiers = []
        if t1_survives:
            rec_tiers.append('TIER1')
        if t2_survives:
            rec_tiers.append('TIER2')

        grade = _grade_law(net_prec, gross_prec, n)

        law_result = {
            'law_id': law_id,
            'law_name': law_name,
            'direction': direction,
            'n_signals': n,
            'gross_precision': round(gross_prec, 4),
            'net_precision':   round(net_prec, 4),
            'avg_gross_return': round(avg_gross, 6),
            'avg_net_return':   round(avg_net, 6),
            'cost_drag': round(cost_drag, 6),
            'survives_tier1_cost': int(t1_survives),
            'survives_tier2_cost': int(t2_survives),
            'recommended_tiers': json.dumps(rec_tiers),
            'grade': grade,
        }
        results.append(law_result)

    # Upsert into DB
    conn.execute("DELETE FROM law_alpha_analysis")
    for r in results:
        conn.execute(
            """
            INSERT INTO law_alpha_analysis
                (law_id, law_name, direction, n_signals, gross_precision, net_precision,
                 avg_gross_return, avg_net_return, cost_drag,
                 survives_tier1_cost, survives_tier2_cost, recommended_tiers, grade)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r['law_id'], r['law_name'], r['direction'],
                r['n_signals'], r['gross_precision'], r['net_precision'],
                r['avg_gross_return'], r['avg_net_return'], r['cost_drag'],
                r['survives_tier1_cost'], r['survives_tier2_cost'],
                r['recommended_tiers'], r['grade'],
            )
        )
    conn.commit()
    conn.close()

    # Summarise
    graded = [r for r in results if r['grade'] not in ('N/A', None)]
    grade_counts = {}
    for r in graded:
        g = r['grade']
        grade_counts[g] = grade_counts.get(g, 0) + 1

    surviving_t1 = [r for r in results if r['survives_tier1_cost']]
    surviving_t2 = [r for r in results if r['survives_tier2_cost']]

    results.sort(key=lambda x: x.get('net_precision') or 0, reverse=True)

    return {
        'n_laws_analyzed': len(results),
        'grade_distribution': grade_counts,
        'n_survive_tier1_cost': len(surviving_t1),
        'n_survive_tier2_cost': len(surviving_t2),
        'top_laws_by_net_precision': results[:10],
        'bottom_laws': [r for r in results if r['grade'] == 'F'][:10],
    }


# ---------------------------------------------------------------------------
# Command: law_cost_hurdle
# ---------------------------------------------------------------------------

def law_cost_hurdle(params):
    """
    For each liquidity tier, compute minimum gross return needed to break even.
    Also computes hurdle-pass rate for each law.
    """
    # Standard position size assumption
    position_size = float(params.get('position_size', 50000))

    tiers = {
        'TIER1':   {'spread_bps': 75,  'advt_est': 5_000_000},
        'TIER2':   {'spread_bps': 200, 'advt_est': 500_000},
        'TIER3':   {'spread_bps': 500, 'advt_est': 100_000},
        'ILLIQUID':{'spread_bps': 800, 'advt_est': 20_000},
    }

    hurdles = {}
    for tier, config in tiers.items():
        costs = compute_cost(config['spread_bps'], position_size, config['advt_est'])
        hurdle_pct = costs['total'] * 100  # express as %
        hurdles[tier] = {
            'spread_bps':      config['spread_bps'],
            'commission_pct':  round(costs['commission'] * 100, 3),
            'spread_pct':      round(costs['spread'] * 100, 3),
            'impact_pct':      round(costs['impact'] * 100, 3),
            'total_cost_pct':  round(costs['total'] * 100, 3),
            'min_gross_return_to_breakeven_pct': round(hurdle_pct, 3),
            'interpretation':  (
                f"A signal on {tier} stock must deliver >{hurdle_pct:.2f}% gross return "
                f"to be net-positive after all EGX trading costs."
            ),
        }

    # Cross-reference with actual law alphas from law_alpha_analysis
    conn = get_db()
    law_hurdle_analysis = []
    try:
        laws = conn.execute(
            "SELECT law_id, law_name, avg_gross_return, avg_net_return, grade FROM law_alpha_analysis"
        ).fetchall()
        for law in laws:
            agr = law['avg_gross_return']
            law_hurdles = {}
            for tier, h in hurdles.items():
                passes = agr is not None and (agr * 100) > h['min_gross_return_to_breakeven_pct']
                law_hurdles[tier] = passes
            law_hurdle_analysis.append({
                'law_id':   law['law_id'],
                'law_name': law['law_name'],
                'avg_gross_return_pct': round(agr * 100, 3) if agr else None,
                'passes_tier1': law_hurdles.get('TIER1'),
                'passes_tier2': law_hurdles.get('TIER2'),
                'passes_tier3': law_hurdles.get('TIER3'),
                'grade': law['grade'],
            })
    except Exception:
        pass
    conn.close()

    law_hurdle_analysis.sort(key=lambda x: x.get('avg_gross_return_pct') or -999, reverse=True)

    return {
        'position_size_egp': position_size,
        'tier_cost_hurdles': hurdles,
        'law_hurdle_analysis': law_hurdle_analysis[:30],
        'summary': {
            'n_laws_checked': len(law_hurdle_analysis),
            'pass_tier1': sum(1 for x in law_hurdle_analysis if x.get('passes_tier1')),
            'pass_tier2': sum(1 for x in law_hurdle_analysis if x.get('passes_tier2')),
            'pass_tier3': sum(1 for x in law_hurdle_analysis if x.get('passes_tier3')),
        },
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params):
    from_date = params.get('from_date', '2021-08-01')
    to_date   = params.get('to_date',   '2026-05-06')

    print(json.dumps({'step': '1/4', 'action': 'backtest_universe'}), flush=True)
    universe = backtest_universe({'from_date': from_date, 'to_date': to_date})

    print(json.dumps({'step': '2/4', 'action': 'oos_validation'}), flush=True)
    oos = oos_validation({})

    print(json.dumps({'step': '3/4', 'action': 'compare_laws'}), flush=True)
    laws = compare_laws({})

    print(json.dumps({'step': '4/4', 'action': 'law_cost_hurdle'}), flush=True)
    hurdles = law_cost_hurdle({})

    return {
        'pipeline': 'build_full',
        'completed_at': datetime.datetime.utcnow().isoformat(),
        'universe_summary': {
            'n_symbols':    universe.get('n_symbols'),
            'n_signals':    universe.get('universe_aggregate', {}).get('n_signals'),
            'win_rate_net': universe.get('universe_aggregate', {}).get('win_rate_net'),
            'avg_net_return': universe.get('universe_aggregate', {}).get('avg_net_return'),
        },
        'oos_verdict': oos.get('verdict'),
        'oos_win_rate_net': oos.get('oos_period', {}).get('win_rate_net'),
        'laws_summary': {
            'n_analyzed':       laws.get('n_laws_analyzed'),
            'grade_distribution': laws.get('grade_distribution'),
            'n_survive_tier1':  laws.get('n_survive_tier1_cost'),
        },
        'tier_hurdles': {k: v['min_gross_return_to_breakeven_pct'] for k, v in hurdles.get('tier_cost_hurdles', {}).items()},
        'top_laws': laws.get('top_laws_by_net_precision', [])[:5],
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

COMMANDS = {
    'backtest_symbol':    backtest_symbol,
    'backtest_universe':  backtest_universe,
    'oos_validation':     oos_validation,
    'compare_laws':       compare_laws,
    'law_cost_hurdle':    law_cost_hurdle,
    'build_full':         build_full,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
