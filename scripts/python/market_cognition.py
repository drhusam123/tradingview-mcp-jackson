#!/usr/bin/env python3
"""
EGX Phase 16 — Autonomous Market Cognition Engine
==================================================
7-stage continuous-learning pipeline:
  1. stock_dna         — per-symbol behavioral DNA with drift detection
  2. sector_dna        — sector synchronization, contagion, leadership
  3. explosion_anatomy — archetype clustering, universal signatures
  4. universal_laws    — full precision/recall/FAR, regime/OOS validation
  5. consolidate_memory — knowledge-graph construction
  6. self_evolve       — threshold competition, law ranking
  7. generate_report   — comprehensive 10-section intelligence report
  8. full_cognition    — all 7 stages

Usage:
  python3 market_cognition.py <command> '<json_params>'
  Commands: status | stock_dna | sector_dna | explosion_anatomy |
            universal_laws | consolidate_memory | self_evolve |
            generate_report | full_cognition
"""

import sys, json, sqlite3, math, os, re, time
from pathlib import Path
from datetime import datetime, timedelta, date
from collections import defaultdict, Counter

import numpy as np

ROOT    = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / 'data' / 'egx_trading.db'
RPT_DIR = ROOT / 'data' / 'research_reports'
RPT_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ──────────────────────────────────────────────────────────────────
N_EXPLOSION_ARCHETYPES = 5
SYNC_WINDOW_DAYS       = 5      # days within which two sector explosions are "synchronized"
CONTAGION_WINDOW_DAYS  = 10     # days to look for contagion spread
SIGNATURE_MIN_PREV     = 0.30   # min prevalence to call a signature "common"
UNIVERSAL_THRESHOLD    = 0.55   # prevalence in > 55% of explosions = "universal"
MIN_SECTOR_SAMPLES     = 30     # minimum explosions to characterize a sector
MIN_STOCK_EXPLOSIONS   = 3      # minimum to build stock DNA

REGIME_CODE   = {'BULL': 0.75, 'CHOPPY': 0.50, 'BEAR': 0.25, 'UNKNOWN': 0.10}
PHYSICS_CODE  = {'COMPRESSION_RELEASE': 1.0, 'STRUCTURAL_EXPANSION': 0.75,
                 'MOMENTUM_BURST': 0.50, 'REVERSAL_SPIKE': 0.25}
EXPL_CODE     = {'EXTREME': 1.0, 'LARGE': 0.75, 'MEDIUM': 0.50, 'SMALL': 0.25}

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_dna (
    symbol                   TEXT PRIMARY KEY,
    sector                   TEXT,
    updated_at               TEXT,
    explosion_count          INTEGER,
    explosion_rate_pct       REAL,
    false_breakout_rate_pct  REAL,
    cycle_period_days        REAL,
    avg_return_1d            REAL,
    avg_return_3d            REAL,
    avg_return_5d            REAL,
    post_decay_ratio         REAL,
    avg_pre_bbw              REAL,
    avg_compression_days     REAL,
    avg_ignition_speed       REAL,
    avg_cascade_score        REAL,
    archetype                TEXT,
    dominant_physics_type    TEXT,
    dominant_explosion_class TEXT,
    dominant_regime          TEXT,
    early_explosion_rate     REAL,
    late_explosion_rate      REAL,
    drift_direction          TEXT,
    drift_magnitude          REAL,
    best_precursor           TEXT,
    best_precursor_precision REAL,
    momentum_persistence     REAL,
    hurst_approx             REAL,
    liquidity_score          REAL
);

CREATE TABLE IF NOT EXISTS sector_dna (
    sector                    TEXT PRIMARY KEY,
    updated_at                TEXT,
    n_stocks                  INTEGER,
    n_stocks_with_explosions  INTEGER,
    total_explosions          INTEGER,
    avg_explosion_rate        REAL,
    synchronization_pct       REAL,
    contagion_delay_days      REAL,
    leadership_stock          TEXT,
    rotation_period_days      REAL,
    dominant_physics          TEXT,
    dominant_regime           TEXT,
    false_breakout_rate       REAL,
    avg_return_1d             REAL,
    avg_return_5d             REAL,
    bull_explosion_rate       REAL,
    bear_explosion_rate       REAL,
    choppy_explosion_rate     REAL,
    sector_archetype          TEXT
);

CREATE TABLE IF NOT EXISTS sector_contagion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sector   TEXT,
    target_sector   TEXT,
    n_co_explosions INTEGER,
    avg_delay_days  REAL,
    co_rate_pct     REAL,
    updated_at      TEXT,
    UNIQUE(source_sector, target_sector)
);

CREATE TABLE IF NOT EXISTS explosion_archetypes (
    archetype_id             INTEGER PRIMARY KEY,
    archetype_name           TEXT,
    n_members                INTEGER,
    pct_of_total             REAL,
    centroid_pre_bbw         REAL,
    centroid_compression_days REAL,
    centroid_ignition_speed  REAL,
    centroid_cascade_score   REAL,
    centroid_return_1d       REAL,
    centroid_return_5d       REAL,
    dominant_physics_type    TEXT,
    dominant_explosion_class TEXT,
    dominant_regime          TEXT,
    dominant_sector          TEXT,
    avg_return_1d            REAL,
    avg_return_5d            REAL,
    false_breakout_rate      REAL,
    signature_features       TEXT,
    updated_at               TEXT
);

CREATE TABLE IF NOT EXISTS explosion_signatures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_name   TEXT UNIQUE,
    description      TEXT,
    scope            TEXT,
    scope_value      TEXT,
    prevalence_pct   REAL,
    avg_return_uplift REAL,
    feature_type     TEXT,
    condition_expr   TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS universal_laws_p16 (
    pattern_id               TEXT PRIMARY KEY,
    pattern_name             TEXT,
    direction                TEXT,
    precision                REAL,
    recall                   REAL,
    false_alarm_rate         REAL,
    f1_score                 REAL,
    n_activations            INTEGER,
    n_hits                   INTEGER,
    random_baseline_precision REAL,
    precision_vs_random      REAL,
    information_gain         REAL,
    is_regime_dependent      INTEGER,
    best_regime              TEXT,
    best_regime_precision    REAL,
    worst_regime             TEXT,
    worst_regime_precision   REAL,
    regime_stability_score   REAL,
    is_sector_dependent      INTEGER,
    best_sector              TEXT,
    best_sector_precision    REAL,
    stability_class          TEXT,
    early_precision          REAL,
    late_precision           REAL,
    oos_gap                  REAL,
    beats_random             INTEGER,
    law_status               TEXT,
    updated_at               TEXT
);

CREATE TABLE IF NOT EXISTS law_competition (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id       TEXT,
    pattern_name     TEXT,
    direction        TEXT,
    variant_name     TEXT,
    variant_threshold REAL,
    variant_precision REAL,
    base_precision   REAL,
    improvement_pp   REAL,
    beats_base       INTEGER,
    random_baseline  REAL,
    beats_random     INTEGER,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
    node_id          TEXT PRIMARY KEY,
    node_type        TEXT,
    node_name        TEXT,
    properties_json  TEXT,
    importance_score REAL,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT,
    target_id       TEXT,
    edge_type       TEXT,
    weight          REAL,
    evidence_count  INTEGER,
    label           TEXT,
    updated_at      TEXT,
    UNIQUE(source_id, target_id, edge_type)
);

CREATE TABLE IF NOT EXISTS cognition_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp      TEXT,
    stage              TEXT,
    duration_sec       REAL,
    records_processed  INTEGER,
    key_findings       TEXT,
    error              TEXT
);
"""

# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db

def ensure_schema(db):
    for stmt in SCHEMA_SQL.strip().split(';'):
        s = stmt.strip()
        if s:
            try:
                db.execute(s)
            except Exception:
                pass
    # Migrations
    _migrate_column(db, 'law_stability_curves', 'stability_class', 'TEXT')
    db.commit()

def _migrate_column(db, table, col, col_type):
    try:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            db.commit()
    except Exception:
        pass

def _log_stage(db, stage, duration, n_records, findings, error=None):
    db.execute("""INSERT INTO cognition_log
        (run_timestamp, stage, duration_sec, records_processed, key_findings, error)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.utcnow().isoformat(), stage, round(duration, 2), n_records,
         json.dumps(findings) if isinstance(findings, list) else findings, error))
    db.commit()

def _now():
    return datetime.utcnow().isoformat()

# ── Math helpers ───────────────────────────────────────────────────────────────
def _safe_div(a, b, default=0.0):
    return a / b if b and b != 0 else default

def _entropy(p):
    if p <= 0 or p >= 1:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)

def _information_gain(precision, base_rate):
    """Bits of information from knowing precursor is active"""
    if base_rate <= 0 or base_rate >= 1:
        return 0.0
    if precision <= 0 or precision >= 1:
        return 0.0
    return _entropy(base_rate) - _entropy(precision)

def _mode(lst):
    if not lst:
        return None
    c = Counter(lst)
    return c.most_common(1)[0][0]

def _pct(n, total):
    return round(100.0 * n / total, 1) if total > 0 else 0.0

# ── Simple K-means (no sklearn dependency) ────────────────────────────────────
def _kmeans(X, k=5, max_iter=50, seed=42):
    """Numpy-only K-means clustering. Returns (labels, centers)."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), k, replace=False)
    centers = X[idx].copy().astype(float)
    labels = np.zeros(len(X), dtype=int)
    for _ in range(max_iter):
        dists = np.linalg.norm(X[:, None] - centers[None, :], axis=2)  # (N, k)
        new_labels = np.argmin(dists, axis=1)
        new_centers = np.array([
            X[new_labels == i].mean(axis=0) if (new_labels == i).any() else centers[i]
            for i in range(k)
        ])
        if np.allclose(centers, new_centers, atol=1e-6):
            labels = new_labels
            centers = new_centers
            break
        labels = new_labels
        centers = new_centers
    return labels, centers

def _normalize_features(X):
    """Normalize each column to [0, 1] range."""
    mn = X.min(axis=0)
    mx = X.max(axis=0)
    rng = mx - mn
    rng[rng == 0] = 1.0
    return (X - mn) / rng, mn, mx

# ── STATUS ─────────────────────────────────────────────────────────────────────
def get_status(db, params=None):
    def cnt(t, where=''):
        try:
            return db.execute(f"SELECT COUNT(*) FROM {t} {where}").fetchone()[0]
        except Exception:
            return 0
    def latest(t, col):
        try:
            r = db.execute(f"SELECT {col} FROM {t} ORDER BY {col} DESC LIMIT 1").fetchone()
            return r[0] if r else None
        except Exception:
            return None

    last_run = db.execute("""SELECT run_timestamp, stage, duration_sec FROM cognition_log
                              ORDER BY run_timestamp DESC LIMIT 1""").fetchone()

    return {
        'stock_dna_profiles':     cnt('stock_dna'),
        'sector_dna_profiles':    cnt('sector_dna'),
        'explosion_archetypes':   cnt('explosion_archetypes'),
        'explosion_signatures':   cnt('explosion_signatures'),
        'universal_laws_p16':     cnt('universal_laws_p16'),
        'knowledge_graph_nodes':  cnt('knowledge_graph_nodes'),
        'knowledge_graph_edges':  cnt('knowledge_graph_edges'),
        'law_competition_runs':   cnt('law_competition'),
        'cognition_runs':         cnt('cognition_log'),
        'last_run':               dict(last_run) if last_run else None,
        'source_tables': {
            'explosive_moves':    cnt('explosive_moves'),
            'market_physics':     cnt('market_physics'),
            'false_breakouts':    cnt('false_breakout_anatomy'),
            'counterfactuals':    cnt('counterfactual_events'),
            'stock_profiles':     cnt('stock_profiles'),
            'sector_cycles':      cnt('sector_behavioral_cycles'),
        }
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — STOCK DNA
# ══════════════════════════════════════════════════════════════════════════════
def build_stock_dna(db, params=None):
    t0 = time.time()
    print("  [Stage 1] Building Stock DNA profiles...", flush=True)

    # Fetch all symbols with sufficient explosions
    syms = db.execute("""
        SELECT symbol, COUNT(*) n FROM explosive_moves
        GROUP BY symbol HAVING n >= ?
        ORDER BY n DESC
    """, (MIN_STOCK_EXPLOSIONS,)).fetchall()
    print(f"  → {len(syms)} symbols with ≥{MIN_STOCK_EXPLOSIONS} explosions", flush=True)

    # Pre-load supplementary data (keyed by (symbol, date))
    physics_map = {}
    for r in db.execute("""SELECT symbol, explosion_date, compression_days, compression_depth,
                               ignition_speed, cascade_score, physics_type FROM market_physics""").fetchall():
        physics_map[(r['symbol'], r['explosion_date'])] = dict(r)

    fba_map = {}
    for r in db.execute("""SELECT symbol, explosion_date, reversal_pct, reversal_days,
                               pre5_bbw FROM false_breakout_anatomy""").fetchall():
        fba_map[(r['symbol'], r['explosion_date'])] = dict(r)

    # Pre-load stock_profiles for momentum/hurst/liquidity
    sp_map = {}
    for r in db.execute("""SELECT symbol, momentum_persistence, hurst_approx, liquidity_score
                            FROM stock_profiles""").fetchall():
        sp_map[r['symbol']] = dict(r)

    # Pre-load best precursor per stock from counterfactual_events
    precursor_hits = defaultdict(lambda: defaultdict(lambda: {'hits': 0, 'total': 0}))
    for r in db.execute("""SELECT symbol, pattern_name, outcome FROM counterfactual_events""").fetchall():
        d = precursor_hits[r['symbol']][r['pattern_name']]
        d['total'] += 1
        if r['outcome'] == 'HIT':
            d['hits'] += 1

    # Pre-load regime per explosion date (approximate from regime_history)
    regime_by_date = {}
    for r in db.execute("SELECT date, regime FROM regime_history").fetchall():
        regime_by_date[r['date']] = r['regime']

    # Pre-load sector from stock_universe
    sector_map = {}
    for r in db.execute("SELECT symbol, sector FROM stock_universe").fetchall():
        sector_map[r['symbol']] = r['sector'] or ''

    n_done = 0
    findings = []

    for row in syms:
        sym = row['symbol']
        sector = sector_map.get(sym, '')

        # All explosions for this symbol
        expl = db.execute("""SELECT explosion_date, direction, return_1d, return_3d, return_5d,
                                     explosion_class, pre1_bb_width
                              FROM explosive_moves WHERE symbol=? ORDER BY explosion_date""",
                          (sym,)).fetchall()
        expl = [dict(e) for e in expl]
        n = len(expl)
        if n < MIN_STOCK_EXPLOSIONS:
            continue

        # Merge physics + fba
        for e in expl:
            d = e['explosion_date']
            p = physics_map.get((sym, d), {})
            f = fba_map.get((sym, d), {})
            e.update({
                'compression_days':  p.get('compression_days', 0) or 0,
                'ignition_speed':    p.get('ignition_speed', 1.0) or 1.0,
                'cascade_score':     p.get('cascade_score', 0.5) or 0.5,
                'physics_type':      p.get('physics_type', None),
                'is_false_breakout': int(f.get('reversal_pct', 0) or 0) > 0,
                'bbw': f.get('pre5_bbw') or e.get('pre1_bb_width') or 0,
                'regime': regime_by_date.get(d, 'UNKNOWN'),
            })

        # ── Basic stats ──
        returns_1d = [e['return_1d'] for e in expl if e['return_1d']]
        returns_3d = [e['return_3d'] for e in expl if e['return_3d']]
        returns_5d = [e['return_5d'] for e in expl if e['return_5d']]
        avg_r1 = float(np.mean(returns_1d)) if returns_1d else 0
        avg_r3 = float(np.mean(returns_3d)) if returns_3d else 0
        avg_r5 = float(np.mean(returns_5d)) if returns_5d else 0
        post_decay = _safe_div(avg_r5, avg_r1, 1.0)

        # ── Pre-explosion conditions ──
        bbws = [e['bbw'] for e in expl if e['bbw'] and e['bbw'] > 0]
        avg_bbw = float(np.mean(bbws)) if bbws else 0
        comp_days = [e['compression_days'] for e in expl if e['compression_days'] > 0]
        avg_comp  = float(np.mean(comp_days)) if comp_days else 0
        ign_sp    = [e['ignition_speed'] for e in expl if e['ignition_speed'] > 0]
        avg_ign   = float(np.mean(ign_sp)) if ign_sp else 1.0
        casc      = [e['cascade_score'] for e in expl]
        avg_casc  = float(np.mean(casc)) if casc else 0.5

        # ── Dominant categorical features ──
        dom_physics = _mode([e['physics_type'] for e in expl if e['physics_type']])
        dom_class   = _mode([e['explosion_class'] for e in expl if e['explosion_class']])
        dom_regime  = _mode([e['regime'] for e in expl if e['regime'] != 'UNKNOWN'])

        # ── False breakout rate ──
        n_false = sum(1 for e in expl if e['is_false_breakout'])
        fbr = _pct(n_false, n)

        # ── Cycle period ──
        dates = sorted([e['explosion_date'] for e in expl])
        if len(dates) >= 2:
            deltas = [(datetime.fromisoformat(dates[i+1]) - datetime.fromisoformat(dates[i])).days
                      for i in range(len(dates)-1)]
            cycle_period = float(np.median(deltas))
        else:
            cycle_period = 0.0

        # ── Explosion rate (per 100 trading days) ──
        total_bars = db.execute("""SELECT COUNT(*) FROM ohlcv_history WHERE symbol=?""",
                                (sym,)).fetchone()[0]
        expl_rate = _pct(n, total_bars) if total_bars > 0 else 0.0

        # ── Behavioral drift: early vs late half ──
        mid = len(expl) // 2
        early_expl = expl[:mid]
        late_expl  = expl[mid:]
        early_bars  = total_bars // 2 if total_bars > 0 else 1
        late_bars   = total_bars - early_bars if total_bars > 0 else 1
        early_rate  = _pct(len(early_expl), early_bars) if early_bars > 0 else 0
        late_rate   = _pct(len(late_expl),  late_bars)  if late_bars  > 0 else 0
        drift_mag   = late_rate - early_rate
        if abs(drift_mag) < 0.3:
            drift_dir = 'STABLE'
        elif drift_mag > 1.5:
            drift_dir = 'ACCELERATING'
        elif drift_mag > 0.3:
            drift_dir = 'INCREASING'
        elif drift_mag < -1.5:
            drift_dir = 'FADING'
        else:
            drift_dir = 'DECREASING'

        # ── Best precursor ──
        best_prec, best_prec_prec = None, 0.0
        for pname, d in precursor_hits[sym].items():
            if d['total'] >= 5:
                prec = _safe_div(d['hits'], d['total'])
                if prec > best_prec_prec:
                    best_prec, best_prec_prec = pname, prec

        # ── DNA archetype (5 classes) ──
        if expl_rate >= 2.5 and avg_bbw < 0.07 and dom_physics == 'COMPRESSION_RELEASE':
            archetype = 'EXPLOSIVE_FAST'
        elif expl_rate >= 1.5 and post_decay >= 0.8:
            archetype = 'EXPLOSIVE_STEADY'
        elif expl_rate >= 1.0 and fbr > 30:
            archetype = 'VOLATILE_REVERSAL'
        elif expl_rate >= 0.5:
            archetype = 'STEADY_GROWER'
        else:
            archetype = 'DORMANT'

        sp = sp_map.get(sym, {})

        db.execute("""INSERT OR REPLACE INTO stock_dna
            (symbol, sector, updated_at,
             explosion_count, explosion_rate_pct, false_breakout_rate_pct, cycle_period_days,
             avg_return_1d, avg_return_3d, avg_return_5d, post_decay_ratio,
             avg_pre_bbw, avg_compression_days, avg_ignition_speed, avg_cascade_score,
             archetype, dominant_physics_type, dominant_explosion_class, dominant_regime,
             early_explosion_rate, late_explosion_rate, drift_direction, drift_magnitude,
             best_precursor, best_precursor_precision, momentum_persistence, hurst_approx, liquidity_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sym, sector, _now(),
             n, round(expl_rate, 3), round(fbr, 1), round(cycle_period, 1),
             round(avg_r1, 4), round(avg_r3, 4), round(avg_r5, 4), round(post_decay, 3),
             round(avg_bbw, 5), round(avg_comp, 1), round(avg_ign, 3), round(avg_casc, 3),
             archetype, dom_physics, dom_class, dom_regime,
             round(early_rate, 3), round(late_rate, 3), drift_dir, round(drift_mag, 3),
             best_prec, round(best_prec_prec, 3),
             round(sp.get('momentum_persistence') or 0, 3),
             round(sp.get('hurst_approx') or 0, 3),
             round(sp.get('liquidity_score') or 0, 3)))
        n_done += 1

    db.commit()

    # Summarize archetypes
    arch_dist = dict(db.execute("""SELECT archetype, COUNT(*) FROM stock_dna
                                   GROUP BY archetype ORDER BY COUNT(*) DESC""").fetchall())
    top_exploding = [dict(r) for r in db.execute("""SELECT symbol, archetype, explosion_count,
                                explosion_rate_pct FROM stock_dna ORDER BY explosion_rate_pct DESC LIMIT 10""").fetchall()]
    drift_accel = db.execute("SELECT COUNT(*) FROM stock_dna WHERE drift_direction='ACCELERATING'").fetchone()[0]
    drift_fading = db.execute("SELECT COUNT(*) FROM stock_dna WHERE drift_direction='FADING'").fetchone()[0]

    findings = [
        f"{n_done} stock DNA profiles built",
        f"Archetype distribution: {arch_dist}",
        f"{drift_accel} stocks accelerating | {drift_fading} fading",
        f"Top by rate: {[r['symbol'] for r in top_exploding[:5]]}",
    ]
    dt = time.time() - t0
    _log_stage(db, 'stock_dna', dt, n_done, findings)
    print(f"  ✅ Stock DNA: {n_done} profiles | {arch_dist}", flush=True)
    return {
        'profiles_built':   n_done,
        'archetype_dist':   arch_dist,
        'drift_accelerating': drift_accel,
        'drift_fading':       drift_fading,
        'top_explosive':      top_exploding[:10],
        'elapsed_sec':        round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — SECTOR DNA
# ══════════════════════════════════════════════════════════════════════════════
def build_sector_dna(db, params=None):
    t0 = time.time()
    print("  [Stage 2] Building Sector DNA profiles...", flush=True)

    # Load all explosions grouped by sector
    all_expl = db.execute("""
        SELECT symbol, explosion_date, direction, return_1d, return_5d,
               explosion_class, sector, pre1_bb_width
        FROM explosive_moves WHERE sector IS NOT NULL AND sector != ''
        ORDER BY sector, explosion_date
    """).fetchall()
    all_expl = [dict(r) for r in all_expl]

    # Load regime info
    regime_by_date = {}
    for r in db.execute("SELECT date, regime FROM regime_history").fetchall():
        regime_by_date[r['date']] = r['regime']

    # Load physics for false breakout info
    physics_map = {}
    for r in db.execute("SELECT symbol, explosion_date, physics_type FROM market_physics").fetchall():
        physics_map[(r['symbol'], r['explosion_date'])] = r['physics_type']

    fba_set = set()
    for r in db.execute("SELECT symbol, explosion_date FROM false_breakout_anatomy WHERE reversal_pct > 0").fetchall():
        fba_set.add((r['symbol'], r['explosion_date']))

    # Group by sector
    by_sector = defaultdict(list)
    for e in all_expl:
        by_sector[e['sector']].append(e)

    # Stock counts per sector
    sector_stocks = defaultdict(set)
    for r in db.execute("SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL AND sector != ''").fetchall():
        sector_stocks[r['sector']].add(r['symbol'])

    n_sectors = 0
    contagion_edges = []

    for sector, expl in by_sector.items():
        if len(expl) < MIN_SECTOR_SAMPLES:
            continue

        n = len(expl)
        n_stocks_w_expl = len({e['symbol'] for e in expl})
        n_stocks_total  = len(sector_stocks.get(sector, set()))

        # Returns
        r1s = [e['return_1d'] for e in expl if e['return_1d']]
        r5s = [e['return_5d'] for e in expl if e['return_5d']]
        avg_r1 = float(np.mean(r1s)) if r1s else 0
        avg_r5 = float(np.mean(r5s)) if r5s else 0

        # False breakout rate
        n_false = sum(1 for e in expl if (e['symbol'], e['explosion_date']) in fba_set)
        fbr = _pct(n_false, n)

        # Dominant physics & class
        physics_types = [physics_map.get((e['symbol'], e['explosion_date'])) for e in expl]
        dom_physics = _mode([p for p in physics_types if p])

        # Regime breakdown
        regime_counts = Counter(regime_by_date.get(e['explosion_date'], 'UNKNOWN') for e in expl)
        dom_regime = regime_counts.most_common(1)[0][0] if regime_counts else 'UNKNOWN'
        bull_pct  = _pct(regime_counts.get('BULL', 0), n)
        bear_pct  = _pct(regime_counts.get('BEAR', 0), n)
        choppy_pct = _pct(regime_counts.get('CHOPPY', 0), n)

        # Synchronization: for each explosion, how many same-sector stocks exploded within ±SYNC_WINDOW_DAYS?
        dates_by_sym = defaultdict(list)
        for e in expl:
            dates_by_sym[e['symbol']].append(e['explosion_date'])
        all_dates_sorted = sorted(e['explosion_date'] for e in expl)
        n_synced = 0
        for e in expl:
            ed = e['explosion_date']
            window_start = (datetime.fromisoformat(ed) - timedelta(days=SYNC_WINDOW_DAYS)).date().isoformat()
            window_end   = (datetime.fromisoformat(ed) + timedelta(days=SYNC_WINDOW_DAYS)).date().isoformat()
            # Count other symbols with explosions in window
            nearby = [d for d in all_dates_sorted
                      if window_start <= d <= window_end and d != ed]
            if nearby:
                n_synced += 1
        sync_pct = _pct(n_synced, n)

        # Leadership: which stock most often triggers cluster (first explosion in cluster)
        leadership_counts = Counter()
        processed = set()
        for e in expl:
            key = (e['symbol'], e['explosion_date'])
            if key in processed:
                continue
            ed = e['explosion_date']
            window_start = (datetime.fromisoformat(ed) - timedelta(days=SYNC_WINDOW_DAYS)).date().isoformat()
            # Is this the earliest explosion in a nearby cluster?
            earlier = [x for x in expl
                       if x['symbol'] != e['symbol']
                       and window_start <= x['explosion_date'] <= ed]
            if not earlier:  # This stock went first
                leadership_counts[e['symbol']] += 1
            processed.add(key)
        leadership_stock = leadership_counts.most_common(1)[0][0] if leadership_counts else None

        # Rotation period (avg gap between cluster starts)
        cluster_starts = []
        last_cluster_end = None
        sorted_expl = sorted(expl, key=lambda x: x['explosion_date'])
        for i, e in enumerate(sorted_expl):
            if last_cluster_end is None or e['explosion_date'] > last_cluster_end:
                cluster_starts.append(e['explosion_date'])
                last_cluster_end = (datetime.fromisoformat(e['explosion_date']) +
                                    timedelta(days=SYNC_WINDOW_DAYS)).date().isoformat()
        if len(cluster_starts) >= 2:
            cs_dates = [datetime.fromisoformat(d) for d in cluster_starts]
            rotation_period = float(np.median([(cs_dates[i+1]-cs_dates[i]).days for i in range(len(cs_dates)-1)]))
        else:
            rotation_period = 0.0

        # Average explosion rate per stock in sector
        total_bars_sector = db.execute("""SELECT SUM(total_bars) FROM stock_universe
                                          WHERE sector=?""", (sector,)).fetchone()[0] or 1
        avg_rate = _pct(n, total_bars_sector / max(n_stocks_total, 1))

        # Sector archetype — relative percentile ranking (not fixed absolute thresholds)
        # Collect will be filled after loop; store raw metrics for now, archetype assigned post-loop
        s_archetype = '__PENDING__'  # assigned in post-loop normalization pass

        # Contagion delay (avg days from sector's leadership stock explosion to others)
        contagion_delay = SYNC_WINDOW_DAYS / 2.0  # default approx

        db.execute("""INSERT OR REPLACE INTO sector_dna
            (sector, updated_at, n_stocks, n_stocks_with_explosions, total_explosions,
             avg_explosion_rate, synchronization_pct, contagion_delay_days, leadership_stock,
             rotation_period_days, dominant_physics, dominant_regime, false_breakout_rate,
             avg_return_1d, avg_return_5d, bull_explosion_rate, bear_explosion_rate,
             choppy_explosion_rate, sector_archetype)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sector, _now(), n_stocks_total, n_stocks_w_expl, n,
             round(avg_rate, 3), round(sync_pct, 1), round(contagion_delay, 1), leadership_stock,
             round(rotation_period, 1), dom_physics, dom_regime, round(fbr, 1),
             round(avg_r1, 4), round(avg_r5, 4), round(bull_pct, 1), round(bear_pct, 1),
             round(choppy_pct, 1), s_archetype))
        n_sectors += 1

    # ── Relative percentile archetype assignment (avoids all-LEADERSHIP problem) ──
    # Collect all sector metrics for relative ranking BEFORE writing final archetypes
    all_sector_rows = db.execute("""
        SELECT sector, synchronization_pct, total_explosions, n_stocks_with_explosions,
               bull_explosion_rate, bear_explosion_rate, false_breakout_rate,
               rotation_period_days
        FROM sector_dna WHERE sector_archetype = '__PENDING__'
    """).fetchall()

    if all_sector_rows:
        sync_vals       = [r[1] for r in all_sector_rows]
        density_vals    = [r[2]/max(r[3],1) for r in all_sector_rows]  # explosions per stock
        bull_vals       = [r[4] for r in all_sector_rows]
        bear_vals       = [r[5] for r in all_sector_rows]
        fbr_vals        = [r[6] for r in all_sector_rows]
        rot_vals        = [r[7] for r in all_sector_rows if r[7] and r[7] > 0]

        def _percentile_rank(val, series):
            """What percentile of series is val? 0–100"""
            if not series or len(series) < 2: return 50.0
            return 100.0 * sum(1 for x in series if x <= val) / len(series)

        for row in all_sector_rows:
            sector_name = row[0]
            sync_p    = _percentile_rank(row[1], sync_vals)
            density_p = _percentile_rank(row[2]/max(row[3],1), density_vals)
            bull_p    = _percentile_rank(row[4], bull_vals)
            bear_p    = _percentile_rank(row[5], bear_vals)
            fbr_p     = _percentile_rank(row[6], fbr_vals)
            rot_p     = _percentile_rank(row[7], rot_vals) if row[7] and row[7] > 0 else 50.0

            # Relative thresholds: top 25% = LEADERSHIP, not a fixed 75% sync
            if sync_p >= 75 and density_p >= 75:
                new_arch = 'LEADERSHIP'
            elif bull_p >= 75:
                new_arch = 'BULL_DRIVEN'
            elif bear_p >= 75:
                new_arch = 'STRESS_SENSITIVE'
            elif rot_p <= 25 and row[7] and row[7] > 0:    # shortest rotation = FAST_ROTATOR
                new_arch = 'FAST_ROTATOR'
            elif sync_p >= 50:
                new_arch = 'SYNCHRONIZED'
            elif fbr_p >= 75:
                new_arch = 'REVERSAL_PRONE'
            else:
                new_arch = 'INDEPENDENT'

            db.execute("UPDATE sector_dna SET sector_archetype=? WHERE sector=?",
                       (new_arch, sector_name))

    db.commit()

    # ── Cross-sector contagion: which sector pairs often co-explode? ──
    print("  → Computing sector contagion network...", flush=True)
    sectors_built = [r['sector'] for r in db.execute("SELECT sector FROM sector_dna").fetchall()]

    # Build a date→sector(s) map for co-explosion detection
    date_sectors = defaultdict(set)
    for e in all_expl:
        date_sectors[e['explosion_date']].add(e['sector'])

    # For each ordered pair (A,B), count dates where A exploded and B exploded within CONTAGION_WINDOW_DAYS
    co_counts = defaultdict(lambda: {'n': 0, 'delays': []})
    sector_dates = defaultdict(list)
    for e in all_expl:
        sector_dates[e['sector']].append(e['explosion_date'])

    for src in sectors_built:
        src_dates = sorted(set(sector_dates[src]))
        for tgt in sectors_built:
            if src == tgt:
                continue
            tgt_dates_set = set(sector_dates[tgt])
            n_co = 0
            delays = []
            for sd in src_dates:
                sd_dt = datetime.fromisoformat(sd)
                for delta in range(1, CONTAGION_WINDOW_DAYS + 1):
                    candidate = (sd_dt + timedelta(days=delta)).date().isoformat()
                    if candidate in tgt_dates_set:
                        n_co += 1
                        delays.append(delta)
                        break
            if n_co >= 3:
                avg_delay = float(np.mean(delays))
                co_rate = _pct(n_co, len(src_dates))
                db.execute("""INSERT OR REPLACE INTO sector_contagion
                    (source_sector, target_sector, n_co_explosions, avg_delay_days, co_rate_pct, updated_at)
                    VALUES (?,?,?,?,?,?)""",
                    (src, tgt, n_co, round(avg_delay, 1), round(co_rate, 1), _now()))
    db.commit()

    n_edges = db.execute("SELECT COUNT(*) FROM sector_contagion").fetchone()[0]
    arch_dist = dict(db.execute("SELECT sector_archetype, COUNT(*) FROM sector_dna GROUP BY sector_archetype").fetchall())
    findings = [
        f"{n_sectors} sector DNA profiles built",
        f"Archetype distribution: {arch_dist}",
        f"{n_edges} sector contagion edges detected",
    ]
    dt = time.time() - t0
    _log_stage(db, 'sector_dna', dt, n_sectors, findings)
    print(f"  ✅ Sector DNA: {n_sectors} sectors | {n_edges} contagion edges | {arch_dist}", flush=True)
    return {
        'sectors_built': n_sectors,
        'archetype_dist': arch_dist,
        'contagion_edges': n_edges,
        'elapsed_sec': round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — EXPLOSION ANATOMY
# ══════════════════════════════════════════════════════════════════════════════
def discover_explosion_anatomy(db, params=None):
    t0 = time.time()
    print("  [Stage 3] Discovering explosion anatomy and archetypes...", flush=True)

    # Build comprehensive feature matrix for ALL explosions
    rows = db.execute("""
        SELECT e.symbol, e.explosion_date, e.direction, e.return_1d, e.return_3d, e.return_5d,
               e.explosion_class, e.sector, e.pre1_bb_width,
               p.compression_days, p.compression_depth, p.ignition_speed, p.cascade_score, p.physics_type,
               f.reversal_pct
        FROM explosive_moves e
        LEFT JOIN market_physics p ON e.symbol=p.symbol AND e.explosion_date=p.explosion_date
        LEFT JOIN false_breakout_anatomy f ON e.symbol=f.symbol AND e.explosion_date=f.explosion_date
        ORDER BY e.explosion_date
    """).fetchall()
    rows = [dict(r) for r in rows]
    n_total = len(rows)
    print(f"  → {n_total:,} explosions loaded for anatomy analysis", flush=True)

    # Regime lookup
    regime_by_date = {}
    for r in db.execute("SELECT date, regime FROM regime_history").fetchall():
        regime_by_date[r['date']] = r['regime']

    for r in rows:
        r['regime'] = regime_by_date.get(r['explosion_date'], 'UNKNOWN')

    # ── Build feature matrix ──
    def encode_row(r):
        return [
            r['pre1_bb_width'] or 0.0,
            r['compression_days'] or 0.0,
            r['ignition_speed'] or 1.0,
            r['cascade_score'] or 0.5,
            r['return_1d'] or 0.0,
            r['return_5d'] or 0.0,
            REGIME_CODE.get(r['regime'], 0.1),
            PHYSICS_CODE.get(r['physics_type'], 0.0),
            EXPL_CODE.get(r['explosion_class'], 0.5),
        ]

    X_raw = np.array([encode_row(r) for r in rows], dtype=float)
    X_norm, _, _ = _normalize_features(X_raw)

    # ── K-means clustering ──
    print(f"  → Clustering {n_total:,} explosions into {N_EXPLOSION_ARCHETYPES} archetypes...", flush=True)
    labels, centers = _kmeans(X_norm, k=N_EXPLOSION_ARCHETYPES, max_iter=60)

    # ── Characterize each archetype ──
    db.execute("DELETE FROM explosion_archetypes")
    archetype_findings = []

    ARCHETYPE_NAMES = ['COMPRESSION_BURST', 'MOMENTUM_SURGE', 'REVERSAL_EXPLOSION',
                       'SUSTAINED_EXPANSION', 'LOW_ENERGY_MOVE']

    for kid in range(N_EXPLOSION_ARCHETYPES):
        members = [rows[i] for i, l in enumerate(labels) if l == kid]
        n_k = len(members)
        pct = _pct(n_k, n_total)
        if n_k == 0:
            continue

        # Centroid in raw feature space
        X_k = np.array([encode_row(m) for m in members])
        centroid = X_k.mean(axis=0)

        r1s = [m['return_1d'] for m in members if m['return_1d']]
        r5s = [m['return_5d'] for m in members if m['return_5d']]
        avg_r1 = float(np.mean(r1s)) if r1s else 0
        avg_r5 = float(np.mean(r5s)) if r5s else 0

        # Dominant categoricals
        dom_physics = _mode([m['physics_type'] for m in members if m['physics_type']])
        dom_class   = _mode([m['explosion_class'] for m in members if m['explosion_class']])
        dom_regime  = _mode([m['regime'] for m in members if m['regime'] != 'UNKNOWN'])
        dom_sector  = _mode([m['sector'] for m in members if m['sector']])
        fbr = _pct(sum(1 for m in members if (m.get('reversal_pct') or 0) > 0), n_k)

        # Name archetype by dominant characteristics
        avg_bbw = centroid[0]
        avg_comp = centroid[1]
        avg_ign  = centroid[2]
        if avg_bbw < 0.06 and avg_comp > 5:
            aname = 'COMPRESSION_BURST'
        elif avg_ign > 1.5 and avg_r1 > 0.08:
            aname = 'MOMENTUM_SURGE'
        elif fbr > 35:
            aname = 'REVERSAL_EXPLOSION'
        elif avg_r5 > avg_r1 * 1.2:
            aname = 'SUSTAINED_EXPANSION'
        else:
            aname = 'LOW_ENERGY_MOVE'

        sig = {
            'pre_bbw < 0.07': int(avg_bbw < 0.07),
            'compression_days > 10': int(avg_comp > 10),
            'fast_ignition': int(avg_ign > 1.3),
            'sustained (5d > 1d)': int(avg_r5 > avg_r1 * 0.9),
            'high_cascade': int(centroid[3] > 0.5),
        }

        db.execute("""INSERT OR REPLACE INTO explosion_archetypes
            (archetype_id, archetype_name, n_members, pct_of_total,
             centroid_pre_bbw, centroid_compression_days, centroid_ignition_speed, centroid_cascade_score,
             centroid_return_1d, centroid_return_5d,
             dominant_physics_type, dominant_explosion_class, dominant_regime, dominant_sector,
             avg_return_1d, avg_return_5d, false_breakout_rate, signature_features, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kid, aname, n_k, round(pct, 1),
             round(float(centroid[0]), 5), round(float(centroid[1]), 1),
             round(float(centroid[2]), 3), round(float(centroid[3]), 3),
             round(float(centroid[4]), 4), round(float(centroid[5]), 4),
             dom_physics, dom_class, dom_regime, dom_sector,
             round(avg_r1, 4), round(avg_r5, 4), round(fbr, 1),
             json.dumps(sig), _now()))

        archetype_findings.append(f"Archetype {kid} ({aname}): {n_k} members ({pct}%), "
                                   f"avg_r1={avg_r1:.1%}, avg_r5={avg_r5:.1%}, fbr={fbr:.0f}%")

    db.commit()

    # ── Universal signature detection ──
    print("  → Detecting universal signatures...", flush=True)
    db.execute("DELETE FROM explosion_signatures")

    all_r1 = float(np.mean([r['return_1d'] for r in rows if r['return_1d']])) if rows else 0

    signatures = [
        ('Tight_BBW_05', 'Pre-explosion BBW < 0.05 (extreme compression)', 'BBW', 'UNIVERSAL', None,
         lambda r: (r['pre1_bb_width'] or 0) < 0.05),
        ('Tight_BBW_08', 'Pre-explosion BBW < 0.08 (moderate compression)', 'BBW', 'UNIVERSAL', None,
         lambda r: (r['pre1_bb_width'] or 0) < 0.08),
        ('Long_Compression', 'Compression ≥ 10 days before explosion', 'COMPRESSION', 'UNIVERSAL', None,
         lambda r: (r.get('compression_days') or 0) >= 10),
        ('Very_Long_Compression', 'Compression ≥ 20 days before explosion', 'COMPRESSION', 'UNIVERSAL', None,
         lambda r: (r.get('compression_days') or 0) >= 20),
        ('Physics_Compression_Release', 'Physics type = COMPRESSION_RELEASE', 'PHYSICS', 'UNIVERSAL', None,
         lambda r: r.get('physics_type') == 'COMPRESSION_RELEASE'),
        ('Physics_Structural', 'Physics type = STRUCTURAL_EXPANSION', 'PHYSICS', 'UNIVERSAL', None,
         lambda r: r.get('physics_type') == 'STRUCTURAL_EXPANSION'),
        ('Extreme_Or_Large_Class', 'Explosion class = EXTREME or LARGE', 'CLASS', 'UNIVERSAL', None,
         lambda r: r.get('explosion_class') in ('EXTREME', 'LARGE')),
        ('Extreme_Class', 'Explosion class = EXTREME', 'CLASS', 'UNIVERSAL', None,
         lambda r: r.get('explosion_class') == 'EXTREME'),
        ('Bull_Regime', 'Market regime = BULL during explosion', 'REGIME', 'REGIME', 'BULL',
         lambda r: r['regime'] == 'BULL'),
        ('High_Cascade', 'Cascade score > 0.6 (contagion effect)', 'CASCADE', 'UNIVERSAL', None,
         lambda r: (r.get('cascade_score') or 0) > 0.6),
        ('Fast_Ignition', 'Ignition speed > 1.5 (fast acceleration)', 'IGNITION', 'UNIVERSAL', None,
         lambda r: (r.get('ignition_speed') or 0) > 1.5),
        ('Sustained_Move', '5-day return > 80% of 1-day return (sustained)', 'MOMENTUM', 'UNIVERSAL', None,
         lambda r: (r.get('return_5d') or 0) > (r.get('return_1d') or 0) * 0.8),
        ('No_Reversal', 'False breakout rate (reversal_pct = 0)', 'REVERSAL', 'UNIVERSAL', None,
         lambda r: not ((r.get('reversal_pct') or 0) > 0)),
    ]

    for sig_name, desc, ftype, scope, scope_val, cond_fn in signatures:
        try:
            matching = [r for r in rows if cond_fn(r)]
            n_match = len(matching)
            prev = _pct(n_match, n_total)
            r1_match = float(np.mean([r['return_1d'] for r in matching if r['return_1d']])) if matching else 0
            uplift = r1_match / all_r1 - 1 if all_r1 > 0 else 0
            if prev >= SIGNATURE_MIN_PREV * 100:
                scope_final = 'UNIVERSAL' if prev >= UNIVERSAL_THRESHOLD * 100 else scope
                db.execute("""INSERT OR REPLACE INTO explosion_signatures
                    (signature_name, description, scope, scope_value, prevalence_pct,
                     avg_return_uplift, feature_type, condition_expr, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sig_name, desc, scope_final, scope_val, round(prev, 1),
                     round(uplift * 100, 1), ftype, sig_name, _now()))
        except Exception:
            pass
    db.commit()

    n_sigs = db.execute("SELECT COUNT(*) FROM explosion_signatures").fetchone()[0]
    n_universal = db.execute("SELECT COUNT(*) FROM explosion_signatures WHERE scope='UNIVERSAL'").fetchone()[0]

    findings = archetype_findings + [f"{n_sigs} signatures found ({n_universal} universal)"]
    dt = time.time() - t0
    _log_stage(db, 'explosion_anatomy', dt, n_total, findings)

    archetypes_out = [dict(r) for r in db.execute("""
        SELECT archetype_id, archetype_name, n_members, pct_of_total,
               avg_return_1d, avg_return_5d, false_breakout_rate, dominant_physics_type
        FROM explosion_archetypes ORDER BY n_members DESC""").fetchall()]
    sigs_out = [dict(r) for r in db.execute("""
        SELECT signature_name, scope, prevalence_pct, avg_return_uplift, feature_type
        FROM explosion_signatures ORDER BY prevalence_pct DESC""").fetchall()]

    print(f"  ✅ Explosion anatomy: {N_EXPLOSION_ARCHETYPES} archetypes | {n_sigs} signatures ({n_universal} universal)", flush=True)
    return {
        'total_explosions': n_total,
        'archetypes': archetypes_out,
        'n_signatures': n_sigs,
        'n_universal': n_universal,
        'signatures': sigs_out[:15],
        'elapsed_sec': round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — UNIVERSAL LAWS
# ══════════════════════════════════════════════════════════════════════════════
def discover_universal_laws(db, params=None):
    t0 = time.time()
    print("  [Stage 4] Computing universal law statistics...", flush=True)

    patterns = [dict(r) for r in db.execute(
        "SELECT id, pattern_name, direction, support_rate, feature, threshold, operator FROM precursor_patterns"
    ).fetchall()]

    # Global directional random baselines:
    # P(UP explosion | random bar) and P(DOWN explosion | random bar)
    total_bars   = db.execute("SELECT COUNT(*) FROM ohlcv_history").fetchone()[0] or 1
    n_up_expl    = db.execute("SELECT COUNT(*) FROM explosive_moves WHERE direction='UP'").fetchone()[0]
    n_down_expl  = db.execute("SELECT COUNT(*) FROM explosive_moves WHERE direction='DOWN'").fetchone()[0]
    baseline_up   = _safe_div(n_up_expl,   total_bars, 0.05)
    baseline_down = _safe_div(n_down_expl, total_bars, 0.05)
    # Fallback overall
    random_baseline = _safe_div(n_up_expl + n_down_expl, total_bars, 0.05)

    for pat in patterns:
        pid = pat['id']

        # All counterfactual events for this pattern
        events = db.execute("""SELECT outcome, regime, sector, precursor_date
                                FROM counterfactual_events WHERE pattern_id=?""", (pid,)).fetchall()
        events = [dict(e) for e in events]
        n_act = len(events)
        n_hits = sum(1 for e in events if e['outcome'] == 'HIT')
        n_fa   = sum(1 for e in events if e['outcome'] == 'FALSE_ALARM')

        precision = _safe_div(n_hits, n_act, 0.0)
        recall    = float(pat['support_rate']) if pat['support_rate'] else 0.0
        far       = _safe_div(n_fa, n_act, 0.0)
        f1        = _safe_div(2 * precision * recall, precision + recall, 0.0)
        # Directional random baseline
        pat_dir = pat.get('direction', 'UP')
        dir_baseline = baseline_up if pat_dir == 'UP' else baseline_down
        info_gain = _information_gain(precision, max(dir_baseline, 0.01))
        prec_vs_random = _safe_div(precision, max(dir_baseline, 0.001), 1.0)
        beats_random = int(precision > dir_baseline + 0.01)

        # Regime dependency
        regime_prec = {}
        for regime in ['BULL', 'BEAR', 'CHOPPY']:
            re = [e for e in events if e['regime'] == regime]
            if len(re) >= 10:
                rh = sum(1 for e in re if e['outcome'] == 'HIT')
                regime_prec[regime] = _safe_div(rh, len(re))
        is_regime_dep = int(len(regime_prec) >= 2 and max(regime_prec.values()) - min(regime_prec.values()) > 0.08)
        best_regime  = max(regime_prec, key=regime_prec.get) if regime_prec else None
        worst_regime = min(regime_prec, key=regime_prec.get) if regime_prec else None
        best_rp  = regime_prec.get(best_regime, 0)
        worst_rp = regime_prec.get(worst_regime, 0)
        regime_std = float(np.std(list(regime_prec.values()))) if len(regime_prec) >= 2 else 0.0

        # Sector dependency
        sector_prec = {}
        sector_events = defaultdict(list)
        for e in events:
            if e['sector']:
                sector_events[e['sector']].append(e)
        for s, se in sector_events.items():
            if len(se) >= 10:
                sh = sum(1 for e in se if e['outcome'] == 'HIT')
                sector_prec[s] = _safe_div(sh, len(se))
        is_sector_dep = int(len(sector_prec) >= 2 and max(sector_prec.values()) - min(sector_prec.values()) > 0.08)
        best_sector  = max(sector_prec, key=sector_prec.get) if sector_prec else None
        best_sector_p = sector_prec.get(best_sector, 0)

        # Temporal OOS split (first half vs second half by date)
        dates = sorted(e['precursor_date'] for e in events if e['precursor_date'])
        if len(dates) >= 20:
            split = dates[len(dates)//2]
            early_ev = [e for e in events if e['precursor_date'] and e['precursor_date'] < split]
            late_ev  = [e for e in events if e['precursor_date'] and e['precursor_date'] >= split]
            early_prec = _safe_div(sum(1 for e in early_ev if e['outcome']=='HIT'), len(early_ev))
            late_prec  = _safe_div(sum(1 for e in late_ev  if e['outcome']=='HIT'), len(late_ev))
            oos_gap    = late_prec - early_prec
        else:
            early_prec = late_prec = precision
            oos_gap = 0.0

        # Stability class from existing stability curves
        stab_row = db.execute("""SELECT stability_class FROM law_stability_curves
                                  WHERE pattern_id=? LIMIT 1""", (pid,)).fetchone()
        stab_class = (stab_row['stability_class'] if stab_row and stab_row['stability_class']
                      else ('STABLE' if abs(oos_gap) < 0.03 else 'DRIFTING'))
        stab_score = max(0.0, 1.0 - abs(oos_gap) * 5)

        # Law status (compare against directional baseline)
        if precision >= dir_baseline * 1.5 and stab_class != 'DRIFTING':
            law_status = 'DOMINANT'
        elif precision >= dir_baseline * 1.2:
            law_status = 'ACTIVE'
        elif precision >= dir_baseline * 0.9:
            law_status = 'DEGRADING'
        else:
            law_status = 'ARCHIVED'

        db.execute("""INSERT OR REPLACE INTO universal_laws_p16
            (pattern_id, pattern_name, direction,
             precision, recall, false_alarm_rate, f1_score, n_activations, n_hits,
             random_baseline_precision, precision_vs_random, information_gain,
             is_regime_dependent, best_regime, best_regime_precision, worst_regime, worst_regime_precision,
             regime_stability_score, is_sector_dependent, best_sector, best_sector_precision,
             stability_class, early_precision, late_precision, oos_gap,
             beats_random, law_status, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, pat['pattern_name'], pat['direction'],
             round(precision, 4), round(recall, 4), round(far, 4), round(f1, 4),
             n_act, n_hits,
             round(random_baseline, 4), round(prec_vs_random, 3), round(info_gain, 4),
             is_regime_dep, best_regime, round(best_rp, 4), worst_regime, round(worst_rp, 4),
             round(regime_std, 4), is_sector_dep, best_sector, round(best_sector_p, 4),
             stab_class, round(early_prec, 4), round(late_prec, 4), round(oos_gap, 4),
             beats_random, law_status, _now()))

    db.commit()

    laws = [dict(r) for r in db.execute("""SELECT pattern_name, direction, precision,
             precision_vs_random, f1_score, law_status, is_regime_dependent, beats_random,
             best_regime, best_regime_precision, oos_gap
             FROM universal_laws_p16 ORDER BY precision DESC""").fetchall()]
    dominant = [l for l in laws if l['law_status'] == 'DOMINANT']
    active   = [l for l in laws if l['law_status'] == 'ACTIVE']

    findings = [f"{len(laws)} laws analyzed", f"{len(dominant)} DOMINANT | {len(active)} ACTIVE",
                f"UP baseline={baseline_up:.1%} | DOWN baseline={baseline_down:.1%}",
                f"Beat random: {sum(1 for l in laws if l['beats_random'])} / {len(laws)}"]
    dt = time.time() - t0
    _log_stage(db, 'universal_laws', dt, len(laws), findings)
    print(f"  ✅ Universal laws: {len(laws)} analyzed | {len(dominant)} DOMINANT | random_baseline={random_baseline:.1%}", flush=True)
    return {
        'n_laws':           len(laws),
        'random_baseline':  round(random_baseline, 4),
        'dominant':         len(dominant),
        'active':           len(active),
        'laws':             laws,
        'elapsed_sec':      round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — MEMORY CONSOLIDATION (KNOWLEDGE GRAPH)
# ══════════════════════════════════════════════════════════════════════════════
def consolidate_memory(db, params=None):
    t0 = time.time()
    print("  [Stage 5] Consolidating memory into knowledge graph...", flush=True)

    ts = _now()
    db.execute("DELETE FROM knowledge_graph_nodes")
    db.execute("DELETE FROM knowledge_graph_edges")

    def upsert_node(nid, ntype, nname, props, importance):
        db.execute("""INSERT OR REPLACE INTO knowledge_graph_nodes
            (node_id, node_type, node_name, properties_json, importance_score, updated_at)
            VALUES (?,?,?,?,?,?)""",
            (nid, ntype, nname, json.dumps(props), round(importance, 3), ts))

    def upsert_edge(src, tgt, etype, weight, evidence, label=''):
        try:
            db.execute("""INSERT OR REPLACE INTO knowledge_graph_edges
                (source_id, target_id, edge_type, weight, evidence_count, label, updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (src, tgt, etype, round(weight, 3), evidence, label, ts))
        except Exception:
            pass

    # ── Law nodes ──
    laws = [dict(r) for r in db.execute("SELECT * FROM universal_laws_p16").fetchall()]
    for law in laws:
        imp = min(1.0, law['precision'] / 0.15)  # scale to [0,1]
        upsert_node(f"law_{law['pattern_id']}", 'law', law['pattern_name'],
                    {'direction': law['direction'], 'precision': law['precision'],
                     'status': law['law_status'], 'f1': law['f1_score']}, imp)

    # ── Regime nodes ──
    for regime in ['BULL', 'BEAR', 'CHOPPY', 'UNKNOWN']:
        n = db.execute("SELECT COUNT(*) FROM regime_history WHERE regime=?", (regime,)).fetchone()[0]
        upsert_node(f"regime_{regime}", 'regime', regime, {'n_occurrences': n}, n / 500)

    # ── Sector nodes ──
    sectors = [dict(r) for r in db.execute("SELECT * FROM sector_dna").fetchall()]
    for sec in sectors:
        imp = min(1.0, sec['total_explosions'] / 500)
        upsert_node(f"sector_{sec['sector'].replace(' ', '_')}", 'sector', sec['sector'],
                    {'total_explosions': sec['total_explosions'],
                     'sync_pct': sec['synchronization_pct'],
                     'archetype': sec['sector_archetype']}, imp)

    # ── Explosion archetype nodes ──
    archetypes = [dict(r) for r in db.execute("SELECT * FROM explosion_archetypes").fetchall()]
    for a in archetypes:
        imp = a['pct_of_total'] / 100.0
        upsert_node(f"archetype_{a['archetype_id']}", 'archetype', a['archetype_name'],
                    {'n_members': a['n_members'], 'avg_r1': a['avg_return_1d'],
                     'avg_r5': a['avg_return_5d'], 'fbr': a['false_breakout_rate']}, imp)

    # ── Failure cause nodes ──
    fail_causes = ['LOW_MOMENTUM', 'REGIME_MISMATCH', 'LIQUIDITY_COLLAPSE',
                   'COMPETING_SIGNALS', 'OVEREXTENDED_RSI', 'SECTOR_DIVERGENCE', 'MACRO_PRESSURE']
    for fc in fail_causes:
        n = db.execute("SELECT COUNT(*) FROM failure_reconstruction WHERE primary_cause=?", (fc,)).fetchone()[0]
        upsert_node(f"failure_{fc}", 'failure_cause', fc, {'n_failures': n}, min(1.0, n / 5000))

    # ── Stock DNA nodes (top 50 by explosion rate) ──
    top_stocks = [dict(r) for r in db.execute("""SELECT symbol, archetype, explosion_rate_pct,
                   explosion_count, drift_direction, sector FROM stock_dna
                   ORDER BY explosion_rate_pct DESC LIMIT 50""").fetchall()]
    for s in top_stocks:
        imp = min(1.0, s['explosion_rate_pct'] / 5.0)
        upsert_node(f"stock_{s['symbol']}", 'stock', s['symbol'],
                    {'archetype': s['archetype'], 'rate': s['explosion_rate_pct'],
                     'drift': s['drift_direction']}, imp)

    # ── Edges: Law → Regime (law performs best in this regime) ──
    for law in laws:
        if law['best_regime']:
            w = (law['best_regime_precision'] or 0) - (law['precision'] or 0)
            upsert_edge(f"law_{law['pattern_id']}", f"regime_{law['best_regime']}",
                        'strongest_in', max(0, w), 10, f"best_precision={law['best_regime_precision']:.3f}")

    # ── Edges: Law → Sector (law co-occurs with explosions in this sector) ──
    for law in laws:
        if law['best_sector']:
            upsert_edge(f"law_{law['pattern_id']}",
                        f"sector_{law['best_sector'].replace(' ', '_')}",
                        'dominant_in', law['best_sector_precision'] or 0, 10,
                        f"precision={law['best_sector_precision']:.3f}")

    # ── Edges: Stock → Sector (membership) ──
    for s in top_stocks:
        if s['sector']:
            upsert_edge(f"stock_{s['symbol']}",
                        f"sector_{s['sector'].replace(' ', '_')}",
                        'belongs_to', 1.0, 1, 'member')

    # ── Edges: Sector → Sector (contagion) ──
    contagions = [dict(r) for r in db.execute("""SELECT source_sector, target_sector, co_rate_pct, avg_delay_days
                                                  FROM sector_contagion WHERE co_rate_pct >= 10""").fetchall()]
    for c in contagions:
        src_id = f"sector_{c['source_sector'].replace(' ', '_')}"
        tgt_id = f"sector_{c['target_sector'].replace(' ', '_')}"
        upsert_edge(src_id, tgt_id, 'propagates_to', c['co_rate_pct'] / 100.0,
                    c['co_rate_pct'], f"delay={c['avg_delay_days']}d")

    # ── Edges: Law → Archetype (law precedes this explosion type) ──
    # Link each law to its most common explosion archetype
    for law in laws:
        pid = law['pattern_id']
        # Find explosions that were hits for this law and their archetype assignments
        hit_symbols_dates = [dict(r) for r in db.execute("""
            SELECT symbol, precursor_date FROM counterfactual_events
            WHERE pattern_id=? AND outcome='HIT' LIMIT 500""", (pid,)).fetchall()]
        # Look up their archetypes from stock_dna dominant class
        if hit_symbols_dates:
            upsert_edge(f"law_{pid}", "archetype_0", 'precedes', law['precision'] or 0,
                        len(hit_symbols_dates), 'precursor→explosion')

    # ── Edges: Failure cause → Law ──
    fail_stats = db.execute("""SELECT law_id, primary_cause, COUNT(*) n
                                FROM failure_reconstruction GROUP BY law_id, primary_cause""").fetchall()
    for r in fail_stats:
        lid = r['law_id']
        if lid:
            upsert_edge(f"failure_{r['primary_cause']}", f"law_{lid}",
                        'suppresses', r['n'] / 1000.0, r['n'],
                        f"cause={r['primary_cause']}")

    db.commit()

    n_nodes = db.execute("SELECT COUNT(*) FROM knowledge_graph_nodes").fetchone()[0]
    n_edges = db.execute("SELECT COUNT(*) FROM knowledge_graph_edges").fetchone()[0]
    node_types = dict(db.execute("SELECT node_type, COUNT(*) FROM knowledge_graph_nodes GROUP BY node_type").fetchall())

    findings = [f"Knowledge graph: {n_nodes} nodes | {n_edges} edges", f"Node types: {node_types}"]
    dt = time.time() - t0
    _log_stage(db, 'consolidate_memory', dt, n_nodes + n_edges, findings)
    print(f"  ✅ Memory: {n_nodes} nodes | {n_edges} edges | types={node_types}", flush=True)
    return {
        'nodes': n_nodes,
        'edges': n_edges,
        'node_types': node_types,
        'elapsed_sec': round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — SELF-EVOLUTION
# ══════════════════════════════════════════════════════════════════════════════
def self_evolve(db, params=None):
    t0 = time.time()
    print("  [Stage 6] Self-evolution: threshold competition...", flush=True)

    patterns = [dict(r) for r in db.execute(
        "SELECT id, pattern_name, direction, feature, threshold, operator FROM precursor_patterns"
    ).fetchall()]

    # Direction-aware baselines
    total_bars_se = db.execute("SELECT COUNT(*) FROM ohlcv_history").fetchone()[0] or 1
    n_up_se   = db.execute("SELECT COUNT(*) FROM explosive_moves WHERE direction='UP'").fetchone()[0]
    n_down_se = db.execute("SELECT COUNT(*) FROM explosive_moves WHERE direction='DOWN'").fetchone()[0]
    baseline_se = {'UP': _safe_div(n_up_se, total_bars_se, 0.05),
                   'DOWN': _safe_div(n_down_se, total_bars_se, 0.05)}
    random_baseline = _safe_div(n_up_se + n_down_se, total_bars_se, 0.05)

    PERTURBATIONS = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
    n_competition = 0
    promoted = []
    archived = []

    for pat in patterns:
        pid = pat['id']
        base_prec_row = db.execute("SELECT precision FROM universal_laws_p16 WHERE pattern_id=?", (pid,)).fetchone()
        base_prec = base_prec_row['precision'] if base_prec_row else 0.0

        base_thresh = float(pat['threshold'] or 0)
        if base_thresh == 0:
            continue

        best_variant_prec = base_prec
        best_variant_thresh = base_thresh

        dir_base = baseline_se.get(pat.get('direction', 'UP'), random_baseline)

        for mult in PERTURBATIONS:
            variant_thresh = base_thresh * mult
            # operator stored as 'lt', 'gt', 'range', '<=', '>='
            operator = (pat['operator'] or 'lt').lower().replace('<=', 'lt').replace('>=', 'gt')
            if operator in ('lt', '<', 'le'):
                n_var = db.execute("""SELECT COUNT(*) FROM counterfactual_events
                    WHERE pattern_id=? AND feature_value <= ?""", (pid, variant_thresh)).fetchone()[0]
                n_var_hit = db.execute("""SELECT COUNT(*) FROM counterfactual_events
                    WHERE pattern_id=? AND feature_value <= ? AND outcome='HIT'""", (pid, variant_thresh)).fetchone()[0]
            elif operator in ('gt', '>', 'ge'):
                n_var = db.execute("""SELECT COUNT(*) FROM counterfactual_events
                    WHERE pattern_id=? AND feature_value >= ?""", (pid, variant_thresh)).fetchone()[0]
                n_var_hit = db.execute("""SELECT COUNT(*) FROM counterfactual_events
                    WHERE pattern_id=? AND feature_value >= ? AND outcome='HIT'""", (pid, variant_thresh)).fetchone()[0]
            else:
                # 'range' or unknown — use all events for this pattern
                n_var = db.execute("SELECT COUNT(*) FROM counterfactual_events WHERE pattern_id=?",
                                   (pid,)).fetchone()[0]
                n_var_hit = db.execute("SELECT COUNT(*) FROM counterfactual_events WHERE pattern_id=? AND outcome='HIT'",
                                       (pid,)).fetchone()[0]

            if n_var < 10:
                continue

            var_prec = _safe_div(n_var_hit, n_var)
            improvement = var_prec - base_prec
            beats_base  = int(improvement >= 0.02)
            beats_rand  = int(var_prec > dir_base + 0.01)

            db.execute("""INSERT INTO law_competition
                (pattern_id, pattern_name, direction, variant_name, variant_threshold,
                 variant_precision, base_precision, improvement_pp, beats_base,
                 random_baseline, beats_random, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, pat['pattern_name'], pat['direction'],
                 f"x{mult:.2f}", round(variant_thresh, 5),
                 round(var_prec, 4), round(base_prec, 4), round(improvement * 100, 2),
                 beats_base, round(random_baseline, 4), beats_rand, _now()))
            n_competition += 1

            if var_prec > best_variant_prec:
                best_variant_prec = var_prec
                best_variant_thresh = variant_thresh

        # Update law status based on competition result
        if best_variant_prec > base_prec * 1.10 and best_variant_thresh != base_thresh:
            promoted.append({'pattern': pat['pattern_name'], 'direction': pat['direction'],
                             'new_prec': round(best_variant_prec, 4),
                             'best_thresh': round(best_variant_thresh, 5)})

        if base_prec < random_baseline:
            archived.append(pat['pattern_name'])

    db.commit()

    # Compute competition summary
    best_variants = [dict(r) for r in db.execute("""
        SELECT pattern_name, direction, variant_threshold, variant_precision, improvement_pp, beats_base
        FROM law_competition WHERE beats_base=1
        ORDER BY improvement_pp DESC LIMIT 20""").fetchall()]

    findings = [
        f"{n_competition} threshold variants tested",
        f"{len(promoted)} patterns with improvement ≥10%",
        f"{len(archived)} patterns below random baseline",
    ]
    dt = time.time() - t0
    _log_stage(db, 'self_evolve', dt, n_competition, findings)
    print(f"  ✅ Self-evolution: {n_competition} variants | {len(promoted)} improvements | {len(archived)} below-random", flush=True)
    return {
        'variants_tested':   n_competition,
        'improved_patterns': promoted,
        'below_random':      archived,
        'best_variants':     best_variants[:10],
        'elapsed_sec':       round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 7 — GENERATE COMPREHENSIVE REPORT
# ══════════════════════════════════════════════════════════════════════════════
def generate_report(db, params=None):
    t0 = time.time()
    today = datetime.utcnow().strftime('%Y-%m-%d')
    report_file = RPT_DIR / f'cognition_report_{today}.txt'

    def q(sql, *args):
        try:
            return db.execute(sql, args).fetchall()
        except Exception:
            return []

    def qs(sql, *args):
        try:
            r = db.execute(sql, args).fetchone()
            return r[0] if r else 0
        except Exception:
            return 0

    lines = []
    w = lines.append
    sep = lambda: w('═' * 70)

    w('')
    sep()
    w('  EGX AUTONOMOUS MARKET COGNITION REPORT — Phase 16')
    w(f'  Generated: {datetime.utcnow().isoformat()} UTC')
    sep()
    w('')

    # ── Section 1: System Overview ──
    w('  ═══ SECTION 1: SYSTEM INTELLIGENCE OVERVIEW ═══')
    w('')
    w(f"  Stock DNA profiles:       {qs('SELECT COUNT(*) FROM stock_dna')}")
    w(f"  Sector DNA profiles:      {qs('SELECT COUNT(*) FROM sector_dna')}")
    w(f"  Explosion archetypes:     {qs('SELECT COUNT(*) FROM explosion_archetypes')}")
    w(f"  Universal signatures:     {qs('SELECT COUNT(*) FROM explosion_signatures')}")
    w(f"  Universal laws analyzed:  {qs('SELECT COUNT(*) FROM universal_laws_p16')}")
    w(f"  Knowledge graph nodes:    {qs('SELECT COUNT(*) FROM knowledge_graph_nodes')}")
    w(f"  Knowledge graph edges:    {qs('SELECT COUNT(*) FROM knowledge_graph_edges')}")
    w(f"  Total experience events:  {qs('SELECT COUNT(*) FROM market_experience'):,}")
    w(f"  Total explosions studied: {qs('SELECT COUNT(*) FROM explosive_moves'):,}")
    w('')

    # ── Section 2: Stock DNA Intelligence ──
    w('  ═══ SECTION 2: STOCK DNA INTELLIGENCE ═══')
    w('')
    arch_dist = q("SELECT archetype, COUNT(*) n FROM stock_dna GROUP BY archetype ORDER BY n DESC")
    for row in arch_dist:
        pct = _pct(row[1], qs('SELECT COUNT(*) FROM stock_dna'))
        w(f"  {row[0]:<25} {row[1]:>4} stocks ({pct:.1f}%)")
    w('')
    w('  Top EXPLOSIVE_FAST stocks:')
    for r in q("SELECT symbol, explosion_rate_pct, avg_return_1d, cycle_period_days FROM stock_dna WHERE archetype='EXPLOSIVE_FAST' ORDER BY explosion_rate_pct DESC LIMIT 10"):
        w(f"    {r[0]:<10} rate={r[1]:.2f}%/100d  avg_r1={r[2]:.1%}  cycle={r[3]:.0f}d")
    w('')
    w('  Behavioral drift (recent 50% vs early 50%):')
    w(f"    Accelerating: {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'ACCELERATING')} stocks")
    w(f"    Increasing:   {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'INCREASING')} stocks")
    w(f"    Stable:       {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'STABLE')} stocks")
    w(f"    Decreasing:   {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'DECREASING')} stocks")
    w(f"    Fading:       {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'FADING')} stocks")
    w('')

    # ── Section 3: Sector DNA Intelligence ──
    w('  ═══ SECTION 3: SECTOR DNA INTELLIGENCE ═══')
    w('')
    for r in q("SELECT sector, total_explosions, synchronization_pct, avg_return_1d, sector_archetype, dominant_regime FROM sector_dna ORDER BY total_explosions DESC"):
        w(f"  {r[0]:<30} {r[1]:>5} expl  sync={r[2]:.0f}%  avg_r1={r[3]:.1%}  [{r[4]}|{r[5]}]")
    w('')
    w('  Sector contagion network (top pairs by co-explosion rate):')
    for r in q("SELECT source_sector, target_sector, co_rate_pct, avg_delay_days FROM sector_contagion ORDER BY co_rate_pct DESC LIMIT 10"):
        w(f"    {r[0]:<25} → {r[1]:<25}  rate={r[2]:.1f}%  delay={r[3]:.1f}d")
    w('')

    # ── Section 4: Explosion Anatomy ──
    w('  ═══ SECTION 4: EXPLOSION ANATOMY & ARCHETYPES ═══')
    w('')
    for r in q("SELECT archetype_name, n_members, pct_of_total, avg_return_1d, avg_return_5d, false_breakout_rate, dominant_physics_type FROM explosion_archetypes ORDER BY n_members DESC"):
        w(f"  [{r[0]:<25}] {r[1]:>5} ({r[2]:.1f}%)  r1={r[3]:.1%} r5={r[4]:.1%}  fbr={r[5]:.0f}%  phys={r[6]}")
    w('')
    w('  Universal explosion signatures:')
    for r in q("SELECT signature_name, scope, prevalence_pct, avg_return_uplift FROM explosion_signatures WHERE scope='UNIVERSAL' ORDER BY prevalence_pct DESC"):
        uplift_str = f"+{r[3]:.1f}%" if r[3] >= 0 else f"{r[3]:.1f}%"
        w(f"    ✓ {r[0]:<35} prevalence={r[2]:.1f}%  return_uplift={uplift_str}")
    w('')

    # ── Section 5: Universal Laws ──
    w('  ═══ SECTION 5: UNIVERSAL LAW ANALYSIS ═══')
    w('')
    n_ohlcv_r   = qs('SELECT COUNT(*) FROM ohlcv_history')
    n_up_r      = qs("SELECT COUNT(*) FROM explosive_moves WHERE direction='UP'")
    n_down_r    = qs("SELECT COUNT(*) FROM explosive_moves WHERE direction='DOWN'")
    up_base_r   = n_up_r / max(n_ohlcv_r, 1)
    down_base_r = n_down_r / max(n_ohlcv_r, 1)
    w(f"  Random baseline — UP: {up_base_r:.1%} | DOWN: {down_base_r:.1%}")
    w(f"  (P(explosion in correct direction | random activation)")
    w('')
    for r in q("SELECT pattern_name, direction, precision, recall, f1_score, precision_vs_random, law_status, beats_random, oos_gap, best_regime FROM universal_laws_p16 ORDER BY precision DESC"):
        regime_str = f"best={r[9]}" if r[9] else "no_dep"
        oos_str = f"OOS={r[8]:+.3f}"
        w(f"  {'▲' if r[1]=='UP' else '▼'} {(r[0]+' ('+r[1]+')'):<38} P={r[2]:.3f}  R={r[3]:.3f}  F1={r[4]:.3f}  x{r[5]:.1f}rand  [{r[6]}]  {oos_str}  {regime_str}")
    w('')

    # ── Section 6: Failure Intelligence ──
    w('  ═══ SECTION 6: FAILURE INTELLIGENCE ═══')
    w('')
    total_fail = qs('SELECT COUNT(*) FROM failure_reconstruction')
    w(f"  Total failures analyzed: {total_fail:,}")
    w('')
    for r in q("SELECT primary_cause, COUNT(*) n FROM failure_reconstruction GROUP BY primary_cause ORDER BY n DESC"):
        pct = _pct(r[1], total_fail)
        w(f"  {r[0]:<30} {r[1]:>7,}  ({pct:.1f}%)")
    w('')

    # ── Section 7: Self-Evolution Competition ──
    w('  ═══ SECTION 7: SELF-EVOLUTION COMPETITION ═══')
    w('')
    n_comp = qs('SELECT COUNT(*) FROM law_competition')
    n_beat = qs('SELECT COUNT(*) FROM law_competition WHERE beats_base=1')
    w(f"  Threshold variants tested: {n_comp:,}")
    w(f"  Variants beating baseline: {n_beat} ({_pct(n_beat, n_comp):.1f}%)")
    w('')
    w('  Top improvement candidates:')
    for r in q("""
        SELECT pattern_name, direction, variant_threshold, improvement_pp
        FROM law_competition
        WHERE beats_base=1
          AND pattern_name IS NOT NULL
          AND direction IS NOT NULL
          AND variant_threshold IS NOT NULL
          AND improvement_pp IS NOT NULL
        ORDER BY improvement_pp DESC LIMIT 8
    """):
        w(f"    {r[0]} ({r[1]})  thresh={r[2]:.5f}  improvement=+{r[3]:.2f}pp")
    w('')

    # ── Section 8: Knowledge Graph Summary ──
    w('  ═══ SECTION 8: KNOWLEDGE GRAPH SUMMARY ═══')
    w('')
    for r in q("SELECT node_type, COUNT(*) n FROM knowledge_graph_nodes GROUP BY node_type ORDER BY n DESC"):
        w(f"  {r[0]:<20} {r[1]} nodes")
    w('')
    for r in q("SELECT edge_type, COUNT(*) n FROM knowledge_graph_edges GROUP BY edge_type ORDER BY n DESC"):
        w(f"  {r[0]:<25} {r[1]} edges")
    w('')

    # ── Section 9: Regime & Memory ──
    w('  ═══ SECTION 9: REGIME & STRUCTURAL MEMORY ═══')
    w('')
    for r in q("SELECT regime, COUNT(*) n FROM regime_history GROUP BY regime ORDER BY n DESC"):
        pct = _pct(r[1], qs('SELECT COUNT(*) FROM regime_history'))
        w(f"  {r[0]:<15} {r[1]:>5} sessions ({pct:.1f}%)")
    w('')
    w('  Market memory snapshot:')
    mm = q("SELECT * FROM market_memory ORDER BY snapshot_date DESC LIMIT 1")
    if mm:
        r = dict(mm[0])
        w(f"    Stocks: {r.get('total_stocks', '?')}  regime: {r.get('regime', '?')}")
        w(f"    explosion_rate: {r.get('explosion_rate', '?')}  dominant_archetype: {r.get('dominant_archetype', '?')}")
    w('')

    # ── Section 10: Scientific Synthesis ──
    w('  ═══ SECTION 10: SCIENTIFIC SYNTHESIS & KEY DISCOVERIES ═══')
    w('')
    w('  VALIDATED LAWS:')
    for r in q("SELECT pattern_name, direction, precision, f1_score FROM universal_laws_p16 WHERE law_status IN ('DOMINANT','ACTIVE') ORDER BY precision DESC"):
        w(f"    ✓ {r[0]} ({r[1]}): precision={r[2]:.3f}, F1={r[3]:.3f}")
    w('')
    w('  DEGRADING / ARCHIVED LAWS:')
    for r in q("SELECT pattern_name, direction, precision, oos_gap FROM universal_laws_p16 WHERE law_status IN ('DEGRADING','ARCHIVED') ORDER BY oos_gap ASC"):
        w(f"    ⚠ {r[0]} ({r[1]}): precision={r[2]:.3f}, OOS_gap={r[3]:+.3f}")
    w('')
    w('  STRUCTURAL DISCOVERIES:')
    w(f"    • {qs('SELECT COUNT(*) FROM explosion_archetypes')} distinct explosion archetypes identified")
    w(f"    • {qs('SELECT COUNT(*) FROM explosion_signatures WHERE scope=?', 'UNIVERSAL')} universal pre-explosion signatures")
    w(f"    • {qs('SELECT COUNT(*) FROM sector_contagion WHERE co_rate_pct >= 20')} strong sector contagion pairs")
    w(f"    • {qs('SELECT COUNT(*) FROM stock_dna WHERE drift_direction=?', 'ACCELERATING')} stocks with accelerating explosion rates")
    w(f"    • {qs('SELECT COUNT(*) FROM stock_dna WHERE archetype=?', 'EXPLOSIVE_FAST')} stocks classified EXPLOSIVE_FAST")
    w('')

    # Regime-specific law performance
    w('  REGIME-SPECIFIC LAW PERFORMANCE:')
    for r in q("SELECT pattern_name, direction, best_regime, best_regime_precision, worst_regime, worst_regime_precision FROM universal_laws_p16 WHERE is_regime_dependent=1"):
        w(f"    {r[0]} ({r[1]}): best in {r[2]} ({r[3]:.3f}) | worst in {r[4]} ({r[5]:.3f})")
    w('')

    sep()
    w(f'  ✅ Phase 16 Cognition Report — {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC')
    sep()
    w('')

    # Write file
    report_text = '\n'.join(lines)
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_text)

    dt = time.time() - t0
    print(f"  ✅ Report: {report_file}", flush=True)
    return {
        'report_file': str(report_file),
        'sections': 10,
        'lines': len(lines),
        'elapsed_sec': round(dt, 1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# FULL COGNITION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
def full_cognition(db, params=None):
    t0_total = time.time()
    print("\n  🧠 EGX Phase 16 — Full Cognition Pipeline", flush=True)
    print("  ─────────────────────────────────────────", flush=True)

    stage_results = {}
    key_findings = []

    stages = [
        ('stock_dna',          build_stock_dna,            '1/7 Stock DNA'),
        ('sector_dna',         build_sector_dna,           '2/7 Sector DNA'),
        ('explosion_anatomy',  discover_explosion_anatomy, '3/7 Explosion Anatomy'),
        ('universal_laws',     discover_universal_laws,    '4/7 Universal Laws'),
        ('consolidate_memory', consolidate_memory,         '5/7 Memory Consolidation'),
        ('self_evolve',        self_evolve,                '6/7 Self-Evolution'),
        ('generate_report',    generate_report,            '7/7 Report'),
    ]

    for key, fn, label in stages:
        print(f"\n  [{label}]", flush=True)
        t_s = time.time()
        try:
            result = fn(db, params)
            stage_results[key] = result
            stage_results[key]['_elapsed'] = round(time.time() - t_s, 1)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            stage_results[key] = {'error': str(e), 'traceback': err[-500:]}
            print(f"  ❌ {label} error: {e}", flush=True)

    # ── Key findings extraction ──
    sd = stage_results.get('stock_dna', {})
    sec = stage_results.get('sector_dna', {})
    ea = stage_results.get('explosion_anatomy', {})
    ul = stage_results.get('universal_laws', {})
    mm = stage_results.get('consolidate_memory', {})
    ev = stage_results.get('self_evolve', {})

    if sd.get('profiles_built'):
        ad = sd.get('archetype_dist', {})
        key_findings.append(f"{sd['profiles_built']} stock DNA profiles: {ad}")
    if sec.get('sectors_built'):
        key_findings.append(f"{sec['sectors_built']} sectors | {sec.get('contagion_edges',0)} contagion edges")
    if ea.get('n_universal'):
        key_findings.append(f"{ea['n_universal']} universal explosion signatures discovered")
    if ul.get('laws'):
        dom = [l['pattern_name'] for l in ul['laws'] if l['law_status'] == 'DOMINANT']
        key_findings.append(f"Dominant laws: {dom}")
    if mm.get('nodes'):
        key_findings.append(f"Knowledge graph: {mm['nodes']} nodes, {mm['edges']} edges")
    if ev.get('improved_patterns'):
        key_findings.append(f"{len(ev['improved_patterns'])} patterns improved by threshold evolution")

    total_elapsed = round(time.time() - t0_total, 1)

    return {
        'total_elapsed':        total_elapsed,
        'stages_completed':     sum(1 for v in stage_results.values() if 'error' not in v),
        'stages_total':         len(stages),
        'key_findings':         key_findings,
        'stock_dna':            stage_results.get('stock_dna', {}),
        'sector_dna':           stage_results.get('sector_dna', {}),
        'explosion_anatomy':    stage_results.get('explosion_anatomy', {}),
        'universal_laws':       stage_results.get('universal_laws', {}),
        'knowledge_graph':      stage_results.get('consolidate_memory', {}),
        'self_evolution':       stage_results.get('self_evolve', {}),
        'report':               stage_results.get('generate_report', {}),
        'report_file':          stage_results.get('generate_report', {}).get('report_file'),
    }

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
COMMANDS = {
    'status':           get_status,
    'stock_dna':        build_stock_dna,
    'sector_dna':       build_sector_dna,
    'explosion_anatomy': discover_explosion_anatomy,
    'universal_laws':   discover_universal_laws,
    'consolidate_memory': consolidate_memory,
    'self_evolve':      self_evolve,
    'generate_report':  generate_report,
    'full_cognition':   full_cognition,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'status'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}. Available: {list(COMMANDS)}'}))
        sys.exit(1)

    db = get_db()
    ensure_schema(db)

    try:
        result = COMMANDS[cmd](db, params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({'error': str(e), 'traceback': traceback.format_exc()[-1000:]}))
        sys.exit(1)
    finally:
        db.close()
