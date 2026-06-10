#!/usr/bin/env node
/**
 * Phase 66 — Realistic EGX Backtest runner
 * "اختبار واقعي بتكاليف السوق الحقيقية — عمولة + سبريد + تأثير السوق"
 *
 * Sections: symbol | universe | oos | compare | hurdle | full
 *   --symbol COMI
 *   --law-id <id>
 *   --law-type universal|structural
 */
import { pythonBTSymbol, pythonBTUniverse, pythonBTOOS,
         pythonBTCompareLaws, pythonBTCostHurdle, pythonBTBuildFull }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'hurdle';
const symIdx   = args.indexOf('--symbol');
const symbol   = symIdx !== -1 ? args[symIdx + 1] : null;
const ltIdx    = args.indexOf('--law-type');
const lawType  = ltIdx !== -1 ? args[ltIdx + 1] : 'universal';
const liIdx    = args.indexOf('--law-id');
const lawId    = liIdx !== -1 ? args[liIdx + 1] : null;
const nIdx     = args.indexOf('--top-n');
const topN     = nIdx !== -1 ? parseInt(args[nIdx + 1]) : 20;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💰 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { A: '🏆', B: '🥇', C: '🥈', D: '🥉', F: '❌', '?': '❓' };

function alphaBar(net_alpha) {
  if (net_alpha == null) return 'n/a';
  const pct = Math.max(-10, Math.min(10, net_alpha));
  const positive = pct >= 0;
  const bars = Math.round(Math.abs(pct) / 10 * 15);
  return positive
    ? '▓'.repeat(bars) + ' '.repeat(15 - bars) + ' +' + net_alpha?.toFixed(2) + '%'
    : ' '.repeat(15 - bars) + '▓'.repeat(bars) + ' -' + Math.abs(net_alpha)?.toFixed(2) + '%';
}

switch (section) {
  case 'symbol': {
    if (!symbol) { console.log('Error: --symbol required'); process.exit(1); }
    if (!lawId)  { console.log('Error: --law-id required'); process.exit(1); }
    banner(`Backtest — ${symbol} using ${lawId}`);
    const r = await pythonBTSymbol({ symbol, law_id: lawId, law_type: lawType });
    if (r?.net_alpha !== undefined || r?.gross_win_rate !== undefined) {
      const alpha = r.net_alpha ?? 0;
      const em = alpha >= 2 ? '🏆' : alpha >= 0 ? '✅' : '❌';
      console.log(`\n   ${em} ${symbol}  |  Law: ${lawId}`);
      console.log(`\n   Gross win rate:  ${(r.gross_win_rate*100)?.toFixed(1)}%`);
      console.log(`   Net alpha:       ${alpha?.toFixed(2)}% per trade`);
      console.log(`   Total trades:    ${r.n_trades ?? '?'}`);
      console.log(`\n   Cost breakdown:`);
      const c = r.cost_breakdown ?? r.costs ?? {};
      console.log(`     Commission:    ${c.commission_bps?.toFixed(1) ?? r.commission_bps?.toFixed(1) ?? '?'} bps`);
      console.log(`     Spread:        ${c.spread_bps?.toFixed(1)     ?? r.spread_bps?.toFixed(1)     ?? '?'} bps`);
      console.log(`     Market impact: ${c.impact_bps?.toFixed(1)     ?? r.impact_bps?.toFixed(1)     ?? '?'} bps`);
      console.log(`     Total cost:    ${c.total_bps?.toFixed(1)      ?? r.total_cost_bps?.toFixed(1) ?? '?'} bps`);
      if (r.grade) console.log(`\n   Grade: ${GRADE_EMOJI[r.grade] ?? ''} ${r.grade}`);
    } else pp(r);
    break;
  }
  case 'universe': {
    banner(`Universe Backtest — ${lawType} laws`);
    console.log('\n   Running backtest across universe (may take 60s)...');
    const r = await pythonBTUniverse({ law_type: lawType, top_n: topN });
    const results = r?.results ?? [];
    if (results.length) {
      console.log(`\n   ${results.length} law(s) tested:\n`);
      console.log('   Law ID                  Trades  WinRate  NetAlpha  Grade');
      console.log('   ' + '─'.repeat(62));
      results.slice(0, 20).forEach(res => {
        const g = GRADE_EMOJI[res.grade ?? '?'] ?? '?';
        const alpha = res.net_alpha ?? res.avg_net_alpha ?? 0;
        const alphaEm = alpha >= 0 ? '+' : '';
        console.log(`   ${g} ${String(res.law_id).padEnd(24)} ${String(res.n_trades ?? '?').padStart(6)} ${String((res.gross_win_rate*100)?.toFixed(1)+'%').padStart(7)}  ${alphaEm}${alpha?.toFixed(2)}%`);
      });
      const positive = results.filter(r => (r.net_alpha ?? r.avg_net_alpha ?? 0) > 0);
      console.log(`\n   Positive net alpha: ${positive.length}/${results.length} laws (${(positive.length/results.length*100)?.toFixed(0)}%)`);
    } else pp(r);
    break;
  }
  case 'oos': {
    banner(`OOS Validation — Walk-Forward`);
    const r = await pythonBTOOS({ law_type: lawType });
    if (r?.oos_results !== undefined || r?.avg_oos_alpha !== undefined) {
      const oos = r.oos_results ?? [];
      console.log(`\n   Walk-forward OOS validation (train → 2024, OOS → 2024-2026):`);
      console.log(`\n   Laws tested:     ${r.n_laws_tested ?? oos.length}`);
      console.log(`   Avg OOS alpha:   ${r.avg_oos_alpha?.toFixed(2) ?? 'n/a'}%`);
      console.log(`   Avg IS alpha:    ${r.avg_is_alpha?.toFixed(2)  ?? 'n/a'}%`);
      if (r.overfitting_ratio != null)
        console.log(`   Overfitting:     ${(r.overfitting_ratio * 100)?.toFixed(0)}% alpha degradation`);
      if (oos.length) {
        console.log(`\n   Symbol-level OOS results:`);
        console.log('   Law ID                  IS Alpha  OOS Alpha  Degraded?');
        console.log('   ' + '─'.repeat(58));
        oos.slice(0, 15).forEach(o => {
          const is_a   = o.is_alpha ?? 0;
          const oos_a  = o.oos_alpha ?? 0;
          const deg    = oos_a < is_a * 0.5;
          const em     = deg ? '⚠️' : oos_a >= 0 ? '✅' : '❌';
          console.log(`   ${em} ${String(o.law_id).padEnd(24)} ${String(is_a?.toFixed(2)+'%').padStart(8)}  ${String(oos_a?.toFixed(2)+'%').padStart(9)}  ${deg ? 'YES' : 'no'}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'compare': {
    banner(`Compare Laws — ${lawType}`);
    const r = await pythonBTCompareLaws({ law_type: lawType });
    const cmp = r?.comparison ?? r?.results ?? [];
    if (cmp.length) {
      console.log(`\n   ${cmp.length} laws ranked by net alpha:\n`);
      console.log('   Rank  Law                      NetAlpha  WinRate  Cost_bps  Grade');
      console.log('   ' + '─'.repeat(68));
      cmp.slice(0, 20).forEach((l, i) => {
        const g     = GRADE_EMOJI[l.grade ?? '?'] ?? '?';
        const alpha = l.net_alpha ?? l.avg_net_alpha ?? 0;
        const em    = alpha >= 2 ? '🏆' : alpha >= 0 ? '✅' : '❌';
        console.log(`   ${String(i+1).padStart(4)}. ${em} ${String(l.law_id ?? l.law_name).padEnd(24)} ${String(alpha?.toFixed(2)+'%').padStart(8)} ${String(((l.gross_win_rate??0)*100)?.toFixed(1)+'%').padStart(7)} ${String(l.total_cost_bps?.toFixed(0) ?? '?').padStart(8)}   ${g} ${l.grade ?? '?'}`);
      });
    } else pp(r);
    break;
  }
  case 'hurdle': {
    banner(`Cost Hurdle Analysis — ${lawType}`);
    const r = await pythonBTCostHurdle({ law_type: lawType });
    if (r?.hurdle_rates !== undefined || r?.n_above_hurdle !== undefined) {
      console.log(`\n   EGX Transaction Cost Hurdle:\n`);
      console.log(`   Avg commission: ${r.avg_commission_bps?.toFixed(0) ?? 30} bps`);
      console.log(`   Avg spread:     ${r.avg_spread_bps?.toFixed(0)    ?? '?'} bps`);
      console.log(`   Total hurdle:   ${r.total_hurdle_bps?.toFixed(0)  ?? '?'} bps per round-trip`);
      console.log(`\n   Laws above hurdle: ${r.n_above_hurdle ?? 0}/${r.n_total ?? '?'}`);
      console.log(`   Laws below hurdle: ${r.n_below_hurdle ?? 0}`);
      if (r.hurdle_rates) {
        console.log('\n   Hurdle rates by liquidity tier:');
        Object.entries(r.hurdle_rates).forEach(([tier, bps]) =>
          console.log(`     ${String(tier).padEnd(12)} ${bps?.toFixed(0)} bps`));
      }
      if (r.above_hurdle?.length) {
        console.log(`\n   ✅ Laws that pass the hurdle:`);
        r.above_hurdle.slice(0, 10).forEach(l =>
          console.log(`     🏆 ${l.law_id}  net_alpha=+${l.net_alpha?.toFixed(2)}%`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Realistic Backtest Full — ${lawType}`);
    const r = await pythonBTBuildFull({ law_type: lawType });
    if (r?.n_tested !== undefined || r?.top_laws !== undefined) {
      console.log(`\n   Laws tested:       ${r.n_tested ?? '?'}`);
      console.log(`   Positive alpha:    ${r.n_positive_alpha ?? 0}`);
      console.log(`   Avg net alpha:     ${r.avg_net_alpha?.toFixed(2) ?? 'n/a'}%`);
      console.log(`   Total cost (avg):  ${r.avg_cost_bps?.toFixed(0) ?? '?'} bps`);
      const top = r.top_laws ?? [];
      if (top.length) {
        console.log(`\n   🏆 Top performing laws:`);
        top.slice(0, 8).forEach(l => {
          const g = GRADE_EMOJI[l.grade ?? '?'] ?? '';
          console.log(`     ${g} ${String(l.law_id).padEnd(24)} net=+${l.net_alpha?.toFixed(2)}%  WR=${((l.gross_win_rate??0)*100)?.toFixed(0)}%`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: symbol|universe|oos|compare|hurdle|full`); process.exit(1);
}
