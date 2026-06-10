"""
governance_constitution.py — Phase 41: EGX Autonomous Quant System
Governance Constitution Engine — the highest meta-governance authority.
Defines rules above all other Phases; prevents chaos, overfit, and hallucination.

Commands:
  audit_rule_violations      — Scan all active rules and report violations/warnings
  enforce_mutation_limits    — Suspend excess mutations that breach the weekly limit
  resolve_override_conflict  — Determine which phase wins a governance conflict
  check_halt_conditions      — Evaluate STS / uncertainty / MII halt triggers
  governance_report          — Combined audit + halt check summary
  build_full                 — Full audit, persist to DB, return structured report
  propose_amendment          — Record a proposed change to a constitution rule (no auto-apply)
  apply_trust_decay          — Penalise engines with recent violations; persist trust weights
  activate_safe_mode         — Detect / force emergency safe-mode; log state to DB
  exploration_vs_exploitation — Compute E/E regime and capital-allocation directive
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import random

# ─────────────────────────────────────────────
# DB PATH
# ─────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ─────────────────────────────────────────────
# GOVERNANCE CONSTITUTION
# ─────────────────────────────────────────────
GOVERNANCE_CONSTITUTION = {
    # === Mutation Speed Limits ===
    'max_mutations_per_week': 20,
    'min_law_age_days_before_mutation': 7,
    'max_new_laws_per_cycle': 5,

    # === Law Promotion Thresholds ===
    'sandbox_min_cycles_before_promotion': 3,
    'sandbox_min_precision_to_promote': 0.22,   # above 18.2% baseline
    'sandbox_min_eae_to_promote': 0.005,
    'sandbox_min_n_samples': 15,
    'auto_promote_if_p_lt': 0.01,               # strong evidence: skip cycle requirement

    # === Override Hierarchy (index 0 = highest authority) ===
    'override_hierarchy': [
        'HARD_VETO',           # 0 — absolute, cannot be overridden
        'UNCERTAINTY_VETO',    # 1 — total_uncertainty > 0.80
        'ANTI_LAW_VETO',       # 2 — VETO-level anti-law triggered
        'GOVERNANCE_BLOCK',    # 3 — constitution violation
        'ARBITRATION',         # 4 — Phase 34 decision
        'PORTFOLIO_LIMIT',     # 5 — Phase 32 constraint
        'SANDBOX_SUGGESTION',  # 6 — Phase 40 new law suggestion
    ],

    # === Anti-Law Severity Escalation ===
    'anti_law_escalation': {
        'first_trigger': 'CAUTION',
        'repeat_3': 'DANGER',
        'repeat_7': 'VETO',
    },

    # === Uncertainty Veto ===
    'uncertainty_veto_threshold': 0.80,
    'ood_halt_threshold': 0.85,

    # === Sandbox Authority ===
    'sandbox_can_auto_promote': True,
    'sandbox_auto_retire_precision_threshold': 0.15,

    # === Predictive Stability ===
    'max_prediction_flip_rate': 0.40,  # >40% flips in 5 days = unstable

    # === System Halt Rules ===
    'halt_if_sts_below': 30.0,
    'halt_if_total_uncertainty_above': 0.85,
    'halt_if_mii_below': 15.0,
}

# ─────────────────────────────────────────────
# DB HELPER
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_exists(conn, table_name):
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def column_exists(conn, table_name, col_name):
    try:
        cur = conn.execute(f"PRAGMA table_info({table_name})")
        cols = [row['name'] for row in cur.fetchall()]
        return col_name in cols
    except Exception:
        return False


def now_iso():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def days_ago_iso(n):
    return (datetime.utcnow() - timedelta(days=n)).strftime('%Y-%m-%dT%H:%M:%SZ')


# ─────────────────────────────────────────────
# INTERNAL: Read mutation log
# ─────────────────────────────────────────────
def _read_mutations_last_n_days(conn, n_days=7):
    """Return list of mutation rows from law_evolution_log in last n days."""
    rows = []
    try:
        if not table_exists(conn, 'law_evolution_log'):
            return rows
        cutoff = days_ago_iso(n_days)
        cur = conn.execute(
            "SELECT * FROM law_evolution_log WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,)
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass
    return rows


def _read_all_active_laws(conn):
    """Return all active laws from law_registry or similar table."""
    rows = []
    for tbl in ('law_registry', 'active_laws', 'laws'):
        try:
            if table_exists(conn, tbl):
                cur = conn.execute(f"SELECT * FROM {tbl} WHERE status='ACTIVE'")
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    return rows
        except Exception:
            pass
    return rows


def _read_sandbox_promoted(conn):
    """Return sandbox hypotheses that were promoted."""
    rows = []
    try:
        if not table_exists(conn, 'sandbox_hypotheses'):
            return rows
        cur = conn.execute(
            "SELECT * FROM sandbox_hypotheses WHERE status='PROMOTED' ORDER BY updated_at DESC"
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass
    return rows


def _read_latest_uncertainty(conn):
    """Return the most recent uncertainty report row."""
    for tbl in ('uncertainty_reports', 'uncertainty_engine_output', 'uncertainty_log'):
        try:
            if not table_exists(conn, tbl):
                continue
            cur = conn.execute(
                f"SELECT * FROM {tbl} ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
    return {}


def _read_latest_arbitration_decisions(conn, limit=20):
    """Return recent arbitration decisions."""
    rows = []
    for tbl in ('arbitration_decisions', 'cognitive_arbitration_log', 'arbitration_log'):
        try:
            if not table_exists(conn, tbl):
                continue
            cur = conn.execute(
                f"SELECT * FROM {tbl} ORDER BY decided_at DESC LIMIT ?",
                (limit,)
            )
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                return rows
        except Exception:
            pass
    return rows


def _read_latest_system_health(conn):
    """Return the most recent system health report."""
    for tbl in ('system_health_reports', 'system_health_log', 'health_reports'):
        try:
            if not table_exists(conn, tbl):
                continue
            cur = conn.execute(
                f"SELECT * FROM {tbl} ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
    return {}


def _read_latest_mii(conn):
    """Return the most recent market intelligence index row."""
    for tbl in ('market_intelligence_index', 'mii_log', 'intelligence_index'):
        try:
            if not table_exists(conn, tbl):
                continue
            cur = conn.execute(
                f"SELECT * FROM {tbl} ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
    return {}


def _read_recent_predictions(conn, last_n_days=5):
    """Return predictions grouped by symbol for flip-rate analysis."""
    symbol_preds = defaultdict(list)
    for tbl in ('predictions', 'daily_predictions', 'prediction_log', 'unified_daily_synthesis'):
        try:
            if not table_exists(conn, tbl):
                continue
            cutoff = days_ago_iso(last_n_days)
            cur = conn.execute(
                f"SELECT * FROM {tbl} WHERE created_at >= ? ORDER BY created_at ASC",
                (cutoff,)
            )
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                continue
            for r in rows:
                sym = r.get('symbol') or r.get('ticker') or 'UNKNOWN'
                direction = (
                    r.get('direction') or
                    r.get('signal') or
                    r.get('prediction') or
                    r.get('bias') or
                    ''
                )
                symbol_preds[sym].append(str(direction).upper())
            return symbol_preds
        except Exception:
            pass
    return symbol_preds


# ─────────────────────────────────────────────
# HELPER: compute prediction flip rate
# ─────────────────────────────────────────────
def _compute_flip_rate(directions):
    """Given a list of direction strings, compute fraction of consecutive flips."""
    if len(directions) < 2:
        return 0.0
    flips = sum(
        1 for i in range(1, len(directions))
        if directions[i] != directions[i - 1]
    )
    return flips / (len(directions) - 1)


# ─────────────────────────────────────────────
# HELPER: extract float safely
# ─────────────────────────────────────────────
def _safe_float(d, *keys, default=0.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


# ─────────────────────────────────────────────
# COMMAND: audit_rule_violations
# ─────────────────────────────────────────────
def audit_rule_violations(params):
    violations = []
    const = GOVERNANCE_CONSTITUTION

    try:
        conn = get_db()
    except Exception as e:
        return {
            'violations': [],
            'n_violations': 0,
            'n_warnings': 0,
            'constitution_health': 'DEGRADED',
            'audit_timestamp': now_iso(),
            'error': str(e),
        }

    # ── 1. Mutation Speed ──────────────────────────────────────────────
    try:
        mutations_7d = _read_mutations_last_n_days(conn, 7)
        if len(mutations_7d) > const['max_mutations_per_week']:
            violations.append({
                'rule': 'mutation_speed',
                'severity': 'VIOLATION',
                'detail': (
                    f"Found {len(mutations_7d)} mutations in last 7 days; "
                    f"limit is {const['max_mutations_per_week']}."
                ),
                'phase_involved': 'Phase 38 (Evolution Engine)',
            })
    except Exception as ex:
        violations.append({
            'rule': 'mutation_speed',
            'severity': 'WARNING',
            'detail': f"Could not read law_evolution_log: {ex}",
            'phase_involved': 'Unknown',
        })

    # ── 2. Law Age Before Mutation ─────────────────────────────────────
    try:
        mutations_7d = _read_mutations_last_n_days(conn, 7)
        min_age = const['min_law_age_days_before_mutation']
        premature = []
        for row in mutations_7d:
            law_created = row.get('law_created_at') or row.get('original_created_at')
            mutation_ts = row.get('created_at') or row.get('mutated_at')
            law_id = row.get('law_id') or row.get('id') or 'unknown'
            if law_created and mutation_ts:
                try:
                    t0 = datetime.strptime(law_created[:19], '%Y-%m-%dT%H:%M:%S')
                    t1 = datetime.strptime(mutation_ts[:19], '%Y-%m-%dT%H:%M:%S')
                    age_days = (t1 - t0).total_seconds() / 86400.0
                    if age_days < min_age:
                        premature.append({'law_id': law_id, 'age_days': round(age_days, 2)})
                except Exception:
                    pass
        if premature:
            violations.append({
                'rule': 'law_age',
                'severity': 'VIOLATION',
                'detail': (
                    f"{len(premature)} laws mutated before minimum age of {min_age} days: "
                    + json.dumps(premature[:5])
                ),
                'phase_involved': 'Phase 38 (Evolution Engine)',
            })
    except Exception as ex:
        violations.append({
            'rule': 'law_age',
            'severity': 'WARNING',
            'detail': f"Could not check law age: {ex}",
            'phase_involved': 'Unknown',
        })

    # ── 3. Sandbox Promotion Too Early ────────────────────────────────
    try:
        promoted = _read_sandbox_promoted(conn)
        min_cycles = const['sandbox_min_cycles_before_promotion']
        early_promotions = []
        for row in promoted:
            cycles = row.get('cycles_tested') or row.get('n_cycles') or row.get('cycle_count')
            hyp_id = row.get('hypothesis_id') or row.get('id') or 'unknown'
            p_value = row.get('p_value')
            auto_promote = False
            if p_value is not None:
                try:
                    if float(p_value) < const['auto_promote_if_p_lt']:
                        auto_promote = True
                except (ValueError, TypeError):
                    pass
            if not auto_promote and cycles is not None:
                try:
                    if int(cycles) < min_cycles:
                        early_promotions.append({
                            'hypothesis_id': hyp_id,
                            'cycles_tested': cycles,
                        })
                except (ValueError, TypeError):
                    pass
        if early_promotions:
            violations.append({
                'rule': 'sandbox_promotions',
                'severity': 'WARNING',
                'detail': (
                    f"{len(early_promotions)} hypotheses promoted with fewer than "
                    f"{min_cycles} cycles (no strong p-value): "
                    + json.dumps(early_promotions[:5])
                ),
                'phase_involved': 'Phase 40 (Research Sandbox)',
            })
    except Exception as ex:
        violations.append({
            'rule': 'sandbox_promotions',
            'severity': 'WARNING',
            'detail': f"Could not check sandbox promotions: {ex}",
            'phase_involved': 'Phase 40 (Research Sandbox)',
        })

    # ── 4. Uncertainty Bypass ──────────────────────────────────────────
    try:
        uncertainty_row = _read_latest_uncertainty(conn)
        total_unc = _safe_float(uncertainty_row, 'total_uncertainty', 'composite_uncertainty', default=None)
        arb_decisions = _read_latest_arbitration_decisions(conn, 30)

        if total_unc is not None and total_unc > const['uncertainty_veto_threshold']:
            enter_decisions = [
                d for d in arb_decisions
                if (d.get('outcome') or d.get('decision') or '').upper() in ('ENTER', 'BUY', 'SELL', 'TRADE')
            ]
            if enter_decisions:
                violations.append({
                    'rule': 'uncertainty_bypass',
                    'severity': 'WARNING',
                    'detail': (
                        f"total_uncertainty={round(total_unc, 3)} exceeds "
                        f"threshold={const['uncertainty_veto_threshold']} but "
                        f"{len(enter_decisions)} ENTER decisions found in arbitration."
                    ),
                    'phase_involved': 'Phase 34 (Cognitive Arbitration)',
                })
    except Exception as ex:
        violations.append({
            'rule': 'uncertainty_bypass',
            'severity': 'WARNING',
            'detail': f"Could not cross-check uncertainty vs arbitration: {ex}",
            'phase_involved': 'Phase 34 / Phase 30',
        })

    # ── 5. Prediction Flip Rate ────────────────────────────────────────
    try:
        symbol_preds = _read_recent_predictions(conn, last_n_days=5)
        max_flip = const['max_prediction_flip_rate']
        unstable_symbols = []
        for sym, dirs in symbol_preds.items():
            if len(dirs) >= 3:
                flip_rate = _compute_flip_rate(dirs)
                if flip_rate > max_flip:
                    unstable_symbols.append({
                        'symbol': sym,
                        'flip_rate': round(flip_rate, 3),
                        'n_predictions': len(dirs),
                    })
        if unstable_symbols:
            violations.append({
                'rule': 'prediction_flip_rate',
                'severity': 'WARNING',
                'detail': (
                    f"{len(unstable_symbols)} symbols exceed flip rate "
                    f"threshold of {max_flip}: "
                    + json.dumps(unstable_symbols[:5])
                ),
                'phase_involved': 'Phase 43 (Unified Synthesis)',
            })
    except Exception as ex:
        violations.append({
            'rule': 'prediction_flip_rate',
            'severity': 'WARNING',
            'detail': f"Could not compute prediction flip rates: {ex}",
            'phase_involved': 'Phase 43 (Unified Synthesis)',
        })

    conn.close()

    n_violations = sum(1 for v in violations if v['severity'] == 'VIOLATION')
    n_warnings = sum(1 for v in violations if v['severity'] == 'WARNING')

    if n_violations >= 3:
        health = 'CRITICAL'
    elif n_violations >= 1:
        health = 'DEGRADED'
    elif n_warnings >= 3:
        health = 'DEGRADED'
    else:
        health = 'CLEAN'

    return {
        'violations': violations,
        'n_violations': n_violations,
        'n_warnings': n_warnings,
        'constitution_health': health,
        'audit_timestamp': now_iso(),
    }


# ─────────────────────────────────────────────
# COMMAND: enforce_mutation_limits
# ─────────────────────────────────────────────
def enforce_mutation_limits(params):
    const = GOVERNANCE_CONSTITUTION
    limit = const['max_mutations_per_week']
    suspended_laws = []
    action_taken = 'none'

    try:
        conn = get_db()
    except Exception as e:
        return {
            'mutations_this_week': 0,
            'limit': limit,
            'over_limit': False,
            'suspended_laws': [],
            'action_taken': 'error',
            'error': str(e),
        }

    try:
        mutations = _read_mutations_last_n_days(conn, 7)
        n_mutations = len(mutations)
        over_limit = n_mutations > limit

        if over_limit:
            excess = mutations[limit:]  # newest are first due to DESC sort; suspend the excess
            for row in excess:
                law_id = str(row.get('law_id') or row.get('id') or 'unknown')
                suspended_laws.append(law_id)

            # Attempt to update status in law_evolution_log if column exists
            if table_exists(conn, 'law_evolution_log') and column_exists(conn, 'law_evolution_log', 'status'):
                for row in excess:
                    row_id = row.get('id')
                    if row_id:
                        try:
                            conn.execute(
                                "UPDATE law_evolution_log SET status='SUSPENDED' WHERE id=?",
                                (row_id,)
                            )
                        except Exception:
                            pass
                conn.commit()
                action_taken = 'suspended_in_db'
            else:
                action_taken = 'reported_only_no_status_column'
        else:
            action_taken = 'within_limit_no_action'

    except Exception as ex:
        conn.close()
        return {
            'mutations_this_week': 0,
            'limit': limit,
            'over_limit': False,
            'suspended_laws': [],
            'action_taken': 'error',
            'error': str(ex),
        }

    conn.close()

    return {
        'mutations_this_week': n_mutations,
        'limit': limit,
        'over_limit': over_limit,
        'suspended_laws': suspended_laws,
        'action_taken': action_taken,
    }


# ─────────────────────────────────────────────
# COMMAND: resolve_override_conflict
# ─────────────────────────────────────────────
def resolve_override_conflict(params):
    hierarchy = GOVERNANCE_CONSTITUTION['override_hierarchy']
    phase_a = str(params.get('phase_a', '')).upper()
    phase_b = str(params.get('phase_b', '')).upper()
    context = params.get('context', '')

    def find_position(phase_name):
        for i, entry in enumerate(hierarchy):
            if entry.upper() == phase_name:
                return i
        return -1  # not found → lowest authority

    pos_a = find_position(phase_a)
    pos_b = find_position(phase_b)

    # Lower index = higher authority
    # -1 means not in hierarchy = lowest authority
    eff_pos_a = pos_a if pos_a >= 0 else len(hierarchy)
    eff_pos_b = pos_b if pos_b >= 0 else len(hierarchy)

    if eff_pos_a < eff_pos_b:
        winner = phase_a
        loser = phase_b
        reason = (
            f"{phase_a} has higher authority (position {pos_a}) than "
            f"{phase_b} (position {pos_b}) in the override hierarchy."
        )
    elif eff_pos_b < eff_pos_a:
        winner = phase_b
        loser = phase_a
        reason = (
            f"{phase_b} has higher authority (position {pos_b}) than "
            f"{phase_a} (position {pos_a}) in the override hierarchy."
        )
    else:
        winner = 'TIE'
        loser = 'TIE'
        reason = (
            f"Both {phase_a} and {phase_b} have equal authority "
            f"(positions {pos_a} and {pos_b}). Human review required."
        )

    return {
        'winner': winner,
        'loser': loser,
        'reason': reason,
        'context': context,
        'hierarchy_positions': {
            'phase_a': pos_a,
            'phase_b': pos_b,
        },
        'hierarchy_reference': hierarchy,
    }


# ─────────────────────────────────────────────
# COMMAND: check_halt_conditions
# ─────────────────────────────────────────────
def check_halt_conditions(params):
    const = GOVERNANCE_CONSTITUTION
    halt_reasons = []
    sts = None
    total_uncertainty = None
    mii = None

    try:
        conn = get_db()
    except Exception as e:
        return {
            'should_halt': False,
            'halt_reasons': [f'DB connection failed: {e}'],
            'sts': 0.0,
            'total_uncertainty': 0.0,
            'mii': 0.0,
            'recommendation': 'Could not evaluate halt conditions — DB unavailable.',
        }

    # ── STS ─────────────────────────────────────────────────────────────
    try:
        health_row = _read_latest_system_health(conn)
        sts = _safe_float(
            health_row,
            'sts', 'system_trust_score', 'trust_score', 'score',
            default=None
        )
        if sts is None:
            sts = 50.0  # neutral fallback
        if sts < const['halt_if_sts_below']:
            halt_reasons.append(
                f"STS={round(sts, 2)} is below halt threshold {const['halt_if_sts_below']}."
            )
    except Exception as ex:
        sts = 50.0
        halt_reasons.append(f"WARNING: Could not read STS: {ex}")

    # ── Total Uncertainty ──────────────────────────────────────────────
    try:
        unc_row = _read_latest_uncertainty(conn)
        total_uncertainty = _safe_float(
            unc_row,
            'total_uncertainty', 'composite_uncertainty', 'uncertainty_score',
            default=None
        )
        if total_uncertainty is None:
            total_uncertainty = 0.0
        if total_uncertainty > const['halt_if_total_uncertainty_above']:
            halt_reasons.append(
                f"total_uncertainty={round(total_uncertainty, 3)} exceeds "
                f"halt threshold {const['halt_if_total_uncertainty_above']}."
            )
    except Exception as ex:
        total_uncertainty = 0.0
        halt_reasons.append(f"WARNING: Could not read uncertainty: {ex}")

    # ── MII ──────────────────────────────────────────────────────────────
    try:
        mii_row = _read_latest_mii(conn)
        mii = _safe_float(
            mii_row,
            'mii', 'market_intelligence_index', 'index_score', 'score',
            default=None
        )
        if mii is None:
            mii = 50.0  # neutral fallback
        if mii < const['halt_if_mii_below']:
            halt_reasons.append(
                f"MII={round(mii, 2)} is below halt threshold {const['halt_if_mii_below']}."
            )
    except Exception as ex:
        mii = 50.0
        halt_reasons.append(f"WARNING: Could not read MII: {ex}")

    conn.close()

    # Filter only true halt triggers (not warnings)
    true_halts = [r for r in halt_reasons if not r.startswith('WARNING:')]
    should_halt = len(true_halts) > 0

    if should_halt:
        if len(true_halts) >= 2:
            recommendation = (
                "IMMEDIATE HALT — multiple critical conditions triggered. "
                "Suspend all trading operations and run full system diagnostics."
            )
        else:
            recommendation = (
                "HALT RECOMMENDED — one critical condition triggered. "
                "Suspend new trade entries; review flagged metric before resuming."
            )
    else:
        recommendation = (
            "No halt conditions triggered. System may proceed with normal operations."
        )

    return {
        'should_halt': should_halt,
        'halt_reasons': halt_reasons,
        'sts': round(float(sts), 4),
        'total_uncertainty': round(float(total_uncertainty), 4),
        'mii': round(float(mii), 4),
        'recommendation': recommendation,
    }


# ─────────────────────────────────────────────
# COMMAND: governance_report
# ─────────────────────────────────────────────
def governance_report(params):
    audit = audit_rule_violations(params)
    halt_check = check_halt_conditions(params)

    active_rules = len(GOVERNANCE_CONSTITUTION)

    return {
        'audit': audit,
        'halt_check': halt_check,
        'constitution_version': '1.0',
        'active_rules': active_rules,
        'generated_at': now_iso(),
    }


# ─────────────────────────────────────────────
# COMMAND: build_full
# ─────────────────────────────────────────────
def build_full(params):
    report = governance_report(params)
    audit = report['audit']
    halt_check = report['halt_check']

    try:
        conn = get_db()
    except Exception as e:
        return {
            'status': 'error',
            'error': str(e),
            'n_violations': 0,
            'n_warnings': 0,
            'constitution_health': 'UNKNOWN',
            'should_halt': False,
        }

    # ── Ensure tables exist ─────────────────────────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS governance_violations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                rule            TEXT,
                severity        TEXT,
                detail          TEXT,
                phase_involved  TEXT,
                detected_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS governance_decisions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_type   TEXT,
                outcome         TEXT,
                reason          TEXT,
                decided_at      TEXT
            )
        """)
        conn.commit()
    except Exception as ex:
        conn.close()
        return {
            'status': 'error',
            'error': f"Table creation failed: {ex}",
            'n_violations': audit.get('n_violations', 0),
            'n_warnings': audit.get('n_warnings', 0),
            'constitution_health': audit.get('constitution_health', 'UNKNOWN'),
            'should_halt': halt_check.get('should_halt', False),
        }

    # ── Insert violations ───────────────────────────────────────────────
    detected_at = now_iso()
    try:
        for v in audit.get('violations', []):
            conn.execute(
                """INSERT INTO governance_violations
                   (rule, severity, detail, phase_involved, detected_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    v.get('rule', ''),
                    v.get('severity', ''),
                    v.get('detail', ''),
                    v.get('phase_involved', ''),
                    detected_at,
                )
            )
    except Exception as ex:
        pass  # graceful: continue without crashing

    # ── Insert governance decision summary ──────────────────────────────
    health = audit.get('constitution_health', 'UNKNOWN')
    should_halt = halt_check.get('should_halt', False)
    halt_reasons = halt_check.get('halt_reasons', [])

    outcome = 'HALT' if should_halt else health
    reason_text = (
        '; '.join(halt_reasons) if halt_reasons
        else f"Constitution health: {health}"
    )

    try:
        conn.execute(
            """INSERT INTO governance_decisions
               (decision_type, outcome, reason, decided_at)
               VALUES (?, ?, ?, ?)""",
            (
                'GOVERNANCE_AUDIT',
                outcome,
                reason_text,
                detected_at,
            )
        )
    except Exception:
        pass

    try:
        conn.commit()
    except Exception:
        pass

    conn.close()

    return {
        'status': 'built',
        'n_violations': audit.get('n_violations', 0),
        'n_warnings': audit.get('n_warnings', 0),
        'constitution_health': health,
        'should_halt': should_halt,
        'halt_reasons': halt_reasons,
        'generated_at': detected_at,
    }


# ─────────────────────────────────────────────
# COMMAND REGISTRY
# ─────────────────────────────────────────────
COMMANDS = {
    'audit_rule_violations':  audit_rule_violations,
    'enforce_mutation_limits': enforce_mutation_limits,
    'resolve_override_conflict': resolve_override_conflict,
    'check_halt_conditions':  check_halt_conditions,
    'governance_report':      governance_report,
    'build_full':             build_full,
}


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(json.dumps({
            'error': 'Usage: governance_constitution.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
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
            'error': f'Unknown command: {cmd}',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = handler(params)
    except Exception as e:
        result = {
            'error': f'Unhandled exception in {cmd}: {e}',
            'command': cmd,
        }

    print(json.dumps(result))
