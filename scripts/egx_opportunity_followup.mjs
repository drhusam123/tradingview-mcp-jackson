#!/usr/bin/env node
/**
 * Opportunity followup — trend alerts from opportunity_quality_history.
 * Usage: node scripts/egx_opportunity_followup.mjs [--json]
 */
import { analyzeOpportunityTrend } from './lib/opportunity_followup.mjs';

const AS_JSON = process.argv.includes('--json');
const report = analyzeOpportunityTrend();

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

console.log('\n═══ EGX Opportunity Followup ═══');
console.log(`  Sessions tracked: ${report.n_sessions}`);
console.log(`  Quality trend:    ${report.trends.quality.prior ?? '—'} → ${report.trends.quality.recent ?? '—'} (Δ${report.trends.quality.delta})`);
console.log(`  Missed trend:     Δ${report.trends.missed.delta}`);
console.log(`  Blocked trend:    Δ${report.trends.blocked.delta}`);
console.log(`  Delivered trend:  Δ${report.trends.delivered.delta}`);

if (report.alerts.length) {
  console.log('\n  Alerts:');
  for (const a of report.alerts) {
    console.log(`    [${a.severity}] ${a.code}: ${a.message}`);
  }
} else {
  console.log('\n  No trend alerts.');
}

console.log('\n  Saved: data/opportunity_followup_last.json\n');
