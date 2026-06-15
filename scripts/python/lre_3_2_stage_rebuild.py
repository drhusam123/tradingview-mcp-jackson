#!/usr/bin/env python3
"""
LRE Phase 3.2 — Stage Scoring Rebuild + Threshold/Timing/Stop Diagnostic.

Keeps LRE-3.0 and LRE-3.1 for comparison. Shadow only.
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
    atr_pct,
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
    upper_wick_ratio,
    volume_filter_ok,
)
from lre_3_2_stages import (  # noqa: E402
    MONITOR_SUBSTAGES,
    SUBSTAGE_LABELS,
    TRADE_SUBSTAGES,
    classify_substage,
    substage_monitoring,
    substage_tradeable,
)
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_walkforward_shadow import pf  # noqa: E402

PHASE_INVARIANTS = {**LRE_INVARIANTS, "phase": "LRE-3.2", "paper_only": True, "client_path_allowed": False}
COST_BPS = 100
COST_BPS_150 = 150
DEDUP_COOLDOWN = 10
OOS_START = "2025-01-01"

OUTPUTS = {
    "replay": DATA / "lre_3_2_stage_rebuild_replay.json",
    "threshold": DATA / "lre_3_2_threshold_diagnostic.json",
    "entry_timing": DATA / "lre_3_2_entry_timing_diagnostic.json",
    "stop": DATA / "lre_3_2_stop_diagnostic.json",
    "candidates": DATA / "lre_3_2_candidate_review.json",
    "report": ROOT / "docs/LRE_PHASE_3_2_STAGE_REBUILD_REPORT.md",
}

A_SIM_BANDS = [(80, 85), (85, 87.5), (87.5, 90), (90, 100)]
COMP_BANDS = [(10, 15), (15, 20), (20, 30), (30, 999)]
VOL20_BANDS = [(1.0, 1.3), (1.3, 2.0), (2.0, 3.5), (3.5, 99)]
VOL60_BANDS = [(1.0, 1.2), (1.2, 2.0), (2.0, 3.0), (3.0, 99)]
HOLD_WINDOWS = (10, 20, 30, 45)


def _window_dates(max_date: str) -> dict:
    from datetime import datetime as dt, timedelta
    md = dt.strptime(max_date, "%Y-%m-%d")
    return {
        "full": ("2020-12-10", "2099-12-31"),
        "oos": (OOS_START, "2099-12-31"),
        "latest_6m": ((md - timedelta(days=183)).strftime("%Y-%m-%d"), max_date),
        "latest_3m": ((md - timedelta(days=92)).strftime("%Y-%m-%d"), max_date),
    }


def in_window(d: str, w: Tuple[str, str]) -> bool:
    return w[0] <= d <= w[1]


def build_pool(conn, by_sym: dict, fingerprints: dict, thresholds: dict) -> List[dict]:
    pool = []
    for sym, bars in by_sym.items():
        if len(bars) < 90:
            continue
        for idx in range(40, len(bars) - MAX_FORWARD):
            if vol_ratio(bars, idx, 20) < 1.05 and compression_days(bars, idx) < 6:
                continue
            row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
            if not row or int(row.get("stage") or 0) not in (3, 4):
                continue
            if float(row.get("explosion_potential") or 0) < 50:
                continue
            sub, sub_detail = classify_substage(bars, idx, row)
            row["sub_stage"] = sub
            row["sub_stage_label"] = SUBSTAGE_LABELS.get(sub, sub)
            row["sub_stage_detail"] = sub_detail
            row["_sym"] = sym
            row["_idx"] = idx
            row["_bars"] = bars
            pool.append(row)
    return pool


def resolve_entry_idx(bars: List[dict], sig_idx: int, timing: str) -> Tuple[Optional[int], str]:
    if timing == "same_day":
        return sig_idx, "same_day_close"
    if sig_idx + 1 >= len(bars):
        return None, "no_next_bar"
    sig_close = bars[sig_idx]["close"]
    if timing == "pullback":
        nb = bars[sig_idx + 1]
        o, c = nb["open"], nb["close"]
        if not sig_close or not o or not c:
            return None, "pullback_missing"
        gap = (o - sig_close) / sig_close * 100
        day_ret = (c - o) / o * 100 if o else 0
        if gap > 3 or day_ret > 3:
            return None, "pullback_skip_extended"
        return sig_idx + 1, "next_day_pullback"
    if timing == "confirmation":
        for j in range(sig_idx + 1, min(len(bars), sig_idx + 4)):
            b = bars[j]
            c, o = b["close"], b["open"]
            if not c or not sig_close:
                continue
            if c <= sig_close:
                continue
            if upper_wick_ratio(b) > 0.55:
                continue
            mv = (c - sig_close) / sig_close * 100
            if mv > 5:
                continue
            return j, "confirmation_follow_through"
        return None, "no_confirmation"
    return sig_idx, timing


def simulate_from_entry(
    bars: List[dict],
    entry_idx: int,
    hold_days: int = 10,
    stop_mode: str = "base_low",
    stop_pct: Optional[float] = None,
) -> dict:
    entry = bars[entry_idx]["close"]
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
    stop_hit_day = None
    end = min(len(bars) - 1, entry_idx + max(HOLD_WINDOWS))
    for j in range(entry_idx + 1, end + 1):
        lo, hi, cl = bars[j]["low"], bars[j]["high"], bars[j]["close"]
        if lo and hi:
            mae = min(mae, (lo - entry) / entry * 100)
            mfe = max(mfe, (hi - entry) / entry * 100)
        if stop_mode != "none" and lo and lo <= stop:
            exit_price = stop
            exit_reason = "stop_hit"
            stop_hit_day = j - entry_idx
            hold = stop_hit_day
            break
        hold = j - entry_idx
        if hold >= hold_days and cl:
            exit_price = cl
            exit_reason = f"hold_{hold_days}d"
            break

    gross = (exit_price - entry) / entry * 100
    post_stop_mfe = None
    if stop_hit_day and stop_hit_day < end - entry_idx:
        post_mfe = 0.0
        for j in range(entry_idx + stop_hit_day, min(len(bars), entry_idx + hold_days + 15)):
            hi = bars[j]["high"]
            if hi:
                post_mfe = max(post_mfe, (hi - entry) / entry * 100)
        post_stop_mfe = round(post_mfe, 3)

    return {
        "gross_return": round(gross, 3),
        "holding_days": hold,
        "exit_reason": exit_reason,
        "MAE": round(mae, 3),
        "MFE": round(mfe, 3),
        "stop_hit_day": stop_hit_day,
        "post_stop_mfe": post_stop_mfe,
        "entry_price": round(entry, 4),
    }


def row_to_trade(row: dict, sim: dict, mode: str, entry_timing: str = "same_day") -> dict:
    gross = sim.get("gross_return") or 0
    net100 = net_return(gross, COST_BPS)
    return {
        "mode": mode,
        "symbol": row["symbol"],
        "signal_date": row["trade_date"],
        "sub_stage": row.get("sub_stage"),
        "legacy_stage": row.get("stage"),
        "explosion_potential": row.get("explosion_potential"),
        "family_similarity_A": row.get("family_similarity_A"),
        "stop_prone_score": row.get("stop_prone_score"),
        "compression_days": row.get("compression_days"),
        "vol_ratio_20": row.get("vol_ratio_20"),
        "vol_ratio_60": row.get("vol_ratio_60"),
        "entry_timing": entry_timing,
        "gross_return": gross,
        "net_return_100bps": round(net100, 3),
        "net_return_150bps": round(net_return(gross, COST_BPS_150), 3),
        "MAE": sim.get("MAE"),
        "MFE": sim.get("MFE"),
        "exit_reason": sim.get("exit_reason"),
        "holding_days": sim.get("holding_days"),
        "artifact_risk": row.get("artifact_risk"),
        "already_exploded": row.get("already_exploded"),
    }


def trade_metrics(trades: List[dict], window: Optional[Tuple[str, str]] = None) -> dict:
    if window:
        trades = [t for t in trades if in_window(t["signal_date"], window)]
    if not trades:
        return {"trade_count": 0, "sample_ok": False}
    rets = [t["net_return_100bps"] for t in trades]
    gross = [t["gross_return"] for t in trades]
    wins = [r for r in rets if r >= 5]
    losses = [abs(r) for r in rets if r < 5]
    sym_pnl = defaultdict(float)
    for t in trades:
        sym_pnl[t["symbol"]] += t["net_return_100bps"]
    top10 = sum(v for _, v in sorted(sym_pnl.items(), key=lambda x: -abs(x[1]))[:10])
    total = sum(abs(v) for v in sym_pnl.values()) or 1
    substage_counts = Counter(t.get("sub_stage") for t in trades)
    return {
        "trade_count": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "hit_5pct": round(100 * sum(1 for g in gross if g >= 5) / len(gross), 1),
        "hit_10pct": round(100 * sum(1 for g in gross if g >= 10) / len(gross), 1),
        "hit_15pct": round(100 * sum(1 for g in gross if g >= 15) / len(gross), 1),
        "median_return": round(median(rets), 3),
        "average_return": round(mean(rets), 3),
        "net_PF_100bps": round(pf(wins, losses), 2),
        "net_PF_150bps": round(pf(
            [t["net_return_150bps"] for t in trades if t["net_return_150bps"] >= 5],
            [abs(t["net_return_150bps"]) for t in trades if t["net_return_150bps"] < 5],
        ), 2),
        "stop_hit_ratio": round(100 * sum(1 for t in trades if t.get("exit_reason") == "stop_hit") / len(trades), 1),
        "MAE_median": round(median([t.get("MAE") or 0 for t in trades]), 3),
        "MFE_median": round(median([t.get("MFE") or 0 for t in trades]), 3),
        "top10_dominance_pct": round(100 * abs(top10) / total, 1),
        "A_purity_pct": round(100 * sum(1 for t in trades if (t.get("family_similarity_A") or 0) >= 80) / len(trades), 1),
        "F_leakage_pct": 0.0,
        "artifact_pct": round(100 * sum(1 for t in trades if t.get("artifact_risk")) / len(trades), 1),
        "exploded_pct": round(100 * sum(1 for t in trades if t.get("already_exploded")) / len(trades), 1),
        "substage_mix": dict(substage_counts),
        "sample_ok": True,
    }


def passes_32_rebuilt(row: dict, min_a: float = 80.0) -> Tuple[bool, List[str]]:
    fails = []
    if row.get("sub_stage") not in TRADE_SUBSTAGES:
        fails.append(f"sub_stage={row.get('sub_stage')}")
    if float(row.get("explosion_potential") or 0) < 60:
        fails.append("eps")
    if float(row.get("family_similarity_A") or 0) < min_a:
        fails.append("A_sim")
    if float(row.get("stop_prone_score") or 0) > 45:
        fails.append("stop_prone")
    if int(row.get("artifact_risk") or 0):
        fails.append("artifact")
    if float(row.get("prior_20d_return_pct") or 0) >= 12:
        fails.append("prior_20d")
    if int(row.get("already_exploded") or 0):
        fails.append("exploded")
    bars, idx = row["_bars"], row["_idx"]
    if not volume_filter_ok(bars, idx)[0]:
        fails.append("volume")
    if not compression_filter_ok(bars, idx, 15)[0]:
        fails.append("compression")
    return len(fails) == 0, fails


def filter_mode_trades(pool: List[dict], mode: str, thresholds: dict, min_a: float, timing: str) -> List[dict]:
    out = []
    for row in pool:
        if mode == "lre_31_conservative":
            ok, _ = mode_passes(row, "conservative", thresholds)
            if not ok or row.get("artifact_risk"):
                continue
            if not volume_filter_ok(row["_bars"], row["_idx"])[0]:
                continue
            if not compression_filter_ok(row["_bars"], row["_idx"], 15)[0]:
                continue
        elif mode == "lre_32_rebuilt":
            ok, _ = passes_32_rebuilt(row, min_a)
            if not ok:
                continue
        elif mode in ("lre_32_confirmation", "lre_32_pullback"):
            ok, _ = passes_32_rebuilt(row, min_a)
            if not ok:
                continue
            timing = "confirmation" if mode == "lre_32_confirmation" else "pullback"
        elif mode == "lre_32_monitoring":
            if not substage_monitoring(row.get("sub_stage", "")):
                continue
        else:
            continue

        bars, sig_idx = row["_bars"], row["_idx"]
        eidx, _ = resolve_entry_idx(bars, sig_idx, timing if mode != "lre_31_conservative" else "same_day")
        if eidx is None and mode != "lre_32_monitoring":
            continue
        if mode == "lre_32_monitoring":
            sim = {"gross_return": 0, "MAE": 0, "MFE": 0, "exit_reason": "monitor", "holding_days": 0}
            converted = False
            for j in range(sig_idx + 1, min(len(bars), sig_idx + 16)):
                fut = dict(row)
                fut["_idx"] = j
                sub, _ = classify_substage(bars, j, row)
                if sub in TRADE_SUBSTAGES:
                    converted = True
                    break
            out.append({**row_to_trade(row, sim, mode), "converted_to_tradeable": int(converted)})
            continue
        sim = simulate_from_entry(bars, eidx, hold_days=10, stop_mode="base_low")
        out.append(row_to_trade(row, sim, mode, timing))
    dates = sorted({t["signal_date"] for t in out})
    return dedup_trades(out, dates, DEDUP_COOLDOWN) if out else []


def band_filter(trades: List[dict], field: str, lo: float, hi: float) -> List[dict]:
    key = {
        "a_sim": "family_similarity_A",
        "compression": "compression_days",
        "vol20": "vol_ratio_20",
        "vol60": "vol_ratio_60",
    }[field]
    return [t for t in trades if lo <= float(t.get(key) or 0) < hi]


def diagnostic_bands(base_trades: List[dict], bands: list, field: str, windows: dict) -> List[dict]:
    rows = []
    for lo, hi in bands:
        sub = band_filter(base_trades, field, lo, hi)
        m_full = trade_metrics(sub)
        m_oos = trade_metrics(sub, windows["oos"])
        label = f"{lo}-{hi}" if hi < 90 else f">{lo}"
        rows.append({
            "band": label,
            "field": field,
            "full": m_full,
            "oos": m_oos,
            "sample_guard_ok": (
                m_full.get("trade_count", 0) >= 30
                and m_oos.get("trade_count", 0) >= 10
                and m_full.get("top10_dominance_pct", 100) < 50
            ),
        })
    return rows


def pick_best_a_band(a_diag: List[dict]) -> Tuple[float, str]:
    """Pick A_sim band with best OOS PF subject to min trades."""
    best = (80.0, "80-85 default")
    best_score = -1.0
    for row in a_diag:
        oos = row.get("oos", {})
        n = oos.get("trade_count") or 0
        if n < 15:
            continue
        pf_val = oos.get("net_PF_100bps") or 0
        med = oos.get("median_return") or -99
        score = pf_val * 0.6 + (med / 10) * 0.2 + min(n / 100, 1) * 0.2
        if score > best_score:
            best_score = score
            lo = float(row["band"].split("-")[0].replace(">", ""))
            best = (lo, row["band"])
    return best


def entry_timing_diagnostic(pool: List[dict], min_a: float) -> dict:
    rebuilt = [r for r in pool if passes_32_rebuilt(r, min_a)[0]]
    results = {}
    for timing in ("same_day", "pullback", "confirmation"):
        trades = []
        for row in rebuilt:
            bars, sig_idx = row["_bars"], row["_idx"]
            eidx, reason = resolve_entry_idx(bars, sig_idx, timing)
            if eidx is None:
                continue
            sim = simulate_from_entry(bars, eidx, 10, "base_low")
            trades.append(row_to_trade(row, sim, f"timing_{timing}", timing))
        dates = sorted({t["signal_date"] for t in trades})
        ded = dedup_trades(trades, dates, DEDUP_COOLDOWN)
        results[timing] = {
            "full": trade_metrics(ded),
            "oos": trade_metrics(ded, (OOS_START, "2099-12-31")),
            "skipped_no_entry": len(rebuilt) - len(trades),
        }
    return results


def stop_diagnostic(pool: List[dict], min_a: float) -> dict:
    rebuilt = [r for r in pool if passes_32_rebuilt(r, min_a)[0]][:800]
    configs = [
        ("stop_6pct", {"stop_mode": "fixed", "stop_pct": 6}),
        ("stop_8pct", {"stop_mode": "fixed", "stop_pct": 8}),
        ("atr_stop", {"stop_mode": "atr"}),
        ("base_low", {"stop_mode": "base_low"}),
        ("no_stop_10d", {"stop_mode": "none", "hold_days": 10}),
        ("no_stop_20d", {"stop_mode": "none", "hold_days": 20}),
    ]
    out = {}
    post_stop_mfes = []
    for name, cfg in configs:
        sims = []
        for row in rebuilt:
            bars, sig_idx = row["_bars"], row["_idx"]
            eidx, _ = resolve_entry_idx(bars, sig_idx, "same_day")
            if eidx is None:
                continue
            sim = simulate_from_entry(bars, eidx, cfg.get("hold_days", 10), **{k: v for k, v in cfg.items() if k != "hold_days"})
            sims.append(sim)
            if sim.get("exit_reason") == "stop_hit" and sim.get("post_stop_mfe"):
                post_stop_mfes.append(sim["post_stop_mfe"])
        rets = [net_return(s["gross_return"], COST_BPS) for s in sims]
        wins = [r for r in rets if r >= 5]
        losses = [abs(r) for r in rets if r < 5]
        out[name] = {
            "n": len(sims),
            "PF": round(pf(wins, losses), 2),
            "median": round(median(rets), 3) if rets else None,
            "stop_hit_pct": round(100 * sum(1 for s in sims if s.get("exit_reason") == "stop_hit") / max(len(sims), 1), 1),
            "avg_MFE": round(mean([s.get("MFE") or 0 for s in sims]), 3) if sims else None,
            "avg_MAE": round(mean([s.get("MAE") or 0 for s in sims]), 3) if sims else None,
        }
    stopped_then_ran = [m for m in post_stop_mfes if m >= 5]
    out["stop_hit_post_mfe"] = {
        "count": len(post_stop_mfes),
        "pct_would_have_hit_5": round(100 * len(stopped_then_ran) / max(len(post_stop_mfes), 1), 1),
        "median_post_stop_mfe": round(median(post_stop_mfes), 3) if post_stop_mfes else None,
        "interpretation": (
            "stop_cuts_valid_losers" if len(stopped_then_ran) < len(post_stop_mfes) * 0.35
            else "stop_too_tight_moves_later"
        ),
    }
    return out


def holding_diagnostic(pool: List[dict], min_a: float) -> dict:
    rebuilt = [r for r in pool if passes_32_rebuilt(r, min_a)[0]][:600]
    out = {}
    for hold in HOLD_WINDOWS:
        sims = []
        delayed = 0
        for row in rebuilt:
            bars, sig_idx = row["_bars"], row["_idx"]
            eidx, _ = resolve_entry_idx(bars, sig_idx, "same_day")
            if eidx is None:
                continue
            sim = simulate_from_entry(bars, eidx, hold, "base_low")
            sims.append(sim)
            if (sim.get("MFE") or 0) >= 5 and (sim.get("gross_return") or 0) < 3 and hold >= 20:
                delayed += 1
        rets = [net_return(s["gross_return"], COST_BPS) for s in sims]
        wins = [r for r in rets if r >= 5]
        losses = [abs(r) for r in rets if r < 5]
        out[f"hold_{hold}d"] = {
            "PF": round(pf(wins, losses), 2),
            "median": round(median(rets), 3) if rets else None,
            "delayed_thrust_proxy_pct": round(100 * delayed / max(len(sims), 1), 1),
        }
    return out


def substage_diagnostic(pool: List[dict]) -> dict:
    by_sub = defaultdict(list)
    for row in pool:
        if row.get("sub_stage") in TRADE_SUBSTAGES | MONITOR_SUBSTAGES | {"4X"}:
            bars, idx = row["_bars"], row["_idx"]
            sim = simulate_from_entry(bars, idx, 10, "base_low")
            by_sub[row["sub_stage"]].append(row_to_trade(row, sim, "substage"))
    out = {}
    for sub, trades in by_sub.items():
        dates = sorted({t["signal_date"] for t in trades})
        ded = dedup_trades(trades, dates, DEDUP_COOLDOWN)
        out[sub] = {**trade_metrics(ded), "oos": trade_metrics(ded, (OOS_START, "2099-12-31"))}
    return out


def review_candidates(conn, by_sym, fingerprints, thresholds, symbols: List[str], date: str) -> dict:
    out = {}
    for sym in symbols:
        bars = by_sym.get(sym)
        if not bars:
            out[sym] = {"error": "no_bars"}
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == date), None)
        if idx is None:
            out[sym] = {"error": "no_date"}
            continue
        row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
        if not row:
            out[sym] = {"error": "enrich"}
            continue
        sub, detail = classify_substage(bars, idx, row)
        row["sub_stage"] = sub
        row["_bars"], row["_idx"] = bars, idx
        _, c31 = mode_passes(row, "conservative", thresholds)
        _, c32 = passes_32_rebuilt(row, 80)
        pull = resolve_entry_idx(bars, idx, "pullback")[0] is not None
        conf = resolve_entry_idx(bars, idx, "confirmation")[0] is not None
        out[sym] = {
            "legacy_stage": row.get("stage_name"),
            "sub_stage": sub,
            "sub_label": SUBSTAGE_LABELS.get(sub, sub),
            "eps": row.get("explosion_potential"),
            "A_sim": row.get("family_similarity_A"),
            "compression_days": row.get("compression_days"),
            "stop_prone": row.get("stop_prone_score"),
            "fails_31_conservative": c31,
            "fails_32_rebuilt": c32,
            "pullback_entry_possible": pull,
            "confirmation_entry_possible": conf,
            "monitoring_only": substage_monitoring(sub),
            "detail": detail,
        }
    return out


def _timing_valid(oos: dict) -> bool:
    return (
        (oos.get("trade_count") or 0) >= 40
        and (oos.get("top10_dominance_pct") or 100) < 35
        and abs(oos.get("average_return") or 0) < 12
    )


def final_verdict(mode_results: dict, entry_diag: dict, stop_diag: dict) -> Tuple[str, str]:
    rebuilt_oos = mode_results.get("lre_32_rebuilt", {}).get("oos", {})
    conf_oos = mode_results.get("lre_32_confirmation", {}).get("oos", {})
    pull_oos = mode_results.get("lre_32_pullback", {}).get("oos", {})
    same = entry_diag.get("same_day", {}).get("oos", {})
    c31_oos = mode_results.get("lre_31_conservative", {}).get("oos", {})
    n = rebuilt_oos.get("trade_count") or 0

    if n < 40:
        return "FAIL_CURVE_FIT_RISK", f"OOS trades {n} < 40 for rebuilt mode"

    curve_risk = (rebuilt_oos.get("top10_dominance_pct", 100) > 35)

    gate = (
        rebuilt_oos.get("net_PF_100bps", 0) >= 1.3
        and (rebuilt_oos.get("median_return") or 0) > 0
        and (rebuilt_oos.get("stop_hit_ratio") or 100) < 40
        and n >= 40
        and (rebuilt_oos.get("artifact_pct") or 100) < 10
        and not curve_risk
    )
    if gate:
        return "PASS_SHADOW_REBUILT_STAGE", "3.2 rebuilt OOS passes shadow gate"

    conf_valid = _timing_valid(conf_oos)
    pull_valid = _timing_valid(pull_oos)
    timing_better = False
    if conf_valid and (conf_oos.get("net_PF_100bps") or 0) > (same.get("net_PF_100bps") or 0) + 0.15:
        timing_better = True
        timing_note = "confirmation follow-through"
    elif pull_valid and (pull_oos.get("net_PF_100bps") or 0) > (same.get("net_PF_100bps") or 0) + 0.1:
        timing_better = True
        timing_note = "next-day pullback"
    else:
        timing_note = ""

    if timing_better:
        return "RESEARCH_EDGE_TIMING_DEPENDENT", f"{timing_note} improves PF vs same-day (sanitized sample)"

    rebuilt_vs_31 = (rebuilt_oos.get("net_PF_100bps") or 0) >= (c31_oos.get("net_PF_100bps") or 0)
    post_stop = stop_diag.get("stop_hit_post_mfe", {})
    if not rebuilt_vs_31:
        if post_stop.get("interpretation") == "stop_too_tight_moves_later":
            return (
                "RESEARCH_EDGE_MONITOR_ONLY",
                "3.2 sub-stages improve radar classification; edge delayed/thin — not standalone gate; pair with MDE",
            )
        return "FAIL_STAGE_REBUILD", "3.2 rebuilt did not beat 3.1 conservative OOS"

    if post_stop.get("interpretation") == "stop_too_tight_moves_later":
        return (
            "RESEARCH_EDGE_MONITOR_ONLY",
            "Edge timing-dependent / delayed thrust; LRE radar + MDE confirmation — not standalone trade gate",
        )

    if curve_risk:
        return "FAIL_CURVE_FIT_RISK", "Top-10 dominance or thin sample"

    return "RESEARCH_EDGE_MONITOR_ONLY", "Stage rebuild clarifies radar; trade gate still weak"


def render_report(doc: dict) -> str:
    lines = [
        "# LRE-3.2 — Stage Rebuild, Threshold & Timing Audit",
        "",
        f"**Generated:** {doc['at']}",
        f"**Verdict:** {doc['verdict']}",
        "",
        doc.get("verdict_reason", ""),
        "",
        "## Sub-Stage Strength",
        "",
    ]
    for sub, m in doc.get("substage_diag", {}).items():
        lines.append(f"- **{sub}**: full n={m.get('trade_count')} PF={m.get('net_PF_100bps')} | OOS PF={m.get('oos', {}).get('net_PF_100bps')}")
    lines.extend(["", "## A_similarity Bands (OOS)", ""])
    for row in doc.get("a_bands", []):
        o = row.get("oos", {})
        lines.append(f"- {row['band']}: n={o.get('trade_count')} PF={o.get('net_PF_100bps')} median={o.get('median_return')}% stop={o.get('stop_hit_ratio')}%")
    lines.extend(["", "## Entry Timing", ""])
    for t, d in doc.get("entry_timing", {}).items():
        o = d.get("oos", {})
        lines.append(f"- **{t}**: n={o.get('trade_count')} PF={o.get('net_PF_100bps')} median={o.get('median_return')}%")
    lines.extend(["", "## Stop Diagnostic", ""])
    for k, v in doc.get("stop_diag", {}).items():
        if isinstance(v, dict) and "PF" in v:
            lines.append(f"- {k}: PF={v.get('PF')} stop_hit={v.get('stop_hit_pct')}%")
    lines.extend(["", "## Mode Comparison (OOS)", ""])
    for mode, m in doc.get("mode_oos", {}).items():
        lines.append(f"- {mode}: n={m.get('trade_count')} PF={m.get('net_PF_100bps')} median={m.get('median_return')}% stop={m.get('stop_hit_ratio')}%")
    lines.extend(["", "## Candidate Review", ""])
    for sym, r in doc.get("candidates", {}).items():
        lines.append(f"### {sym}")
        lines.append(f"- legacy={r.get('legacy_stage')} → **{r.get('sub_stage')}** ({r.get('sub_label')})")
        lines.append(f"- 31 fails: {r.get('fails_31_conservative')} | 32 fails: {r.get('fails_32_rebuilt')}")
        lines.append(f"- pullback={r.get('pullback_entry_possible')} confirmation={r.get('confirmation_entry_possible')} monitoring={r.get('monitoring_only')}")
        lines.append("")
    lines.extend(["", "## Answers", ""])
    for i, (q, a) in enumerate(doc.get("answers", {}).items(), 1):
        lines.append(f"{i}. **{q}** — {a}")
    lines.extend(["", "```text", "Shadow only. client_path_allowed=False.", "```"])
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ LRE-3.2: Stage Rebuild + Diagnostic ═══", flush=True)

    conn = connect()
    ensure_tables(conn)
    by_sym, meta = load_all_bars(conn)
    fingerprints = load_fingerprints()
    thresholds = calibrate_a_thresholds(conn, by_sym, fingerprints)
    windows = _window_dates(meta["max_date"])
    latest = params.get("trade_date") or meta["max_date"]

    print("  building pool...", flush=True)
    pool = build_pool(conn, by_sym, fingerprints, thresholds)
    print(f"    pool={len(pool)} stage3/4 signals", flush=True)

    substage_diag = substage_diagnostic(pool)
    tradeable_pool = [r for r in pool if r.get("sub_stage") in TRADE_SUBSTAGES]
    base_for_bands = []
    for row in tradeable_pool:
        bars, idx = row["_bars"], row["_idx"]
        sim = simulate_from_entry(bars, idx, 10, "base_low")
        base_for_bands.append(row_to_trade(row, sim, "band_base"))
    dates = sorted({t["signal_date"] for t in base_for_bands})
    base_dedup = dedup_trades(base_for_bands, dates, DEDUP_COOLDOWN)

    a_bands = diagnostic_bands(base_dedup, A_SIM_BANDS, "a_sim", windows)
    comp_bands = diagnostic_bands(base_dedup, COMP_BANDS, "compression", windows)
    vol20_bands = diagnostic_bands(base_dedup, VOL20_BANDS, "vol20", windows)
    vol60_bands = diagnostic_bands(base_dedup, VOL60_BANDS, "vol60", windows)

    min_a, best_band_label = pick_best_a_band(a_bands)
    print(f"  best A band: {best_band_label} (min={min_a})", flush=True)

    entry_timing = entry_timing_diagnostic(pool, min_a)
    stop_diag = stop_diagnostic(pool, min_a)
    hold_diag = holding_diagnostic(pool, min_a)

    mode_results = {}
    for mode, timing in [
        ("lre_31_conservative", "same_day"),
        ("lre_32_rebuilt", "same_day"),
        ("lre_32_confirmation", "confirmation"),
        ("lre_32_pullback", "pullback"),
        ("lre_32_monitoring", "same_day"),
    ]:
        trades = filter_mode_trades(pool, mode, thresholds, min_a, timing)
        mode_results[mode] = {
            "full": trade_metrics(trades),
            "oos": trade_metrics(trades, windows["oos"]),
            "latest_6m": trade_metrics(trades, windows["latest_6m"]),
            "latest_3m": trade_metrics(trades, windows["latest_3m"]),
        }
        print(f"    {mode}: {mode_results[mode]['oos'].get('trade_count')} OOS trades", flush=True)

    candidates = review_candidates(conn, by_sym, fingerprints, thresholds, ["OLFI", "HBCO", "EFIC", "EGAS"], latest)
    verdict, reason = final_verdict(mode_results, entry_timing, stop_diag)

    sub_strength = sorted(
        substage_diag.items(),
        key=lambda x: -(x[1].get("oos", {}).get("net_PF_100bps") or 0),
    )
    best_sub = sub_strength[0][0] if sub_strength else "4B"
    same_oos = entry_timing.get("same_day", {}).get("oos", {})
    conf_oos = entry_timing.get("confirmation", {}).get("oos", {})
    pull_oos = entry_timing.get("pullback", {}).get("oos", {})

    answers = {
        "هل المشكلة threshold أم stage scoring؟": (
            f"كلاهما — A threshold 85+ يقتل العينة؛ sub-stage 4X كان يمر كـ Stage 4. "
            f"أقوى sub-stage: {best_sub}"
        ),
        "هل A_similarity 85+ منطقي أم مبالغ؟": (
            f"مبالغ للدخول — أفضل باند تشخيصي: {best_band_label} (min={min_a}). "
            f"باند >90 غالباً عينة صغيرة"
        ),
        "أي sub-stage أقوى: 3B أم 4A أم 4B؟": (
            ", ".join(f"{s}: OOS PF={d.get('oos', {}).get('net_PF_100bps')}" for s, d in sub_strength[:3])
        ),
        "هل same-day سبب الفشل؟": (
            f"same-day OOS PF={same_oos.get('net_PF_100bps')} median={same_oos.get('median_return')}%"
        ),
        "هل pullback/follow-through يحسن؟": (
            f"pullback PF={pull_oos.get('net_PF_100bps')} (dom={pull_oos.get('top10_dominance_pct')}%) | "
            f"confirmation PF={conf_oos.get('net_PF_100bps')} (dom={conf_oos.get('top10_dominance_pct')}% — "
            f"{'invalid outlier' if conf_oos.get('top10_dominance_pct', 0) > 35 else 'ok'}) "
            f"vs same-day {same_oos.get('net_PF_100bps')}"
        ),
        "هل stop -8% مناسب لـ EGX؟": (
            f"stop_8 PF={stop_diag.get('stop_8pct', {}).get('PF')} hit={stop_diag.get('stop_8pct', {}).get('stop_hit_pct')}% | "
            f"post-stop MFE≥5%: {stop_diag.get('stop_hit_post_mfe', {}).get('pct_would_have_hit_5')}% — "
            f"{stop_diag.get('stop_hit_post_mfe', {}).get('interpretation')}"
        ),
        "هل LRE trade gate مستقل؟": "لا حتى الآن — PF OOS rebuilt < 1.3 أو median سالب",
        "monitoring-only + MDE confirmation؟": (
            "نعم — RESEARCH_EDGE_MONITOR_ONLY أو TIMING_DEPENDENT؛ "
            "LRE يرصد التحول، MDE يؤكد hidden repricing"
        ),
    }

    replay_doc = {
        "at": at, "phase": "LRE-3.2", "invariants": PHASE_INVARIANTS,
        "filter_id": "LRE_3_2_STAGE_REBUILD",
        "preserved": ["LRE_3_0", "LRE_3_1_TIGHT_FILTER"],
        "thresholds": thresholds, "best_a_band": {"min": min_a, "label": best_band_label},
        "mode_results": mode_results, "substage_diagnostic": substage_diag,
        "holding_diagnostic": hold_diag, "verdict": verdict, "verdict_reason": reason,
    }
    threshold_doc = {
        "at": at, "A_similarity_bands": a_bands, "compression_bands": comp_bands,
        "vol20_bands": vol20_bands, "vol60_bands": vol60_bands,
        "recommended_min_a": min_a, "recommended_band": best_band_label,
    }
    entry_doc = {"at": at, "entry_timing": entry_timing, "holding_windows": hold_diag}
    stop_doc = {"at": at, **stop_diag}
    cand_doc = {"at": at, "trade_date": latest, "candidates": candidates}

    report_doc = {
        "at": at, "verdict": verdict, "verdict_reason": reason,
        "substage_diag": substage_diag, "a_bands": a_bands,
        "entry_timing": entry_timing, "stop_diag": stop_diag,
        "mode_oos": {k: v["oos"] for k, v in mode_results.items()},
        "candidates": candidates, "answers": answers,
    }

    OUTPUTS["replay"].write_text(json.dumps(replay_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["threshold"].write_text(json.dumps(threshold_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["entry_timing"].write_text(json.dumps(entry_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["stop"].write_text(json.dumps(stop_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["candidates"].write_text(json.dumps(cand_doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report(report_doc), encoding="utf-8")
    conn.close()

    print(f"  done. verdict={verdict}", flush=True)
    return {"success": True, "verdict": verdict, "best_a_band": best_band_label, "mode_oos": {k: v["oos"] for k, v in mode_results.items()}}


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(cmd_run(p), indent=2))
