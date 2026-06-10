#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monte Carlo Robustness Testing suite for an EGX stock trading strategy.

Purpose: detect whether the strategy's edge is GENUINE or just
luck / overfitting / outlier-driven, BEFORE risking real client capital.

Four tests (vectorized with numpy when available; pure-python fallback):
  1. Trade-order shuffling      (N=10,000) -> drawdown-path risk
  2. Bootstrap resampling       (N=10,000) -> P(unprofitable history)
  3. Skip-trades fragility      (N=10,000) -> outlier dependence  [MOST IMPORTANT]
  4. Return-perturbation noise  (N=5,000)  -> overfitting symptom

Position model: FIXED-FRACTION. Each trade risks POS_FRACTION of capital:
    equity *= (1 + POS_FRACTION * trade_return)
This avoids the absurd 100%-capital compounding bug.

Usage:  python3 egx_monte_carlo.py [path_to_trades.json]
"""

import json
import math
import random
import sys

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
INPUT_PATH = "/tmp/egx_clean_trades.json"
OUTPUT_PATH = "/tmp/egx_monte_carlo_report.json"

SEED = 42
POS_FRACTION = 0.10          # fraction of capital risked per trade
N_SHUFFLE = 10_000
N_BOOTSTRAP = 10_000
N_SKIP = 10_000
N_NOISE = 5_000
NOISE_STD = 0.005            # ±0.5% gaussian noise on each trade return
SKIP_MIN, SKIP_MAX = 0.05, 0.20   # 5%-20% of trades removed per skip-sim
BARS_PER_TRADE = 5           # ~5-bar holding period
TRADING_DAYS = 252
ANNUAL_FACTOR = math.sqrt(TRADING_DAYS / BARS_PER_TRADE)

# ----------------------------------------------------------------------------
# numpy detection
# ----------------------------------------------------------------------------
try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:
    HAVE_NUMPY = False


# ----------------------------------------------------------------------------
# Equity-curve / drawdown helpers
# ----------------------------------------------------------------------------
def equity_curve_returns(returns):
    """Return final_return (fraction) and max_drawdown (positive fraction)
    for a sequence of trade returns using the fixed-fraction model.
    Pure-python implementation."""
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1.0 + POS_FRACTION * r)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
    return equity - 1.0, max_dd


def percentile(sorted_vals, p):
    """Linear-interpolation percentile on an already-sorted list."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# ----------------------------------------------------------------------------
# numpy-vectorized core
# ----------------------------------------------------------------------------
def np_curve_stats(growth_matrix):
    """growth_matrix: (n_sims, n_trades) array of per-trade growth factors
    (1 + POS_FRACTION*r). Returns (final_returns, max_drawdowns) arrays."""
    equity = np.cumprod(growth_matrix, axis=1)
    final_returns = equity[:, -1] - 1.0
    running_peak = np.maximum.accumulate(equity, axis=1)
    dd = (running_peak - equity) / running_peak
    max_dd = dd.max(axis=1)
    return final_returns, max_dd


# ----------------------------------------------------------------------------
# Tests (numpy)
# ----------------------------------------------------------------------------
def test1_shuffle_np(rng, growth, orig_dd, n):
    idx = np.argsort(rng.random((n, growth.size)), axis=1)
    mat = growth[idx]
    _, max_dd = np_curve_stats(mat)
    s = np.sort(max_dd)
    return {
        "original_max_dd": orig_dd,
        "mc_mean_dd": float(max_dd.mean()),
        "mc_median_dd": float(np.median(max_dd)),
        "dd_p5": float(np.percentile(s, 5)),
        "dd_p95": float(np.percentile(s, 95)),
        "p_dd_gt_50pct": float((max_dd > 0.50).mean()),
        "n_sims": n,
    }


def test2_bootstrap_np(rng, growth, n):
    k = growth.size
    idx = rng.integers(0, k, size=(n, k))
    mat = growth[idx]
    final_returns, max_dd = np_curve_stats(mat)
    return {
        "final_return_mean": float(final_returns.mean()),
        "final_return_median": float(np.median(final_returns)),
        "final_return_p5": float(np.percentile(final_returns, 5)),
        "final_return_p95": float(np.percentile(final_returns, 95)),
        "max_dd_mean": float(max_dd.mean()),
        "max_dd_median": float(np.median(max_dd)),
        "max_dd_p5": float(np.percentile(max_dd, 5)),
        "max_dd_p95": float(np.percentile(max_dd, 95)),
        "p_final_return_lt_0": float((final_returns < 0).mean()),
        "n_sims": n,
    }


def test3_skip_np(rng, returns, n):
    k = returns.size
    skip_fracs = rng.uniform(SKIP_MIN, SKIP_MAX, size=n)
    keep_mask = rng.random((n, k)) >= skip_fracs[:, None]
    growth_full = 1.0 + POS_FRACTION * returns[None, :]
    # where skipped, growth factor = 1 (no-op); win = return > win_threshold
    growth = np.where(keep_mask, growth_full, 1.0)
    equity = np.cumprod(growth, axis=1)
    final_returns = equity[:, -1] - 1.0
    win_flags = returns > WIN_THRESHOLD
    kept_wins = (keep_mask & win_flags[None, :]).sum(axis=1)
    kept_count = keep_mask.sum(axis=1)
    win_rate = kept_wins / np.maximum(kept_count, 1)
    p_neg = float((final_returns < 0).mean())
    verdict = ("FRAGILE: edge is outlier-driven"
               if p_neg > 0.30 else "ROBUST")
    return {
        "final_return_mean": float(final_returns.mean()),
        "final_return_median": float(np.median(final_returns)),
        "final_return_p5": float(np.percentile(final_returns, 5)),
        "final_return_p95": float(np.percentile(final_returns, 95)),
        "win_rate_mean": float(win_rate.mean()),
        "p_final_return_lt_0": p_neg,
        "verdict": verdict,
        "n_sims": n,
    }


def test4_noise_np(rng, returns, orig_mean, orig_sharpe, n):
    k = returns.size
    noise = rng.normal(0.0, NOISE_STD, size=(n, k))
    noisy = returns[None, :] + noise
    growth = 1.0 + POS_FRACTION * noisy
    final_returns, _ = np_curve_stats(growth)
    means = noisy.mean(axis=1)
    stds = noisy.std(axis=1, ddof=1)
    sharpes = np.where(stds > 0, means / stds * ANNUAL_FACTOR, 0.0)
    # "survive": edge stays positive after noise
    mean_survival = float((means > 0).mean())
    sharpe_survival = float((sharpes > 0).mean())
    profit_survival = float((final_returns > 0).mean())
    return {
        "original_mean_return": orig_mean,
        "original_sharpe": orig_sharpe,
        "noisy_mean_return_avg": float(means.mean()),
        "noisy_sharpe_avg": float(sharpes.mean()),
        "noisy_final_return_p5": float(np.percentile(final_returns, 5)),
        "mean_return_survival": mean_survival,
        "sharpe_survival": sharpe_survival,
        "profit_survival": profit_survival,
        "n_sims": n,
    }


# ----------------------------------------------------------------------------
# Tests (pure python fallback)
# ----------------------------------------------------------------------------
def test1_shuffle_py(returns, orig_dd, n):
    dds = []
    base = list(returns)
    for _ in range(n):
        random.shuffle(base)
        _, dd = equity_curve_returns(base)
        dds.append(dd)
    s = sorted(dds)
    mean = sum(dds) / len(dds)
    return {
        "original_max_dd": orig_dd,
        "mc_mean_dd": mean,
        "mc_median_dd": percentile(s, 50),
        "dd_p5": percentile(s, 5),
        "dd_p95": percentile(s, 95),
        "p_dd_gt_50pct": sum(1 for d in dds if d > 0.50) / len(dds),
        "n_sims": n,
    }


def test2_bootstrap_py(returns, n):
    k = len(returns)
    fr, dd = [], []
    for _ in range(n):
        samp = [returns[random.randrange(k)] for _ in range(k)]
        f, d = equity_curve_returns(samp)
        fr.append(f)
        dd.append(d)
    sfr, sdd = sorted(fr), sorted(dd)
    return {
        "final_return_mean": sum(fr) / len(fr),
        "final_return_median": percentile(sfr, 50),
        "final_return_p5": percentile(sfr, 5),
        "final_return_p95": percentile(sfr, 95),
        "max_dd_mean": sum(dd) / len(dd),
        "max_dd_median": percentile(sdd, 50),
        "max_dd_p5": percentile(sdd, 5),
        "max_dd_p95": percentile(sdd, 95),
        "p_final_return_lt_0": sum(1 for f in fr if f < 0) / len(fr),
        "n_sims": n,
    }


def test3_skip_py(returns, n):
    k = len(returns)
    fr, wrs = [], []
    for _ in range(n):
        sf = random.uniform(SKIP_MIN, SKIP_MAX)
        kept = [r for r in returns if random.random() >= sf]
        if not kept:
            kept = [0.0]
        f, _ = equity_curve_returns(kept)
        fr.append(f)
        wins = sum(1 for r in kept if r > WIN_THRESHOLD)
        wrs.append(wins / len(kept))
    sfr = sorted(fr)
    p_neg = sum(1 for f in fr if f < 0) / len(fr)
    verdict = ("FRAGILE: edge is outlier-driven"
               if p_neg > 0.30 else "ROBUST")
    return {
        "final_return_mean": sum(fr) / len(fr),
        "final_return_median": percentile(sfr, 50),
        "final_return_p5": percentile(sfr, 5),
        "final_return_p95": percentile(sfr, 95),
        "win_rate_mean": sum(wrs) / len(wrs),
        "p_final_return_lt_0": p_neg,
        "verdict": verdict,
        "n_sims": n,
    }


def test4_noise_py(returns, orig_mean, orig_sharpe, n):
    k = len(returns)
    means, sharpes, frs = [], [], []
    for _ in range(n):
        noisy = [r + random.gauss(0.0, NOISE_STD) for r in returns]
        f, _ = equity_curve_returns(noisy)
        frs.append(f)
        m = sum(noisy) / k
        var = sum((x - m) ** 2 for x in noisy) / (k - 1)
        sd = math.sqrt(var)
        means.append(m)
        sharpes.append((m / sd * ANNUAL_FACTOR) if sd > 0 else 0.0)
    sfr = sorted(frs)
    return {
        "original_mean_return": orig_mean,
        "original_sharpe": orig_sharpe,
        "noisy_mean_return_avg": sum(means) / len(means),
        "noisy_sharpe_avg": sum(sharpes) / len(sharpes),
        "noisy_final_return_p5": percentile(sfr, 5),
        "mean_return_survival": sum(1 for m in means if m > 0) / len(means),
        "sharpe_survival": sum(1 for s in sharpes if s > 0) / len(sharpes),
        "profit_survival": sum(1 for f in frs if f > 0) / len(frs),
        "n_sims": n,
    }


# ----------------------------------------------------------------------------
# Static metrics
# ----------------------------------------------------------------------------
def compute_static(returns):
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    sharpe = (mean / std * ANNUAL_FACTOR) if std > 0 else 0.0

    # outlier dependence: % of total profit from top 5% of trades
    gains = sorted((r for r in returns if r > 0), reverse=True)
    total_gain = sum(gains)
    top_n = max(1, int(round(0.05 * n)))
    top_gain = sum(gains[:top_n])
    outlier_pct = (top_gain / total_gain) if total_gain > 0 else float("nan")

    # original (in-sample order) equity curve
    orig_final, orig_dd = equity_curve_returns(returns)

    return {
        "n_trades": n,
        "mean_net_return": mean,
        "std_net_return": std,
        "annualized_sharpe": sharpe,
        "win_rate": sum(1 for r in returns if r > WIN_THRESHOLD) / n,
        "outlier_top5pct_profit_share": outlier_pct,
        "outlier_driven": (outlier_pct > 0.50) if not math.isnan(outlier_pct) else None,
        "original_final_return": orig_final,
        "original_max_dd": orig_dd,
    }


# ----------------------------------------------------------------------------
# Composite robustness confidence score
# ----------------------------------------------------------------------------
def robustness_score(t1, t2, t3, t4):
    a = 1.0 - t1["p_dd_gt_50pct"]
    b = 1.0 - t2["p_final_return_lt_0"]
    c = 1.0 - t3["p_final_return_lt_0"]
    d = t4["profit_survival"]            # noise-survival fraction
    score = (a + b + c + d) / 4.0 * 100.0
    if score < 55:
        flag = "WEAK"
    elif score <= 80:
        flag = "MODERATE"
    else:
        flag = "STRONG"
    return {
        "components": {
            "1_minus_p_dd_gt_50": a,
            "1_minus_p_boot_return_lt_0": b,
            "1_minus_p_skip_return_lt_0": c,
            "noise_survival_fraction": d,
        },
        "score": score,
        "flag": flag,
    }


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
def pct(x):
    return f"{x * 100:.2f}%"


def print_report(static, t1, t2, t3, t4, comp, meta):
    L = print
    L("=" * 74)
    L("  EGX STRATEGY — MONTE CARLO ROBUSTNESS REPORT")
    L("  تقرير اختبار المتانة (مونت كارلو) لاستراتيجية البورصة المصرية")
    L("=" * 74)
    L(f"  Engine: {'numpy (vectorized)' if HAVE_NUMPY else 'pure-python'}"
      f"   |   Seed: {SEED}   |   Position fraction: {POS_FRACTION:.0%}")
    L(f"  Trades / الصفقات: {static['n_trades']}"
      f"   |   Source meta n_trades: {meta.get('n_trades')}")
    L("-" * 74)
    L("  BASELINE / الأساس")
    L(f"    Mean net return / متوسط العائد   : {pct(static['mean_net_return'])}")
    L(f"    Std net return                   : {pct(static['std_net_return'])}")
    L(f"    Win rate (>{WIN_THRESHOLD:.0%}) / نسبة الفوز   : {pct(static['win_rate'])}")
    L(f"    Annualized Sharpe / شارب السنوي  : {static['annualized_sharpe']:.3f}")
    L(f"    Original final return            : {pct(static['original_final_return'])}")
    L(f"    Original max drawdown / أقصى تراجع: {pct(static['original_max_dd'])}")
    L(f"    Top-5% trades' profit share / حصة أكبر 5%: "
      f"{pct(static['outlier_top5pct_profit_share'])}"
      f"  -> {'OUTLIER-DRIVEN ⚠' if static['outlier_driven'] else 'OK'}")
    L("=" * 74)

    L("  TEST 1 — Trade-Order Shuffling / خلط ترتيب الصفقات  "
      f"(N={t1['n_sims']:,})")
    L(f"    Original max DD : {pct(t1['original_max_dd'])}")
    L(f"    MC mean DD      : {pct(t1['mc_mean_dd'])}")
    L(f"    MC median DD    : {pct(t1['mc_median_dd'])}")
    L(f"    DD 5th pct      : {pct(t1['dd_p5'])}")
    L(f"    DD 95th pct     : {pct(t1['dd_p95'])}")
    L(f"    P(max DD > 50%) : {pct(t1['p_dd_gt_50pct'])}")
    L("-" * 74)

    L("  TEST 2 — Bootstrap Resampling / إعادة المعاينة  "
      f"(N={t2['n_sims']:,})")
    L(f"    Final return  mean / median : {pct(t2['final_return_mean'])} / "
      f"{pct(t2['final_return_median'])}")
    L(f"    Final return  5th / 95th    : {pct(t2['final_return_p5'])} / "
      f"{pct(t2['final_return_p95'])}")
    L(f"    Max DD        mean / median : {pct(t2['max_dd_mean'])} / "
      f"{pct(t2['max_dd_median'])}")
    L(f"    Max DD        5th / 95th    : {pct(t2['max_dd_p5'])} / "
      f"{pct(t2['max_dd_p95'])}")
    L(f"    P(final return < 0) / احتمال الخسارة : {pct(t2['p_final_return_lt_0'])}")
    L("-" * 74)

    L("  TEST 3 — Skip-Trades Fragility / هشاشة تخطي الصفقات  "
      f"(N={t3['n_sims']:,})  [MOST IMPORTANT]")
    L(f"    Final return mean   : {pct(t3['final_return_mean'])}")
    L(f"    Final return median : {pct(t3['final_return_median'])}")
    L(f"    Final return 5th pct: {pct(t3['final_return_p5'])}")
    L(f"    Win rate mean       : {pct(t3['win_rate_mean'])}")
    L(f"    P(final return < 0) : {pct(t3['p_final_return_lt_0'])}")
    L(f"    VERDICT / الحكم     : {t3['verdict']}")
    L("-" * 74)

    L("  TEST 4 — Return-Noise Perturbation / تشويش العوائد  "
      f"(N={t4['n_sims']:,}, σ={NOISE_STD:.3f})")
    L(f"    Original mean / Sharpe     : {pct(t4['original_mean_return'])} / "
      f"{t4['original_sharpe']:.3f}")
    L(f"    Noisy mean / Sharpe (avg)  : {pct(t4['noisy_mean_return_avg'])} / "
      f"{t4['noisy_sharpe_avg']:.3f}")
    L(f"    Noisy final return 5th pct : {pct(t4['noisy_final_return_p5'])}")
    L(f"    Mean-return survival       : {pct(t4['mean_return_survival'])}")
    L(f"    Sharpe survival            : {pct(t4['sharpe_survival'])}")
    L(f"    Profit survival            : {pct(t4['profit_survival'])}")
    L("=" * 74)

    L("  COMPOSITE ROBUSTNESS / درجة المتانة المركبة")
    c = comp["components"]
    L(f"    (1 - P[DD>50%])            : {c['1_minus_p_dd_gt_50']:.3f}")
    L(f"    (1 - P[boot return<0])     : {c['1_minus_p_boot_return_lt_0']:.3f}")
    L(f"    (1 - P[skip return<0])     : {c['1_minus_p_skip_return_lt_0']:.3f}")
    L(f"    noise-survival fraction    : {c['noise_survival_fraction']:.3f}")
    L(f"    >>> ROBUSTNESS SCORE / الدرجة : {comp['score']:.1f}/100  "
      f"[{comp['flag']}]")
    L("=" * 74)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else INPUT_PATH
    with open(path, "r") as f:
        data = json.load(f)
    meta = data.get("meta", {})
    global WIN_THRESHOLD
    WIN_THRESHOLD = meta.get("win_threshold", 0.07)

    returns_list = [t["net_return_pct"] for t in data["trades"]]

    static = compute_static(returns_list)
    orig_dd = static["original_max_dd"]
    orig_mean = static["mean_net_return"]
    orig_sharpe = static["annualized_sharpe"]

    if HAVE_NUMPY:
        random.seed(SEED)
        rng = np.random.default_rng(SEED)
        returns = np.asarray(returns_list, dtype=np.float64)
        growth = 1.0 + POS_FRACTION * returns
        t1 = test1_shuffle_np(rng, growth, orig_dd, N_SHUFFLE)
        t2 = test2_bootstrap_np(rng, growth, N_BOOTSTRAP)
        t3 = test3_skip_np(rng, returns, N_SKIP)
        t4 = test4_noise_np(rng, returns, orig_mean, orig_sharpe, N_NOISE)
    else:
        random.seed(SEED)
        t1 = test1_shuffle_py(returns_list, orig_dd, N_SHUFFLE)
        t2 = test2_bootstrap_py(returns_list, N_BOOTSTRAP)
        t3 = test3_skip_py(returns_list, N_SKIP)
        t4 = test4_noise_py(returns_list, orig_mean, orig_sharpe, N_NOISE)

    comp = robustness_score(t1, t2, t3, t4)

    print_report(static, t1, t2, t3, t4, comp, meta)

    report = {
        "config": {
            "seed": SEED,
            "pos_fraction": POS_FRACTION,
            "noise_std": NOISE_STD,
            "skip_range": [SKIP_MIN, SKIP_MAX],
            "win_threshold": WIN_THRESHOLD,
            "bars_per_trade": BARS_PER_TRADE,
            "annual_factor": ANNUAL_FACTOR,
            "engine": "numpy" if HAVE_NUMPY else "pure-python",
            "n_shuffle": N_SHUFFLE,
            "n_bootstrap": N_BOOTSTRAP,
            "n_skip": N_SKIP,
            "n_noise": N_NOISE,
        },
        "baseline": static,
        "test1_shuffle": t1,
        "test2_bootstrap": t2,
        "test3_skip": t3,
        "test4_noise": t4,
        "composite_robustness": comp,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote JSON report -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
