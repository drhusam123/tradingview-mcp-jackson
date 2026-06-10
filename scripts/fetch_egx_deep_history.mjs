#!/usr/bin/env node
/**
 * Phase 49 — Deep History Fetcher
 * =================================
 * يجلب Weekly + Monthly OHLCV لكل سهم في الكون المصري
 * ويحفظها في ohlcv_weekly و ohlcv_monthly
 *
 * TradingView has 10+ years of weekly data — we've been leaving it on the table.
 *
 * Usage:
 *   node scripts/fetch_egx_deep_history.mjs            -- جلب الكل
 *   node scripts/fetch_egx_deep_history.mjs --weekly   -- weekly فقط
 *   node scripts/fetch_egx_deep_history.mjs --monthly  -- monthly فقط
 *   node scripts/fetch_egx_deep_history.mjs --symbol COMI
 *   node scripts/fetch_egx_deep_history.mjs --resume   -- تخطّ المكتملة
 */

import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv }                from '../src/core/data.js';
import { getDB, saveOHLCVTimeframe,
         getTimeframeCoverage, EGX_UNIVERSE } from '../src/egx/index.js';
import { waitForChartReady }       from '../src/wait.js';
import { toTvSymbol }              from '../src/egx/tv_symbols.js';

const args     = process.argv.slice(2);
const WEEKLY   = !args.includes('--monthly');   // default: fetch weekly
const MONTHLY  = !args.includes('--weekly');    // default: fetch monthly
const RESUME   = args.includes('--resume');
const SELF_TEST_QUALITY = args.includes('--self-test-quality');
const SINGLE   = (() => { const i = args.indexOf('--symbol'); return i >= 0 ? args[i+1] : null; })();
const MAX_SYMBOLS = (() => {
  const i = args.indexOf('--max-symbols');
  return i >= 0 ? Math.max(1, parseInt(args[i + 1] || '0')) : null;
})();
const DELAY_MS = process.env.DELAY_MS ? +process.env.DELAY_MS : 250;
const READY_TIMEOUT_MS = process.env.READY_TIMEOUT_MS ? +process.env.READY_TIMEOUT_MS : 3000;
const MAX_BARS = 500;

function sleep(ms)        { return new Promise(r => setTimeout(r, ms)); }
function fmtDate(unixSec) { return unixSec ? new Date(unixSec * 1000).toISOString().split('T')[0] : '—'; }
function bar(pct, w=28)   { const f = Math.round(pct/100*w); return '[' + '█'.repeat(f) + '░'.repeat(w-f) + ']'; }

const KNOWN_BAD_OHLCV_FINGERPRINTS = new Set([
  '41.61|42.18|41.61|41.70|83579',
]);

function validateBar(b) {
  if (!b || !b.time) return false;
  const time = Number(b.time);
  const open = Number(b.open);
  const high = Number(b.high);
  const low = Number(b.low);
  const close = Number(b.close);
  const volume = Number(b.volume ?? 0);
  if (![time, open, high, low, close, volume].every(Number.isFinite)) return false;
  if (time <= 0 || open <= 0 || close <= 0 || volume < 0) return false;
  if (high < low || high < open || high < close || low > open || low > close) return false;
  if (volume === 0 && open === close && high === close && low === close) return false;
  return true;
}

function ohlcvFingerprint(b) {
  return [
    Number(b.open).toFixed(2),
    Number(b.high).toFixed(2),
    Number(b.low).toFixed(2),
    Number(b.close).toFixed(2),
    Number(b.volume).toFixed(0),
  ].join('|');
}

function getNewBars(db, tableName, symbol, validBars) {
  const latest = db.prepare(`SELECT MAX(bar_time) AS latest FROM ${tableName} WHERE symbol=?`).get(symbol)?.latest;
  return validBars
    .filter(b => !latest || Number(b.time) > Number(latest))
    .sort((a, b) => Number(a.time) - Number(b.time));
}

function analyzeBatchQuality(candidates, { minDuplicateSymbols = 4 } = {}) {
  const knownBad = [];
  const byTableFingerprint = new Map();

  for (const c of candidates) {
    for (const b of c.newBars) {
      const fingerprint = ohlcvFingerprint(b);
      const item = {
        tableName: c.tableName,
        sym: c.sym,
        time: Number(b.time),
        date: fmtDate(Number(b.time)),
        fingerprint,
      };

      if (KNOWN_BAD_OHLCV_FINGERPRINTS.has(fingerprint)) knownBad.push(item);

      const key = `${c.tableName}|${fingerprint}`;
      const rows = byTableFingerprint.get(key) ?? [];
      rows.push(item);
      byTableFingerprint.set(key, rows);
    }
  }

  const duplicateFingerprints = [];
  for (const rows of byTableFingerprint.values()) {
    const symbols = [...new Set(rows.map(r => r.sym))];
    if (symbols.length >= minDuplicateSymbols) {
      duplicateFingerprints.push({
        tableName: rows[0].tableName,
        fingerprint: rows[0].fingerprint,
        symbols,
        rows,
      });
    }
  }

  return {
    ok: knownBad.length === 0 && duplicateFingerprints.length === 0,
    knownBad,
    duplicateFingerprints,
  };
}

function printQualityRejection(quality) {
  process.stdout.write('\n\n⛔ رفض حارس جودة Deep History الدفعة قبل الحفظ — لم تُكتب weekly/monthly جديدة.\n');
  for (const row of quality.knownBad.slice(0, 20)) {
    process.stdout.write(`  بصمة فاسدة: ${row.tableName} ${row.sym} ${row.date} ${row.fingerprint}\n`);
  }
  for (const group of quality.duplicateFingerprints.slice(0, 10)) {
    process.stdout.write(`  تكرار مشبوه: ${group.tableName} ${group.fingerprint} ← ${group.symbols.join(', ')}\n`);
  }
  process.stdout.write('الإجراء: أعد تشغيل TradingView/CDP ثم أعد الجلب.\n');
}

function runQualitySelfTest() {
  const unique = analyzeBatchQuality([
    { tableName: 'ohlcv_weekly', sym: 'COMI', newBars: [{ time: 1, open: 1, high: 2, low: 1, close: 1.5, volume: 100 }] },
    { tableName: 'ohlcv_weekly', sym: 'PHDC', newBars: [{ time: 1, open: 2, high: 3, low: 2, close: 2.5, volume: 200 }] },
  ]);
  if (!unique.ok) throw new Error('deep quality self-test failed: unique bars rejected');

  const duplicate = analyzeBatchQuality([
    { tableName: 'ohlcv_weekly', sym: 'COMI', newBars: [{ time: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 12345 }] },
    { tableName: 'ohlcv_weekly', sym: 'PHDC', newBars: [{ time: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 12345 }] },
  ], { minDuplicateSymbols: 2 });
  if (duplicate.ok || duplicate.duplicateFingerprints.length !== 1) {
    throw new Error('deep quality self-test failed: duplicate fingerprint not rejected');
  }

  const knownBad = analyzeBatchQuality([
    { tableName: 'ohlcv_monthly', sym: 'ABUK', newBars: [{ time: 1, open: 41.61, high: 42.18, low: 41.61, close: 41.70, volume: 83579 }] },
  ]);
  if (knownBad.ok || knownBad.knownBad.length !== 1) {
    throw new Error('deep quality self-test failed: known bad fingerprint not rejected');
  }

  process.stdout.write('✅ Deep history quality self-test passed.\n');
}

const ALL_SYMBOLS = (SINGLE ? [SINGLE] : [...new Set(EGX_UNIVERSE)]).slice(0, MAX_SYMBOLS ?? undefined);

async function fetchCurrentTimeframe(symbol, tableName) {
  const tvSymbol = toTvSymbol(symbol);
  const symbolResult = await setSymbol({ symbol: tvSymbol });
  const ready = symbolResult?.chart_ready || await waitForChartReady(tvSymbol, null, READY_TIMEOUT_MS);
  if (!ready) await sleep(700);
  const data = await getOhlcv({ count: MAX_BARS });
  if (!data?.bars?.length) return { validBars: [], newBars: [] };
  const validBars = data.bars.filter(validateBar);
  const db = getDB();
  return { validBars, newBars: getNewBars(db, tableName, symbol, validBars) };
}

async function fetchFrameBatch({ label, tf, tableName, doneSet, resultBucket, targets, candidates, startTime }) {
  await setTimeframe({ timeframe: tf });

  const frameTargets = targets.filter(sym => !doneSet.has(sym));
  for (let i = 0; i < frameTargets.length; i++) {
    const sym = frameTargets[i];
    const pct = Math.round((i / Math.max(1, frameTargets.length)) * 100);
    const eta = i > 0 ? Math.round((Date.now() - startTime) / i * (frameTargets.length - i) / 60000) : '?';
    process.stdout.write(`\r  ${label} ${bar(pct)} ${String(i+1).padStart(3)}/${frameTargets.length}  ${String(sym).padEnd(8)}  ETA:${eta}m  W:${resultBucket.weekly.ok} M:${resultBucket.monthly.ok}  `);

    try {
      const fetched = await fetchCurrentTimeframe(sym, tableName);
      candidates.push({ sym, tableName, ...fetched });
    } catch (e) {
      if (tableName === 'ohlcv_weekly') resultBucket.weekly.err++;
      else resultBucket.monthly.err++;
      process.stderr.write(`\n  ⚠️  ${label} ${sym}: ${e.message}\n`);
    }

    await sleep(DELAY_MS);
  }
}

async function main() {
  if (SELF_TEST_QUALITY) {
    runQualitySelfTest();
    return;
  }

  const db = getDB();

  // Ensure tables exist
  try {
    const { initPhase49to55Schema } = await import('../src/egx/index.js');
    initPhase49to55Schema();
  } catch { /* already initialized */ }

  // Resume tracking
  const weeklyDone  = new Set(RESUME
    ? db.prepare("SELECT DISTINCT symbol FROM ohlcv_weekly").all().map(r => r.symbol)
    : []);
  const monthlyDone = new Set(RESUME
    ? db.prepare("SELECT DISTINCT symbol FROM ohlcv_monthly").all().map(r => r.symbol)
    : []);

  const targets = ALL_SYMBOLS.filter(s =>
    (WEEKLY && !weeklyDone.has(s)) || (MONTHLY && !monthlyDone.has(s)));

  process.stdout.write(`
╔════════════════════════════════════════════════════════════════╗
║         EGX Deep History Fetcher — Phase 49                    ║
╠════════════════════════════════════════════════════════════════╣
║  Symbols   : ${String(targets.length).padEnd(4)} of ${String(ALL_SYMBOLS.length).padEnd(4)}                               ║
║  Weekly    : ${WEEKLY  ? '✅ YES' : '❌ NO '}  →  ohlcv_weekly  (target: ~10 years)    ║
║  Monthly   : ${MONTHLY ? '✅ YES' : '❌ NO '}  →  ohlcv_monthly (target: ~40 years)   ║
║  Max bars  : 500 per symbol per timeframe                      ║
║  Resume    : ${RESUME  ? '✅ YES' : '❌ NO '}                                          ║
╚════════════════════════════════════════════════════════════════╝
`);

  const results = { weekly: { ok: 0, err: 0, bars: 0 }, monthly: { ok: 0, err: 0, bars: 0 } };
  const candidates = [];
  const startTime = Date.now();

  if (WEEKLY) {
    await fetchFrameBatch({
      label: 'Weekly ', tf: 'W', tableName: 'ohlcv_weekly',
      doneSet: weeklyDone, resultBucket: results, targets, candidates, startTime,
    });
  }

  if (MONTHLY) {
    await fetchFrameBatch({
      label: 'Monthly', tf: 'M', tableName: 'ohlcv_monthly',
      doneSet: monthlyDone, resultBucket: results, targets, candidates, startTime,
    });
  }

  const quality = analyzeBatchQuality(candidates);
  if (!quality.ok) {
    printQualityRejection(quality);
    process.exitCode = 2;
  } else {
    for (const c of candidates) {
      if (!c.validBars.length) continue;
      const saved = saveOHLCVTimeframe(c.tableName, c.sym, c.validBars);
      if (c.tableName === 'ohlcv_weekly') {
        results.weekly.ok++;
        results.weekly.bars += saved;
      } else if (c.tableName === 'ohlcv_monthly') {
        results.monthly.ok++;
        results.monthly.bars += saved;
      }
    }
  }

  // Restore to daily
  await setTimeframe({ timeframe: 'D' }).catch(() => {});

  // Final report
  const weeklyCov  = db.prepare("SELECT COUNT(DISTINCT symbol) as s, COUNT(*) as b, MIN(date(bar_time,'unixepoch')) as oldest FROM ohlcv_weekly").get();
  const monthlyCov = db.prepare("SELECT COUNT(DISTINCT symbol) as s, COUNT(*) as b, MIN(date(bar_time,'unixepoch')) as oldest FROM ohlcv_monthly").get();

  process.stdout.write(`

╔════════════════════════════════════════════════════════════════╗
║                      FINAL REPORT                              ║
╠════════════════════════════════════════════════════════════════╣
║  Weekly   : ${String(weeklyCov?.s ?? 0).padEnd(3)} symbols  ${String(weeklyCov?.b ?? 0).padEnd(7)} bars  oldest: ${weeklyCov?.oldest ?? '—'}   ║
║  Monthly  : ${String(monthlyCov?.s ?? 0).padEnd(3)} symbols  ${String(monthlyCov?.b ?? 0).padEnd(7)} bars  oldest: ${monthlyCov?.oldest ?? '—'}   ║
║  Errors   : W:${results.weekly.err}  M:${results.monthly.err}                                    ║
║  Duration : ${Math.round((Date.now()-startTime)/60000)} min                                        ║
╚════════════════════════════════════════════════════════════════╝
`);
  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
