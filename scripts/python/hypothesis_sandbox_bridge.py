#!/usr/bin/env python3
"""
Hypothesis sandbox bridge — promoted sandbox_hypotheses → discovery feedback + quant seeds.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUT_PATH = ROOT / "data" / "hypothesis_sandbox_bridge_last.json"
FEEDBACK_PATH = ROOT / "data" / "discovery_feedback_last.json"

LAW_TO_ATOMS = {
    "VOLUME": ["vol_2_5_3", "vol_2_4"],
    "ACCUMULATION": ["lower_third_close", "low20_retest", "vol_2_5_3"],
    "RETEST": ["low20_retest", "not_extended_3d"],
    "BREAKOUT": ["high20_break", "vol_2_5_3"],
    "REVERSAL": ["lower_third_close", "bb_squeeze_low35"],
    "MOMENTUM": ["mom5_2_18", "above_ema20"],
}

ATOM_RE = re.compile(
    r"(lower_third|vol_2_5|low20|near_ath|bb_squeeze|range_lt|upper_close|high20)",
    re.I,
)


def _extract_atoms(text: str, law_type: str) -> list[str]:
    found = []
    for m in ATOM_RE.findall(text or ""):
        key = m.lower().replace(" ", "_")
        if "lower_third" in key:
            found.append("lower_third_close")
        elif "vol_2_5" in key:
            found.append("vol_2_5_3")
        elif "low20" in key:
            found.append("low20_retest")
        elif "near_ath" in key:
            found.append("not_near_ath")
        elif "bb_squeeze" in key:
            found.append("bb_squeeze_low35")
        elif "range_lt" in key:
            found.append("range_lt4pct")
        elif "upper_close" in key:
            found.append("upper_close")
        elif "high20" in key:
            found.append("high20_break")
    for a in LAW_TO_ATOMS.get((law_type or "").upper(), []):
        if a not in found:
            found.append(a)
    return found[:6]


def load_promoted(db):
    try:
        rows = db.execute(
            """
            SELECT hypothesis_id, hypothesis_text, law_type, regime_filter, precision, eae, source
            FROM sandbox_hypotheses
            WHERE status = 'PROMOTED'
            ORDER BY COALESCE(precision, 0) DESC, COALESCE(eae, 0) DESC
            LIMIT 40
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        out.append({
            "hypothesis_id": r[0],
            "text": r[1],
            "law_type": r[2],
            "regime_filter": r[3],
            "precision": r[4],
            "eae": r[5],
            "source": r[6],
            "atoms": _extract_atoms(r[1], r[2]),
        })
    return out


def merge_feedback(new_items: list[dict]):
    base = {"at": datetime.now(timezone.utc).isoformat(), "queue": []}
    if FEEDBACK_PATH.exists():
        try:
            base = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    queue = list(base.get("queue") or [])
    seen = {(i.get("type"), i.get("target")) for i in queue}
    added = 0
    for item in new_items:
        key = (item.get("type"), item.get("target"))
        if key in seen:
            continue
        queue.append(item)
        seen.add(key)
        added += 1
    base["at"] = datetime.now(timezone.utc).isoformat()
    base["queue"] = queue
    base["n_items"] = len(queue)
    base["sandbox_bridge_added"] = added
    FEEDBACK_PATH.write_text(json.dumps(base, indent=2), encoding="utf-8")
    return base


def run(params: dict | None = None):
    params = params or {}
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    promoted = load_promoted(db)
    db.close()

    feedback_items = []
    priority_atoms = set()
    seed_pairs = []
    for h in promoted:
        atoms = h.get("atoms") or []
        priority_atoms.update(atoms)
        if len(atoms) >= 2:
            seed_pairs.append(atoms[:2])
        feedback_items.append({
            "type": "UPRANK_BEHAVIORAL",
            "target": (h.get("law_type") or "SANDBOX").upper(),
            "priority": round(min(0.95, 0.55 + (h.get("precision") or 0) / 200), 2),
            "rationale": f"Sandbox PROMOTED: {h.get('hypothesis_id')} prec={h.get('precision')}",
            "atoms": atoms,
            "regime_filter": h.get("regime_filter"),
        })

    if params.get("merge_feedback", True) and feedback_items:
        merge_feedback(feedback_items)

    payload = {
        "success": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "n_promoted": len(promoted),
        "promoted": promoted[:20],
        "priority_atoms": sorted(priority_atoms),
        "seed_pairs": seed_pairs[:16],
        "feedback_items": feedback_items,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), ensure_ascii=False))
