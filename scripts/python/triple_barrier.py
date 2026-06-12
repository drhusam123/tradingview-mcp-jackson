#!/usr/bin/env python3
"""
Triple Barrier Labeling, Meta Labeling, and Purged K-Fold CV
MLFinLab-style implementation — pure numpy/sklearn, no external library needed.

Commands:
  label        — Apply triple barrier labels to explosive_moves events
  meta_label   — Train meta-labeling model on top of primary LightGBM
  purged_cv    — Purged K-Fold cross-validation
  stability    — Feature importance stability across purged CV folds
  bet_sizing   — Kelly-based bet sizing from barrier results
  report       — Full pipeline: label → meta_label → purged_cv → stability → bet_sizing

Usage:
  python triple_barrier.py label '{"upper_pct": 0.07, "lower_pct": 0.04}'
  python triple_barrier.py report '{}'
"""

import sys
import json
import math
import sqlite3
import pickle
import datetime
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent.parent / "data" / "egx_trading.db"
LABELS_PATH = Path(__file__).parent.parent.parent / "data" / "triple_barrier_labels.json"
META_MODEL_PATH = Path(__file__).parent / "models" / "meta_model.pkl"
PRIMARY_MODEL_PATH = Path(__file__).parent / "models" / "explosion_model.txt"

LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
META_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────
#  Feature columns — must match explosion_ml.py exactly
# ──────────────────────────────────────────────────────────────
FEATURE_COLS = [
    "pre1_bb_width",    "pre3_bb_width",    "pre5_bb_width",
    "pre1_vol_ratio",   "pre3_vol_ratio",   "pre5_vol_ratio",
    "pre1_rsi",         "pre3_rsi",         "pre5_rsi",
    "pre3_momentum_5d", "pre5_momentum_5d",
    "pre5_bb_position", "pre5_compression_days",
]

# ──────────────────────────────────────────────────────────────
#  DB helpers
# ──────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe(v, d=0.0):
    """Safe float cast with NaN/Inf guard."""
    try:
        if v is None:
            return d
        f = float(v)
        return f if math.isfinite(f) else d
    except Exception:
        return d


# ──────────────────────────────────────────────────────────────
#  Load explosive_moves with features
# ──────────────────────────────────────────────────────────────
def load_events(conn, start_date="2022-01-01"):
    cols = ", ".join(["symbol", "explosion_date", "direction"] + FEATURE_COLS)
    rows = conn.execute(
        f"SELECT {cols} FROM explosive_moves "
        f"WHERE explosion_date >= ? ORDER BY explosion_date",
        (start_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def events_to_xy(events):
    """Convert event dicts to (X, dates, symbols) arrays."""
    X, dates, symbols = [], [], []
    for ev in events:
        feats = [_safe(ev.get(c)) for c in FEATURE_COLS]
        X.append(feats)
        dates.append(ev["explosion_date"])
        symbols.append(ev["symbol"])
    return np.array(X, dtype=np.float32), dates, symbols


# ──────────────────────────────────────────────────────────────
#  Triple Barrier Labeling
# ──────────────────────────────────────────────────────────────
def _apply_barriers(symbol, explosion_date, conn, upper_pct, lower_pct, max_holding_days):
    """
    Returns (label, holding_bars, hit_type) for one event.
      +1 → upper barrier hit first
      -1 → lower barrier hit first
       0 → time stop (vertical barrier)
    Returns None on missing OHLCV data.
    """
    # entry price = close on explosion_date itself (last bar before entry)
    row = conn.execute(
        "SELECT close FROM ohlcv_history_execution "
        "WHERE symbol=? AND date(bar_time,'unixepoch') <= ? "
        "ORDER BY bar_time DESC LIMIT 1",
        (symbol, explosion_date),
    ).fetchone()
    if row is None or _safe(row["close"]) == 0.0:
        return None

    entry_close = _safe(row["close"])
    upper = entry_close * (1.0 + upper_pct)
    lower = entry_close * (1.0 - lower_pct)

    # forward bars after explosion_date
    bars = conn.execute(
        "SELECT date(bar_time,'unixepoch') as d, high, low, close "
        "FROM ohlcv_history_execution "
        "WHERE symbol=? AND date(bar_time,'unixepoch') > ? "
        "ORDER BY bar_time LIMIT ?",
        (symbol, explosion_date, max_holding_days),
    ).fetchall()

    if not bars:
        return None

    for i, bar in enumerate(bars):
        h = _safe(bar["high"])
        l = _safe(bar["low"])
        # check in bar order; if both barriers hit on same bar, upper wins (bullish bias)
        if h >= upper:
            return (1, i + 1, "upper")
        if l <= lower:
            return (-1, i + 1, "lower")

    # vertical barrier (time stop) — use final close to decide direction for reference
    final_close = _safe(bars[-1]["close"])
    ret = (final_close - entry_close) / entry_close if entry_close > 0 else 0.0
    return (0, len(bars), "time")


def cmd_label(params):
    """Apply triple barrier labels to all events and save to JSON."""
    upper_pct       = float(params.get("upper_pct",       0.07))
    lower_pct       = float(params.get("lower_pct",       0.04))
    max_holding     = int(params.get("max_holding_days",  10))
    start_date      = params.get("start_date",            "2022-01-01")

    conn = get_db()
    events = load_events(conn, start_date)
    if not events:
        return {"success": False, "error": "No events found in explosive_moves"}

    labeled = []
    skipped = 0
    label_counts = {1: 0, -1: 0, 0: 0}

    for ev in events:
        result = _apply_barriers(
            ev["symbol"], ev["explosion_date"], conn,
            upper_pct, lower_pct, max_holding
        )
        if result is None:
            skipped += 1
            continue
        label, holding, hit_type = result
        label_counts[label] += 1
        rec = {
            "symbol":         ev["symbol"],
            "explosion_date": ev["explosion_date"],
            "label":          label,
            "holding_bars":   holding,
            "hit_type":       hit_type,
            "upper_pct":      upper_pct,
            "lower_pct":      lower_pct,
        }
        # carry features forward for downstream use
        for c in FEATURE_COLS:
            rec[c] = _safe(ev.get(c))
        labeled.append(rec)

    conn.close()

    total = len(labeled)
    dist = {
        "upper_hit (+1)": label_counts[1],
        "lower_hit (-1)": label_counts[-1],
        "time_stop (0)":  label_counts[0],
    }
    if total > 0:
        dist["upper_hit_pct"] = round(label_counts[1] / total * 100, 1)
        dist["lower_hit_pct"] = round(label_counts[-1] / total * 100, 1)
        dist["time_stop_pct"] = round(label_counts[0] / total * 100, 1)

    LABELS_PATH.write_text(json.dumps(labeled, indent=2))

    return {
        "success":       True,
        "total_labeled": total,
        "skipped":       skipped,
        "distribution":  dist,
        "params":        {"upper_pct": upper_pct, "lower_pct": lower_pct,
                          "max_holding_days": max_holding},
        "saved_to":      str(LABELS_PATH),
    }


# ──────────────────────────────────────────────────────────────
#  Meta Labeling
# ──────────────────────────────────────────────────────────────
def _load_lgb_model(path):
    """Load a LightGBM model (tries lgb.Booster, falls back to plain predict)."""
    try:
        import lightgbm as lgb
        return lgb.Booster(model_file=str(path))
    except ImportError:
        raise RuntimeError("lightgbm not installed — cannot load primary model")


def _lgb_predict_proba(model, X):
    """Return 1-D probability array for positive class."""
    preds = model.predict(X)
    if preds.ndim == 2:
        return preds[:, 1]
    return preds


def cmd_meta_label(params):
    """
    Train a meta-labeling classifier on top of the primary LightGBM explosion model.
    Meta target: did the primary model's signal turn out to be correct?
    """
    threshold  = float(params.get("threshold",  0.5))
    is_end     = params.get("is_end",    "2025-12-31")
    oos_start  = params.get("oos_start", "2026-01-30")
    start_date = params.get("start_date", "2022-01-01")

    # --- Load primary model ---
    if not PRIMARY_MODEL_PATH.exists():
        return {"success": False, "error": f"Primary model not found: {PRIMARY_MODEL_PATH}"}
    primary_model = _load_lgb_model(PRIMARY_MODEL_PATH)

    # --- Load / generate barrier labels ---
    if LABELS_PATH.exists():
        labeled = json.loads(LABELS_PATH.read_text())
    else:
        res = cmd_label({"start_date": start_date})
        if not res["success"]:
            return res
        labeled = json.loads(LABELS_PATH.read_text())

    if not labeled:
        return {"success": False, "error": "No barrier labels available"}

    # Build dataset indexed by (symbol, date)
    label_map = {(r["symbol"], r["explosion_date"]): r["label"] for r in labeled}

    # Align with events that have both features and barrier labels
    conn = get_db()
    events = load_events(conn, start_date)
    conn.close()

    records = []
    for ev in events:
        key = (ev["symbol"], ev["explosion_date"])
        if key not in label_map:
            continue
        barrier_label = label_map[key]
        feats = [_safe(ev.get(c)) for c in FEATURE_COLS]
        records.append({
            "date":          ev["explosion_date"],
            "symbol":        ev["symbol"],
            "feats":         feats,
            "barrier_label": barrier_label,
        })

    if not records:
        return {"success": False, "error": "No records with both features and barrier labels"}

    dates   = [r["date"]  for r in records]
    X_all   = np.array([r["feats"] for r in records], dtype=np.float32)
    y_bar   = np.array([r["barrier_label"] for r in records], dtype=np.int32)

    # Primary model predictions
    primary_proba  = _lgb_predict_proba(primary_model, X_all)
    primary_signal = (primary_proba >= threshold).astype(np.int32)

    # Meta target: did primary signal match barrier label?
    # Signal=1 and actual=+1 → correct (1)
    # Signal=0 and actual≠+1 → correct (1)
    # Otherwise → incorrect (0)
    meta_y = np.where(
        ((primary_signal == 1) & (y_bar == 1)) |
        ((primary_signal == 0) & (y_bar != 1)),
        1, 0
    ).astype(np.int32)

    # IS / OOS split
    dates_arr = np.array(dates)
    is_mask   = dates_arr <= is_end
    oos_mask  = dates_arr >= oos_start

    X_is, y_is     = X_all[is_mask],  meta_y[is_mask]
    X_oos, y_oos   = X_all[oos_mask], meta_y[oos_mask]

    if len(X_is) < 10:
        return {"success": False, "error": f"Insufficient IS data: {len(X_is)} rows"}

    # --- Train meta-classifier ---
    try:
        import lightgbm as lgb

        meta_train = lgb.Dataset(X_is, label=y_is)
        meta_model = lgb.train(
            {
                "objective": "binary",
                "metric":    "binary_logloss",
                "num_leaves": 15,
                "n_estimators": 100,
                "learning_rate": 0.05,
                "min_child_samples": 5,
                "verbosity": -1,
                "random_state": 42,
            },
            meta_train,
            num_boost_round=100,
        )
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        meta_model = GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42
        )
        meta_model.fit(X_is, y_is)

    # IS precision
    def _precision(y_true, y_pred):
        tp = ((y_pred == 1) & (y_true == 1)).sum()
        pp = (y_pred == 1).sum()
        return round(tp / pp, 4) if pp > 0 else 0.0

    try:
        import lightgbm as lgb
        is_proba  = meta_model.predict(X_is)
        oos_proba = meta_model.predict(X_oos) if len(X_oos) > 0 else np.array([])
    except Exception:
        is_proba  = meta_model.predict_proba(X_is)[:, 1]
        oos_proba = meta_model.predict_proba(X_oos)[:, 1] if len(X_oos) > 0 else np.array([])

    is_pred  = (is_proba  >= 0.5).astype(int)
    oos_pred = (oos_proba >= 0.5).astype(int) if len(oos_proba) > 0 else np.array([])

    meta_prec_is  = _precision(y_is,  is_pred)
    meta_prec_oos = _precision(y_oos, oos_pred) if len(y_oos) > 0 else None

    # Primary-only precision on same splits
    prim_is_pred  = primary_signal[is_mask]
    prim_oos_pred = primary_signal[oos_mask]
    # primary precision = P(barrier==+1 | primary_signal==1)
    prim_prec_is  = _precision(y_bar[is_mask],  prim_is_pred)
    prim_prec_oos = _precision(y_bar[oos_mask], prim_oos_pred) if len(y_oos) > 0 else None

    # Feature importance
    try:
        import lightgbm as lgb
        imp = meta_model.feature_importance(importance_type="gain")
    except Exception:
        try:
            imp = meta_model.feature_importances_
        except Exception:
            imp = np.zeros(len(FEATURE_COLS))

    feat_imp = sorted(
        zip(FEATURE_COLS, imp.tolist()),
        key=lambda x: -x[1]
    )

    # Save meta model
    with open(META_MODEL_PATH, "wb") as f:
        pickle.dump(meta_model, f)

    return {
        "success":          True,
        "n_is":             int(len(X_is)),
        "n_oos":            int(len(X_oos)),
        "meta_precision_is":  meta_prec_is,
        "meta_precision_oos": meta_prec_oos,
        "primary_precision_is":  prim_prec_is,
        "primary_precision_oos": prim_prec_oos,
        "lift_is":          round(meta_prec_is - prim_prec_is, 4) if prim_prec_is else None,
        "lift_oos":         round(meta_prec_oos - prim_prec_oos, 4)
                            if (meta_prec_oos is not None and prim_prec_oos is not None) else None,
        "top_features":     feat_imp[:5],
        "meta_model_saved": str(META_MODEL_PATH),
    }


# ──────────────────────────────────────────────────────────────
#  Purged K-Fold Cross-Validation (MLFinLab style)
# ──────────────────────────────────────────────────────────────
def purged_kfold_splits(dates, n_splits=5, embargo_days=30):
    """
    Yield (train_idx, test_idx) pairs with purging and embargo.

    Purging: remove training samples whose observation date falls within
             embargo_days of the test fold boundaries.
    """
    dates = np.array(dates)
    n = len(dates)
    idx = np.arange(n)

    # Sort by date for fold splitting
    sorted_idx = np.argsort(dates)
    fold_size = n // n_splits

    for k in range(n_splits):
        # Test fold boundaries (in sorted order)
        test_start = k * fold_size
        test_end   = (k + 1) * fold_size if k < n_splits - 1 else n

        test_sorted_idx = sorted_idx[test_start:test_end]
        test_dates      = dates[test_sorted_idx]

        fold_start_date = min(test_dates)
        fold_end_date   = max(test_dates)

        # Parse dates for embargo arithmetic
        def to_dt(d):
            return datetime.date.fromisoformat(str(d)[:10])

        fs = to_dt(fold_start_date)
        fe = to_dt(fold_end_date)
        embargo_before = fs - datetime.timedelta(days=embargo_days)
        embargo_after  = fe + datetime.timedelta(days=embargo_days)

        # Train: not in test fold AND outside embargo windows
        train_mask = np.array([
            (i not in set(test_sorted_idx)) and
            (to_dt(dates[i]) < embargo_before or to_dt(dates[i]) > embargo_after)
            for i in idx
        ])
        train_idx = idx[train_mask]

        yield train_idx, test_sorted_idx


def _train_lgb_simple(X_train, y_train):
    """Train a lightweight LightGBM or sklearn GBM classifier."""
    try:
        import lightgbm as lgb
        ds = lgb.Dataset(X_train, label=y_train)
        model = lgb.train(
            {
                "objective": "binary",
                "metric":    "binary_logloss",
                "num_leaves": 15,
                "learning_rate": 0.05,
                "min_child_samples": 5,
                "verbosity": -1,
                "random_state": 42,
            },
            ds,
            num_boost_round=80,
        )
        return model, "lgb"
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(
            n_estimators=80, max_depth=3, learning_rate=0.05, random_state=42
        )
        model.fit(X_train, y_train)
        return model, "sklearn"


def _model_predict(model, model_type, X):
    if model_type == "lgb":
        p = model.predict(X)
        return p[:, 1] if p.ndim == 2 else p
    else:
        return model.predict_proba(X)[:, 1]


def _model_importance(model, model_type):
    try:
        if model_type == "lgb":
            return model.feature_importance(importance_type="gain").tolist()
        else:
            return model.feature_importances_.tolist()
    except Exception:
        return [0.0] * len(FEATURE_COLS)


def _precision_recall(y_true, y_pred):
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    pp = int((y_pred == 1).sum())
    ap = int((y_true == 1).sum())
    prec = tp / pp if pp > 0 else 0.0
    rec  = tp / ap if ap > 0 else 0.0
    return round(prec, 4), round(rec, 4)


def cmd_purged_cv(params):
    """Purged K-Fold CV with embargo — MLFinLab style."""
    n_splits      = int(params.get("n_splits",     5))
    embargo_days  = int(params.get("embargo_days", 30))
    start_date    = params.get("start_date",       "2022-01-01")

    conn   = get_db()
    events = load_events(conn, start_date)
    conn.close()

    if len(events) < n_splits * 10:
        return {"success": False, "error": f"Too few events ({len(events)}) for {n_splits} folds"}

    X, dates, symbols = events_to_xy(events)

    # Use barrier label if available, else use direction proxy
    if LABELS_PATH.exists():
        label_map = {
            (r["symbol"], r["explosion_date"]): r["label"]
            for r in json.loads(LABELS_PATH.read_text())
        }
        y = np.array([
            label_map.get((s, d), 0) for s, d in zip(symbols, dates)
        ], dtype=np.int32)
        # Convert to binary: +1 → 1, else → 0
        y = (y == 1).astype(np.int32)
    else:
        # Fallback: use direction column proxy
        conn = get_db()
        dir_map = {
            (r["symbol"], r["explosion_date"]): 1 if str(r.get("direction", "")).upper() == "UP" else 0
            for r in conn.execute(
                "SELECT symbol, explosion_date, direction FROM explosive_moves WHERE explosion_date >= ?",
                (start_date,)
            ).fetchall()
        }
        conn.close()
        y = np.array([dir_map.get((s, d), 0) for s, d in zip(symbols, dates)], dtype=np.int32)

    # ── Purged CV ──
    purged_results = []
    purged_importances = []

    for fold_idx, (train_idx, test_idx) in enumerate(
        purged_kfold_splits(dates, n_splits=n_splits, embargo_days=embargo_days)
    ):
        if len(train_idx) < 5 or len(test_idx) < 3:
            continue
        X_train, y_train = X[train_idx], y[train_idx]
        X_test,  y_test  = X[test_idx],  y[test_idx]

        if y_train.sum() < 2:
            continue

        model, mtype = _train_lgb_simple(X_train, y_train)
        proba        = _model_predict(model, mtype, X_test)
        pred         = (proba >= 0.5).astype(int)
        prec, rec    = _precision_recall(y_test, pred)
        imp          = _model_importance(model, mtype)

        purged_importances.append(imp)
        purged_results.append({
            "fold":         fold_idx + 1,
            "n_train":      int(len(train_idx)),
            "n_test":       int(len(test_idx)),
            "precision":    prec,
            "recall":       rec,
            "pos_rate_test": round(float(y_test.mean()), 4),
        })

    if not purged_results:
        return {"success": False, "error": "All folds were empty or had insufficient data"}

    purged_precs = [r["precision"] for r in purged_results]
    mean_prec    = round(float(np.mean(purged_precs)), 4)
    std_prec     = round(float(np.std(purged_precs)),  4)

    # ── Standard K-Fold (for leakage comparison) ──
    from sklearn.model_selection import KFold
    kf = KFold(n_splits=n_splits, shuffle=False)
    std_results = []
    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(X)):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test,  y_test  = X[test_idx],  y[test_idx]
        if y_train.sum() < 2:
            continue
        model, mtype = _train_lgb_simple(X_train, y_train)
        proba        = _model_predict(model, mtype, X_test)
        pred         = (proba >= 0.5).astype(int)
        prec, _      = _precision_recall(y_test, pred)
        std_results.append(prec)

    std_mean = round(float(np.mean(std_results)), 4) if std_results else None
    leakage_delta = round(std_mean - mean_prec, 4) if std_mean is not None else None

    return {
        "success":             True,
        "n_folds":             len(purged_results),
        "embargo_days":        embargo_days,
        "purged_mean_precision": mean_prec,
        "purged_std_precision":  std_prec,
        "standard_kfold_mean_precision": std_mean,
        "leakage_delta": leakage_delta,
        "note": (
            "Positive leakage_delta means standard K-Fold over-estimates "
            "precision by that amount due to look-ahead."
        ) if leakage_delta and leakage_delta > 0 else None,
        "per_fold":           purged_results,
    }


# ──────────────────────────────────────────────────────────────
#  Feature Importance Stability
# ──────────────────────────────────────────────────────────────
def cmd_stability(params):
    """Feature importance stability across purged CV folds."""
    n_splits     = int(params.get("n_splits",     5))
    embargo_days = int(params.get("embargo_days", 30))
    start_date   = params.get("start_date",       "2022-01-01")
    threshold    = float(params.get("stability_threshold", 0.30))  # std/mean < threshold → stable

    conn   = get_db()
    events = load_events(conn, start_date)
    conn.close()

    if len(events) < n_splits * 10:
        return {"success": False, "error": f"Too few events ({len(events)}) for {n_splits} folds"}

    X, dates, symbols = events_to_xy(events)

    if LABELS_PATH.exists():
        label_map = {
            (r["symbol"], r["explosion_date"]): r["label"]
            for r in json.loads(LABELS_PATH.read_text())
        }
        y = np.array([(1 if label_map.get((s, d), 0) == 1 else 0)
                      for s, d in zip(symbols, dates)], dtype=np.int32)
    else:
        y = np.zeros(len(X), dtype=np.int32)

    fold_importances = []

    for train_idx, test_idx in purged_kfold_splits(dates, n_splits=n_splits, embargo_days=embargo_days):
        if len(train_idx) < 5 or y[train_idx].sum() < 2:
            continue
        model, mtype = _train_lgb_simple(X[train_idx], y[train_idx])
        imp = _model_importance(model, mtype)
        fold_importances.append(imp)

    if not fold_importances:
        return {"success": False, "error": "No valid folds for stability analysis"}

    imp_matrix = np.array(fold_importances)   # shape: (n_folds, n_features)
    means      = imp_matrix.mean(axis=0)
    stds       = imp_matrix.std(axis=0)

    stability_scores = []
    stable_features  = []
    unstable_features= []

    for i, feat in enumerate(FEATURE_COLS):
        mean_i = float(means[i])
        std_i  = float(stds[i])
        cv     = (std_i / mean_i) if mean_i > 1e-9 else 1.0
        score  = round(1.0 - cv, 4)
        is_stable = cv < threshold

        entry = {
            "feature":     feat,
            "mean_importance": round(mean_i, 4),
            "std_importance":  round(std_i,  4),
            "cv":              round(cv,     4),
            "stability_score": score,
            "stable":          is_stable,
        }
        stability_scores.append(entry)
        (stable_features if is_stable else unstable_features).append(feat)

    # Sort by mean importance descending
    stability_scores.sort(key=lambda x: -x["mean_importance"])

    return {
        "success":            True,
        "n_folds_used":       len(fold_importances),
        "stability_threshold": threshold,
        "stable_features":    stable_features,
        "unstable_features":  unstable_features,
        "feature_stability":  stability_scores,
        "recommendation": (
            f"Use {len(stable_features)}/{len(FEATURE_COLS)} stable features for production. "
            f"Unstable: {unstable_features}"
        ) if unstable_features else "All features are stable.",
    }


# ──────────────────────────────────────────────────────────────
#  Kelly Bet Sizing
# ──────────────────────────────────────────────────────────────
def cmd_bet_sizing(params):
    """Kelly-based bet sizing from triple barrier results."""
    upper_pct  = float(params.get("upper_pct",  0.07))
    lower_pct  = float(params.get("lower_pct",  0.04))

    if not LABELS_PATH.exists():
        res = cmd_label(params)
        if not res["success"]:
            return res

    labeled = json.loads(LABELS_PATH.read_text())
    if not labeled:
        return {"success": False, "error": "No barrier labels available"}

    labels = np.array([r["label"] for r in labeled])
    n = len(labels)

    win_rate  = float((labels == 1).sum() / n)
    loss_rate = 1.0 - win_rate

    avg_win  = upper_pct   # barrier was hit at exactly upper_pct
    avg_loss = lower_pct   # barrier was hit at exactly lower_pct

    b = avg_win / avg_loss if avg_loss > 0 else 0.0
    kelly_f = (win_rate * b - loss_rate) / b if b > 0 else 0.0
    half_kelly = kelly_f * 0.5

    # Clamp to [0, 1]
    kelly_f    = max(0.0, min(1.0, kelly_f))
    half_kelly = max(0.0, min(1.0, half_kelly))

    # Expected value per trade
    ev = win_rate * avg_win - loss_rate * avg_loss

    # Per-regime breakdown (from barrier label dates)
    regime_kelly = {}
    try:
        regime_path = Path(__file__).parent / "models" / "ohlcv_regime_hmm.json"
        if regime_path.exists():
            regime_data = json.loads(regime_path.read_text())
            regime_map  = {r.get("date", ""): r.get("regime", "unknown")
                           for r in regime_data if isinstance(r, dict)}

            by_regime = {}
            for rec in labeled:
                regime = regime_map.get(rec["explosion_date"], "unknown")
                by_regime.setdefault(regime, []).append(rec["label"])

            for regime, rlabels in by_regime.items():
                ra  = np.array(rlabels)
                rn  = len(ra)
                rwr = float((ra == 1).sum() / rn) if rn > 0 else 0.0
                rlr = 1.0 - rwr
                rkf = max(0.0, (rwr * b - rlr) / b) if b > 0 else 0.0
                regime_kelly[regime] = {
                    "n":           rn,
                    "win_rate":    round(rwr, 4),
                    "kelly_f":     round(rkf, 4),
                    "half_kelly":  round(rkf * 0.5, 4),
                }
    except Exception:
        pass

    return {
        "success":     True,
        "n_events":    n,
        "win_rate":    round(win_rate,  4),
        "loss_rate":   round(loss_rate, 4),
        "avg_win_pct": round(avg_win,   4),
        "avg_loss_pct":round(avg_loss,  4),
        "payoff_ratio_b": round(b,        4),
        "kelly_fraction":  round(kelly_f,    4),
        "half_kelly":      round(half_kelly, 4),
        "expected_value_per_trade": round(ev, 4),
        "recommendation": (
            f"Bet {round(half_kelly * 100, 1)}% of capital per trade (half-Kelly). "
            f"Win rate: {round(win_rate * 100, 1)}%, EV: {round(ev * 100, 2)}% per trade."
        ),
        "by_regime": regime_kelly if regime_kelly else None,
    }


# ──────────────────────────────────────────────────────────────
#  Full Report
# ──────────────────────────────────────────────────────────────
def cmd_report(params):
    """Run complete pipeline: label → meta_label → purged_cv → stability → bet_sizing."""
    results = {}

    print("[1/5] Running Triple Barrier Labeling...", flush=True)
    results["label"] = cmd_label(params)

    print("[2/5] Running Meta Labeling...", flush=True)
    try:
        results["meta_label"] = cmd_meta_label(params)
    except Exception as e:
        results["meta_label"] = {"success": False, "error": str(e)}

    print("[3/5] Running Purged K-Fold CV...", flush=True)
    results["purged_cv"] = cmd_purged_cv(params)

    print("[4/5] Running Feature Stability Analysis...", flush=True)
    results["stability"] = cmd_stability(params)

    print("[5/5] Running Bet Sizing...", flush=True)
    results["bet_sizing"] = cmd_bet_sizing(params)

    # Summary
    summary = {
        "labeled_events":       results["label"].get("total_labeled"),
        "label_distribution":   results["label"].get("distribution"),
        "purged_cv_precision":  results["purged_cv"].get("purged_mean_precision"),
        "leakage_delta":        results["purged_cv"].get("leakage_delta"),
        "stable_features":      results["stability"].get("stable_features"),
        "half_kelly":           results["bet_sizing"].get("half_kelly"),
        "ev_per_trade":         results["bet_sizing"].get("expected_value_per_trade"),
    }
    if results.get("meta_label", {}).get("success"):
        summary["meta_lift_oos"] = results["meta_label"].get("lift_oos")

    results["summary"] = summary
    results["success"] = all(
        v.get("success", False)
        for k, v in results.items()
        if k not in ("summary", "success")
    )
    return results


# ──────────────────────────────────────────────────────────────
#  Command dispatch
# ──────────────────────────────────────────────────────────────
COMMANDS = {
    "label":      cmd_label,
    "meta_label": cmd_meta_label,
    "purged_cv":  cmd_purged_cv,
    "stability":  cmd_stability,
    "bet_sizing": cmd_bet_sizing,
    "report":     cmd_report,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "success": False,
            "error":   "Usage: python triple_barrier.py <command> [json_params]",
            "commands": list(COMMANDS.keys()),
        }, indent=2))
        sys.exit(1)

    cmd_name = sys.argv[1].lower()
    if cmd_name not in COMMANDS:
        print(json.dumps({
            "success": False,
            "error":   f"Unknown command: {cmd_name}",
            "commands": list(COMMANDS.keys()),
        }, indent=2))
        sys.exit(1)

    params = {}
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({"success": False, "error": f"Invalid JSON params: {e}"}))
            sys.exit(1)

    try:
        result = COMMANDS[cmd_name](params)
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            "success": False,
            "error":   str(e),
            "traceback": traceback.format_exc(),
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
