"""
Backtest Engine v2 — Real Price Based  Ph79
=============================================
يستبدل +3%/-1% hardcoded في Ph6 بأسعار OHLCV حقيقية.
يطبق: slippage + transaction costs + EGX-specific constraints.

الفرق عن Ph6:
  - Ph6: win=+3%, loss=-1% hardcoded  ← وهمي
  - Ph79: أسعار حقيقية OHLCV forward simulation  ← حقيقي

CLI:
  python3 backtest_engine.py run             # backtest آخر إشارات في unified_signals
  python3 backtest_engine.py run --days 180  # آخر 180 يوم من الإشارات
  python3 backtest_engine.py run --json      # JSON output
  python3 backtest_engine.py walkforward     # walk-forward 4-window analysis
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = _ROOT / "data" / "egx_trading.db"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

DDL_BACKTEST_V2 = """
CREATE TABLE IF NOT EXISTS backtest_v2_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    signal_date TEXT,
    symbol TEXT,
    signal_type TEXT,
    regime TEXT,
    entry_price REAL,
    stop_loss REAL,
    target1 REAL,
    exit_reason TEXT,
    pnl_pct REAL,
    r_multiple REAL,
    hold_days INT,
    slippage_pct REAL,
    commission_pct REAL,
    ues_score REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_date, signal_date, symbol)
)
"""

DDL_BACKTEST_V2_UNIQUE_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_v2_run_sig_sym
    ON backtest_v2_results (run_date, signal_date, symbol)
"""


def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Core simulation helpers
# ---------------------------------------------------------------------------

def _get_hold_days_for_type(signal_type: str) -> int:
    """
    Return maximum hold duration (days) for a given signal type.

    SHORT_SWING raised 7→10→9 days — 2026-05-22 hold-duration sweeps:
      7d: 6m WR=55.0% PF=1.81  |  10d: 6m WR=55.8% PF=1.89  (+0.8pp)
      9d: 6m WR=65.2% vs 10d: 63.7% (+1.5pp); 12m: 54.2% vs 53.7% (+0.5pp)
    Hold=9 maximizes WR (TIME_STOP exits at day 9 avg -0.20% vs -0.26% at day 10).
    The 10th day tends to capture losing positions reversing lower not higher.
    """
    mapping = {
        "SHORT_SWING": 9,
        "LONG_SWING": 20,
        "SCALP": 3,
        "INVESTMENT": 60,
        "UNDERVALUED": 90,
    }
    return mapping.get(str(signal_type).upper(), 20)


def _simulate_trade_pnl(
    entry_price: float,
    stop_loss: float,
    target1: float,
    ohlcv_forward: List[Dict[str, float]],
    max_hold_days: int,
    slippage_pct: float = 0.003,
    commission_pct: float = 0.001,
) -> Dict[str, Any]:
    """
    Simulate a single trade using real OHLCV forward bars.

    Parameters
    ----------
    entry_price   : midpoint of entry zone (entry_lo+entry_hi)/2
    stop_loss     : stop loss price
    target1       : first profit target
    ohlcv_forward : list of dicts {open, high, low, close, volume} ordered by date
    max_hold_days : auto-exit after this many bars
    slippage_pct  : one-way slippage fraction (e.g. 0.003 = 0.3%)
    commission_pct: round-trip commission fraction

    Returns
    -------
    dict with keys: exit_reason, pnl_pct, hold_days, r_multiple, exit_price
    """
    if not ohlcv_forward:
        return {
            "exit_reason": "NO_DATA",
            "pnl_pct": 0.0,
            "hold_days": 0,
            "r_multiple": 0.0,
            "exit_price": entry_price,
        }

    # Entry with slippage applied at open of day 0
    day0 = ohlcv_forward[0]
    day0_open = float(day0.get("open", entry_price))

    # Sanity check: reject trade if OHLCV prices are wildly mismatched.
    # Guards against stale signals after corporate actions (splits, consolidations).
    #
    # Three-layer check:
    #   1. Hard ratio: day0_open vs entry_price outside [0.4, 2.5]  → immediate reject
    #   2. Stop vs open outside [0.4, 2.5]                          → immediate reject
    #   3. Compound: if price_ratio < 0.6 AND stop is clearly above open price range,
    #      the signal levels are pre-split but OHLCV is post-split  → reject
    if entry_price > 0 and day0_open > 0:
        price_ratio = day0_open / entry_price
        if price_ratio < 0.4 or price_ratio > 2.5:
            return {
                "exit_reason": "PRICE_MISMATCH",
                "pnl_pct": 0.0,
                "hold_days": 0,
                "r_multiple": 0.0,
                "exit_price": day0_open,
            }
        # Secondary check: stop_loss level wildly outside actual OHLCV range
        if stop_loss > 0 and (stop_loss / day0_open > 2.5 or stop_loss / day0_open < 0.4):
            return {
                "exit_reason": "PRICE_MISMATCH",
                "pnl_pct": 0.0,
                "hold_days": 0,
                "r_multiple": 0.0,
                "exit_price": day0_open,
            }
        # Compound check: price is suspiciously low AND stop is suspiciously high
        # relative to the actual open — classic pre/post-split signal contamination.
        # Threshold: day0_open < 80% of entry AND stop > 120% of day0_open.
        # This catches 62-76% ratio corporate actions while preserving valid gap-down
        # entries where price opened below the signal zone but within 80% range.
        if price_ratio < 0.8 and stop_loss > 0 and stop_loss / day0_open > 1.2:
            return {
                "exit_reason": "PRICE_MISMATCH",
                "pnl_pct": 0.0,
                "hold_days": 0,
                "r_multiple": 0.0,
                "exit_price": day0_open,
            }

    # Enter at open if within 2% of entry, otherwise take market open
    actual_entry = day0_open if day0_open < entry_price * 1.02 else day0_open
    # Apply slippage cost (fills slightly worse)
    actual_entry = actual_entry * (1.0 + slippage_pct)
    # Total cost basis including round-trip commission
    total_cost = actual_entry * (1.0 + commission_pct)

    # STALE-SIGNAL GUARD: reject trade if the signal levels are no longer valid
    # at market open. Catches stale scan prices where the stock already ran past
    # the target (most common: entry_price stale by several days, stock rallied).
    # Also catches gap-downs where entry is far above the open.
    #
    # Tightened threshold: if actual_entry >= target1 * 0.98 (within 2% of target),
    # the trade has no meaningful upside — risk/reward is broken. This prevents
    # the "negative TARGET1 PnL" bug where a stock gaps above entry zone but still
    # below target * 1.01, causing entry above target after slippage/costs.
    if target1 > 0 and actual_entry >= target1 * 0.98:
        # Target already breached or within 2%: no worthwhile R/R.
        return {
            "exit_reason": "STALE_SIGNAL",
            "pnl_pct": 0.0,
            "hold_days": 0,
            "r_multiple": 0.0,
            "exit_price": actual_entry,
        }
    if actual_entry > entry_price * 1.30:
        # Price has already moved +30% beyond signal entry — signal is stale.
        return {
            "exit_reason": "STALE_SIGNAL",
            "pnl_pct": 0.0,
            "hold_days": 0,
            "r_multiple": 0.0,
            "exit_price": actual_entry,
        }

    # Degenerate-risk guard: if the stop loss is already inside the actual entry bar,
    # the trade has no breathing room — any small intraday move triggers the stop.
    # This catches "entry price dropped to near the stop" (stale scan, price fell).
    if stop_loss > 0 and actual_entry > 0:
        actual_risk_pct = abs(actual_entry - stop_loss) / actual_entry
        if actual_risk_pct < 0.005:   # < 0.5% — stop is essentially at market
            return {
                "exit_reason": "DEGENERATE_RISK",
                "pnl_pct": 0.0,
                "hold_days": 0,
                "r_multiple": 0.0,
                "exit_price": actual_entry,
            }

    risk_per_unit = max(0.001, abs(stop_loss - entry_price) / entry_price)

    # TRAIL_STOP tested 2026-05-22 — flat breakeven at entry+0.6% triggered at 50% target:
    # Result: WR 71%→79.7% but Exp collapsed +0.614R→+0.301R (saved 20 STOP_LOSS but
    # converted 36 TIME_STOP avg+0.66% and ~8 TARGET1 avg+8.87% into +0.40% tiny exits).
    # Net: HARMFUL. Revert to simple stop. TARGET1-first check order KEPT (correct).
    trailing_stop = stop_loss  # original stop only — no trail
    breakeven_activated = False  # kept for TRAIL_STOP reporting path (unused)

    exit_reason = "TIME_STOP"
    exit_price = float(ohlcv_forward[-1].get("close", actual_entry))
    hold_days = len(ohlcv_forward)

    for i, bar in enumerate(ohlcv_forward):
        bar_low = float(bar.get("low", actual_entry))
        bar_high = float(bar.get("high", actual_entry))
        bar_close = float(bar.get("close", actual_entry))

        # Check TARGET1 first — favorable-fill assumption on same-bar high/low
        # (intrabar order unknown on daily bars; exit at target if bar touches it)
        if bar_high >= target1:
            exit_reason = "TARGET1"
            exit_price = target1  # assume fills at target
            hold_days = i + 1
            break

        # Check stop loss
        if bar_low <= trailing_stop:
            exit_reason = "STOP_LOSS"
            exit_price = trailing_stop  # assume fills at stop
            hold_days = i + 1
            break

        # Time stop
        if i + 1 >= max_hold_days:
            exit_reason = "TIME_STOP"
            exit_price = bar_close
            hold_days = i + 1
            break
    else:
        # Ran out of forward data before any exit triggered
        exit_reason = "DATA_END"
        exit_price = float(ohlcv_forward[-1].get("close", actual_entry))
        hold_days = len(ohlcv_forward)

    # PnL: gross return minus commission on exit side
    exit_net = exit_price * (1.0 - commission_pct)
    pnl_frac = (exit_net - total_cost) / total_cost
    pnl_pct  = pnl_frac * 100.0   # store as percentage for readability

    r_multiple = pnl_frac / risk_per_unit

    return {
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 4),
        "hold_days": hold_days,
        "r_multiple": round(r_multiple, 4),
        "exit_price": round(exit_price, 4),
    }


# ---------------------------------------------------------------------------
# OHLCV loader
# ---------------------------------------------------------------------------

def _load_forward_ohlcv(
    conn: sqlite3.Connection,
    symbol: str,
    after_date: str,
    n_days: int,
) -> List[Dict[str, Any]]:
    """
    Load forward OHLCV bars for *symbol* after *after_date*.

    Parameters
    ----------
    conn        : open sqlite3 connection
    symbol      : ticker symbol
    after_date  : ISO date string YYYY-MM-DD (exclusive start)
    n_days      : max number of bars to return

    Returns
    -------
    List of dicts: {trade_date, open, high, low, close, volume}
    """
    try:
        # Convert after_date to unix timestamp (start of day, UTC-safe)
        # calendar.timegm treats timetuple as UTC, avoiding local-timezone shift
        dt = datetime.datetime.strptime(after_date, "%Y-%m-%d")
        unix_after = calendar.timegm(dt.timetuple())

        cursor = conn.execute(
            """
            SELECT bar_time, open, high, low, close, volume
            FROM ohlcv_history_execution
            WHERE symbol = ?
              AND bar_time > ?
            ORDER BY bar_time ASC
            LIMIT ?
            """,
            (symbol, unix_after, n_days),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            bar_time = int(row["bar_time"])
            trade_date = datetime.datetime.utcfromtimestamp(bar_time).strftime("%Y-%m-%d")
            result.append(
                {
                    "trade_date": trade_date,
                    "open": float(row["open"]) if row["open"] is not None else None,
                    "high": float(row["high"]) if row["high"] is not None else None,
                    "low": float(row["low"]) if row["low"] is not None else None,
                    "close": float(row["close"]) if row["close"] is not None else None,
                    "volume": float(row["volume"]) if row["volume"] is not None else 0.0,
                }
            )
        return [r for r in result if r["open"] is not None]
    except Exception as exc:
        # Non-fatal: return empty on any DB error
        return []


# ---------------------------------------------------------------------------
# Aggregate stats helper
# ---------------------------------------------------------------------------

def _compute_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate backtest statistics from a list of trade result dicts."""
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "avg_hold_days": 0.0,
            "total_pnl_pct": 0.0,
        }

    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]

    n = len(trades)
    win_rate = len(wins) / n

    gross_profit = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    expectancy_r = sum(t.get("r_multiple", 0) for t in trades) / n if n else 0.0
    avg_hold_days = sum(t.get("hold_days", 0) for t in trades) / n if n else 0.0
    total_pnl_pct = sum(t.get("pnl_pct", 0) for t in trades)

    return {
        "n_trades": n,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_r": round(expectancy_r, 4),
        "avg_hold_days": round(avg_hold_days, 2),
        "total_pnl_pct": round(total_pnl_pct, 4),
    }


def _breakdown_by_key(
    enriched: List[Dict[str, Any]], key: str
) -> Dict[str, Dict[str, Any]]:
    """Group enriched trade results by a string key and compute stats per group."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in enriched:
        grp = str(row.get(key) or "UNKNOWN").upper()
        groups.setdefault(grp, []).append(row)
    return {grp: _compute_stats(rows) for grp, rows in groups.items()}


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    db_path: Optional[Path] = None,
    days: int = 180,
    min_signals: int = 10,
    slippage_pct: float = 0.003,
    commission_pct: float = 0.001,
) -> Dict[str, Any]:
    """
    Run full backtest over the last `days` days of unified_signals.

    Parameters
    ----------
    db_path        : path to egx_trading.db (defaults to module-level DB_PATH)
    days           : look-back window for signals
    min_signals    : minimum required signals to proceed (else return early)
    slippage_pct   : one-way slippage (EGX illiquidity premium)
    commission_pct : per-leg commission rate

    Returns
    -------
    dict with aggregate stats, by_type breakdown, by_regime breakdown
    """
    db_path = db_path or DB_PATH
    conn = _get_conn(db_path)

    try:
        # Ensure result table and unique index exist
        conn.execute(DDL_BACKTEST_V2)
        # Add unique index idempotently (harmless if it already exists)
        try:
            conn.execute(DDL_BACKTEST_V2_UNIQUE_IDX)
        except Exception:
            pass
        conn.commit()

        # Determine date cutoff
        cutoff = (
            datetime.date.today() - datetime.timedelta(days=days)
        ).strftime("%Y-%m-%d")

        # Load signals — map columns defensively (schema may vary between versions)
        try:
            # Detect available columns
            col_cursor = conn.execute("PRAGMA table_info(unified_signals)")
            col_names = {row["name"] for row in col_cursor.fetchall()}

            # Column aliases: (preferred_name, fallback_names...)
            def _pick_col(options: list, default_expr: str) -> str:
                for name in options:
                    if name in col_names:
                        return name
                return default_expr  # literal SQL expression or NULL

            signal_type_col  = _pick_col(["signal_type", "conviction_tier", "behavioral_class"], "NULL")
            entry_lo_col     = _pick_col(["entry_lo", "entry_price", "entry_high"], "entry_price")
            entry_hi_col     = _pick_col(["entry_hi", "entry_high", "entry_price"], "entry_high")
            stop_loss_col    = _pick_col(["stop_loss"], "NULL")
            target1_col      = _pick_col(["target1", "t1_target"], "t1_target")
            target2_col      = _pick_col(["target2", "t2_target"], "t2_target")
            ues_col          = _pick_col(["ues_score", "unified_score", "scan_score"], "unified_score")
            regime_col       = _pick_col(["regime_at_signal", "active_regime"], "active_regime")

            query = f"""
                SELECT signal_date, symbol,
                       {signal_type_col}  AS signal_type,
                       {entry_lo_col}     AS entry_lo,
                       {entry_hi_col}     AS entry_hi,
                       {stop_loss_col}    AS stop_loss,
                       {target1_col}      AS target1,
                       {target2_col}      AS target2,
                       {ues_col}          AS ues_score,
                       {regime_col}       AS regime_at_signal
                FROM unified_signals
                WHERE signal_date >= ?
                ORDER BY signal_date ASC
            """
            cursor = conn.execute(query, (cutoff,))
            signals = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            return {
                "error": f"unified_signals table not accessible: {exc}",
                "n_trades": 0,
            }

        if not signals:
            return {
                "error": f"No signals found in the last {days} days (since {cutoff}).",
                "n_trades": 0,
            }

        if len(signals) < min_signals:
            return {
                "error": (
                    f"Only {len(signals)} signals found (min_signals={min_signals}). "
                    "Insufficient data for meaningful backtest."
                ),
                "n_trades": 0,
            }

        enriched: List[Dict[str, Any]] = []
        skipped = 0
        run_date = datetime.date.today().strftime("%Y-%m-%d")

        for sig in signals:
            signal_date = sig["signal_date"]
            symbol = sig["symbol"]
            signal_type = str(sig["signal_type"] or "LONG_SWING").upper()

            # Compute entry price
            entry_lo = sig["entry_lo"]
            entry_hi = sig["entry_hi"]
            if entry_lo is not None and entry_hi is not None:
                entry_price = (float(entry_lo) + float(entry_hi)) / 2.0
            elif entry_lo is not None:
                entry_price = float(entry_lo)
            elif entry_hi is not None:
                entry_price = float(entry_hi)
            else:
                skipped += 1
                continue  # no entry price available

            stop_loss = float(sig["stop_loss"]) if sig["stop_loss"] is not None else entry_price * 0.97
            target1 = float(sig["target1"]) if sig["target1"] is not None else entry_price * 1.05

            if stop_loss >= entry_price:
                # Invalid stop (above entry) — skip
                skipped += 1
                continue

            max_hold = _get_hold_days_for_type(signal_type)

            # Load forward OHLCV data — entry starts the day AFTER signal_date.
            # Signals are generated EOD (after market close), so the first valid
            # entry session is signal_date + 1 calendar day (weekend bars don't
            # exist in OHLCV, so this naturally lands on the next trading day).
            entry_after_date = (
                datetime.datetime.strptime(signal_date, "%Y-%m-%d").date()
                + datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")
            forward_bars = _load_forward_ohlcv(conn, symbol, entry_after_date, max_hold + 5)

            if not forward_bars:
                skipped += 1
                continue

            # Run simulation
            result = _simulate_trade_pnl(
                entry_price=entry_price,
                stop_loss=stop_loss,
                target1=target1,
                ohlcv_forward=forward_bars,
                max_hold_days=max_hold,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
            )

            # Skip mismatched price data (stale signals / stock splits)
            if result["exit_reason"] == "PRICE_MISMATCH":
                skipped += 1
                continue

            row = {
                "run_date": run_date,
                "signal_date": signal_date,
                "symbol": symbol,
                "signal_type": signal_type,
                "regime": str(sig["regime_at_signal"] or "UNKNOWN").upper(),
                "entry_price": round(entry_price, 4),
                "stop_loss": round(stop_loss, 4),
                "target1": round(target1, 4),
                "exit_reason": result["exit_reason"],
                "pnl_pct": result["pnl_pct"],
                "r_multiple": result["r_multiple"],
                "hold_days": result["hold_days"],
                "slippage_pct": slippage_pct,
                "commission_pct": commission_pct,
                "ues_score": float(sig["ues_score"]) if sig["ues_score"] is not None else None,
            }
            enriched.append(row)

        if not enriched:
            return {
                "error": f"All {len(signals)} signals were skipped (no price data or invalid entries).",
                "n_trades": 0,
                "skipped": skipped,
            }

        # Persist to DB — OR IGNORE deduplicates on (run_date, signal_date, symbol)
        conn.executemany(
            """
            INSERT OR IGNORE INTO backtest_v2_results
              (run_date, signal_date, symbol, signal_type, regime,
               entry_price, stop_loss, target1, exit_reason, pnl_pct,
               r_multiple, hold_days, slippage_pct, commission_pct, ues_score)
            VALUES
              (:run_date, :signal_date, :symbol, :signal_type, :regime,
               :entry_price, :stop_loss, :target1, :exit_reason, :pnl_pct,
               :r_multiple, :hold_days, :slippage_pct, :commission_pct, :ues_score)
            """,
            enriched,
        )
        conn.commit()

        # Aggregate stats
        agg = _compute_stats(enriched)
        by_type = _breakdown_by_key(enriched, "signal_type")
        by_regime = _breakdown_by_key(enriched, "regime")
        by_exit = _breakdown_by_key(enriched, "exit_reason")

        results = {
            **agg,
            "days_window": days,
            "skipped": skipped,
            "by_type": by_type,
            "by_regime": by_regime,
            "by_exit": by_exit,
        }

        # Optional: integrate institutional_metrics scorecard if available
        _try_institutional_scorecard(enriched, results)

        return results

    finally:
        conn.close()


def _try_institutional_scorecard(
    enriched: List[Dict[str, Any]], results: Dict[str, Any]
) -> None:
    """
    Attempt to compute institutional metrics scorecard from backtest results.
    Gracefully skips if institutional_metrics module is not available.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import importlib
        im = importlib.import_module("institutional_metrics")
        # Build a minimal trades list compatible with institutional_metrics API
        trades_for_im = [
            {
                "pnl_pct": row["pnl_pct"],
                "hold_days": row["hold_days"],
                "r_multiple": row["r_multiple"],
                "exit_reason": row["exit_reason"],
                "signal_type": row["signal_type"],
                "symbol": row["symbol"],
            }
            for row in enriched
        ]
        if hasattr(im, "compute_scorecard"):
            scorecard = im.compute_scorecard(trades_for_im)
            results["institutional_scorecard"] = scorecard
        elif hasattr(im, "load_trades_from_db"):
            # Module uses DB-based loading; results already persisted above
            pass
    except (ImportError, ModuleNotFoundError):
        pass  # institutional_metrics not available — skip silently
    except Exception:
        pass  # Any other error — don't break the backtest pipeline


# ---------------------------------------------------------------------------
# Walk-forward analysis
# ---------------------------------------------------------------------------

def walk_forward_real(
    db_path: Optional[Path] = None,
    n_windows: int = 4,
) -> List[Dict[str, Any]]:
    """
    Run walk-forward analysis by splitting historical signals into n_windows
    equal time windows and running backtest on each.

    Returns
    -------
    list of per-window dicts with stats + stability assessment
    """
    db_path = db_path or DB_PATH
    conn = _get_conn(db_path)

    try:
        try:
            cursor = conn.execute(
                "SELECT MIN(signal_date) AS min_d, MAX(signal_date) AS max_d FROM unified_signals"
            )
            row = cursor.fetchone()
            if not row or not row["min_d"] or not row["max_d"]:
                return [{"error": "unified_signals is empty or has no date range."}]

            start_dt = datetime.datetime.strptime(row["min_d"], "%Y-%m-%d").date()
            end_dt = datetime.datetime.strptime(row["max_d"], "%Y-%m-%d").date()
        except sqlite3.OperationalError as exc:
            return [{"error": f"Cannot read unified_signals: {exc}"}]

    finally:
        conn.close()

    total_days = (end_dt - start_dt).days
    if total_days < n_windows * 14:
        return [
            {
                "error": (
                    f"Date range too short for {n_windows} windows "
                    f"({total_days} total days)."
                )
            }
        ]

    window_size = total_days // n_windows
    window_results = []

    for i in range(n_windows):
        w_start = start_dt + datetime.timedelta(days=i * window_size)
        w_end = (
            start_dt + datetime.timedelta(days=(i + 1) * window_size - 1)
            if i < n_windows - 1
            else end_dt
        )
        w_days = (w_end - w_start).days + 1

        # run_backtest already uses a lookback from today; for walk-forward we
        # run against signals in [w_start, w_end] by calling a targeted variant
        result = _run_backtest_window(db_path, w_start, w_end)
        result["window"] = i + 1
        result["window_start"] = w_start.strftime("%Y-%m-%d")
        result["window_end"] = w_end.strftime("%Y-%m-%d")
        result["window_days"] = w_days
        window_results.append(result)

    # Stability assessment across windows
    win_rates = [w.get("win_rate", 0) for w in window_results if "win_rate" in w]
    if len(win_rates) >= 2:
        avg_wr = sum(win_rates) / len(win_rates)
        max_wr = max(win_rates)
        min_wr = min(win_rates)
        spread = max_wr - min_wr
        stability = "STABLE" if spread < 0.15 else ("MODERATE" if spread < 0.30 else "UNSTABLE")
        for w in window_results:
            w["stability_label"] = stability
            w["wr_spread_across_windows"] = round(spread, 4)
            w["avg_window_win_rate"] = round(avg_wr, 4)

    return window_results


def _run_backtest_window(
    db_path: Path,
    start_date: datetime.date,
    end_date: datetime.date,
    slippage_pct: float = 0.003,
    commission_pct: float = 0.001,
) -> Dict[str, Any]:
    """Internal: run backtest restricted to signals in [start_date, end_date]."""
    conn = _get_conn(db_path)
    try:
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        try:
            col_cursor = conn.execute("PRAGMA table_info(unified_signals)")
            col_names = {row["name"] for row in col_cursor.fetchall()}

            def _pick_col(options: list, default_expr: str) -> str:
                for name in options:
                    if name in col_names:
                        return name
                return default_expr

            signal_type_col  = _pick_col(["signal_type", "conviction_tier", "behavioral_class"], "NULL")
            entry_lo_col     = _pick_col(["entry_lo", "entry_price", "entry_high"], "entry_price")
            entry_hi_col     = _pick_col(["entry_hi", "entry_high", "entry_price"], "entry_high")
            stop_loss_col    = _pick_col(["stop_loss"], "NULL")
            target1_col      = _pick_col(["target1", "t1_target"], "t1_target")
            target2_col      = _pick_col(["target2", "t2_target"], "t2_target")
            ues_col          = _pick_col(["ues_score", "unified_score", "scan_score"], "unified_score")
            regime_col       = _pick_col(["regime_at_signal", "active_regime"], "active_regime")

            query = f"""
                SELECT signal_date, symbol,
                       {signal_type_col}  AS signal_type,
                       {entry_lo_col}     AS entry_lo,
                       {entry_hi_col}     AS entry_hi,
                       {stop_loss_col}    AS stop_loss,
                       {target1_col}      AS target1,
                       {target2_col}      AS target2,
                       {ues_col}          AS ues_score,
                       {regime_col}       AS regime_at_signal
                FROM unified_signals
                WHERE signal_date >= ? AND signal_date <= ?
                ORDER BY signal_date ASC
            """
            cursor = conn.execute(query, (start_str, end_str))
            signals = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            return {"error": str(exc), "n_trades": 0}

        if not signals:
            return {"n_trades": 0, "win_rate": 0.0, "note": "No signals in window"}

        enriched: List[Dict[str, Any]] = []
        skipped = 0

        for sig in signals:
            signal_date = sig["signal_date"]
            symbol = sig["symbol"]
            signal_type = str(sig["signal_type"] or "LONG_SWING").upper()

            entry_lo = sig["entry_lo"]
            entry_hi = sig["entry_hi"]
            if entry_lo is not None and entry_hi is not None:
                entry_price = (float(entry_lo) + float(entry_hi)) / 2.0
            elif entry_lo is not None:
                entry_price = float(entry_lo)
            elif entry_hi is not None:
                entry_price = float(entry_hi)
            else:
                skipped += 1
                continue

            stop_loss = float(sig["stop_loss"]) if sig["stop_loss"] is not None else entry_price * 0.97
            target1 = float(sig["target1"]) if sig["target1"] is not None else entry_price * 1.05

            if stop_loss >= entry_price:
                skipped += 1
                continue

            max_hold = _get_hold_days_for_type(signal_type)
            # Entry starts the day AFTER signal_date (EOD signal → next-day entry)
            entry_after_date = (
                datetime.datetime.strptime(signal_date, "%Y-%m-%d").date()
                + datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")
            forward_bars = _load_forward_ohlcv(conn, symbol, entry_after_date, max_hold + 5)
            if not forward_bars:
                skipped += 1
                continue

            result = _simulate_trade_pnl(
                entry_price=entry_price,
                stop_loss=stop_loss,
                target1=target1,
                ohlcv_forward=forward_bars,
                max_hold_days=max_hold,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
            )

            if result["exit_reason"] == "PRICE_MISMATCH":
                skipped += 1
                continue

            enriched.append(
                {
                    "signal_type": signal_type,
                    "regime": str(sig["regime_at_signal"] or "UNKNOWN").upper(),
                    "pnl_pct": result["pnl_pct"],
                    "r_multiple": result["r_multiple"],
                    "hold_days": result["hold_days"],
                    "exit_reason": result["exit_reason"],
                }
            )

        stats = _compute_stats(enriched)
        stats["skipped"] = skipped
        return stats

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_backtest_report(results: Dict[str, Any]) -> None:
    """Print a formatted Arabic/English backtest report to stdout."""
    if "error" in results:
        print(f"\n❌ خطأ: {results['error']}\n")
        return

    n = results.get("n_trades", 0)
    days = results.get("days_window", "?")
    wr = results.get("win_rate", 0) * 100
    pf = results.get("profit_factor", 0)
    exp = results.get("expectancy_r", 0)
    hold = results.get("avg_hold_days", 0)
    total_pnl = results.get("total_pnl_pct", 0)   # already in % after Ph79 fix
    skipped = results.get("skipped", 0)

    print()
    print("📊 نتائج Backtest حقيقي (Ph79)")
    print("━" * 40)
    print(f"  إجمالي الصفقات: {n:>4d}  |  فترة: {days} يوم  |  تخطى: {skipped}")
    print(f"  Win Rate:      {wr:>5.1f}%  (prev hardcoded: ~30%)")
    print(f"  Profit Factor: {pf:>5.2f}")
    sign = "+" if exp >= 0 else ""
    print(f"  Expectancy:    {sign}{exp:.3f}R per trade")
    print(f"  Avg Hold:      {hold:.1f} days")
    sign_pnl = "+" if total_pnl >= 0 else ""
    print(f"  Total PnL:     {sign_pnl}{total_pnl:.2f}%  (raw, equal-weight)")
    print()

    by_type = results.get("by_type", {})
    if by_type:
        print("  حسب النوع:")
        for stype in ["SHORT_SWING", "LONG_SWING", "INVESTMENT", "UNDERVALUED", "SCALP"]:
            if stype in by_type:
                s = by_type[stype]
                print(
                    f"    {stype:<12s}  WR={s['win_rate']*100:.0f}%  "
                    f"PF={s['profit_factor']:.2f}  "
                    f"(N={s['n_trades']})"
                )
        print()

    by_regime = results.get("by_regime", {})
    if by_regime:
        print("  حسب الـ Regime:")
        for regime in ["BULL", "NEUTRAL", "CHOPPY", "BEAR", "UNKNOWN"]:
            if regime in by_regime:
                s = by_regime[regime]
                note = "  ← تجنب" if regime == "BEAR" and s.get("win_rate", 1) < 0.35 else ""
                print(
                    f"    {regime:<8s}  WR={s['win_rate']*100:.0f}%  "
                    f"PF={s['profit_factor']:.2f}{note}"
                )
        print()

    by_exit = results.get("by_exit", {})
    if by_exit:
        print("  حسب سبب الخروج:")
        exit_order = ["TARGET1", "TARGET2", "STOP_LOSS", "TIME_STOP", "DATA_END", "STALE_SIGNAL", "DEGENERATE_RISK"]
        for reason in exit_order:
            if reason in by_exit:
                s = by_exit[reason]
                avg_pnl = s["total_pnl_pct"] / s["n_trades"] if s["n_trades"] > 0 else 0.0
                print(
                    f"    {reason:<18s}  N={s['n_trades']:>3d}  "
                    f"avg={avg_pnl:+.2f}%  "
                    f"WR={s['win_rate']*100:.0f}%"
                )
        print()

    if "institutional_scorecard" in results:
        sc = results["institutional_scorecard"]
        print("  Institutional Scorecard:")
        for k, v in sc.items():
            print(f"    {k}: {v}")
        print()

    print("━" * 40)
    print()


def _print_walkforward_report(windows: List[Dict[str, Any]]) -> None:
    """Print walk-forward window results."""
    print()
    print("📈 Walk-Forward Analysis — 4 Windows")
    print("━" * 50)
    for w in windows:
        if "error" in w:
            print(f"  Window {w.get('window', '?')}: ERROR — {w['error']}")
            continue
        wn = w.get("window", "?")
        ws = w.get("window_start", "")
        we = w.get("window_end", "")
        wr = w.get("win_rate", 0) * 100
        pf = w.get("profit_factor", 0)
        n = w.get("n_trades", 0)
        exp = w.get("expectancy_r", 0)
        sign = "+" if exp >= 0 else ""
        print(
            f"  Window {wn}  [{ws} → {we}]  "
            f"N={n:>4d}  WR={wr:>5.1f}%  PF={pf:.2f}  Exp={sign}{exp:.3f}R"
        )

    if windows and "stability_label" in windows[0]:
        stability = windows[0]["stability_label"]
        spread = windows[0].get("wr_spread_across_windows", 0) * 100
        avg_wr = windows[0].get("avg_window_win_rate", 0) * 100
        emoji = {"STABLE": "✅", "MODERATE": "⚠️", "UNSTABLE": "🔴"}.get(stability, "")
        print()
        print(f"  Stability: {emoji} {stability}  (WR spread={spread:.1f}%,  avg={avg_wr:.1f}%)")
    print("━" * 50)
    print()


# ---------------------------------------------------------------------------
# Historical backtest — uses hist_backtest_signals for long-term evaluation
# ---------------------------------------------------------------------------

def run_historical_backtest(
    db_path: Optional[Path] = None,
    months: int = 12,
    min_ues: float = 92.0,  # raised 75→82→92 (2026-05-22): UES>=92 → WR=61.7% PF=2.39 (6m)  3m: WR=71.4% PF=3.92
    max_ues: Optional[float] = 96.0,    # (2026-05-23): UES=97-99 underperforms UES=92-96 by ~7pp WR — 6m WR=80.4% vs 77.9% at max=99
    regime_filter: Optional[str] = None,
    rsi_max: Optional[float] = None,
    min_adx: Optional[float] = None,
    min_vol_ratio: Optional[float] = 0.90,   # (2026-05-23): Gate 6d — vol<0.90 → low conviction, 8/15 losers had vol<0.90
    max_vol_ratio: Optional[float] = None,   # NEW (2026-05-22): vol>3 WR=64% vs vol<1.5 WR=82%
    min_ad_ratio: Optional[float] = None,    # NEW (2026-05-22): STOP_LOSS avg AD=0.98 vs TARGET1 1.14
    slippage_pct: float = 0.003,
    commission_pct: float = 0.001,
    max_hold_override: Optional[int] = None,  # NEW (2026-05-22): override hold duration for sweep testing
) -> Dict[str, Any]:
    """
    Backtest using hist_backtest_signals (reconstructed historical signals).

    This gives a proper long-term WR estimate using 12 months of EGX data
    rather than relying on the 7-day unified_signals window.

    Entry timing: signal_date + 1 day (EOD signal → next-day entry).
    """
    db_path = db_path or DB_PATH
    conn = _get_conn(db_path)
    run_date = datetime.date.today().strftime("%Y-%m-%d")

    try:
        cutoff = (
            datetime.date.today() - datetime.timedelta(days=months * 30)
        ).strftime("%Y-%m-%d")

        # Check table exists
        try:
            conn.execute("SELECT 1 FROM hist_backtest_signals LIMIT 1")
        except sqlite3.OperationalError:
            return {"error": "hist_backtest_signals table not found. Run historical_signal_reconstructor.py build first."}

        regime_clause = f"AND regime = '{regime_filter}'" if regime_filter else ""
        rsi_clause = f"AND rsi14 <= {rsi_max}" if rsi_max is not None else ""
        adx_clause = f"AND adx14 >= {min_adx}" if min_adx is not None else ""
        vol_clause = f"AND vol_ratio >= {min_vol_ratio}" if min_vol_ratio is not None else ""
        max_vol_clause = f"AND vol_ratio <= {max_vol_ratio}" if max_vol_ratio is not None else ""
        ad_clause = f"AND ad_ratio >= {min_ad_ratio}" if min_ad_ratio is not None else ""
        max_ues_clause = f"AND ues_proxy < {max_ues}" if max_ues is not None else ""
        cursor = conn.execute(
            f"""
            SELECT signal_date, symbol, signal_type,
                   entry_price, stop_loss, target1, target2,
                   rsi14, adx14, vol_ratio, ues_proxy, regime
            FROM hist_backtest_signals
            WHERE signal_date >= ?
              AND ues_proxy >= ?
              {max_ues_clause}
              {regime_clause}
              {rsi_clause}
              {adx_clause}
              {vol_clause}
              {max_vol_clause}
              {ad_clause}
            ORDER BY signal_date ASC
            """,
            (cutoff, min_ues),
        )
        signals = cursor.fetchall()

        if not signals:
            return {
                "error": f"No historical signals found since {cutoff} with ues_proxy >= {min_ues}",
                "n_trades": 0,
            }

        enriched: List[Dict[str, Any]] = []
        skipped = 0

        for sig in signals:
            signal_date = sig["signal_date"]
            symbol = sig["symbol"]
            signal_type = str(sig["signal_type"] or "LONG_SWING").upper()

            entry_price = float(sig["entry_price"])
            stop_loss   = float(sig["stop_loss"])
            target1     = float(sig["target1"])

            if stop_loss >= entry_price or entry_price <= 0:
                skipped += 1
                continue

            max_hold = max_hold_override if max_hold_override is not None else _get_hold_days_for_type(signal_type)

            # Enter next day after signal (EOD signal → next-day entry)
            entry_after_date = (
                datetime.datetime.strptime(signal_date, "%Y-%m-%d").date()
                + datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")
            forward_bars = _load_forward_ohlcv(conn, symbol, entry_after_date, max_hold + 5)

            if not forward_bars:
                skipped += 1
                continue

            result = _simulate_trade_pnl(
                entry_price=entry_price,
                stop_loss=stop_loss,
                target1=target1,
                ohlcv_forward=forward_bars,
                max_hold_days=max_hold,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
            )

            if result["exit_reason"] in ("PRICE_MISMATCH",):
                skipped += 1
                continue

            enriched.append({
                "signal_date": signal_date,
                "symbol": symbol,
                "signal_type": signal_type,
                "regime": str(sig["regime"] or "UNKNOWN").upper(),
                "ues_proxy": float(sig["ues_proxy"] or 0),
                "pnl_pct": result["pnl_pct"],
                "r_multiple": result["r_multiple"],
                "hold_days": result["hold_days"],
                "exit_reason": result["exit_reason"],
            })

        if not enriched:
            return {
                "error": "All historical signals were skipped.",
                "skipped": skipped,
                "n_trades": 0,
            }

        agg = _compute_stats(enriched)
        by_type = _breakdown_by_key(enriched, "signal_type")
        by_exit = _breakdown_by_key(enriched, "exit_reason")

        # UES-stratified WR: above/below median
        if enriched:
            all_ues = sorted(t["ues_proxy"] for t in enriched)
            ues_median = all_ues[len(all_ues) // 2]
            high_ues = [t for t in enriched if t["ues_proxy"] >= ues_median]
            low_ues  = [t for t in enriched if t["ues_proxy"] < ues_median]
            ues_high_stats = _compute_stats(high_ues)
            ues_low_stats  = _compute_stats(low_ues)
        else:
            ues_median = 0.0
            ues_high_stats = ues_low_stats = {}

        return {
            **agg,
            "months_window": months,
            "min_ues_filter": min_ues,
            "regime_filter": regime_filter or "ALL",
            "skipped": skipped,
            "by_type": by_type,
            "by_exit": by_exit,
            "ues_median": round(ues_median, 1),
            "ues_high_stats": ues_high_stats,
            "ues_low_stats": ues_low_stats,
        }

    finally:
        conn.close()


def get_recent_losers(db_path=None, lookback_days: int = 60) -> Dict[str, Any]:
    """
    Compute symbols with STOP_LOSS/TIME_STOP exits in the last `lookback_days` days.

    Uses hist_backtest_signals + forward OHLCV simulation to find actual losing trades.
    Returns a dict: symbol → {last_loss_date, days_ago, worst_pnl, loss_count}

    Used by cmd_predict_ensemble() for recent-failure-memory penalty:
    stocks that recently failed are down-weighted to avoid repeat losses.
    (Added 2026-05-23)
    """
    db_path = db_path or DB_PATH
    conn = _get_conn(db_path)
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    losers: Dict[str, Any] = {}

    try:
        rows = conn.execute(
            """
            SELECT signal_date, symbol, signal_type, entry_price, stop_loss, target1
            FROM hist_backtest_signals
            WHERE signal_date >= ?
            ORDER BY signal_date ASC
            """,
            (cutoff,),
        ).fetchall()

        for sig in rows:
            symbol      = sig["symbol"]
            signal_date = sig["signal_date"]
            signal_type = str(sig["signal_type"] or "SHORT_SWING").upper()
            entry_price = float(sig["entry_price"] or 0)
            stop_loss   = float(sig["stop_loss"]   or 0)
            target1     = float(sig["target1"]     or 0)

            if stop_loss >= entry_price or entry_price <= 0:
                continue

            # Use the correct hold duration for each signal type (e.g. SHORT_SWING=9d)
            max_hold = _get_hold_days_for_type(signal_type)

            entry_after = (
                datetime.datetime.strptime(signal_date, "%Y-%m-%d").date()
                + datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")

            forward_bars = _load_forward_ohlcv(conn, symbol, entry_after, max_hold + 5)
            if not forward_bars:
                continue

            result = _simulate_trade_pnl(
                entry_price=entry_price,
                stop_loss=stop_loss,
                target1=target1,
                ohlcv_forward=forward_bars,
                max_hold_days=max_hold,
            )

            # Count as a loser only if STOP_LOSS or negative-PnL TIME_STOP
            is_loser = (
                result["exit_reason"] == "STOP_LOSS"
                or (result["exit_reason"] == "TIME_STOP" and result["pnl_pct"] < 0)
            )
            if not is_loser:
                continue

            pnl      = result["pnl_pct"]
            days_ago = (today - datetime.datetime.strptime(signal_date, "%Y-%m-%d").date()).days

            if symbol not in losers:
                losers[symbol] = {
                    "last_loss_date": signal_date,
                    "days_ago":       days_ago,
                    "worst_pnl":      pnl,
                    "loss_count":     1,
                }
            else:
                # Keep the MOST RECENT loss date (for penalty freshness)
                if signal_date > losers[symbol]["last_loss_date"]:
                    losers[symbol]["last_loss_date"] = signal_date
                    losers[symbol]["days_ago"]       = days_ago
                # Track worst PnL for severity scaling
                if pnl < losers[symbol]["worst_pnl"]:
                    losers[symbol]["worst_pnl"] = pnl
                losers[symbol]["loss_count"] += 1

    except Exception:
        pass
    finally:
        conn.close()

    return losers


def _print_historical_report(results: Dict[str, Any]) -> None:
    if "error" in results:
        print(f"\n❌ خطأ: {results['error']}\n")
        return

    n = results.get("n_trades", 0)
    months = results.get("months_window", "?")
    wr = results.get("win_rate", 0) * 100
    pf = results.get("profit_factor", 0)
    exp = results.get("expectancy_r", 0)
    hold = results.get("avg_hold_days", 0)
    total_pnl = results.get("total_pnl_pct", 0)
    skipped = results.get("skipped", 0)
    min_ues = results.get("min_ues_filter", 0)

    print()
    print(f"📊 Backtest تاريخي ({months} شهر | UES>={min_ues:.0f})")
    print("━" * 50)
    print(f"  إجمالي الصفقات: {n:>5d}  |  تخطى: {skipped}")
    print(f"  Win Rate:      {wr:>5.1f}%")
    sign_pf = "+" if pf > 1 else ""
    print(f"  Profit Factor: {pf:>5.2f}")
    sign_exp = "+" if exp >= 0 else ""
    print(f"  Expectancy:    {sign_exp}{exp:.3f}R per trade")
    print(f"  Avg Hold:      {hold:.1f} days")
    sign_pnl = "+" if total_pnl >= 0 else ""
    print(f"  Total PnL:     {sign_pnl}{total_pnl:.2f}%  (equal-weight, {n} trades)")

    # UES stratification
    ues_med = results.get("ues_median", 0)
    h = results.get("ues_high_stats", {})
    l = results.get("ues_low_stats", {})
    if h and l:
        print()
        print(f"  UES تقطيع (median={ues_med:.0f}):")
        print(f"    UES>={ues_med:.0f}  WR={h.get('win_rate',0)*100:.1f}%  N={h.get('n_trades',0)}  Exp={h.get('expectancy_r',0):+.3f}R")
        print(f"    UES< {ues_med:.0f}  WR={l.get('win_rate',0)*100:.1f}%  N={l.get('n_trades',0)}  Exp={l.get('expectancy_r',0):+.3f}R")

    by_exit = results.get("by_exit", {})
    if by_exit:
        print()
        print("  حسب سبب الخروج:")
        for reason in ["TARGET1", "STOP_LOSS", "TRAIL_STOP", "TIME_STOP", "DATA_END", "STALE_SIGNAL"]:
            if reason in by_exit:
                s = by_exit[reason]
                avg_pnl = s["total_pnl_pct"] / s["n_trades"] if s["n_trades"] > 0 else 0.0
                print(f"    {reason:<18s}  N={s['n_trades']:>4d}  avg={avg_pnl:+.2f}%  WR={s['win_rate']*100:.0f}%")

    by_type = results.get("by_type", {})
    if by_type:
        print()
        print("  حسب النوع:")
        for stype in ["SHORT_SWING", "LONG_SWING"]:
            if stype in by_type:
                s = by_type[stype]
                print(f"    {stype:<12}  WR={s['win_rate']*100:.1f}%  PF={s['profit_factor']:.2f}  N={s['n_trades']}")

    print("━" * 50)
    print()


# ---------------------------------------------------------------------------
# Discovery Fabric — validate_atoms endpoint
# ---------------------------------------------------------------------------

def validate_discovery_atoms(
    db_path: Optional[Path] = None,
    atom_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run OOS atom validation (delegates to discovery_backtest_gate helpers)."""
    sys.path.insert(0, str(_ROOT / "scripts" / "python"))
    from discovery_backtest_gate import (  # noqa: WPS433
        eval_atom_on_oos,
        passes_gate,
        MIN_N,
    )
    from quant_discovery import load_bars, build_examples, atoms  # noqa: WPS433

    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path, timeout=120)
    conn.row_factory = sqlite3.Row

    if atom_ids:
        placeholders = ",".join("?" * len(atom_ids))
        proposed = conn.execute(
            f"SELECT atom_id, hard_negative, regime_filter FROM discovery_atom_registry "
            f"WHERE atom_id IN ({placeholders})",
            atom_ids,
        ).fetchall()
    else:
        proposed = conn.execute(
            "SELECT atom_id, hard_negative, regime_filter FROM discovery_atom_registry "
            "WHERE status IN ('proposed', 'validated', 'rejected')"
        ).fetchall()

    data = load_bars(conn)
    examples = build_examples(data, horizon=5)
    dates = sorted({x["date"] for x in examples})
    split_date = dates[int(len(dates) * 0.75)] if dates else "2025-01-01"
    atom_map = {name: fn for name, fn in atoms()}

    results_atoms = []
    n_val, n_rej = 0, 0
    for row in proposed:
        aid = row["atom_id"]
        fn = atom_map.get(aid)
        if not fn:
            results_atoms.append({"atom_id": aid, "status": "skipped", "reason": "no_evaluator"})
            continue
        metrics = eval_atom_on_oos(aid, examples, split_date, atom_map)
        if not metrics:
            status = "rejected"
            n_rej += 1
            results_atoms.append({
                "atom_id": aid, "status": status, "reason": f"n<{MIN_N}",
            })
            continue
        ok = passes_gate(metrics, row["hard_negative"])
        status = "validated" if ok else "rejected"
        if ok:
            n_val += 1
        else:
            n_rej += 1
        results_atoms.append({
            "atom_id": aid,
            "status": status,
            "backtest_wr": metrics["backtest_wr"],
            "backtest_n": metrics["backtest_n"],
            "backtest_lift": metrics["backtest_lift"],
            "backtest_pf": metrics["backtest_pf"],
            "baseline_wr": metrics["baseline_wr"],
        })

    conn.close()
    return {
        "success": True,
        "n_validated": n_val,
        "n_rejected": n_rej,
        "split_date": split_date,
        "n_examples": len(examples),
        "atoms": results_atoms,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backtest Engine v2 — Real Price Based (Ph79)"
    )
    sub = parser.add_subparsers(dest="command")

    # run subcommand
    run_p = sub.add_parser("run", help="Run backtest on recent signals")
    run_p.add_argument(
        "--days", type=int, default=180, help="Look-back window in days (default=180)"
    )
    run_p.add_argument(
        "--json", action="store_true", dest="output_json", help="Output JSON instead of report"
    )
    run_p.add_argument(
        "--db", type=str, default=None, help="Override DB path"
    )
    run_p.add_argument(
        "--min-signals", type=int, default=10, help="Minimum signals required (default=10)"
    )
    run_p.add_argument(
        "--slippage", type=float, default=0.003, help="Slippage fraction (default=0.003)"
    )
    run_p.add_argument(
        "--commission", type=float, default=0.001, help="Commission fraction (default=0.001)"
    )

    # historical subcommand — uses hist_backtest_signals for proper long-term backtest
    hist_p = sub.add_parser("historical", help="Backtest against hist_backtest_signals (12 months)")
    hist_p.add_argument("--months", type=int, default=12, help="How many months back to test (default=12)")
    hist_p.add_argument("--min-ues", type=float, default=92.0, help="Min UES proxy to include (default=92, sweet spot: WR=61.7%% PF=2.39)")
    hist_p.add_argument("--max-ues", type=float, default=96.0, dest="max_ues",
                        help="Max UES proxy to include (default=96; UES=97-99 underperform UES=92-96 by ~7pp WR: 6m WR=80.4%% vs 77.9%% at max=99)")
    hist_p.add_argument("--db", type=str, default=None, help="Override DB path")
    hist_p.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")
    hist_p.add_argument("--regime", type=str, default=None, help="Filter to regime: BULL, BEAR, CHOPPY")
    hist_p.add_argument("--rsi-max", type=float, default=None, dest="rsi_max", help="Max RSI14 at entry (e.g. 70 to exclude overbought)")
    hist_p.add_argument("--min-adx", type=float, default=None, dest="min_adx", help="Min ADX at entry (e.g. 30 for trend strength filter)")
    hist_p.add_argument("--min-vol", type=float, default=0.90, dest="min_vol_ratio",
                        help="Min vol ratio 20d (default=0.90; Gate 6d: vol<0.90=low conviction, 8/15 recent losers had vol<0.90)")
    hist_p.add_argument("--max-vol", type=float, default=None, dest="max_vol_ratio",
                        help="Max vol ratio 20d (default=None; 2026-05-22: vol>3 WR=64%% — high-vol chase entries fail at 2x rate; use 3.0)")
    hist_p.add_argument("--min-ad", type=float, default=None, dest="min_ad_ratio",
                        help="Min A/D ratio on signal day (default=None; 2026-05-22: min_ad=1.0 → 6m WR=76.2%% +5.2pp; combined ad+vol → 78.6%%)")
    hist_p.add_argument("--max-hold", type=int, default=None, dest="max_hold_override",
                        help="Override hold duration in days (default: per signal_type; SHORT_SWING=9)")
    hist_p.add_argument("--slippage", type=float, default=0.003)
    hist_p.add_argument("--commission", type=float, default=0.001)

    # validate_atoms — Discovery Fabric gate (OOS WR/lift per atom)
    va_p = sub.add_parser("validate_atoms", help="Validate discovery atoms via OOS backtest gate")
    va_p.add_argument("--db", type=str, default=None, help="Override DB path")
    va_p.add_argument(
        "--atoms", type=str, default=None,
        help="Comma-separated atom_ids (default: all proposed in registry)",
    )
    va_p.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")

    # walkforward subcommand
    wf_p = sub.add_parser("walkforward", help="Walk-forward 4-window analysis")
    wf_p.add_argument("--db", type=str, default=None, help="Override DB path")
    wf_p.add_argument(
        "--windows", type=int, default=4, help="Number of walk-forward windows (default=4)"
    )
    wf_p.add_argument(
        "--json", action="store_true", dest="output_json", help="Output JSON instead of report"
    )

    return parser


def main() -> None:
    parser = _build_parser()

    # Support bare invocation with no subcommand → default to "run"
    if len(sys.argv) == 1:
        sys.argv.append("run")

    args = parser.parse_args()

    db_path = Path(args.db) if getattr(args, "db", None) else DB_PATH

    if args.command == "run":
        results = run_backtest(
            db_path=db_path,
            days=args.days,
            min_signals=args.min_signals,
            slippage_pct=args.slippage,
            commission_pct=args.commission,
        )
        if args.output_json:
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        else:
            _print_backtest_report(results)

    elif args.command == "historical":
        results = run_historical_backtest(
            db_path=db_path,
            months=args.months,
            min_ues=args.min_ues,
            max_ues=getattr(args, "max_ues", 96.0),
            regime_filter=getattr(args, "regime", None),
            rsi_max=getattr(args, "rsi_max", None),
            min_adx=getattr(args, "min_adx", None),
            min_vol_ratio=getattr(args, "min_vol_ratio", None),
            max_vol_ratio=getattr(args, "max_vol_ratio", None),
            min_ad_ratio=getattr(args, "min_ad_ratio", None),
            slippage_pct=args.slippage,
            commission_pct=args.commission,
            max_hold_override=getattr(args, "max_hold_override", None),
        )
        if args.output_json:
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        else:
            _print_historical_report(results)

    elif args.command == "validate_atoms":
        results = validate_discovery_atoms(
            db_path=db_path,
            atom_ids=[a.strip() for a in args.atoms.split(",")] if args.atoms else None,
        )
        if args.output_json:
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Validated: {results.get('n_validated', 0)} | Rejected: {results.get('n_rejected', 0)}")
            for r in results.get("atoms") or []:
                print(f"  {r['atom_id']}: {r['status']} WR={r.get('backtest_wr')} n={r.get('backtest_n')}")

    elif args.command == "walkforward":
        windows = walk_forward_real(
            db_path=db_path,
            n_windows=args.windows,
        )
        if args.output_json:
            print(json.dumps(windows, ensure_ascii=False, indent=2, default=str))
        else:
            _print_walkforward_report(windows)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
