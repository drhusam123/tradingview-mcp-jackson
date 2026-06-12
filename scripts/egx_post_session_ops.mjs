#!/usr/bin/env node
/**
 * Post-session ops — runs after Telegram cron as safety net.
 * reconcile → verify → alert on gaps.
 *
 * Cron: 45 15 * * 0-4 (5:45 PM Cairo)
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
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

const signalDate = latestOhlcvDate() || today;
const steps = [];

console.log('\n═══ EGX Post-Session Ops ═══');
console.log(`Date: ${signalDate}`);

function runStep(name, cmd, { optional = false, timeout = 600_000 } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout });
    steps.push({ name, ok: true, ms: Date.now() - t0 });
    return true;
  } catch (e) {
    steps.push({ name, ok: false, ms: Date.now() - t0, optional, error: e.message?.slice(0, 120) });
    if (!optional) throw e;
    console.log(`  ⚠️  ${name}: ${e.message?.slice(0, 80)}`);
    return false;
  }
}

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

const mlOk = runStep(
  'ml_refresh',
  `"${NODE}" scripts/egx_ml_boost.mjs --skip-ensemble --date ${signalDate}`,
  { optional: true, timeout: 900_000 },
);
if (!mlOk) {
  alertNotification('POST_SESSION_ML_REFRESH_FAIL', { date: signalDate, signal_date: signalDate });
}

try {
  execSync(`"${NODE}" scripts/egx_closed_loop.mjs`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 360_000 });
  steps.push({ name: 'closed_loop', ok: true });
} catch (e) {
  steps.push({ name: 'closed_loop', ok: false, error: e.message?.slice(0, 80) });
  console.log(`⚠️  Closed loop: ${e.message?.slice(0, 80)}`);
}

try {
  execSync(`"${NODE}" scripts/egx_loop_audit.mjs`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 60_000 });
} catch (e) {
  console.log(`⚠️  Loop audit: ${e.message?.slice(0, 80)}`);
}

try {
  execSync(`"${NODE}" scripts/egx_p6_sync.mjs --light`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 300_000 });
} catch (e) {
  console.log(`⚠️  P6 sync: ${e.message?.slice(0, 80)}`);
}

try {
  execSync(`"${NODE}" scripts/egx_p6_delivered_orchestrator.mjs`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 60_000 });
} catch (e) {
  console.log(`⚠️  P6 delivered orchestrator: ${e.message?.slice(0, 80)}`);
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

const digest = { ...buildDeliveryDigest(signalDate), proof_loop: proof };
const mlBoost = existsSync(join(PROJECT_ROOT, 'data/ml_boost_last.json'))
  ? JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/ml_boost_last.json'), 'utf8'))
  : null;
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/post_session_last.json'), JSON.stringify({
  at: new Date().toISOString(),
  signal_date: signalDate,
  steps,
  ml_boost: mlBoost,
  digest,
}, null, 2));

await opsSuccessAlert('POST_SESSION_OK', digest);

console.log('\n═══ Post-Session OK ═══\n');
