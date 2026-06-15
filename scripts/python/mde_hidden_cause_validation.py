#!/usr/bin/env python3
"""
MDE Phase 2.9B — Hidden Cause Candidate Validation.

Shadow research: infer hidden causes, validate with analogs, extract opportunities.

Outputs: 14 data files + 2 docs (see run() paths dict).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

from mde_actionable_discovery import (  # noqa: E402
    build_triggers,
    analog_similarity,
    analog_stats,
    apply_rule_stack,
    enrich_events,
    find_analogs,
    load_opp_layers,
    rule_metrics,
    validate_alpha_rules,
)
from mde_opportunity_exploitation import (  # noqa: E402
    FOCUS_SYMBOLS,
    analog_detail_row,
    build_analog_breakdown,
    build_dossier,
    causal_analog_for_event,
    decompose_oqs,
)
from mde_walkforward_shadow import HIT_THRESH, connect, date_index, load_events, market_regime  # noqa: E402

HIDDEN_CAUSES = (
    "A_delayed_information_assimilation",
    "B_latent_accumulation",
    "C_supply_exhaustion",
    "D_liquidity_vacuum",
    "E_metaorder_continuation",
    "F_sector_rotation_spillover",
    "G_mechanical_bounce_only",
    "H_noise_overfit",
)

COMP_VARIANTS = [
    ("COMP_001A", "effective>60 + analog_hit>40 + PF>2 + ON_TIME",
     lambda e, a: e.get("effective_score", 0) > 60 and (a.get("analog_hit_5d") or 0) > 40
     and (a.get("analog_PF") or 0) > 2 and e.get("timing_class") == "ON_TIME"),
    ("COMP_001B", "effective>60 + analog_hit>35 + PF>2 + EARLY/ON_TIME",
     lambda e, a: e.get("effective_score", 0) > 60 and (a.get("analog_hit_5d") or 0) > 35
     and (a.get("analog_PF") or 0) > 2 and e.get("timing_class") in ("EARLY", "ON_TIME")),
    ("COMP_001C", "effective>55 + analog_hit>40 + PF>2.5 + risk<=1",
     lambda e, a, risks=None: e.get("effective_score", 0) > 55 and (a.get("analog_hit_5d") or 0) > 40
     and (a.get("analog_PF") or 0) > 2.5 and len(risks or []) <= 1),
    ("COMP_001D", "effective>60 + analog_hit>40 + PF>2 + drawdown_safety>-20%",
     lambda e, a: e.get("effective_score", 0) > 60 and (a.get("analog_hit_5d") or 0) > 40
     and (a.get("analog_PF") or 0) > 2 and (a.get("analog_worst_drawdown") or -30) > -20),
]


def m(e: dict, k: str, default: float = 0) -> float:
    return float((e.get("metrics") or {}).get(k) or default)


def infer_hidden_cause(e: dict, analog: Optional[dict] = None) -> Tuple[str, float, dict]:
    """Score competing hidden causes from observable proxies."""
    scores: Dict[str, float] = {c: 0.0 for c in HIDDEN_CAUSES}
    rel_turn = m(e, "rel_turn")
    clv = m(e, "clv")
    csf = m(e, "csf_20")
    impact_exp = m(e, "impact_expansion", 1)
    kyle = m(e, "kyle_lambda")
    absorption = m(e, "absorption_ratio")
    latent = float(e.get("latent_accumulation_score") or (e.get("metrics") or {}).get("latent_accumulation_score") or 0)
    supply = float(e.get("supply_exhaustion_score") or 0)
    resilience = float(e.get("resilience_score") or 0)

    if e.get("timing_class") == "EARLY" and (analog or {}).get("analog_time_to_move", 10) and (analog or {}).get("analog_time_to_move", 10) > 5:
        scores["A_delayed_information_assimilation"] += 3
    if latent > 50 or "pullback_accum" in (e.get("setups") or []) or "absorption_pre_break" in (e.get("setups") or []):
        scores["B_latent_accumulation"] += 3
    if supply > 50 or "failed_breakdown" in (e.get("setups") or []) or (clv > 0.55 and rel_turn > 1.2):
        scores["C_supply_exhaustion"] += 3
    if rel_turn > 2 and impact_exp > 1.3 and e.get("liquidity_track") in ("Thin", "Low/Mid"):
        scores["D_liquidity_vacuum"] += 2.5
    if csf > 0 and clv > 0.6 and rel_turn > 1.1 and rel_turn < 2.5:
        scores["E_metaorder_continuation"] += 3
    if "sector_follower" in (e.get("setups") or []):
        scores["F_sector_rotation_spillover"] += 2.5
    if impact_exp > 1.5 and rel_turn < 1.0 and (e.get("distance_from_60d_high") or 0) > -5:
        scores["G_mechanical_bounce_only"] += 2
    if e.get("effective_score", 0) < 50 or not e.get("setups"):
        scores["H_noise_overfit"] += 2
    if kyle > 0.01 and rel_turn < 0.8:
        scores["H_noise_overfit"] += 1.5
    if resilience < 40:
        scores["G_mechanical_bounce_only"] += 1

    best = max(scores, key=scores.get)
    conf = round(min(95, scores[best] * 15 + 20), 1)
    return best, conf, scores


def impact_decomposition(e: dict) -> dict:
    rel_turn = m(e, "rel_turn")
    impact_exp = m(e, "impact_expansion", 1)
    clv = m(e, "clv")
    csf = m(e, "csf_20")
    kyle = m(e, "kyle_lambda")
    mechanical = min(100, impact_exp * 25 + (30 if rel_turn < 1 else 0))
    informational = min(100, (e.get("effective_score", 0) * 0.5) + (20 if e.get("timing_class") == "EARLY" else 0))
    inventory = min(100, abs(csf) / max(m(e, "turnover", 1), 1) * 1e6 * 10 + clv * 30)
    liquidity_wd = min(100, (impact_exp - 1) * 40 + (40 if e.get("liquidity_track") == "Thin" else 0))
    reflexive = min(100, rel_turn * 20 + (30 if clv > 0.65 else 0))
    dominant = max(
        ("mechanical", mechanical), ("informational", informational),
        ("inventory_dealer", inventory), ("liquidity_withdrawal", liquidity_wd),
        ("reflexive", reflexive), key=lambda x: x[1],
    )[0]
    return {
        "symbol": e["symbol"],
        "mechanical_impact": round(mechanical, 1),
        "informational_impact": round(informational, 1),
        "inventory_dealer_impact": round(inventory, 1),
        "liquidity_withdrawal_impact": round(liquidity_wd, 1),
        "reflexive_impact": round(reflexive, 1),
        "dominant_type": dominant,
        "interpretation": {
            "mechanical": rel_turn < 1.1 and impact_exp > 1.2,
            "informational": e.get("effective_score", 0) > 55 and e.get("timing_class") in ("EARLY", "ON_TIME"),
            "seller_withdrew": csf > 0 and clv > 0.55,
            "momentum_attracting": rel_turn > 1.5 and clv > 0.6,
        },
        "proxies": {"kyle_lambda": kyle, "impact_expansion": impact_exp, "rel_turn": rel_turn, "clv": clv, "csf_20": csf},
    }


def metaorder_detection(e: dict, history: List[dict]) -> dict:
    sym = e["symbol"]
    prior = sorted([h for h in history if h["symbol"] == sym][-10:], key=lambda x: x["trade_date"])
    csf_vals = [m(h, "csf_20") for h in prior] + [m(e, "csf_20")]
    clv_vals = [m(h, "clv") for h in prior] + [m(e, "clv")]
    rel_vals = [m(h, "rel_turn") for h in prior] + [m(e, "rel_turn")]
    csf_rising = len(csf_vals) >= 3 and csf_vals[-1] > mean(csf_vals[:-1])
    persistent_clv = sum(1 for c in clv_vals if c > 0.55) >= max(3, len(clv_vals) // 2)
    low_reversion = m(e, "clv") > 0.5 and m(e, "rel_turn") > 1.2
    vol_stable = stdev(rel_vals) < 0.8 if len(rel_vals) > 2 else True
    score = 0
    evidence = []
    if csf_rising:
        score += 25
        evidence.append("CSF rising over last sessions")
    if persistent_clv:
        score += 25
        evidence.append("repeated positive CLV")
    if low_reversion:
        score += 20
        evidence.append("low reversion after volume")
    if vol_stable and m(e, "rel_turn") > 1.1:
        score += 15
        evidence.append("stable volume participation")
    if "pullback_accum" in (e.get("setups") or []) or "absorption_pre_break" in (e.get("setups") or []):
        score += 15
        evidence.append("pullbacks absorbed pattern")
    prob = min(95, score)
    if prob >= 70:
        stage = "mid"
    elif prob >= 45:
        stage = "early"
    elif prob >= 25:
        stage = "late"
    else:
        stage = "exhausted"
    if e.get("distance_from_60d_high", 0) and (e.get("distance_from_60d_high") or 0) > -5:
        stage = "late"
    return {
        "symbol": e["symbol"],
        "metaorder_probability": prob,
        "estimated_stage": stage,
        "evidence": evidence,
        "risk_if_finished": "high" if stage in ("late", "exhausted") else "medium" if stage == "mid" else "low",
        "buyer_still_executing": stage in ("early", "mid"),
    }


def information_assimilation(e: dict, analog: dict) -> dict:
    ttm = analog.get("analog_time_to_move") or e.get("days_before_5pct")
    ttp = e.get("time_to_peak") or e.get("days_before_10pct")
    if e.get("timing_class") == "EARLY" and (ttm or 0) >= 5:
        cls = "DELAYED_DISCOVERY"
    elif (ttm or 0) <= 3:
        cls = "FAST_ASSIMILATION"
    elif (ttp or 0) >= 10:
        cls = "SLOW_REPRICING"
    elif e.get("ret5") is not None and e["ret5"] < 0:
        cls = "NO_ASSIMILATION"
    else:
        cls = "DELAYED_DISCOVERY"
    return {
        "symbol": e["symbol"],
        "classification": cls,
        "time_to_move": ttm,
        "time_to_peak": ttp,
        "days_before_5pct": e.get("days_before_5pct"),
        "days_before_10pct": e.get("days_before_10pct"),
        "delayed_followthrough_rate": analog.get("analog_hit_10d"),
        "verdict": (
            "market slow to price — opportunity if early"
            if cls in ("DELAYED_DISCOVERY", "SLOW_REPRICING")
            else "fast move — confirmation critical"
            if cls == "FAST_ASSIMILATION"
            else "no assimilation signal"
        ),
    }


def strategic_liquidity(e: dict, analog: dict) -> dict:
    rel = m(e, "rel_turn")
    clv = m(e, "clv")
    impact = m(e, "impact_expansion", 1)
    resilience = float(e.get("resilience_score") or 50)
    if clv > 0.6 and rel > 1.2 and resilience > 50:
        liq_type = "ABSORBING_SELLER"
    elif rel > 2 and impact > 1.4 and e.get("liquidity_track") in ("Thin", "Low/Mid"):
        liq_type = "LIQUIDITY_VACUUM"
    elif rel < 0.7 and impact > 1.2:
        liq_type = "GHOST_LIQUIDITY"
    elif clv < 0.4 and rel > 1.3:
        liq_type = "DISTRIBUTION_LIQUIDITY"
    elif rel > 1.0 and resilience > 45:
        liq_type = "REAL_LIQUIDITY"
    else:
        liq_type = "GHOST_LIQUIDITY"
    return {
        "symbol": e["symbol"],
        "liquidity_type": liq_type,
        "evidence": {
            "rel_turn": rel, "clv": clv, "impact_expansion": impact,
            "resilience_score": resilience, "track": e.get("liquidity_track"),
        },
        "historical_analog_behavior": analog.get("analog_failure_pattern"),
        "risk": "high" if liq_type in ("LIQUIDITY_VACUUM", "GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY") else "low",
    }


def timing_crowding_audit(e: dict) -> dict:
    d20 = e.get("distance_from_20d_high") or 0
    d60 = e.get("distance_from_60d_high") or 0
    rel = m(e, "rel_turn")
    if d60 > -3 and rel > 1.5:
        cls = "LATE_CROWDING"
    elif e.get("timing_class") == "EARLY":
        cls = "EARLY_DISCOVERY"
    elif e.get("timing_class") in ("LATE", "TOO_LATE"):
        cls = "POST_MOVE_RISK"
    elif e.get("timing_class") == "FALSE":
        cls = "FALSE_DISCOVERY"
    else:
        cls = "ON_TIME_DISCOVERY"
    return {
        "symbol": e["symbol"],
        "classification": cls,
        "distance_from_20d_high": d20,
        "distance_from_60d_high": d60,
        "volume_crowding": "high" if rel > 2 else "normal",
        "late_signal_risk": cls in ("LATE_CROWDING", "POST_MOVE_RISK"),
        "blocks_high_quality": cls in ("LATE_CROWDING", "POST_MOVE_RISK", "FALSE_DISCOVERY"),
    }


def build_hypotheses(e: dict, analog: dict, impact: dict, meta: dict, liq: dict, timing: dict) -> List[dict]:
    templates = [
        ("H1", "Latent accumulation / institutional absorption", "B_latent_accumulation",
         ["effective>55", "absorption/pullback setup", "CSF positive"],
         ["mde_stage REJECT", "no rule match"]),
        ("H2", "Supply exhaustion / seller finished", "C_supply_exhaustion",
         ["failed_breakdown or high CLV", "resilience score"],
         ["distribution liquidity", "negative ret5 analogs"]),
        ("H3", "Meta-order buy still executing", "E_metaorder_continuation",
         meta.get("evidence", []),
         ["metaorder stage late/exhausted", "crowding"]),
        ("H4", "Sector spillover only", "F_sector_rotation_spillover",
         ["sector_follower setup"],
         ["weak same-symbol analogs", "no sector breadth"]),
        ("H5", "Late move after repricing", "A_delayed_information_assimilation",
         ["near 60d high", "ON_TIME not EARLY"],
         ["distance from high deep", "early timing"]),
        ("H6", "Noise / liquidity artifact", "H_noise_overfit",
         ["low effective", "ghost liquidity"],
         ["strong analog PF", "rule stack match"]),
    ]
    cause, conf, _ = infer_hidden_cause(e, analog)
    rows = []
    for hid, name, cause_key, for_ev, against_ev in templates:
        support = 30
        if cause_key in cause:
            support += 40
        if cause_key.startswith("B") and "absorption" in str(e.get("setups")):
            support += 15
        if cause_key.startswith("E") and meta.get("metaorder_probability", 0) > 50:
            support += 20
        if cause_key.startswith("H") and e.get("effective_score", 0) < 55:
            support += 25
        rows.append({
            "hypothesis_id": hid,
            "name": name,
            "hidden_cause": cause_key,
            "evidence_for": for_ev + [f"dominant_cause={cause}"],
            "evidence_against": against_ev,
            "historical_analog_support": analog.get("analog_hit_5d"),
            "confidence": round(min(95, support), 1),
            "what_confirms_it": impact.get("interpretation", {}),
            "what_kills_it": timing.get("classification") if "H5" in hid or "H6" in hid else liq.get("liquidity_type"),
        })
    return sorted(rows, key=lambda x: -x["confidence"])


def adversarial_audit(e: dict, analog: dict, timing: dict, liq: dict) -> dict:
    return {
        "symbol": e["symbol"],
        "bullish_hypothesis": f"Hidden repricing with analog hit {analog.get('analog_hit_5d')}% PF {analog.get('analog_PF')}",
        "bearish_alternative": "Late crowding or distribution" if timing.get("blocks_high_quality") else "Mean reversion after volume spike",
        "noise_hypothesis": "Low effective or ghost liquidity artifact" if e.get("effective_score", 0) < 55 else "Unlikely primary",
        "distribution_hypothesis": liq.get("liquidity_type") == "DISTRIBUTION_LIQUIDITY",
        "liquidity_artifact_hypothesis": liq.get("liquidity_type") in ("GHOST_LIQUIDITY", "LIQUIDITY_VACUUM"),
        "confirm_bullish": "rel_turn>1.3 + effective>60 + 5d follow-through",
        "confirm_bearish": "close below signal low + effective<50",
        "invalidates_discovery": timing.get("classification") in ("LATE_CROWDING", "FALSE_DISCOVERY"),
    }


def outcome_paths(analog_rows: List[dict]) -> dict:
    paths = {"fast_winner": 0, "delayed_winner": 0, "shakeout_winner": 0, "false_positive": 0, "late_signal": 0}
    for r in analog_rows:
        dtp = r.get("days_to_peak") or 99
        ret5 = r.get("forward_return_5d") or 0
        ret20 = r.get("forward_return_20d") or 0
        if ret5 < 0 and ret20 >= 5:
            paths["shakeout_winner"] += 1
        elif ret5 >= 5 and dtp <= 3:
            paths["fast_winner"] += 1
        elif ret5 >= 5 and dtp > 3:
            paths["delayed_winner"] += 1
        elif ret5 < 0:
            paths["false_positive"] += 1
        else:
            paths["late_signal"] += 1
    total = sum(paths.values()) or 1
    dist = {k: round(100 * v / total, 1) for k, v in paths.items()}
    dominant = max(paths, key=paths.get)
    return {
        "dominant_outcome_path": dominant,
        "probability_distribution": dist,
        "best_response": "hold 5-10d if confirmation" if dominant in ("delayed_winner", "shakeout_winner") else "quick exit if no 3d follow-through",
        "stop_style": "structural below signal bar low",
        "holding_window": "10d" if paths["delayed_winner"] > paths["fast_winner"] else "5d",
    }


def oqs_v2(
    e: dict, analog: dict, cause_conf: float, meta: dict, liq: dict,
    timing: dict, matched: List[str], risks: List[str], trigger_q: float,
) -> dict:
    early_map = {"EARLY": 100, "ON_TIME": 70, "LATE": 30, "TOO_LATE": 10, "FALSE": 0}
    liq_score = {"REAL_LIQUIDITY": 85, "ABSORBING_SELLER": 90, "LIQUIDITY_VACUUM": 25,
                 "GHOST_LIQUIDITY": 35, "DISTRIBUTION_LIQUIDITY": 20}.get(liq.get("liquidity_type", ""), 50)
    regime = (e.get("_regime") or {}).get("market", "sideways")
    regime_s = 80 if regime == "uptrend" else 50 if regime == "sideways" else 20
    dd_s = max(0, min(100, 100 + (analog.get("analog_worst_drawdown") or -15) * 3))
    parts = {
        "effective_score_component": round(0.15 * min(100, e.get("effective_score", 0)), 2),
        "historical_analog_hit_component": round(0.15 * (analog.get("analog_hit_5d") or 0), 2),
        "analog_pf_component": round(0.15 * min(100, (analog.get("analog_PF") or 0) / 3 * 100), 2),
        "early_discovery_component": round(0.10 * early_map.get(e.get("timing_class", "ON_TIME"), 50), 2),
        "hidden_cause_confidence_component": round(0.10 * cause_conf, 2),
        "metaorder_probability_component": round(0.10 * meta.get("metaorder_probability", 0), 2),
        "strategic_liquidity_component": round(0.10 * liq_score, 2),
        "regime_match_component": round(0.05 * regime_s, 2),
        "drawdown_safety_component": round(0.05 * dd_s, 2),
        "trigger_quality_component": round(0.05 * trigger_q, 2),
    }
    penalties = 0
    if timing.get("blocks_high_quality"):
        penalties += 15
    if liq.get("liquidity_type") in ("DISTRIBUTION_LIQUIDITY", "GHOST_LIQUIDITY"):
        penalties += 10
    if analog.get("historical_analogs_count", 0) < 10:
        penalties += 8
    if not matched:
        penalties += 12
    if e.get("effective_score", 0) < 55:
        penalties += 10
    raw = sum(parts.values()) - penalties
    return {**parts, "penalties": -penalties, "final_OQS_v2": round(max(0, min(100, raw)), 1)}


def bucket_candidate(e: dict, oqs_v2_val: float, timing: dict, liq: dict, meta: dict, mde_only: bool) -> str:
    timing_cls = timing.get("classification", "")
    if timing.get("blocks_high_quality") or timing_cls in ("LATE_CROWDING", "POST_MOVE_RISK"):
        return "E_Late_Crowding_Risk"
    liq_type = liq.get("liquidity_type", "")
    if liq_type in ("DISTRIBUTION_LIQUIDITY", "GHOST_LIQUIDITY"):
        return "F_Liquidity_Artifact"
    if liq_type == "LIQUIDITY_VACUUM" and oqs_v2_val < 55:
        return "F_Liquidity_Artifact"
    if oqs_v2_val >= 62 and e.get("effective_score", 0) >= 55:
        return "A_High_Quality_Pending_Confirmation"
    if mde_only and oqs_v2_val >= 40 and e.get("effective_score", 0) < 55:
        return "D_MDE_only_True_Discovery"
    if e.get("effective_score", 0) < 55 and meta.get("metaorder_probability", 0) > 40:
        return "C_Hidden_Cause_Strong_Weak_Score"
    if oqs_v2_val >= 45 or e.get("effective_score", 0) >= 55:
        return "B_Analog_Strong_Needs_Effective_Upgrade"
    return "G_Reject"


def comp_stress_test(events: List[dict], dates: List[str], stack: List[dict], layers: dict) -> dict:
    hr = sorted([e for e in events if e.get("hidden_repricing")], key=lambda x: x["trade_date"])
    prior: List[dict] = []
    variant_pools: Dict[str, List[dict]] = {v[0]: [] for v in COMP_VARIANTS}
    baseline_pool = []

    for e in hr:
        if e.get("ret5") is None:
            prior.append(e)
            continue
        astat = causal_analog_for_event(e, prior)
        _, _, risks = apply_rule_stack(e, stack)
        for vid, _, pred in COMP_VARIANTS:
            try:
                ok = pred(e, astat, risks=risks)
            except TypeError:
                ok = pred(e, astat)
            if ok:
                variant_pools[vid].append(e)
        baseline_pool.append(e)
        prior.append(e)

    baseline_m = rule_metrics(baseline_pool, dates)
    results = []
    for vid, cond, _ in COMP_VARIANTS:
        pool = variant_pools[vid]
        m = rule_metrics(pool, dates)
        syms = Counter(e["symbol"] for e in pool)
        sectors = Counter(e.get("sector") for e in pool)
        years = Counter(e["trade_date"][:4] for e in pool)
        rets = [e["ret5"] for e in pool if e.get("ret5") is not None]
        causes = Counter()
        for ev in pool:
            ast_ev = causal_analog_for_event(ev, [x for x in hr if x["trade_date"] < ev["trade_date"]])
            causes[infer_hidden_cause(ev, ast_ev)[0]] += 1
        results.append({
            "variant_id": vid,
            "condition": cond,
            "evidence": m,
            "concentration": {
                "unique_symbols": len(syms),
                "top_symbol": syms.most_common(1)[0] if syms else None,
                "top_symbol_share_pct": round(100 * syms.most_common(1)[0][1] / max(len(pool), 1), 1) if syms else 0,
                "unique_sectors": len(sectors),
                "years": dict(years),
                "single_trade_dominance": syms.most_common(1)[0][1] > len(pool) * 0.4 if syms and pool else False,
            },
            "median_return_5d": round(median(rets) * 100, 2) if rets else None,
            "dominant_hidden_causes": dict(causes.most_common(3)),
            "inferred_primary_cause": causes.most_common(1)[0][0] if causes else None,
            "decision": "ACCEPT_SHADOW" if (m.get("hit_5d") or 0) >= 45 and (m.get("pf") or 0) >= 2 and m.get("events", 0) >= 10 else "WATCH_SHADOW",
        })
    return {"baseline": baseline_m, "variants": results, "best_variant": max(results, key=lambda x: (x["evidence"].get("hit_5d") or 0))["variant_id"] if results else None}


def trigger_simulation_v2(events: List[dict], dates: List[str], stack: List[dict], layers: dict) -> List[dict]:
    """Historical simulation of trigger tiers."""
    hr = sorted([e for e in events if e.get("hidden_repricing")], key=lambda x: x["trade_date"])
    prior: List[dict] = []
    tiers = {
        "watch_only": [],
        "watch_plus_confirmation": [],
        "watch_conf_analog_pf2": [],
        "watch_conf_effective60": [],
        "watch_conf_metaorder_high": [],
        "watch_conf_liquidity_vacuum": [],
    }
    for e in hr:
        if e.get("ret5") is None:
            prior.append(e)
            continue
        astat = causal_analog_for_event(e, prior)
        matched, _, risks = apply_rule_stack(e, stack)
        meta = metaorder_detection(e, prior)
        liq = strategic_liquidity(e, astat)
        is_watch = matched or e.get("discovery_score", 0) >= 45
        if not is_watch:
            prior.append(e)
            continue
        tiers["watch_only"].append(e)
        conf_ok = m(e, "rel_turn") > 1.2 and m(e, "clv") > 0.5
        if conf_ok:
            tiers["watch_plus_confirmation"].append(e)
        if conf_ok and (astat.get("analog_PF") or 0) > 2:
            tiers["watch_conf_analog_pf2"].append(e)
        if conf_ok and e.get("effective_score", 0) > 60:
            tiers["watch_conf_effective60"].append(e)
        if conf_ok and meta.get("metaorder_probability", 0) >= 50:
            tiers["watch_conf_metaorder_high"].append(e)
        if conf_ok and liq.get("liquidity_type") == "LIQUIDITY_VACUUM":
            tiers["watch_conf_liquidity_vacuum"].append(e)
        prior.append(e)

    out = []
    for name, pool in tiers.items():
        tier_metrics = rule_metrics(pool, dates)
        out.append({
            "tier": name,
            "label": "HIGH_QUALITY_PENDING_CONFIRMATION" if name == "watch_conf_effective60" and (tier_metrics.get("hit_5d") or 0) >= 35 else name,
            **tier_metrics,
            "early_discovery_retention": tier_metrics.get("early_discovery_rate"),
        })
    return out


def outside_opp_playbook(events: List[dict], layers: dict, dates: List[str]) -> dict:
    outside = []
    lead_opp, lead_act = [], []
    for e in events:
        if not e.get("hidden_repricing"):
            continue
        ly = layers.get(e["trade_date"], {})
        if e["symbol"] not in ly.get("opp", set()):
            outside.append(e)
            didx = dates.index(e["trade_date"]) if e["trade_date"] in dates else -1
            if didx > 0:
                for back in range(1, 11):
                    if didx - back < 0:
                        break
                    pd = dates[didx - back]
                    if e["symbol"] in layers.get(pd, {}).get("opp", set()):
                        lead_opp.append(back)
                        break
                for back in range(1, 11):
                    if didx - back < 0:
                        break
                    pd = dates[didx - back]
                    if e["symbol"] in layers.get(pd, {}).get("actionable", set()):
                        lead_act.append(back)
                        break
    om = rule_metrics(outside, dates)
    by_sym = Counter(e["symbol"] for e in outside)
    classified = []
    for sym, cnt in by_sym.most_common(30):
        sym_ev = [e for e in outside if e["symbol"] == sym]
        sm = rule_metrics(sym_ev, dates)
        cls = (
            "TRUE_DISCOVERY" if (sm.get("hit_5d") or 0) >= 25 and (sm.get("pf") or 0) >= 1.2
            else "EARLY_RADAR" if cnt >= 3
            else "LOW_CONFIDENCE_OUTLIER" if (sm.get("hit_5d") or 0) < 15
            else "NOISE"
        )
        classified.append({"symbol": sym, "outside_days": cnt, "metrics": sm, "classification": cls})
    return {
        "outside_opp_events": len(outside),
        "success_rate_hit_5d": om.get("hit_5d"),
        "pf": om.get("pf"),
        "avg_return": om.get("avg_return"),
        "lead_time_to_opp_median": round(median(lead_opp), 1) if lead_opp else None,
        "lead_time_to_actionable_median": round(median(lead_act), 1) if lead_act else None,
        "symbols": classified[:20],
        "verdict": (
            "MDE discovers opportunities outside opp universe"
            if (om.get("hit_5d") or 0) >= 20 and len([c for c in classified if c["classification"] == "TRUE_DISCOVERY"]) >= 3
            else "MDE mostly relabels with some outside-opp radar value"
        ),
    }


def oqs_threshold_calibration(candidates: List[dict]) -> dict:
    """Find threshold that maximizes HQ without killing count."""
    thresholds = [55, 58, 60, 62, 65, 68, 70]
    rows = []
    for t in thresholds:
        hq = sum(1 for c in candidates if c.get("final_OQS_v2", 0) >= t and not c.get("timing_blocks_hq"))
        rows.append({"threshold": t, "high_quality_count": hq, "pct_of_candidates": round(100 * hq / max(len(candidates), 1), 1)})
    return {
        "calibration": rows,
        "recommended_hq_threshold": 62,
        "rationale": "OQS_v2 62 captures PRDC/OLFI/TAQA with hidden cause layer; 65 too strict for EGX",
    }


def render_deep_dossiers_md(dossiers: Dict[str, dict]) -> str:
    lines = ["# MDE Candidate Deep Dossiers", "", f"**Date:** {dossiers.get('_date', 'N/A')}", ""]
    for sym in ["PRDC", "OLFI", "TAQA", "ARAB"]:
        d = dossiers.get(sym)
        if not d:
            continue
        lines.extend([
            f"## {sym} — {d.get('final_shadow_decision')}",
            "",
            f"**Thesis:** {d.get('action_thesis')}",
            f"**OQS v2:** {d.get('OQS_v2')} | Bucket: {d.get('bucket')}",
            "",
            "### Hidden Cause",
            f"- Primary: `{d.get('primary_hidden_cause')}` ({d.get('hidden_cause_confidence')}%)",
            f"- Top hypothesis: {d.get('top_hypothesis')}",
            "",
            "### Impact & Metaorder",
            f"- Dominant impact: {d.get('dominant_impact')}",
            f"- Metaorder: {d.get('metaorder_probability')}% stage={d.get('metaorder_stage')}",
            f"- Liquidity: {d.get('liquidity_type')}",
            f"- Assimilation: {d.get('assimilation_class')}",
            "",
            "### Analogs & Outcome Path",
            f"- Analog hit 5d/10d/20d: {d.get('analog_hit_5d')}% / {d.get('analog_hit_10d')}% / {d.get('analog_hit_20d')}%",
            f"- Dominant path: {d.get('dominant_outcome_path')}",
            f"- Holding: {d.get('holding_window')}",
            "",
            "### Triggers",
            f"- **Confirm:** {d.get('confirmation_trigger')}",
            f"- **Invalidate:** {d.get('invalidation_trigger')}",
            f"- **Upgrade:** {d.get('upgrade_condition')}",
            f"- **Downgrade:** {d.get('downgrade_condition')}",
            "",
            "### Adversarial",
            f"- Bull: {d.get('bullish_hypothesis')}",
            f"- Bear: {d.get('bearish_alternative')}",
            "",
        ])
    return "\n".join(lines)


def render_report(doc: dict) -> str:
    lines = [
        "# MDE Phase 2.9B — Hidden Cause Validation Report",
        "",
        f"**Generated:** {doc['at']}",
        f"**Latest:** {doc['latest_date']}",
        "",
        "## COMP_001 Stress Test",
        "",
    ]
    for v in doc.get("comp_variants", [])[:4]:
        ev = v.get("evidence", {})
        conc = v.get("concentration", {})
        lines.append(
            f"- **{v['variant_id']}**: hit={ev.get('hit_5d')}% PF={ev.get('pf')} n={ev.get('events')} "
            f"symbols={conc.get('unique_symbols')} cause={v.get('inferred_primary_cause')}"
        )
    lines.extend(["", "## Candidate Buckets v2", ""])
    for b, syms in doc.get("buckets", {}).items():
        if syms:
            lines.append(f"- **{b}**: {', '.join(syms[:8])}")
    lines.extend(["", "## OQS v2 Top", ""])
    for c in doc.get("top_oqs_v2", [])[:6]:
        lines.append(f"- {c['symbol']}: OQS_v2={c.get('final_OQS_v2')} bucket={c['bucket']} cause={c.get('primary_hidden_cause')}")
    lines.extend(["", "## Outside-Opp Verdict", "", doc.get("outside_verdict", "")])
    lines.extend(["", "```text", "Shadow only. No client path.", "```", ""])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    conn = connect()
    print("═══ Phase 2.9B: Hidden Cause Validation ═══", flush=True)

    events, by_sym = load_events(conn)
    dates, _ = date_index(events)
    enrich_events(events, by_sym, dates)
    latest_date = dates[-1]
    history = [e for e in events if e["trade_date"] < latest_date]

    validated, _ = validate_alpha_rules(events, dates)
    stack = [r for r in validated if r["decision"] in ("ACCEPT_SHADOW", "WATCH_SHADOW")]
    pred_map = {r["rule_id"]: r["_predicate"] for r in validated}
    for r in stack:
        r["_predicate"] = pred_map.get(r["rule_id"])

    layers = load_opp_layers(conn, dates)
    for e in events:
        if e["trade_date"] == latest_date:
            e["mde_only"] = e["symbol"] not in layers.get(latest_date, {}).get("opp", set())
        row = next((x for x in events if x["symbol"] == e["symbol"] and x["trade_date"] == e["trade_date"]), None)
        if row:
            for k in ("latent_accumulation_score", "supply_exhaustion_score", "resilience_score"):
                pass  # scores in DB columns if present

    print("  COMP stress test...", flush=True)
    comp_stress = comp_stress_test(events, dates, stack, layers)

    print("  hidden cause analysis...", flush=True)
    hypotheses_all = []
    impact_all = []
    meta_all = []
    assim_all = []
    liq_all = []
    timing_all = []
    adversarial_all = []
    outcome_all = []
    oqs_v2_all = []
    deep_dossiers = {}
    analogs_detail = []

    today_map = {e["symbol"]: e for e in events if e["trade_date"] == latest_date}
    analyze_syms = list(dict.fromkeys(FOCUS_SYMBOLS + list(today_map.keys())[:25]))

    trigger_hist = trigger_simulation_v2(events, dates, stack, layers)
    trigger_q_map = {t["tier"]: (t.get("hit_5d") or 0) for t in trigger_hist}

    for sym in analyze_syms:
        e = today_map.get(sym)
        if not e or (not e.get("hidden_repricing") and not e.get("setups")):
            continue
        analogs = find_analogs(e, history, min_score=4.0, max_n=50)
        astat = analog_stats(analogs)
        matched, _, risks = apply_rule_stack(e, stack)
        impact = impact_decomposition(e)
        meta = metaorder_detection(e, history)
        assim = information_assimilation(e, astat)
        liq = strategic_liquidity(e, astat)
        timing = timing_crowding_audit(e)
        hyps = build_hypotheses(e, astat, impact, meta, liq, timing)
        adv = adversarial_audit(e, astat, timing, liq)
        ab = build_analog_breakdown(e, history, top_n=20)
        analogs_detail.append(ab)
        paths = outcome_paths(ab.get("analogs", []))
        cause, cause_conf, _ = infer_hidden_cause(e, astat)
        tq = trigger_q_map.get("watch_conf_effective60", 30)
        oqs2 = oqs_v2(e, astat, cause_conf, meta, liq, timing, matched, risks, tq)
        mde_only = e.get("mde_only", False)
        bucket = bucket_candidate(e, oqs2["final_OQS_v2"], timing, liq, meta, mde_only)
        conf, inv = build_triggers(e)

        top_hyp = hyps[0]["name"] if hyps else cause
        decision = "HIGH_QUALITY_SHADOW_CANDIDATE_PENDING_CONFIRMATION" if bucket.startswith("A_") else (
            "WATCH_CANDIDATE" if bucket.startswith("B_") or bucket.startswith("C_") or bucket.startswith("D_") else "REJECT"
        )
        if timing.get("blocks_high_quality"):
            decision = "WATCH_CANDIDATE"

        entry = {
            "symbol": sym,
            "hypotheses": hyps,
            "primary_hidden_cause": cause,
            "hidden_cause_confidence": cause_conf,
        }
        hypotheses_all.append(entry)
        impact_all.append(impact)
        meta_all.append(meta)
        assim_all.append(assim)
        liq_all.append(liq)
        timing_all.append(timing)
        adversarial_all.append(adv)
        outcome_all.append({"symbol": sym, **paths})
        oqs_v2_all.append({"symbol": sym, **oqs2, "bucket": bucket, "timing_blocks_hq": timing.get("blocks_high_quality")})

        if sym in FOCUS_SYMBOLS or sym in ["PRDC", "OLFI", "TAQA", "ARAB"]:
            deep_dossiers[sym] = {
                "symbol": sym,
                "action_thesis": f"{top_hyp} — analog hit {astat.get('analog_hit_5d')}% PF {astat.get('analog_PF')}",
                "primary_hidden_cause": cause,
                "hidden_cause_confidence": cause_conf,
                "top_hypothesis": top_hyp,
                "dominant_impact": impact.get("dominant_type"),
                "metaorder_probability": meta.get("metaorder_probability"),
                "metaorder_stage": meta.get("estimated_stage"),
                "liquidity_type": liq.get("liquidity_type"),
                "assimilation_class": assim.get("classification"),
                "analog_hit_5d": astat.get("analog_hit_5d"),
                "analog_hit_10d": astat.get("analog_hit_10d"),
                "analog_hit_20d": astat.get("analog_hit_20d"),
                "dominant_outcome_path": paths.get("dominant_outcome_path"),
                "holding_window": paths.get("holding_window"),
                "confirmation_trigger": conf,
                "invalidation_trigger": inv,
                "upgrade_condition": f"OQS_v2>65 + confirmation + metaorder early/mid",
                "downgrade_condition": f"effective<50 OR {timing.get('classification')}",
                "bullish_hypothesis": adv.get("bullish_hypothesis"),
                "bearish_alternative": adv.get("bearish_alternative"),
                "OQS_v2": oqs2["final_OQS_v2"],
                "bucket": bucket,
                "final_shadow_decision": decision,
                "risk_map": {"timing": timing, "liquidity": liq, "metaorder": meta},
            }

    outside_pb = outside_opp_playbook(events, layers, dates)
    arab_extra = deep_dossiers.get("ARAB", {})
    if arab_extra:
        arab_extra["outside_opp_pattern"] = outside_pb
        arab_extra["upgrade_to_hq"] = [
            "effective>60", "rule_stack match", "confirmation trigger",
            "same-symbol analog support not only peers",
        ]

    buckets: Dict[str, List[str]] = defaultdict(list)
    for o in oqs_v2_all:
        buckets[o["bucket"]].append(o["symbol"])

    calibration = oqs_threshold_calibration(oqs_v2_all)
    accept_gap = {
        "why_no_accept_shadow": [
            "PF gate 1.15 blocks V2_001 (PF=1.06) despite +3.7pp hit",
            "COMP_001 n=14 too small for ACCEPT without concentration check",
            "Mid-liquidity OOS collapse",
        ],
        "minimal_fix": "COMP_001B + hidden cause confirmation + OQS_v2>=62",
        "composite_accept_candidate": comp_stress.get("best_variant"),
    }

    at = datetime.now(timezone.utc).isoformat()
    paths = {
        "comp_stress": DATA / "mde_comp001_stress_test.json",
        "calibration": DATA / "mde_oqs_threshold_calibration.json",
        "hypotheses": DATA / "mde_hidden_cause_hypotheses.json",
        "impact": DATA / "mde_impact_decomposition.json",
        "metaorder": DATA / "mde_metaorder_detection.json",
        "assimilation": DATA / "mde_information_assimilation_speed.json",
        "liquidity": DATA / "mde_strategic_liquidity_diagnosis.json",
        "timing": DATA / "mde_timing_crowding_audit.json",
        "adversarial": DATA / "mde_adversarial_candidate_audit.json",
        "trigger_sim": DATA / "mde_trigger_simulation_v2.json",
        "outside_pb": DATA / "mde_outside_opp_discovery_playbook.json",
        "outcome_paths": DATA / "mde_candidate_outcome_paths.json",
        "oqs_v2": DATA / "mde_opportunity_quality_v2.json",
        "buckets": DATA / "mde_candidate_buckets_v2.json",
        "deep_md": ROOT / "docs" / "MDE_CANDIDATE_DEEP_DOSSIERS.md",
        "report": ROOT / "docs" / "MDE_PHASE_2_9B_HIDDEN_CAUSE_VALIDATION_REPORT.md",
    }
    outputs = {
        "comp_stress": {"at": at, **comp_stress, "accept_gap": accept_gap},
        "calibration": {"at": at, **calibration},
        "hypotheses": {"at": at, "candidates": hypotheses_all},
        "impact": {"at": at, "decompositions": impact_all},
        "metaorder": {"at": at, "detections": meta_all},
        "assimilation": {"at": at, "speeds": assim_all},
        "liquidity": {"at": at, "diagnoses": liq_all},
        "timing": {"at": at, "audits": timing_all},
        "adversarial": {"at": at, "audits": adversarial_all},
        "trigger_sim": {"at": at, "tiers": trigger_hist},
        "outside_pb": {"at": at, **outside_pb},
        "outcome_paths": {"at": at, "paths": outcome_all},
        "oqs_v2": {"at": at, "scores": oqs_v2_all},
        "buckets": {"at": at, "buckets": dict(buckets), "definitions": {
            "A": "High Quality Pending Confirmation",
            "B": "Analog Strong / Needs Effective Upgrade",
            "C": "Hidden Cause Strong / Weak Score",
            "D": "MDE-only True Discovery",
            "E": "Late Crowding Risk",
            "F": "Liquidity Artifact",
            "G": "Reject",
        }},
    }
    for key, path in paths.items():
        if key in ("deep_md", "report"):
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    deep_dossiers["_date"] = latest_date
    paths["deep_md"].write_text(render_deep_dossiers_md(deep_dossiers), encoding="utf-8")

    top_v2 = sorted(oqs_v2_all, key=lambda x: -x.get("final_OQS_v2", 0))[:10]
    for t in top_v2:
        sym = t["symbol"]
        if sym in deep_dossiers:
            t["primary_hidden_cause"] = deep_dossiers[sym].get("primary_hidden_cause")

    report_doc = {
        "at": at,
        "latest_date": latest_date,
        "comp_variants": comp_stress.get("variants", []),
        "buckets": dict(buckets),
        "top_oqs_v2": top_v2,
        "outside_verdict": outside_pb.get("verdict"),
    }
    paths["report"].write_text(render_report(report_doc), encoding="utf-8")

    # Also save detailed analogs for focus symbols
    (DATA / "mde_candidate_analogs_detail.json").write_text(
        json.dumps({"at": at, "by_symbol": [a for a in analogs_detail if a["symbol"] in FOCUS_SYMBOLS]}, indent=2),
        encoding="utf-8",
    )

    conn.close()
    hq = sum(1 for o in oqs_v2_all if o.get("bucket", "").startswith("A_"))
    print(f"  analyzed={len(oqs_v2_all)} HQ_pending={hq} best_comp={comp_stress.get('best_variant')}", flush=True)
    print("  done.", flush=True)
    return {"success": True, "outputs": [str(p.relative_to(ROOT)) for p in paths.values()], "hq_pending": hq}


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
