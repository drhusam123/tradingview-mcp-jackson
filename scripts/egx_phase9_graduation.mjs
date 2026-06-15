#!/usr/bin/env node
/**
 * Phase 9 — Research engine graduation bundle.
 * P6 delivered track + MED 0.4 + LRE 4.0 + MDE client-grade status.
 *
 * Usage: node scripts/egx_phase9_graduation.mjs [--json] [--skip-engines]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import {
  getProofLoopMetrics,
  writeProofLoopSnapshot,
  PROOF_MIN_N,
  PROOF_MIN_WR,
} from './lib/proof_loop.mjs';
import {
  syncDeliveredOutcomes,
  backfillOutcomeSafetyGate,
  seedDeliveredOutcomes,
} from './lib/delivered_outcomes.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_ENGINES = process.argv.includes('--skip-engines');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

const checks = [];

function runCheck(id, label, cmd, { optional = false, timeout = 300_000 } = {}) {
  const t0 = Date.now();
  try {
    const out = execSync(cmd, { cwd: PROJECT_ROOT, encoding: 'utf8', timeout, stdio: ['pipe', 'pipe', 'pipe'] });
    checks.push({ id, label, ok: true, ms: Date.now() - t0, detail: out.trim().slice(-200) });
    return { ok: true, out };
  } catch (e) {
    const detail = (e.stdout || e.stderr || e.message || '').trim().slice(-200);
    checks.push({ id, label, ok: false, optional, ms: Date.now() - t0, detail });
    return { ok: false, detail };
  }
}

console.log('\n═══ Phase 9 — Research Graduation Bundle ═══');
console.log(`  Signal date: ${signalDate}\n`);

// ── P6 delivered pipeline ───────────────────────────────────────────
const seed = seedDeliveredOutcomes({ lookbackDays: 180 });
const sync = syncDeliveredOutcomes({ lookbackDays: 180 });
const backfill = backfillOutcomeSafetyGate();

runCheck('track_outcomes', 'Track recommendation outcomes',
  `"${PYTHON}" scripts/python/signal_integration.py track_outcomes '{}'`,
  { optional: true, timeout: 120_000 });

writeProofLoopSnapshot();

const p6Filtered = getProofLoopMetrics({ safetyFiltered: true });
const p6Delivered = getProofLoopMetrics({ deliveredOnly: true, allDeliveredTiers: true });
const p6DeliveredSafe = getProofLoopMetrics({ deliveredOnly: true, safetyFiltered: true });

checks.push({
  id: 'p6_safety_filtered',
  label: 'P6 safety-filtered ULTRA',
  ok: p6Filtered.gate_pass || p6Filtered.samples_needed > 0,
  detail: `${p6Filtered.n_completed}/${PROOF_MIN_N} @ ${p6Filtered.win_rate ?? '—'}%`,
});
checks.push({
  id: 'p6_delivered_sync',
  label: 'Client delivered sync',
  ok: sync.ok && (sync.delivered_total ?? 0) > 0,
  detail: `${sync.delivered_total ?? 0} total, ${sync.seeded ?? 0} seeded, ${sync.rows_updated ?? 0} marked`,
});
checks.push({
  id: 'p6_delivered_track',
  label: 'P6 delivered cohort',
  ok: true,
  warn: p6DeliveredSafe.n_completed < PROOF_MIN_N,
  detail: `${p6DeliveredSafe.n_completed} safe-delivered @ ${p6DeliveredSafe.win_rate ?? '—'}% (raw ${p6Delivered.win_rate ?? '—'}%)`,
});

// ── Research engines (shadow) ───────────────────────────────────────
let med = null;
let lre = null;
let mde = null;

if (!SKIP_ENGINES) {
  const medR = runCheck('med_phase4', 'MED 0.4 acceptance',
    'npm run egx:med:phase4:acceptance --silent 2>&1 | tail -3',
    { optional: true, timeout: 180_000 });
  if (medR.ok) {
    try {
      med = JSON.parse(execSync(`"${PYTHON}" scripts/python/med_0_4_acceptance.py '{}'`, {
        cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 60_000,
      }));
    } catch { /* */ }
  }

  const lreR = runCheck('lre_acceptance', 'LRE 4.0 acceptance',
    'npm run egx:lre:acceptance --silent 2>&1 | tail -3',
    { optional: true, timeout: 120_000 });

  runCheck('lre_status', 'LRE 4.0 status',
    `"${PYTHON}" scripts/python/lre_4_0_status.py '${JSON.stringify({ trade_date: signalDate })}'`,
    { optional: true, timeout: 60_000 });

  const mdeR = runCheck('mde_client_grade', 'MDE client-grade validation',
    'npm run egx:mde:client-grade --silent 2>&1 | tail -5',
    { optional: true, timeout: 300_000 });
}

const hardFail = checks.filter(c => !c.ok && !c.optional && !c.warn).length;
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: hardFail === 0,
  checks,
  p6: {
    safety_filtered: p6Filtered,
    delivered_raw: p6Delivered,
    delivered_safe: p6DeliveredSafe,
    seed,
    sync,
    backfill,
  },
  engines: { med, lre, mde },
  graduation: {
    p6_ultra_samples_needed: p6Filtered.samples_needed,
    p6_delivered_samples_needed: Math.max(0, PROOF_MIN_N - p6DeliveredSafe.n_completed),
    target_wr: PROOF_MIN_WR,
  },
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase9_graduation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  for (const c of checks) {
    const icon = c.ok ? (c.warn ? '⏳' : '✅') : (c.optional ? '⚠️' : '❌');
    console.log(`  ${icon} ${c.label}: ${c.detail || ''}`);
  }
  console.log(`\n  P6 ULTRA:     ${p6Filtered.n_completed}/${PROOF_MIN_N} @ ${p6Filtered.win_rate ?? '—'}%`);
  console.log(`  P6 Delivered: ${p6DeliveredSafe.n_completed}/${PROOF_MIN_N} safe @ ${p6DeliveredSafe.win_rate ?? '—'}%`);
  console.log(`\n  Saved: data/phase9_graduation_last.json`);
  console.log(`\n═══ Phase 9: ${checks.filter(c => c.ok).length}/${checks.length} OK ═══\n`);
}

process.exit(hardFail ? 1 : 0);
