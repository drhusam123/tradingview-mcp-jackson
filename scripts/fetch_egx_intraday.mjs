#!/usr/bin/env node
/**
 * Phase 50 — EGX Intraday Data Fetcher
 * ======================================
 * يجلب بيانات 60min + 15min لكل سهم في الكون المصري
 * يُشغَّل يومياً بعد إغلاق الجلسة (14:30 بتوقيت القاهرة)
 *
 * Usage:
 *   node scripts/fetch_egx_intraday.mjs            -- جلب الكل (60min + 15min)
 *   node scripts/fetch_egx_intraday.mjs --60only   -- 60min فقط
 *   node scripts/fetch_egx_intraday.mjs --15only   -- 15min فقط
 *   node scripts/fetch_egx_intraday.mjs --symbol COMI
 *   node scripts/fetch_egx_intraday.mjs --resume
 *
 * Note: 60min = ~3 months of history (500 bars ÷ 4.5h/day ÷ 5d/week)
 *       15min = ~25 trading days (500 bars ÷ 18bars/day)
 */

import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv }                from '../src/core/data.js';
import { getDB, saveOHLCVTimeframe,
         getTimeframeCoverage, EGX_UNIVERSE, EGX_UNIVERSE_CORE } from '../src/egx/index.js';
import { loadLiquidTierSymbols } from './lib/liquid_tier.mjs';
import { withTvRetry } from './lib/tv_fetch_retry.mjs';
import { waitForChartReady }       from '../src/wait.js';
import { toTvSymbol }              from '../src/egx/tv_symbols.js';

const args     = process.argv.slice(2);
const DO_60    = !args.includes('--15only');
const DO_15    = !args.includes('--60only');
const RESUME   = args.includes('--resume');
const CORE_ONLY = args.includes('--core-only');
const TIER_LIQUID = args.includes('--tier') && args[args.indexOf('--tier') + 1] === 'liquid'
  || args.includes('--tier-liquid');
const MAX_SYMBOLS = (() => {
  const i = args.indexOf('--max-symbols');
  return i >= 0 ? Math.max(1, parseInt(args[i + 1] || '0', 10)) : null;
})();
const ROTATION_OFFSET = (() => {
  const i = args.indexOf('--offset');
  return i >= 0 ? Math.max(0, parseInt(args[i + 1] || '0', 10)) : 0;
})();
const SINGLE   = (() => { const i = args.indexOf('--symbol'); return i >= 0 ? args[i+1] : null; })();
const DELAY_MS = process.env.DELAY_MS ? +process.env.DELAY_MS : 2000;
const MAX_BARS = 500;

// Core liquid symbols — must exist in EGX_UNIVERSE (delisted symbols removed)
const LIQUID_FIRST = EGX_UNIVERSE_CORE.filter(s => EGX_UNIVERSE.includes(s));

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function bar(pct, w=26) { const f=Math.round(pct/100*w); return '[' + '█'.repeat(f) + '░'.repeat(w-f) + ']'; }

const dbForTier = getDB();
let ALL_RAW = SINGLE
  ? [SINGLE]
  : CORE_ONLY
    ? [...new Set(LIQUID_FIRST)]
    : TIER_LIQUID
      ? loadLiquidTierSymbols(dbForTier, { limit: 80, offset: 0 })
      : [...new Set(EGX_UNIVERSE)];

// Liquid tier + resume: prioritize symbols missing intraday (skip already-fetched core)
if (TIER_LIQUID && RESUME && !SINGLE) {
  const done60pre = new Set(
    dbForTier.prepare('SELECT DISTINCT symbol FROM ohlcv_60min').all().map(r => r.symbol),
  );
  const done15pre = new Set(
    dbForTier.prepare('SELECT DISTINCT symbol FROM ohlcv_15min').all().map(r => r.symbol),
  );
  const missing = ALL_RAW.filter(s =>
    (DO_60 && !done60pre.has(s)) || (DO_15 && !done15pre.has(s)),
  );
  const have = ALL_RAW.filter(s => !missing.includes(s));
  const cap = MAX_SYMBOLS ?? 20;
  ALL_RAW = [...missing, ...have].slice(ROTATION_OFFSET, ROTATION_OFFSET + cap);
}

const ALL_SYMBOLS = [
  ...LIQUID_FIRST.filter(s => ALL_RAW.includes(s)),
  ...ALL_RAW.filter(s => !LIQUID_FIRST.includes(s)),
];

async function fetchTF(symbol, tf, tableName) {
  const tvSymbol = toTvSymbol(symbol);
  return withTvRetry(async () => {
    await setSymbol({ symbol: tvSymbol });
    await setTimeframe({ timeframe: tf });
    const ready = await waitForChartReady(tvSymbol, null, 8000);
    if (!ready) await sleep(1500);
    const data = await getOhlcv({ count: MAX_BARS });
    if (!data?.bars?.length) throw new Error('Could not extract OHLCV data. The chart may still be loading.');
    return saveOHLCVTimeframe(tableName, symbol, data.bars);
  }, { label: `${symbol}:${tf}` });
}

async function main() {
  // Ensure tables exist
  try {
    const { initPhase49to55Schema } = await import('../src/egx/index.js');
    initPhase49to55Schema();
  } catch { /* already initialized */ }

  const db = getDB();

  const done60 = new Set(RESUME
    ? db.prepare("SELECT DISTINCT symbol FROM ohlcv_60min").all().map(r => r.symbol)
    : []);
  const done15 = new Set(RESUME
    ? db.prepare("SELECT DISTINCT symbol FROM ohlcv_15min").all().map(r => r.symbol)
    : []);

  const targets = ALL_SYMBOLS.filter(s =>
    (DO_60 && !done60.has(s)) || (DO_15 && !done15.has(s)));

  process.stdout.write(`
╔════════════════════════════════════════════════════════════════╗
║         EGX Intraday Data Fetcher — Phase 50                   ║
╠════════════════════════════════════════════════════════════════╣
║  Symbols : ${String(targets.length).padEnd(4)} of ${String(ALL_SYMBOLS.length).padEnd(4)} (liquid-first order)           ║
║  60min   : ${DO_60 ? '✅ YES' : '❌ NO '}  ~3 months intraday history            ║
║  15min   : ${DO_15 ? '✅ YES' : '❌ NO '}  ~25 trading days                       ║
║  Resume  : ${RESUME ? '✅ YES' : '❌ NO '}                                          ║
╚════════════════════════════════════════════════════════════════╝
`);

  const res = { '60': { ok: 0, err: 0, bars: 0 }, '15': { ok: 0, err: 0, bars: 0 } };
  const t0 = Date.now();

  for (let i = 0; i < targets.length; i++) {
    const sym = targets[i];
    const pct = Math.round(i / targets.length * 100);
    const eta = i > 2 ? Math.round((Date.now()-t0)/i*(targets.length-i)/60000) : '?';
    process.stdout.write(`\r  ${bar(pct)} ${String(i+1).padStart(3)}/${targets.length}  ${String(sym).padEnd(8)} ETA:${eta}m  `);

    if (DO_60 && !done60.has(sym)) {
      try {
        const n = await fetchTF(sym, '60', 'ohlcv_60min');
        res['60'].ok++; res['60'].bars += n;
      } catch (e) {
        res['60'].err++;
        process.stderr.write(`\n  ⚠️  60min ${sym}: ${e.message}\n`);
      }
      await sleep(DELAY_MS);
    }

    if (DO_15 && !done15.has(sym)) {
      try {
        const n = await fetchTF(sym, '15', 'ohlcv_15min');
        res['15'].ok++; res['15'].bars += n;
      } catch (e) {
        res['15'].err++;
        process.stderr.write(`\n  ⚠️  15min ${sym}: ${e.message}\n`);
      }
      await sleep(DELAY_MS);
    }
  }

  // Restore chart
  await setTimeframe({ timeframe: 'D' }).catch(() => {});

  // Final stats
  const cov60 = db.prepare("SELECT COUNT(DISTINCT symbol) as s, COUNT(*) as b FROM ohlcv_60min").get();
  const cov15 = db.prepare("SELECT COUNT(DISTINCT symbol) as s, COUNT(*) as b FROM ohlcv_15min").get();

  process.stdout.write(`

╔════════════════════════════════════════════════════════════════╗
║                      FINAL REPORT                              ║
╠════════════════════════════════════════════════════════════════╣
║  60min : ${String(cov60?.s??0).padEnd(3)} symbols  ${String(cov60?.b??0).padEnd(7)} bars  ${String(res['60'].err).padEnd(2)} errors  ║
║  15min : ${String(cov15?.s??0).padEnd(3)} symbols  ${String(cov15?.b??0).padEnd(7)} bars  ${String(res['15'].err).padEnd(2)} errors  ║
║  Time  : ${Math.round((Date.now()-t0)/60000)} min                                        ║
╚════════════════════════════════════════════════════════════════╝
`);
  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
