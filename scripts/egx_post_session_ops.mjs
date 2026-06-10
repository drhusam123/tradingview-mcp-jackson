#!/usr/bin/env node
/**
 * Post-session ops — runs after Telegram cron as safety net.
 * reconcile → verify → alert on gaps.
 *
 * Cron: 45 15 * * 0-4 (5:45 PM Cairo)
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { isTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { alertNotification, opsSuccessAlert } from './lib/notification_alert.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { writeProofLoopSnapshot } from './lib/proof_loop.mjs';

loadEnv();

const NODE = process.execPath;
const FORCE = process.argv.includes('--force');
const today = cairoDateParts().date;

try {
  const cal = isTradingDay(today);
  if (!cal.is_trading_day && !FORCE) {
    console.log(`⏭  Post-session skip: not trading day (${cal.holiday_name || 'weekend'})`);
    process.exit(0);
  }
} catch (e) {
  console.log(`⚠️  Calendar check failed: ${e.message} — continuing`);
}

console.log('\n═══ EGX Post-Session Ops ═══');
console.log(`Date: ${latestOhlcvDate() || today}`);

let reconcileExit = 0;
try {
  execSync(`"${NODE}" scripts/egx_notify_reconcile.mjs`, { cwd: PROJECT_ROOT, stdio: 'inherit' });
} catch (e) {
  reconcileExit = e.status || 2;
}

if (reconcileExit !== 0) {
  alertNotification('POST_SESSION_RECONCILE_GAP', { date: today });
  if (process.env.EGX_AUTO_BACKFILL === '1') {
    console.log('\n▶  Auto-recovery (EGX_AUTO_BACKFILL=1)...');
    try {
      execSync(`"${NODE}" scripts/egx_notify_recovery.mjs --send`, { cwd: PROJECT_ROOT, stdio: 'inherit' });
    } catch {
      process.exit(2);
    }
  } else {
    console.error('\n⛔ Pending deliveries — run: npm run egx:notify:recovery');
    console.error('   Or set EGX_AUTO_BACKFILL=1 for automatic backfill\n');
    process.exit(2);
  }
}

const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
try {
  execSync(`"${PYTHON}" scripts/python/egx_outcome_tracker.py`, {
    cwd: PROJECT_ROOT,
    stdio: 'inherit',
    timeout: 120_000,
  });
} catch (e) {
  console.log(`⚠️  Outcome tracker: ${e.message?.slice(0, 80)}`);
}

let proof = null;
try {
  proof = writeProofLoopSnapshot();
  console.log(`📊 ${proof.n_completed} ULTRA completed | WR5 ${proof.win_rate ?? '—'}% | gate ${proof.gate_pass ? 'PASS' : proof.gate_reason}`);
} catch (e) {
  console.log(`⚠️  Proof loop: ${e.message?.slice(0, 80)}`);
}

try {
  execSync(`"${NODE}" scripts/egx_closed_loop.mjs`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 360_000 });
} catch (e) {
  console.log(`⚠️  Closed loop: ${e.message?.slice(0, 80)}`);
}

try {
  execSync(`"${NODE}" scripts/egx_full_verify.mjs --skip-tests --skip-cdp`, {
    cwd: PROJECT_ROOT,
    stdio: 'inherit',
  });
} catch {
  alertNotification('POST_SESSION_VERIFY_FAIL', { date: today });
  process.exit(1);
}

const digest = { ...buildDeliveryDigest(latestOhlcvDate() || today), proof_loop: proof };
await opsSuccessAlert('POST_SESSION_OK', digest);

console.log('\n═══ Post-Session OK ═══\n');
