/**
 * Discovery quality loop — measures quant + opportunity discovery precision.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { DB_PATH } from './delivery_audit.mjs';

const HIST_PATH = join(PROJECT_ROOT, 'data/discovery_quality_history.json');
const OUT_PATH = join(PROJECT_ROOT, 'data/discovery_quality_last.json');

function readQuantQuality(db) {
  const has = db.prepare(`
    SELECT name FROM sqlite_master WHERE type='table' AND name='quant_discovery_rules'
  `).get();
  if (!has) return null;

  const cols = new Set(
    db.prepare('PRAGMA table_info(quant_discovery_rules)').all().map(r => r.name),
  );
  const qExpr = cols.has('quality_score')
    ? 'COALESCE(quality_score, composite_score * 0.6)'
    : 'composite_score * 0.6';
  const run = db.prepare(`
    SELECT run_date, COUNT(*) n,
           AVG(${qExpr}) avg_q,
           AVG(oos_precision) avg_prec,
           AVG(stability_score) avg_stab,
           SUM(CASE WHEN ${qExpr} >= 55 THEN 1 ELSE 0 END) high_q
    FROM quant_discovery_rules
    WHERE run_date = (SELECT MAX(run_date) FROM quant_discovery_rules)
  `).get();

  if (!run?.n) return null;

  const sweet = db.prepare(`
    SELECT COUNT(*) n FROM quant_discovery_rules
    WHERE run_date = ?
      AND (
        rule_name LIKE '%lower_third_close%'
        OR rule_name LIKE '%vol_2_5_3%'
        OR rule_name LIKE '%low20_retest%'
      )
  `).get(run.run_date)?.n ?? 0;

  return {
    run_date: run.run_date,
    rules_kept: run.n,
    avg_quality: Math.round((run.avg_q || 0) * 10) / 10,
    avg_oos_precision: Math.round((run.avg_prec || 0) * 1000) / 10,
    avg_stability: Math.round((run.avg_stab || 0) * 1000) / 10,
    high_quality_rules: run.high_q,
    sweet_spot_rules: sweet,
  };
}

function readOpportunityQuality(db, signalDate) {
  const has = db.prepare(`
    SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_score_v2'
  `).get();
  if (!has) return null;

  const date = signalDate || db.prepare(
    'SELECT MAX(trade_date) d FROM opportunity_score_v2',
  ).get()?.d;
  if (!date) return null;

  const agg = db.prepare(`
    SELECT COUNT(*) n,
           AVG(opportunity_score) avg_score,
           SUM(CASE WHEN stage IN ('QUALIFIED_DISCOVERY','ACTIONABLE_CANDIDATE','NEAR_BREAKOUT') THEN 1 ELSE 0 END) qualified_plus,
           SUM(CASE WHEN flags_json LIKE '%LOWER_THIRD_CLOSE%' THEN 1 ELSE 0 END) lower_third,
           SUM(CASE WHEN flags_json LIKE '%VOL_SWEET_SPOT%' THEN 1 ELSE 0 END) vol_sweet,
           SUM(CASE WHEN flags_json LIKE '%NEAR_ATH_LOW_VOL%' THEN 1 ELSE 0 END) near_ath_risk
    FROM opportunity_score_v2 WHERE trade_date = ?
  `).get(date);

  return {
    trade_date: date,
    symbols_scored: agg?.n ?? 0,
    avg_opportunity_score: Math.round((agg?.avg_score || 0) * 10) / 10,
    qualified_plus: agg?.qualified_plus ?? 0,
    lower_third_count: agg?.lower_third ?? 0,
    vol_sweet_spot_count: agg?.vol_sweet ?? 0,
    near_ath_risk_count: agg?.near_ath_risk ?? 0,
  };
}

export function computeDiscoveryQualityScore({ quant = null, opportunity = null } = {}) {
  let score = 50;
  const components = {};

  if (quant) {
    const hq = quant.high_quality_rules / Math.max(quant.rules_kept, 1);
    components.quant_avg_quality = quant.avg_quality;
    components.quant_high_quality_pct = Math.round(hq * 100);
    score += Math.min(15, quant.avg_quality * 0.12);
    score += Math.min(10, hq * 25);
    score += Math.min(6, (quant.sweet_spot_rules / Math.max(quant.rules_kept, 1)) * 30);
  }

  if (opportunity) {
    const qPct = opportunity.qualified_plus / Math.max(opportunity.symbols_scored, 1);
    components.opp_avg_score = opportunity.avg_opportunity_score;
    components.opp_qualified_pct = Math.round(qPct * 100);
    score += Math.min(12, opportunity.avg_opportunity_score * 0.1);
    score += Math.min(8, qPct * 20);
    score -= Math.min(8, (opportunity.near_ath_risk_count / Math.max(opportunity.symbols_scored, 1)) * 40);
    components.lower_third_count = opportunity.lower_third_count;
    components.vol_sweet_spot_count = opportunity.vol_sweet_spot_count;
  }

  const discovery_quality_score = Math.round(Math.max(0, Math.min(100, score)) * 10) / 10;
  const grade = discovery_quality_score >= 75 ? 'A'
    : discovery_quality_score >= 62 ? 'B'
      : discovery_quality_score >= 50 ? 'C' : 'D';

  return { discovery_quality_score, grade, components };
}

export function runDiscoveryQualityLoop(signalDate = null) {
  if (!existsSync(DB_PATH)) {
    return { error: 'NO_DB', signal_date: signalDate };
  }

  const db = new Database(DB_PATH, { readonly: true });
  const quant = readQuantQuality(db);
  const opportunity = readOpportunityQuality(db, signalDate);
  db.close();

  const quality = computeDiscoveryQualityScore({ quant, opportunity });

  const directives = [];
  if (quality.discovery_quality_score < 50) {
    directives.push({
      id: 'discovery_quality_low',
      priority: 'HIGH',
      action: `Discovery quality ${quality.discovery_quality_score}% (grade ${quality.grade}) — tighten quant OOS gates`,
    });
  }
  if (opportunity?.near_ath_risk_count >= 5) {
    directives.push({
      id: 'near_ath_discovery_risk',
      priority: 'MEDIUM',
      action: `${opportunity.near_ath_risk_count} symbols flagged NEAR_ATH_LOW_VOL in opportunity map`,
    });
  }

  const report = {
    at: new Date().toISOString(),
    signal_date: signalDate || opportunity?.trade_date || quant?.run_date,
    quant,
    opportunity,
    ...quality,
    directives,
  };

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(OUT_PATH, JSON.stringify(report, null, 2));

  let hist = { entries: [] };
  if (existsSync(HIST_PATH)) {
    try { hist = JSON.parse(readFileSync(HIST_PATH, 'utf8')); } catch { /* */ }
  }
  hist.entries.push({
    at: report.at,
    signal_date: report.signal_date,
    discovery_quality_score: report.discovery_quality_score,
    grade: report.grade,
    quant_rules: quant?.rules_kept ?? null,
    opp_qualified_pct: quality.components.opp_qualified_pct ?? null,
  });
  hist.entries = hist.entries.slice(-90);
  writeFileSync(HIST_PATH, JSON.stringify(hist, null, 2));

  return report;
}
