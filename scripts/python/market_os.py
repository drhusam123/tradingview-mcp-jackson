#!/usr/bin/env python3
"""
Market Operating System — Phase 10
====================================
Production-grade autonomous operational layer for the EGX cognition stack.

Commands:
  pipeline_run       Full daily autonomous pipeline (data → stack → arbitrate → alert → report → archive → health)
  pipeline_status    Last pipeline run summary
  dashboard          Real-time condensed cognition dashboard
  alert_scan         Intelligent alert scanner (10 conditions, deduplication)
  archive_snapshot   Archive today's full cognition snapshot
  health_monitor     Comprehensive system health (DB, layers, freshness, latency)
  resilience_check   Individual layer resilience testing with retry
  observability      Execution metrics, step timings, confidence trends
  historical_replay  Timeline of past cognition snapshots
  os_full            Full OS status (dashboard + alerts + health + observability)
"""
import sys, json, time, pathlib, statistics, sqlite3, traceback, datetime, os
import importlib.util as _ilu

HERE     = pathlib.Path(__file__).parent
ROOT     = HERE.parent.parent
DATA     = ROOT / 'data'
ARCH_DIR = DATA / 'cognition_archive'

DB_PATH       = str(DATA / 'egx_trading.db')
PIPELINE_LOG  = str(DATA / 'pipeline_log.json')
ALERT_LOG     = str(DATA / 'alert_log.json')
HEALTH_LOG    = str(DATA / 'health_metrics.json')
ORCH_LOG      = str(DATA / 'orchestrator_log.json')
EVO_LOG       = str(DATA / 'evolution_memory.json')
COUP_LOG      = str(DATA / 'world_coupling_log.json')

COMMANDS = {
    'pipeline_run', 'pipeline_status', 'dashboard',
    'alert_scan', 'archive_snapshot', 'health_monitor',
    'resilience_check', 'observability', 'historical_replay', 'os_full',
}

ALERT_TYPES = [
    'REGIME_SHIFT', 'CONFIDENCE_COLLAPSE', 'TOPOLOGY_FRAGMENTATION',
    'CAUSAL_INSTABILITY', 'VOLATILITY_RELEASE', 'CONTAGION_SPIKE',
    'EXPOSURE_REDUCTION', 'HIGH_CONVICTION_OPP', 'CRITICAL_SYSTEM_FAILURE',
    'MACRO_REGIME_CHANGE',
]

# ── Module loader ─────────────────────────────────────────────────────────────

_MODULE_CACHE = {}

def _load_mod(name, path):
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    try:
        spec = _ilu.spec_from_file_location(name, str(path))
        mod  = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE_CACHE[name] = mod
        return mod
    except Exception as e:
        return None

def orch():
    return _load_mod('cognitive_orchestrator', HERE / 'cognitive_orchestrator.py')

# ── JSON log helpers ──────────────────────────────────────────────────────────

def load_json_log(path):
    try:
        p = pathlib.Path(path)
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []

def append_json_log(path, entry, max_entries=365):
    log = load_json_log(path)
    if not isinstance(log, list):
        log = []
    log.append(entry)
    if len(log) > max_entries:
        log = log[-max_entries:]
    try:
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(path).write_text(json.dumps(log, indent=2, default=str))
    except Exception:
        pass

# ── DB initialization ─────────────────────────────────────────────────────────

def ensure_tables(con):
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_date     TEXT PRIMARY KEY,
                status       TEXT,
                steps_done   INTEGER,
                steps_total  INTEGER,
                duration_sec REAL,
                error        TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS cognition_snapshots (
                snapshot_date     TEXT PRIMARY KEY,
                regime            TEXT,
                confidence        REAL,
                posture           TEXT,
                exposure_pct      REAL,
                n_conflicts       INTEGER,
                layer_health_json TEXT,
                opportunities_json TEXT,
                macro_regime      TEXT,
                created_at        TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS alert_history (
                alert_id   TEXT,
                alert_date TEXT,
                alert_type TEXT,
                severity   TEXT,
                message    TEXT,
                sent       INTEGER DEFAULT 0,
                PRIMARY KEY (alert_type, alert_date)
            );
        """)
        con.commit()
    except Exception:
        pass

# ── Alert deduplication ───────────────────────────────────────────────────────

def is_alert_dup(db, alert_type, today=None):
    today = today or time.strftime('%Y-%m-%d')
    try:
        row = db.execute(
            "SELECT 1 FROM alert_history WHERE alert_type=? AND alert_date=?",
            [alert_type, today]
        ).fetchone()
        return row is not None
    except Exception:
        return False

def save_alert(db, con, alert_type, severity, message, today=None):
    today = today or time.strftime('%Y-%m-%d')
    try:
        db.execute(
            "INSERT OR IGNORE INTO alert_history (alert_id, alert_date, alert_type, severity, message) VALUES (?,?,?,?,?)",
            [f"{alert_type}_{today}", today, alert_type, severity, message]
        )
        con.commit()
    except Exception:
        pass

# ── Alert scanning ────────────────────────────────────────────────────────────

def scan_alerts(layers, conflicts, confidence, posture_r, macro, orch_log, db=None, con=None):
    """Scan 10 alert conditions with deduplication."""
    today    = time.strftime('%Y-%m-%d')
    alerts   = []
    prev     = orch_log[-1] if (isinstance(orch_log, list) and orch_log) else {}
    prev_c   = prev.get('global_confidence', confidence)
    prev_reg = prev.get('regime', layers['latent']['regime'])
    posture  = posture_r.get('posture', 'NEUTRAL') if posture_r else 'NEUTRAL'

    def _alert(atype, severity, msg):
        if db and is_alert_dup(db, atype, today):
            return  # already fired today
        alerts.append({'type': atype, 'severity': severity, 'message': msg, 'date': today})
        if db and con:
            save_alert(db, con, atype, severity, msg, today)

    # 1. Regime shift
    curr_reg = layers['latent']['regime']
    if prev_reg and curr_reg != prev_reg:
        _alert('REGIME_SHIFT', 'HIGH',
               f"Regime shifted {prev_reg} → {curr_reg} | confidence={confidence:.1%}")

    # 2. Confidence collapse (>12% drop in one day)
    if (prev_c - confidence) > 0.12:
        _alert('CONFIDENCE_COLLAPSE', 'CRITICAL',
               f"Confidence collapsed from {prev_c:.1%} → {confidence:.1%}")

    # 3. Topology fragmentation (propagation health < 0.30)
    prop_h = layers.get('propagation', {}).get('health', 1.0)
    if prop_h < 0.30:
        _alert('TOPOLOGY_FRAGMENTATION', 'HIGH',
               f"Propagation layer critically fragmented (health={prop_h:.2f})")

    # 4. Causal instability (causality health < 0.40)
    caus_h = layers.get('causality', {}).get('health', 1.0)
    if caus_h < 0.40:
        _alert('CAUSAL_INSTABILITY', 'HIGH',
               f"Causality layer degraded (health={caus_h:.2f}) — unreliable signal sequencing")

    # 5. Volatility release (energy HIGH + low prev vol)
    energy_h = layers.get('energy', {}).get('health', 0.5)
    energy_d = layers.get('energy', {}).get('detail', '')
    if energy_h > 0.85 and 'vol_r=' in energy_d:
        try:
            vol_r = float(energy_d.split('vol_r=')[1].split(',')[0])
            if vol_r > 2.0:
                _alert('VOLATILITY_RELEASE', 'MEDIUM',
                       f"Volatility spike detected — vol_ratio={vol_r:.2f}")
        except Exception:
            pass

    # 6. Contagion spike (propagation ρ > 0.65 = systemic)
    prop_d = layers.get('propagation', {}).get('detail', '')
    if 'ρ=' in prop_d:
        try:
            rho = float(prop_d.split('ρ=')[1].split(' ')[0])
            if rho > 0.65:
                _alert('CONTAGION_SPIKE', 'CRITICAL',
                       f"Cross-sector contagion detected (ρ={rho:.3f}) — systemic move")
        except Exception:
            pass

    # 7. Exposure reduction signal
    if posture in ('DEFENSIVE', 'AVOID') and prev.get('posture') not in ('DEFENSIVE', 'AVOID'):
        _alert('EXPOSURE_REDUCTION', 'HIGH',
               f"Posture downgraded → {posture} | exposure={posture_r.get('exposure_pct',0):.1f}%")

    # 8. High-conviction opportunities (layer health > 0.8 across 4+ layers)
    n_healthy = sum(1 for v in layers.values() if v.get('health', 0) >= 0.80)
    if n_healthy >= 5 and curr_reg == 'BULL' and confidence >= 0.80:
        _alert('HIGH_CONVICTION_OPP', 'MEDIUM',
               f"High-conviction environment — {n_healthy}/8 layers healthy, conf={confidence:.1%}")

    # 9. Critical system failure (any layer < 0.15)
    for lname, lv in layers.items():
        if lv.get('health', 1.0) < 0.15:
            _alert('CRITICAL_SYSTEM_FAILURE', 'CRITICAL',
                   f"Layer {lname} critically failed (health={lv['health']:.3f})")
            break

    # 10. Macro regime change
    if macro:
        macro_reg = macro.get('macro_regime', '')
        prev_mac  = prev.get('dominant_coupling', macro_reg)
        if prev_mac and macro_reg and macro_reg != prev_mac:
            _alert('MACRO_REGIME_CHANGE', 'HIGH',
                   f"Macro regime changed: {prev_mac} → {macro_reg}")

    return alerts

# ── Step execution with retry ─────────────────────────────────────────────────

def run_step(step_name, fn, retries=3, delay_sec=0.5):
    last_err = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            result = fn()
            return {
                'step':        step_name,
                'status':      'OK',
                'duration_sec': round(time.time() - t0, 3),
                'attempt':     attempt + 1,
                'result':      result,
            }
        except Exception as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(delay_sec)
    return {
        'step':    step_name,
        'status':  'FAILED',
        'error':   last_err,
        'attempt': retries,
    }

# ── Full orchestration computation ────────────────────────────────────────────

def _compute_all(db, data, cur_ind, macro):
    """Run full cognitive stack and return all computed state."""
    o = orch()
    snaps      = o.latest_snapshot(data, cur_ind)
    layers     = o.run_all_layers(data, cur_ind, macro)
    confidence = o.compute_confidence(layers)
    conflicts  = o.detect_conflicts(layers)
    arb        = o.arbitrate(layers, conflicts)
    posture_r  = o.compute_posture(layers, conflicts, confidence, macro)
    watch      = o.cmd_instability_watch(layers, conflicts, confidence)
    opps       = o.find_opportunities(snaps)
    avoids     = o.find_avoid_zones(snaps)
    return dict(
        snaps=snaps, layers=layers, confidence=confidence,
        conflicts=conflicts, arb=arb, posture_r=posture_r,
        watch=watch, opps=opps, avoids=avoids,
    )

# ── Archive helper ────────────────────────────────────────────────────────────

def _archive_snapshot(db, con, state, macro, data):
    today = time.strftime('%Y-%m-%d')
    layers    = state['layers']
    posture_r = state['posture_r']
    opps      = state['opps']

    layer_health_json = json.dumps(
        {k: {'health': round(v['health'],4), 'state': v['state']} for k,v in layers.items()},
        default=str
    )
    opps_json = json.dumps(
        [{'symbol': o['symbol'], 'score': round(o.get('score',0),3)} for o in opps[:10]],
        default=str
    )

    # Write to DB
    try:
        db.execute("""
            INSERT OR REPLACE INTO cognition_snapshots
            (snapshot_date, regime, confidence, posture, exposure_pct, n_conflicts,
             layer_health_json, opportunities_json, macro_regime, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
        """, [
            today,
            layers['latent']['regime'],
            round(state['confidence'], 4),
            posture_r.get('posture', 'NEUTRAL'),
            round(posture_r.get('exposure_pct', 0), 2),
            len(state['conflicts']),
            layer_health_json,
            opps_json,
            (macro or {}).get('macro_regime', 'UNKNOWN'),
        ])
        con.commit()
    except Exception:
        pass

    # Write JSON archive file
    ARCH_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCH_DIR / f"{today}.json"
    snapshot = {
        'snapshot_date':    today,
        'regime':           layers['latent']['regime'],
        'confidence':       round(state['confidence'], 4),
        'posture':          posture_r.get('posture', 'NEUTRAL'),
        'exposure_pct':     round(posture_r.get('exposure_pct', 0), 2),
        'n_conflicts':      len(state['conflicts']),
        'arbitration':      state['arb'].get('winner'),
        'escalation':       state['watch'].get('escalation_level'),
        'trust':            layers['evolution'].get('trust', 'UNKNOWN'),
        'macro_regime':     (macro or {}).get('macro_regime', 'UNKNOWN'),
        'layer_health':     {k: {'health': round(v['health'],4), 'state': v['state']} for k,v in layers.items()},
        'top_opps':         [{'symbol': o['symbol'], 'score': round(o.get('score',0),3)} for o in opps[:5]],
        'n_symbols':        len(data),
        'created_at':       time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        archive_path.write_text(json.dumps(snapshot, indent=2, default=str))
    except Exception:
        pass

    return {'archived': True, 'path': str(archive_path), 'date': today}

# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_pipeline_run(db, con):
    """Full autonomous daily pipeline with retry, checkpointing, archiving."""
    o = orch()
    if o is None:
        return {'error': 'Failed to load cognitive_orchestrator module'}

    today          = time.strftime('%Y-%m-%d')
    pipeline_start = time.time()

    # ── Load data (critical step, no retry needed — just fail fast) ───────────
    try:
        data, cur_ind = o.load_ohlcv(db)
        o.enrich(data)
        macro = o.load_macro(db)
        n_symbols = len(data)
    except Exception as e:
        result = {'status': 'CRITICAL_FAILURE', 'error': f'Data load failed: {e}', 'date': today}
        append_json_log(PIPELINE_LOG, {**result, 'duration_sec': 0})
        return result

    # ── Pipeline state shared across steps ────────────────────────────────────
    _state = {}

    def step_data_validate():
        return o.cmd_data_health(db, data, cur_ind, macro)

    def step_stack_recompute():
        _state['snaps']  = o.latest_snapshot(data, cur_ind)
        _state['layers'] = o.run_all_layers(data, cur_ind, macro)
        return {'layers_computed': 8, 'states': {k: v['state'] for k,v in _state['layers'].items()}}

    def step_orchestrate():
        L = _state['layers']
        _state['confidence'] = o.compute_confidence(L)
        _state['conflicts']  = o.detect_conflicts(L)
        _state['arb']        = o.arbitrate(L, _state['conflicts'])
        _state['posture_r']  = o.compute_posture(L, _state['conflicts'], _state['confidence'], macro)
        _state['watch']      = o.cmd_instability_watch(L, _state['conflicts'], _state['confidence'])
        return {
            'confidence':  round(_state['confidence'], 4),
            'posture':     _state['posture_r']['posture'],
            'n_conflicts': len(_state['conflicts']),
            'winner':      _state['arb']['winner'],
        }

    def step_alert_scan():
        orch_log = load_json_log(ORCH_LOG)
        alerts = scan_alerts(
            _state['layers'], _state['conflicts'], _state['confidence'],
            _state['posture_r'], macro, orch_log, db, con
        )
        _state['alerts'] = alerts
        return {'n_alerts': len(alerts), 'alerts': [a['type'] for a in alerts]}

    def step_report_gen():
        return o.cmd_daily_report(db, con, data, cur_ind, macro)

    def step_evolution_sync():
        sync_r = o.cmd_evolution_sync(_state['layers'], _state['confidence'], _state['posture_r'])
        _state['sync'] = sync_r
        return {'sync_status': sync_r.get('sync_status')}

    def step_archive():
        _state['opps']  = o.find_opportunities(_state['snaps'])
        _state['avoids'] = o.find_avoid_zones(_state['snaps'])
        full_state = {
            'layers': _state['layers'], 'confidence': _state['confidence'],
            'conflicts': _state['conflicts'], 'arb': _state['arb'],
            'posture_r': _state['posture_r'], 'watch': _state['watch'],
            'opps': _state.get('opps', []), 'avoids': _state.get('avoids', []),
        }
        return _archive_snapshot(db, con, full_state, macro, data)

    def step_health_check():
        return _cmd_health_monitor_impl(db, data, cur_ind, macro)

    STEPS = [
        ('data_validate',   step_data_validate,   3),
        ('stack_recompute', step_stack_recompute,  2),
        ('orchestrate',     step_orchestrate,      2),
        ('alert_scan',      step_alert_scan,       2),
        ('report_gen',      step_report_gen,       2),
        ('evolution_sync',  step_evolution_sync,   2),
        ('archive',         step_archive,          2),
        ('health_check',    step_health_check,     2),
    ]

    steps_results = []
    n_failed = 0
    for sname, sfn, retries in STEPS:
        r = run_step(sname, sfn, retries=retries)
        # Strip large result objects from log entry
        log_r = {k: v for k, v in r.items() if k != 'result'}
        if r.get('status') == 'OK' and r.get('result'):
            res = r['result']
            log_r['summary'] = str(res)[:120] if not isinstance(res, dict) else {
                k: v for k,v in list(res.items())[:4]
            }
        steps_results.append(log_r)
        if r['status'] == 'FAILED':
            n_failed += 1

    total_dur    = round(time.time() - pipeline_start, 2)
    steps_ok     = len(STEPS) - n_failed
    overall_stat = 'OK' if n_failed == 0 else 'PARTIAL' if n_failed <= 2 else 'DEGRADED'

    # ── Save to DB ────────────────────────────────────────────────────────────
    try:
        db.execute("""
            INSERT OR REPLACE INTO pipeline_runs
            (run_date, status, steps_done, steps_total, duration_sec, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, [today, overall_stat, steps_ok, len(STEPS), total_dur])
        con.commit()
    except Exception:
        pass

    # ── Append to pipeline_log ────────────────────────────────────────────────
    log_entry = {
        'date':         today,
        'status':       overall_stat,
        'steps_done':   steps_ok,
        'steps_total':  len(STEPS),
        'duration_sec': total_dur,
        'n_symbols':    n_symbols,
        'steps':        steps_results,
    }
    append_json_log(PIPELINE_LOG, log_entry)

    return {
        'date':          today,
        'status':        overall_stat,
        'steps_done':    steps_ok,
        'steps_total':   len(STEPS),
        'duration_sec':  total_dur,
        'n_symbols':     n_symbols,
        'confidence':    round(_state.get('confidence', 0), 4),
        'posture':       (_state.get('posture_r') or {}).get('posture', 'N/A'),
        'n_conflicts':   len(_state.get('conflicts', [])),
        'n_alerts':      len(_state.get('alerts', [])),
        'alert_types':   [a['type'] for a in _state.get('alerts', [])],
        'steps':         steps_results,
    }


def cmd_pipeline_status(db):
    """Return last pipeline run summary from log + DB."""
    pipe_log = load_json_log(PIPELINE_LOG)
    last = pipe_log[-1] if isinstance(pipe_log, list) and pipe_log else {}

    # Also query DB for the last 7 runs
    runs_db = []
    try:
        rows = db.execute("""
            SELECT run_date, status, steps_done, steps_total, duration_sec, created_at
            FROM pipeline_runs ORDER BY run_date DESC LIMIT 7
        """).fetchall()
        runs_db = [dict(r) for r in rows]
    except Exception:
        pass

    return {
        'last_run':         last.get('date', 'never'),
        'last_status':      last.get('status', 'UNKNOWN'),
        'last_duration_sec': last.get('duration_sec'),
        'n_symbols':        last.get('n_symbols'),
        'n_alerts':         last.get('n_alerts', 0),
        'steps_done':       last.get('steps_done'),
        'steps_total':      last.get('steps_total'),
        'total_runs':       len(pipe_log) if isinstance(pipe_log, list) else 0,
        'recent_runs':      runs_db,
        'log_entries':      len(pipe_log) if isinstance(pipe_log, list) else 0,
    }


def cmd_dashboard(db, data, cur_ind, macro):
    """Real-time condensed cognition dashboard."""
    state    = _compute_all(db, data, cur_ind, macro)
    orch_log = load_json_log(ORCH_LOG)
    pipe_log = load_json_log(PIPELINE_LOG)
    if not isinstance(orch_log, list):
        orch_log = []

    # Confidence trend last 14 days
    conf_trend = [
        {'date': e.get('date', ''), 'conf': round(e.get('global_confidence', 0), 3)}
        for e in orch_log[-14:]
    ]

    # Regime evolution
    regime_hist = [
        {'date': e.get('date', ''), 'regime': e.get('regime', ''), 'posture': e.get('posture', '')}
        for e in orch_log[-7:]
    ]

    # Alert scan
    alerts = scan_alerts(
        state['layers'], state['conflicts'], state['confidence'],
        state['posture_r'], macro, orch_log
    )

    last_run = (pipe_log[-1] if isinstance(pipe_log, list) and pipe_log else {})

    return {
        'as_of':    time.strftime('%Y-%m-%d %H:%M'),
        'market': {
            'regime':           state['layers']['latent']['regime'],
            'confidence':       round(state['confidence'], 4),
            'conf_label':       ('VERY_HIGH' if state['confidence']>=0.85
                                 else 'HIGH' if state['confidence']>=0.70
                                 else 'MODERATE' if state['confidence']>=0.55 else 'LOW'),
            'posture':          state['posture_r']['posture'],
            'exposure_pct':     round(state['posture_r']['exposure_pct'], 1),
            'dominant_layer':   state['arb']['winner'],
            'n_conflicts':      len(state['conflicts']),
            'escalation':       state['watch']['escalation_level'],
        },
        'layers': {
            k: {'health': round(v['health'], 3), 'state': v['state'], 'detail': v.get('detail','')}
            for k,v in state['layers'].items()
        },
        'macro': {
            'regime':    (macro or {}).get('macro_regime', 'UNKNOWN'),
            'cbe_rate':  (macro or {}).get('cbe_rate'),
            'inflation': (macro or {}).get('inflation_yoy'),
            'usd_egp':   (macro or {}).get('usd_egp'),
            'fx_trend':  (macro or {}).get('fx_trend'),
        },
        'active_alerts':   alerts,
        'n_alerts':        len(alerts),
        'opportunities':   state['opps'][:5],
        'avoid_zones':     [{'symbol': a['symbol'], 'rsi': a.get('rsi14')} for a in state['avoids'][:5]],
        'pipeline': {
            'last_run':      last_run.get('date', 'never'),
            'status':        last_run.get('status', 'UNKNOWN'),
            'duration_sec':  last_run.get('duration_sec'),
        },
        'confidence_trend': conf_trend,
        'regime_history':   regime_hist,
        'n_symbols':        len(data),
        'n_opportunities':  len(state['opps']),
        'n_avoids':         len(state['avoids']),
    }


def cmd_alert_scan(db, con, data, cur_ind, macro):
    """Standalone alert scanner with deduplication and severity ranking."""
    state    = _compute_all(db, data, cur_ind, macro)
    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(orch_log, list):
        orch_log = []

    alerts = scan_alerts(
        state['layers'], state['conflicts'], state['confidence'],
        state['posture_r'], macro, orch_log, db, con
    )

    # Count by severity
    severity_dist = {}
    for a in alerts:
        severity_dist[a['severity']] = severity_dist.get(a['severity'], 0) + 1

    # Check for already-sent today
    today = time.strftime('%Y-%m-%d')
    sent_today = []
    try:
        rows = db.execute(
            "SELECT alert_type, severity, message FROM alert_history WHERE alert_date=?",
            [today]
        ).fetchall()
        sent_today = [dict(r) for r in rows]
    except Exception:
        pass

    return {
        'date':          today,
        'n_new_alerts':  len(alerts),
        'n_sent_today':  len(sent_today),
        'all_clear':     len(alerts) == 0,
        'new_alerts':    alerts,
        'sent_today':    sent_today,
        'severity_dist': severity_dist,
        'critical_count': severity_dist.get('CRITICAL', 0),
        'posture':       state['posture_r']['posture'],
        'confidence':    round(state['confidence'], 4),
        'regime':        state['layers']['latent']['regime'],
    }


def cmd_archive_snapshot(db, con, data, cur_ind, macro):
    """Archive today's cognition snapshot to DB + JSON file."""
    state = _compute_all(db, data, cur_ind, macro)
    return _archive_snapshot(db, con, state, macro, data)


def _cmd_health_monitor_impl(db, data, cur_ind, macro):
    """Internal health implementation (reused by pipeline_run)."""
    today     = time.strftime('%Y-%m-%d')
    o         = orch()
    checks    = {}
    warnings  = []

    # ── DB connectivity ───────────────────────────────────────────────────────
    try:
        row = db.execute("SELECT COUNT(*) FROM ohlcv_history_execution").fetchone()
        n_ohlcv = row[0] if row else 0
        checks['db_connectivity'] = {'score': 1.0, 'n_ohlcv_rows': n_ohlcv}
    except Exception as e:
        checks['db_connectivity'] = {'score': 0.0, 'error': str(e)[:60]}
        warnings.append('DB connectivity issue')

    # ── Data freshness ────────────────────────────────────────────────────────
    try:
        row = db.execute("SELECT MAX(bar_time) FROM ohlcv_history_execution").fetchone()
        last_bar = row[0] if row else 0
        age_h = (time.time() - (last_bar or 0)) / 3600
        fresh_score = 1.0 if age_h < 24 else 0.7 if age_h < 72 else 0.3 if age_h < 168 else 0.0
        checks['data_freshness'] = {'score': round(fresh_score, 3), 'age_hours': round(age_h, 1)}
        if age_h > 72:
            warnings.append(f'OHLCV data {age_h:.0f}h old')
    except Exception:
        checks['data_freshness'] = {'score': 0.0, 'error': 'Could not read bar_time'}

    # ── Indicator coverage ────────────────────────────────────────────────────
    try:
        n_sym = len(data)
        n_ind = db.execute("SELECT COUNT(*) FROM indicators_cache").fetchone()[0]
        cov   = min(n_ind / max(n_sym, 1), 1.0)
        checks['indicator_coverage'] = {'score': round(cov, 3), 'symbols': n_sym, 'with_indicators': n_ind}
        if cov < 0.8:
            warnings.append(f'Indicator coverage low: {cov:.1%}')
    except Exception:
        checks['indicator_coverage'] = {'score': 0.5, 'error': 'Could not count indicators'}

    # ── Macro freshness ───────────────────────────────────────────────────────
    try:
        row = db.execute("SELECT fetched_at FROM macro_snapshot ORDER BY fetched_at DESC LIMIT 1").fetchone()
        if row and row[0]:
            ts = datetime.datetime.fromisoformat(str(row[0]))
            age_d = (datetime.datetime.utcnow() - ts).total_seconds() / 86400
            mac_score = 1.0 if age_d < 1 else 0.8 if age_d < 3 else 0.5 if age_d < 7 else 0.2
            checks['macro_freshness'] = {'score': round(mac_score, 3), 'age_days': round(age_d, 1)}
        else:
            checks['macro_freshness'] = {'score': 0.3, 'note': 'No macro timestamp'}
    except Exception:
        checks['macro_freshness'] = {'score': 0.3, 'note': 'No macro data found'}

    # ── Pipeline health ───────────────────────────────────────────────────────
    pipe_log = load_json_log(PIPELINE_LOG)
    if isinstance(pipe_log, list) and pipe_log:
        last_run = pipe_log[-1]
        days_ago = 999
        try:
            last_date = datetime.datetime.strptime(last_run.get('date',''), '%Y-%m-%d')
            days_ago = (datetime.datetime.utcnow() - last_date).days
        except Exception:
            pass
        pipe_score = 1.0 if days_ago == 0 else 0.7 if days_ago <= 1 else 0.4 if days_ago <= 3 else 0.1
        checks['pipeline_health'] = {
            'score': round(pipe_score, 3),
            'last_run': last_run.get('date', 'unknown'),
            'last_status': last_run.get('status', 'UNKNOWN'),
            'days_ago': days_ago,
        }
    else:
        checks['pipeline_health'] = {'score': 0.0, 'note': 'No pipeline runs recorded'}
        warnings.append('Pipeline has never been run')

    # ── Layer health (from last orchestration) ────────────────────────────────
    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(orch_log, list):
        orch_log = []
    if orch_log:
        last_orch = orch_log[-1]
        layer_h   = last_orch.get('layer_health', {})
        # layer_h values are either floats or dicts with 'health' key
        n_healthy = sum(1 for v in layer_h.values()
                        if (isinstance(v, dict) and v.get('health', 0) >= 0.6)
                        or (isinstance(v, (int, float)) and v >= 0.6))
        orch_score = n_healthy / max(len(layer_h), 1) if layer_h else 0.5
        checks['orchestration_health'] = {
            'score': round(orch_score, 3),
            'n_healthy_layers': n_healthy,
            'n_total_layers': len(layer_h),
            'last_orch_date': last_orch.get('date', 'unknown'),
        }
    else:
        checks['orchestration_health'] = {'score': 0.5, 'note': 'No orchestration log'}

    # ── Archive health ────────────────────────────────────────────────────────
    n_archives = len(list(ARCH_DIR.glob('*.json'))) if ARCH_DIR.exists() else 0
    checks['archive_health'] = {
        'score': 1.0 if n_archives > 0 else 0.0,
        'n_snapshots': n_archives,
        'archive_dir': str(ARCH_DIR),
    }

    # ── Overall ───────────────────────────────────────────────────────────────
    scores = [v['score'] for v in checks.values() if isinstance(v, dict) and 'score' in v]
    overall = round(statistics.mean(scores), 4) if scores else 0.0
    state   = 'HEALTHY' if overall >= 0.75 else 'DEGRADED' if overall >= 0.45 else 'CRITICAL'

    return {
        'overall_health': overall,
        'overall_state':  state,
        'n_symbols':      len(data),
        'n_with_indicators': len(cur_ind),
        'checks':         checks,
        'warnings':       warnings,
        'last_checked':   time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def cmd_health_monitor(db, data, cur_ind, macro):
    return _cmd_health_monitor_impl(db, data, cur_ind, macro)


def cmd_resilience_check(db, data, cur_ind, macro):
    """Test each layer independently and measure latency."""
    o = orch()
    results  = {}
    snaps    = o.latest_snapshot(data, cur_ind)

    layer_tests = {
        'latent':      lambda: o.layer_latent(snaps),
        'fields':      lambda: o.layer_fields(snaps),
        'propagation': lambda: o.layer_propagation(data),
        'energy':      lambda: o.layer_energy(snaps, data),
        'causality':   lambda: o.layer_causality(data),
        'decision':    lambda: o.layer_decision(snaps),
        'evolution':   lambda: o.layer_evolution(),
        'coupling':    lambda: o.layer_coupling(macro),
    }

    for lname, fn in layer_tests.items():
        try:
            t0  = time.time()
            res = fn()
            ms  = round((time.time() - t0) * 1000, 1)
            results[lname] = {
                'status':     'OPERATIONAL',
                'health':     round(res.get('health', 0), 4),
                'state':      res.get('state', 'UNKNOWN'),
                'latency_ms': ms,
                'detail':     res.get('detail', '')[:60],
            }
        except Exception as e:
            results[lname] = {
                'status': 'FAILED',
                'error':  str(e)[:80],
            }

    n_op     = sum(1 for r in results.values() if r['status'] == 'OPERATIONAL')
    n_fail   = len(results) - n_op
    res_score = round(n_op / len(results), 3)
    latencies = [r['latency_ms'] for r in results.values() if 'latency_ms' in r]
    avg_ms    = round(statistics.mean(latencies), 1) if latencies else 0

    return {
        'n_layers':       len(results),
        'n_operational':  n_op,
        'n_failed':       n_fail,
        'resilience_score': res_score,
        'overall_state':  ('RESILIENT' if res_score == 1.0
                           else 'DEGRADED' if res_score >= 0.75 else 'CRITICAL'),
        'avg_latency_ms': avg_ms,
        'p95_latency_ms': round(sorted(latencies)[int(len(latencies)*0.95)], 1) if len(latencies) >= 3 else avg_ms,
        'layers':         results,
        'recovery_notes': [f"Layer '{k}' failed: {v['error']}" for k,v in results.items() if v['status']=='FAILED'],
    }


def cmd_observability(db):
    """Execution metrics, step timings, confidence trends, DB metrics."""
    pipe_log = load_json_log(PIPELINE_LOG)
    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(pipe_log, list):
        pipe_log = []
    if not isinstance(orch_log, list):
        orch_log = []

    # Step timing analysis (last 30 pipeline runs)
    step_times = {}
    step_fails = {}
    for run in pipe_log[-30:]:
        if not isinstance(run, dict):
            continue
        for step in run.get('steps', []):
            sname = step.get('step', 'unknown')
            dur   = step.get('duration_sec') or 0
            step_times.setdefault(sname, []).append(dur)
            if step.get('status') == 'FAILED':
                step_fails[sname] = step_fails.get(sname, 0) + 1

    timing_stats = {}
    for sname, times in step_times.items():
        if not times:
            continue
        ts = sorted(times)
        n  = len(ts)
        timing_stats[sname] = {
            'p50_sec':  round(ts[n//2], 3),
            'p95_sec':  round(ts[min(int(n*0.95), n-1)], 3),
            'mean_sec': round(statistics.mean(ts), 3),
            'n_runs':   n,
            'n_failed': step_fails.get(sname, 0),
            'fail_rate': round(step_fails.get(sname, 0) / max(n, 1), 3),
        }

    # Confidence trend (last 30 orch log entries)
    conf_trend = [
        {'date': e.get('date',''), 'confidence': round(e.get('global_confidence',0),3),
         'regime': e.get('regime',''), 'posture': e.get('posture','')}
        for e in orch_log[-30:]
    ]

    # Pipeline success metrics
    n_runs = len(pipe_log)
    n_ok   = sum(1 for r in pipe_log if isinstance(r,dict) and r.get('status')=='OK')
    n_partial = sum(1 for r in pipe_log if isinstance(r,dict) and r.get('status')=='PARTIAL')
    durations = [r.get('duration_sec',0) for r in pipe_log[-30:] if isinstance(r,dict) and r.get('duration_sec')]

    # DB metrics
    db_metrics = {}
    try:
        db_metrics = {
            'ohlcv_rows':      db.execute("SELECT COUNT(*) FROM ohlcv_history_execution").fetchone()[0],
            'n_symbols':       db.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv_history_execution").fetchone()[0],
            'last_bar_epoch':  db.execute("SELECT MAX(bar_time) FROM ohlcv_history_execution").fetchone()[0],
            'indicator_rows':  db.execute("SELECT COUNT(*) FROM indicators_cache").fetchone()[0],
            'pipeline_runs_db': db.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0],
            'cognition_snapshots': db.execute("SELECT COUNT(*) FROM cognition_snapshots").fetchone()[0],
            'alerts_sent':     db.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0],
        }
    except Exception:
        pass

    # Archive metrics
    n_archives = len(list(ARCH_DIR.glob('*.json'))) if ARCH_DIR.exists() else 0

    return {
        'pipeline_metrics': {
            'total_runs':    n_runs,
            'success_rate':  round(n_ok  / max(n_runs, 1), 3),
            'partial_rate':  round(n_partial / max(n_runs, 1), 3),
            'avg_duration_sec': round(statistics.mean(durations), 2) if durations else None,
            'p95_duration_sec': round(sorted(durations)[int(len(durations)*0.95)], 2) if len(durations)>=3 else None,
            'last_run':      pipe_log[-1].get('date') if pipe_log else None,
            'last_status':   pipe_log[-1].get('status') if pipe_log else None,
        },
        'step_timings':   timing_stats,
        'confidence_trend': conf_trend[-14:],
        'db_metrics':     db_metrics,
        'archive_metrics': {
            'n_snapshots': n_archives,
            'archive_dir': str(ARCH_DIR),
        },
        'log_sizes': {
            'pipeline_log':    len(pipe_log),
            'orchestrator_log': len(orch_log),
            'evolution_log':   len(load_json_log(EVO_LOG) if isinstance(load_json_log(EVO_LOG), list)
                                   else load_json_log(EVO_LOG).get('entries', [])),
            'coupling_log':    len(load_json_log(COUP_LOG) if isinstance(load_json_log(COUP_LOG), list) else []),
        },
    }


def cmd_historical_replay(db):
    """Timeline of past cognition snapshots from DB + JSON archive."""
    # Load from DB
    db_snaps = []
    try:
        rows = db.execute("""
            SELECT snapshot_date, regime, confidence, posture, exposure_pct,
                   n_conflicts, layer_health_json, macro_regime
            FROM cognition_snapshots ORDER BY snapshot_date DESC LIMIT 60
        """).fetchall()
        for row in rows:
            r = dict(row)
            if r.get('layer_health_json'):
                try:
                    r['layer_health'] = json.loads(r['layer_health_json'])
                except Exception:
                    pass
            db_snaps.append(r)
    except Exception:
        pass

    # Load from JSON archive
    json_snaps = []
    if ARCH_DIR.exists():
        for f in sorted(ARCH_DIR.glob('*.json'), reverse=True)[:60]:
            try:
                snap = json.loads(f.read_text())
                json_snaps.append(snap)
            except Exception:
                pass

    # Merge by date (DB wins over JSON)
    merged = {}
    for s in json_snaps:
        d = s.get('snapshot_date') or s.get('date', '')
        merged[d] = s
    for s in db_snaps:
        d = s.get('snapshot_date', '')
        merged[d] = s

    timeline = sorted(merged.values(),
                      key=lambda x: x.get('snapshot_date') or x.get('date', ''),
                      reverse=True)

    # Evolution analysis
    regimes  = [s.get('regime','') for s in timeline if s.get('regime')]
    confs    = [float(s.get('confidence',0)) for s in timeline if s.get('confidence')]
    postures = [s.get('posture','') for s in timeline if s.get('posture')]
    transitions = sum(1 for i in range(1, len(regimes)) if regimes[i] != regimes[i-1])

    return {
        'n_snapshots':  len(timeline),
        'date_range': {
            'earliest': (timeline[-1].get('snapshot_date') or timeline[-1].get('date')) if timeline else None,
            'latest':   (timeline[0].get('snapshot_date') or timeline[0].get('date')) if timeline else None,
        },
        'evolution': {
            'regime_transitions':    transitions,
            'current_regime':        regimes[0] if regimes else 'UNKNOWN',
            'regime_distribution':   {r: regimes.count(r) for r in set(regimes)},
            'avg_confidence':        round(statistics.mean(confs), 4) if confs else 0,
            'confidence_std':        round(statistics.stdev(confs), 4) if len(confs) > 1 else 0,
            'min_confidence':        round(min(confs), 4) if confs else 0,
            'max_confidence':        round(max(confs), 4) if confs else 0,
            'posture_distribution':  {p: postures.count(p) for p in set(postures)},
        },
        'timeline': [
            {
                'date':       s.get('snapshot_date') or s.get('date', ''),
                'regime':     s.get('regime', ''),
                'confidence': round(float(s.get('confidence', 0)), 3),
                'posture':    s.get('posture', ''),
                'exposure':   s.get('exposure_pct', 0),
                'n_conflicts': s.get('n_conflicts', 0),
                'macro_regime': s.get('macro_regime', ''),
            }
            for s in timeline[:30]
        ],
    }


def cmd_os_full(db, con, data, cur_ind, macro):
    """Full OS status: dashboard + alerts + health + resilience + observability."""
    # Compute once, share across
    state    = _compute_all(db, data, cur_ind, macro)
    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(orch_log, list):
        orch_log = []

    # Alert scan
    alerts = scan_alerts(
        state['layers'], state['conflicts'], state['confidence'],
        state['posture_r'], macro, orch_log, db, con
    )

    # Health
    health = _cmd_health_monitor_impl(db, data, cur_ind, macro)

    # Observability
    observe = cmd_observability(db)

    # Historical replay
    replay = cmd_historical_replay(db)

    # Pipeline status
    pipe_status = cmd_pipeline_status(db)

    return {
        'as_of': time.strftime('%Y-%m-%d %H:%M'),
        'market_summary': {
            'regime':           state['layers']['latent']['regime'],
            'confidence':       round(state['confidence'], 4),
            'posture':          state['posture_r']['posture'],
            'exposure_pct':     round(state['posture_r']['exposure_pct'], 1),
            'dominant_layer':   state['arb']['winner'],
            'n_conflicts':      len(state['conflicts']),
            'escalation':       state['watch']['escalation_level'],
            'macro_regime':     (macro or {}).get('macro_regime', 'UNKNOWN'),
            'trust':            state['layers']['evolution'].get('trust', 'UNKNOWN'),
        },
        'layers': {
            k: {'health': round(v['health'],3), 'state': v['state']}
            for k,v in state['layers'].items()
        },
        'active_alerts':  alerts,
        'n_alerts':       len(alerts),
        'opportunities':  state['opps'][:5],
        'health':         health,
        'pipeline':       pipe_status,
        'observability':  {
            'pipeline_metrics':  observe['pipeline_metrics'],
            'confidence_trend':  observe['confidence_trend'][-7:],
            'db_metrics':        observe['db_metrics'],
        },
        'history': {
            'n_snapshots':        replay['n_snapshots'],
            'regime_transitions': replay['evolution']['regime_transitions'],
            'avg_confidence':     replay['evolution']['avg_confidence'],
            'recent':             replay['timeline'][:7],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'dashboard'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': sorted(COMMANDS)}))
        sys.exit(1)

    try:
        json.loads(sys.stdin.read() or '{}')
    except Exception:
        pass

    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    con.row_factory = _sq.Row
    db  = con.cursor()
    ensure_tables(con)

    try:
        if cmd == 'pipeline_run':
            result = cmd_pipeline_run(db, con)
        elif cmd == 'pipeline_status':
            result = cmd_pipeline_status(db)
        elif cmd == 'observability':
            result = cmd_observability(db)
        elif cmd == 'historical_replay':
            result = cmd_historical_replay(db)
        else:
            o = orch()
            if o is None:
                raise RuntimeError('cognitive_orchestrator module failed to load')
            data, cur_ind = o.load_ohlcv(db)
            o.enrich(data)
            macro = o.load_macro(db)

            dispatch = {
                'dashboard':        lambda: cmd_dashboard(db, data, cur_ind, macro),
                'alert_scan':       lambda: cmd_alert_scan(db, con, data, cur_ind, macro),
                'archive_snapshot': lambda: cmd_archive_snapshot(db, con, data, cur_ind, macro),
                'health_monitor':   lambda: cmd_health_monitor(db, data, cur_ind, macro),
                'resilience_check': lambda: cmd_resilience_check(db, data, cur_ind, macro),
                'os_full':          lambda: cmd_os_full(db, con, data, cur_ind, macro),
            }
            result = dispatch[cmd]()

        print(json.dumps(result, default=str))
    except Exception as e:
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()}))
    finally:
        con.close()


if __name__ == '__main__':
    main()
