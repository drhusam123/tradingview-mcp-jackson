#!/usr/bin/env python3
"""
Phase 71 — Autonomous Research Director
"مدير البحث المستقل — يولّد، يختبر، يقيّم، يقتل، يطوّر، يبلّغ"

Commands: morning_run | status | top_alpha | history | generate_report | build_full
"""
import sys, json, sqlite3, subprocess
from datetime import datetime
from pathlib import Path

DB_PATH     = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
SCRIPTS_DIR = Path(__file__).parent

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS director_log (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date             TEXT,
        run_at               TEXT DEFAULT CURRENT_TIMESTAMP,
        phase                TEXT,
        hypotheses_total     INTEGER DEFAULT 0,
        hypotheses_tested    INTEGER DEFAULT 0,
        hypotheses_killed    INTEGER DEFAULT 0,
        hypotheses_evolved   INTEGER DEFAULT 0,
        new_alpha_found      INTEGER DEFAULT 0,
        top_hyp_id           TEXT,
        top_expectancy       REAL,
        top_grade            TEXT,
        oos_stable_count     INTEGER DEFAULT 0,
        elapsed_sec          REAL,
        summary              TEXT
    );
    CREATE TABLE IF NOT EXISTS daily_alpha_report (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date  TEXT UNIQUE,
        generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        n_alive      INTEGER DEFAULT 0,
        n_grade_s    INTEGER DEFAULT 0,
        n_grade_a    INTEGER DEFAULT 0,
        top_symbols  TEXT,
        top_hyp_ids  TEXT,
        avg_composite REAL,
        recommendations TEXT,
        full_report  TEXT
    );
    """)
    conn.commit()

def _run_script(script_name, command, params=None, timeout=300):
    """Call a Phase 68-70 Python script and return parsed JSON."""
    script = SCRIPTS_DIR / script_name
    args   = [sys.executable, str(script), command]
    if params:
        args.append(json.dumps(params))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                cwd=str(SCRIPTS_DIR.parent.parent))
        out = result.stdout.strip()
        # Get last JSON line
        for line in reversed(out.split('\n')):
            line = line.strip()
            if line.startswith('{') or line.startswith('['):
                return json.loads(line)
        return {"error": "No JSON output", "raw": out[-200:]}
    except subprocess.TimeoutExpired:
        return {"error": f"Script timeout after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# Morning autonomous research run
# ─────────────────────────────────────────────
def morning_run(params):
    today    = params.get('date', datetime.now().strftime('%Y-%m-%d'))
    max_test = int(params.get('max_test', 50))
    t0       = datetime.now()
    log      = []
    conn     = db()
    ensure_tables(conn)

    # ══ STEP 1: Refresh feature matrix (Ph 62) ═══════════════
    log.append("STEP 1: Refreshing feature matrix (Ph 62)...")
    feat = _run_script('feature_factory.py', 'build_features', {}, timeout=120)
    n_feat = feat.get('n_symbols', feat.get('n_rows', feat.get('symbols_processed', 0)))
    log.append(f"  → {n_feat} symbols updated in feature_matrix")

    # ══ STEP 2: Law quality refinement (Ph 67) ═══════════════
    log.append("STEP 2: Law quality refinement (Ph 67)...")
    ref = _run_script('refinement_cycle.py', 'measure', {}, timeout=60)
    n_laws     = ref.get('n_laws', ref.get('total_laws', 0))
    avg_prec   = ref.get('avg_precision', 0)
    log.append(f"  → {n_laws} laws measured, avg precision={avg_prec:.1%}")

    # ══ STEP 3: Generate hypotheses (Ph 68) ══════════════════
    log.append("STEP 3: Generating hypotheses (Ph 68)...")
    gen = _run_script('hypothesis_dsl.py', 'generate', {'mode': 'templates'})
    n_total = gen.get('total_hypotheses', 0)
    log.append(f"  → {gen.get('n_inserted', 0)} new | {n_total} total")

    # ══ STEP 3b: Quant discovery OOS rules (parallel alpha path) ══
    log.append("STEP 3b: Quant discovery rule refresh...")
    fb_params = {}
    fb_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'discovery_feedback_last.json')
    if os.path.exists(fb_path):
        try:
            with open(fb_path, encoding='utf-8') as f:
                fb_params['feedback_queue'] = json.load(f).get('queue', [])
        except Exception:
            pass
    p6_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'p6_research_context.json')
    if os.path.exists(p6_path):
        try:
            with open(p6_path, encoding='utf-8') as f:
                p6 = json.load(f)
            fb_params['p6_priorities'] = p6.get('research_priorities', [])
            fb_params['evolution_hints'] = p6.get('evolution_hints', {})
            fb_params['p6_gate'] = p6.get('p6_gate', {})
        except Exception:
            pass
    qd = _run_script('quant_discovery.py', 'run', fb_params, timeout=180)
    n_qd = qd.get('n_rules', qd.get('rules_saved', 0))
    log.append(f"  → quant_discovery: {n_qd} rules refreshed")

    # ══ STEP 4: Research grid — test new hypotheses (Ph 69) ══
    log.append("STEP 4: Research grid (Ph 69)...")
    _conn = sqlite3.connect(DB_PATH)
    n_untested = _conn.execute("""
        SELECT COUNT(*) FROM hypotheses h
        LEFT JOIN research_results r ON h.hyp_id = r.hyp_id
        WHERE r.hyp_id IS NULL
    """).fetchone()[0]
    _conn.close()
    if n_untested > 0:
        grid = _run_script('research_grid.py', 'run_grid',
                           {'limit': min(max_test, n_untested), 'workers': 2},
                           timeout=900)
        n_tested = grid.get('n_tested', 0)
        n_valid  = grid.get('n_valid', 0)
    else:
        n_tested, n_valid = 0, 0
        grid = {'message': 'all tested'}
    log.append(f"  → untested={n_untested}, tested={n_tested}, valid={n_valid}")

    # ══ STEP 5: Rank + kill + decay (Ph 70) ══════════════════
    log.append("STEP 5: Alpha ranking + pruning (Ph 70)...")
    rank       = _run_script('alpha_ranker.py', 'rank_all', {})
    n_ranked   = rank.get('n_ranked', 0)
    grade_dist = rank.get('grade_distribution', {})
    top_10     = rank.get('top_10', [])
    kill_preview = _run_script('alpha_ranker.py', 'kill_weak', {'dry_run': True})
    n_kill       = kill_preview.get('n_killed', 0)
    apply_kill   = params.get('apply_kill', True) and 0 < n_kill <= 5
    kill         = _run_script('alpha_ranker.py', 'kill_weak', {'dry_run': not apply_kill}) if apply_kill else kill_preview
    if apply_kill:
        n_kill = kill.get('n_killed', n_kill)
    decay      = _run_script('alpha_ranker.py', 'decay_check', {})
    n_decay    = decay.get('n_decaying', 0)
    n_stable   = decay.get('n_stable', 0)
    log.append(f"  → ranked={n_ranked} | S={grade_dist.get('S',0)} A={grade_dist.get('A',0)} B={grade_dist.get('B',0)}")
    log.append(f"  → kill_candidates={n_kill} | decaying={n_decay} | stable={n_stable}")

    # ══ STEP 6: Evolve top strategies ════════════════════════
    log.append("STEP 6: Evolving top strategies...")
    evo        = _run_script('alpha_ranker.py', 'evolve', {'n_top': 3, 'n_mutate': 3})
    n_children = evo.get('n_children', 0)
    log.append(f"  → {n_children} evolved variants generated")
    if n_children > 0:
        grid_evo = _run_script('research_grid.py', 'run_grid',
                               {'limit': min(n_children, 20), 'workers': 2},
                               timeout=900)
        n_evo_tested = grid_evo.get('n_tested', 0)
        log.append(f"  → evolved re-grid: tested={n_evo_tested}")

    # ══ STEP 7: UES signal scoring — skipped when daily pipeline will rescore post-scan ══
    skip_ues = bool(params.get('skip_ues_score'))
    n_sig = n_high = n_med = 0
    if skip_ues:
        log.append("STEP 7: UES scoring deferred — daily pipeline runs score_all after scan_today")
    else:
        log.append("STEP 7: UES scoring — all layers (Ph 62-67 + Ph 70 integrated)...")
        sig    = _run_script('signal_integration.py', 'score_all', {'date': today})
        n_sig  = sig.get('n_scored', 0)
        n_high = sig.get('n_high', 0)
        n_med  = sig.get('n_medium', sig.get('n_med', 0))
        log.append(f"  → scored={n_sig} | HIGH={n_high} | MED={n_med}")

    # ── Summary ─────────────────────────────────────────────
    elapsed = (datetime.now() - t0).total_seconds()
    top_hyp = top_10[0] if top_10 else {}

    conn.execute("""
        INSERT INTO director_log
        (run_date, phase, hypotheses_total, hypotheses_tested, hypotheses_killed,
         hypotheses_evolved, new_alpha_found, top_hyp_id, top_expectancy,
         top_grade, oos_stable_count, elapsed_sec, summary)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (today, 'morning_run_v2', n_total, n_tested, n_kill, n_children,
          grade_dist.get('S', 0) + grade_dist.get('A', 0),
          top_hyp.get('hyp_id'), top_hyp.get('expectancy_pct'),
          top_hyp.get('grade'), n_stable, elapsed, "\n".join(log)))
    conn.commit()
    conn.close()

    # ── Build unified recommendations ────────────────────────
    recs = []
    if grade_dist.get('S', 0) > 0 or grade_dist.get('A', 0) > 0:
        recs.append(f"🏆 {grade_dist.get('S',0)+grade_dist.get('A',0)} Grade-S/A strategies confirmed — ready for live deployment")
    if n_high > 0:
        recs.append(f"⚡ {n_high} HIGH_CONVICTION UES signals today (cross-validated with alpha grid)")
    if n_kill > 0:
        recs.append(f"✂️  {n_kill} weak strategies flagged — run egx:alpha:kill --apply to remove")
    if n_decay > 0:
        recs.append(f"⚠️  {n_decay} strategies showing alpha decay — reduce position sizing")
    if n_children > 0:
        recs.append(f"🧬 {n_children} evolved variants queued — run egx:grid:run to backtest")
    if avg_prec < 0.40:
        recs.append(f"📉 Law quality degraded (avg={avg_prec:.1%}) — consider regime conditioning")
    if not recs:
        recs.append("📊 System healthy — all layers running normally")

    return {
        "success":     True,
        "date":        today,
        "elapsed_sec": round(elapsed, 1),
        "steps_completed": 7,
        "steps":       log,
        "summary": {
            "feature_symbols":    n_feat,
            "laws_measured":      n_laws,
            "law_avg_precision":  round(avg_prec, 4) if avg_prec else None,
            "hypotheses_total":   n_total,
            "tested_today":       n_tested,
            "valid_results":      n_valid,
            "ranked":             n_ranked,
            "grade_s":            grade_dist.get('S', 0),
            "grade_a":            grade_dist.get('A', 0),
            "grade_b":            grade_dist.get('B', 0),
            "kill_candidates":    n_kill,
            "decaying":           n_decay,
            "stable":             n_stable,
            "evolved":            n_children,
            "ues_scored":         n_sig,
            "high_conviction":    n_high,
            "medium_conviction":  n_med,
        },
        "top_alpha":       top_10[:5],
        "recommendations": recs,
    }

# ─────────────────────────────────────────────
# System status
# ─────────────────────────────────────────────
def status(params):
    conn = db()
    ensure_tables(conn)

    # From research_results
    try:
        rr = conn.execute("""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) active,
                   SUM(CASE WHEN status='KILLED' THEN 1 ELSE 0 END) killed,
                   AVG(CASE WHEN status='ACTIVE' THEN expectancy_pct END) avg_exp,
                   MAX(CASE WHEN status='ACTIVE' THEN expectancy_pct END) best_exp
            FROM research_results
        """).fetchone()
    except:
        rr = None

    # From alpha_rankings
    try:
        ar = conn.execute("""
            SELECT COUNT(*) n_alive,
                   SUM(CASE WHEN grade='S' THEN 1 ELSE 0 END) n_s,
                   SUM(CASE WHEN grade='A' THEN 1 ELSE 0 END) n_a,
                   AVG(composite_score) avg_score
            FROM alpha_rankings WHERE is_alive=1
        """).fetchone()
    except:
        ar = None

    # Last director run
    last = conn.execute("""
        SELECT * FROM director_log ORDER BY run_at DESC LIMIT 1
    """).fetchone()

    # Total hypotheses
    try:
        n_hyps = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    except:
        n_hyps = 0

    conn.close()

    return {
        "success": True,
        "hypotheses": {
            "total":  n_hyps,
            "tested": rr['total'] if rr else 0,
            "active": rr['active'] if rr else 0,
            "killed": rr['killed'] if rr else 0,
        },
        "alpha": {
            "n_alive":     ar['n_alive'] if ar else 0,
            "n_grade_s":   ar['n_s']     if ar else 0,
            "n_grade_a":   ar['n_a']     if ar else 0,
            "avg_score":   round(ar['avg_score'], 1) if ar and ar['avg_score'] else None,
            "avg_exp_pct": round(rr['avg_exp'], 3)   if rr and rr['avg_exp']   else None,
            "best_exp_pct":round(rr['best_exp'], 3)  if rr and rr['best_exp']  else None,
        },
        "last_run": dict(last) if last else None,
    }

# ─────────────────────────────────────────────
# Top alpha findings
# ─────────────────────────────────────────────
def top_alpha(params):
    limit   = int(params.get('limit', 15))
    min_exp = float(params.get('min_expectancy', 0.0))
    conn    = db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT ar.hyp_id, ar.hyp_name, ar.composite_score, ar.grade,
               ar.expectancy_pct, ar.oos_score, ar.win_rate_pct, ar.n_activations,
               h.conditions_json, h.direction, h.holding_days, h.category,
               r.is_samples, r.oos_samples, r.avg_net_return
        FROM alpha_rankings ar
        JOIN research_results r ON ar.hyp_id = r.hyp_id
        LEFT JOIN hypotheses h ON ar.hyp_id = h.hyp_id
        WHERE ar.is_alive = 1 AND ar.expectancy_pct >= ?
        ORDER BY ar.composite_score DESC
        LIMIT ?
    """, (min_exp, limit)).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        try:
            conds = json.loads(d.pop('conditions_json', '[]'))
            d['conditions'] = [{
                'feature': c['col'],
                'op':      c['op'],
                'value':   c['val'],
            } for c in conds]
            d['conditions_str'] = ' AND '.join(
                f"{c['col']}{c['op']}{c['val']}" for c in conds)
        except:
            d.pop('conditions_json', None)
        result.append(d)

    return {
        "success":    True,
        "n_results":  len(result),
        "top_alpha":  result,
    }

# ─────────────────────────────────────────────
# Director run history
# ─────────────────────────────────────────────
def history(params):
    last_n = int(params.get('last_n', 10))
    conn   = db()
    ensure_tables(conn)
    rows   = conn.execute("""
        SELECT run_date, run_at, hypotheses_total, hypotheses_tested,
               hypotheses_killed, hypotheses_evolved, new_alpha_found,
               top_hyp_id, top_expectancy, top_grade, elapsed_sec
        FROM director_log
        ORDER BY run_at DESC
        LIMIT ?
    """, (last_n,)).fetchall()
    conn.close()

    return {
        "success":  True,
        "n_runs":   len(rows),
        "runs":     [dict(r) for r in rows],
    }

# ─────────────────────────────────────────────
# Generate daily alpha report
# ─────────────────────────────────────────────
def generate_report(params):
    """
    Unified Daily Alpha Report — merges ALL layers:
      Ph 62 feature quality | Ph 64 law confirmation | Ph 65 UES signals
      Ph 67 refinement | Ph 69 research grid | Ph 70 alpha rankings
    """
    today  = params.get('date', datetime.now().strftime('%Y-%m-%d'))
    conn   = db()
    ensure_tables(conn)

    top_res = top_alpha({'limit': 10, 'min_expectancy': 0.0})
    st      = status({})
    al      = st.get('alpha', {})
    hyp_st  = st.get('hypotheses', {})

    # ── Layer 1: UES signals (Ph 65) ────────────────────────
    try:
        sigs = conn.execute("""
            SELECT symbol, unified_score, conviction_tier, active_regime,
                   explosion_score, technical_score, breadth_score,
                   n_confirming_laws, top_law, liquidity_tier, max_position_egp
            FROM unified_signals
            WHERE signal_date=? AND conviction_tier IN ('HIGH_CONVICTION','MEDIUM_CONVICTION')
            ORDER BY unified_score DESC LIMIT 20
        """, (today,)).fetchall()
        ues_high   = [dict(s) for s in sigs if s['conviction_tier'] == 'HIGH_CONVICTION']
        ues_med    = [dict(s) for s in sigs if s['conviction_tier'] == 'MEDIUM_CONVICTION']
        top_symbols= [s['symbol'] for s in sigs]
        n_high     = len(ues_high)
    except Exception:
        ues_high = []; ues_med = []; top_symbols = []; n_high = 0

    # ── Layer 2: Alpha strategies (Ph 69-70) ────────────────
    top_strategies = top_res.get('top_alpha', [])[:8]
    top_hyp_ids    = [r['hyp_id'] for r in top_strategies]

    # ── Layer 3: Law quality snapshot (Ph 67) ───────────────
    try:
        law_snap = conn.execute("""
            SELECT AVG(precision) avg_prec, COUNT(*) n_laws,
                   SUM(CASE WHEN precision >= 0.55 THEN 1 ELSE 0 END) n_elite
            FROM universal_laws_p16 WHERE n_activations >= 10
        """).fetchone()
        law_info = {
            'n_laws':  law_snap['n_laws'] if law_snap else 0,
            'avg_precision': round(law_snap['avg_prec'] or 0, 3) if law_snap else 0,
            'n_elite': law_snap['n_elite'] if law_snap else 0,
        }
    except Exception:
        law_info = {'n_laws': 0, 'avg_precision': 0, 'n_elite': 0}

    # ── Layer 4: Research grid health (Ph 69) ───────────────
    try:
        grid_snap = conn.execute("""
            SELECT COUNT(*) n_total,
                   SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) n_active,
                   AVG(CASE WHEN status='ACTIVE' THEN expectancy_pct END) avg_exp,
                   MAX(CASE WHEN status='ACTIVE' THEN expectancy_pct END) best_exp
            FROM research_results
        """).fetchone()
        grid_info = {
            'n_total':  grid_snap['n_total'] if grid_snap else 0,
            'n_active': grid_snap['n_active'] if grid_snap else 0,
            'avg_exp':  round(grid_snap['avg_exp'] or 0, 3) if grid_snap else 0,
            'best_exp': round(grid_snap['best_exp'] or 0, 3) if grid_snap else 0,
        }
    except Exception:
        grid_info = {'n_total': 0, 'n_active': 0, 'avg_exp': 0, 'best_exp': 0}

    # ── Unified recommendations ──────────────────────────────
    recs = []
    n_sa = al.get('n_grade_s', 0) + al.get('n_grade_a', 0)
    if al.get('n_grade_s', 0) > 0:
        recs.append(f"🏆 {al['n_grade_s']} Grade-S strategies (OOS-validated) — eligible for live deployment")
    if al.get('n_grade_a', 0) > 0:
        recs.append(f"⭐ {al['n_grade_a']} Grade-A strategies — paper-trade recommended")
    if n_high > 0:
        recs.append(f"⚡ {n_high} HIGH_CONVICTION UES signals (law-confirmed + alpha-boosted): {', '.join(top_symbols[:6])}")
    if grid_info['best_exp'] > 2.0:
        recs.append(f"🎯 Best strategy expectancy +{grid_info['best_exp']:.2f}% after 150bps EGX costs")
    if al.get('avg_exp_pct') and al['avg_exp_pct'] > 0:
        recs.append(f"✅ Portfolio avg expectancy +{al['avg_exp_pct']:.2f}% — positive systematic edge")
    if law_info['n_elite'] >= 5:
        recs.append(f"📐 {law_info['n_elite']} elite laws (>55% precision) providing signal confirmation")
    if not recs:
        recs.append("📊 System healthy — all 10 phases running. Run egx:director:morning daily.")

    full_report = {
        "date":              today,
        "ues_layer":         {"high": ues_high[:5], "medium": ues_med[:3], "n_high": n_high, "n_medium": len(ues_med)},
        "alpha_layer":       {"top_strategies": top_strategies[:5], "grade_dist": al, "grid_health": grid_info},
        "law_layer":         law_info,
        "system_status":     {"hypotheses": hyp_st, "alpha": al},
        "recommendations":   recs,
        "generated_at":      datetime.now().isoformat(),
    }

    conn.execute("""
        INSERT OR REPLACE INTO daily_alpha_report
        (report_date, n_alive, n_grade_s, n_grade_a, top_symbols, top_hyp_ids,
         avg_composite, recommendations, full_report)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (today, al.get('n_alive', 0), al.get('n_grade_s', 0), al.get('n_grade_a', 0),
          json.dumps(top_symbols[:10]), json.dumps(top_hyp_ids[:5]),
          al.get('avg_score'), json.dumps(recs), json.dumps(full_report)))
    conn.commit()
    conn.close()

    return {
        "success":          True,
        "report_date":      today,
        "generated_at":     datetime.now().isoformat(),
        "n_alive_strategies": al.get('n_alive', 0),
        "n_grade_s":        al.get('n_grade_s', 0),
        "n_grade_a":        al.get('n_grade_a', 0),
        "avg_composite":    al.get('avg_score'),
        "best_expectancy":  grid_info['best_exp'],
        "top_ues_symbols":  top_symbols[:8],
        "top_strategies":   top_strategies[:5],
        "law_quality":      law_info,
        "sections":         {"ues": full_report["ues_layer"], "alpha": full_report["alpha_layer"], "laws": law_info},
        "recommendations":  recs,
        "executive_summary": {
            "market_bias":    "BULL" if n_high >= 5 else "NEUTRAL" if n_high >= 2 else "CAUTIOUS",
            "alpha_quality":  "EXCELLENT" if al.get('n_grade_s', 0) >= 5 else "GOOD" if n_sa >= 3 else "DEVELOPING",
            "n_actionable":   min(n_high, n_sa),
            "risk_level":     "LOW" if grid_info['avg_exp'] > 1.0 else "MEDIUM" if grid_info['avg_exp'] > 0 else "HIGH",
            "key_insight":    recs[0] if recs else "System running normally",
        },
    }

# ─────────────────────────────────────────────
# Build full
# ─────────────────────────────────────────────
def build_full(params):
    st   = status({})
    top  = top_alpha({'limit': 5, 'min_expectancy': 0.0})
    rep  = generate_report(params)
    hist = history({'last_n': 3})
    return {
        "success":       True,
        "status":        st,
        "report":        rep,
        "top_alpha":     top['top_alpha'][:5],
        "recent_runs":   hist['runs'],
    }

# ─────────────────────────────────────────────
if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'status'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    dispatch = {
        'morning_run':     morning_run,
        'status':          status,
        'top_alpha':       top_alpha,
        'history':         history,
        'generate_report': generate_report,
        'build_full':      build_full,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(params), default=str))
    else:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(dispatch.keys())}))
