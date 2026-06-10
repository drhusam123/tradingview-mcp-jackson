#!/usr/bin/env python3
"""
semantic_language.py — Phase 46: EGX Autonomous Quant System
Semantic Market Language Engine

Compresses hundreds of metrics into a single coherent narrative sentence using
a structured vocabulary of market archetypes.  Creates a "language layer" that
makes the system's intelligence human-readable and auditable.

Invocation:  python semantic_language.py <command> '<json_params>'
Output:      last stdout line = valid JSON

Commands:
  classify_archetype  — match current system state to closest market archetype
  generate_narrative  — full cognitive narrative (Arabic + English)
  narrative_history   — last N market narratives + archetype evolution
  vocabulary_map      — full vocabulary + which archetype current market matches
  build_full          — classify + generate + persist to market_narratives
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, date
from collections import defaultdict, Counter

# ─── DB PATH ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

TODAY = date.today().isoformat()
NOW   = datetime.utcnow().isoformat(timespec='seconds') + 'Z'

# ─── MARKET ARCHETYPES (EGX-specific) ─────────────────────────────────────────
MARKET_ARCHETYPES = {
    'late_fragile_expansion': {
        'arabic': 'توسع متأخر هش',
        'conditions': {
            'regime': 'BULL',
            'mii_range': (30, 60),
            'uncertainty': (0.4, 0.7),
            'coherence': (30, 60),
        },
        'risk_level': 'ELEVATED',
        'recommended_action': 'selective_entry',
    },
    'early_accumulation': {
        'arabic': 'تراكم مبكر',
        'conditions': {
            'regime': ['SIDEWAYS', 'BEAR'],
            'mii_range': (20, 45),
            'uncertainty': (0.3, 0.6),
            'coherence': (40, 70),
        },
        'risk_level': 'MODERATE',
        'recommended_action': 'cautious_entry',
    },
    'distribution_phase': {
        'arabic': 'مرحلة توزيع',
        'conditions': {
            'regime': 'BULL',
            'mii_range': (50, 75),
            'trend': 'DEGRADING',
            'uncertainty': (0.3, 0.5),
        },
        'risk_level': 'HIGH',
        'recommended_action': 'reduce_exposure',
    },
    'crisis_deleveraging': {
        'arabic': 'تخفيض رافعة في أزمة',
        'conditions': {
            'regime': 'CRISIS',
            'uncertainty': (0.6, 1.0),
            'coherence': (0, 40),
        },
        'risk_level': 'EXTREME',
        'recommended_action': 'halt',
    },
    'liquidity_vacuum': {
        'arabic': 'فراغ سيولة',
        'conditions': {
            'ood_level': ['HIGH_OOD', 'EXTREME_OOD'],
            'fragmentation': 'HIGH',
        },
        'risk_level': 'VERY_HIGH',
        'recommended_action': 'minimal_exposure',
    },
    'breadth_deterioration': {
        'arabic': 'تدهور اتساع السوق',
        'conditions': {
            'n_veto': (5, 999),
            'arbitration_bias': 'BEARISH',
        },
        'risk_level': 'HIGH',
        'recommended_action': 'avoid',
    },
    'contagion_spiral': {
        'arabic': 'حلزون عدوى',
        'conditions': {
            'contagion_score': (0.7, 1.0),
            'fragmentation': ['HIGH', 'SEVERE'],
        },
        'risk_level': 'EXTREME',
        'recommended_action': 'halt',
    },
    'regime_inflection': {
        'arabic': 'نقطة انعطاف النظام',
        'conditions': {
            'ewi': (65, 100),
            'transition_prob': (0.5, 1.0),
        },
        'risk_level': 'VERY_HIGH',
        'recommended_action': 'standby',
    },
    'healthy_bull': {
        'arabic': 'سوق صاعد صحي',
        'conditions': {
            'regime': 'BULL',
            'mii_range': (60, 100),
            'uncertainty': (0.0, 0.4),
            'coherence': (60, 100),
        },
        'risk_level': 'LOW',
        'recommended_action': 'full_engagement',
    },
    'sideways_compression': {
        'arabic': 'ضغط جانبي',
        'conditions': {
            'regime': 'SIDEWAYS',
            'mii_range': (35, 60),
            'uncertainty': (0.3, 0.6),
        },
        'risk_level': 'MODERATE',
        'recommended_action': 'wait_breakout',
    },
}

# ─── NARRATIVE TEMPLATES ──────────────────────────────────────────────────────
NARRATIVE_TEMPLATE_AR = (
    "السوق في [{archetype_ar}] — {force_description} — "
    "أبرز خطر: [{top_risk}] — الموقف: [{action}]"
)
NARRATIVE_TEMPLATE_EN = (
    "Market is in [{archetype}] — {force_description} — "
    "Key risk: [{top_risk}] — Stance: [{action}]"
)

# Force description vocab
FORCE_DESCRIPTIONS_AR = {
    'MOMENTUM':         'زخم اتجاهي قوي',
    'LIQUIDITY':        'ديناميكيات السيولة تهيمن',
    'REGIME_PULL':      'شد النظام السائد',
    'SENTIMENT_WAVE':   'موجة المشاعر الجماعية',
    'CATALYST_FLOW':    'تدفق محفزات السوق',
    'LAW_DENSITY':      'كثافة قوانين الأنماط النشطة',
    'RISK_PRESSURE':    'ضغط المخاطر المنهجية',
    'ANOMALY_FIELD':    'حقل الشذوذات السوقية',
    'CONTAGION_WAVE':   'موجة عدوى القطاعات',
    'STRUCTURAL_DRIFT': 'انجراف هيكلي طويل الأمد',
    'UNKNOWN':          'قوى سوقية غير محددة',
}
FORCE_DESCRIPTIONS_EN = {
    'MOMENTUM':         'strong directional momentum dominates',
    'LIQUIDITY':        'liquidity dynamics dominate',
    'REGIME_PULL':      'prevailing regime pull is primary driver',
    'SENTIMENT_WAVE':   'collective sentiment wave is driving',
    'CATALYST_FLOW':    'catalyst flow is driving price action',
    'LAW_DENSITY':      'high active pattern-law density',
    'RISK_PRESSURE':    'systemic risk pressure elevated',
    'ANOMALY_FIELD':    'market anomaly field is active',
    'CONTAGION_WAVE':   'sector contagion wave spreading',
    'STRUCTURAL_DRIFT': 'long-term structural drift underway',
    'UNKNOWN':          'unidentified market forces active',
}

# Risk label vocab
RISK_LABELS_AR = {
    'EXTREME_OOD':  'انزياح سوقي شديد',
    'HIGH_OOD':     'انزياح سوقي مرتفع',
    'MEDIUM_OOD':   'انزياح سوقي متوسط',
    'contagion':    'خطر العدوى القطاعية',
    'regime_shift': 'خطر تحول النظام',
    'liquidity':    'خطر نقص السيولة',
    'veto':         'إشارات فيتو نشطة',
    'uncertainty':  'عدم يقين مرتفع',
    'default':      'مخاطر عامة غير محددة',
}
RISK_LABELS_EN = {
    'EXTREME_OOD':  'extreme out-of-distribution shift',
    'HIGH_OOD':     'high out-of-distribution shift',
    'MEDIUM_OOD':   'moderate out-of-distribution drift',
    'contagion':    'sector contagion risk',
    'regime_shift': 'regime transition risk',
    'liquidity':    'liquidity shortfall risk',
    'veto':         'active veto signals',
    'uncertainty':  'elevated uncertainty',
    'default':      'general unspecified risk',
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def safe_query(conn, sql, params=()):
    """Execute a query and return all rows, or [] on any error."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def safe_scalar(conn, sql, params=(), default=None):
    """Return first column of first row, or default."""
    rows = safe_query(conn, sql, params)
    if rows:
        return rows[0][0]
    return default


def table_exists(conn, name):
    """Check if a table exists in the DB."""
    result = safe_scalar(
        conn,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
        default=0,
    )
    return bool(result)


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def in_range(value, lo, hi):
    """Return True if lo <= value <= hi (inclusive)."""
    if value is None:
        return False
    return lo <= value <= hi


def matches_condition(key, cond_value, state):
    """
    Returns a float 0-1 indicating how well state[key] satisfies cond_value.
    Supports: str (exact), list (any-of), tuple (numeric range).
    """
    actual = state.get(key)
    if actual is None:
        return 0.0

    # numeric range condition: (lo, hi)
    if isinstance(cond_value, tuple) and len(cond_value) == 2:
        lo, hi = cond_value
        if not isinstance(actual, (int, float)):
            return 0.0
        if in_range(actual, lo, hi):
            # partial score: how centred in the range?
            mid = (lo + hi) / 2.0
            half = (hi - lo) / 2.0 if hi != lo else 1.0
            dist = abs(actual - mid) / half
            return max(0.0, 1.0 - dist * 0.3)  # min 0.7 if inside range
        return 0.0

    # list condition: any of these values
    if isinstance(cond_value, list):
        for v in cond_value:
            if str(actual).upper() == str(v).upper():
                return 1.0
        return 0.0

    # scalar condition: exact match (case-insensitive for strings)
    return 1.0 if str(actual).upper() == str(cond_value).upper() else 0.0


# ─── STATE COLLECTION ─────────────────────────────────────────────────────────

def collect_state(conn):
    """
    Pull current system state from all available tables.
    Returns a dict with normalised keys used by archetype matching.
    """
    state = {}

    # ── Regime ────────────────────────────────────────────────────────────────
    if table_exists(conn, 'market_regimes'):
        rows = safe_query(
            conn,
            "SELECT regime_type, confidence FROM market_regimes "
            "ORDER BY id DESC LIMIT 1",
        )
        if rows:
            state['regime'] = str(rows[0]['regime_type']).upper()
            state['regime_confidence'] = float(rows[0]['confidence'] or 0)
    elif table_exists(conn, 'regime_history'):
        rows = safe_query(
            conn,
            "SELECT regime_type, confidence FROM regime_history "
            "ORDER BY id DESC LIMIT 1",
        )
        if rows:
            state['regime'] = str(rows[0]['regime_type']).upper()
            state['regime_confidence'] = float(rows[0]['confidence'] or 0)

    # ── MII ───────────────────────────────────────────────────────────────────
    if table_exists(conn, 'market_intelligence_index'):
        mii_val = safe_scalar(
            conn,
            "SELECT mii FROM market_intelligence_index ORDER BY id DESC LIMIT 1",
        )
        if mii_val is not None:
            state['mii'] = float(mii_val)
            state['mii_range'] = float(mii_val)   # same field, archetype uses mii_range

    # ── Uncertainty ───────────────────────────────────────────────────────────
    if table_exists(conn, 'uncertainty_reports'):
        rows = safe_query(
            conn,
            "SELECT total_uncertainty, ood_level FROM uncertainty_reports "
            "ORDER BY id DESC LIMIT 1",
        )
        if rows:
            state['uncertainty'] = float(rows[0]['total_uncertainty'] or 0)
            state['ood_level']   = str(rows[0]['ood_level'] or 'NORMAL').upper()

    # ── Coherence ─────────────────────────────────────────────────────────────
    if table_exists(conn, 'bus_state'):
        rows = safe_query(
            conn,
            "SELECT coherence_score, narrative_direction FROM bus_state "
            "ORDER BY id DESC LIMIT 1",
        )
        if rows:
            state['coherence'] = float(rows[0]['coherence_score'] or 0)
            state['arbitration_bias'] = str(rows[0]['narrative_direction'] or 'NEUTRAL').upper()

    # ── EWI + Transition Probability ─────────────────────────────────────────
    if table_exists(conn, 'regime_transition_signals'):
        rows = safe_query(
            conn,
            "SELECT ewi, transition_probability FROM regime_transition_signals "
            "ORDER BY id DESC LIMIT 1",
        )
        if rows:
            state['ewi']             = float(rows[0]['ewi'] or 0)
            state['transition_prob'] = float(rows[0]['transition_probability'] or 0)

    # ── N Veto ────────────────────────────────────────────────────────────────
    if table_exists(conn, 'anti_law_daily_scan'):
        n_veto = safe_scalar(
            conn,
            "SELECT COUNT(*) FROM anti_law_daily_scan WHERE scan_date=?",
            (TODAY,),
            default=0,
        )
        state['n_veto'] = int(n_veto or 0)
    elif table_exists(conn, 'anti_laws'):
        n_veto = safe_scalar(
            conn,
            "SELECT COUNT(*) FROM anti_laws WHERE is_active=1",
            default=0,
        )
        state['n_veto'] = int(n_veto or 0)

    # ── Contagion Score ───────────────────────────────────────────────────────
    if table_exists(conn, 'contagion_maps'):
        contagion = safe_scalar(
            conn,
            "SELECT AVG(contagion_score) FROM contagion_maps",
        )
        if contagion is not None:
            state['contagion_score'] = float(contagion)
    elif table_exists(conn, 'sector_contagion'):
        contagion = safe_scalar(
            conn,
            "SELECT AVG(contagion_level) FROM sector_contagion",
        )
        if contagion is not None:
            state['contagion_score'] = float(contagion)

    # ── Fragmentation ─────────────────────────────────────────────────────────
    if table_exists(conn, 'engine_health_scores'):
        rows = safe_query(
            conn,
            "SELECT score FROM engine_health_scores "
            "WHERE engine_name='graph_fragmentation' ORDER BY id DESC LIMIT 1",
        )
        if rows:
            frag_score = float(rows[0]['score'] or 0)
            if frag_score >= 0.8:
                state['fragmentation'] = 'SEVERE'
            elif frag_score >= 0.6:
                state['fragmentation'] = 'HIGH'
            elif frag_score >= 0.4:
                state['fragmentation'] = 'MODERATE'
            else:
                state['fragmentation'] = 'LOW'

    # ── Trend Quality ─────────────────────────────────────────────────────────
    if table_exists(conn, 'synthesis_reports'):
        rows = safe_query(
            conn,
            "SELECT trend_quality FROM synthesis_reports ORDER BY id DESC LIMIT 1",
        )
        if rows and rows[0]['trend_quality']:
            state['trend'] = str(rows[0]['trend_quality']).upper()

    return state


# ─── ARCHETYPE MATCHING ───────────────────────────────────────────────────────

def score_archetype(archetype_name, archetype_def, state):
    """
    Compute 0-1 match score for one archetype against the current state.
    Averaged across all conditions defined for that archetype.
    """
    conditions = archetype_def.get('conditions', {})
    if not conditions:
        return 0.0

    scores = []
    for cond_key, cond_value in conditions.items():
        s = matches_condition(cond_key, cond_value, state)
        scores.append(s)

    return sum(scores) / len(scores)


def rank_archetypes(state):
    """
    Score every archetype and return list sorted descending by match_score.
    Returns: [(name, score, archetype_def), ...]
    """
    ranked = []
    for name, defn in MARKET_ARCHETYPES.items():
        score = score_archetype(name, defn, state)
        ranked.append((name, score, defn))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


# ─── TOP RISK DERIVATION ──────────────────────────────────────────────────────

def derive_top_risk(state):
    """
    Determine the most significant active risk from state, returning
    (risk_key, risk_ar, risk_en) tuple.
    """
    ood = state.get('ood_level', 'NORMAL')
    if ood in ('EXTREME_OOD',):
        return 'EXTREME_OOD', RISK_LABELS_AR['EXTREME_OOD'], RISK_LABELS_EN['EXTREME_OOD']
    if ood in ('HIGH_OOD',):
        return 'HIGH_OOD', RISK_LABELS_AR['HIGH_OOD'], RISK_LABELS_EN['HIGH_OOD']

    contagion = state.get('contagion_score', 0.0)
    if contagion >= 0.7:
        return 'contagion', RISK_LABELS_AR['contagion'], RISK_LABELS_EN['contagion']

    tp = state.get('transition_prob', 0.0)
    if tp >= 0.5:
        return 'regime_shift', RISK_LABELS_AR['regime_shift'], RISK_LABELS_EN['regime_shift']

    n_veto = state.get('n_veto', 0)
    if n_veto >= 5:
        return 'veto', RISK_LABELS_AR['veto'], RISK_LABELS_EN['veto']

    unc = state.get('uncertainty', 0.0)
    if unc >= 0.6:
        return 'uncertainty', RISK_LABELS_AR['uncertainty'], RISK_LABELS_EN['uncertainty']

    if ood in ('MEDIUM_OOD',):
        return 'MEDIUM_OOD', RISK_LABELS_AR['MEDIUM_OOD'], RISK_LABELS_EN['MEDIUM_OOD']

    frag = state.get('fragmentation', 'LOW')
    if frag in ('HIGH', 'SEVERE'):
        return 'liquidity', RISK_LABELS_AR['liquidity'], RISK_LABELS_EN['liquidity']

    return 'default', RISK_LABELS_AR['default'], RISK_LABELS_EN['default']


# ─── DOMINANT FORCE LOOKUP ────────────────────────────────────────────────────

def get_dominant_force(conn):
    """
    Try to read dominant force from compression engine table.
    Returns (force_key, force_ar, force_en) or fallback 'UNKNOWN'.
    """
    if table_exists(conn, 'dominant_market_forces'):
        rows = safe_query(
            conn,
            "SELECT force_type, force_name FROM dominant_market_forces "
            "ORDER BY rank ASC, id DESC LIMIT 1",
        )
        if rows:
            ft = str(rows[0]['force_type'] or rows[0]['force_name'] or 'UNKNOWN').upper()
            ar = FORCE_DESCRIPTIONS_AR.get(ft, FORCE_DESCRIPTIONS_AR['UNKNOWN'])
            en = FORCE_DESCRIPTIONS_EN.get(ft, FORCE_DESCRIPTIONS_EN['UNKNOWN'])
            return ft, ar, en

    # Fallback: infer from bus_state
    if table_exists(conn, 'bus_state'):
        rows = safe_query(
            conn,
            "SELECT dominant_force FROM bus_state ORDER BY id DESC LIMIT 1",
        )
        if rows and rows[0]['dominant_force']:
            ft = str(rows[0]['dominant_force']).upper()
            ar = FORCE_DESCRIPTIONS_AR.get(ft, FORCE_DESCRIPTIONS_AR['UNKNOWN'])
            en = FORCE_DESCRIPTIONS_EN.get(ft, FORCE_DESCRIPTIONS_EN['UNKNOWN'])
            return ft, ar, en

    return 'UNKNOWN', FORCE_DESCRIPTIONS_AR['UNKNOWN'], FORCE_DESCRIPTIONS_EN['UNKNOWN']


# ─── COMMANDS ─────────────────────────────────────────────────────────────────

def cmd_classify_archetype(params):
    """
    Match current system state to the closest market archetype.
    """
    conn = get_db()
    try:
        state = collect_state(conn)
        ranked = rank_archetypes(state)

        primary_name, primary_score, primary_def = ranked[0]
        secondary_name = ranked[1][0] if len(ranked) > 1 else 'none'

        # Confidence: penalise if primary score is low or very close to second
        confidence = clamp(primary_score, 0.0, 1.0)
        if len(ranked) > 1:
            gap = primary_score - ranked[1][1]
            if gap < 0.1:
                confidence *= 0.8  # less confident when close contest

        state_snapshot = {
            'regime':          state.get('regime', 'UNKNOWN'),
            'mii':             state.get('mii', None),
            'uncertainty':     state.get('uncertainty', None),
            'coherence':       state.get('coherence', None),
            'ewi':             state.get('ewi', None),
            'ood_level':       state.get('ood_level', 'NORMAL'),
            'n_veto':          state.get('n_veto', 0),
            'contagion_score': state.get('contagion_score', None),
            'fragmentation':   state.get('fragmentation', 'LOW'),
        }

        return {
            'primary_archetype':    primary_name,
            'primary_archetype_ar': primary_def['arabic'],
            'match_score':          round(primary_score, 4),
            'secondary_archetype':  secondary_name,
            'risk_level':           primary_def['risk_level'],
            'recommended_action':   primary_def['recommended_action'],
            'state_snapshot':       state_snapshot,
            'confidence':           round(confidence, 4),
        }
    finally:
        conn.close()


def cmd_generate_narrative(params):
    """
    Generate full cognitive narrative sentence (Arabic + English) plus executive summary.
    """
    conn = get_db()
    try:
        state = collect_state(conn)
        ranked = rank_archetypes(state)

        primary_name, primary_score, primary_def = ranked[0]
        archetype_ar = primary_def['arabic']
        risk_level   = primary_def['risk_level']
        action       = primary_def['recommended_action']

        # Confidence
        confidence = clamp(primary_score, 0.0, 1.0)
        if len(ranked) > 1:
            gap = primary_score - ranked[1][1]
            if gap < 0.1:
                confidence *= 0.8
        confidence_pct = int(round(confidence * 100))

        # Dominant force
        force_key, force_ar, force_en = get_dominant_force(conn)

        # Top risk
        risk_key, risk_ar, risk_en = derive_top_risk(state)

        # Build narratives
        arabic_narrative = NARRATIVE_TEMPLATE_AR.format(
            archetype_ar=archetype_ar,
            force_description=force_ar,
            top_risk=risk_ar,
            action=action,
        )
        english_narrative = NARRATIVE_TEMPLATE_EN.format(
            archetype=primary_name,
            force_description=force_en,
            top_risk=risk_en,
            action=action,
        )

        # Executive sentences
        executive_arabic  = (
            f"المحرك الرئيسي: {force_ar} | "
            f"الموقف: {action} | "
            f"الثقة: {confidence_pct}%"
        )
        executive_english = (
            f"Primary driver: {force_en} | "
            f"Stance: {action} | "
            f"Confidence: {confidence_pct}%"
        )

        return {
            'arabic_narrative':  arabic_narrative,
            'english_narrative': english_narrative,
            'executive_arabic':  executive_arabic,
            'executive_english': executive_english,
            'archetype':         primary_name,
            'archetype_ar':      archetype_ar,
            'risk_level':        risk_level,
            'action':            action,
            'confidence':        round(confidence, 4),
            'dominant_force':    force_key,
            'top_risk':          risk_key,
            'generated_at':      NOW,
        }
    finally:
        conn.close()


def cmd_narrative_history(params):
    """
    Read last N market narratives from market_narratives table.
    Show archetype evolution over time.
    """
    n = int(params.get('n', 20))
    conn = get_db()
    try:
        _ensure_narratives_table(conn)

        rows = safe_query(
            conn,
            "SELECT archetype, archetype_ar, risk_level, recommended_action, "
            "match_score, generated_at "
            "FROM market_narratives ORDER BY id DESC LIMIT ?",
            (n,),
        )

        narratives = []
        for r in rows:
            narratives.append({
                'date':       r['generated_at'],
                'archetype':  r['archetype'],
                'archetype_ar': r['archetype_ar'],
                'risk_level': r['risk_level'],
                'action':     r['recommended_action'],
                'match_score': float(r['match_score'] or 0),
            })

        # Archetype frequency
        freq = Counter(n_['archetype'] for n_ in narratives)
        most_frequent = freq.most_common(1)[0][0] if freq else 'none'

        # Count transitions (consecutive archetype changes)
        transitions = 0
        prev = None
        for n_ in reversed(narratives):  # oldest→newest
            if prev is not None and n_['archetype'] != prev:
                transitions += 1
            prev = n_['archetype']

        return {
            'n_narratives':        len(narratives),
            'narratives':          narratives,
            'archetype_frequency': dict(freq),
            'most_frequent':       most_frequent,
            'transitions':         transitions,
        }
    finally:
        conn.close()


def cmd_vocabulary_map(params):
    """
    Return the full vocabulary of archetypes with their metadata.
    Also show which archetype the current market most closely resembles.
    """
    conn = get_db()
    try:
        state = collect_state(conn)
        ranked = rank_archetypes(state)
        current_match = ranked[0][0] if ranked else 'unknown'

        archetypes_list = []
        for name, defn in MARKET_ARCHETYPES.items():
            archetypes_list.append({
                'name':         name,
                'arabic':       defn['arabic'],
                'risk_level':   defn['risk_level'],
                'action':       defn['recommended_action'],
                'n_conditions': len(defn.get('conditions', {})),
                'conditions':   list(defn.get('conditions', {}).keys()),
            })

        # Sort by risk severity for readability
        risk_order = {'LOW': 0, 'MODERATE': 1, 'ELEVATED': 2,
                      'HIGH': 3, 'VERY_HIGH': 4, 'EXTREME': 5}
        archetypes_list.sort(
            key=lambda x: risk_order.get(x['risk_level'], 3), reverse=True
        )

        # Attach current match scores
        score_map = {name: score for name, score, _ in ranked}
        for a in archetypes_list:
            a['current_match_score'] = round(score_map.get(a['name'], 0.0), 4)

        return {
            'archetypes':     archetypes_list,
            'current_match':  current_match,
            'vocabulary_size': len(MARKET_ARCHETYPES),
        }
    finally:
        conn.close()


def cmd_build_full(params):
    """
    Run classify_archetype + generate_narrative. Save to DB.
    """
    conn = get_db()
    try:
        _ensure_narratives_table(conn)

        # Collect state once
        state = collect_state(conn)
        ranked = rank_archetypes(state)

        primary_name, primary_score, primary_def = ranked[0]
        archetype_ar = primary_def['arabic']
        risk_level   = primary_def['risk_level']
        action       = primary_def['recommended_action']

        confidence = clamp(primary_score, 0.0, 1.0)
        if len(ranked) > 1:
            gap = primary_score - ranked[1][1]
            if gap < 0.1:
                confidence *= 0.8
        confidence_pct = int(round(confidence * 100))

        force_key, force_ar, force_en = get_dominant_force(conn)
        risk_key, risk_ar, risk_en    = derive_top_risk(state)

        arabic_narrative = NARRATIVE_TEMPLATE_AR.format(
            archetype_ar=archetype_ar,
            force_description=force_ar,
            top_risk=risk_ar,
            action=action,
        )
        english_narrative = NARRATIVE_TEMPLATE_EN.format(
            archetype=primary_name,
            force_description=force_en,
            top_risk=risk_en,
            action=action,
        )
        executive_arabic  = (
            f"المحرك الرئيسي: {force_ar} | "
            f"الموقف: {action} | "
            f"الثقة: {confidence_pct}%"
        )
        executive_english = (
            f"Primary driver: {force_en} | "
            f"Stance: {action} | "
            f"Confidence: {confidence_pct}%"
        )

        conn.execute(
            """
            INSERT INTO market_narratives
              (archetype, archetype_ar, risk_level, recommended_action,
               arabic_narrative, english_narrative, executive_arabic,
               match_score, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                primary_name, archetype_ar, risk_level, action,
                arabic_narrative, english_narrative, executive_arabic,
                round(primary_score, 4), NOW,
            ),
        )
        conn.commit()

        return {
            'status':            'built',
            'archetype':         primary_name,
            'archetype_ar':      archetype_ar,
            'risk_level':        risk_level,
            'action':            action,
            'arabic_narrative':  arabic_narrative,
            'english_narrative': english_narrative,
            'executive_arabic':  executive_arabic,
            'executive_english': executive_english,
            'match_score':       round(primary_score, 4),
            'confidence':        round(confidence, 4),
            'generated_at':      NOW,
        }
    finally:
        conn.close()


# ─── TABLE INIT ───────────────────────────────────────────────────────────────

def _ensure_narratives_table(conn):
    """Create market_narratives table if it does not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_narratives (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            archetype           TEXT,
            archetype_ar        TEXT,
            risk_level          TEXT,
            recommended_action  TEXT,
            arabic_narrative    TEXT,
            english_narrative   TEXT,
            executive_arabic    TEXT,
            match_score         REAL,
            generated_at        TEXT
        )
        """
    )
    conn.commit()


# ─── DISPATCH ─────────────────────────────────────────────────────────────────

COMMANDS = {
    'classify_archetype': cmd_classify_archetype,
    'generate_narrative': cmd_generate_narrative,
    'narrative_history':  cmd_narrative_history,
    'vocabulary_map':     cmd_vocabulary_map,
    'build_full':         cmd_build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'Usage: python semantic_language.py <command> \'<json_params>\'',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1].strip()
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({
            'error': f'Unknown command: {cmd}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({
            'error':     str(e),
            'command':   cmd,
            'traceback': traceback.format_exc(),
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()
