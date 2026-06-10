#!/usr/bin/env python3
"""
Adaptive Threshold Manager
===========================
Weekly: reads realized precision from outcome_metrics and adjusts
        ml_threshold in adaptive_gate_params table.

Run: python3 scripts/python/adaptive_threshold.py
Cron: 0 16 * * 0  (after market close on Sundays — start of EGX week)
"""
import sqlite3, json, datetime
from pathlib import Path

DB = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    today = datetime.date.today().isoformat()

    # Read last 30d realized win rate
    metrics = conn.execute("""
        SELECT json_extract(metrics_json,'$.win_rate_t5') as win_rate_t5,
               json_extract(metrics_json,'$.profit_factor') as profit_factor
        FROM outcome_metrics
        WHERE run_date >= date('now','-30 days')
        ORDER BY run_date DESC LIMIT 10
    """).fetchall()

    if not metrics:
        print("[AdaptiveThreshold] No metrics yet — using defaults", flush=True)
        conn.close()
        return

    win_rates = [float(m['win_rate_t5']) for m in metrics if m['win_rate_t5']]
    if not win_rates:
        print("[AdaptiveThreshold] No win_rate_t5 data yet — skipping", flush=True)
        conn.close()
        return

    avg_wr = sum(win_rates) / len(win_rates)
    print(f"[AdaptiveThreshold] Avg win rate last 30d: {avg_wr:.1%}", flush=True)

    # Current threshold
    cur = conn.execute("""
        SELECT param_value FROM adaptive_gate_params
        WHERE param_name='ml_threshold_OVERALL'
        ORDER BY updated_at DESC LIMIT 1
    """).fetchone()
    cur_threshold = float(cur['param_value']) if cur else 55.0

    # Adjust:
    # WR < 50% → raise threshold by 3 pts
    # WR > 70% → lower threshold by 2 pts
    # 50-70% → hold
    if avg_wr < 0.50:
        new_threshold = min(75.0, cur_threshold + 3.0)
        reason = f"WR={avg_wr:.1%} < 50% → raise threshold"
    elif avg_wr > 0.70:
        new_threshold = max(45.0, cur_threshold - 2.0)
        reason = f"WR={avg_wr:.1%} > 70% → lower threshold"
    else:
        new_threshold = cur_threshold
        reason = f"WR={avg_wr:.1%} in 50-70% range → hold"

    print(f"[AdaptiveThreshold] {reason}: {cur_threshold:.1f} → {new_threshold:.1f}", flush=True)

    if new_threshold != cur_threshold:
        conn.execute("""
            INSERT OR REPLACE INTO adaptive_gate_params
            (param_name, param_value, basis, n_signals, precision_p50, updated_at)
            VALUES ('ml_threshold_OVERALL', ?, ?, 0, 0.5, CURRENT_TIMESTAMP)
        """, (new_threshold, reason))
        conn.execute("""
            INSERT OR REPLACE INTO adaptive_gate_params
            (param_name, param_value, basis, n_signals, precision_p50, updated_at)
            VALUES ('ml_threshold_BEAR', ?, ?, 0, 0.5, CURRENT_TIMESTAMP)
        """, (min(new_threshold + 10.0, 80.0), 'bear_premium_+10'))
        conn.commit()

    conn.close()
    print(json.dumps({'date': today, 'old_threshold': cur_threshold,
                      'new_threshold': new_threshold, 'reason': reason}), flush=True)


if __name__ == '__main__':
    main()
