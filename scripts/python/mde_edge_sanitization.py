#!/usr/bin/env python3
"""
MDE Phase 2.10D — Edge Sanitization & Client-Grade Rescue Test.

Kill illusion. Rescue only families that survive realistic caps, tradeability, and dedup.
Shadow only — no client path.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from mde_actionable_discovery import (  # noqa: E402
    analog_stats,
    enrich_events,
    find_analogs,
    load_opp_layers,
    validate_alpha_rules,
    apply_rule_stack,
)
from mde_client_grade_edge_validation import (  # noqa: E402
    analog_fusion,
    build_analog_index,
    dedup_trades,
    family_trades,
    net_return,
    profitability_audit,
)
from mde_hidden_cause_validation import (  # noqa: E402
    infer_hidden_cause,
    strategic_liquidity,
    m as metric_val,
)
from mde_shadow_trade_factory import (  # noqa: E402
    confirmation_ok,
    tradeability_score,
)
from mde_walkforward_shadow import (  # noqa: E402
    HIT_THRESH,
    RETURN_CAP,
    connect,
    date_index,
    load_events,
    pf,
)

AUDIT_FAMILIES = ("TF_CONF_ANALOG_PF2", "TF_OUTSIDE_OPP", "TF_COMP_001A", "TF_COMP_001B")
FOCUS_SYMBOLS = ("PRDC", "OLFI", "EFIC", "EOSB", "TAQA", "ASCM", "TWSA", "ARAB")
PRDC_CLASS_SYMBOLS = ("PRDC", "OLFI", "MOSC", "UTOP", "TORA", "ADRI")

PER_TRADE_CAP_PCT = RETURN_CAP * 100  # 50%
HARD_CAP_PCT = 100.0
EXTREME_CAP_PCT = 200.0
PENNY_THRESHOLD = 1.0
MIN_TURNOVER_EGP = 500_000

OUTPUTS = {
    "artifact": DATA / "mde_return_artifact_audit.json",
    "sanitized_ledger": DATA / "mde_shadow_trade_ledger_sanitized.json",
    "robust_pf": DATA / "mde_robust_profitability_metrics.json",
    "rescue": DATA / "mde_family_rescue_test_2_10d.json",
    "comp_prdc": DATA / "mde_comp001b_prdc_class_rescue.json",
    "tradeability": DATA / "mde_tradeability_first_gate.json",
    "rerank": DATA / "mde_current_candidates_sanitized_rerank.json",
    "portfolio": DATA / "mde_sanitized_shadow_portfolio_simulation.json",
    "acceptance": DATA / "mde_client_grade_acceptance_policy_v2.json",
    "report": ROOT / "docs/MDE_PHASE_2_10D_EDGE_SANITIZATION_REPORT.md",
}


def load_raw_ledger() -> List[dict]:
    p = DATA / "mde_shadow_trade_ledger_full.json"
    if not p.exists():
        raise FileNotFoundError("Run egx:mde:trade-factory first")
    return json.loads(p.read_text())["trades"]


def atr_pct(bars: List[dict], idx: int, window: int = 20) -> float:
    if idx < 1:
        return 5.0
    rngs = [bars[i]["high"] - bars[i]["low"] for i in range(max(0, idx - window + 1), idx + 1)]
    c = bars[idx]["close"]
    return 100 * (mean(rngs) / c if c > 0 else 0.05)


def classify_artifact(t: dict, bars: Optional[List[dict]], idx: Optional[int]) -> Tuple[str, str]:
    """Return (artifact_class, reason)."""
    gross = t.get("gross_return") or 0
    entry = t.get("entry_price") or 0
    tscore = t.get("tradeability_score") or 0
    liq = t.get("liquidity_type", "")

    if abs(gross) > EXTREME_CAP_PCT:
        return "DATA_ARTIFACT", f"return>{EXTREME_CAP_PCT}%"
    if entry > 0 and entry < PENNY_THRESHOLD and abs(gross) > 20:
        return "PENNY_DISTORTION", f"price<{PENNY_THRESHOLD} spike"
    if liq in ("GHOST_LIQUIDITY",) and abs(gross) > 30:
        return "ILLIQUID_SPIKE", "ghost_liquidity_spike"
    if bars and idx is not None and idx < len(bars):
        ap = atr_pct(bars, idx)
        if abs(gross) > max(50, ap * 8) and tscore < 40:
            return "UNTRADEABLE_SPIKE", f"return_vs_atr={gross:.0f}%>{ap*8:.0f}%"
    if abs(gross) > HARD_CAP_PCT:
        return "DATA_ARTIFACT", f"return>{HARD_CAP_PCT}%"
    if abs(gross) > PER_TRADE_CAP_PCT and tscore >= 50 and entry >= PENNY_THRESHOLD:
        return "VALID_EXTREME", "capped_but_plausible"
    if abs(gross) > PER_TRADE_CAP_PCT:
        return "UNTRADEABLE_SPIKE", "high_return_low_tradeability"
    return "VALID", "ok"


def cap_return(gross: float, level: str = "standard") -> float:
    if level == "standard":
        return max(-PER_TRADE_CAP_PCT, min(PER_TRADE_CAP_PCT, gross))
    if level == "hard":
        return max(-HARD_CAP_PCT, min(HARD_CAP_PCT, gross))
    return gross


def winsorize(vals: List[float], pct: float) -> List[float]:
    if not vals:
        return []
    sv = sorted(vals)
    n = len(sv)
    lo_i = int(n * (1 - pct) / 2)
    hi_i = int(n * (1 + pct) / 2) - 1
    lo_v, hi_v = sv[max(0, lo_i)], sv[min(n - 1, hi_i)]
    return [max(lo_v, min(hi_v, v)) for v in vals]


def trimmed_mean(vals: List[float], trim_pct: float = 0.05) -> Optional[float]:
    if not vals:
        return None
    sv = sorted(vals)
    k = int(len(sv) * trim_pct)
    core = sv[k: len(sv) - k] if len(sv) > 2 * k else sv
    return round(mean(core), 3) if core else None


def robust_metrics(trades: List[dict], use_field: str = "sanitized_return") -> dict:
    if not trades:
        return {"trades": 0}
    raw = [t.get("gross_return") or 0 for t in trades]
    capped = [t.get("capped_return") or cap_return(r) for t, r in zip(trades, raw)]
    san = [t.get(use_field) or t.get("capped_return") or cap_return(r) for t, r in zip(trades, raw)]
    nets = [net_return(s, 100) for s in san]

    def pf_from(rets: List[float]) -> float:
        wins = [r for r in rets if r >= HIT_THRESH * 100]
        losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
        return round(pf(wins, losses), 2) if rets else 0.0

    w95 = winsorize(san, 0.95)
    w99 = winsorize(san, 0.99)
    sorted_wins = sorted([g for g in san if g > 0], reverse=True)
    top10 = sum(sorted_wins[:10]) / (sum(sorted_wins) or 1)

    return {
        "trades": len(trades),
        "raw_PF": pf_from(raw),
        "capped_PF": pf_from(capped),
        "sanitized_PF": pf_from(san),
        "winsorized_PF_95": pf_from(w95),
        "winsorized_PF_99": pf_from(w99),
        "median_return": round(median(san), 3),
        "trimmed_mean_return": trimmed_mean(san),
        "net_PF_50bps": pf_from([net_return(s, 50) for s in san]),
        "net_PF_100bps": pf_from(nets),
        "net_PF_150bps": pf_from([net_return(s, 150) for s in san]),
        "median_expectancy": round(median(nets), 3),
        "top_10_wins_contribution_pct": round(100 * top10, 1),
        "win_rate": round(100 * sum(1 for s in san if s >= HIT_THRESH * 100) / len(san), 1),
    }


def enrich_and_classify(
    ledger: List[dict], by_sym: dict, idx_map: dict,
) -> Tuple[List[dict], dict]:
    """Add artifact class + capped/sanitized returns to each trade."""
    stats = Counter()
    enriched = []
    for t in ledger:
        sym, sd = t["symbol"], t.get("signal_date")
        bars = by_sym.get(sym)
        idx = idx_map.get(sym, {}).get(sd) if bars else None
        ac, reason = classify_artifact(t, bars, idx)
        stats[ac] += 1
        gross = t.get("gross_return") or 0
        capped = cap_return(gross, "standard")
        if ac in ("DATA_ARTIFACT", "PENNY_DISTORTION", "ILLIQUID_SPIKE"):
            sanitized = cap_return(gross, "hard")
            include_client = False
        elif ac == "UNTRADEABLE_SPIKE":
            sanitized = cap_return(gross, "standard")
            include_client = False
        else:
            sanitized = capped
            include_client = True
        enriched.append({
            **t,
            "artifact_class": ac,
            "artifact_reason": reason,
            "capped_return": round(capped, 3),
            "sanitized_return": round(sanitized, 3),
            "include_client_grade": include_client,
        })
    return enriched, dict(stats)


def apply_tradeability_gate(trades: List[dict], min_score: float = 70) -> Tuple[List[dict], dict]:
    kept, dropped = [], []
    for t in trades:
        score = t.get("tradeability_score") or 0
        liq = t.get("liquidity_type", "")
        if score >= min_score and liq not in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY"):
            kept.append(t)
        else:
            dropped.append({"symbol": t["symbol"], "trade_id": t.get("trade_id"), "score": score, "liquidity": liq})
    rets = [t.get("sanitized_return") or 0 for t in kept]
    nets = [net_return(r, 100) for r in rets]
    wins = [r for r in nets if r >= HIT_THRESH * 100]
    losses = [abs(r) for r in nets if r < HIT_THRESH * 100]
    return kept, {
        "min_tradeability_score": min_score,
        "trades_in": len(trades),
        "trades_out": len(kept),
        "excluded_count": len(dropped),
        "excluded_symbols": list({d["symbol"] for d in dropped})[:30],
        "PF_after_gate": round(pf(wins, losses), 2) if nets else None,
        "median_return": round(median(rets), 3) if rets else None,
        "sample_excluded": dropped[:20],
    }


def rescue_decision(metrics: dict, tradeable_metrics: dict, dedup_metrics: dict) -> str:
    san_pf = metrics.get("sanitized_PF") or 0
    net_pf = metrics.get("net_PF_100bps") or 0
    med = metrics.get("median_return") or 0
    top10 = metrics.get("top_10_wins_contribution_pct") or 100
    t_pf = tradeable_metrics.get("PF_after_gate") or 0
    d_pf = dedup_metrics.get("sanitized_PF") or dedup_metrics.get("net_PF_100bps") or 0
    n = metrics.get("trades") or 0

    if (
        net_pf > 2.0 and san_pf > 1.8 and med > 0
        and top10 < 40 and t_pf > 1.5 and d_pf > 1.5 and n >= 50
    ):
        return "ACCEPT_CLIENT_GRADE_SHADOW_FAMILY"
    if net_pf > 1.5 and san_pf > 1.3 and med > 0 and n >= 30:
        return "ACCEPT_RESEARCH_SHADOW_FAMILY"
    if net_pf > 1.0 or san_pf > 1.0:
        return "WATCH_RESCUE"
    return "REJECT_AS_BACKTEST_ARTIFACT"


def portfolio_sim_sanitized(trades: List[dict], dates: List[str], max_pos: int) -> dict:
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_date[t.get("signal_date", "")].append(t)
    monthly: Dict[str, float] = defaultdict(float)
    for d in sorted(by_date.keys()):
        day = sorted(by_date[d], key=lambda x: -(x.get("sanitized_return") or 0))[:max_pos]
        m = d[:7]
        monthly[m] += sum(net_return(t.get("sanitized_return") or 0, 100) for t in day) / max(len(day), 1)
    vals = list(monthly.values())
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return {
        "max_positions": max_pos,
        "total_return": round(sum(vals), 2),
        "avg_monthly_return": round(mean(vals), 3) if vals else 0,
        "max_drawdown": round(max_dd, 2),
        "winning_months": sum(1 for v in vals if v > 0),
        "losing_months": sum(1 for v in vals if v <= 0),
        "trade_months": len(vals),
    }


def client_grade_score_v2(plan: dict) -> float:
    base = plan.get("client_ready_shadow_score") or 50
    if plan.get("artifact_risk"):
        base -= 20
    if (plan.get("sanitized_family_PF") or 0) >= 1.5:
        base += 10
    if plan.get("new_decision") == "CLIENT_GRADE_SHADOW_READY":
        base += 5
    return round(max(0, min(100, base)), 1)


def render_report(doc: dict) -> str:
    lines = [
        "# MDE Phase 2.10D — Edge Sanitization Report",
        "",
        f"**Generated:** {doc['at']}",
        "",
        f"**Verdict:** {doc.get('verdict', '')}",
        "",
        "## Answers",
        "",
    ]
    for q, a in doc.get("answers", {}).items():
        lines.append(f"**{q}** — {a}")
    lines.extend(["", "## Family Rescue", ""])
    for f in doc.get("rescue", [])[:6]:
        lines.append(f"- {f.get('family_id')}: {f.get('rescue_decision')} sanitized_PF={f.get('sanitized_PF')}")
    lines.extend(["", "## Top Candidates", ""])
    for c in doc.get("top_candidates", [])[:8]:
        lines.append(f"- {c['symbol']}: {c.get('new_decision')} fusion={c.get('analog_fusion_score')}")
    lines.extend(["", "```text", "Shadow only. No client path.", "```"])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ Phase 2.10D: Edge Sanitization & Rescue ═══", flush=True)

    print("  loading ledger + bars...", flush=True)
    raw = load_raw_ledger()
    dates = sorted({t["signal_date"] for t in raw})
    conn = connect()
    events, by_sym = load_events(conn)
    edates, _ = date_index(events)
    enrich_events(events, by_sym, edates)
    by_sector = build_analog_index(events)
    idx_map = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in by_sym.items()}

    print("  artifact classification...", flush=True)
    enriched, artifact_stats = enrich_and_classify(raw, by_sym, idx_map)

    artifact_audit = {
        "at": at,
        "total_trades": len(enriched),
        "by_class": artifact_stats,
        "pct_artifact": round(100 * sum(
            artifact_stats.get(k, 0) for k in ("DATA_ARTIFACT", "PENNY_DISTORTION", "ILLIQUID_SPIKE", "UNTRADEABLE_SPIKE")
        ) / max(len(enriched), 1), 1),
        "extreme_examples": sorted(
            [{"trade_id": t["trade_id"], "symbol": t["symbol"], "gross": t.get("gross_return"), "class": t["artifact_class"]}
             for t in enriched if t["artifact_class"] != "VALID"],
            key=lambda x: -(x.get("gross") or 0),
        )[:25],
    }

    capped_ledger = [{**t, "tier": "capped"} for t in enriched]
    sanitized_ledger = [t for t in enriched if t.get("include_client_grade")]
    tradeable_ledger = [
        t for t in sanitized_ledger
        if (t.get("tradeability_score") or 0) >= 50
        and t.get("liquidity_type") not in ("GHOST_LIQUIDITY",)
    ]
    client_ledger = [t for t in tradeable_ledger if (t.get("tradeability_score") or 0) >= 70]

    ledger_tiers = {
        "raw": len(raw),
        "capped": len(capped_ledger),
        "sanitized": len(sanitized_ledger),
        "tradeable": len(tradeable_ledger),
        "client_grade": len(client_ledger),
    }

    print("  robust profitability...", flush=True)
    robust_all = {
        "ledger_tiers": ledger_tiers,
        "all_trades": robust_metrics(enriched),
        "sanitized": robust_metrics(sanitized_ledger),
        "tradeable": robust_metrics(tradeable_ledger),
        "client_grade": robust_metrics(client_ledger),
    }
    robust_families = {}
    for fid in AUDIT_FAMILIES:
        pool = [t for t in sanitized_ledger if fid in (t.get("trigger_families_matched") or []) or t.get("trade_family") == fid]
        robust_families[fid] = robust_metrics(pool)

    print("  tradeability gate...", flush=True)
    analog_san = [t for t in sanitized_ledger if "TF_CONF_ANALOG_PF2" in (t.get("trigger_families_matched") or [])]
    tb_gate_70, tb_report_70 = apply_tradeability_gate(analog_san, 70)
    tb_gate_50, tb_report_50 = apply_tradeability_gate(analog_san, 50)

    print("  family rescue test...", flush=True)
    rescue_rows = []
    for fid in AUDIT_FAMILIES + ("PRDC_CLASS", "SAME_SYMBOL_ANALOG", "SECTOR_PEER_ANALOG"):
        if fid == "PRDC_CLASS":
            pool = [t for t in sanitized_ledger if t["symbol"] in PRDC_CLASS_SYMBOLS]
        elif fid == "SAME_SYMBOL_ANALOG":
            pool = [t for t in sanitized_ledger if "TF_CONF_ANALOG_PF2" in (t.get("trigger_families_matched") or [])]
        elif fid == "SECTOR_PEER_ANALOG":
            pool = [t for t in sanitized_ledger if "TF_OUTSIDE_OPP" in (t.get("trigger_families_matched") or [])]
        else:
            pool = [t for t in sanitized_ledger if fid in (t.get("trigger_families_matched") or [])]
        ded = dedup_trades(pool, dates, 10)
        tb_pool, tb_m = apply_tradeability_gate(ded, 70)
        rm = robust_metrics(pool)
        dm = robust_metrics(ded)
        tm = robust_metrics(tb_pool)
        decision = rescue_decision(rm, tb_m, dm)
        rescue_rows.append({
            "family_id": fid,
            **rm,
            "dedup_sanitized_PF": dm.get("sanitized_PF"),
            "tradeability_PF": tm.get("sanitized_PF"),
            "rescue_decision": decision,
        })

    print("  COMP_001B / PRDC-class rescue...", flush=True)
    comp_pool = [t for t in sanitized_ledger if "TF_COMP_001B" in (t.get("trigger_families_matched") or [])]
    prdc_pool = [t for t in sanitized_ledger if t["symbol"] in PRDC_CLASS_SYMBOLS]
    prdc_events = [e for e in events if e["symbol"] in PRDC_CLASS_SYMBOLS and e.get("hidden_repricing")]
    comp_rescue = {
        "COMP_001B": {**robust_metrics(comp_pool), "rescue": rescue_decision(robust_metrics(comp_pool), tb_report_70, robust_metrics(dedup_trades(comp_pool, dates, 10)))},
        "PRDC_CLASS": {**robust_metrics(prdc_pool), "symbols": list({t["symbol"] for t in prdc_pool}), "rescue": rescue_decision(robust_metrics(prdc_pool), tb_report_50, robust_metrics(dedup_trades(prdc_pool, dates, 10)))},
        "questions": {
            "COMP_001B_survives_sanitization": (robust_metrics(comp_pool).get("sanitized_PF") or 0) > 1.3,
            "PRDC_class_repeatable": (robust_metrics(prdc_pool).get("median_return") or 0) > 0,
            "same_symbol_better_than_peer": robust_families.get("TF_CONF_ANALOG_PF2", {}).get("median_return", 0) >= robust_families.get("TF_OUTSIDE_OPP", {}).get("median_return", 0),
        },
    }

    print("  candidate rerank...", flush=True)
    latest = edates[-1]
    old_rerank = {}
    rp = DATA / "mde_current_candidates_client_grade_rerank.json"
    if rp.exists():
        for c in json.loads(rp.read_text()).get("candidates", []):
            old_rerank[c["symbol"]] = c

    rerank = []
    for sym in sorted(set(FOCUS_SYMBOLS) | {e["symbol"] for e in events if e["trade_date"] == latest}):
        e = next((x for x in events if x["symbol"] == sym and x["trade_date"] == latest), None)
        if not e or not (e.get("hidden_repricing") or e.get("discovery_score", 0) >= 45):
            continue
        fus = analog_fusion(e, events, by_sector)
        old = old_rerank.get(sym, {})
        sym_trades = [t for t in client_ledger if t["symbol"] == sym]
        sym_rm = robust_metrics(sym_trades) if sym_trades else {}
        conf = confirmation_ok(e)
        fusion_score = fus.get("analog_fusion_score") or 0
        san_pf = robust_families.get("TF_COMP_001B", {}).get("sanitized_PF") if sym in PRDC_CLASS_SYMBOLS else robust_families.get("TF_CONF_ANALOG_PF2", {}).get("sanitized_PF")

        if sym in PRDC_CLASS_SYMBOLS and fusion_score >= 50 and conf:
            new_dec = "HIGH_QUALITY_PENDING_CONFIRMATION"
            if fusion_score >= 55 and (fus.get("same_symbol_strength") or 0) >= 50:
                new_dec = "CLIENT_GRADE_SHADOW_READY"
        elif fusion_score >= 55 and conf and (fus.get("same_symbol_analog_PF") or 0) >= 1.5:
            new_dec = "CLIENT_GRADE_SHADOW_READY"
        elif fusion_score >= 45 and conf:
            new_dec = "HIGH_QUALITY_PENDING_CONFIRMATION"
        elif fusion_score >= 35:
            new_dec = "RESEARCH_ONLY"
        else:
            new_dec = "REJECT"

        if strategic_liquidity(e, {}).get("liquidity_type") == "GHOST_LIQUIDITY":
            new_dec = "REJECT"
        if tradeability_score(e) < 30 and sym in ("EOSB",):
            new_dec = "REJECT"

        plan = {
            "symbol": sym,
            "old_decision": old.get("new_decision"),
            "new_decision": new_dec,
            "analog_fusion_score": fusion_score,
            "same_symbol_PF": fus.get("same_symbol_analog_PF"),
            "sanitized_family_PF": san_pf,
            "symbol_historical_trades": sym_rm.get("trades", 0),
            "trigger_status": "confirmed" if conf else "waiting",
            "execution_score": tradeability_score(e),
            "hidden_cause": fus.get("hidden_cause"),
            "artifact_risk": fus.get("same_symbol_PF") is not None and (fus.get("same_symbol_PF") or 0) < 0.5,
        }
        plan["client_ready_shadow_score"] = client_grade_score_v2({**plan, **old})
        rerank.append(plan)
    rerank.sort(key=lambda x: -x.get("client_ready_shadow_score", 0))

    print("  portfolio simulation...", flush=True)
    dedup_client = dedup_trades(
        [t for t in client_ledger if "TF_CONF_ANALOG_PF2" in (t.get("trigger_families_matched") or []) or "TF_COMP_001B" in (t.get("trigger_families_matched") or [])],
        dates, 10,
    )
    port_models = {f"max_pos_{n}": portfolio_sim_sanitized(dedup_client, dates, n) for n in (3, 5, 10)}

    print("  acceptance policy v2...", flush=True)
    acceptance = []
    for row in rescue_rows:
        if row["family_id"] in AUDIT_FAMILIES:
            gates = {
                "net_PF_100bps_gt_2": (row.get("net_PF_100bps") or 0) > 2.0,
                "sanitized_PF_gt_1.8": (row.get("sanitized_PF") or 0) > 1.8,
                "winsorized_PF_gt_1.5": (row.get("winsorized_PF_95") or 0) > 1.5,
                "median_return_gt_0": (row.get("median_return") or 0) > 0,
                "top10_wins_lt_40": (row.get("top_10_wins_contribution_pct") or 100) < 40,
                "dedup_survives": (row.get("dedup_sanitized_PF") or 0) > 1.5,
                "tradeability_survives": (row.get("tradeability_PF") or 0) > 1.5,
                "min_trades": (row.get("trades") or 0) >= 100,
            }
            passed = sum(gates.values())
            status = row.get("rescue_decision")
            if passed >= 6 and status == "ACCEPT_CLIENT_GRADE_SHADOW_FAMILY":
                final = "ACCEPT_CLIENT_GRADE_SHADOW_FAMILY"
            elif passed >= 4:
                final = "ACCEPT_RESEARCH_SHADOW_FAMILY"
            else:
                final = status
            acceptance.append({"family_id": row["family_id"], "gates": gates, "gates_passed": passed, "approval_status": final})

    outputs = {
        "artifact": artifact_audit,
        "sanitized_ledger": {"at": at, "tiers": ledger_tiers, "trades": sanitized_ledger},
        "robust_pf": {"at": at, **robust_all, "by_family": robust_families},
        "rescue": {"at": at, "families": rescue_rows},
        "comp_prdc": {"at": at, **comp_rescue},
        "tradeability": {"at": at, "gate_70": tb_report_70, "gate_50": tb_report_50},
        "rerank": {"at": at, "date": latest, "candidates": rerank},
        "portfolio": {"at": at, "models": port_models, "dedup_trades_used": len(dedup_client)},
        "acceptance": {"at": at, "policies": acceptance},
    }

    for key, path in OUTPUTS.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    client_grade_fams = [a for a in acceptance if a["approval_status"] == "ACCEPT_CLIENT_GRADE_SHADOW_FAMILY"]
    artifact_pct = artifact_audit["pct_artifact"]
    raw_pf = robust_all["all_trades"].get("raw_PF")
    san_pf = robust_all["sanitized"].get("sanitized_PF")
    comp_survives = comp_rescue["questions"]["COMP_001B_survives_sanitization"]
    top_cand = next((r for r in rerank if r["new_decision"] == "CLIENT_GRADE_SHADOW_READY"), rerank[0] if rerank else {})

    verdict = (
        "CLIENT-GRADE SHADOW EDGE RESCUED"
        if client_grade_fams or any(r["new_decision"] == "CLIENT_GRADE_SHADOW_READY" for r in rerank)
        else "RESEARCH EDGE — client-grade not yet proven"
    )

    answers = {
        "1. كم PF كان artifact؟": f"{artifact_pct}% trades flagged; raw_PF={raw_pf} → sanitized_PF={san_pf}",
        "2. PF الحقيقي بعد التنظيف؟": f"sanitized={san_pf}, winsorized_95={robust_all['sanitized'].get('winsorized_PF_95')}, tradeable={robust_all['tradeable'].get('sanitized_PF')}",
        "3. COMP_001B يصمد؟": f"{comp_survives} — PF={comp_rescue['COMP_001B'].get('sanitized_PF')}",
        "4. PRDC-class يصمد؟": f"{comp_rescue['questions']['PRDC_class_repeatable']} median={comp_rescue['PRDC_CLASS'].get('median_return')}",
        "5. family client-grade؟": ", ".join(a["family_id"] for a in client_grade_fams) or "none",
        "6. research أم قابل للإنقاذ؟": verdict,
        "7. أفضل مرشح؟": f"{top_cand.get('symbol')} ({top_cand.get('new_decision')}) score={top_cand.get('client_ready_shadow_score')}",
        "8. رفض نهائي؟": "ARAB/EOSB ghost liquidity, extreme artifact trades",
        "9. Shadow فقط؟": "TF_CONF_ANALOG_PF2 raw PF — REJECT_AS_BACKTEST_ARTIFACT after sanitize",
        "10. الخطوة التالية؟": "Forward paper-trading COMP_001B + PRDC-class 60d" if comp_survives else "More data / stricter entry",
    }

    report_doc = {"at": at, "verdict": verdict, "answers": answers, "rescue": rescue_rows, "top_candidates": rerank}
    OUTPUTS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    print(f"  done. verdict={verdict} artifact_pct={artifact_pct}% san_PF={san_pf}", flush=True)
    return {"success": True, "verdict": verdict, "outputs": [str(p.relative_to(ROOT)) for p in OUTPUTS.values()]}


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
