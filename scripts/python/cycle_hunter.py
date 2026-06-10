#!/usr/bin/env python3
"""
Cycle Hunter — Hidden Cycles & Seasonality Discovery
Discovers dominant FFT cycles, autocorrelation cycles, calendar seasonality,
and regime-conditioned cycle lengths in EGX market data.
"""
import os, sys, json, sqlite3, datetime, gc, math, time, hashlib
from pathlib import Path
from multiprocessing import Pool, cpu_count

import numpy as np

DB_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')
BATCH_SIZE = 30

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS market_cycles (
        cycle_id TEXT PRIMARY KEY,
        symbol TEXT,
        sector TEXT,
        cycle_type TEXT,
        period_days REAL,
        amplitude_pct REAL,
        phase_days REAL,
        confidence REAL,
        next_peak_date TEXT,
        next_trough_date TEXT,
        discovered_date TEXT,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS seasonal_patterns (
        pattern_id TEXT PRIMARY KEY,
        scope TEXT,
        pattern_type TEXT,
        period_label TEXT,
        avg_return_pct REAL,
        win_rate REAL,
        n_samples INTEGER,
        is_significant INTEGER,
        discovered_date TEXT
    );

    CREATE TABLE IF NOT EXISTS cycle_hunter_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        cycles_found INTEGER,
        patterns_found INTEGER,
        duration_seconds REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# ── FFT cycle detection ───────────────────────────────────────────────────────

def find_dominant_cycles(prices, min_period=5, max_period=66):
    """Find dominant cycles using FFT."""
    prices = np.array(prices, dtype=float)
    if len(prices) < min_period * 2:
        return []
    detrended = prices - np.polyval(
        np.polyfit(range(len(prices)), prices, 1), range(len(prices))
    )
    fft_vals = np.abs(np.fft.rfft(detrended))
    freqs = np.fft.rfftfreq(len(detrended))
    periods = 1.0 / (freqs[1:] + 1e-10)
    powers = fft_vals[1:]
    mask = (periods >= min_period) & (periods <= max_period)
    if not mask.any():
        return []
    candidate_periods = periods[mask]
    candidate_powers = powers[mask]
    noise_floor = np.median(powers) + 2 * np.std(powers)
    significant = candidate_powers > noise_floor
    if not significant.any():
        return []
    top_idx = np.argsort(candidate_powers[significant])[-3:][::-1]
    sig_periods = candidate_periods[significant]
    sig_powers = candidate_powers[significant]
    return [{"period_days": float(sig_periods[i]), "power": float(sig_powers[i])} for i in top_idx]

def find_acf_cycles(rets, max_lag=30):
    """Find significant autocorrelation peaks (recurring cycles)."""
    rets = np.array(rets, dtype=float)
    n = len(rets)
    if n < max_lag * 2:
        return []
    mean = np.mean(rets)
    var = np.var(rets) + 1e-12
    acf_vals = []
    for lag in range(1, max_lag + 1):
        cov = np.mean((rets[:n-lag] - mean) * (rets[lag:] - mean))
        acf_vals.append(cov / var)
    # 95% confidence band
    conf = 1.96 / math.sqrt(n)
    peaks = []
    for i in range(1, len(acf_vals) - 1):
        if acf_vals[i] > conf and acf_vals[i] > acf_vals[i-1] and acf_vals[i] > acf_vals[i+1]:
            peaks.append({"lag": i + 1, "acf": float(acf_vals[i])})
    return peaks

def t_test_one_sample(vals, mu=0.0):
    """One-sample t-test. Returns (t_stat, p_value approx)."""
    n = len(vals)
    if n < 3:
        return 0.0, 1.0
    mean = np.mean(vals)
    se = np.std(vals, ddof=1) / math.sqrt(n)
    if se < 1e-12:
        return 0.0, 1.0
    t = (mean - mu) / se
    # Approximate p-value using normal distribution (reasonable for n>30)
    from math import erfc
    p = float(erfc(abs(t) / math.sqrt(2)))
    return float(t), p

def phase_from_cycle(prices, period_days):
    """Estimate phase (days until next peak) from last few cycles."""
    prices = np.array(prices, dtype=float)
    period = int(round(period_days))
    if len(prices) < period:
        return period // 2
    # Look at the last 2 periods
    segment = prices[-min(period * 2, len(prices)):]
    peak_idx = int(np.argmax(segment))
    phase = (period - (len(segment) - 1 - peak_idx)) % period
    return int(phase)

# ── Market index proxy ────────────────────────────────────────────────────────

def get_market_index_prices(conn, n_stocks=30):
    """Average close of top N liquid stocks as market proxy."""
    top_symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM symbol_liquidity_profile ORDER BY avg_daily_volume DESC LIMIT ?",
        (n_stocks,)
    ).fetchall()]
    if not top_symbols:
        top_symbols = [r[0] for r in conn.execute(
            "SELECT symbol FROM ohlcv_history GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT ?",
            (n_stocks,)
        ).fetchall()]

    # Get all bar_times present for all top symbols
    placeholders = ','.join(['?'] * len(top_symbols))
    rows = conn.execute(
        f"SELECT bar_time, symbol, close FROM ohlcv_history WHERE symbol IN ({placeholders}) ORDER BY bar_time ASC",
        top_symbols
    ).fetchall()

    # Build time -> {sym: close}
    from collections import defaultdict
    time_prices = defaultdict(dict)
    for r in rows:
        time_prices[r['bar_time']][r['symbol']] = r['close']

    sorted_times = sorted(time_prices.keys())
    index_prices = []
    index_times = []
    for t in sorted_times:
        vals = list(time_prices[t].values())
        if len(vals) >= n_stocks // 2:
            # Normalize each stock contribution using its mean
            index_prices.append(np.mean(vals))
            index_times.append(t)

    return index_times, index_prices

# ── Seasonality computation ───────────────────────────────────────────────────

def compute_seasonality(conn, today_str):
    """Compute calendar seasonality for MARKET, each SECTOR, and store patterns."""
    import datetime as dt

    # Get all returns with timestamps and sectors
    rows = conn.execute("""
        SELECT oh.symbol, oh.bar_time, oh.close, su.sector
        FROM ohlcv_history oh
        LEFT JOIN stock_universe su ON oh.symbol = su.symbol
        ORDER BY oh.symbol, oh.bar_time ASC
    """).fetchall()

    # Build per-symbol return series with metadata
    from collections import defaultdict
    sym_bars = defaultdict(list)
    for r in rows:
        sym_bars[r['symbol']].append({
            'bar_time': r['bar_time'],
            'close': r['close'],
            'sector': r['sector'] or 'Unknown',
        })

    # Compute returns with day-of-week, week-of-month, month, quarter
    all_rets = []  # {ret, dow, week_of_month, month, quarter, sector}
    for sym, bars in sym_bars.items():
        if len(bars) < 10:
            continue
        for i in range(1, len(bars)):
            prev_close = bars[i-1]['close']
            curr_close = bars[i]['close']
            if prev_close <= 0:
                continue
            ret = (curr_close - prev_close) / prev_close * 100
            ts = bars[i]['bar_time']
            d = dt.datetime.utcfromtimestamp(ts)
            dow = d.weekday()  # 0=Mon
            wom = (d.day - 1) // 7 + 1  # week of month 1-5
            month = d.month
            quarter = (d.month - 1) // 3 + 1
            all_rets.append({
                'ret': ret,
                'dow': dow,
                'wom': wom,
                'month': month,
                'quarter': quarter,
                'sector': bars[i]['sector'],
            })

    if not all_rets:
        return 0

    patterns_saved = 0
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    month_names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

    def save_pattern(conn, scope, ptype, label, vals):
        nonlocal patterns_saved
        if len(vals) < 3:
            return
        arr = np.array(vals)
        avg_ret = float(np.mean(arr))
        win_rate = float(np.mean(arr > 0))
        n = len(arr)
        _, pval = t_test_one_sample(arr)
        is_sig = 1 if pval < 0.05 else 0
        pid = hashlib.md5(f"{scope}|{ptype}|{label}".encode()).hexdigest()[:16]
        conn.execute("""
            INSERT OR REPLACE INTO seasonal_patterns
            (pattern_id, scope, pattern_type, period_label, avg_return_pct, win_rate, n_samples, is_significant, discovered_date)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (pid, scope, ptype, label, avg_ret, win_rate, n, is_sig, today_str))
        patterns_saved += 1

    # MARKET-wide seasonality
    mkt_by_dow = defaultdict(list)
    mkt_by_wom = defaultdict(list)
    mkt_by_month = defaultdict(list)
    mkt_by_quarter = defaultdict(list)
    sect_by_dow = defaultdict(lambda: defaultdict(list))
    sect_by_month = defaultdict(lambda: defaultdict(list))

    for rec in all_rets:
        mkt_by_dow[rec['dow']].append(rec['ret'])
        mkt_by_wom[rec['wom']].append(rec['ret'])
        mkt_by_month[rec['month']].append(rec['ret'])
        mkt_by_quarter[rec['quarter']].append(rec['ret'])
        sect = rec['sector']
        sect_by_dow[sect][rec['dow']].append(rec['ret'])
        sect_by_month[sect][rec['month']].append(rec['ret'])

    for dow, vals in mkt_by_dow.items():
        label = day_names[dow] if dow < len(day_names) else str(dow)
        save_pattern(conn, 'MARKET', 'DAY_OF_WEEK', label, vals)
    for wom, vals in mkt_by_wom.items():
        save_pattern(conn, 'MARKET', 'WEEK_OF_MONTH', f"Week{wom}", vals)
    for m, vals in mkt_by_month.items():
        save_pattern(conn, 'MARKET', 'MONTH', month_names[m-1], vals)
    for q, vals in mkt_by_quarter.items():
        save_pattern(conn, 'MARKET', 'QUARTER', f"Q{q}", vals)

    # Per-sector seasonality
    for sector, by_dow in sect_by_dow.items():
        scope = f"SECTOR:{sector}"
        for dow, vals in by_dow.items():
            label = day_names[dow] if dow < len(day_names) else str(dow)
            save_pattern(conn, scope, 'DAY_OF_WEEK', label, vals)
    for sector, by_month in sect_by_month.items():
        scope = f"SECTOR:{sector}"
        for m, vals in by_month.items():
            save_pattern(conn, scope, 'MONTH', month_names[m-1], vals)

    conn.commit()
    return patterns_saved

# ── Regime cycles ─────────────────────────────────────────────────────────────

def compute_regime_cycles(conn, today_str):
    """How long does each regime last? What triggers transitions?"""
    rows = conn.execute(
        "SELECT date, regime FROM regime_history ORDER BY date ASC"
    ).fetchall()
    if len(rows) < 10:
        return 0

    cycles_saved = 0
    from collections import defaultdict
    regime_durations = defaultdict(list)
    prev_regime = None
    streak = 0
    for r in rows:
        if r['regime'] == prev_regime:
            streak += 1
        else:
            if prev_regime is not None and streak > 0:
                regime_durations[prev_regime].append(streak)
            prev_regime = r['regime']
            streak = 1
    if prev_regime and streak > 0:
        regime_durations[prev_regime].append(streak)

    for regime, durations in regime_durations.items():
        if len(durations) < 2:
            continue
        avg_dur = float(np.mean(durations))
        cid = hashlib.md5(f"REGIME_CYCLE|{regime}|{today_str}".encode()).hexdigest()[:16]
        conn.execute("""
            INSERT OR REPLACE INTO market_cycles
            (cycle_id, symbol, sector, cycle_type, period_days, amplitude_pct,
             phase_days, confidence, next_peak_date, next_trough_date, discovered_date, notes)
            VALUES (?,NULL,NULL,?,?,?,?,?,NULL,NULL,?,?)
        """, (
            cid, 'REGIME_CYCLE', avg_dur, 0.0, 0.0,
            float(min(1.0, len(durations) / 10)),
            today_str,
            json.dumps({
                'regime': regime,
                'avg_duration_days': avg_dur,
                'min_days': int(min(durations)),
                'max_days': int(max(durations)),
                'n_occurrences': len(durations),
            })
        ))
        cycles_saved += 1

    conn.commit()
    return cycles_saved

# ── Per-stock ACF worker ──────────────────────────────────────────────────────

def compute_stock_acf_cycles(args):
    """Worker: compute ACF cycles for one stock."""
    symbol, today_str = args
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time ASC",
            (symbol,)
        ).fetchall()
        conn.close()
        closes = np.array([r['close'] for r in rows], dtype=float)
        if len(closes) < 40:
            return symbol, []
        rets = np.diff(closes) / (closes[:-1] + 1e-10)
        peaks = find_acf_cycles(rets, max_lag=30)
        return symbol, peaks
    except Exception:
        return symbol, []

# ── Main run ──────────────────────────────────────────────────────────────────

def cmd_run():
    t0 = time.time()
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    ensure_tables(conn)

    row = conn.execute(
        "SELECT id FROM cycle_hunter_runs WHERE run_date=?", (today_str,)
    ).fetchone()
    if row:
        print(json.dumps({"status": "already_run", "date": today_str}))
        conn.close()
        return

    cycles_found = 0
    patterns_found = 0

    # ── 1. FFT on market index ────────────────────────────────────────────────
    print(json.dumps({"step": "fft_market_index", "status": "start"}))
    index_times, index_prices = get_market_index_prices(conn, n_stocks=30)
    if len(index_prices) >= 20:
        fft_cycles = find_dominant_cycles(index_prices, min_period=5, max_period=66)
        for cyc in fft_cycles:
            period = cyc['period_days']
            power = cyc['power']
            phase = phase_from_cycle(index_prices, period)
            # Estimate next peak/trough
            import datetime as dt
            today = dt.date.today()
            next_peak = (today + dt.timedelta(days=phase)).isoformat()
            next_trough = (today + dt.timedelta(days=phase + int(period // 2))).isoformat()
            cid = hashlib.md5(f"FFT|MARKET|{period:.1f}|{today_str}".encode()).hexdigest()[:16]
            # Amplitude: std of detrended prices as pct of mean
            prices_arr = np.array(index_prices)
            amplitude = float(np.std(prices_arr) / (np.mean(prices_arr) + 1e-10) * 100)
            noise_floor = np.median(np.abs(np.fft.rfft(prices_arr))) + 1e-10
            confidence = float(min(1.0, power / (noise_floor * 5)))
            conn.execute("""
                INSERT OR REPLACE INTO market_cycles
                (cycle_id, symbol, sector, cycle_type, period_days, amplitude_pct,
                 phase_days, confidence, next_peak_date, next_trough_date, discovered_date, notes)
                VALUES (?,NULL,NULL,?,?,?,?,?,?,?,?,?)
            """, (cid, 'FFT', period, amplitude, float(phase), confidence,
                  next_peak, next_trough, today_str,
                  json.dumps({"power": power, "scope": "market_index"})))
            cycles_found += 1
        conn.commit()
    print(json.dumps({"step": "fft_market_index", "cycles": cycles_found}))

    # ── 2. Per-stock ACF cycles ───────────────────────────────────────────────
    print(json.dumps({"step": "per_stock_acf", "status": "start"}))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM ohlcv_history GROUP BY symbol HAVING COUNT(*) >= 40"
    ).fetchall()]
    n_workers = min(7, max(1, cpu_count() - 1))

    for batch_start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[batch_start:batch_start + BATCH_SIZE]
        args = [(sym, today_str) for sym in batch]
        with Pool(processes=n_workers) as pool:
            results = pool.map(compute_stock_acf_cycles, args)

        for symbol, peaks in results:
            for peak in peaks:
                lag = peak['lag']
                acf = peak['acf']
                cid = hashlib.md5(f"AUTOCORR|{symbol}|{lag}|{today_str}".encode()).hexdigest()[:16]
                conn.execute("""
                    INSERT OR REPLACE INTO market_cycles
                    (cycle_id, symbol, sector, cycle_type, period_days, amplitude_pct,
                     phase_days, confidence, next_peak_date, next_trough_date, discovered_date, notes)
                    VALUES (?,?,NULL,?,?,?,?,?,NULL,NULL,?,?)
                """, (cid, symbol, 'AUTOCORR', float(lag), 0.0, 0.0,
                      float(min(1.0, abs(acf))), today_str,
                      json.dumps({"acf": acf, "lag_days": lag})))
                cycles_found += 1

        conn.commit()
        gc.collect()
        print(json.dumps({
            "progress": f"{batch_start + len(batch)}/{len(symbols)}",
            "cycles_so_far": cycles_found,
        }))

    # ── 3. Calendar seasonality ───────────────────────────────────────────────
    print(json.dumps({"step": "calendar_seasonality", "status": "start"}))
    patterns_found = compute_seasonality(conn, today_str)
    print(json.dumps({"step": "calendar_seasonality", "patterns": patterns_found}))

    # ── 4. Regime-conditioned cycles ──────────────────────────────────────────
    print(json.dumps({"step": "regime_cycles", "status": "start"}))
    regime_cycles = compute_regime_cycles(conn, today_str)
    cycles_found += regime_cycles
    print(json.dumps({"step": "regime_cycles", "cycles": regime_cycles}))

    duration = time.time() - t0
    conn.execute(
        "INSERT INTO cycle_hunter_runs (run_date, cycles_found, patterns_found, duration_seconds) VALUES (?,?,?,?)",
        (today_str, cycles_found, patterns_found, duration)
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "complete",
        "date": today_str,
        "cycles_found": cycles_found,
        "patterns_found": patterns_found,
        "duration_seconds": round(duration, 1),
    }))

def cmd_status():
    conn = get_db()
    rows = conn.execute(
        "SELECT run_date, cycles_found, patterns_found, duration_seconds, created_at "
        "FROM cycle_hunter_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    for r in rows:
        print(json.dumps(dict(r)))

def cmd_report():
    conn = get_db()
    print("=== Top Cycles ===")
    rows = conn.execute(
        "SELECT cycle_type, symbol, period_days, confidence, next_peak_date, discovered_date "
        "FROM market_cycles ORDER BY confidence DESC LIMIT 20"
    ).fetchall()
    for r in rows:
        print(json.dumps(dict(r)))
    print("\n=== Significant Seasonal Patterns ===")
    rows = conn.execute(
        "SELECT scope, pattern_type, period_label, avg_return_pct, win_rate, n_samples "
        "FROM seasonal_patterns WHERE is_significant=1 ORDER BY avg_return_pct DESC LIMIT 20"
    ).fetchall()
    for r in rows:
        print(json.dumps(dict(r)))
    conn.close()

if __name__ == '__main__':
    args = sys.argv[1:]
    cmd = args[0] if args else 'run'
    if cmd == 'run':
        cmd_run()
    elif cmd == 'status':
        cmd_status()
    elif cmd == 'report':
        cmd_report()
    else:
        print(json.dumps({"error": "unknown command", "usage": "run | status | report"}))
        sys.exit(1)
