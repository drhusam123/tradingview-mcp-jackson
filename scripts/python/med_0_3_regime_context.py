#!/usr/bin/env python3
"""MED-0.3 — Real regime / breadth / sector context (causal joins only)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from med_common import DATA, clip, connect, sf

ROOT = DATA.parent
PARQUET_BREADTH = DATA / "parquet" / "market_breadth_enhanced.parquet"

_REGIME_LABELS = {
    "BULL": 0.75,
    "BULLISH": 0.70,
    "NEUTRAL": 0.50,
    "BEAR": 0.30,
    "BEARISH": 0.25,
}


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


@lru_cache(maxsize=1)
def _load_breadth_parquet() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not PARQUET_BREADTH.exists():
        return out
    try:
        import pyarrow.parquet as pq

        t = pq.read_table(PARQUET_BREADTH)
        for i in range(t.num_rows):
            d = t.column("date")[i].as_py()
            out[str(d)] = {
                "ad_ratio": sf(t.column("ad_ratio")[i].as_py(), 0.5),
                "pct_above_ema20": sf(t.column("pct_above_ema20")[i].as_py(), 0.5),
                "breadth_score": sf(t.column("breadth_score")[i].as_py(), 50) / 100.0,
                "signal": str(t.column("signal")[i].as_py() or ""),
            }
    except Exception:
        pass
    return out


def load_markov_by_date(conn) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not _table_exists(conn, "markov_regime_daily"):
        return out
    for r in conn.execute(
        "SELECT date, roll20_percentile, state_base, state_pct, base_confidence FROM markov_regime_daily"
    ):
        base = (r["state_base"] or r["state_pct"] or "NEUTRAL").upper()
        conf = (r["base_confidence"] or "medium").lower()
        conf_mult = {"strong": 1.0, "medium": 0.85, "weak": 0.7}.get(conf, 0.85)
        pct = clip(sf(r["roll20_percentile"], 50) / 100.0, 0, 1)
        out[r["date"]] = {
            "regime_label": base,
            "regime_state": _REGIME_LABELS.get(base, 0.5),
            "roll20_percentile": pct,
            "regime_fit": clip(_REGIME_LABELS.get(base, 0.5) * conf_mult * (0.5 + 0.5 * pct), 0.2, 0.95),
        }
    return out


def load_sector_rotation_by_date(conn) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not _table_exists(conn, "sector_rotation_daily"):
        return out
    for r in conn.execute(
        "SELECT date, leading_sector, rotation_score, sector_dispersion, top3_sectors FROM sector_rotation_daily"
    ):
        top3 = []
        try:
            top3 = json.loads(r["top3_sectors"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass
        out[r["date"]] = {
            "leading_sector": r["leading_sector"] or "",
            "rotation_score": clip(sf(r["rotation_score"], 50) / 100.0, 0, 1),
            "sector_dispersion": clip(sf(r["sector_dispersion"], 0) / 30.0, 0, 1),
            "top3_sectors": top3,
        }
    return out


def sector_strength(sector: str, rot: Optional[dict]) -> float:
    if not rot:
        return 0.5
    top3 = rot.get("top3_sectors") or []
    if sector and sector in top3:
        idx = top3.index(sector)
        return clip(0.85 - idx * 0.12, 0.55, 0.9)
    if sector and rot.get("leading_sector") == sector:
        return 0.8
    return clip(0.35 + rot.get("rotation_score", 0.5) * 0.3, 0.25, 0.65)


def breadth_state(trade_date: str, breadth: Dict[str, dict]) -> float:
    b = breadth.get(trade_date)
    if not b:
        return 0.5
    ad = clip(b.get("ad_ratio", 0.5), 0, 2) / 2.0
    ema = b.get("pct_above_ema20", 0.5)
    score = b.get("breadth_score", 0.5)
    return clip(0.35 * ad + 0.35 * ema + 0.30 * score, 0.15, 0.85)


def regime_context_for(
    trade_date: str,
    sector: str,
    markov: Dict[str, dict],
    rotation: Dict[str, dict],
    breadth: Dict[str, dict],
) -> dict:
    mk = markov.get(trade_date, {})
    rot = rotation.get(trade_date, {})
    br = breadth_state(trade_date, breadth)
    ss = sector_strength(sector, rot)
    rs = mk.get("regime_state", 0.5)
    rf = mk.get("regime_fit", 0.5)
    regime_fit = clip(0.45 * rf + 0.30 * br + 0.25 * ss, 0.2, 0.95)
    label = mk.get("regime_label", "NEUTRAL")
    return {
        "regime_label": label,
        "regime_state": rs,
        "breadth_state": br,
        "sector_strength": ss,
        "regime_fit": regime_fit,
    }


def load_all_regime_caches(conn=None) -> tuple:
    conn = conn or connect()
    return (
        load_markov_by_date(conn),
        load_sector_rotation_by_date(conn),
        _load_breadth_parquet(),
    )
