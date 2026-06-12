#!/usr/bin/env node
/**
 * EGX Signal Funnel Audit
 * =======================
 * Shows where symbols drop out of the client pipeline and why.
 *
 * Usage:
 *   node scripts/egx_signal_funnel.mjs
 *   node scripts/egx_signal_funnel.mjs --date 2026-06-08
 */

import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { writeFileSync, mkdirSync } from 'fs';
import { latestFinalSignalDate } from './lib/final_signals_query.mjs';
import { PROJECT_ROOT } from './lib/load_env.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB = join(__dirname, '../data/egx_trading.db');

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const AS_JSON = process.argv.includes('--json');

function section(title) {
  console.log(`\n${'═'.repeat(60)}\n  ${title}\n${'═'.repeat(60)}`);
}

const db = new Database(DB, { readonly: true });
const tradeDate = dateArg || latestFinalSignalDate(db);

if (!tradeDate) {
  console.error('No final_signals data');
  process.exit(1);
}

section(`Signal Funnel — ${tradeDate}`);

const totals = db.prepare(`
  SELECT COUNT(*) tot,
         SUM(CASE WHEN actionable=1 THEN 1 ELSE 0 END) act,
         ROUND(AVG(score),1) avg_ues,
         ROUND(AVG(source_ml),1) avg_ml,
         ROUND(AVG(source_rules),1) avg_scan
  FROM final_signals WHERE trade_date=?
`).get(tradeDate);

console.log(`  Symbols scored : ${totals.tot}`);
console.log(`  Actionable     : ${totals.act} (${((totals.act / totals.tot) * 100).toFixed(1)}%)`);
console.log(`  Avg UES / ML / Scan : ${totals.avg_ues} / ${totals.avg_ml} / ${totals.avg_scan}`);

section('Veto Breakdown');
const vetoes = db.prepare(`
  SELECT COALESCE(veto_reason, 'ACTIONABLE') reason, COUNT(*) n,
         ROUND(MAX(score),1) top_ues, ROUND(MAX(source_ml),1) top_ml
  FROM final_signals WHERE trade_date=?
  GROUP BY COALESCE(veto_reason, 'ACTIONABLE')
  ORDER BY n DESC LIMIT 15
`).all(tradeDate);
for (const v of vetoes) {
  console.log(`  ${String(v.n).padStart(4)}  ${v.reason?.slice(0, 50).padEnd(50)}  UES≤${v.top_ues} ML≤${v.top_ml}`);
}

let gateBlockers = [];
try {
  gateBlockers = db.prepare(`
    SELECT first_blocking_gate gate, COUNT(*) n,
           ROUND(AVG(ues),1) avg_ues, ROUND(AVG(ml_score),1) avg_ml
    FROM gate_audit_snapshots
    WHERE signal_date=? AND COALESCE(actionable,0)=0
    GROUP BY first_blocking_gate
    ORDER BY n DESC LIMIT 20
  `).all(tradeDate);
  section('First Blocking Gate (gate_audit_snapshots)');
  if (!gateBlockers.length) console.log('  (no gate_audit rows)');
  for (const g of gateBlockers) {
    console.log(`  ${String(g.n).padStart(4)}  ${(g.gate || 'unknown').padEnd(45)}  avg UES=${g.avg_ues} ML=${g.avg_ml}`);
  }
  const top = gateBlockers[0];
  if (top?.gate?.includes('ml_too_low')) {
    console.log('\n  ℹ️  ml_too_low: default ML threshold ~65% (BULL) — see signal_integration quality_gate');
  }
} catch {
  section('First Blocking Gate');
  console.log('  (gate_audit_snapshots unavailable)');
}

section('Near-Misses (UES≥70, blocked)');
const near = db.prepare(`
  SELECT fs.symbol, fs.score ues, fs.source_ml ml, fs.source_rules scan,
         fs.veto_reason, o.opportunity_score opp, o.stage
  FROM final_signals fs
  LEFT JOIN opportunity_score_v2 o ON o.symbol=fs.symbol AND o.trade_date=fs.trade_date
  WHERE fs.trade_date=? AND COALESCE(fs.actionable,0)=0 AND fs.score>=70
  ORDER BY fs.score DESC LIMIT 15
`).all(tradeDate);
for (const r of near) {
  console.log(`  ${r.symbol.padEnd(6)} UES=${r.ues} ML=${r.ml} scan=${r.scan} opp=${r.opp ?? '-'} ${r.veto_reason ?? ''}`);
}

section('Engine Utilization');
const engines = [
  ['scans (rejected=0)', `SELECT COUNT(DISTINCT symbol) n FROM scans WHERE scan_date='${tradeDate}' AND rejected=0`],
  ['explosion_predictions', `SELECT COUNT(*) n, ROUND(AVG(prob_pct),1) avg FROM explosion_predictions WHERE pred_date=(SELECT MAX(pred_date) FROM explosion_predictions)`],
  ['quant_discovery_rules', `SELECT COUNT(*) n FROM quant_discovery_rules WHERE run_date=(SELECT MAX(run_date) FROM quant_discovery_rules)`],
  ['opportunity_score_v2', `SELECT COUNT(*) n, ROUND(AVG(opportunity_score),1) avg FROM opportunity_score_v2 WHERE trade_date='${tradeDate}'`],
  ['pine_analytics (non-fallback)', `SELECT COUNT(*) n FROM pine_analytics WHERE bar_date='${tradeDate}' AND source NOT LIKE '%fallback%'`],
  ['anti_law_daily_scan veto', `SELECT SUM(anti_law_veto) n FROM anti_law_daily_scan WHERE date='${tradeDate}'`],
  ['arbitration_decisions veto', `SELECT SUM(veto_triggered) n FROM arbitration_decisions WHERE date='${tradeDate}'`],
];
for (const [label, sql] of engines) {
  try {
    const row = db.prepare(sql).get();
    console.log(`  ${label.padEnd(32)} ${JSON.stringify(row)}`);
  } catch {
    console.log(`  ${label.padEnd(32)} (table missing)`);
  }
}

section('Actionable Signals');
const act = db.prepare(`
  SELECT symbol, score, source_ml, source_rules, r_ratio
  FROM final_signals WHERE trade_date=? AND actionable=1
  ORDER BY score DESC
`).all(tradeDate);
if (!act.length) console.log('  (none)');
for (const r of act) {
  console.log(`  ${r.symbol.padEnd(6)} UES=${r.score} ML=${r.source_ml} scan=${r.source_rules} R:R=${r.r_ratio}`);
}

const report = {
  at: new Date().toISOString(),
  trade_date: tradeDate,
  totals,
  vetoes,
  gate_blockers: gateBlockers,
  near_misses: near,
  actionable: act,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/signal_funnel_last.json'), JSON.stringify(report, null, 2));
if (AS_JSON) console.log(JSON.stringify(report, null, 2));

db.close();
