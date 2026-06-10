"""
intelligence_observatory.py — Phase 37: EGX Autonomous Quant System
Observatory that monitors health, trustability, and inter-engine agreement
across all 18 engines (Phases 16-36).

Commands:
  engine_health             — Per-engine health scores and status
  system_trustability       — Weighted system trust score (STS)
  detect_failures           — Detect stale data, confidence collapse, grade degradation
  inter_engine_agreement    — Cross-engine directional consensus
  health_report             — Combined health + failure + agreement report
  build_full                — Full report persisted to DB (includes enhanced metrics)
  latency_drift             — Track if engines are getting slower over time
  freshness_degradation     — Systematic trend of data becoming staler
  regime_detector_disagreement — Do regime-related engines agree with each other?
  causal_instability_spikes — Detect sudden changes in causal network structure
  model_entropy             — Track decision entropy (is the system confused?)
  graph_fragmentation       — Is the knowledge/contagion graph breaking apart?
  enhanced_health           — Run all 6 new metrics together (combined report)
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import datetime
import collections
import random

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# Engine Registry
# ---------------------------------------------------------------------------
ENGINE_REGISTRY = [
    {"name": "phase_16_regime",       "table": "market_regimes",        "metric": "confidence"},
    {"name": "phase_17_prediction",   "table": "predictions",           "metric": "confidence"},
    {"name": "phase_18_risk",         "table": "risk_assessments",      "metric": "risk_score"},
    {"name": "phase_19_contagion",    "table": "contagion_maps",        "metric": "contagion_score"},
    {"name": "phase_20_laws",         "table": "pattern_laws",          "metric": "precision"},
    {"name": "phase_21_portfolio",    "table": "portfolio_snapshots",   "metric": "total_value"},
    {"name": "phase_22_anomaly",      "table": "market_anomalies",      "metric": "anomaly_score"},
    {"name": "phase_23_sentiment",    "table": "sentiment_scores",      "metric": "sentiment_score"},
    {"name": "phase_24_catalyst",     "table": "catalyst_events",       "metric": "impact_score"},
    {"name": "phase_25_evolution",    "table": "law_evolution_log",     "metric": "fitness_score"},
    {"name": "phase_26_research",     "table": "research_directives",   "metric": "confidence"},
    {"name": "phase_27_synthesis",    "table": "daily_synthesis",       "metric": "synthesis_score"},
    {"name": "phase_28_meta_cognition","table":"meta_cognition_log",    "metric": "confidence"},
    {"name": "phase_29_prioritizer",  "table": "intelligence_scores",   "metric": "intelligence_score"},
    {"name": "phase_30_memory",       "table": "market_episodes",       "metric": "similarity_score"},
    {"name": "phase_31_meta_learning","table": "meta_learning_results", "metric": "confidence"},
    {"name": "phase_34_arbitration",  "table": "arbitration_decisions", "metric": "confidence"},
    {"name": "phase_36_grounding",    "table": "law_grades",            "metric": "precision"},
]

# Weights for system trustability (sum = 1.0)
ENGINE_WEIGHTS = {
    "phase_16_regime":        0.15,
    "phase_17_prediction":    0.15,
    "phase_18_risk":          0.10,
    "phase_20_laws":          0.10,
    "phase_34_arbitration":   0.10,
    "phase_36_grounding":     0.10,
}
# Remaining 30% split equally among the other 12 engines
_OTHER_ENGINES = [e["name"] for e in ENGINE_REGISTRY if e["name"] not in ENGINE_WEIGHTS]
_OTHER_WEIGHT = 0.30 / max(len(_OTHER_ENGINES), 1)
for _e in _OTHER_ENGINES:
    ENGINE_WEIGHTS[_e] = _OTHER_WEIGHT


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn, table_name):
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _row_count(conn, table_name):
    try:
        cur = conn.execute(f"SELECT COUNT(*) AS cnt FROM \"{table_name}\"")
        row = cur.fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _metric_avg(conn, table_name, metric_col, limit=100):
    """Return average of metric_col for last `limit` rows, or None on failure."""
    try:
        cur = conn.execute(
            f"SELECT \"{metric_col}\" FROM \"{table_name}\" "
            f"ORDER BY rowid DESC LIMIT ?", (limit,)
        )
        vals = [r[0] for r in cur.fetchall() if r[0] is not None]
        if not vals:
            return None
        return statistics.mean(vals)
    except Exception:
        return None


def _days_since_update(conn, table_name):
    """
    Try common timestamp column names to find the most recent row's age in days.
    Returns None if not determinable.
    """
    timestamp_cols = [
        "updated_at", "created_at", "timestamp", "date", "recorded_at",
        "generated_at", "ts", "analysis_date", "last_updated"
    ]
    try:
        cur = conn.execute(f"PRAGMA table_info(\"{table_name}\")")
        cols = {row["name"].lower() for row in cur.fetchall()}
        for tc in timestamp_cols:
            if tc in cols:
                try:
                    cur2 = conn.execute(
                        f"SELECT \"{tc}\" FROM \"{table_name}\" ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur2.fetchone()
                    if row and row[0]:
                        val = str(row[0])
                        # Try ISO parse
                        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                            try:
                                dt = datetime.datetime.strptime(val[:26], fmt)
                                delta = datetime.datetime.utcnow() - dt
                                return max(0.0, delta.total_seconds() / 86400.0)
                            except ValueError:
                                continue
                except Exception:
                    continue
        # Fallback: use rowid ordering heuristic (can't determine age)
        return None
    except Exception:
        return None


def _metric_trend_7d(conn, table_name, metric_col):
    """
    Return slope direction: +1 improving, -1 degrading, 0 flat/unknown.
    Uses last 14 rows vs previous 14 rows.
    """
    try:
        cur = conn.execute(
            f"SELECT \"{metric_col}\" FROM \"{table_name}\" ORDER BY rowid DESC LIMIT 28"
        )
        rows = [r[0] for r in cur.fetchall() if r[0] is not None]
        if len(rows) < 4:
            return 0
        recent = rows[:len(rows)//2]
        older = rows[len(rows)//2:]
        avg_recent = statistics.mean(recent)
        avg_older = statistics.mean(older)
        if avg_older == 0:
            return 0
        change = (avg_recent - avg_older) / abs(avg_older)
        if change > 0.02:
            return 1
        if change < -0.02:
            return -1
        return 0
    except Exception:
        return 0


def _regime_change_count_7d(conn):
    """Count how many distinct regime transitions occurred in the last 7 days."""
    try:
        if not _table_exists(conn, "market_regimes"):
            return 0
        # Try to find a regime/direction column
        cur = conn.execute("PRAGMA table_info(\"market_regimes\")")
        cols = [r["name"].lower() for r in cur.fetchall()]
        regime_col = None
        for c in ["regime", "direction", "market_regime", "regime_type", "state"]:
            if c in cols:
                regime_col = c
                break
        if not regime_col:
            return 0
        cur2 = conn.execute(
            f"SELECT \"{regime_col}\" FROM market_regimes ORDER BY rowid DESC LIMIT 50"
        )
        regimes = [r[0] for r in cur2.fetchall()]
        if not regimes:
            return 0
        changes = sum(1 for i in range(1, len(regimes)) if regimes[i] != regimes[i-1])
        return changes
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Engine Health Assessment
# ---------------------------------------------------------------------------

def _assess_engine(conn, engine):
    name = engine["name"]
    table = engine["table"]
    metric = engine["metric"]

    if not _table_exists(conn, table):
        return {
            "name": name,
            "table": table,
            "status": "MISSING",
            "health_score": 0,
            "row_count": 0,
            "days_since_update": None,
            "metric_avg": None,
            "trend": 0,
        }

    row_count = _row_count(conn, table)
    days = _days_since_update(conn, table)
    avg = _metric_avg(conn, table, metric)
    trend = _metric_trend_7d(conn, table, metric)

    # Compute health_score
    score = 0

    # Base from row count
    if row_count == 0:
        score = 0
    elif row_count < 5:
        score = 20
    elif row_count < 10:
        score = 40
    elif row_count < 50:
        score = 60
    elif row_count < 200:
        score = 70
    else:
        score = 80

    # Adjust for staleness
    if days is None:
        # Can't determine age — give neutral nudge
        pass
    elif days > 7:
        score = min(score, 20)
    elif days > 3:
        score = min(score, 50)
    elif days <= 1:
        score = max(score, 60)

    # Bonus for good metric average
    if avg is not None:
        # Normalise metric to 0-1 range (heuristic)
        try:
            norm = min(1.0, max(0.0, float(avg)))
        except (TypeError, ValueError):
            norm = 0.5
        if norm > 0.7:
            score = min(100, score + 15)
        elif norm > 0.5:
            score = min(100, score + 8)
        elif norm < 0.2:
            score = max(0, score - 10)

    # Bonus for upward trend
    if trend == 1:
        score = min(100, score + 5)
    elif trend == -1:
        score = max(0, score - 5)

    # Determine status
    if score >= 75:
        status = "HEALTHY"
    elif score >= 50:
        status = "DEGRADED"
    elif score >= 10:
        status = "STALE"
    else:
        status = "MISSING"

    # Override: if days_since_update > 7, force STALE
    if days is not None and days > 7 and status == "HEALTHY":
        status = "STALE"

    return {
        "name": name,
        "table": table,
        "status": status,
        "health_score": round(score, 2),
        "row_count": row_count,
        "days_since_update": round(days, 2) if days is not None else None,
        "metric_avg": round(avg, 4) if avg is not None else None,
        "trend": trend,
    }


def engine_health(params):
    results = []
    try:
        conn = get_db()
        for engine in ENGINE_REGISTRY:
            info = _assess_engine(conn, engine)
            results.append(info)
        conn.close()
    except Exception as exc:
        # Graceful degradation: mark all as MISSING
        results = [
            {
                "name": e["name"], "table": e["table"], "status": "MISSING",
                "health_score": 0, "row_count": 0,
                "days_since_update": None, "metric_avg": None, "trend": 0,
            }
            for e in ENGINE_REGISTRY
        ]

    n_healthy = sum(1 for r in results if r["status"] == "HEALTHY")
    n_degraded = sum(1 for r in results if r["status"] in ("DEGRADED", "STALE"))
    n_missing = sum(1 for r in results if r["status"] == "MISSING")
    scores = [r["health_score"] for r in results]
    avg_health = round(statistics.mean(scores), 2) if scores else 0.0

    return {
        "engines": results,
        "n_healthy": n_healthy,
        "n_degraded": n_degraded,
        "n_missing": n_missing,
        "avg_health": avg_health,
    }


# ---------------------------------------------------------------------------
# System Trustability Score
# ---------------------------------------------------------------------------

def system_trustability(params):
    health_data = engine_health(params)
    engines = health_data["engines"]
    engine_map = {e["name"]: e for e in engines}

    weighted_sum = 0.0
    total_weight = 0.0
    critical_failures = []

    for name, weight in ENGINE_WEIGHTS.items():
        info = engine_map.get(name)
        if info is None:
            critical_failures.append(name)
            total_weight += weight
            continue
        score = info["health_score"]
        weighted_sum += score * weight
        total_weight += weight
        if info["status"] in ("MISSING", "STALE") or score < 30:
            critical_failures.append(name)

    sts = round((weighted_sum / total_weight) if total_weight > 0 else 0.0, 2)

    if sts >= 70:
        status = "OPERATIONAL"
    elif sts >= 40:
        status = "DEGRADED"
    else:
        status = "CRITICAL"

    regime_ok = engine_map.get("phase_16_regime", {}).get("status") == "HEALTHY"
    arb_ok = engine_map.get("phase_34_arbitration", {}).get("status") == "HEALTHY"
    safe_to_trade = bool(sts >= 60 and regime_ok and arb_ok)

    if safe_to_trade:
        recommendation = "System is operational. Proceed with normal trading protocols."
    elif sts >= 50:
        recommendation = (
            "System partially degraded. Use reduced position sizes and "
            "verify signals manually before execution."
        )
    elif sts >= 30:
        recommendation = (
            "System significantly degraded. Avoid new positions. "
            "Review and repair failing engines before resuming trading."
        )
    else:
        recommendation = (
            "CRITICAL: System trust is too low for safe operation. "
            "Halt trading immediately and perform full system diagnostics."
        )

    return {
        "sts": sts,
        "status": status,
        "safe_to_trade": safe_to_trade,
        "critical_failures": critical_failures,
        "n_healthy": health_data["n_healthy"],
        "n_degraded": health_data["n_degraded"],
        "n_missing": health_data["n_missing"],
        "avg_health": health_data["avg_health"],
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Failure Detection
# ---------------------------------------------------------------------------

def detect_failures(params):
    failures = []

    try:
        conn = get_db()

        # --- STALE_DATA: engine not updated in >3 days ---
        for engine in ENGINE_REGISTRY:
            table = engine["table"]
            name = engine["name"]
            if not _table_exists(conn, table):
                continue
            days = _days_since_update(conn, table)
            if days is not None and days > 3:
                severity = "HIGH" if days > 7 else "MEDIUM"
                failures.append({
                    "type": "STALE_DATA",
                    "engine": name,
                    "severity": severity,
                    "detail": f"Table '{table}' last updated {round(days, 1)} days ago.",
                })

        # --- EMPTY_OUTPUT: table exists but <5 rows ---
        for engine in ENGINE_REGISTRY:
            table = engine["table"]
            name = engine["name"]
            if not _table_exists(conn, table):
                continue
            cnt = _row_count(conn, table)
            if cnt < 5:
                failures.append({
                    "type": "EMPTY_OUTPUT",
                    "engine": name,
                    "severity": "HIGH" if cnt == 0 else "MEDIUM",
                    "detail": f"Table '{table}' has only {cnt} rows (minimum expected: 5).",
                })

        # --- CONFIDENCE_COLLAPSE: avg confidence < 0.3 ---
        confidence_engines = ["phase_17_prediction", "phase_34_arbitration"]
        for name in confidence_engines:
            engine = next((e for e in ENGINE_REGISTRY if e["name"] == name), None)
            if engine is None:
                continue
            table = engine["table"]
            if not _table_exists(conn, table):
                continue
            avg = _metric_avg(conn, table, "confidence", limit=50)
            if avg is not None and avg < 0.3:
                failures.append({
                    "type": "CONFIDENCE_COLLAPSE",
                    "engine": name,
                    "severity": "HIGH",
                    "detail": (
                        f"Average confidence in '{table}' is {round(avg, 3)}, "
                        f"below threshold of 0.3."
                    ),
                })

        # --- GRADE_DEGRADATION: >50% laws graded D/F ---
        if _table_exists(conn, "law_grades"):
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) AS total FROM law_grades"
                )
                total_row = cur.fetchone()
                total = total_row["total"] if total_row else 0
                if total > 0:
                    # Try grade column
                    pragma = conn.execute("PRAGMA table_info(\"law_grades\")")
                    cols = [r["name"].lower() for r in pragma.fetchall()]
                    grade_col = None
                    for c in ["grade", "letter_grade", "law_grade", "rating"]:
                        if c in cols:
                            grade_col = c
                            break
                    if grade_col:
                        cur2 = conn.execute(
                            f"SELECT COUNT(*) AS cnt FROM law_grades "
                            f"WHERE upper(\"{grade_col}\") IN ('D', 'F')"
                        )
                        bad_row = cur2.fetchone()
                        bad = bad_row["cnt"] if bad_row else 0
                        ratio = bad / total if total > 0 else 0
                        if ratio > 0.5:
                            failures.append({
                                "type": "GRADE_DEGRADATION",
                                "engine": "phase_36_grounding",
                                "severity": "HIGH" if ratio > 0.75 else "MEDIUM",
                                "detail": (
                                    f"{round(ratio*100, 1)}% of laws graded D/F "
                                    f"({bad}/{total} laws)."
                                ),
                            })
            except Exception:
                pass

        # --- REGIME_INSTABILITY: regime changed >3 times in last 7 days ---
        change_count = _regime_change_count_7d(conn)
        if change_count > 3:
            failures.append({
                "type": "REGIME_INSTABILITY",
                "engine": "phase_16_regime",
                "severity": "HIGH" if change_count > 6 else "MEDIUM",
                "detail": (
                    f"Regime changed {change_count} times in the last 7 days "
                    f"(threshold: 3)."
                ),
            })

        conn.close()

    except Exception as exc:
        failures.append({
            "type": "SYSTEM_ERROR",
            "engine": "observatory",
            "severity": "HIGH",
            "detail": f"Observatory DB access failed: {str(exc)}",
        })

    n_critical = sum(1 for f in failures if f["severity"] == "HIGH")
    n_moderate = sum(1 for f in failures if f["severity"] == "MEDIUM")
    system_alert = n_critical >= 2 or len(failures) >= 4

    return {
        "failures": failures,
        "n_critical": n_critical,
        "n_moderate": n_moderate,
        "system_alert": system_alert,
    }


# ---------------------------------------------------------------------------
# Inter-Engine Agreement
# ---------------------------------------------------------------------------

def _read_direction(conn, table, col, positive_vals, negative_vals):
    """
    Return 'bullish', 'bearish', or 'neutral' from latest row of a table.
    positive_vals / negative_vals are case-insensitive string fragments.
    """
    try:
        if not _table_exists(conn, table):
            return "unknown"
        cur = conn.execute(
            f"SELECT \"{col}\" FROM \"{table}\" ORDER BY rowid DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return "unknown"
        val = str(row[0]).lower().strip()
        for pv in positive_vals:
            if pv.lower() in val:
                return "bullish"
        for nv in negative_vals:
            if nv.lower() in val:
                return "bearish"
        return "neutral"
    except Exception:
        return "unknown"


def _regime_direction(conn):
    """Get regime bullish/bearish/neutral/unknown."""
    try:
        if not _table_exists(conn, "market_regimes"):
            return "unknown"
        pragma = conn.execute("PRAGMA table_info(\"market_regimes\")")
        cols = [r["name"].lower() for r in pragma.fetchall()]
        direction_col = None
        for c in ["direction", "regime", "market_regime", "regime_type", "state", "bias"]:
            if c in cols:
                direction_col = c
                break
        if not direction_col:
            return "unknown"
        return _read_direction(
            conn, "market_regimes", direction_col,
            ["bull", "up", "long", "rising", "positive"],
            ["bear", "down", "short", "falling", "negative"]
        )
    except Exception:
        return "unknown"


def _prediction_direction(conn):
    """Get aggregate prediction direction from predictions table."""
    try:
        if not _table_exists(conn, "predictions"):
            return "unknown"
        pragma = conn.execute("PRAGMA table_info(\"predictions\")")
        cols = [r["name"].lower() for r in pragma.fetchall()]
        # Look for a direction or prediction column
        dir_col = None
        for c in ["direction", "prediction", "signal", "forecast", "bias", "action"]:
            if c in cols:
                dir_col = c
                break
        if dir_col:
            return _read_direction(
                conn, "predictions", dir_col,
                ["bull", "up", "long", "buy", "positive"],
                ["bear", "down", "short", "sell", "negative"]
            )
        # Fallback: check numeric confidence
        if "confidence" in cols:
            avg = _metric_avg(conn, "predictions", "confidence", limit=20)
            if avg is not None:
                return "bullish" if avg > 0.6 else ("bearish" if avg < 0.4 else "neutral")
        return "unknown"
    except Exception:
        return "unknown"


def _arbitration_direction(conn):
    """Get arbitration bias from arbitration_decisions table."""
    try:
        if not _table_exists(conn, "arbitration_decisions"):
            return "unknown"
        pragma = conn.execute("PRAGMA table_info(\"arbitration_decisions\")")
        cols = [r["name"].lower() for r in pragma.fetchall()]
        for c in ["decision", "action", "direction", "bias", "signal", "outcome"]:
            if c in cols:
                return _read_direction(
                    conn, "arbitration_decisions", c,
                    ["bull", "buy", "long", "up", "positive"],
                    ["bear", "sell", "short", "down", "negative"]
                )
        return "unknown"
    except Exception:
        return "unknown"


def _sentiment_direction(conn):
    """Get overall market sentiment from sentiment_scores table."""
    try:
        if not _table_exists(conn, "sentiment_scores"):
            return "unknown"
        pragma = conn.execute("PRAGMA table_info(\"sentiment_scores\")")
        cols = [r["name"].lower() for r in pragma.fetchall()]
        for c in ["sentiment", "direction", "label", "sentiment_label", "signal"]:
            if c in cols:
                return _read_direction(
                    conn, "sentiment_scores", c,
                    ["positive", "bull", "long", "up", "optimistic"],
                    ["negative", "bear", "short", "down", "pessimistic"]
                )
        # Fallback: numeric sentiment_score centred on 0.5
        if "sentiment_score" in cols:
            avg = _metric_avg(conn, "sentiment_scores", "sentiment_score", limit=20)
            if avg is not None:
                return "bullish" if avg > 0.55 else ("bearish" if avg < 0.45 else "neutral")
        return "unknown"
    except Exception:
        return "unknown"


def inter_engine_agreement(params):
    try:
        conn = get_db()
        regime_says = _regime_direction(conn)
        predictions_say = _prediction_direction(conn)
        arbitration_says = _arbitration_direction(conn)
        sentiment_says = _sentiment_direction(conn)
        conn.close()
    except Exception:
        regime_says = predictions_say = arbitration_says = sentiment_says = "unknown"

    directions = [regime_says, predictions_say, arbitration_says, sentiment_says]
    known = [d for d in directions if d not in ("unknown", "neutral")]

    conflict_areas = []

    if len(known) < 2:
        agreement_score = 0.0
        consensus = "MIXED"
    else:
        bullish_count = known.count("bullish")
        bearish_count = known.count("bearish")
        dominant = max(bullish_count, bearish_count)
        agreement_score = round(dominant / len(known), 3)

        if agreement_score > 0.75:
            consensus = "CONSENSUS"
        elif agreement_score >= 0.4:
            consensus = "MIXED"
        else:
            consensus = "CONFLICT"

        # Identify conflict pairs
        if regime_says not in ("unknown", "neutral") and predictions_say not in ("unknown", "neutral"):
            if regime_says != predictions_say:
                conflict_areas.append("regime vs predictions")
        if arbitration_says not in ("unknown", "neutral") and sentiment_says not in ("unknown", "neutral"):
            if arbitration_says != sentiment_says:
                conflict_areas.append("arbitration vs sentiment")
        if regime_says not in ("unknown", "neutral") and arbitration_says not in ("unknown", "neutral"):
            if regime_says != arbitration_says:
                conflict_areas.append("regime vs arbitration")
        if predictions_say not in ("unknown", "neutral") and sentiment_says not in ("unknown", "neutral"):
            if predictions_say != sentiment_says:
                conflict_areas.append("predictions vs sentiment")

    return {
        "agreement_score": agreement_score,
        "consensus": consensus,
        "regime_says": regime_says,
        "predictions_say": predictions_say,
        "arbitration_says": arbitration_says,
        "sentiment_says": sentiment_says,
        "conflict_areas": conflict_areas,
    }


# ---------------------------------------------------------------------------
# Health Report
# ---------------------------------------------------------------------------

def health_report(params):
    health = engine_health(params)
    failures = detect_failures(params)
    agreement = inter_engine_agreement(params)
    generated_at = datetime.datetime.utcnow().isoformat()

    return {
        "health": health,
        "failures": failures,
        "agreement": agreement,
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# Build Full — persist to DB
# ---------------------------------------------------------------------------

def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS engine_health_scores (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            engine_name      TEXT,
            health_score     REAL,
            status           TEXT,
            row_count        INTEGER,
            days_since_update REAL,
            updated_at       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_health_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sts             REAL,
            status          TEXT,
            safe_to_trade   INTEGER,
            n_healthy       INTEGER,
            n_degraded      INTEGER,
            n_missing       INTEGER,
            agreement_score REAL,
            generated_at    TEXT
        )
    """)
    conn.commit()


def build_full(params):
    report = health_report(params)
    trust = system_trustability(params)
    enh = enhanced_health(params)
    now = datetime.datetime.utcnow().isoformat()

    try:
        conn = get_db()
        _ensure_tables(conn)

        # Insert per-engine health rows
        for eng in report["health"]["engines"]:
            conn.execute(
                """
                INSERT INTO engine_health_scores
                    (engine_name, health_score, status, row_count, days_since_update, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    eng["name"],
                    eng["health_score"],
                    eng["status"],
                    eng["row_count"],
                    eng["days_since_update"],
                    now,
                )
            )

        # Insert enhanced metric summary rows (one row per metric)
        enh_metrics_summary = {
            "latency_drift":                enh["metrics"]["latency_drift"].get("avg_system_drift", 0.0),
            "freshness_degradation":        enh["metrics"]["freshness_degradation"].get("avg_degrade_score", 0.0),
            "regime_detector_disagreement": 1.0 - enh["metrics"]["regime_detector_disagreement"].get("agreement_rate", 1.0),
            "causal_instability_spikes":    1.0 if enh["metrics"]["causal_instability_spikes"].get("spike_detected") else 0.0,
            "model_entropy":                enh["metrics"]["model_entropy"].get("system_entropy", 0.0),
            "graph_fragmentation":          enh["metrics"]["graph_fragmentation"].get("fragmentation_rate", 0.0),
        }
        for metric_name, metric_val in enh_metrics_summary.items():
            conn.execute(
                """
                INSERT INTO engine_health_scores
                    (engine_name, health_score, status, row_count, days_since_update, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    metric_name,
                    round(float(metric_val), 4) if metric_val is not None else 0.0,
                    enh["overall_status"],
                    None,
                    None,
                    now,
                )
            )

        # Insert system health summary row
        conn.execute(
            """
            INSERT INTO system_health_reports
                (sts, status, safe_to_trade, n_healthy, n_degraded, n_missing,
                 agreement_score, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trust["sts"],
                trust["status"],
                1 if trust["safe_to_trade"] else 0,
                report["health"]["n_healthy"],
                report["health"]["n_degraded"],
                report["health"]["n_missing"],
                report["agreement"]["agreement_score"],
                now,
            )
        )
        conn.commit()
        conn.close()
        db_ok = True
    except Exception as exc:
        db_ok = False

    n_healthy = report["health"]["n_healthy"]
    n_total = len(report["health"]["engines"])
    n_failures = report["failures"]["n_critical"]
    consensus = report["agreement"]["consensus"]

    report_summary = (
        f"STS={trust['sts']:.1f} ({trust['status']}), "
        f"{n_healthy}/{n_total} engines healthy, "
        f"{n_failures} critical failures, "
        f"engine consensus: {consensus}, "
        f"safe_to_trade={trust['safe_to_trade']}, "
        f"enhancement_score={enh['overall_enhancement_score']} ({enh['overall_status']})"
    )

    return {
        "status": "built" if db_ok else "built_no_db",
        "sts": trust["sts"],
        "safe_to_trade": trust["safe_to_trade"],
        "n_healthy": n_healthy,
        "enhancement_score": enh["overall_enhancement_score"],
        "enhancement_status": enh["overall_status"],
        "enhancement_alerts": enh["alerts"],
        "report_summary": report_summary,
    }


# ---------------------------------------------------------------------------
# Enhanced Health Metrics — 6 new commands
# ---------------------------------------------------------------------------

def latency_drift(params):
    """
    Track if engines are getting slower over time.
    Compares update frequency of the last 7 days vs previous 7 days.
    Positive drift = getting slower (worse). Negative drift = getting faster (better).
    """
    engines_with_drift = []

    try:
        conn = get_db()
        now = datetime.datetime.utcnow()
        cutoff_7 = now - datetime.timedelta(days=7)
        cutoff_14 = now - datetime.timedelta(days=14)

        for engine in ENGINE_REGISTRY:
            table = engine["table"]
            name = engine["name"]

            if not _table_exists(conn, table):
                continue

            # Find a usable timestamp column
            ts_cols = [
                "updated_at", "created_at", "timestamp", "date", "recorded_at",
                "generated_at", "ts", "analysis_date", "last_updated"
            ]
            pragma = conn.execute(f"PRAGMA table_info(\"{table}\")")
            col_names = {r["name"].lower() for r in pragma.fetchall()}
            ts_col = next((c for c in ts_cols if c in col_names), None)

            if ts_col:
                try:
                    cur = conn.execute(
                        f"SELECT \"{ts_col}\" FROM \"{table}\" ORDER BY rowid DESC LIMIT 200"
                    )
                    rows = cur.fetchall()
                    recent_count = 0
                    older_count = 0
                    for r in rows:
                        val = str(r[0]) if r[0] else None
                        if not val:
                            continue
                        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                            try:
                                dt = datetime.datetime.strptime(val[:26], fmt)
                                if dt >= cutoff_7:
                                    recent_count += 1
                                elif dt >= cutoff_14:
                                    older_count += 1
                                break
                            except ValueError:
                                continue
                    recent_freq = recent_count / 7.0
                    older_freq = older_count / 7.0
                    if older_freq > 0:
                        drift_rate = (recent_freq - older_freq) / older_freq
                    elif recent_freq > 0:
                        drift_rate = 1.0
                    else:
                        drift_rate = 0.0

                    if drift_rate > 0.05:
                        direction = "FASTER"
                    elif drift_rate < -0.05:
                        direction = "SLOWER"
                    else:
                        direction = "STABLE"

                    engines_with_drift.append({
                        "engine": name,
                        "drift_rate": round(drift_rate, 4),
                        "direction": direction,
                        "recent_updates_per_day": round(recent_freq, 3),
                        "older_updates_per_day": round(older_freq, 3),
                    })
                except Exception:
                    pass
            else:
                # No timestamp column — use row_count as proxy with heuristic
                cnt = _row_count(conn, table)
                days = _days_since_update(conn, table)
                if days is not None and days > 0:
                    implied_freq = cnt / max(days, 1)
                    # Can't compute drift without history; report as STABLE
                    engines_with_drift.append({
                        "engine": name,
                        "drift_rate": 0.0,
                        "direction": "STABLE",
                        "recent_updates_per_day": round(implied_freq, 3),
                        "older_updates_per_day": round(implied_freq, 3),
                    })

        conn.close()
    except Exception as exc:
        return {
            "engines_with_drift": [],
            "n_degrading": 0,
            "n_improving": 0,
            "worst_drift_engine": None,
            "avg_system_drift": 0.0,
            "interpretation": f"Error computing latency drift: {exc}",
        }

    n_degrading = sum(1 for e in engines_with_drift if e["direction"] == "SLOWER")
    n_improving = sum(1 for e in engines_with_drift if e["direction"] == "FASTER")

    sorted_by_drift = sorted(engines_with_drift, key=lambda x: x["drift_rate"])
    worst_drift_engine = sorted_by_drift[0]["engine"] if sorted_by_drift else None

    drift_vals = [e["drift_rate"] for e in engines_with_drift]
    avg_system_drift = round(statistics.mean(drift_vals), 4) if drift_vals else 0.0

    if n_degrading == 0:
        interpretation = "No latency degradation detected. All engines running at normal frequency."
    elif n_degrading <= 2:
        interpretation = f"{n_degrading} engine(s) showing reduced update frequency. Monitor closely."
    else:
        interpretation = (
            f"{n_degrading} engines degrading — system-wide latency drift detected. "
            "Investigate pipeline bottlenecks."
        )

    return {
        "engines_with_drift": engines_with_drift,
        "n_degrading": n_degrading,
        "n_improving": n_improving,
        "worst_drift_engine": worst_drift_engine,
        "avg_system_drift": avg_system_drift,
        "interpretation": interpretation,
    }


def freshness_degradation(params):
    """
    Systematic trend of data becoming staler.
    For each engine, compute degrade_score = days_since_update / 7.
    Positive score (>1.0) = critical staleness.
    """
    degrading_engines = []

    try:
        conn = get_db()
        for engine in ENGINE_REGISTRY:
            table = engine["table"]
            name = engine["name"]

            if not _table_exists(conn, table):
                degrade_score = 2.0  # Missing table = very stale
                days_val = None
                trend = "CRITICAL"
            else:
                days_val = _days_since_update(conn, table)
                if days_val is None:
                    # Can't determine — use row count as proxy
                    cnt = _row_count(conn, table)
                    degrade_score = 0.0 if cnt > 50 else (1.0 if cnt < 5 else 0.3)
                    trend = "CRITICAL" if degrade_score >= 1.0 else "STABLE"
                else:
                    degrade_score = round(max(0.0, days_val / 7.0), 4)
                    if degrade_score > 1.0:
                        trend = "CRITICAL"
                    elif degrade_score > 0.5:
                        trend = "DEGRADING"
                    else:
                        trend = "STABLE"

            degrading_engines.append({
                "engine": name,
                "degrade_score": round(degrade_score, 4),
                "days_since_update": round(days_val, 2) if days_val is not None else None,
                "trend": trend,
            })

        conn.close()
    except Exception as exc:
        return {
            "degrading_engines": [],
            "n_critical": 0,
            "avg_degrade_score": 0.0,
            "system_freshness_health": "CRITICAL",
            "recommendation": f"Error computing freshness degradation: {exc}",
        }

    n_critical = sum(1 for e in degrading_engines if e["degrade_score"] > 1.0)
    scores = [e["degrade_score"] for e in degrading_engines]
    avg_degrade_score = round(statistics.mean(scores), 4) if scores else 0.0

    if avg_degrade_score < 0.3:
        system_freshness_health = "FRESH"
        recommendation = "Data freshness is healthy. No action required."
    elif avg_degrade_score < 0.7:
        system_freshness_health = "STALE"
        recommendation = (
            "Some engines have stale data. Review update schedules for flagged engines."
        )
    else:
        system_freshness_health = "CRITICAL"
        recommendation = (
            "Critical data staleness detected across multiple engines. "
            "Run full system update cycle immediately."
        )

    return {
        "degrading_engines": degrading_engines,
        "n_critical": n_critical,
        "avg_degrade_score": avg_degrade_score,
        "system_freshness_health": system_freshness_health,
        "recommendation": recommendation,
    }


def regime_detector_disagreement(params):
    """
    Compare direction signals from regime, transition, arbitration, and sentiment engines.
    Disagreement detected if agreement_rate < 0.6.
    """
    try:
        conn = get_db()

        # --- Phase 16: market_regimes ---
        phase_16_says = "NEUTRAL"
        if _table_exists(conn, "market_regimes"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"market_regimes\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                regime_col = next(
                    (c for c in ["regime_type", "regime", "market_regime", "state", "direction"] if c in cols),
                    None
                )
                if regime_col:
                    cur = conn.execute(
                        f"SELECT \"{regime_col}\" FROM market_regimes ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        val = str(row[0]).upper()
                        if any(x in val for x in ["BULL", "UP", "LONG", "RISING"]):
                            phase_16_says = "BULLISH"
                        elif any(x in val for x in ["BEAR", "DOWN", "SHORT", "FALLING", "CRISIS"]):
                            phase_16_says = "BEARISH"
                        elif "SIDEWAYS" in val or "NEUTRAL" in val or "FLAT" in val:
                            phase_16_says = "NEUTRAL"
            except Exception:
                pass

        # --- Phase 33: regime_transition_signals (EWI) ---
        phase_33_says = "NEUTRAL"
        if _table_exists(conn, "regime_transition_signals"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"regime_transition_signals\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                ewi_col = next((c for c in ["ewi", "ewi_score", "transition_score"] if c in cols), None)
                dir_col = next((c for c in ["direction", "transition", "signal"] if c in cols), None)
                if ewi_col:
                    cur = conn.execute(
                        f"SELECT \"{ewi_col}\" FROM regime_transition_signals ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        ewi_val = float(row[0])
                        if ewi_val > 60:
                            phase_33_says = "BEARISH"  # High EWI = transition risk
                        elif dir_col:
                            cur2 = conn.execute(
                                f"SELECT \"{dir_col}\" FROM regime_transition_signals ORDER BY rowid DESC LIMIT 1"
                            )
                            row2 = cur2.fetchone()
                            if row2 and row2[0]:
                                val = str(row2[0]).upper()
                                if any(x in val for x in ["BULL", "UP", "LONG"]):
                                    phase_33_says = "BULLISH"
                                elif any(x in val for x in ["BEAR", "DOWN", "SHORT"]):
                                    phase_33_says = "BEARISH"
                elif dir_col:
                    cur = conn.execute(
                        f"SELECT \"{dir_col}\" FROM regime_transition_signals ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        val = str(row[0]).upper()
                        if any(x in val for x in ["BULL", "UP", "LONG"]):
                            phase_33_says = "BULLISH"
                        elif any(x in val for x in ["BEAR", "DOWN", "SHORT"]):
                            phase_33_says = "BEARISH"
            except Exception:
                pass

        # --- Phase 34: arbitration_decisions ---
        phase_34_says = "NEUTRAL"
        if _table_exists(conn, "arbitration_decisions"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"arbitration_decisions\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                dec_col = next((c for c in ["decision", "action", "direction", "signal"] if c in cols), None)
                if dec_col:
                    cur = conn.execute(
                        f"SELECT \"{dec_col}\" FROM arbitration_decisions ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        val = str(row[0]).upper()
                        if "ENTER" in val or "BUY" in val or "BULL" in val or "LONG" in val:
                            phase_34_says = "BULLISH"
                        elif "AVOID" in val or "SELL" in val or "BEAR" in val or "SHORT" in val:
                            phase_34_says = "BEARISH"
                        # WAIT → NEUTRAL
            except Exception:
                pass

        # --- Phase 23: sentiment_scores ---
        phase_23_says = "NEUTRAL"
        if _table_exists(conn, "sentiment_scores"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"sentiment_scores\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                sent_col = next(
                    (c for c in ["sentiment_direction", "direction", "sentiment", "label", "sentiment_label"] if c in cols),
                    None
                )
                if sent_col:
                    cur = conn.execute(
                        f"SELECT \"{sent_col}\" FROM sentiment_scores ORDER BY rowid DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        val = str(row[0]).upper()
                        if any(x in val for x in ["BULL", "POS", "LONG", "UP", "OPTIMIS"]):
                            phase_23_says = "BULLISH"
                        elif any(x in val for x in ["BEAR", "NEG", "SHORT", "DOWN", "PESSIM"]):
                            phase_23_says = "BEARISH"
                elif "sentiment_score" in cols:
                    avg = _metric_avg(conn, "sentiment_scores", "sentiment_score", limit=20)
                    if avg is not None:
                        phase_23_says = "BULLISH" if avg > 0.55 else ("BEARISH" if avg < 0.45 else "NEUTRAL")
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        return {
            "phase_16_says": "NEUTRAL",
            "phase_33_says": "NEUTRAL",
            "phase_34_says": "NEUTRAL",
            "phase_23_says": "NEUTRAL",
            "agreement_rate": 0.0,
            "disagreement_detected": True,
            "majority_direction": "NEUTRAL",
            "conflict_pairs": [],
            "recommendation": f"Error reading regime signals: {exc}",
        }

    all_signals = [
        ("phase_16", phase_16_says),
        ("phase_33", phase_33_says),
        ("phase_34", phase_34_says),
        ("phase_23", phase_23_says),
    ]

    counts = collections.Counter(s for _, s in all_signals)
    majority_direction = counts.most_common(1)[0][0] if counts else "NEUTRAL"
    n_majority = counts[majority_direction]
    agreement_rate = round(n_majority / len(all_signals), 4)
    disagreement_detected = agreement_rate < 0.6

    conflict_pairs = []
    names = [n for n, _ in all_signals]
    sigs = [s for _, s in all_signals]
    for i in range(len(all_signals)):
        for j in range(i + 1, len(all_signals)):
            if sigs[i] != sigs[j] and sigs[i] != "NEUTRAL" and sigs[j] != "NEUTRAL":
                conflict_pairs.append({
                    "engine_a": names[i],
                    "engine_b": names[j],
                    "direction_a": sigs[i],
                    "direction_b": sigs[j],
                })

    if not disagreement_detected:
        recommendation = (
            f"All regime engines in agreement: {majority_direction}. "
            "Directional confidence is high."
        )
    elif len(conflict_pairs) == 0:
        recommendation = (
            "Minor disagreement detected (some engines neutral). "
            "Treat signals with moderate confidence."
        )
    else:
        recommendation = (
            f"Regime disagreement detected ({len(conflict_pairs)} conflict pair(s)). "
            "Do not rely on directional signals until engines converge."
        )

    return {
        "phase_16_says": phase_16_says,
        "phase_33_says": phase_33_says,
        "phase_34_says": phase_34_says,
        "phase_23_says": phase_23_says,
        "agreement_rate": agreement_rate,
        "disagreement_detected": disagreement_detected,
        "majority_direction": majority_direction,
        "conflict_pairs": conflict_pairs,
        "recommendation": recommendation,
    }


def causal_instability_spikes(params):
    """
    Detect sudden changes in causal network structure.
    Reads from causal_graph / research_directives / anti_laws tables.
    """
    spike_detected = False
    spike_severity = "NONE"
    causal_inversions_detected = 0
    confidence_drop_pct = 0.0
    affected_engines = []
    detected_at = datetime.datetime.utcnow().isoformat()

    dimensions_changed = 0

    try:
        conn = get_db()

        # --- Check for CAUSAL_INVERSION in anti_laws ---
        if _table_exists(conn, "anti_laws"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"anti_laws\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                type_col = next((c for c in ["type", "law_type", "anti_type", "category"] if c in cols), None)
                if type_col:
                    cur = conn.execute(
                        f"SELECT COUNT(*) AS cnt FROM anti_laws "
                        f"WHERE upper(\"{type_col}\") LIKE '%CAUSAL_INVERSION%'"
                    )
                    row = cur.fetchone()
                    causal_inversions_detected = row["cnt"] if row else 0
                    if causal_inversions_detected > 0:
                        spike_detected = True
                        dimensions_changed += 1
                        affected_engines.append("anti_laws")
            except Exception:
                pass

        # --- Check research_directives confidence drop ---
        if _table_exists(conn, "research_directives"):
            try:
                cur = conn.execute(
                    "SELECT confidence FROM research_directives ORDER BY rowid DESC LIMIT 14"
                )
                conf_rows = [r[0] for r in cur.fetchall() if r[0] is not None]
                if len(conf_rows) >= 4:
                    half = len(conf_rows) // 2
                    recent_avg = statistics.mean(conf_rows[:half])
                    older_avg = statistics.mean(conf_rows[half:])
                    if older_avg > 0:
                        confidence_drop_pct = round(
                            max(0.0, (older_avg - recent_avg) / older_avg * 100), 2
                        )
                        if confidence_drop_pct > 20:
                            spike_detected = True
                            dimensions_changed += 1
                            affected_engines.append("research_directives")
            except Exception:
                pass

        # --- Check causal_graph for structural changes ---
        if _table_exists(conn, "causal_graph"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"causal_graph\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                conf_col = next(
                    (c for c in ["confidence", "strength", "weight", "score"] if c in cols),
                    None
                )
                if conf_col:
                    cur = conn.execute(
                        f"SELECT \"{conf_col}\" FROM causal_graph ORDER BY rowid DESC LIMIT 28"
                    )
                    rows = [r[0] for r in cur.fetchall() if r[0] is not None]
                    if len(rows) >= 4:
                        half = len(rows) // 2
                        recent_avg = statistics.mean(rows[:half])
                        older_avg = statistics.mean(rows[half:])
                        if older_avg > 0:
                            drop = (older_avg - recent_avg) / older_avg
                            if drop > 0.20:
                                spike_detected = True
                                dimensions_changed += 1
                                if "causal_graph" not in affected_engines:
                                    affected_engines.append("causal_graph")
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        return {
            "spike_detected": False,
            "spike_severity": "NONE",
            "causal_inversions_detected": 0,
            "confidence_drop_pct": 0.0,
            "affected_engines": [],
            "recommendation": f"Error reading causal tables: {exc}",
            "detected_at": detected_at,
        }

    if not spike_detected:
        spike_severity = "NONE"
    elif dimensions_changed == 1:
        spike_severity = "MINOR"
    elif dimensions_changed == 2:
        spike_severity = "MODERATE"
    else:
        spike_severity = "SEVERE"

    if spike_severity == "NONE":
        recommendation = "Causal network structure is stable. No anomalies detected."
    elif spike_severity == "MINOR":
        recommendation = "Minor causal instability detected. Monitor for escalation."
    elif spike_severity == "MODERATE":
        recommendation = (
            "Moderate causal instability spike. Review affected engines and validate "
            "signal reliability before trading."
        )
    else:
        recommendation = (
            "SEVERE causal instability. Multiple causal dimensions destabilized. "
            "Halt automated trading until causal network stabilizes."
        )

    return {
        "spike_detected": spike_detected,
        "spike_severity": spike_severity,
        "causal_inversions_detected": causal_inversions_detected,
        "confidence_drop_pct": confidence_drop_pct,
        "affected_engines": affected_engines,
        "recommendation": recommendation,
        "detected_at": detected_at,
    }


def model_entropy(params):
    """
    Track decision entropy — are decisions becoming more random/uncertain?
    Computes Shannon entropy of arbitration_decisions and predictions distributions.
    """

    def _shannon_entropy(counts_dict):
        """Return Shannon entropy normalized to 0-1 given a dict of label: count."""
        total = sum(counts_dict.values())
        if total == 0:
            return 0.0
        n_categories = len([v for v in counts_dict.values() if v > 0])
        if n_categories <= 1:
            return 0.0
        h = 0.0
        for count in counts_dict.values():
            if count > 0:
                p = count / total
                h -= p * math.log2(p)
        max_h = math.log2(n_categories)
        return round(h / max_h if max_h > 0 else 0.0, 4)

    arbitration_entropy = 0.0
    prediction_entropy = 0.0
    decision_distribution = {"ENTER": 0, "WAIT": 0, "AVOID": 0}
    prediction_distribution = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}

    try:
        conn = get_db()

        # --- arbitration_decisions entropy ---
        if _table_exists(conn, "arbitration_decisions"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"arbitration_decisions\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                dec_col = next(
                    (c for c in ["decision", "action", "direction", "signal"] if c in cols),
                    None
                )
                if dec_col:
                    cur = conn.execute(
                        f"SELECT \"{dec_col}\" FROM arbitration_decisions ORDER BY rowid DESC LIMIT 30"
                    )
                    for row in cur.fetchall():
                        if row[0] is None:
                            continue
                        val = str(row[0]).upper()
                        if "ENTER" in val or "BUY" in val or "LONG" in val:
                            decision_distribution["ENTER"] += 1
                        elif "AVOID" in val or "SELL" in val or "SHORT" in val:
                            decision_distribution["AVOID"] += 1
                        else:
                            decision_distribution["WAIT"] += 1
                    arbitration_entropy = _shannon_entropy(decision_distribution)
            except Exception:
                pass

        # --- predictions entropy ---
        if _table_exists(conn, "predictions"):
            try:
                pragma = conn.execute("PRAGMA table_info(\"predictions\")")
                cols = [r["name"].lower() for r in pragma.fetchall()]
                dir_col = next(
                    (c for c in ["direction", "prediction", "signal", "forecast", "bias"] if c in cols),
                    None
                )
                if dir_col:
                    cur = conn.execute(
                        f"SELECT \"{dir_col}\" FROM predictions ORDER BY rowid DESC LIMIT 30"
                    )
                    for row in cur.fetchall():
                        if row[0] is None:
                            continue
                        val = str(row[0]).upper()
                        if any(x in val for x in ["BULL", "UP", "LONG", "BUY"]):
                            prediction_distribution["BULLISH"] += 1
                        elif any(x in val for x in ["BEAR", "DOWN", "SHORT", "SELL"]):
                            prediction_distribution["BEARISH"] += 1
                        else:
                            prediction_distribution["NEUTRAL"] += 1
                    prediction_entropy = _shannon_entropy(prediction_distribution)
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        return {
            "arbitration_entropy": 0.0,
            "prediction_entropy": 0.0,
            "system_entropy": 0.0,
            "entropy_level": "LOW",
            "decision_distribution": decision_distribution,
            "interpretation": f"Error computing model entropy: {exc}",
            "alert": False,
        }

    system_entropy = round((arbitration_entropy + prediction_entropy) / 2.0, 4)

    if system_entropy > 0.85:
        entropy_level = "CRITICAL"
    elif system_entropy > 0.75:
        entropy_level = "HIGH"
    elif system_entropy >= 0.4:
        entropy_level = "HEALTHY"
    elif system_entropy >= 0.2:
        entropy_level = "LOW"
    else:
        entropy_level = "LOW"

    alert = system_entropy > 0.85 or system_entropy < 0.2

    if entropy_level == "CRITICAL":
        interpretation = (
            "CRITICAL: Decision entropy is dangerously high. The system is oscillating "
            "between conflicting signals. Avoid automated trading until resolved."
        )
    elif entropy_level == "HIGH":
        interpretation = (
            "High entropy detected. System decisions are becoming less decisive. "
            "Reduce position sizes and increase manual oversight."
        )
    elif entropy_level == "HEALTHY":
        interpretation = "Decision entropy is within the healthy range (0.4-0.75). System is operating normally."
    else:
        interpretation = (
            "Low entropy detected — system may be overfit or stuck in a single regime. "
            "Validate that signals are based on diverse evidence."
        )

    return {
        "arbitration_entropy": arbitration_entropy,
        "prediction_entropy": prediction_entropy,
        "system_entropy": system_entropy,
        "entropy_level": entropy_level,
        "decision_distribution": decision_distribution,
        "interpretation": interpretation,
        "alert": alert,
    }


def graph_fragmentation(params):
    """
    Is the knowledge/contagion graph breaking apart?
    Compares last 14 days vs previous 14 days of contagion_maps data.
    """
    fragmentation_rate = 0.0
    fragmentation_level = "STABLE"
    old_avg_contagion = 0.0
    recent_avg_contagion = 0.0
    disconnected_sectors = []
    n_link_pairs_lost = 0
    alert = False

    try:
        conn = get_db()

        if not _table_exists(conn, "contagion_maps"):
            conn.close()
            return {
                "fragmentation_rate": 0.0,
                "fragmentation_level": "STABLE",
                "old_avg_contagion": 0.0,
                "recent_avg_contagion": 0.0,
                "disconnected_sectors": [],
                "n_link_pairs_lost": 0,
                "alert": False,
                "recommendation": "contagion_maps table not found. Cannot assess graph fragmentation.",
            }

        pragma = conn.execute("PRAGMA table_info(\"contagion_maps\")")
        col_names = {r["name"].lower() for r in pragma.fetchall()}

        score_col = next(
            (c for c in ["contagion_score", "score", "strength", "weight", "value"] if c in col_names),
            None
        )
        source_col = next((c for c in ["source", "source_sector", "from_sector", "from"] if c in col_names), None)
        target_col = next((c for c in ["target", "target_sector", "to_sector", "to"] if c in col_names), None)

        ts_cols = ["updated_at", "created_at", "timestamp", "date", "recorded_at", "generated_at", "ts"]
        ts_col = next((c for c in ts_cols if c in col_names), None)

        now = datetime.datetime.utcnow()
        cutoff_14 = now - datetime.timedelta(days=14)
        cutoff_28 = now - datetime.timedelta(days=28)

        recent_scores = []
        older_scores = []
        recent_pairs = set()
        older_pairs = set()

        # Fetch up to 200 rows ordered by timestamp or rowid
        order_col = ts_col if ts_col else "rowid"
        try:
            cur = conn.execute(
                f"SELECT * FROM contagion_maps ORDER BY \"{order_col}\" DESC LIMIT 400"
            )
            rows = cur.fetchall()
        except Exception:
            rows = []

        for row in rows:
            row_dict = dict(row)

            # Parse timestamp
            row_ts = None
            if ts_col and row_dict.get(ts_col):
                val = str(row_dict[ts_col])
                for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        row_ts = datetime.datetime.strptime(val[:26], fmt)
                        break
                    except ValueError:
                        continue

            score_val = None
            if score_col and row_dict.get(score_col) is not None:
                try:
                    score_val = float(row_dict[score_col])
                except (TypeError, ValueError):
                    pass

            pair = None
            if source_col and target_col:
                pair = (str(row_dict.get(source_col, "")), str(row_dict.get(target_col, "")))

            if row_ts is not None:
                if row_ts >= cutoff_14:
                    if score_val is not None:
                        recent_scores.append(score_val)
                    if pair:
                        recent_pairs.add(pair)
                elif row_ts >= cutoff_28:
                    if score_val is not None:
                        older_scores.append(score_val)
                    if pair:
                        older_pairs.add(pair)
            else:
                # No timestamp: split by position (first half = recent, second half = older)
                pass

        # Fallback: if no timestamp parsing worked, split by rowid position
        if not recent_scores and not older_scores and score_col:
            half = len(rows) // 2
            for i, row in enumerate(rows):
                row_dict = dict(row)
                sv = None
                try:
                    sv = float(row_dict.get(score_col, None))
                except (TypeError, ValueError):
                    pass
                if sv is not None:
                    if i < half:
                        recent_scores.append(sv)
                    else:
                        older_scores.append(sv)
                if source_col and target_col:
                    pair = (str(row_dict.get(source_col, "")), str(row_dict.get(target_col, "")))
                    if i < half:
                        recent_pairs.add(pair)
                    else:
                        older_pairs.add(pair)

        recent_avg_contagion = round(statistics.mean(recent_scores), 4) if recent_scores else 0.0
        old_avg_contagion = round(statistics.mean(older_scores), 4) if older_scores else 0.0

        if old_avg_contagion > 0:
            fragmentation_rate = round((old_avg_contagion - recent_avg_contagion) / old_avg_contagion, 4)
        else:
            fragmentation_rate = 0.0

        # Disconnected sectors: pairs in older but not in recent
        lost_pairs = older_pairs - recent_pairs
        n_link_pairs_lost = len(lost_pairs)
        disconnected_sectors = list({p[0] for p in lost_pairs} | {p[1] for p in lost_pairs})[:20]

        conn.close()
    except Exception as exc:
        return {
            "fragmentation_rate": 0.0,
            "fragmentation_level": "STABLE",
            "old_avg_contagion": 0.0,
            "recent_avg_contagion": 0.0,
            "disconnected_sectors": [],
            "n_link_pairs_lost": 0,
            "alert": False,
            "recommendation": f"Error computing graph fragmentation: {exc}",
        }

    if fragmentation_rate < 0.10:
        fragmentation_level = "STABLE"
    elif fragmentation_rate < 0.25:
        fragmentation_level = "MODERATE"
    elif fragmentation_rate < 0.40:
        fragmentation_level = "HIGH"
    else:
        fragmentation_level = "SEVERE"

    alert = fragmentation_level in ("HIGH", "SEVERE")

    if fragmentation_level == "STABLE":
        recommendation = "Contagion graph is stable. Sector relationships are intact."
    elif fragmentation_level == "MODERATE":
        recommendation = (
            "Moderate graph fragmentation detected. Some sector links are weakening. "
            "Review contagion model inputs."
        )
    elif fragmentation_level == "HIGH":
        recommendation = (
            "High fragmentation: significant sector decoupling detected. "
            "Contagion predictions may be unreliable. Increase manual validation."
        )
    else:
        recommendation = (
            "SEVERE: Knowledge graph is breaking down. Sector relationships have "
            "collapsed significantly. Predictions are unreliable — halt reliance on "
            "contagion-based signals immediately."
        )

    return {
        "fragmentation_rate": fragmentation_rate,
        "fragmentation_level": fragmentation_level,
        "old_avg_contagion": old_avg_contagion,
        "recent_avg_contagion": recent_avg_contagion,
        "disconnected_sectors": disconnected_sectors,
        "n_link_pairs_lost": n_link_pairs_lost,
        "alert": alert,
        "recommendation": recommendation,
    }


def _run_enhanced_metrics():
    """Run all 6 enhanced health metrics and return combined dict."""
    params = {}
    return {
        "latency_drift": latency_drift(params),
        "freshness_degradation": freshness_degradation(params),
        "regime_detector_disagreement": regime_detector_disagreement(params),
        "causal_instability_spikes": causal_instability_spikes(params),
        "model_entropy": model_entropy(params),
        "graph_fragmentation": graph_fragmentation(params),
    }


def enhanced_health(params):
    """
    Run all 6 enhanced health metrics together.
    Returns a combined report with an overall_enhancement_score 0-100.
    """
    metrics = _run_enhanced_metrics()
    generated_at = datetime.datetime.utcnow().isoformat()

    # --- Score computation (0-100 total) ---
    # Each dimension contributes ~16.7 points

    score = 100.0

    # 1. Latency drift penalty
    ld = metrics["latency_drift"]
    n_deg = ld.get("n_degrading", 0)
    n_engines = max(len(ENGINE_REGISTRY), 1)
    score -= (n_deg / n_engines) * 20

    # 2. Freshness degradation penalty
    fd = metrics["freshness_degradation"]
    fsh = fd.get("system_freshness_health", "FRESH")
    if fsh == "CRITICAL":
        score -= 20
    elif fsh == "STALE":
        score -= 10

    # 3. Regime disagreement penalty
    rd = metrics["regime_detector_disagreement"]
    if rd.get("disagreement_detected", False):
        n_conflicts = len(rd.get("conflict_pairs", []))
        score -= min(15, n_conflicts * 5)

    # 4. Causal instability penalty
    ci = metrics["causal_instability_spikes"]
    severity = ci.get("spike_severity", "NONE")
    penalty_map = {"NONE": 0, "MINOR": 5, "MODERATE": 10, "SEVERE": 20}
    score -= penalty_map.get(severity, 0)

    # 5. Model entropy penalty
    me = metrics["model_entropy"]
    el = me.get("entropy_level", "HEALTHY")
    if el == "CRITICAL":
        score -= 20
    elif el == "HIGH":
        score -= 10
    elif el == "LOW":
        score -= 5

    # 6. Graph fragmentation penalty
    gf = metrics["graph_fragmentation"]
    fl = gf.get("fragmentation_level", "STABLE")
    frag_penalty = {"STABLE": 0, "MODERATE": 5, "HIGH": 10, "SEVERE": 20}
    score -= frag_penalty.get(fl, 0)

    overall_enhancement_score = max(0.0, min(100.0, round(score, 1)))

    if overall_enhancement_score >= 80:
        overall_status = "HEALTHY"
    elif overall_enhancement_score >= 55:
        overall_status = "DEGRADED"
    elif overall_enhancement_score >= 30:
        overall_status = "POOR"
    else:
        overall_status = "CRITICAL"

    alerts = []
    if ld.get("n_degrading", 0) > 2:
        alerts.append("Latency drift: multiple engines slowing down")
    if fsh == "CRITICAL":
        alerts.append("Freshness: critical data staleness")
    if rd.get("disagreement_detected"):
        alerts.append("Regime disagreement: engines in conflict")
    if ci.get("spike_detected"):
        alerts.append(f"Causal instability spike: {severity}")
    if me.get("alert"):
        alerts.append(f"Model entropy alert: {el}")
    if gf.get("alert"):
        alerts.append(f"Graph fragmentation: {fl}")

    return {
        "overall_enhancement_score": overall_enhancement_score,
        "overall_status": overall_status,
        "alerts": alerts,
        "metrics": metrics,
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# Command Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "engine_health":                  engine_health,
    "system_trustability":            system_trustability,
    "detect_failures":                detect_failures,
    "inter_engine_agreement":         inter_engine_agreement,
    "health_report":                  health_report,
    "build_full":                     build_full,
    "latency_drift":                  latency_drift,
    "freshness_degradation":          freshness_degradation,
    "regime_detector_disagreement":   regime_detector_disagreement,
    "causal_instability_spikes":      causal_instability_spikes,
    "model_entropy":                  model_entropy,
    "graph_fragmentation":            graph_fragmentation,
    "enhanced_health":                enhanced_health,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "Usage: intelligence_observatory.py <command> '<json_params>'",
            "available_commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON params: {exc}"}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({
            "error": f"Unknown command: '{cmd}'",
            "available_commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as exc:
        result = {"error": str(exc), "command": cmd}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
