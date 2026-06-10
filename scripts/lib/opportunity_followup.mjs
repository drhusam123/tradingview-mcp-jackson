/**
 * Opportunity followup — trend analysis from opportunity_quality_history.json.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const HIST_PATH = join(PROJECT_ROOT, 'data/opportunity_quality_history.json');
const OUT_PATH = join(PROJECT_ROOT, 'data/opportunity_followup_last.json');

function loadHistory() {
  if (!existsSync(HIST_PATH)) return { entries: [] };
  try {
    return JSON.parse(readFileSync(HIST_PATH, 'utf8'));
  } catch {
    return { entries: [] };
  }
}

function trendDelta(entries, field, window = 5) {
  if (entries.length < 2) return { delta: 0, recent: null, prior: null };
  const recent = entries.slice(-window);
  const prior = entries.slice(-window * 2, -window);
  if (!prior.length) return { delta: 0, recent: null, prior: null };
  const avg = (arr) => arr.reduce((s, e) => s + (e[field] ?? 0), 0) / arr.length;
  const r = avg(recent);
  const p = avg(prior);
  return { delta: Math.round((r - p) * 10) / 10, recent: r, prior: p };
}

export function analyzeOpportunityTrend({ window = 5 } = {}) {
  const hist = loadHistory();
  const entries = hist.entries || [];
  const quality = trendDelta(entries, 'quality_score', window);
  const missed = trendDelta(entries, 'missed', window);
  const blocked = trendDelta(entries, 'n_safety_blocked', window);
  const delivered = trendDelta(entries, 'n_delivered', window);

  const alerts = [];
  const directives = [];

  if (quality.delta <= -10 && entries.length >= window * 2) {
    alerts.push({
      severity: 'HIGH',
      code: 'QUALITY_DECLINING',
      message: `Opportunity quality score fell ${quality.delta} pts (${quality.prior} → ${quality.recent})`,
    });
    directives.push({
      id: 'opp_quality_decline',
      priority: 'HIGH',
      action: 'Review promotion gates — high-opp delivery rate declining',
    });
  }

  if (missed.delta >= 2 && entries.length >= window * 2) {
    alerts.push({
      severity: 'MEDIUM',
      code: 'MISSED_HIGH_OPP_RISING',
      message: `Missed high-opportunity names up +${missed.delta} per session`,
    });
    directives.push({
      id: 'opp_missed_trend',
      priority: 'MEDIUM',
      action: 'Tune client_signal_promotion — sustained missed high-opp trend',
    });
  }

  if (blocked.delta >= 3 && entries.length >= window * 2) {
    alerts.push({
      severity: 'MEDIUM',
      code: 'SAFETY_BLOCKS_RISING',
      message: `Safety blocks on high-opp pipeline up +${blocked.delta}`,
    });
    directives.push({
      id: 'opp_safety_collateral',
      priority: 'MEDIUM',
      action: 'Counterfactual review — safety veto collateral on opportunity pipeline',
    });
  }

  if (delivered.delta >= 1 && quality.delta >= 5) {
    alerts.push({
      severity: 'INFO',
      code: 'DELIVERY_IMPROVING',
      message: `Delivered high-opp count improving (+${delivered.delta}) with quality +${quality.delta}`,
    });
  }

  const report = {
    at: new Date().toISOString(),
    n_sessions: entries.length,
    window,
    trends: { quality, missed, blocked, delivered },
    alerts,
    directives,
    last_sessions: entries.slice(-window),
  };

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(OUT_PATH, JSON.stringify(report, null, 2));
  return report;
}
