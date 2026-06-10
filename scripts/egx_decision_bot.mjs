#!/usr/bin/env node
/**
 * EGX Decision Bot — Jackson bot.js equivalent for EGX (no broker execution).
 * Evaluates deliverable signals against egx_rules.json + TRADING_LESSONS filters.
 *
 * Usage:
 *   node scripts/egx_decision_bot.mjs              # run safety check for latest date
 *   node scripts/egx_decision_bot.mjs --date YYYY-MM-DD
 *   node scripts/egx_decision_bot.mjs --summary    # activity summary (like bot.js --tax-summary)
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv } from './lib/load_env.mjs';
import { latestOhlcvDate, logDeliveryAttempt } from './lib/delivery_audit.mjs';
import { runEgxSafetyCheck, appendSafetyLog } from './lib/egx_safety_check.mjs';

loadEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const SUMMARY = process.argv.includes('--summary');
const signalDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

function printSummary() {
  try {
    execSync(`"${process.execPath}" scripts/egx_export_trades_csv.mjs --summary`, {
      cwd: ROOT,
      stdio: 'inherit',
    });
  } catch {
    console.log('No trade export data yet — run after portfolio closes.');
  }
}

if (SUMMARY) {
  printSummary();
  process.exit(0);
}

console.log('\n═══ EGX Decision Bot ═══');
console.log(`Date: ${signalDate} | Paper: ${process.env.EGX_PAPER_TRADING === 'true'}`);

const result = runEgxSafetyCheck(signalDate);
appendSafetyLog(result);

console.log(`\nActionable: ${result.actionable} | Deliverable before: ${result.deliverable_before}`);
console.log(`After safety: ${result.deliverable_after} | Passed: ${result.passed_symbols.join(', ') || 'none'}`);
if (result.blocked_symbols.length) {
  console.log(`Blocked: ${result.blocked_symbols.join(', ')}`);
}

for (const d of result.decisions) {
  const icon = d.decision === 'PASS' ? '✅' : '⛔';
  console.log(`  ${icon} ${d.symbol} — ${d.decision}${d.failed_conditions.length ? ` (${d.failed_conditions.join(', ')})` : ''}`);
  if (d.warnings?.length) d.warnings.forEach(w => console.log(`     ⚠️  ${w}`));
}

if (result.global_conditions.max_open_positions?.result === 'FAIL') {
  console.log(`\n⛔ Global: max open positions (${result.global_conditions.max_open_positions.actual}/${result.global_conditions.max_open_positions.threshold})`);
}

logDeliveryAttempt({
  signal_date: signalDate,
  actionable: result.actionable > 0,
  deliverable: result.deliverable_after > 0,
  skip_reason: result.ok ? null : `SAFETY_BLOCKED:${result.blocked_symbols.join(',')}`,
  pipeline_stage: result.ok ? 'safety_check_pass' : 'safety_check_blocked',
  dedup_key: `safety:${signalDate}`,
  meta_json: {
    passed: result.passed_symbols,
    blocked: result.blocked_symbols,
    paper: result.paper_trading,
  },
});

if (process.env.EGX_PORTFOLIO_AUTO === '1' && result.passed_symbols.length) {
  try {
    execSync(`"${PY}" scripts/python/portfolio_tracker.py import_signals`, {
      cwd: ROOT,
      stdio: 'inherit',
      timeout: 120_000,
    });
  } catch (e) {
    console.error(`Portfolio import skipped: ${e.message}`);
  }
}

console.log(`\n═══ Decision Bot ${result.ok ? 'PASS ✅' : 'BLOCKED ⛔'} ═══\n`);
process.exit(result.ok ? 0 : 2);
