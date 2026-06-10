#!/usr/bin/env node
/**
 * Auto-recover pending client deliveries (optional live backfill).
 * Usage:
 *   node scripts/egx_notify_recovery.mjs           # dry-run only
 *   node scripts/egx_notify_recovery.mjs --send    # live backfill when EGX_AUTO_BACKFILL=1
 */
import { execSync } from 'child_process';
import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH, getAuditForDate } from './lib/delivery_audit.mjs';
import { alertNotification } from './lib/notification_alert.mjs';

loadEnv();

const NODE = process.execPath;
const SEND = process.argv.includes('--send');
const DAYS = Number(process.argv.find((a, i) => process.argv[i - 1] === '--days') || 14);
const autoBackfill = process.env.EGX_AUTO_BACKFILL === '1';

const db = new Database(DB_PATH, { readonly: true });
const signals = db.prepare(`
  SELECT trade_date AS date, symbol
  FROM final_signals
  WHERE actionable=1 AND veto_reason IS NULL
    AND trade_date >= date('now', ?)
    AND trade_date NOT LIKE '2099-%'
  ORDER BY trade_date DESC, symbol
`).all(`-${DAYS} days`);
db.close();

const byDate = new Map();
for (const s of signals) {
  if (!byDate.has(s.date)) byDate.set(s.date, []);
  byDate.get(s.date).push(s.symbol);
}

const pending = [];
for (const [date, symbols] of byDate) {
  const audit = getAuditForDate(date);
  const live = audit.find(a =>
    a.send_success === 1
    && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage),
  );
  if (!live) pending.push({ date, symbols });
}

console.log('\n=== EGX Delivery Recovery ===\n');
console.log(`Pending: ${pending.length} signal-day(s)`);
if (!pending.length) {
  console.log('Nothing to recover.\n');
  process.exit(0);
}

for (const p of pending) {
  console.log(`  ${p.date} | ${p.symbols.join(', ')}`);
}

if (!SEND) {
  console.log('\nDry-run — commands:');
  pending.forEach(p => console.log(`  npm run egx:notify:backfill -- --date ${p.date} --send`));
  console.log('\nOr: EGX_AUTO_BACKFILL=1 npm run egx:notify:recovery -- --send\n');
  process.exit(2);
}

if (!autoBackfill) {
  alertNotification('RECOVERY_BLOCKED', {
    pending: pending.map(p => p.date),
    reason: 'EGX_AUTO_BACKFILL not set to 1',
  });
  console.error('\n⛔ Live recovery blocked — set EGX_AUTO_BACKFILL=1 in .env\n');
  process.exit(3);
}

let ok = 0;
for (const p of pending) {
  console.log(`\n▶  Backfill ${p.date}`);
  try {
    execSync(`"${NODE}" scripts/egx_notify_backfill.mjs --date ${p.date} --send`, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 600_000,
    });
    ok += 1;
  } catch (e) {
    alertNotification('RECOVERY_BACKFILL_FAILED', { date: p.date, error: e.message?.slice(0, 300) });
    console.error(`❌  Backfill failed for ${p.date}`);
  }
}

console.log(`\n=== Recovery: ${ok}/${pending.length} sent ===\n`);
process.exit(ok === pending.length ? 0 : 1);
