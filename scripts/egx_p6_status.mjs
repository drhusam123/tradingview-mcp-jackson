#!/usr/bin/env node
/**
 * P6 proof loop status — samples, WR5, gate, projected counterfactual.
 * Usage: node scripts/egx_p6_status.mjs [--json]
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { getProofLoopMetrics, formatProofLoopLine, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { runCounterfactualSafety } from './lib/counterfactual_safety.mjs';
import { nextTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const proof = getProofLoopMetrics();
const counter = runCounterfactualSafety();

const report = {
  at: new Date().toISOString(),
  cairo_date: cairoDateParts().date,
  next_session: nextTradingDay(cairoDateParts().date).next_trading_day,
  p6: {
    n_completed: proof.n_completed,
    samples_needed: proof.samples_needed,
    win_rate: proof.win_rate,
    target_wr: PROOF_MIN_WR,
    target_n: PROOF_MIN_N,
    gate_pass: proof.gate_pass,
    gate_reason: proof.gate_reason,
  },
  counterfactual: {
    projected_wr: counter.projected_wr,
    would_block_losses: counter.would_block_losses,
    would_block_wins: counter.would_block_wins,
    residual_losses: counter.loss_symbols_still_passing?.length ?? 0,
  },
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/p6_status_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

console.log('\n═══ P6 Proof Loop Status ═══\n');
console.log(`  ${formatProofLoopLine(proof)}`);
console.log(`  Samples needed: ${proof.samples_needed} → next gate check after ${PROOF_MIN_N} ULTRA @ ≥${PROOF_MIN_WR}%`);
console.log(`  Counterfactual: ${counter.actual_wr}% → ${counter.projected_wr}% | block ${counter.would_block_losses}L/${counter.would_block_wins}W`);
console.log(`  Residual losses: ${counter.loss_symbols_still_passing?.length ?? 0}`);
console.log(`  Next session:    ${report.next_session}\n`);
console.log('  Saved: data/p6_status_last.json\n');
