#!/usr/bin/env python3
"""
LRE Phase 3.6A — Historical Walk-Forward Capped Shadow Pilot.

Simulates daily LRE×MDE confluence without future leakage in signal generation.
Shadow / research only — no client path, Telegram, actionable, or promotion.
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
SCRIPTS = ROOT / "scripts" / "python"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    connect,
    ensure_tables,
    load_all_bars,
    table_exists,
)
from lre_3_1_filters import calibrate_a_thresholds, load_fingerprints  # noqa: E402
from lre_3_2_stage_rebuild import DEDUP_COOLDOWN, simulate_from_entry  # noqa: E402
from lre_3_3_dual_gate_audit import forward_metrics  # noqa: E402
from lre_3_4_confluence_robustness import (  # noqa: E402
    _resolve_entry_extended,
    _simulate_entry_field,
    load_sectors,
)
from lre_3_4_robustness import confluence_metrics, top10_dominance  # noqa: E402
from lre_3_5_pilot_caps import (  # noqa: E402
    apply_caps_to_trades,
    assign_bucket,
    cap_config_for_mode,
    clean_confluence,
    max_sector_dominance,
    sector_dominance,
)
from lre_3_6a_causal import (  # noqa: E402
    STATIC_THRESHOLD_DEFAULTS,
    build_causal_signal,
    calibrate_thresholds_causal,
    load_explosion_events,
    load_mde_by_date,
    map_bucket_36a,
    mde_watch_row,
)
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402
from mde_walkforward_shadow import pf  # noqa: E402

PHASE = "LRE-3.6A"
CALIBRATION_START = "2020-12-10"
SIM_START = "2025-01-01"
SIM_END = "2026-06-11"
ROLLING_SESSIONS = 500

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": PHASE,
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
    "shadow_pilot_only": True,
}

WINDOW_MODES = ("expanding", "rolling_500")
THRESHOLD_MODES = ("STATIC_THRESHOLDS", "WALK_FORWARD_RECALIBRATED_THRESHOLDS")
ENTRY_RULES = {
    "same_day_close": "same_day",
    "next_day_close": "next_day_close",
    "next_day_not_extended": "pullback",
    "wait_1d_confirmation": "wait_1d_confirm",
}
CAP_COMPARE_MODES = (
    "raw",
    "sector_cap_only",
    "finance_cap_25",
    "symbol_cap_only",
    "symbol_sector_finance_cap_25",
)
BUCKET_FILTERS = (
    "Clean_Confluence_Core",
    "Controlled_4B_Monitor",
    "Core_plus_4B",
    "New_Pattern_Monitor",
    "All_eligible",
)
PILOT_CAP_MODE = "symbol_sector_finance_cap_25"

OUTPUTS = {
    "results": DATA / "lre_3_6a_walk_forward_results.json",
    "ledger": DATA / "lre_3_6a_walk_forward_ledger.json",
    "caps": DATA / "lre_3_6a_caps_comparison.json",
    "leakage": DATA / "lre_3_6a_threshold_leakage_audit.json",
    "buckets": DATA / "lre_3_6a_bucket_results.json",
    "signals_cache": DATA / "lre_3_6a_signals_cache.json",
    "report": ROOT / "docs/LRE_PHASE_3_6A_HISTORICAL_WALK_FORWARD_PILOT_REPORT.md",
}


def ensure_walk_forward_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_walk_forward_shadow_pilot (
        sim_date                TEXT NOT NULL,
        symbol                  TEXT NOT NULL,
        sector                  TEXT,
        mode                    TEXT NOT NULL,
        threshold_mode          TEXT NOT NULL,
        lre_sub_stage           TEXT,
        lre_eps                 REAL,
        mde_score               REAL,
        dual_gate_score         REAL,
        bucket                  TEXT,
        cap_status              TEXT,
        pilot_eligible          INTEGER DEFAULT 0,
        simulated_entry_price   REAL,
        simulated_entry_rule    TEXT NOT NULL,
        forward_return_5d       REAL,
        forward_return_10d      REAL,
        forward_return_20d      REAL,
        forward_return_30d      REAL,
        mfe_20d                 REAL,
        mae_20d                 REAL,
        stop_8_hit              INTEGER DEFAULT 0,
        stop_10_hit             INTEGER DEFAULT 0,
        exit_reason             TEXT,
        artifact_flag           INTEGER DEFAULT 0,
        liquidity_flag          INTEGER DEFAULT 0,
        already_exploded_flag   INTEGER DEFAULT 0,
        client_path_allowed     INTEGER DEFAULT 0,
        created_at              TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (sim_date, symbol, mode, threshold_mode, simulated_entry_rule)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_wf_pilot_mode ON lre_walk_forward_shadow_pilot(mode, threshold_mode);
    CREATE INDEX IF NOT EXISTS idx_lre_wf_pilot_bucket ON lre_walk_forward_shadow_pilot(bucket);
    """)


def _static_thresholds(conn, by_sym: dict, fingerprints: dict) -> dict:
    th = calibrate_a_thresholds(conn, by_sym, fingerprints)
    th["source"] = "STATIC_RESEARCH_THRESHOLD"
    th["leakage_warning"] = (
        "Calibrated from full lre_explosion_events sample — may contain research calibration leakage."
    )
    return th


def _thresholds_for_date(
    static_th: dict,
    events: List[dict],
    by_sym: dict,
    fingerprints: dict,
    sim_date: str,
    threshold_mode: str,
    window_mode: str,
    trading_dates: List[str],
) -> dict:
    if threshold_mode == "STATIC_THRESHOLDS":
        return dict(static_th)
    rolling_dates = None
    if window_mode == "rolling_500":
        idx = trading_dates.index(sim_date) if sim_date in trading_dates else -1
        if idx > 0:
            rolling_dates = set(trading_dates[max(0, idx - ROLLING_SESSIONS):idx])
    return calibrate_thresholds_causal(events, by_sym, fingerprints, sim_date, rolling_dates)


def _bar_idx(bars: List[dict], d: str) -> Optional[int]:
    return next((i for i, b in enumerate(bars) if b["date"] == d), None)


def _simulate_entry_row(
    row: dict,
    bars_by_sym: dict,
    entry_rule: str,
    timing: str,
) -> Optional[dict]:
    sym, td = row["symbol"], row.get("sim_date") or row.get("trade_date")
    bars = bars_by_sym.get(sym)
    if not bars:
        return None
    sig_idx = _bar_idx(bars, td)
    if sig_idx is None:
        return None
    entry_idx, entry_label, entry_field = _resolve_entry_extended(bars, sig_idx, timing)
    if entry_idx is None:
        return None
    sim20 = _simulate_entry_field(bars, entry_idx, 20, "base_low", None, entry_field)
    sim8 = _simulate_entry_field(bars, entry_idx, 20, "base_low", 8.0, entry_field)
    sim10 = _simulate_entry_field(bars, entry_idx, 20, "base_low", 10.0, entry_field)
    fwd = forward_metrics(bars, entry_idx)
    out = {
        **row,
        "simulated_entry_price": sim20.get("entry_price"),
        "simulated_entry_rule": entry_rule,
        "gross_return": sim20.get("gross_return"),
        "exit_reason": sim20.get("exit_reason"),
        "mfe_20d": sim20.get("MFE"),
        "mae_20d": sim20.get("MAE"),
        "stop_8_hit": 1 if sim8.get("exit_reason") == "stop_hit" else 0,
        "stop_10_hit": 1 if sim10.get("exit_reason") == "stop_hit" else 0,
        "forward_return_5d": fwd.get("forward_return_5d"),
        "forward_return_10d": fwd.get("forward_return_10d"),
        "forward_return_20d": fwd.get("forward_return_20d"),
        "forward_return_30d": fwd.get("forward_return_30d"),
        "entry_label": entry_label,
        "signal_date": td,
    }
    return out


def run_walk_forward_signals(
    conn,
    by_sym: dict,
    fingerprints: dict,
    sectors: Dict[str, str],
    mde_by_date: Dict[str, List[dict]],
    trading_dates: List[str],
    static_th: dict,
    events: List[dict],
    window_mode: str,
    threshold_mode: str,
) -> Tuple[List[dict], List[dict]]:
    """Daily causal confluence generation; caps applied once via retrospective trim."""
    sim_dates = [d for d in trading_dates if SIM_START <= d <= SIM_END]
    raw_confluence: List[dict] = []
    ledger_raw: List[dict] = []

    date_index = {sym: {b["date"]: i for i, b in enumerate(bars)} for sym, bars in by_sym.items()}

    for sim_date in sim_dates:
        thresholds = _thresholds_for_date(
            static_th, events, by_sym, fingerprints, sim_date,
            threshold_mode, window_mode, trading_dates,
        )
        for mde_row in mde_by_date.get(sim_date, []):
            if not mde_watch_row(mde_row):
                continue
            sym = mde_row["symbol"]
            bars = by_sym.get(sym)
            if not bars:
                continue
            idx = date_index.get(sym, {}).get(sim_date)
            if idx is None:
                continue
            sig = build_causal_signal(
                conn, sym, sim_date, bars, idx, fingerprints, thresholds, mde_row,
                sectors.get(sym, "Unknown"),
            )
            if not sig or sig.get("dual_gate_type") != "LRE_MDE_CONFLUENCE":
                continue
            sig["sim_date"] = sim_date
            sig["trade_date"] = sim_date
            sig["mode"] = window_mode
            sig["threshold_mode"] = threshold_mode
            sig["sector"] = sectors.get(sym, "Unknown")
            raw_confluence.append(sig)

    hist_by_sym: Dict[str, int] = defaultdict(int)
    for sig in raw_confluence:
        hist_by_sym[sig["symbol"]] += 1

    accepted_all, all_rows = apply_caps_to_trades(
        sorted(raw_confluence, key=lambda x: x["sim_date"]),
        PILOT_CAP_MODE,
        hist_trades_by_symbol=hist_by_sym,
    )
    accepted_keys = {
        (t["symbol"], t.get("sim_date") or t.get("trade_date")) for t in accepted_all
    }

    for row in all_rows:
        td = row.get("sim_date") or row.get("trade_date")
        key = (row["symbol"], td)
        row["pilot_eligible"] = key in accepted_keys
        bucket = map_bucket_36a(
            row,
            bool(row.get("pilot_eligible")),
            row.get("cap_status") or "",
            row.get("cap_reason") or "",
        )
        ledger_raw.append({
            "sim_date": td,
            "symbol": row["symbol"],
            "sector": row.get("sector"),
            "mode": window_mode,
            "threshold_mode": threshold_mode,
            "lre_sub_stage": row.get("lre_sub_stage"),
            "lre_eps": row.get("lre_eps"),
            "mde_score": row.get("mde_score"),
            "dual_gate_score": row.get("dual_gate_score"),
            "bucket": bucket,
            "cap_status": row.get("cap_status"),
            "pilot_eligible": int(bool(row.get("pilot_eligible"))),
            "pilot_bucket": row.get("pilot_bucket"),
            "artifact_flag": int(row.get("artifact_flag") or 0),
            "liquidity_flag": int(row.get("liquidity_flag") or 0),
            "already_exploded_flag": int(row.get("already_exploded_flag") or 0),
        })

    return list(accepted_all), ledger_raw, list(raw_confluence)


def _trade_from_signal(sig: dict, sim: dict) -> dict:
    return {
        "symbol": sig["symbol"],
        "sector": sig.get("sector"),
        "signal_date": sig.get("sim_date") or sig.get("trade_date"),
        "gross_return": sim.get("gross_return"),
        "exit_reason": sim.get("exit_reason"),
        "MFE": sim.get("mfe_20d"),
        "MAE": sim.get("mae_20d"),
        "dual_gate_score": sig.get("dual_gate_score"),
        "pilot_bucket": sig.get("pilot_bucket") or sig.get("bucket"),
        "forward_return_5d": sim.get("forward_return_5d"),
        "forward_return_10d": sim.get("forward_return_10d"),
        "forward_return_20d": sim.get("forward_return_20d"),
        "forward_return_30d": sim.get("forward_return_30d"),
    }


def build_simulated_trades(
    signals: List[dict],
    bars_by_sym: dict,
    entry_rule: str,
) -> List[dict]:
    timing = ENTRY_RULES[entry_rule]
    trades = []
    for sig in signals:
        sim = _simulate_entry_row(sig, bars_by_sym, entry_rule, timing)
        if sim and sim.get("gross_return") is not None:
            trades.append(_trade_from_signal(sig, sim))
    dates = sorted({t["signal_date"] for t in trades})
    return dedup_trades(trades, dates, DEDUP_COOLDOWN)


def _bucket_filter(trades: List[dict], filt: str) -> List[dict]:
    if filt == "All_eligible":
        return trades
    if filt == "Core_plus_4B":
        ok = {"Clean_Confluence_Core", "Controlled_4B_Monitor"}
        return [t for t in trades if (t.get("pilot_bucket") or t.get("bucket")) in ok]
    return [t for t in trades if (t.get("pilot_bucket") or t.get("bucket")) == filt]


def extended_metrics(trades: List[dict], include_periods: bool = True) -> dict:
    if not trades:
        return {"trade_count": 0, "sample_ok": False}
    m100 = confluence_metrics(trades, 100)
    nets = [net_return(t.get("gross_return") or 0, 100) for t in trades]
    nets150 = [net_return(t.get("gross_return") or 0, 150) for t in trades]
    wins150 = [r for r in nets150 if r >= 5]
    loss150 = [abs(r) for r in nets150 if r < 5]
    gross = [t.get("gross_return") or 0 for t in trades]
    sec_dom = sector_dominance(trades)
    fin_pct = sec_dom.get("Finance", 0.0)
    sym_dom = Counter(t["symbol"] for t in trades)
    top_sym = round(100 * max(sym_dom.values()) / len(trades), 1) if trades else 0

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in sorted(trades, key=lambda x: x["signal_date"]):
        cum += net_return(r.get("gross_return") or 0, 100)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    monthly: Dict[str, int] = defaultdict(int)
    for t in trades:
        monthly[t["signal_date"][:7]] += 1

    periods = {}
    if include_periods:
        def trades_in_period(lo: str, hi: str) -> List[dict]:
            return [t for t in trades if lo <= t["signal_date"] <= hi]

        periods = {
            "2025_H1": extended_metrics(trades_in_period("2025-01-01", "2025-06-30"), include_periods=False),
            "2025_H2": extended_metrics(trades_in_period("2025-07-01", "2025-12-31"), include_periods=False),
            "2026_YTD": extended_metrics(trades_in_period("2026-01-01", SIM_END), include_periods=False),
        }

    return {
        **m100,
        "PF_100bps": m100.get("net_PF"),
        "PF_150bps": round(pf(wins150, loss150), 2) if wins150 or loss150 else None,
        "PF_200bps": confluence_metrics(trades, 200).get("net_PF"),
        "average_return": round(mean(nets), 3) if nets else None,
        "hit_stop_8pct": round(100 * sum(1 for g in gross if g <= -8) / len(gross), 1),
        "hit_stop_10pct": round(100 * sum(1 for g in gross if g <= -10) / len(gross), 1),
        "max_drawdown_proxy": round(max_dd, 3),
        "top10_dominance_pct": top10_dominance(trades, 100),
        "max_sector_dominance_pct": max_sector_dominance(trades),
        "finance_exposure_pct": fin_pct,
        "top_symbol_concentration_pct": top_sym,
        "monthly_distribution": dict(sorted(monthly.items())),
        "periods": periods,
    }


def caps_comparison(all_signals: List[dict], bars_by_sym: dict) -> dict:
    """Replay caps on chronological confluence signals (clean only path)."""
    base = sorted(all_signals, key=lambda x: x.get("sim_date") or x.get("trade_date", ""))
    hist: Dict[str, int] = defaultdict(int)
    out = {}
    for cap_mode in CAP_COMPARE_MODES:
        accepted, _ = apply_caps_to_trades(base, cap_mode, hist_trades_by_symbol=hist)
        trades = []
        for sig in accepted:
            sim = _simulate_entry_row(sig, bars_by_sym, "same_day_close", "same_day")
            if sim:
                trades.append(_trade_from_signal(sig, sim))
        if not trades:
            out[cap_mode] = {"trade_count": 0}
            continue
        dates = sorted({t["signal_date"] for t in trades})
        deduped = dedup_trades(trades, dates, DEDUP_COOLDOWN)
        out[cap_mode] = extended_metrics(deduped)
    return out


def load_replay_baseline(conn, bars_by_sym: dict) -> dict:
    if not table_exists(conn, "lre_mde_dual_gate_audit"):
        return {"trade_count": 0, "note": "audit table missing"}
    rows = conn.execute(
        """SELECT * FROM lre_mde_dual_gate_audit
           WHERE dual_gate_type='LRE_MDE_CONFLUENCE' AND trade_date >= ? AND trade_date <= ?""",
        (SIM_START, SIM_END),
    ).fetchall()
    trades = []
    for r in rows:
        d = dict(r)
        sim = _simulate_entry_row(
            {**d, "sim_date": d["trade_date"]},
            bars_by_sym,
            "same_day_close",
            "same_day",
        )
        if sim:
            trades.append(_trade_from_signal(d, sim))
    dates = sorted({t["signal_date"] for t in trades})
    deduped = dedup_trades(trades, dates, DEDUP_COOLDOWN)
    return extended_metrics(deduped)


def persist_ledger(conn, rows: List[dict]) -> int:
    if not rows:
        return 0
    sql = """
    INSERT OR REPLACE INTO lre_walk_forward_shadow_pilot (
        sim_date, symbol, sector, mode, threshold_mode, lre_sub_stage, lre_eps,
        mde_score, dual_gate_score, bucket, cap_status, pilot_eligible,
        simulated_entry_price, simulated_entry_rule,
        forward_return_5d, forward_return_10d, forward_return_20d, forward_return_30d,
        mfe_20d, mae_20d, stop_8_hit, stop_10_hit, exit_reason,
        artifact_flag, liquidity_flag, already_exploded_flag, client_path_allowed
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    batch = []
    for r in rows:
        batch.append((
            r["sim_date"], r["symbol"], r.get("sector"), r["mode"], r["threshold_mode"],
            r.get("lre_sub_stage"), r.get("lre_eps"), r.get("mde_score"), r.get("dual_gate_score"),
            r.get("bucket"), r.get("cap_status"), r.get("pilot_eligible"),
            r.get("simulated_entry_price"), r.get("simulated_entry_rule"),
            r.get("forward_return_5d"), r.get("forward_return_10d"),
            r.get("forward_return_20d"), r.get("forward_return_30d"),
            r.get("mfe_20d"), r.get("mae_20d"), r.get("stop_8_hit"), r.get("stop_10_hit"),
            r.get("exit_reason"),
            r.get("artifact_flag", 0), r.get("liquidity_flag", 0),
            r.get("already_exploded_flag", 0), 0,
        ))
    for i in range(0, len(batch), 400):
        conn.executemany(sql, batch[i:i + 400])
    conn.commit()
    return len(batch)


def evaluate_verdict(
    primary: dict,
    static_m: dict,
    walk_m: dict,
    replay: dict,
    leakage: dict,
) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    m = primary.get("metrics", {})
    n = m.get("trade_count") or 0
    pf100 = m.get("PF_100bps") or 0
    pf150 = m.get("PF_150bps") or 0
    med = m.get("median_return") or 0
    top10 = m.get("top10_dominance_pct") or 100
    ytd = (m.get("periods") or {}).get("2026_YTD", {})
    ytd_pf = ytd.get("PF_100bps") or ytd.get("net_PF") or 0

    core = primary.get("bucket_metrics", {}).get("Clean_Confluence_Core", {})
    all_el = primary.get("bucket_metrics", {}).get("All_eligible", {})
    core_pf = core.get("PF_100bps") or core.get("net_PF") or 0
    all_pf = all_el.get("PF_100bps") or all_el.get("net_PF") or 0

    static_pf = static_m.get("PF_100bps") or static_m.get("net_PF") or 0
    walk_pf = walk_m.get("PF_100bps") or walk_m.get("net_PF") or 0
    replay_pf = replay.get("PF_100bps") or replay.get("net_PF") or 0

    if pf100 < 1.0 and walk_pf < 1.0:
        return "FAIL_NO_FORWARD_EDGE", ["PF below 1.0 in walk-forward"]

    if static_pf >= 1.3 and walk_pf < 1.1:
        return "RESEARCH_EDGE_LEAKAGE_RISK", [
            "Static thresholds materially outperform walk-forward recalibration",
            leakage.get("warning", ""),
        ]

    if pf100 >= 1.3 and pf150 >= 1.2 and med > 0 and n >= 40:
        if top10 >= 35:
            return "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED", [
                f"PF ok (PF@100={pf100}) but top-10 dominance {top10}% > 35%",
            ]
        if (core_pf or 0) >= (all_pf or 0) and (ytd_pf or 0) >= 1.0:
            return "PASS_WALK_FORWARD_SHADOW_PILOT", [
                "Meets PF, median, sample, Clean Core >= All, 2026 YTD stable",
            ]
        return "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED", [
            f"PF ok but Clean Core ({core_pf}) vs All ({all_pf}) or 2026 YTD ({ytd_pf}) weak",
        ]

    if (replay_pf or 0) > (walk_pf or 0) * 1.25:
        return "RESEARCH_EDGE_MONITOR_ONLY", [
            "Walk-forward weaker than LRE-3.3/3.5 replay baseline",
        ]

    if pf100 < 1.3 or n < 40 or med <= 0:
        return "FAIL_NO_FORWARD_EDGE", ["Does not meet minimum acceptance thresholds"]

    return "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED", ["Mixed metrics — monitor"]


def build_report(
    results: dict,
    leakage: dict,
    caps: dict,
    buckets: dict,
    verdict: str,
    answers: dict,
) -> str:
    inv = json.dumps(PHASE_INVARIANTS, indent=2)
    lines = [
        "# LRE-3.6A — Historical Walk-Forward Capped Shadow Pilot",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Verdict:** `{verdict}`",
        "",
        "> Forward-like shadow pilot validated ≠ production / Telegram / actionable / client path.",
        "",
        "## Invariants",
        "",
        "```json",
        inv,
        "```",
        "",
        "## A. Why walk-forward?",
        "",
        "- Simulates each historical day as if it were “today” without using future bars in signal generation.",
        "- Surfaces threshold leakage and overfitting before waiting months of real forward data.",
        "- Complements LRE-3.5 replay (which used full-sample calibration).",
        "",
        "## B. Methodology",
        "",
        f"- Simulation window: **{SIM_START} → {SIM_END}**",
        f"- Calibration warmup from **{CALIBRATION_START}**",
        "- **Expanding window:** train/calibrate on all sessions before `trade_date`.",
        f"- **Rolling window:** last **{ROLLING_SESSIONS}** sessions before `trade_date`.",
        "- **STATIC_THRESHOLDS:** LRE-3.x full-sample A-sim calibration (leakage risk flagged).",
        "- **WALK_FORWARD_RECALIBRATED_THRESHOLDS:** A-sim percentiles only from prior events.",
        "- MDE rows read from `egx_market_discovery_daily` per day (metrics_json analog fields may carry backfill leakage — see leakage audit).",
        "- Caps in pilot path: symbol 10%, sector 25%, finance 25%.",
        "",
        "## C. Results (primary: expanding + walk-forward thresholds, same-day entry)",
        "",
    ]
    primary = results.get("primary", {})
    pm = primary.get("metrics", {})
    lines += [
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Trades | {pm.get('trade_count')} |",
        f"| PF@100bps | {pm.get('PF_100bps')} |",
        f"| PF@150bps | {pm.get('PF_150bps')} |",
        f"| Median return | {pm.get('median_return')}% |",
        f"| Hit +5% | {pm.get('hit_5pct')}% |",
        f"| Top-10 dominance | {pm.get('top10_dominance_pct')}% |",
        f"| Finance exposure | {pm.get('finance_exposure_pct')}% |",
        "",
        "### Buckets",
        "",
    ]
    for b, bm in (buckets.get("primary") or {}).items():
        lines.append(
            f"- **{b}:** n={bm.get('trade_count')} PF@100={bm.get('PF_100bps') or bm.get('net_PF')} "
            f"median={bm.get('median_return')}%"
        )
    lines += ["", "## D. Caps impact", ""]
    for mode, cm in caps.items():
        lines.append(
            f"- **{mode}:** n={cm.get('trade_count')} PF={cm.get('PF_100bps') or cm.get('net_PF')} "
            f"top10={cm.get('top10_dominance_pct')}% finance={cm.get('finance_exposure_pct')}%"
        )
    lines += ["", "## E. Entry timing", ""]
    for er, em in (results.get("entry_comparison") or {}).items():
        lines.append(
            f"- **{er}:** n={em.get('trade_count')} PF={em.get('PF_100bps') or em.get('net_PF')} "
            f"median={em.get('median_return')}%"
        )
    lines += ["", "## F. Leakage audit", ""]
    lines.append(f"- Static threshold source: `{leakage.get('static_source')}`")
    lines.append(f"- Warning: {leakage.get('warning')}")
    lines.append(
        f"- Static PF@100: {leakage.get('static_pf')} vs Walk-forward PF@100: {leakage.get('walk_pf')}"
    )
    lines.append(f"- Collapse without static: {leakage.get('collapse_without_static')}")
    lines += ["", "## G. Final decision", "", f"**`{verdict}`**", ""]
    lines += ["## Answers", ""]
    for k, v in answers.items():
        lines.append(f"1. **{k}** — {v}")
    lines.append("")
    lines.append("---")
    lines.append("*Shadow research only. `client_path_allowed=False` always.*")
    return "\n".join(lines)


def _signal_cache_key(window_mode: str, threshold_mode: str) -> str:
    return f"{window_mode}__{threshold_mode}"


def _serialize_signal(sig: dict) -> dict:
    out = {k: v for k, v in sig.items() if not k.startswith("_")}
    return out


def _load_signals_cache() -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
    path = OUTPUTS["signals_cache"]
    if not path.exists():
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw.get("signals", {}), raw.get("raw_confluence", {})
    except (json.JSONDecodeError, OSError):
        return {}, {}


def _save_signals_cache(
    signals_by_combo: Dict[Tuple[str, str], List[dict]],
    raw_by_combo: Optional[Dict[Tuple[str, str], List[dict]]] = None,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sim_window": {"start": SIM_START, "end": SIM_END},
        "signals": {
            _signal_cache_key(w, t): [_serialize_signal(s) for s in sigs]
            for (w, t), sigs in signals_by_combo.items()
        },
        "raw_confluence": {
            _signal_cache_key(w, t): [_serialize_signal(s) for s in (raw_by_combo or {}).get((w, t), [])]
            for (w, t) in signals_by_combo
        },
    }
    OUTPUTS["signals_cache"].write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run() -> dict:
    conn = connect()
    ensure_tables(conn)
    ensure_walk_forward_table(conn)
    by_sym, _ = load_all_bars(conn)
    fingerprints = load_fingerprints()
    sectors = load_sectors(conn)
    events = load_explosion_events(conn)
    static_th = _static_thresholds(conn, by_sym, fingerprints)
    mde_by_date = load_mde_by_date(conn, "2022-04-05")

    trading_dates = sorted(mde_by_date.keys())
    if SIM_END not in trading_dates:
        trading_dates = [d for d in trading_dates if d <= SIM_END]

    all_results: dict = {"phase": PHASE, "invariants": PHASE_INVARIANTS}
    ledger_db_rows: List[dict] = []
    signals_by_combo: Dict[Tuple[str, str], List[dict]] = {}
    raw_by_combo: Dict[Tuple[str, str], List[dict]] = {}
    cached, cached_raw = _load_signals_cache()
    cache_stale = bool(cached) and all(len(v) == 0 for v in cached.values())

    for window_mode in WINDOW_MODES:
        for threshold_mode in THRESHOLD_MODES:
            key = (window_mode, threshold_mode)
            cache_key = _signal_cache_key(window_mode, threshold_mode)
            if cached and not cache_stale and cache_key in cached:
                print(f"[3.6A] load cached signals {window_mode} / {threshold_mode}", flush=True)
                accepted = cached[cache_key]
                raw_by_combo[key] = cached_raw.get(cache_key, [])
            else:
                print(f"[3.6A] walk-forward signals {window_mode} / {threshold_mode} ...", flush=True)
                accepted, _raw, raw_conf = run_walk_forward_signals(
                    conn, by_sym, fingerprints, sectors, mde_by_date, trading_dates,
                    static_th, events, window_mode, threshold_mode,
                )
                raw_by_combo[key] = raw_conf
            signals_by_combo[key] = accepted

    if not cached or cache_stale:
        _save_signals_cache(signals_by_combo, raw_by_combo)

    for window_mode in WINDOW_MODES:
        for threshold_mode in THRESHOLD_MODES:
            key = (window_mode, threshold_mode)
            accepted = signals_by_combo[key]
            for entry_rule in ENTRY_RULES:
                trades = build_simulated_trades(accepted, by_sym, entry_rule)
                for t in trades:
                    sig = next(
                        (s for s in accepted
                         if s["symbol"] == t["symbol"] and s["sim_date"] == t["signal_date"]),
                        None,
                    )
                    if not sig:
                        continue
                    sim = _simulate_entry_row(sig, by_sym, entry_rule, ENTRY_RULES[entry_rule])
                    if sim:
                        ledger_db_rows.append(sim)

    persist_ledger(conn, ledger_db_rows)

    # Primary analysis: expanding + walk-forward recalibrated + same_day_close
    primary_key = ("expanding", "WALK_FORWARD_RECALIBRATED_THRESHOLDS")
    primary_signals = signals_by_combo.get(primary_key, [])
    primary_trades = build_simulated_trades(primary_signals, by_sym, "same_day_close")

    entry_comparison = {}
    for er in ENTRY_RULES:
        entry_comparison[er] = extended_metrics(
            build_simulated_trades(primary_signals, by_sym, er)
        )

    bucket_metrics_primary = {}
    for bf in BUCKET_FILTERS:
        bucket_metrics_primary[bf] = extended_metrics(_bucket_filter(primary_trades, bf))

    static_key = ("expanding", "STATIC_THRESHOLDS")
    walk_key = primary_key
    static_trades = build_simulated_trades(signals_by_combo.get(static_key, []), by_sym, "same_day_close")
    walk_trades = primary_trades
    static_m = extended_metrics(static_trades)
    walk_m = extended_metrics(walk_trades)

    mode_comparison = {}
    for wm in WINDOW_MODES:
        for tm in THRESHOLD_MODES:
            mode_comparison[f"{wm}_{tm}"] = extended_metrics(
                build_simulated_trades(signals_by_combo.get((wm, tm), []), by_sym, "same_day_close")
            )

    caps_comp = {}
    for wm in WINDOW_MODES:
        raw_key = (wm, "WALK_FORWARD_RECALIBRATED_THRESHOLDS")
        caps_comp[wm] = caps_comparison(raw_by_combo.get(raw_key, []), by_sym)

    replay = load_replay_baseline(conn, by_sym)

    leakage = {
        "static_source": static_th.get("source"),
        "static_thresholds": {
            k: static_th.get(k) for k in ("balanced", "conservative", "ultra", "calibrated_n")
        },
        "warning": static_th.get("leakage_warning"),
        "static_pf": static_m.get("PF_100bps"),
        "walk_pf": walk_m.get("PF_100bps"),
        "static_median": static_m.get("median_return"),
        "walk_median": walk_m.get("median_return"),
        "collapse_without_static": bool(
            (static_m.get("PF_100bps") or 0) >= 1.3 and (walk_m.get("PF_100bps") or 0) < 1.1
        ),
        "mde_analog_leakage_risk": (
            "egx_market_discovery_daily metrics_json analog_hit/PF may be backfilled with future data"
        ),
        "mode_comparison": mode_comparison,
    }

    primary = {
        "mode": "expanding",
        "threshold_mode": "WALK_FORWARD_RECALIBRATED_THRESHOLDS",
        "entry_rule": "same_day_close",
        "metrics": walk_m,
        "bucket_metrics": bucket_metrics_primary,
    }

    verdict, verdict_reasons = evaluate_verdict(primary, static_m, walk_m, replay, leakage)

    answers = {
        "هل confluence يعيش في walk-forward؟": (
            f"نعم جزئياً — PF@100={walk_m.get('PF_100bps')} على n={walk_m.get('trade_count')} "
            f"(مقابل replay {replay.get('PF_100bps')})"
            if (walk_m.get("PF_100bps") or 0) >= 1.1
            else f"ضعيف — PF@100={walk_m.get('PF_100bps')}"
        ),
        "هل النتيجة مشابهة لـ replay أم أضعف؟": (
            f"أضعف من replay: walk-forward {walk_m.get('PF_100bps')} vs audit replay {replay.get('PF_100bps')}"
            if (replay.get("PF_100bps") or 0) > (walk_m.get("PF_100bps") or 0) * 1.05
            else "قريبة من replay"
        ),
        "هل caps تنجح تاريخياً بدون قتل edge؟": (
            f"combined caps PF={caps_comp.get('expanding', {}).get('symbol_sector_finance_cap_25', {}).get('PF_100bps')} "
            f"vs raw {caps_comp.get('expanding', {}).get('raw', {}).get('PF_100bps')}"
        ),
        "هل thresholds فيها leakage؟": (
            "خطر leakage مع STATIC؛ "
            + ("النتيجة تنهار بدون static" if leakage["collapse_without_static"] else "recalibrated قريبة من static")
        ),
        "هل Clean Core أفضل من 4B؟": (
            f"Core PF={bucket_metrics_primary.get('Clean_Confluence_Core', {}).get('PF_100bps')} "
            f"vs 4B PF={bucket_metrics_primary.get('Controlled_4B_Monitor', {}).get('PF_100bps')}"
        ),
        "هل نستمر في forward الحقيقي أم نعود للتعديل؟": (
            "استمر في forward shadow فقط" if verdict == "PASS_WALK_FORWARD_SHADOW_PILOT"
            else "راقب / عدّل thresholds قبل forward" if verdict in (
                "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED",
                "RESEARCH_EDGE_MONITOR_ONLY",
            )
            else "لا forward — راجع leakage أو ألغِ edge"
        ),
    }

    results_payload = {
        "phase": PHASE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "invariants": PHASE_INVARIANTS,
        "sim_window": {"start": SIM_START, "end": SIM_END},
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "primary": primary,
        "mode_comparison": mode_comparison,
        "entry_comparison": entry_comparison,
        "replay_baseline": replay,
        "static_vs_walk": {"static": static_m, "walk_forward": walk_m},
        "answers": answers,
        "max_verdict": "Forward-like shadow pilot validated"
        if verdict == "PASS_WALK_FORWARD_SHADOW_PILOT"
        else verdict,
    }

    buckets_payload = {"primary": bucket_metrics_primary, "mode_comparison": {}}
    for k, sigs in signals_by_combo.items():
        trades = build_simulated_trades(sigs, by_sym, "same_day_close")
        buckets_payload["mode_comparison"][f"{k[0]}_{k[1]}"] = {
            bf: extended_metrics(_bucket_filter(trades, bf)) for bf in BUCKET_FILTERS
        }

    OUTPUTS["results"].write_text(json.dumps(results_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUTS["ledger"].write_text(
        json.dumps(ledger_db_rows[:5000], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    OUTPUTS["caps"].write_text(json.dumps(caps_comp, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUTS["leakage"].write_text(json.dumps(leakage, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUTS["buckets"].write_text(json.dumps(buckets_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUTS["report"].write_text(
        build_report(results_payload, leakage, caps_comp.get("expanding", {}), buckets_payload, verdict, answers),
        encoding="utf-8",
    )

    print(json.dumps({"verdict": verdict, "trades": walk_m.get("trade_count"), "PF_100": walk_m.get("PF_100bps")}))
    return results_payload


if __name__ == "__main__":
    run()
