#!/usr/bin/env node
/**
 * Scan production cron logs for recent failures → ops alert.
 * Usage: node scripts/egx_cron_log_check.mjs [--hours 24]
 */
import { existsSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { alertNotification } from './lib/notification_alert.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';

loadEnv();

const hoursArg = process.argv.find((a, i) => process.argv[i - 1] === '--hours');
const HOURS = Number(hoursArg || 24);
const cutoff = Date.now() - HOURS * 3_600_000;

function recentLines(rel, maxLines = 150) {
  const path = join(PROJECT_ROOT, rel);
  if (!existsSync(path)) return [];
  const st = statSync(path);
  if (st.mtimeMs < cutoff) return [];
  return readFileSync(path, 'utf8').split('\n').slice(-maxLines);
}

function scan(name, rel, isBad) {
  const hits = [];
  for (const line of recentLines(rel)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (isBad(trimmed)) hits.push({ log: name, line: trimmed.slice(0, 240) });
  }
  return hits;
}

const issues = [
  ...scan('telegram', 'logs/telegram.log', l =>
    /Telegram Cron FAILED/.test(l) || /CRON_FAILED/.test(l)),
  ...scan('tv_sync', 'logs/tv_auto_daily.log', l =>
    /\[tv-auto\] fatal:/.test(l)),
  ...scan('post_session', 'logs/post_session.log', l =>
    /⛔ Pending deliveries/.test(l) || /POST_SESSION_VERIFY_FAIL/.test(l)),
  ...scan('full_verify', 'logs/full_verify.log', l => {
    const m = l.match(/Full Verify: (\d+)\/(\d+) PASS/);
    return m && Number(m[1]) < Number(m[2]);
  }),
  ...scan('session_ready', 'logs/session_ready.log', l => {
    const m = l.match(/Session Ready \([^)]+\): (\d+)\/(\d+)/);
    return m && Number(m[1]) < Number(m[2]);
  }),
  ...scan('prod_ready', 'logs/prod_ready.log', l => {
    const m = l.match(/Production Ready: (\d+)\/(\d+) PASS/);
    return m && Number(m[1]) < Number(m[2]);
  }),
];

console.log('\n═══ EGX Cron Log Check ═══');
console.log(`Window: last ${HOURS}h | Cairo: ${cairoDateParts().date}`);

if (!issues.length) {
  console.log('✅ No cron failures in recent logs\n');
  process.exit(0);
}

console.log(`❌ ${issues.length} issue(s):`);
for (const i of issues) console.log(`  [${i.log}] ${i.line}`);

alertNotification('CRON_LOG_FAILURES', {
  hours: HOURS,
  count: issues.length,
  samples: issues.slice(0, 5),
});

console.log('');
process.exit(1);
