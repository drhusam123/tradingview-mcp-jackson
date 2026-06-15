#!/usr/bin/env python3
"""MED-0.3 — discovery atoms for fabric (research boost / false-edge penalize)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

NOW = lambda: datetime.now(timezone.utc).isoformat()


def _atom(atom_id, layer, table, miner, cond=None, boost=1.0, penalize=1.0, hard_neg=0, n=None):
    return {
        "atom_id": atom_id,
        "source_layer": layer,
        "source_table": table,
        "source_miner": miner,
        "condition_json": json.dumps(cond or {"atom": atom_id}),
        "regime_filter": "",
        "boost_weight": boost,
        "penalize_weight": penalize,
        "hard_negative": hard_neg,
        "backtest_n": n,
        "status": "proposed",
        "proposed_at": NOW(),
    }


def mine_egx_med(db: sqlite3.Connection) -> List[dict]:
    out: List[dict] = []
    has = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='med_research_feed'"
    ).fetchone()
    if not has:
        return out

    td_row = db.execute("SELECT MAX(trade_date) d FROM med_research_feed").fetchone()
    td = td_row["d"] if td_row else None
    if not td:
        return out

    hc = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND med_bucket='MED_HIGH_CONVICTION_RESEARCH'",
        (td,),
    ).fetchone()["n"]
    pos = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND med_bucket='MED_POSITIVE_EXPECTANCY'",
        (td,),
    ).fetchone()["n"]
    fail = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND med_bucket='MED_FAILURE_WARNING'",
        (td,),
    ).fetchone()["n"]
    chase = db.execute(
        "SELECT COUNT(*) n FROM med_research_feed WHERE trade_date=? AND med_bucket='MED_DO_NOT_CHASE'",
        (td,),
    ).fetchone()["n"]
    hidden = db.execute(
        """SELECT COUNT(*) n FROM med_daily_scores m
           JOIN med_failure_patterns f ON m.trade_date=f.trade_date AND m.symbol=f.symbol
           WHERE m.trade_date=? AND m.risk_flags LIKE '%HIDDEN_ENERGY%'""",
        (td,),
    ).fetchone()["n"]

    p_tail_strong = db.execute(
        """SELECT COUNT(*) n FROM med_daily_scores d
           JOIN med_analogue_scores_daily a ON d.trade_date=a.trade_date AND d.symbol=a.symbol
           WHERE d.trade_date=? AND a.analogue_p_tail_20_10 >= 0.35 AND d.p_cond_20d_10 >= 0.15""",
        (td,),
    ).fetchone()["n"]

    out.append(_atom(
        "med_high_conviction_research", "L2", "med_research_feed", "egx_med_miner",
        cond={"trade_date": td, "bucket": "MED_HIGH_CONVICTION_RESEARCH"},
        boost=1.06 if hc else 1.01, n=hc,
    ))
    out.append(_atom(
        "med_positive_expectancy", "L2", "med_research_feed", "egx_med_miner",
        cond={"trade_date": td, "bucket": "MED_POSITIVE_EXPECTANCY"},
        boost=1.04 if pos else 1.01, n=pos,
    ))
    out.append(_atom(
        "med_p_tail_analogue_confluence", "L2", "med_analogue_scores_daily", "egx_med_miner",
        cond={"trade_date": td, "analogue_p_tail_gte": 0.35, "p_cond_gte": 0.15},
        boost=1.05 if p_tail_strong else 1.02, n=p_tail_strong,
    ))
    out.append(_atom(
        "med_hidden_energy_field", "L2", "med_daily_scores", "egx_med_miner",
        cond={"trade_date": td, "flag": "HIDDEN_ENERGY"},
        boost=1.04 if hidden else 1.02, n=hidden,
    ))
    out.append(_atom(
        "med_failure_warning_penalty", "L2", "med_research_feed", "egx_med_miner",
        cond={"trade_date": td, "bucket": "MED_FAILURE_WARNING"},
        boost=1.0, penalize=0.82 if fail else 1.0, hard_neg=1 if fail > 30 else 0, n=fail,
    ))
    out.append(_atom(
        "med_do_not_chase_penalty", "L2", "med_research_feed", "egx_med_miner",
        cond={"trade_date": td, "bucket": "MED_DO_NOT_CHASE"},
        boost=1.0, penalize=0.78 if chase else 1.0, hard_neg=1 if chase > 40 else 0, n=chase,
    ))

    edge_top = db.execute(
        """SELECT condition_key, n, hit_rate, expectancy FROM med_conditional_edge_tables
           WHERE horizon=20 AND abs(threshold-0.10)<1e-6 AND n>=30
           ORDER BY expectancy DESC LIMIT 1"""
    ).fetchone()
    if edge_top:
        out.append(_atom(
            "med_conditional_edge_top", "L2", "med_conditional_edge_tables", "egx_med_miner",
            cond={"condition_key": edge_top["condition_key"], "horizon": 20, "threshold": 0.10},
            boost=1.03, n=edge_top["n"],
        ))

    return out


def strict_false_edge_symbols(
    db: sqlite3.Connection,
    trade_date: str,
    max_n: int = 50,
) -> List[str]:
    """High-confidence MED false edges for discovery_ml_manifest hard_negative_symbols."""
    rows = db.execute(
        """
        SELECT symbol FROM med_daily_scores WHERE trade_date=?
          AND (
            (med_bucket='MED_FAILURE_WARNING' AND failure_similarity >= 0.35)
            OR (med_bucket='MED_DO_NOT_CHASE' AND crowding_score >= 0.70)
            OR (
              med_bucket='MED_FAILURE_WARNING'
              AND failure_similarity >= 0.25 AND crowding_score >= 0.75
            )
          )
        ORDER BY failure_similarity DESC, crowding_score DESC
        LIMIT ?
        """,
        (trade_date, max_n),
    ).fetchall()
    return [r["symbol"] for r in rows]


def export_false_edge_symbols(db: sqlite3.Connection, trade_date: str) -> dict:
    rows = db.execute(
        """
        SELECT symbol, med_bucket, med_score, p_cond_20d_10, failure_similarity, crowding_score
        FROM med_daily_scores WHERE trade_date=?
          AND med_bucket IN ('MED_FAILURE_WARNING', 'MED_DO_NOT_CHASE')
        ORDER BY med_score DESC
        """,
        (trade_date,),
    ).fetchall()
    return {
        "trade_date": trade_date,
        "false_edge_count": len(rows),
        "symbols": [dict(r) for r in rows],
    }
