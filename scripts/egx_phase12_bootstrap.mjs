#!/usr/bin/env node
/**
 * Phase 12 — Historical bootstrap graduation + live validation prep.
 * Replaces live-only 30/30 wait with OHLCV-backed safety-filtered ULTRA proof.
 *
 * Usage: node scripts/egx_phase12_bootstrap.mjs [--json] [--skip-phase11]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluateGraduationReadiness } from './lib/p6_graduation_gate.mjs';
import {
  runHistoricalOutcomeBackfill,
  writeHistoricalProofSnapshot,
  getBootstrapProofMetrics,
  BOOTSTRAP_MIN_N,
  GRADUATION_MODE,
} from './lib/p6_historical_proof.mjs';
import { PROOF_MIN_N } from './lib/proof_loop.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE11 = process.argv.includes('--skip-phase11');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 12 — Historical Bootstrap Graduation ═══');
console.log(`  Signal date: ${signalDate}`);
console.log(`  Mode: ${GRADUATION_MODE}\n`);

const backfill = runHistoricalOutcomeBackfill({ lookbackDays: 365 });
writeHistoricalProofSnapshot(backfill);

if (!SKIP_PHASE11) {
  try {
    execSync(`"${NODE}" scripts/egx_phase11_promotion.mjs --skip-phase10`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 120_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 11: ${e.message?.slice(0, 80)}`);
  }
}

const bootstrap = getBootstrapProofMetrics();
const readiness = evaluateGraduationReadiness();
const resolved = writeResearchClientEnvSnapshot(resolveResearchClientEnv({ readiness }));

let medShadow = null;
if (bootstrap.bootstrap_pass) {
  try {
    const raw = execSync(
      `${resolved.prefix} "${PYTHON}" scripts/python/med_0_3_status.py '${JSON.stringify({ trade_date: signalDate })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 60_000 },
    );
    medShadow = { ok: true, tail: raw.trim().slice(-200) };
  } catch (e) {
    medShadow = { ok: false, error: e.message?.slice(0, 120) };
  }
}

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: bootstrap.bootstrap_pass,
  graduation_mode: GRADUATION_MODE,
  backfill,
  bootstrap,
  readiness,
  research_env: resolved,
  med_shadow_probe: medShadow,
  recommendations: bootstrap.bootstrap_pass ? [
    'Set EGX_PHASE11_AUTO_PROMOTE=1 in .env to apply MED_CLIENT_SIGNAL=1 when gates recommend',
    `Live forward KPI continues: ${bootstrap.ultra_safe.n_completed}/${PROOF_MIN_N} ULTRA (non-blocking)`,
    'Run npm run egx:prod:prepare-send before next session',
  ] : [
    'Run npm run egx:p6:historical-backfill to refresh OHLCV outcomes',
    `Need ${bootstrap.samples_needed_bootstrap} more safety-filtered ULTRA @ ≥${bootstrap.bootstrap_min_wr}%`,
  ],
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase12_bootstrap_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  const u = bootstrap.ultra_safe;
  console.log(`  Historical backfill: ${backfill.ok ? '✅' : '⚠️'}`);
  console.log(`  ULTRA safe:          ${u.n_completed}/${BOOTSTRAP_MIN_N} @ ${u.win_rate ?? '—'}%`);
  console.log(`  Bootstrap gate:      ${bootstrap.bootstrap_pass ? '✅ PASS' : '⏳ pending'}`);
  console.log(`  Client beta ready:   ${readiness.client_beta_ready ? '✅ YES' : '⏳'}`);
  console.log(`  MED_CLIENT_SIGNAL:   ${readiness.gates.med_client_signal.recommended} (${readiness.gates.med_client_signal.reason})`);
  console.log(`  Live KPI (monitor):  ${u.n_completed}/${PROOF_MIN_N} ULTRA safe`);
  if (report.recommendations.length) {
    console.log('\n  Next steps:');
    report.recommendations.forEach(r => console.log(`    • ${r}`));
  }
  console.log('\n  Saved: data/phase12_bootstrap_last.json\n');
}

process.exit(bootstrap.bootstrap_pass ? 0 : 1);
