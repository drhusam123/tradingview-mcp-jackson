"""
cognitive_compression.py — Phase 38: EGX Autonomous Quant System
Cognitive Compression Engine

Synthesizes all system intelligence into:
  - 5 dominant market forces
  - 3 critical risks
  - 2 top opportunities
  - Market Intelligence Index (MII) 0-100
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, date
from collections import defaultdict

# ─── DB PATH ────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ─── FORCE TAXONOMY ─────────────────────────────────────────────────────────
FORCE_TYPES = [
    'MOMENTUM',          # directional price momentum
    'LIQUIDITY',         # market depth and participation
    'REGIME_PULL',       # current regime strength
    'SENTIMENT_WAVE',    # collective sentiment direction
    'CATALYST_FLOW',     # upcoming/recent catalyst impact
    'LAW_DENSITY',       # density of active pattern laws
    'RISK_PRESSURE',     # systemic risk level
    'ANOMALY_FIELD',     # active market anomalies
    'CONTAGION_WAVE',    # sector contagion dynamics
    'STRUCTURAL_DRIFT',  # long-term structural change
]

FORCE_ENGINE_MAP = {
    'MOMENTUM':         ['regime_history', 'market_episodes'],
    'LIQUIDITY':        ['symbol_liquidity_profile', 'stock_universe'],
    'REGIME_PULL':      ['regime_history', 'regime_transition_signals'],
    'SENTIMENT_WAVE':   ['synthesis_reports', 'daily_intelligence_brief'],
    'CATALYST_FLOW':    ['synthesis_reports', 'market_episodes'],
    'LAW_DENSITY':      ['structural_laws', 'law_grades', 'universal_laws_p16'],
    'RISK_PRESSURE':    ['regime_transition_signals', 'anti_laws'],
    'ANOMALY_FIELD':    ['market_episodes', 'synthesis_reports'],
    'CONTAGION_WAVE':   ['sector_contagion', 'sector_dna'],
    'STRUCTURAL_DRIFT': ['market_episodes', 'law_stability_curves'],
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def safe_query(conn, sql, params=()):
    """Execute query, return list of dicts. Graceful on missing tables."""
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def today_str():
    return date.today().isoformat()


def weighted_avg(values, weights):
    """Compute weighted average. Returns 0.0 if empty."""
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_w


def table_exists(conn, table_name):
    rows = safe_query(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return len(rows) > 0


# ─── DATA READERS ────────────────────────────────────────────────────────────

def read_regime(conn):
    """Read latest regime history row."""
    rows = safe_query(conn, "SELECT * FROM regime_history ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {}


def read_regime_transition(conn):
    """Read latest regime transition signals."""
    rows = safe_query(conn, "SELECT * FROM regime_transition_signals ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {}


def read_law_grades(conn):
    """Return all law grades rows."""
    return safe_query(conn, "SELECT * FROM law_grades")


def read_structural_laws(conn):
    """Return structural_laws rows."""
    return safe_query(conn, "SELECT * FROM structural_laws")


def read_universal_laws(conn):
    """Return universal_laws_p16 rows."""
    return safe_query(conn, "SELECT * FROM universal_laws_p16")


def read_anti_laws(conn):
    """Return anti_laws rows."""
    return safe_query(conn, "SELECT * FROM anti_laws")


def read_arbitration(conn):
    """Return today's arbitration decisions with ENTER decision."""
    today = today_str()
    rows = safe_query(conn, "SELECT * FROM arbitration_decisions WHERE date=? ORDER BY score DESC", (today,))
    if not rows:
        rows = safe_query(conn, "SELECT * FROM arbitration_decisions ORDER BY date DESC, score DESC LIMIT 50")
    return rows


def read_intelligence_scores(conn):
    """Return top intelligence scores."""
    today = today_str()
    rows = safe_query(conn, "SELECT * FROM intelligence_scores WHERE date=? ORDER BY intelligence_score DESC LIMIT 20", (today,))
    if not rows:
        rows = safe_query(conn, "SELECT * FROM intelligence_scores ORDER BY date DESC, intelligence_score DESC LIMIT 20")
    return rows


def read_sector_contagion(conn):
    """Return sector contagion data."""
    return safe_query(conn, "SELECT * FROM sector_contagion ORDER BY co_rate_pct DESC LIMIT 20")


def read_market_episodes(conn):
    """Return recent market episodes."""
    return safe_query(conn, "SELECT * FROM market_episodes ORDER BY end_date DESC LIMIT 10")


def read_synthesis_reports(conn):
    """Return latest synthesis report."""
    rows = safe_query(conn, "SELECT * FROM synthesis_reports ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {}


def read_law_stability(conn):
    """Return law stability curves."""
    return safe_query(conn, "SELECT * FROM law_stability_curves ORDER BY rowid DESC LIMIT 50")


def read_liquidity(conn):
    """Return liquidity profiles."""
    return safe_query(conn, "SELECT * FROM symbol_liquidity_profile LIMIT 50")


def read_stock_universe(conn):
    """Return stock universe with tier info."""
    return safe_query(conn, "SELECT * FROM stock_universe LIMIT 100")


# ─── FORCE CALCULATORS ───────────────────────────────────────────────────────

def calc_momentum(regime, transition, episodes):
    """MOMENTUM force from regime and episodes."""
    direction = 0
    magnitude = 0.3  # baseline
    evidence_parts = []

    if regime:
        r = regime.get('regime', 'NEUTRAL')
        ret = regime.get('market_return_20d', 0.0) or 0.0
        vol = regime.get('market_vol_20d', 0.1) or 0.1
        breadth = regime.get('breadth_pct', 0.5) or 0.5

        if r == 'BULL':
            direction = 1
            magnitude = clamp(0.4 + abs(ret) * 2 + breadth * 0.3)
            evidence_parts.append(f"Bull regime: return={ret:.2%}, breadth={breadth:.0%}")
        elif r == 'BEAR':
            direction = -1
            magnitude = clamp(0.4 + abs(ret) * 2 + (1 - breadth) * 0.3)
            evidence_parts.append(f"Bear regime: return={ret:.2%}, breadth={breadth:.0%}")
        else:
            direction = 1 if ret > 0 else (-1 if ret < 0 else 0)
            magnitude = clamp(0.2 + abs(ret) * 1.5)
            evidence_parts.append(f"Neutral/sideways regime: return={ret:.2%}")

    if episodes:
        ep = episodes[0]
        ts = ep.get('trend_strength', 0.5) or 0.5
        magnitude = clamp((magnitude + abs(ts)) / 2)
        evidence_parts.append(f"Episode trend_strength={ts:.2f}")

    return {
        'force_type': 'MOMENTUM',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No regime data',
        'contributing_engines': FORCE_ENGINE_MAP['MOMENTUM'],
    }


def calc_liquidity(liquidity_rows, universe_rows, arb_rows):
    """LIQUIDITY force from liquidity profiles and stock universe."""
    direction = 0
    magnitude = 0.3
    evidence_parts = []

    if universe_rows:
        tiers = [r.get('tier', '') for r in universe_rows if r.get('tier')]
        deep_mid = sum(1 for t in tiers if t in ('DEEP', 'MID'))
        total = len(tiers)
        if total > 0:
            liquid_ratio = deep_mid / total
            magnitude = clamp(liquid_ratio * 0.8 + 0.1)
            direction = 1 if liquid_ratio > 0.5 else (-1 if liquid_ratio < 0.3 else 0)
            evidence_parts.append(f"Liquid tier ratio={liquid_ratio:.0%} ({deep_mid}/{total} DEEP/MID)")

    if liquidity_rows:
        evidence_parts.append(f"{len(liquidity_rows)} symbols with liquidity profiles")

    # Veto-blocked arbitration implies liquidity pressure
    if arb_rows:
        veto_count = sum(1 for r in arb_rows if r.get('veto_triggered', 0))
        ratio = veto_count / len(arb_rows)
        if ratio > 0.5:
            direction = max(direction - 1, -1)
            evidence_parts.append(f"High veto rate from execution: {ratio:.0%}")

    return {
        'force_type': 'LIQUIDITY',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No liquidity data',
        'contributing_engines': FORCE_ENGINE_MAP['LIQUIDITY'],
    }


def calc_regime_pull(regime, transition):
    """REGIME_PULL force — how strongly is the regime pulling the market."""
    direction = 0
    magnitude = 0.3
    evidence_parts = []

    if regime:
        r = regime.get('regime', 'NEUTRAL')
        breadth = regime.get('breadth_pct', 0.5) or 0.5
        direction = 1 if r == 'BULL' else (-1 if r == 'BEAR' else 0)
        magnitude = clamp(0.3 + breadth * 0.5)
        evidence_parts.append(f"Regime={r}, breadth={breadth:.0%}")

    if transition:
        ewi = transition.get('early_warning_index', 50.0) or 50.0
        prob5 = transition.get('prob_5d', 0.2) or 0.2
        # High transition probability weakens regime pull
        magnitude = clamp(magnitude * (1.0 - prob5 * 0.5))
        level = transition.get('ewi_level', 'NORMAL')
        evidence_parts.append(f"EWI={ewi:.1f} ({level}), P(transition_5d)={prob5:.0%}")

    return {
        'force_type': 'REGIME_PULL',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No regime data',
        'contributing_engines': FORCE_ENGINE_MAP['REGIME_PULL'],
    }


def calc_sentiment_wave(synthesis, regime):
    """SENTIMENT_WAVE from synthesis reports and regime context."""
    direction = 0
    magnitude = 0.3
    evidence_parts = []

    if synthesis:
        summary = synthesis.get('market_state_summary', '') or ''
        key_risks = synthesis.get('key_risks', '') or ''
        candidates_raw = synthesis.get('top_candidates', '') or ''

        # Heuristic: parse JSON if possible
        try:
            candidates = json.loads(candidates_raw) if candidates_raw else []
        except Exception:
            candidates = []

        if candidates:
            magnitude = clamp(0.4 + len(candidates) * 0.05)

        # Keyword-based sentiment
        bullish_kw = ['bull', 'strong', 'uptrend', 'breakout', 'momentum']
        bearish_kw = ['bear', 'weak', 'downtrend', 'breakdown', 'risk', 'decline']
        s_lower = summary.lower()
        bull_score = sum(1 for k in bullish_kw if k in s_lower)
        bear_score = sum(1 for k in bearish_kw if k in s_lower)

        if bull_score > bear_score:
            direction = 1
        elif bear_score > bull_score:
            direction = -1

        evidence_parts.append(f"Synthesis report: bull_kw={bull_score}, bear_kw={bear_score}")
        if candidates:
            evidence_parts.append(f"{len(candidates)} candidates identified")

    if regime:
        r = regime.get('regime', 'NEUTRAL')
        if r == 'BULL' and direction >= 0:
            direction = 1
            magnitude = clamp(magnitude + 0.1)
        elif r == 'BEAR' and direction <= 0:
            direction = -1
            magnitude = clamp(magnitude + 0.1)

    return {
        'force_type': 'SENTIMENT_WAVE',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No sentiment data',
        'contributing_engines': FORCE_ENGINE_MAP['SENTIMENT_WAVE'],
    }


def calc_catalyst_flow(synthesis, episodes):
    """CATALYST_FLOW from synthesis and recent episode outcomes."""
    direction = 0
    magnitude = 0.25
    evidence_parts = []

    if episodes:
        outcomes = [ep.get('outcome_7d', 0.0) or 0.0 for ep in episodes[:5]]
        if outcomes:
            avg_out = statistics.mean(outcomes)
            magnitude = clamp(0.2 + abs(avg_out) * 2)
            direction = 1 if avg_out > 0.005 else (-1 if avg_out < -0.005 else 0)
            evidence_parts.append(f"Recent episode avg 7d outcome={avg_out:.2%}")

        labels = [ep.get('outcome_label', '') or '' for ep in episodes[:5]]
        bull_eps = sum(1 for l in labels if 'bull' in l.lower() or 'up' in l.lower())
        bear_eps = sum(1 for l in labels if 'bear' in l.lower() or 'down' in l.lower())
        if bull_eps > bear_eps:
            direction = 1
        elif bear_eps > bull_eps:
            direction = -1
        evidence_parts.append(f"Episode labels: {bull_eps} bullish, {bear_eps} bearish")

    if synthesis:
        n_expl = synthesis.get('explosion_count', 0) or 0
        if n_expl > 0:
            magnitude = clamp(magnitude + n_expl * 0.02)
            evidence_parts.append(f"Explosion count in synthesis={n_expl}")

    return {
        'force_type': 'CATALYST_FLOW',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No catalyst data',
        'contributing_engines': FORCE_ENGINE_MAP['CATALYST_FLOW'],
    }


def calc_law_density(law_grades, structural_laws, universal_laws):
    """LAW_DENSITY from active high-precision laws."""
    direction = 1  # Laws are generally bullish (they indicate predictability)
    magnitude = 0.3
    evidence_parts = []

    total_laws = len(law_grades) + len(structural_laws) + len(universal_laws)

    if law_grades:
        grades = [r.get('grade', '') for r in law_grades]
        a_count = grades.count('A')
        b_count = grades.count('B')
        good_ratio = (a_count + b_count) / len(grades) if grades else 0
        magnitude = clamp(0.2 + good_ratio * 0.6 + len(law_grades) * 0.002)
        evidence_parts.append(f"Law grades: A={a_count}, B={b_count} of {len(grades)} graded laws")

    if structural_laws:
        high_conf = [r for r in structural_laws if r.get('confidence_level', '') in ('HIGH', 'VERY_HIGH')]
        evidence_parts.append(f"{len(high_conf)}/{len(structural_laws)} structural laws high confidence")
        magnitude = clamp(magnitude + len(high_conf) * 0.01)

    if universal_laws:
        evidence_parts.append(f"{len(universal_laws)} universal laws active")

    if not evidence_parts:
        evidence_parts.append(f"Total laws in system: {total_laws}")

    return {
        'force_type': 'LAW_DENSITY',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No law data',
        'contributing_engines': FORCE_ENGINE_MAP['LAW_DENSITY'],
    }


def calc_risk_pressure(transition, anti_laws, arb_rows):
    """RISK_PRESSURE — higher magnitude = more risk."""
    direction = -1  # Risk is always bearish pressure
    magnitude = 0.3
    evidence_parts = []

    if transition:
        ewi = transition.get('early_warning_index', 50.0) or 50.0
        fail_sig = transition.get('failure_signal', 0.0) or 0.0
        frag_sig = transition.get('fragmentation_signal', 0.0) or 0.0
        prob5 = transition.get('prob_5d', 0.2) or 0.2
        level = transition.get('ewi_level', 'NORMAL')

        magnitude = clamp(ewi / 100.0 * 0.5 + fail_sig * 0.2 + frag_sig * 0.2 + prob5 * 0.1)
        evidence_parts.append(f"EWI={ewi:.1f} ({level}), fail={fail_sig:.2f}, frag={frag_sig:.2f}")

    if anti_laws:
        veto_laws = [r for r in anti_laws if r.get('is_veto', 0)]
        critical_laws = [r for r in anti_laws if r.get('severity', '') == 'CRITICAL']
        magnitude = clamp(magnitude + len(veto_laws) * 0.03 + len(critical_laws) * 0.02)
        evidence_parts.append(f"Anti-laws: {len(veto_laws)} VETO, {len(critical_laws)} CRITICAL")

    if arb_rows:
        veto_count = sum(1 for r in arb_rows if r.get('veto_triggered', 0))
        if veto_count > 0:
            magnitude = clamp(magnitude + veto_count * 0.01)
            evidence_parts.append(f"{veto_count} veto-blocked arbitration decisions")

    return {
        'force_type': 'RISK_PRESSURE',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No risk data',
        'contributing_engines': FORCE_ENGINE_MAP['RISK_PRESSURE'],
    }


def calc_anomaly_field(episodes, synthesis):
    """ANOMALY_FIELD from unusual episodes and synthesis."""
    direction = 0
    magnitude = 0.2
    evidence_parts = []

    if episodes:
        high_vol = [ep for ep in episodes if (ep.get('volatility_level', 0) or 0) > 0.15]
        high_disp = [ep for ep in episodes if (ep.get('return_dispersion', 0) or 0) > 0.05]
        magnitude = clamp(0.15 + len(high_vol) * 0.04 + len(high_disp) * 0.03)

        avg_vols = [ep.get('volatility_level', 0) or 0 for ep in episodes]
        mean_vol = statistics.mean(avg_vols) if avg_vols else 0
        direction = -1 if mean_vol > 0.12 else (0 if mean_vol > 0.06 else 1)
        evidence_parts.append(f"{len(high_vol)} high-vol episodes, mean_vol={mean_vol:.2%}")

    if synthesis:
        expl = synthesis.get('explosion_count', 0) or 0
        if expl > 5:
            magnitude = clamp(magnitude + 0.15)
            direction = 1  # explosions = opportunity anomaly
            evidence_parts.append(f"Explosion anomaly: {expl} explosions in synthesis")

    return {
        'force_type': 'ANOMALY_FIELD',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No anomaly data',
        'contributing_engines': FORCE_ENGINE_MAP['ANOMALY_FIELD'],
    }


def calc_contagion_wave(contagion_rows):
    """CONTAGION_WAVE from sector contagion data."""
    direction = -1  # contagion is generally bearish
    magnitude = 0.2
    evidence_parts = []

    if contagion_rows:
        rates = [r.get('co_rate_pct', 0) or 0 for r in contagion_rows]
        max_rate = max(rates) if rates else 0
        avg_rate = statistics.mean(rates) if rates else 0
        magnitude = clamp(avg_rate / 100.0 * 0.6 + 0.1)

        high_contagion = [r for r in contagion_rows if (r.get('co_rate_pct', 0) or 0) > 50]
        if high_contagion:
            pairs = [f"{r.get('source_sector','?')}→{r.get('target_sector','?')}" for r in high_contagion[:3]]
            evidence_parts.append(f"High contagion pairs: {', '.join(pairs)}")
        evidence_parts.append(f"Max co_rate={max_rate:.0f}%, avg={avg_rate:.0f}%")
    else:
        evidence_parts.append("No contagion data available")
        magnitude = 0.15

    return {
        'force_type': 'CONTAGION_WAVE',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No contagion data',
        'contributing_engines': FORCE_ENGINE_MAP['CONTAGION_WAVE'],
    }


def calc_structural_drift(episodes, law_stability):
    """STRUCTURAL_DRIFT from episodes and law stability over time."""
    direction = 0
    magnitude = 0.2
    evidence_parts = []

    if episodes and len(episodes) >= 3:
        outcomes_30d = [ep.get('outcome_30d', 0) or 0 for ep in episodes[:5]]
        avg_30d = statistics.mean(outcomes_30d) if outcomes_30d else 0
        direction = 1 if avg_30d > 0.01 else (-1 if avg_30d < -0.01 else 0)
        magnitude = clamp(0.15 + abs(avg_30d) * 3)
        evidence_parts.append(f"30d structural outcome avg={avg_30d:.2%}")

        vols = [ep.get('volatility_level', 0) or 0 for ep in episodes]
        vol_trend = vols[0] - vols[-1] if len(vols) >= 2 else 0
        evidence_parts.append(f"Vol trend (recent vs old): {vol_trend:+.3f}")

    if law_stability:
        # If many laws are degrading, structural drift is negative
        evidence_parts.append(f"{len(law_stability)} law stability data points")

    return {
        'force_type': 'STRUCTURAL_DRIFT',
        'magnitude': round(magnitude, 4),
        'direction': direction,
        'evidence': '; '.join(evidence_parts) if evidence_parts else 'No structural data',
        'contributing_engines': FORCE_ENGINE_MAP['STRUCTURAL_DRIFT'],
    }


# ─── COMMAND: dominant_forces ─────────────────────────────────────────────────

def dominant_forces(params):
    conn = get_db()
    try:
        regime = read_regime(conn)
        transition = read_regime_transition(conn)
        law_grades = read_law_grades(conn)
        structural_laws = read_structural_laws(conn)
        universal_laws = read_universal_laws(conn)
        anti_laws = read_anti_laws(conn)
        arb_rows = read_arbitration(conn)
        contagion = read_sector_contagion(conn)
        episodes = read_market_episodes(conn)
        synthesis = read_synthesis_reports(conn)
        liquidity_rows = read_liquidity(conn)
        universe_rows = read_stock_universe(conn)
        law_stability = read_law_stability(conn)
    finally:
        conn.close()

    all_forces = [
        calc_momentum(regime, transition, episodes),
        calc_liquidity(liquidity_rows, universe_rows, arb_rows),
        calc_regime_pull(regime, transition),
        calc_sentiment_wave(synthesis, regime),
        calc_catalyst_flow(synthesis, episodes),
        calc_law_density(law_grades, structural_laws, universal_laws),
        calc_risk_pressure(transition, anti_laws, arb_rows),
        calc_anomaly_field(episodes, synthesis),
        calc_contagion_wave(contagion),
        calc_structural_drift(episodes, law_stability),
    ]

    # Sort by magnitude descending, pick top 5
    all_forces.sort(key=lambda f: f['magnitude'], reverse=True)
    top5 = all_forces[:5]

    # Weighted market vector
    total_mag = sum(f['magnitude'] for f in top5)
    if total_mag > 0:
        market_vector = sum(f['magnitude'] * f['direction'] for f in top5) / total_mag
    else:
        market_vector = 0.0

    n_bullish = sum(1 for f in top5 if f['direction'] > 0)
    n_bearish = sum(1 for f in top5 if f['direction'] < 0)
    dominant_force = top5[0]['force_type'] if top5 else 'UNKNOWN'

    return {
        'forces': top5,
        'dominant_force': dominant_force,
        'market_vector': round(market_vector, 4),
        'n_bullish': n_bullish,
        'n_bearish': n_bearish,
    }


# ─── COMMAND: critical_risks ──────────────────────────────────────────────────

def critical_risks(params):
    conn = get_db()
    try:
        transition = read_regime_transition(conn)
        anti_laws = read_anti_laws(conn)
        law_grades = read_law_grades(conn)
        episodes = read_market_episodes(conn)
        synthesis = read_synthesis_reports(conn)
    finally:
        conn.close()

    risks = []

    # ── Risk 1: Regime transition risk
    if transition:
        ewi = transition.get('early_warning_index', 50.0) or 50.0
        prob5 = transition.get('prob_5d', 0.2) or 0.2
        prob10 = transition.get('prob_10d', 0.3) or 0.3
        level = transition.get('ewi_level', 'NORMAL')
        current_regime = transition.get('current_regime', 'UNKNOWN') or 'UNKNOWN'
        next_regime = transition.get('most_likely_next', 'UNKNOWN') or 'UNKNOWN'

        if ewi >= 70:
            severity = 'CRITICAL'
        elif ewi >= 50:
            severity = 'HIGH'
        elif ewi >= 30:
            severity = 'MEDIUM'
        else:
            severity = 'MEDIUM'

        risks.append({
            'risk_type': 'REGIME_TRANSITION',
            'severity': severity,
            'description': (
                f"EWI={ewi:.1f} ({level}): regime may shift from {current_regime} to {next_regime}. "
                f"P(5d)={prob5:.0%}, P(10d)={prob10:.0%}"
            ),
            'engine_source': 'regime_transition_forecaster',
            'mitigation': 'Reduce position sizes 30-50%; monitor breadth for confirmation',
            '_score': ewi / 100.0,
        })

    # ── Risk 2: VETO anti-laws
    if anti_laws:
        veto_laws = [r for r in anti_laws if r.get('is_veto', 0)]
        critical_anti = [r for r in anti_laws if r.get('severity', '') == 'CRITICAL']
        count = len(veto_laws) + len(critical_anti)
        score = clamp(count * 0.1)

        if count >= 5:
            severity = 'CRITICAL'
        elif count >= 3:
            severity = 'HIGH'
        else:
            severity = 'MEDIUM'

        if count > 0:
            examples = [r.get('description', r.get('anti_law_id', '?')) for r in (veto_laws + critical_anti)[:3]]
            risks.append({
                'risk_type': 'ANTI_LAW_VETO',
                'severity': severity,
                'description': (
                    f"{len(veto_laws)} VETO-level + {len(critical_anti)} CRITICAL anti-laws active. "
                    f"Examples: {'; '.join(examples[:2])}"
                ),
                'engine_source': 'anti_laws_engine',
                'mitigation': 'Block entries for symbols covered by VETO anti-laws; review causal filters',
                '_score': score,
            })

    # ── Risk 3: Poor law quality (F-grade laws)
    if law_grades:
        f_grades = [r for r in law_grades if r.get('grade', '') == 'F']
        high_overfit = [r for r in law_grades if r.get('overfitting_risk', '') in ('HIGH', 'EXTREME')]
        total = len(law_grades)
        bad_ratio = (len(f_grades) + len(high_overfit)) / total if total > 0 else 0
        score = clamp(bad_ratio)

        if bad_ratio >= 0.4:
            severity = 'CRITICAL'
        elif bad_ratio >= 0.25:
            severity = 'HIGH'
        else:
            severity = 'MEDIUM'

        if f_grades or high_overfit:
            risks.append({
                'risk_type': 'LAW_QUALITY_DEGRADATION',
                'severity': severity,
                'description': (
                    f"{len(f_grades)} F-grade laws + {len(high_overfit)} high-overfit laws out of {total} total. "
                    f"Bad law ratio={bad_ratio:.0%}"
                ),
                'engine_source': 'law_synthesis / historical_validation',
                'mitigation': 'Quarantine F-grade laws; increase minimum precision threshold to 0.60',
                '_score': score,
            })

    # ── Risk 4: Anomaly volatility spike from episodes
    if episodes:
        recent_vols = [ep.get('volatility_level', 0) or 0 for ep in episodes[:5]]
        max_vol = max(recent_vols) if recent_vols else 0
        if max_vol > 0.18:
            risks.append({
                'risk_type': 'VOLATILITY_SPIKE',
                'severity': 'HIGH' if max_vol > 0.25 else 'MEDIUM',
                'description': (
                    f"Recent episode max volatility={max_vol:.2%}. "
                    f"Structural instability detected in last {len(episodes[:5])} episodes."
                ),
                'engine_source': 'episodic_memory_engine',
                'mitigation': 'Tighten stops; avoid momentum entries; wait for vol compression',
                '_score': clamp(max_vol * 3),
            })

    # ── Risk 5: Synthesis key risks
    if synthesis:
        key_risks_raw = synthesis.get('key_risks', '') or ''
        try:
            key_risks_list = json.loads(key_risks_raw) if key_risks_raw else []
        except Exception:
            key_risks_list = [key_risks_raw] if key_risks_raw else []
        if key_risks_list:
            risks.append({
                'risk_type': 'SYNTHESIS_ALERT',
                'severity': 'HIGH',
                'description': f"Synthesis engine flags: {'; '.join(str(r) for r in key_risks_list[:3])}",
                'engine_source': 'unified_daily_synthesis',
                'mitigation': 'Review synthesis report; cross-check with causal engine',
                '_score': 0.55,
            })

    # Sort by score and pick top 3
    risks.sort(key=lambda r: r.get('_score', 0), reverse=True)
    top3 = risks[:3]

    # Compute aggregate risk level
    for r in top3:
        r.pop('_score', None)

    if top3:
        severity_map = {'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        avg_sev = statistics.mean([severity_map.get(r['severity'], 2) for r in top3])
    else:
        avg_sev = 1.5

    if avg_sev >= 3.5:
        aggregate_risk_level = 'EXTREME'
        risk_score = clamp(0.85 + (avg_sev - 3.5) * 0.1)
    elif avg_sev >= 2.5:
        aggregate_risk_level = 'HIGH'
        risk_score = clamp(0.60 + (avg_sev - 2.5) * 0.2)
    elif avg_sev >= 1.5:
        aggregate_risk_level = 'MODERATE'
        risk_score = clamp(0.35 + (avg_sev - 1.5) * 0.2)
    else:
        aggregate_risk_level = 'LOW'
        risk_score = 0.2

    return {
        'risks': top3,
        'aggregate_risk_level': aggregate_risk_level,
        'risk_score': round(risk_score, 4),
    }


# ─── COMMAND: opportunities ────────────────────────────────────────────────────

def opportunities(params):
    conn = get_db()
    try:
        intel_scores = read_intelligence_scores(conn)
        arb_rows = read_arbitration(conn)
        law_grades = read_law_grades(conn)
        episodes = read_market_episodes(conn)
        synthesis = read_synthesis_reports(conn)
    finally:
        conn.close()

    opps = []

    # ── Opp 1: Top intelligence score symbol
    if intel_scores:
        top_sym = intel_scores[0]
        sym = top_sym.get('symbol', 'N/A')
        score = top_sym.get('intelligence_score', 50.0) or 50.0
        driver = top_sym.get('primary_driver', 'unknown') or 'unknown'
        rank = top_sym.get('percentile_rank', 50.0) or 50.0
        confidence = clamp(score / 100.0)
        opps.append({
            'opportunity_type': 'TOP_RANKED_SYMBOL',
            'symbol_or_pattern': sym,
            'confidence': round(confidence, 4),
            'expected_edge': round(score / 200.0, 4),
            'time_window': '1-3 days',
            'description': f"Intelligence score={score:.1f}, driver={driver}, percentile={rank:.0f}%",
            '_score': confidence,
        })

    # ── Opp 2: Best ENTER arbitration decision
    enter_decisions = [r for r in arb_rows if r.get('decision', '') == 'ENTER' and not r.get('veto_triggered', 0)]
    if enter_decisions:
        best = max(enter_decisions, key=lambda r: r.get('confidence', 0) or 0)
        sym = best.get('symbol', 'N/A')
        conf = clamp((best.get('confidence', 0) or 0) / 100.0)
        score = best.get('score', 50.0) or 50.0
        regime = best.get('regime', 'UNKNOWN') or 'UNKNOWN'
        opps.append({
            'opportunity_type': 'ARBITRATION_ENTER',
            'symbol_or_pattern': sym,
            'confidence': round(conf, 4),
            'expected_edge': round(score / 200.0, 4),
            'time_window': '1-5 days',
            'description': f"Arbitration ENTER: conf={conf:.0%}, regime={regime}",
            '_score': conf,
        })

    # ── Opp 3: A-grade law firing
    if law_grades:
        a_laws = [r for r in law_grades if r.get('grade', '') == 'A']
        if a_laws:
            best_law = max(a_laws, key=lambda r: r.get('precision', 0) or 0)
            prec = best_law.get('precision', 0.7) or 0.7
            law_name = best_law.get('law_id', 'unknown') or 'unknown'
            opps.append({
                'opportunity_type': 'A_GRADE_LAW',
                'symbol_or_pattern': law_name,
                'confidence': round(clamp(prec), 4),
                'expected_edge': round((prec - 0.5) * 0.8, 4),
                'time_window': '2-7 days',
                'description': f"{len(a_laws)} A-grade laws; top precision={prec:.0%}",
                '_score': clamp(prec),
            })

    # ── Opp 4: Positive episode outcome (structural opportunity)
    if episodes:
        pos_ep = [ep for ep in episodes if (ep.get('outcome_7d', 0) or 0) > 0.02]
        if pos_ep:
            best_ep = max(pos_ep, key=lambda e: e.get('outcome_7d', 0) or 0)
            out = best_ep.get('outcome_7d', 0) or 0
            opps.append({
                'opportunity_type': 'EPISODE_MOMENTUM',
                'symbol_or_pattern': f"Episode_{best_ep.get('episode_id','?')}",
                'confidence': round(clamp(0.4 + out * 5), 4),
                'expected_edge': round(out, 4),
                'time_window': '5-7 days',
                'description': f"Recent episode 7d outcome={out:.2%}, breadth={best_ep.get('breadth_score',0):.2f}",
                '_score': clamp(0.4 + out * 5),
            })

    # Sort and pick top 2
    opps.sort(key=lambda o: o.get('_score', 0), reverse=True)
    top2 = opps[:2]

    for o in top2:
        o.pop('_score', None)

    if top2:
        opp_score = statistics.mean([o['confidence'] for o in top2])
        best_opp = top2[0]['symbol_or_pattern']
    else:
        opp_score = 0.2
        best_opp = 'NONE'

    return {
        'opportunities': top2,
        'best_opportunity': best_opp,
        'opportunity_score': round(opp_score, 4),
    }


# ─── COMMAND: mii ─────────────────────────────────────────────────────────────

def _compute_regime_clarity(regime, transition):
    """0-1 score for how clear/confident the current regime is."""
    clarity = 0.5
    if regime:
        r = regime.get('regime', 'NEUTRAL')
        breadth = regime.get('breadth_pct', 0.5) or 0.5
        if r in ('BULL', 'BEAR'):
            clarity = 0.5 + breadth * 0.4
        else:
            clarity = 0.3 + breadth * 0.2

    if transition:
        prob5 = transition.get('prob_5d', 0.2) or 0.2
        # High transition probability reduces clarity
        clarity = clamp(clarity * (1.0 - prob5 * 0.6))

    return round(clarity, 4)


def _compute_law_quality(law_grades, structural_laws):
    """0-1 score: fraction of laws graded A or B."""
    if not law_grades:
        return 0.4  # default neutral

    grades = [r.get('grade', '') for r in law_grades]
    ab_count = sum(1 for g in grades if g in ('A', 'B'))
    quality = ab_count / len(grades) if grades else 0.4

    # Boost slightly for having structural laws
    if structural_laws:
        high_conf = [r for r in structural_laws if r.get('confidence_level', '') in ('HIGH', 'VERY_HIGH')]
        quality = clamp(quality + len(high_conf) * 0.01)

    return round(quality, 4)


def _compute_system_agreement(synthesis, arb_rows):
    """0-1 inter-engine agreement. Default 0.5 when unavailable."""
    if not arb_rows:
        return 0.5

    decisions = [r.get('decision', '') for r in arb_rows]
    if not decisions:
        return 0.5

    from collections import Counter
    counts = Counter(decisions)
    most_common_ratio = counts.most_common(1)[0][1] / len(decisions)
    # Invert veto signals
    veto_ratio = sum(1 for r in arb_rows if r.get('veto_triggered', 0)) / len(arb_rows)

    agreement = clamp(most_common_ratio * 0.7 + (1 - veto_ratio) * 0.3)
    return round(agreement, 4)


def _compute_momentum_strength(market_vector, top5_forces):
    """0-1 measure of |market_vector| × direction_consistency."""
    if not top5_forces:
        return 0.3
    directions = [f['direction'] for f in top5_forces]
    n_agree = sum(1 for d in directions if (d > 0) == (market_vector > 0) or d == 0)
    consistency = n_agree / len(directions) if directions else 0.5
    strength = clamp(abs(market_vector) * consistency)
    return round(strength, 4)


def mii(params):
    # Gather data
    conn = get_db()
    try:
        regime = read_regime(conn)
        transition = read_regime_transition(conn)
        law_grades = read_law_grades(conn)
        structural_laws = read_structural_laws(conn)
        arb_rows = read_arbitration(conn)
        synthesis = read_synthesis_reports(conn)
    finally:
        conn.close()

    # Sub-commands for building MII
    forces_result = dominant_forces(params)
    risks_result = critical_risks(params)
    opps_result = opportunities(params)

    market_vector = forces_result['market_vector']
    top5 = forces_result['forces']
    aggregate_risk = risks_result['risk_score']
    opp_score = opps_result['opportunity_score']

    # Component calculations
    regime_clarity = _compute_regime_clarity(regime, transition)
    law_quality = _compute_law_quality(law_grades, structural_laws)
    risk_adj_opp = clamp(opp_score * (1.0 - aggregate_risk))
    system_agreement = _compute_system_agreement(synthesis, arb_rows)
    momentum_strength = _compute_momentum_strength(market_vector, top5)

    # Weighted MII
    components = {
        'regime_clarity': regime_clarity,
        'law_quality': law_quality,
        'risk_adjusted_opportunity': round(risk_adj_opp, 4),
        'system_agreement': system_agreement,
        'momentum_strength': momentum_strength,
    }

    mii_raw = weighted_avg(
        [regime_clarity, law_quality, risk_adj_opp, system_agreement, momentum_strength],
        [0.25, 0.20, 0.20, 0.15, 0.20]
    )
    mii_score = round(clamp(mii_raw) * 100, 2)

    # Interpretation
    if mii_score >= 80:
        interpretation = 'PRIME'
        recommendation = 'Optimal trading conditions — deploy capital with high conviction setups'
    elif mii_score >= 60:
        interpretation = 'GOOD'
        recommendation = 'Favorable conditions — trade A/B-grade setups with standard sizing'
    elif mii_score >= 40:
        interpretation = 'NEUTRAL'
        recommendation = 'Mixed signals — reduce size, only highest-conviction setups'
    elif mii_score >= 20:
        interpretation = 'POOR'
        recommendation = 'Avoid most entries — wait for clearer regime and better law quality'
    else:
        interpretation = 'CRISIS'
        recommendation = 'Capital preservation mode — no new entries; protect existing positions'

    return {
        'mii': mii_score,
        'interpretation': interpretation,
        'components': components,
        'recommendation': recommendation,
    }


# ─── COMMAND: market_briefing ──────────────────────────────────────────────────

def market_briefing(params):
    forces_result = dominant_forces(params)
    risks_result = critical_risks(params)
    opps_result = opportunities(params)

    dominant = forces_result['dominant_force']
    market_vector = forces_result['market_vector']
    forces = forces_result['forces']
    risks = risks_result['risks']
    opps = opps_result['opportunities']
    top_risk = risks[0] if risks else {}
    top_opp = opps[0] if opps else {}

    # Get regime
    conn = get_db()
    try:
        regime_row = read_regime(conn)
    finally:
        conn.close()
    regime_name = regime_row.get('regime', 'محايد') if regime_row else 'محايد'

    magnitude_pct = round((forces[0]['magnitude'] if forces else 0) * 100)
    opp_label = top_opp.get('symbol_or_pattern', 'غير محدد') if top_opp else 'غير محدد'
    _risk_ar_map = {
        'ANTI_LAW_VETO':           'تعارض قانون مضاد',
        'LAW_QUALITY_DEGRADATION': 'تدهور جودة القوانين',
        'SYNTHESIS_ALERT':         'تنبيه تحليلي',
        'REGIME_INSTABILITY':      'عدم استقرار النظام',
        'BREADTH_COLLAPSE':        'انهيار الاتساع',
        'LIQUIDITY_CRISIS':        'أزمة سيولة',
        'DATA_STALENESS':          'قدم البيانات',
    }
    _raw_risk = top_risk.get('risk_type', '') if top_risk else ''
    risk_label = _risk_ar_map.get(_raw_risk, _raw_risk.replace('_', ' ')) if _raw_risk else 'غير محدد'

    # Arabic regime map
    regime_ar = {
        'BULL': 'صاعد', 'BEAR': 'هابط', 'SIDEWAYS': 'عرضي',
        'NEUTRAL': 'محايد', 'VOLATILE': 'متذبذب'
    }
    regime_ar_name = regime_ar.get(regime_name, regime_name)

    # Force Arabic names
    force_ar = {
        'MOMENTUM': 'الزخم السعري', 'LIQUIDITY': 'السيولة',
        'REGIME_PULL': 'جذب النظام', 'SENTIMENT_WAVE': 'موجة المعنويات',
        'CATALYST_FLOW': 'تدفق المحفزات', 'LAW_DENSITY': 'كثافة القوانين',
        'RISK_PRESSURE': 'ضغط المخاطر', 'ANOMALY_FIELD': 'حقل الشذوذات',
        'CONTAGION_WAVE': 'موجة العدوى', 'STRUCTURAL_DRIFT': 'الانجراف الهيكلي',
    }
    dominant_ar = force_ar.get(dominant, dominant)

    arabic_briefing = (
        f"السوق في وضع {regime_ar_name} مع {dominant_ar} قوة {magnitude_pct}% "
        f"— أبرز فرصة: {opp_label} "
        f"— أبرز خطر: {risk_label}"
    )

    dir_str = 'bullish' if market_vector > 0.1 else ('bearish' if market_vector < -0.1 else 'neutral')
    risk_sev = top_risk.get('severity', 'MEDIUM') if top_risk else 'MEDIUM'
    opp_conf = f"{top_opp.get('confidence', 0):.0%}" if top_opp else '0%'

    english_briefing = (
        f"Market is in {regime_name} regime ({dir_str} vector={market_vector:+.2f}) — "
        f"Dominant force: {dominant} at {magnitude_pct}% — "
        f"Best opportunity: {opp_label} ({opp_conf} confidence) — "
        f"Top risk: {risk_label} ({risk_sev})"
    )

    # ── Persist briefing to DB ────────────────────────────────────────────────
    now_iso = datetime.utcnow().isoformat()
    _conn = get_db()
    try:
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS cognitive_briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                arabic_briefing TEXT,
                english_briefing TEXT,
                market_vector REAL,
                top_risk TEXT,
                top_risk_severity TEXT,
                n_risks INTEGER,
                n_opportunities INTEGER,
                risks_json TEXT,
                generated_at TEXT
            )
        """)
        _conn.execute(
            """INSERT INTO cognitive_briefings
               (date, arabic_briefing, english_briefing, market_vector,
                top_risk, top_risk_severity, n_risks, n_opportunities,
                risks_json, generated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                now_iso[:10],
                arabic_briefing,
                english_briefing,
                market_vector,
                top_risk.get('risk_type', '') if top_risk else '',
                top_risk.get('severity', '') if top_risk else '',
                len(risks),
                len(opps),
                json.dumps(risks[:3], ensure_ascii=False),
                now_iso,
            )
        )
        _conn.commit()
    except Exception:
        try:
            _conn.rollback()
        except Exception:
            pass
    finally:
        _conn.close()

    return {
        'arabic_briefing': arabic_briefing,
        'english_briefing': english_briefing,
        'forces': forces,
        'risks': risks,
        'opportunities': opps,
        'market_vector': market_vector,
    }


# ─── COMMAND: build_full ───────────────────────────────────────────────────────

def build_full(params):
    forces_result = dominant_forces(params)
    risks_result = critical_risks(params)
    opps_result = opportunities(params)
    mii_result = mii(params)

    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        # Create tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dominant_market_forces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                force_type TEXT,
                magnitude REAL,
                direction INTEGER,
                evidence TEXT,
                generated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_intelligence_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mii REAL,
                interpretation TEXT,
                market_vector REAL,
                aggregate_risk_level TEXT,
                n_opportunities INTEGER,
                generated_at TEXT
            )
        """)

        # Insert forces
        for f in forces_result['forces']:
            conn.execute(
                "INSERT INTO dominant_market_forces (force_type, magnitude, direction, evidence, generated_at) VALUES (?,?,?,?,?)",
                (f['force_type'], f['magnitude'], f['direction'], f['evidence'], now)
            )

        # Insert MII
        conn.execute(
            "INSERT INTO market_intelligence_index (mii, interpretation, market_vector, aggregate_risk_level, n_opportunities, generated_at) VALUES (?,?,?,?,?,?)",
            (
                mii_result['mii'],
                mii_result['interpretation'],
                forces_result['market_vector'],
                risks_result['aggregate_risk_level'],
                len(opps_result['opportunities']),
                now,
            )
        )

        conn.commit()

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            'status': 'error',
            'error': str(e),
        }
    finally:
        conn.close()

    top_risk_type = risks_result['risks'][0]['risk_type'] if risks_result['risks'] else 'NONE'
    top_opp_sym = opps_result['opportunities'][0]['symbol_or_pattern'] if opps_result['opportunities'] else 'NONE'

    return {
        'status': 'built',
        'mii': mii_result['mii'],
        'interpretation': mii_result['interpretation'],
        'market_vector': forces_result['market_vector'],
        'dominant_force': forces_result['dominant_force'],
        'top_risk': top_risk_type,
        'top_opportunity': top_opp_sym,
    }


# ─── DISPATCH ─────────────────────────────────────────────────────────────────

COMMANDS = {
    'dominant_forces': dominant_forces,
    'critical_risks': critical_risks,
    'opportunities': opportunities,
    'market_briefing': market_briefing,
    'mii': mii,
    'build_full': build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({'error': 'Usage: cognitive_compression.py <command> <params_json>'}))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except Exception as e:
        print(json.dumps({'error': f'Invalid params JSON: {e}'}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
    except Exception as e:
        import traceback
        result = {'error': str(e), 'traceback': traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
