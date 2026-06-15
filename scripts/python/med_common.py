#!/usr/bin/env python3
"""MED — shared utilities, invariants, DB, bar loading."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
EPS = 1e-9

MED_INVARIANTS = {
    "MED_SHADOW": "1",
    "MED_CLIENT_SIGNAL": "0",
    "MED_OPP_BOOST": "0",
    "MED_FEED_BOOST": "0",
    "MED_POSITION_SIZING_LIVE": "0",
    "client_path_allowed": False,
    "research_only": True,
    "shadow_only": True,
    "no_veto": True,
    "no_actionable_change": True,
}

HORIZONS = (5, 10, 20, 30, 45)
THRESHOLDS = (0.05, 0.10, 0.15, 0.20)
PRIMARY_H, PRIMARY_TH = 20, 0.10
OOS_START = "2025-01-01"
OOS_END = "2026-06-11"


def effective_oos_end(conn: sqlite3.Connection, trade_date: Optional[str] = None) -> str:
    """Extend OOS window through latest LRE / requested trade_date."""
    candidates = [OOS_END]
    if trade_date:
        candidates.append(trade_date)
    try:
        row = conn.execute("SELECT MAX(trade_date) d FROM lre_daily_scores").fetchone()
        if row and row["d"]:
            candidates.append(row["d"])
    except sqlite3.OperationalError:
        pass
    return max(candidates)
MIN_BARS = 130
MED2_FORWARD_START = "2026-06-12"
ANALOGUE_K = 20
ANALOGUE_K_LEGACY = 50
ELIGIBLE_BUCKETS = {
    "MED_HIGH_CONVICTION_RESEARCH",
    "MED_POSITIVE_EXPECTANCY",
    "MED_MONITOR",
}

HYPOTHETICAL_BOOST = {
    "MED_HIGH_CONVICTION_RESEARCH": 3.0,
    "MED_POSITIVE_EXPECTANCY": 2.0,
    "MED_MONITOR": 1.0,
    "MED_DO_NOT_CHASE": 0.0,
    "MED_FAILURE_WARNING": 0.0,
    "MED_INSUFFICIENT_SAMPLE": 0.0,
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    conn.row_factory = sqlite3.Row
    return conn


def sf(v, default=None):
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def bar_date(ts: int) -> str:
    return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")


def load_bars(conn: sqlite3.Connection) -> Tuple[Dict[str, List[dict]], dict]:
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    meta = {"source": "ohlcv_history_execution"}
    rows = conn.execute(
        """
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history_execution
        ORDER BY symbol, bar_time
        """
    ).fetchall()
    if not rows:
        meta["source"] = "ohlcv_history"
        rows = conn.execute(
            """
            SELECT symbol, bar_time, open, high, low, close, volume
            FROM ohlcv_history ORDER BY symbol, bar_time
            """
        ).fetchall()
    for r in rows:
        sym = r["symbol"]
        by_sym[sym].append({
            "date": bar_date(r["bar_time"]),
            "open": sf(r["open"]), "high": sf(r["high"]),
            "low": sf(r["low"]), "close": sf(r["close"]),
            "volume": sf(r["volume"], 0) or 0,
        })
    for sym in by_sym:
        by_sym[sym] = sorted(by_sym[sym], key=lambda x: x["date"])
    dates = [b["date"] for bars in by_sym.values() for b in bars]
    meta["symbols"] = len(by_sym)
    meta["bars"] = sum(len(v) for v in by_sym.values())
    if dates:
        meta["min_date"], meta["max_date"] = min(dates), max(dates)
    return dict(by_sym), meta


def zscore_hist(vals: List[float], x: float, window: int) -> float:
    sl = [v for v in vals[-window:] if v is not None and not math.isnan(v)]
    if len(sl) < 5:
        return 0.0
    mu = mean(sl)
    var = sum((v - mu) ** 2 for v in sl) / max(len(sl) - 1, 1)
    sig = math.sqrt(var) if var > 0 else EPS
    return (x - mu) / (sig + EPS)


def percentile_rank(vals: List[float], x: float) -> float:
    clean = sorted(v for v in vals if v is not None)
    if not clean:
        return 0.5
    below = sum(1 for v in clean if v <= x)
    return below / len(clean)


def rank_normalize(x: float, population: List[float]) -> float:
    return percentile_rank(population, x)


def sigmoid(x: float) -> float:
    x = max(-20, min(20, x))
    return 1.0 / (1.0 + math.exp(-x))


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def ret_n(bars: List[dict], idx: int, n: int) -> float:
    if idx < n:
        return 0.0
    c0, c1 = bars[idx - n]["close"], bars[idx]["close"]
    if not c0 or not c1 or c0 <= 0:
        return 0.0
    return c1 / c0 - 1.0


def forward_return(bars: List[dict], idx: int, h: int) -> Optional[float]:
    if idx + h >= len(bars):
        return None
    c0, c1 = bars[idx]["close"], bars[idx + h]["close"]
    if not c0 or not c1 or c0 <= 0:
        return None
    return c1 / c0 - 1.0


def forward_path(bars: List[dict], idx: int, h: int) -> dict:
    out = {"mfe": None, "mae": None, "path_quality": None,
           "stop6": 0, "stop8": 0, "stop10": 0, "late_mover": 0}
    if idx + h >= len(bars):
        return out
    c0 = bars[idx]["close"]
    if not c0 or c0 <= 0:
        return out
    rets = []
    for k in range(1, h + 1):
        ck = bars[idx + k]["close"]
        if ck:
            rets.append(ck / c0 - 1.0)
    if not rets:
        return out
    mfe, mae = max(rets), min(rets)
    out["mfe"] = mfe
    out["mae"] = mae
    out["path_quality"] = mfe / (EPS + abs(mae))
    out["stop6"] = int(mae <= -0.06)
    out["stop8"] = int(mae <= -0.08)
    out["stop10"] = int(mae <= -0.10)
    out["late_mover"] = int(mae <= -0.08 and mfe >= 0.05)
    return out


def vol_ratio(bars: List[dict], idx: int, n: int = 20) -> float:
    if idx < n:
        return 1.0
    v = bars[idx]["volume"] or 0
    avg = mean(b["volume"] or 0 for b in bars[idx - n:idx]) or 1.0
    return v / avg


def dollar_volume(bar: dict) -> float:
    return (bar["close"] or 0) * (bar["volume"] or 0)


def ensure_med_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS med_daily_scores (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL, sector TEXT,
        med_score REAL, med_bucket TEXT,
        p_cond_20d_10 REAL, expected_return_20d REAL,
        stored_energy REAL, absorption_score REAL,
        distribution_shift_score REAL,
        behavior_changed_before_price INTEGER DEFAULT 0,
        physics_force REAL, stored_pressure_physics REAL,
        liquidity_fitness REAL, crowding_score REAL,
        failure_similarity REAL, sample_quality REAL, regime_fit REAL,
        condition_key TEXT, reason_codes TEXT, risk_flags TEXT,
        hypothetical_boost REAL DEFAULT 0,
        client_path_allowed INTEGER DEFAULT 0,
        research_only INTEGER DEFAULT 1,
        shadow_only INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_conditional_edge_tables (
        asof_date TEXT NOT NULL, condition_key TEXT NOT NULL,
        horizon INTEGER NOT NULL, threshold REAL NOT NULL,
        n INTEGER, hit_rate REAL, avg_return REAL, median_return REAL,
        avg_win REAL, avg_loss REAL, expectancy REAL,
        pf_100 REAL, pf_150 REAL, pf_200 REAL,
        stop8 REAL, top10_dominance REAL, sector_concentration REAL,
        sample_quality REAL, window_mode TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (asof_date, condition_key, horizon, threshold, window_mode)
    );
    CREATE TABLE IF NOT EXISTS med_distribution_shift_daily (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
        psi REAL, w_shift REAL, ks REAL, shift_score REAL,
        behavior_changed INTEGER DEFAULT 0,
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_path_profiles (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL, horizon INTEGER NOT NULL,
        mfe REAL, mae REAL, path_quality REAL,
        stop6 INTEGER, stop8 INTEGER, stop10 INTEGER, late_mover INTEGER,
        PRIMARY KEY (trade_date, symbol, horizon)
    );
    CREATE TABLE IF NOT EXISTS med_failure_patterns (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
        failure_similarity REAL, crowding_penalty REAL,
        do_not_chase INTEGER DEFAULT 0,
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_sample_quality (
        asof_date TEXT NOT NULL, condition_key TEXT NOT NULL,
        n INTEGER, sample_quality REAL, top10_dominance REAL,
        sector_concentration REAL, bootstrap_confidence REAL,
        PRIMARY KEY (asof_date, condition_key)
    );
    CREATE TABLE IF NOT EXISTS med_research_feed (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
        med_score REAL, med_bucket TEXT, condition_key TEXT,
        reason_codes TEXT, risk_flags TEXT,
        hypothetical_boost REAL DEFAULT 0,
        client_path_allowed INTEGER DEFAULT 0,
        research_only INTEGER DEFAULT 1,
        shadow_only INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_analogue_scores_daily (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
        analogue_p_tail_20_10 REAL, analogue_neighbors INTEGER,
        analogue_confidence REAL, analogue_lift REAL,
        client_path_allowed INTEGER DEFAULT 0,
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS med_threshold_snapshots (
        asof_date TEXT NOT NULL, metric TEXT NOT NULL,
        p50 REAL, p75 REAL, p90 REAL, window_mode TEXT,
        PRIMARY KEY (asof_date, metric, window_mode)
    );
    CREATE TABLE IF NOT EXISTS med_forward_shadow_ledger (
        trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
        sector TEXT, med_score REAL, med_bucket TEXT,
        condition_key TEXT, analogue_p_tail REAL,
        forward_return_5d REAL, forward_return_10d REAL,
        forward_return_20d REAL, mfe_20d REAL, mae_20d REAL,
        exit_status TEXT DEFAULT 'open',
        ledger_mode TEXT DEFAULT 'live',
        client_path_allowed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol, ledger_mode)
    );
    """)


def _ensure_med_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def ensure_med_schema(conn: sqlite3.Connection) -> None:
    """Create MED tables + apply lightweight column migrations (MED-0.4)."""
    ensure_med_tables(conn)
    for col, typ in (
        ("p_tail", "REAL"),
        ("med_score_rank", "REAL"),
        ("med_edge_score", "REAL"),
        ("med_math_score", "REAL"),
    ):
        _ensure_med_column(conn, "med_daily_scores", col, typ)
    conn.commit()


def latest_med_trade_date(conn: sqlite3.Connection, *, prefer_complete: bool = True) -> Optional[str]:
    """Latest MED trade_date; skip partial runs (few buckets / flat sample_quality)."""
    if prefer_complete:
        row = conn.execute(
            """
            SELECT trade_date
            FROM med_daily_scores
            GROUP BY trade_date
            HAVING COUNT(DISTINCT med_bucket) >= 5
               AND MAX(sample_quality) > 0.2
            ORDER BY trade_date DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return row[0] if isinstance(row, tuple) else row["trade_date"]
    row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
    if not row:
        return None
    return row[0] if isinstance(row, tuple) else row["d"]


def ensure_med_tables_legacy(conn: sqlite3.Connection) -> None:
    """Alias — prefer ensure_med_schema."""
    ensure_med_schema(conn)


def load_sectors(conn: sqlite3.Connection) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for r in conn.execute("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL"):
            out[r["symbol"]] = r["sector"] or "Unknown"
    except sqlite3.OperationalError:
        pass
    return out


def load_lre_context(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not _table_exists(conn, "lre_daily_scores"):
        return out
    for r in conn.execute("SELECT * FROM lre_daily_scores WHERE trade_date=?", (trade_date,)):
        d = dict(r)
        out[d["symbol"]] = d
    if _table_exists(conn, "lre_research_feed_daily"):
        for r in conn.execute(
            "SELECT symbol, feed_tier, pilot_eligible FROM lre_research_feed_daily WHERE signal_date=?",
            (trade_date,),
        ):
            out.setdefault(r["symbol"], {})["feed_tier"] = r["feed_tier"]
            out[r["symbol"]]["pilot_eligible"] = r["pilot_eligible"]
    if _table_exists(conn, "lre_mde_dual_gate_audit"):
        for r in conn.execute(
            "SELECT symbol, dual_gate_type, lre_sub_stage FROM lre_mde_dual_gate_audit WHERE trade_date=?",
            (trade_date,),
        ):
            out.setdefault(r["symbol"], {})["dual_gate_type"] = r["dual_gate_type"]
            out[r["symbol"]]["lre_sub_stage"] = r["lre_sub_stage"]
    return out


def load_lre_all(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Dict[str, dict]]:
    out: Dict[str, Dict[str, dict]] = defaultdict(dict)
    if not _table_exists(conn, "lre_daily_scores"):
        return {}
    for r in conn.execute(
        "SELECT * FROM lre_daily_scores WHERE trade_date>=? AND trade_date<=?",
        (start, end),
    ):
        d = dict(r)
        out[d["trade_date"]][d["symbol"]] = d
    if _table_exists(conn, "lre_research_feed_daily"):
        for r in conn.execute(
            "SELECT signal_date, symbol, feed_tier, pilot_eligible FROM lre_research_feed_daily "
            "WHERE signal_date>=? AND signal_date<=?",
            (start, end),
        ):
            out[r["signal_date"]].setdefault(r["symbol"], {})["feed_tier"] = r["feed_tier"]
            out[r["signal_date"]][r["symbol"]]["pilot_eligible"] = r["pilot_eligible"]
    if _table_exists(conn, "lre_mde_dual_gate_audit"):
        for r in conn.execute(
            "SELECT trade_date, symbol, dual_gate_type, lre_sub_stage FROM lre_mde_dual_gate_audit "
            "WHERE trade_date>=? AND trade_date<=?",
            (start, end),
        ):
            out[r["trade_date"]].setdefault(r["symbol"], {})["dual_gate_type"] = r["dual_gate_type"]
            out[r["trade_date"]][r["symbol"]]["lre_sub_stage"] = r["lre_sub_stage"]
    return dict(out)


def load_mde_context(conn: sqlite3.Connection, trade_date: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if _table_exists(conn, "egx_market_discovery_daily"):
        for r in conn.execute(
            "SELECT symbol, effective_score, mde_stage, gates_passed_json FROM egx_market_discovery_daily WHERE trade_date=?",
            (trade_date,),
        ):
            stage = (r["mde_stage"] or "").upper()
            gates = r["gates_passed_json"] or ""
            passed = (
                stage in ("WATCH", "ACTIONABLE", "PASS", "MONITOR", "IGNITION", "PRE_BREAKOUT")
                or '"pass"' in gates.lower()
                or gates.count("true") >= 2
            )
            out[r["symbol"]] = {
                "mde_score": sf(r["effective_score"], 0),
                "mde_gate_passed": 1 if passed else 0,
            }
    return out


def load_mde_all(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Dict[str, dict]]:
    out: Dict[str, Dict[str, dict]] = defaultdict(dict)
    if not _table_exists(conn, "egx_market_discovery_daily"):
        return {}
    for r in conn.execute(
        "SELECT trade_date, symbol, effective_score, mde_stage, gates_passed_json "
        "FROM egx_market_discovery_daily WHERE trade_date>=? AND trade_date<=?",
        (start, end),
    ):
        stage = (r["mde_stage"] or "").upper()
        gates = r["gates_passed_json"] or ""
        passed = (
            stage in ("WATCH", "ACTIONABLE", "PASS", "MONITOR", "IGNITION", "PRE_BREAKOUT")
            or '"pass"' in gates.lower()
            or gates.count("true") >= 2
        )
        out[r["trade_date"]][r["symbol"]] = {
            "mde_score": sf(r["effective_score"], 0),
            "mde_gate_passed": 1 if passed else 0,
        }
    return dict(out)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def pf_from_returns(rets: List[float], cost: float = 0.01) -> Optional[float]:
    if not rets:
        return None
    wins = [r - cost for r in rets if r - cost > 0]
    losses = [abs(r - cost) for r in rets if r - cost <= 0]
    if not losses:
        return 99.0 if wins else None
    gw = sum(wins)
    gl = sum(losses)
    return gw / gl if gl > 0 else None


def top10_dominance(symbols: List[str]) -> float:
    if not symbols:
        return 0.0
    from collections import Counter
    c = Counter(symbols)
    top10 = sum(n for _, n in c.most_common(10))
    return top10 / len(symbols)


def sector_concentration(sectors: List[str]) -> float:
    if not sectors:
        return 0.0
    from collections import Counter
    c = Counter(sectors)
    return c.most_common(1)[0][1] / len(sectors) if c else 0.0
