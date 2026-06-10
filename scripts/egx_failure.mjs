#!/usr/bin/env node
/**
 * Phase 23 — Failure Memory Engine runner
 *
 * Sections:
 *   analyze        — analyze all failure_reconstruction rows
 *   classify       — validate classification accuracy
 *   families       — build failure family groups
 *   predictive     — find archetypes that predict explosions
 *   recurrence     — compute recurrence probabilities
 *   scan           — daily failure scan
 *   report         — full summary report
 *   full           — run all
 */
import { pythonFailureAnalyzeAll, pythonFailureClassify, pythonFailureFamilies,
         pythonFailurePredictive, pythonFailureRecurrence,
         pythonFailureDailyScan, pythonFailureReport, pythonFailureBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧠 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'analyze': {
    banner('Failure: Analyzing all failure events…');
    const r = await pythonFailureAnalyzeAll({});
    console.log(`   Processed: ${r.n_processed}  Inserted: ${r.n_inserted}`);
    pp(r); break;
  }
  case 'classify': {
    banner('Failure: Classification validation');
    const r = await pythonFailureClassify({});
    console.log(`   Accuracy: ${(r.accuracy ?? 0).toFixed(1)}%  Samples: ${r.n_samples}`);
    pp(r); break;
  }
  case 'families': {
    banner('Failure: Building failure families');
    const r = await pythonFailureFamilies({});
    pp(r); break;
  }
  case 'predictive': {
    banner('Failure: Finding predictive archetypes');
    const r = await pythonFailurePredictive({});
    pp(r); break;
  }
  case 'recurrence': {
    banner('Failure: Recurrence probabilities');
    const r = await pythonFailureRecurrence({});
    pp(r); break;
  }
  case 'scan': {
    banner('Failure: Daily scan');
    const r = await pythonFailureDailyScan({});
    if (r?.warnings) {
      console.log(`   Warnings: ${r.warnings.length}`);
      r.warnings.slice(0, 10).forEach(w =>
        console.log(`   ⚠️  ${w.symbol?.padEnd(10)} ${w.archetype}  risk: ${w.risk_level}`));
    }
    pp(r); break;
  }
  case 'report': {
    banner('Failure: Summary Report');
    const r = await pythonFailureReport({});
    if (r?.archetype_summary) {
      console.log('\n📊 Archetype Distribution:');
      r.archetype_summary.forEach(a =>
        console.log(`   ${a.failure_archetype?.padEnd(30)} ${a.n} events  sev: ${(a.avg_severity ?? 0).toFixed(2)}`));
    }
    pp(r); break;
  }
  case 'full': {
    banner('Failure: Full pipeline');
    const r = await pythonFailureBuildFull({});
    pp(r); break;
  }
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
