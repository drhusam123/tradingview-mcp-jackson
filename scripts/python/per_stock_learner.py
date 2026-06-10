#!/usr/bin/env python3
"""
Per-Stock Learner — Individual Stock DNA Profiler
Learns the behavioral fingerprint of each of 247 EGX stocks:
RSI optimal levels, momentum duration, volatility personality,
volume spike thresholds, seasonal effects, Hurst exponent.
"""
import os, sys, json, sqlite3, datetime, gc, math, time
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
    CREATE TABLE IF NOT EXISTS stock_profiles_deep (
        symbol TEXT,
        computed_date TEXT,
        rsi_optimal_buy REAL,
        rsi_optimal_sell REAL,
        rsi_mean_reversion_speed REAL,
        momentum_avg_duration_days INTEGER,
        momentum_avg_magnitude_pct REAL,
        momentum_success_rate REAL,
        avg_atr_pct REAL,
        vol_regime_low REAL,
        vol_regime_high REAL,
        avg_volume REAL,
        volume_spike_threshold REAL,
        accumulation_score REAL,
        best_day_of_week INTEGER,
        worst_day_of_week INTEGER,
        best_month INTEGER,
        worst_month INTEGER,
        avg_drawdown_pct REAL,
        max_drawdown_pct REAL,
        recovery_days_avg REAL,
        trend_persistence_score REAL,
        mean_reversion_score REAL,
        n_bars INTEGER,
        PRIMARY KEY (symbol, computed_date)
    );

    CREATE TABLE IF NOT EXISTS per_stock_learner_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        symbols_processed INTEGER,
        symbols_skipped INTEGER,
        symbols_failed INTEGER,
        duration_seconds REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# ── Math helpers ──────────────────────────────────────────────────────────────

def hurst_exponent(ts):
    """0.5=random walk, >0.5=trending, <0.5=mean-reverting"""
    ts = np.array(ts, dtype=float)
    if len(ts) < 20:
        return 0.5
    lags = range(2, min(20, len(ts) // 2))
    tau = []
    variance = []
    for lag in lags:
        tau.append(lag)
        variance.append(np.std(np.subtract(ts[lag:], ts[:-lag])))
    lags_log = np.log(list(lags))
    var_log = np.log(np.array(variance) + 1e-12)
    poly = np.polyfit(lags_log, var_log, 1)
    return float(poly[0] * 0.5)

def compute_rsi(closes, period=14):
    """Compute RSI series from close prices."""
    closes = np.array(closes, dtype=float)
    if len(closes) < period + 1:
        return np.full(len(closes), 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    rsi_vals = np.full(len(closes), 50.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / (avg_loss + 1e-10)
        rsi_vals[i + 1] = 100 - (100 / (1 + rs))
    return rsi_vals

def compute_atr(highs, lows, closes, period=14):
    """Compute ATR series."""
    highs = np.array(highs, dtype=float)
    lows = np.array(lows, dtype=float)
    closes = np.array(closes, dtype=float)
    if len(closes) < 2:
        return np.zeros(len(closes))
    tr = np.maximum(highs[1:] - lows[1:],
         np.maximum(np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1])))
    atr = np.zeros(len(closes))
    if len(tr) >= period:
        atr[period] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i + 1] = (atr[i] * (period - 1) + tr[i]) / period
    return atr

# ── Per-stock computation ─────────────────────────────────────────────────────

def compute_stock_profile(args):
    """Worker function: compute full DNA for one stock."""
    symbol, today_str = args
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT bar_time, open, high, low, close, volume FROM ohlcv_history "
            "WHERE symbol=? ORDER BY bar_time ASC",
            (symbol,)
        ).fetchall()
        conn.close()

        if len(rows) < 60:
            return symbol, None, "too_few_bars"

        bar_times = [r['bar_time'] for r in rows]
        opens  = np.array([r['open']   for r in rows], dtype=float)
        highs  = np.array([r['high']   for r in rows], dtype=float)
        lows   = np.array([r['low']    for r in rows], dtype=float)
        closes = np.array([r['close']  for r in rows], dtype=float)
        volumes = np.array([r['volume'] for r in rows], dtype=float)
        n = len(closes)

        # Day of week and month from Unix timestamps
        import datetime as dt
        dows = np.array([dt.datetime.utcfromtimestamp(t).weekday() for t in bar_times])
        months = np.array([dt.datetime.utcfromtimestamp(t).month for t in bar_times])

        # Returns
        rets = np.diff(closes) / (closes[:-1] + 1e-10)

        # ── RSI DNA ──────────────────────────────────────────────────────────
        rsi_vals = compute_rsi(closes, period=14)
        rsi_buckets = np.arange(5, 105, 5)  # 5,10,...,100
        fwd5_rets = np.full(n, np.nan)
        for i in range(n - 5):
            fwd5_rets[i] = (closes[i + 5] - closes[i]) / (closes[i] + 1e-10)

        bucket_returns = {}
        for b in rsi_buckets:
            mask = (rsi_vals >= b - 2.5) & (rsi_vals < b + 2.5) & ~np.isnan(fwd5_rets)
            if mask.sum() >= 3:
                bucket_returns[b] = float(np.mean(fwd5_rets[mask]))

        rsi_optimal_buy = 30.0
        rsi_optimal_sell = 70.0
        if bucket_returns:
            rsi_optimal_buy = float(min(bucket_returns, key=lambda b: b if bucket_returns[b] == max(bucket_returns.values()) else 999))
            rsi_optimal_sell = float(max(bucket_returns, key=lambda b: b if bucket_returns[b] == min(bucket_returns.values()) else -1))

        # RSI mean reversion speed: avg bars to cross 50 from oversold (<30)
        mr_speed = 10.0
        mr_times = []
        in_oversold = False
        oversold_start = 0
        for i in range(n):
            if rsi_vals[i] < 30 and not in_oversold:
                in_oversold = True
                oversold_start = i
            elif rsi_vals[i] > 50 and in_oversold:
                mr_times.append(i - oversold_start)
                in_oversold = False
        if mr_times:
            mr_speed = float(np.mean(mr_times))

        # ── Momentum DNA ─────────────────────────────────────────────────────
        # Count consecutive up-day streaks
        up_streaks = []
        current_streak = 0
        streak_mag = 1.0
        for i, r in enumerate(rets):
            if r > 0:
                current_streak += 1
                streak_mag *= (1 + r)
            else:
                if current_streak >= 2:
                    up_streaks.append((current_streak, (streak_mag - 1) * 100))
                current_streak = 0
                streak_mag = 1.0
        if current_streak >= 2:
            up_streaks.append((current_streak, (streak_mag - 1) * 100))

        momentum_avg_duration = float(np.mean([s[0] for s in up_streaks])) if up_streaks else 3.0
        momentum_avg_magnitude = float(np.mean([s[1] for s in up_streaks])) if up_streaks else 2.0
        # Success rate: fraction of up-streaks where 5-day forward return also positive
        success_count = 0
        for i in range(len(rets) - 5):
            if rets[i] > 0:
                fwd = np.sum(rets[i:i+5])
                if fwd > 0:
                    success_count += 1
        total_up = np.sum(rets > 0)
        momentum_success_rate = float(success_count / (total_up + 1e-5))

        # ── Volatility DNA ───────────────────────────────────────────────────
        atr_vals = compute_atr(highs, lows, closes, period=14)
        atr_pct = atr_vals / (closes + 1e-10) * 100
        valid_atr = atr_pct[atr_pct > 0]
        avg_atr_pct = float(np.mean(valid_atr)) if len(valid_atr) > 0 else 2.0
        vol_regime_low = float(np.percentile(valid_atr, 25)) if len(valid_atr) > 4 else avg_atr_pct * 0.5
        vol_regime_high = float(np.percentile(valid_atr, 75)) if len(valid_atr) > 4 else avg_atr_pct * 1.5

        # ── Volume DNA ───────────────────────────────────────────────────────
        avg_volume = float(np.mean(volumes))
        # 20-day volume MA ratio
        vol_ratio = np.zeros(n)
        for i in range(20, n):
            ma20 = np.mean(volumes[i-20:i])
            vol_ratio[i] = volumes[i] / (ma20 + 1e-5)
        spike_threshold = float(np.percentile(vol_ratio[vol_ratio > 0], 90)) if np.sum(vol_ratio > 0) > 5 else 2.0

        # Accumulation score: price gain on high-volume days vs low-volume days
        if n >= 20:
            median_vol = np.median(volumes)
            high_vol_mask = volumes > median_vol
            low_vol_mask = ~high_vol_mask
            hv_ret = np.mean(rets[high_vol_mask[1:]]) if high_vol_mask[1:].sum() > 0 else 0
            lv_ret = np.mean(rets[low_vol_mask[1:]]) if low_vol_mask[1:].sum() > 0 else 0
            accumulation_score = float(np.clip((hv_ret - lv_ret) * 100, -100, 100))
        else:
            accumulation_score = 0.0

        # ── Seasonal DNA ─────────────────────────────────────────────────────
        dow_returns = {}
        for d in range(5):  # Mon=0 ... Fri=4 (EGX Mon-Thu)
            mask = dows[1:] == d
            if mask.sum() >= 3:
                dow_returns[d] = float(np.mean(rets[mask]))
        best_dow = int(max(dow_returns, key=dow_returns.get)) if dow_returns else 0
        worst_dow = int(min(dow_returns, key=dow_returns.get)) if dow_returns else 3

        month_returns = {}
        for m in range(1, 13):
            mask = months[1:] == m
            if mask.sum() >= 2:
                month_returns[m] = float(np.mean(rets[mask]))
        best_month = int(max(month_returns, key=month_returns.get)) if month_returns else 1
        worst_month = int(min(month_returns, key=month_returns.get)) if month_returns else 8

        # ── Drawdown DNA ─────────────────────────────────────────────────────
        drawdowns = []
        recovery_days_list = []
        peak = closes[0]
        in_dd = False
        dd_start = 0
        dd_peak = closes[0]
        for i in range(1, n):
            if closes[i] > peak:
                if in_dd:
                    recovery_days_list.append(i - dd_start)
                    in_dd = False
                peak = closes[i]
            else:
                dd_pct = (peak - closes[i]) / (peak + 1e-10) * 100
                if dd_pct > 1.0:
                    if not in_dd:
                        in_dd = True
                        dd_start = i
                        dd_peak = peak
                    drawdowns.append(dd_pct)

        avg_drawdown_pct = float(np.mean(drawdowns)) if drawdowns else 5.0
        max_drawdown_pct = float(np.max(drawdowns)) if drawdowns else 10.0
        recovery_days_avg = float(np.mean(recovery_days_list)) if recovery_days_list else 15.0

        # ── Trend/MR score via Hurst ─────────────────────────────────────────
        h = hurst_exponent(closes)
        trend_persistence_score = float(np.clip((h - 0.5) * 200, -100, 100))
        mean_reversion_score = float(np.clip((0.5 - h) * 200, -100, 100))

        profile = {
            'symbol': symbol,
            'computed_date': today_str,
            'rsi_optimal_buy': rsi_optimal_buy,
            'rsi_optimal_sell': rsi_optimal_sell,
            'rsi_mean_reversion_speed': mr_speed,
            'momentum_avg_duration_days': int(round(momentum_avg_duration)),
            'momentum_avg_magnitude_pct': momentum_avg_magnitude,
            'momentum_success_rate': momentum_success_rate,
            'avg_atr_pct': avg_atr_pct,
            'vol_regime_low': vol_regime_low,
            'vol_regime_high': vol_regime_high,
            'avg_volume': avg_volume,
            'volume_spike_threshold': spike_threshold,
            'accumulation_score': accumulation_score,
            'best_day_of_week': best_dow,
            'worst_day_of_week': worst_dow,
            'best_month': best_month,
            'worst_month': worst_month,
            'avg_drawdown_pct': avg_drawdown_pct,
            'max_drawdown_pct': max_drawdown_pct,
            'recovery_days_avg': recovery_days_avg,
            'trend_persistence_score': trend_persistence_score,
            'mean_reversion_score': mean_reversion_score,
            'n_bars': n,
        }
        return symbol, profile, "ok"

    except Exception as e:
        return symbol, None, f"error:{e}"

# ── Main commands ─────────────────────────────────────────────────────────────

def cmd_run():
    t0 = time.time()
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    ensure_tables(conn)

    # Check if already run today
    row = conn.execute(
        "SELECT id FROM per_stock_learner_runs WHERE run_date=?", (today_str,)
    ).fetchone()
    if row:
        print(json.dumps({"status": "already_run", "date": today_str}))
        conn.close()
        return

    # Get all symbols with enough data
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM ohlcv_history GROUP BY symbol HAVING COUNT(*) >= 60"
    ).fetchall()]
    conn.close()

    total = len(symbols)
    processed = skipped = failed = 0
    n_workers = min(7, max(1, cpu_count() - 1))

    print(json.dumps({"status": "start", "total_symbols": total, "workers": n_workers, "date": today_str}))

    for batch_start in range(0, total, BATCH_SIZE):
        batch = symbols[batch_start:batch_start + BATCH_SIZE]
        args = [(sym, today_str) for sym in batch]

        with Pool(processes=n_workers) as pool:
            results = pool.map(compute_stock_profile, args)

        conn = get_db()
        for symbol, profile, status in results:
            if profile is not None:
                conn.execute("""
                    INSERT OR REPLACE INTO stock_profiles_deep
                    (symbol, computed_date, rsi_optimal_buy, rsi_optimal_sell,
                     rsi_mean_reversion_speed, momentum_avg_duration_days,
                     momentum_avg_magnitude_pct, momentum_success_rate,
                     avg_atr_pct, vol_regime_low, vol_regime_high,
                     avg_volume, volume_spike_threshold, accumulation_score,
                     best_day_of_week, worst_day_of_week, best_month, worst_month,
                     avg_drawdown_pct, max_drawdown_pct, recovery_days_avg,
                     trend_persistence_score, mean_reversion_score, n_bars)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    profile['symbol'], profile['computed_date'],
                    profile['rsi_optimal_buy'], profile['rsi_optimal_sell'],
                    profile['rsi_mean_reversion_speed'],
                    profile['momentum_avg_duration_days'],
                    profile['momentum_avg_magnitude_pct'],
                    profile['momentum_success_rate'],
                    profile['avg_atr_pct'], profile['vol_regime_low'],
                    profile['vol_regime_high'], profile['avg_volume'],
                    profile['volume_spike_threshold'], profile['accumulation_score'],
                    profile['best_day_of_week'], profile['worst_day_of_week'],
                    profile['best_month'], profile['worst_month'],
                    profile['avg_drawdown_pct'], profile['max_drawdown_pct'],
                    profile['recovery_days_avg'],
                    profile['trend_persistence_score'],
                    profile['mean_reversion_score'], profile['n_bars'],
                ))
                processed += 1
            elif "too_few_bars" in status:
                skipped += 1
            else:
                failed += 1
        conn.commit()
        conn.close()
        gc.collect()

        done = batch_start + len(batch)
        print(json.dumps({
            "progress": f"{done}/{total}",
            "batch_end": batch_start + len(batch),
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
        }))

    duration = time.time() - t0
    conn = get_db()
    conn.execute(
        "INSERT INTO per_stock_learner_runs (run_date, symbols_processed, symbols_skipped, symbols_failed, duration_seconds) VALUES (?,?,?,?,?)",
        (today_str, processed, skipped, failed, duration)
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "complete",
        "date": today_str,
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "duration_seconds": round(duration, 1),
    }))

def cmd_status():
    conn = get_db()
    rows = conn.execute(
        "SELECT run_date, symbols_processed, symbols_skipped, symbols_failed, duration_seconds, created_at "
        "FROM per_stock_learner_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    for r in rows:
        print(json.dumps(dict(r)))

def cmd_symbol(symbol):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM stock_profiles_deep WHERE symbol=? ORDER BY computed_date DESC LIMIT 1",
        (symbol.upper(),)
    ).fetchone()
    conn.close()
    if row:
        print(json.dumps(dict(row), indent=2))
    else:
        print(json.dumps({"error": "not_found", "symbol": symbol}))

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    cmd = args[0] if args else 'run'
    if cmd == 'run':
        cmd_run()
    elif cmd == 'status':
        cmd_status()
    elif cmd == 'symbol' and len(args) >= 2:
        cmd_symbol(args[1])
    else:
        print(json.dumps({"error": "unknown command", "usage": "run | status | symbol <SYM>"}))
        sys.exit(1)
