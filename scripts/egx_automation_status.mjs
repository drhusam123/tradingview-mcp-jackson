#!/usr/bin/env node
/**
 * Unified automation status — runbook + digest + verify + log check.
 * Usage: node scripts/egx_automation_status.mjs [--next] [--check-logs]
 */
import { execSync } from 'child_process';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';

loadEnv();

const NODE = process.execPath;
const NEXT = process.argv.includes('--next');
const CHECK_LOGS = process.argv.includes('--check-logs') || !process.argv.includes('--skip-logs');

console.log('\n═══ EGX Automation Status ═══\n');

try {
  execSync(`"${NODE}" scripts/egx_runbook.mjs ${NEXT ? '--next' : ''}`, {
    cwd: PROJECT_ROOT, stdio: 'inherit',
  });
} catch { /* runbook is informational */ }

const digest = buildDeliveryDigest(latestOhlcvDate());
console.log('\n── Delivery digest ──');
console.log(JSON.stringify(digest, null, 2));

if (CHECK_LOGS) {
  console.log('\n── Cron log check ──');
  try {
    execSync(`"${NODE}" scripts/egx_cron_log_check.mjs --hours 48`, {
      cwd: PROJECT_ROOT, stdio: 'inherit',
    });
  } catch {
    process.exit(1);
  }
}

console.log('');
