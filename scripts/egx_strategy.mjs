#!/usr/bin/env node
/**
 * Phase 59 — Strategy Backtest runner
 * "اختبار الاستراتيجيات على قوانين السوق"
 *
 * Sections: list | generate | validate | rank | full
 *   --law-type universal|structural
 *   --symbol COMI
 *   --n 5
 */
import { pythonStrategyGenerate, pythonStrategyList, pythonStrategyParse,
         pythonStrategyValidate, pythonStrategyRank, pythonStrategyBuildFull }
  from '../src/egx/index.js';

const args      = process.argv.slice(2);
const section   = args.find(a => !a.startsWith('--')) ?? 'rank';
const ltIdx     = args.indexOf('--law-type');
const lawType   = ltIdx   !== -1 ? args[ltIdx   + 1] : 'universal';
const symIdx    = args.indexOf('--symbol');
const symbol    = symIdx  !== -1 ? args[symIdx  + 1] : 'COMI';
const nIdx      = args.indexOf('--n');
const n         = nIdx    !== -1 ? parseInt(args[nIdx + 1]) : 5;
const liIdx     = args.indexOf('--law-id');
const lawId     = liIdx   !== -1 ? args[liIdx   + 1] : null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⚗️  ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { A: '🏆', B: '🥇', C: '🥈', D: '🥉', F: '❌', '?': '❓' };
const DIR_EMOJI   = { LONG: '📈', SHORT: '📉', BOTH: '↕️' };

switch (section) {
  case 'list': {
    banner(`Laws Ready for Backtesting — type=${lawType}`);
    const r = await pythonStrategyList({ law_type: lawType, min_precision: 0.55, limit: 30 });
    const laws = r?.laws ?? [];
    if (!laws.length) {
      console.log('\n   No laws found. Run law discovery first.');
      break;
    }
    console.log(`\n   ${laws.length} law(s) ready:\n`);
    console.log('   #    Law ID              Dir    Prec%   Tested?');
    console.log('   ' + '─'.repeat(58));
    laws.slice(0, 25).forEach((l, i) => {
      const dir = DIR_EMOJI[l.direction] ?? '?';
      const tested = l.already_tested ? '✅' : '—';
      console.log(`   ${String(i+1).padStart(3)}. ${String(l.law_id).padEnd(20)} ${dir} ${String(l.direction).padEnd(6)} ${String((l.precision*100)?.toFixed(1)+'%').padStart(6)}   ${tested}`);
    });
    break;
  }
  case 'generate': {
    banner(`Generate Pine Strategy — type=${lawType}, symbol=${symbol}`);
    const r = await pythonStrategyGenerate({ law_type: lawType, symbol, n_laws: n });
    if (r?.pine_code) {
      console.log(`\n   Law: ${r.law_name ?? r.law_id}`);
      console.log(`   Direction: ${DIR_EMOJI[r.direction] ?? ''} ${r.direction}`);
      console.log(`   Precision: ${(r.precision*100)?.toFixed(1)}%`);
      console.log(`\n   Pine Script (first 40 lines):\n`);
      const lines = r.pine_code.split('\n').slice(0, 40);
      lines.forEach((l, i) => console.log(`   ${String(i+1).padStart(3)} │ ${l}`));
      if (r.saved_to) console.log(`\n   ✅ Saved to: ${r.saved_to}`);
    } else pp(r);
    break;
  }
  case 'validate': {
    if (!lawId) { console.log('Error: --law-id required for validate section'); process.exit(1); }
    banner(`Validate Law — ${lawId}`);
    const r = await pythonStrategyValidate({ law_id: lawId });
    if (r?.is_valid !== undefined) {
      const em = r.is_valid ? '✅' : '❌';
      console.log(`\n   ${em} Valid: ${r.is_valid}`);
      if (r.win_rate != null)       console.log(`   Win rate:      ${(r.win_rate*100)?.toFixed(1)}%`);
      if (r.profit_factor != null)  console.log(`   Profit factor: ${r.profit_factor?.toFixed(2)}`);
      if (r.max_drawdown != null)   console.log(`   Max drawdown:  ${r.max_drawdown?.toFixed(1)}%`);
      if (r.sharpe_ratio != null)   console.log(`   Sharpe ratio:  ${r.sharpe_ratio?.toFixed(2)}`);
      if (r.grade)                  console.log(`   Grade:         ${GRADE_EMOJI[r.grade] ?? ''} ${r.grade}`);
      if (r.issues?.length) {
        console.log('\n   Issues:');
        r.issues.forEach(i => console.log(`     ⚠️  ${i}`));
      }
    } else pp(r);
    break;
  }
  case 'rank': {
    banner('Ranked Strategies by Performance');
    const r = await pythonStrategyRank({ min_tests: 1 });
    const rankings = r?.rankings ?? [];
    if (!rankings.length) {
      console.log('\n   No backtest results yet. Run without --rank to test laws.');
      console.log('   Try: npm run egx:strategy:generate');
      break;
    }
    console.log(`\n   ${rankings.length} law(s) ranked:\n`);
    console.log('   Rank  Law                  WinRate%  PF    Sharpe  DD%    Grade');
    console.log('   ' + '─'.repeat(72));
    rankings.slice(0, 20).forEach(l => {
      const g = GRADE_EMOJI[l.grade ?? '?'] ?? '?';
      console.log(`   ${String(l.rank).padStart(4)}. ${String(l.law_name ?? l.law_id).padEnd(20)} ${String((l.avg_win_rate*100)?.toFixed(1)+'%').padStart(8)} ${String(l.avg_profit_factor?.toFixed(2)).padStart(5)}  ${String(l.avg_sharpe?.toFixed(2)).padStart(6)}  ${String(l.avg_drawdown?.toFixed(1)+'%').padStart(6)}   ${g} ${l.grade ?? '?'}`);
    });
    if (r?.top_grade_a?.length)
      console.log(`\n   🏆 Grade-A laws: ${r.top_grade_a.slice(0, 5).join(', ')}`);
    break;
  }
  case 'full': {
    banner(`Strategy Full Report — type=${lawType}, symbol=${symbol}`);
    const r = await pythonStrategyBuildFull({ law_type: lawType, symbol, n_laws: n });
    if (r?.laws_ready_for_testing?.length || r?.top_law_strategy) {
      console.log(`\n   Laws ready: ${r.laws_ready_for_testing?.length ?? 0}`);
      if (r.top_law_strategy) {
        const tl = r.top_law_strategy;
        const dir = DIR_EMOJI[tl.direction] ?? '';
        console.log(`\n   Top law to test:`);
        console.log(`     ${dir} ${tl.law_name ?? tl.law_id}`);
        console.log(`     Direction: ${tl.direction}  Precision: ${(tl.precision*100)?.toFixed(1)}%`);
        if (tl.saved_to) console.log(`     Pine saved to: ${tl.saved_to}`);
      }
      if (r.laws_ready_for_testing?.length) {
        console.log(`\n   Next laws queue:`);
        r.laws_ready_for_testing.slice(0, 8).forEach(l => {
          const dir = DIR_EMOJI[l.direction] ?? '';
          const tested = l.already_tested ? '✅' : '—';
          console.log(`     ${dir} ${String(l.law_id).padEnd(22)} prec=${(l.precision*100)?.toFixed(1)}%  ${tested}`);
        });
      }
      if (r.rankings?.length) {
        console.log(`\n   Best tested:`);
        r.rankings.slice(0, 3).forEach(l => {
          const g = GRADE_EMOJI[l.grade ?? '?'] ?? '';
          console.log(`     ${g} ${l.law_name ?? l.law_id}  WR=${(l.avg_win_rate*100)?.toFixed(1)}%  PF=${l.avg_profit_factor?.toFixed(2)}`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: list|generate|validate|rank|full`); process.exit(1);
}
