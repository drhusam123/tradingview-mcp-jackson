#!/usr/bin/env python3
"""
egx_model_lift.py — THE decisive edge test.

Question: does the trained explosion ML ensemble actually IMPROVE on the naive
breakout baseline? We score each clean breakout trade with the ensemble and ask:
do the HIGH-probability trades win MORE than the 16.9% baseline win rate?

Method:
  1. Load /tmp/egx_clean_trades.json (real forward returns of breakout entries).
  2. Build OHLCV + EGX30 caches ONCE from the DB.
  3. For each trade, build the look-ahead-safe feature row at entry_date via
     explosion_ml._build_feature_row, then score with the SAME weighted-average
     ensemble production uses (the smart-fallback blend, since the meta-model
     collapses):  0.40*lgbm + 0.25*xgb + 0.20*rf + 0.15*et
     Feature vector is truncated to len(explosion_features_v3.json).
  4. Edge analysis: decile table, top vs bottom, lift, prob thresholds,
     rank correlation, and an honest verdict.

Run:  python3 egx_model_lift.py
"""
import os
import sys
import json
import sqlite3
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TRADES_JSON = "/tmp/egx_clean_trades.json"
REPORT_JSON = "/tmp/egx_model_lift_report.json"
DB_PATH = "/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db"
MODELS = HERE / "models" / "ml_trainer"
WIN_THRESHOLD = 0.07  # net_return_pct >= 0.07 => win (matches clean-trades meta)

# Ensemble weights — the production "smart-fallback" weighted average
# (egx_ml_trainer.cmd_predict_ensemble, lines ~5100). The meta-model collapses,
# so production falls back to exactly this blend.
W_LGBM, W_XGB, W_RF, W_ET = 0.40, 0.25, 0.20, 0.15


def log(msg):
    print(msg, flush=True)


def load_models():
    """Load the four base models. Returns (models_dict, loaded_report)."""
    import lightgbm as lgb
    import xgboost as xgb
    import joblib

    models = {}
    loaded = {}

    lgbm_path = MODELS / "explosion_lgbm_v3.txt"
    xgb_path = MODELS / "explosion_xgb_v1.json"
    rf_path = MODELS / "explosion_rf_v1.pkl"
    et_path = MODELS / "explosion_et_v1.pkl"

    try:
        models["lgbm"] = lgb.Booster(model_file=str(lgbm_path))
        loaded["lgbm"] = True
    except Exception as e:
        loaded["lgbm"] = f"FAIL: {e}"

    try:
        m = xgb.Booster()
        m.load_model(str(xgb_path))
        models["xgb"] = m
        models["_xgb_mod"] = xgb
        loaded["xgb"] = True
    except Exception as e:
        loaded["xgb"] = f"FAIL: {e}"

    try:
        models["rf"] = joblib.load(str(rf_path))
        loaded["rf"] = True
    except Exception as e:
        loaded["rf"] = f"FAIL: {e}"

    try:
        models["et"] = joblib.load(str(et_path))
        loaded["et"] = True
    except Exception as e:
        loaded["et"] = f"FAIL: {e}"

    return models, loaded


def score_one(models, X):
    """Weighted-average ensemble probability for a single (1, n_feat) array X.

    Uses only the models that loaded. Weights of missing models are dropped and
    the remaining weights renormalised so we still get a sensible probability.
    Returns None if no model is available.
    """
    parts = []
    if "lgbm" in models:
        parts.append((W_LGBM, float(models["lgbm"].predict(X)[0])))
    if "xgb" in models:
        xgb = models["_xgb_mod"]
        parts.append((W_XGB, float(models["xgb"].predict(xgb.DMatrix(X))[0])))
    if "rf" in models:
        parts.append((W_RF, float(models["rf"].predict_proba(X)[0, 1])))
    if "et" in models:
        parts.append((W_ET, float(models["et"].predict_proba(X)[0, 1])))
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return sum(w * p for w, p in parts) / wsum


def main():
    if not os.path.exists(TRADES_JSON):
        log(f"ERROR: trades file not found: {TRADES_JSON}")
        sys.exit(1)

    with open(TRADES_JSON) as f:
        data = json.load(f)
    trades = data["trades"]
    meta = data.get("meta", {})
    log(f"[load] {len(trades)} trades from {TRADES_JSON}")
    log(f"[load] baseline meta: win_rate={meta.get('win_rate')} "
        f"avg_net_return={meta.get('avg_net_return')}")

    # ── Models ────────────────────────────────────────────────────────────
    models, loaded = load_models()
    log(f"[models] loaded: {json.dumps(loaded)}")
    if not any(k in models for k in ("lgbm", "xgb", "rf", "et")):
        log("ERROR: no base models loaded — cannot score.")
        sys.exit(1)

    # ── Feature list / count ──────────────────────────────────────────────
    from explosion_ml import (_build_feature_row, _build_ohlcv_cache,
                              _load_egx30_cache, FEATURE_COLS)
    feat_path = MODELS / "explosion_features_v3.json"
    FEAT = json.loads(feat_path.read_text()) if feat_path.exists() else list(FEATURE_COLS)
    n_feat = len(FEAT)
    log(f"[features] expected feature count = {n_feat}")

    # ── Build caches ONCE ─────────────────────────────────────────────────
    max_date = max(t["entry_date"] for t in trades)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    log(f"[cache] building OHLCV cache up to {max_date} ...")
    cache = _build_ohlcv_cache(conn, max_date)
    egx30 = _load_egx30_cache(conn)
    log(f"[cache] OHLCV symbols={len(cache)}  EGX30 dates={len(egx30)}")

    # ── Score every trade ─────────────────────────────────────────────────
    scored = []        # list of (net_return, ml_prob, is_win)
    n_skip_nodf = 0    # no symbol dataframe
    n_skip_norow = 0   # feature row could not be built
    n_skip_score = 0   # scoring failed
    n_done = 0

    for t in trades:
        n_done += 1
        if n_done % 500 == 0:
            log(f"[score] {n_done}/{len(trades)}  scored={len(scored)} "
                f"skip(nodf={n_skip_nodf}, norow={n_skip_norow}, score={n_skip_score})")

        sym = t["symbol"]
        date_str = t["entry_date"]
        sym_df = cache.get(sym)
        if sym_df is None:
            n_skip_nodf += 1
            continue

        row = _build_feature_row(sym_df, date_str, egx30=egx30)
        if row is None:
            n_skip_norow += 1
            continue

        X = np.array([list(row)[:n_feat]], dtype=np.float32)
        try:
            prob = score_one(models, X)
        except Exception:
            prob = None
        if prob is None:
            n_skip_score += 1
            continue

        net = float(t["net_return_pct"])
        scored.append((net, float(prob), 1 if net >= WIN_THRESHOLD else 0))

    conn.close()
    n_scored = len(scored)
    n_skipped = n_skip_nodf + n_skip_norow + n_skip_score
    log(f"[score] DONE. scored={n_scored}  skipped={n_skipped} "
        f"(nodf={n_skip_nodf}, norow={n_skip_norow}, score={n_skip_score})")

    if n_scored < 50:
        log("ERROR: too few trades scored for a meaningful edge test.")
        sys.exit(1)

    nets = np.array([s[0] for s in scored], dtype=float)
    probs = np.array([s[1] for s in scored], dtype=float)
    wins = np.array([s[2] for s in scored], dtype=float)

    # ── Baseline (all scored trades) ──────────────────────────────────────
    base_wr = float(wins.mean())
    base_ret = float(nets.mean())

    # ── Decile table (by ML probability) ──────────────────────────────────
    order = np.argsort(probs)              # ascending
    deciles = np.array_split(order, 10)    # decile 1 = lowest prob ... 10 = highest
    decile_rows = []
    for i, idx in enumerate(deciles, start=1):
        if len(idx) == 0:
            continue
        d_wr = float(wins[idx].mean())
        d_ret = float(nets[idx].mean())
        decile_rows.append({
            "decile": i,
            "n": int(len(idx)),
            "prob_min": float(probs[idx].min()),
            "prob_max": float(probs[idx].max()),
            "prob_mean": float(probs[idx].mean()),
            "win_rate": d_wr,
            "avg_net_return": d_ret,
        })

    top = decile_rows[-1]
    bottom = decile_rows[0]
    lift = (top["win_rate"] / base_wr) if base_wr > 0 else float("nan")

    # ── Probability thresholds ────────────────────────────────────────────
    threshold_rows = []
    for thr in (0.5, 0.6, 0.7, 0.8):
        mask = probs >= thr
        n = int(mask.sum())
        if n == 0:
            threshold_rows.append({"threshold": thr, "n": 0,
                                   "win_rate": None, "avg_net_return": None})
        else:
            threshold_rows.append({
                "threshold": thr,
                "n": n,
                "win_rate": float(wins[mask].mean()),
                "avg_net_return": float(nets[mask].mean()),
            })

    # ── Rank (Spearman) correlation: ml_prob vs net_return ────────────────
    def _rankdata(a):
        # average ranks, ties handled
        order_a = np.argsort(a, kind="mergesort")
        ranks = np.empty(len(a), dtype=float)
        ranks[order_a] = np.arange(len(a), dtype=float)
        # resolve ties to average rank
        sorted_a = a[order_a]
        i = 0
        while i < len(a):
            j = i
            while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
                j += 1
            if j > i:
                avg = (i + j) / 2.0
                ranks[order_a[i:j + 1]] = avg
            i = j + 1
        return ranks

    rp = _rankdata(probs)
    rn = _rankdata(nets)
    if rp.std() > 0 and rn.std() > 0:
        spearman = float(np.corrcoef(rp, rn)[0, 1])
    else:
        spearman = float("nan")

    # ── Verdict ───────────────────────────────────────────────────────────
    top_clearly_positive = top["avg_net_return"] > base_ret and top["avg_net_return"] > 0
    if base_wr > 0 and lift > 1.5 and top_clearly_positive:
        verdict = "EDGE CONFIRMED"
    elif base_wr > 0 and lift < 1.15:
        verdict = "NO EDGE — model adds nothing over breakout"
    else:
        verdict = "WEAK EDGE"

    # ── Print report ──────────────────────────────────────────────────────
    log("")
    log("=" * 72)
    log("EGX MODEL LIFT — DECISIVE EDGE TEST")
    log("=" * 72)
    log(f"Scored trades : {n_scored}   (skipped {n_skipped})")
    log(f"Win threshold : net_return >= {WIN_THRESHOLD:.2%}")
    log("")
    log(f"BASELINE (all scored): win_rate={base_wr:.4f} ({base_wr:.2%})  "
        f"avg_net_return={base_ret:+.4f} ({base_ret:+.2%})")
    log("")
    log("ML-PROBABILITY DECILE TABLE (1=lowest prob, 10=highest):")
    log(f"  {'dec':>3} {'n':>5} {'prob_rng':>15} {'prob_mu':>8} "
        f"{'win_rate':>9} {'avg_net':>9}")
    for r in decile_rows:
        log(f"  {r['decile']:>3} {r['n']:>5} "
            f"{r['prob_min']:>6.3f}-{r['prob_max']:<6.3f} "
            f"{r['prob_mean']:>8.3f} "
            f"{r['win_rate']:>8.2%} {r['avg_net_return']:>+8.2%}")
    log("")
    log(f"TOP decile (10): win_rate={top['win_rate']:.2%}  "
        f"avg_net={top['avg_net_return']:+.2%}  n={top['n']}")
    log(f"BOT decile (1) : win_rate={bottom['win_rate']:.2%}  "
        f"avg_net={bottom['avg_net_return']:+.2%}  n={bottom['n']}")
    log(f"LIFT (top win_rate / baseline win_rate): {lift:.2f}x")
    log("")
    log("PROBABILITY THRESHOLDS:")
    log(f"  {'thr':>5} {'n':>6} {'win_rate':>9} {'avg_net':>9}")
    for r in threshold_rows:
        if r["n"] == 0:
            log(f"  {r['threshold']:>5.2f} {r['n']:>6} {'--':>9} {'--':>9}")
        else:
            log(f"  {r['threshold']:>5.2f} {r['n']:>6} "
                f"{r['win_rate']:>8.2%} {r['avg_net_return']:>+8.2%}")
    log("")
    log(f"Spearman rank corr (ml_prob vs net_return): {spearman:+.4f}")
    log("")
    log(f"VERDICT: {verdict}")
    log("=" * 72)

    # ── Write JSON report ─────────────────────────────────────────────────
    report = {
        "n_scored": n_scored,
        "n_skipped": n_skipped,
        "skips": {"no_dataframe": n_skip_nodf, "no_feature_row": n_skip_norow,
                  "score_fail": n_skip_score},
        "win_threshold": WIN_THRESHOLD,
        "models_loaded": loaded,
        "ensemble_weights": {"lgbm": W_LGBM, "xgb": W_XGB, "rf": W_RF, "et": W_ET},
        "baseline": {"win_rate": base_wr, "avg_net_return": base_ret,
                     "n": n_scored},
        "baseline_meta_from_file": {"win_rate": meta.get("win_rate"),
                                    "avg_net_return": meta.get("avg_net_return")},
        "deciles": decile_rows,
        "top_decile": top,
        "bottom_decile": bottom,
        "lift": lift,
        "thresholds": threshold_rows,
        "spearman_prob_vs_return": spearman,
        "verdict": verdict,
    }
    with open(REPORT_JSON, "w") as f:
        json.dump(report, f, indent=2)
    log(f"[report] written to {REPORT_JSON}")


if __name__ == "__main__":
    main()
