#!/usr/bin/env node
/**
 * Reconcile actionable signals vs delivery audit.
 * Usage: node scripts/egx_notify_reconcile.mjs [--days 14]
 */
import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { DB_PATH, getAuditForDate } from './lib/delivery_audit.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const daysArg = process.argv.find((a, i) => process.argv[i - 1] === '--days');
const DAYS = Number(daysArg || 14);

const db = new Database(DB_PATH, { readonly: true });
const signals = db.prepare(`
  SELECT trade_date AS date, symbol
  FROM final_signals
  WHERE actionable=1 AND veto_reason IS NULL
    AND trade_date >= date('now', ?)
    AND trade_date NOT LIKE '2099-%'
  ORDER BY trade_date DESC, symbol
`).all(`-${DAYS} days`);

const byDate = new Map();
for (const s of signals) {
  if (!byDate.has(s.date)) byDate.set(s.date, []);
  byDate.get(s.date).push(s.symbol);
}

console.log('\n=== EGX Delivery Reconciliation ===\n');
const rows = [];
for (const [date, symbols] of [...byDate.entries()].sort().reverse()) {
  const audit = getAuditForDate(date);
  const live = audit.find(a => a.send_success === 1 && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage));
  const status = live ? 'SENT' : 'NOT_SENT';
  const provider = live?.provider_response ? JSON.parse(live.provider_response).messageId : null;
  rows.push({ date, symbols, status, messageId: provider, stage: live?.pipeline_stage });
  console.log(`${date} | ${symbols.join(', ')} | ${status}${live ? ` (${live.pipeline_stage} id=${live.id})` : ''}`);
}

const unsent = rows.filter(r => r.status === 'NOT_SENT');
console.log(`\nSummary: ${rows.length} signal-days | ${rows.length - unsent.length} sent | ${unsent.length} pending`);
if (unsent.length) {
  console.log('\nPending backfill:');
  unsent.forEach(u => console.log(`  npm run egx:notify:backfill -- --date ${u.date} --dry-run`));
}
db.close();
process.exit(unsent.length ? 2 : 0);
