#!/usr/bin/env python3
"""
MDE Phase 2.8 — Actionable Discovery Extraction & Alpha Rule Validation.

Shadow research only. No production activation.

Outputs:
  data/mde_rule_stack_v2.json
  data/mde_rule_stack_v2_backtest.json
  data/mde_current_opportunity_extraction.json
  data/mde_historical_analogs.json
  data/mde_current_candidate_ranking.json
  data/mde_mde_only_opportunities.json
  data/mde_sequence_alpha.json
  data/mde_regime_opportunity_layer.json
  data/mde_liquidity_track_playbooks.json
  data/mde_false_discovery_rule_stack.json
  data/mde_family_playbooks_v2.json
  docs/MDE_PHASE_2_8_ACTIONABLE_DISCOVERY_REPORT.md
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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

from mde_walkforward_shadow import (  # noqa: E402
    HIT_THRESH,
    RETURN_CAP,
    SETUP_KEYS,
    connect,
    date_index,
    event_regime,
    load_events,
    market_regime,
    pf,
)

LIQUIDITY_TRACKS = [
    ("Thin", 0, 1_000_000),
    ("Low/Mid", 1_000_000, 3_000_000),
    ("Mid", 3_000_000, 10_000_000),
    ("Liquid", 10_000_000, 50_000_000),
    ("Institutional", 50_000_000, float("inf")),
]

CAP_BUCKETS = [
    ("micro", 0, 500_000_000),
    ("small", 500_000_000, 2_000_000_000),
    ("mid", 2_000_000_000, 10_000_000_000),
    ("large", 10_000_000_000, float("inf")),
]

SEQUENCE_ALPHA = [
    ("SEQ_001", "pullback_accum → impact_expansion"),
    ("SEQ_002", "failed_breakdown → HR"),
    ("SEQ_003", "absorption_pre_break → impact_expansion"),
    ("SEQ_004", "HR → sector_follower → pullback"),
    ("SEQ_005", "impact_expansion → compression → breakout"),
    ("SEQ_006", "sector_follower → HR → move"),
]

FALSE_RULE_CANDIDATES = [
    ("FD_001", "impact_expansion + late signal", lambda e: "impact_expansion" in (e.get("setups") or []) and e.get("timing_class") in ("LATE", "TOO_LATE")),
    ("FD_002", "HR + high extension near 60d high", lambda e: e.get("hidden_repricing") and (e.get("distance_from_60d_high") or 0) > -3),
    ("FD_003", "sector_follower + weak rel_turn", lambda e: "sector_follower" in (e.get("setups") or []) and float((e.get("metrics") or {}).get("rel_turn") or 0) < 0.9),
    ("FD_004", "absorption + low CLV", lambda e: "absorption_pre_break" in (e.get("setups") or []) and float((e.get("metrics") or {}).get("clv") or 0) < 0.45),
    ("FD_005", "failed_breakdown no confirmation", lambda e: "failed_breakdown" in (e.get("setups") or []) and float((e.get("metrics") or {}).get("rel_turn") or 0) < 1.0),
]

FAMILY_DEFS = [
    ("FAM_001", "High Effective HR", lambda e: e.get("hidden_repricing") and e.get("effective_score", 0) > 60),
    ("FAM_002", "Failed Breakdown Recovery", lambda e: "failed_breakdown" in (e.get("setups") or [])),
    ("FAM_003", "Pullback → Impact Sequence", lambda e: e.get("has_seq_pullback_impact")),
    ("FAM_004", "Mid-Liquidity Repricing", lambda e: e.get("liquidity_track") == "Mid" and e.get("hidden_repricing")),
    ("FAM_005", "Outside-Opp Discovery", lambda e: e.get("mde_only")),
    ("FAM_006", "Absorption Pre-Break", lambda e: "absorption_pre_break" in (e.get("setups") or [])),
    ("FAM_007", "Sector-Follower Regime", lambda e: "sector_follower" in (e.get("setups") or []) and (e.get("_regime") or {}).get("market") == "uptrend"),
]


def liquidity_track(turnover: float) -> str:
    for name, lo, hi in LIQUIDITY_TRACKS:
        if lo <= turnover < hi:
            return name
    return "Unknown"


def cap_bucket(mc: float) -> str:
    for name, lo, hi in CAP_BUCKETS:
        if lo <= mc < hi:
            return name
    return "unknown"


def enrich_events(events: List[dict], by_sym: dict, dates: List[str]) -> None:
    by_sym_date: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for e in events:
        by_sym_date[e["symbol"]][e["trade_date"]] = e

    for e in events:
        sym = e["symbol"]
        bars = by_sym.get(sym, [])
        imap = {b["date"]: i for i, b in enumerate(bars)}
        idx = imap.get(e["trade_date"])
        m = e.get("metrics") or {}
        turnover = float(m.get("turnover") or m.get("avg_turn_20") or 0)
        e["turnover_egp"] = turnover
        e["liquidity_track"] = liquidity_track(turnover)
        e["market_cap_bucket"] = cap_bucket(float(m.get("market_cap") or 0))
        e["_regime"] = event_regime(e, by_sym)

        if idx is not None:
            c0 = bars[idx]["close"]
            if c0 > 0:
                h20 = max(bars[i]["high"] for i in range(max(0, idx - 19), idx + 1))
                h60 = max(bars[i]["high"] for i in range(max(0, idx - 59), idx + 1))
                e["distance_from_20d_high"] = round((c0 / h20 - 1) * 100, 2)
                e["distance_from_60d_high"] = round((c0 / h60 - 1) * 100, 2)
                rng20 = [bars[i]["high"] - bars[i]["low"] for i in range(max(0, idx - 19), idx + 1)]
                e["atr_state"] = "compressed" if mean(rng20) < mean(rng20[:10]) * 0.85 else "expanded"
                e["compression_state"] = "tight" if (h60 - min(bars[i]["low"] for i in range(max(0, idx - 59), idx + 1))) / c0 < 0.15 else "wide"
                e["volume_state"] = "surge" if float(m.get("rel_turn") or 0) > 1.5 else "normal"

                for fwd in range(1, 41):
                    if idx + fwd >= len(bars):
                        break
                    ret = bars[idx + fwd]["close"] / c0 - 1
                    if e.get("days_before_5pct") is None and ret >= 0.05:
                        e["days_before_5pct"] = fwd
                    if e.get("days_before_10pct") is None and ret >= 0.10:
                        e["days_before_10pct"] = fwd
                    if ret >= 0.05:
                        e["time_to_peak"] = fwd
                        break

        # timing class
        d5 = e.get("days_before_5pct")
        ret5 = e.get("ret5")
        dist60 = e.get("distance_from_60d_high") or 0
        if dist60 > -2 and d5 is not None and d5 <= 1:
            e["timing_class"] = "TOO_LATE"
        elif d5 is not None and d5 >= 3:
            e["timing_class"] = "EARLY"
        elif d5 is not None and d5 <= 2:
            e["timing_class"] = "ON_TIME"
        elif ret5 is not None and ret5 < 0:
            e["timing_class"] = "FALSE"
        elif dist60 > -3:
            e["timing_class"] = "LATE"
        else:
            e["timing_class"] = "ON_TIME"

        # sequence flags
        sym_ev = by_sym_date[sym]
        d = e["trade_date"]
        if d in dates:
            di = dates.index(d)
            prev_dates = [dates[di - i] for i in range(1, 4) if di - i >= 0]
            prev_setups = []
            for pd in prev_dates:
                pe = sym_ev.get(pd)
                if pe:
                    prev_setups.append(pe.get("setups") or [])
            e["has_seq_pullback_impact"] = (
                prev_setups and "pullback_accum" in (prev_setups[0] or [])
                and "impact_expansion" in (e.get("setups") or [])
            )
            e["has_seq_failed_hr"] = (
                prev_setups and "failed_breakdown" in (prev_setups[0] or [])
                and e.get("hidden_repricing")
            )


def rule_metrics(pool: List[dict], dates: List[str]) -> dict:
    if not pool:
        return {"events": 0}
    with_ret = [e for e in pool if e.get("ret5") is not None]
    if not with_ret:
        return {"events": len(pool), "symbols": len({e["symbol"] for e in pool})}

    def block(h: int) -> dict:
        rk, hk = f"ret{h}", f"hit{h}"
        sub = [e for e in with_ret if e.get(rk) is not None]
        if not sub:
            return {"hit_rate": None, "pf": None, "avg_return": None, "median_return": None}
        rets = [e[rk] for e in sub]
        hits = [e[hk] for e in sub]
        wins = [r for r, hit in zip(rets, hits) if hit]
        losses = [abs(r) for r, hit in zip(rets, hits) if not hit]
        return {
            "hit_rate": round(sum(hits) / len(hits) * 100, 1),
            "pf": round(pf(wins, losses), 2),
            "avg_return": round(mean(rets) * 100, 2),
            "median_return": round(median(rets) * 100, 2),
        }

    rets5 = [e["ret5"] for e in with_ret]
    dds = [e.get("max_dd_20d") for e in with_ret if e.get("max_dd_20d") is not None]
    early = [e for e in with_ret if e.get("timing_class") == "EARLY"]
    ttp = [e.get("time_to_peak") for e in with_ret if e.get("time_to_peak")]

    return {
        "events": len(pool),
        "symbols_count": len({e["symbol"] for e in pool}),
        "sector_count": len({e.get("sector") or "Unknown" for e in pool}),
        "hit_5d": block(5)["hit_rate"],
        "hit_10d": block(10)["hit_rate"],
        "hit_20d": block(20)["hit_rate"],
        "pf": block(5)["pf"],
        "avg_return": block(5)["avg_return"],
        "median_return": block(5)["median_return"],
        "max_return": round(max(rets5) * 100, 2) if rets5 else None,
        "worst_return": round(min(rets5) * 100, 2) if rets5 else None,
        "avg_drawdown": round(mean(dds) * 100, 2) if dds else None,
        "max_drawdown": round(min(dds) * 100, 2) if dds else None,
        "time_to_peak": round(mean(ttp), 1) if ttp else None,
        "early_discovery_rate": round(100 * len(early) / len(with_ret), 1),
        "false_discovery_rate": round(100 * sum(1 for r in rets5 if r < 0) / len(rets5), 1),
    }


def classify_rule(metrics: dict, baseline_hr: dict) -> str:
    n = metrics.get("events", 0)
    if n < 30:
        return "NEEDS_DATA"
    hit = metrics.get("hit_5d") or 0
    base_hit = baseline_hr.get("hit_5d") or 20
    pf_val = metrics.get("pf") or 0
    early = metrics.get("early_discovery_rate") or 0
    if hit >= base_hit + 3 and pf_val >= 1.15 and n >= 50:
        return "ACCEPT_SHADOW"
    if hit >= base_hit + 1 and pf_val >= 1.0 and early >= 40:
        return "WATCH_SHADOW"
    if hit < base_hit - 2 or pf_val < 0.85:
        return "REJECT"
    return "WATCH_SHADOW"


# ── Alpha rule candidates from Phase 2.7 hypotheses ──────────────────────────

def build_alpha_candidates() -> List[dict]:
    return [
        {"rule_id": "MDE_V2_001", "name": "High Effective Hidden Repricing", "scope": "global",
         "condition": "hidden_repricing=1 AND effective_score>60",
         "predicate": lambda e: e.get("hidden_repricing") and e.get("effective_score", 0) > 60,
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_002", "name": "Failed Breakdown Setup", "scope": "setup",
         "condition": "setup=failed_breakdown",
         "predicate": lambda e: "failed_breakdown" in (e.get("setups") or []),
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_003", "name": "Pullback→Impact Sequence", "scope": "sequence",
         "condition": "pullback_accum prev day AND impact_expansion today",
         "predicate": lambda e: e.get("has_seq_pullback_impact"),
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_004", "name": "Mid Liquidity HR", "scope": "liquidity",
         "condition": "hidden_repricing=1 AND liquidity_track=Mid (3-10M)",
         "predicate": lambda e: e.get("hidden_repricing") and e.get("liquidity_track") == "Mid",
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_005", "name": "Early Discovery HR", "scope": "timing",
         "condition": "hidden_repricing=1 AND timing_class=EARLY",
         "predicate": lambda e: e.get("hidden_repricing") and e.get("timing_class") == "EARLY",
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_006", "name": "Impact Expansion Mid-Range", "scope": "setup",
         "condition": "setup=impact_expansion AND -15<distance_from_60d_high<-3",
         "predicate": lambda e: "impact_expansion" in (e.get("setups") or []) and -15 < (e.get("distance_from_60d_high") or 0) < -3,
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_007", "name": "Absorption Pre-Break Uptrend", "scope": "regime",
         "condition": "setup=absorption_pre_break AND market=uptrend",
         "predicate": lambda e: "absorption_pre_break" in (e.get("setups") or []) and (e.get("_regime") or {}).get("market") == "uptrend",
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_008", "name": "Sector Follower Expansion", "scope": "setup",
         "condition": "setup=sector_follower AND rel_turn>1.2",
         "predicate": lambda e: "sector_follower" in (e.get("setups") or []) and float((e.get("metrics") or {}).get("rel_turn") or 0) > 1.2,
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_009", "name": "Failed Breakdown + HR Combo", "scope": "sequence",
         "condition": "failed_breakdown prev AND hidden_repricing today",
         "predicate": lambda e: e.get("has_seq_failed_hr"),
         "effect": "shadow_candidate_upgrade"},
        {"rule_id": "MDE_V2_010", "name": "High Confidence Compressed ATR", "scope": "regime",
         "condition": "confidence>70 AND atr_state=compressed AND hidden_repricing",
         "predicate": lambda e: e.get("hidden_repricing") and e.get("confidence_score", 0) > 70 and e.get("atr_state") == "compressed",
         "effect": "shadow_candidate_upgrade"},
    ]


def validate_alpha_rules(events: List[dict], dates: List[str]) -> Tuple[List[dict], dict]:
    hr_pool = [e for e in events if e.get("hidden_repricing")]
    baseline = rule_metrics(hr_pool, dates)
    candidates = build_alpha_candidates()
    validated = []
    for cand in candidates:
        pool = [e for e in events if cand["predicate"](e)]
        m = rule_metrics(pool, dates)
        decision = classify_rule(m, baseline)
        # walk-forward positive rate (simple: last 20% dates OOS)
        split = int(len(dates) * 0.8)
        oos_dates = set(dates[split:])
        oos_pool = [e for e in pool if e["trade_date"] in oos_dates]
        is_pool = [e for e in pool if e["trade_date"] not in oos_dates]
        is_m = rule_metrics(is_pool, dates)
        oos_m = rule_metrics(oos_pool, dates)
        pos_windows = 1 if (oos_m.get("hit_5d") or 0) >= (is_m.get("hit_5d") or 0) else 0
        overfit = "high" if (is_m.get("hit_5d") or 0) - (oos_m.get("hit_5d") or 0) > 5 else "low" if decision == "ACCEPT_SHADOW" else "medium"

        validated.append({
            "rule_id": cand["rule_id"],
            "name": cand["name"],
            "condition": cand["condition"],
            "scope": cand["scope"],
            "effect": cand["effect"],
            "evidence": {**m, "positive_window_rate": pos_windows * 100, "in_sample_hit_5d": is_m.get("hit_5d"), "oos_hit_5d": oos_m.get("hit_5d")},
            "risk": {
                "overfit_risk": overfit,
                "sector_concentration": round(100 / max(m.get("sector_count", 1), 1), 1),
                "liquidity_bias": "high" if cand["scope"] == "liquidity" else "medium",
                "late_signal_risk": "low" if m.get("early_discovery_rate", 0) >= 50 else "medium",
            },
            "decision": decision,
            "status": "shadow_only",
            "_predicate": cand["predicate"],
        })
    return validated, baseline


def build_rule_stack(validated: List[dict]) -> List[dict]:
    return [
        {k: v for k, v in r.items() if k != "_predicate"}
        for r in validated
        if r["decision"] in ("ACCEPT_SHADOW", "WATCH_SHADOW")
    ]


def apply_rule_stack(e: dict, stack: List[dict]) -> Tuple[List[str], float, List[str]]:
    """Return matched rule_ids, boost score, risk flags."""
    matched = []
    boost = 0.0
    risks = []
    for r in stack:
        pred = r.get("_predicate")
        if pred and pred(e):
            matched.append(r["rule_id"])
            if r["decision"] == "ACCEPT_SHADOW":
                boost += 12
            else:
                boost += 6
    # false discovery penalties
    for fid, _, pred in FALSE_RULE_CANDIDATES:
        if pred(e):
            risks.append(fid)
            boost -= 8
    if e.get("timing_class") in ("LATE", "TOO_LATE"):
        risks.append("late_signal")
        boost -= 10
    if not matched:
        risks.append("no_rule_match")
    return matched, boost, risks


def backtest_rule_stack(events: List[dict], dates: List[str], stack: List[dict]) -> dict:
    hr = [e for e in events if e.get("hidden_repricing")]
    baseline_m = rule_metrics(hr, dates)
    stack_pool = []
    for e in hr:
        matched, boost, risks = apply_rule_stack(e, stack)
        if matched and boost > 0:
            e2 = dict(e)
            e2["rules_matched"] = matched
            e2["rule_boost"] = boost
            e2["risk_flags"] = risks
            stack_pool.append(e2)
    stack_m = rule_metrics(stack_pool, dates)
    early_b = baseline_m.get("early_discovery_rate") or 0
    early_s = stack_m.get("early_discovery_rate") or 0
    return {
        "baseline": baseline_m,
        "rule_stack": stack_m,
        "comparison": {
            "delta_hit_5d": round((stack_m.get("hit_5d") or 0) - (baseline_m.get("hit_5d") or 0), 1),
            "delta_pf": round((stack_m.get("pf") or 0) - (baseline_m.get("pf") or 0), 2),
            "delta_avg_return": round((stack_m.get("avg_return") or 0) - (baseline_m.get("avg_return") or 0), 2),
            "candidate_reduction_pct": round(100 * (1 - stack_m.get("events", 0) / max(baseline_m.get("events", 1), 1)), 1),
            "early_discovery_preserved": early_s >= early_b * 0.85,
            "early_discovery_delta": round(early_s - early_b, 1),
        },
        "verdict": (
            "Rule Stack v2 improves quality while preserving early discovery"
            if (stack_m.get("hit_5d") or 0) > (baseline_m.get("hit_5d") or 0) + 2
            and early_s >= early_b * 0.8
            else "Rule Stack v2 improves hit rate but may delay discovery"
            if (stack_m.get("hit_5d") or 0) > (baseline_m.get("hit_5d") or 0)
            else "Rule Stack v2 needs refinement"
        ),
    }


def analog_similarity(current: dict, historical: dict) -> float:
    score = 0.0
    cs = set(current.get("setups") or [])
    hs = set(historical.get("setups") or [])
    if cs & hs:
        score += 3
    if current.get("sector") == historical.get("sector"):
        score += 2
    if current.get("liquidity_track") == historical.get("liquidity_track"):
        score += 2
    if (current.get("_regime") or {}).get("market") == (historical.get("_regime") or {}).get("market"):
        score += 1.5
    if abs((current.get("effective_score") or 0) - (historical.get("effective_score") or 0)) <= 15:
        score += 1
    if current.get("timing_class") == historical.get("timing_class"):
        score += 1
    if current.get("has_seq_pullback_impact") and historical.get("has_seq_pullback_impact"):
        score += 2
    if current.get("atr_state") == historical.get("atr_state"):
        score += 0.5
    dist_c = current.get("distance_from_60d_high") or 0
    dist_h = historical.get("distance_from_60d_high") or 0
    if abs(dist_c - dist_h) <= 5:
        score += 1
    return score


def find_analogs(current: dict, history: List[dict], min_score: float = 5.0, max_n: int = 50) -> List[dict]:
    scored = []
    for h in history:
        if h["symbol"] == current["symbol"] and h["trade_date"] == current["trade_date"]:
            continue
        if h["trade_date"] >= current["trade_date"]:
            continue
        s = analog_similarity(current, h)
        if s >= min_score:
            scored.append((s, h))
    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored[:max_n]]


def analog_stats(analogs: List[dict]) -> dict:
    if not analogs:
        return {"count": 0, "confidence": 0}
    m = rule_metrics(analogs, [])
    failures = [e for e in analogs if e.get("ret5") is not None and e["ret5"] < 0]
    ttm = [e.get("days_before_5pct") for e in analogs if e.get("days_before_5pct")]
    return {
        "historical_analogs_count": len(analogs),
        "analog_hit_5d": m.get("hit_5d"),
        "analog_hit_10d": m.get("hit_10d"),
        "analog_hit_20d": m.get("hit_20d"),
        "analog_PF": m.get("pf"),
        "analog_avg_return": m.get("avg_return"),
        "analog_median_return": m.get("median_return"),
        "analog_worst_drawdown": m.get("max_drawdown"),
        "analog_best_holding_window": "5-10d" if (m.get("hit_10d") or 0) > (m.get("hit_5d") or 0) else "5d",
        "analog_time_to_move": round(mean(ttm), 1) if ttm else None,
        "analog_failure_pattern": "high_false_rate" if len(failures) > len(analogs) * 0.5 else "moderate",
        "analog_confidence": round(min(100, len(analogs) * 2 + (m.get("hit_5d") or 0)), 1),
    }


def opportunity_quality_score(e: dict, analog: dict, matched: List[str], risks: List[str]) -> float:
    score = 0.0
    score += 0.20 * min(100, e.get("effective_score", 0))
    score += 0.20 * (analog.get("analog_hit_5d") or 0)
    score += 0.15 * min(3, analog.get("analog_PF") or 0) / 3 * 100
    early_map = {"EARLY": 100, "ON_TIME": 70, "LATE": 30, "TOO_LATE": 10, "FALSE": 0}
    score += 0.15 * early_map.get(e.get("timing_class", "ON_TIME"), 50)
    liq_map = {"Institutional": 90, "Liquid": 80, "Mid": 85, "Low/Mid": 60, "Thin": 30}
    score += 0.10 * liq_map.get(e.get("liquidity_track", "Mid"), 50)
    regime = (e.get("_regime") or {}).get("market", "sideways")
    score += 0.10 * (80 if regime == "uptrend" else 50 if regime == "sideways" else 20)
    score += 0.05 * min(100, len(matched) * 20)
    dd_safety = 100 + (analog.get("analog_worst_drawdown") or -15) * 3
    score += 0.05 * max(0, min(100, dd_safety))
    # penalties
    if risks:
        score -= min(30, len(risks) * 8)
    if analog.get("historical_analogs_count", 0) < 5:
        score -= 15
    if e.get("timing_class") in ("LATE", "TOO_LATE"):
        score -= 20
    return round(max(0, min(100, score)), 1)


def candidate_decision(oqs: float, analog: dict, risks: List[str], timing: str) -> str:
    if timing in ("LATE", "TOO_LATE", "FALSE"):
        return "REJECT" if timing == "FALSE" else "WEAK_CANDIDATE"
    if oqs >= 65 and (analog.get("analog_hit_5d") or 0) >= 22 and len(risks) <= 1:
        return "HIGH_QUALITY_SHADOW_CANDIDATE"
    if oqs >= 45 and analog.get("historical_analogs_count", 0) >= 5:
        return "WATCH_CANDIDATE"
    if oqs >= 30:
        return "WEAK_CANDIDATE"
    return "REJECT"


def build_triggers(e: dict) -> Tuple[str, str]:
    m = e.get("metrics") or {}
    setups = e.get("setups") or []
    conf_parts = []
    if "failed_breakdown" in setups:
        conf_parts.append("close holds above spring low + rel_turn>1.2 next session")
    if "impact_expansion" in setups:
        conf_parts.append("close above 20d high + rel_turn>1.3")
    if "absorption_pre_break" in setups:
        conf_parts.append("CLV>0.6 + volume follow-through")
    if e.get("hidden_repricing"):
        conf_parts.append("effective_score remains >55")
    if not conf_parts:
        conf_parts.append("rel_turn>1.2 + close in upper half of range")
    inv_parts = ["effective_score drops below 50"]
    if "failed_breakdown" in setups:
        inv_parts.append("close below spring low")
    if e.get("timing_class") in ("LATE", "TOO_LATE"):
        inv_parts.append("no 5d follow-through after signal")
    return " AND ".join(conf_parts[:2]), " OR ".join(inv_parts[:2])


def load_opp_layers(conn: sqlite3.Connection, dates: List[str]) -> Dict[str, dict]:
    """opp / final / actionable per date."""
    layers: Dict[str, dict] = {}
    for d in dates:
        opp, fs, act = set(), set(), set()
        try:
            for r in conn.execute("SELECT symbol FROM opportunity_score_v2 WHERE trade_date=?", (d,)).fetchall():
                opp.add(r["symbol"])
            for r in conn.execute("SELECT symbol, actionable FROM final_signals WHERE trade_date=?", (d,)).fetchall():
                fs.add(r["symbol"])
                if r["actionable"]:
                    act.add(r["symbol"])
        except sqlite3.OperationalError:
            pass
        layers[d] = {"opp": opp, "final": fs, "actionable": act}
    return layers


def mde_only_study(events: List[dict], layers: Dict[str, dict], dates: List[str]) -> dict:
    daily = []
    outside_pool = []
    for d in dates:
        mde_hr = {e["symbol"] for e in events if e["trade_date"] == d and e["hidden_repricing"]}
        ly = layers.get(d, {})
        opp, act = ly.get("opp", set()), ly.get("actionable", set())
        mde_only = mde_hr - opp
        for sym in mde_only:
            ev = next((e for e in events if e["symbol"] == sym and e["trade_date"] == d), None)
            if ev:
                outside_pool.append(ev)
        daily.append({
            "trade_date": d,
            "mde_hr": len(mde_hr),
            "mde_only": len(mde_only),
            "mde_and_opp": len(mde_hr & opp),
            "mde_before_actionable": len(mde_hr - act),
        })
    outside_m = rule_metrics(outside_pool, dates)
    return {
        "daily_summary": daily[-90:],
        "outside_opp_metrics": outside_m,
        "outside_opp_success_rate": outside_m.get("hit_5d"),
        "outside_opp_pf": outside_m.get("pf"),
        "recurring_outside": [s for s, c in Counter(e["symbol"] for e in outside_pool).most_common(15) if c >= 3],
    }


def sequence_alpha_mining(events: List[dict], dates: List[str], latest_date: str) -> List[dict]:
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)

    rows = []
    for seq_id, seq_name in SEQUENCE_ALPHA:
        parts = [p.strip() for p in seq_name.replace("→", "->").split("->")]
        pool = []
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
                        if not ({"accum_breakout", "impact_expansion"} & set(ev.get("setups") or [])):
                            match = False
                            break
                    elif part == "pullback":
                        if "pullback_accum" not in (ev.get("setups") or []):
                            match = False
                            break
                    else:
                        if part not in (ev.get("setups") or []):
                            match = False
                            break
                if match:
                    pool.append(evs[i + len(parts) - 1])

        m = rule_metrics(pool, dates)
        current = [e["symbol"] for e in events if e["trade_date"] == latest_date and e in pool]
        # re-check current with sequence logic
        current_syms = []
        for sym, evs in by_sym.items():
            today = [e for e in evs if e["trade_date"] == latest_date]
            if not today:
                continue
            evs_s = sorted(evs, key=lambda x: x["trade_date"])
            idx = next((i for i, e in enumerate(evs_s) if e["trade_date"] == latest_date), None)
            if idx is None or idx < len(parts) - 1:
                continue
            window = evs_s[idx - len(parts) + 1: idx + 1]
            if len(window) == len(parts):
                ok = True
                for j, part in enumerate(parts):
                    if part == "HR" and not window[j].get("hidden_repricing"):
                        ok = False
                    elif part not in ("HR", "move", "compression", "breakout", "pullback") and part not in (window[j].get("setups") or []):
                        ok = False
                if ok:
                    current_syms.append(sym)

        decision = classify_rule(m, {"hit_5d": 22})
        rows.append({
            "sequence_id": seq_id,
            "sequence": seq_name,
            **m,
            "median_days_between_steps": 1,
            "best_holding_window": "10d" if (m.get("hit_10d") or 0) > (m.get("hit_5d") or 0) else "5d",
            "failure_conditions": "late timing or low rel_turn follow-through",
            "current_candidates": current_syms,
            "decision": decision,
        })
    return rows


def regime_layer(candidates: List[dict], history: List[dict]) -> List[dict]:
    rows = []
    for c in candidates:
        setup = (c.get("setups") or [""])[0] if c.get("setups") else "HR"
        regime_pool = [
            h for h in history
            if setup in (h.get("setups") or []) or (setup == "HR" and h.get("hidden_repricing"))
        ]
        by_regime: Dict[str, List[dict]] = defaultdict(list)
        for h in regime_pool:
            by_regime[(h.get("_regime") or {}).get("market", "unknown")].append(h)
        best_reg = max(by_regime, key=lambda k: rule_metrics(by_regime[k], []).get("hit_5d") or 0) if by_regime else "unknown"
        cur_reg = (c.get("_regime") or {}).get("market", "unknown")
        rows.append({
            "symbol": c["symbol"],
            "setup": setup,
            "current_market_regime": cur_reg,
            "current_sector_regime": c.get("sector"),
            "current_stock_regime": (c.get("_regime") or {}).get("vs_ma50"),
            "best_historical_regime_for_setup": best_reg,
            "regime_match_score": 100 if cur_reg == best_reg else 50 if cur_reg == "sideways" else 25,
        })
    return rows


def liquidity_playbooks(events: List[dict], latest_date: str) -> List[dict]:
    playbooks = []
    latest_hr = [e for e in events if e["trade_date"] == latest_date and e.get("hidden_repricing")]
    for track_name, lo, hi in LIQUIDITY_TRACKS:
        pool = [e for e in events if e.get("hidden_repricing") and lo <= (e.get("turnover_egp") or 0) < hi]
        m = rule_metrics(pool, [])
        setup_perf = {}
        for sk in SETUP_KEYS:
            sub = [e for e in pool if sk in (e.get("setups") or [])]
            if len(sub) >= 20:
                setup_perf[sk] = rule_metrics(sub, [])
        best = max(setup_perf, key=lambda k: setup_perf[k].get("hit_5d") or 0) if setup_perf else None
        worst = min(setup_perf, key=lambda k: setup_perf[k].get("hit_5d") or 0) if setup_perf else None
        current = [e["symbol"] for e in latest_hr if e.get("liquidity_track") == track_name]
        playbooks.append({
            "track": track_name,
            "events": m.get("events"),
            "best_setups": best,
            "worst_setups": worst,
            "hit_rate": m.get("hit_5d"),
            "pf": m.get("pf"),
            "expected_drawdown": m.get("avg_drawdown"),
            "holding_window": "10d" if (m.get("hit_10d") or 0) > (m.get("hit_5d") or 0) else "5d",
            "risk_flags": ["thin_track_false_discovery"] if track_name == "Thin" else [],
            "current_candidates": current,
        })
    return playbooks


def false_discovery_stack(events: List[dict]) -> List[dict]:
    hr = [e for e in events if e.get("hidden_repricing")]
    rows = []
    for fid, desc, pred in FALSE_RULE_CANDIDATES:
        affected = [e for e in hr if pred(e)]
        if len(affected) < 15:
            continue
        m = rule_metrics(affected, [])
        fail_rets = [e["ret5"] for e in affected if e.get("ret5") is not None and e["ret5"] < 0]
        rows.append({
            "false_rule_id": fid,
            "condition": desc,
            "failure_rate": m.get("false_discovery_rate"),
            "avg_loss": round(mean(fail_rets) * 100, 2) if fail_rets else None,
            "drawdown": m.get("avg_drawdown"),
            "affected_setups": list({s for e in affected for s in (e.get("setups") or [])})[:5],
            "affected_sectors": list({e.get("sector") for e in affected if e.get("sector")})[:5],
            "recommendation": "confidence_penalty",
            "effect": "confidence_penalty",
            "status": "shadow_only",
        })
    return rows


def family_playbooks_v2(events: List[dict], latest_date: str, history: List[dict]) -> List[dict]:
    books = []
    for fam_id, name, pred in FAMILY_DEFS:
        pool = [e for e in events if pred(e)]
        if len(pool) < 20:
            continue
        m = rule_metrics(pool, dates=[])
        sectors = Counter(e.get("sector") for e in pool if e.get("sector")).most_common(3)
        regimes = Counter((e.get("_regime") or {}).get("market") for e in pool).most_common(2)
        current = [e["symbol"] for e in events if e["trade_date"] == latest_date and pred(e)]
        examples = sorted(pool, key=lambda x: -(x.get("ret5") or 0))[:3]
        books.append({
            "family_id": fam_id,
            "name": name,
            "definition": name,
            "why_it_works": f"hit_5d={m.get('hit_5d')}% PF={m.get('pf')} over {m.get('events')} events",
            "when_it_works": f"regimes: {[r[0] for r in regimes]} | sectors: {[s[0] for s in sectors]}",
            "when_it_fails": "late timing, low rel_turn, downtrend market",
            "best_sectors": [s[0] for s in sectors],
            "best_liquidity_bucket": Counter(e.get("liquidity_track") for e in pool).most_common(1)[0][0] if pool else None,
            "best_regime": regimes[0][0] if regimes else None,
            "best_holding_window": "10d" if (m.get("hit_10d") or 0) > (m.get("hit_5d") or 0) else "5d",
            "required_confirmations": ["rel_turn>1.2", "effective>50"],
            "risk_flags": ["late_signal", "thin_liquidity"],
            "current_candidates": current,
            "historical_analog_examples": [
                {"symbol": e["symbol"], "date": e["trade_date"], "ret5": round((e.get("ret5") or 0) * 100, 2)}
                for e in examples
            ],
            "metrics": m,
        })
    return books


def extract_current_opportunities(
    events: List[dict],
    history: List[dict],
    stack: List[dict],
    latest_date: str,
    layers: Dict[str, dict],
) -> Tuple[List[dict], List[dict], List[dict]]:
    today_events = [e for e in events if e["trade_date"] == latest_date]
    ly = layers.get(latest_date, {})
    opp, act = ly.get("opp", set()), ly.get("actionable", set())

    candidates = []
    analogs_out = []
    for e in today_events:
        if not e.get("hidden_repricing") and not (e.get("setups") or []):
            continue
        matched, boost, risks = apply_rule_stack(e, stack)
        if not matched and e.get("discovery_score", 0) < 40:
            continue
        analogs = find_analogs(e, history)
        astat = analog_stats(analogs)
        oqs = opportunity_quality_score(e, astat, matched, risks)
        timing = e.get("timing_class", "ON_TIME")
        decision = candidate_decision(oqs, astat, risks, timing)
        conf, inv = build_triggers(e)
        mde_only = e["symbol"] not in opp
        mde_before_act = e["symbol"] not in act

        cand = {
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "liquidity_bucket": e.get("liquidity_track"),
            "market_cap_bucket": e.get("market_cap_bucket"),
            "mde_stage": e.get("mde_stage"),
            "discovery_score": e.get("discovery_score"),
            "confidence_score": e.get("confidence_score"),
            "effective_score": e.get("effective_score"),
            "rules_matched": matched,
            "setup_sequence": e.get("setups"),
            "timing_class": timing,
            "distance_from_20d_high": e.get("distance_from_20d_high"),
            "distance_from_60d_high": e.get("distance_from_60d_high"),
            "atr_state": e.get("atr_state"),
            "compression_state": e.get("compression_state"),
            "volume_state": e.get("volume_state"),
            **astat,
            "expected_drawdown": astat.get("analog_worst_drawdown"),
            "best_holding_window": astat.get("analog_best_holding_window"),
            "early_on_time_late": timing,
            "mde_only_or_existing": "MDE_ONLY" if mde_only else "MDE_AND_OPP",
            "mde_before_actionable": mde_before_act,
            "risk_flags": risks,
            "confirmation_trigger": conf,
            "invalidation_trigger": inv,
            "opportunity_quality_score": oqs,
            "decision": decision,
        }
        candidates.append(cand)
        if analogs:
            analogs_out.append({
                "symbol": e["symbol"],
                "trade_date": latest_date,
                "analog_count": len(analogs),
                "top_analogs": [
                    {"symbol": a["symbol"], "date": a["trade_date"], "similarity_reason": "setup+sector+liquidity+regime",
                     "ret5": round((a.get("ret5") or 0) * 100, 2), "hit5": a.get("hit5")}
                    for a in analogs[:8]
                ],
                **astat,
            })

    ranked = sorted(candidates, key=lambda x: -x.get("opportunity_quality_score", 0))
    return candidates, ranked, analogs_out


def render_report(doc: dict) -> str:
    d = doc
    lines = [
        "# MDE Phase 2.8 — Actionable Discovery Extraction Report",
        "",
        f"**Generated:** {d['at']}",
        f"**Latest date:** {d['latest_date']}",
        "",
        "> Shadow only. No client path. No promotion. No EGX_MDE_BEHAVIOR_MEMORY=1.",
        "",
        "## Institutional Answers",
        "",
    ]
    for q, a in d.get("answers", {}).items():
        lines.append(f"### {q}")
        lines.append(a)
        lines.append("")

    lines.extend(["## Rule Stack v2 Backtest", ""])
    bt = d.get("backtest", {})
    lines.append(f"**Verdict:** {bt.get('verdict')}")
    cmp = bt.get("comparison", {})
    for k, v in cmp.items():
        lines.append(f"- {k}: {v}")

    lines.extend(["", "## Top 10 Current Candidates", ""])
    lines.append("| rank | symbol | OQS | analog_hit | timing | decision |")
    lines.append("|---:|---|---:|---:|---|---|")
    for i, c in enumerate(d.get("top10", []), 1):
        lines.append(
            f"| {i} | {c['symbol']} | {c.get('opportunity_quality_score')} | "
            f"{c.get('analog_hit_5d')} | {c.get('timing_class')} | {c.get('decision')} |"
        )

    lines.extend(["", "## Accepted Shadow Rules", ""])
    for r in d.get("accepted_rules", []):
        lines.append(f"- **{r['rule_id']}** {r['name']}: hit_5d={r['evidence'].get('hit_5d')}% decision={r['decision']}")
    if d.get("watch_rules"):
        lines.extend(["", "## WATCH Shadow Rules", ""])
        for r in d["watch_rules"]:
            lines.append(f"- **{r['rule_id']}** {r['name']}: hit_5d={r['evidence'].get('hit_5d')}% PF={r['evidence'].get('pf')}")

    lines.extend(["", "## MDE-Only Opportunities", ""])
    for s in d.get("mde_only_current", [])[:10]:
        lines.append(f"- {s}")

    lines.extend([
        "",
        "```text",
        "EGX_MDE_BEHAVIOR_MEMORY=0 | EGX_MDE_OPP_BOOST=0 | No Phase 3",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    print("═══ Phase 2.8: Actionable Discovery Extraction ═══", flush=True)

    events, by_sym = load_events(conn)
    dates, by_date = date_index(events)
    print(f"  loaded {len(events)} events, {len(dates)} dates", flush=True)

    enrich_events(events, by_sym, dates)
    latest_date = dates[-1]
    history = [e for e in events if e["trade_date"] < latest_date]

    print("  validating alpha rules...", flush=True)
    validated, baseline_hr = validate_alpha_rules(events, dates)
    stack = build_rule_stack(validated)
    # keep predicates on stack for application
    pred_map = {r["rule_id"]: r["_predicate"] for r in validated}
    for r in stack:
        r["_predicate"] = pred_map.get(r["rule_id"])

    print("  backtesting rule stack...", flush=True)
    backtest = backtest_rule_stack(events, dates, stack)

    print("  loading opp layers...", flush=True)
    layers = load_opp_layers(conn, dates)
    for e in events:
        if e["trade_date"] == latest_date:
            e["mde_only"] = e["symbol"] not in layers.get(latest_date, {}).get("opp", set())

    print("  mde-only study...", flush=True)
    mde_only_doc = mde_only_study(events, layers, dates)

    print("  sequence alpha...", flush=True)
    seq_alpha = sequence_alpha_mining(events, dates, latest_date)

    print("  extracting current opportunities...", flush=True)
    candidates, ranked, analogs = extract_current_opportunities(events, history, stack, latest_date, layers)

    print("  regime + liquidity + families...", flush=True)
    regime_rows = regime_layer(ranked[:30], history)
    liq_playbooks = liquidity_playbooks(events, latest_date)
    false_stack = false_discovery_stack(events)
    families = family_playbooks_v2(events, latest_date, history)

    at = datetime.now(timezone.utc).isoformat()
    accepted = [r for r in validated if r["decision"] == "ACCEPT_SHADOW"]
    watch = [r for r in validated if r["decision"] == "WATCH_SHADOW"]
    rejected = [r for r in validated if r["decision"] == "REJECT"]
    needs_data = [r for r in validated if r["decision"] == "NEEDS_DATA"]

    top10 = ranked[:10]
    hq = [c for c in ranked if c["decision"] == "HIGH_QUALITY_SHADOW_CANDIDATE"]
    mde_only_current = [c["symbol"] for c in ranked if c.get("mde_only_or_existing") == "MDE_ONLY" and c["decision"] != "REJECT"]

    outputs = {
        "rule_stack": {"at": at, "rules": [{k: v for k, v in r.items() if k != "_predicate"} for r in stack]},
        "backtest": {"at": at, **backtest},
        "extraction": {"at": at, "latest_date": latest_date, "candidates": candidates},
        "analogs": {"at": at, "latest_date": latest_date, "analogs": analogs},
        "ranking": {"at": at, "latest_date": latest_date, "ranked": ranked},
        "mde_only": {"at": at, **mde_only_doc, "current_mde_only": mde_only_current, "current_outside_opp": mde_only_current[:15]},
        "sequence_alpha": {"at": at, "sequences": seq_alpha},
        "regime_layer": {"at": at, "candidates": regime_rows},
        "liquidity_playbooks": {"at": at, "tracks": liq_playbooks},
        "false_stack": {"at": at, "rules": false_stack},
        "families": {"at": at, "playbooks": families},
        "alpha_validation": {"at": at, "baseline_hr": baseline_hr, "all_rules": [{k: v for k, v in r.items() if k != "_predicate"} for r in validated]},
    }

    paths = {
        "rule_stack": DATA / "mde_rule_stack_v2.json",
        "backtest": DATA / "mde_rule_stack_v2_backtest.json",
        "extraction": DATA / "mde_current_opportunity_extraction.json",
        "analogs": DATA / "mde_historical_analogs.json",
        "ranking": DATA / "mde_current_candidate_ranking.json",
        "mde_only": DATA / "mde_mde_only_opportunities.json",
        "sequence_alpha": DATA / "mde_sequence_alpha.json",
        "regime_layer": DATA / "mde_regime_opportunity_layer.json",
        "liquidity_playbooks": DATA / "mde_liquidity_track_playbooks.json",
        "false_stack": DATA / "mde_false_discovery_rule_stack.json",
        "families": DATA / "mde_family_playbooks_v2.json",
        "report": ROOT / "docs" / "MDE_PHASE_2_8_ACTIONABLE_DISCOVERY_REPORT.md",
    }
    for key, path in paths.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    answers = {
        "1. القواعد المقبولة Shadow؟": ", ".join(r["rule_id"] for r in accepted) or "none yet — see WATCH list",
        "2. القواعد المرفوضة؟": ", ".join(r["rule_id"] for r in rejected) or "none",
        "3. تحتاج بيانات؟": ", ".join(r["rule_id"] for r in needs_data) or "none",
        "4. أفضل 10 فرص حالية؟": ", ".join(f"{c['symbol']}({c['opportunity_quality_score']})" for c in top10),
        "5. أفضل MDE-only؟": ", ".join(mde_only_current[:10]) or "none today",
        "6. عائلات تستحق التطوير؟": ", ".join(f["name"] for f in families if (f.get("metrics") or {}).get("hit_5d", 0) >= 24),
        "7. عائلات يجب تجاهلها؟": "Thin Track pure HR without setup confirmation",
        "8. Rule Stack v2 يحسن Baseline؟": f"{'نعم' if (backtest['comparison'].get('delta_hit_5d') or 0) > 0 else 'لا'} Δhit={backtest['comparison'].get('delta_hit_5d')}pp",
        "9. يحافظ على early discovery؟": str(backtest["comparison"].get("early_discovery_preserved")),
        "10. يدخل MDE v2 Shadow Brain؟": "Rule Stack v2 + analog engine + false penalties + regime/liquidity layers (shadow tags only)",
    }

    report_doc = {
        "at": at,
        "latest_date": latest_date,
        "answers": answers,
        "backtest": backtest,
        "top10": top10,
        "accepted_rules": [{k: v for k, v in r.items() if k != "_predicate"} for r in accepted],
        "watch_rules": [{k: v for k, v in r.items() if k != "_predicate"} for r in watch],
        "high_quality_count": len(hq),
    }
    paths["report"].write_text(render_report(report_doc), encoding="utf-8")

    conn.close()
    print(f"  latest={latest_date} candidates={len(candidates)} HQ={len(hq)}", flush=True)
    print("  done.", flush=True)
    return {
        "success": True,
        "latest_date": latest_date,
        "candidates": len(candidates),
        "high_quality": len(hq),
        "accepted_rules": len(accepted),
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
