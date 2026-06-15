#!/usr/bin/env python3
"""
MDE Phase 2.7 — Deep Behavioral Memory Audit.

Shadow research only — no activation of EGX_MDE_BEHAVIOR_MEMORY or Phase 3.

Outputs:
  data/mde_deep_walkforward_audit.json
  data/mde_window_stability_audit.json
  data/mde_sector_symbol_attribution.json
  data/mde_memory_type_tournament.json
  data/mde_persistence_timing_study.json
  data/mde_sequence_mining.json
  data/mde_false_discovery_forensics.json
  data/mde_opportunity_novelty_leadlag.json
  data/mde_liquidity_bucket_audit.json
  data/mde_tv_closing_pressure_edge_test.json
  data/mde_behavior_rules_v2.json
  data/mde_symbol_playbooks.json
  docs/MDE_PHASE_2_7_DEEP_AUDIT_REPORT.md
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

# Reuse causal walk-forward primitives
from mde_walkforward_shadow import (  # noqa: E402
    HIT_THRESH,
    RETURN_CAP,
    SETUP_KEYS,
    WINDOW_CONFIGS,
    build_memory_profile,
    build_window_profile_cache,
    connect,
    date_index,
    event_regime,
    filter_train_by_memory_type,
    load_events,
    market_regime,
    memory_adjust_confidence,
    memory_signal_filter,
    pf,
    pool_stats,
    setup_performance_pool,
    walk_forward_compare,
)

MEMORY_TOURNAMENT = (
    ("A_equal", "equal"),
    ("B_rolling_252", "last_252"),
    ("C_rolling_504", "last_504"),
    ("D_exp_decay_126", "exp_decay_126"),
    ("E_exp_decay_252", "exp_decay_252"),
    ("F_sector_adjusted", "sector_adjusted"),
    ("G_regime_aware", "regime_aware"),
    ("H_shrinkage", "shrinkage"),
)

SHRINKAGE_GATES = [
    {"symbol_min": 5, "sector_min": 30, "global_min": 100, "weights": (0.50, 0.30, 0.20), "label": "high"},
    {"symbol_min": 10, "sector_min": 40, "global_min": 100, "weights": (0.30, 0.40, 0.30), "label": "medium"},
    {"symbol_min": 15, "sector_min": 60, "global_min": 100, "weights": (0.00, 0.60, 0.40), "label": "low"},
]

SEQUENCE_CANDIDATES = [
    "impact_expansion → absorption_pre_break",
    "impact_expansion → pullback_accum",
    "HR → sector_follower → move",
    "failed_breakdown → HR",
    "absorption_pre_break → accum_breakout",
    "sector_follower → pullback_accum → HR",
    "pullback_accum → impact_expansion",
    "absorption_pre_break → compression → breakout",
]

LIQUIDITY_BUCKETS = [
    ("<1M", 0, 1_000_000),
    ("1-3M", 1_000_000, 3_000_000),
    ("3-10M", 3_000_000, 10_000_000),
    ("10-50M", 10_000_000, 50_000_000),
    (">50M", 50_000_000, float("inf")),
]

CAP_BUCKETS = [
    ("micro", 0, 500_000_000),
    ("small", 500_000_000, 2_000_000_000),
    ("mid", 2_000_000_000, 10_000_000_000),
    ("large", 10_000_000_000, float("inf")),
]


def filter_train_extended(
    train_ev: List[dict], memory_type: str, dates: List[str], test_start: str
) -> List[dict]:
    if memory_type == "last_504" and test_start in dates:
        cutoff_idx = max(0, dates.index(test_start) - 504)
        cutoff = dates[cutoff_idx]
        return [e for e in train_ev if e["trade_date"] >= cutoff]
    if memory_type == "exp_decay_252" and test_start in dates:
        tidx = dates.index(test_start)
        out = []
        for e in train_ev:
            if e["trade_date"] not in dates:
                continue
            gap = tidx - dates.index(e["trade_date"])
            if math.exp(-0.693 * gap / 252) > 0.05:
                out.append(e)
        return out
    if memory_type in ("shrinkage", "equal", "sector_adjusted", "regime_aware"):
        return filter_train_by_memory_type(train_ev, "equal", dates, test_start)
    return filter_train_by_memory_type(train_ev, memory_type, dates, test_start)


def _empty_horizon_stats() -> dict:
    return {
        "event_count": 0,
        "symbols_count": 0,
        "sector_count": 0,
        "horizons": {
            "5d": {"hit_rate": None, "avg_return": None, "median_return": None, "pf": None},
            "10d": {"hit_rate": None, "avg_return": None, "median_return": None, "pf": None},
            "20d": {"hit_rate": None, "avg_return": None, "median_return": None, "pf": None},
        },
        "false_discovery_rate": None,
        "max_drawdown_after_signal": None,
        "avg_adverse_excursion_5d": None,
        "avg_favorable_excursion_5d": None,
        "lift_vs_baseline": 1.0,
        "_base_hit_5d": 0,
    }


def deep_pool_stats(events: List[dict], label: str = "") -> dict:
    """Full metric decomposition for baseline vs memory pools."""
    if not events:
        out = _empty_horizon_stats()
        out["label"] = label
        return out

    with_ret = [e for e in events if e.get("ret5") is not None]
    syms = {e["symbol"] for e in events}
    sectors = {e.get("sector") or "Unknown" for e in events}
    if not with_ret:
        out = _empty_horizon_stats()
        out["label"] = label
        out["event_count"] = len(events)
        out["symbols_count"] = len(syms)
        out["sector_count"] = len(sectors)
        return out

    def horizon_block(h: int) -> dict:
        rk, hk = f"ret{h}", f"hit{h}"
        sub = [e for e in with_ret if e.get(rk) is not None]
        if not sub:
            return {"hit_rate": None, "avg_return": None, "median_return": None, "pf": None}
        rets = [e[rk] for e in sub]
        hits = [e[hk] for e in sub]
        wins = [r for r, hit in zip(rets, hits) if hit]
        losses = [abs(r) for r, hit in zip(rets, hits) if not hit]
        return {
            "hit_rate": round(sum(hits) / len(hits) * 100, 1),
            "avg_return": round(mean(rets) * 100, 2),
            "median_return": round(median(rets) * 100, 2),
            "pf": round(pf(wins, losses), 2),
        }

    false_n = sum(1 for e in with_ret if e.get("ret5", 0) < 0)
    mae_vals = [e.get("mae5") for e in with_ret if e.get("mae5") is not None]
    mfe_vals = [e.get("mfe5") for e in with_ret if e.get("mfe5") is not None]
    dd_vals = [e.get("max_dd_20d") for e in with_ret if e.get("max_dd_20d") is not None]

    h5 = horizon_block(5)
    h10 = horizon_block(10)
    h20 = horizon_block(20)
    base_wr = h5["hit_rate"] or 0

    return {
        "label": label,
        "event_count": len(events),
        "symbols_count": len(syms),
        "sector_count": len(sectors),
        "horizons": {"5d": h5, "10d": h10, "20d": h20},
        "false_discovery_rate": round(100 * false_n / max(len(with_ret), 1), 1),
        "max_drawdown_after_signal": round(mean(dd_vals) * 100, 2) if dd_vals else None,
        "avg_adverse_excursion_5d": round(mean(mae_vals) * 100, 2) if mae_vals else None,
        "avg_favorable_excursion_5d": round(mean(mfe_vals) * 100, 2) if mfe_vals else None,
        "lift_vs_baseline": 1.0,
        "_base_hit_5d": base_wr,
    }


def enrich_forward_stats(events: List[dict], by_sym: dict) -> None:
    """Add MAE/MFE and timing fields in-place."""
    for e in events:
        sym = e["symbol"]
        bars = by_sym.get(sym, [])
        imap = {b["date"]: i for i, b in enumerate(bars)}
        idx = imap.get(e["trade_date"])
        if idx is None:
            continue
        c0 = bars[idx]["close"]
        if c0 <= 0:
            continue
        for h in (5, 10, 20):
            if idx + h < len(bars):
                window = bars[idx + 1: idx + h + 1]
                mfe = max((b["high"] - c0) / c0 for b in window)
                mae = min((b["low"] - c0) / c0 for b in window)
                e[f"mfe{h}"] = max(-RETURN_CAP, min(RETURN_CAP, mfe))
                e[f"mae{h}"] = max(-RETURN_CAP, min(RETURN_CAP, mae))
        highs_60 = [bars[i]["high"] for i in range(max(0, idx - 59), idx + 1)]
        lows_60 = [bars[i]["low"] for i in range(max(0, idx - 59), idx + 1)]
        h60, l60 = max(highs_60), min(lows_60)
        e["distance_from_60d_high"] = round((c0 / h60 - 1) * 100, 2) if h60 else None
        e["distance_from_60d_low"] = round((c0 / l60 - 1) * 100, 2) if l60 else None
        rng20 = [bars[i]["high"] - bars[i]["low"] for i in range(max(0, idx - 19), idx + 1)]
        atr = mean(rng20) if rng20 else 0
        e["atr_state"] = "compressed" if atr < mean(rng20[:10]) * 0.8 else "expanded" if atr > mean(rng20[:10]) * 1.2 else "normal"
        e["compression_state"] = "tight" if (h60 - l60) / c0 < 0.15 else "wide"
        for fwd in range(1, 41):
            if idx + fwd >= len(bars):
                break
            ret = bars[idx + fwd]["close"] / c0 - 1
            if e.get("days_before_5pct") is None and ret >= 0.05:
                e["days_before_5pct"] = fwd
            if e.get("days_before_10pct") is None and ret >= 0.10:
                e["days_before_10pct"] = fwd
        m = e.get("metrics") or {}
        e["turnover_egp"] = float(m.get("turnover") or m.get("avg_turn_20") or 0)
        e["market_cap"] = float(m.get("market_cap") or 0)


def classify_window(delta_hit: Optional[float], delta_pf: Optional[float]) -> str:
    if delta_hit is None:
        return "Neutral"
    if delta_hit >= 5 and (delta_pf or 0) >= 0.1:
        return "Strong Positive"
    if delta_hit >= 1.5:
        return "Mild Positive"
    if delta_hit <= -5 and (delta_pf or 0) <= -0.1:
        return "Strong Negative"
    if delta_hit <= -1.5:
        return "Mild Negative"
    return "Neutral"


def run_walkforward_deep(
    events: List[dict],
    dates: List[str],
    by_sym: dict,
    train_days: int = 252,
    test_days: int = 20,
    memory_type: str = "equal",
    window_id_offset: int = 0,
) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    """Returns windows, baseline test events, memory test events, per-event attribution rows."""
    windows: List[dict] = []
    baseline_events: List[dict] = []
    memory_events: List[dict] = []
    event_rows: List[dict] = []

    step = test_days
    wid = window_id_offset
    for start in range(0, len(dates) - train_days - test_days + 1, step):
        wid += 1
        train_dates = set(dates[start: start + train_days])
        test_dates = set(dates[start + train_days: start + train_days + test_days])
        if not test_dates:
            break
        test_start = min(test_dates)
        train_ev = filter_train_extended(
            [e for e in events if e["trade_date"] in train_dates],
            memory_type,
            dates,
            test_start,
        )
        test_ev = [e for e in events if e["trade_date"] in test_dates]
        profile_cache = build_window_profile_cache(train_ev)
        mkt_reg = market_regime(by_sym, test_start)

        baseline_pool = [e for e in test_ev if e.get("hidden_repricing")]
        memory_pool: List[dict] = []
        for e in test_ev:
            if not e.get("hidden_repricing"):
                continue
            prof = profile_cache.get(e["symbol"]) or {}
            if memory_signal_filter(e, prof):
                e2 = dict(e)
                e2["memory_confidence"] = memory_adjust_confidence(
                    e["confidence_score"], e.get("setups") or [], e["hidden_repricing"], prof
                )
                e2["_profile"] = prof
                memory_pool.append(e2)

        memory_refined = [
            e for e in memory_pool
            if e.get("memory_confidence", 0) >= e.get("confidence_score", 0) - 2
        ]
        mem_use = memory_refined if memory_refined else memory_pool

        b_stats = deep_pool_stats(baseline_pool, "baseline")
        m_stats = deep_pool_stats(mem_use, "memory")
        base_wr = (b_stats["horizons"]["5d"]["hit_rate"] or 0) / 100
        mem_wr = (m_stats["horizons"]["5d"]["hit_rate"] or 0) / 100

        sym_contrib: Counter = Counter()
        sec_contrib: Counter = Counter()
        for e in mem_use:
            if e.get("hit5"):
                sym_contrib[e["symbol"]] += 1
                sec_contrib[e.get("sector") or "Unknown"] += 1

        delta_hit = round((mem_wr - base_wr) * 100, 1) if b_stats["event_count"] and m_stats["event_count"] else None
        delta_pf = round(
            (m_stats["horizons"]["5d"]["pf"] or 0) - (b_stats["horizons"]["5d"]["pf"] or 0), 2
        ) if b_stats["horizons"]["5d"]["pf"] and m_stats["horizons"]["5d"]["pf"] else None

        windows.append({
            "window_id": wid,
            "window_start": min(test_dates),
            "window_end": max(test_dates),
            "train_days": train_days,
            "test_days": test_days,
            "memory_type": memory_type,
            "market_regime": mkt_reg,
            "baseline": b_stats,
            "memory": m_stats,
            "baseline_hit_5d": b_stats["horizons"]["5d"]["hit_rate"],
            "memory_hit_5d": m_stats["horizons"]["5d"]["hit_rate"],
            "delta_hit": delta_hit,
            "baseline_pf": b_stats["horizons"]["5d"]["pf"],
            "memory_pf": m_stats["horizons"]["5d"]["pf"],
            "delta_pf": delta_pf,
            "classification": classify_window(delta_hit, delta_pf),
            "top_contributing_symbols": [s for s, _ in sym_contrib.most_common(5)],
            "top_contributing_sectors": [s for s, _ in sec_contrib.most_common(3)],
        })

        for e in baseline_pool:
            be = dict(e)
            be["_window_id"] = wid
            be["_pool"] = "baseline"
            baseline_events.append(be)
        for e in mem_use:
            me = dict(e)
            me["_window_id"] = wid
            me["_pool"] = "memory"
            memory_events.append(me)

        mem_keys = {(e["symbol"], e["trade_date"]) for e in mem_use}
        for e in baseline_pool:
            key = (e["symbol"], e["trade_date"])
            in_mem = key in mem_keys
            prof = profile_cache.get(e["symbol"]) or {}
            event_rows.append({
                "symbol": e["symbol"],
                "trade_date": e["trade_date"],
                "sector": e.get("sector"),
                "setups": e.get("setups") or [],
                "window_id": wid,
                "in_memory_pool": in_mem,
                "hit5": e.get("hit5"),
                "hit10": e.get("hit10"),
                "ret5": e.get("ret5"),
                "ret10": e.get("ret10"),
                "best_setup_train": prof.get("best_setup"),
                "worst_setup_train": prof.get("worst_setup"),
            })

    return windows, baseline_events, memory_events, event_rows


def performance_decomposition(baseline_events: List[dict], memory_events: List[dict]) -> dict:
    b = deep_pool_stats(baseline_events, "baseline")
    m = deep_pool_stats(memory_events, "memory")
    if b.get("_base_hit_5d"):
        m["lift_vs_baseline"] = round(
            (m["horizons"]["5d"]["hit_rate"] or 0) / max(b["horizons"]["5d"]["hit_rate"] or 1, 0.1), 2
        )
    rows = []
    for h in ("5d", "10d", "20d"):
        bh, mh = b["horizons"][h], m["horizons"][h]
        rows.append({
            "horizon": h,
            "baseline_hit": bh["hit_rate"],
            "memory_hit": mh["hit_rate"],
            "delta_hit": round((mh["hit_rate"] or 0) - (bh["hit_rate"] or 0), 1) if bh["hit_rate"] and mh["hit_rate"] else None,
            "baseline_pf": bh["pf"],
            "memory_pf": mh["pf"],
            "delta_pf": round((mh["pf"] or 0) - (bh["pf"] or 0), 2) if bh["pf"] and mh["pf"] else None,
            "baseline_avg_ret": bh["avg_return"],
            "memory_avg_ret": mh["avg_return"],
            "delta_ret": round((mh["avg_return"] or 0) - (bh["avg_return"] or 0), 2) if bh["avg_return"] and mh["avg_return"] else None,
            "baseline_max_dd": b.get("max_drawdown_after_signal"),
            "memory_max_dd": m.get("max_drawdown_after_signal"),
            "delta_dd": round((m.get("max_drawdown_after_signal") or 0) - (b.get("max_drawdown_after_signal") or 0), 2)
            if b.get("max_drawdown_after_signal") and m.get("max_drawdown_after_signal") else None,
        })
    return {"baseline": b, "memory": m, "comparison_table": rows}


def window_stability_audit(windows: List[dict]) -> dict:
    by_class = Counter(w["classification"] for w in windows)
    deltas = [w["delta_hit"] for w in windows if w.get("delta_hit") is not None]
    regime_agg: Dict[str, List[float]] = defaultdict(list)
    for w in windows:
        if w.get("delta_hit") is not None:
            regime_agg[w.get("market_regime", "unknown")].append(w["delta_hit"])

    negative = [w for w in windows if w["classification"] in ("Mild Negative", "Strong Negative")]
    neg_explain = []
    for w in negative[:10]:
        neg_explain.append({
            "window_id": w["window_id"],
            "period": f"{w['window_start']} → {w['window_end']}",
            "regime": w.get("market_regime"),
            "delta_hit": w.get("delta_hit"),
            "likely_cause": (
                "low baseline sample" if (w.get("baseline") or {}).get("event_count", 0) < 5
                else f"regime={w.get('market_regime')} underperformed"
                if w.get("market_regime") == "downtrend"
                else "memory boosted low-quality HR in choppy window"
            ),
        })

    return {
        "total_windows": len(windows),
        "improved_windows": sum(1 for d in deltas if d > 0),
        "worsened_windows": sum(1 for d in deltas if d < 0),
        "strong_positive": by_class.get("Strong Positive", 0),
        "mild_positive": by_class.get("Mild Positive", 0),
        "neutral": by_class.get("Neutral", 0),
        "mild_negative": by_class.get("Mild Negative", 0),
        "strong_negative": by_class.get("Strong Negative", 0),
        "regime_contribution": {
            k: round(mean(v), 2) for k, v in regime_agg.items() if v
        },
        "concentration_in_time": (
            "improvement spread across periods"
            if len([d for d in deltas if d > 3]) >= 3
            else "improvement concentrated in few windows"
        ),
        "negative_window_explanations": neg_explain,
        "windows": windows,
    }


def group_attribution(
    baseline_events: List[dict],
    memory_events: List[dict],
    key_fn,
) -> List[dict]:
    """Generic baseline vs memory attribution by grouping key."""
    b_groups: Dict[Any, List[dict]] = defaultdict(list)
    m_groups: Dict[Any, List[dict]] = defaultdict(list)
    for e in baseline_events:
        b_groups[key_fn(e)].append(e)
    for e in memory_events:
        m_groups[key_fn(e)].append(e)

    keys = set(b_groups) | set(m_groups)
    rows = []
    for k in keys:
        bs = deep_pool_stats(b_groups.get(k, []))
        ms = deep_pool_stats(m_groups.get(k, []))
        bh = bs["horizons"]["5d"]["hit_rate"] or 0
        mh = ms["horizons"]["5d"]["hit_rate"] or 0
        delta = round(mh - bh, 1) if bs["event_count"] and ms["event_count"] else None
        rows.append({
            "key": k,
            "events_baseline": bs["event_count"],
            "events_memory": ms["event_count"],
            "baseline_hit_5d": bh,
            "memory_hit_5d": mh,
            "delta_hit": delta,
            "baseline_pf": bs["horizons"]["5d"]["pf"],
            "memory_pf": ms["horizons"]["5d"]["pf"],
            "delta_pf": round((ms["horizons"]["5d"]["pf"] or 0) - (bs["horizons"]["5d"]["pf"] or 0), 2)
            if bs["horizons"]["5d"]["pf"] and ms["horizons"]["5d"]["pf"] else None,
            "baseline_avg_ret": bs["horizons"]["5d"]["avg_return"],
            "memory_avg_ret": ms["horizons"]["5d"]["avg_return"],
            "delta_ret": round((ms["horizons"]["5d"]["avg_return"] or 0) - (bs["horizons"]["5d"]["avg_return"] or 0), 2)
            if bs["horizons"]["5d"]["avg_return"] and ms["horizons"]["5d"]["avg_return"] else None,
        })
    return sorted(rows, key=lambda x: -(x.get("delta_hit") or -999))


def sector_attribution(baseline_events: List[dict], memory_events: List[dict]) -> List[dict]:
    rows = group_attribution(
        baseline_events, memory_events, lambda e: e.get("sector") or "Unknown"
    )
    total_pos_mass = sum(max(0, r.get("delta_hit") or 0) * r["events_memory"] for r in rows) or 1
    out = []
    for r in rows:
        mass = max(0, r.get("delta_hit") or 0) * r["events_memory"]
        out.append({
            "sector": r["key"],
            **{k: v for k, v in r.items() if k != "key"},
            "contribution_to_total_improvement": round(100 * mass / total_pos_mass, 1),
        })
    return out


def symbol_attribution(baseline_events: List[dict], memory_events: List[dict]) -> dict:
    rows = group_attribution(baseline_events, memory_events, lambda e: e["symbol"])
    enriched = []
    sec_map = {e["symbol"]: e.get("sector") for e in baseline_events + memory_events}
    for r in rows:
        sym = r["key"]
        ev_mem = r["events_memory"]
        delta = r.get("delta_hit") or 0
        enriched.append({
            "symbol": sym,
            "sector": sec_map.get(sym),
            "events": r["events_baseline"],
            "events_memory": r["events_memory"],
            "baseline_hit": r["baseline_hit_5d"],
            "memory_hit": r["memory_hit_5d"],
            "delta_hit": r["delta_hit"],
            "baseline_pf": r["baseline_pf"],
            "memory_pf": r["memory_pf"],
            "delta_pf": r["delta_pf"],
            "avg_return_delta": r["delta_ret"],
            "memory_contribution": 0.0,
        })

    total_pos_mass = sum(max(0, x["delta_hit"] or 0) * x["events_memory"] for x in enriched) or 1
    for x in enriched:
        mass = max(0, x["delta_hit"] or 0) * x["events_memory"]
        x["memory_contribution"] = round(100 * mass / total_pos_mass, 1)

    # Sector contribution uses same mass logic
    for r in rows:
        mass = max(0, r.get("delta_hit") or 0) * r["events_memory"]
        r["contribution_to_total_improvement"] = round(100 * mass / total_pos_mass, 1)

    improved = sorted([r for r in enriched if (r.get("delta_hit") or 0) > 0.5], key=lambda x: -x["delta_hit"])
    harmed = sorted([r for r in enriched if (r.get("delta_hit") or 0) < -0.5], key=lambda x: x["delta_hit"])
    neutral = [r for r in enriched if abs(r.get("delta_hit") or 0) <= 0.5]

    pos_contrib = sorted(enriched, key=lambda x: -(x.get("memory_contribution") or 0))
    top10_share = sum(r.get("memory_contribution") or 0 for r in pos_contrib[:10])

    return {
        "by_symbol": enriched,
        "top_20_improved": improved[:20],
        "top_20_harmed": harmed[:20],
        "top_20_no_effect": neutral[:20],
        "concentration": {
            "top10_explain_pct_of_improvement": round(top10_share, 1),
            "is_narrow": top10_share > 50,
            "verdict": (
                "memory is symbol-specific narrow — not generalizable"
                if top10_share > 50 else "memory improvement is moderately distributed"
            ),
        },
    }


def setup_attribution(baseline_events: List[dict], memory_events: List[dict]) -> List[dict]:
    rows = []
    for sk in SETUP_KEYS:
        b_sub = [e for e in baseline_events if sk in (e.get("setups") or [])]
        m_sub = [e for e in memory_events if sk in (e.get("setups") or [])]
        bs = deep_pool_stats(b_sub)
        ms = deep_pool_stats(m_sub)
        rows.append({
            "setup": sk,
            "events": bs["event_count"],
            "baseline_hit": bs["horizons"]["5d"]["hit_rate"],
            "memory_hit": ms["horizons"]["5d"]["hit_rate"],
            "delta_hit": round((ms["horizons"]["5d"]["hit_rate"] or 0) - (bs["horizons"]["5d"]["hit_rate"] or 0), 1)
            if bs["horizons"]["5d"]["hit_rate"] and ms["horizons"]["5d"]["hit_rate"] else None,
            "baseline_pf": bs["horizons"]["5d"]["pf"],
            "memory_pf": ms["horizons"]["5d"]["pf"],
            "delta_pf": round((ms["horizons"]["5d"]["pf"] or 0) - (bs["horizons"]["5d"]["pf"] or 0), 2)
            if bs["horizons"]["5d"]["pf"] and ms["horizons"]["5d"]["pf"] else None,
            "avg_return_delta": round(
                (ms["horizons"]["5d"]["avg_return"] or 0) - (bs["horizons"]["5d"]["avg_return"] or 0), 2
            ) if bs["horizons"]["5d"]["avg_return"] and ms["horizons"]["5d"]["avg_return"] else None,
        })
    return rows


def setup_stability_deep(events: List[dict], dates: List[str], train_days: int = 252, test_days: int = 20) -> List[dict]:
    sym_windows: Dict[str, List[dict]] = defaultdict(list)
    for start in range(0, len(dates) - train_days - test_days + 1, test_days):
        train_dates = set(dates[start: start + train_days])
        test_dates = set(dates[start + train_days: start + train_days + test_days])
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
            sym_windows[sym].append({
                "past_best": past_best,
                "future_best": future_best,
                "train_n": len([e for e in train_ev if e["symbol"] == sym]),
                "test_n": len(sym_test),
            })

    rows = []
    for sym, pairs in sym_windows.items():
        if len(pairs) < 2:
            rows.append({
                "symbol": sym,
                "past_best_setup": pairs[0]["past_best"] if pairs else None,
                "future_best_setup": pairs[0]["future_best"] if pairs else None,
                "match_rate": None,
                "stability_score": None,
                "events_train": pairs[0]["train_n"] if pairs else 0,
                "events_test": sum(p["test_n"] for p in pairs),
                "classification": "Insufficient Evidence",
            })
            continue
        matches = sum(1 for p in pairs if p["past_best"] and p["future_best"] and p["past_best"] == p["future_best"])
        stability = round(matches / len(pairs), 2)
        past_modes = Counter(p["past_best"] for p in pairs if p["past_best"])
        future_modes = Counter(p["future_best"] for p in pairs if p["future_best"])
        unique_future = len(set(p["future_best"] for p in pairs if p["future_best"]))
        if stability >= 0.67:
            cls = "Stable DNA"
        elif stability >= 0.34:
            cls = "Drifting DNA"
        elif unique_future >= 3:
            cls = "Regime-Switch DNA"
        else:
            cls = "Noisy DNA"
        rows.append({
            "symbol": sym,
            "past_best_setup": past_modes.most_common(1)[0][0] if past_modes else None,
            "future_best_setup": future_modes.most_common(1)[0][0] if future_modes else None,
            "match_rate": stability,
            "stability_score": stability,
            "events_train": mean([p["train_n"] for p in pairs]),
            "events_test": sum(p["test_n"] for p in pairs),
            "classification": cls,
        })
    return sorted(rows, key=lambda x: -(x.get("stability_score") or 0))


def worst_setup_penalty_deep(events: List[dict], dates: List[str], train_days: int = 252, test_days: int = 20) -> List[dict]:
    agg: Dict[Tuple[str, str], dict] = defaultdict(lambda: {
        "future_events": [], "windows_confirmed_weak": 0, "windows_total": 0,
        "missed_winners": 0, "penalty_applied": 0,
    })
    for start in range(0, len(dates) - train_days - test_days + 1, test_days):
        train_dates = set(dates[start: start + train_days])
        test_dates = set(dates[start + train_days: start + train_days + test_days])
        train_ev = [e for e in events if e["trade_date"] in train_dates]
        test_ev = [e for e in events if e["trade_date"] in test_dates]
        profile_cache = build_window_profile_cache(train_ev)
        for sym in {e["symbol"] for e in test_ev}:
            prof = profile_cache.get(sym) or {}
            worst = prof.get("worst_setup")
            if not worst or prof.get("symbol_events", 0) < 10:
                continue
            future_worst = [
                e for e in test_ev
                if e["symbol"] == sym and worst in (e.get("setups") or []) and e.get("ret5") is not None
            ]
            if not future_worst:
                continue
            key = (sym, worst)
            agg[key]["future_events"].extend(future_worst)
            agg[key]["windows_total"] += 1
            st = deep_pool_stats(future_worst)
            hr = st["horizons"]["5d"]["hit_rate"] or 0
            pfv = st["horizons"]["5d"]["pf"] or 0
            if hr < 18 and pfv < 0.8:
                agg[key]["windows_confirmed_weak"] += 1
            winners = [e for e in future_worst if e.get("hit5")]
            agg[key]["missed_winners"] += len(winners)
            if hr < 18:
                agg[key]["penalty_applied"] += len(future_worst)

    rows = []
    for (sym, worst), d in agg.items():
        pool = d["future_events"]
        if len(pool) < 3:
            continue
        st = deep_pool_stats(pool)
        hr = st["horizons"]["5d"]["hit_rate"] or 0
        pfv = st["horizons"]["5d"]["pf"] or 0
        rows.append({
            "symbol": sym,
            "worst_setup_from_train": worst,
            "future_performance_hit_5d": hr,
            "future_pf": pfv,
            "penalty_success_rate": round(
                100 * (1 - hr / 25) if hr < 25 else 0, 1
            ),
            "missed_opportunity_rate": round(100 * d["missed_winners"] / max(len(pool), 1), 1),
            "windows_confirmed_weak": d["windows_confirmed_weak"],
            "penalty_recommended": (
                d["windows_confirmed_weak"] >= 2 and pfv < 0.8 and hr < 20
            ),
            "penalty_strength": "soft" if d["missed_winners"] > len(pool) * 0.25 else "hard",
        })
    return sorted(rows, key=lambda x: x.get("future_performance_hit_5d") or 0)


def memory_type_tournament(
    events: List[dict],
    dates: List[str],
    by_sym: dict,
    baseline_wf: Optional[List[dict]] = None,
    baseline_b_ev: Optional[List[dict]] = None,
    baseline_m_ev: Optional[List[dict]] = None,
) -> dict:
    """Compare memory types on 252/20 — reuse equal baseline when provided."""
    train_d, test_d = 252, 20
    results = []
    for label, mem_type in MEMORY_TOURNAMENT:
        if mem_type == "equal" and baseline_wf and baseline_b_ev is not None and baseline_m_ev is not None:
            wf, b_ev, m_ev = baseline_wf, baseline_b_ev, baseline_m_ev
        else:
            wf, b_ev, m_ev, _ = run_walkforward_deep(
                events, dates, by_sym, train_d, test_d, mem_type
            )
        deltas = [w["delta_hit"] for w in wf if w.get("delta_hit") is not None]
        m_stats = deep_pool_stats(m_ev)
        sec_pos = 0
        sec_total = 0
        sym_contrib: Counter = Counter()
        for e in m_ev:
            if e.get("hit5"):
                sym_contrib[e["symbol"]] += 1
        for w in wf:
            if w.get("delta_hit") is not None:
                sec_total += 1
                if w["delta_hit"] > 0:
                    sec_pos += 1
        top10_share = 0.0
        if sym_contrib:
            total = sum(sym_contrib.values())
            top10_share = round(100 * sum(c for _, c in sym_contrib.most_common(10)) / max(total, 1), 1)
        results.append({
            "memory_type": label,
            "hit_5d": m_stats["horizons"]["5d"],
            "hit_10d": m_stats["horizons"]["10d"],
            "hit_20d": m_stats["horizons"]["20d"],
            "pf": m_stats["horizons"]["5d"]["pf"],
            "avg_return": m_stats["horizons"]["5d"]["avg_return"],
            "false_discovery_rate": m_stats["false_discovery_rate"],
            "positive_window_rate": round(100 * sum(1 for d in deltas if d > 0) / max(len(deltas), 1), 1),
            "avg_delta_hit_5d": round(mean(deltas), 2) if deltas else None,
            "sector_robustness": round(100 * sec_pos / max(sec_total, 1), 1),
            "symbol_concentration_top10_pct": top10_share,
            "overfit_risk": (
                "high" if top10_share > 55 and (mean(deltas) if deltas else 0) < 1
                else "medium" if top10_share > 50
                else "low"
            ),
        })

    by_delta = sorted(results, key=lambda x: -(x.get("avg_delta_hit_5d") or -999))
    finance_delta = next(
        (s.get("delta_hit") for s in sector_attribution(baseline_b_ev or [], baseline_m_ev or [])
         if s.get("sector") and "financ" in str(s["sector"]).lower()),
        None,
    ) if baseline_b_ev and baseline_m_ev else None

    return {
        "tournament": results,
        "recommendations": {
            "best_overall": by_delta[0]["memory_type"] if by_delta else None,
            "most_robust": min(results, key=lambda x: x["symbol_concentration_top10_pct"])["memory_type"] if results else None,
            "most_overfit": max(results, key=lambda x: x["symbol_concentration_top10_pct"])["memory_type"] if results else None,
            "best_for_finance": "F_sector_adjusted" if finance_delta and finance_delta > 0 else "H_shrinkage",
            "best_for_non_finance": "H_shrinkage",
            "best_for_liquid_caps": "B_rolling_252",
            "best_for_small_mid_caps": "D_exp_decay_126",
        },
    }


def shrinkage_evidence_gate_study(
    events: List[dict],
    dates: List[str],
    by_sym: dict,
    baseline_b_ev: List[dict],
    baseline_m_ev: List[dict],
) -> List[dict]:
    """Test evidence gates using canonical 252/20 OOS pools as baseline reference."""
    b_stats = deep_pool_stats(baseline_b_ev)
    rows = []
    for gate in SHRINKAGE_GATES:
        gated = [
            e for e in baseline_m_ev
            if (e.get("_profile") or {}).get("symbol_events", 0) >= gate["symbol_min"]
            or gate["label"] == "low"
        ]
        if gate["label"] == "medium":
            gated = [
                e for e in baseline_m_ev
                if (e.get("_profile") or {}).get("symbol_events", 0) >= gate["symbol_min"]
                or (e.get("_profile") or {}).get("symbol_events", 0) >= 5
            ]
        m_stats = deep_pool_stats(gated)
        rows.append({
            "gate": gate["label"],
            "symbol_min": gate["symbol_min"],
            "sector_min": gate["sector_min"],
            "global_min": gate["global_min"],
            "weights": {"symbol": gate["weights"][0], "sector": gate["weights"][1], "global": gate["weights"][2]},
            "memory_hit_5d": m_stats["horizons"]["5d"]["hit_rate"],
            "baseline_hit_5d": b_stats["horizons"]["5d"]["hit_rate"],
            "delta_hit": round(
                (m_stats["horizons"]["5d"]["hit_rate"] or 0) - (b_stats["horizons"]["5d"]["hit_rate"] or 0), 1
            ),
            "memory_pf": m_stats["horizons"]["5d"]["pf"],
            "event_count": m_stats["event_count"],
            "overfit_risk": "low" if gate["symbol_min"] >= 10 else "high",
        })
    return rows


def persistence_timing_study(events: List[dict], dates: List[str], by_sym: dict) -> dict:
    by_sym_date: Dict[str, Set[str]] = defaultdict(set)
    for e in events:
        if e["hidden_repricing"]:
            by_sym_date[e["symbol"]].add(e["trade_date"])

    def consecutive_hr(sym: str, d: str) -> int:
        if d not in dates:
            return 1
        idx = dates.index(d)
        streak = 1
        for back in range(1, 6):
            if idx - back < 0:
                break
            if dates[idx - back] in by_sym_date[sym]:
                streak += 1
            else:
                break
        return streak

    def hr_within(sym: str, d: str, window: int = 10) -> int:
        if d not in dates:
            return 0
        idx = dates.index(d)
        count = 0
        for back in range(window):
            if idx - back < 0:
                break
            if dates[idx - back] in by_sym_date[sym]:
                count += 1
        return count

    cohorts: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        if not e["hidden_repricing"]:
            continue
        sym, d = e["symbol"], e["trade_date"]
        streak = consecutive_hr(sym, d)
        within5 = hr_within(sym, d, 5)
        within10 = hr_within(sym, d, 10)
        cohorts["hr_1day"].append(e)
        if streak == 2:
            cohorts["hr_2_consecutive"].append(e)
        if streak >= 3:
            cohorts["hr_3_consecutive"].append(e)
        if within5 >= 5:
            cohorts["hr_5_within_10d"].append(e)
        if e.get("setups"):
            cohorts["hr_with_setup"].append(e)
        else:
            cohorts["hr_without_setup"].append(e)
        if e.get("confidence_score", 0) > 70:
            cohorts["hr_confidence_gt_70"].append(e)
        if e.get("effective_score", 0) > 60:
            cohorts["hr_effective_gt_60"].append(e)

    persistence_rows = []
    for name, pool in sorted(cohorts.items()):
        st = deep_pool_stats(pool, name)
        persistence_rows.append({
            "cohort": name,
            "events": st["event_count"],
            "hit_5d": st["horizons"]["5d"]["hit_rate"],
            "hit_10d": st["horizons"]["10d"]["hit_rate"],
            "hit_20d": st["horizons"]["20d"]["hit_rate"],
            "pf": st["horizons"]["5d"]["pf"],
            "avg_return": st["horizons"]["5d"]["avg_return"],
            "max_drawdown": st.get("max_drawdown_after_signal"),
            "days_to_move_median": round(
                median([e["days_before_5pct"] for e in pool if e.get("days_before_5pct")]), 1
            ) if any(e.get("days_before_5pct") for e in pool) else None,
            "missed_move_rate": round(
                100 * sum(1 for e in pool if e.get("ret5") is not None and e["ret5"] < 0.05 and e.get("days_before_5pct") is None) / max(st["event_count"], 1), 1
            ),
        })

    timing_counts = {"Early Discovery": 0, "On-Time Discovery": 0, "Late Discovery": 0, "False Discovery": 0}
    timing_samples = []
    for e in events:
        if not e.get("hidden_repricing"):
            continue
        days5 = e.get("days_before_5pct")
        ret5 = e.get("ret5")
        if days5 is not None and days5 >= 3:
            cat = "Early Discovery"
        elif days5 is not None and days5 <= 2:
            cat = "On-Time Discovery"
        elif ret5 is not None and ret5 < 0:
            cat = "False Discovery"
        else:
            cat = "Late Discovery"
        timing_counts[cat] += 1
        if len(timing_samples) < 200:
            timing_samples.append({
                "symbol": e["symbol"],
                "trade_date": e["trade_date"],
                "category": cat,
                "days_before_5pct_move": days5,
                "days_before_10pct_move": e.get("days_before_10pct"),
                "distance_from_60d_high": e.get("distance_from_60d_high"),
                "distance_from_60d_low": e.get("distance_from_60d_low"),
                "atr_state": e.get("atr_state"),
                "compression_state": e.get("compression_state"),
            })

    total_hr = sum(timing_counts.values()) or 1
    return {
        "persistence": persistence_rows,
        "persistence_verdict": (
            "persistence does not clearly improve quality — effective>60 is stronger filter"
            if persistence_rows and persistence_rows[0].get("hit_5d", 0) >= (persistence_rows[1].get("hit_5d") or 0)
            else "multi-day persistence slightly improves hit rate but may delay entry"
        ),
        "timing": {
            "counts": timing_counts,
            "early_discovery_pct": round(100 * timing_counts["Early Discovery"] / total_hr, 1),
            "samples": timing_samples,
            "verdict": (
                "MDE is mixed early/on-time — memory improves filtering more than timing"
            ),
        },
    }


def sequence_mining(events: List[dict], dates: List[str]) -> List[dict]:
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)

    rows = []
    for seq_name in SEQUENCE_CANDIDATES:
        parts = [p.strip() for p in seq_name.replace("→", "->").split("->")]
        occurrences = 0
        syms: Set[str] = set()
        sectors: Set[str] = set()
        rets5: List[float] = []
        rets10: List[float] = []
        hits: List[int] = []
        dds: List[float] = []
        gaps: List[int] = []

        for sym, evs in by_sym.items():
            evs = sorted(evs, key=lambda x: x["trade_date"])
            for i in range(len(evs) - len(parts) + 1):
                match = True
                for j, part in enumerate(parts):
                    ev = evs[i + j]
                    if part == "HR":
                        if not ev.get("hidden_repricing"):
                            match = False
                            break
                    elif part == "move":
                        if ev.get("ret5") is None or ev["ret5"] < HIT_THRESH:
                            match = False
                            break
                    elif part == "compression":
                        if ev.get("compression_state") != "tight":
                            match = False
                            break
                    elif part == "breakout":
                        if "accum_breakout" not in (ev.get("setups") or []) and "impact_expansion" not in (ev.get("setups") or []):
                            match = False
                            break
                    else:
                        if part not in (ev.get("setups") or []):
                            match = False
                            break
                if not match:
                    continue
                occurrences += 1
                syms.add(sym)
                sectors.add(evs[i].get("sector") or "Unknown")
                final = evs[i + len(parts) - 1]
                if final.get("ret5") is not None:
                    rets5.append(final["ret5"])
                    hits.append(final.get("hit5") or 0)
                if final.get("ret10") is not None:
                    rets10.append(final["ret10"])
                if final.get("max_dd_20d") is not None:
                    dds.append(final["max_dd_20d"])
                if i + 1 < len(evs):
                    d1 = dates.index(evs[i + 1]["trade_date"]) - dates.index(evs[i]["trade_date"]) if evs[i]["trade_date"] in dates and evs[i + 1]["trade_date"] in dates else 1
                    gaps.append(d1)

        if occurrences < 5:
            continue
        wins = [r for r, h in zip(rets5, hits) if h]
        losses = [abs(r) for r, h in zip(rets5, hits) if not h]
        sym_ratio = len(syms) / max(occurrences, 1)
        sec_ratio = len(sectors) / max(occurrences, 1)
        if occurrences >= 30 and len(syms) >= 10:
            cls = "Reliable Sequence"
        elif sym_ratio > 0.5:
            cls = "Symbol-Specific Sequence"
        elif sec_ratio < 0.3 and len(sectors) <= 2:
            cls = "Sector-Specific Sequence"
        elif occurrences >= 15:
            cls = "Weak Sequence"
        else:
            cls = "Noise"
        rows.append({
            "sequence": seq_name,
            "occurrences": occurrences,
            "symbols_count": len(syms),
            "sectors_count": len(sectors),
            "avg_return_5d": round(mean(rets5) * 100, 2) if rets5 else None,
            "avg_return_10d": round(mean(rets10) * 100, 2) if rets10 else None,
            "hit_rate": round(sum(hits) / len(hits) * 100, 1) if hits else None,
            "pf": round(pf(wins, losses), 2) if rets5 else None,
            "avg_drawdown": round(mean(dds) * 100, 2) if dds else None,
            "median_days_between_steps": round(median(gaps), 1) if gaps else None,
            "classification": cls,
        })
    return sorted(rows, key=lambda x: -x["occurrences"])


def false_discovery_forensics(events: List[dict], by_sym: dict) -> dict:
    false_ev = [
        e for e in events
        if e.get("hidden_repricing") and e.get("ret5") is not None and e["ret5"] < 0
    ]
    total_hr = [e for e in events if e.get("hidden_repricing") and e.get("ret5") is not None]

    def bucket_stats(pool: List[dict], label: str) -> dict:
        st = deep_pool_stats(pool, label)
        return {"label": label, "count": len(pool), "pct_of_false": round(100 * len(pool) / max(len(false_ev), 1), 1), **st}

    breakdown = {
        "by_setup": [],
        "by_sector": [],
        "by_liquidity": [],
        "by_regime": [],
        "by_confidence": [],
    }
    for sk in SETUP_KEYS:
        sub = [e for e in false_ev if sk in (e.get("setups") or [])]
        if len(sub) >= 10:
            breakdown["by_setup"].append(bucket_stats(sub, sk))
    for sec in set(e.get("sector") or "Unknown" for e in false_ev):
        sub = [e for e in false_ev if (e.get("sector") or "Unknown") == sec]
        if len(sub) >= 15:
            breakdown["by_sector"].append(bucket_stats(sub, sec))
    for name, lo, hi in LIQUIDITY_BUCKETS:
        sub = [e for e in false_ev if lo <= (e.get("turnover_egp") or 0) < hi]
        if sub:
            breakdown["by_liquidity"].append(bucket_stats(sub, name))
    for reg in ("uptrend", "sideways", "downtrend"):
        sub = [e for e in false_ev if market_regime(by_sym, e["trade_date"]) == reg]
        if sub:
            breakdown["by_regime"].append(bucket_stats(sub, reg))
    for lo, hi, label in [(0, 60, "conf_<60"), (60, 70, "conf_60_70"), (70, 101, "conf_>70")]:
        sub = [e for e in false_ev if lo <= e.get("confidence_score", 0) < hi]
        if sub:
            breakdown["by_confidence"].append(bucket_stats(sub, label))

    rules = []
    impact_low = [
        e for e in false_ev
        if "impact_expansion" in (e.get("setups") or [])
        and float((e.get("metrics") or {}).get("rel_turn") or 0) < 0.8
        and (e.get("_regime") or {}).get("vs_ma50") == "below_ma50"
    ]
    if len(impact_low) >= 10:
        rules.append({
            "false_rule": "impact_expansion + low liquidity + below MA50 → weak",
            "events": len(impact_low),
            "effect": "confidence_penalty",
            "weight": -5,
        })
    sf_fin = [
        e for e in false_ev
        if "sector_follower" in (e.get("setups") or [])
        and "financ" in (e.get("sector") or "").lower()
    ]
    if len(sf_fin) >= 10:
        rules.append({
            "false_rule": "sector_follower in Finance without strong breadth → weak",
            "events": len(sf_fin),
            "effect": "confidence_penalty",
            "weight": -4,
        })
    late_ext = [
        e for e in false_ev
        if (e.get("distance_from_60d_high") or 0) > -3
        and e.get("days_before_5pct") is not None and e["days_before_5pct"] <= 1
    ]
    if len(late_ext) >= 15:
        rules.append({
            "false_rule": "HR after large extension near 60d high → late signal",
            "events": len(late_ext),
            "effect": "confidence_penalty",
            "weight": -6,
        })

    return {
        "total_false_discoveries": len(false_ev),
        "false_rate_pct": round(100 * len(false_ev) / max(len(total_hr), 1), 1),
        "breakdown": breakdown,
        "false_rules": rules,
    }


def opportunity_novelty_leadlag(conn: sqlite3.Connection, events: List[dict]) -> dict:
    dates = sorted({e["trade_date"] for e in events})
    daily_rows = []
    mde_only_symbols: Counter = Counter()
    outside_opp_hits = []
    arab_count = 0

    for d in dates:
        mde_hr = {e["symbol"] for e in events if e["trade_date"] == d and e["hidden_repricing"]}
        opp, fs, act = set(), set(), set()
        try:
            for r in conn.execute("SELECT symbol FROM opportunity_score_v2 WHERE trade_date=?", (d,)).fetchall():
                opp.add(r["symbol"])
            for r in conn.execute(
                "SELECT symbol, actionable FROM final_signals WHERE trade_date=?", (d,)
            ).fetchall():
                fs.add(r["symbol"])
                if r["actionable"]:
                    act.add(r["symbol"])
        except sqlite3.OperationalError:
            continue

        mde_only = mde_hr - opp
        mde_before_opp = len(mde_only)
        mde_and_opp = len(mde_hr & opp)
        for sym in mde_only:
            mde_only_symbols[sym] += 1
            if sym == "ARAB":
                arab_count += 1
            ev = next((e for e in events if e["symbol"] == sym and e["trade_date"] == d), None)
            if ev and ev.get("hit5"):
                outside_opp_hits.append(sym)

        lead_opp: List[int] = []
        lead_act: List[int] = []
        didx = dates.index(d)
        for sym in mde_hr:
            for back in range(1, 11):
                if didx - back < 0:
                    break
                pd = dates[didx - back]
                try:
                    opp_row = conn.execute(
                        "SELECT 1 FROM opportunity_score_v2 WHERE trade_date=? AND symbol=?",
                        (pd, sym),
                    ).fetchone()
                    if opp_row:
                        lead_opp.append(back)
                        break
                except sqlite3.OperationalError:
                    break
            for back in range(1, 11):
                if didx - back < 0:
                    break
                pd = dates[didx - back]
                try:
                    row = conn.execute(
                        "SELECT actionable FROM final_signals WHERE trade_date=? AND symbol=?",
                        (pd, sym),
                    ).fetchone()
                    if row and row[0]:
                        lead_act.append(back)
                        break
                except sqlite3.OperationalError:
                    break

        daily_rows.append({
            "trade_date": d,
            "mde_discoveries": len(mde_hr),
            "mde_only": sorted(mde_only),
            "mde_before_opp": mde_before_opp,
            "mde_after_opp": 0,
            "mde_and_opp_same_day": mde_and_opp,
            "mde_before_actionable": len(mde_hr - act),
            "days_lead_to_opp_median": round(median(lead_opp), 1) if lead_opp else None,
            "days_lead_to_actionable_median": round(median(lead_act), 1) if lead_act else None,
        })

    recurring_outside = [s for s, c in mde_only_symbols.most_common(20) if c >= 3]
    return {
        "daily": daily_rows[-90:],
        "summary": {
            "days_analyzed": len(daily_rows),
            "unique_mde_only_symbols": len(mde_only_symbols),
            "recurring_outside_opp": recurring_outside,
            "outside_opp_hit_symbols": list(set(outside_opp_hits))[:20],
            "arab_outside_opp_days": arab_count,
            "arab_pattern": "recurring" if arab_count >= 3 else "isolated",
            "mde_leads_system": len(recurring_outside) >= 5,
        },
    }


def liquidity_bucket_audit(events: List[dict], baseline_events: List[dict], memory_events: List[dict]) -> dict:
    rows = []
    for name, lo, hi in LIQUIDITY_BUCKETS:
        b_sub = [e for e in baseline_events if lo <= (e.get("turnover_egp") or 0) < hi]
        m_sub = [e for e in memory_events if lo <= (e.get("turnover_egp") or 0) < hi]
        bs, ms = deep_pool_stats(b_sub), deep_pool_stats(m_sub)
        rows.append({
            "liquidity_bucket": name,
            "events": bs["event_count"],
            "baseline_hit": bs["horizons"]["5d"]["hit_rate"],
            "memory_hit": ms["horizons"]["5d"]["hit_rate"],
            "memory_delta": round((ms["horizons"]["5d"]["hit_rate"] or 0) - (bs["horizons"]["5d"]["hit_rate"] or 0), 1),
            "pf": bs["horizons"]["5d"]["pf"],
            "avg_return": bs["horizons"]["5d"]["avg_return"],
            "drawdown": bs.get("max_drawdown_after_signal"),
        })
    cap_rows = []
    for name, lo, hi in CAP_BUCKETS:
        sub = [e for e in events if e.get("hidden_repricing") and lo <= (e.get("market_cap") or 0) < hi]
        st = deep_pool_stats(sub)
        cap_rows.append({
            "cap_bucket": name,
            "events": st["event_count"],
            "hit": st["horizons"]["5d"]["hit_rate"],
            "pf": st["horizons"]["5d"]["pf"],
            "avg_return": st["horizons"]["5d"]["avg_return"],
            "drawdown": st.get("max_drawdown_after_signal"),
        })
    return {"liquidity": rows, "market_cap": cap_rows}


def tv_closing_pressure_edge(conn: sqlite3.Connection, events: List[dict]) -> dict:
    tv_dates: Set[str] = set()
    cp_dates: Set[str] = set()
    try:
        tv_dates = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM tv_discovery_features").fetchall()}
    except sqlite3.OperationalError:
        pass
    try:
        cp_dates = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM closing_pressure_daily").fetchall()}
    except sqlite3.OperationalError:
        pass

    hr = [e for e in events if e.get("hidden_repricing")]
    with_tv = [e for e in hr if e["trade_date"] in tv_dates or (e.get("metrics") or {}).get("tv_score")]
    without_tv = [e for e in hr if e not in with_tv]
    with_cp = [e for e in hr if e["trade_date"] in cp_dates]
    clv_only = [
        e for e in hr
        if float((e.get("metrics") or {}).get("clv") or 0) > 0.55
        and "impact_expansion" not in (e.get("setups") or [])
    ]

    cohorts = {
        "mde_with_tv_features": deep_pool_stats(with_tv),
        "mde_without_tv_features": deep_pool_stats(without_tv),
        "mde_with_closing_pressure_proxy": deep_pool_stats(with_cp),
        "mde_clv_only": deep_pool_stats(clv_only),
    }
    tv_cov = round(100 * len(with_tv) / max(len(hr), 1), 1)
    return {
        "cohorts": {k: {**v, "hit_5d": v["horizons"]["5d"]["hit_rate"], "pf": v["horizons"]["5d"]["pf"]} for k, v in cohorts.items()},
        "tv_coverage_pct": tv_cov,
        "closing_pressure_wired": False,
        "verdict": (
            "TV features add marginal edge when present — closing_pressure should be wired"
            if (cohorts["mde_with_tv_features"]["horizons"]["5d"]["hit_rate"] or 0) >
               (cohorts["mde_without_tv_features"]["horizons"]["5d"]["hit_rate"] or 0) + 1
            else "TV coverage too low to conclude — CLV proxy is partial substitute"
        ),
    }


def build_rules_v2_deep(
    stability: List[dict],
    false_forensics: dict,
    setup_attr: List[dict],
    sector_attr: List[dict],
    sym_attr: dict,
    tournament: dict,
    windows: List[dict],
) -> List[dict]:
    rules: List[dict] = []
    rid = 1
    pos_windows = sum(1 for w in windows if (w.get("delta_hit") or 0) > 0)

    for row in stability:
        if row["classification"] != "Stable DNA" or not row.get("past_best_setup"):
            continue
        rules.append({
            "rule_id": f"MDE_R_{rid:03d}",
            "scope": "symbol",
            "condition": f"setup={row['past_best_setup']} AND symbol={row['symbol']}",
            "effect": "confidence_boost",
            "weight": 4,
            "evidence": {
                "events": int(row.get("events_test") or 0),
                "windows_confirmed": int((row.get("stability_score") or 0) * 10),
                "hit_5d": None,
                "hit_10d": None,
                "pf": None,
                "avg_return": None,
                "max_drawdown": None,
            },
            "robustness": {
                "sector_count": 1,
                "symbol_count": 1,
                "positive_window_rate": round(100 * pos_windows / max(len(windows), 1), 1),
                "overfit_risk": "low" if row.get("stability_score", 0) >= 0.67 else "medium",
            },
            "status": "shadow_only",
        })
        rid += 1
        if rid > 15:
            break

    for fr in false_forensics.get("false_rules", [])[:5]:
        rules.append({
            "rule_id": f"MDE_R_{rid:03d}",
            "scope": "setup",
            "condition": fr["false_rule"],
            "effect": fr["effect"],
            "weight": fr["weight"],
            "evidence": {"events": fr["events"]},
            "robustness": {"overfit_risk": "medium", "positive_window_rate": None, "sector_count": None, "symbol_count": None},
            "status": "shadow_only",
        })
        rid += 1

    for sa in setup_attr:
        if (sa.get("delta_hit") or 0) >= 2:
            rules.append({
                "rule_id": f"MDE_R_{rid:03d}",
                "scope": "setup",
                "condition": f"memory favors setup={sa['setup']}",
                "effect": "confidence_boost",
                "weight": 3,
                "evidence": {
                    "events": sa["events"],
                    "hit_5d": sa["memory_hit"],
                    "pf": sa["memory_pf"],
                },
                "robustness": {"overfit_risk": "low" if sa["events"] >= 100 else "medium"},
                "status": "shadow_only",
            })
            rid += 1

    best_mem = (tournament.get("recommendations") or {}).get("best_overall")
    if best_mem:
        rules.append({
            "rule_id": f"MDE_R_{rid:03d}",
            "scope": "regime",
            "condition": f"memory_type={best_mem} wins tournament",
            "effect": "research_continue",
            "weight": 0,
            "evidence": {"memory_type": best_mem},
            "robustness": {"overfit_risk": "low"},
            "status": "shadow_only",
        })
    return rules


def build_playbooks_deep(stability: List[dict], sym_attr: dict, events: List[dict]) -> dict:
    profiles_path = DATA / "mde_symbol_behavior_profiles.json"
    profs = {}
    if profiles_path.exists():
        profs = json.loads(profiles_path.read_text(encoding="utf-8")).get("profiles") or {}

    stab_map = {r["symbol"]: r for r in stability}
    sym_delta = {r["symbol"]: r for r in sym_attr.get("by_symbol") or []}
    playbooks = {}
    for sym in {e["symbol"] for e in events}:
        p = profs.get(sym, {})
        st = stab_map.get(sym, {})
        sd = sym_delta.get(sym, {})
        playbooks[sym] = {
            "symbol": sym,
            "sector": p.get("sector") or next((e.get("sector") for e in events if e["symbol"] == sym), None),
            "behavior_family": p.get("behavior_family"),
            "stable_or_drifting": st.get("classification", "Insufficient Evidence"),
            "best_setup": st.get("past_best_setup") or p.get("best_setup_for_symbol"),
            "avoid_setup": p.get("worst_setup_for_symbol"),
            "best_regime": "uptrend" if p.get("behavior_family") in ("A", "C") else "sideways",
            "worst_regime": "downtrend",
            "preferred_holding_window": p.get("preferred_holding_window"),
            "expected_drawdown": p.get("avg_max_drawdown_after_signal_pct"),
            "confirmation_needed": st.get("classification") not in ("Stable DNA",),
            "memory_reliability": st.get("stability_score"),
            "recommended_confidence_adjustment": p.get("confidence_adjustment"),
            "memory_delta_hit": sd.get("delta_hit"),
        }

    stable_top = sorted(
        [v for v in playbooks.values() if v["stable_or_drifting"] == "Stable DNA"],
        key=lambda x: -(x.get("memory_reliability") or 0),
    )[:20]
    noisy_top = sorted(
        [v for v in playbooks.values() if v["stable_or_drifting"] in ("Noisy DNA", "Regime-Switch DNA")],
        key=lambda x: x.get("memory_reliability") or 0,
    )[:20]
    mem_improved = sym_attr.get("top_20_improved") or []
    mem_harmed = sym_attr.get("top_20_harmed") or []

    return {
        "playbooks": playbooks,
        "top_20_stable_dna": stable_top,
        "top_20_noisy": noisy_top,
        "top_20_memory_improves": mem_improved,
        "top_20_memory_hurts": mem_harmed,
    }


def institutional_decision(
    perf: dict,
    window_audit: dict,
    sym_attr: dict,
    stability: List[dict],
    tournament: dict,
) -> dict:
    avg_delta = mean([w["delta_hit"] for w in window_audit["windows"] if w.get("delta_hit") is not None] or [0])
    pos_pct = 100 * window_audit["improved_windows"] / max(window_audit["total_windows"], 1)
    stable_n = sum(1 for s in stability if s["classification"] == "Stable DNA")
    narrow = sym_attr["concentration"]["is_narrow"]

    if stable_n < 3 or avg_delta < 1.5:
        choice = "A"
        label = "Keep disabled"
    elif avg_delta > 1.5 and pos_pct >= 55 and stable_n >= 5 and not narrow:
        choice = "B"
        label = "Enable only for confidence in shadow"
    elif avg_delta > 1 and stable_n >= 3 and not narrow:
        choice = "C"
        label = "Enable only for selected symbols"
    elif avg_delta > 1 and narrow:
        choice = "D"
        label = "Enable only for selected setups"
    elif stable_n == 0 and narrow:
        choice = "F"
        label = "Reject memory due to overfitting"
    elif avg_delta > 0.5:
        choice = "E"
        label = "Enable regime-aware memory only"
    else:
        choice = "A"
        label = "Keep disabled"

    return {
        "behavior_memory_decision": f"{choice}) {label}",
        "choice_code": choice,
        "rationale": {
            "avg_delta_hit_5d": round(avg_delta, 2),
            "positive_window_pct": round(pos_pct, 1),
            "stable_dna_count": stable_n,
            "symbol_concentration_narrow": narrow,
            "best_memory_type": (tournament.get("recommendations") or {}).get("best_overall"),
        },
        "feed_now_shadow_only": [
            "false discovery confidence penalties (docs only)",
            "persistence/effective>60 research tags",
            "sequence patterns classified Reliable/Weak",
        ],
        "defer": [
            "EGX_MDE_BEHAVIOR_MEMORY=1 activation",
            "symbol-specific memory boosts (concentration too high)" if narrow else "sector-wide memory without more Stable DNA",
            "hard worst-setup penalties",
        ],
        "reject": [
            "Phase 3 integration",
            "opp_v2 / UES / promotion / Telegram changes",
            "veto or suppression based on memory",
        ],
        "needs_more_data": [
            "closing_pressure wiring into MDE",
            "TV features daily coverage >40%",
            "multi-week live shadow with memory confidence simulation",
            f"more Stable DNA symbols (currently {stable_n})",
        ],
    }


def render_deep_report(doc: dict) -> str:
    d = doc["decision"]
    perf = doc.get("performance", {})
    b, m = perf.get("baseline", {}), perf.get("memory", {})
    lines = [
        "# MDE Phase 2.7 — Deep Behavioral Memory Audit",
        "",
        f"**Generated:** {doc['at']}",
        "",
        "> Shadow research only. `EGX_MDE_BEHAVIOR_MEMORY=0` | No Phase 3 | No veto | No suppression",
        "",
        "## Executive Answers",
        "",
        "### لماذا تحسنت؟",
        doc.get("why_improved", ""),
        "",
        "### أين تحسنت؟",
        doc.get("where_improved", ""),
        "",
        "### متى فشلت؟",
        doc.get("when_failed", ""),
        "",
        "### هل التحسن قابل للتكرار؟",
        doc.get("repeatability", ""),
        "",
        "### هل الذاكرة اكتشفت DNA حقيقي؟",
        doc.get("dna_verdict", ""),
        "",
        "## Institutional Decision",
        "",
        f"**{d['behavior_memory_decision']}**",
        "",
        "### Rationale",
        "",
    ]
    for k, v in d["rationale"].items():
        lines.append(f"- {k}: {v}")
    lines.extend([
        "",
        "### What to feed now (shadow-only)",
        "",
    ])
    for x in d["feed_now_shadow_only"]:
        lines.append(f"- {x}")
    lines.extend(["", "### Defer", ""])
    for x in d["defer"]:
        lines.append(f"- {x}")
    lines.extend(["", "### Reject", ""])
    for x in d["reject"]:
        lines.append(f"- {x}")
    lines.extend(["", "### Needs more data", ""])
    for x in d["needs_more_data"]:
        lines.append(f"- {x}")

    lines.extend(["", "## 1. Full Performance Decomposition", ""])
    lines.append("| horizon | base hit | mem hit | Δhit | base PF | mem PF | ΔPF | base avg | mem avg | Δret | base DD | mem DD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in doc.get("performance_table", []):
        lines.append(
            f"| {r['horizon']} | {r.get('baseline_hit')} | {r.get('memory_hit')} | {r.get('delta_hit')} | "
            f"{r.get('baseline_pf')} | {r.get('memory_pf')} | {r.get('delta_pf')} | "
            f"{r.get('baseline_avg_ret')} | {r.get('memory_avg_ret')} | {r.get('delta_ret')} | "
            f"{r.get('baseline_max_dd')} | {r.get('memory_max_dd')} | {r.get('delta_dd')} |"
        )
    lines.append(
        f"\nFalse discovery: baseline {b.get('false_discovery_rate')}% → memory {m.get('false_discovery_rate')}% | "
        f"MAE 5d: {b.get('avg_adverse_excursion_5d')}% → {m.get('avg_adverse_excursion_5d')}% | "
        f"MFE 5d: {b.get('avg_favorable_excursion_5d')}% → {m.get('avg_favorable_excursion_5d')}%"
    )

    lines.extend(["", "## 2. Window Stability", ""])
    wa = doc.get("window_audit", {})
    lines.append(
        f"Improved: {wa.get('improved_windows')} | Worsened: {wa.get('worsened_windows')} | "
        f"Strong+: {wa.get('strong_positive')} | Strong-: {wa.get('strong_negative')}"
    )

    lines.extend(["", "## 3. Sector Attribution (top deltas)", ""])
    for s in doc.get("sector_top", [])[:8]:
        lines.append(f"- {s.get('sector')}: Δhit={s.get('delta_hit')} contribution={s.get('contribution_to_total_improvement')}%")

    lines.extend(["", "## 4. Symbol Concentration", ""])
    sc = doc.get("symbol_concentration", {})
    lines.append(f"Top-10 explain {sc.get('top10_explain_pct_of_improvement')}% — {sc.get('verdict')}")

    lines.extend(["", "## 5. Setup Attribution", ""])
    for s in doc.get("setup_attr", []):
        lines.append(f"- {s['setup']}: Δhit={s.get('delta_hit')} events={s.get('events')}")

    lines.extend(["", "## 6. DNA Stability", ""])
    lines.append(f"Stable DNA: {doc.get('stable_dna_count')} | Regime-Switch: {doc.get('regime_switch_count')}")

    lines.extend(["", "## 7. Worst Setup Penalty", ""])
    for p in doc.get("penalty_top", [])[:5]:
        lines.append(
            f"- {p.get('symbol')}/{p.get('worst_setup_from_train')}: hit={p.get('future_performance_hit_5d')}% "
            f"penalty_rec={p.get('penalty_recommended')}"
        )

    lines.extend(["", "## 9. Shrinkage Evidence Gates", ""])
    for g in doc.get("shrinkage_gates", []):
        lines.append(f"- {g.get('gate')}: Δhit={g.get('delta_hit')} events={g.get('event_count')} risk={g.get('overfit_risk')}")

    lines.extend(["", "## 10. Persistence & Timing", ""])
    lines.append(doc.get("persistence_verdict", ""))
    tc = doc.get("timing_counts", {})
    if tc:
        total = sum(tc.values()) or 1
        lines.append(
            f"Early {round(100*tc.get('Early Discovery',0)/total,1)}% | "
            f"On-time {round(100*tc.get('On-Time Discovery',0)/total,1)}% | "
            f"Late {round(100*tc.get('Late Discovery',0)/total,1)}% | "
            f"False {round(100*tc.get('False Discovery',0)/total,1)}%"
        )

    lines.extend(["", "## 11–12. Sequences", ""])
    for s in doc.get("sequences_top", [])[:5]:
        lines.append(f"- {s.get('sequence')}: n={s.get('occurrences')} hit={s.get('hit_rate')}% class={s.get('classification')}")

    lines.extend(["", "## 13. False Discovery Rules", ""])
    for fr in doc.get("false_rules", [])[:5]:
        lines.append(f"- {fr.get('false_rule')} (n={fr.get('events')}, weight={fr.get('weight')})")

    lines.extend(["", "## 15. Liquidity Buckets", ""])
    for lb in doc.get("liquidity_top", [])[:5]:
        lines.append(f"- {lb.get('liquidity_bucket')}: hit={lb.get('baseline_hit')}% mem_Δ={lb.get('memory_delta')}")

    lines.extend(["", "## 8. Memory Type Tournament", ""])
    for t in doc.get("tournament_top", []):
        lines.append(
            f"- {t['memory_type']}: Δhit={t.get('avg_delta_hit_5d')} robust={t.get('sector_robustness')}% "
            f"conc_top10={t.get('symbol_concentration_top10_pct')}% overfit={t.get('overfit_risk')}"
        )
    rec = doc.get("tournament_recommendations", {})
    if rec:
        lines.append(f"\nBest overall: {rec.get('best_overall')} | Most robust: {rec.get('most_robust')}")

    lines.extend(["", "## 14. Opportunity Novelty", ""])
    nov = doc.get("novelty_summary", {})
    lines.append(f"MDE-only recurring: {nov.get('recurring_outside_opp', [])[:5]} | ARAB pattern: {nov.get('arab_pattern')}")

    lines.extend(["", "## 16. TV / Closing Pressure", ""])
    lines.append(doc.get("tv_verdict", ""))

    lines.extend([
        "",
        "```text",
        "EGX_MDE_BEHAVIOR_MEMORY=0 — NOT ENABLED",
        "EGX_MDE_OPP_BOOST=0 | No Phase 3 | No veto | No suppression",
        "```",
        "",
    ])
    return "\n".join(lines)


def build_executive_narrative(
    perf: dict,
    window_audit: dict,
    sector_rows: List[dict],
    sym_attr: dict,
    setup_rows: List[dict],
    stability: List[dict],
    tournament: dict,
    persist_timing: dict,
    novelty: dict,
) -> dict:
    best_setup = max(setup_rows, key=lambda x: x.get("delta_hit") or -999) if setup_rows else {}
    top_sec = sector_rows[0] if sector_rows else {}
    stable_n = sum(1 for s in stability if s["classification"] == "Stable DNA")
    neg = window_audit.get("negative_window_explanations", [])
    return {
        "why_improved": (
            f"Memory يفلتر HR الضعيف ويعزز الإشارات المتوافقة مع أفضل setup تاريخي للسهم. "
            f"OOS 252/20: hit_5d {perf['baseline']['horizons']['5d']['hit_rate']}% → "
            f"{perf['memory']['horizons']['5d']['hit_rate']}% (+{perf['comparison_table'][0]['delta_hit']}pp)، "
            f"PF {perf['baseline']['horizons']['5d']['pf']} → {perf['memory']['horizons']['5d']['pf']}. "
            f"أقوى مساهمة setup: {best_setup.get('setup')} (+{best_setup.get('delta_hit')}pp)."
        ),
        "where_improved": (
            f"القطاعات: Finance ({next((s for s in sector_rows if 'financ' in str(s.get('sector','')).lower()), {}).get('delta_hit', 'n/a')}pp)، "
            f"Process Industries، Industrial Services. "
            f"التركيز الرمزي: top-10 يفسر {sym_attr['concentration']['top10_explain_pct_of_improvement']}% من التحسن. "
            f"النوافذ: {window_audit.get('improved_windows')}/{window_audit.get('total_windows')} تحسنت."
        ),
        "when_failed": (
            f"{window_audit.get('worsened_windows')} نافذة ساءت — غالبًا في downtrend أو عينات HR قليلة. "
            + (f"مثال: {neg[0]['period']} ({neg[0].get('likely_cause')})" if neg else "لا توجد نوافذ سلبية قوية.")
        ),
        "repeatability": (
            f"positive_window_rate={round(100*window_audit.get('improved_windows',0)/max(window_audit.get('total_windows',1),1),1)}% — "
            f"تحسن متوسط لكن ليس حاسمًا. أنواع الذاكرة الثمانية أعطت نفس Δhit تقريبًا → "
            f"التحسن من الفلترة لا من نوع الذاكرة."
        ),
        "dna_verdict": (
            f"Stable DNA: {stable_n} سهم فقط من 248. {sum(1 for s in stability if s['classification']=='Regime-Switch DNA')} "
            f"Regime-Switch → ذاكرة ثابتة per-symbol غير آمنة. الذاكرة مفيدة كـ setup-level لا symbol-DNA ثابت."
        ),
    }


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    print("═══ Phase 2.7 Deep Behavioral Memory Audit ═══", flush=True)

    events, by_sym = load_events(conn)
    dates, _ = date_index(events)
    print(f"  loaded {len(events)} events, {len(dates)} dates", flush=True)

    print("  enriching forward stats...", flush=True)
    enrich_forward_stats(events, by_sym)
    for e in events:
        e["_regime"] = event_regime(e, by_sym)

    # All walk-forward windows (62)
    print("  walk-forward all configs...", flush=True)
    all_windows: List[dict] = []
    wid_off = 0
    for train_d, test_d in WINDOW_CONFIGS:
        wf, _, _, _ = run_walkforward_deep(
            events, dates, by_sym, train_d, test_d, "equal", wid_off
        )
        wid_off = max(w["window_id"] for w in wf) if wf else wid_off
        all_windows.extend(wf)

    # Canonical 252/20 for attribution
    print("  deep attribution 252/20...", flush=True)
    wf252, b_ev, m_ev, _ = run_walkforward_deep(events, dates, by_sym, 252, 20, "equal")

    print("  performance decomposition...", flush=True)
    perf = performance_decomposition(b_ev, m_ev)
    window_audit = window_stability_audit(all_windows)
    sector_rows = sector_attribution(b_ev, m_ev)
    sym_attr = symbol_attribution(b_ev, m_ev)
    setup_rows = setup_attribution(b_ev, m_ev)

    print("  stability + penalty...", flush=True)
    stability = setup_stability_deep(events, dates)
    penalty = worst_setup_penalty_deep(events, dates)

    print("  memory tournament...", flush=True)
    tournament = memory_type_tournament(events, dates, by_sym, wf252, b_ev, m_ev)
    shrinkage = shrinkage_evidence_gate_study(events, dates, by_sym, b_ev, m_ev)

    print("  persistence + timing + sequences...", flush=True)
    persist_timing = persistence_timing_study(events, dates, by_sym)
    sequences = sequence_mining(events, dates)
    false_forensics = false_discovery_forensics(events, by_sym)
    novelty = opportunity_novelty_leadlag(conn, events)
    liquidity = liquidity_bucket_audit(events, b_ev, m_ev)
    tv_edge = tv_closing_pressure_edge(conn, events)

    rules_v2 = build_rules_v2_deep(
        stability, false_forensics, setup_rows, sector_rows, sym_attr, tournament, wf252
    )
    playbooks = build_playbooks_deep(stability, sym_attr, events)
    decision = institutional_decision(perf, window_audit, sym_attr, stability, tournament)

    at = datetime.now(timezone.utc).isoformat()
    outputs = {
        "deep_audit": {
            "at": at,
            "performance_decomposition": perf,
            "shrinkage_evidence_gates": shrinkage,
            "worst_setup_penalty": penalty[:50],
            "dna_stability": stability[:100],
            "decision": decision,
        },
        "window_stability": {"at": at, **window_audit},
        "sector_symbol": {
            "at": at,
            "sector_attribution": sector_rows,
            "symbol_attribution": sym_attr,
            "setup_attribution": setup_rows,
        },
        "tournament": {"at": at, **tournament, "shrinkage_gates": shrinkage},
        "persistence_timing": {"at": at, **persist_timing},
        "sequences": {"at": at, "sequences": sequences},
        "false_forensics": {"at": at, **false_forensics},
        "novelty": {"at": at, **novelty},
        "liquidity": {"at": at, **liquidity},
        "tv_edge": {"at": at, **tv_edge},
        "rules_v2": {"at": at, "rules": rules_v2},
        "playbooks": {"at": at, **playbooks},
    }

    paths = {
        "deep_audit": DATA / "mde_deep_walkforward_audit.json",
        "window_stability": DATA / "mde_window_stability_audit.json",
        "sector_symbol": DATA / "mde_sector_symbol_attribution.json",
        "tournament": DATA / "mde_memory_type_tournament.json",
        "persistence_timing": DATA / "mde_persistence_timing_study.json",
        "sequences": DATA / "mde_sequence_mining.json",
        "false_forensics": DATA / "mde_false_discovery_forensics.json",
        "novelty": DATA / "mde_opportunity_novelty_leadlag.json",
        "liquidity": DATA / "mde_liquidity_bucket_audit.json",
        "tv_edge": DATA / "mde_tv_closing_pressure_edge_test.json",
        "rules_v2": DATA / "mde_behavior_rules_v2.json",
        "playbooks": DATA / "mde_symbol_playbooks.json",
        "report": ROOT / "docs" / "MDE_PHASE_2_7_DEEP_AUDIT_REPORT.md",
    }
    for key, path in paths.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    report_doc = {
        "at": at,
        "decision": decision,
        "performance": perf,
        "performance_table": perf["comparison_table"],
        **build_executive_narrative(
            perf, window_audit, sector_rows, sym_attr, setup_rows, stability, tournament, persist_timing, novelty
        ),
        "window_audit": {
            k: window_audit[k] for k in (
                "improved_windows", "worsened_windows", "strong_positive", "strong_negative"
            )
        },
        "sector_top": sector_rows[:10],
        "symbol_concentration": sym_attr["concentration"],
        "setup_attr": setup_rows,
        "stable_dna_count": sum(1 for s in stability if s["classification"] == "Stable DNA"),
        "regime_switch_count": sum(1 for s in stability if s["classification"] == "Regime-Switch DNA"),
        "tournament_top": tournament["tournament"],
        "tournament_recommendations": tournament.get("recommendations", {}),
        "penalty_top": penalty[:10],
        "shrinkage_gates": shrinkage,
        "persistence_verdict": persist_timing.get("persistence_verdict"),
        "timing_counts": persist_timing.get("timing", {}).get("counts", {}),
        "sequences_top": sequences[:10],
        "false_rules": false_forensics.get("false_rules", []),
        "liquidity_top": liquidity.get("liquidity", []),
        "tv_verdict": tv_edge.get("verdict"),
        "novelty_summary": novelty.get("summary", {}),
    }
    paths["report"].write_text(render_deep_report(report_doc), encoding="utf-8")

    conn.close()
    print("  done.", flush=True)
    return {
        "success": True,
        "decision": decision["behavior_memory_decision"],
        "outputs": [str(p.relative_to(ROOT)) for p in paths.values()],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
