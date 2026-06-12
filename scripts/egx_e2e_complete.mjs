#!/usr/bin/env node
/**
 * Master end-to-end stack runner — discovery → closed loop → verify → go-live prep.
 *
 * Usage:
 *   npm run egx:e2e:complete
 *   npm run egx:e2e:complete -- --skip-automate --skip-cdp
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const NODE = process.execPath;
const SKIP_AUTOMATE = process.argv.includes('--skip-automate');
const SKIP_CDP = process.argv.includes('--skip-cdp');
const SKIP_GO_LIVE = process.argv.includes('--skip-go-live');
const AS_JSON = process.argv.includes('--json');

const steps = [];

function run(name, cmd, { optional = false, timeout = 1_800_000 } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout });
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

console.log('\n═══ EGX End-to-End Complete ═══\n');

run('migrations_check', `"${NODE}" scripts/migrations/migrate.mjs --check`, { timeout: 60_000 });

if (!SKIP_AUTOMATE) {
  run('discovery_automate', 'npm run egx:discovery:automate', { timeout: 2_400_000 });
} else {
  run('discovery_fabric', `"${NODE}" scripts/egx_discovery_fabric.mjs`, { timeout: 1_200_000 });
  run('discovery_refresh', `"${NODE}" scripts/egx_discovery_refresh.mjs`, { timeout: 1_200_000 });
  run('discovery_verify', `"${NODE}" scripts/egx_discovery_verify.mjs`, { timeout: 300_000 });
}

run('closed_loop', `"${NODE}" scripts/egx_closed_loop.mjs`, { timeout: 600_000 });
run('learning_loop', `"${NODE}" scripts/egx_learning_loop.mjs`, { timeout: 300_000, optional: true });
run('loop_audit', `"${NODE}" scripts/egx_loop_audit.mjs`, { timeout: 120_000 });
run('data_layer_audit', `"${NODE}" scripts/egx_data_layer_audit.mjs`, { timeout: 60_000 });
run('architecture_audit', `"${NODE}" scripts/egx_architecture_audit.mjs`, { timeout: 60_000 });
run('offline_tests', 'npm test', { timeout: 180_000 });

if (!SKIP_CDP) {
  run('tv_smoke', 'npm run tv:smoke', { timeout: 30_000, optional: true });
  run('tv_integration', `"${NODE}" scripts/egx_tv_integration_verify.mjs`, { timeout: 300_000, optional: true });
}

run('automation_verify', `"${NODE}" scripts/egx_automation_verify.mjs`, { timeout: 120_000 });
run('production_acceptance', `"${NODE}" scripts/egx_production_acceptance.mjs`, { timeout: 300_000 });

if (!SKIP_GO_LIVE) {
  run('go_live_local', `"${NODE}" scripts/egx_go_live.mjs --skip-push`, { timeout: 600_000, optional: true });
}

const fail = steps.filter(s => !s.ok && !s.optional).length;
const report = {
  at: new Date().toISOString(),
  pass: fail === 0,
  steps,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/e2e_complete_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ E2E Complete Summary ═══');
for (const s of steps) {
  console.log(`  ${s.ok ? '✅' : (s.optional ? '⚠️' : '❌')} ${s.name} (${s.ms}ms)`);
}
console.log(`\n=== E2E Complete: ${steps.length - fail}/${steps.length} PASS ===\n`);

if (AS_JSON) console.log(JSON.stringify(report, null, 2));
process.exit(fail ? 1 : 0);
