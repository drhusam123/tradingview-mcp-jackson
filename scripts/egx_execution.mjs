#!/usr/bin/env node
/**
 * Phase 27 — Execution Reality Engine runner
 *
 * Sections:
 *   liquidity      — build liquidity profiles for all symbols
 *   adjust         — adjust returns for friction
 *   stress         — portfolio stress test
 *   feasibility    — scan top candidates for feasibility
 *   profile        — full profile for one stock  --ticker COMI
 *   full           — liquidity + feasibility
 */
import { pythonExecutionLiquidityProfiles, pythonExecutionAdjustReturns,
         pythonExecutionPortfolioStress, pythonExecutionScanFeasibility,
         pythonExecutionProfile } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'feasibility';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⚖️  ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'liquidity': {
    banner('Execution: Building liquidity profiles…');
    const r = await pythonExecutionLiquidityProfiles({});
    if (r?.tier_distribution) {
      console.log('\n💧 Liquidity Tiers:');
      Object.entries(r.tier_distribution).forEach(([t, n]) =>
        console.log(`   ${t.padEnd(10)} ${n} stocks`));
    }
    if (r?.deepest) console.log(`   Deepest: ${r.deepest.symbol}  ${r.deepest.avg_daily_volume?.toLocaleString()} EGP/day`);
    pp(r); break;
  }
  case 'adjust': {
    banner('Execution: Adjusting returns for friction…');
    const r = await pythonExecutionAdjustReturns({});
    console.log(`   Adjusted: ${r.n_adjusted}  Feasible: ${r.feasible_count}  Avg friction: ${r.avg_friction_bps} bps`);
    pp(r); break;
  }
  case 'stress': {
    banner('Execution: Portfolio stress test');
    const r = await pythonExecutionPortfolioStress({ capital: 100000 });
    if (r?.theoretical_sharpe !== undefined)
      console.log(`   Sharpe: ${r.theoretical_sharpe} → ${r.realistic_sharpe}  Drag: ${r.friction_drag_bps} bps`);
    pp(r); break;
  }
  case 'feasibility': {
    banner('Execution: Scanning feasibility of top candidates…');
    const r = await pythonExecutionScanFeasibility({});
    if (r?.n_feasible !== undefined) {
      console.log(`   Feasible: ${r.n_feasible}  Borderline: ${r.n_borderline}  Infeasible: ${r.n_infeasible}`);
      if (r.ranked_list) {
        console.log('\n✅ Top Feasible:');
        r.ranked_list.filter(x => x.feasibility === 'FEASIBLE').slice(0, 10).forEach(x =>
          console.log(`   ${String(x.symbol).padEnd(10)} tier: ${x.tier?.padEnd(10)} realistic: ${x.realistic_return_pct}%  friction: ${x.total_friction_bps}bps`));
      }
    } else pp(r);
    break;
  }
  case 'profile': {
    banner(`Execution: Profile for ${ticker}`);
    const r = await pythonExecutionProfile({ symbol: ticker });
    if (r?.liquidity_profile) {
      console.log(`\n   ${ticker}: ${r.liquidity_profile.tier} tier`);
      console.log(`   Spread: ${r.cost_structure?.spread_cost_bps}bps  Slippage: ${r.cost_structure?.slippage_est_bps}bps`);
      console.log(`   Total friction: ${r.cost_structure?.total_friction_bps}bps`);
      console.log(`   Break-even: ${r.cost_structure?.break_even_return_pct}%`);
      console.log(`   Feasibility: ${r.expected_performance?.feasibility}`);
    }
    pp(r); break;
  }
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
