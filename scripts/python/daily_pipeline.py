#!/usr/bin/env python3
"""
EGX Daily Automated Pipeline
==============================
Runs every trading day at 06:00 EEST (after market data is fetched).
Schedule: 06:00 → fetch, 06:10 → breadth, 06:15 → regime, 06:20 → closing pressure,
          06:25 → predict_ensemble, Sunday only → phase12 incremental

Usage:
    python3 daily_pipeline.py                 # run all steps
    python3 daily_pipeline.py --step predict  # run single step
    python3 daily_pipeline.py --weekly        # include weekly retrain check

Cron setup (edit crontab with: crontab -e):
    0 6 * * 0-4 cd /Users/dr.husam/tradingview-mcp-jackson && python3 scripts/python/daily_pipeline.py >> /tmp/daily_pipeline.log 2>&1
"""
import subprocess, sys, time, datetime, json, os, argparse

ROOT    = "/Users/dr.husam/tradingview-mcp-jackson"
TRAINER = f"{ROOT}/scripts/python/egx_ml_trainer.py"
PYTHON  = sys.executable
LOG     = "/tmp/daily_pipeline.log"


def run_script(script_name, desc, timeout=120):
    """Run a standalone python script (not egx_ml_trainer.py phases)."""
    script_path = f"{ROOT}/scripts/python/{script_name}.py"
    print(f"\n[{ts()}] ▶ {script_name} — {desc}", flush=True)
    t0 = time.time()
    try:
        result = subprocess.run(
            [PYTHON, script_path],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT
        )
        dur = round(time.time() - t0, 1)
        ok = result.returncode == 0
        out = result.stdout.strip().split('\n')
        for line in reversed(out):
            if line.strip():
                print(f"  → {line.strip()}", flush=True)
                break
        print(f"  {'✓' if ok else '✗'} {script_name} {'done' if ok else 'FAILED'} ({dur}s)", flush=True)
        if not ok and result.stderr:
            print(f"  stderr: {result.stderr.strip()[:200]}", flush=True)
        return ok, dur, out[-3:] if len(out) >= 3 else out
    except subprocess.TimeoutExpired:
        print(f"  ✗ {script_name} TIMEOUT after {timeout}s", flush=True)
        return False, timeout, ["TIMEOUT"]
    except Exception as e:
        print(f"  ✗ {script_name} ERROR: {e}", flush=True)
        return False, 0, [str(e)]


def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(phase_cmd, desc, timeout=300):
    """Run a trainer phase and return (success, duration, output_tail)."""
    print(f"\n[{ts()}] ▶ {phase_cmd} — {desc}", flush=True)
    t0 = time.time()
    try:
        result = subprocess.run(
            [PYTHON, TRAINER, phase_cmd],
            capture_output=True, text=True,
            timeout=timeout, cwd=ROOT
        )
        dur = round(time.time() - t0, 1)
        out = result.stdout.strip().split('\n')
        # Print last meaningful line
        for line in reversed(out):
            if line.strip() and not line.startswith('[Trainer]'):
                print(f"  → {line.strip()}", flush=True)
                break
        ok = result.returncode == 0
        print(f"  ✓ {phase_cmd} done ({dur}s)" if ok else f"  ✗ {phase_cmd} FAILED ({dur}s)", flush=True)
        return ok, dur, out[-3:] if len(out) >= 3 else out
    except subprocess.TimeoutExpired:
        print(f"  ✗ {phase_cmd} TIMEOUT after {timeout}s", flush=True)
        return False, timeout, ["TIMEOUT"]
    except Exception as e:
        print(f"  ✗ {phase_cmd} ERROR: {e}", flush=True)
        return False, 0, [str(e)]


def check_model_drift():
    """Check if models need retraining using realized outcomes, governance, and drift monitor."""
    try:
        import sqlite3
        conn = sqlite3.connect(f"{ROOT}/data/egx_trading.db")
        conn.row_factory = sqlite3.Row

        # Check recent prediction accuracy (last 30 days)
        recent = conn.execute("""
            SELECT COUNT(*) as n,
                   SUM(CASE WHEN hit_t5 = 1 OR return_t5 > 0 THEN 1 ELSE 0 END) as wins
            FROM recommendation_outcomes
            WHERE created_at >= date('now', '-30 days')
              AND outcome_filled = 1
              AND return_t5 IS NOT NULL
        """).fetchone()

        governance = conn.execute("""
            SELECT accepted_for_client, risk_level, reasons_json
            FROM ml_governance_audit
            ORDER BY run_date DESC, created_at DESC
            LIMIT 1
        """).fetchone()

        latest_model = conn.execute("""
            SELECT auc_oos, precision_at_50, precision_at_70, n_oos_total
            FROM ml_model_scores
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        phase58 = conn.execute("""
            SELECT results
            FROM ml_trainer_runs
            WHERE phase IN ('58', 'phase58')
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        latest_phase2 = conn.execute("""
            SELECT run_date, results
            FROM ml_trainer_runs
            WHERE phase IN ('2', 'phase2')
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        latest_phase9 = conn.execute("""
            SELECT run_date, results
            FROM ml_trainer_runs
            WHERE phase IN ('9', 'phase9')
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        needs_retrain = False
        reason = []

        if recent and recent['n'] > 10:
            win_rate = recent['wins'] / recent['n']
            if win_rate < 0.35:
                needs_retrain = True
                reason.append(f"win_rate={win_rate:.1%} below threshold")

        if governance:
            accepted = int(governance['accepted_for_client'] or 0) == 1
            risk = (governance['risk_level'] or '').upper()
            if not accepted or risk in {'HIGH', 'CRITICAL'}:
                needs_retrain = True
                reason.append(f"ml_governance={risk or 'NOT_ACCEPTED'}")

        if latest_model:
            auc_oos = latest_model['auc_oos']
            p50 = latest_model['precision_at_50']
            n_oos = latest_model['n_oos_total'] or 0
            if n_oos and n_oos < 500:
                needs_retrain = True
                reason.append(f"oos_sample={n_oos} below 500")
            if auc_oos is not None and auc_oos < 0.58:
                needs_retrain = True
                reason.append(f"auc_oos={auc_oos:.3f} below 0.58")
            if p50 is not None and p50 < 0.45:
                needs_retrain = True
                reason.append(f"precision@50={p50:.1%} below 45%")

        if phase58:
            try:
                p58 = json.loads(phase58['results'] or '{}')
                trigger = p58.get('retrain_trigger') or {}
                if trigger.get('needed'):
                    needs_retrain = True
                    reason.extend(trigger.get('reasons') or ['phase58 drift trigger'])
                feature_drift = p58.get('feature_drift') or {}
                max_psi = float(feature_drift.get('max_psi') or 0)
                if max_psi >= 0.50:
                    needs_retrain = True
                    reason.append(f"severe_feature_psi={max_psi:.3f}")
            except Exception as e:
                needs_retrain = True
                reason.append(f"phase58 unreadable: {e}")

        retrained_today = False
        try:
            today = datetime.date.today().isoformat()
            p2 = json.loads((latest_phase2 or {})['results'] or '{}') if latest_phase2 else {}
            p9 = json.loads((latest_phase9 or {})['results'] or '{}') if latest_phase9 else {}
            p2_ok = bool((p2.get('acceptance') or {}).get('accepted_for_prediction'))
            p9_ok = bool(p9.get('calibrator_path'))
            retrained_today = (
                latest_phase2 and latest_phase9 and
                latest_phase2['run_date'] == today and latest_phase9['run_date'] == today and
                p2_ok and p9_ok
            )
        except Exception:
            retrained_today = False

        drift_only_reasons = reason and all(
            r.startswith('feature_psi') or r.startswith('severe_feature_psi')
            for r in reason
        )
        if needs_retrain and retrained_today and drift_only_reasons:
            needs_retrain = False
            reason = ['retrain completed today; monitoring persistent feature drift']

        conn.close()

        reason = list(dict.fromkeys(reason))
        return needs_retrain, '; '.join(reason) if reason else "metrics OK"
    except Exception as e:
        return False, f"drift check error: {e}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', default='all', help='all|predict|breadth|regime|pressure|monitor|outcomes|check')
    parser.add_argument('--weekly', action='store_true', help='Include weekly incremental update')
    parser.add_argument('--monthly', action='store_true', help='Include monthly full retrain')
    parser.add_argument('--auto-retrain', action='store_true', help='Allow drift-triggered full retrain outside monthly window')
    args = parser.parse_args()

    today = datetime.date.today()
    dow   = today.weekday()  # 0=Mon, 6=Sun
    print(f"\n{'='*60}", flush=True)
    print(f"EGX DAILY PIPELINE — {today.isoformat()} ({today.strftime('%A')})", flush=True)
    print(f"{'='*60}", flush=True)

    results = {}

    if args.step in ('all', 'breadth'):
        # Phase52: Update sector breadth + market breadth (fast, ~10s)
        ok, dur, _ = run('phase52', 'Market + Sector Breadth Update', timeout=120)
        results['phase52'] = {'ok': ok, 'dur': dur}

    if args.step in ('all', 'regime'):
        # Phase56: Update Markov regime engine (~10s)
        ok, dur, _ = run('phase56', 'Markov Regime Engine Update', timeout=120)
        results['phase56'] = {'ok': ok, 'dur': dur}

    if args.step in ('all', 'pressure'):
        # Phase57: Closing pressure signal (~30s)
        ok, dur, _ = run('phase57', 'Closing Pressure Signal', timeout=120)
        results['phase57'] = {'ok': ok, 'dur': dur}

    if args.step in ('all', 'predict'):
        # Predict ensemble — main daily output (~40s)
        ok, dur, out = run('predict_ensemble', 'Ensemble Prediction (33 features)', timeout=180)
        results['predict_ensemble'] = {'ok': ok, 'dur': dur}
        if ok:
            # Extract and display top predictions
            for line in out:
                if 'top5' in line or 'n_stored' in line or 'regime' in line:
                    print(f"  📊 {line.strip()}", flush=True)

    if args.step in ('all', 'monitor'):
        # Phase58: Model health monitor — drift detection + auto-retrain check (~15s)
        ok, dur, _ = run('phase58', 'Model Health Monitor', timeout=120)
        results['phase58'] = {'ok': ok, 'dur': dur}

    if args.step in ('all', 'outcomes'):
        # Fill trade outcomes from OHLCV prices (return_t1/t3/t5/t10)
        ok, dur, _ = run_script('outcome_filler', 'Outcome Filler (returns T1/T3/T5)', timeout=120)
        results['outcome_filler'] = {'ok': ok, 'dur': dur}

    if args.step in ('all', 'monitor', 'check'):
        ok, dur, _ = run_script('ml_purged_audit', 'ML Purged Walk-Forward Governance', timeout=120)
        results['ml_purged_audit'] = {'ok': ok, 'dur': dur}

        ok, dur, _ = run_script('macro_edge_validator', 'Macro Edge Purged Validation', timeout=120)
        results['macro_edge_validator'] = {'ok': ok, 'dur': dur}

    # Weekly: Sunday — run Phase12 incremental update + adaptive threshold
    if (args.step == 'all' and dow == 6) or args.weekly:
        print(f"\n[{ts()}] 📅 Weekly incremental update (Sunday)", flush=True)
        ok, dur, _ = run('phase12', 'Incremental Online Learning (weekly)', timeout=120)
        results['phase12'] = {'ok': ok, 'dur': dur}
        # Adjust ML threshold based on last 30d realized win rate
        ok, dur, _ = run_script('adaptive_threshold', 'Adaptive Threshold Update (feedback)', timeout=60)
        results['adaptive_threshold'] = {'ok': ok, 'dur': dur}

    drift_window = (args.step == 'all' and dow == 6 and today.day <= 7) or args.monthly
    if args.step in ('all', 'monitor', 'check'):
        print(f"\n[{ts()}] 📅 Model drift gate", flush=True)
        needs_retrain, reason = check_model_drift()
        can_retrain = drift_window or args.auto_retrain
        print(f"  Drift check: {'⚠️  RETRAIN NEEDED' if needs_retrain else '✅  OK'} — {reason}", flush=True)
        if needs_retrain and can_retrain:
            print(f"  Starting full retrain (phase2 + phase3 + phase9)...", flush=True)
            run('phase2', 'Ensemble Retrain (33 features) — drift triggered', timeout=7200)
            run('phase3', 'Regime Models Retrain — drift triggered', timeout=2400)
            run('phase9', 'Recalibrate after retrain', timeout=300)
        elif needs_retrain:
            print("  Retrain deferred: pass --auto-retrain or run monthly window.", flush=True)

    # Summary
    ok_count  = sum(1 for v in results.values() if v['ok'])
    total_dur = sum(v['dur'] for v in results.values())
    print(f"\n[{ts()}] ✅ Pipeline done: {ok_count}/{len(results)} phases OK, total={total_dur:.0f}s", flush=True)
    print(json.dumps({"date": today.isoformat(), "results": results}), flush=True)
    return 0 if ok_count == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
