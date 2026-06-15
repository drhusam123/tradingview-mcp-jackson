#!/usr/bin/env node
/**
 * Per-symbol OHLCV catch-up for symbols lagging behind last EGX trading day.
 *
 * Usage:
 *   node scripts/egx_ohlcv_catchup.mjs
 *   node scripts/egx_ohlcv_catchup.mjs --date 2026-06-14
 *   node scripts/egx_ohlcv_catchup.mjs --max-symbols 20
 *   node scripts/egx_ohlcv_catchup.mjs --dry-run
 *   node scripts/egx_ohlcv_catchup.mjs --archive-chronic
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { getSymbolsLaggingOhlcv, getDB } from '../src/egx/index.js';
import { tradingDayStaleness, freshnessReferenceDate } from './lib/egx_calendar.mjs';
import {
  purgeSuspiciousTailIfNeeded,
  recordFetchOutcome,
  archiveChronicFetchFailures,
  writeHygieneReport,
  findSuspiciousTailPurgeDate,
} from './lib/ohlcv_hygiene.mjs';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const DRY_RUN = process.argv.includes('--dry-run');
const ARCHIVE_CHRONIC = process.argv.includes('--archive-chronic');

const dateArg = (() => {
  const i = process.argv.indexOf('--date');
  return i >= 0 ? process.argv[i + 1] : null;
})();

const maxSymbols = (() => {
  const i = process.argv.indexOf('--max-symbols');
  return i >= 0 ? Math.max(1, parseInt(process.argv[i + 1] || '0', 10)) : null;
})();

function targetDate() {
  if (dateArg) return dateArg;
  const db = getDB();
  const latest = db.prepare("SELECT MAX(date(bar_time,'unixepoch')) AS d FROM ohlcv_history").get()?.d;
  if (latest) {
    const cal = tradingDayStaleness(latest, freshnessReferenceDate());
    return cal.last_trading_day || latest;
  }
  return freshnessReferenceDate();
}

function runDailyUpdate(symbol) {
  const out = execSync(`"${NODE}" scripts/daily_update.mjs --symbol ${symbol}`, {
    cwd: ROOT,
    stdio: 'pipe',
    timeout: 120_000,
    encoding: 'utf8',
  });
  const ok = /✅ ناجح\s*:\s*[1-9]/.test(out) || /شمعات جديدة\s*:\s*[1-9]/.test(out);
  const unchanged = /➡️\s*بدون تغيير\s*:\s*1/.test(out) && !/❌ فاشل\s*:\s*[1-9]/.test(out);
  const failLine = (out.split('\n').find(l => l.includes('suspicious') || l.includes('فاشل')) || '').trim();
  return { ok, unchanged, failLine, out };
}

const signalDate = targetDate();
let lagging = getSymbolsLaggingOhlcv(signalDate);
if (maxSymbols) lagging = lagging.slice(0, maxSymbols);

console.log('\n═══ EGX OHLCV Per-Symbol Catch-up ═══');
console.log(`Target date: ${signalDate}`);
console.log(`Lagging symbols: ${lagging.length}${maxSymbols ? ` (capped at ${maxSymbols})` : ''}`);
console.log(`Mode: ${DRY_RUN ? 'DRY-RUN' : 'LIVE'}\n`);

if (!lagging.length) {
  console.log('✅ No per-symbol lag — all universe symbols current.\n');
  if (ARCHIVE_CHRONIC && !DRY_RUN) {
    const archive = archiveChronicFetchFailures();
    console.log(`Chronic archive: ${archive.archived.length} symbol(s)\n`);
  }
  process.exit(0);
}

const results = { ok: 0, fail: 0, skipped: 0, purged: 0, errors: [] };

for (const row of lagging) {
  const { symbol, last_bar: lastBar } = row;
  const label = `${symbol} (last=${lastBar ?? 'none'})`;
  if (DRY_RUN) {
    const purgeFrom = findSuspiciousTailPurgeDate(symbol);
    if (purgeFrom) results.purged += 1;
    console.log(`  [dry] ${label}${purgeFrom ? ` would_purge_from=${purgeFrom}` : ''}`);
    results.skipped += 1;
    continue;
  }

  const purge = purgeSuspiciousTailIfNeeded(symbol);
  if (purge.purged) {
    results.purged += 1;
    process.stdout.write(`  🧹 ${symbol} purged tail from ${purge.fromDate} … `);
  } else {
    process.stdout.write(`  ▶ ${label} … `);
  }

  try {
    const r = runDailyUpdate(symbol);
    if (r.ok || r.unchanged) {
      console.log(r.ok ? 'OK' : 'UNCHANGED');
      results.ok += 1;
      recordFetchOutcome(symbol, { success: true });
    } else {
      console.log('SKIP');
      results.skipped += 1;
      recordFetchOutcome(symbol, { success: false, reason: r.failLine || 'no_new_bars' });
    }
  } catch (e) {
    const msg = (e.stdout || e.stderr || e.message || '').split('\n').find(l =>
      l.includes('فاشل') || l.includes('failed') || l.includes('suspicious'),
    ) || e.message?.slice(0, 80);
    console.log(`FAIL — ${msg}`);
    results.fail += 1;
    results.errors.push({ symbol, error: String(msg).slice(0, 120) });
    recordFetchOutcome(symbol, { success: false, reason: String(msg).slice(0, 200) });
  }
}

let archive = null;
if (ARCHIVE_CHRONIC) {
  archive = archiveChronicFetchFailures({ dryRun: DRY_RUN });
  console.log(`\nChronic archive: ${archive.archived.length} archived, ${archive.skipped.length} protected`);
}

const after = getSymbolsLaggingOhlcv(signalDate);
console.log('\n═══ Summary ═══');
console.log(`  Synced  : ${results.ok}`);
console.log(`  Failed  : ${results.fail}`);
console.log(`  Skipped : ${results.skipped}`);
console.log(`  Purged  : ${results.purged}`);
console.log(`  Remaining lag: ${after.length} symbols behind ${signalDate}`);

writeHygieneReport({ signalDate, results, archive, remaining_lag: after.length });
console.log('');

const actionableLag = lagging.filter(r => {
  try {
    const db = getDB();
    return db.prepare(`
      SELECT 1 ok FROM final_signals
      WHERE trade_date=? AND symbol=? AND actionable=1 LIMIT 1
    `).get(signalDate, r.symbol)?.ok === 1;
  } catch { return false; }
}).length;

if (results.fail > 0 && actionableLag === 0 && after.length > 0) {
  console.log('ℹ️  Illiquid-only failures — actionable symbols current; exiting 0 for cron');
  process.exit(0);
}

process.exit(results.fail > 0 ? 1 : 0);
