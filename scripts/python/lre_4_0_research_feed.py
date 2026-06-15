#!/usr/bin/env python3
"""
LRE Phase 4.0 — Unified Research Feed Source.

Consolidates LRE-2.0 daily radar through LRE-3.6A confluence into one research
feeding layer for fabric (L11), opportunity_score_v2 (L7), and prioritizer.

Shadow / additive only — never writes final_signals.actionable or client path.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
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
from lre_3_1_filters import calibrate_a_thresholds, load_fingerprints  # noqa: E402
from lre_3_4_confluence_robustness import load_sectors  # noqa: E402
from lre_3_5_pilot_caps import apply_caps_to_trades, assign_bucket  # noqa: E402
from lre_3_6a_causal import (  # noqa: E402
    build_causal_signal,
    calibrate_thresholds_causal,
    load_explosion_events,
    load_mde_by_date,
    mde_watch_row,
)
from lre_3_6a_walk_forward_pilot import _static_thresholds  # noqa: E402
from lre_signal_provider import build_daily_signals, publish as publish_shadow  # noqa: E402

PHASE = "LRE-4.0"
PROVIDER_ID = "LRE_4_0_RESEARCH_FEED"

PHASE_INVARIANTS = {
    **LRE_INVARIANTS,
    "phase": PHASE,
    "FEED_SYSTEM": 1,
    "CLIENT_SIGNAL": 0,
    "EGX_LRE_SHADOW": "1",
    "EGX_LRE_OPP_BOOST": "0",
    "client_path_allowed": False,
    "additive_only": True,
    "no_veto": True,
    "no_suppression": True,
    "no_actionable_change": True,
}

OUTPUTS = {
    "feed_last": DATA / "lre_research_feed_last.json",
    "manifest": DATA / "discovery_lre_manifest.json",
    "learning": DATA / "lre_learning_snapshot.json",
}

FEED_TIER_BOOST = {
    "LRE_CLEAN_CORE": 3.0,
    "LRE_CONFLUENCE_CAPPED": 2.5,
    "LRE_4B_MONITOR": 2.0,
    "LRE_CONFLUENCE": 2.0,
    "LRE_GATE": 1.0,
    "LRE_MONITOR": 0.0,
}


def ensure_feed_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_research_feed_daily (
        signal_date           TEXT NOT NULL,
        symbol                TEXT NOT NULL,
        sector                TEXT,
        provider_id           TEXT NOT NULL DEFAULT 'LRE_4_0_RESEARCH_FEED',
        feed_tier             TEXT,
        opp_boost_points      REAL DEFAULT 0,
        lre_stage             INTEGER,
        lre_sub_stage         TEXT,
        lre_eps               REAL,
        primary_list          TEXT,
        dual_gate_type        TEXT,
        mde_score             REAL,
        dual_gate_score       REAL,
        pilot_bucket          TEXT,
        pilot_eligible        INTEGER DEFAULT 0,
        cap_status            TEXT,
        fabric_atoms_json     TEXT,
        detail_json           TEXT,
        client_path_allowed   INTEGER DEFAULT 0,
        created_at            TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (signal_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_feed_tier ON lre_research_feed_daily(signal_date, feed_tier);
    CREATE INDEX IF NOT EXISTS idx_lre_feed_eligible ON lre_research_feed_daily(pilot_eligible, signal_date);
    """)


def _latest_date(conn, table: str, col: str = "trade_date") -> Optional[str]:
    if not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT MAX({col}) AS d FROM {table}").fetchone()
    return row["d"] if row and row["d"] else None


def _load_audit_confluence(conn, trade_date: str) -> List[dict]:
    if not table_exists(conn, "lre_mde_dual_gate_audit"):
        return []
    rows = conn.execute(
        """
        SELECT * FROM lre_mde_dual_gate_audit
        WHERE trade_date=? AND dual_gate_type='LRE_MDE_CONFLUENCE'
        """,
        (trade_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_causal_confluence(
    conn,
    trade_date: str,
    by_sym: dict,
    sectors: Dict[str, str],
    fingerprints: dict,
    events: List[dict],
    static_th: dict,
    mde_by_date: Dict[str, List[dict]],
) -> List[dict]:
    th = calibrate_thresholds_causal(events, by_sym, fingerprints, trade_date, None)
    out: List[dict] = []
    for mde_row in mde_by_date.get(trade_date, []):
        if not mde_watch_row(mde_row):
            continue
        sym = mde_row["symbol"]
        bars = by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
        if idx is None:
            continue
        sig = build_causal_signal(
            conn, sym, trade_date, bars, idx, fingerprints, th, mde_row,
            sectors.get(sym, "Unknown"),
        )
        if sig and sig.get("dual_gate_type") == "LRE_MDE_CONFLUENCE":
            sig["trade_date"] = trade_date
            sig["sector"] = sectors.get(sym, "Unknown")
            out.append(sig)
    return out


def _hist_pilot_context(conn, trade_date: str) -> Tuple[Dict[str, int], List[dict]]:
    hist_by_sym: Dict[str, int] = defaultdict(int)
    hist_trades: List[dict] = []
    if table_exists(conn, "lre_dual_gate_shadow_pilot"):
        for r in conn.execute(
            """
            SELECT symbol, trade_date, sector, lre_sub_stage, lre_eps, mde_score,
                   dual_gate_score, clean_confluence, pilot_eligible, pilot_bucket
            FROM lre_dual_gate_shadow_pilot
            WHERE trade_date < ? AND pilot_eligible=1
            ORDER BY trade_date
            """,
            (trade_date,),
        ).fetchall():
            d = dict(r)
            d["dual_gate_type"] = "LRE_MDE_CONFLUENCE"
            d["signal_date"] = d["trade_date"]
            hist_trades.append(d)
            hist_by_sym[d["symbol"]] += 1
    if not hist_trades and table_exists(conn, "lre_mde_dual_gate_audit"):
        for r in conn.execute(
            """
            SELECT symbol, trade_date, sector, lre_sub_stage, lre_eps, mde_score,
                   dual_gate_score, dual_gate_type
            FROM lre_mde_dual_gate_audit
            WHERE trade_date < ? AND dual_gate_type='LRE_MDE_CONFLUENCE'
            ORDER BY trade_date
            """,
            (trade_date,),
        ).fetchall():
            d = dict(r)
            d["signal_date"] = d["trade_date"]
            hist_trades.append(d)
            hist_by_sym[d["symbol"]] += 1
    return hist_by_sym, hist_trades


def _classify_day_confluence(
    conn,
    trade_date: str,
    confluence_rows: List[dict],
    sectors: Dict[str, str],
) -> Dict[str, dict]:
    if not confluence_rows:
        return {}
    hist_by_sym, hist_trades = _hist_pilot_context(conn, trade_date)
    day_trades = []
    for r in confluence_rows:
        sym = r["symbol"]
        day_trades.append({
            **r,
            "symbol": sym,
            "trade_date": trade_date,
            "signal_date": trade_date,
            "sector": r.get("sector") or sectors.get(sym, "Unknown"),
            "dual_gate_type": "LRE_MDE_CONFLUENCE",
        })
    combined = hist_trades + day_trades
    accepted, all_rows = apply_caps_to_trades(
        combined, "symbol_sector_finance_cap_25", hist_trades_by_symbol=hist_by_sym,
    )
    accepted_keys = {
        (t["symbol"], t.get("trade_date") or t.get("signal_date")) for t in accepted
    }
    out: Dict[str, dict] = {}
    for row in all_rows:
        td = row.get("trade_date") or row.get("signal_date")
        if td != trade_date:
            continue
        sym = row["symbol"]
        key = (sym, td)
        row["pilot_eligible"] = key in accepted_keys
        if row.get("pilot_eligible"):
            row["pilot_bucket"] = assign_bucket(row, True, "ok", hist_by_sym)
        out[sym] = row
    return out


def _tier_for_row(score_row: dict, conf: Optional[dict]) -> Tuple[str, float, List[str]]:
    atoms: List[str] = []
    if conf:
        bucket = conf.get("pilot_bucket") or ""
        eligible = bool(conf.get("pilot_eligible"))
        if eligible and bucket == "Clean_Confluence_Core":
            atoms.extend(["lre_confluence_clean_core", "lre_mde_confluence_capped"])
            return "LRE_CLEAN_CORE", FEED_TIER_BOOST["LRE_CLEAN_CORE"], atoms
        if eligible and bucket == "Controlled_4B_Monitor":
            atoms.extend(["lre_mde_confluence_capped", "lre_4b_monitor"])
            return "LRE_4B_MONITOR", FEED_TIER_BOOST["LRE_4B_MONITOR"], atoms
        if eligible:
            atoms.append("lre_mde_confluence_capped")
            return "LRE_CONFLUENCE_CAPPED", FEED_TIER_BOOST["LRE_CONFLUENCE_CAPPED"], atoms
        atoms.append("lre_mde_confluence")
        return "LRE_CONFLUENCE", FEED_TIER_BOOST["LRE_CONFLUENCE"], atoms

    stage = int(score_row.get("stage") or 0)
    eps = float(score_row.get("explosion_potential") or 0)
    tags = score_row.get("list_tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    primary = score_row.get("primary_list") or ""

    if stage in (3, 4) and eps >= 50 and int(score_row.get("artifact_risk") or 0) == 0:
        if primary == "ignition_candidates":
            atoms.append("lre_ignition_candidates")
        if int(score_row.get("rotation_trigger") or 0):
            atoms.append("lre_next_rotation")
        if "silent_accumulation" in tags:
            atoms.append("lre_silent_accumulation")
        if "volume_awakening" in tags:
            atoms.append("lre_volume_awakening")
        atoms.append("lre_gate_candidates")
        return "LRE_GATE", FEED_TIER_BOOST["LRE_GATE"], atoms

    if eps >= 35:
        return "LRE_MONITOR", FEED_TIER_BOOST["LRE_MONITOR"], atoms
    return "LRE_MONITOR", 0.0, atoms


def build_research_feed(
    conn,
    trade_date: str,
    by_sym: dict,
    sectors: Dict[str, str],
) -> List[dict]:
    fingerprints = load_fingerprints()
    events = load_explosion_events(conn)
    static_th = _static_thresholds(conn, by_sym, fingerprints)
    mde_by_date = load_mde_by_date(conn, "2022-04-05")

    audit_conf = _load_audit_confluence(conn, trade_date)
    if audit_conf:
        confluence_map = _classify_day_confluence(conn, trade_date, audit_conf, sectors)
    else:
        causal = _build_causal_confluence(
            conn, trade_date, by_sym, sectors, fingerprints, events, static_th, mde_by_date,
        )
        confluence_map = _classify_day_confluence(conn, trade_date, causal, sectors)

    score_rows = conn.execute(
        "SELECT * FROM lre_daily_scores WHERE trade_date=? ORDER BY explosion_potential DESC",
        (trade_date,),
    ).fetchall()
    if not score_rows and not confluence_map:
        return []

    feed: List[dict] = []
    seen = set()
    for r in score_rows:
        sym = r["symbol"]
        rd = dict(r)
        seen.add(sym)
        conf = confluence_map.get(sym)
        tags = json.loads(r["list_tags"] or "[]")
        score_row = {
            **rd,
            "list_tags": tags,
            "primary_list": next(
                (t for t in (
                    "ignition_candidates", "next_rotation", "silent_accumulation",
                    "volume_awakening",
                ) if t in tags),
                None,
            ),
        }
        tier, boost, atoms = _tier_for_row(score_row, conf)
        if conf and conf.get("pilot_bucket") == "Clean_Confluence_Core" and conf.get("pilot_eligible"):
            tier, boost = "LRE_CLEAN_CORE", FEED_TIER_BOOST["LRE_CLEAN_CORE"]

        feed.append({
            "signal_date": trade_date,
            "symbol": sym,
            "sector": sectors.get(sym, "Unknown"),
            "provider_id": PROVIDER_ID,
            "feed_tier": tier,
            "opp_boost_points": boost,
            "lre_stage": rd["stage"],
            "lre_sub_stage": (conf or {}).get("lre_sub_stage") or rd.get("sub_stage"),
            "lre_eps": float(rd["explosion_potential"] or 0),
            "primary_list": score_row.get("primary_list"),
            "dual_gate_type": (conf or {}).get("dual_gate_type"),
            "mde_score": (conf or {}).get("mde_score"),
            "dual_gate_score": (conf or {}).get("dual_gate_score"),
            "pilot_bucket": (conf or {}).get("pilot_bucket"),
            "pilot_eligible": int(bool((conf or {}).get("pilot_eligible"))),
            "cap_status": (conf or {}).get("cap_status"),
            "fabric_atoms_json": json.dumps(sorted(set(atoms))),
            "detail_json": {
                "phase": PHASE,
                "confluence_source": "audit" if audit_conf else "causal",
                "list_tags": tags,
                "rotation_leader": rd["rotation_leader"],
                "compression_days": rd["compression_days"],
                "vol_ratio_20": rd["vol_ratio_20"],
            },
            "client_path_allowed": 0,
        })

    for sym, conf in confluence_map.items():
        if sym in seen:
            continue
        tier, boost, atoms = _tier_for_row({}, conf)
        feed.append({
            "signal_date": trade_date,
            "symbol": sym,
            "sector": conf.get("sector") or sectors.get(sym, "Unknown"),
            "provider_id": PROVIDER_ID,
            "feed_tier": tier,
            "opp_boost_points": boost,
            "lre_stage": conf.get("lre_stage"),
            "lre_sub_stage": conf.get("lre_sub_stage"),
            "lre_eps": conf.get("lre_eps"),
            "primary_list": None,
            "dual_gate_type": conf.get("dual_gate_type"),
            "mde_score": conf.get("mde_score"),
            "dual_gate_score": conf.get("dual_gate_score"),
            "pilot_bucket": conf.get("pilot_bucket"),
            "pilot_eligible": int(bool(conf.get("pilot_eligible"))),
            "cap_status": conf.get("cap_status"),
            "fabric_atoms_json": json.dumps(sorted(set(atoms))),
            "detail_json": {"phase": PHASE, "confluence_only": True},
            "client_path_allowed": 0,
        })
    return feed


def publish_feed(conn, trade_date: str, rows: List[dict]) -> int:
    conn.execute("DELETE FROM lre_research_feed_daily WHERE signal_date=?", (trade_date,))
    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO lre_research_feed_daily
            (signal_date, symbol, sector, provider_id, feed_tier, opp_boost_points,
             lre_stage, lre_sub_stage, lre_eps, primary_list, dual_gate_type,
             mde_score, dual_gate_score, pilot_bucket, pilot_eligible, cap_status,
             fabric_atoms_json, detail_json, client_path_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r["signal_date"], r["symbol"], r.get("sector"), r["provider_id"],
                r.get("feed_tier"), r.get("opp_boost_points"),
                r.get("lre_stage"), r.get("lre_sub_stage"), r.get("lre_eps"),
                r.get("primary_list"), r.get("dual_gate_type"),
                r.get("mde_score"), r.get("dual_gate_score"),
                r.get("pilot_bucket"), r.get("pilot_eligible", 0),
                r.get("cap_status"), r.get("fabric_atoms_json"),
                json.dumps(r.get("detail_json") or {}, default=str),
                0,
            ),
        )
    conn.commit()
    return len(rows)


def write_manifest(trade_date: str, rows: List[dict]) -> dict:
    tier_counts: Dict[str, int] = defaultdict(int)
    atom_counts: Dict[str, int] = defaultdict(int)
    for r in rows:
        tier_counts[r.get("feed_tier") or "unknown"] += 1
        try:
            for a in json.loads(r.get("fabric_atoms_json") or "[]"):
                atom_counts[a] += 1
        except json.JSONDecodeError:
            pass
    manifest = {
        "at": datetime.now(timezone.utc).isoformat(),
        "phase": PHASE,
        "trade_date": trade_date,
        "provider_id": PROVIDER_ID,
        "invariants": PHASE_INVARIANTS,
        "symbol_count": len(rows),
        "tier_counts": dict(tier_counts),
        "atom_counts": dict(atom_counts),
        "pilot_eligible_count": sum(int(r.get("pilot_eligible") or 0) for r in rows),
        "confluence_count": sum(
            1 for r in rows if (r.get("dual_gate_type") == "LRE_MDE_CONFLUENCE")
        ),
        "max_opp_boost": max((float(r.get("opp_boost_points") or 0) for r in rows), default=0),
        "feed_targets": ["discovery_fabric", "opportunity_score_v2", "intelligence_prioritizer"],
        "client_path_allowed": False,
    }
    OUTPUTS["manifest"].write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def write_learning_snapshot(trade_date: str, rows: List[dict]) -> dict:
    snap = {
        "at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "top_confluence": [
            {
                "symbol": r["symbol"],
                "tier": r.get("feed_tier"),
                "bucket": r.get("pilot_bucket"),
                "eps": r.get("lre_eps"),
                "dual_gate_score": r.get("dual_gate_score"),
            }
            for r in sorted(
                rows,
                key=lambda x: float(x.get("dual_gate_score") or x.get("lre_eps") or 0),
                reverse=True,
            )[:15]
        ],
        "walk_forward_verdict": "RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED",
        "notes": "LRE-3.6A validated edge; feed integrates confluence + caps into system layers",
    }
    OUTPUTS["learning"].write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return snap


def run(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    conn = connect()
    ensure_tables(conn)
    ensure_feed_table(conn)
    sectors = load_sectors(conn)
    by_sym, _ = load_all_bars(conn)

    trade_date = params.get("trade_date") or params.get("date")
    if not trade_date:
        trade_date = _latest_date(conn, "lre_daily_scores") or _latest_date(
            conn, "lre_research_feed_daily", "signal_date"
        )
    if not trade_date:
        conn.close()
        return {"success": False, "error": "no_trade_date"}

    shadow_n = 0
    if params.get("refresh_shadow", True):
        signals = build_daily_signals(conn, trade_date)
        shadow_n = publish_shadow(conn, trade_date, signals)

    feed_rows = build_research_feed(conn, trade_date, by_sym, sectors)
    n_feed = publish_feed(conn, trade_date, feed_rows)
    manifest = write_manifest(trade_date, feed_rows)
    learning = write_learning_snapshot(trade_date, feed_rows)

    payload = {
        "success": True,
        "at": at,
        "trade_date": trade_date,
        "shadow_signals": shadow_n,
        "feed_rows": n_feed,
        "manifest": manifest,
        "learning": learning,
        "invariants": PHASE_INVARIANTS,
    }
    OUTPUTS["feed_last"].write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    print(json.dumps({
        "phase": PHASE,
        "trade_date": trade_date,
        "feed_rows": n_feed,
        "pilot_eligible": manifest.get("pilot_eligible_count"),
    }))
    return payload


if __name__ == "__main__":
    p: dict = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    run(p)
