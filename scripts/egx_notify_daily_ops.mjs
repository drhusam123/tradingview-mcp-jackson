#!/usr/bin/env node
/**
 * Daily notification ops — reconcile → health → dry-run (no live send).
 * Usage: node scripts/egx_notify_daily_ops.mjs [--date YYYY-MM-DD]
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const reportDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

function run(cmd, label) {
  console.log(`\n▶  ${label}`);
  execSync(cmd, { cwd: ROOT, stdio: 'inherit' });
}

console.log('\n═══ EGX Notify Daily Ops ═══');
console.log(`Report date: ${reportDate}`);

let reconcileOk = true;
try {
  run(`"${NODE}" scripts/egx_notify_reconcile.mjs`, 'Reconcile actionable vs audit (14d)');
} catch {
  reconcileOk = false;
  console.log('⚠️  Reconcile found pending deliveries');
  try {
    execSync(`"${NODE}" scripts/egx_notify_recovery.mjs`, { cwd: ROOT, stdio: 'inherit' });
  } catch {
    console.log('   Run: npm run egx:notify:recovery -- --send (with EGX_AUTO_BACKFILL=1)');
  }
}

run(`"${NODE}" scripts/egx_decision_bot.mjs --date ${reportDate}`, 'Safety decision bot');
run(`"${NODE}" scripts/egx_notify_health.mjs --date ${reportDate}`, 'Health check');
run(`"${NODE}" scripts/egx_notify_dry_run.mjs --date ${reportDate}`, 'Dry-run send decision');
run(`"${NODE}" scripts/egx_notification_pipeline_audit.mjs --date ${reportDate}`, 'Pipeline audit');

console.log('\n═══ Daily Ops Complete ═══\n');
