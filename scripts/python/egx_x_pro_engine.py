#!/usr/bin/env python3
"""
EGX-X Pro Engine
================

Liquidity-first intraday/swing discovery layer for EGX.

It ranks the market using:
EMA alignment, VWAP/anchored VWAP, relative strength vs EGX30 and sector,
RVOL 5/20/60, liquidity expansion, volatility compression, turnover proxy,
ownership rotation, ATR efficiency, and volume profile proxy levels.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone())


def sf(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def clamp(x: Optional[float], lo: float = 0.0, hi: float = 100.0) -> float:
    x = sf(x, lo)
    return max(lo, min(hi, x if x is not None else lo))


def bar_date(ts: int) -> str:
    return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")


def ema(vals: Sequence[float], n: int) -> Optional[float]:
    vals = [float(v) for v in vals if sf(v) is not None]
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = mean(vals[:n])
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def ret(rows: Sequence[sqlite3.Row], n: int) -> Optional[float]:
    if len(rows) <= n:
        return None
    a = sf(rows[-n - 1]["close"])
    b = sf(rows[-1]["close"])
    if not a or a <= 0 or b is None:
        return None
    return b / a - 1


def atr_pct(rows: Sequence[sqlite3.Row], n: int = 14) -> Optional[float]:
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(len(rows) - n, len(rows)):
        h = sf(rows[i]["high"])
        l = sf(rows[i]["low"])
        pc = sf(rows[i - 1]["close"])
        if h is None or l is None or pc is None:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c = sf(rows[-1]["close"])
    return mean(trs) / c if trs and c else None


def avg(values: Sequence[float]) -> Optional[float]:
    vals = [sf(v) for v in values if sf(v) is not None]
    return mean(vals) if vals else None


def load_bars(conn: sqlite3.Connection) -> Dict[str, List[sqlite3.Row]]:
    source = "ohlcv_history_execution" if table_exists(conn, "ohlcv_history_execution") else "ohlcv_history_execution"
    rows = conn.execute(f"""
        SELECT h.symbol, h.bar_time, h.open, h.high, h.low, h.close, h.volume,
               COALESCE(su.sector, 'Unknown') sector
        FROM {source} h
        LEFT JOIN stock_universe su ON su.symbol=h.symbol
        WHERE h.close IS NOT NULL AND h.close > 0
        ORDER BY h.symbol, h.bar_time
    """).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r["symbol"]].append(r)
    return dict(by)


def latest_date(by: Dict[str, List[sqlite3.Row]]) -> str:
    return max(bar_date(rows[-1]["bar_time"]) for rows in by.values() if rows)


def latest_liquidity(conn: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    if not table_exists(conn, "liquidity_profile"):
        return {}
    rows = conn.execute("""
        SELECT lp.*
        FROM liquidity_profile lp
        JOIN (SELECT symbol, MAX(computed_date) d FROM liquidity_profile GROUP BY symbol) x
          ON x.symbol=lp.symbol AND x.d=lp.computed_date
    """).fetchall()
    return {r["symbol"]: r for r in rows}


def vwap_proxy(rows: Sequence[sqlite3.Row], n: int = 21) -> Optional[float]:
    tail = rows[-n:]
    num = 0.0
    den = 0.0
    for r in tail:
        h, l, c, v = sf(r["high"]), sf(r["low"]), sf(r["close"]), sf(r["volume"], 0)
        if h is None or l is None or c is None or not v:
            continue
        typical = (h + l + c) / 3
        num += typical * v
        den += v
    return num / den if den > 0 else None


def anchored_vwap_from_low(rows: Sequence[sqlite3.Row], lookback: int = 120) -> Optional[float]:
    tail = rows[-lookback:]
    if len(tail) < 20:
        return None
    low_i = min(range(len(tail)), key=lambda i: sf(tail[i]["low"], 1e18) or 1e18)
    return vwap_proxy(tail[low_i:], len(tail) - low_i)


def volume_profile_proxy(rows: Sequence[sqlite3.Row], lookback: int = 80) -> Dict[str, Optional[float]]:
    tail = rows[-lookback:]
    if len(tail) < 20:
        return {"poc": None, "hvn": None, "lvn": None}
    closes = [sf(r["close"]) for r in tail if sf(r["close"]) is not None]
    if not closes:
        return {"poc": None, "hvn": None, "lvn": None}
    lo, hi = min(closes), max(closes)
    if hi <= lo:
        return {"poc": closes[-1], "hvn": closes[-1], "lvn": closes[-1]}
    bins = 12
    vol_bins = [0.0] * bins
    for r in tail:
        c = sf(r["close"])
        v = sf(r["volume"], 0) or 0
        if c is None:
            continue
        idx = min(bins - 1, max(0, int((c - lo) / (hi - lo) * bins)))
        vol_bins[idx] += v
    poc_i = max(range(bins), key=lambda i: vol_bins[i])
    nonzero = [(v, i) for i, v in enumerate(vol_bins) if v > 0]
    lvn_i = min(nonzero)[1] if nonzero else poc_i
    def price(i: int) -> float:
        return lo + (i + 0.5) * (hi - lo) / bins
    return {"poc": price(poc_i), "hvn": price(poc_i), "lvn": price(lvn_i)}


def sane_level(level: Optional[float], close: float) -> Optional[float]:
    if level is None or close <= 0:
        return None
    if close * 0.55 <= level <= close * 1.65:
        return level
    return None


def quality_review_needed(rows: Sequence[sqlite3.Row], close: float) -> bool:
    if len(rows) < 30 or close <= 0:
        return True
    tail = rows[-90:]
    for i in range(1, len(tail)):
        prev = sf(tail[i - 1]["close"])
        cur = sf(tail[i]["close"])
        if prev and cur and abs(cur / prev - 1) > 0.35:
            return True
    lows = [sf(r["low"]) for r in rows[-20:] if sf(r["low"]) is not None]
    highs = [sf(r["high"]) for r in rows[-20:] if sf(r["high"]) is not None]
    if lows and min(lows) < close * 0.45:
        return True
    if highs and max(highs) > close * 1.85:
        return True
    return False


def consolidation_score(rows: Sequence[sqlite3.Row]) -> float:
    if len(rows) < 60:
        return 0.0
    atr = atr_pct(rows, 14) or 0
    ranges = []
    for r in rows[-20:]:
        h, l, c = sf(r["high"]), sf(r["low"]), sf(r["close"])
        if h is not None and l is not None and c:
            ranges.append((h - l) / c)
    range20 = mean(ranges) if ranges else 0
    closes = [sf(r["close"], 0) or 0 for r in rows]
    e20 = ema(closes[-60:], 20)
    std20 = 0.0
    if e20:
        vals = closes[-20:]
        std20 = (sum((v - mean(vals)) ** 2 for v in vals) / len(vals)) ** 0.5 / e20
    raw = 100 - (atr * 450 + range20 * 450 + std20 * 500)
    return clamp(raw)


def sector_stats(by: Dict[str, List[sqlite3.Row]]) -> Dict[str, Dict[str, float]]:
    groups = defaultdict(list)
    for sym, rows in by.items():
        if sym.startswith("EGX") or len(rows) < 80:
            continue
        r20 = ret(rows, 20)
        if r20 is None or abs(r20) > 0.55:
            continue
        groups[rows[-1]["sector"] or "Unknown"].append(r20)
    ranked = sorted(((sec, mean(vals)) for sec, vals in groups.items() if vals), key=lambda x: x[1], reverse=True)
    out = {}
    n = max(1, len(ranked) - 1)
    for i, (sec, r20) in enumerate(ranked):
        out[sec] = {"ret20": r20, "score": 100 * (1 - i / n), "rank": i + 1}
    return out


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS egx_x_pro_daily (
      trade_date TEXT NOT NULL,
      symbol TEXT NOT NULL,
      sector TEXT,
      x_score REAL,
      stage TEXT,
      close REAL,
      ema21 REAL,
      ema50 REAL,
      ema200 REAL,
      vwap REAL,
      anchored_vwap REAL,
      volume_poc REAL,
      volume_hvn REAL,
      volume_lvn REAL,
      rs_market_score REAL,
      rs_sector_score REAL,
      rvol5 REAL,
      rvol20 REAL,
      rvol60 REAL,
      liquidity_expansion_score REAL,
      compression_score REAL,
      turnover_score REAL,
      ownership_rotation_score REAL,
      atr_pct REAL,
      entry_price REAL,
      stop_loss REAL,
      target_1 REAL,
      target_2 REAL,
      target_3 REAL,
      rr_ratio REAL,
      flags_json TEXT,
      evidence_json TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      PRIMARY KEY (trade_date, symbol)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_xpro_date_score ON egx_x_pro_daily(trade_date, x_score DESC)")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS egx_x_pro_backtest (
      run_date TEXT PRIMARY KEY,
      sample_signals INTEGER,
      win1 REAL,
      win3 REAL,
      win5 REAL,
      avg1 REAL,
      avg3 REAL,
      avg5 REAL,
      summary_json TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS egx_signal_tracker (
      signal_date TEXT NOT NULL,
      symbol TEXT NOT NULL,
      source TEXT NOT NULL,
      source_score REAL,
      stage TEXT,
      entry_price REAL,
      stop_loss REAL,
      target_1 REAL,
      target_2 REAL,
      target_3 REAL,
      status TEXT DEFAULT 'PENDING',
      hit_stop INTEGER DEFAULT 0,
      hit_t1 INTEGER DEFAULT 0,
      hit_t2 INTEGER DEFAULT 0,
      hit_t3 INTEGER DEFAULT 0,
      return_1d REAL,
      return_3d REAL,
      return_5d REAL,
      outcome_reason TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now')),
      PRIMARY KEY (signal_date, symbol, source)
    )
    """)


def score_symbol(symbol: str, rows: Sequence[sqlite3.Row], market_ret20: float, sector_map: Dict[str, Dict[str, float]], liq: Dict[str, sqlite3.Row]) -> Optional[dict]:
    if symbol.startswith("EGX") or len(rows) < 220:
        return None
    close = sf(rows[-1]["close"])
    if not close:
        return None
    closes = [sf(r["close"], 0) or 0 for r in rows]
    vols = [sf(r["volume"], 0) or 0 for r in rows]
    sector = rows[-1]["sector"] or "Unknown"
    e21, e50, e200 = ema(closes, 21), ema(closes, 50), ema(closes, 200)
    vw = vwap_proxy(rows, 21)
    avw = anchored_vwap_from_low(rows, 120)
    vp = volume_profile_proxy(rows, 80)
    vw = sane_level(vw, close)
    avw = sane_level(avw, close)
    vp = {k: sane_level(v, close) for k, v in vp.items()}
    a = atr_pct(rows, 14) or 0
    needs_quality_review = quality_review_needed(rows, close) or a > 0.12
    r20 = ret(rows, 20) or 0
    sec_ret = sector_map.get(sector, {}).get("ret20", market_ret20)
    sec_strength = sector_map.get(sector, {}).get("score", 45)
    rs_mkt = clamp(50 + (r20 - market_ret20) * 600)
    rs_sec = clamp(50 + (r20 - sec_ret) * 600)
    avg5 = avg(vols[-5:]) or 0
    avg20 = avg(vols[-20:]) or 0
    avg60 = avg(vols[-60:]) or 0
    rvol5 = avg5 / avg20 if avg20 > 0 else 1
    rvol20 = avg20 / avg60 if avg60 > 0 else 1
    rvol60 = vols[-1] / avg60 if avg60 > 0 else 1
    liq_exp = clamp(35 + min(max(rvol5, rvol20, rvol60), 4) * 18)
    comp = consolidation_score(rows)
    value_today = close * vols[-1]
    liq_row = liq.get(symbol)
    advt10 = sf(liq_row["advt_10d"], 0) if liq_row else 0
    turnover = value_today / advt10 if advt10 and advt10 > 0 else rvol60
    turnover_score = clamp(45 + min(turnover, 4) * 14)
    range20 = max(closes[-20:]) - min(closes[-20:])
    range_pct20 = range20 / close if close else 0
    ownership = clamp((min(rvol20, 3) * 30) + (70 - range_pct20 * 450))
    trend = 0
    if e21 and close > e21: trend += 12
    if e50 and close > e50: trend += 18
    if e200 and close > e200: trend += 20
    if e50 and e200 and e50 > e200: trend += 15
    if vw and close > vw: trend += 10
    if avw and close > avw: trend += 15
    if e21 and e50 and e21 > e50: trend += 10
    trend = clamp(trend)
    atr_eff = clamp(90 - abs((a or 0) - 0.045) * 550) if a else 45
    flags = []
    for cond, name in [
        (sec_strength >= 65, "SECTOR_STRONG"),
        (rs_mkt >= 60, "RS_MARKET"),
        (rs_sec >= 60, "RS_SECTOR"),
        (rvol5 >= 1.5 or rvol20 >= 1.5 or rvol60 >= 1.5, "RVOL_EXPANSION"),
        (liq_exp >= 70, "LIQUIDITY_EXPANSION"),
        (comp >= 65, "VOLATILITY_COMPRESSION"),
        (close > (e50 or 1e18), "ABOVE_EMA50"),
        (close > (e200 or 1e18), "ABOVE_EMA200"),
        (vw and close > vw, "ABOVE_VWAP"),
        (avw and close > avw, "ABOVE_ANCHORED_VWAP"),
        (ownership >= 70, "OWNERSHIP_ROTATION"),
        (0.018 <= a <= 0.085, "ATR_OK"),
        (needs_quality_review, "DATA_QUALITY_REVIEW"),
        (a > 0.12, "ATR_TOO_HIGH"),
    ]:
        if cond:
            flags.append(name)
    x_score = (
        rs_mkt * 0.18 + rs_sec * 0.16 + liq_exp * 0.18 + sec_strength * 0.12
        + turnover_score * 0.10 + comp * 0.10 + trend * 0.10 + atr_eff * 0.06
    )
    if needs_quality_review:
        x_score -= 18
    x_score = clamp(x_score)
    if needs_quality_review and x_score >= 66:
        stage = "X_REVIEW_QUALITY"
    elif x_score >= 82 and "ABOVE_EMA200" in flags and "RS_SECTOR" in flags and "LIQUIDITY_EXPANSION" in flags:
        stage = "X_READY"
    elif x_score >= 74:
        stage = "X_WATCH_HIGH"
    elif x_score >= 66:
        stage = "X_WATCH"
    elif comp >= 70 and (rs_sec >= 55 or rs_mkt >= 55):
        stage = "X_BASE_BUILDING"
    else:
        stage = "X_AVOID"
    recent_lows = [sf(r["low"]) for r in rows[-10:] if sf(r["low"]) is not None]
    valid_lows = [x for x in recent_lows if close * 0.72 <= x < close]
    structural_stop = min(valid_lows) if valid_lows else None
    atr_stop = close * (1 - max(0.018, min(a * 2, 0.12)))
    stop = max(structural_stop, atr_stop) if structural_stop and structural_stop < close else atr_stop
    risk = max(close - stop, close * 0.01)
    t1, t2, t3 = close + 2 * risk, close + 3 * risk, close + 5 * risk
    poc = vp["poc"]
    if poc and poc > close:
        t1 = max(t1, poc)
    t2 = max(t2, t1 + risk)
    t3 = max(t3, t2 + 2 * risk)
    rr = (t2 - close) / risk if risk > 0 else 0
    evidence = {
        "ret20": round(r20, 4),
        "market_ret20": round(market_ret20, 4),
        "sector_ret20": round(sec_ret, 4),
        "sector_rank": sector_map.get(sector, {}).get("rank"),
        "trend_score": round(trend, 2),
        "atr_efficiency": round(atr_eff, 2),
        "turnover_proxy": round(turnover, 3),
        "value_today": round(value_today, 2),
    }
    return {
        "trade_date": bar_date(rows[-1]["bar_time"]),
        "symbol": symbol,
        "sector": sector,
        "x_score": round(x_score, 2),
        "stage": stage,
        "close": close,
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "vwap": vw,
        "anchored_vwap": avw,
        "volume_poc": vp["poc"],
        "volume_hvn": vp["hvn"],
        "volume_lvn": vp["lvn"],
        "rs_market_score": round(rs_mkt, 2),
        "rs_sector_score": round(rs_sec, 2),
        "rvol5": round(rvol5, 3),
        "rvol20": round(rvol20, 3),
        "rvol60": round(rvol60, 3),
        "liquidity_expansion_score": round(liq_exp, 2),
        "compression_score": round(comp, 2),
        "turnover_score": round(turnover_score, 2),
        "ownership_rotation_score": round(ownership, 2),
        "atr_pct": round(a, 5),
        "entry_price": close,
        "stop_loss": round(stop, 4),
        "target_1": round(t1, 4),
        "target_2": round(t2, 4),
        "target_3": round(t3, 4),
        "rr_ratio": round(rr, 2),
        "flags_json": json.dumps(flags, ensure_ascii=False),
        "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    }


def run() -> dict:
    conn = connect()
    ensure_tables(conn)
    by = load_bars(conn)
    d = latest_date(by)
    market_rows = by.get("EGX30") or []
    if len(market_rows) >= 30:
        market_ret = ret(market_rows, 20) or 0
    else:
        vals = [ret(rows, 20) for sym, rows in by.items() if not sym.startswith("EGX") and len(rows) >= 80]
        market_ret = median([v for v in vals if v is not None]) if vals else 0
    sec = sector_stats(by)
    liq = latest_liquidity(conn)
    out = []
    for sym, rows in by.items():
        r = score_symbol(sym, rows, market_ret, sec, liq)
        if r and r["trade_date"] == d:
            out.append(r)
    out.sort(key=lambda x: x["x_score"], reverse=True)
    conn.execute("DELETE FROM egx_x_pro_daily WHERE trade_date=?", (d,))
    for r in out:
        conn.execute("""
            INSERT OR REPLACE INTO egx_x_pro_daily
            (trade_date,symbol,sector,x_score,stage,close,ema21,ema50,ema200,vwap,anchored_vwap,
             volume_poc,volume_hvn,volume_lvn,rs_market_score,rs_sector_score,rvol5,rvol20,rvol60,
             liquidity_expansion_score,compression_score,turnover_score,ownership_rotation_score,
             atr_pct,entry_price,stop_loss,target_1,target_2,target_3,rr_ratio,flags_json,evidence_json)
            VALUES
            (:trade_date,:symbol,:sector,:x_score,:stage,:close,:ema21,:ema50,:ema200,:vwap,:anchored_vwap,
             :volume_poc,:volume_hvn,:volume_lvn,:rs_market_score,:rs_sector_score,:rvol5,:rvol20,:rvol60,
             :liquidity_expansion_score,:compression_score,:turnover_score,:ownership_rotation_score,
             :atr_pct,:entry_price,:stop_loss,:target_1,:target_2,:target_3,:rr_ratio,:flags_json,:evidence_json)
        """, r)
    seed_tracker(conn, d)
    conn.commit()
    counts = dict(conn.execute("SELECT stage, COUNT(*) FROM egx_x_pro_daily WHERE trade_date=? GROUP BY stage", (d,)).fetchall())
    result = {"success": True, "trade_date": d, "symbols_scored": len(out), "stage_counts": counts, "top": out[:15]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def seed_tracker(conn: sqlite3.Connection, d: str) -> int:
    conn.execute("DELETE FROM egx_signal_tracker WHERE signal_date=? AND source='EGX_X_PRO'", (d,))
    rows = conn.execute("""
        SELECT * FROM egx_x_pro_daily
        WHERE trade_date=? AND stage IN ('X_READY','X_WATCH_HIGH','X_BASE_BUILDING')
        ORDER BY x_score DESC LIMIT 40
    """, (d,)).fetchall()
    n = 0
    for r in rows:
        conn.execute("""
            INSERT OR IGNORE INTO egx_signal_tracker
            (signal_date, symbol, source, source_score, stage, entry_price, stop_loss, target_1, target_2, target_3)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (d, r["symbol"], "EGX_X_PRO", r["x_score"], r["stage"], r["entry_price"], r["stop_loss"], r["target_1"], r["target_2"], r["target_3"]))
        n += 1
    return n


def update_tracker() -> dict:
    conn = connect()
    ensure_tables(conn)
    rows = conn.execute("SELECT * FROM egx_signal_tracker WHERE status='PENDING'").fetchall()
    updated = 0
    for r in rows:
        bars = conn.execute("""
            SELECT date(bar_time,'unixepoch') d, high, low, close
            FROM ohlcv_history_execution
            WHERE symbol=? AND date(bar_time,'unixepoch') > ?
            ORDER BY bar_time LIMIT 12
        """, (r["symbol"], r["signal_date"])).fetchall()
        if not bars:
            continue
        entry = sf(r["entry_price"])
        if not entry:
            continue
        hit_stop = hit_t1 = hit_t2 = hit_t3 = 0
        reason = None
        for b in bars:
            h, l = sf(b["high"]), sf(b["low"])
            if l is not None and r["stop_loss"] is not None and l <= r["stop_loss"]:
                hit_stop = 1
                reason = "STOP"
                break
            if h is not None and r["target_1"] is not None and h >= r["target_1"]:
                hit_t1 = 1
                reason = reason or "T1"
            if h is not None and r["target_2"] is not None and h >= r["target_2"]:
                hit_t2 = 1
                reason = "T2"
            if h is not None and r["target_3"] is not None and h >= r["target_3"]:
                hit_t3 = 1
                reason = "T3"
        def rr(i):
            return (sf(bars[i]["close"]) / entry - 1) if len(bars) > i and sf(bars[i]["close"]) else None
        r1, r3, r5 = rr(0), rr(2), rr(4)
        status = "COMPLETED" if len(bars) >= 5 or hit_stop or hit_t1 else "PENDING"
        conn.execute("""
            UPDATE egx_signal_tracker
            SET status=?, hit_stop=?, hit_t1=?, hit_t2=?, hit_t3=?,
                return_1d=?, return_3d=?, return_5d=?, outcome_reason=?,
                updated_at=datetime('now')
            WHERE signal_date=? AND symbol=? AND source=?
        """, (status, hit_stop, hit_t1, hit_t2, hit_t3, r1, r3, r5, reason,
              r["signal_date"], r["symbol"], r["source"]))
        updated += 1
    conn.commit()
    out = {"success": True, "pending_checked": len(rows), "updated": updated}
    print(json.dumps(out, indent=2))
    return out


def backtest(max_signals: int = 1200) -> dict:
    conn = connect()
    ensure_tables(conn)
    by = load_bars(conn)
    candidates = []
    for sym, rows in by.items():
        if sym.startswith("EGX") or len(rows) < 260:
            continue
        for i in range(220, len(rows) - 6, 5):
            hist = rows[: i + 1]
            market_ret = 0.0
            sec = {hist[-1]["sector"] or "Unknown": {"ret20": 0.0, "score": 60, "rank": 1}}
            r = score_symbol(sym, hist, market_ret, sec, {})
            if r and r["x_score"] >= 74:
                entry = sf(rows[i]["close"])
                future = rows[i + 1:i + 6]
                clean_future = True
                for j in range(1, len(future)):
                    prev = sf(future[j - 1]["close"])
                    cur = sf(future[j]["close"])
                    if prev and cur and abs(cur / prev - 1) > 0.35:
                        clean_future = False
                        break
                c1, c3, c5 = sf(rows[i + 1]["close"]), sf(rows[i + 3]["close"]), sf(rows[i + 5]["close"])
                if entry and c5 and abs(c5 / entry - 1) > 0.80:
                    clean_future = False
                if not clean_future:
                    continue
                if entry and c5:
                    candidates.append({
                        "symbol": sym, "date": bar_date(rows[i]["bar_time"]), "score": r["x_score"],
                        "r1": c1 / entry - 1 if c1 else None,
                        "r3": c3 / entry - 1 if c3 else None,
                        "r5": c5 / entry - 1,
                    })
    candidates.sort(key=lambda x: (x["date"], x["score"]), reverse=True)
    candidates = candidates[:max_signals]
    def stat(key):
        vals = [x[key] for x in candidates if x[key] is not None]
        return {
            f"win{key[-1]}": round(sum(v > 0 for v in vals) / len(vals), 3) if vals else None,
            f"avg{key[-1]}": round(mean(vals), 4) if vals else None,
        }
    s1, s3, s5 = stat("r1"), stat("r3"), stat("r5")
    summary = {"top_examples": candidates[:20]}
    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT OR REPLACE INTO egx_x_pro_backtest
        (run_date, sample_signals, win1, win3, win5, avg1, avg3, avg5, summary_json)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (run_date, len(candidates), s1["win1"], s3["win3"], s5["win5"], s1["avg1"], s3["avg3"], s5["avg5"], json.dumps(summary)))
    conn.commit()
    out = {"success": True, "sample_signals": len(candidates), **s1, **s3, **s5}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def report(limit: int = 20) -> None:
    conn = connect()
    if not table_exists(conn, "egx_x_pro_daily"):
        print("EGX-X Pro not built yet")
        return
    d = conn.execute("SELECT MAX(trade_date) d FROM egx_x_pro_daily").fetchone()["d"]
    rows = conn.execute("""
        SELECT symbol, sector, x_score, stage, rvol5, rvol60, rs_market_score, rs_sector_score, flags_json
        FROM egx_x_pro_daily WHERE trade_date=?
        ORDER BY x_score DESC LIMIT ?
    """, (d, limit)).fetchall()
    print(f"EGX-X Pro — {d}")
    for i, r in enumerate(rows, 1):
        flags = ", ".join(json.loads(r["flags_json"] or "[]")[:5])
        print(f"{i:02d}. {r['symbol']:6s} {r['x_score']:5.1f} {r['stage']:15s} RVOL5={r['rvol5']:.2f} RS={r['rs_market_score']:.0f}/{r['rs_sector_score']:.0f} | {flags}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "report":
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif cmd == "backtest":
        backtest()
    elif cmd == "track":
        update_tracker()
    else:
        raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
