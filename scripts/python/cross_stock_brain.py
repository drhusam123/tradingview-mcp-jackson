#!/usr/bin/env python3
"""
Cross-Stock Brain — Lead/Lag Network, Correlation Clusters, Sector Rotation
Discovers how EGX stocks relate to each other:
- Correlation clusters (hierarchical clustering)
- Lead-lag relationships (top 50 liquid stocks)
- Sector rotation signals
- PCA factor decomposition
"""
import os, sys, json, sqlite3, datetime, gc, math, time, hashlib
from pathlib import Path
from collections import defaultdict
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
    CREATE TABLE IF NOT EXISTS stock_lead_lag (
        leader_symbol TEXT,
        follower_symbol TEXT,
        lag_days INTEGER,
        correlation REAL,
        granger_pvalue REAL,
        n_observations INTEGER,
        regime TEXT,
        computed_date TEXT,
        PRIMARY KEY (leader_symbol, follower_symbol, lag_days, regime)
    );

    CREATE TABLE IF NOT EXISTS correlation_clusters (
        cluster_id TEXT PRIMARY KEY,
        cluster_name TEXT,
        symbols TEXT,
        avg_correlation REAL,
        n_symbols INTEGER,
        dominant_sector TEXT,
        computed_date TEXT
    );

    CREATE TABLE IF NOT EXISTS sector_rotation_signals (
        signal_date TEXT,
        from_sector TEXT,
        to_sector TEXT,
        rotation_strength REAL,
        evidence TEXT,
        PRIMARY KEY (signal_date, from_sector, to_sector)
    );

    CREATE TABLE IF NOT EXISTS cross_stock_brain_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        lead_lag_pairs INTEGER,
        clusters_found INTEGER,
        rotation_signals INTEGER,
        duration_seconds REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# ── Data loading ──────────────────────────────────────────────────────────────

def load_return_matrix(conn, symbols, min_bars=60):
    """
    Load aligned return matrix for given symbols.
    Returns (bar_times_sorted, {symbol: returns_array})
    """
    if not symbols:
        return [], {}
    placeholders = ','.join(['?'] * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, bar_time, close FROM ohlcv_history "
        f"WHERE symbol IN ({placeholders}) ORDER BY bar_time ASC",
        symbols
    ).fetchall()

    sym_prices = defaultdict(dict)
    for r in rows:
        sym_prices[r['symbol']][r['bar_time']] = r['close']

    # Find common timestamps (bars present for >= half the symbols)
    time_counts = defaultdict(int)
    for sym, prices in sym_prices.items():
        for t in prices:
            time_counts[t] += 1
    min_present = max(2, len(symbols) // 2)
    common_times = sorted([t for t, c in time_counts.items() if c >= min_present])

    if len(common_times) < min_bars:
        return common_times, {}

    # Build aligned matrix — fill missing with previous value
    returns = {}
    for sym, prices in sym_prices.items():
        if len(prices) < min_bars:
            continue
        # Fill forward
        close_series = []
        last_close = None
        for t in common_times:
            if t in prices:
                last_close = prices[t]
            if last_close is not None:
                close_series.append(last_close)
            else:
                close_series.append(np.nan)
        closes = np.array(close_series, dtype=float)
        if np.sum(~np.isnan(closes)) < min_bars:
            continue
        rets = np.diff(closes) / (closes[:-1] + 1e-10)
        rets = np.where(np.isfinite(rets), rets, 0.0)
        returns[sym] = rets

    return common_times[1:], returns

def get_top_liquid_symbols(conn, n=50):
    rows = conn.execute(
        "SELECT symbol FROM symbol_liquidity_profile ORDER BY avg_daily_volume DESC LIMIT ?",
        (n,)
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT symbol FROM ohlcv_history GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT ?",
            (n,)
        ).fetchall()
    return [r[0] for r in rows]

def get_symbol_sectors(conn):
    rows = conn.execute("SELECT symbol, sector FROM stock_universe").fetchall()
    return {r['symbol']: r['sector'] or 'Unknown' for r in rows}

# ── Correlation helpers ───────────────────────────────────────────────────────

def pearson_corr(a, b):
    n = len(a)
    if n < 5:
        return 0.0
    ma, mb = np.mean(a), np.mean(b)
    num = np.sum((a - ma) * (b - mb))
    denom = math.sqrt(np.sum((a - ma)**2) * np.sum((b - mb)**2) + 1e-12)
    return float(num / denom)

def cross_corr_at_lag(a, b, lag):
    """Correlation of a[:-lag] with b[lag:] — a leads b by `lag` days."""
    if lag == 0:
        return pearson_corr(a, b)
    if lag > 0 and len(a) > lag:
        return pearson_corr(a[:-lag], b[lag:])
    return 0.0

def simple_granger_pvalue(x, y, lag=1):
    """
    Simplified Granger causality: does x[t-lag] add predictive power for y[t]?
    Returns approximate p-value using F-approximation from R² improvement.
    """
    n = len(y)
    if n < lag + 10:
        return 1.0
    # Restricted model: y[t] ~ y[t-1]
    yt = y[lag:]
    yt_lag = y[:-lag]
    # Add x lagged
    xt_lag = x[:-lag] if len(x) == len(y) else np.zeros(len(yt))
    n_eff = len(yt)

    def ols_r2(X_cols, Y):
        X = np.column_stack([np.ones(len(Y))] + list(X_cols))
        try:
            beta = np.linalg.lstsq(X, Y, rcond=None)[0]
            y_pred = X @ beta
            ss_res = np.sum((Y - y_pred)**2)
            ss_tot = np.sum((Y - np.mean(Y))**2) + 1e-12
            return float(1 - ss_res / ss_tot)
        except Exception:
            return 0.0

    r2_restricted = ols_r2([yt_lag], yt)
    r2_unrestricted = ols_r2([yt_lag, xt_lag], yt)
    delta_r2 = max(0, r2_unrestricted - r2_restricted)
    # F-stat approximation: p-value heuristic
    if delta_r2 < 0.01:
        return 0.5
    f_stat = (delta_r2 / 1) / ((1 - r2_unrestricted) / (n_eff - 3) + 1e-10)
    # Approximate p-value: if F > 3.84, p < 0.05
    if f_stat > 10:
        return 0.01
    elif f_stat > 3.84:
        return 0.04
    elif f_stat > 2.71:
        return 0.10
    else:
        return 0.5

# ── Lead-lag detection ────────────────────────────────────────────────────────

def compute_lead_lag(conn, symbols, today_str, regime='ALL'):
    """Compute lead-lag for all pairs in symbols. Return count of saved pairs."""
    _, returns = load_return_matrix(conn, symbols, min_bars=60)
    sym_list = sorted(returns.keys())
    if len(sym_list) < 2:
        return 0

    saved = 0
    max_lag = 5
    for i, sym_a in enumerate(sym_list):
        for sym_b in sym_list[i+1:]:
            a = returns[sym_a]
            b = returns[sym_b]
            n = min(len(a), len(b))
            if n < 60:
                continue
            a, b = a[:n], b[:n]
            for lag in range(1, max_lag + 1):
                # Test A leads B
                r_ab = cross_corr_at_lag(a, b, lag)
                if abs(r_ab) > 0.3:
                    p_val = simple_granger_pvalue(a[:n-lag], b[lag:])
                    if p_val < 0.1:
                        conn.execute("""
                            INSERT OR REPLACE INTO stock_lead_lag
                            (leader_symbol, follower_symbol, lag_days, correlation, granger_pvalue,
                             n_observations, regime, computed_date)
                            VALUES (?,?,?,?,?,?,?,?)
                        """, (sym_a, sym_b, lag, r_ab, p_val, n - lag, regime, today_str))
                        saved += 1
                # Test B leads A
                r_ba = cross_corr_at_lag(b, a, lag)
                if abs(r_ba) > 0.3:
                    p_val = simple_granger_pvalue(b[:n-lag], a[lag:])
                    if p_val < 0.1:
                        conn.execute("""
                            INSERT OR REPLACE INTO stock_lead_lag
                            (leader_symbol, follower_symbol, lag_days, correlation, granger_pvalue,
                             n_observations, regime, computed_date)
                            VALUES (?,?,?,?,?,?,?,?)
                        """, (sym_b, sym_a, lag, r_ba, p_val, n - lag, regime, today_str))
                        saved += 1
    conn.commit()
    return saved

# ── Correlation clustering ─────────────────────────────────────────────────────

def compute_correlation_clusters(conn, symbols, today_str):
    """Hierarchical clustering of stocks by return correlation."""
    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
    except ImportError:
        print(json.dumps({"warning": "scipy not available, skipping clustering"}))
        return 0

    _, returns = load_return_matrix(conn, symbols, min_bars=60)
    sym_list = sorted(returns.keys())
    n = len(sym_list)
    if n < 4:
        return 0

    sym_sectors = get_symbol_sectors(conn)

    # Build correlation matrix (use recent 60 bars for speed)
    mat = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            r = pearson_corr(returns[sym_list[i]][-60:], returns[sym_list[j]][-60:])
            mat[i, j] = r
            mat[j, i] = r
    np.fill_diagonal(mat, 1.0)

    # Distance matrix: 1 - |correlation|
    dist_mat = 1 - np.abs(mat)
    np.fill_diagonal(dist_mat, 0.0)
    dist_mat = np.clip(dist_mat, 0, 1)

    try:
        condensed = squareform(dist_mat, checks=False)
        Z = linkage(condensed, method='ward')
        labels = fcluster(Z, t=0.5, criterion='distance')
    except Exception as e:
        print(json.dumps({"warning": f"clustering failed: {e}"}))
        return 0

    # Group by cluster label
    clusters = defaultdict(list)
    for i, label in enumerate(labels):
        clusters[label].append(sym_list[i])

    saved = 0
    for label, members in clusters.items():
        if len(members) < 2:
            continue
        # Compute avg intra-cluster correlation
        corrs = []
        for i in range(len(members)):
            for j in range(i+1, len(members)):
                ii = sym_list.index(members[i])
                jj = sym_list.index(members[j])
                corrs.append(mat[ii, jj])
        avg_corr = float(np.mean(corrs)) if corrs else 0.0
        if avg_corr < 0.4:
            continue

        # Dominant sector
        sector_counts = defaultdict(int)
        for sym in members:
            sector_counts[sym_sectors.get(sym, 'Unknown')] += 1
        dominant_sector = max(sector_counts, key=sector_counts.get)

        cluster_id = hashlib.md5(f"CLUSTER|{sorted(members)}|{today_str}".encode()).hexdigest()[:16]
        cluster_name = f"{dominant_sector}_C{label}"
        conn.execute("""
            INSERT OR REPLACE INTO correlation_clusters
            (cluster_id, cluster_name, symbols, avg_correlation, n_symbols, dominant_sector, computed_date)
            VALUES (?,?,?,?,?,?,?)
        """, (
            cluster_id, cluster_name,
            json.dumps(sorted(members)),
            avg_corr, len(members),
            dominant_sector, today_str
        ))
        saved += 1

    conn.commit()
    return saved

# ── Sector rotation ───────────────────────────────────────────────────────────

def compute_sector_rotation(conn, today_str):
    """Detect money flows between sectors based on weekly performance trends."""
    import datetime as dt

    sym_sectors = get_symbol_sectors(conn)
    rows = conn.execute(
        "SELECT symbol, bar_time, close FROM ohlcv_history ORDER BY bar_time ASC"
    ).fetchall()

    # Build weekly returns per sector
    sym_bar = defaultdict(list)
    for r in rows:
        sym_bar[r['symbol']].append((r['bar_time'], r['close']))

    # Group by week
    def week_key(ts):
        d = dt.datetime.utcfromtimestamp(ts)
        return f"{d.year}-W{d.isocalendar()[1]:02d}"

    sector_weekly = defaultdict(lambda: defaultdict(list))
    for sym, bars in sym_bar.items():
        sector = sym_sectors.get(sym, 'Unknown')
        if sector == 'Unknown' or len(bars) < 5:
            continue
        for i in range(1, len(bars)):
            t, c = bars[i]
            prev_c = bars[i-1][1]
            if prev_c > 0:
                ret = (c - prev_c) / prev_c
                wk = week_key(t)
                sector_weekly[sector][wk].append(ret)

    # Compute weekly avg returns per sector
    sector_weekly_avg = {}
    for sector, week_data in sector_weekly.items():
        weekly_ret = {}
        for wk, rets in week_data.items():
            weekly_ret[wk] = float(np.mean(rets))
        sector_weekly_avg[sector] = weekly_ret

    all_weeks = sorted(set(wk for sw in sector_weekly_avg.values() for wk in sw.keys()))
    if len(all_weeks) < 4:
        return 0

    sectors = list(sector_weekly_avg.keys())
    saved = 0
    # Look for rotation: sector A weakening, sector B strengthening for 3+ consecutive weeks
    for i, sec_a in enumerate(sectors):
        for sec_b in sectors:
            if sec_a == sec_b:
                continue
            a_rets = sector_weekly_avg[sec_a]
            b_rets = sector_weekly_avg[sec_b]
            # Find windows of 3+ weeks where A declining and B rising
            for w_idx in range(len(all_weeks) - 3):
                window = all_weeks[w_idx:w_idx+3]
                a_trend = [a_rets.get(wk, 0) for wk in window]
                b_trend = [b_rets.get(wk, 0) for wk in window]
                a_declining = all(a_trend[i] < a_trend[i-1] for i in range(1, len(a_trend)))
                b_rising = all(b_trend[i] > b_trend[i-1] for i in range(1, len(b_trend)))
                if a_declining and b_rising:
                    a_net = sum(a_trend)
                    b_net = sum(b_trend)
                    strength = float(min(100, abs(b_net - a_net) * 1000))
                    signal_date = window[-1]
                    conn.execute("""
                        INSERT OR REPLACE INTO sector_rotation_signals
                        (signal_date, from_sector, to_sector, rotation_strength, evidence)
                        VALUES (?,?,?,?,?)
                    """, (
                        signal_date, sec_a, sec_b, strength,
                        json.dumps({
                            "weeks": window,
                            "a_returns": a_trend,
                            "b_returns": b_trend,
                        })
                    ))
                    saved += 1
    conn.commit()
    return saved

# ── Main commands ─────────────────────────────────────────────────────────────

def cmd_run():
    t0 = time.time()
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    ensure_tables(conn)

    row = conn.execute(
        "SELECT id FROM cross_stock_brain_runs WHERE run_date=?", (today_str,)
    ).fetchone()
    if row:
        print(json.dumps({"status": "already_run", "date": today_str}))
        conn.close()
        return

    # ── 1. Lead-lag for top 50 liquid stocks ──────────────────────────────────
    print(json.dumps({"step": "lead_lag", "status": "start"}))
    top50 = get_top_liquid_symbols(conn, n=50)
    print(json.dumps({"step": "lead_lag", "n_symbols": len(top50)}))
    lead_lag_pairs = compute_lead_lag(conn, top50, today_str, regime='ALL')

    # Also compute per regime if data available
    try:
        regime_rows = conn.execute(
            "SELECT date, regime FROM regime_history ORDER BY date DESC LIMIT 200"
        ).fetchall()
        if regime_rows:
            # Get recent bull/bear dates
            bull_dates = {r['date'] for r in regime_rows if r['regime'] == 'BULL'}
            bear_dates = {r['date'] for r in regime_rows if r['regime'] == 'BEAR'}
            if len(bull_dates) >= 30:
                lead_lag_pairs += compute_lead_lag(conn, top50[:30], today_str, regime='BULL')
            if len(bear_dates) >= 30:
                lead_lag_pairs += compute_lead_lag(conn, top50[:30], today_str, regime='BEAR')
    except Exception as e:
        print(json.dumps({"warning": f"regime lead_lag failed: {e}"}))

    print(json.dumps({"step": "lead_lag", "pairs_found": lead_lag_pairs}))
    gc.collect()

    # ── 2. Correlation clustering ─────────────────────────────────────────────
    print(json.dumps({"step": "correlation_clusters", "status": "start"}))
    all_symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM ohlcv_history GROUP BY symbol HAVING COUNT(*) >= 60"
    ).fetchall()]
    clusters_found = compute_correlation_clusters(conn, all_symbols, today_str)
    print(json.dumps({"step": "correlation_clusters", "clusters": clusters_found}))
    gc.collect()

    # ── 3. Sector rotation ────────────────────────────────────────────────────
    print(json.dumps({"step": "sector_rotation", "status": "start"}))
    rotation_signals = compute_sector_rotation(conn, today_str)
    print(json.dumps({"step": "sector_rotation", "signals": rotation_signals}))

    # ── 4. PCA factor decomposition ───────────────────────────────────────────
    print(json.dumps({"step": "pca_factors", "status": "start"}))
    try:
        _, returns = load_return_matrix(conn, top50, min_bars=60)
        sym_list = sorted(returns.keys())
        if len(sym_list) >= 5:
            ret_matrix = np.array([returns[s][-100:] for s in sym_list])  # shape (n_sym, T)
            # Standardize
            ret_matrix = (ret_matrix - ret_matrix.mean(axis=1, keepdims=True)) / (ret_matrix.std(axis=1, keepdims=True) + 1e-10)
            cov = np.cov(ret_matrix)
            eigenvalues, eigenvectors = np.linalg.eigh(cov)
            idx = np.argsort(eigenvalues)[::-1]
            eigenvalues = eigenvalues[idx]
            eigenvectors = eigenvectors[:, idx]
            total_var = np.sum(eigenvalues)
            top3_var = float(np.sum(eigenvalues[:3]) / (total_var + 1e-10))
            # Top loadings for first factor
            factor1_loadings = eigenvectors[:, 0]
            top_loaders = sorted(zip(sym_list, factor1_loadings.tolist()), key=lambda x: abs(x[1]), reverse=True)[:5]
            print(json.dumps({
                "step": "pca_factors",
                "top3_variance_explained": round(top3_var, 3),
                "factor1_top_loaders": [{"symbol": s, "loading": round(l, 3)} for s, l in top_loaders],
            }))
    except Exception as e:
        print(json.dumps({"step": "pca_factors", "warning": str(e)}))

    duration = time.time() - t0
    conn.execute(
        "INSERT INTO cross_stock_brain_runs (run_date, lead_lag_pairs, clusters_found, rotation_signals, duration_seconds) VALUES (?,?,?,?,?)",
        (today_str, lead_lag_pairs, clusters_found, rotation_signals, duration)
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "status": "complete",
        "date": today_str,
        "lead_lag_pairs": lead_lag_pairs,
        "clusters_found": clusters_found,
        "rotation_signals": rotation_signals,
        "duration_seconds": round(duration, 1),
    }))

def cmd_status():
    conn = get_db()
    rows = conn.execute(
        "SELECT run_date, lead_lag_pairs, clusters_found, rotation_signals, duration_seconds, created_at "
        "FROM cross_stock_brain_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    for r in rows:
        print(json.dumps(dict(r)))

def cmd_leaders():
    conn = get_db()
    print("=== Top Leader Stocks ===")
    rows = conn.execute("""
        SELECT leader_symbol, COUNT(*) as follower_count, AVG(correlation) as avg_corr, AVG(granger_pvalue) as avg_pval
        FROM stock_lead_lag
        WHERE granger_pvalue < 0.05
        GROUP BY leader_symbol
        ORDER BY follower_count DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        print(json.dumps(dict(r)))
    conn.close()

def cmd_clusters():
    conn = get_db()
    print("=== Correlation Clusters ===")
    rows = conn.execute(
        "SELECT cluster_name, n_symbols, avg_correlation, dominant_sector, computed_date "
        "FROM correlation_clusters ORDER BY avg_correlation DESC LIMIT 20"
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
    elif cmd == 'leaders':
        cmd_leaders()
    elif cmd == 'clusters':
        cmd_clusters()
    else:
        print(json.dumps({"error": "unknown command", "usage": "run | status | leaders | clusters"}))
        sys.exit(1)
