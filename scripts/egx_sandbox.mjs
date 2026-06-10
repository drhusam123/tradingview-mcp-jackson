#!/usr/bin/env node
/**
 * Phase 40 — Autonomous Research Sandbox runner
 * "الذكاء الاصطناعي يولّد قوانين جديدة بنفسه"
 *
 * Sections:
 *   generate    — generate candidate hypotheses from 5 sources
 *   backtest    — backtest one hypothesis  --id HYP_...
 *   cycle       — full autonomous cycle: generate → backtest → promote (default)
 *   report      — sandbox activity report
 *   full        — cycle + save to DB (recommended)
 */
import { pythonSandboxGenerate, pythonSandboxBacktest, pythonSandboxRunCycle,
         pythonSandboxReport, pythonSandboxBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'cycle';
const hypId   = args[args.indexOf('--id') + 1] ?? null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧪 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const SOURCE_EMOJI = {
  meta_directive:     '🧠',
  anti_law_inversion: '🔄',
  anomaly_pattern:    '🔮',
  episodic_memory:    '💭',
  law_mutation:       '🧬',
};

switch (section) {
  case 'generate': {
    banner('Sandbox: Generating Hypotheses');
    const r = await pythonSandboxGenerate({});
    if (r?.n_generated !== undefined) {
      console.log(`\n   Generated: ${r.n_generated} hypotheses\n`);
      console.log('   By source:');
      Object.entries(r.by_source ?? {}).forEach(([src, n]) =>
        console.log(`   ${SOURCE_EMOJI[src] ?? '•'} ${String(src).padEnd(25)} ${n} hypotheses`));
      if (r.hypotheses?.length) {
        console.log('\n   Sample hypotheses:');
        r.hypotheses.slice(0, 5).forEach(h =>
          console.log(`   ${SOURCE_EMOJI[h.source] ?? '•'} [${h.source}] ${h.hypothesis_text}`));
      }
    } else pp(r);
    break;
  }
  case 'backtest': {
    if (!hypId) { console.log('Usage: node egx_sandbox.mjs backtest --id HYP_...'); process.exit(1); }
    banner(`Sandbox: Backtesting ${hypId}`);
    const r = await pythonSandboxBacktest({ hypothesis_id: hypId });
    if (r?.n_samples !== undefined) {
      const em = r.promoted ? '✅ PROMOTED' : '❌ REJECTED';
      console.log(`\n   ${em}`);
      console.log(`   n_samples: ${r.n_samples}  precision: ${(r.precision*100)?.toFixed(1)}%`);
      console.log(`   p-value: ${r.p_value?.toFixed(4)}  EAE: ${r.eae?.toFixed(4)}`);
      console.log(`   Reason: ${r.reason}`);
    } else pp(r);
    break;
  }
  case 'cycle': {
    banner('Sandbox: Autonomous Research Cycle');
    const r = await pythonSandboxRunCycle({});
    if (r?.cycle_id !== undefined) {
      const rate = (r.promotion_rate * 100)?.toFixed(1);
      console.log(`\n   Cycle ID: ${r.cycle_id}`);
      console.log(`   Generated:  ${r.n_generated}`);
      console.log(`   Tested:     ${r.n_tested}`);
      console.log(`   ✅ Promoted: ${r.n_promoted}  (${rate}% rate)`);
      console.log(`   ❌ Rejected: ${r.n_rejected}`);
      if (r.promoted_laws?.length) {
        console.log('\n   Newly Discovered Laws:');
        r.promoted_laws.forEach(l =>
          console.log(`   🧬 ${String(l.law_name).padEnd(35)} prec:${(l.precision*100).toFixed(0)}%  EAE:${l.eae?.toFixed(4)}`));
      }
      console.log(`\n   ${r.cycle_summary}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Sandbox: Activity Report');
    const r = await pythonSandboxReport({});
    if (r?.total_hypotheses !== undefined) {
      const rate = (r.promotion_rate * 100)?.toFixed(1);
      console.log(`\n   Total hypotheses: ${r.total_hypotheses}`);
      console.log(`   ✅ Promoted: ${r.n_promoted}  (${rate}%)`);
      console.log(`   ❌ Rejected: ${r.n_rejected}`);
      console.log(`   Cycles run:  ${r.total_cycles}`);
      console.log(`   Best source: ${r.best_source}`);
      console.log(`   Health: ${r.sandbox_health}`);
      if (r.best_laws?.length) {
        console.log('\n   Best Discovered Laws:');
        r.best_laws.forEach(l =>
          console.log(`   🧬 ${String(l.law_name ?? l.hypothesis_id).padEnd(35)} prec:${(l.precision*100).toFixed(0)}%`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Sandbox: Full Build');
    const r = await pythonSandboxBuildFull({});
    if (r?.cycle_id !== undefined) {
      console.log(`\n   Cycle: ${r.cycle_id}`);
      console.log(`   ✅ Promoted: ${r.n_promoted}  (${(r.promotion_rate*100)?.toFixed(1)}%)`);
      console.log(`   Top discovery: ${r.top_discovery ?? 'None'}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
