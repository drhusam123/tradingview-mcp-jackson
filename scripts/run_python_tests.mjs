#!/usr/bin/env node
/**
 * Run offline Python smoke tests (no TradingView CDP).
 * Individual tests skip gracefully when local data/ DB artifacts are missing.
 */
import { execSync } from 'child_process';
import { readdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './lib/load_env.mjs';

const PY = process.env.PYTHON || 'python3';
const tests = readdirSync(join(PROJECT_ROOT, 'tests'))
  .filter((f) => f.endsWith('.test.py'))
  .sort();

if (!tests.length) {
  console.error('No Python tests found in tests/');
  process.exit(1);
}

let failed = 0;
for (const file of tests) {
  const rel = join('tests', file);
  process.stdout.write(`▶  ${rel}\n`);
  try {
    execSync(`${PY} ${rel}`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 120_000 });
  } catch {
    failed += 1;
  }
}

console.log(`\n=== Python tests: ${tests.length - failed}/${tests.length} PASS ===`);
process.exit(failed ? 1 : 0);
