#!/usr/bin/env python3
"""
fix_prediction_reliability.py — Data-quality fixes for explosion_predictions.

Fix B: Add model_version + reliability_flag columns and backfill:
  - May 16-22: LOW_RELIABILITY_OVERFIT  (models with 700-1000 trees, in-sample leakage)
  - May 23-26: ACCEPTABLE               (582-tree model, but meta-model still broken)
  - May 27+:   OK                       (weighted-average ensemble fix applied 2026-05-27)

Fix D: Create forward_test_predictions table and seed it with current top predictions
       so we can measure real-world lift over the next 5-7 trading days.

Usage:
  python3 fix_prediction_reliability.py [--dry-run]
"""

import os
import sys
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')
DRY_RUN = '--dry-run' in sys.argv


def log(msg: str):
    print(f"[fix_reliability] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# Fix B: reliability columns + backfill
# ═══════════════════════════════════════════════════════════════════════════════

def apply_fix_b(conn: sqlite3.Connection):
    """Add model_version and reliability_flag columns; backfill by date range."""
    log("=== Fix B: Reliability flagging ===")

    # Check if columns already exist
    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(explosion_predictions)"
    ).fetchall()]

    if 'model_version' not in cols:
        log("Adding column: model_version TEXT DEFAULT 'unknown'")
        if not DRY_RUN:
            conn.execute(
                "ALTER TABLE explosion_predictions ADD COLUMN model_version TEXT DEFAULT 'unknown'"
            )
    else:
        log("Column model_version already exists — skipping ALTER")

    if 'reliability_flag' not in cols:
        log("Adding column: reliability_flag TEXT DEFAULT 'UNKNOWN'")
        if not DRY_RUN:
            conn.execute(
                "ALTER TABLE explosion_predictions ADD COLUMN reliability_flag TEXT DEFAULT 'UNKNOWN'"
            )
    else:
        log("Column reliability_flag already exists — skipping ALTER")

    if not DRY_RUN:
        conn.commit()

    # ── Backfill by known date ranges ────────────────────────────────────────
    # Date ranges determined from git log of model checkpoints:
    #   May 16-18: variable models, partial data (122-149-130 rows — incomplete universe)
    #              model was ~1000 trees, in-sample overfit, 80-99% probabilities
    #   May 19:    only 37 rows (pipeline error), model still overfit
    #   May 20:    130 rows (incomplete), model still overfit
    #   May 21-22: 252 rows each BUT meta-model still using in-sample stacking
    #              → INVERTED ordering (high base scores → low final scores)
    #   May 23-26: 252 rows, 582-tree model (improved), but meta-model still broken
    #              → weighted avg fix NOT yet applied
    #   May 27+:   weighted-average ensemble fix applied → RELIABLE

    reliability_schedule = [
        # (date_from, date_to_inclusive, model_version, reliability_flag, reason)
        ('2026-05-16', '2026-05-20',
         'lgbm_overfit_v0',
         'LOW_RELIABILITY_OVERFIT',
         'Incomplete universe (37-149 rows), overfit model ~1000 trees, '
         'probabilities 80-99% unrealistic'),
        ('2026-05-21', '2026-05-22',
         'lgbm_v3_meta_broken',
         'LOW_RELIABILITY_META_INVERSION',
         'Full 252-row universe but meta-model trained on in-sample predictions '
         '→ inverts ordering (ADRI 73-83% base → 16% final). Probabilities unreliable.'),
        ('2026-05-23', '2026-05-26',
         'lgbm_v3_582tree',
         'ACCEPTABLE',
         '582-tree model (covariate shift fixed), but meta-model still broken. '
         'Rankings directionally meaningful but absolute probs may be off.'),
        ('2026-05-27', '9999-12-31',
         'lgbm_v3_weighted_ensemble',
         'OK',
         'Weighted-average ensemble (0.40×LGBM + 0.25×XGB + 0.20×RF + 0.15×ET) '
         'replaces broken meta-model. Distribution mismatch resolved 2026-05-27.'),
    ]

    for date_from, date_to, model_ver, flag, reason in reliability_schedule:
        count_row = conn.execute(
            "SELECT COUNT(*) FROM explosion_predictions "
            "WHERE pred_date BETWEEN ? AND ?",
            (date_from, date_to)
        ).fetchone()
        n = count_row[0]
        log(f"  {date_from} → {date_to}: {n} rows → {flag} ({model_ver})")
        if not DRY_RUN and n > 0:
            conn.execute(
                "UPDATE explosion_predictions "
                "SET model_version=?, reliability_flag=? "
                "WHERE pred_date BETWEEN ? AND ?",
                (model_ver, flag, date_from, date_to)
            )

    if not DRY_RUN:
        conn.commit()
        log("Fix B committed.")
    else:
        log("Fix B: DRY RUN — no changes written.")


# ═══════════════════════════════════════════════════════════════════════════════
# Fix D: forward_test_predictions table
# ═══════════════════════════════════════════════════════════════════════════════

FORWARD_TEST_DDL = """
CREATE TABLE IF NOT EXISTS forward_test_predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Prediction metadata
    symbol              TEXT    NOT NULL,
    pred_date           TEXT    NOT NULL,           -- date prediction was made (YYYY-MM-DD)
    ensemble_prob       REAL    NOT NULL,           -- explosion_prob at prediction time
    confidence_tier     TEXT    NOT NULL,           -- HIGH / MEDIUM / LOW
    regime_at_pred      TEXT,                       -- market regime when prediction was made
    model_version       TEXT,                       -- model checkpoint used

    -- Entry reference (close price on pred_date)
    entry_price         REAL,                       -- close on pred_date (filled post-hoc)

    -- Forward-test outcomes (filled 5 and 10 trading days later)
    close_5d            REAL,                       -- close 5 trading days after pred_date
    close_10d           REAL,                       -- close 10 trading days after pred_date
    return_5d           REAL,                       -- (close_5d / entry_price - 1) * 100
    return_10d          REAL,                       -- (close_10d / entry_price - 1) * 100
    outcome_5d          TEXT,                       -- UP / DOWN / FLAT  (>+3% / <-3% / else)
    outcome_10d         TEXT,

    -- Signal quality checks
    hit_t1              INTEGER DEFAULT 0,          -- 1 if price reached +5% within 10d
    hit_sl              INTEGER DEFAULT 0,          -- 1 if price dropped -7% within 10d
    max_gain_10d        REAL,                       -- max(close_i / entry_price - 1)*100 over 10d
    max_drawdown_10d    REAL,                       -- min(close_i / entry_price - 1)*100 over 10d

    -- Status tracking
    status              TEXT    DEFAULT 'PENDING',  -- PENDING / FILLED_5D / FILLED_10D / EXPIRED
    notes               TEXT,
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now')),

    UNIQUE(symbol, pred_date)
);
"""

FORWARD_TEST_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ftp_symbol_date
    ON forward_test_predictions(symbol, pred_date);
CREATE INDEX IF NOT EXISTS idx_ftp_status
    ON forward_test_predictions(status);
CREATE INDEX IF NOT EXISTS idx_ftp_pred_date
    ON forward_test_predictions(pred_date);
"""


def apply_fix_d(conn: sqlite3.Connection):
    """Create forward_test_predictions table and seed with current top predictions."""
    log("=== Fix D: Forward test table ===")

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    if 'forward_test_predictions' not in tables:
        log("Creating forward_test_predictions table...")
        if not DRY_RUN:
            conn.executescript(FORWARD_TEST_DDL + FORWARD_TEST_INDEX)
            conn.commit()
    else:
        log("Table forward_test_predictions already exists — checking for schema updates...")
        # Check for new columns (upgrades)
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(forward_test_predictions)"
        ).fetchall()]
        # BUG-06 FIX: use correct SQLite type per column — TEXT for string fields,
        # REAL for numeric. Original code used REAL for all, corrupting TEXT semantics.
        needed_with_types = {
            'regime_at_pred':   'TEXT',
            'model_version':    'TEXT',
            'max_gain_10d':     'REAL',
            'max_drawdown_10d': 'REAL',
        }
        for col, col_type in needed_with_types.items():
            if col not in cols:
                log(f"  Adding column: {col} ({col_type})")
                if not DRY_RUN:
                    conn.execute(f"ALTER TABLE forward_test_predictions ADD COLUMN {col} {col_type}")
        if not DRY_RUN:
            conn.commit()

    # ── Seed with current top-15 predictions ─────────────────────────────────
    log("Seeding forward test with today's top predictions...")
    today = datetime.date.today().isoformat()

    # Get current market regime
    regime_row = conn.execute(
        "SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    current_regime = regime_row[0] if regime_row else 'UNKNOWN'

    # Top predictions for today from explosion_predictions
    top_preds = conn.execute(
        """
        SELECT ep.symbol, ep.pred_date, ep.explosion_prob, ep.confidence_tier,
               ep.model_version
        FROM   explosion_predictions ep
        WHERE  ep.pred_date = ?
          AND  ep.explosion_prob >= 0.35
        ORDER  BY ep.explosion_prob DESC
        LIMIT  15
        """,
        (today,)
    ).fetchall()

    if not top_preds:
        # Fallback: latest date
        latest = conn.execute(
            "SELECT MAX(pred_date) FROM explosion_predictions"
        ).fetchone()[0]
        if latest:
            top_preds = conn.execute(
                """
                SELECT ep.symbol, ep.pred_date, ep.explosion_prob, ep.confidence_tier,
                       ep.model_version
                FROM   explosion_predictions ep
                WHERE  ep.pred_date = ?
                  AND  ep.explosion_prob >= 0.35
                ORDER  BY ep.explosion_prob DESC
                LIMIT  15
                """,
                (latest,)
            ).fetchall()

    # Entry prices from ohlcv_history_execution
    inserted = 0
    skipped  = 0
    for row in top_preds:
        sym, pred_date, prob, tier, model_ver = row

        # Get entry price (latest close for this symbol)
        price_row = conn.execute(
            "SELECT close FROM ohlcv_history_execution WHERE symbol=? ORDER BY bar_time DESC LIMIT 1",
            (sym,)
        ).fetchone()
        entry_price = price_row[0] if price_row else None

        ep_str = f"{entry_price:.2f}" if entry_price else "N/A"
        log(f"  → {sym:8s} {pred_date} prob={prob:.3f} tier={tier:6s} entry={ep_str}")

        if not DRY_RUN:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO forward_test_predictions
                      (symbol, pred_date, ensemble_prob, confidence_tier,
                       regime_at_pred, model_version, entry_price, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (sym, pred_date, prob, tier, current_regime,
                     model_ver or 'lgbm_v3_weighted_ensemble', entry_price)
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

    if not DRY_RUN:
        conn.commit()
        log(f"Forward test seeded: {inserted} inserted, {skipped} already existed.")
    else:
        log(f"DRY RUN: would seed {len(top_preds)} rows into forward_test_predictions.")


# ═══════════════════════════════════════════════════════════════════════════════
# Update forward test outcomes (run daily)
# ═══════════════════════════════════════════════════════════════════════════════

def _trading_days_since(pred_date_str: str) -> int:
    """
    Count EGX trading days elapsed since pred_date_str (exclusive of pred_date,
    inclusive of today up to the last trading day).
    Uses event_calendar if available; falls back to calendar-day count / 1.4 estimate.
    """
    try:
        import importlib.util as _ilu, os as _os
        _ec_path = _os.path.join(_os.path.dirname(__file__), 'event_calendar.py')
        _ec_spec = _ilu.spec_from_file_location('event_calendar', _ec_path)
        _ec_mod  = _ilu.module_from_spec(_ec_spec)
        _ec_spec.loader.exec_module(_ec_mod)
        return _ec_mod.staleness_trading_days(pred_date_str)
    except Exception:
        # Rough fallback: calendar days × 5/7 (EGX trades 5 days/week)
        today = datetime.date.today()
        pred  = datetime.date.fromisoformat(pred_date_str)
        cal_days = (today - pred).days
        return int(cal_days * 5 / 7)


def update_forward_test_outcomes(conn: sqlite3.Connection):
    """
    Fill in close_5d / close_10d for PENDING predictions where enough time has passed.
    Uses EGX TRADING days (not calendar days) to correctly handle holidays/weekends.
    Call this daily from night_lab.py to track real-world lift.
    """
    log("=== Updating forward test outcomes ===")
    today = datetime.date.today()

    pending = conn.execute(
        "SELECT id, symbol, pred_date, entry_price FROM forward_test_predictions "
        "WHERE status='PENDING' OR status='FILLED_5D'"
    ).fetchall()

    updated = 0
    for row in pending:
        fid, sym, pred_date_str, entry_price = row
        if not entry_price or entry_price <= 0:
            continue

        trading_days_elapsed = _trading_days_since(pred_date_str)
        if trading_days_elapsed < 5:
            continue  # Not yet 5 trading days — too early

        # Get close prices after pred_date
        closes = conn.execute(
            """
            SELECT date(bar_time, 'unixepoch') AS d, close
            FROM   ohlcv_history_execution
            WHERE  symbol=?
              AND  date(bar_time, 'unixepoch') > ?
            ORDER  BY bar_time ASC
            LIMIT  15
            """,
            (sym, pred_date_str)
        ).fetchall()

        if not closes:
            continue

        close_prices = [r[1] for r in closes if r[1] and r[1] > 0]
        if not close_prices:
            continue

        close_5d  = close_prices[4]  if len(close_prices) >= 5  else None
        close_10d = close_prices[9]  if len(close_prices) >= 10 else None
        max_gain  = max((p / entry_price - 1) * 100 for p in close_prices) if close_prices else None
        max_dd    = min((p / entry_price - 1) * 100 for p in close_prices) if close_prices else None

        def outcome(close_val):
            if close_val is None:
                return None
            ret = (close_val / entry_price - 1) * 100
            if ret > 3.0:
                return 'UP'
            elif ret < -3.0:
                return 'DOWN'
            return 'FLAT'

        status = 'FILLED_10D' if close_10d else ('FILLED_5D' if close_5d else 'PENDING')
        r5d    = (close_5d / entry_price - 1) * 100   if close_5d  and entry_price else None
        r10d   = (close_10d / entry_price - 1) * 100  if close_10d and entry_price else None
        hit_t1 = 1 if max_gain and max_gain >= 5.0  else 0
        hit_sl = 1 if max_dd  and max_dd  <= -7.0  else 0

        if not DRY_RUN:
            conn.execute(
                """
                UPDATE forward_test_predictions SET
                  close_5d=?, close_10d=?, return_5d=?, return_10d=?,
                  outcome_5d=?, outcome_10d=?,
                  hit_t1=?, hit_sl=?,
                  max_gain_10d=?, max_drawdown_10d=?,
                  status=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (close_5d, close_10d, r5d, r10d,
                 outcome(close_5d), outcome(close_10d),
                 hit_t1, hit_sl, max_gain, max_dd, status, fid)
            )
            updated += 1
            r5d_str  = f"{r5d:+.1f}%"  if r5d  is not None else "pend"
            r10d_str = f"{r10d:+.1f}%" if r10d is not None else "pend"
            log(f"  Updated {sym} {pred_date_str}: 5d={r5d_str} 10d={r10d_str} → {status}")

    if not DRY_RUN:
        conn.commit()
        log(f"Updated {updated} forward test outcomes.")
    else:
        log(f"DRY RUN: would update {updated} outcomes.")


# ═══════════════════════════════════════════════════════════════════════════════
# Lift report
# ═══════════════════════════════════════════════════════════════════════════════

def print_lift_report(conn: sqlite3.Connection):
    """Print a compact lift report for all resolved forward test predictions."""
    print("\n" + "=" * 70)
    print("FORWARD TEST LIFT REPORT")
    print("=" * 70)

    # Overall stats
    stats = conn.execute("""
        SELECT
          COUNT(*) total,
          SUM(CASE WHEN status='FILLED_10D' THEN 1 ELSE 0 END) resolved,
          SUM(CASE WHEN outcome_5d='UP'  THEN 1 ELSE 0 END) up_5d,
          SUM(CASE WHEN outcome_5d='DOWN' THEN 1 ELSE 0 END) dn_5d,
          AVG(return_5d)  avg_ret_5d,
          AVG(return_10d) avg_ret_10d,
          SUM(hit_t1) total_t1,
          SUM(hit_sl) total_sl
        FROM forward_test_predictions
        WHERE status IN ('FILLED_5D','FILLED_10D')
    """).fetchone()

    if stats and stats[0]:
        total, resolved, up5, dn5, avg5, avg10, t1, sl = stats
        print(f"Total tracked:  {total}")
        print(f"Resolved (10d): {resolved}")
        print(f"Outcome 5d:     UP={up5}  DOWN={dn5}  "
              f"({up5/total*100:.0f}% win rate)")
        print(f"Avg return 5d:  {avg5:+.2f if avg5 else 0:.2f}%")
        print(f"Avg return 10d: {avg10:+.2f if avg10 else 0:.2f}%")
        print(f"Hit T1 (+5%):   {t1} ({t1/total*100:.0f}%)")
        print(f"Hit SL (-7%):   {sl} ({sl/total*100:.0f}%)")
    else:
        print("No resolved predictions yet — check back in 5-10 trading days.")

    # By confidence tier
    print("\nBy confidence tier:")
    tier_stats = conn.execute("""
        SELECT confidence_tier,
               COUNT(*) n,
               AVG(return_5d) avg5,
               SUM(CASE WHEN outcome_5d='UP' THEN 1 ELSE 0 END) up_count
        FROM forward_test_predictions
        WHERE status IN ('FILLED_5D','FILLED_10D')
        GROUP BY confidence_tier
        ORDER BY avg5 DESC
    """).fetchall()
    for t in tier_stats:
        print(f"  {t[0]:6s}: n={t[1]:3d}  avg5={t[2]:+.2f if t[2] else 0:.2f}%  "
              f"up={t[3]}/{t[1]}")

    # Pending predictions
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM forward_test_predictions WHERE status='PENDING'"
    ).fetchone()[0]
    print(f"\nPending (not yet resolved): {pending_count}")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log(f"DB: {DB_PATH}")
    log(f"DRY RUN: {DRY_RUN}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        apply_fix_b(conn)
        apply_fix_d(conn)

        # Also update any forward test outcomes that are now resolvable
        update_forward_test_outcomes(conn)

        # Print lift report
        print_lift_report(conn)

    finally:
        conn.close()

    log("All fixes applied successfully.")
