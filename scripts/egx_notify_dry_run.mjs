#!/usr/bin/env node
/**
 * Notification dry-run — shows what WOULD be sent without sending.
 * Usage: node scripts/egx_notify_dry_run.mjs [--date YYYY-MM-DD]
 */
import { pythonTgFormatDaily } from '../src/egx/index.js';
import { validateTelegramPayload, isTelegramConfigured } from '../src/egx/notify.js';
import {
  countActionable, upstreamIssues, latestOhlcvDate, logDeliveryAttempt,
} from './lib/delivery_audit.mjs';

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const reportDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

const act = countActionable(reportDate);
const upstream = upstreamIssues(reportDate);

console.log('\n=== EGX Notification Dry-Run ===');
console.log(`Report date: ${reportDate}`);
console.log(`Telegram configured: ${isTelegramConfigured()}`);
console.log(`\nActionable in DB: ${act.db}`);
console.log(`Deliverable (quality_gate_passed=true): ${act.deliverable}`);
console.log(`Symbols: ${act.symbols.join(', ') || '(none)'}`);

if (upstream.length) {
  console.log(`\n⚠️  Upstream blockers (live send would fail QA):`);
  upstream.forEach(u => console.log(`   - ${u}`));
} else {
  console.log('\n✅ Upstream alignment OK');
}

let formatResult;
try {
  formatResult = await pythonTgFormatDaily({ report_date: reportDate });
} catch (e) {
  console.log(`\n❌ Format failed: ${e.message}`);
  logDeliveryAttempt({
    signal_date: reportDate,
    actionable: act.db > 0,
    message_generated: 0,
    send_attempted: 0,
    send_success: 0,
    skip_reason: `format_exception:${e.message}`,
    pipeline_stage: 'dry_run_format',
    dedup_key: `dry_run:${reportDate}`,
  });
  process.exit(1);
}

if (formatResult.error) {
  console.log(`\n❌ Format error: ${formatResult.error}`);
  process.exit(1);
}

const messages = formatResult.messages || [];
const topSymbols = formatResult.top_symbols || [];
const finalCount = Number(formatResult.final_actionable_count ?? act.deliverable);

console.log(`\nFormatter output:`);
console.log(`  messages: ${messages.length}`);
console.log(`  final_actionable_count: ${finalCount}`);
console.log(`  top_symbols: ${topSymbols.join(', ') || '(none)'}`);
if (formatResult.formatter_diagnostics) {
  console.log(`  diagnostics: ${JSON.stringify(formatResult.formatter_diagnostics)}`);
}

const decisions = [];
for (const sym of act.symbols) {
  const inTop = topSymbols.includes(sym);
  const wouldInclude = inTop && finalCount > 0;
  decisions.push({
    symbol: sym,
    actionable_db: true,
    in_telegram_top: inTop,
    would_send_to_client: wouldInclude,
    reason: wouldInclude ? 'included in client message' : (inTop ? 'formatter excluded' : 'filtered by telegram_report (qg/structure)'),
  });
}

console.log('\n=== Per-Symbol Decision ===');
if (decisions.length === 0) {
  console.log('  (no actionable signals — market brief only)');
} else {
  for (const d of decisions) {
    console.log(`  ${d.symbol}: send=${d.would_send_to_client ? 'YES' : 'NO'} — ${d.reason}`);
  }
}

let wouldSendLive = false;
let blockReasons = [];

if (!isTelegramConfigured()) blockReasons.push('TELEGRAM not configured');
if (upstream.length) blockReasons.push(...upstream);
if (messages.length === 0) blockReasons.push('no messages generated');

for (let i = 0; i < messages.length; i++) {
  const qa = validateTelegramPayload(messages[i], {
    clientDelivery: true,
    reportDate,
    finalActionableCount: finalCount,
  });
  if (!qa.ok) {
    blockReasons.push(`msg${i + 1} QA: ${qa.issues.join('; ')}`);
  }
}

wouldSendLive = blockReasons.length === 0 && messages.length > 0;

console.log('\n=== Send Decision ===');
console.log(`Would attempt live send: ${wouldSendLive ? 'YES' : 'NO'}`);
if (blockReasons.length) {
  console.log('Blockers:');
  blockReasons.forEach(b => console.log(`  - ${b}`));
}
console.log(`Recipient: ${process.env.TELEGRAM_CHAT_ID || '(not set)'}`);

logDeliveryAttempt({
  signal_date: reportDate,
  actionable: act.db > 0,
  message_generated: messages.length > 0 ? 1 : 0,
  send_attempted: 0,
  send_success: 0,
  skip_reason: wouldSendLive ? 'dry_run_no_send' : blockReasons.join(' | ') || 'no_messages',
  pipeline_stage: 'dry_run',
  dedup_key: `dry_run:${reportDate}`,
  meta_json: { act, topSymbols, finalCount, decisions, blockReasons },
});

console.log('\n(Dry-run complete — no messages sent)\n');
process.exit(wouldSendLive || act.db === 0 ? 0 : 2);
