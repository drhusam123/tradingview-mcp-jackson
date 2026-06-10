#!/usr/bin/env node
/**
 * Phase 61 — Intraday Monitor runner
 * "مراقبة السوق اللحظية — أسعار + دفتر الأوامر + توقيت التنفيذ"
 *
 * Sections: status | dom | quotes | timing | spread | snapshot | full
 *   --symbol COMI
 *   --top-n 20
 */
import { pythonMonitorSessionStatus, pythonMonitorSaveDom, pythonMonitorSaveQuotes,
         pythonMonitorExecution, pythonMonitorSpread, pythonMonitorLiveSnapshot,
         pythonMonitorBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'status';
const symIdx  = args.indexOf('--symbol');
const symbol  = symIdx  !== -1 ? args[symIdx  + 1] : null;
const tnIdx   = args.indexOf('--top-n');
const topN    = tnIdx   !== -1 ? parseInt(args[tnIdx + 1]) : 20;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📡 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const PHASE_EMOJI = {
  OPENING_AUCTION: '🔔', CONTINUOUS: '🟢', CLOSING_AUCTION: '🔶',
  PRE_MARKET: '🌅', CLOSED: '🌙', UNKNOWN: '❓',
};

switch (section) {
  case 'status': {
    banner('EGX Session Status');
    const r = await pythonMonitorSessionStatus({});
    const em = PHASE_EMOJI[r.session_phase] ?? '❓';
    console.log(`\n   ${em} Session Phase:  ${r.session_phase}`);
    console.log(`   Cairo Time:      ${r.cairo_time}`);
    console.log(`   Trading Day:     ${r.is_trading_day ? 'Yes ✅' : 'No ❌'}`);
    if (r.minutes_to_open  != null) console.log(`   Opens in:        ${r.minutes_to_open} min`);
    if (r.minutes_to_close != null) console.log(`   Closes in:       ${r.minutes_to_close} min`);
    console.log(`   Optimal Exec:    ${r.optimal_execution ?? 'n/a'}`);
    if (r.note) console.log(`\n   ℹ️  ${r.note}`);
    break;
  }
  case 'dom': {
    if (!symbol) { console.log('Error: --symbol required for dom section'); process.exit(1); }
    banner(`DOM Snapshot — ${symbol}`);
    const r = await pythonMonitorSpread({ symbol });
    if (r?.spread_bps !== undefined || r?.best_bid !== undefined) {
      const imbalPct = (r.imbalance_ratio * 100)?.toFixed(1);
      const imbalEm  = r.imbalance_ratio >= 0.6 ? '🟢 BUY pressure' : r.imbalance_ratio <= 0.4 ? '🔴 SELL pressure' : '⚖️ Balanced';
      console.log(`\n   Symbol:          ${symbol}`);
      console.log(`   Best Bid:        ${r.best_bid?.toFixed(2) ?? 'n/a'}`);
      console.log(`   Best Ask:        ${r.best_ask?.toFixed(2) ?? 'n/a'}`);
      console.log(`   Spread:          ${r.spread_bps?.toFixed(1)} bps  (${r.spread_pct?.toFixed(3)}%)`);
      console.log(`   Imbalance:       ${imbalPct}% — ${imbalEm}`);
      console.log(`   Bid depth:       ${r.total_bid_vol?.toFixed(0) ?? 'n/a'}`);
      console.log(`   Ask depth:       ${r.total_ask_vol?.toFixed(0) ?? 'n/a'}`);
    } else pp(r);
    break;
  }
  case 'quotes': {
    banner(`Live Quotes Snapshot — Top ${topN}`);
    const r = await pythonMonitorLiveSnapshot({ top_n: topN });
    if (r?.top_movers_up?.length || r?.top_movers_down?.length) {
      if (r.top_movers_up?.length) {
        console.log(`\n   🟢 Top Gainers:\n`);
        console.log('   Symbol    Price     Chg%     Volume');
        console.log('   ' + '─'.repeat(45));
        r.top_movers_up.slice(0, 10).forEach(q => {
          console.log(`   📈 ${String(q.symbol).padEnd(8)} ${String(q.price?.toFixed(2)).padStart(8)}  ${String(('+'+q.change_pct?.toFixed(1)+'%')).padStart(7)}   ${q.volume?.toLocaleString() ?? 'n/a'}`);
        });
      }
      if (r.top_movers_down?.length) {
        console.log(`\n   🔴 Top Losers:\n`);
        console.log('   Symbol    Price     Chg%     Volume');
        console.log('   ' + '─'.repeat(45));
        r.top_movers_down.slice(0, 10).forEach(q => {
          console.log(`   📉 ${String(q.symbol).padEnd(8)} ${String(q.price?.toFixed(2)).padStart(8)}  ${String((q.change_pct?.toFixed(1)+'%')).padStart(7)}   ${q.volume?.toLocaleString() ?? 'n/a'}`);
        });
      }
      if (r.n_total_quotes !== undefined)
        console.log(`\n   Total live quotes: ${r.n_total_quotes}  |  Fetched at: ${r.fetched_at ?? 'n/a'}`);
    } else {
      console.log('\n   No live quotes yet. Run: npm run egx:fetch:live --quotes');
    }
    break;
  }
  case 'timing': {
    banner('Execution Timing Windows — EGX');
    const r = await pythonMonitorSessionStatus({});
    const em = PHASE_EMOJI[r.session_phase] ?? '❓';
    console.log(`\n   ${em} Current Phase: ${r.session_phase}`);
    console.log(`   Cairo Time:     ${r.cairo_time}`);
    console.log(`   Optimal Exec:   ${r.optimal_execution ?? 'n/a'}`);
    console.log('\n   EGX Session Schedule (Cairo Time):');
    console.log('   ' + '─'.repeat(55));
    console.log(`   🌅  Pre-Market:       08:30 – 10:00   Prepare watchlist`);
    console.log(`   🔔  Opening Auction:  10:00 – 10:30   Monitor order book`);
    console.log(`   🟢  Continuous:       10:30 – 14:00   ✅ Optimal execution`);
    console.log(`   🔶  Closing Auction:  14:00 – 14:30   Reduce position size`);
    console.log(`   🌙  After Close:      14:30+          Analysis & planning`);
    if (r.minutes_to_open  != null) console.log(`\n   ⏰ Opens in: ${r.minutes_to_open} min`);
    if (r.minutes_to_close != null) console.log(`   ⏰ Closes in: ${r.minutes_to_close} min`);
    if (r.note) console.log(`\n   ℹ️  ${r.note}`);
    // If symbol provided, also get DOM execution data
    if (symbol) {
      const exec = await pythonMonitorExecution({ symbol }).catch(() => null);
      if (exec && !exec.error) {
        console.log(`\n   ${symbol} Execution Stats:`);
        if (exec.avg_spread_bps != null) console.log(`   Avg spread:  ${exec.avg_spread_bps?.toFixed(1)} bps`);
        if (exec.avg_imbalance  != null) console.log(`   Avg imbalance: ${(exec.avg_imbalance*100)?.toFixed(1)}%`);
      }
    }
    break;
  }
  case 'spread': {
    banner('Market Spread Analysis');
    const sym = symbol ?? null;
    const r = await pythonMonitorSpread(sym ? { symbol: sym } : {});
    if (Array.isArray(r?.spreads)) {
      console.log(`\n   ${r.spreads.length} symbol(s) with spread data:\n`);
      console.log('   Symbol    Bid       Ask       Spread_bps  Imbalance%');
      console.log('   ' + '─'.repeat(60));
      r.spreads.slice(0, 20).forEach(s => {
        const iEm = s.imbalance_ratio >= 0.6 ? '🟢' : s.imbalance_ratio <= 0.4 ? '🔴' : '⚖️';
        console.log(`   ${iEm} ${String(s.symbol).padEnd(8)} ${String(s.best_bid?.toFixed(2)).padStart(8)}  ${String(s.best_ask?.toFixed(2)).padStart(8)}  ${String(s.spread_bps?.toFixed(1)).padStart(10)}   ${(s.imbalance_ratio*100)?.toFixed(1)}%`);
      });
    } else if (r?.spread_bps !== undefined) {
      // Single symbol
      const iEm = r.imbalance_ratio >= 0.6 ? '🟢' : r.imbalance_ratio <= 0.4 ? '🔴' : '⚖️';
      console.log(`\n   ${sym ?? '?'}:`);
      console.log(`   Spread: ${r.spread_bps?.toFixed(1)} bps  |  Imbalance: ${iEm} ${(r.imbalance_ratio*100)?.toFixed(1)}%`);
    } else {
      console.log('\n   No DOM snapshots yet. Run: npm run egx:fetch:live --dom');
    }
    break;
  }
  case 'snapshot': {
    banner(`Live Snapshot — Top ${topN}`);
    const r = await pythonMonitorLiveSnapshot({ top_n: topN });
    if (r) {
      const session = r.session_phase ? `${PHASE_EMOJI[r.session_phase] ?? ''} ${r.session_phase}` : '';
      if (session) console.log(`\n   ${session}`);
      if (r.n_total_quotes) console.log(`   Live quotes: ${r.n_total_quotes}  |  ${r.fetched_at ?? ''}`);
      if (r.market_summary) {
        const ms = r.market_summary;
        console.log(`\n   Market summary:`);
        if (ms.n_up)   console.log(`   Advancing:  ${ms.n_up}`);
        if (ms.n_down) console.log(`   Declining:  ${ms.n_down}`);
        if (ms.n_unch) console.log(`   Unchanged:  ${ms.n_unch}`);
        if (ms.avg_change_pct != null) console.log(`   Avg change: ${ms.avg_change_pct?.toFixed(2)}%`);
      }
      if (r.top_movers_up?.length) {
        console.log(`\n   🟢 ${r.top_movers_up.slice(0,5).map(q => `${q.symbol}(+${q.change_pct?.toFixed(1)}%)`).join('  ')}`);
      }
      if (r.top_movers_down?.length) {
        console.log(`   🔴 ${r.top_movers_down.slice(0,5).map(q => `${q.symbol}(${q.change_pct?.toFixed(1)}%)`).join('  ')}`);
      }
      if (r.top_volume?.length) {
        console.log(`   📊 Volume leaders: ${r.top_volume.slice(0,5).map(q => q.symbol).join(', ')}`);
      }
    } else {
      console.log('\n   No snapshot data. Run: npm run egx:fetch:live');
    }
    break;
  }
  case 'full': {
    banner('Intraday Monitor Full Report');
    const r = await pythonMonitorBuildFull({});
    if (r?.session) {
      const sess = r.session;
      const em = PHASE_EMOJI[sess.session_phase] ?? '❓';
      console.log(`\n   ${em} ${sess.session_phase}  |  ${sess.cairo_time}`);
      console.log(`   Trading day: ${sess.is_trading_day ? 'Yes' : 'No'}`);
      console.log(`   Optimal exec: ${sess.optimal_execution}`);
      if (r.live_snapshot) {
        const snap = r.live_snapshot;
        if (snap.n_total_quotes) console.log(`\n   Live quotes: ${snap.n_total_quotes}`);
        if (snap.top_movers_up?.length)
          console.log(`   🟢 Up: ${snap.top_movers_up.slice(0,4).map(q => `${q.symbol}+${q.change_pct?.toFixed(1)}%`).join(' ')}`);
        if (snap.top_movers_down?.length)
          console.log(`   🔴 Dn: ${snap.top_movers_down.slice(0,4).map(q => `${q.symbol}${q.change_pct?.toFixed(1)}%`).join(' ')}`);
      }
      if (r.dom_summary) {
        const dom = r.dom_summary;
        console.log(`\n   DOM snapshots: ${dom.n_symbols ?? 0}`);
        if (dom.avg_spread_bps) console.log(`   Avg spread: ${dom.avg_spread_bps?.toFixed(1)} bps`);
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: status|dom|quotes|timing|spread|snapshot|full`); process.exit(1);
}
