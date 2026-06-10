"""
capital_intelligence.py — Phase 48: Capital Intelligence Engine
EGX Autonomous Quant System

Solves the "paralysis problem" — transforms the system from a pure analyst into an
actionable capital allocator using uncertainty-weighted sizing, drawdown cognition,
survival-first allocation, and the Exploration vs Exploitation directive.

Commands:
    compute_exposure      — compute recommended capital allocation based on system state
    size_with_uncertainty — compute position size for a symbol, uncertainty-adjusted
    drawdown_state        — assess current drawdown and response rules
    exploration_budget    — determine E/E regime for today (the anti-paralysis directive)
    capital_report        — full capital intelligence report (exposure + drawdown + E/E)
    build_full            — run capital_report and persist to DB

Usage:
    python capital_intelligence.py <command> '<json_params>'
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone
from collections import defaultdict

# ─── DB ──────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
TODAY = datetime.now(timezone.utc).strftime('%Y-%m-%d')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ─── Capital Rules (Survival-First Constants) ─────────────────────────────────

CAPITAL_RULES = {
    # === Survival-First Allocation ===
    'base_capital':                  100_000,   # EGP — reference portfolio size
    'max_risk_per_trade':            0.02,      # 2% max risk per position
    'max_total_exposure':            0.60,      # never exceed 60% deployed
    'min_cash_reserve':              0.20,      # always keep 20% cash
    'max_simultaneous_positions':    6,         # T+3 constraint

    # === Uncertainty-Weighted Sizing ===
    'full_size_uncertainty_max':     0.35,      # < 35%  → full size
    'half_size_uncertainty_max':     0.55,      # 35-55% → 50% size
    'quarter_size_uncertainty_max':  0.75,      # 55-75% → 25% size
    'halt_uncertainty_threshold':    0.80,      # > 80%  → no new positions

    # === Drawdown Cognition ===
    'drawdown_10_reduce_to':         0.50,      # at -10% drawdown → 50% of normal size
    'drawdown_15_reduce_to':         0.25,      # at -15%          → 25% of normal size
    'drawdown_20_halt':              True,      # at -20%          → complete halt

    # === Exploration vs Exploitation ===
    'exploration_budget_pct':        0.10,      # 10% of capital for exploration
    'exploitation_mii_threshold':    60,        # MII > 60 → full exploitation mode
    'exploration_mii_min':           25,        # MII < 25 → sandbox only, no real capital
    'exploration_uncertainty_max':   0.65,      # uncertainty > 65% → exploration only
}


# ─── DB Helpers ───────────────────────────────────────────────────────────────

def safe_query(conn, sql, params=()):
    """Execute a query; return list of dicts or [] on any error."""
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def safe_scalar(conn, sql, params=(), default=None):
    """Execute a query; return first column of first row or default."""
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default


def read_latest_uncertainty(conn):
    """Read total_uncertainty from uncertainty_reports (latest row)."""
    rows = safe_query(conn, "SELECT total_uncertainty FROM uncertainty_reports ORDER BY id DESC LIMIT 1")
    if rows:
        return rows[0].get('total_uncertainty', 0.5)
    return 0.5   # graceful fallback: medium uncertainty


def read_latest_mii(conn):
    """Read mii from market_intelligence_index (latest row)."""
    rows = safe_query(conn, "SELECT mii FROM market_intelligence_index ORDER BY id DESC LIMIT 1")
    if rows:
        val = rows[0].get('mii', 50.0)
        # mii is stored as 0-1 in some phases, normalise to 0-100
        if val is not None and val <= 1.0:
            val = val * 100.0
        return val if val is not None else 50.0
    return 50.0  # fallback: neutral market


def read_latest_sts(conn):
    """Read sts from system_health_reports (latest row)."""
    rows = safe_query(conn, "SELECT sts FROM system_health_reports ORDER BY id DESC LIMIT 1")
    if rows:
        val = rows[0].get('sts', 50.0)
        if val is not None and val <= 1.0:
            val = val * 100.0
        return val if val is not None else 50.0
    return 50.0


def read_latest_bus_directive(conn):
    """Read directive from bus_state (latest row)."""
    rows = safe_query(conn, "SELECT directive FROM bus_state ORDER BY id DESC LIMIT 1")
    if rows:
        return rows[0].get('directive', 'HOLD')
    return 'HOLD'


def read_latest_drawdown(conn):
    """Read current_drawdown_pct from capital_state (latest row). Default 0.0."""
    try:
        rows = safe_query(conn, "SELECT current_drawdown_pct FROM capital_state ORDER BY id DESC LIMIT 1")
        if rows:
            val = rows[0].get('current_drawdown_pct', 0.0)
            return val if val is not None else 0.0
    except Exception:
        pass
    return 0.0


def read_symbol_intelligence_score(conn, symbol):
    """Read intelligence_score for a specific symbol (latest row)."""
    rows = safe_query(
        conn,
        "SELECT intelligence_score FROM intelligence_scores WHERE symbol=? ORDER BY rowid DESC LIMIT 1",
        (symbol,)
    )
    if rows:
        val = rows[0].get('intelligence_score', None)
        if val is not None and val <= 1.0:
            val = val * 100.0
        return val
    return None


# ─── Factor Calculators ───────────────────────────────────────────────────────

def calc_uncertainty_size_factor(total_uncertainty):
    """Map total_uncertainty (0-1) to a position size multiplier."""
    cr = CAPITAL_RULES
    if total_uncertainty < cr['full_size_uncertainty_max']:         # < 0.35
        return 1.0
    elif total_uncertainty < cr['half_size_uncertainty_max']:       # 0.35-0.55
        return 0.5
    elif total_uncertainty < cr['quarter_size_uncertainty_max']:    # 0.55-0.75
        return 0.25
    elif total_uncertainty >= cr['halt_uncertainty_threshold']:     # > 0.80
        return 0.0
    else:                                                           # 0.75-0.80
        return 0.25


def calc_drawdown_factor(current_drawdown_pct):
    """Map current_drawdown_pct (0-1, positive = loss) to size multiplier."""
    # current_drawdown_pct is stored as a positive fraction (e.g., 0.12 = -12% drawdown)
    dd = abs(current_drawdown_pct)
    if dd < 0.10:
        return 1.0
    elif dd < 0.15:
        return 0.5
    elif dd < 0.20:
        return 0.25
    else:
        return 0.0


def calc_mii_factor(mii):
    """Map MII (0-100) to a size multiplier."""
    cr = CAPITAL_RULES
    if mii > cr['exploitation_mii_threshold']:     # > 60
        return 1.0
    elif mii >= 40:                                # 40-60
        return 0.75
    elif mii >= cr['exploration_mii_min']:         # 25-40
        return 0.5
    else:                                          # < 25
        return 0.0


# ─── Command: compute_exposure ────────────────────────────────────────────────

def compute_exposure(params):
    """
    Compute recommended capital allocation based on current system state.
    Reads: uncertainty_reports, market_intelligence_index, system_health_reports,
           bus_state, capital_state.
    """
    cr = CAPITAL_RULES
    notes = []

    try:
        conn = get_db()

        total_uncertainty  = read_latest_uncertainty(conn)
        mii                = read_latest_mii(conn)
        sts                = read_latest_sts(conn)
        bus_directive      = read_latest_bus_directive(conn)
        current_drawdown   = read_latest_drawdown(conn)

        conn.close()
    except Exception as e:
        return {'error': f'DB read failed: {e}'}

    # ── Factor 1: Uncertainty ──
    u_factor = calc_uncertainty_size_factor(total_uncertainty)
    if u_factor == 0.0:
        notes.append(f'HALT — uncertainty {total_uncertainty:.2f} > {cr["halt_uncertainty_threshold"]}')
    elif u_factor < 1.0:
        notes.append(f'Uncertainty {total_uncertainty:.2f} → {int(u_factor*100)}% size')

    # ── Factor 2: Drawdown ──
    dd_factor = calc_drawdown_factor(current_drawdown)
    dd_pct    = abs(current_drawdown)
    if dd_factor == 0.0:
        notes.append(f'HALT — drawdown {dd_pct:.1%} exceeds 20% threshold')
    elif dd_factor < 1.0:
        notes.append(f'Drawdown {dd_pct:.1%} → {int(dd_factor*100)}% size')

    # ── Factor 3: MII ──
    m_factor = calc_mii_factor(mii)
    if m_factor == 0.0:
        notes.append(f'SANDBOX ONLY — MII {mii:.1f} < {cr["exploration_mii_min"]}')
    elif m_factor < 1.0:
        notes.append(f'MII {mii:.1f} → {int(m_factor*100)}% size')

    # ── Bus override ──
    if bus_directive == 'HALT':
        notes.append('Bus directive: HALT — forcing combined_factor to 0.0')
        combined_factor = 0.0
    else:
        combined_factor = min(u_factor, dd_factor, m_factor)

    # ── STS note ──
    if sts < 30:
        notes.append(f'Warning: System health STS={sts:.1f} is low — verify data quality')

    # ── Final allocation ──
    recommended_exposure_pct      = combined_factor * cr['max_total_exposure']
    recommended_capital_egp       = cr['base_capital'] * recommended_exposure_pct
    max_positions                 = int(cr['max_simultaneous_positions'] * combined_factor)
    max_positions                 = max(max_positions, 0)

    # Cash floor check
    if recommended_exposure_pct > (1.0 - cr['min_cash_reserve']):
        recommended_exposure_pct  = 1.0 - cr['min_cash_reserve']
        recommended_capital_egp   = cr['base_capital'] * recommended_exposure_pct
        notes.append('Capped at 80% deployed — cash reserve floor enforced')

    if combined_factor == 0.0:
        notes.append('ACTION: No new positions. Monitor only.')
    elif combined_factor >= 1.0:
        notes.append('ACTION: Full allocation available — deploy up to recommended capital.')
    else:
        notes.append(f'ACTION: Reduced allocation ({int(combined_factor*100)}% of normal). Size carefully.')

    return {
        'recommended_exposure_pct':   round(recommended_exposure_pct, 4),
        'recommended_capital_egp':    round(recommended_capital_egp, 2),
        'uncertainty_factor':         round(u_factor, 4),
        'drawdown_factor':            round(dd_factor, 4),
        'mii_factor':                 round(m_factor, 4),
        'combined_factor':            round(combined_factor, 4),
        'max_positions':              max_positions,
        'total_uncertainty_raw':      round(total_uncertainty, 4),
        'mii_raw':                    round(mii, 2),
        'sts_raw':                    round(sts, 2),
        'bus_directive':              bus_directive,
        'current_drawdown_pct':       round(current_drawdown, 4),
        'notes':                      notes,
    }


# ─── Command: size_with_uncertainty ───────────────────────────────────────────

def size_with_uncertainty(params):
    """
    Compute uncertainty-adjusted position size for a specific symbol.
    params: {"symbol": "COMI", "entry_price": 50.0, "stop_loss_pct": 0.05}
    """
    cr    = CAPITAL_RULES
    notes = []

    symbol        = params.get('symbol', '')
    entry_price   = float(params.get('entry_price', 0.0))
    stop_loss_pct = float(params.get('stop_loss_pct', 0.05))

    if not symbol or entry_price <= 0:
        return {'error': 'symbol and entry_price (> 0) are required'}
    if stop_loss_pct <= 0 or stop_loss_pct >= 1:
        return {'error': 'stop_loss_pct must be between 0 and 1 (e.g., 0.05 for 5%)'}

    try:
        conn = get_db()
        total_uncertainty      = read_latest_uncertainty(conn)
        intelligence_score_raw = read_symbol_intelligence_score(conn, symbol)
        conn.close()
    except Exception as e:
        return {'error': f'DB read failed: {e}'}

    # ── Step 1: Base position size (2% risk rule) ──
    risk_per_share  = entry_price * stop_loss_pct
    if risk_per_share <= 0:
        return {'error': 'risk_per_share is zero — check entry_price and stop_loss_pct'}
    base_shares     = (cr['base_capital'] * cr['max_risk_per_trade']) / risk_per_share
    base_shares     = int(base_shares)

    # ── Step 2: Uncertainty factor ──
    u_factor = calc_uncertainty_size_factor(total_uncertainty)
    if u_factor == 0.0:
        notes.append('HALT — system uncertainty too high, no new positions')

    # ── Step 3: Intelligence score bonus ──
    intelligence_bonus = 0.0
    if intelligence_score_raw is not None:
        # Score is 0-100; high score means high-conviction signal → +10% bonus
        if intelligence_score_raw >= 70:
            intelligence_bonus = 0.10
            notes.append(f'Intelligence score {intelligence_score_raw:.1f} ≥ 70 → +10% size bonus')
        else:
            notes.append(f'Intelligence score {intelligence_score_raw:.1f} — no bonus')
    else:
        notes.append(f'No intelligence score found for {symbol} — no bonus applied')

    # ── Step 4: Adjusted shares ──
    adjusted_shares_raw = base_shares * u_factor * (1.0 + intelligence_bonus)
    adjusted_shares     = int(adjusted_shares_raw)

    # ── Step 5: 15% of capital cap ──
    max_position_by_capital = (cr['base_capital'] * 0.15) / entry_price
    if adjusted_shares * entry_price > cr['base_capital'] * 0.15:
        adjusted_shares = int(max_position_by_capital)
        notes.append(f'Capped at 15% of capital ({cr["base_capital"] * 0.15:.0f} EGP)')

    # ── Step 6: Daily volume constraint (5% cap) ──
    # Without live volume data we note the constraint; if avg_daily_volume is in params, apply it
    avg_daily_volume = params.get('avg_daily_volume', None)
    if avg_daily_volume is not None:
        avg_daily_volume = float(avg_daily_volume)
        volume_cap = int(avg_daily_volume * 0.05)
        if adjusted_shares > volume_cap:
            adjusted_shares = volume_cap
            notes.append(f'Capped at 5% of avg daily volume ({volume_cap:,} shares)')
    else:
        notes.append('avg_daily_volume not provided — volume constraint not applied')

    # ── Final metrics ──
    position_value_egp     = adjusted_shares * entry_price
    position_pct_of_capital = position_value_egp / cr['base_capital']
    stop_loss_egp          = entry_price * (1.0 - stop_loss_pct)
    max_loss_egp           = adjusted_shares * risk_per_share
    feasible               = adjusted_shares > 0 and u_factor > 0.0

    if not feasible:
        notes.append('INFEASIBLE — position size rounds to 0 or system halted')
    elif position_pct_of_capital < 0.01:
        notes.append('Warning: Position is less than 1% of capital — very small bet')

    return {
        'symbol':                  symbol,
        'entry_price':             entry_price,
        'stop_loss_pct':           stop_loss_pct,
        'base_shares':             base_shares,
        'adjusted_shares':         adjusted_shares,
        'position_value_egp':      round(position_value_egp, 2),
        'position_pct_of_capital': round(position_pct_of_capital, 4),
        'uncertainty_factor':      round(u_factor, 4),
        'total_uncertainty_raw':   round(total_uncertainty, 4),
        'intelligence_score':      round(intelligence_score_raw, 2) if intelligence_score_raw is not None else None,
        'intelligence_bonus':      round(intelligence_bonus, 4),
        'stop_loss_egp':           round(stop_loss_egp, 4),
        'max_loss_egp':            round(max_loss_egp, 2),
        'feasible':                feasible,
        'notes':                   notes,
    }


# ─── Command: drawdown_state ──────────────────────────────────────────────────

def drawdown_state(params):
    """
    Assess current drawdown, classify severity, and determine active response rules.
    Reads: capital_state (latest). Defaults to 0% if table absent.
    """
    cr = CAPITAL_RULES

    try:
        conn             = get_db()
        current_drawdown = read_latest_drawdown(conn)
        conn.close()
    except Exception as e:
        # Table may not exist yet — default to no drawdown
        current_drawdown = 0.0

    dd = abs(current_drawdown)   # work with positive magnitude

    # ── Severity classification ──
    if dd < 0.05:
        severity = 'NONE'
    elif dd < 0.10:
        severity = 'MINOR'
    elif dd < 0.15:
        severity = 'MODERATE'
    elif dd < 0.20:
        severity = 'SEVERE'
    else:
        severity = 'CRITICAL'

    # ── Recovery math ──
    # To recover from -X% loss you need +X/(1-X)% gain
    if dd < 1.0:
        recovery_required_pct = dd / (1.0 - dd) if dd < 1.0 else float('inf')
    else:
        recovery_required_pct = float('inf')

    # ── Active rules ──
    active_rules = []
    if dd >= 0.10:
        active_rules.append(f'RULE DD-10: Reduce position sizes to {int(cr["drawdown_10_reduce_to"]*100)}% of normal')
    if dd >= 0.15:
        active_rules.append(f'RULE DD-15: Reduce position sizes to {int(cr["drawdown_15_reduce_to"]*100)}% of normal')
    if dd >= 0.20 and cr['drawdown_20_halt']:
        active_rules.append('RULE DD-20: COMPLETE HALT — no new positions until recovery above -15%')
    if dd >= 0.05:
        active_rules.append('RULE DD-5: Tighten stops, avoid chasing momentum, review existing positions')

    should_halt = dd >= 0.20 and cr['drawdown_20_halt']

    # ── Size factor ──
    recommended_size_factor = calc_drawdown_factor(current_drawdown)

    # ── Recommendation ──
    if should_halt:
        recommendation = (
            f'HALT — drawdown of {dd:.1%} has triggered the survival protocol. '
            'Close weaker positions, do not open new ones. '
            f'Need to recover {recovery_required_pct:.1%} to reach breakeven.'
        )
    elif severity == 'SEVERE':
        recommendation = (
            f'SEVERE drawdown {dd:.1%}. Size at {int(recommended_size_factor*100)}% of normal. '
            f'Focus on capital preservation. Recovery needed: {recovery_required_pct:.1%}.'
        )
    elif severity == 'MODERATE':
        recommendation = (
            f'MODERATE drawdown {dd:.1%}. Size at {int(recommended_size_factor*100)}% of normal. '
            f'Be selective. Recovery needed: {recovery_required_pct:.1%}.'
        )
    elif severity == 'MINOR':
        recommendation = (
            f'MINOR drawdown {dd:.1%}. Normal operations with modest caution. '
            f'Recovery needed: {recovery_required_pct:.1%}.'
        )
    else:
        recommendation = 'No meaningful drawdown — full normal operations permitted.'

    return {
        'current_drawdown_pct':   round(current_drawdown, 4),
        'drawdown_magnitude':     round(dd, 4),
        'severity':               severity,
        'recovery_required_pct':  round(recovery_required_pct, 4),
        'active_rules':           active_rules,
        'recommended_size_factor': round(recommended_size_factor, 4),
        'should_halt':            should_halt,
        'recommendation':         recommendation,
    }


# ─── Command: exploration_budget ─────────────────────────────────────────────

def exploration_budget(params):
    """
    Determine the Exploration vs Exploitation directive for today.
    The anti-paralysis engine — forces the system to ACT.
    """
    cr = CAPITAL_RULES

    try:
        conn              = get_db()
        total_uncertainty = read_latest_uncertainty(conn)
        mii               = read_latest_mii(conn)
        bus_directive     = read_latest_bus_directive(conn)
        conn.close()
    except Exception as e:
        return {'error': f'DB read failed: {e}'}

    # ── Regime determination ──
    is_exploit_mii          = mii > cr['exploitation_mii_threshold']          # > 60
    is_low_uncertainty      = total_uncertainty < 0.40
    is_bus_active           = bus_directive not in ('HALT', 'HALT_SEVERE')
    is_sandbox_mii          = mii < cr['exploration_mii_min']                 # < 25
    is_high_uncertainty     = total_uncertainty > cr['exploration_uncertainty_max']  # > 0.65
    is_bus_halt             = bus_directive in ('HALT', 'HALT_SEVERE')

    if is_sandbox_mii or is_high_uncertainty or is_bus_halt:
        regime = 'SANDBOX_ONLY'
        action_allowed       = False
        arabic_message       = (
            'وضع المختبر — لا رأس مال حقيقي، استخدم الـ sandbox فقط. '
            f'MII={mii:.1f} | عدم اليقين={total_uncertainty:.2f} | الحافلة={bus_directive}'
        )
        english_message      = (
            f'SANDBOX ONLY — conditions not met for real capital. '
            f'MII={mii:.1f} (min {cr["exploration_mii_min"]}), '
            f'Uncertainty={total_uncertainty:.2f} (max {cr["exploration_uncertainty_max"]}), '
            f'Bus={bus_directive}. Use paper trading or research only.'
        )

    elif is_exploit_mii and is_low_uncertainty and is_bus_active:
        regime = 'EXPLOITATION'
        action_allowed       = True
        arabic_message       = (
            'شروط الاستغلال الكامل متوفرة — ادخل بكامل الحجم المحدد. '
            f'MII={mii:.1f} | عدم اليقين={total_uncertainty:.2f} | الحافلة={bus_directive}'
        )
        english_message      = (
            f'FULL EXPLOITATION MODE — all conditions met. '
            f'MII={mii:.1f} > {cr["exploitation_mii_threshold"]}, '
            f'Uncertainty={total_uncertainty:.2f} < 0.40, '
            f'Bus={bus_directive}. Deploy at full recommended size.'
        )

    else:
        regime = 'EXPLORATION'
        action_allowed       = True
        arabic_message       = (
            'وضع الاستكشاف — 10% فقط من رأس المال للفرص الجديدة. '
            f'MII={mii:.1f} | عدم اليقين={total_uncertainty:.2f} | الحافلة={bus_directive}'
        )
        english_message      = (
            f'EXPLORATION MODE — partial capital only. '
            f'MII={mii:.1f} (target > {cr["exploitation_mii_threshold"]}), '
            f'Uncertainty={total_uncertainty:.2f}. '
            f'Use max {int(cr["exploration_budget_pct"]*100)}% of capital for new positions.'
        )

    # ── Budget calculation ──
    exploration_budget_egp  = cr['base_capital'] * cr['exploration_budget_pct']

    if regime == 'EXPLOITATION':
        # Compute proper exposure for exploitation
        dd_factor           = calc_drawdown_factor(0.0)   # assume no drawdown for budget calc
        u_factor            = calc_uncertainty_size_factor(total_uncertainty)
        m_factor            = calc_mii_factor(mii)
        combined            = min(u_factor, dd_factor, m_factor)
        real_capital_egp    = cr['base_capital'] * combined * cr['max_total_exposure']
    elif regime == 'EXPLORATION':
        real_capital_egp    = exploration_budget_egp
    else:
        real_capital_egp    = 0.0

    return {
        'regime':                    regime,
        'exploration_budget_egp':    round(exploration_budget_egp, 2),
        'real_capital_available_egp': round(real_capital_egp, 2),
        'mii':                       round(mii, 2),
        'uncertainty':               round(total_uncertainty, 4),
        'bus_directive':             bus_directive,
        'is_exploit_conditions_met': is_exploit_mii and is_low_uncertainty and is_bus_active,
        'arabic_message':            arabic_message,
        'english_message':           english_message,
        'action_allowed':            action_allowed,
        'conditions': {
            'mii_gt_exploitation_threshold':   is_exploit_mii,
            'uncertainty_lt_40pct':            is_low_uncertainty,
            'bus_not_halted':                  is_bus_active,
            'sandbox_mii_triggered':           is_sandbox_mii,
            'high_uncertainty_triggered':      is_high_uncertainty,
            'bus_halt_triggered':              is_bus_halt,
        },
    }


# ─── Command: capital_report ──────────────────────────────────────────────────

def capital_report(params):
    """
    Full capital intelligence report: compute_exposure + drawdown_state + exploration_budget.
    """
    exposure    = compute_exposure(params)
    drawdown    = drawdown_state(params)
    ee_regime   = exploration_budget(params)

    # Guard against individual sub-command errors
    if 'error' in exposure:
        return {'error': f'compute_exposure failed: {exposure["error"]}'}
    if 'error' in drawdown:
        return {'error': f'drawdown_state failed: {drawdown["error"]}'}
    if 'error' in ee_regime:
        return {'error': f'exploration_budget failed: {ee_regime["error"]}'}

    cr = CAPITAL_RULES

    # ── Derived totals ──
    total_deployable_egp    = min(
        ee_regime['real_capital_available_egp'],
        exposure['recommended_capital_egp']
    )
    max_single_position_egp = cr['base_capital'] * 0.15   # 15% cap per position

    # ── Arabic summary ──
    regime       = ee_regime['regime']
    mii          = ee_regime['mii']
    uncertainty  = ee_regime['uncertainty']
    dd_severity  = drawdown['severity']
    combined     = exposure['combined_factor']

    if regime == 'SANDBOX_ONLY':
        today_summary_ar = (
            f'اليوم: وضع المختبر فقط — لا تداول حقيقي. '
            f'MII={mii:.1f} | عدم اليقين={uncertainty:.2f} | التراجع={dd_severity}.'
        )
        today_summary_en = (
            f'Today: SANDBOX ONLY. MII={mii:.1f}, Uncertainty={uncertainty:.2f}, '
            f'Drawdown={dd_severity}. No real capital at risk.'
        )
    elif regime == 'EXPLOITATION':
        today_summary_ar = (
            f'اليوم: وضع الاستغلال الكامل. '
            f'ادخل بالحجم الكامل — {total_deployable_egp:,.0f} جنيه متاح. '
            f'MII={mii:.1f} | عدم اليقين={uncertainty:.2f} | التراجع={dd_severity}.'
        )
        today_summary_en = (
            f'Today: FULL EXPLOITATION. Deploy up to {total_deployable_egp:,.0f} EGP '
            f'({exposure["recommended_exposure_pct"]:.1%} of capital). '
            f'MII={mii:.1f}, Uncertainty={uncertainty:.2f}, Drawdown={dd_severity}.'
        )
    else:
        today_summary_ar = (
            f'اليوم: وضع الاستكشاف — {total_deployable_egp:,.0f} جنيه فقط للفرص الجديدة. '
            f'MII={mii:.1f} | عدم اليقين={uncertainty:.2f} | التراجع={dd_severity}.'
        )
        today_summary_en = (
            f'Today: EXPLORATION MODE. Max {total_deployable_egp:,.0f} EGP for new positions. '
            f'MII={mii:.1f}, Uncertainty={uncertainty:.2f}, Drawdown={dd_severity}.'
        )

    return {
        'exposure':                  exposure,
        'drawdown':                  drawdown,
        'ee_regime':                 ee_regime,
        'total_deployable_egp':      round(total_deployable_egp, 2),
        'max_single_position_egp':   round(max_single_position_egp, 2),
        'today_summary_ar':          today_summary_ar,
        'today_summary_en':          today_summary_en,
        'generated_at':              NOW,
    }


# ─── DB Schema Creators ───────────────────────────────────────────────────────

_CREATE_CAPITAL_STATE = """
CREATE TABLE IF NOT EXISTS capital_state (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    current_drawdown_pct        REAL    DEFAULT 0,
    ee_regime                   TEXT,
    recommended_exposure_pct    REAL,
    real_capital_available_egp  REAL,
    uncertainty_factor          REAL,
    mii_factor                  REAL,
    updated_at                  TEXT
)
"""

_CREATE_EXPOSURE_HISTORY = """
CREATE TABLE IF NOT EXISTS exposure_history (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    recommended_exposure_pct    REAL,
    ee_regime                   TEXT,
    mii                         REAL,
    uncertainty                 REAL,
    recorded_at                 TEXT
)
"""


# ─── Command: build_full ──────────────────────────────────────────────────────

def build_full(params):
    """
    Run capital_report, persist results to DB, and return actionable summary.
    Creates capital_state and exposure_history tables if not yet present.
    """
    report = capital_report(params)
    if 'error' in report:
        return {'error': f'capital_report failed: {report["error"]}', 'status': 'failed'}

    exposure   = report['exposure']
    ee         = report['ee_regime']
    drawdown   = report['drawdown']

    try:
        conn = get_db()
        conn.execute(_CREATE_CAPITAL_STATE)
        conn.execute(_CREATE_EXPOSURE_HISTORY)

        # ── Upsert capital_state (insert a new snapshot row) ──
        conn.execute(
            """INSERT INTO capital_state
               (current_drawdown_pct, ee_regime, recommended_exposure_pct,
                real_capital_available_egp, uncertainty_factor, mii_factor, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                drawdown['current_drawdown_pct'],
                ee['regime'],
                exposure['recommended_exposure_pct'],
                ee['real_capital_available_egp'],
                exposure['uncertainty_factor'],
                exposure['mii_factor'],
                NOW,
            )
        )

        # ── Append to exposure_history ──
        conn.execute(
            """INSERT INTO exposure_history
               (recommended_exposure_pct, ee_regime, mii, uncertainty, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                exposure['recommended_exposure_pct'],
                ee['regime'],
                ee['mii'],
                ee['uncertainty'],
                NOW,
            )
        )

        conn.commit()
        conn.close()

    except Exception as e:
        return {
            'status':                      'error',
            'error':                       str(e),
            'ee_regime':                   ee.get('regime', 'UNKNOWN'),
            'recommended_exposure_pct':    exposure.get('recommended_exposure_pct', 0.0),
            'real_capital_available_egp':  ee.get('real_capital_available_egp', 0.0),
            'action_allowed':              ee.get('action_allowed', False),
            'today_summary_ar':            report.get('today_summary_ar', ''),
        }

    return {
        'status':                      'built',
        'ee_regime':                   ee['regime'],
        'recommended_exposure_pct':    exposure['recommended_exposure_pct'],
        'real_capital_available_egp':  ee['real_capital_available_egp'],
        'total_deployable_egp':        report['total_deployable_egp'],
        'max_single_position_egp':     report['max_single_position_egp'],
        'action_allowed':              ee['action_allowed'],
        'drawdown_severity':           drawdown['severity'],
        'should_halt':                 drawdown['should_halt'],
        'mii':                         ee['mii'],
        'uncertainty':                 ee['uncertainty'],
        'bus_directive':               ee['bus_directive'],
        'arabic_message':              ee['arabic_message'],
        'english_message':             ee['english_message'],
        'today_summary_ar':            report['today_summary_ar'],
        'today_summary_en':            report['today_summary_en'],
        'generated_at':                NOW,
    }


# ─── Command Dispatch ─────────────────────────────────────────────────────────

COMMANDS = {
    'compute_exposure':      compute_exposure,
    'size_with_uncertainty': size_with_uncertainty,
    'drawdown_state':        drawdown_state,
    'exploration_budget':    exploration_budget,
    'capital_report':        capital_report,
    'build_full':            build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'error':    'Usage: python capital_intelligence.py <command> \'<json_params>\'',
            'commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd = sys.argv[1]

    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    handler = COMMANDS.get(cmd)
    if handler is None:
        print(json.dumps({
            'error':             f'Unknown command: {cmd}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        result = {'error': f'Unhandled exception in {cmd}: {e}', 'command': cmd}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
