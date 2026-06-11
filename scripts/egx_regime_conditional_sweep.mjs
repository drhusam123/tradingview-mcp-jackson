#!/usr/bin/env node
/**
 * Regime-conditional sweep runner.
 * Usage: node scripts/egx_regime_conditional_sweep.mjs [--json]
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

loadEnv();

const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const AS_JSON = process.argv.includes('--json');

const out = execFileSync(PYTHON3, [join(PROJECT_ROOT, 'scripts/python/regime_conditional_sweep.py'), '{}'], {
  cwd: PROJECT_ROOT,
  encoding: 'utf8',
  timeout: 600_000,
});
const result = parsePythonJson(out);

if (AS_JSON) {
  console.log(JSON.stringify(result, null, 2));
} else {
  console.log('\n═══ Regime Conditional Sweep ═══\n');
  for (const b of result.regimes || []) {
    console.log(`  ${b.regime}: ${b.n_examples} examples | top_pairs=${(b.top_pairs || []).length}`);
  }
  console.log(`\n  seed_pairs: ${(result.seed_pairs || []).length} | saved: data/regime_conditional_sweep_last.json\n`);
}

process.exit(result.success ? 0 : 1);
