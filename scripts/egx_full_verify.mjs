#!/usr/bin/env node
/**
 * Full production stack verification — one command, no gaps.
 * TV MCP + automation + notify reconcile + unit tests.
 *
 * Usage: node scripts/egx_full_verify.mjs [--skip-tests] [--skip-cdp]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { alertNotification } from './lib/notification_alert.mjs';

loadEnv();

const NODE = process.execPath;
const SKIP_TESTS = process.argv.includes('--skip-tests');
const SKIP_CDP = process.argv.includes('--skip-cdp');

const steps = [];
function run(label, cmd, { optional = false } = {}) {
  console.log(`\n▶  ${label}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 600_000 });
    steps.push({ label, ok: true });
    return true;
  } catch (e) {
    steps.push({ label, ok: false, error: e.message?.slice(0, 200) });
    if (!optional) throw e;
    console.error(`⚠️  ${label} skipped: ${e.message}`);
    return false;
  }
}

console.log('\n═══ EGX Full Stack Verify ═══');
console.log(`Project: ${PROJECT_ROOT}`);

const mcpPath = join(homedir(), '.claude', '.mcp.json');
const expectedServer = join(PROJECT_ROOT, 'src/server.js');
if (existsSync(mcpPath)) {
  try {
    const cfg = JSON.parse(readFileSync(mcpPath, 'utf8'));
    const args = cfg?.mcpServers?.tradingview?.args?.[0];
    const mcpOk = args === expectedServer;
    console.log(`${mcpOk ? '✅' : '❌'} Claude MCP path: ${args || 'missing'}`);
    if (!mcpOk) {
      console.log(`   Expected: ${expectedServer}`);
      console.log('   Fix: update ~/.claude/.mcp.json tradingview.args[0]');
    }
    steps.push({ label: 'Claude MCP config path', ok: mcpOk });
  } catch {
    steps.push({ label: 'Claude MCP config parse', ok: false });
    console.log('❌ Could not parse ~/.claude/.mcp.json');
  }
} else {
  console.log('⚠️  ~/.claude/.mcp.json not found (optional for Claude Code)');
}

if (SKIP_CDP) {
  console.log('⏭  TV live CDP checks skipped (--skip-cdp)');
} else {
  run('TV MCP integration', `"${NODE}" scripts/egx_tv_integration_verify.mjs`);
}

run('Automation (cron + notify)', `"${NODE}" scripts/egx_automation_verify.mjs`);
run('ML+Gate pipeline', `"${NODE}" scripts/egx_ml_gate_pipeline_verify.mjs --ci`);
if (!process.argv.includes('--skip-session')) {
  run('Session ready', `"${NODE}" scripts/egx_session_ready.mjs --skip-verify-check`, { optional: true });
}
run('Delivery reconcile', `"${NODE}" scripts/egx_notify_reconcile.mjs`, { optional: true });
run('Decision bot (safety)', `"${NODE}" scripts/egx_decision_bot.mjs --verify`, { optional: true });

if (!SKIP_TESTS) {
  run('Offline tests (npm test)', 'npm test');
}

const fail = steps.filter(s => !s.ok).length;
const report = {
  at: new Date().toISOString(),
  skip_tests: SKIP_TESTS,
  skip_cdp: SKIP_CDP,
  pass: fail === 0,
  total: steps.length,
  failed: fail,
  steps,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/full_verify_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ Summary ═══');
for (const s of steps) console.log(`  ${s.ok ? '✅' : '❌'} ${s.label}`);
console.log(`\n=== Full Verify: ${steps.length - fail}/${steps.length} PASS ===\n`);

if (fail) {
  alertNotification('FULL_VERIFY_FAILED', {
    failed: steps.filter(s => !s.ok).map(s => s.label),
    skip_cdp: SKIP_CDP,
  });
}
process.exit(fail ? 1 : 0);
