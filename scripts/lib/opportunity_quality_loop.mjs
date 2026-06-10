/**
 * Opportunity quality loop — tracks high-opportunity names through gates → delivery.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { DB_PATH } from './delivery_audit.mjs';
import { runEgxSafetyCheck } from './egx_safety_check.mjs';

export function runOpportunityQualityLoop(signalDate) {
  if (!existsSync(DB_PATH)) return { error: 'NO_DB', signal_date: signalDate };

  const db = new Database(DB_PATH, { readonly: true });

  const hasOpp = db.prepare(`
    SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_score_v2'
  `).get();
  if (!hasOpp) {
    db.close();
    return { signal_date: signalDate, error: 'NO_OPPORTUNITY_TABLE' };
  }

  const topOpp = db.prepare(`
    SELECT symbol, opportunity_score, stage, structure_score, risk_score
    FROM opportunity_score_v2
    WHERE trade_date = ? AND opportunity_score >= 70
    ORDER BY opportunity_score DESC
    LIMIT 30
  `).all(signalDate);

  const actionable = db.prepare(`
    SELECT symbol, score, setup_type, actionable, veto_reason
    FROM final_signals WHERE trade_date = ? AND COALESCE(actionable, 0) = 1
  `).all(signalDate);

  const delivered = db.prepare(`
    SELECT DISTINCT symbol FROM notification_delivery_audit
    WHERE signal_date = ? AND send_success = 1 AND dry_run = 0 AND deliverable = 1
  `).all(signalDate).map(r => r.symbol);

  db.close();

  const safety = runEgxSafetyCheck(signalDate, { veto: true });
  const blockedSet = new Set(safety.blocked_symbols);
  const actionableSet = new Set(actionable.map(r => r.symbol));
  const deliveredSet = new Set(delivered);

  const pipeline = topOpp.map(o => ({
    symbol: o.symbol,
    opportunity_score: o.opportunity_score,
    stage: o.stage,
    actionable: actionableSet.has(o.symbol),
    delivered: deliveredSet.has(o.symbol),
    safety_blocked: blockedSet.has(o.symbol),
    missed_high_opp: o.opportunity_score >= 75 && !actionableSet.has(o.symbol),
  }));

  const highOppDelivered = pipeline.filter(p => p.delivered && p.opportunity_score >= 75);
  const highOppBlocked = pipeline.filter(p => p.safety_blocked && p.opportunity_score >= 75);
  const missed = pipeline.filter(p => p.missed_high_opp);

  const report = {
    signal_date: signalDate,
    n_top_opportunity: topOpp.length,
    n_actionable: actionable.length,
    n_delivered: delivered.length,
    n_safety_blocked: safety.blocked_symbols.length,
    avg_opp_delivered: highOppDelivered.length
      ? Math.round(highOppDelivered.reduce((s, p) => s + p.opportunity_score, 0) / highOppDelivered.length * 10) / 10
      : null,
    quality_score: highOppDelivered.length && pipeline.length
      ? Math.round((highOppDelivered.length / Math.max(1, pipeline.length)) * 100)
      : 0,
    missed_high_opportunity: missed.slice(0, 10),
    blocked_high_opportunity: highOppBlocked.slice(0, 10),
    pipeline_sample: pipeline.slice(0, 15),
    directives: [
      ...(missed.length >= 3 ? [{
        id: 'opp_missed_high',
        priority: 'MEDIUM',
        action: `${missed.length} symbols with opp≥75 not actionable — review promotion/scoring gates`,
      }] : []),
      ...(highOppBlocked.length >= 2 ? [{
        id: 'opp_blocked_safety',
        priority: 'MEDIUM',
        action: `${highOppBlocked.length} high-opp symbols blocked at safety — validate veto rules`,
        symbols: highOppBlocked.map(p => p.symbol).slice(0, 5),
      }] : []),
    ],
  };

  persistOpportunityHistory(report);
  return report;
}

function persistOpportunityHistory(report) {
  const histPath = join(PROJECT_ROOT, 'data/opportunity_quality_history.json');
  let hist = { entries: [] };
  if (existsSync(histPath)) {
    try { hist = JSON.parse(readFileSync(histPath, 'utf8')); } catch { /* */ }
  }
  hist.entries.push({
    at: new Date().toISOString(),
    signal_date: report.signal_date,
    n_top_opportunity: report.n_top_opportunity,
    n_delivered: report.n_delivered,
    n_safety_blocked: report.n_safety_blocked,
    quality_score: report.quality_score,
    missed: report.missed_high_opportunity?.length ?? 0,
  });
  hist.entries = hist.entries.slice(-90);
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(histPath, JSON.stringify(hist, null, 2));
}
