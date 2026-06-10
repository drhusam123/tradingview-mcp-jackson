#!/usr/bin/env node
/**
 * Phase 44 — Execution Reality Engine runner
 * "التكاليف الحقيقية — من 7.2 إلى 9+"
 *
 * Sections:
 *   entry       — simulate entry cost  --ticker COMI --price 50 --shares 1000
 *   exit        — simulate exit cost   --ticker COMI --price 52 --shares 1000 --days 5
 *   pnl         — realistic P&L for all historical trades
 *   calendar    — 5-day liquidity calendar
 *   reality     — reality check: theoretical vs realized EAE (default)
 *   full        — reality + calendar + save to DB (recommended)
 */
import { pythonExecSimulateEntry, pythonExecSimulateExit, pythonExecRealisticPnL,
         pythonExecCalendar, pythonExecRealityCheck,
         pythonExecBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'reality';
const ticker  = args[args.indexOf('--ticker') + 1]  ?? 'COMI';
const price   = parseFloat(args[args.indexOf('--price') + 1]  ?? '50');
const shares  = parseInt(args[args.indexOf('--shares') + 1] ?? '1000');
const days    = parseInt(args[args.indexOf('--days') + 1]   ?? '5');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💰 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'entry': {
    banner(`Execution Reality: Simulate Entry — ${ticker}`);
    const r = await pythonExecSimulateEntry({ symbol: ticker, price, shares });
    if (r?.total_entry_cost !== undefined) {
      const ok = r.feasible ? '✅' : '❌ NOT FEASIBLE';
      console.log(`\n   ${ok}  ${ticker}: ${shares.toLocaleString()} shares @ ${price}`);
      console.log(`\n   Total entry cost: EGP ${r.total_entry_cost?.toFixed(2)}  (${r.entry_cost_bps?.toFixed(0)} bps)`);
      if (r.components) {
        console.log('\n   Cost breakdown:');
        Object.entries(r.components).forEach(([k, v]) =>
          console.log(`   ${String(k).padEnd(20)} EGP ${v?.toFixed(2)}`));
      }
      if (r.warnings?.length)
        r.warnings.forEach(w => console.log(`   ⚠️  ${w}`));
    } else pp(r);
    break;
  }
  case 'exit': {
    banner(`Execution Reality: Simulate Exit — ${ticker}`);
    const r = await pythonExecSimulateExit({ symbol: ticker, price, shares, hold_days: days });
    if (r?.total_exit_cost !== undefined) {
      console.log(`\n   ${ticker}: exit ${shares.toLocaleString()} @ ${price}  (held ${days}d)`);
      console.log(`   Total exit cost: EGP ${r.total_exit_cost?.toFixed(2)}  (${r.exit_cost_bps?.toFixed(0)} bps)`);
      console.log(`   T+3 cycles during hold: ${r.t3_cycles}`);
      if (r.total_roundtrip_cost_bps)
        console.log(`   Round-trip cost: ${r.total_roundtrip_cost_bps?.toFixed(0)} bps`);
    } else pp(r);
    break;
  }
  case 'pnl': {
    banner('Execution Reality: Realistic P&L');
    const r = await pythonExecRealisticPnL({});
    if (r?.n_trades !== undefined) {
      console.log(`\n   Trades analyzed: ${r.n_trades}`);
      console.log(`   Theoretical return:  ${r.avg_theoretical_return_bps?.toFixed(0)} bps`);
      console.log(`   Realized return:     ${r.avg_realized_return_bps?.toFixed(0)} bps`);
      console.log(`   Cost drag:           ${r.avg_cost_drag_bps?.toFixed(0)} bps`);
      console.log(`   Trades flipped -ve:  ${r.trades_that_flip_negative}`);
      console.log(`   Reality edge:        ${r.reality_adjusted_edge?.toFixed(4)}`);
      console.log(`\n   ${r.assessment}`);
    } else pp(r);
    break;
  }
  case 'calendar': {
    banner('Execution Reality: Liquidity Calendar');
    const r = await pythonExecCalendar({});
    if (r?.calendar) {
      console.log(`\n   Best day:  ${r.best_day}`);
      console.log(`   Worst day: ${r.worst_day}\n`);
      console.log('   Day          Liquidity  Spread(bps)  Max order   Window');
      r.calendar.forEach(d => {
        const liq = d.liquidity_score >= 70 ? '🟢' : d.liquidity_score >= 50 ? '🟡' : '🔴';
        console.log(`   ${liq} ${String(d.day).padEnd(12)} ${String(d.liquidity_score?.toFixed(0)).padStart(6)}     ${String(d.spread_estimate_bps?.toFixed(0)).padStart(6)} bps   ${String((d.recommended_max_order_pct_adv*100).toFixed(1)).padStart(5)}% adv   ${d.optimal_entry_window}`);
      });
    } else pp(r);
    break;
  }
  case 'reality': {
    banner('Execution Reality: EAE Reality Check');
    const r = await pythonExecRealityCheck({});
    if (r?.n_laws !== undefined) {
      const rate = (r.reality_survival_rate*100)?.toFixed(0);
      console.log(`\n   Laws analyzed: ${r.n_laws}`);
      console.log(`   ✅ Survive reality: ${r.n_survive_reality}  (${rate}%)`);
      console.log(`   ❌ Killed by costs: ${r.n_killed_by_costs}`);
      console.log(`   Avg cost drag: ${r.avg_cost_drag_bps?.toFixed(0)} bps`);
      if (r.best_surviving_laws?.length) {
        console.log('\n   Laws that survive EGX execution costs:');
        r.best_surviving_laws.slice(0, 8).forEach(l =>
          console.log(`   ✅ ${String(l.law_name).padEnd(35)} theoretical:${l.theoretical_eae?.toFixed(4)}  realized:${l.realistic_eae?.toFixed(4)}`));
      }
      if (r.laws_killed?.length) {
        console.log('\n   Laws killed by execution costs:');
        r.laws_killed.slice(0, 5).forEach(l =>
          console.log(`   ❌ ${String(l.law_name).padEnd(35)} theoretical:${l.theoretical_eae?.toFixed(4)}  realized:${l.realistic_eae?.toFixed(4)}`));
      }
      console.log(`\n   ${r.reality_assessment}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Execution Reality: Full Build');
    const r = await pythonExecBuildFull({});
    if (r?.survival_rate !== undefined) {
      console.log(`\n   Laws checked: ${r.n_laws_checked}`);
      console.log(`   Survival rate: ${(r.survival_rate*100)?.toFixed(0)}%`);
      console.log(`   Avg cost drag: ${r.avg_cost_drag_bps?.toFixed(0)} bps`);
      console.log(`   Best day: ${r.best_day}`);
      console.log(`   ${r.reality_assessment}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
