/**
 * P6 research context — bundles closed-loop outputs for evolution/cognition/discovery.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { DB_PATH } from './delivery_audit.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import { readDiscoveryFeedback } from './discovery_feedback.mjs';

export const P6_CONTEXT_PATH = join(PROJECT_ROOT, 'data/p6_research_context.json');

function queryUltraLosses(limit = 30) {
  if (!existsSync(DB_PATH)) return [];
  const db = new Database(DB_PATH, { readonly: true });
  try {
    const hasTable = db.prepare(`
      SELECT name FROM sqlite_master WHERE type='table' AND name='recommendation_outcomes'
    `).get();
    if (!hasTable) return [];
    return db.prepare(`
      SELECT symbol, signal_date, behavioral_class, return_t5, hit_t5
      FROM recommendation_outcomes
      WHERE conviction_tier = 'ULTRA_CONVICTION'
        AND outcome_filled >= 5
        AND hit_t5 = 0
      ORDER BY signal_date DESC
      LIMIT ?
    `).all(limit);
  } finally {
    db.close();
  }
}

function queryUltraWins(limit = 15) {
  if (!existsSync(DB_PATH)) return [];
  const db = new Database(DB_PATH, { readonly: true });
  try {
    const hasTable = db.prepare(`
      SELECT name FROM sqlite_master WHERE type='table' AND name='recommendation_outcomes'
    `).get();
    if (!hasTable) return [];
    return db.prepare(`
      SELECT symbol, signal_date, behavioral_class, return_t5, hit_t5
      FROM recommendation_outcomes
      WHERE conviction_tier = 'ULTRA_CONVICTION'
        AND outcome_filled >= 5
        AND hit_t5 = 1
      ORDER BY signal_date DESC
      LIMIT ?
    `).all(limit);
  } finally {
    db.close();
  }
}

export function buildP6ResearchContext({
  signalDate = null,
  learning = null,
  forensic = null,
  discovery = null,
  opportunity = null,
  discoveryQuality = null,
  oppFollowup = null,
  ingested = null,
} = {}) {
  const proofAll = getProofLoopMetrics();
  const proofDelivered = getProofLoopMetrics({ deliveredOnly: true });
  const feedback = discovery || readDiscoveryFeedback();
  const ultraLosses = queryUltraLosses();
  const ultraWins = queryUltraWins();

  const downrank = (feedback?.queue || [])
    .filter(i => i.type === 'DOWNRANK_BEHAVIORAL')
    .map(i => i.target);
  const investigate = (feedback?.queue || [])
    .filter(i => i.type === 'INVESTIGATE_PATTERN')
    .map(i => i.target);

  const ctx = {
    at: new Date().toISOString(),
    signal_date: signalDate,
    p6_gate: {
      n_completed: proofAll.n_completed,
      win_rate: proofAll.win_rate,
      gate_pass: proofAll.gate_pass,
      samples_needed: proofAll.samples_needed,
      min_n: PROOF_MIN_N,
      min_wr: PROOF_MIN_WR,
    },
    p6_delivered: {
      n_completed: proofDelivered.n_completed,
      win_rate: proofDelivered.win_rate,
      delivered_only: true,
    },
    discovery_feedback: feedback,
    forensic_summary: forensic ? {
      n_losses: forensic.n_losses,
      by_class: forensic.by_class,
    } : null,
    loss_autopsy: learning?.loss_autopsy ? {
      n_residual_losses: learning.loss_autopsy.n_residual_losses,
      flag_counts: learning.loss_autopsy.flag_counts,
    } : null,
    counterfactual: learning?.counterfactual ? {
      projected_wr: learning.counterfactual.projected_wr,
      would_block_losses: learning.counterfactual.would_block_losses,
      would_block_wins: learning.counterfactual.would_block_wins,
    } : null,
    opportunity_snapshot: opportunity ? {
      quality_score: opportunity.quality_score,
      n_delivered: opportunity.n_delivered,
      n_top_opportunity: opportunity.n_top_opportunity,
      missed_high_opportunity: opportunity.missed_high_opportunity?.length ?? 0,
      discovery_quality_score: opportunity.discovery_quality_score ?? discoveryQuality?.discovery_quality_score,
    } : null,
    discovery_quality: discoveryQuality ? {
      score: discoveryQuality.discovery_quality_score,
      grade: discoveryQuality.grade,
      quant_avg_quality: discoveryQuality.quant?.avg_quality ?? null,
      sweet_spot_rules: discoveryQuality.quant?.sweet_spot_rules ?? null,
      opp_qualified_pct: discoveryQuality.components?.opp_qualified_pct ?? null,
      near_ath_risk: discoveryQuality.opportunity?.near_ath_risk_count ?? null,
    } : null,
    opportunity_trend: oppFollowup ? {
      alerts: oppFollowup.alerts?.length ?? 0,
      trends: oppFollowup.trends,
      top_alert: oppFollowup.alerts?.[0]?.code ?? null,
    } : null,
    ultra_losses: ultraLosses,
    ultra_wins: ultraWins,
    evolution_hints: {
      downrank_behavioral: downrank,
      investigate_patterns: investigate,
      loss_symbols: ultraLosses.map(r => r.symbol),
      win_symbols: ultraWins.map(r => r.symbol),
    },
    cognition_hints: {
      prioritize_explosive_review: downrank.includes('EXPLOSIVE'),
      explosion_loss_count: ultraLosses.filter(r => r.behavioral_class === 'EXPLOSIVE').length,
      pattern_flags: investigate,
    },
    directives_ingested: ingested?.ingested ?? 0,
    research_priorities: (feedback?.queue || []).slice(0, 8).map(q => ({
      type: q.type,
      target: q.target,
      priority: q.priority,
      rationale: q.rationale,
    })),
  };

  return ctx;
}

export function writeP6ResearchContext(ctx) {
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(P6_CONTEXT_PATH, JSON.stringify(ctx, null, 2));
  return { path: P6_CONTEXT_PATH, at: ctx.at };
}

export function loadP6ResearchContext() {
  if (!existsSync(P6_CONTEXT_PATH)) return null;
  try {
    return JSON.parse(readFileSync(P6_CONTEXT_PATH, 'utf8'));
  } catch {
    return null;
  }
}
