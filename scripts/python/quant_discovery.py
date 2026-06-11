#!/usr/bin/env python3
"""
EGX Quant Discovery
===================
Walk-forward discovery of simple, explainable entry rules across the full
OHLCV history. It mines one-, two-, and focused three-condition rules, scores
them on OOS precision/lift/expectancy, and stores measurable candidates.

This is a discovery layer, not a client recommendation layer.
"""

import datetime as dt
import hashlib
import itertools
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"

try:
    from discovery_feedback_loader import (
        load_feedback_queue,
        adjust_rule_composite,
        apply_p6_research_hints,
        feedback_summary,
    )
except ImportError:
    load_feedback_queue = lambda: []
    adjust_rule_composite = lambda r, q=None: r
    apply_p6_research_hints = lambda candidates, _params: candidates

try:
    from discovery_quality_gate import filter_quant_candidates, score_discovery_run
except ImportError:
    filter_quant_candidates = lambda c, _p=None: (c, {"n_pass": len(c), "n_in": len(c)})
    score_discovery_run = lambda *a, **k: {"discovery_quality_score": 0, "grade": "?"}
    feedback_summary = lambda q=None: {"n_items": 0}


def safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def pct_rank(value, values):
    if not values:
        return 0.5
    return sum(1 for v in values if v <= value) / len(values)


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


def ensure_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS quant_discovery_rules (
        id                  TEXT PRIMARY KEY,
        run_date             TEXT NOT NULL,
        rule_name            TEXT NOT NULL,
        direction            TEXT DEFAULT 'UP',
        conditions_json       TEXT NOT NULL,
        n_train              INTEGER,
        n_oos                INTEGER,
        train_precision      REAL,
        oos_precision        REAL,
        oos_lift             REAL,
        oos_expectancy_pct   REAL,
        oos_avg_win_pct      REAL,
        oos_avg_loss_pct     REAL,
        oos_profit_factor    REAL,
        oos_hit_t1_rate      REAL,
        oos_stop_rate        REAL,
        baseline_precision   REAL,
        stability_score      REAL,
        composite_score      REAL,
        discovered_at        TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_quant_rules_run
      ON quant_discovery_rules(run_date, composite_score DESC);
    """)
    cols = {r[1] for r in db.execute("PRAGMA table_info(quant_discovery_rules)").fetchall()}
    if "quality_score" not in cols:
        db.execute("ALTER TABLE quant_discovery_rules ADD COLUMN quality_score REAL")
    db.commit()


def load_bars(db):
    rows = db.execute("""
        SELECT symbol, date(bar_time,'unixepoch') AS d, open, high, low, close, volume
        FROM ohlcv_history
        WHERE close IS NOT NULL AND close > 0
        ORDER BY symbol, bar_time
    """).fetchall()
    data = defaultdict(list)
    for r in rows:
        data[r["symbol"]].append({
            "symbol": r["symbol"],
            "date": r["d"],
            "open": safe(r["open"]),
            "high": safe(r["high"]),
            "low": safe(r["low"]),
            "close": safe(r["close"]),
            "volume": safe(r["volume"]),
        })
    return data


def build_examples(data, min_history=60, horizon=5):
    examples = []
    for symbol, bars in data.items():
        if len(bars) < min_history + horizon + 1:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["volume"] for b in bars]
        for i in range(min_history, len(bars) - horizon):
            b = bars[i]
            close = b["close"]
            if close <= 0:
                continue
            # Data integrity guard: unit/split errors create impossible 5d returns
            # and poison discovery. EGX daily limits make adjacent 45%+ jumps suspect.
            local_closes = closes[max(0, i - 5): i + horizon + 1]
            if len(local_closes) >= 2:
                jumps = [
                    abs(local_closes[j] / local_closes[j - 1] - 1.0)
                    for j in range(1, len(local_closes))
                    if local_closes[j - 1] > 0
                ]
                if any(j > 0.45 for j in jumps):
                    continue
            prior_closes = closes[: i + 1]
            prior_highs = highs[: i + 1]
            prior_vols = [v for v in vols[max(0, i - 20): i] if v > 0]
            avg_vol20 = mean(prior_vols) if prior_vols else 0.0
            vol_ratio = b["volume"] / avg_vol20 if avg_vol20 > 0 else 1.0
            ret1 = close / closes[i - 1] - 1 if i >= 1 and closes[i - 1] else 0.0
            ret3 = close / closes[i - 3] - 1 if i >= 3 and closes[i - 3] else 0.0
            ret5 = close / closes[i - 5] - 1 if i >= 5 and closes[i - 5] else 0.0
            ret20 = close / closes[i - 20] - 1 if i >= 20 and closes[i - 20] else 0.0
            rsi = compute_rsi(prior_closes[-80:], 14)
            hi20 = max(prior_highs[-20:])
            lo20 = min(lows[max(0, i - 19): i + 1])
            rng = max(b["high"] - b["low"], 0.0)
            close_pos = (close - b["low"]) / rng if rng > 0 else 0.5
            ath300 = max(prior_highs[-300:])
            pct_from_ath = (ath300 - close) / ath300 if ath300 > 0 else 1.0
            bbw = (4 * stdev(prior_closes[-20:]) / mean(prior_closes[-20:])) if len(prior_closes) >= 20 and mean(prior_closes[-20:]) > 0 else 0.0
            bb_hist = []
            for j in range(max(20, i - 80), i + 1):
                seg = closes[j - 19:j + 1]
                m = mean(seg)
                if m > 0:
                    bb_hist.append(4 * stdev(seg) / m)
            bb_pct = pct_rank(bbw, bb_hist)
            ema20 = mean(prior_closes[-20:])
            ema50 = mean(prior_closes[-50:])

            future = bars[i + 1:i + 1 + horizon]
            fwd_max = max(x["high"] for x in future) / close - 1
            fwd_min = min(x["low"] for x in future) / close - 1
            fwd_close = future[-1]["close"] / close - 1
            if abs(fwd_close) > 0.45 or fwd_max > 0.60 or fwd_min < -0.45:
                continue
            hit_t1 = fwd_max >= 0.05
            stopped = fwd_min <= -0.035
            # Conservative expectancy proxy: win takes fwd_close capped by max,
            # stop takes -3.5% if hit before any meaningful close progress.
            realized = -0.035 if stopped and not hit_t1 else max(-0.08, min(0.12, fwd_close))

            examples.append({
                "symbol": symbol,
                "date": b["date"],
                "rsi": rsi,
                "vol_ratio": vol_ratio,
                "ret1": ret1,
                "ret3": ret3,
                "ret5": ret5,
                "ret20": ret20,
                "close_pos": close_pos,
                "range_pct": rng / close if close > 0 else 0.0,
                "pct_from_ath": pct_from_ath,
                "bb_width": bbw,
                "bb_width_pct": bb_pct,
                "above_ema20": close > ema20,
                "above_ema50": close > ema50,
                "high20_break": close >= hi20 * 0.995,
                "low20_retest": close <= lo20 * 1.08,
                "fwd_close": fwd_close,
                "fwd_max": fwd_max,
                "fwd_min": fwd_min,
                "hit": hit_t1,
                "stopped": stopped,
                "realized": realized,
            })
    return examples


def load_counterfactual_seeds(params: dict | None = None) -> dict:
    params = params or {}
    if params.get("counterfactual_atoms"):
        return params["counterfactual_atoms"]
    path = ROOT / "data" / "counterfactual_atoms_last.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def atoms():
    return [
        ("rsi_35_55", lambda x: 35 <= x["rsi"] <= 55),
        ("rsi_45_65", lambda x: 45 <= x["rsi"] <= 65),
        ("rsi_40_70", lambda x: 40 <= x["rsi"] <= 70),
        ("rsi_50_75", lambda x: 50 <= x["rsi"] <= 75),
        ("rsi_lt45", lambda x: x["rsi"] < 45),
        ("rsi_gt55_lt75", lambda x: 55 < x["rsi"] < 75),
        ("rsi_not_hot", lambda x: x["rsi"] <= 72),
        ("vol_1_5_3", lambda x: 1.5 <= x["vol_ratio"] <= 3.0),
        ("vol_1_8_3", lambda x: 1.8 <= x["vol_ratio"] <= 3.0),
        ("vol_2_4", lambda x: 2.0 <= x["vol_ratio"] <= 4.0),
        ("vol_2_5_3", lambda x: 2.5 <= x["vol_ratio"] <= 3.0),
        ("vol_lt1_5", lambda x: x["vol_ratio"] < 1.5),
        ("vol_gt3", lambda x: x["vol_ratio"] > 3.0),
        ("vol_3_8", lambda x: 3.0 < x["vol_ratio"] <= 8.0),
        ("vol_gt5", lambda x: x["vol_ratio"] > 5.0),
        ("lower_third_close", lambda x: x["close_pos"] <= 0.33),
        ("middle_close", lambda x: 0.33 < x["close_pos"] <= 0.66),
        ("upper_close", lambda x: x["close_pos"] > 0.66),
        ("very_upper_close", lambda x: x["close_pos"] >= 0.82),
        ("bb_squeeze_low20", lambda x: x["bb_width_pct"] <= 0.20),
        ("bb_squeeze_low35", lambda x: x["bb_width_pct"] <= 0.35),
        ("bb_expanding", lambda x: x["bb_width_pct"] >= 0.70),
        ("ret1_pos", lambda x: x["ret1"] > 0),
        ("ret1_flat_pos", lambda x: 0 <= x["ret1"] <= 0.05),
        ("ret1_not_gap_chase", lambda x: x["ret1"] <= 0.08),
        ("mom3_pos", lambda x: x["ret3"] > 0),
        ("mom3_2_12", lambda x: 0.02 <= x["ret3"] <= 0.12),
        ("mom3_soft_pullback", lambda x: -0.05 <= x["ret3"] <= 0.0),
        ("mom5_pos_lt15", lambda x: 0 < x["ret5"] <= 0.15),
        ("mom5_2_18", lambda x: 0.02 <= x["ret5"] <= 0.18),
        ("mom20_pos", lambda x: x["ret20"] > 0),
        ("mom20_5_35", lambda x: 0.05 <= x["ret20"] <= 0.35),
        ("not_extended_3d", lambda x: x["ret3"] <= 0.12),
        ("not_extended_5d", lambda x: x["ret5"] <= 0.18),
        ("near_ath_300", lambda x: x["pct_from_ath"] <= 0.03),
        ("not_near_ath", lambda x: x["pct_from_ath"] > 0.03),
        ("far_from_ath_10", lambda x: x["pct_from_ath"] >= 0.10),
        ("above_ema20", lambda x: x["above_ema20"]),
        ("above_ema50", lambda x: x["above_ema50"]),
        ("high20_break", lambda x: x["high20_break"]),
        ("low20_retest", lambda x: x["low20_retest"]),
        ("range_lt4pct", lambda x: x["range_pct"] <= 0.04),
        ("range_4_9pct", lambda x: 0.04 < x["range_pct"] <= 0.09),
        ("range_gt9pct", lambda x: x["range_pct"] > 0.09),
    ]


def score_rule(name, cond_names, selected, baseline, split_date, min_oos=35):
    canonical_conditions = sorted(cond_names)
    canonical_name = " + ".join(canonical_conditions)
    train = [x for x in selected if x["date"] < split_date]
    oos = [x for x in selected if x["date"] >= split_date]
    if len(train) < 80 or len(oos) < min_oos:
        return None

    def metrics(rows):
        hits = [x for x in rows if x["hit"]]
        losses = [x for x in rows if not x["hit"]]
        wins = [x["realized"] for x in rows if x["realized"] > 0]
        negs = [-x["realized"] for x in rows if x["realized"] < 0]
        gp = sum(wins)
        gl = sum(negs)
        return {
            "n": len(rows),
            "precision": len(hits) / len(rows) if rows else 0.0,
            "expectancy": mean([x["realized"] for x in rows]) if rows else 0.0,
            "avg_win": mean(wins),
            "avg_loss": -mean(negs) if negs else 0.0,
            "pf": gp / gl if gl > 0 else (9.99 if gp > 0 else 0.0),
            "hit_t1": len(hits) / len(rows) if rows else 0.0,
            "stop": sum(1 for x in rows if x["stopped"]) / len(rows) if rows else 0.0,
        }

    mt, mo = metrics(train), metrics(oos)
    if mo["precision"] <= baseline["precision"] or mo["expectancy"] <= baseline["expectancy"]:
        return None
    lift = mo["precision"] / baseline["precision"] if baseline["precision"] > 0 else 0.0
    stability = max(0.0, 1.0 - abs(mt["precision"] - mo["precision"]) / max(mt["precision"], 0.01))
    composite = (
        40.0 * min(lift, 3.0) / 3.0
        + 30.0 * min(max(mo["expectancy"] * 100.0, 0.0), 4.0) / 4.0
        + 20.0 * stability
        + 10.0 * min(math.log10(max(mo["n"], 1)) / 3.0, 1.0)
    )
    stable_id = hashlib.sha1("|".join(canonical_conditions).encode("utf-8")).hexdigest()[:10].upper()
    return {
        "id": "QD_" + stable_id,
        "rule_name": canonical_name or name,
        "conditions": canonical_conditions,
        "n_train": mt["n"],
        "n_oos": mo["n"],
        "train_precision": mt["precision"],
        "oos_precision": mo["precision"],
        "oos_lift": lift,
        "oos_expectancy_pct": mo["expectancy"] * 100.0,
        "oos_avg_win_pct": mo["avg_win"] * 100.0,
        "oos_avg_loss_pct": mo["avg_loss"] * 100.0,
        "oos_profit_factor": mo["pf"],
        "oos_hit_t1_rate": mo["hit_t1"],
        "oos_stop_rate": mo["stop"],
        "baseline_precision": baseline["precision"],
        "stability_score": stability,
        "composite_score": composite,
    }


def run_discovery(params):
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    ensure_tables(db)

    horizon = int(params.get("horizon", 5))
    max_pairs = int(params.get("max_pairs", 1600))
    max_triples = int(params.get("max_triples", 4500))
    min_oos = int(params.get("min_oos", 80))
    data = load_bars(db)
    examples = build_examples(data, horizon=horizon)
    if not examples:
        return {"success": False, "error": "no examples"}

    dates = sorted({x["date"] for x in examples})
    split_date = params.get("split_date") or dates[int(len(dates) * 0.75)]
    oos_all = [x for x in examples if x["date"] >= split_date]
    baseline = {
        "n": len(oos_all),
        "precision": sum(1 for x in oos_all if x["hit"]) / len(oos_all),
        "expectancy": mean([x["realized"] for x in oos_all]),
    }

    cf_seeds = load_counterfactual_seeds(params)
    penalize_atoms = set(cf_seeds.get("penalize_atoms") or [])
    priority_atoms = list(cf_seeds.get("priority_atoms") or cf_seeds.get("boost_atoms") or [])
    seed_pairs = list(cf_seeds.get("seed_pairs") or [])

    atom_defs = atoms()
    atom_map = {name: fn for name, fn in atom_defs}
    selected_by_atom = {
        name: {idx for idx, x in enumerate(examples) if fn(x)}
        for name, fn in atom_defs
    }
    candidates = []

    def _score_single(name: str):
        if name not in atom_map:
            return
        selected = [examples[i] for i in selected_by_atom[name]]
        r = score_rule(name, [name], selected, baseline, split_date, min_oos=min_oos)
        if r:
            candidates.append(r)

    seen_single = set()
    for name in priority_atoms:
        if name not in seen_single:
            _score_single(name)
            seen_single.add(name)
    for name, _fn in atom_defs:
        if name not in seen_single:
            _score_single(name)

    pair_pool = []
    seen_pairs = set()

    def _score_pair(n1: str, n2: str):
        key = tuple(sorted((n1, n2)))
        if key in seen_pairs or n1 not in atom_map or n2 not in atom_map:
            return
        seen_pairs.add(key)
        idxs = selected_by_atom[n1] & selected_by_atom[n2]
        if len(idxs) < min_oos:
            return
        selected = [examples[i] for i in idxs]
        r = score_rule(f"{n1} + {n2}", [n1, n2], selected, baseline, split_date, min_oos=min_oos)
        if r:
            candidates.append(r)
            pair_pool.append((r, n1, n2, idxs))

    for pair in seed_pairs:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            _score_pair(pair[0], pair[1])

    pair_defs = list(itertools.combinations(atom_defs, 2))[:max_pairs]
    for (n1, _f1), (n2, _f2) in pair_defs:
        if n1 == n2:
            continue
        _score_pair(n1, n2)

    pair_pool.sort(
        key=lambda p: (
            p[0]["oos_precision"],
            p[0]["oos_expectancy_pct"],
            math.log10(max(p[0]["n_oos"], 1)),
        ),
        reverse=True,
    )
    triples_tested = 0
    for _pair_rule, n1, n2, pair_idxs in pair_pool[:160]:
        used = {n1, n2}
        for n3, _f3 in atom_defs:
            if n3 in used:
                continue
            idxs = pair_idxs & selected_by_atom[n3]
            if len(idxs) < min_oos:
                continue
            conds = [n1, n2, n3]
            selected = [examples[i] for i in idxs]
            r = score_rule(" + ".join(conds), conds, selected, baseline, split_date, min_oos=min_oos)
            triples_tested += 1
            if r:
                candidates.append(r)
            if triples_tested >= max_triples:
                break
        if triples_tested >= max_triples:
            break

    by_id = {}
    for r in candidates:
        existing = by_id.get(r["id"])
        if not existing or r["composite_score"] > existing["composite_score"]:
            by_id[r["id"]] = r
    candidates = list(by_id.values())

    feedback_queue = params.get("feedback_queue") or load_feedback_queue()
    if feedback_queue:
        for r in candidates:
            adjust_rule_composite(r, feedback_queue)

    candidates = apply_p6_research_hints(candidates, params)

    priority_set = set(priority_atoms)
    for r in candidates:
        conds = set(r.get("conditions") or [])
        if conds & penalize_atoms:
            r["composite_score"] = round(float(r["composite_score"]) * 0.72, 4)
            r["counterfactual_penalized"] = True
        elif priority_set and conds & priority_set:
            r["composite_score"] = round(float(r["composite_score"]) * 1.06, 4)
            r["counterfactual_boosted"] = True

    quality_params = dict(params)
    if params.get("p6_gate", {}).get("gate_pass") is False:
        quality_params["strict_quality"] = True
    candidates, quality_summary = filter_quant_candidates(candidates, quality_params)

    composite_top = sorted(
        candidates,
        key=lambda x: (x["composite_score"], x["oos_expectancy_pct"], x["oos_lift"]),
        reverse=True,
    )[: int(params.get("keep", 120))]
    precision_top = sorted(
        candidates,
        key=lambda x: (x["oos_precision"], x["oos_expectancy_pct"], x["n_oos"]),
        reverse=True,
    )[: int(params.get("keep_precision", 80))]
    top_by_id = {r["id"]: r for r in composite_top}
    top_by_id.update({r["id"]: r for r in precision_top})
    top = sorted(
        top_by_id.values(),
        key=lambda x: (x["composite_score"], x["oos_precision"], x["oos_expectancy_pct"]),
        reverse=True,
    )
    best_precision = max(top, key=lambda r: r["oos_precision"]) if top else None
    run_date = dt.date.today().isoformat()
    db.execute("DELETE FROM quant_discovery_rules WHERE run_date=?", (run_date,))
    for r in top:
        db.execute("""
            INSERT OR REPLACE INTO quant_discovery_rules
            (id, run_date, rule_name, direction, conditions_json, n_train, n_oos,
             train_precision, oos_precision, oos_lift, oos_expectancy_pct,
             oos_avg_win_pct, oos_avg_loss_pct, oos_profit_factor,
             oos_hit_t1_rate, oos_stop_rate, baseline_precision,
             stability_score, composite_score, quality_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r["id"], run_date, r["rule_name"], "UP", json.dumps(r["conditions"], ensure_ascii=False),
            r["n_train"], r["n_oos"], r["train_precision"], r["oos_precision"],
            r["oos_lift"], r["oos_expectancy_pct"], r["oos_avg_win_pct"],
            r["oos_avg_loss_pct"], r["oos_profit_factor"], r["oos_hit_t1_rate"],
            r["oos_stop_rate"], r["baseline_precision"], r["stability_score"],
            r["composite_score"], r.get("quality_score"),
        ))
    db.commit()
    db.close()
    fb = feedback_summary(feedback_queue)
    quality_summary["rules_kept"] = len(top)
    quality_summary["avg_quality"] = quality_summary.get("avg_quality_pass", 0)
    discovery_quality = score_discovery_run(quality_summary)
    return {
        "success": True,
        "feedback_applied": fb,
        "quality_gate": quality_summary,
        "discovery_quality": discovery_quality,
        "n_examples": len(examples),
        "n_symbols": len(data),
        "split_date": split_date,
        "baseline_precision": round(baseline["precision"], 4),
        "baseline_expectancy_pct": round(baseline["expectancy"] * 100.0, 4),
        "rules_tested": len(atom_defs) + len(pair_defs) + triples_tested,
        "rules_kept": len(top),
        "triples_tested": triples_tested,
        "best_precision_rule": None if not best_precision else {
            "rule": best_precision["rule_name"],
            "n_oos": best_precision["n_oos"],
            "precision": round(best_precision["oos_precision"], 4),
            "lift": round(best_precision["oos_lift"], 3),
            "expectancy_pct": round(best_precision["oos_expectancy_pct"], 3),
            "pf": round(best_precision["oos_profit_factor"], 3),
            "score": round(best_precision["composite_score"], 2),
        },
        "top_rules": [
            {
                "rule": r["rule_name"],
                "n_oos": r["n_oos"],
                "precision": round(r["oos_precision"], 4),
                "lift": round(r["oos_lift"], 3),
                "expectancy_pct": round(r["oos_expectancy_pct"], 3),
                "pf": round(r["oos_profit_factor"], 3),
                "score": round(r["composite_score"], 2),
                "quality": r.get("quality_score"),
            }
            for r in top[:12]
        ],
    }


def status():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    ensure_tables(db)
    rows = db.execute("""
        SELECT run_date, COUNT(*) n, MAX(composite_score) best_score,
               AVG(oos_precision) avg_precision, AVG(oos_lift) avg_lift
        FROM quant_discovery_rules
        GROUP BY run_date ORDER BY run_date DESC LIMIT 5
    """).fetchall()
    top = db.execute("""
        SELECT rule_name, n_oos, oos_precision, oos_lift, oos_expectancy_pct,
               oos_profit_factor, composite_score
        FROM quant_discovery_rules
        WHERE run_date=(SELECT MAX(run_date) FROM quant_discovery_rules)
        ORDER BY composite_score DESC LIMIT 10
    """).fetchall()
    db.close()
    return {
        "success": True,
        "runs": [dict(r) for r in rows],
        "top": [dict(r) for r in top],
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    params = {}
    if len(sys.argv) > 2:
        try:
            params = json.loads(sys.argv[2])
        except Exception:
            params = {}
    if cmd in ("run", "discover"):
        result = run_discovery(params)
    elif cmd == "status":
        result = status()
    else:
        result = {"success": False, "error": f"unknown command {cmd}", "available": ["run", "status"]}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
