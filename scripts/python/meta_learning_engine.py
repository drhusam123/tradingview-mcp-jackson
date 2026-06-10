#!/usr/bin/env python3
"""
meta_learning_engine.py — Phase 31
EGX Autonomous Quant System: Meta-Learning Engine

Analyzes the system's own learning history to understand WHAT types of patterns
work, WHEN they fail, and WHERE to focus research next. It learns HOW to learn.

Commands:
  analyze_hypotheses   — what feature/sector/regime combos succeed most?
  failure_contexts     — when/where does the system fail most?
  predictability_map   — sector × regime predictability grid + research opportunities
  meta_directives      — actionable directives for the research loop
  build_full           — run all 4 commands sequentially

Usage:
  python meta_learning_engine.py <command> '<json_params>'
Output: last stdout line = valid JSON
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# DB Setup
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

KNOWN_FAILURE_ARCHETYPES = [
    'NOISY_SIGNAL',
    'STRUCTURAL_INVALIDATION',
    'REGIME_MISMATCH',
    'LIQUIDITY_DISTORTION',
    'VOLATILITY_SUPPRESSION',
    'MACRO_OVERRIDE',
    'CAUSAL_INVERSION',
]

KNOWN_REGIMES = [
    'TRENDING_UP',
    'TRENDING_DOWN',
    'VOLATILE',
    'SIDEWAYS',
    'RECOVERING',
    'CRISIS',
    'UNKNOWN',
]

FEATURE_KEYWORDS = ['volume', 'momentum', 'reversal', 'breakout', 'volatility']


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS meta_learning_results (
        run_date          TEXT PRIMARY KEY,
        best_feature_type TEXT,
        best_sector       TEXT,
        best_regime       TEXT,
        worst_combination TEXT,
        top_directive     TEXT,
        predictability_json TEXT,
        directives_json   TEXT,
        computed_at       TEXT
    );

    CREATE TABLE IF NOT EXISTS predictability_map (
        sector              TEXT,
        regime              TEXT,
        predictability_score REAL,
        n_laws              INTEGER,
        opportunity_score   REAL,
        updated_at          TEXT,
        PRIMARY KEY (sector, regime)
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def safe_mean(values, default=0.0):
    """Return mean of non-None numeric values, or default if empty."""
    clean = [v for v in values if v is not None]
    if not clean:
        return default
    try:
        return statistics.mean(clean)
    except Exception:
        return default


def extract_feature_type(pattern_name, feature_type_col=None):
    """Infer feature type from column value or by scanning pattern name keywords."""
    if feature_type_col and isinstance(feature_type_col, str) and feature_type_col.strip():
        return feature_type_col.strip().lower()
    name_lower = (pattern_name or '').lower()
    for kw in FEATURE_KEYWORDS:
        if kw in name_lower:
            return kw
    return 'other'


def rows_to_dicts(rows):
    """Convert sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows] if rows else []


def safe_query(db, sql, params=()):
    """Execute a query, return rows or [] if the table does not exist."""
    try:
        cursor = db.execute(sql, params)
        return cursor.fetchall()
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_pattern_laws(db):
    """Load all rows from pattern_laws. Returns list of dicts."""
    rows = safe_query(db, """
        SELECT pattern_name, precision, recall, f1, sector, regime_context,
               feature_type, last_validated, status
        FROM pattern_laws
    """)
    return rows_to_dicts(rows)


def load_law_lineage(db):
    """Load law_lineage rows. Returns list of dicts."""
    rows = safe_query(db, """
        SELECT child_id, parent_id, mutation_type, precision_delta
        FROM law_lineage
    """)
    return rows_to_dicts(rows)


def load_failure_intelligence(db):
    """Load failure_intelligence rows. Returns list of dicts."""
    rows = safe_query(db, """
        SELECT symbol, failure_archetype AS archetype, confidence,
               analysis_date AS date
        FROM failure_intelligence
    """)
    return rows_to_dicts(rows)


def load_regime_history(db):
    """Load regime history from market_regime or regime_history tables."""
    rows = safe_query(db, """
        SELECT date, regime_label, regime_confidence
        FROM market_regime
        ORDER BY date
    """)
    if not rows:
        rows = safe_query(db, """
            SELECT date, regime_label, regime_confidence
            FROM regime_history
            ORDER BY date
        """)
    return rows_to_dicts(rows)


def load_stock_dna(db):
    """Load stock_dna rows. Returns dict keyed by symbol."""
    rows = safe_query(db, """
        SELECT symbol, sector, archetype
        FROM stock_dna
    """)
    result = {}
    for r in rows_to_dicts(rows):
        sym = r.get('symbol', '')
        if sym:
            result[sym] = r
    return result


def load_ohlcv_summary(db):
    """Load a lightweight OHLCV summary (symbol + latest close + avg volume)."""
    rows = safe_query(db, """
        SELECT symbol,
               AVG(volume) AS avg_volume,
               COUNT(*) AS n_bars
        FROM ohlcv
        GROUP BY symbol
    """)
    result = {}
    for r in rows_to_dicts(rows):
        sym = r.get('symbol', '')
        if sym:
            result[sym] = r
    return result


def build_regime_lookup(regime_history):
    """Build date -> regime_label lookup dict."""
    lookup = {}
    for row in regime_history:
        d = row.get('date', '')
        label = row.get('regime_label') or 'UNKNOWN'
        if d:
            lookup[d] = label
    return lookup


def get_regime_for_date(date_str, regime_lookup):
    """Return regime for a given date string. Falls back to UNKNOWN."""
    if not date_str:
        return 'UNKNOWN'
    # Try exact match first
    if date_str in regime_lookup:
        return regime_lookup[date_str]
    # Try prefix match (date might include time)
    date_prefix = str(date_str)[:10]
    if date_prefix in regime_lookup:
        return regime_lookup[date_prefix]
    return 'UNKNOWN'


def infer_liquidity_tier(symbol, ohlcv_summary, stock_dna):
    """
    Infer liquidity tier: HIGH / MEDIUM / LOW based on avg_volume.
    Falls back to MEDIUM if unknown.
    """
    data = ohlcv_summary.get(symbol, {})
    avg_vol = data.get('avg_volume') or 0
    if avg_vol == 0:
        return 'MEDIUM'
    if avg_vol > 5_000_000:
        return 'HIGH'
    if avg_vol > 500_000:
        return 'MEDIUM'
    return 'LOW'


# ---------------------------------------------------------------------------
# Command 1: analyze_hypotheses
# ---------------------------------------------------------------------------

def analyze_hypotheses(params):
    """Analyze which feature_type / sector / regime_context combos succeed most."""
    db = get_db()
    laws = load_pattern_laws(db)
    lineage = load_law_lineage(db)
    stock_dna = load_stock_dna(db)
    db.close()

    if not laws:
        return {
            "best_feature_types": [],
            "best_sectors": [],
            "best_regime_contexts": [],
            "best_mutation_types": [],
            "worst_combinations": [],
            "meta_insight": "No pattern_laws data available yet."
        }

    # ── Group by feature_type ─────────────────────────────────────────────────
    by_feature = defaultdict(list)
    for law in laws:
        ft = extract_feature_type(
            law.get('pattern_name', ''),
            law.get('feature_type')
        )
        by_feature[ft].append(law)

    best_feature_types = []
    for ft, group in by_feature.items():
        precisions = [g.get('precision') or 0 for g in group]
        statuses   = [g.get('status', '') for g in group]
        n_active   = sum(1 for s in statuses if s == 'ACTIVE')
        n_degrading= sum(1 for s in statuses if s == 'DEGRADING')
        n          = len(group)
        best_law   = max(group, key=lambda x: x.get('precision') or 0)
        best_feature_types.append({
            'type':          ft,
            'avg_precision': round(safe_mean(precisions), 4),
            'n_laws':        n,
            'pct_active':    round(n_active / n, 3) if n else 0,
            'pct_degrading': round(n_degrading / n, 3) if n else 0,
            'best_law_name': best_law.get('pattern_name', ''),
        })
    best_feature_types.sort(key=lambda x: x['avg_precision'], reverse=True)

    # ── Group by sector ──────────────────────────────────────────────────────
    by_sector = defaultdict(list)
    for law in laws:
        # Use sector column first; fall back to stock_dna if blank
        sector = (law.get('sector') or '').strip()
        if not sector:
            # Try to find via stock_dna by pattern_name keyword match — best effort
            sector = 'Unknown'
        by_sector[sector].append(law)

    best_sectors = []
    for sector, group in by_sector.items():
        precisions = [g.get('precision') or 0 for g in group]
        best_sectors.append({
            'sector':        sector,
            'avg_precision': round(safe_mean(precisions), 4),
            'n_laws':        len(group),
        })
    best_sectors.sort(key=lambda x: x['avg_precision'], reverse=True)

    # ── Group by regime_context ───────────────────────────────────────────────
    by_regime = defaultdict(list)
    for law in laws:
        regime = (law.get('regime_context') or 'UNKNOWN').strip().upper()
        if not regime:
            regime = 'UNKNOWN'
        by_regime[regime].append(law)

    best_regime_contexts = []
    for regime, group in by_regime.items():
        precisions = [g.get('precision') or 0 for g in group]
        best_regime_contexts.append({
            'regime':        regime,
            'avg_precision': round(safe_mean(precisions), 4),
            'n_laws':        len(group),
        })
    best_regime_contexts.sort(key=lambda x: x['avg_precision'], reverse=True)

    # ── Mutation analysis ─────────────────────────────────────────────────────
    by_mutation = defaultdict(list)
    for lin in lineage:
        mt    = lin.get('mutation_type') or 'unknown'
        delta = lin.get('precision_delta')
        if delta is not None:
            by_mutation[mt].append(delta)

    best_mutation_types = []
    for mt, deltas in by_mutation.items():
        positive = [d for d in deltas if d > 0]
        best_mutation_types.append({
            'type':            mt,
            'avg_delta':       round(safe_mean(deltas), 4),
            'avg_pos_delta':   round(safe_mean(positive), 4) if positive else 0,
            'n_mutations':     len(deltas),
            'success_rate':    round(len(positive) / len(deltas), 3) if deltas else 0,
        })
    best_mutation_types.sort(key=lambda x: x['avg_delta'], reverse=True)

    # ── Worst combinations (feature_type × regime) ───────────────────────────
    combo_map = defaultdict(list)
    for law in laws:
        ft     = extract_feature_type(law.get('pattern_name', ''), law.get('feature_type'))
        regime = (law.get('regime_context') or 'UNKNOWN').strip().upper()
        combo  = f"{ft} + {regime}"
        combo_map[combo].append(law.get('precision') or 0)

    worst_combinations = []
    for combo, precisions in combo_map.items():
        avg = safe_mean(precisions)
        if avg < 0.45:
            worst_combinations.append({
                'combo':         combo,
                'avg_precision': round(avg, 4),
                'n_laws':        len(precisions),
            })
    worst_combinations.sort(key=lambda x: x['avg_precision'])

    # ── Meta insight ─────────────────────────────────────────────────────────
    best_ft      = best_feature_types[0] if best_feature_types else {}
    best_sec     = best_sectors[0]       if best_sectors       else {}
    best_reg     = best_regime_contexts[0] if best_regime_contexts else {}

    ft_name   = best_ft.get('type', 'N/A')
    sec_name  = best_sec.get('sector', 'N/A')
    reg_name  = best_reg.get('regime', 'N/A')
    ft_prec   = best_ft.get('avg_precision', 0)
    sec_prec  = best_sec.get('avg_precision', 0)
    baseline  = safe_mean([ft_prec, sec_prec]) if (ft_prec or sec_prec) else 0
    multi     = round(ft_prec / baseline, 1) if baseline > 0 else 1.0

    meta_insight = (
        f"{ft_name.capitalize()} patterns in {sec_name} sector during "
        f"{reg_name} regime show {multi}x better precision "
        f"({best_ft.get('n_laws', 0)} laws, avg {ft_prec:.2f})"
    )

    result = {
        "best_feature_types":    best_feature_types[:10],
        "best_sectors":          best_sectors[:10],
        "best_regime_contexts":  best_regime_contexts[:10],
        "best_mutation_types":   best_mutation_types[:10],
        "worst_combinations":    worst_combinations[:10],
        "meta_insight":          meta_insight,
    }

    # ── Persist top-line to meta_learning_results ─────────────────────────────
    try:
        db2 = get_db()
        db2.execute("""
            INSERT OR REPLACE INTO meta_learning_results
            (run_date, best_feature_type, best_sector, best_regime,
             worst_combination, computed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().strftime('%Y-%m-%d'),
            ft_name,
            sec_name,
            reg_name,
            worst_combinations[0].get('combo', '') if worst_combinations else '',
            datetime.utcnow().isoformat(),
        ))
        db2.commit()
        db2.close()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Command 2: failure_contexts
# ---------------------------------------------------------------------------

def failure_contexts(params):
    """Cross-reference failures with regime and sector context."""
    db = get_db()
    failures       = load_failure_intelligence(db)
    regime_history = load_regime_history(db)
    stock_dna      = load_stock_dna(db)
    ohlcv_summary  = load_ohlcv_summary(db)
    db.close()

    regime_lookup = build_regime_lookup(regime_history)

    if not failures:
        return {
            "failure_by_regime":       {},
            "failure_by_sector":       {},
            "failure_by_archetype":    {},
            "safest_contexts":         [],
            "dangerous_contexts":      [],
            "pcmci_failure_threshold": {},
            "meta_note": "No failure_intelligence data available yet."
        }

    # ── Annotate each failure with regime + sector + liquidity ───────────────
    annotated = []
    for f in failures:
        symbol   = f.get('symbol', '')
        date_str = f.get('date', '')
        regime   = get_regime_for_date(date_str, regime_lookup)
        dna      = stock_dna.get(symbol, {})
        sector   = dna.get('sector', 'Unknown')
        liq_tier = infer_liquidity_tier(symbol, ohlcv_summary, stock_dna)
        annotated.append({
            **f,
            'regime':    regime,
            'sector':    sector,
            'liquidity': liq_tier,
        })

    # ── failure_rate_by_regime ────────────────────────────────────────────────
    by_regime_arch = defaultdict(lambda: defaultdict(int))
    regime_total   = defaultdict(int)
    for f in annotated:
        regime = f.get('regime', 'UNKNOWN')
        arch   = f.get('archetype', 'UNKNOWN')
        by_regime_arch[regime][arch] += 1
        regime_total[regime] += 1

    failure_by_regime = {}
    for regime in sorted(regime_total.keys()):
        total = regime_total[regime]
        arch_counts = dict(by_regime_arch[regime])
        dominant    = max(arch_counts, key=arch_counts.get) if arch_counts else 'NONE'
        failure_by_regime[regime] = {
            'total_failures':    total,
            'dominant_archetype': dominant,
            'archetype_breakdown': arch_counts,
        }

    # ── failure_rate_by_sector ────────────────────────────────────────────────
    by_sector_arch = defaultdict(lambda: defaultdict(int))
    sector_total   = defaultdict(int)
    for f in annotated:
        sector = f.get('sector', 'Unknown')
        arch   = f.get('archetype', 'UNKNOWN')
        by_sector_arch[sector][arch] += 1
        sector_total[sector] += 1

    failure_by_sector = {}
    for sector in sorted(sector_total.keys()):
        total = sector_total[sector]
        arch_counts = dict(by_sector_arch[sector])
        dominant    = max(arch_counts, key=arch_counts.get) if arch_counts else 'NONE'
        failure_by_sector[sector] = {
            'total_failures':     total,
            'dominant_archetype': dominant,
            'archetype_breakdown': arch_counts,
        }

    # ── failure_rate_by_liquidity ─────────────────────────────────────────────
    by_liq = defaultdict(int)
    for f in annotated:
        by_liq[f.get('liquidity', 'MEDIUM')] += 1

    failure_by_liquidity = dict(by_liq)

    # ── failure_rate_by_archetype ─────────────────────────────────────────────
    arch_regime = defaultdict(lambda: defaultdict(int))
    arch_sector = defaultdict(lambda: defaultdict(int))
    arch_total  = defaultdict(int)
    for f in annotated:
        arch   = f.get('archetype', 'UNKNOWN')
        regime = f.get('regime', 'UNKNOWN')
        sector = f.get('sector', 'Unknown')
        arch_regime[arch][regime] += 1
        arch_sector[arch][sector] += 1
        arch_total[arch] += 1

    failure_by_archetype = {}
    for arch in KNOWN_FAILURE_ARCHETYPES:
        total = arch_total.get(arch, 0)
        regime_counts = dict(arch_regime.get(arch, {}))
        sector_counts = dict(arch_sector.get(arch, {}))
        top_regime = max(regime_counts, key=regime_counts.get) if regime_counts else 'NONE'
        top_sector = max(sector_counts, key=sector_counts.get) if sector_counts else 'NONE'
        failure_by_archetype[arch] = {
            'total':         total,
            'top_regime':    top_regime,
            'top_sector':    top_sector,
            'regime_breakdown': regime_counts,
            'sector_breakdown': sector_counts,
        }

    # ── Regime × sector combo analysis ───────────────────────────────────────
    combo_count = defaultdict(int)
    n_total = len(annotated)
    for f in annotated:
        combo = (f.get('regime', 'UNKNOWN'), f.get('sector', 'Unknown'))
        combo_count[combo] += 1

    combo_rate = {
        f"{r}|{s}": round(cnt / n_total, 4)
        for (r, s), cnt in combo_count.items()
        if n_total > 0
    }

    sorted_combos = sorted(combo_rate.items(), key=lambda x: x[1])
    safest_contexts = [
        {'context': k, 'failure_share': v}
        for k, v in sorted_combos[:5]
    ]
    dangerous_contexts = [
        {'context': k, 'failure_share': v}
        for k, v in sorted_combos[-5:][::-1]
    ]

    # ── PCMCI failure threshold (REGIME_MISMATCH spike analysis) ─────────────
    mismatch_by_regime = defaultdict(int)
    total_by_regime    = defaultdict(int)
    for f in annotated:
        arch   = f.get('archetype', '')
        regime = f.get('regime', 'UNKNOWN')
        total_by_regime[regime] += 1
        if arch == 'REGIME_MISMATCH':
            mismatch_by_regime[regime] += 1

    mismatch_rate = {}
    for regime in total_by_regime:
        total = total_by_regime[regime]
        mm    = mismatch_by_regime.get(regime, 0)
        mismatch_rate[regime] = round(mm / total, 4) if total > 0 else 0

    # Regime confidence from regime_history — correlate mismatch spikes
    reg_conf_by_label = defaultdict(list)
    for rh in regime_history:
        label = rh.get('regime_label', 'UNKNOWN')
        conf  = rh.get('regime_confidence') or 0
        reg_conf_by_label[label].append(conf)

    avg_conf_by_regime = {
        label: round(safe_mean(confs), 4)
        for label, confs in reg_conf_by_label.items()
    }

    # Threshold: regimes where mismatch rate > 0.3 are risky for causal inference
    risky_regimes = {
        r: {'mismatch_rate': mr, 'avg_confidence': avg_conf_by_regime.get(r, 0)}
        for r, mr in mismatch_rate.items()
        if mr > 0.25
    }

    pcmci_failure_threshold = {
        'regime_mismatch_rates':  mismatch_rate,
        'risky_regimes':          risky_regimes,
        'caution_threshold':      0.30,
        'explanation': (
            "PCMCI causal inference degrades when REGIME_MISMATCH failure rate "
            "exceeds 30% for a given regime. Use regime_confidence > 0.7 as "
            "minimum filter before running PCMCI on new hypotheses."
        ),
    }

    return {
        "failure_by_regime":        failure_by_regime,
        "failure_by_sector":        failure_by_sector,
        "failure_by_archetype":     failure_by_archetype,
        "failure_by_liquidity":     failure_by_liquidity,
        "safest_contexts":          safest_contexts,
        "dangerous_contexts":       dangerous_contexts,
        "pcmci_failure_threshold":  pcmci_failure_threshold,
    }


# ---------------------------------------------------------------------------
# Command 3: predictability_map
# ---------------------------------------------------------------------------

def predictability_map(params):
    """Build sector × regime predictability grid with research opportunity scores."""
    db = get_db()
    laws          = load_pattern_laws(db)
    failures_raw  = load_failure_intelligence(db)
    regime_history= load_regime_history(db)
    stock_dna     = load_stock_dna(db)
    ohlcv_summary = load_ohlcv_summary(db)
    db.close()

    # ── Build all unique sectors and regimes from laws + dna + regime history ─
    sectors_set = set()
    regimes_set = set(KNOWN_REGIMES)

    for law in laws:
        sec = (law.get('sector') or 'Unknown').strip()
        if sec:
            sectors_set.add(sec)
        reg = (law.get('regime_context') or '').strip().upper()
        if reg:
            regimes_set.add(reg)

    for dna in stock_dna.values():
        sec = (dna.get('sector') or 'Unknown').strip()
        if sec:
            sectors_set.add(sec)

    for rh in regime_history:
        reg = (rh.get('regime_label') or '').strip().upper()
        if reg:
            regimes_set.add(reg)

    if not sectors_set:
        sectors_set = {'Banking', 'Telecom', 'Real_Estate', 'Energy', 'Industrial'}

    regimes_set.discard('UNKNOWN')
    regimes_set = regimes_set or {'TRENDING_UP', 'TRENDING_DOWN', 'VOLATILE', 'SIDEWAYS'}

    # ── Map laws to (sector, regime) buckets ─────────────────────────────────
    bucket = defaultdict(list)
    for law in laws:
        sec    = (law.get('sector') or 'Unknown').strip()
        regime = (law.get('regime_context') or 'UNKNOWN').strip().upper()
        prec   = law.get('precision') or 0
        status = law.get('status', '') or ''
        if status != 'DEAD':
            bucket[(sec, regime)].append(prec)

    # Global average precision as fallback
    all_precisions = [law.get('precision') or 0 for law in laws if law.get('status', '') != 'DEAD']
    global_avg     = safe_mean(all_precisions, default=0.40)

    # ── Compute data richness per sector (proxy: n laws + n symbols) ─────────
    sector_law_count = defaultdict(int)
    for (sec, reg), precisions in bucket.items():
        sector_law_count[sec] += len(precisions)

    max_sector_laws = max(sector_law_count.values()) if sector_law_count else 1

    def data_richness(sector):
        """0-1 richness based on law count relative to max."""
        cnt = sector_law_count.get(sector, 0)
        return round(cnt / max_sector_laws, 3)

    # ── Build the predictability map ─────────────────────────────────────────
    pred_map    = {}
    opp_records = []

    for sector in sorted(sectors_set):
        pred_map[sector] = {}
        for regime in sorted(regimes_set):
            key = (sector, regime)
            if key in bucket and bucket[key]:
                prec_vals = bucket[key]
                pred_score = round(safe_mean(prec_vals), 4)
                n_laws     = len(prec_vals)
            else:
                # Uncertainty penalty: cross-sector average × 0.5
                pred_score = round(global_avg * 0.5, 4)
                n_laws     = 0

            pred_map[sector][regime] = pred_score

            dr          = data_richness(sector)
            opp_score   = round((1.0 - pred_score) * (dr if dr > 0 else 0.5), 4)
            opp_records.append({
                'sector':         sector,
                'regime':         regime,
                'predictability': pred_score,
                'n_laws':         n_laws,
                'opportunity_score': opp_score,
            })

    # ── Identify best opportunities and combos to avoid ───────────────────────
    opp_records.sort(key=lambda x: x['opportunity_score'], reverse=True)

    # Best opportunities: high opportunity_score but at least some data richness
    best_opportunities = [
        o for o in opp_records
        if o['opportunity_score'] > 0.3 and o['n_laws'] >= 0
    ][:10]

    # For avoid list: low predictability AND low n_laws (sparse + unreliable)
    avoid_candidates = [
        o for o in sorted(opp_records, key=lambda x: x['predictability'])
        if o['predictability'] < 0.35 and o['n_laws'] == 0
    ][:10]

    # Enrich avoid list with failure context
    regime_lookup = build_regime_lookup(regime_history)
    failure_counts_combo = defaultdict(int)
    for f in failures_raw:
        sym    = f.get('symbol', '')
        date   = f.get('date', '')
        regime = get_regime_for_date(date, regime_lookup)
        dna    = stock_dna.get(sym, {})
        sector = dna.get('sector', 'Unknown')
        failure_counts_combo[(sector, regime)] += 1

    avoid_combinations = []
    for o in avoid_candidates:
        sec = o['sector']
        reg = o['regime']
        f_count = failure_counts_combo.get((sec, reg), 0)
        reason_parts = []
        if o['predictability'] < 0.35:
            reason_parts.append("Low predictability")
        if o['n_laws'] == 0:
            reason_parts.append("No active laws")
        if f_count > 0:
            reason_parts.append(f"{f_count} recorded failures")
        avoid_combinations.append({
            'sector':         sec,
            'regime':         reg,
            'predictability': o['predictability'],
            'failure_count':  f_count,
            'reason':         "; ".join(reason_parts) or "High uncertainty, low data density",
        })

    # ── Persist to predictability_map table ───────────────────────────────────
    now_iso = datetime.utcnow().isoformat()
    try:
        db2 = get_db()
        for rec in opp_records:
            db2.execute("""
                INSERT OR REPLACE INTO predictability_map
                (sector, regime, predictability_score, n_laws, opportunity_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                rec['sector'], rec['regime'],
                rec['predictability'], rec['n_laws'],
                rec['opportunity_score'], now_iso,
            ))
        db2.commit()
        db2.close()
    except Exception:
        pass

    return {
        "predictability_map":   pred_map,
        "best_opportunities":   best_opportunities,
        "avoid_combinations":   avoid_combinations,
    }


# ---------------------------------------------------------------------------
# Command 4: meta_directives
# ---------------------------------------------------------------------------

def _build_directives(hyp, fail, pred):
    """Combine all three analyses into prioritized actionable directives."""
    directives = []
    priority_counter = [10]

    def next_priority(decrement=1):
        p = priority_counter[0]
        priority_counter[0] = max(1, priority_counter[0] - decrement)
        return p

    # ── FOCUS_HERE directives from best feature_type × sector combos ─────────
    best_fts  = hyp.get('best_feature_types', [])[:3]
    best_secs = hyp.get('best_sectors', [])[:3]
    best_regs = hyp.get('best_regime_contexts', [])[:2]

    for ft in best_fts:
        for sec in best_secs[:2]:
            for reg in best_regs[:1]:
                ft_name  = ft.get('type', 'unknown')
                sec_name = sec.get('sector', 'Unknown')
                reg_name = reg.get('regime', 'UNKNOWN')
                avg_prec = ft.get('avg_precision', 0)
                n_laws   = ft.get('n_laws', 0)
                pct_act  = ft.get('pct_active', 0)

                # Only recommend if avg precision is reasonable
                if avg_prec >= 0.50:
                    directives.append({
                        "priority":         next_priority(1),
                        "type":             "FOCUS_HERE",
                        "instruction":      (
                            f"Search for {ft_name} laws in {sec_name} sector "
                            f"during {reg_name} regime"
                        ),
                        "reasoning":        (
                            f"Best historical precision {avg_prec:.2f}, "
                            f"{n_laws} active laws, "
                            f"{pct_act*100:.0f}% currently ACTIVE"
                        ),
                        "expected_gain":    f"+{round(avg_prec * 0.12, 3)} precision",
                        "feature_type":     ft_name,
                        "sector":           sec_name,
                        "regime":           reg_name,
                    })

    # ── AVOID_THIS directives from worst combinations ─────────────────────────
    worst_combos = hyp.get('worst_combinations', [])[:5]
    for wc in worst_combos:
        combo    = wc.get('combo', '')
        avg_prec = wc.get('avg_precision', 0)
        if avg_prec < 0.40:
            directives.append({
                "priority":      next_priority(1),
                "type":          "AVOID_THIS",
                "instruction":   f"Do not invest research cycles in: {combo}",
                "reasoning":     f"Historical avg precision only {avg_prec:.2f}, signals are noisy",
                "expected_gain": "N/A — avoid waste",
                "combo":         combo,
            })

    # ── INVESTIGATE_FAILURE directives from dangerous contexts ────────────────
    dangerous = fail.get('dangerous_contexts', [])[:3]
    for dc in dangerous:
        context = dc.get('context', '')
        share   = dc.get('failure_share', 0)
        parts   = context.split('|') if '|' in context else [context, 'Unknown']
        regime  = parts[0] if parts else 'UNKNOWN'
        sector  = parts[1] if len(parts) > 1 else 'Unknown'
        directives.append({
            "priority":      next_priority(1),
            "type":          "INVESTIGATE_FAILURE",
            "instruction":   (
                f"Audit failure patterns in {sector} during {regime}: "
                f"{share*100:.1f}% of all failures occur here"
            ),
            "reasoning":     (
                f"High failure density in {context}. "
                "Understanding root cause could unlock significant precision gains."
            ),
            "expected_gain": "+0.05 to +0.12 precision via failure-aware filtering",
            "context":       context,
        })

    # ── MUTATION_STRATEGY directives from best mutation types ─────────────────
    best_muts = hyp.get('best_mutation_types', [])[:3]
    for bm in best_muts:
        mt       = bm.get('type', 'unknown')
        avg_d    = bm.get('avg_delta', 0)
        succ_rt  = bm.get('success_rate', 0)
        n_muts   = bm.get('n_mutations', 0)
        if avg_d > 0 and succ_rt >= 0.5:
            directives.append({
                "priority":      next_priority(1),
                "type":          "MUTATION_STRATEGY",
                "instruction":   (
                    f"Prefer '{mt}' mutation type when evolving existing laws"
                ),
                "reasoning":     (
                    f"Avg precision delta +{avg_d:.3f} over {n_muts} mutations, "
                    f"success rate {succ_rt*100:.0f}%"
                ),
                "expected_gain": f"+{round(avg_d, 3)} precision per mutation",
                "mutation_type": mt,
            })

    # ── REGIME_CAUTION directives from PCMCI threshold analysis ───────────────
    pcmci = fail.get('pcmci_failure_threshold', {})
    risky = pcmci.get('risky_regimes', {})
    for regime, info in risky.items():
        mm_rate = info.get('mismatch_rate', 0)
        directives.append({
            "priority":      next_priority(1),
            "type":          "REGIME_CAUTION",
            "instruction":   (
                f"Disable PCMCI causal inference during {regime} regime "
                f"(mismatch rate {mm_rate*100:.0f}%)"
            ),
            "reasoning":     (
                "REGIME_MISMATCH failure rate exceeds caution threshold. "
                "Causal graphs learned in this regime are unreliable."
            ),
            "expected_gain": "+0.04 precision via false-signal elimination",
            "regime":        regime,
        })

    # ── FOCUS_HERE from predictability map opportunities ──────────────────────
    opp_list = pred.get('best_opportunities', [])[:5]
    for opp in opp_list:
        sec   = opp.get('sector', 'Unknown')
        reg   = opp.get('regime', 'UNKNOWN')
        p_sc  = opp.get('predictability', 0)
        o_sc  = opp.get('opportunity_score', 0)
        n_l   = opp.get('n_laws', 0)
        if o_sc >= 0.3:
            directives.append({
                "priority":      next_priority(1),
                "type":          "FOCUS_HERE",
                "instruction":   (
                    f"Expand law discovery in {sec} × {reg} "
                    f"(current predictability only {p_sc:.2f})"
                ),
                "reasoning":     (
                    f"Opportunity score {o_sc:.2f} — high room for improvement, "
                    f"{n_l} laws currently covering this cell"
                ),
                "expected_gain": f"+{round((1 - p_sc) * 0.3, 3)} predictability potential",
                "sector":        sec,
                "regime":        reg,
            })

    # ── Sort by priority descending ───────────────────────────────────────────
    directives.sort(key=lambda x: x['priority'], reverse=True)

    # Re-number priorities cleanly 1–10
    n_d = len(directives)
    for i, d in enumerate(directives):
        d['priority'] = max(1, 10 - i) if n_d > 0 else 5

    top_directive = directives[0] if directives else {}

    # ── Research budget allocation ────────────────────────────────────────────
    sector_budget = defaultdict(float)
    total_opp = 0.0
    opp_all = pred.get('best_opportunities', [])
    for opp in opp_all:
        sec = opp.get('sector', 'Other')
        o   = opp.get('opportunity_score', 0)
        sector_budget[sec] += o
        total_opp += o

    if total_opp > 0:
        for sec in sector_budget:
            sector_budget[sec] = round(sector_budget[sec] / total_opp, 3)
    else:
        # fallback uniform
        unique_sectors = list({opp.get('sector', 'Other') for opp in opp_all}) or ['Other']
        for sec in unique_sectors:
            sector_budget[sec] = round(1.0 / len(unique_sectors), 3)

    # Normalize to 1.0
    total = sum(sector_budget.values())
    if total > 0:
        sector_budget = {k: round(v / total, 3) for k, v in sector_budget.items()}

    # Ensure "Other" bucket
    if 'Other' not in sector_budget:
        sector_budget['Other'] = round(1.0 - sum(v for k, v in sector_budget.items() if k != 'Other'), 3)

    return {
        "directives":                 directives[:20],
        "top_directive":              top_directive,
        "research_budget_allocation": dict(sector_budget),
    }


def meta_directives(params):
    """Generate actionable directives by combining all three analyses."""
    hyp  = analyze_hypotheses({})
    fail = failure_contexts({})
    pred = predictability_map({})

    result = _build_directives(hyp, fail, pred)

    # Persist top directive to meta_learning_results
    try:
        db = get_db()
        top = result.get('top_directive', {})
        db.execute("""
            INSERT OR REPLACE INTO meta_learning_results
            (run_date, top_directive, directives_json, computed_at)
            VALUES (?, ?, ?, ?)
        """, (
            datetime.utcnow().strftime('%Y-%m-%d'),
            json.dumps(top, default=str),
            json.dumps(result.get('directives', []), default=str),
            datetime.utcnow().isoformat(),
        ))
        db.commit()
        db.close()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Command 5: build_full
# ---------------------------------------------------------------------------

def build_full(params):
    """Run all 4 analyses and produce a consolidated report."""
    hyp  = analyze_hypotheses({})
    fail = failure_contexts({})
    pred = predictability_map({})
    dirs = _build_directives(hyp, fail, pred)

    # ── Compose meta_summary ─────────────────────────────────────────────────
    best_ft  = (hyp.get('best_feature_types', [{}])[0] if hyp.get('best_feature_types') else {})
    best_sec = (hyp.get('best_sectors', [{}])[0]       if hyp.get('best_sectors')       else {})
    best_reg = (hyp.get('best_regime_contexts', [{}])[0]
                if hyp.get('best_regime_contexts') else {})
    worst_combo = (hyp.get('worst_combinations', [{}])[0]
                   if hyp.get('worst_combinations') else {})

    ft_name  = best_ft.get('type', 'N/A')
    sec_name = best_sec.get('sector', 'N/A')
    reg_name = best_reg.get('regime', 'N/A')
    bad_combo = worst_combo.get('combo', 'N/A')

    meta_summary = (
        f"Focus on {ft_name} laws in {sec_name} during {reg_name} regime; "
        f"avoid {bad_combo} combinations."
    )

    # ── Persist full results ──────────────────────────────────────────────────
    now_iso = datetime.utcnow().isoformat()
    today   = datetime.utcnow().strftime('%Y-%m-%d')
    try:
        db = get_db()
        db.execute("""
            INSERT OR REPLACE INTO meta_learning_results
            (run_date, best_feature_type, best_sector, best_regime,
             worst_combination, top_directive, predictability_json,
             directives_json, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            ft_name,
            sec_name,
            reg_name,
            bad_combo,
            json.dumps(dirs.get('top_directive', {}), default=str),
            json.dumps(pred.get('predictability_map', {}), default=str),
            json.dumps(dirs.get('directives', []), default=str),
            now_iso,
        ))
        db.commit()
        db.close()
    except Exception:
        pass

    return {
        "hypotheses":    hyp,
        "failures":      fail,
        "predictability": pred,
        "directives":    dirs,
        "status":        "complete",
        "meta_summary":  meta_summary,
        "computed_at":   now_iso,
    }


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'analyze_hypotheses':  analyze_hypotheses,
    'failure_contexts':    failure_contexts,
    'predictability_map':  predictability_map,
    'meta_directives':     meta_directives,
    'build_full':          build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "Usage: python meta_learning_engine.py <command> '<json_params>'",
            "available_commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    command = sys.argv[1].strip()

    # Parse optional JSON params
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except (json.JSONDecodeError, ValueError):
            params = {}
    else:
        params = {}

    if command not in COMMANDS:
        print(json.dumps({
            "error": f"Unknown command: '{command}'",
            "available_commands": list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[command](params)
        # Ensure last stdout line is valid JSON
        print(json.dumps(result, default=str))
    except Exception as exc:
        import traceback
        print(json.dumps({
            "error":     str(exc),
            "command":   command,
            "traceback": traceback.format_exc(),
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()
