#!/usr/bin/env node
/**
 * Phase 62 — Feature Factory runner
 * "مصنع الخصائص — 300+ مؤشر مشتق من البيانات الخام"
 *
 * Sections: build | get | importance | coverage | full
 *   --symbol COMI
 *   --date 2026-05-15
 */
import { pythonFeatBuildFeatures, pythonFeatGetFeatures, pythonFeatImportance,
         pythonFeatCoverage, pythonFeatBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'coverage';
const symIdx  = args.indexOf('--symbol');
const symbol  = symIdx !== -1 ? args[symIdx + 1] : null;
const dateIdx = args.indexOf('--date');
const date    = dateIdx !== -1 ? args[dateIdx + 1] : new Date().toISOString().split('T')[0];
const nIdx    = args.indexOf('--n');
const n       = nIdx !== -1 ? parseInt(args[nIdx + 1]) : 20;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'build': {
    banner(`Build Feature Matrix — ${date}`);
    const sym = symbol ?? null;
    const r = await pythonFeatBuildFeatures(sym ? { symbol: sym, date } : { date });
    if (r?.n_built !== undefined || r?.n_symbols !== undefined) {
      console.log(`\n   Built:    ${r.n_built ?? r.n_symbols ?? 0} symbol-date rows`);
      console.log(`   Features: ${r.n_features ?? '?'}`);
      if (r.elapsed_sec) console.log(`   Time:     ${r.elapsed_sec?.toFixed(1)}s`);
      if (r.sample) {
        console.log('\n   Sample features:');
        Object.entries(r.sample).slice(0, 12).forEach(([k, v]) =>
          console.log(`     ${String(k).padEnd(25)} ${typeof v === 'number' ? v?.toFixed(4) : v}`));
      }
    } else pp(r);
    break;
  }
  case 'get': {
    if (!symbol) { console.log('Error: --symbol required for get section'); process.exit(1); }
    banner(`Features — ${symbol} @ ${date}`);
    const r = await pythonFeatGetFeatures({ symbol, date });
    if (r?.features) {
      console.log(`\n   ${symbol} on ${date}:\n`);
      const feats = r.features;
      const groups = {
        'RSI':    Object.entries(feats).filter(([k]) => k.startsWith('rsi')),
        'Volume': Object.entries(feats).filter(([k]) => k.startsWith('vol')),
        'MACD':   Object.entries(feats).filter(([k]) => k.startsWith('macd')),
        'EMA':    Object.entries(feats).filter(([k]) => k.startsWith('ema')),
        'BB':     Object.entries(feats).filter(([k]) => k.startsWith('bb')),
        'Other':  Object.entries(feats).filter(([k]) =>
          !k.startsWith('rsi') && !k.startsWith('vol') && !k.startsWith('macd') &&
          !k.startsWith('ema') && !k.startsWith('bb')),
      };
      for (const [grp, pairs] of Object.entries(groups)) {
        if (!pairs.length) continue;
        console.log(`   ── ${grp} ──`);
        pairs.slice(0, 10).forEach(([k, v]) =>
          console.log(`     ${String(k).padEnd(28)} ${typeof v === 'number' ? v?.toFixed(4) : v ?? 'n/a'}`));
      }
    } else pp(r);
    break;
  }
  case 'importance': {
    banner(`Feature Importance`);
    const r = await pythonFeatImportance({ top_n: n });
    const top = r?.top_features ?? r?.features ?? [];
    if (top.length) {
      console.log(`\n   Top ${top.length} features by importance:\n`);
      console.log('   Rank  Feature                         Importance');
      console.log('   ' + '─'.repeat(52));
      top.slice(0, 25).forEach((f, i) => {
        const name = typeof f === 'string' ? f : f.feature ?? f.name ?? '?';
        const imp  = typeof f === 'object' ? (f.importance ?? f.score ?? 0) : 0;
        const bar  = '█'.repeat(Math.round((imp / (top[0]?.importance ?? 1)) * 20));
        console.log(`   ${String(i+1).padStart(4)}. ${String(name).padEnd(30)} ${String(imp?.toFixed(4)).padStart(8)}  ${bar}`);
      });
    } else pp(r);
    break;
  }
  case 'coverage': {
    banner(`Feature Matrix Coverage — ${date}`);
    const r = await pythonFeatCoverage({ date });
    if (r?.n_symbols !== undefined || r?.n_dates !== undefined) {
      console.log(`\n   Symbols covered: ${r.n_symbols ?? 0}`);
      console.log(`   Date range:      ${r.oldest_date ?? 'n/a'} → ${r.newest_date ?? 'n/a'}`);
      console.log(`   Features/row:    ${r.n_features ?? '?'}`);
      console.log(`   Total rows:      ${r.total_rows ?? 0}`);
      if (r.completeness_pct != null)
        console.log(`   Completeness:    ${r.completeness_pct?.toFixed(1)}%`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Feature Factory Full — ${date}`);
    const r = await pythonFeatBuildFull({ date });
    if (r?.coverage !== undefined || r?.n_built !== undefined) {
      console.log(`\n   Built:    ${r.n_built ?? 0} rows`);
      console.log(`   Features: ${r.n_features ?? '?'}`);
      if (r.top_features?.length) {
        console.log(`\n   Top predictive features:`);
        r.top_features.slice(0, 8).forEach((f, i) => {
          const name = typeof f === 'string' ? f : f.feature ?? f.name ?? '?';
          const imp  = typeof f === 'object' ? (f.importance ?? 0) : '';
          console.log(`     ${i+1}. ${name}${imp ? `  (${imp?.toFixed(4)})` : ''}`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: build|get|importance|coverage|full`); process.exit(1);
}
