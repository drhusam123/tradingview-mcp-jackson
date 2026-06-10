#!/usr/bin/env node
/**
 * Phase 82 — Event-Driven Backtesting (backtesting.py)
 *
 * Sections:
 *   strategy    — Run single-symbol strategy with realistic execution
 *   portfolio   — Multi-symbol portfolio backtest with constraints
 *   walkforward — Walk-forward validation with slippage + settlement
 *   cost        — Execution cost model (spread, market impact, settlement)
 *   report      — Full execution research report
 */
import { pythonBTRunStrategy, pythonBTPortfolio, pythonBTWalkForward,
         pythonBTExecCost, pythonBTReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const symbol  = args[args.indexOf('--symbol') + 1];
function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  📈 ${t}\n` + '═'.repeat(62)); }
function pct(v) { return v != null ? (v*100).toFixed(1)+'%' : 'N/A'; }

async function runStrategy() {
  banner(`Strategy Backtest — ${symbol || 'COMI'}`);
  const r = await pythonBTRunStrategy({ symbol: symbol || 'COMI', holding_days: 5, commission: 0.015 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Symbol: ${r.symbol}   Period: ${r.period || 'N/A'}`);
  console.log(`   Return:       ${pct(r.return_pct)}`);
  console.log(`   Sharpe:       ${r.sharpe?.toFixed(2) ?? 'N/A'}`);
  console.log(`   Max Drawdown: ${pct(r.max_drawdown)}`);
  console.log(`   Win Rate:     ${pct(r.win_rate)}`);
  console.log(`   Trades:       ${r.n_trades}`);
  if (r.avg_trade_pct) console.log(`   Avg Trade:    ${pct(r.avg_trade_pct)}`);
}

async function runPortfolio() {
  banner('Portfolio Backtest — محفظة متعددة الأسهم');
  console.log('  ⏳ يعالج محفظة متعددة الأسهم...');
  const r = await pythonBTPortfolio({ max_weight: 0.15, min_prob: 0.65, commission: 0.015 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Symbols tested: ${r.n_symbols}   Avg positions: ${r.avg_positions}`);
  console.log(`   Portfolio Return:  ${pct(r.portfolio_return)}`);
  console.log(`   Sharpe:           ${r.sharpe?.toFixed(2) ?? 'N/A'}`);
  console.log(`   Max Drawdown:     ${pct(r.max_drawdown)}`);
  console.log(`   Avg Correlation:  ${r.avg_correlation?.toFixed(2) ?? 'N/A'}`);
  if (r.symbol_results?.length) {
    console.log('\n   Top Symbol Results:');
    r.symbol_results.slice(0, 5).forEach(s =>
      console.log(`     ${String(s.symbol).padEnd(10)} ret=${pct(s.return_pct)} trades=${s.n_trades}`));
  }
}

async function runWalkForward() {
  banner('Walk-Forward Backtesting — مع تكاليف تنفيذ واقعية');
  console.log('  ⏳ IS=12m / OOS=3m / step=1m...');
  const r = await pythonBTWalkForward({ commission: 0.015, slippage: 0.005 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Windows: ${r.n_windows}   Stability: ${r.stability_pct?.toFixed(1)}%`);
  console.log(`   Avg OOS Return: ${pct(r.avg_oos_return)}`);
  console.log(`   Avg OOS Sharpe: ${r.avg_oos_sharpe?.toFixed(2) ?? 'N/A'}`);
  if (r.windows?.length) {
    console.log('\n   Per-Window OOS:');
    r.windows.forEach((w,i) => console.log(`     W${i+1} ${w.oos_start}→${w.oos_end}  ret=${pct(w.oos_return)}  trades=${w.n_trades}`));
  }
}

async function runCost() {
  banner('Execution Cost Model — نموذج تكاليف التنفيذ في EGX');
  const r = await pythonBTExecCost({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  // Actual response nests aggregate stats under r.aggregate
  const agg = r.aggregate || r;
  console.log(`\n   Symbols analyzed:     ${r.n_symbols ?? 'N/A'}`);
  console.log(`   Avg Bid-Ask Spread:   ${agg.avg_spread_pct != null ? agg.avg_spread_pct.toFixed(3)+'%' : 'N/A'}`);
  console.log(`   Avg Market Impact:    ${agg.avg_market_impact_pct != null ? agg.avg_market_impact_pct.toFixed(3)+'%' : 'N/A'}`);
  console.log(`   Settlement Cost T+2:  ${agg.settlement_cost_pct != null ? agg.settlement_cost_pct.toFixed(4)+'%' : 'N/A'}`);
  console.log(`   Total Roundtrip Cost: ${agg.avg_roundtrip_pct != null ? agg.avg_roundtrip_pct.toFixed(3)+'%' : 'N/A'}`);
  if (agg.market_impact_model) console.log(`   Market Impact Model:  ${agg.market_impact_model}`);
  // Show cheapest 3 and most expensive 3 symbols
  if (r.per_symbol && typeof r.per_symbol === 'object') {
    const sorted = Object.entries(r.per_symbol).sort(([,a],[,b]) => a.total_roundtrip_pct - b.total_roundtrip_pct);
    console.log('\n   ✅ Cheapest (lowest cost):');
    sorted.slice(0, 3).forEach(([sym, s]) =>
      console.log(`     ${String(sym).padEnd(10)} roundtrip=${s.total_roundtrip_pct?.toFixed(2)}%  vol=${s.avg_daily_vol_egp ? (s.avg_daily_vol_egp/1e6).toFixed(0)+'M EGP' : 'N/A'}`));
    console.log('\n   ⚠️  Most Expensive (thin liquidity):');
    sorted.slice(-3).reverse().forEach(([sym, s]) =>
      console.log(`     ${String(sym).padEnd(10)} roundtrip=${s.total_roundtrip_pct?.toFixed(2)}%  vol=${s.avg_daily_vol_egp ? (s.avg_daily_vol_egp/1e6).toFixed(0)+'M EGP' : 'N/A'}`));
  }
}

async function runReport() {
  await runCost();
  await runPortfolio();
}

const SECTIONS = { strategy: runStrategy, portfolio: runPortfolio,
                   walkforward: runWalkForward, cost: runCost, report: runReport };
const fn = SECTIONS[section];
if (!fn) { console.error(`❌ Unknown: ${section}  Available: ${Object.keys(SECTIONS).join(', ')}`); process.exit(1); }
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
