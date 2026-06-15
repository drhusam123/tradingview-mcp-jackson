#!/usr/bin/env python3
"""MED-2 — Causal KNN analogue kernel on state vectors."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import (
    ANALOGUE_K, DATA, MIN_BARS, OOS_START, PRIMARY_H, PRIMARY_TH,
    connect, ensure_med_tables, forward_return, load_bars, load_lre_context, load_mde_context,
)
from med_0_1_math_features import compute_math_fields
from med_0_1_distribution_shift import distribution_shift


def state_vector(mf: dict, dist: dict) -> List[float]:
    return [
        mf.get("stored_energy", 0),
        mf.get("absorption_score", 0),
        dist.get("shift_score", 0),
        mf.get("volume_z_20", 0),
        mf.get("clv", 0.5),
        mf.get("crowding_penalty", 0),
        mf.get("physics_force", 0),
        mf.get("liquidity_fitness", 0),
        mf.get("r_5", 0),
        mf.get("r_20", 0),
    ]


def _dist(a: List[float], b: List[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) / max(len(a), 1)


def analogue_p_tail(
    vec: List[float],
    library: List[Tuple[List[float], float]],
    k: int = ANALOGUE_K,
) -> Tuple[float, int, float]:
    if not library:
        return 0.0, 0, 0.0
    scored = sorted((_dist(vec, lv), hit) for lv, hit in library)
    top = scored[:k]
    hits = [h for _, h in top]
    p = sum(hits) / len(hits) if hits else 0.0
    return p, len(top), min(1.0, len(top) / k)


def build_library(by_sym: dict, conn, upto_date: str, stride: int = 2) -> List[Tuple[List[float], float]]:
    from med_common import load_lre_all, load_mde_all

    lre_all = load_lre_all(conn, OOS_START, upto_date)
    mde_all = load_mde_all(conn, OOS_START, upto_date)
    lib: List[Tuple[List[float], float]] = []
    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS + PRIMARY_H:
            continue
        for idx in range(MIN_BARS, len(bars) - PRIMARY_H, stride):
            d = bars[idx]["date"]
            if d < OOS_START or d >= upto_date:
                continue
            mf = compute_math_fields(
                bars, idx, lre_all.get(d, {}).get(sym, {}), mde_all.get(d, {}).get(sym, {}),
            )
            if not mf:
                continue
            dist = distribution_shift(bars, idx, mf)
            mf["liquidity_fitness"] = min(1.0, (mf.get("liquidity_fitness_raw", 0) / 5e7))
            fr = forward_return(bars, idx, PRIMARY_H)
            hit = 1.0 if fr is not None and fr >= PRIMARY_TH else 0.0
            lib.append((state_vector(mf, dist), hit))
    return lib


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_med_tables(conn)
    by_sym, meta = load_bars(conn)

    trade_date = params.get("trade_date")
    if not trade_date:
        row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        trade_date = row["d"] if row and row["d"] else meta.get("max_date")

    vec_lib = build_library(by_sym, conn, trade_date)
    symbols = [
        r["symbol"] for r in conn.execute(
            "SELECT symbol FROM med_daily_scores WHERE trade_date=?", (trade_date,),
        ).fetchall()
    ]

    scored = []
    lre = load_lre_context(conn, trade_date)
    mde = load_mde_context(conn, trade_date)
    for sym in symbols:
        bars = by_sym.get(sym)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == trade_date), None)
        if idx is None or idx < MIN_BARS:
            continue
        mf = compute_math_fields(bars, idx, lre.get(sym, {}), mde.get(sym, {}))
        if not mf:
            continue
        dist = distribution_shift(bars, idx, mf)
        mf["liquidity_fitness"] = min(1.0, (mf.get("liquidity_fitness_raw", 0) / 5e7))
        p, n, conf = analogue_p_tail(state_vector(mf, dist), vec_lib, ANALOGUE_K)
        row_score = conn.execute(
            "SELECT med_score FROM med_daily_scores WHERE trade_date=? AND symbol=?",
            (trade_date, sym),
        ).fetchone()
        med_score = row_score["med_score"] if row_score else 0
        conn.execute(
            """
            INSERT OR REPLACE INTO med_analogue_scores_daily
            (trade_date, symbol, analogue_p_tail_20_10, analogue_neighbors,
             analogue_confidence, analogue_lift, client_path_allowed)
            VALUES (?,?,?,?,?,?,0)
            """,
            (trade_date, sym, p, n, conf, p * 100 - (med_score or 0) * 0.01),
        )
        scored.append({"symbol": sym, "p_tail": p, "neighbors": n, "confidence": conf})

    conn.commit()
    scored.sort(key=lambda x: x["p_tail"], reverse=True)
    out = {
        "success": True,
        "trade_date": trade_date,
        "library_size": len(vec_lib),
        "scored": len(scored),
        "top10": scored[:10],
    }
    (DATA / "med_analogue_scores_last.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8",
    )
    conn.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
