#!/usr/bin/env python3
"""
EGX Cross-Market Coupling Engine  (Phase 51)
=============================================
EGX does NOT live in a vacuum. USD/EGP alone explains ~40% of EGX volatility.
This engine reads the cross_market_daily table and produces actionable regime
context: is the global environment risk-on or risk-off? What is the macro
tailwind/headwind for Egyptian equities?

Commands:
  market_coverage   — check what cross-market data we have
  risk_on_score     — global Risk-On/Risk-Off composite score (0-100)
  usdegp_regime     — USD/EGP currency regime analysis
  coupling_matrix   — correlation matrix between EGX and cross-market assets
  macro_regime      — comprehensive macro regime from all signals
  daily_context     — daily cross-market context for session bias
  build_full        — full build + save to cross_market_regime table
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ── Cross-Market Asset Registry ──────────────────────────────────────────────

CROSS_MARKET_ASSETS = {
    # FX
    'USDEGP':  {'category': 'FX',         'impact': 'DIRECT',   'direction': 'INVERSE',  'weight': 0.35, 'arabic': 'دولار/جنيه'},
    'EURUSD':  {'category': 'FX',         'impact': 'INDIRECT', 'direction': 'MIXED',    'weight': 0.05, 'arabic': 'يورو/دولار'},
    # Commodities
    'XAUUSD':  {'category': 'COMMODITY',  'impact': 'DIRECT',   'direction': 'POSITIVE', 'weight': 0.15, 'arabic': 'ذهب'},
    'UKOIL':   {'category': 'COMMODITY',  'impact': 'DIRECT',   'direction': 'POSITIVE', 'weight': 0.10, 'arabic': 'نفط'},
    # Global Indices
    'SPY':     {'category': 'INDEX',      'impact': 'INDIRECT', 'direction': 'POSITIVE', 'weight': 0.08, 'arabic': 'S&P500'},
    'EEM':     {'category': 'INDEX',      'impact': 'DIRECT',   'direction': 'POSITIVE', 'weight': 0.12, 'arabic': 'أسواق ناشئة'},
    'VIX':     {'category': 'VOLATILITY', 'impact': 'DIRECT',   'direction': 'INVERSE',  'weight': 0.10, 'arabic': 'تقلب عالمي'},
    'DXY':     {'category': 'FX_INDEX',   'impact': 'DIRECT',   'direction': 'INVERSE',  'weight': 0.10, 'arabic': 'دولار إندكس'},
    # Bonds
    'US10Y':   {'category': 'BOND',       'impact': 'INDIRECT', 'direction': 'INVERSE',  'weight': 0.05, 'arabic': 'سندات أمريكا'},
    # Regional
    'TADAWUL': {'category': 'REGIONAL',   'impact': 'DIRECT',   'direction': 'POSITIVE', 'weight': 0.05, 'arabic': 'تداول السعودية'},
    'DFMGI':   {'category': 'REGIONAL',   'impact': 'DIRECT',   'direction': 'POSITIVE', 'weight': 0.05, 'arabic': 'سوق دبي'},
}

# ── Commands registry ─────────────────────────────────────────────────────────

COMMANDS = {
    'market_coverage', 'risk_on_score', 'usdegp_regime',
    'coupling_matrix', 'macro_regime', 'daily_context', 'build_full',
}

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def safe(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def pct_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / abs(old)) * 100.0


# ── Schema helpers ────────────────────────────────────────────────────────────

def ensure_regime_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cross_market_regime (
            date TEXT PRIMARY KEY,
            usdegp_regime TEXT,
            gold_regime TEXT,
            em_regime TEXT,
            oil_regime TEXT,
            vix_regime TEXT,
            risk_on_score REAL,
            macro_headwind TEXT,
            generated_at TEXT
        )
    """)
    conn.commit()


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ── Data fetch helpers ────────────────────────────────────────────────────────

NO_DATA_ERROR = {
    'error': 'cross_market_data_not_fetched',
    'hint': 'Run: npm run egx:fetch:cross-market'
}


def check_cross_market_available(conn):
    if not table_exists(conn, 'cross_market_daily'):
        return False
    row = conn.execute("SELECT COUNT(*) AS cnt FROM cross_market_daily").fetchone()
    return row['cnt'] > 0


def fetch_closes(conn, asset, limit=300):
    """Return list of (bar_time, close) tuples ordered oldest→newest."""
    rows = conn.execute(
        """SELECT bar_time, close FROM cross_market_daily
           WHERE asset = ? AND close IS NOT NULL
           ORDER BY bar_time DESC LIMIT ?""",
        (asset, limit)
    ).fetchall()
    result = [(r['bar_time'], safe(r['close'])) for r in rows]
    result.reverse()
    return result


def last_n_closes(conn, asset, n):
    pairs = fetch_closes(conn, asset, limit=n + 5)
    return [c for _, c in pairs[-n:]] if len(pairs) >= 2 else []


def sma(closes, period):
    if len(closes) < period:
        return None
    return statistics.mean(closes[-period:])


def compute_returns(closes):
    if len(closes) < 2:
        return []
    return [pct_change(closes[i - 1], closes[i]) for i in range(1, len(closes))]


def pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


# ── Command: market_coverage ──────────────────────────────────────────────────

def cmd_market_coverage(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    rows = conn.execute("""
        SELECT asset,
               COUNT(*) AS bars,
               MIN(bar_time) AS first_bar,
               MAX(bar_time) AS last_bar,
               MIN(close) AS min_close,
               MAX(close) AS max_close,
               AVG(close) AS avg_close
        FROM cross_market_daily
        GROUP BY asset
        ORDER BY asset
    """).fetchall()

    coverage = {}
    known_assets = set(CROSS_MARKET_ASSETS.keys())
    found_assets = set()

    for r in rows:
        asset = r['asset']
        found_assets.add(asset)
        first_dt = datetime.utcfromtimestamp(r['first_bar']).strftime('%Y-%m-%d') if r['first_bar'] else None
        last_dt  = datetime.utcfromtimestamp(r['last_bar']).strftime('%Y-%m-%d')  if r['last_bar']  else None
        coverage[asset] = {
            'bars':       r['bars'],
            'first_date': first_dt,
            'last_date':  last_dt,
            'min_close':  round(safe(r['min_close']), 4),
            'max_close':  round(safe(r['max_close']), 4),
            'avg_close':  round(safe(r['avg_close']), 4),
            'registered': asset in known_assets,
        }

    missing = sorted(known_assets - found_assets)

    total_bars = sum(v['bars'] for v in coverage.values())

    return {
        'success':          True,
        'total_assets':     len(coverage),
        'total_bars':       total_bars,
        'coverage':         coverage,
        'missing_assets':   missing,
        'registered_found': sorted(known_assets & found_assets),
        'extra_assets':     sorted(found_assets - known_assets),
    }


# ── Command: risk_on_score ────────────────────────────────────────────────────

def _score_return(ret, weight, direction='POSITIVE', scale=5.0):
    """Map a percent return to a 0–1 contribution using direction and sigmoid-like scaling."""
    if direction == 'POSITIVE':
        raw = ret / scale
    elif direction == 'INVERSE':
        raw = -ret / scale
    else:  # MIXED — treat as positive
        raw = ret / scale
    # Clamp to [-1, 1]
    raw = max(-1.0, min(1.0, raw))
    # Shift to 0–1
    return (raw + 1.0) / 2.0


def cmd_risk_on_score(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    lookback = int(params.get('lookback_days', 5))

    components = {}
    weighted_sum = 0.0
    weight_used = 0.0

    # ── EEM (weight 0.25) ─────────────────────────────────────────────────────
    eem_closes = last_n_closes(conn, 'EEM', lookback + 2)
    if len(eem_closes) >= 2:
        ret = pct_change(eem_closes[0], eem_closes[-1])
        score = _score_return(ret, 0.25, 'POSITIVE', scale=4.0)
        components['EEM'] = {'return_pct': round(ret, 3), 'contribution': round(score * 0.25, 4)}
        weighted_sum += score * 0.25
        weight_used  += 0.25

    # ── VIX (weight 0.20) — level-based, not return-based ────────────────────
    vix_closes = last_n_closes(conn, 'VIX', 3)
    if vix_closes:
        vix_level = vix_closes[-1]
        # VIX < 15 → score 1.0; VIX > 30 → score 0.0; linear interpolation
        if vix_level <= 15:
            vix_score = 1.0
        elif vix_level >= 30:
            vix_score = 0.0
        else:
            vix_score = 1.0 - (vix_level - 15.0) / 15.0
        components['VIX'] = {'level': round(vix_level, 2), 'contribution': round(vix_score * 0.20, 4)}
        weighted_sum += vix_score * 0.20
        weight_used  += 0.20

    # ── SPY (weight 0.15) ─────────────────────────────────────────────────────
    spy_closes = last_n_closes(conn, 'SPY', lookback + 2)
    if len(spy_closes) >= 2:
        ret = pct_change(spy_closes[0], spy_closes[-1])
        score = _score_return(ret, 0.15, 'POSITIVE', scale=3.0)
        components['SPY'] = {'return_pct': round(ret, 3), 'contribution': round(score * 0.15, 4)}
        weighted_sum += score * 0.15
        weight_used  += 0.15

    # ── Gold / XAUUSD (weight 0.10) — positive gold is slight negative for risk ─
    gold_closes = last_n_closes(conn, 'XAUUSD', lookback + 2)
    if len(gold_closes) >= 2:
        ret = pct_change(gold_closes[0], gold_closes[-1])
        # Rising gold = flight to safety = risk-off signal → INVERSE
        score = _score_return(ret, 0.10, 'INVERSE', scale=3.0)
        components['XAUUSD'] = {'return_pct': round(ret, 3), 'contribution': round(score * 0.10, 4)}
        weighted_sum += score * 0.10
        weight_used  += 0.10

    # ── DXY (weight 0.15) — weak dollar benefits EM ───────────────────────────
    dxy_closes = last_n_closes(conn, 'DXY', lookback + 2)
    if len(dxy_closes) >= 2:
        ret = pct_change(dxy_closes[0], dxy_closes[-1])
        score = _score_return(ret, 0.15, 'INVERSE', scale=2.0)
        components['DXY'] = {'return_pct': round(ret, 3), 'contribution': round(score * 0.15, 4)}
        weighted_sum += score * 0.15
        weight_used  += 0.15

    # ── Oil / UKOIL (weight 0.15) — Egypt is net oil exporter ─────────────────
    oil_closes = last_n_closes(conn, 'UKOIL', lookback + 2)
    if len(oil_closes) >= 2:
        ret = pct_change(oil_closes[0], oil_closes[-1])
        score = _score_return(ret, 0.15, 'POSITIVE', scale=5.0)
        components['UKOIL'] = {'return_pct': round(ret, 3), 'contribution': round(score * 0.15, 4)}
        weighted_sum += score * 0.15
        weight_used  += 0.15

    # ── Normalize by weight actually used ─────────────────────────────────────
    if weight_used == 0:
        return {'error': 'insufficient_data', 'hint': 'No cross-market closes available for lookback period'}

    raw_score = (weighted_sum / weight_used) * 100.0
    score_0_100 = round(min(100.0, max(0.0, raw_score)), 1)

    if score_0_100 < 30:
        label = 'RISK_OFF'
        arabic = 'تجنب المخاطرة — بيئة سلبية للأسهم المصرية'
    elif score_0_100 < 50:
        label = 'CAUTIOUS'
        arabic = 'حذر — بيئة غير مؤكدة'
    elif score_0_100 < 70:
        label = 'NEUTRAL'
        arabic = 'محايد — لا إشارة واضحة'
    elif score_0_100 < 85:
        label = 'RISK_ON'
        arabic = 'إقبال على المخاطرة — بيئة إيجابية للأسهم المصرية'
    else:
        label = 'STRONG_RISK_ON'
        arabic = 'إقبال قوي جداً على المخاطرة — بيئة ممتازة'

    return {
        'success':         True,
        'risk_on_score':   score_0_100,
        'label':           label,
        'arabic':          arabic,
        'lookback_days':   lookback,
        'weight_coverage': round(weight_used, 3),
        'components':      components,
    }


# ── Command: usdegp_regime ────────────────────────────────────────────────────

def cmd_usdegp_regime(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    lookback = int(params.get('lookback_days', 20))

    closes_200 = last_n_closes(conn, 'USDEGP', 210)
    if len(closes_200) < 5:
        return {'error': 'insufficient_usdegp_data', 'bars_available': len(closes_200)}

    current = closes_200[-1]
    ma20    = sma(closes_200, min(20, len(closes_200)))
    ma50    = sma(closes_200, min(50, len(closes_200)))
    ma200   = sma(closes_200, min(200, len(closes_200)))

    # Recent trend over lookback window
    window = closes_200[-lookback:] if len(closes_200) >= lookback else closes_200
    start_val = window[0]
    total_change_pct = pct_change(start_val, current)

    # Annualize: assume ~252 trading days/year
    trading_days = len(window)
    if trading_days > 1:
        annualized_rate = ((current / start_val) ** (252.0 / trading_days) - 1.0) * 100.0
    else:
        annualized_rate = 0.0

    # Regime detection
    if total_change_pct > 1.0:
        regime = 'DEPRECIATING'       # USD rising vs EGP → EGP weakening
        regime_arabic = 'استهلاك الجنيه المصري'
    elif total_change_pct < -1.0:
        regime = 'APPRECIATING'       # USD falling vs EGP → EGP strengthening
        regime_arabic = 'تقوية الجنيه المصري'
    else:
        regime = 'STABLE'
        regime_arabic = 'استقرار نسبي للجنيه'

    # EGX Impact assessment
    if regime == 'DEPRECIATING' and abs(total_change_pct) > 5:
        egx_impact = 'HIGH_NEGATIVE'
        impact_arabic = 'تأثير سلبي قوي على البورصة المصرية'
    elif regime == 'DEPRECIATING':
        egx_impact = 'MODERATE_NEGATIVE'
        impact_arabic = 'تأثير سلبي معتدل على البورصة المصرية'
    elif regime == 'APPRECIATING':
        egx_impact = 'POSITIVE'
        impact_arabic = 'تأثير إيجابي على البورصة المصرية'
    else:
        egx_impact = 'NEUTRAL'
        impact_arabic = 'تأثير محايد على البورصة المصرية'

    vs_ma20  = round(pct_change(ma20,  current), 3) if ma20  else None
    vs_ma50  = round(pct_change(ma50,  current), 3) if ma50  else None
    vs_ma200 = round(pct_change(ma200, current), 3) if ma200 else None

    return {
        'success':            True,
        'asset':              'USDEGP',
        'current_level':      round(current, 4),
        'ma20':               round(ma20,  4) if ma20  else None,
        'ma50':               round(ma50,  4) if ma50  else None,
        'ma200':              round(ma200, 4) if ma200 else None,
        'vs_ma20_pct':        vs_ma20,
        'vs_ma50_pct':        vs_ma50,
        'vs_ma200_pct':       vs_ma200,
        'lookback_days':      lookback,
        'change_pct':         round(total_change_pct, 3),
        'annualized_rate_pct':round(annualized_rate, 2),
        'regime':             regime,
        'regime_arabic':      regime_arabic,
        'egx_impact':         egx_impact,
        'impact_arabic':      impact_arabic,
    }


# ── Command: coupling_matrix ──────────────────────────────────────────────────

def _get_egx_proxy_returns(conn, n_bars):
    """
    Proxy EGX30 using average daily returns of the top liquid EGX stocks
    from ohlcv_history_execution (most recent n_bars bars).
    """
    # Get top 20 by bar count (most liquid)
    top_syms = conn.execute("""
        SELECT symbol, COUNT(*) AS cnt FROM ohlcv_history_execution
        GROUP BY symbol ORDER BY cnt DESC LIMIT 20
    """).fetchall()

    if not top_syms:
        return []

    symbols = [r['symbol'] for r in top_syms]
    symbol_placeholders = ','.join('?' * len(symbols))

    rows = conn.execute(f"""
        SELECT symbol, bar_time, close FROM ohlcv_history_execution
        WHERE symbol IN ({symbol_placeholders})
          AND close IS NOT NULL
        ORDER BY symbol, bar_time
    """, symbols).fetchall()

    # Group by date (bar_time)
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['bar_time']].append(safe(r['close']))

    # Sort dates, take last n_bars+1 to compute n_bars returns
    dates = sorted(by_date.keys())
    if len(dates) < 2:
        return []

    dates = dates[-(n_bars + 2):]

    # Average close per date
    avg_closes = [(d, statistics.mean(by_date[d])) for d in dates if by_date[d]]
    if len(avg_closes) < 2:
        return []

    returns = [pct_change(avg_closes[i - 1][1], avg_closes[i][1])
               for i in range(1, len(avg_closes))]
    return returns


def cmd_coupling_matrix(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    lookback = int(params.get('lookback_days', 60))

    egx_rets = _get_egx_proxy_returns(conn, lookback + 5)
    if len(egx_rets) < 10:
        return {'error': 'insufficient_egx_data', 'bars': len(egx_rets),
                'hint': 'Need at least 10 EGX bars for correlation'}

    correlations = {}
    for asset in CROSS_MARKET_ASSETS:
        closes = last_n_closes(conn, asset, lookback + 5)
        if len(closes) < 5:
            correlations[asset] = None
            continue
        asset_rets = compute_returns(closes)
        n = min(len(egx_rets), len(asset_rets))
        if n < 5:
            correlations[asset] = None
            continue
        corr = pearson(egx_rets[-n:], asset_rets[-n:])
        correlations[asset] = round(corr, 4)

    # Filter valid correlations
    valid = {k: v for k, v in correlations.items() if v is not None}
    sorted_corr = sorted(valid.items(), key=lambda x: x[1], reverse=True)

    strongest_positive = [
        {'asset': k, 'correlation': v, 'arabic': CROSS_MARKET_ASSETS[k]['arabic']}
        for k, v in sorted_corr if v > 0.2
    ][:5]

    strongest_negative = [
        {'asset': k, 'correlation': v, 'arabic': CROSS_MARKET_ASSETS[k]['arabic']}
        for k, v in reversed(sorted_corr) if v < -0.2
    ][:5]

    return {
        'success':                    True,
        'lookback_days':              lookback,
        'egx_proxy_bars':             len(egx_rets),
        'correlations':               {k: v for k, v in sorted_corr},
        'strongest_positive_correlations': strongest_positive,
        'strongest_negative_correlations': strongest_negative,
        'note': 'EGX proxy = average return of top 20 liquid stocks in ohlcv_history_execution',
    }


# ── Command: macro_regime ─────────────────────────────────────────────────────

def _vix_label(vix_level):
    if vix_level < 15:
        return 'LOW',      'تقلب منخفض — بيئة مواتية'
    elif vix_level < 20:
        return 'ELEVATED', 'تقلب مرتفع قليلاً'
    elif vix_level < 30:
        return 'HIGH',     'تقلب مرتفع — حذر'
    else:
        return 'EXTREME',  'تقلب شديد — بيئة أزمة'


def _gold_label(ret_5d):
    if ret_5d > 2:
        return 'BULL',     'الذهب في صعود — تدفق للملاذ الآمن'
    elif ret_5d < -2:
        return 'BEAR',     'الذهب في هبوط — انخفاض الملاذ الآمن'
    else:
        return 'SIDEWAYS', 'الذهب عرضي'


def _oil_label(ret_5d):
    if ret_5d > 3:
        return 'HIGH',    'النفط مرتفع — داعم لمصر كمصدر'
    elif ret_5d < -3:
        return 'LOW',     'النفط منخفض — ضغط على الإيرادات'
    else:
        return 'NEUTRAL', 'النفط محايد'


def _em_label(ret_5d):
    if ret_5d > 1.5:
        return 'RISK_ON',  'أسواق ناشئة في صعود — تدفقات إيجابية'
    elif ret_5d < -1.5:
        return 'RISK_OFF', 'أسواق ناشئة في هبوط — تدفقات سلبية'
    else:
        return 'NEUTRAL',  'أسواق ناشئة محايدة'


def cmd_macro_regime(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    # Gather individual signals
    usdegp_result = cmd_usdegp_regime(conn, {'lookback_days': 20})
    risk_result    = cmd_risk_on_score(conn, {'lookback_days': 5})

    signals = {}

    # VIX regime
    vix_closes = last_n_closes(conn, 'VIX', 3)
    vix_level = vix_closes[-1] if vix_closes else None
    vix_regime_label, vix_arabic = _vix_label(vix_level) if vix_level else ('UNKNOWN', 'بيانات VIX غير متاحة')
    signals['VIX'] = {'level': round(vix_level, 2) if vix_level else None,
                      'regime': vix_regime_label, 'arabic': vix_arabic}

    # Gold regime
    gold_closes = last_n_closes(conn, 'XAUUSD', 7)
    gold_ret = pct_change(gold_closes[0], gold_closes[-1]) if len(gold_closes) >= 2 else 0.0
    gold_label, gold_arabic = _gold_label(gold_ret)
    signals['XAUUSD'] = {'return_5d_pct': round(gold_ret, 3), 'regime': gold_label, 'arabic': gold_arabic}

    # EEM regime
    eem_closes = last_n_closes(conn, 'EEM', 7)
    eem_ret = pct_change(eem_closes[0], eem_closes[-1]) if len(eem_closes) >= 2 else 0.0
    em_label, em_arabic = _em_label(eem_ret)
    signals['EEM'] = {'return_5d_pct': round(eem_ret, 3), 'regime': em_label, 'arabic': em_arabic}

    # Oil regime
    oil_closes = last_n_closes(conn, 'UKOIL', 7)
    oil_ret = pct_change(oil_closes[0], oil_closes[-1]) if len(oil_closes) >= 2 else 0.0
    oil_label, oil_arabic = _oil_label(oil_ret)
    signals['UKOIL'] = {'return_5d_pct': round(oil_ret, 3), 'regime': oil_label, 'arabic': oil_arabic}

    # USD/EGP
    if usdegp_result.get('success'):
        signals['USDEGP'] = {
            'regime':         usdegp_result['regime'],
            'change_pct':     usdegp_result['change_pct'],
            'egx_impact':     usdegp_result['egx_impact'],
            'arabic':         usdegp_result['regime_arabic'],
        }
    else:
        signals['USDEGP'] = {'regime': 'UNKNOWN', 'arabic': 'بيانات USD/EGP غير متاحة'}

    # ── Synthesize overall macro regime ───────────────────────────────────────
    risk_score = risk_result.get('risk_on_score', 50) if risk_result.get('success') else 50
    usdegp_impact = usdegp_result.get('egx_impact', 'NEUTRAL') if usdegp_result.get('success') else 'NEUTRAL'

    bearish_flags = 0
    if vix_regime_label in ('HIGH', 'EXTREME'):                         bearish_flags += 2
    if vix_regime_label == 'ELEVATED':                                  bearish_flags += 1
    if em_label == 'RISK_OFF':                                          bearish_flags += 2
    if gold_label == 'BULL':                                            bearish_flags += 1
    if usdegp_impact in ('HIGH_NEGATIVE', 'MODERATE_NEGATIVE'):        bearish_flags += 2 if 'HIGH' in usdegp_impact else 1
    if risk_score < 30:                                                  bearish_flags += 2
    elif risk_score < 45:                                                bearish_flags += 1

    bullish_flags = 0
    if vix_regime_label == 'LOW':                                       bullish_flags += 2
    if em_label == 'RISK_ON':                                           bullish_flags += 2
    if gold_label != 'BULL':                                            bullish_flags += 1
    if usdegp_impact == 'POSITIVE':                                     bullish_flags += 2
    if risk_score > 70:                                                  bullish_flags += 2
    elif risk_score > 55:                                                bullish_flags += 1

    net = bullish_flags - bearish_flags

    if net >= 4:
        overall_regime = 'MACRO_BULL'
        macro_arabic = 'بيئة ماكرو إيجابية — داعمة للأسهم المصرية'
        macro_headwind = 'STRONG_TAILWIND'
    elif net >= 1:
        overall_regime = 'MACRO_NEUTRAL'
        macro_arabic = 'بيئة ماكرو محايدة'
        macro_headwind = 'TAILWIND'
    elif net >= -2:
        overall_regime = 'MACRO_NEUTRAL'
        macro_arabic = 'بيئة ماكرو محايدة مع بعض الضغوط'
        macro_headwind = 'NEUTRAL'
    elif net >= -5:
        overall_regime = 'MACRO_BEAR'
        macro_arabic = 'بيئة ماكرو سلبية — رياح معاكسة للأسهم'
        macro_headwind = 'HEADWIND'
    else:
        overall_regime = 'MACRO_CRISIS'
        macro_arabic = 'بيئة أزمة ماكرو — خطر مرتفع جداً'
        macro_headwind = 'STRONG_HEADWIND'

    # Key risks
    risks = []
    if usdegp_impact in ('HIGH_NEGATIVE', 'MODERATE_NEGATIVE'):
        risks.append('EGP depreciation pressure reducing foreign investor appetite')
    if vix_regime_label in ('HIGH', 'EXTREME'):
        risks.append(f'Elevated global volatility (VIX={round(vix_level, 1) if vix_level else "?"})')
    if em_label == 'RISK_OFF':
        risks.append('EM sell-off reducing capital flows to EGX')
    if oil_label == 'LOW':
        risks.append('Low oil hurting Egyptian energy sector revenues')

    tailwinds = []
    if usdegp_impact == 'POSITIVE':
        tailwinds.append('EGP appreciation supporting foreign flows')
    if em_label == 'RISK_ON':
        tailwinds.append('EM risk appetite lifting EGX alongside peers')
    if oil_label == 'HIGH':
        tailwinds.append('High oil supporting Egyptian fiscal position')
    if vix_regime_label == 'LOW':
        tailwinds.append('Low global volatility — favourable for risk assets')

    return {
        'success':            True,
        'overall_regime':     overall_regime,
        'macro_arabic':       macro_arabic,
        'macro_headwind':     macro_headwind,
        'risk_on_score':      round(risk_score, 1),
        'risk_on_label':      risk_result.get('label', 'UNKNOWN'),
        'bullish_flags':      bullish_flags,
        'bearish_flags':      bearish_flags,
        'net_score':          net,
        'signals':            signals,
        'key_risks':          risks,
        'key_tailwinds':      tailwinds,
        'generated_at':       datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


# ── Command: daily_context ────────────────────────────────────────────────────

def cmd_daily_context(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    moves = []
    for asset, meta in CROSS_MARKET_ASSETS.items():
        closes = last_n_closes(conn, asset, 3)
        if len(closes) < 2:
            continue
        prev, curr = closes[-2], closes[-1]
        ret = pct_change(prev, curr)
        # Assess significance for EGX
        if meta['direction'] == 'INVERSE':
            egx_signal = 'NEGATIVE' if ret > 0 else 'POSITIVE'
        elif meta['direction'] == 'POSITIVE':
            egx_signal = 'POSITIVE' if ret > 0 else 'NEGATIVE'
        else:
            egx_signal = 'MIXED'

        impact_magnitude = abs(ret) * meta['weight']
        moves.append({
            'asset':            asset,
            'arabic':           meta['arabic'],
            'category':         meta['category'],
            'prev_close':       round(prev, 4),
            'curr_close':       round(curr, 4),
            'change_pct':       round(ret, 3),
            'egx_signal':       egx_signal,
            'impact_score':     round(impact_magnitude, 4),
            'direction_rule':   meta['direction'],
            'weight':           meta['weight'],
        })

    # Sort by impact magnitude descending
    moves.sort(key=lambda x: x['impact_score'], reverse=True)

    # Overall session bias
    weighted_positive = sum(m['weight'] for m in moves if m['egx_signal'] == 'POSITIVE')
    weighted_negative = sum(m['weight'] for m in moves if m['egx_signal'] == 'NEGATIVE')
    total_weight = weighted_positive + weighted_negative or 1.0
    bias_score = (weighted_positive - weighted_negative) / total_weight  # -1 to +1

    if bias_score > 0.3:
        session_bias = 'BULLISH'
        bias_arabic  = 'توقعات إيجابية لجلسة اليوم بناءً على المعطيات الخارجية'
    elif bias_score > 0.0:
        session_bias = 'MILDLY_BULLISH'
        bias_arabic  = 'ميل إيجابي طفيف لجلسة اليوم'
    elif bias_score > -0.3:
        session_bias = 'MILDLY_BEARISH'
        bias_arabic  = 'ميل سلبي طفيف لجلسة اليوم'
    else:
        session_bias = 'BEARISH'
        bias_arabic  = 'توقعات سلبية لجلسة اليوم بناءً على المعطيات الخارجية'

    # Top movers
    top_positive = [m for m in moves if m['egx_signal'] == 'POSITIVE'][:3]
    top_negative = [m for m in moves if m['egx_signal'] == 'NEGATIVE'][:3]

    return {
        'success':             True,
        'session_bias':        session_bias,
        'bias_arabic':         bias_arabic,
        'bias_score':          round(bias_score, 3),
        'top_positive_drivers':top_positive,
        'top_negative_drivers':top_negative,
        'all_moves':           moves,
        'generated_at':        datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


# ── Command: build_full ───────────────────────────────────────────────────────

def cmd_build_full(conn, params):
    if not check_cross_market_available(conn):
        return NO_DATA_ERROR

    ensure_regime_table(conn)

    # Run sub-commands
    risk_result   = cmd_risk_on_score(conn, {'lookback_days': 5})
    macro_result  = cmd_macro_regime(conn, {})
    usdegp_result = cmd_usdegp_regime(conn, {'lookback_days': 20})

    today_str = datetime.utcnow().strftime('%Y-%m-%d')

    risk_score = risk_result.get('risk_on_score', 50) if risk_result.get('success') else 50
    macro_headwind = macro_result.get('macro_headwind', 'NEUTRAL') if macro_result.get('success') else 'NEUTRAL'

    # Sub-regime labels
    usdegp_regime_label = usdegp_result.get('regime', 'UNKNOWN') if usdegp_result.get('success') else 'UNKNOWN'
    vix_lvl    = last_n_closes(conn, 'VIX', 3)
    gold_c     = last_n_closes(conn, 'XAUUSD', 7)
    eem_c      = last_n_closes(conn, 'EEM', 7)
    oil_c      = last_n_closes(conn, 'UKOIL', 7)

    vix_level  = vix_lvl[-1] if vix_lvl else None
    vix_label, _  = _vix_label(vix_level) if vix_level else ('UNKNOWN', '')
    gold_ret   = pct_change(gold_c[0], gold_c[-1]) if len(gold_c) >= 2 else 0.0
    gold_label_v, _ = _gold_label(gold_ret)
    eem_ret    = pct_change(eem_c[0], eem_c[-1]) if len(eem_c) >= 2 else 0.0
    em_label_v, _   = _em_label(eem_ret)
    oil_ret    = pct_change(oil_c[0], oil_c[-1]) if len(oil_c) >= 2 else 0.0
    oil_label_v, _  = _oil_label(oil_ret)

    generated_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    conn.execute("""
        INSERT OR REPLACE INTO cross_market_regime
            (date, usdegp_regime, gold_regime, em_regime, oil_regime, vix_regime,
             risk_on_score, macro_headwind, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today_str,
        usdegp_regime_label,
        gold_label_v,
        em_label_v,
        oil_label_v,
        vix_label,
        risk_score,
        macro_headwind,
        generated_at,
    ))
    conn.commit()

    return {
        'success':          True,
        'date':             today_str,
        'saved_to_db':      True,
        'usdegp_regime':    usdegp_regime_label,
        'gold_regime':      gold_label_v,
        'em_regime':        em_label_v,
        'oil_regime':       oil_label_v,
        'vix_regime':       vix_label,
        'risk_on_score':    round(risk_score, 1),
        'macro_headwind':   macro_headwind,
        'overall_regime':   macro_result.get('overall_regime', 'UNKNOWN'),
        'macro_arabic':     macro_result.get('macro_arabic', ''),
        'key_risks':        macro_result.get('key_risks', []),
        'key_tailwinds':    macro_result.get('key_tailwinds', []),
        'generated_at':     generated_at,
    }


# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMAND_MAP = {
    'market_coverage': cmd_market_coverage,
    'risk_on_score':   cmd_risk_on_score,
    'usdegp_regime':   cmd_usdegp_regime,
    'coupling_matrix': cmd_coupling_matrix,
    'macro_regime':    cmd_macro_regime,
    'daily_context':   cmd_daily_context,
    'build_full':      cmd_build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'usage',
            'hint': 'python cross_market_engine.py <command> <params_json>',
            'commands': sorted(COMMANDS),
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    params = json.loads(sys.argv[2])

    if cmd not in COMMAND_MAP:
        print(json.dumps({
            'error':    'unknown_command',
            'command':  cmd,
            'available': sorted(COMMANDS),
        }))
        sys.exit(1)

    try:
        conn   = get_db()
        result = COMMAND_MAP[cmd](conn, params)
        conn.close()
    except Exception as exc:
        result = {'error': 'engine_exception', 'message': str(exc), 'command': cmd}

    print(json.dumps(result))


if __name__ == '__main__':
    main()
