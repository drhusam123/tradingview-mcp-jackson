#!/usr/bin/env node
/**
 * Phase 20 — Outcome closure (live anchor 2026-06-17, t5 EGCH/UEFM ~2026-06-19).
 *
 * Usage:
 *   node scripts/egx_phase20_outcome_closure.mjs [--json] [--skip-phase19] [--apply-env] [--weekly-full]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { upsertEnvVars, PHASE20_ENV_DEFAULTS } from './lib/env_sync.mjs';
import {
  evaluatePhase20OutcomeClosure,
  writePhase20Snapshot,
} from './lib/phase20_outcome_closure.mjs';
import { writeLiveSessionDaySnapshot } from './lib/live_session_day_gate.mjs';
import {
  runWeeklyProdReadyFull,
  writeWeeklyProdReadySnapshot,
} from './lib/weekly_prod_ready.mjs';
import { writeLiveKpiSnapshot } from './lib/p6_live_kpi.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './lib/research_client_env.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE19 = process.argv.includes('--skip-phase19');
const APPLY_ENV = process.argv.includes('--apply-env') || process.argv.includes('--activate-env');
const WEEKLY_FULL = process.argv.includes('--weekly-full')
  || process.env.EGX_WEEKLY_PROD_READY_FORCE === '1';
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 20 — Outcome Closure ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (APPLY_ENV) {
  const envResult = upsertEnvVars(PHASE20_ENV_DEFAULTS);
  for (const [k, v] of Object.entries(PHASE20_ENV_DEFAULTS)) {
    process.env[k] = v;
  }
  if (!AS_JSON) console.log(`  Env activated → ${envResult.envPath}\n`);
}

if (!SKIP_PHASE19) {
  try {
    execSync(`"${NODE}" scripts/egx_phase19_session_ops.mjs --skip-phase18`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 600_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 19: ${e.message?.slice(0, 80)}`);
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
      `${envPrefix()} "${PYTHON}" scripts/python/${script} '${JSON.stringify({ trade_date: signalDate, as_of_date: signalDate, ...extra })}'`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 300_000 },
    );
    return JSON.parse(raw);
  } catch (e) {
    return { success: false, error: e.message?.slice(0, 120) };
  }
}

const t5Watch = runPy('p6_watch_t5_closure.py');
runPy('p6_t5_fill_orchestrator.py');
const lreAcc = runPy('lre_oos_accumulator.py');

try {
  execSync('npm run egx:lre:status', { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout: 120_000 });
} catch (e) {
  if (!AS_JSON) console.log(`⚠️  LRE status: ${e.message?.slice(0, 80)}`);
}

writeLiveSessionDaySnapshot(signalDate);
writeLiveKpiSnapshot();
writeResearchClientEnvSnapshot(resolveResearchClientEnv());

let weeklyRun = null;
if (WEEKLY_FULL) {
  weeklyRun = runWeeklyProdReadyFull({ dryRun: false });
} else {
  weeklyRun = writeWeeklyProdReadySnapshot();
}

const phase20 = writePhase20Snapshot(evaluatePhase20OutcomeClosure(signalDate));
const env = resolveResearchClientEnv().env;

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: phase20.phase20_ready,
  phase20,
  t5_watch: t5Watch,
  lre_accumulator: lreAcc,
  weekly_prod: weeklyRun,
  effective_env: env,
  next_session: phase20.next_session,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase20_outcome_closure_report_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`  Phase 20:          ${phase20.phase20_ready ? '✅ READY' : '⏳ monitoring'}`);
  console.log(`  Live anchor:       ${phase20.gates.live_session_anchor.detail}`);
  console.log(`  T5 watch:          ${phase20.gates.t5_watch_closure.detail}`);
  if (t5Watch?.watch?.length) {
    t5Watch.watch.forEach(w => {
      console.log(`    • ${w.symbol} ${w.signal_date} filled=${w.outcome_filled}/5 closed=${w.t5_closed ? 'YES' : 'NO'}`);
    });
  }
  console.log(`  P6 delivered:      ${phase20.gates.p6_delivered_kpi.detail}`);
  console.log(`  LRE OOS:           ${phase20.gates.lre_oos.detail}`);
  console.log(`  Live session:      ${phase20.gates.live_session.pass ? '✅' : '⏳'} — ${phase20.gates.live_session.detail}`);
  console.log(`  Next session:      ${phase20.next_session}`);

  if (phase20.blockers.length) {
    console.log('\n  Blockers:');
    phase20.blockers.forEach(b => console.log(`    • ${b}`));
  }
  console.log('\n  Saved: data/phase20_outcome_closure_last.json');
  console.log('  Saved: data/phase20_outcome_closure_report_last.json\n');
}

process.exit(
  phase20.phase20_ready || phase20.gates.production_graduated.pass ? 0 : 1,
);
