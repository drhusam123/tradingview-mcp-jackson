#!/usr/bin/env node
/**
 * Phase 50 — Intraday Intelligence runner
 * "توقيت التنفيذ — When to Enter"
 *
 * Sections: session | coverage | window | gaps | momentum | profiles | full
 */
import { pythonIntradaySession, pythonIntradayCoverage, pythonIntradayWindow,
         pythonIntradayGaps, pythonIntradayMomentum, pythonIntradayBuildProfiles,
         pythonIntradayBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'coverage';
const symbol  = args[args.indexOf('--symbol') + 1] ?? 'COMI';
const date    = args[args.indexOf('--date') + 1]   ?? null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⏱️  ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const BIAS_EMOJI = { ABOVE_VWAP: '📈', BELOW_VWAP: '📉', AT_VWAP: '⚖️' };

switch (section) {
  case 'coverage': {
    banner('Intraday: Data Coverage');
    const r = await pythonIntradayCoverage({});
    if (r?.ohlcv_60min || r?.error) {
      if (r.error) { console.log(`\n   ❌ ${r.error}\n   💡 ${r.hint ?? ''}`); break; }
      const s60 = r.ohlcv_60min ?? {};
      const s15 = r.ohlcv_15min ?? {};
      const sa  = r.intraday_analytics ?? {};
      console.log(`\n   60min:  ${s60.symbols ?? 0} symbols  ${s60.total_bars ?? 0} bars  (${s60.oldest ?? '—'} → ${s60.newest ?? '—'})`);
      console.log(`   15min:  ${s15.symbols ?? 0} symbols  ${s15.total_bars ?? 0} bars  (${s15.oldest ?? '—'} → ${s15.newest ?? '—'})`);
      console.log(`   Analytics: ${sa.symbols ?? 0} symbols  ${sa.total_rows ?? 0} rows`);
    } else pp(r);
    break;
  }
  case 'session': {
    banner(`Intraday: Session Analytics — ${symbol}`);
    const r = await pythonIntradaySession({ symbol, date });
    if (r?.session_bias) {
      const em = BIAS_EMOJI[r.session_bias] ?? '?';
      console.log(`\n   ${em} Session bias: ${r.session_bias}`);
      if (r.vwap) console.log(`   VWAP: ${r.vwap?.toFixed(2)}`);
      if (r.opening_range_high) console.log(`   Opening range: ${r.opening_range_low?.toFixed(2)} — ${r.opening_range_high?.toFixed(2)}`);
      if (r.opening_gap_pct !== undefined) console.log(`   Opening gap: ${r.opening_gap_pct?.toFixed(2)}%`);
      if (r.best_entry_window) console.log(`   Best entry window: ${r.best_entry_window}`);
      if (r.first_hour_direction) console.log(`   First hour: ${r.first_hour_direction}`);
    } else pp(r);
    break;
  }
  case 'window': {
    banner(`Intraday: Optimal Execution Window — ${symbol}`);
    const r = await pythonIntradayWindow({ symbol });
    if (r?.optimal_entry_window) {
      console.log(`\n   🎯 Optimal entry: ${r.optimal_entry_window}`);
      console.log(`   Volume multiple: ${r.avg_volume_multiple?.toFixed(2)}x`);
      console.log(`   Confidence: ${(r.confidence * 100)?.toFixed(0)}%`);
      if (r.avoid_windows?.length) console.log(`   ⚠️  Avoid: ${r.avoid_windows.join(', ')}`);
    } else pp(r);
    break;
  }
  case 'gaps': {
    banner(`Intraday: Opening Gap Analysis — ${symbol}`);
    const r = await pythonIntradayGaps({ symbol });
    if (r?.gap_fill_rate !== undefined) {
      console.log(`\n   Gap fill rate (≥0.5%): ${(r.gap_fill_rate * 100)?.toFixed(0)}%`);
      console.log(`   Up gaps: ${r.gap_up_pct?.toFixed(0)}%  Down: ${r.gap_down_pct?.toFixed(0)}%  Flat: ${r.gap_flat_pct?.toFixed(0)}%`);
      console.log(`   Direction persistence to close: ${(r.direction_persistence * 100)?.toFixed(0)}%`);
    } else pp(r);
    break;
  }
  case 'momentum': {
    banner(`Intraday: Hour-by-Hour Momentum — ${symbol}`);
    const r = await pythonIntradayMomentum({ symbol });
    if (r?.strongest_hour) {
      console.log(`\n   Strongest hour: ${r.strongest_hour}  (avg: ${(r.strongest_return * 100)?.toFixed(2)}%)`);
      console.log(`   Weakest hour:   ${r.weakest_hour}  (avg: ${(r.weakest_return * 100)?.toFixed(2)}%)`);
      if (r.hourly_pattern) {
        console.log('\n   Hour-by-hour average returns:');
        Object.entries(r.hourly_pattern).forEach(([h, v]) => {
          const bar = v >= 0 ? '█'.repeat(Math.min(Math.round(v*2000), 20)) : '░'.repeat(Math.min(Math.round(-v*2000), 20));
          const em = v >= 0 ? '🟢' : '🔴';
          console.log(`   ${em} ${String(h).padEnd(6)} ${v >= 0 ? '+' : ''}${(v*100)?.toFixed(3)}%  ${bar}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'profiles': {
    banner('Intraday: Build Session Profiles');
    const r = await pythonIntradayBuildProfiles({});
    if (r?.n_computed !== undefined) {
      console.log(`\n   ✅ Computed: ${r.n_computed}  ⏭️ Skipped: ${r.n_skipped}  ❌ Errors: ${r.errors?.length ?? 0}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Intraday: Full Build');
    const r = await pythonIntradayBuildFull({});
    if (r?.n_symbols !== undefined) {
      console.log(`\n   ✅ Symbols processed: ${r.n_symbols}`);
      console.log(`   Session bias summary: Bullish:${r.n_bullish ?? 0}  Bearish:${r.n_bearish ?? 0}  Neutral:${r.n_neutral ?? 0}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
