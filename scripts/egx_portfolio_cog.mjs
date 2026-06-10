#!/usr/bin/env node
/**
 * Phase 32 — Portfolio Cognition System runner
 *
 * Sections:
 *   orchestrate  — build full portfolio allocation  --capital 100000
 *   size         — compute dynamic position sizes   --capital 100000
 *   risk         — systemic risk budget analysis
 *   concentrate  — adaptive concentration mode
 *   build        — full portfolio package (recommended)
 *   full         — alias for build
 */
import { pythonPortfolioOrchestrate, pythonPortfolioSizePositions,
         pythonPortfolioRiskBudget, pythonPortfolioAdaptiveConcentration,
         pythonPortfolioBuild, pythonPortfolioBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'build';
const capital = parseFloat(args[args.indexOf('--capital') + 1] ?? '100000');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💼 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'orchestrate': {
    banner(`Portfolio: Orchestrating allocation (${capital.toLocaleString()} EGP)…`);
    const r = await pythonPortfolioOrchestrate({ capital });
    if (r?.portfolio) {
      console.log(`\n   Positions: ${r.n_positions}  Score: ${r.portfolio_score?.toFixed(1)}  Friction: ${r.total_friction_bps}bps`);
      console.log(`   Regime: ${r.regime}  Mode: ${r.concentration_mode}`);
      console.log('\n   Allocation:');
      r.portfolio.forEach(p =>
        console.log(`   ${String(p.symbol).padEnd(10)} ${(p.weight*100).toFixed(1)}%  ${p.amount_egp?.toLocaleString()} EGP  score:${p.intelligence_score}  ${p.size_rationale}`));
      if (r.warnings?.length) {
        console.log('\n   ⚠️  Warnings:');
        r.warnings.forEach(w => console.log(`   ${w}`));
      }
    } else pp(r);
    break;
  }
  case 'size': {
    banner('Portfolio: Dynamic Position Sizing');
    const r = await pythonPortfolioSizePositions({ capital });
    if (r?.positions) {
      console.log(`\n   Position Sizes (${capital.toLocaleString()} EGP capital):`);
      r.positions.forEach(p =>
        console.log(`   ${String(p.symbol).padEnd(10)} kelly: ${(p.kelly_fraction*100).toFixed(1)}%  adj: ${(p.adj_fraction*100).toFixed(1)}%  ${p.amount_egp?.toLocaleString()} EGP  ${p.rationale}`));
    } else pp(r);
    break;
  }
  case 'risk': {
    banner('Portfolio: Systemic Risk Budget');
    const r = await pythonPortfolioRiskBudget({});
    if (r?.systemic_risk_score !== undefined) {
      console.log(`\n   Systemic Risk Score: ${r.systemic_risk_score?.toFixed(1)} / 100  [${r.risk_level}]`);
      if (r.risk_breakdown) {
        console.log('\n   Risk Breakdown:');
        Object.entries(r.risk_breakdown).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(30)} ${typeof v === 'number' ? v.toFixed(3) : v}`));
      }
      if (r.recommendations?.length) {
        console.log('\n   📋 Recommendations:');
        r.recommendations.forEach(rec => console.log(`   • ${rec}`));
      }
    } else pp(r);
    break;
  }
  case 'concentrate': {
    banner('Portfolio: Adaptive Concentration Mode');
    const r = await pythonPortfolioAdaptiveConcentration({});
    if (r?.regime) {
      console.log(`\n   Regime:    ${r.regime}`);
      console.log(`   Mode:      ${r.concentration_mode}`);
      console.log(`   Max positions: ${r.max_positions}`);
      console.log(`   Max single:    ${(r.max_single_pct * 100).toFixed(0)}%`);
      console.log(`   Max sector:    ${(r.max_sector_pct * 100).toFixed(0)}%`);
      console.log(`   Rationale: ${r.rationale}`);
    } else pp(r);
    break;
  }
  case 'build':
  case 'full': {
    banner(`Portfolio: Full Cognition Package (${capital.toLocaleString()} EGP)`);
    const r = await pythonPortfolioBuildFull({ capital });
    if (r?.allocation?.portfolio) {
      const alloc = r.allocation;
      console.log(`\n   ✅ Portfolio Ready`);
      console.log(`   Positions: ${alloc.n_positions}  Score: ${alloc.portfolio_score?.toFixed(1)}`);
      console.log(`   Mode: ${alloc.concentration_mode}  Risk: ${r.risk?.risk_level}`);
      alloc.portfolio.forEach(p =>
        console.log(`   ${String(p.symbol).padEnd(10)} ${(p.weight*100).toFixed(1)}%  ${p.amount_egp?.toLocaleString()} EGP`));
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
