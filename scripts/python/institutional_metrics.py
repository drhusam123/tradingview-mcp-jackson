"""
Institutional Metrics Engine — EGX Navigator  Ph78
====================================================
محرك القياس المؤسسي: يحوّل الأداء الخام إلى لوحة قابلة للدفاع أمام لجنة استثمار.

المقاييس:
  - Sharpe, Sortino, Calmar, Omega ratio
  - Max Drawdown, Ulcer Index, Recovery Factor
  - Expectancy per trade (R-multiple based)
  - Rolling performance (30/60/90d windows)
  - Regime-conditioned performance split
  - Monte Carlo stress test (10,000 paths)
  - Walk-forward stability score
  - Confidence calibration check

CLI:
  python3 institutional_metrics.py            # full scorecard from live DB
  python3 institutional_metrics.py --days 60  # last 60 days
  python3 institutional_metrics.py --json     # JSON output for integration
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = _SCRIPT_DIR.parent.parent / "data" / "egx_trading.db"
_SCORECARD_JSON = _SCRIPT_DIR.parent.parent / "data" / "institutional_scorecard.json"

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class InstitutionalScorecard:
    # Returns
    cagr: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    omega: float = 0.0
    # Risk
    max_drawdown: float = 0.0        # negative float, e.g. -0.12
    ulcer_index: float = 0.0
    recovery_factor: float = 0.0
    avg_recovery_days: int = 0
    # Trade stats
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    max_consec_losses: int = 0
    # Stability
    rolling_sharpe_30d: float = 0.0
    rolling_sharpe_90d: float = 0.0
    stability_score: float = 0.0     # 0–100
    regime_robustness: str = "N/A"
    confidence_calibration: float = 0.0  # 0–1
    # Monte Carlo (populated separately)
    mc_p5_dd: float = 0.0
    mc_p1_dd: float = 0.0
    mc_ruin_prob: float = 0.0
    # Grade
    institutional_grade: str = "N/A"
    deployment_ready: bool = False
    # Meta
    days_analyzed: int = 0
    n_trades: int = 0
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_max_dd(returns_series: pd.Series) -> float:
    """Return max drawdown as a negative float (e.g. -0.25 for -25%)."""
    if returns_series.empty:
        return 0.0
    equity = (1 + returns_series).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def _max_dd_from_equity(equity_array: np.ndarray) -> float:
    """Return max drawdown from a raw equity array (starts at 1.0)."""
    if len(equity_array) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity_array)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = np.where(peak > 0, (equity_array - peak) / peak, 0.0)
    return float(dd.min())


def _compute_avg_recovery_days(returns_series: pd.Series) -> int:
    """
    Compute average number of days to recover from each drawdown episode
    (defined as peak-to-trough-to-new-peak cycle).
    """
    if returns_series.empty or len(returns_series) < 3:
        return 0

    equity = (1 + returns_series).cumprod().values
    n = len(equity)
    peak = equity[0]
    in_dd = False
    dd_start = 0
    recovery_lengths: List[int] = []

    for i in range(n):
        if equity[i] >= peak:
            if in_dd:
                # recovered
                recovery_lengths.append(i - dd_start)
                in_dd = False
            peak = equity[i]
        else:
            if not in_dd:
                in_dd = True
                dd_start = i

    if not recovery_lengths:
        return 0
    return int(round(np.mean(recovery_lengths)))


def _rolling_sharpe(returns: pd.Series, window: int, daily_rf: float) -> float:
    """Return Sharpe of the last `window` observations."""
    if len(returns) < max(5, window):
        slice_ = returns
    else:
        slice_ = returns.iloc[-window:]

    excess = slice_ - daily_rf
    if excess.std(ddof=1) == 0:
        return 0.0
    return float(excess.mean() / excess.std(ddof=1) * math.sqrt(252))


def _compute_equity_smoothness(equity_curve: pd.Series) -> float:
    """
    Return R² of linear fit on log(equity) — scaled to 0–100.
    100 = perfectly smooth upward drift; 0 = chaotic.
    """
    if equity_curve.empty or len(equity_curve) < 3:
        return 0.0

    ev = equity_curve.values.astype(float)
    ev = np.where(ev <= 0, 1e-9, ev)
    log_eq = np.log(ev)

    x = np.arange(len(log_eq))
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, log_eq)
    r2 = max(0.0, r_value ** 2)
    return float(round(r2 * 100, 1))


def _regime_split_analysis(
    trade_results: pd.DataFrame,
    regime_labels: pd.Series,
) -> str:
    """
    Join trades with regime labels and compute profit factor per regime bucket.
    Returns 'STRONG' if PF > 1.0 in all observed regimes,
            'MODERATE' if PF > 1.0 in majority,
            'WEAK' otherwise.
    """
    if trade_results.empty or regime_labels.empty:
        return "N/A"

    # Normalise regime labels to BULL / BEAR / NEUTRAL
    def _bucket(label: str) -> str:
        label = str(label).upper()
        if "BULL" in label:
            return "BULL"
        if "BEAR" in label:
            return "BEAR"
        return "NEUTRAL"

    regime_df = regime_labels.reset_index()
    regime_df.columns = ["date", "regime"]
    regime_df["regime"] = regime_df["regime"].apply(_bucket)
    # Normalise to string YYYY-MM-DD for safe merging
    regime_df["date"] = pd.to_datetime(regime_df["date"]).dt.strftime("%Y-%m-%d")

    # Use the trade's entry date as the join key
    date_col = None
    for c in ("entry_date", "scan_date", "date"):
        if c in trade_results.columns:
            date_col = c
            break
    if date_col is None:
        return "N/A"

    trades = trade_results.copy()
    trades["date"] = pd.to_datetime(trades[date_col]).dt.strftime("%Y-%m-%d")
    merged = trades.merge(regime_df, on="date", how="left")
    merged["regime"] = merged["regime"].fillna("NEUTRAL")

    results: Dict[str, float] = {}
    for regime, grp in merged.groupby("regime"):
        pnl = grp["pnl_pct"].dropna()
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl < 0].sum())
        pf = wins / losses if losses > 0 else (2.0 if wins > 0 else 0.0)
        results[str(regime)] = pf

    if not results:
        return "N/A"

    n_above = sum(1 for pf in results.values() if pf > 1.0)
    ratio = n_above / len(results)
    if ratio == 1.0:
        return "STRONG"
    if ratio >= 0.5:
        return "MODERATE"
    return "WEAK"


def _calibration_score(confidence_levels_df: pd.DataFrame) -> float:
    """
    ECE-based calibration: given a DataFrame with columns
    ['predicted_confidence', 'actual_outcome'] (0/1),
    compute Expected Calibration Error then return 1 - ECE (higher = better).
    Falls back to 0.5 if DataFrame is missing or malformed.
    """
    if confidence_levels_df is None or confidence_levels_df.empty:
        return 0.5
    if not {"predicted_confidence", "actual_outcome"}.issubset(confidence_levels_df.columns):
        return 0.5

    df = confidence_levels_df.dropna(subset=["predicted_confidence", "actual_outcome"]).copy()
    if len(df) < 5:
        return 0.5

    conf = df["predicted_confidence"].clip(0.0, 1.0).values
    actual = df["actual_outcome"].astype(float).values

    n_bins = 10
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(conf)

    for i in range(n_bins):
        mask = (conf >= bin_edges[i]) & (conf < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = (conf >= bin_edges[i]) & (conf <= bin_edges[i + 1])
        n_bin = mask.sum()
        if n_bin == 0:
            continue
        avg_conf = conf[mask].mean()
        avg_acc = actual[mask].mean()
        ece += (n_bin / n) * abs(avg_conf - avg_acc)

    return float(round(max(0.0, 1.0 - ece), 4))


def _max_consecutive(bool_series: pd.Series) -> int:
    """Return maximum consecutive True run length in a boolean Series."""
    if bool_series.empty:
        return 0
    max_run = 0
    current = 0
    for val in bool_series:
        if val:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def _assign_grade(
    sharpe: float,
    max_dd: float,
    win_rate: float,
    expectancy_r: float,
    stability: float,
) -> str:
    """
    Grading rubric:
      A — Sharpe ≥ 1.5 AND |DD| ≤ 15% AND WR ≥ 55% AND E(R) ≥ 0.5 AND stability ≥ 70
      B — Sharpe ≥ 1.0 AND |DD| ≤ 25% AND WR ≥ 45% AND E(R) ≥ 0.2 AND stability ≥ 50
      C — Sharpe ≥ 0.5 AND |DD| ≤ 35%
      D — otherwise
    """
    abs_dd = abs(max_dd)

    if (sharpe >= 1.5 and abs_dd <= 0.15 and win_rate >= 0.55
            and expectancy_r >= 0.5 and stability >= 70):
        return "A"
    if (sharpe >= 1.0 and abs_dd <= 0.25 and win_rate >= 0.45
            and expectancy_r >= 0.2 and stability >= 50):
        return "B"
    if sharpe >= 0.5 and abs_dd <= 0.35:
        return "C"
    return "D"


def _compute_pf(trades_df: pd.DataFrame) -> float:
    """Profit factor = sum(winning pnl_pct) / |sum(losing pnl_pct)|."""
    pnl = trades_df["pnl_pct"].dropna()
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def _compute_omega(returns: pd.Series, threshold: float = 0.0) -> float:
    """
    Omega ratio: integral of (1 - F(r)) dr for r > threshold
                 divided by integral of F(r) dr for r < threshold.
    Approximated via sum over sorted returns.
    """
    r = returns.dropna().values
    if len(r) < 2:
        return 1.0
    above = r[r > threshold] - threshold
    below = threshold - r[r < threshold]
    if below.sum() == 0:
        return float("inf")
    return float(above.sum() / below.sum())


def _compute_ulcer_index(equity_curve: pd.Series) -> float:
    """Ulcer Index = sqrt(mean(drawdown_pct²))."""
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    eq = equity_curve.values.astype(float)
    peak = np.maximum.accumulate(eq)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_pct = np.where(peak > 0, (eq - peak) / peak * 100, 0.0)
    return float(math.sqrt(np.mean(dd_pct ** 2)))


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def monte_carlo_stress(
    trade_results: pd.DataFrame,
    n_sims: int = 10_000,
    n_trades_forward: int = 100,
) -> Dict[str, float]:
    """
    Bootstrap resampling of pnl_pct values across n_sims paths of
    n_trades_forward trades each.

    Returns:
        p5_dd  : 5th-percentile worst drawdown (negative float)
        p1_dd  : 1st-percentile worst drawdown (CVaR proxy)
        ruin_prob : fraction of paths where max DD > 30%
    """
    pnl = trade_results["pnl_pct"].dropna().values / 100.0  # fractional
    if len(pnl) < 3:
        return {"p5_dd": 0.0, "p1_dd": 0.0, "ruin_prob": 0.0}

    rng = np.random.default_rng(42)
    worst_dds = np.empty(n_sims)

    for i in range(n_sims):
        sample = rng.choice(pnl, size=n_trades_forward, replace=True)
        equity = np.cumprod(1 + sample)
        equity = np.concatenate([[1.0], equity])
        worst_dds[i] = _max_dd_from_equity(equity)

    p5_dd = float(np.percentile(worst_dds, 5))
    p1_dd = float(np.percentile(worst_dds, 1))
    ruin_prob = float(np.mean(worst_dds < -0.30))

    return {
        "p5_dd": round(p5_dd, 4),
        "p1_dd": round(p1_dd, 4),
        "ruin_prob": round(ruin_prob, 4),
    }


# ---------------------------------------------------------------------------
# Main scorecard computation
# ---------------------------------------------------------------------------

def compute_full_scorecard(
    trade_results: pd.DataFrame,
    equity_curve: pd.Series,
    regime_labels: pd.Series,
    risk_free_rate: float = 0.19,
    confidence_levels_df: Optional[pd.DataFrame] = None,
) -> InstitutionalScorecard:
    """
    Compute complete institutional scorecard.

    Parameters
    ----------
    trade_results       : DataFrame with at minimum ['pnl_pct', 'r_multiple'] columns
    equity_curve        : pd.Series of daily equity values (starts at 1.0, date-indexed)
    regime_labels       : pd.Series of regime strings, date-indexed (from market_breadth_enhanced)
    risk_free_rate      : annual risk-free rate (default 0.19 for EGX / CBE)
    confidence_levels_df: optional DataFrame with calibration data
    """
    sc = InstitutionalScorecard()
    sc.n_trades = len(trade_results)

    if sc.n_trades < 5:
        sc.institutional_grade = "INSUFFICIENT_DATA"
        sc.deployment_ready = False
        return sc

    pnl = trade_results["pnl_pct"].dropna() / 100.0  # fractional
    r_multiples = trade_results["r_multiple"].dropna()

    # Daily risk-free rate
    daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1

    # ---- Daily returns from equity curve --------------------------------
    if len(equity_curve) >= 2:
        daily_returns = equity_curve.pct_change().dropna()
    else:
        daily_returns = pd.Series(dtype=float)

    # ---- CAGR -----------------------------------------------------------
    n_days = max(len(equity_curve) - 1, 1)
    sc.days_analyzed = n_days
    final_equity = float(equity_curve.iloc[-1]) if not equity_curve.empty else 1.0
    start_equity = float(equity_curve.iloc[0]) if not equity_curve.empty else 1.0
    if start_equity > 0 and final_equity > 0:
        sc.cagr = round((final_equity / start_equity) ** (252 / max(n_days, 1)) - 1, 4)
    else:
        sc.cagr = 0.0

    # ---- Sharpe ---------------------------------------------------------
    if len(daily_returns) >= 5:
        excess = daily_returns - daily_rf
        if excess.std(ddof=1) > 0:
            sc.sharpe = round(excess.mean() / excess.std(ddof=1) * math.sqrt(252), 3)

    # ---- Sortino --------------------------------------------------------
    if len(daily_returns) >= 5:
        excess = daily_returns - daily_rf
        downside = excess[excess < 0]
        downside_std = downside.std(ddof=1) if len(downside) >= 2 else 0.0
        if downside_std > 0:
            sc.sortino = round(excess.mean() / downside_std * math.sqrt(252), 3)

    # ---- Max Drawdown ---------------------------------------------------
    sc.max_drawdown = round(_compute_max_dd(daily_returns), 4) if len(daily_returns) >= 2 else 0.0

    # ---- Calmar ---------------------------------------------------------
    if sc.max_drawdown < 0:
        sc.calmar = round(sc.cagr / abs(sc.max_drawdown), 3)

    # ---- Omega ----------------------------------------------------------
    sc.omega = round(_compute_omega(daily_returns, threshold=daily_rf), 3)

    # ---- Ulcer Index ----------------------------------------------------
    sc.ulcer_index = round(_compute_ulcer_index(equity_curve), 3)

    # ---- Recovery Factor ------------------------------------------------
    total_return = (final_equity / start_equity - 1) if start_equity > 0 else 0.0
    if sc.max_drawdown < 0:
        sc.recovery_factor = round(total_return / abs(sc.max_drawdown), 3)

    # ---- Avg Recovery Days ----------------------------------------------
    sc.avg_recovery_days = _compute_avg_recovery_days(daily_returns)

    # ---- Win Rate -------------------------------------------------------
    n_wins = int((pnl > 0).sum())
    sc.win_rate = round(n_wins / len(pnl), 4) if len(pnl) > 0 else 0.0

    # ---- Profit Factor --------------------------------------------------
    sc.profit_factor = round(_compute_pf(trade_results), 3)

    # ---- R-multiple stats -----------------------------------------------
    if len(r_multiples) >= 3:
        wins_r = r_multiples[r_multiples > 0]
        losses_r = r_multiples[r_multiples < 0]
        sc.avg_win_r = round(float(wins_r.mean()), 3) if len(wins_r) else 0.0
        sc.avg_loss_r = round(float(losses_r.mean()), 3) if len(losses_r) else 0.0
        sc.expectancy_r = round(float(r_multiples.mean()), 3)
    else:
        # fallback from pnl_pct
        wins_p = pnl[pnl > 0]
        losses_p = pnl[pnl < 0]
        sc.avg_win_r = round(float(wins_p.mean() * 100), 3) if len(wins_p) else 0.0
        sc.avg_loss_r = round(float(losses_p.mean() * 100), 3) if len(losses_p) else 0.0
        sc.expectancy_r = round(float(pnl.mean() * 100), 3)

    # ---- Max Consecutive Losses -----------------------------------------
    is_loss = trade_results["pnl_pct"].fillna(0) < 0
    sc.max_consec_losses = _max_consecutive(is_loss)

    # ---- Rolling Sharpe -------------------------------------------------
    sc.rolling_sharpe_30d = round(_rolling_sharpe(daily_returns, 30, daily_rf), 3)
    sc.rolling_sharpe_90d = round(_rolling_sharpe(daily_returns, 90, daily_rf), 3)

    # ---- Stability Score (R²) -------------------------------------------
    sc.stability_score = round(_compute_equity_smoothness(equity_curve), 1)

    # ---- Regime Robustness ----------------------------------------------
    sc.regime_robustness = _regime_split_analysis(trade_results, regime_labels)

    # ---- Calibration ----------------------------------------------------
    sc.confidence_calibration = _calibration_score(confidence_levels_df)

    # ---- Grade ----------------------------------------------------------
    sc.institutional_grade = _assign_grade(
        sc.sharpe, sc.max_drawdown, sc.win_rate, sc.expectancy_r, sc.stability_score
    )
    sc.deployment_ready = sc.institutional_grade in ("A", "B")

    return sc


# ---------------------------------------------------------------------------
# DB loaders
# ---------------------------------------------------------------------------

def load_trades_from_db(
    db_path: Path = _DEFAULT_DB,
    days: int = 90,
) -> pd.DataFrame:
    """
    Load trades from the `trades` table for the last `days` days.
    Adds `r_multiple` column: pnl_pct / risk_pct (default risk 7%).
    Uses `scan_date` as primary date column (falls back to entry_date).
    """
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    try:
        # Determine which date column to filter on
        cursor = conn.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in cursor.fetchall()]

        date_col = "scan_date" if "scan_date" in cols else ("entry_date" if "entry_date" in cols else "created_at")

        query = f"""
            SELECT *
            FROM trades
            WHERE {date_col} >= ?
              AND pnl_pct IS NOT NULL
            ORDER BY {date_col} ASC
        """
        df = pd.read_sql_query(query, conn, params=(cutoff,))
    finally:
        conn.close()

    if df.empty:
        return df

    # Ensure pnl_pct is numeric
    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")

    # Compute risk_pct: |entry_price - stop_loss| / entry_price
    DEFAULT_RISK = 0.07

    if "entry_price" in df.columns and "stop_loss" in df.columns:
        entry = pd.to_numeric(df["entry_price"], errors="coerce")
        stop = pd.to_numeric(df.get("stop_loss", pd.Series(np.nan, index=df.index)), errors="coerce")
        risk_pct = np.where(
            (entry.notna()) & (stop.notna()) & (entry != 0),
            (entry - stop).abs() / entry,
            DEFAULT_RISK,
        )
    else:
        risk_pct = np.full(len(df), DEFAULT_RISK)

    risk_pct = np.where(risk_pct <= 0, DEFAULT_RISK, risk_pct)
    df["risk_pct"] = risk_pct
    df["r_multiple"] = (df["pnl_pct"] / 100.0) / risk_pct

    return df.reset_index(drop=True)


def load_equity_curve_from_db(
    db_path: Path = _DEFAULT_DB,
    days: int = 90,
) -> pd.Series:
    """
    Reconstruct daily equity curve from trades.
    Groups pnl_pct by date, computes average daily return,
    fills missing days (weekdays) with 0% return, and
    returns a pd.Series of cumulative equity (starting at 1.0).
    """
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    today_str = datetime.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in cursor.fetchall()]
        date_col = "scan_date" if "scan_date" in cols else ("entry_date" if "entry_date" in cols else "created_at")

        query = f"""
            SELECT {date_col} AS trade_date, AVG(pnl_pct) AS avg_pnl_pct
            FROM trades
            WHERE {date_col} >= ?
              AND pnl_pct IS NOT NULL
            GROUP BY {date_col}
            ORDER BY {date_col} ASC
        """
        df = pd.read_sql_query(query, conn, params=(cutoff,))
    finally:
        conn.close()

    if df.empty:
        idx = pd.date_range(start=cutoff, end=today_str, freq="B")
        return pd.Series(np.ones(len(idx)), index=idx)

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date")["avg_pnl_pct"] / 100.0

    # Reindex to business day range with 0 for missing days
    full_idx = pd.date_range(
        start=df.index.min(),
        end=max(df.index.max(), pd.Timestamp(today_str)),
        freq="B",
    )
    daily_returns = df.reindex(full_idx).fillna(0.0)

    # Cumulative product → equity curve starting at 1.0
    equity = (1 + daily_returns).cumprod()
    equity.iloc[0] = (1 + daily_returns.iloc[0])  # ensure first bar is correct
    return equity


def load_regime_labels_from_db(
    db_path: Path = _DEFAULT_DB,
    days: int = 90,
) -> pd.Series:
    """Return date-indexed Series of regime labels from market_breadth_enhanced."""
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT date, signal FROM market_breadth_enhanced WHERE date >= ? ORDER BY date",
            conn,
            params=(cutoff,),
        )
    finally:
        conn.close()

    if df.empty:
        return pd.Series(dtype=str)

    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["signal"]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def cmd_scorecard(params: dict) -> None:
    """
    Main command: load data, compute scorecard, print formatted Arabic report,
    and save JSON to data/institutional_scorecard.json.
    """
    db_path = Path(params.get("db", str(_DEFAULT_DB)))
    days = int(params.get("days", 90))
    as_json = bool(params.get("json", False))

    # ---- Load data -------------------------------------------------------
    trades = load_trades_from_db(db_path, days=days)
    equity = load_equity_curve_from_db(db_path, days=days)
    regimes = load_regime_labels_from_db(db_path, days=days)

    # ---- Compute scorecard -----------------------------------------------
    sc = compute_full_scorecard(trades, equity, regimes)

    # ---- Monte Carlo -----------------------------------------------------
    if sc.n_trades >= 5:
        mc = monte_carlo_stress(trades, n_sims=10_000, n_trades_forward=100)
        sc.mc_p5_dd = mc["p5_dd"]
        sc.mc_p1_dd = mc["p1_dd"]
        sc.mc_ruin_prob = mc["ruin_prob"]

    # ---- JSON output -----------------------------------------------------
    sc_dict = asdict(sc)

    if as_json:
        print(json.dumps(sc_dict, indent=2, ensure_ascii=False))
    else:
        _print_report(sc, days)

    # ---- Save to disk ----------------------------------------------------
    _SCORECARD_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(_SCORECARD_JSON, "w", encoding="utf-8") as fh:
        json.dump(sc_dict, fh, indent=2, ensure_ascii=False)


def _print_report(sc: InstitutionalScorecard, days: int) -> None:
    """Print Arabic-formatted institutional scorecard to stdout."""

    if sc.institutional_grade == "INSUFFICIENT_DATA":
        print(f"\n⚠️  بيانات غير كافية (أقل من 5 صفقات في آخر {days} يوم)")
        return

    grade_emoji = {"A": "🏆", "B": "✅", "C": "⚠️", "D": "❌"}.get(sc.institutional_grade, "❓")
    deployment_text = "نعم ✅" if sc.deployment_ready else "لا ❌"

    bar = "━" * 34

    cagr_pct = f"{sc.cagr * 100:.1f}%"
    mdd_pct = f"{sc.max_drawdown * 100:.1f}%"
    wr_pct = f"{sc.win_rate * 100:.1f}%"
    ulcer_pct = f"{sc.ulcer_index:.2f}%"
    mc_p5_pct = f"{sc.mc_p5_dd * 100:.1f}%"
    mc_p1_pct = f"{sc.mc_p1_dd * 100:.1f}%"
    ruin_pct = f"{sc.mc_ruin_prob * 100:.1f}%"

    print(f"""
📊 بطاقة الأداء المؤسسي ({days} يوم)  [{sc.n_trades} صفقة]
{bar}
  العوائد:
    CAGR: {cagr_pct}   Sharpe: {sc.sharpe:.2f}   Sortino: {sc.sortino:.2f}   Calmar: {sc.calmar:.2f}   Omega: {sc.omega:.2f}
  المخاطر:
    Max DD: {mdd_pct}   Ulcer: {ulcer_pct}   Recovery: {sc.avg_recovery_days} أيام
  الصفقات:
    WR: {wr_pct}   PF: {sc.profit_factor:.2f}   Expectancy: {sc.expectancy_r:+.2f}R   Max Loss Streak: {sc.max_consec_losses}
    Avg Win: {sc.avg_win_r:+.2f}R   Avg Loss: {sc.avg_loss_r:+.2f}R
  الاستقرار:
    Rolling Sharpe 30d: {sc.rolling_sharpe_30d:.2f}   90d: {sc.rolling_sharpe_90d:.2f}   Stability: {sc.stability_score:.0f}/100
    Regime Robustness: {sc.regime_robustness}
  Monte Carlo (10,000 sim):
    VaR (p5 DD): {mc_p5_pct}   CVaR (p1 DD): {mc_p1_pct}   Ruin Risk: {ruin_pct}
  {bar}
  الدرجة المؤسسية: {sc.institutional_grade} {grade_emoji}   جاهز للنشر: {deployment_text}
""")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EGX Navigator — Institutional Metrics Engine"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of past days to analyze (default: 90)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output scorecard as JSON instead of formatted report",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(_DEFAULT_DB),
        help=f"Path to SQLite DB (default: {_DEFAULT_DB})",
    )

    args = parser.parse_args()
    cmd_scorecard({"days": args.days, "json": args.json, "db": args.db})
