"""
Phase 49 — Deep History Engine
Analyzes weekly/monthly OHLCV data for long-horizon regime context,
historical pattern matching, and cycle analysis.
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _tables_exist(conn):
    """Check whether ohlcv_weekly and ohlcv_monthly exist."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ohlcv_weekly','ohlcv_monthly')"
    ).fetchall()
    names = {r['name'] for r in rows}
    missing = {'ohlcv_weekly', 'ohlcv_monthly'} - names
    if missing:
        return False, sorted(missing)
    return True, []


def _weekly_not_ready():
    return {
        'error': 'weekly_data_not_fetched',
        'hint': 'Run: npm run egx:fetch:deep to populate weekly/monthly data'
    }


# ---------------------------------------------------------------------------
# history_coverage
# ---------------------------------------------------------------------------

def history_coverage(params):
    conn = get_db()
    ok, missing = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    def _fetch(table):
        row = conn.execute(f"""
            SELECT COUNT(DISTINCT symbol) AS symbols,
                   COUNT(*) AS total_bars,
                   MIN(date(bar_time,'unixepoch')) AS oldest,
                   MAX(date(bar_time,'unixepoch')) AS newest
            FROM {table}
        """).fetchone()
        return dict(row) if row else {}

    weekly_stats  = _fetch('ohlcv_weekly')
    monthly_stats = _fetch('ohlcv_monthly')

    daily_stats = {}
    try:
        row = conn.execute("""
            SELECT COUNT(DISTINCT symbol) AS symbols,
                   COUNT(*) AS total_bars,
                   MIN(date(bar_time,'unixepoch')) AS oldest,
                   MAX(date(bar_time,'unixepoch')) AS newest
            FROM ohlcv_history_execution
        """).fetchone()
        daily_stats = dict(row) if row else {}
    except Exception:
        pass

    # Symbols that have more weekly history than daily history
    extended = 0
    try:
        rows = conn.execute("""
            SELECT w.symbol,
                   COUNT(w.bar_time) AS w_bars,
                   COALESCE(d.d_bars, 0) AS d_bars
            FROM ohlcv_weekly w
            LEFT JOIN (
                SELECT symbol, COUNT(*) AS d_bars FROM ohlcv_history_execution GROUP BY symbol
            ) d ON w.symbol = d.symbol
            GROUP BY w.symbol
        """).fetchall()
        extended = sum(1 for r in rows if r['w_bars'] > r['d_bars'])
    except Exception:
        pass

    return {
        'weekly':  weekly_stats,
        'monthly': monthly_stats,
        'daily_only': daily_stats,
        'symbols_with_deeper_weekly_than_daily': extended,
        'data_ready': bool(weekly_stats.get('total_bars', 0) > 0)
    }


# ---------------------------------------------------------------------------
# long_term_regime
# ---------------------------------------------------------------------------

def long_term_regime(params):
    symbol = params.get('symbol')
    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    def _fetch_weekly(sym):
        return conn.execute("""
            SELECT bar_time, close FROM ohlcv_weekly
            WHERE symbol = ?
            ORDER BY bar_time ASC
        """, (sym,)).fetchall()

    def _analyse(rows, source):
        closes = [r['close'] for r in rows]
        if len(closes) < 26:
            return {'error': 'insufficient_data', 'bars': len(closes), 'source': source}

        ma13 = statistics.mean(closes[-13:])
        ma26 = statistics.mean(closes[-26:])
        current = closes[-1]
        all_mean = statistics.mean(closes)
        all_std  = statistics.stdev(closes) if len(closes) > 1 else 1.0

        z_score = (current - all_mean) / all_std if all_std else 0.0

        if ma13 > ma26 * 1.02:
            regime = 'BULL'
        elif ma13 < ma26 * 0.98:
            regime = 'BEAR'
        else:
            regime = 'SIDEWAYS'

        # Strength 0-100: based on distance between MAs relative to price
        ma_spread = abs(ma13 - ma26) / ma26 if ma26 else 0.0
        strength = min(100.0, round(ma_spread * 500, 1))

        # Deviation from long-term mean (in %)
        deviation_pct = round((current - all_mean) / all_mean * 100, 2) if all_mean else 0.0

        return {
            'symbol': symbol or 'MARKET',
            'regime': regime,
            'strength_score': strength,
            'ma13w': round(ma13, 4),
            'ma26w': round(ma26, 4),
            'current_close': round(current, 4),
            'long_term_mean': round(all_mean, 4),
            'deviation_from_mean_pct': deviation_pct,
            'z_score': round(z_score, 3),
            'bars_analysed': len(closes),
            'source': source
        }

    if symbol:
        rows = _fetch_weekly(symbol)
        if len(rows) >= 26:
            return _analyse(rows, 'weekly')
        # Fallback to daily
        try:
            rows = conn.execute("""
                SELECT bar_time, close FROM ohlcv_history_execution
                WHERE symbol = ?
                ORDER BY bar_time ASC
            """, (symbol,)).fetchall()
            return _analyse(rows, 'daily_fallback')
        except Exception as e:
            return {'error': str(e), 'symbol': symbol}
    else:
        # Market-wide: average weekly close across all symbols
        rows = conn.execute("""
            SELECT bar_time, AVG(close) AS close
            FROM ohlcv_weekly
            GROUP BY bar_time
            ORDER BY bar_time ASC
        """).fetchall()
        return _analyse(rows, 'weekly_market_avg')


# ---------------------------------------------------------------------------
# historical_volatility_profile
# ---------------------------------------------------------------------------

def historical_volatility_profile(params):
    symbol = params.get('symbol')
    if not symbol:
        return {'error': 'symbol required'}

    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    rows = conn.execute("""
        SELECT bar_time, close FROM ohlcv_weekly
        WHERE symbol = ?
        ORDER BY bar_time ASC
    """, (symbol,)).fetchall()

    source = 'weekly'
    if len(rows) < 13:
        try:
            rows = conn.execute("""
                SELECT bar_time, close FROM ohlcv_history_execution
                WHERE symbol = ?
                ORDER BY bar_time ASC
            """, (symbol,)).fetchall()
            source = 'daily_fallback'
        except Exception as e:
            return {'error': str(e)}

    closes = [r['close'] for r in rows]
    if len(closes) < 13:
        return {'error': 'insufficient_data', 'bars': len(closes)}

    def log_returns(series):
        return [math.log(series[i] / series[i-1])
                for i in range(1, len(series))
                if series[i-1] > 0 and series[i] > 0]

    ann_factor = math.sqrt(52) if source == 'weekly' else math.sqrt(252)

    def annvol(rets):
        if len(rets) < 2:
            return None
        return statistics.stdev(rets) * ann_factor

    all_rets = log_returns(closes)
    vol13  = annvol(all_rets[-12:])  if len(all_rets) >= 12 else None
    vol26  = annvol(all_rets[-25:])  if len(all_rets) >= 25 else None
    vol52  = annvol(all_rets[-51:])  if len(all_rets) >= 51 else None
    current_vol = vol13

    # Percentile rank of current vol vs all 13-period rolling vols
    rolling_vols = []
    window = 12
    for i in range(window, len(all_rets) + 1):
        v = annvol(all_rets[i-window:i])
        if v is not None:
            rolling_vols.append(v)

    pct_rank = None
    if rolling_vols and current_vol is not None:
        below = sum(1 for v in rolling_vols if v <= current_vol)
        pct_rank = round(below / len(rolling_vols) * 100, 1)

    # Vol regime
    if pct_rank is None:
        vol_regime = 'UNKNOWN'
    elif pct_rank >= 90:
        vol_regime = 'EXTREME'
    elif pct_rank >= 70:
        vol_regime = 'HIGH'
    elif pct_rank >= 30:
        vol_regime = 'NORMAL'
    else:
        vol_regime = 'LOW'

    return {
        'symbol': symbol,
        'source': source,
        'vol_13w': round(vol13, 4) if vol13 is not None else None,
        'vol_26w': round(vol26, 4) if vol26 is not None else None,
        'vol_52w': round(vol52, 4) if vol52 is not None else None,
        'current_vol_annualized': round(current_vol, 4) if current_vol is not None else None,
        'percentile_rank': pct_rank,
        'vol_regime': vol_regime,
        'bars_available': len(closes)
    }


# ---------------------------------------------------------------------------
# decade_pattern_match
# ---------------------------------------------------------------------------

def decade_pattern_match(params):
    symbol = params.get('symbol')
    if not symbol:
        return {'error': 'symbol required'}
    lookback_weeks = int(params.get('lookback_weeks', 520))

    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    rows = conn.execute("""
        SELECT bar_time, close FROM ohlcv_weekly
        WHERE symbol = ?
        ORDER BY bar_time ASC
        LIMIT ?
    """, (symbol, lookback_weeks)).fetchall()

    if len(rows) < 16:
        return {'error': 'insufficient_weekly_data', 'bars': len(rows)}

    closes     = [r['close'] for r in rows]
    timestamps = [r['bar_time'] for r in rows]
    pattern_len = 12
    fwd_len     = 4

    # Current (last 12 bars)
    current_pattern = closes[-pattern_len:]

    def normalize(seq):
        mn = min(seq)
        mx = max(seq)
        rng = mx - mn
        if rng == 0:
            return [0.0] * len(seq)
        return [(v - mn) / rng for v in seq]

    def cosine_sim(a, b):
        dot  = sum(x * y for x, y in zip(a, b))
        na   = math.sqrt(sum(x**2 for x in a))
        nb   = math.sqrt(sum(x**2 for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    current_norm = normalize(current_pattern)

    matches = []
    # Slide through history (exclude last pattern_len bars)
    for i in range(len(closes) - pattern_len - fwd_len):
        window = closes[i: i + pattern_len]
        win_norm = normalize(window)
        sim = cosine_sim(current_norm, win_norm)
        start_ts = timestamps[i]
        end_ts   = timestamps[i + pattern_len - 1]
        # Future return after pattern
        fwd_close_start = closes[i + pattern_len - 1]
        fwd_close_end   = closes[i + pattern_len + fwd_len - 1]
        fwd_return = (fwd_close_end - fwd_close_start) / fwd_close_start if fwd_close_start else 0.0
        matches.append({
            'similarity': round(sim, 4),
            'start_date': datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d'),
            'end_date':   datetime.fromtimestamp(end_ts,   tz=timezone.utc).strftime('%Y-%m-%d'),
            'next_4w_return_pct': round(fwd_return * 100, 2)
        })

    matches.sort(key=lambda x: x['similarity'], reverse=True)
    top3 = matches[:3]

    avg_fwd = round(statistics.mean(m['next_4w_return_pct'] for m in top3), 2) if top3 else None

    return {
        'symbol': symbol,
        'pattern_weeks': pattern_len,
        'forecast_weeks': fwd_len,
        'total_windows_scanned': len(matches),
        'top_matches': top3,
        'avg_next_4w_return_pct': avg_fwd
    }


# ---------------------------------------------------------------------------
# cycle_analysis
# ---------------------------------------------------------------------------

def cycle_analysis(params):
    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    rows = conn.execute("""
        SELECT bar_time, AVG(close) AS avg_close
        FROM ohlcv_weekly
        GROUP BY bar_time
        ORDER BY bar_time ASC
    """).fetchall()

    if len(rows) < 26:
        return {'error': 'insufficient_weekly_market_data', 'bars': len(rows)}

    closes     = [r['avg_close'] for r in rows]
    timestamps = [r['bar_time'] for r in rows]

    # Weekly returns
    rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes)) if closes[i-1] > 0]

    avg_weekly_return = round(statistics.mean(rets) * 100, 4) if rets else 0.0

    # Identify bull/bear phases: consecutive up/down weeks
    phases = []
    if rets:
        current_dir  = 'bull' if rets[0] >= 0 else 'bear'
        current_len  = 1
        for r in rets[1:]:
            d = 'bull' if r >= 0 else 'bear'
            if d == current_dir:
                current_len += 1
            else:
                phases.append({'direction': current_dir, 'length_weeks': current_len})
                current_dir  = d
                current_len  = 1
        phases.append({'direction': current_dir, 'length_weeks': current_len})

    bull_lengths = [p['length_weeks'] for p in phases if p['direction'] == 'bull']
    bear_lengths = [p['length_weeks'] for p in phases if p['direction'] == 'bear']

    def _stats(lst):
        if not lst:
            return {'mean': None, 'median': None, 'max': None}
        return {
            'mean':   round(statistics.mean(lst), 1),
            'median': round(statistics.median(lst), 1),
            'max':    max(lst)
        }

    current_phase = phases[-1] if phases else {}
    current_dir   = current_phase.get('direction', 'unknown')
    current_age   = current_phase.get('length_weeks', 0)

    # Cycle phase estimation
    if current_dir == 'bull':
        avg_bull = statistics.mean(bull_lengths) if bull_lengths else 26
        frac = current_age / avg_bull if avg_bull else 0
        if frac < 0.33:
            phase_label = 'EARLY_BULL'
        elif frac < 0.67:
            phase_label = 'MID_BULL'
        else:
            phase_label = 'LATE_BULL'
    else:
        avg_bear = statistics.mean(bear_lengths) if bear_lengths else 13
        frac = current_age / avg_bear if avg_bear else 0
        if frac < 0.33:
            phase_label = 'EARLY_BEAR'
        elif frac < 0.67:
            phase_label = 'MID_BEAR'
        else:
            phase_label = 'LATE_BEAR'

    return {
        'avg_weekly_return_pct': avg_weekly_return,
        'total_weeks_analysed': len(rows),
        'total_phases': len(phases),
        'bull_phases': _stats(bull_lengths),
        'bear_phases': _stats(bear_lengths),
        'current_direction': current_dir,
        'current_cycle_age_weeks': current_age,
        'cycle_phase': phase_label,
        'last_bar_date': datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).strftime('%Y-%m-%d') if timestamps else None
    }


# ---------------------------------------------------------------------------
# sector_long_term
# ---------------------------------------------------------------------------

def sector_long_term(params):
    sector = params.get('sector')
    if not sector:
        return {'error': 'sector required'}

    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    # Get symbols for sector
    try:
        sym_rows = conn.execute("""
            SELECT DISTINCT symbol FROM symbols WHERE sector = ?
        """, (sector,)).fetchall()
        symbols = [r['symbol'] for r in sym_rows]
    except Exception:
        symbols = []

    if not symbols:
        return {'error': 'no_symbols_for_sector', 'sector': sector}

    results = []
    for sym in symbols:
        rows = conn.execute("""
            SELECT bar_time, close FROM ohlcv_weekly
            WHERE symbol = ?
            ORDER BY bar_time ASC
        """, (sym,)).fetchall()
        if len(rows) < 14:
            continue
        closes = [r['close'] for r in rows]
        ret_13w = (closes[-1] - closes[-14]) / closes[-14] * 100 if closes[-14] > 0 else None
        results.append({'symbol': sym, 'return_13w_pct': round(ret_13w, 2) if ret_13w is not None else None})

    if not results:
        return {'error': 'insufficient_weekly_data_for_sector', 'sector': sector}

    valid_rets = [r['return_13w_pct'] for r in results if r['return_13w_pct'] is not None]
    sector_avg_13w = round(statistics.mean(valid_rets), 2) if valid_rets else None

    # Market average 13w
    market_rows = conn.execute("""
        SELECT bar_time, AVG(close) AS avg_close
        FROM ohlcv_weekly
        GROUP BY bar_time
        ORDER BY bar_time DESC
        LIMIT 14
    """).fetchall()
    market_rets = list(reversed([r['avg_close'] for r in market_rows]))
    mkt_13w = (market_rets[-1] - market_rets[0]) / market_rets[0] * 100 if len(market_rets) >= 2 and market_rets[0] > 0 else None

    alpha = round(sector_avg_13w - mkt_13w, 2) if (sector_avg_13w is not None and mkt_13w is not None) else None

    # Sector cycle position: above/below its own 26w mean
    sector_closes = []
    for sym in symbols:
        rows = conn.execute("""
            SELECT close FROM ohlcv_weekly WHERE symbol = ? ORDER BY bar_time DESC LIMIT 26
        """, (sym,)).fetchall()
        if rows:
            sector_closes.append(statistics.mean([r['close'] for r in rows]))

    return {
        'sector': sector,
        'n_symbols': len(results),
        'sector_avg_13w_return_pct': sector_avg_13w,
        'market_avg_13w_return_pct': round(mkt_13w, 2) if mkt_13w is not None else None,
        'weekly_alpha_pct': alpha,
        'symbol_breakdown': results
    }


# ---------------------------------------------------------------------------
# build_full
# ---------------------------------------------------------------------------

def build_full(params):
    conn = get_db()
    ok, _ = _tables_exist(conn)
    if not ok:
        return _weekly_not_ready()

    # Create snapshot table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deep_history_snapshot (
            generated_at       TEXT,
            regime             TEXT,
            cycle_phase        TEXT,
            cycle_age_weeks    INTEGER,
            avg_volatility     REAL,
            n_symbols_weekly   INTEGER,
            n_symbols_monthly  INTEGER,
            regime_strength    REAL,
            summary            TEXT
        )
    """)
    conn.commit()

    regime_data  = long_term_regime({})
    cycle_data   = cycle_analysis({})
    coverage     = history_coverage({})

    # Collect volatility across all weekly symbols
    sym_rows = conn.execute("SELECT DISTINCT symbol FROM ohlcv_weekly").fetchall()
    symbols  = [r['symbol'] for r in sym_rows]
    vols = []
    for sym in symbols[:50]:  # cap to avoid long runtime
        vp = historical_volatility_profile({'symbol': sym})
        v = vp.get('current_vol_annualized')
        if v is not None:
            vols.append(v)
    avg_vol = round(statistics.mean(vols), 4) if vols else None

    regime        = regime_data.get('regime', 'UNKNOWN')
    regime_str    = regime_data.get('strength_score', 0.0)
    cycle_phase   = cycle_data.get('cycle_phase', 'UNKNOWN')
    cycle_age     = cycle_data.get('current_cycle_age_weeks', 0)
    n_wk          = coverage.get('weekly',  {}).get('symbols', 0)
    n_mo          = coverage.get('monthly', {}).get('symbols', 0)
    generated_at  = datetime.now(tz=timezone.utc).isoformat()

    summary = (
        f"Regime={regime}(strength={regime_str}), "
        f"Cycle={cycle_phase}(age={cycle_age}w), "
        f"AvgVol={avg_vol}, "
        f"WeeklySymbols={n_wk}, MonthlySymbols={n_mo}"
    )

    conn.execute("""
        INSERT INTO deep_history_snapshot
        (generated_at, regime, cycle_phase, cycle_age_weeks,
         avg_volatility, n_symbols_weekly, n_symbols_monthly,
         regime_strength, summary)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (generated_at, regime, cycle_phase, cycle_age,
          avg_vol, n_wk, n_mo, regime_str, summary))
    conn.commit()

    return {
        'generated_at':     generated_at,
        'regime':           regime,
        'regime_strength':  regime_str,
        'cycle_phase':      cycle_phase,
        'cycle_age_weeks':  cycle_age,
        'avg_volatility':   avg_vol,
        'n_symbols_weekly': n_wk,
        'n_symbols_monthly': n_mo,
        'summary':          summary,
        'saved_to_db':      True
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'history_coverage':              history_coverage,
    'long_term_regime':              long_term_regime,
    'historical_volatility_profile': historical_volatility_profile,
    'decade_pattern_match':          decade_pattern_match,
    'cycle_analysis':                cycle_analysis,
    'sector_long_term':              sector_long_term,
    'build_full':                    build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'Usage: deep_history_engine.py <command> <json_params>',
            'available': list(COMMANDS.keys())
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    params = json.loads(sys.argv[2])

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        result = {'error': str(e), 'command': cmd}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
