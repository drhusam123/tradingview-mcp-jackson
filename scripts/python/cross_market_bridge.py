"""
cross_market_bridge.py
──────────────────────
Bridges global_macro table (egx_market.db) → cross_market_daily table (egx_trading.db).
Runs after fetch_global_macro.py to ensure cross_market_engine.py has fresh OHLCV-format
data for VIX, Gold, Oil, USD/EGP, S&P500, EEM, DXY, and US10Y.

Commands (sys.argv[1]):
  build_full  — bridge all mapped indicators (default)
  status      — show row counts and latest dates
"""

import json
import pathlib
import sqlite3
import sys
import time

HERE    = pathlib.Path(__file__).parent
DB_SRC  = HERE / '../../data/egx_market.db'    # fetch_global_macro output
DB_DST  = HERE / '../../data/egx_trading.db'   # cross_market_engine input

# global_macro indicator  →  cross_market_daily asset
# OIL_BRENT wins over OIL_WTI (same asset UKOIL — BRENT inserted first)
ASSET_MAP = {
    'EGPUSD':    'USDEGP',   # inverted: EGP/USD → USD/EGP
    'GOLD':      'XAUUSD',
    'OIL_BRENT': 'UKOIL',
    'OIL_WTI':   'UKOIL',    # fallback if BRENT missing
    'SP500':     'SPY',
    'EEM':       'EEM',
    'VIX':       'VIX',
    'DXY':       'DXY',
    'US10Y':     'US10Y',
    'EURUSD':    'EURUSD',
}


def _get_src():
    if not DB_SRC.exists():
        raise FileNotFoundError(f'Source DB not found: {DB_SRC}')
    con = sqlite3.connect(str(DB_SRC))
    con.row_factory = sqlite3.Row
    return con


def _get_dst():
    con = sqlite3.connect(str(DB_DST))
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''
        CREATE TABLE IF NOT EXISTS cross_market_daily (
            asset    TEXT,
            bar_time TEXT,
            open     REAL,
            high     REAL,
            low      REAL,
            close    REAL,
            volume   REAL,
            UNIQUE(asset, bar_time)
        )
    ''')
    con.commit()
    return con


def build_full(params: dict) -> dict:
    t0 = time.time()
    src = _get_src()
    dst = _get_dst()

    n_inserted = 0
    n_skipped  = 0
    seen_ukoil: set = set()

    for indicator, asset_name in ASSET_MAP.items():
        rows = src.execute(
            'SELECT date, value FROM global_macro '
            'WHERE indicator = ? AND value IS NOT NULL ORDER BY date',
            (indicator,)
        ).fetchall()

        for row in rows:
            date_str = str(row['date'])
            try:
                val = float(row['value'])
            except (TypeError, ValueError):
                continue
            if not val:
                continue

            # Invert EGPUSD → USDEGP only when the source is truly EGP/USD.
            # Some upstream feeds already store USD/EGP under the legacy EGPUSD key.
            if indicator == 'EGPUSD' and 0 < val < 1:
                val = 1.0 / val

            # Deduplicate UKOIL: OIL_BRENT takes priority over OIL_WTI
            if asset_name == 'UKOIL':
                if indicator == 'OIL_WTI' and date_str in seen_ukoil:
                    n_skipped += 1
                    continue
                seen_ukoil.add(date_str)

            try:
                dst.execute(
                    'INSERT OR REPLACE INTO cross_market_daily '
                    '(asset, bar_time, open, high, low, close, volume) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (asset_name, date_str, val, val, val, val, 0)
                )
                n_inserted += 1
            except Exception:
                n_skipped += 1

    dst.commit()
    src.close()
    dst.close()

    return {
        'success':    True,
        'command':    'build_full',
        'n_inserted': n_inserted,
        'n_skipped':  n_skipped,
        'elapsed':    round(time.time() - t0, 2),
    }


def status(params: dict) -> dict:
    dst = _get_dst()
    assets = list(set(ASSET_MAP.values()))
    rows_info = {}
    for asset in sorted(assets):
        cnt = dst.execute(
            'SELECT COUNT(*) FROM cross_market_daily WHERE asset=?', (asset,)
        ).fetchone()[0]
        latest = dst.execute(
            'SELECT MAX(bar_time) FROM cross_market_daily WHERE asset=?', (asset,)
        ).fetchone()[0]
        rows_info[asset] = {'n_rows': cnt, 'latest': latest}
    dst.close()
    return {'command': 'status', 'assets': rows_info}


COMMANDS = {
    'build_full': build_full,
    'status':     status,
}


def main():
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({'error': f'Unknown command: {cmd}',
                          'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()}))
        sys.exit(1)


if __name__ == '__main__':
    main()
