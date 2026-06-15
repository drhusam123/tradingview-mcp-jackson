#!/usr/bin/env python3
"""LRE-3.5 shadow pilot eligibility, caps, and bucket assignment."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set, Tuple

from lre_mde_dual_gate import lre_monitoring_valid, lre_rejected

TOP_CONTRIBUTOR_FAMILY = frozenset({"ORAS", "HELI", "ORHD", "HDBK", "TMGH"})
CORE_SUBSTAGES = frozenset({"3B", "4A"})
CONTROLLED_4B = "4B"
MIN_HIST_TRADES_FOR_CORE = 1

PILOT_BUCKETS = (
    "Clean_Confluence_Core",
    "Controlled_4B_Monitor",
    "New_Pattern_Monitor",
    "Rejected_Despite_Confluence",
)

CAP_MODES = (
    "raw",
    "symbol_cap_only",
    "sector_cap_only",
    "finance_cap_20",
    "finance_cap_25",
    "finance_cap_30",
    "symbol_sector_cap",
    "symbol_sector_finance_cap_25",
    "clean_core_only",
    "core_plus_4b",
)


def top_contributor_family_flag(symbol: str) -> bool:
    return symbol in TOP_CONTRIBUTOR_FAMILY


def clean_confluence(row: dict, lre_row: Optional[dict] = None) -> Tuple[bool, List[str]]:
    """Eligible clean confluence per LRE-3.5 rules."""
    reasons: List[str] = []
    if row.get("dual_gate_type") != "LRE_MDE_CONFLUENCE":
        reasons.append("not_confluence")
        return False, reasons
    sub = row.get("lre_sub_stage")
    if sub == "4X":
        reasons.append("4X")
        return False, reasons
    if sub not in ("3B", "4A", "4B"):
        reasons.append(f"sub_stage={sub}")
        return False, reasons
    if not int(row.get("mde_gate_passed") or 0):
        hidden = "hidden_repricing" in (row.get("mde_reason_codes") or [])
        if not hidden:
            reasons.append("mde_not_confirmed")
            return False, reasons
    if int(row.get("artifact_flag") or 0):
        reasons.append("artifact")
    if int(row.get("liquidity_flag") or 0):
        reasons.append("low_liquidity")
    if int(row.get("already_exploded_flag") or 0):
        reasons.append("already_exploded")
    if lre_row:
        rejected, rej = lre_rejected(lre_row)
        if rejected:
            reasons.extend(rej)
    else:
        risk = row.get("lre_risk_flags") or []
        if risk:
            reasons.extend(risk if isinstance(risk, list) else [str(risk)])
    if reasons:
        return False, reasons
    return True, []


def assign_bucket(
    row: dict,
    pilot_eligible: bool,
    cap_status: str,
    hist_trades_by_symbol: Dict[str, int],
) -> str:
    if not pilot_eligible or cap_status == "rejected":
        return "Rejected_Despite_Confluence"
    sub = row.get("lre_sub_stage")
    sym = row.get("symbol")
    hist_n = hist_trades_by_symbol.get(sym, 0)
    stop_high = float(row.get("stop_prone_score") or row.get("lre_stop_prone") or 0) >= 45
    exploded = int(row.get("already_exploded_flag") or 0)
    mde_strong = int(row.get("mde_gate_passed") or 0) and float(row.get("mde_score") or 0) >= 60

    if sub in CORE_SUBSTAGES and mde_strong and not exploded and not stop_high and hist_n >= MIN_HIST_TRADES_FOR_CORE:
        return "Clean_Confluence_Core"
    if sub == CONTROLLED_4B and mde_strong:
        return "Controlled_4B_Monitor"
    if hist_n < MIN_HIST_TRADES_FOR_CORE:
        return "New_Pattern_Monitor"
    if sub in CORE_SUBSTAGES:
        return "Clean_Confluence_Core"
    return "Controlled_4B_Monitor"


def _would_exceed_caps(
    sym: str,
    sec: str,
    sym_counts: Dict[str, int],
    sec_counts: Dict[str, int],
    total_after: int,
    symbol_cap: Optional[float],
    sector_cap: Optional[float],
    finance_cap: Optional[float],
) -> Tuple[bool, Optional[str]]:
    if total_after <= 0:
        return False, None
    if symbol_cap is not None:
        sym_pct = (sym_counts.get(sym, 0) + 1) / total_after
        if sym_pct > symbol_cap:
            return True, f"symbol_cap>{int(symbol_cap * 100)}%"
    if sector_cap is not None:
        sec_pct = (sec_counts.get(sec, 0) + 1) / total_after
        if sec_pct > sector_cap:
            return True, f"sector_cap>{int(sector_cap * 100)}%"
    if finance_cap is not None and sec == "Finance":
        fin_pct = (sec_counts.get("Finance", 0) + 1) / total_after
        if fin_pct > finance_cap:
            return True, f"finance_cap>{int(finance_cap * 100)}%"
    return False, None


def cap_config_for_mode(mode: str) -> dict:
    cfg = {
        "symbol_cap": None,
        "sector_cap": None,
        "finance_cap": None,
        "hard_cap": True,
        "buckets": None,
    }
    if mode == "raw":
        return cfg
    if mode == "symbol_cap_only":
        cfg["symbol_cap"] = 0.10
    elif mode == "sector_cap_only":
        cfg["sector_cap"] = 0.25
    elif mode == "finance_cap_20":
        cfg["finance_cap"] = 0.20
    elif mode == "finance_cap_25":
        cfg["finance_cap"] = 0.25
    elif mode == "finance_cap_30":
        cfg["finance_cap"] = 0.30
    elif mode == "symbol_sector_cap":
        cfg["symbol_cap"] = 0.10
        cfg["sector_cap"] = 0.25
    elif mode == "symbol_sector_finance_cap_25":
        cfg["symbol_cap"] = 0.10
        cfg["sector_cap"] = 0.25
        cfg["finance_cap"] = 0.25
    elif mode == "clean_core_only":
        cfg["symbol_cap"] = 0.10
        cfg["sector_cap"] = 0.25
        cfg["finance_cap"] = 0.25
        cfg["buckets"] = frozenset({"Clean_Confluence_Core"})
    elif mode == "core_plus_4b":
        cfg["symbol_cap"] = 0.10
        cfg["sector_cap"] = 0.25
        cfg["finance_cap"] = 0.25
        cfg["buckets"] = frozenset({"Clean_Confluence_Core", "Controlled_4B_Monitor"})
    return cfg


def _trim_to_caps(
    trades: List[dict],
    symbol_cap: Optional[float],
    sector_cap: Optional[float],
    finance_cap: Optional[float],
) -> List[dict]:
    """Remove lowest-score trades until symbol/sector/finance caps satisfied."""
    selected = list(trades)
    if not selected:
        return selected

    def overweight() -> Optional[Tuple[str, str]]:
        total = len(selected)
        sym_c = Counter(t["symbol"] for t in selected)
        sec_c = Counter(t.get("sector") or "Unknown" for t in selected)
        worst = None
        worst_ratio = 0.0
        if symbol_cap is not None:
            for sym, c in sym_c.items():
                ratio = c / total
                if ratio > symbol_cap and ratio > worst_ratio:
                    worst_ratio = ratio
                    worst = ("symbol", sym)
        if sector_cap is not None:
            for sec, c in sec_c.items():
                ratio = c / total
                if ratio > sector_cap and ratio >= worst_ratio:
                    worst_ratio = ratio
                    worst = ("sector", sec)
        if finance_cap is not None:
            fin = sec_c.get("Finance", 0)
            ratio = fin / total
            if ratio > finance_cap and ratio >= worst_ratio:
                worst = ("finance", "Finance")
        return worst

    while True:
        if not selected:
            break
        ov = overweight()
        if not ov:
            break
        kind, key = ov
        pool = [
            t for t in selected
            if (kind == "symbol" and t["symbol"] == key)
            or (kind in ("sector", "finance") and (t.get("sector") or "Unknown") == key)
        ]
        if not pool:
            break
        drop = min(pool, key=lambda t: float(t.get("dual_gate_score") or 0))
        selected.remove(drop)
    return selected


def apply_caps_to_trades(
    trades: List[dict],
    mode: str = "symbol_sector_finance_cap_25",
    hist_trades_by_symbol: Optional[Dict[str, int]] = None,
    running_sym: Optional[Dict[str, int]] = None,
    running_sec: Optional[Dict[str, int]] = None,
) -> Tuple[List[dict], List[dict]]:
    """
    Apply eligibility + caps. Replay uses retrospective trim; daily uses running totals.
    """
    cfg = cap_config_for_mode(mode)
    hist = hist_trades_by_symbol or {}
    sym_run = dict(running_sym or {})
    sec_run = dict(running_sec or {})
    clean_pool: List[dict] = []
    all_rows: List[dict] = []

    for t in sorted(trades, key=lambda x: x.get("signal_date") or x.get("trade_date", "")):
        row = dict(t)
        sym = row["symbol"]
        sec = row.get("sector") or "Unknown"
        row.setdefault("dual_gate_type", "LRE_MDE_CONFLUENCE")
        clean, clean_reasons = clean_confluence(row)
        row["clean_confluence"] = clean
        row["top_contributor_family_similarity"] = top_contributor_family_flag(sym)

        if not clean:
            row["cap_status"] = "rejected"
            row["cap_reason"] = ",".join(clean_reasons) or "not_clean"
            row["pilot_eligible"] = False
            row["pilot_bucket"] = "Rejected_Despite_Confluence"
            all_rows.append(row)
            continue

        row["cap_status"] = "ok"
        row["cap_reason"] = None
        row["pilot_eligible"] = True
        row["pilot_bucket"] = assign_bucket(row, True, "ok", hist)
        if cfg["buckets"] and row["pilot_bucket"] not in cfg["buckets"]:
            row["pilot_eligible"] = False
            row["cap_status"] = "rejected"
            row["cap_reason"] = f"bucket_filter:{row['pilot_bucket']}"
            row["pilot_bucket"] = "Rejected_Despite_Confluence"
            all_rows.append(row)
            continue
        clean_pool.append(row)
        all_rows.append(row)

    if not clean_pool:
        return [], all_rows

    has_caps = any(cfg[k] is not None for k in ("symbol_cap", "sector_cap", "finance_cap"))
    if mode == "raw" or not has_caps:
        accepted = clean_pool
    elif running_sym is not None:
        accepted = []
        for row in clean_pool:
            sym = row["symbol"]
            sec = row.get("sector") or "Unknown"
            total_after = sum(sym_run.values()) + sum(sec_run.values()) // max(len(sec_run), 1) + 1
            total_after = max(sum(sym_run.values()), 0) + 1
            exceed, reason = _would_exceed_caps(
                sym, sec, sym_run, sec_run, total_after,
                cfg["symbol_cap"], cfg["sector_cap"], cfg["finance_cap"],
            )
            if exceed and cfg["hard_cap"]:
                row["cap_status"] = "rejected"
                row["cap_reason"] = reason
                row["pilot_eligible"] = False
                row["pilot_bucket"] = "Rejected_Despite_Confluence"
            else:
                accepted.append(row)
                sym_run[sym] = sym_run.get(sym, 0) + 1
                sec_run[sec] = sec_run.get(sec, 0) + 1
    else:
        accepted = _trim_to_caps(
            clean_pool, cfg["symbol_cap"], cfg["sector_cap"], cfg["finance_cap"],
        )
        accepted_keys = {
            (t["symbol"], t.get("signal_date") or t.get("trade_date")) for t in accepted
        }
        for row in all_rows:
            key = (row["symbol"], row.get("signal_date") or row.get("trade_date"))
            if row.get("clean_confluence") and key not in accepted_keys:
                row["cap_status"] = "rejected"
                row["cap_reason"] = "cap_trim"
                row["pilot_eligible"] = False
                row["pilot_bucket"] = "Rejected_Despite_Confluence"

    return accepted, all_rows


def sector_dominance(trades: List[dict]) -> Dict[str, float]:
    if not trades:
        return {}
    sec_counts = Counter(t.get("sector") or "Unknown" for t in trades)
    total = len(trades)
    return {s: round(100 * c / total, 1) for s, c in sec_counts.most_common()}


def symbol_concentration(trades: List[dict]) -> Dict[str, float]:
    if not trades:
        return {}
    sym_counts = Counter(t["symbol"] for t in trades)
    total = len(trades)
    return {s: round(100 * c / total, 1) for s, c in sym_counts.most_common()}


def max_sector_dominance(trades: List[dict]) -> float:
    dom = sector_dominance(trades)
    return max(dom.values()) if dom else 0.0
