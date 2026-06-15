#!/usr/bin/env python3
"""
LRE Phase 3.0 — Forward Paper-Trading + Historical Replay Gate.

Paper-only. Proves whether Ignition Candidates (Stage 3–4) can graduate from
Research Shadow to Client-Grade Shadow. No actionable / promotion / Telegram impact.
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

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    MAX_FORWARD,
    MIN_FORWARD,
    compression_days,
    connect,
    ensure_tables,
    load_all_bars,
    score_symbol_daily,
    vol_ratio,
)
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_walkforward_shadow import HIT_THRESH, pf  # noqa: E402

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": "LRE-3.0",
    "paper_only": True,
    "no_client_path": True,
}

COST_BPS = 100
MIN_EPS = 50.0
MIN_EPS_EXEC = 55.0
GATE_STAGES = {3, 4}
HOLD_DAYS = 10
DEDUP_COOLDOWN = 10
MAX_MOVE_20D_PCT = 15.0

OUTPUTS = {
    "paper_trades": DATA / "lre_forward_paper_trades.json",
    "ignition_monitor": DATA / "lre_ignition_forward_monitor.json",
    "top_track": DATA / "lre_top_eps_special_track.json",
    "replay": DATA / "lre_3_0_historical_replay.json",
    "gate": DATA / "lre_client_grade_gate_status.json",
    "report": ROOT / "docs/LRE_PHASE_3_0_FORWARD_PAPER_TRADING_REPORT.md",
}


def ignition_signal_ok(row: dict) -> Tuple[bool, List[str]]:
    fails = []
    if int(row.get("artifact_risk") or 0):
        fails.append("artifact")
    if int(row.get("stage") or 0) not in GATE_STAGES:
        fails.append("stage_not_3_4")
    if float(row.get("explosion_potential") or 0) < MIN_EPS:
        fails.append("eps_low")
    tags = row.get("list_tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    if "do_not_chase" in tags:
        fails.append("do_not_chase")
    if float(row.get("move_from_low_20d_pct") or 0) >= MAX_MOVE_20D_PCT:
        fails.append("move_too_extended")
    return len(fails) == 0, fails


def simulate_trade(bars: List[dict], idx: int) -> dict:
    """Structural stop + 10d hold (max 30d window). Returns gross_return in %."""
    entry = bars[idx]["close"]
    if not entry or entry <= 0:
        return {"gross_return": 0.0, "holding_days": 0, "exit_reason": "no_entry"}
    sl = bars[max(0, idx - 2):idx + 1]
    stop = min(b["low"] for b in sl if b["low"])
    mae = 0.0
    mfe = 0.0
    exit_price = entry
    exit_date = bars[idx]["date"]
    exit_reason = "time_exit"
    hold = 0

    end = min(len(bars) - 1, idx + MAX_FORWARD)
    for j in range(idx + 1, end + 1):
        lo, hi, cl = bars[j]["low"], bars[j]["high"], bars[j]["close"]
        if lo and hi:
            mae = min(mae, (lo - entry) / entry * 100)
            mfe = max(mfe, (hi - entry) / entry * 100)
        if lo and lo <= stop:
            exit_price = stop
            exit_date = bars[j]["date"]
            exit_reason = "structural_stop"
            hold = j - idx
            break
        hold = j - idx
        if hold >= HOLD_DAYS and cl:
            exit_price = cl
            exit_date = bars[j]["date"]
            exit_reason = "hold_10d"
            break
    else:
        if hold >= MIN_FORWARD and bars[idx + hold]["close"]:
            exit_price = bars[idx + hold]["close"]
            exit_date = bars[idx + hold]["date"]

    gross = (exit_price - entry) / entry * 100
    return {
        "entry_price": round(entry, 4),
        "stop_loss": round(stop, 4),
        "exit_price": round(exit_price, 4),
        "exit_date": exit_date,
        "holding_days": hold,
        "gross_return": round(gross, 3),
        "MAE": round(mae, 3),
        "MFE": round(mfe, 3),
        "exit_reason": exit_reason,
    }


def row_to_paper_trade(row: dict, sim: dict) -> dict:
    gross = sim.get("gross_return") or 0
    net100 = net_return(gross, COST_BPS)
    outcome = "WIN" if net100 >= HIT_THRESH * 100 else "LOSS" if net100 < 0 else "FLAT"
    tags = row.get("list_tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    return {
        "trade_id": f"LRE_{row['symbol']}_{row['trade_date']}",
        "track": "IGNITION_CANDIDATES",
        "symbol": row["symbol"],
        "signal_date": row["trade_date"],
        "entry_date": row["trade_date"],
        "stage": row.get("stage"),
        "stage_name": row.get("stage_name"),
        "explosion_potential": row.get("explosion_potential"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "move_from_low_20d_pct": row.get("move_from_low_20d_pct"),
        "list_tags": tags,
        "entry_reason": "LRE_stage_3_4+eps50+not_chase",
        "entry_price": sim.get("entry_price"),
        "stop_loss": sim.get("stop_loss"),
        "exit_date": sim.get("exit_date"),
        "exit_price": sim.get("exit_price"),
        "exit_rule": sim.get("exit_reason"),
        "holding_days": sim.get("holding_days"),
        "gross_return": gross,
        "net_return_100bps": round(net100, 3),
        "MAE": sim.get("MAE"),
        "MFE": sim.get("MFE"),
        "outcome": outcome,
        "monitor_state": "EXIT",
    }


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


def historical_replay_metrics(paper_trades: List[dict]) -> dict:
    if not paper_trades:
        return {"trade_count": 0}
    rets = [t.get("net_return_100bps") or 0 for t in paper_trades]
    wins = [r for r in rets if r >= HIT_THRESH * 100]
    losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
    monthly = monthly_returns(paper_trades)
    mvals = list(monthly.values())
    cum = peak = 0.0
    max_dd = 0.0
    for v in mvals:
        cum += v
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    sorted_wins = sorted([r for r in rets if r > 0], reverse=True)
    top10_pct = 100 * sum(sorted_wins[:10]) / (sum(sorted_wins) or 1)
    eps_vals = [t.get("explosion_potential") or 0 for t in paper_trades]
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
        "eps_median": round(median(eps_vals), 1) if eps_vals else None,
        "stop_hit_ratio": round(
            100 * sum(1 for t in paper_trades if t.get("exit_rule") == "structural_stop") / len(paper_trades), 1
        ),
    }


def client_grade_gate(paper_trades: List[dict], replay: dict) -> dict:
    n = replay.get("trade_count") or 0
    exec_trades = [t for t in paper_trades if (t.get("explosion_potential") or 0) >= MIN_EPS_EXEC]
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
        "eps_median_gte_50": (replay.get("eps_median") or 0) >= 50,
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
        "client_path_allowed": False,
        "verdict": (
            "LRE Ignition may graduate to Client-Grade Shadow Pilot discussion"
            if status == "CLIENT_GRADE_SHADOW_PILOT_READY"
            else "Remain Research Edge — forward paper continues"
        ),
    }


def build_historical_candidates(conn, by_sym: Dict[str, List[dict]]) -> List[dict]:
    """Walk full history; score ignition candidates with causal simulation."""
    raw: List[dict] = []
    rotation_empty: Dict[str, dict] = {}
    for sym, bars in by_sym.items():
        if len(bars) < 80:
            continue
        for idx in range(40, len(bars) - MAX_FORWARD):
            if vol_ratio(bars, idx, 20) < 1.05 and compression_days(bars, idx) < 6:
                continue
            td = bars[idx]["date"]
            row = score_symbol_daily(conn, sym, bars, td, rotation_empty)
            if not row:
                continue
            ok, _ = ignition_signal_ok(row)
            if not ok:
                continue
            sim = simulate_trade(bars, idx)
            raw.append(row_to_paper_trade(row, sim))
    return raw


def build_ignition_monitor(conn, latest: str, paper_hist: List[dict]) -> dict:
    rows = conn.execute(
        "SELECT * FROM lre_daily_scores WHERE trade_date=? ORDER BY explosion_potential DESC",
        (latest,),
    ).fetchall()
    daily_log = []
    for r in rows:
        row = dict(r)
        row["list_tags"] = json.loads(row.get("list_tags") or "[]")
        ok, fails = ignition_signal_ok(row)
        if "do_not_chase" in (row.get("list_tags") or []):
            state = "REJECTED_AFTER_TRIGGER"
        elif not ok:
            state = "WAIT_CONFIRMATION" if int(row.get("stage") or 0) < 3 else "REJECTED_AFTER_TRIGGER"
        else:
            state = "OPEN_PAPER_TRADE"
        daily_log.append({
            "date": latest,
            "symbol": row["symbol"],
            "state": state,
            "stage_name": row.get("stage_name"),
            "explosion_potential": row.get("explosion_potential"),
            "move_20d_pct": row.get("move_from_low_20d_pct"),
            "vol_ratio": row.get("vol_ratio_20"),
            "gate_fails": fails if not ok else [],
        })

    state_counts: Dict[str, int] = defaultdict(int)
    for x in daily_log:
        state_counts[x["state"]] += 1
    open_states = ("OPEN_PAPER_TRADE", "NEW_SIGNAL", "WAIT_CONFIRMATION")
    return {
        "track": "LRE_IGNITION_FORWARD_MONITOR",
        "latest_date": latest,
        "historical_paper_trades": len(paper_hist),
        "latest_day_signals": len(daily_log),
        "latest_open_or_pending": sum(1 for x in daily_log if x["state"] in open_states),
        "state_counts": dict(state_counts),
        "latest_decisions": daily_log[:40],
    }


def build_top_eps_track(conn, latest: str) -> dict:
    row = conn.execute(
        """SELECT * FROM lre_daily_scores
           WHERE trade_date=? AND artifact_risk=0
           ORDER BY explosion_potential DESC LIMIT 1""",
        (latest,),
    ).fetchone()
    if not row:
        return {"status": "NO_SIGNAL", "date": latest}
    d = dict(row)
    d["list_tags"] = json.loads(d.get("list_tags") or "[]")
    ok, fails = ignition_signal_ok(d)
    return {
        "symbol": d["symbol"],
        "date": latest,
        "monitor_state": "OPEN_PAPER_TRADE" if ok else "WAIT_CONFIRMATION",
        "explosion_potential": d.get("explosion_potential"),
        "stage_name": d.get("stage_name"),
        "gate_ok": ok,
        "gate_fails": fails,
        "client_grade_eligible": False,
        "note": "Top-EPS special shadow track only — not client path",
    }


def render_report(doc: dict) -> str:
    replay = doc.get("replay", {})
    gate = doc.get("gate", {})
    gates = gate.get("gates", {})
    mon = doc.get("monitor_summary", {})
    top = doc.get("top_summary", {})
    lines = [
        "# LRE Phase 3.0 — Forward Paper-Trading + Historical Replay Gate",
        "",
        f"**Generated:** {doc.get('at')}",
        f"**Verdict:** {gate.get('status')}",
        "",
        gate.get("verdict", ""),
        "",
        "## Invariants",
        "",
        "```text",
        "EGX_LRE_SHADOW=1 | EGX_LRE_OPP_BOOST=0",
        "No client path | No promotion | No Telegram | Paper-only",
        "```",
        "",
        "## 1. Historical Replay — Ignition Candidates (Stage 3–4)",
        "",
        f"Filters: stage 3–4 + EPS≥{MIN_EPS} + not chase + structural stop + {COST_BPS}bps + dedup {DEDUP_COOLDOWN}d",
        "",
        f"- trade_count: **{replay.get('trade_count', 0)}**",
        f"- net_PF_100bps: **{replay.get('net_PF_100bps')}** (gate ≥2.0)",
        f"- median_return: **{replay.get('median_return')}%**",
        f"- win_rate: {replay.get('win_rate')}%",
        f"- eps_median: {replay.get('eps_median')}",
        f"- stop_hit_ratio: {replay.get('stop_hit_ratio')}%",
        f"- max_drawdown (monthly cum): {replay.get('max_drawdown')}%",
        "",
        "## 2. Forward Monitor",
        "",
        f"- latest_date: {mon.get('latest_date')}",
        f"- historical_paper_trades: {mon.get('historical_paper_trades')}",
        f"- latest_open_or_pending: {mon.get('latest_open_or_pending')}",
        f"- state_counts: {json.dumps(mon.get('state_counts', {}))}",
        "",
        "## 3. Top-EPS Special Track",
        "",
        f"- symbol: **{top.get('symbol')}**",
        f"- monitor_state: {top.get('monitor_state')}",
        f"- eps: {top.get('explosion_potential')}",
        "",
        "## 4. Client-Grade Gate",
        "",
        f"- execution_filtered_PF: {gate.get('execution_filtered_PF')}",
        "",
    ]
    for k, v in gates.items():
        lines.append(f"- {k}: {'✓' if v else '✗'}")
    lines.extend([
        "",
        f"**Gates passed:** {gate.get('gates_passed')}/{gate.get('gates_required')}",
        "",
        "## Decision",
        "",
        "```text",
        "LRE = Research Edge until gate passes",
        "Client-grade = NOT proven until net PF ≥ 2.0 with full gates",
        "Continue forward paper — no actionable impact",
        "```",
    ])
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ LRE-3.0: Forward Paper + Historical Replay ═══", flush=True)

    conn = connect()
    ensure_tables(conn)
    by_sym, meta = load_all_bars(conn)
    latest = params.get("trade_date") or meta.get("max_date")

    print(f"  replay walk {meta['symbols']} symbols...", flush=True)
    raw = build_historical_candidates(conn, by_sym)
    dates = sorted({t["signal_date"] for t in raw})
    deduped = dedup_trades(raw, dates, DEDUP_COOLDOWN)
    print(f"    raw={len(raw)} deduped={len(deduped)}", flush=True)

    replay = historical_replay_metrics(deduped)
    replay["filters"] = {
        "track": "IGNITION_CANDIDATES",
        "stage": list(GATE_STAGES),
        "eps_gte": MIN_EPS,
        "max_move_20d_pct": MAX_MOVE_20D_PCT,
        "cost_bps": COST_BPS,
        "dedup_days": DEDUP_COOLDOWN,
        "hold_days": HOLD_DAYS,
        "stop": "structural_3bar_low",
    }

    gate = client_grade_gate(deduped, replay)
    monitor = build_ignition_monitor(conn, latest, deduped)
    top_track = build_top_eps_track(conn, latest)

    outputs = {
        "paper_trades": {
            "at": at,
            "phase": "LRE-3.0",
            "invariants": PHASE_INVARIANTS,
            "track": "IGNITION_CANDIDATES",
            "rules": replay["filters"],
            "trade_count": len(deduped),
            "trades": deduped,
        },
        "ignition_monitor": {"at": at, "phase": "LRE-3.0", "invariants": PHASE_INVARIANTS, **monitor},
        "top_track": {"at": at, "phase": "LRE-3.0", "invariants": PHASE_INVARIANTS, **top_track},
        "replay": {"at": at, "phase": "LRE-3.0", "invariants": PHASE_INVARIANTS, "IGNITION": replay},
        "gate": {"at": at, "phase": "LRE-3.0", "invariants": PHASE_INVARIANTS, "IGNITION": gate},
    }

    for key, path in OUTPUTS.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    report_doc = {
        "at": at,
        "replay": replay,
        "gate": gate,
        "monitor_summary": {
            "latest_date": monitor["latest_date"],
            "historical_paper_trades": monitor["historical_paper_trades"],
            "latest_open_or_pending": monitor["latest_open_or_pending"],
            "state_counts": monitor.get("state_counts"),
        },
        "top_summary": top_track,
    }
    OUTPUTS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    print(
        f"  done. trades={len(deduped)} PF={replay.get('net_PF_100bps')} gate={gate['status']}",
        flush=True,
    )
    return {
        "success": True,
        "paper_trades": len(deduped),
        "net_PF_100bps": replay.get("net_PF_100bps"),
        "gate_status": gate["status"],
        "outputs": [str(p.relative_to(ROOT)) for p in OUTPUTS.values()],
    }


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(cmd_run(p), indent=2))
