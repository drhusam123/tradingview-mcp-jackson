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
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
RULES_PATH = ROOT / "egx_rules.json"
PROOF_PATH = ROOT / "data" / "proof_loop_last.json"
P6_MIN_WR_SOFT = 50
P6_MIN_N_SOFT = 20

_DEFAULT_RULES = {
    "lessons_filters": {
        "near_ath_min_vol_ratio": 2.5,
        "optimal_vol_ratio_min": 2.5,
        "optimal_vol_ratio_max": 3.5,
    },
    "behavioral_filters": {
        "block_volatile_client": True,
        "block_dormant_client": True,
        "explosive_max_rsi": 70,
        "explosive_min_vol_ratio": 2.5,
        "explosive_ultra_thin_vol": 1.0,
        "volatile_max_rsi": 65,
        "volatile_min_vol_ratio": 2.5,
        "high_false_signal_rate_max": 0.65,
        "block_upper_third_close": True,
        "max_close_position": 0.66,
        "block_volume_chase": True,
        "max_vol_ratio_chase": 3.5,
        "repeat_ultra_loss_lookback_days": 120,
        "max_ultra_losses_per_symbol": 1,
        "require_indicator_cache": True,
    },
}

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
    conn.execute("PRAGMA busy_timeout=60000")
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


def _p6_ultra_promotion_allowed() -> bool:
    """Proof-aware gate: pause new ULTRA promotions when live WR is critically low."""
    if not PROOF_PATH.exists():
        return True
    try:
        proof = json.loads(PROOF_PATH.read_text(encoding="utf-8"))
        wr = proof.get("win_rate")
        n = int(proof.get("n_completed") or 0)
        if n >= P6_MIN_N_SOFT and wr is not None and float(wr) < P6_MIN_WR_SOFT:
            return False
    except Exception:
        pass
    return True


def _load_egx_rules() -> dict:
    rules = dict(_DEFAULT_RULES)
    if RULES_PATH.exists():
        try:
            loaded = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            rules["lessons_filters"] = {
                **_DEFAULT_RULES["lessons_filters"],
                **(loaded.get("lessons_filters") or {}),
            }
            rules["behavioral_filters"] = {
                **_DEFAULT_RULES["behavioral_filters"],
                **(loaded.get("behavioral_filters") or {}),
            }
        except Exception:
            pass
    return rules


def _indicator_row(conn: sqlite3.Connection, symbol: str, trade_date: str) -> sqlite3.Row | None:
    if not table_exists(conn, "indicators_cache"):
        return None
    return conn.execute(
        """
        SELECT vol_ratio_20, rsi14, close_position
        FROM indicators_cache
        WHERE symbol=? AND bar_date=?
        ORDER BY bar_date DESC LIMIT 1
        """,
        (symbol, trade_date),
    ).fetchone()


def _behavioral_row(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    if not table_exists(conn, "stock_behavioral_memory"):
        return None
    return conn.execute(
        "SELECT behavioral_class, false_signal_rate FROM stock_behavioral_memory WHERE symbol=?",
        (symbol,),
    ).fetchone()


def _delivery_safety_block(
    conn: sqlite3.Connection,
    symbol: str,
    trade_date: str,
    setup_type: str | None = None,
) -> str | None:
    """Mirror scripts/lib/egx_safety_check.mjs — block promotion before delivery veto."""
    rules = _load_egx_rules()
    bf = rules.get("behavioral_filters") or {}
    lf = rules.get("lessons_filters") or {}

    ind = _indicator_row(conn, symbol, trade_date)
    if bf.get("require_indicator_cache", True) and not ind:
        return "indicator_cache"

    beh = _behavioral_row(conn, symbol)
    bclass = (beh["behavioral_class"] if beh else "UNKNOWN").upper()
    fsr = float(beh["false_signal_rate"] or 0) if beh else None

    vol = float(ind["vol_ratio_20"]) if ind and ind["vol_ratio_20"] is not None else None
    rsi = float(ind["rsi14"]) if ind and ind["rsi14"] is not None else None
    cp = float(ind["close_position"]) if ind and ind["close_position"] is not None else None

    setup = (setup_type or "").lower()
    near_ath = "near ath" in setup or "ath" in setup

    if bf.get("block_dormant_client", True) and bclass == "DORMANT":
        return "behavioral_dormant"

    fsr_max = float(bf.get("high_false_signal_rate_max", 0.65))
    if fsr is not None and fsr > fsr_max:
        return "high_false_signal_rate"

    explosive_max_rsi = float(bf.get("explosive_max_rsi", 70))
    if bclass == "EXPLOSIVE" and rsi is not None and rsi > explosive_max_rsi:
        return "explosive_rsi"

    ultra_thin = float(bf.get("explosive_ultra_thin_vol", 1.0))
    if bclass == "EXPLOSIVE" and vol is not None and vol < ultra_thin:
        max_losses = bf.get("max_ultra_losses_per_symbol")
        if max_losses is not None and table_exists(conn, "recommendation_outcomes"):
            lookback = int(bf.get("repeat_ultra_loss_lookback_days", 120))
            cutoff = (date.today() - timedelta(days=lookback)).isoformat()
            prior = conn.execute(
                """
                SELECT COUNT(*) FROM recommendation_outcomes
                WHERE symbol=? AND conviction_tier='ULTRA_CONVICTION'
                  AND outcome_filled>=5 AND hit_t5=0
                  AND signal_date>=? AND signal_date<?
                """,
                (symbol, cutoff, trade_date),
            ).fetchone()[0]
            if prior >= int(max_losses):
                return "explosive_ultra_thin_repeat"

    max_cp = float(bf.get("max_close_position", 0.66))
    if bf.get("block_upper_third_close", True) and cp is not None and cp > max_cp:
        return "upper_third_close"

    chase_max = float(bf.get("max_vol_ratio_chase", 3.5))
    if bf.get("block_volume_chase", True) and vol is not None and vol > chase_max:
        return "volume_chase"

    if near_ath and vol is not None and vol < float(lf.get("near_ath_min_vol_ratio", 2.5)):
        return "near_ath_volume"

    if bf.get("block_volatile_client", True) and bclass == "VOLATILE":
        vol_min = float(bf.get("volatile_min_vol_ratio", 2.5))
        vol_max = float(lf.get("optimal_vol_ratio_max", 3.5))
        volatile_max_rsi = float(bf.get("volatile_max_rsi", 65))
        vol_ok = vol is not None and vol_min <= vol <= vol_max
        rsi_ok = rsi is None or rsi <= volatile_max_rsi
        if not (vol_ok and rsi_ok):
            return "behavioral_volatile"

    max_losses = bf.get("max_ultra_losses_per_symbol")
    if max_losses is not None and table_exists(conn, "recommendation_outcomes"):
        lookback = int(bf.get("repeat_ultra_loss_lookback_days", 120))
        cutoff = (date.today() - timedelta(days=lookback)).isoformat()
        loss_n = conn.execute(
            """
            SELECT COUNT(*) FROM recommendation_outcomes
            WHERE symbol=? AND conviction_tier='ULTRA_CONVICTION'
              AND outcome_filled>=5 AND hit_t5=0
              AND signal_date>=? AND signal_date<?
            """,
            (symbol, cutoff, trade_date),
        ).fetchone()[0]
        if loss_n >= int(max_losses):
            return "repeat_ultra_loser"

    return None


def _load_promotion_tuning(params: dict) -> dict:
    try:
        from discovery_feedback_loader import load_feedback_queue, load_promotion_tuning

        queue = params.get("feedback_queue") or load_feedback_queue()
        followup = params.get("opportunity_followup")
        return load_promotion_tuning(queue, followup)
    except Exception:
        return {
            "min_opportunity": 75.0,
            "min_ues": 70.0,
            "min_scan": 58.0,
            "min_ml": 55.0,
            "adjustments": [],
        }


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    trade_date = params.get("date")
    if not trade_date:
        from discovery_constants import FINAL_SIGNALS_PROD_WHERE
        trade_date = conn.execute(
            f"SELECT MAX(trade_date) FROM final_signals WHERE {FINAL_SIGNALS_PROD_WHERE}"
        ).fetchone()[0]
    if not trade_date:
        return {"success": False, "error": "no final_signals rows"}

    if not table_exists(conn, "opportunity_score_v2"):
        return {"success": True, "trade_date": trade_date, "promoted": 0, "reason": "no opportunity table"}

    try:
        from discovery_promotion_policy import (
            effective_scan_score,
            is_opp_stage_promotable,
            veto_allows_discovery_override,
            promotion_skip_reason,
        )
    except ImportError:
        effective_scan_score = lambda r: float(r["source_rules"] or 0)
        is_opp_stage_promotable = lambda s: True
        veto_allows_discovery_override = lambda v, r: False
        promotion_skip_reason = None

    tuning = _load_promotion_tuning(params)
    min_opp = float(params.get("min_opportunity", tuning["min_opportunity"]))
    min_ues = float(params.get("min_ues", tuning["min_ues"]))
    min_scan = float(params.get("min_scan", tuning["min_scan"]))
    min_ml = float(params.get("min_ml", tuning["min_ml"]))

    rows = conn.execute(
        """
        SELECT fs.*, o.opportunity_score, o.stage AS opp_stage,
               o.structure_score, o.risk_score, o.flags_json
        FROM final_signals fs
        LEFT JOIN opportunity_score_v2 o
          ON o.symbol = fs.symbol AND o.trade_date = fs.trade_date
        WHERE fs.trade_date = ?
          AND COALESCE(fs.actionable, 0) = 0
        """,
        (trade_date,),
    ).fetchall()

    promoted = []
    skipped = []
    for r in rows:
        veto = r["veto_reason"] or ""
        if _is_hard_veto(veto):
            skipped.append({"symbol": r["symbol"], "reason": f"hard_veto:{veto}"})
            continue
        discovery_override = veto_allows_discovery_override(veto, r)
        if veto and not _is_soft_veto(veto) and not discovery_override:
            skipped.append({"symbol": r["symbol"], "reason": f"veto:{veto}"})
            continue

        opp = float(r["opportunity_score"] or 0)
        ues = float(r["score"] or 0)
        scan = effective_scan_score(r)
        ml = float(r["source_ml"] or 0)
        stage = (r["opp_stage"] or "").upper()

        tier = "MEDIUM"
        if ues >= 78 and ml >= 72 and scan >= 70 and opp >= 78:
            tier = "ULTRA"
        elif ues >= 72 and ml >= 65 and opp >= 75:
            tier = "HIGH"
        elif opp >= 78 and scan >= 65 and ues >= 70:
            tier = "HIGH"  # discovery-led promotion path
        if tier == "ULTRA" and not _p6_ultra_promotion_allowed():
            tier = "HIGH"
        if promotion_skip_reason:
            skip = promotion_skip_reason(
                r, min_opp=min_opp, min_ues=min_ues, min_scan=min_scan, min_ml=min_ml,
                tier=tier, veto=veto if not discovery_override else None,
            )
            if skip:
                skipped.append({"symbol": r["symbol"], "reason": skip, "opp": opp, "ues": ues})
                continue
        elif opp < min_opp or ues < min_ues or scan < min_scan or ml < min_ml:
            skipped.append({"symbol": r["symbol"], "reason": "thresholds", "opp": opp, "ues": ues, "scan": scan})
            continue
        if stage and not is_opp_stage_promotable(stage) and tier == "MEDIUM":
            skipped.append({"symbol": r["symbol"], "reason": f"stage:{stage}"})
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
            SELECT veto_triggered, veto_reason FROM arbitration_decisions
            WHERE symbol=? AND date=?
            ORDER BY computed_at DESC LIMIT 1
            """,
            (r["symbol"], trade_date),
        ).fetchone()
        if arb and int(arb["veto_triggered"] or 0) == 1:
            arb_veto = arb["veto_reason"] or ""
            try:
                from discovery_promotion_policy import arbitration_allows_discovery_override
                arb_ok = arbitration_allows_discovery_override(arb_veto, r)
            except ImportError:
                arb_ok = False
            if not arb_ok:
                skipped.append({
                    "symbol": r["symbol"],
                    "reason": f"arbitration_veto:{arb_veto}",
                    "opp": opp,
                })
                continue

        safety_block = _delivery_safety_block(
            conn, r["symbol"], trade_date, r["setup_type"]
        )
        if safety_block:
            continue

        note = f"promoted:{tier}:opp={opp:.1f},ues={ues:.1f},ml={ml:.1f},scan={scan:.1f},was={veto or 'none'}"
        if discovery_override and veto:
            note += ";discovery_override"
        breakdown = r["source_breakdown"] or "{}"
        try:
            import json as _json
            bd = _json.loads(breakdown) if breakdown else {}
        except Exception:
            bd = {}
        bd["promotion"] = {
            "source": "client_signal_promotion",
            "tier": tier,
            "note": note,
            "discovery_override": bool(discovery_override and veto),
            "opp_stage": stage,
        }
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
        "promotion_tuning": {
            "min_opportunity": min_opp,
            "min_ues": min_ues,
            "min_scan": min_scan,
            "min_ml": min_ml,
            "adjustments": tuning.get("adjustments", []),
        },
        "skipped_sample": skipped[:15],
        "n_skipped": len(skipped),
    }


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
