#!/usr/bin/env node
/**
 * Audit client Telegram message: next-session framing, buy vs watch, confidence basis.
 * Usage:
 *   npm run egx:client:message:audit
 *   npm run egx:client:message:audit -- --date 2026-06-11 --prep
 */
import { pythonTgFormatDaily } from '../src/egx/index.js';
import { countActionable, latestOhlcvDate } from './lib/delivery_audit.mjs';
import { buildClientFormatParams, resolvePrepMode } from './lib/client_message_prep.mjs';
import { runEgxSafetyCheck } from './lib/egx_safety_check.mjs';

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const PREP = resolvePrepMode();
const signalDate = dateArg || latestOhlcvDate();

if (!signalDate) {
  console.error('No signal date (pass --date YYYY-MM-DD or ensure OHLCV exists)');
  process.exit(2);
}

const act = countActionable(signalDate);
const prepBundle = buildClientFormatParams(signalDate, { prep: PREP });
const safety = prepBundle.safety || runEgxSafetyCheck(signalDate, { veto: true });
const targetSession = prepBundle.target_session_date;

const formatResult = await pythonTgFormatDaily(prepBundle.params);
const messages = formatResult.messages || [];
const msg2 = messages[1] || messages[0] || '';
const checks = [];

function pass(name, detail = '') {
  checks.push({ ok: true, name, detail });
}

function fail(name, detail = '') {
  checks.push({ ok: false, name, detail });
}

if (PREP) {
  if (/توصية الجلسة القادمة/.test(msg2)) pass('prep_title');
  else fail('prep_title', 'missing توصية الجلسة القادمة');

  if (msg2.includes(targetSession) || /الجلسة المستهدفة/.test(msg2)) {
    pass('target_session_date', targetSession);
  } else fail('target_session_date', `expected ${targetSession} in message`);

  if (/مبنية على إغلاق جلسة/.test(msg2)) pass('signal_date_reference');
  else fail('signal_date_reference', 'missing closure reference');

  if (safety.passed_symbols.length > 0) {
    if (/شراء مؤهل/.test(msg2)) pass('buy_section');
    else fail('buy_section', `expected buy section for ${safety.passed_symbols.join(',')}`);
    for (const sym of safety.passed_symbols) {
      if (msg2.includes(sym)) pass(`buy_symbol_${sym}`);
      else fail(`buy_symbol_${sym}`, 'not in message');
    }
  } else {
    pass('buy_section', 'no passed symbols — section optional');
  }

  const blocked = safety.blocked_symbols.filter(s => act.symbols.includes(s));
  if (blocked.length > 0) {
    if (/مراقبة — انتظر تأكيد الدخول/.test(msg2)) pass('watch_section');
    else fail('watch_section', `expected watch section for ${blocked.join(',')}`);
  }

  if (!/للدراسة والمراقبة فقط/.test(msg2)) pass('no_study_only_halt');
  else fail('no_study_only_halt', 'conflicting HALT study-only banner');

  if (/ثقة التوصية:/.test(msg2)) pass('confidence_line');
  else fail('confidence_line', 'missing ثقة التوصية');

  if (/درجة موحّدة|نموذج ML|ع\/خ/.test(msg2)) pass('confidence_basis');
  else fail('confidence_basis', 'missing basis breakdown');
} else {
  if (/أفضل فرص التداول|حالة فرص التداول/.test(msg2)) pass('daily_title');
  else fail('daily_title', 'missing standard title');
  if (/ثقة التوصية:/.test(msg2)) pass('confidence_line');
  else fail('confidence_line', 'missing ثقة التوصية');
}

const failed = checks.filter(c => !c.ok);
const ok = failed.length === 0;

console.log('\n=== EGX Client Message Audit ===');
console.log(`Signal date: ${signalDate} | Prep: ${PREP} | Target session: ${targetSession || 'n/a'}`);
console.log(`Actionable: ${act.deliverable} | Safety pass: ${safety.passed_symbols.join(',') || 'none'}`);
console.log(`Blocked: ${safety.blocked_symbols.join(',') || 'none'}`);
console.log(`Messages: ${messages.length} | msg2 chars: ${msg2.length}`);
console.log('');

for (const c of checks) {
  console.log(`${c.ok ? '✅' : '❌'} ${c.name}${c.detail ? `: ${c.detail}` : ''}`);
}

if (msg2) {
  console.log('\n--- Message 2 preview (first 1200 chars) ---');
  console.log(msg2.slice(0, 1200) + (msg2.length > 1200 ? '...' : ''));
}

console.log(`\n${ok ? 'PASS' : 'FAIL'} (${checks.length - failed.length}/${checks.length})\n`);
process.exit(ok ? 0 : 1);
