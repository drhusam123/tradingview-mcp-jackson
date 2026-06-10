#!/usr/bin/env node
/**
 * Phase 49 — Deep History Engine runner
 * "10 سنوات من الذاكرة — Long-Term Intelligence"
 *
 * Sections: coverage | regime | volatility | pattern | cycles | sector | full
 */
import { pythonDeepHistoryCoverage, pythonDeepHistoryRegime, pythonDeepHistoryVolatility,
         pythonDeepHistoryPattern, pythonDeepHistoryCycles, pythonDeepHistorySector,
         pythonDeepHistoryBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'coverage';
const symbol  = args[args.indexOf('--symbol') + 1] ?? 'COMI';
const sector  = args[args.indexOf('--sector') + 1] ?? null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📜 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'coverage': {
    banner('Deep History: Data Coverage');
    const r = await pythonDeepHistoryCoverage({});
    if (r?.data_ready !== undefined) {
      const em = r.data_ready ? '✅' : '⚠️';
      console.log(`\n   ${em} Data ready: ${r.data_ready ? 'YES' : 'NO — run: npm run egx:fetch:deep'}`);
      if (r.weekly?.total_bars) {
        console.log(`\n   📊 Weekly:  ${r.weekly.symbols} symbols  ${r.weekly.total_bars} bars  (${r.weekly.oldest} → ${r.weekly.newest})`);
      } else { console.log('\n   ⚠️  Weekly: no data yet'); }
      if (r.monthly?.total_bars) {
        console.log(`   📅 Monthly: ${r.monthly.symbols} symbols  ${r.monthly.total_bars} bars  (${r.monthly.oldest} → ${r.monthly.newest})`);
      } else { console.log('   ⚠️  Monthly: no data yet'); }
      if (r.daily_only) {
        console.log(`   📈 Daily:   ${r.daily_only.symbols} symbols  ${r.daily_only.total_bars} bars  (${r.daily_only.oldest} → ${r.daily_only.newest})`);
      }
    } else pp(r);
    break;
  }
  case 'regime': {
    banner(`Deep History: Long-Term Regime — ${symbol}`);
    const r = await pythonDeepHistoryRegime({ symbol });
    if (r?.regime) {
      const em = r.regime === 'BULL' ? '📈' : r.regime === 'BEAR' ? '📉' : '↔️';
      console.log(`\n   ${em} Regime: ${r.regime}  |  Strength: ${r.regime_strength?.toFixed(1)}/100`);
      if (r.ma13w && r.ma26w) console.log(`   MA13w: ${r.ma13w?.toFixed(2)}  |  MA26w: ${r.ma26w?.toFixed(2)}`);
      if (r.deviation_from_mean !== undefined) console.log(`   Deviation from mean: ${(r.deviation_from_mean * 100)?.toFixed(1)}%`);
      if (r.current_price) console.log(`   Current: ${r.current_price?.toFixed(2)}`);
      console.log(`\n   ${r.description ?? ''}`);
    } else pp(r);
    break;
  }
  case 'volatility': {
    banner(`Deep History: Volatility Profile — ${symbol}`);
    const r = await pythonDeepHistoryVolatility({ symbol });
    if (r?.vol_regime) {
      const em = r.vol_regime === 'LOW' ? '🟢' : r.vol_regime === 'NORMAL' ? '🟡' : r.vol_regime === 'HIGH' ? '🟠' : '🔴';
      console.log(`\n   ${em} Vol regime: ${r.vol_regime}`);
      if (r.vol_13w !== undefined) console.log(`   Vol 13w: ${(r.vol_13w * 100)?.toFixed(1)}%  |  26w: ${(r.vol_26w * 100)?.toFixed(1)}%  |  52w: ${(r.vol_52w * 100)?.toFixed(1)}%`);
      if (r.percentile !== undefined) console.log(`   Percentile: ${r.percentile?.toFixed(0)}th (vs history)`);
    } else pp(r);
    break;
  }
  case 'pattern': {
    banner(`Deep History: Pattern Match — ${symbol}`);
    const r = await pythonDeepHistoryPattern({ symbol });
    if (r?.matches?.length) {
      console.log(`\n   🔍 Top historical pattern matches:`);
      r.matches.forEach((m, i) =>
        console.log(`   ${i+1}. ${m.period_start} → ${m.period_end}  similarity:${(m.similarity * 100)?.toFixed(0)}%  next4w:${m.next_4w_return !== null ? (m.next_4w_return * 100)?.toFixed(1) + '%' : 'N/A'}`));
      if (r.avg_forward_return !== undefined)
        console.log(`\n   📊 Avg forward return (4w): ${(r.avg_forward_return * 100)?.toFixed(1)}%`);
    } else pp(r);
    break;
  }
  case 'cycles': {
    banner('Deep History: Market Cycle Analysis');
    const r = await pythonDeepHistoryCycles({});
    if (r?.cycle_phase) {
      const em = r.cycle_phase?.includes('BULL') ? '📈' : r.cycle_phase?.includes('BEAR') ? '📉' : '⚖️';
      console.log(`\n   ${em} Cycle phase: ${r.cycle_phase}`);
      console.log(`   Current phase age: ${r.cycle_age_weeks} weeks`);
      if (r.avg_bull_weeks && r.avg_bear_weeks)
        console.log(`   Avg cycle: Bull=${r.avg_bull_weeks?.toFixed(0)}w  Bear=${r.avg_bear_weeks?.toFixed(0)}w`);
      console.log(`\n   ${r.description ?? ''}`);
    } else pp(r);
    break;
  }
  case 'sector': {
    banner(`Deep History: Sector Long-Term — ${sector ?? 'all'}`);
    const r = await pythonDeepHistorySector({ sector });
    if (r?.sectors || r?.momentum) {
      const entries = Object.entries(r.sectors ?? r.momentum ?? {});
      console.log('\n   Sector          13w Return  Weekly Alpha  Position');
      console.log('   ' + '─'.repeat(55));
      entries.sort((a,b) => (b[1].return_13w ?? 0) - (a[1].return_13w ?? 0)).slice(0, 10).forEach(([k, v]) => {
        const em = (v.return_13w ?? 0) > 0 ? '📈' : '📉';
        console.log(`   ${em} ${String(k).padEnd(16)} ${String(((v.return_13w ?? 0)*100)?.toFixed(1)+'%').padStart(8)}  ${String(((v.alpha ?? 0)*100)?.toFixed(1)+'%').padStart(10)}  ${v.position ?? ''}`);
      });
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Deep History: Full Build');
    const r = await pythonDeepHistoryBuildFull({});
    if (r?.regime || r?.cycle_phase) {
      const em = r.regime === 'BULL' ? '📈' : r.regime === 'BEAR' ? '📉' : '⚖️';
      console.log(`\n   ${em} Long-term regime: ${r.regime}  |  Strength: ${r.regime_strength?.toFixed(0)}/100`);
      console.log(`   Cycle phase: ${r.cycle_phase}  (${r.cycle_age_weeks}w old)`);
      console.log(`   Symbols with weekly data: ${r.n_symbols_weekly}`);
      console.log(`\n   ${r.summary ?? ''}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
