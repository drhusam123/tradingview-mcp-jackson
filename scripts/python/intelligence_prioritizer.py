#!/usr/bin/env python3
"""
intelligence_prioritizer.py — Phase 29
EGX Autonomous Quant System: Intelligence Prioritization Layer

Compresses ALL signals from the other 28 phases into a daily actionable brief:
  - Top 3 insights
  - Dominant market force
  - Anomaly of the day
  - Intelligence score per symbol (0-100)

Usage:
    python intelligence_prioritizer.py <command> [json_params]

Commands:
    prioritize      — compute intelligence scores for all symbols
    top_insights    — get the top 3 most actionable insights today
    anomaly_today   — detect what changed abnormally today vs yesterday
    score_symbol    — deep score for one symbol (params: {"symbol": "COMI"})
    daily_brief     — executive summary (crown jewel output)
    build_full      — run prioritize + top_insights + anomaly_today sequentially
"""

import os
import sys
import json
import math
import sqlite3
import datetime
from collections import defaultdict

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS intelligence_scores (
        symbol TEXT,
        date TEXT,
        intelligence_score REAL,
        explosion_component REAL,
        law_component REAL,
        execution_component REAL,
        regime_component REAL,
        causal_component REAL,
        primary_driver TEXT,
        percentile_rank REAL,
        data_quality TEXT,
        computed_at TEXT,
        PRIMARY KEY (symbol, date)
    );

    CREATE TABLE IF NOT EXISTS daily_intelligence_brief (
        date TEXT PRIMARY KEY,
        market_state TEXT,
        dominant_force TEXT,
        risk_level TEXT,
        top_3_insights TEXT,
        top_5_symbols TEXT,
        anomaly_count INTEGER,
        key_anomaly TEXT,
        regime_stability TEXT,
        actionable_today INTEGER,
        brief_summary TEXT,
        computed_at TEXT
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Safe query helpers — all table reads are guarded
# ---------------------------------------------------------------------------

def safe_fetchall(db, sql, params=()):
    """Execute a query and return rows as dicts, empty list on any error."""
    try:
        cur = db.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def safe_fetchone(db, sql, params=()):
    """Execute a query and return one row as dict, None on any error."""
    try:
        cur = db.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def table_exists(db, name):
    """Return True if the named table exists in the database."""
    row = safe_fetchone(
        db,
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    )
    return row is not None


# ---------------------------------------------------------------------------
# Data-loading helpers (graceful on missing tables)
# ---------------------------------------------------------------------------

def load_symbols(db):
    """Return distinct symbols from ohlcv, or empty list."""
    rows = safe_fetchall(db, "SELECT DISTINCT symbol FROM ohlcv")
    return [r["symbol"] for r in rows]


def load_explosion_readiness(db):
    """Return {symbol: {readiness_score, compression_index}} from latest rows."""
    result = {}
    rows = safe_fetchall(
        db,
        """
        SELECT e.symbol, e.readiness_score, e.compression_index
        FROM explosion_readiness e
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date
            FROM explosion_readiness
            GROUP BY symbol
        ) latest ON e.symbol = latest.symbol AND e.date = latest.max_date
        """
    )
    for r in rows:
        result[r["symbol"]] = {
            "readiness_score": float(r.get("readiness_score") or 0),
            "compression_index": float(r.get("compression_index") or 0),
        }
    return result


def load_liquidity_profiles(db):
    """Return {symbol: {tier, avg_daily_volume}}."""
    result = {}
    rows = safe_fetchall(db, "SELECT symbol, tier, avg_daily_volume FROM liquidity_profiles")
    for r in rows:
        result[r["symbol"]] = {
            "tier": (r.get("tier") or "ILLIQUID").upper(),
            "avg_daily_volume": float(r.get("avg_daily_volume") or 0),
        }
    return result


def load_pattern_laws(db):
    """Return list of {pattern_name, precision, last_validated}."""
    return safe_fetchall(
        db, "SELECT pattern_name, precision, last_validated FROM pattern_laws"
    )


def load_failure_intelligence(db):
    """Return {symbol: {archetype, confidence}} from most recent per symbol."""
    result = {}
    # Try 'confidence' column first, fall back gracefully
    rows = safe_fetchall(
        db,
        """
        SELECT f.symbol, f.archetype, f.confidence
        FROM failure_intelligence f
        INNER JOIN (
            SELECT symbol, MAX(analysis_date) AS max_date
            FROM failure_intelligence
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.analysis_date = latest.max_date
        """
    )
    if not rows:
        # Try without confidence column
        rows = safe_fetchall(
            db,
            """
            SELECT f.symbol, f.failure_archetype AS archetype
            FROM failure_intelligence f
            INNER JOIN (
                SELECT symbol, MAX(analysis_date) AS max_date
                FROM failure_intelligence
                GROUP BY symbol
            ) latest ON f.symbol = latest.symbol AND f.analysis_date = latest.max_date
            """
        )
    for r in rows:
        result[r["symbol"]] = {
            "archetype": r.get("archetype") or r.get("failure_archetype") or "UNKNOWN",
            "confidence": float(r.get("confidence") or 0.5),
        }
    return result


def load_stock_dna(db):
    """Return {symbol: {archetype, energy_score, trend_strength}}."""
    result = {}
    rows = safe_fetchall(
        db, "SELECT symbol, archetype, energy_score, trend_strength FROM stock_dna"
    )
    for r in rows:
        result[r["symbol"]] = {
            "archetype": r.get("archetype") or "UNKNOWN",
            "energy_score": float(r.get("energy_score") or 0),
            "trend_strength": float(r.get("trend_strength") or 0),
        }
    return result


def load_market_regime(db):
    """Return latest regime row as dict."""
    # Try market_regime first, then regime_history
    for tbl in ("market_regime", "regime_history"):
        if table_exists(db, tbl):
            row = safe_fetchone(
                db,
                f"SELECT * FROM {tbl} ORDER BY date DESC LIMIT 1"
            )
            if row:
                return row
    return {}


def load_causal_data(db):
    """Return list of causal edges from causal_edges or causal_chains."""
    for tbl in ("causal_edges", "causal_chains"):
        if table_exists(db, tbl):
            rows = safe_fetchall(db, f"SELECT source, target, strength, lag FROM {tbl}")
            if rows:
                return rows
    return []


def load_umcg_nodes(db):
    """Return {symbol: {centrality_score, community_id}}."""
    result = {}
    rows = safe_fetchall(
        db, "SELECT symbol, centrality_score, community_id FROM umcg_nodes"
    )
    for r in rows:
        result[r["symbol"]] = {
            "centrality_score": float(r.get("centrality_score") or 0),
            "community_id": r.get("community_id"),
        }
    return result


def load_ohlcv_recent(db, symbol, days=25):
    """Return last `days` OHLCV rows for symbol, newest last."""
    return safe_fetchall(
        db,
        """
        SELECT date, close, volume
        FROM ohlcv
        WHERE symbol = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (symbol, days),
    )


def load_all_ohlcv_latest(db):
    """Return {symbol: {date, close, volume}} for each symbol's most recent row."""
    result = {}
    rows = safe_fetchall(
        db,
        """
        SELECT o.symbol, o.date, o.close, o.volume
        FROM ohlcv o
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date FROM ohlcv GROUP BY symbol
        ) latest ON o.symbol = latest.symbol AND o.date = latest.max_date
        """
    )
    for r in rows:
        result[r["symbol"]] = {
            "date": r["date"],
            "close": float(r.get("close") or 0),
            "volume": float(r.get("volume") or 0),
        }
    return result


def load_yesterday_scores(db, today_str):
    """Return {symbol: intelligence_score} for the most recent prior date."""
    rows = safe_fetchall(
        db,
        """
        SELECT symbol, intelligence_score
        FROM intelligence_scores
        WHERE date < ?
        ORDER BY date DESC
        LIMIT 1000
        """,
        (today_str,)
    )
    return {r["symbol"]: float(r["intelligence_score"] or 0) for r in rows}


def load_today_scores(db, today_str):
    """Return {symbol: row_dict} for today's intelligence_scores."""
    rows = safe_fetchall(
        db,
        "SELECT * FROM intelligence_scores WHERE date = ?",
        (today_str,)
    )
    return {r["symbol"]: r for r in rows}


# ---------------------------------------------------------------------------
# Component computation
# ---------------------------------------------------------------------------

TIER_MAP = {"DEEP": 1.0, "MID": 0.7, "SHALLOW": 0.4, "ILLIQUID": 0.1}


def compute_explosion_component(symbol, explosion_map):
    """Return 0-100 explosion component."""
    er = explosion_map.get(symbol)
    if not er:
        return 0.0
    # readiness_score is typically 0-100
    raw = float(er.get("readiness_score") or 0)
    return max(0.0, min(100.0, raw))


def compute_law_component(symbol, pattern_laws, stock_dna_map):
    """Return 0-100 based on best law precision applicable to this symbol."""
    if not pattern_laws:
        return 0.0
    # Use the maximum precision among validated laws as a proxy
    best_precision = 0.0
    for law in pattern_laws:
        p = float(law.get("precision") or 0)
        if p > best_precision:
            best_precision = p
    # Adjust by DNA archetype match quality if available
    dna = stock_dna_map.get(symbol, {})
    energy = float(dna.get("energy_score") or 0)
    # blend: 70% best law precision + 30% energy score (both expected 0-1 or 0-100)
    # Normalize to 0-100
    prec_norm = min(100.0, best_precision * 100.0) if best_precision <= 1.0 else min(100.0, best_precision)
    energy_norm = min(100.0, energy * 100.0) if energy <= 1.0 else min(100.0, energy)
    return 0.7 * prec_norm + 0.3 * energy_norm


def compute_execution_component(symbol, liquidity_map):
    """Return 0-100 based on liquidity tier."""
    liq = liquidity_map.get(symbol, {})
    tier = (liq.get("tier") or "ILLIQUID").upper()
    factor = TIER_MAP.get(tier, 0.1)
    return factor * 100.0


def compute_regime_component(symbol, stock_dna_map, regime_row):
    """Return 0-100 based on DNA trend_strength / energy_score and regime."""
    dna = stock_dna_map.get(symbol, {})
    trend = float(dna.get("trend_strength") or 0)
    energy = float(dna.get("energy_score") or 0)
    trend_norm = min(100.0, trend * 100.0) if trend <= 1.0 else min(100.0, trend)
    energy_norm = min(100.0, energy * 100.0) if energy <= 1.0 else min(100.0, energy)
    base = (trend_norm + energy_norm) / 2.0
    # Boost if current regime is aligned (regime_confidence available)
    reg_conf = float(regime_row.get("regime_confidence") or 0) if regime_row else 0
    reg_conf_norm = min(100.0, reg_conf * 100.0) if reg_conf <= 1.0 else min(100.0, reg_conf)
    if reg_conf_norm > 0:
        return 0.8 * base + 0.2 * reg_conf_norm
    return base


def compute_causal_component(symbol, causal_edges, umcg_map):
    """Return 0-100 based on causal edge count / strength."""
    # Sum of strength for edges where this symbol is source or target
    total_strength = 0.0
    edge_count = 0
    for edge in causal_edges:
        src = edge.get("source") or ""
        tgt = edge.get("target") or ""
        if src == symbol or tgt == symbol:
            strength = float(edge.get("strength") or 0)
            total_strength += strength
            edge_count += 1
    # UMCG centrality bonus
    umcg = umcg_map.get(symbol, {})
    centrality = float(umcg.get("centrality_score") or 0)
    centrality_norm = min(100.0, centrality * 100.0) if centrality <= 1.0 else min(100.0, centrality)
    # Normalize edge-based score: assume max ~20 edges at strength 1.0 = 20 points
    edge_score = min(100.0, (total_strength / max(1, edge_count)) * 100.0 if edge_count > 0 else 0.0)
    count_bonus = min(30.0, edge_count * 3.0)
    combined = edge_score * 0.5 + centrality_norm * 0.3 + count_bonus * 0.2
    return min(100.0, combined)


def determine_primary_driver(comps):
    """Return the name of the component with highest weighted contribution."""
    weights = {
        "explosion": 0.25,
        "law": 0.20,
        "execution": 0.20,
        "regime": 0.20,
        "causal": 0.15,
    }
    best = max(weights, key=lambda k: comps[k] * weights[k])
    return best.upper()


def score_data_quality(comps):
    """Return RICH/PARTIAL/SPARSE based on how many components are non-zero."""
    non_zero = sum(1 for v in comps.values() if v > 0)
    if non_zero >= 4:
        return "RICH"
    if non_zero >= 2:
        return "PARTIAL"
    return "SPARSE"


def compute_risk_level(explosion_map, threshold_counts=None):
    """CRITICAL/>30%, HIGH/>20%, ELEVATED/>10%, else NORMAL."""
    if not explosion_map:
        return "NORMAL"
    total = len(explosion_map)
    high_readiness = sum(
        1 for v in explosion_map.values()
        if float(v.get("readiness_score") or 0) > 70
    )
    pct = high_readiness / total if total > 0 else 0
    if pct > 0.30:
        return "CRITICAL"
    if pct > 0.20:
        return "HIGH"
    if pct > 0.10:
        return "ELEVATED"
    return "NORMAL"


def infer_market_state(regime_row, scores_list):
    """Infer TRENDING/VOLATILE/TRANSITIONING/SIDEWAYS from available signals."""
    label = (regime_row.get("regime_label") or "").upper() if regime_row else ""
    if "TREND" in label:
        return "TRENDING"
    if "VOLAT" in label:
        return "VOLATILE"
    if "TRANSIT" in label or "CHANGE" in label:
        return "TRANSITIONING"
    # Fall back to score distribution
    if not scores_list:
        return "SIDEWAYS"
    avg = sum(s["intelligence_score"] for s in scores_list) / len(scores_list)
    spread = max(s["intelligence_score"] for s in scores_list) - min(s["intelligence_score"] for s in scores_list)
    if avg > 65:
        return "TRENDING"
    if spread > 50:
        return "VOLATILE"
    if avg < 35:
        return "SIDEWAYS"
    return "TRANSITIONING"


def infer_dominant_force(regime_row, explosion_map, stock_dna_map):
    """Return a short descriptive string for the dominant market force."""
    label = (regime_row.get("regime_label") or "") if regime_row else ""
    if label:
        return f"Regime: {label}"
    # Find archetype with highest energy
    best_sym = None
    best_energy = -1
    for sym, dna in stock_dna_map.items():
        e = float(dna.get("energy_score") or 0)
        if e > best_energy:
            best_energy = e
            best_sym = sym
    if best_sym:
        arch = stock_dna_map[best_sym].get("archetype", "UNKNOWN")
        return f"DNA-{arch} leadership ({best_sym})"
    # Fall back to explosion
    if explosion_map:
        top = max(explosion_map, key=lambda s: explosion_map[s].get("readiness_score", 0))
        return f"Explosion pressure ({top})"
    return "Indeterminate"


def infer_regime_stability(regime_row):
    """Return STABLE/UNSTABLE/TRANSITIONING."""
    if not regime_row:
        return "STABLE"
    conf = float(regime_row.get("regime_confidence") or 0)
    conf_norm = conf if conf > 1 else conf * 100
    label = (regime_row.get("regime_label") or "").upper()
    if "TRANSIT" in label or "CHANGE" in label:
        return "TRANSITIONING"
    if conf_norm < 40:
        return "UNSTABLE"
    return "STABLE"


def rolling_avg_volume(ohlcv_rows, window=20):
    """Compute average volume from last `window` rows (excluding today)."""
    if len(ohlcv_rows) < 2:
        return 0.0
    # ohlcv_rows newest-first; skip index 0 (today), use next window rows
    prior = ohlcv_rows[1: window + 1]
    vols = [float(r.get("volume") or 0) for r in prior]
    if not vols:
        return 0.0
    return sum(vols) / len(vols)


# ---------------------------------------------------------------------------
# Command: prioritize
# ---------------------------------------------------------------------------

def cmd_prioritize(params):
    today = datetime.date.today().isoformat()
    computed_at = datetime.datetime.utcnow().isoformat()

    db = get_db()

    # Load all data sources
    symbols = load_symbols(db)
    explosion_map = load_explosion_readiness(db)
    liquidity_map = load_liquidity_profiles(db)
    pattern_laws = load_pattern_laws(db)
    stock_dna_map = load_stock_dna(db)
    regime_row = load_market_regime(db)
    causal_edges = load_causal_data(db)
    umcg_map = load_umcg_nodes(db)

    # Merge all symbol sets
    all_symbols = set(symbols)
    all_symbols.update(explosion_map.keys())
    all_symbols.update(liquidity_map.keys())
    all_symbols.update(stock_dna_map.keys())
    all_symbols = sorted(all_symbols)

    if not all_symbols:
        db.close()
        return {
            "success": True,
            "n_scored": 0,
            "top_10": [],
            "avg_score": 0.0,
            "computed_at": computed_at,
        }

    scored_rows = []
    for sym in all_symbols:
        exp_comp = compute_explosion_component(sym, explosion_map)
        law_comp = compute_law_component(sym, pattern_laws, stock_dna_map)
        exec_comp = compute_execution_component(sym, liquidity_map)
        reg_comp = compute_regime_component(sym, stock_dna_map, regime_row)
        caus_comp = compute_causal_component(sym, causal_edges, umcg_map)

        raw_score = (
            exp_comp  * 0.25 +
            law_comp  * 0.20 +
            exec_comp * 0.20 +
            reg_comp  * 0.20 +
            caus_comp * 0.15
        )
        intelligence_score = max(0.0, min(100.0, raw_score))

        comps = {
            "explosion": exp_comp,
            "law": law_comp,
            "execution": exec_comp,
            "regime": reg_comp,
            "causal": caus_comp,
        }
        primary_driver = determine_primary_driver(comps)
        data_quality = score_data_quality(comps)
        tier = (liquidity_map.get(sym, {}).get("tier") or "ILLIQUID").upper()

        scored_rows.append({
            "symbol": sym,
            "intelligence_score": round(intelligence_score, 2),
            "explosion_component": round(exp_comp, 2),
            "law_component": round(law_comp, 2),
            "execution_component": round(exec_comp, 2),
            "regime_component": round(reg_comp, 2),
            "causal_component": round(caus_comp, 2),
            "primary_driver": primary_driver,
            "data_quality": data_quality,
            "tier": tier,
        })

    # Rank and assign percentile
    scored_rows.sort(key=lambda r: r["intelligence_score"], reverse=True)
    total = len(scored_rows)
    for rank, row in enumerate(scored_rows, start=1):
        row["percentile_rank"] = round((total - rank) / total * 100, 1)

    # Upsert into DB
    upsert_sql = """
    INSERT OR REPLACE INTO intelligence_scores
        (symbol, date, intelligence_score,
         explosion_component, law_component, execution_component,
         regime_component, causal_component,
         primary_driver, percentile_rank, data_quality, computed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for row in scored_rows:
        db.execute(upsert_sql, (
            row["symbol"], today, row["intelligence_score"],
            row["explosion_component"], row["law_component"],
            row["execution_component"], row["regime_component"],
            row["causal_component"], row["primary_driver"],
            row["percentile_rank"], row["data_quality"], computed_at,
        ))
    db.commit()

    avg_score = round(sum(r["intelligence_score"] for r in scored_rows) / total, 2)
    top_10 = [
        {
            "symbol": r["symbol"],
            "intelligence_score": r["intelligence_score"],
            "primary_driver": r["primary_driver"],
            "tier": r["tier"],
        }
        for r in scored_rows[:10]
    ]

    db.close()
    return {
        "success": True,
        "n_scored": total,
        "top_10": top_10,
        "avg_score": avg_score,
        "computed_at": computed_at,
    }


# ---------------------------------------------------------------------------
# Command: top_insights
# ---------------------------------------------------------------------------

def cmd_top_insights(params):
    today = datetime.date.today().isoformat()
    db = get_db()

    # Load scored data
    today_scores = safe_fetchall(
        db,
        "SELECT * FROM intelligence_scores WHERE date = ? ORDER BY intelligence_score DESC",
        (today,)
    )
    explosion_map = load_explosion_readiness(db)
    failure_map = load_failure_intelligence(db)
    stock_dna_map = load_stock_dna(db)
    regime_row = load_market_regime(db)
    causal_edges = load_causal_data(db)

    insights = []

    # ---- Insight 1: highest intelligence_score symbol ----
    if today_scores:
        top = today_scores[0]
        sym = top["symbol"]
        score = float(top.get("intelligence_score") or 0)
        driver = top.get("primary_driver") or "MIXED"
        dna = stock_dna_map.get(sym, {})
        archetype = dna.get("archetype") or "UNKNOWN"
        er = explosion_map.get(sym, {})
        readiness = float(er.get("readiness_score") or 0)
        insight_text = (
            f"{sym} leads with intelligence score {score:.1f}/100 driven by {driver}. "
            f"DNA archetype: {archetype}. Explosion readiness: {readiness:.1f}."
        )
        insights.append({
            "rank": 1,
            "symbol": sym,
            "insight_text": insight_text,
            "signal_type": "TOP_SCORE",
            "confidence": round(min(1.0, score / 100), 2),
            "action_hint": f"Monitor {sym} for entry — dominant {driver} driver active.",
        })

    # ---- Insight 2: biggest volume anomaly or explosion signal ----
    all_symbols = [r["symbol"] for r in today_scores] if today_scores else list(explosion_map.keys())
    best_anomaly_sym = None
    best_anomaly_ratio = 0.0
    for sym in all_symbols[:50]:  # cap scan at 50 to stay fast
        ohlcv_rows = load_ohlcv_recent(db, sym, days=25)
        if not ohlcv_rows:
            continue
        today_vol = float(ohlcv_rows[0].get("volume") or 0)
        avg_vol = rolling_avg_volume(ohlcv_rows, window=20)
        if avg_vol > 0:
            ratio = today_vol / avg_vol
            if ratio > best_anomaly_ratio:
                best_anomaly_ratio = ratio
                best_anomaly_sym = sym

    if best_anomaly_sym and best_anomaly_ratio >= 1.5:
        er = explosion_map.get(best_anomaly_sym, {})
        readiness = float(er.get("readiness_score") or 0)
        signal_type = "VOLUME_EXPLOSION" if best_anomaly_ratio >= 3.0 else "VOLUME_SURGE"
        conf = min(1.0, best_anomaly_ratio / 5.0)
        insight_text = (
            f"{best_anomaly_sym} shows {signal_type}: volume is "
            f"{best_anomaly_ratio:.1f}x the 20-day average. "
            f"Explosion readiness: {readiness:.1f}."
        )
        insights.append({
            "rank": 2,
            "symbol": best_anomaly_sym,
            "insight_text": insight_text,
            "signal_type": signal_type,
            "confidence": round(conf, 2),
            "action_hint": (
                f"Investigate {best_anomaly_sym} urgently — "
                f"unusual volume may signal institutional activity."
            ),
        })
    elif explosion_map:
        # Fall back to top explosion candidate
        top_exp = max(explosion_map, key=lambda s: explosion_map[s].get("readiness_score", 0))
        rs = float(explosion_map[top_exp].get("readiness_score") or 0)
        ci = float(explosion_map[top_exp].get("compression_index") or 0)
        insights.append({
            "rank": 2,
            "symbol": top_exp,
            "insight_text": (
                f"{top_exp} has the highest explosion readiness at {rs:.1f}/100 "
                f"with compression index {ci:.2f}. Energy build-up detected."
            ),
            "signal_type": "EXPLOSION_CANDIDATE",
            "confidence": round(min(1.0, rs / 100), 2),
            "action_hint": f"Watch {top_exp} for breakout confirmation before entry.",
        })

    # ---- Insight 3: most critical failure risk or regime change signal ----
    regime_label = (regime_row.get("regime_label") or "") if regime_row else ""
    regime_conf = float(regime_row.get("regime_confidence") or 0) if regime_row else 0
    regime_conf_norm = regime_conf if regime_conf > 1 else regime_conf * 100

    # Pick highest-confidence failure as the risk signal
    best_failure_sym = None
    best_failure_conf = -1.0
    for sym, finfo in failure_map.items():
        c = float(finfo.get("confidence") or 0)
        if c > best_failure_conf:
            best_failure_conf = c
            best_failure_sym = sym

    if best_failure_sym:
        archetype = failure_map[best_failure_sym].get("archetype") or "UNKNOWN"
        insight_text = (
            f"RISK ALERT: {best_failure_sym} exhibits {archetype} failure archetype "
            f"(confidence {best_failure_conf:.2f}). "
        )
        if regime_label and regime_conf_norm < 50:
            insight_text += (
                f"Regime '{regime_label}' is weakening (confidence {regime_conf_norm:.1f}%). "
                f"Elevated transition risk."
            )
        else:
            insight_text += "Consider reducing exposure or setting tighter stops."
        insights.append({
            "rank": 3,
            "symbol": best_failure_sym,
            "insight_text": insight_text,
            "signal_type": "FAILURE_RISK",
            "confidence": round(best_failure_conf, 2),
            "action_hint": f"Avoid new long positions in {best_failure_sym} until archetype resolves.",
        })
    elif regime_label and regime_conf_norm < 40:
        insights.append({
            "rank": 3,
            "symbol": "MARKET",
            "insight_text": (
                f"Regime '{regime_label}' showing low confidence ({regime_conf_norm:.1f}%). "
                f"Potential regime transition ahead — reduce risk across the board."
            ),
            "signal_type": "REGIME_CHANGE",
            "confidence": round(1.0 - regime_conf_norm / 100, 2),
            "action_hint": "Reduce position sizes and tighten stops until regime stabilizes.",
        })
    else:
        insights.append({
            "rank": 3,
            "symbol": "MARKET",
            "insight_text": "No critical failure signals detected. Market risk appears within normal bounds.",
            "signal_type": "NO_CRITICAL_RISK",
            "confidence": 0.5,
            "action_hint": "Continue standard risk management protocols.",
        })

    # Fill gaps if fewer than 3 insights
    while len(insights) < 3:
        insights.append({
            "rank": len(insights) + 1,
            "symbol": "N/A",
            "insight_text": "Insufficient data for this insight category.",
            "signal_type": "DATA_GAP",
            "confidence": 0.0,
            "action_hint": "Run full data pipeline to populate intelligence sources.",
        })

    # Dominant force and risk level
    dominant_force = infer_dominant_force(regime_row, explosion_map, stock_dna_map)
    risk_level = compute_risk_level(explosion_map)

    db.close()
    return {
        "success": True,
        "date": today,
        "insights": insights[:3],
        "dominant_force": dominant_force,
        "risk_level": risk_level,
    }


# ---------------------------------------------------------------------------
# Command: anomaly_today
# ---------------------------------------------------------------------------

def cmd_anomaly_today(params):
    today = datetime.date.today().isoformat()
    db = get_db()

    anomalies = []

    explosion_map = load_explosion_readiness(db)
    regime_row = load_market_regime(db)

    # Load today's and yesterday's intelligence scores
    today_rows = safe_fetchall(
        db,
        "SELECT * FROM intelligence_scores WHERE date = ?",
        (today,)
    )
    today_score_map = {r["symbol"]: r for r in today_rows}

    # Yesterday: most recent date before today in intelligence_scores
    yesterday_rows = safe_fetchall(
        db,
        """
        SELECT symbol, intelligence_score, date
        FROM intelligence_scores
        WHERE date < ?
        ORDER BY date DESC
        LIMIT 5000
        """,
        (today,)
    )
    # Build yesterday map: per-symbol pick the newest prior date
    yesterday_map = {}
    for r in yesterday_rows:
        sym = r["symbol"]
        if sym not in yesterday_map:
            yesterday_map[sym] = r

    all_symbols = sorted(set(list(today_score_map.keys()) + list(yesterday_map.keys())))

    # ---- 1. Volume spikes > 2.5x ----
    for sym in all_symbols[:100]:
        ohlcv_rows = load_ohlcv_recent(db, sym, days=25)
        if not ohlcv_rows:
            continue
        today_vol = float(ohlcv_rows[0].get("volume") or 0)
        avg_vol = rolling_avg_volume(ohlcv_rows, window=20)
        if avg_vol > 0 and today_vol > 2.5 * avg_vol:
            ratio = today_vol / avg_vol
            severity = "CRITICAL" if ratio > 5 else ("HIGH" if ratio > 3.5 else ("MEDIUM" if ratio > 2.5 else "LOW"))
            anomalies.append({
                "symbol": sym,
                "anomaly_type": "VOLUME_SPIKE",
                "current_value": round(today_vol, 0),
                "baseline_value": round(avg_vol, 0),
                "severity": severity,
                "description": f"Volume {ratio:.1f}x the 20-day average — possible institutional activity.",
            })

    # ---- 2. Intelligence score jumps > 20 points ----
    for sym in today_score_map:
        curr = float(today_score_map[sym].get("intelligence_score") or 0)
        if sym in yesterday_map:
            prev = float(yesterday_map[sym].get("intelligence_score") or 0)
            delta = curr - prev
            if delta > 20:
                severity = "CRITICAL" if delta > 40 else ("HIGH" if delta > 30 else "MEDIUM")
                anomalies.append({
                    "symbol": sym,
                    "anomaly_type": "SCORE_JUMP",
                    "current_value": round(curr, 1),
                    "baseline_value": round(prev, 1),
                    "severity": severity,
                    "description": (
                        f"Intelligence score surged +{delta:.1f} pts "
                        f"({prev:.1f} → {curr:.1f}). Significant signal alignment shift."
                    ),
                })

    # ---- 3. New explosion candidates: readiness_score jumped > 15 ----
    # Use explosion_readiness table directly for delta
    prev_er_rows = safe_fetchall(
        db,
        """
        SELECT e.symbol, e.readiness_score, e.date
        FROM explosion_readiness e
        INNER JOIN (
            SELECT symbol, MAX(date) AS prev_date
            FROM explosion_readiness
            WHERE date < (
                SELECT MAX(date) FROM explosion_readiness
            )
            GROUP BY symbol
        ) p ON e.symbol = p.symbol AND e.date = p.prev_date
        """
    )
    prev_er_map = {r["symbol"]: float(r.get("readiness_score") or 0) for r in prev_er_rows}
    for sym, er in explosion_map.items():
        curr_rs = float(er.get("readiness_score") or 0)
        prev_rs = prev_er_map.get(sym, curr_rs)  # default to same if no history
        delta = curr_rs - prev_rs
        if delta > 15:
            severity = "HIGH" if delta > 25 else "MEDIUM"
            anomalies.append({
                "symbol": sym,
                "anomaly_type": "EXPLOSION_CANDIDATE_NEW",
                "current_value": round(curr_rs, 1),
                "baseline_value": round(prev_rs, 1),
                "severity": severity,
                "description": (
                    f"Explosion readiness jumped +{delta:.1f} pts "
                    f"({prev_rs:.1f} → {curr_rs:.1f}). New breakout candidate emerging."
                ),
            })

    # ---- 4. Regime confidence drops > 20% ----
    if regime_row:
        curr_conf = float(regime_row.get("regime_confidence") or 0)
        curr_conf_norm = curr_conf if curr_conf > 1 else curr_conf * 100
        # Get prior regime row
        for tbl in ("market_regime", "regime_history"):
            if table_exists(db, tbl):
                prior_regime = safe_fetchone(
                    db,
                    f"""
                    SELECT regime_confidence FROM {tbl}
                    WHERE date < ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (today,)
                )
                if prior_regime:
                    prev_conf = float(prior_regime.get("regime_confidence") or 0)
                    prev_conf_norm = prev_conf if prev_conf > 1 else prev_conf * 100
                    drop = prev_conf_norm - curr_conf_norm
                    if drop > 20:
                        severity = "CRITICAL" if drop > 40 else ("HIGH" if drop > 30 else "MEDIUM")
                        anomalies.append({
                            "symbol": "MARKET",
                            "anomaly_type": "REGIME_CONFIDENCE_DROP",
                            "current_value": round(curr_conf_norm, 1),
                            "baseline_value": round(prev_conf_norm, 1),
                            "severity": severity,
                            "description": (
                                f"Regime confidence fell {drop:.1f}% "
                                f"({prev_conf_norm:.1f}% → {curr_conf_norm:.1f}%). "
                                f"Market structure may be destabilizing."
                            ),
                        })
                    break

    # Sort by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    anomalies.sort(key=lambda a: severity_order.get(a["severity"], 4))

    most_severe = anomalies[0]["anomaly_type"] if anomalies else "NONE"

    db.close()
    return {
        "success": True,
        "date": today,
        "anomalies": anomalies,
        "n_anomalies": len(anomalies),
        "most_severe": most_severe,
    }


# ---------------------------------------------------------------------------
# Command: score_symbol
# ---------------------------------------------------------------------------

def cmd_score_symbol(params):
    symbol = params.get("symbol") or params.get("sym") or ""
    if not symbol:
        return {"success": False, "error": "symbol parameter required"}

    today = datetime.date.today().isoformat()
    db = get_db()

    # Load all data for this symbol
    explosion_map = load_explosion_readiness(db)
    liquidity_map = load_liquidity_profiles(db)
    pattern_laws = load_pattern_laws(db)
    failure_map = load_failure_intelligence(db)
    stock_dna_map = load_stock_dna(db)
    regime_row = load_market_regime(db)
    causal_edges = load_causal_data(db)
    umcg_map = load_umcg_nodes(db)

    # Compute components
    exp_comp  = compute_explosion_component(symbol, explosion_map)
    law_comp  = compute_law_component(symbol, pattern_laws, stock_dna_map)
    exec_comp = compute_execution_component(symbol, liquidity_map)
    reg_comp  = compute_regime_component(symbol, stock_dna_map, regime_row)
    caus_comp = compute_causal_component(symbol, causal_edges, umcg_map)

    raw_score = (
        exp_comp  * 0.25 +
        law_comp  * 0.20 +
        exec_comp * 0.20 +
        reg_comp  * 0.20 +
        caus_comp * 0.15
    )
    intelligence_score = max(0.0, min(100.0, raw_score))

    comps = {
        "explosion": round(exp_comp, 2),
        "law": round(law_comp, 2),
        "execution": round(exec_comp, 2),
        "regime": round(reg_comp, 2),
        "causal": round(caus_comp, 2),
    }

    primary_driver = determine_primary_driver(comps)
    data_quality = score_data_quality(comps)

    # Secondary driver: second highest weighted contribution
    weights = {"explosion": 0.25, "law": 0.20, "execution": 0.20, "regime": 0.20, "causal": 0.15}
    sorted_comps = sorted(comps, key=lambda k: comps[k] * weights[k], reverse=True)
    secondary_driver = sorted_comps[1].upper() if len(sorted_comps) > 1 else "N/A"

    # Percentile rank vs today's scores
    all_scores = safe_fetchall(
        db,
        "SELECT intelligence_score FROM intelligence_scores WHERE date = ?",
        (today,)
    )
    scored_vals = sorted([float(r["intelligence_score"] or 0) for r in all_scores])
    if scored_vals:
        rank = sum(1 for v in scored_vals if v <= intelligence_score)
        percentile_rank = round(rank / len(scored_vals) * 100, 1)
    else:
        percentile_rank = 50.0

    # Action hint
    er = explosion_map.get(symbol, {})
    readiness = float(er.get("readiness_score") or 0)
    failure = failure_map.get(symbol, {})
    fail_arch = failure.get("archetype") or "NONE"
    fail_conf = float(failure.get("confidence") or 0)

    if readiness > 75 and intelligence_score > 70:
        action_hint = f"Strong buy candidate — explosion readiness {readiness:.1f} + high intelligence score."
    elif fail_conf > 0.7:
        action_hint = f"CAUTION: {fail_arch} failure archetype active. Avoid until resolved."
    elif intelligence_score > 60:
        action_hint = "Watchlist — accumulate on pullbacks."
    elif intelligence_score > 40:
        action_hint = "Monitor. No immediate action required."
    else:
        action_hint = "Low priority. Insufficient signal strength."

    db.close()
    return {
        "success": True,
        "symbol": symbol,
        "intelligence_score": round(intelligence_score, 2),
        "components": comps,
        "percentile_rank": percentile_rank,
        "primary_driver": primary_driver,
        "secondary_driver": secondary_driver,
        "action_hint": action_hint,
        "data_quality": data_quality,
    }


# ---------------------------------------------------------------------------
# Command: daily_brief
# ---------------------------------------------------------------------------

def cmd_daily_brief(params):
    today = datetime.date.today().isoformat()
    computed_at = datetime.datetime.utcnow().isoformat()
    db = get_db()

    # Pull today's scores
    today_scores = safe_fetchall(
        db,
        "SELECT * FROM intelligence_scores WHERE date = ? ORDER BY intelligence_score DESC",
        (today,)
    )

    explosion_map = load_explosion_readiness(db)
    failure_map = load_failure_intelligence(db)
    stock_dna_map = load_stock_dna(db)
    regime_row = load_market_regime(db)
    causal_edges = load_causal_data(db)

    # Market state
    market_state = infer_market_state(regime_row, today_scores)
    dominant_force = infer_dominant_force(regime_row, explosion_map, stock_dna_map)
    risk_level = compute_risk_level(explosion_map)
    regime_stability = infer_regime_stability(regime_row)

    # Top 3 insights (reuse logic inline)
    insights_result = cmd_top_insights(params)
    top_3_insights = insights_result.get("insights", [])

    # Top 5 symbols
    top_5_symbols = []
    for row in today_scores[:5]:
        sym = row["symbol"]
        score = float(row.get("intelligence_score") or 0)
        driver = row.get("primary_driver") or "MIXED"
        er = explosion_map.get(sym, {})
        readiness = float(er.get("readiness_score") or 0)
        dna = stock_dna_map.get(sym, {})
        archetype = dna.get("archetype") or "UNKNOWN"
        reason = f"{driver} driver"
        if readiness > 60:
            reason += f"; explosion readiness {readiness:.0f}"
        if archetype != "UNKNOWN":
            reason += f"; {archetype} DNA"
        top_5_symbols.append({
            "symbol": sym,
            "score": round(score, 1),
            "reason": reason,
        })

    # Anomaly count
    anomaly_result = cmd_anomaly_today(params)
    anomaly_count = anomaly_result.get("n_anomalies", 0)
    key_anomaly = anomaly_result.get("most_severe", "NONE")
    if anomaly_count > 0 and anomaly_result.get("anomalies"):
        key_anomaly = anomaly_result["anomalies"][0].get("description", key_anomaly)

    # Actionable today
    actionable_today = (
        len(top_5_symbols) > 0
        and risk_level in ("NORMAL", "ELEVATED")
        and market_state in ("TRENDING", "VOLATILE")
    )

    # Brief summary
    top_sym = top_5_symbols[0]["symbol"] if top_5_symbols else "N/A"
    brief_summary = (
        f"{market_state} market with {dominant_force.lower()}; "
        f"{top_sym} leads at {top_5_symbols[0]['score'] if top_5_symbols else 0:.0f}/100 — "
        f"risk {risk_level}, {anomaly_count} anomalies detected today."
    )

    # Persist to DB
    db.execute(
        """
        INSERT OR REPLACE INTO daily_intelligence_brief
            (date, market_state, dominant_force, risk_level,
             top_3_insights, top_5_symbols, anomaly_count, key_anomaly,
             regime_stability, actionable_today, brief_summary, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            today, market_state, dominant_force, risk_level,
            json.dumps(top_3_insights), json.dumps(top_5_symbols),
            anomaly_count, key_anomaly,
            regime_stability, int(actionable_today), brief_summary, computed_at,
        ),
    )
    db.commit()
    db.close()

    return {
        "success": True,
        "date": today,
        "market_state": market_state,
        "dominant_force": dominant_force,
        "risk_level": risk_level,
        "top_3_insights": top_3_insights,
        "top_5_symbols": top_5_symbols,
        "anomaly_count": anomaly_count,
        "key_anomaly": key_anomaly,
        "regime_stability": regime_stability,
        "actionable_today": actionable_today,
        "brief_summary": brief_summary,
    }


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def cmd_build_full(params):
    prioritize_result = cmd_prioritize(params)
    insights_result   = cmd_top_insights(params)
    anomaly_result    = cmd_anomaly_today(params)

    return {
        "success": True,
        "prioritize": prioritize_result,
        "insights":   insights_result,
        "anomalies":  anomaly_result,
        "status":     "complete",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "prioritize":    cmd_prioritize,
    "top_insights":  cmd_top_insights,
    "anomaly_today": cmd_anomaly_today,
    "score_symbol":  cmd_score_symbol,
    "daily_brief":   cmd_daily_brief,
    "build_full":    cmd_build_full,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        result = {
            "success": False,
            "error": "Usage: intelligence_prioritizer.py <command> [json_params]",
            "commands": list(COMMANDS.keys()),
        }
        print(json.dumps(result))
        sys.exit(1)

    command = sys.argv[1].lower().strip()
    raw_params = sys.argv[2] if len(sys.argv) > 2 else "{}"

    try:
        params = json.loads(raw_params)
    except (json.JSONDecodeError, ValueError) as e:
        result = {
            "success": False,
            "error": f"Invalid JSON params: {e}",
            "raw": raw_params,
        }
        print(json.dumps(result))
        sys.exit(1)

    handler = COMMANDS.get(command)
    if handler is None:
        result = {
            "success": False,
            "error": f"Unknown command: {command}",
            "available": list(COMMANDS.keys()),
        }
        print(json.dumps(result))
        sys.exit(1)

    try:
        output = handler(params)
    except Exception as exc:
        import traceback
        result = {
            "success": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(result))
        sys.exit(1)

    print(json.dumps(output))
