#!/usr/bin/env node
/**
 * Phase 54 — Corporate Actions Tracker runner
 * "الأحداث الشركاتية — Protect Backtest Integrity"
 *
 * Sections: scan | list | impact | warning | full
 *   --symbol COMI
 */
import { pythonCorpScanSymbol, pythonCorpScanAll, pythonCorpListEvents,
         pythonCorpImpact, pythonCorpWarning, pythonCorpBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'list';
const symbol  = args[args.indexOf('--symbol') + 1] ?? null;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🏢 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const TYPE_EMOJI = { DIVIDEND: '💰', RIGHTS_ISSUE: '📜', SPLIT: '✂️', CAPITAL_INC: '💹', BONUS_SHARES: '🎁', SUSPICIOUS: '⚠️', UNKNOWN: '❓' };

switch (section) {
  case 'scan': {
    const target = symbol ?? 'all';
    banner(`Corp Actions: Scanning — ${target}`);
    const r = symbol
      ? await pythonCorpScanSymbol({ symbol })
      : await pythonCorpScanAll({});
    if (r?.n_detected !== undefined || r?.total_detected !== undefined) {
      const n = r.n_detected ?? r.total_detected;
      console.log(`\n   Detected: ${n} events`);
      if (r.by_type) Object.entries(r.by_type).forEach(([t, n]) =>
        console.log(`   ${TYPE_EMOJI[t] ?? '?'} ${String(t).padEnd(14)} ${n}`));
      if (r.events?.length) {
        console.log('\n   Events:');
        r.events.slice(0, 10).forEach(e =>
          console.log(`   ${TYPE_EMOJI[e.event_type] ?? '?'} ${e.symbol}  ${e.event_date}  gap:${e.gap_pct?.toFixed(1)}%  vol:${e.volume_multiple?.toFixed(1)}x  conf:${(e.confidence*100)?.toFixed(0)}%`));
      }
    } else pp(r);
    break;
  }
  case 'list': {
    banner(`Corp Actions: Event List${symbol ? ' — '+symbol : ''}`);
    const r = await pythonCorpListEvents({ symbol });
    if (r?.events !== undefined) {
      if (!r.events?.length) { console.log('\n   ✅ No events found'); break; }
      console.log(`\n   ${r.events.length} events\n`);
      console.log('   Type            Symbol  Date         Gap%   Vol×  Conf  Confirmed');
      console.log('   ' + '─'.repeat(65));
      r.events.slice(0, 20).forEach(e =>
        console.log(`   ${TYPE_EMOJI[e.event_type]??'?'} ${String(e.event_type).padEnd(14)} ${String(e.symbol).padEnd(7)} ${e.event_date}  ${String(e.gap_pct?.toFixed(1)+'%').padStart(6)} ${String(e.volume_multiple?.toFixed(1)+'x').padStart(5)} ${String((e.confidence*100)?.toFixed(0)+'%').padStart(5)}  ${e.is_confirmed ? '✅' : '—'}`));
    } else pp(r);
    break;
  }
  case 'impact': {
    banner('Corp Actions: Impact on Law Discovery');
    const r = await pythonCorpImpact({});
    if (r?.n_events_365d !== undefined) {
      console.log(`\n   Events last 365 days: ${r.n_events_365d}`);
      console.log(`   Potentially contaminated laws: ${r.contaminated_laws_estimate}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'warning': {
    banner('Corp Actions: Unadjusted Data Warning');
    const r = await pythonCorpWarning({});
    if (r?.n_unadjusted !== undefined) {
      const em = r.severity === 'HIGH' ? '🔴' : r.severity === 'MEDIUM' ? '🟡' : '🟢';
      console.log(`\n   ${em} Unadjusted confirmed events: ${r.n_unadjusted}`);
      console.log(`   Affected symbols: ${r.affected_symbols?.length ?? 0}`);
      if (r.affected_symbols?.length)
        console.log(`   Symbols: ${r.affected_symbols.slice(0,10).join(', ')}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Corp Actions: Full Scan + Impact');
    const r = await pythonCorpBuildFull({});
    if (r?.total_detected !== undefined) {
      console.log(`\n   Total detected: ${r.total_detected}`);
      console.log(`   Contaminated laws est: ${r.contaminated_laws_estimate}`);
      console.log(`   Unadjusted: ${r.n_unadjusted}`);
      if (r.by_type) Object.entries(r.by_type).forEach(([t, n]) =>
        console.log(`   ${TYPE_EMOJI[t]??'?'} ${t}: ${n}`));
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
