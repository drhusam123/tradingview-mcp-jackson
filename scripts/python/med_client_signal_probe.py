#!/usr/bin/env python3
"""
MED Phase 14 — client signal probe (MED_CLIENT_SIGNAL=1 shadow, no Telegram).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUTPUT = DATA / "med_client_signal_probe_last.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import connect, ensure_med_schema, ELIGIBLE_BUCKETS


def run(params: dict | None = None) -> dict:
    params = params or {}
    shadow = json.loads((DATA / "med_client_signal_shadow_last.json").read_text(encoding="utf-8")) if (DATA / "med_client_signal_shadow_last.json").exists() else {}
    client_on = os.environ.get("MED_CLIENT_SIGNAL", "0") == "1"
    validation_pass = bool(shadow.get("validation_pass"))

    conn = connect()
    ensure_med_schema(conn)
    trade_date = params.get("trade_date") or shadow.get("trade_date")
    symbols = []
    if trade_date:
        rows = conn.execute(
            """
            SELECT symbol, med_bucket, med_score FROM med_daily_scores
            WHERE trade_date=? AND med_bucket IN ({})
            ORDER BY med_score DESC LIMIT 5
            """.format(",".join("?" * len(ELIGIBLE_BUCKETS))),
            (trade_date, *ELIGIBLE_BUCKETS),
        ).fetchall()
        symbols = [dict(r) for r in rows]
    conn.close()

    probe_active = client_on or (validation_pass and os.environ.get("EGX_PHASE11_AUTO_PROMOTE") == "1")

    payload = {
        "success": True,
        "phase": "14_client_signal_probe",
        "trade_date": trade_date,
        "probe_active": probe_active,
        "MED_CLIENT_SIGNAL": os.environ.get("MED_CLIENT_SIGNAL", "0"),
        "shadow_validation_pass": validation_pass,
        "would_surface": symbols,
        "telegram_changed": False,
        "note": "Probe only — records MED high-conviction surface under MED_CLIENT_SIGNAL=1",
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(run(p), indent=2, default=str))
