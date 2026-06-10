#!/usr/bin/env node
/**
 * Phase 53 — Pine Analytics Bridge runner
 * "قوة Pine Script في خدمة النظام"
 *
 * Sections: rs | vwap | volume | events | coverage | full
 *   --symbol COMI --date 2026-05-14
 */
import { pythonPineVolumeProfile, pythonPineRSRanking, pythonPineVWAP,
         pythonPineCorpEvents, pythonPineCoverage, pythonPineBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'rs';
const symbol  = args[args.indexOf('--symbol') + 1] ?? 'COMI';
const date    = args[args.indexOf('--date')   + 1] ?? new Date().toISOString().split('T')[0];

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🌲 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const RS_EMOJI = { RS_LEADER: '🥇', RS_STRONG: '🟢', RS_NEUTRAL: '⚖️', RS_WEAK: '🟡', RS_LAGGARD: '🔴' };

switch (section) {
  case 'rs': {
    banner(`Pine Analytics: RS Ranking — ${date}`);
    const r = await pythonPineRSRanking({ date });
    if (r?.rankings?.length !== undefined) {
      if (!r.rankings?.length) { console.log('\n   ⚠️  No RS data for this date. Run: npm run egx:fetch:pine:rs'); break; }
      console.log(`\n   ${r.rankings.length} stocks ranked  |  Date: ${date}\n`);
      console.log('   # Rank  Symbol     RS Score  Percentile  Class        Trend');
      console.log('   ' + '─'.repeat(60));
      r.rankings.slice(0, 25).forEach((s, i) => {
        const em = RS_EMOJI[s.rs_class] ?? '?';
        const trend = s.trend === 'IMPROVING' ? '↑' : s.trend === 'DETERIORATING' ? '↓' : '→';
        console.log(`   ${String(i+1).padStart(3)}.  ${String(s.symbol).padEnd(10)} ${String(s.rs_score?.toFixed(1)).padStart(7)}   ${String(s.rs_percentile?.toFixed(0)+'%').padStart(9)}  ${em} ${String(s.rs_class).padEnd(12)} ${trend}`);
      });
    } else pp(r);
    break;
  }
  case 'vwap': {
    banner(`Pine Analytics: VWAP Position — ${symbol}`);
    const r = await pythonPineVWAP({ symbol });
    if (r?.vwap_distance_pct !== undefined) {
      const em = r.above_vwap_pct > 50 ? '📈' : '📉';
      console.log(`\n   ${em} VWAP: ${r.current_vwap?.toFixed(2)}`);
      console.log(`   Distance: ${r.vwap_distance_pct?.toFixed(2)}% (${r.vwap_distance_bps?.toFixed(0)} bps)`);
      console.log(`   Above VWAP: ${r.above_vwap_pct?.toFixed(0)}% of recent sessions`);
      console.log(`   VWAP trend: ${r.vwap_trend}`);
    } else pp(r);
    break;
  }
  case 'volume': {
    banner(`Pine Analytics: Volume Profile — ${symbol}`);
    const r = await pythonPineVolumeProfile({ symbol });
    if (r?.poc_level !== undefined) {
      console.log(`\n   POC (Point of Control): ${r.poc_level?.toFixed(2)}`);
      console.log(`   Value Area: ${r.val_level?.toFixed(2)} — ${r.vah_level?.toFixed(2)}`);
      console.log(`   VA width: ${r.va_width_pct?.toFixed(2)}%  (${r.va_trend})`);
      console.log(`   Price vs POC: ${r.price_vs_poc}`);
    } else pp(r);
    break;
  }
  case 'events': {
    banner('Pine Analytics: Corporate Events Scan');
    const r = await pythonPineCorpEvents({});
    if (r?.n_events !== undefined) {
      console.log(`\n   Events detected: ${r.n_events}`);
      if (r.by_type) Object.entries(r.by_type).forEach(([t, n]) => console.log(`   ${t}: ${n}`));
      if (r.most_affected?.length) {
        console.log('\n   Most affected symbols:');
        r.most_affected.slice(0, 10).forEach(s => console.log(`   ⚡ ${s}`));
      }
    } else pp(r);
    break;
  }
  case 'coverage': {
    banner('Pine Analytics: Data Coverage');
    const r = await pythonPineCoverage({});
    if (r?.total_symbols !== undefined) {
      console.log(`\n   Symbols with Pine data: ${r.total_symbols}`);
      console.log(`   Date range: ${r.oldest_date} → ${r.newest_date}`);
      if (r.by_script) {
        console.log('\n   Coverage by script:');
        Object.entries(r.by_script).forEach(([k, v]) =>
          console.log(`   ${String(k).padEnd(25)} ${v} symbols`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Pine Analytics: Full Build');
    const r = await pythonPineBuildFull({});
    if (r?.top_rs_leaders?.length !== undefined) {
      console.log(`\n   RS Leaders today:`);
      (r.top_rs_leaders ?? []).slice(0, 8).forEach(s =>
        console.log(`   🥇 ${String(s.symbol).padEnd(8)} RS:${s.rs_score?.toFixed(1)}  ${s.rs_class}`));
      if (r.corporate_events_today?.length)
        console.log(`\n   ⚡ Corp events today: ${r.corporate_events_today.map(s => s.symbol).join(', ')}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
