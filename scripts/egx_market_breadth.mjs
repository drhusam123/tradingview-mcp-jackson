#!/usr/bin/env node
/**
 * Phase 56 — Market Breadth Engine runner
 * "نبض السوق الداخلي — مؤشرات الاتساع"
 *
 * Sections: signal | ad | ma | highs | mcclellan | sector | history | full
 *   --date 2026-05-15
 */
import { pythonBreadthSignal, pythonBreadthAD, pythonBreadthMA,
         pythonBreadthHighsLows, pythonBreadthMcClellan, pythonBreadthSector,
         pythonBreadthHistory, pythonBreadthBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'signal';
const dateIdx = args.indexOf('--date');
const date    = dateIdx !== -1 ? args[dateIdx + 1] : null;
const daysIdx = args.indexOf('--days');
const days    = daysIdx !== -1 ? parseInt(args[daysIdx + 1]) : 90;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📊 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const SIG_EMOJI = {
  BREADTH_BULL: '🟢🟢', BREADTH_LEAN_BULL: '🟢', BREADTH_NEUTRAL: '⚖️',
  BREADTH_LEAN_BEAR: '🔴', BREADTH_BEAR: '🔴🔴',
};
const MC_EMOJI = { OVERBOUGHT: '⚡', OVERSOLD: '💎', NEUTRAL: '⚖️' };

switch (section) {
  case 'signal': {
    banner(`Breadth: Market Signal${date ? ' — '+date : ''}`);
    const r = await pythonBreadthSignal(date ? { date } : {});
    if (r?.breadth_score !== undefined) {
      const em = SIG_EMOJI[r.signal] ?? '?';
      console.log(`\n   ${em} Signal: ${r.signal}`);
      console.log(`   Breadth Score: ${r.breadth_score?.toFixed(1)}/100`);
      console.log(`   Regime Input:  ${r.regime_input}`);
      if (r.key_stats) {
        console.log(`\n   A/D Ratio:      ${(r.key_stats.ad_ratio * 100)?.toFixed(1)}%`);
        console.log(`   Above MA50:     ${r.key_stats.pct_above_ma50?.toFixed(1)}%`);
        console.log(`   Above MA200:    ${r.key_stats.pct_above_ma200?.toFixed(1)}%`);
        console.log(`   52w Highs:      ${r.key_stats.n_new_highs_52w}  |  Lows: ${r.key_stats.n_new_lows_52w}`);
        console.log(`   McClellan:      ${r.key_stats.mcclellan_oscillator?.toFixed(2)}`);
      }
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'ad': {
    banner(`Breadth: Advance/Decline${date ? ' — '+date : ''}`);
    const r = await pythonBreadthAD(date ? { start_date: date, end_date: date } : {});
    if (r?.series?.length || r?.n_advances !== undefined) {
      const series = r.series ?? [r];
      series.slice(-20).forEach(d => {
        const em = d.ad_ratio >= 0.6 ? '🟢' : d.ad_ratio >= 0.5 ? '🟡' : '🔴';
        console.log(`   ${em} ${d.date}  ${d.n_advances}↑ / ${d.n_declines}↓  ratio=${(d.ad_ratio*100)?.toFixed(1)}%  line=${d.ad_line_value?.toFixed(0)}`);
      });
    } else pp(r);
    break;
  }
  case 'ma': {
    banner(`Breadth: % Above Moving Averages${date ? ' — '+date : ''}`);
    const r = await pythonBreadthMA(date ? { date } : {});
    if (r?.pct_above_ma20 !== undefined) {
      const pct = (v) => {
        const em = v >= 70 ? '🟢' : v >= 50 ? '🟡' : '🔴';
        return `${em} ${v?.toFixed(1)}%`;
      };
      console.log(`\n   Symbols checked: ${r.n_symbols_checked}`);
      console.log(`   Above MA20:  ${pct(r.pct_above_ma20)}  (${r.n_above_ma20} stocks)`);
      console.log(`   Above MA50:  ${pct(r.pct_above_ma50)}  (${r.n_above_ma50} stocks)`);
      console.log(`   Above MA200: ${pct(r.pct_above_ma200)}  (${r.n_above_ma200} stocks)`);
    } else pp(r);
    break;
  }
  case 'highs': {
    banner(`Breadth: 52-Week Highs/Lows${date ? ' — '+date : ''}`);
    const r = await pythonBreadthHighsLows(date ? { date } : {});
    if (r?.n_new_highs !== undefined) {
      const ratio = r.n_new_highs / Math.max(r.n_new_highs + r.n_new_lows, 1);
      const em = ratio >= 0.7 ? '🟢' : ratio >= 0.5 ? '🟡' : '🔴';
      console.log(`\n   ${em} New 52w Highs: ${r.n_new_highs}  |  New 52w Lows: ${r.n_new_lows}`);
      console.log(`   H/L Ratio: ${(ratio*100)?.toFixed(1)}%`);
      if (r.symbols_highs?.length)
        console.log(`\n   📈 New Highs: ${r.symbols_highs.slice(0, 15).join(', ')}`);
      if (r.symbols_lows?.length)
        console.log(`   📉 New Lows:  ${r.symbols_lows.slice(0, 10).join(', ')}`);
    } else pp(r);
    break;
  }
  case 'mcclellan': {
    banner(`Breadth: McClellan Oscillator${date ? ' — '+date : ''}`);
    const r = await pythonBreadthMcClellan(date ? { date } : {});
    if (r?.mcclellan_oscillator !== undefined) {
      const em = MC_EMOJI[r.signal] ?? '⚖️';
      const osc = r.mcclellan_oscillator;
      const oscBar = '█'.repeat(Math.min(Math.abs(osc / 5), 10));
      const dir = osc >= 0 ? '🟢' : '🔴';
      console.log(`\n   ${em} Signal: ${r.signal}`);
      console.log(`   Oscillator:  ${dir} ${osc?.toFixed(2)}  ${oscBar}`);
      console.log(`   Summation:   ${r.mcclellan_summation?.toFixed(2)}`);
    } else pp(r);
    break;
  }
  case 'sector': {
    banner(`Breadth: Sector Breakdown${date ? ' — '+date : ''}`);
    const r = await pythonBreadthSector(date ? { date } : {});
    const sectors = r?.sectors ?? r;
    if (Array.isArray(sectors) && sectors.length) {
      console.log('\n   Sector                  Breadth%  A↑   D↓   Signal');
      console.log('   ' + '─'.repeat(60));
      sectors.forEach(s => {
        const em = s.breadth_pct >= 65 ? '🟢' : s.breadth_pct >= 50 ? '🟡' : '🔴';
        console.log(`   ${em} ${String(s.sector).padEnd(22)} ${String(s.breadth_pct?.toFixed(1)+'%').padStart(7)}  ${String(s.n_advances).padStart(3)}  ${String(s.n_declines).padStart(3)}  ${s.signal ?? ''}`);
      });
    } else pp(r);
    break;
  }
  case 'history': {
    banner(`Breadth: ${days}-Day History`);
    const r = await pythonBreadthHistory({ days });
    const computed = r?.computed ?? [];
    if (computed.length) {
      console.log(`\n   Computed ${computed.length} days\n`);
      console.log('   Date        Score  Signal           A/D%   MA50%');
      console.log('   ' + '─'.repeat(58));
      computed.slice(-20).forEach(d => {
        const em = SIG_EMOJI[d.signal] ?? '?';
        console.log(`   ${d.date}  ${String(d.breadth_score?.toFixed(1)).padStart(5)}  ${em} ${String(d.signal).padEnd(18)} ${String((d.ad_ratio*100)?.toFixed(1)+'%').padStart(5)}  ${d.pct_above_ma50?.toFixed(1)}%`);
      });
      if (r.skipped > 0) console.log(`\n   (${r.skipped} days skipped — already cached)`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Breadth: Full Market Breadth Report');
    const r = await pythonBreadthBuildFull(date ? { date } : {});
    if (r?.breadth?.breadth_score !== undefined) {
      const b = r.breadth;
      const em = SIG_EMOJI[b.signal] ?? '?';
      console.log(`\n   Date: ${r.date}`);
      console.log(`   ${em} Signal: ${b.signal}  (Score: ${b.breadth_score?.toFixed(1)}/100)`);
      console.log(`\n   Advances:   ${b.n_advances}  |  Declines: ${b.n_declines}  |  Unchanged: ${b.n_unchanged}`);
      console.log(`   A/D Ratio:  ${(b.ad_ratio*100)?.toFixed(1)}%  |  A/D Line: ${b.ad_line_value?.toFixed(0)}`);
      console.log(`   MA20:  ${b.pct_above_ma20?.toFixed(1)}%  |  MA50: ${b.pct_above_ma50?.toFixed(1)}%  |  MA200: ${b.pct_above_ma200?.toFixed(1)}%`);
      console.log(`   52w Highs: ${b.n_new_highs_52w}  |  Lows: ${b.n_new_lows_52w}  |  H/L Ratio: ${(b.hl_ratio*100)?.toFixed(0)}%`);
      console.log(`   McClellan:  ${b.mcclellan_oscillator?.toFixed(2)}  |  Summation: ${b.mcclellan_summation?.toFixed(2)}`);
      if (r.signal?.recommendation) console.log(`\n   ${r.signal.recommendation}`);
      if (r.new_highs_symbols?.length)
        console.log(`\n   📈 New 52w Highs: ${r.new_highs_symbols.slice(0,12).join(', ')}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
