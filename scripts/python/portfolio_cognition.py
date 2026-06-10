#!/usr/bin/env python3
"""
Phase 32: Portfolio Cognition System — EGX Egyptian Exchange
============================================================
Transforms the system from stock-intelligent to capital-intelligent.
Decides not just WHAT to buy, but HOW MUCH, WITH WHOM, and at WHAT RISK.

Invocation: python portfolio_cognition.py <command> '<json_params>'
Output: last stdout line = valid JSON

Commands:
  orchestrate           — Build full portfolio allocation from intelligence scores
  size_positions        — Compute dynamic Kelly-inspired position sizes
  risk_budget           — Systemic risk breakdown for a portfolio
  adaptive_concentration — How concentrated should we be today?
  build_portfolio       — Full pipeline: orchestrate + risk_budget + adaptive_concentration
  build_full            — Alias for build_portfolio
"""

import os
import sys
import sqlite3
import json
import math
import statistics
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta

# ── Paths ─────────────────────────────────────────────────────────────────────

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')

# ── EGX-Specific Parameters ───────────────────────────────────────────────────

COMMISSION_BPS    = 50      # 0.5% per side
SPREAD_BPS        = 50      # estimated spread
CIRCUIT_BREAKER   = 0.10    # 10% daily limit
T_PLUS            = 3       # settlement days
MIN_POSITION_EGP  = 5000    # minimum position size in EGP
MAX_SECTOR_PCT    = 0.35    # max 35% in one sector
MAX_SINGLE_PCT    = 0.15    # max 15% in one stock

TIER_MULT = {
    'DEEP':     1.0,
    'MID':      0.8,
    'LIQUID':   0.9,
    'MODERATE': 0.6,
    'SHALLOW':  0.5,
    'THIN':     0.4,
    'ILLIQUID': 0.2,
}

REGIME_CONCENTRATION = {
    'TRENDING_UP':   {'max_positions': 4,  'max_single': 0.20, 'max_sector': 0.45, 'mode': 'FOCUSED'},
    'TRENDING_DOWN': {'max_positions': 6,  'max_single': 0.15, 'max_sector': 0.35, 'mode': 'DEFENSIVE'},
    'VOLATILE':      {'max_positions': 8,  'max_single': 0.10, 'max_sector': 0.25, 'mode': 'DIVERSIFIED'},
    'SIDEWAYS':      {'max_positions': 5,  'max_single': 0.18, 'max_sector': 0.40, 'mode': 'BALANCED'},
    'TRANSITION':    {'max_positions': 10, 'max_single': 0.08, 'max_sector': 0.20, 'mode': 'DEFENSIVE'},
    'CRISIS':        {'max_positions': 0,  'max_single': 0.00, 'max_sector': 0.00, 'mode': 'CASH'},
}

# Systemic risk weights
RISK_WEIGHTS = {
    'sector_concentration_hhi': 0.25,
    'causal_cluster_risk':       0.20,
    'regime_alignment_risk':     0.20,
    'liquidity_stress_risk':     0.20,
    'failure_overlap_risk':      0.15,
}

COMMANDS = {
    'orchestrate',
    'size_positions',
    'risk_budget',
    'adaptive_concentration',
    'build_portfolio',
    'build_full',
}

# ── DB Setup ──────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS portfolio_allocations (
        allocation_id        TEXT PRIMARY KEY,
        date                 TEXT,
        capital              REAL,
        n_positions          INTEGER,
        portfolio_json       TEXT,
        sector_exposure_json TEXT,
        regime               TEXT,
        concentration_mode   TEXT,
        portfolio_score      REAL,
        systemic_risk_score  REAL,
        computed_at          TEXT
    );

    CREATE TABLE IF NOT EXISTS position_sizes (
        symbol          TEXT,
        date            TEXT,
        kelly_fraction  REAL,
        adj_fraction    REAL,
        amount_egp      REAL,
        rationale       TEXT,
        computed_at     TEXT,
        PRIMARY KEY (symbol, date)
    );
    """)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def now_str():
    return datetime.utcnow().isoformat()


def today_str():
    return datetime.utcnow().strftime('%Y-%m-%d')


def make_id(*parts):
    raw = '_'.join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def emit(result):
    print(json.dumps(result, default=str))


# ── Data Readers ──────────────────────────────────────────────────────────────

def read_intelligence_scores(db):
    """Read intelligence_scores table. Returns list of dicts."""
    try:
        rows = db.execute("""
            SELECT symbol, intelligence_score, execution_component,
                   regime_component, causal_component, primary_driver,
                   percentile_rank
            FROM intelligence_scores
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def read_liquidity_profiles(db):
    """Read liquidity_profiles table. Returns dict keyed by symbol."""
    result = {}
    try:
        rows = db.execute("""
            SELECT symbol, tier, avg_daily_volume, spread_cost_bps, slippage_est_bps
            FROM liquidity_profiles
        """).fetchall()
        for r in rows:
            result[r['symbol']] = dict(r)
    except Exception:
        pass
    # Also try symbol_liquidity_profile (alternate name from execution_reality_engine)
    if not result:
        try:
            rows = db.execute("""
                SELECT symbol,
                       liquidity_tier AS tier,
                       avg_daily_volume,
                       avg_spread_est_bps AS spread_cost_bps,
                       0 AS slippage_est_bps
                FROM symbol_liquidity_profile
            """).fetchall()
            for r in rows:
                result[r['symbol']] = dict(r)
        except Exception:
            pass
    return result


def read_explosion_readiness(db):
    """Read explosion_readiness table. Returns dict keyed by symbol."""
    result = {}
    try:
        rows = db.execute("""
            SELECT symbol, readiness_score, sector
            FROM explosion_readiness
        """).fetchall()
        for r in rows:
            result[r['symbol']] = dict(r)
    except Exception:
        pass
    return result


def read_stock_dna(db):
    """Read stock_dna table. Returns dict keyed by symbol."""
    result = {}
    try:
        rows = db.execute("""
            SELECT symbol, sector, archetype, energy_score, trend_strength
            FROM stock_dna
        """).fetchall()
        for r in rows:
            result[r['symbol']] = dict(r)
    except Exception:
        pass
    return result


def read_failure_intelligence(db):
    """Read failure_intelligence table. Returns dict keyed by symbol."""
    result = {}
    try:
        rows = db.execute("""
            SELECT symbol, archetype, confidence
            FROM failure_intelligence
        """).fetchall()
        for r in rows:
            result[r['symbol']] = dict(r)
    except Exception:
        pass
    return result


def read_causal_edges(db):
    """
    Read causal_edges or causal_chains table.
    Returns set of (source, target) tuples — both directions for easy lookup.
    """
    edges = set()
    try:
        rows = db.execute("SELECT source, target FROM causal_edges").fetchall()
        for r in rows:
            edges.add((r['source'], r['target']))
            edges.add((r['target'], r['source']))
        return edges
    except Exception:
        pass
    try:
        rows = db.execute("SELECT source, target FROM causal_chains").fetchall()
        for r in rows:
            edges.add((r['source'], r['target']))
            edges.add((r['target'], r['source']))
    except Exception:
        pass
    return edges


def read_current_regime(db):
    """
    Read most recent regime from market_regime or regime_history.
    Returns (regime_label, confidence).
    """
    for table in ('market_regime', 'regime_history'):
        try:
            row = db.execute(f"""
                SELECT regime_label, regime_confidence
                FROM {table}
                ORDER BY date DESC
                LIMIT 1
            """).fetchone()
            if row:
                return row['regime_label'], safe(row['regime_confidence'], 0.5)
        except Exception:
            continue
    return 'SIDEWAYS', 0.5


def read_ohlcv_last_n(db, symbol, n=20):
    """
    Read last n closing prices for a symbol from ohlcv table.
    Returns list of floats (closes), most recent last.
    """
    closes = []
    for table in ('ohlcv', 'ohlcv_history'):
        try:
            rows = db.execute(f"""
                SELECT close FROM {table}
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
            """, (symbol, n)).fetchall()
            if rows:
                closes = [safe(r['close']) for r in reversed(rows)]
                break
        except Exception:
            continue
    return closes


# ── Volatility Computation ────────────────────────────────────────────────────

def compute_20d_vol(db, symbol):
    """
    Compute 20-day realized volatility (std of daily log returns).
    Falls back to sector-average default if insufficient data.
    """
    closes = read_ohlcv_last_n(db, symbol, 22)
    if len(closes) < 5:
        return 0.025  # EGX default ~2.5% daily vol
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            ret = math.log(closes[i] / closes[i - 1])
            returns.append(ret)
    if len(returns) < 3:
        return 0.025
    try:
        vol = statistics.stdev(returns)
        return max(vol, 0.005)  # floor at 0.5%
    except statistics.StatisticsError:
        return 0.025


def compute_returns_vector(db, symbol, n=20):
    """Return list of daily returns for a symbol."""
    closes = read_ohlcv_last_n(db, symbol, n + 2)
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            ret = (closes[i] - closes[i - 1]) / closes[i - 1]
            returns.append(ret)
    return returns[-n:] if len(returns) >= n else returns


# ── Sector Lookup ─────────────────────────────────────────────────────────────

def get_symbol_sector(symbol, dna_map, readiness_map):
    """Get sector for a symbol from available sources."""
    if symbol in dna_map and dna_map[symbol].get('sector'):
        return dna_map[symbol]['sector']
    if symbol in readiness_map and readiness_map[symbol].get('sector'):
        return readiness_map[symbol]['sector']
    return 'Unknown'


# ── Kelly Position Sizing ─────────────────────────────────────────────────────

def compute_kelly_fraction(intelligence_score, vol):
    """
    Simplified Kelly fraction.
    edge = (intelligence_score / 100) - 0.5
    kelly_f = edge / (vol^2 + 0.01)
    Capped between 2%-20%.
    """
    edge = (intelligence_score / 100.0) - 0.5
    kelly_f = edge / (vol ** 2 + 0.01)
    kelly_f = max(0.02, min(kelly_f, 0.20))
    return kelly_f


def apply_liquidity_adjustment(kelly_f, tier):
    """Apply liquidity tier multiplier to Kelly fraction."""
    mult = TIER_MULT.get(tier, 0.5)
    return kelly_f * mult


def build_size_rationale(symbol, intelligence_score, tier, sector, kelly_f, adj_f):
    """Build a human-readable rationale string for a position size."""
    parts = []
    if tier in ('DEEP', 'LIQUID'):
        parts.append(f"{tier} liquidity")
    elif tier in ('SHALLOW', 'THIN', 'ILLIQUID'):
        parts.append(f"Limited by {tier} liquidity")
    else:
        parts.append(f"{tier} liquidity")

    if intelligence_score >= 80:
        parts.append("high score")
    elif intelligence_score >= 65:
        parts.append("solid score")
    else:
        parts.append("moderate score")

    if sector and sector != 'Unknown':
        parts.append(f"{sector} sector")

    kelly_pct = round(kelly_f * 100, 1)
    adj_pct   = round(adj_f * 100, 1)
    parts.append(f"Kelly={kelly_pct}% adj={adj_pct}%")
    return ', '.join(parts)


# ── Causal Correlation Penalty ────────────────────────────────────────────────

def build_causal_correlation_matrix(symbols, causal_edges):
    """
    Build a correlation penalty matrix.
    If a causal edge exists between A and B → penalty = 0.3 (reduce combined weight).
    Otherwise penalty = 0.
    """
    n = len(symbols)
    matrix = {}
    for i, s1 in enumerate(symbols):
        for j, s2 in enumerate(symbols):
            if i == j:
                matrix[(s1, s2)] = 0.0
            elif (s1, s2) in causal_edges:
                matrix[(s1, s2)] = 0.3
            else:
                matrix[(s1, s2)] = 0.0
    return matrix


def causal_penalty_for_symbol(symbol, portfolio_symbols, causal_matrix):
    """Sum of causal penalties for symbol relative to already-selected symbols."""
    return sum(causal_matrix.get((symbol, s), 0.0) for s in portfolio_symbols if s != symbol)


# ── HHI Calculation ───────────────────────────────────────────────────────────

def compute_hhi(sector_weights_dict):
    """
    Herfindahl-Hirschman Index for sector concentration.
    Returns value in [0, 1]. Higher = more concentrated.
    """
    total = sum(sector_weights_dict.values())
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in sector_weights_dict.values())


# ── Tier Liquidity Score ──────────────────────────────────────────────────────

TIER_STRESS_SCORE = {
    'DEEP':     0.0,
    'LIQUID':   0.1,
    'MID':      0.2,
    'MODERATE': 0.4,
    'SHALLOW':  0.6,
    'THIN':     0.8,
    'ILLIQUID': 1.0,
}


def tier_stress(tier):
    return TIER_STRESS_SCORE.get(tier, 0.5)


# ── Portfolio Score ───────────────────────────────────────────────────────────

def compute_portfolio_score(portfolio_items):
    """
    Weighted average intelligence score across portfolio items.
    Returns 0-100 float.
    """
    if not portfolio_items:
        return 0.0
    total_weight = sum(item.get('weight', 0.0) for item in portfolio_items)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(
        item.get('intelligence_score', 50.0) * item.get('weight', 0.0)
        for item in portfolio_items
    )
    return round(weighted_sum / total_weight, 2)


# ── Total Friction ────────────────────────────────────────────────────────────

def compute_total_friction_bps(portfolio_items, liquidity_map):
    """
    Weighted average friction (spread + commission) across portfolio.
    """
    if not portfolio_items:
        return COMMISSION_BPS * 2 + SPREAD_BPS
    total_weight = sum(item.get('weight', 0.0) for item in portfolio_items)
    if total_weight <= 0:
        return COMMISSION_BPS * 2 + SPREAD_BPS
    total = 0.0
    for item in portfolio_items:
        symbol = item['symbol']
        lp = liquidity_map.get(symbol, {})
        spread = safe(lp.get('spread_cost_bps'), SPREAD_BPS)
        slip   = safe(lp.get('slippage_est_bps'), 0.0)
        friction = COMMISSION_BPS * 2 + spread + slip
        total += friction * item.get('weight', 0.0)
    return round(total / total_weight, 1)


# ── Risk Contribution ─────────────────────────────────────────────────────────

def compute_risk_contribution(weight, vol, portfolio_vol=0.02):
    """
    Simplified marginal risk contribution.
    RC_i ≈ w_i * vol_i / portfolio_vol
    Capped at 1.0.
    """
    if portfolio_vol <= 0:
        return 0.0
    rc = (weight * vol) / portfolio_vol
    return round(min(rc, 1.0), 4)


# ── Command: orchestrate ──────────────────────────────────────────────────────

def cmd_orchestrate(params):
    capital       = safe(params.get('capital', 100000))
    max_positions = safe_int(params.get('max_positions', 10))
    risk_tolerance = params.get('risk_tolerance', 'MODERATE').upper()

    db = get_db()
    try:
        # 1. Read regime
        regime_label, regime_conf = read_current_regime(db)

        # 2. Apply regime-based concentration rules
        regime_rules = REGIME_CONCENTRATION.get(regime_label, REGIME_CONCENTRATION['SIDEWAYS'])
        concentration_mode = regime_rules['mode']
        effective_max_pos  = min(max_positions, regime_rules['max_positions'])
        effective_max_single = regime_rules['max_single']
        effective_max_sector = regime_rules['max_sector']

        # Risk tolerance modifier
        if risk_tolerance == 'AGGRESSIVE':
            effective_max_single = min(effective_max_single * 1.2, 0.25)
            effective_max_sector = min(effective_max_sector * 1.2, 0.50)
        elif risk_tolerance == 'CONSERVATIVE':
            effective_max_single = effective_max_single * 0.7
            effective_max_sector = effective_max_sector * 0.7

        if regime_label == 'CRISIS':
            result = {
                'capital': capital,
                'n_positions': 0,
                'portfolio': [],
                'sector_exposure': {},
                'regime': regime_label,
                'concentration_mode': 'CASH',
                'total_friction_bps': 0,
                'portfolio_score': 0,
                'warnings': ['CRISIS regime: remain in cash'],
            }
            db.close()
            return result

        # 3. Read all data sources
        intel_rows    = read_intelligence_scores(db)
        liquidity_map = read_liquidity_profiles(db)
        dna_map       = read_stock_dna(db)
        readiness_map = read_explosion_readiness(db)
        causal_edges  = read_causal_edges(db)

        # 4. Filter candidates: score > 50, not ILLIQUID
        candidates = []
        for row in intel_rows:
            symbol = row.get('symbol', '')
            score  = safe(row.get('intelligence_score', 0))
            lp     = liquidity_map.get(symbol, {})
            tier   = lp.get('tier', 'SHALLOW')
            if score > 50 and tier != 'ILLIQUID':
                candidates.append({
                    'symbol': symbol,
                    'intelligence_score': score,
                    'tier': tier,
                    'sector': get_symbol_sector(symbol, dna_map, readiness_map),
                    'lp': lp,
                })

        # Sort by intelligence score descending
        candidates.sort(key=lambda x: x['intelligence_score'], reverse=True)

        # 5. Build causal correlation matrix for top-N candidates
        top_symbols = [c['symbol'] for c in candidates[:max_positions * 3]]
        causal_matrix = build_causal_correlation_matrix(top_symbols, causal_edges)

        # 6. Greedy selection with causal penalty and sector cap
        selected = []
        sector_weights = defaultdict(float)
        warnings = []

        for candidate in candidates:
            if len(selected) >= effective_max_pos:
                break

            symbol = candidate['symbol']
            sector = candidate['sector']
            score  = candidate['intelligence_score']
            tier   = candidate['tier']
            lp     = candidate['lp']

            # Compute volatility for sizing
            vol = compute_20d_vol(db, symbol)

            # Kelly fraction
            kelly_f = compute_kelly_fraction(score, vol)
            adj_f   = apply_liquidity_adjustment(kelly_f, tier)

            # Tentative weight
            weight = adj_f

            # Apply max single-stock cap
            weight = min(weight, effective_max_single)

            # Check sector cap — tentative
            current_sector_w = sector_weights.get(sector, 0.0)
            if current_sector_w + weight > effective_max_sector:
                # Trim to fit within sector cap
                available = effective_max_sector - current_sector_w
                if available < 0.02:
                    warnings.append(f"Skipping {symbol}: {sector} sector at cap")
                    continue
                weight = available

            # Causal penalty: reduce weight if closely linked to existing selections
            selected_symbols = [s['symbol'] for s in selected]
            penalty = causal_penalty_for_symbol(symbol, selected_symbols, causal_matrix)
            weight = weight * (1.0 - min(penalty, 0.5))

            # Minimum position check
            if weight * capital < MIN_POSITION_EGP:
                continue

            # Compute approximate price and shares
            closes = read_ohlcv_last_n(db, symbol, 1)
            last_price = closes[-1] if closes else 1.0
            amount_egp = weight * capital
            n_shares_approx = int(amount_egp / last_price) if last_price > 0 else 0

            # Risk contribution
            portfolio_vol = 0.02  # simplified portfolio vol
            rc = compute_risk_contribution(weight, vol, portfolio_vol)

            rationale = build_size_rationale(symbol, score, tier, sector, kelly_f, adj_f)

            selected.append({
                'symbol': symbol,
                'weight': round(weight, 4),
                'amount_egp': round(amount_egp, 2),
                'n_shares_approx': n_shares_approx,
                'intelligence_score': round(score, 2),
                'size_rationale': rationale,
                'risk_contribution': rc,
                'sector': sector,
                'tier': tier,
                '_vol': vol,
            })

            sector_weights[sector] += weight

        # 7. Normalize weights to sum to 1.0 (scale to capital)
        total_weight = sum(s['weight'] for s in selected)
        if total_weight > 0 and total_weight != 1.0:
            scale = 1.0 / total_weight
            for s in selected:
                s['weight'] = round(s['weight'] * scale, 4)
                s['amount_egp'] = round(s['weight'] * capital, 2)

        # 8. Check sector exposures against warnings
        sector_exposure_final = defaultdict(float)
        for s in selected:
            sector_exposure_final[s['sector']] += s['weight']

        for sec, w in sector_exposure_final.items():
            if w > MAX_SECTOR_PCT:
                warnings.append(f"{sec} exposure at limit: {round(w * 100)}%")

        # Remove internal fields from output
        portfolio_output = []
        for s in selected:
            item = {k: v for k, v in s.items() if not k.startswith('_')}
            item.pop('tier', None)
            item.pop('sector', None)
            portfolio_output.append(item)

        portfolio_score = compute_portfolio_score(portfolio_output)
        total_friction  = compute_total_friction_bps(portfolio_output, liquidity_map)

        # 9. Persist to DB
        allocation_id = make_id('orchestrate', today_str(), str(capital))
        sector_exp_clean = dict(sector_exposure_final)
        try:
            db.execute("""
                INSERT OR REPLACE INTO portfolio_allocations
                (allocation_id, date, capital, n_positions, portfolio_json,
                 sector_exposure_json, regime, concentration_mode,
                 portfolio_score, systemic_risk_score, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                allocation_id,
                today_str(),
                capital,
                len(selected),
                json.dumps(portfolio_output),
                json.dumps(sector_exp_clean),
                regime_label,
                concentration_mode,
                portfolio_score,
                None,
                now_str(),
            ))
            db.commit()
        except Exception:
            pass

        return {
            'capital': capital,
            'n_positions': len(selected),
            'portfolio': portfolio_output,
            'sector_exposure': {k: round(v, 4) for k, v in sector_exp_clean.items()},
            'regime': regime_label,
            'concentration_mode': concentration_mode,
            'total_friction_bps': total_friction,
            'portfolio_score': portfolio_score,
            'warnings': warnings,
        }

    finally:
        db.close()


# ── Command: size_positions ───────────────────────────────────────────────────

def cmd_size_positions(params):
    capital = safe(params.get('capital', 100000))
    symbols = params.get('symbols', [])

    if not symbols:
        return {'error': 'No symbols provided', 'positions': []}

    db = get_db()
    try:
        intel_rows    = read_intelligence_scores(db)
        intel_map     = {r['symbol']: r for r in intel_rows}
        liquidity_map = read_liquidity_profiles(db)
        dna_map       = read_stock_dna(db)
        readiness_map = read_explosion_readiness(db)

        positions = []
        today = today_str()

        for symbol in symbols:
            intel = intel_map.get(symbol, {})
            score = safe(intel.get('intelligence_score', 50))
            lp    = liquidity_map.get(symbol, {})
            tier  = lp.get('tier', 'SHALLOW')
            sector = get_symbol_sector(symbol, dna_map, readiness_map)

            vol     = compute_20d_vol(db, symbol)
            kelly_f = compute_kelly_fraction(score, vol)
            adj_f   = apply_liquidity_adjustment(kelly_f, tier)

            # Apply global caps
            adj_f = min(adj_f, MAX_SINGLE_PCT)

            amount_egp = adj_f * capital
            rationale  = build_size_rationale(symbol, score, tier, sector, kelly_f, adj_f)

            pos = {
                'symbol': symbol,
                'kelly_fraction': round(kelly_f, 4),
                'adj_fraction':   round(adj_f, 4),
                'amount_egp':     round(amount_egp, 2),
                'rationale':      rationale,
            }
            positions.append(pos)

            # Persist
            try:
                db.execute("""
                    INSERT OR REPLACE INTO position_sizes
                    (symbol, date, kelly_fraction, adj_fraction, amount_egp, rationale, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, today, kelly_f, adj_f, amount_egp, rationale, now_str()))
            except Exception:
                pass

        db.commit()
        return {'capital': capital, 'positions': positions}

    finally:
        db.close()


# ── Command: risk_budget ──────────────────────────────────────────────────────

def cmd_risk_budget(params):
    symbols = params.get('symbols', [])
    weights = params.get('weights', [])

    if not symbols:
        return {'error': 'No symbols provided'}

    # Align weights length
    if len(weights) != len(symbols):
        # Equal weight fallback
        n = len(symbols)
        weights = [1.0 / n] * n

    total_w = sum(weights)
    if total_w <= 0:
        weights = [1.0 / len(symbols)] * len(symbols)
        total_w = 1.0
    # Normalize
    weights = [w / total_w for w in weights]

    db = get_db()
    try:
        dna_map       = read_stock_dna(db)
        readiness_map = read_explosion_readiness(db)
        liquidity_map = read_liquidity_profiles(db)
        failure_map   = read_failure_intelligence(db)
        causal_edges  = read_causal_edges(db)
        regime_label, regime_conf = read_current_regime(db)

        # ── 1. Sector Concentration HHI ──────────────────────────────────────
        sector_w = defaultdict(float)
        for sym, w in zip(symbols, weights):
            sec = get_symbol_sector(sym, dna_map, readiness_map)
            sector_w[sec] += w

        hhi = compute_hhi(sector_w)
        # Normalize HHI to 0-100 risk scale (1.0 HHI = 100 risk)
        sector_risk_score = round(hhi * 100, 2)

        # ── 2. Causal Cluster Risk ────────────────────────────────────────────
        n = len(symbols)
        if n < 2:
            causal_cluster_raw = 0.0
        else:
            linked_pairs = 0
            total_pairs  = 0
            for i in range(n):
                for j in range(i + 1, n):
                    total_pairs += 1
                    if (symbols[i], symbols[j]) in causal_edges:
                        linked_pairs += 1
            causal_cluster_raw = linked_pairs / total_pairs if total_pairs > 0 else 0.0

        causal_risk_score = round(causal_cluster_raw * 100, 2)

        # ── 3. Regime Alignment Risk ──────────────────────────────────────────
        # Score based on how many positions have positive trend alignment with regime
        regime_rules = REGIME_CONCENTRATION.get(regime_label, REGIME_CONCENTRATION['SIDEWAYS'])
        alignment_scores = []
        for sym, w in zip(symbols, weights):
            dna = dna_map.get(sym, {})
            ts  = safe(dna.get('trend_strength', 0.5))
            es  = safe(dna.get('energy_score', 0.5))

            if regime_label in ('TRENDING_UP',):
                # Aligned if strong trend + energy
                alignment = (ts + es) / 2.0
            elif regime_label in ('TRENDING_DOWN',):
                # Aligned (defensive) if LOW trend/energy
                alignment = 1.0 - (ts + es) / 2.0
            elif regime_label == 'CRISIS':
                alignment = 0.0
            else:
                alignment = 0.5

            alignment_scores.append(alignment * w)

        # Regime alignment risk = 1 - weighted alignment (high = misaligned = risky)
        weighted_alignment = sum(alignment_scores)
        regime_risk_score  = round((1.0 - weighted_alignment) * 100, 2)

        # ── 4. Liquidity Stress Risk ──────────────────────────────────────────
        liq_stress_weighted = 0.0
        for sym, w in zip(symbols, weights):
            lp   = liquidity_map.get(sym, {})
            tier = lp.get('tier', 'SHALLOW')
            liq_stress_weighted += tier_stress(tier) * w

        liquidity_risk_score = round(liq_stress_weighted * 100, 2)

        # ── 5. Failure Overlap Risk ───────────────────────────────────────────
        failure_count = sum(1 for sym in symbols if sym in failure_map)
        failure_overlap_raw = failure_count / len(symbols) if symbols else 0.0
        failure_risk_score  = round(failure_overlap_raw * 100, 2)

        # ── Composite Systemic Risk Score ─────────────────────────────────────
        component_scores = {
            'sector_concentration_hhi':  sector_risk_score,
            'causal_cluster_risk':        causal_risk_score,
            'regime_alignment_risk':      regime_risk_score,
            'liquidity_stress_risk':      liquidity_risk_score,
            'failure_overlap_risk':       failure_risk_score,
        }
        systemic_risk_score = round(sum(
            component_scores[k] * RISK_WEIGHTS[k]
            for k in RISK_WEIGHTS
        ), 2)

        # Risk level classification
        if systemic_risk_score >= 75:
            risk_level = 'CRITICAL'
        elif systemic_risk_score >= 55:
            risk_level = 'HIGH'
        elif systemic_risk_score >= 35:
            risk_level = 'MODERATE'
        else:
            risk_level = 'LOW'

        # ── Recommendations ───────────────────────────────────────────────────
        recommendations = []
        if sector_risk_score > 60:
            top_sector = max(sector_w, key=sector_w.get)
            recommendations.append(
                f"Reduce {top_sector} concentration ({round(sector_w[top_sector]*100)}% of portfolio)"
            )
        if causal_risk_score > 40:
            recommendations.append(
                "Multiple positions share causal linkages — consider reducing correlated holdings"
            )
        if regime_risk_score > 60:
            recommendations.append(
                f"Portfolio misaligned with {regime_label} regime — rebalance toward regime-favored archetypes"
            )
        if liquidity_risk_score > 50:
            recommendations.append(
                "High proportion of thin-liquidity positions — ensure exit capacity before entry"
            )
        if failure_risk_score > 30:
            recommendations.append(
                f"{failure_count} position(s) have active failure intelligence signals"
            )
        if not recommendations:
            recommendations.append("Portfolio risk profile within acceptable parameters")

        return {
            'systemic_risk_score': systemic_risk_score,
            'risk_breakdown': {
                'sector_concentration_hhi':  round(hhi, 4),
                'sector_risk_score':          sector_risk_score,
                'causal_cluster_risk':        causal_risk_score,
                'regime_alignment_risk':      regime_risk_score,
                'liquidity_stress_risk':      liquidity_risk_score,
                'failure_overlap_risk':       failure_risk_score,
                'sector_weights':             {k: round(v, 4) for k, v in sector_w.items()},
                'regime':                     regime_label,
                'causal_linked_pairs':        int(causal_cluster_raw * max((n*(n-1)//2), 1)),
            },
            'risk_level': risk_level,
            'recommendations': recommendations,
        }

    finally:
        db.close()


# ── Command: adaptive_concentration ──────────────────────────────────────────

def cmd_adaptive_concentration(_params):
    db = get_db()
    try:
        regime_label, regime_conf = read_current_regime(db)
        rules = REGIME_CONCENTRATION.get(regime_label, REGIME_CONCENTRATION['SIDEWAYS'])

        mode           = rules['mode']
        max_positions  = rules['max_positions']
        max_single_pct = rules['max_single']
        max_sector_pct = rules['max_sector']

        # Build rationale
        rationale_map = {
            'TRENDING_UP':   "Bull regime: concentrate in high-conviction leaders, accept sector concentration",
            'TRENDING_DOWN': "Bear regime: spread risk, reduce single-stock exposure, hold defensive names",
            'VOLATILE':      "High-volatility regime: diversify broadly, keep positions small to absorb swings",
            'SIDEWAYS':      "Sideways regime: balanced exposure, favor mean-reversion setups",
            'TRANSITION':    "Regime transition: minimize exposure across all sectors until direction clarifies",
            'CRISIS':        "Crisis regime: exit all positions, preserve capital in cash",
        }
        rationale = rationale_map.get(regime_label, "Unknown regime — applying conservative defaults")

        return {
            'regime':           regime_label,
            'regime_confidence': round(regime_conf, 3),
            'concentration_mode': mode,
            'max_positions':    max_positions,
            'max_single_pct':   max_single_pct,
            'max_sector_pct':   max_sector_pct,
            'rationale':        rationale,
        }

    finally:
        db.close()


# ── Command: build_portfolio ──────────────────────────────────────────────────

def cmd_build_portfolio(params):
    capital = safe(params.get('capital', 100000))

    # Step 1: orchestrate
    orch_params = {
        'capital':        capital,
        'max_positions':  10,
        'risk_tolerance': params.get('risk_tolerance', 'MODERATE'),
    }
    orchestration = cmd_orchestrate(orch_params)

    # Step 2: risk_budget on result
    portfolio_items = orchestration.get('portfolio', [])
    risk_params = {
        'symbols': [p['symbol'] for p in portfolio_items],
        'weights': [p['weight']  for p in portfolio_items],
    }
    if risk_params['symbols']:
        risk_analysis = cmd_risk_budget(risk_params)
    else:
        risk_analysis = {
            'systemic_risk_score': 0,
            'risk_level': 'LOW',
            'risk_breakdown': {},
            'recommendations': ['No positions selected'],
        }

    # Step 3: adaptive_concentration
    concentration = cmd_adaptive_concentration({})

    # Step 4: Update portfolio allocation with systemic risk score
    db = get_db()
    try:
        sys_risk = risk_analysis.get('systemic_risk_score', 0)
        allocation_id = make_id('orchestrate', today_str(), str(capital))
        try:
            db.execute("""
                UPDATE portfolio_allocations
                SET systemic_risk_score = ?
                WHERE allocation_id = ?
            """, (sys_risk, allocation_id))
            db.commit()
        except Exception:
            pass
    finally:
        db.close()

    return {
        'capital':            capital,
        'orchestration':      orchestration,
        'risk_analysis':      risk_analysis,
        'concentration':      concentration,
        'summary': {
            'n_positions':          orchestration.get('n_positions', 0),
            'portfolio_score':      orchestration.get('portfolio_score', 0),
            'systemic_risk_score':  risk_analysis.get('systemic_risk_score', 0),
            'risk_level':           risk_analysis.get('risk_level', 'UNKNOWN'),
            'regime':               orchestration.get('regime', 'UNKNOWN'),
            'concentration_mode':   orchestration.get('concentration_mode', 'UNKNOWN'),
            'total_friction_bps':   orchestration.get('total_friction_bps', 0),
            'warnings':             orchestration.get('warnings', []),
            'recommendations':      risk_analysis.get('recommendations', []),
        },
    }


# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMAND_MAP = {
    'orchestrate':            cmd_orchestrate,
    'size_positions':         cmd_size_positions,
    'risk_budget':            cmd_risk_budget,
    'adaptive_concentration': cmd_adaptive_concentration,
    'build_portfolio':        cmd_build_portfolio,
    'build_full':             cmd_build_portfolio,
}


def main():
    if len(sys.argv) < 2:
        emit({'error': 'Usage: portfolio_cognition.py <command> [json_params]'})
        sys.exit(1)

    command = sys.argv[1].strip().lower()

    if command not in COMMAND_MAP:
        emit({
            'error':             f'Unknown command: {command}',
            'available_commands': sorted(COMMAND_MAP.keys()),
        })
        sys.exit(1)

    # Parse params
    params = {}
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            emit({'error': f'Invalid JSON params: {e}'})
            sys.exit(1)

    try:
        result = COMMAND_MAP[command](params)
        emit({'success': True, **result})
    except Exception as e:
        import traceback
        emit({
            'success': False,
            'error':   str(e),
            'trace':   traceback.format_exc(),
        })
        sys.exit(1)


if __name__ == '__main__':
    main()
