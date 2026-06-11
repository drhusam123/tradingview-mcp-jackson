#!/usr/bin/env node
/**
 * Discovery stack full automation — cron + fabric + refresh + verify.
 *
 * Usage:
 *   npm run egx:discovery:automate
 *   npm run egx:discovery:automate -- --skip-cron
 *   npm run egx:discovery:automate -- --skip-refresh
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const NODE = process.execPath;
const SKIP_CRON = process.argv.includes('--skip-cron');
const SKIP_REFRESH = process.argv.includes('--skip-refresh');
const AS_JSON = process.argv.includes('--json');

const steps = [];

function run(name, cmd, { optional = false, timeout = 900_000 } = {}) {
  const t0 = Date.now();
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout, env: process.env });
    const row = { name, ok: true, ms: Date.now() - t0 };
    steps.push(row);
    return row;
  } catch (e) {
    const row = { name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 200), optional };
    steps.push(row);
    if (!optional) throw e;
    console.log(`  ⚠️  ${name}: ${e.message?.slice(0, 120)}`);
    return row;
  }
}

console.log('\n═══ Discovery Full Automation ═══\n');

if (!SKIP_CRON) {
  run('cron_install', `"${NODE}" scripts/install_cron.mjs`, { timeout: 120_000 });
}

run('discovery_fabric', `"${NODE}" scripts/egx_discovery_fabric.mjs`);

if (!SKIP_REFRESH) {
  run('discovery_refresh', `"${NODE}" scripts/egx_discovery_refresh.mjs`, { timeout: 1_200_000 });
}

run('discovery_verify', `"${NODE}" scripts/egx_discovery_verify.mjs`, { timeout: 300_000 });

const report = { at: new Date().toISOString(), steps, pass: steps.every(s => s.ok || s.optional) };
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_automate_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Discovery Automation OK ═══\n');
}

process.exit(report.pass ? 0 : 1);
