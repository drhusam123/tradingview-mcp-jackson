#!/usr/bin/env python3
"""
Night Lab — Autonomous Overnight Learning Coordinator
Runs every night after market close, builds on all previous knowledge.
Sequences: per_stock_learner → cycle_hunter → cross_stock_brain → evolution_engine → signal_integration
"""
import os, sys, json, sqlite3, datetime, time, subprocess
from datetime import datetime, timezone
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


# ── Data Freshness Check (Fail-CLOSED) ───────────────────────────────────────

def _check_data_freshness(conn) -> dict:
    """
    يتحقق من حداثة البيانات قبل تشغيل أي ML.
    EGX يفتح الأحد-الخميس. نتحقق من آخر بيانات مقارنةً بآخر يوم تداول.
    """
    try:
        row = conn.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history WHERE close > 0"
        ).fetchone()
        last_ohlcv = row[0] if row and row[0] else None
    except Exception:
        last_ohlcv = None

    today = datetime.now(timezone.utc).date()

    if last_ohlcv is None:
        return {'last_ohlcv': None, 'days_behind': 99, 'is_fresh': False, 'is_critical': True}

    last_date = datetime.strptime(last_ohlcv, '%Y-%m-%d').date()
    delta = (today - last_date).days

    # EGX trades Sun-Thu. On Friday/Saturday, 1-2 days behind is normal.
    weekday = today.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    if weekday == 5:   # Saturday
        allowed_lag = 2
    elif weekday == 6: # Sunday (market opens, but data may not be in yet)
        allowed_lag = 3
    elif weekday == 0: # Monday
        allowed_lag = 4  # Thursday was last trading day
    else:
        allowed_lag = 1

    return {
        'last_ohlcv': last_ohlcv,
        'days_behind': delta,
        'is_fresh': delta <= allowed_lag,
        'is_critical': delta >= (allowed_lag + 2),  # critically stale
    }


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
    today_str = datetime.now().strftime('%Y-%m-%d')
    command = "quick" if quick else "run"

    # ── EGX Market Calendar: skip heavy pipeline on holidays / weekends ───
    # Client delivery is handled only by egx_telegram_daily.mjs quality gates.
    _today_is_trading = True
    _today_holiday    = None
    try:
        import importlib.util as _cal_ilu, os as _cal_os
        _cal_path = _cal_os.path.join(_cal_os.path.dirname(__file__), 'event_calendar.py')
        _cal_spec = _cal_ilu.spec_from_file_location('event_calendar', _cal_path)
        _cal_mod  = _cal_ilu.module_from_spec(_cal_spec)
        _cal_spec.loader.exec_module(_cal_mod)
        _today_is_trading = _cal_mod.is_trading_day(today_str)
        _today_holiday    = _cal_mod.holiday_name(today_str)
    except Exception as _ce:
        print(f"[night_lab] Calendar check failed ({_ce}) — proceeding as trading day", flush=True)

    if not _today_is_trading:
        _reason = _today_holiday if _today_holiday else "Weekend (Fri/Sat)"
        print(f"[night_lab] 🎉 Non-trading day: {_reason} — skipping ML pipeline.", flush=True)
        print(json.dumps({"status": "skipped_non_trading_day", "date": today_str, "reason": _reason}))
        return

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
            # FIX BUG-01: 'from datetime import datetime' binds datetime to the class,
            # not the module — use class methods directly (no .datetime sub-attribute).
            run_time = datetime.fromisoformat(created_at)
            now = datetime.utcnow()
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

    # ── Fail-CLOSED: تحقق من حداثة البيانات قبل أي ML ───────────────────────
    try:
        _fc_conn = get_db()
        _freshness = _check_data_freshness(_fc_conn)
        _fc_conn.close()
    except Exception as _fce:
        _freshness = {'last_ohlcv': None, 'days_behind': 99, 'is_fresh': False, 'is_critical': True}
    step_results['data_freshness'] = _freshness

    if _freshness['is_critical']:
        msg = (
            f"[FAIL-CLOSED] ⛔ OHLCV قديمة {_freshness['days_behind']} أيام "
            f"(آخر بيانات: {_freshness['last_ohlcv']}) — "
            f"النظام يوقف التشغيل حتى تُحدَّث البيانات"
        )
        print(msg, flush=True)
        print("[night_lab] Telegram halt alert blocked; health alerts are internal/log-only by default.", flush=True)
        return {
            'status': 'HALTED_STALE_DATA',
            'days_behind': _freshness['days_behind'],
            'last_ohlcv': _freshness['last_ohlcv'],
            'message': msg,
        }

    if not _freshness['is_fresh']:
        print(
            f"[night_lab] ⚠️ تحذير: OHLCV قديمة {_freshness['days_behind']} أيام "
            f"(آخر: {_freshness['last_ohlcv']}) — المتابعة مع تحذير",
            flush=True
        )

    # ── Step 0a: Health Monitor — فحص صحة النظام (~2s) ─────────────────────────
    try:
        rc_hm, out_hm, _, dur_hm = run_script(
            "health_monitor.py", "check", timeout=30,
        )
        _hm_status = get_script_status(rc_hm)
        _hm_info = {}
        try:
            for line in reversed(out_hm.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"status"' in line:
                    _hm_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['health_monitor'] = {
            'status': _hm_status,
            'duration': round(dur_hm, 1),
            'health': _hm_info.get('status', 'unknown'),
        }
        _hlt = _hm_info.get('status', '?')
        print(f"[night_lab] 🏥 Health Monitor: {_hm_status} | health={_hlt} ({dur_hm:.1f}s)", flush=True)
    except Exception as e_hm:
        step_results['health_monitor'] = {'status': f'exception:{e_hm}', 'duration': 0}
        print(f"[night_lab] Health Monitor: skipped ({e_hm})", flush=True)

    # ── Step 0b: Data Quality Gate — بوابة جودة البيانات (~5s) ─────────────────
    try:
        rc_dq, out_dq, _, dur_dq = run_script(
            "data_quality_gate.py", "build_full", '{}', timeout=120,
        )
        _dq_status = get_script_status(rc_dq)
        _dq_info = {}
        try:
            for line in reversed(out_dq.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_issues"' in line:
                    _dq_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['data_quality'] = {
            'status': _dq_status,
            'duration': round(dur_dq, 1),
            'n_issues': _dq_info.get('n_issues', 0),
        }
        _dq_n = _dq_info.get('n_issues', '?')
        print(f"[night_lab] 🔍 Data Quality: {_dq_status} | issues={_dq_n} ({dur_dq:.1f}s)", flush=True)
    except Exception as e_dq:
        step_results['data_quality'] = {'status': f'exception:{e_dq}', 'duration': 0}
        print(f"[night_lab] Data Quality Gate: skipped ({e_dq})", flush=True)

    # ── Step 0c: Global Macro Fetch — جلب الماكرو العالمي (~10s) ────────────────
    try:
        rc_gm, out_gm, _, dur_gm = run_script(
            "fetch_global_macro.py", "fetch_all", '{}', timeout=60,
        )
        _gm_status = get_script_status(rc_gm)
        _gm_info = {}
        try:
            for line in reversed(out_gm.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_fetched"' in line:
                    _gm_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['global_macro_fetch'] = {
            'status': _gm_status,
            'duration': round(dur_gm, 1),
            'n_fetched': _gm_info.get('n_fetched', 0),
        }
        print(f"[night_lab] 🌐 Global Macro: {_gm_status} | fetched={_gm_info.get('n_fetched','?')} ({dur_gm:.1f}s)", flush=True)
    except Exception as e_gm:
        step_results['global_macro_fetch'] = {'status': f'exception:{e_gm}', 'duration': 0}
        print(f"[night_lab] Global Macro Fetch: skipped ({e_gm})", flush=True)

    # ── Step 0d: Cross-Market Bridge — نقل الماكرو لجدول الأسواق (~3s) ──────
    try:
        rc_cmb, out_cmb, _, dur_cmb = run_script(
            "cross_market_bridge.py", "build_full", '{}', timeout=30,
        )
        _cmb_status = get_script_status(rc_cmb)
        _cmb_info = {}
        try:
            for line in reversed(out_cmb.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_inserted"' in line:
                    _cmb_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['cross_market_bridge'] = {
            'status':     _cmb_status,
            'duration':   round(dur_cmb, 1),
            'n_inserted': _cmb_info.get('n_inserted', 0),
        }
        print(f"[night_lab] 🔗 Cross-Market Bridge: {_cmb_status} | n={_cmb_info.get('n_inserted','?')} ({dur_cmb:.1f}s)", flush=True)
    except Exception as e_cmb:
        step_results['cross_market_bridge'] = {'status': f'exception:{e_cmb}', 'duration': 0}
        print(f"[night_lab] Cross-Market Bridge: skipped ({e_cmb})", flush=True)

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

    # ── Step 4b: Corporate Actions Tracker — اكتشاف التعديلات (~5s) ───────────
    try:
        rc_ca, out_ca, _, dur_ca = run_script(
            "corporate_actions_tracker.py", "build_full", '{}', timeout=60,
        )
        _ca_status = get_script_status(rc_ca)
        _ca_info = {}
        try:
            for line in reversed(out_ca.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_detected"' in line:
                    _ca_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['corporate_actions'] = {
            'status': _ca_status,
            'duration': round(dur_ca, 1),
            'n_detected': _ca_info.get('n_detected', 0),
        }
        print(f"[night_lab] 📋 Corp Actions: {_ca_status} | detected={_ca_info.get('n_detected','?')} ({dur_ca:.1f}s)", flush=True)
    except Exception as e_ca:
        step_results['corporate_actions'] = {'status': f'exception:{e_ca}', 'duration': 0}
        print(f"[night_lab] Corporate Actions: skipped ({e_ca})", flush=True)

    # ── Step 5: Signal integration score_all ─────────────────────────────────
    rc, _, _, dur = run_script("signal_integration.py", "score_all", timeout=1800)
    step_results['signal_integration'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 5b: Alpha Ranker — تصنيف وتتبع اضمحلال الألفا (~3s) ────────────
    try:
        rc_ar, out_ar, _, dur_ar = run_script(
            "alpha_ranker.py", "build_full", '{}', timeout=60,
        )
        _ar_status = get_script_status(rc_ar)
        _ar_info = {}
        try:
            for line in reversed(out_ar.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"top_5"' in line:
                    _ar_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['alpha_ranker'] = {
            'status': _ar_status,
            'duration': round(dur_ar, 1),
            'top_alpha': _ar_info.get('top_5', []),
        }
        _top1 = (_ar_info.get('top_5') or [{}])[0].get('symbol', '?') if _ar_info.get('top_5') else '?'
        print(f"[night_lab] 🏆 Alpha Ranker: {_ar_status} | leader={_top1} ({dur_ar:.1f}s)", flush=True)
    except Exception as e_ar:
        step_results['alpha_ranker'] = {'status': f'exception:{e_ar}', 'duration': 0}
        print(f"[night_lab] Alpha Ranker: skipped ({e_ar})", flush=True)

    # ── Step 5c: Alert Automation — إدارة التنبيهات (~2s) ────────────────────
    try:
        rc_aa, out_aa, _, dur_aa = run_script(
            "alert_automation.py", "build_full", '{}', timeout=30,
        )
        _aa_status = get_script_status(rc_aa)
        _aa_info = {}
        try:
            for line in reversed(out_aa.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_active"' in line:
                    _aa_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['alert_automation'] = {
            'status': _aa_status,
            'duration': round(dur_aa, 1),
            'n_active': _aa_info.get('n_active', 0),
        }
        print(f"[night_lab] 🔔 Alerts: {_aa_status} | active={_aa_info.get('n_active','?')} ({dur_aa:.1f}s)", flush=True)
    except Exception as e_aa:
        step_results['alert_automation'] = {'status': f'exception:{e_aa}', 'duration': 0}
        print(f"[night_lab] Alert Automation: skipped ({e_aa})", flush=True)

    # ── Step 5d: Execution Reality Engine — تحقق تكاليف التنفيذ (~2s) ────────
    try:
        rc_er, out_er, _, dur_er = run_script(
            "execution_reality_engine.py", "build_full", '{}', timeout=30,
        )
        _er_status = get_script_status(rc_er)
        _er_info = {}
        try:
            for line in reversed(out_er.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_laws_checked"' in line:
                    _er_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['execution_reality'] = {
            'status': _er_status,
            'duration': round(dur_er, 1),
            'n_laws_checked': _er_info.get('n_laws_checked', 0),
        }
        print(f"[night_lab] ⚙️  Execution Reality: {_er_status} | laws={_er_info.get('n_laws_checked','?')} ({dur_er:.1f}s)", flush=True)
    except Exception as e_er:
        step_results['execution_reality'] = {'status': f'exception:{e_er}', 'duration': 0}
        print(f"[night_lab] Execution Reality: skipped ({e_er})", flush=True)

    # ── Step 5e: Capital Intelligence — إدارة رأس المال (~2s) ───────────────
    try:
        rc_ci, out_ci, _, dur_ci = run_script(
            "capital_intelligence.py", "build_full", '{}', timeout=30,
        )
        _ci_status = get_script_status(rc_ci)
        _ci_info = {}
        try:
            for line in reversed(out_ci.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"recommended_exposure_pct"' in line:
                    _ci_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['capital_intelligence'] = {
            'status': _ci_status,
            'duration': round(dur_ci, 1),
            'ee_regime': _ci_info.get('ee_regime', 'N/A'),
            'exposure_pct': _ci_info.get('recommended_exposure_pct', 0),
        }
        _ci_r = _ci_info.get('ee_regime', '?')
        _ci_e = _ci_info.get('recommended_exposure_pct', '?')
        print(f"[night_lab] 💰 Capital Intelligence: {_ci_status} | regime={_ci_r} exposure={_ci_e}% ({dur_ci:.1f}s)", flush=True)
    except Exception as e_ci:
        step_results['capital_intelligence'] = {'status': f'exception:{e_ci}', 'duration': 0}
        print(f"[night_lab] Capital Intelligence: skipped ({e_ci})", flush=True)

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

    # ── Step 8a: Regime History Daily Update — تحديث سجل الأنظمة يومياً (~5s) ──
    # CRITICAL FIX: regime_history was only updated in cmd_weekly_deep().
    # Without this, BEAR/BULL/CHOPPY regime used by signal_integration is up to 7
    # days stale — causing wrong BEAR_REGIME_FILTER decisions and 0 gate_passed.
    rc_rh, out_rh, _, dur_rh = run_script(
        "historical_validation.py", "regime_history", '{}', timeout=60
    )
    _rh_status = get_script_status(rc_rh)
    _rh_info = {}
    try:
        for line in reversed((out_rh or '').splitlines()):
            line = line.strip()
            if line.startswith('{') and 'regime' in line:
                _rh_info = json.loads(line)
                break
    except Exception:
        pass
    step_results['regime_history_daily'] = {
        'status': _rh_status,
        'duration': round(dur_rh, 1),
        'n_days': _rh_info.get('n_days', '?'),
    }
    print(f"[night_lab] 📅 Regime History: {_rh_status} | n_days={_rh_info.get('n_days','?')} ({dur_rh:.1f}s)", flush=True)

    # ── Step 8b: Signal Integration Re-score — إعادة تسجيل الإشارات (~30s) ──
    # CRITICAL FIX: Step 5 score_all ran BEFORE predict_ensemble (Step 8), so
    # unified_signals.gate_passed was computed using yesterday's ML scores.
    # This second score_all run uses today's fresh explosion_predictions.
    rc_rs, _, _, dur_rs = run_script("signal_integration.py", "score_all", timeout=1800)
    _rs_status = get_script_status(rc_rs)
    step_results['signal_integration_rescore'] = {
        'status': _rs_status,
        'duration': round(dur_rs, 1),
    }
    print(f"[night_lab] 🔄 Signal Re-score (fresh ML): {_rs_status} ({dur_rs:.1f}s)", flush=True)

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

    # ── Step 19c2: Regime Transition Forecaster (~5s) ──────────────────────────
    try:
        rc_rt, out_rt, _, dur_rt = run_script(
            "regime_transition_forecaster.py", "build_full", '{}', timeout=60,
        )
        _rt_status = get_script_status(rc_rt)
        _rt_info = {}
        try:
            for line in reversed(out_rt.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"transition_risk"' in line:
                    _rt_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['regime_transition'] = {
            'status': _rt_status,
            'duration': round(dur_rt, 1),
            'transition_risk': _rt_info.get('transition_risk'),
        }
        _tr_risk = _rt_info.get('transition_risk', '?')
        print(f"[night_lab] 🔄 Regime Transition: {_rt_status} | risk={_tr_risk} ({dur_rt:.1f}s)", flush=True)
    except Exception as e_rt:
        step_results['regime_transition'] = {'status': f'exception:{e_rt}', 'duration': 0}
        print(f"[night_lab] Regime Transition: skipped ({e_rt})", flush=True)

    # ── Step 19c3: Cross-Market Coupling Engine (~5s) ──────────────────────────
    try:
        rc_cm, out_cm, _, dur_cm = run_script(
            "cross_market_engine.py", "build_full", '{}', timeout=60,
        )
        _cm_status = get_script_status(rc_cm)
        _cm_info = {}
        try:
            for line in reversed(out_cm.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"overall_regime"' in line:
                    _cm_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['cross_market'] = {
            'status': _cm_status,
            'duration': round(dur_cm, 1),
            'regime': _cm_info.get('overall_regime', 'N/A'),
            'risk_on': _cm_info.get('risk_on_score'),
        }
        _cm_reg = _cm_info.get('overall_regime', '?')
        _cm_ros = _cm_info.get('risk_on_score', '?')
        print(f"[night_lab] 🌍 Cross-Market: {_cm_status} | regime={_cm_reg} risk_on={_cm_ros} ({dur_cm:.1f}s)", flush=True)
    except Exception as e_cm:
        step_results['cross_market'] = {'status': f'exception:{e_cm}', 'duration': 0}
        print(f"[night_lab] Cross-Market: skipped ({e_cm})", flush=True)

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

    # ── Step 19d1: Ph77 — tsfresh Daily Store (~1s) ──────────────────────────────
    # يُشغَّل هنا (قبل Ph51/Ph55) حتى تستفيد منه في نفس الجلسة.
    # الاستخراج الثقيل (extract_explosions) يُشغَّل أسبوعياً في weekly_deep.
    try:
        rc77n, out77n, _, dur77n = run_script(
            "tsfresh_features.py", "daily_store", '{"lookback":20}', timeout=60,
        )
        _ph77n_status = get_script_status(rc77n)
        _ph77n_info   = {}
        try:
            for line in reversed(out77n.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_stored"' in line:
                    _ph77n_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['tsfresh_daily'] = {
            'status':     _ph77n_status,
            'duration':   round(dur77n, 1),
            'n_stored':   _ph77n_info.get('n_stored'),
            'trade_date': _ph77n_info.get('trade_date'),
        }
        print(f"[night_lab] 🔬 Ph77 tsfresh_daily: {_ph77n_status} "
              f"stored={_ph77n_info.get('n_stored','?')} ({dur77n:.1f}s)", flush=True)
    except Exception as e77n:
        step_results['tsfresh_daily'] = {'status': f'exception:{e77n}', 'duration': 0}
        print(f"[night_lab] Ph77: skipped ({e77n})", flush=True)

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

    # ── Step 19h: Technical Confluence Score (~5s) ─────────────────────────────
    try:
        rc_tc, out_tc, _, dur_tc = run_script(
            "technical_confluence.py", "build_full", json.dumps({"date": today_str}), timeout=60,
        )
        _tc_status = get_script_status(rc_tc)
        _tc_info = {}
        try:
            for line in reversed(out_tc.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"confluence"' in line:
                    _tc_info = json.loads(line)
                    break
        except Exception:
            pass
        _tc_conf = _tc_info.get('confluence', {})
        _tc_picks = len(_tc_conf.get('top_picks', []))
        step_results['technical_confluence'] = {
            'status': _tc_status,
            'duration': round(dur_tc, 1),
            'n_top_picks': _tc_picks,
        }
        print(f"[night_lab] 📐 Technical Confluence: {_tc_status} | top_picks={_tc_picks} ({dur_tc:.1f}s)", flush=True)
    except Exception as e_tc:
        step_results['technical_confluence'] = {'status': f'exception:{e_tc}', 'duration': 0}
        print(f"[night_lab] Technical Confluence: skipped ({e_tc})", flush=True)

    # ── Step 19i: Anti-Laws Daily Scan (~5s) ──────────────────────────────────
    try:
        rc_al, out_al, _, dur_al = run_script(
            "anti_laws_engine.py", "daily_scan", '{}', timeout=60,
        )
        _al_status = get_script_status(rc_al)
        _al_info = {}
        try:
            for line in reversed(out_al.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_veto"' in line:
                    _al_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['anti_laws'] = {
            'status': _al_status,
            'duration': round(dur_al, 1),
            'n_veto': _al_info.get('n_veto', 0),
            'n_caution': _al_info.get('n_caution', 0),
        }
        _al_v = _al_info.get('n_veto', '?')
        _al_c = _al_info.get('n_caution', '?')
        print(f"[night_lab] ⚖️  Anti-Laws Scan: {_al_status} | veto={_al_v} caution={_al_c} ({dur_al:.1f}s)", flush=True)
    except Exception as e_al:
        step_results['anti_laws'] = {'status': f'exception:{e_al}', 'duration': 0}
        print(f"[night_lab] Anti-Laws Scan: skipped ({e_al})", flush=True)

    # ── Step 20: Ph47 — QMC Portfolio Risk (Sobol sequences) ─────────────────
    # يُشغَّل كل ليلة — تقدير VaR/CVaR بدقة أعلى من MC العادي
    rc, _, _, dur = run_script("egx_ml_trainer.py", "phase47", timeout=120)
    step_results['qmc_portfolio_risk'] = {'status': get_script_status(rc), 'duration': dur}

    # ── Step 20a: Ph81 — Risk Engine Check (~2s) ──────────────────────────────────
    try:
        rc_re, out_re, _, dur_re = run_script(
            "risk_engine.py", "check", "--json", timeout=30,
        )
        _re_status = get_script_status(rc_re)
        _re_info = {}
        try:
            _re_out = out_re.strip()
            if _re_out.startswith('{'):
                _re_info = json.loads(_re_out)
            else:
                for line in reversed(out_re.splitlines()):
                    line = line.strip()
                    if line.startswith('{') and '"overall_level"' in line:
                        _re_info = json.loads(line); break
        except Exception:
            pass
        _re_level = _re_info.get('overall_level', 'UNKNOWN')
        _re_drawdown = (_re_info.get('drawdown') or {}).get('current_pct')
        step_results['risk_engine'] = {
            'status': _re_status,
            'duration': round(dur_re, 1),
            'overall_level': _re_level,
            'exposure_multiplier': 1.0,  # not in new API — kept for backward compat
            'alpha_health': 'OK' if _re_level == 'NORMAL' else _re_level,
        }
        print(f"[night_lab] 🛡️ Risk Engine: {_re_status} | "
              f"Level={_re_level} dd={_re_drawdown}% ({dur_re:.1f}s)", flush=True)
    except Exception as e_re:
        step_results['risk_engine'] = {'status': f'exception:{e_re}', 'duration': 0}
        print(f"[night_lab] Risk Engine: skipped ({e_re})", flush=True)

    # ── Step 20b: Ph80 — Portfolio Construction (~3s) ─────────────────────────────
    try:
        _pe_multiplier = step_results.get('risk_engine', {}).get('exposure_multiplier', 1.0)
        if _pe_multiplier < 1.0:
            print(f"[night_lab] ⚠️ Portfolio: applying risk multiplier {_pe_multiplier:.2f} (drawdown protection)", flush=True)
        rc_pe, out_pe, _, dur_pe = run_script(
            "portfolio_engine.py", "build",
            "--capital", "1000000",
            "--drawdown-mult", str(_pe_multiplier),
            timeout=30,
        )
        _pe_status = get_script_status(rc_pe)
        _pe_info = {}
        try:
            for line in reversed(out_pe.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_positions"' in line:
                    _pe_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['portfolio_engine'] = {
            'status': _pe_status,
            'duration': round(dur_pe, 1),
            'n_positions': _pe_info.get('n_positions'),
            'total_exposure_pct': _pe_info.get('total_exposure_pct'),
        }
        print(f"[night_lab] 🏗️ Portfolio: {_pe_status} | "
              f"positions={_pe_info.get('n_positions','?')} | "
              f"exposure={_pe_info.get('total_exposure_pct','?')}% ({dur_pe:.1f}s)", flush=True)
    except Exception as e_pe:
        step_results['portfolio_engine'] = {'status': f'exception:{e_pe}', 'duration': 0}
        print(f"[night_lab] Portfolio Engine: skipped ({e_pe})", flush=True)

    # ── Step 20c: Ph82 — Macro Sector Analysis (~2s) ──────────────────────────────
    try:
        rc_ms, out_ms, _, dur_ms = run_script(
            "macro_sector_engine.py", "analyze", "--json", timeout=30,
        )
        _ms_status = get_script_status(rc_ms)
        _ms_info = {}
        try:
            _ms_out = out_ms.strip()
            if _ms_out.startswith('{'):
                _ms_info = json.loads(_ms_out)
        except Exception: pass
        _ms_regime = (_ms_info.get('macro_snapshot') or {}).get('regime') or _ms_info.get('rate_direction')
        step_results['macro_sector'] = {
            'status': _ms_status,
            'duration': round(dur_ms, 1),
            'macro_regime': _ms_regime,
        }
        print(f"[night_lab] 🌍 Macro Sector: {_ms_status} | regime={_ms_regime} ({dur_ms:.1f}s)", flush=True)
    except Exception as e_ms:
        step_results['macro_sector'] = {'status': f'exception:{e_ms}', 'duration': 0}
        print(f"[night_lab] Macro Sector: skipped ({e_ms})", flush=True)

    # ── Step 20e: Event Calendar — upcoming corporate events alert ────────────────────
    try:
        rc_ec, out_ec, _, dur_ec = run_script(
            "event_calendar.py", "status", '{"days":7}', timeout=15,
        )
        _ec_status = get_script_status(rc_ec)
        _ec_info = {}
        try:
            for line in reversed(out_ec.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_events"' in line:
                    _ec_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['event_calendar'] = {
            'status': _ec_status,
            'duration': round(dur_ec, 1),
            'n_events': _ec_info.get('n_events', 0),
            'alert': _ec_info.get('alert'),
        }
        _alert = _ec_info.get('alert', '')
        print(f"[night_lab] 📅 Event Calendar: {_ec_status} | "
              f"events={_ec_info.get('n_events',0)} | {_alert or 'no alerts'} ({dur_ec:.1f}s)", flush=True)
    except Exception as e_ec:
        step_results['event_calendar'] = {'status': f'exception:{e_ec}', 'duration': 0}
        print(f"[night_lab] Event Calendar: skipped ({e_ec})", flush=True)

    # ── Step 20d: Ph78 — Institutional Metrics Scorecard (~3s) ────────────────────
    try:
        rc_im, out_im, _, dur_im = run_script(
            "institutional_metrics.py", "--json", "--days", "90", timeout=30,
        )
        _im_status = get_script_status(rc_im)
        _im_info = {}
        try:
            # Output is pretty-printed JSON — collect all lines and parse the full block
            _im_out_stripped = out_im.strip()
            if _im_out_stripped.startswith('{'):
                _im_info = json.loads(_im_out_stripped)
            else:
                # Fallback: find first JSON object block
                for line in reversed(out_im.splitlines()):
                    line = line.strip()
                    if '"institutional_grade"' in line:
                        try: _im_info = json.loads(_im_out_stripped); break
                        except Exception: pass
        except Exception:
            pass
        step_results['institutional_metrics'] = {
            'status': _im_status,
            'duration': round(dur_im, 1),
            'grade': _im_info.get('institutional_grade', 'N/A'),
            'sharpe': _im_info.get('sharpe'),
            'max_dd': _im_info.get('max_drawdown'),
        }
        print(f"[night_lab] 📊 Inst. Metrics: {_im_status} | "
              f"Grade={_im_info.get('institutional_grade','?')} | "
              f"Sharpe={_im_info.get('sharpe','?')} ({dur_im:.1f}s)", flush=True)
    except Exception as e_im:
        step_results['institutional_metrics'] = {'status': f'exception:{e_im}', 'duration': 0}
        print(f"[night_lab] Institutional Metrics: skipped ({e_im})", flush=True)

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

    # ── Step 28: Unified Daily Synthesis — التوليف اليومي الشامل (~10s) ────────
    try:
        rc_ud, out_ud, _, dur_ud = run_script(
            "unified_daily_synthesis.py", "synthesize", '{}', timeout=120,
        )
        _ud_status = get_script_status(rc_ud)
        _ud_info = {}
        try:
            for line in reversed(out_ud.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"synthesis_id"' in line:
                    _ud_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['unified_synthesis'] = {
            'status': _ud_status,
            'duration': round(dur_ud, 1),
            'synthesis_id': _ud_info.get('synthesis_id'),
        }
        _ud_id = _ud_info.get('synthesis_id', '?')
        print(f"[night_lab] 🧠 Unified Synthesis: {_ud_status} | id={_ud_id} ({dur_ud:.1f}s)", flush=True)
    except Exception as e_ud:
        step_results['unified_synthesis'] = {'status': f'exception:{e_ud}', 'duration': 0}
        print(f"[night_lab] Unified Synthesis: skipped ({e_ud})", flush=True)

    # ── Step 29: Uncertainty Engine — تقدير عدم اليقين (~3s) ─────────────────
    try:
        rc_ue, out_ue, _, dur_ue = run_script(
            "uncertainty_engine.py", "build_full", '{}', timeout=60,
        )
        _ue_status = get_script_status(rc_ue)
        _ue_info = {}
        try:
            for line in reversed(out_ue.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"total_uncertainty"' in line:
                    _ue_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['uncertainty'] = {
            'status': _ue_status,
            'duration': round(dur_ue, 1),
            'total_uncertainty': _ue_info.get('total_uncertainty'),
            'pipeline_confidence': _ue_info.get('pipeline_confidence'),
        }
        _uc = _ue_info.get('total_uncertainty', '?')
        print(f"[night_lab] 🎲 Uncertainty: {_ue_status} | unc={_uc} ({dur_ue:.1f}s)", flush=True)
    except Exception as e_ue:
        step_results['uncertainty'] = {'status': f'exception:{e_ue}', 'duration': 0}
        print(f"[night_lab] Uncertainty Engine: skipped ({e_ue})", flush=True)

    # ── Step 30: Longitudinal Learning — التتبع طويل الأمد (~3s) ────────────
    try:
        rc_ll, out_ll, _, dur_ll = run_script(
            "longitudinal_learning.py", "build_full", '{}', timeout=60,
        )
        _ll_status = get_script_status(rc_ll)
        _ll_info = {}
        try:
            for line in reversed(out_ll.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"avg_reliability"' in line:
                    _ll_info = json.loads(line)
                    break
        except Exception:
            pass
        step_results['longitudinal'] = {
            'status': _ll_status,
            'duration': round(dur_ll, 1),
            'avg_reliability': _ll_info.get('avg_reliability'),
        }
        _lr = _ll_info.get('avg_reliability', '?')
        print(f"[night_lab] 📈 Longitudinal: {_ll_status} | reliability={_lr} ({dur_ll:.1f}s)", flush=True)
    except Exception as e_ll:
        step_results['longitudinal'] = {'status': f'exception:{e_ll}', 'duration': 0}
        print(f"[night_lab] Longitudinal Learning: skipped ({e_ll})", flush=True)

    # ── Step 31: Liquidity Microstructure — تحليل سيولة السوق (~20s) ─────────
    try:
        rc_lm, out_lm, _, dur_lm = run_script(
            "liquidity_microstructure.py", "build_full", '{}', timeout=60,
        )
        _lm_status = get_script_status(rc_lm)
        _lm_info = {}
        try:
            for line in reversed(out_lm.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_computed"' in line:
                    _lm_info = json.loads(line)
                    break
            if not _lm_info:
                for line in reversed(out_lm.splitlines()):
                    line = line.strip()
                    if line.startswith('{'):
                        _lm_info = json.loads(line)
                        break
        except Exception:
            pass
        _lm_bld = _lm_info.get('build_summary', {})
        _lm_rpt = _lm_info.get('report_summary', {})
        step_results['liquidity_microstructure'] = {
            'status':        _lm_status,
            'duration':      round(dur_lm, 1),
            'n_computed':    _lm_bld.get('n_computed'),
            'tradeable_pct': _lm_rpt.get('tradeable_pct'),
            'mkt_liq_score': _lm_rpt.get('market_liquidity_score'),
        }
        _lm_n   = _lm_bld.get('n_computed', '?')
        _lm_pct = _lm_rpt.get('tradeable_pct', '?')
        _lm_sc  = _lm_rpt.get('market_liquidity_score', '?')
        print(f"[night_lab] 💧 Liquidity: {_lm_status} | n={_lm_n} tradeable={_lm_pct}% score={_lm_sc} ({dur_lm:.1f}s)", flush=True)
    except Exception as e_lm:
        step_results['liquidity_microstructure'] = {'status': f'exception:{e_lm}', 'duration': 0}
        print(f"[night_lab] Liquidity Microstructure: skipped ({e_lm})", flush=True)

    # ── Step 32: Market Breadth Engine — مؤشرات اتساع السوق (~0.5s) ──────────
    try:
        rc_mb, out_mb, _, dur_mb = run_script("market_breadth_engine.py", "build_full", '{}', timeout=30)
        _mb_status = get_script_status(rc_mb)
        _mb_info = {}
        try:
            _mb_info = json.loads(out_mb) if out_mb.strip().startswith('{') else {}
            if not _mb_info:
                for line in reversed(out_mb.splitlines()):
                    if line.strip().startswith('{'):
                        _mb_info = json.loads(line.strip()); break
        except Exception: pass
        _mb_b = _mb_info.get('breadth', {})
        step_results['market_breadth'] = {'status': _mb_status, 'duration': round(dur_mb,1),
            'adv_ratio': _mb_b.get('adv_ratio') if isinstance(_mb_b, dict) else None}
        print(f"[night_lab] 📊 Market Breadth: {_mb_status} ({dur_mb:.1f}s)", flush=True)
    except Exception as e: step_results['market_breadth'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 33: Explosion Physics — طاقة الانفجار والجاهزية (~1s) ─────────
    try:
        rc_ep, out_ep, _, dur_ep = run_script("explosion_physics_engine.py", "compute_readiness", '{}', timeout=30)
        _ep_status = get_script_status(rc_ep)
        _ep_info = {}
        try:
            for line in reversed(out_ep.splitlines()):
                if line.strip().startswith('{'):
                    _ep_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['explosion_physics'] = {'status': _ep_status, 'duration': round(dur_ep,1),
            'regime': _ep_info.get('regime')}
        print(f"[night_lab] 💥 Explosion Physics: {_ep_status} | regime={_ep_info.get('regime','?')} ({dur_ep:.1f}s)", flush=True)
    except Exception as e: step_results['explosion_physics'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 34: Failure Memory — استذكار حالات الفشل السابقة (~2s) ─────────
    try:
        rc_fm, out_fm, _, dur_fm = run_script("failure_memory_engine.py", "daily_failure_scan", '{}', timeout=30)
        _fm_status = get_script_status(rc_fm)
        _fm_info = {}
        try:
            for line in reversed(out_fm.splitlines()):
                if line.strip().startswith('{'):
                    _fm_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['failure_memory'] = {'status': _fm_status, 'duration': round(dur_fm,1),
            'n_failure_modes': _fm_info.get('n_failure_modes', _fm_info.get('n_failures'))}
        print(f"[night_lab] 🧠 Failure Memory: {_fm_status} | modes={_fm_info.get('n_failure_modes',_fm_info.get('n_failures','?'))} ({dur_fm:.1f}s)", flush=True)
    except Exception as e: step_results['failure_memory'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 35: Intelligence Observatory — مرصد صحة المحركات (~0.5s) ───────
    try:
        rc_io, out_io, _, dur_io = run_script("intelligence_observatory.py", "build_full", '{}', timeout=30)
        _io_status = get_script_status(rc_io)
        _io_info = {}
        try:
            for line in reversed(out_io.splitlines()):
                if line.strip().startswith('{'):
                    _io_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['intelligence_observatory'] = {'status': _io_status, 'duration': round(dur_io,1),
            'system_status': _io_info.get('status')}
        print(f"[night_lab] 🔭 Observatory: {_io_status} | sys_status={_io_info.get('status','?')} ({dur_io:.1f}s)", flush=True)
    except Exception as e: step_results['intelligence_observatory'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 36: Historical Integrity — سلامة البيانات التاريخية (~1.5s) ────
    try:
        rc_hi, out_hi, _, dur_hi = run_script("historical_integrity_engine.py", "scan_all", '{}', timeout=30)
        _hi_status = get_script_status(rc_hi)
        _hi_info = {}
        try:
            for line in reversed(out_hi.splitlines()):
                if line.strip().startswith('{'):
                    _hi_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['historical_integrity'] = {'status': _hi_status, 'duration': round(dur_hi,1),
            'n_suspicious': _hi_info.get('n_suspicious', 0)}
        print(f"[night_lab] 🔍 Historical Integrity: {_hi_status} | suspicious={_hi_info.get('n_suspicious','?')} ({dur_hi:.1f}s)", flush=True)
    except Exception as e: step_results['historical_integrity'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 37: Governance Constitution — حوكمة النظام (~0.3s) ─────────────
    try:
        rc_gc, out_gc, _, dur_gc = run_script("governance_constitution.py", "governance_report", '{}', timeout=30)
        _gc_status = get_script_status(rc_gc)
        _gc_info = {}
        try:
            for line in reversed(out_gc.splitlines()):
                if line.strip().startswith('{'):
                    _gc_info = json.loads(line.strip()); break
        except Exception: pass
        _gc_audit = _gc_info.get('audit', {})
        _gc_viol  = _gc_audit.get('n_violations', 0) if isinstance(_gc_audit, dict) else 0
        step_results['governance'] = {'status': _gc_status, 'duration': round(dur_gc,1),
            'n_violations': _gc_viol}
        print(f"[night_lab] ⚖️  Governance: {_gc_status} | violations={_gc_viol} ({dur_gc:.1f}s)", flush=True)
    except Exception as e: step_results['governance'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 38: Energy Flow Engine — طاقة السوق الحالية (~0.3s) ────────────
    try:
        rc_ef, out_ef, _, dur_ef = run_script("energy_flow_engine.py", "energy_now", '{}', timeout=30)
        _ef_status = get_script_status(rc_ef)
        _ef_info = {}
        try:
            for line in reversed(out_ef.splitlines()):
                if line.strip().startswith('{'):
                    _ef_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['energy_flow'] = {'status': _ef_status, 'duration': round(dur_ef,1),
            'market_state': _ef_info.get('market_state'),
            'market_energy': _ef_info.get('market_energy')}
        print(f"[night_lab] ⚡ Energy Flow: {_ef_status} | state={_ef_info.get('market_state','?')} energy={_ef_info.get('market_energy','?')} ({dur_ef:.1f}s)", flush=True)
    except Exception as e: step_results['energy_flow'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 39: World Coupling Engine — الربط بالأسواق العالمية (~4s) ───────
    try:
        rc_wc, out_wc, _, dur_wc = run_script("world_coupling_engine.py", "coupling_now", '{}', timeout=30)
        _wc_status = get_script_status(rc_wc)
        _wc_info = {}
        try:
            for line in reversed(out_wc.splitlines()):
                if line.strip().startswith('{'):
                    _wc_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['world_coupling'] = {'status': _wc_status, 'duration': round(dur_wc,1),
            'regime': _wc_info.get('regime')}
        print(f"[night_lab] 🌍 World Coupling: {_wc_status} | regime={_wc_info.get('regime','?')} ({dur_wc:.1f}s)", flush=True)
    except Exception as e: step_results['world_coupling'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 40: Causal Engine — العلاقات السببية الآنية (~0.3s) ────────────
    try:
        rc_ce, out_ce, _, dur_ce = run_script("causal_engine.py", "causal_now", '{}', timeout=30)
        _ce_status = get_script_status(rc_ce)
        _ce_info = {}
        try:
            for line in reversed(out_ce.splitlines()):
                if line.strip().startswith('{'):
                    _ce_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['causal_engine'] = {'status': _ce_status, 'duration': round(dur_ce,1),
            'n_chains': _ce_info.get('n_chains', _ce_info.get('n_causal_links'))}
        print(f"[night_lab] 🔗 Causal Engine: {_ce_status} | chains={_ce_info.get('n_chains',_ce_info.get('n_causal_links','?'))} ({dur_ce:.1f}s)", flush=True)
    except Exception as e: step_results['causal_engine'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 41: Regime Transition Early Warning — إنذار مبكر انتقال النظام ─
    try:
        rc_rt, out_rt, _, dur_rt = run_script("regime_transition.py", "early_warning", '{}', timeout=30)
        _rt_status = get_script_status(rc_rt)
        _rt_info = {}
        try:
            _rt_raw = out_rt.strip()
            if _rt_raw:
                _rt_info = json.loads(_rt_raw)
        except Exception:
            try:
                for line in reversed(out_rt.splitlines()):
                    if line.strip().startswith('{'):
                        _rt_info = json.loads(line.strip()); break
            except Exception: pass
        step_results['regime_transition_warning'] = {'status': _rt_status, 'duration': round(dur_rt,1),
            'warning_level':         _rt_info.get('warning_level'),
            'most_likely_next':      _rt_info.get('most_likely_next_regime'),
            'similarity_precursor':  _rt_info.get('similarity_to_precursor')}
        _rt_w = _rt_info.get('warning_level', '?')
        _rt_n = _rt_info.get('most_likely_next_regime', '?')
        print(f"[night_lab] ⚠️  Regime Warning: {_rt_status} | level={_rt_w} next={_rt_n} ({dur_rt:.1f}s)", flush=True)
    except Exception as e: step_results['regime_transition_warning'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 42: Regime-Specific ML — تعيين الأنظمة لكل يوم (~0.4s) ─────────
    try:
        rc_rs, out_rs, _, dur_rs = run_script("regime_specific_ml.py", "assign_regimes", '{}', timeout=30)
        _rs_status = get_script_status(rc_rs)
        _rs_info = {}
        try:
            for line in reversed(out_rs.splitlines()):
                if line.strip().startswith('{'):
                    _rs_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['regime_specific_ml'] = {'status': _rs_status, 'duration': round(dur_rs,1),
            'n_dates': _rs_info.get('n_dates')}
        print(f"[night_lab] 🎯 Regime ML: {_rs_status} | n_dates={_rs_info.get('n_dates','?')} ({dur_rs:.1f}s)", flush=True)
    except Exception as e: step_results['regime_specific_ml'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 43: Market Evolution Status — حالة تطور النظام (~0.4s) ──────────
    try:
        rc_me, out_me, _, dur_me = run_script("market_evolution.py", "status", '{}', timeout=30)
        _me_status = get_script_status(rc_me)
        _me_info = {}
        try:
            for line in reversed(out_me.splitlines()):
                if line.strip().startswith('{'):
                    _me_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['market_evolution'] = {'status': _me_status, 'duration': round(dur_me,1),
            'experience_events': _me_info.get('total_experience_events')}
        print(f"[night_lab] 🧬 Market Evolution: {_me_status} | events={_me_info.get('total_experience_events','?')} ({dur_me:.1f}s)", flush=True)
    except Exception as e: step_results['market_evolution'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 44: Market Intelligence Full Discovery — اكتشاف البنى الانفجارية (~38s) ──
    try:
        rc_mi, out_mi, _, dur_mi = run_script("market_intelligence.py", "full_discovery", '{}', timeout=120)
        _mi_status = get_script_status(rc_mi)
        _mi_info = {}
        try:
            for line in reversed(out_mi.splitlines()):
                if line.strip().startswith('{') and '"status"' in line:
                    _mi_info = json.loads(line.strip()); break
        except Exception: pass
        _mi_expl  = (_mi_info.get('explosion_scan') or {}).get('total_explosions')
        _mi_prof  = (_mi_info.get('stock_profiles') or {}).get('n_profiled')
        step_results['market_intelligence'] = {'status': _mi_status, 'duration': round(dur_mi,1),
            'total_explosions': _mi_expl, 'n_profiled': _mi_prof}
        print(f"[night_lab] 🔬 Market Intelligence: {_mi_status} | explosions={_mi_expl} profiled={_mi_prof} ({dur_mi:.1f}s)", flush=True)
    except Exception as e: step_results['market_intelligence'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 45: Portfolio Optimizer — Kelly + Max-Sharpe + Risk Parity (~5s) ─────────
    try:
        rc_po, out_po, _, dur_po = run_script("portfolio_optimizer.py", timeout=60)
        _po_status = get_script_status(rc_po)
        _po_info = {}
        try:
            for line in reversed(out_po.splitlines()):
                if line.strip().startswith('{') and '"kelly"' in line:
                    _po_info = json.loads(line.strip()); break
        except Exception: pass
        _po_n = (_po_info.get('kelly') or {}).get('n_positions')
        step_results['portfolio_optimizer'] = {'status': _po_status, 'duration': round(dur_po,1),
            'n_positions': _po_n}
        print(f"[night_lab] 💼 Portfolio Optimizer: {_po_status} | n_positions={_po_n} ({dur_po:.1f}s)", flush=True)
    except Exception as e: step_results['portfolio_optimizer'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 46: Market OS Status — لوحة تحكم حالة السوق الكاملة (~1s) ─────────────
    try:
        rc_mos, out_mos, _, dur_mos = run_script("market_os.py", timeout=30)
        _mos_status = get_script_status(rc_mos)
        _mos_info = {}
        try:
            for line in reversed(out_mos.splitlines()):
                if line.strip().startswith('{') and '"market"' in line:
                    _mos_info = json.loads(line.strip()); break
        except Exception: pass
        _mos_regime  = (_mos_info.get('market') or {}).get('regime')
        _mos_posture = (_mos_info.get('market') or {}).get('posture')
        _mos_conf    = (_mos_info.get('market') or {}).get('confidence')
        step_results['market_os'] = {'status': _mos_status, 'duration': round(dur_mos,1),
            'regime': _mos_regime, 'posture': _mos_posture, 'confidence': _mos_conf}
        print(f"[night_lab] 🖥️  Market OS: {_mos_status} | {_mos_regime}/{_mos_posture} conf={_mos_conf} ({dur_mos:.1f}s)", flush=True)
    except Exception as e: step_results['market_os'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 47: Decision Engine — محرك القرار (~1s) ──────────────────────────────
    try:
        rc_de, out_de, _, dur_de = run_script("decision_engine.py", "decision_now", '{}', timeout=30)
        _de_status = get_script_status(rc_de)
        _de_info = {}
        try:
            for line in reversed(out_de.splitlines()):
                if line.strip().startswith('{') and '"market_decision"' in line:
                    _de_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['decision_engine'] = {'status': _de_status, 'duration': round(dur_de,1),
            'market_decision': _de_info.get('market_decision'),
            'market_regime': _de_info.get('market_regime')}
        print(f"[night_lab] ⚡ Decision Engine: {_de_status} | {_de_info.get('market_regime','?')}/{_de_info.get('market_decision','?')} ({dur_de:.1f}s)", flush=True)
    except Exception as e: step_results['decision_engine'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 48: Force Field Engine — قوى الجذب والدفع (~1s) ─────────────────────
    try:
        rc_ff, out_ff, _, dur_ff = run_script("force_field_engine.py", "force_field_now", '{}', timeout=30)
        _ff_status = get_script_status(rc_ff)
        _ff_info = {}
        try:
            for line in reversed(out_ff.splitlines()):
                if line.strip().startswith('{') and '"field_state"' in line:
                    _ff_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['force_field'] = {'status': _ff_status, 'duration': round(dur_ff,1),
            'field_state': _ff_info.get('field_state')}
        print(f"[night_lab] 🌊 Force Field: {_ff_status} | {str(_ff_info.get('field_state','?'))[:40]} ({dur_ff:.1f}s)", flush=True)
    except Exception as e: step_results['force_field'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 49: Propagation Engine — انتشار العدوى عبر السوق (~1s) ──────────────
    try:
        rc_pe, out_pe, _, dur_pe = run_script("propagation_engine.py", "propagation_now", '{}', timeout=30)
        _pe_status = get_script_status(rc_pe)
        _pe_info = {}
        try:
            for line in reversed(out_pe.splitlines()):
                if line.strip().startswith('{') and '"market_stress"' in line:
                    _pe_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['propagation'] = {'status': _pe_status, 'duration': round(dur_pe,1),
            'market_stress': _pe_info.get('market_stress')}
        print(f"[night_lab] 📡 Propagation: {_pe_status} | stress={_pe_info.get('market_stress','?')} ({dur_pe:.1f}s)", flush=True)
    except Exception as e: step_results['propagation'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 50: Market Cognition Status — ذاكرة الكوجنيشن الكاملة (~0.5s) ────────
    try:
        rc_mc, out_mc, _, dur_mc = run_script("market_cognition.py", "status", '{}', timeout=30)
        _mc_status = get_script_status(rc_mc)
        _mc_info = {}
        try:
            for line in reversed(out_mc.splitlines()):
                if line.strip().startswith('{') and '"universal_laws_p16"' in line:
                    _mc_info = json.loads(line.strip()); break
        except Exception: pass
        _mc_laws = _mc_info.get('universal_laws_p16')
        _mc_nodes = _mc_info.get('knowledge_graph_nodes')
        step_results['market_cognition'] = {'status': _mc_status, 'duration': round(dur_mc,1),
            'universal_laws': _mc_laws, 'knowledge_nodes': _mc_nodes}
        print(f"[night_lab] 🧠 Market Cognition: {_mc_status} | laws={_mc_laws} nodes={_mc_nodes} ({dur_mc:.1f}s)", flush=True)
    except Exception as e: step_results['market_cognition'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 51: Adaptive Research Loop — تقييم بقاء القوانين (~1s) ─────────────────
    try:
        rc_arl, out_arl, _, dur_arl = run_script("adaptive_research_loop.py", "assess_laws", '{}', timeout=30)
        _arl_status = get_script_status(rc_arl)
        _arl_info = {}
        try:
            for line in reversed(out_arl.splitlines()):
                if line.strip().startswith('{') and '"n_laws"' in line:
                    _arl_info = json.loads(line.strip()); break
        except Exception: pass
        _arl_dist = _arl_info.get('fitness_distribution', {})
        step_results['adaptive_research'] = {'status': _arl_status, 'duration': round(dur_arl,1),
            'n_laws': _arl_info.get('n_laws'), 'n_strong': _arl_dist.get('STRONG'), 'n_dead': _arl_dist.get('DEAD')}
        print(f"[night_lab] 🔬 Adaptive Research: {_arl_status} | laws={_arl_info.get('n_laws','?')} strong={_arl_dist.get('STRONG','?')} dead={_arl_dist.get('DEAD','?')} ({dur_arl:.1f}s)", flush=True)
    except Exception as e: step_results['adaptive_research'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 52: Refinement Cycle — دورة تشذيب وتحسين القوانين (~1s) ──────────────
    try:
        rc_rc, out_rc, _, dur_rc = run_script("refinement_cycle.py", timeout=30)
        _rc_status = get_script_status(rc_rc)
        _rc_info = {}
        try:
            for line in reversed(out_rc.splitlines()):
                if line.strip().startswith('{') and '"cycle"' in line:
                    _rc_info = json.loads(line.strip()); break
        except Exception: pass
        _rc_cycle = _rc_info.get('cycle', {})
        _rc_pruned = (_rc_cycle.get('prune') or {}).get('n_pruned', 0)
        _rc_improve = (_rc_cycle.get('condition') or {}).get('n_improvable', 0)
        step_results['refinement_cycle'] = {'status': _rc_status, 'duration': round(dur_rc,1),
            'n_pruned': _rc_pruned, 'n_improvable': _rc_improve}
        print(f"[night_lab] ✂️  Refinement Cycle: {_rc_status} | pruned={_rc_pruned} improvable={_rc_improve} ({dur_rc:.1f}s)", flush=True)
    except Exception as e: step_results['refinement_cycle'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 53: Cognitive Arbitration — تحكيم القرارات اليومية (~1s) ──────────────
    try:
        rc_ca, out_ca, _, dur_ca = run_script("cognitive_arbitration.py", "daily_decisions", '{}', timeout=30)
        _ca_status = get_script_status(rc_ca)
        _ca_info = {}
        try:
            for line in reversed(out_ca.splitlines()):
                if line.strip().startswith('{') and '"market_posture"' in line:
                    _ca_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['cognitive_arbitration'] = {'status': _ca_status, 'duration': round(dur_ca,1),
            'market_posture': _ca_info.get('market_posture'),
            'n_enter': _ca_info.get('n_enter'), 'n_avoid': _ca_info.get('n_avoid')}
        print(f"[night_lab] ⚖️  Cognitive Arbitration: {_ca_status} | posture={_ca_info.get('market_posture','?')} enter={_ca_info.get('n_enter','?')} ({dur_ca:.1f}s)", flush=True)
    except Exception as e: step_results['cognitive_arbitration'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 54: Semantic Language — الرواية العربية/الإنجليزية للسوق (~0.5s) ────────
    try:
        rc_sl, out_sl, _, dur_sl = run_script("semantic_language.py", "build_full", '{}', timeout=30)
        _sl_status = get_script_status(rc_sl)
        _sl_info = {}
        try:
            for line in reversed(out_sl.splitlines()):
                if line.strip().startswith('{') and '"archetype"' in line:
                    _sl_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['semantic_language'] = {'status': _sl_status, 'duration': round(dur_sl,1),
            'archetype': _sl_info.get('archetype'), 'risk_level': _sl_info.get('risk_level')}
        print(f"[night_lab] 💬 Semantic Language: {_sl_status} | {_sl_info.get('archetype','?')} risk={_sl_info.get('risk_level','?')} ({dur_sl:.1f}s)", flush=True)
    except Exception as e: step_results['semantic_language'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 55: Latent Engine — القوى الكامنة في هيكل السوق (~1s) ─────────────────
    try:
        rc_le, out_le, _, dur_le = run_script("latent_engine.py", "behavioral_forces", '{}', timeout=30)
        _le_status = get_script_status(rc_le)
        _le_info = {}
        try:
            for line in reversed(out_le.splitlines()):
                if line.strip().startswith('{') and '"dominant_archetype"' in line:
                    _le_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['latent_engine'] = {'status': _le_status, 'duration': round(dur_le,1),
            'dominant_archetype': _le_info.get('dominant_archetype'), 'n_stocks': _le_info.get('n_stocks')}
        print(f"[night_lab] 🔭 Latent Engine: {_le_status} | archetype={_le_info.get('dominant_archetype','?')} ({dur_le:.1f}s)", flush=True)
    except Exception as e: step_results['latent_engine'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 56: Cognitive Compression — الملخص التنفيذي المضغوط (~0.5s) ─────────
    try:
        rc_cc, out_cc, _, dur_cc = run_script("cognitive_compression.py", "market_briefing", '{}', timeout=30)
        _cc_status = get_script_status(rc_cc)
        _cc_info = {}
        try:
            for line in reversed(out_cc.splitlines()):
                if line.strip().startswith('{') and '"arabic_briefing"' in line:
                    _cc_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['cognitive_compression'] = {'status': _cc_status, 'duration': round(dur_cc,1),
            'has_briefing': bool(_cc_info.get('arabic_briefing'))}
        print(f"[night_lab] 📋 Cognitive Compression: {_cc_status} ({dur_cc:.1f}s)", flush=True)
    except Exception as e: step_results['cognitive_compression'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 57: Central Cognitive Bus — تحديث الحافلة المعرفية (~1s) ─────────────
    try:
        rc_cb, out_cb, _, dur_cb = run_script("central_cognitive_bus.py", "build_full", '{}', timeout=30)
        _cb_status = get_script_status(rc_cb)
        _cb_info = {}
        try:
            for line in reversed(out_cb.splitlines()):
                if line.strip().startswith('{') and '"directive"' in line:
                    _cb_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['cognitive_bus'] = {'status': _cb_status, 'duration': round(dur_cb,1),
            'directive': _cb_info.get('directive', '?'),
            'coherence': _cb_info.get('coherence_score', 0),
            'confidence': _cb_info.get('global_confidence', 0)}
        print(f"[night_lab] 🧠 Cognitive Bus: {_cb_status} | directive={_cb_info.get('directive','?')} coh={_cb_info.get('coherence_score',0):.0f} ({dur_cb:.1f}s)", flush=True)
    except Exception as e: step_results['cognitive_bus'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 58: Intelligence Prioritizer — تحديث التقرير الاستخباراتي اليومي (~1s) ──
    try:
        rc_ip, out_ip, _, dur_ip = run_script("intelligence_prioritizer.py", "daily_brief", '{}', timeout=30)
        _ip_status = get_script_status(rc_ip)
        _ip_info = {}
        try:
            for line in reversed(out_ip.splitlines()):
                if line.strip().startswith('{') and '"market_state"' in line:
                    _ip_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['intel_prioritizer'] = {'status': _ip_status, 'duration': round(dur_ip,1),
            'market_state': _ip_info.get('market_state', '?'),
            'dominant_force': _ip_info.get('dominant_force', '?'),
            'actionable_today': _ip_info.get('actionable_today', False)}
        print(f"[night_lab] 🎯 Intel Prioritizer: {_ip_status} | state={_ip_info.get('market_state','?')} force={str(_ip_info.get('dominant_force','?'))[:30]} ({dur_ip:.1f}s)", flush=True)
    except Exception as e: step_results['intel_prioritizer'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 59: Research Pressure Engine — منع مناطق ضغط البحث (~1s) ────────────
    try:
        rc_rp, out_rp, _, dur_rp = run_script("research_pressure_engine.py", "build_full", '{}', timeout=20)
        _rp_status = get_script_status(rc_rp)
        _rp_info = {}
        try:
            for line in reversed(out_rp.splitlines()):
                if line.strip().startswith('{') and '"n_zones"' in line:
                    _rp_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['research_pressure'] = {'status': _rp_status, 'duration': round(dur_rp,1),
            'n_zones': _rp_info.get('n_zones', 0),
            'top_pressure': _rp_info.get('top_pressure', '?')}
        print(f"[night_lab] 🔬 Research Pressure: {_rp_status} | zones={_rp_info.get('n_zones',0)} top={_rp_info.get('top_pressure','?')} ({dur_rp:.1f}s)", flush=True)
    except Exception as e: step_results['research_pressure'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 60: Causal Discovery — تحليل العلاقات السببية (~8s) ─────────────────
    try:
        rc_cd, out_cd, _, dur_cd = run_script("causal_discovery.py", "report", '{}', timeout=30)
        _cd_status = get_script_status(rc_cd)
        _cd_info = {}
        try:
            for line in reversed(out_cd.splitlines()):
                if line.strip().startswith('{') and '"granger_causality"' in line:
                    _cd_info = json.loads(line.strip()); break
        except Exception: pass
        _cd_gc = _cd_info.get('granger_causality', {})
        _cd_drivers = _cd_gc.get('causal_drivers', [])
        step_results['causal_discovery'] = {'status': _cd_status, 'duration': round(dur_cd,1),
            'n_causal_drivers': len(_cd_drivers),
            'top_driver': _cd_drivers[0] if _cd_drivers else 'none'}
        print(f"[night_lab] 🔗 Causal Discovery: {_cd_status} | drivers={_cd_drivers[:3]} ({dur_cd:.1f}s)", flush=True)
    except Exception as e: step_results['causal_discovery'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 61: Research Director Report — تقرير مدير البحث (~0.3s) ──────────────
    try:
        rc_rd, out_rd, _, dur_rd = run_script("research_director.py", "generate_report", '{}', timeout=15)
        _rd_status = get_script_status(rc_rd)
        _rd_info = {}
        try:
            for line in reversed(out_rd.splitlines()):
                if line.strip().startswith('{') and '"n_alive_strategies"' in line:
                    _rd_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['research_director'] = {'status': _rd_status, 'duration': round(dur_rd,1),
            'n_alive': _rd_info.get('n_alive_strategies', 0),
            'n_grade_s': _rd_info.get('n_grade_s', 0)}
        print(f"[night_lab] 📋 Research Director: {_rd_status} | alive={_rd_info.get('n_alive_strategies',0)} S={_rd_info.get('n_grade_s',0)} ({dur_rd:.1f}s)", flush=True)
    except Exception as e: step_results['research_director'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 62: Hypothesis DSL — توليد الفرضيات (~0.3s) ─────────────────────────
    try:
        rc_hd, out_hd, _, dur_hd = run_script("hypothesis_dsl.py", "generate", '{}', timeout=15)
        _hd_status = get_script_status(rc_hd)
        _hd_info = {}
        try:
            for line in reversed(out_hd.splitlines()):
                if line.strip().startswith('{') and '"total_hypotheses"' in line:
                    _hd_info = json.loads(line.strip()); break
        except Exception: pass
        step_results['hypothesis_dsl'] = {'status': _hd_status, 'duration': round(dur_hd,1),
            'n_inserted': _hd_info.get('n_inserted', 0),
            'total': _hd_info.get('total_hypotheses', 0)}
        print(f"[night_lab] 🧬 Hypothesis DSL: {_hd_status} | new={_hd_info.get('n_inserted',0)} total={_hd_info.get('total_hypotheses',0)} ({dur_hd:.1f}s)", flush=True)
    except Exception as e: step_results['hypothesis_dsl'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 63: Research Sandbox Report — إحصاء الفرضيات (~0.4s) ─────────────────
    try:
        rc_sb, out_sb, _, dur_sb = run_script("research_sandbox.py", "sandbox_report", '{}', timeout=15)
        _sb_info   = {}
        try: _sb_info = json.loads(out_sb)
        except Exception: pass
        _sb_status = 'ok' if rc_sb == 0 and _sb_info.get('total_hypotheses', 0) >= 0 else 'warn'
        step_results['research_sandbox'] = {'status': _sb_status, 'duration': round(dur_sb,1),
            'total': _sb_info.get('total_hypotheses', 0),
            'promoted': _sb_info.get('n_promoted', 0),
            'promotion_rate': round(_sb_info.get('promotion_rate', 0), 2)}
        print(f"[night_lab] 🧪 Research Sandbox: {_sb_status} | total={_sb_info.get('total_hypotheses',0)} promoted={_sb_info.get('n_promoted',0)} ({dur_sb:.1f}s)", flush=True)
    except Exception as e: step_results['research_sandbox'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 64: Intraday Monitor Session Status — حالة الجلسة (~0.2s) ────────────
    try:
        rc_im, out_im, _, dur_im = run_script("intraday_monitor.py", "session_status", '{}', timeout=10)
        _im_info   = {}
        try: _im_info = json.loads(out_im)
        except Exception: pass
        _im_status = 'ok' if rc_im == 0 else 'warn'
        step_results['intraday_monitor'] = {'status': _im_status, 'duration': round(dur_im,1),
            'session_phase': _im_info.get('session_phase', '?'),
            'is_open': _im_info.get('is_market_open', False),
            'cairo_time': _im_info.get('cairo_time', '?')}
        print(f"[night_lab] ⏱️  Intraday Monitor: {_im_status} | phase={_im_info.get('session_phase','?')} open={_im_info.get('is_market_open',False)} ({dur_im:.1f}s)", flush=True)
    except Exception as e: step_results['intraday_monitor'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 65: Triple Barrier Bet Sizing — تحجيم الرهانات (~1.5s) ──────────────
    try:
        rc_tb, out_tb, _, dur_tb = run_script("triple_barrier.py", "bet_sizing", '{}', timeout=20)
        _tb_info   = {}
        try: _tb_info = json.loads(out_tb)
        except Exception: pass
        _tb_status = 'ok' if rc_tb == 0 and _tb_info.get('success') else 'warn'
        step_results['triple_barrier'] = {'status': _tb_status, 'duration': round(dur_tb,1),
            'n_events': _tb_info.get('n_events', 0),
            'win_rate': round(_tb_info.get('win_rate', 0), 3),
            'payoff_ratio': round(_tb_info.get('payoff_ratio', 0), 3) if _tb_info.get('payoff_ratio') is not None else 0}
        print(f"[night_lab] 🎯 Triple Barrier: {_tb_status} | n={_tb_info.get('n_events',0)} win={_tb_info.get('win_rate',0):.1%} ({dur_tb:.1f}s)", flush=True)
    except Exception as e: step_results['triple_barrier'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 66: Pine Analytics Bridge Coverage — تغطية البيانات (~0.3s) ──────────
    try:
        rc_pa, out_pa, _, dur_pa = run_script("pine_analytics_bridge.py", "pine_data_coverage", '{}', timeout=10)
        _pa_info   = {}
        try: _pa_info = json.loads(out_pa)
        except Exception: pass
        _pa_status = 'ok' if rc_pa == 0 and _pa_info.get('success') else 'warn'
        step_results['pine_coverage'] = {'status': _pa_status, 'duration': round(dur_pa,1),
            'total_rows': _pa_info.get('total_rows', 0),
            'unique_symbols': _pa_info.get('unique_symbols', 0)}
        print(f"[night_lab] 🌲 Pine Coverage: {_pa_status} | rows={_pa_info.get('total_rows',0)} symbols={_pa_info.get('unique_symbols',0)} ({dur_pa:.1f}s)", flush=True)
    except Exception as e: step_results['pine_coverage'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 67: Explosion ML DB Check — فحص قاعدة بيانات نموذج الانفجار (~0.5s) ──
    try:
        rc_em, out_em, _, dur_em = run_script("explosion_ml.py", "check_db", '{}', timeout=15)
        _em_info   = {}
        try: _em_info = json.loads(out_em)
        except Exception: pass
        _em_status = 'ok' if rc_em == 0 and _em_info.get('success') else 'warn'
        _em_expl   = _em_info.get('explosions', {})
        _em_preds  = _em_info.get('predictions', {})
        _em_model  = _em_info.get('latest_model', {})
        step_results['explosion_ml'] = {'status': _em_status, 'duration': round(dur_em,1),
            'n_explosions': _em_expl.get('total', 0),
            'n_predictions': _em_preds.get('total', 0),
            'model_auc': round(float(_em_model.get('auc_oos', 0) or 0), 3) if _em_model else 0}
        print(f"[night_lab] 💥 Explosion ML: {_em_status} | explosions={_em_expl.get('total',0)} preds={_em_preds.get('total_predictions',0)} ({dur_em:.1f}s)", flush=True)
    except Exception as e: step_results['explosion_ml'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 68: EGX Market Analysis — تحليل السوق الشامل (~5s) ──────────────────
    try:
        rc_ea, out_ea, _, dur_ea = run_script("egx_analysis.py", "market_summary", '{}', timeout=20)
        _ea_info   = {}
        try: _ea_info = json.loads(out_ea)
        except Exception: pass
        _ea_status = 'ok' if rc_ea == 0 and _ea_info.get('symbols_count', 0) > 0 else 'warn'
        _ea_rsi    = _ea_info.get('rsi_distribution', {})
        _ea_n      = _ea_info.get('symbols_count', 0)
        _ea_obv    = _ea_info.get('obv_distribution', {})
        step_results['egx_analysis'] = {'status': _ea_status, 'duration': round(dur_ea,1),
            'n_symbols': _ea_n,
            'rsi_above70': _ea_rsi.get('>80', 0) + _ea_rsi.get('70-80', 0),
            'rsi_below30': _ea_rsi.get('<30', 0)}
        print(f"[night_lab] 📈 EGX Analysis: {_ea_status} | n={_ea_n} rsi>70={_ea_rsi.get('>80',0)+_ea_rsi.get('70-80',0)} rsi<30={_ea_rsi.get('<30',0)} ({dur_ea:.1f}s)", flush=True)
    except Exception as e: step_results['egx_analysis'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 69: Research Grid Status — حالة شبكة البحث (~3s) ────────────────────
    try:
        rc_rg, out_rg, _, dur_rg = run_script("research_grid.py", "status", '{}', timeout=15)
        _rg_info   = {}
        try: _rg_info = json.loads(out_rg)
        except Exception: pass
        _rg_status = 'ok' if rc_rg == 0 and _rg_info.get('success') else 'warn'
        step_results['research_grid'] = {'status': _rg_status, 'duration': round(dur_rg,1),
            'total': _rg_info.get('total_hypotheses', 0),
            'active': _rg_info.get('active', 0),
            'untested': _rg_info.get('untested', 0)}
        print(f"[night_lab] 🔬 Research Grid: {_rg_status} | total={_rg_info.get('total_hypotheses',0)} active={_rg_info.get('active',0)} untested={_rg_info.get('untested',0)} ({dur_rg:.1f}s)", flush=True)
    except Exception as e: step_results['research_grid'] = {'status': f'exception:{e}', 'duration': 0}

    # ── Step 70: Episodic Memory Analogy — القياس على الحالات التاريخية (~4s) ──────
    try:
        rc_ep, out_ep, _, dur_ep = run_script("episodic_memory_engine.py", "analogy_report", '{}', timeout=20)
        _ep_info   = {}
        try: _ep_info = json.loads(out_ep)
        except Exception: pass
        _ep_status = 'ok' if rc_ep == 0 and _ep_info.get('success') else 'warn'
        step_results['episodic_memory'] = {'status': _ep_status, 'duration': round(dur_ep,1),
            'analogy': _ep_info.get('analogy', ''),
            'similarity': round(float(_ep_info.get('top_similarity', 0) or 0), 3),
            'historical_outcome': (_ep_info.get('historical_outcome') or '')[:80]}
        print(f"[night_lab] 🧩 Episodic Memory: {_ep_status} | {_ep_info.get('analogy','')[:60]} ({dur_ep:.1f}s)", flush=True)
    except Exception as e: step_results['episodic_memory'] = {'status': f'exception:{e}', 'duration': 0}

    total_duration = time.time() - t0

    summary = {
        "date": today_str,
        "command": command,
        "steps": step_results,
        "total_duration_seconds": round(total_duration, 1),
        "all_ok": all(v.get('status', 'ok') == 'ok' for v in step_results.values() if isinstance(v, dict)),
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

    # ── Forward Test Outcome Update — fills close_5d/close_10d for PENDING rows ──
    # Runs daily: resolves any predictions that are ≥5 or ≥10 trading days old.
    # Seeds today's new top-15 predictions into forward_test_predictions table.
    try:
        import importlib.util as _ilu, os as _os
        _fpr_path = _os.path.join(_os.path.dirname(__file__), 'fix_prediction_reliability.py')
        _fpr_spec = _ilu.spec_from_file_location('fix_prediction_reliability', _fpr_path)
        _fpr_mod  = _ilu.module_from_spec(_fpr_spec)
        _fpr_spec.loader.exec_module(_fpr_mod)

        _fpr_conn = get_db()
        _fpr_conn.execute("PRAGMA journal_mode=WAL")
        # 1. Seed today's top predictions into forward_test_predictions
        _fpr_mod.apply_fix_d(_fpr_conn)
        # 2. Resolve any predictions old enough to have outcome data
        _fpr_mod.update_forward_test_outcomes(_fpr_conn)
        _fpr_conn.close()

        step_results['forward_test_update'] = {'status': 'ok', 'duration': 0}
        print("[night_lab] Forward test outcomes updated.", flush=True)
    except Exception as _fpr_e:
        step_results['forward_test_update'] = {'status': f'exception:{_fpr_e}', 'duration': 0}
        print(f"[night_lab] Forward test update: skipped ({_fpr_e})", flush=True)

    # ── Telegram Visual Cards ───────────────────────────────────────────────────
    # Night Lab is a research pipeline. Client delivery is owned by
    # egx_telegram_daily.mjs and its single notify.js QA/freshness gate.
    # Default is log-only; EGX_NIGHT_LAB_TELEGRAM_OK=1 is internal/manual only.
    if os.environ.get('EGX_NIGHT_LAB_TELEGRAM_OK') == '1':
        try:
            import importlib.util as _tilu, os as _tos
            _tsc_path = _tos.path.join(_tos.path.dirname(__file__), 'telegram_send_cards.py')
            _tsc_spec = _tilu.spec_from_file_location('telegram_send_cards', _tsc_path)
            _tsc_mod  = _tilu.module_from_spec(_tsc_spec)
            _tsc_spec.loader.exec_module(_tsc_mod)

            _cards_ok = _tsc_mod.send_daily_cards(today_str, dry_run=False)
            step_results['telegram_cards'] = {
                'status': 'ok' if _cards_ok else 'warn',
                'duration': 0
            }
            print(f"[night_lab] Telegram cards: {'sent' if _cards_ok else 'failed'}", flush=True)
        except Exception as _tsc_e:
            step_results['telegram_cards'] = {'status': f'exception:{_tsc_e}', 'duration': 0}
            print(f"[night_lab] Telegram cards: skipped ({_tsc_e})", flush=True)
    else:
        step_results['telegram_cards'] = {'status': 'blocked_by_delivery_policy', 'duration': 0}
        print("[night_lab] Telegram cards blocked by delivery policy.", flush=True)

    # ── Portfolio Tracker Daily Update ─────────────────────────────────────────
    # Updates prices, detects T1/T2/T3/SL hits, and takes a snapshot.
    # Telegram alerts remain blocked by default for Night Lab.
    try:
        import importlib.util as _ptilu, os as _ptos
        _pt_path = _ptos.path.join(_ptos.path.dirname(__file__), 'portfolio_tracker.py')
        _pt_spec = _ptilu.spec_from_file_location('portfolio_tracker', _pt_path)
        _pt_mod  = _ptilu.module_from_spec(_pt_spec)
        _pt_spec.loader.exec_module(_pt_mod)

        _pt_result = _pt_mod.daily_update(
            conn=None,
            send_telegram=os.environ.get('EGX_NIGHT_LAB_TELEGRAM_OK') == '1',
        )
        step_results['portfolio_tracker'] = {
            'status':           _pt_result.get('status', 'ok'),
            'n_open':           _pt_result.get('n_open', 0),
            'port_return_pct':  _pt_result.get('port_return_pct', 0),
            'alerts_sent':      _pt_result.get('alerts', 0),
        }
        _pt_open   = _pt_result.get('n_open', 0)
        _pt_ret    = _pt_result.get('port_return_pct', 0)
        _pt_alerts = _pt_result.get('alerts', 0)
        print(f"[night_lab] 💼 Portfolio: open={_pt_open} return={_pt_ret:+.2f}% alerts={_pt_alerts}",
              flush=True)
    except Exception as _pt_e:
        step_results['portfolio_tracker'] = {'status': f'exception:{_pt_e}'}
        print(f"[night_lab] Portfolio tracker: skipped ({_pt_e})", flush=True)

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
    today_str = datetime.now().strftime('%Y-%m-%d')

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

    # ── Ph83: Historical Signal Reconstruction (MOVED before predict_ensemble) ─────
    # Must run BEFORE predict_ensemble so get_recent_losers() uses fresh
    # hist_backtest_signals for the Phase 5 recent-failure-memory penalty.
    # (2026-05-23: moved from after predict_ensemble to before it)
    print("[weekly] 🔄 Ph83 — Historical Signal Reconstruction (pre-predict)...", flush=True)
    try:
        rc83, out83, _, dur83 = run_script(
            "historical_signal_reconstructor.py", "build",
            '{"months":12}', timeout=600,
        )
        _ph83_status = get_script_status(rc83)
        _ph83_info = {}
        try:
            for line in reversed(out83.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_signals"' in line:
                    _ph83_info = json.loads(line)
                    break
        except Exception:
            pass
        results['ph83_hist_signals'] = {
            'status': _ph83_status,
            'duration': round(dur83, 1),
            'n_signals': _ph83_info.get('n_signals'),
        }
        print(f"[weekly] Ph83: {_ph83_status} signals={_ph83_info.get('n_signals','?')} ({dur83:.0f}s)", flush=True)
    except Exception as e83:
        results['ph83_hist_signals'] = {'status': f'exception:{e83}', 'duration': 0}
        print(f"[weekly] Ph83: skipped ({e83})", flush=True)

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

    # ── Ph79: Real Backtest (weekly validation) ──────────────────────────────────
    print("[weekly] 📊 Ph79 — Real Price Backtest Validation...", flush=True)
    try:
        rc79, out79, _, dur79 = run_script(
            "backtest_engine.py", "run",
            '{"days":180}', timeout=120,
        )
        _ph79_status = get_script_status(rc79)
        _ph79_info = {}
        try:
            for line in reversed(out79.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"win_rate"' in line:
                    _ph79_info = json.loads(line)
                    break
        except Exception:
            pass
        results['ph79_backtest'] = {
            'status': _ph79_status,
            'duration': round(dur79, 1),
            'win_rate': _ph79_info.get('win_rate'),
            'profit_factor': _ph79_info.get('profit_factor'),
        }
        _wr = _ph79_info.get('win_rate')
        _pf = _ph79_info.get('profit_factor')
        _wr_str = f"{_wr:.1%}" if isinstance(_wr, (int, float)) else str(_wr or '?')
        _pf_str = f"{_pf:.2f}" if isinstance(_pf, (int, float)) else str(_pf or '?')
        print(f"[weekly] Ph79: {_ph79_status} WR={_wr_str} PF={_pf_str} ({dur79:.0f}s)", flush=True)
    except Exception as e79:
        results['ph79_backtest'] = {'status': f'exception:{e79}', 'duration': 0}
        print(f"[weekly] Ph79: skipped ({e79})", flush=True)

    # ── Ph78: Institutional Metrics (weekly deep scorecard) ──────────────────────
    print("[weekly] 📊 Ph78 — Institutional Metrics (deep)...", flush=True)
    try:
        rc78, out78, _, dur78 = run_script(
            "institutional_metrics.py", "--json", "--days", "180", timeout=60,
        )
        _ph78_status = get_script_status(rc78)
        _ph78_info = {}
        try:
            _out78_stripped = out78.strip()
            if _out78_stripped.startswith('{'):
                _ph78_info = json.loads(_out78_stripped)
        except Exception:
            pass
        results['ph78_scorecard'] = {
            'status': _ph78_status,
            'duration': round(dur78, 1),
            'grade': _ph78_info.get('institutional_grade', 'N/A'),
            'sharpe': _ph78_info.get('sharpe'),
        }
        print(f"[weekly] Ph78: {_ph78_status} Grade={_ph78_info.get('institutional_grade','?')} Sharpe={_ph78_info.get('sharpe','?')} ({dur78:.0f}s)", flush=True)
    except Exception as e78:
        results['ph78_scorecard'] = {'status': f'exception:{e78}', 'duration': 0}
        print(f"[weekly] Ph78: skipped ({e78})", flush=True)

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

    # ── Multi-Horizon Engine (weekly deep) ──────────────────────────────────────
    print("[weekly] 🎯 Multi-Horizon Engine — 5 forecast horizons...", flush=True)
    try:
        rc_mh, out_mh, _, dur_mh = run_script(
            "multi_horizon_engine.py", "build_full", '{}', timeout=120,
        )
        _mh_status = get_script_status(rc_mh)
        _mh_info = {}
        try:
            for line in reversed(out_mh.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_horizons"' in line:
                    _mh_info = json.loads(line)
                    break
        except Exception:
            pass
        results['multi_horizon'] = {
            'status': _mh_status,
            'duration': round(dur_mh, 1),
            'n_horizons': _mh_info.get('n_horizons', 5),
        }
        print(f"[weekly] Multi-Horizon: {_mh_status} ({dur_mh:.0f}s)", flush=True)
    except Exception as e_mh:
        results['multi_horizon'] = {'status': f'exception:{e_mh}', 'duration': 0}
        print(f"[weekly] Multi-Horizon: skipped ({e_mh})", flush=True)

    # ── Hidden Regime HMM (weekly deep) ─────────────────────────────────────────
    print("[weekly] 🔮 Hidden Regime HMM — latent state detection...", flush=True)
    try:
        rc_hmm, out_hmm, _, dur_hmm = run_script(
            "hidden_regime_hmm.py", "detect", '{}', timeout=120,
        )
        _hmm_status = get_script_status(rc_hmm)
        _hmm_info = {}
        try:
            for line in reversed(out_hmm.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"hidden_state"' in line:
                    _hmm_info = json.loads(line)
                    break
        except Exception:
            pass
        results['hidden_regime_hmm'] = {
            'status': _hmm_status,
            'duration': round(dur_hmm, 1),
            'hidden_state': _hmm_info.get('hidden_state'),
        }
        _hs = _hmm_info.get('hidden_state', '?')
        print(f"[weekly] HMM: {_hmm_status} | state={_hs} ({dur_hmm:.0f}s)", flush=True)
    except Exception as e_hmm:
        results['hidden_regime_hmm'] = {'status': f'exception:{e_hmm}', 'duration': 0}
        print(f"[weekly] Hidden Regime HMM: skipped ({e_hmm})", flush=True)

    # ── Realistic Backtest (weekly — full cost-adjusted OOS validation) ──────────
    print("[weekly] 📊 Realistic Backtest — cost-adjusted OOS validation...", flush=True)
    try:
        rc_rb, out_rb, _, dur_rb = run_script(
            "realistic_backtest.py", "build_full", '{}', timeout=120,
        )
        _rb_status = get_script_status(rc_rb)
        _rb_info = {}
        try:
            for line in reversed(out_rb.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"oos_verdict"' in line:
                    _rb_info = json.loads(line)
                    break
        except Exception:
            pass
        results['realistic_backtest'] = {
            'status': _rb_status,
            'duration': round(dur_rb, 1),
            'oos_verdict': _rb_info.get('oos_verdict', 'N/A'),
        }
        _rb_v = (_rb_info.get('oos_verdict') or 'N/A')[:20]
        print(f"[weekly] Realistic BT: {_rb_status} | {_rb_v} ({dur_rb:.0f}s)", flush=True)
    except Exception as e_rb:
        results['realistic_backtest'] = {'status': f'exception:{e_rb}', 'duration': 0}
        print(f"[weekly] Realistic Backtest: skipped ({e_rb})", flush=True)

    # ── Market Intelligence Discovery (weekly refresh) ─────────────────────────
    print("[weekly] 🔬 Market Intelligence — stock profiles + precursors...", flush=True)
    try:
        rc_mi, out_mi, _, dur_mi = run_script(
            "market_intelligence.py", "full_discovery", '{}', timeout=300,
        )
        _mi_status = get_script_status(rc_mi)
        _mi_info = {}
        try:
            for line in reversed(out_mi.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"stock_profiles"' in line:
                    _mi_info = json.loads(line)
                    break
        except Exception:
            pass
        results['market_intelligence'] = {
            'status': _mi_status,
            'duration': round(dur_mi, 1),
            'stock_profiles': _mi_info.get('stock_profiles', 0),
        }
        _mi_n = _mi_info.get('stock_profiles', '?')
        print(f"[weekly] Market Intelligence: {_mi_status} | profiles={_mi_n} ({dur_mi:.0f}s)", flush=True)
    except Exception as e_mi:
        results['market_intelligence'] = {'status': f'exception:{e_mi}', 'duration': 0}
        print(f"[weekly] Market Intelligence: skipped ({e_mi})", flush=True)

    # ── Historical Validation (weekly full review) ────────────────────────────
    print("[weekly] 📜 Historical Validation — law + precursor validation...", flush=True)
    try:
        rc_hv, out_hv, _, dur_hv = run_script(
            "historical_validation.py", "full_historical_validation", '{}', timeout=120,
        )
        _hv_status = get_script_status(rc_hv)
        _hv_info = {}
        try:
            for line in reversed(out_hv.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_validated"' in line:
                    _hv_info = json.loads(line)
                    break
        except Exception:
            pass
        results['historical_validation'] = {
            'status': _hv_status,
            'duration': round(dur_hv, 1),
            'n_validated': _hv_info.get('n_validated', 0),
        }
        print(f"[weekly] Historical Validation: {_hv_status} ({dur_hv:.0f}s)", flush=True)
    except Exception as e_hv:
        results['historical_validation'] = {'status': f'exception:{e_hv}', 'duration': 0}
        print(f"[weekly] Historical Validation: skipped ({e_hv})", flush=True)

    # ── Walk-Forward Lab (weekly — Monte Carlo + param stability) ─────────────
    print("[weekly] 🔬 Walk-Forward Lab — Monte Carlo validation...", flush=True)
    try:
        rc_wf, out_wf, _, dur_wf = run_script(
            "walk_forward_lab.py", "report", '{}', timeout=60,
        )
        _wf_status = get_script_status(rc_wf)
        results['walk_forward_lab'] = {'status': _wf_status, 'duration': round(dur_wf, 1)}
        print(f"[weekly] Walk-Forward Lab: {_wf_status} ({dur_wf:.0f}s)", flush=True)
    except Exception as e_wf:
        results['walk_forward_lab'] = {'status': f'exception:{e_wf}', 'duration': 0}
        print(f"[weekly] Walk-Forward Lab: skipped ({e_wf})", flush=True)

    # ── Intraday Intelligence (weekly session profile refresh) ────────────────
    print("[weekly] ⏰ Intraday Intelligence — session profiles...", flush=True)
    try:
        rc_ii, out_ii, _, dur_ii = run_script(
            "intraday_intelligence.py", "build_full", '{}', timeout=60,
        )
        _ii_status = get_script_status(rc_ii)
        results['intraday_intelligence'] = {'status': _ii_status, 'duration': round(dur_ii, 1)}
        print(f"[weekly] Intraday Intelligence: {_ii_status} ({dur_ii:.0f}s)", flush=True)
    except Exception as e_ii:
        results['intraday_intelligence'] = {'status': f'exception:{e_ii}', 'duration': 0}
        print(f"[weekly] Intraday Intelligence: skipped ({e_ii})", flush=True)

    # ── Market DNA Engine — بناء بصمة كل سهم (أسبوعي) ──────────────────────
    print("[weekly] 🧬 Market DNA — building stock archetypes...", flush=True)
    try:
        rc_dna, out_dna, _, dur_dna = run_script(
            "market_dna_engine.py", "build_dna", '{}', timeout=120,
        )
        _dna_status = get_script_status(rc_dna)
        _dna_info = {}
        try:
            for line in reversed(out_dna.splitlines()):
                line = line.strip()
                if line.startswith('{') and '"n_built"' in line:
                    _dna_info = json.loads(line)
                    break
        except Exception:
            pass
        results['market_dna'] = {
            'status':   _dna_status,
            'duration': round(dur_dna, 1),
            'n_built':  _dna_info.get('n_built'),
        }
        print(f"[weekly] Market DNA build: {_dna_status} | n_built={_dna_info.get('n_built','?')} ({dur_dna:.0f}s)", flush=True)

        # sector_dna_refresh after build_dna
        if _dna_status == 'ok':
            rc_sdr, out_sdr, _, dur_sdr = run_script(
                "market_dna_engine.py", "sector_dna_refresh", '{}', timeout=60,
            )
            results['market_dna_sector'] = {
                'status':   get_script_status(rc_sdr),
                'duration': round(dur_sdr, 1),
            }
            print(f"[weekly] Market DNA sector refresh: {results['market_dna_sector']['status']} ({dur_sdr:.0f}s)", flush=True)
    except Exception as e_dna:
        results['market_dna'] = {'status': f'exception:{e_dna}', 'duration': 0}
        print(f"[weekly] Market DNA: skipped ({e_dna})", flush=True)

    # ── Law Synthesis — تحليل قوانين التداول العميق (أسبوعي) ─────────────────
    print("[weekly] ⚗️ Law Synthesis — counterfactual analysis...", flush=True)
    try:
        rc_ls, out_ls, _, dur_ls = run_script(
            "law_synthesis.py", "counterfactuals", '{}', timeout=120,
        )
        _ls_status = get_script_status(rc_ls)
        _ls_info = {}
        try:
            for line in reversed(out_ls.splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    _ls_info = json.loads(line)
                    break
        except Exception:
            pass
        results['law_synthesis'] = {
            'status':   _ls_status,
            'duration': round(dur_ls, 1),
            'n_laws':   _ls_info.get('n_laws_tested', _ls_info.get('n_laws')),
        }
        print(f"[weekly] Law Synthesis: {_ls_status} | n_laws={results['law_synthesis'].get('n_laws','?')} ({dur_ls:.0f}s)", flush=True)
    except Exception as e_ls:
        results['law_synthesis'] = {'status': f'exception:{e_ls}', 'duration': 0}
        print(f"[weekly] Law Synthesis: skipped ({e_ls})", flush=True)

    # ── Feature Factory — بناء مستودع الميزات الكامل (أسبوعي) ───────────────
    print("[weekly] 🏗️  Feature Factory — building all features...", flush=True)
    try:
        rc_ff, out_ff, _, dur_ff = run_script("feature_factory.py", "build_full", '{}', timeout=120)
        _ff_status = get_script_status(rc_ff)
        _ff_info = {}
        try:
            for line in reversed(out_ff.splitlines()):
                if line.strip().startswith('{'):
                    _ff_info = json.loads(line.strip()); break
        except Exception: pass
        _ff_bf = _ff_info.get('build_features', {})
        _ff_n  = _ff_bf.get('symbols_processed') if isinstance(_ff_bf, dict) else None
        results['feature_factory'] = {'status': _ff_status, 'duration': round(dur_ff,1), 'n_symbols': _ff_n}
        print(f"[weekly] Feature Factory: {_ff_status} | n_symbols={_ff_n or '?'} ({dur_ff:.0f}s)", flush=True)
    except Exception as e_ff:
        results['feature_factory'] = {'status': f'exception:{e_ff}', 'duration': 0}
        print(f"[weekly] Feature Factory: skipped ({e_ff})", flush=True)

    # ── Feature Store — تحديث مستودع الميزات (أسبوعي) ──────────────────────
    print("[weekly] 💾 Feature Store — refreshing feature store...", flush=True)
    try:
        rc_fst, out_fst, _, dur_fst = run_script("feature_store.py", "refresh", '{}', timeout=60)
        _fst_status = get_script_status(rc_fst)
        _fst_info = {}
        try:
            _fst_raw = out_fst.strip()
            _fst_info = json.loads(_fst_raw) if _fst_raw.startswith('{') else {}
        except Exception: pass
        results['feature_store'] = {'status': _fst_status, 'duration': round(dur_fst,1),
            'n_symbols': _fst_info.get('n_symbols'), 'n_features': _fst_info.get('n_features')}
        print(f"[weekly] Feature Store: {_fst_status} | syms={_fst_info.get('n_symbols','?')} feats={_fst_info.get('n_features','?')} ({dur_fst:.0f}s)", flush=True)
    except Exception as e_fst:
        results['feature_store'] = {'status': f'exception:{e_fst}', 'duration': 0}
        print(f"[weekly] Feature Store: skipped ({e_fst})", flush=True)

    # ── Unified Market Graph — الرسم البياني الموحد للسوق (أسبوعي) ──────────
    print("[weekly] 🕸️  Unified Market Graph — building correlation graph...", flush=True)
    try:
        rc_umg, out_umg, _, dur_umg = run_script("unified_market_graph.py", "build_full", '{}', timeout=120)
        _umg_status = get_script_status(rc_umg)
        _umg_info = {}
        try:
            for line in reversed(out_umg.splitlines()):
                if line.strip().startswith('{'):
                    _umg_info = json.loads(line.strip()); break
        except Exception: pass
        results['unified_market_graph'] = {'status': _umg_status, 'duration': round(dur_umg,1),
            'n_nodes': _umg_info.get('n_nodes'), 'n_edges': _umg_info.get('n_edges')}
        print(f"[weekly] Market Graph: {_umg_status} | nodes={_umg_info.get('n_nodes','?')} edges={_umg_info.get('n_edges','?')} ({dur_umg:.0f}s)", flush=True)
    except Exception as e_umg:
        results['unified_market_graph'] = {'status': f'exception:{e_umg}', 'duration': 0}
        print(f"[weekly] Market Graph: skipped ({e_umg})", flush=True)

    # ── Meta-Learning Engine — التعلم من تجارب النظام (أسبوعي) ──────────────
    print("[weekly] 🧪 Meta-Learning — analyzing hypotheses...", flush=True)
    try:
        rc_ml, out_ml, _, dur_ml = run_script("meta_learning_engine.py", "build_full", '{}', timeout=60)
        _ml2_status = get_script_status(rc_ml)
        _ml2_info = {}
        try:
            for line in reversed(out_ml.splitlines()):
                if line.strip().startswith('{'):
                    _ml2_info = json.loads(line.strip()); break
        except Exception: pass
        results['meta_learning'] = {'status': _ml2_status, 'duration': round(dur_ml,1),
            'n_directives': _ml2_info.get('meta_directives', {}).get('n_directives') if isinstance(_ml2_info.get('meta_directives'), dict) else None}
        print(f"[weekly] Meta-Learning: {_ml2_status} ({dur_ml:.0f}s)", flush=True)
    except Exception as e_ml:
        results['meta_learning'] = {'status': f'exception:{e_ml}', 'duration': 0}
        print(f"[weekly] Meta-Learning: skipped ({e_ml})", flush=True)

    # ── Statistical Grounding — الدرجات الإحصائية للقوانين (أسبوعي) ─────────
    print("[weekly] 📐 Statistical Grounding — grading all laws...", flush=True)
    try:
        rc_sg, out_sg, _, dur_sg = run_script("statistical_grounding.py", "build_full", '{}', timeout=60)
        _sg_status = get_script_status(rc_sg)
        _sg_info = {}
        try:
            for line in reversed(out_sg.splitlines()):
                if line.strip().startswith('{'):
                    _sg_info = json.loads(line.strip()); break
        except Exception: pass
        _sg_gr = _sg_info.get('grading', {})
        results['statistical_grounding'] = {'status': _sg_status, 'duration': round(dur_sg,1),
            'n_graded': _sg_gr.get('n_graded') if isinstance(_sg_gr, dict) else None}
        print(f"[weekly] Statistical Grounding: {_sg_status} | n_graded={_sg_gr.get('n_graded','?') if isinstance(_sg_gr, dict) else '?'} ({dur_sg:.0f}s)", flush=True)
    except Exception as e_sg:
        results['statistical_grounding'] = {'status': f'exception:{e_sg}', 'duration': 0}
        print(f"[weekly] Statistical Grounding: skipped ({e_sg})", flush=True)

    # ── Graph Contagion Engine — محاكاة العدوى والانتشار (أسبوعي) ────────────
    print("[weekly] 🦠 Graph Contagion — building contagion network...", flush=True)
    try:
        rc_gcon, out_gcon, _, dur_gcon = run_script("graph_contagion_engine.py", "build_network", '{}', timeout=120)
        _gcon_status = get_script_status(rc_gcon)
        _gcon_info = {}
        try:
            for line in reversed(out_gcon.splitlines()):
                if line.strip().startswith('{'):
                    _gcon_info = json.loads(line.strip()); break
        except Exception: pass
        results['graph_contagion'] = {'status': _gcon_status, 'duration': round(dur_gcon,1),
            'n_stocks': _gcon_info.get('n_stocks'), 'n_edges': _gcon_info.get('n_edges')}
        print(f"[weekly] Graph Contagion: {_gcon_status} | stocks={_gcon_info.get('n_stocks','?')} ({dur_gcon:.0f}s)", flush=True)
    except Exception as e_gcon:
        results['graph_contagion'] = {'status': f'exception:{e_gcon}', 'duration': 0}
        print(f"[weekly] Graph Contagion: skipped ({e_gcon})", flush=True)

    # ── Portfolio Cognition — بناء المحفظة الذكية (أسبوعي) ──────────────────
    print("[weekly] 💼 Portfolio Cognition — building optimal portfolio...", flush=True)
    try:
        rc_pc, out_pc, _, dur_pc = run_script("portfolio_cognition.py", "build_full", '{}', timeout=60)
        _pc_status = get_script_status(rc_pc)
        _pc_info = {}
        try:
            for line in reversed(out_pc.splitlines()):
                if line.strip().startswith('{'):
                    _pc_info = json.loads(line.strip()); break
        except Exception: pass
        results['portfolio_cognition'] = {'status': _pc_status, 'duration': round(dur_pc,1),
            'capital': _pc_info.get('capital'), 'n_positions': _pc_info.get('n_positions')}
        print(f"[weekly] Portfolio Cognition: {_pc_status} | capital={_pc_info.get('capital','?')} ({dur_pc:.0f}s)", flush=True)
    except Exception as e_pc:
        results['portfolio_cognition'] = {'status': f'exception:{e_pc}', 'duration': 0}
        print(f"[weekly] Portfolio Cognition: skipped ({e_pc})", flush=True)

    # ── Deep History Engine — تحليل التاريخ العميق (أسبوعي) ─────────────────
    print("[weekly] 📜 Deep History — long-term regime analysis...", flush=True)
    try:
        rc_dh, out_dh, _, dur_dh = run_script("deep_history_engine.py", "build_full", '{}', timeout=60)
        _dh_status = get_script_status(rc_dh)
        _dh_info = {}
        try:
            for line in reversed(out_dh.splitlines()):
                if line.strip().startswith('{'):
                    _dh_info = json.loads(line.strip()); break
        except Exception: pass
        results['deep_history'] = {'status': _dh_status, 'duration': round(dur_dh,1),
            'regime': _dh_info.get('regime'), 'n_patterns': _dh_info.get('n_patterns')}
        print(f"[weekly] Deep History: {_dh_status} | regime={_dh_info.get('regime','?')} ({dur_dh:.0f}s)", flush=True)
    except Exception as e_dh:
        results['deep_history'] = {'status': f'exception:{e_dh}', 'duration': 0}
        print(f"[weekly] Deep History: skipped ({e_dh})", flush=True)

    # ── Causal Discovery Engine — اكتشاف العلاقات السببية (أسبوعي) ───────────
    print("[weekly] 🔬 Causal Discovery — transfer entropy analysis...", flush=True)
    try:
        rc_cd, out_cd, _, dur_cd = run_script("causal_discovery_engine.py", "build_full", '{}', timeout=120)
        _cd_status = get_script_status(rc_cd)
        _cd_info = {}
        try:
            for line in reversed(out_cd.splitlines()):
                if line.strip().startswith('{'):
                    _cd_info = json.loads(line.strip()); break
        except Exception: pass
        _cd_te = _cd_info.get('transfer_entropy', {})
        results['causal_discovery'] = {'status': _cd_status, 'duration': round(dur_cd,1),
            'n_links': _cd_te.get('n_links_found') if isinstance(_cd_te, dict) else None}
        print(f"[weekly] Causal Discovery: {_cd_status} | links={_cd_te.get('n_links_found','?') if isinstance(_cd_te, dict) else '?'} ({dur_cd:.0f}s)", flush=True)
    except Exception as e_cd:
        results['causal_discovery'] = {'status': f'exception:{e_cd}', 'duration': 0}
        print(f"[weekly] Causal Discovery: skipped ({e_cd})", flush=True)

    # ── Regime Laws — تحليل القوانين المشروطة بالنظام (أسبوعي) ──────────────
    print("[weekly] 📋 Regime Laws — conditioning laws by market regime...", flush=True)
    try:
        rc_rl, out_rl, _, dur_rl = run_script("regime_laws.py", "analyze_conditions", '{}', timeout=60)
        _rl_status = get_script_status(rc_rl)
        _rl_info = {}
        try:
            for line in reversed(out_rl.splitlines()):
                if line.strip().startswith('{'):
                    _rl_info = json.loads(line.strip()); break
        except Exception: pass
        results['regime_laws'] = {'status': _rl_status, 'duration': round(dur_rl,1),
            'total_laws': _rl_info.get('total_laws'),
            'laws_improved': _rl_info.get('laws_improved_by_conditioning')}
        print(f"[weekly] Regime Laws: {_rl_status} | total={_rl_info.get('total_laws','?')} improved={_rl_info.get('laws_improved_by_conditioning','?')} ({dur_rl:.0f}s)", flush=True)
    except Exception as e_rl:
        results['regime_laws'] = {'status': f'exception:{e_rl}', 'duration': 0}
        print(f"[weekly] Regime Laws: skipped ({e_rl})", flush=True)

    # ── Genetic Strategy Evolution — التطور الجيني للاستراتيجيات (أسبوعي) ────
    print("[weekly] 🧬 Genetic Evolution — evolving trading strategies...", flush=True)
    try:
        _gse_params = json.dumps({'pop_size': 30, 'n_generations': 10})
        rc_gse, out_gse, _, dur_gse = run_script(
            "genetic_strategy_evolution.py", "evolve", _gse_params, timeout=120)
        _gse_status = get_script_status(rc_gse)
        _gse_info = {}
        try:
            for line in reversed(out_gse.splitlines()):
                if line.strip().startswith('{'):
                    _gse_info = json.loads(line.strip()); break
        except Exception: pass
        results['genetic_evolution'] = {'status': _gse_status, 'duration': round(dur_gse,1),
            'best_fitness': _gse_info.get('best_fitness'),
            'n_generations': _gse_info.get('n_generations')}
        _gse_f = _gse_info.get('best_fitness', '?')
        print(f"[weekly] Genetic Evolution: {_gse_status} | best_fitness={_gse_f} ({dur_gse:.0f}s)", flush=True)
    except Exception as e_gse:
        results['genetic_evolution'] = {'status': f'exception:{e_gse}', 'duration': 0}
        print(f"[weekly] Genetic Evolution: skipped ({e_gse})", flush=True)

    print("[weekly] 🔬 Market Intelligence — full discovery (explosions, archetypes, laws)...", flush=True)
    try:
        rc_mi2, out_mi2, _, dur_mi2 = run_script(
            "market_intelligence.py", "full_discovery", '{}', timeout=180)
        _mi2_status = get_script_status(rc_mi2)
        _mi2_info = {}
        try:
            for line in reversed(out_mi2.splitlines()):
                if line.strip().startswith('{') and '"status"' in line:
                    _mi2_info = json.loads(line.strip()); break
        except Exception: pass
        _mi2_expl = (_mi2_info.get('explosion_scan') or {}).get('total_explosions')
        _mi2_laws = (_mi2_info.get('knowledge_update') or {}).get('laws_generated')
        results['market_intelligence'] = {'status': _mi2_status, 'duration': round(dur_mi2,1),
            'total_explosions': _mi2_expl, 'laws_generated': _mi2_laws}
        print(f"[weekly] Market Intelligence: {_mi2_status} | explosions={_mi2_expl} laws={_mi2_laws} ({dur_mi2:.0f}s)", flush=True)
    except Exception as e_mi2:
        results['market_intelligence'] = {'status': f'exception:{e_mi2}', 'duration': 0}
        print(f"[weekly] Market Intelligence: skipped ({e_mi2})", flush=True)

    print("[weekly] 📈 Strategy Tester — backtesting signal strategies...", flush=True)
    try:
        rc_st, out_st, _, dur_st = run_script(
            "strategy_tester.py", "build_full", '{}', timeout=120)
        _st_status = get_script_status(rc_st)
        _st_info = {}
        try:
            for line in reversed(out_st.splitlines()):
                if line.strip().startswith('{'):
                    _st_info = json.loads(line.strip()); break
        except Exception: pass
        results['strategy_tester'] = {'status': _st_status, 'duration': round(dur_st,1),
            'sharpe': _st_info.get('sharpe')}
        print(f"[weekly] Strategy Tester: {_st_status} | sharpe={_st_info.get('sharpe','?')} ({dur_st:.0f}s)", flush=True)
    except Exception as e_st:
        results['strategy_tester'] = {'status': f'exception:{e_st}', 'duration': 0}
        print(f"[weekly] Strategy Tester: skipped ({e_st})", flush=True)

    print("[weekly] 🧪 Event Backtest — testing trading laws on historical events...", flush=True)
    try:
        rc_eb, out_eb, _, dur_eb = run_script(
            "event_backtest.py", "report", '{}', timeout=60)
        _eb_status = get_script_status(rc_eb)
        _eb_info = {}
        try:
            for line in reversed(out_eb.splitlines()):
                if line.strip().startswith('{'):
                    _eb_info = json.loads(line.strip()); break
        except Exception: pass
        results['event_backtest'] = {'status': _eb_status, 'duration': round(dur_eb,1),
            'laws_tested': _eb_info.get('already_tested_count'),
            'top_law': (_eb_info.get('top_law_strategy') or {}).get('law_name') if isinstance(_eb_info.get('top_law_strategy'), dict) else None}
        print(f"[weekly] Event Backtest: {_eb_status} | laws_tested={_eb_info.get('already_tested_count','?')} ({dur_eb:.0f}s)", flush=True)
    except Exception as e_eb:
        results['event_backtest'] = {'status': f'exception:{e_eb}', 'duration': 0}
        print(f"[weekly] Event Backtest: skipped ({e_eb})", flush=True)

    # ── Weekly: Central Cognitive Bus refresh ───────────────────────────────────
    try:
        rc_cb_w, out_cb_w, _, dur_cb_w = run_script("central_cognitive_bus.py", "build_full", '{}', timeout=30)
        _cb_w_status = get_script_status(rc_cb_w)
        _cb_w_info = {}
        try:
            for line in reversed(out_cb_w.splitlines()):
                if line.strip().startswith('{') and '"directive"' in line:
                    _cb_w_info = json.loads(line.strip()); break
        except Exception: pass
        results['cognitive_bus'] = {'status': _cb_w_status, 'duration': round(dur_cb_w,1),
            'directive': _cb_w_info.get('directive', '?')}
        print(f"[weekly] 🧠 Cognitive Bus: {_cb_w_status} | directive={_cb_w_info.get('directive','?')} ({dur_cb_w:.1f}s)", flush=True)
    except Exception as e_cb_w:
        results['cognitive_bus'] = {'status': f'exception:{e_cb_w}', 'duration': 0}

    # ── Weekly: Intelligence Prioritizer ─────────────────────────────────────────
    try:
        rc_ip_w, out_ip_w, _, dur_ip_w = run_script("intelligence_prioritizer.py", "daily_brief", '{}', timeout=30)
        _ip_w_status = get_script_status(rc_ip_w)
        _ip_w_info = {}
        try:
            for line in reversed(out_ip_w.splitlines()):
                if line.strip().startswith('{') and '"market_state"' in line:
                    _ip_w_info = json.loads(line.strip()); break
        except Exception: pass
        results['intel_prioritizer'] = {'status': _ip_w_status, 'duration': round(dur_ip_w,1),
            'market_state': _ip_w_info.get('market_state', '?')}
        print(f"[weekly] 🎯 Intel Prioritizer: {_ip_w_status} | state={_ip_w_info.get('market_state','?')} ({dur_ip_w:.1f}s)", flush=True)
    except Exception as e_ip_w:
        results['intel_prioritizer'] = {'status': f'exception:{e_ip_w}', 'duration': 0}

    # ── Weekly: Research Director Morning Run — الجولة البحثية الأسبوعية (~600s) ──
    print("[weekly] 🔬 Research Director morning_run — full research cycle...", flush=True)
    try:
        rc_rdw, out_rdw, _, dur_rdw = run_script("research_director.py", "morning_run", '{}', timeout=720)
        _rdw_status = get_script_status(rc_rdw)
        _rdw_info = {}
        try:
            for line in reversed(out_rdw.splitlines()):
                if line.strip().startswith('{'):
                    _rdw_info = json.loads(line.strip()); break
        except Exception: pass
        results['research_director_run'] = {'status': _rdw_status, 'duration': round(dur_rdw,1),
            'new_alpha': _rdw_info.get('new_alpha_found', 0),
            'n_evolved': _rdw_info.get('hypotheses_evolved', 0),
            'top_grade': _rdw_info.get('top_grade', '?')}
        print(f"[weekly] Research Director: {_rdw_status} | new_alpha={_rdw_info.get('new_alpha_found',0)} evolved={_rdw_info.get('hypotheses_evolved',0)} ({dur_rdw:.0f}s)", flush=True)
    except Exception as e_rdw:
        results['research_director_run'] = {'status': f'exception:{e_rdw}', 'duration': 0}
        print(f"[weekly] Research Director: skipped ({e_rdw})", flush=True)

    # ── Weekly: MLflow Experiment Report ─────────────────────────────────────────
    try:
        _mlrc, _mlout, _, _mldur = run_script("mlflow_tracker.py", "report", '{}', timeout=30)
        _mls = get_script_status(_mlrc)
        _mlinf = {}
        try: _mlinf = json.loads(_mlout)
        except Exception: pass
        results['mlflow_report'] = {'status': _mls, 'duration': round(_mldur,1),
            'n_experiments': len(_mlinf.get('init', {}).get('experiments', {})) if isinstance(_mlinf.get('init'), dict) else 0}
        print(f"[weekly] MLflow Report: {_mls} ({_mldur:.1f}s)", flush=True)
    except Exception as _me:
        results['mlflow_report'] = {'status': f'exception:{_me}', 'duration': 0}

    # ── Weekly: Explosion ML Feature Importance (~10s) ───────────────────────────
    try:
        _emwrc, _emwout, _, _emwdur = run_script("explosion_ml.py", "feature_importance", '{}', timeout=30)
        _emws = get_script_status(_emwrc)
        _emwinf = {}
        try: _emwinf = json.loads(_emwout)
        except Exception: pass
        results['explosion_ml_fi'] = {'status': _emws, 'duration': round(_emwdur,1),
            'n_features': _emwinf.get('n_features', 0),
            'top_feature': (_emwinf.get('features') or [{}])[0].get('feature', '') if _emwinf.get('features') else ''}
        print(f"[weekly] Explosion ML Feature Importance: {_emws} | n={_emwinf.get('n_features',0)} top={results['explosion_ml_fi']['top_feature']} ({_emwdur:.1f}s)", flush=True)
    except Exception as _emwe:
        results['explosion_ml_fi'] = {'status': f'exception:{_emwe}', 'duration': 0}

    # ── Weekly: Research Grid Full Run (~300s) ────────────────────────────────────
    try:
        _rgwrc, _rgwout, _, _rgwdur = run_script("research_grid.py", "run_grid",
            json.dumps({"max_hypotheses": 5}), timeout=360)
        _rgws = get_script_status(_rgwrc)
        _rgwinf = {}
        try: _rgwinf = json.loads(_rgwout)
        except Exception: pass
        results['research_grid_run'] = {'status': _rgws, 'duration': round(_rgwdur,1),
            'n_tested': _rgwinf.get('n_tested', 0),
            'n_new_laws': _rgwinf.get('n_new_laws', 0)}
        print(f"[weekly] Research Grid Run: {_rgws} | tested={_rgwinf.get('n_tested',0)} new_laws={_rgwinf.get('n_new_laws',0)} ({_rgwdur:.1f}s)", flush=True)
    except Exception as _rgwe:
        results['research_grid_run'] = {'status': f'exception:{_rgwe}', 'duration': 0}

    # ── Weekly: Episodic Memory Full Encode + Explainability ─────────────────────
    for _wscript2, _wcmd2, _wkey2, _wtout2 in [
        ('episodic_memory_engine.py', 'encode_episodes',    'episodic_encode',     60),
        ('explainability_engine.py',  'daily_explanations', 'explainability_daily', 120),
    ]:
        try:
            _w2rc, _w2out, _, _w2dur = run_script(_wscript2, _wcmd2, '{}', timeout=_wtout2)
            _w2s = get_script_status(_w2rc)
            _w2inf = {}
            try: _w2inf = json.loads(_w2out)
            except Exception: pass
            results[_wkey2] = {'status': _w2s, 'duration': round(_w2dur,1)}
            print(f"[weekly] {_wscript2} {_wcmd2}: {_w2s} ({_w2dur:.1f}s)", flush=True)
        except Exception as _w2e:
            results[_wkey2] = {'status': f'exception:{_w2e}', 'duration': 0}

    # ── Weekly: Causal Discovery + Pressure ──────────────────────────────────────
    for _wscript, _wcmd, _wkey in [
        ('causal_discovery.py',         'report',     'causal_discovery'),
        ('research_pressure_engine.py', 'build_full', 'research_pressure'),
        ('hypothesis_dsl.py',           'generate',   'hypothesis_dsl'),
    ]:
        try:
            _wrc, _wout, _, _wdur = run_script(_wscript, _wcmd, '{}', timeout=30)
            _ws = get_script_status(_wrc)
            results[_wkey] = {'status': _ws, 'duration': round(_wdur,1)}
            print(f"[weekly] {_wscript} {_wcmd}: {_ws} ({_wdur:.1f}s)", flush=True)
        except Exception as _we:
            results[_wkey] = {'status': f'exception:{_we}', 'duration': 0}

    total_duration = time.time() - t0
    all_ok = all(v.get('status', 'ok') == 'ok' for v in results.values() if isinstance(v, dict))

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
