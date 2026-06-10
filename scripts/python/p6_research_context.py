"""
P6 research context loader — closed loop → evolution + cognition.
Reads data/p6_research_context.json from egx_closed_loop.mjs.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTEXT_PATH = ROOT / "data" / "p6_research_context.json"


def load_context() -> dict | None:
    if not CONTEXT_PATH.exists():
        return None
    try:
        return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ctx_from_params(params: dict | None) -> dict | None:
    if params and params.get("p6_context"):
        return params["p6_context"]
    return load_context()


def ingest_p6_ultra_failures(db, params: dict | None = None) -> dict:
    """P6 live ULTRA losses → failure_reconstruction (closed loop)."""
    ctx = _ctx_from_params(params) or {}
    downrank = set(ctx.get("evolution_hints", {}).get("downrank_behavioral") or [])

    try:
        losses = db.execute("""
            SELECT symbol, signal_date, behavioral_class, return_t5
            FROM recommendation_outcomes
            WHERE conviction_tier = 'ULTRA_CONVICTION'
              AND outcome_filled >= 5
              AND hit_t5 = 0
            ORDER BY signal_date DESC
            LIMIT 30
        """).fetchall()
    except Exception:
        return {"n_ingested": 0, "reason": "NO_RECOMMENDATION_OUTCOMES"}

    n = 0
    symbols = []
    for f in losses:
        cls = f["behavioral_class"] or "UNKNOWN"
        primary = "P6_ULTRA_LOSS"
        if cls in downrank:
            primary = f"P6_{cls}_DOWNRANK"
        try:
            db.execute("""
                INSERT INTO failure_reconstruction
                  (failure_date, symbol, law_id, law_name, direction,
                   failure_class, primary_cause, secondary_cause,
                   regime_at_failure, feature_value_at_failure,
                   n_competing_laws, reconstruction_confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                f["signal_date"], f["symbol"], "P6_LIVE", "ULTRA_CONVICTION", "UP",
                primary, primary, cls,
                "", None, 0, 0.85,
            ))
            n += 1
            symbols.append(f["symbol"])
        except Exception:
            pass

    if n:
        db.commit()

    findings = []
    if n:
        findings.append(f"P6 live: {n} ULTRA losses ingested into failure_reconstruction")
    explosive_n = sum(1 for f in losses if (f["behavioral_class"] or "") == "EXPLOSIVE")
    if explosive_n and "EXPLOSIVE" in downrank:
        findings.append(f"P6 priority: EXPLOSIVE downrank ({explosive_n} recent ULTRA losses)")

    return {
        "n_ingested": n,
        "symbols": symbols[:15],
        "key_findings": findings,
        "downrank_targets": list(downrank),
    }


def apply_p6_stock_adjustments(db, params: dict | None = None) -> dict:
    """Post stock_behavioral_memory — bump false_signal_rate from P6 outcomes."""
    ctx = _ctx_from_params(params) or {}
    hints = ctx.get("evolution_hints") or {}
    downrank = set(hints.get("downrank_behavioral") or [])
    loss_symbols = set(hints.get("loss_symbols") or [])

    adjusted = []
    class_bumps = 0

    for cls in downrank:
        try:
            cur = db.execute("""
                UPDATE stock_behavioral_memory
                SET false_signal_rate = MIN(0.95, COALESCE(false_signal_rate, 0.5) + 0.08),
                    mutation_flag = 1
                WHERE behavioral_class = ?
            """, (cls,))
            class_bumps += cur.rowcount
        except Exception:
            pass

    for sym in loss_symbols:
        try:
            cur = db.execute("""
                UPDATE stock_behavioral_memory
                SET false_signal_rate = MIN(0.98, COALESCE(false_signal_rate, 0.5) + 0.10),
                    mutation_flag = 1
                WHERE symbol = ?
            """, (sym,))
            if cur.rowcount:
                adjusted.append(sym)
        except Exception:
            pass

    if adjusted or class_bumps:
        db.commit()

    findings = []
    if class_bumps:
        findings.append(f"P6 adjusted false_signal_rate for {class_bumps} {','.join(downrank)} stocks")
    if adjusted:
        findings.append(f"P6 penalized {len(adjusted)} ULTRA-loss symbols in behavioral memory")

    return {
        "class_rows_bumped": class_bumps,
        "symbol_rows_bumped": len(adjusted),
        "symbols": adjusted[:15],
        "key_findings": findings,
    }


def apply_p6_cognition_priorities(db, params: dict | None = None) -> dict:
    """Extract P6 priorities for cognition pipeline key_findings."""
    ctx = _ctx_from_params(params) or {}
    hints = ctx.get("cognition_hints") or {}
    priorities = []

    if hints.get("prioritize_explosive_review"):
        priorities.append({
            "focus": "EXPLOSIVE",
            "reason": "P6 forensic downrank — review explosion archetypes",
            "loss_count": hints.get("explosion_loss_count", 0),
        })

    for flag in hints.get("pattern_flags") or []:
        priorities.append({
            "focus": flag,
            "reason": "P6 loss autopsy pattern — investigate in self_evolution",
        })

    gate = ctx.get("p6_gate") or {}
    findings = []
    if gate.get("n_completed"):
        wr = gate.get("win_rate")
        wr_s = f"{wr:.1f}%" if wr is not None else "—"
        findings.append(
            f"P6 gate: {gate['n_completed']}/{gate.get('min_n', 30)} @ {wr_s} WR"
        )
    if priorities:
        findings.append(f"P6 cognition priorities: {len(priorities)} active")

    return {
        "priorities": priorities,
        "key_findings": findings,
        "p6_context_loaded": bool(ctx),
    }
