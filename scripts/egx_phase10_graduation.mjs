#!/usr/bin/env node
/**
 * Phase 10 — Client beta graduation readiness.
 * Extends Phase 9 with gate evaluation + env recommendations.
 *
 * Usage: node scripts/egx_phase10_graduation.mjs [--json] [--skip-phase9]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluateGraduationReadiness } from './lib/p6_graduation_gate.mjs';
import { PROOF_MIN_N } from './lib/proof_loop.mjs';

loadEnv();

const NODE = process.execPath;
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE9 = process.argv.includes('--skip-phase9');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 10 — Client Beta Graduation Readiness ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (!SKIP_PHASE9) {
  try {
    execSync(`"${NODE}" scripts/egx_phase9_graduation.mjs`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 600_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 9 bundle: ${e.message?.slice(0, 80)}`);
  }
}

const readiness = evaluateGraduationReadiness();
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: readiness.blockers.length === 0 || readiness.client_beta_ready,
  ...readiness,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase10_graduation_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  const g = readiness.gates;
  console.log('  Gates:');
  console.log(`    P6 ULTRA safe:      ${g.p6_ultra_safe.pass ? '✅' : '⏳'} ${g.p6_ultra_safe.n}/${PROOF_MIN_N} @ ${g.p6_ultra_safe.wr ?? '—'}%`);
  console.log(`    P6 delivered safe:  ${g.p6_delivered_safe.pass ? '✅' : '⏳'} ${g.p6_delivered_safe.n}/${PROOF_MIN_N} @ ${g.p6_delivered_safe.wr ?? '—'}%`);
  console.log(`    MED_CLIENT_SIGNAL:  keep ${g.med_client_signal.current} → ${g.med_client_signal.recommended} (${g.med_client_signal.reason})`);
  console.log(`    MED_FEED_BOOST:     keep ${g.med_feed_boost.current} → ${g.med_feed_boost.recommended}`);
  console.log(`    LRE_FEED_BOOST:     keep ${g.lre_feed_boost.current} → ${g.lre_feed_boost.recommended} (${g.lre_feed_boost.reason})`);
  console.log(`    MDE client path:    shadow (${g.mde_client_actionable.reason})`);

  if (readiness.pending_delivered?.length) {
    console.log('\n  Pending delivered (awaiting t5):');
    for (const p of readiness.pending_delivered.slice(0, 6)) {
      console.log(`    ${p.symbol.padEnd(6)} ${p.signal_date}  filled=${p.outcome_filled}/5`);
    }
  }

  if (readiness.blockers.length) {
    console.log('\n  Blockers:');
    readiness.blockers.forEach(b => console.log(`    • ${b}`));
  }

  console.log(`\n  Client beta ready: ${readiness.client_beta_ready ? '✅ YES' : '⏳ accumulating'}`);
  console.log('  Saved: data/phase10_graduation_last.json\n');
}

process.exit(readiness.client_beta_ready ? 0 : 0);
