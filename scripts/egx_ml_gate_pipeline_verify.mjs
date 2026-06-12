#!/usr/bin/env node
/**
 * Verify ML + gate pipeline automation wiring (scripts, cron markers, npm aliases).
 * Usage: npm run egx:ml:gate:verify
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const CI = process.argv.includes('--ci');
const checks = [];
function ok(name, pass, detail = '') {
  checks.push({ name, pass, detail });
  console.log(`${pass ? '✅' : '❌'} ${name}${detail ? `: ${detail}` : ''}`);
}

const pkg = JSON.parse(readFileSync(join(PROJECT_ROOT, 'package.json'), 'utf8'));
const scripts = pkg.scripts || {};

const requiredFiles = [
  'scripts/egx_ml_boost.mjs',
  'scripts/egx_pre_session.mjs',
  'scripts/egx_post_session_ops.mjs',
  'scripts/egx_signal_funnel.mjs',
  'scripts/python/gate_actionable_simulate.py',
  'scripts/python/signal_integration.py',
  'tests/gate_ml_threshold.test.py',
  'tests/final_edge_relaxations.test.py',
  'tests/explosion_score_date.test.py',
];
for (const f of requiredFiles) ok(`file ${f}`, existsSync(join(PROJECT_ROOT, f)));

ok('npm egx:ml:boost', scripts['egx:ml:boost']?.includes('egx_ml_boost.mjs'));
ok('npm egx:ml:refresh', scripts['egx:ml:refresh']?.includes('--skip-ensemble'));
ok('npm egx:post:session → post_session_ops', scripts['egx:post:session']?.includes('egx_post_session_ops.mjs'));
ok('npm egx:post-session alias', scripts['egx:post-session']?.includes('egx_post_session_ops.mjs'));
ok('npm egx:pre:session', scripts['egx:pre:session']?.includes('egx_pre_session.mjs'));
ok('npm egx:signals:diagnose', scripts['egx:signals:diagnose']?.includes('egx_signal_funnel.mjs'));
ok('npm egx:gate:simulate', scripts['egx:gate:simulate']?.includes('gate_actionable_simulate.py'));

const tvAuto = readFileSync(join(PROJECT_ROOT, 'scripts/egx_tv_auto_update.mjs'), 'utf8');
ok('tv:auto → predict_ensemble', tvAuto.includes('predict_ensemble'));
ok('tv:auto → score_all', tvAuto.includes('score_all'));
ok('tv:auto → apply_arbitration_veto', tvAuto.includes('apply_arbitration_veto'));
ok('tv:auto → signals diagnose', tvAuto.includes('egx_signal_funnel.mjs'));

const postOps = readFileSync(join(PROJECT_ROOT, 'scripts/egx_post_session_ops.mjs'), 'utf8');
ok('post_session → ml refresh', postOps.includes('egx_ml_boost.mjs'));
ok('post_session → closed_loop', postOps.includes('egx_closed_loop.mjs'));
ok('post_session → reconcile', postOps.includes('egx_notify_reconcile.mjs'));

const preSess = readFileSync(join(PROJECT_ROOT, 'scripts/egx_pre_session.mjs'), 'utf8');
ok('pre_session → gate_simulate', preSess.includes('gate_actionable_simulate.py'));
ok('pre_session → signals_diagnose', preSess.includes('egx_signal_funnel.mjs'));

const installCron = readFileSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs'), 'utf8');
ok('cron → tv:auto marker', installCron.includes('EGX-DAILY-AUTOMATION'));
ok('cron → funnel marker', installCron.includes('EGX-FUNNEL-DAILY'));
ok('cron → post-session marker', installCron.includes('EGX-POST-SESSION-DAILY'));
ok('cron → pre-session marker', installCron.includes('EGX-PRE-SESSION-DAILY'));

if (!CI) {
  let cron = '';
  try { cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' }); } catch { /* */ }
  ok('crontab tv:auto installed', cron.includes('EGX-DAILY-AUTOMATION'));
  ok('crontab funnel installed', cron.includes('EGX-FUNNEL-DAILY'));
  ok('crontab post-session installed', cron.includes('EGX-POST-SESSION-DAILY'));
  ok('crontab pre-session installed', cron.includes('EGX-PRE-SESSION-DAILY'));
}

const PY = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
for (const t of ['gate_ml_threshold.test.py', 'explosion_score_date.test.py', 'final_edge_relaxations.test.py']) {
  try {
    execSync(`${PY} tests/${t}`, { cwd: PROJECT_ROOT, stdio: 'pipe', timeout: 30_000, env: { ...process.env, PYTHONPATH: 'scripts/python' } });
    ok(`python test ${t}`, true);
  } catch (e) {
    ok(`python test ${t}`, false, e.message?.slice(0, 60));
  }
}

const fail = checks.filter(c => !c.pass).length;
console.log(`\n=== ML/Gate Pipeline Verify: ${checks.length - fail}/${checks.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
