#!/usr/bin/env python3
"""
RL Environment & Backtesting Engine — Phase 18
===============================================
Builds a 40-dimensional state vector per trading day, runs vectorbt-based
backtesting, walk-forward validation, threshold optimization, and
performance reporting.

Commands (sys.argv[1]):
  build_state_vector   — construct 40-dim state for all stocks/days
  backtest_strategy    — run signal-based strategy with vectorbt
  walk_forward         — rolling-window IS/OOS validation
  optimize_thresholds  — scipy differential_evolution for optimal thresholds
  performance_report   — full backtest report: Sharpe, drawdown, win rate

State vector features (40 dims):
  [0]  RSI 5d                [1]  RSI 14d
  [2]  BB width              [3]  BB position (% of band)
  [4]  Volume ratio 20d      [5]  Volume ratio 5d
  [6]  Momentum 1d           [7]  Momentum 5d
  [8]  Momentum 20d          [9]  ATR ratio (current/20d avg)
  [10] Market regime         [11] Sector sync score
  [12] Law activation count  [13] Stock DNA archetype score
  [14] KG centrality         [15] Macro stress score
  [16] Phase 15 confidence   [17] Close normalized
  [18] High-Low range        [19] Gap (open vs prev close)
  [20] Trend score (EMA20/EMA50 ratio) [21] Support distance
  [22] Resistance distance   [23] OBV trend (3d)
  [24] Beta to EGX30         [25] Sector return 1d
  [26] Sector return 5d      [27] Breadth (% green stocks today)
  [28] Volatility percentile [29] Liquidity score
  [30-39] Padding / future features (zeros)
"""

import json, sys, time, sqlite3, math
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

DATA    = Path(__file__).parent.parent.parent / 'data'
DB_PATH = str(DATA / 'egx_trading.db')

# ─── Library imports with graceful fallback ──────────────────────────────────

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False

try:
    import pandas as pd
    HAS_PD = True
except ImportError:
    HAS_PD = False

try:
    import vectorbt as vbt
    HAS_VBT = True
except ImportError:
    HAS_VBT = False

try:
    from scipy.optimize import differential_evolution
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def ensure_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS rl_state_vectors (
            symbol TEXT, bar_time INTEGER,
            state_json TEXT, signal INTEGER,
            updated_at TEXT,
            PRIMARY KEY (symbol, bar_time)
        );
        CREATE TABLE IF NOT EXISTS backtest_results (
            symbol TEXT, strategy TEXT,
            start_date TEXT, end_date TEXT,
            total_return REAL, sharpe_ratio REAL,
            max_drawdown REAL, win_rate REAL,
            profit_factor REAL, n_trades INTEGER,
            updated_at TEXT,
            PRIMARY KEY (symbol, strategy, start_date)
        );
        CREATE TABLE IF NOT EXISTS walk_forward_results (
            window_id INTEGER, train_start TEXT, train_end TEXT,
            test_start TEXT, test_end TEXT,
            is_sharpe REAL, oos_sharpe REAL,
            is_return REAL, oos_return REAL,
            n_test_trades INTEGER, updated_at TEXT,
            PRIMARY KEY (window_id)
        );
    """)
    con.commit()

# ─── Data loading ─────────────────────────────────────────────────────────────

def load_ohlcv_all(con, start_ts=None, end_ts=None):
    """Load all OHLCV data, optionally filtered by time range."""
    query = "SELECT symbol, bar_time, open, high, low, close, volume FROM ohlcv_history_execution"
    filters = []
    args = []
    if start_ts:
        filters.append("bar_time >= ?")
        args.append(start_ts)
    if end_ts:
        filters.append("bar_time <= ?")
        args.append(end_ts)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY symbol, bar_time"
    rows = con.execute(query, args).fetchall()
    return rows

def load_indicators_cache(con):
    """Load latest indicators for each stock."""
    rows = con.execute("""
        SELECT symbol, rsi, rsi_5, bb_width, bb_pct, vol_ratio_20, momentum_1d,
               momentum_5d, momentum_20d, atr_ratio, regime
        FROM indicators_cache
    """).fetchall()
    return {r['symbol']: dict(r) for r in rows}

def load_regime_history(con):
    """Load market regime per date from regime_history table if available."""
    try:
        rows = con.execute(
            "SELECT bar_time, regime FROM regime_history ORDER BY bar_time"
        ).fetchall()
        return {r['bar_time']: r['regime'] for r in rows}
    except Exception:
        return {}

def load_precursor_patterns(con):
    """Count currently active precursor patterns per stock."""
    try:
        rows = con.execute("""
            SELECT symbol, COUNT(*) as n_active
            FROM precursor_patterns
            WHERE active = 1
            GROUP BY symbol
        """).fetchall()
        return {r['symbol']: int(r['n_active']) for r in rows}
    except Exception:
        return {}

def load_stock_dna(con):
    """Load stock DNA archetypes."""
    try:
        rows = con.execute(
            "SELECT symbol, archetype FROM stock_dna"
        ).fetchall()
        return {r['symbol']: r['archetype'] for r in rows}
    except Exception:
        return {}

def load_stock_centrality(con):
    """Load PageRank from stock_centrality."""
    try:
        rows = con.execute(
            "SELECT symbol, pagerank FROM stock_centrality"
        ).fetchall()
        return {r['symbol']: float(r['pagerank']) for r in rows}
    except Exception:
        return {}

def load_universal_laws(con):
    """Load Phase 16 universal laws confidence per stock."""
    try:
        rows = con.execute("""
            SELECT symbol, AVG(confidence) as avg_conf
            FROM universal_laws_p16
            GROUP BY symbol
        """).fetchall()
        return {r['symbol']: float(r['avg_conf']) for r in rows}
    except Exception:
        return {}

def load_macro(con):
    """Load latest macro data: CBE rate, inflation."""
    try:
        row = con.execute("""
            SELECT cbe_rate, inflation_rate
            FROM macro_data
            ORDER BY bar_time DESC LIMIT 1
        """).fetchone()
        if row:
            return {'cbe_rate': float(row['cbe_rate'] or 12.0),
                    'inflation': float(row['inflation_rate'] or 0.25)}
    except Exception:
        pass
    return {'cbe_rate': 12.0, 'inflation': 0.25}

# ─── State vector builder ─────────────────────────────────────────────────────

ARCHETYPE_SCORES = {
    'MOMENTUM':      1.0,
    'EXPLOSIVE':     0.8,
    'ACCUMULATOR':   0.6,
    'NEUTRAL':       0.4,
    'MEAN_REVERTER': 0.2,
    'VOLATILE':      0.1,
}

REGIME_SCORES = {
    'BULL':    1.0,
    'TRENDING_UP': 1.0,
    'CHOPPY':  0.5,
    'RANGING': 0.5,
    'BEAR':    0.0,
    'TRENDING_DOWN': 0.0,
    'UNKNOWN': 0.25,
}

def build_state_for_symbol(symbol, ohlcv_rows, indicators, active_patterns,
                            stock_dna, centrality, macro, regime_score, law_conf):
    """
    Build a 40-dimensional state vector for a symbol using all available data.
    Returns list of (bar_time, state_vector_40, signal) tuples.
    """
    if len(ohlcv_rows) < 21:
        return []

    # Sort by bar_time
    rows = sorted(ohlcv_rows, key=lambda x: x['bar_time'])
    closes = [float(r['close']) for r in rows]
    highs  = [float(r['high'])  for r in rows]
    lows   = [float(r['low'])   for r in rows]
    vols   = [float(r['volume']) for r in rows]
    opens  = [float(r['open'])  for r in rows]
    times  = [r['bar_time']     for r in rows]

    n = len(closes)
    results = []

    for i in range(20, n):
        c_slice = closes[max(0, i-19):i+1]
        h_slice = highs[max(0, i-19):i+1]
        l_slice = lows[max(0, i-19):i+1]
        v_slice = vols[max(0, i-19):i+1]

        # RSI 14d
        rsi14 = _compute_rsi(c_slice, 14)
        # RSI 5d
        rsi5  = _compute_rsi(c_slice[-6:], 5) if len(c_slice) >= 6 else 0.5

        # BB width
        c_20 = c_slice
        mean_20 = sum(c_20) / len(c_20)
        std_20  = _std(c_20)
        bb_width = (std_20 * 2) / mean_20 if mean_20 > 0 else 0
        bb_pct   = (closes[i] - (mean_20 - 2*std_20)) / (4*std_20) if std_20 > 0 else 0.5
        bb_pct   = max(0.0, min(1.0, bb_pct))

        # Volume ratio
        avg_vol_20 = sum(v_slice) / len(v_slice)
        vol_ratio_20 = v_slice[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
        avg_vol_5  = sum(v_slice[-5:]) / 5 if len(v_slice) >= 5 else avg_vol_20
        vol_ratio_5 = v_slice[-1] / avg_vol_5 if avg_vol_5 > 0 else 1.0

        # Momentum
        mom_1d  = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] > 0 else 0
        mom_5d  = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 and closes[i-5] > 0 else 0
        mom_20d = (closes[i] - closes[i-20]) / closes[i-20] if i >= 20 and closes[i-20] > 0 else 0

        # ATR ratio
        atrs = []
        for k in range(max(1, i-19), i+1):
            tr = max(highs[k] - lows[k],
                     abs(highs[k] - closes[k-1]),
                     abs(lows[k] - closes[k-1]))
            atrs.append(tr)
        atr_20 = sum(atrs) / len(atrs) if atrs else 0
        atr_curr = atrs[-1] if atrs else 0
        atr_ratio = atr_curr / atr_20 if atr_20 > 0 else 1.0

        # EMA trend
        ema20 = _ema(c_slice, 20)
        ema50_slice = closes[max(0, i-49):i+1]
        ema50 = _ema(ema50_slice, min(50, len(ema50_slice)))
        trend_score = ema20 / ema50 if ema50 > 0 else 1.0
        trend_score = max(0.5, min(1.5, trend_score)) - 0.5  # normalize 0-1

        # Gap
        gap = (opens[i] - closes[i-1]) / closes[i-1] if closes[i-1] > 0 else 0

        # HL range
        hl_range = (highs[i] - lows[i]) / closes[i] if closes[i] > 0 else 0

        # Volatility percentile (simple: atr_ratio normalized)
        vol_pct = min(1.0, atr_ratio / 3.0)

        # Liquidity score (volume / median volume as proxy)
        sorted_vols = sorted(v_slice)
        median_vol  = sorted_vols[len(sorted_vols)//2]
        liq_score   = min(1.0, vols[i] / max(1, median_vol))

        # Use indicators cache if available
        ind = indicators.get(symbol, {})
        rsi14_cached   = ind.get('rsi',           rsi14)
        rsi5_cached    = ind.get('rsi_5',          rsi5)
        bb_w_cached    = ind.get('bb_width',       bb_width)
        bb_pct_cached  = ind.get('bb_pct',         bb_pct)
        vol20_cached   = ind.get('vol_ratio_20',   vol_ratio_20)
        mom1d_cached   = ind.get('momentum_1d',    mom_1d)
        mom5d_cached   = ind.get('momentum_5d',    mom_5d)
        mom20d_cached  = ind.get('momentum_20d',   mom_20d)
        atr_r_cached   = ind.get('atr_ratio',      atr_ratio)

        # Encode regime
        regime_str     = ind.get('regime', 'UNKNOWN')
        reg_score      = REGIME_SCORES.get(str(regime_str).upper(), 0.25)
        if regime_score != 0.25:
            reg_score  = regime_score  # override with market-level regime

        # Macro stress
        macro_stress = min(1.0, (macro['cbe_rate'] - 5.0) / 20.0 +
                                (macro['inflation'] - 0.05) / 0.5)
        macro_stress = max(0.0, min(1.0, macro_stress))

        # Stock DNA
        archetype   = stock_dna.get(symbol, 'NEUTRAL')
        dna_score   = ARCHETYPE_SCORES.get(str(archetype).upper(), 0.4)

        # KG centrality
        kg_centrality = centrality.get(symbol, 0.5)
        kg_centrality = max(0.0, min(1.0, kg_centrality * 100))  # normalize PageRank

        # Law activation
        n_patterns = float(active_patterns.get(symbol, 0))
        n_patterns_norm = min(1.0, n_patterns / 10.0)

        # Phase 15 confidence
        p15_conf = law_conf.get(symbol, 0.5)

        # Sector return (not available per bar — use momentum as proxy)
        sector_ret_1d = mom_1d  # placeholder
        sector_ret_5d = mom_5d  # placeholder

        # Market breadth (not available per bar — approximate)
        breadth = 0.5  # neutral placeholder

        # Normalize RSI to 0-1
        rsi14_n = float(rsi14_cached or rsi14) / 100.0
        rsi5_n  = float(rsi5_cached  or rsi5)  / 100.0

        # Support / resistance distance (simple: % from 20d low/high)
        low_20  = min(l_slice)
        high_20 = max(h_slice)
        c = closes[i]
        support_dist    = (c - low_20) / c if c > 0 else 0
        resistance_dist = (high_20 - c) / c if c > 0 else 0

        # Clamp all to float
        def f(x):
            if x is None or (isinstance(x, float) and math.isnan(x)):
                return 0.0
            return float(max(-10.0, min(10.0, x)))

        state = [
            f(rsi5_n),              # 0
            f(rsi14_n),             # 1
            f(bb_w_cached),         # 2
            f(bb_pct_cached),       # 3
            f(vol20_cached),        # 4
            f(vol_ratio_5),         # 5
            f(mom1d_cached),        # 6
            f(mom5d_cached),        # 7
            f(mom20d_cached),       # 8
            f(atr_r_cached),        # 9
            f(reg_score),           # 10
            f(sector_ret_1d),       # 11 sector sync (proxy)
            f(n_patterns_norm),     # 12
            f(dna_score),           # 13
            f(kg_centrality),       # 14
            f(macro_stress),        # 15
            f(p15_conf),            # 16
            f(closes[i] / max(1, mean_20) - 1),  # 17 close normalized
            f(hl_range),            # 18
            f(gap),                 # 19
            f(trend_score),         # 20
            f(support_dist),        # 21
            f(resistance_dist),     # 22
            f(sum(v_slice[-3:]) / max(1, sum(v_slice[-6:-3])) - 1),  # 23 OBV trend proxy
            f(vol_pct),             # 24 beta proxy (vol pct)
            f(sector_ret_1d),       # 25
            f(sector_ret_5d),       # 26
            f(breadth),             # 27
            f(vol_pct),             # 28
            f(liq_score),           # 29
            0.0, 0.0, 0.0, 0.0, 0.0,  # 30-34
            0.0, 0.0, 0.0, 0.0, 0.0,  # 35-39
        ]

        # Signal logic:
        # BUY when law_activation >= 2 AND regime != BEAR AND rsi < 65
        rsi14_raw = float(rsi14_cached or rsi14)
        regime_is_bear = reg_score == 0.0
        buy_signal  = (n_patterns >= 2 and not regime_is_bear and rsi14_raw < 65)
        sell_signal = (rsi14_raw > 75 or regime_is_bear or n_patterns == 0)
        signal = 1 if buy_signal else (-1 if sell_signal else 0)

        results.append((times[i], state, signal))

    return results

def _compute_rsi(closes, period=14):
    """Compute RSI from a list of closes. Returns value 0-100."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    if not gains:
        return 50.0
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _ema(closes, period):
    if not closes or period <= 0:
        return closes[-1] if closes else 0.0
    k = 2.0 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

def _std(xs):
    if not xs:
        return 0.0
    m = sum(xs) / len(xs)
    v = sum((x - m)**2 for x in xs) / len(xs)
    return math.sqrt(v)

# ─── Command: build_state_vector ─────────────────────────────────────────────

def cmd_build_state_vector(params):
    t0 = time.time()
    print('[Phase 18] Building state vectors...', flush=True)
    con = get_connection()
    ensure_schema(con)

    rows = load_ohlcv_all(con)
    indicators  = load_indicators_cache(con)
    patterns    = load_precursor_patterns(con)
    dna         = load_stock_dna(con)
    centrality  = load_stock_centrality(con)
    macro       = load_macro(con)
    law_conf    = load_universal_laws(con)
    regime_hist = load_regime_history(con)

    # Group by symbol
    by_symbol = defaultdict(list)
    for r in rows:
        by_symbol[r['symbol']].append(r)

    n_total   = 0
    now       = datetime.utcnow().isoformat()
    sample_states = []

    for symbol, sym_rows in by_symbol.items():
        # Get market regime at each bar time
        states = build_state_for_symbol(
            symbol, sym_rows, indicators, patterns, dna, centrality, macro, 0.25, law_conf
        )
        for bar_time, state, signal in states:
            con.execute("""
                INSERT OR REPLACE INTO rl_state_vectors
                (symbol, bar_time, state_json, signal, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, bar_time, json.dumps(state), signal, now))
            n_total += 1
            if len(sample_states) < 3:
                sample_states.append({
                    'symbol': symbol, 'bar_time': bar_time,
                    'signal': signal, 'state_dim': len(state),
                    'state_sample': state[:5]
                })

        if n_total % 5000 == 0:
            print(f'[Phase 18] Saved {n_total} state vectors...', flush=True)
            con.commit()

    con.commit()
    con.close()

    return {
        'n_vectors_built':  n_total,
        'n_symbols':        len(by_symbol),
        'state_dimensions': 40,
        'sample_states':    sample_states,
        'elapsed':          round(time.time() - t0, 2)
    }

# ─── Command: backtest_strategy ──────────────────────────────────────────────

def cmd_backtest_strategy(params):
    t0 = time.time()
    symbol = params.get('symbol', None)
    print('[Phase 18] Running backtest...', flush=True)
    con = get_connection()
    ensure_schema(con)

    if not HAS_PD:
        return {'error': 'pandas not installed', 'elapsed': round(time.time()-t0, 2)}

    # Load signals from rl_state_vectors
    query = "SELECT symbol, bar_time, signal FROM rl_state_vectors"
    args  = []
    if symbol:
        query += " WHERE symbol = ?"
        args.append(symbol)
    query += " ORDER BY symbol, bar_time"
    sig_rows = con.execute(query, args).fetchall()

    if not sig_rows:
        return {'error': 'No state vectors found. Run build_state_vector first.',
                'elapsed': round(time.time()-t0, 2)}

    # Build close prices per symbol
    ohlcv_q = "SELECT symbol, bar_time, close FROM ohlcv_history_execution ORDER BY symbol, bar_time"
    ohlcv_rows = con.execute(ohlcv_q).fetchall()
    close_map = defaultdict(dict)
    for r in ohlcv_rows:
        close_map[r['symbol']][r['bar_time']] = float(r['close'])

    # Process per symbol
    portfolio_results = []
    all_symbols_tested = []

    symbols_to_test = list(set(r['symbol'] for r in sig_rows))
    if symbol:
        symbols_to_test = [symbol]
    else:
        symbols_to_test = symbols_to_test[:30]  # cap for speed

    now = datetime.utcnow().isoformat()

    for sym in symbols_to_test:
        sym_sigs  = [(r['bar_time'], r['signal'])
                     for r in sig_rows if r['symbol'] == sym]
        sym_sigs.sort(key=lambda x: x[0])

        closes_map = close_map.get(sym, {})
        if len(closes_map) < 30:
            continue

        # Build aligned arrays
        all_times = sorted(closes_map.keys())
        closes_arr = [closes_map[t] for t in all_times]
        sig_dict   = dict(sym_sigs)
        sigs_arr   = [sig_dict.get(t, 0) for t in all_times]

        if HAS_VBT and len(closes_arr) >= 30:
            try:
                close_series = pd.Series(closes_arr, name=sym)
                entries  = pd.Series([s == 1  for s in sigs_arr], dtype=bool)
                exits    = pd.Series([s == -1 for s in sigs_arr], dtype=bool)
                pf = vbt.Portfolio.from_signals(
                    close_series, entries, exits,
                    init_cash=100_000,
                    fees=0.001,    # 0.1% per trade
                    slippage=0.001,
                    freq='D'
                )
                stats = pf.stats()
                total_return  = float(stats.get('Total Return [%]', 0) / 100)
                sharpe        = float(stats.get('Sharpe Ratio', 0) or 0)
                max_dd        = float(stats.get('Max Drawdown [%]', 0) / 100)
                n_trades      = int(stats.get('Total Trades', 0) or 0)
                win_rate_raw  = float(stats.get('Win Rate [%]', 50) or 50)
                win_rate      = win_rate_raw / 100
                profit_factor = float(stats.get('Profit Factor', 1.0) or 1.0)
            except Exception as ve:
                # Fallback to manual calculation
                total_return, sharpe, max_dd, n_trades, win_rate, profit_factor = \
                    _manual_backtest(closes_arr, sigs_arr)
        else:
            total_return, sharpe, max_dd, n_trades, win_rate, profit_factor = \
                _manual_backtest(closes_arr, sigs_arr)

        start_date = datetime.utcfromtimestamp(all_times[0]).strftime('%Y-%m-%d') \
            if all_times else ''
        end_date   = datetime.utcfromtimestamp(all_times[-1]).strftime('%Y-%m-%d') \
            if all_times else ''

        con.execute("""
            INSERT OR REPLACE INTO backtest_results
            (symbol, strategy, start_date, end_date,
             total_return, sharpe_ratio, max_drawdown, win_rate,
             profit_factor, n_trades, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sym, 'phase18_signal', start_date, end_date,
              float(total_return), float(sharpe), float(max_dd),
              float(win_rate), float(profit_factor), int(n_trades), now))

        portfolio_results.append({
            'symbol':        sym,
            'total_return':  round(float(total_return), 4),
            'sharpe_ratio':  round(float(sharpe), 3),
            'max_drawdown':  round(float(max_dd), 4),
            'win_rate':      round(float(win_rate), 3),
            'profit_factor': round(float(profit_factor), 3),
            'n_trades':      int(n_trades),
        })
        all_symbols_tested.append(sym)

    con.commit()
    con.close()

    portfolio_results.sort(key=lambda x: -x['sharpe_ratio'])
    avg_sharpe = sum(r['sharpe_ratio'] for r in portfolio_results) / max(1, len(portfolio_results))
    avg_return = sum(r['total_return'] for r in portfolio_results) / max(1, len(portfolio_results))

    return {
        'n_symbols_tested':  len(portfolio_results),
        'avg_sharpe':        round(avg_sharpe, 3),
        'avg_return':        round(avg_return, 4),
        'top_stocks':        portfolio_results[:10],
        'worst_stocks':      portfolio_results[-5:] if len(portfolio_results) >= 5 else [],
        'vectorbt_used':     HAS_VBT,
        'elapsed':           round(time.time() - t0, 2)
    }

def _manual_backtest(closes, signals):
    """Simple manual backtest fallback when vectorbt unavailable."""
    capital = 100_000.0
    position = 0
    entry_price = 0.0
    trades = []
    equity = [capital]

    for i, (c, sig) in enumerate(zip(closes, signals)):
        if sig == 1 and position == 0:
            position   = capital / c
            entry_price = c
        elif (sig == -1 or i == len(closes)-1) and position > 0:
            exit_price = c
            pnl = position * (exit_price - entry_price) * 0.999  # fee
            capital   += pnl
            trades.append(pnl)
            position   = 0
            entry_price = 0.0
        equity.append(capital)

    if not trades:
        return 0.0, 0.0, 0.0, 0, 0.5, 1.0

    total_return   = (capital - 100_000.0) / 100_000.0
    winning_trades = [t for t in trades if t > 0]
    losing_trades  = [t for t in trades if t < 0]
    win_rate       = len(winning_trades) / len(trades)
    avg_win        = sum(winning_trades) / max(1, len(winning_trades))
    avg_loss       = abs(sum(losing_trades) / max(1, len(losing_trades)))
    profit_factor  = avg_win / avg_loss if avg_loss > 0 else 2.0

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd

    # Sharpe (daily returns of equity)
    rets = [(equity[i] - equity[i-1]) / equity[i-1]
            for i in range(1, len(equity)) if equity[i-1] > 0]
    if rets:
        mean_r = sum(rets) / len(rets)
        std_r  = _std(rets)
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    return total_return, sharpe, max_dd, len(trades), win_rate, profit_factor

# ─── Command: walk_forward ────────────────────────────────────────────────────

def cmd_walk_forward(params):
    t0 = time.time()
    print('[Phase 18] Running walk-forward validation...', flush=True)

    if not HAS_PD:
        return {'error': 'pandas not installed', 'elapsed': round(time.time()-t0, 2)}

    con = get_connection()
    ensure_schema(con)

    # Define windows (unix timestamps for year boundaries)
    def ts(year, month=1, day=1):
        return int(datetime(year, month, day).timestamp())

    windows = [
        {'id': 1, 'train_start': ts(2021,1,1), 'train_end': ts(2022,1,1),
         'test_start': ts(2022,1,1), 'test_end': ts(2023,1,1)},
        {'id': 2, 'train_start': ts(2022,1,1), 'train_end': ts(2023,1,1),
         'test_start': ts(2023,1,1), 'test_end': ts(2024,1,1)},
        {'id': 3, 'train_start': ts(2023,1,1), 'train_end': ts(2024,1,1),
         'test_start': ts(2024,1,1), 'test_end': ts(2025,1,1)},
    ]

    # Load all OHLCV
    ohlcv_rows = load_ohlcv_all(con)
    close_map  = defaultdict(dict)
    for r in ohlcv_rows:
        close_map[r['symbol']][r['bar_time']] = float(r['close'])

    # Load signals
    sig_rows = con.execute(
        "SELECT symbol, bar_time, signal FROM rl_state_vectors ORDER BY symbol, bar_time"
    ).fetchall()
    sig_map = defaultdict(dict)
    for r in sig_rows:
        sig_map[r['symbol']][r['bar_time']] = r['signal']

    symbols = list(close_map.keys())[:20]  # limit for speed
    now = datetime.utcnow().isoformat()
    wf_results = []

    for win in windows:
        is_sharpes, oos_sharpes = [], []
        is_returns, oos_returns = [], []
        oos_trades = 0

        for sym in symbols:
            cm   = close_map.get(sym, {})
            sigs = sig_map.get(sym, {})

            # In-sample
            is_times  = sorted(t for t in cm if win['train_start'] <= t < win['train_end'])
            is_closes = [cm[t] for t in is_times]
            is_sigs   = [sigs.get(t, 0) for t in is_times]
            if len(is_closes) >= 30:
                is_tr, is_sh, _, is_ntrades, _, _ = _manual_backtest(is_closes, is_sigs)
                is_sharpes.append(is_sh)
                is_returns.append(is_tr)

            # Out-of-sample
            oos_times  = sorted(t for t in cm if win['test_start'] <= t < win['test_end'])
            oos_closes = [cm[t] for t in oos_times]
            oos_sigs   = [sigs.get(t, 0) for t in oos_times]
            if len(oos_closes) >= 20:
                oos_tr, oos_sh, _, oos_nt, _, _ = _manual_backtest(oos_closes, oos_sigs)
                oos_sharpes.append(oos_sh)
                oos_returns.append(oos_tr)
                oos_trades += oos_nt

        avg_is_sh  = sum(is_sharpes)  / max(1, len(is_sharpes))
        avg_oos_sh = sum(oos_sharpes) / max(1, len(oos_sharpes))
        avg_is_r   = sum(is_returns)  / max(1, len(is_returns))
        avg_oos_r  = sum(oos_returns) / max(1, len(oos_returns))

        train_start_str = datetime.utcfromtimestamp(win['train_start']).strftime('%Y-%m-%d')
        train_end_str   = datetime.utcfromtimestamp(win['train_end']).strftime('%Y-%m-%d')
        test_start_str  = datetime.utcfromtimestamp(win['test_start']).strftime('%Y-%m-%d')
        test_end_str    = datetime.utcfromtimestamp(win['test_end']).strftime('%Y-%m-%d')

        con.execute("""
            INSERT OR REPLACE INTO walk_forward_results
            (window_id, train_start, train_end, test_start, test_end,
             is_sharpe, oos_sharpe, is_return, oos_return, n_test_trades, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (win['id'], train_start_str, train_end_str, test_start_str, test_end_str,
              round(avg_is_sh, 3), round(avg_oos_sh, 3),
              round(avg_is_r, 4), round(avg_oos_r, 4),
              oos_trades, now))

        wf_results.append({
            'window_id':    win['id'],
            'train_period': f"{train_start_str} → {train_end_str}",
            'test_period':  f"{test_start_str} → {test_end_str}",
            'is_sharpe':    round(avg_is_sh, 3),
            'oos_sharpe':   round(avg_oos_sh, 3),
            'is_return':    round(avg_is_r, 4),
            'oos_return':   round(avg_oos_r, 4),
            'overfit_ratio': round(avg_oos_sh / avg_is_sh, 3) if avg_is_sh != 0 else 0,
            'n_test_trades': oos_trades,
        })

    con.commit()
    con.close()

    return {
        'windows':          wf_results,
        'avg_oos_sharpe':   round(sum(w['oos_sharpe'] for w in wf_results)/len(wf_results), 3),
        'strategy_degrades': wf_results[-1]['oos_sharpe'] < wf_results[0]['oos_sharpe'],
        'elapsed':          round(time.time() - t0, 2)
    }

# ─── Command: optimize_thresholds ────────────────────────────────────────────

def cmd_optimize_thresholds(params):
    t0 = time.time()
    print('[Phase 18] Optimizing signal thresholds...', flush=True)

    con = get_connection()
    ohlcv_rows = load_ohlcv_all(con)
    close_map  = defaultdict(dict)
    for r in ohlcv_rows:
        close_map[r['symbol']][r['bar_time']] = float(r['close'])
    sig_rows = con.execute(
        "SELECT symbol, bar_time, state_json FROM rl_state_vectors ORDER BY symbol, bar_time"
    ).fetchall()
    sig_map = defaultdict(list)
    for r in sig_rows:
        try:
            state = json.loads(r['state_json'])
            sig_map[r['symbol']].append((r['bar_time'], state))
        except Exception:
            pass
    con.close()

    symbols = list(close_map.keys())[:10]

    def objective(thresholds):
        """Negative Sharpe ratio to minimize."""
        rsi_buy, rsi_sell, law_min = thresholds
        rsi_buy  = max(20, min(70, rsi_buy))
        rsi_sell = max(50, min(95, rsi_sell))
        law_min  = max(0, min(8, law_min))

        all_sharpes = []
        for sym in symbols:
            cm     = close_map.get(sym, {})
            states = sig_map.get(sym, [])
            if len(states) < 30:
                continue

            closes_arr = []
            sigs_arr   = []
            for bar_time, state in states:
                if bar_time not in cm:
                    continue
                closes_arr.append(cm[bar_time])
                # Re-evaluate signal with custom thresholds
                # state[1] = RSI 14d normalized (0-1), state[12] = law activations
                rsi14 = state[1] * 100 if len(state) > 1 else 50
                n_law = state[12] * 10 if len(state) > 12 else 0
                regime_score = state[10] if len(state) > 10 else 0.25
                buy  = (n_law >= law_min and regime_score > 0 and rsi14 < rsi_buy)
                sell = (rsi14 > rsi_sell or regime_score == 0 or n_law == 0)
                sig  = 1 if buy else (-1 if sell else 0)
                sigs_arr.append(sig)

            if len(closes_arr) >= 30:
                _, sharpe, _, _, _, _ = _manual_backtest(closes_arr, sigs_arr)
                all_sharpes.append(sharpe)

        avg_sharpe = sum(all_sharpes) / max(1, len(all_sharpes))
        return -avg_sharpe  # minimize negative = maximize

    if HAS_SCIPY:
        print('[Phase 18] Running differential evolution...', flush=True)
        bounds = [(30, 65), (60, 90), (1, 6)]
        result = differential_evolution(
            objective, bounds,
            maxiter=30, popsize=8, seed=42, tol=0.01,
            workers=1
        )
        best_rsi_buy  = round(float(result.x[0]), 1)
        best_rsi_sell = round(float(result.x[1]), 1)
        best_law_min  = round(float(result.x[2]), 1)
        best_sharpe   = round(-float(result.fun), 3)
        converged     = bool(result.success)
    else:
        # Manual grid search fallback
        print('[Phase 18] scipy not available, running grid search...', flush=True)
        best_sharpe = -999
        best_rsi_buy, best_rsi_sell, best_law_min = 60, 75, 2
        for rsi_b in [45, 55, 65]:
            for rsi_s in [70, 75, 80]:
                for lm in [1, 2, 3]:
                    s = -objective([rsi_b, rsi_s, lm])
                    if s > best_sharpe:
                        best_sharpe   = s
                        best_rsi_buy  = rsi_b
                        best_rsi_sell = rsi_s
                        best_law_min  = lm
        converged = True

    # Default signal thresholds for comparison
    default_sharpe = -objective([65, 75, 2])

    return {
        'optimal_rsi_buy':   best_rsi_buy,
        'optimal_rsi_sell':  best_rsi_sell,
        'optimal_law_min':   best_law_min,
        'optimal_sharpe':    best_sharpe,
        'default_sharpe':    round(float(default_sharpe), 3),
        'improvement_pct':   round((best_sharpe - default_sharpe) / max(0.001, abs(default_sharpe)) * 100, 1),
        'converged':         converged,
        'scipy_used':        HAS_SCIPY,
        'elapsed':           round(time.time() - t0, 2)
    }

# ─── Command: performance_report ─────────────────────────────────────────────

def cmd_performance_report(params):
    t0 = time.time()
    print('[Phase 18] Generating performance report...', flush=True)
    con = get_connection()

    try:
        rows = con.execute("""
            SELECT symbol, total_return, sharpe_ratio, max_drawdown,
                   win_rate, profit_factor, n_trades
            FROM backtest_results
            WHERE strategy = 'phase18_signal'
            ORDER BY sharpe_ratio DESC
        """).fetchall()
    except Exception:
        rows = []

    if not rows:
        # Trigger backtest first
        con.close()
        bt = cmd_backtest_strategy({})
        return {'note': 'Ran backtest first', 'backtest': bt,
                'elapsed': round(time.time()-t0, 2)}

    stats = [dict(r) for r in rows]

    returns    = [r['total_return']  for r in stats if r['total_return'] is not None]
    sharpes    = [r['sharpe_ratio']  for r in stats if r['sharpe_ratio'] is not None]
    drawdowns  = [r['max_drawdown']  for r in stats if r['max_drawdown'] is not None]
    win_rates  = [r['win_rate']      for r in stats if r['win_rate'] is not None]
    pf_vals    = [r['profit_factor'] for r in stats if r['profit_factor'] is not None]
    n_trades   = [r['n_trades']      for r in stats if r['n_trades'] is not None]

    def _avg(lst):
        return round(sum(lst)/len(lst), 4) if lst else 0.0

    positive_sharpe = sum(1 for s in sharpes if s > 0.5)
    hit_rate        = round(positive_sharpe / max(1, len(sharpes)), 3)

    # Walk-forward summary
    wf_rows = con.execute(
        "SELECT window_id, is_sharpe, oos_sharpe, oos_return FROM walk_forward_results"
    ).fetchall()
    wf_summary = [dict(r) for r in wf_rows]

    con.close()

    return {
        'n_stocks_backtested': len(stats),
        'avg_total_return':    _avg(returns),
        'avg_sharpe_ratio':    _avg(sharpes),
        'avg_max_drawdown':    _avg(drawdowns),
        'avg_win_rate':        _avg(win_rates),
        'avg_profit_factor':   _avg(pf_vals),
        'avg_n_trades':        round(sum(n_trades)/max(1,len(n_trades)), 1),
        'pct_positive_sharpe': round(hit_rate * 100, 1),
        'top_10_by_sharpe':    stats[:10],
        'bottom_5_by_sharpe':  stats[-5:] if len(stats) >= 5 else stats,
        'walk_forward':        wf_summary,
        'elapsed':             round(time.time() - t0, 2)
    }

# ─── Dispatch ────────────────────────────────────────────────────────────────

COMMANDS = {
    'build_state_vector':  cmd_build_state_vector,
    'backtest_strategy':   cmd_backtest_strategy,
    'walk_forward':        cmd_walk_forward,
    'optimize_thresholds': cmd_optimize_thresholds,
    'performance_report':  cmd_performance_report,
}

if __name__ == '__main__':
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else 'performance_report'
        params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        fn = COMMANDS.get(command)
        if fn is None:
            out = {'error': f'unknown command: {command}', 'available': list(COMMANDS.keys())}
        else:
            out = fn(params)
        print(json.dumps(out))
    except Exception as ex:
        import traceback
        print(json.dumps({'error': str(ex), 'traceback': traceback.format_exc()[-1000:]}))
