#!/usr/bin/env node
/**
 * Discovery Fabric orchestrator — L11 unified pipeline.
 * miners → merge → backtest_gate → discovery_ml_manifest.json
 *
 * Usage: npm run egx:discovery:fabric [--json] [--merge-only] [--gate-only]
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { writeFileSync, mkdirSync } from 'fs';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

loadEnv();

const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const AS_JSON = process.argv.includes('--json');
const MERGE_ONLY = process.argv.includes('--merge-only');
const GATE_ONLY = process.argv.includes('--gate-only');

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

if (!GATE_ONLY) {
  const merge = stage('fabric_merge', () => PY('scripts/python/discovery_fabric_merge.py'));
  console.log(`  ✅ Merge: ${merge.n_proposed} atoms | ${merge.miners_run} miners`);
}

if (!MERGE_ONLY) {
  const gate = stage('backtest_gate', () => PY('scripts/python/discovery_backtest_gate.py'));
  console.log(`  ✅ Gate: validated=${gate.n_validated} rejected=${gate.n_rejected} | priority=${gate.priority_atoms}`);
}

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_fabric_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Discovery Fabric OK ═══\n');
}

process.exit(report.stages.every(s => s.ok) ? 0 : 1);
