#!/usr/bin/env node
/**
 * End-to-end notification pipeline forensic audit.
 * Usage: node scripts/egx_notification_pipeline_audit.mjs [--date YYYY-MM-DD]
 */
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { readFileSync, existsSync, writeFileSync, mkdirSync } from 'fs';
import {
  countActionable, upstreamIssues, latestOhlcvDate,
  getAuditForDate, ensureDeliveryAuditTable,
} from './lib/delivery_audit.mjs';
import { isTelegramConfigured } from '../src/egx/notify.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const reportDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

function py(cmd, params = {}) {
  const py3 = '/usr/bin/python3';
  const raw = execFileSync(py3, [
    join(ROOT, 'scripts/python/gate_doctor_audit.py'), cmd, JSON.stringify(params),
  ], { cwd: ROOT, timeout: 120_000 }).toString();
  return JSON.parse(raw.trim());
}

ensureDeliveryAuditTable();
const act = countActionable(reportDate);
const upstream = upstreamIssues(reportDate);
const audit = getAuditForDate(reportDate);

let gateSummary = null;
try {
  const rows = py('check_pending_outcomes', {});
  gateSummary = rows;
} catch { /* optional */ }

const legacyPath = join(ROOT, 'data/telegram_delivery_log.json');
let legacyLast = null;
if (existsSync(legacyPath)) {
  try {
    const log = JSON.parse(readFileSync(legacyPath, 'utf8'));
    legacyLast = (log.deliveries || []).filter(d => d.date === reportDate).slice(-1)[0]
      || (log.deliveries || []).slice(-1)[0];
  } catch { /* */ }
}

const diagnosis = [];
let rootCause = 'unknown';

if (act.db === 0) {
  rootCause = 'signal_generation';
  diagnosis.push('No actionable signals in final_signals for this date.');
  diagnosis.push('Check gates: npm run egx:gate:doctor:post-p0');
} else if (act.deliverable === 0) {
  rootCause = 'formatter_filter';
  diagnosis.push(`${act.db} actionable but 0 deliverable (quality_gate_passed filter).`);
  diagnosis.push('Fix: ensure score_all sets source_breakdown.quality_gate_passed=true');
} else if (upstream.length > 0) {
  rootCause = 'notification_upstream_block';
  diagnosis.push(`Signals exist (${act.symbols.join(', ')}) but live send QA blocks on upstream.`);
  upstream.forEach(u => diagnosis.push(`  - ${u}`));
} else if (audit.some(a => a.send_success === 1 && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage))) {
  rootCause = 'delivered';
  const ok = audit.filter(a => a.send_success === 1);
  diagnosis.push(`Delivery audit: ${ok.length} successful send(s) (${ok.map(a => a.pipeline_stage).join(', ')}).`);
} else if (audit.some(a => a.skip_reason?.includes('duplicate'))) {
  rootCause = 'notification_duplicate_guard';
  diagnosis.push('Duplicate same-day guard prevented resend.');
} else if (legacyLast?.messages_sent > 0 && legacyLast.date === reportDate) {
  rootCause = 'delivered';
  diagnosis.push(`Legacy log shows ${legacyLast.messages_sent} messages sent for ${legacyLast.date}.`);
} else {
  rootCause = 'notification_not_run_or_failed';
  diagnosis.push('Actionable signals exist but no successful delivery audit for this date.');
  diagnosis.push('Check cron: npm run egx:cron:show | verify egx:tg:daily ran');
}

const report = {
  success: true,
  report_date: reportDate,
  root_cause_category: rootCause,
  diagnosis,
  actionable: act,
  upstream_issues: upstream,
  telegram_configured: isTelegramConfigured(),
  delivery_audit_rows: audit.slice(0, 10),
  legacy_delivery: legacyLast,
  daily_answer: {
    signals_today: act.deliverable > 0,
    symbols: act.symbols,
    sent: rootCause === 'delivered',
    why_not: rootCause !== 'delivered' ? diagnosis : [],
  },
};

const outPath = join(ROOT, 'data/research_reports', `notification_pipeline_audit_${reportDate}.json`);
mkdirSync(join(ROOT, 'data/research_reports'), { recursive: true });
writeFileSync(outPath, JSON.stringify(report, null, 2));

console.log('\n=== Notification Pipeline Audit ===');
console.log(`Date: ${reportDate}`);
console.log(`Root cause: ${rootCause}`);
diagnosis.forEach(d => console.log(`  ${d}`));
console.log(`\nActionable: ${act.db} DB / ${act.deliverable} deliverable`);
console.log(`Report: ${outPath}`);
console.log(JSON.stringify(report.daily_answer, null, 2));
