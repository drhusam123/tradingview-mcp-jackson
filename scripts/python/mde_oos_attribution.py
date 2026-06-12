#!/usr/bin/env python3
"""
MDE Phase 2 — OOS shadow attribution for mde_* atoms only.

Additive discovery supplier: never penalizes, vetoes, or blocks legacy signals.
Writes discovery_mde_manifest.json + mde_shadow_attribution_last.json
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
MDE_MANIFEST_PATH = DATA / "discovery_mde_manifest.json"
ATTRIBUTION_PATH = DATA / "mde_shadow_attribution_last.json"

# MDE-only gates (stricter than global fabric PF 1.1)
MDE_GATES = {
    "research": {"min_n": 40, "min_lift": 1.03, "min_pf": 1.15},
    "production_boost": {"min_n": 50, "min_lift": 1.07, "min_pf": 1.25, "max_avg_loss_pct": 8.0},
    "client_influence": {"min_n": 80, "min_lift": 1.10, "min_pf": 1.40, "min_sectors": 3},
}

SETUP_TO_ATOM = {
    "accum_breakout": "mde_accumulation_breakout",
    "pullback_accum": "mde_pullback_accum",
    "failed_breakdown": "mde_failed_breakdown_spring",
    "sector_follower": "mde_sector_follower",
    "absorption_pre_break": "mde_absorption_before_breakout",
    "impact_expansion": "mde_impact_expansion_candidate",
}

MDE_ATOM_IDS = set(SETUP_TO_ATOM.values()) | {
    "mde_institutional_discovery",
    "mde_hidden_repricing",
    "mde_high_effective",
}


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
    )


def atom_applies(row: dict, atom_id: str) -> bool:
    setups = row.get("setups") or []
    stage = row.get("mde_stage")
    if atom_id == "mde_institutional_discovery":
        return stage == "INSTITUTIONAL_DISCOVERY" and row.get("gates_pass")
    if atom_id == "mde_hidden_repricing":
        return bool(row.get("hidden_repricing"))
    if atom_id == "mde_high_effective":
        return float(row.get("effective_score") or 0) >= 70
    for setup_key, aid in SETUP_TO_ATOM.items():
        if aid == atom_id:
            return setup_key in setups
    return False


def load_forward_hits(conn: sqlite3.Connection, by_sym: dict) -> Dict[tuple, dict]:
    """symbol, date -> {hit, realized} using outcomes table or 5d forward return."""
    out: Dict[tuple, dict] = {}
    if table_exists(conn, "recommendation_outcomes"):
        for r in conn.execute(
            """
            SELECT symbol, signal_date AS d, hit_t5, return_t5
            FROM recommendation_outcomes
            WHERE outcome_filled >= 5
            """
        ).fetchall():
            ret = float(r["return_t5"] or 0)
            out[(r["symbol"], r["d"])] = {
                "hit": int(r["hit_t5"] or 0),
                "realized": ret,
            }
    # Fill gaps from OHLCV 5d forward +5%
    for sym, bars in by_sym.items():
        if sym.startswith("EGX"):
            continue
        for i in range(len(bars) - 5):
            d = bars[i]["date"]
            if (sym, d) in out:
                continue
            c0, c5 = bars[i]["close"], bars[i + 5]["close"]
            if c0 > 0:
                ret = c5 / c0 - 1
                out[(sym, d)] = {"hit": 1 if ret >= 0.05 else 0, "realized": ret}
    return out


def backfill_mde_records(conn, max_dates: int = 90) -> List[dict]:
    from egx_market_discovery_engine import (
        compute_bench_returns,
        compute_symbol_metrics,
        load_aux,
        load_bars,
    )

    by_sym = load_bars(conn)
    if not by_sym:
        return []
    all_dates = sorted({b["date"] for bars in by_sym.values() for b in bars})
    dates = all_dates[-max_dates:]
    records: List[dict] = []
    for d in dates:
        aux = load_aux(conn, d)
        bench = compute_bench_returns(by_sym, d, aux["sector"])
        for sym, bars in by_sym.items():
            if sym.startswith("EGX"):
                continue
            row = compute_symbol_metrics(sym, bars, d, aux, bench)
            if row:
                records.append(row)
    return records


def eval_atom_oos(records: List[dict], outcomes: dict, atom_id: str, split_date: str) -> Optional[dict]:
    oos = [r for r in records if r["trade_date"] >= split_date]
    if len(oos) < 20:
        return None
    base_hits = [outcomes.get((r["symbol"], r["trade_date"]), {}).get("hit", 0) for r in oos]
    base_wr = sum(base_hits) / len(base_hits) if base_hits else 0.0

    sub = [r for r in oos if atom_applies(r, atom_id)]
    if len(sub) < 10:
        return None
    hits = []
    wins, losses = [], []
    sectors_hit = defaultdict(int)
    for r in sub:
        o = outcomes.get((r["symbol"], r["trade_date"]), {"hit": 0, "realized": 0.0})
        h = int(o.get("hit") or 0)
        hits.append(h)
        realized = float(o.get("realized") or 0.0)
        if h:
            wins.append(realized)
        else:
            losses.append(abs(realized))
        sec = (r.get("metrics") or {}).get("sector", "Unknown")
        if h:
            sectors_hit[sec] += 1

    wr = sum(hits) / len(hits)
    pf = (sum(wins) / max(sum(losses), 1e-9)) if losses else (2.0 if wins else 0.0)
    lift = wr / base_wr if base_wr > 0 else 0.0
    avg_loss_pct = (sum(losses) / len(losses) * 100) if losses else 0.0

    return {
        "atom_id": atom_id,
        "backtest_n": len(sub),
        "backtest_wr": round(wr * 100, 1),
        "backtest_lift": round(lift, 3),
        "backtest_pf": round(pf, 2),
        "baseline_wr": round(base_wr * 100, 1),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "n_sectors_hit": len(sectors_hit),
        "sectors_hit": dict(sectors_hit),
    }


def classify_tier(metrics: dict) -> str:
    n = metrics["backtest_n"]
    lift = metrics["backtest_lift"]
    pf = metrics["backtest_pf"]
    g = MDE_GATES

    if (
        n >= g["client_influence"]["min_n"]
        and lift >= g["client_influence"]["min_lift"]
        and pf >= g["client_influence"]["min_pf"]
        and metrics.get("n_sectors_hit", 0) >= g["client_influence"]["min_sectors"]
    ):
        return "client_influence"

    if (
        n >= g["production_boost"]["min_n"]
        and lift >= g["production_boost"]["min_lift"]
        and pf >= g["production_boost"]["min_pf"]
        and metrics.get("avg_loss_pct", 99) <= g["production_boost"]["max_avg_loss_pct"]
    ):
        return "production_boost"

    if (
        n >= g["research"]["min_n"]
        and lift >= g["research"]["min_lift"]
        and pf >= g["research"]["min_pf"]
    ):
        return "research"

    return "rejected"


def build_shadow_comparison(conn, trade_date: str, records: List[dict]) -> dict:
    mde_top = {
        r["symbol"]
        for r in records
        if r["trade_date"] == trade_date
        and r["mde_stage"] in ("INSTITUTIONAL_DISCOVERY", "WATCH_TO_BUY")
        and float(r.get("effective_score") or 0) >= 60
    }
    mde_hidden = {
        r["symbol"]
        for r in records
        if r["trade_date"] == trade_date and r.get("hidden_repricing")
    }

    opp_actionable, opp_all = set(), set()
    if table_exists(conn, "opportunity_score_v2"):
        for r in conn.execute(
            """
            SELECT symbol, stage FROM opportunity_score_v2 WHERE trade_date=?
            """,
            (trade_date,),
        ).fetchall():
            opp_all.add(r["symbol"])
            if r["stage"] in ("ACTIONABLE_CANDIDATE", "QUALIFIED_DISCOVERY"):
                opp_actionable.add(r["symbol"])

    fs_actionable = set()
    if table_exists(conn, "final_signals"):
        for r in conn.execute(
            """
            SELECT symbol FROM final_signals
            WHERE trade_date=? AND actionable=1
            """,
            (trade_date,),
        ).fetchall():
            fs_actionable.add(r["symbol"])

    would_add_opp = sorted(mde_top - opp_actionable)
    would_add_client = sorted(mde_top - fs_actionable)
    mde_only_hidden = sorted(mde_hidden - opp_all)

    return {
        "trade_date": trade_date,
        "mde_candidates": len(mde_top),
        "mde_hidden_repricing": len(mde_hidden),
        "opp_actionable": len(opp_actionable),
        "final_actionable": len(fs_actionable),
        "would_add_vs_opp_actionable": would_add_opp[:25],
        "would_add_vs_final_actionable": would_add_client[:25],
        "mde_hidden_not_in_opp_universe": mde_only_hidden[:25],
        "overlap_mde_opp": len(mde_top & opp_actionable),
        "additive_only": True,
        "note": "MDE never removes or downgrades existing signals",
    }


def update_registry_metrics(conn, atom_id: str, metrics: dict, tier: str) -> None:
    status = "validated" if tier != "rejected" else "rejected"
    conn.execute(
        """
        UPDATE discovery_atom_registry SET
          status=?,
          backtest_wr=?,
          backtest_n=?,
          backtest_lift=?,
          backtest_pf=?,
          boost_weight=CASE WHEN ?='validated' THEN 1.06 ELSE boost_weight END,
          penalize_weight=1.0,
          hard_negative=0,
          validated_at=CASE WHEN ?='validated' THEN datetime('now') ELSE validated_at END,
          updated_at=datetime('now')
        WHERE atom_id=? AND (regime_filter='' OR regime_filter IS NULL)
        """,
        (
            status,
            metrics["backtest_wr"],
            metrics["backtest_n"],
            metrics["backtest_lift"],
            metrics["backtest_pf"],
            status,
            status,
            atom_id,
        ),
    )


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    max_dates = int(params.get("max_dates") or 90)
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row

    if not table_exists(conn, "egx_market_discovery_daily"):
        conn.close()
        return {"success": False, "error": "egx_market_discovery_daily missing — run MDE Phase 1 first"}

    records = backfill_mde_records(conn, max_dates=max_dates)
    if not records:
        conn.close()
        return {"success": False, "error": "no MDE backfill records"}

    dates = sorted({r["trade_date"] for r in records})
    split_date = dates[int(len(dates) * 0.75)] if len(dates) >= 8 else dates[0]
    trade_date = dates[-1]

    from egx_market_discovery_engine import load_bars

    by_sym = load_bars(conn)
    outcomes = load_forward_hits(conn, by_sym)

    atom_results: List[dict] = []
    watch, boost, priority, rejected = [], [], [], []

    for atom_id in sorted(MDE_ATOM_IDS):
        metrics = eval_atom_oos(records, outcomes, atom_id, split_date)
        if not metrics:
            rejected.append(atom_id)
            atom_results.append({"atom_id": atom_id, "tier": "rejected", "reason": "insufficient_oos_data"})
            continue
        tier = classify_tier(metrics)
        metrics["tier"] = tier
        atom_results.append(metrics)
        update_registry_metrics(conn, atom_id, metrics, tier)
        if tier == "research":
            watch.append(atom_id)
        elif tier == "production_boost":
            boost.append(atom_id)
        elif tier == "client_influence":
            priority.append(atom_id)
        else:
            rejected.append(atom_id)

    conn.commit()

    mde_manifest = {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow",
        "additive_only": True,
        "egx_mde_opp_boost": os.environ.get("EGX_MDE_OPP_BOOST", "0"),
        "gates": MDE_GATES,
        "oos_split_date": split_date,
        "mde_watch_atoms": watch,
        "mde_boost_atoms": boost,
        "mde_priority_atoms": priority,
        "mde_rejected_atoms": rejected,
        "atom_attribution": atom_results,
        "policy": {
            "hard_veto": False,
            "penalize_legacy": False,
            "opp_influence": "none_until_EGX_MDE_OPP_BOOST=1_and_production_tier",
        },
    }
    MDE_MANIFEST_PATH.write_text(json.dumps(mde_manifest, indent=2), encoding="utf-8")

    shadow_cmp = build_shadow_comparison(conn, trade_date, records)
    attribution = {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_research",
        "backfill_dates": len(dates),
        "oos_split_date": split_date,
        "summary": {
            "atoms_research": len(watch),
            "atoms_production_boost": len(boost),
            "atoms_client_influence": len(priority),
            "atoms_rejected": len(rejected),
        },
        "shadow_comparison": shadow_cmp,
        "atom_attribution": atom_results,
        "questions": {
            "what_mde_discovered": f"{shadow_cmp['mde_candidates']} top candidates @ {trade_date}",
            "what_would_add_vs_opp": shadow_cmp["would_add_vs_opp_actionable"],
            "what_would_add_vs_client": shadow_cmp["would_add_vs_final_actionable"],
            "hidden_not_in_opp": shadow_cmp["mde_hidden_not_in_opp_universe"],
            "real_lift": [a for a in atom_results if a.get("tier") in ("research", "production_boost", "client_influence")],
            "noise_atoms": [a["atom_id"] for a in atom_results if a.get("tier") == "rejected"],
        },
    }
    ATTRIBUTION_PATH.write_text(json.dumps(attribution, indent=2), encoding="utf-8")
    conn.close()

    return {
        "success": True,
        "manifest": str(MDE_MANIFEST_PATH.relative_to(ROOT)),
        "attribution": str(ATTRIBUTION_PATH.relative_to(ROOT)),
        "summary": attribution["summary"],
        "shadow_comparison": shadow_cmp,
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
