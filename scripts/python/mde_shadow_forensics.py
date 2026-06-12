#!/usr/bin/env python3
"""
MDE Phase 2.5 — Shadow Results Forensics.

Dissects MDE coverage, discoveries, persistence, overlap, atom concentration,
family patterns, biases, and pre-Phase-3 recommendations.

Outputs:
  data/mde_shadow_forensics_last.json
  docs/MDE_SHADOW_FORENSICS_REPORT.md
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"
JSON_OUT = DATA / "mde_shadow_forensics_last.json"
MD_OUT = ROOT / "docs" / "MDE_SHADOW_FORENSICS_REPORT.md"

MIN_BARS = 80
LIQUIDITY_GATE_EGP = 2_000_000
SETUP_TO_ATOM = {
    "accum_breakout": "mde_accumulation_breakout",
    "pullback_accum": "mde_pullback_accum",
    "failed_breakdown": "mde_failed_breakdown_spring",
    "sector_follower": "mde_sector_follower",
    "absorption_pre_break": "mde_absorption_before_breakout",
    "impact_expansion": "mde_impact_expansion_candidate",
}
FOCUS_ATOMS = [
    "mde_hidden_repricing",
    "mde_impact_expansion_candidate",
    "mde_sector_follower",
    "mde_absorption_before_breakout",
]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    return conn


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def cap_bucket(turnover_20d: float) -> str:
    if turnover_20d >= 50_000_000:
        return "large"
    if turnover_20d >= 10_000_000:
        return "mid"
    if turnover_20d >= LIQUIDITY_GATE_EGP:
        return "small"
    return "micro"


def infer_hr_reasons(metrics: dict) -> List[str]:
    """Approximate hidden_repricing signal labels from stored metrics."""
    reasons = []
    rel_turn = float(metrics.get("rel_turn") or 0)
    ret_d = abs(float(metrics.get("return_d") or 0))
    clv = float(metrics.get("clv") or 0)
    csf = float(metrics.get("csf_20") or 0)
    impact = float(metrics.get("impact_expansion") or 0)
    kyle = float(metrics.get("kyle_lambda") or 0)
    rs = float(metrics.get("rs_20") or 0)
    n_sig = int(metrics.get("hidden_repricing_signals") or 0)
    if n_sig < 2:
        return reasons
    if impact > 1.15 and csf > 0:
        reasons.append("impact_expansion+csf")
    if rel_turn > 1.3 and clv > 0.55 and ret_d < 0.05:
        reasons.append("volume_without_move")
    if kyle > 0 and rel_turn > 1.2:
        reasons.append("kyle_lambda_elevated")
    if csf > 0 and rs >= 0:
        reasons.append("positive_csf_rs")
    if clv > 0.6 and rel_turn > 1.2:
        reasons.append("close_strength_absorption")
    if not reasons:
        reasons.append(f"multi_signal_n={n_sig}")
    return reasons


def norm_return(v: Any) -> float:
    """Normalize mixed decimal / percent storage in outcomes."""
    x = float(v or 0)
    if abs(x) > 1.5:
        return x / 100.0
    return x


def load_forward_returns(conn, by_sym: dict) -> Dict[tuple, dict]:
    out: Dict[tuple, dict] = {}
    if table_exists(conn, "recommendation_outcomes"):
        for r in conn.execute(
            """
            SELECT symbol, signal_date AS d, hit_t5, return_t5, return_t10
            FROM recommendation_outcomes
            WHERE outcome_filled >= 5
            """
        ).fetchall():
            out[(r["symbol"], r["d"])] = {
                "hit": int(r["hit_t5"] or 0),
                "ret5": norm_return(r["return_t5"]),
                "ret10": norm_return(r["return_t10"]) if r["return_t10"] is not None else None,
            }
    for sym, bars in by_sym.items():
        if sym.startswith("EGX"):
            continue
        for i in range(len(bars) - 10):
            d = bars[i]["date"]
            if (sym, d) in out:
                continue
            c0, c5, c10 = bars[i]["close"], bars[i + 5]["close"], bars[i + 10]["close"]
            if c0 > 0:
                ret5 = c5 / c0 - 1
                ret10 = c10 / c0 - 1
                out[(sym, d)] = {
                    "hit": 1 if ret5 >= 0.05 else 0,
                    "ret5": ret5,
                    "ret10": ret10,
                }
    return out


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


def consecutive_streak(dates: List[str]) -> int:
    if not dates:
        return 0
    from datetime import date

    ds = sorted(date.fromisoformat(d) for d in dates)
    best = cur = 1
    for i in range(1, len(ds)):
        if (ds[i] - ds[i - 1]).days == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def analyze_data_coverage(conn, by_sym: dict, trade_date: str) -> dict:
    from egx_market_discovery_engine import load_aux

    universe = {
        r["symbol"] for r in conn.execute("SELECT symbol FROM stock_universe")
    } if table_exists(conn, "stock_universe") else set()
    ohlcv_syms = {s for s in by_sym if not s.startswith("EGX")}
    enough_bars = {s for s, b in by_sym.items() if len(b) >= MIN_BARS and not s.startswith("EGX")}
    mde_scored = {
        r["symbol"]
        for r in conn.execute(
            "SELECT symbol FROM egx_market_discovery_daily WHERE trade_date=?", (trade_date,)
        )
    }
    aux = load_aux(conn, trade_date)
    fin_syms = set(aux["financial"])
    tv_syms = set(aux["tv"])
    pine_syms = set(aux["pine"])
    sector_syms = set(aux["sector"])

    cp_syms: Set[str] = set()
    if table_exists(conn, "closing_pressure_daily"):
        cp_syms = {
            r["symbol"]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM closing_pressure_daily WHERE trade_date=?", (trade_date,)
            )
        }

    ohlcv_last = conn.execute(
        "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history"
    ).fetchone()[0]

    excluded = {}
    for sym in ohlcv_syms:
        if sym not in enough_bars:
            excluded[sym] = "insufficient_bars_lt_80"
        elif sym not in mde_scored:
            excluded[sym] = "computed_but_not_stored"

    sources: List[dict] = []

    def row_mde(name, pool, used_in_mde, note=""):
        cov = pct(len(used_in_mde), len(mde_scored)) if mde_scored else 0
        return {
            "data_source": name,
            "available_symbols": len(pool),
            "used_symbols": len(used_in_mde),
            "missing_symbols": len(mde_scored - used_in_mde) if mde_scored else 0,
            "coverage_pct": cov,
            "note": note,
        }

    sources.append(row_mde("ohlcv_history", ohlcv_syms, mde_scored, "full history; MIN_BARS=80 for scoring"))
    sources.append(row_mde("ohlcv_80plus_bars", enough_bars, mde_scored))
    sources.append(row_mde("stock_universe", universe or ohlcv_syms, mde_scored & (universe or ohlcv_syms)))
    sources.append(row_mde("financial_data", fin_syms, mde_scored & fin_syms, "confidence +15 when present"))
    sources.append(row_mde("tv_discovery_features", tv_syms, mde_scored & tv_syms, f"trade_date={trade_date}; +10 confidence"))
    sources.append(row_mde("pine_analytics", pine_syms, mde_scored & pine_syms, f"trade_date={trade_date}; RS input"))
    sources.append(row_mde("sector_stock_universe", sector_syms, mde_scored & sector_syms))
    sources.append(row_mde(
        "closing_pressure_daily", cp_syms, mde_scored & cp_syms, "available but NOT wired into MDE v1"
    ))

    return {
        "db_symbols_total": len(universe) if universe else len(ohlcv_syms),
        "ohlcv_symbols": len(ohlcv_syms),
        "ohlcv_symbols_80plus_bars": len(enough_bars),
        "mde_symbols_scored_latest": len(mde_scored),
        "mde_symbols_excluded": len(ohlcv_syms) - len(mde_scored),
        "exclusion_reasons_sample": dict(list(Counter(excluded.values()).items())[:5]),
        "latest_ohlcv_date": ohlcv_last,
        "latest_mde_trade_date": trade_date,
        "mde_db_snapshot_dates": conn.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM egx_market_discovery_daily"
        ).fetchone()[0],
        "oos_backfill_note": "OOS attribution backfills via compute_symbol_metrics over last N trading dates (not stored in DB except latest run)",
        "ohlcv_scope": "full ohlcv_history per symbol; attribution OOS window defaults to 60 trading dates",
        "fundamentals_coverage_pct": pct(len(mde_scored & fin_syms), len(mde_scored)),
        "tv_coverage_pct": pct(len(mde_scored & tv_syms), len(mde_scored)),
        "pine_coverage_pct": pct(len(mde_scored & pine_syms), len(mde_scored)),
        "sector_coverage_pct": pct(len(mde_scored & sector_syms), len(mde_scored)),
        "data_source_table": sources,
    }


def load_latest_mde_rows(conn, trade_date: Optional[str] = None) -> tuple[str, List[dict]]:
    if not trade_date:
        trade_date = conn.execute("SELECT MAX(trade_date) FROM egx_market_discovery_daily").fetchone()[0]
    rows = []
    for r in conn.execute(
        "SELECT * FROM egx_market_discovery_daily WHERE trade_date=?", (trade_date,)
    ).fetchall():
        d = dict(r)
        try:
            d["setups"] = json.loads(d.get("setups_json") or "[]")
        except json.JSONDecodeError:
            d["setups"] = []
        try:
            d["metrics"] = json.loads(d.get("metrics_json") or "{}")
        except json.JSONDecodeError:
            d["metrics"] = {}
        try:
            d["gates"] = json.loads(d.get("gates_passed_json") or "{}")
        except json.JSONDecodeError:
            d["gates"] = {}
        rows.append(d)
    return trade_date, rows


def enrich_symbol_row(conn, row: dict, trade_date: str, opp_map: dict, fs_map: dict, liq_map: dict) -> dict:
    sym = row["symbol"]
    m = row.get("metrics") or {}
    sector = m.get("sector") or row.get("sector")
    turnover = float(m.get("turnover") or 0)
    rel_turn = float(m.get("rel_turn") or 0)
    avg_turn_20d = turnover / rel_turn if rel_turn > 0 else turnover

    opp = opp_map.get(sym, {})
    fs = fs_map.get(sym, {})
    liq = liq_map.get(sym, {})

    flags = []
    if opp:
        flags.append("in_opp_universe")
    else:
        flags.append("outside_opp_universe")
    if fs.get("actionable"):
        flags.append("final_actionable")
    if fs:
        flags.append("in_final_signals")
    if not opp and not fs:
        flags.append("mde_only_discovery")

    return {
        "symbol": sym,
        "sector": sector,
        "discovery_score": row.get("discovery_score"),
        "confidence_score": row.get("confidence_score"),
        "effective_score": row.get("effective_score"),
        "mde_stage": row.get("mde_stage"),
        "setups_fired": row.get("setups") or [],
        "pre_explosion_multiplier": row.get("pre_explosion_multiplier"),
        "hidden_repricing": bool(row.get("hidden_repricing")),
        "hidden_repricing_reasons": infer_hr_reasons(m),
        "hidden_repricing_signals": m.get("hidden_repricing_signals"),
        "metrics": {
            "kyle_lambda": m.get("kyle_lambda"),
            "impact_expansion": m.get("impact_expansion"),
            "csf_20": m.get("csf_20"),
            "absorption_ratio": m.get("absorption_ratio"),
            "vpin_proxy": m.get("vpin_proxy"),
            "resilience_score_layer": row.get("resilience_score"),
            "sector_rs_20": m.get("rs_20"),
            "rel_turn": m.get("rel_turn"),
            "return_d": m.get("return_d"),
        },
        "avg_turnover_20d_egp": round(avg_turn_20d, 0),
        "cap_bucket": cap_bucket(avg_turn_20d),
        "liquidity_tier": liq.get("liquidity_tier"),
        "opp_stage": opp.get("stage"),
        "opp_score": opp.get("opportunity_score"),
        "final_setup_type": fs.get("setup_type"),
        "final_actionable": bool(fs.get("actionable")),
        "overlap_flags": flags,
    }


def analyze_persistence(backfill_records: List[dict]) -> dict:
    hr_by_sym: Dict[str, List[str]] = defaultdict(list)
    setups_by_sym: Dict[str, Set[str]] = defaultdict(set)
    for r in backfill_records:
        if r.get("hidden_repricing"):
            hr_by_sym[r["symbol"]].append(r["trade_date"])
        for s in r.get("setups") or []:
            setups_by_sym[r["symbol"]].add(s)

    persistence_rows = []
    one_day = two_three = five_plus = 0
    for sym, dates in sorted(hr_by_sym.items(), key=lambda x: -len(x[1])):
        n = len(set(dates))
        streak = consecutive_streak(list(set(dates)))
        if n == 1:
            one_day += 1
        elif 2 <= n <= 3:
            two_three += 1
        else:
            five_plus += 1
        persistence_rows.append({
            "symbol": sym,
            "days_detected": n,
            "consecutive_days": streak,
            "first_seen": min(dates),
            "last_seen": max(dates),
            "repeated_setups": sorted(setups_by_sym.get(sym, [])),
        })

    dates_in_backfill = sorted({r["trade_date"] for r in backfill_records})
    return {
        "backfill_trading_dates": len(dates_in_backfill),
        "backfill_from": dates_in_backfill[0] if dates_in_backfill else None,
        "backfill_to": dates_in_backfill[-1] if dates_in_backfill else None,
        "hidden_repricing_unique_symbols": len(hr_by_sym),
        "one_day_only": one_day,
        "two_to_three_days": two_three,
        "five_plus_days": five_plus if five_plus else sum(1 for r in persistence_rows if r["days_detected"] >= 4),
        "four_plus_days": sum(1 for r in persistence_rows if r["days_detected"] >= 4),
        "note": "egx_market_discovery_daily stores latest run only; persistence from backfill recompute",
        "symbol_persistence_table": persistence_rows[:50],
    }


def sector_clustering(hidden_rows: List[dict]) -> List[dict]:
    by_sec: Dict[str, List[dict]] = defaultdict(list)
    for r in hidden_rows:
        by_sec[r.get("sector") or "Unknown"].append(r)

    out = []
    for sec, items in sorted(by_sec.items(), key=lambda x: -len(x[1])):
        setups = Counter(s for r in items for s in (r.get("setups_fired") or []))
        out.append({
            "sector": sec,
            "count_hidden_repricing": len(items),
            "avg_effective_score": round(mean(float(r["effective_score"] or 0) for r in items), 2),
            "avg_discovery_score": round(mean(float(r["discovery_score"] or 0) for r in items), 2),
            "dominant_setup": setups.most_common(1)[0][0] if setups else None,
            "avg_turnover_20d": round(mean(float(r.get("avg_turnover_20d_egp") or 0) for r in items), 0),
            "symbols": [r["symbol"] for r in items],
        })
    return out


def overlap_analysis(hidden_syms: Set[str], conn, trade_date: str, hidden_enriched: List[dict]) -> dict:
    opp_all, opp_actionable = set(), set()
    opp_stages = {}
    if table_exists(conn, "opportunity_score_v2"):
        for r in conn.execute(
            "SELECT symbol, stage, opportunity_score, flags_json FROM opportunity_score_v2 WHERE trade_date=?",
            (trade_date,),
        ).fetchall():
            opp_all.add(r["symbol"])
            opp_stages[r["symbol"]] = r["stage"]
            if r["stage"] in ("ACTIONABLE_CANDIDATE", "QUALIFIED_DISCOVERY"):
                opp_actionable.add(r["symbol"])

    fs_all, fs_actionable = set(), set()
    fs_setups = {}
    if table_exists(conn, "final_signals"):
        for r in conn.execute(
            "SELECT symbol, actionable, setup_type FROM final_signals WHERE trade_date=?",
            (trade_date,),
        ).fetchall():
            fs_all.add(r["symbol"])
            fs_setups[r["symbol"]] = r["setup_type"]
            if r["actionable"]:
                fs_actionable.add(r["symbol"])

    outside_opp = sorted(hidden_syms - opp_all)
    mde_only = sorted(hidden_syms - opp_all - fs_all)
    shared_opp = sorted(hidden_syms & opp_all)
    shared_actionable = sorted(hidden_syms & fs_actionable)

    # Legacy setup overlap via opp flags / final setup_type keywords
    legacy_retest = set()
    legacy_vol = set()
    for sym in hidden_syms:
        st = (fs_setups.get(sym) or "").lower()
        if "retest" in st or "low20" in st:
            legacy_retest.add(sym)
        if "vol" in st or "accum" in st:
            legacy_vol.add(sym)

    return {
        "trade_date": trade_date,
        "mde_hidden_repricing_count": len(hidden_syms),
        "opp_universe_count": len(opp_all),
        "opp_actionable_count": len(opp_actionable),
        "final_signals_count": len(fs_all),
        "final_actionable_count": len(fs_actionable),
        "overlap_hidden_vs_opp_universe": len(hidden_syms & opp_all),
        "overlap_hidden_vs_opp_actionable": len(hidden_syms & opp_actionable),
        "overlap_hidden_vs_final_signals": len(hidden_syms & fs_all),
        "overlap_hidden_vs_final_actionable": len(hidden_syms & fs_actionable),
        "mde_only_not_in_opp": outside_opp,
        "mde_only_not_in_any_system": mde_only,
        "mde_new_vs_actionable": sorted(hidden_syms - fs_actionable),
        "shared_with_opp_universe": shared_opp[:25],
        "shared_with_final_actionable": shared_actionable,
        "arab_case": {
            "in_hidden_repricing": "ARAB" in hidden_syms,
            "in_opp_universe": "ARAB" in opp_all,
            "in_final_signals": "ARAB" in fs_all,
            "note": "ARAB is not unique — see mde_only_not_in_opp list",
        },
        "legacy_setup_overlap": {
            "retest_like_in_final_signals": sorted(legacy_retest & hidden_syms),
            "vol_accum_like_in_final_signals": sorted(legacy_vol & hidden_syms),
        },
        "additive_verdict": "MDE adds visibility outside opp universe" if outside_opp else "no outside-opp additions on this date",
    }


def atom_forensics(backfill_records: List[dict], outcomes: dict, split_date: str) -> List[dict]:
    from mde_oos_attribution import classify_tier, MDE_GATES

    oos = [r for r in backfill_records if r["trade_date"] >= split_date]
    base_hits = [outcomes.get((r["symbol"], r["trade_date"]), {}).get("hit", 0) for r in oos]
    base_wr = sum(base_hits) / len(base_hits) if base_hits else 0

    results = []
    for atom_id in FOCUS_ATOMS:
        sub = [r for r in oos if atom_applies(r, atom_id)]
        if len(sub) < 5:
            results.append({"atom": atom_id, "insufficient_data": True, "n": len(sub)})
            continue

        sym_stats: Dict[str, dict] = defaultdict(lambda: {"hits": 0, "n": 0, "rets5": [], "rets10": []})
        sector_hits: Counter = Counter()
        wins, losses, rets5, rets10, dds = [], [], [], [], []

        for r in sub:
            o = outcomes.get((r["symbol"], r["trade_date"]), {"hit": 0, "ret5": 0, "ret10": 0})
            h = int(o.get("hit") or 0)
            r5 = float(o.get("ret5") or 0)
            r10 = float(o.get("ret10") or 0) if o.get("ret10") is not None else None
            sym_stats[r["symbol"]]["n"] += 1
            sym_stats[r["symbol"]]["rets5"].append(r5)
            if h:
                sym_stats[r["symbol"]]["hits"] += 1
            sec = (r.get("metrics") or {}).get("sector", "Unknown")
            if h:
                sector_hits[sec] += 1
            rets5.append(r5)
            if r10 is not None:
                rets10.append(r10)
            if h:
                wins.append(r5)
            else:
                losses.append(abs(r5))
            if r5 < 0:
                dds.append(r5)

        wr = sum(1 for r in sub if outcomes.get((r["symbol"], r["trade_date"]), {}).get("hit")) / len(sub)
        pf = (sum(wins) / max(sum(losses), 1e-9)) if losses else (2.0 if wins else 0)
        lift = wr / base_wr if base_wr > 0 else 0

        top_syms = sorted(
            sym_stats.items(),
            key=lambda x: (-x[1]["hits"], -x[1]["n"]),
        )[:10]
        top_wr_contrib = sorted(sym_stats.items(), key=lambda x: -sum(x[1]["rets5"]))[:5]

        pf_from_top2 = None
        if len(top_wr_contrib) >= 2:
            t2 = top_wr_contrib[:2]
            t2_wins = sum(max(0, mean(s[1]["rets5"])) for s in t2 if s[1]["rets5"])
            t2_loss = sum(abs(min(0, mean(s[1]["rets5"]))) for s in t2 if s[1]["rets5"])
            pf_from_top2 = round(t2_wins / max(t2_loss, 1e-9), 2)

        metrics = {
            "backtest_n": len(sub),
            "backtest_lift": round(lift, 3),
            "backtest_pf": round(pf, 2),
            "hit_rate_pct": round(wr * 100, 1),
            "avg_forward_return_5d_pct": round(mean(rets5) * 100, 2),
            "avg_forward_return_10d_pct": round(mean(rets10) * 100, 2) if rets10 else None,
            "max_drawdown_5d_pct": round(min(rets5) * 100, 2) if rets5 else None,
            "symbols_count": len(sym_stats),
            "sector_distribution": dict(sector_hits),
            "top_symbols": [
                {"symbol": s, "n": d["n"], "hits": d["hits"], "avg_ret5_pct": round(mean(d["rets5"]) * 100, 2)}
                for s, d in top_syms
            ],
            "concentration": {
                "top2_symbols": [s for s, _ in top_wr_contrib[:2]],
                "top2_pf_proxy": pf_from_top2,
                "balanced_across_symbols": len([s for s, d in sym_stats.items() if d["hits"] > 0]) >= 5,
                "balanced_across_sectors": len(sector_hits) >= 3,
                "finance_sector_share_pct": round(
                    100 * sector_hits.get("Finance", 0) / max(sum(sector_hits.values()), 1), 1
                ),
            },
        }
        tier = classify_tier({**metrics, "backtest_wr": metrics["hit_rate_pct"], "n_sectors_hit": len(sector_hits)})
        results.append({"atom": atom_id, "tier": tier, **metrics})

    return results


def classify_families(hidden_enriched: List[dict]) -> List[dict]:
    families = {
        "A_impact_expansion": [],
        "B_sector_follower": [],
        "C_absorption_before_breakout": [],
        "D_pullback_accumulation": [],
        "E_hidden_repricing_multi_signal": [],
        "F_false_weak_discovery": [],
    }
    for r in hidden_enriched:
        setups = set(r.get("setups_fired") or [])
        eff = float(r.get("effective_score") or 0)
        n_sig = int(r.get("hidden_repricing_signals") or 0)
        if "impact_expansion" in setups:
            families["A_impact_expansion"].append(r["symbol"])
        elif "sector_follower" in setups:
            families["B_sector_follower"].append(r["symbol"])
        elif "absorption_pre_break" in setups:
            families["C_absorption_before_breakout"].append(r["symbol"])
        elif "pullback_accum" in setups:
            families["D_pullback_accumulation"].append(r["symbol"])
        elif n_sig >= 3:
            families["E_hidden_repricing_multi_signal"].append(r["symbol"])
        else:
            families["F_false_weak_discovery"].append(r["symbol"])

    labels = {
        "A_impact_expansion": "A) Impact Expansion Family",
        "B_sector_follower": "B) Sector Follower Family",
        "C_absorption_before_breakout": "C) Absorption Before Breakout Family",
        "D_pullback_accumulation": "D) Pullback Accumulation Family",
        "E_hidden_repricing_multi_signal": "E) Hidden Repricing Multi-Signal Family",
        "F_false_weak_discovery": "F) False/Weak Discovery Family",
    }
    out = []
    for key, syms in families.items():
        items = [r for r in hidden_enriched if r["symbol"] in syms]
        if not items:
            continue
        follow = "watch" if key != "F_false_weak_discovery" else "reject_or_gate"
        gate = None
        if key == "A_impact_expansion":
            gate = "require rel_turn>1.0 + liquidity floor"
        elif key == "F_false_weak_discovery":
            gate = "persistence 2+ days"
        out.append({
            "family": labels[key],
            "count": len(syms),
            "examples": syms[:8],
            "common_traits": {
                "avg_effective": round(mean(float(r["effective_score"] or 0) for r in items), 2),
                "avg_impact_expansion": round(
                    mean(float((r.get("metrics") or {}).get("impact_expansion") or 0) for r in items), 3
                ),
                "dominant_cap_bucket": Counter(r.get("cap_bucket") for r in items).most_common(1)[0][0],
            },
            "followable": follow,
            "suggested_gate": gate,
        })
    return out


def risk_traps(hidden_enriched: List[dict], persistence: dict, atom_results: List[dict]) -> List[dict]:
    traps = []
    micro = sum(1 for r in hidden_enriched if r.get("cap_bucket") == "micro")
    low_liq = sum(1 for r in hidden_enriched if (r.get("avg_turnover_20d_egp") or 0) < LIQUIDITY_GATE_EGP * 1.2)
    impact_only = sum(
        1 for r in hidden_enriched
        if (r.get("metrics") or {}).get("impact_expansion", 0) > 1.2
        and float((r.get("metrics") or {}).get("rel_turn") or 0) < 0.8
    )
    one_day_pct = pct(persistence.get("one_day_only", 0), persistence.get("hidden_repricing_unique_symbols", 1))

    traps.append({
        "trap": "thin_liquidity_bias",
        "severity": "medium" if low_liq > 5 else "low",
        "detail": f"{low_liq}/35 hidden repricing near/below 2M EGP liquidity gate; {micro} micro-cap by turnover proxy",
    })
    traps.append({
        "trap": "impact_expansion_low_volume",
        "severity": "medium" if impact_only > 3 else "low",
        "detail": f"{impact_only} names show impact_expansion>1.2 with rel_turn<0.8 (possible illiquidity artifact)",
    })
    traps.append({
        "trap": "single_day_snapshot",
        "severity": "high",
        "detail": f"DB has 1 stored MDE date; backfill shows {one_day_pct}% hidden-repricing symbols appear only 1 day",
    })
    traps.append({
        "trap": "finance_sector_pf_concentration",
        "severity": "medium",
        "detail": "Atom OOS hits skewed to Finance sector (see atom concentration finance_sector_share_pct)",
    })
    traps.append({
        "trap": "confidence_score_ceiling",
        "severity": "low",
        "detail": "Many hidden_repricing names at confidence=100 despite missing TV/pine on subset",
    })
    traps.append({
        "trap": "hidden_repricing_no_persistence_gate",
        "severity": "medium",
        "detail": "hidden_repricing fires on 2+ intraday signals same day — no multi-day confirmation in v1",
    })
    return traps


def recommendations_before_phase3(persistence: dict, overlap: dict, traps: List[dict]) -> List[dict]:
    recs = [
        {"recommendation": "persistence_gate", "priority": "high",
         "detail": "Require hidden_repricing on 2+ consecutive sessions before watch-tier promotion"},
        {"recommendation": "sector_concentration_cap", "priority": "medium",
         "detail": "Cap Finance-sector MDE watch entries to ≤40% per day until cross-sector stability proven"},
        {"recommendation": "liquidity_floor_review", "priority": "medium",
         "detail": "Consider raising avg_turnover_20d floor to 3M EGP for hidden_repricing flag"},
        {"recommendation": "confidence_minimum", "priority": "low",
         "detail": "Require confidence≥85 only when fundamentals+TV present; penalize missing data harder"},
        {"recommendation": "exclude_one_day_only", "priority": "high",
         "detail": f"Filter backfill one-day-only symbols ({persistence.get('one_day_only', '?')} symbols) from watch manifest"},
        {"recommendation": "split_liquid_vs_small_cap_tracks", "priority": "medium",
         "detail": "Tag discoveries as liquid_track vs small_cap_track before any future boost"},
        {"recommendation": "no_phase3_until", "priority": "critical",
         "detail": "mde_boost_atoms=[] — do not enable EGX_MDE_OPP_BOOST until Production tier + multi-week stability"},
    ]
    if overlap.get("mde_only_not_in_opp"):
        recs.insert(0, {
            "recommendation": "track_outside_opp_symbols",
            "priority": "high",
            "detail": f"Paper-track outside-opp discoveries: {overlap['mde_only_not_in_opp'][:10]}",
        })
    return recs


def render_markdown(report: dict) -> str:
    lines = [
        "# MDE Shadow Forensics Report (Phase 2.5)",
        "",
        f"**Generated:** {report['at']}",
        f"**Latest MDE trade date:** {report['latest_trade_date']}",
        "",
        "## Executive Summary",
        "",
    ]
    es = report["executive_summary"]
    for k, v in es.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "## Data Coverage", ""])
    lines.append("| data_source | available | used | missing | coverage % |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in report["data_coverage"]["data_source_table"]:
        lines.append(
            f"| {r['data_source']} | {r['available_symbols']} | {r['used_symbols']} | "
            f"{r['missing_symbols']} | {r['coverage_pct']} |"
        )
    lines.extend(["", "## Top Discoveries (Hidden Repricing)", ""])
    for r in report["top_discoveries"]["by_effective_score"][:20]:
        lines.append(
            f"- **{r['symbol']}** ({r.get('sector')}) eff={r['effective_score']} "
            f"disc={r['discovery_score']} setups={r.get('setups_fired')} flags={r.get('overlap_flags')}"
        )
    lines.extend(["", "## Persistence Analysis", ""])
    p = report["persistence_analysis"]
    lines.append(f"- Backfill window: **{p['backfill_from']}** → **{p['backfill_to']}** ({p['backfill_trading_dates']} dates)")
    lines.append(f"- Unique hidden-repricing symbols in backfill: **{p['hidden_repricing_unique_symbols']}**")
    lines.append(f"- One day only: **{p['one_day_only']}** | 2–3 days: **{p['two_to_three_days']}** | 4+ days: **{p['four_plus_days']}**")
    lines.append("")
    lines.append("| symbol | days | consecutive | first | last | setups |")
    lines.append("|---|---:|---:|---|---|---|")
    for r in p.get("symbol_persistence_table", [])[:15]:
        lines.append(
            f"| {r['symbol']} | {r['days_detected']} | {r['consecutive_days']} | "
            f"{r['first_seen']} | {r['last_seen']} | {', '.join(r.get('repeated_setups') or [])} |"
        )
    lines.extend(["", "## Sector Clustering", ""])
    lines.append("| sector | count | avg_eff | dominant_setup | avg_turnover_20d |")
    lines.append("|---|---:|---:|---|---:|")
    for s in report.get("sector_clustering", [])[:12]:
        lines.append(
            f"| {s['sector']} | {s['count_hidden_repricing']} | {s['avg_effective_score']} | "
            f"{s.get('dominant_setup') or '-'} | {s['avg_turnover_20d']:,.0f} |"
        )
    lines.extend(["", "## Family Classification", ""])
    for f in report.get("family_classification", []):
        lines.append(f"- **{f['family']}** ({f['count']}): {f['examples'][:5]} — follow={f['followable']}")
    lines.extend(["", "## Risks / Biases", ""])
    for t in report.get("risks_and_biases", []):
        lines.append(f"- **[{t['severity']}]** {t['trap']}: {t['detail']}")
    lines.extend(["", "## Overlap With Existing System", ""])
    ov = report["overlap_analysis"]
    lines.append(f"- Hidden repricing: **{ov['mde_hidden_repricing_count']}**")
    lines.append(f"- Overlap opp universe: **{ov['overlap_hidden_vs_opp_universe']}**")
    lines.append(f"- Outside opp universe: **{ov['mde_only_not_in_opp']}**")
    lines.append(f"- New vs actionable: **{len(ov['mde_new_vs_actionable'])}** symbols")
    lines.extend(["", "## Atom Performance", ""])
    for a in report["atom_performance"]:
        if a.get("insufficient_data"):
            lines.append(f"- **{a['atom']}**: insufficient data (n={a.get('n')})")
            continue
        c = a.get("concentration", {})
        lines.append(
            f"- **{a['atom']}** n={a['backtest_n']} lift={a['backtest_lift']} PF={a['backtest_pf']} "
            f"hit={a['hit_rate_pct']}% | balanced_symbols={c.get('balanced_across_symbols')} "
            f"finance_share={c.get('finance_sector_share_pct')}%"
        )
    lines.extend(["", "## Recommendations Before Phase 3", ""])
    for r in report["recommendations_before_phase3"]:
        lines.append(f"- **[{r['priority']}]** {r['recommendation']}: {r['detail']}")
    lines.extend([
        "",
        "## Architectural Reminder",
        "",
        "```text",
        "MDE remains strictly additive. No veto. No suppression. No negative boost.",
        "No opp_v2 / UES / promotion / Telegram / final_signals changes.",
        "mde_priority_atoms = shadow evidence only. mde_boost_atoms = [] → Phase 3 OFF.",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    max_dates = int(params.get("max_dates") or 60)

    from egx_market_discovery_engine import load_bars
    from mde_oos_attribution import backfill_mde_records

    conn = connect()
    by_sym = load_bars(conn)
    trade_date, latest_rows = load_latest_mde_rows(conn)
    if not latest_rows:
        conn.close()
        return {"success": False, "error": "no egx_market_discovery_daily rows"}

    coverage = analyze_data_coverage(conn, by_sym, trade_date)

    opp_map, fs_map, liq_map = {}, {}, {}
    if table_exists(conn, "opportunity_score_v2"):
        for r in conn.execute(
            "SELECT symbol, stage, opportunity_score FROM opportunity_score_v2 WHERE trade_date=?",
            (trade_date,),
        ).fetchall():
            opp_map[r["symbol"]] = dict(r)
    if table_exists(conn, "final_signals"):
        for r in conn.execute(
            "SELECT symbol, actionable, setup_type FROM final_signals WHERE trade_date=?",
            (trade_date,),
        ).fetchall():
            fs_map[r["symbol"]] = dict(r)
    if table_exists(conn, "liquidity_profile"):
        for r in conn.execute(
            "SELECT symbol, liquidity_tier, advt_30d FROM liquidity_profile ORDER BY computed_date DESC"
        ).fetchall():
            if r["symbol"] not in liq_map:
                liq_map[r["symbol"]] = dict(r)

    enriched = [
        enrich_symbol_row(conn, r, trade_date, opp_map, fs_map, liq_map) for r in latest_rows
    ]
    hidden = [r for r in enriched if r["hidden_repricing"]]
    hidden_syms = {r["symbol"] for r in hidden}

    hidden_by_disc = sorted(hidden, key=lambda x: -float(x["discovery_score"] or 0))
    hidden_by_conf = sorted(hidden, key=lambda x: -float(x["confidence_score"] or 0))
    hidden_by_eff = sorted(hidden, key=lambda x: -float(x["effective_score"] or 0))

    backfill = backfill_mde_records(conn, max_dates=max_dates)
    dates = sorted({r["trade_date"] for r in backfill})
    split_date = dates[int(len(dates) * 0.75)] if len(dates) >= 8 else (dates[0] if dates else trade_date)
    outcomes = load_forward_returns(conn, by_sym)
    persistence = analyze_persistence(backfill)
    overlap = overlap_analysis(hidden_syms, conn, trade_date, hidden)
    atom_perf = atom_forensics(backfill, outcomes, split_date)
    families = classify_families(hidden)
    sector_cl = sector_clustering(hidden)
    traps = risk_traps(hidden, persistence, atom_perf)
    recs = recommendations_before_phase3(persistence, overlap, traps)

    cap_dist = Counter(r.get("cap_bucket") for r in hidden)
    setup_dist = Counter(s for r in hidden for s in (r.get("setups_fired") or []))

    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": "2.5_shadow_forensics",
        "latest_trade_date": trade_date,
        "oos_backfill": {
            "max_dates": max_dates,
            "trading_dates": len(dates),
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "oos_split_date": split_date,
            "scope": "full ohlcv_history bars per symbol; backfill recomputes MDE metrics per date",
        },
        "executive_summary": {
            "mde_ran_on_symbols": coverage["mde_symbols_scored_latest"],
            "ohlcv_universe": coverage["ohlcv_symbols"],
            "hidden_repricing_latest_day": len(hidden),
            "stored_mde_dates_in_db": coverage["mde_db_snapshot_dates"],
            "outside_opp_universe": overlap["mde_only_not_in_opp"],
            "overlap_with_actionable": overlap["overlap_hidden_vs_final_actionable"],
            "persistence_one_day_only_pct": pct(
                persistence["one_day_only"], max(persistence["hidden_repricing_unique_symbols"], 1)
            ),
            "phase3_ready": False,
            "reason": "mde_boost_atoms empty; single-day DB snapshot; persistence not proven",
            "additive_guarantee": "zero changes to opp_v2/UES/promotion/Telegram/final_signals",
        },
        "data_coverage": coverage,
        "latest_run_results": {
            "hidden_repricing_count": len(hidden),
            "all_hidden_symbols": [r["symbol"] for r in hidden_by_eff],
            "by_discovery_score": hidden_by_disc,
            "by_confidence_score": hidden_by_conf,
            "by_effective_score": hidden_by_eff,
            "top_20_effective": hidden_by_eff[:20],
            "pattern_summary": {
                "cap_bucket_distribution": dict(cap_dist),
                "setup_distribution": dict(setup_dist),
                "avg_turnover_20d": round(mean(float(r.get("avg_turnover_20d_egp") or 0) for r in hidden), 0),
                "median_turnover_20d": round(median(float(r.get("avg_turnover_20d_egp") or 0) for r in hidden), 0),
                "sectors_represented": len(sector_cl),
                "dominant_sector": sector_cl[0]["sector"] if sector_cl else None,
            },
        },
        "top_discoveries": {
            "by_discovery_score": hidden_by_disc[:20],
            "by_confidence_score": hidden_by_conf[:20],
            "by_effective_score": hidden_by_eff[:20],
        },
        "persistence_analysis": persistence,
        "sector_clustering": sector_cl,
        "overlap_analysis": overlap,
        "atom_performance": atom_perf,
        "family_classification": families,
        "risks_and_biases": traps,
        "recommendations_before_phase3": recs,
        "architectural_reminder": {
            "additive_only": True,
            "no_veto": True,
            "no_suppression": True,
            "no_negative_boost": True,
            "no_opp_v2": True,
            "no_ues": True,
            "no_promotion": True,
            "no_telegram": True,
            "no_final_signals_changes": True,
            "mde_priority_is_shadow_only": True,
            "mde_boost_atoms_empty": True,
        },
    }

    JSON_OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    MD_OUT.write_text(render_markdown(report), encoding="utf-8")
    conn.close()

    return {
        "success": True,
        "json": str(JSON_OUT.relative_to(ROOT)),
        "markdown": str(MD_OUT.relative_to(ROOT)),
        "executive_summary": report["executive_summary"],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
