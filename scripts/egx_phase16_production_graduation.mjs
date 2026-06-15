#!/usr/bin/env node
/**
 * Phase 16 — Production graduation + live beta session monitor.
 *
 * Usage:
 *   node scripts/egx_phase16_production_graduation.mjs [--json] [--skip-phase15] [--full-prod-ready]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import {
  evaluateProductionGraduation,
  writeProductionGraduationSnapshot,
} from './lib/production_graduation.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE15 = process.argv.includes('--skip-phase15');
const FULL_PROD_READY = process.argv.includes('--full-prod-ready');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 16 — Production Graduation ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (!SKIP_PHASE15) {
  try {
    execSync(`"${NODE}" scripts/egx_phase15_client_beta.mjs --skip-phase14`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 180_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 15: ${e.message?.slice(0, 80)}`);
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

const medDelta = runPy('med_opp_delta_monitor.py');
const medAb = runPy('med_feed_ab_pilot.py', {
  backfill_historical: process.env.EGX_MED_AB_BACKFILL === '1',
});
runPy('mde_pilot_stability.py');
runPy('mde_pilot_shadow.py');

writeResearchClientEnvSnapshot(resolveResearchClientEnv());

const prodFlags = FULL_PROD_READY ? '' : '--skip-cdp --skip-tests';
let prodReady = null;
try {
  execSync(`"${NODE}" scripts/egx_prod_ready.mjs ${prodFlags}`, {
    cwd: PROJECT_ROOT,
    stdio: AS_JSON ? 'pipe' : 'inherit',
    timeout: FULL_PROD_READY ? 900_000 : 600_000,
  });
  prodReady = { success: true, mode: FULL_PROD_READY ? 'full' : 'fast' };
} catch (e) {
  prodReady = { success: false, mode: FULL_PROD_READY ? 'full' : 'fast', error: e.message?.slice(0, 120) };
}

writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const graduation = writeProductionGraduationSnapshot(evaluateProductionGraduation(signalDate));
const kpi = writeLiveKpiSnapshot();
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: graduation.production_graduated,
  graduation,
  med_opp_delta: medDelta,
  med_feed_ab: medAb,
  prod_ready_run: prodReady,
  live_kpi: kpi,
  effective_env: env,
  next_promotions: graduation.promotions,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase16_production_graduation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Production:        ${graduation.production_graduated ? '✅ GRADUATED' : '⏳ pending'}`);
  console.log(`  Client beta:       ${graduation.gates.client_beta_signoff.pass ? '✅ signed off' : '⏳'} (${graduation.gates.client_beta_signoff.detail})`);
  console.log(`  Prod ready:        ${graduation.gates.prod_ready.pass ? '✅ PASS' : '❌ pending'} (${prodReady.mode})`);
  console.log(`  Live beta monitor: ${graduation.gates.live_beta_monitor.pass ? '✅ healthy' : '⏳'} — ${graduation.gates.live_beta_monitor.detail}`);
  console.log(`  Opp delta:         ${medDelta?.symbols_monitored ?? 0} sym | avg Δ ${medDelta?.avg_delta_boost_vs_pen ?? '—'}`);
  console.log(`  Telegram today:    sent ${graduation.live_beta.sent_today} | deliverable ${graduation.live_beta.deliverable_today}`);
  console.log(`  MED_FEED_BOOST:    streak ${graduation.gates.med_feed_boost.streak}/${graduation.gates.med_feed_boost.target} → rec=${graduation.gates.med_feed_boost.recommended}`);
  console.log(`  MDE memory:        ${graduation.gates.mde_behavior_memory.days}/${graduation.gates.mde_behavior_memory.target_days}d → rec=${graduation.gates.mde_behavior_memory.recommended}`);
  console.log(`  Live KPI:          ${kpi?.status_line ?? '—'}`);
  console.log(`  MED_CLIENT_SIGNAL: ${env.MED_CLIENT_SIGNAL} | MED_FEED_BOOST=${env.MED_FEED_BOOST} | MDE_MEM=${env.EGX_MDE_BEHAVIOR_MEMORY}`);

  if (graduation.blockers.length) {
    console.log('\n  Blockers:');
    graduation.blockers.forEach(b => console.log(`    • ${b}`));
  }
  if (graduation.promotions) {
    console.log('\n  Pending promotions:');
    Object.values(graduation.promotions).forEach(p => console.log(`    • ${p}`));
  }
  console.log('\n  Saved: data/phase16_production_graduation_last.json');
  console.log('  Saved: data/production_graduation_last.json\n');
}

process.exit(graduation.production_graduated ? 0 : 1);
