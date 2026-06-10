/**
 * EGX Phase 18 — RL Environment & Walk-Forward Backtesting (standalone runner)
 * ==============================================================================
 * Builds state vectors, runs walk-forward backtests, optimizes decision thresholds.
 *
 * Usage:
 *   node scripts/egx_rl.mjs                          # walk-forward (default)
 *   node scripts/egx_rl.mjs --section state          # build state vector
 *   node scripts/egx_rl.mjs --section backtest        # single backtest
 *   node scripts/egx_rl.mjs --section walkforward     # walk-forward validation
 *   node scripts/egx_rl.mjs --section optimize        # threshold optimization
 *   node scripts/egx_rl.mjs --section report          # performance report
 *   node scripts/egx_rl.mjs --ticker COMI             # focus on specific stock
 */

import {
  pythonRLStateVector, pythonRLBacktest, pythonRLWalkForward,
  pythonRLOptimize, pythonRLReport,
} from '../src/egx/index.js';

const SECTION = (() => {
  const i = process.argv.indexOf('--section');
  return i !== -1 ? process.argv[i + 1] : 'walkforward';
})();

const TICKER = (() => {
  const i = process.argv.indexOf('--ticker');
  return i !== -1 ? process.argv[i + 1] : null;
})();

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));
const pct = v => v != null ? `${(v * 100).toFixed(2)}%` : '?';

sep();
wl('  🤖 EGX RL ENVIRONMENT & WALK-FORWARD ENGINE (Phase 18)');
wl(`  ${new Date().toISOString()} | section: ${SECTION}${TICKER ? ` | ticker: ${TICKER}` : ''}`);
sep();
wl('');

const t0 = Date.now();

async function run() {
  let result;

  switch (SECTION) {
    case 'state': {
      wl('  🧮 Building 40-dim state vector...');
      const params = TICKER ? { ticker: TICKER } : {};
      result = await pythonRLStateVector(params);
      break;
    }
    case 'backtest': {
      wl('  📈 Running single-window backtest...');
      const params = {
        start_date: '2023-01-01',
        end_date:   '2024-12-31',
        ...(TICKER ? { ticker: TICKER } : {}),
      };
      result = await pythonRLBacktest(params);
      break;
    }
    case 'walkforward': {
      wl('  🔄 Walk-forward validation (3 windows)...');
      wl('  Windows: 2021→2022, 2022→2023, 2023→2024-2025');
      wl('  (Estimated: 60–180 seconds)\n');
      result = await pythonRLWalkForward({});
      break;
    }
    case 'optimize': {
      wl('  🎯 Optimizing decision thresholds...');
      result = await pythonRLOptimize({ n_trials: 50 });
      break;
    }
    case 'report': {
      wl('  📋 Generating performance report...');
      result = await pythonRLReport({});
      break;
    }
    default:
      wl(`  ❓ Unknown section: ${SECTION}`);
      process.exit(1);
  }

  if (!result || result.error) {
    wl(`  ❌ RL engine error: ${result?.error ?? 'no result returned'}`);
    process.exit(1);
  }

  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  // ── Display results ─────────────────────────────────────────────────────────
  switch (SECTION) {
    case 'state': {
      wl(`  ✅ State vector built: ${elapsed}s`);
      const vec = result.state_vector ?? {};
      wl(`  📐 Dimensions: ${result.n_dims ?? '?'}`);
      wl(`  🏷️  Components:`);
      for (const [k, v] of Object.entries(vec).slice(0, 15))
        wl(`    ${k.padEnd(30)} = ${typeof v === 'number' ? v.toFixed(4) : v}`);
      break;
    }
    case 'backtest': {
      const perf = result.performance ?? result;
      wl(`  ✅ Backtest complete: ${elapsed}s`);
      wl(`  📊 Period: ${perf.start_date ?? '?'} → ${perf.end_date ?? '?'}`);
      wl(`  💰 Total Return:    ${pct(perf.total_return)}`);
      wl(`  📈 Annualized:      ${pct(perf.annualized_return)}`);
      wl(`  📉 Max Drawdown:    ${pct(perf.max_drawdown)}`);
      wl(`  ⚡ Sharpe Ratio:    ${perf.sharpe_ratio?.toFixed(3) ?? '?'}`);
      wl(`  🎯 Win Rate:        ${pct(perf.win_rate)}`);
      wl(`  📋 Total Trades:    ${perf.total_trades ?? '?'}`);
      break;
    }
    case 'walkforward': {
      const windows = result.windows ?? [];
      wl(`  ✅ Walk-forward complete: ${elapsed}s`);
      wl(`  🪟 Windows validated: ${windows.length}`);
      wl('');
      wl('  WINDOW RESULTS:');
      for (const w of windows) {
        const p = w.performance ?? w;
        wl(`  ┌─ ${w.train_start ?? '?'} → ${w.test_end ?? '?'}`);
        wl(`  │  Return: ${pct(p.total_return)}  Sharpe: ${p.sharpe_ratio?.toFixed(3) ?? '?'}  DD: ${pct(p.max_drawdown)}`);
        wl(`  └─ Trades: ${p.total_trades ?? '?'}  Win%: ${pct(p.win_rate)}`);
      }
      const agg = result.aggregate ?? {};
      if (Object.keys(agg).length) {
        wl('');
        wl('  AGGREGATE PERFORMANCE:');
        wl(`  💰 Avg Return:   ${pct(agg.avg_return)}`);
        wl(`  ⚡ Avg Sharpe:   ${agg.avg_sharpe?.toFixed(3) ?? '?'}`);
        wl(`  📉 Avg MaxDD:    ${pct(agg.avg_max_drawdown)}`);
        wl(`  🎯 Consistency:  ${pct(agg.consistency)}`);
      }
      break;
    }
    case 'optimize': {
      const best = result.best_params ?? {};
      wl(`  ✅ Optimization complete: ${elapsed}s`);
      wl(`  🔬 Trials: ${result.n_trials ?? '?'}`);
      wl(`  🏆 Best Sharpe: ${result.best_sharpe?.toFixed(3) ?? '?'}`);
      wl('  🎛️  Optimal thresholds:');
      for (const [k, v] of Object.entries(best))
        wl(`    ${k.padEnd(30)} = ${typeof v === 'number' ? v.toFixed(4) : v}`);
      break;
    }
    case 'report': {
      wl(`  ✅ Report generated: ${elapsed}s`);
      wl(`  📄 File: ${result.report_file ?? '?'}`);
      const summary = result.summary ?? {};
      if (summary.total_trades != null) {
        wl(`  📊 Lifetime stats:`);
        wl(`    Total trades: ${summary.total_trades}`);
        wl(`    Avg return:   ${pct(summary.avg_return)}`);
        wl(`    Best window:  ${summary.best_window ?? '?'}`);
      }
      break;
    }
  }
}

await run().catch(e => {
  wl(`  ❌ Fatal error: ${e.message}`);
  process.exit(1);
});

wl('');
sep();
wl('  ✅ Phase 18 RL Environment & Walk-Forward Engine complete');
sep();
