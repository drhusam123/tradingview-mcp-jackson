#!/usr/bin/env node
/**
 * Phase 74 — Walk-Forward Lab + Monte Carlo runner
 * "التحقق الصارم — walk-forward + Monte Carlo robustness"
 *
 * Sections: signals | laws | mc | stability | report
 *   --feature pre1_rsi   --lo 20 --hi 55
 *   --n-sims 1000
 */
import { pythonWFSignals, pythonWFLaws, pythonWFMonteCarlo,
         pythonWFParamStability, pythonWFReport }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const featIdx = args.indexOf('--feature');
const feature = featIdx !== -1 ? args[featIdx+1] : 'pre1_rsi';
const loIdx   = args.indexOf('--lo');
const lo      = loIdx !== -1 ? parseFloat(args[loIdx+1]) : 20;
const hiIdx   = args.indexOf('--hi');
const hi      = hiIdx !== -1 ? parseFloat(args[hiIdx+1]) : 55;
const nsIdx   = args.indexOf('--n-sims');
const nSims   = nsIdx !== -1 ? parseInt(args[nsIdx+1]) : 500;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'signals': {
    banner('Walk-Forward Validation — Explosion Signals');
    const r = await pythonWFSignals({});
    if (r?.n_windows) {
      const em = r.verdict?.includes('Robust') ? '✅' : r.verdict?.includes('Marginal') ? '⚠️' : '❌';
      console.log(`\n   ${em} ${r.verdict}`);
      console.log(`   Windows:     ${r.n_windows}`);
      console.log(`   Avg Win%:    ${r.avg_win_rate}%`);
      console.log(`   Avg Return:  ${r.avg_return_pct}%`);
      console.log(`   Stability:   ${r.stability_score}% of windows profitable\n`);
      console.log('   Window          Trades  Win%   Avg Ret%');
      console.log('   ' + '─'.repeat(50));
      (r.windows || []).forEach(w => {
        const em2 = w.avg_return > 0 ? '🟢' : '🔴';
        console.log(`   ${em2} ${String(w.window).padEnd(22)} ${String(w.n_trades).padStart(4)}  ${String(w.win_rate+'%').padStart(6)}  ${String(w.avg_return+'%').padStart(7)}`);
      });
    } else pp(r);
    break;
  }
  case 'laws': {
    banner('Walk-Forward — Law Precision Stability');
    const r = await pythonWFLaws({});
    if (r?.n_laws) {
      console.log(`\n   ${r.verdict}\n`);
      console.log('   Law                              Overall%  StdDev  CV     Stability');
      console.log('   ' + '─'.repeat(72));
      (r.results || []).forEach(l => {
        const em = l.stability === 'STABLE' ? '✅' : l.stability === 'VARIABLE' ? '⚠️' : '❌';
        console.log(`   ${em} ${String(l.law_name || l.law_id).substring(0,30).padEnd(30)} ${String(l.overall_precision+'%').padStart(8)}  ${String(l.std_precision).padStart(6)}  ${String(l.cv).padStart(5)}  ${l.stability}`);
      });
    } else pp(r);
    break;
  }
  case 'mc': {
    banner(`Monte Carlo — ${nSims} simulations`);
    const r = await pythonWFMonteCarlo({ n_sims: nSims });
    if (r?.success) {
      const em = r.verdict?.includes('ROBUST') ? '✅' : r.verdict?.includes('MARGINAL') ? '⚠️' : '❌';
      console.log(`\n   ${em} ${r.verdict}`);
      console.log(`   Trades:      ${r.n_trades}  |  Win Rate: ${r.actual_win_rate}%`);
      console.log(`   Avg Return:  ${r.actual_avg_ret}%`);
      console.log(`   Prob Ruin:   ${r.prob_ruin}%`);
      console.log(`\n   CAGR (p5/p50/p95): ${r.cagr?.p5}% / ${r.cagr?.p50}% / ${r.cagr?.p95}%`);
      console.log(`   MaxDD (p50/p95):   ${r.max_drawdown?.p50}% / ${r.max_drawdown?.p95}%`);
      console.log(`   Sharpe (p50):      ${r.sharpe?.p50}`);
    } else pp(r);
    break;
  }
  case 'stability': {
    banner(`Parameter Stability Map — ${feature}`);
    const r = await pythonWFParamStability({ feature, lo, hi, steps: 15 });
    if (r?.success) {
      const em = r.verdict?.includes('STABLE') ? '✅' : r.verdict?.includes('MARGINAL') ? '⚠️' : '❌';
      console.log(`\n   ${em} ${r.verdict}`);
      console.log(`   Feature: ${feature} | Range: ${lo}→${hi}`);
      console.log(`   Island width: ${r.island_width}% | Best: ${feature} ${r.best_threshold} → ${r.peak_precision}%\n`);
      console.log('   Threshold  Precision    Bar');
      console.log('   ' + '─'.repeat(45));
      (r.grid || []).forEach(g => {
        const bar = '█'.repeat(Math.round(g.precision / 5));
        const em2 = g.precision > 50 ? '🟢' : g.precision > 30 ? '🟡' : '🔴';
        console.log(`   ${em2} ${String(g.threshold).padStart(7)}  ${String(g.precision+'%').padStart(7)}   ${bar}`);
      });
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Walk-Forward Full Report');
    const r = await pythonWFReport({});
    const wf = r?.walk_forward ?? {};
    const mc = r?.monte_carlo ?? {};
    if (wf.n_windows) {
      console.log(`\n   📊 Walk-Forward: ${wf.verdict}  (${wf.n_windows} windows, stability=${wf.stability_score}%)`);
      console.log(`   📊 Monte Carlo:  ${mc.verdict}  (ruin=${mc.prob_ruin}% CAGR_p50=${mc.cagr?.p50}%)`);
      const ps = r?.param_stability ?? {};
      if (ps.rsi) console.log(`   📊 RSI Stability: ${ps.rsi.verdict}  (island=${ps.rsi.island_width}%)`);
      if (ps.bb_width) console.log(`   📊 BB Width Stability: ${ps.bb_width.verdict}  (island=${ps.bb_width.island_width}%)`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown: ${section}. Use: signals|laws|mc|stability|report`); process.exit(1);
}
