"""
causal_discovery_engine.py — Phase 22
EGX Market Intelligence: Causal Discovery & Transfer Entropy Engine

Commands:
  transfer_entropy   — compute sector-level lagged cross-correlations → causal links
  lagged_inference   — find optimal lags, validate with Granger-style test
  causal_stability   — rolling-window stability & causal half-life
  regime_causality   — rebuild causal graph filtered to a specific regime
  macro_transmission — macro indicator → sector → stock transmission chains
  build_full         — run transfer_entropy + lagged_inference + causal_stability

Usage:
  python causal_discovery_engine.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import time
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Optional scipy import (graceful fallback to manual t-test)
# ---------------------------------------------------------------------------
try:
    from scipy import stats as _scipy_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ---------------------------------------------------------------------------
# DB Setup
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS causal_chains (
        chain_id TEXT PRIMARY KEY,
        source_entity TEXT,
        target_entity TEXT,
        chain_type TEXT,
        lag_days INTEGER,
        strength REAL,
        p_value REAL,
        confidence REAL,
        regime_stable INTEGER DEFAULT 0,
        best_regime TEXT,
        causal_half_life_days REAL,
        validated INTEGER DEFAULT 0,
        last_validated TEXT,
        discovery_date TEXT
    );
    CREATE TABLE IF NOT EXISTS causal_stability (
        stability_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chain_id TEXT,
        period TEXT,
        strength_in_period REAL,
        p_value REAL,
        is_active INTEGER,
        drift_from_baseline REAL
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Math utilities (manual t-test / correlation)
# ---------------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs, ddof=1):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    variance = sum((x - m) ** 2 for x in xs) / (len(xs) - ddof)
    return math.sqrt(variance)


def _pearson(xs, ys):
    """Return (r, p_value) for two equal-length lists. Falls back gracefully."""
    n = len(xs)
    if n < 5:
        return 0.0, 1.0
    if HAS_SCIPY:
        try:
            r, p = _scipy_stats.pearsonr(xs, ys)
            return float(r), float(p)
        except Exception:
            pass
    # manual
    mx, my = _mean(xs), _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0, 1.0
    r = num / (dx * dy)
    r = max(-1.0, min(1.0, r))
    # t-statistic → p-value via erfc approximation
    if abs(r) == 1.0:
        return r, 0.0
    t = r * math.sqrt(n - 2) / math.sqrt(1 - r * r)
    p = _t_pvalue(t, n - 2)
    return r, p


def _t_pvalue(t, df):
    """Two-tailed p-value approximation using erfc."""
    # Use scipy if available
    if HAS_SCIPY:
        try:
            return float(_scipy_stats.t.sf(abs(t), df) * 2)
        except Exception:
            pass
    # Normal approximation (adequate for df > 20)
    z = abs(t)
    p = math.erfc(z / math.sqrt(2))
    return min(1.0, p)


def _chain_id(source, target, lag, chain_type):
    raw = f"{source}|{target}|{lag}|{chain_type}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_sector_returns(db):
    """
    Load daily returns per sector.
    Returns: {sector: {date_str: median_return}}
    """
    rows = db.execute("""
        SELECT su.sector, date(o.bar_time, 'unixepoch') AS dt,
               o.close, o.open
        FROM ohlcv_history_execution o
        JOIN stock_universe su ON su.symbol = o.symbol
        WHERE su.status = 'active' OR su.status IS NULL
        ORDER BY su.sector, o.symbol, o.bar_time
    """).fetchall()

    # Build {sector: {symbol: [(date, close)]}}
    sym_data = defaultdict(lambda: defaultdict(list))
    sector_of = {}
    for r in rows:
        sym_data[r['sector']][r['dt']].append(r['close'])
        sector_of[r['dt']] = r['sector']

    # Compute daily returns per symbol, then aggregate to sector median
    sector_returns = defaultdict(dict)
    for sector, date_closes in sym_data.items():
        dates_sorted = sorted(date_closes.keys())
        prev_medians = {}
        for dt in dates_sorted:
            closes = date_closes[dt]
            med = sorted(closes)[len(closes) // 2]
            if sector in prev_medians and prev_medians[sector] != 0:
                ret = (med - prev_medians[sector]) / prev_medians[sector]
                sector_returns[sector][dt] = ret
            prev_medians[sector] = med

    return dict(sector_returns)


def _load_symbol_returns(db):
    """Returns {symbol: {date_str: return}}"""
    rows = db.execute("""
        SELECT symbol, date(bar_time, 'unixepoch') AS dt, close
        FROM ohlcv_history_execution
        ORDER BY symbol, bar_time
    """).fetchall()
    sym = defaultdict(list)
    for r in rows:
        sym[r['symbol']].append((r['dt'], r['close']))

    result = {}
    for symbol, pairs in sym.items():
        rets = {}
        for i in range(1, len(pairs)):
            dt, c = pairs[i]
            _, pc = pairs[i - 1]
            if pc and pc != 0:
                rets[dt] = (c - pc) / pc
        result[symbol] = rets
    return result


def _load_sector_map(db):
    """Returns {symbol: sector}"""
    rows = db.execute("SELECT symbol, sector FROM stock_universe").fetchall()
    return {r['symbol']: r['sector'] for r in rows}


def _load_regime_history(db):
    """Returns {date_str: regime}"""
    try:
        rows = db.execute("SELECT * FROM regime_history").fetchall()
    except Exception:
        return {}
    mapping = {}
    for r in rows:
        cols = r.keys()
        date_col = next((c for c in cols if 'date' in c.lower()), None)
        regime_col = next((c for c in cols if 'regime' in c.lower()), None)
        if date_col and regime_col:
            mapping[str(r[date_col])[:10]] = r[regime_col]
    return mapping


def _align_series(dict_a, dict_b, lag=0):
    """
    Align two {date: value} dicts with a lag.
    Returns (xs, ys) where xs[i] = dict_a[d], ys[i] = dict_b[d+lag_days]
    """
    dates_a = sorted(dict_a.keys())
    xs, ys = [], []
    for i, da in enumerate(dates_a):
        # Find ys date = da + lag trading days
        idx_b = i + lag
        if idx_b < len(dates_a):
            db_date = dates_a[idx_b]
            if db_date in dict_b:
                xs.append(dict_a[da])
                ys.append(dict_b[db_date])
    return xs, ys


def _common_dates(dict_a, dict_b):
    """Return sorted list of dates present in both dicts."""
    return sorted(set(dict_a.keys()) & set(dict_b.keys()))


# ---------------------------------------------------------------------------
# Command: transfer_entropy
# ---------------------------------------------------------------------------

def cmd_transfer_entropy(params):
    tau_max = params.get('tau_max', 5)
    n_sectors_limit = params.get('n_sectors', 10)
    t0 = time.time()

    db = get_db()
    sector_returns = _load_sector_returns(db)
    sectors = sorted(sector_returns.keys())[:n_sectors_limit]

    if not sectors:
        db.close()
        return {"error": "No sector data found", "n_sectors": 0}

    discovery_date = datetime.utcnow().strftime('%Y-%m-%d')
    links_found = []
    inserted = 0

    for src in sectors:
        for tgt in sectors:
            if src == tgt:
                continue
            src_ret = sector_returns[src]
            tgt_ret = sector_returns[tgt]
            best_r, best_lag, best_p = 0.0, 1, 1.0

            for lag in range(1, tau_max + 1):
                xs = []
                ys = []
                dates_src = sorted(src_ret.keys())
                for i, d in enumerate(dates_src):
                    j = i + lag
                    if j < len(dates_src):
                        d2 = dates_src[j]
                        if d2 in tgt_ret:
                            xs.append(src_ret[d])
                            ys.append(tgt_ret[d2])

                if len(xs) < 10:
                    continue

                r, p = _pearson(xs, ys)
                if abs(r) > abs(best_r):
                    best_r, best_lag, best_p = r, lag, p

            # Causal link criterion
            if abs(best_r) > 0.15 and best_p < 0.05:
                cid = _chain_id(src, tgt, best_lag, 'TRANSFER_ENTROPY')
                confidence = min(1.0, abs(best_r) * (1 - best_p))
                db.execute("""
                    INSERT OR REPLACE INTO causal_chains
                    (chain_id, source_entity, target_entity, chain_type,
                     lag_days, strength, p_value, confidence, discovery_date)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (cid, src, tgt, 'TRANSFER_ENTROPY',
                      best_lag, best_r, best_p, confidence, discovery_date))
                links_found.append({
                    'source': src, 'target': tgt,
                    'lag': best_lag, 'strength': round(best_r, 4),
                    'p_value': round(best_p, 6)
                })
                inserted += 1

    db.commit()

    # Top 10 links by |strength|
    top_links = sorted(links_found, key=lambda x: abs(x['strength']), reverse=True)[:10]
    elapsed = round(time.time() - t0, 2)
    db.close()

    return {
        'n_sectors': len(sectors),
        'n_links_found': inserted,
        'top_links': top_links,
        'execution_time': elapsed
    }


# ---------------------------------------------------------------------------
# Command: lagged_inference
# ---------------------------------------------------------------------------

def _granger_style_test(cause_series, effect_series, lag):
    """
    Manual Granger-style test:
    Model 1: effect[t] ~ mean(effect)  (baseline)
    Model 2: effect[t] ~ alpha + beta * cause[t-lag]
    Compare RSS; return (improvement_ratio, p_value)
    """
    n = len(effect_series)
    if n < lag + 5:
        return 0.0, 1.0

    y = effect_series[lag:]
    x_cause = cause_series[:n - lag]

    if len(y) != len(x_cause):
        min_l = min(len(y), len(x_cause))
        y = y[:min_l]
        x_cause = x_cause[:min_l]

    mean_y = _mean(y)
    rss_base = sum((yi - mean_y) ** 2 for yi in y)

    # OLS: y = a + b*x
    mean_x = _mean(x_cause)
    sxy = sum((x_cause[i] - mean_x) * (y[i] - mean_y) for i in range(len(y)))
    sxx = sum((x - mean_x) ** 2 for x in x_cause)
    if sxx == 0:
        return 0.0, 1.0
    b = sxy / sxx
    a = mean_y - b * mean_x
    y_pred = [a + b * x for x in x_cause]
    rss_model = sum((y[i] - y_pred[i]) ** 2 for i in range(len(y)))

    if rss_base == 0:
        return 0.0, 1.0

    improvement = (rss_base - rss_model) / rss_base
    # F-statistic approximation
    k = 1  # one predictor
    df1 = k
    df2 = len(y) - k - 1
    if df2 < 1:
        return improvement, 1.0
    F = (rss_model / df2) if rss_model > 0 else 0
    F_stat = ((rss_base - rss_model) / df1) / (rss_model / df2) if rss_model > 0 else 0
    p = _t_pvalue(math.sqrt(abs(F_stat)), df2) if F_stat > 0 else 1.0
    return improvement, p


def cmd_lagged_inference(params):
    min_lag = params.get('min_lag', 1)
    max_lag = params.get('max_lag', 10)

    db = get_db()
    sector_returns = _load_sector_returns(db)
    sym_returns = _load_symbol_returns(db)
    sector_map = _load_sector_map(db)
    discovery_date = datetime.utcnow().strftime('%Y-%m-%d')

    sectors = sorted(sector_returns.keys())
    sector_lead_lags = {}
    n_validated = 0

    # Sector → Sector optimal lag
    for src in sectors:
        for tgt in sectors:
            if src == tgt:
                continue
            src_dates = sorted(sector_returns[src].keys())
            src_vals = [sector_returns[src][d] for d in src_dates]
            tgt_dict = sector_returns[tgt]

            best_r, best_lag = 0.0, min_lag
            for lag in range(min_lag, max_lag + 1):
                ys = []
                xs = []
                for i, d in enumerate(src_dates):
                    j = i + lag
                    if j < len(src_dates):
                        d2 = src_dates[j]
                        if d2 in tgt_dict:
                            xs.append(src_vals[i])
                            ys.append(tgt_dict[d2])
                if len(xs) < 10:
                    continue
                r, p = _pearson(xs, ys)
                if abs(r) > abs(best_r):
                    best_r, best_lag = r, lag

            if abs(best_r) > 0.1:
                # Granger test
                tgt_dates = sorted(tgt_dict.keys())
                tgt_vals = [tgt_dict[d] for d in tgt_dates]
                impr, p_granger = _granger_style_test(src_vals, tgt_vals, best_lag)
                key = f"{src}->{tgt}"
                sector_lead_lags[key] = {
                    'lag': best_lag,
                    'strength': round(best_r, 4),
                    'granger_improvement': round(impr, 4),
                    'p_granger': round(p_granger, 6)
                }
                if p_granger < 0.1:
                    cid = _chain_id(src, tgt, best_lag, 'LAGGED_INFERENCE')
                    db.execute("""
                        INSERT OR REPLACE INTO causal_chains
                        (chain_id, source_entity, target_entity, chain_type,
                         lag_days, strength, p_value, confidence,
                         validated, last_validated, discovery_date)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (cid, src, tgt, 'LAGGED_INFERENCE',
                          best_lag, best_r, p_granger,
                          min(1.0, abs(best_r) * impr),
                          1, discovery_date, discovery_date))
                    n_validated += 1

    # Stock → Sector lead lags (sample first 50 stocks)
    stock_sector_lags = {}
    symbols = list(sym_returns.keys())[:50]
    for sym in symbols:
        sec = sector_map.get(sym)
        if not sec or sec not in sector_returns:
            continue
        sec_dict = sector_returns[sec]
        sym_dict = sym_returns[sym]
        dates = sorted(set(sym_dict.keys()) & set(sec_dict.keys()))
        if len(dates) < 30:
            continue
        sym_vals = [sym_dict[d] for d in dates]
        sec_vals = [sec_dict[d] for d in dates]
        best_r, best_lag = 0.0, 1
        for lag in range(min_lag, min(max_lag, 5) + 1):
            xs = sym_vals[:len(sym_vals) - lag]
            ys = sec_vals[lag:]
            if len(xs) < 10:
                continue
            r, _ = _pearson(xs, ys)
            if abs(r) > abs(best_r):
                best_r, best_lag = r, lag
        if abs(best_r) > 0.1:
            stock_sector_lags[sym] = {
                'sector': sec,
                'lag': best_lag,
                'strength': round(best_r, 4)
            }

    db.commit()
    db.close()

    return {
        'sector_lead_lags': sector_lead_lags,
        'stock_sector_lags': stock_sector_lags,
        'n_validated': n_validated
    }


# ---------------------------------------------------------------------------
# Command: causal_stability
# ---------------------------------------------------------------------------

def _rolling_windows(dates_sorted, window_days=180):
    """Yield (start_idx, end_idx) for 6-month rolling windows."""
    if not dates_sorted:
        return
    try:
        start_dt = datetime.strptime(dates_sorted[0], '%Y-%m-%d')
        end_dt = datetime.strptime(dates_sorted[-1], '%Y-%m-%d')
    except ValueError:
        return
    step = timedelta(days=30)
    window = timedelta(days=window_days)
    cur = start_dt
    while cur + window <= end_dt:
        ws = cur.strftime('%Y-%m-%d')
        we = (cur + window).strftime('%Y-%m-%d')
        yield ws, we
        cur += step


def cmd_causal_stability(params):
    chain_id_filter = params.get('chain_id', None)

    db = get_db()
    sector_returns = _load_sector_returns(db)

    query = "SELECT * FROM causal_chains"
    args = []
    if chain_id_filter:
        query += " WHERE chain_id = ?"
        args.append(chain_id_filter)
    chains = db.execute(query, args).fetchall()

    n_tested = 0
    stabilities = []
    unstable = []

    for chain in chains:
        src = chain['source_entity']
        tgt = chain['target_entity']
        lag = chain['lag_days'] or 1
        baseline_r = chain['strength'] or 0.0

        src_ret = sector_returns.get(src, {})
        tgt_ret = sector_returns.get(tgt, {})
        if not src_ret or not tgt_ret:
            continue

        all_dates = sorted(set(src_ret.keys()) & set(tgt_ret.keys()))
        if len(all_dates) < 60:
            continue

        window_results = []
        for ws, we in _rolling_windows(all_dates):
            w_dates = [d for d in all_dates if ws <= d <= we]
            if len(w_dates) < 20:
                continue
            xs, ys = [], []
            for i, d in enumerate(w_dates):
                j = i + lag
                if j < len(w_dates):
                    d2 = w_dates[j]
                    if d2 in tgt_ret:
                        xs.append(src_ret[d])
                        ys.append(tgt_ret[d2])
            if len(xs) < 10:
                continue
            r, p = _pearson(xs, ys)
            is_active = 1 if abs(r) > 0.10 and p < 0.1 else 0
            drift = abs(r - baseline_r)
            window_results.append((ws + '_' + we, r, p, is_active, drift))

            db.execute("""
                INSERT OR REPLACE INTO causal_stability
                (chain_id, period, strength_in_period, p_value, is_active, drift_from_baseline)
                VALUES (?,?,?,?,?,?)
            """, (chain['chain_id'], ws + '_' + we, r, p, is_active, drift))

        if not window_results:
            continue

        stability = sum(w[3] for w in window_results) / len(window_results)
        avg_drift = _mean([w[4] for w in window_results])

        # Causal half-life: find lag where |strength| decays to half
        strengths = [abs(w[1]) for w in window_results]
        half = abs(baseline_r) / 2 if baseline_r else 0
        half_life = None
        for i, s in enumerate(strengths):
            if s <= half:
                half_life = i * 30.0  # approximate in days
                break
        if half_life is None:
            half_life = len(window_results) * 30.0

        regime_stable = 1 if stability >= 0.6 else 0
        db.execute("""
            UPDATE causal_chains
            SET regime_stable=?, causal_half_life_days=?, last_validated=?
            WHERE chain_id=?
        """, (regime_stable, half_life, datetime.utcnow().strftime('%Y-%m-%d'),
              chain['chain_id']))

        stabilities.append(stability)
        if stability < 0.4:
            unstable.append({'chain_id': chain['chain_id'],
                             'source': src, 'target': tgt,
                             'stability': round(stability, 3)})
        n_tested += 1

    db.commit()
    db.close()

    avg_stability = round(_mean(stabilities), 3) if stabilities else 0.0
    return {
        'n_chains_tested': n_tested,
        'avg_stability': avg_stability,
        'unstable_chains': unstable[:20]
    }


# ---------------------------------------------------------------------------
# Command: regime_causality
# ---------------------------------------------------------------------------

def cmd_regime_causality(params):
    regime_filter = params.get('regime', 'BULL')

    db = get_db()
    regime_history = _load_regime_history(db)
    sector_returns = _load_sector_returns(db)

    # Dates in the specified regime
    regime_dates = {d for d, r in regime_history.items() if r == regime_filter}

    if not regime_dates:
        db.close()
        return {
            'regime': regime_filter,
            'error': 'No dates found for this regime',
            'n_links_in_regime': 0
        }

    sectors = sorted(sector_returns.keys())
    overall_chains = db.execute(
        "SELECT * FROM causal_chains WHERE chain_type='TRANSFER_ENTROPY'"
    ).fetchall()
    overall_ids = {(r['source_entity'], r['target_entity']) for r in overall_chains}

    regime_links = []
    discovery_date = datetime.utcnow().strftime('%Y-%m-%d')

    for src in sectors:
        for tgt in sectors:
            if src == tgt:
                continue
            src_ret = {d: v for d, v in sector_returns[src].items() if d in regime_dates}
            tgt_ret = {d: v for d, v in sector_returns[tgt].items() if d in regime_dates}
            dates = sorted(set(src_ret.keys()) & set(tgt_ret.keys()))
            if len(dates) < 15:
                continue

            best_r, best_lag, best_p = 0.0, 1, 1.0
            for lag in range(1, 6):
                xs, ys = [], []
                for i, d in enumerate(dates):
                    j = i + lag
                    if j < len(dates):
                        d2 = dates[j]
                        if d2 in tgt_ret:
                            xs.append(src_ret[d])
                            ys.append(tgt_ret[d2])
                if len(xs) < 8:
                    continue
                r, p = _pearson(xs, ys)
                if abs(r) > abs(best_r):
                    best_r, best_lag, best_p = r, lag, p

            if abs(best_r) > 0.15 and best_p < 0.1:
                cid = _chain_id(src, tgt, best_lag, f'REGIME_{regime_filter}')
                db.execute("""
                    INSERT OR REPLACE INTO causal_chains
                    (chain_id, source_entity, target_entity, chain_type,
                     lag_days, strength, p_value, confidence,
                     best_regime, discovery_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (cid, src, tgt, f'REGIME_{regime_filter}',
                      best_lag, best_r, best_p,
                      min(1.0, abs(best_r)), regime_filter, discovery_date))
                in_overall = (src, tgt) in overall_ids
                regime_links.append({
                    'source': src, 'target': tgt,
                    'lag': best_lag,
                    'strength': round(best_r, 4),
                    'regime_specific': not in_overall
                })

    db.commit()
    db.close()

    regime_specific = [l for l in regime_links if l['regime_specific']]
    vs_overall = len(regime_links) - len(overall_chains)

    return {
        'regime': regime_filter,
        'n_regime_dates': len(regime_dates),
        'n_links_in_regime': len(regime_links),
        'vs_overall_change': vs_overall,
        'regime_specific_links': regime_specific[:20]
    }


# ---------------------------------------------------------------------------
# Command: macro_transmission
# ---------------------------------------------------------------------------

def cmd_macro_transmission(params):
    db = get_db()

    # Try to load global_macro table
    try:
        macro_rows = db.execute("SELECT * FROM global_macro LIMIT 1000").fetchall()
    except Exception:
        macro_rows = []

    sector_returns = _load_sector_returns(db)
    sectors = sorted(sector_returns.keys())
    discovery_date = datetime.utcnow().strftime('%Y-%m-%d')

    macro_sector_links = []
    sector_stock_links = []
    transmission_chains = []

    if macro_rows:
        cols = macro_rows[0].keys() if macro_rows else []
        date_col = next((c for c in cols if 'date' in c.lower()), None)
        value_cols = [c for c in cols if c != date_col and 'id' not in c.lower()]

        if date_col:
            for vc in value_cols[:5]:  # limit to first 5 macro indicators
                macro_dict = {}
                prev_val = None
                for row in macro_rows:
                    dt = str(row[date_col])[:10]
                    val = row[vc]
                    if val is not None and prev_val is not None and prev_val != 0:
                        try:
                            macro_dict[dt] = (float(val) - float(prev_val)) / abs(float(prev_val))
                        except (TypeError, ValueError):
                            pass
                    try:
                        prev_val = float(val) if val is not None else prev_val
                    except (TypeError, ValueError):
                        pass

                if len(macro_dict) < 10:
                    continue

                for sec in sectors:
                    sec_ret = sector_returns[sec]
                    common = sorted(set(macro_dict.keys()) & set(sec_ret.keys()))
                    if len(common) < 10:
                        continue
                    for lag in range(0, 6):
                        xs = [macro_dict[d] for d in common[:len(common) - lag]] if lag else [macro_dict[d] for d in common]
                        ys = [sec_ret[common[i + lag]] for i in range(len(common) - lag)] if lag else [sec_ret[d] for d in common]
                        if len(xs) < 8:
                            continue
                        r, p = _pearson(xs, ys)
                        if abs(r) > 0.2 and p < 0.05:
                            macro_sector_links.append({
                                'macro_indicator': vc,
                                'sector': sec,
                                'lag': lag,
                                'strength': round(r, 4)
                            })
                            # Build transmission chain
                            chain = f"{vc} → {sec} (lag {lag}d)"
                            transmission_chains.append(chain)
                            break  # best lag found

    # Sector → top stocks (simplified: find sector-dominating stocks)
    sym_returns = _load_symbol_returns(db)
    sector_map = _load_sector_map(db)

    for sec in sectors[:5]:
        sec_ret = sector_returns[sec]
        sec_symbols = [s for s, sc in sector_map.items() if sc == sec][:10]
        for sym in sec_symbols:
            if sym not in sym_returns:
                continue
            sym_ret = sym_returns[sym]
            common = sorted(set(sec_ret.keys()) & set(sym_ret.keys()))
            if len(common) < 20:
                continue
            xs = [sec_ret[d] for d in common[:-1]]
            ys = [sym_ret[d] for d in common[1:]]
            if len(xs) < 10:
                continue
            r, p = _pearson(xs, ys)
            if abs(r) > 0.25 and p < 0.05:
                sector_stock_links.append({
                    'sector': sec,
                    'stock': sym,
                    'lag': 1,
                    'strength': round(r, 4)
                })

    db.close()

    return {
        'macro_sector_links': macro_sector_links[:20],
        'sector_stock_links': sector_stock_links[:20],
        'transmission_chains': transmission_chains[:30],
        'n_macro_rows_found': len(macro_rows)
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def cmd_build_full(params):
    t0 = time.time()

    te_result = cmd_transfer_entropy({'tau_max': 5, 'n_sectors': 10})
    li_result = cmd_lagged_inference({'min_lag': 1, 'max_lag': 10})
    cs_result = cmd_causal_stability({})

    elapsed = round(time.time() - t0, 2)

    return {
        'transfer_entropy': {
            'n_links_found': te_result.get('n_links_found', 0),
            'n_sectors': te_result.get('n_sectors', 0),
        },
        'lagged_inference': {
            'n_validated': li_result.get('n_validated', 0),
            'n_sector_pairs': len(li_result.get('sector_lead_lags', {})),
        },
        'causal_stability': {
            'n_chains_tested': cs_result.get('n_chains_tested', 0),
            'avg_stability': cs_result.get('avg_stability', 0),
            'n_unstable': len(cs_result.get('unstable_chains', [])),
        },
        'total_execution_time': elapsed,
        'status': 'complete'
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'transfer_entropy': cmd_transfer_entropy,
    'lagged_inference': cmd_lagged_inference,
    'causal_stability': cmd_causal_stability,
    'regime_causality': cmd_regime_causality,
    'macro_transmission': cmd_macro_transmission,
    'build_full': cmd_build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: causal_discovery_engine.py <command> [json_params]'}))
        sys.exit(1)

    command = sys.argv[1]
    raw_params = sys.argv[2] if len(sys.argv) > 2 else '{}'
    try:
        params = json.loads(raw_params)
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
    except Exception as e:
        import traceback
        print(json.dumps({
            'error': str(e),
            'traceback': traceback.format_exc()
        }))
        sys.exit(1)

    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
