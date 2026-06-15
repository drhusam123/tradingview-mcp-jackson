#!/usr/bin/env node
/**
 * Phase 18 — Live ops: session validation, P6 delivered KPI, LRE OOS, weekly prod:ready.
 *
 * Usage:
 *   node scripts/egx_phase18_live_ops.mjs [--json] [--skip-phase17] [--apply-env] [--weekly-full]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { upsertEnvVars, PHASE18_ENV_DEFAULTS } from './lib/env_sync.mjs';
import { runHistoricalOutcomeBackfill } from './lib/p6_historical_proof.mjs';
import {
  evaluatePhase18LiveOps,
  writePhase18Snapshot,
} from './lib/phase18_live_ops.mjs';
import {
  runWeeklyProdReadyFull,
  writeWeeklyProdReadySnapshot,
} from './lib/weekly_prod_ready.mjs';
import {
  applyPromotionEnv,
  writePromotionActivationSnapshot,
} from './lib/promotion_activation.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE17 = process.argv.includes('--skip-phase17');
const APPLY_ENV = process.argv.includes('--apply-env') || process.argv.includes('--activate-env');
const WEEKLY_FULL = process.argv.includes('--weekly-full')
  || process.env.EGX_WEEKLY_PROD_READY_FORCE === '1';
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 18 — Live Ops ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (APPLY_ENV) {
  const envResult = upsertEnvVars(PHASE18_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE18_ENV_DEFAULTS)) {
    process.env[k] = v;
  }
  if (!AS_JSON) console.log(`  Env activated → ${envResult.envPath}\n`);
}

if (!SKIP_PHASE17) {
  try {
    execSync(`"${NODE}" scripts/egx_phase17_promotion_activation.mjs --skip-phase16`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 180_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 17: ${e.message?.slice(0, 80)}`);
  }
}

const envPrefix = () => {
  const r = resolveResearchClientEnv();
  writeResearchClientEnvSnapshot(r);
  return r.prefix;
};

function runPy(script, extra = {}) {
  try {
    const raw = execSync(
      `${envPrefix()} "${PYTHON}" scripts/python/${script} '${JSON.stringify({ trade_date: signalDate, ...extra })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 120_000 },
    );
    return JSON.parse(raw);
  } catch (e) {
    return { success: false, error: e.message?.slice(0, 120) };
  }
}

runHistoricalOutcomeBackfill({ lookbackDays: 120 });
runPy('med_live_delivery_correlation.py');
const p6Delivered = runPy('p6_delivered_kpi_tracker.py');

try {
  execSync('npm run egx:lre:status', { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout: 120_000 });
} catch (e) {
  if (!AS_JSON) console.log(`⚠️  LRE status: ${e.message?.slice(0, 80)}`);
}

writeResearchClientEnvSnapshot(resolveResearchClientEnv());

let weeklyRun = null;
if (WEEKLY_FULL || process.env.EGX_WEEKLY_PROD_READY === '1') {
  weeklyRun = runWeeklyProdReadyFull({ dryRun: false });
} else {
  weeklyRun = writeWeeklyProdReadySnapshot();
}

const applied = applyPromotionEnv({ signalDate });
writePromotionActivationSnapshot();
writeLiveKpiSnapshot();
writeResearchClientEnvSnapshot(resolveResearchClientEnv());

const phase18 = writePhase18Snapshot(evaluatePhase18LiveOps(signalDate));
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: phase18.phase18_ready,
  phase18,
  p6_delivered: p6Delivered,
  weekly_prod: weeklyRun,
  promotion_applied: applied.applied,
  effective_env: env,
  next_session: phase18.next_session,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase18_live_ops_report_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Phase 18:          ${phase18.phase18_ready ? '✅ READY' : '⏳ monitoring'}`);
  console.log(`  Production:        ${phase18.gates.production_graduated.pass ? '✅ graduated' : '⏳'}`);
  console.log(`  Live session:      ${phase18.gates.live_session.pass ? '✅ validated' : '⏳'} — ${phase18.gates.live_session.detail}`);
  console.log(`  P6 delivered KPI:  ${p6Delivered?.status_line ?? '—'}`);
  if (p6Delivered?.pending?.length) {
    p6Delivered.pending.slice(0, 5).forEach(p => {
      console.log(`    • ${p.symbol} ${p.signal_date} bars=${p.bars_available}/5 partial=${p.partial_return_pct ?? '—'}%`);
    });
  }
  console.log(`  LRE OOS:           ${phase18.gates.lre_oos.closed}/${phase18.gates.lre_oos.target} → ${phase18.gates.lre_oos.detail}`);
  console.log(`  Weekly prod:ready: ${weeklyRun?.recommendation ?? weeklyRun?.needs_full_run ? 'due' : 'fresh'}`);
  console.log(`  Next session:      ${phase18.next_session}`);
  console.log(`  MED_CLIENT_SIGNAL: ${env.MED_CLIENT_SIGNAL} | LRE_FEED_BOOST=${env.EGX_LRE_FEED_BOOST}`);

  if (phase18.blockers.length) {
    console.log('\n  Blockers:');
    phase18.blockers.forEach(b => console.log(`    • ${b}`));
  }
  console.log('\n  Saved: data/phase18_live_ops_last.json');
  console.log('  Saved: data/phase18_live_ops_report_last.json\n');
}

process.exit(
  phase18.phase18_ready || phase18.gates.production_graduated.pass ? 0 : 1,
);
