#!/usr/bin/env python3
"""MED-2 — Threshold snapshots from persisted MED scores (fast)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import DATA, connect, ensure_med_tables


def _pct(vals: list, q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = int(q * (len(s) - 1))
    return s[min(i, len(s) - 1)]


def run(params: dict | None = None) -> dict:
    params = params or {}
    conn = connect()
    ensure_med_tables(conn)
    asof = params.get("trade_date")
    if not asof:
        row = conn.execute("SELECT MAX(trade_date) d FROM med_daily_scores").fetchone()
        asof = row["d"] if row else None

    scores = [r[0] for r in conn.execute(
        "SELECT med_score FROM med_daily_scores WHERE trade_date=?", (asof,),
    ).fetchall() if r[0] is not None]
    energy = [r[0] for r in conn.execute(
        "SELECT stored_energy FROM med_daily_scores WHERE trade_date=?", (asof,),
    ).fetchall() if r[0] is not None]
    analogue = [r[0] for r in conn.execute(
        "SELECT analogue_p_tail_20_10 FROM med_analogue_scores_daily WHERE trade_date=?", (asof,),
    ).fetchall() if r[0] is not None]

    written = []
    for name, vals in (("med_score", scores), ("stored_energy", energy), ("analogue_p_tail", analogue)):
        if not vals:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO med_threshold_snapshots
            (asof_date, metric, p50, p75, p90, window_mode)
            VALUES (?,?,?,?,?,'expanding')
            """,
            (asof, name, _pct(vals, 0.5), _pct(vals, 0.75), _pct(vals, 0.9)),
        )
        written.append(name)
    conn.commit()
    out = {"success": True, "asof_date": asof, "metrics": written}
    (DATA / "med_threshold_snapshots_last.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    conn.close()
    return out


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2))
