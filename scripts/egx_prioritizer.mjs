#!/usr/bin/env node
/**
 * Phase 29 — Intelligence Prioritization Layer runner
 *
 * Sections:
 *   run        — compute intelligence scores for all symbols
 *   insights   — top 3 actionable insights today
 *   anomaly    — detect today's abnormal changes
 *   score      — deep score for one symbol  --ticker COMI
 *   brief      — full daily executive brief (THE OUTPUT)
 *   full       — run + insights + anomaly sequentially
 */
import { pythonPrioritizerRun, pythonPrioritizerTopInsights, pythonPrioritizerAnomaly,
         pythonPrioritizerScoreSymbol, pythonPrioritizerDailyBrief,
         pythonPrioritizerBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'brief';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧠 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'run': {
    banner('Intelligence: Scoring all symbols…');
    const r = await pythonPrioritizerRun({});
    if (r?.top_10) {
      console.log(`\n   Scored: ${r.n_scored}  Avg score: ${r.avg_score?.toFixed(1)}`);
      console.log('\n🏆 Top 10 Intelligence Scores:');
      r.top_10.forEach(s =>
        console.log(`   ${String(s.symbol).padEnd(10)} score: ${String(s.intelligence_score?.toFixed(1)).padEnd(6)} driver: ${s.primary_driver}  tier: ${s.tier}`));
    } else pp(r);
    break;
  }
  case 'insights': {
    banner('Intelligence: Top 3 Insights Today');
    const r = await pythonPrioritizerTopInsights({});
    if (r?.insights) {
      console.log(`\n   Dominant Force: ${r.dominant_force}`);
      console.log(`   Risk Level:     ${r.risk_level}\n`);
      r.insights.forEach(i =>
        console.log(`   [${i.rank}] ${i.symbol?.padEnd(8)} ${i.signal_type?.padEnd(20)} ${i.insight_text}`));
    } else pp(r);
    break;
  }
  case 'anomaly': {
    banner('Intelligence: Anomaly Detection');
    const r = await pythonPrioritizerAnomaly({});
    if (r?.anomalies) {
      console.log(`\n   Anomalies found: ${r.n_anomalies}  Most severe: ${r.most_severe || 'none'}`);
      r.anomalies.filter(a => a.severity !== 'LOW').forEach(a =>
        console.log(`   ⚠️  ${String(a.symbol).padEnd(10)} [${a.severity}] ${a.anomaly_type}: ${a.description}`));
    } else pp(r);
    break;
  }
  case 'score': {
    banner(`Intelligence: Deep Score for ${ticker}`);
    const r = await pythonPrioritizerScoreSymbol({ symbol: ticker });
    if (r?.intelligence_score !== undefined) {
      console.log(`\n   ${ticker}: ${r.intelligence_score?.toFixed(1)} / 100  (${r.percentile_rank?.toFixed(0)}th percentile)`);
      console.log(`   Quality: ${r.data_quality}  Action: ${r.action_hint}`);
      if (r.components) {
        console.log('\n   Components:');
        Object.entries(r.components).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(25)} ${(v ?? 0).toFixed(1)}`));
      }
    } else pp(r);
    break;
  }
  case 'brief': {
    banner('Intelligence: Daily Executive Brief');
    const r = await pythonPrioritizerDailyBrief({});
    if (r?.market_state) {
      console.log(`\n   📅 ${r.date}`);
      console.log(`   Market State:    ${r.market_state}`);
      console.log(`   Risk Level:      ${r.risk_level}`);
      console.log(`   Dominant Force:  ${r.dominant_force}`);
      console.log(`   Regime:          ${r.regime_stability}`);
      console.log(`   Actionable:      ${r.actionable_today ? '✅ YES' : '⛔ NO'}`);
      console.log(`\n   📝 ${r.brief_summary}`);
      if (r.top_3_insights?.length) {
        console.log('\n   🔍 Top Insights:');
        r.top_3_insights.forEach(i =>
          console.log(`      [${i.rank}] ${i.symbol?.padEnd(8)} ${i.insight_text}`));
      }
      if (r.top_5_symbols?.length) {
        console.log('\n   🏆 Top Symbols:');
        r.top_5_symbols.forEach(s =>
          console.log(`      ${String(s.symbol).padEnd(10)} ${s.score?.toFixed(1)}  ${s.reason}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Intelligence: Full Build (run + insights + anomaly)');
    const r = await pythonPrioritizerBuildFull({});
    pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
