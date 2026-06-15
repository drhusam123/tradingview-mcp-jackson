#!/usr/bin/env python3
"""
LRE Phase 3.3 — LRE × MDE Dual-Gate Observe-Only Audit.

Shadow research only. No actionable, promotion, Telegram, or client path impact.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    MAX_FORWARD,
    connect,
    ensure_tables,
    load_all_bars,
    vol_ratio,
    compression_days,
)
from lre_3_1_filters import calibrate_a_thresholds, enrich_signal, load_fingerprints  # noqa: E402
from lre_3_2_stage_rebuild import (  # noqa: E402
    COST_BPS,
    COST_BPS_150,
    DEDUP_COOLDOWN,
    OOS_START,
    in_window,
    resolve_entry_idx,
    row_to_trade,
    simulate_from_entry,
    trade_metrics,
)
from lre_3_2_stages import classify_substage  # noqa: E402
from lre_mde_dual_gate import (  # noqa: E402
    DUAL_GATE_TYPES,
    LRE_LEAD_SUBSTAGES,
    assess_mde_gate,
    classify_dual_gate_type,
    dual_gate_score,
    lre_monitoring_sighting,
    lre_monitoring_valid,
    lre_rejected,
    lre_row_summary,
)
from mde_actionable_discovery import enrich_events  # noqa: E402
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_shadow_trade_factory import build_analog_index, quick_analog  # noqa: E402
from mde_walkforward_shadow import date_index, load_events, pf  # noqa: E402

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": "LRE-3.3",
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
    "observe_only": True,
}
INSAMPLE_END = "2024-12-31"
MDE_START = "2022-04-05"

OUTPUTS = {
    "audit": DATA / "lre_mde_dual_gate_audit_last.json",
    "sequence": DATA / "lre_mde_sequence_audit.json",
    "oos": DATA / "lre_mde_oos_results.json",
    "candidates": DATA / "lre_mde_candidate_review_last.json",
    "report": ROOT / "docs/LRE_PHASE_3_3_MDE_DUAL_GATE_AUDIT.md",
}


def ensure_dual_gate_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_mde_dual_gate_audit (
        trade_date              TEXT NOT NULL,
        symbol                  TEXT NOT NULL,
        lre_stage               INTEGER,
        lre_sub_stage           TEXT,
        lre_eps                 REAL,
        lre_candidate_type      TEXT,
        lre_reason_codes        TEXT,
        lre_risk_flags          TEXT,
        lre_monitoring_only     INTEGER DEFAULT 0,
        mde_stage               TEXT,
        mde_score               REAL,
        mde_gate_passed         INTEGER DEFAULT 0,
        mde_reason_codes        TEXT,
        mde_risk_flags          TEXT,
        dual_gate_type          TEXT,
        dual_gate_score         REAL,
        dual_gate_passed_shadow INTEGER DEFAULT 0,
        dual_gate_reason        TEXT,
        client_path_allowed       INTEGER DEFAULT 0,
        forward_return_5d       REAL,
        forward_return_10d      REAL,
        forward_return_20d      REAL,
        forward_return_30d      REAL,
        forward_return_45d      REAL,
        mfe_20d                 REAL,
        mae_20d                 REAL,
        hit_5pct_20d            INTEGER DEFAULT 0,
        hit_10pct_20d           INTEGER DEFAULT 0,
        hit_15pct_30d           INTEGER DEFAULT 0,
        artifact_flag           INTEGER DEFAULT 0,
        liquidity_flag          INTEGER DEFAULT 0,
        already_exploded_flag   INTEGER DEFAULT 0,
        created_at              TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_mde_dual_type ON lre_mde_dual_gate_audit(dual_gate_type, trade_date);
    """)


def _window_dates(max_date: str) -> dict:
    md = datetime.strptime(max_date, "%Y-%m-%d")
    return {
        "full": ("2020-12-10", "2099-12-31"),
        "in_sample": ("2020-12-10", INSAMPLE_END),
        "oos": (OOS_START, "2099-12-31"),
        "latest_6m": ((md - timedelta(days=183)).strftime("%Y-%m-%d"), max_date),
        "latest_3m": ((md - timedelta(days=92)).strftime("%Y-%m-%d"), max_date),
    }


def forward_metrics(bars: List[dict], idx: int) -> dict:
    c0 = bars[idx]["close"]
    if not c0 or c0 <= 0:
        return {}
    out: dict = {}
    mfe = mae = 0.0
    for h in (5, 10, 20, 30, 45):
        if idx + h < len(bars):
            c = bars[idx + h]["close"]
            if c:
                out[f"forward_return_{h}d"] = round((c / c0 - 1) * 100, 3)
    end = min(len(bars) - 1, idx + 20)
    for j in range(idx + 1, end + 1):
        lo, hi = bars[j]["low"], bars[j]["high"]
        if lo and hi:
            mae = min(mae, (lo - c0) / c0 * 100)
            mfe = max(mfe, (hi - c0) / c0 * 100)
    out["mfe_20d"] = round(mfe, 3)
    out["mae_20d"] = round(mae, 3)
    out["hit_5pct_20d"] = 1 if mfe >= 5 else 0
    out["hit_10pct_20d"] = 1 if mfe >= 10 else 0
    if idx + 30 < len(bars):
        peak = c0
        for j in range(idx + 1, idx + 31):
            peak = max(peak, bars[j]["high"] or c0)
        out["hit_15pct_30d"] = 1 if (peak / c0 - 1) * 100 >= 15 else 0
    else:
        out["hit_15pct_30d"] = 0
    return out


def build_lre_pool(conn, by_sym: dict, fingerprints: dict, thresholds: dict) -> List[dict]:
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
            row["sub_stage_detail"] = sub_detail
            row["symbol"] = sym
            row["trade_date"] = bars[idx]["date"]
            row["_sym"] = sym
            row["_idx"] = idx
            row["_bars"] = bars
            pool.append(row)
    return pool


def build_mde_lookup(events: List[dict], by_sector: dict) -> Dict[Tuple[str, str], dict]:
    lookup: Dict[Tuple[str, str], dict] = {}
    for e in events:
        astat = quick_analog(e, by_sector, e["trade_date"])
        gate = assess_mde_gate(e, astat)
        key = (e["symbol"], e["trade_date"])
        lookup[key] = {**e, **gate, "_astat": astat}
    return lookup


def build_lre_lookup(pool: List[dict]) -> Dict[Tuple[str, str], dict]:
    return {(r["symbol"], r["trade_date"]): r for r in pool}


def audit_row_from_pair(
    sym: str,
    td: str,
    lre_row: Optional[dict],
    mde_rec: Optional[dict],
    bars_by_sym: dict,
) -> dict:
    mde_gate = mde_rec or {
        "mde_gate_passed": False,
        "mde_stage": None,
        "mde_score": 0,
        "mde_reason_codes": [],
        "mde_risk_flags": [],
    }
    dg_type, dg_reason = classify_dual_gate_type(lre_row, mde_gate)
    score = dual_gate_score(lre_row, mde_gate)
    shadow_pass = 1 if dg_type == "LRE_MDE_CONFLUENCE" else 0

    lre_sum = lre_row_summary(lre_row) if lre_row else {
        "lre_stage": None,
        "lre_sub_stage": None,
        "lre_eps": None,
        "lre_candidate_type": None,
        "lre_reason_codes": [],
        "lre_risk_flags": [],
        "lre_monitoring_only": 0,
        "artifact_flag": 0,
        "liquidity_flag": 0,
        "already_exploded_flag": 0,
    }

    fwd: dict = {}
    bars = bars_by_sym.get(sym)
    if bars:
        idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
        if idx is not None:
            fwd = forward_metrics(bars, idx)

    return {
        "trade_date": td,
        "symbol": sym,
        **lre_sum,
        "mde_stage": mde_gate.get("mde_stage"),
        "mde_score": mde_gate.get("mde_score"),
        "mde_gate_passed": int(mde_gate.get("mde_gate_passed") or 0),
        "mde_reason_codes": mde_gate.get("mde_reason_codes") or [],
        "mde_risk_flags": mde_gate.get("mde_risk_flags") or [],
        "dual_gate_type": dg_type,
        "dual_gate_score": score,
        "dual_gate_passed_shadow": shadow_pass,
        "dual_gate_reason": dg_reason,
        "client_path_allowed": 0,
        **fwd,
    }


def persist_audit_rows(conn: sqlite3.Connection, rows: List[dict]) -> int:
    conn.execute("DELETE FROM lre_mde_dual_gate_audit")
    sql = """
        INSERT OR REPLACE INTO lre_mde_dual_gate_audit
        (trade_date, symbol, lre_stage, lre_sub_stage, lre_eps, lre_candidate_type,
         lre_reason_codes, lre_risk_flags, lre_monitoring_only,
         mde_stage, mde_score, mde_gate_passed, mde_reason_codes, mde_risk_flags,
         dual_gate_type, dual_gate_score, dual_gate_passed_shadow, dual_gate_reason,
         client_path_allowed, forward_return_5d, forward_return_10d, forward_return_20d,
         forward_return_30d, forward_return_45d, mfe_20d, mae_20d,
         hit_5pct_20d, hit_10pct_20d, hit_15pct_30d,
         artifact_flag, liquidity_flag, already_exploded_flag)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    batch = []
    for r in rows:
        batch.append((
            r["trade_date"], r["symbol"], r.get("lre_stage"), r.get("lre_sub_stage"),
            r.get("lre_eps"), r.get("lre_candidate_type"),
            json.dumps(r.get("lre_reason_codes") or []),
            json.dumps(r.get("lre_risk_flags") or []),
            int(r.get("lre_monitoring_only") or 0),
            r.get("mde_stage"), r.get("mde_score"), r.get("mde_gate_passed"),
            json.dumps(r.get("mde_reason_codes") or []),
            json.dumps(r.get("mde_risk_flags") or []),
            r.get("dual_gate_type"), r.get("dual_gate_score"),
            r.get("dual_gate_passed_shadow"), r.get("dual_gate_reason"),
            0,
            r.get("forward_return_5d"), r.get("forward_return_10d"), r.get("forward_return_20d"),
            r.get("forward_return_30d"), r.get("forward_return_45d"),
            r.get("mfe_20d"), r.get("mae_20d"),
            r.get("hit_5pct_20d"), r.get("hit_10pct_20d"), r.get("hit_15pct_30d"),
            int(r.get("artifact_flag") or 0),
            int(r.get("liquidity_flag") or 0),
            int(r.get("already_exploded_flag") or 0),
        ))
    for i in range(0, len(batch), 500):
        conn.executemany(sql, batch[i:i + 500])
    conn.commit()
    return len(batch)


def trade_from_audit(
    row: dict,
    bars_by_sym: dict,
    timing: str = "same_day",
    hold_days: int = 20,
    stop_mode: str = "base_low",
) -> Optional[dict]:
    sym, td = row["symbol"], row["trade_date"]
    bars = bars_by_sym.get(sym)
    if not bars:
        return None
    sig_idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
    if sig_idx is None:
        return None
    entry_idx, entry_label = resolve_entry_idx(bars, sig_idx, timing)
    if entry_idx is None:
        return None
    sim = simulate_from_entry(bars, entry_idx, hold_days, stop_mode)
    if timing == "none":
        sim = simulate_from_entry(bars, entry_idx, hold_days, "none")
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
    t = row_to_trade(lre_stub, sim, row.get("dual_gate_type"), entry_label)
    t["dual_gate_type"] = row.get("dual_gate_type")
    t["dual_gate_score"] = row.get("dual_gate_score")
    t["mde_gate_passed"] = row.get("mde_gate_passed")
    t["timing_mode"] = timing
    return t


def extended_trade_metrics(trades: List[dict], window: Optional[Tuple[str, str]] = None) -> dict:
    base = trade_metrics(trades, window)
    if not trades or not base.get("sample_ok"):
        return base

    fr5 = [t.get("forward_return_5d") for t in trades if t.get("forward_return_5d") is not None]
    fr10 = [t.get("forward_return_10d") for t in trades if t.get("forward_return_10d") is not None]
    fr20 = [t.get("forward_return_20d") for t in trades if t.get("forward_return_20d") is not None]
    fr30 = [t.get("forward_return_30d") for t in trades if t.get("forward_return_30d") is not None]
    fr45 = [t.get("forward_return_45d") for t in trades if t.get("forward_return_45d") is not None]

    no_stop = [t for t in trades if t.get("exit_reason") != "stop_hit"]
    no_stop_20 = [t for t in no_stop if (t.get("holding_days") or 0) >= 20 or t.get("exit_reason", "").startswith("hold")]

    base.update({
        "forward_return_5d_avg": round(mean(fr5), 3) if fr5 else None,
        "forward_return_10d_avg": round(mean(fr10), 3) if fr10 else None,
        "forward_return_20d_avg": round(mean(fr20), 3) if fr20 else None,
        "forward_return_30d_avg": round(mean(fr30), 3) if fr30 else None,
        "forward_return_45d_avg": round(mean(fr45), 3) if fr45 else None,
        "forward_return_20d_median": round(median(fr20), 3) if fr20 else None,
        "no_stop_20d_count": len(no_stop_20),
        "no_stop_20d_median": round(median([t["net_return_100bps"] for t in no_stop_20]), 3) if no_stop_20 else None,
        "no_stop_20d_PF": round(pf(
            [t["net_return_100bps"] for t in no_stop_20 if t["net_return_100bps"] >= 5],
            [abs(t["net_return_100bps"]) for t in no_stop_20 if t["net_return_100bps"] < 5],
        ), 2) if no_stop_20 else None,
        "liquidity_contribution_pct": round(
            100 * sum(1 for t in trades if t.get("liquidity_flag")) / len(trades), 1
        ),
    })
    return base


def build_group_trades(
    audit_rows: List[dict],
    bars_by_sym: dict,
    group_type: str,
    timing: str = "same_day",
    hold_days: int = 20,
) -> List[dict]:
    subset = [r for r in audit_rows if r.get("dual_gate_type") == group_type]
    trades = []
    for r in subset:
        t = trade_from_audit(r, bars_by_sym, timing=timing, hold_days=hold_days)
        if t:
            for k in ("forward_return_5d", "forward_return_10d", "forward_return_20d",
                      "forward_return_30d", "forward_return_45d", "liquidity_flag"):
                t[k] = r.get(k)
            trades.append(t)
    dates = sorted({t["signal_date"] for t in trades})
    return dedup_trades(trades, dates, DEDUP_COOLDOWN)


def build_sequence_trades(
    pool: List[dict],
    mde_lookup: Dict[Tuple[str, str], dict],
    bars_by_sym: dict,
    trading_dates: List[str],
    date_to_idx: Dict[str, int],
    max_lag: int = 10,
) -> Dict[str, List[dict]]:
    """Sequence patterns including LRE_LEADS_MDE_CONFIRMATION."""
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for r in pool:
        by_sym[r["symbol"]].append(r)

    sequences = {
        "LRE_FIRST_THEN_MDE": [],
        "MDE_FIRST_THEN_LRE": [],
        "SAME_DAY_CONFLUENCE": [],
        "LRE_WITHOUT_MDE": [],
        "MDE_WITHOUT_LRE": [],
        "LRE_LEADS_MDE_CONFIRMATION": [],
    }

    for sym, rows in by_sym.items():
        rows.sort(key=lambda x: x["trade_date"])
        for row in rows:
            td = row["trade_date"]
            sub = row.get("sub_stage")
            lre_lead = sub in LRE_LEAD_SUBSTAGES and lre_monitoring_sighting(row)
            lre_valid, _, _ = lre_monitoring_valid(row)
            mde_same = mde_lookup.get((sym, td))
            mde_pass_same = bool(mde_same and mde_same.get("mde_gate_passed"))

            if lre_valid and mde_pass_same:
                t = _sequence_trade(sym, td, row, mde_same, bars_by_sym, "same_day", "SAME_DAY_CONFLUENCE")
                if t:
                    sequences["SAME_DAY_CONFLUENCE"].append(t)
                continue

            if lre_lead:
                found_mde = None
                for lag in range(1, max_lag + 1):
                    di = date_to_idx.get(td, -1) + lag
                    if di < 0 or di >= len(trading_dates):
                        break
                    future = trading_dates[di]
                    mde_f = mde_lookup.get((sym, future))
                    if mde_f and mde_f.get("mde_gate_passed"):
                        found_mde = (future, mde_f, lag)
                        break
                if found_mde:
                    entry_date, mde_f, lag = found_mde
                    t = _sequence_trade(
                        sym, entry_date, row, mde_f, bars_by_sym,
                        "same_day", "LRE_LEADS_MDE_CONFIRMATION",
                        meta={"lre_first_date": td, "lag_sessions": lag},
                    )
                    if t:
                        sequences["LRE_LEADS_MDE_CONFIRMATION"].append(t)
                        sequences["LRE_FIRST_THEN_MDE"].append(t)
                elif lre_monitoring_sighting(row):
                    t = _sequence_trade(sym, td, row, None, bars_by_sym, "same_day", "LRE_WITHOUT_MDE")
                    if t:
                        sequences["LRE_WITHOUT_MDE"].append(t)

    for (sym, td), mde_rec in mde_lookup.items():
        if not mde_rec.get("mde_gate_passed"):
            continue
        lre_same = next((r for r in by_sym.get(sym, []) if r["trade_date"] == td), None)
        if lre_same and lre_monitoring_valid(lre_same)[0]:
            continue
        found_lre = None
        for lag in range(1, max_lag + 1):
            di = date_to_idx.get(td, -1) + lag
            if di < 0 or di >= len(trading_dates):
                break
            future = trading_dates[di]
            lre_f = next((r for r in by_sym.get(sym, []) if r["trade_date"] == future), None)
            if lre_f and lre_monitoring_valid(lre_f)[0]:
                found_lre = (future, lre_f, lag)
                break
        if found_lre:
            entry_date, lre_f, lag = found_lre
            t = _sequence_trade(
                sym, entry_date, lre_f, mde_rec, bars_by_sym,
                "same_day", "MDE_FIRST_THEN_LRE",
                meta={"mde_first_date": td, "lag_sessions": lag},
            )
            if t:
                sequences["MDE_FIRST_THEN_LRE"].append(t)
        elif not lre_same or not lre_monitoring_sighting(lre_same):
            t = _sequence_trade(sym, td, None, mde_rec, bars_by_sym, "same_day", "MDE_WITHOUT_LRE")
            if t:
                sequences["MDE_WITHOUT_LRE"].append(t)

    for key in sequences:
        dates = sorted({t["signal_date"] for t in sequences[key]})
        sequences[key] = dedup_trades(sequences[key], dates, DEDUP_COOLDOWN)
    return sequences


def _sequence_trade(
    sym: str,
    entry_date: str,
    lre_row: Optional[dict],
    mde_rec: Optional[dict],
    bars_by_sym: dict,
    timing: str,
    seq_type: str,
    meta: Optional[dict] = None,
) -> Optional[dict]:
    audit = audit_row_from_pair(sym, entry_date, lre_row, mde_rec, bars_by_sym)
    audit["dual_gate_type"] = seq_type
    t = trade_from_audit(audit, bars_by_sym, timing=timing, hold_days=20)
    if not t:
        return None
    t["sequence_type"] = seq_type
    if meta:
        t.update(meta)
    return t


def timing_comparison(
    audit_rows: List[dict],
    bars_by_sym: dict,
) -> dict:
    confluence = [r for r in audit_rows if r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE"]
    out = {}
    for timing, label in (("same_day", "same_day_close"), ("pullback", "next_day_not_extended")):
        trades = []
        for r in confluence:
            t = trade_from_audit(r, bars_by_sym, timing=timing if timing != "same_day" else "same_day")
            if t:
                trades.append(t)
        dates = sorted({t["signal_date"] for t in trades})
        ded = dedup_trades(trades, dates, DEDUP_COOLDOWN)
        out[label] = {
            "full": trade_metrics(ded),
            "oos": trade_metrics(ded, (OOS_START, "2099-12-31")),
        }
    return out


def stop_diagnostic_confluence(audit_rows: List[dict], bars_by_sym: dict) -> dict:
    conf = [r for r in audit_rows if r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE"]
    out = {}
    for stop_mode, label in (("base_low", "structural"), ("none", "no_stop_20d")):
        trades = []
        for r in conf:
            t = trade_from_audit(r, bars_by_sym, timing="same_day", hold_days=20, stop_mode=stop_mode)
            if t:
                trades.append(t)
        dates = sorted({t["signal_date"] for t in trades})
        ded = dedup_trades(trades, dates, DEDUP_COOLDOWN)
        out[label] = {
            "oos": trade_metrics(ded, (OOS_START, "2099-12-31")),
        }
    out["stop_8pct"] = {}
    trades8 = []
    for r in conf:
        bars = bars_by_sym.get(r["symbol"])
        if not bars:
            continue
        sig_idx = next((i for i, b in enumerate(bars) if b["date"] == r["trade_date"]), None)
        if sig_idx is None:
            continue
        sim = simulate_from_entry(bars, sig_idx, 20, "base_low", stop_pct=8.0)
        lre_stub = {"symbol": r["symbol"], "trade_date": r["trade_date"], "sub_stage": r.get("lre_sub_stage"),
                    "stage": r.get("lre_stage"), "explosion_potential": r.get("lre_eps"),
                    "family_similarity_A": None, "stop_prone_score": None, "compression_days": None,
                    "vol_ratio_20": None, "vol_ratio_60": None,
                    "artifact_risk": r.get("artifact_flag"), "already_exploded": r.get("already_exploded_flag")}
        trades8.append(row_to_trade(lre_stub, sim, "confluence_stop8", "same_day"))
    dates = sorted({t["signal_date"] for t in trades8})
    ded8 = dedup_trades(trades8, dates, DEDUP_COOLDOWN)
    out["stop_8pct"]["oos"] = trade_metrics(ded8, (OOS_START, "2099-12-31"))
    return out


def review_candidates_dual(
    conn,
    by_sym: dict,
    fingerprints: dict,
    thresholds: dict,
    mde_lookup: Dict[Tuple[str, str], dict],
    symbols: List[str],
    date: str,
) -> dict:
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
        sub, _ = classify_substage(bars, idx, row)
        row["sub_stage"] = sub
        row["symbol"] = sym
        row["trade_date"] = date
        mde_rec = mde_lookup.get((sym, date))
        audit = audit_row_from_pair(sym, date, row, mde_rec, by_sym)
        valid, reasons, _ = lre_monitoring_valid(row)
        rejected, rej = lre_rejected(row)
        out[sym] = {
            **audit,
            "lre_monitoring_valid": valid,
            "lre_rejected": rejected,
            "lre_reject_reasons": rej,
            "lre_keep_monitoring": valid or sub in ("3A", "4B") or (sub in ("3B", "4A") and not rejected),
            "mde_confirms": bool(mde_rec and mde_rec.get("mde_gate_passed")),
            "should_remain_monitoring_only": True,
            "shadow_confluence_detected": audit["dual_gate_type"] == "LRE_MDE_CONFLUENCE",
        }
    return out


def final_verdict(group_oos: dict, seq_oos: dict) -> Tuple[str, str]:
    conf = group_oos.get("LRE_MDE_CONFLUENCE", {})
    lre_only = group_oos.get("LRE_ONLY", {})
    mde_only = group_oos.get("MDE_ONLY", {})
    n = conf.get("trade_count") or 0

    if n < 40:
        return "FAIL_CURVE_FIT_RISK", f"Confluence OOS trades {n} < 40"

    curve = (conf.get("top10_dominance_pct") or 100) > 35
    if curve:
        if (
            (conf.get("net_PF_100bps") or 0) >= 1.3
            and (conf.get("median_return") or 0) > 0
            and (conf.get("stop_hit_ratio") or 100) < 40
        ):
            return (
                "RESEARCH_EDGE_MONITOR_ONLY",
                f"Confluence OOS strong (PF={conf.get('net_PF_100bps')}, median={conf.get('median_return')}%) "
                f"but top-10 dominance {conf.get('top10_dominance_pct')}% > 35%",
            )
        return "FAIL_CURVE_FIT_RISK", "Confluence OOS top-10 dominance > 35%"

    gate = (
        (conf.get("net_PF_100bps") or 0) >= 1.3
        and (conf.get("median_return") or 0) > 0
        and (conf.get("stop_hit_ratio") or 100) < 40
        and n >= 40
        and (conf.get("artifact_pct") or 100) < 10
        and (conf.get("net_PF_100bps") or 0) > (lre_only.get("net_PF_100bps") or 0)
        and (conf.get("net_PF_100bps") or 0) > (mde_only.get("net_PF_100bps") or 0)
    )
    if gate:
        return "PASS_DUAL_GATE_SHADOW", "Confluence OOS passes shadow gate vs LRE_ONLY and MDE_ONLY"

    lre_leads = seq_oos.get("LRE_LEADS_MDE_CONFIRMATION", {})
    same_day = seq_oos.get("SAME_DAY_CONFLUENCE", {})
    if (
        (lre_leads.get("net_PF_100bps") or 0) >= 1.2
        and (lre_leads.get("median_return") or 0) > 0
        and (lre_leads.get("net_PF_100bps") or 0) > (same_day.get("net_PF_100bps") or 0) + 0.1
        and (lre_leads.get("trade_count") or 0) >= 30
    ):
        return (
            "RESEARCH_EDGE_SEQUENCE_DEPENDENT",
            "Best edge when LRE leads and MDE confirms 1–10 sessions later",
        )

    if (conf.get("net_PF_100bps") or 0) > max(lre_only.get("net_PF_100bps") or 0, mde_only.get("net_PF_100bps") or 0):
        return "RESEARCH_EDGE_MONITOR_ONLY", "Dual-gate improves vs singles but below PASS threshold"

    if (conf.get("net_PF_100bps") or 0) <= (lre_only.get("net_PF_100bps") or 0):
        return "FAIL_NO_CONFLUENCE_EDGE", "Confluence does not beat LRE_ONLY OOS"

    return "RESEARCH_EDGE_MONITOR_ONLY", "Dual-gate marginal — monitoring + MDE confirmation pilot only"


def render_report(doc: dict) -> str:
    g = doc["group_comparison"].get("same_day_close", {}).get("oos", {})
    seq = doc.get("sequence_oos") or {
        sk: doc["sequence_audit"][sk].get("oos", {})
        for sk in doc.get("sequence_audit", {})
    }
    conf = g.get("LRE_MDE_CONFLUENCE", {})
    verdict = doc["verdict"]
    ans = doc["answers"]
    lines = [
        "# LRE-3.3 — LRE × MDE Dual-Gate Observe-Only Audit",
        "",
        f"**Generated:** {doc['at']}",
        f"**Verdict:** `{verdict['code']}` — {verdict['reason']}",
        "",
        "## A. Why LRE Alone Failed",
        "",
        doc.get("lre_failure_summary", ""),
        "",
        "## B. Dual-Gate Design",
        "",
        "- **LRE** = liquidity rotation radar (3A/3B/4A/4B/4X sub-stages)",
        "- **MDE** = hidden repricing / absorption confirmation (COMP_001B, hidden_repricing)",
        "- **Why combine:** LRE sees transition early; MDE confirms repricing — hypothesis from LRE-3.2",
        "- **Observe-only:** No promotion, actionable, Telegram, boost, veto, or final_signals changes",
        "",
        "## C. Group Comparison (OOS 2025+)",
        "",
        "| Group | Trades | PF@100 | Median | Stop% | Hit+5% |",
        "|-------|--------|--------|--------|-------|--------|",
    ]
    for gt in DUAL_GATE_TYPES:
        m = g.get(gt, {})
        lines.append(
            f"| {gt} | {m.get('trade_count', 0)} | {m.get('net_PF_100bps', '—')} | "
            f"{m.get('median_return', '—')}% | {m.get('stop_hit_ratio', '—')}% | {m.get('hit_5pct', '—')}% |"
        )
    lines.extend([
        "",
        "## D. Sequence Audit (OOS)",
        "",
        "| Sequence | Trades | PF@100 | Median | Stop% |",
        "|----------|--------|--------|--------|-------|",
    ])
    for sk in ("LRE_LEADS_MDE_CONFIRMATION", "LRE_FIRST_THEN_MDE", "MDE_FIRST_THEN_LRE",
               "SAME_DAY_CONFLUENCE", "LRE_WITHOUT_MDE", "MDE_WITHOUT_LRE"):
        m = seq.get(sk, {})
        lines.append(
            f"| {sk} | {m.get('trade_count', 0)} | {m.get('net_PF_100bps', '—')} | "
            f"{m.get('median_return', '—')}% | {m.get('stop_hit_ratio', '—')}% |"
        )
    best_seq = doc.get("best_sequence")
    lines.extend([
        "",
        f"**Best sequence (OOS):** {best_seq}",
        "",
        "## E. OOS Results",
        "",
    ])
    for wk, wm in doc.get("windows_oos", {}).items():
        c = wm.get("LRE_MDE_CONFLUENCE", {})
        lines.append(
            f"- **{wk}:** confluence trades={c.get('trade_count')} PF={c.get('net_PF_100bps')} "
            f"median={c.get('median_return')}%"
        )
    lines.extend([
        "",
        "## F. Timing & Stop Diagnostic",
        "",
        f"- MDE confirmation entry vs LRE same-day: {ans.get('timing', '—')}",
        f"- no_stop 20d edge at MDE confirmation: {ans.get('no_stop', '—')}",
        f"- stop_hit change: {ans.get('stop_change', '—')}",
        "",
        "## G. Candidate Review (2026-06-11)",
        "",
    ])
    for sym, c in doc.get("candidates", {}).items():
        lines.append(
            f"- **{sym}:** sub={c.get('lre_sub_stage')} type={c.get('dual_gate_type')} "
            f"score={c.get('dual_gate_score')} MDE={c.get('mde_gate_passed')} "
            f"confluence={c.get('confluence_real')} monitoring_only={c.get('should_remain_monitoring_only')}"
        )
    lines.extend([
        "",
        "## H. Final Decision",
        "",
        f"**{verdict['code']}** — {verdict['reason']}",
        "",
        "## Answers",
        "",
    ])
    for q, a in ans.items():
        lines.append(f"1. **{q}** — {a}")
    lines.append("")
    lines.append("---")
    lines.append("*Shadow dual-gate pilot only — no production step.*")
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("LRE-3.3 dual-gate audit starting...", flush=True)

    conn = connect()
    ensure_tables(conn)
    ensure_dual_gate_table(conn)

    by_sym, _meta = load_all_bars(conn)
    fingerprints = load_fingerprints()
    thresholds = calibrate_a_thresholds(conn, by_sym, fingerprints)

    print("  Loading MDE events...", flush=True)
    events, _ = load_events(conn)
    edates, _ = date_index(events)
    enrich_events(events, by_sym, edates)
    by_sector = build_analog_index(events)
    mde_lookup = build_mde_lookup(events, by_sector)
    print(f"  MDE lookup: {len(mde_lookup)} rows", flush=True)

    print("  Building LRE pool...", flush=True)
    pool = build_lre_pool(conn, by_sym, fingerprints, thresholds)
    lre_lookup = build_lre_lookup(pool)
    print(f"  LRE pool: {len(pool)} rows", flush=True)

    all_keys = set(lre_lookup.keys()) | set(mde_lookup.keys())
    audit_rows: List[dict] = []
    for sym, td in sorted(all_keys):
        if td < MDE_START:
            continue
        audit_rows.append(audit_row_from_pair(
            sym, td,
            lre_lookup.get((sym, td)),
            mde_lookup.get((sym, td)),
            by_sym,
        ))
    print(f"  Audit rows: {len(audit_rows)}", flush=True)
    n_persisted = persist_audit_rows(conn, audit_rows)

    latest = params.get("trade_date") or edates[-1] if edates else max(r["trade_date"] for r in audit_rows)
    windows = _window_dates(latest)
    date_to_idx = {d: i for i, d in enumerate(edates)}

    timing_modes = ("same_day", "pullback")
    group_comparison: Dict[str, dict] = {}
    windows_oos: Dict[str, dict] = {}

    for timing in timing_modes:
        timing_label = "same_day_close" if timing == "same_day" else "next_day_not_extended"
        group_comparison[timing_label] = {}
        for wk, wr in windows.items():
            group_comparison[timing_label][wk] = {}
            for gt in DUAL_GATE_TYPES:
                trades = build_group_trades(audit_rows, by_sym, gt, timing=timing)
                group_comparison[timing_label][wk][gt] = extended_trade_metrics(trades, wr)
        windows_oos[timing_label] = group_comparison[timing_label].get("oos", {})

    primary_timing = "same_day_close"
    group_full = {gt: group_comparison[primary_timing]["full"][gt] for gt in DUAL_GATE_TYPES}
    group_oos = {gt: group_comparison[primary_timing]["oos"][gt] for gt in DUAL_GATE_TYPES}

    print("  Sequence audit...", flush=True)
    sequences = build_sequence_trades(pool, mde_lookup, by_sym, edates, date_to_idx)
    seq_metrics = {}
    for sk, trades in sequences.items():
        seq_metrics[sk] = {
            "full": extended_trade_metrics(trades),
            "oos": extended_trade_metrics(trades, (OOS_START, "2099-12-31")),
        }
    seq_oos = {sk: seq_metrics[sk]["oos"] for sk in seq_metrics}
    best_seq = max(
        (k for k in seq_oos if (seq_oos[k].get("top10_dominance_pct") or 100) < 35),
        key=lambda k: (seq_oos[k].get("net_PF_100bps") or 0, seq_oos[k].get("median_return") or -999),
        default="SAME_DAY_CONFLUENCE",
    )

    timing_diag = timing_comparison(audit_rows, by_sym)
    stop_diag = stop_diagnostic_confluence(audit_rows, by_sym)
    candidates = review_candidates_dual(
        conn, by_sym, fingerprints, thresholds, mde_lookup,
        ["OLFI", "HBCO", "EFIC", "EGAS"], latest,
    )

    verdict_code, verdict_reason = final_verdict(group_oos, seq_oos)

    lre_only_oos = group_oos.get("LRE_ONLY", {})
    mde_only_oos = group_oos.get("MDE_ONLY", {})
    conf_oos = group_oos.get("LRE_MDE_CONFLUENCE", {})
    lre_leads_oos = seq_oos.get("LRE_LEADS_MDE_CONFIRMATION", {})
    same_oos = seq_oos.get("SAME_DAY_CONFLUENCE", {})

    answers = {
        "هل اجتماع LRE و MDE أفضل من كل واحد وحده؟": (
            f"Confluence OOS PF={conf_oos.get('net_PF_100bps')} vs LRE_ONLY={lre_only_oos.get('net_PF_100bps')} "
            f"vs MDE_ONLY={mde_only_oos.get('net_PF_100bps')} — "
            f"{'نعم' if (conf_oos.get('net_PF_100bps') or 0) > max(lre_only_oos.get('net_PF_100bps') or 0, mde_only_oos.get('net_PF_100bps') or 0) else 'لا / هامشي'}"
        ),
        "هل LRE يجب أن يسبق MDE أم العكس؟": (
            f"same-day confluence PF={same_oos.get('net_PF_100bps')} median={same_oos.get('median_return')}% "
            f"(sanitized) | LRE→MDE PF={lre_leads_oos.get('net_PF_100bps')} dom={lre_leads_oos.get('top10_dominance_pct')}% "
            f"(outlier-contaminated) | MDE→LRE PF={seq_oos.get('MDE_FIRST_THEN_LRE', {}).get('net_PF_100bps')} — "
            f"أفضل موثوق: {best_seq}"
        ),
        "هل الدخول عند MDE confirmation يحل مشكلة timing؟": (
            f"LRE_LEADS_MDE stop={lre_leads_oos.get('stop_hit_ratio')}% vs same-day confluence "
            f"stop={same_oos.get('stop_hit_ratio')}% — "
            f"{'تحسن' if (lre_leads_oos.get('stop_hit_ratio') or 100) < (same_oos.get('stop_hit_ratio') or 100) else 'لا يكفي'}"
        ),
        "هل stop hits تنخفض؟": (
            f"Confluence structural stop={conf_oos.get('stop_hit_ratio')}% | "
            f"LRE_ONLY={lre_only_oos.get('stop_hit_ratio')}% | "
            f"MDE_ONLY={mde_only_oos.get('stop_hit_ratio')}% | "
            f"stop-8% OOS={stop_diag.get('stop_8pct', {}).get('oos', {}).get('stop_hit_ratio')}%"
        ),
        "هل OLFI/HBCO/EFIC/EGAS يملكون confluence حقيقي؟": (
            ", ".join(
                f"{s}={c.get('dual_gate_type')}" for s, c in candidates.items() if "error" not in c
            )
        ),
        "هل dual-gate يبقى monitoring-only أم يستحق shadow pilot؟": (
            f"{verdict_code} — shadow pilot only, no production"
        ),
    }

    type_counts = Counter(r["dual_gate_type"] for r in audit_rows)
    doc = {
        "at": at,
        "phase": "LRE-3.3",
        "invariants": PHASE_INVARIANTS,
        "trade_date_latest": latest,
        "audit_row_count": len(audit_rows),
        "db_rows_persisted": n_persisted,
        "dual_gate_type_counts": dict(type_counts),
        "group_comparison": group_comparison,
        "sequence_audit": seq_metrics,
        "sequence_oos": seq_oos,
        "windows_oos": windows_oos,
        "timing_diagnostic": timing_diag,
        "stop_diagnostic": stop_diag,
        "candidates": candidates,
        "best_sequence": best_seq,
        "verdict": {"code": verdict_code, "reason": verdict_reason},
        "answers": answers,
        "lre_failure_summary": (
            "LRE-3.0: PF@100=0.96, stop=61% — not trade gate. "
            "LRE-3.1: conservative PF=1.15 OOS but top-10 dominance blocked. "
            "LRE-3.2: RESEARCH_EDGE_MONITOR_ONLY — 3B/4A strongest; 4X misleading; "
            "56% stop hits had MFE≥5% later; pair LRE radar with MDE confirmation."
        ),
    }

    OUTPUTS["audit"].write_text(json.dumps({
        "at": at,
        "invariants": PHASE_INVARIANTS,
        "row_count": len(audit_rows),
        "type_counts": dict(type_counts),
        "group_oos": group_oos,
        "verdict": doc["verdict"],
        "sample_rows": audit_rows[:20],
    }, indent=2, default=str), encoding="utf-8")

    OUTPUTS["sequence"].write_text(json.dumps(seq_metrics, indent=2, default=str), encoding="utf-8")
    OUTPUTS["oos"].write_text(json.dumps({
        "at": at,
        "windows": {k: group_comparison[primary_timing][k] for k in windows},
        "sequence_oos": seq_oos,
        "verdict": doc["verdict"],
    }, indent=2, default=str), encoding="utf-8")
    OUTPUTS["candidates"].write_text(json.dumps(candidates, indent=2, default=str), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report(doc), encoding="utf-8")

    conn.close()
    print(f"  Verdict: {verdict_code}", flush=True)
    print(json.dumps({"success": True, "verdict": verdict_code, "audit_rows": len(audit_rows)}, indent=2))
    return {"success": True, "verdict": verdict_code, "audit_rows": len(audit_rows)}


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    cmd_run(p)
