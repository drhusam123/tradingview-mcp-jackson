#!/usr/bin/env node
/**
 * EGX Go-Live — pre-client-send checklist
 *
 *   node scripts/egx_go_live.mjs              # preflight + dry-run Telegram
 *   node scripts/egx_go_live.mjs --send       # preflight + live Telegram send
 *   node scripts/egx_go_live.mjs --update     # also run prepare-send first
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  latestOhlcvDate, isPrepareStampValid, logDeliveryAttempt, wasAlreadySent,
} from './lib/delivery_audit.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const SEND = process.argv.includes('--send');
const UPDATE = process.argv.includes('--update');
const FORCE = process.argv.includes('--force');
const DRY_RUN = !SEND;

function run(cmd, label) {
  console.log(`\n▶  ${label}`);
  execSync(cmd, { cwd: ROOT, stdio: 'inherit' });
}

const signalDate = latestOhlcvDate() || new Date().toISOString().slice(0, 10);

console.log('═══ EGX Go-Live Checklist ═══\n');
console.log(`Signal date: ${signalDate}`);

if (UPDATE && !wasAlreadySent(signalDate).duplicate) {
  run(`"${NODE}" scripts/egx_prod_prepare_send.mjs --date ${signalDate}`, 'Prepare send (score+ML+health)');
} else if (wasAlreadySent(signalDate).duplicate) {
  console.log('ℹ️  Already delivered today — skipping prepare-send');
}

if (SEND && !FORCE) {
  const stamp = isPrepareStampValid(signalDate);
  if (!stamp.valid) {
    console.error(`\n⛔ Prepare stamp invalid: ${stamp.reason}`);
    console.error('Run: npm run egx:prod:prepare-send');
    logDeliveryAttempt({
      signal_date: signalDate,
      skip_reason: `SEND_WITHOUT_PREPARE:${stamp.reason}`,
      pipeline_stage: 'send_guard',
      dedup_key: `send_guard:${signalDate}`,
      meta_json: stamp,
    });
    process.exit(4);
  }
  console.log(`✅ Prepare stamp valid (${stamp.stamp.prepared_at})`);
}

run(`"${NODE}" scripts/egx_preflight.mjs --skip-tests`, 'Pre-flight gates');
try {
  run(`"${NODE}" scripts/egx_notify_health.mjs --date ${signalDate}`, 'Notification health');
} catch {
  const sent = wasAlreadySent(signalDate);
  if (!sent.duplicate) throw new Error('Notification health failed');
  console.log(`ℹ️  Health: already sent today (${sent.reason}) — skipping duplicate live send`);
}

if (SEND && !FORCE && wasAlreadySent(signalDate).duplicate) {
  console.log('\n✅ Already delivered today — no duplicate send');
  process.exit(0);
}

const tgCmd = DRY_RUN
  ? `"${NODE}" scripts/egx_telegram_daily.mjs --dry-run`
  : `"${NODE}" scripts/egx_telegram_daily.mjs${FORCE ? ' --force' : ''}`;
run(tgCmd, DRY_RUN ? 'Telegram dry-run' : 'Send client Telegram');

console.log(`\n═══ Go-Live ${SEND ? 'SENT' : 'DRY-RUN OK'} ═══\n`);
