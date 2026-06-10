#!/usr/bin/env python3
"""
fetch_global_macro.py — EGX Global Macro Data Fetcher
=======================================================
Fetches 22 macro indicators from free APIs and saves to the EGX database.
Uses Yahoo Finance (yfinance) as primary, with fallback to stooq CSV API.

Indicators fetched:
  Market:   VIX, S&P500, NASDAQ, DXY, Gold, Oil (WTI), Oil (Brent)
  Rates:    US 2Y, 10Y, 30Y Treasury yields, SOFR
  FX:       EURUSD, GBPUSD, JPYUSD, CNYUSD, SARUSD (EGP proxy)
  Crypto:   BTC, ETH (risk-on/off sentiment)
  Emerging: EEM (EM ETF), GDX (gold miners)
  Egypt:    USD/EGP (via stooq or Yahoo)

Commands (via python_bridge.js argv protocol):
  fetch_all  — fetch all 22 indicators, store to DB
  fetch_now  — fetch last 5 bars only (quick update)
  status     — report what's in DB
  report     — summary for Telegram

Usage:
  python3 scripts/python/fetch_global_macro.py fetch_all '{}'
  python3 scripts/python/fetch_global_macro.py status    '{}'
"""

import sys
import json
import os
import sqlite3
import datetime
import time
import math

# ── DB path ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_market.db')

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS global_macro (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator   TEXT NOT NULL,
            date        TEXT NOT NULL,
            value       REAL,
            pct_change  REAL,
            source      TEXT DEFAULT 'yfinance',
            fetched_at  TEXT,
            UNIQUE(indicator, date)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_macro_ind_date ON global_macro(indicator, date)")
    db.commit()
    return db

# ── Indicators config ─────────────────────────────────────────────────────────
INDICATORS = {
    # Market sentiment
    'VIX':        { 'ticker': '^VIX',     'label': 'Volatility Index',         'category': 'sentiment'  },
    'SP500':      { 'ticker': '^GSPC',    'label': 'S&P 500 Index',            'category': 'equity'     },
    'NASDAQ':     { 'ticker': '^IXIC',    'label': 'NASDAQ Composite',         'category': 'equity'     },

    # Dollar & FX
    'DXY':        { 'ticker': 'DX-Y.NYB', 'label': 'US Dollar Index',         'category': 'fx'         },
    'EURUSD':     { 'ticker': 'EURUSD=X', 'label': 'EUR/USD',                 'category': 'fx'         },
    'GBPUSD':     { 'ticker': 'GBPUSD=X', 'label': 'GBP/USD',                 'category': 'fx'         },
    'JPYUSD':     { 'ticker': 'JPY=X',    'label': 'JPY/USD (inverted)',       'category': 'fx'         },
    'CNYUSD':     { 'ticker': 'CNY=X',    'label': 'CNY/USD',                 'category': 'fx'         },

    # Commodities
    'GOLD':       { 'ticker': 'GC=F',     'label': 'Gold Futures (USD/oz)',    'category': 'commodity'  },
    'OIL_WTI':    { 'ticker': 'CL=F',     'label': 'WTI Crude Oil Futures',   'category': 'commodity'  },
    'OIL_BRENT':  { 'ticker': 'BZ=F',     'label': 'Brent Crude Futures',     'category': 'commodity'  },
    'SILVER':     { 'ticker': 'SI=F',     'label': 'Silver Futures',           'category': 'commodity'  },

    # US Treasury yields
    'US2Y':       { 'ticker': '^IRX',     'label': 'US 2Y Treasury Yield',    'category': 'rates'      },
    'US10Y':      { 'ticker': '^TNX',     'label': 'US 10Y Treasury Yield',   'category': 'rates'      },
    'US30Y':      { 'ticker': '^TYX',     'label': 'US 30Y Treasury Yield',   'category': 'rates'      },

    # Crypto (risk-on/off)
    'BTC':        { 'ticker': 'BTC-USD',  'label': 'Bitcoin USD',             'category': 'crypto'     },
    'ETH':        { 'ticker': 'ETH-USD',  'label': 'Ethereum USD',            'category': 'crypto'     },

    # Emerging markets
    'EEM':        { 'ticker': 'EEM',      'label': 'iShares MSCI EM ETF',     'category': 'em'         },
    'GDX':        { 'ticker': 'GDX',      'label': 'VanEck Gold Miners ETF',  'category': 'em'         },

    # Egypt-specific
    'EGPUSD':     { 'ticker': 'EGP=X',   'label': 'EGP/USD exchange rate',   'category': 'egypt'      },

    # Commodities relevant to Egypt
    'WHEAT':      { 'ticker': 'ZW=F',     'label': 'Wheat Futures',           'category': 'commodity'  },
    'NATGAS':     { 'ticker': 'NG=F',     'label': 'Natural Gas Futures',     'category': 'commodity'  },
}

# ── Fetch via yfinance ────────────────────────────────────────────────────────
def fetch_yfinance(ticker, period='1y', interval='1d'):
    """Fetch OHLCV from Yahoo Finance. Returns list of {date, close, pct_change}."""
    try:
        import yfinance as yf
    except ImportError:
        return None, 'yfinance not installed'

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval=interval, auto_adjust=True)
        if hist.empty:
            return None, f'No data for {ticker}'

        rows = []
        prev_close = None
        for ts, row in hist.iterrows():
            date_str = ts.strftime('%Y-%m-%d') if hasattr(ts, 'strftime') else str(ts)[:10]
            close = float(row['Close'])
            pct = ((close - prev_close) / prev_close * 100) if prev_close and prev_close != 0 else 0.0
            rows.append({'date': date_str, 'value': close, 'pct_change': round(pct, 4)})
            prev_close = close

        return rows, None
    except Exception as e:
        return None, str(e)


def fetch_stooq(ticker, period_days=365):
    """Fallback: fetch from stooq CSV API."""
    import urllib.request
    end   = datetime.date.today()
    start = end - datetime.timedelta(days=period_days)

    # stooq uses symbols like %5EVIX for ^VIX
    stooq_ticker = ticker.replace('^', '%5E').replace('=X', '').lower()
    url = (f"https://stooq.com/q/d/l/?s={stooq_ticker}"
           f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}&i=d")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('utf-8')

        lines = [l.strip() for l in text.strip().splitlines()]
        if len(lines) < 2 or 'No data' in text or '<html' in text:
            return None, f'No stooq data for {stooq_ticker}'

        header = lines[0].lower().split(',')
        close_idx = next((i for i, h in enumerate(header) if 'close' in h), -1)
        date_idx  = next((i for i, h in enumerate(header) if 'date' in h), 0)
        if close_idx == -1:
            return None, f'No close column in stooq for {stooq_ticker}'

        rows = []
        prev_close = None
        for line in lines[1:]:
            parts = line.split(',')
            if len(parts) <= max(date_idx, close_idx):
                continue
            try:
                date_str  = parts[date_idx].strip()
                close_val = float(parts[close_idx].strip())
                pct = ((close_val - prev_close) / prev_close * 100) if prev_close and prev_close != 0 else 0.0
                rows.append({'date': date_str, 'value': close_val, 'pct_change': round(pct, 4)})
                prev_close = close_val
            except ValueError:
                continue

        return rows, None
    except Exception as e:
        return None, str(e)


# ── DB write ──────────────────────────────────────────────────────────────────
def save_to_db(db, indicator, rows, source):
    ts = datetime.datetime.utcnow().isoformat()
    saved = 0
    for r in rows:
        try:
            db.execute("""
                INSERT INTO global_macro (indicator, date, value, pct_change, source, fetched_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(indicator, date) DO UPDATE SET
                    value=excluded.value,
                    pct_change=excluded.pct_change,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
            """, (indicator, r['date'], r['value'], r['pct_change'], source, ts))
            saved += 1
        except Exception:
            pass
    db.commit()
    return saved


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_fetch_all(params):
    """Fetch all 22 indicators, 1 year of daily data."""
    db = get_db()
    period = params.get('period', '1y')
    results = {}
    errors  = {}
    total_saved = 0

    for name, cfg in INDICATORS.items():
        ticker = cfg['ticker']
        # Try yfinance first
        rows, err = fetch_yfinance(ticker, period=period)
        source = 'yfinance'

        # Fallback to stooq
        if rows is None:
            rows, err2 = fetch_stooq(ticker)
            source = 'stooq'
            if rows is None:
                errors[name] = err or err2
                results[name] = {'saved': 0, 'error': errors[name]}
                continue

        saved = save_to_db(db, name, rows, source)
        total_saved += saved
        results[name] = {'saved': saved, 'source': source, 'bars': len(rows)}

        # Be gentle with rate limits
        time.sleep(0.3)

    # Compute summary
    latest = {}
    for name in INDICATORS:
        row = db.execute("""
            SELECT date, value, pct_change FROM global_macro
            WHERE indicator=? ORDER BY date DESC LIMIT 1
        """, (name,)).fetchone()
        if row:
            latest[name] = {'date': row['date'], 'value': row['value'], 'pct_change': row['pct_change']}

    db.close()
    return {
        'success':     True,
        'command':     'fetch_all',
        'total_saved': total_saved,
        'indicators':  len(INDICATORS),
        'successful':  len([r for r in results.values() if r.get('saved', 0) > 0]),
        'errors':      len(errors),
        'details':     results,
        'latest':      latest,
        'error_list':  errors,
    }


def cmd_fetch_now(params):
    """Fetch only the last 5 days (quick daily update)."""
    db = get_db()
    saved_total = 0
    errors = []

    for name, cfg in INDICATORS.items():
        rows, err = fetch_yfinance(cfg['ticker'], period='5d')
        if rows is None:
            rows, _ = fetch_stooq(cfg['ticker'], period_days=7)
            source = 'stooq'
        else:
            source = 'yfinance'

        if rows:
            saved_total += save_to_db(db, name, rows, source)
        else:
            errors.append(f'{name}: {err}')
        time.sleep(0.2)

    latest = {}
    for name in INDICATORS:
        row = db.execute("""
            SELECT date, value, pct_change FROM global_macro
            WHERE indicator=? ORDER BY date DESC LIMIT 1
        """, (name,)).fetchone()
        if row:
            latest[name] = {'date': row['date'], 'value': row['value'], 'pct_change': row['pct_change']}

    db.close()
    return {
        'success': True,
        'command': 'fetch_now',
        'saved': saved_total,
        'errors': errors,
        'latest': latest,
    }


def cmd_status(params):
    """Report what macro data is in the DB."""
    db = get_db()
    rows = db.execute("""
        SELECT indicator,
               COUNT(*)     AS n_bars,
               MIN(date)    AS first_date,
               MAX(date)    AS last_date,
               MAX(value)   AS max_value,
               MIN(value)   AS min_value
        FROM global_macro
        GROUP BY indicator
        ORDER BY indicator
    """).fetchall()

    indicators_info = []
    for r in rows:
        indicators_info.append({
            'indicator':  r['indicator'],
            'n_bars':     r['n_bars'],
            'first_date': r['first_date'],
            'last_date':  r['last_date'],
        })

    total_rows = db.execute("SELECT COUNT(*) AS n FROM global_macro").fetchone()['n']
    db.close()
    return {
        'success':    True,
        'command':    'status',
        'total_rows': total_rows,
        'n_indicators': len(rows),
        'indicators': indicators_info,
    }


def cmd_report(params):
    """Build a Telegram-ready macro snapshot."""
    db = get_db()
    snapshot = {}

    # Get latest value + 1-day change + 5-day change
    for name in INDICATORS:
        rows = db.execute("""
            SELECT date, value, pct_change FROM global_macro
            WHERE indicator=? ORDER BY date DESC LIMIT 6
        """, (name,)).fetchall()
        if not rows:
            continue
        last = rows[0]
        d5   = rows[5] if len(rows) >= 6 else None
        chg5 = ((last['value'] - d5['value']) / d5['value'] * 100) if d5 and d5['value'] else None
        snapshot[name] = {
            'date':        last['date'],
            'value':       last['value'],
            'chg1d':       last['pct_change'],
            'chg5d':       round(chg5, 2) if chg5 is not None else None,
            'label':       INDICATORS[name]['label'],
            'category':    INDICATORS[name]['category'],
        }

    # Build structured sections
    def fmt(val, prec=2, prefix=''):
        if val is None: return '?'
        return f"{prefix}{val:.{prec}f}"

    def arrow(pct):
        if pct is None: return ''
        return ' ▲' if pct > 0.1 else (' ▼' if pct < -0.1 else ' ─')

    sections = {
        'sentiment':  ['VIX', 'SP500', 'NASDAQ', 'EEM'],
        'fx':         ['DXY', 'EURUSD', 'EGPUSD'],
        'commodity':  ['GOLD', 'OIL_WTI', 'OIL_BRENT', 'WHEAT'],
        'rates':      ['US2Y', 'US10Y', 'US30Y'],
        'crypto':     ['BTC', 'ETH'],
    }

    section_lines = {}
    for sec, keys in sections.items():
        lines = []
        for k in keys:
            if k not in snapshot: continue
            s = snapshot[k]
            d1 = s['chg1d']
            lines.append(f"  {k:<12} {fmt(s['value'],2)} {arrow(d1)} ({fmt(d1,2,'+')}%)")
        section_lines[sec] = '\n'.join(lines)

    # Macro regime signals
    vix = snapshot.get('VIX', {}).get('value', 20)
    dxy = snapshot.get('DXY', {}).get('chg5d', 0)
    oil = snapshot.get('OIL_WTI', {}).get('chg5d', 0)
    us10y = snapshot.get('US10Y', {}).get('value', 4.0)

    if vix and vix > 30:
        regime = 'STRESS / HIGH-FEAR'
    elif vix and vix < 15:
        regime = 'CALM / RISK-ON'
    else:
        regime = 'NEUTRAL'

    egp_impact = 'NEGATIVE' if (dxy or 0) > 1.0 else ('POSITIVE' if (dxy or 0) < -1.0 else 'NEUTRAL')

    db.close()
    return {
        'success':     True,
        'command':     'report',
        'snapshot':    snapshot,
        'macro_regime': regime,
        'egp_impact':  egp_impact,
        'section_text': section_lines,
        'vix':         snapshot.get('VIX', {}).get('value'),
        'dxy_5d':      dxy,
        'oil_5d':      oil,
        'us10y':       us10y,
        'date':        datetime.date.today().isoformat(),
    }


# ── Dispatch ──────────────────────────────────────────────────────────────────
COMMANDS = {
    'fetch_all':  cmd_fetch_all,
    'fetch_now':  cmd_fetch_now,
    'status':     cmd_status,
    'report':     cmd_report,
}

if __name__ == '__main__':
    import sys as _sys

    # argv-based invocation (Node.js bridge): script command json_params
    if len(_sys.argv) >= 2:
        _cmd = _sys.argv[1]
        _par = json.loads(_sys.argv[2]) if len(_sys.argv) >= 3 else {}
    else:
        # stdin JSON fallback
        try:
            _raw = sys.stdin.read().strip()
            _msg = json.loads(_raw) if _raw else {}
        except Exception:
            _msg = {}
        _cmd = _msg.get('command', 'status')
        _par = _msg.get('params', {})

    fn = COMMANDS.get(_cmd)
    if not fn:
        print(json.dumps({'success': False, 'error': f'Unknown command: {_cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = fn(_par)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
