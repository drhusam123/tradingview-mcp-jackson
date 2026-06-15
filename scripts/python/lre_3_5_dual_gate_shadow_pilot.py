#!/usr/bin/env python3
"""
LRE Phase 3.5 — Dual-Gate Shadow Pilot Design with Caps.

Shadow pilot only — no client path, Telegram, actionable, or promotion.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

from egx_liquidity_rotation_engine import (  # noqa: E402
    LRE_INVARIANTS,
    connect,
    ensure_tables,
    load_all_bars,
    table_exists,
)
from lre_3_1_filters import calibrate_a_thresholds, enrich_signal, load_fingerprints  # noqa: E402
from lre_3_2_stage_rebuild import DEDUP_COOLDOWN, OOS_START, in_window  # noqa: E402
from lre_3_2_stages import classify_substage  # noqa: E402
from lre_3_3_dual_gate_audit import (  # noqa: E402
    audit_row_from_pair,
    forward_metrics,
    trade_from_audit,
)
from lre_3_4_confluence_robustness import _mde_rec_for_symbol, period_windows  # noqa: E402
from lre_3_4_robustness import bootstrap_confluence, confluence_metrics, top10_dominance  # noqa: E402
from lre_3_5_pilot_caps import (  # noqa: E402
    CAP_MODES,
    PILOT_BUCKETS,
    apply_caps_to_trades,
    assign_bucket,
    cap_config_for_mode,
    clean_confluence,
    max_sector_dominance,
    sector_dominance,
    symbol_concentration,
    top_contributor_family_flag,
)
from mde_client_grade_edge_validation import dedup_trades, net_return  # noqa: E402

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": "LRE-3.5",
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
    "shadow_pilot_only": True,
}

OUTPUTS = {
    "caps_replay": DATA / "lre_3_5_pilot_caps_replay.json",
    "pilot_last": DATA / "lre_3_5_shadow_pilot_last.json",
    "buckets": DATA / "lre_3_5_bucket_distribution.json",
    "candidates": DATA / "lre_3_5_current_candidates_review.json",
    "forward_ledger": DATA / "lre_3_5_forward_ledger_last.json",
    "report": ROOT / "docs/LRE_PHASE_3_5_DUAL_GATE_SHADOW_PILOT_REPORT.md",
}


def ensure_pilot_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_dual_gate_shadow_pilot (
        trade_date              TEXT NOT NULL,
        symbol                  TEXT NOT NULL,
        sector                  TEXT,
        lre_sub_stage           TEXT,
        lre_eps                 REAL,
        mde_score               REAL,
        dual_gate_score         REAL,
        clean_confluence        INTEGER DEFAULT 0,
        cap_status              TEXT,
        cap_reason              TEXT,
        pilot_eligible          INTEGER DEFAULT 0,
        pilot_bucket            TEXT,
        shadow_entry_date       TEXT,
        shadow_entry_price      REAL,
        planned_holding_days    INTEGER DEFAULT 20,
        stop_model              TEXT DEFAULT 'base_low',
        forward_return_5d       REAL,
        forward_return_10d      REAL,
        forward_return_20d      REAL,
        forward_return_30d      REAL,
        mfe_20d                 REAL,
        mae_20d                 REAL,
        exit_status             TEXT,
        exit_reason             TEXT,
        client_path_allowed     INTEGER DEFAULT 0,
        created_at              TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_pilot_bucket ON lre_dual_gate_shadow_pilot(pilot_bucket, trade_date);
    CREATE INDEX IF NOT EXISTS idx_lre_pilot_eligible ON lre_dual_gate_shadow_pilot(pilot_eligible, trade_date);
    """)


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


def attach_sector_to_trades(trades: List[dict], sectors: Dict[str, str]) -> List[dict]:
    for t in trades:
        t["sector"] = sectors.get(t["symbol"], "Unknown")
        t["trade_date"] = t.get("trade_date") or t.get("signal_date")
    return trades


def build_oos_confluence_trades(audit_rows: List[dict], bars_by_sym: dict, sectors: dict) -> List[dict]:
    oos_rows = [r for r in audit_rows if in_window(r["trade_date"], (OOS_START, "2099-12-31"))]
    trades = []
    for r in oos_rows:
        t = trade_from_audit(r, bars_by_sym, timing="same_day", hold_days=20)
        if t:
            t["trade_date"] = r["trade_date"]
            t["lre_sub_stage"] = r.get("lre_sub_stage")
            t["lre_eps"] = r.get("lre_eps")
            t["mde_score"] = r.get("mde_score")
            t["mde_gate_passed"] = r.get("mde_gate_passed")
            t["dual_gate_score"] = r.get("dual_gate_score")
            t["dual_gate_type"] = r.get("dual_gate_type")
            t["artifact_flag"] = r.get("artifact_flag")
            t["liquidity_flag"] = r.get("liquidity_flag")
            t["already_exploded_flag"] = r.get("already_exploded_flag")
            t["lre_risk_flags"] = r.get("lre_risk_flags")
            t["mde_reason_codes"] = r.get("mde_reason_codes")
            trades.append(t)
    dates = sorted({t["signal_date"] for t in trades})
    trades = dedup_trades(trades, dates, DEDUP_COOLDOWN)
    return attach_sector_to_trades(trades, sectors)


def hist_trades_by_symbol(all_trades: List[dict]) -> Dict[str, int]:
    return dict(Counter(t["symbol"] for t in all_trades))


def metrics_extended(trades: List[dict], cost_bps: int = 100) -> dict:
    m = confluence_metrics(trades, cost_bps)
    if not trades or not m.get("sample_ok"):
        return m
    m["net_PF_200bps"] = confluence_metrics(trades, 200).get("net_PF")
    m["sector_dominance"] = sector_dominance(trades)
    m["max_sector_dominance_pct"] = max_sector_dominance(trades)
    m["symbol_concentration_top5"] = dict(list(symbol_concentration(trades).items())[:5])
    return m


def caps_replay_suite(oos_trades: List[dict], hist_by_sym: Dict[str, int], windows: dict) -> dict:
    out = {}
    for mode in CAP_MODES:
        accepted, _ = apply_caps_to_trades(oos_trades, mode, hist_by_sym)
        mode_metrics = {
            "full_oos": metrics_extended(accepted),
            "bootstrap": bootstrap_confluence(accepted, n_runs=1000),
        }
        for wk, wr in windows.items():
            if wk == "oos_full":
                continue
            wtr = [t for t in accepted if in_window(t["signal_date"], wr)]
            mode_metrics[wk] = metrics_extended(wtr)
        out[mode] = mode_metrics
    return out


def persist_pilot_ledger(conn, rows: List[dict], bars_by_sym: dict) -> int:
    sql = """
        INSERT OR REPLACE INTO lre_dual_gate_shadow_pilot
        (trade_date, symbol, sector, lre_sub_stage, lre_eps, mde_score, dual_gate_score,
         clean_confluence, cap_status, cap_reason, pilot_eligible, pilot_bucket,
         shadow_entry_date, shadow_entry_price, planned_holding_days, stop_model,
         forward_return_5d, forward_return_10d, forward_return_20d, forward_return_30d,
         mfe_20d, mae_20d, exit_status, exit_reason, client_path_allowed)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
    """
    batch = []
    for r in rows:
        if not r.get("pilot_eligible"):
            continue
        sym, td = r["symbol"], r.get("trade_date") or r.get("signal_date")
        bars = bars_by_sym.get(sym)
        fwd = {}
        entry_price = r.get("entry_price")
        if bars:
            idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
            if idx is not None:
                fwd = forward_metrics(bars, idx)
                if not entry_price:
                    entry_price = bars[idx].get("close")
        batch.append((
            td, sym, r.get("sector"), r.get("lre_sub_stage"), r.get("lre_eps"),
            r.get("mde_score"), r.get("dual_gate_score"),
            int(r.get("clean_confluence") or 0),
            r.get("cap_status"), r.get("cap_reason"),
            int(r.get("pilot_eligible") or 0), r.get("pilot_bucket"),
            td, entry_price, 20, "base_low",
            fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
            fwd.get("forward_return_20d"), fwd.get("forward_return_30d"),
            fwd.get("mfe_20d"), fwd.get("mae_20d"),
            r.get("exit_reason") or "open", r.get("exit_reason"),
        ))
    for i in range(0, len(batch), 200):
        conn.executemany(sql, batch[i:i + 200])
    conn.commit()
    return len(batch)


def update_forward_outcomes(conn, bars_by_sym: dict, as_of: str) -> int:
    """Refresh forward returns for open pilot entries."""
    rows = conn.execute(
        "SELECT trade_date, symbol FROM lre_dual_gate_shadow_pilot WHERE exit_status='open' OR exit_status IS NULL"
    ).fetchall()
    updated = 0
    for r in rows:
        sym, td = r["symbol"], r["trade_date"]
        bars = bars_by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
        if idx is None:
            continue
        fwd = forward_metrics(bars, idx)
        conn.execute(
            """
            UPDATE lre_dual_gate_shadow_pilot
            SET forward_return_5d=?, forward_return_10d=?, forward_return_20d=?,
                forward_return_30d=?, mfe_20d=?, mae_20d=?,
                exit_status=CASE WHEN ? IS NOT NULL THEN 'closed' ELSE exit_status END
            WHERE trade_date=? AND symbol=?
            """,
            (
                fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                fwd.get("forward_return_20d"), fwd.get("forward_return_30d"),
                fwd.get("mfe_20d"), fwd.get("mae_20d"),
                fwd.get("forward_return_20d"), td, sym,
            ),
        )
        updated += 1
    conn.commit()
    return updated


def running_exposure_from_trades(trades: List[dict]) -> Tuple[Dict[str, int], Dict[str, int]]:
    sym = Counter(t["symbol"] for t in trades)
    sec = Counter(t.get("sector") or "Unknown" for t in trades)
    return dict(sym), dict(sec)


def review_candidate(
    conn,
    sym: str,
    trade_date: str,
    bars_by_sym: dict,
    sectors: Dict[str, str],
    hist_by_sym: Dict[str, int],
    cap_mode: str = "symbol_sector_finance_cap_25",
    running_sym: Optional[Dict[str, int]] = None,
    running_sec: Optional[Dict[str, int]] = None,
) -> dict:
    fingerprints = load_fingerprints()
    thresholds = calibrate_a_thresholds(conn, bars_by_sym, fingerprints)
    bars = bars_by_sym.get(sym)
    if not bars:
        return {"symbol": sym, "error": "no_bars"}
    idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
    if idx is None:
        return {"symbol": sym, "error": "no_date", "trade_date": trade_date}

    row = enrich_signal(conn, sym, bars, idx, fingerprints, thresholds)
    if not row:
        return {"symbol": sym, "error": "enrich"}
    sub, _ = classify_substage(bars, idx, row)
    row["sub_stage"] = sub
    mde_rec = _mde_rec_for_symbol(conn, sym, trade_date, sectors)
    audit = audit_row_from_pair(sym, trade_date, row, mde_rec, bars_by_sym)
    audit["sector"] = sectors.get(sym, "Unknown")
    audit["symbol"] = sym

    clean, clean_reasons = clean_confluence(audit, row)
    t_stub = {
        "symbol": sym,
        "signal_date": trade_date,
        "trade_date": trade_date,
        "sector": audit["sector"],
        "lre_sub_stage": sub,
        "lre_eps": audit.get("lre_eps"),
        "mde_score": audit.get("mde_score"),
        "mde_gate_passed": audit.get("mde_gate_passed"),
        "dual_gate_score": audit.get("dual_gate_score"),
        "dual_gate_type": audit.get("dual_gate_type"),
        "artifact_flag": audit.get("artifact_flag"),
        "liquidity_flag": audit.get("liquidity_flag"),
        "already_exploded_flag": audit.get("already_exploded_flag"),
        "lre_risk_flags": audit.get("lre_risk_flags"),
        "mde_reason_codes": audit.get("mde_reason_codes"),
        "stop_prone_score": row.get("stop_prone_score"),
    }
    accepted, all_status = apply_caps_to_trades(
        [t_stub], cap_mode, hist_by_sym,
        running_sym=running_sym, running_sec=running_sec,
    )
    status = all_status[0] if all_status else {}

    return {
        "symbol": sym,
        "trade_date": trade_date,
        "audit": audit,
        "clean_confluence": clean,
        "clean_reasons": clean_reasons,
        "top_contributor_family_similarity": top_contributor_family_flag(sym),
        "historical_confluence_trades": hist_by_sym.get(sym, 0),
        "pilot_bucket": status.get("pilot_bucket"),
        "pilot_eligible": status.get("pilot_eligible"),
        "cap_status": status.get("cap_status"),
        "cap_reason": status.get("cap_reason"),
        "passes_symbol_cap": status.get("cap_status") != "rejected" or "symbol_cap" not in str(status.get("cap_reason")),
        "passes_sector_cap": "sector_cap" not in str(status.get("cap_reason")),
        "monitoring_only": True,
        "recommendation": None,
        "bucket_reason": _bucket_reason(status, hist_by_sym.get(sym, 0), sub),
    }


def _bucket_reason(status: dict, hist_n: int, sub: str) -> str:
    bucket = status.get("pilot_bucket")
    if bucket == "New_Pattern_Monitor":
        return f"clean confluence but hist_trades={hist_n}<1 — forward observation required"
    if bucket == "Controlled_4B_Monitor":
        return "4B + MDE pass — timing monitor, not core"
    if bucket == "Clean_Confluence_Core":
        return f"{sub} + MDE strong + caps ok"
    if bucket == "Rejected_Despite_Confluence":
        return status.get("cap_reason") or "ineligible"
    return bucket or "unknown"


def final_verdict(replay: dict) -> Tuple[str, str]:
    best_mode = "symbol_sector_finance_cap_25"
    combined = replay.get(best_mode, {}).get("full_oos", {})
    core = replay.get("clean_core_only", {}).get("full_oos", {})
    core4b = replay.get("core_plus_4b", {}).get("full_oos", {})
    raw = replay.get("raw", {}).get("full_oos", {})
    boot = replay.get(best_mode, {}).get("bootstrap", {})

    n = combined.get("trade_count") or 0
    if n < 25:
        return "FAIL_CAPS_CURVE_FIT", f"Combined caps sample too small: n={n}"

    pass_pilot = (
        (combined.get("net_PF") or 0) >= 1.3
        and (combined.get("net_PF_150bps") or 0) >= 1.2
        and (combined.get("median_return") or 0) > 0
        and (combined.get("top10_dominance_pct") or 100) < 35
        and (combined.get("max_sector_dominance_pct") or 100) <= 30
        and (boot.get("prob_PF_gt_1_3") or 0) >= 70
        and n >= 40
    )
    if pass_pilot:
        return "PASS_DUAL_GATE_SHADOW_PILOT_CAPPED", (
            f"Combined caps OOS: PF={combined.get('net_PF')} dom={combined.get('top10_dominance_pct')}% "
            f"median={combined.get('median_return')}%"
        )

    if (combined.get("top10_dominance_pct") or 100) >= 35 and (raw.get("top10_dominance_pct") or 0) >= 35:
        if (combined.get("net_PF") or 0) < 1.0:
            return "FAIL_CONCENTRATION_UNFIXED", "Caps reduced dominance but killed PF"
        return "RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD", (
            f"Caps improve concentration (dom {raw.get('top10_dominance_pct')}%→{combined.get('top10_dominance_pct')}%) "
            f"but need forward pilot — PF={combined.get('net_PF')}"
        )

    if (combined.get("median_return") or 0) < 0:
        return "RESEARCH_EDGE_MONITOR_ONLY", "Caps make median negative — stay monitoring"

    if (core.get("trade_count") or 0) < 15:
        return "FAIL_CAPS_CURVE_FIT", "Clean core sample too thin after caps"

    return "RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD", (
        f"Promising capped replay PF={combined.get('net_PF')} — forward observation before pilot graduation"
    )


def render_report(doc: dict) -> str:
    v = doc["verdict"]
    replay = doc["caps_replay"]
    lines = [
        "# LRE-3.5 — Capped Dual-Gate Shadow Pilot Design",
        "",
        f"**Generated:** {doc['at']}",
        f"**Verdict:** `{v['code']}` — {v['reason']}",
        "",
        "## A. Why 3.4 Was Promising But Not Pass",
        "",
        doc.get("why_34", ""),
        "",
        "## B. Pilot Eligibility Rules",
        "",
        "- dual_gate_type = LRE_MDE_CONFLUENCE",
        "- LRE sub-stage 3B / 4A / 4B (not 4X)",
        "- MDE gate passed or hidden repricing confirmed",
        "- clean_confluence: no artifact, liquidity ok, not exploded, not do-not-chase",
        "- Caps: symbol 10%, sector 25%, Finance 20–30%",
        "",
        "## C. Caps Replay",
        "",
        "| Mode | Trades | PF@100 | Median | Top-10 | Max Sector |",
        "|------|--------|--------|--------|--------|------------|",
    ]
    for mode in CAP_MODES:
        m = replay.get(mode, {}).get("full_oos", {})
        lines.append(
            f"| {mode} | {m.get('trade_count', '—')} | {m.get('net_PF', '—')} | "
            f"{m.get('median_return', '—')}% | {m.get('top10_dominance_pct', '—')}% | "
            f"{m.get('max_sector_dominance_pct', '—')}% |"
        )
    lines.extend(["", "## D. Concentration Results", ""])
    combined = replay.get("symbol_sector_finance_cap_25", {}).get("full_oos", {})
    raw = replay.get("raw", {}).get("full_oos", {})
    lines.append(
        f"- Raw top-10: {raw.get('top10_dominance_pct')}% → Combined caps: {combined.get('top10_dominance_pct')}%"
    )
    lines.append(f"- Finance dominance after caps: {combined.get('sector_dominance', {}).get('Finance', '—')}%")
    lines.extend(["", "## E. Bootstrap After Caps", ""])
    boot = replay.get("symbol_sector_finance_cap_25", {}).get("bootstrap", {})
    lines.append(f"- P(PF>1.3) = {boot.get('prob_PF_gt_1_3')}%")
    lines.append(f"- P(median>0) = {boot.get('prob_median_gt_0')}%")
    lines.append(f"- P(hit+5%>40) = {boot.get('prob_hit_5pct_gt_40')}%")
    lines.extend(["", "## F. Current Candidates", ""])
    for sym, c in doc.get("candidates", {}).items():
        lines.append(
            f"- **{sym}:** bucket={c.get('pilot_bucket')} eligible={c.get('pilot_eligible')} "
            f"clean={c.get('clean_confluence')} cap={c.get('cap_reason')}"
        )
    lines.extend(["", "## G. Final Decision", "", f"**{v['code']}** — {v['reason']}", "", "## Answers", ""])
    for q, a in doc.get("answers", {}).items():
        lines.append(f"1. **{q}** — {a}")
    lines.extend(["", "---", "*Dual-Gate Capped Shadow Pilot only — no production.*"])
    return "\n".join(lines)


def cmd_run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("LRE-3.5 shadow pilot design starting...", flush=True)

    conn = connect()
    ensure_tables(conn)
    ensure_pilot_table(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    audit_rows = load_confluence_audit_rows(conn)
    if not audit_rows:
        print("  WARNING: no confluence audit rows — run egx:lre:dual-gate first", flush=True)
        return {"success": False, "error": "no_audit_rows"}

    oos_trades = build_oos_confluence_trades(audit_rows, by_sym, sectors)
    hist_by_sym = hist_trades_by_symbol(oos_trades)
    latest = params.get("trade_date") or "2026-06-11"
    windows = period_windows(latest)

    print(f"  OOS trades: {len(oos_trades)}", flush=True)
    replay = caps_replay_suite(oos_trades, hist_by_sym, windows)

    best_mode = "symbol_sector_finance_cap_25"
    accepted, all_status = apply_caps_to_trades(oos_trades, best_mode, hist_by_sym)
    run_sym, run_sec = running_exposure_from_trades(accepted)
    n_ledger = persist_pilot_ledger(conn, all_status, by_sym)

    bucket_dist = dict(Counter(r.get("pilot_bucket") for r in all_status if r.get("pilot_eligible")))
    candidates = {
        "OLFI": review_candidate(
            conn, "OLFI", latest, by_sym, sectors, hist_by_sym, best_mode, run_sym, run_sec,
        ),
    }
    new_conf = [r for r in audit_rows if r["trade_date"] == latest and r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE"]
    for r in new_conf:
        sym = r["symbol"]
        if sym not in candidates:
            candidates[sym] = review_candidate(conn, sym, latest, by_sym, sectors, hist_by_sym, best_mode)

    verdict_code, verdict_reason = final_verdict(replay)
    raw_m = replay.get("raw", {}).get("full_oos", {})
    comb_m = replay.get(best_mode, {}).get("full_oos", {})
    core_m = replay.get("clean_core_only", {}).get("full_oos", {})
    core4b_m = replay.get("core_plus_4b", {}).get("full_oos", {})
    olfi = candidates.get("OLFI", {})

    answers = {
        "هل يمكن خفض concentration بدون قتل edge؟": (
            f"top-10 {raw_m.get('top10_dominance_pct')}%→{comb_m.get('top10_dominance_pct')}% | "
            f"PF {raw_m.get('net_PF')}→{comb_m.get('net_PF')} — "
            f"{'نعم جزئياً' if (comb_m.get('net_PF') or 0) >= 1.2 else 'لا'}"
        ),
        "هل caps تجعل confluence صالحاً كـ shadow pilot؟": (
            f"{verdict_code} — PF={comb_m.get('net_PF')} median={comb_m.get('median_return')}% dom={comb_m.get('top10_dominance_pct')}%"
        ),
        "هل Clean Core أفضل من Core + 4B؟": (
            f"core PF={core_m.get('net_PF')} n={core_m.get('trade_count')} | "
            f"core+4B PF={core4b_m.get('net_PF')} n={core4b_m.get('trade_count')}"
        ),
        "هل Finance concentration ما زال مشكلة؟": (
            f"Finance share after caps: {comb_m.get('sector_dominance', {}).get('Finance', '—')}% "
            f"(max sector {comb_m.get('max_sector_dominance_pct')}%)"
        ),
        "أين يقع OLFI؟": (
            f"bucket={olfi.get('pilot_bucket')} eligible={olfi.get('pilot_eligible')} — {olfi.get('bucket_reason')}"
        ),
        "forward capped shadow pilot أم monitoring-only؟": (
            f"{verdict_code} — shadow pilot ledger only, no client path"
        ),
    }

    doc = {
        "at": at,
        "phase": "LRE-3.5",
        "invariants": PHASE_INVARIANTS,
        "caps_replay": replay,
        "bucket_distribution": bucket_dist,
        "candidates": candidates,
        "ledger_rows": n_ledger,
        "verdict": {"code": verdict_code, "reason": verdict_reason},
        "answers": answers,
        "why_34": (
            "LRE-3.4: PF=1.86, bootstrap P(PF>1.3)=94.8%, survives 200bps — but top-10=36.1%, "
            "Finance-heavy, collapses on exclude top-10."
        ),
    }

    OUTPUTS["caps_replay"].write_text(json.dumps(replay, indent=2, default=str), encoding="utf-8")
    OUTPUTS["pilot_last"].write_text(json.dumps({
        "at": at, "verdict": doc["verdict"], "best_mode": best_mode,
        "combined_oos": comb_m, "accepted_count": len(accepted),
    }, indent=2, default=str), encoding="utf-8")
    OUTPUTS["buckets"].write_text(json.dumps(bucket_dist, indent=2), encoding="utf-8")
    OUTPUTS["candidates"].write_text(json.dumps(candidates, indent=2, default=str), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report(doc), encoding="utf-8")

    conn.close()
    print(f"  Verdict: {verdict_code}", flush=True)
    return {"success": True, "verdict": verdict_code, "oos_trades": len(oos_trades), "ledger": n_ledger}


def cmd_pilot(params: Optional[dict] = None) -> dict:
    """Daily forward shadow pilot — apply caps, write ledger, update outcomes."""
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    trade_date = params.get("trade_date") or params.get("date")

    conn = connect()
    ensure_tables(conn)
    ensure_pilot_table(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    audit_rows = load_confluence_audit_rows(conn)
    if not audit_rows:
        conn.close()
        return {"success": False, "error": "no_audit_rows"}

    if not trade_date:
        trade_date = max(r["trade_date"] for r in audit_rows)

    oos_trades = build_oos_confluence_trades(audit_rows, by_sym, sectors)
    hist_trades = [t for t in oos_trades if t["signal_date"] < trade_date]
    hist_accepted, _ = apply_caps_to_trades(
        hist_trades, "symbol_sector_finance_cap_25", hist_trades_by_symbol(hist_trades),
    )
    run_sym, run_sec = running_exposure_from_trades(hist_accepted)
    hist_by_sym = hist_trades_by_symbol(hist_trades)

    day_rows = [r for r in audit_rows if r["trade_date"] == trade_date]
    day_trades = []
    for r in day_rows:
        t = trade_from_audit(r, by_sym, timing="same_day", hold_days=20)
        if t and r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE":
            t.update({
                "trade_date": trade_date,
                "sector": sectors.get(r["symbol"], "Unknown"),
                "lre_sub_stage": r.get("lre_sub_stage"),
                "lre_eps": r.get("lre_eps"),
                "mde_score": r.get("mde_score"),
                "mde_gate_passed": r.get("mde_gate_passed"),
                "dual_gate_score": r.get("dual_gate_score"),
                "dual_gate_type": r.get("dual_gate_type"),
                "artifact_flag": r.get("artifact_flag"),
                "liquidity_flag": r.get("liquidity_flag"),
                "already_exploded_flag": r.get("already_exploded_flag"),
                "lre_risk_flags": r.get("lre_risk_flags"),
                "mde_reason_codes": r.get("mde_reason_codes"),
            })
            day_trades.append(t)

    accepted, all_status = apply_caps_to_trades(
        day_trades, "symbol_sector_finance_cap_25", hist_by_sym,
        running_sym=run_sym, running_sec=run_sec,
    )
    n_new = persist_pilot_ledger(conn, all_status, by_sym)
    n_updated = update_forward_outcomes(conn, by_sym, trade_date)

    ledger = [dict(r) for r in conn.execute(
        "SELECT * FROM lre_dual_gate_shadow_pilot ORDER BY trade_date DESC LIMIT 50"
    ).fetchall()]

    payload = {
        "at": at,
        "trade_date": trade_date,
        "new_entries": n_new,
        "outcomes_updated": n_updated,
        "day_candidates": len(day_trades),
        "accepted_today": len(accepted),
        "entries": accepted,
        "invariants": PHASE_INVARIANTS,
    }
    OUTPUTS["forward_ledger"].write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    print(f"  Pilot daily: {trade_date} new={n_new} updated={n_updated}", flush=True)
    return {"success": True, **payload}


COMMANDS = {"run": cmd_run, "pilot": cmd_pilot, "daily": cmd_pilot}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    p: dict = {}
    if len(sys.argv) > 2:
        try:
            p = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            p = {}
    fn = COMMANDS.get(cmd, cmd_run)
    print(json.dumps(fn(p), indent=2))
