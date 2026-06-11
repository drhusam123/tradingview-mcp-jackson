#!/usr/bin/env node
/**
 * EGX Pre-flight — run before production deploy or client Telegram enable.
 *
 * Usage:
 *   node scripts/egx_preflight.mjs
 *   node scripts/egx_preflight.mjs --skip-tests
 */
import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const SKIP_TESTS = process.argv.includes('--skip-tests');

let fails = 0;

function step(name, cmd, { optional = false } = {}) {
  process.stdout.write(`\n▶  ${name}\n`);
  try {
    execSync(cmd, { cwd: ROOT, stdio: 'inherit', timeout: 600_000 });
    process.stdout.write(`✅  ${name}\n`);
    return true;
  } catch {
    process.stdout.write(`❌  ${name}\n`);
    if (!optional) fails += 1;
    return false;
  }
}

process.stdout.write('═══ EGX Pre-flight ═══\n');

if (!existsSync(join(ROOT, 'data/egx_trading.db'))) {
  process.stdout.write('⚠️  data/egx_trading.db not found — some checks will fail\n');
}

step('Schema migrations', `"${NODE}" scripts/migrations/migrate.mjs --check`);

if (!SKIP_TESTS) {
  step('Offline tests', 'npm test');
}

step('Quick validation', `"${NODE}" scripts/egx_validate.mjs --quick`, { optional: true });
step('Automation verify', `"${NODE}" scripts/egx_automation_verify.mjs`, { optional: true });
step('Production acceptance', `"${NODE}" scripts/egx_production_acceptance.mjs`);

process.stdout.write(`\n═══ Pre-flight: ${fails === 0 ? 'PASS' : `${fails} FAIL`} ═══\n`);
process.exit(fails > 0 ? 1 : 0);
