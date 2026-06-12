#!/usr/bin/env node
/**
 * ML + gate refresh pipeline — predict → score → diagnose actionable funnel.
 *
 * Usage:
 *   npm run egx:ml:boost
 *   npm run egx:ml:boost -- --date 2026-06-11
 */
import { execSync, execFileSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

loadEnv();

const NODE = process.execPath;
const PY = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const signalDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);
const SKIP_ENSEMBLE = process.argv.includes('--skip-ensemble');

const steps = [];

function run(name, cmd, { optional = false, timeout = 600_000 } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout });
    steps.push({ name, ok: true, ms: Date.now() - t0 });
    return true;
  } catch (e) {
    steps.push({ name, ok: false, ms: Date.now() - t0, optional, error: e.message?.slice(0, 120) });
    if (!optional) throw e;
    console.log(`  ⚠️  ${name}: ${e.message?.slice(0, 80)}`);
    return false;
  }
}

console.log(`\n═══ EGX ML Boost (${signalDate}) ═══\n`);

if (!SKIP_ENSEMBLE) {
  let ensDeps = false;
  try {
    execSync(`"${PY}" -c "import xgboost, lightgbm"`, { stdio: 'pipe', timeout: 10_000 });
    ensDeps = true;
  } catch {
    console.log('  ⚠️  predict_ensemble skipped (install: pip install xgboost lightgbm)');
    steps.push({ name: 'predict_ensemble', ok: false, optional: true, error: 'xgboost/lightgbm missing' });
  }
  if (ensDeps) {
    run('predict_ensemble', `"${PY}" scripts/python/egx_ml_trainer.py predict_ensemble`, {
      optional: true,
      timeout: 900_000,
    });
  }
  run('explosion_ml', `"${PY}" scripts/python/explosion_ml.py predict_today '{"date":"${signalDate}"}'`, {
    optional: true,
    timeout: 300_000,
  });
}
run('mladv_daily', `"${PY}" scripts/python/ml_advanced.py daily`, { optional: true, timeout: 300_000 });
run('adaptive_gate_phase50', `"${PY}" scripts/python/egx_ml_trainer.py phase50`, { optional: true, timeout: 300_000 });

const scoreParams = JSON.stringify({ date: signalDate });
run('score_all', `"${PY}" scripts/python/signal_integration.py score_all '${scoreParams}'`, { timeout: 600_000 });
run('apply_arbitration', `"${PY}" scripts/python/signal_integration.py apply_arbitration_veto '${scoreParams}'`, { optional: true });

let simulate = null;
try {
  simulate = parsePythonJson(execFileSync(PY, [
    join(PROJECT_ROOT, 'scripts/python/gate_actionable_simulate.py'),
    'simulate',
    JSON.stringify({ date: signalDate }),
  ], { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 120_000 }));
  console.log('\n▶  gate_simulate');
  console.log(`  actionable=${simulate.actionable} | top_blockers=${JSON.stringify(simulate.top_blockers?.slice(0, 5))}`);
} catch (e) {
  console.log(`  ⚠️  gate_simulate: ${e.message?.slice(0, 80)}`);
}

run('signals_diagnose', `"${NODE}" scripts/egx_signal_funnel.mjs --date ${signalDate}`, { optional: true });

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  steps,
  simulate,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/ml_boost_last.json'), JSON.stringify(report, null, 2));

const fail = steps.filter(s => !s.ok && !s.optional).length;
console.log(`\n═══ ML Boost: ${steps.length - fail}/${steps.length} OK ═══\n`);
process.exit(fail ? 1 : 0);
