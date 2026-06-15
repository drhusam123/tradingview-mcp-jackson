#!/usr/bin/env node
/**
 * Send a one-off Telegram data-correction notice (e.g. after OHLCV repair).
 *
 * Usage:
 *   node scripts/egx_notify_data_correction.mjs --symbol ROTO --date 2026-06-14 --dry-run
 *   node scripts/egx_notify_data_correction.mjs --symbol ROTO --date 2026-06-14 --send
 */
import { sendTelegram, isTelegramConfigured } from '../src/egx/notify.js';
import { logDeliveryAttempt } from './lib/delivery_audit.mjs';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const SEND = process.argv.includes('--send');
const symbol = (() => {
  const i = process.argv.indexOf('--symbol');
  return i >= 0 ? process.argv[i + 1]?.toUpperCase() : null;
})();
const signalDate = (() => {
  const i = process.argv.indexOf('--date');
  return i >= 0 ? process.argv[i + 1] : null;
})();

if (!symbol || !signalDate) {
  console.error('Usage: egx_notify_data_correction.mjs --symbol SYM --date YYYY-MM-DD [--dry-run|--send]');
  process.exit(2);
}

const reason = process.argv.includes('--reason')
  ? process.argv[process.argv.indexOf('--reason') + 1]
  : 'خطأ في بيانات الأسعار التاريخية — تم سحب التوصية من قائمة الشراء المؤهل';

const text = [
  '⚠️ <b>تصحيح بيانات EGX</b>',
  `📅 جلسة: <code>${signalDate}</code>`,
  `📌 السهم: <b>${symbol}</b>`,
  `<i>${reason}</i>`,
  '',
  '✅ الإشارات الصالحة للجلسة: <b>EGCH</b>, <b>UEFM</b>',
  '<i>للأغراض المعلوماتية فقط · ليست نصيحة استثمارية</i>',
].join('\n');

console.log('\n=== EGX Data Correction Notice ===');
console.log(`Symbol: ${symbol} | Date: ${signalDate}`);
console.log(`Mode: ${SEND ? 'LIVE' : 'DRY-RUN'}\n`);
console.log(text);
console.log('');

if (!isTelegramConfigured()) {
  console.error('⛔ Telegram not configured');
  process.exit(1);
}

if (!SEND) {
  logDeliveryAttempt({
    signal_date: signalDate,
    symbol,
    actionable: 0,
    deliverable: 0,
    dry_run: 1,
    pipeline_stage: 'data_correction_preview',
    skip_reason: 'DRY_RUN',
    meta_json: { kind: 'data_correction', symbol, signalDate },
  });
  process.exit(0);
}

const res = await sendTelegram(text, { parse_mode: 'HTML', clientDelivery: true, reportDate: signalDate });
const ok = Boolean(res?.ok ?? res?.success);

logDeliveryAttempt({
  signal_date: signalDate,
  symbol,
  actionable: 0,
  deliverable: 0,
  message_generated: 1,
  send_attempted: 1,
  send_success: ok ? 1 : 0,
  send_error: ok ? null : res?.error,
  dry_run: 0,
  pipeline_stage: 'data_correction',
  dedup_key: `correction:${signalDate}:${symbol}`,
  meta_json: { kind: 'data_correction', symbol, signalDate, reason },
});

console.log(ok ? '✅ Correction sent' : `❌ Send failed: ${res?.error}`);
process.exit(ok ? 0 : 1);
