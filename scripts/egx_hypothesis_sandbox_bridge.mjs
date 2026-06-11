#!/usr/bin/env node
/**
 * Hypothesis sandbox bridge runner.
 * Usage: node scripts/egx_hypothesis_sandbox_bridge.mjs [--no-merge]
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

loadEnv();

const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const params = JSON.stringify({ merge_feedback: !process.argv.includes('--no-merge') });

const out = execFileSync(PYTHON3, [join(PROJECT_ROOT, 'scripts/python/hypothesis_sandbox_bridge.py'), params], {
  cwd: PROJECT_ROOT,
  encoding: 'utf8',
  timeout: 120_000,
});
const result = parsePythonJson(out);

console.log('\n═══ Hypothesis Sandbox Bridge ═══\n');
console.log(`  promoted: ${result.n_promoted} | atoms: ${(result.priority_atoms || []).length}`);
console.log(`  saved: data/hypothesis_sandbox_bridge_last.json\n`);
process.exit(result.success ? 0 : 1);
