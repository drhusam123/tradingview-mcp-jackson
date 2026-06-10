#!/usr/bin/env node
/**
 * Closed learning loop — forensic + counterfactual + knowledge persist.
 * Thinks from outcomes → validates rules → writes structural delivery laws.
 *
 * Usage: node scripts/egx_learning_loop.mjs [--json]
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { getProofLoopMetrics, writeProofLoopSnapshot, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { runCounterfactualSafety } from './lib/counterfactual_safety.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { alertNotification } from './lib/notification_alert.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');

const proof = getProofLoopMetrics();
writeProofLoopSnapshot();
const counter = runCounterfactualSafety();

const directives = [];
if (proof.samples_needed > 0) {
  directives.push({
    id: 'p6_sample_gap',
    priority: 'HIGH',
    action: `Collect ${proof.samples_needed} more ULTRA live outcomes before P6 beta gate`,
    metric: `${proof.n_completed}/${PROOF_MIN_N}`,
  });
}
if (proof.win_rate != null && proof.win_rate < PROOF_MIN_WR) {
  directives.push({
    id: 'p6_wr_below_gate',
    priority: 'HIGH',
    action: `Live ULTRA WR5 ${proof.win_rate}% below ${PROOF_MIN_WR}% — keep behavioral safety veto active`,
    metric: `WR5=${proof.win_rate}%`,
  });
}
if (counter.wr_delta != null && counter.wr_delta > 0) {
  directives.push({
    id: 'counterfactual_wr_lift',
    priority: 'MEDIUM',
    action: `Behavioral filters lift historical ULTRA WR ${counter.actual_wr}% → ${counter.projected_wr}% (+${counter.wr_delta}pp)`,
    metric: `blocked ${counter.would_block_losses} losses / ${counter.would_block} total`,
  });
} else if (counter.wr_delta != null && counter.wr_delta < 0) {
  directives.push({
    id: 'counterfactual_over_block',
    priority: 'HIGH',
    action: `Filters block ${counter.would_block_wins} historical wins — tune behavioral rules before loosening veto`,
    metric: `WR ${counter.actual_wr}% → ${counter.projected_wr}% (${counter.wr_delta}pp)`,
  });
}
if (counter.loss_symbols_still_passing?.length) {
  directives.push({
    id: 'residual_loss_gap',
    priority: 'MEDIUM',
    action: `${counter.loss_symbols_still_passing.length} historical ULTRA losses still pass filters — review setups`,
    symbols: counter.loss_symbols_still_passing.slice(0, 5),
  });
}

const deliveryLaws = [
  {
    id: 'delivery_law_volatile',
    title: 'VOLATILE ULTRA requires vol 2.5–3.5x and RSI≤65',
    source: 'P6 forensic + counterfactual',
    confidence: 'HIGH',
    evidence: counter.block_reason_counts?.behavioral_volatile ?? 0,
  },
  {
    id: 'delivery_law_explosive_rsi',
    title: 'EXPLOSIVE ULTRA blocked when RSI>70',
    source: 'signal_integration Ph27 + P6 forensic',
    confidence: 'HIGH',
    evidence: counter.block_reason_counts?.explosive_rsi ?? 0,
  },
  {
    id: 'delivery_law_p6_gate',
    title: `P6 beta: ≥${PROOF_MIN_N} ULTRA samples at WR≥${PROOF_MIN_WR}%`,
    source: 'EGX_TV_INTEGRATION_ARCHITECTURE Layer 5',
    confidence: 'MANDATORY',
    evidence: `${proof.n_completed}/${PROOF_MIN_N} @ ${proof.win_rate ?? '—'}%`,
  },
];

const report = {
  at: new Date().toISOString(),
  cairo_date: cairoDateParts().date,
  proof_loop: proof,
  counterfactual: counter,
  directives,
  delivery_laws: deliveryLaws,
  p6_ready: proof.gate_pass,
  projected_p6_ready: counter.p6_gate_pass_projected,
};

const dataDir = join(PROJECT_ROOT, 'data');
const kbDir = join(dataDir, 'knowledge_base');
mkdirSync(kbDir, { recursive: true });

writeFileSync(join(dataDir, 'learning_loop_last.json'), JSON.stringify(report, null, 2));
writeFileSync(
  join(kbDir, `delivery_laws_${cairoDateParts().date}.json`),
  JSON.stringify({ generated_at: report.at, n_laws: deliveryLaws.length, laws: deliveryLaws, directives }, null, 2),
);

if (proof.n_completed >= PROOF_MIN_N - 4 && proof.win_rate != null && proof.win_rate < PROOF_MIN_WR - 5) {
  alertNotification('P6_WR_WARNING', {
    wr: proof.win_rate,
    n: proof.n_completed,
    counterfactual_wr: counter.projected_wr,
  });
}

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

console.log('\n═══ EGX Learning Loop (closed) ═══\n');
console.log(`  P6 live:     ${proof.n_completed}/${PROOF_MIN_N} ULTRA | WR5 ${proof.win_rate ?? '—'}%`);
console.log(`  Counterfact: ${counter.actual_wr}% → ${counter.projected_wr}% (+${counter.wr_delta ?? 0}pp)`);
console.log(`  Would block: ${counter.would_block} (${counter.would_block_losses} losses, ${counter.would_block_wins} wins)`);
console.log(`  P6 projected:${counter.p6_gate_pass_projected ? ' ✅ PASS' : ' ⏳ pending'}\n`);

if (directives.length) {
  console.log('  Directives:');
  directives.forEach(d => console.log(`    [${d.priority}] ${d.action}`));
  console.log('');
}

console.log(`  Saved: data/learning_loop_last.json`);
console.log(`         data/knowledge_base/delivery_laws_${cairoDateParts().date}.json\n`);
