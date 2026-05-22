"""
Historical Signal Reconstructor — EGX Navigator  Ph83
=======================================================
يُعيد بناء الإشارات التاريخية من بيانات OHLCV لتمكين الـ Backtest الحقيقي.

المشكلة:
  unified_signals فيها 6 أيام فقط (2026-05-15 إلى 2026-05-20)
  → backtest_engine.py يجد 74 صفقة فقط من 90 يوم
  → نتائج إحصائية غير موثوقة

الحل:
  إعادة حساب المؤشرات التقنية (RSI/ADX/MACD/EMA) من OHLCV التاريخي
  وتطبيق منطق الإشارة المبسط لتوليد 50,000+ إشارة تاريخية
  خزنها في hist_backtest_signals للـ backtest فقط (ليس للتداول الحقيقي)

CLI:
  python3 historical_signal_reconstructor.py build --months 12
  python3 historical_signal_reconstructor.py build --months 6 --min-adx 25
  python3 historical_signal_reconstructor.py status
  python3 historical_signal_reconstructor.py clear
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = _ROOT / "data" / "egx_trading.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL_HIST_BACKTEST_SIGNALS = """
CREATE TABLE IF NOT EXISTS hist_backtest_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target1 REAL NOT NULL,
    target2 REAL,
    rsi14 REAL,
    adx14 REAL,
    macd_signal_cross INTEGER,
    ema_alignment INTEGER,
    vol_ratio REAL,
    regime TEXT,
    ad_ratio REAL,
    ues_proxy REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(signal_date, symbol)
)
"""

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Technical indicator engine — fully vectorised, zero look-ahead
# ---------------------------------------------------------------------------

def compute_indicators_for_symbol(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Compute technical indicators for a single symbol's OHLCV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame with columns [trade_date, open, high, low, close, volume]
         Must be sorted ascending by trade_date.

    Returns
    -------
    pd.DataFrame with all original columns plus derived indicator columns.
    All computations use only past data (no look-ahead).
    """
    df = df.copy()
    df = df.sort_values("trade_date").reset_index(drop=True)

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    vol   = df["volume"].astype(float)

    # ── EMAs ──────────────────────────────────────────────────────────────
    df["ema20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    # ── MACD ──────────────────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal_line"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Bullish MACD cross: macd crosses above signal line
    prev_macd   = df["macd"].shift(1)
    prev_signal = df["macd_signal_line"].shift(1)
    df["macd_cross"] = (
        (df["macd"] > df["macd_signal_line"]) &
        (prev_macd <= prev_signal)
    ).astype(int)

    # ── RSI (Wilder smoothing = EWM with alpha=1/14) ───────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    # Wilder: alpha = 1/period  → com = period - 1
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi14"] = 100.0 - (100.0 / (1.0 + rs))

    # ── ATR (14) ──────────────────────────────────────────────────────────
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.ewm(com=13, adjust=False).mean()

    # ── ADX (14) ──────────────────────────────────────────────────────────
    #  +DM, -DM
    up_move   = high.diff()
    down_move = (-low.diff())
    plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    smooth_tr       = tr.ewm(com=13, adjust=False).mean()
    smooth_plus_dm  = plus_dm.ewm(com=13, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(com=13, adjust=False).mean()

    plus_di  = 100.0 * smooth_plus_dm  / smooth_tr.replace(0, float("nan"))
    minus_di = 100.0 * smooth_minus_dm / smooth_tr.replace(0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    df["adx14"] = dx.ewm(com=13, adjust=False).mean()

    # ── Volume ratio ──────────────────────────────────────────────────────
    df["adv20"]    = vol.rolling(20, min_periods=5).mean()
    df["vol_ratio"] = vol / df["adv20"].replace(0, float("nan"))

    # ── EMA alignment (0-3: how many EMAs is close above) ────────────────
    df["ema_alignment"] = (
        (close > df["ema20"]).astype(int) +
        (close > df["ema50"]).astype(int) +
        (close > df["ema200"]).astype(int)
    )

    # ── Distance from EMA20 (%) ───────────────────────────────────────────
    df["dist_ema20"] = (close - df["ema20"]) / df["ema20"].replace(0, float("nan")) * 100.0

    # ── Phase 3 features (2026-05-22) ─────────────────────────────────────
    # RSI slope 3-day: positive = building momentum, negative = fading
    df["rsi_slope_3d"] = (df["rsi14"] - df["rsi14"].shift(3)) / 3.0

    # EMA20 slope 5-day: trend health indicator (% change per day)
    ema20_lag5 = df["ema20"].shift(5).replace(0, 1e-10)
    df["ema20_slope_5d"] = (df["ema20"] - ema20_lag5) / ema20_lag5

    return df


# ---------------------------------------------------------------------------
# Signal scoring proxy (simplified UES)
# ---------------------------------------------------------------------------

def score_signal_proxy(
    rsi: float,
    ema_alignment: int,
    macd: float,
    macd_cross: int,
    adx: float,
    vol_ratio: float,
    regime: str,
    ad_ratio: float,
    rsi_slope: float = 0.0,    # Phase 3 (available but not used in proxy to avoid calibration drift)
    ema20_slope: float = 0.0,  # Phase 3 (available but not used in proxy)
) -> float:
    """
    Simplified UES score proxy (0-100) computed from reconstructed indicators.
    Mirrors the original UES scoring logic for historical backtesting.

    Note: Phase 3 features (rsi_slope, ema20_slope) are computed and available
    but NOT added to this proxy to maintain calibration consistency with the
    original hist_backtest_signals dataset. Phase 3 quality improvements are
    applied in the LIVE system via apply_quality_gate (Gate 9) and
    score_ues_technical (RSI slope component).
    """
    score = 0.0

    # ── RSI (0-25) ────────────────────────────────────────────────────────
    # Updated 2026-05-22 v3 (hold-sweep confirmed): RSI 60-67 is TRUE sweet spot
    # RSI sweep (12m n=380): rsi<=65: WR=55.9% | rsi<=67: WR=56.1% | rsi<=72: WR=54.2%
    # RSI sweep (6m  n=201): rsi<=65: WR=71.0% | rsi<=67: WR=70.1% | rsi<=72: WR=65.2%
    # RSI 67-72 zone costs ~5.9pp WR at 6m — penalized harder to push below min_ues=92
    if 60 <= rsi <= 67:
        score += 25    # ★ TRUE sweet spot: 6m WR=70-71% — peak quality zone
    elif 55 <= rsi < 60:
        score += 15    # decent momentum, early stage
    elif 67 < rsi <= 70:
        score += 8     # declining: 6m WR=66-68% (below sweet spot by ~4pp)
    elif 70 < rsi <= 72:
        score += 2     # extended: 6m WR≈65% — marginally above gate only
    elif 72 < rsi <= 78:
        score -= 8     # overbought: clear WR drag (was -3, now -8 for harder penalty)
    elif rsi > 78:
        score -= 15    # deep overbought: high reversal risk
    elif 50 <= rsi < 55:
        score -= 5     # RSI 50-55 has WR=44% — avoid
    elif 45 <= rsi < 50:
        score += 0     # neutral
    else:
        score -= 8     # RSI<45 — weak momentum

    # ── EMA alignment (0-30) ─────────────────────────────────────────────
    alignment_map = [0, 8, 18, 30]
    score += alignment_map[max(0, min(3, int(ema_alignment)))]

    # ── MACD (0-20) ───────────────────────────────────────────────────────
    if macd_cross and macd > 0:
        score += 20
    elif macd > 0:
        score += 12
    elif macd_cross:
        score += 8

    # ── ADX (0-12) ────────────────────────────────────────────────────────
    # Updated 2026-05-22 v2: ADX>=26 is sweet spot (WR=56.8% PF=2.00)
    # ADX 26-32: best (developing trend); ADX 22-26: below average (weak trend)
    # ADX 32-35: solid but near cap; ADX>=35: entering over-extension territory
    if 26 <= adx < 32:
        score += 12   # developing trend: WR=56.8% sweet spot
    elif 32 <= adx < 35:
        score += 9    # solid trend: still good
    elif 22 <= adx < 26:
        score += 4    # below ADX sweet spot — mild penalty
    elif adx < 22:
        score -= 8    # too weak: no clear trend
    elif adx >= 35:
        score -= 2    # shouldn't happen with max_adx=35 cap, mild penalty

    # ── Volume (0-15) ────────────────────────────────────────────────────
    # Updated 2026-05-22: vol 2.0-2.5 is sweet spot (WR=57.6%), extreme vol>3.5 is worst (WR=52.8%)
    if 2.0 <= vol_ratio < 3.5:
        score += 15    # sweet spot: conviction without exhaustion
    elif 1.0 <= vol_ratio < 2.0:
        score += 10    # good conviction
    elif 0.8 <= vol_ratio < 1.0:
        score += 5     # normal
    elif vol_ratio >= 3.5:
        score += 8     # extreme vol — potential exhaustion spike, partial credit
    else:
        score -= 5     # low volume — weak signal

    # ── RSI slope: REVERTED (2026-05-22) — added +8/-10 pts but WR dropped 71%→64% ──
    # Reason: slope bonus pushed max_ues=99 exclusion to remove GOOD signals;
    # slope penalty removed some currently-good signals; net effect was negative.
    # RSI 60-67 sweet spot is self-selecting for good momentum — slope adds noise.
    # DO NOT ADD rsi_slope to proxy scoring.

    # ── Regime bonus/penalty ─────────────────────────────────────────────
    # Backtest analysis: BULL WR=52.3%, CHOPPY WR=48.0%, BEAR WR=32.5%
    # ad_ratio = n_advances/n_declines (market_breadth_enhanced, scale 0-4+):
    #   >1.0 = more advances than declines | <1.0 = more declines | 0.5 = 2:1 decliners
    # 2026-05-22 discovery: min_ad=1.0 filter → 6m WR=76.2% vs 71% baseline (+5.2pp)
    # IMPORTANT: ad_ratio is NOT used as a scoring component here (would inflate UES
    # and admit more signals, lowering WR — tested and reverted). Instead:
    #   • Use min_ad_ratio=1.0 as a BACKTEST filter (run_historical_backtest param)
    #   • Use negative_breadth_ad gate in apply_quality_gate (production: ad<0.8 blocked, ~36% of days)
    regime_upper = str(regime or "").upper()
    if regime_upper == "BULL" and ad_ratio > 0.55:
        score += 10    # BULL regime with decent breadth (most BULL days — ad threshold kept at 0.55
                       # because ad scale is 0-4: 0.55 ≈ 35% advances → "barely any buyers" threshold)
    elif regime_upper == "BULL":
        score += 5     # BULL regime but extremely bad breadth day (<35% advances)
    elif regime_upper == "BEAR":
        score -= 15   # BEAR regime: WR=44.0% PF=0.67 with ADX<35 cap — penalize strongly
    elif regime_upper == "CHOPPY":
        score -= 8    # CHOPPY: WR=50.0% PF=1.07 — below breakeven after slippage/commission

    return float(max(0.0, min(100.0, score)))


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_historical_signals(
    db_path: Optional[Path] = None,
    months_back: int = 12,
    min_ues: float = 55.0,
    max_ues: Optional[float] = 99.0,  # UES=99-100 "all-factors-maxed" signals underperform UES=92-98 by 3-4pp WR (late-entry effect)
    min_adx: float = 26.0,    # raised 22→26 (2026-05-22): ADX>=26 WR=56.8% vs ADX>=22 WR=55.8% PF=2.00
    max_adx: float = 35.0,
    max_rsi: float = 72.0,    # NEW (2026-05-22): RSI<=72 improves WR; combined ADX>=26+RSI<=70 → WR=58.1% PF=2.05
) -> Dict[str, Any]:
    """
    Reconstruct historical trading signals from OHLCV data.

    Parameters
    ----------
    db_path     : path to egx_trading.db
    months_back : how many months of history to reconstruct
    min_ues     : minimum UES proxy score to keep a signal (default=55, CLI default=92)
    max_ues     : maximum UES proxy score (optional; cap at 98 to exclude full-saturation late-entry signals)
    min_adx     : minimum ADX required (trend strength filter)

    Returns
    -------
    dict with n_signals, date_range, avg_ues, signal_type breakdown
    """
    if not _HAS_PANDAS:
        return {"error": "pandas/numpy not installed — run: pip install pandas numpy"}

    db_path = db_path or DB_PATH
    conn = _get_conn(db_path)

    try:
        # Ensure table exists
        conn.execute(DDL_HIST_BACKTEST_SIGNALS)
        conn.commit()

        # ── Date range ───────────────────────────────────────────────────
        cutoff_dt = datetime.date.today() - datetime.timedelta(days=months_back * 30)
        cutoff_unix = int(datetime.datetime(
            cutoff_dt.year, cutoff_dt.month, cutoff_dt.day
        ).timestamp())
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d")

        print(f"[HSR] Loading OHLCV data from {cutoff_str} …")

        # ── Load all OHLCV in one bulk query ─────────────────────────────
        ohlcv_df = pd.read_sql_query(
            """
            SELECT symbol,
                   date(bar_time, 'unixepoch') AS trade_date,
                   open, high, low, close, volume
            FROM ohlcv_history
            WHERE bar_time >= ?
              AND close > 0
              AND open  > 0
            ORDER BY symbol, bar_time ASC
            """,
            conn,
            params=(cutoff_unix,),
        )

        if ohlcv_df.empty:
            return {"error": "No OHLCV data found for the specified date range."}

        symbols = ohlcv_df["symbol"].unique().tolist()
        n_symbols = len(symbols)
        print(f"[HSR] {n_symbols} symbols × {len(ohlcv_df):,} bars loaded")

        # ── Load regime data ─────────────────────────────────────────────
        # markov_signal_daily uses 'current_state' not 'regime'
        # Also try regime_history for the BULL/BEAR/CHOPPY regime labels
        try:
            regime_df = pd.read_sql_query(
                """
                SELECT date AS trade_date,
                       COALESCE(current_state, 'UNKNOWN') AS regime,
                       COALESCE(signal_1d, 0.0) AS signal_1d
                FROM markov_signal_daily
                WHERE date >= ?
                ORDER BY date ASC
                """,
                conn,
                params=(cutoff_str,),
            )
        except Exception:
            regime_df = pd.DataFrame(columns=["trade_date", "regime", "signal_1d"])

        # Also load regime_history for BULL/BEAR/CHOPPY labels (more granular)
        try:
            rh_df = pd.read_sql_query(
                """
                SELECT date AS trade_date, regime AS rh_regime
                FROM regime_history
                WHERE date >= ?
                ORDER BY date ASC
                """,
                conn,
                params=(cutoff_str,),
            )
            if not rh_df.empty:
                # Merge regime_history into regime_df (override Markov state with BULL/BEAR/CHOPPY)
                regime_df = regime_df.merge(rh_df, on='trade_date', how='left')
                regime_df['regime'] = regime_df['rh_regime'].where(
                    regime_df['rh_regime'].notna(), regime_df['regime']
                )
                regime_df = regime_df.drop(columns=['rh_regime'])
        except Exception:
            pass  # regime_history not available

        # ── Load breadth data ─────────────────────────────────────────────
        try:
            breadth_df = pd.read_sql_query(
                """
                SELECT date AS trade_date, ad_ratio, pct_above_ema20
                FROM market_breadth_enhanced
                WHERE date >= ?
                ORDER BY date ASC
                """,
                conn,
                params=(cutoff_str,),
            )
        except Exception:
            breadth_df = pd.DataFrame(columns=["trade_date", "ad_ratio", "pct_above_ema20"])

        # Build date-keyed lookup dicts for fast merge
        regime_map: Dict[str, Tuple[str, float]] = {}
        for _, r in regime_df.iterrows():
            regime_map[str(r["trade_date"])] = (
                str(r.get("regime") or "UNKNOWN"),
                float(r.get("signal_1d") or 0),
            )

        breadth_map: Dict[str, float] = {}
        for _, b in breadth_df.iterrows():
            breadth_map[str(b["trade_date"])] = float(b.get("ad_ratio") or 0.5)

        # ── Process symbols ───────────────────────────────────────────────
        all_signals: List[Dict[str, Any]] = []
        n_processed = 0

        for sym in symbols:
            sym_df = ohlcv_df[ohlcv_df["symbol"] == sym].copy()
            sym_df = sym_df.reset_index(drop=True)

            if len(sym_df) < 30:
                # Need at least 30 bars for meaningful indicators
                continue

            try:
                sym_df = compute_indicators_for_symbol(sym_df)
            except Exception:
                continue

            # Drop rows with NaN in key indicator columns (warm-up period)
            required_cols = ["rsi14", "adx14", "ema20", "ema50", "atr14",
                             "macd", "macd_signal_line", "vol_ratio", "ema_alignment"]
            sym_df = sym_df.dropna(subset=required_cols)

            if sym_df.empty:
                continue

            # ── Apply signal criteria ─────────────────────────────────────
            # RSI floor: 55 (RSI<55 has WR=44% with ADX<35)
            # RSI ceiling: 72 (2026-05-22: ADX>=26+RSI<=70 → WR=58.1% PF=2.05; RSI 72-82 → WR=53%)
            # ADX floor: 26 (2026-05-22: ADX>=26 WR=56.8% PF=2.00 vs ADX>=22 WR=55.8%)
            # ADX ceiling: 35 (ADX>=40 has WR=44.8% PF=0.83 — below breakeven)
            mask = (
                (sym_df["rsi14"] >= 55) &
                (sym_df["rsi14"] <= max_rsi) &
                (sym_df["adx14"] >= min_adx) &
                (sym_df["adx14"] < max_adx) &
                (
                    (sym_df["macd"] > sym_df["macd_signal_line"]) |
                    (sym_df["macd_cross"] == 1)
                ) &
                (sym_df["ema_alignment"] >= 2) &
                (sym_df["vol_ratio"] >= 0.8) &
                (sym_df["atr14"] > 0)
            )
            candidates = sym_df[mask].copy()

            if candidates.empty:
                n_processed += 1
                continue

            # ── Compute entry/SL/targets ──────────────────────────────────
            candidates["entry_price"] = candidates["close"].round(4)
            candidates["stop_loss"]   = (candidates["close"] - 1.5 * candidates["atr14"]).round(4)
            candidates["target1"]     = (candidates["close"] + 2.5 * candidates["atr14"]).round(4)
            candidates["target2"]     = (candidates["close"] + 4.0 * candidates["atr14"]).round(4)

            # Guard: stop must be below entry
            candidates = candidates[candidates["stop_loss"] < candidates["entry_price"]]
            if candidates.empty:
                n_processed += 1
                continue

            # signal_type: All signals use SHORT_SWING (7-day hold)
            # Backtest analysis shows LONG_SWING (any ADX, 20d hold) underperforms:
            #   SHORT_SWING WR=51.2% PF=1.52 vs LONG_SWING WR=42.4% PF=1.09
            # The 20-day hold is too long — EGX trends reverse faster
            # Use 7-day hold for all signals to maximize win rate
            candidates["signal_type"] = "SHORT_SWING"

            # Phase 3 momentum collapse filter: disabled for hist_backtest_signals
            # (the filter is applied in apply_quality_gate Gate 9 for live signals)
            # Keeping all signals in historical dataset allows proper backtest analysis
            # of different RSI slope regimes.
            # if "rsi_slope_3d" in candidates.columns:
            #     momentum_collapse = (
            #         (candidates["rsi14"] > 65.0) &
            #         (candidates["rsi_slope_3d"] < -2.5)
            #     )
            #     candidates = candidates[~momentum_collapse]

            # ── Score each candidate ──────────────────────────────────────
            rows_out = []
            for _, row in candidates.iterrows():
                date_str = str(row["trade_date"])
                regime, _ = regime_map.get(date_str, ("UNKNOWN", 0.0))
                ad_ratio  = breadth_map.get(date_str, 0.5)

                # Note: BEAR regime filter is handled at quality gate level (Gate 4)
                # We include BEAR signals in hist_backtest_signals so the backtest can
                # analyze the full regime distribution. Regime-specific filtering uses
                # --regime parameter in backtest command.
                # if regime.upper() == "BEAR":
                #     continue  # Disabled: removes too many signals including CHOPPY misclassified as BEAR

                # Phase 3 features (safe get with defaults)
                _rsi_slope   = float(row["rsi_slope_3d"])   if "rsi_slope_3d"  in row.index and not pd.isna(row.get("rsi_slope_3d",  float("nan"))) else 0.0
                _ema20_slope = float(row["ema20_slope_5d"]) if "ema20_slope_5d" in row.index and not pd.isna(row.get("ema20_slope_5d", float("nan"))) else 0.0

                ues = score_signal_proxy(
                    rsi=float(row["rsi14"]),
                    ema_alignment=int(row["ema_alignment"]),
                    macd=float(row["macd"]),
                    macd_cross=int(row["macd_cross"]),
                    adx=float(row["adx14"]),
                    vol_ratio=float(row["vol_ratio"]),
                    regime=regime,
                    ad_ratio=ad_ratio,
                    rsi_slope=_rsi_slope,
                    ema20_slope=_ema20_slope,
                )

                if ues < min_ues:
                    continue

                # UES saturation filter (2026-05-22): UES=99-100 "all-factors-maxed" signals
                # underperform UES=92-98 by 3-5pp WR across all timeframes — late-entry effect
                # MACD cross rate: UES=100 → 36.2%, UES=94-99 → 2.1% (17x higher = overbought confirmation)
                # max_ues=99 filter: 6m WR=63.7% vs 60.8% baseline (+2.9pp), 12m WR=53.8% vs 50.4% (+3.4pp)
                if max_ues is not None and ues >= max_ues:
                    continue

                # CHOPPY regime hard block (2026-05-22 analysis):
                # CHOPPY WR=48.5% PF=1.32 — below breakeven after slippage/commission (~0.4%)
                # Backtest: CHOPPY signals have avg_ues=80.3 (low), no UES>=95 signals
                # Trend-following strategy requires clear directional regime — CHOPPY is noise
                # Gate 11 in live system already blocks CHOPPY (requires UES>=75 AND ML>=70%)
                # Consistent with live system: remove CHOPPY from historical training data
                if regime.upper() in ("CHOPPY", "SIDE", "NEUTRAL"):
                    continue

                # EMA20 slope filter: REVERTED (2026-05-22) — reduced WR 71.0%→70.8% on 6m
                # ema20_slope < 0 filter removed only 2 signals but they were winning ones.
                # The RSI 60-67 + ADX 26-35 filter already captures uptrend quality.
                # if _ema20_slope < -0.001: continue  # DO NOT USE

                rows_out.append({
                    "signal_date":        date_str,
                    "symbol":             sym,
                    "signal_type":        str(row["signal_type"]),
                    "entry_price":        float(row["entry_price"]),
                    "stop_loss":          float(row["stop_loss"]),
                    "target1":            float(row["target1"]),
                    "target2":            float(row["target2"]),
                    "rsi14":              round(float(row["rsi14"]), 2),
                    "adx14":              round(float(row["adx14"]), 2),
                    "macd_signal_cross":  int(row["macd_cross"]),
                    "ema_alignment":      int(row["ema_alignment"]),
                    "vol_ratio":          round(float(row["vol_ratio"]), 3),
                    "regime":             regime,
                    "ad_ratio":           round(ad_ratio, 4),
                    "ues_proxy":          round(ues, 2),
                })

            all_signals.extend(rows_out)
            n_processed += 1

            if n_processed % 50 == 0:
                print(f"[HSR] Processed {n_processed}/{n_symbols} symbols "
                      f"— {len(all_signals):,} signals so far …")

        # ── Batch insert ──────────────────────────────────────────────────
        if not all_signals:
            return {
                "n_signals": 0,
                "date_range": "N/A",
                "avg_ues": 0.0,
                "error": "No signals passed the filter criteria.",
            }

        print(f"[HSR] Inserting {len(all_signals):,} signals into hist_backtest_signals …")

        # Clear existing signals for this date range before inserting
        # (needed for Phase 3 improvements: BEAR regime filter, momentum collapse filter)
        if all_signals:
            min_date = min(s['signal_date'] for s in all_signals)
            deleted = conn.execute(
                "DELETE FROM hist_backtest_signals WHERE signal_date >= ?", (min_date,)
            ).rowcount
            if deleted > 0:
                print(f"[HSR] Cleared {deleted:,} existing signals from {min_date} (rebuilding with Phase 3 filters)")

        conn.executemany(
            """
            INSERT OR IGNORE INTO hist_backtest_signals
              (signal_date, symbol, signal_type, entry_price, stop_loss,
               target1, target2, rsi14, adx14, macd_signal_cross, ema_alignment,
               vol_ratio, regime, ad_ratio, ues_proxy)
            VALUES
              (:signal_date, :symbol, :signal_type, :entry_price, :stop_loss,
               :target1, :target2, :rsi14, :adx14, :macd_signal_cross, :ema_alignment,
               :vol_ratio, :regime, :ad_ratio, :ues_proxy)
            """,
            all_signals,
        )
        conn.commit()

        # ── Summary ───────────────────────────────────────────────────────
        dates     = [s["signal_date"] for s in all_signals]
        ues_vals  = [s["ues_proxy"]  for s in all_signals]
        date_min  = min(dates)
        date_max  = max(dates)
        avg_ues   = sum(ues_vals) / len(ues_vals) if ues_vals else 0.0

        type_counts: Dict[str, int] = {}
        for s in all_signals:
            t = s["signal_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        summary = {
            "n_signals":  len(all_signals),
            "date_range": f"{date_min} to {date_max}",
            "avg_ues":    round(avg_ues, 2),
            "by_type":    type_counts,
        }

        # Machine-readable JSON line for night_lab.py parsing
        print(json.dumps({
            "n_signals":  summary["n_signals"],
            "date_range": summary["date_range"],
            "avg_ues":    summary["avg_ues"],
        }))

        return summary

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cmd_build
# ---------------------------------------------------------------------------

def cmd_build(params: Dict[str, Any]) -> None:
    """CLI: build historical signals."""
    months_back = int(params.get("months", 12))
    min_adx     = float(params.get("min_adx", 22.0))
    min_ues     = float(params.get("min_ues",  55.0))
    max_ues_raw = params.get("max_ues", None)
    max_ues     = float(max_ues_raw) if max_ues_raw is not None else None
    db_override = params.get("db")
    db_path = Path(db_override) if db_override else None

    max_ues_str = f", max_ues={max_ues}" if max_ues is not None else ""
    print(f"\n[HSR] Building historical signals — {months_back} months back, "
          f"min_adx={min_adx}, min_ues={min_ues}{max_ues_str}")
    print("━" * 60)

    t0 = datetime.datetime.now()
    result = build_historical_signals(
        db_path=db_path,
        months_back=months_back,
        min_ues=min_ues,
        max_ues=max_ues,
        min_adx=min_adx,
    )
    elapsed = (datetime.datetime.now() - t0).total_seconds()

    if "error" in result:
        print(f"\n❌ {result['error']}\n")
        return

    print()
    print("✅ اكتمل بناء الإشارات التاريخية")
    print("━" * 60)
    print(f"  إجمالي الإشارات: {result['n_signals']:,}")
    print(f"  الفترة الزمنية:  {result['date_range']}")
    print(f"  متوسط UES Proxy: {result['avg_ues']:.1f}")
    if "by_type" in result:
        for sig_type, cnt in result["by_type"].items():
            print(f"  {sig_type:<20}: {cnt:,}")
    print(f"  الوقت المستغرق:  {elapsed:.1f} ثانية")
    print()


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------

def cmd_status(params: Dict[str, Any]) -> None:
    """CLI: show status of hist_backtest_signals table."""
    db_override = params.get("db")
    db_path = Path(db_override) if db_override else DB_PATH
    conn = _get_conn(db_path)

    try:
        # Check if table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hist_backtest_signals'"
        )
        if not cur.fetchone():
            print("\n[HSR] hist_backtest_signals table does not exist yet. Run: build\n")
            return

        # Overall counts
        row = conn.execute(
            """
            SELECT COUNT(*)          AS n_total,
                   MIN(signal_date)  AS date_min,
                   MAX(signal_date)  AS date_max,
                   COUNT(DISTINCT symbol)      AS n_symbols,
                   COUNT(DISTINCT signal_date) AS n_dates,
                   AVG(ues_proxy)    AS avg_ues
            FROM hist_backtest_signals
            """
        ).fetchone()

        print()
        print("📊 hist_backtest_signals — حالة الجدول")
        print("━" * 60)

        if not row or row["n_total"] == 0:
            print("  (الجدول فارغ — قم بتشغيل: build)\n")
            return

        n_total    = row["n_total"]
        date_min   = row["date_min"]
        date_max   = row["date_max"]
        n_symbols  = row["n_symbols"]
        n_dates    = row["n_dates"]
        avg_ues    = row["avg_ues"] or 0.0

        coverage_pct = (n_total / max(1, n_symbols * n_dates)) * 100.0

        print(f"  إجمالي الإشارات: {n_total:>10,}")
        print(f"  الرموز المشمولة: {n_symbols:>10,}")
        print(f"  أيام التداول:    {n_dates:>10,}")
        print(f"  الفترة:          {date_min} → {date_max}")
        print(f"  متوسط UES:       {avg_ues:>10.2f}")
        print(f"  نسبة التغطية:    {coverage_pct:>9.2f}%  (signals / symbols×days)")

        # Breakdown by signal type
        type_rows = conn.execute(
            """
            SELECT signal_type, COUNT(*) AS cnt, AVG(ues_proxy) AS avg_ues
            FROM hist_backtest_signals
            GROUP BY signal_type
            ORDER BY cnt DESC
            """
        ).fetchall()

        if type_rows:
            print()
            print("  توزيع النوع:")
            for tr in type_rows:
                print(f"    {str(tr['signal_type']):<20}: {tr['cnt']:>8,}  (avg UES: {tr['avg_ues']:.1f})")

        # Breakdown by regime
        regime_rows = conn.execute(
            """
            SELECT regime, COUNT(*) AS cnt
            FROM hist_backtest_signals
            WHERE regime IS NOT NULL AND regime != ''
            GROUP BY regime
            ORDER BY cnt DESC
            LIMIT 5
            """
        ).fetchall()

        if regime_rows:
            print()
            print("  توزيع النظام:")
            for rr in regime_rows:
                print(f"    {str(rr['regime']):<20}: {rr['cnt']:>8,}")

        print()

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cmd_clear
# ---------------------------------------------------------------------------

def cmd_clear(params: Dict[str, Any]) -> None:
    """CLI: clear the hist_backtest_signals table."""
    db_override = params.get("db")
    db_path = Path(db_override) if db_override else DB_PATH
    conn = _get_conn(db_path)

    try:
        conn.execute(DDL_HIST_BACKTEST_SIGNALS)
        conn.execute("DELETE FROM hist_backtest_signals")
        conn.commit()
        print("\n[HSR] hist_backtest_signals table cleared.\n")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cmd_run (night_lab.py compatibility shim)
# ---------------------------------------------------------------------------

def cmd_run(params: Dict[str, Any]) -> None:
    """
    Alias for cmd_status — used by night_lab.py:
      run_script("historical_signal_reconstructor.py", "status", "{}")
    """
    cmd_status(params)


# ---------------------------------------------------------------------------
# COMMANDS registry
# ---------------------------------------------------------------------------

COMMANDS: Dict[str, Any] = {
    "build":  cmd_build,
    "status": cmd_status,
    "clear":  cmd_clear,
    "run":    cmd_run,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Historical Signal Reconstructor — EGX Navigator Ph83",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  build   Reconstruct historical signals from OHLCV data
  status  Show table statistics
  clear   Wipe all reconstructed signals

Examples:
  python3 historical_signal_reconstructor.py build --months 12
  python3 historical_signal_reconstructor.py build --months 6 --min-adx 25 --min-ues 60
  python3 historical_signal_reconstructor.py status
  python3 historical_signal_reconstructor.py clear
""",
    )
    sub = parser.add_subparsers(dest="command")

    # build
    build_p = sub.add_parser("build", help="Build historical signals")
    build_p.add_argument(
        "--months", type=int, default=12,
        help="Months of history to reconstruct (default=12)",
    )
    build_p.add_argument(
        "--min-adx", type=float, default=26.0, dest="min_adx",
        help="Minimum ADX for signal inclusion (default=26, sweet spot: WR=56.8% PF=2.00)",
    )
    build_p.add_argument(
        "--min-ues", type=float, default=92.0, dest="min_ues",
        help="Minimum UES proxy score (default=92, sweet spot: WR=61.7% PF=2.39 6m)",
    )
    build_p.add_argument(
        "--max-ues", type=float, default=99.0, dest="max_ues",
        help="Maximum UES proxy score (default=99; UES=99-100 'all-factors-maxed' signals underperform by 3-4pp WR — late-entry confirmation effect)",
    )
    build_p.add_argument("--db", type=str, default=None, help="Override DB path")

    # status
    stat_p = sub.add_parser("status", help="Show table status")
    stat_p.add_argument("--db", type=str, default=None, help="Override DB path")

    # clear
    clear_p = sub.add_parser("clear", help="Clear all reconstructed signals")
    clear_p.add_argument("--db", type=str, default=None, help="Override DB path")

    # run (night_lab alias)
    run_p = sub.add_parser("run", help="Alias for status (night_lab.py compatibility)")
    run_p.add_argument("--db", type=str, default=None, help="Override DB path")

    return parser


def main() -> None:
    if not _HAS_PANDAS:
        print("[HSR] ERROR: pandas and numpy are required. Run: pip install pandas numpy")
        sys.exit(1)

    # Support bare invocation → default to status
    if len(sys.argv) == 1:
        sys.argv.append("status")

    # Support night_lab.py call pattern:
    #   python3 historical_signal_reconstructor.py status '{}'
    # Strip trailing JSON arg if present
    if len(sys.argv) == 3 and sys.argv[2].startswith("{"):
        try:
            extra_params = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            extra_params = {}
        sys.argv.pop(2)
    else:
        extra_params = {}

    parser = _build_parser()
    args = parser.parse_args()

    if args.command not in COMMANDS:
        parser.print_help()
        sys.exit(1)

    params = vars(args)
    params.update(extra_params)

    COMMANDS[args.command](params)


if __name__ == "__main__":
    main()
