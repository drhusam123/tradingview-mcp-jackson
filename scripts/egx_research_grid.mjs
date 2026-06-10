#!/usr/bin/env node
/**
 * Phase 69 — Research Grid runner
 * "شبكة البحث — اختبار آلاف الفرضيات على البيانات التاريخية"
 *
 * Sections: run | top | status | full
 *   --limit 50
 *   --workers 4
 *   --min-exp 0.5
 *   --hyp-id HYP_XXXXXXXXXX
 */
import { pythonGridRun, pythonGridRunSingle, pythonGridStatus,
         pythonGridTopResults, pythonGridBuildFull, pythonGridVbtBacktest }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'status';
const limIdx  = args.indexOf('--limit');
const limit   = limIdx !== -1 ? parseInt(args[limIdx + 1]) : 50;
const wIdx    = args.indexOf('--workers');
const workers = wIdx !== -1 ? parseInt(args[wIdx + 1]) : 4;
const meIdx   = args.indexOf('--min-exp');
const minExp  = meIdx !== -1 ? parseFloat(args[meIdx + 1]) : 0.0;
const hidIdx  = args.indexOf('--hyp-id');
const hypId   = hidIdx !== -1 ? args[hidIdx + 1] : null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

function expBar(exp) {
  if (exp == null) return '░'.repeat(20);
  const clamped = Math.max(-5, Math.min(5, exp));
  const bars = Math.round((clamped + 5) / 10 * 20);
  return '█'.repeat(bars) + '░'.repeat(20 - bars);
}

switch (section) {
  case 'run': {
    if (hypId) {
      banner(`Test Single Hypothesis — ${hypId}`);
      const r = await pythonGridRunSingle({ hyp_id: hypId });
      const res = r?.result ?? r;
      if (res?.n_activations !== undefined) {
        const exp = res.expectancy_pct ?? 0;
        const em  = exp >= 1.0 ? '🏆' : exp >= 0.5 ? '⭐' : exp >= 0 ? '✅' : '❌';
        console.log(`\n   ${em} ${res.hyp_id ?? hypId}`);
        console.log(`   Activations: ${res.n_activations}  Win%: ${res.win_rate_pct?.toFixed(1)}%`);
        console.log(`   Expectancy:  ${exp?.toFixed(3)}%  [${expBar(exp)}]`);
        console.log(`   OOS Score:   ${res.oos_score?.toFixed(3) ?? 'n/a'}`);
      } else pp(r);
    } else {
      banner(`Research Grid Run — ${limit} hypotheses, ${workers} workers`);
      console.log('\n   Running backtests in parallel (may take 60-120s)...\n');
      const r = await pythonGridRun({ limit, workers });
      if (r?.n_tested !== undefined) {
        console.log(`   Tested:  ${r.n_tested}`);
        console.log(`   Valid:   ${r.n_valid}`);
        console.log(`   Killed:  ${r.n_killed} (insufficient data)`);
        console.log(`   Time:    ${r.elapsed_sec}s`);
        if (r.top_results?.length) {
          console.log(`\n   🏆 Top findings:\n`);
          console.log('   Hyp ID                  Name                      WinR%  Exp%   OOS');
          console.log('   ' + '─'.repeat(72));
          r.top_results.slice(0, 10).forEach(res => {
            const exp = res.expectancy_pct ?? 0;
            const em  = exp >= 1.0 ? '🏆' : exp >= 0.3 ? '✅' : '⚠️';
            console.log(`   ${em} ${String(res.hyp_id).padEnd(24)} ${String(res.hyp_name ?? '?').padEnd(26)} ${String(res.win_rate_pct?.toFixed(1)+'%').padStart(5)}  ${String(exp?.toFixed(3)).padStart(6)}  ${res.oos_score?.toFixed(3) ?? 'n/a'}`);
          });
        }
        if (r.top_hyp_id) {
          console.log(`\n   🎯 Best: ${r.top_hyp_id}  expectancy=+${r.top_expectancy?.toFixed(3)}%`);
          console.log('   Run: npm run egx:alpha:rank  to score and grade all results');
        }
      } else pp(r);
    }
    break;
  }
  case 'top': {
    banner(`Top Research Results — min_exp=${minExp}%`);
    const r = await pythonGridTopResults({ min_expectancy: minExp, min_activations: 10, limit: 20 });
    const results = r?.results ?? [];
    if (results.length) {
      console.log(`\n   ${results.length} result(s) with expectancy ≥ ${minExp}%:\n`);
      console.log('   # Hyp ID                  Exp%    WinR%  Activations  OOS    Dir  Hold');
      console.log('   ' + '─'.repeat(78));
      results.forEach((res, i) => {
        const exp = res.expectancy_pct ?? 0;
        const em  = exp >= 2.0 ? '🏆' : exp >= 1.0 ? '⭐' : exp >= 0.3 ? '✅' : '⚠️';
        console.log(`   ${em}${String(i+1).padStart(2)}. ${String(res.hyp_id).padEnd(24)} ${String(exp?.toFixed(3)+'%').padStart(7)} ${String(res.win_rate_pct?.toFixed(1)+'%').padStart(6)} ${String(res.n_activations).padStart(11)}  ${String(res.oos_score?.toFixed(3) ?? 'n/a').padStart(6)}  ${String(res.direction ?? 'LONG').padEnd(4)} ${res.holding_days}d`);
      });
      results.slice(0, 5).forEach(res => {
        if (res.conditions_summary)
          console.log(`\n   📋 ${res.hyp_id}: ${res.conditions_summary}`);
      });
    } else {
      console.log('\n   No results yet. Run: npm run egx:grid:run');
    }
    break;
  }
  case 'status': {
    banner('Research Grid Status');
    const r = await pythonGridStatus({});
    if (r?.total_hypotheses !== undefined) {
      const pct = r.total_hypotheses > 0
        ? ((r.tested / r.total_hypotheses) * 100)?.toFixed(0) + '%' : '0%';
      const bar = '█'.repeat(Math.round(parseInt(pct)/5)) + '░'.repeat(20-Math.round(parseInt(pct)/5));
      console.log(`\n   Total hypotheses: ${r.total_hypotheses}`);
      console.log(`   Tested:           ${r.tested}  (${pct})`);
      console.log(`   [${bar}] ${pct}`);
      console.log(`   Untested:         ${r.untested}`);
      console.log(`   Active results:   ${r.active}`);
      console.log(`   Killed:           ${r.killed}`);
      if (r.top_5?.length) {
        console.log(`\n   🏆 Top 5 by expectancy:`);
        r.top_5.forEach(res => {
          const em = (res.expectancy_pct ?? 0) >= 1.0 ? '🏆' : '✅';
          console.log(`     ${em} ${String(res.hyp_id).padEnd(26)} exp=${res.expectancy_pct?.toFixed(3)}%  n=${res.n_activations}`);
        });
      }
      if (r.untested > 0)
        console.log(`\n   Run: npm run egx:grid:run -- --limit ${Math.min(r.untested, 100)}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Research Grid Full');
    const r = await pythonGridBuildFull({ limit });
    if (r?.status !== undefined) {
      const st = r.status ?? {};
      console.log(`\n   Hypotheses: ${st.total_hypotheses ?? 0} total, ${st.tested ?? 0} tested`);
      const grid = r.grid ?? {};
      if (grid.n_tested > 0) {
        console.log(`   Just tested: ${grid.n_tested}  valid=${grid.n_valid}`);
        console.log(`   Time: ${grid.elapsed_sec}s`);
      }
      const top = r.top_alpha ?? [];
      if (top.length) {
        console.log('\n   🏆 Top alpha found:');
        top.slice(0, 5).forEach(res => {
          const em = (res.expectancy_pct ?? 0) >= 1.0 ? '🏆' : '✅';
          console.log(`     ${em} ${res.hyp_name ?? res.hyp_id}  exp=${res.expectancy_pct?.toFixed(3)}%  OOS=${res.oos_score?.toFixed(3) ?? 'n/a'}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'vbt': {
    banner(`vectorbt Backtest — ML Signals (prob≥0.70, hold ${5}d)`);
    const r = await pythonGridVbtBacktest({ holding_days: 5, min_prob: 0.7, start_date: '2026-01-01' });
    if (r?.n_signals !== undefined) {
      const em = (r.avg_total_return ?? 0) > 0 ? '🟢' : '🔴';
      console.log(`\n   ${em} vectorbt Portfolio Backtest`);
      console.log(`   Signals tested:  ${r.n_signals}  (${r.n_symbols} symbols)`);
      console.log(`   Trades executed: ${r.n_trades}`);
      console.log(`   Avg return:      ${r.avg_total_return?.toFixed(2)}%`);
      console.log(`   Avg Sharpe:      ${r.avg_sharpe ?? 'n/a'}`);
      console.log(`   Avg Max DD:      ${r.avg_max_drawdown?.toFixed(2)}%`);
      console.log(`   Period:          ${r.period}`);
      if (r.top_symbols?.length) {
        console.log('\n   🏆 Top performers:');
        r.top_symbols.slice(0, 8).forEach(s =>
          console.log(`     ${s.total_return_pct >= 0 ? '📈' : '📉'} ${String(s.symbol).padEnd(8)} ${String(s.total_return_pct+'%').padStart(8)}  (${s.n_trades} trades)`)
        );
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: run|top|status|full|vbt`); process.exit(1);
}
