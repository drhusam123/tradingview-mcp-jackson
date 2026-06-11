/**
 * Unified discovery context — P6 closed loop → quant / opportunity / promotion.
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { loadDiscoveryFeedback, readPendingResearchDirectives } from './load_discovery_feedback.mjs';

const P6_CTX_PATH = join(PROJECT_ROOT, 'data/p6_research_context.json');
const OPP_FOLLOWUP_PATH = join(PROJECT_ROOT, 'data/opportunity_followup_last.json');
const DISCOVERY_QUALITY_PATH = join(PROJECT_ROOT, 'data/discovery_quality_last.json');

export function readP6ResearchContext() {
  if (!existsSync(P6_CTX_PATH)) return null;
  try {
    return JSON.parse(readFileSync(P6_CTX_PATH, 'utf8'));
  } catch {
    return null;
  }
}

export function readOpportunityFollowup() {
  if (!existsSync(OPP_FOLLOWUP_PATH)) return null;
  try {
    return JSON.parse(readFileSync(OPP_FOLLOWUP_PATH, 'utf8'));
  } catch {
    return null;
  }
}

function mergeFeedbackQueues(primary = [], secondary = []) {
  const seen = new Set();
  const out = [];
  for (const item of [...primary, ...secondary]) {
    const key = `${item.type}|${item.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out.sort((a, b) => (b.priority || 0) - (a.priority || 0));
}

/**
 * Build JSON params for quant_discovery.py / client_signal_promotion.py.
 */
export function buildDiscoveryParams({ signalDate = null, includeDirectives = true } = {}) {
  const feedback = loadDiscoveryFeedback();
  const p6 = readP6ResearchContext();
  const oppFollowup = readOpportunityFollowup();
  const directives = includeDirectives ? readPendingResearchDirectives(12) : [];

  const p6Queue = p6?.discovery_feedback?.queue || p6?.research_priorities || [];
  const queue = mergeFeedbackQueues(feedback.queue || [], p6Queue);

  let discoveryQuality = null;
  if (existsSync(DISCOVERY_QUALITY_PATH)) {
    try { discoveryQuality = JSON.parse(readFileSync(DISCOVERY_QUALITY_PATH, 'utf8')); } catch { /* */ }
  }
  const dqScore = discoveryQuality?.discovery_quality_score ?? p6?.discovery_quality?.score;
  const strictQuality = dqScore != null && dqScore < 52;

  const params = {
    feedback_queue: queue,
    p6_priorities: p6?.research_priorities || [],
    evolution_hints: p6?.evolution_hints || {},
    p6_gate: p6?.p6_gate || {},
    p6_directives: directives.map(d => d.target),
    opportunity_followup: oppFollowup,
    signal_date: signalDate || p6?.signal_date || null,
    strict_quality: strictQuality,
    discovery_quality_score: dqScore ?? null,
  };

  return {
    params,
    feedback: { ...feedback, queue, n_items: queue.length },
    p6,
    oppFollowup,
    directives,
    discoveryQuality,
  };
}

export function discoveryContextSummary(ctx) {
  const { feedback, p6, oppFollowup, directives, discoveryQuality } = ctx;
  return {
    feedback_items: feedback?.n_items ?? 0,
    p6_signal_date: p6?.signal_date ?? null,
    p6_gate_pass: p6?.p6_gate?.gate_pass ?? null,
    opp_alerts: oppFollowup?.alerts?.length ?? 0,
    pending_directives: directives?.length ?? 0,
    discovery_quality_score: discoveryQuality?.discovery_quality_score ?? p6?.discovery_quality?.score ?? null,
    discovery_grade: discoveryQuality?.grade ?? p6?.discovery_quality?.grade ?? null,
  };
}
