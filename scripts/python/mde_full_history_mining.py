#!/usr/bin/env python3
"""
MDE Phase 2.6 — Full-History Behavioral Mining.

1. Backfill egx_market_discovery_daily for ALL trading dates (full OHLCV history)
2. Build symbol behavioral profiles, setup recurrence, families, sector map, rules
3. Output behavioral memory JSON for optional MDE feedback (EGX_MDE_BEHAVIOR_MEMORY=0 default)

Outputs:
  data/mde_full_history_events.json
  data/mde_symbol_behavior_profiles.json
  data/mde_behavior_families.json
  data/mde_behavior_rules.json
  data/mde_sector_behavior_map.json
  docs/MDE_FULL_HISTORY_BEHAVIORAL_MINING_REPORT.md
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

OUT_EVENTS = DATA / "mde_full_history_events.json"
OUT_PROFILES = DATA / "mde_symbol_behavior_profiles.json"
OUT_FAMILIES = DATA / "mde_behavior_families.json"
OUT_RULES = DATA / "mde_behavior_rules.json"
OUT_SECTOR = DATA / "mde_sector_behavior_map.json"
OUT_MD = ROOT / "docs" / "MDE_FULL_HISTORY_BEHAVIORAL_MINING_REPORT.md"

SETUP_KEYS = (
    "accum_breakout",
    "pullback_accum",
    "failed_breakdown",
    "sector_follower",
    "absorption_pre_break",
    "impact_expansion",
)
FAMILY_LABELS = {
    "A": "A) Impact-sensitive stocks",
    "B": "B) Absorption-driven stocks",
    "C": "C) Sector-follower stocks",
    "D": "D) Pullback-accumulation stocks",
    "E": "E) Spring / failed-breakdown stocks",
    "F": "F) Hidden-repricing multi-signal stocks",
    "G": "G) Noisy / false-discovery stocks",
}
SETUP_TO_FAMILY = {
    "impact_expansion": "A",
    "absorption_pre_break": "B",
    "sector_follower": "C",
    "pullback_accum": "D",
    "failed_breakdown": "E",
    "accum_breakout": "A",
}
HIT_THRESH = 0.05
RETURN_CAP = 0.50  # winsorize ±50% to reduce corporate-action outliers
INSERT_BATCH = 500


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def pf(wins: List[float], losses: List[float]) -> float:
    if not losses:
        return 2.0 if wins else 0.0
    return sum(wins) / max(sum(losses), 1e-9)


def stats_block(rets: List[float], hits: List[int]) -> dict:
    if not rets:
        return {"n": 0, "avg": None, "hit_rate": None, "pf": None}
    wins = [r for r, h in zip(rets, hits) if h]
    losses = [abs(r) for r, h in zip(rets, hits) if not h]
    return {
        "n": len(rets),
        "avg": round(mean(rets) * 100, 2),
        "hit_rate": round(sum(hits) / len(hits) * 100, 1),
        "pf": round(pf(wins, losses), 2),
    }


def ensure_indexes(conn: sqlite3.Connection) -> None:
    from egx_market_discovery_engine import ensure_table

    ensure_table(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mde_sym ON egx_market_discovery_daily(symbol)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mde_sym_date "
        "ON egx_market_discovery_daily(symbol, trade_date)"
    )
    conn.commit()


def forward_stats(bars: List[dict], idx: int) -> dict:
    out: dict = {}
    c0 = bars[idx]["close"]
    if c0 <= 0:
        return out
    for h in (5, 10, 20):
        if idx + h < len(bars):
            ret = bars[idx + h]["close"] / c0 - 1
            ret = max(-RETURN_CAP, min(RETURN_CAP, ret))
            out[f"ret{h}"] = ret
            out[f"hit{h}"] = 1 if ret >= HIT_THRESH else 0
    if idx + 20 < len(bars):
        peak = c0
        max_dd = 0.0
        for i in range(idx, idx + 21):
            c = bars[i]["close"]
            peak = max(peak, c)
            dd = (c - peak) / peak if peak else 0
            max_dd = min(max_dd, dd)
        out["max_dd_20d"] = max_dd
    return out


def backfill_full_history(
    conn: sqlite3.Connection,
    force: bool = False,
    max_dates: Optional[int] = None,
) -> dict:
    from egx_market_discovery_engine import (
        MIN_BARS,
        compute_bench_returns,
        compute_symbol_metrics,
        load_aux,
        load_bars,
    )

    t0 = time.time()
    by_sym = load_bars(conn)
    if not by_sym:
        return {"error": "no bars"}

    all_dates = sorted({b["date"] for bars in by_sym.values() for b in bars})
    if max_dates:
        all_dates = all_dates[-max_dates:]

    existing_dates: set = set()
    if not force:
        existing_dates = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_date FROM egx_market_discovery_daily"
            ).fetchall()
        }

    dates_to_run = [d for d in all_dates if force or d not in existing_dates]
    symbols = [s for s in by_sym if not s.startswith("EGX") and len(by_sym[s]) >= MIN_BARS]

    insert_sql = """
        INSERT OR REPLACE INTO egx_market_discovery_daily
        (symbol, trade_date, discovery_score, confidence_score, effective_score,
         mde_stage, mde_setup, hidden_repricing,
         fundamental_repricing_score, liquidity_regime_score, price_impact_score,
         absorption_score, supply_exhaustion_score, vpin_proxy_score, resilience_score,
         latent_accumulation_score, sector_rotation_score, catalyst_score,
         technical_trigger_score, pre_explosion_multiplier,
         gates_passed_json, setups_json, metrics_json, weights_version, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    now = datetime.now(timezone.utc).isoformat()
    total_rows = 0
    batch: List[tuple] = []

    static_aux = {"financial": {}, "tv": {}, "sector": {}, "pine": {}}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_data'"
    ).fetchone():
        for r in conn.execute("SELECT * FROM financial_data").fetchall():
            static_aux["financial"][r["symbol"]] = dict(r)
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_universe'"
    ).fetchone():
        for r in conn.execute("SELECT symbol, sector FROM stock_universe").fetchall():
            static_aux["sector"][r["symbol"]] = r["sector"]

    for di, trade_date in enumerate(dates_to_run):
        aux = {
            "financial": static_aux["financial"],
            "sector": static_aux["sector"],
            "tv": {},
            "pine": {},
        }
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tv_discovery_features'"
        ).fetchone():
            for r in conn.execute(
                "SELECT * FROM tv_discovery_features WHERE trade_date=?", (trade_date,)
            ).fetchall():
                aux["tv"][r["symbol"]] = dict(r)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pine_analytics'"
        ).fetchone():
            for r in conn.execute(
                "SELECT * FROM pine_analytics WHERE trade_date=?", (trade_date,)
            ).fetchall():
                aux["pine"][r["symbol"]] = dict(r)

        bench = compute_bench_returns(by_sym, trade_date, aux["sector"])
        for sym in symbols:
            row = compute_symbol_metrics(sym, by_sym[sym], trade_date, aux, bench)
            if not row:
                continue
            batch.append((
                row["symbol"], row["trade_date"], row["discovery_score"], row["confidence_score"],
                row["effective_score"], row["mde_stage"], row["mde_setup"], row["hidden_repricing"],
                row["fundamental_repricing_score"], row["liquidity_regime_score"],
                row["price_impact_score"], row["absorption_score"], row["supply_exhaustion_score"],
                row["vpin_proxy_score"], row["resilience_score"], row["latent_accumulation_score"],
                row["sector_rotation_score"], row["catalyst_score"], row["technical_trigger_score"],
                row["pre_explosion_multiplier"], row["gates_passed_json"], row["setups_json"],
                row["metrics_json"], row["weights_version"], now,
            ))
            if len(batch) >= INSERT_BATCH:
                conn.executemany(insert_sql, batch)
                total_rows += len(batch)
                batch.clear()

        if batch:
            conn.executemany(insert_sql, batch)
            total_rows += len(batch)
            batch.clear()
        conn.commit()

        if (di + 1) % 25 == 0:
            print(f"  backfill {di + 1}/{len(dates_to_run)} dates | rows={total_rows}", flush=True)

    elapsed = round(time.time() - t0, 1)
    return {
        "symbols": len(symbols),
        "dates_processed": len(dates_to_run),
        "dates_skipped_existing": len(all_dates) - len(dates_to_run),
        "rows_written": total_rows,
        "date_from": all_dates[0] if all_dates else None,
        "date_to": all_dates[-1] if all_dates else None,
        "elapsed_sec": elapsed,
    }


def load_enriched_events(conn: sqlite3.Connection, by_sym: dict) -> List[dict]:
    """Load all MDE rows from DB and attach forward returns from OHLCV."""
    idx_map: Dict[str, Dict[str, int]] = {}
    for sym, bars in by_sym.items():
        idx_map[sym] = {b["date"]: i for i, b in enumerate(bars)}

    events: List[dict] = []
    for r in conn.execute(
        """
        SELECT symbol, trade_date, discovery_score, confidence_score, effective_score,
               mde_stage, mde_setup, hidden_repricing, pre_explosion_multiplier,
               setups_json, metrics_json
        FROM egx_market_discovery_daily
        ORDER BY trade_date, symbol
        """
    ).fetchall():
        try:
            setups = json.loads(r["setups_json"] or "[]")
        except json.JSONDecodeError:
            setups = []
        try:
            metrics = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            metrics = {}

        fwd: dict = {}
        imap = idx_map.get(r["symbol"], {})
        idx = imap.get(r["trade_date"])
        if idx is not None:
            fwd = forward_stats(by_sym[r["symbol"]], idx)

        events.append({
            "symbol": r["symbol"],
            "trade_date": r["trade_date"],
            "sector": metrics.get("sector"),
            "discovery_score": r["discovery_score"],
            "confidence_score": r["confidence_score"],
            "effective_score": r["effective_score"],
            "mde_stage": r["mde_stage"],
            "mde_setup": r["mde_setup"],
            "hidden_repricing": bool(r["hidden_repricing"]),
            "pre_explosion_multiplier": r["pre_explosion_multiplier"],
            "setups": setups,
            "metrics": metrics,
            "ret5": fwd.get("ret5"),
            "ret10": fwd.get("ret10"),
            "ret20": fwd.get("ret20"),
            "hit5": fwd.get("hit5"),
            "hit10": fwd.get("hit10"),
            "hit20": fwd.get("hit20"),
            "max_dd_20d": fwd.get("max_dd_20d"),
        })
    return events


def setup_stats(events: List[dict], setup: str) -> dict:
    sub = [e for e in events if setup in (e.get("setups") or []) and e.get("ret5") is not None]
    rets5 = [e["ret5"] for e in sub]
    rets10 = [e["ret10"] for e in sub if e.get("ret10") is not None]
    rets20 = [e["ret20"] for e in sub if e.get("ret20") is not None]
    hits5 = [e["hit5"] for e in sub]
    hits10 = [e["hit10"] for e in sub if e.get("hit10") is not None]
    hits20 = [e["hit20"] for e in sub if e.get("hit20") is not None]
    dds = [e["max_dd_20d"] for e in sub if e.get("max_dd_20d") is not None]
    wins = [r for r, h in zip(rets5, hits5) if h]
    losses = [abs(r) for r, h in zip(rets5, hits5) if not h]
    return {
        "occurrences": len(sub),
        "avg_5d_pct": round(mean(rets5) * 100, 2) if rets5 else None,
        "avg_10d_pct": round(mean(rets10) * 100, 2) if rets10 else None,
        "avg_20d_pct": round(mean(rets20) * 100, 2) if rets20 else None,
        "hit_rate_5d_pct": round(sum(hits5) / len(hits5) * 100, 1) if hits5 else None,
        "hit_rate_10d_pct": round(sum(hits10) / len(hits10) * 100, 1) if hits10 else None,
        "hit_rate_20d_pct": round(sum(hits20) / len(hits20) * 100, 1) if hits20 else None,
        "pf_5d": round(pf(wins, losses), 2) if rets5 else None,
        "avg_max_dd_20d_pct": round(mean(dds) * 100, 2) if dds else None,
    }


def build_symbol_profiles(events: List[dict]) -> Dict[str, dict]:
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)

    profiles: Dict[str, dict] = {}
    for sym, evs in by_sym.items():
        sector = next((e.get("sector") for e in evs if e.get("sector")), "Unknown")
        hr_evs = [e for e in evs if e["hidden_repricing"]]
        with_ret = [e for e in evs if e.get("ret5") is not None]

        setup_counts = Counter(s for e in evs for s in (e.get("setups") or []))
        setup_perf: Dict[str, dict] = {}
        for sk in SETUP_KEYS:
            st = setup_stats(evs, sk)
            if st["occurrences"]:
                setup_perf[sk] = st

        best_setup, worst_setup = None, None
        best_score, worst_score = -999.0, 999.0
        for sk, st in setup_perf.items():
            if st["occurrences"] < 3:
                continue
            score = (st["hit_rate_5d_pct"] or 0) + (st["avg_5d_pct"] or 0) * 0.5
            if score > best_score:
                best_score, best_setup = score, sk
            if score < worst_score:
                worst_score, worst_setup = score, sk

        rets5 = [e["ret5"] for e in with_ret]
        hits5 = [e["hit5"] for e in with_ret]
        rets10 = [e["ret10"] for e in with_ret if e.get("ret10") is not None]
        hits10 = [e["hit10"] for e in with_ret if e.get("hit10") is not None]
        rets20 = [e["ret20"] for e in with_ret if e.get("ret20") is not None]
        hits20 = [e["hit20"] for e in with_ret if e.get("hit20") is not None]
        dds = [e["max_dd_20d"] for e in with_ret if e.get("max_dd_20d") is not None]

        primary_family = assign_symbol_family(sym, evs, setup_perf, setup_counts)

        reliability = (sum(hits5) / len(hits5)) if hits5 else 0
        conf_adj = 0.0
        if best_setup and reliability >= 0.28:
            conf_adj = min(10, 3 + reliability * 15)
        elif worst_setup and reliability < 0.15:
            conf_adj = -8

        profiles[sym] = {
            "symbol": sym,
            "sector": sector,
            "total_mde_events": len(evs),
            "hidden_repricing_events": len(hr_evs),
            "impact_expansion_events": setup_counts.get("impact_expansion", 0),
            "absorption_events": setup_counts.get("absorption_pre_break", 0),
            "sector_follower_events": setup_counts.get("sector_follower", 0),
            "pullback_accum_events": setup_counts.get("pullback_accum", 0),
            "spring_events": setup_counts.get("failed_breakdown", 0),
            "accum_breakout_events": setup_counts.get("accum_breakout", 0),
            "avg_forward_return_5d_pct": round(mean(rets5) * 100, 2) if rets5 else None,
            "avg_forward_return_10d_pct": round(mean(rets10) * 100, 2) if rets10 else None,
            "avg_forward_return_20d_pct": round(mean(rets20) * 100, 2) if rets20 else None,
            "hit_rate_5d": round(reliability, 3),
            "hit_rate_10d": round(sum(hits10) / len(hits10), 3) if hits10 else None,
            "hit_rate_20d": round(sum(hits20) / len(hits20), 3) if hits20 else None,
            "avg_max_drawdown_after_signal_pct": round(mean(dds) * 100, 2) if dds else None,
            "best_setup_for_symbol": best_setup,
            "worst_setup_for_symbol": worst_setup,
            "most_repeated_setup": setup_counts.most_common(1)[0][0] if setup_counts else None,
            "setup_performance": setup_perf,
            "behavior_family": primary_family,
            "sector_behavior_family": primary_family,
            "best_historical_setup": best_setup,
            "worst_historical_setup": worst_setup,
            "setup_reliability": round(reliability, 3),
            "preferred_holding_window": (
                "5d" if (setup_perf.get(best_setup or "", {}).get("hit_rate_5d_pct") or 0) >= 25
                else "10d" if best_setup else "unknown"
            ),
            "confidence_adjustment": round(conf_adj, 1),
            "first_seen": min(e["trade_date"] for e in evs),
            "last_seen": max(e["trade_date"] for e in evs),
        }
    return profiles


def assign_symbol_family(
    sym: str,
    evs: List[dict],
    setup_perf: Dict[str, dict],
    setup_counts: Counter,
) -> str:
    if not setup_counts:
        hr_rate = sum(1 for e in evs if e["hidden_repricing"]) / max(len(evs), 1)
        return "F" if hr_rate > 0.3 else "G"

    best_sk, best_hr = None, -1.0
    for sk, st in setup_perf.items():
        hr = st.get("hit_rate_5d_pct") or 0
        if st["occurrences"] >= 3 and hr > best_hr:
            best_hr, best_sk = hr, sk

    if best_sk:
        return SETUP_TO_FAMILY.get(best_sk, "F")

    dominant = setup_counts.most_common(1)[0][0]
    return SETUP_TO_FAMILY.get(dominant, "G")


def build_symbol_setup_table(events: List[dict]) -> List[dict]:
    rows: List[dict] = []
    keyed: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for e in events:
        for s in e.get("setups") or []:
            keyed[(e["symbol"], s)].append(e)

    for (sym, setup), evs in sorted(keyed.items(), key=lambda x: -len(x[1])):
        st = setup_stats(evs, setup)
        rows.append({
            "symbol": sym,
            "setup": setup,
            "occurrences": st["occurrences"],
            "avg_5d_pct": st["avg_5d_pct"],
            "avg_10d_pct": st["avg_10d_pct"],
            "avg_20d_pct": st["avg_20d_pct"],
            "hit_rate_5d_pct": st["hit_rate_5d_pct"],
            "pf_5d": st["pf_5d"],
            "last_seen": max(e["trade_date"] for e in evs),
            "first_seen": min(e["trade_date"] for e in evs),
        })
    return rows


def build_behavior_families(profiles: Dict[str, dict]) -> List[dict]:
    by_fam: Dict[str, List[dict]] = defaultdict(list)
    for p in profiles.values():
        by_fam[p["behavior_family"]].append(p)

    out = []
    for fam_id in ("A", "B", "C", "D", "E", "F", "G"):
        members = by_fam.get(fam_id, [])
        if not members:
            continue
        setup_hits: Counter = Counter()
        sector_hits: Counter = Counter()
        all_rets, all_hits = [], []
        for m in members:
            sector_hits[m.get("sector") or "Unknown"] += 1
            bs = m.get("best_setup_for_symbol")
            if bs:
                setup_hits[bs] += 1
            if m.get("hit_rate_5d") is not None:
                all_hits.append(m["hit_rate_5d"])
        out.append({
            "family_id": fam_id,
            "family": FAMILY_LABELS.get(fam_id, fam_id),
            "symbol_count": len(members),
            "examples": [m["symbol"] for m in sorted(members, key=lambda x: -(x.get("hit_rate_5d") or 0))[:12]],
            "best_setup": setup_hits.most_common(1)[0][0] if setup_hits else None,
            "avg_hit_rate_5d_pct": round(mean(all_hits) * 100, 1) if all_hits else None,
            "dominant_sectors": dict(sector_hits.most_common(5)),
            "risks": family_risks(fam_id),
            "followable": fam_id not in ("G",),
        })
    return out


def family_risks(fam_id: str) -> str:
    risks = {
        "A": "Illiquid impact_expansion artifacts when rel_turn < 1.0",
        "B": "Finance-sector concentration; needs sector cap",
        "C": "Sector noise when RS weak",
        "D": "Pullback traps in extended names",
        "E": "False springs in low-volume names",
        "F": "Multi-signal without setup — needs persistence gate",
        "G": "High false-discovery rate; watch-only or reject",
    }
    return risks.get(fam_id, "")


def build_sector_map(events: List[dict], profiles: Dict[str, dict]) -> List[dict]:
    by_sec: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        sec = e.get("sector") or "Unknown"
        by_sec[sec].append(e)

    rows = []
    for sec, evs in sorted(by_sec.items(), key=lambda x: -len(x[1])):
        setup_counts = Counter(s for e in evs for s in (e.get("setups") or []))
        dom = setup_counts.most_common(1)[0][0] if setup_counts else None
        with_ret = [e for e in evs if e.get("ret5") is not None]
        rets = [e["ret5"] for e in with_ret]
        hits = [e["hit5"] for e in with_ret]
        dom_st = setup_stats(evs, dom) if dom else {}
        sym_in_sec = {e["symbol"] for e in evs}
        n_syms = len(sym_in_sec)
        rows.append({
            "sector": sec,
            "symbol_count": n_syms,
            "event_count": len(evs),
            "dominant_behavior": dom,
            "best_setup": dom,
            "avg_forward_return_5d_pct": round(mean(rets) * 100, 2) if rets else None,
            "hit_rate_5d_pct": round(sum(hits) / len(hits) * 100, 1) if hits else dom_st.get("hit_rate_5d_pct"),
            "pf_5d": round(pf([r for r, h in zip(rets, hits) if h], [abs(r) for r, h in zip(rets, hits) if not h]), 2) if rets else dom_st.get("pf_5d"),
            "events_per_symbol": round(len(evs) / n_syms, 1) if n_syms else 0,
            "bias_note": (
                "likely_count_bias" if n_syms >= 15 and (round(sum(hits) / len(hits) * 100, 1) if hits else 0) > 28
                else "liquidity_artifact_check" if sec == "Finance"
                else "normal"
            ),
        })
    return rows


def mine_behavior_rules(events: List[dict]) -> List[dict]:
    with_ret = [e for e in events if e.get("ret5") is not None]

    def pool(filter_fn: Callable) -> List[dict]:
        return [e for e in with_ret if filter_fn(e)]

    def summarize(sub: List[dict]) -> dict:
        if not sub:
            return {"n": 0}
        rets = [e["ret5"] for e in sub]
        hits = [e["hit5"] for e in sub]
        wins = [r for r, h in zip(rets, hits) if h]
        losses = [abs(r) for r, h in zip(rets, hits) if not h]
        return {
            "n": len(sub),
            "hit_rate_5d_pct": round(sum(hits) / len(hits) * 100, 1),
            "avg_5d_pct": round(mean(rets) * 100, 2),
            "pf_5d": round(pf(wins, losses), 2),
        }

    hr_only = pool(lambda e: e["hidden_repricing"])
    hr_combo = pool(
        lambda e: e["hidden_repricing"]
        and "impact_expansion" in (e.get("setups") or [])
        and float((e.get("metrics") or {}).get("rs_20") or 0) > 0
    )
    impact_low_vol = pool(
        lambda e: "impact_expansion" in (e.get("setups") or [])
        and float((e.get("metrics") or {}).get("rel_turn") or 0) < 1.0
    )
    impact_ok_vol = pool(
        lambda e: "impact_expansion" in (e.get("setups") or [])
        and float((e.get("metrics") or {}).get("rel_turn") or 0) >= 1.0
    )
    abs_fin_ev = pool(
        lambda e: "absorption_pre_break" in (e.get("setups") or [])
        and (e.get("sector") == "Finance" or (e.get("metrics") or {}).get("sector") == "Finance")
    )
    no_setup_ev = pool(lambda e: not (e.get("setups") or []) and e["hidden_repricing"])

    rules = []

    s1a, s1b = summarize(hr_only), summarize(hr_combo)
    if s1a["n"] >= 40 and s1b["n"] >= 20:
        rules.append({
            "rule_id": "R1_hr_impact_rs_combo",
            "description": (
                "hidden_repricing + impact_expansion + positive sector RS "
                "outperforms hidden_repricing alone"
            ),
            "condition": "hidden_repricing AND impact_expansion AND rs_20 > 0",
            "baseline": s1a,
            "treatment": s1b,
            "lift_hit_rate": round(
                (s1b.get("hit_rate_5d_pct") or 0) / max(s1a.get("hit_rate_5d_pct") or 1, 1e-9), 2
            ),
            "action": "boost_confidence_when_combo",
            "enabled": (s1b.get("hit_rate_5d_pct") or 0) > (s1a.get("hit_rate_5d_pct") or 0),
        })

    s2a, s2b = summarize(impact_low_vol), summarize(impact_ok_vol)
    if s2a["n"] >= 15 and s2b["n"] >= 15:
        rules.append({
            "rule_id": "R2_impact_needs_volume",
            "description": "impact_expansion without rel_turnover >= 1.0 is often an artifact",
            "condition": "impact_expansion AND rel_turn < 1.0",
            "baseline": s2b,
            "treatment": s2a,
            "action": "penalize_confidence_or_reject_low_rel_turn",
            "enabled": (s2a.get("pf_5d") or 0) < (s2b.get("pf_5d") or 0),
        })

    if summarize(abs_fin_ev)["n"] >= 10:
        rules.append({
            "rule_id": "R3_absorption_finance_cap",
            "description": "absorption_before_breakout in Finance needs sector cap or confirmation",
            "condition": "absorption_pre_break AND sector=Finance",
            "stats": summarize(abs_fin_ev),
            "action": "sector_concentration_cap",
            "enabled": True,
        })

    s4 = summarize(no_setup_ev)
    if s4["n"] >= 20:
        rules.append({
            "rule_id": "R4_no_setup_hr_reject",
            "description": "Family F: hidden_repricing without setup — reject or persistence gate",
            "condition": "hidden_repricing AND setups=[]",
            "stats": s4,
            "action": "reject_or_require_2day_persistence",
            "enabled": (s4.get("hit_rate_5d_pct") or 0) < 22,
        })

    rules.append({
        "rule_id": "R5_persistence_gate_global",
        "description": "Require hidden_repricing on 2+ consecutive sessions before watch tier",
        "condition": "hidden_repricing_persistence >= 2 days",
        "action": "watch_tier_gate",
        "enabled": True,
        "source": "forensics_2.5",
    })

    return rules


def render_report(doc: dict) -> str:
    lines = [
        "# MDE Full-History Behavioral Mining Report (Phase 2.6)",
        "",
        f"**Generated:** {doc['at']}",
        "",
        "## 1. Full-History Coverage",
        "",
    ]
    cov = doc["coverage"]
    for k, v in cov.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "## 2. Events Per Setup", ""])
    for setup, n in doc.get("events_by_setup", {}).items():
        lines.append(f"- `{setup}`: **{n}** events")
    lines.extend(["", "## 3. Symbol-Level Repeated Behavior (top)", ""])
    lines.append("| symbol | setup | n | hit% | avg_5d% | PF |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for r in doc.get("top_symbol_setup_rows", [])[:20]:
        lines.append(
            f"| {r['symbol']} | {r['setup']} | {r['occurrences']} | "
            f"{r.get('hit_rate_5d_pct')} | {r.get('avg_5d_pct')} | {r.get('pf_5d')} |"
        )
    lines.extend(["", "## 4. Sector Clustering", ""])
    lines.append("| sector | syms | events | dominant | hit% | PF | bias |")
    lines.append("|---|---:|---:|---|---:|---:|---|")
    for s in doc.get("sector_behavior_map", [])[:15]:
        lines.append(
            f"| {s['sector']} | {s['symbol_count']} | {s['event_count']} | "
            f"{s.get('dominant_behavior') or '-'} | {s.get('hit_rate_5d_pct')} | "
            f"{s.get('pf_5d')} | {s.get('bias_note')} |"
        )
    lines.extend(["", "## 5. Behavior Families", ""])
    for f in doc.get("behavior_families", []):
        lines.append(f"- **{f['family']}** ({f['symbol_count']}): {f['examples'][:6]}")
    lines.extend(["", "## 6. Behavioral Rules", ""])
    for r in doc.get("behavior_rules", []):
        lines.append(f"- **{r['rule_id']}**: {r['description']} (enabled={r.get('enabled')})")
    lines.extend(["", "## 9. Recommended Engine Changes", ""])
    for rec in doc.get("engine_feedback", []):
        lines.append(f"- {rec}")
    lines.extend([
        "",
        "## Architectural Reminder",
        "",
        "```text",
        "Behavior memory is OFF by default (EGX_MDE_BEHAVIOR_MEMORY=0).",
        "No veto. No suppression. No opp_v2/UES/promotion changes.",
        "Phase 3 / EGX_MDE_OPP_BOOST remains OFF.",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    mode = params.get("mode", "full")  # full | analyze_only
    force = bool(params.get("force", False))
    max_dates = params.get("max_dates")

    from egx_market_discovery_engine import load_bars

    conn = connect()
    ensure_indexes(conn)

    backfill_summary = {"skipped": True}
    if mode != "analyze_only":
        print("═══ MDE Phase 2.6: Full-History Backfill ═══", flush=True)
        backfill_summary = backfill_full_history(conn, force=force, max_dates=max_dates)

    print("═══ Loading enriched events ═══", flush=True)
    by_sym = load_bars(conn)
    events = load_enriched_events(conn, by_sym)

    db_stats = conn.execute(
        """
        SELECT COUNT(*) n, COUNT(DISTINCT symbol) syms, COUNT(DISTINCT trade_date) dates,
               MIN(trade_date) d0, MAX(trade_date) d1
        FROM egx_market_discovery_daily
        """
    ).fetchone()

    print(f"  events={len(events)} db_rows={db_stats['n']}", flush=True)

    profiles = build_symbol_profiles(events)
    sym_setup_table = build_symbol_setup_table(events)
    families = build_behavior_families(profiles)
    sector_map = build_sector_map(events, profiles)
    rules = mine_behavior_rules(events)

    events_by_setup = Counter(s for e in events for s in (e.get("setups") or []))
    events_by_setup["hidden_repricing"] = sum(1 for e in events if e["hidden_repricing"])

    top_sym_setup = sorted(
        [r for r in sym_setup_table if r["occurrences"] >= 3],
        key=lambda x: (-(x.get("hit_rate_5d_pct") or 0), -x["occurrences"]),
    )[:50]

    notable_profiles = {
        s: profiles[s]
        for s in ("PRDC", "OLFI", "ARAB", "ISMQ", "CIRA")
        if s in profiles
    }

    engine_feedback = [
        "Store full history daily in egx_market_discovery_daily (done by this phase)",
        "Enable EGX_MDE_BEHAVIOR_MEMORY=1 only after 2+ weeks shadow with memory file stable",
        "Apply R1 combo boost in confidence when HR+impact+RS>0",
        "Apply R2 rel_turn gate on impact_expansion",
        "Apply R4/R5 persistence gates before watch tier",
        "Keep mde_boost_atoms=[] and EGX_MDE_OPP_BOOST=0",
    ]

    at = datetime.now(timezone.utc).isoformat()
    events_doc = {
        "at": at,
        "phase": "2.6",
        "total_events": len(events),
        "db_rows": db_stats["n"],
        "symbols": db_stats["syms"],
        "trade_dates": db_stats["dates"],
        "date_from": db_stats["d0"],
        "date_to": db_stats["d1"],
        "backfill": backfill_summary,
        "events_by_setup": dict(events_by_setup),
        "notable_symbol_profiles": notable_profiles,
        "sample_events": events[-25:] if events else [],
    }
    profiles_doc = {
        "at": at,
        "phase": "2.6",
        "symbol_count": len(profiles),
        "profiles": profiles,
    }
    families_doc = {"at": at, "families": families}
    rules_doc = {"at": at, "rules": rules, "apply_via": "EGX_MDE_BEHAVIOR_MEMORY=1"}
    sector_doc = {"at": at, "sectors": sector_map}

    report_doc = {
        "at": at,
        "coverage": {
            "total_events": len(events),
            "symbols": db_stats["syms"],
            "trade_dates": db_stats["dates"],
            "date_range": f"{db_stats['d0']} → {db_stats['d1']}",
            "full_history": db_stats["dates"] > 1,
        },
        "events_by_setup": dict(events_by_setup),
        "top_symbol_setup_rows": top_sym_setup,
        "sector_behavior_map": sector_map,
        "behavior_families": families,
        "behavior_rules": rules,
        "engine_feedback": engine_feedback,
        "notable_profiles": notable_profiles,
    }

    OUT_EVENTS.write_text(json.dumps(events_doc, indent=2, default=str), encoding="utf-8")
    OUT_PROFILES.write_text(json.dumps(profiles_doc, indent=2, default=str), encoding="utf-8")
    OUT_FAMILIES.write_text(json.dumps(families_doc, indent=2), encoding="utf-8")
    OUT_RULES.write_text(json.dumps(rules_doc, indent=2), encoding="utf-8")
    OUT_SECTOR.write_text(json.dumps(sector_doc, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_report(report_doc), encoding="utf-8")

    from mde_behavior_memory import clear_cache
    clear_cache()

    conn.close()

    return {
        "success": True,
        "backfill": backfill_summary,
        "total_events": len(events),
        "symbols": len(profiles),
        "trade_dates": db_stats["dates"],
        "outputs": [
            str(OUT_EVENTS.relative_to(ROOT)),
            str(OUT_PROFILES.relative_to(ROOT)),
            str(OUT_FAMILIES.relative_to(ROOT)),
            str(OUT_RULES.relative_to(ROOT)),
            str(OUT_SECTOR.relative_to(ROOT)),
            str(OUT_MD.relative_to(ROOT)),
        ],
    }


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), indent=2))
