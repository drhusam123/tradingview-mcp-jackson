#!/usr/bin/env node
/**
 * EGX Full Heavy Training Orchestrator
 * Runs phases 1→9, validation, predict_ensemble, governance.
 *
 * Usage:
 *   npm run egx:train:full
 *   node scripts/egx_full_train.mjs --skip-discovery
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.env.PYTHON3 || 'python3';

const args = process.argv.slice(2);
const SKIP_DISCOVERY = args.includes('--skip-discovery');
const QUICK = args.includes('--quick');

function run(cmd, label) {
  const t0 = Date.now();
  console.log(`\n[train] ▶ ${label}`);
  console.log(`[train]   ${cmd}`);
  try {
    execSync(cmd, { cwd: ROOT, stdio: 'inherit', timeout: QUICK ? 600_000 : 3_600_000 });
    console.log(`[train] ✓ ${label} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
    return true;
  } catch (e) {
    console.error(`[train] ✗ ${label}: ${e.message}`);
    return false;
  }
}

const steps = [
  ['npm run egx:quality:data', 'Data quality audit'],
  [`${PY} scripts/python/egx_ml_trainer.py phase1`, 'Phase 1 — features'],
  [`${PY} scripts/python/egx_ml_trainer.py phase5`, 'Phase 5 — triple barrier labels'],
  [`${PY} scripts/python/egx_ml_trainer.py phase2`, 'Phase 2 — explosion ensemble'],
  [`${PY} scripts/python/egx_ml_trainer.py phase3`, 'Phase 3 — regime models'],
];

if (!QUICK) {
  steps.push(
    [`${PY} scripts/python/egx_ml_trainer.py phase4`, 'Phase 4 — per-stock models'],
    [`${PY} scripts/python/egx_ml_trainer.py phase6`, 'Phase 6 — walk-forward'],
    [`${PY} scripts/python/ml_purged_audit.py`, 'Purged CV governance'],
    [`${PY} scripts/python/egx_ml_trainer.py phase48`, 'Phase 48 — antithetic backtest'],
    [`${PY} scripts/python/egx_ml_trainer.py phase7`, 'Phase 7 — SHAP'],
  );
}

steps.push(
  [`${PY} scripts/python/egx_ml_trainer.py phase9`, 'Phase 9 — calibration'],
  [`${PY} scripts/python/egx_ml_trainer.py predict_ensemble`, 'Ensemble predict'],
  [`${PY} scripts/python/egx_ml_trainer.py phase21`, 'Phase 21 — spectral'],
  [`${PY} scripts/python/egx_ml_trainer.py phase11`, 'Phase 11 — pine fuse'],
  [`${PY} scripts/python/egx_ml_trainer.py phase51`, 'Phase 51 — tomorrow forecast'],
  [`${PY} scripts/python/egx_ml_trainer.py phase46`, 'Phase 46 — Bayesian WR'],
  [`${PY} scripts/python/egx_ml_trainer.py phase50`, 'Phase 50 — adaptive gates'],
);

if (!SKIP_DISCOVERY) {
  steps.splice(3, 0,
    ['npm run egx:discover:quant', 'Quant discovery refresh'],
    ['npm run egx:discover:wf', 'Walk-forward discovery'],
  );
}

console.log(`[train] EGX full training started (quick=${QUICK})`);
const results = [];
for (const [cmd, label] of steps) {
  results.push({ label, ok: run(cmd, label) });
}

const failed = results.filter(r => !r.ok);
console.log('\n[train] Summary:', JSON.stringify({
  total: results.length,
  passed: results.length - failed.length,
  failed: failed.map(f => f.label),
}, null, 2));

process.exit(failed.length ? 1 : 0);
