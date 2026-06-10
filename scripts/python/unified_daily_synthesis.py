#!/usr/bin/env python3
"""
Phase 28 — Unified Daily Intelligence Synthesis (UDIS)
The Crown Jewel: integrates ALL phases (20-27) into a single daily
intelligence briefing with 9 structured sections.

Usage:
    python unified_daily_synthesis.py <command> [json_params]

Commands:
    synthesize          — run full 9-section synthesis for today (or given date)
    daily_brief         — return the last stored synthesis as formatted text
    get_last_report     — return the last stored synthesis as JSON
    get_section         — return a single section by name
    build_synthesis     — alias for synthesize
    status              — check which data sources are available

Output: JSON to stdout, {success: true/false, ...}
"""

import sys
import os
import json
import sqlite3
import math
import time
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS synthesis_reports (
        report_id   TEXT PRIMARY KEY,
        date        TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        duration_s  REAL,
        n_sections  INTEGER,
        market_state_summary  TEXT,
        explosion_count       INTEGER,
        top_candidates        TEXT,
        key_risks             TEXT,
        full_json             TEXT
    );
    CREATE TABLE IF NOT EXISTS synthesis_sections (
        section_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id   TEXT NOT NULL,
        section_key TEXT NOT NULL,
        section_name TEXT,
        data_json   TEXT,
        signal_count INTEGER DEFAULT 0,
        quality_score REAL DEFAULT 0.0,
        UNIQUE(report_id, section_key)
    );
    """)
    db.commit()


def _safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safeint(v, default=0):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _today():
    return datetime.utcnow().strftime('%Y-%m-%d')


def _report_id(date_str):
    return hashlib.md5(f"synthesis_{date_str}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Section 1: Market State — regime, breadth, forces, macro
# ---------------------------------------------------------------------------

def _section_market_state(db, target_date):
    """Regime, market breadth, macro snapshot, force field."""
    result = {
        'section': '1_market_state',
        'title': 'حالة السوق الكلية (Market State)',
    }

    # Regime
    regime_row = db.execute(
        "SELECT * FROM regime_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if regime_row:
        cols = regime_row.keys()
        result['regime'] = dict(zip(cols, tuple(regime_row)))
    else:
        result['regime'] = {'regime': 'UNKNOWN', 'confidence': 0}

    # Market breadth
    try:
        breadth = db.execute(
            "SELECT * FROM market_breadth_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if breadth:
            result['breadth'] = {
                'date': str(breadth['date'] if 'date' in breadth.keys() else ''),
                'advancing': _safeint(breadth['advancing'] if 'advancing' in breadth.keys() else None),
                'declining': _safeint(breadth['declining'] if 'declining' in breadth.keys() else None),
                'ad_line': _safe(breadth['ad_line'] if 'ad_line' in breadth.keys() else None),
            }
        else:
            result['breadth'] = {'note': 'No breadth data — run historical_integrity_engine.py compute_breadth'}
    except Exception:
        result['breadth'] = {'note': 'breadth table not found'}

    # Macro snapshot
    try:
        macro_rows = db.execute(
            "SELECT * FROM global_macro ORDER BY fetch_date DESC LIMIT 5"
        ).fetchall()
        if macro_rows:
            macro_list = []
            for row in macro_rows:
                cols = row.keys()
                macro_list.append(dict(zip(cols, tuple(row))))
            result['macro'] = macro_list[:3]
        else:
            result['macro'] = []
    except Exception:
        # Try macro_snapshot
        try:
            macro_snap = db.execute("SELECT * FROM macro_snapshot LIMIT 1").fetchone()
            if macro_snap:
                cols = macro_snap.keys()
                result['macro'] = [dict(zip(cols, tuple(macro_snap)))]
        except Exception:
            result['macro'] = []

    # Data integrity overview
    try:
        integrity = db.execute("""
            SELECT tier, COUNT(*) as n
            FROM data_integrity
            GROUP BY tier
        """).fetchall()
        result['data_integrity'] = {row['tier']: row['n'] for row in integrity}
    except Exception:
        result['data_integrity'] = {}

    # Stock universe size
    universe = db.execute(
        "SELECT COUNT(*) as n, status FROM stock_universe GROUP BY status"
    ).fetchall()
    result['universe'] = {row['status']: row['n'] for row in universe}

    result['signals'] = 3 if result['regime'].get('regime', 'UNKNOWN') != 'UNKNOWN' else 1
    return result


# ---------------------------------------------------------------------------
# Section 2: Causal Narrative — transfer entropy + SHAP + graph paths
# ---------------------------------------------------------------------------

def _section_causal_narrative(db, target_date):
    """Top causal chains from causal_discovery_engine + SHAP from explainability."""
    result = {
        'section': '2_causal_narrative',
        'title': 'السردية السببية (Causal Narrative)',
    }

    # Top causal chains
    try:
        chains = db.execute("""
            SELECT source_entity, target_entity, chain_type, lag_days,
                   strength, p_value, confidence, regime_stable
            FROM causal_chains
            WHERE confidence > 0.3
            ORDER BY confidence DESC LIMIT 10
        """).fetchall()
        result['top_causal_chains'] = [dict(zip(r.keys(), tuple(r))) for r in chains]
    except Exception:
        result['top_causal_chains'] = []
        result['causal_note'] = 'Run causal_discovery_engine.py build_full first'

    # SHAP feature importance from explainability
    try:
        shap_rows = db.execute("""
            SELECT feature_name, mean_shap_value, direction, sector
            FROM shap_feature_importance
            ORDER BY ABS(mean_shap_value) DESC LIMIT 10
        """).fetchall()
        result['top_shap_features'] = [dict(zip(r.keys(), tuple(r))) for r in shap_rows]
    except Exception:
        result['top_shap_features'] = []

    # PCMCI results if available
    try:
        pcmci_rows = db.execute("""
            SELECT source, target, lag, coefficient, p_value
            FROM pcmci_results
            ORDER BY ABS(coefficient) DESC LIMIT 5
        """).fetchall()
        result['pcmci_top'] = [dict(zip(r.keys(), tuple(r))) for r in pcmci_rows]
    except Exception:
        result['pcmci_top'] = []

    # Regime-specific causality
    try:
        current_regime = db.execute(
            "SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if current_regime:
            reg = current_regime['regime']
            regime_chains = db.execute("""
                SELECT source_entity, target_entity, lag_days, strength
                FROM causal_chains
                WHERE best_regime = ? AND p_value < 0.1
                ORDER BY ABS(strength) DESC LIMIT 5
            """, (reg,)).fetchall()
            result['regime_specific_causality'] = {
                'regime': reg,
                'chains': [dict(zip(r.keys(), tuple(r))) for r in regime_chains]
            }
    except Exception:
        result['regime_specific_causality'] = {}

    result['signals'] = len(result['top_causal_chains'])
    return result


# ---------------------------------------------------------------------------
# Section 3: Law Evolution — status, precision trends, mutations
# ---------------------------------------------------------------------------

def _section_law_evolution(db, target_date):
    """Universal law health, mutations, evolution history."""
    result = {
        'section': '3_law_evolution',
        'title': 'تطور القوانين (Law Evolution)',
    }

    # Law status summary
    try:
        law_status = db.execute("""
            SELECT law_status, COUNT(*) as n,
                   AVG(precision) as avg_precision,
                   SUM(n_activations) as total_activations
            FROM universal_laws_p16
            GROUP BY law_status
            ORDER BY avg_precision DESC
        """).fetchall()
        result['law_status_distribution'] = [
            {
                'status': row['law_status'],
                'count': row['n'],
                'avg_precision': round(_safe(row['avg_precision']), 4),
                'total_activations': _safeint(row['total_activations'])
            }
            for row in law_status
        ]
    except Exception:
        result['law_status_distribution'] = []

    # Top performing laws
    try:
        top_laws = db.execute("""
            SELECT pattern_id, pattern_name, law_status, precision,
                   n_activations, regime_stability_score, oos_gap
            FROM universal_laws_p16
            ORDER BY precision DESC LIMIT 5
        """).fetchall()
        result['top_laws'] = [dict(zip(r.keys(), tuple(r))) for r in top_laws]
    except Exception:
        result['top_laws'] = []

    # Worst (degrading) laws
    try:
        worst_laws = db.execute("""
            SELECT pattern_id, pattern_name, law_status, precision, oos_gap
            FROM universal_laws_p16
            WHERE law_status IN ('DEGRADING', 'ARCHIVED')
            ORDER BY precision ASC LIMIT 5
        """).fetchall()
        result['degrading_laws'] = [dict(zip(r.keys(), tuple(r))) for r in worst_laws]
    except Exception:
        result['degrading_laws'] = []

    # Law mutations
    try:
        mutations = db.execute("""
            SELECT pattern_id, variant_precision, base_precision,
                   improvement_pp, beats_base
            FROM law_competition
            WHERE beats_base = 1
            ORDER BY improvement_pp DESC LIMIT 5
        """).fetchall()
        result['winning_mutations'] = [dict(zip(r.keys(), tuple(r))) for r in mutations]
    except Exception:
        result['winning_mutations'] = []

    # Law lineage from adaptive_research_loop
    try:
        lineage = db.execute("""
            SELECT parent_law_id, child_law_id, mutation_type, improvement_pp
            FROM law_lineage
            ORDER BY improvement_pp DESC LIMIT 5
        """).fetchall()
        result['law_lineage'] = [dict(zip(r.keys(), tuple(r))) for r in lineage]
    except Exception:
        result['law_lineage'] = []

    # Research directives
    try:
        directives = db.execute("""
            SELECT priority, directive_type, title, description, status
            FROM research_directives
            WHERE status = 'OPEN'
            ORDER BY priority ASC LIMIT 5
        """).fetchall()
        result['open_directives'] = [dict(zip(r.keys(), tuple(r))) for r in directives]
    except Exception:
        result['open_directives'] = []

    result['signals'] = len(result['top_laws'])
    return result


# ---------------------------------------------------------------------------
# Section 4: Failure Intelligence — daily failure scan
# ---------------------------------------------------------------------------

def _section_failure_intel(db, target_date):
    """Failure archetypes, recent failures, lessons."""
    result = {
        'section': '4_failure_intel',
        'title': 'ذكاء الإخفاق (Failure Intelligence)',
    }

    # Failure taxonomy summary
    try:
        taxonomy = db.execute("""
            SELECT failure_archetype, COUNT(*) as n,
                   AVG(severity_score) as avg_severity,
                   MAX(detected_at) as last_seen
            FROM failure_taxonomy
            GROUP BY failure_archetype
            ORDER BY n DESC
        """).fetchall()
        result['failure_taxonomy'] = [
            {
                'archetype': row['failure_archetype'],
                'count': row['n'],
                'avg_severity': round(_safe(row['avg_severity']), 3),
                'last_seen': str(row['last_seen'])
            }
            for row in taxonomy
        ]
    except Exception:
        result['failure_taxonomy'] = []
        result['failure_note'] = 'Run failure_memory_engine.py build_full first'

    # Recent failures / reconstruction
    try:
        recent = db.execute("""
            SELECT symbol, failure_class, regime, severity_score, lesson_key
            FROM failure_reconstruction
            ORDER BY rowid DESC LIMIT 10
        """).fetchall()
        result['recent_failures'] = [dict(zip(r.keys(), tuple(r))) for r in recent]
    except Exception:
        result['recent_failures'] = []

    # High-risk symbols
    try:
        high_risk = db.execute("""
            SELECT symbol, failure_class, COUNT(*) as n_failures,
                   AVG(severity_score) as avg_sev
            FROM failure_reconstruction
            GROUP BY symbol, failure_class
            HAVING n_failures > 2
            ORDER BY avg_sev DESC LIMIT 10
        """).fetchall()
        result['high_risk_symbols'] = [
            {
                'symbol': row['symbol'],
                'failure_class': row['failure_class'],
                'n_failures': row['n_failures'],
                'avg_severity': round(_safe(row['avg_sev']), 3)
            }
            for row in high_risk
        ]
    except Exception:
        result['high_risk_symbols'] = []

    # Lessons
    try:
        lessons = db.execute("""
            SELECT lesson_key, lesson_text, impact_score, created_at
            FROM failure_lessons
            ORDER BY impact_score DESC LIMIT 5
        """).fetchall()
        result['top_lessons'] = [dict(zip(r.keys(), tuple(r))) for r in lessons]
    except Exception:
        result['top_lessons'] = []

    result['signals'] = len(result['recent_failures'])
    return result


# ---------------------------------------------------------------------------
# Section 5: Explosion Watch — readiness scores, top candidates
# ---------------------------------------------------------------------------

def _section_explosion_watch(db, target_date):
    """Top explosion readiness candidates, archetype matches."""
    result = {
        'section': '5_explosion_watch',
        'title': 'رادار الانفجار (Explosion Watch)',
    }

    # Latest readiness scores
    try:
        latest_date = db.execute(
            "SELECT MAX(date) as d FROM explosion_readiness"
        ).fetchone()
        readiness_date = latest_date['d'] if latest_date and latest_date['d'] else None

        if readiness_date:
            candidates = db.execute("""
                SELECT er.symbol, er.readiness_score, er.compression_index,
                       er.structural_energy, er.contagion_alignment,
                       er.matching_archetype, er.expected_failure_mode,
                       su.sector
                FROM explosion_readiness er
                LEFT JOIN stock_universe su ON er.symbol = su.symbol
                WHERE er.date = ?
                ORDER BY er.readiness_score DESC LIMIT 15
            """, (readiness_date,)).fetchall()
            result['top_candidates'] = [dict(zip(r.keys(), tuple(r))) for r in candidates]
            result['readiness_date'] = readiness_date

            # Score distribution
            all_scores = db.execute(
                "SELECT readiness_score FROM explosion_readiness WHERE date=?",
                (readiness_date,)
            ).fetchall()
            scores = [_safe(r['readiness_score']) for r in all_scores]
            if scores:
                result['score_stats'] = {
                    'count': len(scores),
                    'max': round(max(scores), 1),
                    'avg': round(sum(scores) / len(scores), 1),
                    'above_70': sum(1 for s in scores if s >= 70),
                    'above_50': sum(1 for s in scores if s >= 50),
                }
        else:
            result['top_candidates'] = []
            result['note'] = 'No readiness data — run explosion_physics_engine.py compute_readiness'
    except Exception as e:
        result['top_candidates'] = []
        result['explosion_error'] = str(e)

    # Archetype distribution from explosive moves
    try:
        archetypes = db.execute("""
            SELECT explosion_class, COUNT(*) as n,
                   AVG(return_1d) as avg_r1d,
                   AVG(return_5d) as avg_r5d
            FROM explosive_moves
            GROUP BY explosion_class
            ORDER BY n DESC LIMIT 6
        """).fetchall()
        result['archetype_distribution'] = [
            {
                'class': row['explosion_class'],
                'count': row['n'],
                'avg_return_1d': round(_safe(row['avg_r1d']), 2),
                'avg_return_5d': round(_safe(row['avg_r5d']), 2),
            }
            for row in archetypes
        ]
    except Exception:
        result['archetype_distribution'] = []

    # False explosion anatomy summary
    try:
        false_breakdown = db.execute("""
            SELECT COUNT(*) as n_total,
                   SUM(CASE WHEN return_5d < return_1d/2 AND return_1d > 5 THEN 1 ELSE 0 END) as n_false
            FROM explosive_moves
            WHERE return_1d > 5
        """).fetchone()
        if false_breakdown and false_breakdown['n_total'] > 0:
            result['false_breakout_rate'] = round(
                _safe(false_breakdown['n_false']) / _safe(false_breakdown['n_total']) * 100, 1
            )
    except Exception:
        pass

    result['signals'] = len(result.get('top_candidates', []))
    return result


# ---------------------------------------------------------------------------
# Section 6: DNA Mutations — stock and sector DNA changes
# ---------------------------------------------------------------------------

def _section_dna_mutations(db, target_date):
    """DNA classifications, archetype distribution, recent mutations."""
    result = {
        'section': '6_dna_mutations',
        'title': 'طفرات الحمض النووي (DNA Mutations)',
    }

    # Stock DNA archetype distribution
    try:
        stock_dna_dist = db.execute("""
            SELECT archetype, COUNT(*) as n,
                   AVG(explosion_rate_pct) as avg_expl_rate,
                   AVG(false_breakout_rate_pct) as avg_fbr
            FROM stock_dna
            GROUP BY archetype
            ORDER BY n DESC
        """).fetchall()
        result['stock_dna_distribution'] = [
            {
                'archetype': row['archetype'],
                'count': row['n'],
                'avg_explosion_rate': round(_safe(row['avg_expl_rate']), 1),
                'avg_false_breakout_rate': round(_safe(row['avg_fbr']), 1),
            }
            for row in stock_dna_dist
        ]
    except Exception:
        result['stock_dna_distribution'] = []
        result['dna_note'] = 'Run market_dna_engine.py build_full first'

    # Sector DNA
    try:
        sector_dna_dist = db.execute("""
            SELECT archetype, COUNT(*) as n,
                   AVG(avg_sector_return_5d) as avg_ret5d
            FROM sector_dna
            GROUP BY archetype
            ORDER BY n DESC
        """).fetchall()
        result['sector_dna_distribution'] = [
            {
                'archetype': row['archetype'],
                'count': row['n'],
                'avg_return_5d': round(_safe(row['avg_ret5d']), 2),
            }
            for row in sector_dna_dist
        ]
    except Exception:
        result['sector_dna_distribution'] = []

    # DNA mutations / drift from adaptive research
    try:
        mutations = db.execute("""
            SELECT symbol, mutation_type, from_archetype, to_archetype,
                   mutation_date, trigger_event
            FROM dna_mutations
            ORDER BY mutation_date DESC LIMIT 10
        """).fetchall()
        result['recent_mutations'] = [dict(zip(r.keys(), tuple(r))) for r in mutations]
    except Exception:
        result['recent_mutations'] = []

    # Top explosive stocks by DNA
    try:
        top_explosive = db.execute("""
            SELECT symbol, archetype, explosion_rate_pct, false_breakout_rate_pct,
                   avg_return_5d, hurst_approx, liquidity_score
            FROM stock_dna
            WHERE archetype IN ('EXPLOSIVE_STEADY', 'MOMENTUM_LEADER')
            ORDER BY explosion_rate_pct DESC LIMIT 10
        """).fetchall()
        result['top_explosive_stocks'] = [dict(zip(r.keys(), tuple(r))) for r in top_explosive]
    except Exception:
        result['top_explosive_stocks'] = []

    result['signals'] = len(result['recent_mutations'])
    return result


# ---------------------------------------------------------------------------
# Section 7: Graph Intelligence — UMCG centrality, communities, fragility
# ---------------------------------------------------------------------------

def _section_graph_intel(db, target_date):
    """UMCG graph metrics, key nodes, community structure."""
    result = {
        'section': '7_graph_intel',
        'title': 'ذكاء الشبكة (Graph Intelligence)',
    }

    # Top nodes by pagerank
    try:
        top_nodes = db.execute("""
            SELECT node_id, node_type, name, pagerank, betweenness,
                   community_id, is_fragility_hub
            FROM umcg_nodes
            ORDER BY pagerank DESC LIMIT 10
        """).fetchall()
        result['top_nodes_by_pagerank'] = [dict(zip(r.keys(), tuple(r))) for r in top_nodes]
    except Exception:
        result['top_nodes_by_pagerank'] = []
        result['graph_note'] = 'Run unified_market_graph.py build_full first'

    # Fragility hubs
    try:
        fragility = db.execute("""
            SELECT node_id, node_type, name, pagerank, betweenness
            FROM umcg_nodes
            WHERE is_fragility_hub = 1
            ORDER BY betweenness DESC LIMIT 5
        """).fetchall()
        result['fragility_hubs'] = [dict(zip(r.keys(), tuple(r))) for r in fragility]
    except Exception:
        result['fragility_hubs'] = []

    # Community summary
    try:
        communities = db.execute("""
            SELECT community_id, COUNT(*) as n_members,
                   GROUP_CONCAT(node_type) as types,
                   AVG(pagerank) as avg_pagerank
            FROM umcg_nodes
            WHERE community_id IS NOT NULL
            GROUP BY community_id
            ORDER BY n_members DESC LIMIT 6
        """).fetchall()
        result['community_structure'] = [
            {
                'community_id': row['community_id'],
                'n_members': row['n_members'],
                'avg_pagerank': round(_safe(row['avg_pagerank']), 6),
            }
            for row in communities
        ]
    except Exception:
        result['community_structure'] = []

    # Latest snapshot
    try:
        snapshot = db.execute(
            "SELECT * FROM umcg_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if snapshot:
            cols = snapshot.keys()
            snap_dict = dict(zip(cols, tuple(snapshot)))
            # Only include non-null fields
            result['latest_snapshot'] = {
                k: v for k, v in snap_dict.items() if v is not None
            }
    except Exception:
        result['latest_snapshot'] = {}

    # Edge type distribution
    try:
        edges = db.execute("""
            SELECT edge_type, COUNT(*) as n, AVG(weight) as avg_weight
            FROM umcg_edges
            GROUP BY edge_type
            ORDER BY n DESC
        """).fetchall()
        result['edge_distribution'] = [
            {'type': row['edge_type'], 'count': row['n'],
             'avg_weight': round(_safe(row['avg_weight']), 4)}
            for row in edges
        ]
    except Exception:
        result['edge_distribution'] = []

    result['signals'] = len(result['top_nodes_by_pagerank'])
    return result


# ---------------------------------------------------------------------------
# Section 8: Execution Reality — friction-adjusted analysis
# ---------------------------------------------------------------------------

def _section_execution_reality(db, target_date):
    """EGX-specific execution friction, liquidity tiers, feasibility."""
    result = {
        'section': '8_execution_reality',
        'title': 'واقع التنفيذ (Execution Reality)',
    }

    # Liquidity profiles summary
    try:
        tiers = db.execute("""
            SELECT liquidity_tier, COUNT(*) as n,
                   AVG(avg_daily_volume) as avg_vol,
                   AVG(avg_spread_est_bps) as avg_spread
            FROM symbol_liquidity_profile
            GROUP BY liquidity_tier
            ORDER BY avg_vol DESC
        """).fetchall()
        result['liquidity_tier_distribution'] = [
            {
                'tier': row['liquidity_tier'],
                'count': row['n'],
                'avg_daily_volume': round(_safe(row['avg_vol'])),
                'avg_spread_bps': round(_safe(row['avg_spread']), 1),
            }
            for row in tiers
        ]
    except Exception:
        result['liquidity_tier_distribution'] = []
        result['execution_note'] = 'Run execution_reality_engine.py build_liquidity_profiles first'

    # Latest execution adjustments
    try:
        adj_date = db.execute(
            "SELECT MAX(date) as d FROM execution_adjustments"
        ).fetchone()
        adj_d = adj_date['d'] if adj_date and adj_date['d'] else None

        if adj_d:
            # Feasibility summary
            feasibility = db.execute("""
                SELECT execution_feasibility, COUNT(*) as n,
                       AVG(total_friction_bps) as avg_friction,
                       AVG(realistic_return) as avg_realistic
                FROM execution_adjustments
                WHERE date = ?
                GROUP BY execution_feasibility
            """, (adj_d,)).fetchall()
            result['feasibility_summary'] = [
                {
                    'feasibility': row['execution_feasibility'],
                    'count': row['n'],
                    'avg_friction_bps': round(_safe(row['avg_friction']), 1),
                    'avg_realistic_return': round(_safe(row['avg_realistic']), 2),
                }
                for row in feasibility
            ]

            # Top feasible picks
            top_feasible = db.execute("""
                SELECT symbol, realistic_return, total_friction_bps,
                       execution_feasibility, liquidity_score
                FROM execution_adjustments
                WHERE date = ? AND execution_feasibility = 'FEASIBLE'
                ORDER BY realistic_return DESC LIMIT 10
            """, (adj_d,)).fetchall()
            result['top_feasible_picks'] = [dict(zip(r.keys(), tuple(r))) for r in top_feasible]
        else:
            result['feasibility_summary'] = []
            result['top_feasible_picks'] = []
    except Exception:
        result['feasibility_summary'] = []
        result['top_feasible_picks'] = []

    # EGX parameters reminder
    result['egx_params'] = {
        'typical_spread_bps': 50,
        'circuit_breaker_pct': '10%',
        'settlement': 'T+3',
        'trading_hours': '10:00-15:30',
        'commission_bps': 50,
    }

    result['signals'] = len(result.get('top_feasible_picks', []))
    return result


# ---------------------------------------------------------------------------
# Section 9: Research Directives — auto-generated priorities
# ---------------------------------------------------------------------------

def _section_research_directives(db, target_date):
    """Auto-generated research priorities based on system state."""
    result = {
        'section': '9_research_directs',
        'title': 'التوجيهات البحثية (Research Directives)',
    }

    directives = []

    # Check OHLCV coverage
    try:
        ohlcv_stats = db.execute("""
            SELECT COUNT(DISTINCT symbol) as n_symbols,
                   AVG(bar_count) as avg_bars,
                   MIN(bar_count) as min_bars
            FROM (
                SELECT symbol, COUNT(*) as bar_count
                FROM ohlcv_history
                GROUP BY symbol
            )
        """).fetchone()
        if ohlcv_stats and _safe(ohlcv_stats['avg_bars']) < 400:
            directives.append({
                'priority': 1,
                'type': 'DATA_QUALITY',
                'title': 'Expand OHLCV historical data',
                'description': f"Average bars: {round(_safe(ohlcv_stats['avg_bars']))} — run daily_update.mjs --historical to get 500 bars per symbol",
                'status': 'CRITICAL',
                'impact': 'HIGH'
            })
    except Exception:
        pass

    # Check law health
    try:
        law_health = db.execute("""
            SELECT AVG(precision) as avg_prec,
                   SUM(CASE WHEN law_status='DEGRADING' THEN 1 ELSE 0 END) as n_degrading,
                   COUNT(*) as n_total
            FROM universal_laws_p16
        """).fetchone()
        avg_prec = _safe(law_health['avg_prec']) if law_health else 0
        n_degrading = _safeint(law_health['n_degrading']) if law_health else 0
        if avg_prec < 0.15:
            directives.append({
                'priority': 2,
                'type': 'LAW_DEGRADATION',
                'title': 'Universal laws below baseline',
                'description': f"Avg precision: {round(avg_prec*100,1)}% (random baseline: 18.2%). Run adaptive_research_loop.py run_evolution_cycle",
                'status': 'URGENT',
                'impact': 'HIGH'
            })
    except Exception:
        pass

    # Check DNA classification diversity
    try:
        dna_diversity = db.execute("""
            SELECT COUNT(DISTINCT archetype) as n_archetypes,
                   COUNT(*) as n_total
            FROM stock_dna
        """).fetchone()
        if dna_diversity:
            n_arch = _safeint(dna_diversity['n_archetypes'])
            n_total = _safeint(dna_diversity['n_total'])
            if n_arch <= 2 and n_total > 50:
                directives.append({
                    'priority': 3,
                    'type': 'DNA_HOMOGENEITY',
                    'title': 'Stock DNA lacks archetype diversity',
                    'description': f"Only {n_arch} archetypes for {n_total} stocks. Run market_dna_engine.py build_full to recalibrate with relative percentile ranking",
                    'status': 'OPEN',
                    'impact': 'MEDIUM'
                })
    except Exception:
        pass

    # Check causal chains
    try:
        n_chains = db.execute("SELECT COUNT(*) as n FROM causal_chains").fetchone()
        if n_chains and _safeint(n_chains['n']) == 0:
            directives.append({
                'priority': 4,
                'type': 'CAUSAL_DISCOVERY',
                'title': 'No causal chains discovered yet',
                'description': 'Run causal_discovery_engine.py build_full to discover sector lead-lag relationships',
                'status': 'OPEN',
                'impact': 'MEDIUM'
            })
    except Exception:
        directives.append({
            'priority': 4,
            'type': 'CAUSAL_DISCOVERY',
            'title': 'Causal chains table missing',
            'description': 'Run causal_discovery_engine.py build_full',
            'status': 'OPEN',
            'impact': 'MEDIUM'
        })

    # Check UMCG nodes
    try:
        n_umcg = db.execute("SELECT COUNT(*) as n FROM umcg_nodes").fetchone()
        if n_umcg and _safeint(n_umcg['n']) == 0:
            directives.append({
                'priority': 5,
                'type': 'GRAPH_BUILD',
                'title': 'UMCG graph is empty',
                'description': 'Run unified_market_graph.py build_full to build the market cognition graph',
                'status': 'OPEN',
                'impact': 'MEDIUM'
            })
    except Exception:
        directives.append({
            'priority': 5,
            'type': 'GRAPH_BUILD',
            'title': 'UMCG not built',
            'description': 'Run unified_market_graph.py build_full',
            'status': 'OPEN',
            'impact': 'MEDIUM'
        })

    # Check explosion readiness
    try:
        n_readiness = db.execute("SELECT COUNT(*) as n FROM explosion_readiness").fetchone()
        if n_readiness and _safeint(n_readiness['n']) == 0:
            directives.append({
                'priority': 6,
                'type': 'EXPLOSION_WATCH',
                'title': 'No explosion readiness scores',
                'description': 'Run explosion_physics_engine.py compute_readiness for today',
                'status': 'OPEN',
                'impact': 'HIGH'
            })
    except Exception:
        directives.append({
            'priority': 6,
            'type': 'EXPLOSION_WATCH',
            'title': 'Explosion readiness missing',
            'description': 'Run explosion_physics_engine.py compute_readiness',
            'status': 'OPEN',
            'impact': 'HIGH'
        })

    # Check liquidity profiles
    try:
        n_liq = db.execute("SELECT COUNT(*) as n FROM symbol_liquidity_profile").fetchone()
        if n_liq and _safeint(n_liq['n']) == 0:
            directives.append({
                'priority': 7,
                'type': 'EXECUTION_REALITY',
                'title': 'Liquidity profiles not built',
                'description': 'Run execution_reality_engine.py build_liquidity_profiles',
                'status': 'OPEN',
                'impact': 'MEDIUM'
            })
    except Exception:
        pass

    # Also load persisted directives from adaptive_research_loop
    try:
        persisted = db.execute("""
            SELECT priority, directive_type, title, description, status
            FROM research_directives
            WHERE status = 'OPEN'
            ORDER BY priority ASC LIMIT 10
        """).fetchall()
        existing_titles = {d['title'] for d in directives}
        for row in persisted:
            if row['title'] not in existing_titles:
                directives.append({
                    'priority': _safeint(row['priority']),
                    'type': str(row['directive_type']),
                    'title': str(row['title']),
                    'description': str(row['description']),
                    'status': str(row['status']),
                    'impact': 'MEDIUM'
                })
    except Exception:
        pass

    directives.sort(key=lambda x: x['priority'])
    result['directives'] = directives
    result['n_open'] = len([d for d in directives if d.get('status') in ('OPEN', 'CRITICAL', 'URGENT')])
    result['n_critical'] = len([d for d in directives if d.get('status') == 'CRITICAL'])
    result['signals'] = len(directives)
    return result


# ---------------------------------------------------------------------------
# Build synthesis summary (narrative summary from sections)
# ---------------------------------------------------------------------------

def _build_summary(sections, target_date):
    """Build a 1-paragraph Arabic + English narrative summary."""
    regime = sections.get('1_market_state', {}).get('regime', {}).get('regime', 'UNKNOWN')
    n_candidates = len(sections.get('5_explosion_watch', {}).get('top_candidates', []))
    n_chains = len(sections.get('2_causal_narrative', {}).get('top_causal_chains', []))
    n_laws = len(sections.get('3_law_evolution', {}).get('top_laws', []))
    n_directives = sections.get('9_research_directs', {}).get('n_open', 0)
    n_failures = len(sections.get('4_failure_intel', {}).get('recent_failures', []))
    n_feasible = len(sections.get('8_execution_reality', {}).get('top_feasible_picks', []))

    top_candidate = None
    candidates = sections.get('5_explosion_watch', {}).get('top_candidates', [])
    if candidates:
        top_candidate = candidates[0].get('symbol', 'N/A')
        top_score = candidates[0].get('readiness_score', 0)
    else:
        top_score = 0

    summary = {
        'date': target_date,
        'regime': regime,
        'market_state': f"Regime: {regime}",
        'top_explosion_candidate': top_candidate,
        'top_explosion_score': round(top_score, 1),
        'n_causal_chains': n_chains,
        'n_active_laws': n_laws,
        'n_open_directives': n_directives,
        'n_recent_failures': n_failures,
        'n_feasible_picks': n_feasible,
        'narrative_ar': (
            f"تقرير {target_date} — النظام في وضعية {regime}. "
            f"عدد المرشحين للانفجار: {n_candidates} سهم. "
            f"أعلى مرشح: {top_candidate or 'غير متاح'} (درجة: {round(top_score, 1)}). "
            f"السلاسل السببية النشطة: {n_chains}. "
            f"القوانين الفعّالة: {n_laws}. "
            f"التوجيهات البحثية المفتوحة: {n_directives}."
        ),
        'narrative_en': (
            f"Report {target_date} — System in {regime} regime. "
            f"Explosion candidates: {n_candidates} stocks. "
            f"Top candidate: {top_candidate or 'N/A'} (score: {round(top_score, 1)}). "
            f"Active causal chains: {n_chains}. "
            f"Active laws: {n_laws}. "
            f"Open research directives: {n_directives}."
        )
    }
    return summary


# ---------------------------------------------------------------------------
# Command: synthesize
# ---------------------------------------------------------------------------

def cmd_synthesize(params):
    target_date = params.get('date', _today())
    t0 = time.time()

    db = get_db()
    try:
        report_id = _report_id(target_date)
        created_at = datetime.utcnow().isoformat()

        sections_data = {}
        section_fns = [
            ('1_market_state',     '1. Market State',         _section_market_state),
            ('2_causal_narrative', '2. Causal Narrative',     _section_causal_narrative),
            ('3_law_evolution',    '3. Law Evolution',        _section_law_evolution),
            ('4_failure_intel',    '4. Failure Intelligence', _section_failure_intel),
            ('5_explosion_watch',  '5. Explosion Watch',      _section_explosion_watch),
            ('6_dna_mutations',    '6. DNA Mutations',        _section_dna_mutations),
            ('7_graph_intel',      '7. Graph Intelligence',   _section_graph_intel),
            ('8_execution_reality','8. Execution Reality',    _section_execution_reality),
            ('9_research_directs', '9. Research Directives',  _section_research_directives),
        ]

        section_errors = []
        for section_key, section_name, fn in section_fns:
            try:
                sec_data = fn(db, target_date)
                sections_data[section_key] = sec_data
                sig_count = sec_data.get('signals', 0)
                quality = min(1.0, sig_count / 10.0) if sig_count else 0.0

                db.execute("""
                    INSERT OR REPLACE INTO synthesis_sections
                    (report_id, section_key, section_name, data_json, signal_count, quality_score)
                    VALUES (?,?,?,?,?,?)
                """, (report_id, section_key, section_name,
                      json.dumps(sec_data, default=str), sig_count, quality))
            except Exception as e:
                section_errors.append({'section': section_key, 'error': str(e)})
                sections_data[section_key] = {'error': str(e), 'signals': 0}

        # Build summary
        summary = _build_summary(sections_data, target_date)

        # Save report
        top_candidates = [
            c.get('symbol') for c in
            sections_data.get('5_explosion_watch', {}).get('top_candidates', [])[:5]
        ]
        key_risks = [
            d.get('title') for d in
            sections_data.get('9_research_directs', {}).get('directives', [])[:3]
            if d.get('status') in ('CRITICAL', 'URGENT')
        ]

        full_data = {
            'report_id': report_id,
            'date': target_date,
            'summary': summary,
            'sections': sections_data,
        }

        db.execute("""
            INSERT OR REPLACE INTO synthesis_reports
            (report_id, date, created_at, duration_s, n_sections,
             market_state_summary, explosion_count, top_candidates,
             key_risks, full_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            report_id, target_date, created_at,
            round(time.time() - t0, 2),
            len(section_fns),
            summary.get('market_state', ''),
            len(sections_data.get('5_explosion_watch', {}).get('top_candidates', [])),
            json.dumps(top_candidates),
            json.dumps(key_risks),
            json.dumps(full_data, default=str)
        ))
        db.commit()

        duration = round(time.time() - t0, 2)
        return {
            'success': True,
            'report_id': report_id,
            'date': target_date,
            'duration_s': duration,
            'n_sections': len(section_fns),
            'summary': summary,
            'section_errors': section_errors,
            'top_candidates': top_candidates[:5],
            'key_risks': key_risks,
        }

    except Exception as e:
        import traceback
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: get_last_report
# ---------------------------------------------------------------------------

def cmd_get_last_report(params):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM synthesis_reports ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {'success': False, 'error': 'No synthesis reports found. Run synthesize first.'}

        cols = row.keys()
        report = dict(zip(cols, tuple(row)))
        # Deserialize full_json
        try:
            report['full_data'] = json.loads(report.get('full_json', '{}'))
        except Exception:
            report['full_data'] = {}
        del report['full_json']
        return {'success': True, 'report': report}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: daily_brief (formatted text)
# ---------------------------------------------------------------------------

def cmd_daily_brief(params):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM synthesis_reports ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {'success': False, 'error': 'No synthesis reports. Run synthesize first.'}

        # Get sections
        sections = db.execute(
            "SELECT section_key, section_name, signal_count, data_json "
            "FROM synthesis_sections WHERE report_id=? ORDER BY section_key",
            (row['report_id'],)
        ).fetchall()

        lines = [
            f"{'='*60}",
            f"📊 تقرير الذكاء اليومي — EGX Intelligence Daily Brief",
            f"📅 Date: {row['date']}  |  ⏱ Generated: {row['created_at'][:16]}",
            f"{'='*60}",
            f"",
        ]

        for sec in sections:
            sec_name = sec['section_name'] or sec['section_key']
            sig_count = sec['signal_count'] or 0
            lines.append(f"▶ {sec_name}  [{sig_count} signals]")
            try:
                data = json.loads(sec['data_json'] or '{}')
                # Show key data per section
                sec_key = sec['section_key']
                if sec_key == '1_market_state':
                    reg = data.get('regime', {})
                    lines.append(f"   Regime: {reg.get('regime', 'UNKNOWN')}")
                    breadth = data.get('breadth', {})
                    if 'advancing' in breadth:
                        lines.append(f"   Breadth: +{breadth.get('advancing',0)} / -{breadth.get('declining',0)}")
                elif sec_key == '2_causal_narrative':
                    chains = data.get('top_causal_chains', [])
                    for c in chains[:3]:
                        lines.append(f"   {c.get('source_entity','?')} → {c.get('target_entity','?')} (lag {c.get('lag_days','?')}d, r={c.get('strength','?')})")
                elif sec_key == '3_law_evolution':
                    dist = data.get('law_status_distribution', [])
                    for d in dist:
                        lines.append(f"   {d['status']}: {d['count']} laws (avg prec: {d['avg_precision']:.2%})")
                elif sec_key == '4_failure_intel':
                    tax = data.get('failure_taxonomy', [])
                    for t in tax[:3]:
                        lines.append(f"   {t['archetype']}: {t['count']} events (sev: {t['avg_severity']:.2f})")
                elif sec_key == '5_explosion_watch':
                    candidates = data.get('top_candidates', [])
                    for c in candidates[:5]:
                        lines.append(f"   🔥 {c.get('symbol','?')} | Score: {c.get('readiness_score',0):.1f} | {c.get('matching_archetype','?')} | {c.get('sector','?')}")
                elif sec_key == '6_dna_mutations':
                    dist = data.get('stock_dna_distribution', [])
                    for d in dist[:4]:
                        lines.append(f"   {d['archetype']}: {d['count']} stocks (expl rate: {d['avg_explosion_rate']:.1f}%)")
                elif sec_key == '7_graph_intel':
                    top = data.get('top_nodes_by_pagerank', [])
                    for n in top[:3]:
                        lines.append(f"   🔵 {n.get('name','?')} [{n.get('node_type','?')}] PageRank: {n.get('pagerank',0):.5f}")
                elif sec_key == '8_execution_reality':
                    tiers = data.get('liquidity_tier_distribution', [])
                    for t in tiers[:3]:
                        lines.append(f"   {t['tier']}: {t['count']} stocks (spread: {t['avg_spread_bps']:.0f}bps)")
                elif sec_key == '9_research_directs':
                    directives = data.get('directives', [])
                    for d in directives[:3]:
                        icon = '🚨' if d.get('status') == 'CRITICAL' else '⚠️' if d.get('status') == 'URGENT' else '📌'
                        lines.append(f"   {icon} [{d.get('type','?')}] {d.get('title','?')}")
            except Exception:
                pass
            lines.append("")

        lines.append(f"{'='*60}")
        lines.append(f"Total report size: {row['n_sections']} sections")

        return {
            'success': True,
            'date': row['date'],
            'brief_text': '\n'.join(lines)
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: get_section
# ---------------------------------------------------------------------------

def cmd_get_section(params):
    section_key = params.get('section', '5_explosion_watch')
    db = get_db()
    try:
        # Get latest report_id
        report = db.execute(
            "SELECT report_id FROM synthesis_reports ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not report:
            return {'success': False, 'error': 'No synthesis reports. Run synthesize first.'}

        sec = db.execute(
            "SELECT * FROM synthesis_sections WHERE report_id=? AND section_key=?",
            (report['report_id'], section_key)
        ).fetchone()
        if not sec:
            return {'success': False, 'error': f'Section {section_key} not found in last report.'}

        cols = sec.keys()
        sec_dict = dict(zip(cols, tuple(sec)))
        try:
            sec_dict['data'] = json.loads(sec_dict.get('data_json', '{}'))
        except Exception:
            sec_dict['data'] = {}
        del sec_dict['data_json']
        return {'success': True, 'section': sec_dict}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: status — check which data sources are available
# ---------------------------------------------------------------------------

def cmd_status(params):
    db = get_db()
    try:
        checks = {}

        table_checks = [
            ('ohlcv_history',          'OHLCV data'),
            ('stock_universe',          'Stock universe'),
            ('universal_laws_p16',      'Universal laws (P16)'),
            ('explosion_readiness',     'Explosion readiness'),
            ('causal_chains',           'Causal chains'),
            ('umcg_nodes',              'UMCG graph nodes'),
            ('failure_taxonomy',        'Failure taxonomy'),
            ('stock_dna',               'Stock DNA'),
            ('sector_dna',              'Sector DNA'),
            ('symbol_liquidity_profile','Liquidity profiles'),
            ('data_integrity',          'Data integrity scores'),
            ('global_macro',            'Global macro'),
            ('research_directives',     'Research directives'),
            ('dna_mutations',           'DNA mutations'),
            ('law_lineage',             'Law lineage'),
            ('synthesis_reports',       'Synthesis reports'),
        ]

        for table, label in table_checks:
            try:
                row = db.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
                n = _safeint(row['n']) if row else 0
                checks[table] = {'label': label, 'n_rows': n, 'available': n > 0}
            except Exception:
                checks[table] = {'label': label, 'n_rows': 0, 'available': False}

        available = sum(1 for c in checks.values() if c['available'])
        total = len(checks)

        last_report = db.execute(
            "SELECT date, created_at FROM synthesis_reports ORDER BY date DESC LIMIT 1"
        ).fetchone()

        return {
            'success': True,
            'data_sources': checks,
            'n_available': available,
            'n_total': total,
            'readiness_pct': round(available / total * 100, 1),
            'last_synthesis': {
                'date': str(last_report['date']) if last_report else None,
                'created_at': str(last_report['created_at']) if last_report else None,
            }
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'synthesize':      cmd_synthesize,
    'build_synthesis': cmd_synthesize,
    'daily_brief':     cmd_daily_brief,
    'get_last_report': cmd_get_last_report,
    'get_section':     cmd_get_section,
    'status':          cmd_status,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            'error': 'Usage: unified_daily_synthesis.py <command> [json_params]',
            'available_commands': list(COMMANDS.keys())
        }))
        sys.exit(1)

    command = sys.argv[1]
    params = {}
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({'error': f'Invalid JSON params: {e}'}))
            sys.exit(1)

    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys())
        }))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }, default=str))
        sys.exit(1)


if __name__ == '__main__':
    main()
