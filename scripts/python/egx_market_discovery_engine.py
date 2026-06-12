#!/usr/bin/env python3
"""
EGX Market Discovery Engine (MDE) — Phase 1: Discovery Brain + Scoring + Setups (Shadow).

Additive discovery only. Does NOT modify UES, promotion, or client path.
Outputs: egx_market_discovery_daily + data/mde_shadow_last.json
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
SHADOW_PATH = DATA / "mde_shadow_last.json"
LAST_PATH = DATA / "egx_market_discovery_last.json"

WEIGHTS_VERSION = "manual_v1"
MIN_BARS = 80
MIN_TURNOVER_EGP = 2_000_000  # medium portfolio default
LIQUIDITY_GATE_EGP = MIN_TURNOVER_EGP

WEIGHTS = {
    "fundamental": 0.18,
    "accumulation": 0.14,
    "impact": 0.12,
    "absorption": 0.10,
    "supply_exhaustion": 0.10,
    "vpin": 0.08,
    "resilience": 0.08,
    "sector": 0.08,
    "catalyst": 0.07,
    "technical": 0.05,
}

SETUP_IDS = (
    "accum_breakout",
    "pullback_accum",
    "failed_breakdown",
    "sector_follower",
    "absorption_pre_break",
    "impact_expansion",
)


def sf(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
    )


def load_bars(conn: sqlite3.Connection) -> Dict[str, List[dict]]:
    src = "ohlcv_history_execution" if table_exists(conn, "ohlcv_history_execution") else "ohlcv_history"
    rows = conn.execute(
        f"""
        SELECT symbol, date(bar_time,'unixepoch') AS d, open, high, low, close, volume
        FROM {src}
        WHERE close IS NOT NULL AND close > 0
        ORDER BY symbol, bar_time
        """
    ).fetchall()
    out: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        out[r["symbol"]].append(
            {
                "date": r["d"],
                "open": sf(r["open"], 0) or 0,
                "high": sf(r["high"], 0) or 0,
                "low": sf(r["low"], 0) or 0,
                "close": sf(r["close"], 0) or 0,
                "volume": sf(r["volume"], 0) or 0,
            }
        )
    return dict(out)


def ema(vals: Sequence[float], n: int) -> Optional[float]:
    vals = [float(v) for v in vals if sf(v) is not None]
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = mean(vals[:n])
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def atr_pct(bars: Sequence[dict], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, l = bars[i]["high"], bars[i]["low"]
        pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c = bars[-1]["close"]
    return mean(trs) / c if trs and c else None


def z_score(val: Optional[float], population: List[float]) -> float:
    if val is None or len(population) < 8:
        return 0.0
    mu = mean(population)
    sd = pstdev(population) if len(population) > 1 else 1.0
    if sd < 1e-9:
        return 0.0
    return (val - mu) / sd


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egx_market_discovery_daily (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            discovery_score REAL,
            confidence_score REAL,
            effective_score REAL,
            mde_stage TEXT,
            mde_setup TEXT,
            hidden_repricing INTEGER DEFAULT 0,
            fundamental_repricing_score REAL,
            liquidity_regime_score REAL,
            price_impact_score REAL,
            absorption_score REAL,
            supply_exhaustion_score REAL,
            vpin_proxy_score REAL,
            resilience_score REAL,
            latent_accumulation_score REAL,
            sector_rotation_score REAL,
            catalyst_score REAL,
            technical_trigger_score REAL,
            pre_explosion_multiplier REAL,
            gates_passed_json TEXT,
            setups_json TEXT,
            metrics_json TEXT,
            weights_version TEXT,
            created_at TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mde_date_score "
        "ON egx_market_discovery_daily(trade_date, effective_score DESC)"
    )
    conn.commit()


def load_aux(conn: sqlite3.Connection, trade_date: str) -> dict:
    aux: dict = {"financial": {}, "tv": {}, "sector": {}, "pine": {}}
    if table_exists(conn, "financial_data"):
        for r in conn.execute("SELECT * FROM financial_data").fetchall():
            aux["financial"][r["symbol"]] = dict(r)
    if table_exists(conn, "tv_discovery_features"):
        for r in conn.execute(
            "SELECT * FROM tv_discovery_features WHERE trade_date=?", (trade_date,)
        ).fetchall():
            aux["tv"][r["symbol"]] = dict(r)
    if table_exists(conn, "stock_universe"):
        for r in conn.execute("SELECT symbol, sector FROM stock_universe").fetchall():
            aux["sector"][r["symbol"]] = r["sector"]
    if table_exists(conn, "pine_analytics"):
        for r in conn.execute(
            "SELECT * FROM pine_analytics WHERE trade_date=?", (trade_date,)
        ).fetchall():
            aux["pine"][r["symbol"]] = dict(r)
    return aux


def compute_bench_returns(
    by_sym: Dict[str, List[dict]], trade_date: str, sectors: Dict[str, str], n: int = 20
) -> dict:
    """Benchmark returns for RS (EGX100 proxy = median liquid names)."""
    rets = []
    sector_rets: Dict[str, List[float]] = defaultdict(list)
    for sym, bars in by_sym.items():
        if sym.startswith("EGX"):
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
        if idx is None or idx < n:
            continue
        c0, c1 = bars[idx - n]["close"], bars[idx]["close"]
        if c0 > 0:
            r = c1 / c0 - 1
            rets.append(r)
            sec = sectors.get(sym) or "Unknown"
            sector_rets[sec].append(r)
    return {
        "bench_ret20": median_safe(rets),
        "sector_rets": {k: median_safe(v) for k, v in sector_rets.items()},
    }


def median_safe(vals: List[float]) -> float:
    vals = [v for v in vals if v is not None]
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def obv_slope(bars: Sequence[dict], n: int = 20) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    obv = 0.0
    series = []
    for i in range(len(bars) - n, len(bars)):
        if i == 0:
            series.append(0.0)
            continue
        if bars[i]["close"] > bars[i - 1]["close"]:
            obv += bars[i]["volume"]
        elif bars[i]["close"] < bars[i - 1]["close"]:
            obv -= bars[i]["volume"]
        series.append(obv)
    if len(series) < 2:
        return None
    return (series[-1] - series[0]) / max(abs(series[0]), 1.0)


def compute_symbol_metrics(
    sym: str,
    bars: List[dict],
    trade_date: str,
    aux: dict,
    bench: dict,
) -> Optional[dict]:
    idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
    if idx is None or idx < MIN_BARS:
        return None

    window = bars[: idx + 1]
    b = window[-1]
    prev = window[-2]
    close, vol = b["close"], b["volume"]
    if close <= 0:
        return None

    ret_d = close / prev["close"] - 1 if prev["close"] > 0 else 0.0
    turnover = close * vol
    turns_20 = [window[i]["close"] * window[i]["volume"] for i in range(max(0, idx - 19), idx + 1)]
    avg_turn_20 = mean(turns_20) if turns_20 else 0.0
    rel_turn = turnover / avg_turn_20 if avg_turn_20 > 0 else 0.0

    rng = max(b["high"] - b["low"], 1e-9)
    clv = (close - b["low"]) / rng

    vwap_proxy = (b["high"] + b["low"] + close) / 3
    signed_flow = (1 if close >= vwap_proxy else -1) * turnover

    csf_5 = sum(
        (1 if window[i]["close"] >= (window[i]["high"] + window[i]["low"] + window[i]["close"]) / 3 else -1)
        * window[i]["close"]
        * window[i]["volume"]
        for i in range(max(0, idx - 4), idx + 1)
    )
    csf_20 = sum(
        (1 if window[i]["close"] >= (window[i]["high"] + window[i]["low"] + window[i]["close"]) / 3 else -1)
        * window[i]["close"]
        * window[i]["volume"]
        for i in range(max(0, idx - 19), idx + 1)
    )

    kyle = abs(ret_d) / max(turnover / 1_000_000, 1e-6)
    kyle_5, kyle_60 = [], []
    for i in range(max(1, idx - 4), idx + 1):
        r = window[i]["close"] / window[i - 1]["close"] - 1
        t = window[i]["close"] * window[i]["volume"]
        kyle_5.append(abs(r) / max(t / 1_000_000, 1e-6))
    for i in range(max(1, idx - 59), idx + 1):
        r = window[i]["close"] / window[i - 1]["close"] - 1
        t = window[i]["close"] * window[i]["volume"]
        kyle_60.append(abs(r) / max(t / 1_000_000, 1e-6))
    kyle_5m = mean(kyle_5) if kyle_5 else kyle
    kyle_60m = mean(kyle_60) if kyle_60 else kyle
    impact_expansion = kyle_5m / kyle_60m if kyle_60m > 1e-9 else 1.0

    atr_v = atr_pct(window) or 0.03
    absorption_ratio = turnover / max(abs(ret_d), 0.002)

    buy_vol = vol * clv
    sell_vol = vol * (1 - clv)
    vol_imb = abs(buy_vol - sell_vol) / max(vol, 1)
    vpin_10 = []
    for i in range(max(0, idx - 9), idx + 1):
        ri = window[i]
        rrng = max(ri["high"] - ri["low"], 1e-9)
        rclv = (ri["close"] - ri["low"]) / rrng
        rv = ri["volume"]
        vpin_10.append(abs(rv * rclv - rv * (1 - rclv)) / max(rv, 1))
    vpin_proxy = mean(vpin_10) if vpin_10 else vol_imb

    # Supply exhaustion
    down_failure = b["low"] < prev["low"] and close > prev["close"] and clv > 0.60
    lower_wick = min(b["open"], close) - b["low"]
    lower_wick_ratio = lower_wick / rng

    # Resilience (shock + next bar if exists)
    tr = b["high"] - b["low"]
    atr20 = atr_pct(window[:-1], 20) or atr_v
    shock = rel_turn > 2.0 or (tr / close > 1.5 * atr20)
    pos_resilience = False
    neg_resilience = False
    if shock and ret_d > 0 and idx + 1 < len(bars):
        nxt = bars[idx + 1]
        if nxt["date"] <= trade_date:
            pass
        else:
            mid = (b["high"] + b["low"]) / 2
            if nxt["low"] > mid and nxt["close"] >= close * 0.98:
                pos_resilience = True
            if nxt["close"] < b["low"]:
                neg_resilience = True

    closes = [x["close"] for x in window]
    highs_20 = max(closes[-20:])
    highs_60 = max(closes[-60:])
    lows_20 = min(closes[-20:])
    lows_60 = min(closes[-60:])
    ma20 = mean(closes[-20:])
    ma50 = mean(closes[-50:]) if len(closes) >= 50 else ma20
    ret_20 = close / closes[-21] - 1 if len(closes) >= 21 else 0.0

    dd_60 = (highs_60 - close) / highs_60 if highs_60 > 0 else 0.0
    extension = (close - ma50) / ma50 if ma50 > 0 else 0.0

    obv_sl = obv_slope(window, 20) or 0.0
    downside_vol = []
    for i in range(max(1, idx - 19), idx + 1):
        r = window[i]["close"] / window[i - 1]["close"] - 1
        if r < 0:
            downside_vol.append(abs(r))
    down_vol = mean(downside_vol) if downside_vol else 0.0

    sec = aux["sector"].get(sym, "Unknown")
    bench_ret = bench.get("bench_ret20", 0.0)
    sec_ret = bench.get("sector_rets", {}).get(sec, bench_ret)
    rs_stock = ret_20 - bench_ret
    rs_vs_sector = ret_20 - sec_ret

    fin = aux["financial"].get(sym, {})
    pe = sf(fin.get("pe_ratio"))
    roe = sf(fin.get("roe"))
    de = sf(fin.get("debt_to_equity"))
    fcf = sf(fin.get("free_cashflow"))
    div_y = sf(fin.get("dividend_yield"))
    rev_g = sf(fin.get("revenue_growth"))

    tv = aux["tv"].get(sym, {})
    pine = aux["pine"].get(sym, {})
    rs_pine = sf(pine.get("rs_score"), 50.0) or 50.0

    # --- Layer scores 0-100 ---
    liquidity_score = clamp(30 + min(rel_turn, 4) * 15 + (20 if avg_turn_20 >= LIQUIDITY_GATE_EGP else 0))

    impact_score = clamp(40 + min(impact_expansion, 2.5) * 20 + (15 if ret_d > 0 and kyle > kyle_60m else 0))

    absorption_score = clamp(
        35
        + (25 if rel_turn > 1.5 and abs(ret_d) < atr_v else 0)
        + (20 if clv > 0.60 else 0)
        + min(absorption_ratio / 1e8, 25)
    )

    supply_score = clamp(
        30 + (30 if down_failure else 0) + (25 if lower_wick_ratio > 0.45 and close > b["open"] else 0)
    )

    vpin_score = clamp(40 + vpin_proxy * 40 + (10 if clv > 0.55 and ret_d > 0 else 0))

    resilience_score = clamp(50 + (35 if pos_resilience else 0) - (40 if neg_resilience else 0))

    accum_raw = (
        (1 if csf_20 > 0 else 0) * 25
        + (15 if obv_sl > 0 else 0)
        + (15 if close > ma20 else 0)
        + (15 if dd_60 < 0.15 else 0)
        - (20 if extension > 0.20 else 0)
    )
    accum_score = clamp(accum_raw + min(rel_turn, 3) * 10)

    sector_score = clamp(45 + rs_stock * 200 + rs_vs_sector * 100 + (rs_pine - 50) * 0.5)

    fundamental_score = 50.0
    if pe and pe > 0:
        fundamental_score += clamp(80 / pe, 0, 20)
    if roe:
        fundamental_score += clamp(roe, 0, 20)
    if de is not None:
        fundamental_score -= clamp(de / 2, 0, 25)
    if fcf and fcf > 0:
        fundamental_score += 10
    if div_y:
        fundamental_score += clamp(div_y * 5, 0, 10)
    if rev_g:
        fundamental_score += clamp(rev_g, 0, 15)
    fundamental_score = clamp(fundamental_score)

    catalyst_score = 50.0  # placeholder v1
    if rev_g and rev_g > 0.10:
        catalyst_score += 15
    if div_y and div_y > 0.03:
        catalyst_score += 10
    catalyst_score = clamp(catalyst_score)

    tech_score = clamp(
        40
        + (20 if close > highs_60 * 0.94 else 0)
        + (15 if close > ma50 else 0)
        + (15 if rel_turn > 1.3 and clv > 0.65 else 0)
    )

    # Gates (MDE internal only)
    gates = {
        "liquidity": avg_turn_20 >= LIQUIDITY_GATE_EGP,
        "dead_stock": rel_turn > 0 or avg_turn_20 > 0,
        "balance_sheet": de is None or de < 3.5,
        "manipulation": not (rel_turn > 3 and abs(ret_d) < 0.005 and clv < 0.4),
        "extension": extension < 0.25 and ret_20 < 0.40,
    }
    gates_pass = all(gates.values())

    # Hidden repricing: at least 2 signals
    hr_signals = [
        kyle > kyle_60m * 1.1 and abs(ret_d) < atr_v and csf_20 > 0,
        rel_turn > 1.3 and abs(ret_d) < atr_v * 1.2 and clv > 0.55,
        impact_expansion > 1.15 and csf_20 > 0 and extension < 0.15,
        absorption_ratio > avg_turn_20 / max(abs(ret_d), 0.002) * 0.3 and clv > 0.6,
        down_failure or (lower_wick_ratio > 0.45 and rel_turn > 1.2),
        pos_resilience,
    ]
    hidden_repricing = sum(1 for x in hr_signals if x) >= 2

    # Pre-explosion multiplier (conceptual product / risks)
    def norm_score(s: float) -> float:
        return max(0.05, s / 100.0)

    manip_risk = 1.0 + (0.5 if not gates["manipulation"] else 0) + (0.3 if not gates["extension"] else 0)
    bs_risk = 1.0 + (0.4 if not gates["balance_sheet"] else 0)
    ext_risk = 1.0 + max(0, extension - 0.15) * 3

    pre_explosion = (
        norm_score(fundamental_score)
        * norm_score(liquidity_score)
        * norm_score(impact_score)
        * norm_score(absorption_score)
        * norm_score(supply_score)
        * norm_score(accum_score)
        * norm_score(resilience_score)
        * norm_score(sector_score)
        * norm_score(catalyst_score)
        / max(manip_risk * bs_risk * ext_risk, 0.1)
    )

    discovery = (
        WEIGHTS["fundamental"] * fundamental_score
        + WEIGHTS["accumulation"] * accum_score
        + WEIGHTS["impact"] * impact_score
        + WEIGHTS["absorption"] * absorption_score
        + WEIGHTS["supply_exhaustion"] * supply_score
        + WEIGHTS["vpin"] * vpin_score
        + WEIGHTS["resilience"] * resilience_score
        + WEIGHTS["sector"] * sector_score
        + WEIGHTS["catalyst"] * catalyst_score
        + WEIGHTS["technical"] * tech_score
    )

    # Confidence
    conf = 50.0
    if fin:
        conf += 15
    if tv:
        conf += 10
    if pine:
        conf += 5
    if avg_turn_20 >= LIQUIDITY_GATE_EGP * 1.5:
        conf += 10
    if idx >= 120:
        conf += 5
    if hidden_repricing:
        conf += 8
    if sum(1 for x in hr_signals if x) >= 3:
        conf += 7
    if not fin:
        conf -= 12
    if avg_turn_20 < LIQUIDITY_GATE_EGP:
        conf -= 20
    confidence = clamp(conf)

    # Setups (Part 3 — definitions only, testable flags)
    setups: List[str] = []
    if close > highs_60 and rel_turn > 1.5 and clv > 0.70:
        setups.append("accum_breakout")
    if close > ma50 and close < ma20 * 1.03 and rel_turn < 1.2 and csf_20 > 0 and lower_wick_ratio > 0.35:
        setups.append("pullback_accum")
    if b["low"] < lows_20 and close > lows_20 and rel_turn > 1.3 and lower_wick_ratio > 0.45:
        setups.append("failed_breakdown")
    if accum_score >= 70 and close < highs_60 * 0.98 and rs_stock > 0:
        setups.append("sector_follower")
    if rel_turn > 1.5 and abs(ret_d) < atr_v and clv > 0.60 and close < highs_60 * 0.97:
        setups.append("absorption_pre_break")
    if impact_expansion > 1.2 and csf_20 > 0 and extension < 0.15:
        setups.append("impact_expansion")

    primary_setup = setups[0] if setups else None

    # Phase 2.6 — optional behavioral memory (EGX_MDE_BEHAVIOR_MEMORY=1, default off)
    try:
        from mde_behavior_memory import apply_confidence_adjustment
        confidence, _behavior_notes = apply_confidence_adjustment(
            sym, setups, confidence, hidden_repricing=hidden_repricing
        )
    except Exception:
        pass
    effective = discovery * (confidence / 100.0)

    # Stage
    if discovery >= 80 and effective >= 70 and gates_pass:
        stage = "INSTITUTIONAL_DISCOVERY"
    elif 70 <= discovery < 80 and effective >= 60:
        stage = "WATCH_TO_BUY"
    elif 60 <= discovery < 70 and accum_score >= 65:
        stage = "EARLY_ACCUMULATION"
    else:
        stage = "REJECT"

    metrics = {
        "return_d": round(ret_d, 5),
        "turnover": round(turnover, 0),
        "rel_turn": round(rel_turn, 3),
        "kyle_lambda": round(kyle, 6),
        "impact_expansion": round(impact_expansion, 3),
        "csf_20": round(csf_20, 0),
        "clv": round(clv, 3),
        "vpin_proxy": round(vpin_proxy, 3),
        "absorption_ratio": round(absorption_ratio, 0),
        "rs_20": round(rs_stock, 4),
        "hidden_repricing_signals": sum(1 for x in hr_signals if x),
        "sector": sec,
    }

    return {
        "symbol": sym,
        "trade_date": trade_date,
        "discovery_score": round(discovery, 2),
        "confidence_score": round(confidence, 2),
        "effective_score": round(effective, 2),
        "mde_stage": stage,
        "mde_setup": primary_setup,
        "hidden_repricing": int(hidden_repricing),
        "fundamental_repricing_score": round(fundamental_score, 2),
        "liquidity_regime_score": round(liquidity_score, 2),
        "price_impact_score": round(impact_score, 2),
        "absorption_score": round(absorption_score, 2),
        "supply_exhaustion_score": round(supply_score, 2),
        "vpin_proxy_score": round(vpin_score, 2),
        "resilience_score": round(resilience_score, 2),
        "latent_accumulation_score": round(accum_score, 2),
        "sector_rotation_score": round(sector_score, 2),
        "catalyst_score": round(catalyst_score, 2),
        "technical_trigger_score": round(tech_score, 2),
        "pre_explosion_multiplier": round(pre_explosion, 4),
        "gates_passed_json": json.dumps(gates),
        "setups_json": json.dumps(setups),
        "metrics_json": json.dumps(metrics),
        "weights_version": WEIGHTS_VERSION,
        "gates_pass": gates_pass,
        "setups": setups,
        "metrics": metrics,
    }


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    trade_date = params.get("trade_date") or params.get("date")
    conn = connect()
    ensure_table(conn)
    by_sym = load_bars(conn)

    if not by_sym:
        raise SystemExit("No OHLCV data")

    if not trade_date:
        trade_date = max(bars[-1]["date"] for bars in by_sym.values() if bars)

    aux = load_aux(conn, trade_date)
    bench = compute_bench_returns(by_sym, trade_date, aux["sector"])

    rows_out: List[dict] = []
    for sym, bars in by_sym.items():
        if sym.startswith("EGX") or len(bars) < MIN_BARS:
            continue
        row = compute_symbol_metrics(sym, bars, trade_date, aux, bench)
        if row:
            rows_out.append(row)

    conn.execute("DELETE FROM egx_market_discovery_daily WHERE trade_date=?", (trade_date,))
    for r in rows_out:
        conn.execute(
            """
            INSERT OR REPLACE INTO egx_market_discovery_daily
            (symbol, trade_date, discovery_score, confidence_score, effective_score,
             mde_stage, mde_setup, hidden_repricing,
             fundamental_repricing_score, liquidity_regime_score, price_impact_score,
             absorption_score, supply_exhaustion_score, vpin_proxy_score, resilience_score,
             latent_accumulation_score, sector_rotation_score, catalyst_score,
             technical_trigger_score, pre_explosion_multiplier,
             gates_passed_json, setups_json, metrics_json, weights_version, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r["symbol"], r["trade_date"], r["discovery_score"], r["confidence_score"],
                r["effective_score"], r["mde_stage"], r["mde_setup"], r["hidden_repricing"],
                r["fundamental_repricing_score"], r["liquidity_regime_score"], r["price_impact_score"],
                r["absorption_score"], r["supply_exhaustion_score"], r["vpin_proxy_score"],
                r["resilience_score"], r["latent_accumulation_score"], r["sector_rotation_score"],
                r["catalyst_score"], r["technical_trigger_score"], r["pre_explosion_multiplier"],
                r["gates_passed_json"], r["setups_json"], r["metrics_json"], r["weights_version"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    conn.commit()

    stages = defaultdict(int)
    setup_counts = defaultdict(int)
    hidden_n = 0
    for r in rows_out:
        stages[r["mde_stage"]] += 1
        if r["hidden_repricing"]:
            hidden_n += 1
        for s in r["setups"]:
            setup_counts[s] += 1

    top = sorted(rows_out, key=lambda x: (-x["effective_score"], -x["discovery_score"]))[:25]
    shadow = {
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "mode": "shadow",
        "weights_version": WEIGHTS_VERSION,
        "egx_mde_shadow": os.environ.get("EGX_MDE_SHADOW", "1"),
        "egx_mde_opp_boost": os.environ.get("EGX_MDE_OPP_BOOST", "0"),
        "summary": {
            "symbols_scored": len(rows_out),
            "hidden_repricing": hidden_n,
            "by_stage": dict(stages),
            "by_setup": dict(setup_counts),
        },
        "top_candidates": [
            {
                "symbol": t["symbol"],
                "discovery_score": t["discovery_score"],
                "confidence_score": t["confidence_score"],
                "effective_score": t["effective_score"],
                "mde_stage": t["mde_stage"],
                "hidden_repricing": bool(t["hidden_repricing"]),
                "setups": t["setups"],
                "metrics": t["metrics"],
            }
            for t in top
        ],
    }

    SHADOW_PATH.write_text(json.dumps(shadow, indent=2), encoding="utf-8")
    result = {
        "success": True,
        "trade_date": trade_date,
        "symbols_scored": len(rows_out),
        "shadow_path": str(SHADOW_PATH.relative_to(ROOT)),
        "summary": shadow["summary"],
        "top5": [t["symbol"] for t in top[:5]],
    }
    LAST_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    conn.close()
    return result


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        params = {}
        if len(sys.argv) > 2:
            try:
                params = json.loads(sys.argv[2])
            except json.JSONDecodeError:
                params = {"trade_date": sys.argv[2]}
        out = run(params)
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
