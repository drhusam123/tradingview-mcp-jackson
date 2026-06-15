#!/usr/bin/env python3
"""LRE-3.4 confluence robustness primitives — dominance detox, bootstrap, LOO."""
from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from mde_client_grade_edge_validation import net_return
from mde_walkforward_shadow import pf


def trade_net(trade: dict, cost_bps: int = 100) -> float:
    return net_return(trade.get("gross_return") or 0, cost_bps)


def symbol_pnl(trades: List[dict], cost_bps: int = 100) -> Dict[str, float]:
    out: Dict[str, float] = defaultdict(float)
    for t in trades:
        out[t["symbol"]] += trade_net(t, cost_bps)
    return dict(out)


def top10_dominance(trades: List[dict], cost_bps: int = 100) -> float:
    sym = symbol_pnl(trades, cost_bps)
    if not sym:
        return 0.0
    top10 = sum(v for _, v in sorted(sym.items(), key=lambda x: -abs(x[1]))[:10])
    total = sum(abs(v) for v in sym.values()) or 1
    return round(100 * abs(top10) / total, 1)


def confluence_metrics(
    trades: List[dict],
    cost_bps: int = 100,
) -> dict:
    if not trades:
        return {"trade_count": 0, "sample_ok": False}
    nets = [trade_net(t, cost_bps) for t in trades]
    gross = [t.get("gross_return") or 0 for t in trades]
    wins = [r for r in nets if r >= 5]
    losses = [abs(r) for r in nets if r < 5]
    sym = symbol_pnl(trades, cost_bps)
    return {
        "trade_count": len(trades),
        "net_PF_100bps": round(pf(wins, losses), 2) if cost_bps == 100 else None,
        "net_PF": round(pf(wins, losses), 2),
        "net_PF_150bps": round(
            pf(
                [trade_net(t, 150) for t in trades if trade_net(t, 150) >= 5],
                [abs(trade_net(t, 150)) for t in trades if trade_net(t, 150) < 5],
            ),
            2,
        ) if cost_bps == 100 else None,
        "median_return": round(median(nets), 3),
        "average_return": round(mean(nets), 3),
        "hit_5pct": round(100 * sum(1 for g in gross if g >= 5) / len(gross), 1),
        "hit_10pct": round(100 * sum(1 for g in gross if g >= 10) / len(gross), 1),
        "stop_hit_ratio": round(
            100 * sum(1 for t in trades if t.get("exit_reason") == "stop_hit") / len(trades), 1
        ),
        "MFE_median": round(median([t.get("MFE") or 0 for t in trades]), 3),
        "MAE_median": round(median([t.get("MAE") or 0 for t in trades]), 3),
        "top10_dominance_pct": top10_dominance(trades, cost_bps),
        "symbol_count": len(sym),
        "sample_ok": True,
        "cost_bps": cost_bps,
    }


def _top_symbols_by_pnl(trades: List[dict], n: int, cost_bps: int = 100) -> List[str]:
    sym = symbol_pnl(trades, cost_bps)
    ranked = sorted(sym.items(), key=lambda x: -abs(x[1]))
    return [s for s, _ in ranked[:n]]


def exclude_symbols(trades: List[dict], exclude: List[str]) -> List[dict]:
    ex = set(exclude)
    return [t for t in trades if t["symbol"] not in ex]


def equal_weight_symbol_metrics(trades: List[dict], cost_bps: int = 100) -> dict:
    """Average per-symbol PF and median, then aggregate."""
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)
    if not by_sym:
        return {"trade_count": 0, "sample_ok": False}
    sym_pfs, sym_meds, sym_hits = [], [], []
    for sym, ts in by_sym.items():
        m = confluence_metrics(ts, cost_bps)
        if m.get("sample_ok"):
            sym_pfs.append(m["net_PF"])
            sym_meds.append(m["median_return"])
            sym_hits.append(m["hit_5pct"])
    return {
        "trade_count": len(trades),
        "symbol_count": len(by_sym),
        "net_PF": round(mean(sym_pfs), 2) if sym_pfs else None,
        "median_return": round(median(sym_meds), 3) if sym_meds else None,
        "hit_5pct": round(mean(sym_hits), 1) if sym_hits else None,
        "method": "equal_weight_per_symbol_mean",
        "per_symbol_PF_median": round(median(sym_pfs), 2) if sym_pfs else None,
        "sample_ok": bool(sym_pfs),
        "top10_dominance_pct": top10_dominance(trades, cost_bps),
    }


def cap_contribution_metrics(
    trades: List[dict],
    cap_pct: float,
    group_key: str,
    cost_bps: int = 100,
) -> dict:
    """Cap positive PnL contribution per group (symbol or sector) at cap_pct of total wins."""
    groups: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        groups[t.get(group_key) or t["symbol"]].append(trade_net(t, cost_bps))

    pos_by_group = {g: sum(r for r in rs if r > 0) for g, rs in groups.items()}
    total_pos = sum(pos_by_group.values()) or 1e-9
    cap_amt = total_pos * cap_pct

    capped_wins = 0.0
    losses = 0.0
    for g, rs in groups.items():
        group_pos = sum(r for r in rs if r > 0)
        group_neg = sum(abs(r) for r in rs if r < 0)
        if group_pos > 0:
            capped_wins += min(group_pos, cap_amt)
        losses += group_neg

    capped_pf = round(capped_wins / max(losses, 1e-9), 2)
    nets = [trade_net(t, cost_bps) for t in trades]
    return {
        "trade_count": len(trades),
        "net_PF": capped_pf,
        "median_return": round(median(nets), 3),
        "hit_5pct": round(100 * sum(1 for t in trades if (t.get("gross_return") or 0) >= 5) / len(trades), 1),
        "stop_hit_ratio": round(
            100 * sum(1 for t in trades if t.get("exit_reason") == "stop_hit") / len(trades), 1
        ),
        "MFE_median": round(median([t.get("MFE") or 0 for t in trades]), 3),
        "MAE_median": round(median([t.get("MAE") or 0 for t in trades]), 3),
        "top10_dominance_pct": top10_dominance(trades, cost_bps),
        "method": f"cap_{group_key}_{int(cap_pct * 100)}pct_wins",
        "sample_ok": True,
        "cost_bps": cost_bps,
    }


def dominance_detox_suite(trades: List[dict], cost_bps: int = 100) -> dict:
    raw = confluence_metrics(trades, cost_bps)
    raw["label"] = "raw_confluence"
    out = {"raw": raw}
    for n, label in [(1, "exclude_top_1"), (3, "exclude_top_3"), (5, "exclude_top_5"), (10, "exclude_top_10")]:
        ex = _top_symbols_by_pnl(trades, n, cost_bps)
        filtered = exclude_symbols(trades, ex)
        m = confluence_metrics(filtered, cost_bps)
        m["label"] = label
        m["excluded_symbols"] = ex
        out[label] = m
    out["equal_weight_per_symbol"] = equal_weight_symbol_metrics(trades, cost_bps)
    out["cap_symbol_10pct"] = cap_contribution_metrics(trades, 0.10, "symbol", cost_bps)
    out["cap_sector_25pct"] = cap_contribution_metrics(trades, 0.25, "sector", cost_bps)
    return out


def leave_one_symbol_out(trades: List[dict], cost_bps: int = 100) -> dict:
    symbols = sorted({t["symbol"] for t in trades})
    baseline = confluence_metrics(trades, cost_bps)
    rows = []
    for sym in symbols:
        filtered = [t for t in trades if t["symbol"] != sym]
        m = confluence_metrics(filtered, cost_bps)
        sym_trades = [t for t in trades if t["symbol"] == sym]
        sym_m = confluence_metrics(sym_trades, cost_bps)
        pnl = symbol_pnl(trades, cost_bps).get(sym, 0)
        rows.append({
            "symbol": sym,
            "trades_removed": len(sym_trades),
            "symbol_pnl_contribution": round(pnl, 3),
            "symbol_PF": sym_m.get("net_PF"),
            "symbol_median": sym_m.get("median_return"),
            "PF_without": m.get("net_PF"),
            "median_without": m.get("median_return"),
            "hit_5pct_without": m.get("hit_5pct"),
            "stop_without": m.get("stop_hit_ratio"),
            "PF_delta": round((m.get("net_PF") or 0) - (baseline.get("net_PF") or 0), 3),
            "median_delta": round((m.get("median_return") or 0) - (baseline.get("median_return") or 0), 3),
            "dominance_without": m.get("top10_dominance_pct"),
        })
    rows.sort(key=lambda x: -abs(x["symbol_pnl_contribution"]))
    creates_pf = [r for r in rows if r["symbol_pnl_contribution"] > 0][:10]
    destroys_pf = [r for r in rows if r["PF_delta"] > 0.05][:10]
    improves = [r for r in rows if r["PF_delta"] > 0.1]
    collapses = [r for r in rows if (r["PF_without"] or 0) < 1.0 and (baseline.get("net_PF") or 0) >= 1.3]
    return {
        "baseline": baseline,
        "by_symbol": rows,
        "top_contributors": creates_pf,
        "removal_improves_edge": improves,
        "removal_collapses_edge": collapses,
        "symbols_that_destroy_pf": destroys_pf,
    }


def leave_one_sector_out(trades: List[dict], cost_bps: int = 100) -> dict:
    sectors = sorted({t.get("sector") or "Unknown" for t in trades})
    baseline = confluence_metrics(trades, cost_bps)
    rows = []
    for sec in sectors:
        filtered = [t for t in trades if (t.get("sector") or "Unknown") != sec]
        m = confluence_metrics(filtered, cost_bps)
        sec_trades = [t for t in trades if (t.get("sector") or "Unknown") == sec]
        sec_pnl = sum(trade_net(t, cost_bps) for t in sec_trades)
        rows.append({
            "sector": sec,
            "trades_in_sector": len(sec_trades),
            "sector_pnl": round(sec_pnl, 3),
            "PF_without": m.get("net_PF"),
            "median_without": m.get("median_return"),
            "stop_without": m.get("stop_hit_ratio"),
            "dominance_without": m.get("top10_dominance_pct"),
            "PF_delta": round((m.get("net_PF") or 0) - (baseline.get("net_PF") or 0), 3),
        })
    rows.sort(key=lambda x: -abs(x["sector_pnl"]))
    return {
        "baseline": baseline,
        "by_sector": rows,
        "sector_concentrated": any(abs(r["sector_pnl"]) > 0.35 * sum(abs(x["sector_pnl"]) for x in rows) for r in rows),
    }


def max_drawdown_proxy(trades: List[dict], cost_bps: int = 100) -> float:
    ordered = sorted(trades, key=lambda t: t["signal_date"])
    cum = peak = 0.0
    max_dd = 0.0
    for t in ordered:
        cum += trade_net(t, cost_bps)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return round(max_dd, 3)


def bootstrap_confluence(
    trades: List[dict],
    n_runs: int = 1000,
    cost_bps: int = 100,
    seed: int = 42,
) -> dict:
    if len(trades) < 5:
        return {"error": "insufficient_trades", "n": len(trades)}
    rng = random.Random(seed)
    pfs, meds, hits, dds = [], [], [], []
    for _ in range(n_runs):
        sample = [trades[rng.randrange(len(trades))] for _ in range(len(trades))]
        m = confluence_metrics(sample, cost_bps)
        pfs.append(m["net_PF"])
        meds.append(m["median_return"])
        hits.append(m["hit_5pct"])
        dds.append(max_drawdown_proxy(sample, cost_bps))

    pfs.sort()
    meds.sort()
    hits.sort()

    def pct(arr, p):
        i = int(len(arr) * p / 100)
        return arr[min(i, len(arr) - 1)]

    return {
        "n_runs": n_runs,
        "sample_size": len(trades),
        "PF": {
            "p10": round(pct(pfs, 10), 2),
            "p25": round(pct(pfs, 25), 2),
            "median": round(pct(pfs, 50), 2),
            "p75": round(pct(pfs, 75), 2),
            "p90": round(pct(pfs, 90), 2),
        },
        "median_return": {
            "p10": round(pct(meds, 10), 3),
            "p25": round(pct(meds, 25), 3),
            "median": round(pct(meds, 50), 3),
            "p75": round(pct(meds, 75), 3),
            "p90": round(pct(meds, 90), 3),
        },
        "hit_5pct": {
            "p10": round(pct(hits, 10), 1),
            "median": round(pct(hits, 50), 1),
            "p90": round(pct(hits, 90), 1),
        },
        "max_drawdown_proxy": {
            "median": round(median(dds), 3),
            "p10": round(pct(sorted(dds), 10), 3),
        },
        "prob_PF_gt_1": round(100 * sum(1 for x in pfs if x > 1.0) / n_runs, 1),
        "prob_PF_gt_1_3": round(100 * sum(1 for x in pfs if x > 1.3) / n_runs, 1),
        "prob_median_gt_0": round(100 * sum(1 for x in meds if x > 0) / n_runs, 1),
        "prob_hit_5pct_gt_40": round(100 * sum(1 for x in hits if x > 40) / n_runs, 1),
    }
