"""
multi_horizon_engine.py — Phase 47: Multi-Horizon Intelligence Engine
EGX Autonomous Quant System

Creates 5 distinct cognitive layers (INTRADAY / SWING / WEEKLY / MONTHLY / CRISIS),
each with its own laws, uncertainty estimates, and arbitration logic.
Detects inter-horizon conflicts and resolves them via precedence + confidence rules.
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone
from collections import defaultdict

# ---------------------------------------------------------------------------
# DB CONFIG
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# HORIZON REGISTRY
# ---------------------------------------------------------------------------
HORIZONS = {
    'INTRADAY': {
        'bars': 1,
        'law_filter_days': 1,
        'max_hold_days': 1,
        'arabic': 'يومي',
        'uncertainty_multiplier': 1.5,   # intraday is noisier
        'law_min_precision': 0.25,        # lower bar OK for intraday
        'description': 'إشارات جلسة واحدة',
    },
    'SWING': {
        'bars': 5,
        'law_filter_days': 7,
        'max_hold_days': 7,
        'arabic': 'سوينج',
        'uncertainty_multiplier': 1.0,
        'law_min_precision': 0.22,
        'description': 'حركات 2-7 أيام',
    },
    'WEEKLY': {
        'bars': 20,
        'law_filter_days': 30,
        'max_hold_days': 30,
        'arabic': 'أسبوعي',
        'uncertainty_multiplier': 0.7,   # weekly is smoother
        'law_min_precision': 0.20,
        'description': 'تحركات أسبوعية وشهرية',
    },
    'MONTHLY': {
        'bars': 60,
        'law_filter_days': 90,
        'max_hold_days': 90,
        'arabic': 'شهري',
        'uncertainty_multiplier': 0.5,
        'law_min_precision': 0.18,
        'description': 'اتجاهات ربع سنوية',
    },
    'CRISIS': {
        'bars': 3,
        'law_filter_days': 5,
        'max_hold_days': 2,
        'arabic': 'أزمة',
        'uncertainty_multiplier': 2.0,   # crisis = highest uncertainty
        'law_min_precision': 0.30,        # higher bar needed in crisis
        'description': 'وضع الأزمة — خروج سريع',
    },
}

# Horizon precedence for conflict resolution (higher index = higher authority)
HORIZON_PRECEDENCE = ['INTRADAY', 'SWING', 'WEEKLY', 'MONTHLY', 'CRISIS']
# Note: CRISIS sits at the top regardless of index logic — handled explicitly

# Law types that are directionally BULLISH
BULLISH_LAW_TYPES = {
    'EPISODE_CONTINUATION',
    'INV_low_float_escape',
    'INV_news_surge_filter',
    'ANOMALY_PRICE_GAP_UP',
}

# Law types that are directionally BEARISH
BEARISH_LAW_TYPES = {
    'INV_volume_spike_avoidance',
    'EPISODE_REVERSAL',
    'ANOMALY_VOLUME_CLUSTER',
}


# ---------------------------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------------------------

def fetch_latest_regime(conn):
    """Return the most recent regime string, e.g. 'BULL', 'BEAR', 'CHOPPY'."""
    try:
        row = conn.execute(
            "SELECT regime FROM regime_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row['regime'] if row else 'UNKNOWN'
    except Exception:
        return 'UNKNOWN'


def fetch_latest_uncertainty(conn):
    """Return the latest total_uncertainty float (0–1)."""
    try:
        row = conn.execute(
            "SELECT total_uncertainty FROM uncertainty_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return float(row['total_uncertainty']) if row else 0.5
    except Exception:
        return 0.5


def fetch_pattern_laws(conn):
    """
    Return all pattern_laws rows as list of dicts.
    Columns: law_name, law_type, precision, regime_filter, source
    """
    try:
        rows = conn.execute(
            "SELECT law_name, law_type, precision, regime_filter, source FROM pattern_laws"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def classify_law_direction(law_type: str) -> str:
    """Return BULLISH, BEARISH, or NEUTRAL for a law_type string."""
    if law_type in BULLISH_LAW_TYPES:
        return 'BULLISH'
    if law_type in BEARISH_LAW_TYPES:
        return 'BEARISH'
    # Partial match heuristic
    lt_upper = law_type.upper()
    if any(kw in lt_upper for kw in ('BULL', 'UP', 'GROWTH', 'SURGE', 'ESCAPE', 'BREAKOUT')):
        return 'BULLISH'
    if any(kw in lt_upper for kw in ('BEAR', 'DOWN', 'REVERSAL', 'SPIKE_AVOID', 'AVOID', 'CLUSTER')):
        return 'BEARISH'
    return 'NEUTRAL'


def regime_to_direction(regime: str) -> str:
    """Map DB regime values to directional bias."""
    mapping = {
        'BULL': 'BULLISH',
        'RECOVERING': 'BULLISH',
        'BEAR': 'BEARISH',
        'CHOPPY': 'NEUTRAL',
        'SIDEWAYS': 'NEUTRAL',
        'UNKNOWN': 'NEUTRAL',
        'CRISIS': 'BEARISH',
    }
    return mapping.get(regime.upper(), 'NEUTRAL')


def safe_clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, val))


def now_iso() -> str:
    """Return current UTC ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CORE: analyze_horizon
# ---------------------------------------------------------------------------

def analyze_horizon(horizon_key: str) -> dict:
    """
    Analyze ONE time horizon in depth.
    Returns rich dict with direction, uncertainty, arbitration, confidence.
    """
    if horizon_key not in HORIZONS:
        return {
            'error': f"Unknown horizon '{horizon_key}'. Valid: {list(HORIZONS.keys())}",
            'horizon': horizon_key,
        }

    cfg = HORIZONS[horizon_key]
    conn = get_db()

    try:
        # 1. Active laws for this horizon
        all_laws = fetch_pattern_laws(conn)
        min_prec = cfg['law_min_precision']
        active_laws = [l for l in all_laws if l.get('precision', 0) >= min_prec]

        n_laws = len(active_laws)
        n_bullish = sum(1 for l in active_laws if classify_law_direction(l['law_type']) == 'BULLISH')
        n_bearish = sum(1 for l in active_laws if classify_law_direction(l['law_type']) == 'BEARISH')
        n_neutral = n_laws - n_bullish - n_bearish

        # 2. Direction score from laws
        direction_score_laws = (n_bullish - n_bearish) / max(n_laws, 1)

        # 3. Regime bias
        regime = fetch_latest_regime(conn)
        regime_direction = regime_to_direction(regime)
        regime_bias = 0.3 if regime_direction == 'BULLISH' else (-0.3 if regime_direction == 'BEARISH' else 0.0)

        # 4. Combined direction score (laws 70%, regime 30%)
        direction_score = (0.7 * direction_score_laws) + (0.3 * regime_bias)
        direction_score = safe_clamp(direction_score, -1.0, 1.0)

        # Direction label
        if direction_score > 0.15:
            direction = 'BULLISH'
        elif direction_score < -0.15:
            direction = 'BEARISH'
        else:
            direction = 'NEUTRAL'

        # 5. Horizon uncertainty
        base_uncertainty = fetch_latest_uncertainty(conn)
        horizon_uncertainty = safe_clamp(base_uncertainty * cfg['uncertainty_multiplier'])

        # 6. Arbitration
        if direction_score > 0.3 and horizon_uncertainty < 0.6:
            arbitration = 'ENGAGE'
        elif direction_score < -0.3 or horizon_uncertainty > 0.8:
            arbitration = 'AVOID'
        else:
            arbitration = 'WAIT'

        # 7. Confidence: (1 - uncertainty) × |direction_score| × law_coverage_factor
        law_coverage = safe_clamp(n_laws / 10.0)
        confidence = (1.0 - horizon_uncertainty) * abs(direction_score) * law_coverage
        confidence = safe_clamp(round(confidence, 4))

        return {
            'horizon': horizon_key,
            'arabic_name': cfg['arabic'],
            'description': cfg['description'],
            'n_active_laws': n_laws,
            'n_bullish_laws': n_bullish,
            'n_bearish_laws': n_bearish,
            'n_neutral_laws': n_neutral,
            'direction': direction,
            'direction_score': round(direction_score, 4),
            'regime_used': regime,
            'horizon_uncertainty': round(horizon_uncertainty, 4),
            'base_uncertainty': round(base_uncertainty, 4),
            'arbitration': arbitration,
            'confidence': confidence,
            'law_min_precision_used': min_prec,
            'bars': cfg['bars'],
            'max_hold_days': cfg['max_hold_days'],
        }

    except Exception as exc:
        return {
            'horizon': horizon_key,
            'error': str(exc),
            'n_active_laws': 0,
            'direction': 'NEUTRAL',
            'direction_score': 0.0,
            'horizon_uncertainty': 1.0,
            'arbitration': 'AVOID',
            'confidence': 0.0,
            'description': cfg.get('description', ''),
            'arabic_name': cfg.get('arabic', ''),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CORE: multi_view
# ---------------------------------------------------------------------------

def multi_view() -> dict:
    """
    Run analyze_horizon for ALL 5 horizons and synthesize a dashboard view.
    Detects inter-horizon conflicts and identifies the dominant horizon.
    """
    results = {}
    for hkey in HORIZONS:
        results[hkey] = analyze_horizon(hkey)

    # Dominant horizon: highest confidence × |direction_score|
    def horizon_weight(h_data):
        return h_data.get('confidence', 0.0) * abs(h_data.get('direction_score', 0.0))

    dominant_key = max(results.keys(), key=lambda k: horizon_weight(results[k]))

    # Inter-horizon conflicts: ENGAGE vs AVOID pairs
    conflicts = []
    horizon_keys = list(HORIZONS.keys())
    for i in range(len(horizon_keys)):
        for j in range(i + 1, len(horizon_keys)):
            ha = horizon_keys[i]
            hb = horizon_keys[j]
            a_arb = results[ha].get('arbitration', 'WAIT')
            b_arb = results[hb].get('arbitration', 'WAIT')
            if (a_arb == 'ENGAGE' and b_arb == 'AVOID') or (a_arb == 'AVOID' and b_arb == 'ENGAGE'):
                conflicts.append({
                    'horizon_a': ha,
                    'horizon_b': hb,
                    'a_says': a_arb,
                    'b_says': b_arb,
                })

    # Overall alignment: fraction of non-conflicting pairs
    total_pairs = len(horizon_keys) * (len(horizon_keys) - 1) / 2
    overall_alignment = round(1.0 - (len(conflicts) / max(total_pairs, 1)), 4)

    return {
        'horizons': results,
        'dominant_horizon': dominant_key,
        'inter_horizon_conflicts': conflicts,
        'n_conflicts': len(conflicts),
        'overall_alignment': overall_alignment,
    }


# ---------------------------------------------------------------------------
# CORE: horizon_conflict
# ---------------------------------------------------------------------------

def horizon_conflict() -> dict:
    """
    Identify, classify, and resolve inter-horizon conflicts.
    Returns structured conflict analysis with resolution reasoning.
    """
    mv = multi_view()
    horizons_data = mv['horizons']

    # Precedence order (higher index = higher authority in normal conditions)
    # CRISIS overrides all when active
    precedence_order = ['INTRADAY', 'SWING', 'WEEKLY', 'MONTHLY']

    detailed_conflicts = []
    horizon_keys = list(HORIZONS.keys())

    for i in range(len(horizon_keys)):
        for j in range(i + 1, len(horizon_keys)):
            ha = horizon_keys[i]
            hb = horizon_keys[j]
            da = horizons_data.get(ha, {})
            db = horizons_data.get(hb, {})

            a_arb = da.get('arbitration', 'WAIT')
            b_arb = db.get('arbitration', 'WAIT')
            a_dir = da.get('direction', 'NEUTRAL')
            b_dir = db.get('direction', 'NEUTRAL')
            a_conf = da.get('confidence', 0.0)
            b_conf = db.get('confidence', 0.0)
            a_unc = da.get('horizon_uncertainty', 1.0)
            b_unc = db.get('horizon_uncertainty', 1.0)

            conflict_type = None
            resolution = None
            reasoning = ''

            # Check DIRECTION_OPPOSITE
            if (a_dir == 'BULLISH' and b_dir == 'BEARISH') or (a_dir == 'BEARISH' and b_dir == 'BULLISH'):
                conflict_type = 'DIRECTION_OPPOSITE'
            # Check CONFIDENCE_MISMATCH: same direction but one ENGAGE the other AVOID
            elif (a_arb == 'ENGAGE' and b_arb == 'AVOID') or (a_arb == 'AVOID' and b_arb == 'ENGAGE'):
                conflict_type = 'CONFIDENCE_MISMATCH'

            if conflict_type is None:
                continue  # No conflict between this pair

            # Resolve: follow higher precedence (longer horizon) unless CRISIS
            if ha == 'CRISIS' or hb == 'CRISIS':
                # CRISIS always dominates
                winner = 'CRISIS'
                loser = hb if ha == 'CRISIS' else ha
                resolution = f"FOLLOW_{winner}"
                reasoning = (
                    f"وضع الأزمة يتغلب دائماً — {winner} يلغي {loser}. "
                    "CRISIS horizon overrides all others unconditionally."
                )
            else:
                # Higher index in precedence_order = more authoritative
                a_prec = precedence_order.index(ha) if ha in precedence_order else -1
                b_prec = precedence_order.index(hb) if hb in precedence_order else -1

                if a_prec > b_prec:
                    resolution = f"FOLLOW_{ha}"
                    reasoning = (
                        f"الأفق الأطول ({ha}, precision≥{HORIZONS[ha]['law_min_precision']}) "
                        f"يتقدم على {hb}. "
                        f"Longer horizon ({ha}) takes precedence; conf={a_conf:.3f} vs {b_conf:.3f}."
                    )
                elif b_prec > a_prec:
                    resolution = f"FOLLOW_{hb}"
                    reasoning = (
                        f"الأفق الأطول ({hb}, precision≥{HORIZONS[hb]['law_min_precision']}) "
                        f"يتقدم على {ha}. "
                        f"Longer horizon ({hb}) takes precedence; conf={b_conf:.3f} vs {a_conf:.3f}."
                    )
                else:
                    # Equal precedence — follow higher confidence
                    if a_conf > b_conf:
                        resolution = f"FOLLOW_{ha}"
                        reasoning = (
                            f"ثقة {ha} أعلى ({a_conf:.3f} vs {b_conf:.3f}). "
                            f"Following higher-confidence horizon {ha}."
                        )
                    elif b_conf > a_conf:
                        resolution = f"FOLLOW_{hb}"
                        reasoning = (
                            f"ثقة {hb} أعلى ({b_conf:.3f} vs {a_conf:.3f}). "
                            f"Following higher-confidence horizon {hb}."
                        )
                    else:
                        resolution = "WAIT_FOR_ALIGNMENT"
                        reasoning = "Confidence equal — wait for alignment."

            detailed_conflicts.append({
                'horizon_a': ha,
                'horizon_b': hb,
                'conflict_type': conflict_type,
                'a_decision': a_arb,
                'b_decision': b_arb,
                'a_direction': a_dir,
                'b_direction': b_dir,
                'a_confidence': round(a_conf, 4),
                'b_confidence': round(b_conf, 4),
                'resolution': resolution,
                'reasoning': reasoning,
            })

    n_conflicts = len(detailed_conflicts)

    # Overall recommended action
    monthly_d = horizons_data.get('MONTHLY', {})
    swing_d = horizons_data.get('SWING', {})
    crisis_d = horizons_data.get('CRISIS', {})

    if crisis_d.get('horizon_uncertainty', 0) > 0.8 or crisis_d.get('arbitration') == 'AVOID':
        recommended_action = 'WAIT_FOR_ALIGNMENT'
    elif (
        monthly_d.get('arbitration') == 'ENGAGE'
        and swing_d.get('arbitration') == 'ENGAGE'
    ):
        recommended_action = 'FOLLOW_MONTHLY'
    elif (
        monthly_d.get('arbitration') == 'AVOID'
        and swing_d.get('arbitration') == 'ENGAGE'
    ):
        # Classic conflict: monthly cautious but swing bullish
        recommended_action = 'FOLLOW_MONTHLY'  # longer horizon wins
    elif n_conflicts == 0:
        # No conflicts — follow best confidence
        best_arb = monthly_d.get('arbitration', 'WAIT')
        if best_arb == 'ENGAGE':
            recommended_action = 'FOLLOW_MONTHLY'
        elif best_arb == 'AVOID':
            recommended_action = 'WAIT_FOR_ALIGNMENT'
        else:
            recommended_action = 'FOLLOW_SWING'
    else:
        recommended_action = 'WAIT_FOR_ALIGNMENT'

    # Alignment score: fraction of pairs without direction conflict
    total_pairs = len(horizon_keys) * (len(horizon_keys) - 1) / 2
    alignment_score = round(1.0 - (n_conflicts / max(total_pairs, 1)), 4)

    return {
        'conflicts': detailed_conflicts,
        'n_conflicts': n_conflicts,
        'recommended_action': recommended_action,
        'alignment_score': alignment_score,
    }


# ---------------------------------------------------------------------------
# CORE: dominant_signal
# ---------------------------------------------------------------------------

def dominant_signal() -> dict:
    """
    Determine which horizon should lead the decision today.
    Applies CRISIS override, agreement-check, and conflict-wait logic.
    """
    mv = multi_view()
    horizons_data = mv['horizons']

    conn = get_db()
    try:
        regime = fetch_latest_regime(conn)
        base_uncertainty = fetch_latest_uncertainty(conn)
    finally:
        conn.close()

    crisis_d = horizons_data.get('CRISIS', {})
    crisis_unc = crisis_d.get('horizon_uncertainty', 0.0)
    crisis_arb = crisis_d.get('arbitration', 'WAIT')

    # 1. CRISIS override
    if crisis_unc > 0.8 or regime.upper() == 'CRISIS' or crisis_arb == 'AVOID':
        return {
            'dominant_horizon': 'CRISIS',
            'dominant_direction': crisis_d.get('direction', 'BEARISH'),
            'dominant_confidence': crisis_d.get('confidence', 0.0),
            'reason': (
                f"وضع أزمة نشط — عدم اليقين = {crisis_unc:.2f} | النظام = {regime}. "
                "CRISIS mode is active — defensive posture required."
            ),
            'secondary_horizon': 'MONTHLY',
            'action_recommendation': 'AVOID',
            'time_to_act': 'DO_NOT_ACT_NOW',
        }

    # 2. Score each non-CRISIS horizon
    scored = {}
    for hkey, hdata in horizons_data.items():
        if hkey == 'CRISIS':
            continue
        w = hdata.get('confidence', 0.0) * abs(hdata.get('direction_score', 0.0))
        scored[hkey] = w

    sorted_horizons = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    top3_keys = [k for k, _ in sorted_horizons[:3]]
    top3_arbitrations = [horizons_data[k].get('arbitration', 'WAIT') for k in top3_keys]
    top3_directions = [horizons_data[k].get('direction', 'NEUTRAL') for k in top3_keys]

    dominant_key = top3_keys[0] if top3_keys else 'MONTHLY'
    secondary_key = top3_keys[1] if len(top3_keys) > 1 else 'SWING'
    dominant_data = horizons_data.get(dominant_key, {})

    # 3. Check if top 3 agree
    all_agree_arb = len(set(top3_arbitrations)) == 1
    all_agree_dir = len(set(top3_directions)) == 1

    # 4. Check major MONTHLY vs SWING conflict (both high confidence)
    monthly_data = horizons_data.get('MONTHLY', {})
    swing_data = horizons_data.get('SWING', {})
    monthly_conf = monthly_data.get('confidence', 0.0)
    swing_conf = swing_data.get('confidence', 0.0)
    monthly_arb = monthly_data.get('arbitration', 'WAIT')
    swing_arb = swing_data.get('arbitration', 'WAIT')

    major_conflict = (
        monthly_conf > 0.4
        and swing_conf > 0.4
        and ((monthly_arb == 'ENGAGE' and swing_arb == 'AVOID')
             or (monthly_arb == 'AVOID' and swing_arb == 'ENGAGE'))
    )

    if major_conflict:
        return {
            'dominant_horizon': 'MONTHLY',
            'dominant_direction': monthly_data.get('direction', 'NEUTRAL'),
            'dominant_confidence': monthly_data.get('confidence', 0.0),
            'reason': (
                f"تعارض جوهري بين MONTHLY ({monthly_arb}) وSWING ({swing_arb}) بثقة عالية. "
                f"Major conflict: MONTHLY={monthly_arb} (conf={monthly_conf:.3f}) vs "
                f"SWING={swing_arb} (conf={swing_conf:.3f}). Waiting for alignment."
            ),
            'secondary_horizon': 'SWING',
            'action_recommendation': 'WAIT',
            'time_to_act': 'WAIT_FOR_HORIZON_CONVERGENCE',
        }

    # 5. Top 3 agree
    if all_agree_arb and all_agree_dir:
        arb_decision = top3_arbitrations[0]
        action = arb_decision  # ENGAGE / WAIT / AVOID
        reason = (
            f"أفضل 3 آفاق ({', '.join(top3_keys)}) تتفق على {arb_decision} — {top3_directions[0]}. "
            f"Top 3 horizons in agreement: {arb_decision}."
        )
        time_to_act = 'ACT_NOW' if action == 'ENGAGE' else ('AVOID_NOW' if action == 'AVOID' else 'WAIT')
    else:
        # Partial agreement — follow dominant
        action = dominant_data.get('arbitration', 'WAIT')
        reason = (
            f"لا يوجد توافق كامل — الأفق السائد {dominant_key} يقود ({action}). "
            f"No full agreement among top horizons. Following {dominant_key} with "
            f"confidence={dominant_data.get('confidence', 0.0):.3f}."
        )
        time_to_act = 'ACT_WITH_CAUTION' if action == 'ENGAGE' else 'WAIT'

    return {
        'dominant_horizon': dominant_key,
        'dominant_direction': dominant_data.get('direction', 'NEUTRAL'),
        'dominant_confidence': round(dominant_data.get('confidence', 0.0), 4),
        'reason': reason,
        'secondary_horizon': secondary_key,
        'action_recommendation': action,
        'time_to_act': time_to_act,
        'top3_horizons': top3_keys,
        'top3_arbitrations': top3_arbitrations,
        'top3_directions': top3_directions,
        'regime': regime,
        'base_uncertainty': round(base_uncertainty, 4),
    }


# ---------------------------------------------------------------------------
# CORE: build_full
# ---------------------------------------------------------------------------

def build_full() -> dict:
    """
    Run multi_view + horizon_conflict + dominant_signal.
    Persist results to DB. Return summary.
    """
    generated_at = now_iso()

    # Run all three analyses
    mv = multi_view()
    hc = horizon_conflict()
    ds = dominant_signal()

    conn = get_db()
    try:
        # Create tables if they don't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS horizon_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                horizon TEXT,
                direction TEXT,
                direction_score REAL,
                horizon_uncertainty REAL,
                arbitration TEXT,
                confidence REAL,
                n_active_laws INTEGER,
                generated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS horizon_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dominant_horizon TEXT,
                n_conflicts INTEGER,
                overall_alignment REAL,
                action_recommendation TEXT,
                generated_at TEXT
            )
        """)
        conn.execute("PRAGMA journal_mode=WAL")

        # Insert one row per horizon into horizon_signals
        for hkey, hdata in mv['horizons'].items():
            conn.execute(
                """
                INSERT INTO horizon_signals
                    (horizon, direction, direction_score, horizon_uncertainty,
                     arbitration, confidence, n_active_laws, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hkey,
                    hdata.get('direction', 'NEUTRAL'),
                    hdata.get('direction_score', 0.0),
                    hdata.get('horizon_uncertainty', 1.0),
                    hdata.get('arbitration', 'WAIT'),
                    hdata.get('confidence', 0.0),
                    hdata.get('n_active_laws', 0),
                    generated_at,
                )
            )

        # Insert one horizon_state row
        conn.execute(
            """
            INSERT INTO horizon_state
                (dominant_horizon, n_conflicts, overall_alignment,
                 action_recommendation, generated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mv['dominant_horizon'],
                hc['n_conflicts'],
                mv['overall_alignment'],
                ds['action_recommendation'],
                generated_at,
            )
        )

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {
            'status': 'error',
            'error': str(exc),
            'dominant_horizon': mv.get('dominant_horizon', 'UNKNOWN'),
            'n_conflicts': hc.get('n_conflicts', 0),
            'action_recommendation': ds.get('action_recommendation', 'WAIT'),
            'overall_alignment': mv.get('overall_alignment', 0.0),
        }
    finally:
        conn.close()

    return {
        'status': 'built',
        'dominant_horizon': mv['dominant_horizon'],
        'n_conflicts': hc['n_conflicts'],
        'action_recommendation': ds['action_recommendation'],
        'overall_alignment': mv['overall_alignment'],
        'generated_at': generated_at,
    }


# ---------------------------------------------------------------------------
# COMMAND DISPATCH
# ---------------------------------------------------------------------------

COMMANDS = {
    'analyze_horizon',
    'multi_view',
    'horizon_conflict',
    'dominant_signal',
    'build_full',
}


def dispatch(cmd: str, params: dict) -> dict:
    """Route a command string to the appropriate function."""
    if cmd == 'analyze_horizon':
        horizon = params.get('horizon', 'SWING').upper()
        return analyze_horizon(horizon)

    elif cmd == 'multi_view':
        return multi_view()

    elif cmd == 'horizon_conflict':
        return horizon_conflict()

    elif cmd == 'dominant_signal':
        return dominant_signal()

    elif cmd == 'build_full':
        return build_full()

    else:
        return {
            'error': f"Unknown command '{cmd}'",
            'valid_commands': sorted(COMMANDS),
        }


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 3:
        result = {
            'error': 'Usage: python multi_horizon_engine.py <command> <params_json>',
            'valid_commands': sorted(COMMANDS),
            'example': (
                'python multi_horizon_engine.py analyze_horizon \'{"horizon": "SWING"}\''
            ),
        }
        print(json.dumps(result))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    try:
        result = dispatch(cmd, params)
    except Exception as exc:
        result = {
            'error': str(exc),
            'command': cmd,
            'params': params,
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
