#!/usr/bin/env python3
"""
portfolio_tracker.py — Real-time EGX Portfolio Tracker
=======================================================
Tracks open/closed positions with live P&L, T1/T2/T3 + SL hit detection,
automatic Telegram alerts on target hits, and daily portfolio snapshots.

Features:
  • Add positions manually or auto-import from gate-passed signals
  • Daily price update from ohlcv_history (authoritative price source)
  • T1 / T2 / T3 / Stop-Loss hit detection with one-time Telegram alerts
  • Unrealized & realized P&L per position and portfolio-wide
  • Daily snapshot table for equity curve tracking
  • Pillow-based portfolio summary card for Telegram

Usage (CLI):
  python3 portfolio_tracker.py add     SYMBOL ENTRY_PRICE SHARES [--sl SL] [--t1 T1] [--t2 T2]
  python3 portfolio_tracker.py update          # pull latest prices + detect hits
  python3 portfolio_tracker.py close   ID [--price PRICE] [--reason REASON]
  python3 portfolio_tracker.py status          # print all open positions
  python3 portfolio_tracker.py summary         # print portfolio summary JSON
  python3 portfolio_tracker.py daily           # full daily pipeline (update+alerts+snapshot)
  python3 portfolio_tracker.py import_signals  # import today's gate-passed signals as suggested positions
  python3 portfolio_tracker.py card            # generate + save portfolio card

Integration:
  Called by night_lab.py at end of daily pipeline.
  from portfolio_tracker import daily_update, get_portfolio_summary, build_portfolio_card
"""

import os
import sys
import json
import sqlite3
import datetime
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE    = Path(__file__).parent
DB_PATH  = str(_HERE.parent.parent / "data" / "egx_trading.db")
OUT_DIR  = str(_HERE.parent.parent / "data" / "cards")

# ── Pillow (optional — needed only for card generation) ───────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_get_display
    ARABIC_OK = True
except ImportError:
    ARABIC_OK = False

# ── Telegram (optional — needed only for alerts) ──────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


# ══════════════════════════════════════════════════════════════════════════════
# Database schema
# ══════════════════════════════════════════════════════════════════════════════

_DDL_POSITIONS = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol               TEXT NOT NULL,
    entry_date           TEXT NOT NULL,            -- YYYY-MM-DD
    entry_price          REAL NOT NULL,
    shares               REAL NOT NULL DEFAULT 0,
    position_egp         REAL NOT NULL DEFAULT 0,  -- entry_price × shares
    -- Risk / targets (set at entry)
    stop_loss            REAL,
    t1_target            REAL,
    t2_target            REAL,
    t3_target            REAL,
    -- Signal linkage
    signal_date          TEXT,
    ml_score_at_entry    REAL,
    regime_at_entry      TEXT,
    signal_type          TEXT,    -- SWING / SCALP / INVESTMENT / BEAR_EXCEPTION
    -- Live tracking (updated daily)
    current_price        REAL,
    current_pnl_pct      REAL,    -- (current - entry) / entry × 100
    current_pnl_egp      REAL,    -- unrealized EGP P&L
    max_price            REAL,    -- highest close since entry (for trailing)
    max_gain_pct         REAL,    -- peak unrealized gain %
    max_drawdown_pct     REAL,    -- worst intra-trade drawdown from peak %
    -- Target hit tracking
    hit_t1               INTEGER DEFAULT 0,
    hit_t1_date          TEXT,
    hit_t1_price         REAL,
    hit_t2               INTEGER DEFAULT 0,
    hit_t2_date          TEXT,
    hit_t2_price         REAL,
    hit_t3               INTEGER DEFAULT 0,
    hit_t3_date          TEXT,
    hit_t3_price         REAL,
    hit_sl               INTEGER DEFAULT 0,
    hit_sl_date          TEXT,
    hit_sl_price         REAL,
    -- Alert tracking (each alert sent once only)
    t1_alert_sent        INTEGER DEFAULT 0,
    t2_alert_sent        INTEGER DEFAULT 0,
    t3_alert_sent        INTEGER DEFAULT 0,
    sl_alert_sent        INTEGER DEFAULT 0,
    -- Status
    status               TEXT DEFAULT 'OPEN',
    -- OPEN / PARTIAL_T1 / PARTIAL_T2 / CLOSED_T1 / CLOSED_T2 / CLOSED_T3
    -- STOPPED / MANUAL_CLOSE / EXPIRED
    exit_date            TEXT,
    exit_price           REAL,
    exit_reason          TEXT,
    realized_pnl_egp     REAL,
    realized_pnl_pct     REAL,
    hold_days            INTEGER,
    -- Meta
    source               TEXT DEFAULT 'manual',   -- manual / signal_auto / signal_suggested
    notes                TEXT,
    created_at           TEXT DEFAULT (datetime('now')),
    updated_at           TEXT DEFAULT (datetime('now'))
);
"""

_DDL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS portfolio_daily_snapshot (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date            TEXT NOT NULL UNIQUE,
    n_open                   INTEGER DEFAULT 0,
    n_closed_today           INTEGER DEFAULT 0,
    n_total_closed           INTEGER DEFAULT 0,
    total_invested_egp       REAL DEFAULT 0,
    total_unrealized_pnl_egp REAL DEFAULT 0,
    total_realized_pnl_egp   REAL DEFAULT 0,
    portfolio_return_pct     REAL DEFAULT 0,     -- unrealized / invested
    win_rate                 REAL DEFAULT 0,     -- closed trades only
    avg_win_pct              REAL DEFAULT 0,
    avg_loss_pct             REAL DEFAULT 0,
    profit_factor            REAL DEFAULT 0,
    best_open_symbol         TEXT,
    best_open_pnl_pct        REAL DEFAULT 0,
    worst_open_symbol        TEXT,
    worst_open_pnl_pct       REAL DEFAULT 0,
    n_t1_hits_today          INTEGER DEFAULT 0,
    n_sl_hits_today          INTEGER DEFAULT 0,
    created_at               TEXT DEFAULT (datetime('now'))
);
"""

_DDL_ALERTS = """
CREATE TABLE IF NOT EXISTS portfolio_alerts_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    alert_type   TEXT NOT NULL,   -- T1_HIT / T2_HIT / T3_HIT / SL_HIT / NOTE
    price        REAL,
    pnl_pct      REAL,
    message      TEXT,
    sent_at      TEXT DEFAULT (datetime('now')),
    telegram_ok  INTEGER DEFAULT 0
);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_POSITIONS + _DDL_SNAPSHOTS + _DDL_ALERTS)
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Position management
# ══════════════════════════════════════════════════════════════════════════════

def add_position(
    conn: sqlite3.Connection,
    symbol: str,
    entry_price: float,
    shares: float,
    entry_date: str = None,
    stop_loss: float = None,
    t1_target: float = None,
    t2_target: float = None,
    t3_target: float = None,
    signal_date: str = None,
    ml_score: float = None,
    regime: str = None,
    signal_type: str = "SWING",
    source: str = "manual",
    notes: str = None,
) -> int:
    """
    Add a new position. Returns the new position ID.
    Derives T1/T2/T3/SL from signal if not provided explicitly.
    """
    ensure_tables(conn)
    entry_date = entry_date or datetime.date.today().isoformat()
    position_egp = round(entry_price * shares, 2)

    # Auto-derive targets from unified_signals if not provided
    if (t1_target is None or stop_loss is None) and signal_date:
        sig_row = conn.execute(
            """SELECT t1_target, t2_target, stop_loss, unified_score, active_regime
               FROM unified_signals
               WHERE symbol=? AND signal_date=?
               ORDER BY unified_score DESC LIMIT 1""",
            (symbol, signal_date)
        ).fetchone()
        if sig_row:
            t1_target  = t1_target  or (sig_row["t1_target"]  if sig_row["t1_target"]  else None)
            t2_target  = t2_target  or (sig_row["t2_target"]  if sig_row["t2_target"]  else None)
            stop_loss  = stop_loss  or (sig_row["stop_loss"]  if sig_row["stop_loss"]  else None)
            ml_score   = ml_score   or (sig_row["unified_score"] if sig_row["unified_score"] else None)
            regime     = regime     or (sig_row["active_regime"] if sig_row["active_regime"] else None)

    # Derive T3 = T2 + (T2 - entry) if not provided
    if t3_target is None and t2_target and entry_price:
        t3_target = round(t2_target + (t2_target - entry_price), 2)

    cur = conn.execute(
        """INSERT OR IGNORE INTO portfolio_positions
           (symbol, entry_date, entry_price, shares, position_egp,
            stop_loss, t1_target, t2_target, t3_target,
            signal_date, ml_score_at_entry, regime_at_entry, signal_type,
            current_price, max_price, source, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, entry_date, entry_price, shares, position_egp,
         stop_loss, t1_target, t2_target, t3_target,
         signal_date, ml_score, regime, signal_type,
         entry_price, entry_price,  # current = max = entry at start
         source, notes)
    )
    conn.commit()
    row_id = cur.lastrowid or conn.execute(
        "SELECT id FROM portfolio_positions WHERE symbol=? AND entry_date=? AND entry_price=?",
        (symbol, entry_date, entry_price)
    ).fetchone()["id"]
    print(f"[portfolio] Added position: {symbol} @ {entry_price:.2f} × {shares} = {position_egp:,.0f} EGP  (id={row_id})")
    return row_id


def close_position(
    conn: sqlite3.Connection,
    position_id: int,
    exit_price: float = None,
    exit_reason: str = "MANUAL_CLOSE",
    exit_date: str = None,
) -> Dict:
    """Close an open position, calculate realized P&L."""
    ensure_tables(conn)
    exit_date = exit_date or datetime.date.today().isoformat()

    pos = conn.execute(
        "SELECT * FROM portfolio_positions WHERE id=?", (position_id,)
    ).fetchone()
    if not pos:
        return {"error": f"Position {position_id} not found"}
    if pos["status"] not in ("OPEN", "PARTIAL_T1", "PARTIAL_T2"):
        return {"error": f"Position {position_id} is already {pos['status']}"}

    # Use current_price if exit_price not given
    exit_price = exit_price or pos["current_price"] or pos["entry_price"]

    entry_price   = pos["entry_price"]
    shares        = pos["shares"]
    pnl_pct       = (exit_price - entry_price) / entry_price * 100
    pnl_egp       = (exit_price - entry_price) * shares
    entry_date    = pos["entry_date"]
    try:
        hold_days = (datetime.date.fromisoformat(exit_date) -
                     datetime.date.fromisoformat(entry_date)).days
    except Exception:
        hold_days = 0

    conn.execute(
        """UPDATE portfolio_positions
           SET status=?, exit_date=?, exit_price=?, exit_reason=?,
               realized_pnl_egp=?, realized_pnl_pct=?, hold_days=?,
               current_price=?, current_pnl_pct=?, current_pnl_egp=?,
               updated_at=datetime('now')
           WHERE id=?""",
        (exit_reason, exit_date, exit_price, exit_reason,
         round(pnl_egp, 2), round(pnl_pct, 2), hold_days,
         exit_price, round(pnl_pct, 2), round(pnl_egp, 2),
         position_id)
    )
    conn.commit()
    result = {"symbol": pos["symbol"], "pnl_pct": round(pnl_pct, 2),
              "pnl_egp": round(pnl_egp, 2), "hold_days": hold_days, "reason": exit_reason}
    print(f"[portfolio] Closed #{position_id} {pos['symbol']}: {pnl_pct:+.1f}% ({pnl_egp:+,.0f} EGP) | {exit_reason}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Daily price update + hit detection
# ══════════════════════════════════════════════════════════════════════════════

def update_prices(conn: sqlite3.Connection) -> List[Dict]:
    """
    Pull latest close from ohlcv_history for every OPEN position.
    Returns list of updated position summaries.
    """
    ensure_tables(conn)
    open_pos = conn.execute(
        """SELECT id, symbol, entry_price, shares, position_egp,
                  max_price, t1_target, t2_target, t3_target, stop_loss,
                  hit_t1, hit_t2, hit_t3, hit_sl, status
           FROM portfolio_positions
           WHERE status IN ('OPEN','PARTIAL_T1','PARTIAL_T2')"""
    ).fetchall()

    updated = []
    for p in open_pos:
        sym = p["symbol"]
        # Latest close from ohlcv_history (authoritative table)
        price_row = conn.execute(
            "SELECT close, date(bar_time,'unixepoch') AS bar_date "
            "FROM ohlcv_history WHERE symbol=? AND close>0 "
            "ORDER BY bar_time DESC LIMIT 1",
            (sym,)
        ).fetchone()
        if not price_row or not price_row["close"]:
            continue

        current = float(price_row["close"])
        entry   = float(p["entry_price"])
        shares  = float(p["shares"])
        pnl_pct = (current - entry) / entry * 100
        pnl_egp = (current - entry) * shares

        # Update max_price (for trailing stop / max gain tracking)
        new_max   = max(float(p["max_price"] or entry), current)
        max_gain  = (new_max - entry) / entry * 100
        max_dd    = (current - new_max) / new_max * 100  # negative = drawdown

        conn.execute(
            """UPDATE portfolio_positions
               SET current_price=?, current_pnl_pct=?, current_pnl_egp=?,
                   max_price=?, max_gain_pct=?, max_drawdown_pct=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (current, round(pnl_pct, 2), round(pnl_egp, 2),
             new_max, round(max_gain, 2), round(max_dd, 2),
             p["id"])
        )
        updated.append({
            "id": p["id"], "symbol": sym,
            "current": current, "pnl_pct": round(pnl_pct, 2),
            "pnl_egp": round(pnl_egp, 2),
        })

    conn.commit()
    return updated


def detect_target_hits(conn: sqlite3.Connection) -> List[Dict]:
    """
    Check all open positions for T1/T2/T3 and SL hits.
    Returns list of alerts to send (not yet sent).
    """
    ensure_tables(conn)
    today = datetime.date.today().isoformat()
    open_pos = conn.execute(
        """SELECT id, symbol, entry_price, shares, current_price,
                  t1_target, t2_target, t3_target, stop_loss,
                  hit_t1, hit_t2, hit_t3, hit_sl,
                  t1_alert_sent, t2_alert_sent, t3_alert_sent, sl_alert_sent,
                  status
           FROM portfolio_positions
           WHERE status IN ('OPEN','PARTIAL_T1','PARTIAL_T2')"""
    ).fetchall()

    alerts = []

    for p in open_pos:
        sym       = p["symbol"]
        cur       = float(p["current_price"] or p["entry_price"])
        entry     = float(p["entry_price"])
        pnl_pct   = (cur - entry) / entry * 100

        # T1 hit
        if p["t1_target"] and not p["hit_t1"]:
            if cur >= float(p["t1_target"]):
                conn.execute(
                    """UPDATE portfolio_positions
                       SET hit_t1=1, hit_t1_date=?, hit_t1_price=?,
                           status='PARTIAL_T1', updated_at=datetime('now')
                       WHERE id=?""",
                    (today, cur, p["id"])
                )
                if not p["t1_alert_sent"]:
                    alerts.append({
                        "id": p["id"], "symbol": sym, "type": "T1_HIT",
                        "price": cur, "target": p["t1_target"], "pnl_pct": round(pnl_pct, 2),
                        "msg": f"🎯 {sym} وصل الهدف 1 (+{pnl_pct:.1f}%) @ {cur:.2f}"
                    })
                    conn.execute(
                        "UPDATE portfolio_positions SET t1_alert_sent=1 WHERE id=?", (p["id"],)
                    )

        # T2 hit
        if p["t2_target"] and p["hit_t1"] and not p["hit_t2"]:
            if cur >= float(p["t2_target"]):
                conn.execute(
                    """UPDATE portfolio_positions
                       SET hit_t2=1, hit_t2_date=?, hit_t2_price=?,
                           status='PARTIAL_T2', updated_at=datetime('now')
                       WHERE id=?""",
                    (today, cur, p["id"])
                )
                if not p["t2_alert_sent"]:
                    alerts.append({
                        "id": p["id"], "symbol": sym, "type": "T2_HIT",
                        "price": cur, "target": p["t2_target"], "pnl_pct": round(pnl_pct, 2),
                        "msg": f"🚀 {sym} وصل الهدف 2 (+{pnl_pct:.1f}%) @ {cur:.2f}"
                    })
                    conn.execute(
                        "UPDATE portfolio_positions SET t2_alert_sent=1 WHERE id=?", (p["id"],)
                    )

        # T3 hit
        if p["t3_target"] and p["hit_t2"] and not p["hit_t3"]:
            if cur >= float(p["t3_target"]):
                conn.execute(
                    """UPDATE portfolio_positions
                       SET hit_t3=1, hit_t3_date=?, hit_t3_price=?,
                           status='CLOSED_T3', exit_date=?, exit_price=?,
                           exit_reason='T3_HIT', updated_at=datetime('now')
                       WHERE id=?""",
                    (today, cur, today, cur, p["id"])
                )
                if not p["t3_alert_sent"]:
                    alerts.append({
                        "id": p["id"], "symbol": sym, "type": "T3_HIT",
                        "price": cur, "target": p["t3_target"], "pnl_pct": round(pnl_pct, 2),
                        "msg": f"💎 {sym} وصل الهدف 3 — صفقة رائعة! (+{pnl_pct:.1f}%) @ {cur:.2f}"
                    })
                    conn.execute(
                        "UPDATE portfolio_positions SET t3_alert_sent=1 WHERE id=?", (p["id"],)
                    )

        # SL hit (only if T1 NOT yet hit — once T1 hit, SL should be trailed up)
        if p["stop_loss"] and not p["hit_sl"] and not p["hit_t1"]:
            if cur <= float(p["stop_loss"]):
                conn.execute(
                    """UPDATE portfolio_positions
                       SET hit_sl=1, hit_sl_date=?, hit_sl_price=?,
                           status='STOPPED', exit_date=?, exit_price=?,
                           exit_reason='SL_HIT', updated_at=datetime('now')
                       WHERE id=?""",
                    (today, cur, today, cur, p["id"])
                )
                # Calculate realized P&L
                pnl_egp = (cur - entry) * float(p["shares"])
                conn.execute(
                    """UPDATE portfolio_positions
                       SET realized_pnl_egp=?, realized_pnl_pct=?
                       WHERE id=?""",
                    (round(pnl_egp, 2), round(pnl_pct, 2), p["id"])
                )
                if not p["sl_alert_sent"]:
                    alerts.append({
                        "id": p["id"], "symbol": sym, "type": "SL_HIT",
                        "price": cur, "target": p["stop_loss"], "pnl_pct": round(pnl_pct, 2),
                        "msg": f"🛑 {sym} لمس وقف الخسارة ({pnl_pct:.1f}%) @ {cur:.2f}"
                    })
                    conn.execute(
                        "UPDATE portfolio_positions SET sl_alert_sent=1 WHERE id=?", (p["id"],)
                    )

    conn.commit()
    return alerts


def _log_alert(conn: sqlite3.Connection, position_id: int, symbol: str,
               alert_type: str, price: float, pnl_pct: float,
               message: str, telegram_ok: bool) -> None:
    """Write to portfolio_alerts_log table."""
    conn.execute(
        """INSERT INTO portfolio_alerts_log
           (position_id, symbol, alert_type, price, pnl_pct, message, telegram_ok)
           VALUES (?,?,?,?,?,?,?)""",
        (position_id, symbol, alert_type, price, pnl_pct, message, int(telegram_ok))
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Telegram alerts
# ══════════════════════════════════════════════════════════════════════════════

def send_alert(message: str) -> bool:
    """Send a text alert via Telegram Bot API."""
    if os.environ.get("EGX_INTERNAL_TELEGRAM_OK") != "1":
        print("[portfolio] Telegram alert blocked by delivery policy")
        return False
    if not REQUESTS_OK or not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[portfolio] Telegram alert error: {e}")
        return False


def send_target_alerts(conn: sqlite3.Connection, alerts: List[Dict]) -> int:
    """
    Send all pending target hit alerts via Telegram.
    Returns count of successfully sent alerts.
    """
    sent = 0
    for alert in alerts:
        ok = send_alert(alert["msg"])
        _log_alert(
            conn, alert["id"], alert["symbol"], alert["type"],
            alert["price"], alert["pnl_pct"], alert["msg"], ok
        )
        if ok:
            sent += 1
            print(f"[portfolio] 📱 Alert sent: {alert['msg']}")
        else:
            print(f"[portfolio] ⚠️ Alert failed: {alert['msg']}")
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio summary
# ══════════════════════════════════════════════════════════════════════════════

def get_portfolio_summary(conn: sqlite3.Connection) -> Dict:
    """
    Return complete portfolio summary dict.
    Used by Telegram card generator and night_lab reporting.
    """
    ensure_tables(conn)
    today = datetime.date.today().isoformat()

    # Open positions
    open_rows = conn.execute(
        """SELECT symbol, entry_date, entry_price, shares, position_egp,
                  current_price, current_pnl_pct, current_pnl_egp,
                  t1_target, stop_loss, hit_t1, hit_t2, signal_type, status,
                  max_gain_pct, max_drawdown_pct
           FROM portfolio_positions
           WHERE status IN ('OPEN','PARTIAL_T1','PARTIAL_T2')
           ORDER BY current_pnl_pct DESC"""
    ).fetchall()

    # Closed positions (all time)
    closed_rows = conn.execute(
        """SELECT realized_pnl_pct, realized_pnl_egp, exit_reason
           FROM portfolio_positions
           WHERE status NOT IN ('OPEN','PARTIAL_T1','PARTIAL_T2')
             AND realized_pnl_pct IS NOT NULL"""
    ).fetchall()

    # Open P&L
    total_invested   = sum(float(r["position_egp"] or 0) for r in open_rows)
    total_unreal_egp = sum(float(r["current_pnl_egp"] or 0) for r in open_rows)
    port_return_pct  = (total_unreal_egp / total_invested * 100) if total_invested > 0 else 0.0

    # Closed stats
    wins   = [float(r["realized_pnl_pct"]) for r in closed_rows if (r["realized_pnl_pct"] or 0) > 0]
    losses = [float(r["realized_pnl_pct"]) for r in closed_rows if (r["realized_pnl_pct"] or 0) <= 0]
    n_closed     = len(closed_rows)
    win_rate     = len(wins) / n_closed * 100 if n_closed > 0 else 0.0
    avg_win_pct  = sum(wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(losses) / len(losses) if losses else 0.0
    total_real_egp = sum(float(r["realized_pnl_egp"] or 0) for r in closed_rows)

    gross_wins  = sum(w for w in wins) if wins else 0
    gross_losses = abs(sum(l for l in losses)) if losses else 0
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (99.0 if wins else 0.0)

    # Best / worst open
    open_list = [dict(r) for r in open_rows]
    best  = max(open_list, key=lambda x: x.get("current_pnl_pct") or -999) if open_list else None
    worst = min(open_list, key=lambda x: x.get("current_pnl_pct") or 999)  if open_list else None

    return {
        "report_date":         today,
        "n_open":              len(open_rows),
        "n_closed":            n_closed,
        "total_invested_egp":  round(total_invested, 0),
        "total_unrealized_egp": round(total_unreal_egp, 0),
        "total_realized_egp":  round(total_real_egp, 0),
        "portfolio_return_pct": round(port_return_pct, 2),
        "win_rate":            round(win_rate, 1),
        "avg_win_pct":         round(avg_win_pct, 2),
        "avg_loss_pct":        round(avg_loss_pct, 2),
        "profit_factor":       round(profit_factor, 2),
        "best_symbol":         best["symbol"] if best else None,
        "best_pnl_pct":        best["current_pnl_pct"] if best else 0,
        "worst_symbol":        worst["symbol"] if worst else None,
        "worst_pnl_pct":       worst["current_pnl_pct"] if worst else 0,
        "open_positions":      open_list,
    }


def take_daily_snapshot(conn: sqlite3.Connection, summary: Dict = None) -> None:
    """Write today's portfolio snapshot to portfolio_daily_snapshot."""
    ensure_tables(conn)
    today = datetime.date.today().isoformat()
    s = summary or get_portfolio_summary(conn)

    # Count T1/SL hits today
    n_t1_today = conn.execute(
        "SELECT COUNT(*) FROM portfolio_positions WHERE hit_t1=1 AND hit_t1_date=?", (today,)
    ).fetchone()[0]
    n_sl_today = conn.execute(
        "SELECT COUNT(*) FROM portfolio_positions WHERE hit_sl=1 AND hit_sl_date=?", (today,)
    ).fetchone()[0]
    n_closed_today = conn.execute(
        "SELECT COUNT(*) FROM portfolio_positions WHERE exit_date=?", (today,)
    ).fetchone()[0]

    conn.execute(
        """INSERT OR REPLACE INTO portfolio_daily_snapshot
           (snapshot_date, n_open, n_closed_today, n_total_closed,
            total_invested_egp, total_unrealized_pnl_egp, total_realized_pnl_egp,
            portfolio_return_pct, win_rate, avg_win_pct, avg_loss_pct, profit_factor,
            best_open_symbol, best_open_pnl_pct, worst_open_symbol, worst_open_pnl_pct,
            n_t1_hits_today, n_sl_hits_today)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (today,
         s["n_open"], n_closed_today, s["n_closed"],
         s["total_invested_egp"], s["total_unrealized_egp"], s["total_realized_egp"],
         s["portfolio_return_pct"], s["win_rate"], s["avg_win_pct"], s["avg_loss_pct"],
         s["profit_factor"],
         s["best_symbol"], s["best_pnl_pct"], s["worst_symbol"], s["worst_pnl_pct"],
         n_t1_today, n_sl_today)
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Signal import — auto-import gate-passed signals as suggested positions
# ══════════════════════════════════════════════════════════════════════════════

def import_gate_passed_signals(conn: sqlite3.Connection, date: str = None,
                                dry_run: bool = False) -> List[Dict]:
    """
    Import today's gate-passed signals from unified_signals as
    'signal_suggested' positions (status = OPEN but source = signal_suggested).
    These are NOT actual trades — they are suggestions that the user can review.

    Does NOT duplicate: skips symbols already in portfolio_positions for same date.
    """
    ensure_tables(conn)
    date = date or datetime.date.today().isoformat()

    # Get gate-passed signals, excluding UNIT_ERROR symbols
    signals = conn.execute(
        """SELECT us.symbol, us.entry_price, us.entry_high, us.stop_loss,
                  us.t1_target, us.t2_target, us.unified_score, us.active_regime,
                  us.behavioral_class, us.signal_date
           FROM unified_signals us
           LEFT JOIN data_quality_flags dq
               ON us.symbol = dq.symbol AND dq.issue_type = 'UNIT_ERROR'
           WHERE us.quality_gate_passed = 1
             AND us.signal_date = ?
             AND dq.id IS NULL
           ORDER BY us.unified_score DESC
           LIMIT 5""",
        (date,)
    ).fetchall()

    # Fallback to latest gate-passed date
    if not signals:
        latest = conn.execute(
            "SELECT MAX(signal_date) FROM unified_signals WHERE quality_gate_passed=1"
        ).fetchone()[0]
        if latest:
            signals = conn.execute(
                """SELECT us.symbol, us.entry_price, us.entry_high, us.stop_loss,
                          us.t1_target, us.t2_target, us.unified_score, us.active_regime,
                          us.behavioral_class, us.signal_date
                   FROM unified_signals us
                   LEFT JOIN data_quality_flags dq
                       ON us.symbol = dq.symbol AND dq.issue_type = 'UNIT_ERROR'
                   WHERE us.quality_gate_passed = 1
                     AND us.signal_date = ?
                     AND dq.id IS NULL
                   ORDER BY us.unified_score DESC LIMIT 5""",
                (latest,)
            ).fetchall()
            date = latest

    imported = []
    for sig in signals:
        sym = sig["symbol"]
        # Skip if already in portfolio for this date
        exists = conn.execute(
            "SELECT id FROM portfolio_positions WHERE symbol=? AND signal_date=?",
            (sym, date)
        ).fetchone()
        if exists:
            continue

        entry_price = float(sig["entry_price"] or 0)
        if entry_price <= 0:
            # Try current price from ohlcv_history
            pr = conn.execute(
                "SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1",
                (sym,)
            ).fetchone()
            entry_price = float(pr[0]) if pr else 0
        if entry_price <= 0:
            continue

        # Default position size: 10,000 EGP (symbolic — user should adjust)
        shares = round(10_000 / entry_price, 0)
        if shares < 1:
            shares = 1

        if not dry_run:
            pos_id = add_position(
                conn, sym, entry_price, shares,
                entry_date=date,
                stop_loss=sig["stop_loss"],
                t1_target=sig["t1_target"],
                t2_target=sig["t2_target"],
                signal_date=date,
                ml_score=sig["unified_score"],
                regime=sig["active_regime"],
                signal_type="SWING",
                source="signal_suggested",
                notes=f"Auto-imported from gate-passed signals | UES={sig['unified_score']:.0f}"
            )
            imported.append({"id": pos_id, "symbol": sym, "entry": entry_price})
        else:
            imported.append({"symbol": sym, "entry": entry_price, "dry_run": True})
            print(f"[portfolio] DRY RUN — would import: {sym} @ {entry_price:.2f}")

    return imported


# ══════════════════════════════════════════════════════════════════════════════
# Visual card (Pillow)
# ══════════════════════════════════════════════════════════════════════════════

def _ar(text: str) -> str:
    """Reshape + bidi Arabic for Pillow rendering."""
    if not text:
        return text
    # Strip emoji
    import unicodedata
    clean = "".join(c for c in text if (
        0x0600 <= ord(c) <= 0x06FF or 0xFB50 <= ord(c) <= 0xFDFF or ord(c) < 0x2000
        or unicodedata.category(c).startswith(('L', 'N', 'P', 'Z'))
    )).strip()
    if ARABIC_OK:
        try:
            return bidi_get_display(arabic_reshaper.reshape(clean))
        except Exception:
            pass
    return clean


def _find_font(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


_FONT_BOLD = _find_font([
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
])
_FONT_REG = _find_font([
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
])


def build_portfolio_card(summary: Dict) -> Optional[bytes]:
    """
    Build a Pillow-based portfolio summary card.
    Returns PNG bytes or None if Pillow unavailable.
    """
    if not PILLOW_OK:
        return None

    n_open      = summary.get("n_open", 0)
    positions   = summary.get("open_positions", [])
    port_ret    = summary.get("portfolio_return_pct", 0)
    invested    = summary.get("total_invested_egp", 0)
    unreal_egp  = summary.get("total_unrealized_egp", 0)
    win_rate    = summary.get("win_rate", 0)
    pf          = summary.get("profit_factor", 0)
    report_date = summary.get("report_date", "")

    W = 900
    # Dynamic height based on number of positions
    rows       = min(n_open, 6)
    ROW_H      = 42
    H          = max(300, 220 + rows * ROW_H + 40)

    img  = Image.new("RGB", (W, H), (14, 17, 23))
    draw = ImageDraw.Draw(img)

    def _font(size):
        try:
            return ImageFont.truetype(_FONT_BOLD, size) if _FONT_BOLD else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    def _font_reg(size):
        try:
            return ImageFont.truetype(_FONT_REG or _FONT_BOLD, size)
        except Exception:
            return ImageFont.load_default()

    def _rrect(box, r=14, fill=None, outline=None, width=1):
        draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

    def _txt(text, x, y, font, color):
        draw.text((x, y), text, font=font, fill=color)

    # Colors
    GREEN  = (34, 197, 94)
    RED    = (239, 68, 68)
    GOLD   = (251, 191, 36)
    BLUE   = (96, 165, 250)
    DIMMED = (100, 108, 130)
    WHITE  = (240, 242, 248)
    PANEL  = (28, 33, 45)

    # ── Header ────────────────────────────────────────────────────────────────
    header_color = GREEN if port_ret >= 0 else RED
    _rrect((16, 16, W - 16, 88), r=16, fill=(20, 24, 33))
    draw.line([(16, 88), (W - 16, 88)], fill=header_color, width=2)

    # Title
    f_title = _font(22)
    title   = f"محفظة EGX — {report_date}"
    try:
        bbox = draw.textbbox((0, 0), title, font=f_title)
        tw   = bbox[2] - bbox[0]
    except Exception:
        tw = W // 2
    draw.text(((W - tw) // 2, 26), title, font=f_title, fill=WHITE)

    # P&L indicator
    ret_str = f"{port_ret:+.2f}%"
    f_ret   = _font(20)
    ret_col = GREEN if port_ret >= 0 else RED
    try:
        bbox = draw.textbbox((0, 0), ret_str, font=f_ret)
        rw   = bbox[2] - bbox[0]
    except Exception:
        rw = 80
    draw.text(((W - rw) // 2, 55), ret_str, font=f_ret, fill=ret_col)

    # ── Stats row ─────────────────────────────────────────────────────────────
    sy   = 100
    f_v  = _font(20)
    f_l  = _font_reg(12)

    stats = [
        ("مفتوحة", str(n_open),                  BLUE),
        ("مستثمر",  f"{invested/1000:.0f}K",     WHITE),
        ("ربح",     f"{unreal_egp/1000:+.1f}K",   GREEN if unreal_egp >= 0 else RED),
        ("نسبة فوز", f"{win_rate:.0f}%",          GOLD),
        ("PF",      f"{pf:.2f}",                  GOLD),
    ]

    cw = (W - 32) // len(stats)
    for i, (lbl, val, col) in enumerate(stats):
        cx = 16 + i * cw
        _rrect((cx + 4, sy, cx + cw - 4, sy + 70), r=12, fill=PANEL)
        # Value
        try:
            bbox = draw.textbbox((0, 0), val, font=f_v)
            vw   = bbox[2] - bbox[0]
        except Exception:
            vw = len(val) * 10
        draw.text((cx + (cw - vw) // 2, sy + 8), val, font=f_v, fill=col)
        # Label (Arabic)
        lbl_r = _ar(lbl)
        try:
            bbox = draw.textbbox((0, 0), lbl_r, font=f_l)
            lw   = bbox[2] - bbox[0]
        except Exception:
            lw = len(lbl_r) * 7
        draw.text((cx + (cw - lw) // 2, sy + 42), lbl_r, font=f_l, fill=DIMMED)

    # ── Positions table ────────────────────────────────────────────────────────
    ty     = sy + 82
    f_row  = _font(14)
    f_lbl  = _font_reg(12)

    if positions:
        # Header row
        headers = ["الرمز", "دخول", "حالي", "ر/خ %", "هدف1", "وقف"]
        col_xs  = [20, 140, 260, 380, 500, 640, 780]
        for j, hdr in enumerate(headers):
            draw.text((col_xs[j], ty), _ar(hdr), font=f_lbl, fill=DIMMED)
        ty += 20
        draw.line([(16, ty), (W - 16, ty)], fill=(40, 46, 60), width=1)
        ty += 6

        for pos in positions[:6]:
            sym     = pos.get("symbol", "?")
            entry   = float(pos.get("entry_price") or 0)
            cur     = float(pos.get("current_price") or entry)
            pnl_p   = float(pos.get("current_pnl_pct") or 0)
            t1      = float(pos.get("t1_target") or 0)
            sl      = float(pos.get("stop_loss") or 0)
            pnl_col = GREEN if pnl_p >= 0 else RED
            hit_t1  = pos.get("hit_t1", 0)

            # Row background
            bg_col = (20, 38, 26) if pnl_p >= 0 else (38, 20, 20)
            _rrect((16, ty - 2, W - 16, ty + ROW_H - 8), r=8, fill=bg_col)

            vals = [
                (sym,            WHITE),
                (f"{entry:.2f}", DIMMED),
                (f"{cur:.2f}",   WHITE),
                (f"{pnl_p:+.1f}%", pnl_col),
                (f"{t1:.2f}" if t1 else "—", GOLD if hit_t1 else DIMMED),
                (f"{sl:.2f}" if sl else "—", RED),
            ]
            for j, (val, col) in enumerate(vals):
                draw.text((col_xs[j] + 4, ty + 2), val, font=f_row, fill=col)

            ty += ROW_H
    else:
        msg = _ar("لا توجد مراكز مفتوحة")
        f_msg = _font_reg(16)
        try:
            bbox = draw.textbbox((0, 0), msg, font=f_msg)
            mw   = bbox[2] - bbox[0]
        except Exception:
            mw = 200
        draw.text(((W - mw) // 2, ty + 10), msg, font=f_msg, fill=DIMMED)

    # ── Footer ─────────────────────────────────────────────────────────────────
    footer = _ar("بيانات للمعلومات فقط • ليس توصية استثمارية")
    f_foot = _font_reg(11)
    try:
        bbox = draw.textbbox((0, 0), footer, font=f_foot)
        fw   = bbox[2] - bbox[0]
    except Exception:
        fw = 300
    draw.text(((W - fw) // 2, H - 20), footer, font=f_foot, fill=DIMMED)

    # Encode
    buf = __import__('io').BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save_card(image_bytes: bytes, name: str) -> str:
    """Save card PNG to data/cards/ directory. Returns file path."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"portfolio_{name}.png")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# Main daily pipeline (called by night_lab.py)
# ══════════════════════════════════════════════════════════════════════════════

def daily_update(conn: sqlite3.Connection = None,
                 send_telegram: bool = True) -> Dict:
    """
    Full daily Portfolio Tracker pipeline:
      1. Update prices from ohlcv_history
      2. Detect T1/T2/T3/SL hits
      3. Send Telegram alerts for hits
      4. Take daily snapshot
      5. Return summary dict for reporting

    Called by night_lab.py at end of pipeline.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    ensure_tables(conn)

    result = {"status": "ok", "updated": 0, "alerts": 0, "snapshot": False}

    try:
        # 1. Update prices
        updated = update_prices(conn)
        result["updated"] = len(updated)
        print(f"[portfolio] Prices updated: {len(updated)} open positions")

        # 2. Detect hits
        alerts = detect_target_hits(conn)
        result["alerts"] = len(alerts)
        if alerts:
            print(f"[portfolio] ⚡ {len(alerts)} target hit(s) detected!")

        # 3. Telegram alerts
        if send_telegram and alerts:
            n_sent = send_target_alerts(conn, alerts)
            result["alerts_sent"] = n_sent

        # 4. Summary + snapshot
        summary = get_portfolio_summary(conn)
        take_daily_snapshot(conn, summary)
        result["snapshot"] = True
        result["summary"] = summary
        result["n_open"]   = summary["n_open"]
        result["port_return_pct"] = summary["portfolio_return_pct"]

        print(f"[portfolio] Daily update complete: open={summary['n_open']} "
              f"return={summary['portfolio_return_pct']:+.2f}% "
              f"invested={summary['total_invested_egp']:,.0f} EGP")

    except Exception as e:
        result["status"] = f"error: {e}"
        traceback.print_exc()
    finally:
        if own_conn:
            conn.close()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Telegram caption for portfolio card
# ══════════════════════════════════════════════════════════════════════════════

def portfolio_telegram_caption(summary: Dict) -> str:
    """Build Arabic Telegram caption for portfolio card."""
    n_open     = summary.get("n_open", 0)
    port_ret   = summary.get("portfolio_return_pct", 0) or 0
    invested   = summary.get("total_invested_egp", 0) or 0
    unreal_egp = summary.get("total_unrealized_egp", 0) or 0
    win_rate   = summary.get("win_rate", 0) or 0
    pf         = summary.get("profit_factor", 0) or 0
    best_sym   = summary.get("best_symbol")
    best_pct   = summary.get("best_pnl_pct", 0) or 0
    worst_sym  = summary.get("worst_symbol")
    worst_pct  = summary.get("worst_pnl_pct", 0) or 0
    rdate      = summary.get("report_date", "")

    ret_emoji  = "📈" if port_ret >= 0 else "📉"
    pnl_color_open = "+" if unreal_egp >= 0 else ""

    lines = [
        f"💼 <b>المحفظة — {rdate}</b>",
        f"{ret_emoji} العائد الكلي: <b>{port_ret:+.2f}%</b>  |  {pnl_color_open}{unreal_egp:,.0f} جنيه",
        f"📊 مراكز مفتوحة: <b>{n_open}</b>  |  مستثمر: <b>{invested/1000:.0f}K EGP</b>",
    ]
    if win_rate > 0:
        lines.append(f"✅ نسبة الفوز: <b>{win_rate:.0f}%</b>  |  معامل الربح: <b>{pf:.2f}</b>")
    if best_sym:
        lines.append(f"🌟 الأفضل: <code>{best_sym}</code> <b>{best_pct:+.1f}%</b>")
    if worst_sym and worst_sym != best_sym:
        lines.append(f"⚠️ الأضعف: <code>{worst_sym}</code> <b>{worst_pct:+.1f}%</b>")
    lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_open(conn: sqlite3.Connection) -> None:
    """Pretty-print all open positions."""
    rows = conn.execute(
        """SELECT id, symbol, entry_date, entry_price, shares, position_egp,
                  current_price, current_pnl_pct, t1_target, stop_loss, status
           FROM portfolio_positions
           WHERE status IN ('OPEN','PARTIAL_T1','PARTIAL_T2')
           ORDER BY current_pnl_pct DESC"""
    ).fetchall()
    if not rows:
        print("No open positions.")
        return
    print(f"\n{'ID':>4}  {'SYM':>6}  {'DATE':>10}  {'ENTRY':>8}  {'CUR':>8}  "
          f"{'P/L%':>7}  {'T1':>8}  {'SL':>8}  {'STATUS'}")
    print("-" * 90)
    for r in rows:
        pnl = r['current_pnl_pct'] or 0
        print(f"{r['id']:>4}  {r['symbol']:>6}  {r['entry_date']:>10}  "
              f"{r['entry_price']:>8.2f}  {r['current_price'] or r['entry_price']:>8.2f}  "
              f"{pnl:>+7.1f}%  {r['t1_target'] or 0:>8.2f}  {r['stop_loss'] or 0:>8.2f}  "
              f"{r['status']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="EGX Portfolio Tracker")
    sub = parser.add_subparsers(dest="cmd")

    # add
    p_add = sub.add_parser("add")
    p_add.add_argument("symbol")
    p_add.add_argument("entry_price", type=float)
    p_add.add_argument("shares", type=float)
    p_add.add_argument("--sl",    type=float, dest="stop_loss",  default=None)
    p_add.add_argument("--t1",    type=float, dest="t1_target",  default=None)
    p_add.add_argument("--t2",    type=float, dest="t2_target",  default=None)
    p_add.add_argument("--t3",    type=float, dest="t3_target",  default=None)
    p_add.add_argument("--date",  default=None)
    p_add.add_argument("--type",  default="SWING")
    p_add.add_argument("--notes", default=None)

    # close
    p_close = sub.add_parser("close")
    p_close.add_argument("id", type=int)
    p_close.add_argument("--price",  type=float, default=None)
    p_close.add_argument("--reason", default="MANUAL_CLOSE")

    # Other commands
    sub.add_parser("update")
    sub.add_parser("status")
    sub.add_parser("summary")
    sub.add_parser("daily")
    sub.add_parser("card")
    p_imp = sub.add_parser("import_signals")
    p_imp.add_argument("--date", default=None)
    p_imp.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    conn = get_db()
    ensure_tables(conn)

    if args.cmd == "add":
        add_position(conn, args.symbol, args.entry_price, args.shares,
                     entry_date=args.date, stop_loss=args.stop_loss,
                     t1_target=args.t1_target, t2_target=args.t2_target,
                     t3_target=args.t3_target, signal_type=args.type, notes=args.notes)

    elif args.cmd == "close":
        result = close_position(conn, args.id, args.price, args.reason)
        print(json.dumps(result, default=str))

    elif args.cmd == "update":
        updated = update_prices(conn)
        hits = detect_target_hits(conn)
        print(f"Updated {len(updated)} positions, {len(hits)} target hit(s)")
        for h in hits:
            print(f"  → {h['msg']}")

    elif args.cmd == "status":
        _print_open(conn)

    elif args.cmd == "summary":
        s = get_portfolio_summary(conn)
        print(json.dumps({k: v for k, v in s.items() if k != "open_positions"},
                         default=str, ensure_ascii=False, indent=2))
        _print_open(conn)

    elif args.cmd == "daily":
        result = daily_update(
            conn,
            send_telegram=os.environ.get("EGX_INTERNAL_TELEGRAM_OK") == "1",
        )
        print(json.dumps(result, default=str, ensure_ascii=False))

    elif args.cmd == "card":
        s = get_portfolio_summary(conn)
        img = build_portfolio_card(s)
        if img:
            p = save_card(img, datetime.date.today().isoformat())
            print(f"Card saved: {p}")
        else:
            print("Pillow not available")

    elif args.cmd == "import_signals":
        imported = import_gate_passed_signals(conn, args.date, args.dry_run)
        print(f"Imported {len(imported)} positions:")
        for p in imported:
            print(f"  {p}")

    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()
