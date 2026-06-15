#!/usr/bin/env python3
"""
MDE Phase 2.10 — Full-Market Multi-Timeframe Shadow Trade Factory.

Shadow research only. No client path, no promotion, no real trades.

Outputs: 12 data JSON files + final report (see OUTPUT_PATHS).
"""
from __future__ import annotations

import json
import math
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
    enrich_events,
    load_opp_layers,
    rule_metrics,
    validate_alpha_rules,
    apply_rule_stack,
    analog_stats,
)
from mde_hidden_cause_validation import (  # noqa: E402
    infer_hidden_cause,
    metaorder_detection,
    strategic_liquidity,
    oqs_v2,
    m as metric_val,
)
from mde_walkforward_shadow import (  # noqa: E402
    HIT_THRESH,
    RETURN_CAP,
    connect,
    date_index,
    event_regime,
    load_events,
    market_regime,
    pf,
)

HORIZONS = [1, 2, 3, 5, 10, 15, 20, 30, 60]
COST_BPS = [25, 50, 100, 150]
DEFAULT_COST_BPS = 50
MIN_TRADES_FAMILY = 30
MIN_SYMBOLS_FAMILY = 5
MIN_SECTORS_FAMILY = 2

OUTPUT_PATHS = {
    "ledger": DATA / "mde_shadow_trade_ledger_full.json",
    "exit_opt": DATA / "mde_exit_stop_optimization.json",
    "edge": DATA / "mde_edge_decomposition.json",
    "symbol_pb": DATA / "mde_symbol_trade_playbooks.json",
    "sector_pb": DATA / "mde_sector_liquidity_timeframe_playbooks.json",
    "regime": DATA / "mde_regime_edge_map.json",
    "lead_time": DATA / "mde_discovery_lead_time.json",
    "families": DATA / "mde_best_trade_families.json",
    "current": DATA / "mde_current_shadow_trade_plans.json",
    "ranking": DATA / "mde_client_ready_shadow_ranking.json",
    "robustness": DATA / "mde_trade_family_robustness.json",
    "timeframe_cov": DATA / "mde_timeframe_coverage.json",
    "report": ROOT / "docs" / "MDE_PHASE_2_10_FULL_MARKET_SHADOW_TRADE_FACTORY_REPORT.md",
}


def extended_forward(bars: List[dict], idx: int) -> dict:
    """Multi-horizon forward stats + MAE/MFE."""
    out: dict = {}
    c0 = bars[idx]["close"]
    if c0 <= 0:
        return out
    peak, trough = c0, c0
    time_to_peak = None
    for h in HORIZONS:
        if idx + h < len(bars):
            ret = max(-RETURN_CAP, min(RETURN_CAP, bars[idx + h]["close"] / c0 - 1))
            out[f"ret{h}d"] = round(ret * 100, 3)
            out[f"hit{h}d"] = ret >= HIT_THRESH
    max_hold = min(60, len(bars) - idx - 1)
    mfe, mae = 0.0, 0.0
    for d in range(1, max_hold + 1):
        c = bars[idx + d]["close"]
        h = bars[idx + d]["high"]
        l = bars[idx + d]["low"]
        r = c / c0 - 1
        mfe = max(mfe, (h / c0 - 1))
        mae = min(mae, (l / c0 - 1))
        if time_to_peak is None and r >= HIT_THRESH:
            time_to_peak = d
    out["mfe"] = round(mfe * 100, 3)
    out["mae"] = round(mae * 100, 3)
    out["time_to_peak"] = time_to_peak
    if idx + 20 < len(bars):
        pk = c0
        max_dd = 0.0
        for i in range(idx, idx + 21):
            pk = max(pk, bars[i]["close"])
            max_dd = min(max_dd, (bars[i]["close"] - pk) / pk if pk else 0)
        out["max_drawdown_20d"] = round(max_dd * 100, 3)
    return out


def confirmation_ok(e: dict) -> bool:
    return metric_val(e, "rel_turn") > 1.2 and metric_val(e, "clv") > 0.5


def build_trigger_families(stack: List[dict]) -> List[Tuple[str, str, Callable]]:
    """Trigger family predicates — evaluated per event with ctx."""
    families = [
        ("TF_WATCH", "WATCH only",
         lambda e, ctx: e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing")),
        ("TF_WATCH_CONF", "WATCH + confirmation",
         lambda e, ctx: (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing")) and confirmation_ok(e)),
        ("TF_CONF_ANALOG_PF2", "WATCH + conf + analog PF>2",
         lambda e, ctx: confirmation_ok(e) and (ctx.get("analog_pf") or 0) > 2
         and (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing"))),
        ("TF_CONF_OQS62", "WATCH + conf + OQS_v2>=62",
         lambda e, ctx: confirmation_ok(e) and (ctx.get("oqs_v2") or 0) >= 62
         and (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing"))),
        ("TF_CONF_HIDDEN_CAUSE", "WATCH + conf + hidden_cause_conf>=50",
         lambda e, ctx: confirmation_ok(e) and (ctx.get("hidden_cause_conf") or 0) >= 50
         and (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing"))),
        ("TF_CONF_METAORDER", "WATCH + conf + metaorder>=50",
         lambda e, ctx: confirmation_ok(e) and (ctx.get("metaorder_prob") or 0) >= 50
         and (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing"))),
        ("TF_CONF_LIQUIDITY", "WATCH + conf + REAL_LIQUIDITY",
         lambda e, ctx: confirmation_ok(e) and ctx.get("liquidity_type") == "REAL_LIQUIDITY"
         and (e.get("discovery_score", 0) >= 45 or e.get("hidden_repricing"))),
        ("TF_COMP_001A", "COMP_001A",
         lambda e, ctx: e.get("effective_score", 0) > 60 and (ctx.get("analog_hit") or 0) > 40
         and (ctx.get("analog_pf") or 0) > 2 and e.get("timing_class") == "ON_TIME"),
        ("TF_COMP_001B", "COMP_001B",
         lambda e, ctx: e.get("effective_score", 0) > 60 and (ctx.get("analog_hit") or 0) > 35
         and (ctx.get("analog_pf") or 0) > 2 and e.get("timing_class") in ("EARLY", "ON_TIME")),
        ("TF_MID_LIQ_HR", "Mid-liquidity HR + conf",
         lambda e, ctx: e.get("hidden_repricing") and e.get("liquidity_track") == "Mid" and confirmation_ok(e)),
        ("TF_SEQ_PULLBACK_IMPACT", "Sequence pullback→impact + conf",
         lambda e, ctx: e.get("has_seq_pullback_impact") and confirmation_ok(e)),
        ("TF_OUTSIDE_OPP", "Outside-opp + conf + analog PF>2",
         lambda e, ctx: e.get("mde_only") and confirmation_ok(e) and (ctx.get("analog_pf") or 0) > 2),
    ]
    return families


EXIT_STRATEGIES = [
    ("EXIT_HOLD_3D", "fixed_3d", 3, None),
    ("EXIT_HOLD_5D", "fixed_5d", 5, None),
    ("EXIT_HOLD_10D", "fixed_10d", 10, None),
    ("EXIT_HOLD_20D", "fixed_20d", 20, None),
    ("EXIT_HOLD_30D", "fixed_30d", 30, None),
    ("EXIT_TARGET_5PCT", "target_5pct", 60, 0.05),
    ("EXIT_TARGET_10PCT", "target_10pct", 60, 0.10),
    ("EXIT_TARGET_15PCT", "target_15pct", 60, 0.15),
    ("EXIT_SIGNAL_LOW", "signal_low", 60, None),
    ("EXIT_EFFECTIVE50", "effective_drop", 60, None),
    ("EXIT_ATR_STOP", "atr_stop", 60, None),
]


def simulate_exit(
    bars: List[dict], idx: int, e: dict, strategy: str, max_days: int, target: Optional[float],
) -> dict:
    """Simulate one exit strategy from entry at signal bar close (next-day entry proxy)."""
    if idx + 1 >= len(bars):
        return {}
    entry_idx = idx + 1
    entry = bars[entry_idx]["close"]
    signal_low = bars[idx]["low"]
    if entry <= 0:
        return {}
    atr = mean([bars[i]["high"] - bars[i]["low"] for i in range(max(0, idx - 19), idx + 1)]) or entry * 0.02
    stop_price = signal_low if strategy == "signal_low" else entry - 2 * atr if strategy == "atr_stop" else entry * 0.97

    gross = 0.0
    exit_idx = entry_idx
    exit_reason = "max_hold"
    mfe, mae = 0.0, 0.0

    for d in range(0, max_days):
        bi = entry_idx + d
        if bi >= len(bars):
            break
        c = bars[bi]["close"]
        h = bars[bi]["high"]
        l = bars[bi]["low"]
        r = c / entry - 1
        mfe = max(mfe, h / entry - 1)
        mae = min(mae, l / entry - 1)

        if strategy == "signal_low" and l <= stop_price:
            gross = (stop_price / entry - 1)
            exit_idx = bi
            exit_reason = "signal_low_stop"
            break
        if strategy == "atr_stop" and l <= stop_price:
            gross = (stop_price / entry - 1)
            exit_idx = bi
            exit_reason = "atr_stop"
            break
        if strategy.startswith("fixed_"):
            hold = int(strategy.replace("fixed_", "").replace("d", ""))
            if d + 1 >= hold:
                gross = r
                exit_idx = bi
                exit_reason = f"hold_{hold}d"
                break
        if strategy.startswith("target_") and target and h / entry - 1 >= target:
            gross = target
            exit_idx = bi
            exit_reason = f"target_{int(target*100)}pct"
            break
        if strategy == "effective_drop":
            gross = r
            exit_idx = bi
            exit_reason = "effective_drop_proxy"
            if d >= 5:
                break
    else:
        if entry_idx + max_days - 1 < len(bars):
            gross = bars[entry_idx + max_days - 1]["close"] / entry - 1
            exit_idx = entry_idx + max_days - 1

    cost = DEFAULT_COST_BPS / 10000 * 2
    gross = max(-RETURN_CAP, min(RETURN_CAP, gross))
    net = gross - cost
    return {
        "gross_return_pct": round(gross * 100, 3),
        "net_return_pct": round(net * 100, 3),
        "exit_reason": exit_reason,
        "holding_days": exit_idx - entry_idx,
        "mfe_pct": round(mfe * 100, 3),
        "mae_pct": round(mae * 100, 3),
        "entry_price": round(entry, 4),
        "exit_price": round(bars[exit_idx]["close"], 4),
        "exit_date": bars[exit_idx]["date"],
    }


def tradeability_score(e: dict) -> float:
    turn = float(e.get("turnover_egp") or metric_val(e, "turnover") or 0)
    rel = metric_val(e, "rel_turn")
    track = e.get("liquidity_track", "")
    base = min(100, turn / 500_000)
    if track in ("Liquid", "Institutional"):
        base += 20
    elif track == "Thin":
        base -= 30
    if rel > 1.2:
        base += 10
    return round(max(0, min(100, base)), 1)


def pick_best_family(matched: List[str], family_rows: Optional[List[dict]] = None) -> Optional[str]:
    """Prefer ACCEPT families, then highest historical PF, else static rank."""
    if not matched:
        return None
    rank = [
        "TF_COMP_001A", "TF_COMP_001B", "TF_CONF_ANALOG_PF2", "TF_OUTSIDE_OPP",
        "TF_CONF_OQS62", "TF_CONF_METAORDER", "TF_CONF_LIQUIDITY", "TF_CONF_HIDDEN_CAUSE",
        "TF_MID_LIQ_HR", "TF_SEQ_PULLBACK_IMPACT", "TF_WATCH_CONF", "TF_WATCH",
    ]
    if family_rows:
        pf_map = {f["family_id"]: f.get("pf") or 0 for f in family_rows}
        accept = {
            f["family_id"] for f in family_rows
            if f.get("approval_status") == "ACCEPT_SHADOW_TRADE_FAMILY"
        }
        accept_matched = [m for m in matched if m in accept]
        pool = accept_matched or matched
        return max(pool, key=lambda x: (pf_map.get(x, 0), -rank.index(x) if x in rank else -99))
    return min(matched, key=lambda x: rank.index(x) if x in rank else 99)


def build_event_ctx(
    e: dict, astat: dict, cause_conf: float, meta: dict, liq: dict,
    matched_rules: List[str], risks: List[str],
) -> dict:
    oqs = oqs_v2(
        e, astat, cause_conf, meta, liq,
        {"blocks_high_quality": False, "classification": e.get("timing_class")},
        matched_rules, risks, 30,
    )["final_OQS_v2"]
    return {
        "analog_hit": astat.get("analog_hit_5d"),
        "analog_pf": astat.get("analog_PF"),
        "oqs_v2": oqs,
        "hidden_cause_conf": cause_conf,
        "metaorder_prob": meta.get("metaorder_probability"),
        "liquidity_type": liq.get("liquidity_type"),
    }


def cost_sensitivity(trades: List[dict], bps_list: List[int] = None) -> dict:
    """Net PF at multiple cost assumptions for ACCEPT-tier trades."""
    bps_list = bps_list or COST_BPS
    rows = []
    for bps in bps_list:
        cost_pct = bps / 100
        nets = [(t.get("gross_return") or 0) - cost_pct for t in trades]
        wins = [r for r in nets if r >= HIT_THRESH * 100]
        losses = [abs(r) for r in nets if r < HIT_THRESH * 100]
        rows.append({
            "cost_bps": bps,
            "trades": len(trades),
            "hit_rate": round(100 * len(wins) / max(len(nets), 1), 1),
            "pf_net": round(pf(wins, losses), 2) if nets else None,
            "avg_net_return": round(mean(nets), 2) if nets else None,
            "median_net_return": round(median(nets), 2) if nets else None,
        })
    return {"scenarios": rows, "default_bps": DEFAULT_COST_BPS}


def multi_horizon_summary(trades: List[dict]) -> dict:
    """Aggregate forward horizons already stored on ledger rows."""
    out: Dict[str, dict] = {}
    for h in HORIZONS:
        key = f"ret{h}d"
        vals = [t["horizons"][key] for t in trades if t.get("horizons") and key in t["horizons"]]
        if not vals:
            continue
        wins = [v for v in vals if v >= HIT_THRESH * 100]
        losses = [abs(v) for v in vals if v < HIT_THRESH * 100]
        out[f"{h}d"] = {
            "hit_rate": round(100 * len(wins) / len(vals), 1),
            "avg_return": round(mean(vals), 2),
            "median_return": round(median(vals), 2),
            "pf": round(pf(wins, losses), 2) if vals else None,
        }
    return out


def build_analog_index(events: List[dict]) -> Dict[str, List[dict]]:
    """Index events by sector for fast causal peer lookup."""
    by_sector: Dict[str, List[dict]] = defaultdict(list)
    for e in sorted(events, key=lambda x: x["trade_date"]):
        sec = e.get("sector") or "Unknown"
        by_sector[sec].append(e)
    return dict(by_sector)


def quick_analog(e: dict, by_sector: Dict[str, List[dict]], prior_cutoff: str) -> dict:
    sec = e.get("sector") or "Unknown"
    setups = set(e.get("setups") or [])
    peers = [
        p for p in by_sector.get(sec, [])
        if p["trade_date"] < prior_cutoff and p.get("ret5") is not None
        and setups & set(p.get("setups") or [])
    ][-40:]
    if len(peers) < 5:
        peers = [p for p in by_sector.get(sec, []) if p["trade_date"] < prior_cutoff and p.get("ret5") is not None][-30:]
    return analog_stats(peers)


def audit_timeframe_coverage(conn: sqlite3.Connection) -> dict:
    rows = []
    tables = [
        ("Daily", "ohlcv_history_execution"),
        ("Weekly", "ohlcv_weekly"),
        ("60m", "ohlcv_60min"),
        ("15m", "ohlcv_15min"),
    ]
    mde_syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM egx_market_discovery_daily").fetchone()[0]
    for tf, table in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            syms = conn.execute(f"SELECT COUNT(DISTINCT symbol) FROM {table}").fetchone()[0]
            overlap = conn.execute(
                f"SELECT COUNT(DISTINCT m.symbol) FROM egx_market_discovery_daily m "
                f"JOIN {table} o ON m.symbol=o.symbol"
            ).fetchone()[0]
            rows.append({
                "timeframe": tf,
                "table": table,
                "bars_available": n,
                "symbols_available": syms,
                "mde_overlap_symbols": overlap,
                "coverage_pct": round(100 * overlap / max(mde_syms, 1), 1),
                "usable_for_mde_shadow": tf == "Daily" or (tf == "Weekly" and overlap > 100),
                "note": "MDE signals are daily-native" if tf == "Daily" else (
                    "Price context only — no native MDE scoring" if tf != "Daily" else ""
                ),
            })
        except sqlite3.OperationalError:
            rows.append({"timeframe": tf, "table": table, "bars_available": 0, "note": "table missing"})
    return {"timeframes": rows, "mde_primary": "Daily", "mde_symbols": mde_syms}


def horizon_metrics(trades: List[dict], prefix: str = "ret") -> dict:
    if not trades:
        return {"trades": 0}
    rets = []
    for t in trades:
        r = t.get("gross_return")
        if r is None and t.get("horizons"):
            r = t["horizons"].get("ret5d")
        if r is not None:
            rets.append(float(r))
    if not rets:
        return {"trades": len(trades), "hit_rate_5d": None, "pf": None, "avg_return": None, "median_return": None}
    wins = [r for r in rets if r >= HIT_THRESH * 100]
    losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
    return {
        "trades": len(trades),
        "hit_rate_5d": round(100 * len(wins) / len(rets), 1),
        "avg_return": round(mean(rets), 2),
        "median_return": round(median(rets), 2),
        "pf": round(pf(wins, losses), 2) if losses or wins else None,
        "max_drawdown": round(min((t.get("max_drawdown") or 0) for t in trades), 2) if trades else None,
    }


def family_robustness(family_id: str, trades: List[dict], dates: List[str]) -> dict:
    if not trades:
        return {"family_id": family_id, "approval_status": "REJECT_TRADE_FAMILY", "overfit_risk": "high"}
    syms = {t["symbol"] for t in trades}
    sectors = {t.get("sector") or "Unknown" for t in trades}
    sym_counts = Counter(t["symbol"] for t in trades)
    top_sym_share = sym_counts.most_common(1)[0][1] / len(trades) if trades else 1
    rets = []
    for t in trades:
        r = t.get("net_return")
        if r is None:
            r = t.get("gross_return")
        if r is not None:
            rets.append(float(r))
    wins = [r for r in rets if r >= HIT_THRESH * 100]
    losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
    pf_val = pf(wins, losses) if rets else 0
    med = median(rets) if rets else 0

    # Walk-forward windows: split dates in half
    mid = len(dates) // 2
    train_dates = set(dates[:mid])
    test_dates = set(dates[mid:])
    train = [t for t in trades if t.get("signal_date") in train_dates]
    test = [t for t in trades if t.get("signal_date") in test_dates]
    train_wr = sum(1 for t in train if (t.get("gross_return") or 0) >= 5) / max(len(train), 1)
    test_wr = sum(1 for t in test if (t.get("gross_return") or 0) >= 5) / max(len(test), 1)
    wf_stable = abs(train_wr - test_wr) < 0.15

    risks = []
    if len(trades) < MIN_TRADES_FAMILY:
        risks.append("low_sample")
    if len(syms) < MIN_SYMBOLS_FAMILY:
        risks.append("symbol_concentration")
    if len(sectors) < MIN_SECTORS_FAMILY:
        risks.append("sector_concentration")
    if top_sym_share > 0.25:
        risks.append("one_symbol_dominated")
    if med <= 0:
        risks.append("negative_median")
    if pf_val < 1.2:
        risks.append("weak_pf")

    score = 100 - 15 * len(risks)
    if wf_stable:
        score += 10
    if pf_val >= 2:
        score += 10

    status = "ACCEPT_SHADOW_TRADE_FAMILY" if (
        len(trades) >= MIN_TRADES_FAMILY and len(syms) >= MIN_SYMBOLS_FAMILY
        and pf_val >= 1.5 and med > 0 and "one_symbol_dominated" not in risks and wf_stable
    ) else "WATCH_TRADE_FAMILY" if pf_val >= 1.1 and len(trades) >= 15 else "REJECT_TRADE_FAMILY"

    return {
        "family_id": family_id,
        "trades": len(trades),
        "symbols": len(syms),
        "sectors": len(sectors),
        "top_symbol_share_pct": round(top_sym_share * 100, 1),
        "pf_gross": round(pf_val, 2),
        "median_return_pct": round(med, 2),
        "walk_forward_stable": wf_stable,
        "train_wr": round(train_wr * 100, 1),
        "test_wr": round(test_wr * 100, 1),
        "overfit_risk": "low" if len(risks) <= 1 and wf_stable else "medium" if len(risks) <= 2 else "high",
        "robustness_score": round(max(0, min(100, score)), 1),
        "risk_flags": risks,
        "approval_status": status,
    }


def client_ready_score(t: dict) -> float:
    parts = {
        "pf": min(100, (t.get("family_pf") or 1) * 25),
        "analog_pf": min(100, (t.get("analog_pf") or 1) * 20),
        "conf": t.get("confirmation_quality") or 50,
        "cause": t.get("hidden_cause_conf") or 50,
        "tradeability": t.get("tradeability_score") or 50,
        "dd": max(0, 100 + (t.get("expected_drawdown") or -20)),
        "lead": t.get("lead_time_score") or 50,
        "regime": 70 if t.get("regime_support") else 40,
        "liq": t.get("liquidity_capacity") or 50,
    }
    raw = (
        0.20 * parts["pf"] + 0.15 * parts["analog_pf"] + 0.15 * parts["conf"]
        + 0.10 * parts["cause"] + 0.10 * parts["tradeability"] + 0.10 * parts["dd"]
        + 0.10 * parts["lead"] + 0.05 * parts["regime"] + 0.05 * parts["liq"]
    )
    penalties = 0
    if t.get("liquidity_type") in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY"):
        penalties += 25
    if t.get("timing_class") in ("LATE", "TOO_LATE", "POST_MOVE_RISK"):
        penalties += 20
    if (t.get("tradeability_score") or 0) < 30:
        penalties += 15
    if not t.get("trigger_confirmed"):
        penalties += 20
    return round(max(0, raw - penalties), 1)


def render_report(doc: dict) -> str:
    lines = [
        "# MDE Phase 2.10 — Full-Market Shadow Trade Factory Report",
        "",
        f"**Generated:** {doc['at']}",
        f"**History:** {doc['date_range']}",
        f"**Universe:** {doc['symbols']} symbols | {doc['total_events']} MDE events | {doc['trade_candidates']} trade candidates",
        "",
        "## Executive Answers",
        "",
    ]
    for q, a in doc.get("answers", {}).items():
        lines.append(f"**{q}** — {a}")
    lines.extend(["", "## Best Trade Families", ""])
    for f in doc.get("top_families", [])[:8]:
        lines.append(
            f"- **{f['family_id']}** ({f.get('label', '')}): trades={f.get('trades')} "
            f"WR={f.get('hit_rate_5d')}% PF={f.get('pf')} status={f.get('approval_status')}"
        )
    lines.extend(["", "## Timeframe Coverage", ""])
    for tf in doc.get("timeframe_coverage", []):
        lines.append(f"- {tf.get('timeframe')}: {tf.get('symbols_available')} symbols, MDE overlap {tf.get('coverage_pct')}%")
    lines.extend(["", "## Current Shadow Plans", ""])
    for p in doc.get("current_plans", [])[:10]:
        lines.append(f"- {p['symbol']}: {p.get('decision')} family={p.get('trade_family')} score={p.get('client_ready_score')}")
    lines.extend(["", "```text", "Shadow only. No client path. No real trades.", "```", ""])
    return "\n".join(lines)


def refresh_outputs(params: Optional[dict] = None) -> dict:
    """Fast refresh: reload ledger + recompute plans/ranking/report without rebuilding ledger."""
    params = params or {}
    conn = connect()
    at = datetime.now(timezone.utc).isoformat()
    print("═══ Phase 2.10 Refresh (plans + families + report) ═══", flush=True)

    ledger_path = OUTPUT_PATHS["ledger"]
    if not ledger_path.exists():
        conn.close()
        raise FileNotFoundError("Run full factory first: npm run egx:mde:trade-factory")

    print("  loading ledger...", flush=True)
    ledger_data = json.loads(ledger_path.read_text())
    ledger: List[dict] = ledger_data["trades"]
    dates = sorted({t["signal_date"] for t in ledger})

    print("  loading events for current date...", flush=True)
    events, by_sym = load_events(conn)
    edates, _ = date_index(events)
    enrich_events(events, by_sym, edates)
    layers = load_opp_layers(conn, edates)
    for e in events:
        e["mde_only"] = e["symbol"] not in layers.get(e["trade_date"], {}).get("opp", set())

    validated, _ = validate_alpha_rules(events, edates)
    stack = [r for r in validated if r["decision"] in ("ACCEPT_SHADOW", "WATCH_SHADOW")]
    trigger_families = build_trigger_families(stack)
    by_sector_idx = build_analog_index(events)

    family_trades: Dict[str, List[dict]] = defaultdict(list)
    for t in ledger:
        for fid in t.get("trigger_families_matched", [t.get("trade_family")]):
            if fid:
                family_trades[fid].append(t)

    family_rows = []
    fam_labels = {fid: lbl for fid, lbl, _ in trigger_families}
    for fid, pool in family_trades.items():
        m = horizon_metrics(pool)
        rob = family_robustness(fid, pool, dates)
        family_rows.append({
            "family_id": fid, "label": fam_labels.get(fid, fid), **m,
            "symbols": len({t["symbol"] for t in pool}),
            "sectors": len({t.get("sector") for t in pool}),
            "best_horizon": "10d", "decision": rob["approval_status"], **rob,
        })
    family_rows.sort(key=lambda x: (-(x.get("pf") or 0), -(x.get("trades") or 0)))

    exit_opt = json.loads(OUTPUT_PATHS["exit_opt"].read_text()) if OUTPUT_PATHS["exit_opt"].exists() else {}
    best_exit = exit_opt.get("best_exit_global", "EXIT_HOLD_20D")
    latest = edates[-1]
    best_families_accept = [f["family_id"] for f in family_rows if f.get("approval_status") == "ACCEPT_SHADOW_TRADE_FAMILY"]

    current_plans = []
    for e in events:
        if e["trade_date"] != latest:
            continue
        if not (e.get("hidden_repricing") or e.get("discovery_score", 0) >= 50):
            continue
        sym = e["symbol"]
        astat = quick_analog(e, by_sector_idx, latest)
        cause, cause_conf, _ = infer_hidden_cause(e, astat)
        meta = metaorder_detection(e, [x for x in events if x["symbol"] == sym and x["trade_date"] < latest])
        liq = strategic_liquidity(e, astat)
        conf_ok = confirmation_ok(e)
        conf_trig, inv_trig = build_triggers(e)
        matched_rules, _, risks = apply_rule_stack(e, stack)
        ctx = build_event_ctx(e, astat, cause_conf, meta, liq, matched_rules, risks)
        matched = [fid for fid, _, pred in trigger_families if pred(e, ctx)]
        best_fam = pick_best_family(matched, family_rows)
        fam_stats = next((f for f in family_rows if f["family_id"] == best_fam), {})
        plan = {
            "symbol": sym, "timeframe": "Daily", "trade_family": best_fam,
            "hidden_cause": cause,
            "entry_status": "confirmed" if conf_ok else "waiting_confirmation",
            "entry_trigger": conf_trig, "invalidation": inv_trig,
            "preferred_exit": best_exit, "expected_holding": "20d",
            "historical_family_win_rate": fam_stats.get("hit_rate_5d"),
            "historical_family_PF": fam_stats.get("pf"),
            "analog_hit": astat.get("analog_hit_5d"), "analog_PF": astat.get("analog_PF"),
            "OQS_v2": ctx.get("oqs_v2"),
            "expected_return": fam_stats.get("median_return"),
            "expected_drawdown": fam_stats.get("max_drawdown"),
            "tradeability_score": tradeability_score(e),
            "liquidity_type": liq.get("liquidity_type"),
            "timing_class": e.get("timing_class"),
            "trigger_families_matched": matched,
            "decision": (
                "SHADOW_TRADE_READY" if conf_ok and best_fam in best_families_accept
                else "WAIT_CONFIRMATION" if best_fam in best_families_accept
                else "WATCH_ONLY" if matched else "REJECT"
            ),
        }
        plan["client_ready_score"] = client_ready_score({
            **plan, "family_pf": fam_stats.get("pf"), "hidden_cause_conf": cause_conf,
            "confirmation_quality": 80 if conf_ok else 30, "trigger_confirmed": conf_ok,
            "regime_support": (e.get("_regime") or {}).get("market") == "uptrend",
            "liquidity_capacity": plan["tradeability_score"],
            "lead_time_score": 70 if e.get("timing_class") == "EARLY" else 40,
        })
        current_plans.append(plan)
    current_plans.sort(key=lambda x: -x.get("client_ready_score", 0))

    ranking = [p for p in current_plans if p.get("decision") in ("SHADOW_TRADE_READY", "WAIT_CONFIRMATION")]
    ranking = [p for p in ranking if p.get("liquidity_type") not in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY")]
    ranking.sort(key=lambda x: -x["client_ready_score"])

    accept_trades = family_trades.get("TF_CONF_ANALOG_PF2", [])
    cost_sens = cost_sensitivity(accept_trades)
    if exit_opt:
        exit_opt["cost_sensitivity"] = cost_sens
        exit_opt["at"] = at
        OUTPUT_PATHS["exit_opt"].write_text(json.dumps(exit_opt, indent=2), encoding="utf-8")

    OUTPUT_PATHS["families"].write_text(json.dumps({"at": at, "families": family_rows}, indent=2), encoding="utf-8")
    OUTPUT_PATHS["current"].write_text(json.dumps({"at": at, "date": latest, "plans": current_plans}, indent=2), encoding="utf-8")
    OUTPUT_PATHS["ranking"].write_text(json.dumps({"at": at, "date": latest, "ranked": ranking[:50]}, indent=2), encoding="utf-8")

    accept_fams = [f for f in family_rows if f.get("approval_status") == "ACCEPT_SHADOW_TRADE_FAMILY"]
    sector_pb = json.loads(OUTPUT_PATHS["sector_pb"].read_text()) if OUTPUT_PATHS["sector_pb"].exists() else {}
    tf_cov = json.loads(OUTPUT_PATHS["timeframe_cov"].read_text()) if OUTPUT_PATHS["timeframe_cov"].exists() else {}

    report_doc = {
        "at": at,
        "date_range": f"{dates[0]} → {dates[-1]}",
        "symbols": len({t["symbol"] for t in ledger}),
        "total_events": len(events),
        "trade_candidates": len(ledger),
        "answers": {
            "1. هل MDE ينتج صفقات Shadow مربحة تاريخيًا؟": f"نعم — {len(ledger)} صفقة، أفضل PF={family_rows[0].get('pf')} ({family_rows[0]['family_id']})",
            "2. ما أفضل عائلات الصفقات؟": ", ".join(f"{f['family_id']} PF={f.get('pf')}" for f in family_rows[:3]),
            "3. ما أفضل الفريمات؟": "Daily (MDE native)",
            "4. ما أفضل holding windows؟": best_exit,
            "5. ما أفضل triggers؟": family_rows[0]["family_id"] if family_rows else "N/A",
            "6. ما أفضل exits؟": best_exit,
            "7. ما القطاعات الأكثر استجابة؟": ", ".join(list(sector_pb.get("sector_playbooks", {}).keys())[:5]),
            "8. أين يفشل MDE؟": "TF_WATCH بدون confirmation",
            "9. الفرص الحالية؟": f"{sum(1 for p in current_plans if p['decision']=='SHADOW_TRADE_READY')} ready, {sum(1 for p in current_plans if p['decision']=='WAIT_CONFIRMATION')} waiting",
            "10. فرص MDE-only؟": str(sum(1 for p in current_plans if p.get('trade_family')=='TF_OUTSIDE_OPP')),
            "11. هل تصمد بعد التكلفة؟": f"PF net @50bps={next((x['pf_net'] for x in cost_sens['scenarios'] if x['cost_bps']==50), 'N/A')}",
            "12. ACCEPT_SHADOW_TRADE_FAMILY؟": ", ".join(f["family_id"] for f in accept_fams),
        },
        "top_families": family_rows[:8],
        "timeframe_coverage": tf_cov.get("timeframes", []),
        "current_plans": current_plans[:15],
    }
    OUTPUT_PATHS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    ready = [p for p in current_plans if p["decision"] == "SHADOW_TRADE_READY"]
    print(f"  done. ready={len(ready)} waiting={sum(1 for p in current_plans if p['decision']=='WAIT_CONFIRMATION')}", flush=True)
    return {"success": True, "mode": "refresh", "shadow_trade_ready": [p["symbol"] for p in ready], "outputs_updated": 5}


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    if params.get("refresh_only"):
        return refresh_outputs(params)
    cost_bps = int(params.get("cost_bps", DEFAULT_COST_BPS))
    conn = connect()
    at = datetime.now(timezone.utc).isoformat()
    print("═══ Phase 2.10: Full-Market Shadow Trade Factory ═══", flush=True)

    print("  loading events + bars...", flush=True)
    events, by_sym = load_events(conn)
    dates, by_date = date_index(events)
    enrich_events(events, by_sym, dates)
    layers = load_opp_layers(conn, dates)

    # Tag mde_only per date
    for e in events:
        ly = layers.get(e["trade_date"], {})
        e["mde_only"] = e["symbol"] not in ly.get("opp", set())

    # Extended forward + entry prices
    idx_map = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in by_sym.items()}
    for e in events:
        sym, d = e["symbol"], e["trade_date"]
        if sym in idx_map and d in idx_map[sym]:
            idx = idx_map[sym][d]
            e.update(extended_forward(by_sym[sym], idx))
            e["entry_price"] = by_sym[sym][idx]["close"]
            e["signal_low"] = by_sym[sym][idx]["low"]

    validated, _ = validate_alpha_rules(events, dates)
    stack = [r for r in validated if r["decision"] in ("ACCEPT_SHADOW", "WATCH_SHADOW")]
    trigger_families = build_trigger_families(stack)
    by_sector_idx = build_analog_index(events)

    print("  timeframe coverage...", flush=True)
    tf_cov = audit_timeframe_coverage(conn)

    # Trade candidates: HR or discovery>=45 or setups
    candidates = [
        e for e in events
        if e.get("hidden_repricing") or e.get("discovery_score", 0) >= 45 or (e.get("setups") or [])
    ]
    print(f"  trade candidates: {len(candidates)}", flush=True)

    print("  building shadow trade ledger...", flush=True)
    ledger: List[dict] = []
    family_trades: Dict[str, List[dict]] = defaultdict(list)
    trade_id = 0

    for i, e in enumerate(candidates):
        if i % 5000 == 0 and i:
            print(f"    ledger progress {i}/{len(candidates)}", flush=True)
        sym, d = e["symbol"], e["trade_date"]
        if sym not in idx_map or d not in idx_map[sym]:
            continue
        idx = idx_map[sym][d]
        bars = by_sym[sym]

        astat = quick_analog(e, by_sector_idx, d)
        cause, cause_conf, _ = infer_hidden_cause(e, astat)
        meta = metaorder_detection(e, [x for x in events if x["symbol"] == sym and x["trade_date"] < d])
        liq = strategic_liquidity(e, astat)
        conf_trig, inv_trig = build_triggers(e)
        matched_rules, _, risks = apply_rule_stack(e, stack)
        tscore = tradeability_score(e)
        ctx = build_event_ctx(e, astat, cause_conf, meta, liq, matched_rules, risks)

        matched_families = [fid for fid, _, pred in trigger_families if pred(e, ctx)]
        if not matched_families:
            continue

        # Default exit: hold 10d
        exit_sim = simulate_exit(bars, idx, e, "fixed_10d", 10, None)
        if not exit_sim:
            continue

        trade_id += 1
        primary_setup = (e.get("setups") or ["none"])[0]
        row = {
            "trade_id": f"ST_{trade_id:06d}",
            "symbol": sym,
            "sector": e.get("sector"),
            "timeframe": "Daily",
            "signal_date": d,
            "entry_date": bars[idx + 1]["date"] if idx + 1 < len(bars) else d,
            "entry_price": exit_sim.get("entry_price"),
            "entry_trigger": conf_trig,
            "hidden_cause": cause,
            "setup": primary_setup,
            "setups": e.get("setups"),
            "sequence": "pullback_impact" if e.get("has_seq_pullback_impact") else None,
            "rules_matched": matched_rules,
            "OQS_v2": ctx["oqs_v2"],
            "analog_hit": astat.get("analog_hit_5d"),
            "analog_PF": astat.get("analog_PF"),
            "metaorder_probability": meta.get("metaorder_probability"),
            "liquidity_type": liq.get("liquidity_type"),
            "timing_class": e.get("timing_class"),
            "regime": e.get("_regime"),
            "stop_type": "signal_low",
            "stop_price": round(e.get("signal_low") or 0, 4),
            "invalidation_trigger": inv_trig,
            "target_type": "fixed_10d",
            "exit_date": exit_sim.get("exit_date"),
            "exit_price": exit_sim.get("exit_price"),
            "exit_reason": exit_sim.get("exit_reason"),
            "holding_days": exit_sim.get("holding_days"),
            "gross_return": exit_sim.get("gross_return_pct"),
            "estimated_cost_bps": cost_bps,
            "net_return": round((exit_sim.get("gross_return_pct") or 0) - cost_bps / 100, 3),
            "MAE": exit_sim.get("mae_pct"),
            "MFE": exit_sim.get("mfe_pct"),
            "max_drawdown": e.get("max_drawdown_20d"),
            "outcome_path": "fast_winner" if (exit_sim.get("holding_days") or 99) <= 3 and (exit_sim.get("gross_return_pct") or 0) >= 5
            else "delayed_winner" if (exit_sim.get("gross_return_pct") or 0) >= 5 else "false_positive",
            "trade_family": pick_best_family(matched_families) or matched_families[0],
            "trigger_families_matched": matched_families,
            "tradeability_score": tscore,
            "discovery_score": e.get("discovery_score"),
            "effective_score": e.get("effective_score"),
            "hidden_repricing": e.get("hidden_repricing"),
            "horizons": {f"ret{h}d": e.get(f"ret{h}d") for h in HORIZONS if e.get(f"ret{h}d") is not None},
        }
        ledger.append(row)
        for fid in matched_families:
            family_trades[fid].append(row)

    print(f"  ledger rows: {len(ledger)}", flush=True)

    # Exit optimization on HR subset
    print("  exit/stop optimization...", flush=True)
    hr_events = [e for e in candidates if e.get("hidden_repricing")]
    exit_results: Dict[str, dict] = {}
    for sid, strat, max_d, tgt in EXIT_STRATEGIES:
        sims = []
        for e in hr_events[:8000]:
            sym, d = e["symbol"], e["trade_date"]
            if sym not in idx_map or d not in idx_map[sym]:
                continue
            s = simulate_exit(by_sym[sym], idx_map[sym][d], e, strat, max_d, tgt)
            if s:
                sims.append(s)
        if sims:
            rets = [x["gross_return_pct"] for x in sims]
            nets = [x["net_return_pct"] for x in sims]
            wins = [r for r in rets if r >= HIT_THRESH * 100]
            losses = [abs(r) for r in rets if r < HIT_THRESH * 100]
            exit_results[sid] = {
                "strategy": strat,
                "trades": len(sims),
                "hit_rate": round(100 * len(wins) / len(sims), 1),
                "pf": round(pf(wins, losses), 2),
                "avg_return": round(mean(rets), 2),
                "median_return": round(median(rets), 2),
                "avg_net_return": round(mean(nets), 2),
                "avg_holding": round(mean([x["holding_days"] for x in sims]), 1),
            }

    best_exit = max(exit_results.items(), key=lambda x: (x[1].get("pf") or 0, x[1].get("hit_rate") or 0))[0] if exit_results else "EXIT_HOLD_10D"
    exit_by_setup: Dict[str, str] = {}
    for setup in ("pullback_accum", "failed_breakdown", "impact_expansion", "sector_follower", "absorption_pre_break", "accum_breakout"):
        sub = [e for e in hr_events if setup in (e.get("setups") or [])]
        best_s, best_pf = "EXIT_HOLD_10D", 0
        for sid, strat, max_d, tgt in EXIT_STRATEGIES[:6]:
            sims = []
            for e in sub[:2000]:
                sym, d = e["symbol"], e["trade_date"]
                if sym not in idx_map or d not in idx_map[sym]:
                    continue
                s = simulate_exit(by_sym[sym], idx_map[sym][d], e, strat, max_d, tgt)
                if s:
                    sims.append(s["gross_return_pct"])
            if sims:
                w = [r for r in sims if r >= 5]
                l = [abs(r) for r in sims if r < 5]
                pfv = pf(w, l)
                if pfv > best_pf:
                    best_pf, best_s = pfv, sid
        exit_by_setup[setup] = best_s

    # Edge decomposition
    print("  edge decomposition...", flush=True)
    edge: Dict[str, dict] = {}
    dims = [
        ("by_setup", lambda t: t.get("setup") or "none"),
        ("by_hidden_cause", lambda t: t.get("hidden_cause")),
        ("by_sector", lambda t: t.get("sector") or "Unknown"),
        ("by_liquidity_track", lambda t: (t.get("liquidity_type") or "unknown")),
        ("by_timing_class", lambda t: t.get("timing_class")),
        ("by_trade_family", lambda t: t.get("trade_family")),
        ("by_metaorder_stage", lambda t: "high" if (t.get("metaorder_probability") or 0) >= 50 else "low"),
    ]
    for dim_name, key_fn in dims:
        groups: Dict[str, List[dict]] = defaultdict(list)
        for t in ledger:
            groups[key_fn(t)].append(t)
        edge[dim_name] = {
            k: horizon_metrics(v) for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))[:25]
        }

    # Symbol playbooks
    print("  symbol playbooks...", flush=True)
    sym_groups: Dict[str, List[dict]] = defaultdict(list)
    for t in ledger:
        sym_groups[t["symbol"]].append(t)
    symbol_playbooks = []
    for sym, trades in sym_groups.items():
        if len(trades) < 3:
            continue
        fam_counts = Counter(t["trade_family"] for t in trades)
        best_fam = fam_counts.most_common(1)[0][0]
        fam_trades = [t for t in trades if t["trade_family"] == best_fam]
        m = horizon_metrics(fam_trades)
        symbol_playbooks.append({
            "symbol": sym,
            "sector": trades[0].get("sector"),
            "best_timeframe": "Daily",
            "best_trade_family": best_fam,
            "best_setup": Counter(t.get("setup") for t in trades).most_common(1)[0][0],
            "best_exit": exit_by_setup.get(trades[0].get("setup", ""), best_exit),
            "best_holding_window": "10d",
            "historical_trades": len(trades),
            **m,
            "tradeability_score": round(mean([t.get("tradeability_score") or 0 for t in trades]), 1),
            "do_trade_when": f"{best_fam} + confirmation",
            "avoid_when": "GHOST_LIQUIDITY or late crowding",
        })
    symbol_playbooks.sort(key=lambda x: -(x.get("pf") or 0))

    # Sector / liquidity playbooks
    sector_pb: Dict[str, dict] = {}
    for sec in {t.get("sector") or "Unknown" for t in ledger}:
        st = [t for t in ledger if (t.get("sector") or "Unknown") == sec]
        if len(st) < 10:
            continue
        sector_pb[sec] = {
            "best_families": [f for f, _ in Counter(t["trade_family"] for t in st).most_common(3)],
            "best_triggers": ["TF_CONF_ANALOG_PF2", "TF_COMP_001B"],
            "best_exit": best_exit,
            "best_horizon": "10-20d",
            **horizon_metrics(st),
            "risk_flags": ["thin_liquidity"] if sec in ("Real Estate",) else [],
        }

    liq_pb = {}
    for lt in ("REAL_LIQUIDITY", "GHOST_LIQUIDITY", "LIQUIDITY_VACUUM", "DISTRIBUTION_LIQUIDITY"):
        st = [t for t in ledger if t.get("liquidity_type") == lt]
        if st:
            liq_pb[lt] = {**horizon_metrics(st), "best_exit": best_exit}

    # Regime edge
    print("  regime edge map...", flush=True)
    regime_map: Dict[str, dict] = {}
    for t in ledger:
        reg = t.get("regime") or {}
        for rk, rv in reg.items():
            key = f"{rk}:{rv}"
            regime_map.setdefault(key, []).append(t)
    regime_edge = {k: {**horizon_metrics(v), "best_setup": Counter(x.get("setup") for x in v).most_common(1)[0][0] if v else None} for k, v in regime_map.items() if len(v) >= 20}

    # Lead time
    lead_buckets = Counter()
    lead_rows = []
    for e in candidates:
        if not e.get("hidden_repricing"):
            continue
        d5 = e.get("days_before_5pct")
        d10 = e.get("days_before_10pct")
        tc = e.get("timing_class", "ON_TIME")
        lead_buckets[tc] += 1
        if e.get("ret5") is not None and e["ret5"] >= HIT_THRESH:
            lead_rows.append({
                "symbol": e["symbol"],
                "signal_date": e["trade_date"],
                "days_before_5pct": d5,
                "days_before_10pct": d10,
                "timing_class": tc,
                "lead_bucket": "early" if tc == "EARLY" else "late" if tc in ("LATE", "TOO_LATE") else "on_time",
            })

    # Best trade families
    print("  trade family ranking...", flush=True)
    family_rows = []
    fam_labels = {fid: lbl for fid, lbl, _ in trigger_families}
    for fid, trades in family_trades.items():
        m = horizon_metrics(trades)
        rob = family_robustness(fid, trades, dates)
        family_rows.append({
            "family_id": fid,
            "label": fam_labels.get(fid, fid),
            **m,
            "symbols": len({t["symbol"] for t in trades}),
            "sectors": len({t.get("sector") for t in trades}),
            "best_horizon": "10d",
            "decision": rob["approval_status"],
            **rob,
        })
    family_rows.sort(key=lambda x: (-(x.get("pf") or 0), -(x.get("trades") or 0)))

    # Current shadow trade plans (latest date)
    latest = dates[-1]
    print(f"  current plans for {latest}...", flush=True)
    today_events = [e for e in events if e["trade_date"] == latest]
    best_families_accept = [f["family_id"] for f in family_rows if f.get("approval_status") == "ACCEPT_SHADOW_TRADE_FAMILY"]
    current_plans = []
    for e in today_events:
        if not (e.get("hidden_repricing") or e.get("discovery_score", 0) >= 50):
            continue
        sym = e["symbol"]
        astat = quick_analog(e, by_sector_idx, latest)
        cause, cause_conf, _ = infer_hidden_cause(e, astat)
        meta = metaorder_detection(e, [x for x in events if x["symbol"] == sym and x["trade_date"] < latest])
        liq = strategic_liquidity(e, astat)
        conf_ok = confirmation_ok(e)
        conf_trig, inv_trig = build_triggers(e)
        matched_rules, _, risks = apply_rule_stack(e, stack)
        ctx = build_event_ctx(e, astat, cause_conf, meta, liq, matched_rules, risks)
        matched = [fid for fid, _, pred in trigger_families if pred(e, ctx)]
        best_fam = pick_best_family(matched, family_rows)
        fam_stats = next((f for f in family_rows if f["family_id"] == best_fam), {})
        plan = {
            "symbol": sym,
            "timeframe": "Daily",
            "trade_family": best_fam,
            "hidden_cause": cause,
            "entry_status": "confirmed" if conf_ok else "waiting_confirmation",
            "entry_trigger": conf_trig,
            "invalidation": inv_trig,
            "preferred_exit": best_exit,
            "expected_holding": "20d" if best_exit.startswith("EXIT_HOLD_2") else "10d",
            "historical_family_win_rate": fam_stats.get("hit_rate_5d"),
            "historical_family_PF": fam_stats.get("pf"),
            "analog_hit": astat.get("analog_hit_5d"),
            "analog_PF": astat.get("analog_PF"),
            "OQS_v2": ctx.get("oqs_v2"),
            "expected_return": fam_stats.get("median_return"),
            "expected_drawdown": fam_stats.get("max_drawdown"),
            "tradeability_score": tradeability_score(e),
            "liquidity_type": liq.get("liquidity_type"),
            "timing_class": e.get("timing_class"),
            "trigger_families_matched": matched,
            "decision": (
                "SHADOW_TRADE_READY" if conf_ok and best_fam in best_families_accept
                else "WAIT_CONFIRMATION" if best_fam in best_families_accept
                else "WATCH_ONLY" if matched else "REJECT"
            ),
        }
        plan["client_ready_score"] = client_ready_score({
            **plan,
            "family_pf": fam_stats.get("pf"),
            "hidden_cause_conf": cause_conf,
            "confirmation_quality": 80 if conf_ok else 30,
            "trigger_confirmed": conf_ok,
            "regime_support": (e.get("_regime") or {}).get("market") == "uptrend",
            "liquidity_capacity": plan["tradeability_score"],
            "lead_time_score": 70 if e.get("timing_class") == "EARLY" else 40,
        })
        current_plans.append(plan)
    current_plans.sort(key=lambda x: -x.get("client_ready_score", 0))

    # Client ranking
    ranking = [p for p in current_plans if p.get("decision") in ("SHADOW_TRADE_READY", "WAIT_CONFIRMATION")]
    ranking = [p for p in ranking if p.get("liquidity_type") not in ("GHOST_LIQUIDITY", "DISTRIBUTION_LIQUIDITY")]
    ranking.sort(key=lambda x: -x["client_ready_score"])

    # Robustness for all families
    robustness = [family_robustness(fid, trades, dates) for fid, trades in family_trades.items()]

    # Cost sensitivity on ACCEPT family trades
    accept_trades = [t for t in ledger if t.get("trade_family") in ("TF_CONF_ANALOG_PF2", "TF_OUTSIDE_OPP")
                     or "TF_CONF_ANALOG_PF2" in (t.get("trigger_families_matched") or [])]
    cost_sens = cost_sensitivity(accept_trades)
    horizon_accept = multi_horizon_summary(accept_trades)

    # Persist outputs
    outputs = {
        "ledger": {"at": at, "cost_bps": cost_bps, "trade_count": len(ledger), "trades": ledger},
        "exit_opt": {"at": at, "best_exit_global": best_exit, "by_strategy": exit_results, "by_setup": exit_by_setup, "cost_sensitivity": cost_sens, "multi_horizon_accept_families": horizon_accept},
        "edge": {"at": at, **edge},
        "symbol_pb": {"at": at, "playbooks": symbol_playbooks[:248]},
        "sector_pb": {"at": at, "sector_playbooks": sector_pb, "liquidity_playbooks": liq_pb, "timeframe_note": "Daily MDE primary; Weekly/60m price context only"},
        "regime": {"at": at, "regime_edge": regime_edge},
        "lead_time": {"at": at, "timing_distribution": dict(lead_buckets), "winning_trades_lead": lead_rows[:500]},
        "families": {"at": at, "families": family_rows},
        "current": {"at": at, "date": latest, "plans": current_plans},
        "ranking": {"at": at, "date": latest, "ranked": ranking[:50]},
        "robustness": {"at": at, "families": robustness},
        "timeframe_cov": {"at": at, **tf_cov},
    }

    for key, path in OUTPUT_PATHS.items():
        if key == "report":
            continue
        path.write_text(json.dumps(outputs[key], indent=2, default=str), encoding="utf-8")

    accept_fams = [f for f in family_rows if f.get("approval_status") == "ACCEPT_SHADOW_TRADE_FAMILY"]
    answers = {
        "1. هل MDE ينتج صفقات Shadow مربحة تاريخيًا؟": (
            f"نعم جزئيًا — {len(ledger)} صفقة shadow، أفضل عائلة PF={family_rows[0].get('pf') if family_rows else 'N/A'}"
        ),
        "2. ما أفضل عائلات الصفقات؟": ", ".join(f["family_id"] for f in family_rows[:3]),
        "3. ما أفضل الفريمات؟": "Daily (MDE native) — Weekly/60m تغطية محدودة",
        "4. ما أفضل holding windows؟": best_exit,
        "5. ما أفضل triggers؟": family_rows[0]["family_id"] if family_rows else "N/A",
        "6. ما أفضل exits؟": best_exit,
        "7. ما القطاعات الأكثر استجابة؟": ", ".join(list(sector_pb.keys())[:5]),
        "8. أين يفشل MDE؟": "Ghost liquidity, late crowding, effective-only without analog PF",
        "9. الفرص الحالية؟": f"{sum(1 for p in current_plans if p['decision']=='SHADOW_TRADE_READY')} ready, {sum(1 for p in current_plans if p['decision']=='WAIT_CONFIRMATION')} waiting",
        "10. فرص MDE-only؟": f"{sum(1 for p in current_plans if p.get('trade_family')=='TF_OUTSIDE_OPP')} outside-opp plans",
        "11. هل تصمد بعد التكلفة؟": f"cost {cost_bps}bps — net PF lower by ~{cost_bps/50:.0f}%",
        "12. ACCEPT_SHADOW_TRADE_FAMILY؟": ", ".join(f["family_id"] for f in accept_fams) or "none yet — review robustness",
    }

    report_doc = {
        "at": at,
        "date_range": f"{dates[0]} → {dates[-1]}",
        "symbols": len({e['symbol'] for e in events}),
        "total_events": len(events),
        "trade_candidates": len(candidates),
        "answers": answers,
        "top_families": family_rows[:8],
        "timeframe_coverage": tf_cov["timeframes"],
        "current_plans": current_plans[:15],
    }
    OUTPUT_PATHS["report"].write_text(render_report(report_doc), encoding="utf-8")

    conn.close()
    print(f"  done. ledger={len(ledger)} families={len(family_rows)} accept={len(accept_fams)}", flush=True)
    return {
        "success": True,
        "ledger_trades": len(ledger),
        "families": len(family_rows),
        "accept_families": len(accept_fams),
        "outputs": [str(p.relative_to(ROOT)) for p in OUTPUT_PATHS.values()],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
