#!/usr/bin/env node
/**
 * Phase 17 — Promotion auto-apply + live delivery correlation.
 *
 * Usage:
 *   node scripts/egx_phase17_promotion_activation.mjs [--json] [--skip-phase16] [--apply-env] [--mde-backfill]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { upsertEnvVars, PHASE17_ENV_DEFAULTS } from './lib/env_sync.mjs';
import {
  applyPromotionEnv,
  evaluatePromotionActivation,
  writePromotionActivationSnapshot,
} from './lib/promotion_activation.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { evaluatePhase14Readiness } from './lib/phase14_graduation.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE16 = process.argv.includes('--skip-phase16');
const APPLY_ENV = process.argv.includes('--apply-env') || process.argv.includes('--activate-env');
const MDE_BACKFILL = process.argv.includes('--mde-backfill')
  || process.env.EGX_MDE_PILOT_BACKFILL_STABILITY === '1';
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 17 — Promotion Activation ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (APPLY_ENV) {
  const envResult = upsertEnvVars(PHASE17_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE17_ENV_DEFAULTS)) {
    process.env[k] = v;
  }
  if (!AS_JSON) console.log(`  Env activated → ${envResult.envPath}\n`);
}

if (!SKIP_PHASE16) {
  try {
    execSync(`"${NODE}" scripts/egx_phase16_production_graduation.mjs --skip-phase15`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 600_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 16: ${e.message?.slice(0, 80)}`);
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

runPy('med_feed_ab_pilot.py', { backfill_historical: true });
runPy('med_opp_delta_monitor.py');
const correlation = runPy('med_live_delivery_correlation.py');
runPy('mde_pilot_stability.py', { backfill_stability: MDE_BACKFILL });
runPy('mde_pilot_shadow.py');

writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const applied = applyPromotionEnv({ dryRun: false, signalDate });
writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const activation = writePromotionActivationSnapshot();
const p14 = evaluatePhase14Readiness();
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: activation.verdict === 'APPLY' || activation.verdict === 'MONITOR',
  applied: applied.applied,
  activation,
  correlation,
  phase14: p14,
  effective_env: env,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase17_promotion_activation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Verdict:           ${activation.verdict}`);
  console.log(`  Auto-apply:        ${activation.auto_apply_enabled ? 'ON' : 'OFF'} | applied=${applied.applied}`);
  console.log(`  Delivery corr:     ${correlation?.summary ?? '—'}`);
  console.log(`  MED feed A/B:      streak ${p14.gates.med_feed_ab.boost_streak}/${p14.gates.med_feed_ab.target_streak} → ${p14.gates.med_feed_ab.reason}`);
  console.log(`  MDE memory:        ${p14.gates.mde_behavior_memory.days_active}/${p14.gates.mde_behavior_memory.target_days}d → ${p14.gates.mde_behavior_memory.reason}`);
  console.log(`  MED_CLIENT_SIGNAL: ${env.MED_CLIENT_SIGNAL} | MED_FEED_BOOST=${env.MED_FEED_BOOST} | MDE_MEM=${env.EGX_MDE_BEHAVIOR_MEMORY}`);

  if (activation.blockers.length && activation.verdict !== 'MONITOR') {
    console.log('\n  Blockers:');
    activation.blockers.forEach(b => console.log(`    • ${b}`));
  }
  if (Object.keys(activation.env_patch).length) {
    console.log('\n  Ready to apply:');
    Object.entries(activation.env_patch).forEach(([k, v]) => console.log(`    ${k}=${v}`));
  }
  console.log('\n  Saved: data/phase17_promotion_activation_last.json');
  console.log('  Saved: data/promotion_activation_last.json\n');
}

process.exit(activation.verdict === 'BLOCKED_GRADUATION' ? 1 : 0);
