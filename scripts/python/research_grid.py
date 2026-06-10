#!/usr/bin/env python3
"""
Phase 69 — Research Grid
"شبكة البحث — اختبار آلاف الفرضيات بالتوازي على البيانات التاريخية"

Commands: run_grid | run_single | status | top_results | build_full
"""
import sys, json, sqlite3, time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import pandas as pd
    import numpy as np
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
COST_BPS = 150  # EGX avg round-trip (commission 60bps + spread 90bps)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS research_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        hyp_id          TEXT NOT NULL,
        hyp_name        TEXT,
        tested_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        n_activations   INTEGER DEFAULT 0,
        n_hits          INTEGER DEFAULT 0,
        precision_val   REAL,
        win_rate_pct    REAL,
        avg_net_return  REAL,
        avg_win_pct     REAL,
        avg_loss_pct    REAL,
        expectancy_pct  REAL,
        is_precision    REAL,
        oos_precision   REAL,
        oos_score       REAL,
        is_samples      INTEGER DEFAULT 0,
        oos_samples     INTEGER DEFAULT 0,
        direction       TEXT,
        holding_days    INTEGER,
        regime_filter   TEXT,
        status          TEXT DEFAULT 'ACTIVE',
        UNIQUE(hyp_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rr_expectancy ON research_results(expectancy_pct);
    CREATE INDEX IF NOT EXISTS idx_rr_oos ON research_results(oos_score);
    CREATE INDEX IF NOT EXISTS idx_rr_status ON research_results(status);

    CREATE TABLE IF NOT EXISTS grid_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        n_tested     INTEGER DEFAULT 0,
        n_valid      INTEGER DEFAULT 0,
        n_killed     INTEGER DEFAULT 0,
        elapsed_sec  REAL,
        top_hyp_id   TEXT,
        top_exp      REAL,
        notes        TEXT
    );
    """)
    conn.commit()

# ─────────────────────────────────────────────
# Indicator computation from raw OHLCV
# ─────────────────────────────────────────────
def _compute_indicators(df):
    """Compute RSI, MACD, ATR, ADX, CCI, volume ratio, momentum from OHLCV df."""
    close = df['close']
    high  = df['high']
    low   = df['low']
    vol   = df['volume']

    # RSI(14)
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=13, adjust=False).mean()
    avg_l = loss.ewm(com=13, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, float('nan'))
    df['rsi14'] = 100 - (100 / (1 + rs))

    # MACD Histogram
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    df['macd_hist'] = macd - sig

    # Volume ratio (vol / 20d avg vol)
    df['vol_ratio_20'] = vol / vol.rolling(20).mean()

    # ATR(14)
    tr = (high - low).combine(
        (high - close.shift()).abs(), max
    ).combine(
        (low - close.shift()).abs(), max
    )
    df['atr14'] = tr.ewm(com=13, adjust=False).mean()

    # ADX(14) — simplified
    plus_dm  = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr14     = tr.ewm(com=13, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(com=13, adjust=False).mean() / tr14.replace(0, float('nan'))
    minus_di = 100 * minus_dm.ewm(com=13, adjust=False).mean() / tr14.replace(0, float('nan'))
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan')))
    df['adx14'] = dx.ewm(com=13, adjust=False).mean()

    # CCI(20)
    tp      = (high + low + close) / 3
    sma_tp  = tp.rolling(20).mean()
    mad     = tp.rolling(20).apply(lambda x: (abs(x - x.mean())).mean(), raw=True)
    df['cci20'] = (tp - sma_tp) / (0.015 * mad.replace(0, float('nan')))

    # Momentum
    df['momentum_5d']  = close.pct_change(5)  * 100
    df['momentum_10d'] = close.pct_change(10) * 100
    df['momentum_20d'] = close.pct_change(20) * 100

    # Close position within day's range
    rng = (high - low).replace(0, float('nan'))
    df['close_position'] = (close - low) / rng

    # Price vs rolling ATH
    ath = close.cummax()
    df['price_vs_ath'] = close / ath

    return df


# ─────────────────────────────────────────────
# Core: test one hypothesis against all data
# ─────────────────────────────────────────────
def _load_indicator_cache():
    """Load OHLCV + compute indicators for ALL symbols once. Returns dict {sym: DataFrame}."""
    import pandas as pd
    conn = sqlite3.connect(DB_PATH)
    raw  = conn.execute("""
        SELECT symbol, date(bar_time,'unixepoch') as bar_date,
               open, high, low, close, volume
        FROM ohlcv_history ORDER BY symbol, bar_time
    """).fetchall()
    conn.close()
    df_all = pd.DataFrame(raw, columns=['symbol','bar_date','open','high','low','close','volume'])
    cache  = {}
    for sym, grp in df_all.groupby('symbol'):
        g = grp.copy().reset_index(drop=True)
        if len(g) >= 30:
            cache[sym] = _compute_indicators(g).reset_index(drop=True)
    return cache


def _test_hypothesis(hyp_id, hyp_name, conditions_json, direction, holding_days,
                     indicator_cache=None):
    """Run backtest using pre-computed indicator cache (or build it if not provided)."""
    if not HAS_PANDAS:
        return {"hyp_id": hyp_id, "error": "pandas not installed"}

    try:
        conditions = json.loads(conditions_json)
    except:
        return None

    VALID_COLS = {'rsi14','macd_hist','vol_ratio_20','adx14','momentum_5d',
                  'momentum_10d','momentum_20d','close_position','price_vs_ath','cci20','atr14'}

    cond_list = []
    for c in conditions:
        col = c.get('col','')
        op  = c.get('op','')
        val = c.get('val')
        if col not in VALID_COLS or op not in ('<','>','<=','>='):
            continue
        cond_list.append((col, op, float(val)))

    if not cond_list:
        return None

    # Use provided cache or build fresh (single-hypothesis call path)
    cache = indicator_cache if indicator_cache is not None else _load_indicator_cache()

    import pandas as pd
    returns   = []
    is_hits   = 0; is_total  = 0
    oos_hits  = 0; oos_total = 0

    for sym, grp in cache.items():
        # Drop rows where any condition column is NaN
        needed = [c[0] for c in cond_list if c[0] in grp.columns]
        if not needed:
            continue
        g = grp.dropna(subset=needed).reset_index(drop=True)
        if len(g) < 5:
            continue

        mask = pd.Series([True] * len(g), index=g.index)
        for col, op, val in cond_list:
            if col not in g.columns:
                mask[:] = False; break
            if   op == '<':  mask &= g[col] < val
            elif op == '>':  mask &= g[col] > val
            elif op == '<=': mask &= g[col] <= val
            elif op == '>=': mask &= g[col] >= val

        for pos in g.index[mask]:
            entry_close = g.iloc[pos]['close']
            exit_pos    = pos + int(holding_days)
            if exit_pos >= len(g):
                continue
            exit_close = g.iloc[exit_pos]['close']
            bar_date   = g.iloc[pos]['bar_date']

            if not entry_close or not exit_close or entry_close <= 0:
                continue

            gross = (exit_close / entry_close - 1.0) * 100
            if direction == 'SHORT': gross = -gross
            net = gross - (COST_BPS / 100)
            returns.append(net)
            win = net > 0

            if bar_date < '2024-01-01':
                is_total += 1
                if win: is_hits += 1
            else:
                oos_total += 1
                if win: oos_hits += 1

    n_total = len(returns)
    if n_total < 10:
        return {"hyp_id": hyp_id, "hyp_name": hyp_name, "n_activations": n_total,
                "status": "INSUFFICIENT_DATA"}

    n_hits   = sum(1 for r in returns if r > 0)
    prec     = n_hits / n_total
    avg_ret  = sum(returns) / n_total
    wins     = [r for r in returns if r > 0]
    losses   = [r for r in returns if r <= 0]
    avg_win  = sum(wins)/len(wins)     if wins   else 0
    avg_loss = sum(losses)/len(losses) if losses else 0
    exp      = (prec * avg_win) + ((1-prec) * avg_loss)
    is_prec  = is_hits / is_total      if is_total  > 0 else None
    oos_prec = oos_hits / oos_total    if oos_total > 0 else None
    oos_sc   = oos_prec / is_prec      if (is_prec and oos_prec and is_prec > 0) else None

    return {
        "hyp_id":         hyp_id,
        "hyp_name":       hyp_name,
        "n_activations":  n_total,
        "n_hits":         n_hits,
        "precision_val":  round(prec, 4),
        "win_rate_pct":   round(prec * 100, 2),
        "avg_net_return": round(avg_ret, 4),
        "avg_win_pct":    round(avg_win, 4),
        "avg_loss_pct":   round(avg_loss, 4),
        "expectancy_pct": round(exp, 4),
        "is_precision":   round(is_prec, 4)  if is_prec  else None,
        "oos_precision":  round(oos_prec, 4) if oos_prec else None,
        "oos_score":      round(oos_sc, 4)   if oos_sc   else None,
        "is_samples":     is_total,
        "oos_samples":    oos_total,
        "direction":      direction,
        "holding_days":   holding_days,
        "status":         "ACTIVE",
    }

# ─────────────────────────────────────────────
# Run full research grid
# ─────────────────────────────────────────────
def run_grid(params):
    limit      = int(params.get('limit', 100))
    workers    = int(params.get('workers', 4))
    force      = params.get('force', False)  # re-test already-tested
    conn       = db()
    ensure_tables(conn)
    t0         = time.time()

    # Load hypotheses to test
    if force:
        q = "SELECT hyp_id, hyp_name, conditions_json, direction, holding_days FROM hypotheses LIMIT ?"
        args = [limit]
    else:
        q = """SELECT h.hyp_id, h.hyp_name, h.conditions_json, h.direction, h.holding_days
               FROM hypotheses h
               LEFT JOIN research_results r ON h.hyp_id = r.hyp_id
               WHERE r.hyp_id IS NULL
               LIMIT ?"""
        args = [limit]

    hyps = conn.execute(q, args).fetchall()
    conn.close()

    if not hyps:
        return {"success": True, "message": "All hypotheses already tested. Use force=true to retest.",
                "n_tested": 0}

    print(f"[Research Grid] Testing {len(hyps)} hypotheses with {workers} workers...", flush=True)

    # Pre-load + compute indicators ONCE for all symbols (shared across all workers)
    if HAS_PANDAS:
        print("[Research Grid] Pre-computing indicator cache...", flush=True)
        t_cache = time.time()
        indicator_cache = _load_indicator_cache()
        print(f"[Research Grid] Cache ready ({len(indicator_cache)} symbols, {time.time()-t_cache:.1f}s)", flush=True)
    else:
        indicator_cache = None

    results   = []
    n_valid   = 0
    n_killed  = 0
    top_exp   = None
    top_id    = None

    def test_one(h):
        return _test_hypothesis(h['hyp_id'], h['hyp_name'],
                                h['conditions_json'], h['direction'], h['holding_days'],
                                indicator_cache=indicator_cache)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(test_one, h): h for h in hyps}
        for fut in as_completed(futures):
            res = fut.result()
            if not res or res.get('error') or res.get('status') == 'INSUFFICIENT_DATA':
                n_killed += 1
                continue
            results.append(res)
            if res['expectancy_pct'] and (top_exp is None or res['expectancy_pct'] > top_exp):
                top_exp = res['expectancy_pct']
                top_id  = res['hyp_id']

    # Save results to DB
    if results:
        conn2 = db()
        ensure_tables(conn2)
        for res in results:
            try:
                conn2.execute("""
                    INSERT OR REPLACE INTO research_results
                    (hyp_id, hyp_name, n_activations, n_hits, precision_val, win_rate_pct,
                     avg_net_return, avg_win_pct, avg_loss_pct, expectancy_pct,
                     is_precision, oos_precision, oos_score,
                     is_samples, oos_samples, direction, holding_days, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (res['hyp_id'], res['hyp_name'], res['n_activations'], res['n_hits'],
                      res['precision_val'], res['win_rate_pct'], res['avg_net_return'],
                      res['avg_win_pct'], res['avg_loss_pct'], res['expectancy_pct'],
                      res['is_precision'], res['oos_precision'], res['oos_score'],
                      res['is_samples'], res['oos_samples'], res['direction'],
                      res['holding_days'], 'ACTIVE'))
                n_valid += 1
            except Exception as e:
                pass

        elapsed = time.time() - t0
        conn2.execute("""
            INSERT INTO grid_runs (n_tested, n_valid, n_killed, elapsed_sec, top_hyp_id, top_exp)
            VALUES (?,?,?,?,?,?)
        """, (len(hyps), n_valid, n_killed, elapsed, top_id, top_exp))
        conn2.commit()
        conn2.close()

    results.sort(key=lambda x: -(x.get('expectancy_pct') or -999))

    return {
        "success":       True,
        "n_tested":      len(hyps),
        "n_valid":       n_valid,
        "n_killed":      n_killed,
        "elapsed_sec":   round(time.time() - t0, 1),
        "top_results":   results[:10],
        "top_hyp_id":    top_id,
        "top_expectancy":round(top_exp, 3) if top_exp else None,
    }

# ─────────────────────────────────────────────
# Run single hypothesis test
# ─────────────────────────────────────────────
def run_single(params):
    hyp_id = params.get('hyp_id')
    if not hyp_id:
        return {"success": False, "error": "hyp_id required"}

    conn = db()
    ensure_tables(conn)
    row = conn.execute("SELECT * FROM hypotheses WHERE hyp_id=?", (hyp_id,)).fetchone()
    conn.close()

    if not row:
        return {"success": False, "error": f"{hyp_id} not found"}

    res = _test_hypothesis(row['hyp_id'], row['hyp_name'],
                           row['conditions_json'], row['direction'], row['holding_days'])
    if res and not res.get('error') and res.get('status') != 'INSUFFICIENT_DATA':
        conn2 = db()
        conn2.execute("""
            INSERT OR REPLACE INTO research_results
            (hyp_id, hyp_name, n_activations, n_hits, precision_val, win_rate_pct,
             avg_net_return, avg_win_pct, avg_loss_pct, expectancy_pct,
             is_precision, oos_precision, oos_score,
             is_samples, oos_samples, direction, holding_days)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (res['hyp_id'], res['hyp_name'], res['n_activations'], res['n_hits'],
              res['precision_val'], res['win_rate_pct'], res['avg_net_return'],
              res['avg_win_pct'], res['avg_loss_pct'], res['expectancy_pct'],
              res['is_precision'], res['oos_precision'], res['oos_score'],
              res['is_samples'], res['oos_samples'], res['direction'], res['holding_days']))
        conn2.commit()
        conn2.close()

    return {"success": True, "result": res}

# ─────────────────────────────────────────────
# Grid status
# ─────────────────────────────────────────────
def status(params):
    conn = db()
    ensure_tables(conn)
    total_hyps = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    tested     = conn.execute("SELECT COUNT(*) FROM research_results").fetchone()[0]
    active     = conn.execute("SELECT COUNT(*) FROM research_results WHERE status='ACTIVE'").fetchone()[0]
    killed     = conn.execute("SELECT COUNT(*) FROM research_results WHERE status='KILLED'").fetchone()[0]
    top        = conn.execute("""
        SELECT hyp_id, hyp_name, expectancy_pct, oos_score, win_rate_pct, n_activations
        FROM research_results WHERE status='ACTIVE'
        ORDER BY expectancy_pct DESC LIMIT 5
    """).fetchall()
    runs       = conn.execute("SELECT * FROM grid_runs ORDER BY run_at DESC LIMIT 3").fetchall()
    conn.close()

    return {
        "success":          True,
        "total_hypotheses": total_hyps,
        "tested":           tested,
        "untested":         total_hyps - tested,
        "active":           active,
        "killed":           killed,
        "top_5":            [dict(t) for t in top],
        "recent_runs":      [dict(r) for r in runs],
    }

# ─────────────────────────────────────────────
# Top results with filters
# ─────────────────────────────────────────────
def top_results(params):
    min_exp      = float(params.get('min_expectancy', 0.0))
    min_oos      = float(params.get('min_oos_score', 0.0))
    min_acts     = int(params.get('min_activations', 10))
    limit        = int(params.get('limit', 20))
    sort_by      = params.get('sort_by', 'expectancy_pct')

    conn = db()
    ensure_tables(conn)

    valid_sorts = {'expectancy_pct','oos_score','win_rate_pct','n_activations','avg_net_return'}
    if sort_by not in valid_sorts: sort_by = 'expectancy_pct'

    rows = conn.execute(f"""
        SELECT r.*, h.conditions_json, h.category
        FROM research_results r
        LEFT JOIN hypotheses h ON r.hyp_id = h.hyp_id
        WHERE r.status = 'ACTIVE'
          AND r.expectancy_pct >= ?
          AND r.n_activations  >= ?
          AND (r.oos_score IS NULL OR r.oos_score >= ?)
        ORDER BY r.{sort_by} DESC NULLS LAST
        LIMIT ?
    """, (min_exp, min_acts, min_oos, limit)).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        try:
            conds = json.loads(d.pop('conditions_json', '[]'))
            d['conditions_summary'] = ' AND '.join(
                f"{c['col']}{c['op']}{c['val']}" for c in conds
            )
        except: pass
        results.append(d)

    return {
        "success":   True,
        "n_results": len(results),
        "results":   results,
        "filters":   {"min_expectancy": min_exp, "min_oos_score": min_oos},
    }

# ─────────────────────────────────────────────
# vectorbt — ultra-fast signal backtesting
# ─────────────────────────────────────────────
def vbt_backtest_signals(params):
    """vectorbt-accelerated backtesting of explosion_predictions signals.

    Uses vectorbt Portfolio to simulate entries on ML HIGH signals and
    exits after holding_days. 100x faster than pandas loop for many symbols.

    params:
      holding_days : int  (default 5)
      min_prob     : float (default 0.7) — min explosion_prob to enter
      start_date   : str  (default '2026-01-01')
      init_cash    : float (default 100000)
      fee_pct      : float (default 0.015) — EGX round-trip 1.5%
    """
    try:
        import vectorbt as vbt
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'vectorbt/pandas/numpy not installed'}

    holding_days = int(params.get('holding_days', 5))
    min_prob     = float(params.get('min_prob', 0.7))
    start_date   = params.get('start_date', '2026-01-01')
    init_cash    = float(params.get('init_cash', 100_000))
    fee_pct      = float(params.get('fee_pct', 0.015))

    conn = db()

    # Load predictions (HIGH confidence signals)
    sig_rows = conn.execute("""
        SELECT p.symbol, p.pred_date, p.explosion_prob
        FROM explosion_predictions p
        WHERE p.explosion_prob >= ?
          AND p.pred_date >= ?
        ORDER BY p.pred_date, p.symbol
    """, (min_prob, start_date)).fetchall()

    if not sig_rows:
        conn.close()
        return {'success': False, 'error': f'No predictions with prob>={min_prob} after {start_date}'}

    # Load OHLCV for signal symbols
    syms = list({r['symbol'] for r in sig_rows})
    ph_marks = ','.join('?' * len(syms))
    raw = conn.execute(f"""
        SELECT symbol, date(bar_time,'unixepoch') AS bar_date, close
        FROM ohlcv_history
        WHERE symbol IN ({ph_marks})
          AND date(bar_time,'unixepoch') >= ?
        ORDER BY bar_time
    """, syms + [start_date]).fetchall()
    conn.close()

    if not raw:
        return {'success': False, 'error': 'No OHLCV data for signal symbols in range'}

    # Build close price DataFrame: rows=dates, cols=symbols
    df_raw = pd.DataFrame(raw, columns=['symbol', 'bar_date', 'close'])
    df_raw['close'] = pd.to_numeric(df_raw['close'], errors='coerce')
    close_wide = df_raw.pivot_table(index='bar_date', columns='symbol', values='close', aggfunc='last')
    close_wide = close_wide.sort_index()

    # Build entry signals: 1 on prediction date, 0 elsewhere
    entries = pd.DataFrame(False, index=close_wide.index, columns=close_wide.columns)
    for r in sig_rows:
        dt  = r['pred_date']
        sym = r['symbol']
        if dt in entries.index and sym in entries.columns:
            entries.at[dt, sym] = True

    # Build exit signals: exit after holding_days
    exits = entries.shift(holding_days).fillna(False)

    # Run vectorbt Portfolio
    pf = vbt.Portfolio.from_signals(
        close_wide,
        entries=entries,
        exits=exits,
        init_cash=init_cash,
        fees=fee_pct / 2,       # one-way fee
        slippage=0.001,
        freq='D',
        group_by=False,
    )

    stats = pf.stats()
    total_ret     = float(pf.total_return().mean())
    ann_ret       = float(pf.annualized_return().mean()) if hasattr(pf, 'annualized_return') else None
    sharpe        = float(pf.sharpe_ratio().mean())      if hasattr(pf, 'sharpe_ratio')      else None
    max_dd        = float(pf.max_drawdown().mean())      if hasattr(pf, 'max_drawdown')       else None
    n_trades      = int(pf.trades.count().sum())

    # Per-symbol breakdown
    sym_stats = []
    for sym in close_wide.columns:
        try:
            tr = float(pf[sym].total_return())
            nt = int(pf[sym].trades.count())
            if nt > 0:
                sym_stats.append({'symbol': sym, 'total_return_pct': round(tr * 100, 2), 'n_trades': nt})
        except Exception:
            pass

    sym_stats.sort(key=lambda x: -x['total_return_pct'])

    return {
        'success':           True,
        'n_signals':         len(sig_rows),
        'n_symbols':         len(syms),
        'n_trades':          n_trades,
        'avg_total_return':  round(total_ret * 100, 2),
        'avg_sharpe':        round(sharpe, 3) if sharpe is not None else None,
        'avg_max_drawdown':  round(max_dd  * 100, 2) if max_dd is not None else None,
        'holding_days':      holding_days,
        'min_prob':          min_prob,
        'top_symbols':       sym_stats[:10],
        'bottom_symbols':    sym_stats[-5:],
        'period':            f'{start_date} → {close_wide.index[-1]}',
    }


# ─────────────────────────────────────────────
# Build full
# ─────────────────────────────────────────────
def build_full(params):
    limit = int(params.get('limit', 50))
    st    = status({})
    untested = st.get('untested', 0)
    if untested > 0:
        grid = run_grid({'limit': min(limit, untested), 'workers': 4})
    else:
        grid = {"n_tested": 0, "message": "All tested"}
    top = top_results({'min_expectancy': 0.1, 'limit': 10})
    return {
        "success":    True,
        "grid":       grid,
        "status":     st,
        "top_alpha":  top['results'][:5],
    }

# ─────────────────────────────────────────────
if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'status'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    dispatch = {
        'run_grid':             run_grid,
        'run_single':           run_single,
        'status':               status,
        'top_results':          top_results,
        'build_full':           build_full,
        'vbt_backtest_signals': vbt_backtest_signals,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(params), default=str))
    else:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(dispatch.keys())}))
