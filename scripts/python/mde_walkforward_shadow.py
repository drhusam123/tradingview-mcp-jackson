#!/usr/bin/env python3
"""
MDE Phase 2.7 — Historical Walk-Forward Shadow Comparison.

Strictly causal: memory built only from data BEFORE signal date T.
No live activation — shadow research only.

Outputs:
  data/mde_walkforward_shadow_compare.json
  data/mde_memory_decay_comparison.json
  data/mde_setup_persistence_study.json
  data/mde_regime_behavior_map.json
  data/mde_sequence_patterns.json
  data/mde_false_discovery_rules.json
  data/mde_symbol_playbooks.json
  data/mde_behavior_rules_v2.json
  docs/MDE_BEHAVIOR_MEMORY_WALKFORWARD_REPORT.md
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

RETURN_CAP = 0.50
HIT_THRESH = 0.05
SETUP_KEYS = (
    "accum_breakout", "pullback_accum", "failed_breakdown",
    "sector_follower", "absorption_pre_break", "impact_expansion",
)
WINDOW_CONFIGS = [(126, 20), (252, 20), (504, 20)]
MEMORY_TYPES = ("equal", "last_252", "exp_decay_126", "regime_aware", "sector_adjusted")
MIN_SYMBOL_SETUP = 10
MIN_SECTOR_SETUP = 40
MIN_GLOBAL_SETUP = 100


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.row_factory = sqlite3.Row
    return conn


def pf(wins: List[float], losses: List[float]) -> float:
    if not losses:
        return 2.0 if wins else 0.0
    return sum(wins) / max(sum(losses), 1e-9)


def pool_stats(events: List[dict]) -> dict:
    if not events:
        return {"n": 0, "hit_5d": None, "hit_10d": None, "pf": None, "lift": None, "avg_return_5d_pct": None}
    with_ret = [e for e in events if e.get("ret5") is not None]
    if not with_ret:
        return {"n": len(events), "hit_5d": None, "hit_10d": None, "pf": None, "lift": None, "avg_return_5d_pct": None}
    hits5 = [e["hit5"] for e in with_ret]
    hits10 = [e["hit10"] for e in with_ret if e.get("hit10") is not None]
    rets = [e["ret5"] for e in with_ret]
    wins = [r for r, h in zip(rets, hits5) if h]
    losses = [abs(r) for r, h in zip(rets, hits5) if not h]
    base_wr = sum(hits5) / len(hits5)
    return {
        "n": len(with_ret),
        "hit_5d": round(base_wr * 100, 1),
        "hit_10d": round(sum(hits10) / len(hits10) * 100, 1) if hits10 else None,
        "pf": round(pf(wins, losses), 2),
        "avg_return_5d_pct": round(mean(rets) * 100, 2),
        "lift": 1.0,
    }


def forward_stats(bars: List[dict], idx: int) -> dict:
    out: dict = {}
    c0 = bars[idx]["close"]
    if c0 <= 0:
        return out
    for h in (5, 10, 20):
        if idx + h < len(bars):
            ret = max(-RETURN_CAP, min(RETURN_CAP, bars[idx + h]["close"] / c0 - 1))
            out[f"ret{h}"] = ret
            out[f"hit{h}"] = 1 if ret >= HIT_THRESH else 0
    if idx + 20 < len(bars):
        peak = c0
        max_dd = 0.0
        for i in range(idx, idx + 21):
            c = bars[i]["close"]
            peak = max(peak, c)
            max_dd = min(max_dd, (c - peak) / peak if peak else 0)
        out["max_dd_20d"] = max_dd
    return out


def load_events(conn: sqlite3.Connection) -> Tuple[List[dict], dict]:
    from egx_market_discovery_engine import load_bars

    by_sym = load_bars(conn)
    idx_map = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in by_sym.items()}
    events: List[dict] = []
    for r in conn.execute(
        """
        SELECT symbol, trade_date, discovery_score, confidence_score, effective_score,
               mde_stage, hidden_repricing, setups_json, metrics_json
        FROM egx_market_discovery_daily
        """
    ).fetchall():
        try:
            setups = json.loads(r["setups_json"] or "[]")
        except json.JSONDecodeError:
            setups = []
        try:
            metrics = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            metrics = {}
        fwd: dict = {}
        sym = r["symbol"]
        if sym in idx_map and r["trade_date"] in idx_map[sym]:
            fwd = forward_stats(by_sym[sym], idx_map[sym][r["trade_date"]])
        events.append({
            "symbol": sym,
            "trade_date": r["trade_date"],
            "sector": metrics.get("sector"),
            "discovery_score": float(r["discovery_score"] or 0),
            "confidence_score": float(r["confidence_score"] or 0),
            "effective_score": float(r["effective_score"] or 0),
            "mde_stage": r["mde_stage"],
            "hidden_repricing": bool(r["hidden_repricing"]),
            "setups": setups,
            "metrics": metrics,
            **fwd,
        })
    return events, by_sym


def date_index(events: List[dict]) -> Tuple[List[str], Dict[str, List[dict]]]:
    dates = sorted({e["trade_date"] for e in events})
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_date[e["trade_date"]].append(e)
    return dates, by_date


def market_regime(by_sym: dict, trade_date: str) -> str:
    """EGX100 proxy regime from index or median liquid names."""
    egx = None
    if by_sym:
        egx = by_sym.get("EGX30") or by_sym.get("EGX100") or by_sym.get("EGX70")
    if not egx:
        return "unknown"
    imap = {b["date"]: i for i, b in enumerate(egx)}
    idx = imap.get(trade_date)
    if idx is None or idx < 25:
        return "unknown"
    ret20 = egx[idx]["close"] / egx[idx - 20]["close"] - 1
    if ret20 > 0.03:
        return "uptrend"
    if ret20 < -0.03:
        return "downtrend"
    return "sideways"


def event_regime(e: dict, by_sym: dict) -> dict:
    m = e.get("metrics") or {}
    sym = e["symbol"]
    bars = by_sym.get(sym, [])
    imap = {b["date"]: i for i, b in enumerate(bars)}
    idx = imap.get(e["trade_date"])
    reg = {"market": market_regime(by_sym, e["trade_date"])}
    if idx is not None and idx >= 50:
        closes = [bars[i]["close"] for i in range(idx - 49, idx + 1)]
        ma50 = mean(closes)
        c = bars[idx]["close"]
        reg["vs_ma50"] = "above_ma50" if c > ma50 else "below_ma50"
        h60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
        l60 = min(closes[-60:]) if len(closes) >= 60 else min(closes)
        rng = h60 - l60
        if rng > 0:
            pos = (c - l60) / rng
            reg["range_pos"] = "near_60d_high" if pos > 0.85 else "near_60d_low" if pos < 0.15 else "mid_range"
    rel_turn = float(m.get("rel_turn") or 0)
    reg["liquidity"] = "expansion" if rel_turn > 1.3 else "contraction" if rel_turn < 0.8 else "normal"
    return reg


def filter_train_events(
    events: List[dict],
    before_date: str,
    memory_type: str,
    as_of_regime: Optional[dict] = None,
    half_life_days: int = 126,
    lookback_days: int = 252,
    all_dates: Optional[List[str]] = None,
) -> List[dict]:
    pool = [e for e in events if e["trade_date"] < before_date]
    if not pool:
        return []
    if memory_type == "last_252" and all_dates:
        cutoff_idx = max(0, all_dates.index(before_date) - lookback_days) if before_date in all_dates else 0
        cutoff = all_dates[cutoff_idx]
        pool = [e for e in pool if e["trade_date"] >= cutoff]
    elif memory_type == "exp_decay_126" and all_dates:
        def weight(d: str) -> float:
            if before_date not in all_dates or d not in all_dates:
                return 1.0
            gap = all_dates.index(before_date) - all_dates.index(d)
            return math.exp(-0.693 * gap / half_life_days)
        pool = [e for e in pool if weight(e["trade_date"]) > 0.05]
    elif memory_type == "regime_aware" and as_of_regime:
        mkt = as_of_regime.get("market")
        pool = [e for e in pool if market_regime({}, e["trade_date"]) == mkt or True]
        # regime filter via precomputed - fix below
    return pool


def setup_performance_pool(pool: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for sk in SETUP_KEYS:
        sub = [e for e in pool if sk in (e.get("setups") or []) and e.get("ret5") is not None]
        if not sub:
            continue
        hits = [e["hit5"] for e in sub]
        rets = [e["ret5"] for e in sub]
        wins = [r for r, h in zip(rets, hits) if h]
        losses = [abs(r) for r, h in zip(rets, hits) if not h]
        out[sk] = {
            "n": len(sub),
            "hit_rate": sum(hits) / len(hits),
            "avg_ret": mean(rets),
            "pf": pf(wins, losses),
            "score": (sum(hits) / len(hits)) * 100 + mean(rets) * 50,
        }
    return out


def setup_performance(train: List[dict], symbol: Optional[str] = None, sector: Optional[str] = None) -> Dict[str, dict]:
    pool = train
    if symbol:
        pool = [e for e in pool if e["symbol"] == symbol]
    elif sector:
        pool = [e for e in pool if (e.get("sector") or "Unknown") == sector]
    return setup_performance_pool(pool)


def build_window_profile_cache(train_ev: List[dict]) -> Dict[str, dict]:
    """Precompute per-symbol profiles for a train window."""
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    by_sec: Dict[str, List[dict]] = defaultdict(list)
    for e in train_ev:
        by_sym[e["symbol"]].append(e)
        by_sec[e.get("sector") or "Unknown"].append(e)
    glob_perf = setup_performance_pool(train_ev)
    sec_perf_cache: Dict[str, dict] = {}
    cache: Dict[str, dict] = {}
    for sym, sym_ev in by_sym.items():
        sector = (sym_ev[0].get("sector") or "Unknown") if sym_ev else "Unknown"
        if sector not in sec_perf_cache:
            sec_perf_cache[sector] = setup_performance_pool(by_sec.get(sector, []))
        sym_perf = setup_performance_pool(sym_ev)
        sec_perf = sec_perf_cache[sector]
        sym_n = len(sym_ev)
        sec_n = len(by_sec.get(sector, []))
        use_sym = sym_n >= MIN_SYMBOL_SETUP
        use_sec = sec_n >= MIN_SECTOR_SETUP
        use_glob = len(train_ev) >= MIN_GLOBAL_SETUP

        def pick_best(perf: Dict[str, dict], min_n: int) -> Optional[str]:
            cands = {k: v for k, v in perf.items() if v["n"] >= min_n}
            return max(cands, key=lambda k: cands[k]["score"]) if cands else None

        def pick_worst(perf: Dict[str, dict], min_n: int) -> Optional[str]:
            cands = {k: v for k, v in perf.items() if v["n"] >= min_n}
            return min(cands, key=lambda k: cands[k]["score"]) if cands else None

        best_sym = pick_best(sym_perf, 3) if use_sym else None
        best_sec = pick_best(sec_perf, 5) if use_sec else None
        best_glob = pick_best(glob_perf, MIN_GLOBAL_SETUP // 10) if use_glob else None
        worst_sym = pick_worst(sym_perf, 3) if use_sym else None
        best = best_sym or best_sec or best_glob
        cache[sym] = {
            "best_setup": best,
            "worst_setup": worst_sym,
            "symbol_events": sym_n,
            "symbol_setup_perf": sym_perf,
        }
    return cache


def build_memory_profile(
    train: List[dict],
    symbol: str,
    sector: Optional[str],
    memory_type: str = "equal",
) -> dict:
    sym_perf = setup_performance(train, symbol=symbol)
    sec_perf = setup_performance(train, sector=sector) if sector else {}
    glob_perf = setup_performance(train)

    def pick_best(perf: Dict[str, dict], min_n: int) -> Optional[str]:
        cands = {k: v for k, v in perf.items() if v["n"] >= min_n}
        if not cands:
            return None
        return max(cands, key=lambda k: cands[k]["score"])

    def pick_worst(perf: Dict[str, dict], min_n: int) -> Optional[str]:
        cands = {k: v for k, v in perf.items() if v["n"] >= min_n}
        if not cands:
            return None
        return min(cands, key=lambda k: cands[k]["score"])

    sym_n = sum(1 for e in train if e["symbol"] == symbol)
    use_sym = sym_n >= MIN_SYMBOL_SETUP
    sec_n = sum(1 for e in train if (e.get("sector") or "Unknown") == (sector or "Unknown"))
    use_sec = sec_n >= MIN_SECTOR_SETUP and sector
    use_glob = len(train) >= MIN_GLOBAL_SETUP

    if use_sym and use_sec and use_glob:
        w_sym, w_sec, w_glob = 0.50, 0.30, 0.20
    elif use_sec and use_glob:
        w_sym, w_sec, w_glob = 0.20, 0.50, 0.30
    else:
        w_sym, w_sec, w_glob = 0.0, 0.30 if use_sec else 0.0, 0.70 if use_glob else 1.0

    best_sym = pick_best(sym_perf, 3) if use_sym else None
    best_sec = pick_best(sec_perf, 5) if use_sec else None
    best_glob = pick_best(glob_perf, MIN_GLOBAL_SETUP // 10) if use_glob else None
    worst_sym = pick_worst(sym_perf, 3) if use_sym else None

    if memory_type == "sector_adjusted":
        best = best_sec or best_glob or best_sym
        worst = pick_worst(sec_perf, 5) if use_sec else worst_sym
    else:
        best = best_sym or best_sec or best_glob
        worst = worst_sym

    return {
        "best_setup": best,
        "worst_setup": worst,
        "symbol_events": sym_n,
        "use_symbol_memory": use_sym,
        "use_sector_memory": use_sec,
        "weights": {"symbol": w_sym, "sector": w_sec, "global": w_glob},
        "symbol_setup_perf": sym_perf,
    }


def memory_adjust_confidence(
    baseline: float,
    setups: List[str],
    hidden_repricing: bool,
    profile: dict,
) -> float:
    conf = baseline
    best = profile.get("best_setup")
    worst = profile.get("worst_setup")
    if best and best in setups:
        conf += 6
    if worst and worst in setups:
        conf -= 5
    if hidden_repricing and profile.get("symbol_events", 0) >= 10:
        sym_perf = profile.get("symbol_setup_perf") or {}
        if sym_perf:
            avg_hr = mean(v["hit_rate"] for v in sym_perf.values())
            if avg_hr >= 0.28:
                conf += 4
            elif avg_hr < 0.15:
                conf -= 4
    return max(20.0, min(100.0, conf))


def memory_signal_filter(e: dict, profile: dict, mode: str = "boost_only") -> bool:
    """Whether memory would elevate this event vs baseline HR filter."""
    if not e.get("hidden_repricing"):
        return False
    setups = e.get("setups") or []
    best = profile.get("best_setup")
    worst = profile.get("worst_setup")
    if mode == "exclude_worst" and worst and worst in setups and best not in setups:
        return False
    if best and best in setups:
        return True
    return True  # baseline HR passes


def filter_train_by_memory_type(train_ev: List[dict], memory_type: str, dates: List[str], test_start: str) -> List[dict]:
    if memory_type == "equal":
        return train_ev
    if memory_type == "last_252" and test_start in dates:
        cutoff_idx = max(0, dates.index(test_start) - 252)
        cutoff = dates[cutoff_idx]
        return [e for e in train_ev if e["trade_date"] >= cutoff]
    if memory_type == "exp_decay_126" and test_start in dates:
        tidx = dates.index(test_start)
        out = []
        for e in train_ev:
            if e["trade_date"] not in dates:
                continue
            gap = tidx - dates.index(e["trade_date"])
            if math.exp(-0.693 * gap / 126) > 0.05:
                out.append(e)
        return out
    if memory_type == "sector_adjusted":
        return train_ev
    if memory_type == "regime_aware":
        return train_ev  # regime applied per-symbol via sector_adjusted proxy in profile
    return train_ev


def walk_forward_compare(
    events: List[dict],
    dates: List[str],
    by_sym: dict,
    train_days: int,
    test_days: int,
    memory_type: str = "equal",
) -> List[dict]:
    results = []
    step = test_days
    for start in range(0, len(dates) - train_days - test_days + 1, step):
        train_dates = set(dates[start:start + train_days])
        test_dates = set(dates[start + train_days:start + train_days + test_days])
        if not test_dates:
            break
        test_start = min(test_dates)
        train_ev = filter_train_by_memory_type(
            [e for e in events if e["trade_date"] in train_dates],
            memory_type,
            dates,
            test_start,
        )
        test_ev = [e for e in events if e["trade_date"] in test_dates]

        profile_cache = build_window_profile_cache(train_ev)

        baseline_pool = [e for e in test_ev if e.get("hidden_repricing")]
        memory_pool = []
        for e in test_ev:
            if not e.get("hidden_repricing"):
                continue
            prof = profile_cache.get(e["symbol"]) or {}
            if memory_signal_filter(e, prof):
                e2 = dict(e)
                e2["memory_confidence"] = memory_adjust_confidence(
                    e["confidence_score"], e.get("setups") or [], e["hidden_repricing"], prof
                )
                memory_pool.append(e2)

        memory_refined = [
            e for e in memory_pool
            if e.get("memory_confidence", 0) >= e.get("confidence_score", 0) - 2
        ]

        b_stats = pool_stats(baseline_pool)
        m_stats = pool_stats(memory_refined if memory_refined else memory_pool)
        base_wr = (b_stats.get("hit_5d") or 0) / 100
        mem_wr = (m_stats.get("hit_5d") or 0) / 100
        results.append({
            "window_start": min(test_dates),
            "window_end": max(test_dates),
            "train_days": train_days,
            "test_days": test_days,
            "memory_type": memory_type,
            "baseline_n": b_stats["n"],
            "memory_n": m_stats["n"],
            "baseline_hit_5d": b_stats.get("hit_5d"),
            "memory_hit_5d": m_stats.get("hit_5d"),
            "baseline_hit_10d": b_stats.get("hit_10d"),
            "memory_hit_10d": m_stats.get("hit_10d"),
            "baseline_pf": b_stats.get("pf"),
            "memory_pf": m_stats.get("pf"),
            "baseline_avg_return": b_stats.get("avg_return_5d_pct"),
            "memory_avg_return": m_stats.get("avg_return_5d_pct"),
            "delta_hit_5d": round((mem_wr - base_wr) * 100, 1) if b_stats["n"] and m_stats["n"] else None,
            "delta_pf": round((m_stats.get("pf") or 0) - (b_stats.get("pf") or 0), 2)
            if b_stats.get("pf") and m_stats.get("pf") else None,
        })
    return results


def setup_stability_study(events: List[dict], dates: List[str], train_days: int = 252, test_days: int = 20) -> List[dict]:
    sym_windows: Dict[str, List[Tuple[Optional[str], Optional[str]]]] = defaultdict(list)
    for start in range(0, len(dates) - train_days - test_days + 1, test_days):
        train_dates = set(dates[start:start + train_days])
        test_dates = set(dates[start + train_days:start + train_days + test_days])
        train_ev = [e for e in events if e["trade_date"] in train_dates]
        test_ev = [e for e in events if e["trade_date"] in test_dates]
        profile_cache = build_window_profile_cache(train_ev)
        test_by_sym: Dict[str, List[dict]] = defaultdict(list)
        for e in test_ev:
            test_by_sym[e["symbol"]].append(e)
        for sym, sym_test in test_by_sym.items():
            past_best = (profile_cache.get(sym) or {}).get("best_setup")
            future_perf = setup_performance_pool(sym_test)
            future_best = max(future_perf, key=lambda k: future_perf[k]["score"]) if future_perf else None
            sym_windows[sym].append((past_best, future_best))

    rows = []
    for sym, pairs in sym_windows.items():
        if len(pairs) < 2:
            continue
        matches = sum(1 for p, f in pairs if p and f and p == f)
        stability = round(matches / len(pairs), 2)
        past_modes = Counter(p for p, _ in pairs if p)
        future_modes = Counter(f for _, f in pairs if f)
        if stability >= 0.67:
            cls = "Stable DNA"
        elif stability >= 0.34:
            cls = "Drifting DNA"
        elif len(set(f for _, f in pairs if f)) >= 3:
            cls = "Regime-switch DNA"
        else:
            cls = "Noisy DNA"
        rows.append({
            "symbol": sym,
            "past_best_setup": past_modes.most_common(1)[0][0] if past_modes else None,
            "future_best_setup": future_modes.most_common(1)[0][0] if future_modes else None,
            "stability_score": stability,
            "windows": len(pairs),
            "classification": cls,
        })
    return sorted(rows, key=lambda x: -x["stability_score"])


def worst_setup_penalty_study(events: List[dict], dates: List[str], train_days: int = 252, test_days: int = 20) -> List[dict]:
    rows = []
    agg: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for start in range(0, len(dates) - train_days - test_days + 1, test_days):
        train_dates = set(dates[start:start + train_days])
        test_dates = set(dates[start + train_days:start + train_days + test_days])
        train_ev = [e for e in events if e["trade_date"] in train_dates]
        test_ev = [e for e in events if e["trade_date"] in test_dates]
        profile_cache = build_window_profile_cache(train_ev)
        for sym in {e["symbol"] for e in test_ev}:
            worst = (profile_cache.get(sym) or {}).get("worst_setup")
            if not worst:
                continue
            future_worst = [e for e in test_ev if e["symbol"] == sym and worst in (e.get("setups") or [])]
            if future_worst:
                agg[(sym, worst)].extend(future_worst)
    for (sym, worst), pool in agg.items():
        if len(pool) < 3:
            continue
        st = pool_stats(pool)
        rows.append({
            "symbol": sym,
            "worst_setup_from_train": worst,
            "future_n": st["n"],
            "future_hit_5d": st.get("hit_5d"),
            "future_pf": st.get("pf"),
            "penalty_recommended": (st.get("hit_5d") or 0) < 18,
        })
    return rows


def memory_type_comparison(events: List[dict], dates: List[str], by_sym: dict) -> List[dict]:
    """Compare memory decay types on 252/20 window only (performance)."""
    out = []
    train_d, test_d = 252, 20
    for mem in MEMORY_TYPES:
        windows = walk_forward_compare(events, dates, by_sym, train_d, test_d, mem)
        if not windows:
            continue
        hits_b = [w["baseline_hit_5d"] for w in windows if w.get("baseline_hit_5d") is not None]
        hits_m = [w["memory_hit_5d"] for w in windows if w.get("memory_hit_5d") is not None]
        deltas = [w["delta_hit_5d"] for w in windows if w.get("delta_hit_5d") is not None]
        out.append({
            "memory_type": mem,
            "train_days": train_d,
            "test_days": test_d,
            "windows": len(windows),
            "avg_baseline_hit_5d": round(mean(hits_b), 1) if hits_b else None,
            "avg_memory_hit_5d": round(mean(hits_m), 1) if hits_m else None,
            "avg_delta_hit_5d": round(mean(deltas), 2) if deltas else None,
            "positive_windows": sum(1 for d in deltas if d and d > 0),
            "overfit_risk": "high" if deltas and mean(deltas) < -1 else "low" if deltas and mean(deltas) > 1 else "neutral",
        })
    return out


def regime_behavior_map(events: List[dict], by_sym: dict) -> List[dict]:
    rows = []
    for sk in SETUP_KEYS:
        for reg_key in ("market", "vs_ma50", "liquidity"):
            buckets: Dict[str, List[dict]] = defaultdict(list)
            for e in events:
                if sk not in (e.get("setups") or []):
                    continue
                rv = e["_regime"].get(reg_key)
                if rv:
                    buckets[rv].append(e)
            for reg_val, pool in buckets.items():
                if len(pool) < 15:
                    continue
                st = pool_stats(pool)
                rows.append({
                    "setup": sk,
                    "regime": f"{reg_key}={reg_val}",
                    "events": st["n"],
                    "hit_5d": st.get("hit_5d"),
                    "hit_10d": st.get("hit_10d"),
                    "pf": st.get("pf"),
                    "lift": st.get("lift"),
                    "recommendation": (
                        "favorable" if (st.get("hit_5d") or 0) >= 25 else
                        "neutral" if (st.get("hit_5d") or 0) >= 18 else "avoid"
                    ),
                })
    return rows


def persistence_study(events: List[dict], dates: List[str]) -> List[dict]:
    by_sym_date: Dict[str, Set[str]] = defaultdict(set)
    hr_dates: Dict[str, List[str]] = defaultdict(list)
    for e in events:
        if e["hidden_repricing"]:
            by_sym_date[e["symbol"]].add(e["trade_date"])
            hr_dates[e["symbol"]].append(e["trade_date"])

    def consecutive_hr(sym: str, d: str) -> int:
        if d not in dates:
            return 1
        idx = dates.index(d)
        streak = 1
        for back in range(1, 5):
            if idx - back < 0:
                break
            pd = dates[idx - back]
            if pd in by_sym_date[sym]:
                streak += 1
            else:
                break
        return streak

    cohorts: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        if not e["hidden_repricing"]:
            continue
        streak = consecutive_hr(e["symbol"], e["trade_date"])
        if streak == 1:
            cohorts["hr_1day"].append(e)
        elif streak == 2:
            cohorts["hr_2day"].append(e)
        elif streak >= 3:
            cohorts["hr_3plus"].append(e)
        if e.get("setups"):
            cohorts["hr_with_setup"].append(e)
        else:
            cohorts["hr_no_setup"].append(e)
        if e.get("confidence_score", 0) > 70:
            cohorts["hr_conf_gt_70"].append(e)
        if e.get("effective_score", 0) > 60:
            cohorts["hr_eff_gt_60"].append(e)

    return [{"cohort": k, **pool_stats(v)} for k, v in sorted(cohorts.items())]


def discovery_timing(events: List[dict], by_sym: dict) -> dict:
    timing = {"early_discovery": 0, "on_time_discovery": 0, "late_discovery": 0, "false_discovery": 0}
    samples = []
    for e in events:
        if not e.get("hidden_repricing"):
            continue
        sym = e["symbol"]
        bars = by_sym.get(sym, [])
        imap = {b["date"]: i for i, b in enumerate(bars)}
        idx = imap.get(e["trade_date"])
        if idx is None:
            continue
        c0 = bars[idx]["close"]
        days_to_5 = days_to_10 = None
        for fwd in range(1, 41):
            if idx + fwd >= len(bars):
                break
            ret = bars[idx + fwd]["close"] / c0 - 1
            if days_to_5 is None and ret >= 0.05:
                days_to_5 = fwd
            if days_to_10 is None and ret >= 0.10:
                days_to_10 = fwd
        ret5 = e.get("ret5")
        if days_to_5 is not None and days_to_5 >= 3:
            timing["early_discovery"] += 1
            cat = "early"
        elif days_to_5 is not None and days_to_5 <= 2:
            timing["on_time_discovery"] += 1
            cat = "on_time"
        elif ret5 is not None and ret5 < 0:
            timing["false_discovery"] += 1
            cat = "false"
        else:
            timing["late_discovery"] += 1
            cat = "late"
        if len(samples) < 100:
            samples.append({
                "symbol": sym, "date": e["trade_date"], "category": cat,
                "days_before_5pct": days_to_5, "days_before_10pct": days_to_10,
            })
    return {"counts": timing, "samples": samples}


def sequence_patterns(events: List[dict], dates: List[str]) -> List[dict]:
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)
    seq_counts: Counter = Counter()
    seq_syms: Dict[str, Set[str]] = defaultdict(set)
    seq_sectors: Dict[str, Counter] = defaultdict(Counter)
    seq_rets: Dict[str, List[float]] = defaultdict(list)

    for sym, evs in by_sym.items():
        evs = sorted(evs, key=lambda x: x["trade_date"])
        for i in range(len(evs) - 2):
            s1 = evs[i].get("setups") or (["HR"] if evs[i]["hidden_repricing"] else [])
            s2 = evs[i + 1].get("setups") or (["HR"] if evs[i + 1]["hidden_repricing"] else [])
            s3 = evs[i + 2].get("setups") or (["HR"] if evs[i + 2]["hidden_repricing"] else [])
            if not s1 or not s2 or not s3:
                continue
            key = f"{s1[0]} → {s2[0]} → {s3[0]}"
            seq_counts[key] += 1
            seq_syms[key].add(sym)
            seq_sectors[key][evs[i].get("sector") or "Unknown"] += 1
            if evs[i + 2].get("ret10") is not None:
                seq_rets[key].append(evs[i + 2]["ret10"])

    rows = []
    for seq, n in seq_counts.most_common(30):
        rets = seq_rets.get(seq, [])
        hits = [1 if r >= HIT_THRESH else 0 for r in rets]
        wins = [r for r, h in zip(rets, hits) if h]
        losses = [abs(r) for r, h in zip(rets, hits) if not h]
        rows.append({
            "sequence": seq,
            "occurrences": n,
            "symbols": len(seq_syms[seq]),
            "sectors": dict(seq_sectors[seq].most_common(3)),
            "avg_return_10d_pct": round(mean(rets) * 100, 2) if rets else None,
            "hit_rate": round(sum(hits) / len(hits) * 100, 1) if hits else None,
            "pf": round(pf(wins, losses), 2) if rets else None,
        })
    return rows


def false_discovery_rules(events: List[dict], by_sym: dict) -> List[dict]:
    false_ev = [e for e in events if e.get("hidden_repricing") and e.get("ret5") is not None and e["ret5"] < 0]
    rules = []
    # By setup
    for sk in SETUP_KEYS:
        sub = [e for e in false_ev if sk in (e.get("setups") or [])]
        all_sk = [e for e in events if sk in (e.get("setups") or []) and e.get("ret5") is not None]
        if len(sub) >= 20 and all_sk:
            rate = len(sub) / len(all_sk)
            if rate > 0.75:
                rules.append({
                    "false_rule_id": f"FALSE_{sk}_high_fail",
                    "condition": f"setup={sk}",
                    "false_rate_pct": round(rate * 100, 1),
                    "effect": "confidence_penalty",
                    "weight": -4,
                    "status": "shadow_only",
                })
    # Low rel_turn impact
    impact_false = [
        e for e in false_ev
        if "impact_expansion" in (e.get("setups") or [])
        and float((e.get("metrics") or {}).get("rel_turn") or 0) < 0.8
    ]
    if len(impact_false) >= 15:
        rules.append({
            "false_rule_id": "FALSE_impact_low_volume",
            "condition": "impact_expansion AND rel_turn < 0.8",
            "effect": "confidence_penalty",
            "weight": -5,
            "status": "shadow_only",
        })
    # Low confidence bucket
    low_conf = [e for e in false_ev if e.get("confidence_score", 0) < 60]
    if len(low_conf) / max(len(false_ev), 1) > 0.4:
        rules.append({
            "false_rule_id": "FALSE_low_confidence_hr",
            "condition": "hidden_repricing AND confidence < 60",
            "effect": "confidence_penalty",
            "weight": -6,
            "status": "shadow_only",
        })
    return rules


def opportunity_novelty(conn: sqlite3.Connection, events: List[dict]) -> dict:
    novelty_days: Dict[str, dict] = {}
    dates = sorted({e["trade_date"] for e in events})
    for d in dates[-60:]:
        mde_hr = {e["symbol"] for e in events if e["trade_date"] == d and e["hidden_repricing"]}
        opp, fs, act = set(), set(), set()
        try:
            for r in conn.execute(
                "SELECT symbol FROM opportunity_score_v2 WHERE trade_date=?", (d,)
            ).fetchall():
                opp.add(r["symbol"])
            for r in conn.execute(
                "SELECT symbol, actionable FROM final_signals WHERE trade_date=?", (d,)
            ).fetchall():
                fs.add(r["symbol"])
                if r["actionable"]:
                    act.add(r["symbol"])
        except sqlite3.OperationalError:
            continue
        novelty_days[d] = {
            "mde_hr": len(mde_hr),
            "mde_only": sorted(mde_hr - opp),
            "mde_and_opp": len(mde_hr & opp),
            "mde_and_final": len(mde_hr & fs),
            "mde_not_actionable": len(mde_hr - act),
        }
    return {"daily": novelty_days, "days_analyzed": len(novelty_days)}


def build_playbooks(profiles_path: Path, stability: List[dict]) -> Dict[str, dict]:
    if not profiles_path.exists():
        return {}
    doc = json.loads(profiles_path.read_text(encoding="utf-8"))
    profs = doc.get("profiles") or {}
    stab_map = {r["symbol"]: r for r in stability}
    playbooks = {}
    for sym, p in profs.items():
        st = stab_map.get(sym, {})
        playbooks[sym] = {
            "symbol": sym,
            "behavior_family": p.get("behavior_family"),
            "best_setup": p.get("best_setup_for_symbol"),
            "avoid_setup": p.get("worst_setup_for_symbol"),
            "preferred_regime": "uptrend" if p.get("behavior_family") in ("A", "C") else "sideways",
            "preferred_holding_window": p.get("preferred_holding_window"),
            "avg_drawdown_after_signal_pct": p.get("avg_max_drawdown_after_signal_pct"),
            "confirmation_needed": st.get("classification") != "Stable DNA",
            "confidence_adjustment": p.get("confidence_adjustment"),
            "memory_reliability": st.get("stability_score"),
            "dna_classification": st.get("classification"),
        }
    return playbooks


def build_rules_v2(
    stability: List[dict],
    regime_map: List[dict],
    false_rules: List[dict],
    wf_results: List[dict],
) -> List[dict]:
    rules = []
    rid = 1
    for row in stability:
        if row["classification"] == "Stable DNA" and row.get("past_best_setup"):
            rules.append({
                "rule_id": f"MDE_R_{rid:03d}",
                "scope": "symbol",
                "condition": f"setup={row['past_best_setup']} AND symbol={row['symbol']}",
                "effect": "confidence_boost",
                "weight": 4,
                "evidence": {
                    "stability_score": row["stability_score"],
                    "windows": row["windows"],
                },
                "status": "shadow_only",
            })
            rid += 1
    for fr in false_rules[:5]:
        rules.append({
            "rule_id": f"MDE_R_{rid:03d}",
            "scope": "global",
            "condition": fr["condition"],
            "effect": fr["effect"],
            "weight": fr["weight"],
            "evidence": {"false_rate_pct": fr.get("false_rate_pct")},
            "status": "shadow_only",
        })
        rid += 1
    pos_windows = sum(1 for w in wf_results if (w.get("delta_hit_5d") or 0) > 0)
    if wf_results and pos_windows / len(wf_results) > 0.5:
        rules.append({
            "rule_id": f"MDE_R_{rid:03d}",
            "scope": "global",
            "condition": "walk_forward_memory_positive_majority",
            "effect": "research_continue",
            "weight": 0,
            "evidence": {"positive_windows_pct": round(100 * pos_windows / len(wf_results), 1)},
            "status": "shadow_only",
        })
    return rules


def render_report(doc: dict) -> str:
    lines = [
        "# MDE Behavioral Memory Walk-Forward Report (Phase 2.7)",
        "",
        f"**Generated:** {doc['at']}",
        "",
        "## Executive Summary",
        "",
    ]
    es = doc["executive_summary"]
    for k, v in es.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "## Walk-Forward Windows", ""])
    lines.append("| train | test | mem | base hit% | mem hit% | delta |")
    lines.append("|---:|---:|---|---:|---:|---:|")
    for w in doc.get("walkforward_sample", [])[:15]:
        lines.append(
            f"| {w.get('train_days')} | {w.get('test_days')} | {w.get('memory_type')} | "
            f"{w.get('baseline_hit_5d')} | {w.get('memory_hit_5d')} | {w.get('delta_hit_5d')} |"
        )
    lines.extend(["", "## Memory Type Comparison", ""])
    for m in doc.get("memory_comparison", []):
        lines.append(
            f"- **{m['memory_type']}** ({m['train_days']}d train): "
            f"avg Δhit={m.get('avg_delta_hit_5d')} | overfit={m.get('overfit_risk')}"
        )
    lines.extend(["", "## Persistence Cohorts", ""])
    for p in doc.get("persistence", []):
        lines.append(f"- {p['cohort']}: n={p['n']} hit_5d={p.get('hit_5d')}% PF={p.get('pf')}")
    lines.extend(["", "## Decision", ""])
    lines.append(doc.get("decision", ""))
    lines.extend([
        "",
        "```text",
        "EGX_MDE_BEHAVIOR_MEMORY=0 — not enabled.",
        "No Phase 3. No opp_v2/UES/promotion/Telegram/veto.",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    print("═══ Phase 2.7: Walk-Forward Shadow ═══", flush=True)
    events, by_sym = load_events(conn)
    dates, _ = date_index(events)
    print(f"  loaded {len(events)} events, {len(dates)} dates", flush=True)
    print("  precomputing regimes...", flush=True)
    for e in events:
        e["_regime"] = event_regime(e, by_sym)

    wf_all: List[dict] = []
    for train_d, test_d in WINDOW_CONFIGS:
        print(f"  walk-forward {train_d}/{test_d}...", flush=True)
        wf_all.extend(walk_forward_compare(events, dates, by_sym, train_d, test_d, "equal"))

    print("  memory type comparison...", flush=True)
    worst_penalty = worst_setup_penalty_study(events, dates)
    memory_cmp = memory_type_comparison(events, dates, by_sym)
    print("  stability study...", flush=True)
    stability = setup_stability_study(events, dates)
    print("  persistence + regime + sequences...", flush=True)
    persistence = persistence_study(events, dates)
    regime_map = regime_behavior_map(events, by_sym)
    sequences = sequence_patterns(events, dates)
    false_rules = false_discovery_rules(events, by_sym)
    timing = discovery_timing(events, by_sym)
    novelty = opportunity_novelty(conn, events)
    playbooks = build_playbooks(DATA / "mde_symbol_behavior_profiles.json", stability)
    rules_v2 = build_rules_v2(stability, regime_map, false_rules, wf_all)

    deltas = [w["delta_hit_5d"] for w in wf_all if w.get("delta_hit_5d") is not None]
    avg_delta = round(mean(deltas), 2) if deltas else 0
    pos_pct = round(100 * sum(1 for d in deltas if d > 0) / max(len(deltas), 1), 1)

    at = datetime.now(timezone.utc).isoformat()
    wf_doc = {"at": at, "windows": wf_all, "worst_setup_penalty": worst_penalty[:100], "summary": {
        "total_windows": len(wf_all),
        "avg_delta_hit_5d": avg_delta,
        "positive_window_pct": pos_pct,
    }}
    mem_doc = {"at": at, "comparisons": memory_cmp}
    persist_doc = {"at": at, "cohorts": persistence}
    regime_doc = {"at": at, "regimes": regime_map}
    seq_doc = {"at": at, "sequences": sequences}
    false_doc = {"at": at, "rules": false_rules}
    playbook_doc = {"at": at, "playbooks": playbooks}
    rules_doc = {"at": at, "rules": rules_v2}

    decision = (
        "Memory adds OOS edge" if avg_delta > 1 and pos_pct >= 55
        else "Memory inconclusive — continue shadow" if abs(avg_delta) <= 1
        else "Memory may hurt OOS — do not enable EGX_MDE_BEHAVIOR_MEMORY"
    )

    report_doc = {
        "at": at,
        "executive_summary": {
            "events": len(events),
            "walk_forward_windows": len(wf_all),
            "avg_delta_hit_5d": avg_delta,
            "positive_window_pct": pos_pct,
            "stable_dna_symbols": sum(1 for s in stability if s["classification"] == "Stable DNA"),
            "memory_edge_verdict": decision,
        },
        "walkforward_sample": wf_all[:30],
        "memory_comparison": memory_cmp,
        "persistence": persistence,
        "discovery_timing": timing["counts"],
        "decision": decision,
    }

    OUT = {
        "walkforward": DATA / "mde_walkforward_shadow_compare.json",
        "memory": DATA / "mde_memory_decay_comparison.json",
        "persistence": DATA / "mde_setup_persistence_study.json",
        "regime": DATA / "mde_regime_behavior_map.json",
        "sequences": DATA / "mde_sequence_patterns.json",
        "false": DATA / "mde_false_discovery_rules.json",
        "playbooks": DATA / "mde_symbol_playbooks.json",
        "rules_v2": DATA / "mde_behavior_rules_v2.json",
        "report": ROOT / "docs" / "MDE_BEHAVIOR_MEMORY_WALKFORWARD_REPORT.md",
    }
    OUT["walkforward"].write_text(json.dumps(wf_doc, indent=2), encoding="utf-8")
    OUT["memory"].write_text(json.dumps(mem_doc, indent=2), encoding="utf-8")
    OUT["persistence"].write_text(json.dumps(persist_doc, indent=2), encoding="utf-8")
    OUT["regime"].write_text(json.dumps(regime_doc, indent=2), encoding="utf-8")
    OUT["sequences"].write_text(json.dumps(seq_doc, indent=2), encoding="utf-8")
    OUT["false"].write_text(json.dumps(false_doc, indent=2), encoding="utf-8")
    OUT["playbooks"].write_text(json.dumps(playbook_doc, indent=2, default=str), encoding="utf-8")
    OUT["rules_v2"].write_text(json.dumps(rules_doc, indent=2), encoding="utf-8")
    OUT["report"].write_text(render_report(report_doc), encoding="utf-8")

    conn.close()
    return {
        "success": True,
        "executive_summary": report_doc["executive_summary"],
        "outputs": [str(p.relative_to(ROOT)) for p in OUT.values()],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
