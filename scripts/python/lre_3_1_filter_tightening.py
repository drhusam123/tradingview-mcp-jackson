#!/usr/bin/env python3
"""
LRE Phase 3.1 — Filter Tightening + Sanitized Paper Re-Run.

Keeps LRE-3.0 baseline for comparison. Shadow only — no client path.
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

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    MAX_FORWARD,
    MIN_FORWARD,
    compression_days,
    connect,
    ensure_tables,
    load_all_bars,
    vol_ratio,
)
from lre_3_1_filters import (  # noqa: E402
    MODE_SPECS,
    calibrate_a_thresholds,
    compression_filter_ok,
    enrich_signal,
    load_fingerprints,
    mode_passes,
    volume_filter_ok,
)
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_walkforward_shadow import HIT_THRESH, pf  # noqa: E402

PHASE_INVARIANTS = {**LRE_INVARIANTS, "phase": "LRE-3.1", "paper_only": True, "client_path_allowed": False}
COST_BPS = 100
COST_BPS_150 = 150
DEDUP_COOLDOWN = 10

WINDOWS = {
    "full": ("2020-12-10", "2099-12-31"),
    "in_sample": ("2020-12-10", "2024-12-31"),
    "oos": ("2025-01-01", "2099-12-31"),
    "latest_6m": None,
    "latest_3m": None,
}

MIN_TRADES = {"full": 150, "oos": 40, "latest_6m": 15, "latest_3m": 10, "in_sample": 100}

OUTPUTS = {
    "filter_replay": DATA / "lre_3_1_filter_replay.json",
    "sanitized_replay": DATA / "lre_3_1_sanitized_replay.json",
    "mode_comparison": DATA / "lre_3_1_mode_comparison.json",
    "forward_candidates": DATA / "lre_3_1_forward_candidates_last.json",
    "report": ROOT / "docs/LRE_PHASE_3_1_FILTER_TIGHTENING_REPORT.md",
}


def _window_dates(max_date: str) -> dict:
    from datetime import datetime as dt, timedelta
    md = dt.strptime(max_date, "%Y-%m-%d")
    w = dict(WINDOWS)
    w["latest_6m"] = ((md - timedelta(days=183)).strftime("%Y-%m-%d"), max_date)
    w["latest_3m"] = ((md - timedelta(days=92)).strftime("%Y-%m-%d"), max_date)
    return w


def simulate_trade(bars: List[dict], idx: int, hold_days: int = 10, stop_pct: Optional[float] = None) -> dict:
    entry = bars[idx]["close"]
    if not entry or entry <= 0:
        return {"gross_return": 0.0, "holding_days": 0, "exit_reason": "no_entry"}
    if stop_pct is not None:
        stop = entry * (1 - stop_pct / 100)
    else:
        sl = bars[max(0, idx - 2):idx + 1]
        stop = min(b["low"] for b in sl if b["low"])
    mae = mfe = 0.0
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
            exit_reason = "stop_hit"
            hold = j - idx
            break
        hold = j - idx
        if hold >= hold_days and cl:
            exit_price = cl
            exit_date = bars[j]["date"]
            exit_reason = f"hold_{hold_days}d"
            break
    gross = (exit_price - entry) / entry * 100
    return {
        "gross_return": round(gross, 3),
        "holding_days": hold,
        "exit_reason": exit_reason,
        "MAE": round(mae, 3),
        "MFE": round(mfe, 3),
        "entry_price": round(entry, 4),
        "stop_loss": round(stop, 4),
        "exit_price": round(exit_price, 4),
        "exit_date": exit_date,
    }


def row_to_trade(row: dict, sim: dict, mode: str) -> dict:
    gross = sim.get("gross_return") or 0
    net100 = net_return(gross, COST_BPS)
    net150 = net_return(gross, COST_BPS_150)
    return {
        "trade_id": f"LRE31_{mode}_{row['symbol']}_{row['trade_date']}",
        "mode": mode,
        "symbol": row["symbol"],
        "signal_date": row["trade_date"],
        "stage": row.get("stage"),
        "stage_name": row.get("stage_name"),
        "explosion_potential": row.get("explosion_potential"),
        "family_similarity_A": row.get("family_similarity_A"),
        "family_similarity_F": row.get("family_similarity_F"),
        "stop_prone_score": row.get("stop_prone_score"),
        "compression_days": row.get("compression_days"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "gross_return": gross,
        "net_return_100bps": round(net100, 3),
        "net_return_150bps": round(net150, 3),
        "MAE": sim.get("MAE"),
        "MFE": sim.get("MFE"),
        "exit_reason": sim.get("exit_reason"),
        "holding_days": sim.get("holding_days"),
        "artifact_risk": row.get("artifact_risk"),
        "liquidity_fitness_score": row.get("liquidity_fitness_score"),
        "already_exploded": row.get("already_exploded"),
        "outcome": "WIN" if net100 >= 5 else "LOSS" if net100 < 0 else "FLAT",
    }


def build_candidates(conn, by_sym: dict, fingerprints: dict, thresholds: dict) -> List[dict]:
    enriched: List[dict] = []
    for sym, bars in by_sym.items():
        if len(bars) < 80:
            continue
        for idx in range(40, len(bars) - MAX_FORWARD):
            if vol_ratio(bars, idx, 20) < 1.05 and compression_days(bars, idx) < 6:
                continue
            row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
            if not row:
                continue
            if int(row.get("stage") or 0) not in (3, 4, 5, 6, 7) and float(row.get("explosion_potential") or 0) < 45:
                continue
            sim = simulate_trade(bars, idx, hold_days=10)
            row["_sim"] = sim
            row["_bars_sym"] = sym
            row["_idx"] = idx
            enriched.append(row)
    return enriched


def filter_trades(candidates: List[dict], mode: str, thresholds: dict, by_sym: dict) -> List[dict]:
    out = []
    for row in candidates:
        ok, _ = mode_passes(row, mode, thresholds)
        if mode != "baseline_3_0":
            if row.get("artifact_risk"):
                continue
            v_ok, _ = volume_filter_ok(by_sym[row["_bars_sym"]], row["_idx"])
            if MODE_SPECS[mode]["require_volume_band"] and not v_ok:
                continue
            spec = MODE_SPECS[mode]
            c_ok, _ = compression_filter_ok(by_sym[row["_bars_sym"]], row["_idx"], spec["min_compression"])
            if spec["min_compression"] > 0 and not c_ok:
                continue
        if not ok:
            continue
        out.append(row_to_trade(row, row["_sim"], mode))
    dates = sorted({t["signal_date"] for t in out})
    return dedup_trades(out, dates, DEDUP_COOLDOWN)


def in_window(date: str, win: Tuple[str, str]) -> bool:
    return win[0] <= date <= win[1]


def trade_metrics(trades: List[dict]) -> dict:
    if not trades:
        return {"trade_count": 0, "sample_ok": False}
    rets = [t.get("net_return_100bps") or 0 for t in trades]
    gross = [t.get("gross_return") or 0 for t in trades]
    wins = [r for r in rets if r >= 5]
    losses = [abs(r) for r in rets if r < 5]
    nets150 = [t.get("net_return_150bps") or 0 for t in trades]
    maes = [t.get("MAE") or 0 for t in trades]
    mfes = [t.get("MFE") or 0 for t in trades]
    sym_pnl = defaultdict(float)
    for t in trades:
        sym_pnl[t["symbol"]] += t.get("net_return_100bps") or 0
    top10 = sum(v for _, v in sorted(sym_pnl.items(), key=lambda x: -x[1])[:10])
    total = sum(sym_pnl.values()) or 1
    stop_hits = sum(1 for t in trades if t.get("exit_reason") == "stop_hit")
    art = sum(1 for t in trades if t.get("artifact_risk"))
    low_liq = sum(1 for t in trades if (t.get("liquidity_fitness_score") or 0) < 40)
    exploded = sum(1 for t in trades if t.get("already_exploded"))
    a_pure = sum(1 for t in trades if (t.get("family_similarity_A") or 0) >= 55)
    f_leak = sum(1 for t in trades if (t.get("family_similarity_F") or 0) > 35)
    stage34 = sum(1 for t in trades if int(t.get("stage") or 0) in (3, 4))
    stage56 = sum(1 for t in trades if int(t.get("stage") or 0) in (5, 6))
    return {
        "trade_count": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "hit_5pct": round(100 * sum(1 for g in gross if g >= 5) / len(gross), 1),
        "hit_10pct": round(100 * sum(1 for g in gross if g >= 10) / len(gross), 1),
        "hit_15pct": round(100 * sum(1 for g in gross if g >= 15) / len(gross), 1),
        "hit_20pct": round(100 * sum(1 for g in gross if g >= 20) / len(gross), 1),
        "median_return": round(median(rets), 3),
        "average_return": round(mean(rets), 3),
        "net_PF_100bps": round(pf(wins, losses), 2),
        "net_PF_150bps": round(pf(
            [r for r in nets150 if r >= 5],
            [abs(r) for r in nets150 if r < 5],
        ), 2),
        "stop_hit_ratio": round(100 * stop_hits / len(trades), 1),
        "MAE_median": round(median(maes), 3),
        "MFE_median": round(median(mfes), 3),
        "top10_symbol_dominance_pct": round(100 * abs(top10) / abs(total), 1) if total else 0,
        "artifact_contribution_pct": round(100 * art / len(trades), 1),
        "low_liquidity_contribution_pct": round(100 * low_liq / len(trades), 1),
        "already_exploded_contamination_pct": round(100 * exploded / len(trades), 1),
        "family_A_purity_pct": round(100 * a_pure / len(trades), 1),
        "F_artifact_leakage_pct": round(100 * f_leak / len(trades), 1),
        "stage_3_4_pct": round(100 * stage34 / len(trades), 1),
        "stage_5_6_pct": round(100 * stage56 / len(trades), 1),
        "eps_median": round(median([t.get("explosion_potential") or 0 for t in trades]), 1),
        "A_sim_median": round(median([t.get("family_similarity_A") or 0 for t in trades]), 1),
        "stop_prone_median": round(median([t.get("stop_prone_score") or 0 for t in trades]), 1),
    }


def exit_diagnostics(candidates: List[dict], mode: str, thresholds: dict, by_sym: dict, sample_cap: int = 600) -> dict:
    trades_raw = []
    for row in candidates:
        ok, _ = mode_passes(row, mode, thresholds)
        if mode != "baseline_3_0":
            if row.get("artifact_risk"):
                continue
            if MODE_SPECS[mode]["require_volume_band"] and not volume_filter_ok(by_sym[row["_bars_sym"]], row["_idx"])[0]:
                continue
            if MODE_SPECS[mode]["min_compression"] > 0 and not compression_filter_ok(
                by_sym[row["_bars_sym"]], row["_idx"], MODE_SPECS[mode]["min_compression"]
            )[0]:
                continue
        if not ok:
            continue
        trades_raw.append(row)
        if len(trades_raw) >= sample_cap:
            break
    bars_idx = {(r["_bars_sym"], r["trade_date"]): (by_sym[r["_bars_sym"]], r["_idx"]) for r in trades_raw}
    out = {}
    for hold in (5, 10, 20, 30):
        sims = []
        for r in trades_raw[:sample_cap]:
            bars, idx = bars_idx[(r["_bars_sym"], r["trade_date"])]
            sims.append(simulate_trade(bars, idx, hold_days=hold))
        rets = [net_return(s["gross_return"], COST_BPS) for s in sims]
        wins = [x for x in rets if x >= 5]
        losses = [abs(x) for x in rets if x < 5]
        out[f"hold_{hold}d"] = {"PF": round(pf(wins, losses), 2), "median": round(median(rets), 3), "n": len(rets)}
    for stop in (6, 8):
        sims = []
        for r in trades_raw[:sample_cap]:
            bars, idx = bars_idx[(r["_bars_sym"], r["trade_date"])]
            sims.append(simulate_trade(bars, idx, hold_days=10, stop_pct=stop))
        rets = [net_return(s["gross_return"], COST_BPS) for s in sims]
        wins = [x for x in rets if x >= 5]
        losses = [abs(x) for x in rets if x < 5]
        out[f"stop_{stop}pct"] = {
            "PF": round(pf(wins, losses), 2),
            "median": round(median(rets), 3),
            "stop_hit_pct": round(100 * sum(1 for s in sims if s["exit_reason"] == "stop_hit") / len(sims), 1),
        }
    return out


def review_symbol(conn, by_sym: dict, fingerprints: dict, thresholds: dict, sym: str, date: str) -> dict:
    bars = by_sym.get(sym)
    if not bars:
        return {"symbol": sym, "error": "no_bars"}
    idx = next((i for i, b in enumerate(bars) if b["date"] == date), None)
    if idx is None:
        return {"symbol": sym, "error": "no_date"}
    row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
    if not row:
        return {"symbol": sym, "error": "enrich_failed"}
    modes = {}
    for mode in MODE_SPECS:
        ok, fails = mode_passes(row, mode, thresholds)
        if mode != "baseline_3_0":
            if row.get("artifact_risk"):
                ok = False
                fails.append("artifact")
            if MODE_SPECS[mode]["require_volume_band"] and not volume_filter_ok(bars, idx)[0]:
                ok = False
                fails.append("volume_band")
            if MODE_SPECS[mode]["min_compression"] > 0 and not compression_filter_ok(bars, idx, MODE_SPECS[mode]["min_compression"])[0]:
                ok = False
                fails.append("compression")
        modes[mode] = {"pass": ok, "fails": fails}
    a_sim = row.get("family_similarity_A")
    f_sim = row.get("family_similarity_F")
    verdict = "A-like" if a_sim >= thresholds.get("conservative", 55) and f_sim < 30 else (
        "fake_ignition" if f_sim >= 40 or row.get("stop_prone_score", 0) > 60 else "mixed"
    )
    return {
        "symbol": sym,
        "date": date,
        "stage_name": row.get("stage_name"),
        "eps": row.get("explosion_potential"),
        "A_similarity": a_sim,
        "F_similarity": f_sim,
        "stop_prone": row.get("stop_prone_score"),
        "compression_days": row.get("compression_days"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "verdict": verdict,
        "modes": modes,
    }


def pick_final_decision(comparison: dict) -> Tuple[str, str]:
    oos = comparison.get("oos", {})
    base = oos.get("baseline_3_0", {})
    ranked = sorted(
        MODE_SPECS.keys(),
        key=lambda m: (
            oos.get(m, {}).get("net_PF_100bps") or 0,
            oos.get(m, {}).get("median_return") or -99,
            -(oos.get(m, {}).get("stop_hit_ratio") or 100),
        ),
        reverse=True,
    )
    best_mode = ranked[0] if ranked else "baseline_3_0"
    bm = oos.get(best_mode, {})
    if best_mode == "baseline_3_0":
        return "FAIL_FILTER_DOES_NOT_FIX", "No tightened mode beat baseline on OOS"

    if bm.get("trade_count", 0) < MIN_TRADES["oos"]:
        return "FAIL_CURVE_FIT_RISK", f"{best_mode} OOS n={bm.get('trade_count')} < 40"

    improved = (
        (bm.get("net_PF_100bps") or 0) > (base.get("net_PF_100bps") or 0)
        and (bm.get("stop_hit_ratio") or 100) < (base.get("stop_hit_ratio") or 100)
        and (bm.get("median_return") or -99) > (base.get("median_return") or -99)
    )
    if not improved:
        return "FAIL_FILTER_DOES_NOT_FIX", "Tightening did not improve OOS PF, median, and stop_hit vs baseline"

    dominance_fail = (bm.get("top10_symbol_dominance_pct") or 0) >= 35
    lre4 = (
        bm.get("net_PF_100bps", 0) >= 1.3
        and (bm.get("median_return") or 0) > 0
        and (bm.get("stop_hit_ratio") or 100) < 45
        and bm.get("trade_count", 0) >= 40
        and (bm.get("artifact_contribution_pct") or 100) < 10
        and not dominance_fail
        and (bm.get("family_A_purity_pct") or 0) >= 50
        and (bm.get("stage_3_4_pct") or 0) > (bm.get("stage_5_6_pct") or 0)
    )
    if lre4:
        return "PASS_SHADOW_TIGHT_FILTER", f"{best_mode} OOS PF={bm.get('net_PF_100bps')} passes LRE-4.0 gate"

    if dominance_fail:
        return (
            "RESEARCH_EDGE_WEAK_BUT_IMPROVED",
            f"{best_mode} improved PF/stop/median but top-10 dominance {bm.get('top10_symbol_dominance_pct')}% — curve-fit risk",
        )
    return (
        "RESEARCH_EDGE_WEAK_BUT_IMPROVED",
        f"{best_mode} OOS PF={bm.get('net_PF_100bps')} vs baseline {base.get('net_PF_100bps')} — not client-grade",
    )


def render_report(doc: dict) -> str:
    lines = [
        "# LRE-3.1 — Tight Filter Replay & Stop-Prone Audit",
        "",
        f"**Generated:** {doc['at']}",
        f"**Final Decision:** {doc['final_decision']}",
        "",
        doc.get("decision_reason", ""),
        "",
        "## A. Why LRE-3.0 Failed",
        "",
    ]
    for k, v in doc.get("failure_diagnosis", {}).items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "## B. Filter Tightening Results (Full Sample)", ""])
    lines.append("| Mode | Trades | PF@100bps | Median% | WR% | StopHit% | A-Purity% |")
    lines.append("|------|--------|-----------|---------|-----|----------|-----------|")
    for mode, m in doc.get("full_comparison", {}).items():
        lines.append(
            f"| {MODE_SPECS[mode]['label']} | {m.get('trade_count')} | {m.get('net_PF_100bps')} | "
            f"{m.get('median_return')} | {m.get('win_rate')} | {m.get('stop_hit_ratio')} | {m.get('family_A_purity_pct')} |"
        )
    lines.extend(["", "## C. OOS Results (2025–2026)", ""])
    for mode, m in doc.get("oos_comparison", {}).items():
        lines.append(
            f"- **{MODE_SPECS[mode]['label']}**: n={m.get('trade_count')} PF={m.get('net_PF_100bps')} "
            f"median={m.get('median_return')}% stop={m.get('stop_hit_ratio')}%"
        )
    lines.extend(["", "### Latest 6m / 3m", ""])
    for w in ("latest_6m", "latest_3m"):
        lines.append(f"**{w}**")
        for mode, m in doc.get("window_comparison", {}).get(w, {}).items():
            lines.append(f"  - {mode}: n={m.get('trade_count')} PF={m.get('net_PF_100bps')}")
        lines.append("")
    lines.extend(["", "## D. Family A Purity", "", doc.get("family_a_note", ""), "", "## E. Stop-Prone Analysis", ""])
    for mode, note in doc.get("stop_prone_notes", {}).items():
        lines.append(f"- {mode}: {note}")
    lines.extend(["", "## F. Candidate Review", ""])
    for sym, rev in doc.get("candidate_review", {}).items():
        lines.append(f"### {sym}")
        lines.append(f"- verdict: **{rev.get('verdict')}** | eps={rev.get('eps')} A={rev.get('A_similarity')} stop_prone={rev.get('stop_prone')}")
        for mode, md in rev.get("modes", {}).items():
            lines.append(f"  - {mode}: {'PASS' if md.get('pass') else 'FAIL'} {md.get('fails')}")
        lines.append("")
    lines.extend(["", "## G. Answers", ""])
    for i, (q, a) in enumerate(doc.get("answers", {}).items(), 1):
        lines.append(f"{i}. **{q}** — {a}")
    lines.extend([
        "",
        "## LRE-4.0 Gate",
        "",
        f"**Proceed to Rotation Graph Optimization:** {'YES' if doc.get('lre4_gate_pass') else 'NO — monitoring-only'}",
        "",
        "```text",
        "Shadow only. client_path_allowed=False.",
        "```",
    ])
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ LRE-3.1: Filter Tightening + Sanitized Replay ═══", flush=True)

    conn = connect()
    ensure_tables(conn)
    by_sym, meta = load_all_bars(conn)
    fingerprints = load_fingerprints()
    thresholds = calibrate_a_thresholds(conn, by_sym, fingerprints)
    windows = _window_dates(meta["max_date"])
    latest = params.get("trade_date") or meta["max_date"]

    print(f"  calibrate A thresholds n={thresholds.get('calibrated_n')} balanced={thresholds.get('balanced')} conservative={thresholds.get('conservative')}", flush=True)
    print("  building enriched candidates...", flush=True)
    candidates = build_candidates(conn, by_sym, fingerprints, thresholds)
    print(f"    enriched={len(candidates)}", flush=True)

    mode_trades: Dict[str, List[dict]] = {}
    for mode in MODE_SPECS:
        mode_trades[mode] = filter_trades(candidates, mode, thresholds, by_sym)
        print(f"    {mode}: {len(mode_trades[mode])} deduped", flush=True)

    window_metrics: Dict[str, Dict[str, dict]] = {w: {} for w in windows}
    for wname, wrange in windows.items():
        for mode, trades in mode_trades.items():
            sub = [t for t in trades if in_window(t["signal_date"], wrange)]
            m = trade_metrics(sub)
            m["sample_ok"] = m.get("trade_count", 0) >= MIN_TRADES.get(wname, 0)
            window_metrics[wname][mode] = m

    exit_diag = {
        mode: exit_diagnostics(candidates, mode, thresholds, by_sym)
        for mode in ("conservative", "ultra_conservative")
    }

    comparison_table = []
    for mode in MODE_SPECS:
        comparison_table.append({
            "mode": mode,
            "label": MODE_SPECS[mode]["label"],
            "full": window_metrics["full"][mode],
            "oos": window_metrics["oos"][mode],
            "latest_6m": window_metrics["latest_6m"][mode],
            "latest_3m": window_metrics["latest_3m"][mode],
        })

    decision, reason = pick_final_decision(window_metrics)
    base_full = window_metrics["full"]["baseline_3_0"]
    base_oos = window_metrics["oos"]["baseline_3_0"]
    cons_oos = window_metrics["oos"]["conservative"]

    failure_diagnosis = {
        "stop_prone": f"baseline stop_hit={base_full.get('stop_hit_ratio')}% — majority of losses",
        "fake_ignition": f"baseline stage_5_6={base_full.get('stage_5_6_pct')}% leakage into extended moves",
        "artifact": f"artifact contribution {base_full.get('artifact_contribution_pct')}%",
        "low_liquidity": f"low-liq {base_full.get('low_liquidity_contribution_pct')}%",
        "late_entry": f"already-exploded contamination {base_full.get('already_exploded_contamination_pct')}%",
        "wide_filter": "2755 trades @ PF 0.96 — filter too permissive at EPS>=50",
    }

    candidate_review = {}
    for sym in ("OLFI", "HBCO", "EFIC", "EGAS"):
        candidate_review[sym] = review_symbol(conn, by_sym, fingerprints, thresholds, sym, latest)

    forward_candidates = {
        "at": at,
        "trade_date": latest,
        "thresholds": thresholds,
        "modes": {},
    }
    latest_rows = conn.execute(
        "SELECT * FROM lre_daily_scores WHERE trade_date=? ORDER BY explosion_potential DESC LIMIT 40",
        (latest,),
    ).fetchall()
    for mode in MODE_SPECS:
        passed = []
        for r in latest_rows:
            sym = r["symbol"]
            rev = review_symbol(conn, by_sym, fingerprints, thresholds, sym, latest)
            md = rev.get("modes", {}).get(mode, {})
            if md.get("pass"):
                passed.append({
                    "symbol": sym,
                    "eps": r["explosion_potential"],
                    "A_sim": rev.get("A_similarity"),
                    "stop_prone": rev.get("stop_prone"),
                    "verdict": rev.get("verdict"),
                })
        forward_candidates["modes"][mode] = passed[:25]

    answers = {
        "هل الفشل في 3.0 بسبب فلتر واسع؟": (
            f"نعم — {base_full.get('trade_count')} صفقة @ PF {base_full.get('net_PF_100bps')} و stop_hit {base_full.get('stop_hit_ratio')}%"
        ),
        "هل عزل عائلة A يحسن النتائج؟": (
            f"Conservative OOS PF {cons_oos.get('net_PF_100bps')} vs baseline {base_oos.get('net_PF_100bps')} | A-purity {cons_oos.get('family_A_purity_pct')}%"
        ),
        "هل stop-prone score خفّض stop hits؟": (
            f"baseline stop {base_full.get('stop_hit_ratio')}% → conservative {window_metrics['full']['conservative'].get('stop_hit_ratio')}%"
        ),
        "أي mode أفضل؟": max(
            MODE_SPECS,
            key=lambda m: (window_metrics["oos"][m].get("net_PF_100bps") or 0, window_metrics["oos"][m].get("median_return") or -99),
        ),
        "OLFI/HBCO/EFIC/EGAS حقيقيون أم EPS عالي؟": ", ".join(
            f"{s}={candidate_review[s].get('verdict')}" for s in candidate_review
        ),
        "LRE-4.0 أم monitoring-only؟": (
            "LRE-4.0 (conditional)" if decision == "PASS_SHADOW_TIGHT_FILTER"
            else "monitoring-only — tight filter improves edge but not LRE-4.0 gate"
        ),
    }

    lre4_gate_pass = decision == "PASS_SHADOW_TIGHT_FILTER"

    filter_replay_doc = {
        "at": at,
        "phase": "LRE-3.1",
        "invariants": PHASE_INVARIANTS,
        "thresholds": thresholds,
        "windows": {k: list(v) for k, v in windows.items()},
        "by_window": window_metrics,
        "exit_diagnostics": exit_diag,
        "trade_counts_deduped": {m: len(mode_trades[m]) for m in MODE_SPECS},
    }

    sanitized_doc = {
        "at": at, "phase": "LRE-3.1", "filter": "LRE_3_1_TIGHT_FILTER",
        "oos": window_metrics["oos"],
        "latest_6m": window_metrics["latest_6m"],
        "latest_3m": window_metrics["latest_3m"],
        "best_mode_oos": max(MODE_SPECS, key=lambda m: window_metrics["oos"][m].get("net_PF_100bps") or 0),
        "final_decision": decision,
    }

    mode_comparison_doc = {
        "at": at, "comparison": comparison_table,
        "minimum_trades": MIN_TRADES,
        "baseline_3_0_preserved": True,
        "final_decision": decision,
        "decision_reason": reason,
    }

    report_doc = {
        "at": at,
        "final_decision": decision,
        "decision_reason": reason,
        "failure_diagnosis": failure_diagnosis,
        "full_comparison": window_metrics["full"],
        "oos_comparison": window_metrics["oos"],
        "window_comparison": {w: window_metrics[w] for w in ("latest_6m", "latest_3m")},
        "family_a_note": (
            f"Thresholds calibrated from {thresholds.get('calibrated_n')} A events: "
            f"balanced≥{thresholds.get('balanced')} conservative≥{thresholds.get('conservative')} ultra≥{thresholds.get('ultra')}"
        ),
        "stop_prone_notes": {
            m: f"full stop_hit {window_metrics['full'][m].get('stop_hit_ratio')}% (baseline {base_full.get('stop_hit_ratio')}%)"
            for m in MODE_SPECS
        },
        "candidate_review": candidate_review,
        "answers": answers,
        "lre4_gate_pass": lre4_gate_pass,
        "thresholds": thresholds,
    }

    OUTPUTS["filter_replay"].write_text(json.dumps(filter_replay_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["sanitized_replay"].write_text(json.dumps(sanitized_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["mode_comparison"].write_text(json.dumps(mode_comparison_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["forward_candidates"].write_text(json.dumps(forward_candidates, indent=2, default=str), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    print(f"  done. decision={decision} OOS conservative PF={cons_oos.get('net_PF_100bps')}", flush=True)
    return {
        "success": True,
        "final_decision": decision,
        "thresholds": thresholds,
        "oos": window_metrics["oos"],
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
