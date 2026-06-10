#!/usr/bin/env node
/**
 * Phase 31 — Meta-Learning Engine runner
 *
 * Sections:
 *   hypotheses   — analyze which pattern types succeed
 *   failures     — when/why does the system fail?
 *   map          — predictability map: sector × regime
 *   directives   — actionable meta-directives for research
 *   full         — run all 4 in sequence
 */
import { pythonMetaAnalyzeHypotheses, pythonMetaFailureContexts,
         pythonMetaPredictabilityMap, pythonMetaDirectives,
         pythonMetaBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'directives';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔮 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'hypotheses': {
    banner('Meta-Learning: Hypothesis Success Analysis');
    const r = await pythonMetaAnalyzeHypotheses({});
    if (r?.best_feature_types) {
      console.log('\n✅ Best Feature Types:');
      r.best_feature_types.slice(0, 5).forEach(f =>
        console.log(`   ${String(f.type).padEnd(25)} prec: ${f.avg_precision?.toFixed(3)}  laws: ${f.n_laws}  active: ${(f.pct_active * 100)?.toFixed(0)}%`));
      console.log('\n🏦 Best Sectors:');
      r.best_sectors?.slice(0, 5).forEach(s =>
        console.log(`   ${String(s.sector).padEnd(25)} prec: ${s.avg_precision?.toFixed(3)}  laws: ${s.n_laws}`));
      console.log(`\n💡 ${r.meta_insight}`);
    } else pp(r);
    break;
  }
  case 'failures': {
    banner('Meta-Learning: Failure Context Analysis');
    const r = await pythonMetaFailureContexts({});
    if (r?.dangerous_contexts) {
      console.log('\n⚠️  Dangerous Contexts:');
      r.dangerous_contexts?.slice(0, 5).forEach(c =>
        console.log(`   ${c.combo || `${c.sector} × ${c.regime}`}  failure_rate: ${(c.failure_rate * 100)?.toFixed(0)}%`));
      console.log('\n✅ Safe Contexts:');
      r.safest_contexts?.slice(0, 5).forEach(c =>
        console.log(`   ${c.combo || `${c.sector} × ${c.regime}`}  failure_rate: ${(c.failure_rate * 100)?.toFixed(0)}%`));
    } else pp(r);
    break;
  }
  case 'map': {
    banner('Meta-Learning: Predictability Map');
    const r = await pythonMetaPredictabilityMap({});
    if (r?.predictability_map) {
      console.log('\n📊 Predictability Map (sector × regime):');
      Object.entries(r.predictability_map).slice(0, 8).forEach(([sector, regimes]) => {
        const scores = Object.entries(regimes).map(([reg, score]) => `${reg}:${(score * 100).toFixed(0)}%`).join('  ');
        console.log(`   ${String(sector).padEnd(20)} ${scores}`);
      });
      console.log('\n🎯 Best Opportunities:');
      r.best_opportunities?.slice(0, 5).forEach(o =>
        console.log(`   ${o.sector?.padEnd(20)} ${o.regime?.padEnd(15)} opp: ${o.opportunity_score?.toFixed(2)}`));
    } else pp(r);
    break;
  }
  case 'directives': {
    banner('Meta-Learning: Research Directives');
    const r = await pythonMetaDirectives({});
    if (r?.directives) {
      console.log(`\n📌 Meta Directives (${r.directives.length}):`);
      r.directives.slice(0, 8).forEach(d =>
        console.log(`   [${d.priority}] ${d.type?.padEnd(20)} ${d.instruction}`));
      console.log('\n💰 Research Budget:');
      Object.entries(r.research_budget_allocation || {}).forEach(([k, v]) =>
        console.log(`   ${k.padEnd(20)} ${(v * 100).toFixed(0)}%`));
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Meta-Learning: Full Build');
    const r = await pythonMetaBuildFull({});
    if (r?.meta_summary) console.log(`\n💡 Meta Summary: ${r.meta_summary}`);
    pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
