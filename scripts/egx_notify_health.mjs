#!/usr/bin/env node
/**
 * Notification health check — env, provider, actionable, delivery audit, pre-send.
 * Usage: node scripts/egx_notify_health.mjs [--date YYYY-MM-DD] [--send-test]
 */
import { isTelegramConfigured, telegramStatus, sendTelegram } from '../src/egx/notify.js';
import {
  countActionable, latestOhlcvDate, getLatestAuditRows, ensureDeliveryAuditTable,
  isPrepareStampValid, getUpstreamDates,
} from './lib/delivery_audit.mjs';
import { runPreSendCheck } from './lib/pre_send_check.mjs';
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const DRY_RUN_FORCED = process.env.EGX_NOTIFY_DRY_RUN === '1';
const SEND_TEST = process.argv.includes('--send-test');
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const reportDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

const lines = [];
const checks = [];

function record(ok, name, detail = '') {
  checks.push({ ok, name, detail });
  lines.push(`${ok ? '✅' : '❌'} ${name}${detail ? `: ${detail}` : ''}`);
}

ensureDeliveryAuditTable();

record(isTelegramConfigured(), 'Telegram configured', JSON.stringify(telegramStatus()));
record(Boolean(process.env.TELEGRAM_BOT_TOKEN), 'TELEGRAM_BOT_TOKEN set');
record(Boolean(process.env.TELEGRAM_CHAT_ID), 'TELEGRAM_CHAT_ID set', process.env.TELEGRAM_CHAT_ID ? `chat=${process.env.TELEGRAM_CHAT_ID}` : 'missing');
record(!DRY_RUN_FORCED, 'EGX_NOTIFY_DRY_RUN not forcing block', DRY_RUN_FORCED ? 'EGX_NOTIFY_DRY_RUN=1' : 'off');

const act = countActionable(reportDate);
record(act.deliverable >= 0, `Actionable DB (${reportDate})`, `${act.db} actionable, ${act.deliverable} deliverable: ${act.symbols.join(', ') || 'none'}`);

const upstream = getUpstreamDates();
record(upstream.ml_pred && upstream.ml_pred >= reportDate, 'ML prediction date', `latest=${upstream.ml_pred ?? 'none'} required=${reportDate}`);

const pre = runPreSendCheck(reportDate, { dryRun: true, skipMlRemediate: true, logBlock: false });
const alreadySent = pre.dedup?.reason === 'already_sent_live';
const preOk = pre.ok || alreadySent;
record(preOk, 'Pre-send check (dry)', alreadySent
  ? 'already_sent_live — delivery completed today'
  : (pre.ok ? 'all gates pass' : pre.blockers.join(' | ')));

const stamp = isPrepareStampValid(reportDate);
record(stamp.valid || act.deliverable === 0, 'Prepare stamp', stamp.valid ? `ok @ ${stamp.stamp.prepared_at}` : stamp.reason);

const legacyLog = join(ROOT, 'data/telegram_delivery_log.json');
if (existsSync(legacyLog)) {
  try {
    const log = JSON.parse(readFileSync(legacyLog, 'utf8'));
    const last = (log.deliveries || []).slice(-1)[0];
    record(true, 'Legacy delivery log', last ? `last=${last.date} sent=${last.messages_sent}` : 'empty');
  } catch (e) {
    record(false, 'Legacy delivery log parse', e.message);
  }
} else {
  record(false, 'Legacy delivery log', 'missing');
}

const auditRows = getLatestAuditRows(5);
record(auditRows.length > 0, 'Delivery audit table', auditRows.length ? `last stage=${auditRows[0].pipeline_stage} skip=${auditRows[0].skip_reason || 'none'}` : 'no rows yet');

let testSend = null;
if (SEND_TEST && isTelegramConfigured() && pre.ok) {
  testSend = await sendTelegram(
    `🩺 EGX notify health test ${reportDate}`,
    { clientDelivery: true, reportDate, finalActionableCount: act.deliverable },
  );
  record(testSend?.ok === true, 'Test message send', testSend?.error || `messageId=${testSend?.messageId}`);
} else if (SEND_TEST && !pre.ok) {
  record(false, 'Test message send', 'blocked by pre-send check');
} else if (SEND_TEST) {
  record(false, 'Test message send', 'Telegram not configured');
}

const allOk = checks.every(c => c.ok);
const report = {
  success: allOk,
  report_date: reportDate,
  dry_run_env: DRY_RUN_FORCED,
  checks,
  actionable: act,
  upstream,
  pre_send: pre,
  prepare_stamp: stamp,
  latest_audit: auditRows.slice(0, 3),
  test_send: testSend,
};

console.log('\n=== EGX Notification Health ===');
console.log(`Report date: ${reportDate}\n`);
lines.forEach(l => console.log(l));
console.log(`\nOverall: ${allOk ? 'HEALTHY' : 'ISSUES DETECTED'}`);
console.log(JSON.stringify(report, null, 2));
process.exit(allOk ? 0 : 1);
