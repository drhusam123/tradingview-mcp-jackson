"""
Unified discovery context loader (Python mirror of discovery_context.mjs).
Used by research_director, adaptive_research_loop, and other Python engines.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"


def _read_json(name: str) -> dict | None:
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _merge_queues(primary: list, secondary: list) -> list:
    seen: set[str] = set()
    out: list[dict] = []
    for item in [*primary, *secondary]:
        key = f"{item.get('type')}|{item.get('target')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return sorted(out, key=lambda x: float(x.get("priority") or 0), reverse=True)


def read_pending_directives(limit: int = 12) -> list[dict]:
    if not DB_PATH.exists():
        return []
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT directive_id, directive_type, target, priority, rationale, created_at
            FROM research_directives
            WHERE status = 'PENDING'
            ORDER BY priority DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def load_discovery_params(signal_date: str | None = None) -> dict:
    """Build quant/opp/promotion params identical to buildDiscoveryParams()."""
    feedback = _read_json("discovery_feedback_last.json") or {}
    p6 = _read_json("p6_research_context.json")
    followup = _read_json("opportunity_followup_last.json")
    quality = _read_json("discovery_quality_last.json")
    directives = read_pending_directives(12)

    fb_queue = list(feedback.get("queue") or [])
    p6_queue = []
    if p6:
        p6_queue = (p6.get("discovery_feedback") or {}).get("queue") or p6.get("research_priorities") or []
    queue = _merge_queues(fb_queue, p6_queue)

    dq_score = (quality or {}).get("discovery_quality_score")
    if dq_score is None and p6:
        dq_score = (p6.get("discovery_quality") or {}).get("score")

    strict = dq_score is not None and float(dq_score) < 52

    return {
        "feedback_queue": queue,
        "p6_priorities": (p6 or {}).get("research_priorities") or [],
        "evolution_hints": (p6 or {}).get("evolution_hints") or {},
        "p6_gate": (p6 or {}).get("p6_gate") or {},
        "p6_directives": [d["target"] for d in directives if d.get("target")],
        "opportunity_followup": followup,
        "signal_date": signal_date or (p6 or {}).get("signal_date"),
        "strict_quality": strict,
        "discovery_quality_score": dq_score,
    }
