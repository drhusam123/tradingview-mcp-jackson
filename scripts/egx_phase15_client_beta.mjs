#!/usr/bin/env node
/**
 * Phase 15 — Client beta sign-off + MED live session delta monitor.
 *
 * Usage: node scripts/egx_phase15_client_beta.mjs [--json] [--skip-phase14]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluatePhase14Readiness } from './lib/phase14_graduation.mjs';
import {
  evaluateClientBetaSignoff,
  writeClientBetaSignoffSnapshot,
} from './lib/client_beta_signoff.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE14 = process.argv.includes('--skip-phase14');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 15 — Client Beta Sign-Off ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (!SKIP_PHASE14) {
  try {
    execSync(`"${NODE}" scripts/egx_phase14_graduation.mjs --skip-phase13`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 180_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 14: ${e.message?.slice(0, 80)}`);
  }
}

writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const envPrefix = resolveResearchClientEnv().prefix;

let medDelta = null;
try {
  const raw = execSync(
    `${envPrefix} "${PYTHON}" scripts/python/med_opp_delta_monitor.py '${JSON.stringify({ trade_date: signalDate })}'`,
    { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 120_000 },
  );
  medDelta = JSON.parse(raw);
} catch (e) {
  medDelta = { success: false, error: e.message?.slice(0, 120) };
}

writeResearchClientEnvSnapshot(resolveResearchClientEnv());
const signoff = writeClientBetaSignoffSnapshot();
const kpi = writeLiveKpiSnapshot();
const p14 = evaluatePhase14Readiness();

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: signoff.client_beta_signed_off,
  signoff,
  med_opp_delta: medDelta,
  phase14: p14,
  live_kpi: kpi,
  effective_env: resolveResearchClientEnv().env,
  next_steps: signoff.pending_promotions,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase15_client_beta_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Sign-off:           ${signoff.client_beta_signed_off ? '✅ SIGNED OFF' : '⏳ pending'} (${signoff.required_pass}/${signoff.required_total} required)`);
  console.log(`  MED_CLIENT_SIGNAL:  ${report.effective_env.MED_CLIENT_SIGNAL}`);
  console.log(`  Opp delta:          ${medDelta?.symbols_monitored ?? 0} symbols | avg Δ ${medDelta?.avg_delta_boost_vs_pen ?? '—'}`);
  console.log(`  Live MED sessions:  ${medDelta?.live_sessions_with_client_signal ?? 0}`);
  console.log(`  Feed A/B:           streak ${p14.gates.med_feed_ab.boost_streak}/${p14.gates.med_feed_ab.target_streak} | boost rec=${p14.feed_boost_ready ? 'YES' : 'NO'}`);
  console.log(`  MDE memory:         ${p14.gates.mde_behavior_memory.days_active}/${p14.gates.mde_behavior_memory.target_days}d`);
  console.log(`  Live KPI:           ${kpi?.status_line ?? '—'}`);

  if (signoff.blockers.length) {
    console.log('\n  Blockers:');
    signoff.blockers.forEach(b => console.log(`    • ${b}`));
  }
  if (signoff.pending_promotions.length) {
    console.log('\n  Pending promotions:');
    signoff.pending_promotions.forEach(p => console.log(`    • ${p}`));
  }
  console.log('\n  Saved: data/phase15_client_beta_last.json');
  console.log('  Saved: data/client_beta_signoff_last.json\n');
}

process.exit(signoff.client_beta_signed_off ? 0 : 1);
