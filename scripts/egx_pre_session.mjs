#!/usr/bin/env node
/**
 * Pre-session bundle — data layer + signal funnel + readiness before EGX open.
 *
 * Usage:
 *   npm run egx:pre:session
 *   npm run egx:pre:session -- --next
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { cairoDateParts, nextTradingDay } from './lib/egx_calendar.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { alertNotification } from './lib/notification_alert.mjs';

loadEnv();

const NODE = process.execPath;
const useNext = process.argv.includes('--next');
const steps = [];

function run(name, cmd, { optional = false, timeout = 600_000 } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout });
    steps.push({ name, ok: true, ms: Date.now() - t0 });
    return true;
  } catch (e) {
    steps.push({ name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 120), optional });
    if (!optional) return false;
    console.log(`  ⚠️  ${name}: ${e.message?.slice(0, 80)}`);
    return false;
  }
}

console.log('\n═══ EGX Pre-Session Bundle ═══\n');

const nextFlag = useNext ? ' --next' : '';
const hardOk = [
  run('migrations', `"${NODE}" scripts/migrations/migrate.mjs --check`, { timeout: 60_000 }),
  run('data_layer_audit', `"${NODE}" scripts/egx_data_layer_audit.mjs`, { timeout: 120_000 }),
  run('session_ready', `"${NODE}" scripts/egx_session_ready.mjs${nextFlag}`, { timeout: 120_000 }),
].every(Boolean);

const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const cairo = cairoDateParts();
const gateSimDate = latestOhlcvDate() || (useNext ? nextTradingDay(cairo.date).next_trading_day : cairo.date);

run('architecture_audit', `"${NODE}" scripts/egx_architecture_audit.mjs`, { optional: true });
run('signals_diagnose', `"${NODE}" scripts/egx_signal_funnel.mjs`, { optional: true });
run(
  'gate_simulate',
  `"${PYTHON}" scripts/python/gate_actionable_simulate.py simulate '{"date":"${gateSimDate}"}'`,
  { optional: true },
);
run('verify_fast', 'npm run egx:verify:fast', { optional: true, timeout: 300_000 });
run(
  'client_message_audit',
  `"${NODE}" scripts/egx_client_message_audit.mjs --date ${gateSimDate}`,
  { optional: true, timeout: 180_000 },
);
run('runbook', `"${NODE}" scripts/egx_runbook.mjs${useNext ? ' --next' : ''}`, { optional: true });

const report = {
  at: new Date().toISOString(),
  next_mode: useNext,
  pass: hardOk,
  steps,
  data_audit: existsSync(join(PROJECT_ROOT, 'data/data_layer_audit_last.json'))
    ? JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/data_layer_audit_last.json'), 'utf8'))
    : null,
  signal_funnel: existsSync(join(PROJECT_ROOT, 'data/signal_funnel_last.json'))
    ? JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/signal_funnel_last.json'), 'utf8'))
    : null,
  session_ready: existsSync(join(PROJECT_ROOT, 'data/session_ready_last.json'))
    ? JSON.parse(readFileSync(join(PROJECT_ROOT, 'data/session_ready_last.json'), 'utf8'))
    : null,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/pre_session_last.json'), JSON.stringify(report, null, 2));

const fail = steps.filter(s => !s.ok && !s.optional).length;
console.log(`\n═══ Pre-Session: ${steps.length - fail}/${steps.length} OK | L0 hard gate: ${hardOk ? 'PASS' : 'FAIL'} ═══\n`);
if (!hardOk) {
  alertNotification('PRE_SESSION_FAIL', {
    next_mode: useNext,
    gate_date: gateSimDate,
    failed: steps.filter(s => !s.ok && !s.optional).map(s => s.name),
  });
}
process.exit(hardOk ? 0 : 1);
