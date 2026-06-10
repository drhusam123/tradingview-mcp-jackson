#!/usr/bin/env python3
"""
Phase 67 — Scientific Refinement Cycle
"دورة التنقية العلمية — قياس، تشذيب، تكييف، اصطناع، تحسين مستمر"

Commands: measure | prune | condition | synthesize | run_cycle | build_full
"""
import sys, json, os, sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS refinement_cycles (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date      TEXT,
        run_at        TEXT DEFAULT CURRENT_TIMESTAMP,
        laws_measured INTEGER DEFAULT 0,
        laws_pruned   INTEGER DEFAULT 0,
        laws_updated  INTEGER DEFAULT 0,
        avg_precision REAL,
        avg_ues       REAL,
        n_high_conv   INTEGER DEFAULT 0,
        n_signals     INTEGER DEFAULT 0,
        notes         TEXT
    );
    CREATE TABLE IF NOT EXISTS law_quality_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        law_id          TEXT,
        measured_at     TEXT DEFAULT CURRENT_TIMESTAMP,
        precision_val   REAL,
        activation_count INTEGER,
        regime          TEXT,
        best_regime     TEXT,
        best_precision  REAL,
        quality_grade   TEXT,
        action_taken    TEXT
    );
    CREATE TABLE IF NOT EXISTS signal_quality_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        log_date      TEXT,
        n_signals     INTEGER DEFAULT 0,
        n_high_conv   INTEGER DEFAULT 0,
        n_medium_conv INTEGER DEFAULT 0,
        avg_ues       REAL,
        top_symbols   TEXT,
        notes         TEXT
    );
    """)
    conn.commit()

# ─────────────────────────────────────────────
# 1. MEASURE — snapshot all law quality metrics
# ─────────────────────────────────────────────
def measure(params):
    law_type = params.get('law_type', 'universal')
    conn = db()
    ensure_tables(conn)
    try:
        tbl = 'universal_laws_p16' if law_type == 'universal' else 'structural_laws'
        laws = conn.execute(f"""
            SELECT pattern_id as law_id, precision, n_activations as activation_count,
                   best_regime, best_regime_precision, direction
            FROM {tbl}
            WHERE n_activations > 0
            ORDER BY precision DESC
        """).fetchall()

        if not laws:
            return {"success": True, "message": f"No laws in {tbl} — run law discovery first", "n_laws": 0}

        total     = len(laws)
        avg_prec  = sum(l['precision'] for l in laws) / total
        high_prec = [l for l in laws if l['precision'] >= 0.60]
        low_prec  = [l for l in laws if l['precision'] < 0.20]
        conditioned = [l for l in laws if l['best_regime'] and l['best_regime_precision'] and
                       l['best_regime_precision'] > l['precision'] + 0.05]

        # Grade each law
        graded = []
        for l in laws:
            p = l['precision']
            if p >= 0.65: grade = 'A'
            elif p >= 0.50: grade = 'B'
            elif p >= 0.35: grade = 'C'
            elif p >= 0.20: grade = 'D'
            else: grade = 'F'
            graded.append({
                'law_id': l['law_id'],
                'precision': p,
                'activations': l['activation_count'],
                'grade': grade,
                'best_regime': l['best_regime'],
                'best_regime_precision': l['best_regime_precision'],
            })

        # Save to history
        now = datetime.now().isoformat()
        for l in graded:
            conn.execute("""
                INSERT INTO law_quality_history
                (law_id, measured_at, precision_val, activation_count, best_regime,
                 best_precision, quality_grade)
                VALUES (?,?,?,?,?,?,?)
            """, (l['law_id'], now, l['precision'], l['activations'],
                  l['best_regime'], l['best_regime_precision'], l['grade']))
        conn.commit()

        return {
            "success": True,
            "law_type": law_type,
            "n_laws": total,
            "avg_precision": round(avg_prec, 4),
            "n_grade_a": len([g for g in graded if g['grade'] == 'A']),
            "n_grade_b": len([g for g in graded if g['grade'] == 'B']),
            "n_grade_f": len(low_prec),
            "n_high_precision": len(high_prec),
            "n_improvable_with_regime": len(conditioned),
            "top_laws": graded[:10],
            "worst_laws": graded[-5:],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 2. PRUNE — deactivate consistently failing laws
# ─────────────────────────────────────────────
def prune(params):
    law_type   = params.get('law_type', 'universal')
    max_prec   = params.get('max_precision', 0.15)
    min_acts   = params.get('min_activations', 50)
    dry_run    = params.get('dry_run', True)
    conn = db()
    ensure_tables(conn)
    try:
        tbl = 'universal_laws_p16' if law_type == 'universal' else 'structural_laws'
        candidates = conn.execute(f"""
            SELECT pattern_id as law_id, precision, n_activations as activation_count,
                   best_regime_precision
            FROM {tbl}
            WHERE precision < ?
              AND n_activations > ?
        """, (max_prec, min_acts)).fetchall()

        pruned = []
        for law in candidates:
            best = law['best_regime_precision'] or 0
            # Don't prune if regime-conditioning could save it
            if best >= 0.30:
                continue
            pruned.append({
                'law_id': law['law_id'],
                'precision': law['precision'],
                'activations': law['activation_count'],
                'reason': f"precision={law['precision']:.3f} < {max_prec}, no regime lift",
            })

        if not dry_run and pruned:
            for p in pruned:
                conn.execute(f"""
                    UPDATE {tbl} SET law_status = 'PRUNED'
                    WHERE pattern_id = ?
                """, (p['law_id'],))
                conn.execute("""
                    INSERT INTO law_quality_history
                    (law_id, quality_grade, action_taken)
                    VALUES (?,?,?)
                """, (p['law_id'], 'F', 'PRUNED'))
            conn.commit()

        return {
            "success": True,
            "dry_run": dry_run,
            "n_candidates": len(candidates),
            "n_pruned": len(pruned),
            "pruned_laws": pruned[:20],
            "message": "dry_run — no changes applied" if dry_run else f"Pruned {len(pruned)} laws",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 3. CONDITION — apply regime conditioning to improvable laws
# ─────────────────────────────────────────────
def condition(params):
    law_type = params.get('law_type', 'universal')
    min_lift = params.get('min_lift', 0.05)
    conn = db()
    ensure_tables(conn)
    try:
        tbl = 'universal_laws_p16' if law_type == 'universal' else 'structural_laws'
        laws = conn.execute(f"""
            SELECT pattern_id as law_id, precision, best_regime, best_regime_precision
            FROM {tbl}
            WHERE best_regime IS NOT NULL
              AND best_regime_precision IS NOT NULL
              AND (best_regime_precision - precision) >= ?
            ORDER BY (best_regime_precision - precision) DESC
        """, (min_lift,)).fetchall()

        candidates = []
        for l in laws:
            lift = l['best_regime_precision'] - l['precision']
            candidates.append({
                'law_id': l['law_id'],
                'base_precision': round(l['precision'], 4),
                'regime_precision': round(l['best_regime_precision'], 4),
                'lift': round(lift, 4),
                'best_regime': l['best_regime'],
            })

        # Check if regime_law_conditions table exists, if so count coverage
        try:
            n_conditioned = conn.execute(
                "SELECT COUNT(*) FROM regime_law_conditions WHERE is_active=1"
            ).fetchone()[0]
        except:
            n_conditioned = 0

        return {
            "success": True,
            "n_improvable": len(candidates),
            "n_already_conditioned": n_conditioned,
            "min_lift_threshold": min_lift,
            "avg_lift": round(sum(c['lift'] for c in candidates)/len(candidates), 4) if candidates else 0,
            "top_candidates": candidates[:15],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 4. SYNTHESIZE — create composite rules from top-performing law pairs
# ─────────────────────────────────────────────
def synthesize(params):
    law_type  = params.get('law_type', 'universal')
    min_prec  = params.get('min_precision', 0.45)
    top_n     = params.get('top_n', 10)
    conn = db()
    ensure_tables(conn)
    try:
        tbl = 'universal_laws_p16' if law_type == 'universal' else 'structural_laws'
        top_laws = conn.execute(f"""
            SELECT pattern_id as law_id, pattern_name as law_name,
                   precision, n_activations as activation_count, best_regime, direction
            FROM {tbl}
            WHERE precision >= ?
              AND n_activations >= 10
            ORDER BY precision DESC
            LIMIT ?
        """, (min_prec, top_n)).fetchall()

        if not top_laws:
            return {"success": True, "message": "No high-precision laws to synthesize", "n_candidates": 0}

        composites = []
        laws_list = list(top_laws)
        for i in range(len(laws_list)):
            for j in range(i+1, len(laws_list)):
                la, lb = laws_list[i], laws_list[j]
                if la['direction'] != lb['direction']:
                    continue
                # Estimate combined precision (conservative: geometric mean - 0.05)
                import math
                est_prec = math.sqrt(la['precision'] * lb['precision']) - 0.05
                composites.append({
                    'name': f"{la['law_id']}_AND_{lb['law_id']}",
                    'law_a': la['law_id'],
                    'law_b': lb['law_id'],
                    'direction': la['direction'],
                    'est_precision': round(est_prec, 4),
                    'prec_a': round(la['precision'], 4),
                    'prec_b': round(lb['precision'], 4),
                    'same_regime': la['best_regime'] == lb['best_regime'],
                })

        composites.sort(key=lambda x: -x['est_precision'])

        return {
            "success": True,
            "n_base_laws": len(top_laws),
            "n_composites": len(composites),
            "top_composites": composites[:10],
            "message": "Review top composites — test them in strategy_tester.py",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 5. RUN CYCLE — full refinement pass
# ─────────────────────────────────────────────
def run_cycle(params):
    today    = params.get('date', datetime.now().strftime('%Y-%m-%d'))
    law_type = params.get('law_type', 'universal')
    dry_run  = params.get('dry_run', True)
    conn = db()
    ensure_tables(conn)
    try:
        # Step 1: Measure
        m = measure({'law_type': law_type})

        # Step 2: Prune
        p = prune({'law_type': law_type, 'max_precision': 0.15,
                   'min_activations': 50, 'dry_run': dry_run})

        # Step 3: Condition
        c = condition({'law_type': law_type, 'min_lift': 0.05})

        # Step 4: Synthesize
        s = synthesize({'law_type': law_type, 'min_precision': 0.45})

        # Step 5: Get unified signal stats if available
        try:
            sig_stats = conn.execute("""
                SELECT COUNT(*) as n, AVG(ues) as avg_ues,
                       SUM(CASE WHEN conviction='HIGH_CONVICTION' THEN 1 ELSE 0 END) as n_high
                FROM unified_signals
                WHERE signal_date = ?
            """, (today,)).fetchone()
            n_signals  = sig_stats['n'] or 0
            avg_ues    = sig_stats['avg_ues'] or 0
            n_high     = sig_stats['n_high'] or 0
        except:
            n_signals = avg_ues = n_high = 0

        # Log cycle
        conn.execute("""
            INSERT INTO refinement_cycles
            (run_date, laws_measured, laws_pruned, laws_updated, avg_precision,
             avg_ues, n_high_conv, n_signals, notes)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (today, m.get('n_laws', 0), p.get('n_pruned', 0),
              c.get('n_improvable', 0), m.get('avg_precision'),
              avg_ues, n_high, n_signals,
              f"regime_improvable={c.get('n_improvable', 0)}, composites_found={s.get('n_composites', 0)}"))
        conn.commit()

        return {
            "success": True,
            "run_date": today,
            "law_type": law_type,
            "dry_run": dry_run,
            "measure": {
                "n_laws": m.get('n_laws', 0),
                "avg_precision": m.get('avg_precision'),
                "n_grade_a": m.get('n_grade_a', 0),
                "n_grade_f": m.get('n_grade_f', 0),
            },
            "prune": {
                "n_candidates": p.get('n_candidates', 0),
                "n_pruned": p.get('n_pruned', 0),
            },
            "condition": {
                "n_improvable": c.get('n_improvable', 0),
                "avg_lift": c.get('avg_lift', 0),
            },
            "synthesize": {
                "n_base_laws": s.get('n_base_laws', 0),
                "n_composites": s.get('n_composites', 0),
            },
            "signals": {
                "n_signals": n_signals,
                "n_high_conviction": n_high,
                "avg_ues": round(avg_ues, 2),
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 6. HISTORY — cycle history + trend analysis
# ─────────────────────────────────────────────
def cycle_history(params):
    last_n = params.get('last_n', 10)
    conn = db()
    ensure_tables(conn)
    try:
        cycles = conn.execute("""
            SELECT * FROM refinement_cycles
            ORDER BY run_at DESC
            LIMIT ?
        """, (last_n,)).fetchall()

        result = []
        for c in cycles:
            result.append({
                'run_date': c['run_date'],
                'run_at': c['run_at'],
                'laws_measured': c['laws_measured'],
                'laws_pruned': c['laws_pruned'],
                'laws_updated': c['laws_updated'],
                'avg_precision': c['avg_precision'],
                'avg_ues': c['avg_ues'],
                'n_high_conv': c['n_high_conv'],
                'n_signals': c['n_signals'],
                'notes': c['notes'],
            })

        trend = {}
        if len(result) >= 2:
            first = result[-1]
            last  = result[0]
            if first['avg_precision'] and last['avg_precision']:
                trend['precision_delta'] = round(last['avg_precision'] - first['avg_precision'], 4)
            if first['avg_ues'] and last['avg_ues']:
                trend['ues_delta'] = round((last['avg_ues'] or 0) - (first['avg_ues'] or 0), 2)

        return {
            "success": True,
            "n_cycles": len(result),
            "cycles": result,
            "trend": trend,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

# ─────────────────────────────────────────────
# 7. BUILD FULL
# ─────────────────────────────────────────────
def build_full(params):
    today    = params.get('date', datetime.now().strftime('%Y-%m-%d'))
    law_type = params.get('law_type', 'universal')

    cycle = run_cycle({'date': today, 'law_type': law_type, 'dry_run': True})
    hist  = cycle_history({'last_n': 5})

    conn = db()
    ensure_tables(conn)
    try:
        # Get top signals
        try:
            top_sigs = conn.execute("""
                SELECT symbol, ues, conviction, tech_score, explosion_prob
                FROM unified_signals
                WHERE signal_date = ?
                  AND conviction IN ('HIGH_CONVICTION','MEDIUM_CONVICTION')
                ORDER BY ues DESC
                LIMIT 10
            """, (today,)).fetchall()
            signals = [dict(s) for s in top_sigs]
        except:
            signals = []
    finally:
        conn.close()

    return {
        "success": True,
        "date": today,
        "cycle": cycle,
        "history_summary": {
            "n_past_cycles": hist.get('n_cycles', 0),
            "trend": hist.get('trend', {}),
        },
        "top_signals": signals,
        "next_actions": _get_recommendations(cycle),
    }

def _get_recommendations(cycle):
    recs = []
    m = cycle.get('measure', {})
    p = cycle.get('prune', {})
    c = cycle.get('condition', {})
    s = cycle.get('synthesize', {})

    avg_prec = m.get('avg_precision', 0) or 0
    if avg_prec < 0.20:
        recs.append("🚨 CRITICAL: Average precision < 20% — activate regime-conditioning immediately")
    if p.get('n_pruned', 0) > 0:
        recs.append(f"✂️  {p['n_pruned']} laws ready to prune — run with dry_run=false to apply")
    if c.get('n_improvable', 0) > 0:
        recs.append(f"🎯 {c['n_improvable']} laws can gain {c.get('avg_lift',0):.1%} via regime conditioning")
    if s.get('n_composites', 0) > 0:
        recs.append(f"🔬 {s['n_composites']} composite law pairs to backtest")
    if not recs:
        recs.append("✅ System within normal parameters — continue monitoring")
    return recs

# ─────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────
if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    dispatch = {
        'measure':       measure,
        'prune':         prune,
        'condition':     condition,
        'synthesize':    synthesize,
        'run_cycle':     run_cycle,
        'cycle_history': cycle_history,
        'build_full':    build_full,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(params), default=str))
    else:
        print(json.dumps({"error": f"Unknown command: {cmd}. Use: {list(dispatch.keys())}"}))
