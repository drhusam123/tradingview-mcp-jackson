#!/usr/bin/env node
/**
 * EGX production runbook — what runs when, current status.
 * Usage: node scripts/egx_runbook.mjs [--next]  # --next = focus next trading session
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { cairoDateParts, isTradingDay, nextTradingDay } from './lib/egx_calendar.mjs';
import { latestOhlcvDate, wasAlreadySent } from './lib/delivery_audit.mjs';

loadEnv();

const NEXT = process.argv.includes('--next');
const cairo = cairoDateParts();
const ohlcv = latestOhlcvDate();
const session = NEXT ? nextTradingDay(cairo.date).next_trading_day : (ohlcv || cairo.date);

console.log('\n═══ EGX Production Runbook ═══');
console.log(`Cairo now: ${cairo.date} ${String(cairo.hour).padStart(2, '0')}:${String(cairo.minute).padStart(2, '0')}`);
console.log(`Session focus: ${session}${NEXT ? ' (next trading day)' : ' (latest OHLCV)'}`);

try {
  const cal = isTradingDay(session);
  console.log(`Trading day: ${cal.is_trading_day ? 'YES' : 'NO'}${cal.holiday_name ? ` — ${cal.holiday_name}` : ''}`);
} catch { /* */ }

const sent = wasAlreadySent(session);
console.log(`Delivery status: ${sent.duplicate ? `SENT (${sent.reason})` : 'NOT SENT YET'}`);

console.log('\n── Automated cron (Sun–Thu Cairo) ──');
const schedule = [
  ['05:15', 'egx_full_verify --skip-tests --skip-cdp', 'logs/full_verify.log'],
  ['07:00', 'egx:prod:status', 'logs/prod_status.log'],
  ['07:10', 'egx_session_ready (upstream+cron)', 'logs/session_ready.log'],
  ['07:15', 'egx_cron_log_check (48h scan)', 'logs/cron_log_check.log'],
  ['10:30', 'fetch_intraday_live (quotes+DOM)', 'logs/tv_live.log'],
  ['13:15', 'fetch_intraday_live (quotes+DOM)', 'logs/tv_live.log'],
  ['16:30', 'egx_tv_auto_update --launch --pine --tech', 'logs/tv_auto_daily.log'],
  ['17:20', 'egx_telegram_cron (prepare→send→reconcile)', 'logs/telegram.log'],
  ['17:45', 'egx_post_session_ops (reconcile+verify)', 'logs/post_session.log'],
];
console.log('  (Sun) 06:45  egx_prod_ready (weekly full gate) → logs/prod_ready.log');
for (const [t, job, log] of schedule) console.log(`  ${t}  ${job}\n         → ${log}`);

console.log('\n── Manual commands ──');
console.log('  npm run egx:prod:ready        # 7-step production gate');
console.log('  npm run egx:automation:status # runbook + digest + log scan');
console.log('  npm run egx:session:ready     # pre-session check');
console.log('  npm run egx:session:next      # next trading day check');
console.log('  npm run egx:verify:all        # full stack verify (+ CDP)');
console.log('  npm run egx:notify:daily-ops  # reconcile + safety + dry-run');
console.log('  npm run egx:prod:prepare-send # before manual send');
console.log('  npm run egx:prod:send         # manual live send');

const vPath = join(PROJECT_ROOT, 'data/full_verify_last.json');
if (existsSync(vPath)) {
  const v = JSON.parse(readFileSync(vPath, 'utf8'));
  console.log(`\nLast verify: ${v.at?.slice(0, 19)} → ${v.pass ? 'PASS' : 'FAIL'} (${v.total - v.failed}/${v.total})`);
}

const sPath = join(PROJECT_ROOT, 'data/session_ready_last.json');
if (existsSync(sPath)) {
  const s = JSON.parse(readFileSync(sPath, 'utf8'));
  console.log(`Session ready: ${s.date} → ${s.pass ? 'PASS' : 'FAIL'} (${s.passed}/${s.total})`);
}

const rPath = join(PROJECT_ROOT, 'data/prod_ready_last.json');
if (existsSync(rPath)) {
  const r = JSON.parse(readFileSync(rPath, 'utf8'));
  const p = r.steps?.filter(x => x.ok).length ?? 0;
  const n = r.steps?.length ?? 0;
  console.log(`Prod ready: ${r.at?.slice(0, 19)} → ${r.pass ? 'PASS' : 'FAIL'} (${p}/${n}) | next ${r.next_session ?? '—'}`);
}

console.log('');
