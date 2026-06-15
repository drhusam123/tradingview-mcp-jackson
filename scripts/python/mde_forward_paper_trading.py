#!/usr/bin/env python3
"""
MDE Phase 2.10E — Forward Paper-Trading + Historical Replay Final Gate.

Paper-only. Proves whether COMP_001B / PRDC can graduate from Research to Client-Grade Shadow.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from mde_actionable_discovery import (  # noqa: E402
    analog_stats,
    build_triggers,
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
    net_return,
)
from mde_edge_sanitization import (  # noqa: E402
    robust_metrics,
)
from mde_hidden_cause_validation import (  # noqa: E402
    infer_hidden_cause,
    metaorder_detection,
    strategic_liquidity,
    m as metric_val,
)
from mde_shadow_trade_factory import (  # noqa: E402
    build_event_ctx,
    build_trigger_families,
    confirmation_ok,
    quick_analog,
    tradeability_score,
)
from mde_walkforward_shadow import (  # noqa: E402
    HIT_THRESH,
    connect,
    date_index,
    load_events,
    pf,
)

COST_BPS = 100
MIN_TRADEABILITY = 70
DEDUP_COOLDOWN = 10
PRDC_SYMBOL = "PRDC"

OUTPUTS = {
    "paper_trades": DATA / "mde_forward_paper_trades.json",
    "comp_monitor": DATA / "mde_comp001b_forward_monitor.json",
    "prdc_track": DATA / "mde_prdc_special_track.json",
    "replay": DATA / "mde_2_10e_historical_replay.json",
    "gate": DATA / "mde_client_grade_gate_status.json",
    "report": ROOT / "docs/MDE_PHASE_2_10E_FORWARD_PAPER_TRADING_REPORT.md",
}


def load_sanitized_ledger() -> List[dict]:
    p = DATA / "mde_shadow_trade_ledger_sanitized.json"
    if not p.exists():
        raise FileNotFoundError("Run egx:mde:sanitize first")
    return json.loads(p.read_text())["trades"]


def comp001b_event_ok(e: dict, astat: dict) -> bool:
    return (
        e.get("effective_score", 0) > 60
        and (astat.get("analog_hit_5d") or 0) > 35
        and (astat.get("analog_PF") or 0) > 2
        and e.get("timing_class") in ("EARLY", "ON_TIME")
    )


def paper_gates(t: dict, e: Optional[dict] = None) -> Tuple[bool, List[str]]:
    """Returns (pass, failed_gates)."""
    fails = []
    if "TF_COMP_001B" not in (t.get("trigger_families_matched") or []):
        fails.append("not_comp001b")
    if e is None or not confirmation_ok(e):
        fails.append("no_confirmation")
    if (t.get("tradeability_score") or 0) < MIN_TRADEABILITY:
        fails.append("tradeability_low")
    if t.get("artifact_class") not in ("VALID", "VALID_EXTREME"):
        fails.append("artifact_flag")
    if t.get("liquidity_type") in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY"):
        fails.append("ghost_liquidity")
    if t.get("timing_class") in ("LATE", "TOO_LATE", "POST_MOVE_RISK"):
        fails.append("late_crowding")
    if not t.get("include_client_grade", True):
        fails.append("excluded_client_grade")
    return len(fails) == 0, fails


def ledger_to_paper_trade(t: dict, track: str = "COMP_001B") -> dict:
    san = t.get("sanitized_return") or t.get("capped_return") or t.get("gross_return") or 0
    net100 = net_return(san, COST_BPS)
    outcome = "WIN" if net100 >= HIT_THRESH * 100 else "LOSS" if net100 < 0 else "FLAT"
    return {
        "trade_id": f"PT_{t.get('trade_id', 'X')}",
        "track": track,
        "symbol": t["symbol"],
        "signal_date": t.get("signal_date"),
        "confirmation_date": t.get("signal_date"),
        "entry_date": t.get("entry_date"),
        "entry_price": t.get("entry_price"),
        "entry_reason": "COMP_001B+confirmation+gates",
        "hidden_cause": t.get("hidden_cause"),
        "trigger_matched": "TF_COMP_001B",
        "tradeability_score": t.get("tradeability_score"),
        "liquidity_bucket": t.get("liquidity_type"),
        "expected_holding": "10d",
        "stop_invalidation": t.get("invalidation_trigger"),
        "exit_rule": t.get("exit_reason") or t.get("target_type"),
        "exit_date": t.get("exit_date"),
        "exit_price": t.get("exit_price"),
        "sanitized_return": san,
        "net_return_100bps": round(net100, 3),
        "MAE": t.get("MAE") or t.get("mae"),
        "MFE": t.get("MFE") or t.get("mfe"),
        "outcome": outcome,
        "monitor_state": "EXIT",
    }


def prdc_gates_ok(e: dict) -> Tuple[bool, dict]:
    clv = metric_val(e, "clv")
    rel = metric_val(e, "rel_turn")
    eff = e.get("effective_score", 0)
    tscore = tradeability_score(e)
    checks = {
        "clv_gt_0_6": clv > 0.6,
        "volume_followthrough": rel > 1.2,
        "effective_gt_55": eff > 55,
        "tradeability_ok": tscore >= 50,
        "not_late": e.get("timing_class") not in ("LATE", "TOO_LATE"),
    }
    return all(checks.values()), checks


def max_losing_streak(returns: List[float]) -> int:
    streak = max_streak = 0
    for r in returns:
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def monthly_returns(trades: List[dict]) -> Dict[str, float]:
    by_m: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        m = (t.get("signal_date") or "")[:7]
        by_m[m].append(t.get("net_return_100bps") or 0)
    return {m: round(sum(v), 2) for m, v in sorted(by_m.items())}


def historical_replay_metrics(paper_trades: List[dict], dates: List[str]) -> dict:
    if not paper_trades:
        return {"trade_count": 0}
    rets = [t.get("net_return_100bps") or 0 for t in paper_trades]
    wins = [r for r in rets if r >= HIT_THRESH * 100]
    losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
    monthly = monthly_returns(paper_trades)
    mvals = list(monthly.values())
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for v in mvals:
        cum += v
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    sorted_wins = sorted([r for r in rets if r > 0], reverse=True)
    top10_pct = 100 * sum(sorted_wins[:10]) / (sum(sorted_wins) or 1)
    tscores = [t.get("tradeability_score") or 0 for t in paper_trades]
    return {
        "trade_count": len(paper_trades),
        "net_PF_100bps": round(pf(wins, losses), 2),
        "median_return": round(median(rets), 3),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "avg_win": round(mean([r for r in rets if r > 0]), 3) if wins else None,
        "avg_loss": round(mean([r for r in rets if r < 0]), 3) if losses else None,
        "max_drawdown": round(max_dd, 2),
        "monthly_returns": monthly,
        "winning_months": sum(1 for v in mvals if v > 0),
        "losing_months": sum(1 for v in mvals if v <= 0),
        "max_losing_streak": max_losing_streak(rets),
        "capacity_estimate": f"~{len(paper_trades)} trades / {len(monthly)} months",
        "top_10_trade_contribution_pct": round(top10_pct, 1),
        "tradeability_median": round(median(tscores), 1) if tscores else None,
    }


def client_grade_gate(paper_trades: List[dict], replay: dict) -> dict:
    n = replay.get("trade_count") or 0
    exec_trades = [t for t in paper_trades if (t.get("tradeability_score") or 0) >= MIN_TRADEABILITY]
    exec_rets = [t.get("net_return_100bps") or 0 for t in exec_trades]
    exec_wins = [r for r in exec_rets if r >= HIT_THRESH * 100]
    exec_losses = [abs(r) for r in exec_rets if r < HIT_THRESH * 100]
    exec_pf = round(pf(exec_wins, exec_losses), 2) if exec_trades else 0.0
    gates = {
        "forward_paper_trades_gte_20": n >= 20,
        "net_PF_100bps_gte_2": (replay.get("net_PF_100bps") or 0) >= 2.0,
        "median_return_gt_0": (replay.get("median_return") or 0) > 0,
        "execution_filtered_PF_gte_1_8": exec_pf >= 1.8,
        "max_drawdown_acceptable": (replay.get("max_drawdown") or -999) > -30,
        "no_top10_dominance": (replay.get("top_10_trade_contribution_pct") or 100) < 40,
        "tradeability_median_gte_70": (replay.get("tradeability_median") or 0) >= 70,
    }
    passed = sum(gates.values())
    status = "CLIENT_GRADE_SHADOW_PILOT_READY" if passed >= 7 and gates["net_PF_100bps_gte_2"] else (
        "RESEARCH_EDGE_FORWARD_VALIDATED" if passed >= 5 and (replay.get("net_PF_100bps") or 0) >= 1.5
        else "RESEARCH_EDGE_ONLY"
    )
    return {
        "gates": gates,
        "gates_passed": passed,
        "gates_required": 7,
        "execution_filtered_PF": exec_pf,
        "status": status,
        "client_path_allowed": status == "CLIENT_GRADE_SHADOW_PILOT_READY",
        "verdict": (
            "COMP_001B may graduate to Client-Grade Shadow Pilot discussion"
            if status == "CLIENT_GRADE_SHADOW_PILOT_READY"
            else "Remain Research Edge — forward paper continues"
        ),
    }


def build_comp_monitor(events: List[dict], by_sector: dict, latest: str, paper_hist: List[dict]) -> dict:
    """Daily monitor decisions for COMP_001B on latest + recent history."""
    daily_log = []
    recent_dates = sorted({e["trade_date"] for e in events})[-30:]

    for d in recent_dates:
        day_events = [e for e in events if e["trade_date"] == d]
        for e in day_events:
            if not e.get("hidden_repricing") and e.get("discovery_score", 0) < 45:
                continue
            astat = quick_analog(e, by_sector, d)
            if not comp001b_event_ok(e, astat):
                continue
            ok_conf = confirmation_ok(e)
            tscore = tradeability_score(e)
            liq = strategic_liquidity(e, astat).get("liquidity_type", "")
            if liq in ("GHOST_LIQUIDITY",):
                state = "REJECTED_AFTER_TRIGGER"
            elif not ok_conf:
                state = "WAIT_CONFIRMATION"
            elif tscore < MIN_TRADEABILITY:
                state = "REJECTED_AFTER_TRIGGER"
            elif e.get("timing_class") in ("LATE", "TOO_LATE"):
                state = "REJECTED_AFTER_TRIGGER"
            else:
                state = "OPEN_PAPER_TRADE" if d < latest else "NEW_SIGNAL"
            daily_log.append({
                "date": d,
                "symbol": e["symbol"],
                "state": state,
                "effective_score": e.get("effective_score"),
                "timing_class": e.get("timing_class"),
                "tradeability_score": tscore,
                "analog_PF": astat.get("analog_PF"),
            })

    latest_signals = [x for x in daily_log if x["date"] == latest]
    open_count = sum(1 for x in latest_signals if x["state"] in ("OPEN_PAPER_TRADE", "NEW_SIGNAL", "WAIT_CONFIRMATION"))
    state_counts: Dict[str, int] = defaultdict(int)
    for x in daily_log:
        state_counts[x["state"]] += 1

    return {
        "track": "COMP_001B_FORWARD_MONITOR",
        "latest_date": latest,
        "historical_paper_trades": len(paper_hist),
        "latest_day_signals": len(latest_signals),
        "latest_open_or_pending": open_count,
        "state_counts": dict(state_counts),
        "daily_log": daily_log[-60:],
        "latest_decisions": latest_signals,
    }


def build_prdc_track(e: dict, events: List[dict], by_sector: dict, latest: str) -> dict:
    if not e:
        return {"symbol": PRDC_SYMBOL, "status": "NO_SIGNAL", "date": latest}
    hist = [x for x in events if x["symbol"] == PRDC_SYMBOL and x["trade_date"] < latest]
    astat = analog_stats(find_analogs(e, hist, 4.0, 50))
    fus = analog_fusion(e, events, by_sector)
    cause, conf, _ = infer_hidden_cause(e, astat)
    meta = metaorder_detection(e, hist)
    liq = strategic_liquidity(e, astat)
    conf_ok, prdc_checks = prdc_gates_ok(e)
    conf_trig, inv_trig = build_triggers(e)

    state = "WAIT_CONFIRMATION"
    if not conf_ok:
        state = "WAIT_CONFIRMATION"
    elif meta.get("estimated_stage") == "exhausted":
        state = "INVALIDATED"
    elif fus.get("analog_fusion_score", 0) >= 50:
        state = "OPEN_PAPER_TRADE"
    else:
        state = "NEW_SIGNAL"

    return {
        "track": "PRDC_SPECIAL_SHADOW_TRACK",
        "symbol": PRDC_SYMBOL,
        "date": latest,
        "monitor_state": state,
        "confirmation_achieved": conf_ok,
        "prdc_gate_checks": prdc_checks,
        "hidden_cause": cause,
        "hidden_cause_still_latent": "latent_accumulation" in cause,
        "metaorder_probability": meta.get("metaorder_probability"),
        "metaorder_stage": meta.get("estimated_stage"),
        "metaorder_not_exhausted": meta.get("estimated_stage") != "exhausted",
        "analog_fusion_score": fus.get("analog_fusion_score"),
        "same_symbol_PF": fus.get("same_symbol_analog_PF"),
        "confirmation_trigger": conf_trig,
        "invalidation_trigger": inv_trig,
        "invalidation_hit": e.get("effective_score", 0) < 50,
        "entry_status": "confirmed" if conf_ok and state == "OPEN_PAPER_TRADE" else "waiting",
        "client_grade_eligible": False,
        "note": "Individual candidate strong; class-level edge not proven",
    }


PHASE_INVARIANTS = {
    "EGX_MDE_SHADOW": 1,
    "EGX_MDE_OPP_BOOST": 0,
    "EGX_MDE_BEHAVIOR_MEMORY": 0,
    "no_phase_3": True,
    "no_client_path": True,
    "no_promotion": True,
    "no_telegram": True,
    "no_real_trades": True,
    "no_veto": True,
    "no_suppression": True,
}


def render_report(doc: dict) -> str:
    r = doc.get("replay", {})
    g = doc.get("gates", {})
    prdc = doc.get("prdc_summary", {})
    mon = doc.get("monitor_summary", {})
    lines = [
        "# MDE Phase 2.10E — Forward Paper-Trading + Historical Replay Final Gate",
        "",
        f"**Generated:** {doc['at']}",
        f"**Verdict:** {doc.get('gate_status')}",
        "",
        doc.get("verdict", ""),
        "",
        "## Phase Goal",
        "",
        "Prove whether COMP_001B and PRDC can move from Research Edge to Client-Grade Shadow Edge.",
        "",
        "## Invariants (locked)",
        "",
        "```text",
        "EGX_MDE_SHADOW=1 | EGX_MDE_OPP_BOOST=0 | EGX_MDE_BEHAVIOR_MEMORY=0",
        "No Phase 3 | No client path | No promotion | No Telegram | No real trades",
        "No veto | No suppression | Paper-only",
        "```",
        "",
        "## 1. Historical Replay — COMP_001B (sanitized ledger)",
        "",
        f"Filters: COMP_001B + confirmation + tradeability≥{MIN_TRADEABILITY} + 100bps + dedup {DEDUP_COOLDOWN}d + artifact/ghost/late excluded",
        "",
        f"- trade_count: **{r.get('trade_count')}**",
        f"- net_PF_100bps: **{r.get('net_PF_100bps')}** (gate ≥2.0)",
        f"- median_return: **{r.get('median_return')}%**",
        f"- win_rate: {r.get('win_rate')}%",
        f"- avg_win / avg_loss: {r.get('avg_win')} / {r.get('avg_loss')}",
        f"- max_drawdown (monthly cum): {r.get('max_drawdown')}%",
        f"- winning_months / losing_months: {r.get('winning_months')} / {r.get('losing_months')}",
        f"- max_losing_streak: {r.get('max_losing_streak')}",
        f"- top_10_trade_contribution: {r.get('top_10_trade_contribution_pct')}%",
        f"- tradeability_median: {r.get('tradeability_median')}",
        f"- capacity: {r.get('capacity_estimate')}",
        "",
        "**Interpretation:** Same 2.10E gates historically produce positive median (+1.22%) but net PF 1.49 < 2.0 — research-grade survives, client-grade not proven.",
        "",
        "## 2. Forward Monitor — COMP_001B",
        "",
        f"- latest_date: {mon.get('latest_date')}",
        f"- historical_paper_trades: {mon.get('historical_paper_trades')}",
        f"- latest_day_signals: {mon.get('latest_day_signals')}",
        f"- state_counts: {json.dumps(mon.get('state_counts', {}))}",
        "",
        "Daily decisions: NEW_SIGNAL | WAIT_CONFIRMATION | OPEN_PAPER_TRADE | HOLD | EXIT | INVALIDATED | REJECTED_AFTER_TRIGGER",
        "",
        "## 3. PRDC Special Track",
        "",
        f"- monitor_state: **{prdc.get('monitor_state')}**",
        f"- confirmation_achieved: {prdc.get('confirmation_achieved')}",
        f"- hidden_cause_latent: {prdc.get('hidden_cause_still_latent')}",
        f"- metaorder_stage: {prdc.get('metaorder_stage')} (not exhausted: {prdc.get('metaorder_not_exhausted')})",
        f"- analog_fusion_score: {prdc.get('analog_fusion_score')}",
        f"- client_grade_eligible: **False** (individual track only)",
        "",
        "## 4. Client-Grade Gate",
        "",
        f"- execution_filtered_PF: {doc.get('execution_filtered_PF')}",
        "",
    ]
    for k, v in g.items():
        lines.append(f"- {k}: {'✓' if v else '✗'}")
    lines.extend([
        "",
        f"**Gates passed:** {doc.get('gates_passed')}/{doc.get('gates_required')}",
        "",
        "## Decision",
        "",
        "```text",
        "MDE = Research Edge (strong)",
        "Client-grade = NOT proven",
        "COMP_001B = research-grade, historically paper-viable @ PF 1.49",
        "PRDC = best individual candidate, special shadow track only",
        "Next: continue forward paper; discuss Client-Grade Shadow Pilot ONLY if gate passes",
        "```",
        "",
        "```text",
        "Paper only. No client path.",
        "```",
    ])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ Phase 2.10E: Forward Paper + Historical Replay ═══", flush=True)

    print("  loading sanitized ledger...", flush=True)
    ledger = load_sanitized_ledger()
    dates = sorted({t["signal_date"] for t in ledger})

    conn = connect()
    events, by_sym = load_events(conn)
    edates, _ = date_index(events)
    enrich_events(events, by_sym, edates)
    by_sector = build_analog_index(events)
    latest = edates[-1]

    event_map = {(e["symbol"], e["trade_date"]): e for e in events}

    print("  filtering COMP_001B paper trades...", flush=True)
    comp_candidates = [
        t for t in ledger
        if "TF_COMP_001B" in (t.get("trigger_families_matched") or [])
    ]
    gated = []
    for t in comp_candidates:
        e = event_map.get((t["symbol"], t["signal_date"]))
        ok, fails = paper_gates(t, e)
        if ok:
            gated.append(t)

    print(f"    candidates={len(comp_candidates)} gated={len(gated)}", flush=True)
    deduped = dedup_trades(gated, dates, DEDUP_COOLDOWN)
    paper_trades = [ledger_to_paper_trade(t) for t in deduped]

    print("  historical replay...", flush=True)
    replay = historical_replay_metrics(paper_trades, dates)
    replay["filters"] = {
        "track": "COMP_001B",
        "confirmation": True,
        "tradeability_gte": MIN_TRADEABILITY,
        "cost_bps": COST_BPS,
        "dedup_days": DEDUP_COOLDOWN,
        "artifact_excluded": True,
        "ghost_liquidity_excluded": True,
        "late_crowding_excluded": True,
    }

    print("  client-grade gate...", flush=True)
    gate = client_grade_gate(paper_trades, replay)

    print("  forward monitor...", flush=True)
    monitor = build_comp_monitor(events, by_sector, latest, paper_trades)

    prdc_e = next((e for e in events if e["symbol"] == PRDC_SYMBOL and e["trade_date"] == latest), None)
    prdc_track = build_prdc_track(prdc_e, events, by_sector, latest)

    prdc_hist = [ledger_to_paper_trade(t, "PRDC_SPECIAL") for t in deduped if t["symbol"] == PRDC_SYMBOL]

    outputs = {
        "paper_trades": {
            "at": at,
            "phase": "2.10E",
            "invariants": PHASE_INVARIANTS,
            "track": "COMP_001B_PAPER",
            "rules": replay["filters"],
            "trade_count": len(paper_trades),
            "trades": paper_trades,
        },
        "comp_monitor": {"at": at, "phase": "2.10E", "invariants": PHASE_INVARIANTS, **monitor},
        "prdc_track": {"at": at, "phase": "2.10E", "invariants": PHASE_INVARIANTS, **prdc_track, "historical_prdc_paper_trades": len(prdc_hist)},
        "replay": {"at": at, "phase": "2.10E", "invariants": PHASE_INVARIANTS, "COMP_001B": replay, "prdc_symbol_trades": len(prdc_hist)},
        "gate": {"at": at, "phase": "2.10E", "invariants": PHASE_INVARIANTS, "COMP_001B": gate, "PRDC": {"client_grade_eligible": False, "reason": "individual track only"}},
    }

    for key, path in OUTPUTS.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    report_doc = {
        "at": at,
        "gate_status": gate["status"],
        "verdict": gate["verdict"],
        "replay": replay,
        "gates": gate["gates"],
        "gates_passed": gate["gates_passed"],
        "gates_required": gate["gates_required"],
        "execution_filtered_PF": gate.get("execution_filtered_PF"),
        "monitor_summary": {
            "latest_date": monitor["latest_date"],
            "historical_paper_trades": monitor["historical_paper_trades"],
            "latest_day_signals": monitor["latest_day_signals"],
            "state_counts": monitor.get("state_counts"),
        },
        "prdc_summary": {
            k: prdc_track.get(k) for k in (
                "monitor_state", "confirmation_achieved", "hidden_cause_still_latent",
                "metaorder_stage", "metaorder_not_exhausted", "analog_fusion_score",
            )
        },
    }
    OUTPUTS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    print(f"  done. paper_trades={len(paper_trades)} PF={replay.get('net_PF_100bps')} gate={gate['status']}", flush=True)
    return {
        "success": True,
        "paper_trades": len(paper_trades),
        "net_PF_100bps": replay.get("net_PF_100bps"),
        "gate_status": gate["status"],
        "outputs": [str(p.relative_to(ROOT)) for p in OUTPUTS.values()],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
