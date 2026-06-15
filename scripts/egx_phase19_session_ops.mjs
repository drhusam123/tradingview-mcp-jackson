#!/usr/bin/env node
/**
 * Phase 19 — Post-graduation session ops (t5 fill, LRE OOS, first live session).
 *
 * Usage:
 *   node scripts/egx_phase19_session_ops.mjs [--json] [--skip-phase18] [--apply-env] [--weekly-full]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { upsertEnvVars, PHASE19_ENV_DEFAULTS } from './lib/env_sync.mjs';
import {
  evaluatePhase19SessionOps,
  writePhase19Snapshot,
} from './lib/phase19_session_ops.mjs';
import { writePostGraduationSessionSnapshot } from './lib/post_graduation_session.mjs';
import {
  runWeeklyProdReadyFull,
  writeWeeklyProdReadySnapshot,
} from './lib/weekly_prod_ready.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE18 = process.argv.includes('--skip-phase18');
const APPLY_ENV = process.argv.includes('--apply-env') || process.argv.includes('--activate-env');
const WEEKLY_FULL = process.argv.includes('--weekly-full')
  || process.env.EGX_WEEKLY_PROD_READY_FORCE === '1';
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 19 — Post-Graduation Session Ops ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (APPLY_ENV) {
  const envResult = upsertEnvVars(PHASE19_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE19_ENV_DEFAULTS)) {
    process.env[k] = v;
  }
  if (!AS_JSON) console.log(`  Env activated → ${envResult.envPath}\n`);
}

if (!SKIP_PHASE18) {
  try {
    execSync(`"${NODE}" scripts/egx_phase18_live_ops.mjs --skip-phase17`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 600_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 18: ${e.message?.slice(0, 80)}`);
  }
}

syncDeliveredOutcomes({ lookbackDays: 120 });

const envPrefix = () => {
  const r = resolveResearchClientEnv();
  writeResearchClientEnvSnapshot(r);
  return r.prefix;
};

function runPy(script, extra = {}) {
  try {
    const raw = execSync(
      `${envPrefix()} "${PYTHON}" scripts/python/${script} '${JSON.stringify({ trade_date: signalDate, ...extra })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 300_000 },
    );
    return JSON.parse(raw);
  } catch (e) {
    return { success: false, error: e.message?.slice(0, 120) };
  }
}

const t5Fill = runPy('p6_t5_fill_orchestrator.py');
const lreAcc = runPy('lre_oos_accumulator.py');

try {
  execSync('npm run egx:lre:status', { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout: 120_000 });
} catch (e) {
  if (!AS_JSON) console.log(`⚠️  LRE status: ${e.message?.slice(0, 80)}`);
}

writeResearchClientEnvSnapshot(resolveResearchClientEnv());

let weeklyRun = null;
if (WEEKLY_FULL) {
  weeklyRun = runWeeklyProdReadyFull({ dryRun: false });
} else {
  weeklyRun = writeWeeklyProdReadySnapshot();
}

writePostGraduationSessionSnapshot();
const phase19 = writePhase19Snapshot(evaluatePhase19SessionOps(signalDate));
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: phase19.phase19_ready,
  phase19,
  t5_fill: t5Fill,
  lre_accumulator: lreAcc,
  weekly_prod: weeklyRun,
  effective_env: env,
  next_session: phase19.next_session,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase19_session_ops_report_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Phase 19:          ${phase19.phase19_ready ? '✅ READY' : '⏳ monitoring'}`);
  console.log(`  Post-grad session: ${phase19.gates.post_graduation_session.pass ? '✅' : '⏳'} — ${phase19.gates.post_graduation_session.detail}`);
  console.log(`  T5 fill:           ${t5Fill?.status_line ?? '—'}`);
  if (t5Fill?.watch_pending?.length) {
    t5Fill.watch_pending.forEach(w => {
      console.log(`    • ${w.symbol} ${w.signal_date} bars=${w.bars_available}/5 → t5 ${w.projected_t5_date ?? 'pending'}`);
    });
  }
  console.log(`  LRE OOS:           ${phase19.gates.lre_oos_accumulation.detail}`);
  console.log(`  Live session:      ${phase19.gates.live_session.pass ? '✅' : '⏳'} — ${phase19.gates.live_session.detail}`);
  console.log(`  Weekly prod:ready: ${weeklyRun?.recommendation ?? (weeklyRun?.needs_full_run ? 'due' : 'fresh')}`);
  console.log(`  Next session:      ${phase19.next_session}`);

  if (phase19.blockers.length) {
    console.log('\n  Blockers:');
    phase19.blockers.forEach(b => console.log(`    • ${b}`));
  }
  console.log('\n  Saved: data/phase19_session_ops_last.json');
  console.log('  Saved: data/phase19_session_ops_report_last.json\n');
}

process.exit(
  phase19.phase19_ready || phase19.gates.production_graduated.pass ? 0 : 1,
);
