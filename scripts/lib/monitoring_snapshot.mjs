/**
 * Unified monitoring snapshot — P6 + closed loops + directives.
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import { runCounterfactualSafety } from './counterfactual_safety.mjs';
import { auditClosedLoops } from './loop_audit.mjs';
import { countDirectiveStats } from './directive_resolver.mjs';
import { loadP6ResearchContext } from './p6_research_context.mjs';
import { cairoDateParts } from './egx_calendar.mjs';

export function buildMonitoringSnapshot() {
  const proof = getProofLoopMetrics();
  const delivered = getProofLoopMetrics({ deliveredOnly: true });
  const counter = runCounterfactualSafety();
  const loopAudit = auditClosedLoops({ maxAgeHours: 168 });
  const directives = countDirectiveStats();
  const p6Ctx = loadP6ResearchContext();

  return {
    at: new Date().toISOString(),
    cairo_date: cairoDateParts().date,
    p6: {
      n_completed: proof.n_completed,
      win_rate: proof.win_rate,
      gate_pass: proof.gate_pass,
      samples_needed: proof.samples_needed,
      target_n: PROOF_MIN_N,
      target_wr: PROOF_MIN_WR,
    },
    delivered: {
      n_completed: delivered.n_completed,
      win_rate: delivered.win_rate,
    },
    counterfactual: {
      actual_wr: counter.actual_wr,
      projected_wr: counter.projected_wr,
      would_block_losses: counter.would_block_losses,
      would_block_wins: counter.would_block_wins,
      residual_losses: counter.loss_symbols_still_passing?.length ?? 0,
    },
    closed_loops: {
      audit_pass: loopAudit.pass,
      failed_checks: loopAudit.checks.filter(c => !c.ok).map(c => c.id),
      closed_loop_at: loopAudit.closed_loop_at,
    },
    directives,
    p6_context_at: p6Ctx?.at ?? null,
    ultra_losses_in_context: p6Ctx?.ultra_losses?.length ?? 0,
    downrank_behavioral: p6Ctx?.evolution_hints?.downrank_behavioral ?? [],
    discovery_feedback_items: p6Ctx?.discovery_feedback?.n_items ?? 0,
    opportunity_alerts: p6Ctx?.opportunity_trend?.alerts ?? 0,
  };
}

export function writeMonitoringSnapshot() {
  const snap = buildMonitoringSnapshot();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  const path = join(PROJECT_ROOT, 'data/monitoring_snapshot.json');
  writeFileSync(path, JSON.stringify(snap, null, 2));
  return { path, ...snap };
}
