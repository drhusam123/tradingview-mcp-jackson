#!/usr/bin/env node
/**
 * Backfill client-delivered outcomes from OHLCV (no live session wait).
 *
 * Usage: node scripts/egx_p6_historical_backfill.mjs [--json]
 */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import {
  runHistoricalOutcomeBackfill,
  writeHistoricalProofSnapshot,
  getBootstrapProofMetrics,
  BOOTSTRAP_MIN_N,
  BOOTSTRAP_MIN_WR,
  GRADUATION_MODE,
} from './lib/p6_historical_proof.mjs';
import { PROOF_MIN_N } from './lib/proof_loop.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');

console.log('\n═══ P6 Historical Outcome Backfill ═══');
console.log(`  Mode: ${GRADUATION_MODE}\n`);

const backfill = runHistoricalOutcomeBackfill({ lookbackDays: 365 });
const snap = writeHistoricalProofSnapshot(backfill);
const b = getBootstrapProofMetrics();

if (AS_JSON) {
  console.log(JSON.stringify({ backfill, bootstrap: b }, null, 2));
} else {
  for (const s of backfill.steps) {
    console.log(`  ${s.step}: ${s.ok === false ? '⚠️' : '✅'} ${JSON.stringify(s).slice(0, 100)}`);
  }
  const u = b.ultra_safe;
  console.log(`\n  ULTRA safe (historical): ${u.n_completed}/${BOOTSTRAP_MIN_N} @ ${u.win_rate ?? '—'}%`);
  console.log(`  Delivered safe:          ${b.delivered_safe.n_completed} @ ${b.delivered_safe.win_rate ?? '—'}%`);
  console.log(`  Bootstrap gate:          ${b.bootstrap_pass ? '✅ PASS' : '⏳ pending'}`);
  console.log(`  Live full KPI (non-blocking): ${u.n_completed}/${PROOF_MIN_N} @ ${u.win_rate ?? '—'}%`);
  console.log('  Saved: data/p6_historical_proof_last.json\n');
}

process.exit(backfill.ok ? 0 : 1);
