#!/usr/bin/env node
/**
 * Phase 13 — Live validation pilots (MED shadow ledger, feed A/B, MDE pilot, live KPI).
 *
 * Usage: node scripts/egx_phase13_live_validation.mjs [--json] [--skip-phase12]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluateGraduationReadiness } from './lib/p6_graduation_gate.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE12 = process.argv.includes('--skip-phase12');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 13 — Live Validation Pilots ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (!SKIP_PHASE12) {
  try {
    execSync(`"${NODE}" scripts/egx_phase12_bootstrap.mjs --skip-phase11`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 180_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 12: ${e.message?.slice(0, 80)}`);
  }
}

const readiness = evaluateGraduationReadiness();
const resolved = writeResearchClientEnvSnapshot(resolveResearchClientEnv({ readiness }));
const envPrefix = resolved.prefix;

function runPy(script, label) {
  try {
    const raw = execSync(
      `${envPrefix} "${PYTHON}" scripts/python/${script} '${JSON.stringify({ trade_date: signalDate })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 120_000 },
    );
    return JSON.parse(raw);
  } catch (e) {
    return { success: false, error: e.message?.slice(0, 120), label };
  }
}

const medShadow = runPy('med_client_signal_shadow.py', 'MED client shadow');
const medAb = runPy('med_feed_ab_pilot.py', 'MED feed A/B');
const mdePilot = runPy('mde_pilot_shadow.py', 'MDE pilot shadow');
const kpi = writeLiveKpiSnapshot();

const checks = [
  { id: 'med_client_shadow', ok: medShadow?.success === true, detail: `${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5} sessions` },
  { id: 'med_feed_ab', ok: medAb?.success === true, detail: `boost ${medAb?.boost_wins ?? 0} vs penalize ${medAb?.penalize_wins ?? 0}` },
  { id: 'mde_pilot', ok: mdePilot?.success === true, detail: `${mdePilot?.pilot_count ?? 0} hints` },
  { id: 'live_kpi', ok: Boolean(kpi?.ultra_safe), detail: kpi?.status_line ?? '—' },
  { id: 'bootstrap_ready', ok: readiness.client_beta_ready, detail: readiness.client_beta_ready ? 'PASS' : 'pending' },
];

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: checks.filter(c => c.id !== 'bootstrap_ready').every(c => c.ok),
  checks,
  readiness,
  research_env: resolved.env,
  med_client_shadow: medShadow,
  med_feed_ab: medAb,
  mde_pilot: mdePilot,
  live_kpi: kpi,
  recommendations: [
    medShadow?.validation_pass ? 'MED client shadow validation PASS — ready for live MED_CLIENT_SIGNAL probe' : `MED shadow: ${medShadow?.sessions_remaining ?? '—'} sessions remaining`,
    medAb?.recommendation ?? 'Run med_feed_ab daily',
    process.env.EGX_MDE_PILOT_PROMOTE === '1' ? 'MDE pilot active' : 'Set EGX_MDE_PILOT_PROMOTE=1 for behavior memory shadow',
    'Live KPI 3→30 is monitored only — does not block sends',
  ],
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase13_live_validation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  MED client shadow:  ${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5} sessions | validation ${medShadow?.validation_pass ? '✅' : '⏳'}`);
  console.log(`  MED feed A/B:       ${medAb?.production_track ?? '—'} | boost wins ${medAb?.boost_wins ?? 0} / penalize ${medAb?.penalize_wins ?? 0}`);
  console.log(`  MDE pilot:          ${mdePilot?.pilot_count ?? 0} symbols | memory ${mdePilot?.memory_active ? 'ON' : 'OFF'}`);
  console.log(`  Live KPI:           ${kpi?.status_line ?? '—'}`);
  console.log('\n  Saved: data/phase13_live_validation_last.json');
  console.log('  Saved: data/p6_live_kpi_last.json\n');
}

process.exit(report.pass ? 0 : 1);
