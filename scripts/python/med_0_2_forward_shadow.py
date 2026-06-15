#!/usr/bin/env python3
"""MED-2 — Forward shadow ledger (live from 2026-06-12 + OOS research backfill)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from statistics import median

from med_common import (
    DATA, ELIGIBLE_BUCKETS, MED2_FORWARD_START, MED_INVARIANTS, MIN_BARS,
    OOS_END, OOS_START, PRIMARY_H, connect, ensure_med_tables,
    forward_return, load_bars, load_lre_context, load_mde_context, load_sectors,
    pf_from_returns, top10_dominance,
)
from lre_3_3_dual_gate_audit import forward_metrics
from med_0_1_math_features import compute_math_fields
from med_0_1_distribution_shift import distribution_shift

OUTPUT = DATA / "med_forward_shadow_last.json"


from med_0_3_calibration import MedThresholds, med_ok_v3

MED_TH = MedThresholds()


def _med_lre_eligible(mf: dict, lre: dict) -> bool:
    """MED-0.3 MED_LRE filter."""
    stage = int(lre.get("stage") or mf.get("lre_stage") or 0)
    return stage >= 3 and med_ok_v3(mf, MED_TH)


def backfill_oos_research(conn, by_sym: dict, lre_all: dict, mde_all: dict) -> int:
    from med_0_3_replay_utils import precompute_mf_by_day
    from med_0_3_regime_context import load_all_regime_caches, regime_context_for

    markov, rotation, breadth = load_all_regime_caches(conn)
    mf_index = precompute_mf_by_day(by_sym, conn)
    n = 0
    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS + PRIMARY_H:
            continue
        for idx in range(MIN_BARS, len(bars) - PRIMARY_H):
            d = bars[idx]["date"]
            if d < OOS_START or d > OOS_END:
                continue
            lre = lre_all.get(d, {}).get(sym, {})
            mde = mde_all.get(d, {}).get(sym, {})
            mf = compute_math_fields(bars, idx, lre, mde,
                                     regime_context_for(d, "Unknown", markov, rotation, breadth))
            if not mf:
                continue
            cached = mf_index.get((d, sym))
            if cached:
                mf["se_rank"] = cached.get("se_rank", 0)
            if not _med_lre_eligible(mf, lre):
                continue
            dist = distribution_shift(bars, idx, mf)
            fwd = forward_metrics(bars, idx)
            conn.execute(
                """
                INSERT OR REPLACE INTO med_forward_shadow_ledger
                (trade_date, symbol, sector, med_score, med_bucket, condition_key,
                 analogue_p_tail, forward_return_5d, forward_return_10d, forward_return_20d,
                 mfe_20d, mae_20d, exit_status, ledger_mode, client_path_allowed)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    d, sym, "Unknown", mf.get("stored_energy", 0) * 100,
                    "MED_RESEARCH_OOS", "MED_LRE|" + ("HIDDEN_ENERGY" if mf.get("hidden_energy_flag") else "FIELD"),
                    dist.get("shift_score", 0),
                    fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                    fwd.get("forward_return_20d"), fwd.get("mfe_20d"), fwd.get("mae_20d"),
                    "closed" if fwd.get("forward_return_20d") is not None else "open",
                    "research_oos",
                ),
            )
            n += 1
    conn.commit()
    return n


def persist_live(conn, trade_date: str, by_sym: dict) -> int:
    rows = conn.execute(
        """
        SELECT symbol, med_score, med_bucket, condition_key
        FROM med_research_feed WHERE trade_date=? AND med_bucket IN ({})
        """.format(",".join("?" * len(ELIGIBLE_BUCKETS))),
        (trade_date, *ELIGIBLE_BUCKETS),
    ).fetchall()
    n = 0
    for r in rows:
        sym = r["symbol"]
        bars = by_sym.get(sym)
        fwd = {}
        if bars:
            idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
            if idx is not None:
                fwd = forward_metrics(bars, idx)
        ana = conn.execute(
            "SELECT analogue_p_tail_20_10 FROM med_analogue_scores_daily WHERE trade_date=? AND symbol=?",
            (trade_date, sym),
        ).fetchone()
        conn.execute(
            """
            INSERT OR REPLACE INTO med_forward_shadow_ledger
            (trade_date, symbol, sector, med_score, med_bucket, condition_key,
             analogue_p_tail, forward_return_5d, forward_return_10d, forward_return_20d,
             mfe_20d, mae_20d, exit_status, ledger_mode, client_path_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                trade_date, sym, None, r["med_score"], r["med_bucket"], r["condition_key"],
                ana["analogue_p_tail_20_10"] if ana else None,
                fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                fwd.get("forward_return_20d"), fwd.get("mfe_20d"), fwd.get("mae_20d"),
                "open", "live",
            ),
        )
        n += 1
    conn.commit()
    return n


def update_live_outcomes(conn, by_sym: dict, as_of: str) -> int:
    rows = conn.execute(
        "SELECT trade_date, symbol FROM med_forward_shadow_ledger WHERE ledger_mode='live' AND exit_status='open'"
    ).fetchall()
    u = 0
    for r in rows:
        sym, td = r["symbol"], r["trade_date"]
        bars = by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
        as_idx = next((i for i, b in enumerate(bars) if b["date"] == as_of), None)
        if idx is None or as_idx is None or as_idx <= idx:
            continue
        fwd = forward_metrics(bars, idx)
        closed = fwd.get("forward_return_20d") is not None and (as_idx - idx) >= 20
        conn.execute(
            """
            UPDATE med_forward_shadow_ledger
            SET forward_return_5d=?, forward_return_10d=?, forward_return_20d=?,
                mfe_20d=?, mae_20d=?,
                exit_status=CASE WHEN ? THEN 'closed' ELSE exit_status END
            WHERE trade_date=? AND symbol=? AND ledger_mode='live'
            """,
            (
                fwd.get("forward_return_5d"), fwd.get("forward_return_10d"),
                fwd.get("forward_return_20d"), fwd.get("mfe_20d"), fwd.get("mae_20d"),
                closed, td, sym,
            ),
        )
        u += 1
    conn.commit()
    return u


def graduation_stats(conn) -> dict:
    closed = conn.execute(
        """
        SELECT symbol, forward_return_20d FROM med_forward_shadow_ledger
        WHERE exit_status='closed' AND forward_return_20d IS NOT NULL AND ledger_mode='live'
        """
    ).fetchall()
    rets = [r["forward_return_20d"] / 100.0 for r in closed]
    syms = [r["symbol"] for r in closed]
    n = len(rets)
    return {
        "live_closed_trades": n,
        "live_pf_100": pf_from_returns(rets, 0.01) if rets else None,
        "live_median_return": median([x / 100.0 for x in rets]) if rets else None,
        "live_top10_dominance": top10_dominance(syms) if syms else 0,
        "graduation_met": n >= 40 and (pf_from_returns(rets, 0.01) or 0) >= 1.3
            and (median(rets) if rets else 0) > 0,
    }


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_med_tables(conn)
    by_sym, meta = load_bars(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else meta.get("max_date")

    oos_n = 0
    if params.get("backfill_oos", False):
        dates = sorted({
            b["date"] for bars in by_sym.values() for b in bars
            if OOS_START <= b["date"] <= OOS_END
        })
        lre_all = {d: load_lre_context(conn, d) for d in dates}
        mde_all = {d: load_mde_context(conn, d) for d in dates}
        conn.execute("DELETE FROM med_forward_shadow_ledger WHERE ledger_mode='research_oos'")
        oos_n = backfill_oos_research(conn, by_sym, lre_all, mde_all)

    live_n = 0
    skipped = False
    if trade_date and trade_date >= MED2_FORWARD_START:
        live_n = persist_live(conn, trade_date, by_sym)
        update_live_outcomes(conn, by_sym, trade_date)
    else:
        skipped = True

    research_closed = conn.execute(
        "SELECT COUNT(*) n FROM med_forward_shadow_ledger WHERE ledger_mode='research_oos' AND exit_status='closed'"
    ).fetchone()["n"]
    research_rets = [
        r[0] / 100.0 for r in conn.execute(
            "SELECT forward_return_20d FROM med_forward_shadow_ledger WHERE ledger_mode='research_oos' AND forward_return_20d IS NOT NULL"
        ).fetchall()
    ]

    payload = {
        "success": True,
        "trade_date": trade_date,
        "forward_start": MED2_FORWARD_START,
        "live_skipped": skipped,
        "oos_research_rows": oos_n,
        "oos_closed": research_closed,
        "oos_pf_100": pf_from_returns(research_rets, 0.01) if research_rets else None,
        "oos_median_return": median(research_rets) if research_rets else None,
        "new_live_entries": live_n,
        **graduation_stats(conn),
        "invariants": MED_INVARIANTS,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    conn.close()
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
