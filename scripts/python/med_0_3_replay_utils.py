#!/usr/bin/env python3
"""MED-0.3 — shared replay/backfill helpers for se_rank + regime."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Tuple

from med_common import MIN_BARS, OOS_END, OOS_START, load_lre_all, load_mde_all
from med_0_1_math_features import compute_math_fields
from med_0_3_calibration import apply_cross_section_ranks
from med_0_3_regime_context import load_all_regime_caches, regime_context_for


def precompute_mf_by_day(
    by_sym: dict,
    conn,
    oos_start: str = OOS_START,
    oos_end: str = OOS_END,
) -> Dict[Tuple[str, str], dict]:
    """Return (trade_date, symbol) -> math fields with se_rank."""
    markov, rotation, breadth = load_all_regime_caches(conn)
    lre_all = load_lre_all(conn, oos_start, oos_end)
    mde_all = load_mde_all(conn, oos_start, oos_end)
    daily: Dict[str, list] = defaultdict(list)
    index: Dict[Tuple[str, str], dict] = {}

    from med_0_1_math_features import compute_math_fields as cmf
    for sym, bars in by_sym.items():
        if len(bars) < MIN_BARS:
            continue
        for idx in range(MIN_BARS, len(bars)):
            d = bars[idx]["date"]
            if d < oos_start or d > oos_end:
                continue
            lre = lre_all.get(d, {}).get(sym, {})
            mde = mde_all.get(d, {}).get(sym, {})
            rc = regime_context_for(d, "Unknown", markov, rotation, breadth)
            mf = cmf(bars, idx, lre, mde, rc)
            if not mf:
                continue
            row = {"symbol": sym, **mf}
            daily[d].append(row)

    for d, rows in daily.items():
        apply_cross_section_ranks(rows)
        for r in rows:
            index[(d, r["symbol"])] = r
    return index
