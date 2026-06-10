#!/usr/bin/env python3
"""
chart_visualizer.py — Phase 60: EGX Chart Visualizer
═════════════════════════════════════════════════════
Provides drawing specifications for fetch_chart_drawings.mjs to render on
TradingView charts, and manages screenshot logging for daily visual reports.

Commands:
  get_draw_specs       → drawing commands for one symbol
  get_top_picks_draws  → drawing commands for top-N picks
  log_screenshot       → record a saved screenshot
  finalize_report      → assemble daily_visual_report for a date
  list_screenshots     → list all screenshots for a date
  report_summary       → last N days of visual reports
  build_full           → convenience wrapper: top picks + instructions
"""

import os
import sys
import json
import sqlite3
import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')


# ── DB helpers ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chart_screenshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            report_date     TEXT    NOT NULL,
            screenshot_path TEXT,
            draw_specs      TEXT,
            scan_score      REAL,
            setup_type      TEXT,
            entry_low       REAL,
            entry_high      REAL,
            stop_loss       REAL,
            t1              REAL,
            t2              REAL,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_visual_report (
            report_date      TEXT PRIMARY KEY,
            n_picks          INTEGER,
            top_picks        TEXT,
            screenshots_json TEXT,
            summary          TEXT,
            created_at       TEXT
        );
    """)
    conn.commit()


# ── Draw-spec builder ───────────────────────────────────────────────────────

def _build_draw_specs(row):
    """Turn a scans row into a list of draw commands for the JS script."""
    draws = []

    entry_low  = row['entry_low']
    entry_high = row['entry_high']
    stop_loss  = row['stop_loss']
    t1         = row['t1']
    t2         = row['t2']

    if entry_low is not None and entry_high is not None:
        draws.append({
            "type":       "rectangle",
            "label":      "Entry Zone",
            "price_high": entry_high,
            "price_low":  entry_low,
            "color":      "#00AA00",
            "opacity":    30
        })

    if stop_loss is not None:
        draws.append({
            "type":  "horizontal_line",
            "label": "Stop Loss",
            "price": stop_loss,
            "color": "#FF0000",
            "style": "dashed"
        })

    if t1 is not None:
        draws.append({
            "type":  "horizontal_line",
            "label": "Target 1",
            "price": t1,
            "color": "#0066FF",
            "style": "solid"
        })

    if t2 is not None:
        draws.append({
            "type":  "horizontal_line",
            "label": "Target 2",
            "price": t2,
            "color": "#0044BB",
            "style": "dashed"
        })

    return draws


def _row_to_draw_spec(row):
    """Convert a scans row to the full draw-spec dict."""
    return {
        "symbol":     row['symbol'],
        "scan_score": row['score'],
        "setup_type": row['setup_type'],
        "entry_low":  row['entry_low'],
        "entry_high": row['entry_high'],
        "stop_loss":  row['stop_loss'],
        "t1":         row['t1'],
        "t2":         row['t2'],
        "draws":      _build_draw_specs(row)
    }


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_get_draw_specs(params):
    """Return draw commands for a single symbol on a given scan_date."""
    symbol    = params.get('symbol', '').upper().strip()
    scan_date = params.get('scan_date', str(datetime.date.today()))

    if not symbol:
        return {"error": "symbol is required"}

    conn = get_db()
    _ensure_tables(conn)

    row = conn.execute("""
        SELECT symbol, scan_date, setup_type, score, grade, priority,
               entry_low, entry_high, stop_loss, t1, t2, rr1, rr2,
               close_price, confidence, rejected, is_best_safe, is_best_aggressive
        FROM scans
        WHERE symbol = ? AND scan_date = ? AND rejected = 0
        ORDER BY score DESC
        LIMIT 1
    """, (symbol, scan_date)).fetchone()

    if not row:
        return {"error": f"No scan found for {symbol} on {scan_date}"}

    return _row_to_draw_spec(row)


def cmd_get_top_picks_draws(params):
    """Return draw specs for top-N non-rejected picks on a given scan_date."""
    scan_date = params.get('scan_date', str(datetime.date.today()))
    n         = int(params.get('n', 10))
    min_score = float(params.get('min_score', 65))

    conn = get_db()
    _ensure_tables(conn)

    rows = conn.execute("""
        SELECT symbol, scan_date, setup_type, score, grade, priority,
               entry_low, entry_high, stop_loss, t1, t2, rr1, rr2,
               close_price, confidence, rejected, is_best_safe, is_best_aggressive
        FROM scans
        WHERE scan_date = ? AND rejected = 0 AND score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (scan_date, min_score, n)).fetchall()

    picks = [{"symbol": r['symbol'], "draw_specs": _row_to_draw_spec(r)} for r in rows]

    return {
        "scan_date": scan_date,
        "n_picks":   len(picks),
        "picks":     picks
    }


def cmd_log_screenshot(params):
    """Save a screenshot record to chart_screenshots."""
    symbol          = params.get('symbol', '').upper().strip()
    report_date     = params.get('report_date', str(datetime.date.today()))
    screenshot_path = params.get('screenshot_path')
    scan_score      = params.get('scan_score')
    setup_type      = params.get('setup_type')
    draw_specs      = params.get('draw_specs')      # optional raw spec dict
    entry_low       = params.get('entry_low')
    entry_high      = params.get('entry_high')
    stop_loss       = params.get('stop_loss')
    t1              = params.get('t1')
    t2              = params.get('t2')
    created_at      = datetime.datetime.utcnow().isoformat()

    if not symbol:
        return {"error": "symbol is required"}

    draw_specs_json = json.dumps(draw_specs) if draw_specs is not None else None

    conn = get_db()
    _ensure_tables(conn)

    cur = conn.execute("""
        INSERT INTO chart_screenshots
            (symbol, report_date, screenshot_path, draw_specs, scan_score,
             setup_type, entry_low, entry_high, stop_loss, t1, t2, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, report_date, screenshot_path, draw_specs_json, scan_score,
          setup_type, entry_low, entry_high, stop_loss, t1, t2, created_at))
    conn.commit()

    return {
        "id":              cur.lastrowid,
        "symbol":          symbol,
        "report_date":     report_date,
        "screenshot_path": screenshot_path,
        "scan_score":      scan_score,
        "setup_type":      setup_type,
        "created_at":      created_at
    }


def cmd_finalize_report(params):
    """Assemble daily_visual_report for a report_date from logged screenshots."""
    report_date = params.get('report_date', str(datetime.date.today()))

    conn = get_db()
    _ensure_tables(conn)

    rows = conn.execute("""
        SELECT symbol, screenshot_path, scan_score, setup_type
        FROM chart_screenshots
        WHERE report_date = ?
        ORDER BY scan_score DESC
    """, (report_date,)).fetchall()

    top_picks       = [r['symbol'] for r in rows]
    screenshots     = [r['screenshot_path'] for r in rows if r['screenshot_path']]
    top_picks_json  = json.dumps(top_picks)
    screenshots_json = json.dumps(screenshots)
    n_screenshots   = len(rows)

    summary_parts = [f"{r['symbol']} ({r['scan_score']:.0f})" for r in rows]
    summary = f"Visual report for {report_date}: {n_screenshots} charts. " \
              f"Top picks: {', '.join(summary_parts[:5])}" if summary_parts else \
              f"Visual report for {report_date}: no screenshots."

    created_at = datetime.datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO daily_visual_report
            (report_date, n_picks, top_picks, screenshots_json, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET
            n_picks          = excluded.n_picks,
            top_picks        = excluded.top_picks,
            screenshots_json = excluded.screenshots_json,
            summary          = excluded.summary,
            created_at       = excluded.created_at
    """, (report_date, n_screenshots, top_picks_json, screenshots_json, summary, created_at))
    conn.commit()

    return {
        "report_date":    report_date,
        "n_screenshots":  n_screenshots,
        "top_picks":      top_picks,
        "screenshots":    screenshots,
        "summary":        summary
    }


def cmd_list_screenshots(params):
    """List all screenshots for a given date (default: today)."""
    date = params.get('date') or str(datetime.date.today())

    conn = get_db()
    _ensure_tables(conn)

    rows = conn.execute("""
        SELECT id, symbol, report_date, screenshot_path, scan_score, setup_type,
               entry_low, entry_high, stop_loss, t1, t2, created_at
        FROM chart_screenshots
        WHERE report_date = ?
        ORDER BY scan_score DESC
    """, (date,)).fetchall()

    screenshots = [dict(r) for r in rows]
    return {
        "date":         date,
        "n_screenshots": len(screenshots),
        "screenshots":  screenshots
    }


def cmd_report_summary(params):
    """Return last N days of daily_visual_report records."""
    days = int(params.get('days', 7))

    conn = get_db()
    _ensure_tables(conn)

    rows = conn.execute("""
        SELECT report_date, n_picks, top_picks, summary
        FROM daily_visual_report
        ORDER BY report_date DESC
        LIMIT ?
    """, (days,)).fetchall()

    reports = []
    for r in rows:
        top_picks = []
        if r['top_picks']:
            try:
                top_picks = json.loads(r['top_picks'])
            except (ValueError, TypeError):
                top_picks = []
        reports.append({
            "date":      r['report_date'],
            "n_picks":   r['n_picks'],
            "top_picks": top_picks,
            "summary":   r['summary']
        })

    return {"days": days, "reports": reports}


def cmd_build_full(params):
    """Convenience: fetch top picks + return instructions for JS renderer."""
    scan_date = params.get('scan_date', str(datetime.date.today()))
    n_picks   = int(params.get('n_picks', 8))

    result = cmd_get_top_picks_draws({
        "scan_date": scan_date,
        "n":         n_picks,
        "min_score": params.get('min_score', 65)
    })

    return {
        "report_date":   scan_date,
        "n_picks":       result.get('n_picks', 0),
        "picks_to_draw": result.get('picks', []),
        "instructions":  "Run fetch_chart_drawings.mjs to render these specs on TradingView"
    }


# ── Command registry ────────────────────────────────────────────────────────

COMMANDS = {
    'get_draw_specs':      cmd_get_draw_specs,
    'get_top_picks_draws': cmd_get_top_picks_draws,
    'log_screenshot':      cmd_log_screenshot,
    'finalize_report':     cmd_finalize_report,
    'list_screenshots':    cmd_list_screenshots,
    'report_summary':      cmd_report_summary,
    'build_full':          cmd_build_full,
}

# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    fn = COMMANDS.get(command)
    if not fn:
        print(json.dumps({'error': f'Unknown command: {command}', 'available': list(COMMANDS)}))
        sys.exit(1)

    try:
        result = fn(params)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()[-800:]}))
        sys.exit(1)
