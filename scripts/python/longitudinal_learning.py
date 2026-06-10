#!/usr/bin/env python3
"""
longitudinal_learning.py — Phase 45
EGX Autonomous Quant System: Longitudinal Learning Engine

Tracks how each engine's accuracy, reliability, and signal quality evolve
over time. Builds "reliability curves" — enabling the system to weight
confident engines higher and identify which are improving vs degrading.

Invocation: python longitudinal_learning.py <command> '<json_params>'
Output: last stdout line = valid JSON

Commands:
  track_engine        — reliability history for one engine over N days
  reliability_curve   — reliability scores for ALL engines (last 90 days)
  system_trend        — overall system trajectory (improving vs degrading)
  calibration_report  — are engines biased? over/under-estimate analysis
  build_full          — reliability_curve + system_trend, saved to DB
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, date, timedelta
from collections import defaultdict
import random

# ---------------------------------------------------------------------------
# Paths & DB
# ---------------------------------------------------------------------------

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Engine Tracking Registry — 18 engines
# ---------------------------------------------------------------------------

ENGINE_TRACKING = {
    'prediction':  {'table': 'predictions',             'value_col': 'confidence',        'date_col': 'predicted_at'},
    'risk':        {'table': 'risk_assessments',         'value_col': 'risk_score',         'date_col': 'assessed_at'},
    'regime':      {'table': 'market_regimes',           'value_col': 'confidence',         'date_col': 'detected_at'},
    'laws':        {'table': 'pattern_laws',             'value_col': 'precision',          'date_col': 'created_at'},
    'anomaly':     {'table': 'market_anomalies',         'value_col': 'anomaly_score',      'date_col': 'detected_at'},
    'sentiment':   {'table': 'sentiment_scores',         'value_col': 'sentiment_score',    'date_col': 'scored_at'},
    'catalyst':    {'table': 'catalyst_events',          'value_col': 'impact_score',       'date_col': 'event_date'},
    'synthesis':   {'table': 'daily_synthesis',          'value_col': 'synthesis_score',    'date_col': 'date'},
    'prioritizer': {'table': 'intelligence_scores',      'value_col': 'intelligence_score', 'date_col': 'scored_at'},
    'anti_laws':   {'table': 'anti_laws',                'value_col': 'anti_precision',     'date_col': 'extracted_at'},
    'law_grades':  {'table': 'law_grades',               'value_col': 'precision',          'date_col': 'graded_at'},
    'arbitration': {'table': 'arbitration_decisions',    'value_col': 'confidence',         'date_col': 'decided_at'},
    'observatory': {'table': 'system_health_reports',    'value_col': 'sts',                'date_col': 'generated_at'},
    'compression': {'table': 'market_intelligence_index','value_col': 'mii',                'date_col': 'generated_at'},
    'uncertainty': {'table': 'uncertainty_reports',      'value_col': 'total_uncertainty',  'date_col': 'generated_at'},
    'bus':         {'table': 'bus_state',                'value_col': 'coherence_score',    'date_col': 'generated_at'},
    'governance':  {'table': 'governance_violations',    'value_col': 'id',                 'date_col': 'detected_at'},
    'sandbox':     {'table': 'sandbox_results',          'value_col': 'promotion_rate',     'date_col': 'cycle_at'},
}

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_exists(conn, table_name):
    """Return True if table exists in the database."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cur.fetchone() is not None


def column_exists(conn, table_name, col_name):
    """Return True if column exists in table."""
    try:
        cur = conn.execute(f"PRAGMA table_info({table_name})")
        cols = [row[1] for row in cur.fetchall()]
        return col_name in cols
    except Exception:
        return False


def safe_float(val, default=0.0):
    """Safely convert a value to float."""
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Core maths
# ---------------------------------------------------------------------------

def linear_regression_slope(xs, ys):
    """
    Compute least-squares slope.
    slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)
    Returns 0.0 if degenerate.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    sum_x  = sum(xs)
    sum_y  = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom  = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-12:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


def slope_to_trend(slope):
    """Convert numeric slope to trend label."""
    if slope > 0.01:
        return "IMPROVING"
    elif slope < -0.01:
        return "DEGRADING"
    return "STABLE"


# ---------------------------------------------------------------------------
# Command: track_engine
# ---------------------------------------------------------------------------

def cmd_track_engine(params):
    """
    Track one engine's metric over the last N days.
    Groups into 7-day weekly buckets and computes linear trend.
    """
    engine = params.get("engine", "prediction")
    days   = int(params.get("days", 30))

    if engine not in ENGINE_TRACKING:
        return {
            "error": f"Unknown engine '{engine}'. Valid: {sorted(ENGINE_TRACKING.keys())}",
            "engine": engine,
        }

    cfg        = ENGINE_TRACKING[engine]
    table      = cfg["table"]
    value_col  = cfg["value_col"]
    date_col   = cfg["date_col"]

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = []
    try:
        conn = get_db()
        if not table_exists(conn, table):
            conn.close()
            return _empty_track(engine, days, "table_missing")

        if not column_exists(conn, table, value_col):
            conn.close()
            return _empty_track(engine, days, "column_missing")

        # Read date + value; cast date_col to date string prefix (first 10 chars)
        query = (
            f"SELECT substr({date_col}, 1, 10) AS day, "
            f"CAST({value_col} AS REAL) AS val "
            f"FROM {table} "
            f"WHERE {date_col} >= ? AND {value_col} IS NOT NULL "
            f"ORDER BY {date_col} ASC"
        )
        cur  = conn.execute(query, (cutoff,))
        rows = [(r["day"], safe_float(r["val"])) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        return _empty_track(engine, days, str(e))

    if not rows:
        return _empty_track(engine, days, "no_data")

    # ---- Bucket into 7-day weeks ----------------------------------------
    start_date   = date.fromisoformat(rows[0][0]) if rows else date.today()
    weekly_buckets = defaultdict(list)

    for day_str, val in rows:
        try:
            d = date.fromisoformat(day_str)
        except ValueError:
            continue
        week_idx = (d - start_date).days // 7
        weekly_buckets[week_idx].append(val)

    # Build sorted weekly averages
    weekly_avg = []
    for week_idx in sorted(weekly_buckets.keys()):
        vals      = weekly_buckets[week_idx]
        wk_avg    = statistics.mean(vals)
        wk_start  = start_date + timedelta(weeks=week_idx)
        wk_label  = f"W{week_idx + 1} ({wk_start.strftime('%b %d')})"
        weekly_avg.append({"week": wk_label, "avg": round(wk_avg, 4)})

    # ---- Linear regression on weekly avgs --------------------------------
    xs    = list(range(len(weekly_avg)))
    ys    = [w["avg"] for w in weekly_avg]
    slope = linear_regression_slope(xs, ys)
    trend = slope_to_trend(slope)

    current_value    = ys[-1] if ys else 0.0
    change_from_first = round(current_value - ys[0], 4) if len(ys) > 1 else 0.0

    return {
        "engine":           engine,
        "days":             days,
        "weekly_avg":       weekly_avg,
        "trend":            trend,
        "trend_slope":      round(slope, 6),
        "current_value":    round(current_value, 4),
        "change_from_first": change_from_first,
        "data_points":      len(rows),
    }


def _empty_track(engine, days, reason="no_data"):
    """Return an empty track result for engines with missing data."""
    return {
        "engine":            engine,
        "days":              days,
        "weekly_avg":        [],
        "trend":             "STABLE",
        "trend_slope":       0.0,
        "current_value":     0.0,
        "change_from_first": 0.0,
        "data_points":       0,
        "note":              reason,
    }


# ---------------------------------------------------------------------------
# Command: reliability_curve
# ---------------------------------------------------------------------------

def _compute_reliability_score(trend, current_value, data_points):
    """
    Map (trend, current_value, data_points) → reliability_score 0-100.

    Rules:
      - IMPROVING + current > 0.5   → 80-100
      - STABLE    + current > 0.4   → 60-79
      - DEGRADING OR current < 0.3  → 20-59
      - No data                     → 0
    """
    if data_points == 0:
        return 0

    cv = safe_float(current_value)

    if trend == "IMPROVING" and cv > 0.5:
        # Scale within 80-100 based on cv (capped at 1.0)
        score = 80 + min(20, int((cv - 0.5) / 0.5 * 20))
        return min(100, score)

    if trend == "STABLE" and cv > 0.4:
        score = 60 + min(19, int((cv - 0.4) / 0.6 * 19))
        return min(79, score)

    if trend == "DEGRADING" or cv < 0.3:
        # 20-59 range
        score = 20 + min(39, int(cv / 0.3 * 39))
        return max(20, min(59, score))

    # Fallthrough: STABLE but cv in 0.3-0.4 range → lower mid tier
    score = 40 + int((cv - 0.3) / 0.1 * 19)
    return max(0, min(59, score))


def cmd_reliability_curve(params=None):
    """
    Build reliability curve for ALL engines over last 90 days.
    Returns per-engine reliability scores, trends, and suggested bus weights.
    """
    results = []

    for eng_name in ENGINE_TRACKING:
        track = cmd_track_engine({"engine": eng_name, "days": 90})
        score = _compute_reliability_score(
            track["trend"],
            track["current_value"],
            track["data_points"],
        )
        bus_weight = round(score / 100.0, 4)

        results.append({
            "engine":            eng_name,
            "reliability_score": score,
            "trend":             track["trend"],
            "current_value":     track["current_value"],
            "bus_weight":        bus_weight,
            "data_points":       track["data_points"],
        })

    # Sort for stable output
    results.sort(key=lambda r: r["reliability_score"], reverse=True)

    most_reliable   = results[0]["engine"]  if results else "n/a"
    least_reliable  = results[-1]["engine"] if results else "n/a"
    scores          = [r["reliability_score"] for r in results]
    avg_reliability = round(statistics.mean(scores), 2) if scores else 0.0

    weight_distribution = {r["engine"]: r["bus_weight"] for r in results}

    return {
        "engines":            results,
        "most_reliable":      most_reliable,
        "least_reliable":     least_reliable,
        "avg_reliability":    avg_reliability,
        "weight_distribution": weight_distribution,
    }


# ---------------------------------------------------------------------------
# Command: system_trend
# ---------------------------------------------------------------------------

def _avg_reliability_for_period(days_back, window=30):
    """
    Compute average reliability score across all engines for a given
    time window: from (days_back + window) to days_back.

    We proxy this by calling track_engine with days=(days_back + window),
    looking only at weeks inside that window.
    """
    scores = []
    for eng_name in ENGINE_TRACKING:
        track = cmd_track_engine({"engine": eng_name, "days": days_back + window})
        if track["data_points"] == 0:
            continue
        # Use the engine's trend + current_value for a score proxy
        score = _compute_reliability_score(
            track["trend"], track["current_value"], track["data_points"]
        )
        scores.append(score)

    return round(statistics.mean(scores), 2) if scores else 0.0


def cmd_system_trend(params=None):
    """
    Overall system trajectory: is the system getting better or worse?
    Compares last 30 days vs previous 30 days.
    """
    recent_scores = {}
    older_scores  = {}

    for eng_name in ENGINE_TRACKING:
        # Recent 30 days
        recent_track = cmd_track_engine({"engine": eng_name, "days": 30})
        recent_scores[eng_name] = _compute_reliability_score(
            recent_track["trend"],
            recent_track["current_value"],
            recent_track["data_points"],
        )

        # Older 30 days (days 31-60)
        older_track = cmd_track_engine({"engine": eng_name, "days": 60})
        # Represent older period by the first-week avg if available
        if older_track["data_points"] > 0 and len(older_track["weekly_avg"]) >= 2:
            first_half_avgs = [
                w["avg"] for w in older_track["weekly_avg"][: len(older_track["weekly_avg"]) // 2]
            ]
            older_cv = statistics.mean(first_half_avgs) if first_half_avgs else older_track["current_value"]
        else:
            older_cv = older_track["current_value"]

        older_scores[eng_name] = _compute_reliability_score(
            older_track["trend"], older_cv, older_track["data_points"]
        )

    recent_avg = round(statistics.mean(recent_scores.values()), 2) if recent_scores else 0.0
    older_avg  = round(statistics.mean(older_scores.values()),  2) if older_scores  else 0.0

    if older_avg > 0:
        system_momentum = round((recent_avg - older_avg) / older_avg, 4)
    else:
        system_momentum = 0.0

    # Classify trajectory
    pct = system_momentum * 100
    if pct > 10:
        trajectory = "ACCELERATING"
    elif pct > 2:
        trajectory = "IMPROVING"
    elif pct >= -2:
        trajectory = "STABLE"
    elif pct >= -10:
        trajectory = "DEGRADING"
    else:
        trajectory = "DECLINING"

    # Per-engine deltas
    deltas = {}
    for eng in ENGINE_TRACKING:
        deltas[eng] = recent_scores.get(eng, 0) - older_scores.get(eng, 0)

    sorted_deltas = sorted(deltas.items(), key=lambda kv: kv[1], reverse=True)

    top_improvers = [
        {"engine": eng, "improvement": round(delta, 2)}
        for eng, delta in sorted_deltas[:3]
        if delta > 0
    ]
    top_degraders = [
        {"engine": eng, "degradation": round(delta, 2)}
        for eng, delta in reversed(sorted_deltas[-3:])
        if delta < 0
    ]

    # Build human assessment
    if trajectory in ("ACCELERATING", "IMPROVING"):
        assessment = (
            f"System is {trajectory.lower()} with momentum {system_momentum:+.1%}. "
            f"Top gainers: {', '.join(i['engine'] for i in top_improvers) or 'none'}."
        )
    elif trajectory == "STABLE":
        assessment = (
            f"System is STABLE (momentum {system_momentum:+.1%}). "
            f"No material drift in engine reliability."
        )
    else:
        assessment = (
            f"System is {trajectory} with momentum {system_momentum:+.1%}. "
            f"Most degraded: {', '.join(d['engine'] for d in top_degraders) or 'none'}. "
            f"Review engine configurations."
        )

    return {
        "system_momentum": system_momentum,
        "trajectory":      trajectory,
        "top_improvers":   top_improvers,
        "top_degraders":   top_degraders,
        "recent_avg":      recent_avg,
        "older_avg":       older_avg,
        "assessment":      assessment,
    }


# ---------------------------------------------------------------------------
# Command: calibration_report
# ---------------------------------------------------------------------------

def _calibration_predictions(conn):
    """
    predictions: compare confidence vs. whether direction matched next close.
    Returns (calibration_error, bias_direction, n_samples).
    """
    if not table_exists(conn, "predictions"):
        return None, "CALIBRATED", 0

    # We need a prediction and its outcome. Look for direction + actual columns.
    has_direction = column_exists(conn, "predictions", "direction")
    has_outcome   = column_exists(conn, "predictions", "actual_direction")
    has_correct   = column_exists(conn, "predictions", "was_correct")
    has_conf      = column_exists(conn, "predictions", "confidence")

    if not has_conf:
        return None, "CALIBRATED", 0

    errors = []
    try:
        if has_correct:
            cur = conn.execute(
                "SELECT confidence, was_correct FROM predictions "
                "WHERE confidence IS NOT NULL AND was_correct IS NOT NULL "
                "LIMIT 500"
            )
            for row in cur.fetchall():
                conf    = safe_float(row["confidence"])
                correct = safe_float(row["was_correct"])  # 1 or 0
                errors.append(abs(conf - correct))
        elif has_direction and has_outcome:
            cur = conn.execute(
                "SELECT confidence, direction, actual_direction FROM predictions "
                "WHERE confidence IS NOT NULL AND actual_direction IS NOT NULL "
                "LIMIT 500"
            )
            for row in cur.fetchall():
                conf    = safe_float(row["confidence"])
                correct = 1.0 if row["direction"] == row["actual_direction"] else 0.0
                errors.append(abs(conf - correct))
        else:
            # Can only look at the distribution of confidence itself
            cur = conn.execute(
                "SELECT confidence FROM predictions WHERE confidence IS NOT NULL LIMIT 500"
            )
            vals = [safe_float(r["confidence"]) for r in cur.fetchall()]
            if vals:
                avg = statistics.mean(vals)
                # If consistently high conf with no outcome, treat as uncalibrated proxy
                errors = [abs(v - 0.5) for v in vals]  # distance from neutral
    except Exception:
        pass

    if not errors:
        return None, "CALIBRATED", 0

    cal_err = round(statistics.mean(errors), 4)
    avg_err = statistics.mean(errors) if errors else 0.0

    # Bias direction: if mean predicted > 0.5 and errors are high → OVERCONFIDENT
    try:
        cur2  = conn.execute("SELECT AVG(confidence) FROM predictions WHERE confidence IS NOT NULL")
        row2  = cur2.fetchone()
        avg_c = safe_float(row2[0]) if row2 else 0.5
    except Exception:
        avg_c = 0.5

    if avg_c > 0.65 and avg_err > 0.2:
        bias = "OVERCONFIDENT"
    elif avg_c < 0.35 and avg_err > 0.2:
        bias = "UNDERCONFIDENT"
    else:
        bias = "CALIBRATED"

    return cal_err, bias, len(errors)


def _calibration_law_grades(conn):
    """
    law_grades.precision vs pattern_laws.precision
    Returns (calibration_error, bias_direction, n_samples)
    """
    if not table_exists(conn, "law_grades") or not table_exists(conn, "pattern_laws"):
        return None, "CALIBRATED", 0

    try:
        cur = conn.execute(
            "SELECT lg.precision AS graded_prec, pl.precision AS actual_prec "
            "FROM law_grades lg "
            "JOIN pattern_laws pl ON lg.law_id = pl.id "
            "WHERE lg.precision IS NOT NULL AND pl.precision IS NOT NULL "
            "LIMIT 300"
        )
        rows = cur.fetchall()
    except Exception:
        # No join possible; fall back to just looking at law_grades alone
        try:
            cur = conn.execute(
                "SELECT precision FROM law_grades WHERE precision IS NOT NULL LIMIT 300"
            )
            rows_vals = [safe_float(r[0]) for r in cur.fetchall()]
            if not rows_vals:
                return None, "CALIBRATED", 0
            avg_v  = statistics.mean(rows_vals)
            errors = [abs(v - avg_v) for v in rows_vals]
            cal_err = round(statistics.mean(errors), 4)
            return cal_err, "CALIBRATED", len(errors)
        except Exception:
            return None, "CALIBRATED", 0

    if not rows:
        return None, "CALIBRATED", 0

    errors = [abs(safe_float(r["graded_prec"]) - safe_float(r["actual_prec"])) for r in rows]
    cal_err = round(statistics.mean(errors), 4)

    diffs = [safe_float(r["graded_prec"]) - safe_float(r["actual_prec"]) for r in rows]
    avg_diff = statistics.mean(diffs) if diffs else 0.0

    if avg_diff > 0.05:
        bias = "OVERCONFIDENT"
    elif avg_diff < -0.05:
        bias = "UNDERCONFIDENT"
    else:
        bias = "CALIBRATED"

    return cal_err, bias, len(errors)


def _calibration_uncertainty_vs_arbitration(conn):
    """
    Uncertainty: high uncertainty should correlate with bad (AVOID/WAIT) arbitration.
    If high uncertainty → often ENTER decision, engine is OVERCONFIDENT.
    """
    if not table_exists(conn, "uncertainty_reports") or not table_exists(conn, "arbitration_decisions"):
        return None, "CALIBRATED", 0

    try:
        cur = conn.execute(
            "SELECT ur.total_uncertainty, ad.decision "
            "FROM uncertainty_reports ur "
            "JOIN arbitration_decisions ad "
            "   ON substr(ur.generated_at, 1, 10) = substr(ad.decided_at, 1, 10) "
            "WHERE ur.total_uncertainty IS NOT NULL AND ad.decision IS NOT NULL "
            "LIMIT 200"
        )
        rows = cur.fetchall()
    except Exception:
        return None, "CALIBRATED", 0

    if not rows:
        return None, "CALIBRATED", 0

    errors = []
    high_unc_enters = 0
    total_high_unc  = 0

    for row in rows:
        unc      = safe_float(row["total_uncertainty"])
        decision = str(row["decision"]).upper()
        # High uncertainty (>0.6) should ideally not lead to ENTER
        expected = 0.0 if unc > 0.6 else 1.0  # 0 = avoid/wait, 1 = enter
        actual   = 1.0 if decision == "ENTER" else 0.0
        errors.append(abs(expected - actual))
        if unc > 0.6:
            total_high_unc += 1
            if decision == "ENTER":
                high_unc_enters += 1

    cal_err = round(statistics.mean(errors), 4) if errors else 0.0

    if total_high_unc > 0 and high_unc_enters / total_high_unc > 0.5:
        bias = "OVERCONFIDENT"  # enters despite high uncertainty
    elif cal_err < 0.15:
        bias = "CALIBRATED"
    else:
        bias = "UNDERCONFIDENT"

    return cal_err, bias, len(errors)


def _calibration_generic_vs_bus(conn, eng_name):
    """
    For engines without direct outcome columns, compare engine value vs Bus coherence.
    High engine value should correlate with higher Bus coherence score.
    """
    cfg       = ENGINE_TRACKING[eng_name]
    table     = cfg["table"]
    value_col = cfg["value_col"]
    date_col  = cfg["date_col"]

    if not table_exists(conn, table) or not table_exists(conn, "bus_state"):
        return None, "CALIBRATED", 0

    try:
        cur = conn.execute(
            f"SELECT substr(t.{date_col}, 1, 10) AS day, "
            f"AVG(CAST(t.{value_col} AS REAL)) AS eng_val "
            f"FROM {table} t "
            f"WHERE t.{value_col} IS NOT NULL "
            f"GROUP BY substr(t.{date_col}, 1, 10) "
            f"LIMIT 200"
        )
        eng_rows = {r["day"]: safe_float(r["eng_val"]) for r in cur.fetchall()}
    except Exception:
        return None, "CALIBRATED", 0

    if not table_exists(conn, "bus_state") or not column_exists(conn, "bus_state", "coherence_score"):
        return None, "CALIBRATED", 0

    try:
        cur2 = conn.execute(
            "SELECT substr(generated_at, 1, 10) AS day, "
            "AVG(CAST(coherence_score AS REAL)) AS coh "
            "FROM bus_state WHERE coherence_score IS NOT NULL "
            "GROUP BY substr(generated_at, 1, 10) LIMIT 200"
        )
        bus_rows = {r["day"]: safe_float(r["coh"]) for r in cur2.fetchall()}
    except Exception:
        return None, "CALIBRATED", 0

    common_days = set(eng_rows.keys()) & set(bus_rows.keys())
    if not common_days:
        return None, "CALIBRATED", 0

    pairs  = [(eng_rows[d], bus_rows[d]) for d in sorted(common_days)]
    errors = [abs(e - b) for e, b in pairs]

    cal_err = round(statistics.mean(errors), 4) if errors else 0.0

    # Bias: if engine consistently higher than bus coherence → OVERCONFIDENT
    diffs   = [e - b for e, b in pairs]
    avg_diff = statistics.mean(diffs) if diffs else 0.0

    if avg_diff > 0.1:
        bias = "OVERCONFIDENT"
    elif avg_diff < -0.1:
        bias = "UNDERCONFIDENT"
    else:
        bias = "CALIBRATED"

    return cal_err, bias, len(pairs)


def cmd_calibration_report(params=None):
    """
    Analyze calibration for all engines: are they biased?
    """
    engine_results = []

    try:
        conn = get_db()
    except Exception as e:
        return {"error": f"DB connection failed: {e}"}

    # Specialised calibration for engines where we can compute vs actual outcome
    special_handlers = {
        "prediction": lambda: _calibration_predictions(conn),
        "law_grades": lambda: _calibration_law_grades(conn),
        "uncertainty": lambda: _calibration_uncertainty_vs_arbitration(conn),
    }

    for eng_name in ENGINE_TRACKING:
        if eng_name in special_handlers:
            cal_err, bias, n = special_handlers[eng_name]()
        else:
            cal_err, bias, n = _calibration_generic_vs_bus(conn, eng_name)

        engine_results.append({
            "engine":            eng_name,
            "calibration_error": cal_err if cal_err is not None else 0.0,
            "bias_direction":    bias,
            "n_samples":         n,
        })

    conn.close()

    # Sort by calibration error ascending (best calibrated first)
    valid    = [r for r in engine_results if r["n_samples"] > 0]
    no_data  = [r for r in engine_results if r["n_samples"] == 0]
    valid.sort(key=lambda r: r["calibration_error"])

    best_calibrated  = valid[0]["engine"]  if valid else "n/a"
    worst_calibrated = valid[-1]["engine"] if valid else "n/a"

    errors = [r["calibration_error"] for r in valid]
    avg_cal_error = round(statistics.mean(errors), 4) if errors else 0.0

    # Recommendation
    if avg_cal_error < 0.1:
        recommendation = "Engines are well-calibrated overall. No immediate action needed."
    elif avg_cal_error < 0.25:
        recommendation = (
            f"Moderate calibration drift detected. Review {worst_calibrated} — "
            f"consider retraining or threshold adjustment."
        )
    else:
        recommendation = (
            f"High calibration errors. {worst_calibrated} requires urgent recalibration. "
            f"Consider applying Platt scaling or isotonic regression to confidence outputs."
        )

    all_results = valid + no_data

    return {
        "engines":              all_results,
        "best_calibrated":      best_calibrated,
        "worst_calibrated":     worst_calibrated,
        "avg_calibration_error": avg_cal_error,
        "recommendation":       recommendation,
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def _ensure_tables(conn):
    """Create longitudinal learning tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reliability_curves (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            engine           TEXT,
            reliability_score REAL,
            trend            TEXT,
            current_value    REAL,
            bus_weight       REAL,
            data_points      INTEGER,
            recorded_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS longitudinal_snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            system_momentum  REAL,
            trajectory       TEXT,
            avg_reliability  REAL,
            most_reliable    TEXT,
            least_reliable   TEXT,
            snapshotted_at   TEXT
        )
    """)
    conn.commit()


def cmd_build_full(params=None):
    """
    Run reliability_curve + system_trend, then persist results to DB.
    """
    now_iso = datetime.utcnow().isoformat()

    # -- Phase 1: reliability curve ----------------------------------------
    curve   = cmd_reliability_curve()
    engines = curve.get("engines", [])

    # -- Phase 2: system trend ---------------------------------------------
    trend_data = cmd_system_trend()

    # -- Phase 3: persist to DB --------------------------------------------
    try:
        conn = get_db()
        _ensure_tables(conn)

        # Insert one row per engine into reliability_curves
        for eng in engines:
            conn.execute(
                """
                INSERT INTO reliability_curves
                    (engine, reliability_score, trend, current_value,
                     bus_weight, data_points, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eng["engine"],
                    eng["reliability_score"],
                    eng["trend"],
                    eng["current_value"],
                    eng["bus_weight"],
                    eng["data_points"],
                    now_iso,
                ),
            )

        # Insert one longitudinal snapshot
        conn.execute(
            """
            INSERT INTO longitudinal_snapshots
                (system_momentum, trajectory, avg_reliability,
                 most_reliable, least_reliable, snapshotted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trend_data.get("system_momentum", 0.0),
                trend_data.get("trajectory", "STABLE"),
                curve.get("avg_reliability", 0.0),
                curve.get("most_reliable", ""),
                curve.get("least_reliable", ""),
                now_iso,
            ),
        )

        conn.commit()
        conn.close()
        db_status = "saved"
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status":            "built",
        "avg_reliability":   curve.get("avg_reliability", 0.0),
        "most_reliable":     curve.get("most_reliable", "n/a"),
        "least_reliable":    curve.get("least_reliable", "n/a"),
        "trajectory":        trend_data.get("trajectory", "STABLE"),
        "system_momentum":   trend_data.get("system_momentum", 0.0),
        "n_engines_tracked": len(engines),
        "db_status":         db_status,
        "snapshotted_at":    now_iso,
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

COMMANDS = {
    "track_engine":        cmd_track_engine,
    "reliability_curve":   cmd_reliability_curve,
    "system_trend":        cmd_system_trend,
    "calibration_report":  cmd_calibration_report,
    "build_full":          cmd_build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "Usage: python longitudinal_learning.py <command> '<json_params>'",
            "commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON params: {e}"}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({
            "error": f"Unknown command '{cmd}'",
            "valid_commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        import traceback
        result = {
            "error":     str(e),
            "traceback": traceback.format_exc(),
            "command":   cmd,
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
