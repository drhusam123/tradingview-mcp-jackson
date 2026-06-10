#!/usr/bin/env node
/**
 * One-shot production readiness — all gates in sequence.
 * Usage: node scripts/egx_prod_ready.mjs [--skip-cdp] [--skip-tests]
 */
import { execSync } from 'child_process';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { nextTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { alertNotification, opsSuccessAlert } from './lib/notification_alert.mjs';

loadEnv();

const NODE = process.execPath;
const SKIP_CDP = process.argv.includes('--skip-cdp');
const SKIP_TESTS = process.argv.includes('--skip-tests');

const steps = [];
function run(label, cmd, { optional = false } = {}) {
  console.log(`\n▶  ${label}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 600_000 });
    steps.push({ label, ok: true });
    return true;
  } catch (e) {
    steps.push({ label, ok: false, error: e.message?.slice(0, 120) });
    if (!optional) return false;
    console.log(`⚠️  ${label} skipped`);
    return false;
  }
}

console.log('\n═══ EGX Production Ready (full gate) ═══');
console.log(`Cairo: ${cairoDateParts().date}`);

if (!run('Automation verify', `"${NODE}" scripts/egx_automation_verify.mjs`)) process.exit(1);
if (!run('Session ready (today)', `"${NODE}" scripts/egx_session_ready.mjs`)) process.exit(1);
run('Session ready (next)', `"${NODE}" scripts/egx_session_ready.mjs --next`, { optional: true });
run('Cron log check', `"${NODE}" scripts/egx_cron_log_check.mjs --hours 48`, { optional: true });
if (!run('Delivery reconcile', `"${NODE}" scripts/egx_notify_reconcile.mjs`, { optional: true })) {
  console.log('⚠️  Pending deliveries — run: npm run egx:notify:recovery');
}
if (!run('Production acceptance', `"${NODE}" scripts/egx_production_acceptance.mjs`)) process.exit(1);

const verifyFlags = [
  SKIP_TESTS ? '--skip-tests' : '',
  SKIP_CDP ? '--skip-cdp' : '',
].filter(Boolean).join(' ');
if (!run('Full stack verify', `"${NODE}" scripts/egx_full_verify.mjs ${verifyFlags}`)) process.exit(1);

const digest = buildDeliveryDigest(latestOhlcvDate());
const nxt = nextTradingDay(cairoDateParts().date);
const fail = steps.filter(s => !s.ok).length;

const report = {
  at: new Date().toISOString(),
  pass: fail === 0,
  steps,
  digest,
  next_session: nxt.next_trading_day,
  cairo_date: cairoDateParts().date,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/prod_ready_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ Summary ═══');
for (const s of steps) console.log(`  ${s.ok ? '✅' : '❌'} ${s.label}`);
console.log(`\nDelivery: ${digest.reconcile} | Next session: ${nxt.next_trading_day}`);
console.log(`\n=== Production Ready: ${steps.length - fail}/${steps.length} PASS ===\n`);

if (fail) {
  alertNotification('PROD_READY_FAIL', {
    failed: steps.filter(s => !s.ok).map(s => s.label),
    digest,
  });
} else {
  await opsSuccessAlert('PROD_READY_OK', { ...digest, next_session: nxt.next_trading_day });
}

process.exit(fail ? 1 : 0);
