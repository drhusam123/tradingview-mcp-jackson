"""
EGX ML Outcome Tracker
=======================
Fills forward_test_predictions with actual price outcomes after 1, 5, 10 trading sessions.
Computes hit-rate, average return, Sharpe, and win-rate for completed signals.

Run daily after market close:
    python3 egx_outcome_tracker.py          # fill all pending outcomes
    python3 egx_outcome_tracker.py --report # show performance report
"""

import sqlite3
import datetime
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'egx_trading.db'

try:
    from event_calendar import is_trading_day, trading_day_offset
    _HAS_CAL = True
except ImportError:
    _HAS_CAL = False


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ohlcv_table(conn) -> str:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('view','table') AND name='ohlcv_history_execution'"
    ).fetchone()
    return row['name'] if row else 'ohlcv_history'


def ensure_schema(conn):
    for col, typ in [
        ("close_1d", "REAL"),
        ("return_1d", "REAL"),
        ("outcome_1d", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE forward_test_predictions ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def get_close_on_or_after(conn, symbol: str, date_str: str, max_sessions: int = 7) -> tuple:
    """Return (actual_date, close) for the first trading day >= date_str."""
    table = ohlcv_table(conn)
    if _HAS_CAL:
        cur = datetime.date.fromisoformat(date_str)
        for _ in range(max_sessions):
            if is_trading_day(cur):
                row = conn.execute(f"""
                    SELECT date(bar_time,'unixepoch') as bar_date, close
                    FROM {table} WHERE symbol=? AND date(bar_time,'unixepoch')=?
                """, (symbol, cur.isoformat())).fetchone()
                if row and row['close']:
                    return row['bar_date'], float(row['close'])
            cur += datetime.timedelta(days=1)
        return None, None

    dt = datetime.date.fromisoformat(date_str)
    for offset in range(max_sessions + 1):
        check = (dt + datetime.timedelta(days=offset)).isoformat()
        row = conn.execute(f"""
            SELECT date(bar_time,'unixepoch') as bar_date, close
            FROM {table} WHERE symbol=? AND date(bar_time,'unixepoch')=?
        """, (symbol, check)).fetchone()
        if row and row['close']:
            return row['bar_date'], float(row['close'])
    return None, None


def target_trading_date(pred_date: str, sessions_forward: int):
    if _HAS_CAL:
        return trading_day_offset(pred_date, sessions_forward)
    return datetime.date.fromisoformat(pred_date) + datetime.timedelta(days=sessions_forward)


def fill_outcomes():
    conn = get_db()
    ensure_schema(conn)
    today = datetime.date.today()
    table = ohlcv_table(conn)

    pending = conn.execute("""
        SELECT * FROM forward_test_predictions
        WHERE status IN ('PENDING', 'FILLED_5D')
        ORDER BY pred_date
    """).fetchall()

    filled = 0
    for p in pending:
        sym       = p['symbol']
        pred_date = p['pred_date']
        entry     = p['entry_price']

        if not entry or entry <= 0:
            _, entry = get_close_on_or_after(conn, sym, pred_date, max_sessions=3)
            if not entry:
                continue

        updates = {}
        horizons_ready = {1: False, 5: False, 10: False}

        for sessions, hkey in [(1, 1), (5, 5), (10, 10)]:
            target_dt = target_trading_date(pred_date, sessions)
            if not target_dt or target_dt > today:
                continue

            actual_date, close_val = get_close_on_or_after(conn, sym, target_dt.isoformat(), max_sessions=3)
            if close_val is None:
                continue

            ret = (close_val - entry) / entry
            if hkey == 1:
                updates['close_1d'] = close_val
                updates['return_1d'] = round(ret, 4)
                updates['outcome_1d'] = 'WIN' if ret >= 0.03 else 'LOSS' if ret < -0.03 else 'FLAT'
                horizons_ready[1] = True
            elif hkey == 5:
                updates['close_5d'] = close_val
                updates['return_5d'] = round(ret, 4)
                updates['hit_t1'] = 1 if ret >= 0.07 else 0
                updates['hit_sl'] = 1 if ret <= -0.08 else 0
                updates['outcome_5d'] = 'WIN' if ret >= 0.07 else 'LOSS' if ret < -0.05 else 'FLAT'
                horizons_ready[5] = True
            elif hkey == 10:
                updates['close_10d'] = close_val
                updates['return_10d'] = round(ret, 4)
                updates['outcome_10d'] = 'WIN' if ret >= 0.07 else 'LOSS' if ret < -0.05 else 'FLAT'
                horizons_ready[10] = True

        if not updates:
            continue

        try:
            bars = conn.execute(f"""
                SELECT close, high, low
                FROM {table}
                WHERE symbol=? AND date(bar_time,'unixepoch') > ?
                  AND date(bar_time,'unixepoch') <= ?
                ORDER BY bar_time
            """, (sym, pred_date,
                  (datetime.date.fromisoformat(pred_date) + datetime.timedelta(days=20)).isoformat()
                  )).fetchall()
            if bars:
                max_h = max(float(b['high'] or entry) for b in bars)
                min_l = min(float(b['low'] or entry) for b in bars)
                updates['max_gain_10d'] = round((max_h - entry) / entry, 4)
                updates['max_drawdown_10d'] = round((min_l - entry) / entry, 4)
        except Exception:
            pass

        if horizons_ready[10]:
            updates['status'] = 'COMPLETED'
        elif horizons_ready[5]:
            updates['status'] = 'FILLED_5D'
        elif horizons_ready[1]:
            updates['status'] = 'PENDING'

        set_parts = ', '.join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [p['id']]
        conn.execute(
            f"UPDATE forward_test_predictions SET {set_parts}, updated_at=datetime('now') WHERE id=?",
            values,
        )
        filled += 1

    conn.commit()
    conn.close()
    print(f"[TRACKER] Filled outcomes for {filled} / {len(pending)} pending predictions")
    return filled


def performance_report():
    conn = get_db()

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       EGX ML — Forward Test Performance Report                  ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    total = conn.execute("SELECT COUNT(*) FROM forward_test_predictions").fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM forward_test_predictions WHERE status IN ('COMPLETED','FILLED_5D','FILLED_10D')"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM forward_test_predictions WHERE status='PENDING'"
    ).fetchone()[0]

    print(f"  Total signals tracked : {total}")
    print(f"  Completed (outcomes)  : {completed}")
    print(f"  Pending               : {pending}")
    print()

    wr_row = conn.execute("""
        SELECT
          COUNT(*) AS n,
          SUM(CASE WHEN outcome_5d='WIN' THEN 1 ELSE 0 END) AS wins,
          AVG(return_5d) AS avg_ret
        FROM forward_test_predictions
        WHERE return_5d IS NOT NULL
    """).fetchone()
    if wr_row and wr_row['n']:
        wr = (wr_row['wins'] or 0) / wr_row['n'] * 100
        print(f"  5d Win Rate           : {wr:.1f}% ({wr_row['wins']}/{wr_row['n']})")
        print(f"  Avg 5d Return         : {(wr_row['avg_ret'] or 0)*100:+.2f}%")
        print()

    pending_dates = conn.execute("""
        SELECT MIN(pred_date), MAX(pred_date), COUNT(*)
        FROM forward_test_predictions WHERE status='PENDING'
    """).fetchone()
    if pending_dates and pending_dates[2]:
        print(f"  Pending range         : {pending_dates[0]} → {pending_dates[1]} ({pending_dates[2]} rows)")
        print()

    recent = conn.execute("""
        SELECT symbol, pred_date, ensemble_prob, confidence_tier,
               entry_price, return_5d, outcome_5d, regime_at_pred
        FROM forward_test_predictions
        WHERE return_5d IS NOT NULL
        ORDER BY pred_date DESC LIMIT 10
    """).fetchall()
    if recent:
        print("  ── Recent Completed Signals ───────────────────────────────────")
        print(f"  {'Sym':8}  {'Date':12}  {'Prob':6}  {'Entry':8}  {'Ret5d':7}  {'Result':6}  {'Regime':8}")
        print("  " + "─" * 68)
        for r in recent:
            ret_str = f"{r['return_5d']*100:+.1f}%" if r['return_5d'] is not None else "—"
            out_icon = '✅' if r['outcome_5d'] == 'WIN' else '❌' if r['outcome_5d'] == 'LOSS' else '➡️'
            prob = (r['ensemble_prob'] or 0) * 100
            print(f"  {r['symbol']:8}  {r['pred_date']:12}  {prob:5.1f}%  "
                  f"{r['entry_price'] or 0:8.2f}  {ret_str:7}  {out_icon}     {r['regime_at_pred'] or '':8}")

    conn.close()
    print()
    print("━" * 68)


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--report' in args:
        fill_outcomes()
        performance_report()
    else:
        fill_outcomes()
        performance_report()
