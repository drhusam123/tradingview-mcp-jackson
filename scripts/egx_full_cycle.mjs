#!/usr/bin/env node
/**
 * Daily production full-cycle DAG — update → engines → session → post-session → health.
 *
 * Usage:
 *   npm run egx:full-cycle
 *   npm run egx:full-cycle -- --skip-cdp
 *   npm run egx:full-cycle -- --send
 *   npm run egx:full-cycle -- --json
 */
import { execSync } from 'child_process';
import { appendFileSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const SKIP_CDP = process.argv.includes('--skip-cdp');
const LIVE_SEND = process.argv.includes('--send');
const AS_JSON = process.argv.includes('--json');
const QUICK = process.argv.includes('--quick');
const FAST = process.argv.includes('--fast');

const logPath = join(PROJECT_ROOT, 'logs', `full_cycle_${cairoDateParts().date}.log`);
mkdirSync(join(PROJECT_ROOT, 'logs'), { recursive: true });

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  appendFileSync(logPath, line);
  if (!AS_JSON) console.log(msg);
}

const steps = [];

function run(name, cmd, { optional = false, timeout = 600_000 } = {}) {
  const t0 = Date.now();
  log(`▶ ${name}`);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout });
    const row = { name, ok: true, ms: Date.now() - t0, optional };
    steps.push(row);
    return row;
  } catch (e) {
    const row = {
      name, ok: false, ms: Date.now() - t0, optional,
      error: e.message?.slice(0, 200),
    };
    steps.push(row);
    if (!optional) {
      log(`⛔ BLOCKED at ${name}: ${row.error}`);
      throw e;
    }
    log(`⚠️ ${name}: ${row.error}`);
    return row;
  }
}

console.log('\n═══ EGX Full Cycle (daily production DAG) ═══');
log(`start skip_cdp=${SKIP_CDP} send=${LIVE_SEND}`);

try {
  run('preflight', `"${NODE}" scripts/egx_preflight.mjs`, { optional: true, timeout: 120_000 });

  run('ohlcv_catchup', `"${NODE}" scripts/egx_ohlcv_catchup.mjs`, { optional: true, timeout: 300_000 });

  if (!SKIP_CDP) {
    run('tv_auto_update', `"${NODE}" scripts/egx_tv_auto_update.mjs --launch --pine --tech`, {
      timeout: 2_400_000,
    });
  } else {
    run('indicators_rebuild', `"${NODE}" scripts/rebuild_indicators.mjs`, { optional: true });
    run('med_daily_chain', `${PYTHON} scripts/python/med_0_3_daily_chain.py '{}'`, { optional: true });
    run('lre_status', `${PYTHON} scripts/python/lre_4_0_status.py`, { optional: true });
    run('lre_oos_accumulator', `${PYTHON} scripts/python/lre_oos_accumulator.py '{}'`, { optional: true });
  }

  if (FAST) {
    run('session_ready', `"${NODE}" scripts/egx_session_ready.mjs --skip-verify-check`);
    const healthFlags = QUICK ? '--quick' : '';
    run('health_check', `${PYTHON} scripts/python/system_health_check.py ${healthFlags}`, { optional: true });
  } else {
    run('session_ready', `"${NODE}" scripts/egx_session_ready.mjs --skip-verify-check`);

    const prepFlags = LIVE_SEND ? '--send' : '--dry-run';
    run('prod_prepare_send', `"${NODE}" scripts/egx_prod_prepare_send.mjs ${prepFlags}`, { optional: !LIVE_SEND });

    run('post_session', `"${NODE}" scripts/egx_post_session_ops.mjs`, { timeout: 1_800_000 });

    const healthFlags = QUICK ? '--quick' : '';
    run('health_check', `${PYTHON} scripts/python/system_health_check.py ${healthFlags}`, { optional: true });
  }
} catch {
  // captured in steps
}

const fail = steps.filter(s => !s.ok && !s.optional).length;
const report = {
  at: new Date().toISOString(),
  pass: fail === 0,
  signal_date: latestOhlcvDate(),
  cairo_date: cairoDateParts().date,
  skip_cdp: SKIP_CDP,
  live_send: LIVE_SEND,
  steps,
  log_path: logPath,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/full_cycle_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Full Cycle Summary ═══');
  for (const s of steps) console.log(`  ${s.ok ? '✅' : s.optional ? '⚠️' : '❌'} ${s.name}`);
  console.log(`\n=== Full Cycle: ${steps.filter(s => s.ok).length}/${steps.length} OK ===`);
  console.log(`  Log: ${logPath}`);
  console.log(`  Report: data/full_cycle_last.json\n`);
}

process.exit(fail ? 1 : 0);
