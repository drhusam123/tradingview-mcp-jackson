#!/usr/bin/env python3
"""
Regime-conditional parameter sweep — score atom pairs per market regime (BULL/SIDE/BEAR).
Outputs seeds for quant_discovery and discovery feedback.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

def bar_date(bar_time) -> str:
    if isinstance(bar_time, str):
        return bar_time[:10]
    try:
        ts = int(bar_time)
        if ts > 1_000_000_000_000:
            ts //= 1000
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(bar_time)[:10]
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
OUT_PATH = ROOT / "data" / "regime_conditional_sweep_last.json"

# TRADING_LESSONS sweet-spot atoms (priority per regime sweep)
PRIORITY_ATOMS = [
    "lower_third_close",
    "vol_2_5_3",
    "low20_retest",
    "not_near_ath",
    "bb_squeeze_low35",
    "range_lt4pct",
    "not_extended_3d",
]

ATOM_FNS = {
    "lower_third_close": lambda x: x["close_pos"] <= 0.33,
    "vol_2_5_3": lambda x: 2.5 <= x["vol_ratio"] <= 3.0,
    "low20_retest": lambda x: x["low20_retest"],
    "not_near_ath": lambda x: x["pct_from_ath"] > 0.03,
    "bb_squeeze_low35": lambda x: x["bb_width_pct"] <= 0.35,
    "range_lt4pct": lambda x: x["range_pct"] <= 0.04,
    "not_extended_3d": lambda x: x["ret3"] <= 0.12,
    "vol_lt1_5": lambda x: x["vol_ratio"] < 1.5,
    "upper_close": lambda x: x["close_pos"] > 0.66,
    "high20_break": lambda x: x["high20_break"],
}


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def compute_rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    ag, al = mean(gains), mean(losses)
    if al <= 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def load_regime_map(db):
    rows = db.execute(
        "SELECT date, state_base FROM markov_regime_daily WHERE state_base IS NOT NULL"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def load_examples(db, horizon=5):
    bars = db.execute(
        """
        SELECT symbol, bar_time, open, high, low, close, volume
        FROM ohlcv_history
        ORDER BY symbol, bar_time
        """
    ).fetchall()
    regime_map = load_regime_map(db)
    by_sym = defaultdict(list)
    for row in bars:
        by_sym[row[0]].append(row)

    examples = []
    for sym, rows in by_sym.items():
        if len(rows) < 60:
            continue
        closes = [r[5] for r in rows]
        vols = [r[6] or 0 for r in rows]
        for i in range(55, len(rows) - horizon):
            window = rows[max(0, i - 20) : i + 1]
            c = rows[i][5]
            h, l = rows[i][3], rows[i][4]
            date = bar_date(rows[i][1])
            regime = regime_map.get(date, "SIDE")
            rng = h - l if h > l else 1e-9
            close_pos = (c - l) / rng
            vol_avg = mean(vols[max(0, i - 20) : i]) or 1.0
            vol_ratio = (vols[i] or 0) / vol_avg
            ret3 = c / closes[i - 3] - 1 if i >= 3 else 0
            ret5 = c / closes[i - 5] - 1 if i >= 5 else 0
            high20 = max(r[3] for r in window)
            low20 = min(r[4] for r in window)
            ath = max(closes[: i + 1])
            fwd = closes[i + horizon] / c - 1
            examples.append({
                "date": date,
                "symbol": sym,
                "regime": regime,
                "close_pos": close_pos,
                "vol_ratio": vol_ratio,
                "ret3": ret3,
                "ret5": ret5,
                "low20_retest": abs(c - low20) / c <= 0.02,
                "high20_break": c >= high20 * 0.998,
                "pct_from_ath": (ath - c) / ath if ath else 0,
                "bb_width_pct": (high20 - low20) / c if c else 0,
                "range_pct": rng / c if c else 0,
                "hit": fwd >= 0.02,
                "realized": fwd * 100,
            })
    return examples


def score_pair(examples, a1, a2, min_n=40):
    f1, f2 = ATOM_FNS.get(a1), ATOM_FNS.get(a2)
    if not f1 or not f2:
        return None
    sub = [x for x in examples if f1(x) and f2(x)]
    if len(sub) < min_n:
        return None
    base = mean([1 if x["hit"] else 0 for x in examples])
    prec = mean([1 if x["hit"] else 0 for x in sub])
    lift = prec / base if base > 0 else 0
    return {
        "atoms": [a1, a2],
        "n": len(sub),
        "precision": round(prec * 100, 1),
        "lift": round(lift, 3),
        "avg_return": round(mean([x["realized"] for x in sub]), 2),
    }


def sweep_regime(examples, regime, min_n=40):
    pool = [x for x in examples if x["regime"] == regime]
    if len(pool) < 200:
        return {"regime": regime, "n_examples": len(pool), "top_pairs": [], "priority_atoms": []}
    atoms = list(ATOM_FNS.keys())
    scored = []
    for a1, a2 in combinations(atoms, 2):
        r = score_pair(pool, a1, a2, min_n=min_n)
        if r and r["lift"] >= 1.05 and r["precision"] >= 18:
            scored.append(r)
    scored.sort(key=lambda x: (x["lift"], x["precision"], x["n"]), reverse=True)
    top = scored[:12]
    regime_atoms = set()
    seed_pairs = []
    for t in top[:6]:
        seed_pairs.append(t["atoms"])
        regime_atoms.update(t["atoms"])
    for a in PRIORITY_ATOMS:
        regime_atoms.add(a)
    return {
        "regime": regime,
        "n_examples": len(pool),
        "top_pairs": top,
        "priority_atoms": sorted(regime_atoms),
        "seed_pairs": seed_pairs,
    }


def ensure_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS regime_sweep_results (
            id TEXT PRIMARY KEY,
            run_date TEXT NOT NULL,
            regime TEXT NOT NULL,
            atoms_json TEXT NOT NULL,
            n_samples INTEGER,
            precision REAL,
            lift REAL,
            avg_return REAL,
            created_at TEXT
        )
        """
    )


def run(params: dict | None = None):
    params = params or {}
    min_n = int(params.get("min_n", 40))
    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    ensure_table(db)
    examples = load_examples(db)
    if not examples:
        db.close()
        return {"success": False, "error": "no_examples"}

    regimes = sorted({x["regime"] for x in examples})
    by_regime = [sweep_regime(examples, r, min_n=min_n) for r in regimes]
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()

    all_seed_pairs = []
    all_priority = set(PRIORITY_ATOMS)
    for block in by_regime:
        all_seed_pairs.extend(block.get("seed_pairs") or [])
        all_priority.update(block.get("priority_atoms") or [])

    # dedupe seed pairs
    seen = set()
    deduped_pairs = []
    for p in all_seed_pairs:
        key = tuple(sorted(p))
        if key in seen:
            continue
        seen.add(key)
        deduped_pairs.append(list(key))

    for block in by_regime:
        for i, row in enumerate(block.get("top_pairs") or []):
            rid = f"rcs_{block['regime']}_{i}_{'-'.join(row['atoms'])}"
            db.execute(
                """
                INSERT OR REPLACE INTO regime_sweep_results
                (id, run_date, regime, atoms_json, n_samples, precision, lift, avg_return, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    run_date,
                    block["regime"],
                    json.dumps(row["atoms"]),
                    row["n"],
                    row["precision"],
                    row["lift"],
                    row["avg_return"],
                    now,
                ),
            )
    db.commit()
    db.close()

    payload = {
        "success": True,
        "at": now,
        "run_date": run_date,
        "n_examples": len(examples),
        "regimes": by_regime,
        "priority_atoms": sorted(all_priority),
        "seed_pairs": deduped_pairs[:24],
        "feedback_items": [
            {
                "type": "INVESTIGATE_PATTERN",
                "target": f"regime_{b['regime'].lower()}_sweep",
                "priority": 0.72,
                "rationale": f"Regime {b['regime']}: {len(b.get('top_pairs') or [])} pairs lift≥1.05",
            }
            for b in by_regime
            if b.get("top_pairs")
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = {}
    if len(sys.argv) > 1:
        try:
            p = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            p = {}
    print(json.dumps(run(p), ensure_ascii=False))
