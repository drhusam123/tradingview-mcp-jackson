#!/usr/bin/env python3
"""
Night Lab — Autonomous Overnight Learning Coordinator
Runs every night after market close, builds on all previous knowledge.
Sequences: per_stock_learner → cycle_hunter → cross_stock_brain → evolution_engine → signal_integration
"""
import os, sys, json, sqlite3, datetime, time, subprocess
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db')
SCRIPTS_DIR = str(Path(__file__).parent)

# ── Ph74 DuckDB Analytics Layer (optional) ────────────────────────────────────
try:
    _NL_DIR = Path(__file__).parent
    if str(_NL_DIR) not in sys.path:
        sys.path.insert(0, str(_NL_DIR))
    from duckdb_layer import export_parquet_snapshot as _export_parquet
    _DUCKDB_LAYER_NL = True
except ImportError:
    _DUCKDB_LAYER_NL = False
    _export_parquet  = None

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS night_lab_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT,
        command TEXT,
        per_stock_status TEXT,
        cycle_hunter_status TEXT,
        cross_stock_status TEXT,
        evolution_status TEXT,
        signal_integration_status TEXT,
        total_duration_seconds REAL,
        summary TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()

# ── OHLCV Data Validator ──────────────────────────────────────────────────────

def validate_ohlcv_latest_bar(conn, max_dup_pct=0.5, verbose=True):
    """
    Detects index-data contamination: if ≥ max_dup_pct of symbols share
    the same close price on the latest bar, that day is corrupt — delete it.

    Returns: dict with {date, n_total, n_dup, dup_pct, action}
    """
    try:
        latest = conn.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history"
        ).fetchone()[0]
        if not latest:
            return {'action': 'no_data'}

        n_total = conn.execute(
            "SELECT COUNT(*) FROM ohlcv_history WHERE date(bar_time,'unixepoch')=?",
            (latest,)
        ).fetchone()[0]

        if n_total < 5:
            return {'date': latest, 'n_total': n_total, 'action': 'too_few_bars'}

        # Most common close price on this day
        top = conn.execute("""
            SELECT close, COUNT(*) n
            FROM ohlcv_history WHERE date(bar_time,'unixepoch')=?
            GROUP BY close ORDER BY n DESC LIMIT 1
        """, (latest,)).fetchone()

        if not top:
            return {'date': latest, 'action': 'no_top_close'}

        n_dup  = top['n']
        dup_cl = top['close']
        dup_pct = n_dup / n_total

        result = {'date': latest, 'n_total': n_total, 'n_dup': n_dup,
                  'dup_close': dup_cl, 'dup_pct': round(dup_pct, 3)}

        if dup_pct >= max_dup_pct:
            # High duplication → index contamination — delete the day
            deleted = conn.execute(
                "DELETE FROM ohlcv_history WHERE date(bar_time,'unixepoch')=?",
                (latest,)
            ).rowcount
            conn.commit()
            result['action'] = 'deleted_corrupt_day'
            result['deleted'] = deleted
            if verbose:
                print(json.dumps({
                    'ohlcv_validator': 'CORRUPT_DAY_DELETED',
                    'date': latest,
                    'dup_close': dup_cl,
                    'n_deleted': deleted,
                    'dup_pct': round(dup_pct * 100, 1),
                }))
        else:
            result['action'] = 'ok'
            if verbose:
                print(json.dumps({
                    'ohlcv_validator': 'ok',
                    'date': latest,
                    'n_bars': n_total,
                    'top_close_pct': round(dup_pct * 100, 1),
                }))

        return result
    except Exception as e:
        return {'action': f'error:{e}'}


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run_script(script_name, *args, timeout=7200):
    """
    Run a Python script via subprocess. Returns (returncode, stdout, stderr, duration).
    Using subprocess avoids memory leaks from running everything in-process.
    """
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    cmd = [sys.executable, script_path] + list(args)
    t0 = time.time()
    print(json.dumps({"step": script_name, "status": "start", "cmd": " ".join(cmd)}))
    sys.stdout.flush()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(__file__).parent.parent.parent)
        )
        duration = time.time() - t0
        # Echo subprocess output line by line
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                print(line)
        if result.stderr:
            for line in result.stderr.splitlines()[-20:]:  # last 20 error lines
                line = line.strip()
                if line:
                    print(json.dumps({"stderr": line, "script": script_name}))
        sys.stdout.flush()
        status = "ok" if result.returncode == 0 else f"error:{result.returncode}"
        print(json.dumps({"step": script_name, "status": status, "duration_seconds": round(duration, 1)}))
        sys.stdout.flush()
        return result.returncode, result.stdout, result.stderr, duration
    except subprocess.TimeoutExpired:
        duration = time.time() - t0
        print(json.dumps({"step": script_name, "status": "timeout", "duration_seconds": round(duration, 1)}))
        sys.stdout.flush()
        return -1, "", "timeout", duration
    except Exception as e:
        duration = time.time() - t0
        print(json.dumps({"step": script_name, "status": f"exception:{e}", "duration_seconds": round(duration, 1)}))
        sys.stdout.flush()
        return -2, "", str(e), duration

def get_script_status(returncode):
    if returncode == 0:
        return "ok"
    if returncode == -1:
        return "timeout"
    return f"failed:{returncode}"

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_run(quick=False):
    t0 = time.time()
    today_str = datetime.date.today().isoformat()
    command = "quick" if quick else "run"

    conn = get_db()
    ensure_tables(conn)

    # Check if already run in last 12 hours
    row = conn.execute(
        "SELECT id, created_at FROM night_lab_runs WHERE run_date=? AND command=? ORDER BY id DESC LIMIT 1",
        (today_str, command)
    ).fetchone()
    if row:
        created_at = row['created_at']
        try:
            run_time = datetime.datetime.fromisoformat(created_at)
            now = datetime.datetime.utcnow()
            hours_ago = (now - run_time).total_seconds() / 3600
            if hours_ago < 12:
                print(json.dumps({
                    "status": "already_run",
                    "date": today_str,
                    "last_run": created_at,
                    "hours_ago": round(hours_ago, 1),
                }))
                conn.close()
                return
        except Exception:
            pass  # malformed timestamp — proceed with run

    conn.close()

    print(json.dumps({
        "status": "night_lab_start",
        "date": today_str,
        "command": command,
        "quick": quick,
    }))
    sys.stdout.flush()

    step_results = {}

    # ── Step 0: OHLCV Data Validator ─────────────────────────────────────────
    # Detects index-contamination (all symbols same price) before training runs.
    try:
        _vconn = get_db()
        _vresult = validate_ohlcv_latest_bar(_vconn, max_dup_pct=0.5, verbose=True)
        _vconn.close()
        step_results['ohlcv_validator'] = _vresult
        if _vresult.get('action') == 'deleted_corrupt_day':
            print(f"[night_lab] ⚠️  Corrupt OHLCV day {_vresult['date']} deleted "
                  f"({_vresult['deleted']} bars — {_vresult['dup_pct']*100:.0f}% shared close {_vresult['dup_close']})",
                  flush=True)
    except Exception as _ve:
        step_results['ohlcv_validator'] = {'action': f'error:{_ve}'}

    # ── Step 1: Per-stock learner ─────────────────────────────────────────────
    rc, _, _, dur = run_script("per_stock_learner.py", "run", timeout=5400)
    step_results['per_stock'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 2: Cycle hunter ──────────────────────────────────────────────────
    rc, _, _, dur = run_script("cycle_hunter.py", "run", timeout=3600)
    step_results['cycle_hunter'] = {'status': get_script_status(rc), 'duration': dur}

    if not quick:
        # ── Step 3: Cross-stock brain ─────────────────────────────────────────
        rc, _, _, dur = run_script("cross_stock_brain.py", "run", timeout=7200)
        step_results['cross_stock'] = {'status': get_script_status(rc), 'duration': dur}

        # ── Step 4: Evolution engine ──────────────────────────────────────────
        # Use subprocess to prevent memory leaks from ML model loading
        rc, _, _, dur = run_script("evolution_engine.py", "evolution_full", timeout=5400)
        step_results['evolution'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 5: Signal integration score_all ─────────────────────────────────
    rc, _, _, dur = run_script("signal_integration.py", "score_all", timeout=1800)
    step_results['signal_integration'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 6: Cognitive orchestrator — posture بعد التعلم الليلي ──────────
    rc, _, _, dur = run_script("cognitive_orchestrator.py", "orchestrate_full", timeout=600)
    step_results['orchestrator'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 6a: Ph 32 — Recommendation Outcome Tracker (fast ~10ms) ─────────
    # يملأ return_t1/t3/t5/t10 للإشارات التي مضى عليها ≥5 أيام
    rc, _, _, dur = run_script("signal_integration.py", "track_outcomes", timeout=60)
    step_results['track_outcomes'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 6b: Ph 33 — Model Drift Monitor (fast ~5ms) ──────────────────────
    # يفحص Rolling WR + ML calibration — ينذر مبكراً عند تدهور النموذج
    rc, out, _, dur = run_script(
        "signal_integration.py", "model_drift",
        '{"window_days":30,"min_filled":10,"alert_threshold_wr":45.0}',
        timeout=30
    )
    try:
        _drift = json.loads(out.strip()) if out else {}
        if _drift.get('drift_detected'):
            print(f"[night_lab] ⚠️  DRIFT DETECTED: {_drift.get('drift_reason')}", flush=True)
        elif _drift.get('n_filled', 0) >= 10:
            print(f"[night_lab] ✅ Model OK: WR={_drift.get('win_rate')}% | gated={_drift.get('gated_win_rate')}%", flush=True)
    except Exception:
        pass
    step_results['model_drift'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 7 (Deep only): ML Trainer — Phase 1 Feature Engineering ─────────
    # Phase 1 فقط: يُحدّث feature_store بـ 60+ مميز (~5 دقائق)
    # الأوزان الكاملة تُدرَّب في cron الأسبوعي (الأحد فجراً)
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase1", timeout=600)
        step_results['ml_feature_refresh'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 7g (Deep): Phase 29 — Pine Analytics via TradingView MCP ─────────
    # يجلب RS percentile + VWAP bias لـ 252 سهم (~9 دقائق، بطيء جداً)
    # يُشغَّل في deep mode فقط (الأحد الأسبوعي) — ليس في quick
    if not quick:
        import subprocess as _sp2, sys as _sys2, os as _os2
        _node_candidates2 = ['/usr/local/bin/node', '/usr/bin/node', 'node']
        _node2 = next((n for n in _node_candidates2 if _os2.path.exists(n)), 'node')
        _t0_pine = time.time()
        try:
            _r2 = _sp2.run([_node2, 'scripts/fetch_pine_analytics.mjs', 'all'],
                           capture_output=True, text=True, timeout=900,
                           cwd=str(Path(__file__).parent.parent.parent))
            step_results['pine_analytics'] = {
                'status': 'ok' if _r2.returncode == 0 else f'error:{_r2.returncode}',
                'duration': round(time.time() - _t0_pine, 1)
            }
        except subprocess.TimeoutExpired:
            step_results['pine_analytics'] = {'status': 'timeout', 'duration': 900}
        except Exception as _e2:
            step_results['pine_analytics'] = {'status': f'exception:{_e2}', 'duration': round(time.time()-_t0_pine,1)}

    # ── Step 7b: Phase 21 — Spectral Cycle Intelligence (~5 ثواني) ───────────
    # يجب أن يُشغَّل بعد Phase 1 وقبل predict_ensemble لأن الـ UES يستخدم spectral_boost
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase21", timeout=120)
    step_results['spectral_intelligence'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 7c: Phase 24 — Spectral Pine Overlay (generate file, no TV needed) ──
    # يولّد Pine Script يُعرض cycle_bottom_prox + regime colors على TradingView
    import subprocess as _sp, sys as _sys, os as _os
    _node_bin = _sys.executable.replace('python', 'node').replace('python3', 'node')
    _node_candidates = ['/usr/local/bin/node', '/usr/bin/node', 'node']
    _node = next((n for n in _node_candidates if _os.path.exists(n)), 'node')
    _t0c = time.time()
    try:
        _r = _sp.run([_node, 'scripts/load_spectral_indicator.mjs', '--save-only'],
                     capture_output=True, text=True, timeout=30,
                     cwd=str(Path(__file__).parent.parent.parent))
        step_results['spectral_pine_overlay'] = {
            'status': 'ok' if _r.returncode == 0 else f'error:{_r.returncode}',
            'duration': round(time.time() - _t0c, 1)
        }
    except Exception as _e:
        step_results['spectral_pine_overlay'] = {'status': f'exception:{_e}', 'duration': round(time.time()-_t0c,1)}

    # ── Step 7d: Phase 22 — Shadow Validator: fill deferred outcomes (~1ث) ──
    rc, _, _, dur = run_script("signal_integration.py", "shadow_fill_outcomes", timeout=60)
    step_results['shadow_fill_outcomes'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 7d2: Ph 32 — Recommendation Outcome Tracker (~10ms) ─────────────
    # يملأ return_t1/t3/t5 تدريجياً كلما أصبحت البيانات متاحة
    # يُشغَّل كل ليلة — يدعم Ph46 Bayesian WR بأسرع ما يمكن
    rc, out, _, dur = run_script("signal_integration.py", "track_outcomes", timeout=30)
    step_results['track_outcomes'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        _to = json.loads(out.strip().splitlines()[-1])
        if _to.get('outcomes_filled', 0) > 0:
            print(f"[night_lab] 📊 Ph32 Track Outcomes: filled={_to['outcomes_filled']}", flush=True)
    except Exception:
        pass

    # ── Step 7f: Phase 26 — Spectral Alpha Dashboard (تفعّل تلقائياً) ────────
    # صامت حتى ≥10 observations — يتفعّل بعد 26 مايو تلقائياً
    rc, _, _, dur = run_script("signal_integration.py", "spectral_alpha_dashboard", timeout=60)
    step_results['spectral_alpha_dashboard'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 7e: Phase 23 — Spectral Attribution Backtest (~15ث) ─────────────
    # يُشغَّل كل ليلة لاستيعاب أحداث الانفجار الجديدة
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase23", timeout=120)
    step_results['spectral_attribution'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 8: Ensemble Predict + Calibration (كل ليلة) ─────────────────────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "predict_ensemble", timeout=300)
    step_results['ensemble_predict'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 9 (Deep): Incremental Online Learning — 30 trees ~12s ───────────
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase12", timeout=120)
        step_results['incremental_update'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 10 (Deep): MTF Features — ينبّه signals بـ weekly trend ─────────
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase14", timeout=120)
        step_results['mtf_features'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 11 (Deep): Pine Analytics Fusion — VWAP/RS/VP ───────────────────
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase11", timeout=120)
        step_results['pine_fusion'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 12: Conformal Prediction Intervals — uncertainty calibration ─────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase15", timeout=120)
    step_results['conformal_intervals'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 13 (Deep): Feature Drift Monitor — PSI + adversarial ────────────
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase16", timeout=300)
        step_results['drift_monitor'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 14 (Deep): Return Regressor — EV per signal ─────────────────────
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase17", timeout=300)
        step_results['return_regressor'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 15: Cox PH Survival — hazard score per stock ────────────────────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase18", timeout=120)
    step_results['survival_analysis'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 16: Kelly Optimizer — position sizing per signal ─────────────────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase19", timeout=60)
    step_results['kelly_optimizer'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 17: Pine ML Indicator — auto-generate Pine Script dashboard ──────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase20", timeout=60)
    step_results['pine_ml_indicator'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 18 (Deep): Phase 25 — Spectral Reliability Memory ───────────────
    # يحسب per-symbol alpha للطبقة الطيفية — يُشغَّل فقط في الـ deep mode (الأحد)
    # يحتاج بيانات من spectral_shadow_log (تتراكم يوميًا)
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase25", timeout=120)
        step_results['spectral_reliability'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 19 (Deep): Ph46 — Bayesian Win Rate (Beta-Binomial posterior) ────
    # يُحدَّث كل ليلة في deep mode لاستيعاب نتائج التوصيات الجديدة
    # يعطي credible intervals حتى مع <50 observation
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase46", timeout=60)
        step_results['bayesian_wr'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 19b: Ph52+53 — Enhanced Breadth + Sector Rotation (~7s) ──────────
    # يجب أن يسبق Ph56 وPh51 وPh55 — يُنتج market_breadth_enhanced
    rc, out52, _, dur = run_script("egx_ml_trainer.py", "phase52", timeout=120)
    step_results['enhanced_breadth'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out52.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"breadth_rows_written"' in line:
                eb = json.loads(line)
                rot = eb.get('latest_rotation', {})
                print(f"[night_lab] 🔄 Ph52+53 Breadth rows={eb.get('breadth_rows_written','?')} "
                      f"Lead={rot.get('leading','?')}", flush=True)
                break
    except Exception:
        pass

    # ── Step 19c: Ph56 — Markov Regime Engine (~5-15s) ───────────────────────
    # يجب أن يسبق Ph51 وPh55 — يُنتج markov_signal_daily (يقرأ market_breadth_enhanced)
    rc, out56, _, dur = run_script("egx_ml_trainer.py", "phase56", timeout=120)
    step_results['markov_regime'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out56.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"latest_state"' in line:
                mk56 = json.loads(line)
                lat_state  = mk56.get('latest_state', '?')
                lat_sig    = mk56.get('latest_signal_1d', 0) or 0
                lat_age    = mk56.get('latest_regime_age', '?')
                lat_ent    = mk56.get('latest_entropy', 0) or 0
                wf_acc     = mk56.get('wf_accuracy', 0) or 0
                hmm_ok     = '✓' if mk56.get('hmm_enabled') else '✗'
                sc         = mk56.get('state_counts', {})
                print(f"[night_lab] 🔄 Ph56 Markov: state={lat_state} "
                      f"signal={lat_sig:+.3f} age={lat_age}d "
                      f"H={lat_ent:.2f}bits wf_acc={wf_acc:.1%} HMM={hmm_ok} "
                      f"(BULL={sc.get('BULL','?')} SIDE={sc.get('SIDE','?')} "
                      f"BEAR={sc.get('BEAR','?')})", flush=True)
                break
    except Exception:
        pass

    # ── Step 19d0: Ph57 — Closing Pressure Signal (~30s) ────────────────────────
    # يحسب close_pos, vol_surge, closing_pressure من OHLCV اليومي — يُستخدَم في Ph55.
    # يُشغَّل قبل Ph51/Ph55 لأنهما يقرآن closing_pressure_daily.
    rc, out57, _, dur57 = run_script("egx_ml_trainer.py", "phase57", timeout=120)
    step_results['closing_pressure'] = {'status': get_script_status(rc), 'duration': round(dur57, 1)}
    try:
        for line in reversed(out57.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"rows_written"' in line:
                cp57 = json.loads(line)
                top_gap = [g['symbol'] for g in cp57.get('top_gap_stocks', [])[:5]]
                print(f"[night_lab] 🕯️  Ph57 Closing Pressure: "
                      f"{cp57.get('rows_written','?')} rows, "
                      f"top gap: {', '.join(top_gap)}", flush=True)
                break
    except Exception:
        pass

    # ── Step 19d: Ph50 — Adaptive Gate Calibration (~0.1ث) ───────────────────
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase50", timeout=30)
    step_results['adaptive_gate'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 19e: Ph51 — Tomorrow Direction Forecast (~60-120s) ──────────────
    # يقرأ ميزات Markov من markov_signal_daily (يجب أن يسبقه Ph56)
    rc, out51, _, dur = run_script("egx_ml_trainer.py", "phase51", timeout=180)
    step_results['tomorrow_forecast'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out51.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"direction"' in line:
                fc = json.loads(line)
                dir_ = fc.get('direction', '?')
                pu   = fc.get('p_up', 0)
                pd_  = fc.get('p_down', 0)
                print(f"[night_lab] 📅 Ph51 Tomorrow: {dir_} ↑{pu:.0%} ↓{pd_:.0%}", flush=True)
                break
    except Exception:
        pass

    # ── Step 19f: Ph54 — Forecast Accuracy Tracker (~2s) ─────────────────────
    rc, out54, _, dur = run_script("egx_ml_trainer.py", "phase54", timeout=60)
    step_results['forecast_accuracy'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out54.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"n_outcomes"' in line:
                fa = json.loads(line)
                acc = fa.get('acc_30d_pct') or fa.get('acc_all_pct')
                n   = fa.get('n_30d') or fa.get('n_outcomes', 0)
                if fa.get('n_outcomes', 0) > 0:
                    print(f"[night_lab] 📊 Ph54 Forecast acc: {acc}% ({n} obs)", flush=True)
                break
    except Exception:
        pass

    # ── Step 19g: Ph55 — Per-Stock Tomorrow Direction Forecast (~60-90s) ──────
    # يقرأ ميزات Markov من markov_signal_daily (يجب أن يسبقه Ph56)
    rc, out55, _, dur = run_script("egx_ml_trainer.py", "phase55", timeout=240)
    step_results['stock_forecast'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out55.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"dir_counts"' in line:
                sf55 = json.loads(line)
                dc   = sf55.get('dir_counts', {})
                top  = sf55.get('top_up_stocks', [])[:5]
                print(f"[night_lab] 📈 Ph55 Stock Forecast: "
                      f"UP={dc.get('UP','?')} FLAT={dc.get('FLAT','?')} DOWN={dc.get('DOWN','?')} "
                      f"| Top UP: {', '.join(top)}", flush=True)
                break
    except Exception:
        pass

    # ── Step 19h (Deep): Ph77 — tsfresh Daily Store (~1s) ────────────────────────
    # كل ليلة: يحسب 10 مميزات إحصائية لكل 253 رمز ويخزنها في tsfresh_daily.
    # بعد 30 يوم، يُصبح Ph55 يستخدم هذه المميزات تلقائياً في التدريب.
    # الاستخراج الثقيل (extract_explosions) يُشغَّل أسبوعياً في weekly_deep.
    if not quick:
        try:
            rc77n, out77n, _, dur77n = run_script(
                "tsfresh_features.py",
                "daily_store",
                '{"lookback":20}',
                timeout=60,
            )
            _ph77n_status = get_script_status(rc77n)
            _ph77n_info = {}
            try:
                for line in reversed(out77n.splitlines()):
                    line = line.strip()
                    if line.startswith('{') and '"n_stored"' in line:
                        _ph77n_info = json.loads(line)
                        break
            except Exception:
                pass
            step_results['tsfresh_daily'] = {
                'status':    _ph77n_status,
                'duration':  round(dur77n, 1),
                'n_stored':  _ph77n_info.get('n_stored'),
                'n_skipped': _ph77n_info.get('n_skipped'),
                'trade_date':_ph77n_info.get('trade_date'),
            }
            print(f"[night_lab] 🔬 Ph77 tsfresh_daily: {_ph77n_status} "
                  f"stored={_ph77n_info.get('n_stored','?')} "
                  f"skipped={_ph77n_info.get('n_skipped','?')} ({dur77n:.1f}s)", flush=True)
        except Exception as e77n:
            step_results['tsfresh_daily'] = {'status': f'exception:{e77n}', 'duration': 0}
            print(f"[night_lab] Ph77: skipped ({e77n})", flush=True)

    # ── Step 20: Ph47 — QMC Portfolio Risk (Sobol sequences) ─────────────────
    # يُشغَّل كل ليلة — تقدير VaR/CVaR بدقة أعلى من MC العادي
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase47", timeout=120)
    step_results['qmc_portfolio_risk'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 21 (Deep): Ph48 — Antithetic Variates Backtest ──────────────────
    # يُخفض تباين تقديرات Sharpe/WR — مفيد خاصةً مع samples صغيرة
    if not quick:
        rc, _, _, dur = run_script("egx_ml_trainer.py", "phase48", timeout=120)
        step_results['antithetic_backtest'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 22: Ph49 — LHS Parameter Sensitivity (~1s) ──────────────────────
    # يكشف أكثر البارامترات حساسيةً ويقيس متانة النظام (robustness score)
    rc, out, _, dur = run_script("egx_ml_trainer.py", "phase49", timeout=60)
    step_results['lhs_sensitivity'] = {'status': get_script_status(rc), 'duration': dur}
    try:
        for line in reversed(out.splitlines()):
            parsed = json.loads(line)
            if parsed.get('phase') == '49':
                top = parsed.get('most_sensitive_param', '?')
                rob = parsed.get('robustness_score', '?')
                print(f"[night_lab] 🔬 Ph49 LHS: most_sensitive={top} robustness={rob}", flush=True)
                break
    except Exception:
        pass

    total_duration = time.time() - t0

    summary = {
        "date": today_str,
        "command": command,
        "steps": step_results,
        "total_duration_seconds": round(total_duration, 1),
        "all_ok": all(v['status'] == 'ok' for v in step_results.values()),
    }

    # Save to DB
    conn = get_db()
    conn.execute("""
        INSERT INTO night_lab_runs
        (run_date, command, per_stock_status, cycle_hunter_status, cross_stock_status,
         evolution_status, signal_integration_status, total_duration_seconds, summary)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        today_str,
        command,
        step_results.get('per_stock', {}).get('status', 'skipped'),
        step_results.get('cycle_hunter', {}).get('status', 'skipped'),
        step_results.get('cross_stock', {}).get('status', 'skipped'),
        step_results.get('evolution', {}).get('status', 'skipped'),
        step_results.get('signal_integration', {}).get('status', 'skipped'),
        total_duration,
        json.dumps(summary),
    ))
    conn.commit()
    conn.close()

    # ── Ph74: Parquet snapshot (background, non-blocking analytics cache) ────────
    if _DUCKDB_LAYER_NL:
        try:
            _pq_result = _export_parquet(verbose=False)
            summary['parquet_exported'] = _pq_result.get('exported', [])
        except Exception as _pq_e:
            summary['parquet_error'] = str(_pq_e)

    print(json.dumps({"status": "night_lab_complete", **summary}))
    sys.stdout.flush()

def cmd_status():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT run_date, command, per_stock_status, cycle_hunter_status, "
            "cross_stock_status, evolution_status, signal_integration_status, "
            "total_duration_seconds, created_at "
            "FROM night_lab_runs ORDER BY id DESC LIMIT 10"
        ).fetchall()
        conn.close()
        for r in rows:
            print(json.dumps(dict(r)))
    except Exception as e:
        conn.close()
        print(json.dumps({"error": str(e)}))

# ── Weekly Deep Training (الأحد فجراً) ────────────────────────────────────────

def cmd_weekly_deep():
    """
    التدريب العميق الأسبوعي — يُشغَّل كل أحد ~03:00 صباحاً (بعد midnight السبت).
    يُعيد تدريب كامل pipeline ML:
      Ph1 Feature Store → Ph2 Ensemble (LGBM+XGB+RF+ET) → Ph3 Regime Models →
      Ph4 Per-Stock → Ph5 Triple Barrier → Ph6 Walk-Forward → Ph7 SHAP Prune →
      predict_ensemble → score_all
    المدة التقديرية: ~90 دقيقة على 16-core Mac.
    """
    t0 = time.time()
    today_str = datetime.date.today().isoformat()

    print(json.dumps({
        "status": "weekly_deep_start",
        "date": today_str,
        "desc": "Full ML pipeline retrain (Ph1→Ph7 + ensemble predict + score_all)"
    }), flush=True)

    results = {}

    # ── Step 0 (weekly): OHLCV Data Validator ────────────────────────────────
    try:
        _vcw = get_db()
        _vrw = validate_ohlcv_latest_bar(_vcw, max_dup_pct=0.5, verbose=True)
        _vcw.close()
        results['ohlcv_validator'] = _vrw
        if _vrw.get('action') == 'deleted_corrupt_day':
            print(f"[weekly] ⚠️  Corrupt OHLCV day {_vrw['date']} deleted "
                  f"({_vrw['deleted']} bars)", flush=True)
    except Exception as _vew:
        results['ohlcv_validator'] = {'action': f'error:{_vew}'}

    # ── Ph1: Feature store refresh (~5 min) ───────────────────────────────────
    print("[weekly] ⚙️  Ph1 — Feature Engineering...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase1", timeout=600)
    results['ph1_features'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph1: {results['ph1_features']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph2: Full ensemble retrain (~60 min, Optuna 200 trials) ───────────────
    print("[weekly] 🤖 Ph2 — Ensemble LightGBM+XGB+RF+ET (Optuna 200 trials)...", flush=True)
    rc, out, _, dur = run_script("egx_ml_trainer.py", "phase2", timeout=7200)
    results['ph2_ensemble'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    # Extract AUC from output
    try:
        for line in reversed(out.splitlines()):
            parsed = json.loads(line)
            if parsed.get('phase') == '2' and 'ensemble' in str(parsed):
                ens = parsed.get('ensemble', {})
                results['ph2_ensemble']['auc'] = ens.get('auc_oos')
                break
    except Exception:
        pass
    print(f"[weekly] Ph2: {results['ph2_ensemble']['status']} ({dur/60:.1f}min) AUC={results['ph2_ensemble'].get('auc','?')}", flush=True)

    # ── Ph3: Regime-specific models (~30 min per regime × 4) ─────────────────
    print("[weekly] 🌐 Ph3 — Regime Models (BULL/BEAR/CHOPPY/UNKNOWN)...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase3", timeout=7200)
    results['ph3_regime'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph3: {results['ph3_regime']['status']} ({dur/60:.1f}min)", flush=True)

    # ── Ph4: Per-stock models (~18s, Pool(8)) ─────────────────────────────────
    print("[weekly] 📊 Ph4 — Per-Stock Models (Pool 8)...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase4", timeout=300)
    results['ph4_stocks'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph4: {results['ph4_stocks']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph5: Triple barrier meta-labeling (~3s) ────────────────────────────────
    print("[weekly] 🏷️  Ph5 — Triple Barrier Meta-Labeling...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase5", timeout=120)
    results['ph5_barrier'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph5: {results['ph5_barrier']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph6: Walk-forward backtest (~10s) ─────────────────────────────────────
    print("[weekly] 📈 Ph6 — Walk-Forward Backtest (4 windows)...", flush=True)
    rc, out, _, dur = run_script("egx_ml_trainer.py", "phase6", timeout=300)
    results['ph6_backtest'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    try:
        for line in reversed(out.splitlines()):
            parsed = json.loads(line)
            if parsed.get('phase') == '6':
                results['ph6_backtest']['avg_sharpe'] = parsed.get('avg_sharpe')
                break
    except Exception:
        pass
    print(f"[weekly] Ph6: {results['ph6_backtest']['status']} Sharpe={results['ph6_backtest'].get('avg_sharpe','?')}", flush=True)

    # ── Ph7: SHAP feature importance + auto-prune (~5s) ───────────────────────
    print("[weekly] 🔬 Ph7 — SHAP Analysis + Prune...", flush=True)
    rc, out, _, dur = run_script("egx_ml_trainer.py", "phase7", timeout=120)
    results['ph7_shap'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    try:
        for line in reversed(out.splitlines()):
            parsed = json.loads(line)
            if parsed.get('phase') == '7':
                results['ph7_shap']['n_weak'] = parsed.get('n_weak', 0)
                results['ph7_shap']['top5'] = parsed.get('top5', [])
                break
    except Exception:
        pass
    print(f"[weekly] Ph7: {results['ph7_shap']['status']} weak={results['ph7_shap'].get('n_weak','?')}", flush=True)

    # ── predict_ensemble: score 252 symbols ───────────────────────────────────
    print("[weekly] 🎯 predict_ensemble — scoring all symbols...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "predict_ensemble", timeout=300)
    results['predict_ensemble'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] predict_ensemble: {results['predict_ensemble']['status']} ({dur:.0f}s)", flush=True)

    # ── score_all: update unified_signals UES ─────────────────────────────────
    print("[weekly] ⚡ score_all — update unified scores...", flush=True)
    rc, out, _, dur = run_script("signal_integration.py", "score_all", timeout=300)
    results['score_all'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    try:
        for line in reversed(out.splitlines()):
            parsed = json.loads(line)
            if 'n_gated' in parsed or 'gated' in str(parsed):
                results['score_all']['n_gated'] = parsed.get('n_gated', parsed.get('gated'))
                break
    except Exception:
        pass
    print(f"[weekly] score_all: {results['score_all']['status']} gated={results['score_all'].get('n_gated','?')}", flush=True)

    # ── Ph46: Bayesian Win Rate (Beta-Binomial posterior) ─────────────────────
    print("[weekly] 🧮 Ph46 — Bayesian Win Rate...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase46", timeout=60)
    results['ph46_bayesian'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph46: {results['ph46_bayesian']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph50: Adaptive Gate Calibration (≈0.1ث) ──────────────────────────────
    print("[weekly] 🎯 Ph50 — Adaptive Gate Calibration...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase50", timeout=30)
    results['ph50_adaptive_gate'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph50: {results['ph50_adaptive_gate']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph52+53: Enhanced Breadth + Sector Rotation (يجب أن يسبق Ph56/Ph51/Ph55)
    print("[weekly] 🔄 Ph52+53 — Enhanced Breadth + Sector Rotation...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase52", timeout=120)
    results['ph52_53_breadth'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph52+53: {results['ph52_53_breadth']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph56: Markov Regime Engine (يجب أن يسبق Ph51/Ph55 — يقرأ breadth_enhanced)
    print("[weekly] 🔄 Ph56 — Markov Regime Engine...", flush=True)
    rc, out56w, _, dur = run_script("egx_ml_trainer.py", "phase56", timeout=120)
    results['ph56_markov'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    try:
        for line in reversed(out56w.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"latest_state"' in line:
                mk56w = json.loads(line)
                results['ph56_markov']['latest_state']  = mk56w.get('latest_state', '?')
                results['ph56_markov']['latest_signal'] = mk56w.get('latest_signal_1d', 0)
                results['ph56_markov']['wf_accuracy']   = mk56w.get('wf_accuracy', 0)
                break
    except Exception:
        pass
    lat_s = results['ph56_markov'].get('latest_state', '?')
    lat_g = results['ph56_markov'].get('latest_signal', 0) or 0
    wf_a  = results['ph56_markov'].get('wf_accuracy', 0) or 0
    print(f"[weekly] Ph56: {results['ph56_markov']['status']} "
          f"state={lat_s} signal={lat_g:+.3f} wf_acc={wf_a:.1%} ({dur:.0f}s)", flush=True)

    # ── Ph57: Closing Pressure Signal (daily OHLCV proxy) ────────────────────
    print("[weekly] 🕯️  Ph57 — Closing Pressure Signal...", flush=True)
    rc, _out57w, _, dur = run_script("egx_ml_trainer.py", "phase57", timeout=120)
    results['ph57_closing_pressure'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph57: {results['ph57_closing_pressure']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph51: Tomorrow Direction Forecast (يقرأ ميزات Markov من markov_signal_daily)
    print("[weekly] 📅 Ph51 — Tomorrow Direction Forecast...", flush=True)
    rc, out51w, _, dur = run_script("egx_ml_trainer.py", "phase51", timeout=180)
    results['ph51_tomorrow'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    try:
        for line in reversed(out51w.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"direction"' in line:
                fc = json.loads(line)
                results['ph51_tomorrow']['direction'] = fc.get('direction','?')
                results['ph51_tomorrow']['p_up']      = fc.get('p_up', 0)
                break
    except Exception:
        pass
    print(f"[weekly] Ph51: {results['ph51_tomorrow']['status']} dir={results['ph51_tomorrow'].get('direction','?')} ({dur:.0f}s)", flush=True)

    # ── Ph55: Per-Stock Tomorrow Direction Forecast (يقرأ ميزات Markov أيضاً)
    print("[weekly] 📈 Ph55 — Per-Stock Tomorrow Direction Forecast...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase55", timeout=240)
    results['ph55_stock_forecast'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph55: {results['ph55_stock_forecast']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph77: tsfresh Feature Extraction (weekly refresh) ───────────────────────
    # Extracts 300+ statistical features from explosive_moves events,
    # selects the most discriminative, saves to tsfresh_selected_features.json.
    # Non-blocking: failures are logged but don't halt the pipeline.
    print("[weekly] 🔬 Ph77 — tsfresh Feature Extraction (weekly refresh)...", flush=True)
    try:
        rc77, out77, _, dur77 = run_script(
            "tsfresh_features.py",
            "extract_explosions",
            '{"lookback":20,"max_events":1000,"save_model":true}',
            timeout=600,
        )
        _ph77_status = get_script_status(rc77)
        _ph77_info = {}
        try:
            for line in reversed(out77.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_features_selected"' in line:
                    _ph77_info = json.loads(line)
                    break
        except Exception:
            pass
        results['ph77_tsfresh'] = {
            'status':   _ph77_status,
            'duration': round(dur77, 1),
            'n_selected': _ph77_info.get('n_features_selected'),
            'n_events':   _ph77_info.get('n_events'),
        }
        print(f"[weekly] Ph77: {_ph77_status} events={_ph77_info.get('n_events','?')} "
              f"features={_ph77_info.get('n_features_selected','?')} ({dur77/60:.1f}min)", flush=True)
    except Exception as e77:
        results['ph77_tsfresh'] = {'status': f'exception:{e77}', 'duration': 0}
        print(f"[weekly] Ph77: skipped ({e77})", flush=True)

    # ── Ph47: QMC Portfolio Risk (Sobol) ──────────────────────────────────────
    print("[weekly] 📉 Ph47 — QMC Portfolio Risk (Sobol N=4096)...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase47", timeout=120)
    results['ph47_qmc'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph47: {results['ph47_qmc']['status']} ({dur:.0f}s)", flush=True)

    # ── Ph48: Antithetic Variates Backtest ────────────────────────────────────
    print("[weekly] 📊 Ph48 — Antithetic Variates Backtest...", flush=True)
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase48", timeout=120)
    results['ph48_antithetic'] = {'status': get_script_status(rc), 'duration': round(dur, 1)}
    print(f"[weekly] Ph48: {results['ph48_antithetic']['status']} ({dur:.0f}s)", flush=True)

    total_duration = time.time() - t0
    all_ok = all(v['status'] == 'ok' for v in results.values())

    # Save run record
    conn = get_db()
    ensure_tables(conn)
    conn.execute("""
        INSERT INTO night_lab_runs
        (run_date, command, per_stock_status, cycle_hunter_status, cross_stock_status,
         evolution_status, signal_integration_status, total_duration_seconds, summary)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        today_str, 'weekly_deep',
        results.get('ph4_stocks', {}).get('status', 'skipped'),
        results.get('ph3_regime', {}).get('status', 'skipped'),
        results.get('ph2_ensemble', {}).get('status', 'skipped'),
        results.get('ph7_shap', {}).get('status', 'skipped'),
        results.get('score_all', {}).get('status', 'skipped'),
        total_duration,
        json.dumps(results),
    ))
    conn.commit()
    conn.close()

    summary = {
        "status": "weekly_deep_complete",
        "date": today_str,
        "total_minutes": round(total_duration / 60, 1),
        "all_ok": all_ok,
        "ph2_auc": results.get('ph2_ensemble', {}).get('auc'),
        "ph6_sharpe": results.get('ph6_backtest', {}).get('avg_sharpe'),
        "ph7_weak": results.get('ph7_shap', {}).get('n_weak', 0),
        "score_all_gated": results.get('score_all', {}).get('n_gated'),
        "steps": {k: v['status'] for k, v in results.items()},
    }

    # ── Ph74: Parquet snapshot after deep training (force-refresh all tables) ────
    if _DUCKDB_LAYER_NL:
        try:
            _pq_result = _export_parquet(force=True, verbose=False)
            summary['parquet_exported'] = _pq_result.get('exported', [])
            print(json.dumps({"step": "parquet_snapshot", "exported": _pq_result.get('exported', []),
                              "duration_sec": _pq_result.get('duration_sec')}), flush=True)
        except Exception as _pq_e:
            summary['parquet_error'] = str(_pq_e)

    print(json.dumps(summary), flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    cmd = args[0] if args else 'run'
    if cmd == 'run':
        cmd_run(quick=False)
    elif cmd == 'quick':
        cmd_run(quick=True)
    elif cmd == 'weekly_deep':
        cmd_weekly_deep()
    elif cmd == 'status':
        cmd_status()
    else:
        print(json.dumps({"error": "unknown command", "usage": "run | quick | weekly_deep | status"}))
        sys.exit(1)
