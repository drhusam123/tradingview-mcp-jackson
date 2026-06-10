#!/usr/bin/env python3
"""
Client Signal Promotion
=======================
Second-pass promotion after opportunity_score_v2.

When multi-engine consensus (UES + opportunity + scan) confirms edge but a single
soft gate blocked the signal, promote to actionable=1 for Telegram delivery.

Hard vetoes (ANTI_LAW, HARD_GATE, FORECAST_DOWN, arbitration veto_triggered) are
never overridden.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"

SOFT_VETO_PREFIXES = (
    "QUALITY_GATE:ml_too_low",
    "QUALITY_GATE:negative_breadth_ad",
    "LOW_CONVICTION:WATCH",
)

HARD_VETO_PREFIXES = (
    "ANTI_LAW",
    "HARD_GATE:",
    "FORECAST_",
    "ARBITRATION:",
    "MISSING_RISK",
    "INVALID_RISK",
    "RR_TOO_LOW",
    "FINAL_EDGE:",
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
    )


def _is_soft_veto(reason: str | None) -> bool:
    if not reason:
        return False
    return any(reason.startswith(p) for p in SOFT_VETO_PREFIXES)


def _is_hard_veto(reason: str | None) -> bool:
    if not reason:
        return False
    return any(reason.startswith(p) for p in HARD_VETO_PREFIXES)


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    trade_date = params.get("date")
    if not trade_date:
        trade_date = conn.execute(
            "SELECT MAX(trade_date) FROM final_signals"
        ).fetchone()[0]
    if not trade_date:
        return {"success": False, "error": "no final_signals rows"}

    if not table_exists(conn, "opportunity_score_v2"):
        return {"success": True, "trade_date": trade_date, "promoted": 0, "reason": "no opportunity table"}

    min_opp = float(params.get("min_opportunity", 75.0))
    min_ues = float(params.get("min_ues", 70.0))
    min_scan = float(params.get("min_scan", 58.0))
    min_ml = float(params.get("min_ml", 55.0))
    allowed_stages = {"ULTRA", "STRONG", "ACCUMULATION"}

    rows = conn.execute(
        """
        SELECT fs.*, o.opportunity_score, o.stage AS opp_stage,
               o.structure_score, o.risk_score
        FROM final_signals fs
        LEFT JOIN opportunity_score_v2 o
          ON o.symbol = fs.symbol AND o.trade_date = fs.trade_date
        WHERE fs.trade_date = ?
          AND COALESCE(fs.actionable, 0) = 0
        """,
        (trade_date,),
    ).fetchall()

    promoted = []
    for r in rows:
        veto = r["veto_reason"] or ""
        if _is_hard_veto(veto):
            continue
        if veto and not _is_soft_veto(veto):
            continue

        opp = float(r["opportunity_score"] or 0)
        ues = float(r["score"] or 0)
        scan = float(r["source_rules"] or 0)
        ml = float(r["source_ml"] or 0)
        stage = (r["opp_stage"] or "").upper()

        tier = "MEDIUM"
        if ues >= 78 and ml >= 72 and scan >= 70 and opp >= 78:
            tier = "ULTRA"
        elif ues >= 72 and ml >= 65 and opp >= 75:
            tier = "HIGH"
        elif opp < min_opp or ues < min_ues or scan < min_scan or ml < min_ml:
            continue
        if stage and stage not in allowed_stages and tier == "MEDIUM":
            continue

        entry = r["entry_price"]
        stop = r["stop_loss"]
        t1 = r["t1_target"]
        entry_high = r["entry_high"]
        rr = float(r["r_ratio"] or 0)
        if not entry or not stop or not t1 or not entry_high:
            continue
        if stop >= entry or t1 <= entry or entry_high < entry or rr < 1.3:
            continue

        arb = conn.execute(
            """
            SELECT veto_triggered FROM arbitration_decisions
            WHERE symbol=? AND date=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (r["symbol"], trade_date),
        ).fetchone()
        if arb and int(arb["veto_triggered"] or 0) == 1:
            continue

        note = f"promoted:{tier}:opp={opp:.1f},ues={ues:.1f},ml={ml:.1f},was={veto}"
        breakdown = r["source_breakdown"] or "{}"
        try:
            import json as _json
            bd = _json.loads(breakdown) if breakdown else {}
        except Exception:
            bd = {}
        bd["promotion"] = {"source": "client_signal_promotion", "tier": tier, "note": note}
        # Telegram formatter requires quality_gate_passed=true in source_breakdown
        bd["quality_gate_passed"] = True
        if bd.get("gate_reason"):
            bd["promotion_gate_reason"] = bd.get("gate_reason")

        conn.execute(
            """
            UPDATE final_signals
            SET actionable = 1,
                veto_reason = NULL,
                source_breakdown = ?,
                updated_at = datetime('now')
            WHERE trade_date = ? AND symbol = ?
            """,
            (_json.dumps(bd, ensure_ascii=False), trade_date, r["symbol"]),
        )
        promoted.append(
            {
                "symbol": r["symbol"],
                "ues": ues,
                "ml": ml,
                "opportunity": opp,
                "stage": stage,
                "was_veto": veto,
            }
        )

    conn.commit()
    conn.close()
    return {
        "success": True,
        "trade_date": trade_date,
        "promoted": len(promoted),
        "symbols": promoted[:20],
    }


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
