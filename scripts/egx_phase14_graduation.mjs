#!/usr/bin/env node
/**
 * Phase 14 — Graduation + live probes (env activate, shadow backfill, MED probe, MDE stability).
 *
 * Usage: node scripts/egx_phase14_graduation.mjs [--json] [--skip-phase13] [--activate-env]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { upsertEnvVars, PHASE14_ENV_DEFAULTS } from './lib/env_sync.mjs';
import { evaluatePhase14Readiness, writePhase14Snapshot } from './lib/phase14_graduation.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE13 = process.argv.includes('--skip-phase13');
const ACTIVATE_ENV = process.argv.includes('--activate-env') || process.argv.includes('--activate');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 14 — Graduation + Live Probes ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (ACTIVATE_ENV) {
  const envResult = upsertEnvVars(PHASE14_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE14_ENV_DEFAULTS)) {
    process.env[k] = v;
  }
  if (!AS_JSON) console.log(`  Env activated → ${envResult.envPath}\n`);
}

if (!SKIP_PHASE13) {
  try {
    execSync(`"${NODE}" scripts/egx_phase13_live_validation.mjs --skip-phase12`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 180_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 13: ${e.message?.slice(0, 80)}`);
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

const medShadow = runPy('med_client_signal_shadow.py', { backfill_historical: true });
writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const medAb = runPy('med_feed_ab_pilot.py');
const medProbe = runPy('med_client_signal_probe.py');
const mdeStability = runPy('mde_pilot_stability.py');
runPy('mde_pilot_shadow.py');

writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const phase14 = writePhase14Snapshot();
const kpi = writeLiveKpiSnapshot();

const checks = [
  { id: 'env_auto_promote', ok: process.env.EGX_PHASE11_AUTO_PROMOTE === '1', detail: `auto=${process.env.EGX_PHASE11_AUTO_PROMOTE ?? '0'}` },
  { id: 'med_shadow', ok: medShadow?.validation_pass === true, detail: `${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5}` },
  { id: 'med_probe', ok: medProbe?.probe_active === true, detail: `MED_CLIENT_SIGNAL=${medProbe?.MED_CLIENT_SIGNAL ?? '0'}` },
  { id: 'med_ab', ok: medAb?.success === true, detail: `streak ${medAb?.boost_win_streak ?? 0}` },
  { id: 'mde_stability', ok: mdeStability?.success === true, detail: `${mdeStability?.days_active ?? 0}/${mdeStability?.target_days ?? 14}d` },
  { id: 'live_kpi', ok: Boolean(kpi?.ultra_safe), detail: kpi?.status_line },
];

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: checks.every(c => c.ok || c.id === 'med_ab' || c.id === 'mde_stability'),
  checks,
  phase14,
  med_shadow: medShadow,
  med_probe: medProbe,
  med_ab: medAb,
  mde_stability: mdeStability,
  live_kpi: kpi,
  effective_env: resolveResearchClientEnv().env,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase14_graduation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Env:               auto=${process.env.EGX_PHASE11_AUTO_PROMOTE} shadow=${process.env.EGX_MED_CLIENT_SHADOW} mde_pilot=${process.env.EGX_MDE_PILOT_PROMOTE}`);
  console.log(`  MED shadow:        ${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5} | validation ${medShadow?.validation_pass ? '✅' : '⏳'}`);
  console.log(`  MED probe:         active=${medProbe?.probe_active ? '✅' : '⏳'} | MED_CLIENT_SIGNAL=${report.effective_env.MED_CLIENT_SIGNAL}`);
  console.log(`  MED feed A/B:      streak ${medAb?.boost_win_streak ?? 0}/${medAb?.boost_streak_target ?? 5} | boost rec=${medAb?.feed_boost_recommended ? 'YES' : 'NO'}`);
  console.log(`  MDE stability:     ${mdeStability?.days_active ?? 0}/${mdeStability?.target_days ?? 14}d | memory rec=${mdeStability?.EGX_MDE_BEHAVIOR_MEMORY_recommended}`);
  console.log(`  Live KPI:          ${kpi?.status_line ?? '—'}`);
  console.log('\n  Saved: data/phase14_graduation_last.json\n');
}

process.exit(phase14.phase14_ready ? 0 : 1);
