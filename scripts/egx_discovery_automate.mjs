#!/usr/bin/env node
/**
 * Discovery stack FULL automation — hydrate → cron → fabric → refresh → verify → tests.
 *
 * Usage:
 *   npm run egx:discovery:automate
 *   npm run egx:discovery:automate -- --skip-cron --skip-refresh --skip-hydrate
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const SKIP_CRON = process.argv.includes('--skip-cron');
const SKIP_REFRESH = process.argv.includes('--skip-refresh');
const SKIP_HYDRATE = process.argv.includes('--skip-hydrate');
const SKIP_TESTS = process.argv.includes('--skip-tests');
const AS_JSON = process.argv.includes('--json');

const steps = [];

function run(name, cmd, { optional = false, timeout = 900_000 } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    execSync(cmd, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout,
      env: { ...process.env, PYTHON_BIN: PYTHON, PYTHON3: PYTHON },
    });
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

function readStats() {
  const stats = {};
  try {
    const cat = JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/discovery_data_catalog.json'), 'utf8'));
    stats.catalog_tables = cat.total_tables;
    stats.production_tables = cat.production_tables_with_data;
  } catch { /* */ }
  try {
    const m = JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/discovery_ml_manifest.json'), 'utf8'));
    stats.priority_atoms = (m.priority_atoms || []).length;
    stats.penalize_atoms = (m.penalize_atoms || []).length;
    stats.seed_pairs = (m.seed_pairs || []).length;
  } catch { /* */ }
  try {
    const out = execSync(
      `"${PYTHON}" scripts/python/discovery_manifest_loader.py stats`,
      { cwd: PROJECT_ROOT, encoding: 'utf8' },
    ).trim();
    const j = JSON.parse(out);
    stats.atoms_total = j.atoms_total;
    stats.atoms_validated = j.atoms_validated;
    stats.miners_active = j.miners_active;
    stats.source_tables = j.source_tables;
  } catch { /* */ }
  return stats;
}

console.log('\n═══ Discovery FULL Automation ═══\n');

if (!SKIP_HYDRATE) {
  run('data_hydrate', `"${PYTHON}" scripts/python/discovery_data_hydrate.py '{}'`, { timeout: 900_000 });
}

if (!SKIP_CRON) {
  run('cron_install', `"${NODE}" scripts/install_cron.mjs`, { timeout: 120_000 });
}

run('discovery_fabric', `"${NODE}" scripts/egx_discovery_fabric.mjs`, { timeout: 1_200_000 });

if (!SKIP_REFRESH) {
  run('discovery_refresh', `"${NODE}" scripts/egx_discovery_refresh.mjs`, { timeout: 1_200_000 });
}

run('discovery_verify', `"${NODE}" scripts/egx_discovery_verify.mjs`, { timeout: 300_000 });

if (!SKIP_TESTS) {
  run('test_ci', 'npm run test:ci', { timeout: 120_000 });
}

const stats = readStats();
const report = {
  at: new Date().toISOString(),
  steps,
  stats,
  pass: steps.every(s => s.ok || s.optional),
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_automate_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Discovery FULL Automation Summary ═══');
  console.log(`  Tables cataloged:     ${stats.catalog_tables ?? '?'}`);
  console.log(`  Production w/ data:   ${stats.production_tables ?? '?'}`);
  console.log(`  Atoms total:          ${stats.atoms_total ?? '?'}`);
  console.log(`  Atoms validated:      ${stats.atoms_validated ?? '?'}`);
  console.log(`  Miners active:        ${stats.miners_active ?? '?'}`);
  console.log(`  Manifest priority:    ${stats.priority_atoms ?? '?'}`);
  console.log(`  Manifest penalize:    ${stats.penalize_atoms ?? '?'}`);
  console.log(`  Seed pairs:           ${stats.seed_pairs ?? '?'}`);
  for (const s of steps) {
    console.log(`  ${s.ok ? '✅' : '❌'} ${s.name} (${s.ms}ms)`);
  }
  console.log(`\n  Result: ${report.pass ? 'PASS' : 'FAIL'}\n`);
}

process.exit(report.pass ? 0 : 1);
