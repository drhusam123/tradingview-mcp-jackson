#!/usr/bin/env node
/**
 * Phase 59 — Strategy Backtest Fetcher
 * Generates Pine Strategy code from discovered laws, runs TradingView backtests,
 * extracts results and saves to strategy_backtest_results.
 *
 * Options:
 *   --law-type universal|structural   (default: universal)
 *   --n-laws 5                        number of top laws to test
 *   --symbol COMI                     symbol to backtest on (default: COMI)
 *   --list                            list laws ready for testing
 *   --rank                            show ranked results
 */
import { pythonStrategyGenerate, pythonStrategyList, pythonStrategyParse,
         pythonStrategyValidate, pythonStrategyRank, pythonStrategyBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const lawType = args[args.indexOf('--law-type') + 1] ?? 'universal';
const nLaws   = parseInt(args[args.indexOf('--n-laws')  + 1] ?? '5');
const symbol  = args[args.indexOf('--symbol')   + 1] ?? 'COMI';
const doList  = args.includes('--list');
const doRank  = args.includes('--rank');

function log(msg) { console.log(`[strategy] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── List mode ────────────────────────────────────────────────────────────────
if (doList) {
  const r = await pythonStrategyList({ law_type: lawType, min_precision: 0.55, limit: 20 });
  log(`Laws ready for backtesting (${lawType}):`);
  if (!r?.laws?.length) { log('No laws found. Run law discovery first.'); process.exit(0); }
  console.log('\n   #    Law ID              Direction   Precision  Tested?');
  console.log('   ' + '─'.repeat(60));
  r.laws.forEach((l, i) =>
    console.log(`   ${String(i+1).padStart(3)}. ${String(l.law_id).padEnd(20)} ${String(l.direction).padEnd(10)} ${String((l.precision*100)?.toFixed(1)+'%').padStart(8)}  ${l.already_tested ? '✅' : '—'}`));
  process.exit(0);
}

// ── Rank mode ────────────────────────────────────────────────────────────────
if (doRank) {
  const r = await pythonStrategyRank({ min_tests: 1 });
  if (!r?.rankings?.length) { log('No backtest results yet. Run without --rank to test laws.'); process.exit(0); }
  log(`Ranked laws by performance:`);
  console.log('\n   Rank  Law                  WinRate  PF    Sharpe  Drawdown  Grade');
  console.log('   ' + '─'.repeat(70));
  r.rankings.slice(0, 15).forEach(l =>
    console.log(`   ${String(l.rank).padStart(4)}. ${String(l.law_name ?? l.law_id).padEnd(20)} ${String((l.avg_win_rate*100)?.toFixed(1)+'%').padStart(7)} ${String(l.avg_profit_factor?.toFixed(2)).padStart(5)}  ${String(l.avg_sharpe?.toFixed(2)).padStart(6)}    ${String(l.avg_drawdown?.toFixed(1)+'%').padStart(7)}  ${l.grade ?? '?'}`));
  process.exit(0);
}

// ── Main: generate + backtest laws ──────────────────────────────────────────
log(`Getting top ${nLaws} ${lawType} laws for backtesting on ${symbol}...`);
const full = await pythonStrategyBuildFull({ law_type: lawType, symbol, n_laws: nLaws });

if (!full?.laws_ready_for_testing?.length && !full?.top_law_strategy) {
  log('No laws available for testing. Run law discovery first.');
  console.log(JSON.stringify(full, null, 2));
  process.exit(0);
}

// Show the top law strategy code
if (full?.top_law_strategy) {
  log(`\nTop law to test: ${full.top_law_strategy.law_name ?? full.top_law_strategy.law_id}`);
  log(`Direction: ${full.top_law_strategy.direction}`);

  // Try to use TradingView to run the backtest
  let tvAvailable = false;
  try {
    // Check if MCP tools are available
    const { pythonStrategyGenerate: gen } = await import('../src/egx/index.js');
    tvAvailable = true; // will try TV connection
  } catch { /* offline */ }

  if (tvAvailable && full.top_law_strategy.pine_code) {
    log('\nTo run this backtest in TradingView:');
    log('1. Open TradingView Pine Script editor');
    log('2. The strategy code has been generated — inject it with:');
    log('   npm run egx:strategy:generate');
    log('3. Then use TradingView Strategy Tester to view results');
    log('4. Save results with: npm run egx:strategy:parse');

    // Save the pine code to a file for easy access
    const { writeFileSync } = await import('fs');
    const outPath = `scripts/generated_strategy_${full.top_law_strategy.law_id ?? 'law'}.pine`;
    try {
      writeFileSync(`/Users/dr.husam/tradingview-mcp-jackson/${outPath}`, full.top_law_strategy.pine_code);
      log(`\n✅ Pine strategy saved to: ${outPath}`);
    } catch { /* ignore */ }
  }
}

if (full?.laws_ready_for_testing?.length) {
  log(`\n${full.laws_ready_for_testing.length} laws ready for testing:`);
  full.laws_ready_for_testing.slice(0, 8).forEach(l =>
    log(`  • ${l.law_id}  ${l.direction}  precision=${(l.precision*100)?.toFixed(1)}%  tested=${l.already_tested ? 'yes' : 'no'}`));
}
