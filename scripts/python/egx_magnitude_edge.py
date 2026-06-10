#!/usr/bin/env python3
"""
egx_magnitude_edge.py — THE #1 edge lever: magnitude-weighted position sizing.

Premise: the classifier predicts WIN PROBABILITY well (top-decile wins ~25% vs
~16.9% baseline) but does NOT predict return MAGNITUDE (Spearman ≈ 0). Winners
range +7%..+60%; the big winners (>=20%) are ~25% of winners but ~48% of total
profit. Sizing every trade equally leaves that on the table.

This script scores every clean trade with:
  p_win       = classifier ensemble prob (0.40 lgbm + 0.25 xgb + 0.20 rf + 0.15 et)
  e_mag       = return regressor's predicted 5d magnitude
  expectancy  = p_win * e_mag       (magnitude-aware score)
  net         = actual net_return_pct (ground truth)

Then it compares THREE fixed-fraction sizing strategies on the same top-N signal
set (10% base capital per trade):
  A) Flat            — every taken trade = 10%.
  B) Prob-weighted   — size ∝ p_win   (normalised so avg = 10%).
  C) Expectancy-wtd  — size ∝ p_win*e_mag (normalised, capped 15%, floored 0).

Metrics per strategy: total net return (fixed-fraction compounding), avg net
return per trade, annualised Sharpe (sqrt(252/5)), max drawdown, and the share
of big winners (>=20%) captured with meaningful (>=base) size.

Reuses the proven cache-build + ensemble scoring from egx_model_lift.py.

Run:  python3 egx_magnitude_edge.py
"""
import os
import sys
import json
import sqlite3
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Reuse the proven model loading + ensemble scoring verbatim.
from egx_model_lift import load_models, score_one  # noqa: E402

TRADES_JSON = "/tmp/egx_clean_trades.json"
REPORT_JSON = "/tmp/egx_magnitude_edge_report.json"
DB_PATH = "/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db"
MODELS = HERE / "models" / "ml_trainer"

WIN_THRESHOLD = 0.07     # net_return_pct >= 7%  => win  (matches clean-trades meta)
BIG_WINNER = 0.20        # net_return_pct >= 20% => "big winner"
POS_BASE = 0.10          # 10% capital per trade (base fixed fraction)
POS_CAP = 0.15           # 15% cap per trade for Kelly safety (strategy C)
TOP_FRAC = 0.20          # take the top 20% of signals by the relevant score
ANN = np.sqrt(252.0 / 5.0)  # 5-trading-day holding -> annualisation factor


def log(msg):
    print(msg, flush=True)


def load_regressor():
    """Load the multi-horizon return regressor. Returns (reg_dict, info)."""
    import joblib
    path = MODELS / "explosion_return_regressor_v1.pkl"
    info = {"path": str(path)}
    try:
        reg = joblib.load(str(path))
    except Exception as e:
        return None, {**info, "loaded": f"FAIL: {e}"}

    if not isinstance(reg, dict):
        # Single estimator fallback.
        nfi = getattr(reg, "n_features_in_", None)
        return {"_single": reg}, {**info, "loaded": True, "type": type(reg).__name__,
                                  "horizons": ["_single"], "n_features_in": nfi}

    horizons = list(reg.keys())
    sample = reg[horizons[0]]
    nfi = getattr(sample, "n_features_in_", None)
    return reg, {**info, "loaded": True, "type": type(sample).__name__,
                 "horizons": horizons, "n_features_in": nfi}


def _rankdata(a):
    """Average ranks with tie handling (mirrors egx_model_lift)."""
    order_a = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order_a] = np.arange(len(a), dtype=float)
    sorted_a = a[order_a]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            ranks[order_a[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(x, y):
    rx, ry = _rankdata(np.asarray(x, float)), _rankdata(np.asarray(y, float))
    if rx.std() > 0 and ry.std() > 0:
        return float(np.corrcoef(rx, ry)[0, 1])
    return float("nan")


def equity_curve(sizes, rets):
    """Fixed-fraction compounding: each trade risks `size` fraction of equity,
    returns size*ret on it. Returns (equity_array, total_return)."""
    eq = 1.0
    curve = [eq]
    for s, r in zip(sizes, rets):
        eq *= (1.0 + s * r)
        curve.append(eq)
    return np.array(curve), float(curve[-1] - 1.0)


def max_drawdown(curve):
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    return float(dd.min())  # most negative


def sharpe(sizes, rets):
    """Annualised Sharpe of the per-trade sized returns (size*ret)."""
    pnl = np.asarray(sizes, float) * np.asarray(rets, float)
    sd = pnl.std()
    if sd <= 0:
        return float("nan")
    return float((pnl.mean() / sd) * ANN)


def evaluate(name, sizes, rets, big_mask):
    """Compute the full metric bundle for one sizing strategy over the chosen
    signal set. `sizes` and `rets` are aligned arrays; `big_mask` flags big
    winners within the set."""
    sizes = np.asarray(sizes, float)
    rets = np.asarray(rets, float)
    curve, total = equity_curve(sizes, rets)
    avg = float((sizes * rets).mean())
    shp = sharpe(sizes, rets)
    mdd = max_drawdown(curve)
    # Big-winner capture: fraction of big winners taken with >= base size.
    n_big = int(big_mask.sum())
    if n_big > 0:
        captured = int((big_mask & (sizes >= POS_BASE - 1e-9)).sum())
        big_cap = captured / n_big
    else:
        captured, big_cap = 0, float("nan")
    # Capital-weighted exposure to big winners (how much size they actually got).
    big_size_share = (float(sizes[big_mask].sum() / sizes.sum())
                      if sizes.sum() > 0 else float("nan"))
    return {
        "strategy": name,
        "n": int(len(sizes)),
        "avg_size": float(sizes.mean()),
        "total_return": total,
        "avg_return_per_trade": avg,
        "sharpe": shp,
        "max_drawdown": mdd,
        "n_big_winners": n_big,
        "big_winners_captured": captured,
        "big_winner_capture_rate": big_cap,
        "big_winner_size_share": big_size_share,
    }


def normalize_to_base(weights, base=POS_BASE, cap=None, floor=0.0):
    """Scale non-negative weights so their MEAN equals `base`; optional per-trade
    cap and floor. Re-normalises mean after clipping (best effort)."""
    w = np.asarray(weights, float)
    w = np.where(np.isfinite(w), w, 0.0)
    w = np.clip(w, floor, None)
    if w.sum() <= 0:
        return np.full(len(w), base)
    w = w / w.mean() * base
    if cap is not None:
        w = np.clip(w, floor, cap)
        # Light re-normalisation so the average stays ~base after capping.
        if w.mean() > 0:
            scale = base / w.mean()
            w = np.clip(w * scale, floor, cap)
    return w


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

    # ── Classifier ensemble (reuse egx_model_lift loader/scorer) ────────────
    models, loaded = load_models()
    log(f"[clf] loaded: {json.dumps(loaded)}")
    if not any(k in models for k in ("lgbm", "xgb", "rf", "et")):
        log("ERROR: no classifier models loaded — cannot score.")
        sys.exit(1)

    # ── Return regressor ────────────────────────────────────────────────────
    reg, reg_info = load_regressor()
    log(f"[reg] {json.dumps(reg_info)}")
    reg_ok = reg is not None and reg_info.get("loaded") is True
    reg_nfeat = reg_info.get("n_features_in") if reg_ok else None
    # Pick the 5d horizon to match the trades' 5-bar forward return; fall back.
    if reg_ok:
        reg_horizon = "5d" if "5d" in reg else reg_info["horizons"][-1]
        log(f"[reg] using horizon='{reg_horizon}' expecting {reg_nfeat} features")
    else:
        reg_horizon = None

    # ── Feature builder + caches ────────────────────────────────────────────
    from explosion_ml import (_build_feature_row, _build_ohlcv_cache,
                              _load_egx30_cache, FEATURE_COLS)
    feat_path = MODELS / "explosion_features_v3.json"
    FEAT = json.loads(feat_path.read_text()) if feat_path.exists() else list(FEATURE_COLS)
    n_feat = len(FEAT)
    log(f"[features] classifier expects {n_feat}; regressor expects {reg_nfeat}")

    max_date = max(t["entry_date"] for t in trades)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    log(f"[cache] building OHLCV cache up to {max_date} ...")
    cache = _build_ohlcv_cache(conn, max_date)
    egx30 = _load_egx30_cache(conn)
    log(f"[cache] OHLCV symbols={len(cache)}  EGX30 dates={len(egx30)}")

    # ── Score every trade ─────────────────────────────────────────────────
    rows = []  # dicts: net, p_win, e_mag
    n_skip_nodf = n_skip_norow = n_skip_score = 0
    n_done = 0
    for t in trades:
        n_done += 1
        if n_done % 1000 == 0:
            log(f"[score] {n_done}/{len(trades)}  kept={len(rows)} "
                f"skip(nodf={n_skip_nodf}, norow={n_skip_norow}, score={n_skip_score})")
        sym, date_str = t["symbol"], t["entry_date"]
        sym_df = cache.get(sym)
        if sym_df is None:
            n_skip_nodf += 1
            continue
        row = _build_feature_row(sym_df, date_str, egx30=egx30)
        if row is None:
            n_skip_norow += 1
            continue
        full = list(row)
        X_clf = np.array([full[:n_feat]], dtype=np.float32)
        try:
            p_win = score_one(models, X_clf)
        except Exception:
            p_win = None
        if p_win is None:
            n_skip_score += 1
            continue

        e_mag = None
        if reg_ok:
            nf = reg_nfeat if isinstance(reg_nfeat, int) and reg_nfeat > 0 else n_feat
            X_reg = np.nan_to_num(np.array([full[:nf]], dtype=np.float32),
                                  nan=0.0, posinf=10.0, neginf=-10.0)
            try:
                est = reg[reg_horizon]
                e_mag = float(est.predict(X_reg)[0])
            except Exception:
                e_mag = None

        rows.append({"net": float(t["net_return_pct"]),
                     "p_win": float(p_win),
                     "e_mag": (float(e_mag) if e_mag is not None else None)})

    conn.close()
    n_scored = len(rows)
    log(f"[score] DONE. kept={n_scored} "
        f"skip(nodf={n_skip_nodf}, norow={n_skip_norow}, score={n_skip_score})")
    if n_scored < 50:
        log("ERROR: too few trades scored.")
        sys.exit(1)

    nets = np.array([r["net"] for r in rows], float)
    pwin = np.array([r["p_win"] for r in rows], float)
    emag_raw = np.array([(r["e_mag"] if r["e_mag"] is not None else np.nan)
                         for r in rows], float)

    # ── Regressor sanity / fallback ─────────────────────────────────────────
    reg_flag = None
    n_emag = int(np.isfinite(emag_raw).sum())
    if not reg_ok or n_emag < n_scored * 0.5:
        reg_flag = ("regressor unusable (load fail or <50% predictions) — "
                    "FALLING BACK to classifier prob as magnitude proxy")
        emag = pwin.copy()
        e_mag_is_proxy = True
    else:
        emag = np.where(np.isfinite(emag_raw), emag_raw, np.nanmedian(emag_raw))
        e_std = float(np.nanstd(emag_raw))
        e_min, e_max = float(np.nanmin(emag_raw)), float(np.nanmax(emag_raw))
        # Garbage detection: ~constant, or absurd magnitudes (>200%).
        if e_std < 1e-6 or e_max > 2.0 or e_min < -2.0:
            reg_flag = (f"regressor output suspect (std={e_std:.5f}, "
                        f"range=[{e_min:.3f},{e_max:.3f}]) — FALLING BACK to "
                        f"classifier prob as magnitude proxy")
            emag = pwin.copy()
            e_mag_is_proxy = True
        else:
            e_mag_is_proxy = False
    if reg_flag:
        log(f"[reg][FLAG] {reg_flag}")

    # Expectancy = p_win * e_mag (the magnitude-aware score).
    expectancy = pwin * emag

    # ── Correlations (vs actual return) ─────────────────────────────────────
    sp_pwin = spearman(pwin, nets)
    sp_emag = spearman(emag, nets) if not e_mag_is_proxy else float("nan")
    sp_expect = spearman(expectancy, nets)
    log("")
    log(f"[corr] Spearman p_win      vs actual : {sp_pwin:+.4f}  (the ~0 baseline)")
    log(f"[corr] Spearman e_mag      vs actual : {sp_emag:+.4f}")
    log(f"[corr] Spearman expectancy vs actual : {sp_expect:+.4f}")

    # ── Pick the signal set: top TOP_FRAC by each strategy's own score ──────
    n_take = max(1, int(round(n_scored * TOP_FRAC)))

    def top_idx(score):
        return np.argsort(score)[::-1][:n_take]  # highest scores

    # A) Flat: ranked by p_win (the production gate), all sized equally.
    idx_A = top_idx(pwin)
    # B) Prob-weighted: same prob-ranked set, sized ∝ p_win.
    idx_B = top_idx(pwin)
    # C) Expectancy-weighted: ranked AND sized by expectancy.
    idx_C = top_idx(expectancy)

    big_all = nets >= BIG_WINNER

    # Strategy A — flat 10% on the prob-top set.
    rA, bA = nets[idx_A], big_all[idx_A]
    sizesA = np.full(len(idx_A), POS_BASE)
    resA = evaluate("A_flat", sizesA, rA, bA)

    # Strategy B — size ∝ p_win, normalised to mean=base (no cap beyond sanity).
    rB, bB = nets[idx_B], big_all[idx_B]
    sizesB = normalize_to_base(pwin[idx_B], base=POS_BASE, cap=POS_CAP, floor=0.0)
    resB = evaluate("B_prob_weighted", sizesB, rB, bB)

    # Strategy C — size ∝ expectancy, normalised, capped 15%, floored 0.
    rC, bC = nets[idx_C], big_all[idx_C]
    sizesC = normalize_to_base(expectancy[idx_C], base=POS_BASE,
                               cap=POS_CAP, floor=0.0)
    resC = evaluate("C_expectancy_weighted", sizesC, rC, bC)

    # ── Verdict ─────────────────────────────────────────────────────────────
    c_beats_a_sharpe = (np.isfinite(resC["sharpe"]) and np.isfinite(resA["sharpe"])
                        and resC["sharpe"] > resA["sharpe"])
    c_beats_a_ret = resC["total_return"] > resA["total_return"]
    ret_mult = (resC["total_return"] / resA["total_return"]
                if resA["total_return"] not in (0.0,) else float("nan"))
    shp_mult = (resC["sharpe"] / resA["sharpe"]
                if resA["sharpe"] not in (0.0,) and np.isfinite(resA["sharpe"])
                else float("nan"))

    if e_mag_is_proxy:
        verdict = ("INCONCLUSIVE — regressor fell back to prob proxy; magnitude "
                   "signal could not be tested honestly.")
    elif c_beats_a_sharpe and c_beats_a_ret:
        verdict = (f"MAGNITUDE EDGE CONFIRMED — expectancy-weighting (C) beats "
                   f"flat (A) on BOTH Sharpe ({shp_mult:.2f}x) and total return "
                   f"({ret_mult:.2f}x).")
    elif c_beats_a_ret or c_beats_a_sharpe:
        verdict = (f"PARTIAL EDGE — C beats A on "
                   f"{'return' if c_beats_a_ret else 'Sharpe'} only "
                   f"(ret {ret_mult:.2f}x, sharpe {shp_mult:.2f}x).")
    else:
        verdict = ("NO MAGNITUDE EDGE — expectancy-weighting does not beat flat "
                   "sizing on this trade set.")

    # ── Print comparison table ──────────────────────────────────────────────
    def fmt(res):
        return (f"  {res['strategy']:<22} {res['n']:>4} "
                f"{res['avg_size']:>7.3f} "
                f"{res['total_return']:>+9.2%} "
                f"{res['avg_return_per_trade']:>+9.4f} "
                f"{res['sharpe']:>7.2f} "
                f"{res['max_drawdown']:>+8.2%} "
                f"{res['big_winner_capture_rate']:>7.0%} "
                f"{res['big_winner_size_share']:>8.1%}")

    log("")
    log("=" * 96)
    log("EGX MAGNITUDE EDGE — SIZING STRATEGY COMPARISON")
    log("=" * 96)
    log(f"Scored trades: {n_scored}   Signal set: top {TOP_FRAC:.0%} = {n_take} "
        f"trades   Base size: {POS_BASE:.0%}   Cap: {POS_CAP:.0%}")
    log(f"Win threshold {WIN_THRESHOLD:.0%}  |  Big winner threshold {BIG_WINNER:.0%}  "
        f"(big winners in full set: {int(big_all.sum())})")
    if e_mag_is_proxy:
        log("** e_mag is a PROXY (classifier prob) — regressor flagged unusable **")
    log("")
    log(f"  {'strategy':<22} {'n':>4} {'avgSize':>7} {'totRet':>9} "
        f"{'avg/trd':>9} {'Sharpe':>7} {'maxDD':>8} {'bigCap':>7} {'bigWt':>8}")
    log("  " + "-" * 92)
    log(fmt(resA))
    log(fmt(resB))
    log(fmt(resC))
    log("  " + "-" * 92)
    log("  bigCap = % of big winners (in set) sized >= base | "
        "bigWt = capital share to big winners")
    log("")
    log(f"  C vs A total return : {ret_mult:.2f}x   "
        f"C vs A Sharpe : {shp_mult:.2f}x")
    log("")
    log("CORRELATION (rank, vs actual net return):")
    log(f"  p_win      : {sp_pwin:+.4f}   (the classifier's ~0 magnitude signal)")
    log(f"  e_mag      : {sp_emag:+.4f}   (regressor magnitude alone)")
    log(f"  expectancy : {sp_expect:+.4f}   (p_win x e_mag)")
    log("")
    log(f"VERDICT: {verdict}")
    log("=" * 96)

    # ── Write JSON report ─────────────────────────────────────────────────
    report = {
        "n_scored": n_scored,
        "skips": {"no_dataframe": n_skip_nodf, "no_feature_row": n_skip_norow,
                  "score_fail": n_skip_score},
        "params": {"win_threshold": WIN_THRESHOLD, "big_winner": BIG_WINNER,
                   "pos_base": POS_BASE, "pos_cap": POS_CAP, "top_frac": TOP_FRAC,
                   "annualization": float(ANN)},
        "classifier": {"models_loaded": loaded,
                       "weights": {"lgbm": 0.40, "xgb": 0.25, "rf": 0.20, "et": 0.15}},
        "regressor": reg_info,
        "regressor_horizon_used": reg_horizon,
        "regressor_e_mag_range": (
            None if e_mag_is_proxy else
            {"min": float(np.nanmin(emag_raw)), "max": float(np.nanmax(emag_raw)),
             "mean": float(np.nanmean(emag_raw)), "std": float(np.nanstd(emag_raw))}),
        "e_mag_is_proxy": bool(e_mag_is_proxy),
        "regressor_flag": reg_flag,
        "correlations_spearman_vs_actual": {
            "p_win": sp_pwin, "e_mag": sp_emag, "expectancy": sp_expect},
        "strategies": {"A_flat": resA, "B_prob_weighted": resB,
                       "C_expectancy_weighted": resC},
        "C_vs_A": {"total_return_multiple": ret_mult, "sharpe_multiple": shp_mult,
                   "beats_on_return": bool(c_beats_a_ret),
                   "beats_on_sharpe": bool(c_beats_a_sharpe)},
        "verdict": verdict,
    }
    with open(REPORT_JSON, "w") as f:
        json.dump(report, f, indent=2, default=float)
    log(f"[report] written to {REPORT_JSON}")


if __name__ == "__main__":
    main()
