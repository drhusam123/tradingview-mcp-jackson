#!/usr/bin/env node
/**
 * Safe backfill send for late-scored signals.
 * Usage:
 *   node scripts/egx_notify_backfill.mjs --date 2026-06-09 --dry-run
 *   node scripts/egx_notify_backfill.mjs --date 2026-06-09 --send
 */
import { pythonTgFormatDaily } from '../src/egx/index.js';
import { sendTelegram, validateTelegramPayload, isTelegramConfigured } from '../src/egx/notify.js';
import {
  countActionable, logDeliveryAttempt, wasAlreadySent, normalizeDeliverableSignals,
} from './lib/delivery_audit.mjs';
import { runPreSendCheck } from './lib/pre_send_check.mjs';

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const SEND = process.argv.includes('--send');
const FORCE = process.argv.includes('--force');
const PREP = process.argv.includes('--prep');
const signalDate = dateArg;

if (!signalDate) {
  console.error('Usage: egx_notify_backfill.mjs --date YYYY-MM-DD [--dry-run|--send] [--force] [--prep]');
  process.exit(2);
}

normalizeDeliverableSignals(signalDate);
const act = countActionable(signalDate);
const dup = wasAlreadySent(signalDate);

console.log('\n=== EGX Notification Backfill ===');
console.log(`Date: ${signalDate} (LATE BACKFILL)`);
console.log(`Mode: ${SEND ? 'LIVE SEND' : 'DRY-RUN'}${PREP ? ' (PREP — study bulletin before next session)' : ''}`);
console.log(`Actionable: ${act.db} | Deliverable: ${act.deliverable}`);
console.log(`Symbols: ${act.symbols.join(', ') || '(none)'}`);

if (dup.duplicate && !FORCE) {
  console.log(`\n⛔ Dedup block: ${dup.reason}`);
  logDeliveryAttempt({
    signal_date: signalDate,
    actionable: act.db > 0,
    deliverable: act.deliverable > 0,
    skip_reason: `BACKFILL_DEDUP:${dup.reason}`,
    pipeline_stage: 'backfill_skip',
    dedup_key: `backfill:${signalDate}`,
    meta_json: { late_backfill: true, dup },
  });
  process.exit(3);
}

const pre = runPreSendCheck(signalDate, {
  dryRun: !SEND,
  allowDuplicate: FORCE,
  skipLegacyDedup: true,
  logBlock: SEND,
  prepMode: PREP,
});

let formatResult;
try {
  formatResult = await pythonTgFormatDaily({ report_date: signalDate });
} catch (e) {
  console.error(`Format failed: ${e.message}`);
  process.exit(1);
}

const messages = formatResult.messages || [];
const finalCount = Number(formatResult.final_actionable_count ?? act.deliverable);
const topSymbols = formatResult.top_symbols || [];

console.log(`\nMessages: ${messages.length} | top: ${topSymbols.join(', ') || 'none'}`);

if (!SEND) {
  messages.forEach((msg, i) => {
    console.log(`\n--- Preview ${i + 1} (${msg.length} chars) ---`);
    console.log(msg.slice(0, 500) + (msg.length > 500 ? '...' : ''));
  });
  logDeliveryAttempt({
    signal_date: signalDate,
    actionable: act.db > 0,
    deliverable: act.deliverable > 0,
    message_generated: messages.length > 0 ? 1 : 0,
    dry_run: 1,
    skip_reason: 'backfill_dry_run',
    pipeline_stage: 'backfill_dry_run',
    dedup_key: `backfill_dry:${signalDate}`,
    meta_json: { late_backfill: true, pre_send: pre.checks, symbols: act.symbols },
  });
  console.log('\n(Backfill dry-run — no send)\n');
  process.exit(pre.ok ? 0 : 2);
}

if (!pre.ok) {
  console.error('\n⛔ Pre-send failed — live backfill blocked');
  pre.blockers.forEach(b => console.error(`   - ${b}`));
  process.exit(4);
}

if (!isTelegramConfigured()) {
  console.error('Telegram not configured');
  process.exit(5);
}

function isSignalMessage(msg, symbols) {
  const body = String(msg ?? '');
  return symbols.some(s => body.includes(s)) || /أفضل فرص التداول|منطقة الدخول/.test(body);
}

const sent = [];
for (let i = 0; i < messages.length; i++) {
  const msg = messages[i];
  const signalMsg = isSignalMessage(msg, act.symbols);
  const qa = validateTelegramPayload(msg, {
    clientDelivery: true,
    reportDate: signalDate,
    finalActionableCount: finalCount,
    backfillMode: true,
  });
  if (!qa.ok) {
    if (!signalMsg) {
      console.log(`ℹ️  Skip overview msg${i + 1} (backfill): ${qa.issues.join('; ')}`);
      logDeliveryAttempt({
        signal_date: signalDate,
        actionable: act.db > 0,
        deliverable: act.deliverable > 0,
        message_generated: 1,
        send_attempted: 0,
        send_success: 0,
        skip_reason: `BACKFILL_SKIP_OVERVIEW:${qa.issues.join('; ')}`,
        pipeline_stage: 'backfill_skip_overview',
        dedup_key: `backfill:${signalDate}:skip${i + 1}`,
        meta_json: { late_backfill: true, msg_index: i + 1 },
      });
      continue;
    }
    console.error(`QA block signal msg${i + 1}: ${qa.issues.join('; ')}`);
    logDeliveryAttempt({
      signal_date: signalDate,
      actionable: act.db > 0,
      deliverable: act.deliverable > 0,
      message_generated: 1,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `BACKFILL_QA:${qa.issues.join('; ')}`,
      pipeline_stage: 'backfill_qa_block',
      dedup_key: `backfill:${signalDate}:qa`,
      meta_json: { late_backfill: true, signal_msg: true },
    });
    process.exit(6);
  }
  const result = await sendTelegram(msg, {
    clientDelivery: true,
    reportDate: signalDate,
    finalActionableCount: finalCount,
    backfillMode: true,
  });
  const auditId = logDeliveryAttempt({
    signal_date: signalDate,
    symbol: topSymbols[i] ?? topSymbols[0] ?? null,
    actionable: act.db > 0,
    deliverable: act.deliverable > 0,
    message_generated: 1,
    send_attempted: 1,
    send_success: result?.ok ? 1 : 0,
    send_error: result?.error ?? null,
    provider_response: result?.ok ? { messageId: result.messageId } : null,
    pipeline_stage: 'backfill_send',
    dedup_key: `backfill:${signalDate}:msg${i + 1}`,
    meta_json: { late_backfill: true, msg_index: i + 1 },
  });
  if (!result?.ok) {
    console.error(`Send failed: ${result?.error}`);
    process.exit(7);
  }
  sent.push({ index: i + 1, auditId, messageId: result.messageId });
  console.log(`✅ Backfill message ${i + 1} sent (audit_id=${auditId})`);
}

if (sent.length === 0) {
  console.error('⛔ No backfill messages sent — signal QA failed or empty');
  process.exit(8);
}

console.log(`\n=== Backfill complete: ${sent.length} messages ===\n`);
