#!/usr/bin/env python3
"""
Nightly Outcome Filler
======================
Fills return_t1/t3/t5/t10 in recommendation_outcomes from ohlcv_history_execution.
Also calculates hit_t1, hit_t5, reached_t1_target, hit_stop, outcome_filled.

Run: python3 scripts/python/outcome_filler.py
Cron: 30 15 * * 0-4  (after market close, Sun-Thu)
"""
import sqlite3, datetime, sys, json
from pathlib import Path

DB = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    today = datetime.date.today().isoformat()

    # Find signals that need outcome filling (entry price known, not yet filled)
    pending = conn.execute("""
        SELECT id, symbol, signal_date, entry_price, stop_loss, t1_target
        FROM recommendation_outcomes
        WHERE outcome_filled = 0
          AND entry_price IS NOT NULL AND entry_price > 0
          AND signal_date <= date('now', '-1 days')
    """).fetchall()

    print(f"[OutcomeFiller] {today}: {len(pending)} pending signals", flush=True)

    updated = 0
    for rec in pending:
        sid = rec['id']
        sym = rec['symbol']
        sig_date = rec['signal_date']
        entry = rec['entry_price']
        stop = rec['stop_loss']
        target = rec['t1_target']

        # Get OHLCV prices for t+1, t+3, t+5, t+10 trading days after signal
        prices = conn.execute("""
            SELECT date(bar_time,'unixepoch') as bar_date,
                   close
            FROM ohlcv_history_execution
            WHERE symbol = ?
              AND date(bar_time,'unixepoch') > ?
            ORDER BY bar_time
            LIMIT 15
        """, (sym, sig_date)).fetchall()

        if len(prices) < 1:
            continue

        bars = [p['close'] for p in prices]

        c_t1  = bars[0] if len(bars) >= 1 else None
        c_t3  = bars[2] if len(bars) >= 3 else None
        c_t5  = bars[4] if len(bars) >= 5 else None
        c_t10 = bars[9] if len(bars) >= 10 else None

        def ret(close):
            return round((close - entry) / entry * 100, 4) if close and entry else None

        r1 = ret(c_t1); r3 = ret(c_t3); r5 = ret(c_t5); r10 = ret(c_t10)

        # Hit indicators
        hit_t1 = int(r1 > 0) if r1 is not None else None
        hit_t5 = int(r5 > 0) if r5 is not None else None

        # Did it reach T1 target in first 10 bars?
        reached_target = 0
        if target and entry:
            for bar_close in bars[:10]:
                if bar_close >= target:
                    reached_target = 1
                    break

        # Did it hit stop loss?
        hit_stop_flag = 0
        if stop and entry:
            for bar_close in bars[:10]:
                if bar_close <= stop:
                    hit_stop_flag = 1
                    break

        # Only mark filled if we have at least t5
        is_filled = int(c_t5 is not None)

        conn.execute("""
            UPDATE recommendation_outcomes
            SET close_t1=?, close_t3=?, close_t5=?, close_t10=?,
                return_t1=?, return_t3=?, return_t5=?, return_t10=?,
                hit_t1=?, hit_t5=?, reached_t1_target=?, hit_stop=?,
                outcome_filled=?
            WHERE id=?
        """, (c_t1, c_t3, c_t5, c_t10, r1, r3, r5, r10,
              hit_t1, hit_t5, reached_target, hit_stop_flag, is_filled, sid))

        if is_filled:
            updated += 1

    conn.commit()

    # Compute summary metrics
    metrics = conn.execute("""
        SELECT
            COUNT(*) as n,
            SUM(CASE WHEN return_t5 > 0 THEN 1 ELSE 0 END) as wins,
            AVG(return_t5) as avg_ret,
            AVG(CASE WHEN return_t5 > 0 THEN return_t5 END) as avg_win,
            AVG(CASE WHEN return_t5 <= 0 THEN return_t5 END) as avg_loss,
            MIN(return_t5) as worst,
            MAX(return_t5) as best
        FROM recommendation_outcomes
        WHERE outcome_filled=1 AND return_t5 IS NOT NULL
          AND signal_date >= date('now','-90 days')
    """).fetchone()

    summary = {
        'date': today,
        'newly_filled': updated,
        'total_filled_90d': metrics['n'] if metrics else 0,
    }
    if metrics and metrics['n'] > 0:
        wr = metrics['wins'] / metrics['n']
        summary['win_rate_t5'] = round(wr, 4)
        summary['avg_return_t5'] = round(metrics['avg_ret'], 4)
        summary['avg_win'] = round(float(metrics['avg_win'] or 0), 4)
        summary['avg_loss'] = round(float(metrics['avg_loss'] or 0), 4)
        if metrics['avg_loss'] and metrics['avg_loss'] != 0:
            summary['profit_factor'] = round(
                abs((float(metrics['avg_win'] or 0) * wr)) /
                abs(float(metrics['avg_loss']) * (1 - wr)), 4)
        summary['best'] = round(float(metrics['best']), 4)
        summary['worst'] = round(float(metrics['worst']), 4)

    print(json.dumps(summary), flush=True)

    # Store in DB
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outcome_metrics (
            run_date TEXT PRIMARY KEY,
            metrics_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Also close ML forward-test predictions. Keeping this here ensures the
    # single daily outcome cron closes both client recommendations and ML tests.
    try:
        from egx_outcome_tracker import fill_outcomes as fill_forward_predictions
        filled_forward = fill_forward_predictions()
        summary['forward_predictions_filled'] = filled_forward
    except Exception as e:
        summary['forward_predictions_error'] = str(e)[:300]
        print(f"[OutcomeFiller] forward tracker failed: {e}", flush=True)
    conn.execute("INSERT OR REPLACE INTO outcome_metrics (run_date, metrics_json) VALUES (?,?)",
                 (today, json.dumps(summary)))
    conn.commit()
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
