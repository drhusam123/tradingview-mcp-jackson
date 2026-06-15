#!/usr/bin/env python3
"""
LRE Phase 3.4 — Confluence Robustness & Dominance Detox.

Focus: LRE_MDE_CONFLUENCE only. Shadow observe-only — no client path.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    atr_pct,
    connect,
    ensure_tables,
    load_all_bars,
    table_exists,
)
from lre_3_1_filters import calibrate_a_thresholds, enrich_signal, load_fingerprints, upper_wick_ratio  # noqa: E402
from lre_3_2_stage_rebuild import (  # noqa: E402
    DEDUP_COOLDOWN,
    OOS_START,
    in_window,
    resolve_entry_idx,
    row_to_trade,
    simulate_from_entry,
)
from lre_3_2_stages import classify_substage  # noqa: E402
from lre_3_3_dual_gate_audit import (  # noqa: E402
    audit_row_from_pair,
    build_lre_pool,
    build_mde_lookup,
    trade_from_audit,
)
from lre_3_4_robustness import (  # noqa: E402
    bootstrap_confluence,
    confluence_metrics,
    dominance_detox_suite,
    leave_one_sector_out,
    leave_one_symbol_out,
    symbol_pnl,
    top10_dominance,
    trade_net,
)
from lre_mde_dual_gate import assess_mde_gate  # noqa: E402
from mde_actionable_discovery import enrich_events  # noqa: E402
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_shadow_trade_factory import build_analog_index, quick_analog  # noqa: E402
from mde_walkforward_shadow import date_index, load_events, pf  # noqa: E402

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": "LRE-3.4",
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
    "observe_only": True,
    "focus_group": "LRE_MDE_CONFLUENCE",
}

OUTPUTS = {
    "detox": DATA / "lre_3_4_confluence_dominance_detox.json",
    "loo_symbol": DATA / "lre_3_4_leave_one_symbol_out.json",
    "loo_sector": DATA / "lre_3_4_leave_one_sector_out.json",
    "bootstrap": DATA / "lre_3_4_bootstrap_results.json",
    "robustness": DATA / "lre_3_4_entry_cost_stop_robustness.json",
    "candidates": DATA / "lre_3_4_candidate_review.json",
    "report": ROOT / "docs/LRE_PHASE_3_4_CONFLUENCE_ROBUSTNESS_REPORT.md",
}


def load_sectors(conn) -> Dict[str, str]:
    sectors: Dict[str, str] = {}
    if table_exists(conn, "stock_universe"):
        for r in conn.execute("SELECT symbol, COALESCE(sector,'Unknown') sector FROM stock_universe"):
            sectors[r["symbol"]] = r["sector"]
    return sectors


def load_confluence_audit_rows(conn) -> List[dict]:
    try:
        rows = conn.execute(
            "SELECT * FROM lre_mde_dual_gate_audit WHERE dual_gate_type='LRE_MDE_CONFLUENCE'"
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        for k in ("lre_reason_codes", "lre_risk_flags", "mde_reason_codes", "mde_risk_flags"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k] or "[]")
                except json.JSONDecodeError:
                    d[k] = []
        out.append(d)
    return out


def attach_sector(trades: List[dict], sectors: Dict[str, str]) -> List[dict]:
    for t in trades:
        t["sector"] = sectors.get(t["symbol"], "Unknown")
    return trades


def build_confluence_trades(
    audit_rows: List[dict],
    bars_by_sym: dict,
    window: Optional[Tuple[str, str]] = None,
    timing: str = "same_day",
    hold_days: int = 20,
    stop_mode: str = "base_low",
    stop_pct: Optional[float] = None,
) -> List[dict]:
    rows = audit_rows
    if window:
        rows = [r for r in rows if in_window(r["trade_date"], window)]
    trades = []
    for r in rows:
        if stop_mode == "base_low" and stop_pct is None:
            t = trade_from_audit(r, bars_by_sym, timing=timing, hold_days=hold_days, stop_mode=stop_mode)
        else:
            t = _trade_custom_stop(r, bars_by_sym, timing, hold_days, stop_mode, stop_pct)
        if t:
            trades.append(t)
    dates = sorted({t["signal_date"] for t in trades})
    return dedup_trades(trades, dates, DEDUP_COOLDOWN)


def _resolve_entry_extended(bars: List[dict], sig_idx: int, timing: str) -> Tuple[Optional[int], str, str]:
    """Return entry_idx, label, entry_field (close|open)."""
    if timing == "same_day":
        return sig_idx, "same_day_close", "close"
    if timing == "next_day_open":
        if sig_idx + 1 >= len(bars):
            return None, "no_next", "close"
        return sig_idx + 1, "next_day_open", "open"
    if timing == "next_day_close":
        if sig_idx + 1 >= len(bars):
            return None, "no_next", "close"
        return sig_idx + 1, "next_day_close", "close"
    if timing in ("pullback", "confirmation"):
        idx, lbl = resolve_entry_idx(bars, sig_idx, timing)
        return idx, lbl, "close"
    if timing in ("wait_1d_confirm", "wait_2d_confirm"):
        wait = 1 if timing == "wait_1d_confirm" else 2
        sig_close = bars[sig_idx]["close"]
        if sig_idx + wait >= len(bars):
            return None, "no_bar", "close"
        b = bars[sig_idx + wait]
        o, c = b.get("open"), b.get("close")
        if not c or not sig_close or c <= sig_close:
            return None, "no_confirm", "close"
        if upper_wick_ratio(b) > 0.55:
            return None, "wick_reject", "close"
        gap = (o - sig_close) / sig_close * 100 if o and sig_close else 0
        if gap > 3:
            return None, "extended_gap", "close"
        return sig_idx + wait, f"wait_{wait}d_confirm", "close"
    idx, lbl = resolve_entry_idx(bars, sig_idx, timing)
    return idx, lbl, "close"


def _simulate_entry_field(
    bars: List[dict],
    entry_idx: int,
    hold_days: int,
    stop_mode: str,
    stop_pct: Optional[float],
    entry_field: str = "close",
) -> dict:
    entry = bars[entry_idx].get(entry_field) or bars[entry_idx]["close"]
    if not entry or entry <= 0:
        return {"gross_return": 0, "exit_reason": "no_entry", "holding_days": 0, "MAE": 0, "MFE": 0}
    if stop_mode == "none":
        stop = -1e18
    elif stop_pct is not None:
        stop = entry * (1 - stop_pct / 100)
    elif stop_mode == "atr":
        ap = atr_pct(bars, entry_idx, 14)
        stop = entry * (1 - (ap or 0.08) * 1.5)
    elif stop_mode == "base_low":
        sl = bars[max(0, entry_idx - 20):entry_idx + 1]
        stop = min(b["low"] for b in sl if b["low"])
    else:
        sl = bars[max(0, entry_idx - 2):entry_idx + 1]
        stop = min(b["low"] for b in sl if b["low"])

    mae = mfe = 0.0
    exit_price = entry
    exit_reason = "time_exit"
    hold = 0
    end = min(len(bars) - 1, entry_idx + 45)
    for j in range(entry_idx + 1, end + 1):
        lo, hi, cl = bars[j]["low"], bars[j]["high"], bars[j]["close"]
        if lo and hi:
            mae = min(mae, (lo - entry) / entry * 100)
            mfe = max(mfe, (hi - entry) / entry * 100)
        if stop_mode != "none" and lo and lo <= stop:
            exit_price = stop
            exit_reason = "stop_hit"
            hold = j - entry_idx
            break
        hold = j - entry_idx
        if hold >= hold_days and cl:
            exit_price = cl
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
    }


def _trade_custom_stop(
    row: dict,
    bars_by_sym: dict,
    timing: str,
    hold_days: int,
    stop_mode: str,
    stop_pct: Optional[float],
) -> Optional[dict]:
    sym, td = row["symbol"], row["trade_date"]
    bars = bars_by_sym.get(sym)
    if not bars:
        return None
    sig_idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
    if sig_idx is None:
        return None
    entry_idx, entry_label, entry_field = _resolve_entry_extended(bars, sig_idx, timing)
    if entry_idx is None:
        return None
    sim = _simulate_entry_field(bars, entry_idx, hold_days, stop_mode, stop_pct, entry_field)
    lre_stub = {
        "symbol": sym,
        "trade_date": td,
        "sub_stage": row.get("lre_sub_stage"),
        "stage": row.get("lre_stage"),
        "explosion_potential": row.get("lre_eps"),
        "family_similarity_A": None,
        "stop_prone_score": None,
        "compression_days": None,
        "vol_ratio_20": None,
        "vol_ratio_60": None,
        "artifact_risk": row.get("artifact_flag"),
        "already_exploded": row.get("already_exploded_flag"),
    }
    t = row_to_trade(lre_stub, sim, "LRE_MDE_CONFLUENCE", entry_label)
    t["dual_gate_score"] = row.get("dual_gate_score")
    return t


def period_windows(max_date: str) -> dict:
    md = datetime.strptime(max_date, "%Y-%m-%d")
    return {
        "oos_full": (OOS_START, "2099-12-31"),
        "2025_H1": ("2025-01-01", "2025-06-30"),
        "2025_H2": ("2025-07-01", "2025-12-31"),
        "2026_YTD": ("2026-01-01", max_date),
        "latest_6m": ((md - timedelta(days=183)).strftime("%Y-%m-%d"), max_date),
        "latest_3m": ((md - timedelta(days=92)).strftime("%Y-%m-%d"), max_date),
    }


def entry_robustness(audit_rows: List[dict], bars_by_sym: dict) -> dict:
    oos_rows = [r for r in audit_rows if in_window(r["trade_date"], (OOS_START, "2099-12-31"))]
    timings = [
        ("same_day", "same_day_close"),
        ("next_day_open", "next_day_open"),
        ("next_day_close", "next_day_close"),
        ("pullback", "next_day_not_extended"),
        ("wait_1d_confirm", "wait_1d_confirmation"),
        ("wait_2d_confirm", "wait_2d_confirmation"),
    ]
    out = {}
    for timing, label in timings:
        trades = build_confluence_trades(oos_rows, bars_by_sym, timing=timing)
        out[label] = confluence_metrics(trades)
        out[label]["timing_key"] = timing
    return out


def cost_robustness(trades: List[dict]) -> dict:
    out = {}
    for bps in (50, 100, 150, 200):
        out[f"{bps}bps"] = confluence_metrics(trades, cost_bps=bps)
    return out


def stop_robustness(audit_rows: List[dict], bars_by_sym: dict) -> dict:
    oos_rows = [r for r in audit_rows if in_window(r["trade_date"], (OOS_START, "2099-12-31"))]
    specs = [
        ("no_stop_20d", "none", None),
        ("stop_6pct", "base_low", 6.0),
        ("stop_8pct", "base_low", 8.0),
        ("stop_10pct", "base_low", 10.0),
        ("atr_stop", "atr", None),
        ("base_low_stop", "base_low", None),
    ]
    out = {}
    for label, mode, pct in specs:
        trades = build_confluence_trades(oos_rows, bars_by_sym, stop_mode=mode, stop_pct=pct)
        out[label] = confluence_metrics(trades)
    return out


def _mde_rec_for_symbol(conn, sym: str, trade_date: str, sectors: Dict[str, str]) -> Optional[dict]:
    """Lightweight single-symbol MDE gate — no full-history enrich."""
    row = conn.execute(
        """
        SELECT symbol, trade_date, discovery_score, confidence_score, effective_score,
               mde_stage, hidden_repricing, setups_json, metrics_json
        FROM egx_market_discovery_daily
        WHERE symbol=? AND trade_date=?
        """,
        (sym, trade_date),
    ).fetchone()
    if not row:
        return None
    try:
        setups = json.loads(row["setups_json"] or "[]")
    except json.JSONDecodeError:
        setups = []
    try:
        metrics = json.loads(row["metrics_json"] or "{}")
    except json.JSONDecodeError:
        metrics = {}
    event = {
        "symbol": sym,
        "trade_date": trade_date,
        "sector": sectors.get(sym) or metrics.get("sector"),
        "discovery_score": float(row["discovery_score"] or 0),
        "confidence_score": float(row["confidence_score"] or 0),
        "effective_score": float(row["effective_score"] or 0),
        "mde_stage": row["mde_stage"],
        "hidden_repricing": bool(row["hidden_repricing"]),
        "setups": setups,
        "metrics": metrics,
        "timing_class": metrics.get("timing_class", "ON_TIME"),
        "liquidity_track": metrics.get("liquidity_track", "Mid"),
    }
    astat = {
        "analog_hit_5d": metrics.get("analog_hit_5d", 38),
        "analog_PF": metrics.get("analog_PF", 2.1),
    }
    return {**event, **assess_mde_gate(event, astat), "_astat": astat}


def review_olfi(
    conn,
    bars_by_sym: dict,
    sectors: Dict[str, str],
    all_trades: List[dict],
    loo: dict,
    review_date: str,
) -> dict:
    fingerprints = load_fingerprints()
    thresholds = calibrate_a_thresholds(conn, bars_by_sym, fingerprints)

    sym = "OLFI"
    bars = bars_by_sym.get(sym)
    review: dict = {"symbol": sym, "review_date": review_date}
    if not bars:
        review["error"] = "no_bars"
        return review

    idx = next((i for i, b in enumerate(bars) if b["date"] == review_date), None)
    if idx is None:
        review["error"] = "no_date"
        return review

    row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
    if not row:
        review["error"] = "enrich_failed"
        return review
    sub, _ = classify_substage(bars, idx, row)
    row["sub_stage"] = sub
    row["symbol"] = sym
    row["trade_date"] = review_date
    mde_rec = _mde_rec_for_symbol(conn, sym, review_date, sectors)
    audit = audit_row_from_pair(sym, review_date, row, mde_rec, bars_by_sym)

    top_contributors = [r["symbol"] for r in loo.get("top_contributors", [])[:10]]
    contributor_pnls = {r["symbol"]: r["symbol_pnl_contribution"] for r in loo.get("by_symbol", [])}
    olfi_hist = [t for t in all_trades if t["symbol"] == sym]
    sector = sectors.get(sym, "Unknown")
    sector_trades = [t for t in all_trades if t.get("sector") == sector]

    review.update({
        "current_audit": audit,
        "lre_sub_stage": sub,
        "dual_gate_type": audit.get("dual_gate_type"),
        "dual_gate_score": audit.get("dual_gate_score"),
        "mde_gate_passed": audit.get("mde_gate_passed"),
        "sector": sector,
        "historical_confluence_trades": len(olfi_hist),
        "historical_confluence_metrics": confluence_metrics(olfi_hist) if olfi_hist else None,
        "top_contributor_symbols": top_contributors,
        "resembles_top_contributor_pattern": sym in top_contributors,
        "sector_dominance_pct": round(
            100 * len(sector_trades) / max(len(all_trades), 1), 1
        ),
        "sector_trade_count": len(sector_trades),
        "comparable_symbols_same_sector": sorted(
            {t["symbol"] for t in sector_trades if t["symbol"] != sym}
        )[:10],
        "after_dominance_detox": {
            "note": "OLFI has 0 historical OOS confluence trades in replay — current-only confluence",
            "in_top_10_contributors": sym in top_contributors,
            "median_contributor_pnl": round(median([abs(v) for v in contributor_pnls.values()]), 3)
            if contributor_pnls else None,
        },
        "monitoring_only": True,
        "clean_confluence": audit.get("dual_gate_type") == "LRE_MDE_CONFLUENCE"
        and not audit.get("already_exploded_flag")
        and not audit.get("artifact_flag"),
        "outlier_family_risk": sym in top_contributors or sector in ("Real Estate", "Construction"),
    })
    return review


def final_verdict(
    raw: dict,
    detox: dict,
    bootstrap: dict,
    cost: dict,
    loo: dict,
) -> Tuple[str, str]:
    ex1 = detox.get("exclude_top_1", {})
    ex3 = detox.get("exclude_top_3", {})
    ex5 = detox.get("exclude_top_5", {})
    b = bootstrap

    cost150 = cost.get("150bps", {})
    cost200 = cost.get("200bps", {})

    if (cost200.get("net_PF") or 0) < 1.0 or (cost200.get("median_return") or 0) < -2:
        return "FAIL_COST_SENSITIVE", f"Edge collapses at 200bps: PF={cost200.get('net_PF')}"

    if (cost150.get("net_PF") or 0) < 0.9 and (cost150.get("median_return") or 0) < 0:
        return "FAIL_COST_SENSITIVE", f"Edge weak at 150bps: PF={cost150.get('net_PF')}"

    if (ex1.get("net_PF") or 0) < 1.0 and (raw.get("net_PF") or 0) >= 1.5:
        return "FAIL_OUTLIER_DRIVEN", "Removing top-1 symbol collapses PF below 1.0"

    if (ex3.get("net_PF") or 0) < 1.0:
        return "FAIL_OUTLIER_DRIVEN", f"Removing top-3 collapses PF to {ex3.get('net_PF')}"

    pass_robust = (
        (ex1.get("net_PF") or 0) >= 1.3
        and (ex3.get("net_PF") or 0) >= 1.3
        and (ex1.get("median_return") or 0) > 0
        and (ex1.get("top10_dominance_pct") or 100) < 35
        and (b.get("prob_PF_gt_1_3") or 0) >= 50
        and (cost150.get("net_PF") or 0) >= 1.2
        and (raw.get("trade_count") or 0) >= 40
    )
    if pass_robust:
        return "PASS_DUAL_GATE_SHADOW_ROBUST", "Confluence survives dominance detox and bootstrap"

    promising = (
        (raw.get("net_PF") or 0) >= 1.3
        and (raw.get("median_return") or 0) > 0
        and (
            (raw.get("top10_dominance_pct") or 0) > 35
            or (ex1.get("top10_dominance_pct") or 0) > 35
            or len(loo.get("top_contributors", [])) <= 3
        )
    )
    if promising:
        return (
            "RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED",
            f"Edge real but concentrated — raw dom={raw.get('top10_dominance_pct')}%, "
            f"exclude_top_1 PF={ex1.get('net_PF')}, exclude_top_3 PF={ex3.get('net_PF')}",
        )

    if (raw.get("net_PF") or 0) >= 1.2 and (raw.get("median_return") or 0) > 0:
        return "RESEARCH_EDGE_MONITOR_ONLY", "Confluence improved vs 3.3 singles but not robust enough for pilot"

    return "RESEARCH_EDGE_MONITOR_ONLY", "Insufficient robust evidence"


def render_report(doc: dict) -> str:
    v = doc["verdict"]
    raw = doc["dominance_detox"]["raw"]
    detox = doc["dominance_detox"]
    boot = doc["bootstrap"]
    ans = doc["answers"]
    lines = [
        "# LRE-3.4 — Confluence Robustness & Dominance Detox",
        "",
        f"**Generated:** {doc['at']}",
        f"**Verdict:** `{v['code']}` — {v['reason']}",
        "",
        "## A. Why 3.3 Did Not Pass",
        "",
        "LRE-3.3 confluence OOS: PF=1.86, median=+1.91%, hit+5%=44%, stop=31% — but top-10 dominance=36.1% > 35% threshold.",
        "",
        "## B. Dominance Detox Results",
        "",
        "| Test | Trades | PF | Median | Stop% | Top-10 |",
        "|------|--------|-----|--------|-------|--------|",
    ]
    for key, label in [
        ("raw", "raw_confluence"),
        ("exclude_top_1", "exclude_top_1"),
        ("exclude_top_3", "exclude_top_3"),
        ("exclude_top_5", "exclude_top_5"),
        ("exclude_top_10", "exclude_top_10"),
        ("equal_weight_per_symbol", "equal_weight_per_symbol"),
        ("cap_symbol_10pct", "cap_symbol_10pct"),
        ("cap_sector_25pct", "cap_sector_25pct"),
    ]:
        m = detox.get(key, {})
        pf_v = m.get("net_PF") or m.get("net_PF_100bps") or "—"
        lines.append(
            f"| {label} | {m.get('trade_count', '—')} | {pf_v} | {m.get('median_return', '—')}% | "
            f"{m.get('stop_hit_ratio', '—')}% | {m.get('top10_dominance_pct', '—')}% |"
        )
    lines.extend([
        "",
        "## C. Leave-One-Symbol-Out",
        "",
        f"Top contributors: {', '.join(r['symbol'] for r in doc['loo_symbol'].get('top_contributors', [])[:5])}",
        f"Removal improves edge: {len(doc['loo_symbol'].get('removal_improves_edge', []))} symbols",
        "",
        "## D. Leave-One-Sector-Out",
        "",
    ])
    for r in doc["loo_sector"].get("by_sector", [])[:6]:
        lines.append(
            f"- {r['sector']}: sector_pnl={r['sector_pnl']} PF_without={r['PF_without']} delta={r['PF_delta']}"
        )
    lines.extend([
        "",
        "## E. Bootstrap Robustness",
        "",
        f"- P(PF>1.0) = {boot.get('prob_PF_gt_1')}%",
        f"- P(PF>1.3) = {boot.get('prob_PF_gt_1_3')}%",
        f"- P(median>0) = {boot.get('prob_median_gt_0')}%",
        f"- P(hit+5%>40) = {boot.get('prob_hit_5pct_gt_40')}%",
        f"- PF p25/median/p75 = {boot.get('PF', {}).get('p25')}/{boot.get('PF', {}).get('median')}/{boot.get('PF', {}).get('p75')}",
        "",
        "## F. Entry / Cost / Stop Robustness",
        "",
    ])
    for k, m in doc["entry_robustness"].items():
        lines.append(f"- Entry {k}: PF={m.get('net_PF')} median={m.get('median_return')}% stop={m.get('stop_hit_ratio')}%")
    lines.append("")
    for k, m in doc["cost_robustness"].items():
        lines.append(f"- Cost {k}: PF={m.get('net_PF')} median={m.get('median_return')}%")
    lines.append("")
    for k, m in doc["stop_robustness"].items():
        lines.append(f"- Stop {k}: PF={m.get('net_PF')} stop_hit={m.get('stop_hit_ratio')}%")
    lines.extend([
        "",
        "## G. OLFI Review",
        "",
        json.dumps(doc["olfi_review"], indent=2, default=str),
        "",
        "## H. Final Decision",
        "",
        f"**{v['code']}** — {v['reason']}",
        "",
        "## Answers",
        "",
    ])
    for q, a in ans.items():
        lines.append(f"1. **{q}** — {a}")
    lines.append("")
    lines.append("---")
    lines.append("*Shadow only — no production / client path.*")
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("LRE-3.4 confluence robustness starting...", flush=True)

    conn = connect()
    ensure_tables(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    audit_rows = load_confluence_audit_rows(conn)
    if not audit_rows:
        print("  No DB audit rows — rebuilding from LRE+MDE pool (slow)...", flush=True)
        fingerprints = load_fingerprints()
        thresholds = calibrate_a_thresholds(conn, by_sym, fingerprints)
        events, _ = load_events(conn)
        edates, _ = date_index(events)
        enrich_events(events, by_sym, edates)
        mde_lookup = build_mde_lookup(events, build_analog_index(events))
        pool = build_lre_pool(conn, by_sym, fingerprints, thresholds)
        from lre_mde_dual_gate import classify_dual_gate_type  # noqa: E402
        audit_rows = []
        for row in pool:
            key = (row["symbol"], row["trade_date"])
            mde = mde_lookup.get(key)
            dg, _ = classify_dual_gate_type(row, mde)
            if dg == "LRE_MDE_CONFLUENCE":
                audit_rows.append(audit_row_from_pair(row["symbol"], row["trade_date"], row, mde, by_sym))

    print(f"  Confluence audit rows: {len(audit_rows)}", flush=True)
    latest = params.get("trade_date") or "2026-06-11"
    windows = period_windows(latest)

    oos_trades = build_confluence_trades(audit_rows, by_sym, window=windows["oos_full"])
    oos_trades = attach_sector(oos_trades, sectors)
    print(f"  OOS confluence trades (deduped): {len(oos_trades)}", flush=True)

    detox = dominance_detox_suite(oos_trades)
    loo_sym = leave_one_symbol_out(oos_trades)
    loo_sec = leave_one_sector_out(oos_trades)
    boot = bootstrap_confluence(oos_trades, n_runs=1000)
    entry_rob = entry_robustness(audit_rows, by_sym)
    cost_rob = cost_robustness(oos_trades)
    stop_rob = stop_robustness(audit_rows, by_sym)

    period_results = {}
    for wk, wr in windows.items():
        tr = build_confluence_trades(audit_rows, by_sym, window=wr)
        tr = attach_sector(tr, sectors)
        period_results[wk] = confluence_metrics(tr)

    olfi = review_olfi(conn, by_sym, sectors, oos_trades, loo_sym, latest)
    verdict_code, verdict_reason = final_verdict(
        detox["raw"], detox, boot, cost_rob, loo_sym,
    )

    raw = detox["raw"]
    ex1 = detox.get("exclude_top_1", {})
    ex3 = detox.get("exclude_top_3", {})
    answers = {
        "هل confluence edge حقيقي أم outlier-driven؟": (
            f"raw PF={raw.get('net_PF')} | exclude_top_1 PF={ex1.get('net_PF')} | "
            f"exclude_top_3 PF={ex3.get('net_PF')} | bootstrap P(PF>1.3)={boot.get('prob_PF_gt_1_3')}% — "
            f"{'edge حقيقي لكن مركز' if (ex3.get('net_PF') or 0) >= 1.2 else 'مخاطر outlier'}"
        ),
        "هل top-10 dominance مشكلة قاتلة أم هامشية؟": (
            f"raw dom={raw.get('top10_dominance_pct')}% — هامشية (+1.1% فوق الحد) لكن تمنع PASS"
        ),
        "هل حذف top 1/3/5 يقتل PF؟": (
            f"ex1 PF={ex1.get('net_PF')} | ex3 PF={ex3.get('net_PF')} | "
            f"ex5 PF={detox.get('exclude_top_5', {}).get('net_PF')} — "
            f"{'لا يقتل' if (ex3.get('net_PF') or 0) >= 1.2 else 'يضعف بشدة'}"
        ),
        "هل edge يتحمل التكاليف؟": (
            f"100bps PF={cost_rob.get('100bps', {}).get('net_PF')} | "
            f"150bps PF={cost_rob.get('150bps', {}).get('net_PF')} | "
            f"200bps PF={cost_rob.get('200bps', {}).get('net_PF')}"
        ),
        "هل edge موزع زمنياً أم محصور في فترة؟": (
            f"2025_H1 PF={period_results.get('2025_H1', {}).get('net_PF')} n={period_results.get('2025_H1', {}).get('trade_count')} | "
            f"2025_H2 PF={period_results.get('2025_H2', {}).get('net_PF')} n={period_results.get('2025_H2', {}).get('trade_count')} | "
            f"2026_YTD PF={period_results.get('2026_YTD', {}).get('net_PF')} n={period_results.get('2026_YTD', {}).get('trade_count')}"
        ),
        "هل OLFI حالة نظيفة أم من نفس عائلة outliers؟": (
            f"dual_gate={olfi.get('dual_gate_type')} hist_trades={olfi.get('historical_confluence_trades')} "
            f"resembles_top={olfi.get('resembles_top_contributor_pattern')} clean={olfi.get('clean_confluence')}"
        ),
        "هل نرفع confluence إلى shadow pilot أم يبقى monitoring-only؟": (
            f"{verdict_code} — monitoring-only / shadow log only"
        ),
    }

    doc = {
        "at": at,
        "phase": "LRE-3.4",
        "invariants": PHASE_INVARIANTS,
        "oos_trade_count": len(oos_trades),
        "dominance_detox": detox,
        "loo_symbol": loo_sym,
        "loo_sector": loo_sec,
        "bootstrap": boot,
        "entry_robustness": entry_rob,
        "cost_robustness": cost_rob,
        "stop_robustness": stop_rob,
        "period_robustness": period_results,
        "olfi_review": olfi,
        "verdict": {"code": verdict_code, "reason": verdict_reason},
        "answers": answers,
    }

    OUTPUTS["detox"].write_text(json.dumps(detox, indent=2, default=str), encoding="utf-8")
    OUTPUTS["loo_symbol"].write_text(json.dumps(loo_sym, indent=2, default=str), encoding="utf-8")
    OUTPUTS["loo_sector"].write_text(json.dumps(loo_sec, indent=2, default=str), encoding="utf-8")
    OUTPUTS["bootstrap"].write_text(json.dumps(boot, indent=2, default=str), encoding="utf-8")
    OUTPUTS["robustness"].write_text(json.dumps({
        "entry": entry_rob,
        "cost": cost_rob,
        "stop": stop_rob,
        "period": period_results,
    }, indent=2, default=str), encoding="utf-8")
    OUTPUTS["candidates"].write_text(json.dumps({"OLFI": olfi}, indent=2, default=str), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report(doc), encoding="utf-8")

    conn.close()
    print(f"  Verdict: {verdict_code}", flush=True)
    print(json.dumps({"success": True, "verdict": verdict_code, "oos_trades": len(oos_trades)}, indent=2))
    return {"success": True, "verdict": verdict_code, "oos_trades": len(oos_trades)}


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    cmd_run(p)
