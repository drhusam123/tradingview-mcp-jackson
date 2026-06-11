#!/usr/bin/env node
/**
 * Phase 26 — Adaptive Research Loop runner
 *
 * Sections:
 *   assess         — assess current law health
 *   discover       — discover new law candidates
 *   mutate         — mutate weak laws
 *   directives     — generate research directives
 *   evolve         — run full evolution cycle
 *   tree           — show law lineage tree
 *   full           — alias for evolve
 */
import { pythonResearchAssessLaws, pythonResearchDiscover, pythonResearchMutate,
         pythonResearchDirectives, pythonResearchEvolution, pythonResearchLawTree } from '../src/egx/index.js';
import { loadP6ResearchContext } from './lib/p6_research_context.mjs';

const p6Context = loadP6ResearchContext();
const researchParams = p6Context ? { p6_context: p6Context } : {};

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'assess';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'assess': {
    banner('Research: Law Health Assessment');
    const r = await pythonResearchAssessLaws(researchParams);
    if (r?.law_health) {
      console.log(`\n⚖️  Law Health:`);
      r.law_health.forEach(l =>
        console.log(`   ${String(l.pattern_name ?? l.pattern_id).padEnd(25)} status: ${l.law_status?.padEnd(12)} prec: ${(l.precision ?? 0).toFixed(3)}`));
    }
    if (r?.all_degrading) console.log(`\n🚨 ALL LAWS DEGRADING — ${r.action_required}`);
    pp(r); break;
  }
  case 'discover': {
    banner('Research: Discovering new laws…');
    const r = await pythonResearchDiscover(researchParams);
    console.log(`   Tested: ${r.n_tested}  Promoted: ${r.n_promoted}`);
    if (r?.new_laws) r.new_laws.slice(0, 5).forEach(l =>
      console.log(`   ✅ ${l.feature} prec: ${(l.precision ?? 0).toFixed(3)}`));
    pp(r); break;
  }
  case 'mutate': {
    banner('Research: Mutating weak laws…');
    const r = await pythonResearchMutate(researchParams);
    console.log(`   Tested: ${r.n_mutations_tested}  Improvements: ${r.n_improvements}`);
    pp(r); break;
  }
  case 'directives': {
    banner('Research: Generating directives…');
    const r = await pythonResearchDirectives(researchParams);
    if (r?.priority_list) {
      console.log('\n📌 Research Directives:');
      r.priority_list.forEach(d =>
        console.log(`   [${d.priority}] ${d.directive_type?.padEnd(25)} → ${d.target}`));
    }
    pp(r); break;
  }
  case 'evolve':
  case 'full': {
    banner('Research: Full Evolution Cycle');
    const r = await pythonResearchEvolution(researchParams);
    if (r?.summary) {
      console.log(`\n✅ Evolution complete:`);
      console.log(`   Best precision: ${r.summary.best_precision_after}`);
      console.log(`   Avg precision:  ${r.summary.avg_precision_after}`);
      console.log(`   New laws:       ${r.summary.n_new_laws}`);
      console.log(`   Mutations:      ${r.summary.n_mutations}`);
    }
    pp(r); break;
  }
  case 'tree': {
    banner('Research: Law Lineage Tree');
    const r = await pythonResearchLawTree(researchParams);
    pp(r); break;
  }
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
