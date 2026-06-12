#!/usr/bin/env node
/**
 * Discovery Fabric orchestrator — L11 unified pipeline.
 * miners → merge → backtest_gate → discovery_ml_manifest.json
 *
 * Usage: npm run egx:discovery:fabric [--json] [--merge-only] [--gate-only]
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { createHash } from 'crypto';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

loadEnv();

const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const MERGE_ONLY = process.argv.includes('--merge-only');
const GATE_ONLY = process.argv.includes('--gate-only');
const NO_ML_LOOP = process.argv.includes('--no-ml-loop');

const PY = (script, args = '{}') => {
  const out = execFileSync(PYTHON3, [join(PROJECT_ROOT, script), args], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8',
    timeout: 900_000,
  });
  return parsePythonJson(out);
};

const report = { at: new Date().toISOString(), stages: [] };

function stage(name, fn) {
  const t0 = Date.now();
  try {
    const result = fn();
    report.stages.push({ name, ok: true, ms: Date.now() - t0, result });
    return result;
  } catch (e) {
    report.stages.push({ name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 200) });
    throw e;
  }
}

console.log('\n═══ Discovery Fabric (L11) ═══\n');

const manifestPath = join(PROJECT_ROOT, 'data/discovery_ml_manifest.json');
const manifestHashBefore = existsSync(manifestPath)
  ? createHash('sha256').update(readFileSync(manifestPath, 'utf8')).digest('hex').slice(0, 16)
  : null;

if (!GATE_ONLY) {
  const hydrateParams = process.env.EGX_DISCOVERY_FETCH_L0 === '1'
    ? '{}'
    : '{"skip_fetch":true}';
  stage('data_hydrate', () => PY('scripts/python/discovery_data_hydrate.py', hydrateParams));
  const merge = stage('fabric_merge', () => PY('scripts/python/discovery_fabric_merge.py'));
  console.log(`  ✅ Merge: ${merge.n_proposed} atoms | ${merge.miners_run} miners`);
}

if (!MERGE_ONLY) {
  const gate = stage('backtest_gate', () => PY('scripts/python/discovery_backtest_gate.py'));
  console.log(`  ✅ Gate: validated=${gate.n_validated} rejected=${gate.n_rejected} | priority=${gate.priority_atoms}`);
}

let mlLoop = null;
if (!NO_ML_LOOP && !MERGE_ONLY && existsSync(manifestPath)) {
  const manifestHashAfter = createHash('sha256')
    .update(readFileSync(manifestPath, 'utf8')).digest('hex').slice(0, 16);
  if (manifestHashBefore !== manifestHashAfter) {
    const t0 = Date.now();
    try {
      const out = execFileSync(PYTHON3, [
        join(PROJECT_ROOT, 'scripts/python/egx_ml_trainer.py'), 'phase46',
      ], { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 600_000 });
      mlLoop = {
        name: 'ml_manifest_sync', ok: true, ms: Date.now() - t0,
        result: { manifest_changed: true, phase46: out.trim().split('\n').pop()?.slice(0, 200) },
      };
      report.stages.push(mlLoop);
      console.log('  ✅ ML loop: manifest changed → phase46 bayesian_wr');
    } catch (e) {
      mlLoop = {
        name: 'ml_manifest_sync', ok: false, ms: Date.now() - t0,
        error: e.message?.slice(0, 200), skipped: true,
      };
      report.stages.push(mlLoop);
      console.log(`  ⚠️  ML loop skipped: ${e.message?.slice(0, 120)}`);
    }
  }
}

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
report.ml_loop = mlLoop;
writeFileSync(join(PROJECT_ROOT, 'data/discovery_fabric_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Discovery Fabric OK ═══\n');
}

process.exit(report.stages.every(s => s.ok || s.skipped) ? 0 : 1);
