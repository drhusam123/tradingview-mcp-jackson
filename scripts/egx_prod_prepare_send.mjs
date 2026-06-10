#!/usr/bin/env node
/**
 * Prepare client send — score + ML refresh + health + dry-run (no live send).
 * Usage: node scripts/egx_prod_prepare_send.mjs [--date YYYY-MM-DD] [--skip-score]
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  latestOhlcvDate, countActionable, savePrepareStamp, logDeliveryAttempt,
  normalizeDeliverableSignals, wasAlreadySent,
} from './lib/delivery_audit.mjs';
import { ensureUpstreamFresh } from './lib/ensure_upstream_fresh.mjs';
import { runPreSendCheck } from './lib/pre_send_check.mjs';
import { runEgxSafetyCheck, appendSafetyLog } from './lib/egx_safety_check.mjs';
import { verifyActionableIndicatorCache } from './lib/indicator_cache_gate.mjs';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const PY = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const SKIP_SCORE = process.argv.includes('--skip-score');
const signalDate = dateArg || latestOhlcvDate() || new Date().toISOString().slice(0, 10);

function run(cmd, label) {
  console.log(`\n▶  ${label}`);
  try {
    execSync(cmd, { cwd: ROOT, stdio: 'inherit', timeout: 3_600_000 });
    return true;
  } catch (e) {
    console.error(`❌  ${label}: ${e.message}`);
    return false;
  }
}

console.log('\n═══ EGX Prepare Send ═══');
console.log(`Signal date: ${signalDate}`);

if (!SKIP_SCORE) {
  const scoreParams = JSON.stringify({ date: signalDate });
  run(`"${PY}" scripts/python/signal_integration.py score_all '${scoreParams}'`, 'score_all');
  run(`"${PY}" scripts/python/client_signal_promotion.py '${scoreParams}'`, 'client_signal_promotion');
}

normalizeDeliverableSignals(signalDate);
const upstream = ensureUpstreamFresh(signalDate, { autoRemediate: true, logAudit: true });
const act = countActionable(signalDate);

const cacheCheck = verifyActionableIndicatorCache(signalDate);
if (!cacheCheck.ok && cacheCheck.missing?.length) {
  console.log(`\n⚠️  Indicator cache missing for actionable: ${cacheCheck.missing.join(', ')}`);
  console.log('   Run: node scripts/rebuild_indicators.mjs');
}

const safety = runEgxSafetyCheck(signalDate);
appendSafetyLog(safety);

console.log(`\n📊 Actionable: ${act.db} | Deliverable: ${act.deliverable} | ${act.symbols.join(', ') || 'none'}`);
console.log(`🛡️  Safety: passed=${safety.passed_symbols.join(',') || 'none'} blocked=${safety.blocked_symbols.join(',') || 'none'}`);
if (safety.blocked_symbols.length) {
  safety.decisions.filter(d => d.decision === 'BLOCKED').forEach(d => {
    console.log(`   ⛔ ${d.symbol}: ${d.failed_conditions.join(', ')}`);
  });
}
if (!upstream.ok) {
  console.log('\n⛔ Upstream still stale after remediation:');
  upstream.issues?.forEach(i => console.log(`   - ${i}`));
  if (upstream.required_commands) {
    console.log('\nRequired commands:');
    upstream.required_commands.forEach(c => console.log(`   ${c}`));
  }
}

const pre = runPreSendCheck(signalDate, { dryRun: true, skipMlRemediate: true, logBlock: false });

let dryOk = false;
try {
  execSync(`"${NODE}" scripts/egx_notify_dry_run.mjs --date ${signalDate}`, { cwd: ROOT, stdio: 'inherit' });
  dryOk = pre.ok;
} catch {
  dryOk = false;
}

const alreadySent = wasAlreadySent(signalDate).duplicate;
const safetyOk = !safety.veto || safety.ok || act.deliverable === 0;
const ok = (pre.ok || alreadySent) && upstream.ok && (dryOk || alreadySent) && safetyOk;
const stamp = {
  signal_date: signalDate,
  prepared_at: new Date().toISOString(),
  ok,
  actionable: act.db,
  deliverable: act.deliverable,
  deliverable_after_safety: safety.deliverable_after,
  symbols: act.symbols,
  passed_symbols: safety.passed_symbols,
  blocked_symbols: safety.blocked_symbols,
  ml_latest_date: pre.ml_latest_date,
  required_ml_date: signalDate,
  pre_send: pre.checks,
  safety_check: {
    ok: safety.ok,
    passed: safety.passed_symbols,
    blocked: safety.blocked_symbols,
  },
  upstream_remediated: upstream.remediated,
};

if (ok) savePrepareStamp(stamp);
else {
  logDeliveryAttempt({
    signal_date: signalDate,
    actionable: act.db > 0,
    deliverable: act.deliverable > 0,
    skip_reason: `PREPARE_FAILED: ${!safetyOk ? `safety:${safety.blocked_symbols.join(',')}` : pre.blockers.join(' | ') || upstream.skip_reason}`,
    pipeline_stage: 'prepare_send_failed',
    ml_latest_date: pre.ml_latest_date,
    required_ml_date: signalDate,
    dedup_key: `prepare_fail:${signalDate}`,
    meta_json: stamp,
  });
}

console.log(`\n═══ Prepare ${ok ? 'GREEN ✅' : 'BLOCKED ⛔'} ═══\n`);
process.exit(ok ? 0 : 2);
