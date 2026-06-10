#!/usr/bin/env node
/**
 * Weekly deep data quality audit — build_full (slow, full history).
 * Daily pipeline uses gate_daily (~1s); this runs Sunday only.
 *
 * Usage: node scripts/egx_quality_weekly.mjs [--force]
 */
import { spawnSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { isTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { alertNotification } from './lib/notification_alert.mjs';

loadEnv();

const FORCE = process.argv.includes('--force');
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const SCRIPT = join(PROJECT_ROOT, 'scripts/python/data_quality_gate.py');
const OUT = join(PROJECT_ROOT, 'data/quality_weekly_last.json');

const dow = new Date().getUTCDay();
if (dow !== 0 && !FORCE) {
  console.log('⏭  Weekly quality audit: Sunday only (use --force)');
  process.exit(0);
}

try {
  const cal = isTradingDay(cairoDateParts().date);
  if (!cal.is_trading_day && !FORCE) {
    console.log(`⏭  Skip: not trading week context (${cal.holiday_name || 'weekend'})`);
    process.exit(0);
  }
} catch { /* continue */ }

console.log('\n═══ EGX Weekly Data Quality (build_full) ═══');
console.log(`Started: ${new Date().toISOString()}`);
console.log('⚠️  Full-history scan — may take several minutes\n');

const t0 = Date.now();
const r = spawnSync(PYTHON, [SCRIPT, 'build_full', '{}'], {
  cwd: PROJECT_ROOT,
  encoding: 'utf8',
  timeout: 45 * 60 * 1000,
});

let result;
try {
  result = JSON.parse((r.stdout || '').trim());
} catch {
  console.error(r.stderr || r.stdout || 'build_full failed');
  alertNotification('QUALITY_WEEKLY_FAIL', { error: 'parse_error', stderr: r.stderr?.slice(0, 200) });
  process.exit(1);
}

const elapsed = Math.round((Date.now() - t0) / 1000);
const payload = {
  at: new Date().toISOString(),
  elapsed_sec: elapsed,
  system_status: result.system_status,
  avg_trust_score: result.avg_trust_score,
  n_critical_open: result.n_critical_open,
  n_open_issues: result.n_open_issues,
  tables_audited: result.tables_audited,
  success: result.success !== false,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(OUT, JSON.stringify(payload, null, 2));

console.log(`  Status : ${result.system_status}`);
console.log(`  Trust  : avg=${result.avg_trust_score} worst=${result.worst_trust_score}`);
console.log(`  Issues : ${result.n_open_issues} open | ${result.n_critical_open} critical`);
console.log(`  Time   : ${elapsed}s`);
console.log(`  Saved  : data/quality_weekly_last.json\n`);

if (result.system_status === 'CRITICAL') {
  alertNotification('QUALITY_WEEKLY_CRITICAL', payload);
  process.exit(1);
}

console.log('═══ Weekly Quality OK ═══\n');
