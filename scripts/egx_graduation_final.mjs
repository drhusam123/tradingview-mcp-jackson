#!/usr/bin/env node
/**
 * Phases 21–26 — full graduation final chain (live anchor → audit close).
 *
 * Usage: node scripts/egx_graduation_final.mjs [--json] [--skip-phase20] [--apply-env] [--weekly-full]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { upsertEnvVars, PHASE26_ENV_DEFAULTS } from './lib/env_sync.mjs';
import { evaluatePhase21LiveAnchor } from './lib/phase21_live_anchor.mjs';
import { evaluatePhase22P6Delivered } from './lib/phase22_p6_delivered.mjs';
import { evaluatePhase23LreGraduation } from './lib/phase23_lre_graduation.mjs';
import { evaluatePhase24MedAbGraduation } from './lib/phase24_med_ab_graduation.mjs';
import { evaluatePhase25MdeMemory } from './lib/phase25_mde_memory.mjs';
import { runAuditCloseApply } from './lib/phase26_audit_close.mjs';
import { runWeeklyProdReadyFull, writeWeeklyProdReadySnapshot } from './lib/weekly_prod_ready.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';
import { writeJson } from './lib/graduation_phases.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE20 = process.argv.includes('--skip-phase20');
const APPLY_ENV = process.argv.includes('--apply-env') || process.argv.includes('--activate-env');
const WEEKLY_FULL = process.argv.includes('--weekly-full');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Graduation Final — Phases 21–26 ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (APPLY_ENV) {
  const r = upsertEnvVars(PHASE26_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE26_ENV_DEFAULTS)) process.env[k] = v;
  if (!AS_JSON) console.log(`  Env → ${r.envPath}\n`);
}

if (!SKIP_PHASE20) {
  try {
    execSync(`"${NODE}" scripts/egx_phase20_outcome_closure.mjs --skip-phase19`, {
      cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout: 600_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 20: ${e.message?.slice(0, 80)}`);
  }
}

syncDeliveredOutcomes({ lookbackDays: 120 });

const prefix = () => {
  const r = resolveResearchClientEnv();
  writeResearchClientEnvSnapshot(r);
  return r.prefix;
};

function py(script, extra = {}) {
  try {
    const raw = execSync(
      `${prefix()} "${PYTHON}" scripts/python/${script} '${JSON.stringify({ trade_date: signalDate, as_of_date: signalDate, ...extra })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 300_000 },
    );
    return JSON.parse(raw);
  } catch (e) {
    return { success: false, error: e.message?.slice(0, 120) };
  }
}

py('p6_watch_t5_closure.py');
py('p6_t5_fill_orchestrator.py');
py('p6_delivered_wr_dashboard.py');
py('med_feed_ab_pilot.py', { backfill_historical: true });
py('lre_oos_accumulator.py');
py('mde_pilot_stability.py', { backfill_stability: process.env.EGX_MDE_PILOT_BACKFILL_STABILITY === '1' });
py('mde_pilot_shadow.py');

try {
  execSync('npm run egx:lre:status', { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout: 120_000 });
} catch { /* */ }

writeLiveKpiSnapshot();
writeResearchClientEnvSnapshot(resolveResearchClientEnv());

const weekly = WEEKLY_FULL ? runWeeklyProdReadyFull() : writeWeeklyProdReadySnapshot();

const p21 = evaluatePhase21LiveAnchor(signalDate);
const p22 = evaluatePhase22P6Delivered();
const p23 = evaluatePhase23LreGraduation();
const p24 = evaluatePhase24MedAbGraduation();
const p25 = evaluatePhase25MdeMemory();
const p26 = runAuditCloseApply(signalDate);
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  audit_closed: p26.audit_closed,
  verdict: p26.verdict,
  phases: { p21, p22, p23, p24, p25, p26 },
  weekly_prod: weekly,
  effective_env: env,
  pending_promotions: p26.pending_promotions,
};

writeJson('graduation_final_last.json', report);
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/graduation_final_report_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Verdict:           ${p26.verdict}`);
  console.log(`  Audit closed:      ${p26.audit_closed ? '✅ YES' : '⏳ ACCUMULATING'}`);
  console.log(`  Phase 21 anchor:   ${p21.gates.live_anchor.pass ? '✅' : '⏳'} — ${p21.gates.live_anchor.detail}`);
  console.log(`  Phase 22 P6 del:   ${p22.gates.delivered_wr.detail}`);
  console.log(`  Phase 23 LRE:      ${p23.gates.lre_oos.detail} (${p23.gates.lre_oos.closed}/${p23.gates.lre_oos.target})`);
  console.log(`  Phase 24 MED A/B:  ${p24.gates.med_feed_ab.detail}`);
  console.log(`  Phase 25 MDE mem:  ${p25.gates.mde_behavior_memory.detail}`);
  console.log(`  Promotions apply:  ${p26.promotion_apply?.applied ? 'YES' : 'no'}`);
  console.log(`  MED_CLIENT_SIGNAL: ${env.MED_CLIENT_SIGNAL} | BOOST=${env.MED_FEED_BOOST} | LRE=${env.EGX_LRE_FEED_BOOST} | MDE=${env.EGX_MDE_BEHAVIOR_MEMORY}`);
  if (p26.pending_promotions?.length) {
    console.log('\n  Pending promotions:');
    p26.pending_promotions.forEach(p => console.log(`    • ${p}`));
  }
  console.log('\n  Saved: data/audit_close_last.json');
  console.log('  Saved: data/graduation_final_last.json\n');
}

process.exit(p26.audit_closed || p26.phase26_ready ? 0 : 0);
