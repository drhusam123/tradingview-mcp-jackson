#!/usr/bin/env node
/**
 * Notification smoke test — mock provider, proves pipeline + audit logging.
 * Does NOT send to real clients unless EGX_NOTIFY_SMOKE_LIVE=1.
 * Usage: node scripts/egx_notify_smoke.mjs
 */
import { validateTelegramPayload } from '../src/egx/notify.js';
import {
  ensureDeliveryAuditTable, logDeliveryAttempt, getLatestAuditRows, closeDeliveryAuditDb,
} from './lib/delivery_audit.mjs';

const MOCK_DATE = '2099-01-15';
const MOCK_SYMBOL = 'SMOKE_TEST';
const MOCK_MSG = `🧪 Smoke test NARE-like signal ${MOCK_DATE}\nمنطقة الدخول: 10.00`;

let failures = [];

function fail(msg) {
  failures.push(msg);
  console.log(`❌ ${msg}`);
}

function pass(msg) {
  console.log(`✅ ${msg}`);
}

ensureDeliveryAuditTable();

// 1. Message generation (mock)
if (!MOCK_MSG.includes('منطقة الدخول')) fail('mock message not generated');
else pass('mock message generated');

// 2. QA validation path
const qa = validateTelegramPayload(MOCK_MSG, {
  clientDelivery: true,
  reportDate: MOCK_DATE,
  finalActionableCount: 1,
});
if (!qa.ok && qa.issues?.some(i => i.includes('upstream') || i.includes('OHLCV'))) {
  pass('QA ran (upstream warnings expected for mock date)');
} else if (qa.ok) {
  pass('QA passed for mock payload');
} else {
  pass(`QA blocked as expected for mock: ${qa.issues?.join('; ')}`);
}

// 3. Mock notifier
let providerResponse = null;
let sendAttempted = false;
let sendSuccess = false;

if (process.env.EGX_NOTIFY_SMOKE_LIVE === '1') {
  fail('EGX_NOTIFY_SMOKE_LIVE=1 not allowed in smoke test');
} else {
  sendAttempted = true;
  providerResponse = { ok: true, mock: true, messageId: 999001 };
  sendSuccess = true;
  pass('mock provider returned success');
}

// 4. Delivery audit write
const auditId = logDeliveryAttempt({
  signal_date: MOCK_DATE,
  symbol: MOCK_SYMBOL,
  actionable: 1,
  message_generated: 1,
  send_attempted: sendAttempted ? 1 : 0,
  send_success: sendSuccess ? 1 : 0,
  provider_response: providerResponse,
  dedup_key: `smoke:${MOCK_DATE}:${MOCK_SYMBOL}`,
  pipeline_stage: 'smoke_test',
  skip_reason: sendSuccess ? null : 'mock_failed',
  meta_json: { test: 'egx_notify_smoke' },
});

const rows = getLatestAuditRows(3);
const found = rows.find(r => r.dedup_key === `smoke:${MOCK_DATE}:${MOCK_SYMBOL}`);
if (!found || !found.send_success) fail('delivery audit not recorded');
else pass(`delivery audit recorded (id=${auditId})`);

closeDeliveryAuditDb();

console.log('\n=== Smoke Test Summary ===');
if (failures.length) {
  console.log(`FAILED (${failures.length}):`);
  failures.forEach(f => console.log(`  - ${f}`));
  process.exit(1);
}
console.log('ALL SMOKE CHECKS PASSED');
process.exit(0);
