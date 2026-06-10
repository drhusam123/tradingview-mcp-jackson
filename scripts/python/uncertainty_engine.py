#!/usr/bin/env python3
"""
uncertainty_engine.py — Phase 39
EGX Autonomous Quant System: Uncertainty Quantification Engine

Separates epistemic uncertainty (reducible) from aleatoric uncertainty (irreducible),
detects out-of-distribution market regimes, and propagates error through the pipeline.

Invocation: python uncertainty_engine.py <command> '<json_params>'
Output: last stdout line = valid JSON

Commands:
  epistemic_symbol   — epistemic (knowledge) uncertainty for one symbol
  aleatoric_symbol   — aleatoric (market noise) uncertainty for one symbol
  ood_detection      — out-of-distribution detection for current market regime
  propagate          — error propagation across the full decision pipeline
  uncertainty_report — full market-wide uncertainty report
  build_full         — run uncertainty_report and persist to DB
"""

import os
import sys
import json
import math
import sqlite3
import statistics
import random
from datetime import datetime, date, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths & DB
# ---------------------------------------------------------------------------

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# DB Helper
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

# ---------------------------------------------------------------------------
# Cosine Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(a, b):
    """Cosine similarity between two vectors (lists of floats)."""
    if not a or not b:
        return 0.0
    # Pad to same length
    length = max(len(a), len(b))
    a = list(a) + [0.0] * (length - len(a))
    b = list(b) + [0.0] * (length - len(b))
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

# ---------------------------------------------------------------------------
# Quadrature (error propagation)
# ---------------------------------------------------------------------------

def quadrature(*uncertainties):
    """Total uncertainty via quadrature sum: sqrt(u1² + u2² + ...)"""
    total = math.sqrt(sum(u * u for u in uncertainties))
    return min(total, 1.0)

# ---------------------------------------------------------------------------
# epistemic_symbol
# ---------------------------------------------------------------------------

def epistemic_symbol(params):
    """
    Compute epistemic (knowledge) uncertainty for a symbol.
    Returns: symbol, epistemic_uncertainty, confidence_in_knowledge,
             components, interpretation, reducible_by
    """
    symbol = params.get("symbol", "MARKET")

    # --- Component 1: law_coverage ---
    law_coverage_uncertainty = 0.5  # default: uncertain
    n_laws = 0
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM pattern_laws "
            "WHERE symbol = ? OR symbol = 'UNIVERSAL' OR symbol IS NULL",
            (symbol,)
        ).fetchone()
        if rows:
            n_laws = rows["cnt"] or 0
        conn.close()
    except Exception:
        pass

    law_coverage_uncertainty = 1.0 - min(n_laws / 10.0, 1.0)

    # --- Component 2: data_recency ---
    data_recency_uncertainty = 0.5  # default: 15 days stale
    days_stale = 15
    try:
        conn = get_db()
        # Try market_data first
        row = conn.execute(
            "SELECT MAX(date) as last_date FROM market_data WHERE symbol = ?",
            (symbol,)
        ).fetchone()
        if row and row["last_date"]:
            last_date = datetime.strptime(row["last_date"][:10], "%Y-%m-%d").date()
            days_stale = (date.today() - last_date).days
        else:
            # Fallback: check predictions table
            row2 = conn.execute(
                "SELECT MAX(created_at) as last_date FROM predictions WHERE symbol = ?",
                (symbol,)
            ).fetchone()
            if row2 and row2["last_date"]:
                last_date = datetime.strptime(row2["last_date"][:10], "%Y-%m-%d").date()
                days_stale = (date.today() - last_date).days
        conn.close()
    except Exception:
        pass

    data_recency_uncertainty = min(days_stale / 30.0, 1.0)

    # --- Component 3: regime_confidence ---
    regime_confidence_uncertainty = 0.5  # default
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT confidence FROM market_regimes "
            "ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        if row and row["confidence"] is not None:
            regime_conf = float(row["confidence"])
            regime_confidence_uncertainty = 1.0 - regime_conf
        conn.close()
    except Exception:
        pass

    # --- Component 4: model_disagreement ---
    model_disagreement_uncertainty = 0.5  # default
    try:
        conn = get_db()

        scores = []

        # Intelligence score
        row = conn.execute(
            "SELECT intelligence_score FROM market_intelligence WHERE symbol = ? "
            "ORDER BY last_updated DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row and row["intelligence_score"] is not None:
            scores.append(float(row["intelligence_score"]))

        # Arbitration confidence
        row = conn.execute(
            "SELECT confidence FROM arbitration_decisions WHERE symbol = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row and row["confidence"] is not None:
            scores.append(float(row["confidence"]))

        # Prediction confidence
        row = conn.execute(
            "SELECT confidence FROM predictions WHERE symbol = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row and row["confidence"] is not None:
            scores.append(float(row["confidence"]))

        conn.close()

        if len(scores) >= 2:
            mean_score = statistics.mean(scores)
            std_score  = statistics.stdev(scores) if len(scores) > 1 else 0.0
            if mean_score > 0:
                model_disagreement_uncertainty = min(std_score / mean_score, 1.0)
            else:
                model_disagreement_uncertainty = 0.5

    except Exception:
        pass

    # --- Weighted average ---
    weights = [0.30, 0.20, 0.25, 0.25]
    components_raw = [
        law_coverage_uncertainty,
        data_recency_uncertainty,
        regime_confidence_uncertainty,
        model_disagreement_uncertainty,
    ]
    epistemic_uncertainty = sum(w * c for w, c in zip(weights, components_raw))
    confidence_in_knowledge = 1.0 - epistemic_uncertainty

    # --- Interpretation ---
    if epistemic_uncertainty < 0.3:
        interpretation = "LOW epistemic uncertainty — system has strong knowledge of this symbol."
    elif epistemic_uncertainty < 0.5:
        interpretation = "MODERATE epistemic uncertainty — knowledge gaps exist but manageable."
    elif epistemic_uncertainty < 0.7:
        interpretation = "HIGH epistemic uncertainty — significant knowledge gaps; collect more data."
    else:
        interpretation = "VERY HIGH epistemic uncertainty — system barely knows this symbol; avoid trading."

    # --- Reducible by ---
    reducible_by = []
    if law_coverage_uncertainty > 0.4:
        reducible_by.append("Run law_synthesis to build more pattern laws for this symbol")
    if data_recency_uncertainty > 0.4:
        reducible_by.append("Refresh market_data — data is stale")
    if regime_confidence_uncertainty > 0.4:
        reducible_by.append("Run regime_transition_forecaster for fresher regime detection")
    if model_disagreement_uncertainty > 0.4:
        reducible_by.append("Resolve model disagreement via cognitive_arbitration re-run")
    if not reducible_by:
        reducible_by.append("Epistemic uncertainty is already well-controlled")

    return {
        "symbol": symbol,
        "epistemic_uncertainty": round(epistemic_uncertainty, 4),
        "confidence_in_knowledge": round(confidence_in_knowledge, 4),
        "components": {
            "law_coverage": round(law_coverage_uncertainty, 4),
            "data_recency": round(data_recency_uncertainty, 4),
            "regime_confidence": round(regime_confidence_uncertainty, 4),
            "model_disagreement": round(model_disagreement_uncertainty, 4),
        },
        "interpretation": interpretation,
        "reducible_by": reducible_by,
        "n_laws_found": n_laws,
        "days_data_stale": days_stale,
    }

# ---------------------------------------------------------------------------
# aleatoric_symbol
# ---------------------------------------------------------------------------

def aleatoric_symbol(params):
    """
    Compute aleatoric (market noise) uncertainty for a symbol.
    Returns: symbol, aleatoric_uncertainty, components, interpretation, irreducible
    """
    symbol = params.get("symbol", "MARKET")

    # --- Component 1: price_volatility ---
    # Normalized: 2% daily vol = 0.5 baseline
    price_volatility_uncertainty = 0.5  # default
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT close FROM market_data WHERE symbol = ? "
            "ORDER BY date DESC LIMIT 22",
            (symbol,)
        ).fetchall()
        closes = [float(r["close"]) for r in rows if r["close"] is not None]
        conn.close()

        if len(closes) >= 5:
            returns = []
            for i in range(1, len(closes)):
                if closes[i] > 0:
                    ret = (closes[i - 1] - closes[i]) / closes[i]
                    returns.append(ret)
            if returns:
                daily_vol = statistics.stdev(returns) if len(returns) > 1 else abs(returns[0])
                # 2% daily vol = baseline 0.5; scale linearly, cap at 1
                price_volatility_uncertainty = min(daily_vol / 0.04, 1.0)

    except Exception:
        pass

    # --- Component 2: volume_irregularity ---
    volume_irregularity_uncertainty = 0.5  # default
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT volume FROM market_data WHERE symbol = ? "
            "ORDER BY date DESC LIMIT 22",
            (symbol,)
        ).fetchall()
        volumes = [float(r["volume"]) for r in rows if r["volume"] is not None and float(r["volume"]) > 0]
        conn.close()

        if len(volumes) >= 3:
            mean_vol = statistics.mean(volumes)
            std_vol  = statistics.stdev(volumes) if len(volumes) > 1 else 0.0
            if mean_vol > 0:
                cv = std_vol / mean_vol  # coefficient of variation
                volume_irregularity_uncertainty = min(cv, 1.0)

    except Exception:
        pass

    # --- Component 3: regime_instability ---
    regime_instability_uncertainty = 0.3  # default: moderate stability
    try:
        conn = get_db()
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        rows = conn.execute(
            "SELECT regime FROM market_regimes WHERE detected_at >= ? "
            "ORDER BY detected_at ASC",
            (cutoff,)
        ).fetchall()
        conn.close()

        if len(rows) >= 2:
            regimes = [r["regime"] for r in rows]
            changes = sum(1 for i in range(1, len(regimes)) if regimes[i] != regimes[i - 1])
            # Normalize: 5+ changes in 30 days = very unstable (1.0)
            regime_instability_uncertainty = min(changes / 5.0, 1.0)

    except Exception:
        pass

    # --- Component 4: catalyst_proximity ---
    catalyst_proximity_uncertainty = 0.2  # default: no catalyst nearby
    try:
        conn = get_db()
        today_dt = date.today()
        lookahead = (today_dt + timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT event_date FROM catalyst_events "
            "WHERE (symbol = ? OR symbol = 'MARKET') "
            "AND event_date BETWEEN ? AND ?",
            (symbol, TODAY, lookahead)
        ).fetchall()
        conn.close()

        if rows:
            # Find nearest catalyst
            min_days = 7
            for r in rows:
                try:
                    ev_date = datetime.strptime(r["event_date"][:10], "%Y-%m-%d").date()
                    diff = (ev_date - today_dt).days
                    if 0 <= diff < min_days:
                        min_days = diff
                except Exception:
                    pass
            # 0 days away = 1.0; 7 days away = ~0
            catalyst_proximity_uncertainty = max(0.0, 1.0 - min_days / 7.0)

    except Exception:
        pass

    # --- Weighted average ---
    weights = [0.35, 0.20, 0.25, 0.20]
    components_raw = [
        price_volatility_uncertainty,
        volume_irregularity_uncertainty,
        regime_instability_uncertainty,
        catalyst_proximity_uncertainty,
    ]
    aleatoric_uncertainty = sum(w * c for w, c in zip(weights, components_raw))

    # --- Interpretation ---
    if aleatoric_uncertainty < 0.3:
        interpretation = (
            "LOW aleatoric uncertainty — market is relatively calm for this symbol. "
            "Signal-to-noise ratio is favorable."
        )
    elif aleatoric_uncertainty < 0.5:
        interpretation = (
            "MODERATE aleatoric uncertainty — typical market noise. "
            "Use wider stops to accommodate randomness."
        )
    elif aleatoric_uncertainty < 0.7:
        interpretation = (
            "HIGH aleatoric uncertainty — this symbol is behaving erratically. "
            "Reduce position size; noise may overwhelm edge."
        )
    else:
        interpretation = (
            "VERY HIGH aleatoric uncertainty — market randomness is extreme. "
            "This cannot be modeled away. Consider standing aside."
        )

    return {
        "symbol": symbol,
        "aleatoric_uncertainty": round(aleatoric_uncertainty, 4),
        "components": {
            "price_volatility": round(price_volatility_uncertainty, 4),
            "volume_irregularity": round(volume_irregularity_uncertainty, 4),
            "regime_instability": round(regime_instability_uncertainty, 4),
            "catalyst_proximity": round(catalyst_proximity_uncertainty, 4),
        },
        "interpretation": interpretation,
        "irreducible": True,
    }

# ---------------------------------------------------------------------------
# ood_detection
# ---------------------------------------------------------------------------

def ood_detection(params):
    """
    Out-of-Distribution detection — is current market in a regime unseen
    in historical memory? Uses episodic memory fingerprints (Phase 30).
    """
    DEFAULT_FINGERPRINT = [0.5, 0.5, 0.5, 0.5, 0.5]

    episodes = []
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT episode_date, fingerprint FROM market_episodes "
            "ORDER BY episode_date DESC LIMIT 50"
        ).fetchall()
        conn.close()

        for r in rows:
            ep_date = r["episode_date"] if r["episode_date"] else "unknown"
            fp_raw  = r["fingerprint"]
            if fp_raw:
                try:
                    fp = json.loads(fp_raw) if isinstance(fp_raw, str) else list(fp_raw)
                    if isinstance(fp, list) and len(fp) > 0:
                        episodes.append({"date": ep_date, "fingerprint": fp})
                except Exception:
                    pass

    except Exception:
        pass

    n_episodes = len(episodes)

    # Current fingerprint: average of the most recent 3 episodes
    if n_episodes >= 3:
        recent = episodes[:3]
        fp_length = max(len(e["fingerprint"]) for e in recent)
        current_fp = []
        for i in range(fp_length):
            vals = [e["fingerprint"][i] for e in recent if i < len(e["fingerprint"])]
            current_fp.append(statistics.mean(vals) if vals else 0.5)
    elif n_episodes > 0:
        current_fp = episodes[0]["fingerprint"]
    else:
        current_fp = DEFAULT_FINGERPRINT

    # Compare current fingerprint against all historical episodes
    max_similarity = 0.0
    most_similar_date = "N/A"

    historical = episodes[3:] if n_episodes >= 3 else episodes

    if not historical:
        # No historical data — treat as OOD
        ood_score = 0.75
        most_similar_date = "N/A"
        max_similarity = 0.0
    else:
        for ep in historical:
            sim = cosine_similarity(current_fp, ep["fingerprint"])
            if sim > max_similarity:
                max_similarity = sim
                most_similar_date = ep["date"]
        ood_score = 1.0 - max_similarity

    # OOD level classification
    if ood_score > 0.7:
        ood_level = "EXTREME_OOD"
        interpretation = (
            "Current market regime has NEVER been seen in historical memory. "
            "All model predictions are unreliable — operate in extreme caution mode."
        )
        action = "HALT — suspend automated trading until regime normalises"
    elif ood_score > 0.5:
        ood_level = "HIGH_OOD"
        interpretation = (
            "Current market conditions are very unusual. "
            "Historical patterns may not apply. Reduce position sizes significantly."
        )
        action = "REDUCE — cut position sizing to 25% of normal"
    elif ood_score > 0.3:
        ood_level = "MODERATE_OOD"
        interpretation = (
            "Market is somewhat outside historical norms. "
            "Apply caution and use tighter risk controls."
        )
        action = "CAUTION — reduce sizing to 50% and widen stops"
    else:
        ood_level = "IN_DISTRIBUTION"
        interpretation = (
            "Current market regime closely resembles historical episodes. "
            "Model predictions are likely reliable."
        )
        action = "NORMAL — proceed with standard position sizing"

    return {
        "ood_score": round(ood_score, 4),
        "ood_level": ood_level,
        "most_similar_episode_date": most_similar_date,
        "similarity": round(max_similarity, 4),
        "n_episodes_checked": n_episodes,
        "current_fingerprint_length": len(current_fp),
        "interpretation": interpretation,
        "action": action,
    }

# ---------------------------------------------------------------------------
# propagate
# ---------------------------------------------------------------------------

def propagate(params):
    """
    Error propagation across the full decision pipeline.
    Computes how uncertainty compounds from raw data → laws → regime →
    prediction → arbitration.
    """
    # --- Stage 1: data_uncertainty ---
    data_uncertainty = 0.1  # default: high-quality data
    try:
        conn = get_db()
        # Check for any data quality flags or null rates
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN close IS NULL OR close <= 0 THEN 1 ELSE 0 END) as bad "
            "FROM market_data"
        ).fetchone()
        if row and row["total"] and row["total"] > 0:
            null_rate = (row["bad"] or 0) / row["total"]
            data_uncertainty = min(0.05 + null_rate * 2.0, 1.0)
        conn.close()
    except Exception:
        pass

    # --- Stage 2: law_uncertainty ---
    law_uncertainty = 0.5  # default
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT precision_score FROM pattern_laws WHERE is_active = 1"
        ).fetchall()
        conn.close()
        precisions = [float(r["precision_score"]) for r in rows
                      if r["precision_score"] is not None]
        if precisions:
            avg_precision = statistics.mean(precisions)
            law_uncertainty = 1.0 - avg_precision
    except Exception:
        pass

    # --- Stage 3: regime_uncertainty ---
    regime_uncertainty = 0.5  # default
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT confidence FROM market_regimes "
            "ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        if row and row["confidence"] is not None:
            regime_uncertainty = 1.0 - float(row["confidence"])
        conn.close()
    except Exception:
        pass

    # --- Stage 4: prediction_uncertainty ---
    prediction_uncertainty = 0.5  # default
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT confidence FROM predictions "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 50",
            ((date.today() - timedelta(days=3)).isoformat(),)
        ).fetchall()
        conn.close()
        confs = [float(r["confidence"]) for r in rows if r["confidence"] is not None]
        if confs:
            prediction_uncertainty = 1.0 - statistics.mean(confs)
    except Exception:
        pass

    # --- Stage 5: arbitration_uncertainty ---
    arbitration_uncertainty = 0.5  # default
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT confidence FROM arbitration_decisions "
            "WHERE decided_at >= ? ORDER BY decided_at DESC LIMIT 50",
            ((date.today() - timedelta(days=3)).isoformat(),)
        ).fetchall()
        conn.close()
        confs = [float(r["confidence"]) for r in rows if r["confidence"] is not None]
        if confs:
            arbitration_uncertainty = 1.0 - statistics.mean(confs)
    except Exception:
        pass

    # --- Quadrature propagation ---
    after_laws       = quadrature(data_uncertainty, law_uncertainty)
    after_regime     = quadrature(after_laws, regime_uncertainty)
    after_prediction = quadrature(after_regime, prediction_uncertainty)
    total_uncertainty = quadrature(after_prediction, arbitration_uncertainty)

    stages = [
        {
            "stage": "data",
            "uncertainty": round(data_uncertainty, 4),
            "propagated": round(data_uncertainty, 4),
        },
        {
            "stage": "laws",
            "uncertainty": round(law_uncertainty, 4),
            "propagated": round(after_laws, 4),
        },
        {
            "stage": "regime",
            "uncertainty": round(regime_uncertainty, 4),
            "propagated": round(after_regime, 4),
        },
        {
            "stage": "prediction",
            "uncertainty": round(prediction_uncertainty, 4),
            "propagated": round(after_prediction, 4),
        },
        {
            "stage": "arbitration",
            "uncertainty": round(arbitration_uncertainty, 4),
            "propagated": round(total_uncertainty, 4),
        },
    ]

    # --- Bottleneck: highest individual stage uncertainty ---
    stage_uncertainties = {
        "data": data_uncertainty,
        "laws": law_uncertainty,
        "regime": regime_uncertainty,
        "prediction": prediction_uncertainty,
        "arbitration": arbitration_uncertainty,
    }
    bottleneck = max(stage_uncertainties, key=stage_uncertainties.get)
    bottleneck_value = stage_uncertainties[bottleneck]

    # --- Recommendation ---
    pipeline_confidence = 1.0 - total_uncertainty
    if pipeline_confidence >= 0.7:
        recommendation = (
            f"Pipeline confidence is strong ({pipeline_confidence:.0%}). "
            f"Primary bottleneck is '{bottleneck}' stage — acceptable for live trading."
        )
    elif pipeline_confidence >= 0.5:
        recommendation = (
            f"Pipeline confidence is moderate ({pipeline_confidence:.0%}). "
            f"Improve '{bottleneck}' stage before increasing position sizes."
        )
    else:
        recommendation = (
            f"Pipeline confidence is LOW ({pipeline_confidence:.0%}). "
            f"'{bottleneck}' stage is severely degrading output quality. "
            f"Do NOT trade with full sizing until resolved."
        )

    return {
        "stages": stages,
        "total_uncertainty": round(total_uncertainty, 4),
        "pipeline_confidence": round(pipeline_confidence, 4),
        "bottleneck": bottleneck,
        "bottleneck_uncertainty": round(bottleneck_value, 4),
        "recommendation": recommendation,
    }

# ---------------------------------------------------------------------------
# uncertainty_report
# ---------------------------------------------------------------------------

def _compute_market_wide_epistemic():
    """Average epistemic uncertainty across all active symbols."""
    symbols = []
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM market_data LIMIT 30"
        ).fetchall()
        symbols = [r["symbol"] for r in rows if r["symbol"]]
        conn.close()
    except Exception:
        pass

    if not symbols:
        # Fallback defaults
        return 0.5, 0.5

    epist_scores = []
    aleat_scores = []
    sample = symbols[:10]  # cap to avoid slowness

    for sym in sample:
        try:
            e = epistemic_symbol({"symbol": sym})
            epist_scores.append(e["epistemic_uncertainty"])
        except Exception:
            epist_scores.append(0.5)
        try:
            a = aleatoric_symbol({"symbol": sym})
            aleat_scores.append(a["aleatoric_uncertainty"])
        except Exception:
            aleat_scores.append(0.5)

    me = statistics.mean(epist_scores) if epist_scores else 0.5
    ma = statistics.mean(aleat_scores) if aleat_scores else 0.5
    return me, ma


def uncertainty_report(params):
    """
    Full uncertainty report: epistemic + aleatoric + ood + propagate
    for the overall market (not one symbol).
    """
    market_epistemic, market_aleatoric = _compute_market_wide_epistemic()

    ood_result   = ood_detection({})
    prop_result  = propagate({})

    total_market_uncertainty = quadrature(market_epistemic, market_aleatoric)

    # Uncertainty budget fractions
    total_raw = market_epistemic + market_aleatoric
    if total_raw > 0:
        epistemic_fraction = market_epistemic / total_raw
        aleatoric_fraction = market_aleatoric / total_raw
    else:
        epistemic_fraction = 0.5
        aleatoric_fraction = 0.5

    # Overall interpretation
    if total_market_uncertainty < 0.35:
        interpretation = (
            "Overall market uncertainty is LOW. Conditions are favourable for "
            "systematic strategy deployment."
        )
        trading_recommendation = (
            "DEPLOY NORMALLY — run strategies at full allocation. "
            "Epistemic risk is reducible with standard data refreshes."
        )
    elif total_market_uncertainty < 0.55:
        interpretation = (
            "Overall market uncertainty is MODERATE. A mix of knowledge gaps and "
            "market noise is present."
        )
        trading_recommendation = (
            "DEPLOY CAUTIOUSLY — reduce allocation to 60-70% of normal. "
            "Focus on highest-confidence signals only."
        )
    elif total_market_uncertainty < 0.75:
        interpretation = (
            "Overall market uncertainty is HIGH. Both epistemic and aleatoric "
            "components are elevated."
        )
        trading_recommendation = (
            "REDUCE EXPOSURE — deploy at 30-50% allocation. "
            "Consider mean-reversion strategies only (lower directional risk)."
        )
    else:
        interpretation = (
            "Overall market uncertainty is EXTREME. The system is operating near "
            "the limits of its predictive capability."
        )
        trading_recommendation = (
            "STAND ASIDE — suspend new entries. "
            "Run law_synthesis, update market_data, and re-assess before trading."
        )

    # Add OOD warning if applicable
    if ood_result["ood_level"] in ("EXTREME_OOD", "HIGH_OOD"):
        trading_recommendation += (
            f" OOD WARNING: {ood_result['ood_level']} — {ood_result['action']}."
        )

    return {
        "market_epistemic": round(market_epistemic, 4),
        "market_aleatoric": round(market_aleatoric, 4),
        "total_market_uncertainty": round(total_market_uncertainty, 4),
        "ood": ood_result,
        "propagation": prop_result,
        "uncertainty_budget": {
            "epistemic_fraction": round(epistemic_fraction, 4),
            "aleatoric_fraction": round(aleatoric_fraction, 4),
        },
        "interpretation": interpretation,
        "trading_recommendation": trading_recommendation,
    }

# ---------------------------------------------------------------------------
# build_full
# ---------------------------------------------------------------------------

def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uncertainty_estimates (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT,
            epistemic      REAL,
            aleatoric      REAL,
            total          REAL,
            ood_score      REAL,
            ood_level      TEXT,
            estimated_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uncertainty_reports (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            market_epistemic    REAL,
            market_aleatoric    REAL,
            total_uncertainty   REAL,
            ood_score           REAL,
            pipeline_confidence REAL,
            generated_at        TEXT
        )
    """)
    conn.commit()


def build_full(params):
    """
    Run uncertainty_report, persist aggregate results, and return summary.
    Also computes per-symbol estimates for active symbols.
    """
    report = uncertainty_report(params)

    now_str = datetime.utcnow().isoformat()

    try:
        conn = get_db()
        _ensure_tables(conn)

        # Save aggregate report
        conn.execute(
            "INSERT INTO uncertainty_reports "
            "(market_epistemic, market_aleatoric, total_uncertainty, ood_score, "
            "pipeline_confidence, generated_at) VALUES (?,?,?,?,?,?)",
            (
                report["market_epistemic"],
                report["market_aleatoric"],
                report["total_market_uncertainty"],
                report["ood"]["ood_score"],
                report["propagation"]["pipeline_confidence"],
                now_str,
            )
        )

        # Save per-symbol estimates for active symbols
        symbols = []
        try:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM market_data LIMIT 30"
            ).fetchall()
            symbols = [r["symbol"] for r in rows if r["symbol"]]
        except Exception:
            pass

        for sym in symbols[:20]:
            try:
                e_res = epistemic_symbol({"symbol": sym})
                a_res = aleatoric_symbol({"symbol": sym})
                total = quadrature(
                    e_res["epistemic_uncertainty"],
                    a_res["aleatoric_uncertainty"]
                )
                conn.execute(
                    "INSERT INTO uncertainty_estimates "
                    "(symbol, epistemic, aleatoric, total, ood_score, ood_level, estimated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        sym,
                        e_res["epistemic_uncertainty"],
                        a_res["aleatoric_uncertainty"],
                        round(total, 4),
                        report["ood"]["ood_score"],
                        report["ood"]["ood_level"],
                        now_str,
                    )
                )
            except Exception:
                pass

        conn.commit()
        conn.close()
        db_status = "saved"

    except Exception as ex:
        db_status = f"db_error: {ex}"

    return {
        "status": "built",
        "total_uncertainty": report["total_market_uncertainty"],
        "pipeline_confidence": report["propagation"]["pipeline_confidence"],
        "ood_level": report["ood"]["ood_level"],
        "interpretation": report["interpretation"],
        "trading_recommendation": report["trading_recommendation"],
        "db_status": db_status,
        "generated_at": now_str,
    }

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "epistemic_symbol":  epistemic_symbol,
    "aleatoric_symbol":  aleatoric_symbol,
    "ood_detection":     ood_detection,
    "propagate":         propagate,
    "uncertainty_report": uncertainty_report,
    "build_full":        build_full,
}

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "Usage: python uncertainty_engine.py <command> '<json_params>'",
            "commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1]
    params = json.loads(sys.argv[2])

    if cmd not in COMMANDS:
        print(json.dumps({
            "error": f"Unknown command: {cmd}",
            "commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "command": cmd,
        }))
        sys.exit(1)
