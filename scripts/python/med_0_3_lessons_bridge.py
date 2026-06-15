#!/usr/bin/env python3
"""MED-0.3 — sync discoveries into TRADING_LESSONS.md (idempotent auto block)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
LESSONS = ROOT / "TRADING_LESSONS.md"
OUTPUT = DATA / "med_lessons_bridge_last.json"

MARKER_START = "<!-- MED-0.3-AUTO-START -->"
MARKER_END = "<!-- MED-0.3-AUTO-END -->"


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _pct(n: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{100 * n / total:.1f}%"


def build_section(sources: Dict[str, Any]) -> str:
    report = sources.get("report") or {}
    audit = sources.get("audit") or {}
    wire = sources.get("wire") or {}
    replay = sources.get("replay") or {}
    fwd = sources.get("forward") or {}
    status = sources.get("status") or {}

    td = report.get("trade_date") or audit.get("trade_date") or "—"
    buckets = report.get("buckets") or {}
    total = sum(buckets.values()) or 1
    fail_n = buckets.get("MED_FAILURE_WARNING", 0)
    chase_n = buckets.get("MED_DO_NOT_CHASE", 0)
    hc_n = buckets.get("MED_HIGH_CONVICTION_RESEARCH", 0)

    lift = (replay.get("incremental_lift") or {}).get("MED_LRE_vs_LRE") or {}
    med_med = lift.get("median_delta")
    med_pp = round(float(med_med) * 100, 2) if med_med is not None else None

    false_edge = (report.get("false_edge_feed") or {}).get("false_edge_count", 0)
    strict_hn = 0
    for c in wire.get("checks") or []:
        if c.get("name") == "med_strict_hn_in_manifest" and c.get("detail", "").startswith("count="):
            try:
                strict_hn = int(str(c["detail"]).split("=")[1])
            except ValueError:
                pass

    grad = status.get("graduation") or fwd
    live_closed = grad.get("live_closed") or grad.get("live_closed_trades") or 0
    grad_met = grad.get("graduation_met") or grad.get("ready_for_feed_boost") or False

    edges = report.get("conditional_edges_top") or []
    top_edge = edges[0] if edges else {}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        MARKER_START,
        "",
        "### القاعدة #24 — MED-0.4 Mathematical Edge Field (shadow research)",
        "",
        f"**آخر تحديث تلقائي**: {now} | **trade_date**: `{td}`",
        "",
        "#### ما يفعله MED في النظام (يونيو 2026)",
        "```",
        "med_0_3_daily_chain → med_0_4 dual-score + HC gate (cap 8)",
        "  → discovery_fabric (penalize atoms validated)",
        "  → discovery_ml_manifest (med_* penalize + strict hard_negative_symbols)",
        "  → opportunity_score_v2 (MED_FEED_PENALIZE=1, لا boost)",
        "  → actionable / Telegram ← بدون تغيير (MED_CLIENT_SIGNAL=0)",
        "```",
        "",
        "#### فلاتر إلزامية من MED (طبّق مع القواعد #1–#10)",
        "",
        "| إشارة MED | الشرط | الإجراء |",
        "|-----------|--------|---------|",
        f"| `MED_FAILURE_WARNING` | failure_similarity ≥ 0.35 | خصم opp −8..14 أو hard_negative إذا crowding عالي |",
        f"| `MED_DO_NOT_CHASE` | crowding_score ≥ 0.70 | خصم opp −10..14 — لا مطاردة |",
        f"| stored_energy ≥ 0.2 | **ميت على مقياس LRE** | استخدم med_score/percentile لا stored_energy خام |",
        f"| HIGH_CONVICTION | sample_quality + calibrate:full | حالياً `{hc_n}` — يحتاج weekly calibrate |",
        "",
        f"#### توزيع اليوم ({total} سهم)",
        f"- FAILURE_WARNING: **{fail_n}** ({_pct(fail_n, total)})",
        f"- DO_NOT_CHASE: **{chase_n}** ({_pct(chase_n, total)})",
        f"- false_edge_feed: **{false_edge}** | strict manifest HN: **{strict_hn}**",
        "",
    ]

    if med_pp is not None:
        lines.extend([
            "#### OOS replay (MED_LRE vs LRE)",
            f"- median return delta: **+{med_pp}pp** (n={lift.get('n_a', '—')})",
            "- MED يضيف قيمة بحثية في التصفية قبل أي boost للعميل",
            "",
        ])

    if top_edge:
        lines.extend([
            "#### أقوى conditional edge (20d / 10%)",
            f"- `{top_edge.get('condition_key', '—')}` | n={top_edge.get('n')} | "
            f"hit={round(float(top_edge.get('hit_rate') or 0) * 100, 1)}% | "
            f"E={round(float(top_edge.get('expectancy') or 0), 2)}",
            "",
        ])

    lines.extend([
        "#### graduation — قبل `MED_FEED_BOOST=1`",
        f"- live closed: **{live_closed}/40** | graduation_met: **{grad_met}**",
        "- penalize فقط (`MED_FEED_PENALIZE=1`) حتى اكتمال 40 صفقة live OOS",
        "",
        "```python",
        "# invariants — لا تكسر",
        "MED_SHADOW=1 | MED_CLIENT_SIGNAL=0 | MED_OPP_BOOST=0 | MED_FEED_BOOST=0",
        "MED_FEED_PENALIZE=1  # downrank بحثي في opp_v2",
        "```",
        "",
        MARKER_END,
        "",
    ])
    return "\n".join(lines)


def run(params: dict | None = None) -> dict:
    params = params or {}
    sources = {
        "report": _read_json(DATA / "med_0_3_discovery_report.json"),
        "audit": _read_json(DATA / "med_0_3_audit_last.json"),
        "wire": _read_json(DATA / "med_0_3_wire_acceptance_last.json"),
        "replay": _read_json(DATA / "med_replay_audit_last.json"),
        "forward": _read_json(DATA / "med_forward_shadow_last.json"),
        "status": _read_json(DATA / "med_0_3_status_last.json"),
    }
    section = build_section(sources)

    if not LESSONS.exists():
        return {"success": False, "error": "TRADING_LESSONS.md missing"}

    content = LESSONS.read_text(encoding="utf-8")
    if MARKER_START in content and MARKER_END in content:
        pre = content.split(MARKER_START)[0]
        post = content.split(MARKER_END)[-1]
        new_content = pre.rstrip() + "\n\n" + section + post.lstrip("\n")
        action = "replaced"
    else:
        new_content = content.rstrip() + "\n\n---\n\n" + section
        action = "appended"

    if not params.get("dry_run"):
        LESSONS.write_text(new_content, encoding="utf-8")

    out = {
        "success": True,
        "action": action,
        "lessons_path": str(LESSONS.relative_to(ROOT)),
        "section_lines": section.count("\n"),
        "dry_run": bool(params.get("dry_run")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), ensure_ascii=False, indent=2))
