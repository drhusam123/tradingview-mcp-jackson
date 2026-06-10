#!/usr/bin/env node
/**
 * Phase 68 — Hypothesis DSL runner
 * "محرك الفرضيات — يولّد ويقيّم فرضيات السوق تلقائياً"
 *
 * Sections: generate | list | evaluate | full
 *   --hyp-id HYP_XXXXXXXXXX
 *   --mode templates|auto|both
 *   --category explosion|trend|reversal|volume|breakout
 */
import { pythonHypGenerate, pythonHypList, pythonHypAdd,
         pythonHypEvaluate, pythonHypBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'list';
const modeIdx = args.indexOf('--mode');
const mode    = modeIdx !== -1 ? args[modeIdx + 1] : 'templates';
const hidIdx  = args.indexOf('--hyp-id');
const hypId   = hidIdx !== -1 ? args[hidIdx + 1] : null;
const catIdx  = args.indexOf('--category');
const category= catIdx !== -1 ? args[catIdx + 1] : null;
const nIdx    = args.indexOf('--n');
const nAuto   = nIdx !== -1 ? parseInt(args[nIdx + 1]) : 50;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💡 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const CAT_EMOJI = {
  explosion: '💥', trend: '📈', reversal: '🔄', volume: '📊',
  breakout: '🚀', accumulation: '🔋', pullback: '↩️', auto: '🤖', manual: '✍️',
};

function gradeEmoji(g) {
  return g === 'S' ? '🏆' : g === 'A' ? '⭐' : g === 'B' ? '✅' : g === 'C' ? '⚠️' : '❌';
}

switch (section) {
  case 'generate': {
    banner(`Generate Hypotheses — mode=${mode}`);
    const r = await pythonHypGenerate({ mode, n_auto: nAuto });
    if (r?.total_hypotheses !== undefined) {
      console.log(`\n   New inserted:      ${r.n_inserted ?? 0}`);
      console.log(`   Total in system:   ${r.total_hypotheses}`);
      if (r.sample?.length) {
        console.log(`\n   Generated hypotheses:\n`);
        r.sample.forEach((h, i) => {
          const em = CAT_EMOJI[h.source ?? 'auto'] ?? '💡';
          console.log(`   ${em} ${h.hyp_id}  ${h.name}`);
        });
      }
      console.log(`\n   Next: npm run egx:grid:run  to backtest all hypotheses`);
    } else pp(r);
    break;
  }
  case 'list': {
    banner(`Hypothesis Library${category ? ` — ${category}` : ''}`);
    const r = await pythonHypList(category ? { category, limit: 30 } : { limit: 30 });
    const hyps = r?.hypotheses ?? [];
    if (hyps.length) {
      console.log(`\n   ${hyps.length} hypothesis/hypotheses:\n`);
      console.log('   Hyp ID                  Name                        Cat       Dir   Hold');
      console.log('   ' + '─'.repeat(72));
      hyps.forEach(h => {
        const em = CAT_EMOJI[h.category ?? 'auto'] ?? '💡';
        console.log(`   ${em} ${String(h.hyp_id).padEnd(24)} ${String(h.hyp_name ?? '?').padEnd(28)} ${String(h.category ?? '?').padEnd(10)} ${String(h.direction ?? 'LONG').padEnd(5)} ${h.holding_days}d`);
      });
      if (!hyps.length) {
        console.log('\n   No hypotheses yet. Run: npm run egx:hyp:generate');
      }
    } else {
      console.log('\n   No hypotheses found. Run: npm run egx:hyp:generate');
    }
    break;
  }
  case 'evaluate': {
    if (!hypId) { console.log('Error: --hyp-id required for evaluate section'); process.exit(1); }
    banner(`Evaluate Hypothesis — ${hypId}`);
    const r = await pythonHypEvaluate({ hyp_id: hypId });
    if (r?.n_activations !== undefined) {
      if (r.n_activations === 0) {
        console.log(`\n   ⚠️  No activations found — conditions may be too strict`);
        break;
      }
      const exp = r.expectancy_pct ?? 0;
      const oos = r.oos_score;
      const em  = exp >= 1.0 ? '🏆' : exp >= 0.5 ? '⭐' : exp >= 0 ? '✅' : '❌';
      console.log(`\n   ${em} ${r.hyp_id}`);
      if (r.description) console.log(`\n${r.description.split('\n').map(l => '   '+l).join('\n')}`);
      console.log(`\n   Results:`);
      console.log(`   Activations:   ${r.n_activations}`);
      console.log(`   Win rate:      ${r.win_rate_pct?.toFixed(1)}%`);
      console.log(`   Avg net return: ${r.avg_net_return_pct?.toFixed(3)}% (after 150bps costs)`);
      console.log(`   Avg win:       +${r.avg_win_pct?.toFixed(3)}%`);
      console.log(`   Avg loss:       ${r.avg_loss_pct?.toFixed(3)}%`);
      console.log(`   Expectancy:    ${exp?.toFixed(3)}%`);
      console.log(`\n   Walk-forward validation:`);
      console.log(`   IS  (pre-2024): ${r.is_precision != null ? (r.is_precision*100)?.toFixed(1)+'%' : 'n/a'}  (${r.is_samples ?? 0} samples)`);
      console.log(`   OOS (2024+):    ${r.oos_precision != null ? (r.oos_precision*100)?.toFixed(1)+'%' : 'n/a'}  (${r.oos_samples ?? 0} samples)`);
      if (oos != null) {
        const oosEm = oos >= 0.8 ? '✅' : oos >= 0.5 ? '⚠️' : '❌';
        console.log(`   OOS Score:      ${oosEm} ${oos?.toFixed(3)} (1.0 = perfect, >1 = OOS beats IS)`);
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Hypothesis DSL Full');
    const r = await pythonHypBuildFull({});
    if (r?.total_hypotheses !== undefined) {
      console.log(`\n   Hypotheses generated: ${r.hypotheses_generated ?? 0}`);
      console.log(`   Total in system:      ${r.total_hypotheses}`);
      if (r.sample?.length) {
        console.log('\n   Ready to test:');
        r.sample.slice(0, 6).forEach(h =>
          console.log(`     💡 ${h.hyp_name ?? h.hyp_id}`));
      }
      console.log(`\n   ${r.next ?? 'Run egx:grid:run to backtest'}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: generate|list|evaluate|full`); process.exit(1);
}
